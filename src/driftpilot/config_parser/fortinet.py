"""FortiGate の木(fortinet_tree)を IR の interfaces・static_routes・bgp・ospf・ipsec・sdwan・route_maps・prefix_lists・
ipv6_prefix_lists・global(hostname・dns・ntp_options)と vendor_specific の firewall・config に写す extractor(REQ-22・
REQ-31・REQ-45・REQ-50・REQ-164・REQ-174・REQ-191・REQ-192・REQ-193・REQ-194・REQ-195・REQ-196。
ADR-0025 決定4・決定9、ADR-0026 決定3・5・6、ADR-0037、ADR-0040、ADR-0047、ADR-0119、ADR-0129、ADR-0139)。

専用の読み方で写すのは最上位の `config system interface`・`config router static`・`config router static6`・
`config router bgp`(`set as` が読めるとき)・`config router ospf`(入れ子の中も含めて set・unset・edit の行が
あるとき)・`config vpn ipsec phase1`・`phase2`・`phase1-interface`・`phase2-interface`・`config system sdwan`・
`config system virtual-wan-link`(入れ子の中も含めて set・unset・edit の行があるとき)・`config router prefix-list`・
`config router prefix-list6`・`config router route-map`(写せるリストがあるとき)・`config system dns`・`config system ntp`と、
ファイアウォールの11のブロック(下記)。最上位の `config system global` は汎用の形で読み、直下の `set hostname` だけを
global.hostname に写す(下記)。
その edit の中の、表に当たらない行(入れ子の `config ipv6` / `config secondaryip` を含む)は、汎用の形の行(下記)で
その edit の other に写す(ADR-0119 決定1)。ただし表が持つ欄(_INTERFACE_FIELDS・ファイアウォールの表の欄と uuid・
_ROUTE_FIELDS)の、表に当たらない形は other に入れず未解釈に残す(決定2。同じ設定が2か所の差分にならないように)。
ほかの最上位のブロック(`config firewall address6`・`config vdom` などを含む)は汎用の形で写す(下記)。

設定ファイルの見出しの注記(ADR-0129 決定1): 最上位の、子を持たない `#config-version=<値>`・`#buildno=<値>`・
`#global_vdom=<値>` を vendor_specific["fortinet"]["header"] の config_version・buildno・global_vdom に原文で写す
(行が無ければ {})。`#conf_file_ver=<数字>` の行と `#config-version=` の `:user=<…>` は、木を組む前に消える
(fortinet_tree)。ほかの `#` の行は未解釈。
行の判定は Cisco 側と同じ Pattern と apply_first で行い(ADR-0011)、書式に合って写せた行だけを解釈済みにする。

- 共通スキーマに無いもの(vdom・FortiOS の type・mode など)は vendor_specific["fortinet"]["interfaces"][名前] に置く。
  キーは VENDOR_KEYS で固定。行が無ければ None(other も、写す行が無ければ None)
- `unset <attr>` は、vendor_specific の固定キーのうち UNSETTABLE のものだけ「明示的に未設定」の印にする(決定4)。
  共通キーに写す属性(ip / description / status / interface / type)の unset は、型を崩さないよう未解釈のまま残す
  (黙って None にしない。未解釈なら差分に出るので、3状態は潰れない)
- 値の `''` は None(FortiOS が空の値をこう表示する)
- 同じ属性が2回あるときは後の行の値を使う。重複そのものは木が警告(duplicate_line)にする

経路(`config router static` の edit 1つが1本):
- `set dst` / `set gateway` / `set device` / `set distance <数>` / `set status enable|disable` を表で読む。
  表が持たない欄の `set`・`unset <1語>` は、経路の詳細(vendor_specific["fortinet"]["static_routes"][i])の other に写す
- 入れ子の config、表が持つ欄の読めない形(`set distance abc`・`unset gateway` など)、`set` でも `unset <1語>` でもない行が
  1行でもある edit は、まるごと写さない。行はすべて未解釈に残る
- `set dst` が無ければ 0.0.0.0/0(ADR-0026 決定6)。gateway と device がどちらも無い edit は、other に
  `set blackhole enable`・`set virtual-wan-link enable`・値のある `set sdwan-zone` のどれかがあるときだけ写す
  (next_hop・interface は None。ADR-0129 決定2)。ほかは写さない
- `config router static6` も同じ手順で読む(dst は `<IPv6>/<長さ>`、無ければ ::/0。gateway は IPv6)。経路の詳細に
  "static6": True を置く(IPv4 の経路にはキーを置かない。ADR-0129 決定3)
- 同じキーの edit が2つ以上あり(重なった `config router static` を含む)、1つでも写さないものがあれば、そのキーの edit は
  どれも写さない(解釈済みの記録は行をパスで識別するため、写した側の印で写さなかった行が解釈済みに見えてしまう)
- edit のキーの原文は vendor_specific["fortinet"]["static_routes"](static_routes と同じ並び)に置く。突き合わせには使わない。
  other は写す行があるときだけ置く
- 通常の出力(distance の行が無い)と `show full-configuration` の出力(`set distance 10` が付く)は、distance が違う差分になる
  (既定値を推測しない。ADR-0119 決定3)

ファイアウォール(REQ-45・REQ-196。ADR-0037 決定2〜4、ADR-0139 決定3)。最上位の `config firewall policy` / `address` /
`addrgrp` / `service custom` / `service group` / `vip` / `vip6` / `vipgrp` / `ippool` / `ippool6` / `central-snat-map` の
edit を vendor_specific["fortinet"]["firewall"] の policies / addresses / address_groups / services / service_groups / vips /
vips6 / vip_groups / ippools / ippools6 / central_snat に写す(共通スキーマには写さない。ブロックが無くても空の形で置く):
- 表に無い行(`set nat enable`・入れ子の config など)は、汎用の形でその edit の other に写す。写す行が無ければ other のキーは
  置かない(ADR-0119 決定1)。表の欄の `unset <欄>`(1語)は、その欄を「明示的に未設定」の印にする(決定4)
- 持つキーは edit の中に行があった項目だけ。例外はポリシーの status で、行が無ければ None を置く(無い＝有効)
- 集合の項目(srcintf・dstintf・srcaddr・dstaddr・service・member)は語のリスト。ほかは引用符を外した語を空白1つでつないだ原文
  (アドレスやポートの範囲を解釈・正規化しない)。値の `''` は None(集合の項目では空のリスト)
- `set uuid` は解釈済みにするが、値は持たない(機器ごとに違う識別子で、設定の意味ではないため。決定3)
- ポリシーの edit のキーは出現の順に firewall["order"] に、セントラル NAT(上から当たる)の edit のキーは出現の順に
  firewall["central_snat_order"] に並べる(並べ替えは意味のある変更。決定2、ADR-0139 決定4)
- 同じ項目の行・同じキーの edit が2つ以上あれば後のものを使う(重複は木が警告する)。VDOM の中のブロックは読まない

BGP(REQ-191。ADR-0139 決定2・4)。最上位の `config router bgp` を IR の bgp(Cisco と同じ形。ADR-0038)に写す:
- 写す条件: 重なったブロックも含めた直下の最後の `set as …` が 1〜4294967295 の10進の数字(引用符は外す)のときだけ写す。
  行が無い・0・数でない(asdot を含む)・範囲外なら、どのブロックも今のまま汎用の形(bgp は None)。
  写すときは vendor_specific["fortinet"]["config"] に `router bgp` を入れない(2か所の差分にしない)。重なったブロックは1つにまとめる
- 表: 直下の `set as`→local_as、`set router-id <IPv4>`→router_id(原文)。`config neighbor` の `edit <アドレス>`
  (鍵はアドレスの正規の書き方、kind は address)と `config neighbor-group` の `edit <名前>`(kind は peer_group。
  アドレスの形の名前は未解釈)→ neighbors。相手の中の `set remote-as`・`description`・`update-source`・`shutdown enable|disable`、
  `set password …`(password_configured = True だけ)
- 表に無い行(ほかの set・unset <1語>・入れ子の config)は options に道すじで残す。直下と直下の入れ子は bgp.options、
  相手の中は相手の options。例: `network > 1 > prefix 192.0.2.0 255.255.255.0`・`redistribute connected > status enable`。
  値の引用符は外し、語は空白1つでつなぐ。子が1つも写らない config・edit は道すじだけを入れる。
  表が持つ欄(直下の as・router-id、相手の中の5つ)の表に当たらない形、set でも unset <1語> でもない行、直下の edit は未解釈
- 秘密: 汎用の形と同じ判定(_is_secret)の欄は、どの段でも値を持たず `<道すじ> <欄>` だけを options に入れる。
  表が持つ欄でも秘密の行(`set description ENC …` など)は未解釈に残さず欄の名前だけを入れる(未解釈の一覧と差分に
  原文が値ごと入らないように)。相手の中の `set password` は `set` の後ろの空白2つ・タブも区切りとして読む
- `config vdom` の中の `config router bgp` は今のまま汎用の形(ADR-0135 決定2)

OSPF(REQ-192。ADR-0139 決定2・4)。最上位の `config router ospf` を IR の ospf(Cisco と同じ形)に写す:
- 写す条件: 重なったブロックも含めて、どこかに(入れ子の中も含めて)set・unset・edit の行が1つでもあれば写す。
  無ければ(空のブロック、OSPF を設定していない既定の出力のような空の入れ子の config だけのブロック、set・unset・
  edit・config のどれでもない行だけのブロック)、どのブロックも今のまま汎用の形で ospf は {}
  (決定者の回答 2026-11-04「未設定の既定の形〔空の入れ子の config だけ〕は汎用の形のまま」)。写すときは鍵 "0"・process_id 0 の1つにまとめ、vendor_specific["fortinet"]["config"] に
  `router ospf` を入れない。vrf は None、passive_interface_default は False、non_passive_interfaces は空
- 表: 直下の `set router-id <IPv4>`→router_id、`set passive-interface "<名前>" …`→passive_interfaces(集合。昇順。
  `''` は空。同じ欄は後の行)。`config network` の `edit <番号>` の `set prefix <アドレス> <マスク>`・`set area <IPv4>`
  → networks(wildcard はマスクの反転、area が無ければ 0.0.0.0。edit の番号は持たない)
- network の edit: 直下に読める `set prefix` があり、prefix・area の欄の行(set・unset とも)がすべて読めるときだけ networks に
  入れ、ほかの行は options に `network > <番号> > <行>` で残す。そうでない edit(prefix の無い・マスクの無い形・逆のマスク、
  area の読めない形、unset prefix・unset area)は networks に入れず、edit ごと道すじで options に残す。
  同じ番号の edit が2つ以上あり、1つでも道すじにするものがあれば、その番号の edit はどれも道すじにする
- 表に無い行(ほかの set・unset <1語>・入れ子の config)は、BGP と同じ書き方で ospf["0"]["options"] に道すじで残す。
  `config network` の直下の edit でない行は `network > <行>`、何も写らない `config network` は `network`
- 秘密: どの段でも値を持たず `<道すじ> <欄>` だけを options に入れる。判定は汎用の形と同じ判定(_is_secret)に、値の語の
  どれかが ENC、名前が md5-keys・md5-key、Cisco の OSPF と同じ鍵の語の判定(ospf._is_secret)を足したもの(_ospf_is_secret)。
  表が持つ欄でも秘密の行は未解釈に残さない
- 未解釈に残す形: 直下の router-id・passive-interface の表に当たらない形(`set router-id abc`・`unset router-id` など)、
  set でも unset <1語> でもない行、`config router ospf` の直下の edit
- `config vdom` の中の `config router ospf` と、最上位の `config router ospf6` は今のまま汎用の形

IPsec(REQ-193。ADR-0139 決定2・4)。最上位の `config vpn ipsec phase1`・`phase2`・`phase1-interface`・`phase2-interface`
を IR の ipsec(Cisco・Arista と同じ形。ADR-0104)に写す:
- 直下の `edit <名前>` ごとに ipsec.blocks["<種類> <名前>"](種類は見出しから `config vpn ipsec ` を外した語。
  例 `phase1-interface vpn-a`)。同じ種類・同じ名前の edit(重なったブロックを含む)は1つにまとめる(settings は和、
  keys_configured はどれかが True なら True)。同じ名前の phase1 と phase2 は別の鍵。ipsec.options はいつも空。
  写すときは vendor_specific["fortinet"]["config"] に入れない
- edit の中の `set <欄> <値…>` は `<欄> <値…>`、`unset <欄>`(1語)は `unset <欄>`、入れ子は BGP と同じ書き方の道すじ
  (`ipv4-exclude-range > 1 > start-ip 192.0.2.10`)で settings(集合。昇順)に入れる。子が1つも写らない config・edit は
  道すじだけを入れる
- 秘密: 値も欄の名前も settings に入れず、その項目の keys_configured を True にする(値の変更・秘密の行の数は見ない。
  判断の記録の回答 2026-11-04「秘密の行は鍵の真偽値だけ」)。判定(_ipsec_is_secret)は汎用の形と同じ判定(_is_secret)に、
  値の語のどれかが ENC、欄の名前が secret・passwd・password を含む(psksecret-remote・authpasswd など)を足したもの。
  秘密の行しか持たない入れ子の段は道すじだけを入れる。`unset <欄>` は値を持たないので秘密にしない
- edit の無いブロックは見出しだけを解釈済みにし、何も足さない(汎用の形にも入れない。ほかに写す edit が無ければ ipsec は None)
- 未解釈に残す形: ブロックの直下の edit でない行、edit の中の set でも unset <1語> でも config・edit でもない行(中も読まない)
- `vpn ipsec manualkey`・`manualkey-interface`・`concentrator`・`forticlient` などと、`config vdom` の中は今のまま汎用の形

SD-WAN(REQ-194。ADR-0139 決定2・4)。最上位の `config system sdwan`・`config system virtual-wan-link` を IR の sdwan
(Arista と同じ SdwanBlock。settings だけ)に写す。見出しの語(`sdwan` か `virtual-wan-link`)を <S> とする:
- 写す条件: 見出しごとに、重なった同じ見出しのブロックも含めて、どこかに(入れ子の中も含めて)set・unset・edit の行が
  1つでもあれば写す。無ければ(空のブロック、空の入れ子の config だけのブロック、set・unset・edit・config のどれでもない
  行だけのブロック)、その見出しのどのブロックも今のまま汎用の形で、sdwan にその見出しの鍵は入らない(決定者の回答
  2026-11-04。REQ-192 の OSPF と同じ考え方)。2つの見出しは別々に決める。写すときは vendor_specific["fortinet"]["config"]
  に入れない
- 鍵: 見出しの直下の set・unset は `<S>`、`config <表>` の直下の edit でない行は `<S> <表>`、`config <表>` > `edit <キー>` の
  中の行は `<S> <表> <キー>`(例 `sdwan members 1`)。写す見出し・表・edit には、中に行が無くても鍵を作る(settings は空。
  決定者の回答 2026-11-04「行が無くても、見出しと表ごとに鍵を必ず作る」)。表の名前は引用符を外した語を空白1つでつなぐ。
  重なったブロック・同じ名前の表・同じキーの edit は同じ鍵にまとめる(settings は和)
- settings の書き方は BGP の options と同じ(`set <欄> <値…>` は `<欄> <値…>`、`unset <欄>` は `unset <欄>`、入れ子は
  ` > ` でつなぐ道すじ。例 `sla > 1 > latency-threshold 100`)。子が1つも写らない入れ子の config・edit は道すじだけ
- service の順: `config service` の直下の edit のキーを出てきた順(重なったブロック・同じ名前の表はつなげて数え、同じキーは
  最初の出現だけ)に、鍵 `<S> service-order` の settings に `order <キー> <キー> …` の1つの文字列で入れる(上から当たる表
  なので、順の入れ替えが差分に出る)。edit の無い `config service` では作らない。元の行が無いので印は付けない
- 秘密: 汎用の形と同じ判定(_is_secret)の欄は、どの段でも値を持たず `<道すじ> <欄>`(語の名前だけ)を settings に入れる
- 未解釈に残す形: 見出しの直下の edit、どの段でも set でも unset <1語> でも config・edit でもない行(中も読まない)
- `config vdom` の中は今のまま汎用の形

prefix-list・route-map(REQ-195。ADR-0139 決定2・4、ADR-0047)。最上位の `config router prefix-list`・
`config router prefix-list6`・`config router route-map` を IR の prefix_lists・ipv6_prefix_lists・route_maps
(Cisco・Arista と同じ形)に写す:
- 見出しの直下の `edit <名前>` が1つのリスト(鍵は名前)。写すかはリストごとに決める。同じ名前のリスト(重なったブロック、
  同じブロックの同じ名前の edit)はまとめて決め、1つでも写せないものがあれば、その名前のリストはどれも汎用の形に残す
  (印は行のパスで記録するため。経路と同じ考え方)。写すリストが1つも無い見出しは今のまま汎用の形の見出しで読む。
  写す見出しでも、汎用の形に残すリストと直下の edit でない行は、今と同じ汎用の形で vendor_specific["fortinet"]["config"]
  に写す(1行も無ければ config に入れない)
- prefix-list: `set comments`→description、`config rule` > `edit <番号>`→entries[str(int(番号))]。rule の中は
  `set action permit|deny`(無ければ permit)・`set prefix <アドレス> <マスク>`(prefix6 は `set prefix6 <プレフィックス>`。
  ip_network の正規の書き方)・`set ge|le <数>`・`unset ge|le`(None)。eq はいつも None。
  表に当たらない行(`set prefix any`・読めない形・入れ子・表に無い欄・秘密の行)、prefix の無い rule、`set comments`・
  `config rule` 以外の直下の行、edit でない行のある・edit の無い `config rule`、数字でない番号があれば、リストごと汎用の形に残す
  (行を失わないため。PrefixListEntry に残りを置く欄が無い。依頼書 §2 の ★)
- route-map: `config rule` > `edit <番号>`→entries[str(int(番号))]。rule の中は `set action permit|deny`(無ければ permit)、
  `set match-<語> <値…>`→match の `<語> <値…>`、`set set-<語> <値…>`→set の `<語> <値…>`。ほかの set・unset・入れ子は
  BGP と同じ書き方の道すじで options に入れる。秘密の欄は値を持たず欄の名前だけを options に入れる。action の読めない形
  (ほかの値・値の無い行・`unset action`)は options に入れず未解釈(rule は写す)。description はいつも None。
  `set comments` など `config rule` 以外の直下の行、edit でない行のある・edit の無い `config rule`、数字でない番号があれば、
  リストごと汎用の形に残す
- `config vdom` の中と、`router access-list`・`aspath-list`・`community-list` は今のまま汎用の形

全体(REQ-196。ADR-0139 決定2)。Cisco と同じ global の項目に写す:
- 最上位の `config system global` は見出しもほかの行も今のまま汎用の形。直下の `set hostname <値1語>` だけを先に読み、
  引用符を外した値を global.hostname に写す(同じ欄は後の行)。汎用の形の set・unset には hostname を入れない。
  読めない形(`''`・値の無い行・2語以上・`unset hostname`)は未解釈。値の最初の語が ENC の行は汎用の形の secrets に置く
- 最上位の `config system dns`: `set primary|secondary <IPv4>`・`set ip6-primary|ip6-secondary <IPv6>` を
  global.dns.servers に `<正規のアドレス> <欄>`(欄ごとに後の行。昇順)で、ほかの行は BGP と同じ書き方の道すじで
  global.dns.options に入れる。4つの欄の読めない形は未解釈。秘密の欄は欄の名前だけ
- 最上位の `config system ntp`: 行をすべて道すじで global.ntp_options に入れる(例 `ntpserver > 1 > server 192.0.2.1`)。
  秘密(汎用の形と同じ判定に、欄の名前が key を足す)は `<道すじ> <欄>` だけ
- DNS・NTP は vendor_specific["fortinet"]["config"] に入れない。中に行が無いブロックは見出しだけを解釈済みにする。
  直下の edit は未解釈。`config vdom`・`config global` の中は今のまま汎用の形

汎用の形(REQ-50。ADR-0040)。専用の見出しに当たらない最上位のブロックを、FortiOS の書き方のまま
vendor_specific["fortinet"]["config"][ブロックの名前] に写す(値を解釈しない):
- config の段は set・unset・secrets・config・edits・order、edit の段は set・unset・secrets・config を空でも必ず持つ
- `set <欄> <値…>` の値は引用符を外した語を空白1つでつないだ原文(ファイアウォールと同じ)。`''` と値の無い行は None。
  埋め込みの値は木の表記(`<embedded> lines <N> sha256 <…>`)のまま。`unset <欄>`(1語)は unset に置く(集合)
- 秘密の欄(値の最初の語が ENC、名前が SECRET_FIELDS か `hostkey-` で始まる、SNMP のコミュニティの edit の `set name`)は
  値をどこにも入れず、名前だけを secrets に置く。同じ段の set の同じ欄は消す。
  語の区切りは、値を語に分けるときと同じく空白の並び(空白2つ・タブを含む)。ENC は引用符を外して比べる
- 同じ名前のブロック・入れ子の config、同じキーの edit はまとめる(同じ欄の set は後の行の値。order は最初の出現だけ)
- `config vdom`・`config global` の中も同じ形で読む(中のファイアウォール・経路は専用の形には写さない)。
  `set` でも `unset <1語>` でもない行は未解釈のまま
- 専用のブロックの edit の other も同じ形・同じ決まり(edit の段の4つのキー。パターンの id も同じ)で読む
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, IPv6Address, IPv6Network, ip_address, ip_network
from itertools import combinations
from typing import Any

from driftpilot.config_parser import ospf as cisco_ospf
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree
from driftpilot.config_parser.fortinet_tree import CONFIG, EDIT, EMBEDDED, unquote
from driftpilot.config_parser.ir import (
    Bgp,
    BgpNeighbor,
    Global,
    Interface,
    InterfaceType,
    Ipsec,
    IpsecBlock,
    Ospf,
    OspfNetwork,
    PrefixList,
    PrefixListEntry,
    RouteMap,
    RouteMapEntry,
    SdwanBlock,
    StaticRoute,
    new_bgp,
    new_bgp_neighbor,
    new_global,
    new_interface,
    new_ipsec,
    new_ospf,
    new_prefix_list,
    new_route_map_entry,
    unset_marker,
)
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# vendor_specific["fortinet"]["interfaces"][名前] の固定キー。other は表に無い行の汎用の形(ADR-0119 決定1)
VENDOR_KEYS = ("vdom", "type", "mode", "allowaccess", "role", "alias", "vrf", "speed", "vlanid", "other")
# unset を印にするキー。type は共通キーの type にも写すため含めない
UNSETTABLE = ("vdom", "mode", "allowaccess", "role", "alias", "vrf", "speed", "vlanid")
# 表が持つ欄。この欄の、表に当たらない set・unset は other に入れず未解釈に残す(ADR-0119 決定2)
_INTERFACE_FIELDS = frozenset({
    "ip", "description", "status", "interface", "type", "vdom", "mode", "allowaccess", "role", "alias", "vrf", "speed",
    "vlanid",
})
_ROUTE_FIELDS = frozenset({"dst", "gateway", "device", "distance", "status"})

# FortiOS の type → 共通キーの type。ここに無い値(physical / hard-switch / tunnel / vlan など)は routed。
# vlan を routed にするのは、サブインターフェースの関係を parent_interface が別に持つため(ADR-0010)
_TYPE_MAP: dict[str, InterfaceType] = {"loopback": "loopback", "aggregate": "port_channel"}
_DEFAULT_TYPE: InterfaceType = "routed"

_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
# 値1語。引用符で囲まれた語(中の `\X` を含む)か、引用符で始まらない語
_VALUE = "|".join((r'"(?:[^"\\]|\\.)*"', r"'(?:[^'\\]|\\.)*'", r"""[^\s"']\S*"""))
_VALUE_WORD = re.compile(_VALUE)


