"""一括比較の LLM 解析の段取り(ADR-0031 決定2〜4)。ファイルを開かない。文面を持たない(数と形だけ)。

1段目: 全 job を run_batch で比較し、伏せる処理まで行って送らない(LlmRequest(dry_run=True))。
確認: 1段目の結果から要約(BatchLlmPreview)を作る。全体の上限はここで判定する(超えたら1件も送らない)。
2段目: 承認された job だけを、承認した送信内容(approved_payloads)と一緒に run_batch へ渡して送る。
最後に final_results で1段目と2段目の結果を job の順に並べ、送らなかった job の llm に理由を入れる。

要約と承認の関数は、job.id・job.label・job.index と outcome を持つ結果(ResultLike)を受け取る。一括比較の JobResult と、
まとめての比較(core.compare_all。REQ-122)の組の結果 PairResult の両方に同じ関数を使う。
"""

import dataclasses
import re
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, TypeVar

from driftpilot import i18n
from driftpilot.core.batch import STATUSES, BatchSummary, overall_status
from driftpilot.core.types import CompareOutcome, Status
from driftpilot.llm_analyzer import LlmAnalysis, LlmReason, UnsentShape

# 送る差分の合計の上限の既定値(ADR-0031 決定4)。1組の上限(既定 100)の10組分。
# 値の元は driftpilot.llm_constants(画面のラベル「全体（既定 1,000 件）」も同じ値から作る。ADR-0167)
from driftpilot.llm_constants import DEFAULT_MAX_TOTAL_DIFFS

# 送信内容の中の伏せた記号。指示文の `<secret:N>` は数字ではないので数えない
# 利用者に見える文。言語に合わせて読み込むときに 1 回だけ選ぶ(messages は読まない決まりのため、ここに持つ)
_TEXTS_JA = {"max_total_diffs": "max_total_diffs は 1 以上の整数にしてください: {value}"}
_TEXTS_EN = {"max_total_diffs": "max_total_diffs must be an integer of 1 or more: {value}"}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

_SECRET = re.compile(r"<secret:\d+>")


class JobLike(Protocol):
    """結果の見出し(一括比較の Job か、まとめての比較の組の PairJob)。"""

    @property
    def id(self) -> str: ...

    @property
    def label(self) -> str | None: ...

    @property
    def index(self) -> int: ...


class ResultLike(Protocol):
    """1件の結果(一括比較の JobResult か、まとめての比較の PairResult)。"""

    @property
    def job(self) -> JobLike: ...

    @property
    def outcome(self) -> CompareOutcome: ...


R = TypeVar("R", bound=ResultLike)


@dataclass(frozen=True)
class PreviewJob:
    """確認の画面の1行(送る内容がある job)。payload は送る内容(マスク後)の全文。"""

    job_id: str
    label: str | None
    index: int
    sent_diffs: int
    masked: int
    unsent_lines: int
    truncated: bool
    payload: str


@dataclass(frozen=True)
class BatchLlmPreview:
    """1段目の要約。jobs は送る内容がある job だけ(差分が無い・送れる行が無い・比較まで進まなかった job は入らない)。

    unsent_shapes は送らなかった行の形を全 job で合算したもの(値は含まない。ADR-0019 決定5・6)。
    """

    jobs: tuple[PreviewJob, ...]
    total_diffs: int
    max_total_diffs: int
    truncated_jobs: int
    masked: int
    unsent_lines: int
    unsent_shapes: tuple[UnsentShape, ...]
    masking_fingerprint: str | None

    @property
    def over_limit(self) -> bool:
        return self.total_diffs > self.max_total_diffs


def _has_payload(result: ResultLike) -> bool:
    llm = result.outcome.llm
    return llm is not None and llm.reason == "dry_run" and llm.payload is not None


def _is_multi(result: ResultLike) -> bool:
    """一括比較のまとめて比べた組(JobResult.multi がある)か。PairResult と 1 区間の組は偽。"""
    return getattr(result, "multi", None) is not None


def is_sendable(result: ResultLike) -> bool:
    """1段目で送る内容ができ、比較が失敗していない job か。

    失敗した組は確認の一覧に出さず、送らない(REQ-27 §A-8b)。同じ行の重複で構造化比較だけが失敗した組のように、
    単純比較の差分から送る内容ができていても送らない(比較の結果が「失敗」の組に注釈を付けない)。
    まとめて比べた組は、全体の送る文があれば送れる組(組の全体の状態が「失敗」でも同じ。本文に入れるコマンドの組は
    sendable_pair が決め、失敗したコマンドの組は入らない。比較の窓のまとめての比較と同じ入力で同じ文にする。REQ-241 §2)。
    """
    if _is_multi(result):
        return _has_payload(result)
    return _has_payload(result) and result.outcome.status != "error"


def is_failed_with_payload(result: ResultLike) -> bool:
    """送る内容はできたが、比較が失敗したため送らない job。まとめて比べた組は当たらない(is_sendable を見る)。"""
    if _is_multi(result):
        return False
    return _has_payload(result) and result.outcome.status == "error"


def masked_count(payload: str) -> int:
    """送信内容の中で `<secret:N>` に置き換えた数。"""
    return len(_SECRET.findall(payload))


