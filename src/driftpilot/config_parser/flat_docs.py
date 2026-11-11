"""平坦 config(B型)の表から対応一覧(Markdown / JSON)を生成する(ADR-0024、REQ-21)。

parser-patterns.md(pattern_docs.py)と同じ運用。docs/notes/flat-patterns.md はこの出力そのもので、ずれたらテストが失敗する。
内容は、行頭スコープ表(FLAT-NN)・IR への写しの表・指紋の3つ。
FortiGate(D型。REQ-22)は行頭スコープ表を持たないため、木の組み立ての規則・写しの表・指紋を出す(docs/notes/fortinet-patterns.md)。
"""

import json
from typing import Any

from driftpilot import i18n
from driftpilot.config_parser.flat_scopes import FlatScope
from driftpilot.config_parser.fortinet_tree import EMBEDDED, HASH_INPUT_VERSION
from driftpilot.config_parser.pattern_docs import _DOC as _PATTERN_DOC
from driftpilot.config_parser.pattern_docs import _cell, _code, _record
from driftpilot.config_parser.platforms import FLAT_PLATFORMS, patterns_fingerprint

GENERATE_COMMAND = "driftpilot patterns --platform fujitsu_sir --output docs/notes/flat-patterns.md"
FORTINET_GENERATE_COMMAND = "driftpilot patterns --platform fortinet --output docs/notes/fortinet-patterns.md"

_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(平坦 config: {{platform}})

