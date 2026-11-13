"""複数のコマンドのログをまとめて比較する(ADR-0084 決定1・2、REQ-121 §3)。

両側を区間に分けてコマンドで組にし(segmentation.pairing)、組ごとに今の run_compare を区間の番号を指定して呼ぶ。
1組の比較の中身と結果の形は変えない。種類はコマンドから決める(設定の語を含めば config、ほかは show)。
コマンドが分からない組だけ、利用者の kind・platform をそのまま使う(今の compare に区間の番号を指定したのと同じ要求)。

全体の status は error・difference・no_difference の3つ。選択待ち(ambiguous_format)の組は error に数える
(比べていない組を、差分なしや差分ありと取り違えさせないため)。組も相手なしも無いときも error にする。

両側の本文が同じで同じコマンドが2回以上あれば、1つのログの中で出てきた順に2つずつ組にする(ADR-0152 決定2。pair_within)。
組にならなかった区間は unpaired に入れるが、全体の status には数えない(single_log)。

打ち間違いなどのエラーの出力の区間(segmentation.is_error_output)は、組にする前に外し、残りの区間で組にする(ADR-0195 決定1)。
外した区間は error_outputs に入れ、組にも unpaired にも入れない。全体の status にも数えない。

組は後から手で選び直せる(ADR-0195 決定2・REQ-269)。pair_by_hand は選んだ2つの区間だけを組にして、その1組だけを比べ直し
(_run_one と同じ道)、unpair は組を外して2つの区間を戻す(比べない)。解けた組の区間は、エラーの出力なら error_outputs、
ふつうの区間なら unpaired に戻す。どちらも組の並びと番号・全体の status を作り直し、AI の答えの値(llm・llm_pairs・llm_chosen)は消す。

LLM 解析(REQ-122 §4・§5 / ADR-0084 決定6)は、CLI の --all-segments --llm では一括比較と同じ2段(compare_all の llm・approved)。
組ごとの要求は一括比較の _LlmPerJob を、組の番号の文字列を id にして使う(承認した内容だけを送る・承認していない組は送らない・
続けて失敗したら残りでは呼ばない・比較が失敗した組には送らない)。要約と承認は core.batch_llm の関数に PairResult をそのまま渡す。
比較の窓は analyze_all で全部の組を1つの文にして1回で送る(ADR-0152 決定3。記号の表は全部の組で1つ)。
一括比較のまとめて比べた組も、組ごとに analyze_all で同じ文を作って1回で送る(ADR-0178 決定4・REQ-241。core.batch._run_one)。
"""

import dataclasses
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from driftpilot import i18n
from driftpilot.config_parser.family_hint import AUTO_PLATFORM
from driftpilot.config_parser.platforms import CONFIG_PLATFORM_NAMES
from driftpilot.core.batch import _LlmPerJob
from driftpilot.core.compare import llm_inputs, looks_not_sir, run_compare
from driftpilot.core.types import (
    CompareCancelled,
    CompareError,
    CompareOutcome,
    CompareRequest,
    SideName,
    SideSelection,
)
from driftpilot.llm_analyzer import LlmAnalysis, LlmRequest, WholeSection
from driftpilot.llm_analyzer.analyze import analyze_whole_sent
from driftpilot.llm_analyzer.follow_up import same_sent_whole_body
from driftpilot.llm_analyzer.payload import LlmInputs
from driftpilot.segmentation import (
    Segment,
    SegmentPair,
    command_key,
    has_repeats,
    is_error_output,
    pair_segments,
    pair_within,
    split_log,
)

_TEXTS_JA = {"unexpected": "予期しないエラーで比較できませんでした: {error_type}: {error}"}
_TEXTS_EN = {"unexpected": "The comparison failed with an unexpected error: {error_type}: {error}"}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

# この語をコマンドの名前に含む組は config として比べる(REQ-121 §3)
CONFIG_WORDS = ("running-config", "startup-config", "current-configuration", "full-configuration")

MultiStatus = Literal["no_difference", "difference", "error"]


