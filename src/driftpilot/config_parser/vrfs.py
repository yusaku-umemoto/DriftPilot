"""トップレベルの VRF の見出しを IR の vrfs に写す extractor。

読む見出しは `vrf definition <name>`(IOS)、`ip vrf <name>`(古い IOS)、`vrf instance <name>`(Arista)、
`vrf context <name>`(NXOS / Arista)の4種類。同じ名前は4種類の間で共通に、先に出た1つだけを読む。
interface 単位の VRF は extract_interfaces が、`vrf context` の下の経路は extract_static_routes が読む。

REQ-86(ADR-0105)で、見出しの中の今の項目に写らない行も読む。直下の行は、まず見出しの種類ごとの表(rd・address-family)で試し、
当たらなければ共通の表(scope `vrf`)で試す。`description`・`vni` は項目に、`route-target <残り>` は route_targets に、
ほかの子を持たない行は原文を options に足す。子を持つ行(読める address-family と、options の書式が読んだ行)の中は
scope `vrf block` で読み、見出しの次の段からの道すじを ` > ` でつなぐ。
今の項目と同じ設定の、今は読まない形(rd・address-family で始まる行とその中、数でない vni)は options に入れず未解釈に残す
(REQ-84 と同じ扱い)。`ip route`・`ipv6 route` は static_routes が読むので入れない。
道すじのどの段かが、語のどれかが password・secret・key か語の中に鍵が埋まった行(device_settings と同じ判定)は、
その行も子孫も未解釈。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Vrf, new_vrf
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 今の項目と同じ設定を書く行と、ほかの extractor が読む行の頭の語。options に入れない(REQ-86、REQ-84 と同じ扱い)
_OWNED: tuple[str, ...] = ("rd", "address-family", "vni", "description", "ip route", "ipv6 route")
_SEPARATOR = " > "
_COMMON_SCOPE = "vrf"
_BLOCK_SCOPE = "vrf block"


@dataclass
class _Ctx:
    vrfs: dict[str, Vrf]
    current: Vrf | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False
    # 見出しの次の段からその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _definition(m: re.Match[str], ctx: _Ctx) -> bool:
    """vrf を作る。同じ名前がすでにあれば読まない(その行と子行は未解釈)。"""
    if m["name"] in ctx.vrfs:
        return False
    ctx.current = new_vrf(m["name"])
    ctx.vrfs[m["name"]] = ctx.current
    return True


def _rd(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.current["rd"] = m["value"]
    return True


def _address_family(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.current["address_families"].append(m["family"])
    return True


def _description(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    ctx.current["description"] = m["text"]
    return True


def _vni(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.current["vni"] = int(m["vni"])
    return True


def _route_target(m: re.Match[str], ctx: _Ctx) -> bool:
    """`<残り>` を route_targets に足す。子を持つ行の中では `<道すじ> > <残り>` にする。"""
    if ctx.has_children or has_secret(*ctx.path):
        return False
    _add(ctx.current["route_targets"], _SEPARATOR.join([*ctx.path[:-1], m["rest"]]))
    return True


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は道すじを options に足す。子を持つ行は解釈済みにだけする(中の行は呼び出し側が読む)。"""
    if has_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.current["options"], _SEPARATOR.join(ctx.path))
    return True


