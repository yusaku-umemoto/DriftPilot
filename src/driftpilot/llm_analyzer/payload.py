"""LLM へ送る差分の組み立てと打ち切り(ADR-0018 決定1、ADR-0080 決定3、2026-10-16 承認の件数の上限)。

| 比較の結果 | 送るもの |
|---|---|
| 構造化差分がある | 構造化差分(カテゴリ・対象・項目・変更前後の値) |
| show の構造化差分がある | show の行の差分(列と値) |
| 構造化比較(または show の構造化)が動いて 0 件 | 送らない(None を返す。理由は no_structured_diff) |
| 構造化比較が動かなかった(失敗・単純だけ・show のテンプレートが無い)で、行単位の差分がある | 行単位の差分(前後2行と親の行の見出し) |
| 差分が無い | 送らない(None を返す) |

config 全文は既定では送らない。打ち切りは伏せる処理の前の件数で判定し、先頭から上限の件数までを使う。
すべての行は masking.Masker の3段の判定を通る(文脈の行と見出しも)。送らなかった行は Unsent に件数と形だけを残す。

変更後の全文(ADR-0158。利用者が「差分＋全文を送る」を選んだときだけ): include_after_text が真なら、差分の本文を作った後に、
同じ masker・_Sides・_Recorder で変更後の全部の行を _raw_line(context=True) に通す(判定は新しく書かない)。
送れない行は除き、続けて除いた行は FULL_TEXT_OMITTED の1行にまとめる。行番号は付けない。
除いた行は Unsent の context_lines と形の一覧に入り、差分の unsent.lines には入らない。
show の比較でも作る(ADR-0182 決定2。構造化した show でも、_Sides の変更後の行を同じ判定に通す)。
"""

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from driftpilot import i18n
from driftpilot.diff_engine import CompareResult, RawCompareResult, ShowCompareResult
from driftpilot.diff_engine.raw import RawOptionsRecord
from driftpilot.diff_engine.types import Diff
from driftpilot.llm_analyzer import fields
from driftpilot.llm_analyzer.masking import (
    PEM_BEGIN,
    WRAPPED_SHAPE,
    Masker,
    ancestors_by_indent,
    block_index,
    pem_blocks,
    pem_start_shape,
    scope_label,
)
from driftpilot.llm_analyzer.types import LlmSource, Unsent, UnsentKind, UnsentShape

# 送る前の確かめ(送らなかった形の一覧)にだけ出る文。AI に送る本文には入らない(日本語・英語。REQ-258)
_TEXTS_JA = {"show_gate_shape": "[show] 列 {column} の値が秘密語を含む …"}
_TEXTS_EN = {"show_gate_shape": "[show] column {column} has a value containing a secret word ..."}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

# AI に送る差分の本文に DriftPilot が入れる印・見出し(REQ-260 / ADR-0190 決定3)。日本語の表と英語の表を別々に持ち、
# 読み込むときに言語で 1 つ選ぶ(同じ鍵・同じ差し込みの {…})。機器の config の行そのもの(差分の中身)と伏せる処理は言語で変えない。
# 日本語の版は REQ-259 までの送る文と 1 字も変えない
_MARKS_JA = {
    "side_before": "変更前",
    "side_after": "変更後",
    "run_one": "@@ {side} {first} 行目 @@",
    "run_many": "@@ {side} {first}〜{last} 行目 @@",
    "parent": "  親: {text}",
    "omitted": "(判定不能のため省略)",
    "dropped": "  (判定不能のため {count} 行を除いた)",
    "whole_row": "(行全体)",
    "unknown_command": "(コマンド不明)",
    "whole_header": "--- コマンド: {name}（{kind} の差分）---",
    "no_diff_head": "差分の無かったコマンド: ",
    "no_diff_none": "なし",
    "name_separator": "、",
}
_MARKS_EN = {
    "side_before": "before",
    "side_after": "after",
    "run_one": "@@ {side} line {first} @@",
    "run_many": "@@ {side} lines {first}-{last} @@",
    "parent": "  parent: {text}",
    "omitted": "(omitted: could not be judged)",
    "dropped": "  ({count} lines that could not be judged were removed)",
    "whole_row": "(whole line)",
    "unknown_command": "(unknown command)",
    "whole_header": "--- Command: {name} ({kind} differences) ---",
    "no_diff_head": "Commands with no differences: ",
    "no_diff_none": "none",
    "name_separator": ", ",
}
_M = i18n.choose(_MARKS_JA, _MARKS_EN)

