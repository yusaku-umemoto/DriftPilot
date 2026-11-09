"""VTEP のインターフェースの中の VXLAN の行を IR の vxlan に写す extractor(ADR-0053、REQ-64)。

対象は、最上位の `interface <名前>` のうち、名前が `Vxlan` か `nve` で始まるもの(大文字小文字は区別する)。
鍵は `interface ` の後ろ全体。中に VXLAN の行が1つも無くても項目を作る。見出しは表に無く、interfaces が
解釈済みにする(見出しの書式を表に置くと interfaces.interface と重なるので、文字列の頭で見つける)。
中の行のうち、interfaces がすでに読んだ行(`description`・`shutdown` など)とその子は読まない。
interfaces が読めなかったインターフェースの設定の行(`mtu abc`・`auto qos` など)と、項目に写す設定の no 付きの形
(`no source-interface …` など)は、options に入れず未解釈に残す(REQ-64 の条件4)。
Arista の `vxlan ` で始まる行は、`vxlan ` を外した残りを NX-OS の行と同じ表に当てる。
NX-OS の `vlan <n>` の中の `vn-segment <n>` は読まない(決定2。vlans の vn_segment が読む。ADR-0058、REQ-69)。範囲で書いた対応(`vxlan vlan 10-20 vni …`)は
展開せず未解釈にする(決定3)。語のどれかが password・secret・key の行と、語の中に鍵が埋まった行は、
options と members に入れない(device_settings と同じ判定)。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Vxlan, new_vxlan
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_HEADINGS = ("interface Vxlan", "interface nve")
_SCOPE = "vxlan interface"
_MEMBER_SCOPE = "vxlan member"


@dataclass
class _Ctx:
    current: Vxlan = field(default_factory=new_vxlan)
    has_children: bool = False
    # `member vni` の行を読んだ後の、その番号の値の集合
    member: list[str] = field(default_factory=list)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _source_interface(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.current["source_interface"] = m["name"]
    return True


def _udp_port(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.current["udp_port"] = int(m["port"])
    return True


def _vlan_vni(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.current["vlan_vnis"][str(int(m["vlan"]))] = int(m["vni"])
    return True


def _vrf_vni(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.current["vrf_vnis"][m["vrf"]] = int(m["vni"])
    return True


def _member(m: re.Match[str], ctx: _Ctx) -> bool:
    """子があってもなくても読む。同じ番号は同じ項目にまとめる。"""
    ctx.member = ctx.current["members"].setdefault(str(int(m["vni"])), [])
    if m["vrf"]:
        _add(ctx.member, "associate-vrf")
    return True


def _member_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.member, m.string)
    return True


# interfaces の表(インターフェースの中の行)の書式が取りうる頭の語(`no ` は外す。選択肢の書き出しをすべて含む)。
# これで始まる行はインターフェースの設定なので、interfaces が読めなかった形(`mtu abc`・`auto qos`・
# `no flow tracker FT1` など)も options に入れない。表の書式からすべての書き出しを数えた集合と一致することを
# tests/test_vxlan.py が確かめる(interfaces の表を変えたら、ここも同じ変更で直す)
_INTERFACE_WORDS = frozenset(
    {
        "access-session", "arp", "authentication", "auto", "autonomic", "autostate", "bandwidth", "bfd", "bgp-policy", "carrier-delay", "cdp",
        "channel-group",
        "clns", "comment", "counter", "cts", "description", "device-tracking", "dhcp", "dot1x", "duplex", "encapsulation", "ethernet", "evpn", "flow",
        "flowcontrol",
        "hold-queue", "ip", "ipv6", "isis", "keepalive", "l2", "lacp", "link", "link-debounce", "lldp", "load-interval", "mac", "mac-address",
        "mab", "macsec", "media-type", "medium", "mka", "mlag", "mls", "mop", "mpls", "mtu", "mvrp", "negotiate", "negotiation",
        "node-segment", "pim", "poe",
        "port-channel", "power", "priority-queue", "ptp", "qos", "queue-set", "service-policy", "service-profile", "sflow", "shutdown",
        "small-frame", "snmp", "spanning-tree", "speed", "srr-queue", "stackwise-virtual", "standby", "storm-control", "switchport", "trust",
        "tunnel", "tx-queue-limit",
        "tx-ring-limit", "udld", "vrf", "vrrp", "wrr-queue",
        # Cisco の WAN と NX-OS の語(ADR-0136 決定2、REQ-182)
        "channel-protocol", "clock", "crypto", "dialer", "dialer-group", "fabric", "fair-queue", "glbp", "ppp", "pppoe",
        "pppoe-client", "pulse-time", "vpc", "zone-member",
        # NX-OS の hsrp(ADR-0136 決定3、REQ-183)
        "hsrp",
    }
)
# 項目に写す設定の頭の語(`vxlan ` を外した後)。`no ` の付いた形は options に入れない
_ITEM_WORDS = frozenset({"source-interface", "udp-port", "vlan", "vrf", "member"})


def _is_unread_form(line: str) -> bool:
    """今の項目と同じ設定の、今は読まない形(options に入れない)なら True。

    インターフェースの設定の行、項目に写す設定の no 付きの形、上の書式と同じ設定で形が合わない行。
    """
    words = line.split()
    negated = words[0] == "no" and len(words) > 1
    if negated:
        words = words[1:]
    if words[0] in _INTERFACE_WORDS:
        return True
    if words[0] == "vxlan" and len(words) > 1:
        words = words[1:]
    if negated:
        return words[0] in _ITEM_WORDS
    return (
        words[0] in ("source-interface", "udp-port")
        or words[:2] == ["member", "vni"]
        or (words[0] in ("vlan", "vrf") and len(words) >= 3 and words[2] == "vni")
    )


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string) or _is_unread_form(m.string):
        return False
    _add(ctx.current["options"], m["rest"])
    return True


_NOTE_LAST = "2回あれば後の行。子を持つ行は未解釈。Arista の `vxlan ` は外して当てる"

# 並び＝優先順位。vxlan.option は残りすべてに当たるので、vxlan interface の最後
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vxlan.source_interface", _SCOPE, r"(?:vxlan )?source-interface (?P<name>\S+)", "vxlan[名前].source_interface",
        "vxlan source-interface Loopback1", _source_interface,
        note=f"手続き。名前(1語)を source_interface に。{_NOTE_LAST}",
    ),
    Pattern(
        "vxlan.udp_port", _SCOPE, r"(?:vxlan )?udp-port (?P<port>\d+)", "vxlan[名前].udp_port",
        "vxlan udp-port 4789", _udp_port,
        note=f"手続き。数を udp_port に。{_NOTE_LAST}",
    ),
    Pattern(
        "vxlan.vlan_vni", _SCOPE, r"(?:vxlan )?vlan (?P<vlan>\d+) vni (?P<vni>\d+)", "vxlan[名前].vlan_vnis",
        "vxlan vlan 10 vni 10010", _vlan_vni,
        note=(
            f"手続き。VLAN の番号(str(int(...)))を鍵に VNI を vlan_vnis に。{_NOTE_LAST}。"
            "範囲(10-20 など)や数でない値の形は展開せず未解釈(決定3。vxlan.option でも入れない)"
        ),
    ),
    Pattern(
        "vxlan.vrf_vni", _SCOPE, r"(?:vxlan )?vrf (?P<vrf>\S+) vni (?P<vni>\d+)", "vxlan[名前].vrf_vnis",
        "vxlan vrf RED vni 50001", _vrf_vni,
        note=(
            f"手続き。VRF の名前を鍵に VNI を vrf_vnis に。{_NOTE_LAST}。"
            "VNI が数でない形は未解釈(vxlan.option でも入れない)"
        ),
    ),
    Pattern(
        "vxlan.member", _SCOPE, r"(?:vxlan )?member vni (?P<vni>\d+)(?P<vrf> associate-vrf)?",
        "vxlan[名前].members[番号]", "member vni 10010", _member,
        note=(
            "手続き。NX-OS の member vni の番号(str(int(...)))の項目を作る(同じ番号は同じ項目にまとめる)。"
            "associate-vrf があればその文字列を入れる。子があってもなくても読み、子は vxlan.member.setting で読む。"
            "形の合わない member vni の行(範囲・後ろにほかの語)は、子が無くても未解釈"
        ),
    ),
    Pattern(
        "vxlan.member.setting", _MEMBER_SCOPE, r".+", "vxlan[名前].members[番号]", "suppress-arp", _member_setting,
        note=(
            "手続き。member vni の中の子を持たない行の原文を、その番号の集合に足す(重ねない。昇順)。"
            "子を持つ行と、語のどれかが password・secret・key か語の中に鍵が埋まった行は、その行も子孫も未解釈"
        ),
    ),
    Pattern(
        "vxlan.option", _SCOPE, r"(?:vxlan )?(?P<rest>.+)", "vxlan[名前].options",
        "host-reachability protocol bgp", _option,
        note=(
            "手続き。ほかの子を持たない行を options に足す(Arista は `vxlan ` を外した残り、NX-OS は原文。"
            "重ねない。昇順)。子を持つ行、語のどれかが password・secret・key か語の中に鍵が埋まった行、"
            "上の書式と同じ設定の形が合わない行(source-interface・udp-port で始まる、member vni で始まる、"
            "vlan・vrf で始まり3語目が vni)、interfaces の表の書式が取りうる頭の語(選択肢の書き出しをすべて含む)で"
            "始まるインターフェースの設定の行(`mtu abc`・`no mtu`・`auto qos`・`dhcp relay` など。no は外して見る)、項目に写す設定の no 付きの形(`vxlan ` を外した後が "
            "source-interface・udp-port・vlan・vrf・member で始まる)は未解釈"
        ),
    ),
)


def _read_member(node: HConfigChild, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """member vni の中の行を読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, _MEMBER_SCOPE, child.text, ctx)
        if pattern is not None:
            interpreted.mark(child, pattern.id)


def extract_vxlan(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Vxlan]:
    """VTEP のインターフェースから、インターフェースの名前をキーにした Vxlan の辞書を返す。interfaces の後に呼ぶ。"""
    items: dict[str, Vxlan] = {}
    ctx = _Ctx()
    for block in tree.children:
        if not block.text.startswith(_HEADINGS):
            continue
        ctx.current = items.setdefault(block.text.removeprefix("interface ").strip(), new_vxlan())
        for line in block.children:
            if interpreted.is_claimed(line):
                continue
            ctx.has_children = bool(line.children)
            pattern = apply_first(PATTERNS, _SCOPE, line.text, ctx)
            if pattern is None:
                continue
            interpreted.mark(line, pattern.id)
            if pattern.id == "vxlan.member":
                _read_member(line, ctx, interpreted)
    return items
