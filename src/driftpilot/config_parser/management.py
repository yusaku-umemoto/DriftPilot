"""機器の管理の入口(HTTP・SSH・SCP・API)と NX-OS の `feature` を IR の global に写す extractor(ADR-0051、REQ-62)。

読むのは、最上位の子を持たない1行(`ip http …`・`ip ssh …`・`ip scp …`・`nxapi …`。no 付きも)と、
Arista の最上位の `management <名前…>` のブロック(`management security` も。ADR-0114 決定1、REQ-159)と
IOS-XE の `ip ssh server certificate profile` のブロック(鍵はこの原文。ADR-0121、REQ-166)と
`ip ssh pubkey-chain` のブロック(鍵はこの原文。ADR-0130、REQ-175)、
NX-OS の `feature <名前…>`・`no feature <名前…>` の行。
1行は原文を management.settings に、ブロックの中の子を持たない行は見出しの次の段からの道すじを ` > ` でつないだ文字列を
management.blocks[<名前…>].settings に、feature は `<名前…>` か `no <名前…>` を features に、それぞれ集合にする。
有効・無効を真偽値にそろえない(ADR-0051 Option MB を採らない)。
ブロックの見出しと中の行は、語のどれかが password・secret・key・shared-secret の行に加えて、語の中に鍵が埋まった行
(device_settings と同じ判定)も入れない(REQ-159 §3-3)。
`password` が値ではなく語として入る、値の無い決まった形の行(`[no] ip ssh server authenticate user <方式>…`・
`[no] ip ssh server algorithm authentication <方式>…`。方式は password・publickey・keyboard)は、
秘密の行とみなさない(device_settings.is_secret_exempt と同じ判定。ADR-0124 決定1、REQ-169)。
`ip ssh pubkey-chain` の中の `username <名前>` は、子があっても原文を settings に足す。中のどの段でも、
頭の語が key-hash・key-string の行(公開鍵の指紋と本体)は、その行も子孫も読まない(ADR-0130 決定2・3、REQ-175)。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret, is_secret_exempt
from driftpilot.config_parser.ir import Management, ManagementBlock
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 語のどれかがこれらの行は入れない(未解釈に残す。依頼書 §3)。大文字小文字は区別しない
_SECRET_WORDS = frozenset({"password", "secret", "key", "shared-secret"})
_SEPARATOR = " > "
_BLOCK_SCOPE = "management"
_PUBKEY_SCOPE = "ip ssh pubkey-chain"
# ip ssh pubkey-chain の中で、頭の語がこれらの行は入れない(未解釈に残す。ADR-0130 決定2)。大文字小文字は区別しない
_PUBKEY_KEY_WORDS = frozenset({"key-hash", "key-string"})


@dataclass
class _Ctx:
    management: Management = field(default_factory=lambda: {"settings": [], "blocks": {}})
    features: list[str] = field(default_factory=list)
    has_children: bool = False
    # 見出しを読んだら、その鍵のブロック。中の行を読む
    block: ManagementBlock | None = None
    # 中の行を読む置き場所(scope)。ip ssh pubkey-chain の中だけ別の書式で読む
    scope: str = _BLOCK_SCOPE
    # 中の行を読んでいる間の状態。見出しの次の段からその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _has_secret(*texts: str) -> bool:
    """語のどれかが秘密の語なら True。is_secret_exempt に当たる text は判定から外す(ADR-0124 決定1、REQ-169)。"""
    return any(
        not is_secret_exempt(text) and any(word.lower() in _SECRET_WORDS for word in text.split())
        for text in texts
    )


def _block_has_secret(*texts: str) -> bool:
    """ブロックの見出しと中の行の判定。_has_secret に、語の中に鍵が埋まった形(`key=` など)を足す。"""
    return _has_secret(*texts) or has_secret(*texts)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _management_line(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行だけ、原文を management.settings に足す。子を持つ行は子も含めて未解釈。"""
    if ctx.has_children or _has_secret(m.string):
        return False
    _add(ctx.management["settings"], m.string)
    return True


def _management_block(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ見出しだけ読む。見出しは文字列にしない。子の無い見出しは未解釈。同じ見出しは同じ鍵にまとめる。"""
    if not ctx.has_children or _block_has_secret(m.string):
        return False
    ctx.block = ctx.management["blocks"].setdefault(m["name"] or m.string, {"settings": []})
    ctx.scope = _PUBKEY_SCOPE if m["pubkey"] else _BLOCK_SCOPE
    return True


def _block_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は道すじを集合に足す。子を持つ行は解釈済みにだけする。"""
    if ctx.block is None or _block_has_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.block["settings"], _SEPARATOR.join(ctx.path))
    return True


def _pubkey_user(m: re.Match[str], ctx: _Ctx) -> bool:
    """`username <名前>` は子があっても原文を集合に足す。名前の無い形・後ろに語が続く形と秘密の語の行は子孫も含めて未解釈。"""
    if ctx.block is None or not m["name"] or m["rest"] or _block_has_secret(*ctx.path):
        return False
    _add(ctx.block["settings"], m.string)
    return True


def _pubkey_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """頭の語が key-hash・key-string の行は子孫も含めて未解釈。ほかの行は _block_setting と同じに読む。"""
    if m.string.split()[0].lower() in _PUBKEY_KEY_WORDS:
        return False
    return _block_setting(m, ctx)


