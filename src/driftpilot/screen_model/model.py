"""比較画面の論理(REQ-18 決定9)。wx を読まない。

入力から CompareRequest を組み立て、CompareOutcome を表示用のデータ(OutcomeView)に写す。
差分の判定・件数・状態は core の結果から読むだけで、ここで数え直さない(ADR-0022 決定2)。
警告・注意の文面は driftpilot.messages(CLI と共通)から取る。ここに置くのはボタン名・タブ名・案内などの画面の言葉だけ。
"""

import copy
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Literal

import driftpilot.inline_marks as inline_marks
from driftpilot.batch_io import (
    DeviceSplitWritten,
    find_devices,
    multi_filename,
    pair_record,
    write_device_split,
    write_records,
)
from driftpilot.core import (
    AUTO_PLATFORM,
    CONFIG_PLATFORMS,
    DEFAULT_MAX_TOTAL_DIFFS,
    DEFAULT_RAW_OPTIONS,
    MAX_PATTERNS,
    RAW_CONTEXT_DEFAULT,
    RAW_CONTEXT_MAX,
    SHOW_OUTSIDE_TABLE_REASONS,
    CompareCancelled,
    CompareOutcome,
    CompareRequest,
    config_platform_used,
    Conversation,
    MultiCompareOutcome,
    MultiStatus,
    PairResult,
    RawOptionProblem,
    RawOptions,
    RawOptionsRecord,
    Replacement,
    SideInput,
    SideSelection,
    Unpaired,
    analyze_all,
    analyze_compared,
    NO_DISABLED,
    RawDisabled,
    check_raw_options,
    enabled_options,
    prune_disabled,
    compare_all,
    exclude_pair,
    is_error_output,
    exclude_segment,
    multi_overall_status,
    options_to_plain,
    pair_by_hand,
    restore_excluded,
    partner_candidates,
    run_compare,
    wants_all,
    whole_candidates,
)
from driftpilot.core import unpair as unpair_pair
# 全体の答えを組に配る部品は core に置いた(一括比較の保存からも使う。REQ-240 §4)。今までどおりここからも読める
from driftpilot.core import IN_WHOLE, whole_holder, whole_pair_results  # noqa: F401
# ApprovedPromptClient / PromptNotApproved は一括比較と共有するため llm_analyzer に移した(ここからも読める。REQ-27)
from driftpilot.llm_analyzer import (
    ApprovedPromptClient,
    InstructionEdit,
    LLMClient,
    LlmAnalysis,
    LlmRequest,
    PromptNotApproved,  # noqa: F401 - 移す前と同じく screen_model.model からも読める
)
# 答えの読み取りは画面の側からはここ(と batch_model)を通して使う(gui の境界。REQ-204)
from driftpilot.llm_analyzer.answer_format import format_answer, plain_answer
# 人に渡す出力の伏せる判定(ADR-0156 決定2。AI に送るときと同じ判定の道)
from driftpilot.llm_analyzer.redact import (  # noqa: F401 - RedactError は窓が読む
    RedactedResult,
    RedactError,
    plain_result,
    redact_result,
    redact_text,
)
from driftpilot.llm_constants import DEFAULT_MAX_PROMPT_CHARS
from driftpilot.llm_settings import LlmSettings, SettingsError, current_premise, load_settings, premise_text_problem
from driftpilot.llm_settings import build_request as build_llm_request
from driftpilot.messages import (
    SMODEL_TEXTS,
    RAW_RULE_KIND_TEXT,
    RAW_RULE_NO_HITS,
    RAW_LINE_RULES_NONE,
    raw_line_rules_side,
    raw_add_confirm_line,
    raw_rule_hits_text,
    raw_rules_title,
    raw_summary_with_disabled,
    AUTO_PLATFORM_LABEL,
    DEVICE_SPLIT_BUSY,
    DEVICE_SPLIT_NO_DEVICE,
    DEVICE_SPLIT_SAME_LOG,
    device_notice_text,
    COMPARE_KIND_TROUBLE,
    COMPARE_PLATFORM_TROUBLE,
    COMPARE_TAB_OFF_MODE_RAW,
    COMPARE_TAB_OFF_SHOW,
    COMPARE_TAB_RAW,
    COMPARE_TAB_STRUCTURED,
    COMPARE_TAB_UNPARSED,
    COMPARE_PARTNER_TITLE,
    EXPORT_CHOICE_CANCEL,
    EXPORT_CHOICE_DIFFS,
    EXPORT_CHOICE_HTML,
    EXPORT_CHOICE_PAIRS,
    EXPORT_RAW_RULES_OTHERS,
    EXPORT_RAW_WITHHELD,
    EXPORT_TITLE_COMPARE,
    MULTI_ERROR_OUTPUT,
    MULTI_SINGLE,
    MULTI_SINGLE_NOTE,
    MULTI_UNPAIRED_NOT_IN_FILE,
    NO_COUNT,
    OPEN_RESULT_FORMAT,
    OPEN_RESULT_NOT_JSON,
    OPEN_RESULT_UNREADABLE,
    OPEN_RESULT_VERSION,
    OP_ADDED,
    OP_CHANGED,
    OP_REMOVED,
    RAW_ALGORITHM_FELL_BACK,
    RAW_CONTEXT_PROBLEM,
    RAW_IGNORE_EXAMPLES,
    RAW_MARK_EQUATED,
    RAW_MARK_IGNORED,
    RAW_REPLACE_EXAMPLES,
    RAW_SETTINGS_SAVED_PREFIX,
    SM_ALL_RAW_CATEGORY,
    SM_BREAKDOWN_STRUCTURED,
    SM_CANCELLED,
    SM_DEFAULT_PLATFORM_SHORT,
    SM_MODE_BOTH,
    SM_MODE_RAW,
    SM_MODE_STRUCTURED,
    SM_NO_INPUT,
    SM_PASTED_NAME,
    SM_PICK_SEGMENT,
    SM_PLATFORM_UNSPECIFIED,
    SM_RAW_OFF_BY_MODE,
    SM_SELECT_KIND_PROMPT,
    SM_SET_ASIDE_SEPARATOR,
    SM_SHOW_OUTSIDE_TABLE,
    SM_SIDE_LABEL,
    SM_STAGE_LABEL,
    SM_STALE_RESULT,
    SM_SUMMARY_SEPARATOR,
    SM_UNKNOWN_COMMAND,
    SM_UNPARSED_NOTE,
    SM_UNPARSED_OFF,
    SM_WHOLE_ROW,
    STATUS_LABEL,
    STATUS_ORDER_ONLY,
    auto_platform_text,
    compare_pair_progress,
    compare_partner_prompt,
    compare_warning_line,
    error_output_note,
    llm_screen_lines,
    llm_section_lines,
    llm_column,
    llm_truncated_lines,
    llm_unsent_line,
    llm_unsent_shape_lines,
    multi_overall,
    notice_line,
    open_result_too_large,
    opened_band,
    partner_state_paired,
    partner_state_paired_error,
    platform_label,
    raw_defaults_unreadable,
    raw_move_mark,
    raw_moves_part,
    raw_option_problem_lines,
    raw_options_parts,
    raw_options_summary_text,
    raw_set_aside_line,
    route_detail_note,
    saved_notice,
    single_pair_name,
    single_row_name,
    sm_auto_decided_config,
    sm_auto_decided_show,
    sm_count_notices,
    sm_count_raw,
    sm_count_show,
    sm_count_structured,
    sm_count_unparsed,
    sm_count_warnings,
    sm_input_lines,
    sm_input_sections,
    sm_preview_summary,
    sm_select_segment_prompt,
    sm_show_fell_back,
    sm_show_replaced_same,
    sm_show_skipped_columns,
    sm_show_unexplained,
    sm_unparsed_pair,
    status_breakdown,
    unpaired_place,
    unpaired_row_name,
    unpaired_segment_note,
    unpaired_status,
    unreadable,
    unreadable_utf8,
    warning_message,
)
from driftpilot.raw_settings import RawSettings, load_raw_defaults, load_raw_settings, raw_settings_path, save_raw_defaults
from driftpilot.report_output import (
    Report,
    ReportPair,
    ReportRawRule,
    ReportRawRules,
    render_diffs_csv,
    render_html,
    render_pairs_csv,
)
from driftpilot.segmentation.input_forms import InputDecodeError, InputNote, normalize_text, read_config_input
from driftpilot.storage import (
    JOB_KEY,
    PAIRED_BY_HAND_KEY,
    StorageError,
    StorageFormatError,
    StoredRecord,
    UnsupportedSchemaVersion,
    build_record,
    default_filename,
    from_record,
    load,
    load_raw,
    resaved_record,
    save,
)
from driftpilot.value_text import value_text

Side = Literal["before", "after"]
Mode = Literal["structured", "raw", "both"]
KindChoice = Literal["config", "show", "auto"]

SIDES: tuple[Side, ...] = ("before", "after")
SIDE_LABEL = SM_SIDE_LABEL
# 画面の選択肢(値, 表示)。順番は画面に並ぶ順
MODE_CHOICES: tuple[tuple[Mode, str], ...] = (("both", SM_MODE_BOTH), ("structured", SM_MODE_STRUCTURED), ("raw", SM_MODE_RAW))
KIND_CHOICES: tuple[tuple[KindChoice, str], ...] = (("config", "config"), ("show", "show"), ("auto", AUTO_PLATFORM_LABEL))
# 種類が config のときの機種(パーサーの系統)。先頭が既定(指定なし=A型)、残りは CLI の --platform と同じ一覧(REQ-25 パート C)
CONFIG_PLATFORM_CHOICES: tuple[tuple[str, str], ...] = (("", SM_PLATFORM_UNSPECIFIED),) + tuple(
    (name, name) for name in CONFIG_PLATFORMS
)
# 機種の自動(ADR-0151 決定4)。pywebview の比較の窓・一括比較の窓の機種の選択肢。先頭が自動、残りは CONFIG_PLATFORM_CHOICES
WEB_PLATFORM_CHOICES: tuple[tuple[str, str], ...] = ((AUTO_PLATFORM, AUTO_PLATFORM_LABEL),) + CONFIG_PLATFORM_CHOICES
PASTED_NAME = SM_PASTED_NAME
# 進捗の工程名の表示(core の STAGES と同じ並び)
STAGE_LABEL = SM_STAGE_LABEL
OP_LABEL = {"added": OP_ADDED, "removed": OP_REMOVED, "changed": OP_CHANGED}
AUTO_SEGMENT = AUTO_PLATFORM_LABEL
PICK_SEGMENT = SM_PICK_SEGMENT
UNKNOWN_COMMAND = SM_UNKNOWN_COMMAND
WHOLE_ROW = SM_WHOLE_ROW
NO_INPUT = SM_NO_INPUT
CANCELLED = SM_CANCELLED
STALE_RESULT = SM_STALE_RESULT


@dataclass(frozen=True)
class PairProgress:
    """まとめて比べているときの進み具合(REQ-264 §3)。index は何組目か(1始まり)、total は比べる組の数、command はコマンドの文(不明は None)。"""

    index: int
    total: int
    command: str | None


ProgressSink = Callable[[str | PairProgress], None]


def stage_label(stage: str | PairProgress) -> str:
    """工程名の表示。core に工程が増えても落ちない(未知の工程名はそのまま出す)。まとめての比較の進み具合(PairProgress)は組の文にする。"""
    if isinstance(stage, PairProgress):
        return compare_pair_progress(stage.index, stage.total, stage.command)
    return STAGE_LABEL.get(stage, stage)


# --- 入力 ---


class InputReadError(Exception):
    """入力のファイルを読めない。文面は messages(CLI と同じ)から作る。"""


@dataclass(frozen=True)
class SideSource:
    """片側の入力。path はファイルを選んだときの絶対パスで、貼り付けは None。

    notes は読み込みで入力の形を戻した知らせ(文字コードなど。ADR-0138 決定7)。要求の SideInput に渡す。
    """

    text: str
    name: str
    path: Path | None = None
    notes: tuple[InputNote, ...] = ()

    @property
    def line_count(self) -> int:
        """入力の行数(入力の情報。比較の結果を数え直すものではない)。"""
        return len(self.text.splitlines())


def read_file(path: str | Path) -> SideSource:
    """CLI と同じ入口で読む(ADR-0138 決定1)。表示名はパスの文字列(CLI と同じく、選んだパスをそのまま名前にする)。"""
    p = Path(path).absolute()
    try:
        read = read_config_input(p)
    except InputDecodeError as exc:
        raise InputReadError(unreadable_utf8(p, exc.hints)) from exc
    except OSError as exc:
        raise InputReadError(unreadable(p, exc)) from exc
    return SideSource(text=read.text, name=str(p), path=p, notes=read.notes)


def pasted(text: str, side: Side) -> SideSource:
    """貼り付けた入力。表示名は固定で、パスは無い(保存レコードの path は null になる)。

    文字コードの後の工程(normalize_text)を通す(ADR-0138 決定1)。
    """
    read = normalize_text(text)
    return SideSource(text=read.text, name=PASTED_NAME[side], path=None, notes=read.notes)


@dataclass(frozen=True)
class ScreenInput:
    """画面の入力欄の値。区間の番号は None が「自動」。"""

    before: SideSource
    after: SideSource
    mode: Mode = "both"
    kind: KindChoice = "config"
    platform: str = ""
    show_command: str = ""
    before_segment: int | None = None
    after_segment: int | None = None
    llm: bool = False
    # 単純比較の設定(ADR-0166)。変えると「入力が変わった」とみなす(is_current は == で比べる。frozen でタプルのため)
    raw_options: RawOptions = DEFAULT_RAW_OPTIONS
    # show の表の比較で値の違いを比べない列(この窓だけの設定。大文字の名前の並び。REQ-367)
    skip_columns: tuple[str, ...] = ()

    def side(self, side: Side) -> SideSource:
        return self.before if side == "before" else self.after

    def segment(self, side: Side) -> int | None:
        return self.before_segment if side == "before" else self.after_segment


def _blank_to_none(value: str) -> str | None:
    return value.strip() or None


def screen_platform(kind: KindChoice, typed: str, config_index: int) -> str:
    """画面の機種の値。種類が config なら系統の選択(CONFIG_PLATFORM_CHOICES の番号)、それ以外なら手入力の ntc-templates の名前。"""
    if kind == "config":
        return CONFIG_PLATFORM_CHOICES[config_index][0]
    return typed


def web_kind_platform(kind: KindChoice, typed: str, index: int) -> tuple[KindChoice, str]:
    """pywebview の窓の種類と機種の値(ADR-0151 決定4)。index は WEB_PLATFORM_CHOICES の番号。

    show は (show, 打った名前)。自動の番号は (kind, "auto")。ほかは (config, 選んだ名前)。
    種類が自動で機種を選んだときは config として比べる(選択肢は全部 config の系統のため)。
    """
    if kind == "show":
        return "show", typed
    value = WEB_PLATFORM_CHOICES[index][0]
    if value == AUTO_PLATFORM:
        return kind, AUTO_PLATFORM
    return "config", value


def build_request(inp: ScreenInput) -> CompareRequest:
    """core への要求。機種は常に渡す(config ではパーサーの系統、show・自動では ntc-templates の名前)。

    show のコマンドは、種類が show か自動のときだけ渡す(config では欄を使えないため)。
    自動と系統の名前の組み合わせを、ここで先回りして止めない(core が「両方指定してください」で返す)。

    正規化ルールとプロンプトの指定は画面に無い(設定画面は別の依頼書)。既定値で比較する。
    """
    use_show_command = inp.kind != "config"
    return CompareRequest(
        before=SideInput(inp.before.text, inp.before.name, inp.before.notes),
        after=SideInput(inp.after.text, inp.after.name, inp.after.notes),
        mode=inp.mode,
        kind=inp.kind,
        platform=_blank_to_none(inp.platform),
        show_command=_blank_to_none(inp.show_command) if use_show_command else None,
        before_selection=SideSelection(number=inp.before_segment),
        after_selection=SideSelection(number=inp.after_segment),
        raw_options=inp.raw_options,
        skip_columns=inp.skip_columns,
    )


def source_paths(inp: ScreenInput) -> dict[str, str | None]:
    """保存レコードに書く入力のパス(REQ-14)。貼り付けの側は None。"""
    paths = {side: inp.side(side).path for side in SIDES}
    return {side: (str(path) if path is not None else None) for side, path in paths.items()}


def input_summary(source: SideSource | None, segment_count: int | None = None) -> str:
    """入力欄の横の「行数と区間数」。区間数は比較の後にだけ分かる(core の切り分けの結果)。"""
    if source is None:
        return NO_INPUT
    text = sm_input_lines(source.line_count)
    if segment_count is not None:
        text += sm_input_sections(segment_count)
    return text


# --- 表示用のデータ ---


@dataclass(frozen=True)
class Counts:
    """件数の内訳。core の結果の値をそのまま読む(GUI で数え直さない)。比較していないものは None。"""

    structured: int | None
    show: int | None
    raw: int | None
    unparsed: dict[str, int] | None
    warnings: int
    notices: int


def _shown_notices(outcome: CompareOutcome) -> list:
    """窓に出す注意。判定できないだけの注意(unknown_both)は除く(REQ-207 §5 / ADR-0149 決定5)。

    core の判定・CLI の出力・保存の notices は変えない(outcome.notices はそのまま)。
    """
    return [n for n in outcome.notices if n.kind != "unknown_both"]


def read_counts(outcome: CompareOutcome) -> Counts:
    show = outcome.show
    return Counts(
        structured=len(outcome.structured.diffs) if outcome.structured is not None else None,
        # テンプレートが無く raw にフォールバックした show は、構造化の件数を持たない(raw の件数に出る)
        show=len(show.diffs) if show is not None and show.method in ("structured", "both") else None,
        raw=len(outcome.raw.diffs) if outcome.raw is not None else None,
        unparsed=dict(outcome.structured.unparsed_counts) if outcome.structured is not None else None,
        warnings=len(outcome.warnings),
        notices=len(_shown_notices(outcome)),
    )


def counts_text(counts: Counts) -> str:
    parts: list[str] = []
    if counts.structured is not None:
        parts.append(sm_count_structured(counts.structured))
    if counts.show is not None:
        parts.append(sm_count_show(counts.show))
    if counts.raw is not None:
        parts.append(sm_count_raw(counts.raw))
    if counts.unparsed is not None:
        parts.append(sm_count_unparsed(counts.unparsed["before"], counts.unparsed["after"]))
    if counts.warnings:
        parts.append(sm_count_warnings(counts.warnings))
    if counts.notices:
        parts.append(sm_count_notices(counts.notices))
    return "  ".join(parts)


def tab_titles(counts: Counts) -> dict[str, str]:
    """タブの見出し。件数は内訳と同じ値。"""
    structured = counts.structured if counts.structured is not None else counts.show
    unparsed = counts.unparsed
    return {
        "structured": COMPARE_TAB_STRUCTURED + (f" ({structured})" if structured is not None else ""),
        "raw": COMPARE_TAB_RAW + (f" ({counts.raw})" if counts.raw is not None else ""),
        "unparsed": COMPARE_TAB_UNPARSED + (f" ({sm_unparsed_pair(unparsed['before'], unparsed['after'])})" if unparsed is not None else ""),
    }


