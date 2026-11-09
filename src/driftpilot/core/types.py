"""core の要求と結果の型(ADR-0015 決定2・決定5)。

要求はパスを持たない。ファイルの読み込みは入口の責務で、core はテキストと表示名の組を受け取る。
結果は警告・注意の文面を持たない(kind と detail の構造だけ)。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from driftpilot.diff_engine import CompareResult, NormalizationRule, RawCompareResult, SafetyWarning, ShowCompareResult
from driftpilot.diff_engine.exposure_check import ExposureNotice, ExposureSide
from driftpilot.diff_engine.normalization import ExclusionReason
from driftpilot.llm_analyzer.types import LlmAnalysis
from driftpilot.raw_keys import DEFAULT_RAW_OPTIONS, RawOptions
from driftpilot.segmentation import InputNote, Segment, SplitResult

# 結果の JSON 契約の版。上げる条件は docs/notes/req-13-core.md。ここ1か所にだけ置く
SCHEMA_VERSION = "1"

Mode = Literal["structured", "raw", "both"]
KindChoice = Literal["config", "show", "auto"]
Status = Literal["no_difference", "difference", "needs_selection", "error"]
SelectionReason = Literal["segment", "ambiguous_format"]
SideName = Literal["before", "after"]
SIDES: tuple[SideName, ...] = ("before", "after")


@dataclass(frozen=True)
class SideInput:
    """比較の片側。表示名(ファイル名や貼り付けの名前)と、読み込み済みのテキスト。

    notes は読み込みで入力の形を戻した知らせ(文字コードなど。ADR-0138 決定7)。core が警告 input_form にする。
    """

    text: str
    name: str
    notes: tuple[InputNote, ...] = ()


@dataclass(frozen=True)
class SideSelection:
    """区間の選択指定。command は原文の部分一致、number は1始まりの順番。両方なしなら自動(1区間のときだけ)。"""

    command: str | None = None
    number: int | None = None


@dataclass(frozen=True)
class CompareRequest:
    before: SideInput
    after: SideInput
    mode: Mode = "both"
    kind: KindChoice = "config"
    platform: str | None = None
    show_command: str | None = None
    prompt: str | None = None
    before_selection: SideSelection = field(default_factory=SideSelection)
    after_selection: SideSelection = field(default_factory=SideSelection)
    rules: list[NormalizationRule] | None = None  # 読み込み済み
    enabled_rule_ids: list[str] | None = None
    # 単純比較の設定(ADR-0166)。単純比較だけに効き、構造化比較は変えない
    raw_options: RawOptions = DEFAULT_RAW_OPTIONS


@dataclass(frozen=True)
class SideSegmentation:
    """片側の切り分け結果。selected は選べた区間(選択待ちの側は None)。"""

    name: str
    split: SplitResult
    selected: Segment | None


@dataclass(frozen=True)
class Candidate:
    """選択待ちの候補1件。"""

    side: SideName
    segment: Segment


@dataclass(frozen=True)
class CompareError:
    """status=error の内容。stage は分岐に使える工程名、message は利用者向けの文。"""

    code: str
    stage: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ComparisonExclusion:
    """比較から外した値の件数と理由(ADR-0025 決定6)。文面は持たない(入口が reason から作る)。

    rule_id は外したルール、counts は側ごとの当たった行数。どちらかの側が1件以上のときだけ結果に入る。
    """

    reason: ExclusionReason
    rule_id: str
    counts: dict[SideName, int]


@dataclass(frozen=True)
class AutoPlatform:
    """機種を自動にしたときの判定(ADR-0151 決定1・2)。

    used は比較に使った系統(fujitsu_sir・fortinet。None は指定なし=Cisco 系)。before・after は片側ごとの判定
    (fujitsu_sir・fortinet・a・None=判定なし)。conflict は両側の判定が違ったこと(AI に送らない)。
    """

    used: str | None
    before: str | None
    after: str | None
    conflict: bool = False


@dataclass(frozen=True)
class Conversation:
    """比較の窓での AI とのやり取り(ADR-0179 決定2)。保存の result.conversation に入る。差分の判定には使わない。

    first_request は最初の依頼の文(打った文か「プロンプト『…』で解析」など。決定1)、profile は送り先の AI プロファイルの
    名前だけ(接続先・キー・CLI の場所は入れない)、turns は追加の質問のやり取り (質問, 回答) の順の並び。
    考えている内容(ADR-0169)は入れない。
    """

    first_request: str | None = None
    profile: str | None = None
    turns: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CompareOutcome:
    status: Status
    mode: Mode
    schema_version: str = SCHEMA_VERSION
    kind: Literal["config", "show"] | None = None
    has_diff: bool = False
    segmentation: dict[SideName, SideSegmentation] | None = None
    structured: CompareResult | None = None
    raw: RawCompareResult | None = None
    show: ShowCompareResult | None = None
    default_exposure: dict[SideName, ExposureSide] | None = None
    warnings: list[SafetyWarning] = field(default_factory=list)
    notices: list[ExposureNotice] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    selection_reason: SelectionReason | None = None
    selection_detail: dict[str, Any] = field(default_factory=dict)
    error: CompareError | None = None
    # LLM 解析の注釈(ADR-0018 決定5)。差分の判定には使わない。比較まで進まなかった結果では None
    llm: LlmAnalysis | None = None
    # 比較から外した値(FortiGate の再暗号化される値など。ADR-0025 決定6)。黙って無視しないために件数を返す
    exclusions: list[ComparisonExclusion] = field(default_factory=list)
    # 機種を自動にして config と決まったときの判定(ADR-0151)。自動でないとき・show のときは None
    auto_platform: AutoPlatform | None = None
    # 比較の窓での AI とのやり取り(ADR-0179 決定2)。保存するときだけ窓が入れる。core は入れない(いつも None)
    conversation: Conversation | None = None


class CompareCancelled(Exception):
    """should_cancel が真を返したときに送出する。status は増やさない。"""