@dataclass
class _Ctx:
    """1つの edit(インターフェース1つ)を読んでいる間の状態。"""

    name: str = ""
    interface: Interface | None = None
    vendor: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RouteCtx:
    """1つの edit(経路1本)を読んでいる間の状態。static6 は `config router static6` の edit。"""

    static6: bool = False
    key: str = ""
    destination: IPv4Network | IPv6Network | None = None  # dst の行が無ければ既定経路(__post_init__)
    next_hop: IPv4Address | IPv6Address | None = None
    interface: str | None = None
    distance: int | None = None
    enabled: bool | None = None

    def __post_init__(self) -> None:
        if self.destination is None:
            self.destination = IPv6Network("::/0") if self.static6 else IPv4Network("0.0.0.0/0")


@dataclass
class _FwCtx:
    """ファイアウォールの edit 1つ(ポリシー・アドレスなど1つ)を読んでいる間の状態。"""

    key: str = ""
    values: dict[str, Any] = field(default_factory=dict)


def _text(word: str) -> str | None:
    """引用符を外した値。空なら None。"""
    return unquote(word) or None


def _open(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.name = m["name"]
    ctx.interface = interface = new_interface(ctx.name)
    # 名前の `.` から親を推定しない(ssl.root の親は ssl ではない)。親は set interface の行だけが決める
    interface["parent_interface"] = None
    interface["type"] = _DEFAULT_TYPE
    ctx.vendor = dict.fromkeys(VENDOR_KEYS)
    return True


def _block(m: re.Match[str], ctx: None) -> bool:
    return True


def _ip(m: re.Match[str], ctx: _Ctx) -> bool:
    if (m["addr"], m["mask"]) == ("0.0.0.0", "0.0.0.0"):  # FortiOS の「アドレスなし」の表示
        ctx.interface["ip_address"] = None
        return True
    try:
        ctx.interface["ip_address"] = IPv4Interface(f"{m['addr']}/{m['mask']}")
    except ValueError:
        return False
    return True


def _description(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["description"] = _text(m["value"])
    return True


def _status(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["shutdown"] = m["state"] == "down"
    return True


def _parent(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.interface["parent_interface"] = _text(m["value"])
    return True


def _type(m: re.Match[str], ctx: _Ctx) -> bool:
    value = _text(m["value"])
    ctx.vendor["type"] = value
    ctx.interface["type"] = _TYPE_MAP.get(value or "", _DEFAULT_TYPE)
    return True


def _vendor_text(key: str):
    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        ctx.vendor[key] = _text(m["value"])
        return True

    return apply


def _vendor_int(key: str):
    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        ctx.vendor[key] = int(m["value"])
        return True

    return apply


def _allowaccess(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.vendor["allowaccess"] = [unquote(word) for word in _VALUE_WORD.findall(m["value"])]
    return True


def _unset(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.vendor[m["attr"]] = unset_marker()
    return True


# --- 経路(config router static) ---


def _route_open(m: re.Match[str], ctx: _RouteCtx) -> bool:
    ctx.key = m["key"]
    return True


def _route_dst(m: re.Match[str], ctx: _RouteCtx) -> bool:
    try:
        ctx.destination = ip_network(f"{m['addr']}/{m['mask']}", strict=False)
    except ValueError:  # マスクとして読めない(連続していないなど)
        return False
    return True


def _route_gateway(m: re.Match[str], ctx: _RouteCtx) -> bool:
    try:
        ctx.next_hop = IPv4Address(m["addr"])
    except ValueError:
        return False
    return True


def _route6_dst(m: re.Match[str], ctx: _RouteCtx) -> bool:
    try:
        ctx.destination = IPv6Network(m["prefix"], strict=False)
    except ValueError:  # IPv6 として読めない(IPv4 の宛先など)
        return False
    return True


def _route6_gateway(m: re.Match[str], ctx: _RouteCtx) -> bool:
    try:
        ctx.next_hop = IPv6Address(m["addr"])
    except ValueError:
        return False
    return True


def _route_device(m: re.Match[str], ctx: _RouteCtx) -> bool:
    ctx.interface = _text(m["value"])
    return True


def _route_distance(m: re.Match[str], ctx: _RouteCtx) -> bool:
    ctx.distance = int(m["value"])
    return True


def _route_status(m: re.Match[str], ctx: _RouteCtx) -> bool:
    ctx.enabled = m["state"] == "enable"
    return True


# 行き先(gateway・device)の無い edit を経路にする行(ADR-0129 決定2)。値は汎用の形の other の set の値
_NO_GATEWAY_TARGETS = {"blackhole": "enable", "virtual-wan-link": "enable"}
_SDWAN_ZONE = "sdwan-zone"


def _has_no_gateway_target(other: dict[str, Any] | None) -> bool:
    """経路の詳細の other に、blackhole・virtual-wan-link の enable か、値のある sdwan-zone があるか。"""
    if other is None:
        return False
    values = other["set"]
    return any(values.get(name) == state for name, state in _NO_GATEWAY_TARGETS.items()) or bool(
        values.get(_SDWAN_ZONE)
    )


# --- 設定ファイルの見出しの注記(最上位の `#config-version=` など。ADR-0129 決定1) ---


def _header(key: str):
    def apply(m: re.Match[str], ctx: dict[str, str]) -> bool:
        ctx[key] = m["value"]  # 同じ行が2回あれば後の行の値
        return True

    return apply


# --- ファイアウォール(config firewall policy / address / addrgrp / service custom / service group) ---

# 集合として比べる項目(語のリストにする。diff_engine/compare.py の _SET_LISTS と同じ名前)
FIREWALL_SET_FIELDS = ("srcintf", "dstintf", "srcaddr", "dstaddr", "service", "member")
# 値1語以上(空白1つで区切る)
_VALUES = rf"(?:{_VALUE})(?: (?:{_VALUE}))*"


@dataclass(frozen=True)
class _FwBlock:
    """読むブロック1つ。fields は (項目名, 例の値) で、表の並び。"""

    name: str  # パターンの id とスコープに使う名前
    header: str
    key: str  # firewall の中のキー
    edit_example: str
    fields: tuple[tuple[str, str], ...]
    uuid_example: str = "set uuid 00000000-0000-4000-8000-000000000001"
    # edit のキーを出現の順に並べる firewall の中のキー(上から当たる表。並べ替えは意味のある変更)。None は順を持たない
    order_key: str | None = None


_FW_BLOCKS: tuple[_FwBlock, ...] = (
    _FwBlock(
        "policy", "config firewall policy", "policies", "edit 1",
        (
            ("name", '"allow-out"'), ("srcintf", '"port1"'), ("dstintf", '"port2"'), ("srcaddr", '"LAN_NET" "VPN_NET"'),
            ("dstaddr", '"all"'), ("service", '"HTTP" "HTTPS"'), ("schedule", '"always"'), ("action", "accept"),
            ("logtraffic", "all"), ("status", "disable"),
        ),
        order_key="order",
    ),
    _FwBlock(
        "address", "config firewall address", "addresses", "edit LAN_NET",
        (
            ("type", "fqdn"), ("subnet", "192.0.2.0 255.255.255.0"), ("fqdn", '"www.example.com"'),
            ("start-ip", "192.0.2.10"), ("end-ip", "192.0.2.20"), ("interface", '"port1"'),
            ("associated-interface", '"port1"'), ("comment", '"office LAN"'),
        ),
    ),
    _FwBlock("addrgrp", "config firewall addrgrp", "address_groups", "edit LAN_GROUP",
             (("member", '"LAN_NET" "VPN_NET"'),)),
    _FwBlock(
        "service_custom", "config firewall service custom", "services", "edit WEB_ALT",
        (
            ("category", '"Web Access"'), ("protocol", "TCP/UDP/SCTP"), ("tcp-portrange", "8080 8443"),
            ("udp-portrange", "53"), ("icmptype", "8"), ("protocol-number", "6"),
        ),
    ),
    _FwBlock("service_group", "config firewall service group", "service_groups", "edit WEB_GROUP",
             (("member", '"HTTP" "HTTPS"'),)),
    # VIP・IP プール・セントラル NAT(REQ-196。ADR-0139 決定3)
    _FwBlock(
        "vip", "config firewall vip", "vips", "edit VIP-WEB",
        (
            ("type", "static-nat"), ("extip", "198.51.100.10"), ("mappedip", '"192.0.2.10"'), ("extintf", '"port1"'),
            ("portforward", "enable"), ("protocol", "tcp"), ("extport", "8443"), ("mappedport", "443"),
            ("comment", '"web server"'),
        ),
    ),
    _FwBlock(
        "vip6", "config firewall vip6", "vips6", "edit VIP6-WEB",
        (
            ("type", "static-nat"), ("extip", "2001:db8::10"), ("mappedip", '"2001:db8:1::10"'), ("portforward", "enable"),
            ("extport", "8443"), ("mappedport", "443"), ("comment", '"web server"'),
        ),
    ),
    _FwBlock(
        "vipgrp", "config firewall vipgrp", "vip_groups", "edit VIP-GROUP",
        (("interface", '"port1"'), ("member", '"VIP-WEB" "VIP-MAIL"'), ("comments", '"public servers"')),
    ),
    _FwBlock(
        "ippool", "config firewall ippool", "ippools", "edit POOL-A",
        (
            ("type", "overload"), ("startip", "198.51.100.20"), ("endip", "198.51.100.29"), ("startport", "5117"),
            ("endport", "65533"), ("comments", '"outbound"'),
        ),
    ),
    _FwBlock(
        "ippool6", "config firewall ippool6", "ippools6", "edit POOL6-A",
        (("startip", "2001:db8::20"), ("endip", "2001:db8::29"), ("comments", '"outbound"')),
    ),
    _FwBlock(
        "central_snat", "config firewall central-snat-map", "central_snat", "edit 1",
        (
            ("status", "disable"), ("srcintf", '"port2"'), ("dstintf", '"port1"'), ("orig-addr", '"LAN_NET"'),
            ("dst-addr", '"all"'), ("nat", "enable"), ("nat-ippool", '"POOL-A"'), ("protocol", "6"),
        ),
        order_key="central_snat_order",
    ),
)
_FW_ID = "vendor_specific.fgt_firewall."
_FW_NOTE = (
    "表に無い行(set nat enable・入れ子の config など)があっても edit は写し、その行は汎用の形で other に写す"
    "(写す行が無ければ other のキーは置かない。ADR-0119 決定1)。表の欄と uuid の、表に当たらない形は未解釈(決定2)"
)


def _fw_open(initial: dict[str, Any]):
    def apply(m: re.Match[str], ctx: _FwCtx) -> bool:
        ctx.key = m["key"]
        ctx.values = dict(initial)
        return True

    return apply


def _fw_value(key: str):
    if key in FIREWALL_SET_FIELDS:
        def apply(m: re.Match[str], ctx: _FwCtx) -> bool:
            words = [unquote(word) for word in _VALUE_WORD.findall(m["value"])]
            ctx.values[key] = [] if words == [""] else words
            return True
    else:
        def apply(m: re.Match[str], ctx: _FwCtx) -> bool:
            ctx.values[key] = " ".join(unquote(word) for word in _VALUE_WORD.findall(m["value"])) or None
            return True

    return apply


def _fw_uuid(m: re.Match[str], ctx: _FwCtx) -> bool:
    return True  # 解釈済みにするが、値は持たない


def _fw_unset(m: re.Match[str], ctx: _FwCtx) -> bool:
    ctx.values[m["attr"]] = unset_marker()
    return True


def _fw_reserved(spec: _FwBlock) -> frozenset[str]:
    """表が持つ欄(表の欄と uuid)。この欄の、表に当たらない形は other に入れない(ADR-0119 決定2)。"""
    return frozenset({name for name, _ in spec.fields} | {"uuid"})


def _fw_patterns(spec: _FwBlock) -> tuple[Pattern, ...]:
    """1つのブロックの見出し・edit・中の行のパターン。scope は fw_<名前>_edit(直下の edit)/ fw_<名前>(edit の中)。"""
    prefix = f"{_FW_ID}{spec.name}_"
    target = f"vendor_specific.fortinet.firewall.{spec.key}[key]"
    scope = f"fw_{spec.name}"
    rows = [
        Pattern(f"{prefix}block", "top", spec.header, "(ブロックの見出し)", spec.header, _block,
                note=f"最上位の `{spec.header}` だけを写す。VDOM の中のブロックは読まない"),
        Pattern(
            f"{prefix}edit", f"{scope}_edit", r"edit (?P<key>.+)",
            target + (f" / vendor_specific.fortinet.firewall.{spec.order_key}[]" if spec.order_key else ""),
            spec.edit_example, _fw_open({"status": None} if spec.name == "policy" else {}),
            note="edit のキーは引用符を外した原文(数字に変換しない)。同じキーの edit は後のもので上書きする。"
            + ("出現の順に order に並べる。status の行が無ければ None。" if spec.name == "policy" else "")
            + (
                f"出現の順に {spec.order_key} に並べる(上から当たる表なので、並べ替えは差分に出る。ADR-0139 決定4)。"
                if spec.order_key and spec.name != "policy" else ""
            )
            + _FW_NOTE,
        ),
    ]
    for field_name, example in spec.fields:
        if field_name == "status":
            regex, note = r"set status (?P<value>enable|disable)", "enable / disable の文字列。ほかの値は未解釈"
        elif field_name in FIREWALL_SET_FIELDS:
            regex, note = rf"set {field_name} (?P<value>{_VALUES})", "語のリスト(引用符を外す。比べるときは集合)。`''` は空のリスト"
        else:
            regex, note = rf"set {field_name} (?P<value>{_VALUES})", "引用符を外した語を空白1つでつないだ原文。`''` は None"
        rows.append(Pattern(
            f"{prefix}{field_name.replace('-', '_')}", scope, regex, f"{target}.{field_name}",
            f"set {field_name} {example}", _fw_value(field_name), note=note,
        ))
    rows.append(Pattern(
        f"{prefix}uuid", scope, rf"set uuid (?P<value>{_VALUE})", "(値は持たない)", spec.uuid_example, _fw_uuid,
        note="解釈済みにするが値は持たない(ADR-0037 決定3)",
    ))
    rows.append(Pattern(
        f"{prefix}unset", scope, rf"unset (?P<attr>{'|'.join(name for name, _ in spec.fields)})",
        f"{target}.<attr> = {{\"unset\": true}}", f"unset {spec.fields[-1][0]}", _fw_unset,
        note="表の欄の明示的な未設定(行が無いときのキーなしと区別する。集合の欄も同じ。ADR-0119 決定4)。"
        "`unset uuid` と2語以上の unset は未解釈",
    ))
    return tuple(rows)


_FW_PATTERNS: tuple[Pattern, ...] = tuple(p for spec in _FW_BLOCKS for p in _fw_patterns(spec))
_FW_BY_HEADER: dict[str, _FwBlock] = {f"{_FW_ID}{spec.name}_block": spec for spec in _FW_BLOCKS}


# --- 汎用の形(専用の見出しに当たらない最上位のブロック。REQ-50、ADR-0040) ---

_GEN_ID = "vendor_specific.fgt_generic."
_GEN_BLOCK = f"{_GEN_ID}block"
_GEN_SCOPE = "fgt_generic"
_GEN_SNMP_SCOPE = "fgt_generic_snmp"
# 値を持たず、名前だけを secrets に置く欄(完全一致)。ほかに hostkey- で始まる名前と、値の最初の語が ENC の行
SECRET_FIELDS = (
    "password", "passwd", "passwd1", "passwd2", "passwd3", "proxy-password", "auth-password-l1", "auth-password-l2",
    "sae-password", "psksecret", "ppk-secret", "secret", "client-secret", "passphrase", "private-key", "api-key",
    "key-string", "inter-controller-key", "auth-pwd", "priv-pwd",
)
_SECRET_PREFIX = "hostkey-"
# この名前の config の段の直下の edit では、set name がコミュニティ名(v1/v2c の認証の秘密)。深さは問わない
SNMP_COMMUNITY_BLOCKS = frozenset({"system snmp community", "switch-controller snmp-community"})


@dataclass
class _GenCtx:
    """汎用の形の段1つの行を読む状態。opened は config / edit の行で開いた段、name はその名前・キー。

    reserved は、専用のブロックの edit の other に写すときの、表が持つ欄の名前(ADR-0119 決定2)。
    この欄の set・unset は写さず(apply が False を返し)、未解釈に残す。汎用の形のブロックでは空。
    """

    stage: dict[str, Any]
    opened: dict[str, Any] | None = None
    name: str = ""
    reserved: frozenset[str] = frozenset()


def _config_stage() -> dict[str, Any]:
    """config の段。6つのキーは空でも置く(片方にだけキーがあると、余計な差分になるため)。"""
    return {"set": {}, "unset": [], "secrets": [], "config": {}, "edits": {}, "order": []}


def _edit_stage() -> dict[str, Any]:
    """edit の段。4つのキーは空でも置く。"""
    return {"set": {}, "unset": [], "secrets": [], "config": {}}


def _add_sorted(items: list[str], name: str) -> None:
    """集合として持つリストに足す(重複なし。文字列の昇順)。"""
    if name not in items:
        items.append(name)
        items.sort()


def _gen_config(m: re.Match[str], ctx: _GenCtx) -> bool:
    ctx.name = m["name"]
    ctx.opened = ctx.stage["config"].setdefault(ctx.name, _config_stage())  # 同じ名前の段はまとめる
    return True


def _gen_edit(m: re.Match[str], ctx: _GenCtx) -> bool:
    ctx.name = key = m["key"]
    edits = ctx.stage["edits"]
    if key not in edits:  # 同じキーの edit はまとめる。order には最初の出現だけ
        edits[key] = _edit_stage()
        ctx.stage["order"].append(key)
    ctx.opened = edits[key]
    return True


def _is_secret(field: str, words: list[str]) -> bool:
    """秘密の欄か(SNMP のコミュニティ名を除く)。words は値を _VALUE_WORD で分けた語。"""
    return field in SECRET_FIELDS or field.startswith(_SECRET_PREFIX) or (bool(words) and unquote(words[0]) == "ENC")


def _keep_secret(field: str, stage: dict[str, Any]) -> None:
    _add_sorted(stage["secrets"], field)
    stage["set"].pop(field, None)  # 前の行の平文の値を残さない


def _gen_secret(m: re.Match[str], ctx: _GenCtx) -> bool:
    if m["field"] in ctx.reserved:
        return False
    _keep_secret(m["field"], ctx.stage)
    return True


def _gen_embedded(m: re.Match[str], ctx: _GenCtx) -> bool:
    if m["field"] in ctx.reserved:
        return False
    ctx.stage["set"][m["field"]] = m["value"]  # 木の表記(ハッシュ)のまま。本文は木に無い
    return True


def _gen_set(m: re.Match[str], ctx: _GenCtx) -> bool:
    if m["field"] in ctx.reserved:
        return False
    value = m["value"]
    words = _VALUE_WORD.findall(value) if value is not None else []
    if _is_secret(m["field"], words):  # 秘密の行の書式が上の行に当たらなくても、値は入れない
        _keep_secret(m["field"], ctx.stage)
        return True
    ctx.stage["set"][m["field"]] = " ".join(unquote(word) for word in words) or None
    return True


def _gen_unset(m: re.Match[str], ctx: _GenCtx) -> bool:
    if m["field"] in ctx.reserved:
        return False
    _add_sorted(ctx.stage["unset"], m["field"])
    return True


_GEN_VS = "vendor_specific.fortinet.config"
_SECRET_NOTE = (
    "値はどこにも入れず、欄の名前だけを secrets に置く(ADR-0040 決定3)。秘密の欄は、値の最初の語が ENC の行、"
    f"名前が一覧({'・'.join(SECRET_FIELDS)})に完全一致するか `{_SECRET_PREFIX}` で始まる行、"
    f"{'・'.join(f'`config {name}`' for name in sorted(SNMP_COMMUNITY_BLOCKS))} の直下の edit の `set name`(深さは問わない)。"
    "同じ段の set の同じ欄は消す"
)
_GEN_NOTE = (
    "専用の見出しのブロック(system interface・router static・router static6・router bgp〔`set as` が読めるとき〕・"
    "router ospf〔入れ子の中も含めて set・unset・edit の行があるとき〕・"
    "vpn ipsec phase1・phase2・phase1-interface・phase2-interface・"
    "system sdwan・system virtual-wan-link〔入れ子の中も含めて set・unset・edit の行があるとき〕・"
    "router prefix-list・router prefix-list6・router route-map〔写せるリストがあるとき。写せないリストと直下の edit でない"
    "行は、この表の行で config に写す〕・"
    "system dns・system ntp・ファイアウォールの11〔policy・address・addrgrp・service custom・service group・vip・vip6・"
    "vipgrp・ippool・ippool6・central-snat-map〕)は config に写さない。system global は汎用の形で写すが、直下の "
    "`set hostname` は global.hostname に写し、set・unset に入れない"
    "(その edit の中の表に当たらない行は、この表の行で edit の other に写す。表が持つ欄は写さない。ADR-0119)。"
    "同じ名前の config・同じキーの edit はまとめ、同じ欄の set は後の行の値"
)
# 語の区切りは、値を語に分けるとき(_VALUE_WORD)と同じく空白の並び(空白2つ・タブを含む)
_GEN_SPACE_NOTE = "行の中の語の区切りは、空白2つ・タブも1つの区切りとして読む(値を語に分けるときと同じ)"
_GEN_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        _GEN_BLOCK, "top", r"config\s+(?P<name>.+)", f"{_GEN_VS}[name]", "config system global", _gen_config,
        note="専用の見出しに当たらない最上位のブロック(config vdom・config global を含む)を、"
        "FortiOS の書き方のまま写す。名前は `config` と空白の後ろの原文。" + _GEN_NOTE + "。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_GEN_ID}config", _GEN_SCOPE, r"config\s+(?P<name>.+)", "<段>.config[name]", "config ftgd-wf", _gen_config,
        note="入れ子の config。中を同じ規則で読む。" + _GEN_NOTE,
    ),
    Pattern(
        f"{_GEN_ID}edit", _GEN_SCOPE, r"edit (?P<key>.*)", "<段>.edits[key] / <段>.order[]", "edit default", _gen_edit,
        note="キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)。order は出現の順(最初の出現だけ)",
    ),
    Pattern(
        f"{_GEN_ID}snmp_community", _GEN_SNMP_SCOPE, r"set\s+(?P<field>name)(?:\s.*)?", "<段>.secrets[]",
        'set name "EXAMPLEONLY"', _gen_secret, note=_SECRET_NOTE,
    ),
    Pattern(
        f"{_GEN_ID}secret_enc", _GEN_SCOPE, r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""", "<段>.secrets[]",
        "set password ENC EXAMPLEONLY", _gen_secret, note=_SECRET_NOTE + "。値の最初の語は引用符を外して比べる",
    ),
    Pattern(
        f"{_GEN_ID}secret", _GEN_SCOPE,
        rf"set\s+(?P<field>{'|'.join(map(re.escape, SECRET_FIELDS))}|{re.escape(_SECRET_PREFIX)}\S+)(?:\s.*)?",
        "<段>.secrets[]", "set psksecret EXAMPLEONLY", _gen_secret, note=_SECRET_NOTE,
    ),
    Pattern(
        f"{_GEN_ID}embedded", _GEN_SCOPE, rf"set\s+(?P<field>\S+)\s+(?P<value>{re.escape(EMBEDDED)} .*)",
        "<段>.set[field]", f"set buffer {EMBEDDED} lines 2 sha256 {'0' * 64}", _gen_embedded,
        note="複数行の埋め込み。値は木のテキストの `<embedded>` からあとのまま(ADR-0025 決定3。本文は木に無い)",
    ),
    Pattern(
        f"{_GEN_ID}set", _GEN_SCOPE, r"set\s+(?P<field>\S+)(?:\s+(?P<value>.*))?", "<段>.set[field]",
        'set comment "example"', _gen_set,
        note="値は引用符を外した語を空白1つでつないだ原文(REQ-45 と同じ)。`''` と値の無い行は None。"
        "数・アドレス・集合として解釈しない。秘密の欄(上の行と同じ判定。値を語に分けてから見る)は値を持たず secrets に置く",
    ),
    Pattern(
        f"{_GEN_ID}unset", _GEN_SCOPE, r"unset\s+(?P<field>\S+)", "<段>.unset[]", "unset comment", _gen_unset,
        note="1語だけ。集合(文字列の昇順)。set と同じ欄があっても両方に置く。`unset a b` などは未解釈",
    ),
)


# --- BGP(最上位の config router bgp。REQ-191、ADR-0139 決定2・4) ---

_BGP_ID = "bgp.fortinet."
_BGP_BLOCK = f"{_BGP_ID}block"
_BGP_HEADER = "config router bgp"
_BGP_NEIGHBOR_BLOCK = f"{_BGP_ID}neighbor_block"
_BGP_GROUP_BLOCK = f"{_BGP_ID}neighbor_group_block"
_BGP_OPTION_SCOPE = "bgp_option"
# 写すかどうかを決める行(直下の最後の `set as …`)と、local_as に読む形
_BGP_AS_LINE = re.compile(r"set\s+as(?:\s.*)?")
_BGP_AS = rf"set as (?P<value>{_VALUE})"
_MAX_AS = 4294967295
# 表が持つ欄。この欄の、表に当たらない set・unset は options に入れず未解釈に残す(依頼書の条件4。ADR-0103 の補足)
_BGP_FIELDS = frozenset({"as", "router-id"})
_BGP_NEIGHBOR_FIELDS = frozenset({"remote-as", "description", "update-source", "shutdown", "password"})


@dataclass
class _BgpCtx:
    """`config router bgp` の直下か、相手の edit 1つを読んでいる間の状態。"""

    bgp: Bgp
    neighbor: BgpNeighbor | None = None


@dataclass
class _OptCtx:
    """options に写す1行の状態(BGP・OSPF・IPsec で共通)。text は葉の文字列か段の名前、opened は config・edit の行で
    段を開いたか。secret は「秘密の行で、文字列を何も足さない」(IPsec だけ。keys_configured を True にする)。

    reserved は、その段で表が持つ欄の名前(直下と相手の edit の直下だけ)。この欄の set・unset は写さない。
    """

    reserved: frozenset[str] = frozenset()
    text: str = ""
    opened: bool = False
    secret: bool = False


def _bgp_asn(word: str, minimum: int) -> int | None:
    """引用符を外した10進の数字だけの値を int にする(minimum〜4294967295)。asdot・数でない値・範囲外は None。"""
    text = unquote(word)
    if not (text.isascii() and text.isdecimal()):
        return None
    value = int(text)
    return value if minimum <= value <= _MAX_AS else None


def _bgp_local_as(tree: FlatTree) -> int | None:
    """最上位の `config router bgp`(重なったものを含む)の直下の、最後の `set as …` の行から AS 番号を読む。

    行が無い、0、数でない、範囲外なら None(ブロックはすべて今のまま汎用の形で写す。ADR-0139 決定4)。
    """
    last: str | None = None
    for block in tree.children:
        if block.scope_id != CONFIG or block.text != _BGP_HEADER:
            continue
        for line in block.children:
            if line.scope_id is None and _BGP_AS_LINE.fullmatch(line.text):
                last = line.text  # 同じ欄が2回あれば後の行
    if last is None or (m := re.fullmatch(_BGP_AS, last)) is None:
        return None
    return _bgp_asn(m["value"], 1)


def _bgp_as(m: re.Match[str], ctx: _BgpCtx) -> bool:
    # local_as は前もって最後の行で決める(_bgp_local_as)。ここでは読める形かだけを見る
    return _bgp_asn(m["value"], 1) is not None


def _bgp_router_id(m: re.Match[str], ctx: _BgpCtx) -> bool:
    text = unquote(m["value"])
    try:
        IPv4Address(text)
    except ValueError:
        return False
    ctx.bgp["router_id"] = text
    return True


def _bgp_neighbor_open(m: re.Match[str], ctx: _BgpCtx) -> bool:
    try:
        key = str(ip_address(m["key"]))
    except ValueError:
        return False
    # 同じ鍵の相手(ブロックをまたいでも)は1つにまとめる
    ctx.neighbor = ctx.bgp["neighbors"].setdefault(key, new_bgp_neighbor("address"))
    return True


def _bgp_group_open(m: re.Match[str], ctx: _BgpCtx) -> bool:
    key = m["key"]
    try:
        ip_address(key)
    except ValueError:
        ctx.neighbor = ctx.bgp["neighbors"].setdefault(key, new_bgp_neighbor("peer_group"))
        return True
    return False  # アドレスの形の名前は、neighbor の鍵とぶつかるため読まない(Cisco のピアグループの定義と同じ)


def _bgp_remote_as(m: re.Match[str], ctx: _BgpCtx) -> bool:
    if (value := _bgp_asn(m["value"], 0)) is None:
        return False
    ctx.neighbor["remote_as"] = value
    return True


def _bgp_neighbor_text(key: str):
    name = key.replace("_", "-")

    def apply(m: re.Match[str], ctx: _BgpCtx) -> bool:
        words = _VALUE_WORD.findall(m["value"])
        if _is_secret(name, words):  # 値の最初の語が ENC なら値を持たない(bgp_option の秘密の行で欄の名前だけ残す)
            return False
        ctx.neighbor[key] = " ".join(unquote(word) for word in words) or None
        return True

    return apply


def _bgp_shutdown(m: re.Match[str], ctx: _BgpCtx) -> bool:
    ctx.neighbor["shutdown"] = m["state"] == "enable"
    return True


def _bgp_password(m: re.Match[str], ctx: _BgpCtx) -> bool:
    ctx.neighbor["password_configured"] = True  # 値はどこにも持たない
    return True


def _opt_secret(m: re.Match[str], ctx: _OptCtx) -> bool:
    # 表が持つ欄でも、秘密の行は未解釈に残さない(未解釈の一覧と差分に原文が値ごと入るため)。語の名前だけ
    ctx.text = m["field"]
    return True


def _opt_set(secret: Callable[[str, list[str]], bool], *, drop: bool = False):
    """`set <欄> <値…>` を options の文字列にする apply。secret は秘密の判定(欄の名前, 値の語)。

    drop は、秘密の行の欄の名前も残さないか(IPsec。keys_configured だけにする)。False なら欄の名前だけを残す(BGP・OSPF)。
    """

    def apply(m: re.Match[str], ctx: _OptCtx) -> bool:
        name = m["field"]
        value = m["value"]
        words = _VALUE_WORD.findall(value) if value is not None else []
        if secret(name, words):  # 秘密の行の書式が上の行に当たらなくても、値は入れない(表が持つ欄でも同じ)
            if drop:
                ctx.secret = True
            else:
                ctx.text = name
            return True
        if name in ctx.reserved:
            return False
        joined = " ".join(unquote(word) for word in words)
        ctx.text = f"{name} {joined}" if joined else name
        return True

    return apply


_bgp_opt_set = _opt_set(_is_secret)


def _opt_unset(m: re.Match[str], ctx: _OptCtx) -> bool:
    if m["field"] in ctx.reserved:
        return False
    ctx.text = f"unset {m['field']}"
    return True


def _opt_config(m: re.Match[str], ctx: _OptCtx) -> bool:
    name = m["name"]
    ctx.text = " ".join(unquote(word) for word in _VALUE_WORD.findall(name)) or name
    ctx.opened = True
    return True


def _opt_edit(m: re.Match[str], ctx: _OptCtx) -> bool:
    ctx.text = m["key"]
    ctx.opened = True
    return True


_BGP_TARGET = "bgp.neighbors[key]"
_BGP_NEIGHBOR_NOTE = (
    "中の `remote-as`・`description`・`update-source`・`shutdown`・`password` は下の行で読み、ほかの set・unset・入れ子の "
    "config は bgp_option の表でその相手の options に写す(入れ子は道すじ)。表が持つ欄の、表に当たらない形は未解釈。"
    "同じ鍵の相手(ブロックをまたいでも)は1つにまとめ、同じ欄は後の行の値。peer_group はいつも None"
)
_BGP_OPTION_NOTE = (
    "表に無い行を道すじで options に残す(ADR-0139 決定4)。直下と直下の入れ子は bgp.options、相手の edit の中は"
    "その相手の options(文字列の昇順の集合)。入れ子は `<config の名前> > <edit のキー> > <行>` のように ` > ` でつなぐ"
    "(config の名前は引用符を外した語を空白1つでつなぐ)。子が1つも写らない config・edit は道すじだけを入れる"
)
_BGP_SECRET_NOTE = (
    "秘密の欄(値の最初の語が ENC、名前が汎用の形の秘密の一覧に完全一致するか `hostkey-` で始まる。汎用の形と同じ判定)は、"
    "どの段でも値を持たず `<道すじ> <欄>` だけを options に入れる。表が持つ欄(description など)でも、秘密の行は"
    "未解釈に残さず(未解釈の一覧と差分に原文が値ごと入るため)欄の名前だけを入れる"
)
_BGP_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        _BGP_BLOCK, "top", _BGP_HEADER, "bgp", _BGP_HEADER, _block,
        note="最上位の `config router bgp` だけを読む(VDOM の中は今のまま汎用の形)。重なったブロックの直下の、最後の "
        "`set as` が 1〜4294967295 の10進の数字のときだけ写す。行が無い・0・数でない(asdot を含む)ときは、"
        "どのブロックも今のまま汎用の形(bgp は None)。写すときは vendor_specific.fortinet.config に入れない。"
        "重なったブロックは1つの bgp にまとめる。address_families・vrfs・evpn_instances はいつも空",
    ),
    Pattern(
        f"{_BGP_ID}as", "router_bgp", _BGP_AS, "bgp.local_as", "set as 64512", _bgp_as,
        note="10進の数字だけ(引用符は外す)。1〜4294967295。ほかの形(0・asdot など)の行は未解釈",
    ),
    Pattern(
        f"{_BGP_ID}router_id", "router_bgp", rf"set router-id (?P<value>{_VALUE})", "bgp.router_id",
        "set router-id 192.0.2.1", _bgp_router_id,
        note="引用符を外した原文。IPv4 として読めなければ未解釈",
    ),
    Pattern(
        _BGP_NEIGHBOR_BLOCK, "router_bgp", r"config neighbor", "(段の見出し)", "config neighbor", _block,
        note="直下の edit を相手にする。edit でない行は未解釈。相手が1つも読めなければ bgp.options に `neighbor` を入れる",
    ),
    Pattern(
        _BGP_GROUP_BLOCK, "router_bgp", r"config neighbor-group", "(段の見出し)", "config neighbor-group", _block,
        note="直下の edit をピアグループにする。edit でない行は未解釈。"
        "ピアグループが1つも読めなければ bgp.options に `neighbor-group` を入れる",
    ),
    Pattern(
        f"{_BGP_ID}neighbor", "bgp_neighbor_edit", r"edit (?P<key>.+)", f"{_BGP_TARGET} (kind=address)",
        "edit 192.0.2.2", _bgp_neighbor_open,
        note="鍵はアドレスの正規の書き方。アドレスとして読めないキーの edit は、中も含めて未解釈。" + _BGP_NEIGHBOR_NOTE,
    ),
    Pattern(
        f"{_BGP_ID}neighbor_group", "bgp_neighbor_group_edit", r"edit (?P<key>.+)", f"{_BGP_TARGET} (kind=peer_group)",
        "edit PEERS", _bgp_group_open,
        note="鍵は名前(引用符を外した原文)。アドレスとして読める名前の edit は、neighbor の鍵とぶつかるため中も含めて未解釈。"
        + _BGP_NEIGHBOR_NOTE,
    ),
    Pattern(
        f"{_BGP_ID}remote_as", "bgp_neighbor", rf"set remote-as (?P<value>{_VALUE})", f"{_BGP_TARGET}.remote_as",
        "set remote-as 64513", _bgp_remote_as,
        note="10進の数字だけ(引用符は外す)。0〜4294967295。ほかの形は未解釈",
    ),
    Pattern(
        f"{_BGP_ID}description", "bgp_neighbor", rf"set description (?P<value>{_VALUES})", f"{_BGP_TARGET}.description",
        'set description "to upstream"', _bgp_neighbor_text("description"),
        note="引用符を外した語を空白1つでつないだ原文。`''` は None",
    ),
    Pattern(
        f"{_BGP_ID}update_source", "bgp_neighbor", rf"set update-source (?P<value>{_VALUES})",
        f"{_BGP_TARGET}.update_source", 'set update-source "loopback0"', _bgp_neighbor_text("update_source"),
        note="引用符を外した語を空白1つでつないだ原文。`''` は None",
    ),
    Pattern(
        f"{_BGP_ID}shutdown", "bgp_neighbor", r"set shutdown (?P<state>enable|disable)", f"{_BGP_TARGET}.shutdown",
        "set shutdown enable", _bgp_shutdown, note="enable → True、disable → False。行が無ければ False。ほかの値は未解釈",
    ),
    Pattern(
        f"{_BGP_ID}password", "bgp_neighbor", r"set\s+password(?:\s.*)?", f"{_BGP_TARGET}.password_configured",
        "set password ENC EXAMPLEONLY", _bgp_password,
        note="ENC も平文も password_configured = True。値はどこにも持たない(ADR-0038 決定6)。"
        "`set` の後ろの空白2つ・タブも区切りとして読む(汎用の形と同じ。未解釈にして原文が値ごと残らないように)",
    ),
    Pattern(
        f"{_BGP_ID}option_secret_enc", _BGP_OPTION_SCOPE, r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""",
        "bgp.options[] / bgp.neighbors[key].options[]", "set auth-key ENC EXAMPLEONLY", _opt_secret,
        note=_BGP_SECRET_NOTE + "。値の最初の語は引用符を外して比べる",
    ),
    Pattern(
        f"{_BGP_ID}option_secret", _BGP_OPTION_SCOPE,
        rf"set\s+(?P<field>{'|'.join(map(re.escape, SECRET_FIELDS))}|{re.escape(_SECRET_PREFIX)}\S+)(?:\s.*)?",
        "bgp.options[] / bgp.neighbors[key].options[]", "set secret EXAMPLEONLY", _opt_secret, note=_BGP_SECRET_NOTE,
    ),
    Pattern(
        f"{_BGP_ID}option_set", _BGP_OPTION_SCOPE, r"set\s+(?P<field>\S+)(?:\s+(?P<value>.*))?",
        "bgp.options[] / bgp.neighbors[key].options[]", "set ebgp-multipath enable", _bgp_opt_set,
        note="`<道すじ> <欄> <値…>`。値は引用符を外した語を空白1つでつなぐ。値が無いか `''` なら `<欄>` だけ。"
        "秘密の欄(上の行と同じ判定)は `<欄>` だけ。" + _BGP_OPTION_NOTE + "。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_BGP_ID}option_unset", _BGP_OPTION_SCOPE, r"unset\s+(?P<field>\S+)",
        "bgp.options[] / bgp.neighbors[key].options[]", "unset ebgp-multipath", _opt_unset,
        note="`<道すじ> unset <欄>`。1語だけ。`unset a b` などは未解釈",
    ),
    Pattern(
        f"{_BGP_ID}option_config", _BGP_OPTION_SCOPE, r"config\s+(?P<name>.+)", "(道すじの段)", "config network",
        _opt_config,
        note="入れ子の config(network・network6・neighbor-range・redistribute・aggregate-address など)。"
        "中の行を道すじ付きで同じ表で読む。" + _BGP_OPTION_NOTE,
    ),
    Pattern(
        f"{_BGP_ID}option_edit", _BGP_OPTION_SCOPE, r"edit (?P<key>.*)", "(道すじの段)", "edit 1", _opt_edit,
        note="入れ子の config の中の edit。キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)。"
        "`config router bgp` の直下の edit は未解釈",
    ),
)


# --- OSPF(最上位の config router ospf。REQ-192、ADR-0139 決定2・4) ---

_OSPF_ID = "ospf.fortinet."
_OSPF_BLOCK = f"{_OSPF_ID}block"
_OSPF_HEADER = "config router ospf"
_OSPF_NETWORK_BLOCK = f"{_OSPF_ID}network_block"
_OSPF_OPTION_SCOPE = "ospf_option"
# FortiGate の OSPF のプロセスは1つ。鍵と process_id
_OSPF_KEY = "0"
# 表が持つ欄。この欄の、表に当たらない set・unset は options に入れず未解釈に残す(依頼書の条件4。ADR-0103 の補足)
_OSPF_FIELDS = frozenset({"router-id", "passive-interface"})
# network の edit の中で表が持つ欄。この欄の行が1つでも読めない edit は、networks に入れず edit ごと道すじにする
_OSPF_NETWORK_FIELD = re.compile(r"(?:set|unset)\s+(?:prefix|area)(?:\s.*)?")
# 値が ENC でない書き方でも値を持たない、OSPF の鍵の欄(依頼書 §2)
_OSPF_SECRET_FIELDS = ("md5-keys", "md5-key")
_DEFAULT_AREA = IPv4Address("0.0.0.0")


def _ospf_is_secret(field: str, words: list[str]) -> bool:
    """OSPF の options の秘密の判定。汎用の形の判定(_is_secret)より広い(秘密の決まりを足すだけで、緩めない)。

    次のどれかなら秘密: 汎用の形と同じ判定、値の語のどれかが(引用符を外して)ENC、名前が md5-keys・md5-key、
    Cisco の OSPF と同じ鍵の語の判定(ospf._is_secret。語のどれかが password・secret・key・message-digest-key・
    authentication-key か、語の中に鍵が埋まった形)を `set` の後ろの行に当てたもの。
    """
    texts = [unquote(word) for word in words]
    return (
        _is_secret(field, words)
        or field in _OSPF_SECRET_FIELDS
        or "ENC" in texts
        or cisco_ospf._is_secret(" ".join([field, *texts]))
    )


@dataclass
class _OspfCtx:
    """`config router ospf` の直下の行を読んでいる間の状態。"""

    ospf: Ospf


@dataclass
class _OspfNetCtx:
    """`config network` の edit 1つを読んでいる間の状態。同じ欄の行が2回あれば後の行の値。"""

    key: str = ""
    address: IPv4Address | None = None
    wildcard: IPv4Address | None = None
    area: IPv4Address | None = None


_OSPF_CONTENT_HEADS = frozenset({"set", "unset", EDIT})


def _has_setting_line(node: FlatNode) -> bool:
    """node の子孫(入れ子の config の中も含む)に、頭の語が set・unset・edit の行が1つでもあるか。"""
    return any(
        bool(set(line.text.split()[:1]) & _OSPF_CONTENT_HEADS) or _has_setting_line(line) for line in node.children
    )


def _ospf_has_content(tree: FlatTree) -> bool:
    """最上位の `config router ospf`(重なったものを含む)のどこかに、入れ子の中も含めて set・unset・edit の行が
    1つでもあるか。

    無ければ、どのブロックも今のまま汎用の形で写す(依頼書 §2 の ★)。OSPF を設定していない既定の出力
    (中身が空の `config redistribute "…"` が並ぶだけ)もこれに当たる(決定者の回答 2026-11-04「未設定の既定の形
    〔空の入れ子の config だけ〕は汎用の形のまま」)。set・unset・edit・config のどれでもない行も数えない
    (汎用の形でも未解釈のまま残る)。
    """
    return any(
        block.scope_id == CONFIG and block.text == _OSPF_HEADER and _has_setting_line(block) for block in tree.children
    )


def _ospf_router_id(m: re.Match[str], ctx: _OspfCtx) -> bool:
    try:
        ctx.ospf["router_id"] = str(IPv4Address(unquote(m["value"])))
    except ValueError:
        return False
    return True


def _ospf_passive(m: re.Match[str], ctx: _OspfCtx) -> bool:
    found = _VALUE_WORD.findall(m["value"])
    if _ospf_is_secret("passive-interface", found):  # 秘密の行は ospf_option の表で欄の名前だけにする
        return False
    words = [unquote(word) for word in found]
    if words == [""]:  # `''` は空の一覧(行が無いときと同じ)
        words = []
    elif "" in words:
        return False
    ctx.ospf["passive_interfaces"] = sorted(set(words))  # FortiOS の set は上書き。同じ欄が2回あれば後の行
    return True


def _ospf_network_open(m: re.Match[str], ctx: _OspfNetCtx) -> bool:
    ctx.key = m["key"]
    return True


def _ospf_prefix(m: re.Match[str], ctx: _OspfNetCtx) -> bool:
    try:
        address = IPv4Address(m["addr"])
        mask = int(IPv4Address(m["mask"]))
    except ValueError:
        return False
    inverted = ~mask & 0xFFFFFFFF
    if inverted & (inverted + 1):  # 連続したネットマスクだけ(0.0.0.255 のような逆の書き方は読めない形)
        return False
    ctx.address, ctx.wildcard = address, IPv4Address(inverted)
    return True


def _ospf_area(m: re.Match[str], ctx: _OspfNetCtx) -> bool:
    try:
        ctx.area = IPv4Address(m["area"])
    except ValueError:
        return False
    return True


_OSPF_NETWORK_TARGET = "ospf[\"0\"].networks[]"
_OSPF_OPTIONS = "ospf[\"0\"].options[]"
_OSPF_OPTION_NOTE = (
    "表に無い行を道すじで ospf[\"0\"].options(文字列の昇順の集合)に残す(ADR-0139 決定4)。"
    "入れ子は `<config の名前> > <edit のキー> > <行>` のように ` > ` でつなぐ(config の名前は引用符を外した語を空白1つでつなぐ)。"
    "network の edit の中の行は `network > <番号> > <行>`。子が1つも写らない config・edit は道すじだけを入れる"
)
_OSPF_SECRET_NOTE = (
    "秘密の行は、どの段でも値を持たず `<道すじ> <欄>` だけを options に入れる。秘密の判定は汎用の形と同じ判定"
    "(値の最初の語が ENC、名前が汎用の形の秘密の一覧に完全一致するか `hostkey-` で始まる)に、値の語のどれかが ENC、"
    f"名前が {'・'.join(_OSPF_SECRET_FIELDS)}、語のどれかが password・secret・key・message-digest-key・authentication-key "
    "か語の中に鍵が埋まった形(Cisco の OSPF と同じ判定)を足したもの。表が持つ欄でも、秘密の行は未解釈に残さず"
    "(未解釈の一覧と差分に原文が値ごと入るため)欄の名前だけを入れる"
)
_OSPF_NETWORK_NOTE = (
    "直下に読める `set prefix` があり、prefix・area の欄の行(set・unset とも)がすべて読める edit だけを networks の1件にする"
    "(edit の番号は持たない)。area の行が無ければ 0.0.0.0。ほかの行は ospf_option の表で `network > <番号> > <行>` に写す。"
    "そうでない edit(prefix の無い・読めない edit、area の読めない edit、unset prefix・unset area のある edit)は、"
    "edit ごと ospf_option の表で道すじ(`network > <番号> > …`、子の無い edit は `network > <番号>`)にする。"
    "同じ番号の edit が2つ以上あり、1つでも道すじにするものがあれば、その番号の edit はどれも道すじにする"
)
_OSPF_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        _OSPF_BLOCK, "top", _OSPF_HEADER, "ospf[\"0\"]", _OSPF_HEADER, _block,
        note="最上位の `config router ospf` だけを読む(VDOM の中と `config router ospf6` は今のまま汎用の形)。"
        "重なったブロックのどこかに(入れ子の中も含めて)set・unset・edit の行があれば写し、"
        "1つの ospf[\"0\"](process_id は 0、vrf は None)にまとめる。無ければ(空のブロックや、OSPF を設定していない"
        "既定の出力のような空の入れ子の config だけのブロック)、今のまま汎用の形(ospf は {})。"
        "写すときは vendor_specific.fortinet.config に入れない。"
        "passive_interface_default はいつも False、non_passive_interfaces はいつも空",
    ),
    Pattern(
        f"{_OSPF_ID}router_id", "router_ospf", rf"set router-id (?P<value>{_VALUE})", "ospf[\"0\"].router_id",
        "set router-id 192.0.2.1", _ospf_router_id,
        note="引用符を外して IPv4 として読めれば、その書き方(Cisco と同じ)。読めなければ未解釈。同じ欄が2回あれば後の行",
    ),
    Pattern(
        f"{_OSPF_ID}passive_interface", "router_ospf", rf"set passive-interface (?P<value>{_VALUES})",
        "ospf[\"0\"].passive_interfaces", 'set passive-interface "port1" "port2"', _ospf_passive,
        note="語ごとに引用符を外し、重ねずに文字列の昇順の集合にする。`''` だけの行は空の一覧(行が無いときと同じ)。"
        "同じ欄が2回あれば後の行で置き換える(FortiOS の set は上書き)",
    ),
    Pattern(
        _OSPF_NETWORK_BLOCK, "router_ospf", r"config network", "(段の見出し)", "config network", _block,
        note="直下の edit を networks に読む。edit でない行は ospf_option の表で `network > <行>` に写す。"
        "何も写らない段(edit が1つも無いなど)は options に `network` を入れる",
    ),
    Pattern(
        f"{_OSPF_ID}network", "ospf_network_edit", r"edit (?P<key>.+)", _OSPF_NETWORK_TARGET, "edit 1",
        _ospf_network_open, note=_OSPF_NETWORK_NOTE,
    ),
    Pattern(
        f"{_OSPF_ID}prefix", "ospf_network", rf"set prefix (?P<addr>{_IPV4}) (?P<mask>{_IPV4})",
        f"{_OSPF_NETWORK_TARGET}.address / wildcard", "set prefix 192.0.2.0 255.255.255.0", _ospf_prefix,
        note="address は書かれたアドレス、wildcard はマスクのビットの反転。マスクは連続したネットマスクだけ"
        "(`0.0.0.255` のような逆の書き方・マスクの無い形は読めない形で、edit ごと道すじにする)。同じ欄が2回あれば後の行",
    ),
    Pattern(
        f"{_OSPF_ID}area", "ospf_network", rf"set area (?P<area>{_IPV4})", f"{_OSPF_NETWORK_TARGET}.area",
        "set area 0.0.0.1", _ospf_area,
        note="点で区切った IPv4 の書き方だけ(数だけの書き方は読めない形で、edit ごと道すじにする)。"
        "行が無ければ 0.0.0.0(`set area 0.0.0.0` の行と同じ値)。同じ欄が2回あれば後の行",
    ),
    Pattern(
        f"{_OSPF_ID}option_secret_enc", _OSPF_OPTION_SCOPE, r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""",
        _OSPF_OPTIONS, "set authentication-key ENC EXAMPLEONLY", _opt_secret,
        note=_OSPF_SECRET_NOTE + "。値の最初の語は引用符を外して比べる",
    ),
    Pattern(
        f"{_OSPF_ID}option_secret", _OSPF_OPTION_SCOPE,
        rf"set\s+(?P<field>{'|'.join(map(re.escape, (*SECRET_FIELDS, *_OSPF_SECRET_FIELDS)))}"
        rf"|{re.escape(_SECRET_PREFIX)}\S+)(?:\s.*)?",
        _OSPF_OPTIONS, "set md5-keys 1 ENC EXAMPLEONLY", _opt_secret, note=_OSPF_SECRET_NOTE,
    ),
    Pattern(
        f"{_OSPF_ID}option_set", _OSPF_OPTION_SCOPE, r"set\s+(?P<field>\S+)(?:\s+(?P<value>.*))?",
        _OSPF_OPTIONS, "set abr-type standard", _opt_set(_ospf_is_secret),
        note="`<道すじ> <欄> <値…>`。値は引用符を外した語を空白1つでつなぐ。値が無いか `''` なら `<欄>` だけ。"
        "秘密の行(上の行と同じ判定。値を語に分けてから見る)は `<欄>` だけ。直下の router-id・passive-interface の、"
        "表に当たらない形は未解釈。" + _OSPF_OPTION_NOTE + "。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_OSPF_ID}option_unset", _OSPF_OPTION_SCOPE, r"unset\s+(?P<field>\S+)", _OSPF_OPTIONS,
        "unset default-information-metric", _opt_unset,
        note="`<道すじ> unset <欄>`。1語だけ。`unset a b` などと、直下の `unset router-id`・`unset passive-interface` は未解釈",
    ),
    Pattern(
        f"{_OSPF_ID}option_config", _OSPF_OPTION_SCOPE, r"config\s+(?P<name>.+)", "(道すじの段)", "config area",
        _opt_config,
        note="入れ子の config(area・ospf-interface・redistribute・distribute-list・summary-address など)。"
        "中の行を道すじ付きで同じ表で読む。" + _OSPF_OPTION_NOTE,
    ),
    Pattern(
        f"{_OSPF_ID}option_edit", _OSPF_OPTION_SCOPE, r"edit (?P<key>.*)", "(道すじの段)", "edit 0.0.0.1", _opt_edit,
        note="入れ子の config の中の edit。キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)。"
        "`config router ospf` の直下の edit は未解釈",
    ),
)


# --- IPsec(最上位の config vpn ipsec phase1・phase2・phase1-interface・phase2-interface。REQ-193、ADR-0139 決定2・4) ---

_IPSEC_ID = "ipsec.fortinet."
_IPSEC_BLOCK = f"{_IPSEC_ID}block"
_IPSEC_PREFIX = "config vpn ipsec "
_IPSEC_KINDS = ("phase1", "phase2", "phase1-interface", "phase2-interface")
_IPSEC_EDIT_SCOPE = "ipsec_edit"
_IPSEC_OPTION_SCOPE = "ipsec_option"
# 欄の名前にこの文字列を含めば秘密(psksecret-remote・authpasswd など、汎用の形の一覧に無い名前も拾う)
_IPSEC_SECRET_PARTS = ("secret", "passwd", "password")


def _ipsec_is_secret(field: str, words: list[str]) -> bool:
    """IPsec の settings の秘密の判定。汎用の形の判定(_is_secret)より広い(秘密の決まりを足すだけで、緩めない)。

    次のどれかなら秘密: 汎用の形と同じ判定、値の語のどれかが(引用符を外して)ENC、欄の名前が secret・passwd・password を
    含む(psksecret・psksecret-remote・ppk-secret・authpasswd・group-authentication-secret など)。
    秘密でない欄を秘密と見ても、値が IR に出なくなるだけで漏れは起きない向きの判定。
    """
    return (
        _is_secret(field, words)
        or "ENC" in (unquote(word) for word in words)
        or any(part in field for part in _IPSEC_SECRET_PARTS)
    )


@dataclass
class _IpsecEditCtx:
    """ブロックの直下の edit 1つを開くときの状態。key は edit のキー。"""

    key: str = ""


def _ipsec_edit(m: re.Match[str], ctx: _IpsecEditCtx) -> bool:
    ctx.key = m["key"]
    return True


def _ipsec_secret(m: re.Match[str], ctx: _OptCtx) -> bool:
    ctx.secret = True  # 値も欄の名前も settings に入れない。keys_configured を True にする
    return True


_IPSEC_TARGET = "ipsec.blocks[\"<種類> <名前>\"]"
_IPSEC_SETTINGS = f"{_IPSEC_TARGET}.settings[]"
_IPSEC_KEY_NOTE = (
    "鍵は `<種類> <名前>`(種類は見出しから `config vpn ipsec ` を外した語、名前は edit のキー。例 `phase1-interface vpn-a`)。"
    "同じ種類・同じ名前の edit(重なったブロックを含む)は1つにまとめる(settings は和、keys_configured はどれかが True なら True)"
)
_IPSEC_SETTINGS_NOTE = (
    "edit の中の行を、settings(文字列の昇順の集合)に写す。入れ子は `<config の名前> > <edit のキー> > <行>` のように "
    "` > ` でつなぐ(config の名前は引用符を外した語を空白1つでつなぐ)。子が1つも写らない config・edit は道すじだけを入れる"
)
_IPSEC_SECRET_NOTE = (
    "秘密の行は、どの段でも値も欄の名前も settings に入れず、その項目の keys_configured を True にする"
    "(値の変更・秘密の行の数は見ない。Cisco と同じ)。秘密の判定は汎用の形と同じ判定(値の最初の語が ENC、"
    "名前が汎用の形の秘密の一覧に完全一致するか `hostkey-` で始まる)に、値の語のどれかが ENC、"
    f"欄の名前が {'・'.join(_IPSEC_SECRET_PARTS)} を含む、を足したもの。`unset <欄>` は値を持たないので秘密にしない"
)
_IPSEC_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        _IPSEC_BLOCK, "top", rf"{_IPSEC_PREFIX}(?:{'|'.join(map(re.escape, _IPSEC_KINDS))})", "ipsec.blocks",
        "config vpn ipsec phase1-interface", _block,
        note="最上位の4つの見出しだけを読む(`vpn ipsec manualkey`・`concentrator` などと VDOM の中は今のまま汎用の形)。"
        "vendor_specific.fortinet.config には入れない。直下の edit ごとに ipsec.blocks の項目を作る"
        "(ipsec が None なら作る。options はいつも空)。edit が無いブロックは見出しだけを解釈済みにし、何も足さない"
        "(ほかに写す edit が無ければ ipsec は None)。直下の edit でない行は未解釈",
    ),
    Pattern(
        f"{_IPSEC_ID}edit", _IPSEC_EDIT_SCOPE, r"edit (?P<key>.+)", _IPSEC_TARGET, "edit vpn-a", _ipsec_edit,
        note=_IPSEC_KEY_NOTE + "。keys_configured の既定は False",
    ),
    Pattern(
        f"{_IPSEC_ID}option_secret_enc", _IPSEC_OPTION_SCOPE, r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""",
        f"{_IPSEC_TARGET}.keys_configured", "set psksecret ENC EXAMPLEONLY", _ipsec_secret,
        note=_IPSEC_SECRET_NOTE + "。値の最初の語は引用符を外して比べる",
    ),
    Pattern(
        f"{_IPSEC_ID}option_secret", _IPSEC_OPTION_SCOPE,
        rf"set\s+(?P<field>{'|'.join(map(re.escape, SECRET_FIELDS))}|{re.escape(_SECRET_PREFIX)}\S+"
        rf"|\S*(?:{'|'.join(_IPSEC_SECRET_PARTS)})\S*)(?:\s.*)?",
        f"{_IPSEC_TARGET}.keys_configured", "set authpasswd EXAMPLEONLY", _ipsec_secret, note=_IPSEC_SECRET_NOTE,
    ),
    Pattern(
        f"{_IPSEC_ID}option_set", _IPSEC_OPTION_SCOPE, r"set\s+(?P<field>\S+)(?:\s+(?P<value>.*))?", _IPSEC_SETTINGS,
        "set remote-gw 192.0.2.1", _opt_set(_ipsec_is_secret, drop=True),
        note="`<道すじ> <欄> <値…>`。値は引用符を外した語を空白1つでつなぐ。値が無いか `''` なら `<欄>` だけ。"
        "秘密の行(上の行と同じ判定。値を語に分けてから見る)は何も入れず keys_configured を True にする。"
        + _IPSEC_SETTINGS_NOTE + "。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_IPSEC_ID}option_unset", _IPSEC_OPTION_SCOPE, r"unset\s+(?P<field>\S+)", _IPSEC_SETTINGS,
        "unset remote-gw", _opt_unset, note="`<道すじ> unset <欄>`。1語だけ。`unset a b` などは未解釈",
    ),
    Pattern(
        f"{_IPSEC_ID}option_config", _IPSEC_OPTION_SCOPE, r"config\s+(?P<name>.+)", "(道すじの段)",
        "config ipv4-exclude-range", _opt_config,
        note="入れ子の config(ipv4-exclude-range・backup-gateway など)。中の行を道すじ付きで同じ表で読む。"
        + _IPSEC_SETTINGS_NOTE,
    ),
    Pattern(
        f"{_IPSEC_ID}option_edit", _IPSEC_OPTION_SCOPE, r"edit (?P<key>.*)", "(道すじの段)", "edit 1", _opt_edit,
        note="入れ子の config の中の edit。キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)",
    ),
)


# --- SD-WAN(最上位の config system sdwan・config system virtual-wan-link。REQ-194、ADR-0139 決定2・4) ---

_SDWAN_ID = "sdwan.fortinet."
_SDWAN_BLOCK = f"{_SDWAN_ID}block"
_SDWAN_VWL_BLOCK = f"{_SDWAN_ID}virtual_wan_link_block"
# 見出しの行 → 鍵の頭の語(<S>)。見出しはこの2つに完全一致するものだけ
_SDWAN_HEADERS = {"config system sdwan": "sdwan", "config system virtual-wan-link": "virtual-wan-link"}
_SDWAN_BLOCK_IDS = frozenset({_SDWAN_BLOCK, _SDWAN_VWL_BLOCK})
_SDWAN_BLOCK_SCOPE = "sdwan_block"
_SDWAN_TABLE_SCOPE = "sdwan_table"
_SDWAN_OPTION_SCOPE = "sdwan_option"
# 上から当たる表(順に意味がある)と、その順を入れる鍵の後ろの語(`<S> service-order`)
_SDWAN_ORDERED_TABLE = "service"
_SDWAN_ORDER_KEY = "service-order"


@dataclass
class _SdwanTableCtx:
    """見出しの直下の `config <表>` の行を開くときの状態。name は表の名前(引用符を外した語を空白1つでつなぐ)。"""

    name: str = ""


@dataclass
class _SdwanEditCtx:
    """表の直下の `edit <キー>` の行を開くときの状態。key は edit のキー。"""

    key: str = ""


def _sdwan_table(m: re.Match[str], ctx: _SdwanTableCtx) -> bool:
    name = m["name"]
    ctx.name = " ".join(unquote(word) for word in _VALUE_WORD.findall(name)) or name
    return True


def _sdwan_edit(m: re.Match[str], ctx: _SdwanEditCtx) -> bool:
    ctx.key = m["key"]
    return True


def _sdwan_headers_to_map(tree: FlatTree) -> frozenset[str]:
    """写す SD-WAN の見出し(`config system sdwan`・`config system virtual-wan-link`)の集合。

    見出しごとに、重なった同じ見出しのブロックも含めて、どこかに(入れ子の中も含めて)set・unset・edit の行が1つでも
    あれば写す。無ければ(空のブロック、空の入れ子の config だけのブロック、set・unset・edit・config のどれでもない行
    だけのブロック)、その見出しのどのブロックも今のまま汎用の形(決定者の回答 2026-11-04。REQ-192 の OSPF と同じ考え方)。
    """
    return frozenset(
        header
        for header in _SDWAN_HEADERS
        if any(block.scope_id == CONFIG and block.text == header and _has_setting_line(block) for block in tree.children)
    )


_SDWAN_TARGET = "sdwan[\"<S>\"]"
_SDWAN_KEY_NOTE = (
    "`<S>` は見出しの語(`sdwan` か `virtual-wan-link`)。鍵は、見出しの直下の行が `<S>`、`config <表>` の直下の edit でない行が "
    "`<S> <表>`、`config <表>` > `edit <キー>` の中の行が `<S> <表> <キー>`(例 `sdwan members 1`・`sdwan service 2`)。"
    "写す見出し・表・edit には、中に行が無くても鍵を作る(settings は空。決定者の回答 2026-11-04)。"
    "重なったブロック・同じ名前の表・同じキーの edit は同じ鍵にまとめる(settings は和)"
)
_SDWAN_SETTINGS_NOTE = (
    "settings(文字列の昇順の集合)の書き方は BGP の options と同じ。入れ子は `<config の名前> > <edit のキー> > <行>` のように "
    "` > ` でつなぐ(config の名前は引用符を外した語を空白1つでつなぐ)。子が1つも写らない config・edit は道すじだけを入れる"
)
_SDWAN_SECRET_NOTE = (
    "秘密の欄(値の最初の語が ENC、名前が汎用の形の秘密の一覧に完全一致するか `hostkey-` で始まる。汎用の形と同じ判定)は、"
    "どの段でも値を持たず `<道すじ> <欄>`(語の名前だけ)を settings に入れる"
)
_SDWAN_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        _SDWAN_BLOCK, "top", "config system sdwan", "sdwan[\"sdwan\"]", "config system sdwan", _block,
        note="最上位の `config system sdwan` だけを読む(VDOM の中は今のまま汎用の形)。重なったブロックも含めて、"
        "どこかに(入れ子の中も含めて)set・unset・edit の行があれば写す。無ければ(空のブロック、空の入れ子の config だけの"
        "ブロックなど)、どのブロックも今のまま汎用の形。写すときは vendor_specific.fortinet.config に入れない。"
        "直下の set・unset は鍵 `sdwan` の settings、`config <表>` は鍵 `sdwan <表>`。直下の edit は未解釈",
    ),
    Pattern(
        _SDWAN_VWL_BLOCK, "top", "config system virtual-wan-link", "sdwan[\"virtual-wan-link\"]",
        "config system virtual-wan-link", _block,
        note="古い版の書き方。`config system sdwan` と同じ規則で読み、鍵の頭の語は `virtual-wan-link`"
        "(`virtual-wan-link members 1` など)。写すかどうかは `config system sdwan` と別に決める",
    ),
    Pattern(
        f"{_SDWAN_ID}table", _SDWAN_BLOCK_SCOPE, r"config\s+(?P<name>.+)", "sdwan[\"<S> <表>\"]",
        "config members", _sdwan_table,
        note="見出しの直下の `config <表>`(zone・members・health-check・service・neighbor など)。"
        "表の名前は引用符を外した語を空白1つでつなぐ。" + _SDWAN_KEY_NOTE,
    ),
    Pattern(
        f"{_SDWAN_ID}edit", _SDWAN_TABLE_SCOPE, r"edit (?P<key>.+)", "sdwan[\"<S> <表> <キー>\"]", "edit 1",
        _sdwan_edit,
        note="表の直下の edit。キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)。"
        "`config service` の直下の edit のキーは、出てきた順(重なったブロック・同じ名前の表はつなげて数え、同じキーは"
        "最初の出現だけ)に、鍵 `<S> service-order` の settings に `order <キー> <キー> …` の1つの文字列で入れる"
        "(上から当たる表なので、順の入れ替えが差分に出る。edit の無い `config service` では作らない)。" + _SDWAN_KEY_NOTE,
    ),
    Pattern(
        f"{_SDWAN_ID}option_secret_enc", _SDWAN_OPTION_SCOPE, r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""",
        f"{_SDWAN_TARGET}.settings[]", "set password ENC EXAMPLEONLY", _opt_secret,
        note=_SDWAN_SECRET_NOTE + "。値の最初の語は引用符を外して比べる",
    ),
    Pattern(
        f"{_SDWAN_ID}option_secret", _SDWAN_OPTION_SCOPE,
        rf"set\s+(?P<field>{'|'.join(map(re.escape, SECRET_FIELDS))}|{re.escape(_SECRET_PREFIX)}\S+)(?:\s.*)?",
        f"{_SDWAN_TARGET}.settings[]", "set password EXAMPLEONLY", _opt_secret, note=_SDWAN_SECRET_NOTE,
    ),
    Pattern(
        f"{_SDWAN_ID}option_set", _SDWAN_OPTION_SCOPE, r"set\s+(?P<field>\S+)(?:\s+(?P<value>.*))?",
        f"{_SDWAN_TARGET}.settings[]", "set status enable", _opt_set(_is_secret),
        note="`<道すじ> <欄> <値…>`。値は引用符を外した語を空白1つでつなぐ。値が無いか `''` なら `<欄>` だけ。"
        "秘密の欄(上の行と同じ判定)は `<欄>` だけ。" + _SDWAN_SETTINGS_NOTE + "。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_SDWAN_ID}option_unset", _SDWAN_OPTION_SCOPE, r"unset\s+(?P<field>\S+)", f"{_SDWAN_TARGET}.settings[]",
        "unset load-balance-mode", _opt_unset, note="`<道すじ> unset <欄>`。1語だけ。`unset a b` などは未解釈",
    ),
    Pattern(
        f"{_SDWAN_ID}option_config", _SDWAN_OPTION_SCOPE, r"config\s+(?P<name>.+)", "(道すじの段)", "config sla",
        _opt_config,
        note="表の中や edit の中の入れ子の config(health-check・service の sla など)。中の行を道すじ付きで同じ表で読む。"
        + _SDWAN_SETTINGS_NOTE,
    ),
    Pattern(
        f"{_SDWAN_ID}option_edit", _SDWAN_OPTION_SCOPE, r"edit (?P<key>.*)", "(道すじの段)", "edit 1", _opt_edit,
        note="入れ子の config の中の edit。キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)",
    ),
)


# --- 経路の方針(最上位の config router prefix-list・prefix-list6・route-map。REQ-195、ADR-0139 決定2・4) ---

_PL_ID = "prefix_lists.fortinet."
_PL6_ID = "ipv6_prefix_lists.fortinet."
_RM_ID = "route_maps.fortinet."
_PL_HEADER = "config router prefix-list"
_PL6_HEADER = "config router prefix-list6"
_RM_HEADER = "config router route-map"
_PL_BLOCK = f"{_PL_ID}block"
_PL6_BLOCK = f"{_PL6_ID}block"
_RM_BLOCK = f"{_RM_ID}block"
_RM_RULE_BLOCK = f"{_RM_ID}rule_block"
_RM_OPTION_SCOPE = "route_map_option"
# route-map の rule の中で表が持つ欄。この欄の、表に当たらない set・unset は options に入れず未解釈に残す
# (依頼書の条件4。ADR-0103 の補足)
_RM_FIELDS = frozenset({"action"})
# 値1語以上(空白2つ・タブも1つの区切り)
_SPACED_VALUES = rf"(?:{_VALUE})(?:\s+(?:{_VALUE}))*"
_ACTIONS = r"(?P<action>permit|deny)"


@dataclass(frozen=True)
class _PlSpec:
    """prefix-list の見出し1つ。scope は scope の名前の頭、v6 は prefix-list6 か。"""

    header: str
    id: str
    scope: str
    v6: bool


_PL_SPECS: tuple[_PlSpec, ...] = (
    _PlSpec(_PL_HEADER, _PL_ID, "prefix_list", False),
    _PlSpec(_PL6_HEADER, _PL6_ID, "prefix_list6", True),
)
_PL_BY_HEADER: dict[str, _PlSpec] = {spec.header: spec for spec in _PL_SPECS}
_ROUTE_LIST_HEADERS = (_PL_HEADER, _PL6_HEADER, _RM_HEADER)
_ROUTE_LIST_BLOCK_IDS = frozenset({_PL_BLOCK, _PL6_BLOCK, _RM_BLOCK})


@dataclass
class _RouteListCtx:
    """リスト(見出しの直下の edit)か rule の edit の行を開くときの状態。key はリストの名前か rule の番号。"""

    key: str = ""


@dataclass
class _PlCtx:
    """prefix-list のリストの直下か rule の edit の中の行を読む状態。values は行があった欄だけを持つ(同じ欄は後の行)。"""

    values: dict[str, Any] = field(default_factory=dict)


@dataclass
class _RmCtx:
    """route-map の rule の edit の中の行を読む状態。"""

    entry: RouteMapEntry


def _rl_open(m: re.Match[str], ctx: _RouteListCtx) -> bool:
    ctx.key = m["key"]
    return True


def _rl_rule_open(m: re.Match[str], ctx: _RouteListCtx) -> bool:
    ctx.key = str(int(m["key"]))
    return True


def _pl_comments(m: re.Match[str], ctx: _PlCtx) -> bool:
    value = m["value"]
    words = _VALUE_WORD.findall(value) if value is not None else []
    if _is_secret("comments", words):  # 秘密の行はリストごと汎用の形(secrets)に残す
        return False
    ctx.values["description"] = " ".join(unquote(word) for word in words) or None
    return True


def _pl_action(m: re.Match[str], ctx: _PlCtx) -> bool:
    ctx.values["action"] = m["action"]
    return True


def _pl_prefix(m: re.Match[str], ctx: _PlCtx) -> bool:
    try:
        address = IPv4Address(m["addr"])
        mask = int(IPv4Address(m["mask"]))
    except ValueError:
        return False
    inverted = ~mask & 0xFFFFFFFF
    if inverted & (inverted + 1):  # 連続したネットマスクだけ(0.0.0.255 のような逆の書き方は読めない形)
        return False
    ctx.values["prefix"] = str(IPv4Network(f"{address}/{32 - inverted.bit_length()}", strict=False))
    return True


def _pl_prefix6(m: re.Match[str], ctx: _PlCtx) -> bool:
    try:
        network = IPv6Network(m["prefix"], strict=False)
    except ValueError:  # IPv6 として読めない(IPv4 の書き方など)
        return False
    ctx.values["prefix"] = str(network)
    return True


def _pl_length(m: re.Match[str], ctx: _PlCtx) -> bool:
    ctx.values[m["field"]] = int(m["value"])
    return True


def _pl_unset_length(m: re.Match[str], ctx: _PlCtx) -> bool:
    ctx.values[m["field"]] = None
    return True


def _rm_secret(m: re.Match[str], ctx: _RmCtx) -> bool:
    _add_sorted(ctx.entry["options"], m["field"])  # 値は持たない。欄の名前だけ
    return True


def _rm_action(m: re.Match[str], ctx: _RmCtx) -> bool:
    ctx.entry["action"] = m["action"]  # type: ignore[typeddict-item]
    return True


def _rm_list(key: str):
    """`set match-<語> <値…>`・`set set-<語> <値…>` を match・set の `<語> <値…>` にする apply。"""

    def apply(m: re.Match[str], ctx: _RmCtx) -> bool:
        value = m["value"]
        words = _VALUE_WORD.findall(value) if value is not None else []
        if _is_secret(f"{key}-{m['word']}", words):  # 秘密の行は route_map_option の表で欄の名前だけにする
            return False
        joined = " ".join(unquote(word) for word in words)
        _add_sorted(ctx.entry[key], f"{m['word']} {joined}" if joined else m["word"])  # type: ignore[literal-required]
        return True

    return apply


_PL_TARGET = "<prefix_lists|ipv6_prefix_lists>[name]"
_PL_KEEP_NOTE = (
    "リスト(見出しの直下の `edit <名前>`)の中に表に当たらない行があれば、そのリストは写さず、今のまま汎用の形で "
    "vendor_specific.fortinet.config[\"router prefix-list\"] などに残す(行を失わないため。PrefixListEntry に残りを置く欄が無い)。"
    "当たる例: `set prefix any`・読めないアドレスやマスク・引用符の付いた値・`unset action`・`unset prefix`・入れ子の config・"
    "表に無い欄・秘密の行、`set prefix` の無い rule、`set comments`・`config rule` 以外の直下の行、`config rule` の直下の "
    "edit でない行・edit の無い `config rule`、数字でない rule の番号。同じ名前のリスト(重なったブロックを含む)は"
    "まとめて決め、1つでも残すものがあれば、その名前のリストはどれも汎用の形に残す"
)
_PL_RULE_NOTE = "同じ番号の rule はまとめ、欄ごとに後の行の値。" + _GEN_SPACE_NOTE


def _pl_patterns(spec: _PlSpec) -> tuple[Pattern, ...]:
    """prefix-list(または prefix-list6)の表。scope は <頭>_edit(見出しの直下の edit)/ <頭>(リストの中)/
    <頭>_rule_edit(`config rule` の直下の edit)/ <頭>_rule(rule の edit の中)。"""
    category = spec.id.split(".", 1)[0]
    target = f"{category}[name]"
    entry = f"{target}.entries[番号]"
    if spec.v6:
        prefix = Pattern(
            f"{spec.id}prefix6", f"{spec.scope}_rule", r"set\s+prefix6\s+(?P<prefix>[0-9A-Fa-f:.]+/\d{1,3})",
            f"{entry}.prefix", "set prefix6 2001:db8:1::/48", _pl_prefix6,
            note="ip_network の正規の書き方(strict=False。ホストのビットは落とす。ADR-0047 決定2)。"
            "IPv6 として読めない値(IPv4 の書き方・`any` など)はリストごと汎用の形に残す",
        )
    else:
        prefix = Pattern(
            f"{spec.id}prefix", f"{spec.scope}_rule", rf"set\s+prefix\s+(?P<addr>{_IPV4})\s+(?P<mask>{_IPV4})",
            f"{entry}.prefix", "set prefix 192.0.2.0 255.255.255.0", _pl_prefix,
            note="`<アドレス>/<長さ>` の ip_network の正規の書き方(strict=False。ホストのビットは落とす。ADR-0047 決定2)。"
            "マスクは連続したネットマスクだけ(`0.0.0.255` のような逆の書き方・連続していないマスク・`any` は読めない形で、"
            "リストごと汎用の形に残す)",
        )
    return (
        Pattern(
            f"{spec.id}block", "top", spec.header, target, spec.header, _block,
            note=f"最上位の `{spec.header}` だけを読む(VDOM の中は今のまま汎用の形)。"
            "写すリストが1つも無い見出し(edit の無いブロック、全部のリストが汎用の形に残るブロック)は、"
            "今のまま汎用の形の見出しで読む。写す見出しでも、汎用の形に残すリストと直下の edit でない行は、汎用の形で "
            f"vendor_specific.fortinet.config[\"{spec.header.removeprefix('config ')}\"] に写す"
            "(1行も無ければ config に入れない。2か所の差分にしない)",
        ),
        Pattern(
            f"{spec.id}list", f"{spec.scope}_edit", r"edit (?P<key>.+)", target, 'edit PL-A', _rl_open,
            note="鍵は edit のキー(引用符を外した原文)。rule の無いリストは entries が空。"
            "同じ名前のリスト(重なったブロックを含む)は1つにまとめる。" + _PL_KEEP_NOTE,
        ),
        Pattern(
            f"{spec.id}comments", spec.scope, rf"set\s+comments(?:\s+(?P<value>{_SPACED_VALUES}))?",
            f"{target}.description", 'set comments "office nets"', _pl_comments,
            note="引用符を外した語を空白1つでつないだ原文。`''` と値の無い行は None。行が無ければ None。同じ欄は後の行。"
            "秘密の行(値の最初の語が ENC など。汎用の形と同じ判定)はリストごと汎用の形に残す",
        ),
        Pattern(
            f"{spec.id}rule_block", spec.scope, r"config\s+rule", "(段の見出し)", "config rule", _block,
            note="直下の `edit <番号>` を entries にする。重なった `config rule` はまとめる。"
            "edit の無い `config rule` と、直下に edit でない行のある `config rule` は、リストごと汎用の形に残す",
        ),
        Pattern(
            f"{spec.id}rule", f"{spec.scope}_rule_edit", r"edit (?P<key>[0-9]+)", entry, "edit 1", _rl_rule_open,
            note="鍵は番号の str(int(…))。番号が数字でない rule のあるリストは汎用の形に残す。"
            "`set prefix" + ("6" if spec.v6 else "") + "` の無い rule のあるリストも汎用の形に残す(既定値を推測しない。"
            "ADR-0119 決定3)。eq はいつも None(FortiOS に無い)。" + _PL_RULE_NOTE,
        ),
        Pattern(
            f"{spec.id}action", f"{spec.scope}_rule", rf"set\s+action\s+{_ACTIONS}", f"{entry}.action",
            "set action deny", _pl_action,
            note="permit / deny。行が無ければ permit(FortiOS の既定。依頼書 §2)。ほかの値はリストごと汎用の形に残す",
        ),
        prefix,
        Pattern(
            f"{spec.id}ge_le", f"{spec.scope}_rule", r"set\s+(?P<field>ge|le)\s+(?P<value>[0-9]+)", f"{entry}.ge / le",
            "set le 32", _pl_length,
            note="数(int)。範囲は見ない(Cisco・Arista と同じ)。行が無ければ None。数でない値はリストごと汎用の形に残す",
        ),
        Pattern(
            f"{spec.id}unset_ge_le", f"{spec.scope}_rule", r"unset\s+(?P<field>ge|le)", f"{entry}.ge / le = None",
            "unset ge", _pl_unset_length,
            note="None(行が無いときと同じ値。依頼書 §2 の表)。ほかの欄の unset はリストごと汎用の形に残す",
        ),
    )


_RM_TARGET = "route_maps[name].entries[番号]"
_RM_KEEP_NOTE = (
    "リストの直下の `set comments`(どの rule にも属さない)・`config rule` 以外の行、`config rule` の直下の edit でない行・"
    "edit の無い `config rule`、数字でない rule の番号があれば、そのリストは写さず、今のまま汎用の形で "
    "vendor_specific.fortinet.config[\"router route-map\"] に残す。同じ名前のリスト(重なったブロックを含む)はまとめて決め、"
    "1つでも残すものがあれば、その名前のリストはどれも汎用の形に残す"
)
_RM_OPTION_NOTE = (
    "rule の中の表に無い行を道すじで options(文字列の昇順の集合)に残す(ADR-0139 決定4)。入れ子は "
    "`<config の名前> > <edit のキー> > <行>` のように ` > ` でつなぐ(config の名前は引用符を外した語を空白1つでつなぐ)。"
    "子が1つも写らない config・edit は道すじだけを入れる"
)
_RM_SECRET_NOTE = (
    "秘密の欄(値の最初の語が ENC、名前が汎用の形の秘密の一覧に完全一致するか `hostkey-` で始まる。汎用の形と同じ判定)は、"
    "どの段でも値を持たず `<道すじ> <欄>`(語の名前だけ)を options に入れる。match-・set- の欄でも同じ"
)
_RM_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        _RM_BLOCK, "top", _RM_HEADER, "route_maps[name]", _RM_HEADER, _block,
        note="最上位の `config router route-map` だけを読む(VDOM の中は今のまま汎用の形)。"
        "写すリストが1つも無い見出し(edit の無いブロック、全部のリストが汎用の形に残るブロック)は、"
        "今のまま汎用の形の見出しで読む。写す見出しでも、汎用の形に残すリストと直下の edit でない行は、汎用の形で "
        "vendor_specific.fortinet.config[\"router route-map\"] に写す(1行も無ければ config に入れない。2か所の差分にしない)",
    ),
    Pattern(
        f"{_RM_ID}list", "route_map_edit", r"edit (?P<key>.+)", "route_maps[name]", "edit RM-A", _rl_open,
        note="鍵は edit のキー(引用符を外した原文)。rule の無いリストは entries が空。"
        "同じ名前のリスト(重なったブロックを含む)は1つにまとめる。" + _RM_KEEP_NOTE,
    ),
    Pattern(
        _RM_RULE_BLOCK, "route_map", r"config\s+rule", "(段の見出し)", "config rule", _block,
        note="直下の `edit <番号>` を entries にする。重なった `config rule` はまとめる",
    ),
    Pattern(
        f"{_RM_ID}rule", "route_map_rule_edit", r"edit (?P<key>[0-9]+)", _RM_TARGET, "edit 10", _rl_rule_open,
        note="鍵は番号の str(int(…))。description はいつも None。同じ番号の rule はまとめる(action は後の行、"
        "match・set・options は和)",
    ),
    Pattern(
        f"{_RM_ID}secret_enc", "route_map_rule", r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""",
        f"{_RM_TARGET}.options[]", "set match-x ENC EXAMPLEONLY", _rm_secret,
        note=_RM_SECRET_NOTE + "。値の最初の語は引用符を外して比べる",
    ),
    Pattern(
        f"{_RM_ID}secret", "route_map_rule",
        rf"set\s+(?P<field>{'|'.join(map(re.escape, SECRET_FIELDS))}|{re.escape(_SECRET_PREFIX)}\S+)(?:\s.*)?",
        f"{_RM_TARGET}.options[]", "set password EXAMPLEONLY", _rm_secret, note=_RM_SECRET_NOTE,
    ),
    Pattern(
        f"{_RM_ID}action", "route_map_rule", rf"set\s+action\s+{_ACTIONS}", f"{_RM_TARGET}.action", "set action deny",
        _rm_action,
        note="permit / deny。行が無ければ permit(FortiOS の既定。依頼書 §2)。ほかの値・値の無い行・`unset action` は"
        "options に入れず未解釈に残す(rule は写す。依頼書の条件4)。同じ欄は後の行",
    ),
    Pattern(
        f"{_RM_ID}match", "route_map_rule", r"set\s+match-(?P<word>\S+)(?:\s+(?P<value>.*))?", f"{_RM_TARGET}.match[]",
        'set match-ip-address "PL-A"', _rm_list("match"),
        note="`<語> <値…>`(`match-` の後ろの語と、引用符を外した値の語を空白1つでつなぐ)。値が無いか `''` なら `<語>` だけ。"
        "文字列の昇順の集合。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_RM_ID}set", "route_map_rule", r"set\s+set-(?P<word>\S+)(?:\s+(?P<value>.*))?", f"{_RM_TARGET}.set[]",
        "set set-local-preference 200", _rm_list("set"),
        note="`<語> <値…>`(`set-` の後ろの語と、引用符を外した値の語を空白1つでつなぐ)。値が無いか `''` なら `<語>` だけ。"
        "文字列の昇順の集合。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_RM_ID}option_secret_enc", _RM_OPTION_SCOPE, r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""",
        f"{_RM_TARGET}.options[]", "set auth-x ENC EXAMPLEONLY", _opt_secret,
        note=_RM_SECRET_NOTE + "。値の最初の語は引用符を外して比べる",
    ),
    Pattern(
        f"{_RM_ID}option_secret", _RM_OPTION_SCOPE,
        rf"set\s+(?P<field>{'|'.join(map(re.escape, SECRET_FIELDS))}|{re.escape(_SECRET_PREFIX)}\S+)(?:\s.*)?",
        f"{_RM_TARGET}.options[]", "set secret EXAMPLEONLY", _opt_secret, note=_RM_SECRET_NOTE,
    ),
    Pattern(
        f"{_RM_ID}option_set", _RM_OPTION_SCOPE, r"set\s+(?P<field>\S+)(?:\s+(?P<value>.*))?", f"{_RM_TARGET}.options[]",
        "set as 65001", _opt_set(_is_secret),
        note="`<道すじ> <欄> <値…>`。値は引用符を外した語を空白1つでつなぐ。値が無いか `''` なら `<欄>` だけ。"
        "秘密の欄(上の行と同じ判定)は `<欄>` だけ。rule の直下の action の、表に当たらない形は未解釈。"
        + _RM_OPTION_NOTE + "。" + _GEN_SPACE_NOTE,
    ),
    Pattern(
        f"{_RM_ID}option_unset", _RM_OPTION_SCOPE, r"unset\s+(?P<field>\S+)", f"{_RM_TARGET}.options[]",
        "unset set-ip-nexthop", _opt_unset,
        note="`<道すじ> unset <欄>`。1語だけ。`unset a b` などと、rule の直下の `unset action` は未解釈",
    ),
    Pattern(
        f"{_RM_ID}option_config", _RM_OPTION_SCOPE, r"config\s+(?P<name>.+)", "(道すじの段)", "config set-aspath",
        _opt_config, note="rule の中の入れ子の config。中の行を道すじ付きで同じ表で読む。" + _RM_OPTION_NOTE,
    ),
    Pattern(
        f"{_RM_ID}option_edit", _RM_OPTION_SCOPE, r"edit (?P<key>.*)", "(道すじの段)", "edit 1", _opt_edit,
        note="rule の中の edit(入れ子の config の中など)。キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)",
    ),
)
_ROUTE_LIST_PATTERNS: tuple[Pattern, ...] = (*(p for spec in _PL_SPECS for p in _pl_patterns(spec)), *_RM_PATTERNS)


# --- 全体(最上位の config system global の hostname・config system dns・config system ntp。REQ-196、ADR-0139 決定2) ---

_GLOBAL_ID = "global.fortinet."
# 汎用の形の見出しのまま読み、直下の行だけ先に hostname の表で試すブロックの名前
_SYSTEM_GLOBAL = "system global"
_SYSTEM_GLOBAL_SCOPE = "fgt_system_global"
_HOSTNAME_FIELDS = frozenset({"hostname"})
_HOSTNAME_LINE = re.compile(r"set\s+hostname(?:\s+(?P<value>.*))?")
_DNS_HEADER = "config system dns"
_NTP_HEADER = "config system ntp"
_DNS_BLOCK = f"{_GLOBAL_ID}dns_block"
_NTP_BLOCK = f"{_GLOBAL_ID}ntp_block"
_DNS_SCOPE = "fgt_dns"
_DNS_OPTION_SCOPE = "dns_option"
_NTP_OPTION_SCOPE = "ntp_option"
# DNS サーバーの欄 → 読むアドレスの型。要素は `<正規のアドレス> <欄>`(判断の記録の回答 2026-11-04)
_DNS_SERVER_FIELDS: dict[str, type[IPv4Address] | type[IPv6Address]] = {
    "primary": IPv4Address, "secondary": IPv4Address, "ip6-primary": IPv6Address, "ip6-secondary": IPv6Address,
}
# 値が ENC でない書き方でも値を持たない、NTP の鍵の欄(依頼書 §3 の ★)
_NTP_SECRET_FIELDS = ("key",)


def _ntp_is_secret(field: str, words: list[str]) -> bool:
    """NTP の ntp_options の秘密の判定。汎用の形の判定(_is_secret)に、欄の名前が key のものを足す(緩めない)。"""
    return _is_secret(field, words) or field in _NTP_SECRET_FIELDS


def _hostname_is_secret(text: str) -> bool:
    """`set hostname …` の行で、汎用の形と同じ判定で秘密になるか(値の最初の語が ENC)。"""
    m = _HOSTNAME_LINE.fullmatch(text)
    return m is not None and _is_secret("hostname", _VALUE_WORD.findall(m["value"] or ""))


def _hostname(m: re.Match[str], ctx: Global) -> bool:
    if _is_secret("hostname", [m["value"]]):  # 秘密の判定に当たる行は汎用の形の secrets に置く(値を持たない)
        return False
    if (value := _text(m["value"])) is None:  # `''` は読めない形(未解釈)
        return False
    ctx["hostname"] = value  # 同じ欄が2回あれば後の行の値
    return True


@dataclass
class _DnsCtx:
    """`config system dns` の直下の行を読む状態。servers は欄 → 要素(同じ欄は後の行の値)。"""

    servers: dict[str, str] = field(default_factory=dict)


def _dns_server(m: re.Match[str], ctx: _DnsCtx) -> bool:
    name = m["field"]
    try:
        address = _DNS_SERVER_FIELDS[name](unquote(m["value"]))
    except ValueError:  # 欄の版(IPv4・IPv6)のアドレスとして読めない
        return False
    ctx.servers[name] = f"{address} {name}"
    return True


_GLOBAL_OPTION_NOTE = (
    "入れ子は `<config の名前> > <edit のキー> > <行>` のように ` > ` でつなぐ(config の名前は引用符を外した語を空白1つで"
    "つなぐ)。子が1つも写らない config・edit は道すじだけを入れる。書き方は BGP の options と同じ"
)


def _global_option_patterns(
    name: str, scope: str, target: str, extra_secret: tuple[str, ...], secret: Callable[[str, list[str]], bool],
    examples: tuple[str, str, str, str, str], note: str,
) -> tuple[Pattern, ...]:
    """DNS・NTP の行を target(文字列の昇順の集合)に道すじで写す表。examples は (秘密の欄, set, unset, config, edit) の例。"""
    prefix = f"{_GLOBAL_ID}{name}_option_"
    names = "|".join(map(re.escape, (*SECRET_FIELDS, *extra_secret)))
    extra = f"、名前が {'・'.join(extra_secret)}" if extra_secret else ""
    secret_note = (
        "秘密の欄(値の最初の語が ENC、名前が汎用の形の秘密の一覧に完全一致するか `hostkey-` で始まる"
        f"{extra})は、どの段でも値を持たず `<道すじ> <欄>`(語の名前だけ)を入れる。秘密の決まりは緩めない"
    )
    secret_example, set_example, unset_example, config_example, edit_example = examples
    return (
        Pattern(
            f"{prefix}secret_enc", scope, r"""set\s+(?P<field>\S+)\s+(?P<q>["']?)ENC(?P=q)(?:\s.*)?""", target,
            "set password ENC EXAMPLEONLY", _opt_secret, note=secret_note + "。値の最初の語は引用符を外して比べる",
        ),
        Pattern(
            f"{prefix}secret", scope, rf"set\s+(?P<field>{names}|{re.escape(_SECRET_PREFIX)}\S+)(?:\s.*)?", target,
            secret_example, _opt_secret, note=secret_note,
        ),
        Pattern(
            f"{prefix}set", scope, r"set\s+(?P<field>\S+)(?:\s+(?P<value>.*))?", target, set_example, _opt_set(secret),
            note="`<道すじ> <欄> <値…>`。値は引用符を外した語を空白1つでつなぐ。値が無いか `''` なら `<欄>` だけ。"
            "秘密の欄(上の行と同じ判定)は `<欄>` だけ。" + note + _GLOBAL_OPTION_NOTE + "。" + _GEN_SPACE_NOTE,
        ),
        Pattern(
            f"{prefix}unset", scope, r"unset\s+(?P<field>\S+)", target, unset_example, _opt_unset,
            note="`<道すじ> unset <欄>`。1語だけ。`unset a b` などは未解釈。" + note,
        ),
        Pattern(
            f"{prefix}config", scope, r"config\s+(?P<name>.+)", "(道すじの段)", config_example, _opt_config,
            note="入れ子の config。中の行を道すじ付きで同じ表で読む。" + _GLOBAL_OPTION_NOTE,
        ),
        Pattern(
            f"{prefix}edit", scope, r"edit (?P<key>.*)", "(道すじの段)", edit_example, _opt_edit,
            note="入れ子の config の中の edit。キーは木のテキストの `edit <キー>` のキー(引用符を外した原文)。"
            "ブロックの直下の edit は未解釈",
        ),
    )


_GLOBAL_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        f"{_GLOBAL_ID}hostname", _SYSTEM_GLOBAL_SCOPE, rf"set hostname (?P<value>{_VALUE})", "global.hostname",
        'set hostname "FGT-EXAMPLE-01"', _hostname,
        note="最上位の `config system global`(見出しは汎用の形のまま)の直下の行だけを先にこの表で試す。値は引用符を外した"
        "1語(Cisco の hostname と同じ項目)。同じ欄が2回あれば後の行の値。汎用の形の `system global` の set・unset には"
        "入れない。読めない形(`''`・値の無い行・2語以上・`unset hostname`)は未解釈。値の最初の語が ENC の行は、汎用の形と"
        "同じく値を持たず secrets に置く。ほかの行は今のまま汎用の形。`config vdom`・`config global` の中は今のまま汎用の形",
    ),
    Pattern(
        _DNS_BLOCK, "top", _DNS_HEADER, "global.dns", _DNS_HEADER, _block,
        note="最上位の `config system dns` だけを読む(VDOM・`config global` の中は今のまま汎用の形)。"
        "vendor_specific.fortinet.config には入れない。サーバーの4つの欄は global.dns.servers、ほかの行は "
        "global.dns.options。中に行が無いブロックは見出しだけを解釈済みにし、何も足さない。直下の edit は未解釈",
    ),
    Pattern(
        f"{_GLOBAL_ID}dns_server", _DNS_SCOPE,
        rf"set (?P<field>{'|'.join(map(re.escape, _DNS_SERVER_FIELDS))}) (?P<value>{_VALUE})",
        "global.dns.servers[]", "set primary 192.0.2.53", _dns_server,
        note="`<正規のアドレス> <欄>`(例 `192.0.2.53 primary`・`2001:db8::53 ip6-secondary`。判断の記録の回答 2026-11-04)の"
        "文字列の昇順の集合。primary・secondary は IPv4、ip6-primary・ip6-secondary は IPv6 として読めるときだけ読む"
        "(`0.0.0.0`・`::` も原文のとおりアドレスとして読む。既定値を推測しない)。同じ欄は後の行の値(重なったブロックも同じ)。"
        "読めない形(`set primary abc`・`unset primary` など)は options に入れず未解釈",
    ),
    *_global_option_patterns(
        "dns", _DNS_OPTION_SCOPE, "global.dns.options[]", (), _is_secret,
        ("set password EXAMPLEONLY", "set domain example.com", "unset domain", "config server-hostname", "edit 1"),
        "サーバーの4つの欄の、表に当たらない形は未解釈。",
    ),
    Pattern(
        _NTP_BLOCK, "top", _NTP_HEADER, "global.ntp_options", _NTP_HEADER, _block,
        note="最上位の `config system ntp` だけを読む(VDOM・`config global` の中は今のまま汎用の形)。"
        "vendor_specific.fortinet.config には入れない。中の行はすべて global.ntp_options に道すじで入れる"
        "(例 `ntpserver > 1 > server 192.0.2.1`)。中に行が無いブロックは見出しだけを解釈済みにし、何も足さない。"
        "直下の edit は未解釈",
    ),
    *_global_option_patterns(
        "ntp", _NTP_OPTION_SCOPE, "global.ntp_options[]", _NTP_SECRET_FIELDS, _ntp_is_secret,
        ("set key EXAMPLEONLY", "set ntpsync enable", "unset source-ip", "config ntpserver", "edit 1"),
        "",
    ),
)


