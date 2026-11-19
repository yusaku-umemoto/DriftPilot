"""show 出力の比較(REQ-07)。ntc-templates で構造化できれば行の集合で比較し、できなければ raw 比較へ回す。

行のキーは、先頭の列 → 1 行だけの表 → 識別の列の単位(IDENTITY_UNITS)の順に決める(ADR-0204・REQ-287)。
"""

import bisect
import itertools
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from ntc_templates.parse import ParsingException, parse_output
from textfsm import TextFSMError
from textfsm.clitable import CliTableError

from driftpilot import i18n
from driftpilot.diff_engine.normalization import NormalizationRule
from driftpilot.diff_engine.raw import RawCompareResult, compare_raw
from driftpilot.raw_keys import (
    DEFAULT_RAW_OPTIONS,
    CompiledReplacements,
    RawOptions,
    apply_replacements,
    compile_replacements,
    require_valid,
)

Row = dict[str, Any]

# 単純比較へ回した理由の文。言語に合わせて読み込むときに 1 回だけ選ぶ(messages は読まない決まりのため、ここに持つ)
_TEXTS_JA = {
    "side_before": "変更前",
    "side_after": "変更後",
    "no_platform_command": "機種とコマンドが指定されていません",
    "outside_table": "表に出ない行が変わった",
    "ntc_failed": "ntc-templates で構造化できません: {error}",
    "no_rows": "{side}の出力からテンプレートで行を抽出できませんでした",
}
_TEXTS_EN = {
    "side_before": "Before",
    "side_after": "After",
    "no_platform_command": "The platform and the command are not specified",
    "outside_table": "Lines outside the table changed",
    "ntc_failed": "Cannot structure it with ntc-templates: {error}",
    "no_rows": "No rows could be extracted from the {side} output with the template",
}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)


@dataclass
class ShowCompareResult:
    method: Literal["structured", "raw", "both"]
    # structured のとき: {"op": added|removed|changed, "key": ..., "before": 行|None, "after": 行|None, "fields": {列: [前, 後]}}
    diffs: list[dict[str, Any]] = field(default_factory=list)
    # structured で行の同一性を決めた列。複合キーなら列の名前を `+` でつなぐ。
    # None なら行全体の完全一致(個数)で比べたか、1 行だけの表を同じ行として比べた
    key_field: str | None = None
    # raw に回した理由
    fallback_reason: str | None = None
    raw: RawCompareResult | None = None
    unexplained_lines: int = 0
    # 単純比較の置き換えで「変わっていない」とみなした表の列の値の数(structured・both のとき。ADR-0239 決定4)
    replaced_same: int = 0
    # 比べない列(この窓だけの設定)のうち、どちらかの表に列として出てきたもの(大文字。比べない列の並びの順。REQ-367)
    skipped_columns: list[str] = field(default_factory=list)

    @property
    def has_diff(self) -> bool:
        return bool(self.diffs or (self.raw and self.raw.diffs))


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


# 識別の列の単位(ADR-0204 決定3)。上ほど先に試す。括弧の単位は 2 つの列をいっしょに使う。後で足せるように 1 つの定数に置く
IDENTITY_UNITS: tuple[tuple[str, ...], ...] = (
    # 1 隣接
    ("bgp_neighbor",), ("bgp_neigh",), ("neighbor_id",), ("neighbor",), ("neighbor_name",), ("neighbor_ip",),
    # 2 宛先
    ("network", "prefix_length"), ("network", "mask"), ("network",), ("destination",), ("prefix",),
    # 3 アドレス
    ("ip_address",), ("address",), ("ip",), ("ipv6_address",),
    # 4 インターフェース・名前
    ("interface",), ("local_interface",), ("port",), ("ether_port",), ("name",), ("nic",),
    # 5 MAC
    ("destination_address",), ("mac_address",), ("mac",),
    # 6 VLAN
    ("vlan_id",), ("vlan",),
    # 7 一覧の中の位置
    ("acl_name",), ("line_num",), ("sequence",), ("seq",),
    # 8 次の宛先
    ("nexthop_ip",), ("gateway",), ("nexthop_if",), ("destination_port",),
    # 9 区分
    ("vrf",), ("address_family",), ("area",), ("link_id",), ("adv_router",), ("as",), ("process_id",),
    ("hostname",),
)
_MAX_UNITS = 3
_KEY_SEPARATOR = " / "


