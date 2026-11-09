"""比較画面の論理(REQ-18 決定9)。wx を読まない。

入力から CompareRequest を組み立て、CompareOutcome を表示用のデータ(OutcomeView)に写す。
差分の判定・件数・状態は core の結果から読むだけで、ここで数え直さない(ADR-0022 決定2)。
警告・注意の文面は driftpilot.messages(CLI と共通)から取る。ここに置くのはボタン名・タブ名・案内などの画面の言葉だけ。
"""

import copy
import json
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import driftpilot.inline_marks as inline_marks
from driftpilot.batch_io import multi_filename, pair_record, write_records
from driftpilot.core import (
    AUTO_PLATFORM,
    CONFIG_PLATFORMS,
    DEFAULT_MAX_TOTAL_DIFFS,
    DEFAULT_RAW_OPTIONS,
    RAW_CONTEXT_DEFAULT,
    RAW_CONTEXT_MAX,
    CompareCancelled,
    CompareOutcome,
    CompareRequest,
    Conversation,
    MultiCompareOutcome,
    MultiStatus,
    PairResult,
    RawOptions,
    RawOptionsRecord,
    Replacement,
    SideInput,
    SideSelection,
    Unpaired,
    analyze_all,
    check_raw_options,
    compare_all,
    multi_overall_status,
    options_to_plain,
    run_compare,
    wants_all,
)
# 全体の答えを組に配る部品は core に置いた(一括比較の保存からも使う。REQ-240 §4)。今までどおりここからも読める
from driftpilot.core import IN_WHOLE, whole_holder, whole_pair_results  # noqa: F401
# ApprovedPromptClient / PromptNotApproved は一括比較と共有するため llm_analyzer に移した(ここからも読める。REQ-27)
from driftpilot.llm_analyzer import (
    ApprovedPromptClient,
    LLMClient,
    LlmAnalysis,
    LlmRequest,
    PromptNotApproved,  # noqa: F401 - 移す前と同じく screen_model.model からも読める
)
# 答えの読み取りは画面の側からはここ(と batch_model)を通して使う(gui の境界。REQ-204)
from driftpilot.llm_analyzer.answer_format import format_answer, plain_answer
# 人に渡す出力の伏せる判定(ADR-0156 決定2。AI に送るときと同じ判定の道)
from driftpilot.llm_analyzer.redact import RedactedResult, RedactError, redact_result, redact_text  # noqa: F401 - RedactError は窓が読む
from driftpilot.llm_settings import LlmSettings, SettingsError, current_premise
from driftpilot.llm_settings import build_request as build_llm_request
from driftpilot.messages import (
    AUTO_PLATFORM_LABEL,
    COMPARE_KIND_TROUBLE,
    COMPARE_PLATFORM_TROUBLE,
    COMPARE_TAB_OFF_MODE_RAW,
    COMPARE_TAB_OFF_SHOW,
    EXPORT_CHOICE_CANCEL,
    EXPORT_CHOICE_DIFFS,
    EXPORT_CHOICE_HTML,
    EXPORT_CHOICE_PAIRS,
    EXPORT_TITLE_COMPARE,
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
    STATUS_LABEL,
    STATUS_ORDER_ONLY,
    auto_platform_text,
    compare_warning_line,
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
    platform_label,
    raw_defaults_unreadable,
    raw_move_mark,
    raw_moves_part,
    raw_option_problem_lines,
    raw_options_summary_text,
    raw_set_aside_line,
    route_detail_note,
    saved_notice,
    single_pair_name,
    single_row_name,
    status_breakdown,
    unpaired_place,
    unpaired_row_name,
    unpaired_segment_note,
    unpaired_status,
    unreadable,
    unreadable_utf8,
    warning_message,
)
from driftpilot.raw_settings import load_raw_defaults, load_raw_settings, raw_settings_path
from driftpilot.report_output import Report, ReportPair, render_diffs_csv, render_html, render_pairs_csv
from driftpilot.segmentation.input_forms import InputDecodeError, InputNote, normalize_text, read_config_input
from driftpilot.storage import (
    JOB_KEY,
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

Side = Literal["before", "after"]
Mode = Literal["structured", "raw", "both"]
KindChoice = Literal["config", "show", "auto"]

SIDES: tuple[Side, ...] = ("before", "after")
SIDE_LABEL = {"before": "変更前", "after": "変更後"}
# 画面の選択肢(値, 表示)。順番は画面に並ぶ順
MODE_CHOICES: tuple[tuple[Mode, str], ...] = (("both", "両方"), ("structured", "構造化"), ("raw", "単純"))
KIND_CHOICES: tuple[tuple[KindChoice, str], ...] = (("config", "config"), ("show", "show"), ("auto", "自動"))
# 種類が config のときの機種(パーサーの系統)。先頭が既定(指定なし=A型)、残りは CLI の --platform と同じ一覧(REQ-25 パート C)
CONFIG_PLATFORM_CHOICES: tuple[tuple[str, str], ...] = (("", "Cisco 系・Arista(指定なし)"),) + tuple(
    (name, name) for name in CONFIG_PLATFORMS
)
# 機種の自動(ADR-0151 決定4)。pywebview の比較の窓・一括比較の窓の機種の選択肢。先頭が自動、残りは CONFIG_PLATFORM_CHOICES
WEB_PLATFORM_CHOICES: tuple[tuple[str, str], ...] = ((AUTO_PLATFORM, AUTO_PLATFORM_LABEL),) + CONFIG_PLATFORM_CHOICES
PASTED_NAME = {"before": "貼り付け(変更前)", "after": "貼り付け(変更後)"}
# 進捗の工程名の表示(core の STAGES と同じ並び)
STAGE_LABEL = {
    "split": "区間を切り分けています",
    "select": "区間を選んでいます",
    "detect_kind": "入力の種類を判定しています",
    "compare": "比較しています",
    "safety_checks": "安全網を確認しています",
    "llm": "AI 解析をしています",
    "done": "表示しています",
}
OP_LABEL = {"added": OP_ADDED, "removed": OP_REMOVED, "changed": OP_CHANGED}
AUTO_SEGMENT = "自動"
PICK_SEGMENT = "(選んでください)"
UNKNOWN_COMMAND = "(コマンド不明)"
WHOLE_ROW = "(行全体)"
NO_INPUT = "未選択"
CANCELLED = "中止しました"
STALE_RESULT = "入力か設定を変えました。表示中の結果は前の入力のものです。保存するには、もう一度比較してください"


def stage_label(stage: str) -> str:
    """工程名の表示。core に工程が増えても落ちない(未知の工程名はそのまま出す)。"""
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
    )


