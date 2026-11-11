"""LLM の設定と API キーの読み込み(入口用。2026-10-16 承認の「API キーの置き場所」)。

core はこのモジュールを読まない(ADR-0015 決定5)。入口(CLI / GUI)が読んで client を組み立て、core に渡す。

キーを読む場所は次の3つだけで、この順に優先する(ADR-0033 決定3)。最初に見つかったものを使い、3つの場所それぞれの有無も持つ。

1. 環境変数(空でなければ)。プロファイルがあるときは DRIFTPILOT_LLM_API_KEY_<env_suffix>、無いときは DRIFTPILOT_LLM_API_KEY
2. OS の保管庫(key_store。使えないときは理由を持って次へ進む。比較は止めない)
3. 利用者ごとの設定ファイル(OS 既定の場所)の llm.api_key(移行のためだけに読む。平文の印を立てる)

作業フォルダの ./driftpilot.toml からは model / base_url / extra_secret_words だけを読み、api_key は読まない
(書いてあっても効かない。キーを git にコミットする経路を設計として無くすため)。
キーは結果・ログ・送信前のプレビューに出さない。表示が要るときは display_key で末尾4文字だけにする。

AI 解析の送る件数の上限(max_diffs・max_total_diffs)は、コマンドの指定 ＞ 利用者ごとの設定ファイル ＞ 既定 の順(ADR-0071 決定5)。
設定ファイルの値は load_settings が読み、コマンドの指定は入口が with_cli_limits で重ねる。作業フォルダの上限は読まない
(書いてあれば ignored_workdir_limits に項目名を持ち、入口が注意を1行出す)。

AI への指示文のひな形(prompt_template。ADR-0075・ADR-0079)は、指示文のプロファイル(prompt-profiles.json。
prompt_profiles.py)から読む。使うのは使用中のプロファイル(prompt_profile の名前があればそれ。無い名前なら
PromptProfileNotFound で止め、別の文に切り替えない)。「既定」なら prompt_template は None(既定の文)。
利用者ごとの設定ファイルの [llm].prompt_template は読まず(ADR-0079 決定5)、書いてあれば ignored_user_prompt_template を立てる
(入口が注意を1行出す)。作業フォルダの値も読まず、上限と同じく ignored_workdir_limits に項目名を持つ。
build_request が LlmRequest に入れるので、送る前の確認(dry_run)・送る文・一括比較は同じひな形の文になる。接続テストの文は変えない。
設定画面と接続テストは read_prompt=False で読む(指示文のファイルが壊れていても、AI の接続のプロファイルを編集・確かめられる)。

AI に毎回付ける前提の文(ADR-0172。premise.py の llm-premise.txt)も、read_prompt=True のときだけ読む(LlmSettings.premise)。
読めなければ SettingsError で、指示文のファイルが読めないときと同じに AI に送る前で止まる。build_request がどの要求にも入れる
(送らない確認と送る文が同じ前提になる)。秘密の語に当たる前提は、入口が premise_problem で送る前に止め、core も確かめる。
接続テストと一括比較の「AI に案を聞く…」は前提を使わない(決まった文で送る)。作業フォルダーの設定ファイルからは読まない。

比較の画面の自動の比較([gui] auto_compare。ADR-0076 決定3)も、利用者ごとの設定ファイルから読む(auto_compare_enabled)。
[llm] とは別に読み、形が違う・読めないときは偽にする(比較の画面は始めない)。
自動送信([gui] auto_send_llm。ADR-0080 決定2)も同じ読み方で、書かれていないときも偽(auto_send_llm_enabled)。
読むのは比較の画面(gui_web/compare_api.py。結果の後に確認なしで送るか)と、pywebview の一括比較の画面(gui_web/batch_api.py。
実行の後に送る内容の確認のリストを開くか。「送る」を押すまで送らない。REQ-213)だけ。CLI・wx の画面は読まない。
作業フォルダの設定ファイルからは読まない。

このモジュールが書くのは、利用者ごとの設定ファイルが無いときの雛形(write_template)だけ。上書きはしない。
設定画面がファイルの決まった行を書き換えるのは user_config_writer(ADR-0071 決定3)。

AI の接続のプロファイル(llm-profiles.json。llm_profiles.py)があるときは、モデル・接続先・API の形式を
使うプロファイル(--llm-profile があればそれ)から取り、作業フォルダの model・base_url は使わない(ADR-0035 決定3)。
キーは 環境変数 DRIFTPILOT_LLM_API_KEY_<env_suffix> → 保管庫 DriftPilot/LLM/<id>/APIKey →
(id が default のときだけ)config.toml の llm.api_key。使用中かどうかでは変えない(ADR-0077 決定2)。
env_suffix が無いプロファイルは環境変数を読まない。環境変数の名前を作るのは env_key_name だけで、頭の部分は固定する
(任意の名前を読めると、ほかの秘密の値を AI の接続先へ送れてしまうため。ADR-0077 決定1)。
ファイルがあるときは共通の DRIFTPILOT_LLM_API_KEY を読まない。設定されていれば common_env_ignored を立てる
(入口が注意の1行を出す。ADR-0077 決定3)。ファイルが無いときだけ、今までどおり共通の変数を読む。

custom-http-json のプロファイル(REQ-101)は、定義の URL を接続先(base_url)として読む。送る前の確認(接続テスト・
一括比較の要約)は base_url を出すので、確認の作りを変えずに実際の送り先が出る。認証が none のときだけキーが要らない。

CLI の2形式(codex-cli・claude-code-cli。REQ-102)はキーを持たない。保管庫も環境変数のキーも読まず、CLI 自身のログインを使う。
CLI に渡す環境からは、頭が DRIFTPILOT_LLM_API_KEY の変数をすべて除く(ADR-0077 決定4。cli_env)。
接続先(base_url)には CLI の表示の文(messages.cli_destination)を入れる(custom-http-json と同じやり方で、送る前の確認に
実際の送り先が出る。前の形式の接続先は使わない)。モデルが空でも送る(--model を付けず、CLI の既定のモデル)。
"""

