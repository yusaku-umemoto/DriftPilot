"""最上位の QoS のブロックと行を IR の qos に写す extractor(ADR-0066、REQ-77)。

読むのは `class-map …`・`policy-map …`・`table-map …`・`control-plane` のブロックと、子を持たない
`qos …`・`mls qos …`・`auto qos …`(no 付きも)の行。ブロックの中の子を持たない行は、見出しの次の段からの
道すじを ` > ` でつないだ文字列の集合にする。子を持つ行は文字列にしないが解釈済みにする。
`policy-map` の中の `class` の並びは持たない(比べない。ADR-0066 決定3)。
インターフェースの中の行は interfaces.py が読む。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.ir import Qos, new_qos
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 語のどれかがこれらの行は入れない(未解釈に残す。依頼書 §3-1)。大文字小文字は区別しない
_SECRET_WORDS = frozenset({"password", "secret", "key"})
_SEPARATOR = " > "


@dataclass
class _Ctx:
    qos: Qos | None = None
    # 最上位の行を読んでいる間の状態
    has_children: bool = False
    # 見出しを読んだ後、中の行を写す先と中の行の scope。中を読まない行では None
    target: list[str] | None = None
    scope: str | None = None
    # 中の行を読んでいる間の状態。見出しの次の段からその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _block(ctx: _Ctx) -> Qos:
    """qos を返す。無ければ作る(行が写せたときにだけ呼ぶ)。"""
    if ctx.qos is None:
        ctx.qos = new_qos()
    return ctx.qos


def _has_secret(*texts: str) -> bool:
    return any(word.lower() in _SECRET_WORDS for text in texts for word in text.split())


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _class_map(m: re.Match[str], ctx: _Ctx) -> bool:
    if _has_secret(m.string):
        return False
    entry = _block(ctx)["class_maps"].setdefault(
        m["name"], {"type": m["type"], "match_type": m["match_type"], "settings": []}
    )
    ctx.target, ctx.scope = entry["settings"], "class-map"
    return True


def _policy_map(m: re.Match[str], ctx: _Ctx) -> bool:
    if _has_secret(m.string):
        return False
    entry = _block(ctx)["policy_maps"].setdefault(m["name"], {"type": m["type"], "settings": []})
    ctx.target, ctx.scope = entry["settings"], "policy-map"
    return True


def _table_map(m: re.Match[str], ctx: _Ctx) -> bool:
    if _has_secret(m.string):
        return False
    entry = _block(ctx)["table_maps"].setdefault(m["name"], {"settings": []})
    ctx.target, ctx.scope = entry["settings"], "table-map"
    return True


def _control_plane(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.target, ctx.scope = _block(ctx)["control_plane"], "control-plane"
    return True


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行だけ、原文を options に足す。子を持つ行は子も含めて未解釈。"""
    if ctx.has_children or _has_secret(m.string):
        return False
    _add(_block(ctx)["options"], m.string)
    return True


def _setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は道すじを集合に足す。子を持つ行は解釈済みにだけする。"""
    if _has_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.target, _SEPARATOR.join(ctx.path))
    return True


_NOTE_HEADING = (
    "手続き。名前で引く辞書に項目を作る。同じ名前の見出しが2回あれば同じ項目にまとめる(type・match_type は最初の見出しの値)。"
    "中身の無い見出しも項目を作る。語のどれかが password・secret・key の見出しは、見出しも中も未解釈"
)
_NOTE_SETTING = (
    "手続き。ブロックの中の子を持たない行を、見出しの次の段からの道すじを ` > ` でつないだ文字列にして集合に足す"
    "(見出しは含めない。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
    "道すじのどの段かの語が password・secret・key の行は、その行も子孫も未解釈"
)

# 並び＝優先順位。option は qos で始まる行の残りすべてに当たるので、見出しの後ろに置く。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "qos.class_map", "top",
        r"class-map(?: type (?P<type>\S+))?(?: (?P<match_type>match-any|match-all))? (?P<name>\S+)",
        "qos.class_maps[name]", "class-map match-any CM-VOICE", _class_map, note=_NOTE_HEADING,
    ),
    Pattern(
        "qos.policy_map", "top", r"policy-map(?: type (?P<type>\S+))? (?P<name>\S+)", "qos.policy_maps[name]",
        "policy-map PM-EDGE", _policy_map, note=_NOTE_HEADING,
    ),
    Pattern(
        "qos.table_map", "top", r"table-map (?P<name>\S+)", "qos.table_maps[name]",
        "table-map TM-EXAMPLE", _table_map, note=_NOTE_HEADING,
    ),
    Pattern(
        "qos.control_plane", "top", r"control-plane", "qos.control_plane", "control-plane", _control_plane,
        note="手続き。中の行を qos.control_plane に写す。機種の既定の CoPP も名前で除かずに読む(ADR-0066)",
    ),
    Pattern(
        "qos.option", "top", r"(?:no )?(?:mls qos|auto qos|qos)(?: .+)?", "qos.options", "auto qos srnd4", _option,
        note=(
            "手続き。子を持たない行の原文を options の集合に足す(重ねない。昇順)。"
            "子を持つ行と、語のどれかが password・secret・key の行は、子も含めて未解釈"
        ),
    ),
    Pattern(
        "qos.class_map.setting", "class-map", r".+", "qos.class_maps[name].settings", "match dscp ef", _setting,
        note=_NOTE_SETTING,
    ),
    Pattern(
        "qos.policy_map.setting", "policy-map", r".+", "qos.policy_maps[name].settings", "class class-default",
        _setting, note=_NOTE_SETTING + "。`class` の並びは持たず、並びだけの違いは構造化の差分に出ない(ADR-0066 決定3)",
    ),
    Pattern(
        "qos.table_map.setting", "table-map", r".+", "qos.table_maps[name].settings", "map from 0 to 8", _setting,
        note=_NOTE_SETTING,
    ),
    Pattern(
        "qos.control_plane.setting", "control-plane", r".+", "qos.control_plane",
        "service-policy input system-cpp-policy", _setting, note=_NOTE_SETTING,
    ),
)


def _read_block(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """ブロックの中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, ctx.scope, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(child, ctx.path, ctx, interpreted)


def extract_qos(tree: HConfig, interpreted: InterpretedLines) -> Qos | None:
    """最上位の QoS のブロックと行を Qos に写す。1行も写せなければ None。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        ctx.target = ctx.scope = None
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        if ctx.scope is not None:
            _read_block(block, [], ctx, interpreted)
    return ctx.qos