@dataclass(frozen=True)
class PairJob:
    """組の見出し(一括比較の Job の id・label・index と同じ並び)。id は組の番号の文字列、label はコマンドの文。"""

    id: str
    label: str | None
    index: int


@dataclass(frozen=True)
class PairResult:
    """組1つの比較。command は変更前の区間の元のコマンドの文(コマンド不明は None)。index は組の番号(1始まり)。

    request は組ごとの要求(保存の1件を作る build_record に渡せるように持つ)。
    """

    command: str | None
    before_number: int
    after_number: int
    request: CompareRequest
    outcome: CompareOutcome
    index: int
    # 比較の窓で手で組にした組か(REQ-269。自動の組は偽。保存の job.paired_by_hand に出る)
    manual: bool = False

    @property
    def job(self) -> PairJob:
        """core.batch_llm の関数と保存の行の job に使う見出し。"""
        return PairJob(str(self.index), self.command, self.index)


@dataclass(frozen=True)
class Unpaired:
    """相手なしの区間1つ。"""

    side: SideName
    segment: Segment


@dataclass(frozen=True)
class MultiCompareOutcome:
    """まとめての比較の結果。

    single_log は1つのログの中で組にしたか(両側の本文が同じで、同じコマンドが2回以上ある。ADR-0152 決定2)。
    そのときの unpaired は組にならなかった区間(1回だけのコマンド・コマンド不明)で、全体の状態には数えない。
    llm は全部の組をまとめて1回で送った解析(比較の窓と一括比較のまとめて比べた組。analyze_all。ADR-0152 決定3・ADR-0178 決定4)、
    llm_pairs はその本文に入れた組の pairs の位置(保存には入れない)、llm_chosen は送る組として選んだ pairs の位置
    (None はすべて。REQ-265。保存には入れない)。CLI の --all-segments --llm は今のまま組ごとの outcome.llm に入る。
    """

    status: MultiStatus
    pairs: list[PairResult]
    unpaired: list[Unpaired]
    single_log: bool = False
    llm: LlmAnalysis | None = None
    llm_pairs: tuple[int, ...] = ()
    llm_chosen: tuple[int, ...] | None = None
    # 組にする前に外したエラーの出力の区間(ADR-0195 決定1)。変更前の出てきた順、続けて変更後の順。
    # 1つのログの中で組にしたときは、同じ本文の1回分だけ変更前の側として入れる。全体の状態には数えない
    error_outputs: tuple[Unpaired, ...] = ()


def is_config_command(key: str) -> bool:
    """コマンドの名前(command_key)の `|` より前に設定の語を含むか。"""
    head = key.partition("|")[0]
    return any(word in head for word in CONFIG_WORDS)


def pair_request(request: CompareRequest, pair: SegmentPair) -> CompareRequest:
    """組1つの要求。区間の番号を両側に指定し、種類と機種をコマンドから決める。"""
    assert pair.before is not None and pair.after is not None
    numbered = dataclasses.replace(
        request,
        before_selection=SideSelection(number=pair.before.number),
        after_selection=SideSelection(number=pair.after.number),
    )
    if pair.key is None:
        return numbered
    if is_config_command(pair.key):
        # 今の比較は、config に平坦 config・FortiGate 以外の名前(cisco_ios など)を渡すとエラーにするため渡さない
        # 機種の自動(auto)はそのまま渡し、組ごとに run_compare が決める(ADR-0151 決定4)
        keep = request.platform in CONFIG_PLATFORM_NAMES or request.platform == AUTO_PLATFORM
        platform = request.platform if keep else None
        return dataclasses.replace(numbered, kind="config", platform=platform, show_command=None)
    if request.platform == AUTO_PLATFORM:
        # show の機種は自動にしない(ADR-0151 決定6)。機種の指定が無いのと同じ単純比較にする
        return dataclasses.replace(numbered, kind="show", platform=None, show_command=None)
    # 今の比較は機種とコマンドの片方だけだとエラーにするため、--platform が無ければコマンドも渡さない
    show_command = pair.before.command if request.platform else None
    return dataclasses.replace(numbered, kind="show", platform=request.platform, show_command=show_command)


