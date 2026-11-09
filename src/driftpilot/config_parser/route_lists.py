"""最上位の community-list・as-path のリストの行を IR の route_lists に写す extractor(ADR-0062、REQ-73)。

読むのは子を持たない `ip community-list …`・`ip extcommunity-list …`・`ip large-community-list …`・
`ip as-path access-list …` の1行。リストは上から順に当てるので、`entries` は `permit|deny` から後ろの原文を
書かれた順に足す(集合にしない。同じ文字列も重ねる。ADR-0062 決定1)。値(コミュニティ・正規表現)はそろえない。
`permit`・`deny` の無い行、Arista の番号つきの形(`… seq 10 permit …`)など形の合わない行は未解釈(決定2)。
route-map の `match …` とリストの名前は突き合わせない。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from hier_config import HConfig

from driftpilot.config_parser.ir import RouteLists, new_route_lists
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines


@dataclass
class _Ctx:
    route_lists: RouteLists | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False


def _entry(kind: str) -> Callable[[re.Match[str], _Ctx], bool]:
    """route_lists[kind] の名前のリストに、`permit|deny` から後ろを書かれた順に足す apply を返す。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if ctx.has_children:
            return False
        if ctx.route_lists is None:
            ctx.route_lists = new_route_lists()
        route_list = ctx.route_lists[kind].setdefault(m["name"], {"type": None, "entries": []})
        if route_list["type"] is None:
            route_list["type"] = m.groupdict().get("type")
        route_list["entries"].append(m["entry"])
        return True

    return apply


_TYPE = r"(?: (?P<type>standard|expanded|regexp))?"
_REST = r" (?P<name>\S+) (?P<entry>(?:permit|deny) .+)"
_NOTE = (
    "手続き。名前のリストの entries に、permit・deny から後ろの原文を書かれた順に足す(同じ文字列も重ねる)。"
    "{type}子を持つ行は未解釈。permit・deny の無い形と、番号つきの形(`… seq 10 permit …`)は表に当たらず未解釈"
)
_TYPE_NOTE = "type は、そのリストで種類(standard・expanded・regexp)を書いた最初の行の値。どの行にも無ければ None。"

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "route_lists.community", "top", rf"ip community-list{_TYPE}{_REST}", "route_lists.community[name]",
        "ip community-list expanded CL-EXAMPLE permit _65000_", _entry("community"),
        note=_NOTE.format(type=_TYPE_NOTE),
    ),
    Pattern(
        "route_lists.extcommunity", "top", rf"ip extcommunity-list{_TYPE}{_REST}", "route_lists.extcommunity[name]",
        "ip extcommunity-list ECL-EXAMPLE permit soo 192.0.2.1:1", _entry("extcommunity"),
        note=_NOTE.format(type=_TYPE_NOTE),
    ),
    Pattern(
        "route_lists.large_community", "top", rf"ip large-community-list{_TYPE}{_REST}",
        "route_lists.large_community[name]", "ip large-community-list LCL-EXAMPLE permit 65000:1:1",
        _entry("large_community"), note=_NOTE.format(type=_TYPE_NOTE),
    ),
    Pattern(
        "route_lists.as_path", "top", rf"ip as-path access-list{_REST}", "route_lists.as_path[name]",
        "ip as-path access-list AS-EXAMPLE permit ^65000$", _entry("as_path"),
        note=_NOTE.format(type="type は None。"),
    ),
)


def extract_route_lists(tree: HConfig, interpreted: InterpretedLines) -> RouteLists | None:
    """最上位の community-list・as-path のリストの行を RouteLists に写す。1行も写せなければ None。"""
    ctx = _Ctx()
    for line in tree.children:
        ctx.has_children = bool(line.children)
        if (pattern := apply_first(PATTERNS, "top", line.text, ctx)) is not None:
            interpreted.mark(line, pattern.id)
    return ctx.route_lists