> **自動生成。手で編集しない。** 行頭スコープ表(`src/driftpilot/config_parser/flat_scopes.py`)と
> 写しの表(`src/driftpilot/config_parser/fujitsu_sir.py` の `PATTERNS`)から作る。
> 再生成: `{GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

字下げの無い config(B型。ADR-0024)は、行頭の「語 ＋ 添字」でスコープを決め、残りをそのスコープの子の行にする。
行頭スコープ表に無い行頭は読まない(行全体がトップレベルの未解釈になる)。似た語に寄せて読むこともしない。
表の並びが当てる順で、最初に当たったエントリが結果を決める。書式は行頭からの正規表現(スコープ部分だけ)で、
直後は空白か行末でなければならない。

- 添字の範囲(`1-4`)と列挙(`1,3`)は、「展開する添字」の列にある位置でだけ展開する。値の位置のリスト
  (`vlan tag 10,20,30` など)は展開しない。展開できない範囲(逆順、1行で 4,096 を超える)は行ごと未解釈にする
- 親のあるエントリは2段目のスコープ。親の見出し行が無くても親のスコープを暗黙に作る。深さは2段まで
- 同じスコープの下で同じ行が2回以上ある、または同じ項目に違う値が書かれていると、警告(`duplicate_line`)を出す。
  どちらの行も捨てない。IR は後の行の値を使う
"""

_FORTINET_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(FortiGate: fortinet)

> **自動生成。手で編集しない。** 写しの表(`src/driftpilot/config_parser/fortinet.py` の `PATTERNS`)と
> 木の組み立ての版(`src/driftpilot/config_parser/fortinet_tree.py` の `TREE_VERSION`)から作る。
> 再生成: `{FORTINET_GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

FortiGate の config(D型。ADR-0025)は、終端記号の対で木を組む。**字下げは見ない。** hier_config も使わない。

- `config <名前…>` … `end` がスコープ、`edit <キー>` … `next` がスコープ内の要素。行の第1語だけで判定する
- `edit` のキーは引用符を外した原文のまま(数字に変換しない)。木のテキストは `edit <キー>`。キーが変われば追加と削除になる
- `set` / `unset` とその他の行は、今のスコープの子。`end` / `next` は木のノードにしない(行として数えない)
- 対応が取れなければエラーで止める(推測で閉じない): 対応の無い `end` / `next`、`edit` の中の `end`、`config` の中の `next`、
  `config` の直下に無い `edit`、ファイルの末尾で閉じていないブロック、閉じていない引用符、`set` 以外の行で行をまたぐ引用符
- 引用符(`"` と `'`)は行をまたいで追う。`\\` の次の1文字は飛ばす(`\\"` では閉じない)。`''` は空の文字列
- `set` の値の引用符がその行で閉じなければ、閉じ引用符の行までを1つのノードにする。テキストは
  `set <attr> {EMBEDDED} lines <N> sha256 <64桁>`。本文は木にも IR にも入らない。ブロック名や中身の語では判定しない。
  ハッシュは、開き引用符から閉じ引用符まで(両端を含む)の物理行を LF でつないだ UTF-8 の SHA-256 の全桁
  (改行を揃える以外は正規化しない。hash_input_version={HASH_INPUT_VERSION})
- 同じスコープの直下で、同じ属性(`set` / `unset` の属性名)・同じ行・同じ見出しが2回以上あると、警告(`duplicate_line`)を出す。
  どちらの行も捨てない。IR は後の行の値を使う。最上位の `config vdom` が2つ以上あるときは、見出しの重複としては
  警告せず、子を持つ同じキーの edit が2つ以上あるときだけ、スコープ `config vdom` で警告する(ADR-0135)
- 最上位の行の前処理(保存のたびに変わる値を木に入れない。ADR-0129 決定1): `#conf_file_ver=<数字>` の行は木に入れない
  (行番号の数え方は変えない)。`#config-version=` の行の最後の `:user=<…>` を落とす。`config` の中の `#` の行は変えない
"""


# IR への写しの案内の段落(日本語の版。英語の版は _FLAT_MAP_EN・_FORTINET_MAP_EN。docs/notes の生成文書は日本語の版)
_FLAT_MAP_JA = (
        "scope はスコープの種類(`ether` は FLAT-01、`lan` は FLAT-02、`remote` は FLAT-03、"
        "`remote_ap` は FLAT-04(`remote <n> ap <m>`)、`bgp neighbor` は FLAT-07、"
        "`other_scope` は FLAT-05・06・08・09・10(`bridgegroup`・`pseudo-ether`・`serverinfo`・`routemanage`・"
        "`wan`・`etheroam`・`backup`・`ethergroup`)。"
        "`top` は最上位のスコープでない行)。"
        "書式はスコープ部分を除いた残りの行全体(`top` は行全体)に対する正規表現。",
        "`remote` と `remote_ap` は interfaces には入れない。経路の行は static_routes に、ほかの行は "
        "vendor_specific の remotes(相手と接続先)に写す。秘密を含む行は値を持たず、コマンドの名前だけを置く。",
        "今の項目に写らない残りの行(ADR-0041)は、値を解釈せずに行の集合(`attributes`・`secrets`)として写す。"
        "`ether`・`lan` は vendor_specific の interfaces、`other_scope` は scopes(キーは木のスコープ名)、"
        "`top` の汎用の行と秘密は settings に置く。秘密の行(`lan` と `top`)は値を持たず、当たった頭の原文(番号を含む)だけを置く。"
        "`name`・`ip route` で始まるのに形が合わない remote の行、今の項目の頭(`use`・`vlan`・`ip address` など)で始まるのに"
        "形が合わない `ether`・`lan` の行、別の場所の秘密の頭で始まる行、スコープだけの行、"
        "行頭スコープ表の語で始まる最上位の行(展開できない範囲など)、表に無い `bgp …` の行(`bgp ip network` など)は、"
        "木には入るが IR には写さない(未解釈)。\n",
)

_FORTINET_MAP_JA = (
        "専用の読み方で写すのは最上位の `config system interface`・`config router static`・`config router static6`・"
        "`config router bgp`(直下の最後の `set as` が 1〜4294967295 の10進の数字のとき)・"
        "`config router ospf`(入れ子の中も含めて `set`・`unset`・`edit` の行があるとき)・"
        "`config vpn ipsec phase1`・`phase2`・`phase1-interface`・`phase2-interface`・"
        "`config system sdwan`・`config system virtual-wan-link`(入れ子の中も含めて `set`・`unset`・`edit` の行があるとき)・"
        "`config router prefix-list`・`config router prefix-list6`・`config router route-map`(写せるリストがあるとき)・"
        "`config system dns`・`config system ntp`と、ファイアウォールの11のブロック"
        "(`config firewall policy` / `address` / `addrgrp` / `service custom` / `service group` / `vip` / `vip6` / "
        "`vipgrp` / `ippool` / `ippool6` / `central-snat-map`)。"
        "最上位の `config system global` は見出しとほかの行を汎用の形で読み、直下の `set hostname` だけを `global.hostname` に写す。"
        "ほかの最上位のブロック(`config firewall address6`・`config vdom` などを含む)は、"
        "汎用の形(FortiOS の書き方のまま)で `vendor_specific.fortinet.config` に写す(ADR-0040)。"
        "最上位の、子を持たない `#config-version=`・`#buildno=`・`#global_vdom=` の行(値が空でないもの)は、"
        "`vendor_specific.fortinet.header` に原文で写す(行が無ければ `{}`。ほかの `#` の行は未解釈。ADR-0129 決定1)。"
        "scope は `top`(最上位のブロックの見出し)、`fgt_header`(最上位の、子を持たない行)、"
        "`system_interface`(`config system interface` の直下の `edit`)、`interface`(その `edit` の中)、"
        "`router_static`(`config router static` の直下の `edit`)、`static_route`(その `edit` の中)、"
        "`router_static6`(`config router static6` の直下の `edit`)、`static_route6`(その `edit` の中)、"
        "`router_bgp`(`config router bgp` の直下)、`bgp_neighbor_edit`・`bgp_neighbor_group_edit`"
        "(`config neighbor`・`config neighbor-group` の直下の `edit`)、`bgp_neighbor`(相手の `edit` の中)、"
        "`bgp_option`(BGP の表に無い行を `options` に道すじで写す。どの段でも同じ表)、"
        "`router_ospf`(`config router ospf` の直下)、`ospf_network_edit`(`config network` の直下の `edit`)、"
        "`ospf_network`(その `edit` の中)、`ospf_option`(OSPF の表に無い行を `options` に道すじで写す。どの段でも同じ表)、"
        "`ipsec_edit`(IPsec の4つの見出しの直下の `edit`)、"
        "`ipsec_option`(その `edit` の中の行を `settings` に道すじで写す。どの段でも同じ表)、"
        "`sdwan_block`(SD-WAN の2つの見出しの直下の `config`)、`sdwan_table`(その `config` の直下の `edit`)、"
        "`sdwan_option`(SD-WAN の行を `settings` に道すじで写す。どの段でも同じ表)、"
        "`prefix_list_edit`・`prefix_list6_edit`・`route_map_edit`(prefix-list・prefix-list6・route-map の見出しの直下の "
        "`edit`。リスト)、`prefix_list`・`prefix_list6`・`route_map`(リストの中)、`prefix_list_rule_edit`・"
        "`prefix_list6_rule_edit`・`route_map_rule_edit`(`config rule` の直下の `edit`)、`prefix_list_rule`・"
        "`prefix_list6_rule`・`route_map_rule`(rule の `edit` の中)、`route_map_option`(route-map の rule の中の表に無い行を "
        "`options` に道すじで写す。どの段でも同じ表)、"
        "`fgt_system_global`(最上位の `config system global` の直下で先に試す。hostname)、"
        "`fgt_dns`(`config system dns` の直下。サーバーの4つの欄)、`dns_option`(DNS のほかの行を `global.dns.options` に"
        "道すじで写す。どの段でも同じ表)、`ntp_option`(`config system ntp` の行を `global.ntp_options` に道すじで写す。"
        "どの段でも同じ表)、"
        "`fw_<名前>_edit`(ファイアウォールのブロックの直下の `edit`)、`fw_<名前>`(その `edit` の中)、"
        "`fgt_generic`(汎用の形の段の中)、`fgt_generic_snmp`(SNMP のコミュニティの `edit` の中で先に試す)。"
        "書式は行全体に対する正規表現。",
        "専用の読み方のブロックの `edit` の中の、表に当たらない行(入れ子の `config ipv6` / `config secondaryip` などを含む)は、"
        "汎用の形の行(`fgt_generic` の表)でその `edit` の `other` に写す(ADR-0119 決定1)。"
        "インターフェースの `other` は固定キーで、写す行が無ければ `None`。ファイアウォールの `edit` と経路の詳細"
        "(`vendor_specific.fortinet.static_routes[]`)は、写す行があるときだけ `other` のキーを置く。"
        "表が持つ欄(インターフェースの表の欄、ファイアウォールの表の欄と `uuid`、経路の `dst`・`gateway`・`device`・"
        "`distance`・`status`)の、表に当たらない形は `other` に入れず、木には入るが IR には写さない(未解釈。決定2)。"
        "汎用の形でも、`set` でも `unset <1語>` でもない行は未解釈。",
        "汎用の形の段は、config の段が `set`・`unset`・`secrets`・`config`・`edits`・`order`、"
        "edit の段(`other` も)が `set`・`unset`・`secrets`・`config` を空でも必ず持つ。"
        "秘密の欄は値を持たず、名前だけを `secrets` に置く。",
        "経路は、入れ子の `config`、表が持つ欄の読めない形、`set` でも `unset <1語>` でもない行が1行でもある `edit` を"
        "まるごと写さない(その `edit` の行はすべて未解釈)。`set distance`・`set status` は経路の `distance`・`enabled` に読む"
        "(決定3)。通常の出力と `show full-configuration` の出力(既定値の行が付く)は、`distance` などが違う差分になる"
        "(既定値を推測しない)。`set gateway`・`set device` がどちらも無い `edit` は、`set blackhole enable`・"
        "`set virtual-wan-link enable`・値のある `set sdwan-zone` のどれかがあるときだけ写す(`next_hop`・`interface` は "
        "`None`。それらの行は `other` に入る。ADR-0129 決定2)。`config router static6` は同じ手順で読み"
        "(宛先の既定は `::/0`)、経路の詳細に `\"static6\": true` を置く(決定3)。",
        "ファイアウォールは、表に無い行があっても `edit` を写す(ADR-0037 決定4)。表の欄の `unset <欄>` は、その欄を"
        "「明示的に未設定」の印(`{\"unset\": true}`)にする(ADR-0119 決定4)。"
        "`set uuid` は解釈済みにするが、値は持たない(ADR-0037 決定3)。"
        "ポリシーの `edit` のキーは出現の順に `order` に、セントラル NAT(`central-snat-map`。上から当たる)の `edit` のキーは"
        "出現の順に `central_snat_order` に並べる(並べ替えは差分に出る。ADR-0139 決定4)。"
        "ブロックが無くても、11の表の鍵と2つの順の一覧は空の形で置く。",
        "BGP(REQ-191。ADR-0139 決定2・4)は、重なった `config router bgp` も1つの `bgp` にまとめる。"
        "直下の最後の `set as` が無い・`0`・数でない(asdot を含む)ときは、どのブロックも今のまま汎用の形(`bgp` は `None`)。"
        "写すときは `vendor_specific.fortinet.config` に `router bgp` を入れない。"
        "表に無い行は `options` に道すじ(`network > 1 > prefix 192.0.2.0 255.255.255.0`・"
        "`redistribute connected > status enable`)で残し、子が1つも写らない `config`・`edit` は道すじだけを入れる。"
        "秘密の欄は汎用の形と同じ判定で、どの段でも値を持たず欄の名前だけを入れる"
        "(表が持つ欄の秘密の行も、未解釈に残さず欄の名前だけを入れる。原文が値ごと未解釈の一覧に残らないように)。"
        "表が持つ欄(直下の `as`・`router-id`、相手の中の `remote-as`・`description`・`update-source`・`shutdown`・"
        "`password`)の表に当たらない形と、`config router bgp` の直下の `edit` は未解釈。"
        "`config vdom` の中の `config router bgp` は今のまま汎用の形。",
        "OSPF(REQ-192。ADR-0139 決定2・4)は、重なった `config router ospf` も1つの `ospf[\"0\"]`"
        "(`process_id` は `0`、`vrf` は `None`)にまとめる。入れ子の中も含めて `set`・`unset`・`edit` の行が無いとき"
        "(空のブロックや、OSPF を設定していない既定の出力のような空の入れ子の `config` だけのブロック)は、"
        "今のまま汎用の形(`ospf` は `{}`)。"
        "写すときは `vendor_specific.fortinet.config` に `router ospf` を入れない。"
        "`set router-id`・`set passive-interface`(集合)と、`config network` の `edit` の `set prefix <アドレス> <マスク>`・"
        "`set area` を `networks`(`wildcard` はマスクの反転、`area` が無ければ `0.0.0.0`。`edit` の番号は持たない)に読む。"
        "`prefix` が無い・読めない(マスクの無い形・逆のマスク)、`area` が読めない、`prefix`・`area` の `unset` がある `edit` は、"
        "`networks` に入れず `edit` ごと道すじ(`network > <番号> > …`)で `options` に残す。"
        "表に無い行は BGP と同じく `options` に道すじで残す。秘密の行は値を持たず欄の名前だけを入れる"
        "(汎用の形の判定に、値の語のどれかが `ENC`、`md5-keys`・`md5-key`、Cisco の OSPF と同じ鍵の語の判定を足す)。"
        "直下の `router-id`・`passive-interface` の表に当たらない形と、`config router ospf` の直下の `edit` は未解釈。"
        "`config vdom` の中の `config router ospf` と `config router ospf6` は今のまま汎用の形。",
        "IPsec(REQ-193。ADR-0139 決定2・4)は、最上位の `config vpn ipsec phase1`・`phase2`・`phase1-interface`・"
        "`phase2-interface` の直下の `edit` ごとに `ipsec.blocks[\"<種類> <名前>\"]`(例 `phase1-interface vpn-a`)を作る"
        "(Cisco・Arista と同じ形。`options` はいつも空)。同じ種類・同じ名前の `edit` は重なったブロックも含めて1つにまとめ、"
        "同じ名前の `phase1` と `phase2` は別の鍵になる。写すときは `vendor_specific.fortinet.config` に入れない。"
        "`edit` の中の行は BGP と同じ書き方の道すじで `settings`(集合。昇順)に入れる"
        "(例 `remote-gw 192.0.2.1`・`ipv4-exclude-range > 1 > start-ip 192.0.2.10`・`unset dpd-retryinterval`)。"
        "秘密の行は値も欄の名前も `settings` に入れず、その項目の `keys_configured` を `True` にする"
        "(値の変更と秘密の行の数は見ない。判定は汎用の形の判定に、値の語のどれかが `ENC`、欄の名前が "
        "`secret`・`passwd`・`password` を含む、を足したもの。`unset <欄>` は秘密にしない)。"
        "秘密の行しか持たない入れ子の段は道すじだけを入れる。`edit` の無いブロックは見出しだけを解釈済みにし、何も足さない"
        "(ほかに写す `edit` が無ければ `ipsec` は `None`)。ブロックの直下の `edit` でない行、`edit` の中の "
        "`set` でも `unset <1語>` でも `config`・`edit` でもない行は未解釈。"
        "`vpn ipsec manualkey`・`concentrator` などと `config vdom` の中は今のまま汎用の形。",
        "SD-WAN(REQ-194。ADR-0139 決定2・4)は、最上位の `config system sdwan`・`config system virtual-wan-link` を "
        "`sdwan`(Arista と同じ形。`settings` だけ)に写す。見出しの語(`sdwan` か `virtual-wan-link`)を `<S>` として、"
        "直下の `set`・`unset` は鍵 `<S>`、`config <表>` の直下の `edit` でない行は鍵 `<S> <表>`、"
        "`config <表>` > `edit <キー>` の中の行は鍵 `<S> <表> <キー>`(例 `sdwan members 1`・`sdwan service 2`)の `settings`"
        "(集合。昇順)に、BGP と同じ書き方の道すじで入れる(例 `status enable`・`sla > 1 > latency-threshold 100`)。"
        "写す見出し・表・`edit` には、中に行が無くても鍵を作る(`settings` は空)。"
        "重なったブロック・同じ名前の表・同じキーの `edit` は同じ鍵にまとめる。"
        "`config service` は上から当たるので、直下の `edit` のキーを出てきた順に鍵 `<S> service-order` の `settings` に "
        "`order 2 1 3` のような1つの文字列で入れる(順の入れ替えが差分に出る。同じキーは最初の出現だけ)。"
        "見出しごとに、入れ子の中も含めて `set`・`unset`・`edit` の行が無いとき(空のブロックや、空の入れ子の `config` だけの"
        "ブロック)は、その見出しは今のまま汎用の形。写すときは `vendor_specific.fortinet.config` に入れない。"
        "秘密の欄は汎用の形と同じ判定で、どの段でも値を持たず欄の名前だけを入れる。"
        "見出しの直下の `edit` と、どの段でも `set` でも `unset <1語>` でも `config`・`edit` でもない行は未解釈。"
        "`config vdom` の中は今のまま汎用の形。",
        "prefix-list・route-map(REQ-195。ADR-0139 決定2・4、ADR-0047)は、最上位の `config router prefix-list`・"
        "`config router prefix-list6`・`config router route-map` の直下の `edit <名前>` を1つのリストとして、"
        "`prefix_lists`・`ipv6_prefix_lists`・`route_maps`(Cisco・Arista と同じ形)に写す。"
        "`config rule` の `edit <番号>` が `entries[<番号>]`(`str(int(…))`)。"
        "prefix-list は `set comments` を `description` に、rule の `set action permit|deny`(無ければ `permit`)・"
        "`set prefix <アドレス> <マスク>`(prefix-list6 は `set prefix6 <プレフィックス>`。正規の書き方)・"
        "`set ge|le <数>`・`unset ge|le`(`None`)を読む(`eq` はいつも `None`)。"
        "`set prefix any`・読めない形・表に無い行のある rule、`set prefix` の無い rule などがあるリストは、"
        "行を失わないようにリストごと写さず、今のまま汎用の形に残す。"
        "route-map は rule の `set action permit|deny`(無ければ `permit`)、`set match-<語> <値…>`(`match` に `<語> <値…>`)、"
        "`set set-<語> <値…>`(`set` に `<語> <値…>`)を読み、ほかの行・`unset`・入れ子は BGP と同じ書き方の道すじで "
        "`options` に入れる(秘密の欄は欄の名前だけ。`action` の読めない形は未解釈)。"
        "`set comments` などの、どの rule にも属さない行があるリストは汎用の形に残す。"
        "写すかはリストごとに決め、同じ名前のリスト(重なったブロックを含む)は1つでも写せないものがあればどれも汎用の形に残す。"
        "写すリストが1つも無い見出しは今のまま汎用の形の見出しで読む。写す見出しでも、汎用の形に残すリストと直下の "
        "`edit` でない行は `vendor_specific.fortinet.config` に写す(1行も無ければ入れない)。"
        "`config vdom` の中と `router access-list`・`aspath-list`・`community-list` は今のまま汎用の形。",
        "全体(REQ-196。ADR-0139 決定2)は、最上位の `config system global` の直下の `set hostname <値>`(引用符を外した1語)を "
        "`global.hostname`(Cisco と同じ項目。差分は `[changed] global / hostname`)に写し、汎用の形の `system global` の "
        "`set`・`unset` には入れない(ほかの行は今のまま汎用の形。`''`・`unset hostname` などの読めない形は未解釈)。"
        "`config system dns` の `set primary`・`set secondary`(IPv4)・`set ip6-primary`・`set ip6-secondary`(IPv6)は "
        "`global.dns.servers` に `<正規のアドレス> <欄>`(例 `192.0.2.53 primary`)で、ほかの行は `global.dns.options` に"
        "道すじで入れる(4つの欄の読めない形は未解釈)。`config system ntp` の行はすべて `global.ntp_options` に道すじ"
        "(例 `ntpserver > 1 > server 192.0.2.1`)で入れる。秘密の欄(汎用の形と同じ判定。NTP は欄の名前が `key` のものも)は"
        "値を持たず欄の名前だけを入れる。DNS・NTP は `vendor_specific.fortinet.config` に入れない。"
        "`config vdom`・`config global` の中は今のまま汎用の形。\n",
)


# --- 英語の版(REQ-259 / ADR-0190 決定3) ---
# 見出し・案内の段落・指紋の行・節の見出し・列の名前だけを英語にする。各行の備考(note)・写し先(target)・行頭スコープ表の備考は
# 日本語のまま(写し先は表の指紋の計算に入っているので、言語で変えない)。docs/notes の生成文書は日本語の版の出力
_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (flat config: {{platform}})

> **Generated automatically. Do not edit by hand.** It is made from the line-head scope table (`src/driftpilot/config_parser/flat_scopes.py`) and
> the mapping table (`PATTERNS` of `src/driftpilot/config_parser/fujitsu_sir.py`).
> Regenerate: `{GENERATE_COMMAND}`
> If the tables are changed and this file is out of date, a test fails.

A config without indentation (type B. ADR-0024) decides the scope from the "word + index" at the head of a line, and treats the rest as child lines of that scope.
A line head that is not in the line-head scope table is not read (the whole line becomes an uninterpreted top-level line). Lines are not read as similar words either.
The order of the table is the order of matching, and the first matching entry decides the result. The form is a regular expression from the line head (the scope part only),
and what follows must be a space or the end of the line.

- Index ranges (`1-4`) and enumerations (`1,3`) are expanded only at the positions in the "Expanded index" column. Lists at value positions
  (such as `vlan tag 10,20,30`) are not expanded. A range that cannot be expanded (reversed, or more than 4,096 in one line) makes the whole line uninterpreted
- An entry with a parent is a second-level scope. The parent scope is created implicitly even when there is no heading line of the parent. The depth is at most 2 levels
- When the same line appears two or more times under the same scope, or different values are written for the same item, a warning (`duplicate_line`) is given.
  Neither line is dropped. The IR uses the value of the later line
- The notes and the targets in the table rows are developer notes and are shown in Japanese
"""

_FORTINET_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (FortiGate: fortinet)

> **Generated automatically. Do not edit by hand.** It is made from the mapping table (`PATTERNS` of `src/driftpilot/config_parser/fortinet.py`) and
> the version of the tree building (`TREE_VERSION` of `src/driftpilot/config_parser/fortinet_tree.py`).
> Regenerate: `{FORTINET_GENERATE_COMMAND}`
> If the tables are changed and this file is out of date, a test fails.

The FortiGate config (type D. ADR-0025) builds a tree from pairs of terminators. **Indentation is not looked at.** hier_config is not used either.

- `config <name…>` … `end` is a scope, and `edit <key>` … `next` is an element in the scope. Lines are judged only by their first word
- The key of `edit` is the original text with the quotation marks removed (it is not converted to a number). The text of the tree is `edit <key>`. If the key changes, it becomes an addition and a deletion
- `set` / `unset` and other lines are children of the current scope. `end` / `next` are not tree nodes (they are not counted as lines)
- If the pairs do not match, it stops with an error (it does not close them by guessing): an `end` / `next` without a pair, an `end` inside `edit`, a `next` inside `config`,
  an `edit` that is not directly under `config`, a block that is not closed at the end of the file, a quotation mark that is not closed, and a quotation mark that spans lines on a line other than `set`
- Quotation marks (`"` and `'`) are followed across lines. The one character after `\\` is skipped (it is not closed by `\\"`). `''` is an empty string
- When the quotation mark of a `set` value is not closed on that line, everything up to the line of the closing quotation mark becomes one node. The text is
  `set <attr> {EMBEDDED} lines <N> sha256 <64 digits>`. The body does not enter the tree or the IR. It is not judged by the block name or the words inside.
  The hash is the full SHA-256 digits of the UTF-8 text made by joining the physical lines from the opening quotation mark to the closing one (both ends included) with LF
  (nothing is normalized except unifying the line breaks. hash_input_version={HASH_INPUT_VERSION})
- When the same attribute (the attribute name of `set` / `unset`), the same line, or the same heading appears two or more times directly under the same scope, a warning (`duplicate_line`) is given.
  Neither line is dropped. The IR uses the value of the later line. When there are two or more top-level `config vdom`, it does not warn about duplicated headings, and
  warns with the scope `config vdom` only when there are two or more edits with the same key that have children (ADR-0135)
- Preprocessing of top-level lines (values that change at every save are not put in the tree. ADR-0129 decision 1): a `#conf_file_ver=<number>` line is not put in the tree
  (the way line numbers are counted does not change). The last `:user=<…>` of a `#config-version=` line is dropped. `#` lines inside `config` are not changed
- The notes and the targets in the table rows are developer notes and are shown in Japanese
"""
_HEADER = i18n.choose(_HEADER_JA, _HEADER_EN)
_FORTINET_HEADER = i18n.choose(_FORTINET_HEADER_JA, _FORTINET_HEADER_EN)

_FLAT_MAP_EN = (
    "scope is the kind of scope (`ether` is FLAT-01, `lan` is FLAT-02, `remote` is FLAT-03, "
    "`remote_ap` is FLAT-04 (`remote <n> ap <m>`), `bgp neighbor` is FLAT-07, "
    "and `other_scope` is FLAT-05, 06, 08, 09 and 10 (`bridgegroup`, `pseudo-ether`, `serverinfo`, `routemanage`, "
    "`wan`, `etheroam`, `backup` and `ethergroup`). "
    "`top` is a line that is not in a top-level scope). "
    "The form is a regular expression for the whole remaining line after the scope part (the whole line for `top`).",
    "`remote` and `remote_ap` are not put in interfaces. Route lines are mapped to static_routes, and the other lines to "
    "the remotes of vendor_specific (the other party and the connection destination). A line with a secret has no value and only the command name is put.",
    "The remaining lines that are not mapped to the current items (ADR-0041) are mapped as sets of lines (`attributes` and `secrets`) without interpreting the values. "
    "`ether` and `lan` go to the interfaces of vendor_specific, `other_scope` goes to scopes (the key is the scope name of the tree), "
    "and the generic lines and secrets of `top` go to settings. Secret lines (of `lan` and `top`) have no value, and only the original text of the matching head (including the number) is put. "
    "Remote lines that start with `name` or `ip route` but do not match the form, `ether` and `lan` lines that start with the head of a current item (`use`, `vlan`, `ip address` and so on) but "
    "do not match the form, lines that start with the head of a secret elsewhere, scope-only lines, "
    "top-level lines that start with a word of the line-head scope table (such as ranges that cannot be expanded), and `bgp …` lines that are not in the table (such as `bgp ip network`) "
    "enter the tree but are not mapped to the IR (uninterpreted).\n",
)

_FORTINET_MAP_EN = (
    "What is mapped by a dedicated reader is the top-level `config system interface`, `config router static`, `config router static6`, "
    "`config router bgp` (when the last `set as` directly under it is a decimal number from 1 to 4294967295), "
    "`config router ospf` (when there are `set`, `unset` or `edit` lines, including inside nested blocks), "
    "`config vpn ipsec phase1`, `phase2`, `phase1-interface`, `phase2-interface`, "
    "`config system sdwan`, `config system virtual-wan-link` (when there are `set`, `unset` or `edit` lines, including inside nested blocks), "
    "`config router prefix-list`, `config router prefix-list6`, `config router route-map` (when there is a list that can be mapped), "
    "`config system dns`, `config system ntp`, and 11 firewall blocks "
    "(`config firewall policy` / `address` / `addrgrp` / `service custom` / `service group` / `vip` / `vip6` / "
    "`vipgrp` / `ippool` / `ippool6` / `central-snat-map`). "
    "The top-level `config system global` is read in the generic form for the heading and the other lines, and only the `set hostname` directly under it is mapped to `global.hostname`. "
    "The other top-level blocks (including `config firewall address6` and `config vdom`) are mapped "
    "in the generic form (as written in FortiOS) to `vendor_specific.fortinet.config` (ADR-0040). "
    "Top-level `#config-version=`, `#buildno=` and `#global_vdom=` lines that have no children (with a value that is not empty) are mapped "
    "as original text to `vendor_specific.fortinet.header` (`{}` if there is no such line. Other `#` lines are uninterpreted. ADR-0129 decision 1). "
    "scope is `top` (the heading of a top-level block), `fgt_header` (a top-level line without children), "
    "`system_interface` (an `edit` directly under `config system interface`), `interface` (inside that `edit`), "
    "`router_static` (an `edit` directly under `config router static`), `static_route` (inside that `edit`), "
    "`router_static6` (an `edit` directly under `config router static6`), `static_route6` (inside that `edit`), "
    "`router_bgp` (directly under `config router bgp`), `bgp_neighbor_edit` and `bgp_neighbor_group_edit` "
    "(an `edit` directly under `config neighbor` and `config neighbor-group`), `bgp_neighbor` (inside the `edit` of a peer), "
    "`bgp_option` (maps lines that are not in the BGP table to `options` by a path. The same table at every level), "
    "`router_ospf` (directly under `config router ospf`), `ospf_network_edit` (an `edit` directly under `config network`), "
    "`ospf_network` (inside that `edit`), `ospf_option` (maps lines that are not in the OSPF table to `options` by a path. The same table at every level), "
    "`ipsec_edit` (an `edit` directly under the four IPsec headings), "
    "`ipsec_option` (maps the lines inside that `edit` to `settings` by a path. The same table at every level), "
    "`sdwan_block` (a `config` directly under the two SD-WAN headings), `sdwan_table` (an `edit` directly under that `config`), "
    "`sdwan_option` (maps SD-WAN lines to `settings` by a path. The same table at every level), "
    "`prefix_list_edit`, `prefix_list6_edit` and `route_map_edit` (an `edit` directly under the headings of prefix-list, prefix-list6 and route-map. A list), "
    "`prefix_list`, `prefix_list6` and `route_map` (inside a list), `prefix_list_rule_edit`, "
    "`prefix_list6_rule_edit` and `route_map_rule_edit` (an `edit` directly under `config rule`), `prefix_list_rule`, "
    "`prefix_list6_rule` and `route_map_rule` (inside the `edit` of a rule), `route_map_option` (maps lines inside a route-map rule that are not in the table to "
    "`options` by a path. The same table at every level), "
    "`fgt_system_global` (tried first directly under the top-level `config system global`. hostname), "
    "`fgt_dns` (directly under `config system dns`. The four server fields), `dns_option` (maps the other DNS lines to `global.dns.options` "
    "by a path. The same table at every level), `ntp_option` (maps the lines of `config system ntp` to `global.ntp_options` by a path. "
    "The same table at every level), "
    "`fw_<name>_edit` (an `edit` directly under a firewall block), `fw_<name>` (inside that `edit`), "
    "`fgt_generic` (inside a level of the generic form), `fgt_generic_snmp` (tried first inside the `edit` of an SNMP community). "
    "The form is a regular expression for the whole line.",
    "Lines inside the `edit` of a block with a dedicated reader that do not match the table (including nested `config ipv6` / `config secondaryip` and so on) "
    "are mapped to `other` of that `edit` as lines of the generic form (the `fgt_generic` table) (ADR-0119 decision 1). "
    "`other` of an interface is a fixed key, and is `None` when there is no line to map. The `edit` of a firewall and the route details "
    "(`vendor_specific.fortinet.static_routes[]`) have an `other` key only when there are lines to map. Forms that do not match the table in the fields that the table has "
    "(the fields of the interface table, the fields of the firewall table and `uuid`, and `dst`, `gateway`, `device`, "
    "`distance` and `status` of a route) are not put in `other`, and enter the tree but are not mapped to the IR (uninterpreted. Decision 2). "
    "Lines that are neither `set` nor `unset <1 word>` are uninterpreted even in the generic form.",
    "A level of the generic form always has, even when empty, `set`, `unset`, `secrets`, `config`, `edits` and `order` for the config level, "
    "and `set`, `unset`, `secrets` and `config` for the edit level (including `other`). "
    "A secret field has no value, and only its name is put in `secrets`.",
    "A route is not mapped at all (all lines of that `edit` are uninterpreted) when the `edit` has even one nested `config`, a form of a field the table has that cannot be read, "
    "or a line that is neither `set` nor `unset <1 word>`. `set distance` and `set status` are read as `distance` and `enabled` of the route "
    "(decision 3). The normal output and the output of `show full-configuration` (which has lines of default values) give differences in `distance` and so on "
    "(default values are not guessed). An `edit` that has neither `set gateway` nor `set device` is mapped only when it has one of `set blackhole enable`, "
    "`set virtual-wan-link enable` and `set sdwan-zone` with a value (`next_hop` and `interface` are "
    "`None`. Those lines go to `other`. ADR-0129 decision 2). `config router static6` is read by the same procedure "
    "(the default destination is `::/0`), and `\"static6\": true` is put in the route details (decision 3).",
    "For a firewall, an `edit` is mapped even when there are lines that are not in the table (ADR-0037 decision 4). `unset <field>` of a field of the table makes that field "
    "a mark of \"explicitly not set\" (`{\"unset\": true}`) (ADR-0119 decision 4). "
    "`set uuid` is made interpreted, but the value is not kept (ADR-0037 decision 3). "
    "The keys of the `edit` of a policy are arranged in `order` in the order of appearance, and the keys of the `edit` of central NAT (`central-snat-map`. Matched from the top) are arranged in "
    "`central_snat_order` in the order of appearance (reordering appears in the differences. ADR-0139 decision 4). "
    "Even when a block is missing, the keys of the 11 tables and the 2 order lists are placed in an empty form.",
    "BGP (REQ-191. ADR-0139 decisions 2 and 4) merges overlapping `config router bgp` into one `bgp`. "
    "When the last `set as` directly under it is missing, `0`, or not a number (including asdot), every block stays in the generic form as before (`bgp` is `None`). "
    "When it is mapped, `router bgp` is not put in `vendor_specific.fortinet.config`. "
    "Lines that are not in the table are kept in `options` by a path (`network > 1 > prefix 192.0.2.0 255.255.255.0`, "
    "`redistribute connected > status enable`), and only the path is put for a `config` or `edit` none of whose children are mapped. "
    "Secret fields are judged the same as in the generic form, and at every level they have no value and only the field name is put "
    "(a secret line of a field the table has is also not left uninterpreted, and only the field name is put, so that the original text does not remain in the list of uninterpreted lines with its value). "
    "Forms that do not match the table in the fields the table has (`as` and `router-id` directly under it, and `remote-as`, `description`, `update-source`, `shutdown` and "
    "`password` inside a peer), and an `edit` directly under `config router bgp`, are uninterpreted. "
    "`config router bgp` inside `config vdom` stays in the generic form as before.",
    "OSPF (REQ-192. ADR-0139 decisions 2 and 4) merges overlapping `config router ospf` into one `ospf[\"0\"]` "
    "(`process_id` is `0` and `vrf` is `None`). When there are no `set`, `unset` or `edit` lines, including inside nested blocks "
    "(an empty block, or a block with only empty nested `config`, like the default output where OSPF is not configured), "
    "it stays in the generic form as before (`ospf` is `{}`). "
    "When it is mapped, `router ospf` is not put in `vendor_specific.fortinet.config`. "
    "`set router-id`, `set passive-interface` (a set), and `set prefix <address> <mask>` and "
    "`set area` of an `edit` of `config network` are read into `networks` (`wildcard` is the inverse of the mask, `area` is `0.0.0.0` if missing. The number of the `edit` is not kept). "
    "An `edit` that has no `prefix` or one that cannot be read (a form without a mask, or an inverse mask), an `area` that cannot be read, or an `unset` of `prefix` or `area` "
    "is not put in `networks`, and the whole `edit` is kept in `options` by a path (`network > <number> > …`). "
    "Lines that are not in the table are kept in `options` by a path, as in BGP. Secret lines have no value and only the field name is put "
    "(to the judgment of the generic form, it adds that any word of the value is `ENC`, `md5-keys` or `md5-key`, and the same key-word judgment as the Cisco OSPF). "
    "Forms that do not match the table for `router-id` and `passive-interface` directly under it, and an `edit` directly under `config router ospf`, are uninterpreted. "
    "`config router ospf` and `config router ospf6` inside `config vdom` stay in the generic form as before.",
    "IPsec (REQ-193. ADR-0139 decisions 2 and 4) makes an `ipsec.blocks[\"<kind> <name>\"]` (for example `phase1-interface vpn-a`) for each `edit` directly under the top-level "
    "`config vpn ipsec phase1`, `phase2`, `phase1-interface` and `phase2-interface` "
    "(the same form as Cisco and Arista. `options` is always empty). `edit`s with the same kind and the same name, including overlapping blocks, are merged into one, "
    "and a `phase1` and a `phase2` with the same name get different keys. When it is mapped, it is not put in `vendor_specific.fortinet.config`. "
    "The lines inside an `edit` are put in `settings` (a set. Ascending) by a path written the same way as BGP "
    "(for example `remote-gw 192.0.2.1`, `ipv4-exclude-range > 1 > start-ip 192.0.2.10`, `unset dpd-retryinterval`). "
    "A secret line puts neither the value nor the field name in `settings`, and sets `keys_configured` of that item to `True` "
    "(changes of the value and the number of secret lines are not looked at. The judgment adds to that of the generic form that any word of the value is `ENC` and the field name contains "
    "`secret`, `passwd` or `password`. `unset <field>` is not a secret). "
    "A nested level that has only secret lines gets only the path. For a block without an `edit`, only the heading is made interpreted and nothing is added "
    "(`ipsec` is `None` if there is no other `edit` to map). A line directly under a block that is not an `edit`, and a line inside an `edit` that is neither "
    "`set`, `unset <1 word>`, `config` nor `edit`, is uninterpreted. "
    "`vpn ipsec manualkey`, `concentrator` and so on, and the inside of `config vdom`, stay in the generic form as before.",
    "SD-WAN (REQ-194. ADR-0139 decisions 2 and 4) maps the top-level `config system sdwan` and `config system virtual-wan-link` to "
    "`sdwan` (the same form as Arista. `settings` only). With the word of the heading (`sdwan` or `virtual-wan-link`) as `<S>`, "
    "a `set` or `unset` directly under it goes to `settings` with key `<S>`, a line directly under `config <table>` that is not an `edit` goes to key `<S> <table>`, "
    "and a line inside `config <table>` > `edit <key>` goes to key `<S> <table> <key>` (for example `sdwan members 1` and `sdwan service 2`) "
    "(a set. Ascending), by a path written the same way as BGP (for example `status enable` and `sla > 1 > latency-threshold 100`). "
    "A key is made for every heading, table and `edit` that is mapped, even if it has no lines (`settings` is empty). "
    "Overlapping blocks, tables with the same name and `edit`s with the same key are merged into the same key. "
    "`config service` is matched from the top, so the keys of the `edit`s directly under it are put in the order of appearance in `settings` of key `<S> service-order` as "
    "one string like `order 2 1 3` (a change of order appears in the differences. Only the first appearance of the same key is used). "
    "For each heading, when there are no `set`, `unset` or `edit` lines, including inside nested blocks (an empty block, or a block with only empty nested `config`), "
    "that heading stays in the generic form as before. When it is mapped, it is not put in `vendor_specific.fortinet.config`. "
    "Secret fields are judged the same as in the generic form, and at every level they have no value and only the field name is put. "
    "An `edit` directly under the heading, and a line at any level that is neither `set`, `unset <1 word>`, `config` nor `edit`, are uninterpreted. "
    "The inside of `config vdom` stays in the generic form as before.",
    "prefix-list and route-map (REQ-195. ADR-0139 decisions 2 and 4, ADR-0047) map each `edit <name>` directly under the top-level `config router prefix-list`, "
    "`config router prefix-list6` and `config router route-map` as one list to "
    "`prefix_lists`, `ipv6_prefix_lists` and `route_maps` (the same form as Cisco and Arista). "
    "The `edit <number>` of `config rule` is `entries[<number>]` (`str(int(…))`). "
    "For a prefix-list, it reads `set comments` as `description`, and, in a rule, `set action permit|deny` (`permit` if missing), "
    "`set prefix <address> <mask>` (`set prefix6 <prefix>` for prefix-list6. The canonical notation), "
    "and `set ge|le <number>` and `unset ge|le` (`None`) (`eq` is always `None`). "
    "A list that has `set prefix any`, a form that cannot be read, a rule with a line that is not in the table, or a rule without `set prefix` "
    "is not mapped as a list so that no line is lost, and stays in the generic form as before. "
    "For a route-map, it reads `set action permit|deny` of a rule (`permit` if missing), `set match-<word> <value…>` (`<word> <value…>` into `match`), and "
    "`set set-<word> <value…>` (`<word> <value…>` into `set`), and puts the other lines, `unset` and nested blocks in "
    "`options` by a path written the same way as BGP (for a secret field only the field name. A form of `action` that cannot be read is uninterpreted). "
    "A list that has lines belonging to no rule, such as `set comments`, stays in the generic form. "
    "Whether to map is decided for each list, and for lists with the same name (including overlapping blocks), if even one cannot be mapped, none of them is mapped and they all stay in the generic form. "
    "A heading that has no list to map is read as the heading of the generic form as before. Even for a heading that is mapped, the lists that stay in the generic form and the lines directly under it that are not an "
    "`edit` are mapped to `vendor_specific.fortinet.config` (not put if there are none). "
    "The inside of `config vdom`, and `router access-list`, `aspath-list` and `community-list`, stay in the generic form as before.",
    "The global part (REQ-196. ADR-0139 decision 2) maps the `set hostname <value>` directly under the top-level `config system global` (one word with the quotation marks removed) to "
    "`global.hostname` (the same item as Cisco. The difference is `[changed] global / hostname`), and does not put it in `set` or `unset` of `system global` of the generic form "
    "(the other lines stay in the generic form as before. Forms that cannot be read, such as `''` and `unset hostname`, are uninterpreted). "
    "`set primary` and `set secondary` (IPv4) and `set ip6-primary` and `set ip6-secondary` (IPv6) of `config system dns` are put in "
    "`global.dns.servers` as `<canonical address> <field>` (for example `192.0.2.53 primary`), and the other lines in `global.dns.options` "
    "by a path (a form of the four fields that cannot be read is uninterpreted). All the lines of `config system ntp` are put in `global.ntp_options` by a path "
    "(for example `ntpserver > 1 > server 192.0.2.1`). Secret fields (judged the same as in the generic form. For NTP, a field whose name is `key` too) "
    "have no value and only the field name is put. DNS and NTP are not put in `vendor_specific.fortinet.config`. "
    "The inside of `config vdom` and `config global` stays in the generic form as before.\n",
)
_FLAT_MAP = i18n.choose(_FLAT_MAP_JA, _FLAT_MAP_EN)
_FORTINET_MAP = i18n.choose(_FORTINET_MAP_JA, _FORTINET_MAP_EN)

# 表の上の見出し・指紋の行・列の名前(日本語のときの出力は今と同じ)
_DOC_JA = {
    "flat_fingerprint": "表の指紋: `{digest}`({count} 件。行頭スコープ表 {scopes} 件 ＋ 写しの表 {patterns} 件)。"
    "保存した比較結果の `tools.patterns_fingerprint` と突き合わせる(docs/notes/req-14-storage.md)。\n",
    "scope_title": "## 行頭スコープ表\n",
    "scope_head": "| ID | platform | 書式(スコープ部分) | スコープ名 | 親 | 展開する添字 | 例 | 備考 |",
    "flat_map_title": "## IR への写し(interfaces・static_routes・bgp・vendor_specific の remotes・scopes・settings)\n",
    "fortinet_fingerprint": "表の指紋: `{digest}`({count} 件。写しの表 {patterns} 件。木の組み立ての版 `{tree_version}` を含む)。"
    "保存した比較結果の `tools.patterns_fingerprint` と突き合わせる(docs/notes/req-14-storage.md)。\n",
    "fortinet_map_title": "## IR への写し(interfaces・static_routes・bgp・ospf・ipsec・sdwan・route_maps・prefix_lists・ipv6_prefix_lists・"
    "global の hostname・dns・ntp_options・vendor_specific の firewall・config・header)\n",
}
_DOC_EN = {
    "flat_fingerprint": "Table fingerprint: `{digest}` ({count} rows. {scopes} line-head scope rows + {patterns} mapping rows). "
    "Compare it with `tools.patterns_fingerprint` of a saved comparison result (docs/notes/req-14-storage.md).\n",
    "scope_title": "## Line-head scope table\n",
    "scope_head": "| ID | platform | Form (scope part) | Scope name | Parent | Expanded index | Example | Note |",
    "flat_map_title": "## Mapping to the IR (interfaces, static_routes, bgp, and remotes, scopes and settings of vendor_specific)\n",
    "fortinet_fingerprint": "Table fingerprint: `{digest}` ({count} rows. {patterns} mapping rows. Includes the tree building version `{tree_version}`). "
    "Compare it with `tools.patterns_fingerprint` of a saved comparison result (docs/notes/req-14-storage.md).\n",
    "fortinet_map_title": "## Mapping to the IR (interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, "
    "hostname, dns and ntp_options of global, and firewall, config and header of vendor_specific)\n",
}
_DOC = i18n.choose(_DOC_JA, _DOC_EN)


def _scope_record(scope: FlatScope) -> dict[str, Any]:
    return {
        "id": scope.id,
        "platform": scope.platform,
        "match": scope.match,
        "name": scope.name,
        "parent": scope.parent,
        "expand": list(scope.expand),
        "example": scope.example,
        "note": scope.note,
    }


def render_json(platform: str = "fujitsu_sir") -> str:
    flat = FLAT_PLATFORMS[platform]
    fp = patterns_fingerprint(platform)
    payload = {
        "platform": platform,
        "fingerprint": fp.digest,
        "count": fp.count,
        "scopes": [_scope_record(s) for s in flat.scopes],
        "patterns": [_record(p) for p in flat.patterns],
    }
    if flat.tree_version is not None:  # D型だけ(B型の出力は変えない)
        payload["tree_version"] = flat.tree_version
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def render_markdown(platform: str = "fujitsu_sir") -> str:
    flat = FLAT_PLATFORMS[platform]
    if flat.build is not None:
        return _render_built(platform)
    fp = patterns_fingerprint(platform)
    out = [
        _HEADER.format(platform=platform),
        _DOC["flat_fingerprint"].format(
            digest=fp.digest, count=fp.count, scopes=len(flat.scopes), patterns=len(flat.patterns)
        ),
        _DOC["scope_title"],
        _DOC["scope_head"],
        "|---|---|---|---|---|---|---|---|",
    ]
    for s in flat.scopes:
        expand = ", ".join(f"`{name}`" for name in s.expand)
        out.append(
            f"| `{s.id}` | {s.platform} | {_code(s.match)} | {_code(s.name)} | {s.parent or ''} | {expand} "
            f"| {_code(s.example)} | {_cell(s.note)} |"
        )
    out += [
        "",
        _DOC["flat_map_title"],
        *_FLAT_MAP,
        _PATTERN_DOC["table_head"],
        "|---|---|---|---|---|---|",
    ]
    for p in flat.patterns:
        match = _code(p.match) if p.match is not None else _PATTERN_DOC["no_line"]
        out.append(f"| `{p.id}` | {_cell(p.scope)} | {match} | {_cell(p.target)} | {_code(p.example)} | {_cell(p.note)} |")
    return "\n".join(out).rstrip("\n") + "\n"


def _render_built(platform: str) -> str:
    """行頭スコープ表を持たない機種(D型)の一覧。木の組み立ての規則と写しの表。"""
    flat = FLAT_PLATFORMS[platform]
    fp = patterns_fingerprint(platform)
    out = [
        _FORTINET_HEADER,
        _DOC["fortinet_fingerprint"].format(
            digest=fp.digest, count=fp.count, patterns=len(flat.patterns), tree_version=flat.tree_version
        ),
        _DOC["fortinet_map_title"],
        *_FORTINET_MAP,
        _PATTERN_DOC["table_head"],
        "|---|---|---|---|---|---|",
    ]
    for p in flat.patterns:
        match = _code(p.match) if p.match is not None else _PATTERN_DOC["no_line"]
        out.append(f"| `{p.id}` | {_cell(p.scope)} | {match} | {_cell(p.target)} | {_code(p.example)} | {_cell(p.note)} |")
    return "\n".join(out).rstrip("\n") + "\n"
