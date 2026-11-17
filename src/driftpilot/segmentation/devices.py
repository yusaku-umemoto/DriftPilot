"""1 つのログに入っている複数の機器を見つけ、機器ごとの文に分ける(ADR-0194 決定1〜3。REQ-267 §2)。

機器は、プロンプトの hostname で見分ける。プロンプトの候補の見つけ方は `splitter.detect_prompt` と同じ
(`CANDIDATE`・`COMMAND_SHAPED`・コマンドの形が続く行が 1 回以上・出るのが 2 回以上・
ホスト名の頭が文字か数字であること。`*>` などの表の印で始まる候補は数えない)。
hostname は `_host` の結果から、末尾に空白なしで付いたかっこ書き 1 つを外した名前(`edge-a(config-if)#` は `edge-a`)。
FortiGate の形(`fw-01 #`・`fw-01 (root) #`)は `FORTIGATE_PROMPT` の host でまとめる。
アラクサラ AX の形(`AX_PROMPT`。`> `・`# `・`!OFFICE1# `・20文字までのホスト名)も同じ基準で見分け、host の部分でまとめる
(`!` と設定モードは名前に含めない。裸のプロンプトは名前が空の 1 台。ADR-0207)。

Cisco IOS-XR の形(`XR_PROMPT`。`RP/0/RSP0/CPU0:pe1#`・`RP/0/RP0/CPU0:pe1(config-if)#`)も同じ基準で見分け、host の部分でまとめる
(場所と設定モードは名前に含めない。場所だけが違う形は 1 台。ADR-0211 決定7)。XR の形の行は XR の規則だけで読み、
一般の候補・FortiGate の形・AX の形では数えない(ホスト名の短い `RP/0/RSP0/CPU0:R1#` を二重に数えないため)。

括弧の形(`BRACKET_PROMPT`。ヤマハの `[RTX1210] > `・`[RTX1210] # `、Check Point Gaia の expert の `[Expert@gw-lab1:0]# `)も同じ基準で見分け、
括弧の中をホスト名にしてまとめる(`>` と `#` が混ざっても同じ名前の 1 台。ADR-0213 決定8)。FortiGate の形は、`[` で始まるホスト名を数えない。

Cisco ASA の形(`ASA_PROMPT`。`fw-01/pri/act# `・`fw-01/sec/stby# `・`fw-01/ctx1/pri/act(config-if)# `)も同じ基準で見分ける。
機器の名前は行の頭から状態の語の終わりまで(`fw-01/pri/act`。設定モード・終端の記号・空白は含まない。一般の候補で決まった短い ASA のプロンプトの名前と同じ決まり)。
優先度か状態が違う形(`fw-01/pri/act` と `fw-01/sec/stby`)は別の機器(フェイルオーバーの 2 台。ADR-0214 決定7)。

今の区間の切り分け(`detect_prompt`・`split_log`)は変えない。ここは比較の窓の知らせと書き出しのためだけの部品。
行の中身は変えない(見分けるときだけ、ページ送りの印と制御文字を外した行を見る)。
"""

import collections
import re
from dataclasses import dataclass

from driftpilot.segmentation.splitter import (
    ASA_PROMPT,
    AX_PROMPT,
    BRACKET_PROMPT,
    CANDIDATE,
    COMMAND_SHAPED,
    CONTROL,
    PAGER,
    _asa_command,
    _erase_pager,
    _fortigate_match,
    _host,
    _host_head_ok,
    _strip_dollar,
    _within_limit,
    _xr_command,
    _xr_match,
)

# hostname の末尾に空白なしで付いたかっこ書き 1 つ(Cisco 系の設定モードのプロンプト: edge-a(config-if))
_MODE_SUFFIX = re.compile(r"(?<=\S)\([^()]*\)$")  # 直前が空白のかっこ書きは外さない
# ファイル名に使えない文字(Windows)と制御文字
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')
_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{n}" for n in range(1, 10)), *(f"LPT{n}" for n in range(1, 10))})
LOG_SUFFIX = ".log"


