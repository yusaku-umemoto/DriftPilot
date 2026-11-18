"""config テキストを DriftPilot 共通の IR に変換する(ADR-0009)。

hier_config はツリー化と単体ヘルパーにだけ使い、Config View は使わない。
"""

from hier_config import HConfig

from driftpilot.config_parser.acls import extract_acls
from driftpilot.config_parser.aos_switch import prepare_aos_switch
from driftpilot.config_parser.asa import prepare_asa
from driftpilot.config_parser.huawei import prepare_huawei
from driftpilot.config_parser.asa_nat import extract_asa_nat
from driftpilot.config_parser.awplus import looks_like_awplus, split_port_ranges
from driftpilot.config_parser.bfd import extract_bfd
from driftpilot.config_parser.bgp import extract_bgp
from driftpilot.config_parser.eigrp import extract_eigrp
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree, build_flat_tree
from driftpilot.config_parser.fortinet_tree import FortinetStructureError
from driftpilot.config_parser.global_ import extract_global
from driftpilot.config_parser.indentation import repair_indentation
from driftpilot.config_parser.interfaces import extract_interfaces
from driftpilot.config_parser.ipsec import extract_ipsec
from driftpilot.config_parser.ir import (
    IR,
    Aaa,
    Acl,
    AclEntry,
    Bfd,
    Bgp,
    BgpAddressFamily,
    BgpAfNeighbor,
    BgpEvpnInstance,
    BgpNeighbor,
    BgpVrf,
    Certificate,
    CertificateChain,
    ChannelGroup,
    Eigrp,
    Global,
    Interface,
    InterfaceType,
    Ipsec,
    IpsecBlock,
    Isis,
    IsisAddressFamily,
    Line,
    LineType,
    Mlag,
    Mpls,
    Multicast,
    Ospf,
    Ospfv3,
    OspfNetwork,
    PrefixList,
    PrefixListEntry,
    Ptp,
    Qos,
    QosClassMap,
    QosPolicyMap,
    QosTableMap,
    RouteList,
    RouteLists,
    RouteMap,
    RouteMapEntry,
    SdwanBlock,
    Snmp,
    SnmpCommunity,
    SnmpHost,
    SpanningTree,
    StaticRoute,
    Switchport,
    SwitchportMode,
    TrafficMonitoring,
    User,
    Vlan,
    Vrf,
    Vrrp,
    Vxlan,
    new_ir,
)
from driftpilot.config_parser.iosxr import prepare_iosxr
from driftpilot.config_parser.isis import extract_isis
from driftpilot.config_parser.lines import extract_lines
from driftpilot.config_parser.mlag import extract_mlag
from driftpilot.config_parser.mpls_ptp import extract_mpls_ptp
from driftpilot.config_parser.multicast import extract_multicast
from driftpilot.config_parser.ospf import extract_ospf, extract_ospfv3
from driftpilot.config_parser.platforms import FlatPlatform, UnsupportedConfigPlatform, flat_platform
from driftpilot.config_parser.qos import extract_qos
from driftpilot.config_parser.route_lists import extract_route_lists
from driftpilot.config_parser.route_policy import extract_route_policy
from driftpilot.config_parser.sdwan import extract_sdwan
from driftpilot.config_parser.spanning_tree import extract_spanning_tree
from driftpilot.config_parser.static_routes import extract_static_routes
from driftpilot.config_parser.tracking import FieldConflict, InterpretedLines
from driftpilot.config_parser.traffic_monitoring import extract_traffic_monitoring
from driftpilot.config_parser.tree import build_tree
from driftpilot.config_parser.vlans import extract_vlans
from driftpilot.config_parser.vrfs import extract_vrfs
from driftpilot.config_parser.vxlan import extract_vxlan

