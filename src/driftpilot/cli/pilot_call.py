"""ほかの Pilot から呼ぶ入口 `driftpilot pilot-call`(ADR-0203。REQ-286)。

人向けの `compare`・`batch` とは別の入口。依頼は JSON のファイル、出力は標準出力の 1 行 1 つの JSON、結果は output_dir のファイル。

- `pilot-call capabilities`: できることを JSON の 1 行で出して 0 で終わる
- `pilot-call compare --request <依頼のファイル>`: 依頼を確かめ(合わなければ error と 2。accepted は出さない)、
  accepted を出し、2 つの入力を比べ、result.json と masked_payload.txt を output_dir に書き、result を出す
- `pilot-call config-facts --request <依頼のファイル>`(ADR-0223。REQ-319): 1 つの config を「道すじ・値」の一覧にし、
  result.json(伏せない)と masked_facts.json(AI に渡す形)を output_dir に書く。終了コードは成功 0・誤り 2・止めた 130

標準出力には JSON の行だけを出す(1 行ごとに flush)。人向けの知らせは標準エラー。標準入力が閉じたら止める合図とする
(別のスレッドで読み、終わりになったら取り消しの印を立てる。区間ごとの比較の間と比べ終わりで見る。書きかけは残さない)。
AI は呼ばない。AI の設定・鍵は読まない(読むのは利用者が足した伏せる語だけ)。
依頼や入力の中身は標準エラーにも error の文にも出さない(パス・件数・名前だけ)。
"""

import argparse
import json
import os
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import driftpilot
from driftpilot.cli.commands import EXIT_BY_STATUS, EXIT_ERROR
from driftpilot.cli.output import open_machine_file, write_machine
from driftpilot.cli.render import to_json, to_json_all
from driftpilot.config_parser.platforms import CONFIG_PLATFORM_NAMES
from driftpilot.core import (
    AUTO_PLATFORM,
    CompareCancelled,
    CompareOutcome,
    CompareRequest,
    FactsError,
    MaskedPayload,
    MultiCompareOutcome,
    SideInput,
    compare_all,
    config_facts,
    masked_all,
    masked_single,
    request_problem,
    run_compare,
)
from driftpilot.llm_analyzer.mask_patterns import masking_fingerprint
from driftpilot.llm_settings import SettingsError, load_extra_secret_words
from driftpilot.messages import (
    CLI_ERROR_PREFIX,
    CLI_HELP,
    PILOT_CALL_MESSAGES,
    cli_err_all_segments_conflict,
    unreadable,
    unreadable_utf8,
)
from driftpilot.segmentation.input_forms import InputDecodeError, read_config_input

_H = CLI_HELP
_M = PILOT_CALL_MESSAGES

EXIT_CANCELLED = 130  # 標準入力が閉じられて止めた(ADR-0203 決定6)

SCHEMA = "pilot.call-request"
SCHEMA_VERSION = "1"
RESULT_FILE = "result.json"
PAYLOAD_FILE = "masked_payload.txt"
FACTS_MASKED_FILE = "masked_facts.json"  # config-facts の、AI に渡す伏せた一覧(ADR-0223 決定4)

KINDS = ("auto", "config", "show")
MODES = ("both", "structured", "raw")
PARAM_NAMES = ("kind", "platform", "show_command", "mode", "all_segments")

# 比較の状態から result の outcome への写し
OUTCOME_BY_STATUS = {"no_difference": "ok", "difference": "review", "needs_selection": "not_run"}

STDIN_CHUNK = 4096
STDIN_SETTLE_SECONDS = 0.05  # 見張りの最初の読みを待つ長さ(すぐ閉じられた入力を、比較の前に取り消しにするため)

StdinReader = Callable[[int], bytes]


