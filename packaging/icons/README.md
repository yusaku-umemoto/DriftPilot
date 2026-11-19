# DriftPilot のアイコン（ADR-0173・ADR-0246）

アプリのファイルのアイコン（既定）は、ピクセル風の**フォーマル版**。元は共通の置き場 `Pilot-DesignSheet/DesignSheet/DriftPilot/App_Icons/` の `DriftPilot_Business.*`（ADR-0246）。
設定でキャラクターがオンのときだけ、動いている間のアイコン（Mac の Dock・Windows の窓の左上とタスクバー）をキャラクター版にする（下のとおり）。

| ファイル | 使う場所 |
|---|---|
| `DriftPilot.icns` | Mac の `DriftPilot.app`（Dock・Finder・dmg）。フォーマル版。ic10（1024）・ic07（128）など |
| `DriftPilot.ico` | Windows の `DriftPilot.exe`・`driftpilot-cli.exe`（エクスプローラー・タスクバー・窓の左上）。フォーマル版。16・24・32・48・64・128・256px（中身は PNG） |
| `DriftPilot-business-1024.png` | フォーマル版の元（1024×1024） |
| `DriftPilot-mac-1024.png` | キャラクター版の Mac 用の元（1024 の中に 824 の角丸の四角。周りは透明） |
| `DriftPilot-win-1024.png` | キャラクター版の Windows 用の元（1024 の中に 984 の角丸の四角） |
| `make_icon.py` | 正方形の絵から角丸の元を作る道具（標準ライブラリだけ。キャラクター版の作り方として残す） |

spec の `EXE(icon=…)`・`BUNDLE(icon=…)` は `DriftPilot.ico`・`DriftPilot.icns`（フォーマル版）を指す。

## 動いている間のキャラクターのアイコン

キャラクター版（と、オフのときのピクセル風）の画像は `src/driftpilot/gui_web/static/character/` の `app-icon-*.png`・`app-icon-*.ico`（4 つ）にある。
起動のときと設定の保存のときに `gui_web/app_icon.py` が選び、ファイルのアイコンは書き換えない。

## キャラクター版の作り直し方（Mac で）

元の絵は、シリーズ共通の置き場 `Common-Pilot/DesignSheet/DriftPilot/Character/DriftPilot_Mac_Icon.png`（1024×1024、緑の地いっぱいに Mirarin）。

```bash
SRC=…/DriftPilot_Mac_Icon.png
sips -s format png -z 824 824 "$SRC" --out in_824.png
sips -s format png -z 984 984 "$SRC" --out in_984.png
python make_icon.py in_824.png DriftPilot-mac-1024.png 824 0.225
python make_icon.py in_984.png DriftPilot-win-1024.png 984 0.18
```

- `.icns`: `DriftPilot-mac-1024.png` を `sips -z` で 16・32・64・128・256・512・1024 にして `DriftPilot.iconset/icon_<n>x<n>[@2x].png` に置き、`iconutil -c icns DriftPilot.iconset -o DriftPilot.icns`
- `.ico`: `DriftPilot-win-1024.png` を 16・24・32・48・64・128・256 にして、PNG のまま ICO の入れ物に入れる（ヘッダー 6 バイト＋項目 16 バイト×7＋PNG）