def source_paths(inp: ScreenInput) -> dict[str, str | None]:
    """保存レコードに書く入力のパス(REQ-14)。貼り付けの側は None。"""
    paths = {side: inp.side(side).path for side in SIDES}
    return {side: (str(path) if path is not None else None) for side, path in paths.items()}


def input_summary(source: SideSource | None, segment_count: int | None = None) -> str:
    """入力欄の横の「行数と区間数」。区間数は比較の後にだけ分かる(core の切り分けの結果)。"""
    if source is None:
        return NO_INPUT
    text = f"{source.line_count:,} 行"
    if segment_count is not None:
        text += f" / {segment_count} 区間"
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
        show=len(show.diffs) if show is not None and show.method == "structured" else None,
        raw=len(outcome.raw.diffs) if outcome.raw is not None else None,
        unparsed=dict(outcome.structured.unparsed_counts) if outcome.structured is not None else None,
        warnings=len(outcome.warnings),
        notices=len(_shown_notices(outcome)),
    )


def counts_text(counts: Counts) -> str:
    parts: list[str] = []
    if counts.structured is not None:
        parts.append(f"構造化 {counts.structured}")
    if counts.show is not None:
        parts.append(f"show 構造化 {counts.show}")
    if counts.raw is not None:
        parts.append(f"単純 {counts.raw}")
    if counts.unparsed is not None:
        parts.append(f"構造化不可の行 前 {counts.unparsed['before']} / 後 {counts.unparsed['after']}")
    if counts.warnings:
        parts.append(f"警告 {counts.warnings}")
    if counts.notices:
        parts.append(f"注意 {counts.notices}")
    return "  ".join(parts)


