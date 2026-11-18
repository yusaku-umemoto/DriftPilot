"""interface ブロックを IR の interfaces に写す extractor。

Config View は使わず、ツリーの子行を表(PATTERNS)の書式に当てはめて読む(ADR-0009, ADR-0011)。
書式に合って IR に写せた行だけを解釈済みにし、合わない行は unparsed に残す。
"""

import re
from bisect import insort
from collections.abc import Callable
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Interface, IPv6Address, IPv6Interface

from hier_config import HConfig, HConfigChild
from hier_config.platforms.functions import expand_range

from driftpilot.config_parser.aos_switch_ports import AosPorts, expand_ports, expand_vlans, parse_vlan_number
from driftpilot.config_parser.device_settings import has_mask_word, has_secret
from driftpilot.config_parser.huawei import vlan_list
from driftpilot.config_parser.ir import Interface, InterfaceType, Vrrp, new_interface
from driftpilot.config_parser.isis import is_key_form
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 名前の先頭で決まる type(大文字小文字は区別する。IOS・Arista・NX-OS の running-config の表記を並べる。ADR-0043 決定5)
_TYPE_BY_NAME_PREFIX: tuple[tuple[str, InterfaceType], ...] = (
    ("Vlan", "svi"),
    ("Loopback", "loopback"),
    ("Port-channel", "port_channel"),
    ("Port-Channel", "port_channel"),
    ("port-channel", "port_channel"),
    ("loopback", "loopback"),
)
# Huawei VRP と見立てた config だけに足す名前の先頭の判定(大文字小文字は区別する。`Vlanif` は上の `Vlan` で svi。
# ADR-0231 決定 3、REQ-336)。見立てない config の `LoopBack…`・`Eth-Trunk…` の type は今のまま
_HUAWEI_TYPE_BY_NAME_PREFIX: tuple[tuple[str, InterfaceType], ...] = (
    ("LoopBack", "loopback"),
    ("Eth-Trunk", "port_channel"),
    ("Vbdif", "svi"),
)


@dataclass
class _Ctx:
    """1つの interface ブロックを読んでいる間の状態。"""

    name: str = ""
    interface: Interface | None = None
    no_switchport: bool = False
    # 読んでいる子行が、さらに子の行を持つか(interfaces.qos は子を持たない行だけ読む)
    has_children: bool = False
    # 子を持つ `mpls …`・`ptp …`・`evpn …` の行を読んだら、その中の行を足す項目の名前(ADR-0108、REQ-89)
    block_key: str | None = None
    # 中の行を読んでいる間の状態。見出しからその行までの行の文字列
    path: list[str] = field(default_factory=list)
    # 子を持つ `vrrp <VRID> address-family ipv4|ipv6` を読んだら、そのグループと種類(ADR-0125、REQ-170)
    vrrp_group: Vrrp | None = None
    vrrp_family: str | None = None
    # 子を持つ `cts manual` を読んだら True(中の行を読む。ADR-0126 決定3、REQ-171)
    cts_manual: bool = False
    # 子を持たない `comment` を読んだら True。同じ段の次の行から `EOF` の行までを本文として読む(ADR-0133 決定2、REQ-178)
    comment: bool = False
    # 子を持つ NX-OS の `hsrp <数> [<残り>]` を読んだら、中の行に付ける頭(`<数>` か `<数> <残り>`。ADR-0136 決定3、REQ-183)
    hsrp_prefix: str | None = None
    # アライドテレシス AW+ と見立てた config か(ADR-0206 決定1、REQ-289)。トランクの `add`・`remove` の読み方が変わる
    awplus: bool = False
    # このインターフェースで前に `switchport trunk allowed vlan all` を読んだか(AW+ の `add` の判定に使う)
    allowed_all: bool = False
    # AOS-CX の `routing` の行を読んだか(`no routing` で戻る。型の判定で `no switchport` と同じ入力になる。ADR-0210 決定3)
    routing: bool = False
    # Cisco ASA と見立てた config か(ADR-0214 決定 5、REQ-304)。nameif などを読み、type の決まりが変わる
    asa: bool = False
    # 塊の中に `switchport` で始まる行(`no switchport` を含む)があったか。読めた行か読めない行かは問わない(ASA の type の判定)
    switchport_line: bool = False
    # HPE Aruba AOS-Switch と見立てた config か(ADR-0228 決定 3、REQ-332)。`name`・`disable`・`speed-duplex` などと、
    # `vlan <数>` の塊の中のアドレスを読み、`Trk<数>` の type を決める
    aos_switch: bool = False
    # AOS-Switch のポートの VLAN の所属と trunk を集める入れ物(見立てたときだけ。ADR-0228 の追記、REQ-334)。
    # order は読んでいる行の並び順の鍵(最上位の塊の順, 塊の中の子の順)、vlan は読んでいる `vlan <数>` の塊の数
    aos_ports: AosPorts | None = None
    order: tuple[int, int] = (0, 0)
    vlan: int | None = None
    # Huawei VRP と見立てた config か(ADR-0231 決定 3、REQ-336)。`undo shutdown`・`undo portswitch`・`portswitch`・
    # `ip address … sub` を読み、`LoopBack`・`Eth-Trunk`・`Vbdif` の type を決める。
    # さらに `port link-type`・`port default vlan`・`port trunk allow-pass vlan`・`port trunk pvid vlan`・`eth-trunk` を
    # switchport・channel_group に写す(ADR-0231 決定 3(2)、REQ-337)。
    # `port trunk allow-pass vlan all` は読まない(未解釈。REQ-338。VRP の trunk は行が無ければ VLAN 1 だけを通すので、
    # `()`〈行が無い〉に写すと `all` の有る無しが差分に出なくなる)
    huawei: bool = False


def _open(m: re.Match[str], ctx: _Ctx) -> bool:
    _start(ctx, m["name"].strip())
    return True


def _start(ctx: _Ctx, name: str) -> None:
    """名前のインターフェースを既定値で作って ctx に持たせる(`interface <名前>` の塊と、AOS-Switch の `vlan <数>` の塊が使う)。"""
    ctx.name = name
    ctx.interface = new_interface(ctx.name)
    ctx.interface["secondary_addresses"] = []
    ctx.interface["ipv6_addresses"] = []
    ctx.interface["virtual_addresses"] = []
    ctx.interface["spanning_tree"] = []
    ctx.interface["qos"] = []
    ctx.interface["access_groups"] = []
    ctx.interface["dot1x"] = []
    ctx.interface["hsrp"] = []
    ctx.interface["vrrp"] = []
    ctx.interface["port_security"] = []
    ctx.interface["tunnel"] = []
    ctx.interface["dhcp_relay"] = []
    ctx.interface["ip_options"] = []
    ctx.interface["port_options"] = []
    ctx.interface["poe_options"] = []
    ctx.interface["traffic_monitoring"] = []
    ctx.interface["multicast"] = []
    ctx.interface["ospf"] = []
    ctx.interface["bfd"] = []
    ctx.interface["isis"] = []
    ctx.interface["mpls"] = []
    ctx.interface["ptp"] = []
    ctx.interface["evpn"] = []
    ctx.no_switchport = False


