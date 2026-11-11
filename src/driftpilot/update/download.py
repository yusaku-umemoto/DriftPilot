"""配る物を取ってくる・安全に展開する(ADR-0188 決定3 / REQ-253 §2・§4)。

- 取ってくるのは DOWNLOAD_PREFIX で始まる URL だけ(GitHub の Release の配る物)。GET だけで、認証のトークンは使わない。
  TLS は llm_analyzer.http_json.ssl_context、プロキシは urllib の既定、転送先(リダイレクト)は https:// だけ(HttpsOnlyRedirect)。
  1 回の読みの待ち時間は 30 秒。dest の隣の .part に書き、大きさが expected_size と同じときだけ dest に名前を変える
- つなぐ関数(urlopen)は差し替えられる(テストは偽物。本物のネットワークにつながない)
- extract_zip は、先に全部の項目を確かめ、1 つでも当たれば何も書かずに ContentError
- copy_app_from_dmg は hdiutil と ditto を引数の一覧で呼ぶ(shell は使わない)。呼ぶ関数(run)は差し替えられる
"""

import http.client
import os
import re
import shutil
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import zipfile
import zlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

from driftpilot import __version__
from driftpilot.llm_analyzer.http_json import ssl_context
from driftpilot.messages import UPDATE_REASON_UNREACHABLE
from driftpilot.update.check import INSTALL_MAC_APP, INSTALL_WINDOWS_ZIP, _http_reason
from driftpilot.update.errors import ContentError, DownloadError, IntegrityError, UpdateCancelled

# 決まった値(REQ-253 §2)
DOWNLOAD_PREFIX = "https://github.com/yusaku-umemoto/DriftPilot/releases/download/"
ASSET_NAMES = {
    INSTALL_WINDOWS_ZIP: "DriftPilot-{version}-windows-x64.zip",
    INSTALL_MAC_APP: "DriftPilot-{version}-macos-arm64.dmg",
}
ASSET_NAME_PATTERN = re.compile(r"DriftPilot-(\d+\.\d+\.\d+)-(?:windows-x64\.zip|macos-arm64\.dmg)")
ASSET_MAX_BYTES = 1024**3  # 配る物は 1 GiB まで
SUMS_MAX_BYTES = 64 * 1024  # SHA256SUMS.txt は 64 KiB まで
SIG_MAX_BYTES = 1024  # SHA256SUMS.txt.sig は 1 KiB まで
ZIP_MAX_ENTRIES = 20000
ZIP_MAX_TOTAL_BYTES = 2 * 1024**3
READ_TIMEOUT_SECONDS = 30
CHUNK_BYTES = 64 * 1024
PART_SUFFIX = ".part"

# zip の中の形(packaging/build_windows.ps1。先頭は DriftPilot/)
ZIP_ROOT = "DriftPilot"
ZIP_EXE = "DriftPilot/DriftPilot.exe"
ZIP_FORBIDDEN_TOP = ("settings", "_update")  # 利用者の設定と更新の作業の場所(配る物に入っていてはいけない)
_S_IFMT = 0o170000
_S_IFLNK = 0o120000

# dmg の中の形(packaging/build_mac.sh。DriftPilot.app)
APP_NAME = "DriftPilot.app"
APP_BINARY = ("Contents", "MacOS", "DriftPilot")
HDIUTIL = "/usr/bin/hdiutil"
DITTO = "/usr/bin/ditto"

Urlopen = Callable[[urllib.request.Request, float], Any]
Run = Callable[..., Any]


def asset_name(kind: str, version: str) -> str:
    """その入れ方の配る物の名前(REQ-253 §2)。"""
    return ASSET_NAMES[kind].format(version=version)


def is_download_url(url: Any) -> bool:
    """取ってきてよい URL か(DOWNLOAD_PREFIX で始まり、空白・制御文字を含まない)。"""
    return (
        isinstance(url, str)
        and url.startswith(DOWNLOAD_PREFIX)
        and not any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url)
    )