def _norm_value(value: Any) -> Any:
    """キーの値をそろえる。リストは tuple、None は空の文字列。"""
    if value is None:
        return ""
    return tuple(value) if isinstance(value, list) else value


def _is_empty(value: Any) -> bool:
    return value == "" or value == ()


def _identity_key(before: list[Row], after: list[Row]) -> tuple[str, ...] | None:
    """識別の列の単位を組み合わせて、両側で重複しないキーの列を選ぶ(ADR-0204 決定3)。見つからなければ None。"""
    present = {column for row in (*before, *after) for column in row}
    units = [u for u in IDENTITY_UNITS if all(c in present for c in u)]
    # 2 列の単位が残るなら、その列を含む 1 列の単位は使わない
    multi = [set(u) for u in units if len(u) > 1]
    units = [u for u in units if len(u) > 1 or not any(u[0] in m for m in multi)]
    # 単位ごとの値の並びは先に 1 回だけ作る
    side_values = {
        u: ([tuple(_norm_value(r.get(c)) for c in u) for r in before],
            [tuple(_norm_value(r.get(c)) for c in u) for r in after])
        for u in units
    }
    for count in range(1, min(_MAX_UNITS, len(units)) + 1):
        for combo in itertools.combinations(units, count):
            if _usable(combo, side_values):
                return tuple(dict.fromkeys(c for u in combo for c in u))
    return None


def _usable(combo: tuple[tuple[str, ...], ...], side_values: dict) -> bool:
    """組み合わせの値の並びが、両側それぞれで重複せず、全部空の行も無いか。"""
    for side in (0, 1):
        seen: set[tuple] = set()
        for parts in zip(*(side_values[u][side] for u in combo)):
            key = tuple(v for part in parts for v in part)
            if all(_is_empty(v) for v in key) or key in seen:
                return False
            seen.add(key)
    return True


def _changed_fields(row: Row, other: Row) -> dict[str, list[Any]]:
    return {c: [row.get(c), other.get(c)] for c in dict.fromkeys([*row, *other]) if row.get(c) != other.get(c)}


class _SkipColumns:
    """比べない列(REQ-367)。列の名前は大文字・小文字を区別せずに照らす。外した変化は、説明できる行の判定に使うために集める。"""

    def __init__(self, names: tuple[str, ...]) -> None:
        self.names = {n.upper() for n in names}
        # 外した変化: (行のキー, {列: [前, 後]})
        self.changes: list[tuple[Any, dict[str, list[Any]]]] = []

    def take(self, key: Any, fields: dict[str, list[Any]]) -> dict[str, list[Any]]:
        """変わった列から比べない列を外して残りを返す。外した列は変化として集める。"""
        skipped = {c: pair for c, pair in fields.items() if c.upper() in self.names}
        if not skipped:
            return fields
        self.changes.append((key, skipped))
        return {c: pair for c, pair in fields.items() if c not in skipped}


def _value_key(value: Any, replacements: CompiledReplacements) -> Any:
    """列の値の鍵(ADR-0239 決定1)。文字は置き換えを上から順に当てた文字、リストは要素ごと(文字の要素だけ)、ほかはそのまま。"""
    if isinstance(value, str):
        return apply_replacements(value, replacements)
    if isinstance(value, list):
        return [apply_replacements(v, replacements) if isinstance(v, str) else v for v in value]
    return value


def _pair_fields(row: Row, other: Row, replacements: CompiledReplacements,
                 skip: _SkipColumns | None = None, key: Any = None) -> tuple[dict[str, list[Any]] | None, int]:
    """組になった 2 行の変わった列(元の値)と、置き換えで同じとみなした列の数(ADR-0239 決定2)。

    差分にしない組(同じ行、または変わった列が全部、鍵では同じ)は None。置き換えが 0 組なら今と同じ(行が違えば変わった列を出す)。
    """
    if row == other:
        return None, 0
    fields = _changed_fields(row, other)
    if skip is not None and fields:
        # 比べない列の違いは変更の列にしない。残りが 0 の組は差分にしない(REQ-367)
        fields = skip.take(key, fields)
        if not fields:
            return None, 0
    if not replacements or not fields:
        return fields, 0
    kept = {c: pair for c, pair in fields.items()
            if _value_key(pair[0], replacements) != _value_key(pair[1], replacements)}
    if not kept:
        return None, len(fields)
    return kept, len(fields) - len(kept)