@dataclass(frozen=True)
class StructuredRow:
    """構造化比較の表の 1 行。op は操作の欄の文(言語で変わる)。op_key は言語によらない鍵("added"・"removed"・"changed"、
    どれでもなければ "")で、変更の行の印の判定など、行の見分けはこちらで行う(ADR-0190)。
    """

    op: str
    category: str
    target: str
    item: str
    before: str
    after: str
    op_key: str = ""


@dataclass(frozen=True)
class RawRow:
    """単純比較の1行。左右を同じ行に並べる。mark は "" 同じ / "-" 削除 / "+" 追加 / "~" 変更(削除と追加を横に並べたもの)。

    単純比較の設定(ADR-0166)を使ったときは、"x" 無視した行(片側だけ) / "=" 同じとみなした行 も入る。どちらも差分ではない。
    行の移動の検出(REQ-228)を使ったときは、"<" 移動元(変更前だけ) / ">" 移動先(変更後だけ) も入る。どちらも差分で、move は
    移動の番号(1 始まり)。ほかの行の move は None。
    """

    mark: str
    before_no: int | None
    before_text: str
    after_no: int | None
    after_text: str
    move: int | None = None

    @property
    def is_diff(self) -> bool:
        return self.mark in ("-", "+", "~", "<", ">")


@dataclass(frozen=True)
class UnparsedRow:
    op: str
    line: str
    parents: str


@dataclass(frozen=True)
class SegmentChoice:
    number: int
    label: str
    line_count: int
    selected: bool


@dataclass(frozen=True)
class SideView:
    """片側の区間の一覧。waiting の側は候補(core の candidates)をそのまま、他は切り分けの結果から出す。"""

    name: str
    segment_count: int
    choices: list[SegmentChoice]
    selected: int | None
    waiting: bool


@dataclass(frozen=True)
class SelectionView:
    """選択待ち(ADR-0022 決定5)。ダイアログではなく、画面の上で選択を促す。CLI の終了コード 3 と同じもの。"""

    reason: str
    waiting: list[Side]
    candidates: dict[Side, list[SegmentChoice]]
    prompt: str
    detail: str | None
    # ambiguous_format のとき、機種と show のコマンドの欄へ誘導する
    focus_format_fields: bool


@dataclass(frozen=True)
class OutcomeView:
    status: str
    status_text: str  # wx の比較の画面のステータスバーの文
    status_text_web: str  # pywebview の比較の画面の窓の下の文(差分ありの内訳を添える。REQ-113 §3)
    counts: Counts
    counts_text: str
    titles: dict[str, str]
    structured_rows: list[StructuredRow]
    structured_note: str | None
    raw_rows: list[RawRow]
    raw_note: str | None
    unparsed_rows: list[UnparsedRow]
    unparsed_note: str | None
    banner: list[str]
    warnings: list[str]
    # AI の答えは返したまま(<red> も残す)。pywebview の比較の画面の llm の文字と、整えた表示(answer_blocks)の元
    llm_lines: list[str] | None
    sides: dict[Side, SideView] | None
    selection: SelectionView | None
    # llm_lines と同じ行で、答えだけ <red>・</red> を外したもの(wx の比較の画面用。REQ-204 §5)
    llm_plain_lines: list[str] | None = None
    # pywebview の比較の画面の AI の面の行(頭の行を除く。REQ-206 §8 / ADR-0148 決定8)。答えは返したまま(<red> も残す)
    llm_screen_lines: list[str] | None = None
    # コピーのアイコンで入れる文(答えがあるときだけ。<red>・</red> を外した答え)
    llm_copy: str | None = None
    # pywebview の比較の画面の構造化のタブの文と警告(ADR-0151 決定8)。機種違いで失敗したときは短い文だけにし、
    # エラーの全文と platform_mismatch の警告は structured_detail_web(「詳しい内容」の折りたたみ)に入れる。
    # ほかのときは structured_note・warnings と同じ。wx は structured_note・warnings を使う(変えない)
    structured_note_web: str | None = None
    structured_detail_web: str | None = None
    warnings_web: list[str] | None = None
    # pywebview の比較の画面の、構造化比較のタブと構造化不可の行のタブを押せなくする理由(ADR-0164 決定1・2)。押せるなら None。
    # wx は読まない
    structured_off: str | None = None
    # 単純比較の設定で無視した行・同じとみなした行の数の文(ADR-0166 決定11-2)。設定が無い・どちらも 0 なら None。wx は読まない
    raw_set_aside: str | None = None
    # 保存した結果を開いたときの窓の下の行(「保存した結果の単純比較の設定: …」。opened_view が入れる)。wx は読まない
    raw_settings_saved: str | None = None
    # 1組を比べて結果を開いたときに選ぶタブ(structured・raw。show の both・raw だけ。ほかは None=今のタブのまま。REQ-282)。wx は読まない
    open_tab: str | None = None
    # 構造化比較の行ごとの、show の表の変更の行の変わった列の名前(ほかの行は空。structured_rows と同じ数・同じ並び。REQ-367 §3)
    structured_columns: list[list[str]] = field(default_factory=list)
    # 構造化比較の行の番号から、元の行(raw_rows の番号)を探す関数(REQ-367 §2)。単純比較の結果が無いとき(比較方式が構造化だけ)は None。
    # 探すのは呼んだときだけ(行ごとには探さない)。結果の比べ方には入れない
    find_source: Callable[[int], int | None] | None = field(default=None, compare=False)
    # 単純比較のタブの表の一番上に出す、選んでいる組の前と後の元の行(`sw-test1#show version`。{"before", "after"}。ADR-0251 決定1・REQ-380)。
    # 見せるためだけで、比べる文・違いの数には入れない。単純比較の結果が無い・両方とも無いときは None。片方だけ無いときはその側が None
    raw_prompt: dict[str, str | None] | None = None


def answer_blocks(text: str | None) -> list[dict[str, Any]] | None:
    """AI の答え(と知らせの行)を整えて表示するためのブロックの一覧(REQ-204 §2・§3)。None なら None。

    画面はこの一覧の文字を textContent だけで入れる(HTML として扱わない。ADR-0146 決定4)。
    """
    return None if text is None else format_answer(text)


def answer_copy_text(answer: str) -> str:
    """コピーのアイコンで入れる答えの文(赤字の印 <red>・</red> を外す。ほかの印は文字のまま。ADR-0148 決定8)。"""
    return plain_answer(answer)


def plain_llm(llm: LlmAnalysis) -> LlmAnalysis:
    """答えだけ <red>・</red> を外した写し(CLI の text の出力・wx の画面用。保存する結果には使わない。REQ-204 §5)。"""
    return llm if llm.answer is None else replace(llm, answer=plain_answer(llm.answer))


def segment_label(segment: Any) -> str:
    """区間の表示。`2 : show running-config` の形。コマンドが取れない区間は「(コマンド不明)」。"""
    return f"{segment.number} : {segment.command if segment.command is not None else UNKNOWN_COMMAND}"


def _value(value: Any) -> str:
    return "" if value is None else repr(value)


def _diff_value(d: Any, side: str) -> str:
    """差分の値の欄。足した・消した経路に添えた機種固有値は、辞書から外して短い注記にする(ADR-0072。CLI の text と同じ)。"""
    value = d[side]
    if d["category"] == "static_routes" and isinstance(value, dict) and "vendor_specific" in value:
        rest = {k: v for k, v in value.items() if k != "vendor_specific"}
        return f"{value_text(rest)}{route_detail_note(value['vendor_specific'])}"
    return "" if value is None else value_text(value)


def _op_key(op: str) -> str:
    """core の操作(added・removed・changed)なら、そのまま言語によらない鍵。ほかは空(ADR-0190)。"""
    return op if op in OP_LABEL else ""


def _structured_rows(outcome: CompareOutcome) -> list[StructuredRow]:
    if outcome.show is not None:
        rows = []
        for d in outcome.show.diffs:
            target = d["key"] if d["key"] is not None else WHOLE_ROW
            if d["op"] == "changed":
                fields = d["fields"]
                before = ", ".join(f"{c}: {b!r}" for c, (b, _a) in fields.items())
                after = ", ".join(f"{c}: {a!r}" for c, (_b, a) in fields.items())
                rows.append(StructuredRow(OP_LABEL["changed"], "show", target, ", ".join(fields), before, after, "changed"))
            else:
                rows.append(
                    StructuredRow(
                        OP_LABEL.get(d["op"], d["op"]), "show", target, "", _value(d["before"]), _value(d["after"]), _op_key(d["op"])
                    )
                )
        return rows
    if outcome.structured is None:
        return []
    # 未解釈の行の差分も含める(CLI の text と同じ)
    return [
        StructuredRow(
            OP_LABEL.get(d["op"], d["op"]),
            d["category"],
            d["name"] or "",
            " > ".join(d["path"]),
            _diff_value(d, "before"),
            _diff_value(d, "after"),
            _op_key(d["op"]),
        )
        for d in outcome.structured.diffs
    ]


def _outside_table(outcome: CompareOutcome) -> bool:
    """show を単純比較で出した理由が「表に出ない行が変わった」か。理由の文は比べたときの言語で入るので、日英どちらの文も見る(ADR-0200)。"""
    show = outcome.show
    return (
        show is not None
        and show.method == "raw"
        and show.fallback_reason in SHOW_OUTSIDE_TABLE_REASONS
    )


def _show_raw_reason(outcome: CompareOutcome) -> str:
    """show を単純比較で出した理由の1行(表に出ない行が変わった組は専用の文、テンプレートが無い組は今の文)。"""
    assert outcome.show is not None
    return SM_SHOW_OUTSIDE_TABLE if _outside_table(outcome) else sm_show_fell_back(outcome.show.fallback_reason)


def show_method(outcome: CompareOutcome | None) -> str | None:
    """一覧の行に出す、show の比べ方の印の元。単純比較で出した show は "raw"、表と単純比較の両方は "both"、ほかは None。"""
    if outcome is None or outcome.show is None:
        return None
    return outcome.show.method if outcome.show.method in ("raw", "both") else None


def show_open_tab(outcome: CompareOutcome) -> str | None:
    """1組を比べたとき、結果を開いて選ぶタブ。show の both は structured、raw は raw、ほかは None(今のタブのまま。ADR-0200 決定 5)。"""
    return {"both": "structured", "raw": "raw"}.get(show_method(outcome) or "")


def _structured_note(outcome: CompareOutcome) -> str | None:
    """構造化のタブの注記。show を表で比べて置き換えで同じとみなした値があれば、その数の 1 行を次の行に足す(ADR-0239 決定 4)。"""
    note = _structured_note_base(outcome)
    show = outcome.show
    if show is not None and show.method in ("structured", "both") and show.replaced_same >= 1:
        line = sm_show_replaced_same(show.replaced_same)
        note = line if note is None else f"{note}\n{line}"
    if show is not None and show.method in ("structured", "both") and show.skipped_columns:
        # 比べない列(この窓だけの設定)が結果の表にあるときだけ。置き換えの数の 1 行の次に置く(REQ-367)
        line = sm_show_skipped_columns(show.skipped_columns)
        note = line if note is None else f"{note}\n{line}"
    return note


def _structured_note_base(outcome: CompareOutcome) -> str | None:
    if outcome.kind is None:
        return None
    if outcome.mode == "raw":
        return COMPARE_TAB_OFF_MODE_RAW
    if outcome.show is not None and outcome.show.method == "raw":
        return _show_raw_reason(outcome)
    if outcome.show is not None and outcome.show.method == "both":
        return sm_show_unexplained(outcome.show.unexplained_lines)
    if outcome.kind == "config" and outcome.structured is None:
        return outcome.error.message if outcome.error else None
    if (outcome.show is not None and not outcome.show.diffs) or (outcome.structured is not None and not outcome.structured.diffs):
        return STATUS_LABEL["no_difference"]
    return None


def structured_off(outcome: CompareOutcome) -> str | None:
    """構造化比較のタブと構造化不可の行のタブを押せなくする理由の文(ADR-0164 決定1・2)。押せるなら None。

    比較方式が「単純」の組と、show を単純比較で比べた組だけ押せない。構造化した組、show をテンプレートで構造化した組、
    config の構造化が失敗した組(タブにエラーの文を出す)、結果が無い組は押せる。
    """
    if outcome.kind is None:
        return None
    if outcome.mode == "raw":
        return COMPARE_TAB_OFF_MODE_RAW
    if outcome.show is not None and outcome.show.method == "raw":
        return SM_SHOW_OUTSIDE_TABLE if _outside_table(outcome) else COMPARE_TAB_OFF_SHOW
    return None


def _raw_note(outcome: CompareOutcome) -> str | None:
    if outcome.kind is None:
        return None
    if outcome.raw is None:
        return SM_RAW_OFF_BY_MODE
    # 単純比較で出した show は、理由の1行を出す。差分が無いときは、次の行に「差分なし」を続ける
    reason = _show_raw_reason(outcome) if outcome.show is not None and outcome.show.method == "raw" else None
    if not outcome.raw.diffs:
        return STATUS_LABEL["no_difference"] if reason is None else f"{reason}\n{STATUS_LABEL['no_difference']}"
    return reason


def align_raw(
    before_body: Sequence[str],
    after_body: Sequence[str],
    diffs: Sequence[dict[str, Any]],
    options: RawOptionsRecord | None = None,
) -> list[RawRow]:
    """単純比較の結果を左右に並べる。差分の判定はしない(core の行番号だけを使う)。

    本文は正規化前の原文。正規化は行数を変えないため、core の行番号でそのまま原文の行を指せる。
    続けて削除と追加が来たところは、横に並べて「変更」の行にする。
    options(単純比較の設定の記録)があれば、無視した行は片側だけの "x" の行にし(変更前を先に出す)、同じ行の組が
    equated にあれば "=" にする(本文どうしを比べて決めない。機種のルールで同じになった行を「同じとみなした」と出さないため)。
    移動(options.moves。REQ-228)に入る削除の行は "<"、追加の行は ">" の自分の行にし、ほかの削除・追加と横に並べない。
    """
    removed = {d["before_line_no"] for d in diffs if d["op"] == "removed"}
    added = {d["after_line_no"] for d in diffs if d["op"] == "added"}
    ignored_before = set(options.ignored.get("before", [])) if options is not None else set()
    ignored_after = set(options.ignored.get("after", [])) if options is not None else set()
    equated = (
        {(p["before_line_no"], p["after_line_no"]) for p in options.equated} if options is not None else set()
    )
    moved_before: dict[int, int] = {}
    moved_after: dict[int, int] = {}
    for move in options.moves if options is not None else []:
        for k in range(move["count"]):
            moved_before[move["before_start"] + k] = move["number"]
            moved_after[move["after_start"] + k] = move["number"]
    nb, na = len(before_body), len(after_body)
    rows: list[RawRow] = []
    i = j = 1
    while i <= nb or j <= na:
        if i <= nb and i in ignored_before:
            rows.append(RawRow("x", i, before_body[i - 1], None, ""))
            i += 1
        elif j <= na and j in ignored_after:
            rows.append(RawRow("x", None, "", j, after_body[j - 1]))
            j += 1
        elif i <= nb and i in moved_before:
            rows.append(RawRow("<", i, before_body[i - 1], None, "", move=moved_before[i]))
            i += 1
        elif j <= na and j in moved_after:
            rows.append(RawRow(">", None, "", j, after_body[j - 1], move=moved_after[j]))
            j += 1
        elif (i <= nb and i in removed) or (j <= na and j in added):
            # ふつうの削除と追加の続きは、移動の行の手前で切る(移動の行は「変更」に並べない)
            run_b: list[int] = []
            while i <= nb and i in removed and i not in moved_before:
                run_b.append(i)
                i += 1
            run_a: list[int] = []
            while j <= na and j in added and j not in moved_after:
                run_a.append(j)
                j += 1
            for k in range(max(len(run_b), len(run_a))):
                b = run_b[k] if k < len(run_b) else None
                a = run_a[k] if k < len(run_a) else None
                mark = "~" if b is not None and a is not None else ("-" if b is not None else "+")
                rows.append(RawRow(mark, b, before_body[b - 1] if b else "", a, after_body[a - 1] if a else ""))
        elif i <= nb and j <= na:
            rows.append(RawRow("=" if (i, j) in equated else "", i, before_body[i - 1], j, after_body[j - 1]))
            i += 1
            j += 1
        elif i <= nb:  # 結果と本文が合わないとき(起きない想定)も、行は落とさずに出す
            rows.append(RawRow("", i, before_body[i - 1], None, ""))
            i += 1
        else:
            rows.append(RawRow("", None, "", j, after_body[j - 1]))
            j += 1
    return rows


def blend_rgb(background: tuple[int, int, int], accent: tuple[int, int, int], ratio: float = 0.25) -> tuple[int, int, int]:
    """差分の行の背景色。OS の背景色に強調色を少し混ぜた1色だけを作る(独自の配色は持たない。ADR-0022 決定9)。

    ダークモードでは背景色が暗いので、混ぜた色も暗くなる(文字色は OS のまま読める)。
    """
    return tuple(round(b * (1 - ratio) + a * ratio) for b, a in zip(background, accent, strict=True))  # type: ignore[return-value]


def find_diff(rows: Sequence[RawRow], current: int, step: int) -> int | None:
    """「次の差分 / 前の差分」。current(未選択は -1)から step の向きに、差分のかたまりの先頭の行を探す。"""
    heads = [k for k, r in enumerate(rows) if r.is_diff and (k == 0 or not rows[k - 1].is_diff)]
    if step > 0:
        return next((k for k in heads if k > current), None)
    return next((k for k in reversed(heads) if k < current), None)


# 「差分のみ表示」で差分の行の前後に残す行数の既定(ADR-0155 決定2。diff -u の既定と同じ。AI に送る CONTEXT_LINES とは別の値)。
# 比較の窓で 0〜20 に変えられる(ADR-0166 決定8。REQ-228 §2)
RAW_FOLD_CONTEXT = RAW_CONTEXT_DEFAULT


@dataclass(frozen=True)
class RawFold:
    """「差分のみ表示」で1行に折りたたむ範囲(ADR-0155 決定2)。"""

    start: int  # 隠す最初の行の番号(raw_rows の番号)
    count: int  # 隠す行数