class HttpsOnlyRedirect(urllib.request.HTTPRedirectHandler):
    """転送先(リダイレクト)は https:// だけ許す。ほかは HTTPError にする(つながない)。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ANN201 - urllib の形のまま
        if not isinstance(newurl, str) or not newurl.lower().startswith("https://"):
            raise urllib.error.HTTPError(req.full_url, code, "https でない転送先には進みません", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_opener() -> urllib.request.OpenerDirector:
    """既定のつなぎ方。TLS は ssl_context、転送先は https:// だけ、プロキシは urllib の既定。"""
    return urllib.request.build_opener(urllib.request.HTTPSHandler(context=ssl_context()), HttpsOnlyRedirect)


def default_urlopen(request: urllib.request.Request, timeout: float) -> Any:
    """既定の urlopen(build_opener で組む)。"""
    return build_opener().open(request, timeout=timeout)


def request_headers() -> dict[str, str]:
    """送るヘッダー。中身として送るのは DriftPilot の版(User-Agent)だけ。"""
    return {"User-Agent": f"DriftPilot/{__version__}", "Accept": "application/octet-stream"}


def _remove_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def download(
    url: str,
    dest: Path | str,
    expected_size: int,
    *,
    progress: Callable[[int], None] | None = None,
    cancel: threading.Event | None = None,
    urlopen: Urlopen | None = None,
    timeout: float = READ_TIMEOUT_SECONDS,
) -> Path:
    """url を GET して dest に書き、dest を返す。

    url が DOWNLOAD_PREFIX で始まらなければ、つながずに IntegrityError。dest の隣の .part に 64 KiB ずつ書き、
    塊ごとに cancel(立っていれば UpdateCancelled)を見て、progress(取れたバイト数)を呼ぶ。expected_size を超えたら
    その場で止める。読み終えて大きさが expected_size と同じときだけ dest に名前を変える(ほかは .part を消し、dest は作らない)。
    HTTP の誤り・つながらない・時間切れは DownloadError(文は REQ-252 の理由の文)。
    """
    if not is_download_url(url):
        raise IntegrityError
    if not isinstance(expected_size, int) or isinstance(expected_size, bool) or expected_size < 0:
        raise IntegrityError
    dest = Path(dest)
    part = dest.with_name(dest.name + PART_SUFFIX)
    opener = urlopen or default_urlopen
    request = urllib.request.Request(url, headers=request_headers(), method="GET")
    received = 0
    try:
        if cancel is not None and cancel.is_set():
            raise UpdateCancelled
        with opener(request, timeout) as response:
            final = response.geturl() if hasattr(response, "geturl") else url
            if not isinstance(final, str) or not final.lower().startswith("https://"):
                raise DownloadError(UPDATE_REASON_UNREACHABLE)
            with open(part, "wb") as out:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise UpdateCancelled
                    chunk = response.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > expected_size:
                        raise IntegrityError
                    out.write(chunk)
                    if progress is not None:
                        progress(received)
    except BaseException as exc:
        _remove_quietly(part)
        if isinstance(exc, urllib.error.HTTPError):  # URLError の子なので先に見る
            raise DownloadError(_http_reason(exc.code)) from None
        if isinstance(exc, (urllib.error.URLError, OSError, http.client.HTTPException)):
            raise DownloadError(UPDATE_REASON_UNREACHABLE) from None  # 時間切れ(TimeoutError)も OSError
        raise
    if received != expected_size:
        _remove_quietly(part)
        raise IntegrityError
    try:
        os.replace(part, dest)
    except OSError:
        _remove_quietly(part)
        raise ContentError from None
    return dest


# --- zip(Windows) ---


def _entry_problem(info: zipfile.ZipInfo) -> bool:
    """項目の名前・種類が許されない形か(真なら許さない)。"""
    for name in (info.filename, info.orig_filename):
        if not name or "\\" in name or ":" in name or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in name):
            return True
        if name.startswith("/"):
            return True
    name = info.filename
    if not name.startswith(f"{ZIP_ROOT}/"):
        return True
    parts = name[:-1].split("/") if name.endswith("/") else name.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return True
    if len(parts) >= 2 and parts[1].casefold() in ZIP_FORBIDDEN_TOP:
        return True
    if ((info.external_attr >> 16) & _S_IFMT) == _S_IFLNK:  # シンボリックリンク
        return True
    if info.flag_bits & 0x1:  # 暗号にした項目
        return True
    return False