def _no_switchport(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.no_switchport = True
    return True


def _switchport(m: re.Match[str], ctx: _Ctx) -> bool:
    return True


def _routing(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-CX の `routing`(L3 のポート)。型の判定で `no switchport` と同じ入力にする。子を持つ行は読まない(ADR-0210 決定3)。"""
    if ctx.has_children:
        return False
    ctx.routing = True
    return True


def _no_routing(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-CX の `no routing`(L2 のポート)。IR の値は変えず、前の `routing` を打ち消す。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    ctx.routing = False
    return True


def _vlan_access(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-CX の `vlan access <数>`。access_vlan を数にし、mode を access にする。子を持つ行は読まない(ADR-0210 決定4)。"""
    if ctx.has_children:
        return False
    switchport = ctx.interface["switchport"]
    switchport["access_vlan"] = int(m["id"])
    switchport["mode"] = "access"
    return True


def _vlan_trunk_native(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-CX の `vlan trunk native <数> [tag]`。native_vlan を数にし、tag の有無を native_vlan_tagged に写す(ADR-0210 決定5)。"""
    if ctx.has_children:
        return False
    switchport = ctx.interface["switchport"]
    switchport["native_vlan"] = int(m["id"])
    switchport["native_vlan_none"] = False
    switchport["native_vlan_tagged"] = m["tag"] is not None
    switchport["mode"] = "trunk"
    return True


def _vlan_trunk_allowed(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-CX の `vlan trunk allowed <一覧>`。今の一覧との和集合にする(ADR-0210 決定6)。

    今の値が `()` で、前に `all` の行を読んでいれば未解釈。展開できない一覧も未解釈。判定は値を変える前に済ませる。
    """
    if ctx.has_children:
        return False
    switchport = ctx.interface["switchport"]
    current = switchport["trunk_allowed_vlan"]
    if not current and ctx.allowed_all:
        return False
    try:
        added = expand_range(m["range"])
    except ValueError:
        return False
    switchport["trunk_allowed_vlan"] = tuple(sorted(set(current) | set(added)))
    switchport["trunk_allowed_none"] = False
    switchport["mode"] = "trunk"
    return True


def _vlan_trunk_allowed_all(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-CX の `vlan trunk allowed all`。`()` にし、前に `all` を読んだ印を付ける(ADR-0210 決定6)。"""
    if ctx.has_children:
        return False
    switchport = ctx.interface["switchport"]
    switchport["trunk_allowed_vlan"] = ()
    switchport["trunk_allowed_none"] = False
    switchport["mode"] = "trunk"
    ctx.allowed_all = True
    return True


def _channel_group_lag(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-CX の `lag <数>`。channel_group を `{id, mode: lag}` にする。子を持つ行は読まない(ADR-0210 決定7)。"""
    if ctx.has_children:
        return False
    ctx.interface["channel_group"] = {"id": int(m["id"]), "mode": "lag"}
    return True


def _shutdown(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["shutdown"] = True
    return True


def _no_shutdown(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["shutdown"] = False
    return True


def _no_ip_address(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["ip_address"] = None
    return True


def _description(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["description"] = m["text"]
    return True


def _switchport_mode(m: re.Match[str], ctx: _Ctx) -> bool:
    """方式の語(空白は1つ)を switchport.mode に写す(ADR-0126 決定1、REQ-171)。"""
    ctx.interface["switchport"]["mode"] = " ".join(m["mode"].split())
    return True


def _switchport_mode_trunk_phone(m: re.Match[str], ctx: _Ctx) -> bool:
    """mode を trunk にし、印 `mode trunk phone` を switchport.phone に足す(ADR-0060、REQ-71)。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    ctx.interface["switchport"]["mode"] = "trunk"
    _add(ctx.interface["switchport"]["phone"], "mode trunk phone")
    return True


def _access_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["switchport"]["access_vlan"] = int(m["id"])
    return True


def _native_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["switchport"]["native_vlan"] = int(m["id"])
    ctx.interface["switchport"]["native_vlan_none"] = False
    ctx.interface["switchport"]["native_vlan_tagged"] = False
    return True


def _native_vlan_none(m: re.Match[str], ctx: _Ctx) -> bool:
    """`none` は `native_vlan` を None にして `native_vlan_none` を True にする(ADR-0206 決定4)。"""
    ctx.interface["switchport"]["native_vlan"] = None
    ctx.interface["switchport"]["native_vlan_none"] = True
    ctx.interface["switchport"]["native_vlan_tagged"] = False
    return True


def _allowed_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        ctx.interface["switchport"]["trunk_allowed_vlan"] = tuple(expand_range(m["range"]))
    except ValueError:
        return False
    ctx.interface["switchport"]["trunk_allowed_none"] = False
    return True


def _allowed_vlan_all(m: re.Match[str], ctx: _Ctx) -> bool:
    """`all` は行が無いときと同じ `()` にする(すべて通す。ADR-0111 決定1)。`none` の印は False に戻す。"""
    ctx.interface["switchport"]["trunk_allowed_vlan"] = ()
    ctx.interface["switchport"]["trunk_allowed_none"] = False
    ctx.allowed_all = True
    return True


def _allowed_vlan_none(m: re.Match[str], ctx: _Ctx) -> bool:
    """`none` は `()` のまま `trunk_allowed_none` を True にする(1つも通さない。ADR-0132 決定1)。"""
    ctx.interface["switchport"]["trunk_allowed_vlan"] = ()
    ctx.interface["switchport"]["trunk_allowed_none"] = True
    return True


def _allowed_vlan_add(m: re.Match[str], ctx: _Ctx) -> bool:
    """前に読んだ数の一覧に足す(和集合・昇順。ADR-0131 決定1)。

    今の値が `()`(行が無い・`all`・展開できなかった)なら、足す先の意味が決まらないので未解釈にする。
    ただし AW+ と見立てた config では、`none` でも前に `all` の行でもない `()` を空の一覧とみなして足す
    (ADR-0206 決定3、REQ-289)。
    """
    switchport = ctx.interface["switchport"]
    current = switchport["trunk_allowed_vlan"]
    if not current and not (ctx.awplus and not switchport["trunk_allowed_none"] and not ctx.allowed_all):
        return False
    try:
        added = expand_range(m["range"])
    except ValueError:
        return False
    switchport["trunk_allowed_vlan"] = tuple(sorted(set(current) | set(added)))
    return True


def _allowed_vlan_remove(m: re.Match[str], ctx: _Ctx) -> bool:
    """AW+ と見立てた config で、今の値が空でない一覧なら、そこから引く(昇順。全部引けば `()`。ADR-0206 決定3)。

    AW+ でない・今の値が `()`(行が無い・`all`・`none`)・展開できない行は未解釈にする。
    """
    switchport = ctx.interface["switchport"]
    current = switchport["trunk_allowed_vlan"]
    if not ctx.awplus or not current:
        return False
    try:
        removed = expand_range(m["range"])
    except ValueError:
        return False
    switchport["trunk_allowed_vlan"] = tuple(sorted(set(current) - set(removed)))
    return True


def _ip_address(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        ctx.interface["ip_address"] = IPv4Interface(f"{m['addr']}/{m['mask']}")
    except ValueError:
        return False
    return True


def _ip_address_secondary(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        address = IPv4Interface(f"{m['addr']}/{m['mask']}")
    except ValueError:
        return False
    ctx.interface["secondary_addresses"].append(address)
    return True


def _huawei_undo_shutdown(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `undo shutdown`。shutdown を偽にする。見立てていない config と子を持つ行は読まない(REQ-336)。"""
    if not ctx.huawei or ctx.has_children:
        return False
    ctx.interface["shutdown"] = False
    return True


def _huawei_undo_portswitch(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `undo portswitch`(L3 のポート)。type の判定で `no switchport` と同じ入力にする(REQ-336)。"""
    if not ctx.huawei or ctx.has_children:
        return False
    ctx.no_switchport = True
    return True


def _huawei_portswitch(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `portswitch`(L2 のポート)。`switchport` と同じく IR の値は変えない(REQ-336)。"""
    return ctx.huawei and not ctx.has_children


def _huawei_ip_address_sub(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `ip address <A> <マスク|長さ> sub`。secondary_addresses に足す。IPv4 として読めなければ未解釈(REQ-336)。"""
    if not ctx.huawei or ctx.has_children:
        return False
    return _ip_address_secondary(m, ctx)


def _huawei_link_type(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `port link-type access|trunk|dot1q-tunnel`。switchport.mode に写す(REQ-337)。"""
    if not ctx.huawei or ctx.has_children:
        return False
    ctx.interface["switchport"]["mode"] = m["mode"]
    return True


def _huawei_vlan_id(word: str) -> int | None:
    """VLAN ID の語を 1〜4094 の整数にする。範囲の外は None(huawei.py の並びの読み方と同じ)。"""
    ids = vlan_list(word)
    return ids[0] if ids is not None and len(ids) == 1 else None


def _huawei_default_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `port default vlan <N>`。access_vlan に写す。1〜4094 の外は読まない(REQ-337)。"""
    if not ctx.huawei or ctx.has_children:
        return False
    vlan_id = _huawei_vlan_id(m["id"])
    if vlan_id is None:
        return False
    ctx.interface["switchport"]["access_vlan"] = vlan_id
    return True


def _huawei_trunk_pvid(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `port trunk pvid vlan <N>`。native_vlan に写す(native_vlan_none・native_vlan_tagged は偽。REQ-337)。"""
    if not ctx.huawei or ctx.has_children:
        return False
    vlan_id = _huawei_vlan_id(m["id"])
    if vlan_id is None:
        return False
    switchport = ctx.interface["switchport"]
    switchport["native_vlan"] = vlan_id
    switchport["native_vlan_none"] = False
    switchport["native_vlan_tagged"] = False
    return True


def _huawei_trunk_allow_pass(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `port trunk allow-pass vlan <並び>`。今の一覧との和集合(昇順)にする。

    並びが読めなければ未解釈。mode は変えない(REQ-337)。`port trunk allow-pass vlan all` は読まないので(REQ-338)、
    前の `all` を気にしなくてよい。
    """
    if not ctx.huawei or ctx.has_children:
        return False
    switchport = ctx.interface["switchport"]
    current = switchport["trunk_allowed_vlan"]
    added = vlan_list(m["list"])
    if added is None:
        return False
    switchport["trunk_allowed_vlan"] = tuple(sorted(set(current) | set(added)))
    switchport["trunk_allowed_none"] = False
    return True


def _huawei_eth_trunk(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `eth-trunk <N>`。channel_group を `{id: N, mode: eth-trunk}` にする(AOS-CX の `lag` と同じ持ち方。REQ-337)。"""
    if not ctx.huawei or ctx.has_children:
        return False
    ctx.interface["channel_group"] = {"id": int(m["id"]), "mode": "eth-trunk"}
    return True


def _asa_ip_address_standby(m: re.Match[str], ctx: _Ctx) -> bool:
    """ASA の `ip address <A> <マスク> standby <B>`。ASA と見立てた config の、子を持たない行だけ読む(REQ-304)。"""
    if not ctx.asa or ctx.has_children:
        return False
    try:
        address = IPv4Interface(f"{m['addr']}/{m['mask']}")
        standby = IPv4Address(m["standby"])
    except ValueError:
        return False
    ctx.interface["ip_address"] = address
    _add(ctx.interface["ip_options"], f"ip address standby {standby}")
    return True


def _asa_port_options(m: re.Match[str], ctx: _Ctx) -> bool:
    """ASA の `nameif`・`security-level`・`management-only`・`vlan <数>`。ASA と見立てた config だけ読む(REQ-304)。"""
    return ctx.asa and _line_option("port_options")(m, ctx)


def _unquote(text: str) -> str:
    """前後の `"` を 1 組だけ外す。対になっていなければそのまま。"""
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _aos_name(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `name "<名前>"`・`name <名前>`。引用符を外して description に写す。見立てていない config と子を持つ行は読まない。"""
    if not ctx.aos_switch or ctx.has_children:
        return False
    ctx.interface["description"] = _unquote(m["text"])
    return True


def _aos_disable(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `disable`(ポートを止める)。shutdown を真にする。見立てていない config と子を持つ行は読まない。"""
    if not ctx.aos_switch or ctx.has_children:
        return False
    ctx.interface["shutdown"] = True
    return True


def _aos_enable(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `enable`。shutdown を偽にする。見立てていない config と子を持つ行は読まない。"""
    if not ctx.aos_switch or ctx.has_children:
        return False
    ctx.interface["shutdown"] = False
    return True


def _aos_speed_duplex(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `speed-duplex <値>`。値の原文を speed に写す。見立てていない config と子を持つ行は読まない。"""
    if not ctx.aos_switch or ctx.has_children:
        return False
    ctx.interface["speed"] = m["value"]
    return True


def _aos_vlan_ip_address(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `vlan <数>` の塊の `ip address <A> <マスク>`。最初の行だけを ip_address に写し、2 行目からは読まない(REQ-332)。"""
    if not ctx.aos_switch or ctx.has_children or ctx.interface["ip_address"] is not None:
        return False
    return _ip_address(m, ctx)


def _aos_vlan_address_dhcp(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `vlan <数>` の塊の `ip address dhcp-bootp`。address_mode を `dhcp` にする(今の `ip address dhcp` と同じ値)。"""
    if not ctx.aos_switch or ctx.has_children:
        return False
    ctx.interface["address_mode"] = "dhcp"
    return True


def _aos_vlan_helper_address(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `vlan <数>` の塊の `ip helper-address <A>`。今の `ip helper-address` と同じ持ち方で dhcp_relay に足す。"""
    if not ctx.aos_switch:
        return False
    return _line_option("dhcp_relay")(m, ctx)


def _aos_vlan_ipv6_address(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `vlan <数>` の塊の `ipv6 address <A>/<長さ>`。今の `ipv6 address` と同じ持ち方で ipv6_addresses に足す。"""
    if not ctx.aos_switch or ctx.has_children:
        return False
    return _ipv6_address(m, ctx)


def _aos_untagged_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `interface <ポート>` の塊の `untagged vlan <数>`(1〜4094)。ポートの untagged の VLAN として集める。

    集めた所属は extract_interfaces の最後にポートの switchport に写す(REQ-334)。見立てていない config・子を持つ行・範囲の外は読まない。
    """
    if not ctx.aos_switch or ctx.aos_ports is None or ctx.has_children:
        return False
    vlan = parse_vlan_number(m["id"])
    if vlan is None:
        return False
    ctx.aos_ports.add_untagged(ctx.order, (ctx.name,), vlan)
    return True


def _aos_tagged_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `interface <ポート>` の塊の `tagged vlan <並び>`。並びの各 VLAN をポートの tagged の集合に足す(REQ-334)。"""
    if not ctx.aos_switch or ctx.aos_ports is None or ctx.has_children:
        return False
    vlans = expand_vlans(m["ids"])
    if vlans is None:
        return False
    ctx.aos_ports.add_tagged(ctx.order, (ctx.name,), vlans)
    return True


def _aos_vlan_untagged(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `vlan <数>` の塊の `untagged <並び>`。並びの各ポートの untagged の VLAN を、塊の数にする(REQ-334)。"""
    if not ctx.aos_switch or ctx.aos_ports is None or ctx.vlan is None or ctx.has_children:
        return False
    ports = expand_ports(m["ports"])
    if ports is None:
        return False
    ctx.aos_ports.add_untagged(ctx.order, ports, ctx.vlan)
    return True


def _aos_vlan_tagged(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `vlan <数>` の塊の `tagged <並び>`。並びの各ポートの tagged の集合に、塊の数を足す(REQ-334)。"""
    if not ctx.aos_switch or ctx.aos_ports is None or ctx.vlan is None or ctx.has_children:
        return False
    ports = expand_ports(m["ports"])
    if ports is None:
        return False
    ctx.aos_ports.add_tagged(ctx.order, ports, (ctx.vlan,))
    return True


def _aos_vlan_no_member(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の `vlan <数>` の塊の `no untagged <並び>`・`no tagged <並び>`。並びが展開できれば解釈済みにするだけで、所属は変えない。

    running-config の `no untagged` は、既定の VLAN 1 から外したことを表す(所属から外すことは書かない。REQ-334)。
    """
    if not ctx.aos_switch or ctx.vlan is None or ctx.has_children:
        return False
    return expand_ports(m["ports"]) is not None


def _aos_trunk(m: re.Match[str], ctx: _Ctx) -> bool:
    """AOS-Switch の最上位の `trunk <並び> trk<数> <方式>`。並びの各ポートの束ね先を `{"id": 数, "mode": 方式}` にする(REQ-334)。"""
    if not ctx.aos_switch or ctx.aos_ports is None or ctx.has_children:
        return False
    ports = expand_ports(m["ports"])
    if ports is None:
        return False
    ctx.aos_ports.add_trunk(ctx.order, ports, (int(m["id"]), m["mode"]))
    return True


def _ip_address_prefix(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        ctx.interface["ip_address"] = IPv4Interface(f"{m['addr']}/{m['length']}")
    except ValueError:
        return False
    return True


def _ip_address_prefix_secondary(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        address = IPv4Interface(f"{m['addr']}/{m['length']}")
    except ValueError:
        return False
    ctx.interface["secondary_addresses"].append(address)
    return True


def _address_virtual(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip address virtual <A>/<n> [secondary]` を virtual_addresses に足す。IPv4 として読めなければ未解釈。"""
    try:
        text = str(IPv4Interface(f"{m['addr']}/{m['length']}"))
    except ValueError:
        return False
    _add(ctx.interface["virtual_addresses"], text + (" secondary" if m["secondary"] else ""))
    return True


def _ipv6_address_virtual(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        text = str(IPv6Interface(f"{m['addr']}/{m['length']}"))
    except ValueError:
        return False
    _add(ctx.interface["virtual_addresses"], text)
    return True


def _ipv6_address(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ipv6 address <A>/<n> [eui-64|anycast]` を ipv6_addresses に足す。IPv6 として読めなければ未解釈。"""
    try:
        text = str(IPv6Interface(f"{m['addr']}/{m['length']}"))
    except ValueError:
        return False
    _add(ctx.interface["ipv6_addresses"], text + (f" {m['kind']}" if m["kind"] else ""))
    return True


def _ipv6_address_link_local(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        text = str(IPv6Address(m["addr"]))
    except ValueError:
        return False
    _add(ctx.interface["ipv6_addresses"], text + " link-local")
    return True


def _ipv6_address_word(m: re.Match[str], ctx: _Ctx) -> bool:
    _add(ctx.interface["ipv6_addresses"], m["word"])
    return True


def _ipv6_address_name(m: re.Match[str], ctx: _Ctx) -> bool:
    """一般プレフィックスの `ipv6 address <名前> <残り>` を `<名前> <残り>`(空白は1つ)にして ipv6_addresses に足す。

    秘密の語がある行と子を持つ行は読まない(ADR-0136 決定3、REQ-183)。
    """
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.interface["ipv6_addresses"], " ".join([m["name"], *m["rest"].split()]))
    return True


def _ipv6_enable(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["ipv6_enabled"] = True
    return True


def _no_ipv6_enable(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["ipv6_enabled"] = False
    return True


def _virtual_router(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip|ipv6 virtual-router address <A>[/<n>]` のアドレスだけを ` virtual-router` 付きで virtual_addresses に足す。

    `ip` は IPv4、`ipv6` は IPv6 として読む。読めなければ(`/<n>` が範囲外も)未解釈。
    """
    ipv4 = m["family"] == "ip"
    address_type, interface_type = (IPv4Address, IPv4Interface) if ipv4 else (IPv6Address, IPv6Interface)
    try:
        if m["length"] is None:
            address = address_type(m["addr"])
        else:
            address = interface_type(f"{m['addr']}/{m['length']}").ip
    except ValueError:
        return False
    _add(ctx.interface["virtual_addresses"], f"{address} virtual-router")
    return True


def _vrf(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["vrf"] = m["name"]
    return True


def _poe(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["poe"] = m["mode"]
    return True


def _poe_rest(m: re.Match[str], ctx: _Ctx) -> bool:
    """`<方式> <残り>`(空白は1つ)を poe に写す(ADR-0111 決定2)。"""
    ctx.interface["poe"] = " ".join([m["mode"], *m["rest"].split()])
    return True


def _poe_disabled(m: re.Match[str], ctx: _Ctx) -> bool:
    """Arista の `poe disabled` を poe に写す(ADR-0115 決定1。語は原文のまま)。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    ctx.interface["poe"] = m["mode"]
    return True


def _speed(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["speed"] = m["value"]
    return True


def _duplex(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["duplex"] = m["mode"]
    return True


def _mtu(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["mtu"] = int(m["n"])
    return True


def _channel_group(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["channel_group"] = {"id": int(m["id"]), "mode": m["mode"]}
    return True


def _channel_group_auto(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["channel_group_auto"] = True
    return True


def _no_channel_group_auto(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["channel_group_auto"] = False
    return True


def _mlag(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["mlag_id"] = int(m["n"])
    return True


def _ospf_auth_key(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip ospf authentication-key [<型>] <値>` を `authentication-key` にして ospf に足す(値は持たない)。

    後ろの語が1つか2つでない行と、子を持つ行は読まない。
    """
    if ctx.has_children or len((m["rest"] or "").split()) not in (1, 2):
        return False
    _add(ctx.interface["ospf"], "authentication-key")
    return True


def _ospf_md_key(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip ospf message-digest-key <番号> <方式> [<型>] <値>` を `message-digest-key <番号> <方式>` にして ospf に足す。

    値は持たない。後ろの語が3つか4つでない行、番号が数でない行と、子を持つ行は読まない。
    """
    words = (m["rest"] or "").split()
    if ctx.has_children or len(words) not in (3, 4) or not words[0].isdigit():
        return False
    _add(ctx.interface["ospf"], f"message-digest-key {words[0]} {words[1]}")
    return True


# `ipv6 ospf` の次の語がこれらで始まる行は読まない(`key` の語が無いまま鍵が続く。ADR-0120、REQ-165)
_IPV6_OSPF_SECRET_HEADS = ("authentication", "encryption")


def _ospf(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip ospf` の後ろ(空白は1つ)を ospf に足す。`ipv6 ospf …` は行の原文(空白は1つ)を足す。

    秘密の語がある行、`ipv6 ospf authentication …`・`ipv6 ospf encryption …` と、子を持つ行は読まない。
    """
    if ctx.has_children or has_secret(m.string):
        return False
    if m["rest"] is not None:
        _add(ctx.interface["ospf"], " ".join(m["rest"].split()))
        return True
    words = m.string.split()
    rest = words[words.index("ospf") + 1:]
    if not rest or rest[0].startswith(_IPV6_OSPF_SECRET_HEADS):
        return False
    _add(ctx.interface["ospf"], " ".join(words))
    return True


def _ospf_router(m: re.Match[str], ctx: _Ctx) -> bool:
    """NX-OS の `ip router ospf <タグ> area <残り>` を `<タグ> area <残り>`(空白は1つ)にして ospf に足す。

    `ip ospf <タグ> area <残り>` と同じ値になる(ADR-0136 決定3、REQ-183)。秘密の語がある行と子を持つ行は読まない。
    """
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.interface["ospf"], " ".join([m["tag"], "area", *m["rest"].split()]))
    return True


def _encapsulation(m: re.Match[str], ctx: _Ctx) -> bool:
    """`encapsulation` の後ろ(空白は1つ)を encapsulation に写す。2回あれば後の行。

    Arista のブロックの見出し `encapsulation vlan` と、子を持つ行は読まない(ADR-0055 決定2)。
    """
    rest = m["rest"].split()
    if ctx.has_children or rest == ["vlan"]:
        return False
    ctx.interface["encapsulation"] = " ".join(rest)
    return True


def _autostate(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["autostate"] = True
    return True


def _no_autostate(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["autostate"] = False
    return True


def _isis_auth_key(m: re.Match[str], ctx: _Ctx) -> bool:
    """`isis authentication key [<型>] <値> [level-1|level-2]` を `authentication key` にして isis に足す。

    値も型も段も持たない(ADR-0056、REQ-67)。形の合わない行(後ろの無い行も)と、子を持つ行は読まない。
    """
    if ctx.has_children or not is_key_form(m["rest"]):
        return False
    _add(ctx.interface["isis"], "authentication key")
    return True


def _isis(m: re.Match[str], ctx: _Ctx) -> bool:
    """`isis` の後ろ(no 付きは `no <残り>`。空白は1つ)を isis に足す。秘密の語がある行と子を持つ行は読まない。"""
    if ctx.has_children or has_secret(m.string):
        return False
    rest = " ".join(m["rest"].split())
    _add(ctx.interface["isis"], f"no {rest}" if m["no"] else rest)
    return True


def _isis_router(m: re.Match[str], ctx: _Ctx) -> bool:
    """NX-OS の `ip router isis [<タグ>]` を `router`・`router <タグ>` にして isis に足す(ADR-0136 決定3、REQ-183)。

    秘密の語がある行と子を持つ行は読まない。
    """
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.interface["isis"], f"router {m['tag']}" if m["tag"] else "router")
    return True


def _bfd(m: re.Match[str], ctx: _Ctx) -> bool:
    """`bfd` の後ろ(no 付きは `no <残り>`。空白は1つ)を bfd に足す。秘密の語がある行と子を持つ行は読まない。"""
    if ctx.has_children or has_secret(m.string):
        return False
    rest = " ".join(m["rest"].split())
    _add(ctx.interface["bfd"], f"no {rest}" if m["no"] else rest)
    return True


def _add_spanning_tree(ctx: _Ctx, text: str) -> bool:
    """spanning_tree に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    values = ctx.interface["spanning_tree"]
    if text not in values:
        insort(values, text)
    return True


def _spanning_tree(m: re.Match[str], ctx: _Ctx) -> bool:
    return _add_spanning_tree(ctx, " ".join(m["rest"].split()))


def _no_spanning_tree(m: re.Match[str], ctx: _Ctx) -> bool:
    return _add_spanning_tree(ctx, "no " + " ".join(m["rest"].split()))


def _qos(m: re.Match[str], ctx: _Ctx) -> bool:
    """行の原文を qos に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。子を持つ行は読まない。

    `service-profile …` は、秘密の語がある行も読まない(ADR-0115 決定5)。
    """
    if ctx.has_children:
        return False
    if m["head"] == "service-profile" and has_secret(m.string):
        return False
    values = ctx.interface["qos"]
    if m.string not in values:
        insort(values, m.string)
    return True


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _has_word(text: str, words: frozenset[str]) -> bool:
    """語(空白で区切る。大文字小文字は区別しない)のどれかが words にあるか。"""
    return any(word.lower() in words for word in text.split())


def _access_group(m: re.Match[str], ctx: _Ctx) -> bool:
    """行の原文(空白は1つ)を access_groups に足す。子を持つ行は読まない。

    NEC IX の `ip filter`・`ipv6 filter` の形は、伏せる表の秘密語がある行(ACL の名前が snmp など)も読まない
    (ADR-0209 決定6、REQ-292)。
    """
    if ctx.has_children or (m["filter"] is not None and has_mask_word(m.string)):
        return False
    _add(ctx.interface["access_groups"], " ".join(m.string.split()))
    return True


def _negotiation(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["negotiation"] = True
    return True


def _no_negotiation(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["negotiation"] = False
    return True


def _bandwidth(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["bandwidth"] = int(m["n"])
    return True


def _mac_address(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["mac_address"] = m["mac"]
    return True


def _media_type(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["media_type"] = m["kind"]
    return True


def _voice_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.interface["switchport"]["voice_vlan"] = int(m["id"])
    return True


def _trunk_group(m: re.Match[str], ctx: _Ctx) -> bool:
    """名前を switchport.trunk_groups に足す。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    _add(ctx.interface["switchport"]["trunk_groups"], m["name"])
    return True


def _switchport_phone(m: re.Match[str], ctx: _Ctx) -> bool:
    """`switchport phone` の後ろ(空白は1つ)を switchport.phone に足す。秘密の語がある行と子を持つ行は読まない。"""
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.interface["switchport"]["phone"], " ".join(m["rest"].split()))
    return True


def _dot1x(m: re.Match[str], ctx: _Ctx) -> bool:
    """`dot1x` の後ろ(空白は1つ)を dot1x に足す。`authentication …`・`mab …`・`access-session …` は行の原文(空白は1つ)を足す。

    秘密の語がある行と子を持つ行は読まない。
    """
    if ctx.has_children or has_secret(m.string):
        return False
    text = m["rest"] if m["rest"] is not None else m.string
    _add(ctx.interface["dot1x"], " ".join(text.split()))
    return True


# HSRP の行で、この語があるものは入れない(認証の鍵を IR に入れない。ADR-0068 決定2)
_HSRP_SECRET_WORDS = frozenset({"authentication"})
# ポートの守りの行で、語のどれかがこれらのものは入れない(ADR-0068 決定3。qos.py と同じ判定)
_PORT_SECURITY_SECRET_WORDS = frozenset({"password", "secret", "key"})


def _hsrp(m: re.Match[str], ctx: _Ctx) -> bool:
    """`standby` の後ろ(空白は1つ)を hsrp に足す。認証の行と子を持つ行は読まない。"""
    if ctx.has_children or _has_word(m["rest"], _HSRP_SECRET_WORDS):
        return False
    _add(ctx.interface["hsrp"], " ".join(m["rest"].split()))
    return True


_HSRP_SCOPE = "interface hsrp"


def _hsrp_readable(ctx: _Ctx, *texts: str) -> bool:
    """子を持たず、語に authentication が無く、秘密の語も無い行なら True(NX-OS の hsrp。VRRP と同じ判定)。"""
    return not (
        ctx.has_children or any(_has_word(text, _HSRP_SECRET_WORDS) for text in texts) or has_secret(*texts)
    )


def _hsrp_version(m: re.Match[str], ctx: _Ctx) -> bool:
    """NX-OS の `hsrp version <数>` を `version <数>` にして hsrp に足す(ADR-0136 決定3、REQ-183)。子を持つ行は読まない。"""
    if not _hsrp_readable(ctx, m.string):
        return False
    _add(ctx.interface["hsrp"], f"version {m['n']}")
    return True


def _hsrp_group(m: re.Match[str], ctx: _Ctx) -> bool:
    """NX-OS の `hsrp <数> [<残り>]` を読む(ADR-0136 決定3、REQ-183)。

    子を持たない `hsrp <数>` は `<数>` を hsrp に足す。子を持たず後ろに語がある行は読まない。
    子を持つ行は文字列にしないが解釈済みにし、中の行に付ける頭(`<数>` か `<数> <残り>`。空白は1つ)を覚える。
    見出しに authentication の語か秘密の語があれば、子も含めて読まない。
    """
    if _has_word(m.string, _HSRP_SECRET_WORDS) or has_secret(m.string):
        return False
    if ctx.has_children:
        ctx.hsrp_prefix = " ".join([m["group"], *(m["rest"] or "").split()])
        return True
    if m["rest"] is not None:
        return False
    _add(ctx.interface["hsrp"], m["group"])
    return True


def _hsrp_block_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ `hsrp <数>` の中の行を `<頭> <中の行>`(空白は1つ)にして hsrp に足す。

    語に authentication がある行・秘密の語がある行・子を持つ行は、子も含めて読まない。
    """
    if not _hsrp_readable(ctx, *ctx.path):
        return False
    _add(ctx.interface["hsrp"], " ".join([ctx.hsrp_prefix, *m.string.split()]))
    return True


# VRRP の行で、この語があるものは入れない(認証の鍵を IR に入れない。HSRP と同じ。ADR-0068 決定2・ADR-0125)
_VRRP_SECRET_WORDS = frozenset({"authentication"})
_VRRP_SCOPE = "interface vrrp"


def _vrrp_readable(ctx: _Ctx, *texts: str) -> bool:
    """子を持たず、語に authentication が無く、秘密の語も無い行なら True。"""
    return not (
        ctx.has_children or any(_has_word(text, _VRRP_SECRET_WORDS) for text in texts) or has_secret(*texts)
    )


def _vrrp_group(ctx: _Ctx, vrid: int) -> Vrrp:
    """VRID のグループを返す。無ければ作り、VRID の昇順の位置に入れる。"""
    groups = ctx.interface["vrrp"]
    for group in groups:
        if group["group"] == vrid:
            return group
    group: Vrrp = {"group": vrid, "priority": None, "virtual_addresses": [], "preempt": None, "options": []}
    insort(groups, group, key=lambda g: g["group"])
    return group


def _ipv4_address(words: list[str], suffixes: tuple[str, ...]) -> IPv4Address | None:
    """`<IPv4>` か `<IPv4> <suffixes のどれか>` の形なら、そのアドレスを返す。それ以外は None。"""
    if not words or len(words) > 2 or (len(words) == 2 and words[1] not in suffixes):
        return None
    try:
        return IPv4Address(words[0])
    except ValueError:
        return None


def _vrrp_address(m: re.Match[str], ctx: _Ctx) -> bool:
    """`vrrp <VRID> ip|ipv4 <IPv4> [secondary]` のアドレスを virtual_addresses の後ろに足す。

    ほかの形は行の原文(空白は1つ)を options に足す。no 付きの行・認証の行・秘密の語がある行・子を持つ行は読まない。
    """
    if m["no"] or not _vrrp_readable(ctx, m.string):
        return False
    group = _vrrp_group(ctx, int(m["vrid"]))
    address = _ipv4_address((m["rest"] or "").split(), ("secondary",))
    if address is None:
        _add(group["options"], " ".join(m.string.split()))
    else:
        group["virtual_addresses"].append(address)
    return True


def _vrrp_priority(m: re.Match[str], ctx: _Ctx) -> bool:
    """`vrrp <VRID> priority|priority-level <数>` を priority に写す(2回あれば後の行)。ほかの形は options に足す。"""
    if m["no"] or not _vrrp_readable(ctx, m.string):
        return False
    group = _vrrp_group(ctx, int(m["vrid"]))
    if m["rest"] is not None and m["rest"].isdecimal():
        group["priority"] = int(m["rest"])
    else:
        _add(group["options"], " ".join(m.string.split()))
    return True


def _vrrp_preempt(m: re.Match[str], ctx: _Ctx) -> bool:
    """`vrrp <VRID> preempt` は True、`no vrrp <VRID> preempt` は False。後ろがあれば True と行の原文を options に足す。

    no 付きで後ろがある行は読まない。
    """
    if (m["no"] and m["rest"]) or not _vrrp_readable(ctx, m.string):
        return False
    group = _vrrp_group(ctx, int(m["vrid"]))
    group["preempt"] = not m["no"]
    if m["rest"]:
        _add(group["options"], " ".join(m.string.split()))
    return True


def _vrrp_address_family(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ VRRPv3 の見出しはグループを作り、中の行を読む印を付ける。子を持たない見出しは options に足す。"""
    if not ctx.has_children:
        return _vrrp_option(m, ctx)
    ctx.vrrp_group = _vrrp_group(ctx, int(m["vrid"]))
    ctx.vrrp_family = m["family"]
    return True


def _vrrp_option(m: re.Match[str], ctx: _Ctx) -> bool:
    """行の原文(空白は1つ)を options に足す。認証の行・秘密の語がある行・子を持つ行は読まない。"""
    if not _vrrp_readable(ctx, m.string):
        return False
    _add(_vrrp_group(ctx, int(m["vrid"]))["options"], " ".join(m.string.split()))
    return True


def _add_vrrp_path(ctx: _Ctx) -> bool:
    """VRRPv3 のブロックの道すじ(見出し > 行)を、そのグループの options に足す。"""
    _add(ctx.vrrp_group["options"], _SEPARATOR.join(ctx.path))
    return True


def _vrrp_block_address(m: re.Match[str], ctx: _Ctx) -> bool:
    """ipv4 のブロックの `address <IPv4> [primary|secondary]` を virtual_addresses の後ろに足す。ほかは道すじを options に足す。"""
    if m["no"] or not _vrrp_readable(ctx, *ctx.path):
        return False
    address = _ipv4_address((m["rest"] or "").split(), ("primary", "secondary"))
    if ctx.vrrp_family != "ipv4" or address is None:
        return _add_vrrp_path(ctx)
    ctx.vrrp_group["virtual_addresses"].append(address)
    return True


def _vrrp_block_priority(m: re.Match[str], ctx: _Ctx) -> bool:
    """ipv4 のブロックの `priority <数>` を priority に写す(2回あれば後の行)。ほかは道すじを options に足す。"""
    if m["no"] or not _vrrp_readable(ctx, *ctx.path):
        return False
    if ctx.vrrp_family != "ipv4" or m["rest"] is None or not m["rest"].isdecimal():
        return _add_vrrp_path(ctx)
    ctx.vrrp_group["priority"] = int(m["rest"])
    return True


def _vrrp_block_preempt(m: re.Match[str], ctx: _Ctx) -> bool:
    """ipv4 のブロックの `preempt` は True、`no preempt` は False、`preempt <残り>` は True と道すじを options に足す。

    ipv6 のブロックは道すじを options に足す。no 付きで後ろがある行は読まない。
    """
    if (m["no"] and m["rest"]) or not _vrrp_readable(ctx, *ctx.path):
        return False
    if ctx.vrrp_family != "ipv4":
        return _add_vrrp_path(ctx)
    ctx.vrrp_group["preempt"] = not m["no"]
    if m["rest"]:
        _add_vrrp_path(ctx)
    return True


def _vrrp_block_option(m: re.Match[str], ctx: _Ctx) -> bool:
    """道すじを options に足す。認証の行・秘密の語がある行・子を持つ行は読まない。"""
    if not _vrrp_readable(ctx, *ctx.path):
        return False
    return _add_vrrp_path(ctx)


def _port_security(m: re.Match[str], ctx: _Ctx) -> bool:
    """行の原文(空白は1つ)を port_security に足す。秘密の語がある行と子を持つ行は読まない。"""
    if ctx.has_children or _has_word(m.string, _PORT_SECURITY_SECRET_WORDS):
        return False
    _add(ctx.interface["port_security"], " ".join(m.string.split()))
    return True


def _tunnel(m: re.Match[str], ctx: _Ctx) -> bool:
    """`tunnel` の後ろ(空白は1つ)を tunnel に足す。秘密の語がある行と子を持つ行は読まない。"""
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.interface["tunnel"], " ".join(m["rest"].split()))
    return True


def _address_mode_dhcp(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip address dhcp [<残り>]` を `dhcp [<残り>]`(空白は1つ)にして address_mode に写す。2回あれば後の行。"""
    ctx.interface["address_mode"] = " ".join(["dhcp", *(m["rest"] or "").split()])
    return True


def _address_mode_negotiated(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip address negotiated` を `negotiated` にして address_mode に写す。2回あれば後の行(ADR-0136 決定3、REQ-183)。"""
    ctx.interface["address_mode"] = "negotiated"
    return True


def _address_mode_unnumbered(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip address unnumbered <if>`・`ip unnumbered <if>` を `unnumbered <if>` にして address_mode に写す。2回あれば後の行。"""
    ctx.interface["address_mode"] = f"unnumbered {m['name']}"
    return True


def _leaf_only(handler: Callable[[re.Match[str], _Ctx], bool]) -> Callable[[re.Match[str], _Ctx], bool]:
    """子を持たない行だけ handler で読む手続きを返す。子を持つ行は読まず、未解釈に残す(IOS-XR の `ipv4` の行。ADR-0211 決定5)。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        return not ctx.has_children and handler(m, ctx)

    return apply


def _line_option(key: str) -> Callable[[re.Match[str], _Ctx], bool]:
    """行の原文(空白は1つ)を key の集合に足す手続きを返す。秘密の語がある行と子を持つ行は読まない(ADR-0109・ADR-0110)。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if ctx.has_children or has_secret(m.string):
            return False
        _add(ctx.interface[key], " ".join(m.string.split()))
        return True

    return apply


_SEPARATOR = " > "
_BLOCK_SCOPE = "interface block"
# AOS-Switch の `vlan <数>` の塊の中(ADR-0228 決定 3、REQ-332)
_AOS_VLAN_SCOPE = "aos-switch vlan"
# AOS-Switch の `vlan <数>` の塊の中のポートの所属の行。`aos-switch vlan` と分けるのは、所属の行を `vlan<N>` を作る手がかりに
# 数えないため(`untagged 1-12` だけの `vlan 1` から `vlan1` を作らない。ADR-0228 の追記、REQ-334)
_AOS_PORT_SCOPE = "aos-switch vlan port"
# AOS-Switch の最上位の行(`trunk …`)。`top` と分けるのは、`top` が `interface` の見出しを読む場所だから(REQ-334)
_AOS_TOP_SCOPE = "aos-switch top"


def _block_line(key: str) -> Callable[[re.Match[str], _Ctx], bool]:
    """行の原文(空白は1つ)を key の集合に足す手続きを返す(ADR-0108、REQ-89)。

    子を持つ行は文字列にしないが解釈済みにし、中の行を読む印を付ける。秘密の語がある行は、子も含めて読まない。
    """

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if has_secret(m.string):
            return False
        if ctx.has_children:
            ctx.block_key = key
        else:
            _add(ctx.interface[key], " ".join(m.string.split()))
        return True

    return apply


def _block_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は見出しからの道すじを、見出しと同じ集合に足す。子を持つ行は解釈済みにだけする。"""
    if has_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.interface[ctx.block_key], _SEPARATOR.join(ctx.path))
    return True


_CTS_SCOPE = "interface cts manual"
_CTS_MANUAL = "cts manual"


def _cts_manual(m: re.Match[str], ctx: _Ctx) -> bool:
    """`cts manual` を port_options に足す。子を持てば中の行を読む印を付ける(ADR-0126 決定3、REQ-171)。"""
    if has_secret(m.string):
        return False
    _add(ctx.interface["port_options"], _CTS_MANUAL)
    ctx.cts_manual = ctx.has_children
    return True


def _cts_manual_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """見出しからの道すじを port_options に足す。子を持つ行と秘密の語がある行は読まない。"""
    if ctx.has_children or has_secret(*ctx.path):
        return False
    _add(ctx.interface["port_options"], _SEPARATOR.join(ctx.path))
    return True


_COMMENT = "comment"
_COMMENT_END = "EOF"
_COMMENT_PATTERN_ID = "interfaces.comment"


def _comment(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない `comment` を読んだら、次の行から本文を読む印を付ける。子を持つ行は、子も含めて読まない。"""
    if ctx.has_children:
        return False
    ctx.comment = True
    return True


def _read_comment_line(line: HConfigChild, ctx: _Ctx, interpreted: InterpretedLines) -> bool:
    """`comment` の本文の1行を読む。本文が続くなら True、`EOF` の行で終われば False を返す(ADR-0133 決定2)。

    本文はどの書式にも当てず、`comment > <行>`(空白は1つ)にして port_options に足す。
    秘密の語がある行と子を持つ行は、子も含めて読まない(本文は続く)。読まない行は、後ろの extractor
    (vxlan など、インターフェースの残りの行を読むもの)にも読ませない。本文と `EOF` の行は、同じ段の同じ文字列の
    行(コメントの外の設定)と分けるため、出現で記録する。
    """
    text = " ".join(line.text.split())
    if text == _COMMENT_END:
        if line.children:
            interpreted.reserve(line)
        else:
            interpreted.mark_occurrence(line, _COMMENT_PATTERN_ID)
        return False
    if line.children or has_secret(text):
        interpreted.reserve(line)
        return True
    _add(ctx.interface["port_options"], f"{_COMMENT}{_SEPARATOR}{text}")
    interpreted.mark_occurrence(line, _COMMENT_PATTERN_ID)
    return True


def _aoscx_name_type(name: str) -> InterfaceType | None:
    """AOS-CX の名前で決まる型。`lag <数> [<語> …]` は port_channel、`vlan <数>`・`vlan<数>` は svi(ADR-0210 決定2)。

    表を通さない正規表現は使えないので、文字列の操作で見る。大文字小文字は区別する。`lagX`・`vlan10a`・`vlan abc` は None。
    """
    words = name.split()
    if len(words) >= 2 and words[0] == "lag" and words[1].isascii() and words[1].isdigit():
        return "port_channel"
    if len(words) == 2 and words[0] == "vlan" and words[1].isascii() and words[1].isdigit():
        return "svi"
    if len(words) == 1 and words[0].startswith("vlan"):
        number = words[0].removeprefix("vlan")
        if number.isascii() and number.isdigit():
            return "svi"
    return None


def _is_aos_trunk_name(name: str) -> bool:
    """AOS-Switch のトランクの名前(`Trk<数>`。大文字小文字は区別する)か。表を通さない正規表現は使えないので、文字列の操作で見る。"""
    number = name.removeprefix("Trk")
    return number != name and number.isascii() and number.isdigit()


def _derive_type(m: None, ctx: _Ctx) -> bool:
    """type を判定する。名前の先頭 → AOS-CX の名前 → AOS-Switch の `Trk<数>` → routed の条件 → switchport の順。

    サブインターフェース(名前に . を含む)も同じ順で判定し、親は parent_interface で表す。
    """
    interface = ctx.interface
    for prefix, type_ in _TYPE_BY_NAME_PREFIX:
        if ctx.name.startswith(prefix):
            interface["type"] = type_
            return True
    if ctx.huawei:
        for prefix, type_ in _HUAWEI_TYPE_BY_NAME_PREFIX:
            if ctx.name.startswith(prefix):
                interface["type"] = type_
                return True
    if (name_type := _aoscx_name_type(ctx.name)) is not None:
        interface["type"] = name_type
        return True
    if ctx.aos_switch and _is_aos_trunk_name(ctx.name):
        interface["type"] = "port_channel"
        return True
    if ctx.asa and not ctx.switchport_line:
        # ASA のインターフェースは、switchport の行が無ければ L3(ASA 5505・Firepower 1010 のポートは switchport の行がある。REQ-304)
        interface["type"] = "routed"
        return True
    if (
        ctx.no_switchport
        or ctx.routing
        or interface["ip_address"] is not None
        or interface["address_mode"] is not None
    ):
        interface["type"] = "routed"
    else:
        interface["type"] = "switchport"
    return True


# 並び＝優先順位(現在の if / elif の順)。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "interfaces.interface", "top", r"interface (?P<name>.*)", "interfaces[name]",
        "interface GigabitEthernet1/0/1", _open,
        note="interface 行。名前は `interface ` の後ろ全体。以降の子行はこの interface に写す",
    ),
    Pattern(
        "interfaces.no_switchport", "interface", r"no switchport", "type(routed の判定にだけ使う)",
        "no switchport", _no_switchport,
        note="IR のキーには写さない。type の判定(interfaces.type)の入力になる",
    ),
    Pattern(
        "interfaces.switchport", "interface", r"switchport", "type(値は変えない)", "switchport", _switchport,
        note=(
            "L2 のポートの明示。既定と同じなので IR の値は変えない。"
            "`no switchport` に変われば `type` の差分になる（ADR-0043 決定2）"
        ),
    ),
    Pattern(
        "interfaces.routing", "interface", r"routing", "type(routed の判定にだけ使う)", "routing", _routing,
        note="手続き。AOS-CX の L3 のポート。IR のキーには写さず、type の判定(interfaces.type)で `no switchport` と同じ"
        "入力にする(ADR-0210 決定3、REQ-294)。後ろに語が続く行と、子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.no_routing", "interface", r"no routing", "type(値は変えない)", "no routing", _no_routing,
        note="手続き。AOS-CX の L2 のポート。IR の値は変えず、前に読んだ `routing` を打ち消す(後の行が勝つ。"
        "ADR-0210 決定3、REQ-294)。後ろに語が続く行と、子を持つ行は未解釈",
    ),
    Pattern("interfaces.shutdown", "interface", r"shutdown", "shutdown", "shutdown", _shutdown),
    Pattern("interfaces.no_shutdown", "interface", r"no shutdown", "shutdown", "no shutdown", _no_shutdown),
    Pattern(
        "interfaces.no_ip_address", "interface", r"no ip address", "ip_address", "no ip address", _no_ip_address,
        note="hier_config のツリーはこの行を落とすため、build_tree を通した config では当たらない(REQ-10 で判明。振る舞いは変えていない)",
    ),
    Pattern(
        "interfaces.description", "interface", r"description (?P<text>.+)", "description",
        "description uplink to core", _description,
    ),
    Pattern(
        "interfaces.switchport.mode", "interface",
        r"switchport mode (?P<mode>access|trunk|dot1q-tunnel"
        r"|private-vlan (?:host|promiscuous|trunk(?: promiscuous| secondary)?)|dynamic (?:auto|desirable)|tap|tap-tool)",
        "switchport.mode", "switchport mode access", _switchport_mode,
        note="手続き。方式の語(空白は1つ)を写す。`dot1q-tunnel`・`private-vlan …`・`dynamic …`・`tap`・`tap-tool` は "
        "ADR-0126 決定1、REQ-171。`trunk phone` は次のパターンが読む。ほかの方式は未解釈",
    ),
    Pattern(
        "interfaces.switchport.mode_trunk_phone", "interface", r"switchport mode trunk phone",
        "switchport.mode・switchport.phone", "switchport mode trunk phone", _switchport_mode_trunk_phone,
        note="手続き。mode を trunk にし、印 `mode trunk phone` を switchport.phone の集合に足す(ADR-0060、REQ-71)。"
        "4語の行なので interfaces.switchport.mode(access・trunk の1語)には当たらず、別に読む。"
        "mode の型は変えない(ADR-0060 決定2)。後ろに語が続く形と、子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.switchport.access_vlan", "interface", r"switchport access vlan (?P<id>\d+)",
        "switchport.access_vlan", "switchport access vlan 10", _access_vlan,
    ),
    Pattern(
        "interfaces.switchport.native_vlan", "interface", r"switchport trunk native vlan (?P<id>\d+)",
        "switchport.native_vlan", "switchport trunk native vlan 99", _native_vlan,
        note="読めたら `native_vlan_none` を False に戻す(ADR-0206 決定4、REQ-289)",
    ),
    Pattern(
        "interfaces.switchport.native_vlan_none", "interface", r"switchport trunk native vlan none",
        "switchport.native_vlan・switchport.native_vlan_none", "switchport trunk native vlan none",
        _native_vlan_none,
        note="手続き。`native_vlan` を None、`native_vlan_none` を True にする。後に `native vlan <数>` があれば"
        "False に戻る。機種の見立てに関係なく読む(ADR-0206 決定4、REQ-289)",
    ),
    Pattern(
        "interfaces.switchport.trunk_allowed_vlan", "interface",
        r"switchport trunk allowed vlan (?P<range>\d[\d,\-]*)", "switchport.trunk_allowed_vlan",
        "switchport trunk allowed vlan 10,20-30", _allowed_vlan,
        note="範囲を展開して VLAN ID のタプルにする。読めたら `trunk_allowed_none` を False に戻す。"
        "展開できない書式は未解釈にする",
    ),
    Pattern(
        "interfaces.switchport.trunk_allowed_vlan_all", "interface", r"switchport trunk allowed vlan all",
        "switchport.trunk_allowed_vlan", "switchport trunk allowed vlan all", _allowed_vlan_all,
        note="手続き。行が無いときと同じ `()` にする(すべて通す。ADR-0111 決定1)。`trunk_allowed_none` を False に"
        "戻す。2回あれば後の行。`none`・`add` は次のパターンが読む。`remove` は AW+ と見立てたときだけ、"
        "`trunk_allowed_vlan_remove` が読む(今の値が空でない一覧のとき)。`except` は未解釈",
    ),
    Pattern(
        "interfaces.switchport.trunk_allowed_vlan_none", "interface", r"switchport trunk allowed vlan none",
        "switchport.trunk_allowed_vlan・switchport.trunk_allowed_none", "switchport trunk allowed vlan none",
        _allowed_vlan_none,
        note="手続き。`trunk_allowed_vlan` を `()`、`trunk_allowed_none` を True にする(1つも通さない)。"
        "2回以上あれば後の行が勝つ。後ろに語が続く行は未解釈。`none` のあとの `add` は未解釈(ADR-0132、REQ-177)",
    ),
    Pattern(
        "interfaces.switchport.trunk_allowed_vlan_add", "interface",
        r"switchport trunk allowed vlan add (?P<range>\d[\d,\-]*)", "switchport.trunk_allowed_vlan",
        "switchport trunk allowed vlan add 300,350-352", _allowed_vlan_add,
        note="手続き。前に読んだ数の一覧に範囲を展開して足す(和集合・昇順のタプル)。前に一覧が無い・"
        "`all` のあと・展開できない行は未解釈(ADR-0131 決定1、REQ-176)。AW+ と見立てた config では、"
        "`none` の後でも前に `all` の行でもない、前に一覧が無い `add` は、空の一覧に足す(ADR-0206 決定3、REQ-289)",
    ),
    Pattern(
        "interfaces.switchport.trunk_allowed_vlan_remove", "interface",
        r"switchport trunk allowed vlan remove (?P<range>\d[\d,\-]*)", "switchport.trunk_allowed_vlan",
        "switchport trunk allowed vlan remove 20", _allowed_vlan_remove,
        note="手続き。AW+ と見立てた config で、今の値が空でない一覧なら、範囲を展開してそこから引く(昇順のタプル。"
        "全部引けば `()`)。AW+ と見立てない・今の値が `()`(行が無い・`all`・`none`)・展開できない行は未解釈"
        "(ADR-0206 決定3、REQ-289)",
    ),
    Pattern(
        "interfaces.switchport.trunk_group", "interface", r"switchport trunk group (?P<name>\S+)",
        "switchport.trunk_groups", "switchport trunk group MLAG-PEER", _trunk_group,
        note="手続き。1行に1つの名前を集合に足す(同じ名前は重ねず昇順)。後ろが1語でない行・`no` 付きの行・"
        "子を持つ行は未解釈(ADR-0058 決定2)",
    ),
    Pattern(
        "interfaces.switchport.voice_vlan", "interface", r"switchport voice vlan (?P<id>\d+)",
        "switchport.voice_vlan", "switchport voice vlan 20", _voice_vlan,
        note="整数にして写す(ADR-0068 決定1)。後ろが数でない行(dot1p・untagged など)は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.switchport.phone", "interface", r"switchport phone (?P<rest>.+)", "switchport.phone",
        "switchport phone vlan 20", _switchport_phone,
        note="手続き。Arista の IP 電話の設定。`switchport phone` の後ろ(空白は1つ)を switchport.phone の集合に足す。"
        "同じ文字列は重ねず、文字列の昇順(ADR-0060、REQ-71)。voice_vlan とはそろえない。"
        "`no switchport phone …` と、`switchport phone` だけの行は未解釈。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.switchport.vlan_access", "interface", r"vlan access (?P<id>\d+)", "switchport.access_vlan・switchport.mode",
        "vlan access 10", _vlan_access,
        note="手続き。AOS-CX の形。access_vlan を数にし、mode を access にする。2回あれば後の行。"
        "後ろに語が続く行と、子を持つ行は未解釈(ADR-0210 決定4、REQ-294)",
    ),
    Pattern(
        "interfaces.switchport.vlan_trunk_native", "interface", r"vlan trunk native (?P<id>\d+)(?P<tag> tag)?",
        "switchport.native_vlan・switchport.native_vlan_none・switchport.native_vlan_tagged・switchport.mode",
        "vlan trunk native 1", _vlan_trunk_native,
        note="手続き。AOS-CX の形。native_vlan を数、native_vlan_none を偽にし、`tag` があれば native_vlan_tagged を真、"
        "無ければ偽にして、mode を trunk にする。2回あれば後の行。後ろにほかの語が続く行"
        "(`vlan trunk native 1 allowed …` など)と、子を持つ行は未解釈(ADR-0210 決定5、REQ-294)",
    ),
    Pattern(
        "interfaces.switchport.vlan_trunk_allowed", "interface", r"vlan trunk allowed (?P<range>\d[\d,\-]*)",
        "switchport.trunk_allowed_vlan・switchport.trunk_allowed_none・switchport.mode",
        "vlan trunk allowed 10,20-22", _vlan_trunk_allowed,
        note="手続き。AOS-CX の形。範囲を展開して、今の一覧との和集合(昇順のタプル)にする。今の値が `()` で前に `all` の行"
        "(`vlan trunk allowed all`・`switchport trunk allowed vlan all`)を読んでいれば未解釈。展開できない一覧・"
        "後ろに語が続く行・子を持つ行も未解釈。読めたら trunk_allowed_none を偽にし、mode を trunk にする"
        "(ADR-0210 決定6、REQ-294)",
    ),
    Pattern(
        "interfaces.switchport.vlan_trunk_allowed_all", "interface", r"vlan trunk allowed all",
        "switchport.trunk_allowed_vlan・switchport.trunk_allowed_none・switchport.mode",
        "vlan trunk allowed all", _vlan_trunk_allowed_all,
        note="手続き。AOS-CX の形。`()` にし、trunk_allowed_none を偽、mode を trunk にして、前に `all` を読んだ印を付ける"
        "(AW+ の `switchport trunk allowed vlan all` と同じ印)。後ろに語が続く行と、子を持つ行は未解釈"
        "(ADR-0210 決定6、REQ-294)",
    ),
    Pattern(
        "interfaces.address_mode_dhcp", "interface", r"ip address dhcp(?: (?P<rest>.+))?", "address_mode",
        "ip address dhcp", _address_mode_dhcp,
        note="手続き。`dhcp`、後ろに語があれば `dhcp <残り>`(空白は1つ)を address_mode に写す。2回あれば後の行"
        "(ADR-0109 決定2)。`ip address` のほかのパターンより前に置く(後ろだと、マスクの形に当たって写せず未解釈になる)",
    ),
    Pattern(
        "interfaces.address_mode_negotiated", "interface", r"ip address negotiated", "address_mode",
        "ip address negotiated", _address_mode_negotiated,
        note="手続き。`negotiated` を address_mode に写す。2回あれば後の行(dhcp と同じ。ADR-0136 決定3、REQ-183)。"
        "`ip address` のほかのパターンより前に置く。後ろに語が続く形は、マスクの形(interfaces.ip_address)に当たって"
        "IPv4 として読めず未解釈",
    ),
    Pattern(
        "interfaces.address_mode_unnumbered", "interface", r"ip address unnumbered (?P<name>\S+)", "address_mode",
        "ip address unnumbered Loopback0", _address_mode_unnumbered,
        note="手続き。Arista の形。`unnumbered <if>` を address_mode に写す。2回あれば後の行(ADR-0109 決定2)。"
        "`ip address` のほかのパターンより前に置く(後ろだと、マスクの形に当たって写せず未解釈になる)。"
        "後ろに語が続く形は未解釈",
    ),
    Pattern(
        "interfaces.address_mode_ip_unnumbered", "interface", r"ip unnumbered (?P<name>\S+)", "address_mode",
        "ip unnumbered Loopback0", _address_mode_unnumbered,
        note="手続き。Cisco の形。`unnumbered <if>` を address_mode に写す(Arista の形と同じ値)。2回あれば後の行。"
        "後ろに語が続く形は未解釈",
    ),
    Pattern(
        "interfaces.address_virtual", "interface",
        r"ip address virtual (?P<addr>[^\s/]+)/(?P<length>\d+)(?P<secondary> secondary)?", "virtual_addresses",
        "ip address virtual 192.0.2.1/24", _address_virtual,
        note="手続き。Arista のエニーキャストのゲートウェイ。IPv4Interface の正規の書き方(secondary 付きは後ろに "
        "` secondary`)を virtual_addresses の集合に足す。同じ文字列は重ねず、文字列の昇順(ADR-0048 決定3)。"
        "`ip address` のほかのパターンより前に置く(後ろだと、マスクの形の行が先に当たって写せず未解釈になる)。"
        "IPv4 として読めないものは未解釈",
    ),
    Pattern(
        "interfaces.ip_address_prefix", "interface", r"ip address (?P<addr>[^\s/]+)/(?P<length>\d+)", "ip_address",
        "ip address 192.0.2.1/24", _ip_address_prefix,
        note="Arista・NX-OS の `/` の形。マスクの形と同じ値になる。IPv4 として読めないものは未解釈にする",
    ),
    Pattern(
        "interfaces.ip_address_prefix_secondary", "interface",
        r"ip address (?P<addr>[^\s/]+)/(?P<length>\d+) secondary",
        "secondary_addresses", "ip address 192.0.2.129/25 secondary", _ip_address_prefix_secondary,
        note=(
            "`/` の形の secondary。interfaces.ip_address より前に置く"
            "(後ろだと、マスクの形の行が先に当たって写せず未解釈になる)"
        ),
    ),
    Pattern(
        "interfaces.ip_address", "interface", r"ip address (?P<addr>\S+) (?P<mask>\S+)", "ip_address",
        "ip address 192.0.2.1 255.255.255.0", _ip_address,
        note=(
            "アドレスとマスクが IPv4 として読めないものは未解釈にする。secondary 付きは次の行が読む。"
            "長さの形(Huawei VRP の `ip address 192.0.2.1 24`。ADR-0231、REQ-336)も同じ持ち方で読む(見立てに関わらない)"
        ),
    ),
    Pattern(
        "interfaces.ip_address_secondary", "interface", r"ip address (?P<addr>\S+) (?P<mask>\S+) secondary",
        "secondary_addresses", "ip address 192.0.2.129 255.255.255.128 secondary", _ip_address_secondary,
        note="書かれた順に足す(primary の ip_address とは別に持つ)。IPv4 として読めないものは未解釈にする",
    ),
    Pattern(
        "interfaces.asa_ip_address_standby", "interface",
        r"ip address (?P<addr>\S+) (?P<mask>\S+) standby (?P<standby>\S+)", "ip_address, ip_options",
        "ip address 192.0.2.1 255.255.255.0 standby 192.0.2.2", _asa_ip_address_standby,
        note=(
            "手続き。ASA と見立てた config だけ(ADR-0214 決定 5、REQ-304)。`ip address <A> <マスク>` と同じ手続きで "
            "ip_address に写し、`ip address standby <B>` を ip_options の集合に足す。A・マスク・B が IPv4 として読めない行、"
            "standby の後ろに語が続く行、子を持つ行は未解釈。ASA と見立てない config では未解釈"
        ),
    ),
    # IOS-XR のインターフェースのアドレス(ADR-0211 決定5、REQ-296)。`ip …` と同じ欄に、同じ手続きで読む。
    # dhcp・unnumbered の語の決まった形を、マスクの形より前に置く。`/` の形・secondary 付きも、そうでない形より前に置く
    Pattern(
        "interfaces.ipv4_address_dhcp", "interface", r"ipv4 address dhcp(?: (?P<rest>.+))?", "address_mode",
        "ipv4 address dhcp", _leaf_only(_address_mode_dhcp),
        note="手続き。IOS-XR の形。`ip address dhcp` と同じ値(`dhcp [<残り>]`)を address_mode に写す(ADR-0211 決定5)。"
        "`ipv4 address` のほかのパターンより前に置く。`ipv4 address dhcp-client-options …` は当たらず、"
        "マスクの形にも読めず未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.ipv4_unnumbered", "interface", r"ipv4 unnumbered (?P<name>\S+)", "address_mode",
        "ipv4 unnumbered Loopback0", _leaf_only(_address_mode_unnumbered),
        note="手続き。IOS-XR の形。`unnumbered <if>` を address_mode に写す(`ip unnumbered` と同じ値。ADR-0211 決定5)。"
        "後ろに語が続く形と、子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.ipv4_address_prefix_secondary", "interface",
        r"ipv4 address (?P<addr>[^\s/]+)/(?P<length>\d+) secondary",
        "secondary_addresses", "ipv4 address 192.0.2.129/25 secondary", _leaf_only(_ip_address_prefix_secondary),
        note="IOS-XR の `/` の形の secondary(ADR-0211 決定5)。interfaces.ipv4_address_prefix より前に置く。"
        "IPv4 として読めないものと、子を持つ行は未解釈にする",
    ),
    Pattern(
        "interfaces.ipv4_address_prefix", "interface", r"ipv4 address (?P<addr>[^\s/]+)/(?P<length>\d+)", "ip_address",
        "ipv4 address 192.0.2.1/24", _leaf_only(_ip_address_prefix),
        note="IOS-XR の `/` の形(ADR-0211 決定5)。マスクの形と同じ値になる。IPv4 として読めないものは未解釈にする。"
        "`route-tag` などの語が続く行は当たらず未解釈。子を持つ行も未解釈",
    ),
    Pattern(
        "interfaces.ipv4_address_secondary", "interface", r"ipv4 address (?P<addr>\S+) (?P<mask>\S+) secondary",
        "secondary_addresses", "ipv4 address 192.0.2.129 255.255.255.128 secondary", _leaf_only(_ip_address_secondary),
        note="IOS-XR の形(ADR-0211 決定5)。interfaces.ipv4_address より前に置く。"
        "書かれた順に足す。IPv4 として読めないものと、子を持つ行は未解釈にする",
    ),
    Pattern(
        "interfaces.ipv4_address", "interface", r"ipv4 address (?P<addr>\S+) (?P<mask>\S+)", "ip_address",
        "ipv4 address 192.0.2.1 255.255.255.0", _leaf_only(_ip_address),
        note="IOS-XR の形(ADR-0211 決定5)。アドレスとマスクが IPv4 として読めないものは未解釈にする。"
        "`route-tag` などほかの語が続く行は当たらず未解釈。子を持つ行も未解釈",
    ),
    Pattern(
        "interfaces.ipv6_address_virtual", "interface", r"ipv6 address virtual (?P<addr>[^\s/]+)/(?P<length>\d+)",
        "virtual_addresses", "ipv6 address virtual 2001:db8::1/64", _ipv6_address_virtual,
        note="手続き。IPv6Interface の正規の書き方を virtual_addresses の集合に足す(ADR-0048 決定3)。"
        "IPv6 として読めないものは未解釈",
    ),
    Pattern(
        "interfaces.ipv6_address", "interface",
        r"ipv6 address (?P<addr>[^\s/]+)/(?P<length>\d+)(?: (?P<kind>eui-64|anycast))?", "ipv6_addresses",
        "ipv6 address 2001:db8::1/64", _ipv6_address,
        note="手続き。IPv6Interface の正規の書き方(`eui-64`・`anycast` が付けば後ろに空白とその語)を "
        "ipv6_addresses の集合に足す。同じ文字列は重ねず、文字列の昇順(ADR-0048 決定1)。"
        "IPv6 として読めないもの・表に無い語が続くものは未解釈",
    ),
    Pattern(
        "interfaces.ipv6_address_link_local", "interface", r"ipv6 address (?P<addr>[^\s/]+) link-local",
        "ipv6_addresses", "ipv6 address fe80::1 link-local", _ipv6_address_link_local,
        note="手続き。IPv6Address の正規の書き方と ` link-local` を ipv6_addresses の集合に足す。"
        "IPv6 として読めないものは未解釈",
    ),
    Pattern(
        "interfaces.ipv6_address_word", "interface", r"ipv6 address (?P<word>auto-config|autoconfig|dhcp)",
        "ipv6_addresses", "ipv6 address auto-config", _ipv6_address_word,
        note="手続き。アドレスでない形はその語を ipv6_addresses の集合に足す。後ろに語が続く形は未解釈",
    ),
    Pattern(
        "interfaces.ipv6_address_name", "interface",
        r"ipv6 address (?!(?:auto-config|autoconfig|dhcp|virtual)(?: |$))(?P<name>[A-Za-z][^\s:/]*) (?P<rest>.+)",
        "ipv6_addresses", "ipv6 address GENERAL-PREFIX ::1/64", _ipv6_address_name,
        note="手続き。一般プレフィックスの形。`<名前> <残り>`(空白は1つ)を ipv6_addresses の集合に足す。同じ文字列は"
        "重ねず、文字列の昇順(アドレスの形と混ざってよい。ADR-0136 決定3、REQ-183)。名前は英字で始まり、`:`・`/` を"
        "含まない語。`auto-config`・`autoconfig`・`dhcp`・`virtual` で始まる行は当てない(後ろに語が続く形は未解釈)。"
        "`<名前> link-local` は interfaces.ipv6_address_link_local に当たって未解釈。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.ipv6_enable", "interface", r"ipv6 enable", "ipv6_enabled", "ipv6 enable", _ipv6_enable,
        note="手続き。True にする(ADR-0048 決定2)。2回あれば後の行",
    ),
    Pattern(
        "interfaces.no_ipv6_enable", "interface", r"no ipv6 enable", "ipv6_enabled", "no ipv6 enable",
        _no_ipv6_enable, note="手続き。False にする。2回あれば後の行",
    ),
    Pattern(
        "interfaces.virtual_router", "interface",
        r"(?P<family>ip|ipv6) virtual-router address (?P<addr>[^\s/]+)(?:/(?P<length>\d+))?", "virtual_addresses",
        "ip virtual-router address 192.0.2.254", _virtual_router,
        note="手続き。Arista の VARP。アドレスだけの正規の書き方(`/<長さ>` は捨てる)と ` virtual-router` を "
        "virtual_addresses の集合に足す(ADR-0048 決定3)。`ip` は IPv4、`ipv6` は IPv6 として読めないものは未解釈",
    ),
    Pattern(
        "interfaces.vrf", "interface", r"(?:ip )?vrf forwarding (?P<name>\S+)", "vrf",
        "vrf forwarding RED", _vrf,
    ),
    Pattern(
        "interfaces.vrf_attach", "interface", r"vrf attach (?P<name>\S+)", "vrf", "vrf attach BLUE", _vrf,
        note="AOS-CX の形。名前が1語のときだけ。後ろに語が続く行は未解釈。`vrf <名前>`(interfaces.vrf_name)より前に置く"
        "(ADR-0210 決定8、REQ-294)",
    ),
    Pattern(
        "interfaces.vrf_name", "interface", r"vrf (?P<name>\S+)", "vrf", "vrf MGMT", _vrf,
        note="Arista の形。名前が1語のときだけ。`vrf forwarding …` は interfaces.vrf が先に読む",
    ),
    Pattern(
        "interfaces.vrf_member", "interface", r"vrf member (?P<name>\S+)", "vrf", "vrf member RED", _vrf,
        note="NX-OS の形。名前が1語のときだけ。2語なので `vrf <名前>`(interfaces.vrf_name)には当たらない",
    ),
    Pattern(
        "interfaces.poe_options", "interface", r"(?:no )?power inline (?:port|police) .+", "poe_options",
        "power inline port priority low", _line_option("poe_options"),
        note="手続き。行の原文(空白は1つ)を poe_options の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0111 決定3)。後ろの語が要る。interfaces.poe と interfaces.poe_rest より前に置く。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern("interfaces.poe", "interface", r"power inline (?P<mode>\S+)", "poe", "power inline auto", _poe),
    Pattern(
        "interfaces.poe_rest", "interface", r"power inline (?P<mode>auto|static|never|consumption) (?P<rest>.+)",
        "poe", "power inline auto max 30000", _poe_rest,
        note="手続き。`<方式> <残り>`(空白は1つ)を poe に写す(ADR-0111 決定2。speed と同じく後ろ全体)。"
        "表に無い方式の後ろに語が続く形は未解釈",
    ),
    Pattern(
        "interfaces.poe_disabled", "interface", r"poe (?P<mode>disabled)", "poe", "poe disabled", _poe_disabled,
        note="手続き。Arista の `poe disabled` を poe に `disabled` と写す(ADR-0115 決定1、REQ-160。語は原文のまま)。"
        "後ろに語が続く形と no 付きは interfaces.poe_options_arista が読む。子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.poe_options_arista", "interface", r"(?:no )?poe .+", "poe_options",
        "poe priority critical", _line_option("poe_options"),
        note="手続き。Arista の `poe …`(`poe disabled` だけの行を除く)と no 付きの行の原文(空白は1つ)を "
        "poe_options の集合に足す。同じ文字列は重ねず、文字列の昇順(ADR-0115 決定1、REQ-160)。後ろの語が要る"
        "(`poe` だけの行は未解釈)。interfaces.poe_disabled より後ろに置く。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.speed", "interface", r"speed (?P<value>\S+(?: \S+)*)", "speed", "speed 1000", _speed,
        note=(
            "後ろ全体を原文で持つ(語は空白1つでつなぐ)。"
            "`forced` を外したり、速度と duplex に分けたりしない(ADR-0043 決定3)"
        ),
    ),
    Pattern("interfaces.duplex", "interface", r"duplex (?P<mode>\S+)", "duplex", "duplex full", _duplex),
    Pattern(
        "interfaces.mtu", "interface", r"mtu (?P<n>\d+)", "mtu", "mtu 9214", _mtu,
        note=(
            "整数にして写す。2回あれば後の行。`ip mtu`(L3 の MTU)は別の設定なので写さない"
            "(`ip mtu <数>` は interfaces.ip_options が読む。ADR-0121、REQ-166)。"
            "数でない値・2語以上は未解釈"
        ),
    ),
    Pattern(
        "interfaces.channel_group", "interface", r"channel-group (?P<id>\d+) mode (?P<mode>\S+)",
        "channel_group", "channel-group 1 mode active", _channel_group,
    ),
    Pattern(
        "interfaces.channel_group_auto", "interface", r"channel-group auto", "channel_group_auto",
        "channel-group auto", _channel_group_auto,
        note="True にする(ADR-0111 決定4)。2回あれば後の行。後ろに語が続く形と、子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.no_channel_group_auto", "interface", r"no channel-group auto", "channel_group_auto",
        "no channel-group auto", _no_channel_group_auto, note="False にする。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.channel_group_lag", "interface", r"lag (?P<id>\d+)", "channel_group", "lag 1", _channel_group_lag,
        note="手続き。AOS-CX の LAG のメンバー。channel_group を `{id: 数, mode: lag}` にする。2回あれば後の行。"
        "後ろに語が続く行と、子を持つ行は未解釈。最上位や `vsx` の中の `lag` の語は対象外(ADR-0210 決定7、REQ-294)",
    ),
    Pattern(
        "interfaces.mlag", "interface", r"mlag (?P<n>\d+)", "mlag_id", "mlag 10", _mlag,
        note=(
            "整数にして写す(ADR-0054、REQ-65)。2回あれば後の行。数でない値・2語以上・`no mlag …` は未解釈。"
            "子を持つ行は未解釈。NX-OS の `vpc <n>` は interfaces.vpc が同じ mlag_id に読む(ADR-0136 決定3、REQ-183)"
        ),
    ),
    Pattern(
        "interfaces.vpc", "interface", r"vpc (?P<n>\d+)", "mlag_id", "vpc 10", _mlag,
        note="手続き。NX-OS の `vpc <数>` を整数にして mlag_id に写す(`mlag <数>` と同じ。ADR-0136 決定3、REQ-183)。"
        "2回あれば後の行。`no vpc …` は未解釈。子を持つ行は未解釈。`vpc peer-link` などは interfaces.port_options が読む",
    ),
    Pattern(
        "interfaces.ospf_auth_key", "interface", r"ip ospf authentication-key(?: (?P<rest>.+))?", "ospf",
        "ip ospf authentication-key 7 <値>", _ospf_auth_key,
        note="手続き。`authentication-key` を ospf の集合に足す。鍵の値は持たない(ADR-0055 決定1、REQ-66)。"
        "後ろの語(`[<型>] <値>`)が1つか2つでない行は未解釈。interfaces.ospf より前に置く"
        "(形の合わない鍵の行を一般の行として読まない)。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.ospf_md_key", "interface", r"ip ospf message-digest-key(?: (?P<rest>.+))?", "ospf",
        "ip ospf message-digest-key 1 sha256 7 <値>", _ospf_md_key,
        note="手続き。`message-digest-key <番号> <方式>` を ospf の集合に足す。鍵の値は持たない(ADR-0055 決定1、REQ-66)。"
        "番号が数でない行と、後ろの語(`<番号> <方式> [<型>] <値>`)が3つか4つでない行(後ろの無い行も)は未解釈。"
        "interfaces.ospf より前に置く(形の合わない鍵の行を一般の行として読まない)。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.ospf", "interface", r"ip ospf (?P<rest>.+)|(?:no )?ipv6 ospf .+", "ospf", "ip ospf cost 100", _ospf,
        note="手続き。`ip ospf` の後ろ(空白は1つ)を ospf の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0055、REQ-66)。`ipv6 ospf …`(no 付きも)は行の原文(空白は1つ)を足す(ADR-0120、REQ-165)。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "`ipv6 ospf` の次の語が authentication・encryption で始まる行(`key` の語が無いまま鍵が続く)は未解釈。"
        "`no ip ospf …` は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.ospf_router", "interface", r"ip router ospf (?P<tag>\S+) area (?P<rest>.+)", "ospf",
        "ip router ospf 1 area 0.0.0.0", _ospf_router,
        note="手続き。NX-OS の形。`<タグ> area <残り>`(空白は1つ)を ospf の集合に足す(`ip ospf <タグ> area <残り>` と"
        "同じ値。ADR-0136 決定3、REQ-183)。`area` の無い形(`ip router ospf 1`)と `no ip router ospf …` は未解釈。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.encapsulation", "interface", r"encapsulation (?P<rest>.+)", "encapsulation",
        "encapsulation dot1Q 100", _encapsulation,
        note="手続き。後ろ(空白は1つ)を原文で写す。機種の書き方はそろえない(ADR-0055、REQ-66)。2回あれば後の行。"
        "Arista のブロックの書き方 `encapsulation vlan` は、子があっても無くても未解釈。`no encapsulation …` は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.autostate", "interface", r"autostate", "autostate", "autostate", _autostate,
        note="True にする(ADR-0055、REQ-66)。2回あれば後の行。後ろに語が続く形と、子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.no_autostate", "interface", r"no autostate", "autostate", "no autostate", _no_autostate,
        note="False にする。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.isis_auth_key", "interface", r"isis authentication key(?: (?P<rest>.+))?", "isis",
        "isis authentication key 7 <値>", _isis_auth_key,
        note="手続き。`authentication key` を isis の集合に足す。鍵の値も型も段(level-1・level-2)も持たない"
        "(ADR-0056、REQ-67)。後ろが `[<型>] <値> [level-1|level-2]`(型は数か 8a)の形でない行(後ろの無い行も)は未解釈。"
        "interfaces.isis より前に置く(形の合わない鍵の行を一般の行として読まない)。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.isis", "interface", r"(?P<no>no )?isis (?P<rest>.+)", "isis", "isis enable CORE", _isis,
        note="手続き。`isis` の後ろ(空白は1つ)を isis の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0056、REQ-67)。`no isis <残り>` は `no <残り>`(ADR-0133 決定4、REQ-178)。"
        "語のどれかが password・secret・key の行(`no isis authentication key …` も)と、語の中に鍵が埋まった"
        "(key, や password= などの)行は未解釈。`isis`・`no isis` だけの行は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.isis_router", "interface", r"ip router isis(?: (?P<tag>\S+))?", "isis", "ip router isis CORE",
        _isis_router,
        note="手続き。NX-OS の形。`router`、タグがあれば `router <タグ>` を isis の集合に足す(ADR-0136 決定3、REQ-183)。"
        "タグの後ろに語が続く形と `no ip router isis …` は未解釈。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.bfd", "interface", r"(?P<no>no )?bfd (?P<rest>.+)", "bfd",
        "bfd interval 300 min_rx 300 multiplier 3", _bfd,
        note="手続き。`bfd` の後ろ(空白は1つ)を bfd の集合に足す。`no bfd <残り>` は `no <残り>`。同じ文字列は重ねず、"
        "文字列の昇順(ADR-0128 決定2、ADR-0059 決定2、REQ-173)。語のどれかが password・secret・key の行"
        "(`bfd authentication … key …` など)と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "`bfd`・`no bfd` だけの行は当たらず未解釈。子を持つ行は、子も含めて未解釈。最上位の `bfd …` は bfd が読む",
    ),
    Pattern(
        "interfaces.spanning_tree", "interface", r"spanning-tree (?P<rest>.+)", "spanning_tree",
        "spanning-tree portfast", _spanning_tree,
        note="手続き。残り(空白は1つにそろえる)を spanning_tree の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0042 決定2)。後ろが無い spanning-tree は当たらず未解釈",
    ),
    Pattern(
        "interfaces.no_spanning_tree", "interface", r"no spanning-tree (?P<rest>.+)", "spanning_tree",
        "no spanning-tree portfast auto", _no_spanning_tree,
        note="手続き。`no <残り>` を spanning_tree の集合に足す。同じ文字列は重ねず、文字列の昇順",
    ),
    Pattern(
        "interfaces.qos", "interface",
        r"(?:no )?(?P<head>service-policy|service-profile|auto qos|trust device|mls qos|qos trust|priority-queue"
        r"|srr-queue|queue-set|wrr-queue) .+",
        "qos", "service-policy input PM-EDGE", _qos,
        note="手続き。行の原文を qos の集合に足す。同じ文字列は重ねず、文字列の昇順(ADR-0066、REQ-77)。"
        "Arista の `service-profile <名前>` も読む(ADR-0115 決定2、REQ-160)。"
        "Catalyst の待ち行列の `srr-queue …`・`queue-set …`・`wrr-queue …` も読む(ADR-0128 決定3、REQ-173)。"
        "後ろが無い service-policy などは当たらず未解釈。子の行を持つ行は、その行も子も未解釈。"
        "`service-profile …` は、語のどれかが password・secret・key の行と、語の中に鍵が埋まった行も未解釈"
        "(`ip nat service-profile …` は頭の語が違うので当たらない)",
    ),
    Pattern(
        "interfaces.access_group", "interface",
        r"(?:(?:ip|ipv6|mac) access-group|ipv6 traffic-filter) \S+ (?:in|out)"
        r"|(?P<filter>(?:ip|ipv6) filter \S+ \d+ (?:in|out))", "access_groups",
        "ip access-group ACL-IN in", _access_group,
        note="手続き。行の原文(空白は1つ)を access_groups の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0057 決定1)。向きの無い形と `vrf` などの語がある形は当たらず未解釈。子を持つ行は未解釈。"
        "NEC IX の `ip filter <ACL 名> <番号> in|out`・`ipv6 filter …` も読む(ADR-0209 決定6、REQ-292)。"
        "この形は、伏せる表の秘密語がある行(ACL の名前が snmp など)は未解釈",
    ),
    Pattern(
        "interfaces.dot1x", "interface",
        r"dot1x (?P<rest>.+)|(?:no )?(?:authentication .+|mab(?: .+)?|access-session .+)", "dot1x",
        "dot1x port-control auto", _dot1x,
        note="手続き。ポートの 802.1X。`dot1x` の後ろ(空白は1つ)を dot1x の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0060、REQ-71)。`no dot1x …` と、`dot1x` だけの行は未解釈。最上位の `dot1x …` は読まない。"
        "Cisco の旧来の書き方 `authentication <残り>`・`mab`・`mab <残り>`(no 付きも)は行の原文(空白は1つ)を足す"
        "(ADR-0120、REQ-165)。`authentication` だけの行は未解釈。"
        "`access-session <残り>`(no 付きも)も行の原文(空白は1つ)を足す(ADR-0122 決定4、REQ-167)。"
        "`access-session` だけの行は未解釈。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.negotiation", "interface", r"(?:negotiation|negotiate) auto", "negotiation", "negotiation auto",
        _negotiation,
        note="True にする(ADR-0057 決定2)。NX-OS の `negotiate auto` も同じ(ADR-0116 決定2、REQ-161)。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.no_negotiation", "interface", r"no (?:negotiation|negotiate) auto", "negotiation", "no negotiation auto",
        _no_negotiation, note="False にする。NX-OS の `no negotiate auto` も同じ(ADR-0116 決定2)。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.bandwidth", "interface", r"bandwidth (?P<n>\d+)", "bandwidth", "bandwidth 10000", _bandwidth,
        note="整数(kbps)にして写す。2回あれば後の行。数でない値・2語以上は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.mac_address", "interface", r"mac-address (?P<mac>\S+)", "mac_address",
        "mac-address 0000.5e00.5301", _mac_address,
        note="原文で写す。2回あれば後の行。2語以上は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.media_type", "interface", r"media-type (?P<kind>\S+)", "media_type", "media-type gbic",
        _media_type, note="原文で写す。2回あれば後の行。2語以上は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.hsrp", "interface", r"standby (?P<rest>.+)", "hsrp", "standby 1 ip 192.0.2.1", _hsrp,
        note="手続き。`standby` の後ろ(空白は1つ)を hsrp の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0068 決定2)。語に authentication がある行(認証の鍵)は未解釈。後ろが無い standby と "
        "`no standby …` は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.hsrp_version", "interface", r"hsrp version (?P<n>\d+)", "hsrp", "hsrp version 2", _hsrp_version,
        note="手続き。NX-OS の形。`version <数>` を hsrp の集合に足す(ADR-0136 決定3、REQ-183)。"
        "後ろに語が続く形・`no hsrp …`・子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.hsrp_group", "interface", r"hsrp (?P<group>\d+)(?: (?P<rest>.+))?", "hsrp", "hsrp 10",
        _hsrp_group,
        note="手続き。NX-OS の形(ADR-0136 決定3、REQ-183)。子を持たない `hsrp <数>` は `<数>` を hsrp の集合に足す。"
        "子を持たず後ろに語がある行は未解釈。子を持つ行は文字列にしないが解釈済みにし、中の行を "
        "interfaces.hsrp_block_setting で読む(頭は `<数>`、見出しに残りがあれば `<数> <残り>`)。"
        "見出しに authentication の語、password・secret・key の語、語の中に埋まった鍵がある行は、子も含めて未解釈。"
        "`no hsrp …` は未解釈",
    ),
    Pattern(
        "interfaces.hsrp_block_setting", _HSRP_SCOPE, r".+", "hsrp", "priority 150", _hsrp_block_setting,
        note="手続き。子を持つ NX-OS の `hsrp <数> [<残り>]` の中の子を持たない行を、`<頭> <中の行>`(空白は1つ)に"
        "して hsrp の集合に足す。同じ文字列は重ねず、文字列の昇順(`standby` と同じ。ADR-0136 決定3、REQ-183)。"
        "語に authentication がある行(認証の鍵)、語のどれかが password・secret・key の行と、語の中に鍵が埋まった"
        "(key, や password= などの)行は未解釈(同じブロックのほかの行は読む)。子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.vrrp_address", "interface", r"(?P<no>no )?vrrp (?P<vrid>\d+) (?:ip|ipv4)(?: (?P<rest>.+))?",
        "vrrp[].virtual_addresses", "vrrp 1 ip 192.0.2.1", _vrrp_address,
        note="手続き。A型の VRRP(ADR-0125、REQ-170)。`vrrp <VRID> ip|ipv4 <IPv4>`(後ろに `secondary` が付いても。"
        "語は持たない)のアドレスを VRID のグループの virtual_addresses の後ろに足す。グループは VRID ごとに1つで、"
        "VRID の昇順。後ろが `<IPv4> [secondary]` の形でない行は、行の原文(空白は1つ)を vrrp[].options の集合に足す。"
        "`no vrrp <VRID> ip|ipv4 …` は未解釈。語に authentication がある行(認証の鍵)、語のどれかが password・secret・key "
        "の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.vrrp_priority", "interface",
        r"(?P<no>no )?vrrp (?P<vrid>\d+) (?:priority|priority-level)(?: (?P<rest>.+))?",
        "vrrp[].priority", "vrrp 1 priority 110", _vrrp_priority,
        note="手続き。`vrrp <VRID> priority|priority-level <数>` を整数にして priority に写す。2回あれば後の行"
        "(ADR-0125、REQ-170)。後ろが数でない行(後ろの無い行も)は、行の原文(空白は1つ)を vrrp[].options の集合に足す。"
        "`no vrrp <VRID> priority|priority-level …` は未解釈。認証の行・秘密の語がある行・子を持つ行は "
        "interfaces.vrrp_address と同じく未解釈",
    ),
    Pattern(
        "interfaces.vrrp_preempt", "interface", r"(?P<no>no )?vrrp (?P<vrid>\d+) preempt(?: (?P<rest>.+))?",
        "vrrp[].preempt", "vrrp 1 preempt", _vrrp_preempt,
        note="手続き。`vrrp <VRID> preempt` は True、`no vrrp <VRID> preempt` は False にする(ADR-0125、REQ-170)。"
        "`vrrp <VRID> preempt <残り>` は True にし、行の原文(空白は1つ)を vrrp[].options の集合に足す。"
        "`no vrrp <VRID> preempt <残り>` は未解釈。認証の行・秘密の語がある行・子を持つ行は "
        "interfaces.vrrp_address と同じく未解釈",
    ),
    Pattern(
        "interfaces.vrrp_address_family", "interface", r"vrrp (?P<vrid>\d+) address-family (?P<family>ipv4|ipv6)",
        "vrrp[]", "vrrp 1 address-family ipv4", _vrrp_address_family,
        note="手続き。VRRPv3 のブロックの見出し(ADR-0125、REQ-170)。子を持つ行は文字列にしないが解釈済みにし、"
        "VRID のグループを作って、中の行を interfaces.vrrp_block_* で読む(中の行がすべて未解釈でもグループは作る)。"
        "子を持たない行は、行の原文(空白は1つ)を vrrp[].options の集合に足す",
    ),
    Pattern(
        "interfaces.vrrp_option", "interface", r"(?:no )?vrrp (?P<vrid>\d+) .+", "vrrp[].options",
        "vrrp 1 description UPLINK", _vrrp_option,
        note="手続き。上の VRRP のパターンに当たらない `vrrp <VRID> <残り>`・`no vrrp <VRID> <残り>` の行の原文"
        "(空白は1つ)を vrrp[].options の集合に足す。同じ文字列は重ねず、文字列の昇順(ADR-0125、REQ-170)。"
        "`vrrp <VRID>` だけの行(NX-OS のブロックの見出し)は当たらず、子も含めて未解釈。"
        "語に authentication がある行、語のどれかが password・secret・key の行と、語の中に鍵が埋まった"
        "(key, や password= などの)行は未解釈。子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.vrrp_block_address", _VRRP_SCOPE, r"(?P<no>no )?address(?: (?P<rest>.+))?",
        "vrrp[].virtual_addresses", "address 192.0.2.1", _vrrp_block_address,
        note="手続き。VRRPv3 の `address-family ipv4` のブロックの `address <IPv4>`(後ろに `primary`・`secondary` が"
        "付いても。語は持たない)を virtual_addresses の後ろに足す(ADR-0125、REQ-170)。ほかの形と `address-family ipv6` "
        "のブロックの行は、見出しと行(空白は1つ)を ` > ` でつないだ道すじを vrrp[].options の集合に足す。"
        "`no address …` は未解釈。道すじのどの段かに authentication の語・秘密の語がある行と、子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.vrrp_block_priority", _VRRP_SCOPE, r"(?P<no>no )?priority(?: (?P<rest>.+))?",
        "vrrp[].priority", "priority 110", _vrrp_block_priority,
        note="手続き。VRRPv3 の `address-family ipv4` のブロックの `priority <数>` を整数にして priority に写す。"
        "2回あれば後の行(ADR-0125、REQ-170)。ほかの形と `address-family ipv6` のブロックの行は道すじを "
        "vrrp[].options の集合に足す。`no priority …` は未解釈。認証の行・秘密の語がある行・子を持つ行は "
        "interfaces.vrrp_block_address と同じく未解釈",
    ),
    Pattern(
        "interfaces.vrrp_block_preempt", _VRRP_SCOPE, r"(?P<no>no )?preempt(?: (?P<rest>.+))?",
        "vrrp[].preempt", "preempt", _vrrp_block_preempt,
        note="手続き。VRRPv3 の `address-family ipv4` のブロックの `preempt` は True、`no preempt` は False にする。"
        "`preempt <残り>` は True にし、道すじを vrrp[].options の集合に足す(ADR-0125、REQ-170)。"
        "`address-family ipv6` のブロックの行は道すじを options に足す。`no preempt <残り>` は未解釈。"
        "認証の行・秘密の語がある行・子を持つ行は interfaces.vrrp_block_address と同じく未解釈",
    ),
    Pattern(
        "interfaces.vrrp_block_option", _VRRP_SCOPE, r".+", "vrrp[].options", "timers advertise 100",
        _vrrp_block_option,
        note="手続き。VRRPv3 のブロックの中の、上の3つに当たらない子を持たない行の道すじ(見出し > 行。空白は1つ)を "
        "vrrp[].options の集合に足す。同じ文字列は重ねず、文字列の昇順(ADR-0125、REQ-170)。"
        "道すじのどの段かに authentication の語、password・secret・key の語、語の中に埋まった鍵がある行と、"
        "子を持つ行は、子も含めて未解釈(同じブロックのほかの行は読む)",
    ),
    Pattern(
        "interfaces.port_security", "interface",
        r"(?:no )?(?:switchport port-security|switchport nonegotiate|storm-control|udld|ip dhcp snooping"
        r"|ip arp inspection|ip verify source|ip device tracking|device-tracking|loop-protect)(?: .+)?",
        "port_security", "storm-control broadcast level 1.00", _port_security,
        note="手続き。行の原文(空白は1つ)を port_security の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0068 決定3)。語の途中では切らない。語に password・secret・key がある行は未解釈。"
        "AOS-CX の `loop-protect`・`loop-protect <残り>`(`no ` 付きも)も読む(ADR-0210 決定9、REQ-294)。"
        "`spanning-tree guard …` は interfaces.spanning_tree が読む。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.tunnel", "interface", r"tunnel (?P<rest>.+)", "tunnel", "tunnel source GigabitEthernet0/0",
        _tunnel,
        note="手続き。`tunnel` の後ろ(空白は1つ)を tunnel の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0104、REQ-85)。語のどれかが password・secret・key の行(`tunnel key …` など)と、"
        "語の中に鍵が埋まった(key, や password= などの)行は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.dhcp_relay", "interface",
        r"(?:no )?(?:ip helper-address|ipv6 dhcp relay|ip dhcp relay|dhcp relay) .+", "dhcp_relay",
        "ip helper-address 192.0.2.10", _line_option("dhcp_relay"),
        note="手続き。行の原文(空白は1つ)を dhcp_relay の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0109 決定1)。後ろが無い形は未解釈。語のどれかが password・secret・key の行と、"
        "語の中に鍵が埋まった(key, や password= などの)行は未解釈。子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.ip_options", "interface",
        r"(?:no )?(?:ipv6 nd .+|(?:ip|ipv6) tcp adjust-mss .+|(?:ip|ipv6) redirects|ip (?:local-)?proxy-arp"
        r"|(?:ip|ipv6) unreachables|ip directed-broadcast|ip (?:route-cache|cef|split-horizon|virtual-reassembly)(?: .+)?"
        r"|dhcp client .+|ip attached-host .+|ip nat (?:inside|outside)|ip summary-address .+"
        r"|ip load-sharing .+|ip mtu \d+|ipv6 dhcp server .+"
        r"|ip nhrp (?!authentication(?: |$)).+|ip verify unicast .+|ipv6 dhcp client .+"
        r"|ipv4 mtu \d+|ipv4 unreachables disable|ipv4 redirects|ipv4 point-to-point|ipv4 directed-broadcast"
        r"|ipv4 verify .+|ipv4 flowspec .+)",
        "ip_options", "no ip redirects", _line_option("ip_options"),
        note="手続き。行の原文(空白は1つ)を ip_options の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0109 決定3・ADR-0111 決定6)。`ipv6 nd`・`tcp adjust-mss` は後ろの語が要り、`redirects`・`proxy-arp`・"
        "`unreachables`・`directed-broadcast` はその語だけの行、`route-cache`・`cef`・`split-horizon` はどちらも読む。"
        "Arista の `dhcp client …`・`ip attached-host …` も読む(後ろの語が要る。ADR-0115 決定4、REQ-160)。"
        "`ip virtual-reassembly` はその語だけの行も後ろの語がある行も読む(ADR-0116 決定3、REQ-161)。"
        "`ip nat inside`・`ip nat outside`(その語だけの行)と `ip summary-address …`(後ろの語が要る)も読む"
        "(ADR-0120、REQ-165)。"
        "`ip load-sharing …`(後ろの語が要る)と `ip mtu <数>`(L3 の MTU。mtu とは別の設定)も読む"
        "(ADR-0121、REQ-166)。"
        "`ipv6 dhcp server …`(後ろの語が要る)も読む(ADR-0123 決定2、REQ-168)。"
        "DMVPN の `ip nhrp …`、`ip verify unicast …`、`ipv6 dhcp client …`(no 付きも。後ろの語が要る)も読む"
        "(ADR-0136 決定2、REQ-182)。ただし `ip nhrp authentication …`(語の無い鍵)は当てず未解釈"
        "(ADR-0136 決定4)。"
        "IOS-XR の `ipv4 mtu <数>`・`ipv4 unreachables disable`・`ipv4 redirects`・`ipv4 point-to-point`・"
        "`ipv4 directed-broadcast`・`ipv4 verify …`・`ipv4 flowspec …`(no 付きも。後ろの語が要る形は語が要る)も読む"
        "(ADR-0211 決定5、REQ-296)。"
        "表に無い `ip`・`ipv6`・`ipv4` の行は未解釈。秘密の語がある行と子を持つ行は未解釈",
    ),
    Pattern(
        "interfaces.traffic_monitoring", "interface",
        r"(?:sflow|flow tracker) .+|(?:no )?(?:ip|ipv6) flow monitor .+", "traffic_monitoring",
        "sflow enable", _line_option("traffic_monitoring"),
        note="手続き。行の原文(空白は1つ)を traffic_monitoring の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0061、REQ-72)。Cisco の `ip flow monitor …`・`ipv6 flow monitor …`(no 付きも)も読む"
        "(ADR-0120、REQ-165)。後ろの語が要る。`no sflow …` は未解釈。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.multicast", "interface",
        r"(?:no )?(?:ip pim|ip igmp|ip multicast|ip mfib|ipv6 pim|ipv6 mld|ipv6 mfib|pim ipv4|pim ipv6) .+"
        r"|no pim .+|ip igmp",
        "multicast",
        "ip pim sparse-mode", _line_option("multicast"),
        note="手続き。行の原文(空白は1つ)を multicast の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0064、REQ-75)。Arista の `pim ipv4 …`・`pim ipv6 …`・`no pim …` も読む。"
        "`ip mfib …`・`ipv6 mfib …` も読む(ADR-0118、REQ-163)。後ろの語が要る。"
        "ただし後ろの無い `ip igmp` は読む(ADR-0133 決定5、REQ-178。`no ip igmp` は未解釈)。"
        "`ip ospf …`・`ipv6 address …` は書き出しの語が違うので当たらない。"
        "語のどれかが password・secret・key の行と、語の中に鍵が埋まった(key, や password= などの)行は未解釈。"
        "子を持つ行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.mpls", "interface", r"(?:no )?mpls .+", "mpls", "mpls ldp interface", _block_line("mpls"),
        note="手続き。行の原文(空白は1つ)を mpls の集合に足す。同じ文字列は重ねず、文字列の昇順(ADR-0108、REQ-89)。"
        "後ろの語が要る(`mpls` だけの行は未解釈)。子を持つ行は文字列にしないが解釈済みにし、中の行を "
        "interfaces.block_setting で読む。語のどれかが password・secret・key の行と、語の中に鍵が埋まった"
        "(key, や password= などの)行は、子も含めて未解釈",
    ),
    Pattern(
        "interfaces.ptp", "interface", r"(?:no )?ptp .+", "ptp", "ptp enable", _block_line("ptp"),
        note="手続き。行の原文(空白は1つ)を ptp の集合に足す。読み方は interfaces.mpls と同じ(ADR-0108、REQ-89)",
    ),
    Pattern(
        "interfaces.evpn", "interface", r"(?:no )?evpn .+", "evpn", "evpn ethernet-segment", _block_line("evpn"),
        note="手続き。EVPN のマルチホーム。行の原文(空白は1つ)を evpn の集合に足す。読み方は interfaces.mpls と同じ"
        "(ADR-0108、REQ-89)。`evpn ethernet-segment` の中の行は interfaces.block_setting が読む",
    ),
    Pattern(
        "interfaces.block_setting", _BLOCK_SCOPE, r".+", "mpls・ptp・evpn", "identifier 0011.0000.0000.0000.0001",
        _block_setting,
        note="手続き。子を持つ `mpls …`・`ptp …`・`evpn …` の中の子を持たない行を、見出しからの道すじを ` > ` で"
        "つないだ文字列にして、見出しと同じ集合に足す(見出しを含める。同じ文字列は重ねず、文字列の昇順。ADR-0108、"
        "REQ-89)。子を持つ行は文字列にしないが解釈済みにし、さらに中を読む。道すじのどの段かが、語のどれかが "
        "password・secret・key か、語の中に鍵が埋まった行なら、その行も子孫も未解釈(同じブロックのほかの行は読む)",
    ),
    Pattern(
        _COMMENT_PATTERN_ID, "interface", r"comment", "port_options", "comment", _comment,
        note="手続き。Arista のインターフェースの、子を持たない `comment`(後ろに語の無い行)を読んだら、同じ段の次の行から "
        "`EOF` の行までを本文として、表の外の手続きで読む。本文の各行(空白は1つ)を `comment > <行>` にして port_options "
        "の集合に足す(同じ文字列は重ねず、文字列の昇順。本文の並びは持たない)。`comment` の行と `EOF` の行は解釈済みにするが"
        "文字列にしない。本文はどの書式にも当てない(`mtu 9000` も本文として読む)。語のどれかが password・secret・key の"
        "本文の行と、語の中に鍵が埋まった(key, や password= などの)本文の行、子を持つ本文の行は、子も含めて未解釈"
        "(同じコメントのほかの行は読む。後ろの vxlan も読まない)。`EOF` が無ければインターフェースの終わりまでを本文とする。本文の行と `EOF` の行にも "
        "このパターンの印を付ける(出現ごとに記録し、同じ段の同じ文字列のコメントの外の行とは分ける。ADR-0133 決定2、REQ-178)。子を持つ `comment` と、後ろに語がある `comment …` は、"
        "子も含めて未解釈",
    ),
    Pattern(
        "interfaces.cts_manual", "interface", r"cts manual", "port_options", "cts manual", _cts_manual,
        note="手続き。`cts manual` を port_options の集合に足す。子を持てば、中の行を interfaces.cts_manual_setting で"
        "読む(ADR-0126 決定3、REQ-171)。子を持たない行も同じ値になる。interfaces.port_options より前に置く",
    ),
    Pattern(
        "interfaces.cts_manual_setting", _CTS_SCOPE, r"(?:no )?propagate sgt|policy static sgt \d+(?: trusted)?",
        "port_options", "propagate sgt", _cts_manual_setting,
        note="手続き。子を持つ `cts manual` の中の子を持たない行を、見出しからの道すじ(`cts manual > propagate sgt`)に"
        "して port_options の集合に足す(ADR-0126 決定3、REQ-171)。`sap …` の行(PMK の鍵)・ほかの形の行・"
        "子を持つ行とその子孫は未解釈(同じブロックのほかの行は読む)",
    ),
    Pattern(
        "interfaces.asa_port_options", "interface",
        r"nameif \S+|no nameif|security-level [0-9]+|no security-level|(?:no )?management-only|vlan [0-9]+",
        "port_options", "nameif outside", _asa_port_options,
        note=(
            "手続き。ASA と見立てた config だけ(ADR-0214 決定 5、REQ-304)。行の原文(空白は1つ)を port_options の集合に"
            "足す(同じ文字列は重ねず、文字列の昇順)。`vlan <数> secondary …` など語が続く `vlan`・子を持つ行・"
            "秘密の語がある行(`nameif key` など)は未解釈。ASA と見立てない config では未解釈。interfaces.port_options より前に置く"
        ),
    ),
    Pattern(
        "interfaces.aos_name", "interface", r"name (?P<text>.+)", "description", 'name "uplink"', _aos_name,
        note=(
            "手続き。HPE Aruba AOS-Switch と見立てた config だけ(ADR-0228 決定 3、REQ-332)。"
            "`name \"<名前>\"`・`name <名前>` の名前(前後の `\"` は外す)を description に写す。"
            "見立てていない config・子を持つ行は未解釈。interfaces.port_options より前に置く"
        ),
    ),
    Pattern(
        "interfaces.aos_disable", "interface", r"disable", "shutdown", "disable", _aos_disable,
        note=(
            "手続き。AOS-Switch と見立てた config だけ(ADR-0228 決定 3、REQ-332)。shutdown を真にする。"
            "見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_enable", "interface", r"enable", "shutdown", "enable", _aos_enable,
        note=(
            "手続き。AOS-Switch と見立てた config だけ(ADR-0228 決定 3、REQ-332)。shutdown を偽にする"
            "(後の行が勝つ)。見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_speed_duplex", "interface", r"speed-duplex (?P<value>\S+(?: \S+)*)", "speed",
        "speed-duplex auto-1000", _aos_speed_duplex,
        note=(
            "手続き。AOS-Switch と見立てた config だけ(ADR-0228 決定 3、REQ-332)。値の原文を speed に写す。"
            "見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_vlan_ip_address", _AOS_VLAN_SCOPE, r"ip address (?P<addr>\S+) (?P<mask>\S+)", "ip_address",
        "ip address 192.0.2.1 255.255.255.0", _aos_vlan_ip_address,
        note=(
            "手続き。AOS-Switch と見立てた config の、字下げの無い `vlan <1〜4094>` の塊の中だけ(ADR-0228 決定 3、REQ-332)。"
            "インターフェース `vlan<N>`(type は svi)の ip_address に写す。最初の行だけを読み、2 行目からは未解釈"
            "(アドレスを黙って上書きしない)。IPv4 として読めない行・子を持つ行も未解釈。"
            "塊の中に写せる行が 1 つでもあるときだけインターフェースを作り、同じ名前がすでにあれば作らず未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_vlan_address_dhcp", _AOS_VLAN_SCOPE, r"ip address dhcp-bootp", "address_mode",
        "ip address dhcp-bootp", _aos_vlan_address_dhcp,
        note=(
            "手続き。AOS-Switch の `vlan <数>` の塊の中だけ(ADR-0228 決定 3、REQ-332)。address_mode を `dhcp` にする"
            "(今の Cisco の `ip address dhcp` と同じ値)。子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_vlan_helper_address", _AOS_VLAN_SCOPE, r"ip helper-address \S+", "dhcp_relay",
        "ip helper-address 192.0.2.10", _aos_vlan_helper_address,
        note=(
            "手続き。AOS-Switch の `vlan <数>` の塊の中だけ(ADR-0228 決定 3、REQ-332)。行の原文(空白は1つ)を dhcp_relay の"
            "集合に足す(今の Cisco の `ip helper-address` と同じ持ち方。同じ文字列は重ねず昇順)。"
            "子を持つ行・秘密の語がある行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_vlan_ipv6_address", _AOS_VLAN_SCOPE,
        r"ipv6 address (?P<addr>[^\s/]+)/(?P<length>\d+)(?: (?P<kind>eui-64|anycast))?", "ipv6_addresses",
        "ipv6 address 2001:db8::1/64", _aos_vlan_ipv6_address,
        note=(
            "手続き。AOS-Switch の `vlan <数>` の塊の中だけ(ADR-0228 決定 3、REQ-332)。今の Cisco の `ipv6 address <A>/<n>` と"
            "同じ持ち方で ipv6_addresses の集合に足す。IPv6 として読めない行・子を持つ行は未解釈"
            "(`ipv6 address dhcp …` などほかの形も未解釈)"
        ),
    ),
    Pattern(
        "interfaces.aos_untagged_vlan", "interface", r"untagged vlan (?P<id>\d+)", "switchport.mode・switchport.access_vlan・"
        "switchport.native_vlan・switchport.trunk_allowed_vlan", "untagged vlan 30", _aos_untagged_vlan,
        note=(
            "手続き。HPE Aruba AOS-Switch と見立てた config だけ(ADR-0228 の追記、REQ-334)。`interface <ポート>` の塊の"
            "`untagged vlan <1〜4094>` を、そのポートの untagged の VLAN として集める。集めた所属は、AOS-Switch の `vlan <数>` の塊の"
            "`untagged`・`tagged` の行と合わせて、config の上からの順(後の行が勝つ)で、ポートの switchport に写す"
            "(tagged の集合が空なら mode は access・access_vlan は untagged の VLAN。空でなければ mode は trunk・native_vlan は"
            "untagged の VLAN(無ければ None)・trunk_allowed_vlan は tagged の集合と untagged の VLAN の和)。"
            "違う untagged の VLAN が 2 行以上あれば、後の行の値を使い、食い違いを記録する(値・行番号は持たない)。"
            "見立てていない config・範囲の外の数・子を持つ行は未解釈。interfaces.port_options より前に置く"
        ),
    ),
    Pattern(
        "interfaces.aos_tagged_vlan", "interface", r"tagged vlan (?P<ids>\S+)", "switchport.mode・switchport.native_vlan・"
        "switchport.trunk_allowed_vlan", "tagged vlan 40,50", _aos_tagged_vlan,
        note=(
            "手続き。AOS-Switch と見立てた config だけ(ADR-0228 の追記、REQ-334)。`interface <ポート>` の塊の "
            "`tagged vlan <並び>`(`<数>`・`<数>-<数>` を `,` でつないだもの。1〜4094 だけ)の各 VLAN を、そのポートの tagged の"
            "集合に足す。写し方は interfaces.aos_untagged_vlan と同じ。範囲の終わりが始まりより小さい並び・範囲の外の数・"
            "ほかの形・見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_vlan_untagged", _AOS_PORT_SCOPE, r"untagged (?P<ports>\S+)", "switchport.mode・switchport.access_vlan・"
        "switchport.native_vlan・switchport.trunk_allowed_vlan", "untagged 1-12", _aos_vlan_untagged,
        note=(
            "手続き。AOS-Switch と見立てた config の、字下げの無い `vlan <1〜4094>` の塊の中だけ(ADR-0228 の追記、REQ-334)。"
            "ポートの並び(`3`・`1-24`・`A1-A4`・`1/1-1/24`・`1/A1-1/A3`・`Trk1-Trk2` を `,` でつないだもの)の各ポートの "
            "untagged の VLAN を、塊の数にする。展開できない並び(範囲の終わりが始まりより小さい・英字や前の数が食い違う・"
            "端の数が 0 で始まる・空の項目・ほかの形・1 行で 1024 個を超える)・見立てていない config・子を持つ行は未解釈。"
            "この行は `vlan<N>` のインターフェースを作る手がかりに数えない。写し方は interfaces.aos_untagged_vlan と同じ"
        ),
    ),
    Pattern(
        "interfaces.aos_vlan_tagged", _AOS_PORT_SCOPE, r"tagged (?P<ports>\S+)", "switchport.mode・switchport.native_vlan・"
        "switchport.trunk_allowed_vlan", "tagged 23-24,A1", _aos_vlan_tagged,
        note=(
            "手続き。AOS-Switch と見立てた config の、字下げの無い `vlan <1〜4094>` の塊の中だけ(ADR-0228 の追記、REQ-334)。"
            "ポートの並びの各ポートの tagged の集合に、塊の数を足す。展開できない並び・見立てていない config・子を持つ行は"
            "未解釈。写し方は interfaces.aos_untagged_vlan と同じ"
        ),
    ),
    Pattern(
        "interfaces.aos_vlan_no_member", _AOS_PORT_SCOPE, r"no (?:untagged|tagged) (?P<ports>\S+)", "(読むだけ。IR は変えない)",
        "no untagged 13-24", _aos_vlan_no_member,
        note=(
            "手続き。AOS-Switch と見立てた config の、字下げの無い `vlan <1〜4094>` の塊の中だけ(ADR-0228 の追記、REQ-334)。"
            "ポートの並びが展開できれば解釈済みにするだけで、所属は変えない(running-config の `no untagged` は、既定の VLAN 1 から"
            "外したことを表し、所属から外すことは書かない)。展開できない並び・見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.aos_trunk", _AOS_TOP_SCOPE, r"trunk (?P<ports>\S+) trk(?P<id>\d+) (?P<mode>lacp|trunk|dt-lacp|dt-trunk)",
        "interfaces[ポート].channel_group", "trunk 23-24 trk1 lacp", _aos_trunk,
        note=(
            "手続き。AOS-Switch と見立てた config の、最上位の `trunk <並び> trk<数> <方式>` だけ(ADR-0228 の追記、REQ-334。"
            "`trk<数>` と方式の語は小文字のときだけ読む)。並びの各ポートの channel_group を `{\"id\": 数, \"mode\": 方式}` に"
            "する。インターフェース `Trk<数>` が無ければ作る(type は port_channel)。ポートのインターフェースも無ければ作る。"
            "同じポートが違う束ね先の 2 行以上にあれば、後の行の値を使い、食い違いを記録する(値・行番号は持たない)。"
            "展開できない並び・見立てていない config・子を持つ行は未解釈。`Trk<数>` に所属の行(`tagged Trk1` など)があれば、"
            "interfaces.aos_vlan_tagged などの写し方で `Trk<数>` の switchport に写す"
        ),
    ),
    Pattern(
        "interfaces.huawei_undo_shutdown", "interface", r"undo shutdown", "shutdown", "undo shutdown",
        _huawei_undo_shutdown,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3、REQ-336)。shutdown を偽にする(後の行が勝つ)。"
            "見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.huawei_undo_portswitch", "interface", r"undo portswitch", "type(routed の判定にだけ使う)",
        "undo portswitch", _huawei_undo_portswitch,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3、REQ-336)。IR のキーには写さず、type の判定"
            "(interfaces.type)で `no switchport` と同じ入力になる。見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.huawei_portswitch", "interface", r"portswitch", "type(値は変えない)", "portswitch",
        _huawei_portswitch,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3、REQ-336)。L2 のポートの明示。既定と同じなので IR の値は"
            "変えない(`switchport` と同じ扱い)。見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.huawei_ip_address_sub", "interface", r"ip address (?P<addr>\S+) (?P<mask>\S+) sub",
        "secondary_addresses", "ip address 198.51.100.1 255.255.255.0 sub", _huawei_ip_address_sub,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3、REQ-336)。今の Cisco の `ip address <A> <マスク> secondary` "
            "と同じ持ち方で secondary_addresses に足す。マスクは `255.255.255.0` の形でも長さ(`24`)でも読む。"
            "IPv4 として読めない行・見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.huawei_link_type", "interface", r"port link-type (?P<mode>access|trunk|dot1q-tunnel)",
        "switchport.mode", "port link-type access", _huawei_link_type,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3(2)、REQ-337)。方式の語を switchport.mode に写す。"
            "`port link-type hybrid` は IR に hybrid が無いので未解釈。見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.huawei_default_vlan", "interface", r"port default vlan (?P<id>\d+)", "switchport.access_vlan",
        "port default vlan 10", _huawei_default_vlan,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3(2)、REQ-337)。access_vlan を数にする(後の行が勝つ)。"
            "1〜4094 の外・見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.huawei_trunk_allow_pass", "interface", r"port trunk allow-pass vlan (?P<list>\d.*)",
        "switchport.trunk_allowed_vlan・switchport.trunk_allowed_none", "port trunk allow-pass vlan 10 20 to 22",
        _huawei_trunk_allow_pass,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3(2)、REQ-337)。並び(空白で区切った ID と "
            "`<A> to <B>`)を、今の一覧との和集合(昇順のタプル)にする。"
            "読めない並び・見立てていない config・子を持つ行は未解釈。読めたら trunk_allowed_none を偽にする(mode は変えない)。"
            "`port trunk allow-pass vlan all`(REQ-338 で未解釈に改めた)と `undo port trunk allow-pass vlan …` は未解釈のまま"
            "(VRP の trunk は行が無ければ VLAN 1 だけを通すので、`()`〈行が無い〉に写したり一覧から引いたりすると、"
            "`all` や `undo` の有る無しが差分に出なくなる)"
        ),
    ),
    Pattern(
        "interfaces.huawei_trunk_pvid", "interface", r"port trunk pvid vlan (?P<id>\d+)",
        "switchport.native_vlan・switchport.native_vlan_none・switchport.native_vlan_tagged",
        "port trunk pvid vlan 10", _huawei_trunk_pvid,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3(2)、REQ-337)。native_vlan を数にし、"
            "native_vlan_none・native_vlan_tagged を偽にする(後の行が勝つ)。1〜4094 の外・見立てていない config・"
            "子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.huawei_eth_trunk", "interface", r"eth-trunk (?P<id>\d+)", "channel_group", "eth-trunk 1",
        _huawei_eth_trunk,
        note=(
            "手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3(2)、REQ-337)。channel_group を "
            "`{id: 数, mode: eth-trunk}` にする(AOS-CX の `lag` と同じ持ち方。2回あれば後の行)。"
            "見立てていない config・子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "interfaces.port_options", "interface",
        r"(?:no )?(?:switchport block|switchport protected|switchport vepa|switchport private-vlan"
        r"|switchport trunk pruning|snmp trap|cdp|lldp|mvrp|macsec|mka|ethernet oam|autonomic|cts|bgp-policy"
        r"|arp|load-interval|carrier-delay|hold-queue|medium|tx-ring-limit|tx-queue-limit"
        r"|port-channel lacp|lacp|link tracking|link-debounce|mac security|mop|counter|keepalive|fair-queue)(?: .+)?"
        r"|(?:no )?(?:stackwise-virtual|switchport trunk encapsulation|flowcontrol|small-frame|l2 mtu|l2 mru"
        r"|node-segment|channel-protocol|dialer|dialer-group|pppoe|pppoe-client|clock rate|zone-member|pulse-time"
        r"|fabric forwarding|crypto map|logging event) .+"
        r"|(?:no )?(?:ppp (?!(?:chap hostname|pap sent-username|eap identity)(?: |$))"
        r"|glbp (?!(?:.* )?authentication(?: |$))|vpc (?!\d+$)).+"
        r"|(?:no )?clns route-cache"
        r"|(?:ipsec|ikev2) .+",
        "port_options", "no snmp trap link-status", _line_option("port_options"),
        note="手続き。行の原文(空白は1つ)を port_options の集合に足す。同じ文字列は重ねず、文字列の昇順"
        "(ADR-0110 決定1)。Arista の `port-channel lacp`・`lacp`・`link tracking`・`link-debounce`・`mac security` "
        "も読む(ADR-0115 決定3、REQ-160)。`mop` も読む(ADR-0116 決定3、REQ-161)。"
        "`stackwise-virtual …` も読む(後ろの語が要る。ADR-0120、REQ-165)。"
        "`counter` も読む(ADR-0121、REQ-166)。"
        "`clns route-cache`(と `no ` つき。その語だけの行)も読む(ADR-0124 決定2、REQ-169)。"
        "NEC IX の `ipsec …`・`ikev2 …`(`ipsec policy tunnel <マップ名> out`・`ikev2 outgoing-interface …` など。"
        "後ろの語が要る。no は付けない)も読む(ADR-0209 決定6、REQ-292)。"
        "`ikev2 peer … authentication psk …` のように psk の語がある行も入るが、AI に送る側が元の行と同じ文字列・"
        "同じ場所で判定して送らない。`key` などの has_secret の行は入れない。"
        "`switchport trunk encapsulation …`・`flowcontrol …`・`small-frame …`(後ろの語が要る)と `keepalive`(その語だけの行も"
        "後ろの語がある行も)も読む(ADR-0128 決定3、REQ-173。`switchport trunk allowed vlan add …` は当たらず未解釈)。"
        "Arista の `l2 mtu …`・`l2 mru …`・`node-segment …`(no 付きも。後ろの語が要る)も読む(ADR-0133 決定3、REQ-178。"
        "`l2 mtu` は L2 の値で、mtu(L3)とは別の設定)。"
        "Cisco の WAN と NX-OS の `channel-protocol …`・`dialer …`・`dialer-group …`・`ppp …`・`pppoe …`・`pppoe-client …`・"
        "`clock rate …`・`zone-member …`・`pulse-time …`・`fabric forwarding …`・`glbp …`・`crypto map …`・`vpc …`"
        "(no 付きも。後ろの語が要る)と `fair-queue`(その語だけの行も後ろの語がある行も)も読む(ADR-0136 決定2、REQ-182)。"
        "ただし `ppp chap hostname …`・`ppp pap sent-username …`・`ppp eap identity …`(認証の ID)と、"
        "`authentication` の語のある `glbp …`(語の無い鍵)は当てず未解釈(ADR-0136 決定4)。"
        "残りが数字だけの `vpc <数>` は当てない(interfaces.vpc が mlag_id に読む。ADR-0136 決定3、REQ-183)。"
        "語の途中では切らない(`cdpx` は当たらない)。今の項目と同じ設定の行"
        "(`switchport mode bogus` など)は当たらず未解釈(`channel-group auto` は "
        "interfaces.channel_group_auto が読む)。"
        "Cisco の `logging event …`(`logging event trunk-status`・`no logging event link-status` など。"
        "後ろの語が要る)も読む(ADR-0217 決定1、REQ-310)。字下げ1つの ` logging event link-status`・"
        "` logging event subif-link-status` はライブラリが木にする前に消すので、ここには来ない。"
        "インターフェースのほかのパターンすべての後ろに置く。秘密の語がある行と子を持つ行は、子も含めて未解釈"
        "(`cts manual` は interfaces.cts_manual が読む)",
    ),
    Pattern(
        "interfaces.type", "interface", None, "type(行は読まない。派生値)",
        "interface Loopback0", _derive_type,
        note=(
            "手続き。名前の先頭(Vlan → svi / Loopback・loopback → loopback / "
            "Port-channel・Port-Channel・port-channel → port_channel。大文字小文字は区別する)、"
            "AOS-CX の名前(`lag <数>` と、後ろに空白と語が続く `lag <数> …` → port_channel、`vlan <数>`・`vlan<数>` → svi。"
            "`lagX`・`vlan10a`・`vlan abc` は当たらない。ADR-0210 決定2、REQ-294)、"
            "Huawei と見立てた config の `LoopBack` → loopback・`Eth-Trunk` → port_channel・`Vbdif` → svi(大文字小文字は区別する。"
            "`Vlanif` は `Vlan` で svi。ADR-0231 決定 3、REQ-336)、"
            "AOS-Switch と見立てた config の `Trk<数>` → port_channel(大文字小文字は区別する。ADR-0228 決定 3、REQ-332)、"
            "ASA と見立てた config で、塊の中に switchport で始まる行(no switchport を含む)が無ければ routed"
            "(ADR-0214 決定 5、REQ-304。ASA 5505・Firepower 1010 のポートは switchport の行があるので次へ進む)、"
            "次に no switchport、routing(AOS-CX。no routing で戻る)、ip address、address_mode のどれかがあれば routed、"
            "それ以外は switchport"
        ),
    ),
)

_TYPE = next(p for p in PATTERNS if p.id == "interfaces.type")


def _read_block(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """子を持つ `mpls …`・`ptp …`・`evpn …` の中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, _BLOCK_SCOPE, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(child, ctx.path, ctx, interpreted)


def _read_vrrp_block(node: HConfigChild, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """子を持つ `vrrp <VRID> address-family ipv4|ipv6` の中の行を出現順に読む。子を持つ行は、子も含めて読まない。"""
    heading = " ".join(node.text.split())
    for child in node.children:
        ctx.path = [heading, " ".join(child.text.split())]
        ctx.has_children = bool(child.children)
        if (pattern := apply_first(PATTERNS, _VRRP_SCOPE, child.text, ctx)) is not None:
            interpreted.mark(child, pattern.id)


def _read_hsrp_block(node: HConfigChild, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """子を持つ NX-OS の `hsrp <数> [<残り>]` の中の行を出現順に読む。子を持つ行は、子も含めて読まない(REQ-183)。"""
    heading = " ".join(node.text.split())
    for child in node.children:
        ctx.path = [heading, " ".join(child.text.split())]
        ctx.has_children = bool(child.children)
        if (pattern := apply_first(PATTERNS, _HSRP_SCOPE, child.text, ctx)) is not None:
            interpreted.mark(child, pattern.id)


def _read_cts_manual_block(node: HConfigChild, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """子を持つ `cts manual` の中の行を出現順に読む。子を持つ行は、子も含めて読まない(ADR-0126 決定3)。"""
    for child in node.children:
        ctx.path = [_CTS_MANUAL, " ".join(child.text.split())]
        ctx.has_children = bool(child.children)
        if (pattern := apply_first(PATTERNS, _CTS_SCOPE, child.text, ctx)) is not None:
            interpreted.mark(child, pattern.id)


def _aos_vlan_number(text: str) -> int | None:
    """字下げの無い `vlan <1〜4094>`(1 つの数)の見出しの数。そうでなければ None。表を通さない正規表現は使えないので、文字列の操作で見る。"""
    words = text.split()
    if len(words) != 2 or words[0] != "vlan" or not (words[1].isascii() and words[1].isdigit()):
        return None
    number = int(words[1])
    return number if 1 <= number <= 4094 else None


def _read_aos_vlan(
    block: HConfigChild, number: int, interfaces: dict[str, Interface], interpreted: InterpretedLines
) -> None:
    """AOS-Switch の `vlan <数>` の塊の子を読み、写せた行が 1 つでもあれば、インターフェース `vlan<N>`(svi)を作る。

    同じ名前のインターフェースがすでにあれば、作らず、行も読まない(未解釈に残す。上書きしない)。
    """
    name = f"vlan{number}"
    if name in interfaces:
        return
    ctx = _Ctx(aos_switch=True)
    _start(ctx, name)
    matched: list[tuple[HConfigChild, Pattern]] = []
    for line in block.children:
        ctx.has_children = bool(line.children)
        if (pattern := apply_first(PATTERNS, _AOS_VLAN_SCOPE, line.text, ctx)) is not None:
            matched.append((line, pattern))
    if not matched:
        return
    for line, pattern in matched:
        interpreted.mark(line, pattern.id)
    _TYPE.apply(None, ctx)
    interfaces[name] = ctx.interface


def _aos_interface(interfaces: dict[str, Interface], name: str) -> Interface:
    """名前のインターフェースを返す。無ければ `interface` の塊と同じ既定値で作る(type は `Trk<数>` が port_channel、ほかは switchport)。"""
    if name not in interfaces:
        ctx = _Ctx(aos_switch=True)
        _start(ctx, name)
        _TYPE.apply(None, ctx)
        interfaces[name] = ctx.interface
    return interfaces[name]


def _read_aos_ports(tree: HConfig, interpreted: InterpretedLines, ports: AosPorts) -> None:
    """最上位の塊を上から順に読み、`vlan <数>` の塊の `untagged`・`tagged`・`no …` の行と、子を持たない最上位の `trunk …` の行を集める。

    `vlan<N>` のインターフェースがすでにあるかどうかとは関係なく読む(REQ-334)。当たった行は解釈済みにする。
    """
    ctx = _Ctx(aos_switch=True, aos_ports=ports)
    for index, block in enumerate(tree.children):
        number = _aos_vlan_number(block.text)
        if number is not None:
            ctx.vlan = number
            for position, line in enumerate(block.children):
                ctx.order = (index, position)
                ctx.has_children = bool(line.children)
                if (pattern := apply_first(PATTERNS, _AOS_PORT_SCOPE, line.text, ctx)) is not None:
                    interpreted.mark(line, pattern.id)
            ctx.vlan = None
        elif not block.children:
            ctx.order = (index, 0)
            ctx.has_children = False
            if (pattern := apply_first(PATTERNS, _AOS_TOP_SCOPE, block.text, ctx)) is not None:
                interpreted.mark(block, pattern.id)


def _write_aos_ports(
    ports: AosPorts, interfaces: dict[str, Interface], interpreted: InterpretedLines
) -> None:
    """集めた所属と trunk をポートの switchport・channel_group に写し、食い違いを interpreted.conflicts に足す(REQ-334)。

    所属の行があるポートは、tagged の集合が空なら mode access・access_vlan が untagged の VLAN、空でなければ mode trunk・
    native_vlan が untagged の VLAN(無ければ None)・trunk_allowed_vlan が tagged の集合と untagged の VLAN の和(昇順)。
    書くのはこの 4 つだけ。インターフェースが無いポートと `Trk<数>` は作る。
    """
    resolved = ports.resolve()
    for name, state in resolved.ports.items():
        if not state.member and state.group is None:
            continue
        interface = _aos_interface(interfaces, name)
        if state.member:
            switchport = interface["switchport"]
            if not state.tagged:
                switchport["mode"] = "access"
                switchport["access_vlan"] = state.untagged
            else:
                switchport["mode"] = "trunk"
                switchport["native_vlan"] = state.untagged
                allowed = state.tagged | ({state.untagged} if state.untagged is not None else set())
                switchport["trunk_allowed_vlan"] = tuple(sorted(allowed))
        if state.group is not None:
            interface["channel_group"] = {"id": state.group[0], "mode": state.group[1]}
            _aos_interface(interfaces, f"Trk{state.group[0]}")
    interpreted.conflicts.extend(resolved.conflicts)


def extract_interfaces(
    tree: HConfig,
    interpreted: InterpretedLines,
    *,
    awplus: bool = False,
    asa: bool = False,
    aos_switch: bool = False,
    huawei: bool = False,
) -> dict[str, Interface]:
    """tree のトップレベルの interface 行から、名前をキーにした Interface の辞書を返す。

    awplus は、AW+ と見立てた config か(トランクの `add`・`remove` の読み方が変わる。ADR-0206 決定1、REQ-289)。
    asa は、Cisco ASA と見立てた config か(nameif などを読み、type の決まりが変わる。ADR-0214 決定 5、REQ-304)。
    aos_switch は、HPE Aruba AOS-Switch と見立てた config か(インターフェースの `name`・`disable`・`speed-duplex` を読み、
    `Trk<数>` を port_channel にする。字下げの無い `vlan <数>` の塊の中のアドレスなどを、インターフェース `vlan<N>` に写す。
    `interface` の塊を先にすべて読み、`vlan<N>` は同じ名前が無いときだけ作る。ADR-0228 決定 3、REQ-332)。
    見立てたときは、さらに VLAN の所属(`untagged`・`tagged`)と最上位の `trunk` を集め、ポートの switchport・channel_group に写す
    (無いポートと `Trk<数>` は作る)。同じ項目に違う値が書かれていたら、後の行の値を使い、interpreted.conflicts に記録する。
    ADR-0228 の追記、REQ-334。
    huawei は、Huawei VRP と見立てた config か(`undo shutdown`・`undo portswitch`・`portswitch`・`ip address … sub` を読み、
    `LoopBack…`・`Eth-Trunk…`・`Vbdif…` の type を決める。ADR-0231 決定 3、REQ-336)。
    さらに、ポートの L2(`port link-type`・`port default vlan`・`port trunk allow-pass vlan`・`port trunk pvid vlan`・
    `eth-trunk <N>`)を switchport・channel_group に写す(ADR-0231 決定 3(2)、REQ-337)。
    """
    interfaces: dict[str, Interface] = {}
    aos_ports = AosPorts() if aos_switch else None
    for block_index, block in enumerate(tree.children):
        ctx = _Ctx(awplus=awplus, asa=asa, aos_switch=aos_switch, aos_ports=aos_ports, huawei=huawei)
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)

        for line_index, line in enumerate(block.children):
            if ctx.comment:
                ctx.comment = _read_comment_line(line, ctx, interpreted)
                continue
            ctx.has_children = bool(line.children)
            ctx.order = (block_index, line_index)
            words = line.text.split()
            if words[:1] == ["switchport"] or words[:2] == ["no", "switchport"]:
                ctx.switchport_line = True
            ctx.block_key = None
            ctx.vrrp_group = None
            ctx.cts_manual = False
            ctx.hsrp_prefix = None
            if (pattern := apply_first(PATTERNS, "interface", line.text, ctx)) is not None:
                interpreted.mark(line, pattern.id)
                if ctx.block_key is not None:
                    _read_block(line, [" ".join(line.text.split())], ctx, interpreted)
                if ctx.vrrp_group is not None:
                    _read_vrrp_block(line, ctx, interpreted)
                if ctx.cts_manual:
                    _read_cts_manual_block(line, ctx, interpreted)
                if ctx.hsrp_prefix is not None:
                    _read_hsrp_block(line, ctx, interpreted)

        _TYPE.apply(None, ctx)
        interfaces[ctx.name] = ctx.interface
    if aos_ports is not None:
        for block in tree.children:
            number = _aos_vlan_number(block.text)
            if number is not None:
                _read_aos_vlan(block, number, interfaces, interpreted)
        _read_aos_ports(tree, interpreted, aos_ports)
        _write_aos_ports(aos_ports, interfaces, interpreted)
    return interfaces
