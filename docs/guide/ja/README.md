# DriftPilot の使い方の手引き

DriftPilot は、ネットワーク機器の 2 つの config（または 2 つの `show` の出力）を比べて、何が変わったかを出します。秘密を伏せてから、AI に違いの説明を頼むこともできます。

- [画面（GUI）の使い方](gui.md) — 2 つのファイルを比べる、結果の見方、AI 解析、一括比較、設定
- [コマンド（CLI）の使い方](cli.md) — `driftpilot`・`driftpilot-cli` のコマンド
- [困ったとき](troubleshooting.md) — 初めて開くときの警告、更新、AI につながらないとき

The English guide is in the [parent folder](../README.md).

## 言語

画面・コマンド・AI の答えは、既定では英語です。
日本語にするには、**Settings… → Display** の **Language** で **日本語** を選んで **Save** を押し、DriftPilot を開き直します。
1 回のコマンドだけなら、環境変数 `DRIFTPILOT_LANG=ja`（または `en`）でも変えられます。

以下の説明は、日本語の画面のときの名前で書いています。

## パソコンの外に送るもの

- AI 解析と更新の確かめを使わなければ、どこにも送りません。
- **AI 解析**では、秘密を伏せた後の差分（選べば全文も）を、設定した AI の送り先に送ります。送る前に必ず中身を見せて、確かめてから送ります（自動送信を自分でオンにしたときを除く）。
- **更新の確かめ**では、DriftPilot の版の番号だけを GitHub に送ります。