CONTEXT_LINES = 2
# 変更後の全文で、判定できずに除いた行の代わりに置く1行(差分の文脈の行と同じ文。ADR-0158 決定3)
FULL_TEXT_OMITTED_JA = _MARKS_JA["omitted"]
FULL_TEXT_OMITTED_EN = _MARKS_EN["omitted"]
FULL_TEXT_OMITTED = _M["omitted"]
# 変更後の全文の文字数の上限(ADR-0158 決定5)。超えたら全文も差分も送らない(切り詰めない)
MAX_FULL_TEXT_CHARS = 150_000
_SIDE_LABEL = {"before": _M["side_before"], "after": _M["side_after"]}
# リストの項目(要素の総称のパスは `<項目>[]`)
_LIST_FIELDS = frozenset(p[:-2] for p in fields.ir_field_paths() if p.endswith("[]"))


class _Drop:
    """送らない値の印(リストの要素なら要素ごと落とす)。"""


_DROP = _Drop()


@dataclass(frozen=True)
class AfterText:
    """変更後の全文(伏せた後。ADR-0158 決定2・3)。lines は送る行数(省略の1行は数えない)、omitted は除いた行数。"""

    body: str
    lines: int
    omitted: int


@dataclass(frozen=True)
class LlmInputs:
    """core の _llm_analysis が analyze に渡すのと同じ行(正規化の後と前。ADR-0158 決定7)。

    追加の質問で全文を作り直すときに、core から follow_up へ渡す入れ物(llm_analyzer は core を読まないため、ここに置く)。
    """

    before_lines: list[str]
    after_lines: list[str]
    before_source: list[str]
    after_source: list[str]


@dataclass(frozen=True)
class Payload:
    source: LlmSource
    body: str
    diff_total: int
    sent_diffs: int
    truncated: bool
    unsent: Unsent
    # 変更後の全文(include_after_text が真のときだけ。show の比較でも作る。ADR-0182)
    after_text: AfterText | None = None


class _Recorder:
    """送らなかったものを数える。PEM ブロックは (側, 開始位置) で1件にまとめる。

    割れた秘密の行のかたまり(ADR-0137)は PEM の数に入れず、送らなかった行として1行ずつ数える。形は WRAPPED_SHAPE だけ。
    """

    def __init__(self) -> None:
        self.lines = 0
        self.context_lines = 0
        self.shapes: Counter[tuple[UnsentKind, str, str]] = Counter()
        self.shape_lines: Counter[tuple[UnsentKind, str, str]] = Counter()
        self.blocks: set[tuple[str, int]] = set()

    def line(self, shape: str, *, context: bool = False) -> None:
        if context:
            self.context_lines += 1
        else:
            self.lines += 1
        scope, _, rest = shape.partition("] ")
        key: tuple[UnsentKind, str, str] = ("line", scope.lstrip("["), rest)
        self.shapes[key] += 1
        self.shape_lines[key] += 1

    def block(
        self, block_key: tuple[str, int], scope: str, shape: str, length: int, *, context: bool = False, pem: bool = True
    ) -> None:
        if not pem:
            self.line(f"[{scope}] {shape}", context=context)
            return
        if context:
            self.context_lines += 1
        else:
            self.lines += 1
        if block_key in self.blocks:
            return
        self.blocks.add(block_key)
        key: tuple[UnsentKind, str, str] = ("pem_block", scope, shape)
        self.shapes[key] += 1
        self.shape_lines[key] += length

    def result(self) -> Unsent:
        shapes = [
            UnsentShape(kind, scope, shape, count, self.shape_lines[(kind, scope, shape)])
            for (kind, scope, shape), count in sorted(self.shapes.items(), key=lambda kv: (-kv[1], kv[0]))
        ]
        return Unsent(self.lines, self.context_lines, len(self.blocks), shapes)