def fold_ranges(rows: Sequence[RawRow], context: int = RAW_FOLD_CONTEXT) -> list[RawFold]:
    """差分の行とその前後 context 行を残し、残りの続きを1つずつ返す。差分が無ければ []。

    どの行を残すかは Python が決める(ADR-0155 決定2)。全部の行が残る(全部が差分など)ときも []。
    find_diff が返す差分のかたまりの先頭は差分の行なので、必ず残る(どの範囲にも入らない)。
    """
    total = len(rows)
    keep = [False] * total
    found = False
    for k, row in enumerate(rows):
        if row.is_diff:
            found = True
            for j in range(max(0, k - context), min(total, k + context + 1)):
                keep[j] = True
    if not found:
        return []
    folds: list[RawFold] = []
    start: int | None = None
    for k, kept in enumerate(keep):
        if not kept and start is None:
            start = k
        elif kept and start is not None:
            folds.append(RawFold(start, k - start))
            start = None
    if start is not None:
        folds.append(RawFold(start, total - start))
    return folds


def _marks_of(pairs: Iterable[tuple[int, str, str]]) -> list[dict[str, Any]]:
    """変更の行の (番号, 変更前, 変更後) から、違う文字の範囲を求める(ADR-0165、REQ-226)。

    印の付いた行だけを返す。位置は JavaScript の文字の位置(UTF-16 の単位)。
    対象の行を先頭から数え、MAX_MARKED_ROWS 行を超えた分は見ない(テストで差し替えられるよう、呼ぶたびに読む)。
    """
    limit = inline_marks.MAX_MARKED_ROWS
    marks: list[dict[str, Any]] = []
    for count, (index, before, after) in enumerate(pairs):
        if count >= limit:
            break
        found = inline_marks.line_marks(before, after)
        if found is None:
            continue
        marks.append(
            {
                "index": index,
                "before": inline_marks.utf16_ranges(before, found.before),
                "after": inline_marks.utf16_ranges(after, found.after),
            }
        )
    return marks


def raw_marks(rows: Sequence[RawRow]) -> list[dict[str, Any]]:
    """単純比較の表の印。mark が "~"(変更)の行の変更前と変更後の文字。"""
    return _marks_of((k, r.before_text, r.after_text) for k, r in enumerate(rows) if r.mark == "~")


def structured_marks(rows: Sequence[StructuredRow]) -> list[dict[str, Any]]:
    """構造化比較の表の印。操作が「変更」の行(op_key が changed。文では見分けない)の変更前と変更後。"""
    return _marks_of((k, r.before, r.after) for k, r in enumerate(rows) if r.op_key == "changed")


def category_counts(rows: Sequence[StructuredRow]) -> list[tuple[str, int]]:
    """カテゴリと件数。件数の多い順、同じ件数なら名前の順(ADR-0155 決定1)。名前は表の「カテゴリ」の列と同じ文字。"""
    counts = Counter(row.category for row in rows)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _raw_rows(outcome: CompareOutcome) -> list[RawRow]:
    seg = outcome.segmentation
    if outcome.raw is None or seg is None or seg["before"].selected is None or seg["after"].selected is None:
        return []
    return align_raw(seg["before"].selected.body, seg["after"].selected.body, outcome.raw.diffs, outcome.raw.options)


def _raw_prompt(outcome: CompareOutcome) -> dict[str, str | None] | None:
    """単純比較のタブの見出しの行に出す、前と後の元の行(REQ-380)。単純比較で比べた区間の、区間を始めたプロンプトの行。"""
    seg = outcome.segmentation
    if outcome.raw is None or seg is None or seg["before"].selected is None or seg["after"].selected is None:
        return None
    before, after = seg["before"].selected.prompt_line, seg["after"].selected.prompt_line
    if before is None and after is None:
        return None
    return {"before": before, "after": after}


def _unparsed(outcome: CompareOutcome) -> tuple[list[UnparsedRow], str | None]:
    if outcome.kind is None:
        return [], None
    if outcome.structured is None:
        return [], SM_UNPARSED_OFF
    rows = []
    for d in outcome.structured.diffs:
        if d["category"] != "unparsed":
            continue
        line_path = d["before"] if d["op"] == "removed" else d["after"]
        rows.append(UnparsedRow(OP_LABEL.get(d["op"], d["op"]), line_path[-1], " > ".join(line_path[:-1])))
    note = SM_UNPARSED_NOTE
    return rows, note


def _choices_from_candidates(outcome: CompareOutcome, side: Side) -> list[SegmentChoice]:
    # 候補は core が返したものをそのまま、順番どおりに並べる(GUI で絞らない。ADR-0022 決定5)
    return [
        SegmentChoice(c.segment.number, segment_label(c.segment), c.segment.line_count, False)
        for c in outcome.candidates
        if c.side == side
    ]


def _side_views(outcome: CompareOutcome) -> dict[Side, SideView] | None:
    seg = outcome.segmentation
    if seg is None:
        return None
    waiting = {c.side for c in outcome.candidates}
    views: dict[Side, SideView] = {}
    for side in SIDES:
        s = seg[side]
        selected = s.selected.number if s.selected is not None else None
        if side in waiting:
            choices = _choices_from_candidates(outcome, side)
        else:
            choices = [
                SegmentChoice(x.number, segment_label(x), x.line_count, x.number == selected) for x in s.split.segments
            ]
        views[side] = SideView(s.name, len(s.split.segments), choices, selected, side in waiting)
    return views


def _selection(outcome: CompareOutcome) -> SelectionView | None:
    if outcome.status != "needs_selection":
        return None
    if outcome.selection_reason == "ambiguous_format":
        return SelectionView(
            reason="ambiguous_format",
            waiting=[],
            candidates={},
            prompt=SM_SELECT_KIND_PROMPT,
            detail=outcome.selection_detail.get("message"),
            focus_format_fields=True,
        )
    waiting = [side for side in SIDES if any(c.side == side for c in outcome.candidates)]
    return SelectionView(
        reason=outcome.selection_reason or "segment",
        waiting=waiting,
        candidates={side: _choices_from_candidates(outcome, side) for side in waiting},
        prompt=sm_select_segment_prompt([SIDE_LABEL[s] for s in waiting]),
        detail=None,
        focus_format_fields=False,
    )


def _status_text(outcome: CompareOutcome, selection: SelectionView | None) -> str:
    label = STATUS_LABEL[outcome.status]
    if selection is not None:
        return f"{label}: {selection.prompt}"
    if outcome.error is not None:
        return f"{label}: {outcome.error.message}"
    return label


def raw_order_only(outcome: CompareOutcome) -> bool:
    """単純比較の差分が、行の順番だけの違いか(REQ-113 §3)。

    消えた行と増えた行(比較した区間の正規化後の行)を数えて、同じ行が同じ数ずつなら真。差分の無い行は両側で共通なので、
    これは区間の行を並べ替えると同じ(数も同じ)であることと同じ。差分が無ければ偽。
    """
    if outcome.raw is None or not outcome.raw.diffs:
        return False
    removed = Counter(d["text"] for d in outcome.raw.diffs if d["op"] == "removed")
    added = Counter(d["text"] for d in outcome.raw.diffs if d["op"] == "added")
    return removed == added


def _platform_trouble(outcome: CompareOutcome) -> bool:
    """比較が失敗し、platform_mismatch の警告があるか(機種の選択が中身と合っていない疑い。ADR-0151 決定8)。"""
    return outcome.error is not None and any(w.kind == "platform_mismatch" for w in outcome.warnings)


def _status_text_web(outcome: CompareOutcome, selection: SelectionView | None, counts: Counts) -> str:
    """pywebview の比較の画面の窓の下の文(REQ-113 §3)。wx の文(_status_text)は変えない。

    差分ありで、構造化比較(show は構造化できたとき)と単純比較の両方を動かしたときだけ内訳を添える。
    構造化が 0 件で単純比較の差分が順番だけなら、そう言う。片方だけのときは _status_text と同じ文。
    機種違いで失敗したときは「{状態}: {短い文}」、種類の判定の食い違いは種類を選ぶ案内にする(ADR-0151 決定8)。
    """
    if _platform_trouble(outcome):
        return f"{STATUS_LABEL[outcome.status]}: {COMPARE_PLATFORM_TROUBLE}"
    if outcome.error is not None and outcome.error.code == "kind_mismatch":
        return f"{STATUS_LABEL[outcome.status]}: {COMPARE_KIND_TROUBLE}"
    plain = _status_text(outcome, selection)
    if outcome.status != "difference" or selection is not None or outcome.error is not None or counts.raw is None:
        return plain
    if counts.structured is not None:
        label, structured = SM_BREAKDOWN_STRUCTURED, counts.structured
    elif counts.show is not None:
        label, structured = "show", counts.show
    else:
        return plain
    if structured == 0 and counts.raw == 0:
        return plain
    if structured == 0 and raw_order_only(outcome):
        return STATUS_ORDER_ONLY
    return status_breakdown(label, structured, counts.raw)


def structured_note_web(outcome: CompareOutcome) -> str | None:
    """pywebview の構造化のタブの文。機種違いで失敗したときは短い文(ADR-0151 決定8)、ほかは structured_note と同じ。"""
    return COMPARE_PLATFORM_TROUBLE if _platform_trouble(outcome) else _structured_note(outcome)


def structured_detail_web(outcome: CompareOutcome) -> str | None:
    """「詳しい内容」の折りたたみの中身。機種違いで失敗したときだけ、エラーの全文と platform_mismatch の警告の行。"""
    if not _platform_trouble(outcome):
        return None
    assert outcome.error is not None
    lines = [outcome.error.message]
    lines += [compare_warning_line(warning_message(w)) for w in outcome.warnings if w.kind == "platform_mismatch"]
    return "\n".join(lines)


def warnings_web(outcome: CompareOutcome) -> list[str]:
    """pywebview の構造化のタブの警告。機種違いで失敗したときは platform_mismatch を除く(折りたたみに入れるため)。"""
    trouble = _platform_trouble(outcome)
    return [warning_message(w) for w in outcome.warnings if not (trouble and w.kind == "platform_mismatch")]


def view_outcome(outcome: CompareOutcome) -> OutcomeView:
    """core の結果を表示用のデータに写す。状態も件数も core の値を読むだけ。"""
    counts = read_counts(outcome)
    selection = _selection(outcome)
    unparsed_rows, unparsed_note = _unparsed(outcome)
    llm = outcome.llm
    # LLM を頼んだときだけ欄を出す(CLI の text と同じ条件)
    shown = llm is not None and llm.reason != "disabled"
    raw_rows = _raw_rows(outcome)
    has_raw = outcome.raw is not None or (outcome.show is not None and outcome.show.raw is not None)
    return OutcomeView(
        structured_columns=structured_columns(outcome),
        find_source=partial(source_raw_row, outcome, raw_rows) if has_raw else None,
        status=outcome.status,
        status_text=_status_text(outcome, selection),
        status_text_web=_status_text_web(outcome, selection, counts),
        counts=counts,
        counts_text=counts_text(counts),
        titles=tab_titles(counts),
        structured_rows=_structured_rows(outcome),
        structured_note=_structured_note(outcome),
        raw_rows=raw_rows,
        raw_note=_raw_note(outcome),
        unparsed_rows=unparsed_rows,
        unparsed_note=unparsed_note,
        banner=[notice_line(n) for n in _shown_notices(outcome)],
        warnings=[warning_message(w) for w in outcome.warnings],
        llm_lines=llm_section_lines(llm) if shown else None,
        sides=_side_views(outcome),
        selection=selection,
        llm_plain_lines=llm_section_lines(plain_llm(llm)) if shown else None,
        llm_screen_lines=llm_screen_lines(llm) if shown else None,
        llm_copy=answer_copy_text(llm.answer) if shown and llm.status == "done" and isinstance(llm.answer, str) else None,
        structured_note_web=structured_note_web(outcome),
        structured_detail_web=structured_detail_web(outcome),
        warnings_web=warnings_web(outcome),
        structured_off=structured_off(outcome),
        raw_set_aside=raw_set_aside_text(outcome_raw_options(outcome)),
        open_tab=show_open_tab(outcome),
        raw_prompt=_raw_prompt(outcome),
    )


# 比較の設定の要約(REQ-206 §2)で、config の機種が「指定なし」のときの短い名前
DEFAULT_PLATFORM_SHORT = SM_DEFAULT_PLATFORM_SHORT
SUMMARY_SEPARATOR = SM_SUMMARY_SEPARATOR


def settings_summary(mode: str, kind: str, config_platform: int, platform: str, command: str) -> str:
    """比較の設定の要約の文(pywebview の比較の画面の上の3行目。REQ-206 §2 / ADR-0148 決定1)。例: config・Cisco 系・両方。

    種類・機種(config では系統の選択、show・自動では打った名前があれば)・show のコマンド(show・自動で打ってあれば)・比較方式を
    「・」でつなぐ。値は画面の欄の値(form)のまま。
    """
    kinds = dict(KIND_CHOICES)
    modes = dict(MODE_CHOICES)
    parts = [kinds.get(kind, kind)]  # type: ignore[call-overload]
    if kind == "config":
        value = CONFIG_PLATFORM_CHOICES[config_platform][0] if 0 <= config_platform < len(CONFIG_PLATFORM_CHOICES) else ""
        parts.append(value or DEFAULT_PLATFORM_SHORT)
    else:
        parts += [text for text in (platform.strip(), command.strip()) if text]
    parts.append(modes.get(mode, mode))  # type: ignore[call-overload]
    return SUMMARY_SEPARATOR.join(parts)


def web_settings_summary(
    mode: str,
    kind: str,
    index: int,
    typed: str,
    command: str,
    outcome: CompareOutcome | None = None,
    raw_options: RawOptions | None = None,
    raw_disabled_count: int = 0,
) -> str:
    """pywebview の比較の画面の設定の要約(_web_settings_summary)。raw_options が既定でなければ、単純比較の設定の要約を
    「・」でつないで後ろに足す(ADR-0166。既定・None なら今と同じ文)。raw_disabled_count(REQ-359)は無効のルールの数で、
    1 以上なら要約に「（無効 M）」を足す(有効なルールもほかの設定も既定でも出す)。
    """
    text = _web_settings_summary(mode, kind, index, typed, command, outcome)
    summary = raw_options_summary(raw_options, raw_disabled_count) if raw_options is not None else None
    return text if summary is None else SUMMARY_SEPARATOR.join([text, summary])


def _web_settings_summary(
    mode: str, kind: str, index: int, typed: str, command: str, outcome: CompareOutcome | None = None
) -> str:
    """pywebview の比較の画面の設定の要約(ADR-0151 決定7)。index は WEB_PLATFORM_CHOICES の番号。

    outcome は今の種類と機種で比べた結果(比較の前・相手なしの行は None)。例:
    - 種類も機種も自動: 比較の前は「自動・両方」、config に決まれば「自動（config・FortiGate）・両方」、show なら「自動（show）・両方」
    - 種類 config で機種が自動: 「config・自動・両方」、比べた後は「config・自動（FortiGate）・両方」
    - 機種を選んだとき: 「config・FortiGate・両方」(指定なしは「config・Cisco 系・両方」)
    - show: settings_summary と同じ「show・打った名前・コマンド・両方」
    """
    modes = dict(MODE_CHOICES)
    mode_text = modes.get(mode, mode)  # type: ignore[call-overload]
    if kind == "show":
        return settings_summary(mode, kind, 0, typed, command)
    value = WEB_PLATFORM_CHOICES[index][0] if 0 <= index < len(WEB_PLATFORM_CHOICES) else ""
    if value != AUTO_PLATFORM:
        chosen = platform_label(value) if value else DEFAULT_PLATFORM_SHORT
        return SUMMARY_SEPARATOR.join(["config", chosen, mode_text])
    decided = outcome.kind if outcome is not None else None
    used = outcome.auto_platform.used if outcome is not None and outcome.auto_platform is not None else None
    if kind == "config":
        shown = auto_platform_text(used) if decided == "config" else AUTO_PLATFORM_LABEL
        return SUMMARY_SEPARATOR.join(["config", shown, mode_text])
    if decided == "config":
        head = sm_auto_decided_config(platform_label(used))
    elif decided == "show":
        head = sm_auto_decided_show()
    else:
        head = AUTO_PLATFORM_LABEL
    return SUMMARY_SEPARATOR.join([head, mode_text])


def segment_items(view: SideView | None) -> list[str]:
    """区間の選択欄の項目。先頭は「自動」(選択待ちの側では「選んでください」)、続けて区間。"""
    if view is None:
        return [AUTO_SEGMENT]
    return [PICK_SEGMENT if view.waiting else AUTO_SEGMENT, *(c.label for c in view.choices)]


def segment_number(view: SideView | None, index: int) -> int | None:
    """選択欄の位置から区間の番号へ。先頭(自動)は None。"""
    if view is None or index <= 0 or index > len(view.choices):
        return None
    return view.choices[index - 1].number


def segment_index(view: SideView | None, number: int | None) -> int:
    """区間の番号から選択欄の位置へ。見つからなければ先頭(自動)。"""
    if view is None or number is None:
        return 0
    return next((k + 1 for k, c in enumerate(view.choices) if c.number == number), 0)


# --- 単純比較の設定(ADR-0166 / REQ-227 §9) ---

# 画面の「無視する行」の欄の文字数の上限(形の確かめ。式の数と長さの上限は raw_keys の check_raw_options)
MAX_RAW_FORM_IGNORE_CHARS = 20_000
# 画面の置き換えの表の行の数の上限(20 組まで足せる。空の組を含めた形の確かめ)
MAX_RAW_REPLACEMENT_ROWS = 20
_RAW_FORM_KEYS = (
    "ignore_lines",
    "replacements",
    "whitespace",
    "ignore_blank_lines",
    "ignore_case",
    # REQ-228
    "context_lines",
    "detect_moves",
    "algorithm",
    # REQ-359: 無効のルール({"ignore_lines": [式…], "replacements": [[式, 置き換える文字]…]})
    "disabled",
)
# 無効のルールの欄の長さの上限(形の確かめ。無視する行は式の数の上限 MAX_PATTERNS を超える欄の値も形としては受け、中身で捨てる)
MAX_RAW_FORM_DISABLED_ROWS = 100


@dataclass(frozen=True)
class RawForm:
    """画面から来た単純比較の設定の欄の値を読んだもの。options は誤りがあれば None、problems は画面に出す文。

    options は比べるときの設定(無効のルールを除いたもの)。REQ-359: all_options は無効のルールも含めた全部のルール(形が読めれば、
    式に誤りがあっても入る。「効いているルール」の一覧と保存に使う)、disabled は欄の中のルールに当たる無効のルール(欄に無い式・
    組は捨てた後のもの)。省けば all_options は options、disabled は無し。
    context_lines は「差分のみ表示」の前後の行数(表示だけの値で options には入れない。REQ-228 §2)。読めなければ既定の 3。
    compare_options は、前後の行数の誤りを除いて読めた設定(ほかの欄に誤りがあれば None。省けば options と同じ)。
    前後の行数は表示だけの値なので、比較の窓はこれで自動の比較を始めるかを決める(前後の行数だけを直しても比べ直さない)。
    """

    options: RawOptions | None
    problems: list[str]
    context_lines: int = RAW_CONTEXT_DEFAULT
    compare_options: RawOptions | None = None
    all_options: RawOptions | None = None
    disabled: RawDisabled = NO_DISABLED

    def __post_init__(self) -> None:
        if self.compare_options is None and self.options is not None:
            object.__setattr__(self, "compare_options", self.options)
        if self.all_options is None and self.options is not None:
            object.__setattr__(self, "all_options", self.options)


