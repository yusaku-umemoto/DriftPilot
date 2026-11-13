# Sample configs

Small before/after pairs you can use to try DriftPilot. All values are made up: addresses come from the documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) and every password, key and community is fake.

[日本語は下にあります](#例の-config)

| Folder | Device | What changed |
|---|---|---|
| [`cisco/`](cisco/) | Cisco IOS-XE style | New uplink interface, new BGP neighbor with a password, BGP password rotated, new ACL entry, SNMP community changed |
| [`fortigate/`](fortigate/) | FortiGate | SSH allowed on port1, HTTP added to a firewall policy, local user password and IPsec pre-shared key changed |
| [`fujitsu-sir/`](fujitsu-sir/) | Fujitsu Si-R | New LAN interface, IKE pre-shared key changed, SNMP community changed |

## In the app

Drop a `before` file and its `after` file onto the DriftPilot window. The file named `before` becomes **Before**, and the comparison starts automatically. If you have set up an AI profile (see the [user guide](../docs/guide/gui.md#2-ai-analysis)), click **Analyze with AI**: the confirmation dialog shows the masked text, and nothing is sent until you click **Send**.

## On the command line

Run these from the repository folder (with `.venv/bin/` in front if DriftPilot is installed in a virtual environment).

Compare by meaning and line by line:

```bash
driftpilot compare examples/cisco/before.cfg examples/cisco/after.cfg
```

Let DriftPilot detect the device type:

```bash
driftpilot compare examples/fortigate/before.conf examples/fortigate/after.conf --platform auto
```

```bash
driftpilot compare examples/fujitsu-sir/before.txt examples/fujitsu-sir/after.txt --platform auto
```

Preview the masked text that AI analysis would send. Nothing is sent and no API key is needed:

```bash
driftpilot compare examples/fortigate/before.conf examples/fortigate/after.conf --mode raw --llm-dry-run
```

`--mode raw` previews the line diff, where the secret lines are. Without it, the structured differences are sent when the config could be parsed.

`driftpilot compare` exits with status 1 when it finds differences, like `diff`.

---

# 例の config

DriftPilot を試すための、変更前と変更後の小さな組です。値はすべて架空です。アドレスは文書用の範囲（192.0.2.0/24・198.51.100.0/24・203.0.113.0/24）で、パスワード・鍵・コミュニティ名もすべて作り物です。

| フォルダー | 機種 | 変わったこと |
|---|---|---|
| [`cisco/`](cisco/) | Cisco IOS-XE 系 | 上りのインターフェースの追加、パスワード付きの BGP ネイバーの追加、BGP のパスワードの変更、ACL の行の追加、SNMP のコミュニティ名の変更 |
| [`fortigate/`](fortigate/) | FortiGate | port1 で SSH を許可、ポリシーに HTTP を追加、ローカルユーザーのパスワードと IPsec の事前共有鍵の変更 |
| [`fujitsu-sir/`](fujitsu-sir/) | 富士通 Si-R | LAN のインターフェースの追加、IKE の事前共有鍵の変更、SNMP のコミュニティ名の変更 |

## 画面で

`before` と `after` のファイルを DriftPilot の窓に落とします。`before` の名前のほうが「変更前」になり、比較が自動で始まります。AI プロファイルを作ってあれば（[手引き](../docs/guide/ja/gui.md)）、「AI で解析」を押します。「AI に送る内容の確認」に伏せた後の文が出て、「送信する」を押すまで何も送りません。

## コマンドで

リポジトリのフォルダーで動かします（仮想環境に入れたときは、頭に `.venv/bin/` を付けます）。上の英語の節のコマンドと同じです。

- `driftpilot compare examples/cisco/before.cfg examples/cisco/after.cfg` — 意味の比較と行の比較
- `--platform auto` — 機種を中身から決めます（FortiGate・Si-R の例で使います）
- `--mode raw --llm-dry-run` — AI に送る文（伏せた後）を出します。送信はせず、API キーも要りません

`driftpilot compare` は、差分があると終了コード 1 で終わります（`diff` と同じ）。
