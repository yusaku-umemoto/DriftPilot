"""構造化差分の伏せ方(ADR-0018 決定2b 基準1)。IR のフィールド名で決める。

項目は「総称のパス」で書く。名前で引く辞書のキーは `*`、リストの要素は `[]` にする
(例: `global.users.*.secret`、`global.snmp.communities[].name`)。

- MASKED_FIELDS: 値を記号にする項目
- LINE_FIELDS: 行の原文を持つ項目。1行として3段の判定(ゲート → 表 → 送らない)に通す
- UNMASKED_FIELDS: 名前が秘密らしいが伏せない項目と、その理由

IR に項目が増えたときの伏せ漏れを防ぐため、名前に secret / key / password / community などを含む項目が
3つの一覧のどれかに必ず入っていることをテストで確かめる(tests/test_llm_masking.py)。
"""

import types
from typing import Any, Union, get_args, get_origin, get_type_hints, is_typeddict

from driftpilot.config_parser.ir import IR

MASKED_FIELDS: dict[str, str] = {
    "global.users.*.secret": "パスワードのハッシュ(password の行では戻せる型か平文。nopassword では None)",
    "global.enable_secret.value": "enable のパスワードの値",
    "global.enable_password.value": "enable のパスワードの値",
    "global.snmp.communities[].name": "SNMP のコミュニティ名",
    "global.snmp.hosts[].community": "トラップ送信先のコミュニティ名(SNMPv3 では利用者名だが、区別せず伏せる)",
    "global.snmp.users[].name": "SNMPv3 の利用者名(コミュニティ名と同じく伏せる。ADR-0102 決定2)",
    "global.certificate_chains.*.certificates[].hash": "証明書の中身(既にハッシュ。ADR-0011)",
}

