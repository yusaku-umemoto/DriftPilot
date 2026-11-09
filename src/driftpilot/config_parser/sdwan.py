"""Arista の SD-WAN の最上位のブロックとその中の行を IR の sdwan に写す extractor(ADR-0112、REQ-159)。

読むのは、子を持つ見出し(`router path-selection`・`router adaptive-virtual-topology`・`router internet-exit`・
`router service-insertion`・`application traffic recognition`)の中の行。鍵は見出しの原文。
中の子を持たない行は、見出しの次の段からの道すじを ` > ` でつないだ文字列の集合にする。子を持つ行(`path-group …`・
`policy …` など)は文字列にしないが解釈済みにする。`path-group`・`policy` を名前で引く固定の項目にしない
(入れ子が深く、版で書き方が変わる。ADR-0112 Option SB を採らない)。
道すじのどの段かが、語のどれかが password・secret・key か語の中に鍵が埋まった行(device_settings と同じ判定)は、
その行も子孫も未解釈。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import SdwanBlock
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_SEPARATOR = " > "
_SCOPE = "sdwan block"


@dataclass
class _Ctx:
    blocks: dict[str, SdwanBlock] = field(default_factory=dict)
    current: SdwanBlock | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False
    # 見出しの次の段からその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _block(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ見出しだけ読む。見出しは文字列にしない。子の無い見出しは未解釈。同じ見出しは同じ項目にまとめる。"""
    if not ctx.has_children:
        return False
    ctx.current = ctx.blocks.setdefault(m.string, {"settings": []})
    return True


def _setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は道すじを集合に足す。子を持つ行は解釈済みにだけする。"""
    if has_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.current["settings"], _SEPARATOR.join(ctx.path))
    return True


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "sdwan.block", "top",
        r"router path-selection|router adaptive-virtual-topology|router internet-exit|router service-insertion"
        r"|application traffic recognition",
        "sdwan[見出しの原文]", "router path-selection", _block,
        note=(
            "手続き。子を持つ見出しだけ読み、見出しの原文を鍵にした項目を作る(同じ見出しは同じ項目にまとめる)。"
            "見出しは文字列にしないが解釈済みにする。子の無い見出しは未解釈"
        ),
    ),
    Pattern(
        "sdwan.block.setting", _SCOPE, r".+", "sdwan[見出しの原文].settings", "path-group INET id 101", _setting,
        note=(
            "手続き。中の子を持たない行を、見出しの次の段からの道すじを ` > ` でつないだ文字列にして settings に足す"
            "(見出しは含めない。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            "道すじのどの段かの語が password・secret・key か、語の中に鍵が埋まった(key, や password= などの)行は、"
            "その行も子孫も未解釈(同じブロックのほかの行は読む)"
        ),
    ),
)


def _read_block(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """ブロックの中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, _SCOPE, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(child, ctx.path, ctx, interpreted)


def extract_sdwan(tree: HConfig, interpreted: InterpretedLines) -> dict[str, SdwanBlock]:
    """SD-WAN の見出しから、見出しの原文をキーにした SdwanBlock の辞書を返す。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        _read_block(block, [], ctx, interpreted)
    return ctx.blocks
