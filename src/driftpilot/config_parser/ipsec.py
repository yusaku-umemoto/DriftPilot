"""Cisco IOS の IPsec の VPN の最上位のブロックと行を IR の ipsec に写す extractor(ADR-0104、REQ-85)。

読むのは、子を持つ見出し(`crypto isakmp policy <1語>`・`crypto isakmp profile <1語>`・`crypto keyring <1語> …`・
`crypto ipsec transform-set <1語> …`・`crypto ipsec profile <1語>`・`crypto map <1語> <数> …`・
`crypto ikev2 proposal|policy|profile|keyring <1語>`・`crypto ikev2 authorization policy <1語>`)の中の子を持たない行と、
子を持たない最上位の `crypto isakmp|ipsec|ikev2 …` の行(no 付きも)。見出しから `crypto ` を外した原文を鍵にした、
行の原文の集合にする(暗号の方式・相手のアドレスを固定の項目にしない。ADR-0104 Option IB を採らない)。
`crypto ikev2 authorization policy …` の見出しと no 付きの行は ADR-0121、REQ-166。
鍵の値は IR に入れない。語が key・password・secret に等しい最初の位置から行の終わりまでを外し、ブロックの中の行では
keys_configured を True に、最上位の行では末尾に ` <key>` を付ける。外した残りに語の中に鍵が埋まった形(`key=` など)が
残る行は未解釈にする。`pre-shared-key` は語として key と等しくないので外さない。
`crypto pki …` は certificates.py・trustpoints.py が読み、`crypto key …` は読まない。
インターフェースの `tunnel …` は interfaces.py が読む。

Arista の最上位の `ip security` も同じ ipsec に写す(機種をまたいで同じカテゴリで見る。ADR-0113 Option KB を採らない)。
中の子を持つ行(`ike policy <名前>`・`sa policy <名前>`・`profile <名前>`・`key controller` など)ごとに
`ip security ` + その行の原文を鍵にした項目を作り、その中の子を持たない行を、鍵を外した原文(さらに子を持つ行の中では
道すじを ` > ` でつないだもの)の集合にする。ここでは鍵の語に shared-key・pre-shared-key も足す(ADR-0113 決定2)。
`key controller` は鍵の値を持たない見出しなので読み、ほかの鍵の語を持つ中の見出しは、見出しも中も未解釈にする。

NEC IX の最上位の `ike proposal|policy <名前> …`・`ike keepalive|local-id|remote-id|commit-bit|suppress-dangling <名前> …`・
`ike nat-traversal policy <名前>`(ike の塊)と、`ipsec autokey-proposal <名前> …`・`ipsec autokey-map|dynamic-map|
manualkey-map <名前> …`・`ipsec local-id|remote-id|commit-bit <名前> …`(ipsec の塊)の1行、`ikev2 default-profile`・
`ikev2 profile <名前>` のブロックも同じ ipsec に写す(ADR-0209 決定3・4、REQ-292)。名前でつながる行は、名前ごとに1つの塊にまとめる。
鍵の行(`ike policy … key …`・`ikev2 authentication psk … key …`・`ipsec manualkey …` など)は、伏せる表の秘密語がある行を
見分ける has_mask_word が真になるので、IR に入れず未解釈のままにする(鍵を外した残りも入れない)。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_mask_word, has_secret
from driftpilot.config_parser.ir import Ipsec, IpsecBlock, new_ipsec
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# 語がこれらに等しい位置から行の終わりまでを外す(REQ-85 §3-1)。大文字小文字は区別しない
_KEY_WORDS = frozenset({"key", "password", "secret"})
# Arista の `ip security` の中で外す語(REQ-159 §3-2。ADR-0113 決定2)
_ARISTA_KEY_WORDS = _KEY_WORDS | {"shared-key", "pre-shared-key"}
# `ip security` の中の見出しのうち、語に key があっても鍵の値を持たないもの(REQ-159 §3-2)
_KEY_CONTROLLER = "key controller"
_KEY_MARK = " <key>"
_SEPARATOR = " > "
_BLOCK_SCOPE = "crypto block"
_IKEV2_PROFILE_SCOPE = "ikev2 profile"
_IP_SECURITY_SCOPE = "ip security"
_IP_SECURITY_BLOCK_SCOPE = "ip security block"


@dataclass
class _Ctx:
    ipsec: Ipsec | None = None
    current: IpsecBlock | None = None
    # 読んでいる行が子を持つか
    has_children: bool = False
    # `ip security` の中の項目を読んでいる間の状態。項目の見出しの次の段からその行までの行の文字列
    path: list[str] = field(default_factory=list)


def _ipsec(ctx: _Ctx) -> Ipsec:
    """ipsec を返す。無ければ作る(行が写せたときにだけ呼ぶ)。"""
    if ctx.ipsec is None:
        ctx.ipsec = new_ipsec()
    return ctx.ipsec


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _strip_key(text: str, key_words: frozenset[str] = _KEY_WORDS) -> tuple[str, bool]:
    """最初に語が key_words(既定は key・password・secret)に等しい位置から後ろを外した残りと、外したかを返す。"""
    words = text.split()
    for i, word in enumerate(words):
        if word.lower() in key_words:
            return " ".join(words[:i]), True
    return " ".join(words), False


def _is_arista_secret(text: str) -> bool:
    """語のどれかが Arista の鍵の語か、語の中に鍵が埋まった形(`key=` など)があれば True。"""
    return has_secret(text) or any(word.lower() in _ARISTA_KEY_WORDS for word in text.split())


def _block(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ見出しだけ読む。同じ見出しは同じ項目にまとめる。"""
    if not ctx.has_children or has_secret(m.string):
        return False
    ctx.current = _ipsec(ctx)["blocks"].setdefault(m["key"], {"settings": [], "keys_configured": False})
    return True


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行の原文を options に足す。鍵を外した行は末尾に ` <key>` を付ける。"""
    rest, stripped = _strip_key(m.string)
    if ctx.has_children or has_secret(rest):
        return False
    _add(_ipsec(ctx)["options"], rest + _KEY_MARK if stripped else rest)
    return True


def _setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行の原文(鍵を外した残り)を settings に足す。子を持つ行は子も含めて未解釈。"""
    rest, stripped = _strip_key(m.string)
    if ctx.has_children or has_secret(rest):
        return False
    if stripped:
        ctx.current["keys_configured"] = True
    if rest:
        _add(ctx.current["settings"], rest)
    return True