def _changed_diff(key: Any, row: Row, other: Row, replacements: CompiledReplacements,
                  skip: _SkipColumns | None = None) -> tuple[dict[str, Any] | None, int]:
    fields, same = _pair_fields(row, other, replacements, skip, key)
    if fields is None:
        return None, same
    return {"op": "changed", "key": key, "before": row, "after": other, "fields": fields}, same


def _compare_by_columns(before: list[Row], after: list[Row], columns: tuple[str, ...],
                        replacements: CompiledReplacements = (), skip: _SkipColumns | None = None) ->tuple[list[dict[str, Any]], str, int]:
    """選んだ識別の列の値の並び(tuple)で行を突き合わせる。差分の key は値を ` / ` でつないだ文字列(ADR-0204 決定4)。

    3 つめは置き換えで同じとみなした列の数。
    """

    def ident(row: Row) -> tuple:
        return tuple(_norm_value(row.get(c)) for c in columns)

    def label(row: Row) -> str:
        return _KEY_SEPARATOR.join("" if row.get(c) is None else str(row.get(c)) for c in columns)

    b_map = {ident(r): r for r in before}
    a_map = {ident(r): r for r in after}
    diffs: list[dict[str, Any]] = []
    same = 0
    for k, row in b_map.items():
        other = a_map.get(k)
        if other is None:
            diffs.append({"op": "removed", "key": label(row), "before": row, "after": None, "fields": {}})
        else:
            diff, count = _changed_diff(label(row), row, other, replacements, skip)
            same += count
            if diff is not None:
                diffs.append(diff)
    diffs += [{"op": "added", "key": label(row), "before": None, "after": row, "fields": {}}
              for k, row in a_map.items() if k not in b_map]
    return diffs, "+".join(columns), same


def _compare_rows(before: list[Row], after: list[Row]) -> tuple[list[dict[str, Any]], str | None]:
    """置き換えなしの表の比較(2 つの値を返す形。数も要るときは _compare_rows_counted)。"""
    diffs, key, _ = _compare_rows_counted(before, after)
    return diffs, key


def _compare_rows_counted(before: list[Row], after: list[Row], replacements: CompiledReplacements = (),
                          skip: _SkipColumns | None = None) ->tuple[list[dict[str, Any]], str | None, int]:
    """表の比較。3 つめは置き換えで同じとみなした列の値の数。行の組は元の値で決める(ADR-0239 決定2)。"""
    key = _unique_key(before, after)
    diffs: list[dict[str, Any]] = []
    if key is None:
        # 両側とも 1 行だけの表は、その 2 行を同じ行として比べる(ADR-0204 決定2)
        if len(before) == 1 and len(after) == 1:
            diff, same = _changed_diff("", before[0], after[0], replacements, skip)
            if diff is not None:
                diffs.append(diff)
            return diffs, None, same
        columns = _identity_key(before, after)
        if columns is not None:
            return _compare_by_columns(before, after, columns, replacements, skip)
        # キーが無いときは同じ行の個数で比べる(ADR-0202 決定2)。相手に残っている同じ行を 1 つずつ使う
        remaining_after = Counter(_freeze(r) for r in after)
        for r in before:
            frozen = _freeze(r)
            if remaining_after[frozen] > 0:
                remaining_after[frozen] -= 1
            else:
                diffs.append({"op": "removed", "key": None, "before": r, "after": None, "fields": {}})
        remaining_before = Counter(_freeze(r) for r in before)
        for r in after:
            frozen = _freeze(r)
            if remaining_before[frozen] > 0:
                remaining_before[frozen] -= 1
            else:
                diffs.append({"op": "added", "key": None, "before": None, "after": r, "fields": {}})
        return diffs, None, 0
    b_map = {str(r[key]): r for r in before}
    a_map = {str(r[key]): r for r in after}
    same = 0
    for k, row in b_map.items():
        if k not in a_map:
            diffs.append({"op": "removed", "key": k, "before": row, "after": None, "fields": {}})
        else:
            diff, count = _changed_diff(k, row, a_map[k], replacements, skip)
            same += count
            if diff is not None:
                diffs.append(diff)
    diffs += [{"op": "added", "key": k, "before": None, "after": row, "fields": {}}
              for k, row in a_map.items() if k not in b_map]
    return diffs, key, same


