"""Cisco ASA の見立てと、木にする前の前処理(ADR-0214 決定 2・3、REQ-303)。

ASA の config は Cisco 系(A型)の読み方で読む。機種の選択肢は足さず、前処理の前の本文に次のどれかがあれば ASA と見立てる。
- 字下げの無い `ASA Version ` で始まる行
- 字下げの無い `: Saved`・`: Written by`・`: Serial Number:`・`: Hardware:` で始まる行
- 字下げのある `nameif <語>` の行と、字下げのある `security-level <数>` の行の両方
XR と見立てた config(iosxr.py)は ASA と見立てない(呼ぶ側で外す)。見立ては、ここの前処理と、ACL・手動の NAT の読み方
(acls.py・asa_nat.py)にだけ使う。結果(IR・差分の JSON)に見立ての欄は無い。

ASA と見立てたときだけ、木にする前に次を順に行う(行ごとにもとの行番号を持ち回る。iosxr.py と同じ)。
1. 字下げの無い `:` で始まる行と `Cryptochecksum:` で始まる行を消す
2. 字下げの無い `banner <種類>[ <本文>]` の行(1 行ずつのバナー)を、種類ごとに IOS の `^C` で囲む形にまとめる。
   本文のどれかが `!`・`%`・`EOF` と同じか、`^` で始まるか、`^C` を含む種類は、IOS の形なので変えない
3. 字下げの無い `access-list <名前> <1 語以上>` の行を、名前ごとに `access-list <名前>` の塊(子は名前の後ろ)にまとめる。
   ただし名前が伏せる表の秘密語(snmp・key・password など。`has_mask_word`)の行はまとめず、最上位の行のまま残す
   (前処理の前から AI に送らない行を、子にして送る側に変えないため)
4. 最上位の同じ見出しの塊を 1 つにまとめる(iosxr.merge_top_level)。まとめた見出しの行番号は警告に使う。ただし
   `object network <名前>` を、子が全部 `nat ` で始まる塊で開き直したものは、まとめるが警告に数えない
   (実機の出力は、オブジェクトの NAT を別の塊で出す)

行の書式を IR に写す表(ADR-0011)ではなく、木にする前の前処理の規則なので、この部品に置く
(tree.py の終わりの印の規則・awplus.py・iosxr.py と同じ扱い)。
"""

import re
from dataclasses import dataclass

from driftpilot.config_parser.device_settings import has_mask_word
from driftpilot.config_parser.iosxr import Numbered, merge_top_level
from driftpilot.config_parser.tree import is_dropped_line

_MARKERS = ("ASA Version ", ": Saved", ": Written by", ": Serial Number:", ": Hardware:")
_NAMEIF = re.compile(r"nameif \S+")
_SECURITY_LEVEL = re.compile(r"security-level \d+")
_BANNER = re.compile(r"banner (?P<kind>\S+)(?: (?P<body>.*))?")
_OBJECT_NETWORK = re.compile(r"object network \S+")
_IOS_BANNER_ENDS = frozenset({"!", "%", "EOF"})


@dataclass(frozen=True)
class AsaPrep:
    """前処理の結果。text は木にする文字列、asa は ASA と見立てたか、merged_lines はまとめて消した見出しの行番号(昇順)。"""

    text: str
    asa: bool
    merged_lines: tuple[int, ...]


def _is_unindented(line: str) -> bool:
    return bool(line) and not line[0].isspace()


def looks_like_asa(text: str) -> bool:
    """前処理の前の本文が ASA と見立てられるか。"""
    nameif = False
    security_level = False
    for line in text.splitlines():
        if _is_unindented(line):
            if line.startswith(_MARKERS):
                return True
            continue
        words = " ".join(line.split())
        if _NAMEIF.fullmatch(words):
            nameif = True
        elif _SECURITY_LEVEL.fullmatch(words):
            security_level = True
        if nameif and security_level:
            return True
    return False


def _drop_colon_lines(numbered: list[Numbered]) -> list[Numbered]:
    return [
        item for item in numbered
        if not (_is_unindented(item[1]) and item[1].startswith((":", "Cryptochecksum:")))
    ]


