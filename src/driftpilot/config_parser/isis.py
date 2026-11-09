"""最上位の `router isis <タグ>` とその中の行を IR の isis に写す extractor(ADR-0056、REQ-67)。

鍵はタグ。同じタグの見出しが2回あれば同じ項目にまとめる。中の子を持たない行のうち、`net <NET>` は net の集合に、
`is-type <種類>` は is_type に、`router-id ipv4 <アドレス>` は router_id に(どれも原文)、`authentication key …` は
options に `authentication key` を(鍵の値は持たない)、ほかの行は options に行の原文を写す。
`address-family <種類…>` は address_families に項目を作り、中の子を持たない行の原文をその options に写す。
ほかの子を持つ行(`segment-routing mpls` など)は、その行も子孫も未解釈。
3つの項目・address-family・鍵と同じ設定の、今は読まない形(形の合わない行と no 付きの形)は、options に入れず
未解釈に残す(REQ-67 の条件4)。語のどれかが password・secret・key の行と、語の中に鍵が埋まった行は、
3つの項目にも options にも入れない(device_settings と同じ判定)。インターフェースの中の `isis …` は interfaces.py が読む。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Isis, IsisAddressFamily, new_isis
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_SCOPE = "router isis"
_AF_SCOPE = "router isis af"
# 項目に写す設定の頭の語。形の合わない行と no 付きの形は options に入れない
_ITEM_WORDS = frozenset({"net", "is-type", "router-id", "address-family"})
# 鍵の後ろに書ける段の指定(伏せ方の表 MSK-21・MSK-25 と同じ形)
_LEVELS = frozenset({"level-1", "level-2"})


@dataclass
class _Ctx:
    instances: dict[str, Isis] = field(default_factory=dict)
    current: Isis | None = None
    af: IsisAddressFamily | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def is_key_form(rest: str | None) -> bool:
    """`authentication key` の後ろが `[<型>] <値> [level-1|level-2]` の形なら True(伏せ方の表 MSK-21・MSK-25 と同じ形)。

    型は数か `8a`。後ろが無い行と、語が多い行は False。interfaces.py の `isis authentication key …` も同じ判定を使う。
    """
    words = (rest or "").split()
    if len(words) >= 2 and words[-1] in _LEVELS:
        words = words[:-1]
    return len(words) == 1 or (len(words) == 2 and (words[0].isdigit() or words[0] == "8a"))


def _instance(m: re.Match[str], ctx: _Ctx) -> bool:
    """項目を作る。子が無くても作る。同じタグがすでにあれば同じ項目を使う。秘密の語がある見出しは読まない。"""
    if has_secret(m.string):
        return False
    ctx.current = ctx.instances.setdefault(m["tag"], new_isis())
    return True


def _net(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.current["net"], m["value"])
    return True


def _is_type(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    ctx.current["is_type"] = m["value"]
    return True


def _router_id(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    ctx.current["router_id"] = m["value"]
    return True


def _auth_key(m: re.Match[str], ctx: _Ctx) -> bool:
    """`authentication key …` を `authentication key` にして options に足す(値も型も段も持たない)。"""
    if ctx.has_children or not is_key_form(m["rest"]):
        return False
    _add(ctx.current["options"], "authentication key")
    return True


def _address_family(m: re.Match[str], ctx: _Ctx) -> bool:
    """address_families に項目を作る(子が無くても作る)。同じ種類は同じ項目にまとめる。中の行は呼び出し側が読む。"""
    if has_secret(m.string):
        return False
    family = " ".join(m["family"].split())
    ctx.af = ctx.current["address_families"].setdefault(family, {"options": []})
    return True


def _is_unread_form(line: str) -> bool:
    """項目・address-family・鍵と同じ設定の、今は読まない形なら True。"""
    words = line.split()
    if words[0] == "no" and len(words) > 1:
        words = words[1:]
    return words[0] in _ITEM_WORDS or words[:2] == ["authentication", "key"]


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string) or _is_unread_form(m.string):
        return False
    _add(ctx.current["options"], " ".join(m.string.split()))
    return True


def _af_option(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.af["options"], " ".join(m.string.split()))
    return True


_NOTE_ITEM = (
    "後ろの1語(原文)を写す。後ろが0語・2語以上の行は当たらず、isis.option でも入れない。子を持つ行と、"
    "語のどれかが password・secret・key か語の中に鍵が埋まった(key, や password= などの)行は未解釈"
)
_SECRET_NOTE = "語のどれかが password・secret・key か、語の中に鍵が埋まった(key, や password= などの)行は未解釈"

# 並び＝優先順位。isis.auth_key は isis.option より前(形の合わない鍵の行を一般の行として読まない)。
# isis.option は残りすべてに当たるので、router isis の最後
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "isis.instance", "top", r"router isis (?P<tag>\S+)", "isis[タグ]", "router isis CORE", _instance,
        note=(
            "手続き。タグを鍵にした項目を作る(子が無くても作る)。同じタグの見出しが2回あれば同じ項目にまとめる。"
            "タグの無い行・語が多い行と、語のどれかが password・secret・key か語の中に鍵が埋まった見出しは、"
            "見出しも中も未解釈"
        ),
    ),
    Pattern(
        "isis.net", _SCOPE, r"net (?P<value>\S+)", "isis[タグ].net", "net 49.0001.0000.0000.0001.00", _net,
        note=f"手続き。net の集合に足す(重ねない。昇順)。{_NOTE_ITEM}",
    ),
    Pattern(
        "isis.is_type", _SCOPE, r"is-type (?P<value>\S+)", "isis[タグ].is_type", "is-type level-2", _is_type,
        note=f"手続き。2回あれば後の行。{_NOTE_ITEM}",
    ),
    Pattern(
        "isis.router_id", _SCOPE, r"router-id ipv4 (?P<value>\S+)", "isis[タグ].router_id", "router-id ipv4 192.0.2.1",
        _router_id,
        note=(
            "手続き。アドレスを原文で写す(ipv4 の語は入れない)。2回あれば後の行。`router-id ipv6 …` と、"
            "後ろが0語・2語以上の行は当たらず、isis.option でも入れない。子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "isis.auth_key", _SCOPE, r"authentication key(?: (?P<rest>.+))?", "isis[タグ].options",
        "authentication key 7 <値>", _auth_key,
        note=(
            "手続き。`authentication key` を options に足す。鍵の値も型も段(level-1・level-2)も持たない(ADR-0056、REQ-67)。"
            "後ろが `[<型>] <値> [level-1|level-2]`(型は数か 8a)の形でない行(後ろの無い行も)は未解釈。"
            "isis.option より前に置く(形の合わない鍵の行を一般の行として読まない)。子を持つ行は未解釈"
        ),
    ),
    Pattern(
        "isis.af", _SCOPE, r"address-family (?P<family>.+)", "isis[タグ].address_families[種類]",
        "address-family ipv4 unicast", _address_family,
        note=(
            "手続き。種類の語を空白1つでつないだ原文を鍵にした項目を作る(子が無くても作る)。同じ種類は同じ項目にまとめる。"
            "中の行を isis.af.option で読む。" + _SECRET_NOTE
        ),
    ),
    Pattern(
        "isis.option", _SCOPE, r".+", "isis[タグ].options", "log-adjacency-changes", _option,
        note=(
            "手続き。ほかの子を持たない行の原文(空白は1つ)を options に足す(重ねない。昇順)。"
            "子を持つ行(`segment-routing mpls` など。その行も子孫も)、語のどれかが password・secret・key か"
            "語の中に鍵が埋まった行、項目と同じ設定の形が合わない行(no を外した頭の語が net・is-type・router-id・address-family の行と、"
            "`authentication key` で始まる行。`net` だけ、`router-id ipv6 …`、`no is-type` など)は未解釈"
        ),
    ),
    Pattern(
        "isis.af.option", _AF_SCOPE, r".+", "isis[タグ].address_families[種類].options", "maximum-paths 4", _af_option,
        note=(
            "手続き。address-family の中の子を持たない行の原文(空白は1つ)をその options に足す(重ねない。昇順)。"
            "子を持つ行(その行も子孫も)と、" + _SECRET_NOTE
        ),
    ),
)


def _read_children(node: HConfigChild, scope: str, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, scope, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if pattern.id == "isis.af":
            _read_children(child, _AF_SCOPE, ctx, interpreted)


def extract_isis(tree: HConfig, interpreted: InterpretedLines) -> dict[str, Isis]:
    """`router isis <タグ>` から、タグを鍵にした Isis の辞書を返す。"""
    ctx = _Ctx()
    for block in tree.children:
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        _read_children(block, _SCOPE, ctx, interpreted)
    return ctx.instances