def tab_titles(counts: Counts) -> dict[str, str]:
    """タブの見出し。件数は内訳と同じ値。"""
    structured = counts.structured if counts.structured is not None else counts.show
    unparsed = counts.unparsed
    return {
        "structured": "構造化比較" + (f" ({structured})" if structured is not None else ""),
        "raw": "単純比較" + (f" ({counts.raw})" if counts.raw is not None else ""),
        "unparsed": "構造化不可の行" + (f" (前 {unparsed['before']} / 後 {unparsed['after']})" if unparsed is not None else ""),
    }


@dataclass(frozen=True)
class StructuredRow:
    op: str
    category: str
    target: str
    item: str
    before: str
    after: str


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
        return f"{rest!r}{route_detail_note(value['vendor_specific'])}"
    return _value(value)


def _structured_rows(outcome: CompareOutcome) -> list[StructuredRow]:
    if outcome.show is not None:
        rows = []
        for d in outcome.show.diffs:
            target = d["key"] if d["key"] is not None else WHOLE_ROW
            if d["op"] == "changed":
                fields = d["fields"]
                before = ", ".join(f"{c}: {b!r}" for c, (b, _a) in fields.items())
                after = ", ".join(f"{c}: {a!r}" for c, (_b, a) in fields.items())
                rows.append(StructuredRow(OP_LABEL["changed"], "show", target, ", ".join(fields), before, after))
            else:
                rows.append(StructuredRow(OP_LABEL.get(d["op"], d["op"]), "show", target, "", _value(d["before"]), _value(d["after"])))
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
        )
        for d in outcome.structured.diffs
    ]


def _structured_note(outcome: CompareOutcome) -> str | None:
    if outcome.kind is None:
        return None
    if outcome.mode == "raw":
        return COMPARE_TAB_OFF_MODE_RAW
    if outcome.show is not None and outcome.show.method == "raw":
        return f"show のテンプレートで読めないため、単純比較にしました: {outcome.show.fallback_reason}"
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
        return COMPARE_TAB_OFF_SHOW
    return None


def _raw_note(outcome: CompareOutcome) -> str | None:
    if outcome.kind is None:
        return None
    if outcome.raw is None:
        return "比較方式が「構造化」のため、単純比較をしていません"
    if not outcome.raw.diffs:
        return STATUS_LABEL["no_difference"]
    return None


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
    """構造化比較の表の印。操作が「変更」の行の変更前と変更後。"""
    return _marks_of((k, r.before, r.after) for k, r in enumerate(rows) if r.op == OP_LABEL["changed"])


def category_counts(rows: Sequence[StructuredRow]) -> list[tuple[str, int]]:
    """カテゴリと件数。件数の多い順、同じ件数なら名前の順(ADR-0155 決定1)。名前は表の「カテゴリ」の列と同じ文字。"""
    counts = Counter(row.category for row in rows)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))


def _raw_rows(outcome: CompareOutcome) -> list[RawRow]:
    seg = outcome.segmentation
    if outcome.raw is None or seg is None or seg["before"].selected is None or seg["after"].selected is None:
        return []
    return align_raw(seg["before"].selected.body, seg["after"].selected.body, outcome.raw.diffs, outcome.raw.options)


