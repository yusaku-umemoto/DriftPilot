"""AI の接続のプロファイル(llm-profiles.json)の読み書き(ADR-0035 決定1〜3。REQ-42 §2・§3)。

置き場所は利用者ごとの設定ファイル(config.toml)と同じフォルダ。DriftPilot が書く専用のファイルで、
利用者の config.toml は今までどおり書かない(ADR-0029 決定1)。キーはこのファイルに入れない(保管庫。key_store)。

ファイルが無いときは load_profiles が None を返し、設定は今までどおりに読む(ADR-0035 決定3)。
読めない・形が違うときは SettingsError(場所と、どの項目が違うかだけ。値は入れない)。

送り方の細かい値(REQ-100 §3・§4・§7): auth_header・max_output_tokens・wire_names・thinking・thinking_param・query。
欄が無ければ既定値(bearer・無し・空・off・無し・空)として読み、既定値の欄は書かない(既存のファイルの見た目を変えない)。
形式に関係の無い欄も残す(使わないだけ)。予約された名前・キーらしい名前の決まり(wire.wire_problem)と、
接続先に ? # を書かない決まりは、形式に関係なく読むときも書くときも確かめる。

custom-http-json の定義(REQ-101 §2): custom の欄(url・auth・headers・body・answer_path)。custom-http-json では必須で、
ほかの形式でも残す(使わない)。決まり(custom_http.custom_problem)は、形式に関係なく読むときも書くときも確かめる。
無ければ書かない(既存のファイルの見た目を変えない)。定義だけを1つのファイルに書き出す・読み込む関数もここに置く
(write_custom_file・read_custom_file。キーは入れない・読まない)。

CLI の2形式(codex-cli・claude-code-cli。REQ-102 §4): cli_path(CLI のプログラムの場所)・timeout_seconds(時間の上限の秒)。
欄が無ければ既定値(PATH から探す・60 秒)として読み、既定値の欄は書かない。形式を変えても残す(使わない)。
cli_effort(Thinking の深さ。ADR-0157 決定10)は CLI の2形式だけに書ける値で、形式ごとの一覧(CLI_EFFORTS)の中だけを許す。
無ければ書かない。CLI でない形式に書いてある・形式を変えて一覧から外れたときは、読むときも書くときも断る。

環境変数の名前の続き(env_suffix。ADR-0077 決定1): キーを読む環境変数 DRIFTPILOT_LLM_API_KEY_<env_suffix> の続き。
英大文字・数字・_ の 1〜40 文字。読むときに形が違えば SettingsError(小文字も誤り)、書くときは小文字を大文字にする。
2つのプロファイルで同じ値は、読むときも書くときも断る。無ければ書かない。複製では写さない。キーではない(値は書く)。

書く関数は設定画面(REQ-43)が使う。どれもファイルを読み直してから直し、同じフォルダの一時ファイルに書いて
os.replace で置き換える(途中で落ちても壊れたファイルを残さない)。UTF-8・改行 \\n(ADR-0023 決定1)。

まとめたファイルの書き出し・取り込み(profile_bundle。ADR-0082・REQ-119)は、読むときの決まり(profile_from_item)と
送り方の書き方(wire_json)をここから使い、取り込んだプロファイルは add_imported_profiles で1回で足す(キーは無いまま)。
"""

import hashlib
import json
import os
import tempfile
import urllib.parse
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from driftpilot import key_store
from driftpilot.key_store import DEFAULT_PROFILE_ID, StoreResult, is_profile_id
from driftpilot.llm_analyzer.cli_client import CLAUDE_CODE_CLI, CLI_EFFORTS, CODEX_CLI
from driftpilot.llm_analyzer.custom_http import (
    CUSTOM_AUTHS,
    CUSTOM_FIELDS,
    CUSTOM_HTTP_JSON,
    CustomHttpJson,
    CustomProblem,
    custom_problem,
)
from driftpilot.llm_analyzer.wire import (
    AUTH_BEARER,
    AUTH_HEADERS,
    DEFAULT_WIRE,
    MAX_OUTPUT_TOKENS,
    THINKING_OFF,
    THINKING_VALUES,
    ThinkingParam,
    WireOptions,
    parse_json_text,
    wire_problem,
)
from driftpilot.llm_settings import SettingsError, env_suffix_of, is_env_suffix, is_limit, user_config_path
from driftpilot.messages import (
    LLM_DEFAULT_PROFILE_NAME,
    LLMP_CHOICE_SEPARATOR,
    LLMP_CUSTOM_PROBLEMS,
    LLMP_ENV_SUFFIX_RULE,
    LLMP_ERRORS,
    LLMP_URL_CREDENTIALS_RULE,
)

