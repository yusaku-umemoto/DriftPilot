# DriftPilot

[English](README.en.md)

ネットワーク機器の config と show コマンドの出力を比べ、意味のある差分を取り出すツールです。
差分は AI に解析させることもできます（任意。自分の API キーを使います）。

## できること

- **config の比較:** Cisco 系（IOS・IOS-XE などの字下げの config）・FortiGate・富士通 Si-R の config を、行の並びではなく意味で比べます（構造化比較）。読めない部分は行の比較（単純比較）で補います
- **show の比較:** show コマンドの出力を、表として読める範囲で比べます
- **複数のコマンドが入ったログ:** 1 つのファイルに config と show がまとめて入っていても、コマンドごとに分けて比べます
- **一括比較:** 変更前と変更後のフォルダーを選ぶと、ファイル名や hostname で組にして、まとめて比べます
- **AI での解析（任意）:** 差分の要点と影響を AI に答えさせます。OpenAI 互換・Anthropic などの API、手元の CLI を使えます
- **出力:** 結果を JSON・HTML・CSV で保存できます

## 秘密を守るしくみ

- AI に送るのは差分だけです。config の全文は、選んだときだけ送ります
- パスワード・鍵・コミュニティ名などは、送る前に伏せます（`<secret:N>`）。伏せられない形の行は送りません
- 送る前に、送る文の全体を見せて承認を求めます
- AI と更新の確かめを使わなければ、どこにも通信しません

伏せる処理のコードは `src/driftpilot/llm_analyzer/` にあります。誰でも確かめられます。

## 使う

### 配布版（Python は要りません）

[Releases](../../releases) から、使う OS の分を取ってください。

| OS | ファイル | メモ |
|---|---|---|
| Windows 10 / 11（x64） | `DriftPilot-<版>-windows-x64.zip` | 展開して `DriftPilot.exe` を開きます。Windows 10 では WebView2 Runtime が要ることがあります |
| macOS（Apple シリコン） | `DriftPilot-<版>-macos-arm64.dmg` | `DriftPilot.app` を「アプリケーション」に入れて開きます |

署名をしていないので、初めて開くときに OS の警告が出ます。

- **Windows:** 「Windows によって PC が保護されました」→「詳細情報」→「実行」
- **Mac:** 「開発元を確認できない」→ システム設定の「プライバシーとセキュリティ」→「このまま開く」

### 設定の置き場所

- **Windows:** 展開したフォルダーの `settings\`。フォルダーの外には置きません（API キーを除く）
- **Mac:** `~/Library/Application Support/DriftPilot/`
- **API キー:** Windows は資格情報マネージャー、Mac はキーチェーン。設定画面の「削除…」で消せます

### 新しい版の確かめ

- 設定画面の「更新」タブの「更新を確かめる」で、GitHub に新しい版が出ているかを確かめます
- 既定では、ボタンを押したときだけ確かめます
- 「起動したときに新しい版を確かめる（自動アップデートチェック）」をオンにして保存すると、起動のたびに確かめ、新しい版があれば比較の画面の上に知らせます
- 送るのは DriftPilot の版だけです（config・設定・API キーは送りません）
- Windows の zip 版と Mac のアプリは、新しい版があれば「更新する」で取ってきて、署名を確かめてから入れ替えます（DriftPilot が一度閉じて、新しい版が開きます）
- 0.1.0 には更新の機能がありません。0.1.0 から 0.2.0 へは、配る物に入っている `はじめにお読みください.txt` の「■ 更新するとき」の手順で、手で入れ替えてください

### ソースから

Python 3.14 が要ります。

```bash
git clone https://github.com/umemonmon/DriftPilot.git
cd DriftPilot
python3.14 -m venv .venv
.venv/bin/python -m pip install -e ".[gui,llm]"
.venv/bin/driftpilot gui
```

Windows では `.venv\Scripts\python.exe`・`.venv\Scripts\driftpilot.exe` に読み替えてください。

コマンドで比べる例:

```bash
driftpilot compare before.cfg after.cfg
driftpilot compare before.cfg after.cfg --format json --save result.json
driftpilot batch --pair-by-name before_dir after_dir --confirm
```

`driftpilot --help` で全部のコマンドを見られます。

## 状態

0.2.0（試用版）です。画面と出力の形は、これから変わることがあります。

## ライセンス

[Apache License 2.0](LICENSE)。配布版に同梱している部品のライセンスは、配布版の中の `THIRD_PARTY_LICENSES.txt` にあります。
