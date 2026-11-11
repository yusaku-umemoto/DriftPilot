"""設定画面の論理(REQ-27 パート B / ADR-0029 決定2・3、REQ-34 / ADR-0033 決定3・4、REQ-93 / ADR-0071)。wx を読まない。

load_settings() の結果を、そのまま表示用のデータ(SettingsView)に写す。
利用者の設定ファイル(config.toml)に書くのは、ボタンを押したときだけで、書くのは user_config_writer の関数だけ(ADR-0071):
「保存」(save_config。上限2つと伏せる語の行、pywebview の画面では [gui] の auto_compare・auto_send_llm も)、「保管庫に移す」(migrate_plaintext_key。api_key の行を消す)、
ファイルが無いときの雛形(llm_settings.write_template。上書きしない)。api_key・model・base_url は書かない。
API キーは OS の保管庫へ保存・削除できる(save_api_key / delete_api_key。llm_settings を通して key_store を呼ぶだけ)。
「AI 接続テスト」(REQ-35)は、送る準備と文面だけをここで作る(送るのは screen_model/worker.py の ConnectionCheckWorker)。
キーは display_key() の文字列だけを持つ。キーの全体はこのモジュールのどの値にも入れない(ADR-0017・ADR-0019)。

AI 接続のプロファイル(REQ-43 / ADR-0035 決定5)の一覧と編集もここで作る。llm-profiles.json の読み書きは
llm_profiles の関数だけを通す(このモジュールは JSON を読み書きしない)。書くのは「保存」「追加」「複製」「削除」
「このプロファイルを使う」を押したときだけ。ファイルが無いときは、今の設定を「既定（まだ保存していません）」の1件で見せ、
書く操作のときに REQ-42 §3 の移行で id="default" の1件目を作る(開いただけではファイルを作らない)。
環境変数のキーは、プロファイルごとに決めた名前 DRIFTPILOT_LLM_API_KEY_<env_suffix> の変数だけが効く(使用中かどうかでは
変わらない)。この決まりは読み込みの関数(load_settings。ADR-0077)に任せ、画面はコマンドの --llm-profile と同じ読み方をする。
名前の続きの欄(env_suffix)は「API キー」の枠の中に置くが、保存はプロファイルの「保存」で、送り方の欄と同じく文字で受け取る
(pywebview の画面だけ。wx の画面は渡さないので、wx から保存してもファイルの値が残る)。

送り方の細かい設定(REQ-100 §5・§6。認証の見出し・送る上限・上限の送信名・thinking・問い合わせの値)とプロファイルのひな形も
ここに置く。画面は文字を渡すだけで、文字からプロファイルの値への変換と決まりの確かめはこのモジュールが行う(parse_wire_text)。
wx の画面は今の引数(名前・形式・モデル・接続先)だけで呼び、そのときは送り方の値を変えない・比べない。

custom-http-json の定義の欄(REQ-101 §4)も同じ。画面は文字(custom_url・custom_auth・custom_headers・custom_body・
answer_path)を渡すだけで、文字から定義への変換と決まりの確かめは parse_custom_text が行う。定義の書き出し・読み込みは
llm_profiles の関数を通す。接続テストの応答の形の木は、画面に渡す JSON にするだけ(response_shape_view。値は持たない)。

手元の CLI を呼ぶ形式(codex-cli・claude-code-cli。REQ-102 §4)の欄(cli_path・timeout_seconds)も同じ。画面は文字を渡すだけで、
変換と確かめは parse_cli_text が行う。CLI の形式はキーを求めない(状態の印・一覧の印に「キーがありません」を出さない)。

AI への指示文のプロファイル(REQ-116 / ADR-0079)の枠の状態(prompt_pane)と操作もここに置く(pywebview の画面だけ)。
prompt-profiles.json の読み書きは prompt_profiles の関数だけを通す(prompt_profiles を読むのはこのモジュールだけ)。
最初から入っている3つは読むだけで、保存・削除を断る(複製はできる)。使用中の利用者の指示文は消せない(判断の記録 3)。
AI の接続のプロファイルの読み込み(settings_view)は指示文のファイルを読まない(read_prompt=False)。指示文のファイルが
壊れていても、接続のプロファイルを編集できるようにするため。

プロファイルをファイルにまとめて書き出す・取り込む操作(ADR-0082・REQ-119)もここに置く。ファイルの形・キーの確かめ・
読み書きは profile_bundle の関数だけを通す(profile_bundle を読むのはこのモジュールだけ)。キーは扱わず、保管庫に触らない。

AI に毎回付ける前提の文(ADR-0172 決定6 / REQ-234 §5)の読み(premise_values)と保存(save_premise)もここに置く。
llm-premise.txt の読み書きは premise の関数だけを通す(画面の側で premise を読むのはこのモジュールだけ)。保存の前に上限と、
追加の質問と同じ check_question(語は設定の窓が今読んでいる伏せる語)を通し、当たれば書かない。前提の文はログ・例外の文に入れない。
"""

import importlib.util
import itertools
import os
import platform as _platform
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

import driftpilot
from driftpilot.config_parser.platforms import patterns_fingerprint
from driftpilot.llm_analyzer import (
    AUTH_API_KEY,
    AUTH_BEARER,
    AUTH_HEADERS,
    CLI_EFFORTS,
    CUSTOM_AUTHS,
    DEFAULT_PROMPT_TEMPLATE,
    DEFAULT_WIRE,
    THINKING_OFF,
    THINKING_VALUES,
    ConnectionCheckResult,
    CustomHttpJson,
    LLMClient,
    ShapeNode,
    ThinkingParam,
    WireOptions,
    check_prompt_template,
    custom_problem,
    json_text,
    masking_fingerprint,
    normalize_newlines,
    parse_json_text,
    wire_problem,
)
from driftpilot.llm_profiles import (
    API_FORMATS,
    DEFAULT_PROFILE_NAME,
    LlmProfile,
    LlmProfiles,
    add_profile,
    create_first,
    delete_profile,
    duplicate_profile,
    URL_CREDENTIALS_RULE,
    has_url_credentials,
    has_url_extras,
    load_profiles,
    profiles_path,
    read_custom_file,
    set_active,
    update_profile,
    write_custom_file,
)
from driftpilot.llm_settings import (
    ANTHROPIC_MESSAGES,
    AUTO_UPDATE_CHECK_KEY,
    CHAT_COMPLETIONS,
    CLAUDE_CODE_CLI,
    CODEX_CLI,
    CUSTOM_HTTP_JSON,
    PROMPT_TEMPLATE_KEY,
    RESPONSES,
    DEFAULT_PROFILE_ID,
    ENV_API_KEY,
    ConnectionCheckPlan,
    env_key_name,
    env_suffix_of,
    LlmSettings,
    SettingsError,
    StoreResult,
    default_base_url,
    delete_key,
    display_key,
    display_model,
    has_placeholder,
    is_cli,
    is_secret_word,
    key_required,
    load_settings,
    needs_openai,
    parse_section,
    prepare_connection_check,
    read_bytes,
    read_key,
    save_key,
    service_name,
    store_name,
    user_config_path,
    write_template,
)
from driftpilot.llm_settings import store_problem as _store_problem
from driftpilot.messages import raw_defaults_saved
from driftpilot.messages import (
    API_FORMAT_CHOICE,
    API_FORMAT_SHORT,
    AUTH_HEADER_CHOICE,
    CUSTOM_AUTH_CHOICE,
    CUSTOM_FORMAT_REQUIRED,
    CUSTOM_IMPORT_CHANGED,
    SETTINGS_CLI_EFFORT_DEFAULT,
    SHAPE_TYPE_LABEL,
    custom_export_failed,
    custom_exported,
    custom_field_invalid,
    custom_import_unreadable,
    custom_imported,
    PROFILE_BUNDLE_CHANGED,
    PROFILE_BUNDLE_DEFAULT_URL,
    PROFILE_BUNDLE_LOCAL_CLI,
    profile_bundle_connection_line,
    profile_bundle_export_failed,
    profile_bundle_exported,
    profile_bundle_import_confirm,
    profile_bundle_imported,
    profile_bundle_partly_imported,
    profile_bundle_unreadable,
    PROFILE_TEMPLATE_LABEL,
    SETTINGS_FIELD_THINKING_OFF,
    SETTINGS_FIELD_THINKING_ON,
    THINKING_CHOICE,
    base_url_has_extras,
    cli_effort_invalid,
    cli_timeout_invalid,
    query_key_empty,
    query_key_repeated,
    query_line_invalid,
    template_profile_name,
    wire_json_invalid,
    wire_keylike,
    wire_max_tokens_invalid,
    wire_reserved,
    CONFIG_CHANGED_ELSEWHERE,
    ENV_SUFFIX_NOT_COPIED,
    CONFIG_NOT_CHANGED,
    LIMIT_PER_JOB,
    LIMIT_TOTAL,
    KEY_NOT_SET,
    KEY_STATE_ENV_NOT_USED,
    KEY_STATE_IN_USE,
    KEY_STATE_NONE,
    KEY_STATE_NOT_FOR_PROFILE,
    KEY_STATE_UNUSED,
    PROFILE_ALREADY_IN_USE,
    PROFILE_UNSAVED_DEFAULT,
    READ_ONLY_NOTE,
    SELFCHECK_GUI_WEB,
    STATUS_NO_KEY,
    STATUS_NO_LLM_EXTRA,
    STATUS_NO_MODEL,
    STATUS_PLACEHOLDER,
    STATUS_READY,
    STATUS_UNREADABLE,
    api_key_replace_confirm,
    api_key_saved_env_wins,
    auto_send_confirm,
    auto_send_unusable,
    base_url_default_hint,
    common_env_ignored_note,
    config_not_saved,
    config_saved,
    copy_profile_name,
    env_suffix_invalid,
    env_suffix_taken,
    key_help_command_lines,
    key_help_lines,
    key_profile_note,
    key_source_line,
    key_state_unusable,
    key_store_note,
    key_summary,
    key_target_unknown,
    limit_invalid,
    limit_now_line,
    llm_check_confirm_lines,
    llm_check_key_source,
    llm_check_no_model_profile,
    llm_check_outcome_line,
    llm_check_result,
    llm_check_unreadable,
    migrate_confirm,
    migrate_done,
    migrate_file_changed,
    migrate_mismatch,
    migrate_no_key,
    migrate_overwrite_confirm,
    migrate_remove_failed,
    migrate_save_failed,
    migrate_store_unusable,
    new_profile_name,
    plaintext_api_key_band,
    profile_added,
    profile_delete_in_use,
    profile_delete_key_failed,
    profile_delete_last,
    profile_deleted,
    profile_duplicated,
    profile_name_empty,
    profile_name_taken,
    profile_now_in_use,
    profile_row_detail,
    profile_row_line,
    profile_row_title,
    profile_saved,
    profile_unreadable,
    PROMPT_BUILTIN_READ_ONLY,
    PROMPT_ROW_DETAIL,
    PROMPT_ROW_USER,
    new_prompt_name,
    prompt_name_taken,
    prompt_profile_added,
    prompt_profile_delete_in_use,
    prompt_profile_deleted,
    prompt_profile_duplicated,
    prompt_profile_now_in_use,
    prompt_profile_saved,
    prompt_profiles_unreadable,
    prompt_template_invalid,
    user_prompt_template_ignored,
    secret_word_has_space,
    status_ready,
    workdir_api_key_ignored,
    workdir_api_key_reason,
    workdir_limits_ignored,
    workdir_model_ignored,
    workdir_secret_words_line,
)
from driftpilot.messages import (
    PREMISE_SAVED,
    PREMISE_SECRET_REFUSED,
    PREMISE_TOO_LONG,
    premise_settings_unreadable,
    premise_unreadable,
)
from driftpilot.llm_settings import load_model_and_base_url, premise_text_problem
from driftpilot.premise import PREMISE_MAX_CHARS, load_premise, premise_path, write_premise
from driftpilot.profile_bundle import BundleChanged, plan_names, read_bundle, write_bundle
from driftpilot.raw_settings import raw_settings_path
from driftpilot.raw_settings import save_raw_defaults as _save_raw_defaults
from driftpilot.profile_bundle import bundle_data as _bundle_data
from driftpilot.profile_bundle import import_bundle as _import_bundle
from driftpilot.prompt_profiles import (
    PromptProfiles,
    add_prompt_profile,
    delete_prompt_profile,
    duplicate_prompt_profile,
    load_prompt_profiles,
    prompt_file_digest,
    prompt_profiles_path,
    set_active_prompt,
    update_prompt_profile,
)
from driftpilot.user_config_writer import (
    ConfigChanged,
    file_digest,
    read_api_key,
    read_user_config,
    remove_api_key,
    update_gui_auto_send,
    update_gui_items,
    update_llm_items,
)