_SECRET_NOTE = (
    "道すじのどの段かの語が password・secret・key か、語の中に鍵が埋まった(key, や password= などの)行は、"
    "その行も子孫も未解釈"
)

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vrfs.definition", "top", r"vrf definition (?P<name>\S+)", "vrfs[name]", "vrf definition RED",
        _definition, note="手続き。同じ名前が二度目に現れたら読まない",
    ),
    Pattern("vrfs.rd", "vrf definition", r"rd (?P<value>\S+)", "rd", "rd 65000:1", _rd),
    Pattern(
        "vrfs.address_family", "vrf definition", r"address-family (?P<family>ipv4|ipv6)", "address_families",
        "address-family ipv4", _address_family,
        note=(
            "手続き。address-family の見出し行だけを出現順に address_families に足す。"
            "その中の行は vrfs.block.route_target・vrfs.block.option が読む"
        ),
    ),
    Pattern(
        "vrfs.instance", "top", r"vrf instance (?P<name>\S+)", "vrfs[name]", "vrf instance MGMT",
        _definition,
        note=(
            "手続き。同じ名前が二度目に現れたら読まない(vrf definition / instance / context で共通)。"
            "中の行は共通の表(scope vrf)だけで読む"
        ),
    ),
    Pattern(
        "vrfs.context", "top", r"vrf context (?P<name>\S+)", "vrfs[name]", "vrf context RED",
        _definition,
        note=(
            "手続き。同じ名前が二度目に現れたら読まない(vrf definition / instance / context で共通)。"
            "子行の ip route / ipv6 route は static_routes が読む"
        ),
    ),
    Pattern(
        "vrfs.ip_vrf", "top", r"ip vrf (?P<name>\S+)", "vrfs[name]", "ip vrf RED",
        _definition,
        note=(
            "手続き。同じ名前が二度目に現れたら読まない(vrf definition / instance / context / ip vrf で共通)。"
            "address_families は空。rd 以外の子行は共通の表(scope vrf)で読む"
        ),
    ),
    Pattern("vrfs.ip_vrf_rd", "ip vrf", r"rd (?P<value>\S+)", "rd", "rd 64500:1", _rd),
    Pattern("vrfs.context_rd", "vrf context", r"rd (?P<value>\S+)", "rd", "rd auto", _rd),
    Pattern(
        "vrfs.context_address_family", "vrf context", r"address-family (?P<family>ipv4|ipv6)(?: unicast)?",
        "address_families", "address-family ipv4 unicast", _address_family,
        note=(
            "手続き。address-family の見出し行だけを出現順に address_families に足す。"
            "その中の行は vrfs.block.route_target・vrfs.block.option が読む。"
            "unicast 以外(multicast など)の形は、その中の行も含めて読まない(未解釈)"
        ),
    ),
    # REQ-86。見出しの種類ごとの表(上)に当たらなかった直下の行を読む共通の表
    Pattern(
        "vrfs.description", _COMMON_SCOPE, r"description (?P<text>.+)", "description", "description management network",
        _description,
        note="行の残りの原文を写す。2回あれば後の行。子を持つ行と、語のどれかが password・secret・key の行(語の中に鍵が埋まった形も)は未解釈",
    ),
    Pattern(
        "vrfs.vni", _COMMON_SCOPE, r"vni (?P<vni>\d+)", "vni", "vni 50001", _vni,
        note="整数にして写す。2回あれば後の行。数でない形と子を持つ行は未解釈",
    ),
    Pattern(
        "vrfs.route_target", _COMMON_SCOPE, r"route-target (?P<rest>.+)", "route_targets", "route-target both 65000:1",
        _route_target,
        note=(
            "手続き。`<残り>` を route_targets の集合に足す(同じ文字列は重ねず、文字列の昇順)。向きは分けない。"
            "子を持つ行と、語のどれかが password・secret・key の行(語の中に鍵が埋まった形も)は未解釈"
        ),
    ),
    Pattern(
        "vrfs.option", _COMMON_SCOPE, rf"(?!(?:{'|'.join(_OWNED)})(?: |$)).+", "options", "ip pim ssm range 232.0.0.0/8",
        _option,
        note=(
            "手続き。ほかの書式に当たらない子を持たない行の原文を options の集合に足す(同じ文字列は重ねず、文字列の昇順)。"
            "子を持つ行は文字列にしないが解釈済みにし、中の行を vrfs.block.route_target・vrfs.block.option で読む。"
            "今の項目と同じ設定を書く行とほかの extractor が読む行(" + "・".join(_OWNED) + " で始まる行)は入れず未解釈。"
            + _SECRET_NOTE
        ),
    ),
    Pattern(
        "vrfs.block.route_target", _BLOCK_SCOPE, r"route-target (?P<rest>.+)", "route_targets",
        "route-target both auto evpn", _route_target,
        note=(
            "手続き。子を持つ行の中の `route-target <残り>` を、見出しの次の段からの道すじに続けて `<道すじ> > <残り>` にし、"
            "route_targets の集合に足す(例 `address-family ipv4 unicast > both auto evpn`)。"
            "子を持つ行は未解釈。" + _SECRET_NOTE
        ),
    ),
    Pattern(
        "vrfs.block.option", _BLOCK_SCOPE, r".+", "options", "maximum routes 1000", _option,
        note=(
            "手続き。子を持つ行の中の子を持たない行を、見出しの次の段からの道すじを ` > ` でつないだ文字列にして"
            "options に足す(見出しは含めない。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            + _SECRET_NOTE
        ),
    ),
)

# 見出しのパターン ID → 子行を最初に試す scope。無い見出し(vrf instance)は共通の表だけで読む
_CHILD_SCOPES = {"vrfs.definition": "vrf definition", "vrfs.ip_vrf": "ip vrf", "vrfs.context": "vrf context"}
# 子を持つときに中を読むパターン
_READS_INSIDE = frozenset({"vrfs.address_family", "vrfs.context_address_family", "vrfs.option", "vrfs.block.option"})


def _read_block(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """子を持つ行の中を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, _BLOCK_SCOPE, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children and pattern.id in _READS_INSIDE:
            _read_block(child, ctx.path, ctx, interpreted)


def extract_vrfs(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Vrf]:
    """VRF の見出し行から、名前をキーにした Vrf の辞書を返す。"""
    ctx = _Ctx({})
    for block in tree.children:
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)
        scope = _CHILD_SCOPES.get(heading.id)
        for line in block.children:
            ctx.path = [line.text]
            ctx.has_children = bool(line.children)
            pattern = apply_first(PATTERNS, scope, line.text, ctx) if scope is not None else None
            if pattern is None:
                pattern = apply_first(PATTERNS, _COMMON_SCOPE, line.text, ctx)
            if pattern is None:
                continue
            interpreted.mark(line, pattern.id)
            if line.children and pattern.id in _READS_INSIDE:
                _read_block(line, ctx.path, ctx, interpreted)
    return ctx.vrfs