_VS = "vendor_specific.fortinet.interfaces[name]"
_ROUTE_NOTE = (
    "表が持たない欄の set・unset <1語> は、汎用の形で経路の詳細の other に写す(写す行があるときだけキーを置く。ADR-0119 決定3)。"
    "入れ子の config、表が持つ欄(dst・gateway・device・distance・status)の読めない形、set でも unset <1語> でもない行が"
    "1行でもある edit は、まるごと写さない(行はすべて未解釈)。"
    "gateway と device がどちらも無い edit は、other に `set blackhole enable`・`set virtual-wan-link enable`・"
    "値のある `set sdwan-zone` のどれかがあるときだけ写す(next_hop・interface は None。ADR-0129 決定2)。"
    "同じキーの edit が複数あり、1つでも写さないものがあれば、そのキーの edit はどれも写さない"
)
_ROUTE6_NOTE = (
    "`config router static` と同じ手順で読み、経路の詳細に `\"static6\": true` を置く(ADR-0129 決定3)。"
    "IPv6 として読めない dst・gateway があれば edit を写さない"
)
_HEADER_ID = "vendor_specific.fgt_header."
_HEADER_NOTE = (
    "最上位の、子を持たない行だけ。値は `=` の後ろの原文(空なら未解釈)。同じ行が2回あれば後の行の値。"
    "`#conf_file_ver=<数字>` の行と `#config-version=` の行の最後の `:user=<…>` は、木を組む前に消える"
    "(保存のたびに変わるため)。ほかの `#` の行(`#private-encryption-key=` など)は未解釈(ADR-0129 決定1)"
)

