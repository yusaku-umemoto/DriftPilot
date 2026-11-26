"""ファイルをごみ箱へ移す(ADR-0253 決定3。REQ-386)。`pilot-call records-delete` の method が trash のときだけ使う。

move_to_trash(path) は、移せたら真、移せなかったら(その OS で使えない・OS が断った・例外)偽を返す。偽のとき、ファイルには何もしない
(呼ぶ側は完全には消さない)。依存ライブラリは増やさない(Mac は配るアプリに同梱の pyobjc の Foundation、Windows は ctypes)。

Windows の SHFileOperationW は、ごみ箱の無いドライブ(ネットワークの共有・一部の USB)やごみ箱に入らないファイルを、確かめなしに完全に消す。
そこで REQ-387 で、次の 3 つの守りを重ねる。どれかで止まれば SHFileOperationW を呼ばずに偽を返す。
1. ファイルのドライブの根(C:\\ など)が固定のディスク(DRIVE_FIXED)であること。UNC のパスは偽
2. その根にごみ箱があること(SHQueryRecycleBinW が S_OK)
3. FOF_WANTNUKEWARNING を足し、完全に消すことになる場合は Windows に利用者へ確かめさせる。止まったら偽(呼んだ後に元の場所に残っていても偽)
"""

import ntpath
import os
import re
import sys
from pathlib import Path, PureWindowsPath

# SHFileOperationW の決まった数(Windows)
FO_DELETE = 0x0003
FOF_SILENT = 0x0004
FOF_NOCONFIRMATION = 0x0010
FOF_ALLOWUNDO = 0x0040
FOF_NOERRORUI = 0x0400
FOF_WANTNUKEWARNING = 0x4000
# GetDriveTypeW の戻り値(固定のディスク)と、SHQueryRecycleBinW の成功の値(REQ-387)
DRIVE_FIXED = 3
S_OK = 0

_DRIVE_LETTER = re.compile(r"^[A-Za-z]:$")


def _platform() -> str:
    """OS の名前("darwin"・"win32" など)。テストで差し替える。"""
    return sys.platform


def move_to_trash(path: Path | str) -> bool:
    """path をごみ箱へ移す。移せたら真。"""
    target = str(path)
    system = _platform()
    try:
        if system == "darwin":
            return _trash_mac(target)
        if system.startswith("win"):
            return _trash_windows(target)
    except Exception:  # noqa: BLE001 - どんな失敗でも、移せなかった(偽)として呼ぶ側に返す
        return False
    return False


def _trash_mac(target: str) -> bool:
    try:
        from Foundation import NSFileManager, NSURL  # type: ignore[import-not-found]  # pyobjc。読み込めなければ偽
    except Exception:  # noqa: BLE001 - ImportError のほか、読み込みの途中の失敗も含める
        return False
    manager = NSFileManager.defaultManager()
    url = NSURL.fileURLWithPath_(target)
    result = manager.trashItemAtURL_resultingItemURL_error_(url, None, None)
    return bool(result[0])


def _fixed_drive_root(target: str) -> str | None:
    """ファイルの絶対パスのドライブの根(例 C:\\)。UNC・ドライブの無いパス・特別な書き方(\\\\?\\ など)は None。"""
    anchor = PureWindowsPath(ntpath.abspath(target)).anchor
    drive, root = ntpath.splitdrive(anchor)
    if not _DRIVE_LETTER.match(drive) or root != "\\":
        return None
    return drive + root


def _still_there(target: str) -> bool:
    """ファイルが元の場所にまだあるか。テストで差し替える。"""
    return os.path.lexists(target)


def _trash_windows(target: str) -> bool:
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_ushort),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    class SHQUERYRBINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("i64Size", ctypes.c_longlong),
            ("i64NumItems", ctypes.c_longlong),
        ]

    root = _fixed_drive_root(target)
    if root is None:  # UNC・ドライブの無いパス
        return False
    if ctypes.windll.kernel32.GetDriveTypeW(root) != DRIVE_FIXED:  # type: ignore[attr-defined]
        return False
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    info = SHQUERYRBINFO()
    info.cbSize = ctypes.sizeof(SHQUERYRBINFO)
    if shell32.SHQueryRecycleBinW(root, ctypes.byref(info)) != S_OK:
        return False

    operation = SHFILEOPSTRUCTW()
    operation.hwnd = None
    operation.wFunc = FO_DELETE
    operation.pFrom = target + "\0\0"
    operation.pTo = None
    operation.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_WANTNUKEWARNING
    operation.fAnyOperationsAborted = False
    operation.hNameMappings = None
    operation.lpszProgressTitle = None
    code = shell32.SHFileOperationW(ctypes.byref(operation))
    if code != 0 or operation.fAnyOperationsAborted:
        return False
    return not _still_there(target)  # 呼んだ後も元の場所にあるなら、移せていない
