"""HPE Aruba AOS-Switch のポートの並びの展開と、ポートの VLAN の所属・trunk の集計(ADR-0228 の追記、REQ-334)。

AOS-Switch と見立てた config(aos_switch.py)の、`vlan <数>` の塊の `untagged <並び>`・`tagged <並び>`、`interface <ポート>` の塊の
`untagged vlan <数>`・`tagged vlan <並び>`、最上位の `trunk <並び> trk<数> <方式>` を集める。行の書式を IR に写す表(ADR-0011)
ではなく、表の手続きが使う部品なので、ここに置く。表を通さない正規表現は使えないので、文字列の操作だけで書く。

ポートの並び(`expand_ports`)は `,` で区切った項目の並びで、項目は次のどれか(大文字・小文字はそのまま)。
- `<数>`・`<数>-<数>`(`1-24`)
- `<英字><数>`・`<英字><数>-<英字><数>`(`A1-A4`・`Trk1-Trk2`。英字が同じとき)
- `<数>/<数>`・`<数>/<数>-<数>/<数>`(`1/1-1/24`。前の数が同じとき)、`<数>/<英字><数>` とその範囲
展開できない並び(None)は、範囲の終わりが始まりより小さい・英字や前の数が食い違う・範囲の端の数が 0 で始まる・空の項目がある・
ほかの形・展開した数の合計が 1 行で 1024 個(awplus.MAX_PORTS)を超える、のどれか。
"""

import string
from dataclasses import dataclass, field

from driftpilot.config_parser.awplus import MAX_PORTS
from driftpilot.config_parser.tracking import FieldConflict

# 並び順の鍵: (最上位の塊が上から何番目か, 塊の中で何番目の子か)。後の行の値を使うかの判定に使う
Order = tuple[int, int]
# trunk の束ね先: (id, 方式)
Group = tuple[int, str]

MIN_VLAN = 1
MAX_VLAN = 4094

_Endpoint = tuple[str | None, str, str]


def _is_number(text: str) -> bool:
    return text.isascii() and text.isdigit()


def _parse_endpoint(text: str) -> _Endpoint | None:
    """`[<数>/][<英字>]<数>` を (前の数 か None, 英字, 数) に分ける。ほかの形は None。"""
    major: str | None = None
    rest = text
    if "/" in text:
        head, _, rest = text.partition("/")
        if not _is_number(head):
            return None
        major = head
    end = 0
    while end < len(rest) and rest[end] in string.ascii_letters:
        end += 1
    letters, digits = rest[:end], rest[end:]
    if not _is_number(digits):
        return None
    return major, letters, digits


def _expand_item(item: str, room: int) -> tuple[str, ...] | None:
    """項目 1 つを名前の並びにする。展開できない項目と、room(残りの数)を超える範囲は None。"""
    if "-" not in item:
        return (item,) if room >= 1 and _parse_endpoint(item) is not None else None
    parts = item.split("-")
    if len(parts) != 2:
        return None
    first, last = _parse_endpoint(parts[0]), _parse_endpoint(parts[1])
    if first is None or last is None or first[0] != last[0] or first[1] != last[1]:
        return None
    if first[2].startswith("0") or last[2].startswith("0"):
        return None
    start, stop = int(first[2]), int(last[2])
    if start > stop or stop - start + 1 > room:
        return None
    prefix = ("" if first[0] is None else f"{first[0]}/") + first[1]
    return tuple(f"{prefix}{number}" for number in range(start, stop + 1))


def expand_ports(text: str) -> tuple[str, ...] | None:
    """ポートの並びを 1 ポートずつの名前にする。出てきた順で、同じ名前は 1 つにする。展開できなければ None。

    範囲でない項目は書いてあるとおりの文字列を名前にし、範囲から作る名前は 0 を前に付けない 10 進の数にする。
    """
    names: dict[str, None] = {}
    total = 0
    for item in text.split(","):
        expanded = _expand_item(item, MAX_PORTS - total)
        if expanded is None:
            return None
        total += len(expanded)
        names.update(dict.fromkeys(expanded))
    return tuple(names)


