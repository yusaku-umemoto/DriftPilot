"""新しい版の DriftPilot が、今の版の中身をその場で入れ替える(ADR-0188 決定3 / REQ-253 §6)。

入口は packaging/gui_main.py の --apply-update <plan.json>(画面は読み込まない)。流れ:

1. plan.json を読み、形と場所と版を確かめる(自分の版が to_version と同じで from_version より新しいこと)。違えば何もしない(2)
2. 準備のフォルダーに applier-ready を書く(今の版はこれを見てから閉じる)
3. 今の版(pid)が終わるのを 60 秒まで待つ。終わらなければ入れ替えずに記録を残して 1
4. 古い中身を控えに移し、新しい中身を入れる。1 つの操作が失敗したら 0.5 秒あけて 10 回まで繰り返す
   - windows_zip: 新しい DriftPilot\\ の直下にある名前だけ。古い物は名前の変更で _update\\backup-<古い版>\\ へ移し、
     新しい物は準備のフォルダー(署名の確かめを通った new\\DriftPilot\\)から写す(判断の記録 1。動いている新しい版自身が
     _internal の中のファイルを開いているので、フォルダーごとの名前の変更では移せないため)。
     settings・_update と、新しい版に無い名前(利用者が置いた物)には触らない
   - mac_app: .app を丸ごと。.DriftPilot-<古い版>-backup.app に名前を変え、ditto で新しい .app を元の場所に写す
5. 入れ替えた場所の DriftPilot を --update-started <準備のフォルダー>/started-ok で起動する
6. started-ok が 120 秒以内に書かれたら控えを消して 0。起動した物が先に終わったら元に戻す。動いたまま 120 秒たったら控えを残して 0

失敗したときは、入れた新しい中身を取り除いて控えを元の名前に戻し、記録を last-failed.log に写して、
元の版を --update-failed <last-failed.log> で起動する(1)。
記録(update.log)は時刻・段・移した/写した名前・誤りの型と文だけ(設定・キーは扱わないので書かない)。
待つ・起動する・移す・写す・消す関数は差し替えられる(Ops。テストは偽物)。
"""

import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from driftpilot import __version__
from driftpilot.messages import UPDATE_TEXTS as _T
from driftpilot.update.check import INSTALL_MAC_APP, INSTALL_WINDOWS_ZIP, install_kind, parse_version
from driftpilot.update.download import APP_BINARY, DITTO, ZIP_FORBIDDEN_TOP, ZIP_ROOT
from driftpilot.update.prepare import (
    APPLIER_READY,
    CREATE_NEW_PROCESS_GROUP,
    DETACHED_PROCESS,
    LAST_FAILED_LOG,
    NEW_DIR,
    PLAN_NAME,
    STARTED_OK,
    SUPPORTED_KINDS,
    UPDATE_DIR,
    UPDATE_LOG,
    VERSION_DIR,
    install_target,
    staging_root,
)

PLAN_MAX_BYTES = 64 * 1024
OLD_EXIT_WAIT_SECONDS = 60
NEW_START_WAIT_SECONDS = 120
POLL_SECONDS = 0.5
RETRY_TIMES = 10
RETRY_PAUSE_SECONDS = 0.5
EXIT_APPLIED = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2
FLAG_STARTED = "--update-started"
FLAG_FAILED = "--update-failed"
_PLAN_KEYS = ("from_version", "to_version", "kind", "target", "staging", "pid", "key")


class PlanError(ValueError):
    """plan.json が読めない・形や場所や版が合わない(入れ替えない)。"""


@dataclass(frozen=True)
class Plan:
    from_version: str
    to_version: str
    kind: str
    target: Path
    staging: Path
    pid: int
    key: str


# --- 既定の操作(本物) ---


