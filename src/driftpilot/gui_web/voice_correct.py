"""音声入力の「LLM で文脈を踏まえて直す」を 1 回送る部品(ADR-0233 決定4 / REQ-342)。作りは rule_ai.py にならう。

追加の質問のやり取りの外で、AI の接続(`LLMClient.ask`)へ文を 1 回だけ送る。窓の状態(門・やり取り・送信中の表示)には触らない。

- start(client, prompt, respond, still_valid): daemon のスレッドで `client.ask(prompt)` を 1 回呼ぶ。ストリーミングは使わない。
  30 秒の時計を別に動かし、返事と時計のうち先に着いたほうだけが respond を呼ぶ(後に着いたほうは捨てる)
- 返事は `<think>…</think>` を外す。文字でない・閉じていない `<think>`・空・例外は failed。返す前に still_valid() が偽なら cancelled
- cancel_all(): 動いているものすべてに cancelled を返し、後から届いた返事を捨てる
- respond に渡すのは {"content": 文字} か {"error": "failed"|"timeout"|"cancelled"}(1 つの start につき 1 回だけ)
- 失敗の文・返事・送った文は、ログにも標準エラーにも例外の文にも出さない(種類だけ)
"""

import threading
from collections.abc import Callable
from typing import Any

from driftpilot.llm_analyzer import LLMClient
from driftpilot.llm_analyzer.pair_suggestion import strip_think

CORRECT_TIMEOUT = 30.0  # 秒。テストは monkeypatch で短くする

ERROR_FAILED = "failed"
ERROR_TIMEOUT = "timeout"
ERROR_CANCELLED = "cancelled"

Respond = Callable[[dict[str, Any]], None]


class _Job:
    """送信 1 回分。返事・時計・取り消しのうち先に着いた 1 つだけが結果を決める。"""

    def __init__(self, respond: Respond) -> None:
        self._respond = respond
        self._lock = threading.Lock()
        self._done = False
        self.timer: threading.Timer | None = None

    def finish(self, result: dict[str, Any]) -> bool:
        """最初の 1 回だけ respond を呼ぶ(呼んだら True)。後から来たものは捨てる。"""
        with self._lock:
            if self._done:
                return False
            self._done = True
        timer = self.timer
        if timer is not None:
            timer.cancel()
        try:
            self._respond(result)
        except Exception:  # noqa: BLE001 - 返す先の失敗は捨てる(例外の中身は出さない)
            pass
        return True

    @property
    def done(self) -> bool:
        with self._lock:
            return self._done


class VoiceCorrector:
    """直しの送信を動かす入れ物。窓ごとに 1 つ持つ(CompareApi)。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: set[_Job] = set()

    def start(self, client: LLMClient, prompt: str, respond: Respond, still_valid: Callable[[], bool]) -> None:
        """送る文を 1 回だけ送る(daemon のスレッド)。respond は最大 1 回だけ、どれかのスレッドから呼ばれる。"""
        job = _Job(respond)

        def done(result: dict[str, Any]) -> None:
            job.finish(result)
            with self._lock:
                self._jobs.discard(job)

        with self._lock:
            self._jobs.add(job)
        timer = threading.Timer(CORRECT_TIMEOUT, lambda: done({"error": ERROR_TIMEOUT}))
        timer.daemon = True
        job.timer = timer
        timer.start()
        threading.Thread(
            target=self._run, args=(client, prompt, job, done, still_valid), name="driftpilot-voice-correct", daemon=True
        ).start()

    def cancel_all(self) -> None:
        """動いているものすべてに cancelled を返す(後から届いた返事は捨てる)。"""
        with self._lock:
            jobs = list(self._jobs)
            self._jobs.clear()
        for job in jobs:
            job.finish({"error": ERROR_CANCELLED})

    def running(self) -> int:
        """まだ結果を返していない送信の数(テストが読む)。"""
        with self._lock:
            return sum(1 for job in self._jobs if not job.done)

    @staticmethod
    def _run(
        client: LLMClient,
        prompt: str,
        job: _Job,
        done: Callable[[dict[str, Any]], None],
        still_valid: Callable[[], bool],
    ) -> None:
        result: dict[str, Any]
        # 確かめてから送るまでの間に取り消しが入っていたら、送らない(取り消しの返事は取り消した側がもう返している)
        try:
            early = job.done or not still_valid()
        except Exception:  # noqa: BLE001
            early = True
        if early:
            done({"error": ERROR_CANCELLED})
            return
        try:
            reply = client.ask(prompt)
        except Exception:  # noqa: BLE001 - 接続の実装の想定外の例外も、文面を出さずに failed にする
            result = {"error": ERROR_FAILED}
        else:
            content = strip_think(reply) if isinstance(reply, str) else None
            result = {"error": ERROR_FAILED} if content is None or not content.strip() else {"content": content}
        if job.done:
            return  # 時計か取り消しが先に決めた。後から届いた返事は捨てる
        try:
            valid = bool(still_valid())
        except Exception:  # noqa: BLE001
            valid = False
        if not valid:
            result = {"error": ERROR_CANCELLED}
        done(result)
