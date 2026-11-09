"""`line con` / `line vty` / `line aux` のブロックを IR の lines に写す extractor(REQ-23)。

共通スキーマには、異機種間で突き合わせる意味のある項目(認証・接続方式・タイムアウト・特権レベル)だけを写す。
端末の表示や物理の設定(stopbits / length など)は vendor_specific の固定キーに写す。
パスワードの値は IR に持たない。`password …` の行は has_password を立てるだけで、値はどこにも残さない
(残すと保存ファイルと LLM へ送る差分に流れる経路が増える。ADR-0017・ADR-0019)。
書式に合って IR に写せた行だけを解釈済みにし、合わない行は unparsed に残す。

REQ-84(ADR-0103)で、ほかの書式に当たらない行も読む。`access-class …`・`ipv6 access-class …` は原文を access_class に、
それ以外の子を持たない行は原文を options に、子を持つ行は中の子を持たない行を道すじ(` > ` でつなぐ)にして options に足す。
今の項目と同じ設定の、今は読まない形(`exec-timeout 5 0 0` など。_OWNED の語で始まる行)は options に入れず未解釈に残す
(同じ設定が形によって項目と原文の2か所に分かれると、1回の切り替えが差分2件になるため)。
語のどれかが password・secret・key の行と、語の中に鍵が埋まった行は入れない(device_settings と同じ判定)。
"""

import re
from bisect import insort
from dataclasses import dataclass
from typing import Any

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.ir import Line, LoginMode, new_line
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# `console` は `con` の長い綴り
_TYPE_ALIASES = {"console": "con"}
# vendor_specific["cisco"]["lines"][キー] の固定キー
VENDOR_KEYS: tuple[str, ...] = (
    "stopbits",
    "length",
    "width",
    "history_size",
    "timeout_login_response",
    "editing",
    "logging_synchronous",
    "motd_banner",
    "exec_banner",
    "media_type_rj45",
    # 台の番号(文字列)から真偽値への辞書(ADR-0111 決定7、REQ-158)
    "media_type_rj45_switch",
)
# 今の項目(vendor_specific を含む)と同じ設定を書く行の頭の語。no 付きも含め、options に入れない(REQ-84、ADR-0103)
_OWNED: tuple[str, ...] = (
    "login",
    "transport input",
    "exec-timeout",
    "privilege level",
    "password",
    "stopbits",
    "length",
    "width",
    "history size",
    "timeout login response",
    "editing",
    "logging synchronous",
    "motd-banner",
    "exec-banner",
    "media-type",
    "access-class",
    "ipv6 access-class",
)
_SEPARATOR = " > "


@dataclass
class _Ctx:
    lines: dict[str, Line]
    vendor: dict[str, dict[str, Any]]
    key: str = ""
    current: Line | None = None
    # 見出しが書式に合ったが、同じキーが二度目に現れたので読まなかった
    duplicate: bool = False
    # 読んでいる子の行が子を持つか
    has_children: bool = False


def _open(m: re.Match[str], ctx: _Ctx) -> bool:
    """line を作る。キーは種別と先頭の番号。同じキーがすでにあれば読まない(子行も password 以外は読まない)。"""
    type_ = _TYPE_ALIASES.get(m["type"], m["type"])
    first = int(m["first"]) if m["first"] is not None else None
    last = int(m["last"]) if m["last"] is not None else None
    key = type_ if first is None else f"{type_} {first}"
    if key in ctx.lines:
        ctx.duplicate = True
        return False
    ctx.key = key
    ctx.current = new_line(type_, first, last)  # type: ignore[arg-type]
    ctx.lines[key] = ctx.current
    return True


def _set_login(mode: LoginMode, ctx: _Ctx, login_list: str | None = None) -> bool:
    if ctx.current is None or ctx.current["login"] is not None:
        return False
    ctx.current["login"] = mode
    ctx.current["login_list"] = login_list
    return True