def wants_all(request: CompareRequest) -> bool:
    """比較の画面がまとめて比べる場面か(REQ-122 §2)。区間の選択が両側とも無く、どちらかの入力が2区間以上に分かれるとき
    (今の run_compare が「区間を選んでください」で止まる場面)。
    """
    for selection in (request.before_selection, request.after_selection):
        if selection.command is not None or selection.number is not None:
            return False
    return any(len(split_log(side.text, prompt=request.prompt).segments) >= 2 for side in (request.before, request.after))


def compare_all(
    request: CompareRequest,
    *,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[str], None] | None = None,
    llm: LlmRequest | None = None,
    approved: Mapping[str, str] | None = None,
    on_pair: Callable[[int, int, str | None], None] | None = None,
) -> MultiCompareOutcome:
    """全部の組を比べる。should_cancel は組の境目で見て、真なら CompareCancelled("pair") を送出する。

    1組の失敗で止めない。run_compare が例外を出した組は status=error(unexpected_error)の結果にする。
    on_progress には各組の run_compare の工程をそのまま流す。llm は入口が組み立てた LLM 解析の要求(全部の組で使い回す)。
    approved は送る段で使う「組の番号の文字列 → 確認で承認した送信内容」(run_batch と同じ)。
    on_pair(何組目か, 比べる組の数, コマンドの文)は各組を比べる前に1回呼ぶ(REQ-264 §3。数は両側がそろった組。
    コマンド不明は None)。どれも省けば今と同じ動き。
    """
    all_before = split_log(request.before.text, prompt=request.prompt).segments
    all_after = split_log(request.after.text, prompt=request.prompt).segments
    # エラーの出力の区間は組にする前に外す(ADR-0195 決定1)
    before = tuple(s for s in all_before if not is_error_output(s))
    after = tuple(s for s in all_after if not is_error_output(s))
    results: list[PairResult] = []
    unpaired: list[Unpaired] = []
    llm_per_pair = _LlmPerJob(llm, approved)
    # 両側の本文が同じで、同じコマンドが2回以上あれば、1つのログの中で組にする(ADR-0152 決定2。パスは見ない)
    single_log = request.before.text == request.after.text and has_repeats(before)
    pairs = pair_within(before) if single_log else pair_segments(before, after)
    error_outputs = tuple(Unpaired("before", s) for s in all_before if is_error_output(s))
    if not single_log:
        error_outputs += tuple(Unpaired("after", s) for s in all_after if is_error_output(s))
    total = sum(1 for pair in pairs if pair.before is not None and pair.after is not None)
    for pair in pairs:
        if pair.before is None or pair.after is None:
            side: SideName = "before" if pair.after is None else "after"
            unpaired.append(Unpaired(side, pair.before if pair.before is not None else pair.after))
            continue
        if should_cancel is not None and should_cancel():
            raise CompareCancelled("pair")
        index = len(results) + 1
        if on_pair is not None:
            on_pair(index, total, pair.before.command)
        pair_req = pair_request(request, pair)
        outcome = _run_one(pair_req, llm_per_pair.request(str(index)), on_progress)
        llm_per_pair.record(outcome.llm)
        results.append(PairResult(pair.before.command, pair.before.number, pair.after.number, pair_req, outcome, index))
    if single_log:
        # 1回だけのコマンドとコマンド不明の区間は比べていないので、全体の状態に数えない(terminal length 0 などで差分ありにしない)
        return MultiCompareOutcome(overall_status(results, []), results, unpaired, single_log=True, error_outputs=error_outputs)
    return MultiCompareOutcome(overall_status(results, unpaired), results, unpaired, error_outputs=error_outputs)


def _numbered(request: CompareRequest) -> tuple[dict[int, Segment], dict[int, Segment]]:
    """両側の区間を、compare_all と同じ切り分けで番号から引ける形にする(エラーの出力の区間も含む)。"""
    before = split_log(request.before.text, prompt=request.prompt).segments
    after = split_log(request.after.text, prompt=request.prompt).segments
    return {s.number: s for s in before}, {s.number: s for s in after}


