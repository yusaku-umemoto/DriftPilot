"""利用者が定義する形式 custom-http-json の定義・決まり・差し込み・回答の取り出し・応答の形の木(ADR-0073 決定5。REQ-101 §2〜§4)。

通信はしない(送るのは custom_http_client)。定義はプロファイル(llm-profiles.json)の custom の欄と、書き出したファイルに入る。

決まり(custom_problem。読むときも書くときも、画面の保存でも同じ):
- url: https だけ(http は loopback だけ。http_json.is_allowed_url)。# は書けない。問い合わせのキーにキーらしい名前は書けない
- auth: bearer・api-key・x-api-key・none。キーは保管庫だけで、定義には入れない
- headers: 文字列だけ。キーらしい名前と、DriftPilot・送信の部品が自分で付ける名前は書けない
- body: JSON のオブジェクト。文字列の値に {{prompt}} が要る。知らない印は書けない(キーの名前には差し込まない)
- answer_path: choices.0.message.content の形(. で区切る。空の部分は書けない)

応答の形の木(response_shape)は、キーの名前と値の型だけを持ち、値の中身は持たない(ADR-0034 決定4)。
"""

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from driftpilot.llm_analyzer.http_json import is_allowed_url
from driftpilot.llm_analyzer.wire import is_keylike

CUSTOM_HTTP_JSON = "custom-http-json"
CUSTOM_AUTH_BEARER = "bearer"
CUSTOM_AUTH_API_KEY = "api-key"
CUSTOM_AUTH_X_API_KEY = "x-api-key"
CUSTOM_AUTH_NONE = "none"
CUSTOM_AUTHS = (CUSTOM_AUTH_BEARER, CUSTOM_AUTH_API_KEY, CUSTOM_AUTH_X_API_KEY, CUSTOM_AUTH_NONE)
# 定義の欄(この順で書く)
CUSTOM_FIELDS = ("url", "auth", "headers", "body", "answer_path")
MARKS = frozenset({"model", "prompt", "max_output_tokens"})
# キーらしい見出しの名前(同じ名前と、語を含む名前。大文字・小文字を問わない。REQ-101 §2)
KEYLIKE_HEADERS = frozenset({"authorization", "api-key", "x-api-key", "cookie", "proxy-authorization"})
KEYLIKE_HEADER_PARTS = ("key", "token", "secret", "password")
# DriftPilot と送信の部品が自分で付ける見出し(書けない。判断の記録 1)
OWN_HEADERS = frozenset({"content-type", "content-length", "host", "transfer-encoding", "connection"})
# 見出しの名前に使える文字(RFC 9110 の token)
_TOKEN = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_MARK = re.compile(r"\{\{([^{}]*)\}\}")
_PROMPT_MARK = "{{prompt}}"
_TOKENS_MARK = "{{max_output_tokens}}"

# 形の木の型の名前
SHAPE_KINDS = ("string", "number", "boolean", "null", "object", "array")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class CustomHttpJson:
    """custom-http-json の定義。headers は (名前, 値) の並び(書いた順)。キーは持たない。"""

    url: str
    auth: str
    headers: tuple[tuple[str, str], ...]
    body: Any
    answer_path: str

    def __hash__(self) -> int:  # body は表(JSON の文字で比べる)
        return hash((self.url, self.auth, self.headers, _canonical(self.body), self.answer_path))

    def to_json(self) -> dict[str, Any]:
        """ファイルに書く形(欄はこの順)。"""
        return {
            "url": self.url,
            "auth": self.auth,
            "headers": dict(self.headers),
            "body": self.body,
            "answer_path": self.answer_path,
        }


@dataclass(frozen=True)
class CustomProblem:
    """決まりに反した欄(field)と種類(kind)。index は headers の何番目か(headers のときだけ)。値は持たない。"""

    field: str
    kind: str
    index: int | None = None


def is_keylike_header(name: str) -> bool:
    lowered = name.casefold()
    return lowered in KEYLIKE_HEADERS or any(part in lowered for part in KEYLIKE_HEADER_PARTS)


def _is_visible_ascii(text: str, *, space: bool) -> bool:
    return all("!" <= ch <= "~" or (space and ch == " ") for ch in text)