# 並び＝優先順位。scope は top(最上位のブロックの見出し)/ system_interface(その直下の edit)/ interface(edit の中)/
# router_static(config router static の直下の edit)/ static_route(その edit の中)/
# router_static6(config router static6 の直下の edit)/ static_route6(その edit の中)/
# fgt_header(最上位の、子を持たない行)/
# router_bgp(config router bgp の直下)/ bgp_neighbor_edit・bgp_neighbor_group_edit(config neighbor・
# config neighbor-group の直下の edit)/ bgp_neighbor(相手の edit の中)/ bgp_option(表に無い行を options に写す)/
# router_ospf(config router ospf の直下)/ ospf_network_edit(config network の直下の edit)/
# ospf_network(その edit の中)/ ospf_option(OSPF の表に無い行を options に写す)/
# ipsec_edit(config vpn ipsec phase1 などの直下の edit)/ ipsec_option(その edit の中の行を settings に写す)/
# sdwan_block(config system sdwan・config system virtual-wan-link の直下の config)/ sdwan_table(その config の直下の
# edit)/ sdwan_option(SD-WAN の行を settings に写す。どの段でも同じ表)/
# prefix_list_edit・prefix_list6_edit・route_map_edit(リスト)/ prefix_list・prefix_list6・route_map(リストの中)/
# <頭>_rule_edit(config rule の直下の edit)/ <頭>_rule(rule の edit の中)/ route_map_option(rule の中の表に無い行)/
# fgt_system_global(最上位の config system global の直下で先に試す。hostname)/ fgt_dns(config system dns の直下)/
# dns_option(DNS の表に無い行を global.dns.options に写す)/ ntp_option(config system ntp の行を global.ntp_options に写す)/
# fw_<名前>_edit(ファイアウォールのブロックの直下の edit)/ fw_<名前>(その edit の中)/
# fgt_generic(汎用の形の段の中)/ fgt_generic_snmp(SNMP のコミュニティの edit の中で先に試す)。
# ファイアウォールの行は専用の見出しの後ろ、汎用の形の行はさらに後ろ(専用のブロックを汎用の形で読まないように)
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "interfaces.fgt_block", "top", r"config system interface", "(ブロックの見出し)", "config system interface",
        _block, note="最上位の `config system interface` だけを写す。ほかのブロックは読まない(未解釈)",
    ),
    Pattern(
        "interfaces.fgt_edit", "system_interface", r"edit (?P<name>.+)", "interfaces[name]", "edit port1", _open,
        note="木のテキストは `edit <キー>`(引用符を外した原文)。名前の `.` から親を推定しない。"
        "type は set type の行が無ければ routed。表に当たらない行(入れ子の config を含む)は汎用の形で "
        f"{_VS}.other に写す(無ければ None)。表が持つ欄({'・'.join(sorted(_INTERFACE_FIELDS))})の"
        "表に当たらない形は未解釈(ADR-0119 決定1・2)",
    ),
    Pattern(
        "interfaces.fgt_ip", "interface", rf"set ip (?P<addr>{_IPV4}) (?P<mask>{_IPV4})", "ip_address",
        "set ip 192.0.2.1 255.255.255.0", _ip,
        note="`0.0.0.0 0.0.0.0` はアドレスなし(None)。マスクとして読めないものは未解釈",
    ),
    Pattern(
        "interfaces.fgt_description", "interface", rf"set description (?P<value>{_VALUE})", "description",
        'set description "uplink"', _description, note="引用符を外す。`\'\'` は None",
    ),
    Pattern(
        "interfaces.fgt_status", "interface", r"set status (?P<state>up|down)", "shutdown", "set status down", _status,
        note="down → shutdown=True、up → False。行が無ければ False",
    ),
    Pattern(
        "interfaces.fgt_interface", "interface", rf"set interface (?P<value>{_VALUE})", "parent_interface",
        'set interface "internal1"', _parent, note="VLAN などの親。親はこの行だけが決める",
    ),
    Pattern(
        "interfaces.fgt_type", "interface", rf"set type (?P<value>{_VALUE})", f"type / {_VS}.type",
        "set type vlan", _type,
        note="loopback → loopback、aggregate → port_channel、それ以外(physical / hard-switch / tunnel / vlan など)→ routed。"
        "元の値は vendor_specific に置く",
    ),
    Pattern("interfaces.fgt_vdom", "interface", rf"set vdom (?P<value>{_VALUE})", f"{_VS}.vdom", 'set vdom "root"',
            _vendor_text("vdom")),
    Pattern("interfaces.fgt_mode", "interface", rf"set mode (?P<value>{_VALUE})", f"{_VS}.mode", "set mode static",
            _vendor_text("mode")),
    Pattern(
        "interfaces.fgt_allowaccess", "interface", rf"set allowaccess (?P<value>(?:{_VALUE})(?: (?:{_VALUE}))*)",
        f"{_VS}.allowaccess", "set allowaccess ping https ssh", _allowaccess, note="語のリスト(行の順のまま)",
    ),
    Pattern("interfaces.fgt_role", "interface", rf"set role (?P<value>{_VALUE})", f"{_VS}.role", "set role lan",
            _vendor_text("role")),
    Pattern("interfaces.fgt_alias", "interface", rf"set alias (?P<value>{_VALUE})", f"{_VS}.alias",
            'set alias "office"', _vendor_text("alias"), note="`''` は None"),
    Pattern(
        "interfaces.fgt_vrf", "interface", r"set vrf (?P<value>\d+)", f"{_VS}.vrf", "set vrf 0", _vendor_int("vrf"),
        note="FortiOS の vrf は番号で、Cisco の VRF 名と意味が違うため共通キーの vrf には写さない",
    ),
    Pattern("interfaces.fgt_speed", "interface", rf"set speed (?P<value>{_VALUE})", f"{_VS}.speed", "set speed auto",
            _vendor_text("speed"), note="`1000full` のように duplex と一体のため、共通キーの speed / duplex には写さない"),
    Pattern("interfaces.fgt_vlanid", "interface", r"set vlanid (?P<value>\d+)", f"{_VS}.vlanid", "set vlanid 100",
            _vendor_int("vlanid")),
    Pattern(
        "interfaces.fgt_unset", "interface", rf"unset (?P<attr>{'|'.join(UNSETTABLE)})",
        f"{_VS}.<attr> = {{\"unset\": true}}", "unset allowaccess", _unset,
        note="明示的に未設定(行が無い None と区別する)。共通キーに写す属性の unset は未解釈のまま",
    ),
    Pattern(
        "static_routes.fortinet.block", "top", r"config router static", "(ブロックの見出し)", "config router static",
        _block, note="最上位の `config router static` だけを写す。`config router static6` は下の static6_* の行で読む",
    ),
    Pattern(
        "static_routes.fortinet.edit", "router_static", r"edit (?P<key>.+)",
        "static_routes[] / vendor_specific.fortinet.static_routes[].edit", "edit 1", _route_open,
        note="edit のキーは原文の文字列のまま vendor_specific に置く(突き合わせには使わない)。" + _ROUTE_NOTE
        + "。set dst の行が無ければ 0.0.0.0/0",
    ),
    Pattern(
        "static_routes.fortinet.dst", "static_route", rf"set dst (?P<addr>{_IPV4}) (?P<mask>{_IPV4})", "destination",
        "set dst 192.0.2.0 255.255.255.0", _route_dst,
        note="ホスト部が立っていても読む(strict=False)。マスクとして読めないものがあれば edit を写さない",
    ),
    Pattern(
        "static_routes.fortinet.gateway", "static_route", rf"set gateway (?P<addr>{_IPV4})", "next_hop",
        "set gateway 198.51.100.1", _route_gateway, note="IPv4 として読めなければ edit を写さない",
    ),
    Pattern(
        "static_routes.fortinet.device", "static_route", rf"set device (?P<value>{_VALUE})", "interface",
        'set device "port1"', _route_device, note="引用符を外す。`\'\'` は None",
    ),
    Pattern(
        "static_routes.fortinet.distance", "static_route", r"set distance (?P<value>\d+)", "distance",
        "set distance 10", _route_distance,
        note="数(int)。数でないものがあれば edit を写さない。行が無ければ None(既定値を推測しない。ADR-0119 決定3)",
    ),
    Pattern(
        "static_routes.fortinet.status", "static_route", r"set status (?P<state>enable|disable)", "enabled",
        "set status disable", _route_status,
        note="enable → True、disable → False。行が無ければ None。ほかの値があれば edit を写さない",
    ),
    Pattern(
        "static_routes.fortinet.static6_block", "top", r"config router static6", "(ブロックの見出し)",
        "config router static6", _block,
        note="最上位の `config router static6` を static_routes に写す(汎用の形の config には写さない。ADR-0129 決定3)",
    ),
    Pattern(
        "static_routes.fortinet.static6_edit", "router_static6", r"edit (?P<key>.+)",
        "static_routes[] / vendor_specific.fortinet.static_routes[].edit・static6", "edit 1", _route_open,
        note="edit のキーは原文の文字列のまま vendor_specific に置く(突き合わせには使わない)。" + _ROUTE6_NOTE + "。"
        + _ROUTE_NOTE + "。set dst の行が無ければ ::/0",
    ),
    Pattern(
        "static_routes.fortinet.static6_dst", "static_route6", r"set dst (?P<prefix>[0-9A-Fa-f:.]+/\d{1,3})",
        "destination", "set dst 2001:db8:1::/48", _route6_dst,
        note="ホスト部が立っていても読む(strict=False)。IPv6 として読めないものがあれば edit を写さない",
    ),
    Pattern(
        "static_routes.fortinet.static6_gateway", "static_route6", r"set gateway (?P<addr>[0-9A-Fa-f:.]+)", "next_hop",
        "set gateway 2001:db8::fffe", _route6_gateway,
        note="IPv6 として読めなければ edit を写さない。`::` も原文のとおりアドレスとして読む(既定値を推測しない)",
    ),
    Pattern(
        "static_routes.fortinet.static6_device", "static_route6", rf"set device (?P<value>{_VALUE})", "interface",
        'set device "port1"', _route_device, note="引用符を外す。`\'\'` は None",
    ),
    Pattern(
        "static_routes.fortinet.static6_distance", "static_route6", r"set distance (?P<value>\d+)", "distance",
        "set distance 10", _route_distance,
        note="数(int)。数でないものがあれば edit を写さない。行が無ければ None(既定値を推測しない。ADR-0119 決定3)",
    ),
    Pattern(
        "static_routes.fortinet.static6_status", "static_route6", r"set status (?P<state>enable|disable)", "enabled",
        "set status disable", _route_status,
        note="enable → True、disable → False。行が無ければ None。ほかの値があれば edit を写さない",
    ),
    Pattern(
        f"{_HEADER_ID}config_version", "fgt_header", r"#config-version=(?P<value>.+)",
        "vendor_specific.fortinet.header.config_version", "#config-version=FGT60F-7.2.5-FW-build1517-230606:opmode=0:vdom=0",
        _header("config_version"), note=_HEADER_NOTE,
    ),
    Pattern(
        f"{_HEADER_ID}buildno", "fgt_header", r"#buildno=(?P<value>.+)", "vendor_specific.fortinet.header.buildno",
        "#buildno=1517", _header("buildno"), note=_HEADER_NOTE,
    ),
    Pattern(
        f"{_HEADER_ID}global_vdom", "fgt_header", r"#global_vdom=(?P<value>.+)",
        "vendor_specific.fortinet.header.global_vdom", "#global_vdom=1", _header("global_vdom"), note=_HEADER_NOTE,
    ),
    *_BGP_PATTERNS,
    *_OSPF_PATTERNS,
    *_IPSEC_PATTERNS,
    *_SDWAN_PATTERNS,
    *_ROUTE_LIST_PATTERNS,
    *_GLOBAL_PATTERNS,
    *_FW_PATTERNS,
    *_GEN_PATTERNS,
)
# 写すかどうかを前もって決める見出しのパターン ID。`set as` が読めないときは BGP の見出しを、`config router ospf` が
# どれも空のときは OSPF の見出しを、SD-WAN の見出しがどれも空のときはその見出しを、prefix-list・prefix-list6・route-map で
# 写すリストが1つも無いときはその見出しを抜く(抜いた見出しのブロックは汎用の形の見出しに当たる)
_OPTIONAL_HEADERS = (_BGP_BLOCK, _OSPF_BLOCK, _SDWAN_BLOCK, _SDWAN_VWL_BLOCK, _PL_BLOCK, _PL6_BLOCK, _RM_BLOCK)
# 最上位の見出しの表。鍵は抜く見出しのパターン ID の集合(_OPTIONAL_HEADERS のすべての部分集合を前もって作る)。
# apply_first は apply が False を返すと後ろの行に進まないため、見出しの関数の中では決めずに表を分ける
_TOP_PATTERNS: dict[frozenset[str], tuple[Pattern, ...]] = {
    frozenset(dropped): tuple(p for p in PATTERNS if p.id not in dropped)
    for size in range(len(_OPTIONAL_HEADERS) + 1)
    for dropped in combinations(_OPTIONAL_HEADERS, size)
}


