"""DriftPilot 共通の IR(中間表現)の型と、既定値で埋めた空の値を作る関数(ADR-0009)。

IR は 3 層からなる。
- 共通の固定スキーマ(global / vlans / interfaces / acls / ospf / bgp / spanning_tree / vrfs / static_routes / lines / qos
  / ipv6_acls / mac_acls / ospfv3 / eigrp / ipsec / route_maps / prefix_lists / ipv6_prefix_lists / vxlan / mlag
  / isis / bfd / route_lists / traffic_monitoring / multicast / sdwan / mpls / ptp)
- vendor_specific(機種固有で、共通スキーマに載せない項目)
- unparsed(どの extractor も解釈しなかった行のパス)

固定スキーマのキーは値が無くても常に作り、無い値は None(VLAN 範囲は空のタプル、複数値は空のリスト)で表す。
辞書のキーはすべて文字列にする(JSON に書き出して読み直しても同じ比較ができるように)。
"""

from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Network
from typing import Any, Literal, TypedDict

InterfaceType = Literal["switchport", "routed", "svi", "loopback", "port_channel"]
# dot1q-tunnel から tap-tool までは ADR-0126 決定1、REQ-171(空白は1つ)
SwitchportMode = Literal[
    "access",
    "trunk",
    "dot1q-tunnel",
    "private-vlan host",
    "private-vlan promiscuous",
    "private-vlan trunk",
    "private-vlan trunk promiscuous",
    "private-vlan trunk secondary",
    "dynamic auto",
    "dynamic desirable",
    "tap",
    "tap-tool",
]
# role-based は TrustSec の SGACL(`ip access-list role-based <名前>`。ADR-0067)
AclType = Literal["standard", "extended", "role-based"]
CertificateKind = Literal["self-signed", "ca"]
LineType = Literal["con", "vty", "aux"]
# none は `no login`(認証なし)、password は `login`、local は `login local`、list は `login authentication <名前>`
LoginMode = Literal["none", "password", "local", "list"]
BgpNeighborKind = Literal["address", "peer_group"]


class Switchport(TypedDict):
    mode: SwitchportMode | None
    access_vlan: int | None
    native_vlan: int | None
    # `()` は行が無い、または `all`(どちらも、すべて通す。ADR-0111 決定1)。`add` は前の数の一覧に足す(REQ-176)。
    # `none` は `trunk_allowed_none` が表す。`remove`・`except` などは未解釈
    trunk_allowed_vlan: tuple[int, ...]
    # True は `switchport trunk allowed vlan none`(1つも通さない。このとき `trunk_allowed_vlan` は `()`)。
    # False は行が無い・数の一覧・`all`(ADR-0132 決定1、REQ-177)
    trunk_allowed_none: bool
    # `switchport voice vlan <数>`(ADR-0068 決定1)。None は行が無い。Arista の phone の書き方とはそろえない
    voice_vlan: int | None
    # `switchport trunk group <名前>` の名前の集合(昇順)。空は行が無い(ADR-0058、REQ-69)
    trunk_groups: list[str]
    # `switchport phone <残り>` の残り(空白は1つ)と、`switchport mode trunk phone` の印 `mode trunk phone` の集合(昇順)。
    # 空は行が無い(ADR-0060、REQ-71)
    phone: list[str]


class ChannelGroup(TypedDict):
    id: int
    mode: str


class Vrrp(TypedDict):
    """VRRP のグループ1つ(ADR-0024 決定7)。group は VRID(Cisco の `vrrp <VRID>` と同じ意味)。認証鍵は持たない。"""

    group: int
    priority: int | None
    virtual_addresses: list[IPv4Address]
    preempt: bool | None
    # A型の、ほかの `vrrp <VRID> …` の行の原文(空白は1つ)と、VRRPv3 のブロックの中の道すじ(見出し > 子の行)の集合
    # (同じ文字列は重ねず昇順)。Si-R は None(ADR-0125 決定3)
    options: list[str] | None