class _Sides:
    """両側の行と、そこから決まる祖先・PEM ブロック。

    lines は正規化後の行(行単位の差分の行番号と送る文字列はこちら)、source は正規化前の区間の全行。
    正規化は行数を変えないため、同じ位置が同じ行を指す。PEM ブロックの範囲と祖先は**正規化前の全行**から求める
    (正規化ルールが `-----BEGIN` の行を書き換えても、ブロックを見失わないため)。正規化後の全行で見つかったブロックも足す。
    差分の行は、全行での位置でブロックの中かどうかを引く(差分の行だけからは求めない。本体の1行だけが差分に出ることがある)。
    割れた秘密の行のかたまり(ADR-0137)も同じブロックとして持つ。かたまりは判定に使う masker と同じ秘密語で求める。
    """

    def __init__(
        self,
        before: Sequence[str],
        after: Sequence[str],
        before_source: Sequence[str] | None,
        after_source: Sequence[str] | None,
        masker: Masker,
    ) -> None:
        self.lines = {"before": list(before), "after": list(after)}
        self.source = {
            "before": list(before if before_source is None else before_source),
            "after": list(after if after_source is None else after_source),
        }
        self.ancestors = {s: ancestors_by_indent(v, masker) for s, v in self.source.items()}
        self.blocks: dict[str, dict[int, int]] = {}
        # PEM ブロックの開始位置(これ以外の開始位置は、割れた秘密の行のかたまり)
        self.pem_starts: dict[str, set[int]] = {}
        for side in self.lines:
            merged = block_index(self.source[side], masker)
            for position, start in block_index(self.lines[side], masker).items():
                merged.setdefault(position, start)
            self.blocks[side] = merged
            self.pem_starts[side] = {s for t in (self.source[side], self.lines[side]) for s, _ in pem_blocks(t)}
        # unparsed の行(字下げを除く)が PEM ブロックの中の行かどうかを、両側の全文(正規化の前と後)から引く
        self.pem_text: dict[str, tuple[str, int]] = {}
        for side, index in self.blocks.items():
            for position, start in index.items():
                for texts in (self.source[side], self.lines[side]):
                    if position < len(texts):
                        self.pem_text.setdefault(texts[position].strip(), (side, start))

    def ancestors_at(self, side: str, index: int) -> tuple[str, ...]:
        found = self.ancestors[side]
        return found[index] if index < len(found) else ()

    def block_length(self, side: str, start: int) -> int:
        return sum(1 for s in self.blocks[side].values() if s == start)

    def block_shape(self, side: str, start: int) -> tuple[str, str]:
        scope = scope_label(self.ancestors_at(side, start))
        if start not in self.pem_starts[side]:  # 割れた秘密の行のかたまりは、行の中身を形に残さない(ADR-0137 決定4)
            return scope, WRAPPED_SHAPE
        # 開始行は、-----BEGIN を含むほう(正規化の前か後)から形を作る
        candidates = [t[start] for t in (self.source[side], self.lines[side]) if start < len(t)]
        text = next((t for t in candidates if PEM_BEGIN in t), candidates[0])
        return scope, pem_start_shape(text)

    def record_block(self, block_key: tuple[str, int], rec: "_Recorder", *, context: bool = False) -> None:
        side, start = block_key
        scope, shape = self.block_shape(side, start)
        pem = start in self.pem_starts[side]
        rec.block(block_key, scope, shape, self.block_length(side, start), context=context, pem=pem)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


# --- 構造化差分(基準1) ---


def _judge_line(text: str, ancestors: tuple[str, ...], masker: Masker, sides: _Sides, rec: _Recorder) -> str | _Drop:
    hit = sides.pem_text.get(text.strip())
    if hit is not None:
        sides.record_block(hit, rec)
        return _DROP
    judged = masker.judge(text, ancestors)
    if judged.text is None:
        rec.line(judged.shape or "")
        return _DROP
    return judged.text


def _judge_parent(parent: str, ancestors: Sequence[str], masker: Masker, sides: _Sides, rec: _Recorder) -> str | None:
    """見出しに出す祖先の行の判定。ブロックの中の行は送らない(送らないと決めた行を見出しで送る側に戻さない)。"""
    hit = sides.pem_text.get(parent.strip())
    if hit is not None:
        sides.record_block(hit, rec, context=True)
        return None
    judged = masker.judge(parent, ancestors)
    if judged.text is None:
        rec.line(judged.shape or "", context=True)
        return None
    return judged.text


def _judge_field(
    value: str, prefix: str, ancestors: tuple[str, ...], masker: Masker, sides: _Sides, rec: _Recorder
) -> str | _Drop:
    """行の頭の語を外して持つ項目は、語を前に付けた元の行の形で判定し、送るときは語を外す。"""
    judged = _judge_line(prefix + value, ancestors, masker, sides, rec)
    if judged is _DROP or not prefix:
        return judged
    if judged.startswith(prefix):
        return judged[len(prefix):]
    # 伏せた位置が前に付けた語にかかった。値の形に戻せないので送らない
    rec.line(f"[{scope_label(ancestors)}] {prefix}…")
    return _DROP


def _mask_tree(value: Any, path: str, masker: Masker, sides: _Sides, rec: _Recorder) -> Any:
    if value is None:
        return None
    if path in fields.MASKED_FIELDS:
        return masker.mask_value(value)
    ancestors = fields.line_ancestors(path)
    if ancestors is not None and isinstance(value, str):
        return _judge_field(value, fields.line_prefix(path), ancestors, masker, sides, rec)
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            masked = _mask_tree(item, fields.child(path, str(key)), masker, sides, rec)
            if masked is _DROP:  # 項目の値が送れない(ACL のエントリの text など)なら、その辞書ごと送らない
                return _DROP
            out[str(key)] = masked
        return out
    if isinstance(value, (list, tuple)):
        items = [_mask_tree(item, f"{path}[]", masker, sides, rec) for item in value]
        return [item for item in items if item is not _DROP]
    return value


