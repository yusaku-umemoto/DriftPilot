"""Arista の最上位の `mlag configuration` とその中の行を IR の mlag に写す extractor(ADR-0054、REQ-65)。

1台に1つの塊。見出しは子が無くても塊を作る。中の子を持たない行のうち、`domain-id`・`local-interface`・
`peer-address`・`peer-link` の後ろが1語のものは同じ名前の項目(原文)に、ほかの行は options(行の原文の集合)に写す。
`peer-address heartbeat …` は options に入れる。中の子を持つ行は、その行も子孫も未解釈。
4つの項目と同じ設定の、今は読まない形(後ろが0語・2語以上、`no domain-id` などの no 付き、`peer-address heartbeat`
だけの行)は、options に入れず未解釈に残す(REQ-65 の条件4)。語のどれかが password・secret・key の行と、語の中に
鍵が埋まった行も options に入れない(device_settings と同じ判定)。
インターフェースの中の `mlag <数>` と NX-OS の `vpc <数>` は interfaces.py が mlag_id に読む(ADR-0136 決定3、REQ-183)。
NX-OS の `vpc domain` はこの extractor では読まない(決定2)。
"""

import re
from bisect import insort
from dataclasses import dataclass

from hier_config import HConfig

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Mlag, new_mlag
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_SCOPE = "mlag configuration"
# 項目に写す設定の頭の語。形の合わない行と no 付きの形は options に入れない
_ITEM_WORDS = frozenset({"domain-id", "local-interface", "peer-address", "peer-link"})


@dataclass
class _Ctx:
    mlag: Mlag | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _configuration(m: re.Match[str], ctx: _Ctx) -> bool:
    """塊を作る。子が無くても作る。見出しが2回あれば同じ塊にまとめる。"""
    if ctx.mlag is None:
        ctx.mlag = new_mlag()
    return True


def _domain_id(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.mlag["domain_id"] = m["value"]
    return True


def _local_interface(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.mlag["local_interface"] = m["value"]
    return True


def _peer_address(m: re.Match[str], ctx: _Ctx) -> bool:
    """`peer-address heartbeat` だけの行は、heartbeat の引数が欠けた形なので相手のアドレスにしない(未解釈)。"""
    if ctx.has_children or m["value"] == "heartbeat":
        return False
    ctx.mlag["peer_address"] = m["value"]
    return True


def _peer_link(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children:
        return False
    ctx.mlag["peer_link"] = m["value"]
    return True


def _is_unread_form(line: str) -> bool:
    """4つの項目と同じ設定の、今は読まない形なら True。`no peer-address heartbeat …` は heartbeat の設定なので False。"""
    words = line.split()
    if words[0] == "no" and len(words) > 1:
        words = words[1:]
    return words[0] in _ITEM_WORDS and words[:2] != ["peer-address", "heartbeat"]


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.has_children or has_secret(m.string) or _is_unread_form(m.string):
        return False
    _add(ctx.mlag["options"], m.string)
    return True


_NOTE_ITEM = (
    "手続き。後ろの1語(原文)を写す。2回あれば後の行。後ろが0語・2語以上の行は当たらず、mlag.option でも入れない。"
    "子を持つ行は未解釈"
)

# 並び＝優先順位。mlag.option は残りすべてに当たるので、mlag configuration の最後。heartbeat は peer_address より前
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "mlag.configuration", "top", r"mlag configuration", "mlag", "mlag configuration", _configuration,
        note=(
            "手続き。mlag の塊を作る(子が無くても作る)。見出しが2回あれば同じ塊にまとめる。"
            "後ろに語が続く行(mlag configuration foo など)は当たらず未解釈"
        ),
    ),
    Pattern(
        "mlag.domain_id", _SCOPE, r"domain-id (?P<value>\S+)", "mlag.domain_id", "domain-id MLAG-EXAMPLE", _domain_id,
        note=_NOTE_ITEM,
    ),
    Pattern(
        "mlag.local_interface", _SCOPE, r"local-interface (?P<value>\S+)", "mlag.local_interface",
        "local-interface Vlan4094", _local_interface, note=_NOTE_ITEM,
    ),
    Pattern(
        "mlag.peer_address_heartbeat", _SCOPE, r"peer-address heartbeat .+", "mlag.options",
        "peer-address heartbeat 192.0.2.6 vrf MGMT", _option,
        note=(
            "手続き。行の原文を options に足す(重ねない。昇順)。mlag.peer_address より前に置く。"
            "子を持つ行と、語のどれかが password・secret・key か語の中に鍵が埋まった行は未解釈"
        ),
    ),
    Pattern(
        "mlag.peer_address", _SCOPE, r"peer-address (?P<value>\S+)", "mlag.peer_address", "peer-address 192.0.2.2",
        _peer_address,
        note=f"{_NOTE_ITEM}。`peer-address heartbeat` だけの行(heartbeat の引数が欠けた形)も未解釈",
    ),
    Pattern(
        "mlag.peer_link", _SCOPE, r"peer-link (?P<value>\S+)", "mlag.peer_link", "peer-link Port-Channel10", _peer_link,
        note=_NOTE_ITEM,
    ),
    Pattern(
        "mlag.option", _SCOPE, r".+", "mlag.options", "reload-delay mlag 300", _option,
        note=(
            "手続き。ほかの子を持たない行の原文を options に足す(重ねない。昇順)。子を持つ行(その行も子孫も)、"
            "語のどれかが password・secret・key か語の中に鍵が埋まった行、4つの項目と同じ設定の形が合わない行"
            "(no を外した頭の語が domain-id・local-interface・peer-address・peer-link。`domain-id` だけ、"
            "`peer-link Port-Channel10 extra`、`no domain-id` など)は未解釈。`no peer-address heartbeat …` は入れる"
        ),
    ),
)


def extract_mlag(tree: HConfig, interpreted: InterpretedLines) -> Mlag | None:
    """`mlag configuration` とその中の行を Mlag に写す。見出しが無ければ None。"""
    ctx = _Ctx()
    for block in tree.children:
        pattern = apply_first(PATTERNS, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        for child in block.children:
            ctx.has_children = bool(child.children)
            pattern = apply_first(PATTERNS, _SCOPE, child.text, ctx)
            if pattern is not None:
                interpreted.mark(child, pattern.id)
    return ctx.mlag