# キーの読み元の名前。保管庫は OS で名前を変える(画面では store_name を使う。ここは OS を問わない名前)。
# "env" はプロファイルのファイルが無いとき(共通の変数を読む)の名前。プロファイルがあるときは、そのプロファイルの変数の名前を出す
ENV_SOURCE_LABEL = "環境変数"
KEY_SOURCE_LABEL = {
    "env": f"{ENV_SOURCE_LABEL} {ENV_API_KEY}",
    "keyring": "OS の保管庫",
    "user_config": "設定ファイル",
    None: "未設定",
}
FILE_SOURCE_LABEL = {"workdir": "作業フォルダの設定ファイル", "user_config": "利用者ごとの設定ファイル"}
NOT_SET = "(未設定)"
DEFAULT_MARK = "(既定)"
UNREADABLE = "(設定を読めません)"
SECRET_WORDS_NOTE = "この語を含む行は AI に送る前に伏せます"
# 指紋の表。機種ごとに別々に出す(まとめて1つにしない。ADR-0029 Action Item 5)。(表示名, 機種。None は A型)
PATTERN_TABLES: tuple[tuple[str, str | None], ...] = (
    ("読む表(Cisco 系・Arista = A型)", None),
    ("読む表(富士通 Si-R)", "fujitsu_sir"),
    ("読む表(FortiGate)", "fortinet"),
)
MASKING_TABLE = "伏せる表"
# API の形式の選択肢(編集の欄。値, 表示名)
FORMAT_CHOICES: list[tuple[str, str]] = [(f, API_FORMAT_CHOICE[f]) for f in API_FORMATS]
# 認証の見出しと thinking の選択肢(値, 表示名。REQ-100 §6.1)
AUTH_CHOICES: list[tuple[str, str]] = [(v, AUTH_HEADER_CHOICE[v]) for v in AUTH_HEADERS]
THINKING_CHOICES: list[tuple[str, str]] = [(v, THINKING_CHOICE[v]) for v in THINKING_VALUES]
# custom-http-json の定義の認証の選択肢(値, 表示名。REQ-101 §4.1)
CUSTOM_AUTH_CHOICES: list[tuple[str, str]] = [(v, CUSTOM_AUTH_CHOICE[v]) for v in CUSTOM_AUTHS]
# 形式ごとに出す欄(画面はこの表どおりに出し入れするだけ。ADR-0074 決定3)。custom-http-json は接続先を出さず、
# 定義の欄と書き出し・読み込みのボタン(custom_file)を出す(REQ-101 §4.1・§4.3)。
# 「API キー」の枠の全体も表の名前の1つ(api_key)。CLI の2形式は接続先・送り方の欄・「API キー」の枠を出さず、
# CLI の場所・時間の上限と、枠のあった場所に CLI のログインの注記(cli_login_note)を出す(REQ-102 §4.1)
_OPENAI_FIELDS = ("base_url", "auth_header", "max_output_tokens", "max_tokens_name", "thinking", "query", "api_key")
CUSTOM_TEXT_FIELDS = ("custom_url", "custom_auth", "custom_headers", "custom_body", "answer_path")
CLI_TEXT_FIELDS = ("cli_path", "timeout_seconds", "cli_effort")  # cli_effort は Thinking の深さ(ADR-0157 決定12)
FORMAT_FIELDS: dict[str, tuple[str, ...]] = {
    CHAT_COMPLETIONS: _OPENAI_FIELDS,
    RESPONSES: _OPENAI_FIELDS,
    ANTHROPIC_MESSAGES: ("base_url", "max_output_tokens", "thinking", "api_key"),
    CUSTOM_HTTP_JSON: (*CUSTOM_TEXT_FIELDS, "custom_file", "max_output_tokens", "api_key"),
    CODEX_CLI: (*CLI_TEXT_FIELDS, "codex_note", "cli_login_note"),
    CLAUDE_CODE_CLI: (*CLI_TEXT_FIELDS, "cli_login_note"),
}


@dataclass(frozen=True)
class ProfileTemplate:
    """プロファイルのひな形(REQ-100 §5)。足すときに展開した値を保存する(後から個々の値を変えられる)。"""

    api_format: str
    base_url: str | None
    auth_header: str = AUTH_BEARER
    wire: WireOptions = DEFAULT_WIRE


# ひな形の表(値と並び)。表示名は messages.PROFILE_TEMPLATE_LABEL。Azure の <リソース名> は利用者が書き換える目印
PROFILE_TEMPLATES: dict[str, ProfileTemplate] = {
    "openai": ProfileTemplate(CHAT_COMPLETIONS, "https://api.openai.com/v1"),
    "openai-responses": ProfileTemplate(RESPONSES, "https://api.openai.com/v1"),
    "azure-openai": ProfileTemplate(CHAT_COMPLETIONS, "https://<リソース名>.openai.azure.com/openai/v1", AUTH_API_KEY),
    "anthropic": ProfileTemplate(ANTHROPIC_MESSAGES, "https://api.anthropic.com"),
    "qwen": ProfileTemplate(
        CHAT_COMPLETIONS,
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        wire=WireOptions(max_tokens_name="max_tokens", thinking_param=ThinkingParam("enable_thinking", True, False)),
    ),
    "deepseek": ProfileTemplate(
        CHAT_COMPLETIONS,
        "https://api.deepseek.com",
        wire=WireOptions(
            max_tokens_name="max_tokens",
            thinking_param=ThinkingParam("thinking", {"type": "enabled"}, {"type": "disabled"}),
        ),
    ),
    "local": ProfileTemplate(CHAT_COMPLETIONS, "http://localhost:11434/v1"),
    # 手元の CLI(REQ-102 §4.1。形式だけが入り、ほかは既定)
    "codex-cli": ProfileTemplate(CODEX_CLI, None),
    "claude-code-cli": ProfileTemplate(CLAUDE_CODE_CLI, None),
    "custom": ProfileTemplate(CHAT_COMPLETIONS, None),  # 今の「追加」と同じ(名前も「新しいプロファイル」のまま)
}
DEFAULT_TEMPLATE = "custom"
TEMPLATE_CHOICES: list[tuple[str, str]] = [(key, PROFILE_TEMPLATE_LABEL[key]) for key in PROFILE_TEMPLATES]


def _lines(text: str) -> str:
    """比べるための複数行の欄(前後の空白と空の行は比べない)。"""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


@dataclass(frozen=True)
class ProfileWireText:
    """送り方の欄と custom-http-json の定義の欄と CLI の欄と環境変数の名前の欄の文字(画面の入力欄のまま)。

    プロファイルの値への変換は parse_wire_text(送り方)と parse_custom_text(定義)と parse_cli_text(CLI の欄)と
    parse_env_suffix_text(環境変数の名前の続き。ADR-0077)。
    """

    auth_header: str = AUTH_BEARER
    max_output_tokens: str = ""
    max_tokens_name: str = ""
    thinking: str = THINKING_OFF
    thinking_name: str = ""
    thinking_on: str = ""
    thinking_off: str = ""
    query: str = ""
    custom_url: str = ""
    custom_auth: str = AUTH_BEARER
    custom_headers: str = ""
    custom_body: str = ""
    answer_path: str = ""
    cli_path: str = ""
    timeout_seconds: str = ""
    env_suffix: str = ""
    cli_effort: str = ""  # Thinking の深さ(CLI の2形式。空は指定を付けない。ADR-0157 決定12)

    def normalized(self) -> tuple[str, ...]:
        """比べるための形(前後の空白と、問い合わせの値・固定の見出しの空の行は比べない。環境変数の名前は大文字にして比べる)。"""
        return (
            self.auth_header,
            self.max_output_tokens.strip(),
            self.max_tokens_name.strip(),
            self.thinking,
            self.thinking_name.strip(),
            self.thinking_on.strip(),
            self.thinking_off.strip(),
            _lines(self.query),
            self.custom_url.strip(),
            self.custom_auth,
            _lines(self.custom_headers),
            self.custom_body.strip(),
            self.answer_path.strip(),
            self.cli_path.strip(),
            self.timeout_seconds.strip(),
            self.env_suffix.strip().upper(),
            self.cli_effort.strip(),
        )

    def values(self) -> dict[str, str]:
        return {name: getattr(self, name) for name in WIRE_TEXT_FIELDS}


WIRE_TEXT_FIELDS: tuple[str, ...] = tuple(f.name for f in fields(ProfileWireText))


def _custom_texts(custom: CustomHttpJson | None) -> dict[str, str]:
    """定義を欄の文字にする。固定の見出しは1行に1つの 名前: 値、本文は字下げした JSON。無ければ既定の文字。"""
    if custom is None:
        return {}
    return {
        "custom_url": custom.url,
        "custom_auth": custom.auth,
        "custom_headers": "\n".join(f"{name}: {value}" for name, value in custom.headers),
        "custom_body": json_text(custom.body, indent=2),
        "answer_path": custom.answer_path,
    }


def wire_text(profile: LlmProfile) -> ProfileWireText:
    """プロファイルの値を欄の文字にする。JSON の値は JSON の文字、問い合わせの値は1行に1つの キー=値。"""
    wire = profile.wire
    param = wire.thinking_param
    return ProfileWireText(
        **_custom_texts(profile.custom),
        auth_header=profile.auth_header,
        max_output_tokens="" if wire.max_output_tokens is None else str(wire.max_output_tokens),
        max_tokens_name=wire.max_tokens_name or "",
        thinking=wire.thinking,
        thinking_name="" if param is None else param.name,
        thinking_on="" if param is None else json_text(param.on),
        thinking_off="" if param is None else json_text(param.off),
        query="\n".join(f"{key}={value}" for key, value in wire.query),
        cli_path=profile.cli_path or "",
        timeout_seconds="" if profile.timeout_seconds is None else str(profile.timeout_seconds),
        env_suffix=profile.env_suffix or "",
        cli_effort=profile.cli_effort or "",
    )


