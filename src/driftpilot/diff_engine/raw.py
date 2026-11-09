"""行単位の raw 比較(ADR-0007)。structured 比較とは独立で、パーサーを呼ばない。"""

import difflib
from dataclasses import dataclass, field
from typing import Literal, TypedDict

from driftpilot.diff_engine.normalization import (
    NormalizationRule,
    apply_rules_traced,
    select_rules,
)
from driftpilot.diff_engine import raw_algorithms, raw_moves
from driftpilot.diff_engine.raw_moves import RawMove
from driftpilot.raw_keys import (
    DEFAULT_RAW_OPTIONS,
    Algorithm,
    RawOptions,
    compute_keys,
    options_to_plain,
    require_valid,
)


class RawDiffLine(TypedDict):
    op: Literal["added", "removed"]
    before_line_no: int | None
    after_line_no: int | None
    text: str


@dataclass
class NormalizationRecord:
    enabled_rule_ids: list[str] = field(default_factory=list)
    # {"before": {id: 行数}, "after": {id: 行数}}
    applied: dict[str, dict[str, int]] = field(default_factory=dict)
    # {"before": {id: [元の行番号]}, "after": {id: [元の行番号]}}
    applied_lines: dict[str, dict[str, list[int]]] = field(default_factory=dict)


class RawReplacementPlain(TypedDict):
    pattern: str
    replacement: str


class RawOptionsPlain(TypedDict):
    """単純比較の設定の保存の形(raw_keys.options_to_plain。REQ-227 §2.3)。"""

    ignore_lines: list[str]
    replacements: list[RawReplacementPlain]
    whitespace: str
    ignore_blank_lines: bool
    ignore_case: bool
    detect_moves: bool  # REQ-228
    algorithm: str  # REQ-228(standard・minimal・patience)


class EquatedPair(TypedDict):
    before_line_no: int
    after_line_no: int


@dataclass
class RawOptionsRecord:
    """単純比較の設定を使ったときの記録(ADR-0166 決定11・12)。

    ignored は側ごとの無視した行の番号(1 始まり、小さい順)。equated は鍵が同じで、行の文字が違う組
    (機種のルールの後の行どうしを比べる。機種のルールで同じになった行は入らない)。

    REQ-228: moves は見つけた行の移動(detect_moves が偽なら [])。moves_skipped は差分の行が多くて探さなかったとき真。
    algorithm_used は実際に使った比べ方(minimal が上限を超えて standard に戻ったときは "standard")。作るときに省けば設定の algorithm。
    """

    options: RawOptionsPlain
    ignored: dict[str, list[int]]  # {"before": [行番号], "after": [行番号]}
    equated: list[EquatedPair]
    moves: list[RawMove] = field(default_factory=list)
    moves_skipped: bool = False
    algorithm_used: Algorithm | None = None

    def __post_init__(self) -> None:
        if self.algorithm_used is None:
            self.algorithm_used = self.options.get("algorithm", "standard")  # type: ignore[assignment]


@dataclass
class RawCompareResult:
    diffs: list[RawDiffLine]
    kind: str
    normalization: NormalizationRecord
    options: RawOptionsRecord | None = None  # 既定の設定なら None


def compare_raw(
    before_text: str,
    after_text: str,
    *,
    kind: str,
    rules: list[NormalizationRule] | None = None,
    enabled_ids: list[str] | None = None,
    platform: str | None = None,
    options: RawOptions = DEFAULT_RAW_OPTIONS,
) -> RawCompareResult:
    """2つのテキストを行単位で比較する。正規化は kind と enabled_ids で決まる。

    platform は config の機種(kind=config のときだけ渡す)。一致する機種のルールが対象に入る(ADR-0025 決定5)。
    options は単純比較の設定(ADR-0166)。既定なら今までと同じ比べ方で、結果の options は None。
    既定でなければ機種のルールの後の行から鍵を作って比べ、差分の文字と行番号は元の行のものにする。
    誤りのある設定は RawOptionsError(入口で先に止める。ここは守り)。
    """
    if not options.is_default():
        require_valid(options)
    selected = select_rules(rules or [], kind, enabled_ids, platform)
    before_lines, before_changed = apply_rules_traced(before_text, selected)
    after_lines, after_changed = apply_rules_traced(after_text, selected)

    if options.is_default():
        diffs = _line_diffs(before_lines, after_lines)
        option_record = None
    else:
        diffs, option_record = _keyed_diffs(before_lines, after_lines, options)

    record = NormalizationRecord(
        enabled_rule_ids=[r.id for r in selected],
        applied={
            "before": {k: len(v) for k, v in before_changed.items()},
            "after": {k: len(v) for k, v in after_changed.items()},
        },
        applied_lines={"before": before_changed, "after": after_changed},
    )
    return RawCompareResult(diffs=diffs, kind=kind, normalization=record, options=option_record)