def _merge_shapes(analyses: Iterable[LlmAnalysis]) -> tuple[UnsentShape, ...]:
    totals: dict[tuple[str, str, str], list[int]] = {}
    for llm in analyses:
        if llm.unsent is None:
            continue
        for s in llm.unsent.shapes:
            count = totals.setdefault((s.kind, s.scope, s.shape), [0, 0])
            count[0] += s.count
            count[1] += s.lines
    shapes = [UnsentShape(kind, scope, shape, c, n) for (kind, scope, shape), (c, n) in totals.items()]  # type: ignore[arg-type]
    return tuple(sorted(shapes, key=lambda s: (-s.count, s.scope, s.shape)))


def llm_preview(results: Sequence[ResultLike], *, max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS) -> BatchLlmPreview:
    """1段目の結果から確認の要約を作る。送らなかった行は、送る内容が無かった job の分も数える。"""
    if type(max_total_diffs) is not int or max_total_diffs < 1:
        raise ValueError(_T["max_total_diffs"].format(value=repr(max_total_diffs)))
    analyses = [r.outcome.llm for r in results if r.outcome.llm is not None]
    jobs = []
    for r in results:
        if not is_sendable(r):
            continue
        llm = r.outcome.llm
        assert llm is not None and llm.payload is not None
        jobs.append(
            PreviewJob(
                job_id=r.job.id,
                label=r.job.label,
                index=r.job.index,
                sent_diffs=llm.sent_diffs or 0,
                masked=masked_count(llm.payload),
                unsent_lines=llm.unsent.lines if llm.unsent is not None else 0,
                truncated=llm.truncated,
                payload=llm.payload,
            )
        )
    fingerprints = {llm.masking_fingerprint for llm in analyses if llm.masking_fingerprint}
    return BatchLlmPreview(
        jobs=tuple(jobs),
        total_diffs=sum(j.sent_diffs for j in jobs),
        max_total_diffs=max_total_diffs,
        truncated_jobs=sum(1 for j in jobs if j.truncated),
        masked=sum(j.masked for j in jobs),
        unsent_lines=sum(llm.unsent.lines for llm in analyses if llm.unsent is not None),
        unsent_shapes=_merge_shapes(analyses),
        masking_fingerprint=fingerprints.pop() if len(fingerprints) == 1 else None,
    )


def approved_payloads(preview: BatchLlmPreview, checked: Collection[str]) -> dict[str, str]:
    """2段目に渡す「job の id → 承認した送信内容」。確認で残した job だけ。上限を超えていれば空(1件も送らない)。"""
    if preview.over_limit:
        return {}
    return {j.job_id: j.payload for j in preview.jobs if j.job_id in checked}


def withhold(result: R, reason: LlmReason) -> R:
    """送らなかった job の llm に理由を入れ直す(送っていない)。比較の結果と status は変えない。

    まとめて比べた組は、全体の解析(multi.llm)も同じ理由に入れ直す(outcome.llm と食い違わせない。REQ-241 §3)。
    """
    llm = result.outcome.llm
    if llm is None:
        return result
    analysis = dataclasses.replace(llm, status="skipped", reason=reason, transmitted=False)
    changed = dataclasses.replace(result, outcome=dataclasses.replace(result.outcome, llm=analysis))
    multi = getattr(result, "multi", None)
    if multi is not None:
        changed = dataclasses.replace(changed, multi=dataclasses.replace(multi, llm=analysis))
    return changed


def final_results(
    first: Sequence[R],
    second: Sequence[R] = (),
    *,
    approved: Collection[str] = (),
    reason: LlmReason = "not_approved",
) -> list[R]:
    """1段目の job の順に、最終の結果を並べる。

    2段目で実行した job は2段目の結果。送る内容があったのに2段目で実行しなかった job は、承認したもの(2段目を中止した)
    は cancelled、承認しなかったものは reason(既定は not_approved。上限超えなら over_total_limit)を llm に入れる。
    比較が失敗した job は、送る内容ができていても送らない(compare_failed)。
    送る内容が無かった job(差分なし・送れる行が無い・比較まで進まなかった)は1段目の結果のまま。
    """
    resent = {r.job.id: r for r in second}
    out = []
    for r in first:
        if r.job.id in resent:
            out.append(resent[r.job.id])
        elif is_sendable(r):
            out.append(withhold(r, "cancelled" if r.job.id in approved else reason))
        elif is_failed_with_payload(r):
            out.append(withhold(r, "compare_failed"))
        else:
            out.append(r)
    return out


def combined_summary(first: BatchSummary, results: Sequence[ResultLike], *, later: Sequence[BatchSummary] = ()) -> BatchSummary:
    """2段をまとめた件数と所要時間。件数と executed は最終の結果(書き出す行)から数える(status は LLM で変わらない)。"""
    counts: dict[Status, int] = dict.fromkeys(STATUSES, 0)
    for r in results:
        counts[r.outcome.status] += 1
    every = (first, *later)
    return BatchSummary(
        total=first.total,
        executed=len(results),
        counts=counts,
        elapsed_seconds=sum(s.elapsed_seconds for s in every),
        cancelled=any(s.cancelled for s in every),
        interrupted=any(s.interrupted for s in every),
        overall_status=overall_status(counts),
    )


def gave_up_count(results: Iterable[ResultLike]) -> int:
    """連続の失敗であきらめた job の数(文面を出すかの判定に使う)。"""
    return sum(1 for r in results if r.outcome.llm is not None and r.outcome.llm.reason == "gave_up")
