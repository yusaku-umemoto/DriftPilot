"""既定の接続の実装。OpenAI 互換の API を1回叩く(2026-10-16 承認)。

公式 SDK(`openai`、Apache-2.0)は `llm` extra の任意依存で、ここで実行時に読み込む。
入っていなければ LLMClientUnavailable を送出する(比較は完走する。ADR-0018 決定4)。

api_key と base_url は常に明示して渡す。SDK が環境変数(OPENAI_API_KEY / OPENAI_BASE_URL)を読む経路を使わない
(キーの取得元は入口の1か所だけにする。docs/notes/req-16-llm.md)。
SDK の例外はこのモジュールの外へ出さず、client.py の例外に写す。写すときに SDK の文面は持ち出さない。
エラーの本文(SDK の body)と Retry-After の見出しだけは、api_error で掃除した文と秒にして LLMResponseError の
属性 api_error に持たせる(ADR-0168 決定2)。生の本文は写す関数の中の変数にだけ置く。

再試行(ADR-0168 決定6): SDK には常に max_retries=0 を渡し、http_json.ask_with_retries・ask_stream_with_retries と
同じ決まりで DriftPilot が繰り返す(接続の失敗・500・502・503・504・529 だけ。429 は繰り返さない)。
引数 max_retries はこの DriftPilot の再試行の回数。

見出しは Authorization: Bearer(SDK のまま)。api-key の見出しで送るのは openai_http_client(REQ-100 §3)。
wire(REQ-100 §4)の上限・thinking は extra_body、問い合わせの値は extra_query で渡す(既定値なら何も渡さない)。
"""

import json
import time
from collections.abc import Callable
from typing import Any

from driftpilot import i18n
from driftpilot.llm_analyzer import api_error
from driftpilot.llm_analyzer.client import LLMClientUnavailable, LLMConnectionError, LLMError, LLMResponseError
from driftpilot.llm_analyzer.http_json import DeltaRecord, ask_stream_with_retries, ask_with_retries
from driftpilot.llm_analyzer.wire import (
    CHAT_COMPLETIONS,
    DEFAULT_WIRE,
    RESPONSES,
    WireOptions,
    chat_stream_event,
    chat_thinking_event,
    extra_body,
    responses_stream_event,
    responses_text,
    responses_thinking_event,
)

# 利用者に見える文(日本語・英語。REQ-258)
_TEXTS_JA = {"not_installed": "openai が入っていません(pip install 'driftpilot[llm]')"}
_TEXTS_EN = {"not_installed": "openai is not installed (pip install 'driftpilot[llm]')"}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_RETRIES = 2

_sleep = time.sleep  # テストで差し替える


def _plain(chunk: Any) -> Any:
    """SDK の塊・事象を dict にする(読み方を urllib の部品と同じにするため)。読めなければそのまま。"""
    dump = getattr(chunk, "model_dump", None)
    if dump is None:
        return chunk
    try:
        return dump()
    except Exception:  # noqa: BLE001 - 形の違う塊は読まない(文字無しとして扱う)
        return None


def _sdk_body(exc: Any) -> Any:
    """SDK の例外の本文(JSON の値)。SDK が error の中の文字だけを渡したとき・JSON でない本文のときは、
    応答の本文が JSON として読めるときだけその全体を使う(JSON でない本文は出さない。ADR-0168 決定2)。"""
    try:
        body = getattr(exc, "body", None)
        if isinstance(body, (dict, list)):
            return body
        response = getattr(exc, "response", None)
        if response is None:
            return None
        text = response.text
        if not isinstance(text, str) or len(text.encode("utf-8")) > api_error.MAX_ERROR_BODY_BYTES:
            return None
        return json.loads(text)
    except Exception:  # noqa: BLE001 - 読めなければ文なし
        return None


def _sdk_headers(exc: Any) -> Any:
    try:
        return exc.response.headers
    except Exception:  # noqa: BLE001
        return None