PROFILES_NAME = "llm-profiles.json"
SCHEMA_VERSION = 1
# 1 件目を新しく作るときに書く名前(言語に合わせる。日本語は「既定」・英語は Default)。すでにあるファイルの名前は変えない
DEFAULT_PROFILE_NAME = LLM_DEFAULT_PROFILE_NAME
API_FORMATS = ("chat-completions", "responses", "anthropic-messages", CUSTOM_HTTP_JSON, CODEX_CLI, CLAUDE_CODE_CLI)
# 定義のファイル(書き出し・読み込み)の印の欄と値(REQ-101 §4.3)
CUSTOM_FILE_MARK = "driftpilot_custom_http_json"
CUSTOM_FILE_VERSION = 1

_UNSET: Any = object()  # update_profile で「渡していない」を None と区別する


def _err(key: str, **values: object) -> str:
    """誤りの文(messages.LLMP_ERRORS)の {名前} を埋める。値は文に入れない決まりなので、渡すのは場所と欄の名前だけ。"""
    return LLMP_ERRORS[key].format(**values)


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


def _choices(values: Any) -> str:
    return LLMP_CHOICE_SEPARATOR.join(values)


@dataclass(frozen=True)
class LlmProfile:
    """1つの接続の設定。model が None ならモデル未設定。base_url が None なら形式ごとの既定の接続先。

    auth_header は OpenAI 互換の2形式の認証の見出し(bearer・api-key)。wire は送る中身の差を吸収する値(REQ-100 §4)。
    custom は custom-http-json の定義(REQ-101 §2)。ほかの形式でも残す(使わない)。
    cli_path・timeout_seconds は CLI の2形式の欄(REQ-102 §4)。None は既定(PATH から探す・60 秒)。ほかの形式でも残す。
    env_suffix はキーを読む環境変数の名前の続き(ADR-0077)。None なら環境変数を読まない。
    cli_effort は CLI の2形式の Thinking の深さ(ADR-0157 決定10)。None は指定を付けない(CLI の既定)。
    """

    id: str
    name: str
    api_format: str
    model: str | None = None
    base_url: str | None = None
    auth_header: str = AUTH_BEARER
    wire: WireOptions = DEFAULT_WIRE
    custom: CustomHttpJson | None = None
    cli_path: str | None = None
    timeout_seconds: int | None = None
    env_suffix: str | None = None
    cli_effort: str | None = None


@dataclass(frozen=True)
class LlmProfiles:
    active: str
    profiles: tuple[LlmProfile, ...]

    @property
    def active_profile(self) -> LlmProfile:
        return self.get(self.active)

    def get(self, profile_id: str) -> LlmProfile | None:
        return next((p for p in self.profiles if p.id == profile_id), None)

    def find(self, name: str) -> LlmProfile | None:
        """表示名で探す(前後の空白を除き、大文字・小文字を区別しない)。"""
        key = _name_key(name)
        return next((p for p in self.profiles if _name_key(p.name) == key), None)

    def names(self) -> list[str]:
        return [p.name for p in self.profiles]


def _name_key(name: str) -> str:
    return name.strip().casefold()