# 総称のパス → 行として判定するときの祖先の行(scope の照合に使う)
LINE_FIELDS: dict[str, tuple[str, ...]] = {
    "global.aaa.lines[]": (),
    "acls.*.entries[].text": ("ip access-list",),
    # IPv6・MAC の ACL のエントリ(REQ-82)。acls と同じく、見出しの書き出しを祖先にする
    "ipv6_acls.*.entries[].text": ("ipv6 access-list",),
    "mac_acls.*.entries[].text": ("mac access-list",),
    "unparsed[]": (),  # 祖先は行のパスから取る(payload.py)
    "vendor_specific.*": (),  # 中身の形を決めていないため、文字列は行として判定する
    "bgp.neighbors.*.options[]": ("router bgp",),  # 中身が自由な文字列で、秘密が入る形を前もって決められない
    # BGP の第2段の行の原文と残り(REQ-56)。bgp.neighbors.*.options と同じく自由な文字列。祖先は router bgp にそろえる。
    # rd も原文なので行として判定する。router_id はアドレスで、秘密らしい名前ではないので、どの一覧にも足さない
    "bgp.options[]": ("router bgp",),
    "bgp.address_families.*.options[]": ("router bgp",),
    "bgp.address_families.*.neighbors.*.options[]": ("router bgp",),
    "bgp.vrfs.*.rd": ("router bgp",),
    "bgp.vrfs.*.route_targets[]": ("router bgp",),
    "bgp.vrfs.*.options[]": ("router bgp",),
    "bgp.vrfs.*.neighbors.*.options[]": ("router bgp",),
    "bgp.vrfs.*.address_families.*.options[]": ("router bgp",),
    "bgp.vrfs.*.address_families.*.neighbors.*.options[]": ("router bgp",),
    # BGP の第3段の EVPN のブロックの中の原文(REQ-57)。第2段の vrfs と同じ扱い
    "bgp.evpn_instances.*.rd": ("router bgp",),
    "bgp.evpn_instances.*.route_targets[]": ("router bgp",),
    "bgp.evpn_instances.*.options[]": ("router bgp",),
    # スパニングツリーの行の残り(REQ-52)。BGP の options と同じく自由な文字列。祖先は元の行の親(最上位・interface)
    "spanning_tree.options[]": (),
    "interfaces.*.spanning_tree[]": ("interface",),
    # QoS の行の道すじと原文(REQ-77)。自由な文字列。祖先はブロックの中の行でも最上位にそろえる
    "qos.class_maps.*.settings[]": (),
    "qos.policy_maps.*.settings[]": (),
    "qos.table_maps.*.settings[]": (),
    "qos.control_plane[]": (),
    "qos.options[]": (),
    "interfaces.*.qos[]": ("interface",),
    # 機器全体の設定の原文と道すじ(REQ-80)。自由な文字列。祖先はブロックの中の行でも最上位にそろえる
    "global.device_settings[]": (),
    # NTP・ログ・名前解決のサーバーや送り先と、ほかの指定(REQ-61)。後ろに自由な文字列を持つ。
    # 行の頭の語を外した形なので、LINE_PREFIXES の語を前に付けて元の行の形で判定する。
    # ntp_options の `no <残り>`(REQ-166)は logging_options と同じく、判定の前に `ntp ` を付けて行にする
    "global.ntp_servers[]": (),
    "global.ntp_options[]": (),
    "global.logging_hosts[]": (),
    "global.logging_options[]": (),
    "global.dns.servers[]": (),
    # 名前解決の指定とルーティングの有効化の行の原文(REQ-61)。後ろに自由な文字列を持つ。行全体なので語は付けない
    "global.dns.options[]": (),
    "global.routing[]": (),
    # 管理の入口の行の原文と、Arista の management のブロックの中の行の道すじ(REQ-62)。後ろに自由な文字列を持つ。
    # 行全体なので語は付けない。祖先はブロックの中の行でも最上位にそろえる
    "global.management.settings[]": (),
    "global.management.blocks.*.settings[]": (),
    # trustpoint の中の行の原文(REQ-63)。後ろに自由な文字列を持つ。行全体なので語は付けない
    "global.trustpoints.*.settings[]": ("crypto pki trustpoint",),
    # VTP のほかの指定(REQ-63)。頭の vtp を外した形なので、LINE_PREFIXES の語を前に付けて判定する
    "global.vtp.options[]": (),
    # service の行の原文(REQ-74)。後ろに自由な文字列を持つ。行全体なので語は付けない
    "global.services[]": (),
    # line のブロックの中の行の原文と道すじ(REQ-84)。自由な文字列。祖先は line の見出しにそろえる。
    # lines.*.access_class は ACL の名前を持つ行なので、どの一覧にも足さない(ADR-0018 決定2)
    "lines.*.options[]": ("line",),
    # vlan のブロックの中のほかの行の原文(ADR-0126 決定2、REQ-171)。自由な文字列。祖先は vlan の見出しにそろえる
    "vlans.*.options[]": ("vlan",),
    # HSRP の行の残りと、ポートの守りの設定の行の原文(REQ-79)。自由な文字列。祖先は interface。
    # hsrp は頭の standby を外した形だが、spanning_tree と同じく語は付けない。
    # interfaces.*.access_groups は ACL の名前を持つ行なので、どの一覧にも足さない(ADR-0018 決定2、REQ-68)
    "interfaces.*.hsrp[]": ("interface",),
    "interfaces.*.port_security[]": ("interface",),
    # A型の VRRP のほかの行の原文と、VRRPv3 のブロックの中の道すじ(REQ-170)。自由な文字列。祖先は interface。
    # 頭の vrrp は外していないので語は付けない(LINE_PREFIXES は要らない)
    "interfaces.*.vrrp[].options[]": ("interface",),
    # 認証サーバーとグループの中の行の原文と、ほかの指定の行の原文(REQ-81)。自由な文字列。
    # 祖先はブロックの中の行でも最上位にそろえる。鍵の値は IR に無い(key_configured だけ)
    "global.aaa_servers.servers.*.settings[]": (),
    "global.aaa_servers.groups.*.settings[]": (),
    "global.aaa_servers.options[]": (),
    # SNMPv3 の利用者の残り(鍵の値は捨ててある)と、グループ・ビューの残り、ほかの snmp の行の原文(REQ-83)。
    # 自由な文字列。users[].settings・groups・views は頭の語を外した形だが、前に付ける語は足さない(REQ-155 と同じ)
    "global.snmp.users[].settings[]": (),
    "global.snmp.groups[]": (),
    "global.snmp.views[]": (),
    "global.snmp.options[]": (),
    # OSPF のほかの行の原文と道すじ、OSPFv3 の中の行の道すじ(REQ-88)。自由な文字列。祖先は見出しにそろえる
    "ospf.*.options[]": ("router ospf",),
    "ospfv3.*.settings[]": ("router ospfv3",),
    # VRF のほかの行の原文と道すじ、route-target の行の残り(REQ-86)。自由な文字列。祖先は vrf にそろえる。
    # vrfs.*.description はインターフェースの description と同じく、どの一覧にも足さない
    "vrfs.*.options[]": ("vrf",),
    "vrfs.*.route_targets[]": ("vrf",),
    # EIGRP の中の行の道すじ(REQ-76)。自由な文字列。祖先は見出しにそろえる
    "eigrp.*.settings[]": ("router eigrp",),
    # IPsec のブロックの中の行と最上位の行の原文(REQ-85)。鍵の値は外してある(keys_configured・` <key>` だけ)。
    # 祖先は crypto にそろえる。前に付ける語(LINE_PREFIXES)は足さない(REQ-155 と同じ)
    "ipsec.blocks.*.settings[]": ("crypto",),
    "ipsec.options[]": ("crypto",),
    # インターフェースの tunnel の行の残り(REQ-85)。hsrp と同じく頭の語は付けない
    "interfaces.*.tunnel[]": ("interface",),
    # インターフェースの DHCP の中継・IP の補助・L1 と L2 の補助の行の原文(REQ-157)。自由な文字列。祖先は interface。
    # 行全体なので語は付けない。interfaces.*.address_mode は秘密らしい名前ではないので、どの一覧にも足さない
    "interfaces.*.dhcp_relay[]": ("interface",),
    "interfaces.*.ip_options[]": ("interface",),
    "interfaces.*.port_options[]": ("interface",),
    # インターフェースの PoE の優先度・見張りの行の原文(REQ-158)。REQ-157 の3つと同じく、祖先は interface で語は付けない。
    # interfaces.*.channel_group_auto と line の media_type_rj45_switch は秘密らしい名前ではないので、どの一覧にも足さない
    "interfaces.*.poe_options[]": ("interface",),
    # PoE の方式の後ろ全体(REQ-158 で `<方式> <残り>` を持つようになった)。自由な文字列なので、
    # `power inline ` を前に付けた元の行の形で判定する(LINE_PREFIXES)。祖先は interface
    "interfaces.*.poe": ("interface",),
    # route-map のエントリの match・set の後ろと、ほかの行の原文(REQ-58)。自由な文字列。祖先は route-map にそろえる。
    # match・set は頭の語を外した形だが、前に付ける語(LINE_PREFIXES)は足さない(REQ-155 と同じ)。
    # description と prefix-list の prefix は、インターフェースの description・経路の宛先と同じく、どの一覧にも足さない
    "route_maps.*.entries.*.match[]": ("route-map",),
    "route_maps.*.entries.*.set[]": ("route-map",),
    "route_maps.*.entries.*.options[]": ("route-map",),
    # community-list・as-path のリストの permit・deny から後ろの原文(REQ-73)。自由な文字列。祖先は最上位
    "route_lists.community.*.entries[]": (),
    "route_lists.extcommunity.*.entries[]": (),
    "route_lists.large_community.*.entries[]": (),
    "route_lists.as_path.*.entries[]": (),
    # 通信の記録の行の原文とブロックの中の行の道すじ、インターフェースの行の原文(REQ-72)。自由な文字列。
    # 祖先はブロックの中の行でも最上位にそろえる。インターフェースの行の祖先は interface。行全体なので語は付けない
    "traffic_monitoring.settings[]": (),
    "interfaces.*.traffic_monitoring[]": ("interface",),
    # マルチキャストの行の原文とブロックの中の行の道すじ、インターフェースの行の原文(REQ-75)。REQ-72 と同じ扱い
    "multicast.settings[]": (),
    "interfaces.*.multicast[]": ("interface",),
    # SD-WAN のブロックの中の行の道すじ(REQ-159)。自由な文字列。global.management.blocks.*.settings と同じく、
    # 祖先はブロックの中の行でも最上位にそろえ、行全体なので語は付けない
    "sdwan.*.settings[]": (),
    # VTEP のインターフェースの VXLAN のほかの行と、member vni の中の行(REQ-64)。自由な文字列。祖先は interface。
    # options は Arista の `vxlan ` を外した形と NX-OS の原文が混ざるので、前に付ける語(LINE_PREFIXES)は足さない
    "vxlan.*.options[]": ("interface",),
    "vxlan.*.members.*[]": ("interface",),
    # `mlag configuration` の中のほかの行の原文(REQ-65)。自由な文字列。行全体なので語は付けない
    "mlag.options[]": ("mlag configuration",),
    # インターフェースの `ip ospf` の行の残り(REQ-66)。hsrp と同じく頭の語を外した形だが、前に付ける語は足さない。
    # 鍵の値は IR に無い(`authentication-key`・`message-digest-key <番号> <方式>` だけ)。
    # `ipv6 ospf …` は行の原文のまま混ざるが、前に付ける語は今までどおり足さない(REQ-165)。
    # interfaces.*.encapsulation・autostate は秘密らしい名前ではないので、どの一覧にも足さない
    "interfaces.*.ospf[]": ("interface",),
    # インターフェースの `bfd` の行の残り(REQ-173)。ospf と同じく頭の語を外した形で、前に付ける語は足さない。
    # 鍵の行(`bfd authentication … key …` など)は IR に入らない
    "interfaces.*.bfd[]": ("interface",),
    # `router isis` の中のほかの行の原文と、address-family の中の行の原文(REQ-67)。自由な文字列。行全体なので語は付けない。
    # インターフェースの `isis` の行の残りは、ospf と同じく頭の語を外した形だが、前に付ける語は足さない。
    # 鍵の値は IR に無い(`authentication key` だけ)。isis.*.net・is_type・router_id は秘密らしい名前ではないので、
    # どの一覧にも足さない
    "isis.*.options[]": ("router isis",),
    "isis.*.address_families.*.options[]": ("router isis",),
    "interfaces.*.isis[]": ("interface",),
    # インターフェースの `dot1x` の行の残り(REQ-71)。ospf・isis と同じく頭の語を外した形だが、前に付ける語は足さない。
    # Cisco の `authentication …`・`mab …` は行の原文のまま混ざるが、前に付ける語は今までどおり足さない(REQ-165)。
    # 鍵の行は IR に入らない。interfaces.*.switchport.phone は秘密らしい名前ではないので、どの一覧にも足さない
    "interfaces.*.dot1x[]": ("interface",),
    # `router bfd` の中の行の原文と、最上位の `bfd` を外した残りが混ざる自由な文字列(REQ-70)。
    # 前に付ける語(LINE_PREFIXES)は足さない(vxlan と同じ)。鍵の行は IR に入らない
    "bfd.options[]": ("router bfd",),
    # MPLS・PTP の行の原文とブロックの中の行の道すじ、インターフェースの MPLS・PTP・EVPN の行の原文と道すじ(REQ-89)。
    # REQ-75 と同じ扱い。祖先はブロックの中の行でも最上位(インターフェースの行は interface)にそろえ、
    # 行全体なので語は付けない。鍵の行は IR に入らない
    "mpls.settings[]": (),
    "ptp.settings[]": (),
    "interfaces.*.mpls[]": ("interface",),
    "interfaces.*.ptp[]": ("interface",),
    "interfaces.*.evpn[]": ("interface",),
    # バナーの本文(REQ-161)。自由な文字列なので行として判定する。祖先は最上位。
    # 秘密の語がある本文は IR に入らない
    "global.banners.*": (),
}

