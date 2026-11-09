"""一括比較の出力(ADR-0016 決定4)。JSON Lines の1行と、サマリの JSON。

1行は REQ-14 の build_record の形そのもので、後ろに最上位の job を足す。一括比較のために別の結果の形を作らない。
まとめての比較(compare_all。REQ-122 §3)の保存も同じ1行の形で、job には組の番号・コマンドの文を入れる(pair_record)。
"""

import json
import os
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from driftpilot.core import BatchSummary, JobResult, PairResult, job_pair_results
from driftpilot.core.batch import STATUSES
from driftpilot.core.types import SIDES
from driftpilot.messages import multi_pair_name
from driftpilot.storage import JOB_KEY, build_record, default_filename

# まとめての比較の保存の拡張子(JSON Lines)
JSONL_SUFFIX = ".jsonl"

# サマリ JSON の版。JSON Lines の行(結果の SCHEMA_VERSION)やマニフェストの版とは別に持つ
SUMMARY_SCHEMA_VERSION = "1"


def job_record(result: JobResult, *, saved_at: datetime | None = None) -> dict[str, Any]:
    """1 job の1行。--save で個別に保存するファイルも同じ中身にする。"""
    job = result.job
    record = build_record(
        result.request,
        result.outcome,
        source_paths={side: getattr(job, side).path for side in SIDES},
        saved_at=saved_at,
        unread_sides=result.unread_sides,
    )
    record[JOB_KEY] = {
        "id": job.id,
        "label": job.label,
        "index": job.index,
        "selection_source": {side: getattr(job, side).selection_source for side in SIDES},
    }
    return record


def job_records(result: JobResult, *, saved_at: datetime | None = None) -> list[dict[str, Any]]:
    """1 job の行(ADR-0178 決定3)。1 区間の組は [job_record(result)] で今のまま。

    まとめて比べた組(result.multi がある)は、job_pair_results の順にコマンドの組ごとに 1 行(pair_record と同じ形)。
    job の id は `<組の id>/<コマンドの組の番号>`、label は `<組の名前>: <コマンド>`(コマンド不明は組の名前)、index は組の番号。
    相手なしの区間は入れない(比較の窓のまとめての比較の保存と同じ)。どの行も同じ saved_at にする。
    全体の AI の結果は、送ったときは最初の行に全体の答え・ほかの行は in_whole、送らなかったときは最初の行に送る文を含む全体の結果・
    ほかの行は同じ理由だけ(送る文なし。REQ-241 §4)。
    """
    multi = result.multi
    if multi is None:
        return [job_record(result, saved_at=saved_at)]
    if saved_at is None:
        saved_at = datetime.now(timezone.utc).astimezone()
    job = result.job
    group = job.label or job.id
    paths = {side: getattr(job, side).path for side in SIDES}
    records = []
    for pair in job_pair_results(multi):
        record = pair_record(pair, source_paths=paths, saved_at=saved_at)
        record[JOB_KEY] = {
            "id": f"{job.id}/{pair.index}",
            "label": multi_pair_name(group, pair.command),
            "index": job.index,
            "selection_source": dict.fromkeys(SIDES),
        }
        records.append(record)
    return records


def pair_record(
    pair: PairResult,
    *,
    source_paths: Mapping[str, str | os.PathLike[str] | None],
    saved_at: datetime | None = None,
) -> dict[str, Any]:
    """まとめての比較の組1つの1行(REQ-122 §3)。build_record の後ろに job を足す(一括比較の1行と同じ形)。

    id は組の番号の文字列、label は変更前の区間の元のコマンドの文(コマンド不明は null)。組は自動で作ったので、
    区間の指定の出どころ(selection_source)は両側とも null。1つの保存の行は、どれも同じ saved_at にする。
    """
    record = build_record(pair.request, pair.outcome, source_paths=source_paths, saved_at=saved_at)
    job = pair.job
    record[JOB_KEY] = {
        "id": job.id,
        "label": job.label,
        "index": job.index,
        "selection_source": dict.fromkeys(SIDES),
    }
    return record