def raw_form_from_settings(settings: RawSettings) -> RawForm:
    """既定のファイル(raw-compare.json)の中身から作る欄の値(全部のルールと無効。比べるときの設定は有効なものだけ)。"""
    return RawForm(
        settings.enabled_options(), [], settings.context_lines, all_options=settings.options, disabled=settings.disabled
    )


def read_context_lines(value: object) -> int | None:
    """前後の行数の欄の値。整数で 0〜20 ならその値、ほか(null・真偽値・小数・範囲の外)は None。"""
    if type(value) is not int or not 0 <= value <= RAW_CONTEXT_MAX:
        return None
    return value


def read_raw_form(value: object) -> RawForm:
    """画面の欄の値を読む。形が違えば ValueError(入口は _BadRequest)。中身の誤りは problems に入れ、options を None にする。

    形: {"ignore_lines": 改行でつないだ式, "replacements": [[式, 置き換える文字], ...], "whitespace": 文字列,
    "ignore_blank_lines": 真偽値, "ignore_case": 真偽値, "context_lines": 数, "detect_moves": 真偽値, "algorithm": 文字列,
    "disabled": {"ignore_lines": [式, ...], "replacements": [[式, 置き換える文字], ...]}}(REQ-359)。
    誤りの確かめと重複の確かめは、無効のルールも含めた全部で行う。RawForm.options は無効のルールを除いたもの。
    disabled の中で今の欄に無い式・組は捨てる(式を書き換えたら新しいルールで有効)。
    無視する行は改行で分け、行の両端の空白を除いて空になった行は捨てる。
    置き換えは式が空の組を捨てる(置き換える文字は空でよい)。誤りの文の「何行目」「何組目」は、画面の欄の行と組の位置。
    前後の行数が整数で 0〜20 でなければ(null も)、problems に RAW_CONTEXT_PROBLEM を入れる(REQ-228 §2)。
    """
    if not isinstance(value, dict) or set(value) != set(_RAW_FORM_KEYS):
        raise ValueError(SMODEL_TEXTS["raw_form_shape"])
    ignore = value["ignore_lines"]
    if not isinstance(ignore, str) or len(ignore) > MAX_RAW_FORM_IGNORE_CHARS:
        raise ValueError(SMODEL_TEXTS["raw_ignore_shape"])
    items = value["replacements"]
    if (
        not isinstance(items, list)
        or len(items) > MAX_RAW_REPLACEMENT_ROWS
        or not all(isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item) for item in items)
    ):
        raise ValueError(SMODEL_TEXTS["raw_replace_shape"])
    if not isinstance(value["whitespace"], str):
        raise ValueError(SMODEL_TEXTS["raw_whitespace_shape"])
    if type(value["ignore_blank_lines"]) is not bool or type(value["ignore_case"]) is not bool:
        raise ValueError(SMODEL_TEXTS["raw_blank_case_shape"])
    if type(value["detect_moves"]) is not bool:
        raise ValueError(SMODEL_TEXTS["raw_moves_shape"])
    if not isinstance(value["algorithm"], str):
        raise ValueError(SMODEL_TEXTS["raw_algorithm_shape"])
    off = _read_disabled_form(value["disabled"])
    context = read_context_lines(value["context_lines"])
    ignore_lines: list[str] = []
    ignore_rows: list[int] = []  # 欄の何行目か(1 始まり)
    for number, line in enumerate(ignore.splitlines(), start=1):
        pattern = line.strip()
        if pattern:
            ignore_lines.append(pattern)
            ignore_rows.append(number)
    replacements: list[Replacement] = []
    replace_rows: list[int] = []
    for number, (pattern, text) in enumerate(items, start=1):
        if pattern != "":
            replacements.append(Replacement(pattern, text))
            replace_rows.append(number)
    options = RawOptions(
        ignore_lines=tuple(ignore_lines),
        replacements=tuple(replacements),
        whitespace=value["whitespace"],  # type: ignore[arg-type]  # 値は check_raw_options が確かめる
        ignore_blank_lines=value["ignore_blank_lines"],
        ignore_case=value["ignore_case"],
        detect_moves=value["detect_moves"],
        algorithm=value["algorithm"],  # type: ignore[arg-type]  # 値は check_raw_options が確かめる
    )
    problems = check_raw_options(options)
    # 手で書いた重複(ADR-0236 決定 1。check_raw_options は重複を確かめない)。誤りがある行・組には出さない
    duplicates = _raw_duplicates(options, problems, ignore_rows, replace_rows)
    context_lines = RAW_CONTEXT_DEFAULT if context is None else context
    context_problems = [RAW_CONTEXT_PROBLEM] if context is None else []
    # 無効のルール(REQ-359)。欄に無い式・組は捨てる。誤りの確かめと重複の確かめは、無効のものも含めた全部で済ませてある
    disabled = prune_disabled(options, off)
    active = enabled_options(options, disabled)
    if not problems and not duplicates and not context_problems:
        return RawForm(active, [], context_lines, all_options=options, disabled=disabled)
    if not problems and not duplicates:
        # 前後の行数だけの誤り。比べること・保存は止める(options は None)が、比べる設定は読めている
        return RawForm(None, context_problems, context_lines, active, options, disabled)
    rows = {"ignore_lines": ignore_rows, "replacements": replace_rows}
    shown = [
        replace(p, index=rows[p.field][p.index - 1]) if p.index is not None and p.field in rows else p for p in problems
    ]
    if duplicates:
        # 欄の順(無視する行 → 置き換え → 空白 → 比べ方)のまま、同じ欄の中は行・組の順に並べる(整列は安定)
        order = {"ignore_lines": 0, "replacements": 1, "whitespace": 2, "algorithm": 3}
        shown = sorted([*shown, *duplicates], key=lambda p: (order[p.field], p.index or 0))
    return RawForm(None, [*raw_option_problem_lines(shown), *context_problems], context_lines, None, options, disabled)


def _read_disabled_form(value: object) -> RawDisabled:
    """欄の値の disabled を読む(形の確かめ。形が違えば ValueError)。無視する行の式は両端の空白を除く(欄の読み方と同じ)。

    欄の中のルールに当たるかどうか(prune_disabled)は、ここでは見ない。
    """
    if not isinstance(value, dict) or set(value) != {"ignore_lines", "replacements"}:
        raise ValueError(SMODEL_TEXTS["raw_disabled_shape"])
    ignore, items = value["ignore_lines"], value["replacements"]
    if (
        not isinstance(ignore, list)
        or not isinstance(items, list)
        or len(ignore) > MAX_RAW_FORM_DISABLED_ROWS
        or len(items) > MAX_RAW_FORM_DISABLED_ROWS
        or not all(isinstance(x, str) for x in ignore)
        or not all(isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item) for item in items)
    ):
        raise ValueError(SMODEL_TEXTS["raw_disabled_shape"])
    return RawDisabled(
        tuple(x.strip() for x in ignore), tuple(Replacement(pattern, text) for pattern, text in items)
    )


def _raw_duplicates(
    options: RawOptions, problems: list[RawOptionProblem], ignore_rows: list[int], replace_rows: list[int]
) -> list[RawOptionProblem]:
    """同じ式の行・組(2 つ目より後)の誤り(kind は duplicate)。index と detail は画面の欄の位置(式の文字は入れない)。

    無視する行は読んだ式(両端の空白を除いた、空でない行)どうし、置き換えは式が空でない組の (式, 置き換える文字) の両方が
    ちょうど同じものを比べる(式の意味は比べない)。check_raw_options の誤りがある行・組は、最初の位置として覚えることも
    重複として出すこともしない(1 つの行・組には最初の 1 件)。
    """
    bad_ignore = {p.index for p in problems if p.field == "ignore_lines" and p.index is not None}
    bad_replace = {p.index for p in problems if p.field == "replacements" and p.index is not None}
    found: list[RawOptionProblem] = []
    first_ignore: dict[str, int] = {}
    for i, pattern in enumerate(options.ignore_lines, start=1):
        if i in bad_ignore:
            continue
        if pattern not in first_ignore:
            first_ignore[pattern] = ignore_rows[i - 1]
        else:
            found.append(RawOptionProblem("ignore_lines", ignore_rows[i - 1], "duplicate", str(first_ignore[pattern])))
    first_replace: dict[tuple[str, str], int] = {}
    for i, item in enumerate(options.replacements, start=1):
        if i in bad_replace:
            continue
        key = (item.pattern, item.replacement)
        if key not in first_replace:
            first_replace[key] = replace_rows[i - 1]
        else:
            found.append(RawOptionProblem("replacements", replace_rows[i - 1], "duplicate", str(first_replace[key])))
    return found



def raw_form_values(
    options: RawOptions, context_lines: int = RAW_FOLD_CONTEXT, disabled: RawDisabled = NO_DISABLED
) -> dict[str, Any]:
    """設定を画面の欄の値(read_raw_form と同じ形)にする。context_lines は前後の行数(表示だけの値)。

    options は無効のルールも含めた全部のルール、disabled は無効のルール(REQ-359。options に無いものは入れない)。
    """
    off = prune_disabled(options, disabled)
    return {
        "ignore_lines": "\n".join(options.ignore_lines),
        "replacements": [[r.pattern, r.replacement] for r in options.replacements],
        "whitespace": options.whitespace,
        "ignore_blank_lines": options.ignore_blank_lines,
        "ignore_case": options.ignore_case,
        "context_lines": context_lines,
        "detect_moves": options.detect_moves,
        "algorithm": options.algorithm,
        "disabled": {
            "ignore_lines": list(off.ignore_lines),
            "replacements": [[r.pattern, r.replacement] for r in off.replacements],
        },
    }


def raw_rule_rows(
    options: RawOptions | None, disabled: RawDisabled, record: RawOptionsRecord | None
) -> tuple[list[dict[str, Any]], str | None]:
    """単純比較のタブの「効いているルール」の一覧の行と見出し(REQ-359 §4)。ルールが 1 つも無ければ ([], None)。

    options は欄の全部のルール(読めなければ None)、disabled は無効のルール、record は表示中の結果の単純比較の記録(無ければ None)。
    行は無視する行 → 置き換えの順(欄の順)で、{kind: ignore|replace, kind_text, pattern, replacement, enabled, hits}。
    hits は「変更前 n・変更後 m」。無効のルール・まだ比べていないとき・記録に無いルール・数を持たない古い保存の結果は「—」。
    当たった行の数は、記録の options の中で同じ式(置き換えは同じ組)のルールを探して、その位置の rule_hits から引く。
    """
    if options is None or (not options.ignore_lines and not options.replacements):
        return [], None
    off_ignore, off_replace = set(disabled.ignore_lines), set(disabled.replacements)
    hits = record.rule_hits if record is not None else None
    recorded_ignore: list[Any] = list(record.options.get("ignore_lines", [])) if record is not None else []
    recorded_replace: list[tuple[Any, Any]] = (
        [(r.get("pattern"), r.get("replacement")) for r in record.options.get("replacements", [])]
        if record is not None
        else []
    )

    def hits_text(kind: str, index: int | None) -> str:
        found = None if hits is None or index is None else hits.get("ignore_lines" if kind == "ignore" else "replacements")
        if found is None or index >= len(found):  # type: ignore[operator]
            return RAW_RULE_NO_HITS
        return raw_rule_hits_text(found[index]["before"], found[index]["after"])  # type: ignore[index]

    rows: list[dict[str, Any]] = []
    for pattern in options.ignore_lines:
        enabled = pattern not in off_ignore
        index = recorded_ignore.index(pattern) if enabled and pattern in recorded_ignore else None
        rows.append(
            {
                "kind": "ignore",
                "kind_text": RAW_RULE_KIND_TEXT["ignore"],
                "pattern": pattern,
                "replacement": "",
                "enabled": enabled,
                "hits": hits_text("ignore", index),
            }
        )
    for item in options.replacements:
        enabled = item not in off_replace
        pair = (item.pattern, item.replacement)
        index = recorded_replace.index(pair) if enabled and pair in recorded_replace else None
        rows.append(
            {
                "kind": "replace",
                "kind_text": RAW_RULE_KIND_TEXT["replace"],
                "pattern": item.pattern,
                "replacement": item.replacement,
                "enabled": enabled,
                "hits": hits_text("replace", index),
            }
        )
    off_count = sum(1 for row in rows if not row["enabled"])
    return rows, raw_rules_title(len(rows) - off_count, off_count)


def raw_line_rules(rows: Sequence[RawRow], record: RawOptionsRecord | None) -> list[dict[str, Any]]:
    """単純比較の表の行ごとの「効いているルール」の表示用の値(REQ-366 §5)。当たったルールのある行だけを返す。

    形は [{index, sides: [{title, rules: [{kind, kind_text, pattern, replacement}], none}]}]。kind は ignore か replace(画面が置き換える文字の
    出し方を決める印。置き換える文字が空の置き換えと無視する行を見分ける)。index は rows の番号、sides はその行にある側
    (変更前・変更後)だけ。title は「変更前（12 行目）」。当たったルールが無い側は rules が空で、none に理由の文が入る(ほかは None)。
    ルールの式・置き換える文字は、記録の options(core に渡した有効なルール)の、line_hits の番号の位置から引く。
    記録が無い・line_hits を持たない古い保存の結果は [] 。
    """
    if record is None or record.line_hits is None:
        return []
    ignore_lines = record.options.get("ignore_lines", [])
    replacements = record.options.get("replacements", [])
    by_side = {
        side: {hit["line_no"]: hit for hit in record.line_hits.get(side, [])} for side in ("before", "after")  # type: ignore[attr-defined]
    }

    def rules_of(hit: Any) -> list[dict[str, str]]:
        found: list[dict[str, str]] = []
        number = hit["ignore_line"]
        if number is not None and 1 <= number <= len(ignore_lines):
            found.append(
                {"kind": "ignore", "kind_text": RAW_RULE_KIND_TEXT["ignore"], "pattern": ignore_lines[number - 1], "replacement": ""}
            )
        for number in hit["replacements"]:
            if 1 <= number <= len(replacements):
                item = replacements[number - 1]
                found.append(
                    {
                        "kind": "replace",
                        "kind_text": RAW_RULE_KIND_TEXT["replace"],
                        "pattern": item.get("pattern", ""),
                        "replacement": item.get("replacement", ""),
                    }
                )
        return found

    result: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        sides: list[dict[str, Any]] = []
        any_rules = False
        for side, line_no in (("before", row.before_no), ("after", row.after_no)):
            if line_no is None:
                continue
            hit = by_side[side].get(line_no)
            rules = rules_of(hit) if hit is not None else []
            any_rules = any_rules or bool(rules)
            sides.append(
                {
                    "title": raw_line_rules_side(side, line_no),
                    "rules": rules,
                    "none": None if rules else RAW_LINE_RULES_NONE,
                }
            )
        if any_rules:
            result.append({"index": index, "sides": sides})
    return result


# --- 保存したルールを開いている窓へ届ける・この窓だけのルールをいつも効く設定に足す(REQ-361 / ADR-0241) ---

RawBaseline = tuple[RawOptions, RawDisabled]  # 「最後に既定を読んだときの値」(全部のルール, 無効のルール)


def raw_baseline(settings: RawSettings) -> RawBaseline:
    """既定のファイルの中身から、「最後に既定を読んだときの値」を作る(無効のルールは今のルールに当たるものだけ)。"""
    return settings.options, prune_disabled(settings.options, settings.disabled)


def raw_typed_differs(form: RawForm, baseline: RawBaseline) -> bool:
    """欄の今の値が「最後に既定を読んだときの値」と違うか(「直接書いた値あり」)。

    欄の値(read_raw_form で読んだ RawForm)の全部のルールと、欄の中のルールに当たる無効のルールを比べる。前後の行数は表示だけの値
    なので比べない。欄の値は、空の置き換えの行・無効の並びなど意味が同じでも文字としては違うことがあるので、dict のままは比べない。
    全部のルールが読めない(None)ときは違うとみなす。
    """
    if form.all_options is None:
        return True
    return (form.all_options, prune_disabled(form.all_options, form.disabled)) != baseline


@dataclass(frozen=True)
class RawAdditions:
    """いつも効く設定に足すルール(窓の並びのまま)。"""

    ignore_lines: tuple[str, ...] = ()
    replacements: tuple[Replacement, ...] = ()

    def count(self) -> int:
        return len(self.ignore_lines) + len(self.replacements)


def raw_additions(window: RawOptions, saved: RawSettings) -> RawAdditions:
    """窓の有効なルールのうち、既定のファイルの全部のルール(無効も含む)に無いもの。

    無視する行は式で、置き換えは (式, 置き換える文字) で比べる。窓の並びのまま集め、重なりは 1 つにする。
    """
    have_ignore, have_replace = set(saved.options.ignore_lines), set(saved.options.replacements)
    ignore = [p for p in dict.fromkeys(window.ignore_lines) if p not in have_ignore]
    replacements = [r for r in dict.fromkeys(window.replacements) if r not in have_replace]
    return RawAdditions(tuple(ignore), tuple(replacements))


@dataclass(frozen=True)
class RawAppended:
    """append_raw_rules の結果。settings は足した後の既定(上限を超えるなら None)。ignore・replace は足した後の個数と組数。"""

    settings: RawSettings | None
    ignore: int
    replace: int


def append_raw_rules(saved: RawSettings, additions: RawAdditions) -> RawAppended:
    """既定のファイルの中身の最後に、足すルールを足す。空白・空行・大文字小文字・移動・比べ方・前後の行数・無効のルールはそのまま。

    足した後に無視する行が MAX_PATTERNS 個、置き換えが MAX_PATTERNS 組を超えるなら、1 つも足さずに settings を None にする
    (ちょうど MAX_PATTERNS は足せる)。
    """
    ignore = (*saved.options.ignore_lines, *additions.ignore_lines)
    replacements = (*saved.options.replacements, *additions.replacements)
    if len(ignore) > MAX_PATTERNS or len(replacements) > MAX_PATTERNS:
        return RawAppended(None, len(ignore), len(replacements))
    options = replace(saved.options, ignore_lines=ignore, replacements=replacements)
    return RawAppended(replace(saved, options=options), len(ignore), len(replacements))


