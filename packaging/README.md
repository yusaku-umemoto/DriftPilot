# packaging/ — DriftPilot の exe（Windows）と dmg（Mac）を作る

決めたこと（道具・形式・中身・配り方）は **ADR-0036**。ここは手順だけ。製品のコードではない。

## 作る

| OS | コマンド（リポジトリの直下で） | できるもの |
|---|---|---|
| Windows | `powershell -NoProfile -ExecutionPolicy Bypass -File packaging\build_windows.ps1` | `dist\DriftPilot\`（`DriftPilot.exe`・`driftpilot-cli.exe`）と `dist\DriftPilot-<版>-windows-x64.zip` |
| Mac | `packaging/build_mac.sh` | `dist/DriftPilot.app` と `dist/DriftPilot-<版>-macos-<arch>.dmg` |

- 要るもの: **Python 3.14 系**（Windows は python.org の x64 版。Mac は共有ライブラリつきのもの。開発環境の `.venv` があれば、その元の Python を使う）。ネットにつながること（部品を入れるため）
- スクリプトは毎回 `build/pkg-venv` を作り直し、**`constraints.txt` の版**（テストで確かめた版）で入れる
- 途中で**中身の確かめ**（`smoke_test.sh` / `smoke_test.ps1`）をする。失敗したら止まる
- **Windows では、DriftPilot を閉じてから**作る（起動中だと上書きに失敗する）
- 署名はしない（ADR-0036 決定7）。`build/` と `dist/` は git に入らない（`.gitignore`）
- 画面は pywebview（表示部品は Windows が WebView2、Mac が WKWebView。ADR-0074）。Windows 10 などで WebView2 Runtime が入っていない PC では、
  画面版は「WebView2 Runtime が入っていません」の窓を出して止まる（Windows 11 には入っている）

## ファイル

| ファイル | 役目 |
|---|---|
| `DriftPilot.spec` | PyInstaller の指定（2つの実行ファイルで部品を共有する。同梱するデータと版の情報） |
| `gui_main.py` | 画面版の入口（pywebview の比較の画面。`driftpilot gui` と同じ。開けないときは理由を OS のメッセージの窓で出す） |
| `cli_main.py` | コマンド版の入口 |
| `requirements-build.txt` | ビルドの道具の版（PyInstaller など） |
| `constraints.txt` | 同梱する部品の版（開発環境の `.venv` と同じ） |
| `runtime_packages.py` | 同梱する部品の名前（ライセンスの文を集める対象） |
| `smoke_test.sh` / `smoke_test.ps1` | できたコマンド版の中身の確かめ（通信しない・キーに触らない） |
| `README-first.txt` | 配るものに入れる「はじめにお読みください」 |
| `build_mac.sh` / `build_windows.ps1` | 上をまとめて流すスクリプト |
| icons/ | アプリのアイコン（Mac の .icns、Windows の .ico と、その元。作り直し方は icons/README.md。ADR-0173） |

`.ps1` は **BOM 付きの UTF-8** で保存する（Windows PowerShell 5.1 は BOM が無いと日本語を cp932 として読み、壊れる）。

## 版を上げるとき

- 製品の依存（`pyproject.toml`）を変えて `.venv` を入れ直したら、`constraints.txt` を作り直す:
  `.venv/bin/python -m pip freeze`（Windows は `.venv\Scripts\python.exe -m pip freeze`）から、
  `-e`・`driftpilot`・テストだけの部品（`pytest` など）を除いたもの
- Windows だけで使う部品（pywebview の依存の `pythonnet`・`clr_loader`・`cffi`・`pycparser` など）は Mac の `.venv` に入らないので、
  Windows の `.venv` の pip freeze から版を足す（REQ-103）。Mac だけの部品（`pyobjc-*`）は Windows では入らないので、そのままでよい
- ビルドの道具（`requirements-build.txt`）を上げたら、両方の OS で作って `smoke_test` が通ることを確かめる