def profiles_path(
    *, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """llm-profiles.json の場所(利用者ごとの設定ファイルと同じフォルダ)。"""
    return user_config_path(platform=platform, env=env, home=home).parent / PROFILES_NAME


# --- 読む ---


def has_url_extras(base_url: str) -> bool:
    """接続先に ? か # がある(問い合わせの値は query の欄だけで持つ。REQ-100 §4)。"""
    return "?" in base_url or "#" in base_url


# 接続先に利用者名やパスワードがあるときの文(値は入れない。REQ-199 §2)
URL_CREDENTIALS_RULE = LLMP_URL_CREDENTIALS_RULE


def has_url_credentials(base_url: str) -> bool:
    """接続先の @ の前に利用者名やパスワードがある(REQ-199 §2)。urlsplit が読めない文字列は偽(今の検査に任せる)。"""
    try:
        netloc = urllib.parse.urlsplit(base_url).netloc
    except ValueError:
        return False
    return "@" in netloc


def _problem_text(prefix: str, problem: tuple[str, str]) -> str:
    """wire_problem の結果の文(場所と欄の名前だけ。値は入れない)。prefix は「<場所>: profiles[i].」か「<場所>: 」。"""
    field, kind = problem
    if kind == "reserved":
        return _err("wire_reserved", prefix=prefix, field=field)
    return _err("wire_keylike", prefix=prefix, field=field)


def _check_send_options(base_url: str | None, auth_header: Any, wire: Any, prefix: str) -> None:
    """接続先の ? #・利用者名とパスワード・認証の見出し・送り方の値の決まり(読むときと書くときで同じ)。値は文に入れない。"""
    if base_url is not None and has_url_extras(base_url):
        raise SettingsError(_err("base_url_extras", prefix=prefix))
    if base_url is not None and has_url_credentials(base_url):
        raise SettingsError(f"{prefix}{URL_CREDENTIALS_RULE}")
    if auth_header not in AUTH_HEADERS:
        raise SettingsError(_err("auth_header", prefix=prefix, choices=_choices(AUTH_HEADERS)))
    if not isinstance(wire, WireOptions):
        raise SettingsError(_err("wire_shape", prefix=prefix))
    if wire.max_output_tokens is not None and not is_limit(wire.max_output_tokens):
        raise SettingsError(_err("max_output_tokens", prefix=prefix))
    if wire.max_tokens_name is not None and (not isinstance(wire.max_tokens_name, str) or not wire.max_tokens_name.strip()):
        raise SettingsError(_err("wire_names_name", prefix=prefix))
    if wire.thinking not in THINKING_VALUES:
        raise SettingsError(_err("thinking", prefix=prefix, choices=_choices(THINKING_VALUES)))
    param = wire.thinking_param
    if param is not None and (not isinstance(param, ThinkingParam) or not isinstance(param.name, str) or not param.name.strip()):
        raise SettingsError(_err("thinking_param_name", prefix=prefix))
    if not all(isinstance(k, str) and k.strip() and isinstance(v, str) for k, v in wire.query):
        raise SettingsError(_err("query_shape", prefix=prefix))
    if (problem := wire_problem(wire)) is not None:
        raise SettingsError(_problem_text(prefix, problem))


# custom の決まりに反したときの文(どの欄かだけ。値は入れない)。auth だけは選べる値を差し込むので、使うところで作る
_CUSTOM_PROBLEM_TEXT = LLMP_CUSTOM_PROBLEMS


def custom_problem_text(prefix: str, problem: CustomProblem) -> str:
    """custom_problem の結果の文(値は入れない)。prefix は「<場所>: profiles[i].custom.」などの頭。"""
    if (problem.field, problem.kind) == ("auth", "choice"):
        return prefix + _err("custom_auth_choice", choices=_choices(CUSTOM_AUTHS))
    text = _CUSTOM_PROBLEM_TEXT.get((problem.field, problem.kind))
    return prefix + (text if text is not None else _err("custom_other", field=problem.field))


def _check_custom(api_format: Any, custom: Any, prefix: str) -> None:
    """custom-http-json で定義が無い・定義が決まりに反する(形式に関係なく)ときは SettingsError。値は文に入れない。"""
    if custom is None:
        if api_format == CUSTOM_HTTP_JSON:
            raise SettingsError(_err("custom_missing", prefix=prefix, format_name=CUSTOM_HTTP_JSON))
        return
    if (problem := custom_problem(custom)) is not None:
        raise SettingsError(custom_problem_text(f"{prefix}custom.", problem))


def _check_cli(cli_path: Any, timeout_seconds: Any, prefix: str) -> str | None:
    """CLI の欄の決まり(読むときと書くときで同じ)。場所は前後の空白を除いて返す。値は文に入れない。"""
    if cli_path is not None and (not isinstance(cli_path, str) or not cli_path.strip()):
        raise SettingsError(_err("cli_path", prefix=prefix))
    if timeout_seconds is not None and not is_limit(timeout_seconds):
        raise SettingsError(_err("timeout_seconds", prefix=prefix))
    return cli_path.strip() if cli_path is not None else None


def _check_cli_effort(api_format: Any, cli_effort: Any, prefix: str) -> None:
    """Thinking の深さの決まり(読むときと書くときで同じ)。その形式の CLI_EFFORTS の中だけ。値は文に入れない。"""
    if cli_effort is None:
        return
    choices = CLI_EFFORTS.get(api_format)
    if choices is None:
        raise SettingsError(_err("cli_effort_not_cli", prefix=prefix))
    if not isinstance(cli_effort, str) or cli_effort not in choices:
        raise SettingsError(_err("cli_effort_choice", prefix=prefix, choices=_choices(choices)))


ENV_SUFFIX_RULE = LLMP_ENV_SUFFIX_RULE


def _checked_env_suffix(env_suffix: Any, prefix: str) -> str | None:
    """書くときの環境変数の名前の続き。前後の空白を除いて空なら None、小文字は大文字にする。形が違えば SettingsError(値は入れない)。"""
    if env_suffix is None:
        return None
    try:
        if not isinstance(env_suffix, str):
            raise ValueError("env_suffix")
        return env_suffix_of(env_suffix)
    except ValueError:
        raise SettingsError(f"{prefix}env_suffix{ENV_SUFFIX_RULE}") from None


def _custom_from_json(raw: Any, where: str) -> CustomHttpJson:
    """JSON の表から定義を作る。欄はちょうど url・auth・headers・body・answer_path。形が違えば SettingsError(値は入れない)。"""
    if not isinstance(raw, dict) or set(raw) != set(CUSTOM_FIELDS):
        raise SettingsError(_err("custom_table", where=where, fields=", ".join(CUSTOM_FIELDS)))
    headers = raw["headers"]
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise SettingsError(_err("custom_headers_table", where=where))
    for name in ("url", "auth", "answer_path"):
        if not isinstance(raw[name], str):
            raise SettingsError(_err("custom_string_field", where=where, name=name))
    if not isinstance(raw["body"], dict):
        raise SettingsError(_err("custom_body_object", where=where))
    return CustomHttpJson(
        url=raw["url"], auth=raw["auth"], headers=tuple(headers.items()), body=raw["body"], answer_path=raw["answer_path"]
    )


def _wire_of(item: dict[str, Any], where: str) -> WireOptions:
    """ファイルの欄から送り方の値を作る。欄が無ければ既定値。形が違えば SettingsError(値は入れない)。"""
    tokens = item.get("max_output_tokens")
    if tokens is not None and not is_limit(tokens):
        raise SettingsError(_err("where_max_output_tokens", where=where))
    names = item.get("wire_names", {})
    if (
        not isinstance(names, dict)
        or set(names) - {MAX_OUTPUT_TOKENS}
        or not all(isinstance(v, str) and v.strip() for v in names.values())
    ):
        raise SettingsError(_err("wire_names_shape", where=where, token=MAX_OUTPUT_TOKENS))
    param = item.get("thinking_param")
    if param is not None and (
        not isinstance(param, dict)
        or set(param) != {"name", "on", "off"}
        or not isinstance(param["name"], str)
        or not param["name"].strip()
    ):
        raise SettingsError(_err("thinking_param_shape", where=where))
    query = item.get("query", {})
    if not isinstance(query, dict) or not all(isinstance(k, str) and k.strip() and isinstance(v, str) for k, v in query.items()):
        raise SettingsError(_err("where_query_shape", where=where))
    target = names.get(MAX_OUTPUT_TOKENS)
    return WireOptions(
        max_output_tokens=tokens,
        max_tokens_name=target.strip() if target is not None else None,
        thinking=item.get("thinking", THINKING_OFF),
        thinking_param=None if param is None else ThinkingParam(param["name"].strip(), param["on"], param["off"]),
        query=tuple((k.strip(), v) for k, v in query.items()),
    )


def _profile(item: Any, index: int, path: Path) -> LlmProfile:
    where = f"{path}: profiles[{index}]"
    if not isinstance(item, dict):
        raise SettingsError(_err("where_not_object", where=where))
    if not is_profile_id(item.get("id")):
        raise SettingsError(_err("id_form", where=where))
    return replace(profile_from_item(item, where), id=item["id"])


def profile_from_item(item: Any, where: str) -> LlmProfile:
    """ファイルの1件の欄(id を除く)を、読むときの決まりで確かめてプロファイルにする。id は "" で返す。

    llm-profiles.json の読み込みと、まとめたファイルの取り込み(profile_bundle。REQ-119)で同じ決まりを使う。
    形が違えば SettingsError(where と欄の名前だけ。値は入れない)。
    """
    if not isinstance(item, dict):
        raise SettingsError(_err("where_not_object", where=where))
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise SettingsError(_err("where_name", where=where))
    if item.get("api_format") not in API_FORMATS:
        raise SettingsError(_err("where_api_format", where=where, choices=_choices(API_FORMATS)))
    model = item.get("model")
    if not isinstance(model, str):
        raise SettingsError(_err("where_model", where=where))
    base_url = item.get("base_url")
    if base_url is not None and (not isinstance(base_url, str) or not base_url.strip()):
        raise SettingsError(_err("where_base_url", where=where))
    base_url = base_url.strip() if base_url is not None else None
    auth_header = item.get("auth_header", AUTH_BEARER)
    wire = _wire_of(item, where)
    _check_send_options(base_url, auth_header, wire, f"{where}.")
    custom = _custom_from_json(item["custom"], f"{where}.custom") if "custom" in item else None
    _check_custom(item["api_format"], custom, f"{where}.")
    timeout_seconds = item.get("timeout_seconds")
    cli_path = _check_cli(item.get("cli_path"), timeout_seconds, f"{where}.")
    cli_effort = item.get("cli_effort")
    _check_cli_effort(item["api_format"], cli_effort, f"{where}.")
    env_suffix = item.get("env_suffix")
    if env_suffix is not None and not is_env_suffix(env_suffix):
        # ファイルは DriftPilot が大文字で書く。小文字も誤りとして読まない(値は文に入れない)
        raise SettingsError(f"{where}.env_suffix{ENV_SUFFIX_RULE}")
    return LlmProfile(
        id="",
        name=name.strip(),
        api_format=item["api_format"],
        model=model.strip() or None,  # 空はモデル未設定
        base_url=base_url,
        auth_header=auth_header,
        wire=wire,
        custom=custom,
        cli_path=cli_path,
        timeout_seconds=timeout_seconds,
        env_suffix=env_suffix,
        cli_effort=cli_effort,
    )


def _check_unique(profiles: list[LlmProfile], path: Path) -> None:
    ids: set[str] = set()
    names: set[str] = set()
    suffixes: set[str] = set()
    for index, profile in enumerate(profiles):
        if profile.id in ids:
            raise SettingsError(_err("dup_id", path=path, index=index))
        if _name_key(profile.name) in names:
            raise SettingsError(_err("dup_name", path=path, index=index))
        if profile.env_suffix is not None and profile.env_suffix in suffixes:
            # 同じキーが2つの接続先へ送られないように断る(ADR-0077 決定1)
            raise SettingsError(_err("dup_env_suffix", path=path, index=index))
        ids.add(profile.id)
        names.add(_name_key(profile.name))
        if profile.env_suffix is not None:
            suffixes.add(profile.env_suffix)


def load_profiles(path: Path) -> LlmProfiles | None:
    """llm-profiles.json を読む。無ければ None。読めない・形が違うときは SettingsError(値は入れない)。"""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsError(_err("unreadable", path=path, reason=_reason(exc))) from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise SettingsError(_err("not_utf8", path=path)) from None
    except ValueError:
        raise SettingsError(_err("not_json", path=path)) from None
    if not isinstance(data, dict):
        raise SettingsError(_err("top_not_object", path=path))
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise SettingsError(_err("schema_version", path=path, version=SCHEMA_VERSION))
    items = data.get("profiles")
    if not isinstance(items, list) or not items:
        raise SettingsError(_err("profiles_array", path=path))
    profiles = [_profile(item, i, path) for i, item in enumerate(items)]
    _check_unique(profiles, path)
    active = data.get("active")
    if not isinstance(active, str) or active not in {p.id for p in profiles}:
        raise SettingsError(_err("active_id", path=path))
    return LlmProfiles(active=active, profiles=tuple(profiles))


# --- 書く(設定画面用。REQ-43) ---


def _profile_json(p: LlmProfile) -> dict[str, Any]:
    """1件の JSON。送り方の値は既定値と違う欄だけを書く(既存のファイルの見た目を変えない)。"""
    item: dict[str, Any] = {
        "id": p.id,
        "name": p.name,
        "api_format": p.api_format,
        "model": p.model or "",
        "base_url": p.base_url,
    }
    if p.auth_header != AUTH_BEARER:
        item["auth_header"] = p.auth_header
    item.update(wire_json(p.wire))
    if p.custom is not None:
        item["custom"] = p.custom.to_json()
    if p.cli_path is not None:
        item["cli_path"] = p.cli_path
    if p.timeout_seconds is not None:
        item["timeout_seconds"] = p.timeout_seconds
    if p.env_suffix is not None:
        item["env_suffix"] = p.env_suffix
    if p.cli_effort is not None:
        item["cli_effort"] = p.cli_effort
    return item


def wire_json(wire: WireOptions) -> dict[str, Any]:
    """送り方の値のうち、既定値と違う欄だけの表(max_output_tokens・wire_names・thinking・thinking_param・query)。

    llm-profiles.json と、まとめたファイルの wire の表(profile_bundle。REQ-119)で同じ規則を使う。
    """
    item: dict[str, Any] = {}
    if wire.max_output_tokens is not None:
        item["max_output_tokens"] = wire.max_output_tokens
    if wire.max_tokens_name is not None:
        item["wire_names"] = {MAX_OUTPUT_TOKENS: wire.max_tokens_name}
    if wire.thinking != THINKING_OFF:
        item["thinking"] = wire.thinking
    if wire.thinking_param is not None:
        param = wire.thinking_param
        item["thinking_param"] = {"name": param.name, "on": param.on, "off": param.off}
    if wire.query:
        item["query"] = dict(wire.query)
    return item


def _to_json(profiles: LlmProfiles) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "active": profiles.active,
        "profiles": [_profile_json(p) for p in profiles.profiles],
    }


