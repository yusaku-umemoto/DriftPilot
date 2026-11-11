"""手元の CLI(Codex CLI・Claude Code CLI)を呼んで1回送る接続の実装(ADR-0073 決定1・6・7。REQ-102 §1〜§3)。

- キーは持たない。CLI 自身のログインを使う。CLI に渡す環境変数は入口が作って env で渡す(キーの環境変数を除いたもの。
  llm_settings.cli_env)。このモジュールは設定もキーも環境変数も読まない(ADR-0015 決定5)
- 毎回、新しい一時フォルダを作業フォルダにして動かし、終わったら消す。DriftPilot の作業フォルダ・設定の場所は渡さない
- 文(伏せた後の文)は標準入力(UTF-8)で渡し、引数には入れない(引数の長さの上限と、プロセスの一覧に本文が出るのを避ける)
- 道具を使わせない・利用者の設定を読ませない指定を付ける(Claude は --tools "" と --safe-mode、Codex は
  --sandbox read-only と --disable shell_tool と --ignore-user-config)。指定が使えない古い版でも、外して呼び直さない
- 回答は CLI の出力の本文。CLI の出力・エラー出力の文面は例外に入れない(ADR-0018 決定7)。例外は except の外で作り、
  CLI の出力を持つ例外(TimeoutExpired など)を __context__ につながない
- CLI が見つからない → LLMClientUnavailable。時間切れ → CLI を子のプロセスごと止めて LLMConnectionError。
  0 以外で終わった・回答が無い・空 → LLMResponseError
- subprocess.Popen で動かす(shell は使わない。引数はリスト)。CLI を新しいプロセスのまとまりにして動かし
  (Mac は新しいセッション、Windows は新しいプロセスグループで窓を出さない)、時間切れや途中の例外(Ctrl+C など)では
  まとまりごと止める(npm の入口が本体を別のプロセスで動かすため。判断の記録 2)
"""

import json
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from driftpilot import i18n
from driftpilot.llm_analyzer.client import LLMClientUnavailable, LLMConnectionError, LLMError, LLMResponseError

# 利用者に見える文(日本語・英語。REQ-258)。鍵の組は両方で同じ
_TEXTS_JA = {"default_model": "CLI の既定のモデル", "effort_invalid": "cli_effort の値が使えません"}
_TEXTS_EN = {"default_model": "CLI default model", "effort_invalid": "The cli_effort value cannot be used"}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

CODEX_CLI = "codex-cli"
CLAUDE_CODE_CLI = "claude-code-cli"
CLI_FORMATS = (CODEX_CLI, CLAUDE_CODE_CLI)
DEFAULT_CLI_TIMEOUT_SECONDS = 60
# モデルの欄が空のとき(--model を付けずに CLI の既定のモデルで送る)の、確認の行と結果に出すモデルの名前
CLI_DEFAULT_MODEL = _T["default_model"]
ANSWER_FILE = "answer.txt"  # Codex の --output-last-message の書き先(一時フォルダの中)
# Thinking の深さ(プロファイルの cli_effort。ADR-0157 決定10)。ここに無い値は引数に入れない
CLI_EFFORTS: dict[str, tuple[str, ...]] = {
    CLAUDE_CODE_CLI: ("low", "medium", "high", "xhigh", "max"),
    CODEX_CLI: ("low", "medium", "high"),
}
# cli_effort があって時間の上限が書かれていないときの上限(秒。ADR-0157 決定11)
CLI_EFFORT_TIMEOUT_SECONDS = 300
# 届いたところから読むとき、行が来ない間に中止を見る間隔(秒)
STREAM_POLL_SECONDS = 0.2

# Windows の作り方の印(Windows 以外の subprocess には無いので値で持つ)
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200

# テストで差し替える
_IS_WINDOWS = sys.platform.startswith("win")
_popen = subprocess.Popen
_monotonic = time.monotonic