def check_zip_entries(infos: list[zipfile.ZipInfo]) -> None:
    """全部の項目を確かめる。1 つでも当たれば ContentError(REQ-253 §4)。"""
    if len(infos) > ZIP_MAX_ENTRIES:
        raise ContentError
    total = 0
    seen: set[str] = set()
    has_exe = False
    for info in infos:
        if _entry_problem(info):
            raise ContentError
        key = info.filename.rstrip("/").casefold()  # Windows に合わせて大文字と小文字は区別しない
        if key in seen:
            raise ContentError
        seen.add(key)
        total += info.file_size
        if total > ZIP_MAX_TOTAL_BYTES:
            raise ContentError
        if info.filename == ZIP_EXE and not info.is_dir():
            has_exe = True
    if not has_exe:
        raise ContentError


def extract_zip(zip_path: Path | str, dest: Path | str) -> Path:
    """zip を dest(まだ無いフォルダー)に展開し、dest を返す。中身は dest/DriftPilot/… になる。

    先に全部の項目を確かめ、1 つでも当たれば何も書かずに ContentError。展開の途中の誤り(CRC が違う・読めない・
    合計の大きさの上限を超えた)は、展開した dest を消してから ContentError。
    """
    dest = Path(dest)
    try:
        archive = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError):
        raise ContentError from None
    with archive:
        try:
            infos = archive.infolist()
        except (zipfile.BadZipFile, ValueError):
            raise ContentError from None
        check_zip_entries(infos)
        if dest.exists():
            raise ContentError
        written = 0
        try:
            dest.mkdir(parents=True)
            for info in infos:
                parts = info.filename.rstrip("/").split("/")
                target = dest.joinpath(*parts)
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source, open(target, "xb") as out:
                    while chunk := source.read(CHUNK_BYTES):
                        written += len(chunk)
                        if written > ZIP_MAX_TOTAL_BYTES:
                            raise ContentError
                        out.write(chunk)
        except ContentError:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        except (zipfile.BadZipFile, OSError, ValueError, EOFError, zlib.error, NotImplementedError, RuntimeError):
            shutil.rmtree(dest, ignore_errors=True)
            raise ContentError from None
    return dest


# --- dmg(Mac) ---


def _run_checked(run: Run, argv: list[str]) -> None:
    """外のコマンドを引数の一覧で呼ぶ(shell は使わない)。失敗は ContentError。"""
    try:
        result = run(argv, check=False, capture_output=True)
    except (OSError, subprocess.SubprocessError):
        raise ContentError from None
    if getattr(result, "returncode", 1) != 0:
        raise ContentError


def _detach(run: Run, mount: Path) -> None:
    """hdiutil detach。失敗したら -force でもう一度。どちらの失敗も例外にしない。"""
    for argv in ([HDIUTIL, "detach", str(mount)], [HDIUTIL, "detach", "-force", str(mount)]):
        try:
            result = run(argv, check=False, capture_output=True)
        except (OSError, subprocess.SubprocessError):
            continue
        if getattr(result, "returncode", 1) == 0:
            return


def copy_app_from_dmg(
    dmg: Path | str,
    dest_app: Path | str,
    *,
    run: Run = subprocess.run,
    mkdtemp: Callable[[], str] = lambda: tempfile.mkdtemp(prefix="driftpilot-dmg-"),
) -> Path:
    """dmg を読むだけでつなぎ(attach)、中の DriftPilot.app を ditto で dest_app に写し、外す(detach)。dest_app を返す。

    中に DriftPilot.app(リンクでないフォルダー)が無い・写した後に Contents/MacOS/DriftPilot が無い・コマンドの失敗は ContentError。
    """
    dest_app = Path(dest_app)
    mount = Path(mkdtemp())
    attached = False
    try:
        _run_checked(
            run,
            [HDIUTIL, "attach", "-nobrowse", "-noautoopen", "-readonly", "-mountpoint", str(mount), str(dmg)],
        )
        attached = True
        source = mount / APP_NAME
        if source.is_symlink() or not source.is_dir():
            raise ContentError
        _run_checked(run, [DITTO, str(source), str(dest_app)])
        binary = dest_app.joinpath(*APP_BINARY)
        if binary.is_symlink() or not binary.is_file():
            raise ContentError
    except ContentError:
        shutil.rmtree(dest_app, ignore_errors=True)
        raise
    finally:
        if attached:
            _detach(run, mount)
        try:
            mount.rmdir()
        except OSError:
            pass
    return dest_app
