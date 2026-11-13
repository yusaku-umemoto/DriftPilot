# コマンド（CLI）の使い方

[手引きの目次へ](README.md)

コマンドでも、窓を使わないことのほかは画面と同じことができます。2 つのファイルの比較、フォルダーごとの一括比較、端末のログのコマンドごとの切り分け、AI による差分の説明です。人だけでなく、スクリプトや AI エージェントから動かすことも考えて作っています。結果は標準出力、知らせは標準エラーに出し、結果は終了コードでも分かります。どの比較も JSON で出せます。

- [コマンドの場所](#コマンドの場所)
- [早見表（スクリプト・AI エージェント向け）](#早見表スクリプトai-エージェント向け)
- [2 つの config を比べる](#2-つの-config-を比べる)
- [機種を選ぶ](#機種を選ぶ)
- [文字の出力の読み方](#文字の出力の読み方)
- [JSON の出力と結果の保存](#json-の出力と結果の保存)
- [単純比較のオプション](#単純比較のオプション)
- [複数のコマンドが入った端末のログ](#複数のコマンドが入った端末のログ)
- [show の出力を表として比べる](#show-の出力を表として比べる)
- [一括比較（フォルダーとマニフェスト）](#一括比較フォルダーとマニフェスト)
- [AI での解析](#ai-での解析)
- [そのほかのコマンド](#そのほかのコマンド)
- [言語](#言語)
- [使い方の例](#使い方の例)

## コマンドの場所

| 入れ方 | コマンド |
|---|---|
| Windows の zip | 展開した `DriftPilot` フォルダーの `driftpilot-cli.exe`（PowerShell かコマンドプロンプトで動かします） |
| Mac のアプリ | `/Applications/DriftPilot.app/Contents/MacOS/driftpilot-cli` |
| ソースから | `driftpilot`（仮想環境の中で） |

このページの例は `driftpilot` と書いています。入れ方に合わせて読み替えてください。たとえば次のようにします。

```bash
# Mac のアプリ
alias driftpilot=/Applications/DriftPilot.app/Contents/MacOS/driftpilot-cli
```

```powershell
# Windows（PowerShell）。展開したフォルダーで
.\driftpilot-cli.exe compare before.cfg after.cfg
```

`driftpilot --help` でコマンドの一覧、`driftpilot <コマンド> --help` でそのコマンドの全部のオプションが出ます。`driftpilot --version` は版を出します（`driftpilot 0.2.3`）。

このページの例は、[`examples/`](../../../examples/README.md) の例のファイルを使い、0.2.3 で `DRIFTPILOT_LANG=ja` にして動かした実際の出力です（長い行は `…` で縮めています）。

## 早見表（スクリプト・AI エージェント向け）

| 項目 | 決まり |
|---|---|
| `compare`・`batch` の終了コード | `0` 差分なし・`1` 差分あり・`2` エラー（文は標準エラー）・`3` 選択待ち（ファイルに複数のコマンドの出力があり、どれを比べるか選ぶ必要がある。[端末のログ](#複数のコマンドが入った端末のログ)を見てください）。`batch` は全部の組のうち一番重い状態を返します（2 > 3 > 1 > 0） |
| `llm-check` の終了コード | `0` つながった・`1` 送ったが失敗した・`2` 送らなかった（モデルやキーが無い、確認で止めた） |
| `selfcheck` の終了コード | `0` すべて OK・`1` 欠けているものがある |
| 標準出力 | 結果だけ（文字か JSON）。パイプやリダイレクトにそのまま使えます |
| 標準エラー | 知らせ・注意・集計・エラー（「保存しました」、一括比較の集計など） |
| ネットワーク | `--llm`（AI 解析）か `llm-check` のときだけ使います。`--llm-dry-run` は送りません |
| ファイル | `--save`・`--output`・`--summary`・`--write-manifest`（表のコマンドの `--output`）を付けたときだけ書きます。**既にあるファイルは上書きしません**（終了コード 2 で止まります。`/dev/null` も「既にあります」になるので、捨てるときは `--quiet` と標準出力のリダイレクトを使います）。`--save DIR` のフォルダーは先に作っておきます |
| 入力 | ファイルだけ（標準入力の `-` は使えません）。UTF-8（BOM の有無を問わず）・BOM 付きの UTF-16・Shift_JIS（cp932）を読みます。おかしな形で写した文字も、比べる前に戻します（RTF、HTML の `<pre>`、行番号や全行に同じ印が付いたもの、共通の字下げ、端末で折り返された FortiGate の行）。戻したときは注意で知らせます |
| 既定の比較 | `--mode both`（構造化比較と単純比較）・`--kind config`・Cisco 系の読み方。FortiGate・Si-R を見分けるには `--platform auto` を付けます |
| 結果の秘密 | 文字・JSON の結果と保存したファイルには、パスワードなどの元の値がそのまま入ります。config と同じように扱ってください。伏せるのは AI に送る中身だけです |

一番短い使い方:

```bash
driftpilot compare before.cfg after.cfg --platform auto                    # 文字の結果
driftpilot compare before.cfg after.cfg --platform auto --format json      # JSON の結果
driftpilot compare before.cfg after.cfg --platform auto --mode structured  # 意味で変わった所だけ
echo $?                                                                    # 0 / 1 / 2 / 3
```

## 2 つの config を比べる

```bash
driftpilot compare cisco/before.cfg cisco/after.cfg
```

出力（終了コード 1、差分あり）:

```text
== structured ==
[added] acls LAN-IN / entries > 25: None -> {'seq': 25, 'text': 'permit tcp 192.0.2.0 0.0.0.255 any eq 22'}
[added] bgp / neighbors > 198.51.100.1: None -> {'kind': 'address', 'remote_as': 64501, 'peer_group': None, 'description': 'ISP-B', …, 'password_configured': True, 'options': []}
[added] global / snmp > communities: None -> {'name': 'Example-RO-2026', 'access': 'RO', 'acl': None, 'view': None}
[removed] global / snmp > communities: {'name': 'Example-RO-2025', 'access': 'RO', 'acl': None, 'view': None} -> None
[added] interfaces GigabitEthernet0/0/2: None -> {'type': 'routed', 'parent_interface': None, 'description': 'Uplink to ISP-B', 'shutdown': False, 'vrf': None, 'ip_address': IPv4Interface('198.51.100.2/30'), …}
構造化不可の行数: 変更前 0 / 変更後 0
== raw ==
+ 18: interface GigabitEthernet0/0/2
+ 19:  description Uplink to ISP-B
+ 20:  ip address 198.51.100.2 255.255.255.252
+ 21:  no shutdown
+ 22: !
- 24:  neighbor 203.0.113.1 password Old-BGP-Key-2025
+ 29:  neighbor 203.0.113.1 password New-BGP-Key-2026
+ 31:  neighbor 198.51.100.1 remote-as 64501
+ 32:  neighbor 198.51.100.1 password ISP-B-Shared-Key
+ 33:  neighbor 198.51.100.1 description ISP-B
+ 38:  25 permit tcp 192.0.2.0 0.0.0.255 any eq 22
- 32: snmp-server community Example-RO-2025 RO
+ 41: snmp-server community Example-RO-2026 RO
有効なルール: なし
== segmentation ==
変更前: cisco/before.cfg  プロンプト: なし(切り分けなし) (none)
  * 1. (コマンド不明)  39 行
変更後: cisco/after.cfg  プロンプト: なし(切り分けなし) (none)
  * 1. (コマンド不明)  48 行
既定値込みの判定: 変更前 不明 / 変更後 不明
注意: 取得コマンド不明: 変更前・変更後とも、通常版か既定値込み版か判定できません
```

同じファイルどうしを比べると、`== structured ==` の下に `差分なし` と出て、終了コードは 0 です。

`compare` のオプション:

| オプション | 意味 | 既定 |
|---|---|---|
| `before`・`after` | 比べる 2 つのファイル（位置で渡します） | — |
| `--mode structured\|raw\|both` | `structured` は意味で比べる、`raw` は行で比べる（画面の「単純比較」）、`both` は両方 | `both` |
| `--format text\|json` | 出力の形 | `text` |
| `--kind config\|show\|auto` | 入力の種類。`auto` は中身から決めます | `config` |
| `--platform NAME` | 機種。[下](#機種を選ぶ)を見てください | Cisco 系 |
| `--show-command TEXT` | show の出力を表として比べるときの show コマンド（`--platform` と一緒に） | — |
| `--before-command TEXT`・`--after-command TEXT` | ログの中の区間を、コマンドの文字の一部で選ぶ | — |
| `--before-segment N`・`--after-segment N` | ログの中の区間を、番号で選ぶ（1 から） | — |
| `--all-segments` | 両方のログの区間をコマンドで組にして、全部比べる | 切 |
| `--save PATH` | 結果を JSON で保存する（フォルダーを渡すと名前を自動で付けます）。上書きしません | 保存しない |
| `--rules PATH`・`--enable-rule ID` | 正規化のルール（上級者向け。`--help` を見てください） | 同梱のルール |
| `--prompt TEXT` | 自動で見つからないときの機器のプロンプト（例: `'edge-rtr-01#'`） | 自動 |
| 単純比較のオプション | [下](#単純比較のオプション)を見てください | 切 |
| AI のオプション | [下](#ai-での解析)を見てください | 切 |

## 機種を選ぶ

| `--platform` | 読み方 | 使う場面 |
|---|---|---|
| （付けない） | Cisco 系（字下げの config） | Cisco IOS・IOS-XE・NX-OS 風、Arista EOS などの字下げの config |
| `auto` | 中身から決める | 機種が混ざっているとき、分からないとき。`機種: 自動（FortiGate）` のように出ます |
| `fortinet` | FortiGate | FortiOS の `show full-configuration`・`show` の出力 |
| `fujitsu_sir` | 平らな config | 富士通 Si-R |
| `cisco_ios` などの ntc-templates の名前 | show の表 | show の出力だけ。`--kind show --show-command …` と一緒に使います |

```bash
driftpilot compare fortigate/before.conf fortigate/after.conf --platform auto --mode structured
```

```text
機種: 自動（FortiGate）
== structured ==
[added] vendor_specific / fortinet > firewall > policies > 1 > service: None -> 'HTTP'
[changed] vendor_specific / fortinet > interfaces > port1 > allowaccess: ['ping', 'https'] -> ['ping', 'https', 'ssh']
…
```

`--platform auto`（か `fortinet`・`fujitsu_sir`）を付けないと、FortiGate や Si-R のファイルも Cisco 系として読むので、構造化比較の結果は意味を持ちません。迷ったら `--platform auto` を付けてください。

## 文字の出力の読み方

文字の出力には、最大で 4 つの塊があります。

| 塊 | 中身 |
|---|---|
| `== structured ==` | 1 行に 1 つの変更。`[操作] 分類 対象 / 項目: 変更前 -> 変更後`。操作は `added`（追加）・`removed`（削除）・`changed`（変更）。`None` は「未設定」。`構造化不可の行数: 変更前 0 / 変更後 0` は、読めなかった行の数です（その行も単純比較では比べています） |
| `== raw ==` | 行の差分。`+ N: 行`（変更後の N 行目に追加）、`- N: 行`（変更前の N 行目を削除）。`--detect-moves` を付けると、動いた塊を移動として出します |
| `== segmentation ==` | 各ファイルをどう分けたか。`*` が比べた部分。`プロンプト:` は見つけた機器のプロンプト（ふつうの config なら `なし(切り分けなし)`） |
| 注意 | `既定値込みの判定` は、各ファイルがふつうの config か、既定値込み（`show running-config all`・`full-configuration`）かの見立てです。違う種類どうしを比べてしまうのを防ぎます |

## JSON の出力と結果の保存

```bash
driftpilot compare cisco/before.cfg cisco/after.cfg --format json > result.json
```

主な項目（`schema_version` は `"1"`）:

| 項目 | 意味 |
|---|---|
| `status` | `no_difference`・`difference`・`needs_selection`・`error`（終了コードと同じ） |
| `has_diff` | `true`・`false` |
| `mode`・`kind` | 何を比べたか |
| `structured.diffs[]` | `{op, category, name, path, before, after}`。例: `{"op": "added", "category": "bgp", "name": null, "path": ["neighbors", "198.51.100.1"], "before": null, "after": {…}}` |
| `structured.unparsed_counts` | `{before, after}`。構造化できなかった行の数 |
| `raw.diffs[]` | `{op, before_line_no, after_line_no, text}`。例: `{"op": "removed", "before_line_no": 24, "after_line_no": null, "text": " neighbor 203.0.113.1 password Old-BGP-Key-2025"}` |
| `show` | show の出力の表の比較（使ったとき） |
| `segmentation.before`・`.after` | `file`・`prompt`・`segments[]`・`selected` |
| `notices[]` | `unknown_both`（ふつうか既定値込みか分からない）などの注意 |
| `llm` | AI の結果。使わなかったときの `status` は `skipped` |

`jq` の例。構造化比較の変更だけを並べます:

```bash
driftpilot compare before.cfg after.cfg --platform auto --format json \
  | jq -r '.structured.diffs[] | "\(.op)\t\(.category)\t\(.path | map(tostring) | join(" > "))"'
```

`--save PATH` は、入力・設定・道具の版・結果をまとめた記録を書きます。画面の「保存した結果を開く…」でも開けます。

```bash
mkdir -p results
driftpilot compare before.cfg after.cfg --save results/
# 標準エラー: 注意: 比較結果を results/20261113-191259_compare.json に保存しました。入力の設定値をそのまま含みます。…
```

## 単純比較のオプション

行の比較（raw）だけを変えます。構造化比較には関わりません。

| オプション | 働き |
|---|---|
| `--ignore-line REGEX` | 当たる行を比べない（何度でも） |
| `--replace REGEX TEXT` | 当たる部分を置き換えてから比べる（何度でも。上から順に） |
| `--ignore-space off\|trailing\|amount\|all` | 行末の空白・空白の数・すべての空白を無視する |
| `--ignore-blank-lines` | 空の行を比べない |
| `--ignore-case` | 大文字と小文字を区別しない |
| `--detect-moves` | 3 行以上の塊が別の場所に動いたら、移動として出す（移動も差分に数えます） |
| `--diff-algorithm standard\|minimal\|patience` | 行の差分の取り方 |

```bash
# BGP のパスワードの行と時刻の行、行末の空白を無視する
driftpilot compare before.cfg after.cfg --mode raw \
  --ignore-line '^\s*neighbor .* password ' \
  --replace '^! Last configuration change at .*' '! (timestamp)' \
  --ignore-space trailing
```

画面で決めた既定（「設定… → 単純比較」）は、コマンドでは**読みません**。毎回オプションで渡してください。

## 複数のコマンドが入った端末のログ

端末のログ（SSH のやり取りを保存したもの）には、たいてい複数のコマンドが入っています。DriftPilot は機器のプロンプト（例: `edge-rtr-01#`）を見つけて、コマンドごとの区間に分けます。

```text
edge-rtr-01#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0/2   unassigned      YES unset  administratively down down
…
edge-rtr-01#show running-config | section snmp
snmp-server community Example-RO-2025 RO
…
```

このようなログどうしを、選ばずに比べると、終了コード **3** で止まり、区間の一覧を出します。

```bash
driftpilot compare before.log after.log
```

```text
エラー: 区間が複数あります。--before-segment / --after-segment などで区間を指定してください
== segmentation ==
変更前は 2 区間あります。選択してください:
変更前: before.log  プロンプト: 'edge-rtr-01#' (auto)
    1. show ip interface brief  4 行
    2. show running-config | section snmp  2 行
…
```

両方で 1 つずつ、番号かコマンドの一部で選びます。

```bash
driftpilot compare before.log after.log --before-segment 2 --after-segment 2
driftpilot compare before.log after.log --before-command "section snmp" --after-command "section snmp"
```

または全部のコマンドをまとめて比べます。区間はコマンドで組にし、設定のコマンド（`show running-config` など）は config として、ほかは show の出力として比べます。

```bash
driftpilot compare before.log after.log --all-segments
```

```text
=== 1. show ip interface brief (変更前 区間 1 / 変更後 区間 1) ===
== show structured ==
raw 比較にフォールバックしました: 機種とコマンドが指定されていません
== raw ==
- 4: GigabitEthernet0/0/2   unassigned      YES unset  administratively down down
+ 4: GigabitEthernet0/0/2   198.51.100.2    YES manual up                    up
…
=== 2. show running-config | section snmp (変更前 区間 2 / 変更後 区間 2) ===
== structured ==
[added] global / snmp > communities: None -> {'name': 'Example-RO-2026', …}
[removed] global / snmp > communities: {'name': 'Example-RO-2025', …} -> None
…
```

- `--all-segments --save FILE` は、組ごとに JSON Lines を 1 行書きます（相手の無い区間は入りません）
- 打ち間違いのエラーの出力（`% Invalid input detected`、Si-R の `<ERROR> … : Unknown command` など）の区間は組にせず、比べません。`=== エラーの出力（比べていません） ===` の下に並べます
- `--all-segments` は、`--before-segment`・`--after-segment`・`--before-command`・`--after-command`・`--show-command` と一緒には使えません
- プロンプトが見つからないときは `--prompt 'edge-rtr-01#'` で渡します
- 複数の機器が入ったログは、画面の「機器ごとに分けて一括比較で開く…」で機器ごとのファイルに分けられます。コマンドは 1 つのログを 1 台として比べます

## show の出力を表として比べる

show の出力は、[ntc-templates](https://github.com/networktocode/ntc-templates) で表に読み、行ごとに比べられます。機種と、show コマンドをそのまま渡します。

```bash
driftpilot compare before.log after.log \
  --before-command "ip interface brief" --after-command "ip interface brief" \
  --kind show --platform cisco_ios --show-command "show ip interface brief"
```

```text
== show structured ==
[changed] GigabitEthernet0/0/2: ip_address: 'unassigned' -> '198.51.100.2', status: 'administratively down' -> 'up', proto: 'down' -> 'up'
== raw ==
- 4: GigabitEthernet0/0/2   unassigned      YES unset  administratively down down
+ 4: GigabitEthernet0/0/2   198.51.100.2    YES manual up                    up
…
```

show の出力では、`--platform` と `--show-command` は必ず一緒に渡します（片方だけだと `エラー: --platform と --show-command は両方指定してください`）。渡さなければ行で比べます。`driftpilot show-commands` で、比べ方を知っている show コマンドの表が出ます。

## 一括比較（フォルダーとマニフェスト）

### 2 つのフォルダーをファイル名で組にする

```bash
driftpilot batch --pair-by-name before after
```

```text
対になったファイル: 3 件(ファイル名が拡張子まで完全一致するもの)
  branch-rt01.txt
  core-sw01.cfg
  edge-fw01.conf
対にならなかったファイル: 0 件
  変更前にしかない: 0 件
  変更後にしかない: 0 件
比較は実行していません。この対で実行するには --confirm、組のファイルとして保存するには --write-manifest PATH を付けてください
```

これは組を並べるだけです（終了コード 0）。続けて実行するか:

```bash
driftpilot batch --pair-by-name before after --confirm > results.jsonl
# 標準エラー: 一括比較: 3件中3件を実行(差分なし 0 / 差分あり 3 / 選択待ち 0 / 失敗 0)、0.0秒。終了コード 1
```

組をマニフェストのファイルに保存して、直してから後で実行します。

```bash
driftpilot batch --pair-by-name before after --write-manifest pairs.yaml
```

> **一括比較の機種:** `--pair-by-name --confirm` は、全部の組を既定（Cisco 系）の読み方で比べます。フォルダーに FortiGate や Si-R のファイルがあるときは、マニフェストを書き出し、`defaults` に `platform: auto` を足してから実行してください（下の例）。画面の一括比較の窓は、機種を自動で見分けます。

### マニフェストのファイル（YAML）

`--write-manifest` が書くファイル:

```yaml
schema_version: '1'
defaults:
  mode: both
  kind: config
jobs:
- id: branch-rt01.txt
  before:
    path: before/branch-rt01.txt
  after:
    path: after/branch-rt01.txt
- id: core-sw01.cfg
  before:
    path: before/core-sw01.cfg
  after:
    path: after/core-sw01.cfg
```

もっと書いた例（下のキーはすべて動くことを確かめています）:

```yaml
schema_version: '1'
defaults:                 # 組で値を書かなければ、全部の組に効く
  mode: both              # structured | raw | both
  kind: config            # config | show | auto
  platform: auto          # auto | fortinet | fujitsu_sir |（show のときは ntc-templates の名前）
  raw_options:            # 単純比較のオプション（組に書くと、丸ごと置き換わる）
    ignore_lines:
      - '^\s*!'
    whitespace: trailing  # off | trailing | amount | all
    # replacements: [{pattern: '正規表現', replacement: '置き換える文字'}]
    # ignore_blank_lines: true
    # ignore_case: true
    # detect_moves: true
    # algorithm: standard  # standard | minimal | patience
jobs:
  - id: core-sw01                     # 組の名前（重ならないように）
    label: コアスイッチ（Cisco）       # 結果に出す自由な文字
    before: {path: before/core-sw01.cfg}
    after: {path: after/core-sw01.cfg}
  - id: edge-fw01
    label: 拠点のファイアウォール（FortiGate）
    platform: fortinet                # 組ごとの設定
    before: {path: before/edge-fw01.conf}
    after: {path: after/edge-fw01.conf}
  - id: edge-rtr01-ifbrief
    label: 端末のログのインターフェースの状態
    kind: show
    platform: cisco_ios
    show_command: show ip interface brief
    before: {path: logs/before.log, command: ip interface brief}   # 区間をコマンドの文字で選ぶ
    after: {path: logs/after.log, command: ip interface brief}
  - id: edge-rtr01-snmp
    before: {path: logs/before.log, segment: 2}                    # 番号で選ぶ
    after: {path: logs/after.log, segment: 2}
```

- 書けるキー: 一番上は `schema_version`・`defaults`・`jobs`。`defaults` と各組は `mode`・`kind`・`platform`・`show_command`・`prompt`・`rules`・`enable_rule`・`before`・`after`・`raw_options`。各組はさらに `id`・`label`。`before`・`after` の中は `path`・`command`・`segment`。知らないキーはエラーです
- 相対パスは、マニフェストのファイルからの相対です
- show の表のときは、`platform` と `show_command` を一緒に書きます

実行します。

```bash
driftpilot batch pairs.yaml --dry-run                         # 予定を並べるだけ（終了コード 0）
driftpilot batch pairs.yaml > results.jsonl                   # JSON Lines を標準出力へ
driftpilot batch pairs.yaml --output results.jsonl --summary summary.json --save saved/
```

`batch` のオプション:

| オプション | 意味 |
|---|---|
| `MANIFEST` | マニフェストのファイル（`--pair-by-name` のときは要りません） |
| `--pair-by-name BEFORE_DIR AFTER_DIR` | 名前がまったく同じファイルを組にして並べる（フォルダーの直下だけ） |
| `--confirm` | `--pair-by-name` の組を実行する。`--llm` のときは送る |
| `--write-manifest PATH` | `--pair-by-name` の組をマニフェストに書いて終わる |
| `--dry-run` | 実行せずに予定を並べる |
| `--output PATH` | JSON Lines を標準出力ではなくファイルに書く |
| `--summary PATH` | 集計を JSON で書く（下） |
| `--save DIR` | 組ごとの結果を 1 つずつファイルに保存する（フォルダーは先に作る） |
| `--quiet` | 集計を標準エラーに出さない |
| AI のオプション | `--llm`・`--llm-dry-run`・`--llm-max-diffs N`・`--llm-max-total-diffs N`・`--llm-profile`・`--llm-prompt-profile`・`--llm-no-premise`（[AI での解析](#ai-での解析)） |

JSON Lines の各行には、`job`（`id`・`label`・`index`）・`settings`（`mode`・`kind`・`platform` など）・`inputs`・`result`（`compare --format json` と同じ形）・`tools` があります。組ごとの状態を並べる例:

```bash
driftpilot batch pairs.yaml --quiet \
  | jq -r '"\(.job.id)\t\(.result.status)\t\(.result.structured.diffs | length)"'
```

集計のファイル:

```json
{
  "schema_version": "1",
  "total": 3,
  "executed": 3,
  "cancelled": false,
  "interrupted": false,
  "counts": {"no_difference": 0, "difference": 3, "needs_selection": 0, "error": 0},
  "elapsed_seconds": 0.013,
  "exit_code": 1,
  "manifest_error": null,
  "saved": {"dir": "/path/to/saved", "count": 3, "failures": []},
  "unpaired": null
}
```

## AI での解析

### AI のつなぎ先を決める

一番かんたんなのは画面です。「設定… → AI プロファイル」で種類とモデルを選び、API キーを保管庫に保存して、「このプロファイルを使う」を押します。コマンドも同じプロファイルを使います。

画面を使わないときは、設定ファイルにモデルを書き、キーは環境変数で渡します。

- 設定ファイル: 設定のフォルダーの `config.toml`（Windows の zip は exe の隣の `settings\`、Mac は `~/Library/Application Support/DriftPilot/`）。モデルが無いとき、`llm-check` がファイルの場所をそのまま出します

```toml
[llm]
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"      # OpenAI 互換の接続先
# extra_secret_words = ["site-token"]       # 伏せる語を足す（この語を含む行を伏せる）
# max_diffs = 100                           # 1 組あたりの上限
# max_total_diffs = 1000                    # 全部の組の合計の上限
```

- API キー: 環境変数 `DRIFTPILOT_LLM_API_KEY`（または画面から保管庫へ）。ファイルに `api_key` を書くこともできますが、安全ではありません

つながるか確かめます（決まった短い文を 1 回送ります。ごくわずかな料金がかかることがあります）。

```bash
driftpilot llm-check            # 送る前に聞く
driftpilot llm-check --yes      # 聞かない（スクリプトではこちら）
```

何も設定していなければ、送らずに終わります（終了コード 2）: `モデルが設定されていません。送っていません。設定ファイル（…/config.toml）の llm.model にモデル名を書いてください`

### 下見してから送る

必ず先に下見してください。`--llm-dry-run` は、伏せた後に送る中身をそのまま出し、何も送りません（モデルやキーが無くても使えます）。

```bash
driftpilot compare cisco/before.cfg cisco/after.cfg --llm-dry-run
```

```text
…
== AI 解析 ==
AI 解析: 未実施(送信内容の確認だけです(--llm-dry-run)。送信していません)
--- 送信内容(マスク後。このあとに答えの書き方の決まり(固定の文)だけを足して送ります) ---
あなたはネットワーク機器の設定の差分を読む補助です。…
=== 差分 ===
[added] acls LAN-IN / entries > 25: null -> {"seq": 25, "text": "permit tcp 192.0.2.0 0.0.0.255 any eq 22"}
[added] global / snmp > communities: null -> {"name": "<secret:1>", "access": "RO", "acl": null, "view": null}
[removed] global / snmp > communities: {"name": "<secret:2>", "access": "RO", "acl": null, "view": null} -> null
…
=== ここまで ===
```

SNMP のコミュニティ名が `<secret:1>`・`<secret:2>` に伏せられています。確かめたら送ります。

```bash
driftpilot compare before.cfg after.cfg --platform auto --llm
```

| オプション | 意味 |
|---|---|
| `--llm` | 差分を AI に送り、説明を結果に足す |
| `--llm-dry-run` | 送る中身を出すだけで、送らない |
| `--llm-max-diffs N` | 送る差分の数の上限（既定 100、または `max_diffs`） |
| `--llm-profile NAME` | この 1 回だけ、別の AI プロファイルを使う |
| `--llm-prompt-profile NAME` | この 1 回だけ、別のプロンプトを使う（最初から入っているのは「要点と影響」「障害調査」「状態の確認」。自分で作ったものも使えます） |
| `--llm-no-premise` | 「プロンプト」タブの前提の文を付けずに送る |
| `--all-segments … --llm --confirm` | ログのとき。送る中身の集計を出し、`--confirm` を付けたときだけ送る |

`batch` では、`--confirm` を付けない `--llm` は集計を出すだけです。`--confirm` を付けると組ごとに送ります。`--llm-max-total-diffs N` を超えると、全部送りません。

伏せるもの: 秘密の語（パスワード・鍵・SNMP のコミュニティなど）がある行の値を `<secret:N>` にします。同じ値は同じ番号になります。安全に伏せられない行は送りません。`driftpilot mask-patterns` で表の全部が出ます。

## そのほかのコマンド

| コマンド | 働き |
|---|---|
| `selfcheck` | 同梱の部品とデータがそろっているかを確かめる。ネットワークを使わず、何も書きません。`--format json` もあります |
| `mask-patterns` | AI に送る前に伏せるものの表を出す（`--format markdown\|json`・`--output PATH`） |
| `patterns` | config の読み方が対応しているものを出す（`--platform fortinet\|fujitsu_sir` でそれぞれの読み方） |
| `show-commands` | 比べ方を知っている show コマンドの表を出す |
| `coverage`・`coverage-show`・`mask-coverage` | 自分の config を、読み方・テンプレート・伏せる表がどれだけ読めるかを測る（実機の config を手元で確かめる用。くわしくは `--help`） |
| `gui` | 画面を開く（ソースから入れたとき） |

`selfcheck` の出力:

```text
版: OK（driftpilot 0.2.3・Python 3.14.8）
同梱の表: OK（platform_rules.yaml 2 件・defaults.yaml）
パターン表: OK（Cisco 系 … 352 件、fortinet … 246 件、fujitsu_sir … 76 件）
show のテンプレート: OK（979 個・…）
部品の版の情報: OK（driftpilot 0.2.3・ntc_templates 9.3.0・hier_config 3.7.1・textfsm 2.1.0）
署名の確かめ: OK（Ed25519・main 5c50466f5b7455a6・backup 80db1dccd1059a79）
画面の部品（web）: OK（pywebview 6.2.1）
AI の部品: OK（openai 3.32.0）
キーの保管庫: OK（Mac キーチェーン）
すべて OK
```

## 言語

コマンドは、画面と同じ言語の設定（「設定… → 表示 → 言語」）に従います。1 回だけ変えるときは、`DRIFTPILOT_LANG=ja` か `DRIFTPILOT_LANG=en` を付けます。AI の答えの言語も変わります。

```bash
DRIFTPILOT_LANG=ja driftpilot compare before.cfg after.cfg
```

Windows の PowerShell では:

```powershell
$env:DRIFTPILOT_LANG = "ja"
.\driftpilot-cli.exe compare before.cfg after.cfg
```

## 使い方の例

**意図した config から変わっていたら CI を失敗させる**

```bash
driftpilot compare intended.cfg running.cfg --platform auto --mode structured
case $? in
  0) echo "変化なし" ;;
  1) echo "変化あり"; exit 1 ;;
  *) echo "比べられなかった"; exit 2 ;;
esac
```

PowerShell では、コマンドの後に `$LASTEXITCODE` を見ます。

**昨夜のバックアップと今日のバックアップを比べる**

```bash
driftpilot batch --pair-by-name backups/2026-11-12 backups/2026-11-13 --write-manifest pairs.yaml
# pairs.yaml の defaults に「platform: auto」を足してから:
driftpilot batch pairs.yaml --output results.jsonl --summary summary.json
jq '.counts' summary.json
```

**コメントの行を無視して、変わった行だけ**

```bash
driftpilot compare before.cfg after.cfg --mode raw --ignore-line '^\s*!' --ignore-blank-lines
```

**2 つの端末のログから 1 つのコマンドを JSON で**

```bash
driftpilot compare before.log after.log \
  --before-command "show running-config" --after-command "show running-config" \
  --format json | jq '.structured.diffs'
```

**送る中身を確かめてから AI に聞く**

```bash
driftpilot compare before.cfg after.cfg --platform auto --llm-dry-run   # 読む
driftpilot compare before.cfg after.cfg --platform auto --llm --format json | jq -r '.llm.answer'
```