class Interface(TypedDict):
    type: InterfaceType
    parent_interface: str | None
    description: str | None
    shutdown: bool
    vrf: str | None
    ip_address: IPv4Interface | None
    # `ip address … secondary` を書かれた順に持つ(primary は ip_address)。None は読んでいない(B型・D型)
    secondary_addresses: list[IPv4Interface] | None
    switchport: Switchport
    # `power inline <方式>` の方式。方式が auto・static・never・consumption で後ろに語があれば `<方式> <残り>`
    # (空白は1つ。ADR-0111 決定2)。優先度・見張りの行は poe_options。
    # Arista の `poe disabled` は `disabled`(語は原文のまま。ADR-0115 決定1)
    poe: str | None
    speed: str | None
    duplex: str | None
    channel_group: ChannelGroup | None
    # None は VRRP を読んでいない(Si-R の ether・FortiGate)。読んだうえで無ければ空のリスト(A型・Si-R の lan。
    # A型は VRID の昇順。ADR-0125)
    vrrp: list[Vrrp] | None
    # `mtu <数>`(ADR-0043 決定1)。None は行が無い、または読んでいない(B型・D型)。speed・duplex と同じ
    mtu: int | None
    # `ipv6 address …` の集合(文字列の昇順。ADR-0048 決定1)。`<アドレス>/<長さ>` は IPv6Interface の正規の書き方、
    # `eui-64`・`anycast` が付けば後ろに空白とその語、`<アドレス> link-local` は IPv6Address の正規の書き方と ` link-local`、
    # `auto-config`・`autoconfig`・`dhcp` はその語。None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    # (secondary_addresses と同じ)
    ipv6_addresses: list[str] | None
    # `ipv6 enable` → True、`no ipv6 enable` → False(ADR-0048 決定2)。None は行が無い、または読んでいない(mtu と同じ)
    ipv6_enabled: bool | None
    # 仮想のゲートウェイのアドレスの集合(文字列の昇順。ADR-0048 決定3)。`ip address virtual …`・`ipv6 address virtual …`
    # (エニーキャストのゲートウェイ)は正規の書き方(secondary 付きは後ろに ` secondary`)、`… virtual-router address …`
    # (VARP)はアドレスだけの正規の書き方と ` virtual-router`。VRRP の vrrp[].virtual_addresses とは別。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(secondary_addresses と同じ)
    virtual_addresses: list[str] | None
    # `mlag <数>`(ADR-0054、REQ-65)。None は行が無い、または読んでいない(B型・D型。mtu と同じ)
    mlag_id: int | None
    # `ip ospf <残り>`(空白は1つ)の集合。文字列の昇順(ADR-0055、REQ-66)。鍵の値は持たない
    # (`authentication-key …` は `authentication-key`、`message-digest-key …` は `message-digest-key <番号> <方式>`)。
    # `ipv6 ospf <残り>` と no 付きは行の原文(ADR-0120、REQ-165。`ipv6 ospf authentication …`・`ipv6 ospf encryption …`
    # は入れない)。None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(secondary_addresses と同じ)
    ospf: list[str] | None
    # インターフェースの直下の、子を持たない `bfd <残り>` の残りと、`no bfd <残り>` の `no <残り>`(空白は1つ)の集合。
    # 文字列の昇順(ADR-0128 決定2、ADR-0059 決定2、REQ-173)。秘密の語がある行と子を持つ行は入れない。
    # 最上位の `bfd …`・`router bfd` は bfd が読む(別の項目)。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(ospf と同じ)
    bfd: list[str] | None
    # `encapsulation <残り>` の原文(空白は1つ。ADR-0055、REQ-66)。2回あれば後の行。
    # None は行が無い、または読んでいない(B型・D型。mtu と同じ)
    encapsulation: str | None
    # `autostate` → True、`no autostate` → False(ADR-0055、REQ-66)。None は行が無い、または読んでいない(mtu と同じ)
    autostate: bool | None
    # `isis <残り>`(空白は1つ)の集合。文字列の昇順(ADR-0056、REQ-67)。鍵の値は持たない
    # (`isis authentication key …` は `authentication key`)。`no isis <残り>` は `no <残り>`(ADR-0133 決定4、REQ-178)。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(secondary_addresses と同じ)
    isis: list[str] | None
    # `spanning-tree <残り>`(no 付きは `no <残り>`)の集合。文字列の昇順(ADR-0042 決定2)。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(secondary_addresses と同じ)
    spanning_tree: list[str] | None
    # `service-policy …` などの QoS の行の原文の集合。文字列の昇順(ADR-0066、REQ-77)。
    # Arista の `service-profile <名前>` も入る(ADR-0115 決定2)。
    # Catalyst の待ち行列の `srr-queue …`・`queue-set …`・`wrr-queue …` も入る(ADR-0128 決定3、REQ-173)。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    qos: list[str] | None
    # 当てた ACL の行の原文の集合(`ip access-group <ACL> in` など。文字列の昇順。ADR-0057 決定1)。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(secondary_addresses と同じ)
    access_groups: list[str] | None
    # `dot1x <残り>` の残り(空白は1つ)の集合。文字列の昇順(ADR-0060、REQ-71)。秘密の語がある行と子を持つ行は入れない。
    # Cisco の `authentication <残り>`・`mab [<残り>]` と no 付きは行の原文(ADR-0120、REQ-165)。
    # `access-session <残り>` と no 付きも行の原文(ADR-0122 決定4、REQ-167)。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(secondary_addresses と同じ)
    dot1x: list[str] | None
    # `negotiation auto` → True、`no negotiation auto` → False(ADR-0057 決定2)。NX-OS の `negotiate auto`・
    # `no negotiate auto` も同じ(ADR-0116 決定2、REQ-161)。None は行が無い、または読んでいない(mtu と同じ)
    negotiation: bool | None
    # `bandwidth <数>`(kbps。ADR-0057 決定2)。None は行が無い、または読んでいない
    bandwidth: int | None
    # `mac-address <MAC>` の原文(ADR-0057 決定2)。None は行が無い、または読んでいない
    mac_address: str | None
    # `media-type <種類>` の原文(ADR-0057 決定2)。None は行が無い、または読んでいない
    media_type: str | None
    # `standby <残り>` の残りの集合(HSRP。文字列の昇順。ADR-0068 決定2)。認証の行(authentication の語を含む)は入れない。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    hsrp: list[str] | None
    # ポートの守りの設定の行の原文の集合(`storm-control …` など。文字列の昇順。ADR-0068 決定3)。
    # 語に password・secret・key がある行は入れない。None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    port_security: list[str] | None
    # `tunnel <残り>` の残りの集合(文字列の昇順。ADR-0104、REQ-85)。語に password・secret・key がある行
    # (`tunnel key …` など)は入れない。None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    tunnel: list[str] | None
    # DHCP の中継の行(`ip helper-address …`・`ipv6 dhcp relay …`・`ip dhcp relay …`・`dhcp relay …` と no 付き)の
    # 原文(空白は1つ)の集合。文字列の昇順(ADR-0109 決定1、REQ-157)。秘密の語がある行と子を持つ行は入れない。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(qos と同じ)
    dhcp_relay: list[str] | None
    # 固定のアドレスを持たない取り方(ADR-0109 決定2)。`ip address dhcp [<残り>]` → `dhcp [<残り>]`、
    # `ip address unnumbered <if>`(Arista)・`ip unnumbered <if>`(Cisco) → `unnumbered <if>`。2回あれば後の行。
    # None は行が無い、または読んでいない(mtu と同じ)。値があれば type は routed
    address_mode: str | None
    # IP の補助の行(`ipv6 nd …`・`ip redirects`・`ip proxy-arp` など。ADR-0109 決定3。Arista の `dhcp client …`・
    # `ip attached-host …` は ADR-0115 決定4。`ip virtual-reassembly [<残り>]` は ADR-0116 決定3。`ip nat inside`・
    # `ip nat outside`・`ip summary-address …` は ADR-0120、REQ-165。`ip load-sharing …`・`ip mtu <数>` は
    # ADR-0121、REQ-166。`ipv6 dhcp server …` は ADR-0123 決定2、REQ-168)の原文(空白は1つ)の集合。
    # 文字列の昇順。秘密の語がある行と子を持つ行は入れない。None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    ip_options: list[str] | None
    # L1・L2 の補助の行(`cdp …`・`lldp …`・`snmp trap …`・`hold-queue …` など。ADR-0110 決定1。Arista の
    # `port-channel lacp …`・`lacp …`・`link tracking …`・`link-debounce …`・`mac security …` は ADR-0115 決定3。
    # `mop [<残り>]` は ADR-0116 決定3。`stackwise-virtual …` は ADR-0120、REQ-165。`counter [<残り>]` は ADR-0121、
    # REQ-166。`switchport trunk encapsulation …`・`flowcontrol …`・`small-frame …`・`keepalive [<残り>]` は ADR-0128 決定3、
    # REQ-173)の原文(空白は1つ)の集合。子を持つ `cts manual` は `cts manual` と、中の `propagate sgt`・
    # `no propagate sgt`・`policy static sgt <数> [trusted]` を見出しから ` > ` でつないだ文字列にする(中の `sap …` の行と
    # ほかの行は入れない。ADR-0126 決定3、REQ-171)。Arista の `l2 mtu …`・`l2 mru …`・`node-segment …` と no 付きは
    # 行の原文、子を持たない `comment` から `EOF` までの本文の各行は `comment > <行>`(ADR-0133 決定2・3、REQ-178)。
    # 文字列の昇順。今の項目と同じ設定の行(`channel-group auto` は channel_group_auto など)、秘密の語がある行、子を持つ行は入れない。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    port_options: list[str] | None
    # PoE の優先度・見張りの行(`power inline port …`・`power inline police …` と no 付き。ADR-0111 決定3。
    # Arista の `poe …`(`poe disabled` だけの行を除く)と no 付きは ADR-0115 決定1)の原文(空白は1つ)の集合。文字列の昇順。秘密の語がある行と子を持つ行は入れない。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト(qos と同じ)
    poe_options: list[str] | None
    # `channel-group auto` → True、`no channel-group auto` → False(ADR-0111 決定4)。
    # None は行が無い、または読んでいない(mtu と同じ)
    channel_group_auto: bool | None
    # 通信の記録の行(`sflow …`・`flow tracker …`。ADR-0061、REQ-72。Cisco の `ip flow monitor …`・
    # `ipv6 flow monitor …` と no 付きは ADR-0120、REQ-165)の原文の集合。文字列の昇順。
    # 秘密の語がある行と子を持つ行は入れない。None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    traffic_monitoring: list[str] | None
    # マルチキャストの行(`ip pim …`・`ip igmp …`・`ip multicast …`・`ipv6 pim …`・`ipv6 mld …`・`pim ipv4 …`・
    # `pim ipv6 …` と no 付き。ADR-0064、REQ-75。`ip mfib …`・`ipv6 mfib …` と no 付きは ADR-0118、REQ-163。
    # 後ろの無い `ip igmp` は ADR-0133 決定5、REQ-178)の
    # 原文の集合。文字列の昇順。秘密の語がある行と子を持つ行は入れない。
    # None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    multicast: list[str] | None
    # MPLS・PTP・EVPN の行(`mpls …`・`ptp …`・`evpn …` と no 付き。ADR-0108、REQ-89)の原文の集合。文字列の昇順。
    # 子を持つ行(`evpn ethernet-segment` など)は、子を持たない子孫の行を見出しから ` > ` でつないだ文字列にする。
    # 秘密の語がある行は入れない。None は読んでいない(B型・D型)。A型は読んだうえで無ければ空のリスト
    mpls: list[str] | None
    ptp: list[str] | None
    evpn: list[str] | None


