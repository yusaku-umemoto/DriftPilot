"""HPE Aruba AOS-Switch(旧 ProCurve)の見立てと、木にする前の前処理(ADR-0228 決定 1・2、REQ-332)。

AOS-Switch の config は Cisco 系(A型)の読み方で読む。機種の選択肢は足さず、前処理の前の本文に次のどちらかがあれば
AOS-Switch と見立てる。
- 空行を除いた最初の 5 行のうちに、`; ` で始まり ` Configuration Editor; Created on release #` を含む行(型番の行)がある
- 字下げの無い `vlan <数>` の塊の中(字下げのある行)に、`untagged <ポートの並び>` か `tagged <ポートの並び>` の行と、
  `exit` だけの行の両方がある
見立ては、ここの前処理と、VLAN の名前の引用符・SVI のアドレス・インターフェースの名前などの読み方(vlans.py・interfaces.py)
にだけ使う。結果(IR・差分の JSON)に見立ての欄は無い。XR・ASA と見立てた config は、AOS-Switch と見立てない(呼ぶ側で外す)。

AOS-Switch と見立てたときだけ、木にする前に次を順に行う(行ごとにもとの行番号を持ち回る。asa.py・iosxr.py と同じ)。
1. 先頭のほう(空行を除いて、最初の設定の行より前)の `;` で始まる行と `Running configuration:` だけの行を消す
2. 字下げの無い `banner <語> "<本文>"` を、IOS の `^C` で囲む形(`banner <語> ^C`・本文の行・`^C`)にする。
   引用符がその行で閉じるものは 1 行のバナーにし(後ろの設定の行を飲み込まない)、閉じないものは閉じの引用符のある
   行までを 1 つのバナーにする。ファイルの終わりまで閉じない・閉じの引用符の後ろに字がある・本文のどれかが `!`・`%`・`EOF` と
   同じか `^` で始まるか `^C` を含むものは、変えない(今のバナーの扱いのまま)
3. バナーの外の、前後の空白を除くと `exit` だけの行を、字下げによらず消す(塊は字下げで決まる。最上位に重なる `exit` で止まらない)

行の書式を IR に写す表(ADR-0011)ではなく、木にする前の前処理の規則なので、この部品に置く(tree.py の終わりの印の規則・
asa.py・awplus.py・iosxr.py と同じ扱い)。最上位の塊はまとめない。
"""

import re
from dataclasses import dataclass

from driftpilot.config_parser.tree import banner_line_kinds

_CONFIG_EDITOR = " Configuration Editor; Created on release #"
_HEADER_LINES = 5
_VLAN_HEADING = re.compile(r"vlan \d+")
# ポートの並び: 項目は数・英字＋数(`A1`・`Trk1`)・`<数>/<数>`。`-`・`,` でつなぐ
_PORT_ITEM = r"(?:\d+/)?[A-Za-z]*\d+"
_PORT_LIST = rf"{_PORT_ITEM}(?:[-,]{_PORT_ITEM})*"
_MEMBER_LINE = re.compile(rf"(?:untagged|tagged) {_PORT_LIST}")
_BANNER = re.compile(r'banner[ \t]+(?P<kind>\S+)[ \t]+"(?P<rest>.*)')
_IOS_BANNER_ENDS = frozenset({"!", "%", "EOF"})
_RUNNING = "Running configuration:"

Numbered = tuple[int, str]


@dataclass(frozen=True)
class AosSwitchPrep:
    """前処理の結果。text は木にする文字列、aos_switch は AOS-Switch と見立てたか、line_numbers は text の各行のもとの行番号。"""

    text: str
    aos_switch: bool
    line_numbers: tuple[int, ...] = ()


def _normalize(line: str) -> str:
    return " ".join(line.split())


def _has_model_line(lines: list[str]) -> bool:
    """空行を除いた最初の 5 行のうちに、型番の `;` の行があるか。"""
    head = [line for line in lines if line.strip()][:_HEADER_LINES]
    return any(line.startswith("; ") and _CONFIG_EDITOR in line for line in head)