def _write(path: Path, profiles: LlmProfiles) -> None:
    _write_json(path, _to_json(profiles))


def _write_json(path: Path, data: dict[str, Any]) -> None:
    """同じフォルダの一時ファイルに書いてから os.replace で置き換える。失敗したら一時ファイルを消す(元のファイルは残る)。"""
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as fh:
            tmp = Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        raise SettingsError(_err("unwritable", path=path, reason=_reason(exc))) from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _require(path: Path) -> LlmProfiles:
    profiles = load_profiles(path)
    if profiles is None:
        raise SettingsError(_err("missing_file", path=path))
    return profiles


def _require_profile(profiles: LlmProfiles, profile_id: str, path: Path) -> LlmProfile:
    profile = profiles.get(profile_id) if is_profile_id(profile_id) else None
    if profile is None:
        raise SettingsError(_err("no_such_profile", path=path))
    return profile


def _checked(profile: LlmProfile, others: tuple[LlmProfile, ...], path: Path) -> LlmProfile:
    """書く前の検査。名前は前後の空白を除き、空と重複(大文字・小文字を区別しない)を断る。

    環境変数の名前の続きは大文字にし、ほかのプロファイルと同じ値を断る(ADR-0077 決定1)。
    """
    if not isinstance(profile.name, str) or not profile.name.strip():
        raise SettingsError(_err("name_required", path=path))
    if profile.api_format not in API_FORMATS:
        raise SettingsError(_err("api_format", path=path, choices=_choices(API_FORMATS)))
    if profile.model is not None and not isinstance(profile.model, str):
        raise SettingsError(_err("model_string", path=path))
    if profile.base_url is not None and (not isinstance(profile.base_url, str) or not profile.base_url.strip()):
        raise SettingsError(_err("base_url_form", path=path))
    base_url = profile.base_url.strip() if profile.base_url is not None else None
    _check_send_options(base_url, profile.auth_header, profile.wire, f"{path}: ")
    _check_custom(profile.api_format, profile.custom, f"{path}: ")
    cli_path = _check_cli(profile.cli_path, profile.timeout_seconds, f"{path}: ")
    _check_cli_effort(profile.api_format, profile.cli_effort, f"{path}: ")
    env_suffix = _checked_env_suffix(profile.env_suffix, f"{path}: ")
    name = profile.name.strip()
    if any(_name_key(o.name) == _name_key(name) for o in others if o.id != profile.id):
        raise SettingsError(_err("name_duplicate", path=path))
    if env_suffix is not None and any(o.env_suffix == env_suffix for o in others if o.id != profile.id):
        raise SettingsError(_err("env_suffix_duplicate", path=path))
    return replace(
        profile,
        name=name,
        model=(profile.model or "").strip() or None,
        base_url=base_url,
        cli_path=cli_path,
        env_suffix=env_suffix,
    )