class Line(TypedDict):
    """`line con` / `line vty` / `line aux` の1ブロック(REQ-23・REQ-84)。パスワードの値は持たない(has_password だけ)。"""

    type: LineType
    first: int | None
    last: int | None
    # None は line で指定していない(認証は aaa か機器の既定値で決まる)
    login: LoginMode | None
    login_list: str | None
    transport_input: list[str]
    exec_timeout_minutes: int | None
    exec_timeout_seconds: int | None
    privilege_level: int | None
    has_password: bool
    # 入口に当てた ACL の行の原文の集合(`access-class …`・`ipv6 access-class …`。昇順。REQ-84、ADR-0103)
    access_class: list[str]
    # ほかの書式に当たらない行の原文と、子を持つ行の中の道すじ(` > ` でつなぐ)の集合(昇順。REQ-84、ADR-0103)
    options: list[str]


class Vlan(TypedDict):
    id: int
    name: str | None
    # `trunk group <名前>` の名前の集合(昇順)。空は行が無い(ADR-0058、REQ-69)
    trunk_groups: list[str]
    # `state <状態>` の原文(最初の行)。None は行が無い(ADR-0058、REQ-69)
    state: str | None
    # NX-OS の `vn-segment <数>`(最初の行)。None は行が無い(ADR-0058、REQ-69)
    vn_segment: int | None
    # 中のほかの行(`private-vlan …`・`remote-span`・`mtu <数>`・`media <1語>`・`shutdown`・`said <数>` と no 付き)の
    # 原文(空白は1つ)の集合。文字列の昇順。子を持つ行は入れない。空は行が無い(ADR-0126 決定2、REQ-171)
    options: list[str]


class AclEntry(TypedDict):
    seq: int | None
    text: str


class Acl(TypedDict):
    name: str
    type: AclType
    numbered: bool
    entries: list[AclEntry]


class OspfNetwork(TypedDict):
    address: IPv4Address
    wildcard: IPv4Address
    area: IPv4Address


class Ospf(TypedDict):
    process_id: int
    router_id: str | None
    networks: list[OspfNetwork]
    passive_interface_default: bool
    passive_interfaces: list[str]
    non_passive_interfaces: list[str]
    # `router ospf <番号> vrf <名前>` の名前。None は見出しに vrf が無い(ADR-0107、REQ-88)
    vrf: str | None
    # ほかの書式に当たらない行の原文と、子を持つ行の中の道すじ(` > ` でつなぐ)の集合。文字列の昇順
    options: list[str]


class Ospfv3(TypedDict):
    """`router ospfv3 …`・`ipv6 router ospf …` の1ブロック(ADR-0107、REQ-88)。鍵は見出しの原文。"""

    # 中の子を持たない行の、見出しの次の段からの道すじ(` > ` でつなぐ)の集合。文字列の昇順
    settings: list[str]


class Eigrp(TypedDict):
    """`router eigrp <1語>` の1ブロック(ADR-0065、REQ-76)。鍵は `router eigrp` の後ろの語。"""

    # 中の子を持たない行の、見出しの次の段からの道すじ(` > ` でつなぐ)の集合。文字列の昇順
    settings: list[str]


class IpsecBlock(TypedDict):
    # 中の子を持たない行の原文(鍵の語から後ろを外したもの)の集合。文字列の昇順(ADR-0104、REQ-85)。
    # Arista の `ip security` の中の段は、さらに子を持つ行の道すじを ` > ` でつなぐ(ADR-0113、REQ-159)
    settings: list[str]
    # 中に鍵の語(key・password・secret。Arista の `ip security` では shared-key・pre-shared-key も)の行があれば True。
    # 鍵の値は持たない
    keys_configured: bool


class Ipsec(TypedDict):
    """IPsec の VPN の最上位のブロックと行(Cisco IOS の `crypto …`。ADR-0104、REQ-85。
    Arista の `ip security`。ADR-0113、REQ-159)。1台に1つとして、1つの塊で比べる。"""

    # 鍵は見出しから `crypto ` を外した原文(`keyring KR-EXAMPLE` など)か、
    # `ip security ` + `ip security` の中の子を持つ行の原文(`ip security profile PROF-1` など)。
    # `crypto ikev2 authorization policy <名前>` は ADR-0121、REQ-166
    blocks: dict[str, IpsecBlock]
    # 子を持たない最上位の `crypto isakmp|ipsec|ikev2 …`(no 付きは ADR-0121、REQ-166)の原文
    # (鍵を外した行は末尾に ` <key>`)の集合。文字列の昇順
    options: list[str]