@dataclass(frozen=True)
class DeviceText:
    """1 台分の文。text は、その機器の行を出た順につないだもの(行の中身と改行はそのまま)。"""

    hostname: str
    text: str


def device_key(prefix: str) -> str:
    """一般の形のプロンプトの文字列から、機器の名前を求める。名前が空なら空の文字列。"""
    host = _host(prefix)
    stripped = _MODE_SUFFIX.sub("", host)
    return stripped or host


def _lines(text: str) -> list[str]:
    """改行を残したまま行に分ける(つなぎ直すともとの文になる)。"""
    parts = text.split("\n")
    lines = [part + "\n" for part in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def _plain(line: str) -> str:
    """見分けるための行: 行末の改行を外し、ページ送りの印と制御文字を消したもの。書き出す行には使わない。"""
    body = line.rstrip("\n")
    body = PAGER.sub(_erase_pager, body)
    return CONTROL.sub("", body)


def _general_hosts(plains: list[str]) -> list[str]:
    """一般の形のプロンプトで機器と決まった名前(ログに最初に出た順)。"""
    freq: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in plains:
        if _xr_match(line):
            continue  # XR の形の行は XR の規則だけで数える
        m = CANDIDATE.match(line)
        if not m or not _within_limit(m.group("prefix")):
            continue
        key = device_key(m.group("prefix"))
        if not key or not _host_head_ok(key):
            continue
        freq[key] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[key] += 1
    return [key for key in freq if score[key] >= 1 and freq[key] >= 2]


def _fortigate_hosts(plains: list[str]) -> list[str]:
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in plains:
        if _xr_match(line):
            continue
        m = _fortigate_match(line)
        if not m:
            continue
        host = m.group("host")
        total[host] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[host] += 1
    return [host for host in total if score[host] >= 1 and total[host] >= 2]


def _bracket_hosts(plains: list[str]) -> list[str]:
    """括弧の形のプロンプトで機器と決まった名前(括弧の中。ログに最初に出た順)。`>` と `#` の違いは名前に含めない。"""
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in plains:
        if _xr_match(line):
            continue
        m = BRACKET_PROMPT.match(line)
        if not m:
            continue
        host = m.group("host")
        total[host] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[host] += 1
    return [host for host in total if score[host] >= 1 and total[host] >= 2]


def _asa_name(m: re.Match) -> str:
    """ASA の形の行の機器の名前: 行の頭から状態の語の終わりまで(`fw-01/pri/act`)。設定モード・終端の記号・空白は含まない。"""
    return m.group(0)[: m.end("state")]


def _asa_hosts(plains: list[str]) -> list[str]:
    """ASA の形のプロンプトで機器と決まった名前(ログに最初に出た順)。優先度か状態が違う形は別の名前。"""
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in plains:
        if _xr_match(line):
            continue
        m = ASA_PROMPT.match(line)
        if not m:
            continue
        name = _asa_name(m)
        total[name] += 1
        if COMMAND_SHAPED.match(_asa_command(m)):
            score[name] += 1
    return [name for name in total if score[name] >= 1 and total[name] >= 2]


def _ax_hosts(plains: list[str]) -> list[str]:
    """AX の形のプロンプトで機器と決まった名前(ログに最初に出た順)。裸のプロンプトの名前は空の文字列。"""
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in plains:
        if _xr_match(line):
            continue
        m = AX_PROMPT.match(line)
        if not m:
            continue
        host = m.group("host")
        if not _host_head_ok(host):
            continue
        total[host] += 1
        if COMMAND_SHAPED.match(_strip_dollar(m.group("rest"))):
            score[host] += 1
    return [host for host in total if score[host] >= 1 and total[host] >= 2]


def _xr_hosts(plains: list[str]) -> list[str]:
    """XR の形のプロンプトで機器と決まった名前(ログに最初に出た順)。場所と設定モードは名前に含めない。"""
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in plains:
        m = _xr_match(line)
        if not m:
            continue
        host = m.group("host")
        total[host] += 1
        if COMMAND_SHAPED.match(_xr_command(m)):
            score[host] += 1
    return [host for host in total if score[host] >= 1 and total[host] >= 2]


def _line_host(plain: str, known: frozenset[str]) -> str | None:
    """この行が、機器と決まった名前のプロンプトの行ならその名前。そうでなければ None。"""
    m = _xr_match(plain)
    if m:
        # XR の形の行は XR の規則だけで読む(ほかの規則へは進まない)
        return m.group("host") if m.group("host") in known else None
    m = CANDIDATE.match(plain)
    if m and _within_limit(m.group("prefix")):
        key = device_key(m.group("prefix"))
        if _host_head_ok(key) and key in known:
            return key
    m = _fortigate_match(plain)
    if m and m.group("host") in known:
        return m.group("host")
    m = BRACKET_PROMPT.match(plain)
    if m and m.group("host") in known:
        return m.group("host")
    m = ASA_PROMPT.match(plain)
    if m and _asa_name(m) in known:
        return _asa_name(m)
    m = AX_PROMPT.match(plain)
    if m and _host_head_ok(m.group("host")) and m.group("host") in known:
        return m.group("host")
    return None


def _hosts_in_order(lines: list[str]) -> tuple[list[str], list[str]]:
    plains = [_plain(line) for line in lines]
    found = set(_general_hosts(plains)) | set(_fortigate_hosts(plains)) | set(_xr_hosts(plains)) | set(_bracket_hosts(plains)) | set(_asa_hosts(plains)) | set(_ax_hosts(plains))
    known = frozenset(found)
    order: list[str] = []
    for plain in plains:
        host = _line_host(plain, known)
        if host is not None and host not in order:
            order.append(host)
    return order, plains


def find_devices(text: str) -> tuple[str, ...]:
    """機器の名前を、ログに最初に出た順に返す。見つからなければ空。"""
    order, _plains = _hosts_in_order(_lines(text))
    return tuple(order)


def split_by_device(text: str) -> tuple[DeviceText, ...]:
    """機器ごとの文に分ける。1 台なら全文を 1 つ、0 台なら空を返す。

    行は、その行より前で最後に出た機器のプロンプトの行の機器に入れる。最初のプロンプトより前の行は最初の機器に入れる。
    同じ機器が何度も出てくれば、出た順に 1 つの文につなぐ。
    """
    lines = _lines(text)
    order, plains = _hosts_in_order(lines)
    if not order:
        return ()
    if len(order) == 1:
        return (DeviceText(order[0], text),)
    known = frozenset(order)
    parts: dict[str, list[str]] = {host: [] for host in order}
    current = order[0]
    for line, plain in zip(lines, plains):
        host = _line_host(plain, known)
        if host is not None:
            current = host
        parts[current].append(line)
    return tuple(DeviceText(host, "".join(parts[host])) for host in order)


def is_multi_device(text: str) -> bool:
    """機器が 2 つ以上のログか。"""
    return len(find_devices(text)) >= 2


def _safe_stem(hostname: str) -> str:
    stem = _UNSAFE.sub("_", hostname).rstrip(". \t")
    if not stem:
        stem = "_"
    if stem.split(".", 1)[0].upper() in _RESERVED:
        stem = "_" + stem
    return stem


def device_filenames(*sides: tuple[str, ...] | list[str]) -> dict[str, str]:
    """hostname → ファイル名(`<hostname>.log`)の表。名前は渡した順(変更前、変更後)に、最初に出た順で並べて決める。

    同じ hostname は、どの側でも同じ名前になる。使えない文字は `_`、末尾の `.`・空白は除く、予約の名前は頭に `_`。
    大文字・小文字を区別せずに同じ名前になる違う hostname は、2 つ目から `-2`・`-3` を付ける。
    """
    names: dict[str, str] = {}
    taken: set[str] = set()
    for side in sides:
        for hostname in side:
            if hostname in names:
                continue
            stem = _safe_stem(hostname)
            candidate = stem + LOG_SUFFIX
            n = 1
            while candidate.casefold() in taken:
                n += 1
                candidate = f"{stem}-{n}{LOG_SUFFIX}"
            taken.add(candidate.casefold())
            names[hostname] = candidate
    return names