def add_parser(sub: Any) -> None:
    """`pilot-call` とその下の `capabilities`・`compare` を足す。"""
    call = sub.add_parser("pilot-call", help=_H["pilot_call.help"], description=_H["pilot_call.help"])
    inner = call.add_subparsers(dest="pilot_call_command", required=True)
    inner.add_parser("capabilities", help=_H["pilot_call.capabilities"], description=_H["pilot_call.capabilities"])
    cmp = inner.add_parser("compare", help=_H["pilot_call.compare"], description=_H["pilot_call.compare"])
    cmp.add_argument("--request", required=True, metavar="PATH", help=_H["pilot_call.request"])
    facts = inner.add_parser("config-facts", help=_H["pilot_call.config_facts"], description=_H["pilot_call.config_facts"])
    facts.add_argument("--request", required=True, metavar="PATH", help=_H["pilot_call.request"])


def emit(obj: dict[str, Any]) -> None:
    """標準出力へ JSON を 1 行(改行を含めない。UTF-8・`\\n`)で書き、すぐ流す。"""
    write_machine(json.dumps(obj, ensure_ascii=False) + "\n")
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        pass


def capabilities() -> dict[str, Any]:
    return {
        "product": "DriftPilot",
        "version": driftpilot.__version__,
        "operations": {
            "compare": {"protocol_versions": [1], "kind": "task", "llm": ["off"]},
            "config-facts": {"protocol_versions": [1], "kind": "task", "llm": ["off"]},
        },
    }