def _value_path(diff: Diff, value: Any) -> str:
    base = fields.diff_base(diff["category"], diff["path"])
    # 集合として比べるリストの増減は、要素1つが値になる
    if base in _LIST_FIELDS and not isinstance(value, (list, tuple)):
        return f"{base}[]"
    return base


def _structured_line(diff: Diff, masker: Masker, sides: _Sides, rec: _Recorder) -> str | None:
    op = diff["op"]
    if diff["category"] == "unparsed":
        line_path = diff["before"] if op == "removed" else diff["after"]
        *parents, text = line_path
        masked = _judge_line(text, tuple(p.strip() for p in parents), masker, sides, rec)
        if masked is _DROP:
            return None
        # 祖先の行も送る内容に入るため、同じ判定を通す(送れない祖先は文脈の行として数えて省く)
        shown = []
        for depth, parent in enumerate(parents):
            judged = _judge_parent(parent, tuple(p.strip() for p in parents[:depth]), masker, sides, rec)
            shown.append(_M["omitted"] if judged is None else judged.strip())
        return f"[{op}] unparsed: {' > '.join([*shown, str(masked).strip()])}"
    before = _mask_tree(diff["before"], _value_path(diff, diff["before"]), masker, sides, rec)
    after = _mask_tree(diff["after"], _value_path(diff, diff["after"]), masker, sides, rec)
    if before is _DROP or after is _DROP:
        return None
    where = diff["category"] + (f" {diff['name']}" if diff["name"] else "")
    item = " > ".join(diff["path"])
    return f"[{op}] {where}{' / ' + item if item else ''}: {_dump(before)} -> {_dump(after)}"


def _structured_body(diffs: list[Diff], masker: Masker, sides: _Sides, rec: _Recorder) -> tuple[list[str], int]:
    out: list[str] = []
    for diff in diffs:
        lines_before = rec.lines
        line = _structured_line(diff, masker, sides, rec)
        if line is None:
            continue
        dropped = rec.lines - lines_before
        out.append(line + (_M["dropped"].format(count=dropped) if dropped else ""))
    return out, len(out)


# --- show の構造化差分 ---


def _show_gate(diff: dict[str, Any], masker: Masker) -> Any:
    """show の差分の行で、秘密の列の最初の列の名前。無ければ None。

    秘密の列は、秘密語を含む値(masker.gated)のある列か、名前が秘密語を含み(masker.is_secret_column。`COMMUNITY` など)
    空でない値のある列(REQ-277)。行ごと(before と after の両方)に見るので、別の列だけが変わった差分も、同じ行に
    秘密の列の値があれば送らない。
    AI に送る道(_show_body)と、人に渡す出力(redact。ADR-0156 決定2)の両方で使う。
    """
    rows = [r for r in (diff.get("before"), diff.get("after")) if r]
    for row in rows:
        for column, value in row.items():
            values = value if isinstance(value, list) else [value]
            if any(isinstance(v, str) and masker.gated(v) for v in values):
                return column
            if masker.is_secret_column(column) and any(
                v is not None and str(v).strip() for v in values
            ):
                return column
    return None


def _show_body(diffs: list[dict[str, Any]], masker: Masker, rec: _Recorder) -> tuple[list[str], int]:
    """show の行には表を持たない。秘密語を含む値があれば、その差分は送らない(REQ-16 Q5)。"""
    out: list[str] = []
    for diff in diffs:
        found = _show_gate(diff, masker)
        if found is not None:
            rec.line(_T["show_gate_shape"].format(column=found))
            continue
        label = diff.get("key") if diff.get("key") is not None else _M["whole_row"]
        if diff["op"] == "changed":
            detail = _dump(diff["fields"])
        else:
            detail = _dump(diff["before"] if diff["op"] == "removed" else diff["after"])
        out.append(f"[{diff['op']}] {label}: {detail}")
    return out, len(out)


# --- 行単位の差分 ---


def _runs(diffs: list[dict[str, Any]]) -> list[tuple[str, list[int]]]:
    """同じ側で行番号が続く差分を1つのまとまりにする。返すのは (側, 0始まりの位置の列)。"""
    runs: list[tuple[str, list[int]]] = []
    for d in diffs:
        side = "before" if d["op"] == "removed" else "after"
        index = (d["before_line_no"] if side == "before" else d["after_line_no"]) - 1
        if runs and runs[-1][0] == side and runs[-1][1][-1] == index - 1:
            runs[-1][1].append(index)
        else:
            runs.append((side, [index]))
    return runs


def _raw_line(side: str, index: int, masker: Masker, sides: _Sides, rec: _Recorder, *, context: bool) -> str | None:
    blocks = sides.blocks[side]
    if index in blocks:
        sides.record_block((side, blocks[index]), rec, context=context)
        return None
    judged = masker.judge(sides.lines[side][index], sides.ancestors_at(side, index))
    if judged.text is None:
        rec.line(judged.shape or "", context=context)
        return None
    return judged.text


