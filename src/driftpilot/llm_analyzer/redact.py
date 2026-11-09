"""人に渡す出力(HTML・CSV)の差分の行を伏せる(ADR-0156 決定2・3)。

★ 判定を新しく書かない。AI に送るときと同じ payload.py の判定の道(_Sides・_raw_line・_judge_line・_judge_parent・
_mask_tree・show の masker.gated)をそのまま呼ぶ。伏せる表(mask_patterns)と判定(masking)を直すと、出力にも同じに効く。

| 判定 | 出力 |
|---|---|
| plain・pass | そのまま |
| mask | 値の場所を `<secret:N>`(番号は組ごと。同じ値は同じ番号) |
| unsent・PEM のかたまり・割れた秘密のかたまり | OMITTED の決まった文 |

- 判定するのは出力する文字そのもの(画面の単純比較の表と同じ、正規化の前の区間の行 selected.body)
- 構造化比較の行が送らない形なら、操作とカテゴリだけ残し、対象・項目・変更前・変更後を OMITTED にする
- 単純比較の行番号は残す。差分の行の前後 CONTEXT_LINES 行(文脈の行)にも同じ判定を通す
- 行の形(unsent_shape)は出さない。省いた行の数だけを omitted_lines に数える
- 区間の行が取れないのに差分があるとき(PEM のかたまりを判定できない)は RedactError。出力は失敗にする

伏せずに出す口は作らない(ADR-0156 決定2)。
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from driftpilot.diff_engine import CompareResult, RawCompareResult, ShowCompareResult
from driftpilot.diff_engine.types import Diff
from driftpilot.llm_analyzer.masking import Masker, normalize_words
from driftpilot.llm_analyzer.payload import (
    _DROP,
    CONTEXT_LINES,
    _judge_line,
    _judge_parent,
    _mask_tree,
    _raw_line,
    _Recorder,
    _show_gate,
    _Sides,
    _value_path,
)

# llm_analyzer は messages を読まないので、payload の「(判定不能のため省略)」と同じくここに書く
OMITTED = "（秘密を含むかもしれないため出力しません）"
# show の差分でキーの無い行の対象(payload の _show_body と同じ文字)
_WHOLE_ROW = "(行全体)"
# show の差分を構造化比較の表に出すときのカテゴリ(画面の表と同じ)
_SHOW_CATEGORY = "show"
# 構造化不可の行の祖先をつなぐ文字(payload の _structured_line と同じ)
_PARENT_JOIN = " > "


class RedactError(Exception):
    """伏せる判定ができない(区間の行が取れないのに差分がある)。文に行の中身を入れない。"""


@dataclass(frozen=True)
class RedactedDiff:
    """構造化比較・show の 1 行。omitted が真なら対象・項目・変更前・変更後は OMITTED。"""

    op: str
    category: str
    target: str
    item: str
    before: str
    after: str
    omitted: bool


@dataclass(frozen=True)
class RedactedRawRow:
    """単純比較の 1 行(差分の行と文脈の行)。送らない側の文字は OMITTED。行番号は残す。"""

    mark: str
    before_no: int | None
    before_text: str
    after_no: int | None
    after_text: str
    is_diff: bool


@dataclass(frozen=True)
class RedactedResult:
    diffs: list[RedactedDiff]
    raw_rows: list[RedactedRawRow]
    omitted_lines: int


class _RawRowLike(Protocol):
    """screen_model.model.RawRow の形(llm_analyzer は screen_model を読まない)。"""

    mark: str
    before_no: int | None
    before_text: str
    after_no: int | None
    after_text: str

    @property
    def is_diff(self) -> bool: ...


def _dump(value: Any) -> str:
    """値の文字(payload._dump と同じ)。値の無い側(None)は空。"""
    return "" if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _masker(secret_words: Iterable[str]) -> Masker:
    return Masker(secret_words=normalize_words(secret_words))


def redact_text(text: str, secret_words: Iterable[str]) -> str:
    """組の名前・まとめて比較のコマンド名・ファイル名を判定する(ADR-0156 決定3)。

    unsent なら OMITTED。mask の行も OMITTED にする(名前は差分の表と別の判定器で判定するので、`<secret:N>` の番号が
    表の番号と食い違わないよう、値を出さない側に倒す)。
    """
    judged = _masker(secret_words).judge(text)
    if judged.text is None or judged.decision == "mask":
        return OMITTED
    return judged.text


def _omitted_diff(op: str, category: str) -> RedactedDiff:
    return RedactedDiff(op, category, OMITTED, OMITTED, OMITTED, OMITTED, True)


def _structured_row(diff: Diff, masker: Masker, sides: _Sides, rec: _Recorder) -> RedactedDiff:
    op, category = diff["op"], diff["category"]
    if category == "unparsed":
        line_path = diff["before"] if op == "removed" else diff["after"]
        *parents, text = line_path
        masked = _judge_line(text, tuple(p.strip() for p in parents), masker, sides, rec)
        if masked is _DROP:
            return _omitted_diff(op, category)
        # 祖先の行も出す文字なので同じ判定を通す(出せない祖先は OMITTED)。対象の欄(name)は元の行なので使わない
        shown = []
        for depth, parent in enumerate(parents):
            judged = _judge_parent(parent, tuple(p.strip() for p in parents[:depth]), masker, sides, rec)
            shown.append(OMITTED if judged is None else judged.strip())
        line = str(masked).strip()
        before, after = (line, "") if op == "removed" else ("", line)
        return RedactedDiff(op, category, "", _PARENT_JOIN.join(shown), before, after, False)
    before = _mask_tree(diff["before"], _value_path(diff, diff["before"]), masker, sides, rec)
    after = _mask_tree(diff["after"], _value_path(diff, diff["after"]), masker, sides, rec)
    if before is _DROP or after is _DROP:
        return _omitted_diff(op, category)
    return RedactedDiff(op, category, diff["name"] or "", _PARENT_JOIN.join(diff["path"]), _dump(before), _dump(after), False)


def _show_row(diff: dict[str, Any], masker: Masker, rec: _Recorder) -> RedactedDiff:
    op = diff["op"]
    found = _show_gate(diff, masker)
    if found is not None:
        rec.line(f"[show] 列 {found} の値が秘密語を含む …")
        return _omitted_diff(op, _SHOW_CATEGORY)
    target = diff.get("key") if diff.get("key") is not None else _WHOLE_ROW
    if op == "changed":
        fields = diff["fields"]
        before = _dump({column: pair[0] for column, pair in fields.items()})
        after = _dump({column: pair[1] for column, pair in fields.items()})
        return RedactedDiff(op, _SHOW_CATEGORY, str(target), ", ".join(fields), before, after, False)
    return RedactedDiff(op, _SHOW_CATEGORY, str(target), "", _dump(diff.get("before")), _dump(diff.get("after")), False)


def _shown_rows(rows: Sequence[_RawRowLike]) -> list[int]:
    """出す単純比較の行の番号(差分の行と、その前後 CONTEXT_LINES 行)。差分が無ければ []。"""
    keep = [False] * len(rows)
    for k, row in enumerate(rows):
        if row.is_diff:
            for j in range(max(0, k - CONTEXT_LINES), min(len(rows), k + CONTEXT_LINES + 1)):
                keep[j] = True
    return [k for k, kept in enumerate(keep) if kept]


def _raw_side(side: str, number: int | None, masker: Masker, sides: _Sides, rec: _Recorder) -> str:
    if number is None:
        return ""
    text = _raw_line(side, number - 1, masker, sides, rec, context=False)
    return OMITTED if text is None else text


def redact_result(
    *,
    structured: CompareResult | None,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
    before_body: Sequence[str] | None,
    after_body: Sequence[str] | None,
    raw_rows: Sequence[_RawRowLike],
    secret_words: Iterable[str],
) -> RedactedResult:
    """1 組の結果を伏せる。判定器(Masker)は組ごとに 1 つ(同じ値は同じ `<secret:N>`、番号は組ごとに 1 から)。

    before_body・after_body は区間の行(segmentation の selected.body。画面の単純比較の表と同じ行)。raw_rows は画面の
    単純比較の表の行(model.align_raw)。出すのは差分の行とその前後 CONTEXT_LINES 行だけで、ほかの行は判定もしない。
    """
    has_diffs = bool(
        (structured is not None and structured.diffs)
        or (show is not None and show.method == "structured" and show.diffs)
        or (raw is not None and raw.diffs)
        or any(row.is_diff for row in raw_rows)
    )
    if before_body is None or after_body is None:
        if has_diffs:
            raise RedactError("区間の行が無いため、伏せる判定ができません")
        before_body, after_body = (), ()
    masker = _masker(secret_words)
    sides = _Sides(before_body, after_body, None, None, masker)
    rec = _Recorder()
    diffs: list[RedactedDiff] = []
    if structured is not None:
        diffs += [_structured_row(diff, masker, sides, rec) for diff in structured.diffs]
    if show is not None and show.method == "structured":
        diffs += [_show_row(diff, masker, rec) for diff in show.diffs]
    rows: list[RedactedRawRow] = []
    for k in _shown_rows(raw_rows):
        row = raw_rows[k]
        rows.append(
            RedactedRawRow(
                row.mark,
                row.before_no,
                _raw_side("before", row.before_no, masker, sides, rec),
                row.after_no,
                _raw_side("after", row.after_no, masker, sides, rec),
                row.is_diff,
            )
        )
    return RedactedResult(diffs, rows, rec.lines + rec.context_lines)
