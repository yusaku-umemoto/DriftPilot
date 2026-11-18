"""PartsPilot の音声入力(`pilot-call voice-serve`)の子プロセス(ADR-0233 決定2 / REQ-341 §3)。

DriftPilot は録音も文字にすることもしない。`<CLI> pilot-call voice-serve --request <ファイル>` を子として起動し、
標準入力に `start`・`stop`・`cancel`(と `llm_response`)を 1 行ずつ書き、標準出力の行(`ready`・`level`・`text` など)を読む。
画面(gui_web)は読まない。子の知らせは、整えた dict で on_event(関数の呼び返し)に渡す(呼ぶのは読みの裏のスレッド)。

on_event に渡す dict("type" の種類):

- ready・recording・cancelled・stopped
- level(rms: 0〜1 の数)
- text(text: 文字、limit: 真偽値。correcting が真で id が文字か整数のときだけ、id と correcting: true も付く)
- llm_request(id・messages: リスト。中身は見ない。確かめて送るのは画面の側。REQ-342)
- text_corrected(id・text・before・changed・rejected: None か code。REQ-342)
- text_failed・error(code: 英小文字・数字・_ の 64 字まで。ほかは unknown。message は読まない)
- exited(`stopped`・`error` を受けずに、自分で止める前に子が終わった)
- start_failed は起動できなかったとき(start が False を返し、この知らせは出さない。呼ぶ側が決める)

決まり:
- 依頼のファイルは UTF-8(BOM なし)で一時に書き、`accepted` を受けたら消す。受けずに子が終わったときも消す
- 標準出力は 1 行 1 MiB 以下の UTF-8 の JSON のオブジェクトだけ扱う(ほかは捨てる)。知らない type・項目は無視する
- 標準エラーは別のスレッドで読み捨てる(画面にも記録にも出さない)
- 子が終わった後は書かない(書けなくても例外を出さない)
- 止める: 録音中なら cancel を書き、標準入力を閉じ、待っても終わらなければ terminate、さらに待って kill。
  自分で止め始めた後の子の知らせは、画面へ渡さない
- 裏のスレッドは daemon にする。`message` の中身・話した文字は、標準エラー・ログ・例外の文に出さない
"""

import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from driftpilot import __version__
from driftpilot.series.discovery import hidden_console

MAX_LINE = 1024 * 1024
STOP_WAIT = 5.0
KILL_WAIT = 2.0
CODE_PATTERN = re.compile(r"[a-z0-9_]{1,64}")
UNKNOWN_CODE = "unknown"
CORRELATION_PREFIX = "DP-voice-"

Event = dict[str, Any]


def request_body(version: str, correlation_id: str) -> dict[str, Any]:
    """voice-serve の依頼(calling.md の 1)。LLM は使わない(llm: off・llm_correct: false)。

    直しは依頼では頼まず、stop の `correct` と `llm_request` への `llm_response` で行う(DriftPilot が AI の送り口を持つ。REQ-342)。
    """
    return {
        "schema": "pilot.call-request",
        "schema_version": "1",
        "operation": "voice-serve",
        "caller": {"product": "DriftPilot", "version": version, "correlation_id": correlation_id},
        "llm": "off",
        "params": {"engine": "auto", "llm_correct": False},
    }


def new_correlation_id() -> str:
    return f"{CORRELATION_PREFIX}{secrets.token_hex(8)}"


def _code(value: Any) -> str:
    return value if isinstance(value, str) and CODE_PATTERN.fullmatch(value) is not None else UNKNOWN_CODE


def _utterance_id(value: Any) -> str | int | None:
    """発話の id(文字か整数。真偽値・ほかは None)。text・llm_request・text_corrected が同じ id を持つ(calling.md)。"""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    return value


