"""平坦 config(B型)の表から対応一覧(Markdown / JSON)を生成する(ADR-0024、REQ-21)。

parser-patterns.md(pattern_docs.py)と同じ運用。docs/notes/flat-patterns.md はこの出力そのもので、ずれたらテストが失敗する。
内容は、行頭スコープ表(FLAT-NN)・IR への写しの表・指紋の3つ。
FortiGate(D型。REQ-22)は行頭スコープ表を持たないため、木の組み立ての規則・写しの表・指紋を出す(docs/notes/fortinet-patterns.md)。
ヤマハ RTX(yamaha。REQ-300)も同じ形で出す(docs/notes/yamaha-patterns.md。見出しと木の組み立ての規則の要約はヤマハのもの)。
Juniper Junos(juniper_junos。REQ-321・REQ-322・REQ-324)も同じ形で出す(docs/notes/junos-patterns.md。形の見分け・2 段の文の木の規則の要約は
この機種のもの)。
Palo Alto PAN-OS(paloalto_panos。REQ-327・REQ-329)も同じ形で出す(docs/notes/panos-patterns.md。XML・set・brace の見分けと 2 段の文の木の規則の要約は
この機種のもの)。
Check Point Gaia(checkpoint_gaia。REQ-344)は、木の組み立ての関数(build)と行頭スコープ表の両方を持つ。行頭スコープ表・指紋の行(スコープ表の件数も出す)・
写しの表を出す(docs/notes/gaia-patterns.md)。
F5 BIG-IP(f5_bigip。REQ-349)は、行頭スコープ表を持たず、PAN-OS と同じ形で出す(docs/notes/f5-patterns.md。F5 用の木の規則の要約はこの機種のもの)。
"""

import json
from typing import Any

from driftpilot import i18n
from driftpilot.config_parser.flat_scopes import FlatScope
from driftpilot.config_parser.brace_tree import TREE_VERSION as BRACE_TREE_VERSION
from driftpilot.config_parser.f5_tree import TREE_VERSION as F5_TREE_VERSION
from driftpilot.config_parser.fortinet_tree import EMBEDDED, HASH_INPUT_VERSION
from driftpilot.config_parser.gaia import TREE_VERSION as GAIA_TREE_VERSION
from driftpilot.config_parser.junos_set_tree import TREE_VERSION as SET_TREE_VERSION
from driftpilot.config_parser.junos_tree import TREE_VERSION as JUNOS_TREE_VERSION
from driftpilot.config_parser.panos_tree import TREE_VERSION as PANOS_TREE_VERSION
from driftpilot.config_parser.pattern_docs import _DOC as _PATTERN_DOC
from driftpilot.config_parser.pattern_docs import _cell, _code, _record
from driftpilot.config_parser.platforms import FLAT_PLATFORMS, patterns_fingerprint

GENERATE_COMMAND = "driftpilot patterns --platform fujitsu_sir --output docs/notes/flat-patterns.md"
FORTINET_GENERATE_COMMAND = "driftpilot patterns --platform fortinet --output docs/notes/fortinet-patterns.md"
YAMAHA_GENERATE_COMMAND = "driftpilot patterns --platform yamaha --output docs/notes/yamaha-patterns.md"
JUNOS_GENERATE_COMMAND = "driftpilot patterns --platform juniper_junos --output docs/notes/junos-patterns.md"
PANOS_GENERATE_COMMAND = "driftpilot patterns --platform paloalto_panos --output docs/notes/panos-patterns.md"
GAIA_GENERATE_COMMAND = "driftpilot patterns --platform checkpoint_gaia --output docs/notes/gaia-patterns.md"
F5_GENERATE_COMMAND = "driftpilot patterns --platform f5_bigip --output docs/notes/f5-patterns.md"

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

_YAMAHA_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(ヤマハ RTX: yamaha)

> **自動生成。手で編集しない。** 写しの表(`src/driftpilot/config_parser/yamaha.py` の `PATTERNS`)と
> 木の組み立ての版(`src/driftpilot/config_parser/yamaha_tree.py` の `TREE_VERSION`)から作る。
> 再生成: `{YAMAHA_GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

ヤマハ RTX(NVR・FWX を含む)の config(ADR-0213)は平らな 1 行のコマンドの並びで、`pp select <番号>`・`tunnel select <番号>` の後の行が
その PPP の相手・トンネルの設定になる。**字下げだけでは範囲を決めない。** hier_config も使わない。
行は空白を 1 つにまとめた文字列で見る。字下げの有無は元の行の頭の字(空白かタブ)で決める。

- 空の行と、行が `#` で始まる行(注釈)は木に入れない(行番号の数え方は変えない)
- 範囲を選ぶ行は、行全体が `pp select <数字>`・`pp select anonymous`・`pp select none`・`tunnel select <数字>`・`tunnel select none` の行だけ
  (字下げは問わない)。後ろにほかの語がある行は範囲を選ぶ行にしない
- `<数字>`・`anonymous` は範囲 `pp <数字>`・`pp anonymous`・`tunnel <数字>` を今の範囲にする。範囲のノードは最上位に、初めて選んだ行で作る。
  同じ範囲をもう一度選んだときは同じノードを使い、選び直しの行は範囲の子に出現順で置く(重複の警告にはしない)。`none` は今の範囲を無くす(行は木に入れない)
- ほかの行は、今の範囲があり、(a) 字下げがあって直前の行が範囲の子か範囲を選ぶ行だった、または (b) 行の頭が文脈の語
  (`pp `・`tunnel `・`ppp `・`pppoe `・`ip pp `・`ipv6 pp `・`ip tunnel `・`ipv6 tunnel `・`ipsec tunnel `・`ipsec sa policy `・`ipsec ike `・`l2tp `。
  `pp select `・`tunnel select `・`l2tp service ` を除く)なら範囲の子、そうでなければ最上位の行にする。範囲の子は 1 段だけ(字下げの深さは木にしない)
- 同じ範囲(最上位を含む)の子のうち、テキスト(空白を 1 つにまとめた行)が同じ行が 2 つ以上あると、警告(`duplicate_line`)を出す。
  どちらの行も捨てない。範囲を選ぶ行の選び直しは警告しない
"""

_YAMAHA_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (Yamaha RTX: yamaha)

> **Generated automatically. Do not edit by hand.** It is made from the mapping table (`PATTERNS` of `src/driftpilot/config_parser/yamaha.py`) and
> the version of the tree building (`TREE_VERSION` of `src/driftpilot/config_parser/yamaha_tree.py`).
> Regenerate: `{YAMAHA_GENERATE_COMMAND}`
> If the tables are changed and this file is out of date, a test fails.

The Yamaha RTX config (including NVR and FWX. ADR-0213) is a flat sequence of one-line commands, and the lines after `pp select <number>` or `tunnel select <number>` are the settings of
that PPP peer or tunnel. **Indentation alone does not decide a scope.** hier_config is not used either.
A line is looked at as a string with whitespace squeezed to one space. Whether it is indented is decided by the first character of the original line (a space or a tab).

- Empty lines and lines that start with `#` (comments) are not put in the tree (the way line numbers are counted does not change)
- A scope-selecting line is only a line that is wholly `pp select <digits>`, `pp select anonymous`, `pp select none`, `tunnel select <digits>` or `tunnel select none`
  (indentation does not matter). A line with other words after it is not a scope-selecting line
- `<digits>` and `anonymous` make the scope `pp <digits>`, `pp anonymous` or `tunnel <digits>` the current scope. The scope node is made at the top level by the line that first selects it.
  When the same scope is selected again, the same node is used, and the selecting line is put as a child of the scope in order of appearance (it is not a duplicate warning). `none` clears the current scope (the line is not put in the tree)
- Any other line is a child of the scope if there is a current scope and either (a) it is indented and the previous line was a child of the scope or a scope-selecting line, or (b) the line head is a context word
  (`pp `, `tunnel `, `ppp `, `pppoe `, `ip pp `, `ipv6 pp `, `ip tunnel `, `ipv6 tunnel `, `ipsec tunnel `, `ipsec sa policy `, `ipsec ike `, `l2tp `.
  Except `pp select `, `tunnel select ` and `l2tp service `). Otherwise it is a top-level line. A child of a scope is one level only (the depth of indentation is not put in the tree)
- When two or more children of the same scope (including the top level) have the same text (the line with whitespace squeezed to one space), a warning (`duplicate_line`) is given.
  Neither line is dropped. Selecting the same scope again is not warned
- The notes and the targets in the table rows are developer notes and are shown in Japanese
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
        "`config vdom`・`config global` の中は今のまま汎用の形。"
        "FortiSwitch(REQ-299。ADR-0212 決定2〜9)は、最上位の `config switch vlan` を `vlans` に、`config switch interface`・"
        "`config switch trunk`・`config switch physical-port` を `interfaces`(`switchport`・`type`・`shutdown`・`port_options`)に"
        "写し、`vendor_specific.fortinet.config` には入れない(`config system interface` を先に全部読んでから写す)。"
        "scope は `fsw_vlan_edit`(`config switch vlan` の直下の `edit`)、`fsw_vlan_option`(その `edit` の中の行を `options` に"
        "道すじで写す)、`fsw_port_edit`(3つのブロックの直下の `edit`)、`fsw_interface`(`switch interface` の `edit` の中の表の欄)、"
        "`fsw_physical_port`(`switch physical-port` の `edit` の中の `set status`)、`fsw_option`(ほかの行を `port_options` に"
        "道すじで写す)。ほかの `config switch …` と `config switch-controller …` は今のまま汎用の形。\n",
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
_YAMAHA_HEADER = i18n.choose(_YAMAHA_HEADER_JA, _YAMAHA_HEADER_EN)

_JUNOS_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(Juniper Junos: juniper_junos)

> **自動生成。手で編集しない。** 写しの表(`src/driftpilot/config_parser/junos.py` の `PATTERNS`)と
> 木の組み立ての版(`src/driftpilot/config_parser/junos_tree.py` の `TREE_VERSION`)から作る。
> 再生成: `{JUNOS_GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

Junos の config(ADR-0224)は、同じ設定が 2 つの形で出る。`show configuration` の形(brace。`{{ }}` の対と `;`)と、
`show configuration | display set` の形(set。1 行が根からの経路)。**どちらも同じ 2 段の文の木に組む(ADR-0224 の 2026-11-17 の追記。REQ-322)。**
字下げは見ない。hier_config も使わない。set の語の列を区切りの表でノードに分けることはしない(表に無い経路で brace と set の木が食い違うため)。

- 木: 1 段目は文の最初の語(最上位の語。`system`・`interfaces` など。暗黙のノードで、同じ語は 1 つ)。2 段目は、最上位の語を除いた
  根からの残りの語を空白 1 つでつないだ文(`login class ops permissions all`)。引用符で囲んだ語は 1 語のまま、引用符も外さない。
  行番号は元の行(brace は葉の行、set はその行)。最上位の語だけで残りの語が無い行(brace の `foo;`、`set foo`)は、1 段目のノードが実在の行になる
- 形の見分け: 空行・`#` で始まる行・注釈 `/* … */`・引用符の外の ` ## …` を除いて見る。`{{` で終わる行だけがあれば brace、
  `set `・`deactivate ` で始まる行だけがあれば set。両方あれば構造のエラー(`mixed`。文に行番号だけを入れる)。どちらも無ければ set として読む
  (全部が最上位の行になり、未解釈に残る)。木の組み立ての版 `{JUNOS_TREE_VERSION}` は、brace の木の版 `{BRACE_TREE_VERSION}`・
  set の行の読み方の版 `{SET_TREE_VERSION}`・この見分けと木の作り方の規則をまとめた版で、表の指紋に入るのはこの 1 つだけ(どれかを変えたら上げる)
- brace: `{{ }}` の対で入れ子の木を作り(`[ a b ]` は要素ごとの葉に展開。`replace: ` は外す。注釈・`#` で始まる行・行末の ` # …`・`replace:` だけの行は木に入れない。
  `{{` だけの行は直前の `;` などで終わらない行の見出しにし、`;` の無い行は `;` を書き忘れた葉として読む。1 行に 2 つ以上の文がある行は止める)、
  根から葉までの見出しと葉のテキストをつないで 1 つの文にする(子の無い見出し `foo {{ }}` は、その見出しまでの経路が 1 つの文)。
  `delete: `・`insert ` で始まる見出しか葉は外さず、その下の文も含めてテキストのまま文に入れ、未解釈に残す
  (最上位の要素なら文に分けず、最上位の行として未解釈に残す)
- set: `set ` の後ろを語に分け(引用符の中は 1 語)、最初の語が最上位の語、残りをつないだものが文。`protect`・`annotate` は木に入れない。
  `set`・`deactivate`・`protect`・`annotate` のどれでも始まらない行は最上位の行(未解釈)
- 無効の印: brace の `inactive: ` の付いた見出しか葉、set の `deactivate <経路>` は、`deactivate <最上位の語を除いた残りの経路>` の文を足す
  (経路が最上位の語だけなら `deactivate`)。brace の `inactive: ` の付いた要素の中の文は、`inactive: ` を外した経路で普通の文としても置く。
  `deactivate` の経路が木に無くてもエラーにしない
- 引用符(`"`)の中は 1 語。`\\"` では閉じない。閉じなければ構造のエラー(`quote`)。構造のエラーの文には行番号だけを入れ、行の中身は入れない
- 同じ最上位の語の下に同じ文が 2 回以上あると、警告(`duplicate_line`)を出す。どちらの行も捨てない
- `groups` の継承の展開はしない(`groups` は普通の語、`apply-groups` は文の一部)
"""

_JUNOS_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (Juniper Junos: juniper_junos)

> **Generated automatically. Do not edit by hand.** It is made from the mapping table (`PATTERNS` of `src/driftpilot/config_parser/junos.py`) and
> the version of the tree building (`TREE_VERSION` of `src/driftpilot/config_parser/junos_tree.py`).
> Regenerate: `{JUNOS_GENERATE_COMMAND}`
> If the tables are changed and this file is out of date, a test fails.

A Junos config (ADR-0224) comes in two forms for the same settings: the `show configuration` form (brace: `{{ }}` pairs and `;`) and the
`show configuration | display set` form (set: one line is a path from the root). **Both are built into the same two-level statement tree (the 2026-11-17 addition to ADR-0224. REQ-322).**
Indentation is not looked at. hier_config is not used either. The words of a set line are not cut into nodes by a cutting table (a path not in the table made the brace tree and the set tree differ).