def create_first(path: Path, *, model: str | None, base_url: str | None) -> LlmProfiles:
    """1件目(移行)。今の設定のモデル・接続先で id="default"・名前「既定」を作り、active にする。既にあれば断る。"""
    if path.exists():
        raise SettingsError(_err("already_exists", path=path))
    first = LlmProfile(
        id=DEFAULT_PROFILE_ID, name=DEFAULT_PROFILE_NAME, api_format="chat-completions", model=model, base_url=base_url
    )
    profiles = LlmProfiles(active=DEFAULT_PROFILE_ID, profiles=(_checked(first, (), path),))
    _write(path, profiles)
    return profiles


def add_profile(
    path: Path,
    *,
    name: str,
    api_format: str,
    model: str | None = None,
    base_url: str | None = None,
    auth_header: str = AUTH_BEARER,
    wire: WireOptions = DEFAULT_WIRE,
    custom: CustomHttpJson | None = None,
    cli_path: str | None = None,
    timeout_seconds: int | None = None,
    env_suffix: str | None = None,
    cli_effort: str | None = None,
) -> LlmProfile:
    """プロファイルを足す。id は uuid4().hex(後から変えない)。"""
    profiles = _require(path)
    profile = _checked(
        LlmProfile(
            id=uuid.uuid4().hex,
            name=name,
            api_format=api_format,
            model=model,
            base_url=base_url,
            auth_header=auth_header,
            wire=wire,
            custom=custom,
            cli_path=cli_path,
            timeout_seconds=timeout_seconds,
            env_suffix=env_suffix,
            cli_effort=cli_effort,
        ),
        profiles.profiles,
        path,
    )
    _write(path, replace(profiles, profiles=(*profiles.profiles, profile)))
    return profile