def _is_short_value(value: str) -> bool:
    """語の照合が外す値(2 文字以下、または数字だけ)。"""
    return len(value) <= 2 or value.isnumeric()


def _squeeze(text: str) -> str:
    """空白の並びを 1 つにまとめ、前後の空白を除く。"""
    return " ".join(text.split())


def _clue_words(key: Any, columns: list[str]) -> list[str]:
    """表の行のキーと変わった列の名前から、短い値の置き換えを許す手がかりの語を作る。"""
    if isinstance(key, str):
        keys = [key]
    elif isinstance(key, (list, tuple)):
        keys = [k for k in key if isinstance(k, str)]
    else:
        keys = []
    words = [w for k in keys for w in k.split() if len(w) >= 3 and not w.isnumeric()]
    words += [p for c in columns for p in c.split("_") if len(p) >= 3]
    return [w.lower() for w in words]


class _Clues:
    """手がかりの語の集まり。行の語が当たるかを、集合と前方一致の引き方で調べる(語の数に比例して遅くならない)。"""

    def __init__(self) -> None:
        self.words: set[str] = set()
        self._sorted: list[str] | None = None

    def update(self, words: list[str]) -> None:
        self.words.update(words)
        self._sorted = None

    def hit(self, line: str) -> bool:
        """行の語のどれかが手がかりの語に当たるか。大文字・小文字は区別しない。

        行の語の前後の `:`・`,`・`(`・`)` を外す。同じなら当たり。4 文字以上の手がかりの語が行の語の頭になるときと、
        4 文字以上の行の語が手がかりの語の頭になるときも当たり。
        """
        if self._sorted is None:
            self._sorted = sorted(self.words)
        for word in line.split():
            w = word.strip(":,()").lower()
            if not w:
                continue
            if w in self.words or any(w[:n] in self.words for n in range(4, len(w))):
                return True
            if len(w) >= 4:
                i = bisect.bisect_left(self._sorted, w)
                if i < len(self._sorted) and self._sorted[i].startswith(w):
                    return True
        return False


def _replacer(mapping: dict[str, str], loose: bool = False) -> re.Pattern[str]:
    """値を語として(前後が空白か行の端のところだけ)置き換える正規表現。長い値を先に並べて同時に置き換える。

    loose のときは、値の前後が英数字・`.`・`:`・`/`・`-` でなければ語とみなす(`01:06:47,` のように区切りの記号が続く値のため。REQ-367)。
    """
    values = sorted(mapping, key=len, reverse=True)
    body = "|".join(re.escape(v) for v in values)
    if loose:
        return re.compile(r"(?<![\w.:/-])(?:" + body + r")(?![\w:/-])(?!\.\w)")
    return re.compile(r"(?<!\S)(?:" + body + r")(?!\S)")


@dataclass
class _Trial:
    """置き換えの 1 つの試し方(1 つの列、または同じ表の行の変わった列を全部いっしょに)。"""

    forward: dict[str, str]  # 前の値 -> 後の値(消えた行に使う)。同じ前の値が違う後の値になるなら空
    backward: dict[str, str]  # 後の値 -> 前の値(足された行に使う)。同じ後の値が違う前の値になるなら空
    needs_clue: bool
    clues: _Clues
    forward_re: re.Pattern[str] | None = None
    backward_re: re.Pattern[str] | None = None
    loose: bool = False  # 比べない列の試し方(値の後ろに `,` などが続いてもよい。REQ-367)

    def __post_init__(self) -> None:
        self.forward_re = _replacer(self.forward, self.loose) if self.forward else None
        self.backward_re = _replacer(self.backward, self.loose) if self.backward else None