def _feature(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行だけ、`<名前…>` か `no <名前…>` を features に足す。"""
    if ctx.has_children or _has_secret(m.string):
        return False
    _add(ctx.features, f"no {m['name']}" if m["no"] else m["name"])
    return True


# 並び＝優先順位。最上位の行は、子を持たなければ management_block 以外、子を持てば management_line 以外に通す
# (`ip ssh server certificate profile` は両方の書き出しに当たる)。中の行は scope が違う。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "global.management_line", "top", r"(?:no )?(?:ip http|ip ssh|ip scp|nxapi) \S.*",
        "global.management.settings", "ip http server", _management_line,
        note=(
            "手続き。子を持たない行の原文を management.settings の集合に足す(重ねない。昇順)。"
            "子を持つ行と、語のどれかが password・secret・key・shared-secret の行は、子も含めて未解釈。"
            "ただし、行の語の並び全体が `[no] ip ssh server authenticate user <方式>…`・"
            "`[no] ip ssh server algorithm authentication <方式>…`(方式は1つ以上で、どれも password・publickey・"
            "keyboard)の行は、秘密の行とみなさずに読む(ADR-0124 決定1、REQ-169)"
        ),
    ),
    Pattern(
        "global.management_block", "top",
        r"management (?P<name>\S.*)|ip ssh server certificate profile|(?P<pubkey>ip ssh pubkey-chain)",
        "global.management.blocks", "management api http-commands", _management_block,
        note=(
            "手続き。子を持つ見出しだけ読み、management の後ろの語を鍵にして中の行を写す(同じ見出しは同じ鍵にまとめる。"
            "management security も読む)。IOS-XE の ip ssh server certificate profile は、この原文を鍵にする"
            "(ADR-0121、REQ-166)。ip ssh pubkey-chain も、この原文を鍵にし、中の行は ip ssh pubkey-chain の"
            "置き場所の書式で読む(ADR-0130、REQ-175)。見出しは文字列にしないが解釈済みにする。子の無い見出しと、"
            "語のどれかが password・secret・key・shared-secret か、語の中に鍵が埋まった(key, や password= などの)見出しは、"
            "見出しも中も未解釈"
        ),
    ),
    Pattern(
        "global.management_block.setting", _BLOCK_SCOPE, r".+", "global.management.blocks.*.settings", "protocol https",
        _block_setting,
        note=(
            "手続き。ブロックの中の子を持たない行を、見出しの次の段からの道すじを ` > ` でつないだ文字列にして集合に足す"
            "(見出しを含めない。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            "道すじのどの段かの語が password・secret・key・shared-secret か、語の中に鍵が埋まった"
            "(key, や password= などの)行は、その行も子孫も未解釈(同じブロックのほかの行は読む)"
        ),
    ),
    Pattern(
        "global.management_block.pubkey_user", _PUBKEY_SCOPE, r"username(?P<name> \S+)?(?P<rest> .+)?",
        "global.management.blocks.*.settings", "username example-user", _pubkey_user,
        note=(
            "手続き。ip ssh pubkey-chain の中の `username <名前>` の行を、子(公開鍵の行)があっても原文のまま集合に足す"
            "(同じ文字列は重ねず、文字列の昇順。ADR-0130 決定3、REQ-175)。子は同じ置き場所の書式で読む。"
            "名前の無い `username` の行と、名前の後ろに語が続く行と、道すじのどの段かの語が password・secret・key・shared-secret か、語の中に鍵が埋まった"
            "行は、その行も子孫も未解釈"
        ),
    ),
    Pattern(
        "global.management_block.pubkey_setting", _PUBKEY_SCOPE, r".+", "global.management.blocks.*.settings",
        "server 192.0.2.1", _pubkey_setting,
        note=(
            "手続き。ip ssh pubkey-chain の中(どの段でも)で、頭の語が key-hash・key-string の行(公開鍵の指紋と本体)は、"
            "その行も子孫も未解釈(ADR-0130 決定2、REQ-175)。ほかの行は management のブロックの中の行と同じに読む"
            "(子を持たない行は見出しの次の段からの道すじを ` > ` でつないだ文字列を集合に足す。子を持つ行は解釈済みにだけする。"
            "秘密の語の行は子孫も含めて未解釈)"
        ),
    ),
    Pattern(
        "global.feature", "top", r"(?P<no>no )?feature (?P<name>\S+(?: \S+)?)", "global.features", "feature bgp",
        _feature,
        note=(
            "手続き。子を持たない行の `<名前…>`(no 付きは `no <名前…>`)を features の集合に足す(重ねない。昇順)。"
            "名前は1語か2語。子を持つ行と、語のどれかが password・secret・key・shared-secret の行は、子も含めて未解釈"
        ),
    ),
)
_LEAF_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id != "global.management_block")
_BLOCK_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id != "global.management_line")


def _read_block(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """ブロックの中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, ctx.scope, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(child, ctx.path, ctx, interpreted)


def extract_management(tree: HConfig, interpreted: InterpretedLines) -> tuple[Management, list[str]]:
    """最上位の管理の入口の行とブロック、feature の行を読み、(management, features) を返す。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        ctx.block = None
        ctx.scope = _BLOCK_SCOPE
        patterns = _BLOCK_PATTERNS if block.children else _LEAF_PATTERNS
        pattern = apply_first(patterns, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        if ctx.block is not None:
            _read_block(block, [], ctx, interpreted)
    return ctx.management, ctx.features