def _regroup(
    outcome: MultiCompareOutcome,
    kept: list[PairResult],
    loose: list[Unpaired],
) -> MultiCompareOutcome:
    """選び直した後の結果。組は変更前の区間の番号の順に並べて番号を振り直し、相手なしとエラーの出力は
    (変更前→変更後, 区間の番号)の順にする。全体の状態は組と相手なしから求め直す。AI の答えの値は消す。

    loose は組から外れた区間と、元から相手なし・エラーの出力だった区間のうち残るもの。
    エラーの出力の区間(is_error_output)は error_outputs、ほかは unpaired に入れる。
    """
    pairs = [
        dataclasses.replace(p, index=k) for k, p in enumerate(sorted(kept, key=lambda p: p.before_number), start=1)
    ]
    ordered = sorted(loose, key=lambda u: (u.side != "before", u.segment.number))
    unpaired = [u for u in ordered if not is_error_output(u.segment)]
    error_outputs = tuple(u for u in ordered if is_error_output(u.segment))
    return MultiCompareOutcome(
        overall_status(pairs, unpaired),
        pairs,
        unpaired,
        single_log=False,
        llm=None,
        llm_pairs=(),
        llm_chosen=None,
        error_outputs=error_outputs,
    )


def _loose_segments(outcome: MultiCompareOutcome) -> list[Unpaired]:
    return [*outcome.unpaired, *outcome.error_outputs]


@dataclass(frozen=True)
class PartnerCandidate:
    """手で相手を選ぶときの候補の区間1つ(REQ-269 §2)。

    state は今の状態: unpaired(相手なし)・error(エラーの出力)・paired(組になっている)。
    paired のとき、leftover は選ぶと相手を失う組のもう片方の区間、leftover_error はその本文がエラーの出力か
    (真なら相手なしでなくエラーの出力に戻る)。
    """

    segment: Segment
    state: Literal["unpaired", "error", "paired"]
    leftover: Unpaired | None = None
    leftover_error: bool = False


def partner_candidates(request: CompareRequest, outcome: MultiCompareOutcome, side: SideName) -> list[PartnerCandidate]:
    """side の側の区間のすべてを、番号の順に候補として返す(相手なし・エラーの出力・組になっている区間のすべて)。"""
    befores, afters = _numbered(request)
    segments = befores if side == "before" else afters
    other = afters if side == "before" else befores
    unpaired = {u.segment.number for u in outcome.unpaired if u.side == side}
    errors = {u.segment.number for u in outcome.error_outputs if u.side == side}
    paired = {(p.before_number if side == "before" else p.after_number): p for p in outcome.pairs}
    candidates: list[PartnerCandidate] = []
    for number in sorted(segments):
        segment = segments[number]
        if number in paired:
            pair = paired[number]
            left_side: SideName = "after" if side == "before" else "before"
            left = other[pair.after_number if side == "before" else pair.before_number]
            candidates.append(PartnerCandidate(segment, "paired", Unpaired(left_side, left), is_error_output(left)))
        elif number in errors:
            candidates.append(PartnerCandidate(segment, "error"))
        elif number in unpaired:
            candidates.append(PartnerCandidate(segment, "unpaired"))
    return candidates


