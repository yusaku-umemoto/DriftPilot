"""2つの IR を比べて、意味のある差分の一覧を返す(structured 比較、ADR-0007・ADR-0009)。

IR 以外は見ない純粋関数。辞書のキーの並びや、順序に意味のないリストの並びは無視する。
順序に意味のある ACL のエントリは、順序の違いも差分にする。
"""

import copy
from collections import Counter
from typing import Any

from driftpilot.config_parser.ir import IR
from driftpilot.diff_engine.types import Diff, DiffOp

# 順序に意味がなく、要素の集合として比べるリストの項目名
_SET_LISTS = frozenset(
    {
        "ntp_servers",
        "logging_hosts",
        "passive_interfaces",
        "non_passive_interfaces",
        "address_families",
        "networks",
        "communities",
        "hosts",
        "lines",
        "certificates",
        "secondary_addresses",
        # インターフェースの IPv6 のアドレスと仮想のゲートウェイのアドレス(interfaces[]。ADR-0048、REQ-59)。
        # VRRP の vrrp[].virtual_addresses は、vrrp がここに無いのでリスト全体で比べ、ここには当たらない
        "ipv6_addresses",
        "virtual_addresses",
        "transport_input",
        # line の入口に当てた ACL の行の集合(lines[].access_class。ADR-0103)。lines[].options は下の options で比べる
        "access_class",
        # BGP の相手の options(ADR-0038 決定4)。スパニングツリーの全体の options も同じ名前(ADR-0042)。
        # vxlan[].options もここで当たる。vxlan[].members の値は鍵が番号なので当たらず、昇順の一覧まるごとの
        # changed になる(REQ-64)。mlag.options もここで当たる(REQ-65)。bfd.options もここで当たる(REQ-70)
        "options",
        # インターフェースのスパニングツリーの行の集合(interfaces[].spanning_tree。ADR-0042 決定2)
        "spanning_tree",
        # FortiGate のファイアウォール(vendor_specific["fortinet"]["firewall"]。ADR-0037 決定2)
        "srcintf",
        "dstintf",
        "srcaddr",
        "dstaddr",
        "service",
        "member",
        # Si-R の相手・接続先の行と秘密の行の名前(vendor_specific["fujitsu_sir"]["remotes"]。REQ-49、ADR-0039)
        "attributes",
        "secrets",
        # FortiGate の汎用の形の unset の欄の名前(vendor_specific["fortinet"]["config"]。REQ-50、ADR-0040 決定2)。
        # 同じ形の secrets は上で足してある。edit の並び order は並びとして比べるので足さない
        "unset",
        # QoS のブロックの中の行の道すじと control-plane の行(qos。ADR-0066、REQ-77)。全体の options は上で足してある。
        # Si-R の vendor_specific["fujitsu_sir"]["settings"] も同じ名前だが、辞書なので比べ方は変わらない。
        # 認証サーバーとグループの中の行(global.aaa_servers.servers.*.settings・groups.*.settings)と
        # ほかの指定(global.aaa_servers.options。ADR-0070、REQ-81)も、この settings と上の options で当たる
        "settings",
        "control_plane",
        # インターフェースの QoS の行の集合(interfaces[].qos)。カテゴリの qos は辞書なのでここには当たらない
        "qos",
        # 機器全体の設定の行の原文と道すじ(global.device_settings。ADR-0069、REQ-80)
        "device_settings",
        # NTP・ログのほかの指定、名前解決のサーバー、ルーティングの有効化の行(global。ADR-0050、REQ-61)。
        # 名前解決の dns.options は上の options で当たる。servers という名前の一覧はほかの IR に無い
        "ntp_options",
        "logging_options",
        "servers",
        "routing",
        # 管理の入口の行と Arista の management のブロックの中の行(global.management。ADR-0051、REQ-62)は
        # 上の settings で当たる。NX-OS の feature の集合(global.features)。features という名前の一覧はほかの IR に無い
        "features",
        # trustpoint の中の行(global.trustpoints.*.settings)と VTP のほかの指定(global.vtp.options)は
        # 上の settings・options で当たる。system mtu の残りの集合(global.system_mtu。ADR-0052、REQ-63)
        "system_mtu",
        # service の行の原文の集合(global.services。ADR-0063、REQ-74)。FortiGate の
        # vendor_specific["fortinet"]["firewall"]["services"] も同じ名前だが、辞書なので比べ方は変わらない
        "services",
        # インターフェースに当てた ACL の行の集合(interfaces[].access_groups。ADR-0057、REQ-68)
        "access_groups",
        # インターフェースの HSRP の行の残りと、ポートの守りの設定の行の集合
        # (interfaces[].hsrp・interfaces[].port_security。ADR-0068、REQ-79)。同じ名前の一覧はほかの IR に無い
        "hsrp",
        "port_security",
        # SNMPv3 の利用者(要素は辞書。communities と同じく集合として比べる)と、グループ・ビューの残りの集合
        # (global.snmp.users・groups・views。ADR-0102、REQ-83)。ほかの指定(global.snmp.options)は上の options で当たる。
        # global.users・global.aaa_servers.groups・Si-R の groups は辞書なので、同じ名前でも当たらない
        "users",
        "groups",
        "views",
        # VRF の route-target の行の残りの集合(vrfs[].route_targets。ADR-0105、REQ-86)。vrfs[].options・ospf[].options は
        # 上の options で、ospfv3[].settings・eigrp[].settings・ipsec.blocks[].settings・ipsec.options・sdwan[].settings は
        # 上の settings・options で当たる。BGP の VRF の route-target(bgp.vrfs[].route_targets。ADR-0045、REQ-56)も
        # ここで、BGP の直下・address-family・VRF の各 options は上の options で当たる。bgp.address_families は辞書なので、
        # 上の address_families には当たらず名前ごとにたどる。BGP の EVPN のブロック(bgp.evpn_instances[]。REQ-57)の
        # route_targets もここで、options は上の options で当たる
        "route_targets",
        # インターフェースの tunnel の行の残りの集合(interfaces[].tunnel。ADR-0104、REQ-85)
        "tunnel",
        # route-map のエントリの match・set の後ろの集合(route_maps[].entries[].match・set。ADR-0047、REQ-58)。
        # options は上で当たる。FortiGate の汎用の形の set(vendor_specific["fortinet"]["config"])は辞書なので当たらない。
        # community-list・as-path のリストの entries(route_lists。ADR-0062、REQ-73)は順を見るので足さない
        "match",
        "set",
        # インターフェースの DHCP の中継・IP の補助・L1 と L2 の補助の行の集合
        # (interfaces[].dhcp_relay・ip_options・port_options。ADR-0109・ADR-0110、REQ-157)。同じ名前の一覧・辞書はほかの IR に無い。
        # interfaces[].address_mode は1つの文字列なので、値の changed になる
        "dhcp_relay",
        "ip_options",
        "port_options",
        # インターフェースの PoE の優先度・見張りの行の集合(interfaces[].poe_options。ADR-0111 決定3、REQ-158)。
        # 同じ名前の一覧はほかの IR に無い。interfaces[].channel_group_auto は1つの真偽値なので、値の changed になる
        "poe_options",
        # インターフェースの通信の記録の行の集合(interfaces[].traffic_monitoring。ADR-0061、REQ-72)。
        # カテゴリの traffic_monitoring は辞書なのでここには当たらない(中の settings は上で当たる)
        "traffic_monitoring",
        # インターフェースのマルチキャストの行の集合(interfaces[].multicast。ADR-0064、REQ-75)。
        # カテゴリの multicast は辞書なのでここには当たらない(中の settings は上で当たる)
        "multicast",
        # インターフェースの OSPF の行の残りの集合(interfaces[].ospf。ADR-0055、REQ-66)。
        # カテゴリの ospf(router ospf)はカテゴリの名前が道すじに入らないので、ここには当たらない
        "ospf",
        # インターフェースの BFD の行の集合(interfaces[].bfd。ADR-0128 決定2、REQ-173)。
        # カテゴリの bfd(最上位の bfd・router bfd)は1つの塊の分類で、分類の名前が道すじに入らないので当たらない
        "bfd",
        # IS-IS の NET の集合(isis[].net)と、インターフェースの IS-IS の行の残りの集合(interfaces[].isis)
        # (ADR-0056、REQ-67)。カテゴリの isis はカテゴリの名前が道すじに入らないので、ここには当たらない。
        # isis[].options と address_families[].options は上の options で当たる。isis[].address_families は辞書なので
        # 名前ごとにたどる
        "net",
        "isis",
        # VLAN とポートの trunk group の名前の集合(vlans[].trunk_groups・interfaces[].switchport.trunk_groups。
        # ADR-0058、REQ-69)。同じ名前の一覧はほかの IR に無い。vlans[].state・vn_segment は1つの値なので、値の changed になる
        "trunk_groups",
        # インターフェースの 802.1X の行の残りと、ポートの IP 電話の設定の集合
        # (interfaces[].dot1x・interfaces[].switchport.phone。ADR-0060、REQ-71)。同じ名前の一覧はほかの IR に無い
        "dot1x",
        "phone",
        # インターフェースの MPLS・PTP・EVPN の行の集合(interfaces[].mpls・ptp・evpn。ADR-0108、REQ-89)。
        # カテゴリの mpls・ptp は辞書なのでここには当たらない(中の settings は上で当たる)
        "mpls",
        "ptp",
        "evpn",
    }
)
# エントリを seq で引いて比べる ACL のカテゴリ(IPv6・MAC は ADR-0101 決定2、REQ-82)
_SEQ_KEYED_ACLS = frozenset({"acls", "ipv6_acls", "mac_acls"})
# カテゴリ直下にブロック名のキーを持たず、1つの値として扱うカテゴリ
_SINGLE_BLOCK = frozenset(
    {
        "global", "bgp", "spanning_tree", "qos", "ipsec", "route_lists", "traffic_monitoring", "multicast",
        "mlag", "bfd", "mpls", "ptp", "vendor_specific",
    }
)


