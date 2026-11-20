"""音声入力の画面の状態(ADR-0233 決定2・3・5・6 / REQ-341)。比較の窓に 1 つ置き、切り離した AI の窓と共用する。

探す(series/discovery)と子プロセス(series/voice)を束ね、ボタンの状態を持つ。窓とは知らせの関数(deliver)でつなぐ。

状態(state): hidden(探している・使えない・切)→ preparing(押せない)→ ready で idle → start で recording →
stop で transcribing → text・text_failed・cancelled で idle。error・思わず終わったときは failed(押せない)。

- ロックの順: 比較の窓のロック → このロック(このロックの中から比較の窓は呼ばない)。窓への知らせはこのロックの外で送る
  (知らせは 1 本の daemon のスレッドが順に送る。比較の窓のロックの中で呼んでも窓を待たない)
- 画面への知らせ: voice.changed(状態の view。両方の窓)・voice.level(rms。両方の窓)・
  voice.text(入れる文字。ボタンを押した面の窓だけ。その窓が無ければ比較の窓。id・correcting が付くことがある)・
  voice.corrected(LLM で直した文字。押した面の窓だけ。REQ-342)
- LLM の直し(ADR-0233 決定4 / REQ-342): 設定が入で、CompareApi が条件を満たすと判定した(correction を渡した)ときだけ、
  stop に correct: true と context を付ける。stop に発話の id は付かないので、直後の text(correcting が真)の id を「頼んだ発話」に覚える。
  llm_request は、頼んだ発話の id のときだけ CompareApi へ渡し(確かめて送るのは CompareApi)、返事は respond(1 つの id に 1 回だけ)で
  llm_response として子に書く。text_corrected は頼んだ id のときだけ押した面の窓へ知らせる。取り消すとき(cancel_corrections・閉じる・
  子が替わる)は、まだ返していない id に cancelled を返す
- 話した文字(text)・PartsPilot の message の中身は、保存・記録・例外の文に出さない。画面へ渡す文は code だけで作る(決定5)
- 自分で止める(窓を閉じる・切にする・終える)ときは、録音中なら cancel を書き、子を止める(series/voice.VoiceProcess.stop)。
  止める処理は daemon のスレッドで動かす(shutdown だけが待つ)。思わず終わった子は起動し直さない
"""

import queue
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from typing import Any

from driftpilot import messages as msg
from driftpilot.llm_settings import PRESS_HOLD, PRESS_MODES, PRESS_TOGGLE, VoiceSettings
from driftpilot.series import discovery
from driftpilot.series.discovery import Discovery
from driftpilot.series.voice import Event, VoiceProcess

# 状態
HIDDEN = "hidden"
PREPARING = "preparing"
IDLE = "idle"
RECORDING = "recording"
TRANSCRIBING = "transcribing"
FAILED = "failed"

# 知らせの種類(static/js/voice.js が受ける)
CHANGED_EVENT = "voice.changed"
LEVEL_EVENT = "voice.level"
TEXT_EVENT = "voice.text"
CORRECTED_EVENT = "voice.corrected"

# 押す操作(画面から)
ACTION_TOGGLE = "toggle"
ACTION_DOWN = "down"
ACTION_UP = "up"
ACTIONS = (ACTION_TOGGLE, ACTION_DOWN, ACTION_UP)

PANE_COMPARE = "compare"
PANE_AI = "ai"
# PartsPilot の code ではない、DriftPilot が出す code
CODE_START_FAILED = "start_failed"
CODE_EXITED = "exited"
CODE_STOPPED = "stopped"

Deliver = Callable[[str, dict[str, Any]], None]  # (窓の宛先: "compare"・"ai"・"both"、知らせ)
# llm_request の扱い口(CompareApi): (押したときに決めた correction, 子が書いた messages, 返事の口)。返事の口には
# {"content": 文字} か {"error": "failed"|"timeout"|"cancelled"|"refused"} を 1 回だけ渡す
Respond = Callable[[dict[str, Any]], None]
CorrectHandler = Callable[[Any, list[Any], Respond], None]
Finder = Callable[[str], Discovery]
Factory = Callable[[tuple[str, ...], Callable[[Event], None]], VoiceProcess]

TARGET_BOTH = "both"
TARGET_PRESSED = "pressed"


def _system() -> str:
    if sys.platform.startswith("win"):
        return discovery.SYSTEM_WINDOWS
    return discovery.SYSTEM_MAC if sys.platform == "darwin" else discovery.SYSTEM_OTHER


