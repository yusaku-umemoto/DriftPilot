"""トップレベルの `spanning-tree …`・`no spanning-tree …` の行を IR の spanning_tree に写す extractor(ADR-0042、REQ-52)。

読むのは子の行を持たない最上位の行と、`spanning-tree mst configuration` のブロック(ADR-0122 決定3、REQ-167)。
ブロックは、子がすべて子を持たず秘密の語が無いときだけ、子を `mst configuration > <子>` として options に足す。
ほかの子の行を持つ見出しは、見出しも子も未解釈のまま。mode の値は原文の語のままで、機種の間でそろえない(決定3)。
インターフェースの中の行は interfaces.py が読む。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import SpanningTree, new_spanning_tree
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 固定の項目に写す行の残りの先頭。形が合わない行は options に入れず未解釈にする(読み違いを隠さない)
_FIXED_ATTRIBUTES = ("mode", "extend system-id")
_MST_SCOPE = "mst configuration"
_MST_PREFIX = "mst configuration > "


@dataclass
class _Ctx:
    spanning_tree: SpanningTree | None = None
    # 子を持つ見出しを読んでいる間の、その見出しの子
    children: list[HConfigChild] = field(default_factory=list)


def _block(ctx: _Ctx) -> SpanningTree:
    """spanning_tree を返す。無ければ作る(行が写せたときにだけ呼ぶ)。"""
    if ctx.spanning_tree is None:
        ctx.spanning_tree = new_spanning_tree()
    return ctx.spanning_tree


def _mode(m: re.Match[str], ctx: _Ctx) -> bool:
    _block(ctx)["mode"] = m["mode"]
    return True


def _extend_system_id(m: re.Match[str], ctx: _Ctx) -> bool:
    _block(ctx)["extend_system_id"] = True
    return True


def _no_extend_system_id(m: re.Match[str], ctx: _Ctx) -> bool:
    _block(ctx)["extend_system_id"] = False
    return True


def _starts_with(rest: str, attributes: tuple[str, ...]) -> bool:
    return any(rest == a or rest.startswith(a + " ") for a in attributes)


def _add_option(ctx: _Ctx, text: str) -> bool:
    """options に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    options = _block(ctx)["options"]
    if text not in options:
        insort(options, text)
    return True


def _options(m: re.Match[str], ctx: _Ctx) -> bool:
    """固定の項目に無い行の残りを、空白を1つにそろえて options に足す。"""
    rest = " ".join(m["rest"].split())
    if _starts_with(rest, _FIXED_ATTRIBUTES):
        return False
    return _add_option(ctx, rest)


def _no_options(m: re.Match[str], ctx: _Ctx) -> bool:
    """no 付きの行は `no <残り>` を options に足す。`no spanning-tree mode …` もここに入る(依頼書の表のとおり)。"""
    rest = " ".join(m["rest"].split())
    if _starts_with(rest, ("extend system-id",)):
        return False
    return _add_option(ctx, f"no {rest}")


def _mst_configuration(m: re.Match[str], ctx: _Ctx) -> bool:
    """子がすべて子を持たず、どの子にも秘密の語が無いときだけ読む。そうでなければ見出しも子も未解釈。"""
    return all(not child.children and not has_secret(child.text) for child in ctx.children)


def _mst_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子の原文(空白は1つ)を `mst configuration > <子>` として options に足す。"""
    return _add_option(ctx, _MST_PREFIX + " ".join(m.string.split()))


_NOTE_OPTIONS = (
    "手続き。固定の項目に無い行の残り(空白は1つにそろえる)を options に足す。同じ文字列は重ねず、文字列の昇順。"
    "mode・extend system-id で始まるのに形が合わない行(mode の後ろが0語・2語以上、extend system-id の後ろに語が続く)は"
    "入れず未解釈にする。子の行を持つ見出しは表に通さず、見出しも子も未解釈"
    "(spanning-tree mst configuration だけは spanning_tree.mst_configuration が読む。ADR-0122 決定3)"
)

# 並び＝優先順位。options は残りすべてに当たるので、固定の項目の後ろに置く。
# 最上位の行は、子を持たなければ mst_configuration 以外の書式に、子を持てば mst_configuration だけに通す。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "spanning_tree.mode", "top", r"spanning-tree mode (?P<mode>\S+)", "spanning_tree.mode",
        "spanning-tree mode rapid-pvst", _mode,
        note="手続き。原文の語のまま(ADR-0042 決定3)。後ろが0語・2語以上の行は当たらず、options にも入れない",
    ),
    Pattern(
        "spanning_tree.extend_system_id", "top", r"spanning-tree extend system-id",
        "spanning_tree.extend_system_id = True", "spanning-tree extend system-id", _extend_system_id,
    ),
    Pattern(
        "spanning_tree.no_extend_system_id", "top", r"no spanning-tree extend system-id",
        "spanning_tree.extend_system_id = False", "no spanning-tree extend system-id", _no_extend_system_id,
    ),
    Pattern(
        "spanning_tree.mst_configuration", "top", r"spanning-tree mst configuration",
        "spanning_tree.options(mst configuration > <子>)", "spanning-tree mst configuration", _mst_configuration,
        note="手続き。子を持つ見出しだけに当てる(子の無い行は spanning_tree.options が読む)。子がすべて子を持たず、"
        "どの子も語のどれかが password・secret・key でなく語の中に鍵が埋まった(key, や password= などの)形でもないときだけ、"
        "見出しを解釈済みにして子を読む。そうでなければ見出しも子も未解釈(ADR-0122 決定3、REQ-167)",
    ),
    Pattern(
        "spanning_tree.options", "top", r"spanning-tree (?P<rest>.+)", "spanning_tree.options",
        "spanning-tree pathcost method short", _options, note=_NOTE_OPTIONS,
    ),
    Pattern(
        "spanning_tree.no_options", "top", r"no spanning-tree (?P<rest>.+)", "spanning_tree.options(no <残り>)",
        "no spanning-tree vlan-id 4094", _no_options,
        note="手続き。`no <残り>` を options に足す(重ねない。昇順)。no spanning-tree mode … も `no mode …` として入れる。"
        "extend system-id の後ろに語が続く行は未解釈",
    ),
    Pattern(
        "spanning_tree.mst_configuration.setting", _MST_SCOPE, r".+",
        "spanning_tree.options(mst configuration > <子>)", "name EXAMPLE-REGION", _mst_setting,
        note="手続き。`mst configuration > ` に子の原文(空白は1つにそろえる)をつないで options に足す"
        "(重ねない。文字列の昇順。ADR-0122 決定3、REQ-167)",
    ),
)
_LEAF_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id != "spanning_tree.mst_configuration")
_BLOCK_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id == "spanning_tree.mst_configuration")


def extract_spanning_tree(tree: HConfig, interpreted: InterpretedLines) -> SpanningTree | None:
    """最上位の子の行を持たない行と mst configuration のブロックを SpanningTree に写す。1行も写せなければ None。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.children = list(block.children)
        patterns = _BLOCK_PATTERNS if block.children else _LEAF_PATTERNS
        pattern = apply_first(patterns, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        for child in ctx.children:
            child_pattern = apply_first(PATTERNS, _MST_SCOPE, child.text, ctx)
            if child_pattern is not None:
                interpreted.mark(child, child_pattern.id)
    return ctx.spanning_tree