def _strings(value: Any):
    """JSON の値の中の文字列の値(キーの名前は含めない)。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _url_problem(url: Any) -> str | None:
    if not isinstance(url, str) or not url.strip():
        return "empty"
    if "#" in url:
        return "fragment"
    if not _is_visible_ascii(url, space=False):
        return "invalid"
    if not is_allowed_url(url):
        return "scheme"
    if any(is_keylike(key) for key, _ in parse_qsl(urlsplit(url).query, keep_blank_values=True)):
        return "keylike"
    return None


def _header_problem(name: Any, value: Any, seen: set[str]) -> str | None:
    if not isinstance(name, str) or not name:
        return "name_empty"
    if not _TOKEN.fullmatch(name):
        return "name"
    if not isinstance(value, str) or not _is_visible_ascii(value, space=True):
        return "value"
    lowered = name.casefold()
    if lowered in seen:
        return "repeated"
    if is_keylike_header(name):
        return "keylike"
    if lowered in OWN_HEADERS:
        return "own"
    return None


def _body_problem(body: Any) -> str | None:
    if not isinstance(body, dict):
        return "object"
    try:
        json.dumps(body, allow_nan=False)
        texts = list(_strings(body))
    except (TypeError, ValueError, RecursionError):  # NaN などの JSON でない値・入れ子が深すぎる
        return "object"
    if any(mark not in MARKS for text in texts for mark in _MARK.findall(text)):
        return "unknown_mark"
    if not any(_PROMPT_MARK in text for text in texts):
        return "no_prompt"
    return None


def _path_problem(path: Any) -> str | None:
    if not isinstance(path, str) or not path.strip():
        return "empty"
    if any(part == "" for part in path.split(".")):
        return "shape"
    return None


def custom_problem(custom: Any) -> CustomProblem | None:
    """定義の決まりを確かめる。反していれば欄と種類(値は持たない)、通れば None。"""
    if not isinstance(custom, CustomHttpJson):
        return CustomProblem("custom", "shape")
    if (kind := _url_problem(custom.url)) is not None:
        return CustomProblem("url", kind)
    if custom.auth not in CUSTOM_AUTHS:
        return CustomProblem("auth", "choice")
    if not isinstance(custom.headers, tuple):
        return CustomProblem("headers", "shape")
    seen: set[str] = set()
    for index, pair in enumerate(custom.headers):
        if not isinstance(pair, tuple) or len(pair) != 2:
            return CustomProblem("headers", "shape", index)
        if (kind := _header_problem(pair[0], pair[1], seen)) is not None:
            return CustomProblem("headers", kind, index)
        seen.add(pair[0].casefold())
    if (kind := _body_problem(custom.body)) is not None:
        return CustomProblem("body", kind)
    if (kind := _path_problem(custom.answer_path)) is not None:
        return CustomProblem("answer_path", kind)
    return None


# --- 差し込み ---

_DROP = object()  # 上限が無いときに取り除く値の印


def _render(value: Any, values: dict[str, Any]) -> Any:
    if isinstance(value, str):
        whole = _MARK.fullmatch(value)
        if whole is not None and whole.group(1) in values:
            found = values[whole.group(1)]
            return _DROP if found is None else found  # 印だけの値は、その値の型で置き換える
        if values["max_output_tokens"] is None and _TOKENS_MARK in value:
            return _DROP
        # 文字列の一部の印は文字として埋め込む(本文は最後に json.dumps するので、引用符や改行は正しく逃がされる)
        return _MARK.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), value)
    if isinstance(value, dict):
        return {key: item for key, item in ((k, _render(v, values)) for k, v in value.items()) if item is not _DROP}
    if isinstance(value, list):
        return [item for item in (_render(v, values) for v in value) if item is not _DROP]
    return value


def render_body(body: dict[str, Any], *, model: str, prompt: str, max_output_tokens: int | None) -> dict[str, Any]:
    """本文のひな形に差し込む。キーの名前には差し込まない。上限が無いとき、{{max_output_tokens}} を使う値を取り除く。"""
    values = {"model": model, "prompt": prompt, "max_output_tokens": max_output_tokens}
    return _render(body, values)


# --- 回答の取り出し ---


def answer_at(data: Any, path: str) -> str | None:
    """answer_path の位置の値(. で区切る。リストでは数をリストの番号として読む)。位置が無い・文字列でなければ None。"""
    current = data
    for part in path.split("."):
        if isinstance(current, list):
            if not (part.isascii() and part.isdigit()) or int(part) >= len(current):
                return None
            current = current[int(part)]
        elif isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
        else:
            return None
    return current if isinstance(current, str) else None


# --- 応答の形の木 ---


@dataclass(frozen=True)
class ShapeNode:
    """応答の JSON の形の1項目。name はキーの名前(リストの要素は "0"、一番外は "")、kind は型の名前。

    path はその位置の answer_path の形の文字。回答の位置の形で書けないとき(一番外・キーの名前が空か . を含む、
    またはその下)は None。値の中身は持たない。
    """

    name: str
    kind: str
    path: str | None
    children: tuple["ShapeNode", ...] = ()


def _kind(value: Any) -> str:
    if isinstance(value, bool):  # bool は int の仲間なので先に見る
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    if isinstance(value, dict):
        return "object"
    return "array"


def _child_path(parent: str | None, name: str, *, root: bool) -> str | None:
    if not name or "." in name:
        return None
    if root:
        return name
    return None if parent is None else f"{parent}.{name}"


def _node(value: Any, name: str, path: str | None, *, root: bool = False) -> ShapeNode:
    kind = _kind(value)
    children: tuple[ShapeNode, ...] = ()
    if isinstance(value, dict):
        children = tuple(_node(item, key, _child_path(path, key, root=root)) for key, item in value.items())
    elif isinstance(value, list) and value:
        children = (_node(value[0], "0", _child_path(path, "0", root=root)),)  # 先頭の要素だけを開く
    return ShapeNode(name, kind, path, children)


def response_shape(data: Any) -> ShapeNode | None:
    """応答の JSON の形の木(名前と型だけ)。深すぎて作れないときは None。"""
    try:
        return _node(data, "", None, root=True)
    except RecursionError:
        return None


def parse_response(raw: bytes) -> tuple[bool, Any]:
    """応答の本文を JSON として読む。(読めたか, 値)。UTF-8 でない・JSON でない・深すぎるときは (False, None)。"""
    try:
        return True, json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        return False, None
