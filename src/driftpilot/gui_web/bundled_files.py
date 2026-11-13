"""Windows の exe で、画面に要る同梱のファイルがそろっているかの確かめ(ADR-0192 / REQ-263)。

zip の展開が途中で止まると、PyInstaller が作った _internal のフォルダーの後ろのほうのファイルが欠ける。
pywebview はそれを読めず、表示部品の初期化の失敗や「WebView2 Runtime が入っていません」に見える。
このため、画面を開けなかったとき(gui_web/app.py の run)と selfcheck の画面の部品の項目で、固定の短い一覧を確かめる。

このモジュールは pywebview を読まない(selfcheck からも使う。gui_web/__init__.py と同じく pywebview 無しで読める)。
Windows の exe(sys.frozen が真で OS が Windows。ADR-0186 と同じ判定)のときだけ確かめる。
開発の環境・Mac の .app・PyInstaller の展開先(sys._MEIPASS)が無いときは、何も欠けていないものとする。
"""

import sys
from pathlib import Path, PureWindowsPath

from driftpilot import llm_settings

# 確かめるファイル(sys._MEIPASS からの相対パス。区切りは「/」。ADR-0192 決定2)。この順で最初に欠けたものを知らせる
REQUIRED_FILES: tuple[str, ...] = (
    "pythonnet/runtime/Python.Runtime.dll",
    "clr_loader/ffi/dlls/amd64/ClrLoader.dll",
    "webview/lib/Microsoft.Web.WebView2.Core.dll",
    "webview/lib/Microsoft.Web.WebView2.WinForms.dll",
    "webview/lib/runtimes/win-x64/native/WebView2Loader.dll",
)


def _bundle_dir() -> Path | None:
    """確かめる場所(sys._MEIPASS)。Windows の exe でないとき、または _MEIPASS が無いときは None。

    sys._MEIPASS を読むのはここだけ(テストはこの関数か sys._MEIPASS を差し替える)。
    """
    frozen, _executable, platform = llm_settings._runtime()
    if not (frozen and platform.startswith("win")):
        return None
    base = getattr(sys, "_MEIPASS", None)
    return None if base is None else Path(base)


def missing_files(root: Path | str) -> list[str]:
    """root の下に無いファイルを、REQUIRED_FILES の順で返す(名前は「/」区切りのまま)。"""
    base = Path(root)
    return [name for name in REQUIRED_FILES if not base.joinpath(*name.split("/")).is_file()]


def missing_bundled_files() -> list[str]:
    """欠けている同梱のファイル。Windows の exe でないとき(確かめないとき)は空。"""
    base = _bundle_dir()
    return [] if base is None else missing_files(base)


def windows_name(name: str) -> str:
    """名前の区切りを Windows の「\\」にする(例: pythonnet\\runtime\\Python.Runtime.dll)。"""
    return str(PureWindowsPath(*name.split("/")))
