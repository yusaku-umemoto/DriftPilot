DriftPilot — はじめにお読みください

ネットワーク機器の config と show の出力を比べ、意味のある差分を取り出すツールです。

■ 起動
  Windows: フォルダーの中の DriftPilot.exe（画面）。コマンドで使うときは driftpilot-cli.exe
  Mac    : DriftPilot.app を「アプリケーション」へドラッグして開く。
           コマンドで使うときは DriftPilot.app/Contents/MacOS/driftpilot-cli
  Windows 10 などで「WebView2 Runtime が入っていません」と出たら、Microsoft のサイトから
  WebView2 Runtime を入れてください（Windows 11 には入っています）。

■ 初めて開くときの警告（署名をしていないため）
  Windows: 「Windows によって PC が保護されました」と出たら、「詳細情報」→「実行」で開けます。
  Mac    : 「開発元を確認できない」と止められたら、システム設定の
           「プライバシーとセキュリティ」を開き、DriftPilot について「このまま開く」を選びます。

■ 更新するとき
  DriftPilot を閉じてから、フォルダー（Mac は DriftPilot.app）を新しいものに置き換えてください。
  起動したままだと置き換えられません。
  Windows: 設定は展開したフォルダーの settings\ に入っています。前のフォルダーの settings\ を
           新しいフォルダーへ移してください。API キーは資格情報マネージャーに入っているので残ります。
  Mac    : 設定と API キーは置き換えても残ります。
  新しい版が出ているかは、設定画面の「更新」タブの「更新を確かめる」で確かめられます（送るのは DriftPilot の版だけです）。
  0.2.0 からは、設定画面の「更新」タブの「更新する」で入れ替えられます（設定と API キーは残ります）。

■ AI 解析の API キー
  設定画面（比較の画面の「設定…」）の「保管庫に保存」で保存します。
  Windows は資格情報マネージャー、Mac はキーチェーンに入ります（ファイルには書きません）。
  Windows で DriftPilot のフォルダーを消しても、キーは資格情報マネージャーに残ります。
  消すときは、設定画面の「削除…」で消してください。
  コマンドや自動実行で環境変数のキーを使うときは、設定画面でプロファイルごとに
  「環境変数の名前」を決めます（例: LAB と入れると DRIFTPILOT_LLM_API_KEY_LAB を読みます）。
  その変数があれば、そのプロファイルでは保管庫より優先されます。名前を決めていないプロファイルは環境変数を読みません。
  プロファイルがあるときは、共通の DRIFTPILOT_LLM_API_KEY は読みません（設定されていれば注意を出します）。
  0.1.0 で DRIFTPILOT_LLM_API_KEY を使っていた方は、プロファイルに「環境変数の名前」を決めて設定し直してください。
  プロファイルを1つも作っていないときだけ、今までどおり DRIFTPILOT_LLM_API_KEY を読みます。
  AI に送るのは差分だけで、送る前に中身を確かめて承認してから送ります。

■ 設定ファイルの場所
  Windows: 展開したフォルダーの settings\config.toml
           （例: C:\tools\DriftPilot\settings\config.toml）
           ほかの設定と画面の作業のデータも settings\ に入ります。フォルダーの外には置きません。
           フォルダーごと消せば、設定も消えます（API キーは上の「AI 解析の API キー」のとおり）。
           C:\Program Files の下など、書き込めない場所には置かないでください（設定を保存できません）。
           前の版（%APPDATA%\DriftPilot）の設定を使うときは、そのフォルダーの中身を settings\ にコピーしてください。
  Mac    : ~/Library/Application Support/DriftPilot/config.toml
  設定画面の「フォルダを開く」「設定ファイルを作る」から開けます。

■ ライセンス
  同梱している部品のライセンスは THIRD_PARTY_LICENSES.txt にあります。
