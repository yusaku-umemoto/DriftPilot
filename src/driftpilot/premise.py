"""AI に毎回付ける前提の文(llm-premise.txt)の読み書き(ADR-0172 決定1。REQ-234 §2)。

置き場所は利用者ごとの設定のフォルダー(config.toml・llm-profiles.json・prompt-profiles.json と同じ)。
作業フォルダーの driftpilot.toml からは読まない。プロファイルの書き出し・取り込み(profile_bundle)にも入れない。

- UTF-8(先頭の BOM は外して読む)。改行は \\n にそろえて扱う。前後の空白だけの文は「無い」(None)とみなす
- 上限は PREMISE_MAX_CHARS 文字(追加の質問の1つの上限 follow_up.MAX_QUESTION_CHARS と同じ値。確かめに同じ部品を使うため)
- 読めない(UTF-8 でない・上限を超える・読む権限が無い)ときは SettingsError(場所と理由の種類だけ。中身は文に入れない)
- 書くのは設定の窓の「前提を保存」だけ(screen_model/settings_model.py)。同じフォルダーの一時ファイルに書いて os.replace で
  置き換える(prompt_profiles._write と同じ書き方。途中で落ちても壊れたファイルを残さない)。空なら空のファイルを書く(消さない)
- 秘密の語の確かめはこのモジュールでしない(語を知っている側がする。core の analyze・設定の窓の保存・入口の premise_problem)
"""

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from driftpilot import llm_settings
from driftpilot.llm_analyzer import normalize_newlines
from driftpilot.llm_settings import SettingsError

PREMISE_NAME = "llm-premise.txt"
PREMISE_MAX_CHARS = 4_000  # follow_up.MAX_QUESTION_CHARS と同じ値(テストで同じことを確かめる)


def premise_path(*, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """llm-premise.txt の場所(利用者ごとの設定ファイルと同じフォルダー)。

    user_config_path はモジュールから呼ぶたびに取る(テストが一時フォルダーを指すように差し替えるため)。
    """
    return user_config_path(platform=platform, env=env, home=home).parent / PREMISE_NAME


def user_config_path(*, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    """利用者ごとの設定ファイルの場所(llm_settings.user_config_path と同じ)。テストはこの関数を差し替える。"""
    return llm_settings.user_config_path(platform=platform, env=env, home=home)


def _too_long(path: Path) -> SettingsError:
    return SettingsError(f"{path}: 前提は {PREMISE_MAX_CHARS:,} 文字までです")


def load_premise(path: Path) -> str | None:
    """前提の文。無い・空・空白だけなら None。読めない・UTF-8 でない・上限を超えるときは SettingsError(中身は入れない)。"""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsError(f"{path}: 読めません ({exc.strerror or type(exc).__name__})") from None
    try:
        text = raw.decode("utf-8-sig")  # 先頭の BOM を外す
    except UnicodeDecodeError:
        raise SettingsError(f"{path}: UTF-8 として読めません") from None
    text = normalize_newlines(text).strip()
    if not text:
        return None
    if len(text) > PREMISE_MAX_CHARS:
        raise _too_long(path)
    return text


def write_premise(path: Path, text: str) -> None:
    """前提の文を書く。改行を \\n にそろえて strip() し、空なら空のファイル、空でなければ文＋\\n。

    上限を超える文は書かずに SettingsError。同じフォルダーの一時ファイルに書いてから os.replace で置き換え、
    失敗したら一時ファイルを消す(元のファイルは残る)。フォルダーが無ければ作る。どの文にも中身は入れない。
    """
    body = normalize_newlines(text).strip()
    if len(body) > PREMISE_MAX_CHARS:
        raise _too_long(path)
    content = f"{body}\n" if body else ""
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as fh:
            tmp = Path(fh.name)
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        raise SettingsError(f"{path}: 書けません ({exc.strerror or type(exc).__name__})") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