import dataclasses
import functools
import os
import re
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 保存・削除などは設定画面がここから呼ぶ(gui が読むのは llm_settings まで。gui の層の境界)
from driftpilot.core.batch_llm import DEFAULT_MAX_TOTAL_DIFFS
from driftpilot.key_store import (  # noqa: F401
    DEFAULT_PROFILE_ID,
    SERVICE as KEY_STORE_SERVICE,
    StoredKey,
    StoreResult,
    delete_key,
    read_key,
    save_key,
    service_name,
    store_name,
    store_problem,
)
from driftpilot.llm_analyzer import (
    CONNECTION_CHECK_PROMPT,
    DEFAULT_MAX_DIFFS,
    ConnectionCheckResult,
    LLMClient,
    LLMClientUnavailable,
    LlmRequest,
)
from driftpilot.llm_analyzer.anthropic_client import DEFAULT_ANTHROPIC_BASE_URL
from driftpilot.llm_analyzer.cli_client import (
    CLAUDE_CODE_CLI,  # noqa: F401 - 設定画面がここから読む
    CLI_DEFAULT_MODEL,
    CLI_EFFORT_TIMEOUT_SECONDS,
    CLI_FORMATS,
    CODEX_CLI,  # noqa: F401 - 設定画面がここから読む
    DEFAULT_CLI_TIMEOUT_SECONDS,
)
from driftpilot.llm_analyzer.custom_http import CUSTOM_AUTH_NONE, CustomHttpJson
from driftpilot.llm_analyzer.look_rules import LookRulesClient
from driftpilot.llm_analyzer.openai_client import DEFAULT_BASE_URL  # noqa: F401 - 設定画面の「接続先(既定)」。openai は読まない
from driftpilot.llm_analyzer.wire import AUTH_API_KEY, AUTH_BEARER, DEFAULT_WIRE, WireOptions
from driftpilot.messages import (
    CONFIG_TEMPLATE_NOTE,
    cli_destination,
    llm_profile_not_found,
    llm_profiles_missing,
    prompt_profile_not_found,
)

ENV_API_KEY = "DRIFTPILOT_LLM_API_KEY"
# プロファイルごとの環境変数の名前の、固定の頭の部分(ADR-0077 決定1)。続きは英大文字・数字・_ の 1〜40 文字
ENV_KEY_PREFIX = f"{ENV_API_KEY}_"
ENV_SUFFIX_MAX = 40
_ENV_SUFFIX = re.compile(rf"[A-Z0-9_]{{1,{ENV_SUFFIX_MAX}}}")
# 保存するときに受け付ける形(大文字にする前に確かめる。ı・ß などは大文字にすると I・SS に化けるため)
_ENV_SUFFIX_INPUT = re.compile(rf"[A-Za-z0-9_]{{1,{ENV_SUFFIX_MAX}}}")
APP_DIR = "DriftPilot"
CONFIG_NAME = "config.toml"
WORKDIR_CONFIG = "driftpilot.toml"
SECTION = "llm"
# AI 解析の送る件数の上限の項目(ADR-0071 決定5)
LIMIT_KEYS = ("max_diffs", "max_total_diffs")
# AI への指示文のひな形の項目(ADR-0075 決定3)。今は読まない(ADR-0079 決定5。書いてあれば注意を出すために名前を持つ)
PROMPT_TEMPLATE_KEY = "prompt_template"
# 作業フォルダの設定ファイルから読まない項目(書いてあれば ignored_workdir_limits に名前を持つ)
USER_ONLY_KEYS = (*LIMIT_KEYS, PROMPT_TEMPLATE_KEY)
# 画面の動きの表と、比較の画面の自動の比較の項目(ADR-0076 決定3。真偽値。書かれていなければ真)
GUI_SECTION = "gui"
AUTO_COMPARE_KEY = "auto_compare"
# 比較の画面で、比較が終わったら確認なしで AI に送るか(ADR-0080 決定2。真偽値。書かれていなければ偽)
AUTO_SEND_LLM_KEY = "auto_send_llm"
# 窓にキャラクター(Mirarin)の絵を出すか(ADR-0174 決定1。真偽値。書かれていなければ偽)
CHARACTER_KEY = "character"
# 起動したときに新しい版を確かめるか(ADR-0188 決定2。真偽値。書かれていなければ偽。読めなければ偽)
AUTO_UPDATE_CHECK_KEY = "auto_update_check"
# 画面の色(ADR-0189。"system"・"light"・"dark"。書かれていない・ほかの値・文字でないときは "system")
THEME_KEY = "theme"
THEME_SYSTEM = "system"
THEME_LIGHT = "light"
THEME_DARK = "dark"
THEMES = (THEME_SYSTEM, THEME_LIGHT, THEME_DARK)

# 利用者ごとの設定ファイルの雛形(REQ-27 §B-4。2行目と上限の行は REQ-93 で直した)。値の行はすべてコメントのまま
CONFIG_TEMPLATE = f"""\
# DriftPilot の設定ファイル
# {CONFIG_TEMPLATE_NOTE}

[llm]
# AI に差分を読ませるときに使うモデルと接続先
# model = "gpt-4o-mini"
# base_url = "https://api.openai.com/v1"

# API キーは保管庫か環境変数のほうが安全です。
# プロファイルを使うときは DRIFTPILOT_LLM_API_KEY_<続き>、使わないときは DRIFTPILOT_LLM_API_KEY
# このファイルに書くこともできますが、ファイルの権限に注意してください。
# api_key = "sk-..."

# 伏せる語を足す（この語を含む行は AI に送る前に伏せます）
# extra_secret_words = ["site-token", "ap-psk"]

# AI に送る差分の件数の上限（1組あたり・全体。1 以上の整数。書かなければ既定の値を使います）
# max_diffs = {DEFAULT_MAX_DIFFS}
# max_total_diffs = {DEFAULT_MAX_TOTAL_DIFFS}

# プロンプトは、設定画面の「プロンプト」の枠でプロファイルとして編集できます。差分を入れる印 {{{{diff}}}} が要ります
# 比較の画面で、ファイルが2つそろったら自動で比較を始めるか（[gui] の auto_compare。書かなければ始めます）は、設定画面の「設定ファイル」の枠で変えられます
# 画面の色（[gui] の theme。"system"・"light"・"dark"。書かなければ "system"）は、設定画面の「表示」のタブで変えられます
# 起動したときに新しい版を確かめるか（[gui] の auto_update_check。書かなければ確かめません）は、設定画面の「更新」のタブで変えられます
"""


CHAT_COMPLETIONS = "chat-completions"
RESPONSES = "responses"
ANTHROPIC_MESSAGES = "anthropic-messages"
CUSTOM_HTTP_JSON = "custom-http-json"
# 接続テストの確認に出す、キーを使わないときの読み元(custom-http-json で認証が none)
KEY_SOURCE_NONE = "none"
# 接続テストの確認に出す、CLI の形式の読み元(キーを使わず CLI のログインを使う。REQ-102)
KEY_SOURCE_CLI = "cli"
# CLI の形式で、CLI に渡さないキーの環境変数(REQ-102 §2)。頭が ENV_API_KEY の変数も渡さない(ADR-0077 決定4。cli_env)
CLI_KEY_ENV_NAMES = (ENV_API_KEY, "OPENAI_API_KEY", "ANTHROPIC_API_KEY")
# 接続先に残っていたら送らない、書き換えるための目印の文字(Azure OpenAI のひな形の <リソース名>。REQ-100 §5)
PLACEHOLDER_MARK = "<"


