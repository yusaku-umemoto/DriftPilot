"""2つ同時にドロップしたものの順(変更前・変更後)を決める(REQ-115 §2 / ADR-0078 決定1)。窓に頼らない純粋な関数。

上から順に見て、決まったところで止める。古い・前を変更前にする。

1. 更新日時(秒に切り捨てて比べる)。古いほうが変更前。読めなければ、この段を飛ばす
2. ファイル名の語(BEFORE_WORDS・AFTER_WORDS)。片方にだけ語があれば、もう片方は反対側。両方に同じ側の語があれば次へ
3. 親フォルダの名前の同じ語(before/sw1.cfg と after/sw1.cfg)
4. ファイル名の数字の並びを順に数として比べ、小さいほうが変更前(sw2 < sw10)。違う所が無いまま短いほうが尽きたら次へ
5. 名前の順(casefold、同じなら元の名前、それも同じならパスの文字列)。最後まで同じなら渡した順のまま

英字の語は前後が英字でないときだけ数える(14_before・before.txt は数え、afternoon・preview・older は数えない)。大文字小文字は区別しない。
日本語の語は部分一致。1つの名前に両方の側の語があれば、その段では決めない。ファイルの中身の日時は見ない。

比較の窓(compare_api)と一括比較の窓(batch_api)のドロップで共用する。ドロップの事象からパスを取る dropped_paths もここに置く。
"""

import math
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

# 語の表(左が変更前の側、右が変更後の側)
WORD_PAIRS = (("before", "after"), ("pre", "post"), ("old", "new"), ("変更前", "変更後"), ("事前", "事後"))
BEFORE_WORDS = tuple(before for before, _after in WORD_PAIRS)
AFTER_WORDS = tuple(after for _before, after in WORD_PAIRS)

_Side = Literal["before", "after", "both"]


def _pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    """英字の語は前後が英字でないときだけ、日本語の語は部分一致で数える式。"""
    parts = [
        rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])" if word.isascii() else re.escape(word) for word in words
    ]
    return re.compile("|".join(parts), re.IGNORECASE | re.ASCII)


_BEFORE = _pattern(BEFORE_WORDS)
_AFTER = _pattern(AFTER_WORDS)
_NUMBER = re.compile(r"\d+")


def _stat_mtime(path: Path) -> float:
    return path.stat().st_mtime


def _side(name: str) -> _Side | None:
    """名前に入っている語の側。両方の側の語があれば "both"、どちらも無ければ None。"""
    before, after = bool(_BEFORE.search(name)), bool(_AFTER.search(name))
    if before and after:
        return "both"
    if before:
        return "before"
    if after:
        return "after"
    return None


def _by_words(a_name: str, b_name: str) -> bool | None:
    """語で決める。a が変更前なら True、b が変更前なら False、決まらなければ None。"""
    a_side, b_side = _side(a_name), _side(b_name)
    if "both" in (a_side, b_side) or a_side == b_side:
        return None  # 両方の側の語がある・どちらにも語が無い・同じ側の語
    if a_side is not None:
        return a_side == "before"
    return b_side == "after"  # 片方にだけ語があれば、もう片方は反対側


def _by_mtime(a: Path, b: Path, mtime: Callable[[Path], float]) -> bool | None:
    try:
        a_time, b_time = math.floor(mtime(a)), math.floor(mtime(b))
    except (OSError, ValueError):
        return None  # 読めなければ、この段を飛ばす
    if a_time == b_time:
        return None
    return a_time < b_time


def _by_numbers(a_name: str, b_name: str) -> bool | None:
    for a_number, b_number in zip(_NUMBER.findall(a_name), _NUMBER.findall(b_name)):
        if int(a_number) != int(b_number):
            return int(a_number) < int(b_number)
    return None  # 違う所が無いまま短いほうが尽きた


def _name_key(path: Path) -> tuple[str, str, str]:
    return path.name.casefold(), path.name, str(path)


def order_pair(a: Path, b: Path, mtime: Callable[[Path], float] = _stat_mtime) -> tuple[Path, Path]:
    """2つの (変更前, 変更後)。mtime は更新日時を読む関数(テストで差し替える。OSError・ValueError なら日時の段を飛ばす)。"""
    for decide in (
        lambda: _by_mtime(a, b, mtime),
        lambda: _by_words(a.name, b.name),
        lambda: _by_words(a.parent.name, b.parent.name),
        lambda: _by_numbers(a.name, b.name),
    ):
        a_first = decide()
        if a_first is not None:
            return (a, b) if a_first else (b, a)
    return (b, a) if _name_key(b) < _name_key(a) else (a, b)


def dropped_paths(event: Any) -> list[Path]:
    """ドロップの事象の dataTransfer.files[*].pywebviewFullPath(空でない文字列)だけ。無いもの・形が違うものは数えない。

    pywebview 6.2.1 は、窓の側で記録したパスと名前が合ったファイルにだけ pywebviewFullPath を足す(webview/util.py)。
    """
    transfer = event.get("dataTransfer") if isinstance(event, dict) else None
    files = transfer.get("files") if isinstance(transfer, dict) else None
    if not isinstance(files, list):
        return []
    paths = [item.get("pywebviewFullPath") if isinstance(item, dict) else None for item in files]
    return [Path(path) for path in paths if isinstance(path, str) and path]