def raw_defaults_file(
    *, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> tuple[Path, RawSettings]:
    """既定のファイル(raw-compare.json)の場所と中身(無効のルールも含めた全部)。読めない・形が違えば SettingsError。

    比較の窓の「いつも効く設定に足す」が、足す前に読み直すために使う(読み書きは screen_model だけが通る)。
    """
    path = raw_settings_path(platform=platform, env=env, home=home).absolute()
    return path, load_raw_settings(path)


def write_raw_defaults_file(path: Path, settings: RawSettings) -> None:
    """既定のファイルを書く(設定の窓の保存と同じ書き方。無効のルール・前後の行数も書く)。書けなければ SettingsError。"""
    save_raw_defaults(path, settings.options, settings.context_lines, settings.disabled)


def raw_add_confirm_body(additions: RawAdditions) -> str:
    """確認の本文(1 行に 1 つ。「無視する行: 式」「置き換え: 式 → 置き換える文字」。無視する行 → 置き換えの順)。"""
    lines = [raw_add_confirm_line("ignore", pattern, None) for pattern in additions.ignore_lines]
    lines += [raw_add_confirm_line("replace", item.pattern, item.replacement) for item in additions.replacements]
    return "\n".join(lines)


def raw_examples() -> dict[str, list[dict[str, str]]]:
    """例から足す選択肢(文と式。RAW_IGNORE_EXAMPLES・RAW_REPLACE_EXAMPLES)。画面は文字を textContent と value で入れる。"""
    return {
        "ignore": [{"label": label, "pattern": pattern} for label, pattern in RAW_IGNORE_EXAMPLES],
        "replace": [
            {"label": label, "pattern": pattern, "replacement": text} for label, pattern, text in RAW_REPLACE_EXAMPLES
        ],
    }


def raw_options_summary(options: RawOptions | Mapping[str, Any] | None, disabled_count: int = 0) -> str | None:
    """「単純比較の設定: 無視する行 2 件・置き換え 1 組・空白の数を無視・空行を無視・大文字と小文字を無視」。既定なら None。

    options は設定か、保存の形(options_to_plain)。数は有効なルールの数(呼ぶ側が無効を除いた設定を渡す)。
    disabled_count(REQ-359)が 1 以上なら「（無効 M）」を足す。有効なルールもほかの設定も既定なら「単純比較の設定: なし（無効 M）」。
    ただし options が None(誤りのある間)のときは None。
    """
    if options is None:
        return None
    plain = options_to_plain(options) if isinstance(options, RawOptions) else dict(options)
    return raw_summary_with_disabled(raw_options_summary_text(plain), disabled_count)


def raw_set_aside_text(record: RawOptionsRecord | None) -> str | None:
    """「無視した行 12 行（変更前 7・変更後 5）・同じとみなした行 3 組」。どちらも 0(または設定なし)なら None。

    REQ-228: 後ろに「・移動 2 か所」(探さなかったときは「・移動は差分が多いため探していません」)と、最小が標準に戻ったときの
    「・比べ方: 最小（変更が多いため標準で比べました）」を足す。どれも無ければ None。
    """
    if record is None:
        return None
    parts = [
        raw_set_aside_line(
            len(record.ignored.get("before", [])), len(record.ignored.get("after", [])), len(record.equated)
        ),
        raw_moves_part(len(record.moves), record.moves_skipped),
    ]
    if record.options.get("algorithm") == "minimal" and record.algorithm_used == "standard":
        parts.append(RAW_ALGORITHM_FELL_BACK)
    shown = [part for part in parts if part is not None]
    return SM_SET_ASIDE_SEPARATOR.join(shown) if shown else None


def raw_set_aside_rows(rows: Sequence[RawRow]) -> list[int]:
    """表の行のうち、無視した行("x")・同じとみなした行("=")・移動元("<")・移動先(">")の番号。"""
    return [k for k, row in enumerate(rows) if row.mark in ("x", "=", "<", ">")]


def raw_record_summary(record: RawOptionsRecord | None) -> str | None:
    """結果の記録から作る設定の要約(最小が標準に戻ったときはその文。REQ-228 §8.3)。設定が無ければ None。"""
    if record is None:
        return None
    return raw_options_summary_text(dict(record.options), record.algorithm_used)


def outcome_raw_options(outcome: CompareOutcome | None) -> RawOptionsRecord | None:
    """結果の単純比較の設定の記録(raw か、show を単純比較で比べたときの show.raw)。無ければ None。"""
    if outcome is None:
        return None
    raw = outcome.raw if outcome.raw is not None else (outcome.show.raw if outcome.show is not None else None)
    return raw.options if raw is not None else None


def saved_raw_settings_line(outcome: CompareOutcome | None, settings: Mapping[str, Any] | None = None) -> str | None:
    """保存した結果を開いたときの窓の下の行(「保存した結果の単純比較の設定: …」)。

    結果の options(result.raw.options.options)から作り、無ければ保存の settings.raw_options から作る。どちらも無ければ None。
    """
    record = outcome_raw_options(outcome)
    if record is not None:
        summary = raw_record_summary(record)
    else:
        plain = (settings or {}).get("raw_options")
        summary = raw_options_summary(plain) if plain is not None else None
    return None if summary is None else RAW_SETTINGS_SAVED_PREFIX + summary


def raw_defaults(
    *, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> tuple[RawOptions, str | None]:
    """既定の設定(raw-compare.json)。読めなければ (すべてオフ, 帯の文 RAW_DEFAULTS_UNREADABLE)。"""
    try:
        return load_raw_defaults(raw_settings_path(platform=platform, env=env, home=home)), None
    except SettingsError as exc:
        return DEFAULT_RAW_OPTIONS, raw_defaults_unreadable(str(exc))


def raw_form_defaults(
    *, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> tuple[RawSettings, str | None]:
    """既定の設定・前後の行数・無効のルール(raw-compare.json。REQ-228 §2・REQ-359)と帯の文。

    設定は無効のルールも含めた全部(RawSettings.options)。比べるときの設定は RawSettings.enabled_options()。
    読めなければ (すべてオフ・前後の行数 3・無効なし, 帯の文)。
    """
    try:
        loaded = load_raw_settings(raw_settings_path(platform=platform, env=env, home=home))
    except SettingsError as exc:
        return RawSettings(DEFAULT_RAW_OPTIONS, RAW_FOLD_CONTEXT), raw_defaults_unreadable(str(exc))
    return loaded, None


# --- LLM(ADR-0018 決定6。送信前のプレビューを必須とする) ---


@dataclass(frozen=True)
class LlmPlan:
    """LLM 解析の進め方。send が None なら1回で終える(client を作れない。結果の LLM 欄に理由が残る。ADR-0018 決定4)。

    send があるときは、first(dry_run)で比較してプレビューを見せ、承認されたときだけ send で2回目を実行する。
    """

    first: LlmRequest
    send: LlmRequest | None = None


def plan_llm(
    settings: LlmSettings,
    *,
    max_diffs: int | None = None,
    client_factory: Callable[..., LLMClient] | None = None,
    look_rules: bool = True,
) -> LlmPlan:
    """max_diffs を省くと settings.max_diffs(利用者ごとの設定ファイルの値か既定。ADR-0071 決定5)。

    look_rules が偽なら、送る client に答えの見え方の決まりを足さない(組の案を聞く送信だけ。ADR-0177 決定4)。
    """
    send = build_llm_request(settings, max_diffs=max_diffs, client_factory=client_factory, look_rules=look_rules)
    if send.client is None:
        return LlmPlan(first=send)
    return LlmPlan(first=build_llm_request(settings, dry_run=True, max_diffs=max_diffs), send=send)


def with_instruction(plan: LlmPlan, text: str) -> LlmPlan:
    """解析の前に打った文(確かめた後の文)を、送らない1回目と送る2回目の両方に入れた計画(REQ-206 §6 / ADR-0148 決定6)。

    両方に同じ文を入れるので、送信前の確認の全文と、承認した内容の照合(approve)が一致する。
    """
    send = None if plan.send is None else replace(plan.send, extra_instruction=text)
    return LlmPlan(first=replace(plan.first, extra_instruction=text), send=send)


def with_question(
    plan: LlmPlan,
    question: str,
    prefer: Literal["structured", "raw"] | None,
    category: str | None,
    target: str,
    raw_lines: tuple[int | None, int | None] | None = None,
    skip_structured: tuple[int, ...] | None = None,
    skip_raw: tuple[int, ...] | None = None,
) -> LlmPlan:
    """表示中の差分への質問(確かめた後の文)と、送る差分の種類・カテゴリ・対象を、送らない1回目と送る2回目の両方に入れた計画
    (REQ-312 / ADR-0218 決定2〜4)。

    両方に同じ値を入れるので、送信前の確認の全文と、承認した内容の照合(approve)が一致する。
    raw_lines は単純比較の 1 行だけを聞くときの (変更前の行番号, 変更後の行番号。REQ-366 §6)。
    skip_structured は構造化比較の表で隠している行の位置(送らない構造化差分。REQ-389 §5)。None なら今と同じ。
    skip_raw は単純比較の表で、印の列の絞り込みで隠している行の差分の位置(REQ-391 §6)。None なら今と同じ。
    """
    fields: dict[str, Any] = {
        "question": question,
        "prefer_source": prefer,
        "category": category,
        "question_target": target,
    }
    if raw_lines is not None:
        fields["raw_lines"] = raw_lines
    if skip_structured is not None:
        fields["skip_structured"] = skip_structured
    if skip_raw is not None:
        fields["skip_raw"] = skip_raw
    send = None if plan.send is None else replace(plan.send, **fields)
    return LlmPlan(first=replace(plan.first, **fields), send=send)


def with_full_text(plan: LlmPlan) -> LlmPlan:
    """変更後の全文も送る計画(ADR-0158 決定6)。送らない1回目と送る2回目の両方に付ける。

    両方に付けるので、送信前の確認の全文と、承認した内容の照合(approve)が一致する。
    """
    send = None if plan.send is None else replace(plan.send, include_after_text=True)
    return LlmPlan(first=replace(plan.first, include_after_text=True), send=send)


def wants_preview(plan: LlmPlan | None, outcome: CompareOutcome) -> bool:
    """1回目の結果からプレビューに進むか。送る内容があるときだけ(差分なし・送れる行なしでは送らない)。"""
    return plan is not None and plan.send is not None and outcome.llm is not None and outcome.llm.reason == "dry_run"


@dataclass(frozen=True)
class PreviewView:
    """送信前のプレビュー。値は送る内容(マスク後)だけで、送らなかった行は件数と形だけ(ADR-0019 決定5)。"""

    payload: str
    summary: str
    truncated: list[str]
    unsent: str | None
    unsent_shapes: list[str]


def preview_view(llm: LlmAnalysis) -> PreviewView:
    return PreviewView(
        payload=llm.payload or "",
        summary=sm_preview_summary(llm.sent_diffs, llm.diff_total),
        truncated=llm_truncated_lines(llm) if llm.truncated else [],
        unsent=llm_unsent_line(llm),
        unsent_shapes=llm_unsent_shape_lines(llm),
    )


def approve(plan: LlmPlan, preview: LlmAnalysis, edit: InstructionEdit | None = None) -> LlmRequest:
    """プレビューで「送信する」を押したときの要求。承認した内容(preview.payload)以外は送らない。

    edit(送る前の確認で直した指示。REQ-266)があれば、承認する文は直した後の文(edit.text)で、core が組み立て直した差分と
    直した指示でつないだ文と1字でも違えば送らない。edit が無ければ今と同じ。
    """
    if plan.send is None or plan.send.client is None or preview.payload is None:
        raise ValueError(SMODEL_TEXTS["no_plan_or_preview"])
    if edit is None:
        return replace(plan.send, client=ApprovedPromptClient(plan.send.client, preview.payload))
    return replace(plan.send, client=ApprovedPromptClient(plan.send.client, edit.text), instruction_edit=edit)


# 前提のファイルを読めないときに送る要求に入れる、どの文も承認していない印(空の文は送る文にならない)
_NOTHING_APPROVED = ""


def with_current_premise(
    request: LlmRequest,
    *,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> LlmRequest:
    """送信前の確認で「送る」を押した後、送る直前に前提の文を読み直して送る要求に入れる(ADR-0172 決定5)。

    窓は確認の前に作った計画を使い回すので、確認の後に前提を書き換えた・消したときは、読み直した前提で送る文を作り直す。
    作り直した文は承認した文と違うので、core が承認との照合で止める(changed_after_approval。送らない)。
    前提のファイルを読めないときは確かめられないので、どの文も承認していない client にする(同じく changed_after_approval)。
    送らない要求(client が無い)はそのまま返す。
    """
    if request.client is None:
        return request
    try:
        premise = current_premise(env=env, platform=platform, home=home)
    except SettingsError:
        return replace(request, client=ApprovedPromptClient(request.client, _NOTHING_APPROVED))
    return replace(request, premise=premise)


@dataclass(frozen=True)
class SendBasis:
    """送る前の確認を出したときの設定の値(REQ-371 / ADR-0245 決定1)。承認した送信の直前に、今の設定と比べる。

    秘密の語・使用中のプロファイルの送り先の接続先とモデル・送る差分の上限・全体の上限。キーは持たない。
    秘密の語は repr に出さない(ほかの値は設定の値で、秘密ではない)。
    """

    secret_words: tuple[str, ...] = field(repr=False)
    base_url: str | None
    model: str | None
    max_diffs: int
    max_total_diffs: int
    # 送る文全体の上限(ADR-0259 決定7)。basis_change では比べない(上げただけなら承認した文のまま送る)。下がったかは prompt_limit_lowered で見る
    max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS


def send_basis(settings: LlmSettings) -> SendBasis:
    """設定から、確認と一緒に持つ値を作る。"""
    return SendBasis(
        secret_words=tuple(settings.extra_secret_words),
        base_url=settings.base_url,
        model=settings.model,
        max_diffs=settings.max_diffs,
        max_total_diffs=settings.max_total_diffs,
        max_prompt_chars=settings.max_prompt_chars,
    )


def current_send_basis(
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    platform: str | None = None,
    home: Path | None = None,
) -> SendBasis:
    """送る直前に設定を読み直して、今の値を返す(REQ-371)。指示文と前提のファイルは読まない(前提は with_current_premise が読む)。

    読めなければ SettingsError(呼ぶ側は送らない)。
    """
    return send_basis(load_settings(cwd=cwd, env=env, platform=platform, home=home, read_prompt=False))


def basis_change(confirmed: SendBasis, now: SendBasis) -> str | None:
    """確認を出したときの値と今の値を比べる。違いの種類を messages.REREAD_NOT_SENT のキーで返し、同じなら None。

    比べる順は、使用中のプロファイル(接続先かモデル)→ 上限(送る差分・全体)→ 秘密の語。
    秘密の語は並びを問わず、語の集まりが違うときだけ違う(語が違っても、送る文が変わるかは呼ぶ側が作り直して確かめる)。
    """
    if confirmed.base_url != now.base_url or confirmed.model != now.model:
        return "profile"
    if confirmed.max_diffs != now.max_diffs or confirmed.max_total_diffs != now.max_total_diffs:
        return "limits"
    if set(confirmed.secret_words) != set(now.secret_words):
        return "secret_words"
    return None


def prompt_limit_lowered(confirmed: SendBasis, now: SendBasis, length: int) -> bool:
    """送る文全体の上限が確認のときより下がり、しかも承認した文の長さ(length)が今の上限を超えるときだけ真(ADR-0259 決定7)。

    basis_change はこの上限を比べない。上限を上げただけ・下げても承認した文が収まるときは、承認した文のまま送る。
    """
    return now.max_prompt_chars < confirmed.max_prompt_chars and length > now.max_prompt_chars


# --- 保存(ADR-0017。自動保存はしない) ---


def is_current(compared: ScreenInput | None, current: ScreenInput | None) -> bool:
    """保存してよいか。最後に比較したときの入力と、いまの画面の入力が同じときだけ真。

    比較の後に入力(参照・貼り付け)や比較の設定・区間・LLM の有無を変えたら、表示中の結果はいまの入力の結果ではない。
    そのまま保存すると、画面の入力とは違う比較の結果が残るため、比較し直すまで保存させない。
    """
    return compared is not None and current is not None and compared == current


@dataclass(frozen=True)
class SaveState:
    """入力や設定を変えたときの、保存ボタンとステータスバー左の状態。status が None ならステータスバーは触らない。"""

    enabled: bool
    status: str | None
    stale: bool


def save_state(compared: ScreenInput | None, current: ScreenInput | None, result_status: str, was_stale: bool) -> SaveState:
    """保存できるかと、ステータスバーに出し直す文。

    入力が比較したときと違えば「前の入力の結果」と出す。元に戻して同じになったら、結果の状態の文(result_status)に戻す。
    もともと同じだったときは触らない(保存した・中止したなどの直前の文を消さない)。
    """
    if compared is None:
        return SaveState(enabled=False, status=None, stale=False)
    if is_current(compared, current):
        return SaveState(enabled=True, status=result_status if was_stale else None, stale=False)
    return SaveState(enabled=False, status=STALE_RESULT, stale=True)


def build_save_record(
    inp: ScreenInput, request: CompareRequest, outcome: CompareOutcome, *, conversation: Conversation | None = None
) -> dict[str, Any]:
    """保存する中身。入力のパスが分かる側は source_paths に入れ、貼り付けの側は null にする。

    conversation は比較の窓での AI とのやり取り(ADR-0179 決定2)。書くかは storage の決まり(答えがあり、どれかに値があるとき)。
    """
    if conversation is not None:
        outcome = replace(outcome, conversation=conversation)
    return build_record(request, outcome, source_paths=source_paths(inp))


def suggested_filename(record: dict[str, Any]) -> str:
    """保存ダイアログの既定のファイル名(storage と同じ名前の付け方)。"""
    return default_filename(record)


def save_result(record: dict[str, Any], target: str | Path) -> str:
    """保存して、ステータスに出す1行を返す(CLI の --save と同じ文)。StorageError はそのまま送出する。"""
    return saved_notice(save(record, target))


# --- 保存した結果を開く(REQ-120 / ADR-0083。読むだけで、比較し直さない・AI に送らない) ---

# 開く保存ファイルの大きさの上限。超えれば読まない
MAX_RESULT_MB = 200
MAX_RESULT_BYTES = MAX_RESULT_MB * 1024 * 1024


class SavedResultError(Exception):
    """保存した結果を開けない。文は決まった文だけ(ファイルの中身・パス・storage の例外の文を入れない)。"""


def read_saved(path: str | Path) -> StoredRecord:
    """保存した結果を読む。大きさを先に見て、上限を超えれば読まない。失敗は SavedResultError(決まった文)にする。

    storage の例外の文には、見つかった版の値・形の違う値・JSON のパーサーの文・パスが入るので、画面には出さない。
    """
    return read_saved_raw(path)[1]


def read_saved_raw(path: str | Path) -> tuple[dict[str, Any], StoredRecord]:
    """read_saved と同じ決まりで読み、読み込んだ JSON の dict も返す(開いた結果を保存し直すため。REQ-242 §4)。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise SavedResultError(OPEN_RESULT_UNREADABLE) from exc
    if size > MAX_RESULT_BYTES:
        raise SavedResultError(open_result_too_large(MAX_RESULT_MB))
    try:
        return load_raw(p)
    except UnsupportedSchemaVersion as exc:
        raise SavedResultError(OPEN_RESULT_VERSION) from exc
    except StorageFormatError as exc:
        if isinstance(exc.__cause__, (json.JSONDecodeError, UnicodeDecodeError)):
            raise SavedResultError(OPEN_RESULT_NOT_JSON) from exc
        raise SavedResultError(OPEN_RESULT_FORMAT) from exc
    except StorageError as exc:
        raise SavedResultError(OPEN_RESULT_UNREADABLE) from exc
    except (ValueError, RecursionError) as exc:
        # json の桁数の上限・深すぎる入れ子など、storage が形の違いとして包まない失敗
        raise SavedResultError(OPEN_RESULT_FORMAT) from exc


def opened_view(record: StoredRecord) -> tuple[OutcomeView, str]:
    """開いた結果の表示用のデータと帯の文。表は比較したときと同じ view_outcome で作る。

    値の欄は保存した値(IP の値は文字列、タプルはリスト)を repr したものになる(決定者の判断。REQ-120)。
    """
    inputs = record.meta["inputs"]
    band = opened_band(record.meta["saved_at"], inputs["before"]["name"], inputs["after"]["name"])
    view = replace(view_outcome(record.outcome), raw_settings_saved=saved_raw_settings_line(record.outcome, record.meta["settings"]))
    return view, band


# --- 複数のコマンドのまとめての比較(REQ-122 / ADR-0084 決定3・5・6。pywebview の比較の画面だけ) ---
# 入力のどちらかに区間が複数あり、区間の選択が両方「自動」なら core.compare_all で組ごとに比べる(compare_screen)。
# 区間を選んだとき(片側だけでも)は今の run_compare で1組を比べる(core の「選択待ち」は変えない)。
# 件数と状態は core の結果を読むだけで、ここで数え直さない。相手なしの区間は保存しない(決定者の判断)。
# AI は全部の組を1つの文にして1回で送る(core.analyze_all。ADR-0152 決定3)。確認は1組と同じ送信前の確認(全文)で、組のチェックは無い。
# 1つのログの中で組にしたとき(ADR-0152 決定2)は、組の名前に区間の番号を添え、残りの区間を「1 回だけ」の行にする。


def compare_screen(
    request: CompareRequest,
    *,
    llm: LlmRequest | None = None,
    on_progress: ProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
    runner: Callable[..., CompareOutcome] = run_compare,
    max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS,
) -> CompareOutcome | MultiCompareOutcome:
    """比較の画面の比べる関数(CompareWorker の runner)。まとめて比べる場面(core.wants_all)なら compare_all、ほかは runner。

    まとめて比べるときは compare_all に llm を渡さず、llm があれば後から全部の組をまとめて1回で解析する
    (send_approved。ADR-0152 決定3)。max_total_diffs はそのときの全体の上限(設定の値)。承認した2回目は、
    1組の比較と同じく approve で包んだ client を llm に入れて呼ぶ(送る文が確認と違えば changed_after_approval)。
    """
    if wants_all(request):
        return send_approved(
            request, llm=llm, max_total_diffs=max_total_diffs, on_progress=on_progress, should_cancel=should_cancel
        )
    return runner(request, llm=llm, on_progress=on_progress, should_cancel=should_cancel)


def auto_send_all(
    request: CompareRequest,
    *,
    plan: LlmPlan,
    max_total_diffs: int,
    llm: LlmRequest | None = None,
    on_progress: ProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MultiCompareOutcome:
    """自動送信(REQ-117)のまとめての比較。確認なしに、全部の組をまとめて1回で送る(ADR-0152 決定3)。

    llm は CompareWorker が渡す値で、使わない(送り方は plan で決める)。送れない設定(send が無い)では first で解析し、
    送らなかった理由が全体の AI の欄に残る。全体の上限を超えたとき・送る内容が無いときは送らない(analyze_whole)。
    """
    send = plan.send if plan.send is not None else plan.first
    return send_approved(
        request, llm=send, max_total_diffs=max_total_diffs, on_progress=on_progress, should_cancel=should_cancel
    )


def send_approved(
    request: CompareRequest,
    *,
    llm: LlmRequest | None,
    max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS,
    on_progress: ProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MultiCompareOutcome:
    """まとめて比べ、llm があれば全部の組をまとめて1回で解析する(core.analyze_all。ADR-0152 決定3)。

    llm は送らない1回目(dry_run)か、承認した要求(approve で包んだ client)か、自動送信の要求。
    1組の run_compare と同じく、工程の境目(解析の前と後)で中止の印を見る(解析の前なら送らない。答えの途中で中止したときは
    CompareCancelled になり、窓は前の結果と途中の答えを残す)。
    """
    # 進み具合は組ごとに「何組目か・組の数・コマンド」で知らせる(REQ-264 §3)。組の中の工程の名前は流さない
    # (何組目かの文が工程の名前で上書きされないように。組の境目の中止の確かめは compare_all が今のまま行う)
    def on_pair(index: int, total: int, command: str | None) -> None:
        if on_progress is not None:
            on_progress(PairProgress(index, total, command))

    outcome = compare_all(request, should_cancel=should_cancel, on_pair=on_pair)
    if llm is None:
        return outcome
    return analyze_screen(
        request, outcome=outcome, llm=llm, max_total_diffs=max_total_diffs, on_progress=on_progress, should_cancel=should_cancel
    )


def analyze_screen(
    request: CompareRequest,
    *,
    outcome: CompareOutcome | MultiCompareOutcome,
    llm: LlmRequest | None,
    max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS,
    on_progress: ProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
    chosen: Collection[int] | None = None,
) -> CompareOutcome | MultiCompareOutcome:
    """比べ終わった結果から AI の解析の工程だけを行う(比較の窓の「AI で解析」。REQ-264 §2。CompareWorker の runner)。

    chosen はまとめての結果で送る組の pairs の位置(REQ-265。None はすべて。1つのコマンドの結果では使わない)。

    切り分け・組にすること・比較・安全網はやり直さない。まとめての結果は全部の組を1回で解析し(analyze_all。ADR-0152 決定3)、
    1つのコマンドの結果は run_compare の最後の工程と同じ判断で解析する(analyze_compared)。工程の境目(解析の前と後)で
    中止の印を見る(send_approved と同じ順)。llm が無ければ結果をそのまま返す。
    """
    if llm is None:
        return outcome
    _enter("llm", on_progress, should_cancel)
    if isinstance(outcome, MultiCompareOutcome):
        analyzed: CompareOutcome | MultiCompareOutcome = analyze_all(
            outcome, llm, max_total_diffs=max_total_diffs, chosen=chosen
        )
    else:
        analyzed = analyze_compared(request, outcome, llm)
    _enter("done", on_progress, should_cancel)
    return analyzed


def analyze_pair_screen(
    request: CompareRequest,
    *,
    outcome: MultiCompareOutcome,
    llm: LlmRequest | None,
    position: int,
    on_progress: ProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MultiCompareOutcome:
    """まとめての比較の1組だけを AI に送る(組を選んで構造化・単純のタブで聞くとき。REQ-312 §4.3)。

    その組の結果を1つのコマンドの結果と同じ判断で解析し(analyze_compared。機種の判定の食い違い・失敗の組は今と同じく送らない)、
    pairs[position] の llm だけを入れ替える。全体の答え(outcome.llm・llm_pairs)は外す(この答えは組の行に入る)。
    request は使わない(組ごとの要求は PairResult.request)。llm が無ければ結果をそのまま返す。工程の境目で中止の印を見る。
    """
    if llm is None:
        return outcome
    _enter("llm", on_progress, should_cancel)
    pair = outcome.pairs[position]
    analyzed = analyze_compared(pair.request, pair.outcome, llm)
    pairs = [replace(p, outcome=analyzed) if k == position else p for k, p in enumerate(outcome.pairs)]
    result = replace(outcome, pairs=pairs, llm=None, llm_pairs=())
    _enter("done", on_progress, should_cancel)
    return result


def _enter(stage: str, on_progress: ProgressSink | None, should_cancel: Callable[[], bool] | None) -> None:
    """工程の入口(core の run_compare と同じ順。中止の印を見てから進捗を知らせる)。"""
    if should_cancel is not None and should_cancel():
        raise CompareCancelled(stage)
    if on_progress is not None:
        on_progress(stage)


def _segment_choices(segments: Sequence[Any]) -> list[SegmentChoice]:
    return [SegmentChoice(s.number, segment_label(s), s.line_count, False) for s in segments]


def segment_views(outcome: MultiCompareOutcome, request: CompareRequest) -> dict[Side, SideView]:
    """まとめて比べたときの区間の選択欄(両側の区間の一覧。選択は「自動」のまま。「選んでください」は出さない)。

    区間の一覧は組の結果の切り分け(どの組も同じ一覧を持つ)から取る。組が無い(全部が相手なし)ときは相手なしの区間から作る。
    """
    for pair in outcome.pairs:
        seg = pair.outcome.segmentation
        if seg is not None:
            return {
                side: SideView(seg[side].name, len(seg[side].split.segments), _segment_choices(seg[side].split.segments), None, False)
                for side in SIDES
            }
    views: dict[Side, SideView] = {}
    for side in SIDES:
        # 相手なしの区間とエラーの出力の区間(組にしなかった区間)を合わせて番号の順にする
        left = (*outcome.unpaired, *outcome.error_outputs)
        segments = sorted((u.segment for u in left if u.side == side), key=lambda s: s.number)
        name = request.before.name if side == "before" else request.after.name
        views[side] = SideView(name, len(segments), _segment_choices(segments), None, False)
    return views


@dataclass(frozen=True)
class MultiRow:
    """一覧の1行。diffs は構造化(show の構造化)の件数、無ければ単純比較の件数、比べていなければ「—」。"""

    name: str
    status: str
    diffs: str
    unpaired: bool
    # 比較の窓で手で組にした組か(REQ-269。状態の近くに「手で組にした」の印を出す)
    manual: bool = False
    # show の比べ方の印(REQ-282。単純比較で出した組は "raw"、表と単純比較の両方の組は "both"、ほかは None)
    show_method: str | None = None


@dataclass(frozen=True)
class MultiEntry:
    """一覧の1行と、選んだときに出す結果。相手なしの行は outcome が None で、note に区間の説明を持つ。"""

    row: MultiRow
    outcome: CompareOutcome | None
    note: str | None = None
    # 1つのログの中で組にしたときの、1回だけのコマンドの区間の行(比べていない。全体の文の分母に数えない。ADR-0152 決定2)
    single: bool = False
    # 相手なしの行のコマンドと「変更前 区間 3」(「全部」のタブの列に使う。ADR-0164 決定4)。ほかの行は None
    command: str | None = None
    place: str | None = None
    # エラーの出力の区間の行(自動の組から外した。比べていない。全体の文の分母に数えない。「全部」のタブに出さない。ADR-0195 決定1)
    error: bool = False


@dataclass(frozen=True)
class MultiView:
    """まとめての比較の表示。first は最初に選ぶ行(最初の差分ありの組、無ければ1行目)。

    llm は全部の組をまとめて1回で送った解析(ADR-0152 決定3。比べた結果では MultiCompareOutcome.llm、開いた .jsonl では
    in_whole の行があるときの最初の答え)。無ければ None(開いた一括比較の .jsonl・古い保存は組ごとの AI の欄を出す)。
    """

    entries: tuple[MultiEntry, ...]
    status: MultiStatus
    overall: str
    first: int
    llm: LlmAnalysis | None = None


def diff_count(outcome: CompareOutcome) -> str:
    counts = read_counts(outcome)
    if show_method(outcome) == "both" and counts.show is not None and counts.raw is not None:
        return str(counts.show + counts.raw)  # 表と単純比較の差分を合わせた数(REQ-282 §3)
    for value in (counts.structured, counts.show, counts.raw):
        if value is not None:
            return str(value)
    return NO_COUNT


def outcome_entry(name: str, outcome: CompareOutcome, manual: bool = False) -> MultiEntry:
    return MultiEntry(
        MultiRow(name, STATUS_LABEL[outcome.status], diff_count(outcome), False, manual, show_method(outcome)), outcome
    )


def unpaired_entry(unpaired: Unpaired) -> MultiEntry:
    segment = unpaired.segment
    command = segment.command if segment.command is not None else UNKNOWN_COMMAND
    row = MultiRow(unpaired_row_name(unpaired.side, segment.number, command), unpaired_status(unpaired.side), NO_COUNT, True)
    return MultiEntry(
        row,
        None,
        unpaired_segment_note(unpaired.side, segment.number, command, segment.line_count),
        command=command,
        place=unpaired_place(unpaired.side, segment.number),
    )


def single_entry(unpaired: Unpaired) -> MultiEntry:
    """1つのログの中で組にしたときの、1回だけのコマンド(とコマンド不明)の区間の行(ADR-0152 決定2)。"""
    segment = unpaired.segment
    command = segment.command if segment.command is not None else UNKNOWN_COMMAND
    row = MultiRow(single_row_name(segment.number, command), MULTI_SINGLE, NO_COUNT, True)
    return MultiEntry(row, None, MULTI_SINGLE_NOTE, single=True)


def error_entry(unpaired: Unpaired, single_log: bool) -> MultiEntry:
    """エラーの出力の区間の行(ADR-0195 決定1)。名前は相手なしの行(1つのログの中で組にしたときは「1回だけ」の行)と同じ決まり。"""
    segment = unpaired.segment
    command = segment.command if segment.command is not None else UNKNOWN_COMMAND
    name = (
        single_row_name(segment.number, command)
        if single_log
        else unpaired_row_name(unpaired.side, segment.number, command)
    )
    row = MultiRow(name, MULTI_ERROR_OUTPUT, NO_COUNT, True)
    return MultiEntry(row, None, error_output_note(command, segment.line_count), error=True)


def first_row(entries: Sequence[MultiEntry]) -> int:
    return next((k for k, e in enumerate(entries) if e.outcome is not None and e.outcome.status == "difference"), 0)


def overall_text(status: MultiStatus, entries: Sequence[MultiEntry]) -> str:
    """全体の文。分母は一覧の行の数(組と相手なし)。全体の状態は core の overall_status の値をそのまま受け取る。

    1回だけのコマンドの行(1つのログの中で組にしたとき)は分母にも数にも入れない(全体の状態に数えないため。ADR-0152 決定2)。
    """
    counted = [e for e in entries if not e.single and not e.error]
    if status == "difference":
        count = sum(1 for e in counted if e.outcome is None or e.outcome.status == "difference")
    else:
        count = sum(1 for e in counted if e.outcome is not None and e.outcome.status in ("error", "needs_selection"))
    return multi_overall(status, len(counted), count)


def _multi(entries: list[MultiEntry], status: MultiStatus, llm: LlmAnalysis | None = None) -> MultiView:
    return MultiView(tuple(entries), status, overall_text(status, entries), first_row(entries), llm)


def multi_view(outcome: MultiCompareOutcome) -> MultiView:
    """比べた結果の一覧(組を組の順に、続けて相手なし)。

    1つのログの中で組にしたときは、組の名前に区間の番号を添え(「show vlan（区間 2 → 7）」)、残りの区間は「1 回だけ」の行にする。
    全体の AI の解析(outcome.llm)は MultiView.llm に入れる。
    """
    entries = []
    for p in outcome.pairs:
        name = p.command if p.command is not None else UNKNOWN_COMMAND
        if outcome.single_log:
            name = single_pair_name(name, p.before_number, p.after_number)
        entries.append(outcome_entry(name, p.outcome, p.manual))
    for u, is_error in multi_rest(outcome):
        if is_error:
            entries.append(error_entry(u, outcome.single_log))
        else:
            entries.append(single_entry(u) if outcome.single_log else unpaired_entry(u))
    return _multi(entries, outcome.status, outcome.llm)


def multi_rest(outcome: MultiCompareOutcome) -> list[tuple[Unpaired, bool]]:
    """一覧の組でない行の並び(組の後ろ)。(区間, エラーの出力か)。

    組にならなかった区間(相手なし・1回だけ)とエラーの出力の区間を、(変更前→変更後, 区間の番号)の順に並べる(ADR-0195 決定1)。
    一覧の作り方(multi_view)と、行の番号から区間を引く処理(row_target)が同じ順を使う。
    """
    rest = [(u, False) for u in outcome.unpaired] + [(u, True) for u in outcome.error_outputs]
    rest.sort(key=lambda item: (item[0].side != "before", item[0].segment.number))
    return rest


def row_target(outcome: MultiCompareOutcome, row: int) -> tuple[Any, ...] | None:
    """一覧の行の番号から対象を返す(REQ-269)。

    組の行なら ("pair", 組の位置)。組でない行なら ("segment", 側, 区間の番号, エラーの出力か)。範囲の外は None。
    """
    if 0 <= row < len(outcome.pairs):
        return ("pair", row)
    rest = multi_rest(outcome)
    position = row - len(outcome.pairs)
    if not 0 <= position < len(rest):
        return None
    unpaired, is_error = rest[position]
    return ("segment", unpaired.side, unpaired.segment.number, is_error)


@dataclass(frozen=True)
class PartnerChoice:
    """相手を選ぶ窓の候補1つ。label は区間の表示(「3 : show running-config」)、state は今の状態の文。"""

    number: int
    label: str
    state: str


@dataclass(frozen=True)
class PartnerList:
    """相手を選ぶ窓の中身(題・案内の文・候補の並び。候補は相手の側の区間のすべて)。"""

    title: str
    prompt: str
    choices: tuple[PartnerChoice, ...]


def partner_choices(request: CompareRequest, outcome: MultiCompareOutcome, row: int) -> PartnerList | None:
    """一覧の row 行が相手なしかエラーの出力のとき、もう片方の側の区間のすべてを候補にした窓の中身を返す。ほかの行は None。"""
    target = row_target(outcome, row)
    if target is None or target[0] != "segment":
        return None
    _kind, side, number, _is_error = target
    other: Side = "after" if side == "before" else "before"
    rest = {(u.side, u.segment.number): u for u, _e in multi_rest(outcome)}
    unpaired = rest[(side, number)]
    command = unpaired.segment.command if unpaired.segment.command is not None else UNKNOWN_COMMAND
    name = unpaired_row_name(side, number, command)
    choices = []
    for candidate in partner_candidates(request, outcome, other):
        segment = candidate.segment
        if candidate.state == "unpaired":
            state = unpaired_status(other)
        elif candidate.state == "error":
            state = MULTI_ERROR_OUTPUT
        else:
            assert candidate.leftover is not None
            left = candidate.leftover.segment
            left_command = left.command if left.command is not None else UNKNOWN_COMMAND
            place = unpaired_place(candidate.leftover.side, left.number)
            text = partner_state_paired_error if candidate.leftover_error else partner_state_paired
            state = text(place, left_command)
        choices.append(PartnerChoice(segment.number, segment_label(segment), state))
    return PartnerList(COMPARE_PARTNER_TITLE, compare_partner_prompt(other, name), tuple(choices))


def carry_send_checks(
    old: MultiCompareOutcome,
    old_checked: Collection[int],
    new: MultiCompareOutcome,
    new_key: tuple[int, int] | None,
) -> frozenset[int]:
    """組を選び直した後の「AI に送る」のチェックの位置(REQ-265・REQ-269 §3)。

    組は (変更前の番号, 変更後の番号) で見分ける。新しい結果の送れる組のうち、前にチェックしていた組と同じ組はチェックしたままにし、
    新しく作った組(new_key)は送れる組ならチェックする。外した組は新しい結果に無いので自然に消える。
    """
    before_checked = {
        (old.pairs[k].before_number, old.pairs[k].after_number) for k in old_checked if 0 <= k < len(old.pairs)
    }
    keep = before_checked | ({new_key} if new_key is not None else set())
    return frozenset(
        k for k in whole_candidates(new) if (new.pairs[k].before_number, new.pairs[k].after_number) in keep
    )


def pair_by_hand_screen(
    request: CompareRequest,
    *,
    outcome: MultiCompareOutcome,
    before_number: int,
    after_number: int,
    llm: LlmRequest | None = None,
    on_progress: ProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MultiCompareOutcome:
    """手で選んだ2つの区間を組にして、その1組だけを比べ直す(比較の窓の「相手を選ぶ…」。CompareWorker の runner。REQ-269)。

    llm は CompareWorker が渡す値で、使わない(AI は呼ばない)。進み具合は core の工程の名前をそのまま流す。
    """
    return pair_by_hand(
        request, outcome, before_number, after_number, on_progress=on_progress, should_cancel=should_cancel
    )


def unpair_screen(request: CompareRequest, outcome: MultiCompareOutcome, position: int) -> MultiCompareOutcome:
    """組を外して、2つの区間を戻す(比べない。REQ-269)。"""
    return unpair_pair(request, outcome, position)


def exclude_screen(outcome: MultiCompareOutcome, row: int) -> MultiCompareOutcome:
    """一覧の行の組・区間を比べる対象から外す(比べない。REQ-367 §4)。行の番号が範囲の外・最後の1行なら ValueError。"""
    target = row_target(outcome, row)
    if target is None:
        raise ValueError("no such row")
    if target[0] == "pair":
        return exclude_pair(outcome, target[1])
    return exclude_segment(outcome, target[1], target[2])


def exclude_rows(outcome: MultiCompareOutcome) -> list[bool]:
    """一覧の行ごとに、比べる対象から外せるか(外すと一覧に組も相手なしも残らなくなる最後の1行は外せない。exclude_screen と同じ決まり)。

    数える行は組と、1つのログの中で組にしたときを除く相手なし(core の _check_not_last と同じ)。それを 1 回だけ数えて、行数に比例する時間で決める。
    """
    pairs = len(outcome.pairs)
    counted = 0 if outcome.single_log else len(outcome.unpaired)
    result = [pairs - 1 > 0 or counted > 0 for _ in range(pairs)]
    for _unpaired, is_error in multi_rest(outcome):
        left = counted - (0 if is_error or outcome.single_log else 1)
        result.append(pairs > 0 or left > 0)
    return result


def can_exclude_row(outcome: MultiCompareOutcome, row: int) -> bool:
    """一覧の行が、比べる対象から外せるか(exclude_rows の 1 行分)。範囲の外は偽。"""
    rows = exclude_rows(outcome)
    return 0 <= row < len(rows) and rows[row]


def excluded_names(outcome: MultiCompareOutcome) -> list[str]:
    """外した組と区間の名前(外した順)。名前は一覧の行と同じ決まり。"""
    names: list[str] = []
    for item in outcome.excluded:
        if isinstance(item, Unpaired):
            if is_error_output(item.segment):
                entry = error_entry(item, outcome.single_log)
            else:
                entry = single_entry(item) if outcome.single_log else unpaired_entry(item)
            names.append(entry.row.name)
        else:
            name = item.command if item.command is not None else UNKNOWN_COMMAND
            names.append(single_pair_name(name, item.before_number, item.after_number) if outcome.single_log else name)
    return names


def restore_screen(
    request: CompareRequest,
    *,
    outcome: MultiCompareOutcome,
    k: int,
    llm: LlmRequest | None = None,
    on_progress: ProgressSink | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MultiCompareOutcome:
    """外した組・区間の k 番目を戻す(組はその1組だけ比べ直す。CompareWorker の runner。REQ-367)。llm は使わない。"""
    return restore_excluded(request, outcome, k, on_progress=on_progress, should_cancel=should_cancel)


def structured_columns(outcome: CompareOutcome | None) -> list[list[str]]:
    """構造化比較の行ごとに、show の表の変更の行なら変わった列の名前の並び、ほかの行は空(_structured_rows と同じ数・同じ並び。REQ-367)。"""
    if outcome is None:
        return []
    if outcome.show is not None:
        return [list(d["fields"]) if d["op"] == "changed" else [] for d in outcome.show.diffs]
    if outcome.structured is None:
        return []
    return [[] for _ in outcome.structured.diffs]


def _value_search_text(value: Any) -> str:
    """元の行を探す文字。文字はそのまま、整数は数字の文字。真偽値・辞書・リストなどは文字が無いとみなして空。"""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def _source_probe(outcome: CompareOutcome, index: int) -> tuple[str, str] | None:
    """構造化比較の index 番目の行から、元の行を探す文字と、探す側("before"・"after")を決める。決められなければ None。"""
    if outcome.show is not None:
        diffs = outcome.show.diffs
        if not 0 <= index < len(diffs):
            return None
        d = diffs[index]
        if d["op"] == "changed":
            for column, (before, after) in d["fields"].items():
                for side, value in (("after", after), ("before", before)):
                    text = _value_search_text(value)
                    if text:
                        return text, side
            return None
        side = "before" if d["op"] == "removed" else "after"
        row = d["before"] if d["op"] == "removed" else d["after"]
        key_field = outcome.show.key_field
        if isinstance(row, dict) and key_field:
            text = _value_search_text(row.get(key_field.split("+")[0]))
            if text:
                return text, side
        text = _value_search_text(d.get("key"))
        return (text, side) if text else None
    if outcome.structured is None or not 0 <= index < len(outcome.structured.diffs):
        return None
    d = outcome.structured.diffs[index]
    for side in ("after", "before"):
        text = _value_search_text(d.get(side))
        if text:
            return text, side
    # 値が文字でない行(辞書・真偽値など)は、対象の名前の文字で探す。値があった側で探す
    name = _value_search_text(d.get("name"))
    if not name:
        return None
    return name, "after" if d.get("after") is not None else "before"


def source_raw_row(outcome: CompareOutcome | None, rows: Sequence[RawRow], index: int) -> int | None:
    """構造化比較の index 番目の行の元の行を、単純比較の行(rows。align_raw の行)から探して、その番号を返す(REQ-367 §2)。

    1. 差分の辞書に before_line_no・after_line_no(整数)があれば、その行番号の行。
    2. 無ければ行の値の文字で探す。変更後の値なら変更後の側、変更前の値なら変更前の側の文字に含まれる最初の行。
       大文字・小文字は区別する。空の文字は探さない。差分の行を先に探し、無ければ全部の行から探す。
    見つからなければ None。今の構造化比較の差分には行番号が無いので、いつも 2. になる。
    """
    if outcome is None:
        return None
    probe = _source_probe(outcome, index)
    diffs = outcome.show.diffs if outcome.show is not None else (outcome.structured.diffs if outcome.structured else [])
    if 0 <= index < len(diffs):
        d = diffs[index]
        for side, key in (("after", "after_line_no"), ("before", "before_line_no")):
            number = d.get(key) if isinstance(d, dict) else None
            if isinstance(number, int) and not isinstance(number, bool):
                for position, row in enumerate(rows):
                    if (row.after_no if side == "after" else row.before_no) == number:
                        return position
    if probe is None:
        return None
    text, side = probe

    def has(row: RawRow) -> bool:
        return text in (row.after_text if side == "after" else row.before_text)

    for wanted in (True, False):
        for position, row in enumerate(rows):
            if (row.is_diff or not wanted) and has(row):
                return position
    return None


def whole_answer_lines(llm: LlmAnalysis | None) -> tuple[list[str] | None, str | None]:
    """全体の AI の解析を AI の面に出す行とコピーの文(view_outcome の llm_screen_lines・llm_copy と同じ決まり。ADR-0152 決定3)。

    解析を頼んでいない(None・disabled)なら (None, None)。
    """
    if llm is None or llm.reason == "disabled":
        return None, None
    answered = llm.status == "done" and isinstance(llm.answer, str)
    return llm_screen_lines(llm), (answer_copy_text(llm.answer) if answered else None)  # type: ignore[arg-type]


def pair_records(
    outcome: MultiCompareOutcome,
    inp: ScreenInput,
    *,
    saved_at: datetime | None = None,
    conversation: Conversation | None = None,
    holder: int | None = None,
) -> list[dict[str, Any]]:
    """組ごとの保存の行(JSON Lines の1行が1組)。相手なしは入れない。どの行も同じ saved_at にする。

    全体を AI に送ったときの答えは whole_pair_results のとおり(最初の組の行に全体の答え、ほかは in_whole。ADR-0152 決定7)。
    conversation(比較の窓での AI とのやり取り。ADR-0179 決定2)は全体の答えを入れた行(whole_holder)にだけ入れる。
    holder は1組に聞いた答えを入れた組の位置(REQ-312 §4.3。そのときは全体の答えが無い)。渡したときはその行にやり取りを入れる。
    """
    if saved_at is None:
        saved_at = datetime.now(timezone.utc).astimezone()
    paths = source_paths(inp)
    if holder is None:
        holder = whole_holder(outcome) if conversation is not None else None
    elif conversation is None:
        holder = None
    pairs = whole_pair_results(outcome)
    if holder is not None:
        pairs = [
            replace(p, outcome=replace(p.outcome, conversation=conversation)) if k == holder else p
            for k, p in enumerate(pairs)
        ]
    return [pair_record(p, source_paths=paths, saved_at=saved_at) for p in pairs]


# --- 「全部」のタブと、行に合うタブ(ADR-0152 決定7・決定8) ---

# 単純比較の差分の行を「全部」のタブに出すときのカテゴリ
ALL_RAW_CATEGORY = SM_ALL_RAW_CATEGORY


@dataclass(frozen=True)
class AllRow:
    """「全部」のタブの1行(コマンド・操作・カテゴリ・対象・変更前・変更後)。"""

    command: str
    op: str
    category: str
    target: str
    before: str
    after: str
    # 言語によらない操作の鍵(構造化比較の行はその鍵、単純比較の「~」の行は "changed"、ほかは "")。印の判定はこの鍵で行う(ADR-0190)
    op_key: str = ""


def _target_item(target: str, item: str) -> str:
    """対象の列。項目があれば「対象 / 項目」(ADR-0152 決定7)。"""
    if not item:
        return target
    return f"{target} / {item}" if target else item


def all_rows(entries: Sequence[MultiEntry]) -> list[AllRow]:
    """「全部」のタブの行(ADR-0152 決定7)。組の順に、組の中は今の表の並びのまま。

    構造化(show の構造化を含む)の差分があればそれ、無ければ単純比較の差分の行(操作は印から、カテゴリは「(行)」)。
    比較が失敗した組・選択待ちの組は1行(操作の欄に状態の文)。1回だけの行は出さない。
    相手なしの行は1行で、コマンドの列はコマンドだけ、操作の列は「変更後なし」「変更前なし」、対象の列は「変更前 区間 3」
    (ADR-0164 決定4)。ほかの行のコマンドの列は一覧の行の名前。
    """
    rows: list[AllRow] = []
    for entry in entries:
        if entry.single or entry.error:
            continue
        name = entry.row.name
        outcome = entry.outcome
        if outcome is None:
            if entry.command is not None and entry.place is not None:
                rows.append(AllRow(entry.command, entry.row.status, "", entry.place, "", ""))
            else:  # 作られない想定(開いた .jsonl には相手なしの行が無い)。落ちないように一覧の名前を使う
                rows.append(AllRow(name, entry.row.status, "", "", "", ""))
            continue
        if outcome.status in _STATUS_ONLY:
            rows.append(AllRow(name, STATUS_LABEL[outcome.status], "", "", "", ""))
            continue
        view = view_outcome(outcome)
        if view.structured_rows:
            rows += [
                AllRow(name, r.op, r.category, _target_item(r.target, r.item), r.before, r.after, r.op_key)
                for r in view.structured_rows
            ]
            if show_method(outcome) != "both":
                continue
        # 表と単純比較の両方の組は、表の差分のあとに単純比較の差分を続ける(REQ-282 §2)
        rows += [
            AllRow(name, raw_mark_label(r), ALL_RAW_CATEGORY, "", r.before_text, r.after_text, "changed" if r.mark == "~" else "")
            for r in view.raw_rows
            if r.is_diff
        ]
    return rows


def all_marks(rows: Sequence[AllRow]) -> list[dict[str, Any]]:
    """「全部」のタブの印。操作が「変更」の行(op_key が changed。文では見分けない)の変更前と変更後。"""
    return _marks_of((k, r.before, r.after) for k, r in enumerate(rows) if r.op_key == "changed")


def pair_tab(outcome: CompareOutcome | None) -> str | None:
    """一覧で行を選んだときに開くタブ(ADR-0152 決定8)。構造化比較(show の構造化を含む)が動いた組は structured、
    動かず単純比較がある組は raw。相手なし・1回だけの行(outcome が None)と、どちらも無い組は None(今のタブのまま)。
    """
    if outcome is None:
        return None
    if outcome.structured is not None or (outcome.show is not None and outcome.show.method in ("structured", "both")):
        return "structured"
    if outcome.raw is not None:
        return "raw"
    return None


def suggested_multi_filename(records: Sequence[Mapping[str, Any]]) -> str:
    """まとめての比較の保存の既定のファイル名(`<日時>_compare.jsonl`)。"""
    return multi_filename(records[0])


def save_multi(records: Sequence[dict[str, Any]], target: str | Path) -> Path:
    """書いたパスを返す。上書きしない(既にあれば batch_io の ResultsWriteError)。"""
    return write_records(records, target)


def read_saved_lines(path: str | Path) -> list[StoredRecord]:
    """JSON Lines の保存を1行ずつ from_record で読む(REQ-120 の決まりのまま)。大きさを先に見て、上限を超えれば読まない。

    版の違う行が1行でもあれば版の文で断る(ほかの行の形の違いより先に言う)。JSON として読めない行は決まった文、
    形の違いも決まった文にする。行の無いファイルや、空の行が途中にあるファイルは形の違い(最後の改行は許す)。
    """
    return read_saved_lines_raw(path)[1]


def read_saved_lines_raw(path: str | Path) -> tuple[list[dict[str, Any]], list[StoredRecord]]:
    """read_saved_lines と同じ決まりで読み、読み込んだ行の dict も返す(開いた結果を保存し直すため。REQ-242 §4)。"""
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise SavedResultError(OPEN_RESULT_UNREADABLE) from exc
    if size > MAX_RESULT_BYTES:
        raise SavedResultError(open_result_too_large(MAX_RESULT_MB))
    try:
        text = p.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SavedResultError(OPEN_RESULT_NOT_JSON) from exc
    except OSError as exc:
        raise SavedResultError(OPEN_RESULT_UNREADABLE) from exc
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    if not lines or any(not line.strip() for line in lines):
        raise SavedResultError(OPEN_RESULT_FORMAT)
    data = []
    for line in lines:
        try:
            data.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise SavedResultError(OPEN_RESULT_NOT_JSON) from exc
        except (ValueError, RecursionError) as exc:
            raise SavedResultError(OPEN_RESULT_FORMAT) from exc
    records: list[StoredRecord] = []
    other_version = broken = False
    for item in data:
        try:
            records.append(from_record(item))
        except UnsupportedSchemaVersion:
            other_version = True
        except (StorageFormatError, ValueError, RecursionError):
            broken = True
    if other_version:
        raise SavedResultError(OPEN_RESULT_VERSION)
    if broken:
        raise SavedResultError(OPEN_RESULT_FORMAT)
    return data, records


def record_name(record: StoredRecord) -> str:
    """開いた行の一覧の名前。job.label、無ければ job.id、無ければ変更前の区間のコマンド。"""
    job = record.meta.get(JOB_KEY)
    if job is not None:
        return job["label"] or job["id"]
    command = record.meta["inputs"]["before"]["command"]
    return command if command is not None else UNKNOWN_COMMAND


def opened_multi(records: Sequence[StoredRecord]) -> tuple[MultiView, str]:
    """開いた .jsonl の一覧と帯の文。全体の状態は保存した組の行だけで決める(相手なしは保存に無い)。

    帯は1行目の保存日時と入力の名前に、相手なしの区間は保存に入っていないことを足す(一括比較で保存した .jsonl も同じ。
    相手なしがあったかどうかはファイルから分からないので、いつも出す)。
    """
    # 手で組にした行(job.paired_by_hand が true。REQ-269 §4)には印を付ける。無い保存・一括比較の保存は印なし
    entries = [
        outcome_entry(record_name(r), r.outcome, (r.meta.get(JOB_KEY) or {}).get(PAIRED_BY_HAND_KEY) is True)
        for r in records
    ]
    inputs = records[0].meta["inputs"]
    band = opened_band(records[0].meta["saved_at"], inputs["before"]["name"], inputs["after"]["name"])
    return _multi(entries, multi_overall_status(records, []), opened_whole(records)), f"{band}\n{MULTI_UNPAIRED_NOT_IN_FILE}"


def opened_whole(records: Sequence[StoredRecord]) -> LlmAnalysis | None:
    """開いた .jsonl の全体の答え(ADR-0152 決定7)。in_whole の行があるときだけ、最初に答えのある行(in_whole でない AI の解析)。

    in_whole の行が無い(一括比較の .jsonl・古い保存)ときは None(今までどおり組ごとの AI の欄を出す)。
    """
    index = opened_whole_index(records)
    return None if index is None else records[index].outcome.llm


def opened_whole_index(records: Sequence[StoredRecord]) -> int | None:
    """開いた .jsonl の全体の答えを入れた行の位置(opened_whole の行。REQ-242 §4)。無ければ None。

    in_whole の行があるときだけ、最初に in_whole でも disabled でもない AI の解析を持つ行。
    """
    analyses = [r.outcome.llm for r in records]
    if not any(llm is not None and llm.reason == "in_whole" for llm in analyses):
        return None
    return next(
        (k for k, llm in enumerate(analyses) if llm is not None and llm.reason not in ("in_whole", "disabled")), None
    )


def opened_answer_index(records: Sequence[StoredRecord]) -> int | None:
    """開いた .jsonl で、続けて質問できる答え(と保存したやり取り)を持つ行の位置(REQ-242 §4)。無ければ None。

    全体の答えを入れた行(opened_whole_index)。対応する組が1つだけのまとめての比較の保存は、相手なしの区間を保存しないので
    1行だけになり、in_whole の行が無い。そのときはその1行を全体の答えの行とする(答えが送って届いたものかは窓が確かめる)。
    表示(opened_whole・MultiView.llm)は今のまま変えない。
    """
    index = opened_whole_index(records)
    if index is None and len(records) == 1:
        return 0
    if index is None and len(records) > 1:
        # 1組に聞いた答え(REQ-312 §4.3)。in_whole の行が無く、送って届いた答えと保存したやり取りを持つ行がちょうど1つならその行。
        # 一括比較の .jsonl にはやり取りが無いので、ここでは見つからない(今までどおり None)
        held = [
            k
            for k, r in enumerate(records)
            if r.outcome.conversation is not None
            and r.outcome.llm is not None
            and r.outcome.llm.status == "done"
            and r.outcome.llm.transmitted
        ]
        if len(held) == 1:
            return held[0]
    return index


def resaved_lines(
    records: Sequence[Mapping[str, Any]], whole: int | None, conversation: Conversation | None, saved_at: datetime
) -> list[dict[str, Any]]:
    """開いた .jsonl を保存し直す行(REQ-242 §4)。どの行も同じ saved_at にし、conversation は全体の答えの行(whole)にだけ入れる。

    ほかの行の conversation は開いたときのまま(saved_at だけを替える)。元の dict は変えない。
    """
    lines = []
    for k, record in enumerate(records):
        if k == whole:
            lines.append(resaved_record(record, conversation, saved_at))
        else:
            # 全体の答えの行でない行は saved_at だけを替える(ほかは1文字も変えない)
            copied = copy.deepcopy(dict(record))
            copied["saved_at"] = saved_at.isoformat(timespec="seconds")
            lines.append(copied)
    return lines


# --- 人に渡す形の出力(HTML と CSV。ADR-0156 / REQ-216。pywebview の窓だけ) ---
# 結果から Report を作る。差分の行・組の名前・ファイル名は llm_analyzer/redact.py の判定に通す(AI に送るときと同じ判定の道)。
# 既定は伏せる(ADR-0156 決定2)。出力のたびに利用者がチェックを外したときだけ、判定に通さず画面と同じ値で出す(ADR-0257。masked=False)。
# 保存した結果を開いているときは出さない(決定7。窓の側で止める)。

EXPORT_HTML, EXPORT_DIFFS, EXPORT_PAIRS = "html", "csv", "csv_pairs"
EXPORT_KINDS = (EXPORT_HTML, EXPORT_DIFFS, EXPORT_PAIRS)
EXPORT_CANCEL = "cancel"
# 既定のファイルの名前の後ろ(ホスト名は入れない)と、保存のダイアログで選べるファイルの種類
_EXPORT_SUFFIX = {EXPORT_HTML: ".html", EXPORT_DIFFS: ".csv", EXPORT_PAIRS: "-pairs.csv"}
EXPORT_FILE_TYPES = {EXPORT_HTML: ("HTML (*.html)",), EXPORT_DIFFS: ("CSV (*.csv)",), EXPORT_PAIRS: ("CSV (*.csv)",)}
# 単純比較の印の操作の名前(画面の表の OP_LABEL と同じ言葉)
_RAW_OP_LABEL = {
    "~": OP_LABEL["changed"],
    "-": OP_LABEL["removed"],
    "+": OP_LABEL["added"],
    "x": RAW_MARK_IGNORED,
    "=": RAW_MARK_EQUATED,
}
# 単純比較の表の印の文(画面の表の印の列。ADR-0166。記号のままの印は画面が記号を出す)
RAW_MARK_LABEL = {"x": RAW_MARK_IGNORED, "=": RAW_MARK_EQUATED}


def raw_mark_label(row: RawRow) -> str:
    """表の行の操作の言葉。移動元・移動先は「移動元 1」「移動先 1」(REQ-228 §8.2)。ほかは _RAW_OP_LABEL の言葉(無ければ印のまま)。"""
    if row.mark in ("<", ">") and row.move is not None:
        return raw_move_mark(row.mark, row.move)
    return _RAW_OP_LABEL.get(row.mark, row.mark)


def raw_move_labels(rows: Sequence[RawRow]) -> list[dict[str, Any]]:
    """比較の窓の表の、移動の行の印の文([{index, text}]。index は raw_rows の番号)。"""
    return [{"index": k, "text": raw_mark_label(row)} for k, row in enumerate(rows) if row.move is not None]
# 状態だけを出す組(比較が失敗した組と選択待ちの組)
_STATUS_ONLY = ("error", "needs_selection")


def export_choices(multiple: bool) -> list[dict[str, str]]:
    """形を選ぶダイアログの選択肢(ADR-0156 決定1)。組の一覧は組が複数ある形のときだけ。最後は「やめる」。"""
    choices = [(EXPORT_HTML, EXPORT_CHOICE_HTML), (EXPORT_DIFFS, EXPORT_CHOICE_DIFFS)]
    if multiple:
        choices.append((EXPORT_PAIRS, EXPORT_CHOICE_PAIRS))
    choices.append((EXPORT_CANCEL, EXPORT_CHOICE_CANCEL))
    return [{"value": value, "label": label} for value, label in choices]


def export_words(settings_words: Sequence[str], analyses: Iterable[LlmAnalysis | None]) -> list[str]:
    """出力の判定に使う秘密の語(ADR-0156 決定2)。今の設定の語と、送った AI 解析の語を全部の組で合わせる。

    組ごとに判定器を作るので番号は組ごとに 1 から。ほかの組の語でも伏せるのは締める向き。
    """
    words = list(settings_words)
    for llm in analyses:
        if llm is not None and llm.secret_words:
            words += list(llm.secret_words)
    return words


def export_filename(prefix: str, kind: str, now: datetime, *, masked: bool = True) -> str:
    """保存のダイアログの既定の名前。`driftpilot-<prefix>-<YYYYMMDD-HHMMSS>.html` など(ホスト名は入れない)。

    伏せずに出すとき(masked が偽)は、拡張子の前に `-unmasked` を付ける(ADR-0257 決定3)。
    """
    stem, dot, ext = _EXPORT_SUFFIX[kind].rpartition(".")
    return f"driftpilot-{prefix}-{now:%Y%m%d-%H%M%S}{stem}{'' if masked else '-unmasked'}{dot}{ext}"


def file_label(name: str, words: Sequence[str], *, masked: bool = True) -> str:
    """要約に出す入力の名前。ファイル名だけにして判定に通す(ADR-0156 決定3)。貼り付けは今の名前のまま。

    伏せずに出すとき(masked が偽)は、ファイル名だけを判定に通さず出す(フルパスは出さない。ADR-0257 決定2)。
    """
    if not masked:
        return Path(name).name
    return redact_text(Path(name).name, words)


def pair_columns(outcome: CompareOutcome) -> tuple[int | None, int | None, str, str]:
    """組の一覧の列(構造化比較の差分・単純比較の差分・構造化不可の行・AI)。一括比較の結果の一覧(result_row)と同じ値。"""
    counts = read_counts(outcome)
    structured = counts.structured if counts.structured is not None else counts.show
    unparsed = NO_COUNT if counts.unparsed is None else sm_unparsed_pair(counts.unparsed["before"], counts.unparsed["after"])
    return structured, counts.raw, unparsed, llm_column(outcome.llm)


def _labelled(redacted: RedactedResult, raw_rows: Sequence[RawRow] = ()) -> RedactedResult:
    """単純比較の行の印を画面の表と同じ言葉(追加・削除・変更・移動元…)にする。単純比較の文脈の行の印は空のまま。
    構造化比較の行の操作は言語によらない鍵のまま(文は出力の側で引く)。

    移動元・移動先の行(REQ-228)は「移動元 1」「移動先 1」。番号は raw_rows(伏せる前の表の行)から、移動元は変更前の行番号、
    移動先は変更後の行番号で引く(伏せた行は移動の番号を持たないため)。
    """
    moves = {(r.mark, r.before_no if r.mark == "<" else r.after_no): r.move for r in raw_rows if r.move is not None}

    def label(row: Any) -> str:
        number = moves.get((row.mark, row.before_no if row.mark == "<" else row.after_no))
        if row.mark in ("<", ">") and number is not None:
            return raw_move_mark(row.mark, number)
        return _RAW_OP_LABEL.get(row.mark, row.mark)

    # 構造化比較の行の op は文にせず鍵のまま渡す(report_output が鍵から文と色を引く。ADR-0190)
    rows = [replace(r, mark=label(r)) for r in redacted.raw_rows]
    return RedactedResult(list(redacted.diffs), rows, redacted.omitted_lines)


def pair_report(
    name: str,
    outcome: CompareOutcome,
    words: Sequence[str],
    *,
    turns: Sequence[tuple[str, str]] = (),
    before_name: str = "",
    after_name: str = "",
    first_request: str | None = None,
    platform: str | None = None,
    masked: bool = True,
) -> ReportPair:
    """1 組の出力。比較が失敗した組と選択待ちの組は状態だけ(差分の行・件数の文・答えを出さない)。

    状態は STATUS_LABEL の名前だけで、エラーの文・帯の文・警告・診断は入れない(ADR-0156 決定3)。
    名前は判定に通す(空はそのまま)。区間の行が取れないのに差分があれば RedactError(出力は失敗にする)。
    first_request は最初の依頼の文(ADR-0179 決定1)。答えがあるときだけ持つ(追加の質問の文と同じく、送る前に確かめた文)。
    masked が偽(ADR-0257)なら、名前・差分の行・単純比較の設定の式を判定に通さず画面と同じ値で出す(RedactError は起きない)。
    """
    shown_name = (redact_text(name, words) if masked else name) if name else ""
    status = STATUS_LABEL[outcome.status]
    structured, raw, unparsed, ai = pair_columns(outcome)
    if outcome.status in _STATUS_ONLY:
        return ReportPair(
            shown_name,
            status,
            "",
            structured,
            raw,
            unparsed,
            ai,
            None,
            before_name=before_name,
            after_name=after_name,
            status_key=outcome.status,
        )
    seg = outcome.segmentation
    bodies = {
        side: (seg[side].selected.body if seg is not None and seg[side].selected is not None else None) for side in SIDES
    }
    table = _raw_rows(outcome)
    if masked:
        redacted = redact_result(
            structured=outcome.structured,
            raw=outcome.raw,
            show=outcome.show,
            before_body=bodies["before"],
            after_body=bodies["after"],
            raw_rows=table,
            secret_words=words,
            platform=platform,
        )
    else:
        redacted = plain_result(structured=outcome.structured, raw=outcome.raw, show=outcome.show, raw_rows=table)
    llm = outcome.llm
    answer = llm.answer if llm is not None and llm.status == "done" and isinstance(llm.answer, str) else None
    return ReportPair(
        shown_name,
        status,
        counts_text(read_counts(outcome)),
        structured,
        raw,
        unparsed,
        ai,
        _labelled(redacted, table),
        answer,
        tuple(turns) if answer is not None else (),
        before_name,
        after_name,
        _raw_report_lines(outcome),
        first_request=first_request if answer is not None else None,
        status_key=outcome.status,
        raw_rules=_raw_report_rules(outcome, words, masked=masked),
        raw_compared=outcome.raw is not None or (outcome.show is not None and outcome.show.raw is not None),
    )


def _raw_report_rules(outcome: CompareOutcome, words: Sequence[str], *, masked: bool = True) -> ReportRawRules | None:
    """報告書に出す、その組を比べた単純比較の設定(REQ-363)。既定の設定(記録が無い)なら None。

    式と置き換える文字は、追加の質問と同じ判定(premise_text_problem。語は初期の語を足したもの)に通し、断られた欄は EXPORT_RAW_WITHHELD にする
    (種類・当たった行の数・ほかの欄は出す)。置き換える文字が空か空白だけのときは、判定に通さずそのまま出す(空の文は断られるため)。
    並びは無視する行 → 置き換え(記録の options の順)。当たった行の数は rule_hits の同じ位置(無い・足りなければ None)。
    """
    record = outcome_raw_options(outcome)
    if record is None:
        return None
    options = dict(record.options)

    def shown(text: str) -> str:
        # 伏せずに出すときは式と置き換える文字をそのまま出す(判定に通さない。ADR-0257 決定2)
        if not masked or not text.strip():
            return text
        # premise_text_problem は check_question に normalize_words(words) を渡す(長さの上限は質問の 4000 字。式は 200 字までに確かめ済み)
        return EXPORT_RAW_WITHHELD if premise_text_problem(text, words) is not None else text

    def hit(key: str, index: int) -> tuple[int, int] | None:
        found = record.rule_hits.get(key) if record.rule_hits is not None else None
        if found is None or index >= len(found):
            return None
        return found[index]["before"], found[index]["after"]  # type: ignore[index]

    rules = [
        ReportRawRule(RAW_RULE_KIND_TEXT["ignore"], shown(pattern), "", hit("ignore_lines", index))
        for index, pattern in enumerate(options.get("ignore_lines", []))
    ]
    rules += [
        ReportRawRule(
            RAW_RULE_KIND_TEXT["replace"],
            shown(item["pattern"]),
            shown(item["replacement"]),
            hit("replacements", index),
            is_replace=True,
        )
        for index, item in enumerate(options.get("replacements", []))
    ]
    # ルール以外の設定(空白・空行・大文字と小文字・移動・比べ方)。algorithm_used は渡さない(標準に戻った知らせは raw_lines が出す)
    rest = raw_options_parts({**options, "ignore_lines": [], "replacements": []})
    summary = raw_options_summary(options)
    if summary is None and not rules:
        return None
    return ReportRawRules(
        summary or "",
        tuple(rules),
        EXPORT_RAW_RULES_OTHERS.format(parts=SM_SET_ASIDE_SEPARATOR.join(rest)) if rest else None,
        hashlib.sha256(json.dumps(options, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest(),
    )


def _raw_report_lines(outcome: CompareOutcome) -> tuple[str, ...]:
    """HTML の組の見出しの下の、単純比較の設定の要約と数の文(ADR-0166。既定の設定なら空)。"""
    record = outcome_raw_options(outcome)
    if record is None:
        return ()
    lines = (raw_record_summary(record), raw_set_aside_text(record))
    return tuple(line for line in lines if line is not None)


def compare_report(
    inp: ScreenInput,
    outcome: CompareOutcome,
    words: Sequence[str],
    turns: Sequence[tuple[str, str]] = (),
    *,
    first_request: str | None = None,
    masked: bool = True,
) -> Report:
    """比較の窓の 1 組の出力。turns は追加の質問のやり取り (質問, 回答)、first_request は最初の依頼の文(ADR-0179 決定1)。

    masked が偽なら秘密の行を伏せずに出す(ADR-0257。既定は伏せる)。
    """
    pair = pair_report(
        "", outcome, words, turns=turns, first_request=first_request,
        # inp は ScreenInput(platform を持つ)。機種を持たない簡単な入力でも動くよう、無ければ機種なしとして扱う
        platform=config_platform_used(getattr(inp, "platform", None), outcome.kind, outcome.auto_platform),
        masked=masked,
    )
    return Report(
        EXPORT_TITLE_COMPARE,
        pair.status,
        pair.counts,
        (pair,),
        False,
        file_label(inp.before.name, words, masked=masked),
        file_label(inp.after.name, words, masked=masked),
        status_key=pair.status_key,
        masked=masked,
    )


def multi_report(
    inp: ScreenInput,
    outcome: MultiCompareOutcome,
    words: Sequence[str],
    turns: Mapping[int, Sequence[tuple[str, str]]] | None = None,
    *,
    first_requests: Mapping[int, str | None] | None = None,
    masked: bool = True,
) -> Report:
    """まとめて比べた結果の出力。組の名前は一覧の行の名前(コマンド名)。相手なしの区間は入れない(保存と同じ)。

    turns は組の番号(一覧の行の番号。組は一覧の先頭から組の順に並ぶ)ごとの追加の質問のやり取り。
    first_requests は同じ組の番号ごとの最初の依頼の文(ADR-0179 決定1。turns と同じ形)。
    全体を AI に送ったときは保存と同じく、最初の組に全体の答え、ほかの組は in_whole(whole_pair_results。ADR-0152 決定10)。
    """
    turns = turns or {}
    first_requests = first_requests or {}
    view = multi_view(outcome)
    pairs = tuple(
        pair_report(
            view.entries[k].row.name,
            pair.outcome,
            words,
            turns=turns.get(k, ()),
            first_request=first_requests.get(k),
            platform=config_platform_used(pair.request.platform, pair.outcome.kind, pair.outcome.auto_platform),
            masked=masked,
        )
        for k, pair in enumerate(whole_pair_results(outcome))
    )
    return Report(
        EXPORT_TITLE_COMPARE,
        view.overall,
        "",
        pairs,
        True,
        file_label(inp.before.name, words, masked=masked),
        file_label(inp.after.name, words, masked=masked),
        status_key=view.status,
        masked=masked,
    )


def export_bytes(report: Report, kind: str, saved_at: datetime) -> bytes:
    """書き出すバイト列。HTML は UTF-8、CSV は BOM 付きの UTF-8(report_output)。"""
    if kind == EXPORT_HTML:
        return render_html(report, saved_at=saved_at).encode("utf-8")
    if kind == EXPORT_DIFFS:
        return render_diffs_csv(report)
    if kind == EXPORT_PAIRS:
        return render_pairs_csv(report)
    raise ValueError(SMODEL_TEXTS["export_kind"].format(kind=kind))


# --- 複数の機器が入ったログを、機器ごとのファイルに分けて一括比較で開く(ADR-0194 / REQ-267) ---


@dataclass(frozen=True)
class DeviceNotice:
    """比較の窓の、複数の機器の知らせ。比べたときの入力の文だけから作る(ファイルを読み直さない)。

    before・after は、各側の機器の名前(ログに最初に出た順)。同じ文のときは same。
    """

    before: tuple[str, ...]
    after: tuple[str, ...]
    text: str
    same: bool

    @property
    def missing(self) -> bool:
        """機器が 1 つも見つからない側があるか。"""
        return not self.before or not self.after


def device_notice(inp: ScreenInput) -> DeviceNotice | None:
    """どちらかの側が複数の機器のログなら知らせ。どちらも 1 台以下なら None(何も出さない)。"""
    before = find_devices(inp.before.text)
    after = find_devices(inp.after.text)
    if len(before) < 2 and len(after) < 2:
        return None
    return DeviceNotice(before, after, device_notice_text(before, after), inp.before.text == inp.after.text)


def device_split_reason(notice: DeviceNotice, busy: bool) -> str | None:
    """ボタンを押せない理由の文。押せるなら None。理由は、機器が見つからない・同じ文・動いている間の順に 1 つ返す。"""
    if notice.missing:
        return DEVICE_SPLIT_NO_DEVICE
    if notice.same:
        return DEVICE_SPLIT_SAME_LOG
    if busy:
        return DEVICE_SPLIT_BUSY
    return None


def write_device_files(before_text: str, after_text: str, parent: Path, now: datetime | None = None) -> DeviceSplitWritten:
    """比べたときの 2 つの文を機器ごとに分けて、parent の下の新しいフォルダーに書く。書けなければ DeviceSplitError。"""
    return write_device_split(before_text, after_text, parent, now)