def _line_diffs(before_lines: list[str], after_lines: list[str]) -> list[RawDiffLine]:
    """既定の設定の比べ方(行そのものを比べる。REQ-227 より前と同じ)。"""
    diffs: list[RawDiffLine] = []
    matcher = difflib.SequenceMatcher(None, before_lines, after_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            diffs.append(
                {
                    "op": "removed",
                    "before_line_no": i + 1,
                    "after_line_no": None,
                    "text": before_lines[i],
                }
            )
        for j in range(j1, j2):
            diffs.append(
                {
                    "op": "added",
                    "before_line_no": None,
                    "after_line_no": j + 1,
                    "text": after_lines[j],
                }
            )
    return diffs


def _keyed_diffs(
    before_lines: list[str], after_lines: list[str], options: RawOptions
) -> tuple[list[RawDiffLine], RawOptionsRecord]:
    """鍵で比べる(ADR-0166 決定3)。無視した行(鍵が None)は比べない。番号と文字は元の行のもの。"""
    before_keys, after_keys = compute_keys(before_lines, after_lines, options)
    before_kept = [(i, k) for i, k in enumerate(before_keys) if k is not None]
    after_kept = [(j, k) for j, k in enumerate(after_keys) if k is not None]
    # 比べ方(REQ-228 §4)。standard なら REQ-227 と同じ SequenceMatcher(autojunk=False) の呼び出し
    found, used = raw_algorithms.opcodes([k for _, k in before_kept], [k for _, k in after_kept], options.algorithm)
    diffs: list[RawDiffLine] = []
    equated: list[EquatedPair] = []
    for tag, i1, i2, j1, j2 in found:
        if tag == "equal":
            for (i, _), (j, _) in zip(before_kept[i1:i2], after_kept[j1:j2], strict=True):
                if before_lines[i] != after_lines[j]:
                    equated.append({"before_line_no": i + 1, "after_line_no": j + 1})
            continue
        for i, _ in before_kept[i1:i2]:
            diffs.append({"op": "removed", "before_line_no": i + 1, "after_line_no": None, "text": before_lines[i]})
        for j, _ in after_kept[j1:j2]:
            diffs.append({"op": "added", "before_line_no": None, "after_line_no": j + 1, "text": after_lines[j]})
    ignored = {
        "before": [i + 1 for i, k in enumerate(before_keys) if k is None],
        "after": [j + 1 for j, k in enumerate(after_keys) if k is None],
    }
    moves: list[RawMove] = []
    skipped = False
    if options.detect_moves:
        # 行の移動(REQ-228 §3)。差分の行だけから探し、diffs には削除と追加のまま残す(状態は変わらない)
        removed = [(d["before_line_no"], before_keys[d["before_line_no"] - 1]) for d in diffs if d["op"] == "removed"]
        added = [(d["after_line_no"], after_keys[d["after_line_no"] - 1]) for d in diffs if d["op"] == "added"]
        result = raw_moves.find_moves(removed, added)  # type: ignore[arg-type]  # 差分の行の鍵は None でない
        if result is None:
            skipped = True
        else:
            moves = result
    plain: RawOptionsPlain = options_to_plain(options)  # type: ignore[assignment]
    return diffs, RawOptionsRecord(
        options=plain, ignored=ignored, equated=equated, moves=moves, moves_skipped=skipped, algorithm_used=used
    )
