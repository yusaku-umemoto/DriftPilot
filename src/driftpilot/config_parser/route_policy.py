"""最上位の `route-map`・`ip prefix-list`・`ipv6 prefix-list` を IR の route_maps・prefix_lists・ipv6_prefix_lists に写す
extractor(ADR-0047、REQ-58)。

route-map は `route-map <名前> permit|deny <番号>`(4語ちょうど)の見出しを、名前の項目の番号のエントリにする。
中の子を持たない行は、`description` を説明に、`match`・`set` の後ろと、ほかの行の原文を集合にする
(中身をそろえない。ADR-0047 決定2)。番号の無い見出しなど形の合わない見出しは、見出しも中も未解釈(決定3)。
prefix-list は Arista のブロックの形と Cisco・Arista の1行の形を、同じ名前の同じ項目にまとめる。プレフィックスは
ip_network の正規の書き方にそろえる(決定2)。`seq` の無い行は鍵が決まらないので未解釈(決定3)。
`ip community-list`・`ip as-path access-list` などは route_lists.py が読む(REQ-73)。
"""

import re
from bisect import insort
from collections.abc import Callable
from dataclasses import dataclass, field
from ipaddress import ip_network

from hier_config import HConfig

from driftpilot.config_parser.ir import (
    PrefixList,
    PrefixListEntry,
    RouteMap,
    RouteMapEntry,
    new_prefix_list,
    new_route_map_entry,
)
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_ROUTE_MAP_SCOPE = "route-map"
# prefix-list の後ろの ge・le・eq
_LENGTH_WORDS = frozenset({"ge", "le", "eq"})


@dataclass
class _Ctx:
    route_maps: dict[str, RouteMap] = field(default_factory=dict)
    # IP の版(4・6) → 名前 → PrefixList
    prefix_lists: dict[int, dict[str, PrefixList]] = field(default_factory=lambda: {4: {}, 6: {}})
    entry: RouteMapEntry | None = None
    prefix_list: PrefixList | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


# --- route-map ---


def _route_map(m: re.Match[str], ctx: _Ctx) -> bool:
    """名前の項目と番号のエントリを作る。同じ名前・番号の見出しは同じエントリにまとめ、action は後の行。"""
    entries = ctx.route_maps.setdefault(m["name"], {"entries": {}})["entries"]
    ctx.entry = entries.setdefault(str(int(m["seq"])), new_route_map_entry(m["action"]))
    ctx.entry["action"] = m["action"]
    return True