__all__ = [
    "IR",
    "Aaa",
    "Acl",
    "AclEntry",
    "Bfd",
    "Bgp",
    "BgpAddressFamily",
    "BgpAfNeighbor",
    "BgpEvpnInstance",
    "BgpNeighbor",
    "BgpVrf",
    "Certificate",
    "CertificateChain",
    "ChannelGroup",
    "DuplicateLine",
    "Eigrp",
    "FieldConflict",
    "FlatNode",
    "FlatTree",
    "FortinetStructureError",
    "Global",
    "Interface",
    "InterfaceType",
    "InterpretedLines",
    "Ipsec",
    "IpsecBlock",
    "Isis",
    "IsisAddressFamily",
    "Line",
    "LineType",
    "Mlag",
    "Mpls",
    "Multicast",
    "Ospf",
    "OspfNetwork",
    "Ospfv3",
    "PrefixList",
    "PrefixListEntry",
    "Ptp",
    "Qos",
    "QosClassMap",
    "QosPolicyMap",
    "QosTableMap",
    "RouteList",
    "RouteLists",
    "RouteMap",
    "RouteMapEntry",
    "SdwanBlock",
    "Snmp",
    "SnmpCommunity",
    "SnmpHost",
    "SpanningTree",
    "StaticRoute",
    "Switchport",
    "SwitchportMode",
    "TrafficMonitoring",
    "UnsupportedConfigPlatform",
    "User",
    "Vlan",
    "Vrf",
    "Vrrp",
    "Vxlan",
    "field_conflicts",
    "parse_config",
    "parse_config_traced",
    "parse_config_with_tree",
    "top_level_merged_lines",
]


def parse_config(text: str, platform: str | None = None) -> IR:
    """config テキストを IR に変換する。既定は Cisco IOS / IOS-XE(A型)。

    platform は既定でないパーサーの系統を選ぶ(fujitsu_sir は B型、fortinet は D型。config_parser/platforms.py)。
    D型で config / end、edit / next の対応が取れなければ FortinetStructureError(ValueError の派生)。
    どの extractor も解釈しなかった行は、ルートからのパスのタプルとして unparsed に入る。
    """
    return parse_config_with_tree(text, platform)[0]


def parse_config_with_tree(text: str, platform: str | None = None) -> tuple[IR, HConfig | FlatTree]:
    """parse_config と同じ IR に加えて、その元になったツリーも返す(diff_engine の安全網が使う)。"""
    ir, tree, _ = parse_config_traced(text, platform)
    return ir, tree