def _unparsed(outcome: CompareOutcome) -> tuple[list[UnparsedRow], str | None]:
    if outcome.kind is None:
        return [], None
    if outcome.structured is None:
        return [], "構造化比較をしていないため、対象外です"
    rows = []
    for d in outcome.structured.diffs:
        if d["category"] != "unparsed":
            continue
        line_path = d["before"] if d["op"] == "removed" else d["after"]
        rows.append(UnparsedRow(OP_LABEL.get(d["op"], d["op"]), line_path[-1], " > ".join(line_path[:-1])))
    note = "一覧は、変更前と変更後で違う構造化不可の行だけです。両側で同じ構造化不可の行は、見出しの件数にだけ含まれます"
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
            prompt="入力の種類を判定できません。「機種」と「show のコマンド」を入力するか、種類を config か show にして、もう一度比較してください",
            detail=outcome.selection_detail.get("message"),
            focus_format_fields=True,
        )
    waiting = [side for side in SIDES if any(c.side == side for c in outcome.candidates)]
    return SelectionView(
        reason=outcome.selection_reason or "segment",
        waiting=waiting,
        candidates={side: _choices_from_candidates(outcome, side) for side in waiting},
        prompt=f"{'・'.join(SIDE_LABEL[s] for s in waiting)}に区間が複数あります。比較する区間を選んで、もう一度比較してください",
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
        label, structured = "構造化", counts.structured
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
    return OutcomeView(
        status=outcome.status,
        status_text=_status_text(outcome, selection),
        status_text_web=_status_text_web(outcome, selection, counts),
        counts=counts,
        counts_text=counts_text(counts),
        titles=tab_titles(counts),
        structured_rows=_structured_rows(outcome),
        structured_note=_structured_note(outcome),
        raw_rows=_raw_rows(outcome),
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
    )


# 比較の設定の要約(REQ-206 §2)で、config の機種が「指定なし」のときの短い名前
DEFAULT_PLATFORM_SHORT = "Cisco 系"
SUMMARY_SEPARATOR = "・"


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
) -> str:
    """pywebview の比較の画面の設定の要約(_web_settings_summary)。raw_options が既定でなければ、単純比較の設定の要約を
    「・」でつないで後ろに足す(ADR-0166。既定・None なら今と同じ文)。
    """
    text = _web_settings_summary(mode, kind, index, typed, command, outcome)
    summary = raw_options_summary(raw_options) if raw_options is not None else None
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
        head = f"{AUTO_PLATFORM_LABEL}（config{SUMMARY_SEPARATOR}{platform_label(used)}）"
    elif decided == "show":
        head = f"{AUTO_PLATFORM_LABEL}（show）"
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
)


@dataclass(frozen=True)
class RawForm:
    """画面から来た単純比較の設定の欄の値を読んだもの。options は誤りがあれば None、problems は画面に出す文。

    context_lines は「差分のみ表示」の前後の行数(表示だけの値で options には入れない。REQ-228 §2)。読めなければ既定の 3。
    compare_options は、前後の行数の誤りを除いて読めた設定(ほかの欄に誤りがあれば None。省けば options と同じ)。
    前後の行数は表示だけの値なので、比較の窓はこれで自動の比較を始めるかを決める(前後の行数だけを直しても比べ直さない)。
    """

    options: RawOptions | None
    problems: list[str]
    context_lines: int = RAW_CONTEXT_DEFAULT
    compare_options: RawOptions | None = None

    def __post_init__(self) -> None:
        if self.compare_options is None and self.options is not None:
            object.__setattr__(self, "compare_options", self.options)


def read_context_lines(value: object) -> int | None:
    """前後の行数の欄の値。整数で 0〜20 ならその値、ほか(null・真偽値・小数・範囲の外)は None。"""
    if type(value) is not int or not 0 <= value <= RAW_CONTEXT_MAX:
        return None
    return value


