"""利用者が定義した形式 custom-http-json で1回送る接続の実装(ADR-0073 決定5。REQ-101 §3)。

送り方は http_json と同じ(標準ライブラリの urllib・https だけで http は loopback だけ・リダイレクトを断る・
一時的な失敗だけ再試行・キーと応答の本文を例外に入れない)。メソッドは POST、本文は JSON。
送る見出しは、定義の固定の見出し・選んだ認証の見出し1つだけ(none なら無し)・content-type: application/json。
本文は定義のひな形に model・prompt・max_output_tokens を差し込んだもの。回答は answer_path の位置の文字列。

接続テストの口(ask_with_shape)は、応答の JSON の形の木(名前と型だけ)を回答の結果と一緒に返す。
応答の値と本文は、読む関数の中の変数にだけ置き、この部品の属性にも例外にも残さない。
エラーの本文だけは http_json が掃除した文と待つ秒にして、LLMResponseError の属性 api_error に持たせる(ADR-0168 決定2)。
認証が none なら送ったキーは無いので、キーとしては伏せない(行の判定と長い並びの決まりは通る)。
"""

import json
import time
from collections.abc import Callable

from driftpilot.llm_analyzer.client import LLMConnectionError, LLMError, LLMResponseError
from driftpilot.llm_analyzer.custom_http import (
    CUSTOM_AUTH_API_KEY,
    CUSTOM_AUTH_BEARER,
    CUSTOM_AUTH_NONE,
    CUSTOM_AUTH_X_API_KEY,
    CustomHttpJson,
    ShapeNode,
    answer_at,
    parse_response,
    render_body,
    response_shape,
)
from driftpilot.llm_analyzer.http_json import ask_with_retries, build_opener, is_allowed_url, is_valid_key, send_once
from driftpilot.llm_analyzer.openai_client import DEFAULT_MAX_RETRIES, DEFAULT_TIMEOUT_SECONDS
from driftpilot.llm_analyzer.wire import DEFAULT_WIRE, WireOptions

_sleep = time.sleep  # テストで差し替える


class CustomHttpJsonClient:
    """定義(custom)どおりに POST し、answer_path の位置の文字列を返す。一時的な失敗だけ再試行する。

    base_url は build_request の呼び方に合わせて受け取るだけ(送り先は custom.url。設定を読むときに同じ URL が入る)。
    """

    def __init__(
        self,
        *,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        custom: CustomHttpJson,
        wire: WireOptions = DEFAULT_WIRE,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> None:
        self._model = model
        self._api_key = api_key
        self._custom = custom
        self._max_output_tokens = wire.max_output_tokens
        self._timeout = timeout
        self._max_retries = max_retries
        self._opener = build_opener()

    @property
    def model_name(self) -> str:
        return self._model

    def __repr__(self) -> str:  # キーを表示しない
        return f"CustomHttpJsonClient(model={self._model!r})"

    def _headers(self) -> dict[str, str]:
        headers = dict(self._custom.headers)
        auth = self._custom.auth
        if auth == CUSTOM_AUTH_BEARER:
            headers["Authorization"] = f"Bearer {self._api_key}"
        elif auth == CUSTOM_AUTH_API_KEY:
            headers["api-key"] = self._api_key or ""
        elif auth == CUSTOM_AUTH_X_API_KEY:
            headers["x-api-key"] = self._api_key or ""
        headers["content-type"] = "application/json"
        return headers

    def _refused(self) -> LLMError | None:
        """送る前に断る理由(再試行しない)。"""
        if not is_allowed_url(self._custom.url):
            return LLMConnectionError()
        if self._custom.auth != CUSTOM_AUTH_NONE and (not self._api_key or not is_valid_key(self._api_key)):
            return LLMResponseError()  # キーが無い・形が違う。送らず、繰り返しても通らない
        return None

    def _ask(self, prompt: str, parse: Callable[[bytes], str | None]) -> str:
        if (refused := self._refused()) is not None:
            raise refused
        payload = render_body(
            self._custom.body, model=self._model, prompt=prompt, max_output_tokens=self._max_output_tokens
        )
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = self._headers()
        url = self._custom.url
        # エラーの文にキーがそのまま載っていたら伏せる(ADR-0168 決定3)。認証が none なら送っていないので None
        key = self._api_key if self._custom.auth != CUSTOM_AUTH_NONE else None
        return ask_with_retries(
            lambda: send_once(self._opener, url, body, headers, self._timeout, parse, api_key=key),
            self._max_retries,
            lambda seconds: _sleep(seconds),  # 呼ぶたびにモジュールから取る(テストが差し替える)
        )

    def ask(self, prompt: str) -> str:
        path = self._custom.answer_path

        def parse(raw: bytes) -> str | None:
            ok, data = parse_response(raw)
            return answer_at(data, path) if ok else None

        return self._ask(prompt, parse)

    def ask_with_shape(self, prompt: str) -> tuple[str | LLMError, ShapeNode | None]:
        """接続テストの口。(回答の文か LLMError, 応答の形の木)。例外は送出せずに返す。

        木は 2xx で JSON が読めたときだけ(回答の位置が無いときも返す)。JSON でなければ None。
        """
        path = self._custom.answer_path
        shapes: list[ShapeNode | None] = []

        def parse(raw: bytes) -> str | None:
            ok, data = parse_response(raw)
            if not ok:
                return None
            shapes[:] = [response_shape(data)]  # 形だけを残す(値は残さない)
            return answer_at(data, path)

        answer: str | LLMError
        try:
            answer = self._ask(prompt, parse)
        except LLMError as exc:
            answer = exc.with_traceback(None)
        return answer, (shapes[0] if shapes else None)
