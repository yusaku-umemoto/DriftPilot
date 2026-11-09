# DriftPilot のアイコン（ADR-0173）

| ファイル | 使う場所 |
|---|---|
| `DriftPilot.icns` | Mac の `DriftPilot.app`（Dock・Finder・dmg）。16〜1024px |
| `DriftPilot.ico` | Windows の `DriftPilot.exe`・`driftpilot-cli.exe`（エクスプローラー・タスクバー・窓の左上）。16・24・32・48・64・128・256px（中身は PNG） |
| `DriftPilot-mac-1024.png` | Mac 用の元（1024 の中に 824 の角丸の四角。周りは透明） |
| `DriftPilot-win-1024.png` | Windows 用の元（1024 の中に 984 の角丸の四角） |
| `make_icon.py` | 正方形の絵から角丸の元を作る道具（標準ライブラリだけ） |

元の絵は、シリーズ共通の置き場 `Common-Pilot/DesignSheet/DriftPilot/Character/DriftPilot_Mac_Icon.png`（1024×1024、緑の地いっぱいに Mirarin）。

## 作り直し方（Mac で）

```bash
SRC=…/DriftPilot_Mac_Icon.png
sips -s format png -z 824 824 "$SRC" --out in_824.png
sips -s format png -z 984 984 "$SRC" --out in_984.png
python make_icon.py in_824.png DriftPilot-mac-1024.png 824 0.225
python make_icon.py in_984.png DriftPilot-win-1024.png 984 0.18
```

- `.icns`: `DriftPilot-mac-1024.png` を `sips -z` で 16・32・64・128・256・512・1024 にして `DriftPilot.iconset/icon_<n>x<n>[@2x].png` に置き、`iconutil -c icns DriftPilot.iconset -o DriftPilot.icns`
- `.ico`: `DriftPilot-win-1024.png` を 16・24・32・48・64・128・256 にして、PNG のまま ICO の入れ物に入れる（ヘッダー 6 バイト＋項目 16 バイト×7＋PNG）
