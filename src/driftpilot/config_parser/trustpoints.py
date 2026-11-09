"""証明書の trustpoint(`crypto pki trustpoint <名前>` とその中の行)を IR の global に写す extractor(ADR-0052、REQ-63)。

読むのは最上位の `crypto pki trustpoint <名前>`(4語ちょうど)と、その中の子を持たない行。
中の行は原文を trustpoints[<名前>].settings に集合にする。子を持つ行は子も含めて未解釈。
`crypto pki certificate chain` は certificates.py が読み、ここでは読まない(別の項目。ADR-0052 Option TB を採らない)。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig

from driftpilot.config_parser.ir import Trustpoint
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 語のどれかがこれらの行は入れない(未解釈に残す。依頼書 §3-1)。大文字小文字は区別しない
_SECRET_WORDS = frozenset({"password", "secret", "key", "shared-secret"})
_SCOPE = "crypto pki trustpoint"


@dataclass
class _Ctx:
    trustpoints: dict[str, Trustpoint] = field(default_factory=dict)
    has_children: bool = False
    # 見出しを読んだら、その名前の項目。中の行を読む
    current: Trustpoint | None = None


def _has_secret(text: str) -> bool:
    return any(word.lower() in _SECRET_WORDS for word in text.split())


def _trustpoint(m: re.Match[str], ctx: _Ctx) -> bool:
    """項目を作る(子が無くても作る)。同じ名前は同じ項目にまとめる。"""
    if _has_secret(m.string):
        return False
    ctx.current = ctx.trustpoints.setdefault(m["name"], {"settings": []})
    return True


def _setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行だけ、原文を settings の集合に足す。"""
    if ctx.current is None or ctx.has_children or _has_secret(m.string):
        return False
    if m.string not in ctx.current["settings"]:
        insort(ctx.current["settings"], m.string)
    return True


# 並び＝優先順位。互いに重ならない(scope が違う)。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "global.trustpoint", "top", r"crypto pki trustpoint (?P<name>\S+)", "global.trustpoints",
        "crypto pki trustpoint TP-EXAMPLE", _trustpoint,
        note=(
            "手続き。名前(1語)を鍵にして項目を作り、中の行を写す。子が無くても項目を作り、同じ名前は同じ項目にまとめる。"
            "語のどれかが password・secret・key・shared-secret の見出しは、見出しも中も未解釈"
        ),
    ),
    Pattern(
        "global.trustpoint_setting", _SCOPE, r".+", "global.trustpoints.*.settings", "enrollment selfsigned",
        _setting,
        note=(
            "手続き。中の子を持たない行の原文を settings の集合に足す(重ねない。昇順)。"
            "子を持つ行と、語のどれかが password・secret・key・shared-secret の行は、子も含めて未解釈"
            "(同じ trustpoint のほかの行は読む)"
        ),
    ),
)


def extract_trustpoints(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Trustpoint]:
    """最上位の `crypto pki trustpoint <名前>` とその中の行を読み、名前 → 項目の辞書を返す。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.current = None
        ctx.has_children = bool(block.children)
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        for child in block.children:
            ctx.has_children = bool(child.children)
            if (child_pattern := apply_first(PATTERNS, _SCOPE, child.text, ctx)) is not None:
                interpreted.mark(child, child_pattern.id)
    return ctx.trustpoints
