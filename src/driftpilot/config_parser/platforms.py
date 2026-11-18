"""機種名から config のパーサーの系統を選ぶ(ADR-0024 決定6、REQ-21 §10)。

kind=config の `--platform` は「既定でないパーサーの系統を選ぶ」指定である。既定は A型(hier_config)で、A型には指定が要らない。
- 省略・cisco_ios・ntc-templates の名前: A型(今までどおり。config としての意味を持たない)
- fujitsu_sir: B型(平坦 config。行頭スコープ表)
- fortinet: D型(FortiGate。終端記号の対で木を組む。ADR-0025)。木の部品は B型と同じ
- yamaha: B型(ヤマハ RTX。`pp select`・`tunnel select` の範囲で木を組み、範囲ごとの行の集合に写す。ADR-0213)。木の部品は B型と同じ
- juniper_junos: B型(Juniper Junos。brace〔show configuration〕と set〔display set〕を同じ 2 段の文の木〔最上位の語 → 残りの語の文〕に組み、
  global・interfaces・static_routes・bgp・prefix_lists・route_maps・acls のよく使う文を共通スキーマに写し、
  ほかは最上位の語ごとの文の集合に写す。ADR-0224)。形は中身から見分ける。ospf などの写しは後の依頼書。木の部品は B型と同じ。
  指紋に入る木の版は junos_tree の TREE_VERSION の 1 つだけ(brace_tree・junos_set_tree の版と見分けの規則・木の作り方をまとめた版。
  どれかを変えたらこの版を上げる)
- paloalto_panos: B型(Palo Alto PAN-OS。XML・set・brace を同じ 2 段の文の木〔最上位の語 → 残りの語の文〕に組み、全部の文を
  vendor_specific["paloalto_panos"]["config"] の最上位の語ごとの文の集合に写す〔汎用の形だけ。共通スキーマへの写しは後の依頼書〕。ADR-0226)。
  形は中身から見分ける。XML は自前の走査で読み(panos_xml)、複数行の値は <複数行の値 行数 sha256 の頭 16 字> の 1 語にする。
  指紋に入る木の版は panos_tree の TREE_VERSION の 1 つだけ(brace_tree の版と見分けの規則・XML と set の読み方・木の作り方をまとめた版)。
  ntc-templates の show の機種名も paloalto_panos で、kind=show(--show-command つき)の道は変えない
- checkpoint_gaia: B型(Check Point Gaia。`show configuration` の字下げの無い `set <対象> …`・`add <対象> …` の行を、Si-R と同じ行頭スコープ表
  〔GAIA_SCOPES。動詞 set・add はスコープ名に入れない〕で木にし、`#` の行は木に入れない。よく使う葉は interfaces・static_routes・global に写し
  〔REQ-345〕、写さなかった葉を vendor_specific["checkpoint_gaia"]["config"] のスコープごとの葉の集合に写す〔汎用の形〕。ADR-0234)。
  build は `#` の行を外してから build_flat_tree を呼ぶ関数。指紋に入る木の版は gaia の TREE_VERSION。
  ntc-templates の show の機種名も checkpoint_gaia で、kind=show(--show-command つき)の道は変えない
- f5_bigip: B型(F5 BIG-IP。`tmsh list`・`tmsh show running-config`・`bigip.conf` の波かっこの入れ子を、1 行に波かっこを書く形〔`/Common/http { }`・
  `vlans { /Common/a /Common/b }`〕と iRule・iApp の Tcl の本文〔行のまま持つ塊〕も読める F5 用の木〔f5_tree〕に組み、全部の行を
  よく使う物〔ホスト名・NTP・DNS・syslog・SNMP の場所と連絡先・管理 IP・インターフェース・VLAN・Self IP・経路〕を共通スキーマに写し〔REQ-350〕、
  写さなかった行を vendor_specific["f5_bigip"]["config"] の見出しごとの葉の集合に写す〔汎用の形〕。自動判定は REQ-351。端末ログは REQ-352。ADR-0235)。
  行頭スコープ表は無い。imish の部分は読まずに未解釈。指紋に入る木の版は f5_tree の TREE_VERSION
- fujitsu_srs: 表が未作成のため UnsupportedConfigPlatform
sniff では機種を当てない。中身からの系統の見立て(family_hint)は安全網5 の警告に使う(ADR-0027)。
機種を「自動」(auto)にしたときだけ、core が family_hint.detect_platform で選んだ名前をここに渡す(ADR-0151)。
自動でなければ選ぶのは利用者で、SR-S と Si-R の判別もしない。
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from driftpilot import i18n
from driftpilot.config_parser import f5, fortinet, fujitsu_sir, gaia, junos, panos, registry, yamaha
from driftpilot.config_parser.f5_tree import TREE_VERSION as F5_TREE_VERSION
from driftpilot.config_parser.f5_tree import build_f5_tree
from driftpilot.config_parser.flat_scopes import GAIA_SCOPES, SIR_SCOPES, FlatScope
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatTree
from driftpilot.config_parser.fortinet_tree import TREE_VERSION as FORTINET_TREE_VERSION
from driftpilot.config_parser.fortinet_tree import build_fortinet_tree, embedded_lines
from driftpilot.config_parser.ir import (
    Acl,
    Bgp,
    Global,
    Interface,
    Ipsec,
    Ospf,
    PrefixList,
    RouteMap,
    SdwanBlock,
    StaticRoute,
    Vlan,
)
from driftpilot.config_parser.junos_tree import TREE_VERSION as JUNOS_TREE_VERSION
from driftpilot.config_parser.junos_tree import build_junos_tree
from driftpilot.config_parser.panos_tree import TREE_VERSION as PANOS_TREE_VERSION
from driftpilot.config_parser.panos_tree import build_panos_tree
from driftpilot.config_parser.patterns import Pattern
from driftpilot.config_parser.registry import PatternsFingerprint
from driftpilot.config_parser.tracking import InterpretedLines
from driftpilot.config_parser.yamaha_tree import TREE_VERSION as YAMAHA_TREE_VERSION
from driftpilot.config_parser.yamaha_tree import build_yamaha_tree


class UnsupportedConfigPlatform(ValueError):
    """config のパーサーの表がまだ無い機種(code=unsupported_config_platform)。"""


@dataclass(frozen=True)
class FlatPlatform:
    """FlatTree で読む機種1つ。scopes は行頭スコープ表、patterns は IR への写しの表、extract はその表で写す extractor。

    build は木を組む関数(D型・ヤマハ・Junos・PAN-OS・F5 は行頭スコープ表を使わない。Check Point Gaia は中で行頭スコープ表を使う)。None なら build_flat_tree(B型)。
    tree_version は build の規則の版で、表の指紋に入る(build が無い機種では None。指紋を動かさない)。
    protected_lines は、機種の正規化ルールを当てない行(行をまたぐ値の本文など)を返す関数。None なら全行に当てる。
    family は系統(b=平坦 config、d=FortiGate)。安全網5 の見立てとの突き合わせだけに使い、指紋には入れない。
    """

    name: str
    scopes: tuple[FlatScope, ...]
    patterns: tuple[Pattern, ...]
    extract: Callable[
        [FlatTree, InterpretedLines],
        tuple[
            dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
            dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Vlan], dict[str, Acl],
            dict[str, Any], list[DuplicateLine],
        ],
    ]
    build: Callable[[str], FlatTree] | None = None
    tree_version: str | None = None
    protected_lines: Callable[[str], frozenset[int]] | None = None
    family: Literal["b", "d"] = "b"


FLAT_PLATFORMS: dict[str, FlatPlatform] = {
    "fujitsu_sir": FlatPlatform("fujitsu_sir", SIR_SCOPES, fujitsu_sir.PATTERNS, fujitsu_sir.extract),
    "fortinet": FlatPlatform(
        "fortinet",
        (),
        fortinet.PATTERNS,
        fortinet.extract,
        build_fortinet_tree,
        FORTINET_TREE_VERSION,
        embedded_lines,
        family="d",
    ),
    "yamaha": FlatPlatform(
        "yamaha", (), yamaha.PATTERNS, yamaha.extract, build_yamaha_tree, YAMAHA_TREE_VERSION, None, family="b"
    ),
    "juniper_junos": FlatPlatform(
        "juniper_junos", (), junos.PATTERNS, junos.extract, build_junos_tree, JUNOS_TREE_VERSION, None, family="b"
    ),
    "paloalto_panos": FlatPlatform(
        "paloalto_panos", (), panos.PATTERNS, panos.extract, build_panos_tree, PANOS_TREE_VERSION, None, family="b"
    ),
    "checkpoint_gaia": FlatPlatform(
        "checkpoint_gaia", GAIA_SCOPES, gaia.PATTERNS, gaia.extract, gaia.build_gaia_tree, gaia.TREE_VERSION, None, family="b"
    ),
    "f5_bigip": FlatPlatform("f5_bigip", (), f5.PATTERNS, f5.extract, build_f5_tree, F5_TREE_VERSION, None, family="b"),
}
# 系統は B型だが表が無い機種 → 理由
_UNSUPPORTED_CONFIG_PLATFORMS_JA: dict[str, str] = {
    "fujitsu_srs": "fujitsu_srs の config の表はまだありません(実機の config 待ち。ADR-0024 Action Item 5)",
}
_UNSUPPORTED_CONFIG_PLATFORMS_EN: dict[str, str] = {
    "fujitsu_srs": "There is no config table for fujitsu_srs yet (waiting for a config from a real device. ADR-0024 Action Item 5)",
}
UNSUPPORTED_CONFIG_PLATFORMS: dict[str, str] = i18n.choose(_UNSUPPORTED_CONFIG_PLATFORMS_JA, _UNSUPPORTED_CONFIG_PLATFORMS_EN)
# kind=config で意味を持つ名前(「--show-command と両方指定」の検査を外すのはこれだけ)
CONFIG_PLATFORM_NAMES = frozenset(FLAT_PLATFORMS) | frozenset(UNSUPPORTED_CONFIG_PLATFORMS)
# 利用者が選べる config の系統(CLI の --platform の choices と GUI の機種の選択肢)。表が未作成の機種は含めない
CONFIG_PLATFORMS = tuple(sorted(FLAT_PLATFORMS))


def flat_platform(platform: str | None) -> FlatPlatform | None:
    """kind=config のときの系統。None なら A型。表が無い機種は UnsupportedConfigPlatform。"""
    if platform in UNSUPPORTED_CONFIG_PLATFORMS:
        raise UnsupportedConfigPlatform(UNSUPPORTED_CONFIG_PLATFORMS[platform])
    return FLAT_PLATFORMS.get(platform or "")


def config_patterns(platform: str | None) -> tuple[Pattern, ...]:
    """被覆率のパターン一覧に使う写しの表。A型は ALL_PATTERNS。"""
    flat = flat_platform(platform)
    return registry.ALL_PATTERNS if flat is None else flat.patterns


def patterns_fingerprint(platform: str | None) -> PatternsFingerprint:
    """使った表の指紋(ADR-0017 決定2)。A型は registry の指紋そのまま(Cisco の指紋を動かさない)。

    B型は行頭スコープ表の id / match / name / expand / parent と、写しの表の id / scope / match / target を
    表の順に並べてハッシュする。example・note は含めない。count は2つの表の件数の和。
    D型は木の組み立ての版(tree_version)も含める(B型には無いキーなので、Si-R の指紋は変わらない)。
    """
    flat = flat_platform(platform)
    if flat is None:
        return registry.patterns_fingerprint()
    items: dict[str, Any] = {
        "scopes": [[s.id, s.match, s.name, list(s.expand), s.parent] for s in flat.scopes],
        "patterns": [[p.id, p.scope, p.match, p.target] for p in flat.patterns],
    }
    if flat.tree_version is not None:
        items["tree"] = flat.tree_version
    text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    count = len(flat.scopes) + len(flat.patterns)
    return PatternsFingerprint(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], count)


for _flat in FLAT_PLATFORMS.values():
    registry.validate(_flat.patterns)
