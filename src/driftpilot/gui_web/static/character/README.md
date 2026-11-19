# キャラクターの絵（ADR-0174）

設定の「キャラクター（Mirarin）を表示する」がオンのときだけ使う。元はシリーズ共通の置き場 `Common-Pilot/DesignSheet/DriftPilot/`（正）。

| ファイル | 場面 | 元 | 大きさ |
|---|---|---|---|
| `splash.png` | 起動時 | `Startup_Loading/splash.png` | 1536×1024 |
| `ai-thinking.png` | AI の考え中（動く。繰り返し） | `Flight_Loading/loading_flight.png` | 256×160 APNG |
| `ai-thinking-still.png` | 同じ（動きを減らす設定） | `Flight_Loading/flying_right.png` | 768×576 |
| `batch-progress.png` | 一括比較の実行中（動く。繰り返し） | `Software_UI_Assets/progress_motion.png` | 384×256 APNG |
| `batch-progress-still.png` | 同じ（動きを減らす設定） | `Software_UI_Assets/progress.png` | 640×480 |
| `no-diff.png` | 差分なし（1 回だけ動く） | `Software_UI_Assets/success_motion.png` | 384×256 APNG |
| `no-diff-still.png` | 同じ（動きを減らす設定） | `Software_UI_Assets/success.png` | 640×480 |
| `welcome.png` | 空の比較の窓 | `Software_UI_Assets/welcome.png` | 640×480 |
| `warning.png` | AI のエラー | `Software_UI_Assets/warning.png` | 640×480 |
| `avatar.png` | 答えの吹き出しの顔 | `Character/DriftPilot_Mac_Icon.png` を丸く切ったもの | 64×64 |
| `app-icon-character.png` | 動いている間のアイコン（キャラクターがオン。Mac の Dock）（ADR-0246） | 設計側が用意（`packaging/icons/DriftPilot-mac-1024.png` と同じ絵。角の丸めと余白付き） | 1024×1024 |
| `app-icon-character.ico` | 同じ（Windows の窓の左上・タスクバー） | 同上 | 16〜256 の 7 つ |
| `app-icon-business.png` | 動いている間のアイコン（キャラクターがオフ。Mac の Dock） | `Pilot-DesignSheet/DesignSheet/DriftPilot/App_Icons/` の `DriftPilot_Business.*` | 1024×1024 |
| `app-icon-business.ico` | 同じ（Windows） | 同上 | 16〜256 の 7 つ |