class RouteMapEntry(TypedDict):
    """`route-map <名前> permit|deny <番号>` の1つ(ADR-0047、REQ-58)。"""

    action: Literal["permit", "deny"]
    # `description <文>` の <文>。None は行が無い
    description: str | None
    # `match <残り>`・`set <残り>` の <残り>と、ほかの行の原文の集合。文字列の昇順
    match: list[str]
    set: list[str]
    options: list[str]


class RouteMap(TypedDict):
    # 鍵は見出しの番号(str(int(...)))
    entries: dict[str, RouteMapEntry]


class PrefixListEntry(TypedDict):
    """`seq <番号> permit|deny <プレフィックス> [ge|le|eq <長さ>]` の1つ(ADR-0047、REQ-58)。"""

    action: Literal["permit", "deny"]
    # str(ip_network(..., strict=False))
    prefix: str
    ge: int | None
    le: int | None
    eq: int | None


class PrefixList(TypedDict):
    """`ip prefix-list <名前>`・`ipv6 prefix-list <名前>` の1つ(ADR-0047、REQ-58)。"""

    description: str | None
    # 鍵は seq の番号(str(int(...)))
    entries: dict[str, PrefixListEntry]


class RouteList(TypedDict):
    """community-list・as-path のリストの1つ(ADR-0062、REQ-73)。"""

    # standard / expanded / regexp。そのリストで最初に書かれた種類。書かれていなければ None
    type: str | None
    # `permit|deny` から後ろの原文。書かれた順(集合にしない。同じ文字列も重ねる)
    entries: list[str]


class Vxlan(TypedDict):
    """VTEP のインターフェース1つの VXLAN の設定(Arista の `interface Vxlan<…>`・NX-OS の `interface nve<…>`。
    ADR-0053、REQ-64)。鍵はインターフェースの名前。インターフェースそのものの設定は interfaces が持つ。
    """

    source_interface: str | None
    udp_port: int | None
    # VLAN の番号(str(int(...)))→ VNI。範囲で書いた形は展開しない(未解釈。決定3)
    vlan_vnis: dict[str, int]
    # VRF の名前 → VNI
    vrf_vnis: dict[str, int]
    # NX-OS の `member vni` の番号(str(int(...)))→ 中の子を持たない行の原文と associate-vrf の集合。文字列の昇順
    members: dict[str, list[str]]
    # ほかの子を持たない行(Arista は `vxlan ` を外した残り、NX-OS は原文)の集合。文字列の昇順
    options: list[str]


class Mlag(TypedDict):
    """Arista の `mlag configuration` の中の設定(ADR-0054、REQ-65)。1台に1つとして、1つの塊で比べる。"""

    # `domain-id`・`local-interface`・`peer-address`・`peer-link` の後ろの1語(原文)。行が無ければ None
    domain_id: str | None
    local_interface: str | None
    peer_address: str | None
    peer_link: str | None
    # ほかの子を持たない行(`peer-address heartbeat …` を含む)の原文の集合。文字列の昇順
    options: list[str]


class IsisAddressFamily(TypedDict):
    """`router isis` の中の `address-family <種類…>` のブロック1つ(ADR-0056、REQ-67)。鍵は種類の語を空白1つでつないだ原文。"""

    # 中の子を持たない行の原文(空白は1つ)の集合。文字列の昇順
    options: list[str]


class Isis(TypedDict):
    """Arista の `router isis <タグ>` の1ブロック(ADR-0056、REQ-67)。鍵はタグ。認証の鍵の値は持たない。"""

    # `net <NET>` の NET(原文)の集合。文字列の昇順
    net: list[str]
    # `is-type <種類>` の種類(原文)。2回あれば後の行。行が無ければ None
    is_type: str | None
    # `router-id ipv4 <アドレス>` のアドレス(原文)。2回あれば後の行。行が無ければ None
    router_id: str | None
    address_families: dict[str, IsisAddressFamily]
    # ほかの子を持たない行の原文(空白は1つ)と、`authentication key …` の `authentication key` の集合。文字列の昇順
    options: list[str]


class Bfd(TypedDict):
    """最上位の `router bfd` と `bfd <残り>` の設定(ADR-0059、REQ-70)。1台に1つとして、1つの塊で比べる。認証の鍵の行は持たない。"""

    # `router bfd` の中の子を持たない行の原文(空白は1つ)と、最上位の `bfd <残り>` の残りの集合。文字列の昇順
    options: list[str]


class RouteLists(TypedDict):
    """community-list・as-path のリスト(ADR-0062、REQ-73)。1台に1つとして、1つの塊で比べる。鍵はリストの名前。"""

    community: dict[str, RouteList]
    extcommunity: dict[str, RouteList]
    large_community: dict[str, RouteList]
    as_path: dict[str, RouteList]


class TrafficMonitoring(TypedDict):
    """通信の記録の設定(sFlow・flow tracking。ADR-0061、REQ-72)。1台に1つとして、1つの塊で比べる。"""

    # 最上位の `sflow …` の行の原文と、`flow tracking <種類>` のブロックの中の子を持たない行の、見出しからの道すじを
    # ` > ` でつないだ文字列の集合。文字列の昇順
    settings: list[str]


class Multicast(TypedDict):
    """マルチキャストの設定(IGMP snooping・PIM・マルチキャストの経路。ADR-0064、REQ-75)。1台に1つとして、1つの塊で比べる。"""

    # 最上位の `ip igmp snooping …`・`ip multicast-routing …`・`ip pim …`(no 付きも)の行と、
    # `ipv6 multicast-routing …`・`ipv6 pim …`・`ipv6 mld …`・`ip igmp …`・`ip multicast …`・`ip msdp …`
    # (no 付きも。ADR-0118、REQ-163)・`ipv6 multicast …`(no 付きも。ADR-0121、REQ-166)の行の原文と、
    # `router multicast`・`router pim [<方式>]`・`router igmp` のブロックの中の子を持たない行の、見出しからの道すじを
    # ` > ` でつないだ文字列の集合。文字列の昇順
    settings: list[str]


class Mpls(TypedDict):
    """MPLS の設定(LDP・RSVP・TE。ADR-0108、REQ-89)。1台に1つとして、1つの塊で比べる。"""

    # 最上位の `mpls …`(no 付きも)・`router traffic-engineering`・`router ldp`・`router rsvp` の、子を持たない行の原文と、
    # 子を持つ行の中の子を持たない行の、見出しからの道すじを ` > ` でつないだ文字列の集合。文字列の昇順
    settings: list[str]


class Ptp(TypedDict):
    """PTP の設定(ADR-0108、REQ-89)。1台に1つとして、1つの塊で比べる。"""

    # 最上位の `ptp …`(no 付きも)の、子を持たない行の原文と、子を持つ行の中の子を持たない行の、見出しからの道すじを
    # ` > ` でつないだ文字列の集合。文字列の昇順
    settings: list[str]


