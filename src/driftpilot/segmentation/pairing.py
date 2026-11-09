"""変更前と変更後の区間をコマンドで組にする(ADR-0084 決定1、REQ-121 §2)。

省略したコマンド(`show int status` と `show interfaces status`)は同じものとして扱う。
`|` の後ろ(パイプ)はそのまま残す(`show run | section x` は `show run` と別のコマンド)。
"""

from collections import defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass

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


def command_key(text: str, platform: str | None = None) -> str:
    """組にするためのコマンドの名前。空白を詰めて小文字にし、語ごとに既知の語の表で一意に前方一致すれば完全な語にする。

    一意でなければ元の語のまま。`|` の後ろは置き換えず、「前の部分 | 後ろの部分」の形にそろえる。
    platform は REQ-121 の形どおり受け取るが、今は使わない(機種ごとの語の表は無い)。
    """
    head, bar, tail = " ".join(text.split()).lower().partition("|")
    vocabulary = _vocabulary()
    words = head.split()
    if _is_write_terminal(words):
        key = WRITE_TERMINAL_KEY
    else:
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