def _join_banners(numbered: list[Numbered]) -> tuple[list[Numbered], set[int]]:
    """1 行ずつのバナーを `^C` で囲む形にまとめる。(行, まとめて作った行の位置の集合) を返す。"""
    bodies: dict[str, list[str]] = {}
    for _, line in numbered:
        m = _BANNER.fullmatch(line) if _is_unindented(line) else None
        if m is not None:
            bodies.setdefault(m["kind"], []).append(m["body"] or "")
    ios_form = {
        kind
        for kind, lines in bodies.items()
        if any(b in _IOS_BANNER_ENDS or b.startswith("^") or "^C" in b for b in lines)
    }
    out: list[Numbered] = []
    made: set[int] = set()
    placed: set[str] = set()
    for item in numbered:
        number, line = item
        m = _BANNER.fullmatch(line) if _is_unindented(line) else None
        if m is None or m["kind"] in ios_form:
            out.append(item)
            continue
        kind = m["kind"]
        if kind in placed:
            continue
        placed.add(kind)
        for text in (f"banner {kind} ^C", *bodies[kind], "^C"):
            made.add(len(out))
            out.append((number, text))
    return out, made


def _group_access_lists(numbered: list[Numbered], skip: set[int]) -> list[Numbered]:
    """字下げの無い `access-list <名前> <1 語以上>` の行を、名前ごとに `access-list <名前>` の塊にまとめる。

    skip はまとめない行の位置(1 行ずつのバナーから作った行)。
    """
    rest: dict[str, list[Numbered]] = {}
    parsed: dict[int, str] = {}
    for index, (number, line) in enumerate(numbered):
        if index in skip or not _is_unindented(line):
            continue
        words = line.split()
        # 名前が伏せる表の秘密語(snmp・key・password など)の行は、まとめずに最上位のまま残す。
        # 前処理の前は最上位の行として AI に送らない行なので、子にして送る側に変えない(ADR-0214 決定 8、REQ-303)
        if len(words) >= 3 and words[0] == "access-list" and not has_mask_word(f"access-list {words[1]}"):
            parsed[index] = words[1]
            rest.setdefault(words[1], []).append((number, " " + " ".join(words[2:])))
    out: list[Numbered] = []
    placed: set[str] = set()
    for index, item in enumerate(numbered):
        name = parsed.get(index)
        if name is None:
            out.append(item)
        elif name not in placed:
            placed.add(name)
            out.append((item[0], f"access-list {name}"))
            out.extend(rest[name])
    return out


def _quiet_reopen(heading: str, children: list[str]) -> bool:
    """`object network <名前>` を、子が全部 `nat ` で始まる塊で開き直したか(警告に数えない)。

    子は、空の行と木にする前に消える行を除く。1 つも無い・`nat ` で始まらない子がある開き直しは数える。
    """
    if _OBJECT_NETWORK.fullmatch(" ".join(heading.split())) is None:
        return False
    kids = [" ".join(c.split()) for c in children if c.strip() and not is_dropped_line(c)]
    return bool(kids) and all(k.startswith("nat ") for k in kids)


def prepare_asa(text: str) -> AsaPrep:
    """ASA と見立てたときだけ前処理を当てる。何も変えなければ、もとの文字列をそのまま返す。"""
    if not looks_like_asa(text):
        return AsaPrep(text, False, ())
    lines = text.splitlines()
    numbered: list[Numbered] = list(enumerate(lines, start=1))
    numbered = _drop_colon_lines(numbered)
    numbered, made = _join_banners(numbered)
    numbered = _group_access_lists(numbered, made)
    numbered, merged = merge_top_level(numbered, _quiet_reopen)
    result_lines = [line for _, line in numbered]
    if result_lines == lines:
        return AsaPrep(text, True, tuple(sorted(merged)))
    result = "\n".join(result_lines)
    return AsaPrep(result + "\n" if text.endswith("\n") else result, True, tuple(sorted(merged)))