def add_imported_profiles(path: Path, profiles: tuple[LlmProfile, ...]) -> tuple[LlmProfile, ...]:
    """取り込んだプロファイルを、新しい id(uuid4().hex)で1回で足す(REQ-119 §3)。ファイルが無ければ断る。

    cli_path・env_suffix は付けない。既存のプロファイルと使用中は変えない。1件でも決まり(名前の重なりも)に反すれば
    何も書かずに SettingsError。保管庫には触らない(キーは無いまま)。
    """
    current = _require(path)
    added: list[LlmProfile] = []
    for profile in profiles:
        fresh = replace(profile, id=uuid.uuid4().hex, cli_path=None, env_suffix=None)
        added.append(_checked(fresh, (*current.profiles, *added), path))
    _write(path, replace(current, profiles=(*current.profiles, *added)))
    return tuple(added)


def duplicate_profile(path: Path, profile_id: str, *, name: str) -> LlmProfile:
    """複製する。新しい id で形式・モデル・接続先・送り方の値・定義・CLI の欄を写す。キーは写さない(保管庫には触らない)。

    環境変数の名前の続きも写さない(同じ値は断るため。ADR-0077)。
    """
    profiles = _require(path)
    source = _require_profile(profiles, profile_id, path)
    profile = _checked(replace(source, id=uuid.uuid4().hex, name=name, env_suffix=None), profiles.profiles, path)
    _write(path, replace(profiles, profiles=(*profiles.profiles, profile)))
    return profile


