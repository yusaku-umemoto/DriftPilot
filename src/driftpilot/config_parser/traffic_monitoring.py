"""最上位の通信の記録(sFlow・flow tracking)の行とブロックを IR の traffic_monitoring に写す extractor(ADR-0061、REQ-72)。

読むのは、子を持たない `sflow <残り>` の1行と、子を持つ `flow tracking <1語>` のブロックの中の行。
1行は原文を、ブロックの中の子を持たない行は見出しからの道すじを ` > ` でつないだ文字列を、1つの集合にする
(ADR-0051 と同じ原文の集合。送り先などを固定の項目にしない)。
Cisco の `ip flow …`・`flow exporter …`・`flow monitor …` は読まない(Flexible NetFlow は device_settings が読む)。
`monitor session …`(ポートのミラー)も読まない。インターフェースの `sflow …`・`flow tracker …` は interfaces が読む。
語のどれかが password・secret・key の行と、語の中に鍵が埋まった行は入れない(device_settings と同じ判定)。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import TrafficMonitoring
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_SEPARATOR = " > "
_BLOCK_SCOPE = "flow tracking"


@dataclass
class _Ctx:
    values: list[str] = field(default_factory=list)
    has_children: bool = False
    # 見出しを読んだら True。中の行を読む
    in_block: bool = False
    # 中の行を読んでいる間の状態。見出しからその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _sflow(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行だけ、原文を集合に足す。子を持つ行は子も含めて未解釈。"""
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.values, m.string)
    return True


def _block(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ見出しだけ読む。見出しは文字列にしない。子の無い見出しは未解釈。"""
    if not ctx.has_children or has_secret(m.string):
        return False
    ctx.in_block = True
    return True


def _block_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は見出しからの道すじを集合に足す。子を持つ行は解釈済みにだけする。"""
    if has_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.values, _SEPARATOR.join(ctx.path))
    return True


_SECRET_NOTE = "語のどれかが password・secret・key か、語の中に鍵が埋まった(key, や password= などの)"

PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "traffic_monitoring.sflow", "top", r"sflow .+", "traffic_monitoring.settings",
        "sflow vrf MGMT destination 192.0.2.50", _sflow,
        note=(
            "手続き。子を持たない行の原文を settings の集合に足す(重ねない。昇順。ADR-0061)。"
            f"後ろの無い sflow は当たらず未解釈。子を持つ行と、{_SECRET_NOTE}行は、子も含めて未解釈"
        ),
    ),
    Pattern(
        "traffic_monitoring.flow_tracking", "top", r"flow tracking \S+", "traffic_monitoring.settings",
        "flow tracking hardware", _block,
        note=(
            "手続き。子を持つ見出しだけ読み、中の行を settings に写す。見出しは文字列にしないが解釈済みにする。"
            f"子の無い見出しと、{_SECRET_NOTE}見出しは、見出しも中も未解釈"
        ),
    ),
    Pattern(
        "traffic_monitoring.flow_tracking.setting", _BLOCK_SCOPE, r".+", "traffic_monitoring.settings",
        "no shutdown", _block_setting,
        note=(
            "手続き。ブロックの中の子を持たない行を、見出しからの道すじを ` > ` でつないだ文字列にして集合に足す"
            "(見出しを含める。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            f"道すじのどの段かが、{_SECRET_NOTE}行なら、その行も子孫も未解釈(同じブロックのほかの行は読む)"
        ),
    ),
)


def _read_block(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """ブロックの中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, _BLOCK_SCOPE, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(child, ctx.path, ctx, interpreted)


def extract_traffic_monitoring(tree: HConfig, interpreted: InterpretedLines) -> TrafficMonitoring | None:
    """最上位の通信の記録の行とブロックを TrafficMonitoring に写す。1行も読めなければ None。"""
    ctx = _Ctx()
    read = False
    for block in tree.children:
        ctx.has_children = bool(block.children)
        ctx.in_block = False
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        read = True
        if ctx.in_block:
            _read_block(block, [block.text], ctx, interpreted)
    return {"settings": ctx.values} if read else None