class SdwanBlock(TypedDict):
    """SD-WAN の項目1つ。Arista は最上位のブロック1つ(`router path-selection` など。ADR-0112、REQ-159)で、鍵は見出しの原文。
    FortiGate は `config system sdwan`・`config system virtual-wan-link` の段1つ(ADR-0139、REQ-194)で、鍵は
    `sdwan members 1` など。
    """

    # Arista: 中の子を持たない行の、見出しの次の段からの道すじ(` > ` でつなぐ)の集合。
    # FortiGate: その段の行(`<欄> <値…>`・`unset <欄>`。入れ子は ` > ` でつなぐ道すじ)の集合。どちらも文字列の昇順
    settings: list[str]


class BgpNeighbor(TypedDict):
    """BGP の相手1つ(ADR-0038 決定3)。パスワードの値は持たない(password_configured だけ。決定6)。"""

    kind: BgpNeighborKind
    remote_as: int | None
    # 所属するピアグループ(kind が address のとき)。ピアグループから受け継ぐ設定は解かない(決定7)
    peer_group: str | None
    description: str | None
    update_source: str | None
    shutdown: bool
    password_configured: bool
    # 固定キーに無い行の残り(`send-community both` など)。比較では集合として扱う(決定4)
    options: list[str]


class BgpAfNeighbor(TypedDict):
    """address-family の中の相手1つ(ADR-0045 決定3、REQ-56)。属性は行の残りの文字列の集合だけ。"""

    # `activate`・`no activate`・`send-community` など。文字列の昇順。比較では集合として扱う
    options: list[str]


class BgpAddressFamily(TypedDict):
    """`address-family <種類…>` のブロック1つ(ADR-0045、REQ-56)。鍵は種類の語を空白1つでつないだ原文。"""

    # キーは第1段と同じ相手の鍵(アドレスの正規の書き方か、ピアグループ名)
    neighbors: dict[str, BgpAfNeighbor]
    # 中のほかの行の原文(`network …` など)。文字列の昇順
    options: list[str]


class BgpVrf(TypedDict):
    """`router bgp` の中の `vrf <名前>` のブロック1つ(ADR-0045 決定1・2、REQ-56)。"""

    rd: str | None
    # `route-target` の後ろの原文(`import evpn 10:10` など)。文字列の昇順
    route_targets: list[str]
    router_id: str | None
    # 第1段と同じ形(パスワードの値は持たない)
    neighbors: dict[str, BgpNeighbor]
    # Cisco の `address-family … vrf <名前>` もここに置く
    address_families: dict[str, BgpAddressFamily]
    options: list[str]


class BgpEvpnInstance(TypedDict):
    """`router bgp` の直下の `vlan`・`vlan-aware-bundle`・`vpws` のブロック1つ(ADR-0046、REQ-57)。鍵は見出しの原文。"""

    rd: str | None
    # `route-target` の後ろの原文(`both 10:10010` など)。文字列の昇順
    route_targets: list[str]
    # 中のほかの子を持たない行の原文(`redistribute learned`・`vlan 10-20` など)。文字列の昇順
    options: list[str]


class Bgp(TypedDict):
    """BGP のプロセス(ADR-0038 決定1)。1台に1つとして、1つの塊で比べる。"""

    local_as: int
    router_id: str | None
    # キーはアドレスの正規の書き方か、ピアグループ名
    neighbors: dict[str, BgpNeighbor]
    # 直下のほかの子を持たない行の原文(REQ-56)。文字列の昇順。
    # Si-R は最上位のほかの `bgp <残り>` の <残り>(空白は1つ。ADR-0121 決定3、REQ-166)
    options: list[str]
    address_families: dict[str, BgpAddressFamily]
    # VRF ごとの BGP(vrfs カテゴリには入れない。ADR-0045)
    vrfs: dict[str, BgpVrf]
    # EVPN の `vlan <1語>`・`vlan-aware-bundle <1語>`・`vpws <1語>`(REQ-57)。鍵は見出しの原文(空白は1つ)
    evpn_instances: dict[str, BgpEvpnInstance]


class SpanningTree(TypedDict):
    """スパニングツリーの全体の行(ADR-0042 決定1)。1台に1つとして、1つの塊で比べる。mode は原文の語(決定3)。"""

    mode: str | None
    extend_system_id: bool | None
    # 固定の項目に無い行の残り(no 付きは `no <残り>`)。集合として比べ、文字列の昇順に持つ。
    # `spanning-tree mst configuration` のブロックの子は `mst configuration > <子>`(ADR-0122 決定3、REQ-167)
    options: list[str]


class QosClassMap(TypedDict):
    type: str | None
    # match-any / match-all。見出しに無ければ None
    match_type: str | None
    # 中の子を持たない行の、見出しの次の段からの道すじ(` > ` でつなぐ)の集合。文字列の昇順
    settings: list[str]


class QosPolicyMap(TypedDict):
    type: str | None
    # class_maps の settings と同じ形。`class` の並びは持たない(ADR-0066 決定3)
    settings: list[str]


class QosTableMap(TypedDict):
    settings: list[str]


class Qos(TypedDict):
    """QoS の最上位のブロックと行(ADR-0066、REQ-77)。1台に1つとして、1つの塊で比べる。"""

    class_maps: dict[str, QosClassMap]
    policy_maps: dict[str, QosPolicyMap]
    table_maps: dict[str, QosTableMap]
    # control-plane の中の行の道すじの集合。文字列の昇順
    control_plane: list[str]
    # 子を持たない最上位の `qos …`・`mls qos …`・`auto qos …`(no 付きも)の原文の集合。文字列の昇順
    options: list[str]


class Vrf(TypedDict):
    name: str
    rd: str | None
    address_families: list[str]
    # 直下の `description <文>` の残り。2回あれば後の行(ADR-0105、REQ-86)
    description: str | None
    # 直下の `vni <数>`。2回あれば後の行
    vni: int | None
    # `route-target <残り>` の残り(子を持つ行の中は `<道すじ> > <残り>`)の集合。文字列の昇順
    route_targets: list[str]
    # ほかの書式に当たらない行の原文と、子を持つ行の中の道すじ(` > ` でつなぐ)の集合。文字列の昇順
    options: list[str]


class StaticRoute(TypedDict):
    """スタティックルート1本（ADR-0026 決定3）。索引は持たない。"""

    destination: IPv4Network | IPv6Network
    next_hop: IPv4Address | IPv6Address | None
    interface: str | None
    vrf: str | None
    distance: int | None
    enabled: bool | None