def update_profile(
    path: Path,
    profile_id: str,
    *,
    name: str = _UNSET,
    api_format: str = _UNSET,
    model: str | None = _UNSET,
    base_url: str | None = _UNSET,
    auth_header: str = _UNSET,
    wire: WireOptions = _UNSET,
    custom: CustomHttpJson | None = _UNSET,
    cli_path: str | None = _UNSET,
    timeout_seconds: int | None = _UNSET,
    env_suffix: str | None = _UNSET,
    cli_effort: str | None = _UNSET,
) -> LlmProfile:
    """渡した項目だけを変える。id は変えない。auth_header・wire・custom・cli_path・timeout_seconds・env_suffix を省けば
    ファイルのまま(wx の画面の保存)。"""
    profiles = _require(path)
    current = _require_profile(profiles, profile_id, path)
    changes = {
        k: v
        for k, v in (
            ("name", name),
            ("api_format", api_format),
            ("model", model),
            ("base_url", base_url),
            ("auth_header", auth_header),
            ("wire", wire),
            ("custom", custom),
            ("cli_path", cli_path),
            ("timeout_seconds", timeout_seconds),
            ("env_suffix", env_suffix),
            ("cli_effort", cli_effort),
        )
        if v is not _UNSET
    }
    profile = _checked(replace(current, **changes), profiles.profiles, path)
    _write(path, replace(profiles, profiles=tuple(profile if p.id == profile_id else p for p in profiles.profiles)))
    return profile