def _system_interface(
    block: FlatNode, interpreted: InterpretedLines, interfaces: dict[str, Interface], vendor: dict[str, Any]
) -> None:
    for item in block.children:
        if item.scope_id != EDIT:
            continue
        ctx = _Ctx()
        if (opened := apply_first(PATTERNS, "system_interface", item.text, ctx)) is None:
            continue
        interpreted.mark(item, opened.id)
        other = _edit_stage()
        wrote = False
        for line in item.children:
            # 入れ子の config(ipv6 / secondaryip)は表に通さず、汎用の形で other に写す
            pattern = apply_first(PATTERNS, "interface", line.text, ctx) if line.scope_id is None else None
            if pattern is not None:
                interpreted.mark(line, pattern.id)
            elif _generic_child(line, other, interpreted, reserved=_INTERFACE_FIELDS):
                wrote = True
        ctx.vendor["other"] = other if wrote else None
        interfaces[ctx.name] = ctx.interface
        vendor[ctx.name] = ctx.vendor


def _route_edit(
    item: FlatNode, static6: bool
) -> tuple[_RouteCtx, dict[str, Any] | None, list[tuple[FlatNode, str]]] | None:
    """edit 1つを判定する。中の行がすべて表か汎用の形(表が持たない欄)に当たり、行き先があるときだけ
    (状態, 経路の詳細の other〔写す行が無ければ None〕, 当たった行) を返す。static6 は `config router static6` の edit。"""
    ctx = _RouteCtx(static6=static6)
    edit_scope, line_scope = ("router_static6", "static_route6") if static6 else ("router_static", "static_route")
    if (opened := apply_first(PATTERNS, edit_scope, item.text, ctx)) is None:
        return None
    hits = [(item, opened.id)]
    other = _edit_stage()
    wrote = False
    for line in item.children:
        if line.scope_id is not None:  # 入れ子の config があれば、この edit はまるごと写さない
            return None
        if (pattern := apply_first(PATTERNS, line_scope, line.text, ctx)) is None:
            found = _generic_apply(line, other, reserved=_ROUTE_FIELDS)
            # 表が持つ欄の読めない形・set でも unset <1語> でもない行があれば、この edit はまるごと写さない
            if found is None or found[1].opened is not None:
                return None
            pattern, wrote = found[0], True
        hits.append((line, pattern.id))
    other = other if wrote else None
    # 行き先の無い経路は作らない。blackhole・SD-WAN の経路だけは写す(ADR-0129 決定2)
    if ctx.next_hop is None and ctx.interface is None and not _has_no_gateway_target(other):
        return None
    return ctx, other, hits


