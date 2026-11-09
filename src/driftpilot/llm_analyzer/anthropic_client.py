"""Claude の API(Anthropic Messages)を1回叩く接続の実装(ADR-0035 決定4、案 KC)。

SDK は使わず、標準ライブラリの urllib で呼ぶ(依存を足さない)。送る・断る・再試行の決まりは http_json と共通
(TLS は truststore があれば OS の証明書・https だけ・http は loopback だけ・リダイレクトを断る・
キーと応答の本文を例外に入れない)。
改行などを含むキーは urllib がキー入りの ValueError を出すので、送る前に断る(LLMResponseError())。

wire(REQ-100 §4)の上限があれば max_tokens の既定 4096 の代わりに使い、thinking の値を本文に足す。
見出しは x-api-key のまま(auth_header は使わない)。
"""

import json
import time
from collections.abc import Callable
from typing import Any

from driftpilot.llm_analyzer.client import LLMConnectionError, LLMResponseError
from driftpilot.llm_analyzer.http_json import (  # noqa: F401 - 定数は今までの名前でも読めるように
    LOOPBACK_HOSTS,
    MAX_RETRY_WAIT_SECONDS,
    RETRY_STATUS_CODES,
    DeltaRecord,
    EventKind,
    ask_stream_with_retries,
    ask_with_retries,
    build_opener,
    is_allowed_url,
    is_valid_key,
    send_once,
    stream_once,
)
from driftpilot.llm_analyzer.openai_client import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT_SECONDS
from driftpilot.llm_analyzer.wire import ANTHROPIC_MESSAGES, DEFAULT_WIRE, WireOptions, extra_body

DEFAULT_ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MAX_TOKENS = 4096

_sleep = time.sleep  # テストで差し替える


def _text_of(raw: bytes) -> str | None:
    """応答の content のうち type が text の text をつなげる。形が違えば None。"""
    try:
        data: Any = json.loads(raw.decode("utf-8"))
    except ValueError:  # JSON でない・UTF-8 でない
        return None
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, list):
        return None
    texts = [
        block["text"]
        for block in content
        if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    if not texts:
        return None
    return "".join(texts)


def _stream_event(data: Any) -> tuple[str, EventKind]:
    """流れてくる事象1つから (文字, 終わりの種類)。content_block_delta の text_delta だけを読む。"""
    if not isinstance(data, dict):
        return "", None
    kind = data.get("type")
    if kind == "content_block_delta":
        delta = data.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            return delta["text"], None
        return "", None
    if kind == "message_stop":
        return "", "done"
    if kind == "error":
        return "", "error"
    return "", None


def _thinking_event(data: Any) -> str:
    """流れてくる事象1つから考えの文字(ADR-0169 決定2)。content_block_delta の thinking_delta の thinking だけ。

    signature_delta・redacted_thinking は読まない。ほかは ""。
    """
    if not isinstance(data, dict) or data.get("type") != "content_block_delta":
        return ""
    delta = data.get("delta")
    if isinstance(delta, dict) and delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
        return delta["thinking"]
    return ""


class AnthropicMessagesClient:
    """/v1/messages を呼び、回答のテキストを返す。一時的な失敗(接続・500・502・503・504・529)だけ再試行する。

    429 は送り直さない(ADR-0168 決定6)。
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        wire: WireOptions = DEFAULT_WIRE,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._url = (base_url or DEFAULT_ANTHROPIC_BASE_URL).rstrip("/") + "/v1/messages"
        self._timeout = timeout
        self._max_retries = max_retries
        self._max_tokens = wire.max_output_tokens if wire.max_output_tokens is not None else max_tokens
        self._extra = extra_body(ANTHROPIC_MESSAGES, wire)
        self._opener = build_opener()

    @property
    def model_name(self) -> str:
        return self._model

    def __repr__(self) -> str:  # キーを表示しない
        return f"AnthropicMessagesClient(model={self._model!r})"

    def _check(self) -> None:
        if not is_allowed_url(self._url):  # 送る前に断る(再試行しない)
            raise LLMConnectionError()
        if not is_valid_key(self._api_key):  # キーの形が違う。送らず、繰り返しても通らないので再試行しない
            raise LLMResponseError()

    def _body(self, prompt: str, **more: Any) -> bytes:
        return json.dumps(
            {
                "model": self._model,
                "max_tokens": self._max_tokens,
                "messages": [{"role": "user", "content": prompt}],
                **self._extra,
                **more,
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self._api_key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}

    def ask(self, prompt: str) -> str:
        self._check()
        body = self._body(prompt)
        headers = self._headers()
        return ask_with_retries(
            lambda: send_once(self._opener, self._url, body, headers, self._timeout, _text_of, api_key=self._api_key),
            self._max_retries,
            lambda seconds: _sleep(seconds),  # 呼ぶたびにモジュールから取る(テストが差し替える)
        )

    # 考えの文字(thinking_delta)を on_thinking に渡せる(ADR-0169 決定1)
    streams_thinking = True

    def ask_stream(
        self, prompt: str, on_delta: Callable[[str], None], *, on_thinking: Callable[[str], None] | None = None
    ) -> str:
        """"stream": true で送り、text_delta の文字を届いたところから渡す(ADR-0157 決定2)。

        thinking_delta は on_delta には渡さず、on_thinking があるときだけそちらに渡す(ADR-0169 決定2)。送る本文は変えない。
        """
        self._check()
        body = self._body(prompt, stream=True)  # stream は予約された名前で、利用者の値と重ならない
        headers = self._headers()
        record = DeltaRecord(on_delta, on_thinking)
        thinking = record.think if on_thinking is not None else None
        return ask_stream_with_retries(
            lambda: stream_once(
                self._opener,
                self._url,
                body,
                headers,
                self._timeout,
                _stream_event,
                _text_of,
                record,
                api_key=self._api_key,
                read_thinking=_thinking_event if thinking is not None else None,
                on_thinking=thinking,
            ),
            lambda: self.ask(prompt),
            self._max_retries,
            lambda seconds: _sleep(seconds),
            lambda: record.received,
        )