def _has_vlan_member_block(lines: list[str]) -> bool:
    """字下げの無い `vlan <数>` の塊の中に、`untagged|tagged <ポートの並び>` の行と `exit` だけの行の両方があるか。"""
    in_vlan = False
    member = False
    leave = False
    for line in lines:
        if not line.strip():
            continue
        if not line[0].isspace():
            in_vlan = _VLAN_HEADING.fullmatch(_normalize(line)) is not None
            member = leave = False
            continue
        if not in_vlan:
            continue
        words = _normalize(line)
        if words == "exit":
            leave = True
        elif _MEMBER_LINE.fullmatch(words):
            member = True
        if member and leave:
            return True
    return False


def looks_like_aos_switch(text: str) -> bool:
    """前処理の前の本文が AOS-Switch と見立てられるか。"""
    lines = text.splitlines()
    return _has_model_line(lines) or _has_vlan_member_block(lines)


def _drop_header(numbered: list[Numbered]) -> list[Numbered]:
    """先頭のほうの `;` の行と `Running configuration:` だけの行を消す。最初の設定の行から後ろは変えない。"""
    out: list[Numbered] = []
    in_header = True
    for item in numbered:
        line = item[1]
        if in_header:
            if line.startswith(";") or line.strip() == _RUNNING:
                continue
            if line.strip():
                in_header = False
        out.append(item)
    return out


def _is_ios_form(body: list[str]) -> bool:
    """本文のどれかが IOS の終わりの印と同じか、`^` で始まるか、`^C` を含むか(`^C` で囲むと終わりが食い違う)。"""
    return any(b.strip() in _IOS_BANNER_ENDS or b.lstrip().startswith("^") or "^C" in b for b in body)


def _join_banners(numbered: list[Numbered]) -> list[Numbered]:
    """字下げの無い `banner <語> "…` を `^C` で囲む形にまとめる。まとめられないものは変えない。"""
    out: list[Numbered] = []
    index = 0
    while index < len(numbered):
        number, line = numbered[index]
        m = _BANNER.fullmatch(line.rstrip()) if line and not line[0].isspace() else None
        if m is None:
            out.append(numbered[index])
            index += 1
            continue
        rest = m["rest"]
        end = index
        close = rest.find('"')
        if close >= 0:
            body = [rest[:close]]
            tail = rest[close + 1 :]
        else:
            body = [rest]
            tail = None
            for end in range(index + 1, len(numbered)):
                text = numbered[end][1]
                close = text.find('"')
                if close >= 0:
                    body.extend(numbered[k][1] for k in range(index + 1, end))
                    body.append(text[:close])
                    tail = text[close + 1 :]
                    break
        if tail is None or tail.strip() or _is_ios_form(body):
            out.append(numbered[index])
            index += 1
            continue
        out.extend((number, text) for text in (f"banner {m['kind']} ^C", *body, "^C"))
        index = end + 1
    return out


def _drop_exit_lines(numbered: list[Numbered]) -> list[Numbered]:
    """バナーの外の `exit` だけの行を、字下げによらず消す。"""
    lines = [line for _, line in numbered]
    kinds = banner_line_kinds(lines)
    return [item for item, kind in zip(numbered, kinds, strict=True) if kind or item[1].strip() != "exit"]


def prepare_aos_switch(text: str) -> AosSwitchPrep:
    """AOS-Switch と見立てたときだけ前処理を当てる。何も変えなければ、もとの文字列をそのまま返す。"""
    if not looks_like_aos_switch(text):
        return AosSwitchPrep(text, False)
    lines = text.splitlines()
    numbered: list[Numbered] = list(enumerate(lines, start=1))
    numbered = _drop_header(numbered)
    numbered = _join_banners(numbered)
    numbered = _drop_exit_lines(numbered)
    result_lines = [line for _, line in numbered]
    numbers = tuple(number for number, _ in numbered)
    if result_lines == lines:
        return AosSwitchPrep(text, True, numbers)
    result = "\n".join(result_lines)
    return AosSwitchPrep(result + "\n" if text.endswith("\n") else result, True, numbers)