def hidden_view() -> dict[str, Any]:
    """音声入力が無いとき(hub が無い・探している・使えない・切)の状態の view。ボタンは出さない。"""
    return {
        "revision": 0,
        "state": HIDDEN,
        "press": PRESS_TOGGLE,
        "can_press": False,
        "note": None,
        "title": msg.VOICE_BUTTON,
        "limit_note": None,
        "level_revision": 0,
        "correcting": False,
    }


@dataclass
class _Requested:
    """LLM で直すと頼んだ発話 1 つ(id は text の id)。correction は押したときに決めた持ち物(context・送り先など)。"""

    correction: Any
    pane: str
    requested: bool = False  # llm_request が届いて扱った(2 回目は not_requested)
    responded: bool = False  # llm_response を書いた(1 つの id に 1 回だけ)
    cancelled: bool = False  # 取り消した(text_corrected は画面へ知らせない)


def failure_note(code: str) -> str:
    """error・思わず終わったときの案内(決定5)。code から文を選ぶ(message の中身は使わない)。"""
    if code == "operation_not_supported":
        return msg.VOICE_NOT_SUPPORTED
    if code == "model_not_found":
        return msg.VOICE_MODEL_NOT_FOUND
    return msg.voice_stopped(code)


def text_failed_note(code: str, system: str) -> str | None:
    """text_failed の案内(決定5)。audio_too_short は何も出さない(None)。"""
    if code == "audio_too_short":
        return None
    if code == "microphone_unavailable":
        return msg.voice_mic_unavailable(system)
    if code == "microphone_silent":
        return msg.voice_mic_silent(system)
    return msg.voice_text_failed(code)


