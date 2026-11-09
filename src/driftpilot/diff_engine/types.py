"""structured 比較の結果の型(ADR-0009 決定2・決定3)。

差分(Diff)は設定の変化だけを持つ。比較の信頼度に関する情報(安全網)は CompareResult の別の枠に置く。
"""

from dataclasses import dataclass, field
from typing import Any, Literal, TypedDict

DiffOp = Literal["added", "removed", "changed"]
# duplicate_line は B型(平坦 config)の重複(REQ-21 §7)。platform_mismatch は指定した系統と中身の見立ての食い違い
# (安全網5。REQ-25)。pager_marks は端末ログから外したページ送りの印の数(ADR-0134 決定2)。
# input_form は読み込みで入力の形を戻したこと(文字コードなど。ADR-0138 決定7)。
# input_hint は入力の様子から当てた案内(折り返しの疑いなど。直していない。ADR-0138 決定7。REQ-189)。
# platform_auto は機種を自動にしたときの判定なし・食い違いの知らせ(ADR-0151 決定2)。
# どれも分岐に使わない列挙のため、足しても schema_version は上げない
WarningKind = Literal[
    "empty_category",
    "tree_diff_mismatch",
    "duplicate_line",
    "platform_mismatch",
    "pager_marks",
    "input_form",
    "input_hint",
    "platform_auto",
    "raw_ignored_many",
]
Side = Literal["before", "after"]


class Diff(TypedDict):
    """差分1件。葉の項目ごとに1件で、ブロック全体の追加・削除は path が空のタプルになる。"""

    op: DiffOp
    category: str
    name: str | None
    path: tuple[str, ...]
    before: Any
    after: Any


@dataclass(frozen=True)
class SafetyWarning:
    """安全網(不自然な0件、ツリー差分との食い違い)の警告1件。文面は持たない(入口が kind と detail から作る)。"""

    kind: WarningKind
    side: Side | None
    category: str | None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CompareResult:
    diffs: list[Diff]
    unparsed_counts: dict[str, int]
    warnings: list[SafetyWarning]


class DuplicateLineError(Exception):
    """A型の木で、ルート直下(または規則の上限より深い親の下)に同じ行が2回あり、ツリーにできなかった(REQ-26 §B-2)。

    持つのは側・親のパス(ルートなら空)・重複した行・行番号。行と親のパスは本文のまま持つ(LLM に送らないため。
    決定者の指示による REQ-26 §B-2 からの変更)。場所が取れなかったときは line が None。
    行番号は同じ枝の同じ本文の行が2つ以上見つかったときだけ。
    """

    def __init__(self, side: Side, scope: tuple[str, ...], line: str | None, lines: tuple[int, ...]) -> None:
        self.side = side
        self.scope = scope
        self.line = line
        self.lines = lines
        super().__init__(self.summary())

    def summary(self) -> str:
        """ライブラリの例外文と同じ形。"""
        if self.line is None:
            return "Found a duplicate section (location unavailable)"
        return f"Found a duplicate section: {(*self.scope, self.line)}"
