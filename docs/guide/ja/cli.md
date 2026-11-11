# コマンド（CLI）の使い方

[手引きの目次へ](README.md)

## コマンドの場所

| 入れ方 | コマンド |
|---|---|
| Windows の zip | 展開したフォルダーの `driftpilot-cli.exe`（PowerShell かコマンドプロンプトで動かす） |
| Mac のアプリ | `/Applications/DriftPilot.app/Contents/MacOS/driftpilot-cli` |
| ソースから | `driftpilot`（仮想環境の中で） |

下の例では `driftpilot` と書きます。入れ方に合わせて読み替えてください。

`driftpilot --help` で全部のコマンド、`driftpilot <コマンド> --help` でそのコマンドの使い方が出ます。

## 2 つのファイルを比べる

```bash
driftpilot compare before.cfg after.cfg
```

よく使うオプション:

| オプション | 意味 |
|---|---|
| `--mode structured\|raw\|both` | 比較方式（既定 both） |
| `--format text\|json` | 出力の形（既定 text） |
| `--kind config\|show\|auto` | 入力の種類（既定 config） |
| `--platform NAME` | 機種: `fortinet`・`fujitsu_sir`・`auto`。show の出力では ntc-templates の名前（例 `cisco_ios`） |
| `--show-command TEXT` | show のコマンド（例 `'show ip interface brief'`） |
| `--save PATH` | 結果を JSON で保存（上書きしない） |
| `--ignore-line REGEX`・`--replace REGEX TEXT`・`--ignore-space …`・`--ignore-blank-lines`・`--ignore-case` | 単純比較のオプション |
| `--all-segments` | ファイルの中の全部のコマンドの出力を、コマンドで組にして比べる |

JSON で出して保存する例:

```bash
driftpilot compare before.cfg after.cfg --format json --save result.json
```

## コマンドで AI 解析をする

```bash
driftpilot compare before.cfg after.cfg --llm-dry-run
driftpilot compare before.cfg after.cfg --llm
```

- `--llm-dry-run` は、送る中身（伏せた後）を出すだけで、送りません。まずこちらで確かめてください。
- `--llm` は、使用中の AI プロファイルに差分を送ります。
- `--llm-profile NAME`・`--llm-prompt-profile NAME` で、その回だけ使うプロファイルを選べます。
- `driftpilot llm-check` は、決まった短い文を 1 回送って、キーとモデルでつながるかを確かめます（ごく少額の料金がかかることがあります）。

## 一括比較

```bash
driftpilot batch --pair-by-name before_dir after_dir
driftpilot batch --pair-by-name before_dir after_dir --confirm
driftpilot batch pairs.yaml --output results.jsonl
```

- `--pair-by-name` は、名前が同じファイルを組にして一覧を出します。`--confirm` を付けると比べます。
- `--write-manifest PATH` は、組を組のファイル（YAML）に書きます。直してから後で流せます。
- `--save DIR` で結果を 1 件 1 ファイルで保存、`--summary PATH` でまとめを JSON で書きます。
- `--llm` を付けると、送る中身の要約を出します。`--confirm` が無ければ送りません。

## ほかのコマンド

| コマンド | すること |
|---|---|
| `selfcheck` | 同梱の部品とデータがそろっているかを確かめる（通信しない・ファイルを書かない） |
| `mask-patterns` | AI に送る前に伏せる表を出す |
| `patterns` | config の読み取りが対応しているものの一覧を出す |
| `gui` | 画面を開く（ソースから入れた場合） |

## 言語

コマンドも、画面と同じ言語の設定（**設定 → 表示 → 言語**）に従います。その回だけ変えるときは `DRIFTPILOT_LANG=ja` か `DRIFTPILOT_LANG=en` を付けます。

```bash
DRIFTPILOT_LANG=ja driftpilot compare before.cfg after.cfg
```

Windows の PowerShell では、先に `$env:DRIFTPILOT_LANG = "ja"` を実行します。
