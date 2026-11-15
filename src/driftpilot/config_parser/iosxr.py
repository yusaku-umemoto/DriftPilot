"""Cisco IOS-XR の前処理: 閉じの語の対と、開き直した塊のまとめ(ADR-0211 決定 2〜4、REQ-296)。

XR の config は Cisco 系(A型)の読み方で読む。機種の選択肢は足さない。木にする前に次を行う。

1. 閉じの語の対(どの config でも): XR は `route-policy`・`…-set`・`group`・`policy-global`・`policy-map`・`class-map` の塊を、
   字下げの無い `end-policy`・`end-set`・`end-group`・`end-global`・`end-policy-map`・`end-class-map` で閉じる。
   同じ閉じの語が最上位に何度も出ると、木にする規則が同じ行の重なりで止まるので、対を見て閉じの語の行を消す。
   本文に字下げの無い行があれば、本文を1字下げて塊の子にする。
2. XR の見立て: 閉じの語の塊がある・`!! IOS XR Configuration` の行がある・XR の名前のインターフェースがある。
   見立ては3にだけ使う。結果(IR・差分の JSON)に見立ての欄は無い。
3. 最上位の同じ行のまとめ(XR と見立てたときだけ): 同じ見出しの塊が後に出たら、後の見出しの行を消し、子の行を前の塊の
   後ろに足す(機器が同じ塊に入り直したときと同じ)。まとめた見出しの行は、もとの本文の行番号で返す(警告が使う)。

行の対応(もとの行番号)は、行ごとに持ち回る。木にする前の前処理の規則なので、行の書式を IR に写す表(ADR-0011)ではなく
この部品に置く(tree.py の終わりの印の規則・awplus.py のポートの範囲の分割と同じ扱い)。
"""

import re
from dataclasses import dataclass

from driftpilot.config_parser.tree import banner_line_kinds, is_dropped_line

_FIXED_CLOSERS = {
    "route-policy": "end-policy",
    "group": "end-group",
    "policy-global": "end-global",
    "policy-map": "end-policy-map",
    "class-map": "end-class-map",
}
_SET_CLOSER = "end-set"
# 閉じの語は見出しにしない(`end-set` も `-set` で終わる語なので、外さないと対にならない閉じの語が見出しになる)
_CLOSERS = frozenset({*_FIXED_CLOSERS.values(), _SET_CLOSER})

_XR_BANNER = "!! IOS XR Configuration"
_XR_INTERFACE = re.compile(r"interface (?:preconfigure )?(?P<name>\S+)")
_XR_INTERFACE_NAME = re.compile(r"(?:MgmtEth|Bundle-Ether)|.*/CPU\d+/")

Numbered = tuple[int, str]


@dataclass(frozen=True)
class IosxrPrep:
    """前処理の結果。text は木にする文字列、iosxr は XR と見立てたか、merged_lines はまとめて消した見出しの行番号。"""

    text: str
    iosxr: bool
    merged_lines: tuple[int, ...]


def _normalize(line: str) -> str:
    return " ".join(line.split())


def _is_unindented(line: str) -> bool:
    return bool(line) and not line[0].isspace()


def _closer_for(line: str, kind: int) -> str | None:
    """バナーの外の字下げの無い行が見出しなら、その閉じの語。そうでなければ None。"""
    if kind or not _is_unindented(line):
        return None
    word = line.split()[0]
    if word in _CLOSERS:
        return None
    if word in _FIXED_CLOSERS:
        return _FIXED_CLOSERS[word]
    if word.endswith("-set"):
        return _SET_CLOSER
    return None


def _close_pairs(lines: list[str]) -> tuple[list[Numbered], int]:
    """閉じの語の対を見て、閉じの語の行を消す(必要なら本文を字下げる)。(行番号つきの行, 作った塊の数) を返す。"""
    kinds = banner_line_kinds(lines)
    out: list[Numbered] = []
    blocks = 0
    i = 0
    while i < len(lines):
        closer = _closer_for(lines[i], kinds[i])
        end: int | None = None
        if closer is not None:
            for j in range(i + 1, len(lines)):
                if kinds[j] or not _is_unindented(lines[j]):
                    continue
                if _normalize(lines[j]) == closer:
                    end = j
                    break
                if _closer_for(lines[j], kinds[j]) is not None:
                    break
        if end is None:
            out.append((i + 1, lines[i]))
            i += 1
            continue
        blocks += 1
        body = range(i + 1, end)
        indent = any(
            _is_unindented(lines[k]) and not is_dropped_line(lines[k]) for k in body
        )
        out.append((i + 1, lines[i]))
        for k in body:
            line = lines[k]
            out.append((k + 1, " " + line if indent and line.strip() else line))
        i = end + 1
    return out, blocks


def _looks_like_iosxr(lines: list[str]) -> bool:
    """バナーの外の字下げの無い行に、XR の手がかり(`!! IOS XR Configuration`・XR の名前のインターフェース)があるか。"""
    for line, kind in zip(lines, banner_line_kinds(lines), strict=True):
        if kind or not _is_unindented(line):
            continue
        if line.startswith(_XR_BANNER):
            return True
        m = _XR_INTERFACE.fullmatch(_normalize(line))
        if m is not None and _XR_INTERFACE_NAME.match(m["name"]):
            return True
    return False


def _merge_top_level(numbered: list[Numbered]) -> tuple[list[Numbered], tuple[int, ...]]:
    """最上位の同じ見出しの塊を、最初の塊にまとめる。(行番号つきの行, 消した見出しの行番号) を返す。"""
    lines = [line for _, line in numbered]
    # 最上位の塊に分ける。塊の子は、後に続く行頭が空白の行・空の行・木にする前に消える字下げの無い行。
    # バナーは本文を含めて1つの塊にして、見出しとして扱わない
    blocks: list[tuple[Numbered, bool, list[Numbered]]] = []  # (見出し, 比べるか, 子)
    for item, kind in zip(numbered, banner_line_kinds(lines), strict=True):
        line = item[1]
        continues = kind == 2 or (kind == 0 and (not line or line[0].isspace() or is_dropped_line(line)))
        if blocks and continues:
            blocks[-1][2].append(item)
        else:
            comparable = kind == 0 and _is_unindented(line) and not is_dropped_line(line)
            blocks.append((item, comparable, []))

    merged: list[int] = []
    position: dict[str, int] = {}
    kept: list[tuple[Numbered, list[Numbered]]] = []
    for heading, comparable, children in blocks:
        if comparable:
            key = _normalize(heading[1])
            if key in position:
                kept[position[key]][1].extend(children)
                merged.append(heading[0])
                continue
            position[key] = len(kept)
        kept.append((heading, children))
    result = [item for heading, children in kept for item in (heading, *children)]
    return result, tuple(merged)


def prepare_iosxr(text: str) -> IosxrPrep:
    """XR の前処理を当てる。何もしなければ、もとの文字列をそのまま返す。"""
    lines = text.splitlines()
    numbered, blocks = _close_pairs(lines)
    iosxr = blocks > 0 or _looks_like_iosxr(lines) or _looks_like_iosxr([line for _, line in numbered])
    merged: tuple[int, ...] = ()
    if iosxr:
        numbered, merged = _merge_top_level(numbered)
    if blocks == 0 and not merged:
        return IosxrPrep(text, iosxr, ())
    result = "\n".join(line for _, line in numbered)
    return IosxrPrep(result + "\n" if text.endswith("\n") else result, iosxr, merged)