class Reject(Exception):
    """依頼を断る・受け付けた後に止める。code は error の code、message は決まった文、correlation_id は読めた分。"""

    def __init__(self, code: str, message: str, correlation_id: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.correlation_id = correlation_id


@dataclass(frozen=True)
class Params:
    kind: str = "auto"
    platform: str = "auto"
    show_command: str | None = None
    mode: str = "both"
    all_segments: bool = True


@dataclass(frozen=True)
class CallRequest:
    correlation_id: str
    before: str
    after: str
    output_dir: Path
    params: Params


# --- 依頼の確かめ(ADR-0203 決定3) ---


def _read_envelope(path: str) -> dict[str, Any]:
    try:
        text = Path(path).read_bytes().decode("utf-8-sig")
        data = json.loads(text)
    except (OSError, ValueError, RecursionError):  # UnicodeDecodeError・JSONDecodeError は ValueError
        raise Reject("invalid_request", _M["invalid_request"]) from None
    if not isinstance(data, dict):
        raise Reject("invalid_request", _M["invalid_request"])
    return data


def correlation_id_of(data: dict[str, Any]) -> str:
    caller = data.get("caller")
    value = caller.get("correlation_id") if isinstance(caller, dict) else None
    return value if isinstance(value, str) else ""


def _is_absolute(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and Path(value).is_absolute()


def _input_paths(data: dict[str, Any]) -> tuple[str, str]:
    inputs = data.get("inputs")
    invalid = Reject("invalid_inputs", _M["invalid_inputs"])
    if not isinstance(inputs, list) or len(inputs) != 2 or not all(isinstance(i, dict) for i in inputs):
        raise invalid
    by_role = {i.get("role"): i.get("path") for i in inputs if isinstance(i.get("role"), str)}
    if set(by_role) != {"before", "after"} or len(by_role) != len(inputs):
        raise invalid
    if not all(_is_absolute(p) for p in by_role.values()):
        raise invalid
    for path in by_role.values():
        if not Path(path).is_file():
            raise Reject("input_not_found", _M["input_not_found"].format(path=path))
    return by_role["before"], by_role["after"]


def _output_dir(data: dict[str, Any]) -> Path:
    value = data.get("output_dir")
    invalid = Reject("invalid_output_dir", _M["invalid_output_dir"])
    if not _is_absolute(value):
        raise invalid
    path = Path(value)
    try:
        if not path.is_dir() or any(path.iterdir()):
            raise invalid
    except OSError:
        raise invalid from None
    return path


def _params(data: dict[str, Any]) -> Params:
    raw = data.get("params", {})
    if not isinstance(raw, dict):
        raise Reject("invalid_param", _M["invalid_param"].format(name="params"))
    if unknown := [name for name in raw if name not in PARAM_NAMES]:
        raise Reject("unknown_param", _M["unknown_param"].format(count=len(unknown), allowed=", ".join(PARAM_NAMES)))

    def invalid(name: str) -> Reject:
        return Reject("invalid_param", _M["invalid_param"].format(name=name))

    kind = raw.get("kind", "auto")
    if kind not in KINDS:
        raise invalid("kind")
    platform = raw.get("platform", "auto")
    if not isinstance(platform, str) or not platform.strip():
        raise invalid("platform")
    show_command = raw.get("show_command")
    if show_command is not None and not isinstance(show_command, str):
        raise invalid("show_command")
    mode = raw.get("mode", "both")
    if mode not in MODES:
        raise invalid("mode")
    all_segments = raw.get("all_segments", True)
    if not isinstance(all_segments, bool):
        raise invalid("all_segments")
    params = Params(kind, platform.strip(), show_command, mode, all_segments)
    _check_combination(params)
    return params


def _check_combination(params: Params) -> None:
    """人向けの compare と同じ組み合わせの決まり(文は人向けの CLI の文)。"""
    if params.all_segments:
        if params.show_command is not None:
            raise Reject("invalid_param", cli_err_all_segments_conflict(["--show-command"]))
        return
    probe = CompareRequest(
        SideInput("", ""),
        SideInput("", ""),
        mode=params.mode,  # type: ignore[arg-type]
        kind=params.kind,  # type: ignore[arg-type]
        platform=params.platform,
        show_command=params.show_command,
    )
    if (problem := request_problem(probe)) is not None:
        raise Reject("invalid_param", problem.message)


def validate(path: str) -> CallRequest:
    """依頼のファイルを確かめる。合わなければ Reject(読めた correlation_id を持たせる)。"""
    data = _read_envelope(path)
    try:
        if data.get("schema") != SCHEMA or data.get("schema_version") != SCHEMA_VERSION:
            raise Reject("unsupported_schema", _M["unsupported_schema"])
        if data.get("operation") != "compare":
            raise Reject("unsupported_operation", _M["unsupported_operation"])
        before, after = _input_paths(data)
        output_dir = _output_dir(data)
        if data.get("llm", "off") != "off":
            raise Reject("llm_not_supported", _M["llm_not_supported"])
        if data.get("secret_on_stdin", False) is not False:
            raise Reject("secret_not_supported", _M["secret_not_supported"])
        params = _params(data)
    except Reject as exc:
        exc.correlation_id = correlation_id_of(data)
        raise
    return CallRequest(correlation_id_of(data), before, after, output_dir, params)


# --- config-facts の依頼の確かめ(ADR-0223 決定2) ---

FACTS_PARAM_NAMES = ("platform",)


@dataclass(frozen=True)
class FactsRequest:
    correlation_id: str
    config: str
    output_dir: Path
    platform: str


def _facts_input_path(data: dict[str, Any]) -> str:
    """inputs は役 config が 1 つだけ(絶対パスで、ある普通のファイル)。"""
    inputs = data.get("inputs")
    invalid = Reject("invalid_inputs", _M["invalid_inputs_facts"])
    if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict):
        raise invalid
    if inputs[0].get("role") != "config" or not _is_absolute(inputs[0].get("path")):
        raise invalid
    path = inputs[0]["path"]
    if not Path(path).is_file():
        raise Reject("input_not_found", _M["input_not_found"].format(path=path))
    return path


def _facts_platform(data: dict[str, Any]) -> str:
    """params は platform だけ(auto 既定、または compare の kind: config で選べる機種の名前)。"""
    raw = data.get("params", {})
    if not isinstance(raw, dict):
        raise Reject("invalid_param", _M["invalid_param"].format(name="params"))
    if unknown := [name for name in raw if name not in FACTS_PARAM_NAMES]:
        raise Reject(
            "unknown_param", _M["unknown_param"].format(count=len(unknown), allowed=", ".join(FACTS_PARAM_NAMES))
        )
    invalid = Reject("invalid_param", _M["invalid_param"].format(name="platform"))
    platform = raw.get("platform", AUTO_PLATFORM)
    if not isinstance(platform, str) or not platform.strip():
        raise invalid
    platform = platform.strip()
    if platform != AUTO_PLATFORM and platform not in CONFIG_PLATFORM_NAMES:
        raise invalid
    # 表がまだ無い機種(fujitsu_srs)は compare の kind: config と同じ決まりで断る(文は人向けの CLI の文)
    probe = CompareRequest(SideInput("", ""), SideInput("", ""), kind="config", platform=platform)
    if (problem := request_problem(probe)) is not None:
        raise Reject("invalid_param", problem.message)
    return platform


def validate_config_facts(path: str) -> FactsRequest:
    """config-facts の依頼のファイルを確かめる(compare の validate と同じ順)。合わなければ Reject。"""
    data = _read_envelope(path)
    try:
        if data.get("schema") != SCHEMA or data.get("schema_version") != SCHEMA_VERSION:
            raise Reject("unsupported_schema", _M["unsupported_schema"])
        if data.get("operation") != "config-facts":
            raise Reject("unsupported_operation", _M["unsupported_operation_facts"])
        config = _facts_input_path(data)
        output_dir = _output_dir(data)
        if data.get("llm", "off") != "off":
            raise Reject("llm_not_supported", _M["llm_not_supported"])
        if data.get("secret_on_stdin", False) is not False:
            raise Reject("secret_not_supported", _M["secret_not_supported"])
        platform = _facts_platform(data)
    except Reject as exc:
        exc.correlation_id = correlation_id_of(data)
        raise
    return FactsRequest(correlation_id_of(data), config, output_dir, platform)


# --- 標準入力の見張り(ADR-0203 決定7) ---


def default_stdin_reader() -> StdinReader | None:
    """標準入力の番号を直接 os.read する読み口。標準入力が無い・番号を取れないときは None(見張らない)。

    sys.stdin.buffer を使わないのは、daemon のスレッドがバッファの錠を持ったまま終わると、終了時に Fatal error になることがあるため。
    """
    stdin = sys.stdin
    if stdin is None:
        return None
    try:
        fd = stdin.fileno()
    except (OSError, ValueError, AttributeError):
        return None
    return lambda size: os.read(fd, size)


class StdinWatcher:
    """標準入力を読み続け、終わり(EOF)か読めなくなったら closed の印を立てる(daemon のスレッド)。"""

    def __init__(self, reader: StdinReader) -> None:
        self.closed = threading.Event()
        self._reader = reader
        self._first = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._watch, name="pilot-call-stdin", daemon=True).start()
        self._first.wait(STDIN_SETTLE_SECONDS)

    def _watch(self) -> None:
        try:
            while True:
                data = self._reader(STDIN_CHUNK)
                if not data:
                    break
                self._first.set()
        except (OSError, ValueError):
            pass
        self.closed.set()
        self._first.set()


