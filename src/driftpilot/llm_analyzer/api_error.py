"""API が返したエラーの文と待つ秒を取り出し、掃除する(ADR-0168 決定1〜3。REQ-230 §2)。

取り出す順(決定2): error.message → error(文字のとき)→ response.error.message → message → detail(文字のとき)。
最初に見つかった空でない文字を使う。文を取った辞書の code(無ければ type)が [A-Za-z0-9_.-]{1,64} なら、頭に「[コード] 」を付ける。
文は JSON の本文だけから取る(JSON でない本文は出さない)。

掃除の順(決定3。clean_api_message):
1. 接続が持つキーがそのまま文にあれば <キー> に置き換える
2. Unicode の種類が Cc・Cf の文字を外す(タブは空白に、改行は残す)
3. 行に分け、最初の 10 行だけを見る。1 行ずつ redact.redact_text に通す(AI に送るときと同じ判定)
4. 32 文字以上の切れ目の無い並び([A-Za-z0-9+/=_.\\-]{32,})を <長い語> にする
5. 行の中の連続する空白を 1 つにし、行の前後の空白を外し、空の行を落とす(改行は残す)。500 文字を超えたら切って「…（以下略）」
6. 空になれば None

★ 生の本文は読む関数の中の変数にだけ置く。属性・グローバル・ログ・例外の文に残さない。どの関数も例外を外へ出さない。
"""

import json
import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MAX_ERROR_BODY_BYTES = 65_536  # HTTP のエラーの本文を読む上限
MAX_API_MESSAGE_CHARS = 500  # 出す文の上限(掃除の後)
MAX_API_MESSAGE_LINES = 10  # 見る行の上限
MAX_RETRY_AFTER_SECONDS = 86_400
LONG_RUN_CHARS = 32  # この長さ以上の切れ目の無い並びを伏せる
KEY_MARK = "<キー>"
LONG_RUN_MARK = "<長い語>"
CUT_MARK = "…（以下略）"

_CODE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")
_LONG_RUN = re.compile(r"[A-Za-z0-9+/=_.\-]{%d,}" % LONG_RUN_CHARS)
# 見出しの値として読む数字の長さの上限(とても長い数字の並びで int を作らない)
_MAX_HEADER_DIGITS = 18


@dataclass(frozen=True)
class ApiErrorText:
    """掃除した後のエラーの文(無ければ None)と、待つ秒(1〜86,400。無ければ None)。"""

    message: str | None
    retry_after: int | None

    def __repr__(self) -> str:  # 文を出さない(ログ・例外の文に載っても中身が出ないように)
        has_text = "文あり" if self.message else "文なし"
        return f"ApiErrorText({has_text}, retry_after={self.retry_after})"


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _code_of(holder: Any) -> str | None:
    if not isinstance(holder, dict):
        return None
    for name in ("code", "type"):
        value = holder.get(name)
        if isinstance(value, str) and _CODE.fullmatch(value):
            return value
    return None


def _with_code(text: str, holder: Any) -> str:
    code = _code_of(holder)
    return f"[{code}] {text}" if code is not None else text


def message_of(data: object) -> str | None:
    """JSON の値から文を取る(コードの頭付け込み)。見つからなければ None。"""
    try:
        if not isinstance(data, dict):
            return None
        error = data.get("error")
        if isinstance(error, dict) and (text := _text(error.get("message"))) is not None:
            return _with_code(text, error)
        if (text := _text(error)) is not None:
            return text
        response = data.get("response")
        inner = response.get("error") if isinstance(response, dict) else None
        if isinstance(inner, dict) and (text := _text(inner.get("message"))) is not None:
            return _with_code(text, inner)
        if (text := _text(data.get("message"))) is not None:
            # SDK が error の中だけを渡した本体・Azure の {"statusCode":…, "message":…}・流す途中の error の事象
            return _with_code(text, data)
        if (text := _text(data.get("detail"))) is not None:
            return text
    except Exception:  # noqa: BLE001 - 読めない値は文なし
        return None
    return None


def _header_int(headers: Any, name: str) -> int | None:
    value = headers.get(name)
    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > _MAX_HEADER_DIGITS or not (text.isascii() and text.isdigit()):
        return None
    return int(text)