def _router_static(
    blocks: list[tuple[FlatNode, bool]], interpreted: InterpretedLines, routes: list[StaticRoute],
    details: list[dict[str, Any]],
) -> None:
    """edit ごとに、中の行がすべて表か汎用の形に当たったときだけ経路1本にする。すべて判定してから印を付ける。

    blocks は (ブロック, `config router static6` か) の config の出現の順。
    解釈済みの記録は行をパスで識別するため、同じキーの edit(同じブロックの中、または重なった `config router static`)は
    同じパスになる。そのうち1つでも写さない edit があれば、同じパスの edit はどれも写さない
    (写した側の印で、写さなかった edit の行が解釈済みに見えないように)。
    """
    judged: list[
        tuple[tuple[str, ...], tuple[_RouteCtx, dict[str, Any] | None, list[tuple[FlatNode, str]]] | None]
    ] = [
        (tuple(item.path()), _route_edit(item, static6))
        for block, static6 in blocks
        for item in block.children
        if item.scope_id == EDIT
    ]
    rejected = {path for path, result in judged if result is None}
    for path, result in judged:
        if result is None or path in rejected:
            continue
        ctx, other, hits = result
        for node, pattern_id in hits:
            interpreted.mark(node, pattern_id)
        routes.append({
            "destination": ctx.destination, "next_hop": ctx.next_hop, "interface": ctx.interface,
            "vrf": None, "distance": ctx.distance, "enabled": ctx.enabled,
        })
        detail: dict[str, Any] = {"edit": ctx.key} if other is None else {"edit": ctx.key, "other": other}
        if ctx.static6:  # IPv4 の経路にはキーを置かない
            detail["static6"] = True
        details.append(detail)


