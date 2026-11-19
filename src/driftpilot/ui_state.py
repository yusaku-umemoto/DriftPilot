"""窓の配置を覚えるファイル(ui-state.json)の読み書き(REQ-374。REQ-370 の置き場所の直し)。

持つのは比較の窓の AI の欄の境目の位置(compare_split_left。左の面の割合。0.3〜0.9)と、AI の欄を隠したか(compare_ai_hidden)だけ。
利用者ごとの設定ファイル(config.toml)の [gui] ではなく、同じフォルダーの別のファイルに置く(raw-compare.json・prompt-profiles.json と
同じ所)。設定の窓はこのファイルを読まず、書かない。設定の窓の保存の確かめ(ADR-0071 決定4。設定ファイルが読み込んだときから変わって
いないか)の対象にもならないので、比較の窓で境目を動かしても、設定の窓の保存は断られない。読むのも書くのも比較の窓(gui_web/compare_api.py)だけ。

中身は {"schema_version": 1, "compare_split_left": 数, "compare_ai_hidden": 真偽値}。UTF-8・字下げ 2・最後に改行 1 つ(raw-compare.json と同じ)。

- 読む(load_compare_layout): 比較の窓を開くとき。ファイルが無い・読めない・UTF-8 や JSON として読めない・中身が表でない・
  schema_version が整数の 1 でないときは、2 つとも初めの値(境目は None=72%、隠した状態は偽=出す)。2 つの値は別々に確かめ、境目は
  数(真偽値を除く)で有限で 0.3〜0.9 の中のときだけ小数 3 桁に丸めて使い、隠した状態は真偽値のときだけ使う。使えない値だけが初めの値になる。
  知らないキーは無視する。例外は出さない。
- 書く(save_compare_layout): 今のファイルを読み、渡した値だけを置き換えて全体を書く(比較の窓を 2 つ開いていても、片方が書いた値をもう片方が
  消さない)。今のファイルが読めない・形が違うときは新しく作る。形が合っていれば知らないキーも残す(後の版で足したキーを消さない)。
  範囲に当てはめるのは読むときだけ。書くときは、数・真偽値であることだけを確かめる。同じフォルダーの一時ファイルに書いて fsync し、
  os.replace で置き換える(途中で落ちても壊れたファイルを残さない。失敗したら一時ファイルを消し、元のファイルを残す)。フォルダーが無ければ作る。
  書けなければ OSError をそのまま出す(呼ぶ側が捨てる)。
"""

import json
import math
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path

from driftpilot.llm_settings import user_config_path

UI_STATE_NAME = "ui-state.json"
SCHEMA_VERSION = 1
# 比較の窓の左右の境目(左の面の割合。数。0.3〜0.9 の外・数でないときは使わない)と、AI の欄を隠したか(真偽値。書かれていなければ偽)
COMPARE_SPLIT_LEFT_KEY = "compare_split_left"
COMPARE_AI_HIDDEN_KEY = "compare_ai_hidden"
SPLIT_LEFT_MIN = 0.3
SPLIT_LEFT_MAX = 0.9


def ui_state_path(
    *, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """ui-state.json の場所(利用者ごとの設定ファイル・raw-compare.json と同じフォルダー)。"""
    return user_config_path(platform=platform, env=env, home=home).parent / UI_STATE_NAME


def _read_object(path: Path) -> dict[str, object] | None:
    """ファイルを読んで、形が合う(表で、schema_version が整数の 1)ときだけ中身を返す。それ以外は None。例外は出さない。"""
    try:
        data = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        return None
    return data


def _is_number(value: object) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def load_compare_layout(path: Path) -> tuple[float | None, bool]:
    """比較の窓を開くときの (左の面の割合または None, AI の欄を隠すか)。読めない・形が違うときは (None, False)。"""
    data = _read_object(path)
    if data is None:
        return None, False
    split = data.get(COMPARE_SPLIT_LEFT_KEY)
    left: float | None = None
    if _is_number(split) and SPLIT_LEFT_MIN <= split <= SPLIT_LEFT_MAX:  # type: ignore[operator]
        left = round(float(split), 3)  # type: ignore[arg-type]
    hidden = data.get(COMPARE_AI_HIDDEN_KEY)
    return left, hidden if isinstance(hidden, bool) else False


def save_compare_layout(path: Path, values: Mapping[str, object]) -> None:
    """values(compare_split_left・compare_ai_hidden のうち渡したもの)だけを置き換えて書く。

    名前がこの 2 つ以外・隠した状態が真偽値でない・境目が有限の数でない(真偽値も)ときは ValueError(書かない)。
    書けないときは OSError(元のファイルは残り、一時ファイルは消える)。
    """
    unknown = sorted(set(values) - {COMPARE_SPLIT_LEFT_KEY, COMPARE_AI_HIDDEN_KEY})
    if unknown:
        raise ValueError(f"not writable: {', '.join(unknown)}")
    for key, value in values.items():
        if key == COMPARE_AI_HIDDEN_KEY:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false")
        elif not _is_number(value):
            raise ValueError(f"{key} must be a finite number")
    data: dict[str, object] = dict(_read_object(path) or {"schema_version": SCHEMA_VERSION})
    data.update(values)
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
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