def retry_after_of(headers: Any) -> int | None:
    """retry-after が整数の秒ならその値、無ければ retry-after-ms を秒に切り上げる。1〜86,400 の外は None。

    headers は get(name) を持つものなら何でもよい(http.client・httpx 系)。名前の大文字・小文字は headers の get に任せる。
    """
    if headers is None:
        return None
    try:
        seconds = _header_int(headers, "retry-after")
        if seconds is None:
            millis = _header_int(headers, "retry-after-ms")
            seconds = math.ceil(millis / 1000) if millis is not None else None
    except Exception:  # noqa: BLE001 - 見出しを読めなければ秒なし
        return None
    if seconds is None or not 1 <= seconds <= MAX_RETRY_AFTER_SECONDS:
        return None
    return seconds


def _drop_unseen(text: str) -> str:
    """Cc・Cf の文字を外す。タブは空白に、CR と CRLF は改行にする(改行は残す)。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
    return "".join(ch for ch in text if ch == "\n" or unicodedata.category(ch) not in ("Cc", "Cf"))


def clean_api_message(text: str, *, secret_words: Sequence[str], api_key: str | None = None) -> str | None:
    """エラーの文を掃除する(決定3)。空になれば None。読めなければ(判定の失敗を含む)None。"""
    from driftpilot.llm_analyzer.redact import redact_text  # redact は payload・types・client を読む(循環を避ける)

    try:
        if not isinstance(text, str):
            return None
        if api_key:
            text = text.replace(api_key, KEY_MARK)
        text = _drop_unseen(text)
        lines = text.split("\n")[:MAX_API_MESSAGE_LINES]
        shown: list[str] = []
        for line in lines:
            if not line.strip():
                continue
            judged = redact_text(line, secret_words)
            judged = _LONG_RUN.sub(LONG_RUN_MARK, judged)
            judged = " ".join(judged.split())
            if judged:
                shown.append(judged)
        result = "\n".join(shown)
        if not result:
            return None
        if len(result) > MAX_API_MESSAGE_CHARS:
            result = result[:MAX_API_MESSAGE_CHARS] + CUT_MARK
        return result
    except Exception:  # noqa: BLE001 - 掃除できない文は出さない
        return None


def _default_words() -> tuple[str, ...]:
    from driftpilot.llm_analyzer.masking import normalize_words

    return normalize_words()


def from_json(data: object, headers: Any, *, api_key: str | None) -> ApiErrorText | None:
    """JSON の値と見出しから。文も秒も無ければ None。接続の部品は既定の秘密の語で掃除する。"""
    try:
        raw_message = message_of(data)
        message = (
            clean_api_message(raw_message, secret_words=_default_words(), api_key=api_key)
            if raw_message is not None
            else None
        )
        retry_after = retry_after_of(headers)
    except Exception:  # noqa: BLE001
        return None
    if message is None and retry_after is None:
        return None
    return ApiErrorText(message, retry_after)


def from_body(raw: bytes, headers: Any, *, api_key: str | None) -> ApiErrorText | None:
    """HTTP のエラーの本文(先頭だけ)と見出しから。UTF-8 の JSON でなければ文は None。"""
    data: object = None
    try:
        if raw:
            data = json.loads(raw.decode("utf-8"))
    except Exception:  # noqa: BLE001 - JSON でない・UTF-8 でない・途中で切れた本文は文なし
        data = None
    return from_json(data, headers, api_key=api_key)


def for_display(error: "ApiErrorText | None", secret_words: Sequence[str]) -> ApiErrorText | None:
    """画面と CLI に出す前に、利用者の秘密の語を足した語でもう一度掃除する。秒はそのまま。文も秒も無ければ None。"""
    if error is None:
        return None
    try:
        from driftpilot.llm_analyzer.masking import normalize_words

        message = error.message
        if message is not None:
            try:
                words = normalize_words(secret_words)
            except Exception:  # noqa: BLE001 - 語が読めなければ文を出さない(伏せられないため)
                message = None
            else:
                message = clean_api_message(message, secret_words=words)
        retry_after = error.retry_after
    except Exception:  # noqa: BLE001
        return None
    if message is None and retry_after is None:
        return None
    return ApiErrorText(message, retry_after)
