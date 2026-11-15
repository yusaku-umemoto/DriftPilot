"""変更前と変更後の区間をコマンドで組にする(ADR-0084 決定1、REQ-121 §2)。

省略したコマンド(`show int status` と `show interfaces status`)は同じものとして扱う。
`|` の後ろ(パイプ)はそのまま残す(`show run | section x` は `show run` と別のコマンド)。
"""

import re
import warnings
from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from driftpilot.segmentation.splitter import Segment

# 既知の語の表のうち、show-commands の表の外から足す語(REQ-121 §2)
BASE_WORDS = ("show", "running-config", "startup-config", "interfaces", "status", "version")
# `write terminal` は `show running-config` と同じ内容の表示(ADR-0152 決定1)。語の補完では拾えないので固定の対応にする
WRITE_TERMINAL_KEY = "show running-config"

# 別の語の前の部分そのものになっている語を、長いほうにまとめる固定の対応表。表から計算しない
# (計算すると、表に ipv6 を足したときに ip が ipv6 にまとまり、show ip route と show ipv6 route が同じ組になる)
ALIASES = {"interface": "interfaces"}

# 既知の語の表。初めて使うときに作る(coverage_show の読み込みの途中で segmentation が読まれるため、import を輪にしない)
_VOCABULARY: frozenset[str] | None = None


def _vocabulary() -> frozenset[str]:
    global _VOCABULARY
    if _VOCABULARY is None:
        from driftpilot.coverage_show.commands import SHOW_COMMANDS

        words = {*BASE_WORDS, *(w for c in SHOW_COMMANDS for w in c.command.lower().split())}
        _VOCABULARY = frozenset(ALIASES.get(w, w) for w in words)
    return _VOCABULARY


def _complete(word: str, vocabulary: frozenset[str]) -> str:
    word = ALIASES.get(word, word)
    if word in vocabulary:
        return word
    hits = [v for v in vocabulary if v.startswith(word)]
    return hits[0] if len(hits) == 1 else word


# ntc-templates の索引の Command の列の語。種類ごとに (種類, 値 1, 値 2) の形で持つ(ADR-0202 決定1)
#   ("abbr", 要る部分, 全部の形)  `sy[[stem]]` → ("abbr", "sy", "system")
#   ("plain", 語, 語)             `info`
#   ("regex", 正規表現, "")       `(\S+)` `\d+` など。打った語に全体が当たるか
_Matcher = tuple[str, "str | re.Pattern[str]", str]
_REGEX_CHARS = frozenset("\\.^$*+?{}[]()|")
_ABBREVIATED = re.compile(r"([^\\.^$*+?{}\[\]()|]+)\[\[([^\\.^$*+?{}\[\]()|]+)\]\]")

# 語の数ごとの索引の行。初めて使うときに読む(coverage_show と同じく import の輪を作らない)
_INDEX: dict[int, list[tuple[_Matcher, ...]]] | None = None


def _index_path() -> Path:
    import ntc_templates

    return Path(ntc_templates.__file__).parent / "templates" / "index"


def _parse_word(word: str) -> _Matcher | None:
    """索引の語 1 つを読む。正規表現として読めない語、または警告が出る語は None(その行は使わない)。"""
    found = _ABBREVIATED.fullmatch(word)
    if found:
        need, rest = found.group(1).lower(), found.group(2).lower()
        return ("abbr", need, need + rest)
    if not _REGEX_CHARS.intersection(word):
        return ("plain", word.lower(), word.lower())
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # FutureWarning など、警告が出る語は使わない(外には 1 つも出さない)
        try:
            return ("regex", re.compile(word, re.IGNORECASE), "")
        except (re.error, Warning, RecursionError, OverflowError):
            return None


def _load_index() -> dict[int, list[tuple[_Matcher, ...]]]:
    global _INDEX
    if _INDEX is None:
        index: dict[int, list[tuple[_Matcher, ...]]] = defaultdict(list)
        try:
            lines = _index_path().read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            lines = []
        header_seen = False
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not header_seen:  # 最初の行は見出し(Template, Hostname, Platform, Command)
                header_seen = True
                continue
            fields = stripped.split(",", 3)
            if len(fields) < 4 or not fields[3].strip():
                continue
            matchers = [_parse_word(w) for w in fields[3].split()]
            if any(m is None for m in matchers):
                continue
            index[len(matchers)].append(tuple(m for m in matchers if m is not None))
        _INDEX = dict(index)
    return _INDEX


def _word_sentence(matcher: _Matcher, typed: str) -> str | None:
    """索引の語に打った語が当たるなら、キーに使う形(略し方つきは全部の形、略し方の無い語はそのまま、正規表現は打った語)。"""
    kind, first, second = matcher
    if kind == "abbr":
        assert isinstance(first, str)
        return second if typed.startswith(first) and second.startswith(typed) else None
    if kind == "plain":
        return second if typed == first else None
    assert isinstance(first, re.Pattern)
    return typed if first.fullmatch(typed) else None