# --- 結果のファイル(ADR-0203 決定5) ---


class WriteFailed(Exception):
    def __init__(self, path: Path, exc: OSError) -> None:
        super().__init__(str(path))
        self.path = path
        self.reason = str(exc.strerror or type(exc).__name__)


def remove_files(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink()
        except OSError:
            pass


def write_files(directory: Path, contents: list[tuple[str, str]]) -> list[Path]:
    """output_dir の中にだけ、排他作成(上書きしない)で書く。失敗したら、自分が作ったファイルを消して WriteFailed。"""
    created: list[Path] = []
    path = directory
    try:
        for name, text in contents:
            path = directory / name
            with open_machine_file(str(path), "x") as fh:
                created.append(path)
                fh.write(text)
    except OSError as exc:
        remove_files(created)
        raise WriteFailed(path, exc) from exc
    return created


# --- compare ---


def _fail(code: str, message: str, correlation_id: str, exit_code: int = EXIT_ERROR) -> int:
    """最後の行の error と、標準エラーの同じ文(「エラー: 」付き)。"""
    emit({"type": "error", "correlation_id": correlation_id, "code": code, "message": message})
    print(f"{CLI_ERROR_PREFIX}{message}", file=sys.stderr)
    return exit_code


def _failed_message(outcome: CompareOutcome | MultiCompareOutcome) -> str:
    """比較のエラーの文。失敗した組の数と core の code だけ(入力の中身・例外の文は入れない)。"""
    if isinstance(outcome, CompareOutcome):
        codes = [outcome.error.code if outcome.error is not None else "unknown"]
    else:
        codes = [
            p.outcome.error.code if p.outcome.error is not None else p.outcome.status
            for p in outcome.pairs
            if p.outcome.status in ("error", "needs_selection")
        ]
    shown = ", ".join(sorted(set(codes))) if codes else "no_pairs"
    return _M["compare_failed"].format(count=len(codes), codes=shown)


@dataclass(frozen=True)
class Compared:
    """比べた結果。status が error のときは failure に error の文があり、ファイルの中身は空。"""

    status: str
    summary: dict[str, Any]
    result_text: str
    payload_text: str
    failure: str = ""


def _summary(status: str, pairs: int, unpaired: int, masked: MaskedPayload | None) -> dict[str, Any]:
    return {
        "status": status,
        "pairs": pairs,
        "unpaired": unpaired,
        "diffs": masked.diffs if masked is not None else 0,
        "masking_fingerprint": masking_fingerprint().digest,
        "not_sent": masked.not_sent if masked is not None else 0,
        "extra_secret_words": masked.words_count if masked is not None else 0,
    }


def _side(path: str) -> SideInput:
    """入力を読む(人向けの compare と同じ read_config_input)。読めないときは Reject(input_unreadable。パスだけを文に入れる)。"""
    try:
        read = read_config_input(path)
    except InputDecodeError:
        raise Reject("input_unreadable", _M["input_unreadable"].format(reason=unreadable_utf8(path))) from None
    except OSError as exc:
        raise Reject("input_unreadable", _M["input_unreadable"].format(reason=unreadable(path, exc))) from None
    return SideInput(read.text, path, read.notes)


def _request_of(call: CallRequest) -> CompareRequest:
    params = call.params
    return CompareRequest(
        before=_side(call.before),
        after=_side(call.after),
        mode=params.mode,  # type: ignore[arg-type]
        kind=params.kind,  # type: ignore[arg-type]
        platform=params.platform,
        show_command=params.show_command,
    )


def _compare(call: CallRequest, request: CompareRequest, cancel: threading.Event, words: tuple[str, ...]) -> Compared:
    """比べて、状態・summary・2 つのファイルの中身を返す。取り消しの印は区間ごとの比較に渡す。"""
    if call.params.all_segments:
        multi = compare_all(request, should_cancel=cancel.is_set)
        if multi.status == "error":
            summary = _summary("error", len(multi.pairs), len(multi.unpaired), None)
            return Compared("error", summary, "", "", _failed_message(multi))
        masked = masked_all(multi, words)
        summary = _summary(multi.status, len(multi.pairs), len(multi.unpaired), masked)
        return Compared(multi.status, summary, to_json_all(multi) + "\n", masked.text)
    single = run_compare(request, should_cancel=cancel.is_set)
    if single.status == "error":
        return Compared("error", _summary("error", 1, 0, None), "", "", _failed_message(single))
    masked = masked_single(request, single, words)
    return Compared(single.status, _summary(single.status, 1, 0, masked), to_json(single) + "\n", masked.text)


def run_compare_call(args: argparse.Namespace, stdin_reader: StdinReader | None = None) -> int:
    try:
        call = validate(args.request)
    except Reject as exc:
        return _fail(exc.code, exc.message, exc.correlation_id)
    cid = call.correlation_id
    try:
        words = load_extra_secret_words()
    except SettingsError as exc:
        return _fail("settings_unreadable", _M["settings_unreadable"].format(reason=exc), cid)
    emit({"type": "accepted", "correlation_id": cid})

    reader = stdin_reader if stdin_reader is not None else default_stdin_reader()
    watcher = StdinWatcher(reader) if reader is not None else None
    if watcher is not None:
        watcher.start()
    cancel = watcher.closed if watcher is not None else threading.Event()
    written: list[Path] = []
    try:
        request = _request_of(call)
        compared = _compare(call, request, cancel, words)
        if cancel.is_set():
            raise CompareCancelled("done")
        if compared.status == "error":
            return _fail("compare_failed", compared.failure, cid)
        written = write_files(
            call.output_dir, [(RESULT_FILE, compared.result_text), (PAYLOAD_FILE, compared.payload_text)]
        )
        if cancel.is_set():
            raise CompareCancelled("write")
        emit(
            {
                "type": "result",
                "correlation_id": cid,
                "outcome": OUTCOME_BY_STATUS[compared.status],
                "summary": compared.summary,
                "files": [str(p) for p in written],
            }
        )
        return EXIT_BY_STATUS[compared.status]
    except Reject as exc:
        return _fail(exc.code, exc.message, cid)
    except WriteFailed as exc:
        return _fail("write_failed", _M["write_failed"].format(path=exc.path, reason=exc.reason), cid)
    except CompareCancelled:
        remove_files(written)
        return _fail("cancelled", _M["cancelled"], cid, EXIT_CANCELLED)
    except Exception as exc:  # noqa: BLE001 - 受け付けた後の予期しない失敗は、書きかけを消して error の行で終える
        remove_files(written)
        return _fail("internal_error", _M["internal_error"].format(error_type=type(exc).__name__), cid)


# --- config-facts(ADR-0223) ---


def _facts_failure(exc: FactsError) -> tuple[str, str]:
    """一覧を作れなかったときの code と文。文に入れるのは区間の数か例外の型の名前だけ(config の中身は入れない)。"""
    if exc.code == "multiple_segments":
        return exc.code, _M["multiple_segments"].format(count=exc.count)
    return "parse_failed", _M["parse_failed"].format(error_type=exc.error_type or "unknown")


def run_config_facts_call(args: argparse.Namespace, stdin_reader: StdinReader | None = None) -> int:
    try:
        call = validate_config_facts(args.request)
    except Reject as exc:
        return _fail(exc.code, exc.message, exc.correlation_id)
    cid = call.correlation_id
    try:
        words = load_extra_secret_words()
    except SettingsError as exc:
        return _fail("settings_unreadable", _M["settings_unreadable"].format(reason=exc), cid)
    emit({"type": "accepted", "correlation_id": cid})

    reader = stdin_reader if stdin_reader is not None else default_stdin_reader()
    watcher = StdinWatcher(reader) if reader is not None else None
    if watcher is not None:
        watcher.start()
    cancel = watcher.closed if watcher is not None else threading.Event()
    written: list[Path] = []
    try:
        side = _side(call.config)
        if cancel.is_set():
            raise CompareCancelled("read")
        facts = config_facts(side.text, side.name, side.notes, call.platform, words)
        if cancel.is_set():  # 読み終えた後・書く前に見る(書きかけは残さない)
            raise CompareCancelled("done")
        written = write_files(
            call.output_dir, [(RESULT_FILE, facts.result_text), (FACTS_MASKED_FILE, facts.masked_text)]
        )
        if cancel.is_set():
            raise CompareCancelled("write")
        emit(
            {
                "type": "result",
                "correlation_id": cid,
                "outcome": "ok",
                "summary": {
                    "platform": {"requested": call.platform, "used": facts.used},
                    "counts": facts.counts,
                    "unsent": facts.unsent,
                    "fingerprints": facts.fingerprints,
                },
                "files": [str(p) for p in written],
            }
        )
        return 0
    except Reject as exc:
        return _fail(exc.code, exc.message, cid)
    except FactsError as exc:
        code, message = _facts_failure(exc)
        return _fail(code, message, cid)
    except WriteFailed as exc:
        return _fail("write_failed", _M["write_failed"].format(path=exc.path, reason=exc.reason), cid)
    except CompareCancelled:
        remove_files(written)
        return _fail("cancelled", _M["cancelled_facts"], cid, EXIT_CANCELLED)
    except Exception as exc:  # noqa: BLE001 - 受け付けた後の予期しない失敗は、書きかけを消して error の行で終える
        remove_files(written)
        return _fail("internal_error", _M["internal_error"].format(error_type=type(exc).__name__), cid)


def run(args: argparse.Namespace, *, stdin_reader: StdinReader | None = None) -> int:
    """`pilot-call` の入口。終了コードを返す(commands._run から呼ばれる)。"""
    if args.pilot_call_command == "capabilities":
        emit(capabilities())
        return 0
    if args.pilot_call_command == "config-facts":
        return run_config_facts_call(args, stdin_reader)
    return run_compare_call(args, stdin_reader)
