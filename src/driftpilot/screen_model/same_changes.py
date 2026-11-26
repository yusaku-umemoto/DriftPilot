"""構造化比較の表の「同じ変更」(REQ-389 / ADR-0255)。

相手の名前(インターフェース名・オブジェクト名など)だけが違う、同じ内容の変更の行を、鍵でまとめる。
鍵は Python で決め、窓には行の番号の組・件数だけを渡す(窓の JS は鍵を作らない)。見せ方だけの決まりで、
行の作り方・並び・件数・保存・報告書は変えない。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Sequence

from driftpilot.screen_model.model import StructuredRow

# 項目の道すじの区切り(model._structured_rows が " > " でつなぐ)
_PATH_SEP = " > "

SameKey = tuple[str, str, str, str, str]


def _path_without_name(item: str) -> str:
    """項目の道すじから相手の名前の段を除いた形。3 段以上なら葉の 1 つ前の段を除き、2 段以下はそのまま。

    `fortinet > interfaces > port1 > allowaccess` → `fortinet > interfaces > allowaccess`。
    """
    parts = item.split(_PATH_SEP)
    if len(parts) >= 3:
        del parts[-2]
    return _PATH_SEP.join(parts)


def same_change_key(row: StructuredRow) -> SameKey:
    """行の「同じ変更」の鍵。鍵が同じ行どうしが同じ変更。

    入れるもの: 操作の鍵(op_key)・カテゴリ・変更前の値・変更後の値・相手の名前を除いた項目の道すじ。
    対象の列(target)は入れない。show を表で比べた結果の行も同じ決まり(項目は列の名前を並べた 1 段なのでそのまま)。
    """
    return (row.op_key, row.category, row.before, row.after, _path_without_name(row.item))


def same_change_keys(rows: Sequence[StructuredRow]) -> list[SameKey]:
    """行ごとの鍵(rows と同じ数・同じ順)。"""
    return [same_change_key(row) for row in rows]


def same_change_counts(rows: Sequence[StructuredRow]) -> list[int]:
    """行ごとの、同じ鍵の行の数 N(rows と同じ数・同じ順)。カテゴリの絞り込みにはかかわらない。"""
    keys = same_change_keys(rows)
    counts = Counter(keys)
    return [counts[key] for key in keys]


def same_hidden_rows(
    rows: Sequence[StructuredRow], hidden_keys: Collection[SameKey], only_key: SameKey | None
) -> list[int]:
    """隠す行の番号(昇順)。だけ表示のときは、その鍵ではない行の全部。何も隠していなければ空。"""
    keys = same_change_keys(rows)
    if only_key is not None:
        return [i for i, key in enumerate(keys) if key != only_key]
    return [i for i, key in enumerate(keys) if key in hidden_keys]


def same_counts_of(
    rows: Sequence[StructuredRow], hidden_keys: Collection[SameKey], only_key: SameKey | None
) -> tuple[int, int]:
    """表の上の文に入れる数 (件数, 種類の数)。

    隠したときは (隠した行の数, 隠した鍵の種類の数)。だけ表示のときは (残す行の数, 1)。何も隠していなければ (0, 0)。
    """
    keys = same_change_keys(rows)
    if only_key is not None:
        return sum(1 for key in keys if key == only_key), 1
    present = {key for key in keys if key in hidden_keys}
    return sum(1 for key in keys if key in present), len(present)
