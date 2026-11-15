"""アライドテレシス AW+ の見立てと、ポートの範囲の分割(ADR-0206 決定 1・5、REQ-289)。

AW+ の config は Cisco 系(A型)の読み方で読む。機種の選択肢は足さず、config の本文に次のどれかがあれば AW+ と見立てる。
- 字下げの無い `vlan database` の行
- 字下げの無い `interface port<数>.<数>.<数>` で始まる行(範囲・並びを含む)
見立ては、トランクの `add`・`remove` の読み方(interfaces.py)と、ここのポートの範囲の分割にだけ使う。
結果(IR・差分の JSON)に見立ての欄は無い。

ポートの範囲(`interface port1.0.1-1.0.12`)は、木にする前に1ポートずつの塊に分ける。分けたことは結果に残さない。
範囲の名前を読む規則は、行の書式を IR に写す表(ADR-0011)ではなく、木にする前の前処理の規則なので、この部品に置く
(tree.py の終わりの印の規則と同じ扱い)。
"""

import re

from driftpilot.config_parser.tree import banner_line_kinds, is_dropped_line

# 範囲・並びの名前に含められるポートの数の上限(範囲の大きさの合計。重なりを除く前の値)
MAX_PORTS = 1024

_PORT = r"port(?P<a>\d+)\.(?P<b>\d+)\.(?P<c>\d+)"
_AWPLUS_INTERFACE = re.compile(r"interface port\d+\.\d+\.\d+")
# 項目の形: portA.B.C / portA.B.C-portA.B.D / portA.B.C-A.B.D / portA.B.C-D
# 末尾に port を付けるときは A.B.D の全部が要る(port1.0.1-port3 は読めない名前)
_ITEM = re.compile(rf"{_PORT}(?:-(?:(?:port)?(?P<a2>\d+)\.(?P<b2>\d+)\.(?P<d>\d+)|(?P<d2>\d+)))?")
_INTERFACE_LINE = re.compile(r"interface (?P<name>.+)")

Port = tuple[int, int, int]


def _top_level_lines(text: str) -> list[str]:
    """バナーの外の、行頭に空白の無い行を、空白を1つにまとめて返す。"""
    lines = text.splitlines()
    kinds = banner_line_kinds(lines)
    return [
        " ".join(line.split())
        for line, kind in zip(lines, kinds, strict=True)
        if not kind and line and not line[0].isspace()
    ]


def looks_like_awplus(text: str) -> bool:
    """config の本文が AW+ と見立てられるか。"""
    for line in _top_level_lines(text):
        if line == "vlan database" or _AWPLUS_INTERFACE.match(line):
            return True
    return False


def _read_ports(name: str) -> list[Port] | None:
    """名前を1ポートずつの (A, B, C) に分ける。分けない名前なら None。昇順で、重なりは1つにする。"""
    ports: set[Port] = set()
    total = 0
    for raw in name.split(","):
        item = _ITEM.fullmatch(raw.strip())
        if item is None:
            return None
        a, b, first = int(item["a"]), int(item["b"]), int(item["c"])
        if item["d2"] is not None:
            last = int(item["d2"])
        elif item["d"] is not None:
            last = int(item["d"])
            if (int(item["a2"]), int(item["b2"])) != (a, b):
                return None
        else:
            last = first
        if first > last:
            return None
        total += last - first + 1
        if total > MAX_PORTS:
            return None
        ports.update((a, b, c) for c in range(first, last + 1))
    return sorted(ports)


def _heading_name(line: str) -> str | None:
    """字下げの無い `interface <名前>` の行の名前。そうでなければ None。"""
    if not line or line[0].isspace():
        return None
    m = _INTERFACE_LINE.fullmatch(" ".join(line.split()))
    return m["name"] if m else None


def split_port_ranges(text: str) -> str:
    """字下げの無い `interface <名前>` の行の名前が `port` の範囲・並びなら、1ポートずつの塊に分けて返す。

    分けた塊は元の塊の子の行(字下げのまま)を持ち、元の塊の位置にポートの番号の順で置く。
    分けた後で同じポートの塊が2つ以上あれば、最初の塊の位置に1つにまとめ、子の行は出てきた順に並べる。
    まとめるのは、分けた塊を1つ以上含むときだけ(何も分けていない config の同じ見出しの重なりは、今のとおり)。
    何も分けなければ元の文字列をそのまま返す。
    """
    lines = text.splitlines()
    # 最上位の塊に分ける。塊の子は、後に続く行頭が空白の行・空の行・木にする前に消える字下げの無い行( のコメントなど。
    # 木ではこれらの前後の行が同じ塊に属するので、所属を変えない)。バナーは本文を含めて1つの塊にして、見出しとして扱わない
    blocks: list[tuple[str, list[str]]] = []
    for line, kind in zip(lines, banner_line_kinds(lines), strict=True):
        continues = kind == 2 or (kind == 0 and (not line or line[0].isspace() or is_dropped_line(line)))
        if blocks and continues:
            blocks[-1][1].append(line)
        else:
            blocks.append((line, []))

    out: list[tuple[str, list[str]]] = []
    split_any = False
    for heading, children in blocks:
        name = _heading_name(heading)
        ports = _read_ports(name) if name is not None and ("-" in name or "," in name) else None
        if ports is None:
            out.append((heading, children))
            continue
        split_any = True
        out.extend((f"interface port{a}.{b}.{c}", list(children)) for a, b, c in ports)
    if not split_any:
        return text

    merged: list[tuple[str, list[str]]] = []
    position: dict[str, int] = {}
    for heading, children in out:
        key = " ".join(heading.split())
        if _heading_name(heading) is not None and _AWPLUS_INTERFACE.fullmatch(key) and key in position:
            merged[position[key]][1].extend(children)
            continue
        if _AWPLUS_INTERFACE.fullmatch(key):
            position[key] = len(merged)
        merged.append((heading, children))

    result = "\n".join(line for heading, children in merged for line in (heading, *children))
    return result + "\n" if text.endswith("\n") else result
