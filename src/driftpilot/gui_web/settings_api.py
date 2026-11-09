"""pywebview の設定画面の Python の側(REQ-104 / ADR-0074 決定3)。

画面の状態(選んでいるプロファイル・編集中の値・送っている最中か・待っている確かめ)はここに持ち、
中身の判断は screen_model/settings_model.py の関数を呼ぶだけにする(wx の設定画面 gui/settings_frame.py と同じ動き)。
JavaScript(static/js/settings.js)は返した JSON を描くことと、操作をここへ渡すことだけをする。

- どのメソッドも {"ok": true, "view": {...}} を返す。知らせ(wx の MessageBox)は "notice"、確かめ(wx の MessageDialog)は
  "confirm" を足す。想定外の例外と形の違う引数は {"ok": false, "message": <文>}(bridge の _guarded)
- 確かめが要る操作は2段。1回目は確かめの文を返すだけで何も書かない。2回目(answer)で、読み直した値でもう一度確かめてから書く
- 書くのはボタンを押したときだけ(ADR-0071)。開いただけ(state)ではファイルを作らない
- 画面から受け取ったキーは保管庫に保存する関数に渡すだけで、属性・返す値・知らせ・ログに入れない(ADR-0017・0019)。
  接続テストの client(キーを持つ)も関数の中の変数だけに置く
- pywebview は js_api の _ で始まらない属性をたどって画面に出すので、内部の属性とメソッドは _ で始める
- custom-http-json(REQ-101 §4): 接続テストの応答の形の木は形だけを持ち(値は持たない)、記録しない・ログに出さない。
  定義の書き出し・読み込みのファイルの選択は、ここから窓のファイルのダイアログ(create_file_dialog)を開く
  (JavaScript から OS のファイルに触らない)。ダイアログを開いている間はロックを持たない
- CLI の形式(REQ-102 §4.1): CLI の場所の「選ぶ…」も、ここから窓のファイルのダイアログを開き、選んだ場所を欄に入れるだけ
  (ファイルには書かない)
- 気づかずに上書きしない(REQ-112): 名前・形式・つなぐ先を変えた「保存」と、保存していない変更があるときの「新規作成」
  「複製」は、3択の確かめ(confirm の choices。新しいプロファイルとして保存・書き換える・キャンセル)を返す。
  「保管庫に保存」は2段。save_key(form, None) で保管庫にキーがあれば置き換えの確かめを、無ければ proceed を返し、
  画面はそれからキーの入力欄を読んで save_key(form, key) を呼ぶ(キーは確かめの間も属性に置かない)
- AI への指示文のプロファイル(REQ-116 §3): 6つの操作(select_prompt_profile・add_prompt_profile・duplicate_prompt_profile・
  delete_prompt_profile・save_prompt_profile・use_prompt_profile)。状態は view.prompt に返し、欄を入れ直す番号は fill_prompt。
  指示文の操作は接続のプロファイルの欄(fill_editor)と設定ファイルの欄(fill_config)を変えず、接続のプロファイルの操作も
  指示文の欄を変えない。保存していない変更があるときの選択の切り替えは捨ててよいかの確かめ、新規作成・複製は REQ-112 と同じ3択。
  form の prompt_name・prompt_template は無ければ今の値のまま(文字列でなければ形の違う引数)
- 自動送信(ADR-0080 決定2): 設定ファイルの枠の「比較の後に自動で AI に送る」(form の auto_send_llm。無ければ今の値のまま、
  真偽値でなければ形の違う引数)。オフからオンへの「保存」は、使用中の接続が使えるかを接続テストと同じ作りで見て(送らない)、
  使えなければ知らせだけ、使えれば接続の名前と接続先の確かめを返す(まだ何も書かない)。「はい」で確かめ直してから書く。
  状態は view.auto_send(view.config は変えない)
- プロファイルをファイルにまとめて書き出す・取り込む(ADR-0082・REQ-119 §4): 窓の下の操作の行の export_profiles・import_profiles。
  書き出しは1回目で保存してある件数と「API キーは入りません」の確かめを返し、「はい」でロックの外で保存のダイアログを開いてから
  読み直して書く(保存していない編集は入らない)。取り込みはロックの外で開くのダイアログを開き、確かめた名前と接続先の一覧の
  確かめを返す(target はファイルのハッシュ)。「はい」でファイルを読み直して足し、一覧を読み直す(選んでいるもの・編集中の値・
  使用中は変えない)。キーは扱わず、保管庫に触らない。JSON の読み書きはここに入れない(settings_model を通す)
- 毎回付ける前提(ADR-0172 決定6 / REQ-234 §5): 「プロンプト」のタブの欄(form の premise_text。無ければ今の値のまま、
  文字列でなければ形の違う引数)と「前提を保存」(save_premise)。状態は view.premise、欄を入れ直す番号は fill_premise。
  読み書きと確かめは settings_model を通す。前提の文はログ・例外の文に入れない。
  「閉じる」(close(form))は、前提の欄に保存していない変更があるときだけ確かめを返す(ほかの欄は今までどおり確かめずに閉じる。
  form を渡さない呼び方も今までどおりすぐ閉じる)。タブの切り替えは画面の中だけで欄が消えないので確かめない

webview は読まない(窓は app.py が開いて _attach で渡す。テストは偽の窓を渡す)。
"""

import os
import platform as _platform
import subprocess
import threading
from collections.abc import Callable, Mapping
from importlib import metadata
from pathlib import Path
from typing import Any

from driftpilot.gui_web.bridge import Api, _failure, _guarded, notify
from driftpilot.llm_analyzer import LLMClient, ShapeNode, send_connection_check
from driftpilot.llm_settings import CUSTOM_HTTP_JSON, ENV_KEY_PREFIX, SettingsError
from driftpilot.messages import (
    AUTO_SEND_CONFIRM_TITLE,
    CLI_MODEL_HINT,
    CONFIG_SAVE_NOTE,
    CONFIG_SAVE_TITLE,
    CUSTOM_FILE_TITLE,
    CUSTOM_FORMAT_REQUIRED,
    DIALOG_CANCEL,
    DIALOG_NO,
    DIALOG_OK,
    DIALOG_YES,
    LLM_CHECK_DECLINED,
    LLM_CHECK_DONT_SEND,
    LLM_CHECK_SEND,
    LLM_CHECK_SENDING,
    LLM_CHECK_TITLE,
    MIGRATE_TITLE,
    PROFILE_ALREADY_IN_USE,
    PROFILE_DISCARD_CHANGES,
    PROFILE_MARK_IN_USE,
    PROFILE_OVERWRITE,
    PROFILE_SAVE_AS_NEW,
    PROFILE_BUNDLE_TITLE,
    PROFILE_SAVE_FIRST,
    PROFILES_TITLE,
    PREMISE_CLOSE_CONFIRM,
    SETTINGS_BUTTON_CLOSE,
    SETTINGS_PREMISE_COUNT,
    SETTINGS_PREMISE_NOTES,
    SETTINGS_PREMISE_TITLE,
    PROMPT_BUILTIN_READ_ONLY,
    PROMPT_DISCARD_CHANGES,
    PROMPT_TEMPLATE_TITLE,
    SETTINGS_API_KEY_DELETE_TITLE,
    SETTINGS_API_KEY_TITLE,
    SETTINGS_TEMPLATE_CREATED_TITLE,
    SETTINGS_TEMPLATE_FAILED_TITLE,
    api_key_delete_confirm,
    custom_import_confirm,
    fingerprint_line,
    gui_web_call_failed,
    key_target_unknown,
    llm_check_failed,
    migrate_overwrite_declined,
    profile_bundle_export_confirm,
    profile_delete_confirm,
    profile_overwrite_confirm,
    profile_row_detail,
    profile_unreadable,
    prompt_profile_delete_confirm,
    secret_words_in_effect,
    template_created,
)
from driftpilot.messages import RAW_DEFAULTS_TITLE
from driftpilot.screen_model.settings_model import RawDefaultsPane, raw_defaults_pane, save_raw_defaults_form
from driftpilot.screen_model.settings_model import (
    AUTH_CHOICES,
    CUSTOM_AUTH_CHOICES,
    DEFAULT_TEMPLATE,
    FORMAT_CHOICES,
    FORMAT_FIELDS,
    PROFILE_TEMPLATES,
    TEMPLATE_CHOICES,
    THINKING_CHOICES,
    WIRE_TEXT_FIELDS,
    ProfileAction,
    ProfileEditor,
    ProfileWireText,
    PremiseView,
    PromptPane,
    SettingsView,
    premise_changed,
    premise_saved_notice,
    premise_values,
    save_premise,
    add_new_profile,
    add_prompt,
    auto_send_start,
    auto_send_turned_on,
    base_url_hint,
    cli_effort_choices,
    config_changed,
    config_fields_changed,
    connection_check_outcome,
    connection_check_start,
    create_template,
    delete_api_key,
    delete_prompt,
    delete_refusal,
    delete_selected,
    duplicate_prompt,
    duplicate_selected,
    editor_changed,
    export_custom_definition,
    export_profile_bundle,
    import_custom_definition,
    import_profile_bundle,
    kept_input,
    key_replace_confirm,
    key_target,
    migrate_plaintext_key,
    migrate_start,
    overwrite_needs_confirm,
    prepare_bundle_export,
    profile_input_problem,
    prompt_changed,
    prompt_delete_refusal,
    prompt_input_problem,
    prompt_pane,
    prompt_profiles_changed,
    read_custom_definition,
    read_profile_bundle,
    response_shape_view,
    save_api_key,
    save_as_new,
    save_config,
    save_prompt,
    save_prompt_as_new,
    save_selected,
    settings_view,
    store_problem,
    use_prompt,
    use_selected,
)