def _firewall(block: FlatNode, spec: _FwBlock, interpreted: InterpretedLines, firewall: dict[str, Any]) -> None:
    """直下の edit ごとに、表に当たった行を写す。表に無い行と入れ子の config は汎用の形で other に写す(ADR-0119 決定1)。"""
    entries: dict[str, dict[str, Any]] = firewall[spec.key]
    reserved = _fw_reserved(spec)
    for item in block.children:
        if item.scope_id != EDIT:
            continue
        ctx = _FwCtx()
        if (opened := apply_first(PATTERNS, f"fw_{spec.name}_edit", item.text, ctx)) is None:
            continue
        interpreted.mark(item, opened.id)
        other = _edit_stage()
        wrote = False
        for line in item.children:
            # 入れ子の config は表に通さず、汎用の形で other に写す
            pattern = apply_first(PATTERNS, f"fw_{spec.name}", line.text, ctx) if line.scope_id is None else None
            if pattern is not None:
                interpreted.mark(line, pattern.id)
            elif _generic_child(line, other, interpreted, reserved=reserved):
                wrote = True
        if wrote:  # 写す行が無ければキーを置かない(持つキーは行があった項目だけ)
            ctx.values["other"] = other
        entries[ctx.key] = ctx.values  # 同じキーの edit は後のもので上書きする
        if spec.order_key is not None:  # ポリシー・セントラル NAT は上から当たる表(出現の順)
            firewall[spec.order_key].append(ctx.key)


def _generic_apply(
    line: FlatNode, stage: dict[str, Any], *, snmp: bool = False, reserved: frozenset[str] = frozenset()
) -> tuple[Pattern, _GenCtx] | None:
    """1行を汎用の形の表で判定して stage に写す。当たれば (パターン, 状態)。印は付けない。

    snmp は「stage が SNMP のコミュニティの edit の段」。reserved は写さない欄の名前(専用のブロックの表が持つ欄)。
    """
    ctx = _GenCtx(stage, reserved=reserved)
    pattern = apply_first(PATTERNS, _GEN_SNMP_SCOPE, line.text, ctx) if snmp else None
    if pattern is None:
        pattern = apply_first(PATTERNS, _GEN_SCOPE, line.text, ctx)
    return None if pattern is None else (pattern, ctx)


def _generic_child(
    line: FlatNode, stage: dict[str, Any], interpreted: InterpretedLines, *, community: bool = False,
    snmp: bool = False, reserved: frozenset[str] = frozenset(),
) -> bool:
    """1行を汎用の形で stage に写して印を付け、開いた段(config・edit)の中も読む。当たらなければ False(未解釈)。

    community は「line の親が SNMP のコミュニティの config」(line の edit が SNMP の段)。入れ子の段の中では reserved は使わない。
    """
    if (found := _generic_apply(line, stage, snmp=snmp, reserved=reserved)) is None:
        return False
    pattern, ctx = found
    interpreted.mark(line, pattern.id)
    if ctx.opened is not None:
        if line.scope_id == EDIT:
            _generic(line, ctx.opened, interpreted, snmp=community)
        else:
            _generic(line, ctx.opened, interpreted, community=ctx.name in SNMP_COMMUNITY_BLOCKS)
    return True


def _generic(
    node: FlatNode, stage: dict[str, Any], interpreted: InterpretedLines, *, community: bool = False, snmp: bool = False
) -> None:
    """node(config か edit の行)の子を汎用の形で stage に写す。表に当たらない行は未解釈に残し、その中も読まない。

    community は「node が SNMP のコミュニティの config で、直下の edit が SNMP の段」、snmp は「node がその edit」。
    """
    for line in node.children:
        _generic_child(line, stage, interpreted, community=community, snmp=snmp)


def _option_line(
    line: FlatNode, path: tuple[str, ...], options: list[str], interpreted: InterpretedLines,
    reserved: frozenset[str] = frozenset(), scope: str = _BGP_OPTION_SCOPE,
) -> bool:
    """1行を scope の表(bgp_option・ospf_option)で判定し、道すじ path を付けた文字列を options に足して印を付ける。
    当たらなければ False(未解釈)。

    config・edit の行は中の行を同じ表で読む(中では reserved を使わない)。中から1つも写らなければ、道すじだけを足す
    (子の無い段も行として残し、消したときに差分が出るように)。
    """
    return _option_walk(line, path, options, interpreted, reserved, scope) is not None


@dataclass
class _Seen:
    """_option_walk で秘密の行(文字列を足さない行)に当たったか。IPsec の keys_configured に使う。"""

    secret: bool = False


def _option_walk(
    line: FlatNode, path: tuple[str, ...], options: list[str], interpreted: InterpretedLines,
    reserved: frozenset[str] = frozenset(), scope: str = _BGP_OPTION_SCOPE, seen: _Seen | None = None,
) -> bool | None:
    """_option_line の本体。None は当たらない(未解釈)、False は解釈したが文字列を足さない(IPsec の秘密の行)、
    True は文字列を足した。

    「中から1つも写らない」は文字列を足した子が無いこと(秘密の行しか持たない段も道すじを残し、段を消したときに差分が
    出るように)。BGP・OSPF では解釈した行はいつも文字列を足すので、動きは変わらない。
    """
    ctx = _OptCtx(reserved)
    if (pattern := apply_first(PATTERNS, scope, line.text, ctx)) is None:
        return None
    interpreted.mark(line, pattern.id)
    if ctx.secret:
        if seen is not None:
            seen.secret = True
        return False
    inner = (*path, ctx.text)
    if not ctx.opened or not any(
        [_option_walk(child, inner, options, interpreted, scope=scope, seen=seen) for child in line.children]
    ):
        _add_sorted(options, " > ".join(inner))
    return True


def _bgp_option_line(
    line: FlatNode, path: tuple[str, ...], options: list[str], interpreted: InterpretedLines,
    reserved: frozenset[str] = frozenset(),
) -> bool:
    return _option_line(line, path, options, interpreted, reserved, _BGP_OPTION_SCOPE)


def _ospf_option_line(
    line: FlatNode, path: tuple[str, ...], options: list[str], interpreted: InterpretedLines,
    reserved: frozenset[str] = frozenset(),
) -> bool:
    return _option_line(line, path, options, interpreted, reserved, _OSPF_OPTION_SCOPE)


def _bgp_neighbors(section: FlatNode, edit_scope: str, bgp: Bgp, interpreted: InterpretedLines) -> bool:
    """`config neighbor` / `config neighbor-group` の直下の edit を相手にする。読めた相手があれば True。

    edit でない行と、キーが読めない edit は未解釈(中も読まない)。
    """
    made = False
    for item in section.children:
        ctx = _BgpCtx(bgp)
        if item.scope_id != EDIT or (opened := apply_first(PATTERNS, edit_scope, item.text, ctx)) is None:
            continue
        interpreted.mark(item, opened.id)
        made = True
        for line in item.children:
            pattern = apply_first(PATTERNS, "bgp_neighbor", line.text, ctx) if line.scope_id is None else None
            if pattern is not None:
                interpreted.mark(line, pattern.id)
            else:
                _bgp_option_line(line, (), ctx.neighbor["options"], interpreted, _BGP_NEIGHBOR_FIELDS)
    return made


def _router_bgp(blocks: list[FlatNode], local_as: int, interpreted: InterpretedLines) -> Bgp:
    """最上位の `config router bgp`(出現の順)を1つの bgp に写す。

    印の判定は行の文字列と段だけで決まるので、重なったブロックの同じ道すじの行で印が食い違うことはない。
    """
    bgp = new_bgp(local_as)
    sections = {_BGP_NEIGHBOR_BLOCK: "bgp_neighbor_edit", _BGP_GROUP_BLOCK: "bgp_neighbor_group_edit"}
    for block in blocks:
        ctx = _BgpCtx(bgp)
        for line in block.children:
            if line.scope_id == EDIT:  # config router bgp の直下の edit は未解釈
                continue
            if (pattern := apply_first(PATTERNS, "router_bgp", line.text, ctx)) is None:
                _bgp_option_line(line, (), bgp["options"], interpreted, _BGP_FIELDS)
                continue
            interpreted.mark(line, pattern.id)
            if pattern.id in sections and not _bgp_neighbors(line, sections[pattern.id], bgp, interpreted):
                _add_sorted(bgp["options"], line.text.removeprefix("config "))  # 相手の無い段も行として残す
    return bgp


_OspfNetworkResult = tuple[OspfNetwork, list[tuple[FlatNode, str]], str, list[FlatNode]]


def _ospf_network_edit(item: FlatNode) -> _OspfNetworkResult | None:
    """`config network` の edit 1つを判定する。印は付けない。

    直下に読める `set prefix` があり、prefix・area の欄の行(set・unset とも)がすべて読めるときだけ
    (networks の1件, 当たった行, edit の番号, ほかの行) を返す。そうでなければ None(edit ごと道すじにする)。
    """
    ctx = _OspfNetCtx()
    if (opened := apply_first(PATTERNS, "ospf_network_edit", item.text, ctx)) is None:
        return None
    hits = [(item, opened.id)]
    others: list[FlatNode] = []
    for line in item.children:
        if line.scope_id is None and _OSPF_NETWORK_FIELD.fullmatch(line.text):
            if (pattern := apply_first(PATTERNS, "ospf_network", line.text, ctx)) is None:
                return None  # prefix・area の読めない形(unset を含む)
            hits.append((line, pattern.id))
        else:
            others.append(line)
    if ctx.address is None or ctx.wildcard is None:
        return None
    network: OspfNetwork = {
        "address": ctx.address,
        "wildcard": ctx.wildcard,
        "area": ctx.area if ctx.area is not None else _DEFAULT_AREA,  # 行が無ければ 0.0.0.0(依頼書 §2)
    }
    return network, hits, ctx.key, others


def _ospf_networks(sections: list[FlatNode], ospf: Ospf, interpreted: InterpretedLines) -> None:
    """`config network`(出現の順)の中を読む。すべての edit を判定してから写す。

    解釈済みの記録は行をパスで識別するため、同じ番号の edit(同じ段の中、または重なったブロック)は同じパスになる。
    そのうち1つでも道すじにする edit があれば、同じパスの edit はどれも道すじにする
    (networks に写した側の印で、道すじにした edit の行の印が食い違わないように。経路の _router_static と同じ考え方)。
    """
    judged: dict[int, _OspfNetworkResult | None] = {}
    rejected: set[tuple[str, ...]] = set()
    for section in sections:
        for item in section.children:
            if item.scope_id != EDIT:
                continue
            judged[id(item)] = result = _ospf_network_edit(item)
            if result is None:
                rejected.add(tuple(item.path()))
    for section in sections:
        wrote = False
        for item in section.children:
            result = judged.get(id(item))
            if result is None or tuple(item.path()) in rejected:
                # edit でない行と、networks に入れない edit は、道すじ(`network > …`)で options に残す
                wrote = _ospf_option_line(item, ("network",), ospf["options"], interpreted) or wrote
                continue
            network, hits, key, others = result
            for node, pattern_id in hits:
                interpreted.mark(node, pattern_id)
            ospf["networks"].append(network)
            wrote = True
            for line in others:  # edit の中のほかの行は `network > <番号> > <行>`
                _ospf_option_line(line, ("network", key), ospf["options"], interpreted)
        if not wrote:  # 何も写らない段も行として残す(消したときに差分が出るように)
            _add_sorted(ospf["options"], "network")


def _router_ospf(blocks: list[FlatNode], interpreted: InterpretedLines) -> Ospf:
    """最上位の `config router ospf`(出現の順)を1つの ospf に写す(鍵 "0"、process_id 0)。

    直下の行の印の判定は行の文字列だけで決まるので、重なったブロックの同じ道すじの行で印が食い違うことはない。
    """
    ospf = new_ospf(0)
    sections: list[FlatNode] = []
    ctx = _OspfCtx(ospf)
    for block in blocks:
        for line in block.children:
            if line.scope_id == EDIT:  # config router ospf の直下の edit は未解釈
                continue
            if (pattern := apply_first(PATTERNS, "router_ospf", line.text, ctx)) is None:
                _ospf_option_line(line, (), ospf["options"], interpreted, _OSPF_FIELDS)
                continue
            interpreted.mark(line, pattern.id)
            if pattern.id == _OSPF_NETWORK_BLOCK:
                sections.append(line)
    _ospf_networks(sections, ospf, interpreted)
    return ospf