def read_raw_form(value: object) -> RawForm:
    """画面の欄の値を読む。形が違えば ValueError(入口は _BadRequest)。中身の誤りは problems に入れ、options を None にする。

    形: {"ignore_lines": 改行でつないだ式, "replacements": [[式, 置き換える文字], ...], "whitespace": 文字列,
    "ignore_blank_lines": 真偽値, "ignore_case": 真偽値, "context_lines": 数, "detect_moves": 真偽値, "algorithm": 文字列}。
    無視する行は改行で分け、行の両端の空白を除いて空になった行は捨てる。
    置き換えは式が空の組を捨てる(置き換える文字は空でよい)。誤りの文の「何行目」「何組目」は、画面の欄の行と組の位置。
    前後の行数が整数で 0〜20 でなければ(null も)、problems に RAW_CONTEXT_PROBLEM を入れる(REQ-228 §2)。
    """
    if not isinstance(value, dict) or set(value) != set(_RAW_FORM_KEYS):
        raise ValueError("単純比較の設定の形が違います")
    ignore = value["ignore_lines"]
    if not isinstance(ignore, str) or len(ignore) > MAX_RAW_FORM_IGNORE_CHARS:
        raise ValueError("無視する行の形が違います")
    items = value["replacements"]
    if (
        not isinstance(items, list)
        or len(items) > MAX_RAW_REPLACEMENT_ROWS
        or not all(isinstance(item, list) and len(item) == 2 and all(isinstance(x, str) for x in item) for item in items)
    ):
        raise ValueError("置き換えの形が違います")
    if not isinstance(value["whitespace"], str):
        raise ValueError("空白の違いの形が違います")
    if type(value["ignore_blank_lines"]) is not bool or type(value["ignore_case"]) is not bool:
        raise ValueError("空行・大文字と小文字の形が違います")
    if type(value["detect_moves"]) is not bool:
        raise ValueError("行の移動の形が違います")
    if not isinstance(value["algorithm"], str):
        raise ValueError("比べ方の形が違います")
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
    context_lines = RAW_CONTEXT_DEFAULT if context is None else context
    context_problems = [RAW_CONTEXT_PROBLEM] if context is None else []
    if not problems and not context_problems:
        return RawForm(options, [], context_lines)
    if not problems:
        # 前後の行数だけの誤り。比べること・保存は止める(options は None)が、比べる設定は読めている
        return RawForm(None, context_problems, context_lines, options)
    rows = {"ignore_lines": ignore_rows, "replacements": replace_rows}
    shown = [
        replace(p, index=rows[p.field][p.index - 1]) if p.index is not None and p.field in rows else p for p in problems
    ]
    return RawForm(None, [*raw_option_problem_lines(shown), *context_problems], context_lines)



def raw_form_values(options: RawOptions, context_lines: int = RAW_FOLD_CONTEXT) -> dict[str, Any]:
    """設定を画面の欄の値(read_raw_form と同じ形)にする。context_lines は前後の行数(表示だけの値)。"""
    return {
        "ignore_lines": "\n".join(options.ignore_lines),
        "replacements": [[r.pattern, r.replacement] for r in options.replacements],
        "whitespace": options.whitespace,
        "ignore_blank_lines": options.ignore_blank_lines,
        "ignore_case": options.ignore_case,
        "context_lines": context_lines,
        "detect_moves": options.detect_moves,
        "algorithm": options.algorithm,
    }


def raw_examples() -> dict[str, list[dict[str, str]]]:
    """例から足す選択肢(文と式。RAW_IGNORE_EXAMPLES・RAW_REPLACE_EXAMPLES)。画面は文字を textContent と value で入れる。"""
    return {
        "ignore": [{"label": label, "pattern": pattern} for label, pattern in RAW_IGNORE_EXAMPLES],
        "replace": [
            {"label": label, "pattern": pattern, "replacement": text} for label, pattern, text in RAW_REPLACE_EXAMPLES
        ],
    }


