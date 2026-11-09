"""トップレベルの `router ospf <id> [vrf <名前>]` を IR の ospf に、OSPFv3 の見出しを IR の ospfv3 に写す extractor。

passive-interface は、config に書かれているものをそのまま写す(冗長かどうかは判断しない)。

REQ-88(ADR-0107)で、ほかの書式に当たらない行も読む。子を持たない行は原文を options に、子を持つ行は中の子を持たない行を
見出しの次の段からの道すじ(` > ` でつなぐ)にして options に足す。今の項目と同じ設定の、今は読まない形
(`router-id`・`network`・`passive-interface`・`no passive-interface` で始まる行)は options に入れず未解釈に残す
(REQ-84 と同じ扱い。同じ設定が形によって項目と原文の2か所に分かれないように)。
OSPFv3(`router ospfv3 …`・`ipv6 router ospf …`)は、見出しの原文を鍵にして、中の行を同じ決まりで settings に写す。
道すじのどの段かが、語のどれかが password・secret・key か語の中に鍵が埋まった行(device_settings と同じ判定)か、
語に message-digest-key・authentication-key がある行は、その行も子孫も未解釈。
"""

import re
from bisect import insort
from dataclasses import dataclass, field
from ipaddress import AddressValueError, IPv4Address

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Ospf, Ospfv3, OspfNetwork, new_ospf
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 語のどれかがこれらの行は入れない(OSPF の認証の鍵。REQ-88 §3-1)。大文字小文字は区別しない
_KEY_WORDS = frozenset({"message-digest-key", "authentication-key"})
# 今の項目と同じ設定を書く行の頭の語。options に入れない(REQ-84 と同じ扱い)
_OWNED: tuple[str, ...] = ("router-id", "network", "passive-interface", "no passive-interface")
_SEPARATOR = " > "
_BLOCK_SCOPE = "router ospf block"
_V3_SCOPE = "router ospfv3"


@dataclass
class _Ctx:
    processes: dict[str, Ospf]
    current: Ospf | None = None
    v3: dict[str, Ospfv3] = field(default_factory=dict)
    # 読んでいる行が子を持つか
    has_children: bool = False
    # 中の行を写す先(options・settings)と、見出しの次の段からその行までの行の文字列
    target: list[str] | None = None
    path: list[str] = field(default_factory=list)


def _is_secret(*texts: str) -> bool:
    return has_secret(*texts) or any(word.lower() in _KEY_WORDS for text in texts for word in text.split())


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _area(value: str) -> IPv4Address:
    """area を IPv4Address に正規化する。数値指定(`1000`)は 32 ビットの値として変換する。"""
    if value.isdecimal():
        return IPv4Address(int(value))
    return IPv4Address(value)


def _process(m: re.Match[str], ctx: _Ctx) -> bool:
    """process を作る。同じ ID がすでにあれば読まない(その行と子行は未解釈)。"""
    if m["id"] in ctx.processes:
        return False
    ctx.current = new_ospf(int(m["id"]))
    ctx.current["vrf"] = m["vrf"]
    ctx.processes[m["id"]] = ctx.current
    ctx.target = ctx.current["options"]
    return True