def parse_vlan_number(text: str) -> int | None:
    """1〜4094 の 1 つの数。そうでなければ None。"""
    if not _is_number(text):
        return None
    number = int(text)
    return number if MIN_VLAN <= number <= MAX_VLAN else None


def expand_vlans(text: str) -> tuple[int, ...] | None:
    """`tagged vlan` の並び(`<数>`・`<数>-<数>` を `,` でつないだもの。1〜4094 だけ)を、昇順で重なりの無い数の並びにする。

    範囲の終わりが始まりより小さいとき、範囲の外の数があるとき、ほかの形のときは None。
    """
    numbers: set[int] = set()
    for item in text.split(","):
        parts = item.split("-")
        if len(parts) > 2:
            return None
        bounds = [parse_vlan_number(part) for part in parts]
        if any(bound is None for bound in bounds):
            return None
        first, last = bounds[0], bounds[-1]
        if first is None or last is None or first > last:
            return None
        numbers.update(range(first, last + 1))
    return tuple(sorted(numbers))


@dataclass
class PortMembership:
    """1 つのポートの VLAN の所属と trunk。resolve の結果。"""

    untagged: int | None = None
    tagged: set[int] = field(default_factory=set)
    group: Group | None = None
    # untagged・tagged の行があったか(所属の無いポートの switchport は今のまま)
    member: bool = False


@dataclass
class AosResolved:
    """resolve の結果。ports は出てきた順のポートごとの所属、conflicts は食い違いの記録(値・行番号は持たない)。"""

    ports: dict[str, PortMembership]
    conflicts: list[FieldConflict]


class AosPorts:
    """行ごとの所属の記録を集める入れ物。resolve で、鍵の順に(後の行を勝たせて)ポートごとにまとめる。"""

    def __init__(self) -> None:
        # (鍵, ポートの並び, 種類, 値)。種類は untagged(値は VLAN)・tagged(値は VLAN の並び)・trunk(値は Group)
        self._rows: list[tuple[Order, tuple[str, ...], str, object]] = []

    def add_untagged(self, order: Order, ports: tuple[str, ...], vlan: int) -> None:
        self._rows.append((order, ports, "untagged", vlan))

    def add_tagged(self, order: Order, ports: tuple[str, ...], vlans: tuple[int, ...]) -> None:
        self._rows.append((order, ports, "tagged", vlans))

    def add_trunk(self, order: Order, ports: tuple[str, ...], group: Group) -> None:
        self._rows.append((order, ports, "trunk", group))

    def resolve(self) -> AosResolved:
        """鍵の順に並べ、ポートごとに untagged の VLAN・tagged の集合・束ね先をまとめる。

        同じポートに違う untagged の VLAN、または違う束ね先が 2 行以上あれば、後の行の値を使って食い違いを記録する
        (count はそのポートにその項目の値を書いた行の数)。同じ値が重なっているだけなら記録しない。
        """
        ports: dict[str, PortMembership] = {}
        untagged_seen: dict[str, list[int]] = {}
        group_seen: dict[str, list[Group]] = {}
        for _, names, kind, value in sorted(self._rows, key=lambda row: row[0]):
            for name in names:
                state = ports.setdefault(name, PortMembership())
                if kind == "untagged":
                    assert isinstance(value, int)
                    state.untagged = value
                    state.member = True
                    untagged_seen.setdefault(name, []).append(value)
                elif kind == "tagged":
                    assert isinstance(value, tuple)
                    state.tagged.update(value)
                    state.member = True
                else:
                    assert isinstance(value, tuple)
                    state.group = (value[0], value[1])
                    group_seen.setdefault(name, []).append((value[0], value[1]))
        conflicts: list[FieldConflict] = []
        for name in ports:
            for item, seen in (("untagged_vlan", untagged_seen.get(name, [])), ("channel_group", group_seen.get(name, []))):
                if len(set(seen)) >= 2:
                    conflicts.append(FieldConflict((f"interface {name}",), item, len(seen)))
        return AosResolved(ports, conflicts)