# 画面から受け取る欄の値(form)の鍵。キーの入力欄は入れない(save_key の引数で別に受け取る)
PROFILE_FIELDS = ("name", "api_format", "model", "base_url")
# 送り方の細かい設定の欄(REQ-100 §6.2)。どれも文字で受け取り、変換は settings_model.parse_wire_text。
# 「API キー」の枠の環境変数の名前の欄(env_suffix。ADR-0077)も同じ仕組みで受け取る(保存はプロファイルの「保存」)
WIRE_FIELDS = WIRE_TEXT_FIELDS
# 設定ファイルの枠の文字の欄(AI への指示文は REQ-116 で独立した枠に出した)
CONFIG_TEXT_FIELDS = ("max_diffs", "max_total_diffs", "words")
# 設定ファイルの枠の欄(文字の欄と、真偽値の「自動で比較を始める」のチェック。ADR-0076 決定3)
CONFIG_FIELDS = (*CONFIG_TEXT_FIELDS, "auto_compare")
# 「キャラクター（Mirarin）を表示する」のチェック(ADR-0174 決定1)。省いてよい欄(無ければ今の値のまま)。
# 画面へは view の "character" で返す(config の values の形は変えない)
CHARACTER_FIELD = "character"
# 文字で受け取る欄(真偽値の auto_compare・character は _take で別に確かめる)
FORM_FIELDS = PROFILE_FIELDS + WIRE_FIELDS + CONFIG_TEXT_FIELDS
# 「AI への指示文」の枠の欄(名前・本文。REQ-116 §3)。無ければ今の値のまま、文字列でなければ _BadRequest
PROMPT_FIELDS = ("prompt_name", "prompt_template")
# 「毎回付ける前提」の欄(ADR-0172 決定6)。無ければ今の値のまま、文字列でなければ _BadRequest
PREMISE_FIELD = "premise_text"
FORMAT_VALUES = tuple(value for value, _ in FORMAT_CHOICES)
# 選択の欄の決まった値(これ以外は _BadRequest)
CHOICE_VALUES = {
    "api_format": FORMAT_VALUES,
    "auth_header": tuple(value for value, _ in AUTH_CHOICES),
    "thinking": tuple(value for value, _ in THINKING_CHOICES),
    "custom_auth": tuple(value for value, _ in CUSTOM_AUTH_CHOICES),
}
_EMPTY_TYPED = ("", FORMAT_VALUES[0], "", "", ProfileWireText())
_EMPTY_CONFIG = ("", "", "", False, False)  # 設定ファイルが読めないとき
# Python から画面への知らせの種類(送っている最中の状態)。static/js/settings.js が受ける
VIEW_EVENT = "settings.view"
# 窓のファイルのダイアログの種類(webview.FileDialog の OPEN・SAVE の値。webview は読まない)と、選べるファイルの種類
DIALOG_OPEN = 10
DIALOG_SAVE = 30
JSON_FILE_TYPES = ("JSON (*.json)",)
CUSTOM_FILE_NAME = "custom-http-json.json"  # 書き出すときの既定の名前
BUNDLE_FILE_NAME = "driftpilot-profiles.json"  # プロファイルをまとめて書き出すときの既定の名前(REQ-119)
# 3択の確かめ(上書きの確かめ。REQ-112)を出す操作と、その答え。ほかの確かめの答えは真偽値
PROMPT_CHOICE_ACTIONS = ("add_prompt_profile", "duplicate_prompt_profile")  # 指示文の枠(REQ-116 §3)
CHOICE_ACTIONS = ("save_profile", "add_profile", "duplicate_profile", *PROMPT_CHOICE_ACTIONS)
CHOICE_NEW, CHOICE_OVERWRITE, CHOICE_CANCEL = "new", "overwrite", "cancel"
CHOICE_ANSWERS = (CHOICE_NEW, CHOICE_OVERWRITE, CHOICE_CANCEL)
# 返す値の "focus"(画面がフォーカスを移す欄)と "proceed"(画面が続けてする操作)
FOCUS_NAME = "name"
FOCUS_PROMPT_NAME = "prompt_name"
PROCEED_SAVE_KEY = "save_key"


class _BadRequest(Exception):
    """画面から受け取った引数の形が違う。"""


def web_version() -> str:
    """診断に出す pywebview の版(selfcheck と同じくメタデータから取る)。取れなければ ?。"""
    try:
        return metadata.version("pywebview")
    except metadata.PackageNotFoundError:
        return "?"


def open_in_os(path: Path, *, system: str | None = None) -> None:
    """フォルダを OS の既定のアプリで開く。Windows は os.startfile、Mac は open(リストの引数。shell は使わない)。"""
    system = system or _platform.system()
    if system == "Windows":
        os.startfile(str(path))  # type: ignore[attr-defined]  # Windows にだけある
    elif system == "Darwin":
        subprocess.run(["open", str(path)], check=False)