# 総称のパス → 行として判定するときに前に付ける語(LINE_FIELDS のうち、行の頭の語を外して持つ項目)。
# 伏せ方の表は行全体に当てるため、元の行の形にしないと表の行(MSK-30 など)が当たらない。送るときは外す。
# vrf の位置などは元の行と違うことがあるが、表は `vrf <V>` の位置を問わない
LINE_PREFIXES: dict[str, str] = {
    "global.ntp_servers[]": "ntp server ",
    "global.ntp_options[]": "ntp ",
    "global.logging_hosts[]": "logging host ",
    "global.logging_options[]": "logging ",
    "global.dns.servers[]": "ip name-server ",
    "global.vtp.options[]": "vtp ",
    "interfaces.*.poe": "power inline ",
}

UNMASKED_FIELDS: dict[str, str] = {
    "global.users.*.secret_type": "暗号化の型の番号(5 / 8 / 9 など)で、値ではない",
    "global.users.*.privilege": "特権レベルの番号(0〜15)",
    "global.users.*.credential": "secret / password / none の別で値ではない",
    "global.users.*.role": "役割の名前",
    "global.enable_secret": "型と値の組。中の項目(type / value)ごとに判定する",
    "global.enable_password": "型と値の組。中の項目(type / value)ごとに判定する",
    "global.enable_secret.type": "型の名前で値ではない",
    "global.enable_password.type": "型の名前で値ではない",
    "global.snmp.communities": "一覧そのもの。要素の項目(name / access / acl)ごとに判定する",
    "global.snmp.communities[]": "コミュニティ1つ。要素の項目ごとに判定する",
    "global.snmp.communities[].access": "RO / RW の別か、NX-OS の group <役割>",
    "global.snmp.communities[].acl": "ACL 名(ADR-0018 決定2 で伏せない)",
    "global.snmp.hosts[].options": "traps / informs / version の指定",
    "global.snmp.communities[].view": "ビューの名前(REQ-83)",
    "global.snmp.users": "一覧そのもの。要素の項目(name / settings / auth_configured / priv_configured)ごとに判定する",
    "global.snmp.users[]": "SNMPv3 の利用者1人。要素の項目ごとに判定する",
    "global.snmp.users[].auth_configured": "認証の鍵が設定されているかの真偽値で、値は持たない(ADR-0102 決定2)",
    "global.snmp.users[].priv_configured": "暗号の鍵が設定されているかの真偽値で、値は持たない(ADR-0102 決定2)",
    "global.certificate_chains.*.certificates[].hash_algorithm": "ハッシュの算法名(sha256)",
    "global.certificate_chains.*.certificates[].hash_input_version": "ハッシュの入力の版",
    "lines.*.has_password": "パスワードが設定されているかの真偽値で、値は持たない(REQ-23)",
    "lines.*.privilege_level": "特権レベルの番号(0〜15)",
    "bgp.neighbors.*.password_configured": "パスワードが設定されているかの真偽値で、値は持たない(ADR-0038 決定6)",
    "bgp.vrfs.*.neighbors.*.password_configured": (
        "パスワードが設定されているかの真偽値で、値は持たない(ADR-0038 決定6。VRF の相手も同じ。REQ-56)"
    ),
    "global.aaa_servers.servers.*.key_configured": "鍵が設定されているかの真偽値で、値は持たない(ADR-0070 決定2)",
    "ipsec.blocks.*.keys_configured": "鍵が設定されているかの真偽値で、値は持たない(ADR-0104、REQ-85)",
    # community-list のリストの一覧(REQ-73)。global.snmp.communities と同じ扱い
    "route_lists.community": (
        "一覧そのもの。鍵はリストの名前(ACL 名と同じく伏せない。ADR-0018 決定2)。中の entries は行として判定する"
    ),
    "route_lists.extcommunity": (
        "一覧そのもの。鍵はリストの名前(ACL 名と同じく伏せない。ADR-0018 決定2)。中の entries は行として判定する"
    ),
    "route_lists.large_community": (
        "一覧そのもの。鍵はリストの名前(ACL 名と同じく伏せない。ADR-0018 決定2)。中の entries は行として判定する"
    ),
}

