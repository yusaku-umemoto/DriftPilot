"""トップレベルの `vlan <id>` 行を IR の vlans に写す extractor。

VLAN 定義として扱うのは `vlan` 行だけで、interface から言及されただけの VLAN は含めない。
中の行は `name`・`trunk group <名前>`・`state <状態>`・`vn-segment <数>`(NX-OS)を読む(ADR-0058、REQ-69)。
見出しが範囲なら、範囲の中のすべての VLAN に同じ値を写す。`trunk group`・`state`・`vn-segment` の行は、
子を持てば読まない(その行も子孫も未解釈)。子を持たない `private-vlan …`・`remote-span`・`mtu <数>`・`media <1語>`・
`shutdown`・`said <数>` と no 付きは options に原文で読む(ADR-0126 決定2、REQ-171)。
`address locking` などのほかの行は未解釈のまま。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig
from hier_config.platforms.functions import expand_range

from driftpilot.config_parser.ir import Vlan, new_vlan
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines


@dataclass
class _Ctx:
    vlans: dict[str, Vlan]
    ids: list[int] = field(default_factory=list)
    name: str | None = None
    trunk_groups: list[str] = field(default_factory=list)
    state: str | None = None
    vn_segment: int | None = None
    options: list[str] = field(default_factory=list)
    has_children: bool = False


def _definition(m: re.Match[str], ctx: _Ctx) -> bool:
    """範囲を展開する。展開できない・空・定義済みの ID と重なる行は読まない。"""
    try:
        ids = expand_range(m["range"])
    except ValueError:
        return False
    if not ids or any(str(vlan_id) in ctx.vlans for vlan_id in ids):
        return False
    ctx.ids = list(ids)
    return True


def _name(m: re.Match[str], ctx: _Ctx) -> bool:
    """最初の name だけを読む。二度目以降は上書きせず読まない。"""
    if ctx.name is not None:
        return False
    ctx.name = m["text"]
    return True


def _trunk_group(m: re.Match[str], ctx: _Ctx) -> bool:
    """名前を trunk_groups に足す。同じ名前は重ねず、昇順の位置に入れる。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    if m["name"] not in ctx.trunk_groups:
        insort(ctx.trunk_groups, m["name"])
    return True


def _state(m: re.Match[str], ctx: _Ctx) -> bool:
    """最初の state だけを原文で読む。二度目以降と子を持つ行は読まない。"""
    if ctx.has_children or ctx.state is not None:
        return False
    ctx.state = m["value"]
    return True


def _vn_segment(m: re.Match[str], ctx: _Ctx) -> bool:
    """最初の vn-segment だけを整数で読む。二度目以降と子を持つ行は読まない。"""
    if ctx.has_children or ctx.vn_segment is not None:
        return False
    ctx.vn_segment = int(m["value"])
    return True


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    """原文(空白は1つ)を options に足す。同じ文字列は重ねず、昇順の位置に入れる。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    text = " ".join(m.string.split())
    if text not in ctx.options:
        insort(ctx.options, text)
    return True


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vlans.definition", "top", r"vlan (?P<range>\d[\d,\-]*)", "vlans[id]", "vlan 10", _definition,
        note="手続き。範囲(10,20-22)は展開し、全 ID に同じ name を付ける。定義済みの ID と重なる行は読まない",
    ),
    Pattern(
        "vlans.name", "vlan", r"name (?P<text>.+)", "vlans[id].name", "name USERS", _name,
        note="最初の name だけを読む",
    ),
    Pattern(
        "vlans.trunk_group", "vlan", r"trunk group (?P<name>\S+)", "vlans[id].trunk_groups",
        "trunk group MLAG-PEER", _trunk_group,
        note="手続き。名前を集合に足す(同じ名前は重ねず昇順。ADR-0058)。範囲の見出しなら全 ID に写す。"
        "後ろが1語でない行・子を持つ行は未解釈",
    ),
    Pattern(
        "vlans.state", "vlan", r"state (?P<value>\S+)", "vlans[id].state", "state suspend", _state,
        note="手続き。最初の行だけを原文で読む(ADR-0058)。2回目・後ろが1語でない行・子を持つ行は未解釈",
    ),
    Pattern(
        "vlans.vn_segment", "vlan", r"vn-segment (?P<value>\d+)", "vlans[id].vn_segment", "vn-segment 10010",
        _vn_segment,
        note="手続き。NX-OS の VLAN と VNI の対応。最初の行だけを整数で読む(ADR-0058)。"
        "2回目・後ろが数でない行・子を持つ行は未解釈",
    ),
    Pattern(
        "vlans.option", "vlan",
        r"(?:no )?(?:private-vlan .+|remote-span|mtu \d+|media \S+|shutdown|said \d+)",
        "vlans[id].options", "private-vlan primary", _option,
        note="手続き。private VLAN・RSPAN などの行の原文(空白は1つ)を集合に足す(同じ文字列は重ねず昇順。"
        "ADR-0126 決定2、REQ-171)。範囲の見出しなら全 ID に写す。子を持つ行・`address locking` などのほかの行は未解釈",
    ),
)


def extract_vlans(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Vlan]:
    """tree の `vlan` 行から、VLAN ID の文字列をキーにした Vlan の辞書を返す。

    すでに定義されたIDと重なる行は上書きせず、その行と子行を unparsed に残す。
    """
    vlans: dict[str, Vlan] = {}
    for block in tree.children:
        ctx = _Ctx(vlans)
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)

        for line in block.children:
            ctx.has_children = bool(line.children)
            if (pattern := apply_first(PATTERNS, "vlan", line.text, ctx)) is not None:
                interpreted.mark(line, pattern.id)
        for vlan_id in ctx.ids:
            vlan = new_vlan(vlan_id)
            vlan["name"] = ctx.name
            vlan["trunk_groups"] = list(ctx.trunk_groups)
            vlan["state"] = ctx.state
            vlan["vn_segment"] = ctx.vn_segment
            vlan["options"] = list(ctx.options)
            vlans[str(vlan_id)] = vlan
    return vlans