class SettingsApi(Api):
    """設定画面から呼べるメソッド(window.pywebview.api.<名前>)。REQ-103 の app_info・messages も使える。

    cwd・env・platform・home は設定の読み書きの場所(テストで一時フォルダを渡す。省くと利用者の設定)。
    client_factory は接続テストの client を作る関数(テストで偽物にする)。
    """

    def __init__(
        self,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        platform: str | None = None,
        home: Path | None = None,
        client_factory: Callable[..., LLMClient] | None = None,
        web_version: str | None = None,
        opener: Callable[[Path], None] = open_in_os,
    ) -> None:
        self._where: dict[str, Any] = {"cwd": cwd, "env": env, "platform": platform, "home": home}
        self._client_factory = client_factory
        self._web_version = web_version
        self._opener = opener
        self._window: Any = None
        # 状態はここだけに持つ。pywebview は js_api の呼び出しを別々のスレッドで動かすので、触るのはロックの中だけ
        self._lock = threading.RLock()
        self._view: SettingsView | None = None
        self._selected: str | None = None
        # プロファイルの欄: (名前, 形式, モデル, 接続先, 送り方の欄の文字)
        self._typed: tuple[str, str, str, str, ProfileWireText] = _EMPTY_TYPED
        # 設定ファイルの枠の欄: (上限1組, 上限全体, 伏せる語, 自動で比較を始める, キャラクターを表示する)
        self._config_typed: tuple[str, str, str, bool, bool] = _EMPTY_CONFIG
        # 設定ファイルの枠の「比較の後に自動で AI に送る」(ADR-0080 決定2)
        self._auto_send_typed = False
        # 「AI への指示文」の枠(REQ-116 §3): 読んだ状態・選んでいる指示文・欄の値(名前, 本文)
        self._prompt: PromptPane | None = None
        self._prompt_selected: str | None = None
        self._prompt_typed: tuple[str, str] = ("", "")
        # 「毎回付ける前提」の欄(ADR-0172 決定6): 読んだ状態・欄の文
        self._premise: PremiseView | None = None
        self._premise_typed = ""
        self._check: tuple[str, str] | None = None  # 接続テストの結果の行(文, ok・failed・neutral)
        self._shape: ShapeNode | None = None  # 接続テストの応答の形の木(custom-http-json。名前と型だけ)
        self._sending = False
        # 待っている確かめ: (action, target, 確かめの文, 2回目で使う値)
        self._pending: tuple[str, Any, str, Any] | None = None
        self._revision = 0  # 返す view の番号(画面は古い番号の view を捨てる)
        self._fill_editor = 0  # 変わったら、画面はプロファイルの欄に editor_values を入れ直す
        self._fill_config = 0  # 変わったら、画面は設定ファイルの欄に config.values を入れ直す
        self._fill_prompt = 0  # 変わったら、画面は指示文の欄に prompt.values を入れ直す
        self._fill_premise = 0  # 変わったら、画面は前提の欄に premise.text を入れ直す
        self._clear_key = 0  # 変わったら、画面はキーの入力欄を空にする
        # 「単純比較」のタブ(REQ-227 §12): 読んだ既定(raw-compare.json)・欄の値・誤りの文・欄を入れ直す番号
        self._raw_pane: RawDefaultsPane | None = None
        self._raw_values: dict[str, Any] = {}
        self._raw_problems: list[str] = []
        self._fill_raw = 0  # 変わったら、画面は「単純比較」のタブの欄に raw.values を入れ直す
        # 保存の後に呼ぶ口(ADR-0176 決定4。WebSession.open_settings が渡す)と、この呼び出しで保存できたかの印
        self._on_saved: Callable[[], None] | None = None
        self._saved = False

    def _attach(self, window: Any) -> None:
        """pywebview の窓(知らせ・閉じるに使う)。app.run が create_window の後に渡す。"""
        self._window = window

    def _set_on_saved(self, callback: Callable[[], None] | None) -> None:
        """保存できた後に呼ぶ関数(開いている比較の窓と一括比較の窓へ知らせる。ADR-0176 決定4)。

        AI プロファイル・プロンプト・比較と自動の動き・設定ファイル・単純比較の既定・前提の保存が成功した呼び出しの最後に、
        ロックの外で 1 回だけ呼ぶ。失敗は握りつぶす。
        """
        self._on_saved = callback

    def _mark_saved(self, ok: bool = True) -> None:
        """ロックの中で呼ぶ。ok なら、この呼び出しの最後に保存の後の口を呼ぶ印を立てる。"""
        if ok:
            self._saved = True

    # --- 画面から呼ぶメソッド(form は画面の欄の値の dict。キーは入れない) ---

    def state(self) -> dict[str, Any]:
        """開いたときの状態。読むだけで、ファイルを作らない。"""
        return self._call(self._state)

    def select_profile(self, form: Any, profile_id: Any) -> dict[str, Any]:
        """一覧で別のプロファイルを選ぶ。保存していない編集があれば確かめる。"""
        return self._call(self._select_profile, form, profile_id)

    def save_profile(self, form: Any) -> dict[str, Any]:
        """「保存」。名前・形式・つなぐ先を変えたときは3択の確かめを返す(REQ-112)。"""
        return self._call(self._save_profile, form)

    def add_profile(self, form: Any, template: Any = DEFAULT_TEMPLATE) -> dict[str, Any]:
        """「新規作成」。template は「種類」で選んだひな形(REQ-100 §5)。保存していない変更があれば3択の確かめ。"""
        return self._call(self._add_profile, form, template)

    def duplicate_profile(self, form: Any) -> dict[str, Any]:
        """「複製」。保存していない変更があれば3択の確かめ(REQ-112)。"""
        return self._call(self._duplicate_profile, form)

    def delete_profile(self, form: Any) -> dict[str, Any]:
        """「削除…」(プロファイル)。消せるときは確かめを返す。"""
        return self._call(self._delete_profile, form)

    def use_profile(self, form: Any) -> dict[str, Any]:
        return self._call(self._use_profile, form)

    def save_key(self, form: Any, key: Any) -> dict[str, Any]:
        """「保管庫に保存」。key は保存する関数に渡すだけ(残さない)。

        key が None なら1段目(REQ-112): 保管庫にキーがあれば置き換えの確かめを、無ければ proceed を返す(保存しない)。
        """
        return self._call(self._save_key, form, key)

    def delete_key(self, form: Any) -> dict[str, Any]:
        """「削除…」(キー)。確かめを返す。"""
        return self._call(self._delete_key, form)

    def check_connection(self, form: Any) -> dict[str, Any]:
        """「AI 接続テスト」。送れるときは確かめを返す(送るのは answer の中)。"""
        return self._call(self._check_connection, form)

    def save_config(self, form: Any) -> dict[str, Any]:
        """設定ファイルの枠の「保存」。"""
        return self._call(self._save_config, form)

    def save_raw_defaults(self, values: Any) -> dict[str, Any]:
        """「単純比較」のタブの「保存」(REQ-227 §12)。raw-compare.json だけを書く(config.toml は書かない)。

        values は単純比較の設定の欄の値(比較の窓の raw_options と同じ形)。誤りがあれば書かずに誤りの文を出す。
        """
        return self._call(self._save_raw_defaults, values)

    def migrate(self, form: Any) -> dict[str, Any]:
        """「保管庫に移す」。確かめを返す。"""
        return self._call(self._migrate, form)

    def create_template(self, form: Any) -> dict[str, Any]:
        """「ひな形を作る」。"""
        return self._call(self._create_template, form)

    def export_custom(self, form: Any) -> dict[str, Any]:
        """「定義を書き出す…」(custom-http-json)。保存のダイアログで選んだ場所に、キーの無い定義を書く。"""
        return self._call(self._export_custom, form)

    def import_custom(self, form: Any) -> dict[str, Any]:
        """「定義を読み込む…」(custom-http-json)。開くのダイアログで選んだファイルを確かめ、確かめを返す。"""
        return self._call(self._import_custom, form)

    def choose_cli_path(self, form: Any) -> dict[str, Any]:
        """CLI の場所の「選ぶ…」(CLI の形式)。開くのダイアログで選んだ場所を欄に入れた状態を返す(ファイルには書かない)。"""
        return self._call(self._choose_cli_path, form)

    def select_prompt_profile(self, form: Any, profile_id: Any) -> dict[str, Any]:
        """「AI への指示文」の一覧で別の指示文を選ぶ。保存していない変更があれば確かめる。"""
        return self._call(self._select_prompt_profile, form, profile_id)

    def add_prompt_profile(self, form: Any) -> dict[str, Any]:
        """指示文の「新規作成」(既定の文で作る)。保存していない変更があれば3択の確かめ。"""
        return self._call(self._add_prompt_profile, form)

    def duplicate_prompt_profile(self, form: Any) -> dict[str, Any]:
        """指示文の「複製」(最初から入っているものも)。保存していない変更があれば3択の確かめ。"""
        return self._call(self._duplicate_prompt_profile, form)

    def delete_prompt_profile(self, form: Any) -> dict[str, Any]:
        """指示文の「削除…」。消せるときは確かめを返す。"""
        return self._call(self._delete_prompt_profile, form)

    def save_prompt_profile(self, form: Any) -> dict[str, Any]:
        """指示文の「保存」(最初から入っているものは断る)。"""
        return self._call(self._save_prompt_profile, form)

    def use_prompt_profile(self, form: Any) -> dict[str, Any]:
        """指示文の「このプロファイルを使う」。"""
        return self._call(self._use_prompt_profile, form)

    def save_premise(self, form: Any) -> dict[str, Any]:
        """「前提を保存」(ADR-0172 決定6)。form の premise_text を確かめてから書く。断ったら入れた文を欄に残す。"""
        return self._call(self._save_premise, form)

    def answer(self, form: Any, action: Any, target: Any, yes: Any) -> dict[str, Any]:
        """確かめの答え(2段目)。待っている確かめと action・target が合うときだけ、条件を確かめ直して行う。

        yes は真偽値。3択の確かめ(REQ-112)では "new"・"overwrite"・"cancel" のどれか。
        """
        return self._call(self._answer, form, action, target, yes)

    def focus(self, form: Any) -> dict[str, Any]:
        """窓が前に来たとき。設定ファイルが外で変わっていれば読み直す(ADR-0071 決定4)。"""
        return self._call(self._focus, form)

    def open_folder(self) -> dict[str, Any]:
        """「フォルダを開く」。設定ファイルのあるフォルダ(無ければいちばん近い既存の祖先)。"""
        return self._call(self._open_folder)

    def export_profiles(self, form: Any) -> dict[str, Any]:
        """「プロファイルを書き出す…」。件数と「API キーは入りません」の確かめを返す(場所を選ぶのは answer の中)。"""
        return self._call(self._export_profiles, form)

    def import_profiles(self, form: Any) -> dict[str, Any]:
        """「プロファイルを取り込む…」。開くのダイアログで選んだファイルを確かめ、名前と接続先の一覧の確かめを返す。"""
        return self._call(self._import_profiles, form)

    def close(self, form: Any = None) -> dict[str, Any]:
        """「閉じる」。前提の欄に保存していない変更があるときだけ確かめを返す(閉じるのは answer の「閉じる」)。

        ほかの欄の保存していない編集は確かめずに捨てる(今までどおり)。form を渡さないときも確かめずに閉じる。
        """
        return self._call(self._close_asked, form)

    # --- 呼び出しの共通 ---

    def _config_args(self) -> tuple[str, str, str, None, bool]:
        """設定ファイルの枠の欄を、save_config・config_fields_changed の引数の並びにする(指示文の位置は None)。"""
        max_diffs, max_total_diffs, words, auto_compare, _character = self._config_typed
        return max_diffs, max_total_diffs, words, None, auto_compare

    def _config_changed_now(self, fields: Any) -> bool:
        """設定ファイルの枠に保存していない入力があるか(自動送信とキャラクターのチェックも見る)。"""
        return config_fields_changed(
            fields, *self._config_args(), auto_send_llm=self._auto_send_typed, character=self._config_typed[4]
        )

    def _call(self, func: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            try:
                return func(*args)
            except _BadRequest:
                return _failure(gui_web_call_failed())

        result = _guarded(run)
        self._notify_saved()
        return result

    def _notify_saved(self) -> None:
        """保存できた印が立っていれば消して、保存の後の口を呼ぶ(ロックの外で。失敗は握りつぶす。ADR-0176 決定4)。"""
        with self._lock:
            saved, self._saved = self._saved, False
            callback = self._on_saved
        if not saved or callback is None:
            return
        try:
            callback()
        except Exception:  # noqa: BLE001 - 閉じかけの窓への知らせの失敗は捨てる
            pass

    def _kw(self, *names: str) -> dict[str, Any]:
        return {name: self._where[name] for name in names}

    def _take(self, form: Any) -> None:
        """画面の欄の値を受け取る。形が違えば _BadRequest。"""
        if not isinstance(form, dict) or not all(isinstance(form.get(name), str) for name in FORM_FIELDS):
            raise _BadRequest
        if any(form[name] not in values for name, values in CHOICE_VALUES.items()):
            raise _BadRequest
        if not isinstance(form.get("auto_compare"), bool):
            raise _BadRequest
        if any(name in form and not isinstance(form[name], str) for name in PROMPT_FIELDS):
            raise _BadRequest
        if "auto_send_llm" in form and not isinstance(form["auto_send_llm"], bool):
            raise _BadRequest
        # キャラクターのチェックは省いてよい(無ければ今の値のまま。ADR-0174 決定1)
        if CHARACTER_FIELD in form and not isinstance(form[CHARACTER_FIELD], bool):
            raise _BadRequest
        self._take_premise(form)
        wire =ProfileWireText(**{name: form[name] for name in WIRE_FIELDS})
        self._auto_send_typed = form.get("auto_send_llm", self._auto_send_typed)
        self._typed = (*(form[name] for name in PROFILE_FIELDS), wire)  # type: ignore[assignment]
        character = form.get(CHARACTER_FIELD, self._config_typed[4])
        self._config_typed = (*(form[name] for name in CONFIG_FIELDS), character)  # type: ignore[assignment]
        self._prompt_typed = tuple(  # type: ignore[assignment]
            form.get(name, current) for name, current in zip(PROMPT_FIELDS, self._prompt_typed, strict=True)
        )

    def _take_premise(self, form: Any) -> None:
        """前提の欄の文を受け取る(無ければ今の値のまま)。form が dict でない・文字列でなければ _BadRequest。"""
        if not isinstance(form, dict):
            raise _BadRequest
        if PREMISE_FIELD in form:
            if not isinstance(form[PREMISE_FIELD], str):
                raise _BadRequest
            self._premise_typed = form[PREMISE_FIELD]

    def _ok(self, **extra: Any) -> dict[str, Any]:
        return {"ok": True, "view": self._render(), **extra}

    def _notice(self, body: str, title: str) -> dict[str, Any]:
        return {"title": title, "body": body, "ok": DIALOG_OK}

    def _confirm(
        self, action: str, target: Any, body: str, title: str, *, value: Any = None, yes: str = DIALOG_YES, no: str = DIALOG_NO
    ) -> dict[str, Any]:
        """確かめを返す(何も書かない)。既定のボタンは安全なほう(no)。"""
        self._pending = (action, target, body, value)
        return self._ok(confirm={"title": title, "body": body, "yes": yes, "no": no, "action": action, "target": target})

    def _confirm_choices(
        self, action: str, target: str, name: str, *, title: str = PROFILES_TITLE, value: Any = None
    ) -> dict[str, Any]:
        """上書きの確かめ(3択。REQ-112)を返す(何も書かない)。既定のフォーカスは「新しいプロファイルとして保存」、Esc はキャンセル。

        target は書き換える対象の id、name はその保存した名前(接続のプロファイルか指示文)。
        """
        body = profile_overwrite_confirm(name)
        self._pending = (action, target, body, value)
        choices = [
            {"value": CHOICE_NEW, "label": PROFILE_SAVE_AS_NEW},
            {"value": CHOICE_OVERWRITE, "label": PROFILE_OVERWRITE},
            {"value": CHOICE_CANCEL, "label": DIALOG_CANCEL},
        ]
        confirm = {"title": title, "body": body, "action": action, "target": target}
        return self._ok(confirm={**confirm, "choices": choices, "focus": CHOICE_NEW, "cancel": CHOICE_CANCEL})

    # --- 読み直し(wx の refresh・_refresh_keeping_edits と同じ判断) ---

    def _refresh(self, select: str | None = None, *, keep_edits: bool = False) -> None:
        before = self._view
        before_id = self._selected
        before_editor = before.editor if before is not None else None
        before_fields = before.config_fields if before is not None else None
        if select is not None:
            self._selected = select
        view = settings_view(web_version=self._web_version or web_version(), selected=self._selected, **self._where)
        self._view = view
        self._selected = view.selected_id
        if before_id is not None and before_id != view.selected_id:
            # 別のプロファイルの値を残さない(キーの入力欄と接続テストの結果の行・応答の形の木)
            self._check = None
            self._shape = None
            self._clear_key += 1
        # プロファイルの欄: 読んだ値を入れる。同じプロファイルで保存していない編集があれば、入れた値を残す
        kept = kept_input(before_editor, view.editor, self._typed) if keep_edits else None
        editor = view.editor
        if kept is not None:
            self._typed = kept
        elif editor is not None:
            self._typed = (editor.name, editor.api_format, editor.model, editor.base_url, editor.wire)
        else:
            self._typed = _EMPTY_TYPED
        self._fill_editor += 1
        # 設定ファイルの欄: ファイルが変わっていなければ、保存していない入力を残す。変わっていれば読んだ値を入れ直す
        fields = view.config_fields
        keep = (
            before_fields is not None
            and fields is not None
            and before_fields.digest == fields.digest
            and self._config_changed_now(before_fields)
        )
        if not keep:
            if fields is not None:
                self._config_typed = (
                    str(fields.max_diffs),
                    str(fields.max_total_diffs),
                    fields.words_text,
                    fields.auto_compare,
                    fields.character,
                )
                self._auto_send_typed = fields.auto_send_llm
            else:
                self._config_typed = _EMPTY_CONFIG
                self._auto_send_typed = False
            self._fill_config += 1

    def _refresh_prompt(self, select: str | None = None, *, keep_edits: bool = False) -> None:
        """「AI への指示文」の枠を読み直す(接続のプロファイルと設定ファイルの欄は変えない)。

        keep_edits なら、同じ指示文のままで保存していない変更があるときだけ、入れた値を残す(確認なく消さない)。
        """
        before = self._prompt
        if select is not None:
            self._prompt_selected = select
        pane = prompt_pane(self._prompt_selected, **self._kw("env", "platform", "home"))
        self._prompt = pane
        self._prompt_selected = pane.selected_id
        if (
            keep_edits
            and before is not None
            and before.selected_id == pane.selected_id
            and prompt_changed(before, *self._prompt_typed)
        ):
            return
        self._prompt_typed = (pane.name, pane.template)
        self._fill_prompt += 1

    def _profiles_changed(self) -> None:
        """ほかの窓(比較の窓の AI の面)が使用中のプロファイルを変えた後に、WebSession がロックの外で呼ぶ(REQ-206 §5)。

        保存していない編集は残して読み直し(_refresh・_refresh_prompt を keep_edits で)、画面には settings.view の知らせで描き直させる。
        まだ一度も描いていなければ何もしない(最初の state() で読む)。窓への知らせはロックの外で送る。
        """
        with self._lock:
            if self._view is None and self._prompt is None:
                return
            self._refresh(keep_edits=True)
            self._refresh_prompt(keep_edits=True)
            view = self._render()
        window = self._window
        if window is not None:
            notify(window, {"type": VIEW_EVENT, "view": view})

    def _current(self) -> SettingsView:
        if self._view is None:
            self._refresh()
        assert self._view is not None
        return self._view

    def _refresh_raw(self) -> None:
        """ロックの中で呼ぶ。「単純比較」のタブを読み直す(欄は読んだ値。誤りの文は消す。読めなければ欄は既定で帯の文)。"""
        pane = raw_defaults_pane(**self._kw("env", "platform", "home"))
        self._raw_pane = pane
        self._raw_values = dict(pane.values)
        self._raw_problems = []
        self._fill_raw += 1

    def _current_raw(self) -> RawDefaultsPane:
        if self._raw_pane is None:
            self._refresh_raw()
        assert self._raw_pane is not None
        return self._raw_pane

    def _current_prompt(self) -> PromptPane:
        if self._prompt is None:
            self._refresh_prompt()
        assert self._prompt is not None
        return self._prompt

    def _refresh_premise(self) -> None:
        """ロックの中で呼ぶ。「毎回付ける前提」を読み直し、欄を保存してある文で入れ直す。"""
        premise = premise_values(**self._kw("env", "platform", "home"))
        self._premise = premise
        self._premise_typed = premise.text
        self._fill_premise += 1

    def _current_premise(self) -> PremiseView:
        if self._premise is None:
            self._refresh_premise()
        assert self._premise is not None
        return self._premise

    # --- 画面に描く状態 ---

    def _render(self) -> dict[str, Any]:
        view = self._current()
        pane = self._current_prompt()
        raw = self._current_raw()
        premise = self._current_premise()
        prompt_name, prompt_template = self._prompt_typed
        self._revision += 1
        editor = view.editor
        fields = view.config_fields
        name, api_format, model, base_url, wire = self._typed
        words = secret_words_in_effect(view.secret_words)
        return {
            "revision": self._revision,
            "status": {"text": view.status, "ok": view.status_ok},
            "bands": {
                "plaintext": view.plaintext_warning,
                "workdir_key": "\n".join(view.workdir_warning) if view.workdir_warning else None,
                "model": view.workdir_model_warning,
                "limits": view.limits_warning,
                "common_env": view.common_env_warning,
            },
            "error": profile_unreadable(view.error) if view.error else None,
            "migrate": (
                None if view.migrate is None else {"available": view.migrate.available, "reason": view.migrate.reason}
            ),
            # 一覧の行(REQ-113 §1)。使用中は ▶ と「· 使用中」の文ではなく、名前の右のバッジ(badge)と緑の帯で出す。
            # wx も使う row.title・row.detail(▶ と「· 使用中」が入る)は使わない
            "profiles": [
                {
                    "id": row.id,
                    "title": row.name,
                    "detail": profile_row_detail(row.format_label, in_use=False, has_key=row.has_key),
                    "badge": PROFILE_MARK_IN_USE if row.in_use else "",
                    "in_use": row.in_use,
                    "selected": row.selected,
                }
                for row in view.profiles
            ],
            "editor": None if editor is None else {"id": editor.id, "in_use": editor.in_use},
            # 編集の枠の見出しの横に出す名前(保存した名前。打っている途中の名前ではない。REQ-112)
            "editor_name": "" if editor is None else editor.name,
            "editor_values": {
                "name": name,
                "api_format": api_format,
                "model": model,
                "base_url": base_url,
                **wire.values(),
            },
            "in_use_text": PROFILE_ALREADY_IN_USE if editor is not None and editor.in_use else "",
            "format_choices": [
                {"value": value, "label": label, "hint": base_url_hint(value)} for value, label in view.format_choices
            ],
            # モデルの欄に薄く出す例の文(CLI の形式だけ。REQ-113 §4)。無い形式は空にする
            "model_hints": dict(CLI_MODEL_HINT),
            # 形式ごとに出す送り方の欄(画面はこの表どおりに出し入れするだけ。REQ-100 §6.2)
            "format_fields": {value: list(names) for value, names in FORMAT_FIELDS.items()},
            "auth_choices": [{"value": value, "label": label} for value, label in AUTH_CHOICES],
            "thinking_choices": [{"value": value, "label": label} for value, label in THINKING_CHOICES],
            # CLI の2形式の Thinking の深さの選択肢(形式ごと。先頭は空の値の「既定」。ADR-0157 決定12)
            "cli_effort_choices": cli_effort_choices(),
            "custom_auth_choices": [{"value": value, "label": label} for value, label in CUSTOM_AUTH_CHOICES],
            "template_choices": [{"value": value, "label": label} for value, label in TEMPLATE_CHOICES],
            "template_default": DEFAULT_TEMPLATE,
            "key": {
                # 環境変数の名前の欄の前に出す、固定の頭の部分(ADR-0077 決定1。続きだけを入力する)
                "env_prefix": ENV_KEY_PREFIX,
                "summary": view.key_summary,
                "sources": [
                    {"text": ("▶ " if row.in_use else "   ") + row.line, "in_use": row.in_use} for row in view.key_sources
                ],
                "help": [{"text": line, "command": line in view.key_help_commands} for line in view.key_help],
                "store_note": view.store_note,
                "profile_note": view.key_profile_note,
            },
            "config": {
                "values": dict(zip(CONFIG_FIELDS, self._config_typed[: len(CONFIG_FIELDS)], strict=True)),
                "limits_note": fields.limits_note if fields else view.secret_words,
                "words_note": f"{view.secret_words_note}\n{view.read_only_note}\n{CONFIG_SAVE_NOTE}",
                "words_in_effect": f"{words}\n{fields.workdir_words}" if fields else words,
            },
            # 「比較の後に自動で AI に送る」のチェック(ADR-0080 決定2)。欄は fill_config が変わったときに入れ直す
            "auto_send": {"checked": self._auto_send_typed},
            # 「キャラクター（Mirarin）を表示する」のチェック(ADR-0174 決定1)。欄は fill_config が変わったときに入れ直す
            "character": {"checked": self._config_typed[4]},
            # 「AI への指示文」の枠(REQ-116 §3)。一覧の使用中は接続のプロファイルと同じバッジと帯(REQ-113 §1)。
            # readonly(最初から入っているもの)のときは欄を読むだけにし、「保存」「削除…」を押せなくする
            "prompt": {
                "rows": [
                    {
                        "id": row.id,
                        "title": row.name,
                        "detail": row.detail,
                        "badge": PROFILE_MARK_IN_USE if row.in_use else "",
                        "in_use": row.in_use,
                        "selected": row.selected,
                    }
                    for row in pane.rows
                ],
                "values": {"prompt_name": prompt_name, "prompt_template": prompt_template},
                "saved_name": pane.name,
                "readonly": pane.readonly,
                "can_save": pane.can_edit,
                "can_delete": pane.can_edit,
                "in_use_text": PROFILE_ALREADY_IN_USE if pane.selected_id is not None and pane.selected_id == pane.active_id else "",
                "error": pane.error,
                "user_config_note": pane.user_config_note,
            },
            # 「プロンプト」のタブの「毎回付ける前提」(ADR-0172 決定6)。text は欄の文(fill_premise が変わったときに入れ直す)、
            # notes は案内の 3 行、max_chars と count_format は文字数の表示だけに使う(判定は Python)、error は読めないときの帯の文
            "premise": {
                "text": self._premise_typed,
                "notes": list(SETTINGS_PREMISE_NOTES),
                "max_chars": premise.max_chars,
                "count_format": SETTINGS_PREMISE_COUNT,
                "error": premise.error,
            },
            "diagnostics": {
                "fingerprints": [[row.name, fingerprint_line(row.count, row.digest)] for row in view.fingerprints],
                "versions": [[label, value] for label, value in view.versions],
                "location": f"{view.config_path}\n{view.config_state}",
            },
            "can_write_template": view.can_write_template,
            "check": None if self._check is None else {"text": self._check[0], "state": self._check[1]},
            # 接続テストの応答の形の木(custom-http-json。名前と型と道筋だけ。REQ-101 §4.2)。無ければ null
            "response_shape": response_shape_view(self._shape),
            "sending": self._sending,
            "fill_editor": self._fill_editor,
            "fill_config": self._fill_config,
            "fill_prompt": self._fill_prompt,
            "fill_premise": self._fill_premise,
            "clear_key": self._clear_key,
            # 「単純比較」のタブ(REQ-227 §12)。values は欄の値、problems は保存で断った誤りの文、examples は例から足す選択肢、
            # error は既定のファイルを読めないときの帯の文(欄は既定で出す)、fill は変わったら画面が欄に値を入れ直す番号
            "raw": {
                "values": dict(self._raw_values),
                "problems": list(self._raw_problems),
                "examples": raw.examples,
                "error": raw.error,
                "fill": self._fill_raw,
            },
        }

    # --- 開く・選ぶ ---

    def _state(self) -> dict[str, Any]:
        with self._lock:
            self._refresh()
            self._refresh_prompt()
            self._refresh_raw()
            self._refresh_premise()
            return self._ok()

    def _select_profile(self, form: Any, profile_id: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(profile_id, str):
                raise _BadRequest
            view = self._current()
            if profile_id == self._selected or profile_id not in {row.id for row in view.profiles}:
                return self._ok()
            if editor_changed(view.editor, *self._typed):
                return self._confirm("select", profile_id, PROFILE_DISCARD_CHANGES, PROFILES_TITLE)
            self._refresh(profile_id)
            return self._ok()

    # --- プロファイル(REQ-43 §2) ---

    def _unreadable(self) -> dict[str, Any] | None:
        """設定を読めないときは理由の知らせ(プロファイルの操作はしない)。"""
        view = self._current()
        if view.editor is not None:
            return None
        return self._ok(notice=self._notice(profile_unreadable(view.error or ""), PROFILES_TITLE))

    def _save_first(self) -> dict[str, Any] | None:
        """保存していない変更があれば、先に「保存」を押すよう言う知らせ。"""
        if not editor_changed(self._current().editor, *self._typed):
            return None
        return self._ok(notice=self._notice(PROFILE_SAVE_FIRST, PROFILES_TITLE))

    def _show_action(self, action: ProfileAction, *, refresh: bool = True, **extra: Any) -> dict[str, Any]:
        self._mark_saved(action.ok)  # 保存・新規・複製・削除・使用中の変更(ADR-0176 決定4)
        notice = self._notice(action.message, PROFILES_TITLE)
        if refresh:
            self._refresh(action.select_id)
        return self._ok(notice=notice, **extra)

    def _show_added(self, action: ProfileAction) -> dict[str, Any]:
        """「新規作成」の結果。作れたら、画面は「名前」の欄にフォーカスを移して全部を選ぶ(REQ-112)。

        作れたときの知らせは窓を出さず、窓の下の文(status_text)に出す(REQ-199 §5)。作れなかったときは窓の知らせ。
        """
        if not action.ok:
            return self._show_action(action)
        self._mark_saved()
        self._refresh(action.select_id)
        return self._ok(status_text=action.message, focus=FOCUS_NAME)

    def _input_problem(self, editor: ProfileEditor) -> dict[str, Any] | None:
        """上書きの確かめを出す前に見る。保存できない入力なら、今と同じ理由の知らせ(入れた値は残す)。"""
        name, api_format, _, base_url, wire = self._typed
        problem = profile_input_problem(
            editor.id, name, api_format, base_url, wire=wire, **self._kw("env", "platform", "home")
        )
        return None if problem is None else self._ok(notice=self._notice(problem, PROFILES_TITLE))

    def _save_typed(self, editor: ProfileEditor) -> ProfileAction:
        """入れた値で選んでいるプロファイルを書き換える(今の「保存」)。"""
        name, api_format, model, base_url, wire = self._typed
        action = save_selected(
            editor.id, name, api_format, model, base_url, wire=wire, **self._kw("cwd", "env", "platform", "home")
        )
        if action.ok and api_format != editor.api_format:
            self._shape = None  # 形式を変えて保存したら、応答の形の木は消す
        return action

    def _save_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._unreadable()) is not None:
                return stop
            editor = self._current().editor
            assert editor is not None
            name, api_format, _, base_url, wire = self._typed
            if overwrite_needs_confirm(editor, name, api_format, base_url, wire):
                return self._input_problem(editor) or self._confirm_choices("save_profile", editor.id, editor.name)
            action = self._save_typed(editor)
            # 保存できなかったときは、入れた値を残す(直して押し直せる)
            return self._show_action(action, refresh=action.ok)

    def _unsaved_choices(self, action: str, *, value: Any = None) -> dict[str, Any] | None:
        """保存していない変更があれば、「新規作成」「複製」の前に3択の確かめ(入力の誤りは先に知らせる。REQ-112)。"""
        editor = self._current().editor
        if not editor_changed(editor, *self._typed):
            return None
        assert editor is not None
        return self._input_problem(editor) or self._confirm_choices(action, editor.id, editor.name, value=value)

    def _add_profile(self, form: Any, template: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(template, str) or template not in PROFILE_TEMPLATES:
                raise _BadRequest
            if (stop := self._unreadable() or self._unsaved_choices("add_profile", value=template)) is not None:
                return stop
            return self._show_added(add_new_profile(template=template, **self._kw("cwd", "env", "platform", "home")))

    def _duplicate_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._unreadable() or self._unsaved_choices("duplicate_profile")) is not None:
                return stop
            editor = self._current().editor
            assert editor is not None
            return self._show_action(duplicate_selected(editor.id, **self._kw("cwd", "env", "platform", "home")))

    def _choice_answered(self, action: str, profile_id: str, choice: str, template: Any) -> dict[str, Any]:
        """3択の確かめの答え(REQ-112)。読み直して、選んでいるプロファイルが同じときだけ行う。"""
        if choice == CHOICE_CANCEL:
            return self._ok()  # 何も書かない。入れた値は欄に残る
        self._refresh(keep_edits=True)
        editor = self._current().editor
        if editor is None or editor.id != profile_id:
            return self._ok()
        if choice == CHOICE_NEW:
            # 新しい id で作って選ぶ(元のプロファイルとキーは変えない)。新規作成・複製は重ねない
            name, api_format, model, base_url, wire = self._typed
            action_new = save_as_new(
                profile_id, name, api_format, model, base_url, wire=wire, **self._kw("cwd", "env", "platform", "home")
            )
            return self._show_action(action_new, refresh=action_new.ok)
        saved = self._save_typed(editor)
        if action == "save_profile" or not saved.ok:
            return self._show_action(saved, refresh=saved.ok)
        # 書き換えてから、新規作成・複製をする
        self._refresh(saved.select_id)
        if action == "add_profile":
            return self._show_added(add_new_profile(template=template, **self._kw("cwd", "env", "platform", "home")))
        return self._show_action(duplicate_selected(profile_id, **self._kw("cwd", "env", "platform", "home")))

    def _delete_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._unreadable()) is not None:
                return stop
            view = self._current()
            editor = view.editor
            assert editor is not None
            if (refusal := delete_refusal(view, editor.id)) is not None:
                return self._ok(notice=self._notice(refusal, PROFILES_TITLE))
            body = profile_delete_confirm(editor.name, view.store_name)
            return self._confirm("delete_profile", editor.id, body, PROFILES_TITLE)

    def _use_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._unreadable() or self._save_first()) is not None:
                return stop
            editor = self._current().editor
            assert editor is not None
            return self._show_action(use_selected(editor.id, **self._kw("env", "platform", "home")))

    # --- API キー(選んだプロファイルの保存名で) ---

    def _key_target(self) -> tuple[str, bool] | dict[str, Any]:
        """キーの保存・削除に使う (id, 使用中か)。決められないときは理由の知らせ。"""
        target = key_target(self._view) if self._view is not None else key_target_unknown("")
        if isinstance(target, str):
            return self._ok(notice=self._notice(target, SETTINGS_API_KEY_TITLE))
        return target

    def _save_key(self, form: Any, key: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if key is None:
                return self._key_replace_check()
            if not isinstance(key, str):
                raise _BadRequest
            self._clear_key += 1  # 保存できなかったとき・保存しなかったときも残さない
            target = self._key_target()
            if isinstance(target, dict):
                return target
            profile_id, _ = target
            result = save_api_key(key, profile_id=profile_id, **self._kw("env", "platform", "home"))
            notice = self._notice(result.message, SETTINGS_API_KEY_TITLE)
            self._refresh(keep_edits=True)
            return self._ok(notice=notice)

    def _key_replace_check(self) -> dict[str, Any]:
        """「保管庫に保存」の1段目(REQ-112)。キーは受け取らない。保管庫にキーがあれば確かめ、無ければ proceed。"""
        target = self._key_target()
        if isinstance(target, dict):
            self._clear_key += 1
            return target
        editor = self._current().editor
        body = key_replace_confirm(target[0], editor.name if editor is not None else "")
        if body is None:
            return self._ok(proceed=PROCEED_SAVE_KEY)
        return self._confirm("save_key", target[0], body, SETTINGS_API_KEY_TITLE)

    def _key_replace_answered(self, profile_id: str, yes: bool) -> dict[str, Any]:
        # 選んでいるプロファイルが同じときだけ、画面にキーを送ってもらう。「いいえ」ならキーの入力欄を空にする
        if not yes or self._check_target() != profile_id:
            self._clear_key += 1
            return self._ok()
        return self._ok(proceed=PROCEED_SAVE_KEY)

    def _delete_key(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            self._clear_key += 1
            target = self._key_target()
            if isinstance(target, dict):
                return target
            if (problem := store_problem()) is not None:
                return self._ok(notice=self._notice(problem, SETTINGS_API_KEY_TITLE))
            body = api_key_delete_confirm(self._current().store_name)
            return self._confirm("delete_key", target[0], body, SETTINGS_API_KEY_DELETE_TITLE)

    # --- AI 接続テスト(REQ-35 §3。選んだプロファイルで) ---

    def _check_target(self) -> str | None:
        # どれか決められない(設定を読めない)ときは id を渡さない(読み直して「設定を読めません」で送らない)
        target = key_target(self._view) if self._view is not None else ""
        return target[0] if isinstance(target, tuple) else None

    def _check_connection(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if self._sending:
                return self._ok()
            if (stop := self._save_first()) is not None:
                return stop
            profile_id = self._check_target()
            start = connection_check_start(
                client_factory=self._client_factory, profile_id=profile_id, **self._kw("cwd", "env", "platform", "home")
            )
            if start.plan is None:
                self._check = (start.refused or "", "failed")
                self._shape = None
                return self._ok()
            # client(キーを持つ)は残さない。2回目で準備し直す
            return self._confirm(
                "check", profile_id, start.confirm, LLM_CHECK_TITLE, yes=LLM_CHECK_SEND, no=LLM_CHECK_DONT_SEND
            )

    def _send_check(self, profile_id: str | None, confirmed: str) -> dict[str, Any]:
        """確かめの後に送る。送る前に準備し直し、確かめの文が変わっていれば確かめ直す。送るのは1回だけ。"""
        with self._lock:
            if self._sending:
                return self._ok()
            start = connection_check_start(
                client_factory=self._client_factory, profile_id=profile_id, **self._kw("cwd", "env", "platform", "home")
            )
            if start.plan is None:
                self._check = (start.refused or "", "failed")
                self._shape = None
                return self._ok()
            if start.confirm != confirmed:
                return self._confirm(
                    "check", profile_id, start.confirm, LLM_CHECK_TITLE, yes=LLM_CHECK_SEND, no=LLM_CHECK_DONT_SEND
                )
            self._sending = True
            self._check = (LLM_CHECK_SENDING, "neutral")
            self._shape = None
            sent_for = self._selected
            client = start.plan.client
            cli = start.plan.cli
            config_path = start.config_path
            sending_view = self._render()
        outcome: tuple[str, str] | None = None
        shape: ShapeNode | None = None  # 形だけ(応答の値は send_connection_check の中だけにある)
        try:
            if self._window is not None:
                notify(self._window, {"type": VIEW_EVENT, "view": sending_view})
            try:
                result = send_connection_check(client)
            except Exception as exc:  # noqa: BLE001 - 型名だけを出す(wx の ConnectionCheckWorker と同じ)
                outcome = (llm_check_failed(type(exc).__name__), "failed")
            else:
                text, ok = connection_check_outcome(result, config_path, cli=cli)
                outcome = (text, "ok" if ok else "failed")
                shape = result.response_shape
        finally:
            with self._lock:
                self._sending = False
                # 送っている間に別のプロファイルを選んだら、その結果は出さない
                if sent_for == self._selected:
                    self._check = outcome
                    self._shape = shape
        with self._lock:
            return self._ok()

    # --- 設定ファイル(config.toml)の上限と伏せる語、平文のキーを保管庫へ移す(REQ-93) ---

    def _save_config(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            fields = self._current().config_fields
            if fields is None:
                return self._unreadable() or self._ok()
            if auto_send_turned_on(fields, self._auto_send_typed):
                # オフからオンへは、送り先を見せて確かめる(まだ何も書かない。ADR-0080 決定2)
                start = auto_send_start(client_factory=self._client_factory, **self._where)
                if start.confirm is None:
                    return self._ok(notice=self._notice(start.refused or "", CONFIG_SAVE_TITLE))
                return self._confirm("auto_send", None, start.confirm, AUTO_SEND_CONFIRM_TITLE, value=self._config_typed)
            return self._write_config(fields, self._config_typed, self._auto_send_typed)

    def _save_raw_defaults(self, values: Any) -> dict[str, Any]:
        """「単純比較」のタブの「保存」。形が違えば _BadRequest。誤りがあれば書かずに欄を残す。書けたら読み直す。"""
        with self._lock:
            try:
                saved = save_raw_defaults_form(values, **self._kw("env", "platform", "home"))
            except ValueError:
                raise _BadRequest from None
            if saved.problems:
                # 書かない。打った値は残し(入れ直さない)、誤りの文を欄の下に出す
                self._raw_values = dict(values)
                self._raw_problems = saved.problems
                return self._ok()
            if saved.error is not None:
                self._raw_values = dict(values)
                self._raw_problems = []
                return self._ok(notice=self._notice(saved.error, RAW_DEFAULTS_TITLE))
            self._mark_saved()
            self._refresh_raw()
            return self._ok(notice=self._notice(saved.notice or "", RAW_DEFAULTS_TITLE))

    def _write_config(self, fields: Any, typed: tuple[str, str, str, bool, bool], auto_send_llm: bool) -> dict[str, Any]:
        """ロックの中で呼ぶ。設定ファイルの枠の値を書く(読んだときと違う項目だけ)。"""
        max_diffs, max_total_diffs, words, auto_compare, character = typed
        action = save_config(
            fields,
            max_diffs,
            max_total_diffs,
            words,
            None,
            auto_compare,
            auto_send_llm,
            character=character,
            **self._kw("env", "platform", "home"),
        )
        self._mark_saved(action.ok)
        notice = self._notice(action.message, CONFIG_SAVE_TITLE)
        # 書けたとき・外で変わっていたときは読み直す。入力の誤りのときは入力を残す(直して押し直せる)
        if action.ok or action.reload:
            self._refresh(keep_edits=True)
        return self._ok(notice=notice)

    def _auto_send_confirmed(self, typed: tuple[str, str, str, bool, bool], confirmed: str) -> dict[str, Any]:
        """自動送信の確かめで「はい」。接続をもう一度確かめ、確かめを出したときの値で書く。文が変わっていれば確かめ直す。"""
        fields = self._current().config_fields
        if fields is None:
            return self._unreadable() or self._ok()
        start = auto_send_start(client_factory=self._client_factory, **self._where)
        if start.confirm is None:
            return self._ok(notice=self._notice(start.refused or "", CONFIG_SAVE_TITLE))
        if start.confirm != confirmed:
            return self._confirm("auto_send", None, start.confirm, AUTO_SEND_CONFIRM_TITLE, value=typed)
        return self._write_config(fields, typed, True)

    def _migrate(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            start = migrate_start(self._current(), **self._kw("env", "platform", "home"))
            if start.refused is not None:
                notice = self._notice(start.refused, MIGRATE_TITLE)
                if start.reload:
                    self._refresh(keep_edits=True)
                return self._ok(notice=notice)
            return self._confirm("migrate", None, start.confirm, MIGRATE_TITLE, value=start.digest)

    def _migrate_confirmed(self, digest: str | None) -> dict[str, Any]:
        """「保管庫に移す」の確かめの後。準備し直し、保管庫に別のキーがあれば上書きの確かめを返す。"""
        start = migrate_start(self._current(), **self._kw("env", "platform", "home"))
        if start.refused is not None:
            notice = self._notice(start.refused, MIGRATE_TITLE)
            if start.reload:
                self._refresh(keep_edits=True)
            return self._ok(notice=notice)
        if start.overwrite_confirm is not None:
            return self._confirm("migrate_overwrite", None, start.overwrite_confirm, MIGRATE_TITLE, value=digest)
        return self._migrate_run(digest, overwrite=False)

    def _migrate_run(self, digest: str | None, *, overwrite: bool) -> dict[str, Any]:
        # migrate_plaintext_key が、設定ファイルのハッシュ・キー・保管庫をもう一度確かめてから書く
        action = migrate_plaintext_key(digest, overwrite=overwrite, **self._kw("env", "platform", "home"))
        notice = self._notice(action.message, MIGRATE_TITLE)
        self._refresh(keep_edits=True)
        return self._ok(notice=notice)

    def _create_template(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            try:
                written = create_template(self._current())
            except SettingsError as exc:
                notice = self._notice(str(exc), SETTINGS_TEMPLATE_FAILED_TITLE)
            else:
                notice = self._notice(template_created(written), SETTINGS_TEMPLATE_CREATED_TITLE)
            self._refresh(keep_edits=True)
            return self._ok(notice=notice)

    def _focus(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            view = self._current()
            where = self._kw("env", "platform", "home")
            # 枠に保存していない入力があるときは読み直さない(保存のときにハッシュを比べて止める)
            if not self._config_changed_now(view.config_fields) and config_changed(view.config_digest, **where):
                self._refresh(keep_edits=True)
            # 指示文の枠も、保存していない変更が無く、ファイルが変わっていれば読み直す(REQ-116 §3)
            pane = self._current_prompt()
            if not prompt_changed(pane, *self._prompt_typed) and prompt_profiles_changed(pane.digest, **where):
                self._refresh_prompt()
            # 前提の欄も、保存していない変更が無ければ読み直す(ほかで書き換えた文を出す。入れた文は消さない)
            if not premise_changed(self._current_premise(), self._premise_typed) and (
                premise_values(**where) != self._current_premise()
            ):
                self._refresh_premise()
            return self._ok()

    # --- custom-http-json の定義の書き出し・読み込み(REQ-101 §4.3) ---

    def _choose_file(
        self, kind: int, save_filename: str = "", file_types: tuple[str, ...] = JSON_FILE_TYPES
    ) -> Path | None:
        """窓のファイルのダイアログで選んだ場所。取り消した・窓が無いときは None。ロックの外で呼ぶ。

        選んだときは、Mac の保存のダイアログが文字列を、ほかは文字列の並びを返す(pywebview 6.2.1)。
        file_types が空ならファイルの種類で絞らない。
        """
        window = self._window
        if window is None:
            return None
        chosen = window.create_file_dialog(
            kind, directory="", allow_multiple=False, save_filename=save_filename, file_types=file_types
        )
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return Path(chosen) if isinstance(chosen, str) and chosen else None

    def _custom_selected(self) -> dict[str, Any] | None:
        """書き出し・読み込みの前に見る。設定を読めない・保存していない変更がある・形式が違うときは知らせ。"""
        if (stop := self._unreadable() or self._save_first()) is not None:
            return stop
        editor = self._current().editor
        assert editor is not None
        if editor.api_format != CUSTOM_HTTP_JSON:
            return self._ok(notice=self._notice(CUSTOM_FORMAT_REQUIRED, CUSTOM_FILE_TITLE))
        return None

    def _export_custom(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._custom_selected()) is not None:
                return stop
            profile_id = self._current().editor.id  # type: ignore[union-attr]
        target = self._choose_file(DIALOG_SAVE, CUSTOM_FILE_NAME)  # 開いている間はロックを持たない
        with self._lock:
            if target is None:
                return self._ok()  # 取り消した
            action = export_custom_definition(profile_id, target, **self._kw("env", "platform", "home"))
            return self._ok(notice=self._notice(action.message, CUSTOM_FILE_TITLE))

    def _import_custom(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._custom_selected()) is not None:
                return stop
            profile_id = self._current().editor.id  # type: ignore[union-attr]
        source = self._choose_file(DIALOG_OPEN)  # 開いている間はロックを持たない
        with self._lock:
            if source is None:
                return self._ok()  # 取り消した
            read = read_custom_definition(source)
            if isinstance(read, str):
                return self._ok(notice=self._notice(read, CUSTOM_FILE_TITLE))
            editor = self._current().editor
            if editor is None or editor.id != profile_id:
                return self._ok()  # 選んでいる間に別のプロファイルを選んだ
            # 2回目で、ファイルを読み直して同じ中身(ハッシュ)かを確かめてから書く
            body = custom_import_confirm(editor.name)
            return self._confirm("import_custom", profile_id, body, CUSTOM_FILE_TITLE, value=(str(source), read[1]))

    def _import_custom_confirmed(self, profile_id: str, value: tuple[str, str]) -> dict[str, Any]:
        # 読み直した値で、もう一度読み込んでよいかを見る(1回目の結果を信じない)
        self._refresh(keep_edits=True)
        editor = self._current().editor
        if editor is None or editor.id != profile_id:
            return self._ok()
        if (stop := self._custom_selected()) is not None:
            return stop
        source, digest = value
        action = import_custom_definition(profile_id, Path(source), digest, **self._kw("env", "platform", "home"))
        notice = self._notice(action.message, CUSTOM_FILE_TITLE)
        self._mark_saved(action.ok)
        if action.ok:
            self._refresh(action.select_id)  # 読み込んだ定義を欄に入れ直す
        return self._ok(notice=notice)

    # --- プロファイルをファイルにまとめて書き出す・取り込む(REQ-119 §4。キーは扱わない・保管庫に触らない) ---

    def _export_profiles(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            counts = prepare_bundle_export(**self._kw("env", "platform", "home"))
            if isinstance(counts, str):
                return self._ok(notice=self._notice(counts, PROFILE_BUNDLE_TITLE))
            return self._confirm("export_profiles", None, profile_bundle_export_confirm(*counts), PROFILE_BUNDLE_TITLE)

    def _export_profiles_confirmed(self) -> dict[str, Any]:
        """書き出しの確かめで「はい」。ロックの外で保存のダイアログを開き、選んだ場所に読み直した中身を書く。"""
        target = self._choose_file(DIALOG_SAVE, BUNDLE_FILE_NAME)  # 開いている間はロックを持たない
        with self._lock:
            if target is None:
                return self._ok()  # 取り消した
            action = export_profile_bundle(target, **self._kw("env", "platform", "home"))
            return self._ok(notice=self._notice(action.message, PROFILE_BUNDLE_TITLE))

    def _import_profiles(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
        source = self._choose_file(DIALOG_OPEN)  # 開いている間はロックを持たない
        with self._lock:
            if source is None:
                return self._ok()  # 取り消した
            read = read_profile_bundle(source, **self._kw("env", "platform", "home"))
            if isinstance(read, str):
                return self._ok(notice=self._notice(read, PROFILE_BUNDLE_TITLE))
            # 2回目で、ファイルを読み直して同じ中身(ハッシュ)と同じ名前かを確かめてから足す
            return self._confirm(
                "import_profiles", read.digest, read.confirm, PROFILE_BUNDLE_TITLE, value=(str(source), read.names)
            )

    def _import_profiles_confirmed(self, digest: str, value: tuple[str, Any]) -> dict[str, Any]:
        source, names = value
        action = import_profile_bundle(Path(source), digest, names, **self._kw("cwd", "env", "platform", "home"))
        notice = self._notice(action.message, PROFILE_BUNDLE_TITLE)
        self._mark_saved(action.ok)
        # 一覧を読み直す。選んでいるもの・保存していない編集の値は変えない(使用中は取り込みで変わらない)
        self._refresh(keep_edits=True)
        self._refresh_prompt(keep_edits=True)
        return self._ok(notice=notice)

    # --- CLI の形式の CLI の場所(REQ-102 §4.1) ---

    def _choose_cli_path(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            editor = self._current().editor
            if editor is None:
                return self._ok()
            profile_id = editor.id
        # 開いている間はロックを持たない。CLI のプログラムは拡張子が決まらないので、種類で絞らない
        chosen = self._choose_file(DIALOG_OPEN, file_types=())
        with self._lock:
            editor = self._current().editor
            if chosen is None or editor is None or editor.id != profile_id:
                return self._ok()  # 取り消した・選んでいる間に別のプロファイルを選んだ
            name, api_format, model, base_url, wire = self._typed
            # 欄に入れるだけ(保存は利用者が「保存」を押す)。ほかの欄の入力は残す
            self._typed = (name, api_format, model, base_url, ProfileWireText(**{**wire.values(), "cli_path": str(chosen)}))
            self._fill_editor += 1
            return self._ok()

    # --- AI への指示文のプロファイル(REQ-116 §3。接続のプロファイルと設定ファイルの欄は変えない) ---

    def _prompt_notice(self, body: str) -> dict[str, Any]:
        return self._notice(body, PROMPT_TEMPLATE_TITLE)

    def _prompt_unreadable(self) -> dict[str, Any] | None:
        """指示文のファイルを読めないときは理由の知らせ(指示文の操作はしない)。"""
        pane = self._current_prompt()
        if pane.error is None:
            return None
        return self._ok(notice=self._prompt_notice(pane.error))

    def _show_prompt_action(self, action: ProfileAction, *, refresh: bool = True, **extra: Any) -> dict[str, Any]:
        self._mark_saved(action.ok)  # 指示文の保存・新規・複製・削除・使用中の変更(ADR-0176 決定4)
        notice = self._prompt_notice(action.message)
        if refresh:
            self._refresh_prompt(action.select_id)
        return self._ok(notice=notice, **extra)

    def _show_prompt_added(self, action: ProfileAction) -> dict[str, Any]:
        """指示文の「新規作成」の結果。作れたら、画面は名前の欄にフォーカスを移して全部を選ぶ(REQ-112 と同じ)。"""
        return self._show_prompt_action(action, **({"focus": FOCUS_PROMPT_NAME} if action.ok else {}))

    def _unsaved_prompt_choices(self, action: str) -> dict[str, Any] | None:
        """保存していない変更があれば、「新規作成」「複製」の前に3択の確かめ(入力の誤りは先に知らせる)。"""
        pane = self._current_prompt()
        if not prompt_changed(pane, *self._prompt_typed):
            return None
        assert pane.selected_id is not None
        problem = prompt_input_problem(pane.selected_id, *self._prompt_typed, **self._kw("env", "platform", "home"))
        if problem is not None:
            return self._ok(notice=self._prompt_notice(problem))
        return self._confirm_choices(action, pane.selected_id, pane.name, title=PROMPT_TEMPLATE_TITLE)

    def _select_prompt_profile(self, form: Any, profile_id: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(profile_id, str):
                raise _BadRequest
            pane = self._current_prompt()
            if profile_id == self._prompt_selected or profile_id not in {row.id for row in pane.rows}:
                return self._ok()
            if prompt_changed(pane, *self._prompt_typed):
                return self._confirm("select_prompt", profile_id, PROMPT_DISCARD_CHANGES, PROMPT_TEMPLATE_TITLE)
            self._refresh_prompt(profile_id)
            return self._ok()

    def _add_prompt_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._prompt_unreadable() or self._unsaved_prompt_choices("add_prompt_profile")) is not None:
                return stop
            return self._show_prompt_added(add_prompt(**self._kw("env", "platform", "home")))

    def _duplicate_prompt_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._prompt_unreadable() or self._unsaved_prompt_choices("duplicate_prompt_profile")) is not None:
                return stop
            selected = self._current_prompt().selected_id
            assert selected is not None
            return self._show_prompt_action(duplicate_prompt(selected, **self._kw("env", "platform", "home")))

    def _save_prompt_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._prompt_unreadable()) is not None:
                return stop
            pane = self._current_prompt()
            if not pane.can_edit:
                return self._ok(notice=self._prompt_notice(PROMPT_BUILTIN_READ_ONLY))
            assert pane.selected_id is not None
            action = save_prompt(pane.selected_id, *self._prompt_typed, **self._kw("env", "platform", "home"))
            # 保存できなかったときは、入れた値を残す(直して押し直せる)
            return self._show_prompt_action(action, refresh=action.ok)

    def _delete_prompt_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._prompt_unreadable()) is not None:
                return stop
            pane = self._current_prompt()
            assert pane.selected_id is not None
            if (refusal := prompt_delete_refusal(pane, pane.selected_id)) is not None:
                return self._ok(notice=self._prompt_notice(refusal))
            body = prompt_profile_delete_confirm(pane.name)
            return self._confirm("delete_prompt_profile", pane.selected_id, body, PROMPT_TEMPLATE_TITLE)

    def _use_prompt_profile(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if (stop := self._prompt_unreadable()) is not None:
                return stop
            pane = self._current_prompt()
            if prompt_changed(pane, *self._prompt_typed):
                return self._ok(notice=self._prompt_notice(PROFILE_SAVE_FIRST))
            assert pane.selected_id is not None
            return self._show_prompt_action(use_prompt(pane.selected_id, **self._kw("env", "platform", "home")))

    def _prompt_choice_answered(self, action: str, profile_id: str, choice: str) -> dict[str, Any]:
        """指示文の3択の確かめの答え。読み直して、選んでいる指示文が同じときだけ行う。"""
        if choice == CHOICE_CANCEL:
            return self._ok()  # 何も書かない。入れた値は欄に残る
        self._refresh_prompt(keep_edits=True)
        pane = self._current_prompt()
        if pane.selected_id != profile_id or not pane.can_edit:
            return self._ok()
        where = self._kw("env", "platform", "home")
        if choice == CHOICE_NEW:
            # 入れた値で新しい指示文を作って選ぶ(元の指示文は変えない)。新規作成・複製は重ねない
            added = save_prompt_as_new(profile_id, *self._prompt_typed, **where)
            return self._show_prompt_action(added, refresh=added.ok)
        saved = save_prompt(profile_id, *self._prompt_typed, **where)
        if not saved.ok:
            return self._show_prompt_action(saved, refresh=False)
        # 書き換えてから、新規作成・複製をする
        self._refresh_prompt(saved.select_id)
        if action == "add_prompt_profile":
            return self._show_prompt_added(add_prompt(**where))
        return self._show_prompt_action(duplicate_prompt(profile_id, **where))

    def _delete_prompt_confirmed(self, profile_id: str) -> dict[str, Any]:
        # 読み直した値で、もう一度消してよいかを見る(1回目の結果を信じない)
        self._refresh_prompt(keep_edits=True)
        pane = self._current_prompt()
        if pane.selected_id != profile_id:
            return self._ok()
        if (refusal := prompt_delete_refusal(pane, profile_id)) is not None:
            return self._ok(notice=self._prompt_notice(refusal))
        return self._show_prompt_action(delete_prompt(profile_id, **self._kw("env", "platform", "home")))

    # --- 毎回付ける前提(ADR-0172 決定6 / REQ-234 §5。接続のプロファイル・設定ファイル・指示文の欄は変えない) ---

    def _save_premise(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(form.get(PREMISE_FIELD), str):
                raise _BadRequest
            refused = save_premise(self._premise_typed, **self._where)
            if refused is not None:
                # 書かない。入れた文は欄に残す(入れ直さない。直して押し直せる)
                return self._ok(notice=self._notice(refused, SETTINGS_PREMISE_TITLE))
            self._mark_saved()
            self._refresh_premise()
            return self._ok(notice=self._notice(premise_saved_notice(), SETTINGS_PREMISE_TITLE))

    # --- 確かめの2段目 ---

    def _answer(self, form: Any, action: Any, target: Any, yes: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(action, str) or not (isinstance(yes, bool) or yes in CHOICE_ANSWERS):
                raise _BadRequest
            pending, self._pending = self._pending, None
            # 3択の確かめには3択の答え、ほかの確かめには真偽値だけを受け付ける
            if (
                pending is None
                or (pending[0], pending[1]) != (action, target)
                or isinstance(yes, bool) == (action in CHOICE_ACTIONS)
            ):
                self._pending = pending  # 別の確かめの答えでは消さない
                return self._ok()
            _, _, body, value = pending
            if action in PROMPT_CHOICE_ACTIONS:
                return self._prompt_choice_answered(action, target, yes)
            if action in CHOICE_ACTIONS:
                return self._choice_answered(action, target, yes, value)
            if action == "save_key":
                return self._key_replace_answered(target, yes)
            if action == "select":
                if yes:
                    self._refresh(target)
                return self._ok()  # 「いいえ」なら選択を戻す(view の selected は元のまま)
            if action == "select_prompt":
                if yes:
                    self._refresh_prompt(target)
                return self._ok()  # 「いいえ」なら選択を戻す(prompt の selected は元のまま)
            if action == "close" and not yes:
                return self._ok()  # 「キャンセル」なら閉じない。入れた文は欄に残る
            if action == "check" and not yes:
                self._check = (LLM_CHECK_DECLINED, "failed")
                self._shape = None
                return self._ok()
            if action == "migrate_overwrite" and not yes:
                return self._ok(notice=self._notice(migrate_overwrite_declined(), MIGRATE_TITLE))
            if not yes:
                return self._ok()
            if action == "delete_profile":
                return self._delete_profile_confirmed(target)
            if action == "delete_prompt_profile":
                return self._delete_prompt_confirmed(target)
            if action == "delete_key":
                return self._delete_key_confirmed(target)
            if action == "migrate":
                return self._migrate_confirmed(value)
            if action == "migrate_overwrite":
                return self._migrate_run(value, overwrite=True)
            if action == "import_custom":
                return self._import_custom_confirmed(target, value)
            if action == "auto_send":
                return self._auto_send_confirmed(value, body)
            if action == "import_profiles":
                return self._import_profiles_confirmed(target, value)
        if action == "check":
            return self._send_check(target, body)  # ロックの外で送る
        if action == "export_profiles":
            return self._export_profiles_confirmed()  # ロックの外でダイアログを開く
        if action == "close":
            return self._close()  # 前提の確かめで「閉じる」。窓を壊すのはロックの外
        return self._ok()

    def _delete_profile_confirmed(self, profile_id: str) -> dict[str, Any]:
        # 読み直した値で、もう一度消してよいかを見る(1回目の結果を信じない)
        self._refresh(keep_edits=True)
        view = self._current()
        if view.editor is None or view.editor.id != profile_id:
            return self._ok()
        if (refusal := delete_refusal(view, profile_id)) is not None:
            return self._ok(notice=self._notice(refusal, PROFILES_TITLE))
        return self._show_action(delete_selected(profile_id, **self._kw("env", "platform", "home")))

    def _delete_key_confirmed(self, profile_id: str) -> dict[str, Any]:
        self._refresh(keep_edits=True)
        target = key_target(self._current())
        if isinstance(target, str):
            return self._ok(notice=self._notice(target, SETTINGS_API_KEY_TITLE))
        if target[0] != profile_id:
            return self._ok()
        if (problem := store_problem()) is not None:
            return self._ok(notice=self._notice(problem, SETTINGS_API_KEY_TITLE))
        result = delete_api_key(profile_id=profile_id, **self._kw("platform"))
        notice = self._notice(result.message, SETTINGS_API_KEY_TITLE)
        self._refresh(keep_edits=True)
        return self._ok(notice=notice)

    # --- 最下段 ---

    def _open_folder(self) -> dict[str, Any]:
        with self._lock:
            folder = self._current().folder_to_open
        # ファイルではなくフォルダを開く(ファイルが無いときでも押せる)
        self._opener(folder)
        return self._ok()

    def _close_asked(self, form: Any) -> dict[str, Any]:
        """「閉じる」。form があり、前提の欄の文が保存してある文と違うときだけ確かめを返す(既定のボタンはキャンセル)。

        ほかの欄(プロファイル・設定ファイル・指示文)の保存していない編集は見ない(今までどおり確かめずに捨てる)。
        """
        if form is not None:
            with self._lock:
                self._take_premise(form)
                if premise_changed(self._current_premise(), self._premise_typed):
                    return self._confirm(
                        "close",
                        "premise",
                        PREMISE_CLOSE_CONFIRM,
                        SETTINGS_PREMISE_TITLE,
                        yes=SETTINGS_BUTTON_CLOSE,
                        no=DIALOG_CANCEL,
                    )
        return self._close()

    def _close(self) -> dict[str, Any]:
        with self._lock:
            self._clear_key += 1
            self._pending = None
            self._shape = None
            result = self._ok()
        if self._window is not None:
            self._window.destroy()
        return result