def _build_trials(diffs: list[dict[str, Any]], loose: bool = False) -> list[_Trial]:
    """表の差分の変わった行ごとに、置き換えの試し方を作る。列 1 つずつと、同じ表の行の列を全部いっしょに。"""
    # 同じ置き換え(値の対応が同じ)の試し方は 1 つにまとめ、手がかりの語は合わせる(行ごとの置き換えを 1 回にするため)
    merged: dict[tuple, _Trial] = {}
    for diff in diffs:
        if diff.get("op") != "changed":
            continue
        usable: list[tuple[str, str, str]] = []
        for column, pair in diff["fields"].items():
            before, after = pair
            if isinstance(before, str) and isinstance(after, str) and before.strip() and after.strip():
                usable.append((column, before.strip(), after.strip()))
        groups = [[u] for u in usable]
        if len(usable) >= 2:
            groups.append(usable)
        for group in groups:
            forward: dict[str, str] | None = {}
            backward: dict[str, str] | None = {}
            for _, before, after in group:
                if forward is not None and forward.setdefault(before, after) != after:
                    forward = None
                if backward is not None and backward.setdefault(after, before) != before:
                    backward = None
            if not forward and not backward:
                continue
            short = any(_is_short_value(v) for _, b, a in group for v in (b, a))
            forward, backward = forward or {}, backward or {}
            signature = (tuple(sorted(forward.items())), tuple(sorted(backward.items())))
            trial = merged.get(signature)
            if trial is None:
                trial = merged[signature] = _Trial(forward, backward, short, _Clues(), loose=loose)
            trial.clues.update(_clue_words(diff.get("key"), [c for c, _, _ in group]))
    return list(merged.values())


def _unexplained_lines(diffs: list[dict[str, Any]], raw: RawCompareResult,
                       skipped_changes: list[tuple[Any, dict[str, list[Any]]]] | tuple = ()) -> int:
    """単純比較の差分の行のうち、表の差分で説明できない行の数を返す。

    説明できる行: (1) 変更列と追加・削除行の語が行にある、(2) 動いた行(消えた行と同じ文が足された行にもある)、
    (3) 値の置き換えで相手側の行になる。ADR-0200 の追記(決定 3 の補い)・REQ-283。
    (4) 比べない列の値だけを相手の値に置き換えると、相手側の行とちょうど同じになる(REQ-367)。
    比べない列の値は(1)の語には入れない。ほかの部分も変わった行は説明できない行に残す。
    """
    words: set[str] = set()
    for diff in diffs:
        values = (v for pair in diff["fields"].values() for v in pair) if diff["op"] == "changed" else (
            (diff["before"] or diff["after"]).values()
        )
        for value in values:
            for part in value if isinstance(value, list) else [value]:
                if isinstance(part, str):
                    words.update(w for w in part.split() if len(w) > 2 and not w.isnumeric())
    removed = {line["text"] for line in raw.diffs if line["op"] == "removed"}
    added = {line["text"] for line in raw.diffs if line["op"] == "added"}
    removed_squeezed = {_squeeze(t) for t in removed}
    added_squeezed = {_squeeze(t) for t in added}
    # 置き換えの試し方を、置き換え元の値の先頭の語で引ける形にまとめる(値が語として行にあれば、その先頭の語は行の語になる)
    def index_trials(trials: list[_Trial]) -> tuple[dict[str, list[_Trial]], dict[str, list[_Trial]]]:
        by_before: dict[str, list[_Trial]] = {}
        by_after: dict[str, list[_Trial]] = {}
        for trial in trials:
            for value in trial.forward:
                by_before.setdefault(value.split()[0], []).append(trial)
            for value in trial.backward:
                by_after.setdefault(value.split()[0], []).append(trial)
        return by_before, by_after

    table_index = index_trials(_build_trials(diffs))
    # 比べない列の変化から作る試し方(値の置き換えと同じ作り。表の差分には入れない)
    skipped_index = index_trials(_build_trials(
        [{"op": "changed", "key": key, "fields": fields} for key, fields in skipped_changes],
        loose=True)) if skipped_changes else None

    def explained_by_replacement(text: str, forward: bool, indexes: tuple) -> bool:
        index = indexes[0] if forward else indexes[1]
        others = added_squeezed if forward else removed_squeezed
        base = _squeeze(text)
        seen: set[int] = set()
        candidates = text.split()
        if indexes is not table_index:
            # 比べない列の値は `01:06:47,` のように記号が続くので、語の前後の記号を外した形でも引く
            candidates += [w.strip(",;:()[]") for w in candidates]
        for word in dict.fromkeys(candidates):
            for trial in index.get(word, ()):
                if id(trial) in seen:
                    continue
                seen.add(id(trial))
                pattern, mapping = ((trial.forward_re, trial.forward) if forward
                                    else (trial.backward_re, trial.backward))
                if pattern is None:
                    continue
                result = _squeeze(pattern.sub(lambda m: mapping[m.group(0)], text))
                # 置き換えた文が相手側にあるときだけ、手がかりを調べる
                if result != base and result in others and (not trial.needs_clue or trial.clues.hit(text)):
                    return True
        return False

    count = 0
    for line in raw.diffs:
        text = line["text"]
        is_removed = line["op"] == "removed"
        if words.intersection(text.split()):
            continue
        if text in (added if is_removed else removed):
            continue
        if explained_by_replacement(text, is_removed, table_index):
            continue
        if skipped_index is not None and explained_by_replacement(text, is_removed, skipped_index):
            continue
        count += 1
    return count


