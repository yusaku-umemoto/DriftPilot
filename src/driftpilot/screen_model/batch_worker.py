"""一括比較をワーカースレッドで実行する(REQ-27 §A-3 / ADR-0022 決定7)。wx を読まない。

core.run_batch をそのまま呼ぶ。進捗は on_progress(BatchProgress)、1件ごとの結果は on_result で受け、
注入した post で画面へ返す(画面は wx.CallAfter、テストは同期の関数)。
中止は should_cancel(threading.Event)で、job の境界でだけ効く(REQ-15。run_compare には渡さない)。
"""

import threading
from collections.abc import Callable, Mapping, Sequence

from driftpilot.core import BatchProgress, BatchSummary, CompareRequest, Job, JobResult, LlmRequest, run_batch
from driftpilot.screen_model.worker import Post


class BatchWorker:
    """1回の一括実行(1段)。start() で始め、cancel() で中止を頼む(いま読んでいる1件が終わってから止まる)。"""

    def __init__(
        self,
        jobs: Sequence[Job],
        *,
        prepare: Callable[[Job], CompareRequest],
        llm: LlmRequest | None,
        approved: Mapping[str, str] | None = None,
        post: Post,
        on_progress: Callable[[BatchProgress], None],
        on_result: Callable[[JobResult], None],
        on_done: Callable[[BatchSummary], None],
        on_failed: Callable[[BaseException], None],
        runner: Callable[..., BatchSummary] = run_batch,
    ) -> None:
        self.jobs = list(jobs)
        self.llm = llm
        self.approved = approved
        self._prepare = prepare
        self._post = post
        self._on_progress = on_progress
        self._on_result = on_result
        self._on_done = on_done
        self._on_failed = on_failed
        self._runner = runner
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="driftpilot-batch", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel.is_set()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            summary = self._runner(
                self.jobs,
                prepare=self._prepare,
                on_result=lambda result: self._post(self._on_result, result),
                on_progress=lambda progress: self._post(self._on_progress, progress),
                should_cancel=self._cancel.is_set,
                llm=self.llm,
                approved=self.approved,
            )
        except Exception as exc:  # noqa: BLE001 - スレッドの中で消さずに画面へ返す
            self._post(self._on_failed, exc)
        else:
            self._post(self._on_done, summary)