def _is_alive(pid: int) -> bool:
    """pid のプロセスが動いているか。Windows は OpenProcess と WaitForSingleObject、ほかは os.kill(pid, 0)。"""
    if sys.platform.startswith("win"):
        import ctypes

        synchronize = 0x00100000
        wait_timeout = 0x00000102
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(synchronize, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _launch(argv: list[str], cwd: str) -> Any:
    """切り離して起動する(Windows は DETACHED_PROCESS、ほかは start_new_session)。shell は使わない。poll() を持つ物を返す。"""
    options: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform.startswith("win"):
        options["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    return subprocess.Popen(argv, **options)


def _move(source: Path, dest: Path) -> None:
    """名前の変更で移す(同じドライブ)。dest が既にあれば誤り。"""
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(dest.name)
    os.rename(source, dest)


def _copy_tree_or_file(source: Path, dest: Path) -> None:
    """Windows の写し方: ファイルは copy2、フォルダーは copytree(copy2。リンクはたどらない)。"""
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(dest.name)
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, dest, symlinks=True, copy_function=shutil.copy2)
    else:
        shutil.copy2(source, dest, follow_symlinks=False)


def _ditto(source: Path, dest: Path) -> None:
    """Mac の写し方: ditto(引数の一覧。shell は使わない)。"""
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(dest.name)
    result = subprocess.run([DITTO, str(source), str(dest)], check=False, capture_output=True)
    if result.returncode != 0:
        raise OSError(_T["ditto_failed"].format(code=result.returncode))


def _remove(path: Path) -> None:
    """フォルダーは丸ごと、ファイル・リンクはそれだけを消す。無ければ何もしない。"""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


@dataclass
class Ops:
    """差し替えられる操作。copy_in を省くと、入れ方に合う既定(Windows は copy2・copytree、Mac は ditto)。"""

    is_alive: Callable[[int], bool] = _is_alive
    launch: Callable[[list[str], str], Any] = _launch
    move: Callable[[Path, Path], None] = _move
    copy_in: Callable[[Path, Path], None] | None = None
    remove: Callable[[Path], None] = _remove
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    now: Callable[[], str] = _now_text
    touch: Callable[[Path], None] = field(default=lambda path: os.utime(path))


def _copier(ops: Ops, kind: str) -> Callable[[Path, Path], None]:
    if ops.copy_in is not None:
        return ops.copy_in
    return _copy_tree_or_file if kind == INSTALL_WINDOWS_ZIP else _ditto


# --- plan.json ---


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def read_plan(path: Path | str, *, version: str = __version__, home: Path | None = None) -> Plan:
    """plan.json を読み、形・場所・版を確かめる。合わなければ PlanError(何も書かない)。"""
    path = Path(path)
    if path.name != PLAN_NAME:
        raise PlanError(_T["plan_name"])
    try:
        with open(path, "rb") as stream:
            data = stream.read(PLAN_MAX_BYTES + 1)
    except OSError:
        raise PlanError(_T["plan_unreadable"]) from None
    if len(data) > PLAN_MAX_BYTES:
        raise PlanError(_T["plan_too_large"])
    try:
        raw = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise PlanError(_T["plan_not_json"]) from None
    if not isinstance(raw, dict) or not all(key in raw for key in _PLAN_KEYS):
        raise PlanError(_T["plan_missing_field"])
    if not all(isinstance(raw[key], str) for key in _PLAN_KEYS if key != "pid"):
        raise PlanError(_T["plan_type"])
    pid = raw["pid"]
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise PlanError("pid")
    kind = raw["kind"]
    if kind not in SUPPORTED_KINDS:
        raise PlanError(_T["plan_kind"])
    old, new, mine = parse_version(raw["from_version"]), parse_version(raw["to_version"]), parse_version(version)
    if old is None or new is None or mine is None or mine != new or not new > old:
        raise PlanError(_T["plan_version"])
    if not VERSION_DIR.fullmatch(raw["to_version"]) or not VERSION_DIR.fullmatch(raw["from_version"]):
        raise PlanError(_T["plan_version_form"])
    target, staging = Path(raw["target"]), Path(raw["staging"])
    if not target.is_absolute() or not staging.is_absolute():
        raise PlanError(_T["plan_location"])
    if not _same(path.parent, staging):
        raise PlanError(_T["plan_location_file"])
    if staging.name != raw["to_version"]:
        raise PlanError(_T["plan_staging_name"])
    if kind == INSTALL_WINDOWS_ZIP:
        if staging.parent.name.casefold() != UPDATE_DIR or not _same(staging.parent.parent, target):
            raise PlanError(_T["plan_staging_location"])
    else:
        if not target.name.endswith(".app") or not _same(staging.parent, staging_root(kind, target, home)):
            raise PlanError(_T["plan_staging_location"])
    return Plan(raw["from_version"], raw["to_version"], kind, target, staging, pid, raw["key"])


# --- 記録 ---


class UpdateLog:
    """準備のフォルダーの update.log に 1 行ずつ書く(時刻・段・名前・誤りの型と文)。書けなくても止めない。"""

    def __init__(self, path: Path, now: Callable[[], str]) -> None:
        self.path = path
        self._now = now

    def __call__(self, stage: str, detail: str = "") -> None:
        line = f"{self._now()}\t{stage}\t{detail}".rstrip("\t") + "\n"
        try:
            with open(self.path, "a", encoding="utf-8") as stream:
                stream.write(line)
        except OSError:
            pass

    def error(self, stage: str, name: str, exc: BaseException) -> None:
        self(stage, f"{name}\t{type(exc).__name__}: {exc}")


def _retry(ops: Ops, action: Callable[[], None]) -> None:
    """1 つの操作を、失敗したら 0.5 秒あけて 10 回まで繰り返す(ウイルス対策ソフトが一時的につかむため)。"""
    for attempt in range(RETRY_TIMES):
        try:
            action()
            return
        except OSError:
            if attempt == RETRY_TIMES - 1:
                raise
            ops.sleep(RETRY_PAUSE_SECONDS)


# --- 入れ替え ---


@dataclass
class _Swap:
    """入れ替えの記録(元に戻すときに使う)。"""

    backup: Path
    backed_up: list[str] = field(default_factory=list)  # 控えに移した名前
    placed: list[str] = field(default_factory=list)  # 入れた(入れかけた)名前


def _windows_backup(plan: Plan) -> Path:
    return plan.target / UPDATE_DIR / f"backup-{plan.from_version}"


def _mac_backup(plan: Plan) -> Path:
    return plan.target.parent / f".DriftPilot-{plan.from_version}-backup.app"


def _launch_path(plan: Plan) -> Path:
    if plan.kind == INSTALL_WINDOWS_ZIP:
        return plan.target / "DriftPilot.exe"
    return plan.target.joinpath(*APP_BINARY)


def _launch_cwd(plan: Plan) -> str:
    """起動する DriftPilot の作業フォルダー(Windows は入れ替えた場所、Mac は .app の親)。"""
    return str(plan.target.parent if plan.kind == INSTALL_MAC_APP else plan.target)


def _swap_windows(plan: Plan, ops: Ops, log: UpdateLog, swap: _Swap) -> None:
    source_root = plan.staging / NEW_DIR / ZIP_ROOT
    copy_in = _copier(ops, plan.kind)
    names = sorted(
        child.name for child in source_root.iterdir() if child.name.casefold() not in ZIP_FORBIDDEN_TOP
    )
    if not names:
        raise OSError(_T["no_new_content"])
    if swap.backup.exists():
        _retry(ops, lambda: ops.remove(swap.backup))  # 前の失敗の残り(元に戻した後なので要らない)
    swap.backup.mkdir(parents=True)
    for name in names:
        current = plan.target / name
        if current.exists() or current.is_symlink():
            _retry(ops, lambda: ops.move(current, swap.backup / name))
            swap.backed_up.append(name)
            log("backup", name)
        swap.placed.append(name)

        def copy_once(name: str = name) -> None:
            dest = plan.target / name
            try:
                copy_in(source_root / name, dest)
            except OSError:
                if dest.exists() or dest.is_symlink():
                    try:
                        ops.remove(dest)  # 写しかけの物を消してから繰り返す
                    except OSError:
                        pass
                raise

        _retry(ops, copy_once)
        log("copy", name)


def _swap_mac(plan: Plan, ops: Ops, log: UpdateLog, swap: _Swap) -> None:
    source = plan.staging / NEW_DIR / "DriftPilot.app"
    copy_in = _copier(ops, plan.kind)
    if not source.is_dir():
        raise OSError(_T["no_new_content"])
    if swap.backup.exists() or swap.backup.is_symlink():
        _retry(ops, lambda: ops.remove(swap.backup))
    _retry(ops, lambda: ops.move(plan.target, swap.backup))
    swap.backed_up.append(plan.target.name)
    log("backup", plan.target.name)
    try:
        ops.touch(swap.backup)  # 掃除の 10 分を、控えに移した時から数える
    except OSError:
        pass
    swap.placed.append(plan.target.name)

    def copy_once() -> None:
        try:
            copy_in(source, plan.target)
        except OSError:
            if plan.target.exists() or plan.target.is_symlink():
                try:
                    ops.remove(plan.target)
                except OSError:
                    pass
            raise

    _retry(ops, copy_once)
    log("copy", plan.target.name)


def _roll_back(plan: Plan, ops: Ops, log: UpdateLog, swap: _Swap) -> bool:
    """入れた新しい中身を取り除き、控えを元の名前に戻す。全部戻せたら真。"""
    ok = True
    if plan.kind == INSTALL_WINDOWS_ZIP:
        pairs = [(plan.target / name, swap.backup / name) for name in swap.backed_up]
        placed = [plan.target / name for name in swap.placed]
    else:
        pairs = [(plan.target, swap.backup)] if swap.backed_up else []
        placed = [plan.target] if swap.placed else []
    for path in placed:
        if path.exists() or path.is_symlink():
            try:
                _retry(ops, lambda path=path: ops.remove(path))
                log("rollback-remove", path.name)
            except OSError as exc:
                log.error("rollback-remove", path.name, exc)
                ok = False
    for original, saved in pairs:
        try:
            _retry(ops, lambda original=original, saved=saved: ops.move(saved, original))
            log("rollback-restore", original.name)
        except OSError as exc:
            log.error("rollback-restore", original.name, exc)
            ok = False
    if plan.kind == INSTALL_WINDOWS_ZIP and ok:
        try:
            swap.backup.rmdir()
        except OSError:
            pass
    return ok


def _remove_backup(ops: Ops, log: UpdateLog, swap: _Swap) -> None:
    try:
        ops.remove(swap.backup)
        log("backup-removed", swap.backup.name)
    except OSError as exc:
        log.error("backup-removed", swap.backup.name, exc)  # 次の起動の掃除で消える


def _copy_log(plan: Plan, home: Path | None) -> Path:
    """update.log を last-failed.log に写し、その場所を返す。"""
    last = staging_root(plan.kind, plan.target, home) / LAST_FAILED_LOG
    try:
        shutil.copyfile(plan.staging / UPDATE_LOG, last)
    except OSError:
        pass
    return last


def _fail(plan: Plan, ops: Ops, log: UpdateLog, swap: _Swap | None, home: Path | None) -> int:
    """元に戻し、記録を写して、元の版を --update-failed <last-failed.log> で起動する。"""
    if swap is not None:
        _roll_back(plan, ops, log, swap)
    log("failed")
    last = _copy_log(plan, home)
    try:
        ops.launch([str(_launch_path(plan)), FLAG_FAILED, str(last)], _launch_cwd(plan))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.error("relaunch-old", _launch_path(plan).name, exc)
        _copy_log(plan, home)
    return EXIT_FAILED


def _wait(ops: Ops, seconds: float, done: Callable[[], bool]) -> bool:
    deadline = ops.clock() + seconds
    while True:
        if done():
            return True
        if ops.clock() >= deadline:
            return False
        ops.sleep(POLL_SECONDS)


def main(plan_path: Path | str, *, ops: Ops | None = None, version: str = __version__, home: Path | None = None) -> int:
    """入れ替える側の入口。0 は入れ替えた、1 は入れ替えられなかった(元に戻した)、2 は plan.json が合わず何もしなかった。"""
    try:
        plan = read_plan(plan_path, version=version, home=home)
    except PlanError:
        return EXIT_REFUSED
    ops = Ops() if ops is None else ops
    log = UpdateLog(plan.staging / UPDATE_LOG, ops.now)
    log("start", f"{plan.from_version} -> {plan.to_version} ({plan.kind}, key {plan.key})")
    try:
        (plan.staging / APPLIER_READY).write_bytes(b"")
    except OSError as exc:
        log.error("ready", APPLIER_READY, exc)
        return EXIT_REFUSED
    log("ready")
    try:
        exited = _wait(ops, OLD_EXIT_WAIT_SECONDS, lambda: not ops.is_alive(plan.pid))
    except Exception as exc:  # noqa: BLE001 - 確かめられないときは入れ替えない
        log.error("wait-old", str(plan.pid), exc)
        exited = False
    if not exited:
        log("old-still-running", str(plan.pid))  # 古い版はまだ動いているので、起動し直さない
        _copy_log(plan, home)
        return EXIT_FAILED
    backup = _windows_backup(plan) if plan.kind == INSTALL_WINDOWS_ZIP else _mac_backup(plan)
    swap = _Swap(backup)
    try:
        if plan.kind == INSTALL_WINDOWS_ZIP:
            _swap_windows(plan, ops, log, swap)
        else:
            _swap_mac(plan, ops, log, swap)
    except Exception as exc:  # noqa: BLE001 - どの誤りでも元に戻す
        log.error("swap", swap.placed[-1] if swap.placed else "", exc)
        return _fail(plan, ops, log, swap, home)
    started_ok = plan.staging / STARTED_OK
    launch_path = _launch_path(plan)
    try:
        process = ops.launch([str(launch_path), FLAG_STARTED, str(started_ok)], _launch_cwd(plan))
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        log.error("launch-new", launch_path.name, exc)
        return _fail(plan, ops, log, swap, home)
    log("launched", launch_path.name)
    deadline = ops.clock() + NEW_START_WAIT_SECONDS
    while True:
        if started_ok.exists():
            log("started-ok")
            _remove_backup(ops, log, swap)
            log("done")
            return EXIT_APPLIED
        if process.poll() is not None:
            if started_ok.exists():
                continue
            log("new-exited", str(process.poll()))
            return _fail(plan, ops, log, swap, home)
        if ops.clock() >= deadline:
            log("timeout-keep-backup")  # 控えは次の起動の掃除で消える
            return EXIT_APPLIED
        ops.sleep(POLL_SECONDS)


# --- 起動したときの印(gui_web から呼ぶ) ---


def _valid_staging(folder: Path, kind: str, target: Path | None, home: Path | None) -> bool:
    if not VERSION_DIR.fullmatch(folder.name) or folder.is_symlink() or not folder.is_dir():
        return False
    try:
        target = install_target(kind) if target is None else Path(target)
    except Exception:  # noqa: BLE001 - 入れ替える場所が分からなければ書かない
        return False
    return _same(folder.parent, staging_root(kind, target, home))


def write_started_ok(
    path: Path | str | None, *, kind: str | None = None, target: Path | None = None, home: Path | None = None
) -> bool:
    """名前が started-ok で、親が決まった準備の場所の中の X.Y.Z のフォルダーのときだけ、空のファイルを書く。書いたら真。"""
    if path is None:
        return False
    path = Path(path)
    kind = install_kind() if kind is None else kind
    if path.name != STARTED_OK or kind not in SUPPORTED_KINDS:
        return False
    if not _valid_staging(path.parent, kind, target, home):
        return False
    try:
        path.write_bytes(b"")
    except OSError:
        return False
    return True


def read_failed_log_arg(path: Any) -> str | None:
    """--update-failed の引数。名前が last-failed.log のときだけ、知らせに出す文字として返す(ほかは None)。"""
    if not isinstance(path, str) or not path or any(ord(ch) < 0x20 for ch in path):
        return None
    if Path(path).name != LAST_FAILED_LOG:
        return None
    return path
