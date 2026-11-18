"""Huawei VRP の見立てと、木にする前の前処理(ADR-0231 決定 1・2、REQ-336)。

Huawei VRP の config は Cisco 系(A型)の読み方で読む。機種の選択肢は足さず、前処理の前の本文に次のどれかがあれば
Huawei と見立てる。
- 字下げが 0 か 1 文字の `sysname <名前>` の行があり、かつ、次のどれかの行がある: 字下げなしの `#` だけの行(後ろの空白は可)・
  字下げなしの `vlan batch ` で始まる行・字下げなしの `interface <名前>` で名前が `Vlanif`・`Eth-Trunk`・`LoopBack`・`MEth`・
  `XGigabitEthernet`・`GigabitEthernet<数>/<数>/`・`10GE<数>`・`25GE<数>`・`40GE<数>`・`100GE<数>` で始まる行
- 最初の 2,000 文字のうちに、`[V<数 3 桁>R<数 3 桁>C<英数字 1 文字以上>]` だけの行(後ろの空白は可)がある
- 最初の 2,000 文字のうちに、`!Software Version V<数 3 桁>R<数 3 桁>` で始まる行がある
見立ては、ここの前処理と、ホスト名(`sysname`)・インターフェースの `undo shutdown` などの読み方(global_.py・interfaces.py)に
だけ使う。結果(IR・差分の JSON)に見立ての欄は無い。XR・ASA・AOS-Switch と見立てた config は、Huawei と見立てない(呼ぶ側で外す)。

Huawei と見立てたときだけ、木にする前に次を行う(行ごとにもとの行番号を持ち回る。aos_switch.py・asa.py と同じ)。
1. 字下げなしの `#` だけの行で config を塊に分ける(最初の `#` より前も 1 つの塊)。塊ごとに、空白だけでない行の字下げ
   (行の頭の半角空白の数)の最小を求め、その塊の全部の行の頭からその数の空白を外す(保存形は最上位の行に 1 文字の字下げが付くことがある)。
   塊を区切る字下げなしの `#` の行と `[V…]` の括弧の行は、字下げの最小を求める行に数えない(字下げ 0 の行に邪魔されて、
   字下げ付きの行だけの塊が直らないことが無いように)
2. 次の行は木に入れない: `#` だけの行(字下げによらず。CE の塊の中の ` #` を含む。字下げ付きの ` #` では塊を分けない)・
   `[V…]` の括弧の行・字下げを外した後の `return` だけの行

行の書式を IR に写す表(ADR-0011)ではなく、木にする前の前処理の規則なので、この部品に置く(aos_switch.py・asa.py と同じ扱い)。
最上位の塊はまとめない。
木にするときは、`banner ` で始まる行(`banner enable` など)をバナーの始まりとして扱わず、ふつうの行として入れる
(tree.py の build_tree の banners=False。ADR-0231 の追記、REQ-343)。前処理・見立ての決まりは変えない。

見立ては、VLAN(`vlan batch <並び>` と、`vlan <N>` の塊の `name` の後勝ち。vlans.py)と、ポートの L2
(`port link-type`・`port default vlan`・`port trunk allow-pass vlan`・`port trunk pvid vlan`・`eth-trunk`。interfaces.py)の
読み方にも使う(ADR-0231 決定 3(2)、REQ-337)。VLAN の並び(`10 20 to 22`)を読む関数 vlan_list をここに置く。
`port trunk allow-pass vlan all` は読まない(未解釈。REQ-338)。
さらに、スタティックルート(`ip route-static`・`ipv6 route-static`。static_routes.py)・OSPF(`ospf <番号>` の `area` の
`network`。ospf.py)・全体の設定(`ntp-service unicast-server`・`dns server`・`info-center loghost`・
`snmp-agent sys-info location|contact`。global_.py)の読み方にも使う(ADR-0231 決定 3(3)、REQ-338)。
"""

import re
from dataclasses import dataclass

_HEAD_CHARS = 2000
_SYSNAME = re.compile(r" ?sysname +\S")
_HASH_ONLY = re.compile(r"#\s*")
_VLAN_BATCH = "vlan batch "
_INTERFACE = re.compile(
    r"interface +(?:Vlanif|Eth-Trunk|LoopBack|MEth|XGigabitEthernet|GigabitEthernet\d+/\d+/|10GE\d|25GE\d|40GE\d|100GE\d)"
)
_VERSION_BRACKET = re.compile(r"\[V\d{3}R\d{3}C[0-9A-Za-z]+\]\s*")
_SOFTWARE_VERSION = re.compile(r"!Software Version V\d{3}R\d{3}")
_RETURN = re.compile(r"return\s*")