def _raw_body(
    diffs: list[dict[str, Any]], all_diffs: list[dict[str, Any]], masker: Masker, sides: _Sides, rec: _Recorder
) -> tuple[list[str], int]:
    changed = {"before": set(), "after": set()}
    for d in all_diffs:
        if d["op"] == "removed":
            changed["before"].add(d["before_line_no"] - 1)
        else:
            changed["after"].add(d["after_line_no"] - 1)
    out: list[str] = []
    sent = 0
    for side, positions in _runs(diffs):
        mark = "-" if side == "before" else "+"
        body: list[str] = []
        for index in positions:
            text = _raw_line(side, index, masker, sides, rec, context=False)
            if text is not None:
                body.append(f"{mark} {index + 1:5d}  {text}")
                sent += 1
        if not body:
            continue
        lines = sides.lines[side]
        first, last = positions[0], positions[-1]
        side_label = _SIDE_LABEL[side]
        if first != last:
            head = [_M["run_many"].format(side=side_label, first=first + 1, last=last + 1)]
        else:
            head = [_M["run_one"].format(side=side_label, first=first + 1)]
        parents = sides.ancestors_at(side, first)
        for depth, parent in enumerate(parents):
            judged = _judge_parent(parent, parents[:depth], masker, sides, rec)
            head.append(_M["parent"].format(text=_M["omitted"] if judged is None else judged))
        before_ctx: list[str] = []
        for index in range(first - 1, max(first - 1 - CONTEXT_LINES, -1), -1):
            if index in changed[side]:
                break
            text = _raw_line(side, index, masker, sides, rec, context=True)
            before_ctx.insert(0, f"  {index + 1:5d}  {text}" if text is not None else f"  {index + 1:5d}  {_M['omitted']}")
        after_ctx: list[str] = []
        for index in range(last + 1, min(last + 1 + CONTEXT_LINES, len(lines))):
            if index in changed[side]:
                break
            text = _raw_line(side, index, masker, sides, rec, context=True)
            after_ctx.append(f"  {index + 1:5d}  {text}" if text is not None else f"  {index + 1:5d}  {_M['omitted']}")
        out += [*head, *before_ctx, *body, *after_ctx]
    return out, sent


def _after_text(masker: Masker, sides: _Sides, rec: _Recorder) -> AfterText:
    """変更後の全部の行を、差分と同じ判定(_raw_line の文脈の行)に通した全文。続けて除いた行は省略の1行にまとめる。"""
    out: list[str] = []
    sent = 0
    omitted = 0
    previous_omitted = False
    for index in range(len(sides.lines["after"])):
        text = _raw_line("after", index, masker, sides, rec, context=True)
        if text is None:
            omitted += 1
            if not previous_omitted:
                out.append(FULL_TEXT_OMITTED)
            previous_omitted = True
            continue
        out.append(text)
        sent += 1
        previous_omitted = False
    return AfterText("\n".join(out), sent, omitted)