class OpenAICompatibleClient:
    """chat.completions を1回呼び、回答のテキストを返す。base_url を変えればローカル LLM にもつながる。"""

    _api_format = CHAT_COMPLETIONS

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        http_client: Any = None,
        wire: WireOptions = DEFAULT_WIRE,
    ) -> None:
        try:
            import openai
        except ImportError:
            raise LLMClientUnavailable(_T["not_installed"]) from None
        self._openai = openai
        self._model = model
        self._api_key = api_key  # エラーの文にそのまま載っていたら伏せるためだけに持つ(repr には出さない)
        self._max_retries = max_retries  # DriftPilot の再試行の回数(SDK には 0 を渡す。ADR-0168 決定6)
        # 既定値のときは何も渡さない(今の呼び方のまま)
        self._options: dict[str, Any] = {}
        if body := extra_body(self._api_format, wire):
            self._options["extra_body"] = body
        if wire.query:
            self._options["extra_query"] = dict(wire.query)
        kwargs: dict[str, Any] = {
            "api_key": api_key,
            "base_url": base_url or DEFAULT_BASE_URL,
            "timeout": timeout,
            "max_retries": 0,
        }
        if http_client is not None:  # テストで通信を差し替えるときだけ
            kwargs["http_client"] = http_client
        self._client = openai.OpenAI(**kwargs)

    @property
    def model_name(self) -> str:
        return self._model

    def __repr__(self) -> str:  # キーを表示しない
        return f"{type(self).__name__}(model={self._model!r})"

    def _create(self, prompt: str, *, stream: bool = False) -> Any:
        more: dict[str, Any] = {"stream": True} if stream else {}
        return self._client.chat.completions.create(
            model=self._model, messages=[{"role": "user", "content": prompt}], **self._options, **more
        )

    def _read_event(self, data: Any) -> tuple[str, str | None]:
        return chat_stream_event(data)

    def _read_thinking(self, data: Any) -> str:
        return chat_thinking_event(data)

    # 考えの文字を on_thinking に渡せる(ADR-0169 決定1)
    streams_thinking = True

    def _failure(self, kind: str, status: int | None, body: Any, headers: Any) -> LLMError:
        """except の外で、SDK の例外の写し先を作る(SDK の例外を __context__ につながない)。"""
        if kind == "connection":
            return LLMConnectionError()
        if kind == "response":
            return LLMResponseError(status, api_error=api_error.from_json(body, headers, api_key=self._api_key))
        return LLMResponseError()

    def _send_once(self, prompt: str) -> str | LLMError:
        """1回だけ送る。例外は送出せず返す(再試行は ask_with_retries が決める)。"""
        openai = self._openai
        kind: str | None = None
        status: int | None = None
        body: Any = None
        headers: Any = None
        response: Any = None
        try:
            response = self._create(prompt)
        except openai.APIConnectionError:  # タイムアウト(APITimeoutError)を含む
            kind = "connection"
        except openai.APIStatusError as exc:
            kind, status, body, headers = "response", getattr(exc, "status_code", None), _sdk_body(exc), _sdk_headers(exc)
        except openai.APIError as exc:  # 状態コードの無い API のエラー
            kind, body = "response", _sdk_body(exc)
        except openai.OpenAIError:
            kind = "other"
        if kind is not None:
            return self._failure(kind, status, body, headers)
        try:
            return self._text(response)
        except LLMError as exc:
            return exc.with_traceback(None)

    def _stream_once(
        self, prompt: str, on_delta: Callable[[str], None], on_thinking: Callable[[str], None] | None = None
    ) -> str | LLMError:
        """stream=True で1回だけ送る。例外は送出せず返す(StreamStopped だけはそのまま外へ出す。with で接続を閉じる)。

        on_thinking があれば、塊ごとに答えより前に考えの文字を読んで渡す(ADR-0169 決定2)。
        """
        openai = self._openai
        pieces: list[str] = []
        finished: str | None = None
        error_event: Any = None
        kind: str | None = None
        status: int | None = None
        body: Any = None
        headers: Any = None
        try:
            with self._create(prompt, stream=True) as stream:
                for chunk in stream:
                    data = _plain(chunk)
                    if on_thinking is not None:
                        thought = self._read_thinking(data)
                        if thought:
                            on_thinking(thought)  # StreamStopped はそのまま外へ
                    text, ended = self._read_event(data)
                    if text:
                        pieces.append(text)
                    on_delta(text)  # StreamStopped はそのまま外へ
                    if ended is not None:
                        finished = ended
                        if ended == "error":
                            error_event = data  # responses の response.failed・error の事象
                        break
        except openai.APIConnectionError:  # タイムアウト(APITimeoutError)と、読む途中で切れたときを含む
            kind = "connection"
        except openai.APIStatusError as exc:
            kind, status, body, headers = "response", getattr(exc, "status_code", None), _sdk_body(exc), _sdk_headers(exc)
        except openai.APIError as exc:  # 流している途中のエラーの塊(SDK が error の中を body に入れる)
            kind, body = "response", _sdk_body(exc)
        except openai.OpenAIError:
            kind = "other"
        # 例外は except の外で作ったものを返す(SDK の例外を __context__ につながない)
        if kind is not None:
            return self._failure(kind, status, body, headers)
        if finished == "error":
            return LLMResponseError(api_error=api_error.from_json(error_event, None, api_key=self._api_key))
        if finished != "done":
            return LLMConnectionError()  # 全部届いた印が無い(途中で切れた)。途中の文字を答えにしない
        answer = "".join(pieces)
        if not answer:
            return LLMResponseError()
        return answer

    def ask_stream(
        self, prompt: str, on_delta: Callable[[str], None], *, on_thinking: Callable[[str], None] | None = None
    ) -> str:
        """stream=True で呼び、届いた文字から on_delta に渡す(ADR-0157 決定3)。全部届いた印が無ければ例外。

        例外の写し方は ask と同じ(SDK の文面は持ち出さない)。StreamStopped はそのまま外へ出す。
        文字が届く前に STREAM_FALLBACK_STATUS_CODES が返ったら、流す指定が使えない接続先とみて ask を1回だけ呼ぶ。
        再試行は文字が届く前だけ、ask と同じ決まりで DriftPilot が繰り返す(ADR-0168 決定6)。
        考えの文字は on_thinking があるときだけ渡し、届いたら「届いた」に数える(ADR-0169 決定1・2)。送る値は変えない。
        """
        record = DeltaRecord(on_delta, on_thinking)
        thinking = record.think if on_thinking is not None else None
        return ask_stream_with_retries(
            lambda: self._stream_once(prompt, record, thinking),
            lambda: self.ask(prompt),
            self._max_retries,
            lambda seconds: _sleep(seconds),  # 呼ぶたびにモジュールから取る(テストが差し替える)
            lambda: record.received,
        )

    def _text(self, response: Any) -> str:
        try:
            content = response.choices[0].message.content
        except (AttributeError, IndexError, TypeError):
            raise LLMResponseError() from None
        if not isinstance(content, str):
            raise LLMResponseError()
        return content

    def ask(self, prompt: str) -> str:
        try:
            return ask_with_retries(
                lambda: self._send_once(prompt),
                self._max_retries,
                lambda seconds: _sleep(seconds),  # 呼ぶたびにモジュールから取る(テストが差し替える)
            )
        except LLMError as exc:
            raise exc from None  # 今までどおり __cause__ は無く、__context__ も表に出さない


class OpenAIResponsesClient(OpenAICompatibleClient):
    """responses を1回呼び、output の output_text をつなげて返す(REQ-100 §2)。無ければ LLMResponseError。"""

    _api_format = RESPONSES

    def _create(self, prompt: str, *, stream: bool = False) -> Any:
        more: dict[str, Any] = {"stream": True} if stream else {}
        return self._client.responses.create(model=self._model, input=prompt, **self._options, **more)

    def _read_event(self, data: Any) -> tuple[str, str | None]:
        return responses_stream_event(data)

    def _read_thinking(self, data: Any) -> str:
        return responses_thinking_event(data)

    def _text(self, response: Any) -> str:
        try:
            data = response.model_dump()
        except AttributeError:
            raise LLMResponseError() from None
        text = responses_text(data)
        if text is None:
            raise LLMResponseError()
        return text