def _canon(value: Any) -> Any:
    """辞書のキー順に依らない比較用のキーにする(リストの並びは保つ)。"""
    if isinstance(value, dict):
        return tuple(sorted((str(k), _canon(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_canon(v) for v in value)
    return repr(value)


def _diff(
    op: DiffOp, category: str, name: str | None, path: tuple[str, ...], before: Any, after: Any
) -> Diff:
    return {"op": op, "category": category, "name": name, "path": path, "before": before, "after": after}


def _walk(
    category: str, name: str | None, path: tuple[str, ...], before: Any, after: Any, out: list[Diff]
) -> None:
    if before == after:
        return
    if isinstance(before, dict) and isinstance(after, dict):
        for key in before.keys() | after.keys():
            sub = (*path, str(key))
            if key not in after:
                out.append(_diff("removed", category, name, sub, before[key], None))
            elif key not in before:
                out.append(_diff("added", category, name, sub, None, after[key]))
            else:
                _walk(category, name, sub, before[key], after[key], out)
    elif isinstance(before, list) and isinstance(after, list):
        if category in _SEQ_KEYED_ACLS and path == ("entries",):
            _walk_acl_entries(category, name, before, after, out)
        elif path and path[-1] in _SET_LISTS:
            _walk_set(category, name, path, before, after, out)
        else:
            out.append(_diff("changed", category, name, path, before, after))
    else:
        out.append(_diff("changed", category, name, path, before, after))


def _walk_set(
    category: str, name: str | None, path: tuple[str, ...], before: list, after: list, out: list[Diff]
) -> None:
    """並びを無視し、要素の増減だけを出す(同じ要素の重複も数える)。"""
    left = Counter(_canon(item) for item in before)
    right = Counter(_canon(item) for item in after)
    by_key = {_canon(item): item for item in [*before, *after]}
    for key, count in (left - right).items():
        out.extend(_diff("removed", category, name, path, by_key[key], None) for _ in range(count))
    for key, count in (right - left).items():
        out.extend(_diff("added", category, name, path, None, by_key[key]) for _ in range(count))


def _walk_acl_entries(category: str, name: str | None, before: list, after: list, out: list[Diff]) -> None:
    path = ("entries",)
    keyed = all(e["seq"] is not None for e in [*before, *after])
    seqs_unique = len({e["seq"] for e in before}) == len(before) and len({e["seq"] for e in after}) == len(after)
    if not (keyed and seqs_unique):
        # seq の無いエントリがあるときは位置の列として比べる
        out.append(_diff("changed", category, name, path, before, after))
        return
    left = {e["seq"]: e for e in before}
    right = {e["seq"]: e for e in after}
    for seq in left.keys() | right.keys():
        sub = (*path, str(seq))
        if seq not in right:
            out.append(_diff("removed", category, name, sub, left[seq], None))
        elif seq not in left:
            out.append(_diff("added", category, name, sub, None, right[seq]))
        elif left[seq] != right[seq]:
            out.append(_diff("changed", category, name, sub, left[seq], right[seq]))
    common = left.keys() & right.keys()
    if [e["seq"] for e in before if e["seq"] in common] != [e["seq"] for e in after if e["seq"] in common]:
        out.append(_diff("changed", category, name, path, before, after))


def _walk_unparsed(before: list[tuple[str, ...]], after: list[tuple[str, ...]], out: list[Diff]) -> None:
    left = Counter(before)
    right = Counter(after)
    for lines, op in ((left - right, "removed"), (right - left, "added")):
        for line_path, count in lines.items():
            label = " > ".join(line_path)
            for _ in range(count):
                if op == "removed":
                    out.append(_diff("removed", "unparsed", label, (), line_path, None))
                else:
                    out.append(_diff("added", "unparsed", label, (), None, line_path))


def _route_key(route: dict) -> tuple[Any, Any, Any, Any]:
    return route["vrf"], route["destination"], route["next_hop"], route["interface"]


def _route_name(key: tuple[Any, Any, Any, Any]) -> str:
    vrf, destination, next_hop, interface = key
    name = f"{destination} via {next_hop if next_hop is not None else interface}"
    if interface is not None and next_hop is not None:
        name += f" ({interface})"
    return f"vrf {vrf}: {name}" if vrf is not None else name


def _route_details(ir: IR) -> dict[tuple[Any, Any, Any, Any], tuple[str, Any]]:
    """経路と位置が対応する機種固有値を、比較用に内容の鍵へ移す。"""
    result: dict[tuple[Any, Any, Any, Any], tuple[str, Any]] = {}
    for vendor, values in ir.get("vendor_specific", {}).items():
        if not isinstance(values, dict):
            continue
        details = values.get("static_routes")
        if not isinstance(details, list):
            continue
        for route, detail in zip(ir.get("static_routes", []), details):
            result[_route_key(route)] = vendor, detail
    return result


def _route_detail_lists(ir: IR) -> dict[str, list]:
    """鍵が重複して対応付けられない場合に、機種固有値を元の並びで比べる。"""
    return {
        vendor: values["static_routes"]
        for vendor, values in ir.get("vendor_specific", {}).items()
        if isinstance(values, dict) and isinstance(values.get("static_routes"), list)
    }


def _without_route_details(values: dict) -> dict:
    result = {}
    for vendor, data in values.items():
        if isinstance(data, dict) and "static_routes" in data:
            other = {key: value for key, value in data.items() if key != "static_routes"}
            if other:
                result[vendor] = other
        else:
            result[vendor] = data
    return result


def _with_route_detail(route: dict, vendor_detail: tuple[str, Any] | None) -> dict:
    """片側にだけある経路の値に、機種固有値を添えたコピーを返す(ADR-0072)。IR は書き換えない。"""
    if vendor_detail is None:
        return route
    vendor, detail = vendor_detail
    # Cisco の経路にも並びを揃えるため空の値を積む。すべて None のときは添えない(REQ-28)。
    if isinstance(detail, dict) and all(value is None for value in detail.values()):
        return route
    return {**route, "vendor_specific": {vendor: copy.deepcopy(detail)}}


def _walk_routes(before: IR, after: IR, out: list[Diff]) -> None:
    left_list = before.get("static_routes", [])
    right_list = after.get("static_routes", [])
    left_keys = [_route_key(route) for route in left_list]
    right_keys = [_route_key(route) for route in right_list]
    if len(set(left_keys)) != len(left_keys) or len(set(right_keys)) != len(right_keys):
        if left_list != right_list:
            out.append(_diff("changed", "static_routes", None, (), left_list, right_list))
        left_details = _route_detail_lists(before)
        right_details = _route_detail_lists(after)
        for vendor in left_details.keys() | right_details.keys():
            old = left_details.get(vendor, [])
            new = right_details.get(vendor, [])
            if old != new:
                out.append(_diff("changed", "vendor_specific", None, (vendor, "static_routes"), old, new))
        return
    left = dict(zip(left_keys, left_list))
    right = dict(zip(right_keys, right_list))
    before_details = _route_details(before)
    after_details = _route_details(after)
    for key in left.keys() | right.keys():
        name = _route_name(key)
        if key not in right:
            out.append(_diff("removed", "static_routes", name, (), _with_route_detail(left[key], before_details.get(key)), None))
        elif key not in left:
            out.append(_diff("added", "static_routes", name, (), None, _with_route_detail(right[key], after_details.get(key))))
        else:
            _walk("static_routes", name, (), left[key], right[key], out)
    # 片側にだけある経路の機種固有値は経路の差分に添えたので、ここでは両側にある経路だけを見る(ADR-0072)
    for key in left.keys() & right.keys():
        if key in before_details and key in after_details:
            if before_details[key] != after_details[key]:
                old_vendor, old_detail = before_details[key]
                new_vendor, new_detail = after_details[key]
                if old_vendor == new_vendor:
                    _walk("vendor_specific", _route_name(key), (old_vendor, "static_routes"), old_detail, new_detail, out)
                else:
                    out.append(_diff("changed", "vendor_specific", _route_name(key), ("static_routes",),
                                     before_details[key], after_details[key]))
        elif key in before_details:
            vendor, detail = before_details[key]
            # Cisco の経路にも並びを揃えるため空の値を積む。空の差分まで出すと1本の経路が2件に見える。
            if not all(value is None for value in detail.values()):
                out.append(_diff("removed", "vendor_specific", _route_name(key), (vendor, "static_routes"), detail, None))
        elif key in after_details:
            vendor, detail = after_details[key]
            if not all(value is None for value in detail.values()):
                out.append(_diff("added", "vendor_specific", _route_name(key), (vendor, "static_routes"), None, detail))


def _sort_key(diff: Diff) -> tuple[str, str, tuple[str, ...], str, str]:
    return (diff["category"], diff["name"] or "", diff["path"], diff["op"], repr((diff["before"], diff["after"])))


def compare(before: IR, after: IR) -> list[Diff]:
    """before から after への差分を (category, name, path) の昇順で返す。差分がなければ空のリスト。"""
    out: list[Diff] = []
    for category in sorted((before.keys() | after.keys()) - {"unparsed"}):
        left: Any = before.get(category, {})
        right: Any = after.get(category, {})
        if category == "static_routes":
            _walk_routes(before, after, out)
            continue
        if category == "vendor_specific":
            left = _without_route_details(left)
            right = _without_route_details(right)
        if category in _SINGLE_BLOCK:
            _walk(category, None, (), left, right, out)
            continue
        for key in left.keys() | right.keys():
            if key not in right:
                out.append(_diff("removed", category, key, (), left[key], None))
            elif key not in left:
                out.append(_diff("added", category, key, (), None, right[key]))
            else:
                _walk(category, key, (), left[key], right[key], out)
    _walk_unparsed(before.get("unparsed", []), after.get("unparsed", []), out)
    return sorted(out, key=_sort_key)
