"""config テキストを DriftPilot 共通の IR に変換する(ADR-0009)。

hier_config はツリー化と単体ヘルパーにだけ使い、Config View は使わない。
"""

from hier_config import HConfig

from driftpilot.config_parser.acls import extract_acls
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
from driftpilot.config_parser.tracking import InterpretedLines
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
    "parse_config",
    "parse_config_traced",
    "parse_config_with_tree",
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
    tree = build_tree(text)
    repair_indentation(tree)
    interpreted = InterpretedLines()

    ir = new_ir()
    ir["interfaces"] = extract_interfaces(tree, interpreted)
    ir["vlans"] = extract_vlans(tree, interpreted)
    ir["acls"], ir["ipv6_acls"], ir["mac_acls"] = extract_acls(tree, interpreted)
    ir["ospf"] = extract_ospf(tree, interpreted)
    ir["ospfv3"] = extract_ospfv3(tree, interpreted)
    ir["bgp"] = extract_bgp(tree, interpreted)
    ir["spanning_tree"] = extract_spanning_tree(tree, interpreted)
    ir["vrfs"] = extract_vrfs(tree, interpreted)
    ir["static_routes"], route_vendor = extract_static_routes(tree, interpreted)
    if route_vendor:
        vendor = "arista_eos" if platform == "arista_eos" else "cisco"
        ir["vendor_specific"][vendor] = {"static_routes": route_vendor}
    ir["lines"], line_vendor = extract_lines(tree, interpreted)
    if line_vendor:
        ir["vendor_specific"].setdefault("cisco", {})["lines"] = line_vendor
    ir["global"] = extract_global(tree, interpreted)
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
    ir["unparsed"] = interpreted.unparsed(tree)
    return ir, tree, interpreted


def _parse_flat_traced(text: str, flat: FlatPlatform) -> tuple[IR, FlatTree, InterpretedLines]:
    """B型・D型。木を組み、interfaces・static_routes・bgp・ospf・ipsec・sdwan・route_maps・prefix_lists・ipv6_prefix_lists・
    global を写す(REQ-21 §9、REQ-22 §7、REQ-30、REQ-47、REQ-192、REQ-193、REQ-194、REQ-195、REQ-196)。ospf・ipsec・sdwan・
    route_maps・prefix_lists・ipv6_prefix_lists・global(hostname・dns・ntp_options)は D型(FortiGate)だけが写す。
    B型は ospf・sdwan・route_maps・prefix_lists・ipv6_prefix_lists がいつも空の辞書、ipsec がいつも None、global がいつも
    new_global()。
    値の食い違いは tree.warnings に足す。

    B型は行頭スコープ表で、D型は機種の build(終端記号の対)で木を組む。
    """
    tree = flat.build(text) if flat.build is not None else build_flat_tree(text, flat.scopes, flat.name)
    interpreted = InterpretedLines()

    ir = new_ir()
    (
        ir["interfaces"], ir["static_routes"], ir["bgp"], ir["ospf"], ir["ipsec"], ir["sdwan"], ir["route_maps"],
        ir["prefix_lists"], ir["ipv6_prefix_lists"], ir["global"], vendor, conflicts,
    ) = flat.extract(tree, interpreted)
    ir["vendor_specific"] = {flat.name: vendor}
    tree.add_duplicates(conflicts)
    ir["unparsed"] = interpreted.unparsed(tree)
    return ir, tree, interpreted
