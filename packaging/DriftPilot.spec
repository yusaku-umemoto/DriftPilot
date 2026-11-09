# -*- mode: python ; coding: utf-8 -*-
"""DriftPilot の exe（ADR-0036）。PyInstaller のフォルダー形式で、画面版とコマンド版を1つのフォルダーに入れる。

使い方（リポジトリの直下で。driftpilot を [gui,llm] つきで入れた環境から）:
    pyinstaller --noconfirm packaging/DriftPilot.spec

できるもの:
    Windows: dist/DriftPilot/DriftPilot.exe（画面。コンソールの窓を出さない）と driftpilot-cli.exe（コマンド）
    Mac:     dist/DriftPilot.app（画面）。コマンド版は DriftPilot.app/Contents/MacOS/driftpilot-cli

名前を大文字・小文字の違いだけにしない（Mac も Windows も区別しないのでぶつかる。ADR-0036 決定3）。
アイコンは packaging/icons/（ADR-0173）。
"""

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

HERE = SPECPATH  # この spec のあるフォルダー（packaging/）
ICONS = f"{HERE}/icons"
ICON_WINDOWS = f"{ICONS}/DriftPilot.ico"   # Windows の exe（ADR-0173 決定5）
ICON_MAC = f"{ICONS}/DriftPilot.icns"      # Mac の .app（ADR-0173 決定5）

# 同梱するデータ（ADR-0036 決定4）。driftpilot の表と、show の読み取りのテンプレート。
# pywebview の画面の static/（REQ-103）も、pyproject.toml の package-data に書いたので collect_data_files("driftpilot") で入る
datas = collect_data_files("driftpilot") + collect_data_files("ntc_templates")
# 起動時に自分の版を読む部品があるので、版の情報（メタデータ）も入れる（試しのビルドで ntc_templates が起動しなかった）
# pywebview は selfcheck が版を読む（webview に __version__ が無い。REQ-103）
for dist in ("keyring", "ntc_templates", "driftpilot", "hier_config", "textfsm", "openai", "pywebview"):
    datas += copy_metadata(dist)
# keyring は使う保管庫の部品を後から探すので、明示して入れる
hiddenimports = collect_submodules("keyring")
# pywebview の画面（driftpilot.gui_web と webview。REQ-103）は、画面版の解析では gui_main.py の先頭の import から、
# コマンド版の解析では CLI と selfcheck の関数の中の import から入る（REQ-107）。
# pywebview の部品（js/、Windows の WebView2 の DLL）は、pywebview 自身の PyInstaller の設定（hook-webview.py）で入る


def analysis(script):
    return Analysis(
        [f"{HERE}/{script}"],
        datas=datas,
        hiddenimports=hiddenimports,
        noarchive=False,
    )


gui = analysis("gui_main.py")
cli = analysis("cli_main.py")

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name="DriftPilot",
    console=False,  # 画面版はコンソールの窓を出さない
    # Mac の EXE にアイコンを渡すと、PyInstaller が Pillow で変換しようとして止まる。Mac は BUNDLE で付ける
    icon=ICON_WINDOWS if sys.platform == "win32" else None,
)
cli_exe = EXE(
    PYZ(cli.pure),
    cli.scripts,
    [],
    exclude_binaries=True,
    name="driftpilot-cli",
    console=True,
    icon=ICON_WINDOWS if sys.platform == "win32" else None,
)

# 部品は2つの実行ファイルで共有する（2つに分けると大きさが倍になる。ADR-0036 決定3）
coll = COLLECT(
    gui_exe, gui.binaries, gui.datas,
    cli_exe, cli.binaries, cli.datas,
    name="DriftPilot",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="DriftPilot.app",
        bundle_identifier=None,
        icon=ICON_MAC,
    )