# 単一のブロックとして比べ、名前のキーを持たないカテゴリ(diff_engine.compare と同じ)
_SINGLE_BLOCK = frozenset(
    {
        "global", "bgp", "spanning_tree", "qos", "ipsec", "route_lists", "traffic_monitoring", "multicast",
        "mlag", "bfd", "mpls", "ptp", "vendor_specific",
    }
)
# 名前で引く辞書(キーを `*` にする)
_NAME_KEYED = frozenset(
    {
        "global.users",
        "global.certificate_chains",
        "global.management.blocks",
        "global.trustpoints",
        "global.aaa_servers.servers",
        "global.aaa_servers.groups",
        # バナーの種類(REQ-161。鍵は `banner` の次の語)
        "global.banners",
        "bgp.neighbors",
        # BGP の address-family・VRF と、その中の相手(REQ-56)
        "bgp.address_families",
        "bgp.address_families.*.neighbors",
        "bgp.vrfs",
        "bgp.vrfs.*.neighbors",
        "bgp.vrfs.*.address_families",
        "bgp.vrfs.*.address_families.*.neighbors",
        # BGP の EVPN の vlan・vlan-aware-bundle・vpws(REQ-57。鍵は見出しの原文)
        "bgp.evpn_instances",
        "vendor_specific",
        "qos.class_maps",
        "qos.policy_maps",
        "qos.table_maps",
        "ipsec.blocks",
        "route_maps.*.entries",
        "prefix_lists.*.entries",
        "ipv6_prefix_lists.*.entries",
        "route_lists.community",
        "route_lists.extcommunity",
        "route_lists.large_community",
        "route_lists.as_path",
        # VXLAN の VLAN・VRF → VNI の対応と member vni(REQ-64。鍵は VLAN の番号・VRF の名前・VNI の番号)
        "vxlan.*.vlan_vnis",
        "vxlan.*.vrf_vnis",
        "vxlan.*.members",
        # IS-IS の address-family(REQ-67。鍵は種類の語を空白1つでつないだ原文)
        "isis.*.address_families",
    }
)
# seq で引いて比べる ACL のエントリ(diff_engine.compare の _SEQ_KEYED_ACLS と同じカテゴリ)
_SEQ_KEYED_ENTRIES = frozenset({"acls.*.entries", "ipv6_acls.*.entries", "mac_acls.*.entries"})


