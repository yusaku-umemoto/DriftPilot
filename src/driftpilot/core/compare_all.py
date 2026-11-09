"""複数のコマンドのログをまとめて比較する(ADR-0084 決定1・2、REQ-121 §3)。

両側を区間に分けてコマンドで組にし(segmentation.pairing)、組ごとに今の run_compare を区間の番号を指定して呼ぶ。
1組の比較の中身と結果の形は変えない。種類はコマンドから決める(設定の語を含めば config、ほかは show)。
コマンドが分からない組だけ、利用者の kind・platform をそのまま使う(今の compare に区間の番号を指定したのと同じ要求)。

全体の status は error・difference・no_difference の3つ。選択待ち(ambiguous_format)の組は error に数える
(比べていない組を、差分なしや差分ありと取り違えさせないため)。組も相手なしも無いときも error にする。

両側の本文が同じで同じコマンドが2回以上あれば、1つのログの中で出てきた順に2つずつ組にする(ADR-0152 決定2。pair_within)。
組にならなかった区間は unpaired に入れるが、全体の status には数えない(single_log)。

LLM 解析(REQ-122 §4・§5 / ADR-0084 決定6)は、CLI の --all-segments --llm では一括比較と同じ2段(compare_all の llm・approved)。
組ごとの要求は一括比較の _LlmPerJob を、組の番号の文字列を id にして使う(承認した内容だけを送る・承認していない組は送らない・
続けて失敗したら残りでは呼ばない・比較が失敗した組には送らない)。要約と承認は core.batch_llm の関数に PairResult をそのまま渡す。
比較の窓は analyze_all で全部の組を1つの文にして1回で送る(ADR-0152 決定3。記号の表は全部の組で1つ)。
一括比較のまとめて比べた組も、組ごとに analyze_all で同じ文を作って1回で送る(ADR-0178 決定4・REQ-241。core.batch._run_one)。
"""

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol

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
from driftpilot.segmentation import Segment, SegmentPair, has_repeats, pair_segments, pair_within, split_log

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
    llm_pairs はその本文に入れた組の pairs の位置(保存には入れない)。CLI の --all-segments --llm は今のまま組ごとの outcome.llm に入る。
    """

    status: MultiStatus
    pairs: list[PairResult]
    unpaired: list[Unpaired]
    single_log: bool = False
    llm: LlmAnalysis | None = None
    llm_pairs: tuple[int, ...] = ()


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
) -> MultiCompareOutcome:
    """全部の組を比べる。should_cancel は組の境目で見て、真なら CompareCancelled("pair") を送出する。

    1組の失敗で止めない。run_compare が例外を出した組は status=error(unexpected_error)の結果にする。
    on_progress には各組の run_compare の工程をそのまま流す。llm は入口が組み立てた LLM 解析の要求(全部の組で使い回す)。
    approved は送る段で使う「組の番号の文字列 → 確認で承認した送信内容」(run_batch と同じ)。どれも省けば今と同じ動き。
    """
    before = split_log(request.before.text, prompt=request.prompt).segments
    after = split_log(request.after.text, prompt=request.prompt).segments
    results: list[PairResult] = []
    unpaired: list[Unpaired] = []
    llm_per_pair = _LlmPerJob(llm, approved)
    # 両側の本文が同じで、同じコマンドが2回以上あれば、1つのログの中で組にする(ADR-0152 決定2。パスは見ない)
    single_log = request.before.text == request.after.text and has_repeats(before)
    pairs = pair_within(before) if single_log else pair_segments(before, after)
    for pair in pairs:
        if pair.before is None or pair.after is None:
            side: SideName = "before" if pair.after is None else "after"
            unpaired.append(Unpaired(side, pair.before if pair.before is not None else pair.after))
            continue
        if should_cancel is not None and should_cancel():
            raise CompareCancelled("pair")
        index = len(results) + 1
        pair_req = pair_request(request, pair)
        outcome = _run_one(pair_req, llm_per_pair.request(str(index)), on_progress)
        llm_per_pair.record(outcome.llm)
        results.append(PairResult(pair.before.command, pair.before.number, pair.after.number, pair_req, outcome, index))
    if single_log:
        # 1回だけのコマンドとコマンド不明の区間は比べていないので、全体の状態に数えない(terminal length 0 などで差分ありにしない)
        return MultiCompareOutcome(overall_status(results, []), results, unpaired, single_log=True)
    return MultiCompareOutcome(overall_status(results, unpaired), results, unpaired)


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


def whole_sections(outcome: MultiCompareOutcome) -> tuple[list[int], list[WholeSection], list[str | None]]:
    """全部の組をまとめて送るときの組(ADR-0152 決定3・決定9)。返すのは (組の pairs の位置, 組, 差分の無かった組のコマンド)。

    行は core の _llm_analysis が analyze に渡すのと同じ(llm_inputs。区間の本文は outcome.segmentation の selected.text)。
    送らない組(sendable_pair が偽)は、組にも差分の無かったコマンドにも入れない。差分の無かったコマンドは状態が「差分なし」の組だけ。
    """
    positions: list[int] = []
    sections: list[WholeSection] = []
    unchanged: list[str | None] = []
    for position, pair in enumerate(outcome.pairs):
        if not sendable_pair(pair):
            continue
        inputs = llm_inputs(pair.request, pair.outcome)
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
) -> MultiCompareOutcome:
    """全部の組をまとめて1回で AI に送り、答えを outcome.llm に入れて返す(比較の窓だけ。ADR-0152 決定3)。

    比較の結果(組・状態)は変えない。llm の dry_run・client・承認の照合は analyze と同じに扱う(analyze_whole)。
    rules_of は依頼書の形どおり受け取るが使わない(正規化のルールは組の要求から決まる。llm_inputs と同じ)。
    """
    positions, sections, unchanged = whole_sections(outcome)
    analysis, sent = analyze_whole_sent(llm, sections, max_total_diffs=max_total_diffs, unchanged=unchanged)
    return dataclasses.replace(outcome, llm=analysis, llm_pairs=tuple(positions[index] for index in sent))


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
        message = f"予期しないエラーで比較できませんでした: {type(exc).__name__}: {exc}"
        return CompareOutcome(
            status="error", mode=request.mode, error=CompareError("unexpected_error", stage, message, detail)
        )
