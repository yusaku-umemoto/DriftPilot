"""単純比較の既定の設定のファイル(raw-compare.json)の読み書き(ADR-0166 決定10。REQ-227 §10)。

置き場所は AI の指示文のファイル(prompt-profiles.json)と同じフォルダ。比較の窓を開いたときの設定で、設定の窓の
「単純比較」のタブが書く。コマンド(CLI)は読まない。読むのも書くのも画面の論理(screen_model)だけ。

中身は {"schema_version": 1, "raw_options": {raw_keys.options_to_plain の形}, "context_lines": 3}。context_lines は
「差分のみ表示」の前後の行数(REQ-228 §2。0〜20。無ければ 3。REQ-227 のファイルも読む)。無効のルールがあるときだけ、上の段に
"disabled": {"ignore_lines": [式…], "replacements": [{"pattern", "replacement"}…]} を書く(REQ-359。raw_options は無効のルールも
含めた全部。disabled が無ければ全部有効。raw_options に無いものは読むときに捨てる)。読めない・形が違うときは SettingsError
(場所と理由の種類だけ。式は文に入れない)。書くときは同じフォルダの一時ファイルに UTF-8・改行 \\n で書いて os.replace で
置き換える(prompt_profiles と同じ書き方。途中で落ちても壊れたファイルを残さない)。
"""

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from driftpilot.llm_settings import SettingsError, user_config_path
from driftpilot.messages import RAWSET_TEXTS as _T
from driftpilot.raw_keys import (
    DEFAULT_RAW_OPTIONS,
    RAW_CONTEXT_DEFAULT,
    RAW_CONTEXT_MAX,
    NO_DISABLED,
    RawDisabled,
    RawOptions,
    RawOptionsError,
    disabled_from_plain,
    disabled_to_plain,
    enabled_options,
    options_from_plain,
    options_to_plain,
    prune_disabled,
)

RAW_SETTINGS_NAME = "raw-compare.json"
SCHEMA_VERSION = 1


def raw_settings_path(
    *, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """raw-compare.json の場所(prompt-profiles.json・利用者ごとの設定ファイルと同じフォルダ)。"""
    return user_config_path(platform=platform, env=env, home=home).parent / RAW_SETTINGS_NAME


@dataclass(frozen=True)
class RawSettings:
    """raw-compare.json の中身。options は単純比較の設定(無効のルールも含めた全部)、context_lines は「差分のみ表示」の前後の行数
    (表示だけの値)。disabled は無効のルール(REQ-359。options のルールのうち、比べるときだけ外すもの)。
    """

    options: RawOptions = DEFAULT_RAW_OPTIONS
    context_lines: int = RAW_CONTEXT_DEFAULT
    disabled: RawDisabled = NO_DISABLED

    def enabled_options(self) -> RawOptions:
        """比べるときの設定(無効のルールを除いたもの)。"""
        return enabled_options(self.options, self.disabled)


def load_raw_defaults(path: Path) -> RawOptions:
    """既定の設定を読む(無効のルールは除く。一括比較の窓へ渡す設定はこれ)。ファイルが無ければすべてオフ(DEFAULT_RAW_OPTIONS)。
    読めない・形が違えば SettingsError。
    """
    return load_raw_settings(path).enabled_options()


def load_raw_settings(path: Path) -> RawSettings:
    """既定の設定と前後の行数を読む。ファイルが無ければすべてオフと 3。読めない・形が違えば SettingsError。

    context_lines が無ければ 3(REQ-227 のファイル)。整数で 0〜20 でなければ(真偽値も) SettingsError。
    """
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return RawSettings()
    except OSError as exc:
        raise SettingsError(_T["cannot_read"].format(path=path, reason=exc.strerror or type(exc).__name__)) from None
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise SettingsError(_T["not_utf8"].format(path=path)) from None
    except (ValueError, RecursionError):
        raise SettingsError(_T["not_json"].format(path=path)) from None
    if not isinstance(data, dict):
        raise SettingsError(_T["not_object"].format(path=path))
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise SettingsError(_T["schema_version"].format(path=path, version=SCHEMA_VERSION))
    if "raw_options" not in data:
        raise SettingsError(_T["no_raw_options"].format(path=path))
    context = data.get("context_lines", RAW_CONTEXT_DEFAULT)
    if type(context) is not int or not 0 <= context <= RAW_CONTEXT_MAX:
        raise SettingsError(_T["context_lines"].format(path=path, maximum=RAW_CONTEXT_MAX))
    try:
        options = options_from_plain(data["raw_options"])
        # disabled が無ければ全部有効(REQ-359)。raw_options に無いものは捨てる
        disabled = prune_disabled(options, disabled_from_plain(data["disabled"])) if "disabled" in data else NO_DISABLED
        return RawSettings(options, context, disabled)
    except RawOptionsError as exc:
        if exc.problems:
            kinds = _T["kinds_separator"].join(sorted({p.kind for p in exc.problems}))
            raise SettingsError(_T["bad_options"].format(path=path, kinds=kinds)) from None
        # 形の誤りの文は場所と種類だけ(式は入らない)
        raise SettingsError(_T["form_error"].format(path=path, error=exc)) from None


def save_raw_defaults(
    path: Path, options: RawOptions, context_lines: int = RAW_CONTEXT_DEFAULT, disabled: RawDisabled = NO_DISABLED
) -> None:
    """既定の設定と前後の行数を書く。フォルダが無ければ作る。書けなければ SettingsError(元のファイルは残る)。

    options は無効のルールも含めた全部。disabled(REQ-359)は上の段に書く。無効が 1 つも無ければ書かない(今と 1 文字も同じ)。
    """
    data: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "raw_options": options_to_plain(options),
        "context_lines": context_lines,
    }
    disabled = prune_disabled(options, disabled)
    if not disabled.is_empty():
        data["disabled"] = disabled_to_plain(disabled)
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
        raise SettingsError(_T["cannot_write"].format(path=path, reason=exc.strerror or type(exc).__name__)) from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