def _route_map_description(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.entry["description"] = m["text"]
    return True


def _route_map_list(key: str) -> Callable[[re.Match[str], _Ctx], bool]:
    """`match`・`set` の後ろか、行の原文(options)を集合に足す apply を返す。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if ctx.has_children:
            return False
        _add(ctx.entry[key], m["value"])
        return True

    return apply


# --- prefix-list ---


def _lengths(rest: str) -> dict[str, int] | None:
    """`ge <n>`・`le <n>`・`eq <n>` の並び(任意の順、それぞれ1回まで)を読む。形が合わなければ None。"""
    words = rest.split()
    if len(words) % 2:
        return None
    lengths: dict[str, int] = {}
    for word, value in zip(words[::2], words[1::2]):
        if word not in _LENGTH_WORDS or word in lengths or not (value.isascii() and value.isdigit()):
            return None
        lengths[word] = int(value)
    return lengths


def _entry(m: re.Match[str], version: int) -> PrefixListEntry | None:
    """エントリを読む。プレフィックスが読めない・版が違う・後ろの形が合わなければ None。"""
    try:
        network = ip_network(m["prefix"], strict=False)
    except ValueError:
        return None
    lengths = _lengths(m["rest"])
    if network.version != version or lengths is None:
        return None
    return {
        "action": m["action"],
        "prefix": str(network),
        "ge": lengths.get("ge"),
        "le": lengths.get("le"),
        "eq": lengths.get("eq"),
    }


def _list(ctx: _Ctx, version: int, name: str) -> PrefixList:
    return ctx.prefix_lists[version].setdefault(name, new_prefix_list())


def _prefix_block(version: int) -> Callable[[re.Match[str], _Ctx], bool]:
    """子を持つ `ip|ipv6 prefix-list <名前>` の項目を作る apply を返す。子の無い行は読まない。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if not ctx.has_children:
            return False
        ctx.prefix_list = _list(ctx, version, m["name"])
        return True

    return apply


def _prefix_entry_line(version: int) -> Callable[[re.Match[str], _Ctx], bool]:
    """1行の形のエントリを、名前の項目の seq の番号のエントリにする apply を返す。同じ番号は後の行。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        entry = _entry(m, version)
        if ctx.has_children or entry is None:
            return False
        _list(ctx, version, m["name"])["entries"][str(int(m["seq"]))] = entry
        return True

    return apply


def _prefix_description_line(version: int) -> Callable[[re.Match[str], _Ctx], bool]:
    """1行の形の説明を、名前の項目の description にする apply を返す。後の行を採る。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if ctx.has_children:
            return False
        _list(ctx, version, m["name"])["description"] = m["text"]
        return True

    return apply


def _prefix_entry(version: int) -> Callable[[re.Match[str], _Ctx], bool]:
    """ブロックの中のエントリを、seq の番号のエントリにする apply を返す。同じ番号は後の行。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        entry = _entry(m, version)
        if ctx.has_children or entry is None:
            return False
        ctx.prefix_list["entries"][str(int(m["seq"]))] = entry
        return True

    return apply


def _prefix_description(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.prefix_list["description"] = m["text"]
    return True


_ENTRY = r"seq (?P<seq>\d+) (?P<action>permit|deny) (?P<prefix>\S+)(?P<rest>(?: \S+)*)"
_ENTRY_NOTE = (
    "手続き。seq の番号(str(int(...)))を鍵にしたエントリにする。プレフィックスは str(ip_network(..., strict=False))。"
    "読めないプレフィックスと、{family} でないプレフィックスは未解釈。後ろは ge・le・eq の数を任意の順でそれぞれ1回まで。"
    "数でない・同じ語が2回・ほかの語がある行は未解釈。同じ番号が2回あれば後の行。子を持つ行は未解釈"
)


def _prefix_patterns(category: str, head: str, version: int, examples: dict[str, str]) -> tuple[Pattern, ...]:
    """`ip prefix-list`(IPv4)・`ipv6 prefix-list`(IPv6)の表。形は同じで、写し先と版だけが違う。"""
    family = f"IPv{version}"
    entry_note = _ENTRY_NOTE.format(family=family)
    return (
        Pattern(
            f"{category}.block", "top", rf"{head} (?P<name>\S+)", f"{category}[name]", examples["block"],
            _prefix_block(version),
            note=(
                "手続き。子を持つ見出しだけ、名前の項目を作る(1行の形と同じ名前なら同じ項目)。"
                f"子の無い行(`{head} sequence-number` など)は未解釈"
            ),
        ),
        Pattern(
            f"{category}.entry_line", "top", rf"{head} (?P<name>\S+) {_ENTRY}",
            f"{category}[name].entries[seq]", examples["entry_line"], _prefix_entry_line(version),
            note=f"{entry_note}。名前の項目が無ければ作る(ブロックの形と同じ名前なら同じ項目)",
        ),
        Pattern(
            f"{category}.description_line", "top", rf"{head} (?P<name>\S+) description (?P<text>.+)",
            f"{category}[name].description", examples["description_line"], _prefix_description_line(version),
            note="手続き。行の残りの原文を説明にする(後の行を採る)。名前の項目が無ければ作る。子を持つ行は未解釈",
        ),
        Pattern(
            f"{category}.entry", head, _ENTRY, f"{category}[name].entries[seq]", examples["entry"],
            _prefix_entry(version), note=entry_note,
        ),
        Pattern(
            f"{category}.description", head, r"description (?P<text>.+)", f"{category}[name].description",
            examples["description"], _prefix_description,
            note="手続き。行の残りの原文を説明にする(後の行を採る)。子を持つ行は未解釈",
        ),
    )


_LEAF_NOTE = "子を持つ行は未解釈"

# 並び＝優先順位。中の行の option は description・match・set の書式に当たらない行を受ける
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "route_maps.heading", "top", r"route-map (?P<name>\S+) (?P<action>permit|deny) (?P<seq>\d+)",
        "route_maps[name].entries[seq]", "route-map RM-IN permit 10", _route_map,
        note=(
            "手続き。名前の項目と、番号(str(int(...)))を鍵にしたエントリを作る。同じ名前・番号の見出しが2回あれば"
            "同じエントリにまとめ、action は後の行。番号の無い見出し、語の数が違う見出し、permit・deny でない見出しは、"
            "見出しも中も未解釈"
        ),
    ),
    Pattern(
        "route_maps.description", _ROUTE_MAP_SCOPE, r"description (?P<text>.+)",
        "route_maps[name].entries[seq].description", "description from upstream", _route_map_description,
        note=f"手続き。行の残りの原文を説明にする(後の行を採る)。{_LEAF_NOTE}",
    ),
    Pattern(
        "route_maps.match", _ROUTE_MAP_SCOPE, r"match (?P<value>.+)", "route_maps[name].entries[seq].match",
        "match ip address prefix-list PL-LOOPBACK", _route_map_list("match"),
        note=f"手続き。match の後ろの原文を集合に足す(同じ文字列は重ねず、文字列の昇順)。{_LEAF_NOTE}",
    ),
    Pattern(
        "route_maps.set", _ROUTE_MAP_SCOPE, r"set (?P<value>.+)", "route_maps[name].entries[seq].set",
        "set local-preference 200", _route_map_list("set"),
        note=f"手続き。set の後ろの原文を集合に足す(同じ文字列は重ねず、文字列の昇順)。{_LEAF_NOTE}",
    ),
    Pattern(
        "route_maps.option", _ROUTE_MAP_SCOPE, r"(?P<value>(?!(?:description|match|set)$).+)",
        "route_maps[name].entries[seq].options", "continue 20", _route_map_list("options"),
        note=(
            "手続き。description・match・set でない行の原文を options の集合に足す(同じ文字列は重ねず、文字列の昇順)。"
            f"description・match・set だけで残りの無い行は未解釈。{_LEAF_NOTE}"
        ),
    ),
    *_prefix_patterns(
        "prefix_lists", "ip prefix-list", 4,
        {
            "block": "ip prefix-list PL-LOOPBACK",
            "entry_line": "ip prefix-list PL-CORE seq 5 deny 203.0.113.0/24 le 32",
            "description_line": "ip prefix-list PL-CORE description core routes",
            "entry": "seq 20 permit 198.51.100.0/24 ge 25 le 32",
            "description": "description loopbacks",
        },
    ),
    *_prefix_patterns(
        "ipv6_prefix_lists", "ipv6 prefix-list", 6,
        {
            "block": "ipv6 prefix-list PL6",
            "entry_line": "ipv6 prefix-list PL6-CORE seq 5 deny 2001:db8:1::/48 le 64",
            "description_line": "ipv6 prefix-list PL6-CORE description core routes",
            "entry": "seq 10 permit 2001:db8::/32",
            "description": "description loopbacks",
        },
    ),
)

# 見出しのパターン → 中の行の scope
_INNER_SCOPES = {
    "route_maps.heading": _ROUTE_MAP_SCOPE,
    "prefix_lists.block": "ip prefix-list",
    "ipv6_prefix_lists.block": "ipv6 prefix-list",
}


def extract_route_policy(
    tree: HConfig, interpreted: InterpretedLines
) -> tuple[dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList]]:
    """最上位の route-map・prefix-list を (route_maps, prefix_lists, ipv6_prefix_lists) に写す。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        scope = _INNER_SCOPES.get(pattern.id)
        if scope is None:
            continue
        for line in block.children:
            ctx.has_children = bool(line.children)
            if (inner := apply_first(PATTERNS, scope, line.text, ctx)) is not None:
                interpreted.mark(line, inner.id)
    return ctx.route_maps, ctx.prefix_lists[4], ctx.prefix_lists[6]
