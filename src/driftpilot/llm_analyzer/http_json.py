"""標準ライブラリの HTTP で JSON を1回送る共通の部分(REQ-100 §3。Claude の部品と、api-key の見出しの OpenAI 互換の部品)。

依存を足さない(urllib)。TLS は truststore が入っていれば OS の証明書を使い、無ければ ssl の既定の設定を使う。
送り先は https だけ。http は 127.0.0.1・::1・localhost のときだけ許す(キーを平文で外に流さない)。
リダイレクトにはついていかない(キーを別の送り先に渡さない)。3xx は LLMResponseError(状態コード) にする。
例外は client.py の3つに写す。キーと応答の本文は例外の文に入れない。
エラーの本文は先頭 64 KiB だけ読み、api_error.from_body で掃除した文と待つ秒にして LLMResponseError の属性 api_error に持たせる
(ADR-0168 決定2・3)。生の本文は読む関数の中の変数にだけ置く。
例外は except の外で作り、元の例外を __context__ に残さない。
429 は自動で送り直さない(ADR-0168 決定6)。再試行は接続の失敗・500・502・503・504・529 だけ。
"""

import http.client
import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any, Literal
from urllib.parse import urlsplit

from driftpilot.llm_analyzer import api_error
from driftpilot.llm_analyzer.client import LLMConnectionError, LLMError, LLMResponseError