class SnmpCommunity(TypedDict):
    name: str
    # "RO"・"RW"(小文字の書き方も大文字にそろえる)か、NX-OS の "group <役割>"(ADR-0102 決定1)
    access: str
    acl: str | None
    # `… view <ビュー> ro|rw` のビューの名前。None は view の語が無い(REQ-83)
    view: str | None


class SnmpHost(TypedDict):
    host: str
    community: str
    options: str | None


class SnmpUser(TypedDict):
    """SNMPv3 の利用者1人(ADR-0102 決定2、REQ-83)。鍵の値は持たない(auth_configured・priv_configured だけ)。"""

    name: str
    # 行の残りを、最初の auth・priv より後ろは方式の名前だけにした文字列の集合。重ねない。昇順
    settings: list[str]
    auth_configured: bool
    priv_configured: bool


class Snmp(TypedDict):
    communities: list[SnmpCommunity]
    location: str | None
    contact: str | None
    hosts: list[SnmpHost]
    # v3 の利用者を出現順に(名前を項目に持つ要素の一覧。communities と同じ形。ADR-0102 決定2)
    users: list[SnmpUser]
    # `snmp-server group <残り>`・`snmp-server view <残り>` の <残り> の集合。重ねない。昇順(決定3)
    groups: list[str]
    views: list[str]
    # ほかの `snmp-server …`・`snmp …` の行の原文の集合。重ねない。昇順(no 付きは ADR-0121、REQ-166)
    options: list[str]


class Aaa(TypedDict):
    new_model: bool
    lines: list[str]


# secret は一方向のハッシュ、password は戻せる型か平文、none は `nopassword`(ADR-0049 決定1)
CredentialKind = Literal["secret", "password", "none"]


class User(TypedDict):
    """ローカルの利用者1人(ADR-0049)。credential が none のときは secret_type・secret が None。"""

    name: str
    privilege: int | None
    # `role <役割>`(Arista・NX-OS)。None は行に無い
    role: str | None
    credential: CredentialKind
    secret_type: str | None
    secret: str | None


class EnableCredential(TypedDict):
    """`enable secret` / `enable password` の型と値(ADR-0049 決定2)。type は書かれていなければ None。"""

    type: str | None
    value: str


class Certificate(TypedDict):
    kind: CertificateKind | None
    serial: str
    hash_algorithm: Literal["sha256"]
    hash_input_version: Literal[1]
    hash: str


class CertificateChain(TypedDict):
    name: str
    certificates: list[Certificate]


class Dns(TypedDict):
    # 「正規のアドレス[ vrf <V>][ <残り>]」の集合。重ねない。昇順(ADR-0050 決定3、REQ-61)。
    # FortiGate は「正規のアドレス <欄>」(欄は primary・secondary・ip6-primary・ip6-secondary。REQ-196)
    servers: list[str]
    # `ip domain lookup …`・`ip domain-list …` などの行の原文の集合。重ねない。昇順
    # (`ip domain-lookup <残り>`・`ip domain multicast|periodic-resolve|recursive|round-robin …`・`ip hostname strict` は
    # ADR-0121、REQ-166)
    options: list[str]


class ManagementBlock(TypedDict):
    # ブロックの中の行の原文(子を持つ行は見出しの次の段から ` > ` でつなぐ)の集合。重ねない。昇順
    settings: list[str]


class Management(TypedDict):
    # `ip http …`・`ip ssh …`・`ip scp …`・`nxapi …`(no 付きも)の行の原文の集合。重ねない。昇順(ADR-0051、REQ-62)
    settings: list[str]
    # Arista の `management <名前…>` の <名前…> → 中の行(`management security` も。ADR-0114 決定1、REQ-159)。
    # IOS-XE の `ip ssh server certificate profile` は、この原文を鍵にする(ADR-0121、REQ-166)
    blocks: dict[str, ManagementBlock]


class Trustpoint(TypedDict):
    # `crypto pki trustpoint <名前>` の中の子を持たない行の原文の集合。重ねない。昇順(ADR-0052 決定1、REQ-63)
    settings: list[str]


class Vtp(TypedDict):
    # `vtp mode <方式>` の方式の語。None は行が無い(ADR-0052 決定2)
    mode: str | None
    # ほかの `vtp <残り>` の行の <残り> の集合。重ねない。昇順
    options: list[str]


class AaaServer(TypedDict):
    # 中の行の原文と、1行の書き方の `key` より前の残りの集合。重ねない。昇順(ADR-0070 決定1)
    settings: list[str]
    # `key …`・`pac key …` があれば True。鍵の値は持たない(決定2)
    key_configured: bool


class AaaServerGroup(TypedDict):
    # 中の子を持たない行の原文の集合。重ねない。昇順
    settings: list[str]


class AaaServers(TypedDict):
    # 鍵は "<tacacs|radius> <名前またはアドレス>"。ブロックと1行の書き方を同じ項目にまとめる
    servers: dict[str, AaaServer]
    # 鍵は "<種類> <名前>"(`aaa group server <種類> <名前>`)
    groups: dict[str, AaaServerGroup]
    # ほかの `ip radius …`・`ip tacacs …`・`radius-server …`・`tacacs-server …`(no 付きも)の原文の集合。昇順
    options: list[str]