def _vpn_ipsec(blocks: list[FlatNode], interpreted: InterpretedLines) -> Ipsec | None:
    """最上位の `config vpn ipsec phase1|phase2|phase1-interface|phase2-interface`(出現の順)の直下の edit を
    ipsec.blocks に写す。写す edit が1つも無ければ None。

    直下の edit でない行は未解釈(中も読まない)。印の判定は行の文字列と段だけで決まるので、重なったブロックの同じ道すじの
    行で印が食い違うことはない。
    """
    ipsec: Ipsec | None = None
    for block in blocks:
        kind = block.text.removeprefix(_IPSEC_PREFIX)
        for item in block.children:
            ctx = _IpsecEditCtx()
            if item.scope_id != EDIT or (opened := apply_first(PATTERNS, _IPSEC_EDIT_SCOPE, item.text, ctx)) is None:
                continue
            interpreted.mark(item, opened.id)
            if ipsec is None:
                ipsec = new_ipsec()
            entry: IpsecBlock = ipsec["blocks"].setdefault(f"{kind} {ctx.key}", {"settings": [], "keys_configured": False})
            seen = _Seen()
            for line in item.children:
                _option_walk(line, (), entry["settings"], interpreted, scope=_IPSEC_OPTION_SCOPE, seen=seen)
            entry["keys_configured"] = entry["keys_configured"] or seen.secret
    return ipsec


def _system_sdwan(blocks: list[tuple[FlatNode, str]], interpreted: InterpretedLines) -> dict[str, SdwanBlock]:
    """最上位の `config system sdwan`・`config system virtual-wan-link`((ブロック, 鍵の頭の語) の出現の順)を sdwan に写す。

    見出しの直下の edit は未解釈(中も読まない)。表に当たらない行は _option_line が未解釈に残す。
    印の判定は行の文字列と段だけで決まるので、重なったブロックの同じ道すじの行で印が食い違うことはない。
    """
    sdwan: dict[str, SdwanBlock] = {}
    orders: dict[str, list[str]] = {}  # 鍵の頭の語 → `config service` の edit のキー(出てきた順。最初の出現だけ)

    def entry(key: str) -> list[str]:
        return sdwan.setdefault(key, {"settings": []})["settings"]

    for block, head in blocks:
        top = entry(head)  # 直下の行が無くても鍵を作る
        for line in block.children:
            if line.scope_id == EDIT:  # 見出しの直下の edit は未解釈
                continue
            table = _SdwanTableCtx()
            if line.scope_id != CONFIG or (opened := apply_first(PATTERNS, _SDWAN_BLOCK_SCOPE, line.text, table)) is None:
                _option_line(line, (), top, interpreted, scope=_SDWAN_OPTION_SCOPE)
                continue
            interpreted.mark(line, opened.id)
            prefix = f"{head} {table.name}"
            rows = entry(prefix)  # 表の直下の行が無くても鍵を作る
            for item in line.children:
                edit = _SdwanEditCtx()
                if item.scope_id != EDIT or (found := apply_first(PATTERNS, _SDWAN_TABLE_SCOPE, item.text, edit)) is None:
                    _option_line(item, (), rows, interpreted, scope=_SDWAN_OPTION_SCOPE)
                    continue
                interpreted.mark(item, found.id)
                settings = entry(f"{prefix} {edit.key}")  # 中身が空の edit も鍵を作る
                if table.name == _SDWAN_ORDERED_TABLE:
                    keys = orders.setdefault(head, [])
                    if edit.key not in keys:
                        keys.append(edit.key)
                for child in item.children:
                    _option_line(child, (), settings, interpreted, scope=_SDWAN_OPTION_SCOPE)
    for head, keys in orders.items():
        # 上から当たる表の順(元の行は無いので印は付けない。印は各 edit の行に付く)
        sdwan[f"{head} {_SDWAN_ORDER_KEY}"] = {"settings": [f"order {' '.join(keys)}"]}
    return sdwan


_Hits = list[tuple[FlatNode, str]]
# (リストの名前, 当たった行, リストの直下の欄〔行があった欄だけ〕, rule の (番号, 欄〔行があった欄だけ〕) の出現の順)
_PlResult = tuple[str, _Hits, dict[str, Any], list[tuple[str, dict[str, Any]]]]


def _pl_rule(rule: FlatNode, spec: _PlSpec) -> tuple[str, dict[str, Any], _Hits] | None:
    """prefix-list の `config rule` の直下の行1つを判定する。印は付けない。

    数字の番号の edit で、中の行がすべて表に当たり、prefix の行があるときだけ (番号, 欄, 当たった行) を返す。
    """
    opened = _RouteListCtx()
    if rule.scope_id != EDIT or (pattern := apply_first(PATTERNS, f"{spec.scope}_rule_edit", rule.text, opened)) is None:
        return None
    ctx = _PlCtx()
    hits: _Hits = [(rule, pattern.id)]
    for line in rule.children:
        if line.scope_id is not None or (found := apply_first(PATTERNS, f"{spec.scope}_rule", line.text, ctx)) is None:
            return None  # 入れ子の config・表に無い行・読めない形(行を失わないよう、リストごと汎用の形に残す)
        hits.append((line, found.id))
    if "prefix" not in ctx.values:  # prefix の無い rule は既定値を推測しない(ADR-0119 決定3)
        return None
    return opened.key, ctx.values, hits


def _pl_read(item: FlatNode, spec: _PlSpec) -> _PlResult | None:
    """prefix-list のリスト(見出しの直下の edit)1つを判定する。印は付けない。写せるときだけ結果を返す(依頼書 §2 の ★)。"""
    opened = _RouteListCtx()
    if (pattern := apply_first(PATTERNS, f"{spec.scope}_edit", item.text, opened)) is None:
        return None
    hits: _Hits = [(item, pattern.id)]
    top = _PlCtx()
    rules: list[tuple[str, dict[str, Any]]] = []
    for line in item.children:
        if (found := apply_first(PATTERNS, spec.scope, line.text, top)) is None:
            return None
        hits.append((line, found.id))
        if found.id != f"{spec.id}rule_block":
            if line.scope_id is not None:
                return None
            continue
        if line.scope_id != CONFIG or not line.children:  # edit の無い `config rule` も残す(消したときに差分が出るように)
            return None
        for rule in line.children:
            if (result := _pl_rule(rule, spec)) is None:
                return None
            key, values, rule_hits = result
            hits.extend(rule_hits)
            rules.append((key, values))
    return opened.key, hits, top.values, rules


def _rm_read(item: FlatNode) -> tuple[str, list[FlatNode]] | None:
    """route-map のリスト(見出しの直下の edit)1つを判定する。印は付けない。

    直下の行がすべて rule の段(`config rule`)で、その直下がすべて数字の番号の edit のときだけ (名前, rule の段) を返す。
    rule の中の行では判定しない(表に無い行は options、読めない形は未解釈)。
    """
    opened = _RouteListCtx()
    if apply_first(PATTERNS, "route_map_edit", item.text, opened) is None:
        return None
    sections: list[FlatNode] = []
    for line in item.children:
        if line.scope_id != CONFIG or apply_first(PATTERNS, "route_map", line.text, None) is None or not line.children:
            return None
        for rule in line.children:
            if rule.scope_id != EDIT or apply_first(PATTERNS, "route_map_rule_edit", rule.text, _RouteListCtx()) is None:
                return None
        sections.append(line)
    return opened.key, sections


def _route_list_read(header: str, item: FlatNode) -> bool:
    """見出し header の直下の edit 1つを写せるか。"""
    if header == _RM_HEADER:
        return _rm_read(item) is not None
    return _pl_read(item, _PL_BY_HEADER[header]) is not None


def _route_lists_to_map(tree: FlatTree) -> dict[str, frozenset[str]]:
    """最上位の prefix-list・prefix-list6・route-map で写すリスト(見出し → 直下の edit の行の集合)。

    リストごとに決める(依頼書 §2 の ★)。解釈済みの記録は行をパスで識別するため、同じ名前のリスト(重なったブロック、
    同じブロックの中の同じ名前の edit)は同じパスになる。そのうち1つでも写せないものがあれば、その名前のリストはどれも
    汎用の形に残す(写した側の印と汎用の形の側の印が食い違わないように。経路の _router_static と同じ考え方)。
    写すリストが1つも無い見出しは入れない(その見出しは今のまま汎用の形の見出しで読む)。
    """
    result: dict[str, frozenset[str]] = {}
    for header in _ROUTE_LIST_HEADERS:
        readable: set[str] = set()
        rejected: set[str] = set()
        for block in tree.children:
            if block.scope_id != CONFIG or block.text != header:
                continue
            for item in block.children:
                if item.scope_id == EDIT:
                    (readable if _route_list_read(header, item) else rejected).add(item.text)
        if names := frozenset(readable - rejected):
            result[header] = names
    return result


def _prefix_list(item: FlatNode, spec: _PlSpec, interpreted: InterpretedLines, lists: dict[str, PrefixList]) -> None:
    """写すと決めた prefix-list のリスト1つを写して印を付ける。同じ名前のリスト・同じ番号の rule はまとめる(後の行の値)。"""
    result = _pl_read(item, spec)
    assert result is not None  # 写すと決めたリストだけを渡す
    name, hits, top, rules = result
    for node, pattern_id in hits:
        interpreted.mark(node, pattern_id)
    prefix_list = lists.setdefault(name, new_prefix_list())
    prefix_list.update(top)  # type: ignore[typeddict-item]
    for number, values in rules:
        entry: PrefixListEntry = prefix_list["entries"].setdefault(
            number, {"action": "permit", "prefix": "", "ge": None, "le": None, "eq": None}  # action の既定は permit
        )
        entry.update(values)  # type: ignore[typeddict-item]  # prefix はどの rule にもある(_pl_rule)


def _route_map(item: FlatNode, interpreted: InterpretedLines, maps: dict[str, RouteMap]) -> None:
    """写すと決めた route-map のリスト1つを写して印を付ける。同じ名前のリスト・同じ番号の rule はまとめる。"""
    result = _rm_read(item)
    assert result is not None  # 写すと決めたリストだけを渡す
    name, sections = result
    interpreted.mark(item, f"{_RM_ID}list")
    entries = maps.setdefault(name, {"entries": {}})["entries"]
    for section in sections:
        interpreted.mark(section, _RM_RULE_BLOCK)
        for rule in section.children:
            opened = _RouteListCtx()
            pattern = apply_first(PATTERNS, "route_map_rule_edit", rule.text, opened)
            assert pattern is not None
            interpreted.mark(rule, pattern.id)
            ctx = _RmCtx(entries.setdefault(opened.key, new_route_map_entry("permit")))  # action の既定は permit
            for line in rule.children:
                found = apply_first(PATTERNS, "route_map_rule", line.text, ctx) if line.scope_id is None else None
                if found is not None:
                    interpreted.mark(line, found.id)
                else:  # 表に無い行は道すじで options に。action の読めない形と set・unset・config・edit でない行は未解釈
                    _option_line(line, (), ctx.entry["options"], interpreted, _RM_FIELDS, _RM_OPTION_SCOPE)


def _router_route_list(
    block: FlatNode, names: frozenset[str], interpreted: InterpretedLines, generic: dict[str, Any],
    route_maps: dict[str, RouteMap], prefix_lists: dict[str, dict[str, PrefixList]],
) -> None:
    """写す見出しの最上位のブロック1つを読む。names の edit を IR に写し、ほかの行(汎用の形に残すリストと、直下の edit で
    ない行)は今までと同じ汎用の形で config に写す。汎用の形の段は1行でも写すときだけ作る(2か所の差分にしない)。"""
    name = block.text.removeprefix("config ")
    stage = generic.get(name) or _config_stage()
    wrote = False
    for item in block.children:
        if item.scope_id == EDIT and item.text in names:
            if block.text == _RM_HEADER:
                _route_map(item, interpreted, route_maps)
            else:
                _prefix_list(item, _PL_BY_HEADER[block.text], interpreted, prefix_lists[block.text])
        elif _generic_child(item, stage, interpreted):
            wrote = True
    if wrote:
        generic.setdefault(name, stage)


def _system_global(block: FlatNode, stage: dict[str, Any], interpreted: InterpretedLines, glob: Global) -> None:
    """最上位の `config system global`(見出しは汎用の形)の直下の行を読む。`set hostname` は global.hostname に写し、
    ほかの行は今までと同じ汎用の形で stage に写す。hostname の欄は汎用の形の set・unset に入れない(依頼書 §3 の ★)。

    hostname の読めない形は未解釈に残す。値の最初の語が ENC の行(汎用の形で秘密になる行)だけは、未解釈の一覧に値が
    残らないよう、汎用の形の secrets に欄の名前だけを置く。
    """
    for line in block.children:
        if line.scope_id is None and (pattern := apply_first(PATTERNS, _SYSTEM_GLOBAL_SCOPE, line.text, glob)) is not None:
            interpreted.mark(line, pattern.id)
            continue
        reserved = frozenset() if _hostname_is_secret(line.text) else _HOSTNAME_FIELDS
        _generic_child(line, stage, interpreted, reserved=reserved)


def _system_dns(blocks: list[FlatNode], interpreted: InterpretedLines, dns: dict[str, list[str]]) -> None:
    """最上位の `config system dns`(出現の順)を global.dns に写す。直下の edit は未解釈(中も読まない)。

    サーバーの4つの欄は欄ごとに最後の値(重なったブロックも同じ)を、文字列の昇順で servers に入れる。
    ほかの行は道すじで options に入れる。サーバーの欄の読めない形は options に入れず未解釈に残す(依頼書の条件4)。
    """
    ctx = _DnsCtx()
    reserved = frozenset(_DNS_SERVER_FIELDS)
    for block in blocks:
        for line in block.children:
            if line.scope_id == EDIT:
                continue
            if line.scope_id is None and (pattern := apply_first(PATTERNS, _DNS_SCOPE, line.text, ctx)) is not None:
                interpreted.mark(line, pattern.id)
                continue
            _option_line(line, (), dns["options"], interpreted, reserved, _DNS_OPTION_SCOPE)
    dns["servers"] = sorted(ctx.servers.values())


def _system_ntp(blocks: list[FlatNode], interpreted: InterpretedLines, options: list[str]) -> None:
    """最上位の `config system ntp`(出現の順)の行を global.ntp_options に道すじで写す。直下の edit は未解釈。"""
    for block in blocks:
        for line in block.children:
            if line.scope_id == EDIT:
                continue
            _option_line(line, (), options, interpreted, scope=_NTP_OPTION_SCOPE)


def _new_firewall() -> dict[str, Any]:
    """ブロックが1つも無くても置く形(片方の config だけにキーがあると、余計な差分になるため)。"""
    return {
        "policies": {}, "order": [], "addresses": {}, "address_groups": {}, "services": {}, "service_groups": {},
        # REQ-196(ADR-0139 決定3)。ブロックが無くても空の形で置く(判断の記録の回答 2026-11-04)
        "vips": {}, "vips6": {}, "vip_groups": {}, "ippools": {}, "ippools6": {}, "central_snat": {},
        "central_snat_order": [],
    }


def extract(
    tree: FlatTree, interpreted: InterpretedLines
) -> tuple[
    dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
    dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Any], list[DuplicateLine],
]:
    """(interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, global,
    vendor_specific["fortinet"], 値の食い違い) を返す。

    bgp は最上位の `config router bgp` から写す(REQ-191)。直下の最後の `set as` が読めなければ None で、
    そのブロックは汎用の形で config に写す。`config vdom` の中の `config router bgp` はいつも汎用の形。
    ospf は最上位の `config router ospf` から写す(REQ-192。鍵 "0")。入れ子の中も含めて set・unset・edit の行が
    無ければ(空の入れ子の config だけのブロックを含む) {} で、
    そのブロックは汎用の形で config に写す。`config vdom` の中の `config router ospf` と `config router ospf6` は汎用の形。
    ipsec は最上位の `config vpn ipsec phase1`・`phase2`・`phase1-interface`・`phase2-interface` の直下の edit から写す
    (REQ-193)。写す edit が無ければ None(edit の無いブロックは何も足さず、汎用の形にも入れない)。
    `vpn ipsec manualkey`・`concentrator` などと `config vdom` の中は汎用の形。
    sdwan は最上位の `config system sdwan`・`config system virtual-wan-link` から写す(REQ-194)。見出しごとに、入れ子の
    中も含めて set・unset・edit の行が無ければ、その見出しの鍵は作らず、ブロックは汎用の形で config に写す。
    `config vdom` の中は汎用の形。
    route_maps・prefix_lists・ipv6_prefix_lists は最上位の `config router route-map`・`prefix-list`・`prefix-list6` の
    直下の edit(リスト)から写す(REQ-195)。写せないリストは汎用の形で config に残す(見出しは写すリストがあるときだけ
    専用の見出しで読む)。`config vdom` の中と `router access-list`・`aspath-list`・`community-list` は汎用の形。
    global は new_global() に、最上位の `config system global` の `set hostname`(hostname。見出しとほかの行は汎用の形)、
    `config system dns`(dns)、`config system ntp`(ntp_options)を写したもの(REQ-196)。DNS・NTP のブロックは config に
    入れない。`config vdom`・`config global` の中は汎用の形。

    経路は config の出現順。経路が無くても static_routes のキーは空のリストで置く。firewall も同じく、空の辞書・空のリストで置く。
    専用の見出しに当たらない最上位のブロックは、汎用の形で config に写す(同じ名前のブロックは1つにまとめる)。
    写すブロックが無くても config は空の辞書で置く。見出しの注記(header)も、行が無ければ空の辞書で置く。
    食い違いは木が記録するため、ここでは空。
    """
    interfaces: dict[str, Interface] = {}
    vendor: dict[str, Any] = {}
    routes: list[StaticRoute] = []
    details: list[dict[str, Any]] = []
    route_blocks: list[tuple[FlatNode, bool]] = []
    firewall = _new_firewall()
    generic: dict[str, Any] = {}
    file_header: dict[str, str] = {}
    bgp_blocks: list[FlatNode] = []
    ospf_blocks: list[FlatNode] = []
    ipsec_blocks: list[FlatNode] = []
    sdwan_blocks: list[tuple[FlatNode, str]] = []
    route_maps: dict[str, RouteMap] = {}
    prefix_lists: dict[str, dict[str, PrefixList]] = {_PL_HEADER: {}, _PL6_HEADER: {}}
    glob = new_global()
    dns_blocks: list[FlatNode] = []
    ntp_blocks: list[FlatNode] = []
    # BGP・OSPF・SD-WAN・prefix-list・route-map を写すかは、重なったブロックも含めて先に1回だけ決める
    # (ADR-0139 決定4、REQ-192 §2、REQ-194、REQ-195)
    local_as = _bgp_local_as(tree)
    map_ospf = _ospf_has_content(tree)
    map_sdwan = _sdwan_headers_to_map(tree)
    map_lists = _route_lists_to_map(tree)
    mapped = {
        _BGP_BLOCK: local_as is not None,
        _OSPF_BLOCK: map_ospf,
        _SDWAN_BLOCK: "config system sdwan" in map_sdwan,
        _SDWAN_VWL_BLOCK: "config system virtual-wan-link" in map_sdwan,
        _PL_BLOCK: _PL_HEADER in map_lists,
        _PL6_BLOCK: _PL6_HEADER in map_lists,
        _RM_BLOCK: _RM_HEADER in map_lists,
    }
    top_patterns = _TOP_PATTERNS[frozenset(pattern_id for pattern_id, yes in mapped.items() if not yes)]
    for block in tree.children:
        if block.scope_id is None:  # 最上位の、子を持たない行(見出しの注記。ADR-0129 決定1)
            if (pattern := apply_first(PATTERNS, "fgt_header", block.text, file_header)) is not None:
                interpreted.mark(block, pattern.id)
            continue
        ctx = _GenCtx({"config": generic})  # 汎用の形の見出しだけが使う(最上位を段に見立てる)
        if block.scope_id != CONFIG or (header := apply_first(top_patterns, "top", block.text, ctx)) is None:
            continue
        interpreted.mark(block, header.id)
        if header.id == "interfaces.fgt_block":
            _system_interface(block, interpreted, interfaces, vendor)
        elif header.category == "static_routes":
            route_blocks.append((block, header.id == "static_routes.fortinet.static6_block"))
        elif header.id == _BGP_BLOCK:
            bgp_blocks.append(block)
        elif header.id == _OSPF_BLOCK:
            ospf_blocks.append(block)
        elif header.id == _IPSEC_BLOCK:
            ipsec_blocks.append(block)
        elif header.id in _SDWAN_BLOCK_IDS:
            sdwan_blocks.append((block, _SDWAN_HEADERS[block.text]))
        elif header.id in _ROUTE_LIST_BLOCK_IDS:
            _router_route_list(block, map_lists[block.text], interpreted, generic, route_maps, prefix_lists)
        elif header.id == _DNS_BLOCK:
            dns_blocks.append(block)
        elif header.id == _NTP_BLOCK:
            ntp_blocks.append(block)
        elif header.id in _FW_BY_HEADER:
            _firewall(block, _FW_BY_HEADER[header.id], interpreted, firewall)
        elif ctx.name == _SYSTEM_GLOBAL:  # 見出しは汎用の形。直下の hostname だけ global に写す(REQ-196)
            _system_global(block, ctx.opened, interpreted, glob)
        else:
            _generic(block, ctx.opened, interpreted, community=ctx.name in SNMP_COMMUNITY_BLOCKS)
    _router_static(route_blocks, interpreted, routes, details)
    bgp = _router_bgp(bgp_blocks, local_as, interpreted) if local_as is not None else None
    ospf = {_OSPF_KEY: _router_ospf(ospf_blocks, interpreted)} if map_ospf else {}
    ipsec = _vpn_ipsec(ipsec_blocks, interpreted)
    sdwan = _system_sdwan(sdwan_blocks, interpreted)
    _system_dns(dns_blocks, interpreted, glob["dns"])
    _system_ntp(ntp_blocks, interpreted, glob["ntp_options"])
    return (
        interfaces, routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists[_PL_HEADER], prefix_lists[_PL6_HEADER], glob,
        {"interfaces": vendor, "static_routes": details, "firewall": firewall, "config": generic, "header": file_header},
        [],
    )
