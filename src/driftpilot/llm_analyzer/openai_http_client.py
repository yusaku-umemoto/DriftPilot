"""OpenAI 互換の API を api-key の見出しで1回叩く接続の実装(ADR-0073 決定3。REQ-100 §3)。Azure OpenAI 向け。

openai ライブラリでは Authorization の見出しを消せず、AzureOpenAI は v1 の窓口の URL を組み替えるため、
標準ライブラリの urllib で送る(送る・断る・再試行の決まりは Claude の部品と同じ http_json)。
送る見出しは api-key と content-type だけで、Authorization は送らない。
送る URL は <接続先>/chat/completions か <接続先>/responses に問い合わせの値を付けたもので、組み替えない。
"""

import json
import time
from collections.abc import Callable
from typing import Any

from driftpilot import i18n
from driftpilot.llm_analyzer.client import LLMConnectionError, LLMResponseError
from driftpilot.llm_analyzer.http_json import (
    DeltaRecord,
    ask_stream_with_retries,
    ask_with_retries,
    build_opener,
    is_allowed_url,
    is_valid_key,
    send_once,
    stream_once,
)
from driftpilot.llm_analyzer.openai_client import DEFAULT_BASE_URL, DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT_SECONDS
from driftpilot.llm_analyzer.wire import (
    CHAT_COMPLETIONS,
    DEFAULT_WIRE,
    RESPONSES,
    WireOptions,
    chat_stream_event,
    chat_text,
    chat_thinking_event,
    extra_body,
    responses_stream_event,
    responses_text,
    responses_thinking_event,
    with_query,
)

# 利用者に見える文(日本語・英語。REQ-258)。鍵の組と差し込みの名前は両方で同じ
_TEXTS_JA = {"api_format_invalid": "api_format は {formats} にしてください", "or_join": " か "}
_TEXTS_EN = {"api_format_invalid": "api_format must be {formats}", "or_join": " or "}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

_PATHS = {CHAT_COMPLETIONS: "/chat/completions", RESPONSES: "/responses"}

_sleep = time.sleep  # テストで差し替える


def _parser(api_format: str):
    pick = responses_text if api_format == RESPONSES else chat_text

    def parse(raw: bytes) -> str | None:
        try:
            data: Any = json.loads(raw.decode("utf-8"))
        except ValueError:  # JSON でない・UTF-8 でない
            return None
        return pick(data)

    return parse


class OpenAIApiKeyClient:
    """chat-completions か responses を api-key の見出しで呼び、回答のテキストを返す。一時的な失敗だけ再試行する。"""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        api_format: str = CHAT_COMPLETIONS,
        wire: WireOptions = DEFAULT_WIRE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        if api_format not in _PATHS:
            raise ValueError(_T["api_format_invalid"].format(formats=_T["or_join"].join(_PATHS)))
        self._model = model
        self._api_key = api_key
        self._api_format = api_format
        self._url = with_query((base_url or DEFAULT_BASE_URL).rstrip("/") + _PATHS[api_format], wire.query)
        self._extra = extra_body(api_format, wire)
        self._timeout = timeout
        self._max_retries = max_retries
        self._opener = build_opener()

    @property
    def model_name(self) -> str:
        return self._model

    def __repr__(self) -> str:  # キーを表示しない
        return f"OpenAIApiKeyClient(model={self._model!r}, api_format={self._api_format!r})"

    def _check(self) -> None:
        if not is_allowed_url(self._url):  # 送る前に断る(再試行しない)
            raise LLMConnectionError()
        if not is_valid_key(self._api_key):  # キーの形が違う。送らず、繰り返しても通らないので再試行しない
            raise LLMResponseError()

    def _body(self, prompt: str, **more: Any) -> bytes:
        if self._api_format == RESPONSES:
            payload: dict[str, Any] = {"model": self._model, "input": prompt}
        else:
            payload = {"model": self._model, "messages": [{"role": "user", "content": prompt}]}
        return json.dumps({**payload, **self._extra, **more}, ensure_ascii=False).encode("utf-8")

    def _headers(self) -> dict[str, str]:
        return {"api-key": self._api_key, "content-type": "application/json"}

    def ask(self, prompt: str) -> str:
        self._check()
        body = self._body(prompt)
        headers = self._headers()
        parse = _parser(self._api_format)
        return ask_with_retries(
            lambda: send_once(self._opener, self._url, body, headers, self._timeout, parse, api_key=self._api_key),
            self._max_retries,
            lambda seconds: _sleep(seconds),  # 呼ぶたびにモジュールから取る(テストが差し替える)
        )

    # 考えの文字(reasoning_content・reasoning・reasoning の要約の事象)を on_thinking に渡せる(ADR-0169 決定1)
    streams_thinking = True

    def ask_stream(
        self, prompt: str, on_delta: Callable[[str], None], *, on_thinking: Callable[[str], None] | None = None
    ) -> str:
        """"stream": true で送り、届いた文字から渡す(ADR-0157 決定3)。

        考えの文字は on_delta には渡さず、on_thinking があるときだけそちらに渡す(ADR-0169 決定2)。送る本文は変えない。
        """
        self._check()
        body = self._body(prompt, stream=True)  # stream は予約された名前で、利用者の値と重ならない
        headers = self._headers()
        parse = _parser(self._api_format)
        is_responses = self._api_format == RESPONSES
        read_event = responses_stream_event if is_responses else chat_stream_event
        record = DeltaRecord(on_delta, on_thinking)
        thinking = record.think if on_thinking is not None else None
        read_thinking = (responses_thinking_event if is_responses else chat_thinking_event) if thinking else None
        return ask_stream_with_retries(
            lambda: stream_once(
                self._opener,
                self._url,
                body,
                headers,
                self._timeout,
                read_event,
                parse,
                record,
                api_key=self._api_key,
                read_thinking=read_thinking,
                on_thinking=thinking,
            ),
            lambda: self.ask(prompt),
            self._max_retries,
            lambda seconds: _sleep(seconds),
            lambda: record.received,
        )