@lru_cache(maxsize=4096)
def _index_key(head: str) -> str | None:
    """`|` の前の部分を索引で照らす。当たった文がすべて同じならその文、当たりが無い・文が分かれるなら None。"""
    words = head.split()
    if not words:
        return None
    sentences: set[str] = set()
    for matchers in _load_index().get(len(words), ()):
        parts = [_word_sentence(m, w) for m, w in zip(matchers, words, strict=True)]
        if all(part is not None for part in parts):
            sentences.add(" ".join(p for p in parts if p is not None))
            if len(sentences) > 1:
                return None
    return next(iter(sentences)) if sentences else None


def command_key(text: str, platform: str | None = None) -> str:
    """組にするためのコマンドの名前。空白を詰めて小文字にする。

    `write terminal` は固定の対応。次に ntc-templates の索引で照らし、略し方でそろえる(ADR-0202 決定1。
    `get sys stat` → `get system status`)。索引で決まらなければ、語ごとに既知の語の表で一意に前方一致すれば完全な語にする。
    一意でなければ元の語のまま。`|` の後ろは置き換えず、「前の部分 | 後ろの部分」の形にそろえる。
    platform は REQ-121 の形どおり受け取るが、今は使わない(機種ごとの語の表は無い)。
    """
    head, bar, tail = " ".join(text.split()).lower().partition("|")
    words = head.split()
    if _is_write_terminal(words):
        key = WRITE_TERMINAL_KEY
    else:
        indexed = _index_key(" ".join(words))
        if indexed is not None:
            key = indexed
        else:
            vocabulary = _vocabulary()
            key = " ".join(_complete(w, vocabulary) for w in words)
    if bar:
        key = f"{key} | {tail.strip()}".strip()
    return key


def _is_write_terminal(words: list[str]) -> bool:
    """ちょうど2語で、1語目が write の2文字以上の前の部分、2語目が terminal の前の部分か(ADR-0152 決定1。`wr t` など)。"""
    if len(words) != 2:
        return False
    first, second = words
    return len(first) >= 2 and "write".startswith(first) and "terminal".startswith(second)


@dataclass(frozen=True)
class SegmentPair:
    """組1つ。key はコマンドの名前(コマンド不明の組は None)。相手なしは片方が None。"""

    key: str | None
    before: Segment | None
    after: Segment | None


def pair_segments(before: tuple[Segment, ...], after: tuple[Segment, ...]) -> list[SegmentPair]:
    """同じコマンドの名前の区間を、出てきた順に1つずつ組にする。残りは相手なし。

    コマンド不明の区間は、両側とも1区間だけのときだけ組にする。それ以外は相手なし。
    並びは変更前の出てきた順(組と変更前だけの区間)、続けて変更後だけの区間を出てきた順に。
    """
    unknown_before = [s for s in before if s.command is None]
    unknown_after = [s for s in after if s.command is None]
    pair_unknown = len(unknown_before) == 1 and len(unknown_after) == 1
    waiting: dict[str, deque[Segment]] = defaultdict(deque)
    for seg in after:
        if seg.command is not None:
            waiting[command_key(seg.command)].append(seg)
    used: set[int] = set()
    pairs: list[SegmentPair] = []
    for seg in before:
        if seg.command is None:
            partner = unknown_after[0] if pair_unknown else None
            pairs.append(SegmentPair(None, seg, partner))
        else:
            key = command_key(seg.command)
            partner = waiting[key].popleft() if waiting[key] else None
            pairs.append(SegmentPair(key, seg, partner))
        if partner is not None:
            used.add(partner.number)
    for seg in after:
        if seg.number not in used:
            pairs.append(SegmentPair(None if seg.command is None else command_key(seg.command), None, seg))
    return pairs


def pair_within(segments: Sequence[Segment]) -> list[SegmentPair]:
    """1つのログの中で、同じコマンドの名前の区間を出てきた順に2つずつ組にする(ADR-0152 決定2)。先が変更前、後が変更後。

    3回あれば1回目と2回目を組にし、3回目は余り。余りとコマンド不明の区間は SegmentPair(key, 区間, None)。
    並びは組の先の区間(余りはその区間)の出てきた順。
    """
    waiting: dict[str, Segment] = {}
    found: list[tuple[int, SegmentPair]] = []
    for seg in segments:
        if seg.command is None:
            found.append((seg.number, SegmentPair(None, seg, None)))
            continue
        key = command_key(seg.command)
        first = waiting.pop(key, None)
        if first is None:
            waiting[key] = seg
        else:
            found.append((first.number, SegmentPair(key, first, seg)))
    found += [(seg.number, SegmentPair(key, seg, None)) for key, seg in waiting.items()]
    return [pair for _number, pair in sorted(found, key=lambda item: item[0])]


def has_repeats(segments: Sequence[Segment]) -> bool:
    """同じコマンドの名前の区間が2つ以上あるか(コマンド不明の区間は数えない。ADR-0152 決定2)。"""
    keys = [command_key(seg.command) for seg in segments if seg.command is not None]
    return len(keys) != len(set(keys))
