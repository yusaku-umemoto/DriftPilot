"""結果の表の列の絞り込み(REQ-391 / ADR-0256 決定2〜5)。

どの行を隠すかと、列の値の一覧(値・件数)を Python で決める。窓の JS は見える行を決めない。
見せ方だけの決まりで、行の作り方・並び・件数・保存・報告書は変えない。状態(列ごとに選んだ値の集まり)は呼ぶ側
(CompareApi)がメモリだけに持つ。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from typing import Any

from driftpilot.messages import COMPARE_FILTER_BLANK
from driftpilot.screen_model.model import OP_LABEL, RawRow, StructuredRow

# 構造化比較の表の列の鍵(見出しの順。対象と項目は 1 つの列に「対象 › 項目」と出すので 5 列)
STRUCTURED_COLUMNS = ("op", "category", "target_item", "before", "after")
# 単純比較の表の印の列の値の鍵(値の一覧に出す順)
RAW_MARK_VALUES = ("changed", "added", "removed", "ignored", "equated", "moved", "same")
# 値の一覧に出す種類の上限(超えたら検索で絞ってもらう)
MAX_VALUES = 1000
# 操作の列は、件数が 0 でもいつも出す 3 つ(行の op と同じ文)
OP_FIXED = (OP_LABEL["added"], OP_LABEL["changed"], OP_LABEL["removed"])
# 対象と項目をつなぐ文字(js/compare.js の targetItemText と同じ。U+203A)
TARGET_ITEM_SEP = " › "

_RAW_MARK_KEY = {"~": "changed", "+": "added", "-": "removed", "x": "ignored", "=": "equated", "<": "moved", ">": "moved", "": "same"}


def structured_cell(row: StructuredRow, column: str) -> str:
    """構造化比較の行の、列の表示の文字。対象・項目の列は画面と同じ「対象 › 項目」(片方が空ならもう片方)。"""
    if column == "op":
        return row.op
    if column == "category":
        return row.category
    if column == "target_item":
        if row.target and row.item:
            return f"{row.target}{TARGET_ITEM_SEP}{row.item}"
        return row.target or row.item or ""
    if column == "before":
        return row.before
    if column == "after":
        return row.after
    raise KeyError(column)


def raw_mark_value(row: RawRow) -> str:
    """単純比較の行の印を、7 つの値の鍵に変える。知らない印は「同じ行」。"""
    return _RAW_MARK_KEY.get(row.mark, "same")


def value_list(
    cells: Iterable[str],
    selected: Collection[str] | None,
    search: str = "",
    fixed: Sequence[str] = (),
    labels: Mapping[str, str] | None = None,
    ordered: Sequence[str] | None = None,
) -> dict[str, Any]:
    """値の一覧の窓に出す一覧。{items: [{value, label, count, checked}], over: 真偽}。

    cells は列の値(行ごと)。selected は選んでいる値の集まり(None なら全部にチェック)。
    並びは件数の多い順、同じ件数なら名前の順(ordered があればその並び)。空の値の表示は「（空白）」。
    search は大文字小文字を区別せず、表示の文字を含む値だけを残す。fixed は件数が 0 でも出す値。
    出すのは MAX_VALUES 種類まで。超えたら over が真。
    """
    counts = Counter(cells)
    for value in fixed:
        counts.setdefault(value, 0)

    def label_of(value: str) -> str:
        if labels is not None and value in labels:
            return labels[value]
        return value if value != "" else COMPARE_FILTER_BLANK

    needle = search.casefold()
    values = [v for v in counts if needle in label_of(v).casefold()]
    if ordered is not None:
        position = {v: i for i, v in enumerate(ordered)}
        values.sort(key=lambda v: position.get(v, len(position)))
    else:
        values.sort(key=lambda v: (-counts[v], label_of(v), v))
    over = len(values) > MAX_VALUES
    items = [
        {"value": v, "label": label_of(v), "count": counts[v], "checked": selected is None or v in selected}
        for v in values[:MAX_VALUES]
    ]
    return {"items": items, "over": over}


def next_selection(
    all_values: Collection[str],
    shown: Collection[str],
    checked: Iterable[str],
    current: Collection[str] | None,
    search: str = "",
) -> frozenset[str] | None:
    """OK を押したときの新しい選択。None は「絞っていない」(全部が選ばれている)。

    検索が空: 一覧に出した値はチェックのとおりにし、一覧に出なかった値(1,000 種類を超えた分)は今の選択のまま。
    検索がある: 検索に当たってチェックした値だけを残す(Excel と同じ)。
    全部の値が選ばれていれば None。1 つも選ばれていなければ何も変えない(今の選択を返す)。
    """
    everything = frozenset(all_values)
    shown_set = frozenset(shown)
    picked = frozenset(checked) & shown_set
    if search == "":
        base = everything if current is None else frozenset(current) & everything
        chosen = (base - shown_set) | picked
    else:
        chosen = picked
    if not chosen:
        return None if current is None else frozenset(current)
    if chosen >= everything:
        return None
    return chosen


def structured_hidden(rows: Sequence[StructuredRow], filters: Mapping[str, Collection[str]]) -> list[int]:
    """列の絞り込みで隠す構造化比較の行の番号(昇順。カテゴリの列も含む)。絞っていなければ空。"""
    if not filters:
        return []
    return [
        i
        for i, row in enumerate(rows)
        if any(structured_cell(row, column) not in allowed for column, allowed in filters.items())
    ]


def raw_hidden(rows: Sequence[RawRow], marks: Collection[str] | None) -> list[int]:
    """印の列の絞り込みで隠す単純比較の行の番号(昇順)。marks が None なら絞っていないので空。"""
    if marks is None:
        return []
    return [i for i, row in enumerate(rows) if raw_mark_value(row) not in marks]


def find_visible_diff(rows: Sequence[RawRow], hidden: Collection[int], current: int, step: int) -> int | None:
    """絞り込み中の「次の差分 / 前の差分」。隠れていない差分の行の間で、差分のかたまりの先頭を探す。

    find_diff と同じ決まりで、隠れた行は差分ではないもの(かたまりの切れ目)として扱う。
    """

    def visible_diff(k: int) -> bool:
        return rows[k].is_diff and k not in hidden

    heads = [k for k in range(len(rows)) if visible_diff(k) and (k == 0 or not visible_diff(k - 1))]
    if step > 0:
        return next((k for k in heads if k > current), None)
    return next((k for k in reversed(heads) if k < current), None)


def raw_skip_positions(rows: Sequence[RawRow], hidden: Collection[int], diffs: Sequence[dict[str, Any]]) -> tuple[int, ...]:
    """隠れた単純比較の行に入る差分の、diffs の位置(昇順)。

    削除は変更前の行番号が同じ行(印は - ~ <)、追加は変更後の行番号が同じ行(印は + ~ >)で対応を決める。
    """
    removed_row: dict[int, int] = {}
    added_row: dict[int, int] = {}
    for k, row in enumerate(rows):
        if row.before_no is not None and row.mark in ("-", "~", "<"):
            removed_row[row.before_no] = k
        if row.after_no is not None and row.mark in ("+", "~", ">"):
            added_row[row.after_no] = k
    skip: list[int] = []
    for position, diff in enumerate(diffs):
        if diff["op"] == "removed":
            k = removed_row.get(diff["before_line_no"])
        elif diff["op"] == "added":
            k = added_row.get(diff["after_line_no"])
        else:
            k = None
        if k is not None and k in hidden:
            skip.append(position)
    return tuple(skip)
