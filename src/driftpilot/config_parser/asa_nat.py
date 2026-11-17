"""Cisco ASA の手動の NAT(最上位の `nat (<内側>,<外側>) …`)を IR に写す extractor(ADR-0214 決定4、REQ-303)。

ASA と見立てた config だけ、`vendor_specific["cisco_asa"]["nat"]` に、行の文(空白を 1 つにまとめたもの)を出てきた順の
リストで置く(順序は意味を持つ: 手動の NAT は上から当てる)。中身は分解しない。
`object network <名前>` の中の ` nat (…) …`(オブジェクトの NAT)は、ここでは読まず未解釈のまま。
"""

import re

from hier_config import HConfig

from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines


def _nat(m: re.Match[str], ctx: list[str]) -> bool:
    """手動の NAT の行の文を、出てきた順にリストへ足す。"""
    ctx.append(m.string)
    return True


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vendor_specific.cisco_asa_nat", "top", r"nat (?P<rest>\(.*)", 'vendor_specific["cisco_asa"]["nat"]',
        "nat (inside,outside) source dynamic any interface", _nat,
        note=(
            "手続き。Cisco ASA と見立てた config だけ。最上位の `nat (` で始まる行の文(空白は 1 つ)を、出てきた順に"
            "リストへ足す。ASA と見立てた config には、行が無くても空のリストを置く。"
            "`object network` の中の ` nat (…) …` は読まず未解釈(ADR-0214 決定4、REQ-303)"
        ),
    ),
)


def extract_asa_nat(tree: HConfig, interpreted: InterpretedLines) -> list[str]:
    """最上位の手動の NAT の行を、出てきた順の文のリストで返し、解釈済みにする。"""
    nat: list[str] = []
    for block in tree.children:
        pattern = apply_first(PATTERNS, "top", block.text, nat)
        if pattern is not None:
            interpreted.mark(block, pattern.id)
    return nat
