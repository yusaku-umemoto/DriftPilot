"""job の入力の読み込み(ADR-0015 決定5)。core.run_batch に prepare として渡す。"""

from collections.abc import Callable, Mapping
from pathlib import Path

from driftpilot.core import CompareRequest, Job, JobInputError, request_for
from driftpilot.core.types import SIDES
from driftpilot.diff_engine import NormalizationRule
from driftpilot.messages import unreadable_utf8
from driftpilot.segmentation.input_forms import InputDecodeError, InputNote, read_config_input

_LABEL = {"before": "変更前", "after": "変更後"}


def make_prepare(rules: Mapping[Path, list[NormalizationRule]]) -> Callable[[Job], CompareRequest]:
    """入力を CLI と同じ入口(read_config_input。ADR-0138 決定1)で読んで要求を作る関数を返す。rules は load_job_rules で読んだもの。

    同じファイルを両側に書いた job(同じファイルの2区間)も、側ごとに読む。読めない側があれば JobInputError
    (読めた側のテキストは request に残す)。
    """

    def prepare(job: Job) -> CompareRequest:
        texts = {side: "" for side in SIDES}
        notes: dict[str, tuple[InputNote, ...]] = {side: () for side in SIDES}
        failed = []
        messages = []
        for side in SIDES:
            path = getattr(job, side).path
            try:
                read = read_config_input(path)
                texts[side], notes[side] = read.text, read.notes
            except InputDecodeError as exc:
                failed.append(side)
                messages.append(f"{_LABEL[side]}: {unreadable_utf8(path, exc.hints)}")
            except OSError as exc:
                failed.append(side)
                messages.append(f"{_LABEL[side]}: {path}: 読めません ({exc.strerror or exc})")
        request = request_for(
            job,
            texts["before"],
            texts["after"],
            rules[job.rules_path] if job.rules_path else None,
            before_notes=notes["before"],
            after_notes=notes["after"],
        )
        if failed:
            raise JobInputError(failed, " / ".join(messages), request)
        return request

    return prepare