class Global(TypedDict):
    hostname: str | None
    domain_name: str | None
    timezone: str | None
    # 「アドレス[ vrf <V>][ <残り>]」を出現順に。重ねない(ADR-0050 決定1、REQ-61)
    ntp_servers: list[str]
    logging_hosts: list[str]
    snmp: Snmp
    aaa: Aaa
    users: dict[str, User]
    # None は行が無い、または `no enable secret` / `no enable password`(ADR-0049 決定2)
    enable_secret: EnableCredential | None
    enable_password: EnableCredential | None
    certificate_chains: dict[str, CertificateChain]
    # 機器全体の設定の行の原文の集合(ブロックの中は見出しから ` > ` でつなぐ)。重ねない。昇順(ADR-0069、REQ-80。
    # `ip cef`・`netconf-yang`・`alias …` などの語は ADR-0116 決定3、REQ-161。`stack-mac …` の行と `stack-power …` の
    # ブロックは ADR-0120、REQ-165。`subscriber …`・`dot1x …`・`ip access-list persistent` などの語と
    # `ethernet cfm <残り>`・`ip sla server <残り>` のブロックは ADR-0121、REQ-166。IBNS 2.0 の
    # `class-map type control …`・`policy-map type control …`・`service-template …`、`ipv6 nd raguard policy …`・
    # `device-tracking policy …`・`vlan access-map …` のブロックと `track …` の行は ADR-0122 決定1・2、REQ-167。
    # `fhrp …`・`gnmi-yang`・`gnxi`・`iox`・`vlan filter …` の行と `ipv6 dhcp pool <残り>`・`sampler <残り>`・
    # `app-hosting <残り>`・`router lisp[ <残り>]`・`gnmi-yang <残り>`・`gnxi <残り>` のブロックは ADR-0123 決定1、REQ-168。
    # `udld …`・`switch <数> priority …`・`privilege …` の行と `ip sla <数>`・`track <数> …`・
    # `object-group network|service <名前>`・`time-range <名前>` のブロックは ADR-0128 決定1、REQ-173。`key chain` は読まない)
    device_settings: list[str]
    # ほかの `ntp …`・`logging …` の行の残りの集合。重ねない。昇順(ADR-0050、REQ-61)。
    # `no ntp <残り>` は `no <残り>`(no logging と同じ持ち方。ADR-0121、REQ-166)。
    # FortiGate は `config system ntp` の行の道すじ(`ntpserver > 1 > server 192.0.2.1`。秘密の欄は名前だけ。REQ-196)
    ntp_options: list[str]
    logging_options: list[str]
    dns: Dns
    # `ip routing …`・`ipv6 unicast-routing …`(no 付きも)の行の原文の集合。重ねない。昇順(ADR-0050 決定4)
    routing: list[str]
    # `ip default-gateway <IPv4>`。None は行が無い
    default_gateway: str | None
    # 管理の入口の行の原文と、Arista の `management <名前…>` のブロック(ADR-0051、REQ-62)
    management: Management
    # NX-OS の `feature <名前…>` は `<名前…>`、`no feature <名前…>` は `no <名前…>` の集合。重ねない。昇順
    features: list[str]
    # trustpoint の名前 → 中の行。証明書チェーン(certificate_chains)とは別の項目(ADR-0052、REQ-63)
    trustpoints: dict[str, Trustpoint]
    vtp: Vtp
    # `system mtu <残り>` の <残り> の集合。重ねない。昇順
    system_mtu: list[str]
    # `service …`・`no service …` の行の原文の集合。重ねない。昇順(ADR-0063、REQ-74)
    services: list[str]
    # `vlan internal <残り>` の <残り>。None は行が無い
    vlan_internal: str | None
    # `ip virtual-router mac-address <MAC>` の <MAC>。None は行が無い
    virtual_router_mac: str | None
    # 認証サーバー(TACACS+・RADIUS)とサーバーのグループ(ADR-0070、REQ-81)。鍵の値は持たない
    aaa_servers: AaaServers
    # バナーの種類(`banner` の次の語) → 区切りの間の本文(前後の改行を外す。ADR-0116 決定1、REQ-161)。
    # 区切りの違い(`^C`・`#` など)は持たない。秘密の語がある本文は入れない。B型・D型は空の辞書
    banners: dict[str, str]


IR = TypedDict(
    "IR",
    {
        "global": Global,
        "vlans": dict[str, Vlan],
        "interfaces": dict[str, Interface],
        "acls": dict[str, Acl],
        "ospf": dict[str, Ospf],
        "bgp": Bgp | None,
        "spanning_tree": SpanningTree | None,
        "vrfs": dict[str, Vrf],
        "static_routes": list[StaticRoute],
        "lines": dict[str, Line],
        "qos": Qos | None,
        # IPv6・MAC の ACL(ADR-0101 決定2、REQ-82)。形は acls と同じ。名前の空間が別なのでカテゴリを分ける
        "ipv6_acls": dict[str, Acl],
        "mac_acls": dict[str, Acl],
        # OSPFv3(ADR-0107、REQ-88)。鍵は見出しの原文。B型・D型は空の辞書
        "ospfv3": dict[str, Ospfv3],
        # EIGRP(ADR-0065、REQ-76)。鍵は `router eigrp` の後ろの語。B型・D型は空の辞書
        "eigrp": dict[str, Eigrp],
        # IPsec の VPN(ADR-0104、REQ-85)。行の無い config と B型・D型は None
        "ipsec": Ipsec | None,
        # 経路の方針(ADR-0047、REQ-58)。鍵は名前。IPv4 と IPv6 の prefix-list は名前の空間が別。B型は空の辞書。
        # D型は FortiGate の最上位の `config router route-map`・`prefix-list`・`prefix-list6` のリスト(REQ-195)
        "route_maps": dict[str, RouteMap],
        "prefix_lists": dict[str, PrefixList],
        "ipv6_prefix_lists": dict[str, PrefixList],
        # VXLAN(ADR-0053、REQ-64)。鍵はインターフェースの名前。B型・D型は空の辞書
        "vxlan": dict[str, Vxlan],
        # MLAG(ADR-0054、REQ-65)。行の無い config と B型・D型は None
        "mlag": Mlag | None,
        # IS-IS(ADR-0056、REQ-67)。鍵はタグ。B型・D型は空の辞書
        "isis": dict[str, Isis],
        # BFD の全体の設定(ADR-0059、REQ-70)。行の無い config と B型・D型は None
        "bfd": Bfd | None,
        # community-list・as-path のリスト(ADR-0062、REQ-73)。行の無い config と B型・D型は None
        "route_lists": RouteLists | None,
        # 通信の記録(ADR-0061、REQ-72)。行の無い config と B型・D型は None
        "traffic_monitoring": TrafficMonitoring | None,
        # マルチキャスト(ADR-0064、REQ-75)。行の無い config と B型・D型は None
        "multicast": Multicast | None,
        # SD-WAN。Arista(ADR-0112、REQ-159)は鍵が見出しの原文。D型(FortiGate。ADR-0139、REQ-194)は鍵が `<S>`・
        # `<S> <表>`・`<S> <表> <キー>`・`<S> service-order`(<S> は sdwan か virtual-wan-link)。B型は空の辞書
        "sdwan": dict[str, SdwanBlock],
        # MPLS・PTP(ADR-0108、REQ-89)。行の無い config と B型・D型は None
        "mpls": Mpls | None,
        "ptp": Ptp | None,
        "vendor_specific": dict[str, Any],
        "unparsed": list[tuple[str, ...]],
    },
)


def unset_marker() -> dict[str, bool]:
    """「明示的に未設定」の印(FortiGate の `unset <attr>`。ADR-0025 決定4)。

    値・この印・None(行が無い)の3状態を区別する。使うのは vendor_specific の固定キーだけで、
    共通スキーマのキーには入れない(型を崩さないため。docs/notes/ir-schema-keys.md)。
    """
    return {"unset": True}


def is_unset(value: Any) -> bool:
    """unset_marker() の印か。"""
    return value == {"unset": True}