# --- 単純比較の設定の 1 行(ADR-0166 決定11。REQ-227 §6) ---
# llm_analyzer は messages を読まないので、言葉をここに置く(画面の言葉 messages.RAW_* と同じことをテストで確かめる)
# REQ-228 §8.3 の移動・比べ方の言葉も含む(画面の言葉 messages.RAW_MOVES_LABEL などと同じことをテストで確かめる)。
# REQ-260: 言語で 1 つ選ぶ(日本語の版は今までの文と 1 字も変えない。英語の版の名前は messages_en.py の画面の名前と同じ)
_RAW_OPTIONS_JA = {
    "head": "単純比較の設定: ",
    "ignore": "無視する行 {count} 件",
    "replace": "置き換え {count} 組",
    "space": {
        "off": "空白も比べる",
        "trailing": "行末の空白を無視",
        "amount": "空白の数を無視",
        "all": "空白をすべて無視",
    },
    "blank": "空行を無視",
    "case": "大文字と小文字を無視",
    "separator": "・",
    "tail": "。無視した行 {ignored} 行と、同じとみなした行 {equated} 組は差分に入れていません",
    "moves": "移動を検出",
    "algorithm": {"minimal": "比べ方: 最小", "patience": "比べ方: patience"},
    "algorithm_fell_back": "比べ方: 最小（変更が多いため標準で比べました）",
    "moves_count": "移動 {count} か所",
    "moves_skipped": "移動は差分が多いため探していません",
    "sentence": "。",
}
_RAW_OPTIONS_EN = {
    "head": "Simple comparison settings: ",
    "ignore": "Ignore lines: {count}",
    "replace": "Replacements: {count}",
    "space": {
        "off": "Compare whitespace",
        "trailing": "Ignore trailing whitespace",
        "amount": "Ignore amount of whitespace",
        "all": "Ignore all whitespace",
    },
    "blank": "Ignore blank lines",
    "case": "Ignore case",
    "separator": ", ",
    "tail": ". {ignored} ignored lines and {equated} equated pairs are not included in the differences",
    "moves": "Detect moves",
    "algorithm": {"minimal": "Algorithm: minimal", "patience": "Algorithm: patience"},
    "algorithm_fell_back": "Algorithm: minimal (compared with standard because there are many changes)",
    "moves_count": "Moves: {count}",
    "moves_skipped": "Moves were not searched because there are many differences.",
    "sentence": ". ",
}
_R = i18n.choose(_RAW_OPTIONS_JA, _RAW_OPTIONS_EN)
RAW_OPTIONS_HEAD = _R["head"]
RAW_OPTIONS_IGNORE = _R["ignore"]
RAW_OPTIONS_REPLACE = _R["replace"]
RAW_OPTIONS_SPACE = _R["space"]
RAW_OPTIONS_BLANK = _R["blank"]
RAW_OPTIONS_CASE = _R["case"]
RAW_OPTIONS_SEPARATOR = _R["separator"]
RAW_OPTIONS_TAIL = _R["tail"]
RAW_OPTIONS_MOVES = _R["moves"]
RAW_OPTIONS_ALGORITHM = _R["algorithm"]
RAW_OPTIONS_ALGORITHM_FELL_BACK = _R["algorithm_fell_back"]
RAW_OPTIONS_MOVES_COUNT = _R["moves_count"]
RAW_OPTIONS_MOVES_SKIPPED = _R["moves_skipped"]
RAW_OPTIONS_SENTENCE = _R["sentence"]


def raw_options_line(record: RawOptionsRecord) -> str:
    """送る文の頭の 1 行。既定でない設定の名前と件数だけ(利用者の式・置き換える文字は入れない)。"""
    plain = record.options
    parts: list[str] = []
    if plain.get("ignore_lines"):
        parts.append(RAW_OPTIONS_IGNORE.format(count=len(plain["ignore_lines"])))
    if plain.get("replacements"):
        parts.append(RAW_OPTIONS_REPLACE.format(count=len(plain["replacements"])))
    space = plain.get("whitespace", "off")
    if space != "off":
        parts.append(RAW_OPTIONS_SPACE.get(space, ""))
    if plain.get("ignore_blank_lines"):
        parts.append(RAW_OPTIONS_BLANK)
    if plain.get("ignore_case"):
        parts.append(RAW_OPTIONS_CASE)
    if plain.get("detect_moves"):
        parts.append(RAW_OPTIONS_MOVES)
    algorithm = plain.get("algorithm", "standard")
    if algorithm == "minimal" and record.algorithm_used == "standard":
        parts.append(RAW_OPTIONS_ALGORITHM_FELL_BACK)
    elif algorithm in RAW_OPTIONS_ALGORITHM:
        parts.append(RAW_OPTIONS_ALGORITHM[algorithm])
    ignored = sum(len(v) for v in record.ignored.values())
    line = (
        RAW_OPTIONS_HEAD
        + RAW_OPTIONS_SEPARATOR.join(p for p in parts if p)
        + RAW_OPTIONS_TAIL.format(ignored=ignored, equated=len(record.equated))
    )
    # 行の移動(REQ-228 §8.3)。数だけ(移動した行は差分の行のまま送る。送った数・打ち切りの数には入れない)
    if record.moves_skipped:
        line += RAW_OPTIONS_SENTENCE + RAW_OPTIONS_MOVES_SKIPPED
    elif record.moves:
        line += RAW_OPTIONS_SENTENCE + RAW_OPTIONS_MOVES_COUNT.format(count=len(record.moves))
    return line


def source_of(
    structured: CompareResult | None, raw: RawCompareResult | None, show: ShowCompareResult | None
) -> tuple[LlmSource, list[Any]] | None:
    """送る差分の種類と一覧。送るものが無ければ None。

    構造化比較(または show の構造化)が動いたときは、その差分だけを見る(0 件なら行の差分があっても送らない。ADR-0080 決定3)。
    """
    if structured is not None:
        return ("structured", list(structured.diffs)) if structured.diffs else None
    if show is not None and show.method == "structured":
        return ("show", list(show.diffs)) if show.diffs else None
    if raw is not None and raw.diffs:
        return "raw", list(raw.diffs)
    return None