def raw_options_summary(options: RawOptions | Mapping[str, Any] | None) -> str | None:
    """「単純比較の設定: 無視する行 2 件・置き換え 1 組・空白の数を無視・空行を無視・大文字と小文字を無視」。既定なら None。

    options は設定か、保存の形(options_to_plain)。
    """
    if options is None:
        return None
    plain = options_to_plain(options) if isinstance(options, RawOptions) else dict(options)
    return raw_options_summary_text(plain)


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
    return "・".join(shown) if shown else None


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
) -> tuple[RawOptions, int, str | None]:
    """既定の設定と前後の行数(raw-compare.json。REQ-228 §2)。読めなければ (すべてオフ, 3, 帯の文)。"""
    try:
        loaded = load_raw_settings(raw_settings_path(platform=platform, env=env, home=home))
    except SettingsError as exc:
        return DEFAULT_RAW_OPTIONS, RAW_FOLD_CONTEXT, raw_defaults_unreadable(str(exc))
    return loaded.options, loaded.context_lines, None


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
        summary=f"送る差分 {llm.sent_diffs} 件(差分は全部で {llm.diff_total} 件)",
        truncated=llm_truncated_lines(llm) if llm.truncated else [],
        unsent=llm_unsent_line(llm),
        unsent_shapes=llm_unsent_shape_lines(llm),
    )


def approve(plan: LlmPlan, preview: LlmAnalysis) -> LlmRequest:
    """プレビューで「送信する」を押したときの要求。承認した内容(preview.payload)以外は送らない。"""
    if plan.send is None or plan.send.client is None or preview.payload is None:
        raise ValueError("送信できる計画とプレビューがありません")
    return replace(plan.send, client=ApprovedPromptClient(plan.send.client, preview.payload))


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
    on_progress: Callable[[str], None] | None = None,
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
    on_progress: Callable[[str], None] | None = None,
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
    on_progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> MultiCompareOutcome:
    """まとめて比べ、llm があれば全部の組をまとめて1回で解析する(core.analyze_all。ADR-0152 決定3)。

    llm は送らない1回目(dry_run)か、承認した要求(approve で包んだ client)か、自動送信の要求。
    1組の run_compare と同じく、工程の境目(解析の前と後)で中止の印を見る(解析の前なら送らない。答えの途中で中止したときは
    CompareCancelled になり、窓は前の結果と途中の答えを残す)。
    """
    outcome = compare_all(request, should_cancel=should_cancel, on_progress=on_progress)
    if llm is None:
        return outcome
    _enter("llm", on_progress, should_cancel)
    analyzed = analyze_all(outcome, llm, max_total_diffs=max_total_diffs)
    _enter("done", on_progress, should_cancel)
    return analyzed


def _enter(stage: str, on_progress: Callable[[str], None] | None, should_cancel: Callable[[], bool] | None) -> None:
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
        segments = sorted((u.segment for u in outcome.unpaired if u.side == side), key=lambda s: s.number)
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
    for value in (counts.structured, counts.show, counts.raw):
        if value is not None:
            return str(value)
    return NO_COUNT


def outcome_entry(name: str, outcome: CompareOutcome) -> MultiEntry:
    return MultiEntry(MultiRow(name, STATUS_LABEL[outcome.status], diff_count(outcome), False), outcome)


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


def first_row(entries: Sequence[MultiEntry]) -> int:
    return next((k for k, e in enumerate(entries) if e.outcome is not None and e.outcome.status == "difference"), 0)


def overall_text(status: MultiStatus, entries: Sequence[MultiEntry]) -> str:
    """全体の文。分母は一覧の行の数(組と相手なし)。全体の状態は core の overall_status の値をそのまま受け取る。

    1回だけのコマンドの行(1つのログの中で組にしたとき)は分母にも数にも入れない(全体の状態に数えないため。ADR-0152 決定2)。
    """
    counted = [e for e in entries if not e.single]
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
        entries.append(outcome_entry(name, p.outcome))
    entries += [single_entry(u) if outcome.single_log else unpaired_entry(u) for u in outcome.unpaired]
    return _multi(entries, outcome.status, outcome.llm)


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
) -> list[dict[str, Any]]:
    """組ごとの保存の行(JSON Lines の1行が1組)。相手なしは入れない。どの行も同じ saved_at にする。

    全体を AI に送ったときの答えは whole_pair_results のとおり(最初の組の行に全体の答え、ほかは in_whole。ADR-0152 決定7)。
    conversation(比較の窓での AI とのやり取り。ADR-0179 決定2)は全体の答えを入れた行(whole_holder)にだけ入れる。
    """
    if saved_at is None:
        saved_at = datetime.now(timezone.utc).astimezone()
    paths = source_paths(inp)
    holder = whole_holder(outcome) if conversation is not None else None
    pairs = whole_pair_results(outcome)
    if holder is not None:
        pairs = [
            replace(p, outcome=replace(p.outcome, conversation=conversation)) if k == holder else p
            for k, p in enumerate(pairs)
        ]
    return [pair_record(p, source_paths=paths, saved_at=saved_at) for p in pairs]


