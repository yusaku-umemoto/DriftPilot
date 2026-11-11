"""画面とコマンドの言語(英語・日本語)を決める(ADR-0190 決定1)。

`messages.py` が読み込むときに使うので、標準ライブラリだけを読む。
比較の部品・pywebview・`llm_settings`(`messages` を読む)は読まない。

決め方の順:
1. 環境変数 DRIFTPILOT_LANG が en・ja(大文字・小文字・前後の空白は問わない)ならそれ。このときは設定のファイルを読まない
2. 利用者の設定のファイルの [gui] language が en・ja ならそれ、system なら OS の言語
3. ほか(書かれていない・読めない・ほかの値)は en

1 つのプロセスの中で 1 回だけ決めて覚える(`current_language`)。テストのために `reset_language` で消せる。
"""

import locale
import os
import subprocess
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

LANG_ENV = "DRIFTPILOT_LANG"
EN = "en"
JA = "ja"
SYSTEM = "system"
LANGUAGES = (EN, JA, SYSTEM)
DEFAULT_LANGUAGE = EN
LANGUAGE_KEY = "language"

# 設定のファイルの場所の決め方は llm_settings.user_config_path と同じ(messages から読むので、そちらは読めない)
_GUI_SECTION = "gui"
_APP_DIR = "DriftPilot"
_CONFIG_NAME = "config.toml"
_PORTABLE_DIR = "settings"
# 外のコマンドが詰まったときに起動を長く止めないための上限(秒)
_OS_COMMAND_TIMEOUT = 3

_remembered: str | None = None


def language_setting_of(data: bytes | None) -> str:
    """設定のファイルのバイト列から [gui] language を読む。en・ja・system はそのまま。ほかはすべて "en"。

    無い・TOML として読めない・UTF-8 でない・[gui] が表でない・値が文字でない・3 つ以外の値は "en"。
    """
    if data is None:
        return DEFAULT_LANGUAGE
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError):
        return DEFAULT_LANGUAGE
    section = parsed.get(_GUI_SECTION, {})
    if not isinstance(section, dict):
        return DEFAULT_LANGUAGE
    value = section.get(LANGUAGE_KEY, DEFAULT_LANGUAGE)
    return value if isinstance(value, str) and value in LANGUAGES else DEFAULT_LANGUAGE


def _runtime() -> tuple[bool, str, str]:
    """動いている形 (exe か, 実行ファイルのパス, OS の名前)。テストはこの関数を差し替える。"""
    return bool(getattr(sys, "frozen", False)), sys.executable, sys.platform


def config_path(*, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """利用者ごとの設定ファイルの場所。`llm_settings.user_config_path` と同じ決め方(同じ場所になることをテストで確かめる)。"""
    frozen, executable, current = _runtime()
    platform = current if platform is None else platform
    if frozen and platform.startswith("win"):
        return Path(executable).resolve().parent / _PORTABLE_DIR / _CONFIG_NAME
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    if platform.startswith("win"):
        base = Path(env["APPDATA"]) if env.get("APPDATA") else home / "AppData" / "Roaming"
    elif platform == "darwin":
        base = home / "Library" / "Application Support"
    else:
        base = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else home / ".config"
    return base / _APP_DIR / _CONFIG_NAME


def _language_of_name(name: str | None) -> str:
    """"ja-JP"・"ja_JP" のような名前が ja で始まれば "ja"、ほかは "en"。"""
    if not name:
        return EN
    return JA if name.strip().lower().startswith("ja") else EN


def _mac_first_language(output: str) -> str | None:
    """`defaults read -g AppleLanguages` の出力(`(\\n    "ja-JP",\\n    "en-JP"\\n)`)の先頭の項目。"""
    for line in output.splitlines():
        item = line.strip().strip("(),").strip().strip("\"'").strip()
        if item:
            return item
    return None


def os_language(
    *,
    platform: str | None = None,
    run: Callable[..., Any] = subprocess.run,
    ui_language: Callable[[], int] | None = None,
    getlocale: Callable[[], tuple[str | None, str | None]] = locale.getlocale,
) -> str:
    """OS の表示の言語。ja で始まれば "ja"、ほかは "en"。調べられなければ "en"。

    Mac は `defaults read -g AppleLanguages` の先頭、Windows は GetUserDefaultUILanguage、ほかは locale の値。
    外のコマンドは引数の一覧で呼び、shell は使わない。
    """
    platform = _runtime()[2] if platform is None else platform
    try:
        if platform == "darwin":
            done = run(
                ["defaults", "read", "-g", "AppleLanguages"],
                capture_output=True,
                text=True,
                timeout=_OS_COMMAND_TIMEOUT,
                check=False,
            )
            if done.returncode != 0:
                return EN
            return _language_of_name(_mac_first_language(done.stdout or ""))
        if platform.startswith("win"):
            if ui_language is None:
                import ctypes

                ui_language = ctypes.windll.kernel32.GetUserDefaultUILanguage  # type: ignore[attr-defined]
            number = int(ui_language())
            return _language_of_name(locale.windows_locale.get(number))
        return _language_of_name(getlocale()[0])
    except Exception:  # noqa: BLE001 - 調べられないときは英語(起動を止めない)
        return EN


def decide_language(
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
    os_lang: str | None = None,
    read: Callable[[Path], bytes | None] | None = None,
) -> str:
    """言語を決める(覚えない)。順は上の説明のとおり。

    os_lang を渡すと、system のときの OS の言語としてそれを使う。read は設定のファイルを読む関数(テスト用)。
    """
    env = os.environ if env is None else env
    value = env.get(LANG_ENV, "").strip().lower()
    if value in (EN, JA):
        return value
    path = config_path(platform=platform, env=env, home=home)
    try:
        data = (read or _read_bytes)(path)
    except OSError:
        return DEFAULT_LANGUAGE
    setting = language_setting_of(data)
    if setting == SYSTEM:
        return os_lang if os_lang in (EN, JA) else os_language(platform=platform)
    return setting


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def current_language() -> str:
    """"en" か "ja"。1 つのプロセスの中で 1 回だけ決めて覚える。"""
    global _remembered
    if _remembered is None:
        try:
            _remembered = decide_language()
        except Exception:  # noqa: BLE001 - 言語が決められなくても起動は止めない(英語)
            _remembered = DEFAULT_LANGUAGE
    return _remembered


def choose(ja: Any, en: Any) -> Any:
    """今の言語が en なら en を、ほかは ja を返す。

    比較の部品・AI の部品(`messages` を読まない)が、自分の中に持つ文の表(日本語・英語の 2 つ)から 1 つを選ぶのに使う。
    1 つのプロセスの中では言語が変わらないので、モジュールを読み込むときに 1 回だけ選ぶ(`_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)`)。
    """
    return en if current_language() == EN else ja


def reset_language() -> None:
    """覚えた言語を消す(テスト用)。"""
    global _remembered
    _remembered = None
