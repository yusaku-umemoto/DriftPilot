# 困ったとき

[手引きの目次へ](README.md)

## 初めて開くときに警告が出る

DriftPilot は Apple や Microsoft の署名をしていないので、初めて開くときに OS が警告を出します。

- **Windows:** 「Windows によって PC が保護されました」→ **詳細情報** → **実行**
- **Mac:** 「開発元を確認できない」→ **システム設定 → プライバシーとセキュリティ** → **このまま開く**

Windows 10 では、Microsoft Edge WebView2 Runtime が要ることがあります。

## 画面が英語で、日本語にしたい

**Settings… → Display → Language → 日本語 → Save** の後、DriftPilot を開き直します。

## 「更新を確かめる」で「公開されている版が見つかりません」と出る

更新の確かめは、GitHub の最新の Release を読みます。この文が出たら、ネットワークとプロキシの設定を確かめるか、しばらくしてからもう一度試してください。

## 更新に失敗した

更新が終わらなかったときは、DriftPilot が元の版に戻し、「更新できなかったので、元の版に戻しました。記録: …」と記録のファイルの場所を出します。DriftPilot の窓をすべて（コマンド版も）閉じて、もう一度試してください。[Releases のページ](https://github.com/yusaku-umemoto/DriftPilot/releases) から最新の版を取ってきて、手で入れ替えることもできます。

- **Windows:** 新しい版を別のフォルダーに展開し、前のフォルダーの `settings` を新しい `DriftPilot.exe` の隣にコピーします。
- **Mac:** 新しい `DriftPilot.app` を「アプリケーション」にドラッグして置き換えます。

設定と API キーは残ります。

## AI 解析が動かない

1. **設定… → AI プロファイル** で、使用中のプロファイルの種類・モデル・接続先を確かめます。
2. API キーを **保管庫に保存** で保存します。
3. **AI 接続テスト** を押します。
4. コマンドでは `driftpilot llm-check` で同じことを確かめられます。

会社でプロキシを使っている場合は、ふつうのプロキシの環境変数（`HTTPS_PROXY` など）を設定してください。更新の確かめは、OS のプロキシの設定にも従います。

## 設定はどこにある？

- **Windows の zip 版:** `DriftPilot.exe` の隣の `settings` フォルダー
- **Mac:** `~/Library/Application Support/DriftPilot/`
- **API キー:** Windows 資格情報マネージャーか Mac キーチェーン。設定の窓の **削除…** で消せます。

**設定… → フォルダを開く** で、設定のフォルダーを開けます。
