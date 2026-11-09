"""最上位の `router bfd` とその中の行、最上位の `bfd <残り>` を IR の bfd に写す extractor(ADR-0059、REQ-70)。

1台に1つの塊。見出しは子が無くても塊を作る。`router bfd` の中の子を持たない行は原文を、最上位の子を持たない
`bfd <残り>` は残りを options(集合)に写す。どちらか1つでもあれば塊を作り、見出しが2回あれば同じ塊にまとめる。
子を持つ行は、その行も子孫も未解釈。語のどれかが password・secret・key の行と、語の中に鍵が埋まった行も options に
入れない(device_settings と同じ判定)。最上位の `bfd` だけの行と `no bfd …` は読まない。
インターフェースの中の `bfd …` は読まない(決定2)。
"""

import re
from bisect import insort
from dataclasses import dataclass

from hier_config import HConfig

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Bfd, new_bfd
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_SCOPE = "router bfd"


@dataclass
class _Ctx:
    bfd: Bfd | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _router(m: re.Match[str], ctx: _Ctx) -> bool:
    """塊を作る。子が無くても作る。見出しが2回あれば同じ塊にまとめる。"""
    if ctx.bfd is None:
        ctx.bfd = new_bfd()
    return True


def _top_option(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    if ctx.bfd is None:
        ctx.bfd = new_bfd()
    _add(ctx.bfd["options"], m["rest"])
    return True


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.bfd["options"], m.string)
    return True


# 並び＝優先順位。bfd.option は残りすべてに当たるので、router bfd の最後
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "bfd.router", "top", r"router bfd", "bfd", "router bfd", _router,
        note=(
            "手続き。bfd の塊を作る(子が無くても作る)。見出しが2回あれば同じ塊にまとめる。"
            "後ろに語が続く行(router bfd foo など)は当たらず、見出しも中も未解釈"
        ),
    ),
    Pattern(
        "bfd.top_option", "top", r"bfd (?P<rest>.+)", "bfd.options",
        "bfd multihop interval 300 min_rx 300 multiplier 3", _top_option,
        note=(
            "手続き。`bfd` を外した残りを options に足す(重ねない。昇順)。塊が無ければ作る。子を持つ行(その行も子孫も)と、"
            "語のどれかが password・secret・key か語の中に鍵が埋まった行は未解釈(塊も作らない)。"
            "`bfd` だけの行、`no bfd …`、`bfd-template …` のような続けて書く別の語は当たらず未解釈"
        ),
    ),
    Pattern(
        "bfd.option", _SCOPE, r".+", "bfd.options", "multihop interval 300 min-rx 300 multiplier 3", _option,
        note=(
            "手続き。子を持たない行の原文を options に足す(重ねない。昇順。no 付きの行も原文のまま)。"
            "子を持つ行(その行も子孫も)と、語のどれかが password・secret・key か語の中に鍵が埋まった行は未解釈"
        ),
    ),
)


def extract_bfd(tree: HConfig, interpreted: InterpretedLines) -> Bfd | None:
    """最上位の `router bfd` とその中の行、最上位の `bfd <残り>` を Bfd に写す。どれも無ければ None。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        if pattern.id != "bfd.router":
            continue
        for child in block.children:
            ctx.has_children = bool(child.children)
            pattern = apply_first(PATTERNS, _SCOPE, child.text, ctx)
            if pattern is not None:
                interpreted.mark(child, pattern.id)
    return ctx.bfd
