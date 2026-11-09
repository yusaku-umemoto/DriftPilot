"""最上位の `router eigrp <1語>` とその中の行を IR の eigrp に写す extractor(ADR-0065、REQ-76)。

鍵は `router eigrp` の後ろの語(AS 番号でも名前でも文字列のまま)。中の子を持たない行は、見出しの次の段からの道すじを
` > ` でつないだ文字列の集合にする。子を持つ行(名前付きの `address-family …`・`topology base` など)は文字列にしないが
解釈済みにする。名前付き EIGRP の終わりの印(`exit-af-topology` など)は前処理で木から消えている(REQ-78)。
`network` などを固定の項目にしない(ADR-0065 Option EB を採らない)。インターフェースの `ip eigrp …` は読まない。
道すじのどの段かが、語のどれかが password・secret・key か語の中に鍵が埋まった行(device_settings と同じ判定)か、
語に key-chain を含む行は、その行も子孫も未解釈。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Eigrp
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_SEPARATOR = " > "
_SCOPE = "router eigrp"


@dataclass
class _Ctx:
    processes: dict[str, Eigrp] = field(default_factory=dict)
    current: Eigrp | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False
    # 見出しの次の段からその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _is_secret(*texts: str) -> bool:
    return has_secret(*texts) or any("key-chain" in word.lower() for text in texts for word in text.split())


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _process(m: re.Match[str], ctx: _Ctx) -> bool:
    """項目を作る。同じ鍵の見出しが2回あれば同じ項目にまとめる。子の無い見出しも項目を作る。"""
    if _is_secret(m.string):
        return False
    ctx.current = ctx.processes.setdefault(m["name"], {"settings": []})
    return True


def _setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は道すじを集合に足す。子を持つ行は解釈済みにだけする。"""
    if _is_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.current["settings"], _SEPARATOR.join(ctx.path))
    return True


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "eigrp.process", "top", r"router eigrp (?P<name>\S+)", "eigrp[name]", "router eigrp 100", _process,
        note=(
            "手続き。`router eigrp` の後ろの語(AS 番号でも名前でも文字列のまま)を鍵にした項目を作る。"
            "同じ鍵の見出しが2回あれば同じ項目にまとめる。中身の無い見出しも項目を作る"
        ),
    ),
    Pattern(
        "eigrp.setting", _SCOPE, r".+", "eigrp[name].settings", "network 192.0.2.0 0.0.0.255", _setting,
        note=(
            "手続き。中の子を持たない行を、見出しの次の段からの道すじを ` > ` でつないだ文字列にして settings に足す"
            "(見出しは含めない。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            "道すじのどの段かの語が password・secret・key か、語の中に鍵が埋まった(key, や password= などの)行か、"
            "語に key-chain を含む行は、その行も子孫も未解釈"
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


def extract_eigrp(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Eigrp]:
    """`router eigrp` の見出しから、後ろの語をキーにした Eigrp の辞書を返す。"""
    ctx = _Ctx()
    for block in tree.children:
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        _read_block(block, [], ctx, interpreted)
    return ctx.processes
