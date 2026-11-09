"""全 extractor のパターン表を1つに集める(ADR-0011)。

並びは parse_config の実行順(interfaces → vlans → acls → ospf(ospfv3 も) → bgp → spanning_tree → vrfs → static_routes
→ lines → global → certificates → device_settings → management → trustpoints → qos → eigrp → ipsec → route_policy
→ vxlan → mlag → isis → bfd → route_lists → traffic_monitoring → multicast → sdwan → mpls_ptp)。
各表の中の並びは優先順位なので変えない。
"""

import hashlib
import json
from dataclasses import dataclass

from driftpilot.config_parser import (
    acls,
    bfd,
    bgp,
    certificates,
    device_settings,
    eigrp,
    global_,
    interfaces,
    ipsec,
    isis,
    lines,
    management,
    mlag,
    mpls_ptp,
    multicast,
    ospf,
    qos,
    route_lists,
    route_policy,
    sdwan,
    spanning_tree,
    static_routes,
    traffic_monitoring,
    trustpoints,
    vlans,
    vrfs,
    vxlan,
)
from driftpilot.config_parser.patterns import Pattern

# IR の最上位のキーと同じ名前。vendor_specific は機種固有の行(FortiGate のファイアウォールなど)で、A型の表には無い
CATEGORIES: tuple[str, ...] = (
    "interfaces", "vlans", "acls", "ospf", "bgp", "spanning_tree", "vrfs", "static_routes", "lines", "global",
    "certificates", "qos", "ipv6_acls", "mac_acls", "ospfv3", "eigrp", "ipsec", "route_maps", "prefix_lists",
    "ipv6_prefix_lists", "vxlan", "mlag", "isis", "bfd", "route_lists", "traffic_monitoring", "multicast", "sdwan",
    "mpls", "ptp", "vendor_specific",
)

ALL_PATTERNS: tuple[Pattern, ...] = (
    *interfaces.PATTERNS,
    *vlans.PATTERNS,
    *acls.PATTERNS,
    *ospf.PATTERNS,
    *bgp.PATTERNS,
    *spanning_tree.PATTERNS,
    *vrfs.PATTERNS,
    *static_routes.PATTERNS,
    *lines.PATTERNS,
    *global_.PATTERNS,
    *certificates.PATTERNS,
    *device_settings.PATTERNS,
    *management.PATTERNS,
    *trustpoints.PATTERNS,
    *qos.PATTERNS,
    *eigrp.PATTERNS,
    *ipsec.PATTERNS,
    *route_policy.PATTERNS,
    *vxlan.PATTERNS,
    *mlag.PATTERNS,
    *isis.PATTERNS,
    *bfd.PATTERNS,
    *route_lists.PATTERNS,
    *traffic_monitoring.PATTERNS,
    *multicast.PATTERNS,
    *sdwan.PATTERNS,
    *mpls_ptp.PATTERNS,
)


def validate(patterns: tuple[Pattern, ...] = ALL_PATTERNS) -> None:
    """ID の重複と、`<カテゴリ>.<名前>` でない ID を拒否する。"""
    seen: set[str] = set()
    for pattern in patterns:
        if pattern.id in seen:
            raise ValueError(f"パターン ID が重複しています: {pattern.id}")
        seen.add(pattern.id)
        category, dot, rest = pattern.id.partition(".")
        if category not in CATEGORIES or not dot or not rest:
            raise ValueError(f"パターン ID の形が不正です: {pattern.id}")


@dataclass(frozen=True)
class PatternsFingerprint:
    """パターン表の指紋(ADR-0017 決定2)。digest は SHA-256 の先頭16文字、count は件数。"""

    digest: str
    count: int


def patterns_fingerprint(patterns: tuple[Pattern, ...] | None = None) -> PatternsFingerprint:
    """表の各件の id / scope / match / target を、表の順序(優先順位)のまま並べてハッシュする。

    ID だけにしないのは、同じ ID のまま正規表現を広げる改修を検出するため。example と note は含めない
    (例や備考の修正で指紋を動かさない)。None のときは呼んだ時点の ALL_PATTERNS を読む。
    理由は docs/notes/req-14-storage.md。
    """
    table = ALL_PATTERNS if patterns is None else patterns
    items = [[p.id, p.scope, p.match, p.target] for p in table]
    text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return PatternsFingerprint(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], len(table))


validate()