def multi_filename(record: Mapping[str, Any]) -> str:
    """まとめての比較の保存の既定のファイル名。`<saved_at の YYYYMMDD-HHMMSS>_compare.jsonl`(1件の保存と同じ名前の付け方)。"""
    return str(Path(default_filename(record)).with_suffix(JSONL_SUFFIX))


class JsonLinesWriter:
    """1件ごとに1行を書いて、すぐ flush する(途中で止まっても、そこまでの行が読める)。サマリは書かない。"""

    def __init__(self, stream: TextIO) -> None:
        self.stream = stream
        self.count = 0

    def write(self, record: dict[str, Any]) -> None:
        self.stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.stream.flush()
        self.count += 1


class ResultsWriteError(Exception):
    """結果のファイルを書けない(既にある、保存先のフォルダが無いなど)。"""


def _write_error(target: Path, exc: OSError) -> ResultsWriteError:
    """書けなかったときの文(_write_new と write_new_bytes で同じ文にする)。"""
    if isinstance(exc, FileExistsError):
        return ResultsWriteError(f"{target}: 既にあります。上書きしません")
    if isinstance(exc, FileNotFoundError):
        return ResultsWriteError(f"{target}: 書き出し先のフォルダがありません(自動では作りません)")
    return ResultsWriteError(f"{target}: 書けません ({exc.strerror or exc})")


def _write_new(path: str | os.PathLike[str], write: Any) -> Path:
    """新しいファイルを UTF-8・`\\n` で書く。既にあれば上書きしない。親フォルダは自動では作らない。"""
    target = Path(path)
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as fh:
            write(fh)
    except OSError as exc:
        raise _write_error(target, exc) from exc
    return target


def write_new_bytes(data: bytes, path: str | os.PathLike[str]) -> Path:
    """バイト列を新しいファイルに書く(HTML・CSV の出力。ADR-0156 決定1)。既にあれば上書きしない(モード "xb")。

    親フォルダは自動では作らない。失敗の文は _write_new と同じ ResultsWriteError。
    """
    target = Path(path)
    try:
        with open(target, "xb") as fh:
            fh.write(data)
    except OSError as exc:
        raise _write_error(target, exc) from exc
    return target


def write_job_lines(results: Iterable[JobResult], path: str | os.PathLike[str]) -> Path:
    """結果を1件1行(CLI の --output と同じ JSON Lines)で書く。LLM の回答も各行の result.llm に入る。上書きしない。

    まとめて比べた組は、コマンドの組ごとの行(job_records。ADR-0178 決定3)。
    件数だけのファイル(--summary)は書かない(終了コードを持たない GUI の保存。REQ-27 §A-4c)。
    """
    return write_records((record for result in results for record in job_records(result)), path)


def write_records(records: Iterable[dict[str, Any]], path: str | os.PathLike[str]) -> Path:
    """組み立てた行を1件1行の JSON Lines で書く。上書きしない(既にあれば ResultsWriteError)。"""

    def write(fh: TextIO) -> None:
        writer = JsonLinesWriter(fh)
        for record in records:
            writer.write(record)

    return _write_new(path, write)


def write_text_file(text: str, path: str | os.PathLike[str]) -> Path:
    """人が読むテキスト(送信内容の全文など)を書く。上書きしない。"""
    return _write_new(path, lambda fh: fh.write(text))


def summary_dict(
    summary: BatchSummary | None,
    *,
    exit_code: int,
    manifest_error: str | None = None,
    saved: dict[str, Any] | None = None,
    unpaired: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """--summary PATH の中身。summary が None なのは、マニフェストのエラーで1件も実行しなかったとき。

    exit_code を件数の内訳と並べて持つ(なぜその終了コードになったかを1つで辿れるように)。
    """
    counts = dict(summary.counts) if summary else dict.fromkeys(STATUSES, 0)
    return {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "total": summary.total if summary else 0,
        "executed": summary.executed if summary else 0,
        "cancelled": summary.cancelled if summary else False,
        "interrupted": summary.interrupted if summary else False,
        "counts": counts,
        "elapsed_seconds": round(summary.elapsed_seconds, 3) if summary else 0.0,
        "exit_code": exit_code,
        "manifest_error": manifest_error,
        "saved": saved,
        "unpaired": unpaired,
    }
