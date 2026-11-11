"""入れ替えの準備と起動時の掃除(ADR-0188 決定3 / REQ-253 §5・§6・§7)。今の版が行う。

- 入れ替える場所: windows_zip は DriftPilot.exe があるフォルダー(portable_settings_dir() の親)、mac_app は動いている .app
  (sys.executable の 3 つ上)
- 準備のフォルダー: Windows は <場所>\\_update\\<新しい版>\\(同じドライブ)、Mac は ~/Library/Caches/DriftPilot/update/<新しい版>/
- 流れ: 始める前の確かめ → SHA256SUMS.txt と .sig → 署名と一覧(verify_listing) → 配る物 → 大きさと指紋(verify_file)
  → 展開(Windows は zip を new/ へ、Mac は dmg から new/DriftPilot.app へ) → plan.json。
  署名が合わなければ配る物は取ってこない。1 つでも違えば、その後に進まない(展開もしない)
- やめた・失敗したときは準備のフォルダーを消す
- 新しい版の起動(start_applier)は、確かめの窓で「入れ替える」が押されたときだけ画面から呼ぶ
- 掃除(cleanup_stale)は、配った物で動いているときだけ、10 分より古い準備のフォルダーと控えを消す。消せなくても誤りにしない。
  last-failed.log(ファイル)は消さない

取ってくる・展開する・起動する・待つ関数は差し替えられる(テストは偽物。本物のネットワーク・DriftPilot・hdiutil は動かさない)。
sys.executable を読むのは _executable だけ(テストはここを差し替えるか、target を渡す)。
"""

import json
import os
import platform as _platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftpilot import __version__
from driftpilot.llm_settings import portable_settings_dir
from driftpilot.messages import UPDATE_NO_ASSET_MAC, update_is_latest, update_not_writable
from driftpilot.update import download as _download
from driftpilot.update.check import (
    INSTALL_MAC_APP,
    INSTALL_WINDOWS_ZIP,
    STATE_NEWER,
    Asset,
    UpdateStatus,
    install_kind,
    parse_version,
)
from driftpilot.update.errors import (
    ContentError,
    IntegrityError,
    NoAssetError,
    NotNewerError,
    NotWritableError,
    UpdateCancelled,
    UpdateError,
    WrongLocationError,
)
from driftpilot.update.keys import TRUSTED_KEYS
from driftpilot.update.verify import SIG_NAME, SUMS_NAME, verify_file, verify_listing

# 決まった名前と値(REQ-253 §2・§5・§6)
UPDATE_DIR = "_update"
MAC_CACHE_PARTS = ("Library", "Caches", "DriftPilot", "update")
NEW_DIR = "new"
PLAN_NAME = "plan.json"
APPLIER_READY = "applier-ready"
STARTED_OK = "started-ok"
UPDATE_LOG = "update.log"
LAST_FAILED_LOG = "last-failed.log"
APPLIER_WAIT_SECONDS = 30
APPLIER_POLL_SECONDS = 0.2
STALE_SECONDS = 10 * 60
MAC_ARCH = "arm64"
SUPPORTED_KINDS = (INSTALL_WINDOWS_ZIP, INSTALL_MAC_APP)
VERSION_DIR = re.compile(r"\d+\.\d+\.\d+")
WINDOWS_BACKUP_DIR = re.compile(r"backup-\d+\.\d+\.\d+")
MAC_BACKUP_APP = re.compile(r"\.DriftPilot-\d+\.\d+\.\d+-backup\.app")

# 段(progress の 1 つ目)。画面が進み具合の文にする
STAGE_DOWNLOADING = "downloading"
STAGE_VERIFYING = "verifying"
STAGE_PREPARING = "preparing"

# Windows の新しいプロセスの作り方(今の版と切り離す)。Mac でもテストできるよう、無ければ決まった数を使う
DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

Progress = Callable[[str, int, int], None]