def pair_by_hand(
    request: CompareRequest,
    outcome: MultiCompareOutcome,
    before_number: int,
    after_number: int,
    *,
    on_progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MultiCompareOutcome:
    """変更前の区間 before_number と変更後の区間 after_number を手で組にし、その1組だけを比べ直す(REQ-269 §3)。

    request は outcome を作ったときの要求(区間の切り分けと組ごとの要求に使う)。1つのログの中で組にした結果・
    番号の区間が無いときは ValueError。選んだ区間が別の組に入っていれば、その組は解け、残りの区間は相手なし
    (本文がエラーの出力ならエラーの出力)に戻る。ほかの組の比較はやり直さない(run_compare を呼ぶのは新しい組の1回だけ)。
    組の名前は変更前の区間のコマンド。種類と機種を決めるコマンドの名前は変更前の区間のコマンドから作る(コマンド不明は None)。
    中止の印は比べる前に見て、真なら CompareCancelled("pair") を送出する。
    """
    if outcome.single_log:
        raise ValueError("single_log")
    befores, afters = _numbered(request)
    if before_number not in befores or after_number not in afters:
        raise ValueError("no such segment")
    before, after = befores[before_number], afters[after_number]
    kept: list[PairResult] = []
    loose: list[Unpaired] = []
    for pair in outcome.pairs:
        if pair.before_number == before_number or pair.after_number == after_number:
            # 解ける組。選んだ区間でないほうの区間は、相手なしかエラーの出力に戻す
            if pair.before_number != before_number:
                loose.append(Unpaired("before", befores[pair.before_number]))
            if pair.after_number != after_number:
                loose.append(Unpaired("after", afters[pair.after_number]))
        else:
            kept.append(pair)
    loose += [
        u
        for u in _loose_segments(outcome)
        if not (
            (u.side == "before" and u.segment.number == before_number)
            or (u.side == "after" and u.segment.number == after_number)
        )
    ]
    if should_cancel is not None and should_cancel():
        raise CompareCancelled("pair")
    key = None if before.command is None else command_key(before.command)
    pair_req = pair_request(request, SegmentPair(key, before, after))
    result = _run_one(pair_req, None, on_progress)
    kept.append(PairResult(before.command, before_number, after_number, pair_req, result, 0, manual=True))
    return _regroup(outcome, kept, loose)


def unpair(request: CompareRequest, outcome: MultiCompareOutcome, position: int) -> MultiCompareOutcome:
    """outcome.pairs の position の組を外し、両方の区間を相手なし(本文がエラーの出力ならエラーの出力)に戻す(REQ-269 §3)。

    比べない(run_compare を呼ばない)。1つのログの中で組にした結果・position が組の位置でないときは ValueError。
    """
    if outcome.single_log:
        raise ValueError("single_log")
    if not 0 <= position < len(outcome.pairs):
        raise ValueError("no such pair")
    befores, afters = _numbered(request)
    target = outcome.pairs[position]
    kept = [p for k, p in enumerate(outcome.pairs) if k != position]
    loose = [
        Unpaired("before", befores[target.before_number]),
        Unpaired("after", afters[target.after_number]),
        *_loose_segments(outcome),
    ]
    return _regroup(outcome, kept, loose)


def sendable_pair(pair: PairResult) -> bool:
    """全部の組をまとめて送るときに、本文に入れてよい組か(ADR-0152 決定4)。

    比較が失敗した組・選択待ちの組・機種の自動の判定が食い違った組(ADR-0151)・機種に Si-R を指定して中身が別の機種に見える組
    (REQ-222)は送らない。1組の比較で送らない組と同じ。
    """
    outcome = pair.outcome
    if outcome.status in ("error", "needs_selection"):
        return False
    if outcome.auto_platform is not None and outcome.auto_platform.conflict:
        return False
    if outcome.kind == "config" and pair.request.platform == "fujitsu_sir":
        texts = _selected_texts(outcome)
        if texts is None or looks_not_sir(*texts):
            return False
    return True


def _selected_texts(outcome: CompareOutcome) -> tuple[str, str] | None:
    segmentation = outcome.segmentation
    if segmentation is None:
        return None
    before, after = segmentation["before"].selected, segmentation["after"].selected
    if before is None or after is None:
        return None
    return before.text, after.text


def _whole_input(pair: PairResult) -> LlmInputs | None:
    """まとめて送れる組なら llm_inputs を、送れなければ None を返す(whole_sections と whole_candidates の共通の判定)。"""
    if not sendable_pair(pair):
        return None
    return llm_inputs(pair.request, pair.outcome)


def whole_candidates(outcome: MultiCompareOutcome) -> list[int]:
    """全部の組をまとめて送るときに、送れる組の pairs の位置(whole_sections が組に入れる組。REQ-265)。"""
    return [position for position, pair in enumerate(outcome.pairs) if _whole_input(pair) is not None]


def whole_sections(
    outcome: MultiCompareOutcome, chosen: Collection[int] | None = None
) -> tuple[list[int], list[WholeSection], list[str | None]]:
    """全部の組をまとめて送るときの組(ADR-0152 決定3・決定9)。返すのは (組の pairs の位置, 組, 差分の無かった組のコマンド)。

    行は core の _llm_analysis が analyze に渡すのと同じ(llm_inputs。区間の本文は outcome.segmentation の selected.text)。
    送らない組(sendable_pair が偽)は、組にも差分の無かったコマンドにも入れない。差分の無かったコマンドは状態が「差分なし」の組だけ。
    chosen(pairs の位置の集まり)を渡したときは、その中の組だけを入れる(REQ-265)。None なら全部(今と同じ)。
    """
    positions: list[int] = []
    sections: list[WholeSection] = []
    unchanged: list[str | None] = []
    for position, pair in enumerate(outcome.pairs):
        if chosen is not None and position not in chosen:
            continue
        inputs = _whole_input(pair)
        if inputs is None:
            continue
        if pair.outcome.status == "no_difference":
            unchanged.append(pair.command)
        kind = pair.outcome.kind if pair.outcome.kind in ("config", "show") else None
        positions.append(position)
        sections.append(
            WholeSection(
                command=pair.command,
                kind=kind,
                structured=pair.outcome.structured,
                raw=pair.outcome.raw,
                show=pair.outcome.show,
                before_lines=inputs.before_lines,
                after_lines=inputs.after_lines,
                before_source=inputs.before_source,
                after_source=inputs.after_source,
            )
        )
    return positions, sections, unchanged


def analyze_all(
    outcome: MultiCompareOutcome,
    llm: LlmRequest,
    *,
    max_total_diffs: int,
    rules_of: Callable[[PairResult], Any] | None = None,
    chosen: Collection[int] | None = None,
) -> MultiCompareOutcome:
    """全部の組をまとめて1回で AI に送り、答えを outcome.llm に入れて返す(比較の窓だけ。ADR-0152 決定3)。

    比較の結果(組・状態)は変えない。llm の dry_run・client・承認の照合は analyze と同じに扱う(analyze_whole)。
    rules_of は依頼書の形どおり受け取るが使わない(正規化のルールは組の要求から決まる。llm_inputs と同じ)。
    chosen は送る組の pairs の位置(REQ-265)。None なら outcome.llm_chosen(送る組を持たせた結果。それも None ならすべて)。
    全体の上限もその組だけで数え、使った組は結果の llm_chosen に入れる。
    """
    chosen = chosen if chosen is not None else outcome.llm_chosen
    positions, sections, unchanged = whole_sections(outcome, chosen)
    analysis, sent = analyze_whole_sent(llm, sections, max_total_diffs=max_total_diffs, unchanged=unchanged)
    return dataclasses.replace(
        outcome,
        llm=analysis,
        llm_pairs=tuple(positions[index] for index in sent),
        llm_chosen=None if chosen is None else tuple(sorted(chosen)),
    )


# 全体を AI に送ったとき、全体の答えを入れた組のほかの組の llm(ADR-0152 決定7)
IN_WHOLE = LlmAnalysis(status="skipped", reason="in_whole")


def whole_holder(outcome: MultiCompareOutcome) -> int | None:
    """全体の答えを入れる組の位置(本文に最初に入れた組)。全体を AI に送っていなければ None。"""
    llm = outcome.llm
    if llm is None or not llm.transmitted or not outcome.llm_pairs:
        return None
    return outcome.llm_pairs[0]


def whole_pair_results(outcome: MultiCompareOutcome) -> list[PairResult]:
    """保存と出力に渡す組の結果(ADR-0152 決定7)。1行が1組の形のまま。

    全体を AI に送ったときは、本文に最初に入れた組の outcome.llm に全体の答えを入れ、ほかの組は in_whole にする。
    送っていなければ今のまま(組ごとの outcome.llm)。比較の窓が使う。一括比較のまとめて比べた組の保存と出力は job_pair_results
    (送ったときはこの関数と同じ。batch_io・cli は screen_model を読まないので core に置く)。
    """
    holder = whole_holder(outcome)
    if holder is None:
        return list(outcome.pairs)
    return [
        dataclasses.replace(p, outcome=dataclasses.replace(p.outcome, llm=outcome.llm if k == holder else IN_WHOLE))
        for k, p in enumerate(outcome.pairs)
    ]


def job_pair_results(outcome: MultiCompareOutcome) -> list[PairResult]:
    """一括比較のまとめて比べた組の、保存と出力に渡すコマンドの組ごとの結果(REQ-241 §4。1 行が 1 組の形のまま)。

    AI の要求が無ければ(outcome.llm が None)今の組のまま。実際に送ったときは whole_pair_results と同じ
    (最初の行に全体の答え、ほかの行は in_whole)。送らなかったとき(確認だけ・送らなかった・上限超え・中止・連続失敗で
    呼ばなかった・接続が使えないなど)は、最初の行に送る文を含む全体の結果を入れ、ほかの行には同じ理由だけを入れて送る文は入れない
    (送る文が 1 回だけ出る。決定者の判断)。最初の行は送る文に最初に入れたコマンドの組(llm_pairs[0])、送る文が無ければ先頭の組。
    比較の窓は whole_pair_results のまま使う(この関数は一括比較だけ)。
    """
    llm = outcome.llm
    if llm is None:
        return list(outcome.pairs)
    if whole_holder(outcome) is not None:
        return whole_pair_results(outcome)
    first = outcome.llm_pairs[0] if outcome.llm_pairs else 0
    others = dataclasses.replace(llm, payload=None)
    return [
        dataclasses.replace(p, outcome=dataclasses.replace(p.outcome, llm=llm if k == first else others))
        for k, p in enumerate(outcome.pairs)
    ]


def attach_whole_answer(outcome: MultiCompareOutcome, first: LlmAnalysis) -> MultiCompareOutcome | None:
    """比較の窓で比べ直したまとめての比較に、一括比較で送った全体の答え first を付ける(ADR-0170 を全体に広げる。REQ-241 §5)。

    比べ直した結果から whole_sections で組を作り、same_sent_whole_body(全体の本文の照合)が合うときだけ、
    llm に first、llm_pairs に本文に入れた組の pairs の位置を入れて返す。合わなければ None(付けない)。照合は緩めない。
    """
    positions, sections, unchanged = whole_sections(outcome)
    sent = same_sent_whole_body(sections=sections, unchanged=unchanged, first=first)
    if sent is None:
        return None
    return dataclasses.replace(outcome, llm=first, llm_pairs=tuple(positions[index] for index in sent))


class HasOutcome(Protocol):
    """全体の status を決めるのに読む1件(組の結果か、開いた保存の1行)。"""

    @property
    def outcome(self) -> CompareOutcome: ...


def overall_status(pairs: Sequence[HasOutcome], unpaired: Sequence[Unpaired]) -> MultiStatus:
    if not pairs and not unpaired:
        return "error"
    if any(p.outcome.status in ("error", "needs_selection") for p in pairs):
        return "error"
    if unpaired or any(p.outcome.status == "difference" for p in pairs):
        return "difference"
    return "no_difference"


def _run_one(
    request: CompareRequest, llm: LlmRequest | None, on_progress: Callable[[str], None] | None
) -> CompareOutcome:
    stages: list[str] = []

    def progress(stage: str) -> None:
        stages.append(stage)
        if on_progress is not None:
            on_progress(stage)

    # llm が無いときは run_compare に渡さない(今までどおり。run_batch と同じ)
    options: dict[str, Any] = {"llm": llm} if llm is not None else {}
    try:
        return run_compare(request, on_progress=progress, **options)
    except Exception as exc:  # noqa: BLE001 - 1組の失敗で全体を止めない(REQ-121 §3)
        stage = stages[-1] if stages else "split"
        detail: dict[str, Any] = {"stage": stage, "error_type": type(exc).__name__, "error": str(exc)}
        message = _T["unexpected"].format(error_type=type(exc).__name__, error=exc)
        return CompareOutcome(
            status="error", mode=request.mode, error=CompareError("unexpected_error", stage, message, detail)
        )