def build_payload(
    *,
    structured: CompareResult | None,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
    before_lines: Sequence[str],
    after_lines: Sequence[str],
    masker: Masker,
    max_diffs: int,
    before_source: Sequence[str] | None = None,
    after_source: Sequence[str] | None = None,
    include_after_text: bool = False,
) -> Payload | None:
    """送る内容を組み立てる。差分が無ければ None。打ち切りは伏せる前の件数で決める。

    include_after_text が真なら、差分の本文を作った後に変更後の全文も作る(ADR-0158。show の比較でも作る。ADR-0182 決定2)。
    偽なら今と同じ結果。
    """
    found = source_of(structured, raw, show)
    if found is None:
        return None
    source, diffs = found
    selected = diffs[:max_diffs]
    rec = _Recorder()
    sides = _Sides(before_lines, after_lines, before_source, after_source, masker)
    if source == "structured":
        lines, sent = _structured_body(selected, masker, sides, rec)
    elif source == "show":
        lines, sent = _show_body(selected, masker, rec)
    else:
        lines, sent = _raw_body(selected, diffs, masker, sides, rec)
        if raw is not None and raw.options is not None:
            # 単純比較の設定の 1 行(ADR-0166)。件数だけで、利用者の式と置き換える文字は入れない。送った数には数えない
            lines = [raw_options_line(raw.options), *lines]
    # 全文は差分の後に、同じ masker・sides・rec で作る(同じ値は差分と同じ <secret:N>。数は全文の後で取る)
    after_text = _after_text(masker, sides, rec) if include_after_text else None
    return Payload(source, "\n".join(lines), len(diffs), sent, len(diffs) > max_diffs, rec.result(), after_text)


def build_after_text(
    *,
    before_lines: Sequence[str],
    after_lines: Sequence[str],
    masker: Masker,
    before_source: Sequence[str] | None = None,
    after_source: Sequence[str] | None = None,
) -> AfterText:
    """変更後の全文だけを作る(全部の組をまとめて送った答えへの追加の質問。ADR-0152 決定10)。

    判定は build_payload の全文と同じ(_Sides と _after_text をそのまま使う)。masker は差分の本文を作り直した判定器を渡す
    (同じ値は最初の送信と同じ <secret:N> になる)。
    """
    rec = _Recorder()
    sides = _Sides(before_lines, after_lines, before_source, after_source, masker)
    return _after_text(masker, sides, rec)


# --- 全部の組をまとめて1回で送る(ADR-0152 決定3・決定4・決定9) ---

# 区切りの行と差分の無かったコマンドの行で、コマンドが分からない組の名前
# (REQ-260: 印は言語で 1 つ選ぶ。区切りの行の形は prompt.WHOLE_SEPARATOR_QUOTE が引く形と同じ並び)
WHOLE_UNKNOWN_COMMAND = _M["unknown_command"]
# 判定できずに送らない名前の代わり(差分の文脈の行と同じ文)
WHOLE_NAME_OMITTED = FULL_TEXT_OMITTED
WHOLE_NO_DIFF_HEAD = _M["no_diff_head"]
WHOLE_NO_DIFF_NONE = _M["no_diff_none"]
WHOLE_NAME_SEPARATOR = _M["name_separator"]
_KIND_LABEL = {"config": "config", "show": "show"}


def whole_header(name: str, kind: str) -> str:
    """組の区切りの行。name は判定を通した後の名前。"""
    return _M["whole_header"].format(name=name, kind=kind)


@dataclass(frozen=True)
class WholeSection:
    """全部の組をまとめて送るときの組1つ(ADR-0152 決定3)。行は core の _llm_analysis が analyze に渡すのと同じ。

    command は変更前の区間の元のコマンドの文(コマンド不明は None)、kind は組の種類(分からなければ None)。
    """

    command: str | None
    kind: Literal["config", "show"] | None
    structured: CompareResult | None
    raw: RawCompareResult | None
    show: ShowCompareResult | None
    before_lines: Sequence[str]
    after_lines: Sequence[str]
    before_source: Sequence[str] | None
    after_source: Sequence[str] | None


@dataclass(frozen=True)
class WholeBody:
    """全部の組をまとめた差分の本文(ADR-0152 決定3)。body は区切りの行と各組の差分と、最後の「差分の無かったコマンド」の行。

    本文に入れた組が無ければ body は空で、最後の行も作らない(名前だけでは送らない。決定9)。
    件数は差分が1件以上あった組(build_payload が None でない組)の和。sent は本文に入れた組の番号(sections の位置)、
    sources は本文に入れた組の差分の種類(出てきた順、重ねない)。sent_range は組ごとの min(件数, 上限) の和、
    omitted は組ごとに上限を超えて送らなかった件数の和。structured_zero は構造化比較が動いて 0 件の組(行の差分はある)があるか。
    """

    body: str
    sources: tuple[LlmSource, ...]
    diff_total: int
    sent_diffs: int
    sent_range: int
    omitted: int
    truncated: bool
    unsent: Unsent
    sent: tuple[int, ...]
    found: bool
    structured_zero: bool
    # 本文に入れた組の種類(組の順。区切りの行の config / show と同じ。ADR-0152 決定5 の「送る組が全部 show」の判定に使う)
    kinds: tuple[str, ...] = ()


