"""structured 比較の安全網(ADR-0009 決定3)。

既知のコマンドの書式が変わると、設定が存在するのに「なし」と読んで差分ゼロになりうる。
ここでは、それを警告として報告する。

- 安全網2: ツリーに対象があるのに、IR のカテゴリが0件の状態(`check_empty_categories`)
- 安全網3: ツリー差分があるのに structured 差分が0件の食い違い(`check_tree_mismatch`)

hier_config の unified_diff は食い違いの検算にだけ使い、本文は結果に含めない(ADR-0009 決定2)。
"""

import re

from hier_config import HConfig

from driftpilot import i18n
from driftpilot.config_parser.ir import IR
from driftpilot.diff_engine.types import Diff, SafetyWarning, Side

# interfaces.switchport の判定は、interface ブロックがこの数以上あるときだけ行う(1個だけの機器でのノイズを避ける)
_MIN_INTERFACE_BLOCKS = 2
_VLAN_LINE = re.compile(r"vlan \d")

# 警告の文に入る「何を見たか」の名前。言語に合わせて読み込むときに 1 回だけ選ぶ(messages は読まない決まりのため、ここに持つ)
_TEXTS_JA = {
    "switchport": "switchport の行",
    "ip_address": "ip address の行",
    "vlan": "vlan のブロック",
    "ospf": "router ospf のブロック",
    "bgp": "router bgp のブロック",
    "ntp": "ntp server の行",
}
_TEXTS_EN = {
    "switchport": "switchport lines",
    "ip_address": "ip address lines",
    "vlan": "vlan blocks",
    "ospf": "router ospf blocks",
    "bgp": "router bgp blocks",
    "ntp": "ntp server lines",
}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)


def _top_lines(tree: HConfig, *prefixes: str) -> list[str]:
    return [child.text for child in tree.children if child.text.startswith(prefixes)]


def _interface_child_lines(tree: HConfig, prefix: str) -> tuple[int, int]:
    """(interface ブロックの数, 子行のうち prefix で始まる行の数) を返す。"""
    blocks = [child for child in tree.children if child.text.startswith("interface ")]
    lines = sum(1 for block in blocks for child in block.children if child.text.startswith(prefix))
    return len(blocks), lines


def _warning(side: Side, category: str, tree_count: int, ir_count: int, subject: str) -> SafetyWarning:
    return SafetyWarning(
        kind="empty_category",
        side=side,
        category=category,
        detail={"tree_count": tree_count, "ir_count": ir_count, "subject": subject},
    )


def check_empty_categories(tree: HConfig, ir: IR, side: Side) -> list[SafetyWarning]:
    """ツリーに対象があるのに抽出結果が0件のカテゴリを警告にする。割合は見ない。"""
    warnings: list[SafetyWarning] = []
    interfaces = ir["interfaces"].values()

    blocks, switchport_lines = _interface_child_lines(tree, "switchport")
    readable = sum(
        1
        for itf in interfaces
        if (sw := itf["switchport"])["mode"] is not None
        or sw["access_vlan"] is not None
        or sw["native_vlan"] is not None
        or sw["trunk_allowed_vlan"]
    )
    if blocks >= _MIN_INTERFACE_BLOCKS and switchport_lines and readable == 0:
        warnings.append(_warning(side, "interfaces.switchport", switchport_lines, readable, _T["switchport"]))

    _, ip_lines = _interface_child_lines(tree, "ip address ")
    ip_read = sum(1 for itf in interfaces if itf["ip_address"] is not None)
    if ip_lines and ip_read == 0:
        warnings.append(_warning(side, "interfaces.ip_address", ip_lines, ip_read, _T["ip_address"]))

    vlan_lines = [line for line in _top_lines(tree, "vlan ") if _VLAN_LINE.match(line)]
    if vlan_lines and not ir["vlans"]:
        warnings.append(_warning(side, "vlans", len(vlan_lines), 0, _T["vlan"]))

    acl_lines = _top_lines(tree, "ip access-list ", "access-list ")
    if acl_lines and not ir["acls"]:
        warnings.append(_warning(side, "acls", len(acl_lines), 0, "ACL"))

    ospf_lines = _top_lines(tree, "router ospf ")
    if ospf_lines and not ir["ospf"]:
        warnings.append(_warning(side, "ospf", len(ospf_lines), 0, _T["ospf"]))

    bgp_lines = _top_lines(tree, "router bgp ")
    if bgp_lines and ir["bgp"] is None:
        warnings.append(_warning(side, "bgp", len(bgp_lines), 0, _T["bgp"]))

    ntp_lines = [node.text for node in tree.all_children() if node.text.startswith("ntp server ")]
    if ntp_lines and not ir["global"]["ntp_servers"]:
        warnings.append(_warning(side, "global.ntp_servers", len(ntp_lines), 0, _T["ntp"]))

    return warnings


def check_tree_mismatch(before_tree: HConfig, after_tree: HConfig, diffs: list[Diff]) -> list[SafetyWarning]:
    """ツリー差分があるのに structured 差分が0件なら、食い違いとして1件返す。

    unified_diff は変更前から変更後への向きで呼び、行数だけを detail に入れる。
    """
    if diffs:
        return []
    tree_lines = len(list(before_tree.unified_diff(after_tree)))
    if tree_lines == 0:
        return []
    return [
        SafetyWarning(
            kind="tree_diff_mismatch",
            side=None,
            category=None,
            detail={"tree_diff_lines": tree_lines},
        )
    ]