@dataclass(frozen=True)
class PreparedUpdate:
    """準備ができた更新。plan_path は準備のフォルダーの plan.json。"""

    to_version: str
    kind: str
    target: Path
    staging: Path
    plan_path: Path


def _executable() -> str:
    """動いている実行ファイルのパス。sys.executable を読むのはここだけ。"""
    return sys.executable


def install_target(kind: str) -> Path:
    """入れ替える場所。windows_zip は portable_settings_dir() の親、mac_app は sys.executable の 3 つ上(.app)。"""
    if kind == INSTALL_WINDOWS_ZIP:
        portable = portable_settings_dir()
        if portable is None:
            raise NoAssetError
        return portable.parent
    if kind == INSTALL_MAC_APP:
        return Path(_executable()).resolve().parents[2]
    raise NoAssetError


def staging_root(kind: str, target: Path, home: Path | None = None) -> Path:
    """準備のフォルダーを置く場所(この中に <新しい版>/ を作る)。"""
    if kind == INSTALL_WINDOWS_ZIP:
        return Path(target) / UPDATE_DIR
    return (Path.home() if home is None else Path(home)).joinpath(*MAC_CACHE_PARTS)


def last_failed_log(kind: str, target: Path, home: Path | None = None) -> Path:
    """失敗のときに記録を写す場所(Windows は <場所>\\_update\\last-failed.log、Mac は準備の場所の last-failed.log)。"""
    return staging_root(kind, target, home) / LAST_FAILED_LOG


def applier_executable(kind: str, staging: Path) -> Path:
    """準備のフォルダーの新しい版の実行ファイル(入れ替える側として起動する)。"""
    if kind == INSTALL_WINDOWS_ZIP:
        return Path(staging) / NEW_DIR / "DriftPilot" / "DriftPilot.exe"
    return Path(staging) / NEW_DIR / _download.APP_NAME / Path(*_download.APP_BINARY)


def _find(assets: tuple[Asset, ...], name: str) -> Asset | None:
    for asset in assets:
        if asset.name == name:
            return asset
    return None


def _is_mac_wrong_place(target: Path) -> bool:
    text = str(target)
    return text.startswith("/Volumes/") or "AppTranslocation" in text


def _check_writable(kind: str, target: Path) -> None:
    """入れ替える場所に書けるか。Windows は _update を作り、その中に試しのフォルダーを作って消す。Mac は .app の親に試しのファイル。"""
    try:
        if kind == INSTALL_WINDOWS_ZIP:
            root = Path(target) / UPDATE_DIR
            root.mkdir(exist_ok=True)
            probe = Path(tempfile.mkdtemp(prefix=".probe-", dir=root))
            probe.rmdir()
        else:
            with tempfile.NamedTemporaryFile(prefix=".driftpilot-probe-", dir=Path(target).parent):
                pass
    except OSError:
        raise NotWritableError(update_not_writable(str(target))) from None


@dataclass(frozen=True)
class _Assets:
    package: Asset
    sums: Asset
    sig: Asset


def check_can_start(status: UpdateStatus, kind: str, target: Path, *, machine: str) -> _Assets:
    """始める前に止める場合を、次の順に確かめる。止めるときは UpdateError の子を上げる。

    1. 今の版より新しいか(NotNewerError) 2. Mac で /Volumes/ の下・AppTranslocation か(WrongLocationError)
    3. Mac で arm64 か(NoAssetError の Mac の文) 4. 配る物・SHA256SUMS.txt・.sig が名前のとおりあるか(NoAssetError)
    5. 大きさの上限(IntegrityError) 6. 書けるか(NotWritableError)
    """
    if kind not in SUPPORTED_KINDS:
        raise NoAssetError
    current = parse_version(__version__)
    latest = parse_version(status.latest)
    if status.state != STATE_NEWER or current is None or latest is None or latest <= current:
        raise NotNewerError(update_is_latest(__version__))
    if kind == INSTALL_MAC_APP:
        if _is_mac_wrong_place(Path(target)):
            raise WrongLocationError
        if machine != MAC_ARCH:
            raise NoAssetError(UPDATE_NO_ASSET_MAC)
    version = ".".join(str(part) for part in latest)
    package = _find(status.assets, _download.asset_name(kind, version))
    sums = _find(status.assets, SUMS_NAME)
    sig = _find(status.assets, SIG_NAME)
    if package is None or sums is None or sig is None:
        raise NoAssetError
    for asset, limit in (
        (package, _download.ASSET_MAX_BYTES),
        (sums, _download.SUMS_MAX_BYTES),
        (sig, _download.SIG_MAX_BYTES),
    ):
        if asset.size <= 0 or asset.size > limit:
            raise IntegrityError
    _check_writable(kind, Path(target))
    return _Assets(package, sums, sig)