def compare_show(
    before: str,
    after: str,
    *,
    platform: str | None,
    command: str | None,
    rules: list[NormalizationRule] | None = None,
    enabled_ids: list[str] | None = None,
    options: RawOptions = DEFAULT_RAW_OPTIONS,
    skip_columns: tuple[str, ...] = (),
) -> ShowCompareResult:
    """show 出力2つを比較する。テンプレートがなければ正規化付き raw 比較へ回す。

    options は常に併せて流す単純比較の設定(ADR-0166・ADR-0200)。
    skip_columns は表の比較で値の違いを変更の列にしない列の名前(この窓だけの設定。REQ-367)。空なら今と同じ。
    """

    def fallback(reason: str) -> ShowCompareResult:
        raw = compare_raw(before, after, kind="show", rules=rules, enabled_ids=enabled_ids, options=options)
        return ShowCompareResult(method="raw", fallback_reason=reason, raw=raw)

    if not platform or not command:
        return fallback(_T["no_platform_command"])
    try:
        rows_before = parse_output(platform=platform, command=command, data=before)
        rows_after = parse_output(platform=platform, command=command, data=after)
    except (ParsingException, CliTableError, TextFSMError) as exc:
        # テンプレートなし(index に組がない)もここに来る
        return fallback(_T["ntc_failed"].format(error=exc))
    # 非空の入力から1件も取れないのは、テンプレートが合っていない可能性がある。差分なしと報告しない
    for side, text, rows in ((_T["side_before"], before, rows_before), (_T["side_after"], after, rows_after)):
        if text.strip() and not rows:
            return fallback(_T["no_rows"].format(side=side))
    # 単純比較の置き換えを表の列の値にも当てる(ADR-0239)。0 組なら今の道。誤った式は今と同じ RawOptionsError で止める
    replacements: CompiledReplacements = []
    if options.replacements:
        require_valid(options)
        replacements = compile_replacements(options)
    skip = _SkipColumns(skip_columns) if skip_columns else None
    diffs, key, replaced_same = _compare_rows_counted(rows_before, rows_after, replacements, skip)
    raw = compare_raw(before, after, kind="show", rules=rules, enabled_ids=enabled_ids, options=options)
    # 動いた行・値の置き換えで説明できる行は数えない(REQ-283)。説明できない行がなければ表の結果だけにする
    unexplained = _unexplained_lines(diffs, raw, skip.changes if skip is not None else ())
    if not diffs and unexplained:
        return ShowCompareResult(method="raw", fallback_reason=_T["outside_table"], raw=raw)
    skipped: list[str] = []
    if skip is not None:
        present = {c.upper() for row in (*rows_before, *rows_after) for c in row}
        skipped = [n for n in dict.fromkeys(c.upper() for c in skip_columns) if n in present]
    if diffs and unexplained:
        return ShowCompareResult(method="both", diffs=diffs, key_field=key, raw=raw,
                                 unexplained_lines=unexplained, replaced_same=replaced_same, skipped_columns=skipped)
    return ShowCompareResult(method="structured", diffs=diffs, key_field=key, replaced_same=replaced_same,
                             skipped_columns=skipped)
