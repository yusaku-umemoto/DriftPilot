"""変更の行で、前後で違う文字の範囲を求める（ADR-0165 決定1〜3、REQ-226）。

標準ライブラリだけを使い、ほかの driftpilot のモジュールを読まない。
画面の論理（screen_model）と HTML の出力（report_output）の両方から使う。
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

Range = tuple[int, int]  # 始まり(含む)と終わり(含まない)。Python の文字の位置

TOKEN = re.compile(r"<secret:\d+>|[^\W\d_]+|\d+|\s+|[^\w\s]|_")
MAX_LINE_CHARS = 500  # どちらかの側がこれより長い行は塗らない
MAX_DIFF_RATIO = 0.6  # 違いの割合がこれを超える行は塗らない
MAX_MARKED_ROWS = 5000  # 1 つの表で塗る行の数の上限(先頭から数える)


@dataclass(frozen=True)
class LineMarks:
    before: tuple[Range, ...]
    after: tuple[Range, ...]


def _tokens(text: str) -> list[re.Match[str]]:
    return list(TOKEN.finditer(text))


def _join(text: str, ranges: list[Range]) -> list[Range]:
    """間の文字が空白だけの、となり合う範囲を 1 つにつなぐ。"""
    joined: list[Range] = []
    for start, end in ranges:
        if joined:
            prev_start, prev_end = joined[-1]
            gap = text[prev_end:start]
            if gap == "" or gap.isspace():
                joined[-1] = (prev_start, end)
                continue
        joined.append((start, end))
    return joined


def _trim(text: str, ranges: list[Range]) -> list[Range]:
    """範囲の前後の空白を外す。空白だけの範囲はそのまま残す。"""
    trimmed: list[Range] = []
    for start, end in ranges:
        part = text[start:end]
        if part.isspace():
            trimmed.append((start, end))
            continue
        lead = len(part) - len(part.lstrip())
        tail = len(part) - len(part.rstrip())
        trimmed.append((start + lead, end - tail))
    return trimmed


def line_marks(before: str, after: str) -> LineMarks | None:
    """変更前と変更後の 1 行から、それぞれの側の違う範囲を求める。塗らない行は None。"""
    if before == after or not before or not after:
        return None
    if len(before) > MAX_LINE_CHARS or len(after) > MAX_LINE_CHARS:
        return None
    a = _tokens(before)
    b = _tokens(after)
    matcher = SequenceMatcher(
        None, [m.group() for m in a], [m.group() for m in b], autojunk=False
    )
    before_ranges: list[Range] = []
    after_ranges: list[Range] = []
    equal_chars = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            equal_chars += sum(len(m.group()) for m in a[i1:i2])
            continue
        if i2 > i1:
            before_ranges.append((a[i1].start(), a[i2 - 1].end()))
        if j2 > j1:
            after_ranges.append((b[j1].start(), b[j2 - 1].end()))
    ratio = 1 - 2 * equal_chars / (len(before) + len(after))
    if ratio > MAX_DIFF_RATIO:
        return None
    before_out = tuple(_trim(before, _join(before, before_ranges)))
    after_out = tuple(_trim(after, _join(after, after_ranges)))
    if not before_out and not after_out:
        return None
    return LineMarks(before=before_out, after=after_out)


def utf16_ranges(text: str, ranges: Sequence[Range]) -> list[list[int]]:
    """Python の文字の位置を、JavaScript の文字の位置（UTF-16 の単位）に直す。"""
    offsets = [0]
    for ch in text:
        offsets.append(offsets[-1] + (2 if ord(ch) > 0xFFFF else 1))
    return [[offsets[start], offsets[end]] for start, end in ranges]