def new_global() -> Global:
    """既定値で埋めた Global を返す。users の secret などは読み取った値をそのまま保持する(ADR-0004)。"""
    return {
        "hostname": None,
        "domain_name": None,
        "timezone": None,
        "ntp_servers": [],
        "logging_hosts": [],
        "snmp": {
            "communities": [],
            "location": None,
            "contact": None,
            "hosts": [],
            "users": [],
            "groups": [],
            "views": [],
            "options": [],
        },
        "aaa": {"new_model": False, "lines": []},
        "users": {},
        "enable_secret": None,
        "enable_password": None,
        "certificate_chains": {},
        "device_settings": [],
        "ntp_options": [],
        "logging_options": [],
        "dns": {"servers": [], "options": []},
        "routing": [],
        "default_gateway": None,
        "management": {"settings": [], "blocks": {}},
        "features": [],
        "trustpoints": {},
        "vtp": {"mode": None, "options": []},
        "system_mtu": [],
        "services": [],
        "vlan_internal": None,
        "virtual_router_mac": None,
        "aaa_servers": {"servers": {}, "groups": {}, "options": []},
        "banners": {},
    }


def new_vlan(vlan_id: int) -> Vlan:
    return {"id": vlan_id, "name": None, "trunk_groups": [], "state": None, "vn_segment": None, "options": []}


def new_vrf(name: str) -> Vrf:
    return {
        "name": name,
        "rd": None,
        "address_families": [],
        "description": None,
        "vni": None,
        "route_targets": [],
        "options": [],
    }


def new_ospf(process_id: int) -> Ospf:
    return {
        "process_id": process_id,
        "router_id": None,
        "networks": [],
        "passive_interface_default": False,
        "passive_interfaces": [],
        "non_passive_interfaces": [],
        "vrf": None,
        "options": [],
    }


def new_ipsec() -> Ipsec:
    return {"blocks": {}, "options": []}


def new_route_map_entry(action: Literal["permit", "deny"]) -> RouteMapEntry:
    return {"action": action, "description": None, "match": [], "set": [], "options": []}


def new_prefix_list() -> PrefixList:
    return {"description": None, "entries": {}}


def new_vxlan() -> Vxlan:
    return {"source_interface": None, "udp_port": None, "vlan_vnis": {}, "vrf_vnis": {}, "members": {}, "options": []}


def new_mlag() -> Mlag:
    return {"domain_id": None, "local_interface": None, "peer_address": None, "peer_link": None, "options": []}


def new_isis() -> Isis:
    return {"net": [], "is_type": None, "router_id": None, "address_families": {}, "options": []}


def new_bfd() -> Bfd:
    return {"options": []}


def new_route_lists() -> RouteLists:
    return {"community": {}, "extcommunity": {}, "large_community": {}, "as_path": {}}


def new_bgp(local_as: int) -> Bgp:
    return {
        "local_as": local_as,
        "router_id": None,
        "neighbors": {},
        "options": [],
        "address_families": {},
        "vrfs": {},
        "evpn_instances": {},
    }


def new_bgp_vrf() -> BgpVrf:
    return {"rd": None, "route_targets": [], "router_id": None, "neighbors": {}, "address_families": {}, "options": []}


def new_bgp_evpn_instance() -> BgpEvpnInstance:
    return {"rd": None, "route_targets": [], "options": []}


def new_bgp_address_family() -> BgpAddressFamily:
    return {"neighbors": {}, "options": []}


def new_bgp_af_neighbor() -> BgpAfNeighbor:
    return {"options": []}


def new_bgp_neighbor(kind: BgpNeighborKind) -> BgpNeighbor:
    return {
        "kind": kind,
        "remote_as": None,
        "peer_group": None,
        "description": None,
        "update_source": None,
        "shutdown": False,
        "password_configured": False,
        "options": [],
    }


def new_spanning_tree() -> SpanningTree:
    return {"mode": None, "extend_system_id": None, "options": []}


def new_qos() -> Qos:
    return {"class_maps": {}, "policy_maps": {}, "table_maps": {}, "control_plane": [], "options": []}


def new_line(type_: LineType, first: int | None, last: int | None) -> Line:
    return {
        "type": type_,
        "first": first,
        "last": last,
        "login": None,
        "login_list": None,
        "transport_input": [],
        "exec_timeout_minutes": None,
        "exec_timeout_seconds": None,
        "privilege_level": None,
        "has_password": False,
        "access_class": [],
        "options": [],
    }


def new_ir() -> IR:
    """すべてのカテゴリを既定値で用意した IR を返す。"""
    return {
        "global": new_global(),
        "vlans": {},
        "interfaces": {},
        "acls": {},
        "ospf": {},
        "bgp": None,
        "spanning_tree": None,
        "vrfs": {},
        "static_routes": [],
        "lines": {},
        "qos": None,
        "ipv6_acls": {},
        "mac_acls": {},
        "ospfv3": {},
        "eigrp": {},
        "ipsec": None,
        "route_maps": {},
        "prefix_lists": {},
        "ipv6_prefix_lists": {},
        "vxlan": {},
        "mlag": None,
        "isis": {},
        "bfd": None,
        "route_lists": None,
        "traffic_monitoring": None,
        "multicast": None,
        "sdwan": {},
        "mpls": None,
        "ptp": None,
        "vendor_specific": {},
        "unparsed": [],
    }


def new_switchport() -> Switchport:
    return {
        "mode": None,
        "access_vlan": None,
        "native_vlan": None,
        "trunk_allowed_vlan": (),
        "trunk_allowed_none": False,
        "voice_vlan": None,
        "trunk_groups": [],
        "phone": [],
    }


def new_interface(name: str) -> Interface:
    """既定値で埋めた Interface を返す。

    type は名前だけで決まる仮の値で、routed かどうかは子行を読んだ後に extractor が決める。
    """
    return {
        "type": "switchport",
        "parent_interface": name.split(".", 1)[0] if "." in name else None,
        "description": None,
        "shutdown": False,
        "vrf": None,
        "ip_address": None,
        "secondary_addresses": None,
        "switchport": new_switchport(),
        "poe": None,
        "speed": None,
        "duplex": None,
        "channel_group": None,
        "vrrp": None,
        "mtu": None,
        "ipv6_addresses": None,
        "ipv6_enabled": None,
        "virtual_addresses": None,
        "mlag_id": None,
        "ospf": None,
        "bfd": None,
        "encapsulation": None,
        "autostate": None,
        "isis": None,
        "spanning_tree": None,
        "qos": None,
        "access_groups": None,
        "dot1x": None,
        "negotiation": None,
        "bandwidth": None,
        "mac_address": None,
        "media_type": None,
        "hsrp": None,
        "port_security": None,
        "tunnel": None,
        "dhcp_relay": None,
        "address_mode": None,
        "ip_options": None,
        "port_options": None,
        "poe_options": None,
        "channel_group_auto": None,
        "traffic_monitoring": None,
        "multicast": None,
        "mpls": None,
        "ptp": None,
        "evpn": None,
    }