class SettingsError(Exception):
    """設定ファイルが読めない・形が違う。文面に値は含めない(場所と項目名だけ)。"""


class ProfileNotFound(SettingsError):
    """--llm-profile の名前のプロファイルが無い(ファイルが無いときも)。文面はプロファイルの名前の一覧だけ。"""


class PromptProfileNotFound(SettingsError):
    """--llm-prompt-profile の名前の指示文のプロファイルが無い。文面は指示文の名前の一覧だけ(本文は入れない)。"""


@dataclass(frozen=True)
class LlmSettings:
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)  # repr に出さない
    api_key_source: str | None = None  # "env" / "keyring" / "user_config"
    extra_secret_words: tuple[str, ...] = ()
    # 3つの読み元それぞれにキーがあったか(値は持たない。使っていない場所も見せるため。REQ-34 §2)
    env_key_present: bool = False
    keyring_key_present: bool = False
    user_config_key_present: bool = False
    # 保管庫を使えない・読めなかった理由(値は含まない)。使えたら None
    keyring_problem: str | None = None
    # 利用者ごとの設定ファイルに平文のキーがある(使っていなくても立てる。設定画面の黄色の帯)
    plaintext_key_in_user_config: bool = False
    user_config: Path | None = None
    # 作業フォルダの設定ファイルに api_key が書いてあった(読んでいない)
    ignored_workdir_api_key: bool = False
    # model / base_url を読んだ場所("workdir" / "user_config" / "profile")。値が無ければ None(設定画面の表示用)
    model_source: str | None = None
    base_url_source: str | None = None
    workdir_config: Path | None = None
    # 使っている AI の接続のプロファイル(llm-profiles.json が無ければ None)と API の形式
    profile_id: str | None = None
    profile_name: str | None = None
    api_format: str = CHAT_COMPLETIONS
    # 認証の見出しと送り方の細かい値(プロファイルから。無ければ既定値で、今と同じ送信。REQ-100 §3・§4)
    auth_header: str = AUTH_BEARER
    wire: WireOptions = DEFAULT_WIRE
    # custom-http-json の定義(REQ-101。ほかの形式では None)。base_url には定義の URL が入る
    custom: CustomHttpJson | None = None
    # CLI の2形式の CLI の場所(None は PATH から探す)と時間の上限の秒(REQ-102 §4。ほかの形式では使わない)
    cli_path: str | None = None
    timeout_seconds: int = DEFAULT_CLI_TIMEOUT_SECONDS
    # CLI の2形式の Thinking の深さ(ADR-0157 決定10。None は指定を付けない)。時間の上限が書かれていなければ 300 秒にする
    cli_effort: str | None = None
    # プロファイルを使っていて、作業フォルダの設定ファイルに model か base_url があった(使っていない)
    ignored_workdir_model: bool = False
    # このキーのために読む環境変数の名前(ADR-0077 決定1)。読まないとき(続きが無い・CLI の形式)は None
    env_key_name: str | None = None
    # プロファイルがあり、共通の DRIFTPILOT_LLM_API_KEY が設定されている(読んでいない。ADR-0077 決定3)
    common_env_ignored: bool = False
    # AI 解析の送る件数の上限と、どこから来た値か("default" / "user_config" / "cli")。ADR-0071 決定5
    max_diffs: int = DEFAULT_MAX_DIFFS
    max_diffs_source: str = "default"
    max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS
    max_total_diffs_source: str = "default"
    # 作業フォルダの設定ファイルに書いてあった上限・指示文のひな形の項目名(読んでいない)
    ignored_workdir_limits: tuple[str, ...] = ()
    # AI への指示文のひな形(使う指示文のプロファイルの本文。「既定」なら None で既定の文。ADR-0075・ADR-0079)
    prompt_template: str | None = None
    # 使う指示文のプロファイルの id と名前(read_prompt=False で読んだときは None)
    prompt_profile_id: str | None = None
    prompt_profile_name: str | None = None
    # 名前で指定した指示文が、最初から入っている指示文と利用者の指示文の両方に当たり、最初から入っているほうを使った(REQ-204)
    prompt_name_shadowed: bool = False
    # 利用者ごとの設定ファイルに [llm].prompt_template が書いてあった(読んでいない。ADR-0079 決定5)
    ignored_user_prompt_template: bool = False
    # 作業フォルダの設定ファイルの伏せる語(extra_secret_words には足し合わせ済み。設定画面で別に見せる)
    workdir_secret_words: tuple[str, ...] = ()
    # AI に毎回付ける前提の文(llm-premise.txt。ADR-0172)。無い・read_prompt=False で読んだときは None。repr には出さない
    premise: str | None = field(default=None, repr=False)


def is_env_suffix(value: object) -> bool:
    """ファイルに書ける環境変数の名前の続きか(英大文字・数字・_ の 1〜40 文字。ADR-0077 決定1)。"""
    return isinstance(value, str) and _ENV_SUFFIX.fullmatch(value) is not None


def env_suffix_of(text: str) -> str | None:
    """保存するときの続き。前後の空白を除いて空なら None。英数字と _ の 1〜40 文字なら大文字にして返す。

    形が違えば ValueError(値は入れない)。大文字にする前に確かめる(ı・ß などが I・SS に化けて通らないように)。
    """
    value = text.strip()
    if not value:
        return None
    if _ENV_SUFFIX_INPUT.fullmatch(value) is None:
        raise ValueError("env_suffix")
    return value.upper()


def env_key_name(suffix: str) -> str:
    """プロファイルのキーを読む環境変数の名前(DRIFTPILOT_LLM_API_KEY_<続き>)。名前を作るのはここだけ。

    頭の部分は固定で、続きが決まった形でなければ ValueError(任意の環境変数を読む道を作らない。ADR-0077 決定1)。
    """
    if not is_env_suffix(suffix):
        raise ValueError("env_suffix")
    return ENV_KEY_PREFIX + suffix


PORTABLE_DIR = "settings"


def _runtime() -> tuple[bool, str, str]:
    """動いている形 (PyInstaller で作った exe か, 実行ファイルのパス, OS の名前)。

    sys.frozen・sys.executable・sys.platform を読むのはここだけ(ADR-0186。テストはこの関数を差し替える)。
    """
    return bool(getattr(sys, "frozen", False)), sys.executable, sys.platform


def portable_settings_dir(*, platform: str | None = None) -> Path | None:
    """Windows の exe で動いているときの設定の置き場所(exe と同じフォルダーの settings。ADR-0186 決定1)。

    PyInstaller で作った exe(sys.frozen が真)で、かつ OS が Windows のときだけ返す。ほかは None
    (開発の環境・Mac の .app・Linux は今のまま)。platform を渡したときは、そちらを OS の名前として使う。
    """
    frozen, executable, current = _runtime()
    platform = current if platform is None else platform
    if not (frozen and platform.startswith("win")):
        return None
    return Path(executable).resolve().parent / PORTABLE_DIR