Numbered = tuple[int, str]


@dataclass(frozen=True)
class HuaweiPrep:
    """前処理の結果。text は木にする文字列、huawei は Huawei と見立てたか、line_numbers は text の各行のもとの行番号。"""

    text: str
    huawei: bool
    line_numbers: tuple[int, ...] = ()


def _has_sysname_and_clue(lines: list[str]) -> bool:
    sysname = any(_SYSNAME.match(line) for line in lines)
    if not sysname:
        return False
    for line in lines:
        if not line or line[0].isspace():
            continue
        if _HASH_ONLY.fullmatch(line) or line.startswith(_VLAN_BATCH) or _INTERFACE.match(line):
            return True
    return False


def looks_like_huawei(text: str) -> bool:
    """前処理の前の本文が Huawei VRP と見立てられるか。"""
    head = text[:_HEAD_CHARS].splitlines()
    if any(_VERSION_BRACKET.fullmatch(line) or _SOFTWARE_VERSION.match(line) for line in head):
        return True
    return _has_sysname_and_clue(text.splitlines())


def _indent(line: str) -> int:
    """行の頭の半角空白の数(タブは数えない)。"""
    return len(line) - len(line.lstrip(" "))


def _split_chunks(numbered: list[Numbered]) -> list[list[Numbered]]:
    """字下げなしの `#` だけの行で塊に分ける。`#` の行は直前の塊の終わりに入れる。"""
    chunks: list[list[Numbered]] = [[]]
    for item in numbered:
        chunks[-1].append(item)
        if _HASH_ONLY.fullmatch(item[1]):
            chunks.append([])
    return chunks


def _dedent(chunk: list[Numbered]) -> list[Numbered]:
    """塊の字下げの最小(塊を区切る字下げなしの `#` の行・括弧の行・空白だけの行を除く)を、全部の行の頭から外す。"""
    indents = [
        _indent(line)
        for _, line in chunk
        if line.strip() and not _HASH_ONLY.fullmatch(line) and not _VERSION_BRACKET.fullmatch(line.rstrip())
    ]
    if not indents or min(indents) == 0:
        return chunk
    least = min(indents)
    return [(number, line[min(least, _indent(line)) :]) for number, line in chunk]


def _is_dropped(line: str) -> bool:
    """木に入れない行か。`#` だけの行は字下げによらず、括弧の行と `return` だけの行は字下げを外した後(行頭から、後ろの空白は可)。"""
    text = line.rstrip()
    return line.strip() == "#" or _VERSION_BRACKET.fullmatch(text) is not None or _RETURN.fullmatch(text) is not None


def prepare_huawei(text: str) -> HuaweiPrep:
    """Huawei と見立てたときだけ前処理を当てる。何も変えなければ、もとの文字列をそのまま返す。"""
    if not looks_like_huawei(text):
        return HuaweiPrep(text, False)
    lines = text.splitlines()
    numbered: list[Numbered] = list(enumerate(lines, start=1))
    kept = [item for chunk in _split_chunks(numbered) for item in _dedent(chunk) if not _is_dropped(item[1])]
    result_lines = [line for _, line in kept]
    numbers = tuple(number for number, _ in kept)
    if result_lines == lines:
        return HuaweiPrep(text, True, numbers)
    result = "\n".join(result_lines)
    return HuaweiPrep(result + "\n" if text.endswith("\n") else result, True, numbers)


_MIN_VLAN = 1
_MAX_VLAN = 4094


def _vlan_number(word: str) -> int | None:
    """ASCII の数字だけの語を VLAN ID(1〜4094)にする。それ以外は None。"""
    if not (word.isascii() and word.isdigit()):
        return None
    number = int(word)
    return number if _MIN_VLAN <= number <= _MAX_VLAN else None


def vlan_list(text: str) -> tuple[int, ...] | None:
    """VRP の VLAN の並び(空白で区切った ID と `<A> to <B>`)を、重なりを除いた昇順の ID にする(REQ-337)。

    形に合わない語・範囲の外の ID(1〜4094 以外)・A > B・`to` で始まるか終わる並び・空の並びは None(呼ぶ側が行ごと未解釈にする)。
    """
    words = text.split()
    if not words:
        return None
    ids: set[int] = set()
    index = 0
    while index < len(words):
        first = _vlan_number(words[index])
        if first is None:
            return None
        if index + 1 < len(words) and words[index + 1] == "to":
            if index + 2 >= len(words):
                return None
            last = _vlan_number(words[index + 2])
            if last is None or last < first:
                return None
            ids.update(range(first, last + 1))
            index += 3
        else:
            ids.add(first)
            index += 1
    return tuple(sorted(ids))
