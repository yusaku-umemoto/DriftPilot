"""ほかの Pilot へ返す、伏せた差分の本文(ADR-0203 決定5。REQ-286)。

比べ終わった結果から、AI に送るときと同じ伏せ方(llm_analyzer.payload の build_payload)で差分の本文だけを作る。
DriftPilot の指示文・前提の文は入れない。AI は呼ばない(送る文を作る処理だけを使う)。

組ごとの行は llm_inputs で作る(AI に送るときと同じ行)。送らない組は sendable_outcome(sendable_pair と同じ決まり)で決め、
見出しと省略の 1 行だけにする。伏せる語は呼び出し側が渡す(利用者の設定の extra_secret_words。API の鍵は読まない)。
"""

from collections.abc import Sequence
from dataclasses import dataclass

from driftpilot.core.compare import llm_inputs
from driftpilot.core.compare_all import MultiCompareOutcome, sendable_outcome
from driftpilot.core.types import CompareOutcome, CompareRequest
from driftpilot.llm_analyzer.payload import MaskedSection, WholeSection, masked_body, source_of


@dataclass(frozen=True)
class MaskedPayload:
    """text は masked_payload.txt の中身、diffs は差分の件数、not_sent は送らない側にした行の数、words_count は足した伏せる語の数。"""

    text: str
    diffs: int
    not_sent: int
    words_count: int


def diff_count(outcome: CompareOutcome) -> int:
    """組 1 つの AI に送る対象の差分の件数(payload.source_of の一覧の件数。画面の「件数」と同じ決まり)。"""
    found = source_of(outcome.structured, outcome.raw, outcome.show)
    return len(found[1]) if found is not None else 0


def _section(request: CompareRequest, outcome: CompareOutcome, command: str | None) -> MaskedSection | None:
    """組 1 つを本文の組にする。差分が無く送らない組は None(書かない)。"""
    inputs = llm_inputs(request, outcome) if sendable_outcome(request, outcome) else None
    if inputs is None:
        return MaskedSection(command, None) if diff_count(outcome) else None
    kind = outcome.kind if outcome.kind in ("config", "show") else None
    return MaskedSection(
        command,
        WholeSection(
            command=command,
            kind=kind,
            structured=outcome.structured,
            raw=outcome.raw,
            show=outcome.show,
            before_lines=inputs.before_lines,
            after_lines=inputs.after_lines,
            before_source=inputs.before_source,
            after_source=inputs.after_source,
            show_command=inputs.show_command,
        ),
    )


def masked_single(request: CompareRequest, outcome: CompareOutcome, words: Sequence[str] = ()) -> MaskedPayload:
    """区間に分けない比較(1 組)の伏せた本文。見出しは付けない。選択待ち・エラーの結果は指紋の行だけ。"""
    section = _section(request, outcome, None) if outcome.status in ("no_difference", "difference") else None
    body = masked_body([section] if section is not None else [], extra_words=words, headings=False)
    return MaskedPayload(body.text, diff_count(outcome), body.not_sent, len(words))


def masked_all(outcome: MultiCompareOutcome, words: Sequence[str] = ()) -> MaskedPayload:
    """区間ごとの比較(全部の組)の伏せた本文。組ごとに「## コマンドの名前」の見出しを付ける。相手なしの区間は入れない。"""
    sections = [s for pair in outcome.pairs if (s := _section(pair.request, pair.outcome, pair.command)) is not None]
    body = masked_body(sections, extra_words=words, headings=True)
    return MaskedPayload(body.text, sum(diff_count(p.outcome) for p in outcome.pairs), body.not_sent, len(words))