def user_config_path(*, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """利用者ごとの設定ファイルの場所。Windows は %APPDATA%、macOS は Application Support、それ以外は XDG。

    Windows の exe で動いているときは、exe と同じフォルダーの settings の中(portable_settings_dir。ADR-0186 決定1)。
    そのときは %APPDATA% も home も読まない。
    """
    platform = _runtime()[2] if platform is None else platform
    portable = portable_settings_dir(platform=platform)
    if portable is not None:
        return portable / CONFIG_NAME
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if platform.startswith("win"):
        base = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming"
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    return base / APP_DIR / CONFIG_NAME


def read_bytes(path: Path) -> bytes | None:
    """設定ファイルのバイト列。無ければ None。読めなければ SettingsError(場所と理由だけ)。"""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsError(f"{path}: 読めません ({exc.strerror or type(exc).__name__})") from None


def parse_section(data: bytes | None, path: Path) -> dict[str, Any]:
    """バイト列から [llm] の表を取り出す(無ければ空)。TOML として読めない・表でなければ SettingsError。"""
    if data is None:
        return {}
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        raise SettingsError(f"{path}: TOML として読めません") from None
    section = parsed.get(SECTION, {})
    if not isinstance(section, dict):
        raise SettingsError(f"{path}: [{SECTION}] は表にしてください")
    return section


def _read_section(path: Path) -> dict[str, Any]:
    return parse_section(read_bytes(path), path)


def _string(section: dict[str, Any], key: str, path: Path) -> str | None:
    value = section.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"{path}: {SECTION}.{key} は空でない文字列にしてください")
    return value.strip()


def _pick(
    work: dict[str, Any], user: dict[str, Any], key: str, work_path: Path, user_path: Path
) -> tuple[str | None, str | None]:
    """作業フォルダの値を優先する。値と、読んだファイルの区別を返す。"""
    value = _string(work, key, work_path)
    if value is not None:
        return value, "workdir"
    value = _string(user, key, user_path)
    return value, ("user_config" if value is not None else None)