def _login(m: re.Match[str], ctx: _Ctx) -> bool:
    return _set_login("password", ctx)


def _no_login(m: re.Match[str], ctx: _Ctx) -> bool:
    return _set_login("none", ctx)


def _login_local(m: re.Match[str], ctx: _Ctx) -> bool:
    return _set_login("local", ctx)


def _login_authentication(m: re.Match[str], ctx: _Ctx) -> bool:
    return _set_login("list", ctx, m["name"])


def _transport_input(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.current is None or ctx.current["transport_input"]:
        return False
    ctx.current["transport_input"] = m["methods"].split()
    return True


def _exec_timeout(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.current is None or ctx.current["exec_timeout_minutes"] is not None:
        return False
    ctx.current["exec_timeout_minutes"] = int(m["minutes"])
    # 数が1つの `exec-timeout <分>` は秒を 0 にする(`exec-timeout <分> 0` と同じ値。ADR-0136 決定3、REQ-183)
    ctx.current["exec_timeout_seconds"] = int(m["seconds"] or 0)
    return True


def _privilege_level(m: re.Match[str], ctx: _Ctx) -> bool:
    if ctx.current is None or ctx.current["privilege_level"] is not None:
        return False
    ctx.current["privilege_level"] = int(m["level"])
    return True


def _password(m: re.Match[str], ctx: _Ctx) -> bool:
    """値は読まない。line を読んでいれば has_password を立てる。重複したブロックでも行は解釈済みにする。"""
    if ctx.current is not None:
        ctx.current["has_password"] = True
    return True


def _vendor(name: str, value: Any, ctx: _Ctx) -> bool:
    if ctx.current is None:
        return False
    entry = ctx.vendor.setdefault(ctx.key, dict.fromkeys(VENDOR_KEYS))
    if entry[name] is not None:
        return False
    entry[name] = value
    return True


def _media_type_rj45_switch(m: re.Match[str], ctx: _Ctx) -> bool:
    """台の番号から真偽値への辞書に足す。`no` 付きは False。同じ台の番号が二度目に現れたら読まない。"""
    if ctx.current is None:
        return False
    entry = ctx.vendor.setdefault(ctx.key, dict.fromkeys(VENDOR_KEYS))
    switches = entry["media_type_rj45_switch"] or {}
    if m["n"] in switches:
        return False
    switches[m["n"]] = m["no"] is None
    entry["media_type_rj45_switch"] = switches
    return True


def _vendor_int(name: str):
    return lambda m, ctx: _vendor(name, int(m["value"]), ctx)


def _vendor_flag(name: str):
    return lambda m, ctx: _vendor(name, m["no"] is None, ctx)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _access_class(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行だけ、原文を access_class に足す。"""
    if ctx.current is None or ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.current["access_class"], m.string)
    return True


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は原文を options に足す。子を持つ行は文字列にせず、中の行を extract_lines が読む。"""
    if ctx.current is None or has_secret(m.string):
        return False
    if not ctx.has_children:
        _add(ctx.current["options"], m.string)
    return True


_ONCE = "二度目に現れた行は上書きせず読まない(未解釈)"
_VENDOR = "vendor_specific。端末の表示や物理の設定で、異機種間で突き合わせない。" + _ONCE
_FLAG = "vendor_specific。`no` 付きは False。" + _ONCE

# 並び＝優先順位。見出しは top、子行は line。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "lines.line", "top", r"line (?P<type>con|console|vty|aux)(?: (?P<first>\d+)(?: (?P<last>\d+))?)?",
        "lines[種別 先頭の番号]", "line vty 0 4", _open,
        note=(
            "手続き。キーは種別と先頭の番号(`vty 0`、番号なしは `vty`)。console は con に寄せる。"
            "同じキーが二度目に現れたら読まない(子行も password 以外は読まない)"
        ),
    ),
    Pattern("lines.login", "line", r"login", "login(password)", "login", _login, note=_ONCE),
    Pattern("lines.no_login", "line", r"no login", "login(none)", "no login", _no_login, note=_ONCE),
    Pattern("lines.login_local", "line", r"login local", "login(local)", "login local", _login_local, note=_ONCE),
    Pattern(
        "lines.login_authentication", "line", r"login authentication (?P<name>\S+)", "login(list) / login_list",
        "login authentication VTY-AUTH", _login_authentication, note=_ONCE,
    ),
    Pattern(
        "lines.transport_input", "line",
        r"transport input (?P<methods>(?:all|none|telnet|ssh)(?: (?:all|none|telnet|ssh))*)", "transport_input",
        "transport input telnet ssh", _transport_input, note="書かれた順の一覧にする。" + _ONCE,
    ),
    Pattern(
        "lines.exec_timeout", "line", r"exec-timeout (?P<minutes>\d+)(?: (?P<seconds>\d+))?",
        "exec_timeout_minutes / exec_timeout_seconds", "exec-timeout 5 0", _exec_timeout,
        note="手続き。数が1つの `exec-timeout <分>` は秒を 0 にする(`exec-timeout <分> 0` と同じ値。ADR-0136 決定3、"
        "REQ-183)。数が3つ以上の書式は読まない(未解釈)。" + _ONCE,
    ),
    Pattern(
        "lines.privilege_level", "line", r"privilege level (?P<level>\d+)", "privilege_level",
        "privilege level 15", _privilege_level, note=_ONCE,
    ),
    Pattern(
        "lines.password", "line", r"password .+", "has_password", "password 7 <値>", _password,
        note="手続き。値は IR に持たず、has_password を True にするだけ。何度現れても解釈済みにする(値を unparsed に残さない)",
    ),
    Pattern(
        "lines.stopbits", "line", r"stopbits (?P<value>\d+)", "vendor_specific.cisco.lines[キー].stopbits",
        "stopbits 1", _vendor_int("stopbits"), note=_VENDOR,
    ),
    Pattern(
        "lines.length", "line", r"length (?P<value>\d+)", "vendor_specific.cisco.lines[キー].length",
        "length 0", _vendor_int("length"), note=_VENDOR,
    ),
    Pattern(
        "lines.width", "line", r"width (?P<value>\d+)", "vendor_specific.cisco.lines[キー].width",
        "width 80", _vendor_int("width"), note=_VENDOR,
    ),
    Pattern(
        "lines.history_size", "line", r"history size (?P<value>\d+)",
        "vendor_specific.cisco.lines[キー].history_size", "history size 10", _vendor_int("history_size"),
        note=_VENDOR,
    ),
    Pattern(
        "lines.timeout_login_response", "line", r"timeout login response (?P<value>\d+)",
        "vendor_specific.cisco.lines[キー].timeout_login_response", "timeout login response 30",
        _vendor_int("timeout_login_response"), note=_VENDOR,
    ),
    Pattern(
        "lines.editing", "line", r"(?P<no>no )?editing", "vendor_specific.cisco.lines[キー].editing",
        "editing", _vendor_flag("editing"), note=_FLAG,
    ),
    Pattern(
        "lines.logging_synchronous", "line", r"(?P<no>no )?logging synchronous",
        "vendor_specific.cisco.lines[キー].logging_synchronous", "logging synchronous",
        _vendor_flag("logging_synchronous"), note=_FLAG,
    ),
    Pattern(
        "lines.motd_banner", "line", r"(?P<no>no )?motd-banner", "vendor_specific.cisco.lines[キー].motd_banner",
        "motd-banner", _vendor_flag("motd_banner"), note=_FLAG,
    ),
    Pattern(
        "lines.exec_banner", "line", r"(?P<no>no )?exec-banner", "vendor_specific.cisco.lines[キー].exec_banner",
        "exec-banner", _vendor_flag("exec_banner"), note=_FLAG,
    ),
    Pattern(
        "lines.media_type_rj45_switch", "line", r"(?P<no>no )?media-type rj45 switch (?P<n>\d+)",
        "vendor_specific.cisco.lines[キー].media_type_rj45_switch[台の番号]", "no media-type rj45 switch 1",
        _media_type_rj45_switch,
        note=(
            "手続き。vendor_specific。台の番号(文字列)から真偽値への辞書に足す。`no` 付きは False"
            "(ADR-0111 決定7)。同じ台の番号が二度目に現れた行は上書きせず読まない(未解釈)。"
            "lines.media_type_rj45 より前に置く"
        ),
    ),
    Pattern(
        "lines.media_type_rj45", "line", r"(?P<no>no )?media-type rj45",
        "vendor_specific.cisco.lines[キー].media_type_rj45", "no media-type rj45", _vendor_flag("media_type_rj45"),
        note=_FLAG + "`switch <n>` 付きは lines.media_type_rj45_switch が読む",
    ),
    # REQ-84。今のパターンすべての後ろに置く(今のパターンに当たる行を取らない)
    Pattern(
        "lines.access_class", "line", r"(?:ipv6 )?access-class \S+(?: vrf-also)? (?:in|out)(?: vrf-also)?",
        "lines[キー].access_class", "access-class ACL-MGMT in", _access_class,
        note=(
            "行の原文を集合に足す(同じ文字列は重ねず、文字列の昇順)。子を持つ行、重複したブロックの中の行、"
            "語のどれかが password・secret・key の行と語の中に鍵が埋まった行は未解釈。書式に合わない access-class の行も未解釈"
        ),
    ),
    Pattern(
        "lines.option", "line", rf"(?!(?:no )?(?:{'|'.join(_OWNED)})(?: |$)).+", "lines[キー].options",
        "transport preferred ssh", _option,
        note=(
            "手続き。ほかの書式に当たらない子を持たない行の原文を集合に足す(同じ文字列は重ねず、文字列の昇順)。"
            "子を持つ行は文字列にしないが解釈済みにし、中の子を持たない行を、その行からの道すじを ` > ` でつないだ文字列にして足す"
            "(中の行には書式を当てない。途中の行は解釈済みにだけする)。"
            "今の項目と同じ設定を書く行(no 付きも含め " + "・".join(_OWNED) + " で始まる行)は入れず未解釈。"
            "重複したブロックの中の行と、道すじのどの段かの語が password・secret・key の行(語の中に鍵が埋まった形も)は、"
            "その行も子孫も未解釈"
        ),
    ),
)


def _read_options(node: HConfigChild, prefix: list[str], line: Line, interpreted: InterpretedLines) -> None:
    """子を持つ行の中を出現順に読む。秘密の語がある行は、その行も子孫も読まない(未解釈に残す)。"""
    for child in node.children:
        path = [*prefix, child.text]
        if has_secret(*path):
            continue
        interpreted.mark(child, "lines.option")
        if child.children:
            _read_options(child, path, line, interpreted)
        else:
            _add(line["options"], _SEPARATOR.join(path))


def extract_lines(tree: HConfig, interpreted: InterpretedLines) -> tuple[dict[str, Line], dict[str, dict[str, Any]]]:
    """line ブロックから、キーで引く Line の辞書と、vendor_specific に写す辞書を返す。

    vendor の辞書は、vendor_specific の行を1つ以上読んだ line のキーだけを持つ。
    """
    ctx = _Ctx({}, {})
    for block in tree.children:
        ctx.current = None
        ctx.duplicate = False
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is not None:
            interpreted.mark(block, heading.id)
        elif not ctx.duplicate:
            continue
        for line in block.children:
            ctx.has_children = bool(line.children)
            if (pattern := apply_first(PATTERNS, "line", line.text, ctx)) is not None:
                interpreted.mark(line, pattern.id)
                if pattern.id == "lines.option" and ctx.current is not None and line.children:
                    _read_options(line, [line.text], ctx.current, interpreted)
    return ctx.lines, ctx.vendor
