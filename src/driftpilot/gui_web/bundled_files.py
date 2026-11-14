"""Windows の exe で、画面に要る同梱のファイルがそろっているかの確かめ(ADR-0192 / REQ-263)。

zip の展開が途中で止まると、PyInstaller が作った _internal のフォルダーの後ろのほうのファイルが欠ける。
pywebview はそれを読めず、表示部品の初期化の失敗や「WebView2 Runtime が入っていません」に見える。
このため、画面を開けなかったとき(gui_web/app.py の run)と selfcheck の画面の部品の項目で、固定の短い一覧を確かめる。

このモジュールは pywebview を読まない(selfcheck からも使う。gui_web/__init__.py と同じく pywebview 無しで読める)。
Windows の exe(sys.frozen が真で OS が Windows。ADR-0186 と同じ判定)のときだけ確かめる。
開発の環境・Mac の .app・PyInstaller の展開先(sys._MEIPASS)が無いときは、何も欠けていないものとする。

もう 1 つ、ブラウザで取った zip をエクスプローラーの「すべて展開」で開くと、中の全部のファイルに「インターネットから
取った」印(NTFS の代わりのデータの流れ Zone.Identifier)が付き、.NET Framework が Python.Runtime.dll などを読まない
(ADR-0196 / REQ-274)。このため、画面を開く前(SmartScreen の後)に、自分の同梱のフォルダー(sys._MEIPASS)の下のファイルの
印だけを外す。DriftPilot.exe・driftpilot-cli.exe 自身、settings\\、利用者のファイルには触らない。
"""

import os
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

# 「インターネットから取った」印の代わりのデータの流れの名前(ADR-0196)
ZONE_STREAM = ":Zone.Identifier"


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


def zone_mark_path(path: Path | str) -> str:
    """ファイルの「インターネットから取った」印(代わりのデータの流れ)のパス。ファイルのパスに ":Zone.Identifier" を足す(ADR-0196)。"""
    return str(path) + ZONE_STREAM


def marked_files(root: Path | str) -> list[str]:
    """root の下の固定の一覧(REQUIRED_FILES)のうち、印が付いているものを一覧の順で返す(名前は「/」区切りのまま)。

    ADR-0196 決定1: 印を外す前の確かめと、決定2: 画面を開けなかったときの知らせに使う。印が無いふつうの起動では、5 つを見るだけで済む。
    """
    base = Path(root)
    return [name for name in REQUIRED_FILES if os.path.exists(zone_mark_path(base.joinpath(*name.split("/"))))]


def unblock_tree(root: Path | str) -> int:
    """固定の一覧のどれかに印があるときだけ、root の下の全部のファイルの印を外し、外せた数を返す(ADR-0196 決定1)。

    固定の一覧に印が無ければ、何もせず 0(フォルダーを回らない)。外すのは各ファイルの代わりのデータの流れを消すこと
    (Unblock-File と同じ)。消せないファイル(流れが無い・権限が無い・読み取り専用など)は飛ばして続ける。
    回る途中の OSError でも止めず、それまでに外せた数を返す(外す処理のせいで起動を止めない)。
    """
    if not marked_files(root):
        return 0
    removed = 0
    try:
        for path in Path(root).rglob("*"):
            try:
                if not path.is_file():
                    continue
                os.remove(zone_mark_path(path))
            except OSError:
                continue
            removed += 1
    except OSError:
        pass
    return removed


def unblock_bundled_files() -> int:
    """自分の同梱のフォルダー(sys._MEIPASS)の印を外す(ADR-0196 決定1。画面を開く前に app.run が呼ぶ)。

    Windows の exe でないとき(同梱のフォルダーが無いとき)は何もせず 0。外せた数を返す。
    """
    base = _bundle_dir()
    return 0 if base is None else unblock_tree(base)


def marked_bundled_files() -> list[str]:
    """印が残っている同梱のファイル(固定の一覧の中から)。Windows の exe でないとき(確かめないとき)は空(ADR-0196 決定2)。"""
    base = _bundle_dir()
    return [] if base is None else marked_files(base)
