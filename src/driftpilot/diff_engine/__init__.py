"""2つの config の structured 比較と、その安全網(ADR-0007・ADR-0009)。"""

from collections import Counter

from hier_config import HConfig
from hier_config.exceptions import DuplicateChildError

from driftpilot.config_parser import IR, FlatTree, parse_config_with_tree
from driftpilot.config_parser.tree import duplicate_path
from driftpilot.diff_engine.compare import compare
from driftpilot.diff_engine.normalization import NormalizationRule, builtin_rules, load_rules
from driftpilot.diff_engine.raw import RawCompareResult, compare_raw
from driftpilot.diff_engine.safety import check_empty_categories, check_tree_mismatch
from driftpilot.diff_engine.show import ShowCompareResult, compare_show
from driftpilot.diff_engine.types import CompareResult, Diff, DuplicateLineError, SafetyWarning, Side

__all__ = [
    "CompareResult",
    "Diff",
    "DuplicateLineError",
    "NormalizationRule",
    "RawCompareResult",
    "SafetyWarning",
    "ShowCompareResult",
    "builtin_rules",
    "compare",
    "compare_configs",
    "compare_raw",
    "compare_show",
    "load_rules",
]


def compare_configs(before_text: str, after_text: str, platform: str | None = None) -> CompareResult:
    """config テキスト2つを structured 比較し、差分・未解釈行数・警告をまとめて返す。

    platform は既定でないパーサーの系統(fujitsu_sir・fortinet など。config_parser/platforms.py)。省略は A型。
    機種の正規化ルール(ENC の値など)はここでは当てない。当てた本文を渡すのは core の役目(REQ-22 §3b)。
    A型の木にできない重複(ルート直下など)は DuplicateLineError(REQ-26 §B-2)。
    """
    before_ir, before_tree = _parse_side(before_text, platform, "before")
    after_ir, after_tree = _parse_side(after_text, platform, "after")
    diffs = compare(before_ir, after_ir)
    warnings = [
        *_duplicate_warnings(before_tree, "before"),
        *_duplicate_warnings(after_tree, "after"),
        *check_empty_categories(before_tree, before_ir, "before"),
        *check_empty_categories(after_tree, after_ir, "after"),
        *check_tree_mismatch(before_tree, after_tree, diffs),
    ]
    return CompareResult(
        diffs=diffs,
        unparsed_counts={"before": len(before_ir["unparsed"]), "after": len(after_ir["unparsed"])},
        warnings=warnings,
    )


def _parse_side(text: str, platform: str | None, side: Side) -> tuple[IR, HConfig | FlatTree]:
    """1つの側を IR と木にする。ライブラリの重複の例外は、場所と行番号を持つ DuplicateLineError にして投げ直す。"""
    try:
        return parse_config_with_tree(text, platform)
    except DuplicateChildError as exc:
        path = duplicate_path(exc)
        if path is None:
            raise DuplicateLineError(side, (), None, ()) from exc
        scope, line = path[:-1], path[-1]
        raise DuplicateLineError(side, scope, line, _duplicate_line_numbers(text, scope, line)) from exc


def _duplicate_line_numbers(text: str, scope: tuple[str, ...], line: str) -> tuple[int, ...]:
    """重複した行の行番号(区間の本文の1始まり)。同じ枝の同じ本文の行が2つ以上見つかったときだけ返し、それ以外は空。

    失敗の文面に添えるためだけに使う(REQ-26 §B-2)。警告の行番号には使わない(ADR-0030 決定5)。
    字下げで親をたどり、親の並び(空白をまとめた本文)が scope と完全に同じ行だけを数える。前処理は真似ないので、
    前処理で消える行が間に挟まると親の並びが合わずに数えないことがある。そのときは2つに届かず、行番号を省く。
    """
    numbers: list[int] = []
    parents: list[tuple[int, str]] = []  # (字下げの幅, 空白をまとめた本文)
    for number, raw in enumerate(text.splitlines(), start=1):
        normalized = " ".join(raw.split())
        if not normalized:
            continue
        indent = len(raw) - len(raw.lstrip())
        while parents and parents[-1][0] >= indent:
            parents.pop()
        if normalized == line and tuple(t for _, t in parents) == scope:
            numbers.append(number)
        parents.append((indent, normalized))
    return tuple(numbers) if len(numbers) >= 2 else ()


def _duplicate_warnings(tree: HConfig | FlatTree, side: Side) -> list[SafetyWarning]:
    """木にある重複を警告にする。detail はスコープと行番号(と件数)だけ(値を含めない)。

    B型の木は記録した重複を行番号つきで返す。A型の木は組んだ木を歩き、ルートでない親の下で同じ本文の子の組ごとに
    1件出す。A型の木は元の行番号を持たないので lines は空(ADR-0030 決定5)。
    """
    if isinstance(tree, FlatTree):
        return [
            SafetyWarning(
                kind="duplicate_line",
                side=side,
                category=None,
                detail={"scope": " > ".join(d.scope) if d.scope else None, "lines": list(d.lines)},
            )
            for d in tree.warnings
        ]
    warnings: list[SafetyWarning] = []
    for parent in tree.all_children():
        counts = Counter(child.text for child in parent.children)
        for count in counts.values():
            if count >= 2:
                detail = {"scope": " > ".join(parent.path()), "lines": [], "count": count}
                warnings.append(SafetyWarning(kind="duplicate_line", side=side, category=None, detail=detail))
    return warnings