# 429 は入れない(待つように言われたのに続けて送らない。ADR-0168 決定6)
RETRY_STATUS_CODES = frozenset({500, 502, 503, 504, 529})
MAX_RETRY_WAIT_SECONDS = 8
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """リダイレクトについていかない。None を返すと、3xx は urllib の既定の処理で HTTPError になる。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def ssl_context() -> ssl.SSLContext:
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context()
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def build_opener() -> urllib.request.OpenerDirector:
    """プロキシは urllib の既定のまま(環境変数・OS の設定)。https と、リダイレクトの扱いだけ差し替える。"""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()), NoRedirect)


def is_allowed_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        host = parts.hostname
        parts.port  # 数字でない・範囲外の番号はここで ValueError(送る途中で別の例外にしない)
    except ValueError:
        return False
    if not host:
        return False
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and host in LOOPBACK_HOSTS


def is_valid_key(api_key: str) -> bool:
    """ヘッダーに載せられる、見える ASCII の文字だけか。改行などがあると urllib がキー入りの ValueError を出すため、送る前に見る。"""
    return all("!" <= ch <= "~" for ch in api_key)


def _error_body(exc: urllib.error.HTTPError) -> tuple[bytes, Any]:
    """HTTPError の本文の先頭(MAX_ERROR_BODY_BYTES まで)と見出し。読めなければ本文は空。閉じるのはここ。"""
    try:
        raw = exc.read(api_error.MAX_ERROR_BODY_BYTES)
    except Exception:  # noqa: BLE001 - 途中で切れた・読めない本文は無しとして扱う
        raw = b""
    if not isinstance(raw, bytes):
        raw = b""
    headers = getattr(exc, "headers", None)
    exc.close()
    return raw, headers


def send_once(
    opener: urllib.request.OpenerDirector,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
    parse: Callable[[bytes], str | None],
    api_key: str | None = None,
) -> str | LLMError:
    """1回送り、parse で回答の文を取る。例外は送出せず返す(except の外で作り、元の例外を __context__ に残さないため)。

    api_key は接続が送ったキー(エラーの文にそのまま載っていたら伏せる。ADR-0168 決定3)。認証が無ければ None。
    """
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    status: int | None = None
    failed = False
    rejected = False
    raw = b""
    error_raw = b""
    error_headers: Any = None
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:  # 3xx・4xx・5xx。本文は先頭だけ読み、掃除は except の外で
        status = exc.code
        error_raw, error_headers = _error_body(exc)
    except (urllib.error.URLError, OSError, http.client.HTTPException):  # 名前解決・接続・時間切れ・途中で切れた
        failed = True
    except ValueError:  # 送る前の検証をすり抜けた不正な値。文面にキーが載り得るので持ち出さない
        rejected = True
    if failed:
        return LLMConnectionError()
    if rejected:
        return LLMResponseError()
    if status is not None:
        return LLMResponseError(status, api_error=api_error.from_body(error_raw, error_headers, api_key=api_key))
    text = parse(raw)
    if text is None:
        return LLMResponseError()
    return text


def is_retryable(error: LLMError) -> bool:
    if isinstance(error, LLMConnectionError):
        return True
    return isinstance(error, LLMResponseError) and error.status_code in RETRY_STATUS_CODES


def ask_with_retries(send: Callable[[], str | LLMError], max_retries: int, sleep: Callable[[float], object]) -> str:
    """一時的な失敗(接続・500・502・503・504・529)だけ、1・2・4…秒(最大8秒)待って繰り返す。429 は繰り返さない。"""
    attempt = 0
    while True:
        result = send()
        if isinstance(result, str):
            return result
        if attempt >= max_retries or not is_retryable(result):
            raise result
        sleep(min(2**attempt, MAX_RETRY_WAIT_SECONDS))
        attempt += 1


# --- 届いたところから読む(ADR-0157 決定2〜4) ---

EVENT_STREAM = "text/event-stream"
# 文字が届く前にこの状態コードが返ったら、流す指定が使えない接続先とみて、今の ask で1回だけ送り直す
STREAM_FALLBACK_STATUS_CODES = frozenset({400, 404, 405, 415, 422, 501})
EventKind = Literal["done", "error"] | None
ReadEvent = Callable[[Any], tuple[str, EventKind]]


class DeltaRecord:
    """on_delta を包み、文字が1つでも届いたかを覚える(届いた後は再試行も送り直しもしない)。

    考えの文字(think)が届いたときも「届いた」に数える(考えが2回出ないため。ADR-0169 決定1)。
    """

    def __init__(self, on_delta: Callable[[str], None], on_thinking: Callable[[str], None] | None = None) -> None:
        self._on_delta = on_delta
        self._on_thinking = on_thinking
        self.received = False

    def __call__(self, text: str) -> None:
        if text:
            self.received = True
        self._on_delta(text)

    def think(self, text: str) -> None:
        if not text:
            return
        self.received = True
        if self._on_thinking is not None:
            self._on_thinking(text)


def _event_data(line: bytes) -> tuple[bool, Any]:
    """事象の1行から data の値を取る。(読めたか, 値)。[DONE] は値 None で返す。data の行でない・読めない行は (False, None)。"""
    try:
        text = line.decode("utf-8").rstrip("\r\n")
    except UnicodeDecodeError:
        return False, None
    if not text.startswith("data:"):
        return False, None
    data = text[len("data:") :].strip()
    if data == "[DONE]":
        return True, None
    try:
        return True, json.loads(data)
    except ValueError:
        return False, None


def stream_once(
    opener: urllib.request.OpenerDirector,
    url: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
    read_event: ReadEvent,
    parse_plain: Callable[[bytes], str | None],
    on_delta: Callable[[str], None],
    api_key: str | None = None,
    read_thinking: Callable[[Any], str] | None = None,
    on_thinking: Callable[[str], None] | None = None,
) -> str | LLMError:
    """流す指定で1回送り、届いたところから on_delta に渡す。例外は送出せず返す(StreamStopped だけはそのまま外へ出す)。

    read_thinking と on_thinking が両方あれば、流れてくる形の事象ごとに read_event の前に考えの文字を読み、
    文字があれば on_thinking に渡す(ADR-0169 決定2)。text/event-stream でない応答では考えを読まない。

    応答が text/event-stream でなければ、全部読んで parse_plain にかけ、on_delta を1回呼ぶ。
    流れてくる形なら1行ずつ読み、data の行だけを JSON にして read_event で (文字, 終わりの種類) を取る。
    全部届いた印(done)が無いまま読み終えたら LLMConnectionError、error なら LLMResponseError、文字が空なら LLMResponseError。
    error の事象の値と HTTP のエラーの本文は、掃除した文にして LLMResponseError の api_error に持たせる(ADR-0168 決定2)。
    timeout は1回の読み取りごとの待ち時間。応答の本文は例外の文に入れない。api_key は send_once と同じ。
    """
    request = urllib.request.Request(url, data=body, method="POST", headers=headers)
    status: int | None = None
    failed = False
    rejected = False
    plain: bytes | None = None
    pieces: list[str] = []
    finished: EventKind = None
    error_event: Any = None
    error_raw = b""
    error_headers: Any = None
    try:
        with opener.open(request, timeout=timeout) as response:
            content_type = str(response.headers.get("content-type") or "")
            if EVENT_STREAM not in content_type.lower():
                plain = response.read()
            else:
                while True:
                    line = response.readline()
                    if not line:
                        break
                    ok, data = _event_data(line)
                    if not ok:
                        continue  # data の行でない(event: や空行)・読めない行は無視する
                    if data is None:
                        break  # [DONE] は印として読み終える(done かどうかは read_event の印で決める)
                    if read_thinking is not None and on_thinking is not None:
                        thought = read_thinking(data)
                        if thought:
                            on_thinking(thought)  # StreamStopped はそのまま外へ
                    text, kind = read_event(data)
                    if text:
                        pieces.append(text)
                    on_delta(text)  # 文字が無い事象でも呼ぶ(中止を見るため)。StreamStopped はそのまま外へ
                    if kind is not None:
                        finished = kind
                        if kind == "error":
                            error_event = data  # 掃除は読み終えた後(この関数の中の変数にだけ置く)
                        break
    except urllib.error.HTTPError as exc:  # 3xx・4xx・5xx。本文は先頭だけ読み、掃除は except の外で
        status = exc.code
        error_raw, error_headers = _error_body(exc)
    except (urllib.error.URLError, OSError, http.client.HTTPException):  # 名前解決・接続・時間切れ・途中で切れた
        failed = True
    except ValueError:  # 送る前の検証をすり抜けた不正な値。文面にキーが載り得るので持ち出さない
        rejected = True
    if failed:
        return LLMConnectionError()
    if rejected:
        return LLMResponseError()
    if status is not None:
        return LLMResponseError(status, api_error=api_error.from_body(error_raw, error_headers, api_key=api_key))
    if plain is not None:
        answer = parse_plain(plain)
        if not answer:
            return LLMResponseError()
        on_delta(answer)
        return answer
    if finished == "error":
        return LLMResponseError(api_error=api_error.from_json(error_event, None, api_key=api_key))
    if finished != "done":
        return LLMConnectionError()  # 全部届いた印が無い(途中で切れた)。途中の文字を答えにしない
    answer = "".join(pieces)
    if not answer:
        return LLMResponseError()
    return answer


def ask_stream_with_retries(
    send: Callable[[], str | LLMError],
    fall_back: Callable[[], str],
    max_retries: int,
    sleep: Callable[[float], object],
    received: Callable[[], bool],
) -> str:
    """文字が1つも届いていない間だけ、ask_with_retries と同じに再試行する。

    received() が真(文字が届いた後)の失敗は再試行しない。届く前の STREAM_FALLBACK_STATUS_CODES は fall_back() を1回だけ呼ぶ。
    """
    attempt = 0
    while True:
        result = send()
        if isinstance(result, str):
            return result
        if received():
            raise result
        if isinstance(result, LLMResponseError) and result.status_code in STREAM_FALLBACK_STATUS_CODES:
            return fall_back()
        if attempt >= max_retries or not is_retryable(result):
            raise result
        sleep(min(2**attempt, MAX_RETRY_WAIT_SECONDS))
        attempt += 1
