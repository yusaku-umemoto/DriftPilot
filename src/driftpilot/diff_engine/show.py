"""show 出力の比較(REQ-07)。ntc-templates で構造化できれば行の集合で比較し、できなければ raw 比較へ回す。"""

from dataclasses import dataclass, field
from typing import Any, Literal

from ntc_templates.parse import ParsingException, parse_output
from textfsm import TextFSMError
from textfsm.clitable import CliTableError

from driftpilot.diff_engine.normalization import NormalizationRule
from driftpilot.diff_engine.raw import RawCompareResult, compare_raw
from driftpilot.raw_keys import DEFAULT_RAW_OPTIONS, RawOptions

Row = dict[str, Any]


@dataclass
class ShowCompareResult:
    method: Literal["structured", "raw"]
    # structured のとき: {"op": added|removed|changed, "key": ..., "before": 行|None, "after": 行|None, "fields": {列: [前, 後]}}
    diffs: list[dict[str, Any]] = field(default_factory=list)
    # structured で行の同一性を決めた列。None なら行全体の完全一致で比べた
    key_field: str | None = None
    # raw に回した理由
    fallback_reason: str | None = None
    raw: RawCompareResult | None = None

    @property
    def has_diff(self) -> bool:
        return bool(self.raw.diffs if self.raw else self.diffs)


def _freeze(row: Row) -> tuple:
    return tuple((k, tuple(v) if isinstance(v, list) else v) for k, v in row.items())


def _unique_key(rows_a: list[Row], rows_b: list[Row]) -> str | None:
    """先頭の列がどちら側でも重複せず、空でもなければキーに使う。"""
    first = next((next(iter(r)) for r in (*rows_a, *rows_b) if r), None)
    if first is None:
        return None
    for rows in (rows_a, rows_b):
        values = [r.get(first) for r in rows]
        if any(v in (None, "", []) for v in values) or len(set(map(str, values))) != len(values):
            return None
    return first


def _compare_rows(before: list[Row], after: list[Row]) -> tuple[list[dict[str, Any]], str | None]:
    key = _unique_key(before, after)
    diffs: list[dict[str, Any]] = []
    if key is None:
        b_set, a_set = {_freeze(r) for r in before}, {_freeze(r) for r in after}
        diffs += [{"op": "removed", "key": None, "before": r, "after": None, "fields": {}}
                  for r in before if _freeze(r) not in a_set]
        diffs += [{"op": "added", "key": None, "before": None, "after": r, "fields": {}}
                  for r in after if _freeze(r) not in b_set]
        return diffs, None
    b_map = {str(r[key]): r for r in before}
    a_map = {str(r[key]): r for r in after}
    for k, row in b_map.items():
        if k not in a_map:
            diffs.append({"op": "removed", "key": k, "before": row, "after": None, "fields": {}})
        elif row != a_map[k]:
            fields = {c: [row.get(c), a_map[k].get(c)] for c in dict.fromkeys([*row, *a_map[k]])
                      if row.get(c) != a_map[k].get(c)}
            diffs.append({"op": "changed", "key": k, "before": row, "after": a_map[k], "fields": fields})
    diffs += [{"op": "added", "key": k, "before": None, "after": row, "fields": {}}
              for k, row in a_map.items() if k not in b_map]
    return diffs, key


def compare_show(
    before: str,
    after: str,
    *,
    platform: str | None,
    command: str | None,
    rules: list[NormalizationRule] | None = None,
    enabled_ids: list[str] | None = None,
    options: RawOptions = DEFAULT_RAW_OPTIONS,
) -> ShowCompareResult:
    """show 出力2つを比較する。テンプレートがなければ正規化付き raw 比較へ回す。

    options は単純比較の設定(ADR-0166)。raw 比較へ回したときだけ使う(テンプレートで比べるときは使わない)。
    """

    def fallback(reason: str) -> ShowCompareResult:
        raw = compare_raw(before, after, kind="show", rules=rules, enabled_ids=enabled_ids, options=options)
        return ShowCompareResult(method="raw", fallback_reason=reason, raw=raw)

    if not platform or not command:
        return fallback("機種とコマンドが指定されていません")
    try:
        rows_before = parse_output(platform=platform, command=command, data=before)
        rows_after = parse_output(platform=platform, command=command, data=after)
    except (ParsingException, CliTableError, TextFSMError) as exc:
        # テンプレートなし(index に組がない)もここに来る
        return fallback(f"ntc-templates で構造化できません: {exc}")
    # 非空の入力から1件も取れないのは、テンプレートが合っていない可能性がある。差分なしと報告しない
    for side, text, rows in (("変更前", before, rows_before), ("変更後", after, rows_after)):
        if text.strip() and not rows:
            return fallback(f"{side}の出力からテンプレートで行を抽出できませんでした")
    diffs, key = _compare_rows(rows_before, rows_after)
    return ShowCompareResult(method="structured", diffs=diffs, key_field=key)