def _parse_query(text: str) -> tuple[tuple[str, str], ...] | str:
    """問い合わせの値の欄(1行に キー=値。空の行は無視)。形が違えば理由の文(値は入れない)。"""
    pairs: list[tuple[str, str]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        item = line.strip()
        if not item:
            continue
        if "=" not in item:
            return query_line_invalid(number)
        key, value = (part.strip() for part in item.split("=", 1))
        if not key:
            return query_key_empty(number)
        if any(key == seen for seen, _ in pairs):
            return query_key_repeated(number)
        pairs.append((key, value))
    return tuple(pairs)


def parse_wire_text(text: ProfileWireText) -> tuple[str, WireOptions] | str:
    """欄の文字を (認証の見出し, 送り方の値) にする。保存できないときは理由の文(どの欄かだけ。値は入れない)。

    送る上限は空なら送らない。送る名前が空なら thinking の送り方は無し(ON の値・OFF の値は読まない)。
    最後に、予約された名前・キーらしい名前の決まり(wire_problem)を確かめる。
    """
    tokens_text = text.max_output_tokens.strip()
    tokens = _parse_limit(tokens_text) if tokens_text else None
    if tokens_text and tokens is None:
        return wire_max_tokens_invalid()
    param = None
    if name := text.thinking_name.strip():
        values = []
        for label, value_text in ((SETTINGS_FIELD_THINKING_ON, text.thinking_on), (SETTINGS_FIELD_THINKING_OFF, text.thinking_off)):
            ok, value = parse_json_text(value_text.strip())
            if not ok:
                return wire_json_invalid(label)
            values.append(value)
        param = ThinkingParam(name, values[0], values[1])
    query = _parse_query(text.query)
    if isinstance(query, str):
        return query
    wire = WireOptions(
        max_output_tokens=tokens,
        max_tokens_name=text.max_tokens_name.strip() or None,
        thinking=text.thinking,
        thinking_param=param,
        query=query,
    )
    problem = wire_problem(wire)
    if problem is not None:
        field, kind = problem
        return wire_reserved(field) if kind == "reserved" else wire_keylike(field)
    return text.auth_header, wire


def parse_custom_text(text: ProfileWireText) -> CustomHttpJson | str:
    """定義の欄の文字を定義にする。保存できないときは理由の文(どの欄か・何行目かだけ。値は入れない)。

    固定の見出しは1行に 名前: 値(最初の : で分ける。前後の空白と空の行は無視)。本文は JSON(NaN などは断る)。
    最後に決まり(custom_problem)を確かめる。
    """
    headers: list[tuple[str, str]] = []
    numbers: list[int] = []  # headers の各項目が書いてあった行の番号
    for number, line in enumerate(text.custom_headers.splitlines(), start=1):
        item = line.strip()
        if not item:
            continue
        if ":" not in item:
            return custom_field_invalid("headers", "line", number)
        name, value = (part.strip() for part in item.split(":", 1))
        headers.append((name, value))
        numbers.append(number)
    try:
        ok, body = parse_json_text(text.custom_body.strip())
    except RecursionError:  # 入れ子が深すぎる
        ok, body = False, None
    if not ok:
        return custom_field_invalid("body", "json")
    custom = CustomHttpJson(
        url=text.custom_url.strip(),
        auth=text.custom_auth,
        headers=tuple(headers),
        body=body,
        answer_path=text.answer_path.strip(),
    )
    problem = custom_problem(custom)
    if problem is not None:
        line = numbers[problem.index] if problem.index is not None else None
        return custom_field_invalid(problem.field, problem.kind, line)
    return custom


def parse_cli_text(text: ProfileWireText) -> tuple[str | None, int | None] | str:
    """CLI の欄の文字を (CLI の場所, 時間の上限の秒) にする。空はどちらも None(既定: PATH から探す・60 秒)。

    時間の上限が正の整数でなければ理由の文(値は入れない)。
    """
    timeout_text = text.timeout_seconds.strip()
    timeout = _parse_limit(timeout_text) if timeout_text else None
    if timeout_text and timeout is None:
        return cli_timeout_invalid()
    return text.cli_path.strip() or None, timeout


def parse_cli_effort_text(text: ProfileWireText, api_format: str) -> tuple[str | None] | str:
    """Thinking の深さの欄の文字を (値,) にする。空は (None,)(指定を付けない)。

    その形式の CLI_EFFORTS に無い値(CLI でない形式に値があるときも)は理由の文(値は入れない。ADR-0157 決定10)。
    """
    value = text.cli_effort.strip()
    if not value:
        return (None,)
    if value not in CLI_EFFORTS.get(api_format, ()):
        return cli_effort_invalid()
    return (value,)


def cli_effort_choices() -> dict[str, list[dict[str, str]]]:
    """形式ごとの Thinking の深さの選択肢({value, label})。先頭は空の値の「既定（指定を付けない）」。"""
    return {
        api_format: [{"value": "", "label": SETTINGS_CLI_EFFORT_DEFAULT}, *({"value": v, "label": v} for v in values)]
        for api_format, values in CLI_EFFORTS.items()
    }


def parse_env_suffix_text(text: ProfileWireText) -> tuple[str | None] | str:
    """環境変数の名前の欄の文字を (続き,) にする。空は (None,)(環境変数を使わない)。小文字は大文字にする。

    英数字と _ の 1〜40 文字でなければ理由の文(値は入れない)。決まりは llm_profiles が書くときと同じ(env_suffix_of)。
    """
    try:
        return (env_suffix_of(text.env_suffix),)
    except ValueError:
        return env_suffix_invalid()


def _env_suffix_owner(profiles: LlmProfiles | None, env_suffix: str | None, *, except_id: str | None) -> LlmProfile | None:
    """同じ環境変数の名前の続きを持つ、ほかのプロファイル(except_id を除く)。無ければ None。"""
    if profiles is None or env_suffix is None:
        return None
    return next((p for p in profiles.profiles if p.env_suffix == env_suffix and p.id != except_id), None)


@dataclass(frozen=True)
class FingerprintRow:
    name: str
    count: int
    digest: str


@dataclass(frozen=True)
class KeySourceRow:
    """キーの読み元の1行(優先の順)。line は画面に出す文(「1 環境変数 … — 使用中」)。"""

    number: int
    label: str
    state: str
    in_use: bool
    line: str


@dataclass(frozen=True)
class ProfileRow:
    """プロファイルの一覧の1行。in_use は使用中(強調する)、selected は選んでいるもの(別々に見分ける)。キーは有無だけ。"""

    id: str
    name: str
    format_label: str
    in_use: bool
    has_key: bool
    selected: bool
    line: str
    title: str  # 1件を2行で出すときの1行目(名前)と2行目(形式と印)。REQ-53 §3
    detail: str


@dataclass(frozen=True)
class ProfileEditor:
    """右の編集の欄の初期値。model・base_url は入力欄の文字(未設定・既定は空)。saved はファイルにあるか。"""

    id: str
    name: str
    api_format: str
    model: str
    base_url: str
    base_url_hint: str
    saved: bool
    in_use: bool
    wire: ProfileWireText = ProfileWireText()  # 送り方の欄の文字(REQ-100 §6)


@dataclass(frozen=True)
class ProfileAction:
    """プロファイルの操作の結果。message は利用者に見せる文(値は入れない)。select_id は操作の後に選ぶもの。"""

    ok: bool
    message: str
    select_id: str | None = None


@dataclass(frozen=True)
class ConfigFields:
    """「設定ファイル（config.toml）」の枠の初期値(REQ-93 §3)。digest は読んだときの設定ファイルのハッシュ。

    max_diffs・max_total_diffs は今の値(設定ファイルに無ければ既定)。入力欄にはこの数を入れ、変えた項目だけを書く。
    words は設定ファイルの伏せる語だけ(作業フォルダの語は workdir_words に読むだけで出す)。
    auto_compare は [gui] の自動の比較(無ければ真、形が違えば偽。pywebview の画面だけが使う。ADR-0076)。
    auto_send_llm は [gui] の自動送信(true のときだけ真。pywebview の画面だけが使う。ADR-0080 決定2)。
    character は [gui] のキャラクターの絵(true のときだけ真。pywebview の画面だけが使う。ADR-0174 決定1)。
    auto_update_check は [gui] の自動アップデートチェック(true のときだけ真。「更新」のタブの専用の「保存」だけが書く。ADR-0188 決定2)。
    AI への指示文はこの枠から出した(指示文のプロファイルの枠。PromptPane。REQ-116 §3)。
    """

    digest: str | None
    max_diffs: int
    max_total_diffs: int
    words: tuple[str, ...]
    # 上限の欄の下に薄く出す、今の値と出どころ(1行に1つ)
    limits_note: str
    workdir_words: str
    auto_compare: bool = True
    auto_send_llm: bool = False
    character: bool = False
    auto_update_check: bool = False

    @property
    def words_text(self) -> str:
        """伏せる語の欄の文字(1行1語)。"""
        return "\n".join(self.words)


@dataclass(frozen=True)
class ConfigAction:
    """「保存」「保管庫に移す」の結果。message は利用者に見せる文(キーの値は入れない)。

    reload は画面の値を読み直すか(外で変わっていた)。needs_overwrite は保管庫に別のキーがあり、上書きの承認が要る。
    """

    ok: bool
    message: str
    reload: bool = False
    needs_overwrite: bool = False


@dataclass(frozen=True)
class MigrateState:
    """設定ファイルに平文のキーがあるときの「保管庫に移す」。保管庫を使えないときは reason(ボタンの代わりに出す)。"""

    reason: str | None = None

    @property
    def available(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class SettingsView:
    """設定画面に出すもの。error は設定が読めなかったときの文(そのときキー・モデルなどは UNREADABLE)。

    キーの欄(key*・store_note)と model・base_url は、一覧で選んだプロファイルの値。状態の印は使用中のプロファイルの値。
    """

    error: str | None
    key: str
    key_source: str
    model: str
    base_url: str
    secret_words: str
    secret_words_note: str
    config_path: Path
    config_state: str
    can_write_template: bool
    folder_to_open: Path
    workdir_warning: list[str] | None
    versions: list[tuple[str, str]]
    fingerprints: list[FingerprintRow]
    # 見出しの右端の状態の印。status_ok は「使える」(色を変えるかどうか)
    status: str
    status_ok: bool
    # 「API キー」の枠の右上(「保存済み sk-...a3f9」か「未設定」)
    key_summary: str
    key_sources: list[KeySourceRow]
    # 設定ファイルに平文のキーがあるときの帯の文(無ければ None)
    plaintext_warning: str | None
    store_name: str
    store_note: str
    # キーの設定のしかた(保管庫への保存が先、環境変数は補足。環境変数の手順はその OS の分だけ)
    key_help: list[str]
    # key_help のうち、コマンドだけの行(画面で途中を折り返さない。REQ-37 §3)
    key_help_commands: list[str]
    read_only_note: str
    # AI 接続のプロファイル(REQ-43)。profiles_saved は llm-profiles.json があるか。読めないときは一覧が空で editor は None
    profiles: list[ProfileRow]
    profiles_saved: bool
    active_id: str | None
    selected_id: str | None
    editor: ProfileEditor | None
    format_choices: list[tuple[str, str]]
    key_profile_note: str
    # プロファイルを使っていて、作業フォルダの driftpilot.toml の model・base_url を使っていないときの注意
    workdir_model_warning: str | None
    # キーの保存・削除・接続テストに使うプロファイルの id。llm-profiles.json を読めず、どれか決められないときは None
    # (ファイルが無いと分かっていれば、設定ファイルが読めなくても今までどおり "default")
    key_target_id: str | None
    # 読んだときの設定ファイル(config.toml)のハッシュ(前面に来たときに変わったかを見る。無ければ None)
    config_digest: str | None
    # 「設定ファイル（config.toml）」の枠(上限・伏せる語)。設定を読めないときは None
    config_fields: ConfigFields | None
    # 作業フォルダの driftpilot.toml の上限を読んでいないときの注意(帯に出す)
    limits_warning: str | None
    # 設定ファイルに平文のキーがあるときの「保管庫に移す」(無ければ None)
    migrate: MigrateState | None
    # プロファイルがあり、共通の DRIFTPILOT_LLM_API_KEY が設定されているときの注意(帯に出す。ADR-0077 決定3)
    common_env_warning: str | None = None


def _with_source(value: str, source: str | None, workdir: Path | None) -> str:
    if source == "workdir" and workdir is not None:
        return f"{value}({FILE_SOURCE_LABEL['workdir']} {workdir})"
    return f"{value}({FILE_SOURCE_LABEL[source]})" if source in FILE_SOURCE_LABEL else value


def config_state(path: Path) -> str:
    """設定ファイルがあるか。あれば更新日時も出す。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return "なし"
    return f"あり(更新 {datetime.fromtimestamp(mtime).strftime('%Y-%m-%d %H:%M')})"


def folder_to_open(path: Path) -> Path:
    """「フォルダを開く」の開き先。設定ファイルのあるフォルダ。まだ無ければ、いちばん近い既存の祖先。"""
    folder = path.parent
    while not folder.is_dir() and folder.parent != folder:
        folder = folder.parent
    return folder


def fingerprint_rows() -> list[FingerprintRow]:
    masking = masking_fingerprint()
    rows = [FingerprintRow(MASKING_TABLE, masking.count, masking.digest)]
    for name, platform in PATTERN_TABLES:
        fp = patterns_fingerprint(platform)
        rows.append(FingerprintRow(name, fp.count, fp.digest))
    return rows


def version_rows(wx_version: str | None, *, web_version: str | None = None) -> list[tuple[str, str]]:
    """「診断」の版の行。web_version(pywebview の版)を渡すと、wxPython の行の代わりに selfcheck と同じ行を出す(REQ-104 §4)。"""
    gui_row = ("wxPython", wx_version or "(入っていません)")
    if web_version is not None:
        gui_row = (SELFCHECK_GUI_WEB, f"pywebview {web_version}")
    return [
        ("DriftPilot", driftpilot.__version__),
        ("Python", _platform.python_version()),
        gui_row,
        ("OS", _platform.platform()),
    ]


def llm_extra_installed() -> bool:
    """llm extra が入っているか(AI に接続する openai を読めるか。読み込みはしない)。"""
    try:
        return importlib.util.find_spec("openai") is not None
    except (ImportError, ValueError):  # sys.modules で消してあるとき
        return False


def _env_label(settings: LlmSettings | None) -> str:
    """読み元の1行目の名前。読む環境変数の名前を出す(ADR-0077)。読まないときは「環境変数」だけ。設定を読めなければ今の名前。"""
    if settings is None:
        return KEY_SOURCE_LABEL["env"]
    return ENV_SOURCE_LABEL if settings.env_key_name is None else f"{ENV_SOURCE_LABEL} {settings.env_key_name}"


def _source_label(source: str | None, store: str, settings: LlmSettings | None = None) -> str:
    if source == "env" and settings is not None:
        return _env_label(settings)
    return store if source == "keyring" else KEY_SOURCE_LABEL.get(source, KEY_SOURCE_LABEL[None])


def _key_source_rows(settings: LlmSettings | None, store: str, *, file_other: bool = False) -> list[KeySourceRow]:
    """読み元を優先の順に3つ。settings が None(設定を読めない)なら状態は UNREADABLE。

    1行目はそのプロファイルの環境変数の名前。名前を決めていないとき(CLI の形式も)は「環境変数 — 使いません」(ADR-0077)。
    file_other は「default 以外のプロファイルで、設定ファイルにキーがある」(このプロファイルでは読まない)。
    """
    labels = [("env", _env_label(settings)), ("keyring", store), ("user_config", KEY_SOURCE_LABEL["user_config"])]
    rows = []
    for number, (source, label) in enumerate(labels, start=1):
        in_use = settings is not None and settings.api_key_source == source
        if settings is None:
            state = UNREADABLE
        elif in_use:
            state = KEY_STATE_IN_USE
        elif source == "env" and settings.env_key_name is None:
            state = KEY_STATE_ENV_NOT_USED
        elif source == "user_config" and file_other:
            state = KEY_STATE_NOT_FOR_PROFILE
        elif source == "keyring" and settings.keyring_problem is not None:
            state = key_state_unusable(settings.keyring_problem)
        else:
            present = {
                "env": settings.env_key_present,
                "keyring": settings.keyring_key_present,
                "user_config": settings.user_config_key_present,
            }[source]
            state = KEY_STATE_UNUSED if present else KEY_STATE_NONE
        rows.append(KeySourceRow(number, label, state, in_use, key_source_line(number, label, state)))
    return rows


def _status(settings: LlmSettings, llm_extra: bool) -> tuple[str, bool]:
    """右上の印。モデル・キー・接続先・部品がそろったときだけ「使えます」。

    見る順は接続テストの準備(build_request)と同じ(モデル → キー → 接続先の <リソース名> → 部品。REQ-108 §2)。
    CLI の形式はモデルが無くても送り(CLI の既定のモデル)、キーを求めず(key_required)、接続先を見ない。
    部品(openai)は、その接続が openai を使うときだけ見る(needs_openai。REQ-108 §3)。
    """
    cli = is_cli(settings.api_format)
    if settings.model is None and not cli:
        return STATUS_NO_MODEL, False
    if settings.api_key is None and key_required(settings):
        return STATUS_NO_KEY, False
    if not cli and has_placeholder(settings.base_url):
        return STATUS_PLACEHOLDER, False
    if not llm_extra and needs_openai(settings.api_format, settings.auth_header):
        return STATUS_NO_LLM_EXTRA, False
    return STATUS_READY, True


# --- プロファイルの読み方(環境変数のキーは、そのプロファイルの名前の変数だけが効く。load_settings の決まり) ---


def _env_key_present(
    profile_id: str, *, env: Mapping[str, str] | None, platform: str | None, home: Path | None
) -> bool:
    """そのプロファイルのキーを読む環境変数に値があるか(保管庫より優先される)。ファイルが無ければ共通の変数。

    名前を決めていない・CLI の形式・設定を読めないときは偽。
    """
    env = os.environ if env is None else env
    try:
        profiles = load_profiles(profiles_path(platform=platform, env=env, home=home))
    except SettingsError:
        return False
    if profiles is None:
        name = ENV_API_KEY
    else:
        profile = profiles.get(profile_id)
        if profile is None or profile.env_suffix is None or is_cli(profile.api_format):
            return False
        name = env_key_name(profile.env_suffix)
    return bool(env.get(name, "").strip())


def _load_profile(
    profile: LlmProfile,
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    platform: str | None,
    home: Path | None,
) -> LlmSettings:
    return load_settings(cwd=cwd, env=env, platform=platform, home=home, profile=profile.name, read_prompt=False)


def base_url_hint(api_format: str) -> str:
    """接続先の空欄に薄く出す「既定（<形式ごとの既定の URL>）」。"""
    return base_url_default_hint(default_base_url(api_format))


def settings_view(
    *,
    wx_version: str | None = None,
    web_version: str | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
    llm_extra: bool | None = None,
    selected: str | None = None,
) -> SettingsView:
    """設定を読み、画面に出す値を作る。読むだけで、ファイルを作らない・書かない。

    selected は一覧で選んでいるプロファイルの id。省いたとき・一覧に無いときは使用中のものを選ぶ。
    """
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    os_name = sys.platform if platform is None else platform
    store = store_name(os_name)
    try:
        digest = file_digest(path)
    except SettingsError:
        digest = None  # 読めないことは下の読み込みで知らせる
    common = {
        "config_path": path,
        "config_state": config_state(path),
        "can_write_template": not path.exists(),
        "folder_to_open": folder_to_open(path),
        "versions": version_rows(wx_version, web_version=web_version),
        "fingerprints": fingerprint_rows(),
        "secret_words_note": SECRET_WORDS_NOTE,
        "store_name": store,
        "read_only_note": READ_ONLY_NOTE,
        "format_choices": list(FORMAT_CHOICES),
        "key_profile_note": key_profile_note(),
    }
    profiles_read = False
    try:
        profiles = load_profiles(profiles_path(platform=platform, env=env, home=home))
        profiles_read = True
        if profiles is None:
            # ファイルが無い: 今の設定を1件(id="default")として見せる。ファイルは作らない
            active = load_settings(cwd=cwd, env=env, platform=platform, home=home, read_prompt=False)
            items = [
                LlmProfile(DEFAULT_PROFILE_ID, DEFAULT_PROFILE_NAME, CHAT_COMPLETIONS, active.model, active.base_url)
            ]
            active_id = DEFAULT_PROFILE_ID
            loaded = {DEFAULT_PROFILE_ID: active}
        else:
            items = list(profiles.profiles)
            active_id = profiles.active
            loaded = {
                p.id: _load_profile(p, cwd=cwd, env=env, platform=platform, home=home) for p in items
            }
            active = loaded[active_id]
        snapshot = read_user_config(path)
    except SettingsError as exc:
        return SettingsView(
            error=str(exc),
            key=UNREADABLE,
            key_source=UNREADABLE,
            model=UNREADABLE,
            base_url=UNREADABLE,
            secret_words=UNREADABLE,
            workdir_warning=None,
            status=STATUS_UNREADABLE,
            status_ok=False,
            key_summary=UNREADABLE,
            key_sources=_key_source_rows(None, store),
            plaintext_warning=None,
            store_note=key_store_note(store, service_name(DEFAULT_PROFILE_ID)),
            # 設定を読めないときは、どのプロファイルの変数か決められないので今の文(共通の変数の名前)のまま
            key_help=key_help_lines(ENV_API_KEY, os_name),
            key_help_commands=key_help_command_lines(ENV_API_KEY, os_name),
            profiles=[],
            profiles_saved=False,
            active_id=None,
            selected_id=None,
            editor=None,
            workdir_model_warning=None,
            key_target_id=DEFAULT_PROFILE_ID if profiles_read and profiles is None else None,
            config_digest=digest,
            config_fields=None,
            limits_warning=None,
            migrate=None,
            **common,
        )
    saved = profiles is not None
    selected_id = selected if selected in loaded else active_id
    chosen = loaded[selected_id]
    profile = next(p for p in items if p.id == selected_id)
    in_use = selected_id == active_id
    # 設定のしかたの文の変数の名前は、選んだプロファイルの変数(ファイルが無ければ共通の変数。無ければ手順を出さない)
    env_name = chosen.env_key_name
    # 一覧の「キー未設定」の印。CLI の形式はキーを使わないので出さない(キーがあるものとして扱う)
    has_key = {p.id: loaded[p.id].api_key is not None or is_cli(p.api_format) for p in items}

    rows = [
        ProfileRow(
            id=p.id,
            name=p.name if saved else PROFILE_UNSAVED_DEFAULT,
            format_label=API_FORMAT_SHORT[p.api_format],
            in_use=p.id == active_id,
            has_key=has_key[p.id],
            selected=p.id == selected_id,
            line=profile_row_line(
                p.name if saved else PROFILE_UNSAVED_DEFAULT,
                API_FORMAT_SHORT[p.api_format],
                in_use=p.id == active_id,
                has_key=has_key[p.id],
            ),
            title=profile_row_title(p.name if saved else PROFILE_UNSAVED_DEFAULT, in_use=p.id == active_id),
            detail=profile_row_detail(API_FORMAT_SHORT[p.api_format], in_use=p.id == active_id, has_key=has_key[p.id]),
        )
        for p in items
    ]
    editor = ProfileEditor(
        id=profile.id,
        name=profile.name,
        api_format=profile.api_format,
        model=profile.model or "",
        base_url=profile.base_url or "",
        base_url_hint=base_url_hint(profile.api_format),
        saved=saved,
        in_use=in_use,
        wire=wire_text(profile),
    )

    workdir = chosen.workdir_config
    if chosen.model is None:
        model = display_model(chosen) or NOT_SET  # CLI の形式なら「CLI の既定のモデル」
    else:
        model = _with_source(chosen.model, chosen.model_source, workdir)
    if chosen.base_url is None:
        base_url = f"{default_base_url(chosen.api_format)}{DEFAULT_MARK}"
    else:
        base_url = _with_source(chosen.base_url, chosen.base_url_source, workdir)
    words = active.extra_secret_words
    warning = None
    if active.ignored_workdir_api_key:
        # 文面は CLI と同じ関数(言い換えない)。理由を添える
        warning = [workdir_api_key_ignored(workdir or Path("driftpilot.toml").absolute()), workdir_api_key_reason()]
    status, status_ok = _status(active, llm_extra_installed() if llm_extra is None else llm_extra)
    if status_ok and saved:
        status = status_ready(active.profile_name or "")
    return SettingsView(
        error=None,
        key=display_key(chosen.api_key),
        key_source=_source_label(chosen.api_key_source, store, chosen),
        model=model,
        base_url=base_url,
        secret_words=f"{len(words)} 語" + (f": {', '.join(words)}" if words else ""),
        workdir_warning=warning,
        status=status,
        status_ok=status_ok,
        key_summary=key_summary(display_key(chosen.api_key)) if chosen.api_key is not None else KEY_NOT_SET,
        key_sources=_key_source_rows(
            chosen,
            store,
            file_other=selected_id != DEFAULT_PROFILE_ID and chosen.plaintext_key_in_user_config,
        ),
        plaintext_warning=plaintext_api_key_band() if active.plaintext_key_in_user_config else None,
        store_note=key_store_note(store, service_name(selected_id)),
        key_help=key_help_lines(env_name, os_name),
        key_help_commands=key_help_command_lines(env_name, os_name),
        profiles=rows,
        profiles_saved=saved,
        active_id=active_id,
        selected_id=selected_id,
        editor=editor,
        workdir_model_warning=workdir_model_ignored(active.profile_name) if active.ignored_workdir_model else None,
        key_target_id=selected_id,
        config_digest=snapshot.digest,
        config_fields=_config_fields(
            active,
            snapshot.digest,
            snapshot.extra_secret_words,
            snapshot.auto_compare,
            snapshot.auto_send_llm,
            snapshot.character,
            snapshot.auto_update_check,
        ),
        limits_warning=(
            workdir_limits_ignored(workdir or Path("driftpilot.toml").absolute(), active.ignored_workdir_limits)
            if active.ignored_workdir_limits
            else None
        ),
        migrate=MigrateState(_migrate_problem()) if active.plaintext_key_in_user_config else None,
        common_env_warning=common_env_ignored_note() if active.common_env_ignored else None,
        **common,
    )


def _config_fields(
    settings: LlmSettings,
    digest: str | None,
    words: tuple[str, ...],
    auto_compare: bool = True,
    auto_send_llm: bool = False,
    character: bool = False,
    auto_update_check: bool = False,
) -> ConfigFields:
    note = "\n".join(
        [
            limit_now_line(LIMIT_PER_JOB, settings.max_diffs, settings.max_diffs_source),
            limit_now_line(LIMIT_TOTAL, settings.max_total_diffs, settings.max_total_diffs_source),
        ]
    )
    return ConfigFields(
        digest=digest,
        max_diffs=settings.max_diffs,
        max_total_diffs=settings.max_total_diffs,
        words=words,
        limits_note=note,
        workdir_words=workdir_secret_words_line(settings.workdir_secret_words),
        auto_compare=auto_compare,
        auto_send_llm=auto_send_llm,
        character=character,
        auto_update_check=auto_update_check,
    )


def _migrate_problem() -> str | None:
    problem = _store_problem()
    return None if problem is None else migrate_store_unusable(problem)


def create_template(view: SettingsView) -> Path:
    """「ひな形を作る」。設定ファイルが無いときだけ作る(あれば SettingsError。上書きしない)。"""
    if not view.can_write_template:
        raise SettingsError(f"{view.config_path}: 既にあります。上書きしません")
    return write_template(view.config_path)


# --- プロファイルの操作(REQ-43 §2・§3。書くのは llm_profiles の関数だけ。押したときだけ書く) ---


def _no_profile(path: Path) -> SettingsError:
    return SettingsError(f"{path}: 指定した id のプロファイルがありません")


def _ensure_saved(
    path: Path, *, cwd: Path | None, env: Mapping[str, str] | None, platform: str | None, home: Path | None
) -> LlmProfiles:
    """ファイルが無ければ、今の設定のモデル・接続先で id="default" の1件目を作る(REQ-42 §3 の移行)。"""
    profiles = load_profiles(path)
    if profiles is not None:
        return profiles
    current = load_settings(cwd=cwd, env=env, platform=platform, home=home, read_prompt=False)
    return create_first(path, model=current.model, base_url=current.base_url)


def _free_name(profiles: LlmProfiles, make: Callable[[int], str]) -> str:
    """重ならない名前(大文字・小文字を区別しない)。"""
    return next(make(n) for n in itertools.count(1) if profiles.find(make(n)) is None)


def _parse_input(name: str, api_format: str, base_url: str, wire: ProfileWireText | None) -> dict[str, Any] | str:
    """「保存」の欄の確かめ(ファイルを読まずにできる分)。書く値(送り方・定義・CLI の欄)か、保存できない理由の文。

    name は前後の空白を除いたもの。wire を省けば(wx の画面)送り方の値と定義と CLI の欄と環境変数の名前は書かない(空の dict)。
    環境変数の名前の続きは、形式に関係なく書く(空なら消す。ADR-0077)。
    """
    if not name:
        return profile_name_empty()
    if has_url_extras(base_url.strip()):
        return base_url_has_extras()
    if has_url_credentials(base_url.strip()):
        return URL_CREDENTIALS_RULE
    send: dict[str, Any] = {}
    if wire is not None:
        parsed = parse_wire_text(wire)
        if isinstance(parsed, str):
            return parsed
        suffix = parse_env_suffix_text(wire)
        if isinstance(suffix, str):
            return suffix
        effort = parse_cli_effort_text(wire, api_format)
        if isinstance(effort, str):
            return effort
        send = {"auth_header": parsed[0], "wire": parsed[1], "env_suffix": suffix[0], "cli_effort": effort[0]}
        if api_format == CUSTOM_HTTP_JSON:
            custom = parse_custom_text(wire)
            if isinstance(custom, str):
                return custom
            send["custom"] = custom
        if is_cli(api_format):
            cli = parse_cli_text(wire)
            if isinstance(cli, str):
                return cli
            send["cli_path"], send["timeout_seconds"] = cli
    return send


def profile_input_problem(
    profile_id: str,
    name: str,
    api_format: str,
    base_url: str,
    *,
    wire: ProfileWireText | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> str | None:
    """「保存」で断る理由(save_selected と同じ確かめ。空の名前・ほかと重なる名前・接続先の ? #・送り方・定義・CLI の欄・
    ほかと重なる環境変数の名前)。

    ファイルには書かない。保存できる形なら None(REQ-112: 上書きの確かめを出す前に、入力の誤りを先に知らせる)。
    """
    name = name.strip()
    send = _parse_input(name, api_format, base_url, wire)
    if isinstance(send, str):
        return send
    try:
        current = load_profiles(profiles_path(platform=platform, env=env, home=home))
    except SettingsError as exc:
        return str(exc)
    other = current.find(name) if current is not None else None
    if other is not None and other.id != profile_id:
        return profile_name_taken(name)
    owner = _env_suffix_owner(current, send.get("env_suffix"), except_id=profile_id)
    if owner is not None:
        return env_suffix_taken(owner.name)
    return None


def overwrite_needs_confirm(
    editor: ProfileEditor | None, name: str, api_format: str, base_url: str, wire: ProfileWireText
) -> bool:
    """「保存」の前に上書きの確かめが要るか(REQ-112)。保存済みのプロファイルで、名前・形式・つなぐ先が変わったとき。

    つなぐ先は形式ごとに決める欄: custom-http-json は送り先の URL、CLI の2形式は CLI の場所、ほかは接続先。
    前後の空白は比べない。名前は大文字・小文字の違いだけでも変更とみなす。ファイルがまだ無い(なくなる値が無い)ときは要らない。
    """
    if editor is None or not editor.saved:
        return False
    if name.strip() != editor.name or api_format != editor.api_format:
        return True
    if api_format == CUSTOM_HTTP_JSON:
        return wire.custom_url.strip() != editor.wire.custom_url.strip()
    if is_cli(api_format):
        return wire.cli_path.strip() != editor.wire.cli_path.strip()
    return base_url.strip() != editor.base_url


def save_as_new(
    source_id: str,
    name: str,
    api_format: str,
    model: str,
    base_url: str,
    *,
    wire: ProfileWireText,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """上書きの確かめの「新しいプロファイルとして保存」(REQ-112)。入れた値だけで、新しい id のプロファイルを足す。

    元のプロファイル(source_id)とそのキーは変えない。キーは写さない(保管庫に触らない)。
    名前が元のプロファイルと重なるときは「<名前> のコピー」(重なれば番号を付ける)。ほかのプロファイルと重なるときは断る。
    環境変数の名前が元のプロファイルと同じなら写さずに作り、知らせに添える(REQ-199 §4)。ほかのプロファイルと重なれば断る
    (同じキーを2つの接続先へ送らない。ADR-0077 決定1)。
    """
    name = name.strip()
    send = _parse_input(name, api_format, base_url, wire)
    if isinstance(send, str):
        return ProfileAction(False, send, source_id)
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        if load_profiles(path) is None and source_id != DEFAULT_PROFILE_ID:
            raise _no_profile(path)
        profiles = _ensure_saved(path, cwd=cwd, env=env, platform=platform, home=home)
        source = profiles.get(source_id)
        suffix_dropped = (
            source is not None and send.get("env_suffix") is not None and send["env_suffix"] == source.env_suffix
        )
        if suffix_dropped:
            send["env_suffix"] = None
        other = profiles.find(name)
        if other is not None and other.id != source_id:
            return ProfileAction(False, profile_name_taken(name), source_id)
        owner = _env_suffix_owner(profiles, send.get("env_suffix"), except_id=None)
        if owner is not None:
            return ProfileAction(False, env_suffix_taken(owner.name), source_id)
        if other is not None:
            typed = name
            name = _free_name(profiles, lambda n: copy_profile_name(typed, n))
        profile = add_profile(
            path,
            name=name,
            api_format=api_format,
            model=model.strip() or None,
            base_url=base_url.strip() or None,
            **send,
        )
    except SettingsError as exc:
        return ProfileAction(False, str(exc), source_id)
    message = profile_saved(profile.name)
    if suffix_dropped:
        message = f"{message}。{ENV_SUFFIX_NOT_COPIED}"
    return ProfileAction(True, message, profile.id)


def save_selected(
    profile_id: str,
    name: str,
    api_format: str,
    model: str,
    base_url: str,
    *,
    wire: ProfileWireText | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「保存」。空の名前と重複した名前は、何も書かずに断る。空のモデルは未設定、空の接続先は既定。

    wire は送り方の欄の文字(pywebview の画面)。省くと(wx の画面)送り方の値と定義はファイルのまま変えない。
    custom-http-json の定義は、形式が custom-http-json で wire を渡したときだけ欄の文字から書く(ほかの形式では残す)。
    CLI の欄(場所・時間の上限)も、形式が CLI で wire を渡したときだけ書く(ほかの形式と wx の保存ではファイルのまま)。
    環境変数の名前は wire を渡したときだけ書く(wx の保存ではファイルのまま。ADR-0077)。
    接続先の ? #・送り方の欄・定義の欄・時間の上限・環境変数の名前の決まりに反するときも、何も書かずに理由を返す。
    """
    name = name.strip()
    send = _parse_input(name, api_format, base_url, wire)
    if isinstance(send, str):
        return ProfileAction(False, send, profile_id)
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        current = load_profiles(path)
        other = current.find(name) if current is not None else None
        if other is not None and other.id != profile_id:
            return ProfileAction(False, profile_name_taken(name), profile_id)
        owner = _env_suffix_owner(current, send.get("env_suffix"), except_id=profile_id)
        if owner is not None:
            return ProfileAction(False, env_suffix_taken(owner.name), profile_id)
        if current is None and profile_id != DEFAULT_PROFILE_ID:
            raise _no_profile(path)
        _ensure_saved(path, cwd=cwd, env=env, platform=platform, home=home)
        profile = update_profile(
            path,
            profile_id,
            name=name,
            api_format=api_format,
            model=model.strip() or None,
            base_url=base_url.strip() or None,
            **send,
        )
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, profile_saved(profile.name), profile.id)


def add_new_profile(
    *,
    template: str = DEFAULT_TEMPLATE,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「追加」。選んだひな形(REQ-100 §5)の値を展開して、モデル未設定の1件を足す。知らないひな形は SettingsError。

    名前はひな形の表示名(重なれば番号を付ける)。「独自 API」(既定。今の「追加」と同じ OpenAI 互換の空の1件)だけは
    「新しいプロファイル」の名前のまま。
    """
    chosen = PROFILE_TEMPLATES.get(template)
    if chosen is None:
        raise SettingsError("ひな形の種類が違います")
    if template == DEFAULT_TEMPLATE:
        make_name: Callable[[int], str] = new_profile_name
    else:
        make_name = lambda n: template_profile_name(PROFILE_TEMPLATE_LABEL[template], n)  # noqa: E731
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        profiles = _ensure_saved(path, cwd=cwd, env=env, platform=platform, home=home)
        profile = add_profile(
            path,
            name=_free_name(profiles, make_name),
            api_format=chosen.api_format,
            base_url=chosen.base_url,
            auth_header=chosen.auth_header,
            wire=chosen.wire,
        )
    except SettingsError as exc:
        return ProfileAction(False, str(exc))
    return ProfileAction(True, profile_added(profile.name), profile.id)


def duplicate_selected(
    profile_id: str,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「複製」。「<名前> のコピー」で形式・モデル・接続先を写す。キーは写さない(REQ-42 のまま)。"""
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        if load_profiles(path) is None and profile_id != DEFAULT_PROFILE_ID:
            raise _no_profile(path)
        profiles = _ensure_saved(path, cwd=cwd, env=env, platform=platform, home=home)
        source = profiles.get(profile_id)
        if source is None:
            raise _no_profile(path)
        name = _free_name(profiles, lambda n: copy_profile_name(source.name, n))
        profile = duplicate_profile(path, profile_id, name=name)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, profile_duplicated(profile.name), profile.id)


def delete_refusal(view: SettingsView, profile_id: str) -> str | None:
    """「削除」で確認を出す前に見る。消せないとき(最後の1件・使用中)は理由の文。消せるなら None。"""
    if view.error is not None:
        return profile_unreadable(view.error)
    if not view.profiles_saved or len(view.profiles) <= 1:
        return profile_delete_last()
    if profile_id == view.active_id:
        return profile_delete_in_use()
    return None


def delete_selected(
    profile_id: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「削除」(確認の後)。プロファイルとそのキーを消す。キーを保管庫から消せなかったときは、その理由を添える。"""
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        profiles = load_profiles(path)
        if profiles is None or len(profiles.profiles) == 1:
            return ProfileAction(False, profile_delete_last(), profile_id)
        target = profiles.get(profile_id)
        if target is None:
            raise _no_profile(path)
        if profile_id == profiles.active:
            return ProfileAction(False, profile_delete_in_use(), profile_id)
        result = delete_profile(path, profile_id, platform=platform)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    if not result.ok:
        return ProfileAction(False, profile_delete_key_failed(target.name, result.message), profiles.active)
    return ProfileAction(True, profile_deleted(target.name), profiles.active)


def use_selected(
    profile_id: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「このプロファイルを使う」。使用中のものなら「使用中です」と言うだけで書かない。"""
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        profiles = load_profiles(path)
        if profiles is None:
            if profile_id != DEFAULT_PROFILE_ID:
                raise _no_profile(path)
            return ProfileAction(True, PROFILE_ALREADY_IN_USE, profile_id)
        target = profiles.get(profile_id)
        if target is None:
            raise _no_profile(path)
        if profile_id == profiles.active:
            return ProfileAction(True, PROFILE_ALREADY_IN_USE, profile_id)
        set_active(path, profile_id)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, profile_now_in_use(target.name), profile_id)


# --- custom-http-json の定義の書き出し・読み込み(REQ-101 §4.3。ファイルの読み書きは llm_profiles の関数だけ。キーは扱わない) ---


def _custom_profile(path: Path, profile_id: str) -> LlmProfile | None:
    """ファイルにある、形式が custom-http-json で定義を持つプロファイル。無ければ None。"""
    profiles = load_profiles(path)
    profile = profiles.get(profile_id) if profiles is not None else None
    if profile is None or profile.api_format != CUSTOM_HTTP_JSON or profile.custom is None:
        return None
    return profile


def export_custom_definition(
    profile_id: str,
    target: Path,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「定義を書き出す…」(場所を選んだ後)。ファイルの定義を読み直してから書く。保管庫のキーは入れない。"""
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        profile = _custom_profile(path, profile_id)
        if profile is None:
            return ProfileAction(False, CUSTOM_FORMAT_REQUIRED, profile_id)
        write_custom_file(target, profile.custom)
    except SettingsError as exc:
        return ProfileAction(False, custom_export_failed(str(exc)), profile_id)
    return ProfileAction(True, custom_exported(target), profile_id)


def read_custom_definition(path: Path) -> tuple[CustomHttpJson, str] | str:
    """「定義を読み込む…」の1回目。(定義, ファイルのハッシュ)。決まりに反する・読めないときは理由の文(値は入れない)。"""
    try:
        return read_custom_file(path)
    except SettingsError as exc:
        return custom_import_unreadable(str(exc))


def import_custom_definition(
    profile_id: str,
    source: Path,
    digest: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「定義を読み込む…」の確かめの後。ファイルを読み直し、1回目と同じ中身(ハッシュ)のときだけ定義を置き換える。

    プロファイルがまだ custom-http-json かも確かめる。キーは読まない・保管庫には触らない。
    """
    read = read_custom_definition(source)
    if isinstance(read, str):
        return ProfileAction(False, read, profile_id)
    custom, now = read
    if now != digest:
        return ProfileAction(False, CUSTOM_IMPORT_CHANGED, profile_id)
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        if _custom_profile(path, profile_id) is None:
            return ProfileAction(False, CUSTOM_FORMAT_REQUIRED, profile_id)
        profile = update_profile(path, profile_id, custom=custom)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, custom_imported(profile.name), profile.id)


# --- プロファイルをファイルにまとめて書き出す・取り込む(ADR-0082・REQ-119。読み書きは profile_bundle の関数だけ。
# キーは扱わない・保管庫に触らない。profile_bundle を読むのはこのモジュールだけ) ---


def _bundle_paths(env: Mapping[str, str] | None, platform: str | None, home: Path | None) -> dict[str, Path]:
    return {
        "llm_path": profiles_path(platform=platform, env=env, home=home),
        "prompt_path": _prompt_path(env, platform, home),
    }


def prepare_bundle_export(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> tuple[int, int] | str:
    """「プロファイルを書き出す…」の1回目。保存してある (接続の件数, 指示文の件数)。何も書かない。

    書き出せない(読めない・キーらしいものがある)ときは理由の文(値は入れない)。
    """
    try:
        data = _bundle_data(**_bundle_paths(env, platform, home))
    except SettingsError as exc:
        return profile_bundle_export_failed(str(exc))
    return len(data["connections"]), len(data["prompts"])


def export_profile_bundle(
    target: Path, *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> ProfileAction:
    """「プロファイルを書き出す…」(確かめて場所を選んだ後)。ファイルを読み直してから書く。保存していない編集は入らない。"""
    try:
        connections, prompts = write_bundle(target, **_bundle_paths(env, platform, home))
    except SettingsError as exc:
        return ProfileAction(False, profile_bundle_export_failed(str(exc)))
    return ProfileAction(True, profile_bundle_exported(target, connections, prompts))


@dataclass(frozen=True)
class BundleRead:
    """「プロファイルを取り込む…」の1回目の結果。confirm は確かめの文、digest はファイルのハッシュ、names は決めた名前。"""

    confirm: str
    digest: str
    names: tuple[tuple[str, ...], tuple[str, ...]]


def _one_line(text: str) -> str:
    """確かめの一覧に出す、ファイルから読んだ文字(改行などの制御の文字は空白にして、行を作らせない)。"""
    return "".join(ch if ch.isprintable() else " " for ch in text)


def _destination(profile: LlmProfile) -> str:
    """取り込みの確かめに出す接続先(あとで入れたキーが送られる先。ADR-0082 決定4)。"""
    if profile.custom is not None and profile.api_format == CUSTOM_HTTP_JSON:
        return profile.custom.url
    if is_cli(profile.api_format):
        return PROFILE_BUNDLE_LOCAL_CLI
    return profile.base_url or PROFILE_BUNDLE_DEFAULT_URL


def read_profile_bundle(
    source: Path, *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> BundleRead | str:
    """「プロファイルを取り込む…」の1回目。ファイルを確かめ、名前と接続先の一覧の確かめの文を作る(何も書かない)。

    決まりに反する・読めないときは理由の文(値は入れない)。
    """
    paths = _bundle_paths(env, platform, home)
    try:
        bundle = read_bundle(source)
        names = plan_names(bundle, load_profiles(paths["llm_path"]), load_prompt_profiles(paths["prompt_path"]))
    except SettingsError as exc:
        return profile_bundle_unreadable(str(exc))
    connection_names, prompt_names = names
    lines = [
        profile_bundle_connection_line(
            _one_line(name), API_FORMAT_SHORT.get(profile.api_format, profile.api_format), _one_line(_destination(profile))
        )
        for profile, name in zip(bundle.connections, connection_names, strict=True)
    ]
    body = profile_bundle_import_confirm(lines, [_one_line(name) for name in prompt_names])
    return BundleRead(body, bundle.digest, names)


def import_profile_bundle(
    source: Path,
    digest: str,
    names: tuple[tuple[str, ...], tuple[str, ...]],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「プロファイルを取り込む…」の確かめの後。ファイルを読み直し、確かめたときと同じ中身と名前のときだけ足す。

    既存のプロファイルと使用中は変えない。保管庫には触らない(取り込んだ接続にキーは無い)。
    取り込む接続があるのに llm-profiles.json が無ければ、ほかのプロファイルの操作(_ensure_saved)と同じ1件目
    (今の設定のモデル・接続先で id "default"・名前「既定」・使用中)を作ってから足す(ADR-0184 決定1)。
    モデル・接続先は config.toml と作業フォルダの設定だけから読み、保管庫は読まない(_ensure_saved が使う load_settings は
    キーも読むため使わない。ADR-0082 決定2)。ハッシュや名前の確かめで断るときは作らない。
    """
    paths = _bundle_paths(env, platform, home)

    def ensure_first() -> None:
        model, base_url = load_model_and_base_url(cwd=cwd, env=env, platform=platform, home=home)
        create_first(paths["llm_path"], model=model, base_url=base_url)

    try:
        result = _import_bundle(source, digest, names, ensure_first=ensure_first, **paths)
    except BundleChanged:
        return ProfileAction(False, PROFILE_BUNDLE_CHANGED)
    except SettingsError as exc:
        return ProfileAction(False, profile_bundle_unreadable(str(exc)))
    if result.prompt_error is not None:
        return ProfileAction(False, profile_bundle_partly_imported(len(result.connections), result.prompt_error))
    return ProfileAction(True, profile_bundle_imported(len(result.connections), len(result.prompts)))


def response_shape_view(node: ShapeNode | None) -> dict[str, Any] | None:
    """接続テストの応答の形の木を、画面に渡す JSON にする(名前・型・型の表示名・道筋・子だけ。値は持たない)。

    深すぎて作れないときは None(木を出さない)。
    """
    if node is None:
        return None
    try:
        return _shape_json(node)
    except RecursionError:
        return None


def _shape_json(node: ShapeNode) -> dict[str, Any]:
    return {
        "name": node.name,
        "type": node.kind,
        "type_label": SHAPE_TYPE_LABEL[node.kind],
        "path": node.path,
        "children": [_shape_json(child) for child in node.children],
    }


def editor_changed(
    editor: ProfileEditor | None,
    name: str,
    api_format: str,
    model: str,
    base_url: str,
    wire: ProfileWireText | None = None,
) -> bool:
    """編集の欄に、保存していない変更があるか(前後の空白は比べない)。

    wire(送り方の欄の文字)を渡したときだけ送り方の欄も比べる(wx の画面は今の4つで呼ぶ)。
    """
    if editor is None:
        return False
    if (name.strip(), api_format, model.strip(), base_url.strip()) != (
        editor.name,
        editor.api_format,
        editor.model,
        editor.base_url,
    ):
        return True
    return wire is not None and wire.normalized() != editor.wire.normalized()


Typed = tuple[str, str, str, str] | tuple[str, str, str, str, ProfileWireText]


def kept_input(before: ProfileEditor | None, after: ProfileEditor | None, typed: Typed) -> Typed | None:
    """出し直した後も編集の欄に残す値(名前, 形式, モデル, 接続先[, 送り方の欄の文字])。残さなければ None(保存済みの値を出す)。

    キーの保存・削除・ひな形の後の出し直しで使う。同じプロファイルのままで、保存していない変更があるときだけ入れた値を残す
    (確認なく消さない)。
    """
    if before is None or after is None or before.id != after.id or not editor_changed(before, *typed):
        return None
    return typed


def key_target(view: SettingsView) -> tuple[str, bool] | str:
    """キーの保存・削除・接続テストに使う (プロファイルの id, 使用中か)。どれか決められないときは理由の文(保管庫に触らない)。"""
    if view.key_target_id is None:
        return key_target_unknown(view.error or "")
    return view.key_target_id, view.editor.in_use if view.editor is not None else True


# --- API キーの保存・削除(REQ-34 §3。保管庫だけに書く。設定ファイルは書かない) ---


def store_problem() -> str | None:
    """保管庫が使えない理由(「削除…」で確認を出す前に見る)。使えるなら None。"""
    return _store_problem()


def save_api_key(
    text: str,
    *,
    profile_id: str = DEFAULT_PROFILE_ID,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> StoreResult:
    """「保管庫に保存」(選んだプロファイルの保存名で)。そのプロファイルの環境変数のキーが優先されるときは、そう言う。

    使用中かどうかでは変えない(ADR-0077 決定2)。ファイルが無いときは共通の変数を見る。
    """
    result = save_key(text, profile_id=profile_id, platform=platform)
    if result.ok and _env_key_present(profile_id, env=env, platform=platform, home=home):
        return StoreResult(True, api_key_saved_env_wins())
    return result


def key_replace_confirm(profile_id: str, name: str) -> str | None:
    """「保管庫に保存」の前に見る(REQ-112)。その接続の保存名にもうキーがあれば、置き換えの確かめの文。無ければ None。

    文に入れるのはキーの末尾4文字だけ(display_key と同じ決まりで、12 文字より短いキーは末尾も入れない。ADR-0033)。
    キーの全体はこの関数の中の変数にしか置かない。読めない・保管庫が使えないときは None(保存のときに理由を知らせる)。
    """
    key = read_key(profile_id).key
    if key is None:
        return None
    return api_key_replace_confirm(name, key[-4:] if len(key) >= 12 else None)


def delete_api_key(*, profile_id: str = DEFAULT_PROFILE_ID, platform: str | None = None) -> StoreResult:
    """「削除…」(確認のダイアログの後。選んだプロファイルの保存名で)。保存されていなくても落ちない。"""
    return delete_key(profile_id=profile_id, platform=platform)


# --- 設定ファイル(config.toml)の上限と伏せる語(REQ-93 §3 / ADR-0071 決定1・3・4。書くのは user_config_writer だけ) ---


def _parse_limit(text: str) -> int | None:
    """上限の欄の文字を数にする。空・数でない・1 未満は None。"""
    value = text.strip()
    if not value.isdecimal():
        return None
    number = int(value)
    return number if number >= 1 else None


def parse_words(text: str) -> tuple[list[str], list[int]]:
    """伏せる語の欄(1行1語)。前後の空白と空の行は除く。返すのは (語, 空白を含む行の番号)。決まりは読み込みと同じ。"""
    words: list[str] = []
    bad: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        word = line.strip()
        if not word:
            continue
        if is_secret_word(word):
            words.append(word)
        else:
            bad.append(number)
    return words, bad


def config_fields_changed(
    fields: ConfigFields | None,
    max_diffs: str,
    max_total_diffs: str,
    words: str,
    prompt_template: None = None,
    auto_compare: bool | None = None,
    auto_send_llm: bool | None = None,
    character: bool | None = None,
) -> bool:
    """枠に保存していない入力があるか(前面に来たときに読み直してよいかを見る)。

    prompt_template は使わない(REQ-116 で指示文はこの枠から出した。今の呼び方を変えないために位置だけ残す)。
    auto_compare は「自動で比較を始める」のチェック(pywebview の画面だけ)。None なら比べない(wx の画面には欄が無い)。
    auto_send_llm は「比較の後に自動で AI に送る」のチェック(pywebview の画面だけ。None なら比べない)。
    character は「キャラクター（Mirarin）を表示する」のチェック(pywebview の画面だけ。None なら比べない。ADR-0174 決定1)。
    """
    if fields is None:
        return False
    typed = (max_diffs.strip(), max_total_diffs.strip(), [w.strip() for w in words.splitlines() if w.strip()])
    if typed != (str(fields.max_diffs), str(fields.max_total_diffs), list(fields.words)):
        return True
    if auto_send_llm is not None and auto_send_llm != fields.auto_send_llm:
        return True
    if character is not None and character != fields.character:
        return True
    return auto_compare is not None and auto_compare != fields.auto_compare


def auto_send_turned_on(fields: ConfigFields, auto_send_llm: bool | None) -> bool:
    """「保存」で自動送信をオフからオンにするか(そのときは送り先を見せて確かめる。ADR-0080 決定2)。"""
    return auto_send_llm is True and not fields.auto_send_llm


@dataclass(frozen=True)
class AutoSendStart:
    """自動送信をオンにする前の準備。使えるなら confirm に確かめの文、使えないなら refused に知らせの文(何も書かない)。"""

    confirm: str | None = None
    refused: str | None = None


def auto_send_start(
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
    client_factory: Callable[..., LLMClient] | None = None,
) -> AutoSendStart:
    """使用中の接続が使えるかを、接続テストと同じ作り方で見る(送らない)。使えれば接続の名前と接続先の確かめの文。

    キーが無い・モデルが無い・設定を読めないときは、接続テストと同じ理由を添えた「オンにできません」の文(ADR-0080 決定2)。
    """
    start = connection_check_start(cwd=cwd, env=env, platform=platform, home=home, client_factory=client_factory)
    if start.plan is None:
        return AutoSendStart(refused=auto_send_unusable(start.refused or ""))
    plan = start.plan
    destination = plan.base_url or f"既定（{plan.default_base_url}）"
    return AutoSendStart(confirm=auto_send_confirm(start.profile_name or DEFAULT_PROFILE_NAME, destination))


def save_config(
    fields: ConfigFields,
    max_diffs: str,
    max_total_diffs: str,
    words: str,
    prompt_template: None = None,
    auto_compare: bool | None = None,
    auto_send_llm: bool | None = None,
    *,
    character: bool | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ConfigAction:
    """「保存」(設定ファイルの枠)。読み込んだときと違う項目だけを書く。

    入力が正しくなければ何も書かずに理由を返す(入力は画面に残す)。既定と同じ値でも、変えた項目は書いて残す。
    画面が読んだ後に設定ファイルがほかで変わっていたら書かず、reload を立てる(ADR-0071 決定4)。
    prompt_template は使わない(REQ-116 で指示文はこの枠から出した。今の呼び方を変えないために位置だけ残す)。
    auto_compare は「自動で比較を始める」のチェック(pywebview の画面だけ。None なら触らない)。
    読んだときと違うときだけ [gui] の auto_compare を書く(ADR-0076 決定3)。
    auto_send_llm は「比較の後に自動で AI に送る」のチェック(pywebview の画面だけ。None なら触らない)。
    読んだときと違うときだけ [gui] の auto_send_llm を書く(ADR-0080 決定2。オンにする前の確かめは画面が出してから呼ぶ)。
    character は「キャラクター（Mirarin）を表示する」のチェック(pywebview の画面だけ。None なら触らない)。
    読んだときと違うときだけ、auto_compare と同じ一度の書き込みで [gui] の character を書く(ADR-0174 決定1)。
    [llm].prompt_template には触らない(書いてあっても残す。ADR-0079 決定5)。
    """
    values: dict[str, object] = {}
    for key, label, text, before in (
        ("max_diffs", LIMIT_PER_JOB, max_diffs, fields.max_diffs),
        ("max_total_diffs", LIMIT_TOTAL, max_total_diffs, fields.max_total_diffs),
    ):
        number = _parse_limit(text)
        if number is None:
            return ConfigAction(False, limit_invalid(label))
        if number != before:
            values[key] = number
    typed_words, bad = parse_words(words)
    if bad:
        return ConfigAction(False, secret_word_has_space(bad))
    if tuple(typed_words) != fields.words:
        values["extra_secret_words"] = typed_words
    gui_values: dict[str, object] = {}
    if auto_compare is not None and auto_compare != fields.auto_compare:
        gui_values["auto_compare"] = auto_compare
    if character is not None and character != fields.character:
        gui_values["character"] = character
    auto_send = auto_send_llm is not None and auto_send_llm != fields.auto_send_llm
    if not values and not gui_values and not auto_send:
        return ConfigAction(True, CONFIG_NOT_CHANGED)
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    # [llm] を書く → [gui] を書く → 自動送信を書く の順に、書いた後のハッシュをつなぐ
    digest = fields.digest
    written = False
    try:
        if values:
            digest = update_llm_items(path, digest, values)
            written = True
        if gui_values:
            digest = update_gui_items(path, digest, gui_values)
            written = True
        if auto_send:
            update_gui_auto_send(path, digest, bool(auto_send_llm))
    except ConfigChanged:
        return ConfigAction(False, CONFIG_CHANGED_ELSEWHERE, reload=True)
    except SettingsError as exc:
        # 先に書いた項目があればファイルは変わっているので、画面を読み直させる
        return ConfigAction(False, config_not_saved(str(exc)), reload=written)
    return ConfigAction(True, config_saved(path))


def save_auto_update_check(
    fields: ConfigFields,
    checked: bool,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ConfigAction:
    """「更新」のタブの「保存」。[gui] の auto_update_check の 1 項目だけを書く(ADR-0188 決定2 / REQ-252 §4)。

    書き方は character と同じ(update_gui_items。読んだときのハッシュを付け、ほかで変わっていたら書かずに reload を立てる)。
    読んだときと同じ値なら何も書かない。ほかの項目(上限・伏せる語・auto_compare・character・auto_send_llm)には触らない。
    """
    if checked == fields.auto_update_check:
        return ConfigAction(True, CONFIG_NOT_CHANGED)
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    try:
        update_gui_items(path, fields.digest, {AUTO_UPDATE_CHECK_KEY: checked})
    except ConfigChanged:
        return ConfigAction(False, CONFIG_CHANGED_ELSEWHERE, reload=True)
    except SettingsError as exc:
        return ConfigAction(False, config_not_saved(str(exc)))
    return ConfigAction(True, config_saved(path))


def config_changed(
    digest: str | None,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> bool:
    """設定ファイルが、画面が読んだとき(digest)から変わったか。読めないときも変わったとみなす(読み直して理由を出す)。"""
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    try:
        return file_digest(path) != digest
    except SettingsError:
        return True


# --- AI への指示文のプロファイル(REQ-116 §3 / ADR-0079。読み書きは prompt_profiles の関数だけ。押したときだけ書く) ---


@dataclass(frozen=True)
class PromptRow:
    """指示文の一覧の1行。in_use は使用中(バッジと帯)、selected は選んでいるもの。detail は2行目の文。"""

    id: str
    name: str
    detail: str
    in_use: bool
    selected: bool
    builtin: bool


@dataclass(frozen=True)
class PromptPane:
    """「AI への指示文」の枠の状態。name・template は選んでいるものの保存した値(欄の初期値)。

    readonly は最初から入っているもの(欄を読むだけにし、保存・削除を押せなくする)。
    error は指示文のファイルを読めないときの理由(そのとき一覧は空で、選んでいるものは無い)。
    user_config_note は config.toml の [llm].prompt_template を読んでいないときの注意。digest は読んだファイルのハッシュ。
    """

    rows: list[PromptRow]
    active_id: str | None
    selected_id: str | None
    name: str
    template: str
    readonly: bool
    error: str | None
    user_config_note: str | None
    digest: str | None

    @property
    def can_edit(self) -> bool:
        """「保存」「削除…」を押せるか(利用者の指示文を選んでいるとき)。"""
        return self.selected_id is not None and not self.readonly


def _prompt_path(env: Mapping[str, str] | None, platform: str | None, home: Path | None) -> Path:
    return prompt_profiles_path(platform=platform, env=env, home=home)


def _no_prompt(path: Path) -> SettingsError:
    return SettingsError(f"{path}: 指定した id のプロンプトのプロファイルがありません")


def _user_prompt_note(env: Mapping[str, str] | None, platform: str | None, home: Path | None) -> str | None:
    """config.toml に [llm].prompt_template があるときの注意(読まない。ADR-0079 決定5)。読めないときは出さない。"""
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    try:
        section = parse_section(read_bytes(path), path)
    except SettingsError:
        return None  # 読めないことは設定の枠が知らせる
    return user_prompt_template_ignored(path) if PROMPT_TEMPLATE_KEY in section else None


def prompt_pane(
    selected: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> PromptPane:
    """「AI への指示文」の枠の状態。読むだけで、ファイルを作らない。selected が一覧に無ければ使用中のものを選ぶ。"""
    path = _prompt_path(env, platform, home)
    note = _user_prompt_note(env, platform, home)
    try:
        profiles = load_prompt_profiles(path)
    except SettingsError as exc:
        try:
            digest = prompt_file_digest(path)
        except SettingsError:
            digest = None
        return PromptPane([], None, None, "", "", True, prompt_profiles_unreadable(str(exc)), note, digest)
    chosen = (profiles.get(selected) if selected is not None else None) or profiles.active_profile
    rows = [
        PromptRow(
            id=p.id,
            name=p.name,
            detail=PROMPT_ROW_DETAIL[p.id] if p.builtin else PROMPT_ROW_USER,
            in_use=p.id == profiles.active,
            selected=p.id == chosen.id,
            builtin=p.builtin,
        )
        for p in profiles.all()
    ]
    return PromptPane(rows, profiles.active, chosen.id, chosen.name, chosen.template, chosen.builtin, None, note, profiles.digest)


def prompt_changed(pane: PromptPane | None, name: str, template: str) -> bool:
    """欄に保存していない変更があるか。最初から入っているもの(読むだけ)・読めないときは変更とみなさない。

    名前は前後の空白を比べない。本文は改行を \\n にそろえて比べる。
    """
    if pane is None or not pane.can_edit:
        return False
    return name.strip() != pane.name or normalize_newlines(template) != pane.template


# --- 毎回付ける前提(ADR-0172 決定6 / REQ-234 §5.2。読み書きは premise の関数だけ。押したときだけ書く) ---


@dataclass(frozen=True)
class PremiseView:
    """「毎回付ける前提」の欄の状態。text は保存してある文(無ければ "")。error は読めないときの理由の文(そのとき text は "")。

    max_chars は文字数の上限(画面の「1,234 / 4,000 文字」に出すだけ。判定は save_premise)。repr に前提の中身を出さない。
    """

    text: str = field(repr=False)
    error: str | None
    max_chars: int = PREMISE_MAX_CHARS


def premise_values(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> PremiseView:
    """保存してある前提の文。読むだけで、ファイルを作らない。読めなければ理由の文(帯に出す)。"""
    try:
        text = load_premise(premise_path(platform=platform, env=env, home=home))
    except SettingsError as exc:
        return PremiseView("", premise_unreadable(str(exc)))
    return PremiseView(text or "", None)


def premise_changed(view: PremiseView | None, typed: str) -> bool:
    """欄に保存していない変更があるか。改行を \\n にそろえて前後の空白を除いて比べる(保存するときと同じ形)。

    読めないとき(error がある)は変更とみなさない(読めない中身と比べられないため。保存もできない)。
    """
    if view is None or view.error is not None:
        return False
    return normalize_newlines(typed).strip() != view.text


def save_premise(
    text: str,
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> str | None:
    """「前提を保存」。保存できたら None、断ったら知らせの文(どの文にも前提の中身は入れない)。

    順に: 上限(改行をそろえて前後の空白を除いた文で数える) → 前提のファイルが読めるか(読めない中身を確かめずに上書きしない) →
    設定の窓が今読んでいる伏せる語(load_settings(read_prompt=False) の extra_secret_words)で check_question
    (設定が読めず語が分からないときは保存しない) → write_premise。空の文は確かめずに空のファイルを書く(付けない)。
    """
    body = normalize_newlines(text).strip()
    if len(body) > PREMISE_MAX_CHARS:
        return PREMISE_TOO_LONG
    path = premise_path(platform=platform, env=env, home=home)
    try:
        load_premise(path)
    except SettingsError as exc:
        return premise_unreadable(str(exc))
    if body:
        try:
            words = load_settings(cwd=cwd, env=env, platform=platform, home=home, read_prompt=False).extra_secret_words
        except SettingsError as exc:
            return premise_settings_unreadable(str(exc))
        refused = premise_text_problem(body, words)
        if refused is not None:
            return PREMISE_TOO_LONG if refused == "too_long" else PREMISE_SECRET_REFUSED
    try:
        write_premise(path, body)
    except SettingsError as exc:
        return str(exc)
    return None


def premise_saved_notice() -> str:
    """保存できたときの知らせ(今のプロンプトの保存と同じ形)。"""
    return PREMISE_SAVED


def _prompt_problem(name: str, template: str) -> str | None:
    """ファイルを読まずにできる確かめ(空の名前・使えない本文)。文に本文は入れない。"""
    if not name.strip():
        return profile_name_empty()
    if (problem := check_prompt_template(normalize_newlines(template))) is not None:
        return prompt_template_invalid(problem)
    return None


def _free_prompt_name(profiles: PromptProfiles, make: Callable[[int], str]) -> str:
    """重ならない名前(最初から入っている名前も含め、大文字・小文字を区別しない)。"""
    return next(make(n) for n in itertools.count(1) if profiles.find(make(n)) is None)


def prompt_input_problem(
    profile_id: str,
    name: str,
    template: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> str | None:
    """保存できない理由(空の名前・使えない本文・ほかと重なる名前)。保存できる形なら None。ファイルには書かない。"""
    if (problem := _prompt_problem(name, template)) is not None:
        return problem
    try:
        profiles = load_prompt_profiles(_prompt_path(env, platform, home))
    except SettingsError as exc:
        return str(exc)
    other = profiles.find(name)
    if other is not None and other.id != profile_id:
        return prompt_name_taken(name.strip())
    return None


def save_prompt(
    profile_id: str,
    name: str,
    template: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「保存」(指示文)。最初から入っているもの・空の名前・重なる名前・使えない本文は、何も書かずに断る。"""
    if (problem := _prompt_problem(name, template)) is not None:
        return ProfileAction(False, problem, profile_id)
    path = _prompt_path(env, platform, home)
    try:
        profiles = load_prompt_profiles(path)
        target = profiles.get(profile_id)
        if target is None:
            raise _no_prompt(path)
        if target.builtin:
            return ProfileAction(False, PROMPT_BUILTIN_READ_ONLY, profile_id)
        other = profiles.find(name)
        if other is not None and other.id != profile_id:
            return ProfileAction(False, prompt_name_taken(name.strip()), profile_id)
        profile = update_prompt_profile(path, profile_id, name=name, template=template)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, prompt_profile_saved(profile.name), profile.id)


def save_prompt_as_new(
    source_id: str,
    name: str,
    template: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """3択の確かめの「新しいプロファイルとして保存」(REQ-112 と同じ)。入れた値で新しい指示文を足す(元は変えない)。

    名前が元の指示文と重なるときは「<名前> のコピー」(重なれば番号を付ける)。ほかと重なるときは断る。
    """
    if (problem := _prompt_problem(name, template)) is not None:
        return ProfileAction(False, problem, source_id)
    path = _prompt_path(env, platform, home)
    try:
        profiles = load_prompt_profiles(path)
        typed = name.strip()
        other = profiles.find(typed)
        if other is not None and other.id != source_id:
            return ProfileAction(False, prompt_name_taken(typed), source_id)
        if other is not None:
            typed = _free_prompt_name(profiles, lambda n: copy_profile_name(name.strip(), n))
        profile = add_prompt_profile(path, name=typed, template=template)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), source_id)
    return ProfileAction(True, prompt_profile_saved(profile.name), profile.id)


def add_prompt(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> ProfileAction:
    """「新規作成」(指示文)。既定の文で「新しい指示文」(重なれば番号を付ける)を足す。"""
    path = _prompt_path(env, platform, home)
    try:
        profiles = load_prompt_profiles(path)
        profile = add_prompt_profile(path, name=_free_prompt_name(profiles, new_prompt_name), template=DEFAULT_PROMPT_TEMPLATE)
    except SettingsError as exc:
        return ProfileAction(False, str(exc))
    return ProfileAction(True, prompt_profile_added(profile.name), profile.id)


def duplicate_prompt(
    profile_id: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「複製」(指示文)。「<名前> のコピー」で本文を写す。最初から入っているものも複製できる。"""
    path = _prompt_path(env, platform, home)
    try:
        profiles = load_prompt_profiles(path)
        source = profiles.get(profile_id)
        if source is None:
            raise _no_prompt(path)
        name = _free_prompt_name(profiles, lambda n: copy_profile_name(source.name, n))
        profile = duplicate_prompt_profile(path, profile_id, name=name)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, prompt_profile_duplicated(profile.name), profile.id)


def prompt_delete_refusal(pane: PromptPane, profile_id: str) -> str | None:
    """「削除…」で確認を出す前に見る。消せないとき(読めない・最初から入っている・使用中)は理由の文。消せるなら None。"""
    if pane.error is not None:
        return pane.error
    row = next((r for r in pane.rows if r.id == profile_id), None)
    if row is None or row.builtin:
        return PROMPT_BUILTIN_READ_ONLY
    if row.in_use:
        return prompt_profile_delete_in_use()
    return None


def delete_prompt(
    profile_id: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「削除」(確認の後)。読み直し、まだあるか・最初から入っていないか・使用中でないかを確かめ直してから消す。"""
    path = _prompt_path(env, platform, home)
    try:
        profiles = load_prompt_profiles(path)
        target = profiles.get(profile_id)
        if target is None:
            raise _no_prompt(path)
        if target.builtin:
            return ProfileAction(False, PROMPT_BUILTIN_READ_ONLY, profile_id)
        if profile_id == profiles.active:
            return ProfileAction(False, prompt_profile_delete_in_use(), profile_id)
        delete_prompt_profile(path, profile_id)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, prompt_profile_deleted(target.name), profiles.active)


def use_prompt(
    profile_id: str,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ProfileAction:
    """「このプロファイルを使う」(指示文)。使用中のものなら「使用中です」と言うだけで書かない。"""
    path = _prompt_path(env, platform, home)
    try:
        profiles = load_prompt_profiles(path)
        target = profiles.get(profile_id)
        if target is None:
            raise _no_prompt(path)
        if profile_id == profiles.active:
            return ProfileAction(True, PROFILE_ALREADY_IN_USE, profile_id)
        set_active_prompt(path, profile_id)
    except SettingsError as exc:
        return ProfileAction(False, str(exc), profile_id)
    return ProfileAction(True, prompt_profile_now_in_use(target.name), profile_id)


# --- 比較の画面の AI の面の切り替え(REQ-206 §5 / ADR-0148 決定4・7)。名前だけを画面に渡す(接続先の URL・キー・CLI の場所は渡さない) ---


@dataclass(frozen=True)
class ProfileChoices:
    """切り替えの選択肢。ids は中で使う id、names は画面に出す名前(同じ並び)、active は使用中の位置(無ければ -1)。

    problem はファイルを読めないときの理由(そのとき選択肢は空)。切り替えは use_selected・use_prompt で行う。
    """

    ids: tuple[str, ...]
    names: tuple[str, ...]
    active: int
    problem: str | None = None


def llm_choices(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> ProfileChoices:
    """AI の接続のプロファイルの選択肢。ファイルが無ければ「既定」の1つ(使用中)。読むだけで、ファイルを作らない。"""
    path = profiles_path(platform=platform, env=env, home=home)
    try:
        profiles = load_profiles(path)
    except SettingsError as exc:
        return ProfileChoices((), (), -1, profile_unreadable(str(exc)))
    if profiles is None:
        return ProfileChoices((DEFAULT_PROFILE_ID,), (DEFAULT_PROFILE_NAME,), 0)
    ids = tuple(p.id for p in profiles.profiles)
    return ProfileChoices(ids, tuple(p.name for p in profiles.profiles), ids.index(profiles.active))


def prompt_choices(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> ProfileChoices:
    """AI への指示文のプロファイルの選択肢(最初から入っているものを含む)。読むだけで、ファイルを作らない。"""
    try:
        profiles = load_prompt_profiles(_prompt_path(env, platform, home))
    except SettingsError as exc:
        return ProfileChoices((), (), -1, prompt_profiles_unreadable(str(exc)))
    rows = profiles.all()
    ids = tuple(p.id for p in rows)
    return ProfileChoices(ids, tuple(p.name for p in rows), ids.index(profiles.active) if profiles.active in ids else -1)


def destination_of(settings: LlmSettings) -> tuple[str, str]:
    """送り先の AI の接続のプロファイル (id, 名前)。ファイルが無ければ default と「既定」(ADR-0148 決定7)。"""
    if settings.profile_id is None:
        return DEFAULT_PROFILE_ID, DEFAULT_PROFILE_NAME
    return settings.profile_id, settings.profile_name or settings.profile_id


def prompt_profiles_changed(
    digest: str | None,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> bool:
    """指示文のファイルが、画面が読んだとき(digest)から変わったか。読めないときも変わったとみなす(読み直して理由を出す)。"""
    try:
        return prompt_file_digest(_prompt_path(env, platform, home)) != digest
    except SettingsError:
        return True


# --- 平文のキーを保管庫へ移す(REQ-93 §4 / ADR-0071 決定2)。キーの値はこの関数の中だけで扱い、どの文にも入れない ---


@dataclass(frozen=True)
class MigrateStart:
    """「保管庫に移す」を押したときの準備。refused があれば確認を出さずに理由を出す。

    overwrite は保管庫の default にすでに別のキーがある(上書きの確認を出す。値は見せない)。
    """

    digest: str | None
    confirm: str = ""
    overwrite_confirm: str | None = None
    refused: str | None = None
    reload: bool = False


def migrate_start(
    view: SettingsView,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> MigrateStart:
    """確認のダイアログを出す前に見る。設定ファイルが外で変わっていた・キーが無い・保管庫を使えないときは断る。"""
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    try:
        found = read_api_key(path)
    except SettingsError as exc:
        return MigrateStart(None, refused=config_not_saved(str(exc)), reload=True)
    if found.digest != view.config_digest:
        return MigrateStart(found.digest, refused=migrate_file_changed(), reload=True)
    if found.key is None:
        return MigrateStart(found.digest, refused=migrate_no_key(), reload=True)
    if (problem := _migrate_problem()) is not None:
        return MigrateStart(found.digest, refused=problem)
    current = read_key(DEFAULT_PROFILE_ID)
    if current.problem is not None:
        return MigrateStart(found.digest, refused=migrate_save_failed(current.problem))
    other = current.key is not None and current.key != found.key
    return MigrateStart(
        found.digest,
        confirm=migrate_confirm(view.store_name),
        overwrite_confirm=migrate_overwrite_confirm(view.store_name) if other else None,
    )


def migrate_plaintext_key(
    expected_digest: str | None,
    *,
    overwrite: bool = False,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> ConfigAction:
    """「保管庫に移す」(確認の後)。保管庫の default に保存 → 読み直して同じ値か確かめる → api_key の行だけを消す。

    どこかで失敗したら設定ファイルは変えない(キーを失わない)。保管庫に保存できていれば、そのままにする。
    保管庫に別のキーがあり、overwrite が無ければ何もせずに needs_overwrite を返す。
    """
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    store = store_name(sys.platform if platform is None else platform)
    try:
        found = read_api_key(path)
    except SettingsError as exc:
        return ConfigAction(False, config_not_saved(str(exc)), reload=True)
    if found.digest != expected_digest:
        return ConfigAction(False, migrate_file_changed(), reload=True)
    if found.key is None:
        return ConfigAction(False, migrate_no_key(), reload=True)
    if (problem := _migrate_problem()) is not None:
        return ConfigAction(False, problem)
    current = read_key(DEFAULT_PROFILE_ID)
    if current.problem is not None:
        return ConfigAction(False, migrate_save_failed(current.problem))
    if current.key is not None and current.key != found.key and not overwrite:
        return ConfigAction(False, migrate_overwrite_confirm(store), needs_overwrite=True)
    saved = save_key(found.key, profile_id=DEFAULT_PROFILE_ID, platform=platform)
    if not saved.ok:
        return ConfigAction(False, migrate_save_failed(saved.message))
    if read_key(DEFAULT_PROFILE_ID).key != found.key:
        return ConfigAction(False, migrate_mismatch())
    try:
        remove_api_key(path, found.digest)
    except ConfigChanged:
        return ConfigAction(False, migrate_file_changed(), reload=True)
    except SettingsError as exc:
        return ConfigAction(False, migrate_remove_failed(str(exc)), reload=True)
    return ConfigAction(True, migrate_done(store), reload=True)


# --- AI 接続テスト(REQ-35 §3。送るのはワーカー。ここは準備と文面だけ) ---


@dataclass(frozen=True)
class ConnectionCheckStart:
    """「AI 接続テスト」を押したときの準備。送れるなら plan と確認の文、送らないなら refused に理由の文。"""

    config_path: Path
    plan: ConnectionCheckPlan | None = None
    confirm: str = ""
    refused: str | None = None
    # 使うプロファイルの名前(プロファイルのファイルが無ければ None。自動送信の確かめに出す。ADR-0080 決定2)
    profile_name: str | None = None


def _check_settings(
    profile_id: str | None,
    *,
    cwd: Path | None,
    env: Mapping[str, str] | None,
    platform: str | None,
    home: Path | None,
) -> LlmSettings:
    """接続テストに使う設定。profile_id を省くと使用中のもの。環境変数はそのプロファイルの変数だけ(load_settings の決まり)。"""
    profiles = load_profiles(profiles_path(platform=platform, env=env, home=home)) if profile_id is not None else None
    if profiles is None:
        # ファイルが無いときは id="default" の1件だけ(今と同じ読み方)
        return load_settings(cwd=cwd, env=env, platform=platform, home=home, read_prompt=False)
    profile = profiles.get(profile_id)
    if profile is None:
        raise _no_profile(profiles_path(platform=platform, env=env, home=home))
    return _load_profile(profile, cwd=cwd, env=env, platform=platform, home=home)


def connection_check_start(
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
    client_factory: Callable[..., LLMClient] | None = None,
    profile_id: str | None = None,
) -> ConnectionCheckStart:
    """設定を読み直して送る準備をする。設定を読めない・キーやモデルが無いときは送らない(理由の文を返す)。

    profile_id は一覧で選んだプロファイル(省くと使用中のもの)。
    """
    path = user_config_path(platform=platform, env=env, home=home).absolute()
    try:
        settings = _check_settings(profile_id, cwd=cwd, env=env, platform=platform, home=home)
    except SettingsError as exc:
        return ConnectionCheckStart(path, refused=llm_check_unreadable(str(exc)))
    plan = prepare_connection_check(settings, client_factory=client_factory)
    if isinstance(plan, ConnectionCheckResult):
        if plan.kind == "no_model" and settings.profile_id is not None:
            # プロファイルを使っているときは、設定ファイルではなく「モデル」の欄を案内する
            return ConnectionCheckStart(path, refused=llm_check_no_model_profile())
        return ConnectionCheckStart(path, refused=llm_check_result(plan, config_path=path))
    store = store_name(sys.platform if platform is None else platform)
    source = llm_check_key_source(plan.key_source, store, plan.key_env_name)
    confirm = "\n".join(
        llm_check_confirm_lines(plan.base_url, plan.model, plan.prompt, source, default_base_url=plan.default_base_url)
    )
    return ConnectionCheckStart(path, plan=plan, confirm=confirm, profile_name=settings.profile_name)


def connection_check_outcome(
    result: ConnectionCheckResult, config_path: Path, *, cli: bool = False, now: datetime | None = None
) -> tuple[str, bool]:
    """結果の行に出す文(時:分を添える)と、成功か(色を変えるかどうか)。cli は CLI の形式か(CLI 向けの文にする)。"""
    text = llm_check_result(result, config_path=config_path, cli=cli)
    time = (datetime.now() if now is None else now).strftime("%H:%M")
    return llm_check_outcome_line(result, text, time), result.ok


# --- 単純比較の既定(raw-compare.json。ADR-0166 決定10 / REQ-227 §12) ---
# 読み書きは raw_settings の関数だけを通す。config.toml は書かない(ほかのタブの「保存」とは別)


@dataclass(frozen=True)
class RawDefaultsPane:
    """設定の窓の「単純比較」のタブ。values は欄の値、error は読めないときの帯の文(欄はすべてオフで出す)。"""

    path: Path
    values: dict[str, Any]
    error: str | None
    examples: dict[str, list[dict[str, str]]]


def raw_defaults_pane(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> RawDefaultsPane:
    """既定のファイルを読んで、タブの欄の値を作る。読めなければ欄を既定で出し、帯の文を返す。"""
    from driftpilot.screen_model.model import raw_examples, raw_form_defaults, raw_form_values

    options, context_lines, error = raw_form_defaults(platform=platform, env=env, home=home)
    path = raw_settings_path(platform=platform, env=env, home=home).absolute()
    return RawDefaultsPane(path, raw_form_values(options, context_lines), error, raw_examples())


@dataclass(frozen=True)
class RawDefaultsSaved:
    """「保存」の結果。problems があれば書いていない(欄の誤り)。error は書けなかったときの文。notice は書けたときの文。"""

    problems: list[str]
    error: str | None = None
    notice: str | None = None


def save_raw_defaults_form(
    values: object, *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> RawDefaultsSaved:
    """欄の値を読んで raw-compare.json だけに書く。形が違えば ValueError(入口は _BadRequest)。誤りがあれば書かない。"""
    from driftpilot.screen_model.model import read_raw_form

    form = read_raw_form(values)
    if form.options is None:
        return RawDefaultsSaved(form.problems)
    path = raw_settings_path(platform=platform, env=env, home=home).absolute()
    try:
        _save_raw_defaults(path, form.options, form.context_lines)
    except SettingsError as exc:
        return RawDefaultsSaved([], error=str(exc))
    return RawDefaultsSaved([], notice=raw_defaults_saved(path))