def set_active(path: Path, profile_id: str) -> LlmProfiles:
    """使うプロファイルを切り替える。"""
    profiles = _require(path)
    _require_profile(profiles, profile_id, path)
    updated = replace(profiles, active=profile_id)
    _write(path, updated)
    return updated


def delete_profile(path: Path, profile_id: str, *, platform: str | None = None) -> StoreResult:
    """プロファイルを消し、そのキーも保管庫から消す。active と最後の1件は消せない。

    ファイルを書いた後に保管庫のキーを消し、その結果を返す(保管庫が使えなくても、ファイルからは消えている)。
    """
    profiles = _require(path)
    _require_profile(profiles, profile_id, path)
    if len(profiles.profiles) == 1:
        raise SettingsError(_err("last_profile", path=path))
    if profile_id == profiles.active:
        raise SettingsError(_err("active_profile", path=path))
    _write(path, replace(profiles, profiles=tuple(p for p in profiles.profiles if p.id != profile_id)))
    return key_store.delete_key(profile_id=profile_id, platform=platform)


# --- custom-http-json の定義のファイル(書き出し・読み込み。REQ-101 §4.3。キーは入れない・読まない) ---


def write_custom_file(path: Path, custom: CustomHttpJson) -> None:
    """定義を1つの JSON に書く(UTF-8・改行 \\n)。保管庫のキーは入れない。"""
    _write_json(path, {CUSTOM_FILE_MARK: CUSTOM_FILE_VERSION, **custom.to_json()})


def read_custom_file(path: Path) -> tuple[CustomHttpJson, str]:
    """定義のファイルを読み、(定義, ファイルのバイト列の sha256) を返す。

    欄はちょうど印と5つの欄(キーなど、ほかの欄があるファイルは読まない)。決まり(custom_problem)も確かめる。
    読めない・形が違うときは SettingsError(場所と欄の名前だけ。値は入れない)。
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SettingsError(_err("unreadable", path=path, reason=_reason(exc))) from None
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SettingsError(_err("not_utf8", path=path)) from None
    try:
        ok, data = parse_json_text(text)
    except RecursionError:  # 入れ子が深すぎる
        ok = False
    if not ok:
        raise SettingsError(_err("not_json", path=path))
    if not isinstance(data, dict) or set(data) != {CUSTOM_FILE_MARK, *CUSTOM_FIELDS}:
        raise SettingsError(_err("custom_file_shape", path=path, mark=CUSTOM_FILE_MARK, fields=", ".join(CUSTOM_FIELDS)))
    if type(data[CUSTOM_FILE_MARK]) is not int or data[CUSTOM_FILE_MARK] != CUSTOM_FILE_VERSION:
        raise SettingsError(_err("custom_file_version", path=path, mark=CUSTOM_FILE_MARK, version=CUSTOM_FILE_VERSION))
    custom = _custom_from_json({name: data[name] for name in CUSTOM_FIELDS}, f"{path}: custom")
    if (problem := custom_problem(custom)) is not None:
        raise SettingsError(custom_problem_text(f"{path}: ", problem))
    return custom, digest
