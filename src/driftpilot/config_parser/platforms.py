"""機種名から config のパーサーの系統を選ぶ(ADR-0024 決定6、REQ-21 §10)。

kind=config の `--platform` は「既定でないパーサーの系統を選ぶ」指定である。既定は A型(hier_config)で、A型には指定が要らない。
- 省略・cisco_ios・ntc-templates の名前: A型(今までどおり。config としての意味を持たない)
- fujitsu_sir: B型(平坦 config。行頭スコープ表)
- fortinet: D型(FortiGate。終端記号の対で木を組む。ADR-0025)。木の部品は B型と同じ
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
from driftpilot.config_parser import fortinet, fujitsu_sir, registry
from driftpilot.config_parser.flat_scopes import SIR_SCOPES, FlatScope
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatTree
from driftpilot.config_parser.fortinet_tree import TREE_VERSION as FORTINET_TREE_VERSION
from driftpilot.config_parser.fortinet_tree import build_fortinet_tree, embedded_lines
from driftpilot.config_parser.ir import (
    Bgp,
    Global,
    Interface,
    Ipsec,
    Ospf,
    PrefixList,
    RouteMap,
    SdwanBlock,
    StaticRoute,
)
from driftpilot.config_parser.patterns import Pattern
from driftpilot.config_parser.registry import PatternsFingerprint
from driftpilot.config_parser.tracking import InterpretedLines


class UnsupportedConfigPlatform(ValueError):
    """config のパーサーの表がまだ無い機種(code=unsupported_config_platform)。"""


@dataclass(frozen=True)
class FlatPlatform:
    """FlatTree で読む機種1つ。scopes は行頭スコープ表、patterns は IR への写しの表、extract はその表で写す extractor。

    build は行頭スコープ表を使わずに木を組む関数(D型)。None なら build_flat_tree(B型)。
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
            dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Any],
            list[DuplicateLine],
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