def _ip_security(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ `ip security` だけ読む。見出しは文字列にしない。子の無い見出しは未解釈。"""
    return ctx.has_children


def _ip_security_block(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ行ごとに `ip security <原文>` の項目を作る。鍵の語を持つ行は(key controller を除き)子も含めて未解釈。"""
    if not ctx.has_children:
        return False
    if m.string.lower() != _KEY_CONTROLLER and _is_arista_secret(m.string):
        return False
    ctx.current = _ipsec(ctx)["blocks"].setdefault(
        f"{_IP_SECURITY_SCOPE} {m.string}", {"settings": [], "keys_configured": False}
    )
    return True


def _ip_security_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は鍵を外した残りを、途中の段の道すじとつないで settings に足す。子を持つ行は解釈済みにだけする。"""
    if ctx.has_children:
        return not _is_arista_secret(m.string)
    rest, stripped = _strip_key(m.string, _ARISTA_KEY_WORDS)
    if has_secret(rest):
        return False
    if stripped:
        ctx.current["keys_configured"] = True
    if rest:
        _add(ctx.current["settings"], _SEPARATOR.join([*ctx.path[:-1], rest]))
    return True


def _named_block(key: str, ctx: _Ctx) -> IpsecBlock:
    """名前でつながる塊(keys_configured は偽)を返す。無ければ作る。同じ鍵の行は1つの塊にまとめる。"""
    ctx.current = _ipsec(ctx)["blocks"].setdefault(key, {"settings": [], "keys_configured": False})
    return ctx.current


def _join(*words: str | None) -> str:
    """None と空を除いて、空白1つでつなぐ。"""
    return " ".join(word for word in words if word)


def _ike(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ike proposal|policy <名前> [<残り>]`。残りを塊 `ike <種類> <名前>` の settings に足す(無ければ塊だけ作る)。

    塊は、秘密語がある行(鍵の行)でも作る。ただしその行は未解釈(鍵を外した残りも入れない)。子を持つ行は未解釈。
    """
    if ctx.has_children:
        return False
    block = _named_block(f"ike {m['head']} {m['name']}", ctx)
    if has_mask_word(m.string):
        return False
    if m["rest"]:
        _add(block["settings"], m["rest"])
    return True


def _ike_policy_option(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ike keepalive|local-id|… <名前> [<残り>]`。塊 `ike policy <名前>` の settings に `<語> [<残り>]` を足す。"""
    if ctx.has_children:
        return False
    block = _named_block(f"ike policy {m['name']}", ctx)
    if has_mask_word(m.string):
        return False
    _add(block["settings"], _join(m["word"], m["rest"]))
    return True


def _ike_nat_traversal(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ike nat-traversal policy <名前>`。塊 `ike policy <名前>` の settings に `nat-traversal` を足す。"""
    if ctx.has_children:
        return False
    block = _named_block(f"ike policy {m['name']}", ctx)
    if has_mask_word(m.string):
        return False
    _add(block["settings"], "nat-traversal")
    return True


def _autokey_proposal(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ipsec autokey-proposal <名前> [<残り>]`。塊 `ipsec autokey-proposal <名前>` の settings に残りを足す。

    秘密語がある行と子を持つ行は、塊も作らず未解釈。
    """
    if ctx.has_children or has_mask_word(m.string):
        return False
    block = _named_block(f"ipsec autokey-proposal {m['name']}", ctx)
    if m["rest"]:
        _add(block["settings"], m["rest"])
    return True


def _ipsec_map(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ipsec autokey-map|dynamic-map|manualkey-map|local-id|remote-id|commit-bit <名前> [<残り>]`。

    塊 `ipsec map <名前>` の settings に `<語> [<残り>]` を足す。行の順によらず同じ名前は1つの塊。
    秘密語がある行と子を持つ行は、塊も作らず未解釈。
    """
    if ctx.has_children or has_mask_word(m.string):
        return False
    _add(_named_block(f"ipsec map {m['name']}", ctx)["settings"], _join(m["word"], m["rest"]))
    return True


def _ikev2_profile(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ `ikev2 default-profile`・`ikev2 profile <名前>` だけ読む。見出しの原文を鍵にした塊を作る。

    子を持たない行と、秘密語がある見出しは、塊も作らず未解釈。
    """
    if not ctx.has_children or has_mask_word(m.string):
        return False
    _named_block(m.string, ctx)
    return True


def _ikev2_profile_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ikev2` の塊の中の子を持たない行の原文(空白は1つ)を settings に足す。

    子を持つ行と、秘密語がある行(`local-authentication psk …`・`sa-proposal enc …` など)は、子も含めて未解釈。
    """
    if ctx.has_children or has_mask_word(m.string):
        return False
    _add(ctx.current["settings"], " ".join(m.string.split()))
    return True


_STRIP_NOTE = (
    "語が key・password・secret に等しい最初の位置から行の終わりまでを外す(大文字小文字は区別しない。"
    "pre-shared-key は外さない)。外した残りに語の中に鍵が埋まった(key, や password= などの)形が残る行は未解釈"
)

# 並び＝優先順位。最上位の行は、子を持たなければ1行の書式だけ、子を持てば見出しの書式だけに通す
# (`crypto ipsec transform-set …` は両方に当たる)。中の行は scope が違う。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "ipsec.block", "top",
        r"crypto (?P<key>isakmp policy \S+|isakmp profile \S+|keyring \S+(?: .+)?|ipsec transform-set \S+(?: .+)?"
        r"|ipsec profile \S+|map \S+ \d+(?: .+)?|ikev2 (?:proposal|policy|profile|keyring) \S+"
        r"|ikev2 authorization policy \S+)",
        "ipsec.blocks[見出しから crypto を外した原文]", "crypto isakmp policy 10", _block,
        note=(
            "手続き。子を持つ見出しだけ読み、見出しから `crypto ` を外した原文を鍵にした項目を作る。"
            "同じ見出しが2回あれば同じ項目にまとめる。語のどれかが password・secret・key か、語の中に鍵が埋まった見出しは、"
            "見出しも中も未解釈。対象でない子を持つ crypto のブロックは未解釈。"
            "ikev2 authorization policy <1語> は ADR-0121、REQ-166"
        ),
    ),
    Pattern(
        "ipsec.option", "top", r"(?:no )?crypto (?:isakmp|ipsec|ikev2) .+", "ipsec.options",
        "crypto isakmp keepalive 10 10", _option,
        note=(
            "手続き。子を持たない行の原文を options の集合に足す(同じ文字列は重ねず、文字列の昇順。"
            "no 付きの行も原文のまま。ADR-0121、REQ-166)。"
            f"{_STRIP_NOTE}。外した行は末尾に ` <key>` を付ける(例 `crypto isakmp <key>`)。子を持つ行は ipsec.block だけで試す"
        ),
    ),
    Pattern(
        "ipsec.block.setting", _BLOCK_SCOPE, r".+", "ipsec.blocks[鍵].settings / keys_configured", "encr aes", _setting,
        note=(
            "手続き。ブロックの中の子を持たない行の原文を settings の集合に足す(同じ文字列は重ねず、文字列の昇順)。"
            f"{_STRIP_NOTE}。外したら keys_configured を True にし、残りが空でなければ足す。"
            "子を持つ行(ikev2 keyring の peer など)は、子も含めて未解釈"
        ),
    ),
    Pattern(
        "ipsec.ip_security", "top", r"ip security", "ipsec.blocks[ip security <中の見出し>]", "ip security",
        _ip_security,
        note=(
            "手続き。Arista の子を持つ `ip security` だけ読み、中の子を持つ行ごとに項目を作る(ipsec.ip_security.block)。"
            "見出しは文字列にしないが解釈済みにする。子の無い見出しは未解釈"
        ),
    ),
    Pattern(
        "ipsec.ip_security.block", _IP_SECURITY_SCOPE, r".+", "ipsec.blocks[ip security <行の原文>]", "profile PROF-1",
        _ip_security_block,
        note=(
            "手続き。`ip security` の中の子を持つ行ごとに、`ip security ` + 行の原文を鍵にした項目を作る"
            "(keys_configured の既定は False。同じ見出しは同じ項目にまとめる)。見出しは文字列にしないが解釈済みにする。"
            "語のどれかが key・password・secret・shared-key・pre-shared-key か、語の中に鍵が埋まった見出しは、"
            "見出しも中も未解釈(`key controller` は鍵の値を持たないので読む)。子を持たない行は未解釈"
        ),
    ),
    Pattern(
        "ipsec.ip_security.setting", _IP_SECURITY_BLOCK_SCOPE, r".+", "ipsec.blocks[鍵].settings / keys_configured",
        "esp encryption aes256gcm128", _ip_security_setting,
        note=(
            "手続き。項目の中の子を持たない行の原文を、途中の段の道すじと ` > ` でつないで settings の集合に足す"
            "(項目の見出しは含めない。同じ文字列は重ねず、文字列の昇順)。"
            "語が key・password・secret・shared-key・pre-shared-key に等しい最初の位置から行の終わりまでを外し"
            "(大文字小文字は区別しない)、外したら keys_configured を True にし、残りが空でなければ足す。"
            "外した残りに語の中に鍵が埋まった(key, や password= などの)形が残る行は未解釈。"
            "子を持つ行は文字列にしないが解釈済みにする。ただし鍵の語を持つか語の中に鍵が埋まった行は、その行も子孫も未解釈"
        ),
    ),
    Pattern(
        "ipsec.ike", "top", r"ike (?P<head>proposal|policy) (?P<name>\S+)(?: (?P<rest>.+))?",
        "ipsec.blocks[ike proposal|policy <名前>].settings",
        "ike proposal IKE-PROP1 encryption aes-256 hash sha2-256 group 2048-bit lifetime 86400", _ike,
        note=(
            "手続き。NEC IX の子を持たない行。残りを塊 `ike <種類> <名前>` の settings の集合に足す"
            "(残りが無ければ塊だけ作る。同じ文字列は重ねず、文字列の昇順。keys_configured は偽のまま)。"
            "伏せる表の秘密語がある行(`ike policy <名前> peer … key …` など)は、塊は作るが行は未解釈"
            "(鍵を外した残りも入れない)。子を持つ行は未解釈(ADR-0209 決定3、REQ-292)"
        ),
    ),
    Pattern(
        "ipsec.ike_policy_option", "top",
        r"ike (?P<word>keepalive|local-id|remote-id|commit-bit|suppress-dangling) (?P<name>\S+)(?: (?P<rest>.+))?",
        "ipsec.blocks[ike policy <名前>].settings", "ike keepalive IKE-P1 10 3", _ike_policy_option,
        note=(
            "手続き。NEC IX の子を持たない行。塊 `ike policy <名前>` の settings に `<語> [<残り>]` を足す"
            "(見出しの `ike policy <名前> …` が無くても塊を作り、同じ名前は1つにまとめる)。"
            "秘密語がある行は塊は作るが行は未解釈(ADR-0209 決定3、REQ-292)"
        ),
    ),
    Pattern(
        "ipsec.ike_nat_traversal", "top", r"ike nat-traversal policy (?P<name>\S+)",
        "ipsec.blocks[ike policy <名前>].settings", "ike nat-traversal policy IKE-P1", _ike_nat_traversal,
        note="手続き。NEC IX の行。塊 `ike policy <名前>` の settings に `nat-traversal` を足す(ADR-0209 決定3、REQ-292)",
    ),
    Pattern(
        "ipsec.autokey_proposal", "top", r"ipsec autokey-proposal (?P<name>\S+)(?: (?P<rest>.+))?",
        "ipsec.blocks[ipsec autokey-proposal <名前>].settings", "ipsec autokey-proposal IPSEC-PROP1 esp-aes-256 esp-sha",
        _autokey_proposal,
        note=(
            "手続き。NEC IX の子を持たない行。残りを塊 `ipsec autokey-proposal <名前>` の settings の集合に足す"
            "(残りが無ければ塊だけ作る)。秘密語がある行と子を持つ行は、塊も作らず未解釈(ADR-0209 決定4、REQ-292)"
        ),
    ),
    Pattern(
        "ipsec.map", "top",
        r"ipsec (?P<word>autokey-map|dynamic-map|manualkey-map) (?P<name>\S+)(?: (?P<rest>.+))?",
        "ipsec.blocks[ipsec map <名前>].settings", "ipsec autokey-map IPSEC-M1 ACL-VPN peer 192.0.2.1 IPSEC-PROP1",
        _ipsec_map,
        note=(
            "手続き。NEC IX の子を持たない行。塊 `ipsec map <名前>` の settings に `<語> [<残り>]` を足す"
            "(同じ名前の行は、行の順によらず1つの塊にまとめる)。秘密語がある行と子を持つ行は、塊も作らず未解釈"
            "(ADR-0209 決定4、REQ-292)"
        ),
    ),
    Pattern(
        "ipsec.map_option", "top",
        r"ipsec (?P<word>local-id|remote-id|commit-bit) (?P<name>\S+)(?: (?P<rest>.+))?",
        "ipsec.blocks[ipsec map <名前>].settings", "ipsec local-id IPSEC-M1 198.51.100.0/24", _ipsec_map,
        note="手続き。ipsec.map と同じ塊 `ipsec map <名前>` に、`<語> [<残り>]` を足す(ADR-0209 決定4、REQ-292)",
    ),
    Pattern(
        "ipsec.ikev2_profile", "top", r"ikev2 (?:default-profile|profile \S+)", "ipsec.blocks[見出しの原文]",
        "ikev2 default-profile", _ikev2_profile,
        note=(
            "手続き。NEC IX の子を持つ見出しだけ読み、見出しの原文を鍵にした塊を作る(中の行は ipsec.ikev2_profile.setting)。"
            "子が1行も読めなくても見出しは解釈済み。子を持たない行と、秘密語がある見出しは、塊も作らず未解釈"
            "(ADR-0209 決定4、REQ-292)"
        ),
    ),
    Pattern(
        "ipsec.ikev2_profile.setting", _IKEV2_PROFILE_SCOPE, r".+", "ipsec.blocks[鍵].settings", "dpd interval 30",
        _ikev2_profile_setting,
        note=(
            "手続き。`ikev2` の塊の中の子を持たない行の原文(空白は1つ)を settings の集合に足す。"
            "子を持つ行は子も含めて未解釈。伏せる表の秘密語がある行(`local-authentication psk`・`sa-proposal enc …` など)"
            "は未解釈(ADR-0209 決定4、REQ-292)"
        ),
    ),
)
_SINGLE_LINE_IDS = frozenset({
    "ipsec.ike", "ipsec.ike_policy_option", "ipsec.ike_nat_traversal",
    "ipsec.autokey_proposal", "ipsec.map", "ipsec.map_option",
})
_LEAF_PATTERNS = tuple(
    pattern for pattern in PATTERNS if pattern.id not in {"ipsec.block", "ipsec.ip_security", "ipsec.ikev2_profile"}
)
_BLOCK_PATTERNS = tuple(
    pattern for pattern in PATTERNS if pattern.id != "ipsec.option" and pattern.id not in _SINGLE_LINE_IDS
)
# 見出しの id → 中の行の scope
_CHILD_SCOPES = {"ipsec.block": _BLOCK_SCOPE, "ipsec.ikev2_profile": _IKEV2_PROFILE_SCOPE}


def _read_ip_security(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """`ip security` の項目の中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, _IP_SECURITY_BLOCK_SCOPE, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_ip_security(child, ctx.path, ctx, interpreted)


def extract_ipsec(tree: HConfig, interpreted: InterpretedLines) -> Ipsec | None:
    """最上位の IPsec のブロックと行を Ipsec に写す。1行も写せなければ None。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        patterns = _BLOCK_PATTERNS if block.children else _LEAF_PATTERNS
        pattern = apply_first(patterns, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        if pattern.id == "ipsec.ip_security":
            for line in block.children:
                ctx.has_children = bool(line.children)
                if (heading := apply_first(PATTERNS, _IP_SECURITY_SCOPE, line.text, ctx)) is not None:
                    interpreted.mark(line, heading.id)
                    _read_ip_security(line, [], ctx, interpreted)
            continue
        if (scope := _CHILD_SCOPES.get(pattern.id)) is None:
            continue
        for line in block.children:
            ctx.has_children = bool(line.children)
            if (setting := apply_first(PATTERNS, scope, line.text, ctx)) is not None:
                interpreted.mark(line, setting.id)
    return ctx.ipsec