def load_model_and_base_url(
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> tuple[str | None, str | None]:
    """llm-profiles.json が無いときの今の設定の (model, base_url)。load_settings と同じく作業フォルダの値を優先する。

    config.toml と作業フォルダの設定だけを読む。保管庫・キーの環境変数・指示文のファイルは読まない(プロファイルの
    ファイルの取り込みで1件目を作るときに使う。ADR-0082 決定2・ADR-0184 決定1)。読めない・形が違うときは SettingsError。
    """
    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd
    user_path = user_config_path(platform=platform, env=env, home=home)
    work_path = cwd / WORKDIR_CONFIG
    user = _read_section(user_path)
    work = _read_section(work_path)
    model, _ = _pick(work, user, "model", work_path, user_path)
    base_url, _ = _pick(work, user, "base_url", work_path, user_path)
    return model, base_url


def is_secret_word(word: object) -> bool:
    """伏せる語にできるか(空でなく、空白を含まない)。読み込みと設定画面の保存で同じ決まりを使う。"""
    return isinstance(word, str) and bool(word) and len(word.split()) == 1 and word == word.strip()


def words_of(section: Mapping[str, Any], path: Path) -> tuple[str, ...]:
    value = section.get("extra_secret_words", [])
    if not isinstance(value, list) or not all(is_secret_word(w) for w in value):
        raise SettingsError(f"{path}: {SECTION}.extra_secret_words は、空白を含まない語のリストにしてください")
    return tuple(value)


def is_limit(value: object) -> bool:
    """上限にできる値か(真偽値でない 1 以上の整数。ADR-0071 決定5)。"""
    return type(value) is int and value >= 1


def limit_of(section: Mapping[str, Any], key: str, path: Path) -> int | None:
    """[llm] の上限(max_diffs・max_total_diffs)。無ければ None。範囲外・数でなければ SettingsError(値は入れない)。"""
    value = section.get(key)
    if value is None:
        return None
    if not is_limit(value):
        raise SettingsError(f"{path}: {SECTION}.{key} は 1 以上の整数にしてください")
    return value


def _gui_flag(data: bytes | None, key: str, default: bool) -> bool:
    """設定ファイルのバイト列から [gui] の真偽値の項目を読む。

    ファイルが無い・[gui] が無い・項目が無いときは default。TOML として読めない・[gui] が表でない・
    値が真偽値でないときは偽。[llm] の読み方とは別に読む([llm] の形が違っても関係しない)。
    """
    if data is None:
        return default
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False
    section = parsed.get(GUI_SECTION, {})
    if not isinstance(section, dict):
        return False
    value = section.get(key, default)
    return value if isinstance(value, bool) else False


def auto_compare_of(data: bytes | None) -> bool:
    """設定ファイルのバイト列から [gui] の auto_compare を読む(ADR-0076 決定3)。

    ファイルが無い・[gui] が無い・項目が無いときは真(既定はオン)。TOML として読めない・[gui] が表でない・
    値が真偽値でないときは偽(読めないときは始めない)。[llm] の読み方とは別に読む([llm] の形が違っても関係しない)。
    """
    return _gui_flag(data, AUTO_COMPARE_KEY, True)


def auto_send_llm_of(data: bytes | None) -> bool:
    """設定ファイルのバイト列から [gui] の auto_send_llm を読む(ADR-0080 決定2)。

    真偽値の true のときだけ真。ファイルが無い・項目が無い・TOML として読めない・[gui] が表でない・
    値が真偽値でない("yes" や 1 も)ときは偽(読めないときは送らない)。
    """
    return _gui_flag(data, AUTO_SEND_LLM_KEY, False)


def character_of(data: bytes | None) -> bool:
    """設定ファイルのバイト列から [gui] の character を読む(ADR-0174 決定1)。

    真偽値の true のときだけ真。ファイルが無い・項目が無い・TOML として読めない・[gui] が表でない・
    値が真偽値でない("true" や 1 も)ときは偽(既定はオフ)。
    """
    return _gui_flag(data, CHARACTER_KEY, False)


def character_enabled(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> bool:
    """利用者ごとの設定ファイルの [gui] character(窓を読み込んだときに読む。ADR-0174 決定1)。読めなければ偽。"""
    try:
        data = read_bytes(user_config_path(platform=platform, env=env, home=home))
    except SettingsError:
        return False
    return character_of(data)


def auto_update_check_of(data: bytes | None) -> bool:
    """設定ファイルのバイト列から [gui] の auto_update_check を読む(ADR-0188 決定2。character と同じ読み方)。

    真偽値の true のときだけ真。ファイルが無い・項目が無い・TOML として読めない・[gui] が表でない・
    値が真偽値でないときは偽(既定はオフ。読めないときはつながない)。
    """
    return _gui_flag(data, AUTO_UPDATE_CHECK_KEY, False)


def auto_update_check_enabled(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> bool:
    """利用者ごとの設定ファイルの [gui] auto_update_check(起動したときに 1 回読む。ADR-0188 決定2)。読めなければ偽。"""
    try:
        data = read_bytes(user_config_path(platform=platform, env=env, home=home))
    except SettingsError:
        return False
    return auto_update_check_of(data)


def theme_of(data: bytes | None) -> str:
    """設定ファイルのバイト列から [gui] の theme を読む(ADR-0189)。

    "system"・"light"・"dark" はそのまま。ファイルが無い・[gui] が無い・項目が無い・TOML として読めない・
    [gui] が表でない・値が文字でない(true・1)・3 つ以外の文字("blue")は "system"。
    """
    if data is None:
        return THEME_SYSTEM
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return THEME_SYSTEM
    section = parsed.get(GUI_SECTION, {})
    if not isinstance(section, dict):
        return THEME_SYSTEM
    value = section.get(THEME_KEY, THEME_SYSTEM)
    return value if isinstance(value, str) and value in THEMES else THEME_SYSTEM


def gui_theme(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> str:
    """利用者ごとの設定ファイルの [gui] theme(窓を開くたびに読む。ADR-0189)。読めなければ "system"。"""
    try:
        data = read_bytes(user_config_path(platform=platform, env=env, home=home))
    except SettingsError:
        return THEME_SYSTEM
    return theme_of(data)


def auto_compare_enabled(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> bool:
    """利用者ごとの設定ファイルの [gui] auto_compare(比較の画面が始めるかどうかを決めるたびに読む)。読めなければ偽。"""
    try:
        data = read_bytes(user_config_path(platform=platform, env=env, home=home))
    except SettingsError:
        return False
    return auto_compare_of(data)


def auto_send_llm_enabled(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> bool:
    """利用者ごとの設定ファイルの [gui] auto_send_llm(比較の画面が送るかどうかを決めるたびに読む)。読めなければ偽。

    作業フォルダの設定ファイルは読まない(ADR-0080 決定2)。呼ぶのは比較の画面と、pywebview の一括比較の画面
    (実行の後に送る内容の確認のリストを開くか。確認は飛ばさない。REQ-213)だけ。
    """
    try:
        data = read_bytes(user_config_path(platform=platform, env=env, home=home))
    except SettingsError:
        return False
    return auto_send_llm_of(data)


def auto_send_target(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> str | None:
    """自動送信の送り先の名前(比較の画面の帯に出す)。オフなら None。

    名前は AI の接続のプロファイルの使用中のもの(ファイルが無ければ「既定」)。保管庫は読まない。
    プロファイルのファイルを読めなければ SettingsError(そのときは送らない。load_settings も同じ理由で止まる)。
    """
    from driftpilot.llm_profiles import DEFAULT_PROFILE_NAME, load_profiles, profiles_path  # llm_profiles がこのモジュールを読むため

    if not auto_send_llm_enabled(env=env, platform=platform, home=home):
        return None
    profiles = load_profiles(profiles_path(platform=platform, env=env, home=home))
    if profiles is None:
        return DEFAULT_PROFILE_NAME
    return profiles.active_profile.name


def load_settings(
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
    profile: str | None = None,
    prompt_profile: str | None = None,
    read_prompt: bool = True,
) -> LlmSettings:
    """設定を読む。model と base_url は作業フォルダの値を優先し、追加の秘密語は両方を足し合わせる。

    llm-profiles.json があれば、model・base_url・API の形式は使うプロファイルから取る(profile は表示名。
    大文字・小文字を区別しない。省くと active)。profile を指定してファイルか名前が無ければ ProfileNotFound。
    指示文は prompt-profiles.json の使用中のプロファイル(prompt_profile は表示名。大文字・小文字を区別しない)。
    名前が無ければ PromptProfileNotFound(別の文に切り替えない。ADR-0079 決定2)。read_prompt=False なら指示文のファイルを読まない。
    前提の文(llm-premise.txt。ADR-0172)も read_prompt=True のときだけ読む(読めなければ SettingsError)。
    """
    # llm_profiles・prompt_profiles・premise がこのモジュールを読むため、ここで読む
    from driftpilot.llm_profiles import load_profiles, profiles_path
    from driftpilot.premise import load_premise, premise_path
    from driftpilot.prompt_profiles import BUILTIN_DEFAULT_ID, load_prompt_profiles, prompt_profiles_path

    env = os.environ if env is None else env
    cwd = Path.cwd() if cwd is None else cwd
    user_path = user_config_path(platform=platform, env=env, home=home)
    user = _read_section(user_path)
    work_path = cwd / WORKDIR_CONFIG
    work = _read_section(work_path)
    profiles_file = profiles_path(platform=platform, env=env, home=home)
    profiles = load_profiles(profiles_file)

    if profiles is None:
        if profile is not None:
            raise ProfileNotFound(llm_profiles_missing(profiles_file))
        chosen = None
        model, model_source = _pick(work, user, "model", work_path, user_path)
        base_url, base_url_source = _pick(work, user, "base_url", work_path, user_path)
    else:
        chosen = profiles.active_profile if profile is None else profiles.find(profile)
        if chosen is None:
            raise ProfileNotFound(llm_profile_not_found(profile, profiles.names()))
        # 作業フォルダの model・base_url は使わない(検査もしない)。書いてあれば ignored_workdir_model で知らせる
        model, model_source = chosen.model, ("profile" if chosen.model is not None else None)
        base_url, base_url_source = chosen.base_url, ("profile" if chosen.base_url is not None else None)
        if chosen.api_format == CUSTOM_HTTP_JSON and chosen.custom is not None:
            # 定義の URL に送る。送る前の確認にも、この URL を接続先として出す
            base_url, base_url_source = chosen.custom.url, "profile"
        elif is_cli(chosen.api_format):
            # 送り先は手元の CLI。前の形式の接続先(<リソース名> など)は使わず、CLI の表示を接続先として出す
            base_url, base_url_source = cli_destination(chosen.api_format, chosen.cli_path), "profile"
    custom = chosen.custom if chosen is not None and chosen.api_format == CUSTOM_HTTP_JSON else None
    cli = chosen is not None and is_cli(chosen.api_format)
    words = [*words_of(user, user_path)]
    work_words = words_of(work, work_path)
    words += [w for w in work_words if w not in words]
    # 上限は利用者ごとの設定ファイルからだけ読む(作業フォルダの値は読まず、検査もしない。ADR-0071 決定5)
    limits = {key: limit_of(user, key, user_path) for key in LIMIT_KEYS}
    # 指示文は指示文のプロファイルから読む。config.toml と作業フォルダの prompt_template は読まず、検査もしない(ADR-0079)
    prompt_template: str | None = None
    prompt_chosen = None
    prompt_shadowed = False
    premise: str | None = None
    if read_prompt:
        prompts = load_prompt_profiles(prompt_profiles_path(platform=platform, env=env, home=home))
        prompt_chosen = prompts.active_profile if prompt_profile is None else prompts.find(prompt_profile)
        if prompt_chosen is None:
            raise PromptProfileNotFound(prompt_profile_not_found(prompt_profile, prompts.names()))
        prompt_template = None if prompt_chosen.id == BUILTIN_DEFAULT_ID else prompt_chosen.template
        # 名前で選んだときだけ見る(使用中は id で決まる)。入口が注意を1行出す(REQ-204・判断の記録 2)
        prompt_shadowed = prompt_profile is not None and prompt_chosen.builtin and prompts.shadowed(prompt_profile)
        # 毎回付ける前提の文(ADR-0172 決定1)。env・home を渡さないときは利用者のフォルダー(テストでは一時フォルダー)
        premise = load_premise(premise_path(platform=platform, env=env, home=home))

    profile_id = DEFAULT_PROFILE_ID if chosen is None else chosen.id
    # config.toml の api_key は id が default のときだけ読む(今の利用者の移行のため。新しいプロファイルは保管庫だけ)
    file_key_usable = profile_id == DEFAULT_PROFILE_ID
    # 読む環境変数は、ファイルが無ければ共通の変数、あればそのプロファイルの変数だけ(使用中かどうかでは変えない。ADR-0077)
    if profiles is None:
        env_name: str | None = ENV_API_KEY
    else:
        env_name = None if chosen.env_suffix is None else env_key_name(chosen.env_suffix)
    stored = user.get("api_key")
    if cli:
        # CLI の形式はキーを使わない(保管庫を読まない・環境変数のキーも使わない。CLI のログインを使う)。
        # 平文のキーの印と設定ファイルの形の確かめは、設定ファイルについての事実なので今のまま
        file_key_usable = False
        env_name = None
    env_key = (env.get(env_name, "").strip() or None) if env_name is not None else None
    vault = StoredKey() if cli else read_key(profile_id)
    if file_key_usable and env_key is None and stored is not None and (not isinstance(stored, str) or not stored.strip()):
        # 使う環境変数のキーが無いときに形を確かめる(今までと同じ。保管庫にキーがあっても、壊れた設定ファイルは知らせる)
        raise SettingsError(f"{user_path}: {SECTION}.api_key は空でない文字列にしてください")
    file_key = stored.strip() if isinstance(stored, str) and stored.strip() else None
    usable_file_key = file_key if file_key_usable else None
    key, source = next(
        (
            (k, s)
            for k, s in ((env_key, "env"), (vault.key, "keyring"), (usable_file_key, "user_config"))
            if k is not None
        ),
        (None, None),
    )
    return LlmSettings(
        model=model,
        base_url=base_url,
        api_key=key,
        api_key_source=source,
        extra_secret_words=tuple(words),
        env_key_present=env_key is not None,
        keyring_key_present=vault.key is not None,
        user_config_key_present=usable_file_key is not None,
        keyring_problem=vault.problem,
        plaintext_key_in_user_config=file_key is not None,
        user_config=user_path,
        ignored_workdir_api_key="api_key" in work,
        model_source=model_source,
        base_url_source=base_url_source,
        workdir_config=work_path.absolute(),
        profile_id=None if chosen is None else chosen.id,
        profile_name=None if chosen is None else chosen.name,
        api_format=CHAT_COMPLETIONS if chosen is None else chosen.api_format,
        auth_header=AUTH_BEARER if chosen is None else chosen.auth_header,
        wire=DEFAULT_WIRE if chosen is None else chosen.wire,
        custom=custom,
        cli_path=None if chosen is None else chosen.cli_path,
        timeout_seconds=_cli_timeout(chosen),
        cli_effort=None if chosen is None else chosen.cli_effort,
        ignored_workdir_model=chosen is not None and ("model" in work or "base_url" in work),
        env_key_name=env_name,
        common_env_ignored=profiles is not None and bool(env.get(ENV_API_KEY, "").strip()),
        max_diffs=DEFAULT_MAX_DIFFS if limits["max_diffs"] is None else limits["max_diffs"],
        max_diffs_source="default" if limits["max_diffs"] is None else "user_config",
        max_total_diffs=DEFAULT_MAX_TOTAL_DIFFS if limits["max_total_diffs"] is None else limits["max_total_diffs"],
        max_total_diffs_source="default" if limits["max_total_diffs"] is None else "user_config",
        ignored_workdir_limits=tuple(key for key in USER_ONLY_KEYS if key in work),
        workdir_secret_words=work_words,
        prompt_template=prompt_template,
        prompt_profile_id=None if prompt_chosen is None else prompt_chosen.id,
        prompt_profile_name=None if prompt_chosen is None else prompt_chosen.name,
        prompt_name_shadowed=prompt_shadowed,
        ignored_user_prompt_template=PROMPT_TEMPLATE_KEY in user,
        premise=premise,
    )


def with_cli_limits(settings: LlmSettings, *, max_diffs: int | None = None, max_total_diffs: int | None = None) -> LlmSettings:
    """コマンドの指定(--llm-max-diffs・--llm-max-total-diffs)を重ねる。指定したものだけ出どころを "cli" にする。

    読む順は コマンドの指定 ＞ 利用者ごとの設定ファイル ＞ 既定(ADR-0071 決定5)。値の検査は入口が済ませている。
    """
    changes: dict[str, Any] = {}
    if max_diffs is not None:
        changes.update(max_diffs=max_diffs, max_diffs_source="cli")
    if max_total_diffs is not None:
        changes.update(max_total_diffs=max_total_diffs, max_total_diffs_source="cli")
    return dataclasses.replace(settings, **changes) if changes else settings


def default_base_url(api_format: str = CHAT_COMPLETIONS) -> str:
    """API の形式ごとの既定の接続先(接続先が null のとき。REQ-42 §6)。"""
    return DEFAULT_ANTHROPIC_BASE_URL if api_format == ANTHROPIC_MESSAGES else DEFAULT_BASE_URL


def is_cli(api_format: str) -> bool:
    """手元の CLI を呼ぶ形式(codex-cli・claude-code-cli。REQ-102)か。"""
    return api_format in CLI_FORMATS


def cli_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """CLI に渡す環境変数。今の環境を写し(CLI のログインの情報のため)、キーの環境変数だけを除く(REQ-102 §2)。

    除くのは CLI_KEY_ENV_NAMES と、頭が DRIFTPILOT_LLM_API_KEY の変数のすべて(プロファイルごとの変数。ADR-0077 決定4)。
    名前の大文字・小文字は区別せずに比べる(Windows の環境変数は区別しないため)。
    """
    env = os.environ if env is None else env
    return {
        name: value
        for name, value in env.items()
        if name.upper() not in CLI_KEY_ENV_NAMES and not name.upper().startswith(ENV_API_KEY)
    }


def key_required(settings: LlmSettings) -> bool:
    """送るのにキーが要るか。custom-http-json で認証が none のとき(REQ-101 §2)と、CLI の形式(REQ-102)は要らない。"""
    if is_cli(settings.api_format):
        return False
    custom = settings.custom
    return not (settings.api_format == CUSTOM_HTTP_JSON and custom is not None and custom.auth == CUSTOM_AUTH_NONE)


def display_model(settings: LlmSettings) -> str | None:
    """送る前の確認と結果に出すモデルの名前。CLI の形式でモデルが無いときは「CLI の既定のモデル」。"""
    if settings.model is None and is_cli(settings.api_format):
        return CLI_DEFAULT_MODEL
    return settings.model


def needs_openai(api_format: str, auth_header: str = AUTH_BEARER) -> bool:
    """この接続が openai ライブラリを使うか(REQ-108 §3)。_default_factory が openai_client を選ぶ条件と同じ。

    使うのは OpenAI 互換の2形式(chat-completions・responses)で認証が bearer のときだけ。CLI の形式・custom-http-json・
    anthropic-messages・認証が api-key の接続は、標準ライブラリの HTTP か手元の CLI で送る。
    """
    if is_cli(api_format) or api_format in (CUSTOM_HTTP_JSON, ANTHROPIC_MESSAGES):
        return False
    return auth_header != AUTH_API_KEY


def _cli_timeout(chosen: Any) -> int:
    """CLI の時間の上限。書いてあればその値、無くて cli_effort があれば 300 秒、どちらも無ければ 60 秒(ADR-0157 決定11)。"""
    if chosen is not None and chosen.timeout_seconds is not None:
        return chosen.timeout_seconds
    if chosen is not None and chosen.cli_effort is not None:
        return CLI_EFFORT_TIMEOUT_SECONDS
    return DEFAULT_CLI_TIMEOUT_SECONDS


def _default_factory(
    api_format: str,
    *,
    auth_header: str = AUTH_BEARER,
    wire: WireOptions = DEFAULT_WIRE,
    custom: CustomHttpJson | None = None,
    cli_path: str | None = None,
    timeout_seconds: int = DEFAULT_CLI_TIMEOUT_SECONDS,
    cli_effort: str | None = None,
    **options: Any,
) -> Callable[..., LLMClient]:
    """API の形式と認証の見出しから client の型を選ぶ。テストがモジュールの属性を差し替えるため、呼ぶたびにモジュールから取る。

    - codex-cli・claude-code-cli: CliClient(手元の CLI を呼ぶ。CLI の場所・時間の上限と、キーの環境変数を除いた環境を渡す。
      再試行と送り方の値は無い。REQ-102)
    - anthropic-messages: AnthropicMessagesClient(見出しは x-api-key のまま)
    - custom-http-json: CustomHttpJsonClient(定義 custom どおりに送る。標準ライブラリの HTTP。REQ-101 §3)
    - OpenAI 互換の2形式で api-key: OpenAIApiKeyClient(標準ライブラリの HTTP。REQ-100 §3)
    - chat-completions で bearer: OpenAICompatibleClient、responses で bearer: OpenAIResponsesClient(openai ライブラリ)
    wire は既定値と違うときだけ渡す(既定値なら今と同じ呼び方)。
    """
    if is_cli(api_format):
        from driftpilot.llm_analyzer import cli_client

        cli_options: dict[str, Any] = {} if cli_effort is None else {"effort": cli_effort}  # 値があるときだけ渡す
        return functools.partial(
            cli_client.CliClient,
            kind=api_format,
            env=cli_env(),
            cli_path=cli_path,
            timeout_seconds=timeout_seconds,
            **cli_options,
        )
    if wire != DEFAULT_WIRE:
        options["wire"] = wire
    if api_format == CUSTOM_HTTP_JSON:
        from driftpilot.llm_analyzer import custom_http_client

        options["custom"] = custom
        return functools.partial(custom_http_client.CustomHttpJsonClient, **options)
    if api_format == ANTHROPIC_MESSAGES:
        from driftpilot.llm_analyzer import anthropic_client

        factory: Callable[..., LLMClient] = anthropic_client.AnthropicMessagesClient
    elif auth_header == AUTH_API_KEY:
        from driftpilot.llm_analyzer import openai_http_client

        factory = openai_http_client.OpenAIApiKeyClient
        options["api_format"] = api_format
    else:
        from driftpilot.llm_analyzer import openai_client

        factory = openai_client.OpenAIResponsesClient if api_format == RESPONSES else openai_client.OpenAICompatibleClient
    return functools.partial(factory, **options) if options else factory


def has_placeholder(base_url: str | None) -> bool:
    """接続先に書き換えるための目印(<リソース名> など)が残っているか。残っていれば送らない(REQ-100 §5)。"""
    return base_url is not None and PLACEHOLDER_MARK in base_url


def write_template(path: Path) -> Path:
    """利用者ごとの設定ファイルの雛形を新しく作る。既にあれば書かない(上書きしない)。親フォルダは作る。

    作った後は、設定画面の「保存」「保管庫に移す」が決まった行だけを書き換える(user_config_writer。ADR-0071 決定3)。
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8", newline="\n") as fh:
            fh.write(CONFIG_TEMPLATE)
    except FileExistsError:
        raise SettingsError(f"{path}: 既にあります。上書きしません") from None
    except OSError as exc:
        raise SettingsError(f"{path}: 書けません ({exc.strerror or type(exc).__name__})") from None
    return path


def build_request(
    settings: LlmSettings,
    *,
    dry_run: bool = False,
    max_diffs: int | None = None,
    client_factory: Callable[..., LLMClient] | None = None,
    look_rules: bool = True,
) -> LlmRequest:
    """設定から core に渡す LlmRequest を組み立てる。dry_run では client を作らない(キーも要らない)。

    max_diffs を省くと settings.max_diffs(コマンドの指定 ＞ 設定ファイル ＞ 既定)。
    client を作れないときは、理由を unavailable に入れる(比較は完走し、結果に「未実施(理由)」が残る)。
    指示文のひな形はどの要求にも入れる(dry_run の確認と送る文が同じひな形になる。ADR-0075 決定4)。
    client_factory を省いたときだけ、作った client を LookRulesClient で包み、送る直前に答えの見え方の決まりを足す
    (ADR-0150)。client_factory を渡したとき(テストの偽物、接続テスト)は包まない。
    look_rules が偽のときも包まない(組の案を聞く送信。ADR-0177 決定4)。包むのは両方に当たるときだけ。
    前提の文(settings.premise。ADR-0172)も、dry_run でも送るときでも同じ値を入れる(送らない確認と送る文が同じ前提になる)。
    """
    common = {
        "max_diffs": settings.max_diffs if max_diffs is None else max_diffs,
        "extra_secret_words": settings.extra_secret_words,
        "prompt_template": settings.prompt_template,
        "premise": settings.premise,
    }
    if dry_run:
        return LlmRequest(dry_run=True, **common)
    cli = is_cli(settings.api_format)
    if settings.model is None and not cli:
        # CLI の形式はモデルが無くても送る(--model を付けず、CLI の既定のモデル)
        return LlmRequest(unavailable="not_configured", **common)
    if settings.api_key is None and key_required(settings):
        return LlmRequest(unavailable="no_api_key", **common)
    if not cli and has_placeholder(settings.base_url):
        # 接続先の <リソース名> などが書き換えられていない。送らず、比較は完走する(画面にも CLI にも同じに効く)
        return LlmRequest(unavailable="placeholder_base_url", **common)
    add_look_rules = client_factory is None and look_rules
    if client_factory is None:
        client_factory = _default_factory(
            settings.api_format,
            auth_header=settings.auth_header,
            wire=settings.wire,
            custom=settings.custom,
            cli_path=settings.cli_path,
            timeout_seconds=settings.timeout_seconds,
            cli_effort=settings.cli_effort,
        )
    try:
        client = client_factory(model=settings.model, api_key=settings.api_key, base_url=settings.base_url)
    except LLMClientUnavailable:
        # CLI の形式では、CLI のプログラムが見つからない(作るときに探す)
        return LlmRequest(unavailable="cli_not_found" if cli else "client_unavailable", **common)
    if add_look_rules:
        client = LookRulesClient(client)
    return LlmRequest(client=client, **common)


def premise_problem(settings: LlmSettings) -> str | None:
    """前提の文が送れないときの理由の種類(follow_up.REFUSE_*)。前提が無い・送れるときは None(ADR-0172 決定4)。

    確かめは追加の質問と同じ check_question で、語は送るときと同じ normalize_words(利用者の語)。
    入口(CLI・一括比較の窓)が送る前に止めるのに使う。core(analyze)も同じ確かめをする(入口を通らない呼び方の守り)。
    """
    if settings.premise is None:
        return None
    return premise_text_problem(settings.premise, settings.extra_secret_words)


def current_premise(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> str | None:
    """今の前提の文(llm-premise.txt)を読み直す。無ければ None、読めなければ SettingsError(中身は入れない)。

    窓の送信前の確認で「送る」を押したとき、送る直前に読み直すのに使う(ADR-0172 決定5。確認の後に書き換えた前提では送らない)。
    """
    from driftpilot.premise import load_premise, premise_path  # premise がこのモジュールを読むため

    return load_premise(premise_path(platform=platform, env=env, home=home))


def premise_text_problem(text: str, extra_secret_words: Sequence[str]) -> str | None:
    """前提の文を、追加の質問と同じ check_question で確かめる(語は normalize_words(extra_secret_words))。

    送れるなら None、断るなら理由の種類(follow_up.REFUSE_*。中身は返さない)。入口の premise_problem と、
    設定の窓の「前提を保存」(settings_model.save_premise)が使う。
    """
    from driftpilot.llm_analyzer.follow_up import check_question
    from driftpilot.llm_analyzer.masking import normalize_words

    return check_question(text, normalize_words(extra_secret_words))


# 接続テスト(REQ-35)。build_request の「使えない理由」から、送らなかった分類への写し
_NOT_SENT_KIND = {
    "not_configured": "no_model",
    "no_api_key": "no_key",
    "client_unavailable": "unavailable",
    "placeholder_base_url": "placeholder_base_url",
    "cli_not_found": "cli_not_found",
}


@dataclass(frozen=True)
class ConnectionCheckPlan:
    """接続テストを送る前の確認に出すものと、送る client。キーの値は持たない(client の中にだけある)。

    base_url は設定に無ければ None(既定の接続先。default_base_url が API の形式ごとの既定)。CLI の形式では CLI の表示。
    key_source は "env" / "keyring" / "user_config"。キーを使わないとき(custom-http-json で認証が none)は "none"、
    CLI の形式では "cli"。cli は CLI の形式か(結果の文を CLI 向けにする)。
    key_env_name は key_source が "env" のときに読んだ環境変数の名前(確認に出す。ADR-0077)。ほかは None。
    """

    model: str
    base_url: str | None
    key_source: str
    prompt: str
    client: LLMClient = field(repr=False)
    default_base_url: str = DEFAULT_BASE_URL
    cli: bool = False
    key_env_name: str | None = None


def prepare_connection_check(
    settings: LlmSettings, *, client_factory: Callable[..., LLMClient] | None = None
) -> ConnectionCheckPlan | ConnectionCheckResult:
    """接続テストの準備(REQ-35 §2)。client は比較と同じ build_request で作る(別の読み方を作らない)。

    送れないとき(モデルが無い・キーが無い・llm extra が無い)は、送らなかった分類の結果を返す。
    既定の client は再試行をしない(max_retries=0。ADR-0034 決定2)。比較に使う既定は変えない。
    """
    if client_factory is None:
        client_factory = _default_factory(
            settings.api_format,
            auth_header=settings.auth_header,
            wire=settings.wire,
            custom=settings.custom,
            cli_path=settings.cli_path,
            timeout_seconds=settings.timeout_seconds,
            cli_effort=settings.cli_effort,
            max_retries=0,
        )
    request = build_request(settings, client_factory=client_factory)
    if request.client is None:
        return ConnectionCheckResult(_NOT_SENT_KIND[request.unavailable])
    cli = is_cli(settings.api_format)
    if cli:
        key_source = KEY_SOURCE_CLI
    else:
        key_source = settings.api_key_source if key_required(settings) else KEY_SOURCE_NONE
    return ConnectionCheckPlan(
        model=display_model(settings),
        base_url=settings.base_url,
        key_source=key_source,
        prompt=CONNECTION_CHECK_PROMPT,
        client=request.client,
        default_base_url=default_base_url(settings.api_format),
        cli=cli,
        key_env_name=settings.env_key_name if key_source == "env" else None,
    )


def display_key(key: str | None) -> str:
    """画面に出すときのキー。末尾4文字だけを見せる(`sk-...a3f9`)。短いキーは末尾も見せない。"""
    if not key:
        return "(未設定)"
    prefix = "sk-" if key.startswith("sk-") else ""
    if len(key) < 12:
        return f"{prefix}..."
    return f"{prefix}...{key[-4:]}"
