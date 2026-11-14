"""構造化比較の値を、人が読める文字にする(REQ-276・ADR-0197 決定2)。

`repr` のままだと `{'type': 'routed', 'parent_interface': None, …, 'evpn': []}` のように読みにくいので、
空の項目(None・False・空の文字列・空のリスト/タプル/辞書)を省き、`キー: 値` の形にする。
言語によらない(文を持たない)。JSON・保存する記録・AI に送る中身は変えない(この関数は文字の表示だけに使う)。
一番外の None は空の文字を返す。「（未設定）」と出すか空のままにするかは、呼ぶ側が決める。
"""

from dataclasses import asdict, is_dataclass
from typing import Any


def _is_dataclass_value(value: Any) -> bool:
    return is_dataclass(value) and not isinstance(value, type)


def _dict_body(value: dict[Any, Any]) -> str:
    """辞書の中身(`キー: 値` を `, ` でつなぐ)。空の項目は出さない。何も残らなければ空の文字。"""
    parts = []
    for key, item in value.items():
        text = _inner(item)
        if text is not None:
            parts.append(f"{key}: {text}")
    return ", ".join(parts)


def _braced(value: dict[Any, Any]) -> str:
    body = _dict_body(value)
    return "{" + body + "}"


def _element(value: Any) -> str:
    """リストの要素の文字。要素は省かない。辞書は `{…}`、入れ子のリストは `[…]`。"""
    if _is_dataclass_value(value):
        value = asdict(value)
    if isinstance(value, dict):
        return _braced(value)
    if isinstance(value, (list, tuple)):
        return _inner_list(value) or "[]"
    return _scalar(value)


def _list_body(value: list[Any] | tuple[Any, ...]) -> str:
    texts = [_element(v) for v in value]
    has_dict = any(_is_dataclass_value(v) or isinstance(v, dict) for v in value)
    return ("; " if has_dict else ", ").join(texts)


def _scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value if value else '""'
    return str(value)


def _inner(value: Any) -> str | None:
    """辞書の項目の値の文字。省く値(空)は None を返す。0 は False と見分けて残す。"""
    if _is_dataclass_value(value):
        value = asdict(value)
    if value is None or value is False:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        body = _dict_body(value)
        return "{" + body + "}" if body else None
    if isinstance(value, (list, tuple)):
        return _inner_list(value)
    return _scalar(value)


def _inner_list(value: list[Any] | tuple[Any, ...]) -> str | None:
    """辞書の中のリスト。空の要素(None・空の文字列・整えた後に空になる辞書/リスト)を除き、何も残らなければ None(項目ごと省く)。

    False は空とせず残す(`[false, true]` のように意味がある)。
    """
    kept = []
    for element in value:
        text = _inner(element) if element is not False else "false"
        if text is not None:
            kept.append((element, text))
    if not kept:
        return None
    has_dict = any(_is_dataclass_value(v) or isinstance(v, dict) for v, _ in kept)
    return "[" + ("; " if has_dict else ", ").join(text for _, text in kept) + "]"


def value_text(value: Any) -> str:
    """値を人が読める文字にする。決まりはモジュールの説明と ADR-0197・REQ-276 §2。"""
    if _is_dataclass_value(value):
        value = asdict(value)
    if isinstance(value, dict):
        return _braced(value) if not _dict_body(value) else _dict_body(value)
    if isinstance(value, (list, tuple)):
        return _list_body(value)
    return _scalar(value)