def _rms(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    number = float(value)
    if number != number:  # NaN
        return 0.0
    return min(1.0, max(0.0, number))


class VoiceProcess:
    """`voice-serve` の子 1 つ。start で起動し、send で書き、stop で止める。

    argv は CLI を起動する先頭の並び(見つけた CLI。テストは [sys.executable, 偽の CLI])。
    on_event は読みの裏のスレッドから呼ばれる(例外は握りつぶす)。
    """

    def __init__(
        self,
        argv: Sequence[str],
        on_event: Callable[[Event], None],
        *,
        version: str = __version__,
        env: Mapping[str, str] | None = None,
        stop_wait: float = STOP_WAIT,
        kill_wait: float = KILL_WAIT,
    ) -> None:
        self._argv = list(argv)
        self._on_event = on_event
        self._version = version
        self._env = None if env is None else dict(env)
        self._stop_wait = stop_wait
        self._kill_wait = kill_wait
        self._proc: subprocess.Popen[bytes] | None = None
        self._request: Path | None = None
        self._lock = threading.Lock()  # 状態の印(_closing・_terminal・_request)を守る
        self._write_lock = threading.Lock()  # 標準入力への書き込みを 1 つずつにする
        self._closing = False  # 自分で止め始めた
        self._terminal = False  # stopped か error を受けた
        self._stdin_closed = False
        self.ended = threading.Event()  # 子が終わって、読みのスレッドも片付けた

    # --- 起動 ---

    def start(self) -> bool:
        """依頼のファイルを書いて子を起動する。起動できなければ(後始末をして)False。"""
        try:
            self._request = self._write_request()
            self._proc = subprocess.Popen(  # noqa: S603 - 見つけて product を確かめた CLI だけ(ADR-0233 決定8)
                [*self._argv, "pilot-call", "voice-serve", "--request", str(self._request)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._env,
                **hidden_console(),
            )
        except (OSError, ValueError):
            self._remove_request()
            self.ended.set()
            return False
        threading.Thread(target=self._read_stdout, daemon=True, name="voice-stdout").start()
        threading.Thread(target=self._drain_stderr, daemon=True, name="voice-stderr").start()
        return True

    def _write_request(self) -> Path:
        handle, name = tempfile.mkstemp(prefix="driftpilot-voice-", suffix=".json")
        body = request_body(self._version, new_correlation_id())
        try:
            with os.fdopen(handle, "wb") as fh:
                fh.write(json.dumps(body, ensure_ascii=False).encode("utf-8"))
        except BaseException:
            Path(name).unlink(missing_ok=True)
            raise
        return Path(name)

    def _remove_request(self) -> None:
        with self._lock:
            request, self._request = self._request, None
        if request is not None:
            try:
                request.unlink(missing_ok=True)
            except OSError:
                pass

    @property
    def request_path(self) -> Path | None:
        """依頼のファイル(`accepted` の前、または子が終わる前だけ。テストが形を確かめる)。"""
        return self._request

    # --- 子 → DriftPilot ---

    def _emit(self, event: Event) -> None:
        with self._lock:
            if self._closing:
                return  # 自分で止め始めた後の知らせは画面へ渡さない
        try:
            self._on_event(event)
        except Exception:  # noqa: BLE001 - 画面側の失敗で読みを止めない(例外の中身は出さない)
            pass

    def _lines(self, stream: Any) -> Any:
        """1 行ずつ出す。1 MiB を超える行は改行まで捨てる(出さない)。"""
        while True:
            line = stream.readline(MAX_LINE + 1)
            if not line:
                return
            if len(line) > MAX_LINE and not line.endswith(b"\n"):
                while True:  # 長すぎる行の続きを、改行まで読み捨てる
                    rest = stream.readline(MAX_LINE + 1)
                    if not rest or rest.endswith(b"\n"):
                        break
                continue
            yield line

    def _read_stdout(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        try:
            for line in self._lines(proc.stdout):
                self._handle(line)
        except (OSError, ValueError):
            pass
        finally:
            self._finish(proc)

    def _drain_stderr(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stderr is not None
        try:
            while proc.stderr.read(4096):
                pass
        except (OSError, ValueError):
            pass

    def _handle(self, line: bytes) -> None:
        try:
            data = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return
        if not isinstance(data, dict) or not isinstance(data.get("type"), str):
            return
        kind = data["type"]
        if kind == "accepted":
            self._remove_request()
        elif kind in ("ready", "recording", "cancelled"):
            self._emit({"type": kind})
        elif kind == "level":
            self._emit({"type": "level", "rms": _rms(data.get("rms"))})
        elif kind == "text":
            text = data.get("text")
            if isinstance(text, str):
                spoken: Event = {"type": "text", "text": text, "limit": data.get("limit") is True}
                utterance = _utterance_id(data.get("id"))
                if data.get("correcting") is True and utterance is not None:
                    # LLM で直す頼みの発話だけ id と correcting を渡す(直しの知らせと突き合わせる。それ以外の形は今までどおり)
                    spoken["id"] = utterance
                    spoken["correcting"] = True
                self._emit(spoken)
        elif kind == "llm_request":
            utterance = _utterance_id(data.get("id"))
            messages = data.get("messages")
            if utterance is not None:
                # 確かめて送るのは画面の側(中身は見ない)。リストでなければ空のリストとして渡し、画面の側が断る
                self._emit(
                    {"type": "llm_request", "id": utterance, "messages": messages if isinstance(messages, list) else []}
                )
        elif kind == "text_corrected":
            utterance = _utterance_id(data.get("id"))
            text, before, rejected = data.get("text"), data.get("before"), data.get("rejected")
            if utterance is not None:
                # text が文字でなければ、置き換えずに印を消すだけにする(rejected を unknown にする)
                self._emit(
                    {
                        "type": "text_corrected",
                        "id": utterance,
                        "text": text if isinstance(text, str) else "",
                        "before": before if isinstance(before, str) else None,
                        "changed": data.get("changed") is True,
                        "rejected": _code(rejected) if rejected is not None else (None if isinstance(text, str) else UNKNOWN_CODE),
                    }
                )
        elif kind == "text_failed":
            self._emit({"type": "text_failed", "code": _code(data.get("code"))})
        elif kind in ("stopped", "error"):
            with self._lock:
                self._terminal = True
            event: Event = {"type": kind}
            if kind == "error":
                event["code"] = _code(data.get("code"))
            self._emit(event)
        # warning・知らない type は無視する

    def _finish(self, proc: "subprocess.Popen[bytes]") -> None:
        """標準出力が閉じた後。子の終わりを待ち、依頼のファイルを消し、思わず終わったなら知らせる。"""
        try:
            proc.wait()
        except Exception:  # noqa: BLE001
            pass
        self._remove_request()
        with self._lock:
            unexpected = not self._closing and not self._terminal
        if unexpected:
            self._emit({"type": "exited"})
        self.ended.set()

    # --- DriftPilot → 子 ---

    def send(self, message: Mapping[str, Any]) -> bool:
        """1 行の JSON を書いて flush する。終わった後・書けないときは何もせず False(例外を出さない)。"""
        proc = self._proc
        if proc is None or proc.stdin is None or self.ended.is_set():
            return False
        data = json.dumps(dict(message), ensure_ascii=False).encode("utf-8") + b"\n"
        with self._write_lock:
            if self._stdin_closed:
                return False
            try:
                proc.stdin.write(data)
                proc.stdin.flush()
            except (OSError, ValueError):
                return False
        return True

    def _close_stdin(self) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None:
            return
        with self._write_lock:
            if self._stdin_closed:
                return
            self._stdin_closed = True
            try:
                proc.stdin.close()
            except (OSError, ValueError):
                pass

    # --- 止める ---

    def stop(self, cancel: bool = False) -> None:
        """止める(終わるまで待つ。最長で stop_wait + kill_wait + 少し)。cancel が真なら先に cancel を書く。

        標準入力を閉じ、stop_wait 秒待って終わらなければ terminate、さらに kill_wait 秒で kill。何度呼んでもよい。
        """
        proc = self._proc
        with self._lock:
            self._closing = True
        if proc is None:
            return
        if cancel:
            self.send({"type": "cancel"})
        self._close_stdin()
        try:
            proc.wait(timeout=self._stop_wait)
        except subprocess.TimeoutExpired:
            self._terminate(proc)
        except Exception:  # noqa: BLE001
            pass
        self.ended.wait(timeout=self._kill_wait)

    def _terminate(self, proc: "subprocess.Popen[bytes]") -> None:
        try:
            proc.terminate()
            proc.wait(timeout=self._kill_wait)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=self._kill_wait)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass

    def stop_in_background(self, cancel: bool = False) -> threading.Thread:
        """止める処理を daemon のスレッドで動かす(窓を閉じる処理を待たせない)。"""
        with self._lock:
            self._closing = True  # 以後の知らせは画面へ渡さない(スレッドが動く前から)
        thread = threading.Thread(target=self.stop, args=(cancel,), daemon=True, name="voice-stop")
        thread.start()
        return thread