class VoiceHub:
    """音声入力の状態。CompareApi が bind で設定の読み方と知らせの届け方を渡す。

    finder は探す関数(省くと discovery.discover_default を呼ぶ時に取る)、factory は子の作り方、
    system は案内の文を選ぶ OS("windows"・"mac"・"other")、sync は知らせを呼んだ場で送る(テスト用)。
    """

    def __init__(
        self,
        *,
        finder: Finder | None = None,
        factory: Factory | None = None,
        system: str | None = None,
        sync: bool = False,
    ) -> None:
        self._finder = finder
        self._factory = factory or (lambda argv, on_event: VoiceProcess(argv, on_event))
        self._system = system or _system()
        self._sync = sync
        self._settings: Callable[[], VoiceSettings] = VoiceSettings
        self._deliver: Deliver | None = None
        self._lock = threading.Lock()
        self._state = HIDDEN
        self._press = PRESS_TOGGLE
        self._note: str | None = None
        self._limit_note: str | None = None
        self._pane = PANE_COMPARE
        self._revision = 0
        self._level_revision = 0
        self._generation = 0  # 探す・起動する仕事の世代(古い結果を捨てる)
        self._opened = False
        self._closed = False
        self._applied: tuple[bool, str] | None = None  # 起動に使った (enabled, cli)
        self._process: VoiceProcess | None = None
        self._discovery: Discovery | None = None
        self._threads: list[threading.Thread] = []
        self._outbox: queue.Queue[tuple[str, dict[str, Any]] | None] = queue.Queue()
        self._worker: threading.Thread | None = None
        # LLM の直し(REQ-342)
        self._correct_handler: CorrectHandler | None = None
        self._llm_correct = False  # 設定([voice] llm_correct)。open・settings_changed で読む
        self._record_correct = False  # 今の録音を始めたときの設定(次の録音から効く)
        self._awaiting: tuple[Any, str] | None = None  # stop(correct: true)を書いた (correction, 押した面)。次の text を待つ
        self._requested: dict[str | int, _Requested] = {}  # 頼んだ発話(text の id ごと)
        self._correcting: set[str | int] = set()  # 「LLM で直しています」を出している id

    # --- つなぐ ---

    def bind(
        self, settings: Callable[[], VoiceSettings], deliver: Deliver, correct: CorrectHandler | None = None
    ) -> None:
        """CompareApi が渡す: 設定の読み方と、窓への知らせの届け方(宛先と知らせ)・llm_request の扱い口(REQ-342)。"""
        self._settings = settings
        self._deliver = deliver
        self._correct_handler = correct

    # --- 窓を開いた・閉じた・設定が変わった ---

    def open(self) -> None:
        """比較の窓を開いた(最初の state())。裏で探して、設定が入で voice-serve があれば起動する。1 回だけ。"""
        with self._lock:
            if self._opened or self._closed:
                return
            self._opened = True
        settings = self._read_settings()
        with self._lock:
            self._press = settings.press
            self._llm_correct = settings.llm_correct
        self._begin(settings)

    def close(self) -> None:
        """比較の窓を閉じた・DriftPilot を終える。録音中なら cancel を書き、子を止める(待たない)。

        まだ返していない llm_request には、標準入力を閉じる前に cancelled を返す(REQ-342)。
        """
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._generation += 1
            recording = self._state == RECORDING
            self._state = HIDDEN
            self._note = None
            process, self._process = self._process, None
            pending = self._drain_requests_locked()
            if not self._sync:
                self._outbox.put(None)  # 箱に残っている知らせを送り終えてから、送り役を終わらせる
        if process is not None:
            self._write_cancelled(process, pending)
            self._threads.append(process.stop_in_background(cancel=recording))

    def shutdown(self, timeout: float) -> None:
        """止める処理が終わるのを、合計で timeout 秒まで待つ(app.run が窓を閉じた後に呼ぶ。窓はもう無いので目に見えない)。"""
        self.close()
        end = time.monotonic() + timeout
        for thread in list(self._threads):
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            thread.join(remaining)

    def settings_changed(self) -> None:
        """設定の窓で保存した(WebSession.publish → 比較の窓の _on_settings_change)。enabled・場所の変化を受ける。press は次の録音から。"""
        with self._lock:
            if not self._opened or self._closed:
                return
        settings = self._read_settings()
        with self._lock:
            self._press = settings.press
            self._llm_correct = settings.llm_correct  # 次の録音から効く(今の録音は始めたときの値のまま)
            applied = self._applied
        if applied == (settings.enabled, settings.cli):
            self._changed()  # press の変化だけでも、画面の view に届ける
            return
        self._begin(settings)

    # --- 画面から ---

    def view(self) -> dict[str, Any]:
        """画面に描く音声入力の状態(voice_state の中身)。"""
        with self._lock:
            return self._view_locked()

    def wants_correction(self) -> bool:
        """LLM で直す設定が入か(REQ-342)。録音中は、その録音を始めたときの値(設定を変えても次の録音から効く)。"""
        with self._lock:
            return self._record_correct if self._state == RECORDING else self._llm_correct

    def press(self, action: Any, pane: str = PANE_COMPARE, correction: Any = None) -> bool:
        """ボタンを押した・離した。toggle は待ちなら start・録音中なら stop、down は待ちなら start、up は録音中なら stop。

        押せない状態・知らない操作では何もしない(False)。start・stop を書いたら True。
        correction は stop のときだけ使う(CompareApi が直してよいと判定した持ち物。context を持つ)。設定が入で correction が
        あれば stop に correct: true と context を付ける。それ以外は correct: false(context は付けない)。
        """
        if action not in ACTIONS:
            return False
        message: dict[str, Any]
        with self._lock:
            process = self._process
            state = self._state
            if process is None:
                return False
            if state == IDLE and action in (ACTION_TOGGLE, ACTION_DOWN):
                message = {"type": "start"}
                self._state = RECORDING
                self._pane = pane if pane in (PANE_COMPARE, PANE_AI) else PANE_COMPARE
                self._limit_note = None
                self._note = None
                self._level_revision += 1
                self._record_correct = self._llm_correct
                self._awaiting = None
            elif state == RECORDING and action in (ACTION_TOGGLE, ACTION_UP):
                self._state = TRANSCRIBING
                self._note = None
                if self._record_correct and correction is not None:
                    message = {"type": "stop", "correct": True, "context": list(correction.context)}
                    self._awaiting = (correction, self._pane)
                else:
                    message = {"type": "stop", "correct": False}
                    self._awaiting = None
            else:
                return False
        if not process.send(message):
            with self._lock:
                if self._process is process and self._state in (RECORDING, TRANSCRIBING):
                    self._state = IDLE
            self._changed()
            return False
        self._changed()
        return True

    def cancel(self) -> bool:
        """録音中なら cancel を書いて待ちに戻る(Esc・窓を閉じる・比べ直す・入力を変える・窓を切り離す・戻す)。それ以外は何もしない。"""
        with self._lock:
            process = self._process
            if process is None or self._state != RECORDING:
                return False
            self._state = IDLE
            self._note = None
        process.send({"type": "cancel"})
        self._changed()
        return True

    def cancel_corrections(self) -> None:
        """まだ返していない LLM の直しすべてに cancelled を返し、「LLM で直しています」を消す(REQ-342)。

        比べ直す・入力を変える・AI の窓を切り離す・戻す・窓を閉じるときに、録音の取り消しと並べて呼ぶ。録音中でない Esc では呼ばない。
        text_corrected はその後も届くが、取り消した id は画面へ知らせない。
        """
        with self._lock:
            process = self._process
            had = bool(self._correcting) or self._awaiting is not None
            self._awaiting = None
            pending = self._cancel_requests_locked()
        if process is not None:
            self._write_cancelled(process, pending)
        if had or pending:
            self._changed()

    def recording(self) -> bool:
        with self._lock:
            return self._state == RECORDING

    # --- 探す・起動する(裏のスレッド) ---

    def _read_settings(self) -> VoiceSettings:
        try:
            return self._settings()
        except Exception:  # noqa: BLE001 - 設定を読めなければ既定(入・自動で探す)
            return VoiceSettings()

    def _begin(self, settings: VoiceSettings) -> None:
        """今の子を止め、設定に合わせて探して起動する(仕事は daemon のスレッド)。"""
        with self._lock:
            if self._closed:
                return
            self._generation += 1
            generation = self._generation
            self._applied = (settings.enabled, settings.cli)
            process, self._process = self._process, None
            changed = self._state != HIDDEN
            recording = self._state == RECORDING
            self._state = HIDDEN
            self._note = None
            self._limit_note = None
            pending = self._drain_requests_locked()
        if process is not None:
            self._write_cancelled(process, pending)
            self._threads.append(process.stop_in_background(cancel=recording))
        if changed:
            self._changed()
        if not settings.enabled:
            return
        thread = threading.Thread(target=self._find_and_start, args=(generation, settings.cli), daemon=True, name="voice-find")
        self._threads.append(thread)
        thread.start()

    def _find(self, setting: str) -> Discovery:
        finder = self._finder or discovery.discover_default
        try:
            return finder(setting)
        except Exception:  # noqa: BLE001 - 探す途中の失敗は「見つからない」
            return discovery.NOT_FOUND

    def _find_and_start(self, generation: int, setting: str) -> None:
        found = self._find(setting)
        with self._lock:
            if generation != self._generation or self._closed:
                return
            self._discovery = found
        if not found.usable:
            return  # ボタンは出さない(状態は設定の「音声入力」のタブに出す)
        holder: dict[str, VoiceProcess] = {}

        def on_event(event: Event) -> None:
            process = holder.get("process")
            if process is not None:
                self._on_event(process, event)

        process = self._factory(found.argv, on_event)
        holder["process"] = process
        with self._lock:
            if generation != self._generation or self._closed:
                return
            self._process = process
            self._state = PREPARING
        if not process.start():
            with self._lock:
                if self._process is process:
                    self._process = None
                    self._state = FAILED
                    self._note = failure_note(CODE_START_FAILED)
            self._changed()
            return
        self._changed()
        with self._lock:
            stale = generation != self._generation or self._closed
        if stale:
            process.stop_in_background()

    # --- LLM の直し(REQ-342) ---

    def _cancel_requests_locked(self) -> list[str | int]:
        """ロックの中で呼ぶ。頼んだ発話すべてを取り消しにし、llm_request が届いていてまだ返していない id を返す(返したことにする)。"""
        pending: list[str | int] = []
        for key, request in self._requested.items():
            request.cancelled = True
            if request.requested and not request.responded:
                request.responded = True
                pending.append(key)
        self._correcting.clear()
        return pending

    def _drain_requests_locked(self) -> list[str | int]:
        """ロックの中で呼ぶ。子が替わる・閉じるとき。取り消して、頼んだ発話の記録をすべて捨てる。"""
        pending = self._cancel_requests_locked()
        self._requested.clear()
        self._awaiting = None
        return pending

    @staticmethod
    def _write_cancelled(process: VoiceProcess, ids: list[str | int]) -> None:
        for key in ids:
            process.send({"type": "llm_response", "id": key, "error": "cancelled"})

    def _on_llm_request(self, process: VoiceProcess, event: Event) -> None:
        """llm_request(読みのスレッド)。頼んだ発話の id の最初の 1 回だけ CompareApi の扱い口へ渡す。ほかは not_requested。"""
        key = event.get("id")
        messages = event.get("messages")
        handler = self._correct_handler
        error: str | None = None
        correction: Any = None
        with self._lock:
            if self._process is not process or self._closed:
                return
            request = self._requested.get(key) if isinstance(key, (str, int)) else None
            if request is None or request.requested:
                error = "not_requested"
            else:
                request.requested = True
                correction = request.correction
                if request.cancelled:
                    request.responded = True
                    error = "cancelled"
                elif handler is None or not isinstance(messages, list):
                    request.responded = True
                    error = "failed"
        if error is not None:
            process.send({"type": "llm_response", "id": key, "error": error})
            return
        assert handler is not None and isinstance(messages, list)
        try:
            handler(correction, messages, partial(self._respond, process, key))  # 自分のロックの外で呼ぶ
        except Exception:  # noqa: BLE001 - 扱い口の失敗は failed にする(例外の中身は出さない)
            self._respond(process, key, {"error": "failed"})

    def _respond(self, process: VoiceProcess, key: str | int, result: dict[str, Any]) -> None:
        """llm_response を子へ書く(1 つの id に 1 回だけ。子が替わった後・閉じた後・取り消した後は書かない)。refused は注記を出す。"""
        refused = result.get("error") == "refused"
        with self._lock:
            request = self._requested.get(key)
            if request is None or request.responded or self._process is not process or self._closed:
                return
            request.responded = True
            if refused and self._state == IDLE:
                self._note = msg.VOICE_CORRECT_REFUSED
        content, error = result.get("content"), result.get("error")
        message: dict[str, Any] = {"type": "llm_response", "id": key}
        if isinstance(content, str):
            message["content"] = content
        else:
            message["error"] = error if isinstance(error, str) else "failed"
        process.send(message)
        if refused:
            self._changed()

    def _on_text_corrected(self, process: VoiceProcess, event: Event) -> None:
        """text_corrected(読みのスレッド)。頼んだ id(取り消していない)のときだけ、押した面の窓へ知らせる。"""
        key = event.get("id")
        with self._lock:
            if self._process is not process or self._closed:
                return
            request = self._requested.pop(key, None) if isinstance(key, (str, int)) else None
            if request is None:
                return
            was_correcting = key in self._correcting
            self._correcting.discard(key)
            if not request.cancelled:
                self._send_locked(
                    TARGET_PRESSED,
                    {
                        "type": CORRECTED_EVENT,
                        "id": key,
                        "text": event.get("text", ""),
                        "before": event.get("before"),
                        "changed": event.get("changed") is True,
                        "rejected": event.get("rejected"),
                    },
                    request.pane,
                )
        if was_correcting:
            self._changed()

    # --- 子の知らせ(読みのスレッド) ---

    def _on_event(self, process: VoiceProcess, event: Event) -> None:
        kind = event.get("type")
        if kind == "llm_request":
            self._on_llm_request(process, event)
            return
        if kind == "text_corrected":
            self._on_text_corrected(process, event)
            return
        deliver_text: dict[str, Any] | None = None
        pane = PANE_COMPARE
        with self._lock:
            if self._process is not process or self._closed:
                return
            state = self._state
            if kind in ("text", "text_failed", "cancelled"):
                awaiting, self._awaiting = self._awaiting, None
            else:
                awaiting = None
            if kind == "ready":
                if state == PREPARING:
                    self._state = IDLE
            elif kind == "recording":
                if state == RECORDING:
                    self._level_revision += 1  # 経過の秒を 0 から数え直す
            elif kind == "level":
                if state != RECORDING:
                    return
                payload = {"type": LEVEL_EVENT, "rms": event.get("rms", 0.0)}
                self._send_locked(TARGET_BOTH, payload)
                return
            elif kind == "text":
                if state not in (RECORDING, TRANSCRIBING):
                    return  # 取り消しの後に遅れて来た文字は捨てる
                self._state = IDLE
                self._note = None
                self._limit_note = msg.VOICE_LIMIT_NOTE if event.get("limit") is True else None
                pane = self._pane
                deliver_text = {"type": TEXT_EVENT, "text": event.get("text", ""), "pane": pane}
                utterance = event.get("id")
                if utterance is not None and event.get("correcting") is True and awaiting is not None:
                    # stop(correct: true)の直後の text が、頼んだ発話(id は text に付く。calling.md)
                    self._requested[utterance] = _Requested(awaiting[0], awaiting[1])
                    self._correcting.add(utterance)
                    deliver_text["id"] = utterance
                    deliver_text["correcting"] = True
            elif kind == "text_failed":
                if state not in (RECORDING, TRANSCRIBING):
                    return
                self._state = IDLE
                self._note = text_failed_note(str(event.get("code", "unknown")), self._system)
            elif kind == "cancelled":
                if state in (RECORDING, TRANSCRIBING):
                    self._state = IDLE
                    self._note = None
            elif kind in ("error", "stopped", "exited"):
                code = str(event.get("code")) if kind == "error" else (CODE_STOPPED if kind == "stopped" else CODE_EXITED)
                self._state = FAILED
                self._note = failure_note(code)
                self._process = None
                self._drain_requests_locked()  # 子はもう無い(書く先が無いので返さない)
                self._threads.append(process.stop_in_background())
            else:
                return
        if deliver_text is not None:
            self._send(TARGET_PRESSED, deliver_text, pane)
        self._changed()

    # --- 窓へ知らせる ---

    def _view_locked(self) -> dict[str, Any]:
        state = self._state
        if state == PREPARING:
            note, title = msg.VOICE_PREPARING, msg.VOICE_PREPARING
        elif state == TRANSCRIBING:
            note, title = msg.VOICE_TRANSCRIBING, msg.VOICE_TRANSCRIBING
        elif state == RECORDING:
            note = None
            title = msg.VOICE_TITLE_RECORDING_HOLD if self._press == PRESS_HOLD else msg.VOICE_TITLE_RECORDING_TOGGLE
        elif state == FAILED:
            note = title = self._note or ""
        elif state == HIDDEN:
            note, title = None, msg.VOICE_BUTTON
        else:
            note = self._note or (msg.VOICE_CORRECTING if self._correcting else None)
            title = msg.VOICE_TITLE_IDLE_HOLD if self._press == PRESS_HOLD else msg.VOICE_TITLE_IDLE_TOGGLE
            if note:
                title = f"{title}\n{note}"
        return {
            "revision": self._revision,
            "state": state,
            "press": self._press if self._press in PRESS_MODES else PRESS_TOGGLE,
            "can_press": state in (IDLE, RECORDING),
            "note": note,
            "title": title,
            "limit_note": self._limit_note if state == IDLE else None,
            "level_revision": self._level_revision,
            "correcting": bool(self._correcting),
        }

    def _changed(self) -> None:
        """状態の view を両方の窓へ知らせる(このロックの外で呼ぶ)。"""
        with self._lock:
            self._revision += 1
            payload = {"type": CHANGED_EVENT, "voice": self._view_locked()}
            self._send_locked(TARGET_BOTH, payload)

    def _send(self, target: str, payload: dict[str, Any], pane: str = PANE_COMPARE) -> None:
        with self._lock:
            self._send_locked(target, payload, pane)

    def _send_locked(self, target: str, payload: dict[str, Any], pane: str = PANE_COMPARE) -> None:
        """送る順を保つため、ロックの中で順番の箱に入れる(送るのはロックの外の daemon のスレッド)。"""
        destination = TARGET_BOTH if target == TARGET_BOTH else pane
        if self._sync:
            self._dispatch(destination, payload)
            return
        if self._closed:
            return  # 閉じた後は送らない(送り役は終わっている)
        self._outbox.put((destination, payload))
        if self._worker is None:
            # 送り役は閉じるまで 1 本だけ動かし続ける(時間で終わると、終わりかけに入れた知らせが残る競合がある)
            self._worker = threading.Thread(target=self._drain, daemon=True, name="voice-notify")
            self._worker.start()

    def _drain(self) -> None:
        """箱から順に取り出して送る。時間を切らずに待ち、close が入れる None で終わる。"""
        while True:
            item = self._outbox.get()
            if item is None:
                return
            self._dispatch(*item)

    def _dispatch(self, destination: str, payload: dict[str, Any]) -> None:
        deliver = self._deliver
        if deliver is None:
            return
        try:
            deliver(destination, payload)
        except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
            pass

    # --- 設定の窓・テストが読む ---

    @property
    def discovery(self) -> Discovery | None:
        """最後に探した結果(まだ探していなければ None)。"""
        with self._lock:
            return self._discovery
