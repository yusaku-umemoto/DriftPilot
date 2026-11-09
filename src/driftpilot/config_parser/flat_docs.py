"""平坦 config(B型)の表から対応一覧(Markdown / JSON)を生成する(ADR-0024、REQ-21)。

parser-patterns.md(pattern_docs.py)と同じ運用。docs/notes/flat-patterns.md はこの出力そのもので、ずれたらテストが失敗する。
内容は、行頭スコープ表(FLAT-NN)・IR への写しの表・指紋の3つ。
FortiGate(D型。REQ-22)は行頭スコープ表を持たないため、木の組み立ての規則・写しの表・指紋を出す(docs/notes/fortinet-patterns.md)。
"""

import json
from typing import Any

from driftpilot.config_parser.flat_scopes import FlatScope
from driftpilot.config_parser.fortinet_tree import EMBEDDED, HASH_INPUT_VERSION
from driftpilot.config_parser.pattern_docs import _cell, _code, _record
from driftpilot.config_parser.platforms import FLAT_PLATFORMS, patterns_fingerprint

GENERATE_COMMAND = "driftpilot patterns --platform fujitsu_sir --output docs/notes/flat-patterns.md"
FORTINET_GENERATE_COMMAND = "driftpilot patterns --platform fortinet --output docs/notes/fortinet-patterns.md"

_HEADER = f"""<!-- 自動生成。手で編集しない。 -->
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

_FORTINET_HEADER = f"""<!-- 自動生成。手で編集しない。 -->
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
        f"表の指紋: `{fp.digest}`({fp.count} 件。行頭スコープ表 {len(flat.scopes)} 件 ＋ 写しの表 {len(flat.patterns)} 件)。"
        "保存した比較結果の `tools.patterns_fingerprint` と突き合わせる(docs/notes/req-14-storage.md)。\n",
        "## 行頭スコープ表\n",
        "| ID | platform | 書式(スコープ部分) | スコープ名 | 親 | 展開する添字 | 例 | 備考 |",
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
        "## IR への写し(interfaces・static_routes・bgp・vendor_specific の remotes・scopes・settings)\n",
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
        "| ID | scope | 書式 | 写し先 | 例 | 備考 |",
        "|---|---|---|---|---|---|",
    ]
    for p in flat.patterns:
        match = _code(p.match) if p.match is not None else "(行は読まない)"
        out.append(f"| `{p.id}` | {_cell(p.scope)} | {match} | {_cell(p.target)} | {_code(p.example)} | {_cell(p.note)} |")
    return "\n".join(out).rstrip("\n") + "\n"


def _render_built(platform: str) -> str:
    """行頭スコープ表を持たない機種(D型)の一覧。木の組み立ての規則と写しの表。"""
    flat = FLAT_PLATFORMS[platform]
    fp = patterns_fingerprint(platform)
    out = [
        _FORTINET_HEADER,
        f"表の指紋: `{fp.digest}`({fp.count} 件。写しの表 {len(flat.patterns)} 件。木の組み立ての版 `{flat.tree_version}` を含む)。"
        "保存した比較結果の `tools.patterns_fingerprint` と突き合わせる(docs/notes/req-14-storage.md)。\n",
        "## IR への写し(interfaces・static_routes・bgp・ospf・ipsec・sdwan・route_maps・prefix_lists・ipv6_prefix_lists・"
        "global の hostname・dns・ntp_options・vendor_specific の firewall・config・header)\n",
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
        "| ID | scope | 書式 | 写し先 | 例 | 備考 |",
        "|---|---|---|---|---|---|",
    ]
    for p in flat.patterns:
        match = _code(p.match) if p.match is not None else "(行は読まない)"
        out.append(f"| `{p.id}` | {_cell(p.scope)} | {match} | {_cell(p.target)} | {_code(p.example)} | {_cell(p.note)} |")
    return "\n".join(out).rstrip("\n") + "\n"