def _router_id(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.current["router_id"] = str(IPv4Address(m["addr"]))
    return True


def _network(m: re.Match[str], ctx: _Ctx) -> bool:
    network: OspfNetwork = {
        "address": IPv4Address(m["addr"]),
        "wildcard": IPv4Address(m["wildcard"]),
        "area": _area(m["area"]),
    }
    ctx.current["networks"].append(network)
    return True


def _no_passive(m: re.Match[str], ctx: _Ctx) -> bool:
    if m["if"] == "default":
        return False
    ctx.current["non_passive_interfaces"].append(m["if"])
    return True


def _passive(m: re.Match[str], ctx: _Ctx) -> bool:
    if m["if"] == "default":
        ctx.current["passive_interface_default"] = True
    else:
        ctx.current["passive_interfaces"].append(m["if"])
    return True


def _setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は道すじを集合に足す。子を持つ行は解釈済みにだけする(中の行は呼び出し側が読む)。"""
    if _is_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.target, _SEPARATOR.join(ctx.path))
    return True


def _v3_process(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ見出しだけ読む。同じ見出しは同じ項目にまとめる。"""
    if not ctx.has_children or _is_secret(m.string):
        return False
    ctx.target = ctx.v3.setdefault(m.string, {"settings": []})["settings"]
    return True


_SECRET_NOTE = (
    "道すじのどの段かの語が password・secret・key・message-digest-key・authentication-key か、"
    "語の中に鍵が埋まった(key, や password= などの)行は、その行も子孫も未解釈"
)

# 並び＝優先順位(現在の if / elif の順)。no_passive は passive より前に置く。option は今の4つの後ろに置く。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "ospf.process", "top", r"router ospf (?P<id>\d+)(?: vrf (?P<vrf>\S+))?", "ospf[id]", "router ospf 1",
        _process, note="手続き。同じ process ID が二度目に現れたら読まない。`vrf <名前>` が書いてあれば vrf に写す",
    ),
    Pattern(
        "ospf.router_id", "router ospf", r"router-id (?P<addr>\S+)", "router_id", "router-id 192.0.2.1",
        _router_id, note="IPv4 として読めない値は未解釈にする",
    ),
    Pattern(
        "ospf.network", "router ospf", r"network (?P<addr>\S+) (?P<wildcard>\S+) area (?P<area>\S+)",
        "networks", "network 192.0.2.0 0.0.0.255 area 0", _network,
        note="area は数値指定(1000)も IPv4 の値に正規化する。IPv4 として読めない値は未解釈にする",
    ),
    Pattern(
        "ospf.no_passive_interface", "router ospf", r"no passive-interface (?P<if>\S+)",
        "non_passive_interfaces", "no passive-interface Vlan10", _no_passive,
        note="`no passive-interface default` は読まない(未解釈)。passive-interface より前に試す",
    ),
    Pattern(
        "ospf.passive_interface", "router ospf", r"passive-interface (?P<if>\S+)",
        "passive_interfaces / passive_interface_default", "passive-interface Loopback0", _passive,
        note="`passive-interface default` は passive_interface_default に、それ以外は passive_interfaces に写す",
    ),
    Pattern(
        "ospf.option", "router ospf", rf"(?!(?:{'|'.join(_OWNED)})(?: |$)).+", "ospf[id].options",
        "max-lsa 12000", _setting,
        note=(
            "手続き。ほかの書式に当たらない子を持たない行の原文を options の集合に足す(同じ文字列は重ねず、文字列の昇順)。"
            "子を持つ行は文字列にしないが解釈済みにし、中の行を ospf.block.option で読む。"
            "今の項目と同じ設定を書く行(" + "・".join(_OWNED) + " で始まる行)は入れず未解釈"
            "(Arista の `/` の形の network も)。" + _SECRET_NOTE
        ),
    ),
    Pattern(
        "ospf.block.option", _BLOCK_SCOPE, r".+", "ospf[id].options", "log-adjacency-changes", _setting,
        note=(
            "手続き。子を持つ行の中の子を持たない行を、見出しの次の段からの道すじを ` > ` でつないだ文字列にして"
            "options に足す(見出しは含めない。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            + _SECRET_NOTE
        ),
    ),
    Pattern(
        "ospfv3.process", "top", r"router ospfv3(?: \d+)?(?: vrf \S+)?|ipv6 router ospf \d+(?: vrf \S+)?",
        "ospfv3[見出しの原文]", "router ospfv3", _v3_process,
        note=(
            "手続き。子を持つ見出しだけ読み、見出しの原文を鍵にした項目を作る。同じ見出しが2回あれば同じ項目にまとめる。"
            "子の無い見出しと、語のどれかが password・secret・key・message-digest-key・authentication-key か"
            "語の中に鍵が埋まった見出しは、見出しも中も未解釈"
        ),
    ),
    Pattern(
        "ospfv3.setting", _V3_SCOPE, r".+", "ospfv3[見出しの原文].settings", "router-id 192.0.2.1", _setting,
        note=(
            "手続き。中の子を持たない行を、見出しの次の段からの道すじを ` > ` でつないだ文字列にして settings に足す"
            "(見出しは含めない。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            + _SECRET_NOTE
        ),
    ),
)
_OSPF_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.category == "ospf")
_V3_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.category == "ospfv3")


def _read_block(
    patterns: tuple[Pattern, ...], scope: str, node: HConfigChild, prefix: list[str], ctx: _Ctx,
    interpreted: InterpretedLines,
) -> None:
    """ブロックの中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(patterns, scope, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(patterns, scope, child, ctx.path, ctx, interpreted)


def extract_ospf(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Ospf]:
    """`router ospf` 行から、process ID の文字列をキーにした Ospf の辞書を返す。"""
    ctx = _Ctx({})
    for block in tree.children:
        heading = apply_first(_OSPF_PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)
        for line in block.children:
            ctx.path = [line.text]
            ctx.has_children = bool(line.children)
            try:
                pattern = apply_first(_OSPF_PATTERNS, "router ospf", line.text, ctx)
            except (AddressValueError, ValueError):
                pattern = None
            if pattern is None:
                continue
            interpreted.mark(line, pattern.id)
            if pattern.id == "ospf.option" and line.children:
                _read_block(_OSPF_PATTERNS, _BLOCK_SCOPE, line, ctx.path, ctx, interpreted)
    return ctx.processes


def extract_ospfv3(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Ospfv3]:
    """OSPFv3 の見出しから、見出しの原文をキーにした Ospfv3 の辞書を返す。"""
    ctx = _Ctx({})
    for block in tree.children:
        ctx.has_children = bool(block.children)
        heading = apply_first(_V3_PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)
        _read_block(_V3_PATTERNS, _V3_SCOPE, block, [], ctx, interpreted)
    return ctx.v3
