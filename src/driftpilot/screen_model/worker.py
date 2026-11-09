"""比較をワーカースレッドで実行する(ADR-0022 決定7)。wx を読まない。

進捗と中断は core の口(run_compare の on_progress / should_cancel。ADR-0015 決定6)をそのまま使う。GUI 用の口は作らない。
結果・中断・想定外の例外は、注入した post で画面側へ返す。画面は wx.CallAfter を渡し(ウィジェットに触るのはメインスレッドだけ)、
テストは同期の関数を渡す。
"""

import threading
from collections.abc import Callable
from typing import Any

from driftpilot.core import CompareCancelled, CompareOutcome, CompareRequest, LlmRequest, run_compare
from driftpilot.llm_analyzer import ConnectionCheckResult, LLMClient, send_connection_check

Post = Callable[..., Any]


class RunGate:
    """比較画面と一括比較画面で、同時に走らせないための門(ADR-0029 決定9)。メインスレッドからだけ触る。

    走らせる画面が acquire で門を取り、終わったら release で返す。門が変わるたびに、登録した画面へ知らせる
    (相手の画面の実行ボタンを押せなくする・押せるように戻す)。
    """

    def __init__(self) -> None:
        self._owner: object | None = None
        self._listeners: list[Callable[[], None]] = []

    @property
    def owner(self) -> object | None:
        return self._owner

    def free_for(self, who: object) -> bool:
        """who が走らせてよいか(誰も走らせていないか、who 自身が走らせている)。"""
        return self._owner is None or self._owner is who

    def acquire(self, who: object) -> bool:
        if not self.free_for(who):
            return False
        changed = self._owner is None
        self._owner = who
        if changed:
            self._notify()
        return True

    def release(self, who: object) -> None:
        if self._owner is who:
            self._owner = None
            self._notify()

    def subscribe(self, listener: Callable[[], None]) -> None:
        self._listeners.append(listener)

    def unsubscribe(self, listener: Callable[[], None]) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()


class CompareWorker:
    """1回の比較。start() で始め、cancel() で中断を頼む(工程の境目で止まる。LLM の通信中は止まらない)。"""

    def __init__(
        self,
        request: CompareRequest,
        *,
        llm: LlmRequest | None,
        post: Post,
        on_progress: Callable[[str], None],
        on_done: Callable[[CompareOutcome], None],
        on_cancelled: Callable[[CompareCancelled], None],
        on_failed: Callable[[BaseException], None],
        runner: Callable[..., CompareOutcome] = run_compare,
    ) -> None:
        self.request = request
        self.llm = llm
        self._post = post
        self._on_progress = on_progress
        self._on_done = on_done
        self._on_cancelled = on_cancelled
        self._on_failed = on_failed
        self._runner = runner
        self._cancel = threading.Event()
        self._thread = threading.Thread(target=self._run, name="driftpilot-compare", daemon=True)

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
            outcome = self._runner(
                self.request,
                llm=self.llm,
                on_progress=lambda stage: self._post(self._on_progress, stage),
                should_cancel=self._cancel.is_set,
            )
        except CompareCancelled as exc:
            self._post(self._on_cancelled, exc)
        except Exception as exc:  # noqa: BLE001 - スレッドの中で消さずに画面へ返す
            self._post(self._on_failed, exc)
        else:
            self._post(self._on_done, outcome)


class ConnectionCheckWorker:
    """設定画面の「AI 接続テスト」(REQ-35 §3)。1回だけ送り、結果の分類を post で返す。中断の口は無い(1回の通信だけ)。"""

    def __init__(
        self,
        client: LLMClient,
        *,
        post: Post,
        on_done: Callable[[ConnectionCheckResult], None],
        on_failed: Callable[[BaseException], None],
        sender: Callable[[LLMClient], ConnectionCheckResult] = send_connection_check,
    ) -> None:
        self._client = client
        self._post = post
        self._on_done = on_done
        self._on_failed = on_failed
        self._sender = sender
        self._thread = threading.Thread(target=self._run, name="driftpilot-llm-check", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def _run(self) -> None:
        try:
            result = self._sender(self._client)
        except Exception as exc:  # noqa: BLE001 - スレッドの中で消さずに画面へ返す
            self._post(self._on_failed, exc)
        else:
            self._post(self._on_done, result)