def _communicate(process: subprocess.Popen, data: bytes, timeout: float) -> tuple[bytes | None, bytes | None]:
    return process.communicate(data, timeout=timeout)


def _taskkill(env: Mapping[str, str]) -> str:
    """Windows に入っている taskkill の場所(CLI に渡す環境の SystemRoot から。PATH の別の taskkill は使わない)。"""
    root = next((value for name, value in env.items() if name.upper() == "SYSTEMROOT"), r"C:\Windows")
    return str(Path(root) / "System32" / "taskkill.exe")


def _kill_tree(process: subprocess.Popen, env: Mapping[str, str]) -> None:
    """CLI をまとまりごと止める。Mac は新しいセッションのプロセスグループ、Windows は taskkill /F /T(子のプロセスも)。

    taskkill が使えない・失敗したときは、直接の子だけを止める。
    """
    if _IS_WINDOWS:
        try:
            done = subprocess.run(
                [_taskkill(env), "/F", "/T", "/PID", str(process.pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
                check=False,
            )
            ok = done.returncode == 0
        except OSError:
            ok = False
        if not ok:
            process.kill()
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)  # start_new_session なので、グループの番号は CLI のプロセスの番号
    except ProcessLookupError:
        pass  # もう終わっている
    except OSError:
        process.kill()


BuildArgs = Callable[[Path, Path, str | None, str | None], list[str]]


@dataclass(frozen=True)
class CliSpec:
    """CLI ごとの違い。program は PATH から探す名前、build_args は (一時フォルダ, 回答のファイル, モデル, Thinking の深さ) からの引数。

    answer_from_file が真なら回答は回答のファイルから取り(標準出力は捨てる)、偽なら標準出力から取る。
    stream_args は届いたところから読むときの引数(ADR-0157 決定8)。None の CLI は流さず、今の ask で最後にまとめて受け取る。
    """

    program: str
    build_args: BuildArgs
    answer_from_file: bool
    stream_args: BuildArgs | None = None


def _model_args(model: str | None) -> list[str]:
    return ["--model", model] if model else []


def _effort_args(kind: str, effort: str | None) -> list[str]:
    """Thinking の深さの指定(ADR-0157 決定10)。値は CLI_EFFORTS の中だけ(CliClient が作るときに確かめる)。"""
    if effort is None:
        return []
    if effort not in CLI_EFFORTS[kind]:  # 引数に入れる前にもう一度見る(一覧に無い文字を CLI に渡さない)
        raise ValueError(_T["effort_invalid"])
    if kind == CLAUDE_CODE_CLI:
        return ["--effort", effort]
    return ["-c", f'model_reasoning_effort="{effort}"']


def _codex_args(work: Path, answer_file: Path, model: str | None, effort: str | None = None) -> list[str]:
    # codex-cli 0.164.0 の exec の指定(--ignore-user-config と --disable shell_tool は判断の記録 5)
    return [
        "exec",
        "-",
        "--sandbox",
        "read-only",
        "--skip-git-repo-check",
        "--ephemeral",
        "--cd",
        str(work),
        "--output-last-message",
        str(answer_file),
        "--ignore-user-config",
        "--disable",
        "shell_tool",
        *_model_args(model),
        *_effort_args(CODEX_CLI, effort),
    ]


def _claude_args(
    work: Path, answer_file: Path, model: str | None, effort: str | None = None, *, stream: bool = False
) -> list[str]:
    # Claude Code 2.1.310 の指定(--safe-mode は判断の記録 4)。流すときも --tools "" と --safe-mode は必ず付ける(ADR-0157 決定8)
    output = ["--output-format", "stream-json", "--verbose", "--include-partial-messages"] if stream else ["--output-format", "text"]
    return [
        "-p",
        *output,
        "--tools",
        "",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--safe-mode",
        *_model_args(model),
        *_effort_args(CLAUDE_CODE_CLI, effort),
    ]


def _claude_stream_args(work: Path, answer_file: Path, model: str | None, effort: str | None = None) -> list[str]:
    return _claude_args(work, answer_file, model, effort, stream=True)


CLI_SPECS: dict[str, CliSpec] = {
    CODEX_CLI: CliSpec("codex", _codex_args, answer_from_file=True),
    CLAUDE_CODE_CLI: CliSpec("claude", _claude_args, answer_from_file=False, stream_args=_claude_stream_args),
}


def find_cli(kind: str, cli_path: str | None, env: Mapping[str, str]) -> str | None:
    """CLI のプログラムの絶対パス。cli_path が無ければ、env の PATH から探す(Windows の .cmd などは shutil.which に任せる)。"""
    search = next((value for name, value in env.items() if name.upper() == "PATH"), None)
    found = shutil.which(cli_path if cli_path else CLI_SPECS[kind].program, path=search)
    return None if found is None else os.path.abspath(found)


def _spawn_options() -> dict[str, object]:
    if _IS_WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def _stop(process: subprocess.Popen, env: Mapping[str, str]) -> None:
    """まとまりごと止めて、終わるのを待つ。出力の管は閉じる(残った孫を待たない)。"""
    try:
        _kill_tree(process, env)
    finally:
        process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    pass


def _text(raw: bytes | None) -> str | None:
    """回答の本文。UTF-8 でない・空白だけなら None。"""
    if raw is None:
        return None
    try:
        answer = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None
    return answer or None


def _stop_stream(process: subprocess.Popen, env: Mapping[str, str]) -> None:
    """流すときの止め方。まとまりごと止めて終わるのを待つ。管は持ち主のスレッドが閉じる(読みかけの管を別のスレッドから閉じない)。"""
    try:
        _kill_tree(process, env)
    finally:
        process.wait()


def _feed(pipe, data: bytes) -> None:
    """文を標準入力に書いて閉じる(CLI が先に終わったときの BrokenPipeError などは無視する)。"""
    try:
        pipe.write(data)
        pipe.flush()
    except OSError:
        pass
    finally:
        try:
            pipe.close()
        except OSError:
            pass


def _drain(pipe, lines: "queue.Queue[bytes | None]") -> None:
    """標準出力を1行ずつ lines に入れる。閉じたら None を入れて、管を閉じる。"""
    try:
        for line in iter(pipe.readline, b""):
            lines.put(line)
    except (OSError, ValueError):
        pass
    finally:
        lines.put(None)
        try:
            pipe.close()
        except OSError:
            pass


def _stream_line(line: bytes) -> tuple[str, str, dict | None]:
    """stream-json の1行から (文字, 考え, result の行)。

    文字は text_delta の text、考えは thinking_delta の thinking(ADR-0169 決定2)。system・assistant は読まない。
    読めない行は ("", "", None)。
    """
    try:
        data = json.loads(line.decode("utf-8"))
    except ValueError:  # UTF-8 でない(UnicodeDecodeError)・JSON でない
        return "", "", None
    if not isinstance(data, dict):
        return "", "", None
    if data.get("type") == "result":
        return "", "", data
    if data.get("type") != "stream_event":
        return "", "", None
    event = data.get("event")
    if not isinstance(event, dict) or event.get("type") != "content_block_delta":
        return "", "", None
    delta = event.get("delta")
    if not isinstance(delta, dict):
        return "", "", None
    if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
        return delta["text"], "", None
    if delta.get("type") == "thinking_delta" and isinstance(delta.get("thinking"), str):
        return "", delta["thinking"], None
    return "", "", None


class _Seen:
    """on_delta を包み、文字が1つでも届いたかを覚える。考えの文字(think)も「届いた」に数える(ADR-0169 決定1)。"""

    def __init__(self, on_delta: Callable[[str], None], on_thinking: Callable[[str], None] | None = None) -> None:
        self._on_delta = on_delta
        self._on_thinking = on_thinking
        self.received = False

    def __call__(self, text: str) -> None:
        if text:
            self.received = True
        self._on_delta(text)

    @property
    def wants_thinking(self) -> bool:
        """考えの受け取り口があるか。無ければ考えを読まない(on_thinking を渡さないときは今と同じ。REQ-231 §7 の 5)。"""
        return self._on_thinking is not None

    def think(self, text: str) -> None:
        if not text:
            return
        self.received = True
        if self._on_thinking is not None:
            self._on_thinking(text)


class CliClient:
    """手元の CLI に文を渡し、回答の本文を返す。キーは持たない。

    env は CLI に渡す環境変数(入口がキーの環境変数を除いて作る。llm_settings.cli_env)。CLI を探す PATH もここから取る。
    api_key・base_url は build_request の呼び方に合わせて受け取るだけ(持たない・使わない)。
    作るときに CLI の場所を決め、見つからなければ LLMClientUnavailable(比較は完走し、理由が cli_not_found になる)。
    """

    def __init__(
        self,
        *,
        kind: str,
        env: Mapping[str, str],
        model: str | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        cli_path: str | None = None,
        timeout_seconds: int = DEFAULT_CLI_TIMEOUT_SECONDS,
        effort: str | None = None,
    ) -> None:
        del api_key, base_url  # 使わない(キーを属性に残さない)
        if effort is not None and effort not in CLI_EFFORTS[kind]:
            # 一覧に無い値は引数に入れない。文に値を入れない(ADR-0157 決定10)
            raise ValueError(_T["effort_invalid"])
        self._spec = CLI_SPECS[kind]
        self._kind = kind
        self._env = dict(env)
        self._model = model or None
        self._effort = effort
        self._timeout = timeout_seconds
        program = find_cli(kind, cli_path, self._env)
        if program is None:
            raise LLMClientUnavailable()
        self._program = program

    @property
    def model_name(self) -> str:
        return self._model or CLI_DEFAULT_MODEL

    def __repr__(self) -> str:  # 環境変数を表示しない
        return f"CliClient(model={self.model_name!r})"

    def ask(self, prompt: str) -> str:
        data = prompt.encode("utf-8")
        with tempfile.TemporaryDirectory(prefix="driftpilot-cli-", ignore_cleanup_errors=True) as tmp:
            work = Path(tmp)
            answer_file = work / ANSWER_FILE
            argv = [self._program, *self._spec.build_args(work, answer_file, self._model, self._effort)]
            answer, failure = self._run(argv, work, data, answer_file)
        # 例外は except の外で作る(CLI の出力を持つ例外を __context__ につながない)
        if failure is not None:
            raise failure
        assert answer is not None
        return answer

    @property
    def streams_thinking(self) -> bool:
        """流す引数のある CLI(Claude Code)だけ、考えの文字を on_thinking に渡せる(Codex は偽。ADR-0169 決定2)。"""
        return self._spec.stream_args is not None

    def ask_stream(
        self, prompt: str, on_delta: Callable[[str], None], *, on_thinking: Callable[[str], None] | None = None
    ) -> str:
        """届いたところから文字を on_delta に渡す(ADR-0157 決定8)。流す引数の無い CLI(Codex)は今の ask。

        文字が1つも届く前に LLMResponseError になったら、ask で1回だけ呼び直す(--include-partial-messages が
        使えない古い版のため。安全の指定は外さない)。時間切れでは呼び直さない。StreamStopped はそのまま外へ出す。
        考えの文字(thinking_delta)は on_thinking があるときだけ渡し、届いたら「届いた」に数える(ADR-0169)。
        """
        stream_args = self._spec.stream_args
        if stream_args is None:
            return self.ask(prompt)
        data = prompt.encode("utf-8")
        seen = _Seen(on_delta, on_thinking)
        with tempfile.TemporaryDirectory(prefix="driftpilot-cli-", ignore_cleanup_errors=True) as tmp:
            work = Path(tmp)
            argv = [self._program, *stream_args(work, work / ANSWER_FILE, self._model, self._effort)]
            answer, failure = self._run_stream(argv, work, data, seen)
        if isinstance(failure, LLMResponseError) and not seen.received:
            return self.ask(prompt)
        if failure is not None:
            raise failure
        assert answer is not None
        return answer

    def _run_stream(self, argv: list[str], work: Path, data: bytes, on_delta: "_Seen") -> tuple[str | None, LLMError | None]:
        try:
            process = _popen(
                argv,
                cwd=work,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                **_spawn_options(),
            )
        except OSError:
            process = None  # 見つからない・動かす権限が無い・形式が違う
        if process is None:
            return None, LLMClientUnavailable()
        lines: queue.Queue[bytes | None] = queue.Queue()
        # 標準入力と標準出力はそれぞれのスレッドが持ち、終わったら閉じる(主のスレッドは止めて待つだけ)
        threading.Thread(target=_feed, args=(process.stdin, data), daemon=True).start()
        threading.Thread(target=_drain, args=(process.stdout, lines), daemon=True).start()
        deadline = _monotonic() + self._timeout
        result: dict | None = None
        timed_out = False
        try:
            while True:
                try:
                    line = lines.get(timeout=STREAM_POLL_SECONDS)
                except queue.Empty:
                    if _monotonic() >= deadline:
                        timed_out = True
                        break
                    on_delta("")  # 行が来ない間も中止を見る
                    continue
                if line is None:
                    break  # 標準出力が閉じた
                text, thought, found = _stream_line(line)
                if found is not None:
                    result = found
                if thought and on_delta.wants_thinking:
                    on_delta.think(thought)  # StreamStopped はそのまま外へ(下の except で CLI を止める)
                on_delta(text)  # StreamStopped はそのまま外へ(下の except で CLI を止める)
                if _monotonic() >= deadline:
                    timed_out = True
                    break
            # 標準出力が閉じた後も、終わるまでの待ちを STREAM_POLL_SECONDS 以下に分け、その間も中止を見る(ADR-0157 決定8)
            while not timed_out:
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    if process.poll() is None:
                        timed_out = True
                    break
                try:
                    process.wait(timeout=min(remaining, STREAM_POLL_SECONDS))
                    break
                except subprocess.TimeoutExpired:
                    on_delta("")  # StreamStopped はそのまま外へ(下の except で CLI を止める)
        except BaseException:
            _stop_stream(process, self._env)  # 中止・Ctrl+C などでも CLI を残さない
            raise
        if timed_out:
            _stop_stream(process, self._env)
            return None, LLMConnectionError()
        if process.returncode != 0 or result is None:
            return None, LLMResponseError()
        answer = result.get("result")
        if result.get("subtype") != "success" or result.get("is_error") or not isinstance(answer, str) or not answer.strip():
            return None, LLMResponseError()
        return answer.strip(), None

    def _run(self, argv: list[str], work: Path, data: bytes, answer_file: Path) -> tuple[str | None, LLMError | None]:
        from_file = self._spec.answer_from_file
        try:
            process = _popen(
                argv,
                cwd=work,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL if from_file else subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                shell=False,
                **_spawn_options(),
            )
        except OSError:
            process = None  # 見つからない・動かす権限が無い・形式が違う
        if process is None:
            return None, LLMClientUnavailable()
        timed_out = False
        try:
            stdout, _ = _communicate(process, data, self._timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            stdout = None
        except BaseException:
            _stop(process, self._env)  # Ctrl+C などでも CLI を残さない
            raise
        if timed_out:
            _stop(process, self._env)
            return None, LLMConnectionError()
        if process.returncode != 0:
            return None, LLMResponseError()
        if from_file:
            try:
                raw = answer_file.read_bytes()
            except OSError:
                raw = None
        else:
            raw = stdout
        answer = _text(raw)
        if answer is None:
            return None, LLMResponseError()
        return answer, None
