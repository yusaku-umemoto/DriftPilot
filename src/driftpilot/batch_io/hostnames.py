"""組の候補の手がかりにする config の hostname(REQ-212 §7 / ADR-0153 決定7)。

読むのは 2 つの形だけ。推測しない(ほかの形は読まない。大文字小文字などをそろえない):
- 行頭の `hostname <1語>`(字下げなし。語は 1 つだけ。Cisco 系・Arista の形)
- 字下げなしの `config system global` から次の字下げなしの `end` までの、直下の `set hostname <1語>`
  (FortiGate の形。前後の `"` を外す。値が空のものと ENC で始まるものは読まない)

ファイルの中に違う値が 2 つ以上あるとき、どちらの形も無いとき、読めないときは None。
★ Si-R の hostname の行は読まない(形が確かめられていない。ADR-0153 決定7)。
値はログに出さない(このモジュールはログを書かない)。組を作るのは呼ぶ側で、人が確定したときだけ。
"""

import os
import re

from driftpilot.segmentation.input_forms import InputDecodeError, read_config_input

_HOSTNAME_LINE = re.compile(r"hostname[ \t]+(\S+)[ \t]*")
_SET_HOSTNAME = re.compile(r"[ \t]+set[ \t]+hostname[ \t]+(\S+)[ \t]*")
_GLOBAL = "config system global"


def _indented(line: str) -> bool:
    return line[:1] in (" ", "\t")


def _unquote(value: str) -> str | None:
    if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
        value = value[1:-1]
    elif '"' in value:
        return None  # 引用符が片方だけ(語の途中で切れた値など)は読まない
    if not value or value.startswith("ENC"):
        return None
    return value


def hostnames_in(text: str) -> set[str]:
    """本文の中の hostname の値(2 つの形だけ)。"""
    found: set[str] = set()
    in_global = False
    depth = 0  # config system global の中の、字下げした config の入れ子の深さ
    for raw in text.splitlines():
        line = raw.rstrip()
        if not _indented(line):
            if in_global and line == "end":
                in_global = False
                continue
            if line == _GLOBAL:
                in_global, depth = True, 0
                continue
            match = _HOSTNAME_LINE.fullmatch(line)
            if match and not in_global:
                found.add(match.group(1))
            continue
        if not in_global:
            continue
        stripped = line.strip()
        if stripped.startswith("config "):
            depth += 1
        elif stripped == "end" and depth > 0:
            depth -= 1
        elif depth == 0 and (match := _SET_HOSTNAME.fullmatch(line)):
            value = _unquote(match.group(1))
            if value is not None:
                found.add(value)
    return found


def read_hostname(path: str | os.PathLike[str]) -> str | None:
    """ファイルの hostname。読み方は比較と同じ入口(read_config_input)。1 つに決まらないとき・読めないときは None。"""
    try:
        text = read_config_input(path).text
    except (InputDecodeError, OSError):
        return None
    found = hostnames_in(text)
    return found.pop() if len(found) == 1 else None