def _merge_unsent(parts: Sequence[Unsent]) -> Unsent:
    """組ごとの送らなかったものを足す(形は core.batch_llm._merge_shapes と同じ合算と並び)。"""
    totals: dict[tuple[UnsentKind, str, str], list[int]] = {}
    for part in parts:
        for s in part.shapes:
            count = totals.setdefault((s.kind, s.scope, s.shape), [0, 0])
            count[0] += s.count
            count[1] += s.lines
    shapes = [UnsentShape(kind, scope, shape, c, n) for (kind, scope, shape), (c, n) in totals.items()]
    return Unsent(
        lines=sum(p.lines for p in parts),
        context_lines=sum(p.context_lines for p in parts),
        blocks=sum(p.blocks for p in parts),
        shapes=sorted(shapes, key=lambda s: (-s.count, s.scope, s.shape)),
    )


def _judge_name(command: str | None, masker: Masker, rec: _Recorder) -> str:
    """区切りの行と差分の無かったコマンドの行に出す名前(ADR-0152 決定4)。秘密語の判定(masker.judge)を通す。

    送れない名前は WHOLE_NAME_OMITTED にし、送らなかった文脈の行として数える。コマンド不明は WHOLE_UNKNOWN_COMMAND。
    """
    if command is None:
        return WHOLE_UNKNOWN_COMMAND
    judged = masker.judge(command, ())
    if judged.text is None:
        rec.line(judged.shape or "", context=True)
        return WHOLE_NAME_OMITTED
    return judged.text


def whole_body(
    sections: Sequence[WholeSection], masker: Masker, max_diffs: int, *, unchanged: Sequence[str | None] = ()
) -> WholeBody:
    """全部の組の差分を1つの本文にする(ADR-0152 決定3・決定4・決定9)。masker は全部の組で1つ(同じ値は同じ <secret:N>)。

    順は固定(追加の質問で作り直すときに同じ番号になるように): 組の順に、区切りの行の名前を判定してから build_payload を呼ぶ。
    build_payload が None の組(差分なし・構造化が 0 件)と、送れる差分が 0 件の組は本文に入れない(その組の名前の記録も数えない)。
    本文に入れた組があるときだけ、最後に unchanged(両側があって差分の無かった組のコマンド)の名前を順に判定して1行足す。
    名前は件数と上限に数えない。
    """
    parts: list[str] = []
    unsents: list[Unsent] = []
    sources: list[LlmSource] = []
    kinds: list[str] = []
    sent: list[int] = []
    diff_total = sent_diffs = sent_range = omitted = 0
    truncated = found = structured_zero = False
    for index, section in enumerate(sections):
        names = _Recorder()
        name = _judge_name(section.command, masker, names)
        payload = build_payload(
            structured=section.structured,
            raw=section.raw,
            show=section.show,
            before_lines=section.before_lines,
            after_lines=section.after_lines,
            before_source=section.before_source,
            after_source=section.after_source,
            masker=masker,
            max_diffs=max_diffs,
        )
        if payload is None:
            if section.raw is not None and section.raw.diffs:
                structured_zero = True
            continue
        found = True
        diff_total += payload.diff_total
        sent_range += min(payload.diff_total, max_diffs)
        omitted += max(0, payload.diff_total - max_diffs)
        truncated = truncated or payload.truncated
        unsents.append(payload.unsent)
        if payload.sent_diffs == 0:
            continue
        sent_diffs += payload.sent_diffs
        unsents.append(names.result())
        kind = section.kind or ("show" if payload.source == "show" else "config")
        kinds.append(kind)
        parts.append("\n".join([whole_header(name, _KIND_LABEL[kind]), payload.body]))
        sent.append(index)
        if payload.source not in sources:
            sources.append(payload.source)
    if parts:
        names = _Recorder()
        shown = [_judge_name(command, masker, names) for command in unchanged]
        unsents.append(names.result())
        parts.append(WHOLE_NO_DIFF_HEAD + (WHOLE_NAME_SEPARATOR.join(shown) if shown else WHOLE_NO_DIFF_NONE))
    return WholeBody(
        body="\n\n".join(parts),
        sources=tuple(sources),
        diff_total=diff_total,
        sent_diffs=sent_diffs,
        sent_range=sent_range,
        omitted=omitted,
        truncated=truncated,
        unsent=_merge_unsent(unsents),
        sent=tuple(sent),
        found=found,
        structured_zero=structured_zero,
        kinds=tuple(kinds),
    )
