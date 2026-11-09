"""最上位の MPLS(LDP・RSVP・TE)と PTP の行とブロックを IR の mpls・ptp に写す extractor(ADR-0108、REQ-89)。

読むのは、最上位の `mpls <残り>`・`router traffic-engineering`・`router ldp`・`router rsvp`(mpls)と、
`ptp <残り>`(ptp)の行(no 付きも)。子を持たない行は原文を、子を持つ行は中の子を持たない行の見出しからの道すじを
` > ` でつないだ文字列を、カテゴリごとの1つの集合にする(ADR-0051・ADR-0061 と同じ原文の集合。LDP の router-id などを
固定の項目にしない。ADR-0108 Option MB を採らない)。子を持つ行そのものは文字列にしないが解釈済みにする。
インターフェースの `mpls …`・`ptp …`・`evpn …` は interfaces が読む。IS-IS の中の `segment-routing` は isis の範囲。
語のどれかが password・secret・key の行と、語の中に鍵が埋まった行は入れない(device_settings と同じ判定)。
`[no] mpls ldp logging password configuration|rollover` は秘密の行とみなさずに読む(ADR-0124 決定1、REQ-169)。
"""

import re
from bisect import insort
from collections.abc import Callable
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Mpls, Ptp
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_SEPARATOR = " > "
_MPLS_BLOCK = "mpls block"
_PTP_BLOCK = "ptp block"


@dataclass
class _Ctx:
    # カテゴリ(mpls・ptp) → 集合
    values: dict[str, list[str]] = field(default_factory=lambda: {"mpls": [], "ptp": []})
    # 1行でも(見出しも)読めたカテゴリ
    read: set[str] = field(default_factory=set)
    has_children: bool = False
    # 子を持つ見出しを読んだら True。中の行を読む
    in_block: bool = False
    # 中の行を読んでいる間の状態。見出しからその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _top(category: str) -> Callable[[re.Match[str], _Ctx], bool]:
    """最上位の行を読む手続きを返す。子を持たない行は原文を足し、子を持つ行は中を読む印を付ける。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if has_secret(m.string):
            return False
        if ctx.has_children:
            ctx.in_block = True
        else:
            _add(ctx.values[category], m.string)
        ctx.read.add(category)
        return True

    return apply


def _block_setting(category: str) -> Callable[[re.Match[str], _Ctx], bool]:
    """ブロックの中の行を読む手続きを返す。子を持たない行は見出しからの道すじを足す。子を持つ行は解釈済みにだけする。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        if has_secret(*ctx.path):
            return False
        if not ctx.has_children:
            _add(ctx.values[category], _SEPARATOR.join(ctx.path))
        return True

    return apply


_SECRET_NOTE = "語のどれかが password・secret・key か、語の中に鍵が埋まった(key, や password= などの)"
_TOP_NOTE = (
    "子を持たない行は原文を settings の集合に足す(重ねない。昇順。ADR-0108)。"
    "子を持つ行は文字列にしないが解釈済みにし、中の行を ` > ` でつないで読む。"
    f"{_SECRET_NOTE}行は、子も含めて未解釈"
)
_BLOCK_NOTE = (
    "手続き。ブロックの中の子を持たない行を、見出しからの道すじを ` > ` でつないだ文字列にして集合に足す"
    "(見出しを含める。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにし、さらに中を読む。"
    f"道すじのどの段かが、{_SECRET_NOTE}行なら、その行も子孫も未解釈(同じブロックのほかの行は読む)"
)

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "mpls.line", "top", r"(?:no )?mpls .+|router (?:traffic-engineering|ldp|rsvp)", "mpls.settings",
        "mpls ip", _top("mpls"),
        note=(
            f"手続き。{_TOP_NOTE}。`mpls` は後ろの語が要る(`mpls`・`no mpls` だけの行は未解釈)。"
            "`router ldp` などの後ろに語が続く行は未解釈。"
            "語の並びが `[no] mpls ldp logging password configuration`・`[no] mpls ldp logging password rollover` "
            "のとおりの行は、秘密の行とみなさずに読む(ADR-0124 決定1、REQ-169)"
        ),
    ),
    Pattern(
        "mpls.block.setting", _MPLS_BLOCK, r".+", "mpls.settings", "router-id 192.0.2.1", _block_setting("mpls"),
        note=_BLOCK_NOTE,
    ),
    Pattern(
        "ptp.line", "top", r"(?:no )?ptp .+", "ptp.settings", "ptp clock-identity eui-64", _top("ptp"),
        note=f"手続き。{_TOP_NOTE}。後ろの語が要る(`ptp` だけの行は未解釈)",
    ),
    Pattern(
        "ptp.block.setting", _PTP_BLOCK, r".+", "ptp.settings", "clock-port PORT-1", _block_setting("ptp"),
        note=_BLOCK_NOTE,
    ),
)

_BLOCK_SCOPES = {"mpls": _MPLS_BLOCK, "ptp": _PTP_BLOCK}


def _read_block(
    node: HConfigChild, prefix: list[str], scope: str, ctx: _Ctx, interpreted: InterpretedLines
) -> None:
    """ブロックの中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, scope, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(child, ctx.path, scope, ctx, interpreted)


def extract_mpls_ptp(tree: HConfig, interpreted: InterpretedLines) -> tuple[Mpls | None, Ptp | None]:
    """最上位の MPLS・PTP の行とブロックを Mpls・Ptp に写す。1行も読めなかったカテゴリは None。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        ctx.in_block = False
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        if ctx.in_block:
            _read_block(block, [block.text], _BLOCK_SCOPES[pattern.category], ctx, interpreted)
    mpls: Mpls | None = {"settings": ctx.values["mpls"]} if "mpls" in ctx.read else None
    ptp: Ptp | None = {"settings": ctx.values["ptp"]} if "ptp" in ctx.read else None
    return mpls, ptp