# --- 「全部」のタブと、行に合うタブ(ADR-0152 決定7・決定8) ---

# 単純比較の差分の行を「全部」のタブに出すときのカテゴリ
ALL_RAW_CATEGORY = "(行)"


@dataclass(frozen=True)
class AllRow:
    """「全部」のタブの1行(コマンド・操作・カテゴリ・対象・変更前・変更後)。"""

    command: str
    op: str
    category: str
    target: str
    before: str
    after: str


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
        if entry.single:
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
                AllRow(name, r.op, r.category, _target_item(r.target, r.item), r.before, r.after)
                for r in view.structured_rows
            ]
            continue
        rows += [
            AllRow(name, raw_mark_label(r), ALL_RAW_CATEGORY, "", r.before_text, r.after_text)
            for r in view.raw_rows
            if r.is_diff
        ]
    return rows


def all_marks(rows: Sequence[AllRow]) -> list[dict[str, Any]]:
    """「全部」のタブの印。操作が「変更」の行の変更前と変更後。"""
    return _marks_of((k, r.before, r.after) for k, r in enumerate(rows) if r.op == OP_LABEL["changed"])


def pair_tab(outcome: CompareOutcome | None) -> str | None:
    """一覧で行を選んだときに開くタブ(ADR-0152 決定8)。構造化比較(show の構造化を含む)が動いた組は structured、
    動かず単純比較がある組は raw。相手なし・1回だけの行(outcome が None)と、どちらも無い組は None(今のタブのまま)。
    """
    if outcome is None:
        return None
    if outcome.structured is not None or (outcome.show is not None and outcome.show.method == "structured"):
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
    entries = [outcome_entry(record_name(r), r.outcome) for r in records]
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
# 伏せずに出す口は作らない(ADR-0156 決定2)。保存した結果を開いているときは出さない(決定7。窓の側で止める)。

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


def export_filename(prefix: str, kind: str, now: datetime) -> str:
    """保存のダイアログの既定の名前。`driftpilot-<prefix>-<YYYYMMDD-HHMMSS>.html` など(ホスト名は入れない)。"""
    return f"driftpilot-{prefix}-{now:%Y%m%d-%H%M%S}{_EXPORT_SUFFIX[kind]}"


def file_label(name: str, words: Sequence[str]) -> str:
    """要約に出す入力の名前。ファイル名だけにして判定に通す(ADR-0156 決定3)。貼り付けは今の名前のまま。"""
    return redact_text(Path(name).name, words)


def pair_columns(outcome: CompareOutcome) -> tuple[int | None, int | None, str, str]:
    """組の一覧の列(構造化比較の差分・単純比較の差分・構造化不可の行・AI)。一括比較の結果の一覧(result_row)と同じ値。"""
    counts = read_counts(outcome)
    structured = counts.structured if counts.structured is not None else counts.show
    unparsed = NO_COUNT if counts.unparsed is None else f"前 {counts.unparsed['before']} / 後 {counts.unparsed['after']}"
    return structured, counts.raw, unparsed, llm_column(outcome.llm)