def _tell(progress: Progress | None, stage: str, done: int = 0, total: int = 0) -> None:
    if progress is not None:
        progress(stage, done, total)


def _check_cancel(cancel: threading.Event | None) -> None:
    if cancel is not None and cancel.is_set():
        raise UpdateCancelled


def prepare_update(
    status: UpdateStatus,
    *,
    cancel: threading.Event | None = None,
    progress: Progress | None = None,
    kind: str | None = None,
    target: Path | None = None,
    pid: int | None = None,
    home: Path | None = None,
    keys: Mapping[str, bytes] = TRUSTED_KEYS,
    download: Callable[..., Any] = _download.download,
    extract_zip: Callable[[Path, Path], Any] = _download.extract_zip,
    copy_app_from_dmg: Callable[[Path, Path], Any] = _download.copy_app_from_dmg,
    machine: str | None = None,
) -> PreparedUpdate:
    """取ってきて確かめ、展開し、plan.json を書く(REQ-253 §5)。

    段が変わるたびに progress(段, 取れたバイト数, 全体のバイト数) を呼ぶ。段は downloading・verifying・preparing。
    例外(「やめる」を含む)のときは準備のフォルダーを消してから上げ直す。UpdateError でない思いがけない例外は ContentError。
    """
    kind = install_kind() if kind is None else kind
    if kind not in SUPPORTED_KINDS:
        raise NoAssetError
    target = install_target(kind) if target is None else Path(target)
    machine = _platform.machine() if machine is None else machine
    found = check_can_start(status, kind, target, machine=machine)
    version = status.latest or ""
    staging = staging_root(kind, target, home) / version
    try:
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        package = found.package
        _tell(progress, STAGE_DOWNLOADING, 0, package.size)
        sums_path = download(found.sums.browser_download_url, staging / SUMS_NAME, found.sums.size, cancel=cancel)
        sig_path = download(found.sig.browser_download_url, staging / SIG_NAME, found.sig.size, cancel=cancel)
        _check_cancel(cancel)
        _tell(progress, STAGE_VERIFYING, 0, package.size)
        key, digest = verify_listing(Path(sums_path).read_bytes(), Path(sig_path).read_bytes(), package.name, keys)
        _tell(progress, STAGE_DOWNLOADING, 0, package.size)
        package_path = download(
            package.browser_download_url,
            staging / package.name,
            package.size,
            cancel=cancel,
            progress=lambda done: _tell(progress, STAGE_DOWNLOADING, done, package.size),
        )
        _check_cancel(cancel)
        _tell(progress, STAGE_VERIFYING, package.size, package.size)
        verify_file(package_path, package.size, digest)
        _check_cancel(cancel)
        _tell(progress, STAGE_PREPARING, package.size, package.size)
        new = staging / NEW_DIR
        if kind == INSTALL_WINDOWS_ZIP:
            extract_zip(Path(package_path), new)
        else:
            new.mkdir()
            copy_app_from_dmg(Path(package_path), new / _download.APP_NAME)
        _check_cancel(cancel)
        plan = {
            "from_version": __version__,
            "to_version": version,
            "kind": kind,
            "target": str(target),
            "staging": str(staging),
            "pid": os.getpid() if pid is None else pid,
            "key": key,
        }
        plan_path = staging / PLAN_NAME
        plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except BaseException as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, (UpdateError, UpdateCancelled)) or not isinstance(exc, Exception):
            raise
        raise ContentError from None
    return PreparedUpdate(version, kind, target, staging, plan_path)