- Tree: the first level is the first word of a statement (the top word: `system`, `interfaces` and so on; an implicit node, one for each word). The second level is the statement:
  the remaining words from the root, joined by one space (`login class ops permissions all`). A quoted word stays one word and keeps its quotation marks.
  The line number is the original line (the leaf's line for brace, the line itself for set). A line of only the top word (brace `foo;`, `set foo`) makes the first-level node a real line
- Telling the form: empty lines, lines that start with `#`, comments `/* ... */` and ` ## ...` outside quotation marks are left out. If there are lines ending with `{{` only, it is brace;
  if there are lines starting with `set ` or `deactivate ` only, it is set. If both, it is a structure error (`mixed`; the message holds only the line number). If neither, it is read as set
  (every line becomes a top-level line and stays uninterpreted). The tree building version `{JUNOS_TREE_VERSION}` puts together the brace tree version `{BRACE_TREE_VERSION}`,
  the set line reading version `{SET_TREE_VERSION}` and the rules of this telling and of building the tree; only this one goes into the table fingerprint (raise it when any of them changes)
- Brace: a nested tree is built from the `{{ }}` pairs (`[ a b ]` is expanded to one leaf for each element; `replace: ` is removed; comments, lines starting with `#`, ` # ...` at the end of a line, and lines holding only `replace:` are not put in the tree;
  a line holding only `{{` makes the line before it, which does not end with `;` and so on, its heading; a line without `;` is read as a leaf whose `;` was forgotten; a line holding two or more statements stops the reading),
  and the texts of the headings and the leaf from the root to the leaf are joined into one statement (a heading without children, `foo {{ }}`, makes the path up to that heading one statement).
  A heading or a leaf that starts with `delete: ` or `insert ` is not removed; it goes into a statement as text, together with the statements below it, and stays uninterpreted
  (when it is a top-level element, it is not split into statements and stays uninterpreted as a top-level line)
- Set: the words after `set ` are split (a quoted part is one word); the first word is the top word and the rest joined is the statement. `protect` and `annotate` are not put in the tree.
  A line that starts with none of `set`, `deactivate`, `protect` and `annotate` is a top-level line (uninterpreted)
- Inactive mark: a brace heading or leaf with `inactive: `, and a set `deactivate <path>`, add a statement `deactivate <the path without the top word>`
  (`deactivate` when the path is only the top word). A statement inside an element with `inactive: ` in brace is also put as an ordinary statement with the path without `inactive: `.
  A `deactivate` whose path is not in the tree is not an error
- A quoted part (`"`) is one word. `\\"` does not close it. If it is not closed, it is a structure error (`quote`). The message of a structure error holds only the line number and never the content of the line
- When the same statement appears two or more times under the same top word, a warning (`duplicate_line`) is given. Neither line is dropped
- Groups are not expanded by inheritance (`groups` is an ordinary word and `apply-groups` is a part of a statement)
- The notes and the targets in the table rows are developer notes and are shown in Japanese
"""

_JUNOS_HEADER = i18n.choose(_JUNOS_HEADER_JA, _JUNOS_HEADER_EN)

_PANOS_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(Palo Alto PAN-OS: paloalto_panos)

> **自動生成。手で編集しない。** 写しの表(`src/driftpilot/config_parser/panos.py` の `PATTERNS`)と
> 木の組み立ての版(`src/driftpilot/config_parser/panos_tree.py` の `TREE_VERSION`)から作る。
> 再生成: `{PANOS_GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

PAN-OS の config(ADR-0226)は、同じ設定が 3 つの形で出る。XML(`show config running`・API・保存した XML。1 行の XML もある)、
set(`set cli config-output-format set` の後の `show`。1 行が根からの経路)、brace(設定モードの `show`。`{{ }}` の対と `;`)。
**どの形も同じ 2 段の文の木に組む(Junos と同じ作り)。** 字下げは見ない。hier_config も使わない。区切りの表でノードに分けることはしない。

- 木: 1 段目は文の最初の語(最上位の語。`deviceconfig`・`network`・`rulebase`・`shared`・`mgt-config`・`address`・`vsys` など。暗黙のノードで、同じ語は 1 つ)。
  2 段目は、最上位の語を除いた根からの残りの語を空白 1 つでつないだ文(`security rules R1 action allow`)。引用符で囲んだ語は 1 語のまま、引用符も外さない。
  最上位の語だけで残りの語が無い行(set の `set foo`、brace の `foo;`、XML の `<foo/>`)は、1 段目のノードが実在の行になる
- 形の見分け: 空行・空白だけの行を除いた最初の文字が `<` なら XML。そうでなければ、空行と `#` で始まる行を除き(引用符の中で始まる行は見ない)、`{{` で終わる行だけがあれば brace、
  `set ` で始まる行だけがあれば set。XML と set・brace が混ざる、set と brace が混ざる、は構造のエラー(`mixed`。文に行番号だけを入れる)。
  どれでもなければ set として読む(全部が最上位の行になり、未解釈に残る)。木の組み立ての版 `{PANOS_TREE_VERSION}` は、brace の木の版 `{BRACE_TREE_VERSION}`・
  この見分けと XML・set の読み方と木の作り方の規則をまとめた版で、表の指紋に入るのはこの 1 つだけ(どれかを変えたら上げる)
- XML(自前の走査。標準ライブラリの XML の解析器は使わない): 開き・閉じ・空のタグ・文字・`<?…?>`・コメント・`<![CDATA[…]]>` を読む。
  `<!DOCTYPE`・`<!ENTITY` は構造のエラー(`dtd`)。閉じのタグの食い違い・閉じていないタグ・壊れたタグ・知らない文字の参照は構造のエラー(`xml`。推測で閉じない)。
  文字の参照(`&amp;`・`&lt;`・`&gt;`・`&quot;`・`&apos;`・`&#数;`・`&#x16進;`)は戻す
  - 根の要素(`config`)は外す(属性も文にしない)。`devices` の下の `entry name="localhost.localdomain"` の段は `devices` の語とともに外す(テンプレートの中でも。set の形と同じ)
  - `vsys` の下の `entry name="vsys1"` の段は、根の `config` の直下の `devices` > `entry localhost.localdomain` の下の `vsys` が `vsys1` だけのときだけ、`vsys vsys1` の 2 語を外す。
    `vsys2` などほかの `entry` が 1 つでもあれば外さない(`vsys vsys1 …`・`vsys vsys2 …`)。テンプレートの中・`device-group` の下の `vsys` は外さない。空の `<entry name="vsys1"/>` は `vsys vsys1` のまま
  - set・brace の文も同じ決まりでそろえる。最上位の語が `vsys` の文の 2 語目がどれも `vsys1` のとき、`vsys vsys1 ` の頭を外して残りの語で文を作る(最上位の語は残りの最初の語)。
    `vsys1` 以外の名前が 1 つでもあれば、どの文も変えない。最上位の語が `template` の文は変えない
  - `entry` は `name` の値が経路の語(`entry` の語は入れない)。`member` は値が親の経路の後ろに付く 1 つずつの文。
    子を持たず値を持つ要素は「経路 + 要素の名前 + 値」、子も値も無い要素(`<tcp/>`)は「経路 + 要素の名前」で文を終える
  - `name` 以外の属性(`uuid` など)は文にしない。値の前後の空白は除き、空白か `"` があれば `"` で囲む(中の `"` は `\\"`。set の形と同じ)
  - 複数行の値(PEM など)は、中身を文に入れず、行数と SHA-256 の 16 進の頭 16 字だけの 1 語(日本語の固定の語。`panos_xml.py` の `MULTILINE_WORD`。英語で動かしても同じ文字)にする。
    行数は前後の空白を除いた値を CRLF・CR・LF で分けた数、SHA-256 はその行を LF でつないだ UTF-8 のもの(保存し直して改行が変わっても同じ値)。中身が変われば差分に出る
- set: 元の行を 1 字ずつ読み、`set ` の後ろを語に分け(引用符の中は 1 語。`\\"` では閉じない)、最初の語が最上位の語、残りをつないだものが文。文の行番号は `set ` の行
  - 引用符が行の終わりまでに閉じなければ、次の行から閉じの引用符のある行まで続けて 1 つの値として読む(続きの行の空行・`#` の行・字下げも値に入る)。閉じの引用符の後ろの語は同じ文の続き。
    改行を含む値は、XML の複数行の値と同じ 1 語にする(中身は文に入れない)。ファイルの終わりまで閉じなければ構造のエラー `quote`(開いた行)
  - `[ a b ]` は 1 つずつの文に展開する(書かれた順)。語の頭にくっついた `[`(`[a`)と、語の終わりにくっついた `]`(`b]`)も受ける。引用符の中の `[`・`]` は区切りにしない。
    リストが行の終わりまでに閉じなければ、何も挟まずに次の行へ続ける(端末の折り返しを戻す。次の行の頭の空白は区切りになる)。続きの行が `set ` で始まれば `]` の書き忘れとして構造のエラー。
    ファイルの終わりまで閉じなければ構造のエラー `list`(開いた行)
  - `[` は 1 つの文に 1 組。中身が空・前の語が無い・2 組目・`]` の後ろの語・入れ子・対の無いものは構造のエラー(`list`。推測で展開しない)
  - `set ` で始まらない行(空行・`#` で始まる行は飛ばす)は最上位の行(未解釈)
- brace: `{{ }}` の対で入れ子の木を作り(引用符が行の終わりまでに閉じなければ、set と同じに次の行から閉じの引用符まで続けて 1 つの値として読み、閉じの後ろの `;` で葉を終える。
  改行を含む値は set と同じ 1 語。`[ a b ]` は要素ごとの葉に展開。注釈・`#` で始まる行は木に入れない。1 行に 2 つ以上の文がある行は止める)、
  根から葉までの見出しと葉のテキストをつないで 1 つの文にする(子の無い見出し `foo {{ }}` は、その見出しまでの経路が 1 つの文)。
  Junos の `inactive: `・`delete: `・`insert ` のような印は特別に扱わない(文のテキストのまま入る)。brace の構造のエラーは brace の木のエラーのまま出る
- 同じ最上位の語の下に同じ文が 2 回以上あると、警告(`duplicate_line`)を出す。どちらの行も捨てない
- 表の行の備考と写し先は開発者向けのメモで、日本語のまま出す
"""

_PANOS_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (Palo Alto PAN-OS: paloalto_panos)

> **Generated automatically. Do not edit by hand.** It is made from the mapping table (`PATTERNS` of `src/driftpilot/config_parser/panos.py`) and
> the version of the tree building (`TREE_VERSION` of `src/driftpilot/config_parser/panos_tree.py`).
> Regenerate: `{PANOS_GENERATE_COMMAND}`
> If the table is changed but this file is not, the test fails.

A PAN-OS config (ADR-0226) appears in three forms. XML (`show config running`, the API, a saved XML; a one-line XML also exists),
set (`show` after `set cli config-output-format set`; one line is a path from the root), and brace (`show` in configuration mode; `{{ }}` pairs and `;`).
**Every form is built into the same two-level tree of statements (the same structure as Junos).** Indentation is not looked at. hier_config is not used either. Nodes are not split by a separator table.

- Tree: the first level is the first word of a statement (the top word: `deviceconfig`, `network`, `rulebase`, `shared`, `mgt-config`, `address`, `vsys` and so on; an implicit node, one for each distinct word).
  The second level is a statement: the remaining words from the root, without the top word, joined by one space (`security rules R1 action allow`). A quoted word stays one word and its quotation marks are not removed.
  For a line with only a top word and no remaining words (`set foo` in set, `foo;` in brace, `<foo/>` in XML), the first-level node becomes a real line
- Telling the form: if the first character, after blank lines and lines holding only spaces, is `<`, it is XML. Otherwise, blank lines and lines starting with `#` are left out (a line that starts inside quotation marks is not looked at), and if there are only lines ending with `{{` it is brace,
  and if there are only lines starting with `set ` it is set. XML mixed with set or brace, or set mixed with brace, is a structure error (`mixed`; the message holds only the line number).
  If it is none of them, it is read as set (every line becomes a top-level line and stays uninterpreted). The tree building version `{PANOS_TREE_VERSION}` puts together the brace tree version `{BRACE_TREE_VERSION}` and
  the rules of this telling, of reading XML and set, and of building the tree; only this one goes into the table fingerprint (raise it when any of them changes)
- XML (an own scan; the XML parsers of the standard library are not used): opening, closing and empty tags, text, `<?...?>`, comments and `<![CDATA[...]]>` are read.
  `<!DOCTYPE` and `<!ENTITY` are a structure error (`dtd`). A closing tag that does not match, a tag left open, a broken tag and an unknown character reference are a structure error (`xml`; nothing is closed by guessing).
  Character references (`&amp;`, `&lt;`, `&gt;`, `&quot;`, `&apos;`, `&#number;`, `&#xhex;`) are turned back
  - The root element (`config`) is removed (its attributes do not become statements). The level of `entry name="localhost.localdomain"` under `devices` is removed together with the word `devices` (also inside a template; the same as the set form)
  - The level of `entry name="vsys1"` under `vsys` is removed (the two words `vsys vsys1`) only when the `vsys` under `devices` > `entry localhost.localdomain` directly under the root `config` holds `vsys1` alone.
    If there is even one other `entry` (such as `vsys2`), nothing is removed (`vsys vsys1 ...`, `vsys vsys2 ...`). A `vsys` inside a template or under `device-group` is not removed. An empty `<entry name="vsys1"/>` stays `vsys vsys1`
  - Statements of set and brace follow the same rule. When the second word of every statement whose top word is `vsys` is `vsys1`, the head `vsys vsys1 ` is removed and the statement is rebuilt from the remaining words (the top word is the first remaining word).
    If even one other name than `vsys1` exists, no statement is changed. A statement whose top word is `template` is not changed
  - For `entry`, the value of `name` is the word of the path (the word `entry` is not put in). For `member`, the value is a statement of its own after the path of the parent.
    An element without children but with a value is "path + element name + value", and an element with neither children nor a value (`<tcp/>`) ends a statement with "path + element name"
  - Attributes other than `name` (such as `uuid`) do not become statements. Spaces around a value are removed, and a value with a space or `"` is enclosed in `"` (a `"` inside is `\\"`; the same as the set form)
  - A multi-line value (such as a PEM) does not go into a statement; it becomes one word that holds only the line count and the first 16 hex digits of the SHA-256 (a fixed Japanese word, `MULTILINE_WORD` of `panos_xml.py`; the same text when run in English).
    The line count is the number of lines when the value without the surrounding spaces is split by CRLF, CR or LF, and the SHA-256 is of those lines joined by LF in UTF-8 (the same value even if the newlines change when the file is saved again). A change in the content shows as a difference
- Set: the lines are read character by character as written, the words after `set ` are split (a quoted part is one word; `\\"` does not close it), the first word is the top word and the rest joined is the statement. The line number of a statement is that of the `set ` line
  - If a quotation mark is not closed by the end of the line, the reading continues from the next line to the line with the closing quotation mark as one value (blank lines, lines starting with `#` and indentation in the continued lines belong to the value). The words after the closing quotation mark continue the same statement.
    A value with a newline becomes the same one word as a multi-line value of XML (the content does not go into a statement). If it is not closed by the end of the file, it is a structure error `quote` (the line where it was opened)
  - `[ a b ]` is expanded to one statement for each element (in the written order). A `[` stuck to the head of a word (`[a`) and a `]` stuck to the end of a word (`b]`) are accepted too. A `[` or `]` inside quotation marks is not a delimiter.
    If a list is not closed by the end of the line, the reading continues on the next line with nothing put between (this undoes the wrapping of a terminal; the spaces at the head of the next line are separators). If the continued line starts with `set `, it is a structure error as a forgotten `]`.
    If it is not closed by the end of the file, it is a structure error `list` (the line where it was opened)
  - `[` is one pair for a statement. An empty list, no word before it, a second pair, a word after `]`, nesting and an unpaired one are a structure error (`list`; nothing is expanded by guessing)
  - A line that does not start with `set ` (blank lines and lines starting with `#` are skipped) is a top-level line (uninterpreted)
- Brace: a nested tree is built from the `{{ }}` pairs (if a quotation mark is not closed by the end of the line, the reading continues from the next line to the closing quotation mark as one value, the same as set, and a `;` after the closing one ends the leaf.
  A value with a newline becomes the same one word as in set. `[ a b ]` is expanded to one leaf for each element; comments and lines starting with `#` are not put in the tree; a line holding two or more statements stops the reading),
  and the texts of the headings and the leaf from the root to the leaf are joined into one statement (a heading without children, `foo {{ }}`, makes the path up to that heading one statement).
  The marks of Junos such as `inactive: `, `delete: ` and `insert ` are not treated specially (they go in as the text of a statement). A structure error of brace comes out as the error of the brace tree
- When the same statement appears two or more times under the same top word, a warning (`duplicate_line`) is given. Neither line is dropped
- The notes and the targets in the table rows are developer notes and are shown in Japanese
"""

_PANOS_HEADER = i18n.choose(_PANOS_HEADER_JA, _PANOS_HEADER_EN)

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
    "The inside of `config vdom` and `config global` stays in the generic form as before. "
    "For FortiSwitch (REQ-299. ADR-0212 decisions 2 to 9), the top-level `config switch vlan` is mapped to `vlans`, and `config switch interface`, "
    "`config switch trunk` and `config switch physical-port` to `interfaces` (`switchport`, `type`, `shutdown` and `port_options`), "
    "and none of them is put in `vendor_specific.fortinet.config` (`config system interface` is read completely first). "
    "scope is `fsw_vlan_edit` (an `edit` directly under `config switch vlan`), `fsw_vlan_option` (maps the lines inside that `edit` to `options` "
    "by a path), `fsw_port_edit` (an `edit` directly under the three blocks), `fsw_interface` (the table fields inside an `edit` of `switch interface`), "
    "`fsw_physical_port` (`set status` inside an `edit` of `switch physical-port`), and `fsw_option` (maps the other lines to `port_options` "
    "by a path). The other `config switch ...` and `config switch-controller ...` stay in the generic form as before.\n",
)
_YAMAHA_MAP_JA = (
    "**インターフェース(REQ-301)**: 最上位の `ip <IF> address|mtu|secure filter`・`ipv6 <IF> address` と、`pp`・`tunnel` の範囲の中の "
    "`ip pp|tunnel address|mtu|secure filter`・`ipv6 pp|tunnel address` を `interfaces` に写す。"
    "`<IF>` は `lan<数字>`・`lan<数字>/<数字>`・`vlan<数字>`・`bridge<数字>`・`loopback<数字>`。範囲の中のインターフェースの名前は範囲の名前(`pp 1`・`pp anonymous`・`tunnel 1`)。"
    "欄は `ip_address`・`address_mode`(`dhcp [<残り>]`)・`mtu`・`access_groups`(`secure filter` の行の原文。出てきた順)・`ipv6_addresses`(昇順の集合)。"
    "インターフェースは表の行が 1 つでも写せた名前ごとに 1 つ作り、名前の順は最初に写せた行の順。"
    "`type` は `lan`・`pp`・`tunnel` が `routed`、`vlan`・`bridge` が `svi`、`loopback` が `loopback`。"
    "同じインターフェースの同じ欄(`ip_address`・`address_mode`・`mtu`)に違う値の行が 2 つ以上あれば、後の行の値を使い、食い違いを重複の警告に出す(値は持たない)。"
    "範囲の外の `ip pp …`・`ip tunnel …`、アドレスとして読めない行は写さず、汎用の形のまま。\n",
    "**スタティックルート(REQ-301)**: 最上位と範囲の中の `ip route <宛先> gateway <ゲートウェイ>`(後ろに何も無い形)を `static_routes` に、出てきた順に写す。"
    "宛先は `default` → `0.0.0.0/0`、アドレスだけなら `/32`、ホストの部分が 0 でないものは写さない。"
    "ゲートウェイは IPv4 なら `next_hop`、`pp <数字>`・`pp anonymous`・`tunnel <数字>` なら `interface`。"
    "`metric=`・`hide`・`filter` などが続く形、`gateway dhcp <IF>`、`ipv6 route` は写さず、汎用の形のまま。\n",
    "**汎用の形から外す(REQ-301)**: 上で写した行は `vendor_specific.yamaha` の `attributes` に入れない(同じ行が 2 か所に出ない)。"
    "読み方は 2 段で、先に写しの表を試し、当たらない行と値が読めない行は汎用の表で読む。写しの表は汎用の表より前に置く。\n",
    "上で写さなかった行は値を解釈せず、範囲ごとの行の集合(空白を 1 つにまとめた文字列の昇順。同じ文字列は 1 つ)として `vendor_specific.yamaha` に写す。"
    "最上位の行は `settings.attributes`、`pp` の範囲の子は `pp.<キー>.attributes`、`tunnel` の範囲の子は `tunnel.<キー>.attributes`。"
    "キーは範囲の名前の後ろの語(`1`・`anonymous`)で、子が無い範囲も空のリストで置く。範囲が無くても `pp`・`tunnel` は `{}` で置く。"
    "範囲を選ぶ行(木では範囲の名前のノード)は値を写さず、解釈済みにするだけ。"
    "scope は `select`(範囲のノードと選び直しのノード)、`pp`・`tunnel`(範囲の子)、`top`(最上位)。書式は行全体に対する正規表現。\n",
    "**送らない行は入れない**: 行を AI に送る側の判定(`Masker().judge_alone`。祖先は範囲の子ならその範囲を選ぶ行 `pp select 1` など、"
    "最上位は無し)にかけ、「そのまま送る」(plain)でない行(送らない・伏せる・表で「そのまま送る」と決まる行)は集合に入れず、未解釈のままにする。"
    "未解釈の行は木の位置(`('pp 1', '<行>')`)で IR の `unparsed` に入る。利用者が設定で足した語は使わない。\n",
)

_YAMAHA_MAP_EN = (
    "**Interfaces (REQ-301)**: top-level `ip <IF> address|mtu|secure filter` and `ipv6 <IF> address`, and `ip pp|tunnel address|mtu|secure filter` and `ipv6 pp|tunnel address` inside a `pp` or `tunnel` scope, are mapped to `interfaces`. "
    "`<IF>` is `lan<number>`, `lan<number>/<number>`, `vlan<number>`, `bridge<number>` or `loopback<number>`. The name of an interface inside a scope is the scope name (`pp 1`, `pp anonymous`, `tunnel 1`). "
    "The fields are `ip_address`, `address_mode` (`dhcp [<rest>]`), `mtu`, `access_groups` (the original text of the `secure filter` lines, in order of appearance) and `ipv6_addresses` (a set in ascending order). "
    "One interface is made for each name that has at least one mapped line, in the order of the first mapped line. "
    "`type` is `routed` for `lan`, `pp` and `tunnel`, `svi` for `vlan` and `bridge`, and `loopback` for `loopback`. "
    "If two or more lines give different values to the same field of the same interface (`ip_address`, `address_mode`, `mtu`), the value of the later line is used and the conflict is put in the duplicate warning (the value is not kept). "
    "`ip pp ...` and `ip tunnel ...` outside a scope, and lines whose address cannot be read, are not mapped and stay in the generic form.\n",
    "**Static routes (REQ-301)**: top-level and in-scope `ip route <destination> gateway <gateway>` (with nothing after it) is mapped to `static_routes` in order of appearance. "
    "The destination is `default` to `0.0.0.0/0`, a bare address to `/32`, and one whose host part is not 0 is not mapped. "
    "The gateway is `next_hop` for an IPv4 address, and `interface` for `pp <number>`, `pp anonymous` or `tunnel <number>`. "
    "Forms followed by `metric=`, `hide`, `filter` and so on, `gateway dhcp <IF>`, and `ipv6 route` are not mapped and stay in the generic form.\n",
    "**Taken out of the generic form (REQ-301)**: the lines mapped above are not put in `attributes` of `vendor_specific.yamaha` (the same line does not appear in two places). "
    "A line is read in two steps: the mapping rows are tried first, and a line that matches none or whose value cannot be read is read by the generic rows. The mapping rows are placed before the generic rows.\n",
    "The lines not mapped above are not interpreted. They are mapped to `vendor_specific.yamaha` as a set of lines for each scope (strings with whitespace squeezed to one space, in ascending order, one for each distinct string). "
    "Top-level lines go to `settings.attributes`, children of a `pp` scope to `pp.<key>.attributes`, and children of a `tunnel` scope to `tunnel.<key>.attributes`. "
    "The key is the word after the scope name (`1`, `anonymous`), and a scope without children is also put with an empty list. `pp` and `tunnel` are put as `{}` even when there is no scope. "
    "A scope-selecting line (a scope-name node in the tree) is not mapped to a value; it is only marked as interpreted. "
    "scope is `select` (scope nodes and re-selection nodes), `pp` and `tunnel` (children of a scope), and `top` (top level). The form is a regular expression for the whole line.\n",
    "**Lines that are not sent are not put in**: each line is judged by the same judgment as the side that sends to AI (`Masker().judge_alone`. The ancestor is the scope-selecting line such as `pp select 1` for a child of a scope, "
    "and none for a top-level line). A line that is not \"send as it is\" (plain) (not sent, masked, or decided \"send as it is\" by the table) is not put in the set and stays uninterpreted. "
    "An uninterpreted line enters `unparsed` of the IR by its tree position (`('pp 1', '<line>')`). Words that the user added in the settings are not used.\n",
)

_FLAT_MAP = i18n.choose(_FLAT_MAP_JA, _FLAT_MAP_EN)
_FORTINET_MAP = i18n.choose(_FORTINET_MAP_JA, _FORTINET_MAP_EN)
_YAMAHA_MAP = i18n.choose(_YAMAHA_MAP_JA, _YAMAHA_MAP_EN)

_JUNOS_MAP_JA = (
    "**汎用の形(ADR-0224 決定 6 の 2026-11-17 の追記。REQ-322)**: 木の全部の文を `vendor_specific.juniper_junos.config` に、最上位の語ごとの文の集合で写す。"
    "形は `{\"<最上位の語>\": {\"lines\": [文の昇順。同じ文字列は 1 つ]}}`。残りの語の無い最上位の語は `{\"lines\": []}`。"
    "`deactivate …` の文も `lines` に入る。`lines` は並びを無視して要素ごとの追加・削除で比べる。\n",
    "scope は、汎用の形では `top`(最上位の語のノード)・`statement`(文のノード)・`top_line`(残りの語の無い最上位の行のノード)。"
    "共通スキーマの写しでは最上位の語そのもの(`system`・`interfaces`・`routing-options`・`protocols`・`policy-options`・`firewall`)。書式は行全体に対する正規表現。\n",
    "**global(最上位の語 `system`。REQ-324)**: `host-name <名前>` は `global.hostname`、`domain-name <名前>` は `global.domain_name`、"
    "`name-server <アドレス>` は `global.dns.servers`(正規のアドレスの文字列。昇順・重ねない)、`ntp server <アドレス>[ <残り>]` は "
    "`global.ntp_servers`(後ろが無ければアドレスだけ)。値が引用符で囲まれていれば外す。アドレスとして読めない `name-server`・`ntp server` の文は写さない。"
    "同じ欄に違う値の文が 2 つ以上あれば、後の文の値を使い、重複の警告に行番号を記録する(値は持たない)。\n",
    "**インターフェース(最上位の語 `interfaces`。REQ-324)**: 文の最初の語が物理の名前。物理は `description`・`disable`(shutdown を真)・`mtu`。"
    "`unit <n>` は `<物理の名前>.<n>` の別のインターフェース(parent_interface は物理の名前)で、`description`・`disable`・"
    "`vlan-id <数>`(encapsulation に原文)・`family inet address <A>/<長さ>`(行の順の 1 つ目が ip_address、2 つ目以降が secondary_addresses の昇順)・"
    "`family inet6 address <A>/<長さ>`(ipv6_addresses の昇順の集合)。type は名前で決める(`ae` で始まれば port_channel、`lo` で始まれば loopback、"
    "`irb`・`vlan` は svi、ほかは routed。論理は物理と同じ)。表の文が 1 つでも写せた名前ごとに 1 つ作り、`switchport` は推測しない。"
    "後ろに語がある形(`vrrp-group …`・`primary`・`preferred`)は写さない。\n",
    "**スタティックルート(最上位の語 `routing-options`。REQ-324)**: `static route <宛先> next-hop <アドレス>`(IPv4)と "
    "`rib inet6.0 static route <宛先> next-hop <アドレス>`(IPv6)を `static_routes` に写す。宛先はホストの部分が 0 のものだけ。"
    "`next-hop` ごとに 1 本。`discard`・`reject`・`preference`・`qualified-next-hop` などほかの文は写さない。vrf・interface・distance・enabled は None。\n",
    "**BGP(最上位の語 `routing-options` と `protocols`。REQ-325)**: `routing-options` の `autonomous-system <数>`(後ろに語が無い形。1〜4294967295 の 10 進の数だけ)が `bgp.local_as`、"
    "`router-id <IPv4>` が `bgp.router_id`。`protocols` の `bgp …` の文が 1 つでもあり、`autonomous-system` が読めるときだけ `bgp` を作る"
    "(そうでなければ `bgp` は None で、BGP の文・`autonomous-system`・`router-id` は汎用の形のまま)。"
    "`bgp group <名前> …` は `neighbors[<グループ名>]`(kind は peer_group)で、`peer-as <数>` が remote_as、`description` が description、`local-address <アドレス>` が update_source、"
    "`neighbor` で始まらないほかの文が options(`group <名前>` の後ろ。昇順の集合)。`bgp group <名前> neighbor <アドレス> …` は `neighbors[<アドレスの正規の書き方>]`"
    "(kind は address、peer_group はグループ名)で、同じ欄は相手の直下の `peer-as`・`description`・`local-address`、ほかの文は options(`neighbor <アドレス>` の後ろ)。"
    "`bgp` の直下の `group` で始まらない文は `bgp.options`。`authentication-key …` の文(グループか相手の直下)は送らない側のままで、IR にも汎用の形にも入らず、"
    "その相手(グループ)の password_configured が真になる(値は持たない)。shutdown は偽、address_families・vrfs・evpn_instances は空。"
    "読めない値(`peer-as`・`local-address`・アドレス)の文は写さない。同じ欄に違う値の文が 2 つ以上あれば、後の文の値を使い、行番号を記録する。\n",
    "**prefix-list(最上位の語 `policy-options`。REQ-325)**: `prefix-list <名前> <プレフィックス>`(IPv4・IPv6 とも。`/` の無い語は写さない)は "
    "`prefix_lists[<名前>].entries[<プレフィックスの正規の書き方>]`(action は permit、ge・le・eq は None)、`prefix-list <名前> description <文>` は description。"
    "`apply-path …` などほかの文は汎用の形のまま。\n",
    "**policy-statement(最上位の語 `policy-options`。REQ-325)**: `policy-statement <名前> term <項> from <残り>` は route_maps の項の match、"
    "`… then <残り>` は `accept` なら action permit、`reject` なら deny、ほかは set。項の鍵は、項が文に初めて出た順の番号(1 始まり。"
    "無効の印の掛かった項・送らない文も数える)と項の名前を空白でつないだもの(`1 t1`)。項の無い `from`・`then` は鍵 `final` の項。"
    "`accept`・`reject` の無い項は action を permit にして options に `no-final-action` を入れる。`from`・`then` 以外の項の文は options。"
    "match・set・options は昇順の集合。\n",
    "**firewall filter(最上位の語 `firewall`。REQ-325)**: `family inet filter <名前> term <項> <残り>` は `acls[<名前>]`(type は extended、numbered は偽)の項ごとに 1 つの entries"
    "(seq は項が文に初めて出た順の番号、text は `term <項>: ` の後に項の中の文を出た順に `; ` でつないだもの。"
    "最後の語だけが違う文が続く間〔brace の `[ a b ]`〕はその中だけ昇順)。項の無い文(`interface-specific` など)と、`family inet6` などほかの family は汎用の形のまま。\n",
    "**汎用の形から外す・無効の印・鍵の扱い(REQ-324)**: 写した文は汎用の形(`lines`)に入れない。写せなかった文(形が違う・値が読めない・送らない側)は汎用の形に置く。"
    "同じ最上位の語の下の `deactivate <経路>`(最上位の語だけなら `deactivate`)が掛かった文は写さず、汎用の形に残す。`deactivate …` の文そのものも汎用の形に残る。"
    "汎用の文が 0 件になった最上位の語は鍵ごと置かない(`set system` だけの行は今までどおり `{\"lines\": []}`)。\n",
    "**送らない行は入れない(決定 6)**: 文ごとに、`display set` の行の形 `set <最上位の語> <文>`(残りの語の無い最上位の行は `set <最上位の語>`)を"
    "AI に送る側の判定(`Masker().judge_alone`。祖先は無し)にかけ、「そのまま送る」(plain)でない文(送らない・伏せる・表で「そのまま送る」と決まる文)は入れず、"
    "未解釈のままにする(`deactivate …` の文も同じ)。文が全部送らない側になった最上位の語は鍵ごと置かない。"
    "利用者が設定で足した語は使わない。`set` で始まらない最上位の行と、`delete: `・`insert ` を含む文も未解釈のまま。"
    "未解釈の文は木の位置(`('interfaces', 'ge-0/0/0 unit 0 family inet address 192.0.2.1/24')`)で IR の `unparsed` に入る。\n",
)

_JUNOS_MAP_EN = (
    "**Generic form (the 2026-11-17 addition to ADR-0224 decision 6. REQ-322)**: every statement of the tree is mapped to `vendor_specific.juniper_junos.config` as a set of statements for each top word. "
    "The form is `{\"<top word>\": {\"lines\": [statements in ascending order, one for each distinct string]}}`. A top word without remaining words is `{\"lines\": []}`. "
    "A `deactivate ...` statement also goes into `lines`. `lines` is compared by the addition and removal of each element, ignoring the order.\n",
    "In the generic form, scope is `top` (a top word node), `statement` (a statement node) and `top_line` (the node of a top-level line without remaining words). "
    "In the mapping to the common schema, scope is the top word itself (`system`, `interfaces`, `routing-options`, `protocols`, `policy-options`, `firewall`). The form is a regular expression for the whole line.\n",
    "**global (top word `system`. REQ-324)**: `host-name <name>` goes to `global.hostname`, `domain-name <name>` to `global.domain_name`, "
    "`name-server <address>` to `global.dns.servers` (the canonical address string; ascending, no duplicates), and `ntp server <address>[ <rest>]` to "
    "`global.ntp_servers` (only the address if nothing follows). Quotation marks around a value are removed. A `name-server` or `ntp server` statement whose address cannot be read is not mapped. "
    "If two or more statements with different values are for the same field, the value of the later statement is used and the line numbers are recorded in the duplicate warning (no values are kept).\n",
    "**Interfaces (top word `interfaces`. REQ-324)**: the first word of a statement is the physical name. A physical interface takes `description`, `disable` (shutdown becomes true) and `mtu`. "
    "`unit <n>` is another interface named `<physical name>.<n>` (parent_interface is the physical name) that takes `description`, `disable`, "
    "`vlan-id <number>` (the original text goes to encapsulation), `family inet address <A>/<length>` (the first in line order is ip_address, the second and later are secondary_addresses in ascending order) and "
    "`family inet6 address <A>/<length>` (a set of ipv6_addresses in ascending order). The type is decided by the name (`ae` prefix: port_channel, `lo` prefix: loopback, "
    "`irb` and `vlan`: svi, otherwise routed; a logical interface has the same type as its physical one). One interface is made for each name that at least one statement of the table could map, and `switchport` is not guessed. "
    "A form with words after the address (`vrrp-group ...`, `primary`, `preferred`) is not mapped.\n",
    "**Static routes (top word `routing-options`. REQ-324)**: `static route <destination> next-hop <address>` (IPv4) and "
    "`rib inet6.0 static route <destination> next-hop <address>` (IPv6) are mapped to `static_routes`. The destination must be a network (host part 0). "
    "One route for each `next-hop`. Other statements such as `discard`, `reject`, `preference` and `qualified-next-hop` are not mapped. vrf, interface, distance and enabled are None.\n",
    "**BGP (top words `routing-options` and `protocols`. REQ-325)**: `autonomous-system <number>` of `routing-options` (no words after it; a decimal number from 1 to 4294967295 only) is `bgp.local_as`, "
    "and `router-id <IPv4>` is `bgp.router_id`. `bgp` is made only when `protocols` has at least one `bgp ...` statement and `autonomous-system` can be read "
    "(otherwise `bgp` is None, and the BGP statements, `autonomous-system` and `router-id` stay in the generic form). "
    "`bgp group <name> ...` is `neighbors[<group name>]` (kind peer_group): `peer-as <number>` is remote_as, `description` is description, `local-address <address>` is update_source, "
    "and the other statements that do not start with `neighbor` are options (the rest after `group <name>`; an ascending set). `bgp group <name> neighbor <address> ...` is `neighbors[<canonical address>]` "
    "(kind address, peer_group is the group name): the same fields come from `peer-as`, `description` and `local-address` directly under the neighbor, and the other statements are options (the rest after `neighbor <address>`). "
    "A statement directly under `bgp` that does not start with `group` goes to `bgp.options`. An `authentication-key ...` statement (directly under a group or a neighbor) stays on the not-sent side and enters neither the IR nor the generic form; "
    "password_configured of that neighbor (group) becomes true (no value is kept). shutdown is false; address_families, vrfs and evpn_instances are empty. "
    "A statement with an unreadable value (`peer-as`, `local-address`, an address) is not mapped. If two or more statements with different values are for the same field, the later value is used and the line numbers are recorded.\n",
    "**prefix-list (top word `policy-options`. REQ-325)**: `prefix-list <name> <prefix>` (IPv4 and IPv6; a word without `/` is not mapped) goes to "
    "`prefix_lists[<name>].entries[<canonical prefix>]` (action permit; ge, le and eq None), and `prefix-list <name> description <text>` to description. "
    "Other statements such as `apply-path ...` stay in the generic form.\n",
    "**policy-statement (top word `policy-options`. REQ-325)**: `policy-statement <name> term <term> from <rest>` goes to match of a route_maps entry, "
    "and `... then <rest>` to action permit for `accept`, deny for `reject`, and set otherwise. The key of an entry is the number in the order the term first appears in the statements (from 1; "
    "an inactive term and a statement that is not sent are counted) and the term name joined by a space (`1 t1`). `from` and `then` without a term are the entry with the key `final`. "
    "An entry with neither `accept` nor `reject` gets action permit and `no-final-action` in options. A statement of a term other than `from` and `then` goes to options. "
    "match, set and options are ascending sets.\n",
    "**firewall filter (top word `firewall`. REQ-325)**: `family inet filter <name> term <term> <rest>` goes to `acls[<name>]` (type extended, numbered false), one entry for each term "
    "(seq is the number in the order the term first appears; text is `term <term>: ` followed by the statements of the term in the order they appear, joined by `; `. "
    "While statements that differ only in the last word follow each other [a brace `[ a b ]`], only that run is sorted in ascending order). Statements without a term (such as `interface-specific`) "
    "and other families such as `family inet6` stay in the generic form.\n",
    "**Taking out of the generic form, the inactive mark, and keys (REQ-324)**: a mapped statement is not put in the generic form (`lines`). A statement that could not be mapped (a different form, an unreadable value, or on the not-sent side) is put in the generic form. "
    "A statement covered by a `deactivate <path>` under the same top word (`deactivate` alone for the top word itself) is not mapped and stays in the generic form. The `deactivate ...` statement itself also stays in the generic form. "
    "A top word whose generic statements are 0 gets no key (a line of only `set system` stays `{\"lines\": []}` as before).\n",
    "**Lines that are not sent are not put in (decision 6)**: for each statement, the line in the `display set` form, `set <top word> <statement>` (`set <top word>` for a top-level line without remaining words), "
    "is judged by the same judgment as the side that sends to AI (`Masker().judge_alone`, with no ancestors). A statement that is not \"send as it is\" (plain) (not sent, masked, or decided \"send as it is\" by the table) is not put in and stays uninterpreted "
    "(a `deactivate ...` statement too). A top word whose statements are all on the not-sent side gets no key. "
    "Words that the user added in the settings are not used. A top-level line that does not start with `set`, and a statement that contains `delete: ` or `insert `, also stay uninterpreted. "
    "An uninterpreted statement enters `unparsed` of the IR by its tree position (`('interfaces', 'ge-0/0/0 unit 0 family inet address 192.0.2.1/24')`).\n",
)

_JUNOS_MAP = i18n.choose(_JUNOS_MAP_JA, _JUNOS_MAP_EN)

_PANOS_MAP_JA = (
    "**汎用の形(ADR-0226 決定 6。REQ-327)**: 木の全部の文を `vendor_specific.paloalto_panos.config` に、最上位の語ごとの文の集合で写す。"
    "形は `{\"<最上位の語>\": {\"lines\": [文の昇順。同じ文字列は 1 つ]}}`。残りの語の無い最上位の語は `{\"lines\": []}`。"
    "`lines` は並びを無視して要素ごとの追加・削除で比べる。よく使う文(REQ-329。下の 3 つ)は共通スキーマに写し、写した文は汎用の形に入れない。ほかの文は汎用の形に残る。\n",
    "scope は、汎用の形では `top`(最上位の語のノード)・`statement`(文のノード)・`top_line`(残りの語の無い最上位の行のノード)。"
    "共通スキーマの写しでは最上位の語そのもの(`deviceconfig`・`network`)。書式は行全体に対する正規表現。\n",
    "**global(最上位の語 `deviceconfig`。REQ-329)**: `system hostname <名前>` は `global.hostname`、`system domain <名前>` は `global.domain_name`、"
    "`system dns-setting servers primary|secondary <アドレス>` は `global.dns.servers`(正規のアドレスの文字列。昇順・重ねない)、"
    "`system ntp-servers primary-ntp-server|secondary-ntp-server ntp-server-address <アドレスか名前>` は `global.ntp_servers`"
    "(アドレスは正規の書き方、名前はそのまま。使う値の文の行の順・重ねない)、`system default-gateway <IPv4>` は `global.default_gateway`。"
    "値が引用符で囲まれていれば外す。アドレスとして読めない文は写さない。`authentication-type` などほかの文は汎用の形のまま。"
    "同じ欄に違う値の文が 2 つ以上あれば、後の文の値を使い、重複の警告に行番号を記録する(値は持たない)。\n",
    "**インターフェース(最上位の語 `network`。REQ-329)**: `interface ethernet|aggregate-ethernet <名前> …` の `comment <文>` は description、`link-speed <値>` は speed、"
    "`layer3 ip <A>/<長さ>`(行の順の 1 つ目が ip_address、2 つ目以降が secondary_addresses の昇順)、`layer3 mtu <数>` は mtu。"
    "`… layer3 units <名前.数> …` は `<名前.数>` の別のインターフェース(parent_interface は物理の名前)で、`ip <A>/<長さ>`・`tag <数>`"
    "(encapsulation に原文の `tag <数>`)・`comment <文>`。`interface loopback|tunnel|vlan units <名前.数> …` の `ip`・`comment`・`mtu` も同じ"
    "(parent_interface は種類の語)。`interface loopback|tunnel|vlan ip <A>/<長さ>` は種類の語の名前の物理の ip_address。"
    "type は名前と種類で決める(`ae` で始まるか aggregate-ethernet なら port_channel、loopback は loopback、vlan は svi、ほかは routed。論理は物理と同じ)。"
    "表の文が 1 つでも写せた名前ごとに 1 つ作り、`switchport` は推測しない。`ip` が IPv4 として読めない文(アドレスのオブジェクトの名前など)・"
    "`ipv6 …`・`interface-management-profile` などは汎用の形のまま。\n",
    "**スタティックルート(最上位の語 `network`。REQ-329)**: `virtual-router <VR> routing-table ip|ipv6 static-route <名前> …` を、"
    "同じ <VR>・ip か ipv6・<名前> ごとに 1 本の `static_routes` にまとめる。`destination <宛先>/<長さ>`(ip は IPv4、ipv6 は IPv6 のネットワーク。ホストの部分が 0)は destination、"
    "`nexthop ip-address|ipv6-address <アドレス>` は next_hop、`interface <名前>` は interface、`admin-dist <数>` は distance。"
    "vrf は <VR> が `default` なら None、ほかは <VR> の名前。enabled は None。destination が無いか読めない経路は、どの文も写さず汎用の形に残す。"
    "`metric`・`path-monitor …`・`bfd …`・`nexthop next-vr …` などほかの文は汎用の形のまま。\n",
    "**汎用の形から外す・鍵の扱い(REQ-329)**: 写した文は汎用の形(`lines`)に入れない。写せなかった文(形が違う・値が読めない・送らない側)は汎用の形に置く。"
    "汎用の文が 0 件になった最上位の語は鍵ごと置かない(`set deviceconfig` だけの行は今までどおり `{\"lines\": []}`)。\n",
    "**複数行の値**: XML の複数行の値(PEM など)の文は、中身を持たず、`<複数行の値 <行数> 行 <sha256 の 16 進の頭 16 字>>` の 1 語を値の代わりにする"
    "(例 `shared certificate cert-test certificate <複数行の値 18 行 0123456789abcdef>`。英語で動かしても同じ文字)。中身が変われば文が変わって差分に出る。\n",
    "**送らない行は入れない(決定 6)**: 文ごとに、set の行の形 `set <最上位の語> <文>`(残りの語の無い最上位の行は `set <最上位の語>`)を"
    "AI に送る側の判定(`Masker().judge_alone`。祖先は無し)にかけ、「そのまま送る」(plain)でない文(送らない・伏せる・表で「そのまま送る」と決まる文)は入れず、"
    "未解釈のままにする。文が全部送らない側になった最上位の語は鍵ごと置かない。利用者が設定で足した語は使わない。"
    "`set` で始まらない最上位の行も未解釈のまま。未解釈の文は木の位置(`('mgt-config', 'users admin phash <値>')`)で IR の `unparsed` に入る。\n",
)

_PANOS_MAP_EN = (
    "**Generic form (ADR-0226 decision 6. REQ-327)**: every statement of the tree is mapped to `vendor_specific.paloalto_panos.config` as a set of statements for each top word. "
    "The form is `{\"<top word>\": {\"lines\": [statements in ascending order, one for each distinct string]}}`. A top word without remaining words is `{\"lines\": []}`. "
    "`lines` is compared by the addition and removal of each element, ignoring the order. The commonly used statements (REQ-329; the next three) are mapped to the common schema, and a mapped statement is not put in the generic form. The other statements stay in the generic form.\n",
    "scope is, for the generic form, `top` (a top word node), `statement` (a statement node) and `top_line` (the node of a top-level line without remaining words). "
    "For the mapping to the common schema it is the top word itself (`deviceconfig`, `network`). The form is a regular expression for the whole line.\n",
    "**global (top word `deviceconfig`. REQ-329)**: `system hostname <name>` goes to `global.hostname`, `system domain <name>` to `global.domain_name`, "
    "`system dns-setting servers primary|secondary <address>` to `global.dns.servers` (canonical address strings, ascending, no duplicates), "
    "`system ntp-servers primary-ntp-server|secondary-ntp-server ntp-server-address <address or name>` to `global.ntp_servers` "
    "(an address in its canonical form, a name as it is; in the line order of the statements whose values are used, no duplicates), and `system default-gateway <IPv4>` to `global.default_gateway`. "
    "A quoted value loses its quotes. A statement whose address cannot be read is not mapped. Other statements such as `authentication-type` stay in the generic form. "
    "If two or more statements for the same field have different values, the value of the later statement is used and the line numbers are recorded in the duplicate warning (the values are not kept).\n",
    "**Interfaces (top word `network`. REQ-329)**: for `interface ethernet|aggregate-ethernet <name> …`, `comment <text>` is the description, `link-speed <value>` the speed, "
    "`layer3 ip <A>/<length>` (the first one in line order is ip_address, the rest are secondary_addresses in ascending order) and `layer3 mtu <number>` the mtu. "
    "`… layer3 units <name.number> …` is a separate interface `<name.number>` (parent_interface is the physical name) with `ip <A>/<length>`, `tag <number>` "
    "(the original `tag <number>` goes to encapsulation) and `comment <text>`. `ip`, `comment` and `mtu` of `interface loopback|tunnel|vlan units <name.number> …` are the same "
    "(parent_interface is the kind word). `interface loopback|tunnel|vlan ip <A>/<length>` is the ip_address of the physical interface named by the kind word. "
    "type is decided by the name and the kind (port_channel if it starts with `ae` or is aggregate-ethernet, loopback for loopback, svi for vlan, routed otherwise; a logical one is the same as its physical one). "
    "One interface is made for each name that has at least one mapped statement, and `switchport` is not guessed. A statement whose `ip` cannot be read as IPv4 (such as an address object name), "
    "`ipv6 …` and `interface-management-profile` stay in the generic form.\n",
    "**Static routes (top word `network`. REQ-329)**: statements of `virtual-router <VR> routing-table ip|ipv6 static-route <name> …` are put together as one `static_routes` entry "
    "for each same <VR>, ip or ipv6, and <name>. `destination <destination>/<length>` (an IPv4 network for ip, an IPv6 network for ipv6; the host part is 0) is destination, "
    "`nexthop ip-address|ipv6-address <address>` is next_hop, `interface <name>` is interface and `admin-dist <number>` is distance. "
    "vrf is None if <VR> is `default`, otherwise the name of <VR>. enabled is None. A route without a destination or with an unreadable one is not mapped (all its statements stay in the generic form). "
    "Other statements such as `metric`, `path-monitor …`, `bfd …` and `nexthop next-vr …` stay in the generic form.\n",
    "**Taking mapped statements out of the generic form, and keys (REQ-329)**: a mapped statement is not put in the generic form (`lines`). A statement that could not be mapped (a different form, an unreadable value, or the not-sent side) is put in the generic form. "
    "A top word with 0 generic statements gets no key (a line of only `set deviceconfig` stays `{\"lines\": []}` as before).\n",
    "**Multi-line value**: a statement of a multi-line value of XML (such as a PEM) does not hold the content; one word of the line count and the first 16 hex digits of the SHA-256 takes the place of the value "
    "(the fixed word of `MULTILINE_WORD` in `panos_xml.py`; the same text when run in English). When the content changes, the statement changes and shows as a difference.\n",
    "**A line that is not sent is not put in (decision 6)**: for each statement, the set line form `set <top word> <statement>` (`set <top word>` for a top-level line without remaining words) is passed to "
    "the judgment of the side that sends to the AI (`Masker().judge_alone`, no ancestors), and a statement that is not \"sent as it is\" (plain) (not sent, masked, or decided as plain by the table) is not put in and "
    "stays uninterpreted. A top word whose statements are all on the not-sent side gets no key. Words the user added in the settings are not used. "
    "A top-level line that does not start with `set` also stays uninterpreted. An uninterpreted statement enters `unparsed` of the IR by its tree position (`('mgt-config', 'users admin phash <value>')`).\n",
)

_PANOS_MAP = i18n.choose(_PANOS_MAP_JA, _PANOS_MAP_EN)

_GAIA_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(Check Point Gaia: checkpoint_gaia)

> **自動生成。手で編集しない。** 行頭スコープ表(`src/driftpilot/config_parser/flat_scopes.py` の `GAIA_SCOPES`)と
> 写しの表(`src/driftpilot/config_parser/gaia.py` の `PATTERNS`)、木の組み立ての版(`gaia.py` の `TREE_VERSION`)から作る。
> 再生成: `{GAIA_GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

Gaia の config(ADR-0234。`show configuration` の出力)は、字下げの無い `set <対象> …`・`add <対象> …` の行の並びで、Si-R と同じ
行頭スコープ表の平らな読み方(ADR-0024)で木にする。hier_config は使わない。

- `#` で始まる行(前の空白は可)と空の行は木に入れない(`# Configuration of …`・`# Language version: …`・`# Exported by … on <日時>` など)。
  行番号はもとの行のまま。日時だけ違う 2 つの config の比較に差分は出ない
- 動詞(`set`・`add`)はスコープ名に入れない。`set interface eth1 state on` と `add interface eth1 vlan 100` は同じ `interface eth1` の下に入る。
  最後のスコープは、`set`・`add` の後ろの語そのものがスコープ名になる(`set dns primary 192.0.2.53` はスコープ `dns` の子 `primary 192.0.2.53`)
- `delete …` で始まる行と、`set`・`add` の後ろの語が無い・形に合わない行は、どのスコープにも当たらず、行全体が最上位の 1 つのノードになる(未解釈)
- 同じスコープの下で同じ行が 2 回以上ある、または同じ項目に違う値が書かれていると、警告(`duplicate_line`)を出す。どちらの行も捨てない
- 範囲・列挙の展開はしない。木の組み立ての版 `{GAIA_TREE_VERSION}` は `#` の行の外し方の版で、表の指紋に入る(外し方を変えたら上げる)
- 表の行の備考と写し先は開発者向けのメモで、日本語のまま出す
"""

_GAIA_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (Check Point Gaia: checkpoint_gaia)

> **Generated automatically. Do not edit by hand.** It is made from the line-head scope table (`GAIA_SCOPES` of `src/driftpilot/config_parser/flat_scopes.py`),
> the mapping table (`PATTERNS` of `src/driftpilot/config_parser/gaia.py`) and the version of the tree building (`TREE_VERSION` of `gaia.py`).
> Regenerate: `{GAIA_GENERATE_COMMAND}`
> If the table is changed but this file is not, the test fails.

A Gaia config (ADR-0234; the output of `show configuration`) is a series of lines without indentation, `set <object> …` and `add <object> …`.
It is made into a tree by the same flat reading as Si-R, with a line-head scope table (ADR-0024). hier_config is not used.

- Lines that start with `#` (spaces before it are allowed) and empty lines are not put in the tree (`# Configuration of …`, `# Language version: …`, `# Exported by … on <date and time>`, and so on).
  Line numbers stay those of the original lines. Comparing two configs that differ only in the date and time gives no difference
- The verb (`set`, `add`) is not put in the scope name. `set interface eth1 state on` and `add interface eth1 vlan 100` go under the same `interface eth1`.
  For the last scope, the word after `set` or `add` is the scope name itself (`set dns primary 192.0.2.53` is `primary 192.0.2.53` under the scope `dns`)
- A line that starts with `delete …`, and a line with no word after `set` or `add` or in a form that does not fit, matches no scope, and the whole line becomes one top-level node (uninterpreted)
- If the same line appears twice or more under the same scope, or different values are written for the same item, a warning (`duplicate_line`) is given. Neither line is dropped
- Ranges and lists are not expanded. The tree building version `{GAIA_TREE_VERSION}` is the version of how the `#` lines are removed, and it goes into the table fingerprint (raise it if the way is changed)
- The notes and targets of the table rows are memos for developers, and are shown in Japanese as they are
"""

_GAIA_HEADER = i18n.choose(_GAIA_HEADER_JA, _GAIA_HEADER_EN)

_GAIA_MAP_JA = (
    "**共通スキーマへの写し(ADR-0234 決定 3。REQ-345)**: 葉ごとに 2 段で読む。まず、よく使う葉を IR の写しの表(下の `interfaces.gaia…`・`static_routes.gaia…`・`global.gaia…`)で、"
    "最上位のスコープの種類(`interface <名前>`・`bonding group <N>`・`static-route <宛先>`・`hostname`・`domainname`・`timezone`・`dns`・`ntp`・`syslog`・`snmp`)に合わせて写す。"
    "写せた葉は解釈済みにし、汎用の形には入れない(REQ-344・347 の二重の判定によらず入る。写す欄に秘密は無く、AI に送るときの欄の伏せ方は IR の欄の決まりに従う)。"
    "写せなかった葉(書式に合わない・範囲の外の数・アドレスとして読めない値・写す対象でないスコープ)は、後の「汎用の形」のパターンで読み直す(二重の判定つき)。"
    "IR の欄は足さない。`user`・`aaa`・`bgp`・`ospf`・`ipv6 static-route` などは汎用の形のまま。\n",
    "**インターフェースとボンディング**: スコープ `interface <名前>` 1 つに Interface を 1 つ作る(名前の初出の順)。`state on|off` は shutdown(偽・真。行が無ければ既定の偽)、"
    "`ipv4-address <A> mask-length <N>` は ip_address(`<A>/<N>`。N は 0〜32)、`comments <文>` は description(両端の `\"` を外す)、`mtu <N>` は mtu、"
    "`link-speed <語>` は speed(語の原文)。`vlan <N>`(`add interface <名前> vlan <N>`。N は 1〜4094)は Interface `<名前>.<N>` を作り(無ければ)、parent_interface を `<名前>` にする。"
    "type は名前で決める: `.` を含む名前は routed(Cisco のサブインターフェースと同じ)、`bond` + 数字で始まる名前は port_channel、`lo` で始まる名前(`loopback` を含む)は loopback、ほかは routed。"
    "スコープ `bonding group <N>` があれば `bond<N>`(port_channel)を作り、`interface <名前>` の葉でその名前の Interface(無ければ作る)の channel_group を `{\"id\": N, \"mode\": \"bond\"}` にする。"
    "`auto-negotiation …`・`security-zone …`・`ipv6-address …`・`mode …`・`lacp-rate …`・`mii-interval …` などほかの葉と、形に合わない葉は汎用の形のまま。\n",
    "**スタティックルート(スコープ `static-route <宛先>`)**: 宛先は `default`(0.0.0.0/0)か、ホストの部分が 0 の IPv4 の `<A>/<長さ>`。読めない宛先のスコープは全部の葉を汎用の形のままにする。"
    "葉 1 つを経路 1 本にする(スコープの順、その中の葉の順)。`nexthop gateway address <A> [priority <N>] on|off` は next_hop = A・interface = None・distance = N(無ければ None)・enabled = `on` なら真・`off` なら偽、"
    "`nexthop gateway logical <名前> [priority <N>] on|off` は next_hop = None・interface = 名前でほかは同じ、`nexthop blackhole|reject` は next_hop = None・interface = `blackhole`・`reject`・distance = None・enabled = None。"
    "vrf はいつも None。`priority <N>` は Gaia の同じ宛先の次の候補の優先度(1〜8)で、IR に欄が無いので distance に持つ。"
    "`off` だけの葉(経路そのものを止める)・ほかの形は汎用の形のまま。経路の機種固有値(route_vendor の形)は置かない。\n",
    "**全体(IR の global)**: `hostname` は global.hostname、`domainname` は global.domain_name、`timezone` は global.timezone(葉の原文。空白は 1 つにする)、"
    "`dns` の `primary|secondary|tertiary <A>` は global.dns.servers に「`<A>`(正規の書き方) `<欄>`」(FortiGate と同じ形。欄ごとに後の行の値、文字列の昇順)、"
    "`ntp` の `server primary|secondary <A または名前> [<残り>]` は global.ntp_servers に「`<A または名前>` `<欄>`[ `<残り>`]」、"
    "`syslog` の `log-remote-address <A> [<残り>]` は global.logging_hosts に「`<A>`[ `<残り>`]」(NTP・syslog は出た順で、同じ文字列は重ねない。Cisco と同じ)、"
    "`snmp` の `location <文>`・`contact <文>` は global.snmp.location・contact(両端の `\"` を外す)。`ntp active on|off`・`syslog` のほかの葉・`snmp` のほかの葉"
    "(`community`・`usm user`・`traps` など)は写さない(汎用の形か、送らない行は未解釈のまま)。\n",
    "**値の食い違いと SNMP の場所・連絡先**: 値を 1 つしか持てない欄(ホスト名・ドメイン名・タイムゾーン・SNMP の場所・連絡先・DNS の各欄・インターフェースの shutdown・ip_address・description・mtu・speed・channel_group)に"
    "違う値が 2 回書かれたら、後の行の値を使い、両方の行番号を重複の警告(`duplicate_line`)に出す(値は出さない。木が見つけた記録と行を共有すれば 1 件にまとまる。"
    "Junos・PAN-OS・FortiGate と同じで、Cisco のホスト名などの「最初の行を使う」とは逆)。"
    "SNMP の場所・連絡先は、REQ-344 ではつないだ 1 行(`snmp location …`)が語 `snmp` で送らない側になり AI に送っていなかったが、IR に写すと Cisco の `snmp-server location`・`contact` と同じ扱い"
    "(IR の欄の伏せ方。場所・連絡先は伏せない)になり、AI に送る。Cisco と同じ扱いにそろえるもので、新しく送る側に緩める決まりではない。\n",
    "**汎用の形(ADR-0234 決定 2。REQ-344)**: 木の全部のノードを `vendor_specific.checkpoint_gaia.config` に、スコープごとの葉の集合で写す。"
    "形は `{\"lines\": [葉のテキストの昇順。同じ文字列は 1 つ], \"blocks\": {\"<スコープの名前>\": {\"lines\": […], \"blocks\": {…}}}}`(Junos と同じ形)。"
    "スコープの名前は行頭スコープ表の name(`interface eth1`・`hostname`・`static-route default` など。`set`・`add` の動詞は入らない)。"
    "最上位の葉は表に当たらない行で、未解釈のままなので、最上位の `lines` はいつも空。"
    "`lines` は並びを無視して要素ごとの追加・削除で比べる。共通スキーマに写せた葉は入れず、写さなかった葉だけが入る。\n",
    "scope は、汎用の形では `scope`(スコープのノード)・`leaf`(スコープの子の葉)。書式は行全体に対する正規表現。"
    "葉が 1 つ以上入ったスコープ(か、残りの語の無い実在の行のスコープ)に鍵を置く。暗黙のノード(実在の行でない)は何も置かない。"
    "葉が全部送らない側だったスコープは鍵ごと置かない。スコープだけの行の 2 回目以降は写さない(木の位置が 1 回目と同じなので、解釈済みの記録は 1 回目のまま)。\n",
    "**送らない行は入れない(ADR-0224 決定 6 と同じ。Gaia は判定を 2 つにする)**: 葉ごとに、AI に送る側の判定(`Masker().judge_alone`)を 2 回行う。"
    "(1) 残りの部分(葉のテキスト)を、スコープの名前(外側から順)を祖先にして判定する。"
    "(2) スコープの名前と残りをつないだ 1 行(例 `snmp usm user …`。動詞 `set`・`add` は付けない)を、祖先なしで判定する。"
    "(1)(2) の両方が「そのまま送る」(plain)のときだけ入れ、どちらかが plain でない葉(送らない・伏せる)は入れず、未解釈のままにする。"
    "(2) は、語 `snmp` のように秘密を示す語がスコープの名前の側にあるとき、祖先の中では判定で見られず、SNMPv3 の合言葉・ハッシュ"
    "(`add snmp usm user … auth-pass-phrase …`・`privacy-pass-hash …`)が入ってしまうのを防ぐ。このため汎用の形には `set snmp location`・`contact` なども"
    "入らない(この 2 つの葉は、先に共通スキーマの snmp に写すので解釈済みになる)。AI に送る内容・人に渡す伏せた出力・config-facts の AI に渡す一覧でも、機種が Gaia の未解釈の行に同じ 2 つの判定を当てる。"
    "利用者が設定で足した語は使わない。`delete …` など表に当たらない行も未解釈のまま。"
    "未解釈の行は木の位置(`('user admin', 'password-hash <値>')`)で IR の `unparsed` に入る。\n",
)

_GAIA_MAP_EN = (
    "**Mapping to the common schema (ADR-0234 decision 3. REQ-345)**: each leaf is read in two steps. First, the commonly used leaves are mapped by the mapping table for the IR (`interfaces.gaia…`, `static_routes.gaia…` and `global.gaia…` below), "
    "according to the kind of the top-level scope (`interface <name>`, `bonding group <N>`, `static-route <destination>`, `hostname`, `domainname`, `timezone`, `dns`, `ntp`, `syslog`, `snmp`). "
    "A leaf that was mapped is marked as interpreted and is not put in the generic form (it goes into the IR without the double judgment of REQ-344 and REQ-347; the fields have no secrets, and how the fields are hidden when sent to the AI follows the rules of the IR fields). "
    "A leaf that could not be mapped (a different form, a number out of range, a value that cannot be read as an address, or a scope that is not mapped) is read again by the patterns of the generic form below (with the double judgment). "
    "No IR field is added. `user`, `aaa`, `bgp`, `ospf`, `ipv6 static-route` and the like stay in the generic form.\n",
    "**Interfaces and bonding**: one Interface is made for each scope `interface <name>` (in the order of first appearance). `state on|off` is shutdown (false or true; false by default if there is no line), "
    "`ipv4-address <A> mask-length <N>` is ip_address (`<A>/<N>`; N is 0 to 32), `comments <text>` is description (the `\"` at both ends is removed), `mtu <N>` is mtu, "
    "and `link-speed <word>` is speed (the word as written). `vlan <N>` (`add interface <name> vlan <N>`; N is 1 to 4094) makes the Interface `<name>.<N>` (if there is none) and sets its parent_interface to `<name>`. "
    "The type is decided by the name: a name that has `.` is routed (the same as a Cisco subinterface), a name that starts with `bond` and a digit is port_channel, a name that starts with `lo` (including `loopback`) is loopback, and the others are routed. "
    "If there is a scope `bonding group <N>`, `bond<N>` (port_channel) is made, and a leaf `interface <name>` sets channel_group of the Interface of that name (made if there is none) to `{\"id\": N, \"mode\": \"bond\"}`. "
    "Other leaves such as `auto-negotiation …`, `security-zone …`, `ipv6-address …`, `mode …`, `lacp-rate …` and `mii-interval …`, and leaves in a form that does not fit, stay in the generic form.\n",
    "**Static routes (scope `static-route <destination>`)**: the destination is `default` (0.0.0.0/0) or an IPv4 `<A>/<length>` whose host part is 0. All the leaves of a scope with an unreadable destination stay in the generic form. "
    "One leaf is one route (in the order of scopes, and of leaves within a scope). `nexthop gateway address <A> [priority <N>] on|off` is next_hop = A, interface = None, distance = N (None if absent), and enabled = true for `on` and false for `off`; "
    "`nexthop gateway logical <name> [priority <N>] on|off` is next_hop = None, interface = the name, and the others the same; `nexthop blackhole|reject` is next_hop = None, interface = `blackhole` or `reject`, distance = None and enabled = None. "
    "vrf is always None. `priority <N>` is the priority of the next candidate of the same destination in Gaia (1 to 8); the IR has no field for it, so it is held in distance. "
    "A leaf of only `off` (it stops the route itself) and other forms stay in the generic form. The vendor-specific values of a route (the form of route_vendor) are not put.\n",
    "**Global (global of the IR)**: `hostname` is global.hostname, `domainname` is global.domain_name, `timezone` is global.timezone (the leaf as written, with one space), "
    "`primary|secondary|tertiary <A>` of `dns` is put in global.dns.servers as \"`<A>` (the normal form) `<field>`\" (the same form as Fortinet; the value of the later line for each field, in ascending order of the strings), "
    "`server primary|secondary <A or name> [<rest>]` of `ntp` is put in global.ntp_servers as \"`<A or name>` `<field>`[ `<rest>`]\", "
    "`log-remote-address <A> [<rest>]` of `syslog` is put in global.logging_hosts as \"`<A>`[ `<rest>`]\" (NTP and syslog are in the order they appear, and the same string is not put twice; the same as Cisco), "
    "and `location <text>` and `contact <text>` of `snmp` are global.snmp.location and contact (the `\"` at both ends is removed). `ntp active on|off`, other leaves of `syslog`, and other leaves of `snmp` "
    "(`community`, `usm user`, `traps` and the like) are not mapped (the generic form, or uninterpreted if on the not-sent side).\n",
    "**Different values written twice, and the SNMP location and contact**: if different values are written twice for a field that holds only one value (the hostname, domain name, timezone, SNMP location and contact, each field of DNS, "
    "and shutdown, ip_address, description, mtu, speed and channel_group of an interface), the value of the later line is used, and the line numbers of both are put in the duplicate warning (`duplicate_line`; the value is not put. "
    "If it shares a line with a record the tree found, they become one. The same as Junos, PAN-OS and Fortinet, and the opposite of \"the first line is used\" for the hostname and the like of Cisco). "
    "In REQ-344 the one joined line of the SNMP location and contact (`snmp location …`) was on the not-sent side because of the word `snmp` and was not sent to the AI; when mapped to the IR, they are treated the same as the `snmp-server location` and `contact` of Cisco "
    "(the rule of hiding IR fields; the location and contact are not hidden) and are sent to the AI. It only matches the treatment to Cisco, and is not a rule that loosens what is sent.\n",
    "**Generic form (ADR-0234 decision 2. REQ-344)**: every node of the tree is mapped to `vendor_specific.checkpoint_gaia.config` as a set of leaves for each scope. "
    "The form is `{\"lines\": [texts of the leaves in ascending order; the same string once], \"blocks\": {\"<scope name>\": {\"lines\": […], \"blocks\": {…}}}}` (the same form as Junos). "
    "The scope name is the name of the line-head scope table (`interface eth1`, `hostname`, `static-route default`, and so on; the verb `set` or `add` is not in it). "
    "A top-level leaf is a line that matches no scope and stays uninterpreted, so the top-level `lines` is always empty. "
    "`lines` is compared by adding and removing each element, ignoring the order. A leaf that could be mapped to the common schema is not put in; only the leaves that were not mapped are.\n",
    "scope in the generic form is `scope` (a scope node) or `leaf` (a leaf under a scope). The form is a regular expression for the whole line. "
    "A key is put for a scope that got one or more leaves (or a scope of a real line with no remaining words). An implicit node (not a real line) puts nothing. "
    "A scope whose leaves were all on the not-sent side gets no key. The second and later lines of a scope-only line are not mapped (their tree position is the same as the first, so the record of being interpreted stays that of the first).\n",
    "**A line that is not sent is not put in (the same as ADR-0224 decision 6; Gaia uses two judgments)**: for each leaf, the judgment of the side that sends to the AI (`Masker().judge_alone`) is made twice. "
    "(1) The remaining part (the text of the leaf) is judged with the scope names (from the outside) as the ancestors. "
    "(2) The one line made by joining the scope names and the remaining part (for example `snmp usm user …`; the verb `set` or `add` is not added) is judged without ancestors. "
    "A leaf is put in only when both (1) and (2) are \"sent as it is\" (plain); a leaf for which either is not plain (not sent, or masked) is not put in and stays uninterpreted. "
    "(2) covers the case where a word that shows a secret, such as `snmp`, is on the side of the scope name, where the judgment does not look at it among the ancestors, "
    "and the passphrases and hashes of SNMPv3 (`add snmp usm user … auth-pass-phrase …`, `privacy-pass-hash …`) would get in. For this reason `set snmp location`, `contact` and the like are not put in the generic form either "
    "(these two leaves are mapped to snmp of the common schema first, so they are interpreted). The same two judgments are applied to the uninterpreted lines of a Gaia config in the content sent to the AI, in the masked output given to people, and in the list that config-facts gives to the AI. "
    "Words the user added in the settings are not used. A line that matches no table, such as `delete …`, also stays uninterpreted. "
    "An uninterpreted line enters `unparsed` of the IR by its tree position (`('user admin', 'password-hash <value>')`).\n",
)

_GAIA_MAP = i18n.choose(_GAIA_MAP_JA, _GAIA_MAP_EN)

_F5_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(F5 BIG-IP: f5_bigip)

> **自動生成。手で編集しない。** 写しの表(`src/driftpilot/config_parser/f5.py` の `PATTERNS`)と
> 木の組み立ての版(`src/driftpilot/config_parser/f5_tree.py` の `TREE_VERSION`)から作る。
> 再生成: `{F5_GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

F5 BIG-IP の config(ADR-0235。`tmsh list`・`tmsh show running-config`・`bigip.conf`)は、`<モジュール> <種類> <名前> {{` … `}}` の波かっこの入れ子だが、
1 行の中に波かっこを書く(`/Common/http {{ }}`・`vlans {{ /Common/a /Common/b }}`)ので、Junos・PAN-OS の波かっこの木(1 行 1 文)では読めない。
iRule・iApp のテンプレートの本文は Tcl の文で、波かっこの決まりでは読めない。そのため **F5 用の木の組み立て(`f5_tree.py`)** で読む。hier_config は使わない。
字下げは、Tcl の塊の終わりの確かめにだけ使う。

- 字句: 引用符は `"`(`\\"` では閉じない)。引用符の中の `{{`・`}}`・`#` は区切りにしない。行の終わりまでに閉じない引用符は、閉じの引用符のある行まで続けて 1 つの値として読む
  (改行を含む値は、行数と SHA-256 の頭 16 字だけの 1 語にする。PAN-OS と同じ)。ファイルの終わりまで閉じなければ構造のエラー `quote`(開いた行)。
  Tcl の塊の外で、最初の空白でない字が `#` の行(`#TMSH-VERSION: …`)と、空の行は木に入れない(版の行だけ違う 2 つの config の比較に差分は出ない)
- 1 行の入れ子: `{{` の前の語の並び(全体)が見出しのテキスト。`<語>… {{ }}` は子の無い見出し 1 つ、`<語>… {{ <語> <語> … }}`(対が 1 組だけ)は見出し 1 つと中の語 1 つずつの葉。
  ほかの `{{`・`}}` を含む行(`members {{ /Common/a {{ address 192.0.2.1 }} }}`)は、左から順に見出しを開き、`}}` の前の語の並びを 1 つの葉にして見出しを閉じる。
  行の終わりで開いたままの見出しは次の行に続く。閉じの `}}` が足りない・多いときは構造のエラー `unbalanced`(推測で閉じない)
- 最上位の 1 行のまとまり: 最上位(今開いている見出しが無い)の行で、`{{`・`}}` が行の中で全部対になる行
  (`ltm monitor http /Common/m1 {{ … }}`・`auth user … {{ … partition-access {{ … }} … }}`・`ltm rule /Common/r1 {{ when … {{ … }} }}`・中が空の `ltm pool /Common/p1 {{ }}`)は、
  見出しと葉に分けず、前後の空白を落とした行全体(語の間の空白は 1 つ)を最上位の葉 1 つにする。**中身は構造化しない**(値が 1 つ変わると、その行の削除と追加になる)。
  最上位の葉なので、重複の警告は出ない
- 入れ子の段の 1 行の形: 見出しの中の、`{{`・`}}` が行の中で全部対になる行は、行全体を AI に送る側の判定(`Masker().judge_alone`。祖先は今開いている見出しのテキスト)にかけ、
  「そのまま送る」でなければ行全体を葉 1 つにする(`credentials {{ password … }}`・`cert-key-chain {{ default {{ description x passphrase … }} }}`・`recv {{ a }} description x password …` など、
  中の語 1 つずつや `}}` の前の語の並びに分けると秘密が漏れる行)。「そのまま送る」のときだけ上の分け方をする(`vlans {{ /Common/a /Common/b }}` は葉 2 つ、`members {{ … {{ address … }} }}` は見出し → 見出し → 葉)。
  前の行から続く `}}` や次の行へ続く `{{` を含む行は、この判定を当てずに分ける。行全体の判定が「そのまま通す」(pass)になる行(`description` で始まる行など)は、ここでは防げない
  (複数行の形で単独の `description` の行を送るのと同じ扱い)
- 名前の無い項目: 前に語の無い `{{`(`cm device` の `unicast-address {{ {{ effective-ip … }} }}`・APM の `rules {{ {{ … }} }}`)は、名前の無い見出し `{{}}` として開く。
  同じ親の下の同じテキストの見出しは 1 つのノードにまとまるので、名前の無い項目も 1 つの `{{}}` にまとまる(どの葉がどの項目に属していたかは分からなくなる。行は失わない)
- Tcl の塊(行のまま持つ。中を文として読まない): 最上位の見出しが `ltm rule `・`gtm rule `・`pem irule `・`sys icall script ` で始まるもの、と、
  どの段でも見出しのテキストが `implementation`・`presentation`・`html-help`・`definition`・`macro` のもの。塊が始まるのは、見出しを開く `{{` が行の最後の字のときだけ
  (同じ行の中で閉じる形は普通の文。行の途中で開いて閉じないまま行が終わるときは構造のエラー `tcl_inline`)。次の行から、`\\` の付かない `{{` を +1、`}}` を −1 と数え
  (引用符・`#` は区別しない。Tcl の決まり)、数が 0 に戻った行で閉じる。その行が、開いた行と同じ字下げの `}}` だけの行でない・数が 0 より下がったときは構造のエラー `tcl_close`、
  ファイルの終わりまで 0 に戻らなければ `tcl_open`(開いた行)。中の行は、空の行を除き、前後の空白を落とした行を 1 行ずつ葉にする(`#` の行も葉にする)
- imish の部分: `!!! imish configuration starts here` の行から後ろの行(空の行を除く)は、F5 の木として読まず、最上位の葉(未解釈)にする。Cisco 系の読み方は当てない。
  `!!! imish configuration ends here` で始まる行があれば、その行までを imish とし、後ろは F5 の木に戻る
- 重複の警告(`duplicate_line`): F5 の見出しの直下で同じテキストの葉が 2 つ以上あるときだけ出す。Tcl の塊の中・最上位の行・名前の無い見出し `{{}}` の下(入れ子の下も)では出さない。
  どの行も捨てない。警告に値は入らない(見出しの道すじと行番号だけ)
- 木の組み立ての版 `{F5_TREE_VERSION}` は、この規則の版で、表の指紋に入る(規則を変えたら上げる)
- 表の行の備考と写し先は開発者向けのメモで、日本語のまま出す
"""

_F5_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (F5 BIG-IP: f5_bigip)

> **Generated automatically. Do not edit by hand.** It is made from the mapping table (`PATTERNS` of `src/driftpilot/config_parser/f5.py`) and
> the version of the tree building (`TREE_VERSION` of `src/driftpilot/config_parser/f5_tree.py`).
> Regenerate: `{F5_GENERATE_COMMAND}`
> If the table is changed but this file is not, the test fails.

An F5 BIG-IP config (ADR-0235; `tmsh list`, `tmsh show running-config` and `bigip.conf`) is nested braces, `<module> <kind> <name> {{` … `}}`, but
braces are also written inside one line (`/Common/http {{ }}`, `vlans {{ /Common/a /Common/b }}`), so the brace tree of Junos and PAN-OS (one statement per line) cannot read it.
The bodies of iRules and iApp templates are Tcl statements, which the brace rules cannot read. So it is read by **the tree building for F5 (`f5_tree.py`)**. hier_config is not used.
Indentation is used only to check the end of a Tcl block.

- Lexing: the quotation mark is `"` (`\\"` does not close it). A `{{`, `}}` or `#` inside quotation marks is not a delimiter. A quotation mark not closed by the end of the line continues to the line with the closing one as one value
  (a value with a newline becomes one word of the line count and the first 16 characters of the SHA-256, the same as PAN-OS). If it is not closed by the end of the file, it is a structure error `quote` (the line where it was opened).
  Outside a Tcl block, a line whose first non-space character is `#` (`#TMSH-VERSION: …`) and an empty line are not put in the tree (comparing two configs that differ only in the version line gives no difference)
- Nesting in one line: the whole run of words before a `{{` is the text of the heading. `<words>… {{ }}` is one heading without children, and `<words>… {{ <word> <word> … }}` (only one pair) is one heading with each inner word as a leaf.
  Any other line with `{{` or `}}` (`members {{ /Common/a {{ address 192.0.2.1 }} }}`) is read from left to right: a heading is opened, the words before a `}}` become one leaf, and the heading is closed.
  A heading still open at the end of a line continues on the next line. A missing or extra `}}` is a structure error `unbalanced` (nothing is closed by guessing)
- A top-level one-line group: a line at the top level (no heading is open) whose `{{` and `}}` all pair up within the line
  (`ltm monitor http /Common/m1 {{ … }}`, `auth user … {{ … partition-access {{ … }} … }}`, `ltm rule /Common/r1 {{ when … {{ … }} }}`, and an empty `ltm pool /Common/p1 {{ }}`)
  is not split into a heading and leaves; the whole line, with the surrounding spaces removed (one space between words), becomes one top-level leaf. **The inside is not structured**
  (when one value changes, it shows as the removal and the addition of that line). Being a top-level leaf, it gives no duplicate warning
- A one-line form at a nested level: a line inside a heading whose `{{` and `}}` all pair up within the line is passed whole to the judgment of the side that sends to the AI (`Masker().judge_alone`; the ancestors are the texts of the headings now open),
  and if it is not "sent as it is", the whole line becomes one leaf (a line that would leak a secret if split into one word per leaf or into the words before a `}}`, such as `credentials {{ password … }}`,
  `cert-key-chain {{ default {{ description x passphrase … }} }}` and `recv {{ a }} description x password …`). Only when it is "sent as it is" is it split as above (`vlans {{ /Common/a /Common/b }}` is two leaves,
  `members {{ … {{ address … }} }}` is heading → heading → leaf). A line with a `}}` continuing from a previous line or a `{{` continuing to the next line is split without this judgment.
  A line whose whole-line judgment is "pass" (such as a line starting with `description`) cannot be prevented here (the same treatment as sending a lone `description` line in a multi-line form)
- Unnamed items: a `{{` with no word before it (`unicast-address {{ {{ effective-ip … }} }}` of `cm device`, `rules {{ {{ … }} }}` of APM) is opened as an unnamed heading `{{}}`.
  Headings with the same text under the same parent are put together into one node, so unnamed items also become one `{{}}` (which leaf belonged to which item is no longer known. No line is lost)
- Tcl blocks (kept as lines; the inside is not read as statements): a top-level heading starting with `ltm rule `, `gtm rule `, `pem irule ` or `sys icall script `, and, at any level, a heading whose text is
  `implementation`, `presentation`, `html-help`, `definition` or `macro`. A block starts only when the `{{` that opens the heading is the last character of the line
  (a form closed in the same line is an ordinary statement; if it is opened in the middle of a line and the line ends with it still open, it is a structure error `tcl_inline`). From the next line, a `{{` without a `\\` counts +1 and a `}}` counts −1
  (quotation marks and `#` are not distinguished, which is the Tcl rule), and the block closes at the line where the count returns to 0. If that line is not a line holding only `}}` at the same indentation as the opening line, or the count goes below 0, it is a structure error `tcl_close`,
  and if it does not return to 0 by the end of the file, `tcl_open` (the line where it was opened). Each inner line, except empty ones, becomes one leaf with the surrounding spaces removed (a `#` line also becomes a leaf)
- The imish part: the lines (except empty ones) from the line `!!! imish configuration starts here` are not read as the F5 tree; each becomes a top-level leaf (uninterpreted). No Cisco-family reading is applied.
  If there is a line starting with `!!! imish configuration ends here`, the imish part runs to that line and the F5 tree resumes after it
- Duplicate warning (`duplicate_line`): given only when two or more leaves with the same text are directly under an F5 heading. It is not given inside a Tcl block, for top-level lines, or under an unnamed heading `{{}}` (including nested ones).
  No line is dropped. The warning holds no values (only the path of the heading and the line numbers)
- The tree building version `{F5_TREE_VERSION}` is the version of these rules and goes into the table fingerprint (raise it when the rules change)
- The notes and targets of the table rows are memos for developers, and are shown in Japanese as they are
"""

_F5_HEADER = i18n.choose(_F5_HEADER_JA, _F5_HEADER_EN)

_F5_MAP_JA = (
    "**汎用の形(ADR-0235 決定 2。REQ-349)**: 木の全部のノードを `vendor_specific.f5_bigip.config` に、見出しごとの葉の集合で写す。"
    "形は `{\"lines\": [最上位の葉の昇順。同じ文字列は 1 つ], \"blocks\": {\"<見出しのテキスト>\": 同じ形}}`(Junos・Check Point Gaia と同じ)。"
    "見出しは行そのもの(`ltm virtual /Common/vs-test1`・`profiles`・`/Common/http`)で、語を削らない。`lines` は並びを無視して要素ごとの追加・削除で比べる。"
    "よく使う物は、先に共通スキーマ(IR。global・interfaces・vlans・static_routes)へ写す(下の「IR に写す物」。REQ-350)。機種の自動判定は REQ-351、端末ログは REQ-352。\n",
    "scope は `top`(最上位の見出しで、IR に写す種類を決める行)・`<種類>`(その見出しの子の行)・`<種類>:block`(入れ子の見出しの行)・"
    "`block`(汎用の形の見出しのノード)・`leaf`(汎用の形の葉のノード)。書式は行全体に対する正規表現。\n",
    "**IR に写す物(ADR-0235 決定 3。REQ-350)**: 写した行は解釈済みにし、汎用の形には入れない。IR の欄は足さない。名前(`/Common/vlan-test1` など)は `/Common/` を含めた原文のまま使う。"
    "アドレスの後ろの `%<数>`(ルートドメイン)は外した値をアドレスにし、`<数>` を vrf の欄に持つ(欄がある項目だけ。`<A>%<数>/<長さ>` と `<A>/<長さ>%<数>` の両方を受ける)。"
    "値を 1 つしか持てない欄に違う値が 2 回あれば、後の行の値を使い、食い違った行の番号を重複の警告に出す(値は出さない。Gaia と同じ)。"
    "写した行は、行全体の判定によらず IR に入る(IR の欄の伏せ方は今の決まり。写す欄に秘密は無い)。1 行で閉じるまとまり(木が行全体を 1 つの葉にしたもの)は写さない。"
    "形に合わない子の行(アドレスや長さとして読めない値・範囲の外の数・ホストの部分が 0 でない宛先・宛先と gw で IPv4・IPv6 の種類が違う経路・network と gw の `%<数>` が違う経路・"
    "vrf の欄の無い dns・syslog・管理 IP の `%` 付きのアドレス)は写さず、汎用の形か未解釈のまま。2 つの子がそろって写るもの"
    "(Self IP の `address` と `vlan`・経路の `network` と `gw`・syslog の `host`)は、そろったときだけ写す。\n",
    "**IR に写す物の一覧**: 全体は `sys global-settings` の hostname・`sys ntp` の servers(書かれた順。重ねない)と timezone・`sys dns` の name-servers(正規の書き方の昇順)・"
    "`sys syslog` の remote-servers の下の host(`remote-port` があれば付ける)・`sys snmp` の sys-location・sys-contact(両端の `\"` を外す)。"
    "管理 IP は `sys management-ip <A>/<長さ>` を Interface `mgmt`(type は routed)に、インターフェースは `net interface <名前>`(type は switchport。media-fixed → speed・mtu・description・disabled/enabled → shutdown)。"
    "VLAN は `net vlan <名前>` の `tag` を vlans[\"<N>\"](name は名前)に、`mtu`・`description` を同じ名前の Interface(type は svi)に。"
    "Self IP は `net self <名前>` の `address` と `vlan` を Interface `<VLAN の名前>`(type は svi)に、全部読んだあとに Self IP の名前の昇順で当てる"
    "(floating でない IPv4 は ip_address、2 つ目からは secondary_addresses、floating は virtual_addresses、floating でない IPv6 は ipv6_addresses)。"
    "経路は `net route <名前>` の `network`(`default`・`default-inet6` も)と `gw` を 1 本に(見出しの出た順。`gw` の無い経路は写さない)。\n",
    "**IR に写す物の決まり(依頼書に足した決まり)**: (1) F5 で作る Interface の parent_interface はいつも None(名前の `.` から親を推定しない。FortiGate と同じ)。"
    "(2) `net interface mgmt` と `sys management-ip` は 1 つの Interface `mgmt` にまとまり、管理 IP があれば type は書かれた順によらず routed(種類の食い違いの警告は出さない)。"
    "(3) IPv6 の管理 IP は mgmt の ipv6_addresses に入る。(4) floating の Self IP は IPv6 でも virtual_addresses に入り、ipv6_addresses には floating でないものだけが入る。"
    "(5) `sys snmp` の見出しは送らない側の判定なので、見出しもほかの子も未解釈のまま。sys-location・sys-contact の子だけを写す。\n",
    "**見出しの鍵**: 見出しは、判定が通れば鍵を置く。子の葉が全部送らない側でも `{\"lines\": [], \"blocks\": {}}` で置く"
    "(見出しは実在の行なので、子の無い見出し `/Common/http { }` の有る無しも差分に出る)。同じ親の下の同じテキストの見出しは木でまとまっているので、鍵は 1 つ。"
    "**例外(REQ-350)**: IR に写す子を持つ見出し(`net route`・`sys ntp`・`net self` など。見出しそのものを写す `net interface`・`sys management-ip` も)は、"
    "写さなかった子が残るとき(`traffic-group` など)だけ鍵を置き、写した行は汎用の形に入れない(見出しの行も解釈済みにする)。"
    "子が 1 つも写らなかった見出しは、今までのとおり判定が通れば鍵を置く。残る注意: 経路・Self IP の名前は IR に欄が無いので、写した分は構造化の比べる対象から外れる"
    "(名前だけを変えても構造化の差分は出ない。単純比較の差分には出る)。\n",
    "**1 行のまとまり**: 最上位の 1 行のまとまり(`ltm monitor http /Common/m1 { … }` など、同じ行の中で閉じる行)は、行全体を最上位の葉 1 つにして、最上位の `lines` に入れる"
    "(中身は構造化しない。値が変わると、行の削除と追加になる。重複の警告は出ない)。入れ子の段の 1 行の形は、行全体の判定が「そのまま送る」のときだけ見出しと葉に分け、"
    "そうでなければ行全体を葉 1 つにして、見出しの `lines` に入れる(`credentials { password … }` など。判定が「そのまま送る」でなければ入れず、未解釈のまま)。"
    "行全体の判定が pass になる行(`description` で始まる行など)は、木の組み立てでは防げない。\n",
    "**iRule・iApp の本文と名前の無い項目**: Tcl の塊の行も、名前の無い見出し `{}` の中の葉も、ほかの行と同じ判定を通ってから入る。"
    "名前の無い項目は 1 つの `{}` にまとまるので、どの葉がどの項目に属していたかは、汎用の形では分からなくなる"
    "(例: APM の `rules` の 2 つの項目の間で葉を入れ替えても、汎用の形の差分は出ない。行は失わず、単純比較の差分には出る)。\n",
    "**送らない行は入れない(ADR-0224 決定 6 と同じ)**: 葉のテキストを AI に送る側の判定(`Masker().judge_alone(テキスト, 祖先)`。祖先は見出しのテキストで外側から順)に"
    "かけ、「そのまま送る」(plain)でない行(送らない・伏せる行。`auth user …` の暗号化したパスワード・`community-name`・iRule の `set api_token \"…\"`・`$M$…` の鍵など)は入れず、"
    "未解釈のままにする。見出しのテキストも同じく判定し、plain でなければ、その中の行も読まずに未解釈に残す。"
    "F5 の見出しは行そのもの(語を削らない)なので、行頭スコープ表の機種の二重の判定は当てない。利用者が設定で足した語は使わない。"
    "imish の部分の最上位の葉は未解釈のまま。未解釈の行は木の位置(見出しのテキストの並び)で IR の `unparsed` に入る。\n",
)

_F5_MAP_EN = (
    "**Generic form (ADR-0235 decision 2. REQ-349)**: every node of the tree is mapped to `vendor_specific.f5_bigip.config` as a set of leaves for each heading. "
    "The form is `{\"lines\": [top-level leaves in ascending order, one for each distinct string], \"blocks\": {\"<heading text>\": the same form}}` (the same as Junos and Check Point Gaia). "
    "A heading is the line itself (`ltm virtual /Common/vs-test1`, `profiles`, `/Common/http`); no word is removed. `lines` is compared by the addition and removal of each element, ignoring the order. "
    "The commonly used items are first mapped to the common schema (the IR: global, interfaces, vlans, static_routes; see \"Items mapped to the IR\" below; REQ-350). The automatic device detection is REQ-351 and the terminal logs are REQ-352.\n",
    "scope is `top` (a top-level heading; the row that decides the kind mapped to the IR), `<kind>` (a row for a child of that heading), `<kind>:block` (a row for a nested heading), "
    "`block` (a heading node of the generic form) or `leaf` (a leaf node of the generic form). The form is a regular expression for the whole line.\n",
    "**Items mapped to the IR (ADR-0235 decision 3. REQ-350)**: a mapped line is marked as interpreted and is not put in the generic form. No field is added to the IR. A name (such as `/Common/vlan-test1`) is used as written, including `/Common/`. "
    "A `%<number>` (route domain) after an address is removed to make the address, and `<number>` goes into the vrf field (only for items that have the field; both `<A>%<number>/<length>` and `<A>/<length>%<number>` are accepted). "
    "If a field that holds one value gets different values twice, the value of the later line is used and the numbers of the conflicting lines are given in a duplicate warning (no value is given; the same as Gaia). "
    "A mapped line goes into the IR regardless of the whole-line judgment (the IR fields are masked by the current rules; the mapped fields hold no secret). A one-line group that closes within a line (which the tree made one leaf of the whole line) is not mapped. "
    "A child line of a form that does not fit (a value that cannot be read as an address or a length, a number out of range, a destination whose host part is not 0, a route whose destination and gw differ in IPv4/IPv6, "
    "a route whose `%<number>` of network and gw differ, and an address with `%` in dns, syslog or a management IP, which have no vrf field) is not mapped and stays in the generic form or uninterpreted. "
    "Items that are mapped only when two children are present (`address` and `vlan` of a Self IP, `network` and `gw` of a route, `host` of syslog) are mapped only when they are all present.\n",
    "**List of items mapped to the IR**: for global, hostname of `sys global-settings`, servers of `sys ntp` (in written order, without duplicates) and timezone, name-servers of `sys dns` (canonical form, ascending), "
    "host under remote-servers of `sys syslog` (with `remote-port` if present), and sys-location and sys-contact of `sys snmp` (the `\"` at both ends removed). "
    "A management IP is `sys management-ip <A>/<length>` to Interface `mgmt` (type routed), and an interface is `net interface <name>` (type switchport; media-fixed to speed, mtu, description, disabled/enabled to shutdown). "
    "A VLAN is `tag` of `net vlan <name>` to vlans[\"<N>\"] (name is the name), and `mtu` and `description` to the Interface of the same name (type svi). "
    "A Self IP is `address` and `vlan` of `net self <name>` to Interface `<VLAN name>` (type svi), applied after everything is read in ascending order of the Self IP name "
    "(a non-floating IPv4 is ip_address, from the second secondary_addresses, a floating one is virtual_addresses, and a non-floating IPv6 is ipv6_addresses). "
    "A route is `network` (also `default` and `default-inet6`) and `gw` of `net route <name>` as one route (in the order the headings appear; a route without `gw` is not mapped).\n",
    "**Rules for the items mapped to the IR (rules added to the request)**: (1) parent_interface of an Interface made for F5 is always None (the parent is not guessed from a `.` in the name; the same as FortiGate). "
    "(2) `net interface mgmt` and `sys management-ip` are put together into one Interface `mgmt`, and if there is a management IP its type is routed regardless of the written order (no warning about a conflict of types). "
    "(3) An IPv6 management IP goes into ipv6_addresses of mgmt. (4) A floating Self IP goes into virtual_addresses even for IPv6, and ipv6_addresses holds only non-floating ones. "
    "(5) The heading `sys snmp` is judged to be on the not-sent side, so the heading and the other children stay uninterpreted. Only the children sys-location and sys-contact are mapped.\n",
    "**Keys of headings**: a heading gets a key when its judgment passes. Even if all its leaves are on the not-sent side it is put as `{\"lines\": [], \"blocks\": {}}` "
    "(a heading is a real line, so whether a heading without children such as `/Common/http { }` exists also shows as a difference). Headings with the same text under the same parent are already put together by the tree, so there is one key. "
    "**Exception (REQ-350)**: a heading that has children mapped to the IR (such as `net route`, `sys ntp` and `net self`; also `net interface` and `sys management-ip`, whose heading itself is mapped) "
    "gets a key only when children that were not mapped remain (such as `traffic-group`), and the mapped lines are not put in the generic form (the line of the heading is interpreted too). "
    "A heading none of whose children was mapped gets a key as before when its judgment passes. A remaining caution: the names of routes and Self IPs have no field in the IR, so the mapped ones are outside the structured comparison "
    "(changing only a name gives no structured difference; the plain comparison shows it).\n",
    "**One-line groups**: a top-level one-line group (a line that closes within the same line, such as `ltm monitor http /Common/m1 { … }`) becomes one top-level leaf of the whole line and goes into the top-level `lines` "
    "(the inside is not structured; when a value changes, it is the removal and the addition of the line; no duplicate warning is given). A one-line form at a nested level is split into a heading and leaves only when the whole-line judgment is \"sent as it is\"; "
    "otherwise the whole line becomes one leaf and goes into the `lines` of the heading (such as `credentials { password … }`; if the judgment is not \"sent as it is\" it is not put in and stays uninterpreted). "
    "A line whose whole-line judgment is pass (such as a line starting with `description`) cannot be prevented by the tree building.\n",
    "**Bodies of iRules and iApps, and unnamed items**: the lines of a Tcl block and the leaves inside an unnamed heading `{}` also go in after the same judgment as other lines. "
    "Unnamed items are put together into one `{}`, so which leaf belonged to which item is no longer known in the generic form "
    "(for example, swapping leaves between two items of `rules` of APM gives no difference in the generic form. No line is lost, and the plain comparison shows it).\n",
    "**A line that is not sent is not put in (the same as ADR-0224 decision 6)**: the text of a leaf is passed to the judgment of the side that sends to the AI (`Masker().judge_alone(text, ancestors)`; the ancestors are the heading texts from the outside in), "
    "and a line that is not \"sent as it is\" (plain) (a line not sent or masked, such as an encrypted password of `auth user …`, `community-name`, `set api_token \"…\"` of an iRule and a `$M$…` key) is not put in and "
    "stays uninterpreted. The text of a heading is judged in the same way, and if it is not plain, the lines inside it are not read either and stay uninterpreted. "
    "An F5 heading is the line itself (no word is removed), so the double judgment of the machines with a line-head scope table is not applied. Words the user added in the settings are not used. "
    "The top-level leaves of the imish part stay uninterpreted. An uninterpreted line enters `unparsed` of the IR by its tree position (the run of heading texts).\n",
)

_F5_MAP = i18n.choose(_F5_MAP_JA, _F5_MAP_EN)

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
    "global の hostname・dns・ntp_options・vlans・vendor_specific の firewall・config・header)\n",
    "yamaha_map_title": "## IR への写し(interfaces・static_routes・vendor_specific の settings・pp・tunnel)\n",
    "junos_map_title": "## IR への写し(global・interfaces・static_routes・bgp・prefix_lists・route_maps・acls・vendor_specific の config)\n",
    "panos_map_title": "## IR への写し(global・interfaces・static_routes・vendor_specific の config)\n",
    "gaia_map_title": "## IR への写し(global・interfaces・static_routes・vendor_specific の config)\n",
    "f5_map_title": "## IR への写し(global・interfaces・vlans・static_routes・vendor_specific の config)\n",
    "scoped_fingerprint": "表の指紋: `{digest}`({count} 件。行頭スコープ表 {scopes} 件 ＋ 写しの表 {patterns} 件。"
    "木の組み立ての版 `{tree_version}` を含む)。保存した比較結果の `tools.patterns_fingerprint` と突き合わせる(docs/notes/req-14-storage.md)。\n",
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
    "hostname, dns and ntp_options of global, vlans, and firewall, config and header of vendor_specific)\n",
    "yamaha_map_title": "## Mapping to the IR (interfaces, static_routes, and settings, pp and tunnel of vendor_specific)\n",
    "junos_map_title": "## Mapping to the IR (global, interfaces, static_routes, bgp, prefix_lists, route_maps, acls, and config of vendor_specific)\n",
    "panos_map_title": "## Mapping to the IR (global, interfaces, static_routes, and config of vendor_specific)\n",
    "gaia_map_title": "## Mapping to the IR (global, interfaces, static_routes, and config of vendor_specific)\n",
    "f5_map_title": "## Mapping to the IR (global, interfaces, vlans, static_routes, and config of vendor_specific)\n",
    "scoped_fingerprint": "Table fingerprint: `{digest}` ({count} rows. {scopes} line-head scope rows + {patterns} mapping rows. "
    "Includes the tree building version `{tree_version}`). Compare it with `tools.patterns_fingerprint` of a saved comparison result (docs/notes/req-14-storage.md).\n",
}
_DOC = i18n.choose(_DOC_JA, _DOC_EN)


# build を持つ機種ごとの (見出し, 写しの見出しの鍵, 案内の段落)
_BUILT_DOCS = {
    "fortinet": (_FORTINET_HEADER, "fortinet_map_title", _FORTINET_MAP),
    "yamaha": (_YAMAHA_HEADER, "yamaha_map_title", _YAMAHA_MAP),
    "juniper_junos": (_JUNOS_HEADER, "junos_map_title", _JUNOS_MAP),
    "paloalto_panos": (_PANOS_HEADER, "panos_map_title", _PANOS_MAP),
    "checkpoint_gaia": (_GAIA_HEADER, "gaia_map_title", _GAIA_MAP),
    "f5_bigip": (_F5_HEADER, "f5_map_title", _F5_MAP),
}


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
    """木の組み立ての関数(build)を持つ機種(D型・ヤマハ・Junos・PAN-OS・Check Point Gaia)の一覧。木の組み立ての規則と写しの表。
    見出し・案内・節の見出しは機種ごとの表で引く。行頭スコープ表を持つ機種(Check Point Gaia)だけ、写しの表の前にスコープ表を出す。"""
    flat = FLAT_PLATFORMS[platform]
    fp = patterns_fingerprint(platform)
    header, map_title, map_paragraphs = _BUILT_DOCS[platform]
    if flat.scopes:  # 行頭スコープ表を持つ機種(Check Point Gaia)は、指紋の行にスコープ表の件数も出し、写しの表の前にスコープ表を出す
        out = [
            header,
            _DOC["scoped_fingerprint"].format(
                digest=fp.digest, count=fp.count, scopes=len(flat.scopes), patterns=len(flat.patterns),
                tree_version=flat.tree_version,
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
        out += ["", _DOC[map_title], *map_paragraphs, _PATTERN_DOC["table_head"], "|---|---|---|---|---|---|"]
    else:
        out = [
            header,
            # 指紋の行の文は、写しの表の件数と木の組み立ての版を出す形で機種に依らない(FortiGate の出力は変えない)
            _DOC["fortinet_fingerprint"].format(
                digest=fp.digest, count=fp.count, patterns=len(flat.patterns), tree_version=flat.tree_version
            ),
            _DOC[map_title],
            *map_paragraphs,
            _PATTERN_DOC["table_head"],
            "|---|---|---|---|---|---|",
        ]
    for p in flat.patterns:
        match = _code(p.match) if p.match is not None else _PATTERN_DOC["no_line"]
        out.append(f"| `{p.id}` | {_cell(p.scope)} | {match} | {_cell(p.target)} | {_code(p.example)} | {_cell(p.note)} |")
    return "\n".join(out).rstrip("\n") + "\n"