def parse_config_traced(text: str, platform: str | None = None) -> tuple[IR, HConfig | FlatTree, InterpretedLines]:
    """parse_config_with_tree に加えて、行ごとの解釈の記録も返す(被覆率の測定が使う)。"""
    flat = flat_platform(platform)
    if flat is not None:
        return _parse_flat_traced(text, flat)
    # AW+ と見立てたときだけ、木にする前にポートの範囲を1ポートずつに分ける(ADR-0206 決定1・5、REQ-289)
    awplus = looks_like_awplus(text)
    # XR の閉じの語の対を見て閉じの語の行を消し、XR と見立てたときは最上位の同じ見出しの塊をまとめる(ADR-0211、REQ-296)。
    # XR でなく ASA と見立てたときは、`:` の行・1 行ずつのバナー・access-list・最上位の塊を直す(ADR-0214、REQ-303)
    # どちらでもなければ、AOS-Switch と見立てたときだけ、`;` の行・1 行のバナー・`exit` の行を直す(ADR-0228、REQ-332)
    # どれでもなければ、Huawei VRP と見立てたときだけ、`#` の塊ごとの字下げ・`[V…]` の行・`return` を直し、
    # `sysname` と `undo shutdown` などを読む(ADR-0231 決定 1・2・3、REQ-336)
    prepared, asa, _, aos_switch, huawei = _prepare_a_type(text)
    # Huawei と見立てたときは、`banner ` で始まる行(`banner enable` など)をバナーの始まりとして扱わない
    # (ADR-0231 の追記、REQ-343。ライブラリが最後までバナーとみなして止まるのを避ける)
    tree = build_tree(split_port_ranges(prepared) if awplus else prepared, banners=not huawei)
    repair_indentation(tree)
    interpreted = InterpretedLines()

    ir = new_ir()
    ir["interfaces"] = extract_interfaces(
        tree, interpreted, awplus=awplus, asa=asa, aos_switch=aos_switch, huawei=huawei
    )
    # Huawei と見立てたときは `vlan batch` を読み、`name` は後の行が勝つ(ADR-0231 決定 3(2)、REQ-337)
    ir["vlans"] = extract_vlans(tree, interpreted, aos_switch=aos_switch, huawei=huawei)
    ir["acls"], ir["ipv6_acls"], ir["mac_acls"] = extract_acls(tree, interpreted, asa=asa)
    # Huawei と見立てたときは最上位の `ospf <番号>` の塊も読む(ADR-0231 決定 3(3)、REQ-338)
    ir["ospf"] = extract_ospf(tree, interpreted, huawei=huawei)
    ir["ospfv3"] = extract_ospfv3(tree, interpreted)
    ir["bgp"] = extract_bgp(tree, interpreted)
    ir["spanning_tree"] = extract_spanning_tree(tree, interpreted)
    ir["vrfs"] = extract_vrfs(tree, interpreted)
    # Huawei と見立てたときは `ip route-static`・`ipv6 route-static` も読む(REQ-338)
    ir["static_routes"], route_vendor = extract_static_routes(tree, interpreted, asa=asa, huawei=huawei)
    if route_vendor:
        vendor = "arista_eos" if platform == "arista_eos" else "cisco"
        ir["vendor_specific"][vendor] = {"static_routes": route_vendor}
    ir["lines"], line_vendor = extract_lines(tree, interpreted)
    if line_vendor:
        ir["vendor_specific"].setdefault("cisco", {})["lines"] = line_vendor
    ir["global"] = extract_global(tree, interpreted, asa=asa, huawei=huawei)
    ir["qos"] = extract_qos(tree, interpreted)
    ir["eigrp"] = extract_eigrp(tree, interpreted)
    ir["ipsec"] = extract_ipsec(tree, interpreted)
    ir["route_maps"], ir["prefix_lists"], ir["ipv6_prefix_lists"] = extract_route_policy(tree, interpreted)
    ir["vxlan"] = extract_vxlan(tree, interpreted)
    ir["mlag"] = extract_mlag(tree, interpreted)
    ir["isis"] = extract_isis(tree, interpreted)
    ir["bfd"] = extract_bfd(tree, interpreted)
    ir["route_lists"] = extract_route_lists(tree, interpreted)
    ir["traffic_monitoring"] = extract_traffic_monitoring(tree, interpreted)
    ir["multicast"] = extract_multicast(tree, interpreted)
    ir["sdwan"] = extract_sdwan(tree, interpreted)
    ir["mpls"], ir["ptp"] = extract_mpls_ptp(tree, interpreted)
    if asa:
        # 手動の NAT(最上位の `nat (…) …`)。ASA と見立てた config だけ、行が無くても空の一覧を置く(REQ-303)
        ir["vendor_specific"]["cisco_asa"] = {"nat": extract_asa_nat(tree, interpreted)}
    ir["unparsed"] = interpreted.unparsed(tree)
    return ir, tree, interpreted


def _prepare_a_type(text: str) -> tuple[str, bool, tuple[int, ...], bool, bool]:
    """A型の、木にする前の前処理。(木にする文字列, ASA と見立てたか, まとめて消した見出しの行番号, AOS-Switch と見立てたか,
    Huawei と見立てたか) を返す。

    XR(prepare_iosxr)を先に当て、XR と見立てなければ ASA(prepare_asa)を当て、どちらでもなければ AOS-Switch
    (prepare_aos_switch。ADR-0228、REQ-332)を当て、それでもなければ Huawei(prepare_huawei。ADR-0231、REQ-336)を当てる。
    前のどれかに見立てた config は、後ろの機種と見立てない。
    parse_config_traced と top_level_merged_lines が同じ関数を使うので、parse と警告の行番号は食い違わない。
    """
    prep = prepare_iosxr(text)
    if prep.iosxr:
        return prep.text, False, prep.merged_lines, False, False
    asa = prepare_asa(prep.text)
    if asa.asa:
        return asa.text, True, asa.merged_lines, False, False
    aos = prepare_aos_switch(asa.text)
    if aos.aos_switch:
        return aos.text, False, (), True, False
    huawei = prepare_huawei(aos.text)
    return huawei.text, False, (), False, huawei.huawei