def discard(prepared: PreparedUpdate | None) -> None:
    """準備のフォルダーを消す(確かめの窓の「やめる」と、新しい版が始まらなかったとき)。消せなくても誤りにしない。"""
    if prepared is not None:
        shutil.rmtree(prepared.staging, ignore_errors=True)


def start_applier(
    prepared: PreparedUpdate,
    *,
    popen: Callable[..., Any] = subprocess.Popen,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    timeout: float = APPLIER_WAIT_SECONDS,
) -> bool:
    """準備のフォルダーの新しい版を --apply-update <plan.json> で起動し、applier-ready が書かれるのを待つ(0.2 秒ごと、30 秒まで)。

    書かれたら真。起動できない・先に終わった・時間切れは偽(時間切れのときは起動したものを止める)。
    今の版と切り離して起動する(Windows は DETACHED_PROCESS、Mac は start_new_session)。shell は使わない。
    """
    argv = [str(applier_executable(prepared.kind, prepared.staging)), "--apply-update", str(prepared.plan_path)]
    options: dict[str, Any] = {
        "cwd": str(prepared.staging),
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if prepared.kind == INSTALL_WINDOWS_ZIP:
        options["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    try:
        process = popen(argv, **options)
    except (OSError, ValueError, subprocess.SubprocessError):
        return False
    ready = prepared.staging / APPLIER_READY
    deadline = clock() + timeout
    while True:
        if ready.exists():
            return True
        if process.poll() is not None:
            return ready.exists()
        if clock() >= deadline:
            try:
                process.kill()
            except Exception:  # noqa: BLE001 - 止められなくても、入れ替える側は今の版が閉じるのを待って諦める
                pass
            return False
        sleep(APPLIER_POLL_SECONDS)


def _stale_candidates(kind: str, target: Path, home: Path | None) -> list[Path]:
    found: list[Path] = []
    root = staging_root(kind, target, home)
    if root.is_dir():
        for child in root.iterdir():
            if child.is_symlink() or not child.is_dir():
                continue
            if VERSION_DIR.fullmatch(child.name) or (
                kind == INSTALL_WINDOWS_ZIP and WINDOWS_BACKUP_DIR.fullmatch(child.name)
            ):
                found.append(child)
    if kind == INSTALL_MAC_APP and Path(target).parent.is_dir():
        for child in Path(target).parent.iterdir():
            if MAC_BACKUP_APP.fullmatch(child.name) and child.is_dir() and not child.is_symlink():
                found.append(child)
    return found


def cleanup_stale(
    *,
    kind: str | None = None,
    target: Path | None = None,
    home: Path | None = None,
    now: float | None = None,
) -> list[Path]:
    """配った物で動いているときだけ、10 分より古い(更新の時刻で見る)準備のフォルダーと控えを消す。消したものを返す。

    消すのは、準備の場所の X.Y.Z のフォルダー、Windows の _update の backup-X.Y.Z、Mac の .app の隣の
    .DriftPilot-X.Y.Z-backup.app だけ。ファイル(last-failed.log など)は消さない。消せなくても誤りにしない。
    """
    kind = install_kind() if kind is None else kind
    if kind not in SUPPORTED_KINDS:
        return []
    now = time.time() if now is None else now
    removed: list[Path] = []
    try:
        target = install_target(kind) if target is None else Path(target)
        candidates = _stale_candidates(kind, target, home)
    except (OSError, UpdateError):
        return []
    for path in candidates:
        try:
            if now - path.lstat().st_mtime <= STALE_SECONDS:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            removed.append(path)
    return removed