def ir_field_paths() -> list[str]:
    """IR の型から、全項目の総称のパスを列挙する(フィールドの分類の検査に使う)。"""
    out: list[str] = []

    def walk(tp: Any, path: str) -> None:
        origin = get_origin(tp)
        if origin in (Union, types.UnionType):
            for arg in get_args(tp):
                if arg is not type(None):
                    walk(arg, path)
            return
        if path:
            out.append(path)
        if is_typeddict(tp):
            for name, hint in get_type_hints(tp).items():
                walk(hint, f"{path}.{name}" if path else name)
        elif origin is dict:
            walk(get_args(tp)[1], f"{path}.*")
        elif origin is list:
            walk(get_args(tp)[0], f"{path}[]")

    walk(IR, "")
    return sorted(set(out))


def diff_base(category: str, path: tuple[str, ...]) -> str:
    """差分1件の位置(category と path)の総称のパス。"""
    parts = category if category in _SINGLE_BLOCK else f"{category}.*"
    for key in path:
        if parts in _NAME_KEYED:
            parts += ".*"
        elif parts in _SEQ_KEYED_ENTRIES:  # seq で引くエントリ
            parts += "[]"
        else:
            parts += f".{key}"
    return parts


def child(path: str, key: str) -> str:
    return f"{path}.*" if path in _NAME_KEYED else f"{path}.{key}"


def line_ancestors(path: str) -> tuple[str, ...] | None:
    """行として判定する項目なら、判定に使う祖先の行。そうでなければ None。

    vendor_specific は中身の形を決めていないため、下の文字列はすべて行として判定する。
    足した・消した経路の差分に添えた機種固有値(static_routes.*.vendor_specific。ADR-0072)も同じ扱い。
    """
    if path in LINE_FIELDS:
        return LINE_FIELDS[path]
    if path.startswith(("vendor_specific.", "static_routes.*.vendor_specific.")):
        return ()
    return None


def line_prefix(path: str) -> str:
    """行として判定するときに値の前に付ける語(LINE_PREFIXES)。無ければ空の文字列。"""
    return LINE_PREFIXES.get(path, "")