def top_level_merged_lines(text: str, platform: str | None = None) -> tuple[int, ...]:
    """XR か ASA と見立てた config で、最上位の同じ見出しとしてまとめて消した行の行番号(text の1始まり。昇順)。

    parse_config_traced と同じ前処理(_prepare_a_type)の結果を返すので、parse と食い違わない。
    平坦な機種(B型・D型)と、まとめなかった config は空。diff_engine の警告 top_level_merged が使う(ADR-0211 決定4)。
    ASA は、子が全部 `nat ` で始まる `object network <名前>` の開き直しを数えない(ADR-0214 決定3)。
    """
    if flat_platform(platform) is not None:
        return ()
    return _prepare_a_type(text)[2]


def field_conflicts(text: str, platform: str | None = None) -> tuple[FieldConflict, ...]:
    """同じ項目に違う値が書かれていた記録(AOS-Switch のポートの VLAN の所属と trunk。ADR-0228 の追記、REQ-334)。

    parse_config_traced と同じ読み方の結果の記録を返すので、IR と警告は食い違わない。平坦な機種(B型・D型)と、AOS-Switch と
    見立てない config は空(軽い道: 見立てなければ読み直さない)。値と行番号は持たない。diff_engine の警告 duplicate_line が使う。
    """
    if flat_platform(platform) is not None:
        return ()
    if not _prepare_a_type(text)[3]:
        return ()
    return tuple(parse_config_traced(text, platform)[2].conflicts)


def _parse_flat_traced(text: str, flat: FlatPlatform) -> tuple[IR, FlatTree, InterpretedLines]:
    """B型・D型。木を組み、interfaces・static_routes・bgp・ospf・ipsec・sdwan・route_maps・prefix_lists・ipv6_prefix_lists・
    global を写す(REQ-21 §9、REQ-22 §7、REQ-30、REQ-47、REQ-192、REQ-193、REQ-194、REQ-195、REQ-196)。ospf・ipsec・sdwan・
    route_maps・prefix_lists・ipv6_prefix_lists・global(hostname・dns・ntp_options)は D型(FortiGate)だけが写す。
    B型(Si-R・ヤマハ)は ospf・sdwan・route_maps・prefix_lists・ipv6_prefix_lists がいつも空の辞書、ipsec がいつも None、global がいつも
    new_global()。Junos は bgp・route_maps・prefix_lists・acls も写す(REQ-325)。acls は Junos だけが写し、ほかの機種ではいつも空の辞書。
    vlans は D型の FortiSwitch(最上位の `config switch vlan`。REQ-299)と、B型の F5 BIG-IP(`net vlan` の `tag`。REQ-350)だけが写す。
    ほかの B型と FortiGate の config ではいつも空の辞書。
    値の食い違いは tree.warnings に足す。

    B型は行頭スコープ表で、D型は機種の build(終端記号の対)で木を組む。ヤマハ(yamaha。B型の系統)も機種の build
    (`pp select`・`tunnel select` の範囲。yamaha_tree)で木を組み、インターフェースの行を interfaces に、
    `ip route … gateway …` を static_routes に写す(REQ-301)。写さなかった行は範囲ごとの行の集合として
    vendor_specific に写す(REQ-300)。bgp などはいつも空。
    """
    tree = flat.build(text) if flat.build is not None else build_flat_tree(text, flat.scopes, flat.name)
    interpreted = InterpretedLines()

    ir = new_ir()
    (
        ir["interfaces"], ir["static_routes"], ir["bgp"], ir["ospf"], ir["ipsec"], ir["sdwan"], ir["route_maps"],
        ir["prefix_lists"], ir["ipv6_prefix_lists"], ir["global"], ir["vlans"], ir["acls"], vendor, conflicts,
    ) = flat.extract(tree, interpreted)
    ir["vendor_specific"] = {flat.name: vendor}
    tree.add_duplicates(conflicts)
    ir["unparsed"] = interpreted.unparsed(tree)
    return ir, tree, interpreted