def _labelled(redacted: RedactedResult, raw_rows: Sequence[RawRow] = ()) -> RedactedResult:
    """操作の欄を画面の表と同じ言葉(追加・削除・変更)にする。単純比較の文脈の行の印は空のまま。

    移動元・移動先の行(REQ-228)は「移動元 1」「移動先 1」。番号は raw_rows(伏せる前の表の行)から、移動元は変更前の行番号、
    移動先は変更後の行番号で引く(伏せた行は移動の番号を持たないため)。
    """
    moves = {(r.mark, r.before_no if r.mark == "<" else r.after_no): r.move for r in raw_rows if r.move is not None}

    def label(row: Any) -> str:
        number = moves.get((row.mark, row.before_no if row.mark == "<" else row.after_no))
        if row.mark in ("<", ">") and number is not None:
            return raw_move_mark(row.mark, number)
        return _RAW_OP_LABEL.get(row.mark, row.mark)

    diffs = [replace(d, op=OP_LABEL.get(d.op, d.op)) for d in redacted.diffs]
    rows = [replace(r, mark=label(r)) for r in redacted.raw_rows]
    return RedactedResult(diffs, rows, redacted.omitted_lines)


def pair_report(
    name: str,
    outcome: CompareOutcome,
    words: Sequence[str],
    *,
    turns: Sequence[tuple[str, str]] = (),
    before_name: str = "",
    after_name: str = "",
    first_request: str | None = None,
) -> ReportPair:
    """1 組の出力。比較が失敗した組と選択待ちの組は状態だけ(差分の行・件数の文・答えを出さない)。

    状態は STATUS_LABEL の名前だけで、エラーの文・帯の文・警告・診断は入れない(ADR-0156 決定3)。
    名前は判定に通す(空はそのまま)。区間の行が取れないのに差分があれば RedactError(出力は失敗にする)。
    first_request は最初の依頼の文(ADR-0179 決定1)。答えがあるときだけ持つ(追加の質問の文と同じく、送る前に確かめた文)。
    """
    shown_name = redact_text(name, words) if name else ""
    status = STATUS_LABEL[outcome.status]
    structured, raw, unparsed, ai = pair_columns(outcome)
    if outcome.status in _STATUS_ONLY:
        return ReportPair(shown_name, status, "", structured, raw, unparsed, ai, None, before_name=before_name, after_name=after_name)
    seg = outcome.segmentation
    bodies = {
        side: (seg[side].selected.body if seg is not None and seg[side].selected is not None else None) for side in SIDES
    }
    table = _raw_rows(outcome)
    redacted = redact_result(
        structured=outcome.structured,
        raw=outcome.raw,
        show=outcome.show,
        before_body=bodies["before"],
        after_body=bodies["after"],
        raw_rows=table,
        secret_words=words,
    )
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
) -> Report:
    """比較の窓の 1 組の出力。turns は追加の質問のやり取り (質問, 回答)、first_request は最初の依頼の文(ADR-0179 決定1)。"""
    pair = pair_report("", outcome, words, turns=turns, first_request=first_request)
    return Report(
        EXPORT_TITLE_COMPARE,
        pair.status,
        pair.counts,
        (pair,),
        False,
        file_label(inp.before.name, words),
        file_label(inp.after.name, words),
    )


def multi_report(
    inp: ScreenInput,
    outcome: MultiCompareOutcome,
    words: Sequence[str],
    turns: Mapping[int, Sequence[tuple[str, str]]] | None = None,
    *,
    first_requests: Mapping[int, str | None] | None = None,
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
        )
        for k, pair in enumerate(whole_pair_results(outcome))
    )
    return Report(
        EXPORT_TITLE_COMPARE,
        view.overall,
        "",
        pairs,
        True,
        file_label(inp.before.name, words),
        file_label(inp.after.name, words),
    )


def export_bytes(report: Report, kind: str, saved_at: datetime) -> bytes:
    """書き出すバイト列。HTML は UTF-8、CSV は BOM 付きの UTF-8(report_output)。"""
    if kind == EXPORT_HTML:
        return render_html(report, saved_at=saved_at).encode("utf-8")
    if kind == EXPORT_DIFFS:
        return render_diffs_csv(report)
    if kind == EXPORT_PAIRS:
        return render_pairs_csv(report)
    raise ValueError(f"出力の形が違います: {kind!r}")
