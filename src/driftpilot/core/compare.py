"""比較の実行(ADR-0015 決定1・3・6)。切り分け、区間の選択、種類の解決、比較、安全網4、LLM 解析(ADR-0018)。

区間が選べないときは例外を投げず、status=needs_selection と候補を返す。
LLM 解析は比較の後の注釈で、差分・status・has_diff を変えない。失敗しても比較は完走する(ADR-0018 決定4・決定5)。

正規化ルールは、要求に無ければ同梱の機種のルール(builtin_rules)を使う。config の機種のルールがあるときだけ、
選んだ機種のルールを structured の前の本文にも行数を変えずに当て、外した値の件数を exclusions に返す(ADR-0025 決定5・6)。

安全網5(REQ-25): config で structured を試みたとき(--mode structured / both)、区間を選んだ後の本文から系統を見立て
(family_hint)、指定した系統と食い違う側ごとに platform_mismatch の警告を足す。structured の結果があればその warnings にも入る。
structured が失敗したとき(REQ-97。FortiGate を機種なしで読み、重複で落ちるなど)は、結果の warnings だけに入れる。
見立ては比較の経路に入れず、status・has_diff・差分・error を変えない。

ページ送りの印(ADR-0134 決定2): 区間を選べた後、切り分けで印を外した側ごとに pager_marks の警告を足す。
比べ方・種類によらず、platform_mismatch と同じ経路で structured の warnings と結果の warnings に入れる。

入力の形(ADR-0138 決定7。REQ-185): 要求の側の notes(読み込みで文字コードなどを戻した知らせ)ごとに input_form の警告を足す。
pager_marks と同じ経路で、読み込みは切り分けより前の工程なので先頭に置く。区間を選べなかったときは出ない。

機種の自動(ADR-0151。REQ-209): 要求の platform が auto のとき、区間を選んだ後の本文から片側ごとに機種を判定し
(detect_platform)、choose_platform の組み合わせで使う系統を決める。以降は同じ系統を手で指定したのと同じ道を通る。
種類も auto なら、判定が fujitsu_sir・fortinet の側は config、ほかは sniff で決める(AmbiguousFormatError は出さない)。
show に決まったら機種なし(単純比較)にする。自動のときは platform_mismatch を出さず、判定なし・食い違いのときだけ
platform_auto の警告を1件出す。食い違いのときは AI に送らない(platform_conflict)。

Si-R の指定と中身の食い違い(ADR-0161 の追記。REQ-222): 種類が config で機種の指定が fujitsu_sir のとき、区間を選んだ後の
両側の本文に detect_platform を当てる。どちらかが fortinet か a なら、送る文を作らず(analyze を呼ばない)、
llm を status=skipped・reason=platform_not_sir にする。判定が fujitsu_sir か None なら今のまま。比較・警告は変えない。
すべての入口が run_compare を通るので、窓・CLI・一括比較・まとめて比較・追加の質問のどれでも同じになる。

入力の案内(ADR-0138 決定7。REQ-189): 側ごとに形を戻した後のテキストから案内(diagnose_input)を当て、input_hint の警告にする。
input_form の直後に同じ経路で置く。構造化比較に失敗したとき(structured_compare_failed。重複も含む)は、エラーの文の後ろにも
「 {側}: {文}」を足す。detail は変えない。案内は数と種類だけで、行の中身は入れない。

単純比較の設定(ADR-0166。REQ-227): 要求の raw_options を単純比較(compare_raw と、show のテンプレートが無くて単純比較に
回ったとき)にだけ渡す。構造化比較は変えない。誤りのある設定は status=error・code=raw_options_error。
設定で側の行の半分を超えて無視したとき(その側が 10 行以上)は、raw_ignored_many の警告を pager_marks と同じ経路の最後に足す。
"""

import dataclasses
from collections.abc import Callable

from driftpilot import i18n
from driftpilot.config_parser import UnsupportedConfigPlatform
from driftpilot.config_parser.family_hint import AUTO_PLATFORM, detect_platform, family_hint
from driftpilot.config_parser.platforms import CONFIG_PLATFORM_NAMES, flat_platform
from driftpilot.core.types import (
    SIDES,
    AutoPlatform,
    Candidate,
    CompareCancelled,
    CompareError,
    CompareOutcome,
    CompareRequest,
    ComparisonExclusion,
    SideName,
    SideSegmentation,
    SideSelection,
)
from driftpilot.diff_engine import (
    CompareResult,
    DuplicateLineError,
    RawCompareResult,
    SafetyWarning,
    ShowCompareResult,
    compare_configs,
    compare_raw,
    compare_show,
)
from driftpilot.diff_engine.exposure_check import ExposureNotice, ExposureSide, check_default_exposure
from driftpilot.diff_engine.normalization import (
    NormalizationRule,
    apply_rules_traced,
    builtin_rules,
    normalize_text,
    select_rules,
)
from driftpilot.format_detect import AmbiguousFormatError, detect_format, sniff
from driftpilot.llm_analyzer import DISABLED, LlmAnalysis, LlmRequest, analyze
from driftpilot.llm_analyzer.payload import LlmInputs
from driftpilot.raw_keys import RawOptionsError
from driftpilot.segmentation import (
    Candidates,
    Segment,
    SelectionError,
    SplitResult,
    classify_exposure,
    diagnose_input,
    select,
    split_log,
)

# 進捗の工程名。工程の入口で通知する(中断の確認も同じ位置)
STAGES = ("split", "select", "detect_kind", "compare", "safety_checks", "llm", "done")

# 利用者に見える文。言語に合わせて読み込むときに 1 回だけ選ぶ(messages は読まない決まりのため、ここに持つ)
_TEXTS_JA = {
    "side_before": "変更前",
    "side_after": "変更後",
    "platform_show_command": "--platform と --show-command は両方指定してください",
    "platform_use_kind_config": "。{platform} の config を比較するときは --kind config を指定してください(--show-command は不要)",
    "kind_mismatch": "変更前と変更後で入力の種類の判定が食い違います。--kind config または --kind show を指定してください",
    "structured_failed": "structured 比較に失敗しました: {error_type}: {error}",
}
_TEXTS_EN = {
    "side_before": "Before",
    "side_after": "After",
    "platform_show_command": "Specify both --platform and --show-command",
    "platform_use_kind_config": ". To compare a {platform} config, specify --kind config (--show-command is not needed)",
    "kind_mismatch": "The input kind detected for Before and After does not match. Specify --kind config or --kind show",
    "structured_failed": "Structured comparison failed: {error_type}: {error}",
}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)
_LABEL = {"before": _T["side_before"], "after": _T["side_after"]}


def run_compare(
    request: CompareRequest,
    *,
    llm: LlmRequest | None = None,
    on_progress: Callable[[str], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> CompareOutcome:
    """1件の比較を実行する。should_cancel が真なら CompareCancelled を送出する。

    llm は入口が組み立てた LLM 解析の要求。None なら解析しない(結果の llm は disabled)。
    """

    def enter(stage: str) -> None:
        if should_cancel is not None and should_cancel():
            raise CompareCancelled(stage)
        if on_progress is not None:
            on_progress(stage)

    mode = request.mode
    # kind=config の --platform は既定でないパーサーの系統を選ぶ。意味を持つのは平坦 config の名前だけで、
    # それ以外(cisco_ios・ntc の名前)は今までどおり「--show-command と両方指定」を求める(REQ-21 §10)
    flat_name = request.platform in CONFIG_PLATFORM_NAMES
    auto = request.platform == AUTO_PLATFORM
    if auto:
        pass  # 機種の自動は show のコマンドとの対を求めない(ADR-0151 決定4)
    elif request.kind == "config" and flat_name:
        if (unsupported := _unsupported_platform(mode, request.platform, "request")) is not None:
            return unsupported
    elif bool(request.platform) != bool(request.show_command):
        message = _T["platform_show_command"]
        if flat_name:
            message += _T["platform_use_kind_config"].format(platform=request.platform)
        return _error(mode, "platform_show_command", "request", message)

    inputs = {"before": request.before, "after": request.after}
    selections: dict[SideName, SideSelection] = {"before": request.before_selection, "after": request.after_selection}

    enter("split")
    splits: dict[SideName, SplitResult] = {side: split_log(inputs[side].text, prompt=request.prompt) for side in SIDES}

    enter("select")
    chosen: dict[SideName, Segment | Candidates] = {}
    for side in SIDES:
        try:
            chosen[side] = select(splits[side].segments, command=selections[side].command, number=selections[side].number)
        except SelectionError as exc:
            return _error(mode, "selection_error", "select", f"{_LABEL[side]}: {exc}", {"side": side})
    segmentation = {
        side: SideSegmentation(inputs[side].name, splits[side], c if isinstance(c := chosen[side], Segment) else None)
        for side in SIDES
    }
    candidates = [
        Candidate(side, s) for side in SIDES if isinstance(chosen[side], Candidates) for s in chosen[side].segments
    ]
    if candidates:
        return CompareOutcome(
            status="needs_selection",
            mode=mode,
            segmentation=segmentation,
            candidates=candidates,
            selection_reason="segment",
        )
    # 前置き行を外した本文を、structured と raw の両方へ渡す
    before = chosen["before"].text
    after = chosen["after"].text

    enter("detect_kind")
    detected: dict[SideName, str | None] | None = None
    try:
        if auto and request.kind == "auto":
            detected = {"before": detect_platform(before), "after": detect_platform(after)}
            kind = _resolve_auto_kind(detected, {"before": before, "after": after})
        else:
            kind = _resolve_kind(request, before, after)
    except AmbiguousFormatError as exc:
        return CompareOutcome(
            status="needs_selection",
            mode=mode,
            segmentation=segmentation,
            selection_reason="ambiguous_format",
            selection_detail={"scores": exc.scores, "message": str(exc)},
        )
    if kind is None:
        return _error(
            mode,
            "kind_mismatch",
            "detect_kind",
            _T["kind_mismatch"],
            segmentation=segmentation,
        )

    enter("compare")
    # config と決まったときだけ、--platform をパーサーの系統として渡す(show では ntc-templates の名前のまま)。
    # 機種が自動なら、config では判定で決めた系統を手で指定したのと同じに渡し、show では機種なし(単純比較)にする
    auto_platform: AutoPlatform | None = None
    if auto and kind == "config":
        if detected is None:
            detected = {"before": detect_platform(before), "after": detect_platform(after)}
        auto_platform = combine_platforms(detected["before"], detected["after"])
        config_platform = auto_platform.used
    else:
        config_platform = request.platform if kind == "config" else None
    show_platform = None if auto else request.platform
    show_command = None if auto else request.show_command
    if (unsupported := _unsupported_platform(mode, config_platform, "compare", segmentation)) is not None:
        return unsupported
    run_structured = mode in ("structured", "both")
    run_raw = mode in ("raw", "both")
    structured: CompareResult | None = None
    structured_error: str | None = None
    structured_error_type: str | None = None
    duplicate: DuplicateLineError | None = None
    raw: RawCompareResult | None = None
    show: ShowCompareResult | None = None
    rules = effective_rules(request)
    structured_texts = {"before": before, "after": after}
    exclusions: list[ComparisonExclusion] = []
    try:
        if kind == "show" and run_structured:
            show = compare_show(
                before,
                after,
                platform=show_platform,
                command=show_command,
                rules=rules,
                enabled_ids=request.enabled_rule_ids,
                options=request.raw_options,
            )
            # テンプレートがなければ raw の結果を出す。構造化できたときは --mode both のときだけ raw も付ける
            raw = show.raw if show.method == "raw" else None
        if run_raw and raw is None:
            raw = compare_raw(
                before,
                after,
                kind=kind,
                rules=rules,
                enabled_ids=request.enabled_rule_ids,
                platform=config_platform,
                options=request.raw_options,
            )
        platform_rules = _platform_rules(rules, config_platform, request.enabled_rule_ids)
        if platform_rules:
            counts: dict[SideName, dict[str, int]] = {}
            for side, text in (("before", before), ("after", after)):
                structured_texts[side], counts[side] = normalize_text(text, platform_rules)
            exclusions = [
                ComparisonExclusion(r.exclusion_reason, r.id, {side: counts[side][r.id] for side in SIDES})
                for r in platform_rules
                if r.exclusion_reason is not None and sum(counts[side][r.id] for side in SIDES) > 0
            ]
    except RawOptionsError as exc:  # 単純比較の設定の誤り(入口で先に止める。ここは守り)
        return _error(mode, "raw_options_error", "compare", _raw_options_message(exc), segmentation=segmentation)
    except ValueError as exc:  # 正規化ルールの指定の誤り(未知の ID、kind で使えない ID など)
        return _error(mode, "rules_error", "compare", str(exc), segmentation=segmentation)
    if run_structured and kind != "show":
        try:
            structured = compare_configs(structured_texts["before"], structured_texts["after"], config_platform)
        except DuplicateLineError as exc:  # 同じ行の重複。専用の文面にする(REQ-26 §B-2)
            duplicate = exc
        except Exception as exc:  # noqa: BLE001 - raw の結果は残すため、ここでは止めない
            structured_error_type, structured_error = type(exc).__name__, str(exc)

    enter("safety_checks")
    # 既定値込み版の判定は config と判定された区間にだけ適用する。比較の各側の属性として持つ
    exposure: dict[SideName, ExposureSide] | None = None
    notices: list[ExposureNotice] = []
    if kind == "config":
        exposure = {
            side: ExposureSide(chosen[side].command, classify_exposure(chosen[side].command)) for side in SIDES
        }
        notices = check_default_exposure(exposure["before"], exposure["after"])
    mismatch: list[SafetyWarning] = []
    if auto_platform is not None:
        mismatch = _platform_auto(auto_platform)
    elif kind == "config" and run_structured:
        mismatch = _platform_mismatch(config_platform, {"before": before, "after": after})
    hints = _input_hints(request)
    extra = [
        *_input_forms(request),
        *hints,
        *mismatch,
        *_pager_marks(splits),
        *_raw_ignored_many(raw if raw is not None else (show.raw if show is not None else None), before, after),
    ]
    if extra and structured is not None:
        structured = dataclasses.replace(structured, warnings=[*structured.warnings, *extra])

    has_diff = bool((structured and structured.diffs) or (raw and raw.diffs) or (show and show.diffs))
    error = None
    if duplicate is not None:
        error = _duplicate_error(mode, duplicate)
    elif structured_error is not None:
        error = CompareError(
            "structured_compare_failed",
            "structured_compare",
            _T["structured_failed"].format(error_type=structured_error_type, error=structured_error),
            {"stage": "structured_compare", "error_type": structured_error_type, "error": structured_error},
        )
    if error is not None and hints:
        error = _with_hints(error, hints)
    enter("llm")
    llm_result = _llm_step(
        llm,
        request=request,
        kind=kind,
        error=error,
        auto_platform=auto_platform,
        rules=rules,
        before=before,
        after=after,
        structured=structured,
        raw=raw,
        show=show,
    )
    enter("done")
    return CompareOutcome(
        status="error" if error else ("difference" if has_diff else "no_difference"),
        mode=mode,
        kind=kind,
        has_diff=has_diff,
        segmentation=segmentation,
        structured=structured,
        raw=raw,
        show=show,
        default_exposure=exposure,
        warnings=list(structured.warnings) if structured else extra,
        notices=notices,
        error=error,
        llm=llm_result,
        exclusions=exclusions,
        auto_platform=auto_platform,
    )


def _llm_step(
    llm: LlmRequest | None,
    *,
    request: CompareRequest,
    kind: str,
    error: CompareError | None,
    auto_platform: AutoPlatform | None,
    rules: list[NormalizationRule],
    before: str,
    after: str,
    structured: CompareResult | None,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
) -> LlmAnalysis:
    """比較の最後の工程(llm)。送らない場合の判断と _llm_analysis の呼び出し。run_compare と analyze_compared が共有する。"""
    if llm is not None and kind == "config" and request.platform == "fujitsu_sir" and looks_not_sir(before, after):
        # Si-R の読み方は FortiGate の場所を持たないので、場所の条件のある形が当たらない。送る文も作らない
        # (analyze・build_payload を呼ばない。ADR-0161 の追記。REQ-222)。比較の結果は変えない
        return LlmAnalysis(status="skipped", reason="platform_not_sir")
    if auto_platform is not None and auto_platform.conflict and llm is not None:
        # 違う機種どうしの比較は送らない。送信前の確認にも進めない(ADR-0151 決定2)
        llm = dataclasses.replace(llm, client=None, dry_run=False, unavailable="platform_conflict")
    if error is not None and llm is not None and llm.skip_failed and llm.client is not None:
        # 失敗した比較の結果は送らない(一括比較の送る段。送る内容は作り、理由を残す)
        llm = dataclasses.replace(llm, client=None, unavailable="compare_failed")
    return _llm_analysis(llm, rules, before, after, structured, raw, show)


def analyze_compared(request: CompareRequest, outcome: CompareOutcome, llm: LlmRequest | None) -> CompareOutcome:
    """比べ終わった結果から、AI の解析の工程だけを行う(REQ-264。比較・切り分け・安全網はやり直さない)。

    run_compare(request, llm=llm) の最後の工程(llm)と同じ判断・同じ送る文にする。入力は outcome に残っている値
    (kind・error・auto_platform・structured・raw・show と、選んだ区間の本文)と effective_rules(request)。
    llm 以外の結果は変えない。AI の工程まで進んでいない結果(kind が無い・区間を選べていない)は、今の run_compare と
    同じく AI の解析が付かないので、そのまま返す。
    """
    segmentation = outcome.segmentation
    if outcome.kind is None or segmentation is None:
        return outcome
    before, after = segmentation["before"].selected, segmentation["after"].selected
    if before is None or after is None:
        return outcome
    llm_result = _llm_step(
        llm,
        request=request,
        kind=outcome.kind,
        error=outcome.error,
        auto_platform=outcome.auto_platform,
        rules=effective_rules(request),
        before=before.text,
        after=after.text,
        structured=outcome.structured,
        raw=outcome.raw,
        show=outcome.show,
    )
    return dataclasses.replace(outcome, llm=llm_result)


def choose_platform(before: str, after: str) -> AutoPlatform:
    """機種が自動のとき、区間を選んだ後の両側の本文から使う系統を決める(ADR-0151 決定1・2)。"""
    return combine_platforms(detect_platform(before), detect_platform(after))


def combine_platforms(before: str | None, after: str | None) -> AutoPlatform:
    """片側ごとの判定(fujitsu_sir・fortinet・a・None)の組み合わせ(ADR-0151 決定2 の表)。

    同じならそれ、片側だけ判定できればその側、両側とも判定なしなら指定なし、違えば指定なしで conflict。
    a(Cisco 系)は指定なし(used=None)。
    """
    known = {p for p in (before, after) if p is not None}
    if len(known) > 1:
        return AutoPlatform(None, before, after, conflict=True)
    chosen = known.pop() if known else None
    return AutoPlatform(None if chosen == "a" else chosen, before, after)


def looks_not_sir(before: str, after: str) -> bool:
    """機種に Si-R を指定したとき、どちらかの側の判定が FortiGate か Cisco 系か(ADR-0161 の追記。REQ-222)。

    判定が fujitsu_sir か判定なし(None)の側は疑わない。
    """
    return any(detect_platform(text) in ("fortinet", "a") for text in (before, after))


def _platform_auto(auto_platform: AutoPlatform) -> list[SafetyWarning]:
    """判定なし(両側)・食い違いの知らせ(ADR-0151 決定2)。detail は判定の名前だけで、値も行も入れない。"""
    if auto_platform.conflict:
        result = "conflict"
    elif auto_platform.before is None and auto_platform.after is None:
        result = "undetected"
    else:
        return []
    detail = {"result": result, "before": auto_platform.before, "after": auto_platform.after}
    return [SafetyWarning("platform_auto", None, None, detail)]


def effective_rules(request: CompareRequest) -> list[NormalizationRule]:
    """比較に使うルール。要求に無ければ同梱の機種のルール。要求にあれば(空でも)そのファイルだけ(既定を外す手段)。"""
    return request.rules if request.rules is not None else builtin_rules()


def _platform_rules(
    rules: list[NormalizationRule], platform: str | None, enabled_ids: list[str] | None
) -> list[NormalizationRule]:
    """structured の前の本文に当てる機種のルール。比較の機種のルールが rules に無ければ空(A型・B型は何も選ばない)。"""
    if platform is None or not any(r.platform == platform for r in rules):
        return []
    return [r for r in select_rules(rules, "config", enabled_ids, platform) if r.platform is not None]


def _llm_analysis(
    llm: LlmRequest | None,
    rules: list[NormalizationRule],
    before: str,
    after: str,
    structured: CompareResult | None,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
) -> LlmAnalysis:
    """差分を読むだけで書き換えない。行単位の差分の行番号に合わせて、比較と同じ正規化をした行を渡す。

    正規化前の全行も渡す。PEM ブロックの範囲は正規化前の本文全体から求める(正規化ルールが BEGIN の行を変えても見失わない)。
    """
    if llm is None:
        return DISABLED
    inputs = _analysis_inputs(rules, before, after, raw, show)
    return analyze(
        llm,
        structured=structured,
        raw=raw,
        show=show,
        before_lines=inputs.before_lines,
        after_lines=inputs.after_lines,
        before_source=inputs.before_source,
        after_source=inputs.after_source,
    )


def _analysis_inputs(
    rules: list[NormalizationRule],
    before: str,
    after: str,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
) -> LlmInputs:
    """analyze に渡す行。行単位の差分と同じ正規化をした行と、正規化前の全行。"""
    applied = raw if raw is not None else (show.raw if show is not None else None)
    by_id = {r.id: r for r in rules}
    used = [by_id[i] for i in applied.normalization.enabled_rule_ids if i in by_id] if applied is not None else []
    before_lines, _ = apply_rules_traced(before, used)
    after_lines, _ = apply_rules_traced(after, used)
    return LlmInputs(list(before_lines), list(after_lines), before.splitlines(), after.splitlines())


def llm_inputs(request: CompareRequest, outcome: CompareOutcome) -> LlmInputs | None:
    """_llm_analysis が analyze に渡すのと同じ行(正規化の後と前。ADR-0158 決定7)。区間の本文が無ければ None。

    区間の本文は切り分けで選んだ区間(outcome.segmentation の selected.text。run_compare の比べた本文と同じ)、
    ルールは effective_rules(request)。追加の質問で変更後の全文を作り直すときに使う。
    """
    segmentation = outcome.segmentation
    if segmentation is None:
        return None
    selected = {side: segmentation[side].selected for side in SIDES}
    before, after = selected["before"], selected["after"]
    if before is None or after is None:
        return None
    return _analysis_inputs(effective_rules(request), before.text, after.text, outcome.raw, outcome.show)


def _resolve_kind(request: CompareRequest, before: str, after: str):
    """config か show か。2つの判定が食い違うと None。曖昧なら AmbiguousFormatError。"""
    if request.kind != "auto":
        return request.kind
    kinds = {
        detect_format(text, platform=request.platform, command=request.show_command).kind for text in (before, after)
    }
    return kinds.pop() if len(kinds) == 1 else None


def _resolve_auto_kind(detected: dict[SideName, str | None], texts: dict[SideName, str]):
    """種類も機種も自動のときの種類(ADR-0151 決定3)。2つの判定が食い違うと None。AmbiguousFormatError は出さない。

    片側ごとに、機種の判定が fujitsu_sir・fortinet なら config。ほかは sniff の判定が show なら show、それ以外は config。
    """
    kinds = set()
    for side in SIDES:
        if detected[side] in ("fujitsu_sir", "fortinet"):
            kinds.add("config")
        else:
            kinds.add("show" if sniff(texts[side]).verdict == "show" else "config")
    return kinds.pop() if len(kinds) == 1 else None


def _platform_mismatch(platform: str | None, texts: dict[SideName, str]) -> list[SafetyWarning]:
    """指定した系統と中身の見立てが食い違う側ごとの警告(安全網5)。見立てが無い(None)側は疑わない。

    detail は指定(系統の名前。A型は None)・見立て・手がかりの ID だけ。設定値も行も入れない(ADR-0017・ADR-0019)。
    """
    flat = flat_platform(platform)
    expected = flat.family if flat is not None else "a"
    specified = flat.name if flat is not None else None
    warnings: list[SafetyWarning] = []
    for side in SIDES:
        hint = family_hint(texts[side])
        if hint is not None and hint.family != expected:
            detail = {"specified": specified, "hint": hint.family, "rule": hint.rule}
            warnings.append(SafetyWarning("platform_mismatch", side, None, detail))
    return warnings


def _pager_marks(splits: dict[SideName, SplitResult]) -> list[SafetyWarning]:
    """ページ送りの印を外した側ごとの警告(ADR-0134 決定2)。detail は外した数(ファイル全体)だけ。"""
    return [
        SafetyWarning("pager_marks", side, None, {"count": splits[side].pager_marks})
        for side in SIDES
        if splits[side].pager_marks > 0
    ]


# 無視した行が多いと知らせる下限(その側の行の数)。ADR-0166 決定11-3
RAW_IGNORED_MANY_MIN_LINES = 10


def _raw_ignored_many(raw: RawCompareResult | None, before: str, after: str) -> list[SafetyWarning]:
    """単純比較の設定で、側の行の半分を超えて無視したときの警告(ADR-0166 決定11-3)。detail は数だけ。

    行の数は比べた本文の行の数(compare_raw と同じ splitlines の数え方)。その側が 10 行未満なら出さない。
    """
    if raw is None or raw.options is None:
        return []
    warnings: list[SafetyWarning] = []
    for side, text in (("before", before), ("after", after)):
        lines = len(text.splitlines())
        ignored = len(raw.options.ignored.get(side, []))
        if lines >= RAW_IGNORED_MANY_MIN_LINES and ignored * 2 > lines:
            warnings.append(SafetyWarning("raw_ignored_many", side, None, {"ignored": ignored, "lines": lines}))
    return warnings


def _raw_options_message(exc: RawOptionsError) -> str:
    """設定の誤りの文(1 件 1 行をつなぐ)。式そのものは入れない。"""
    if not exc.problems:
        return str(exc)
    # messages は driftpilot.core を import するので、循環を避けるため呼ぶときに読む
    from driftpilot.messages import raw_option_problem_lines

    return " ".join(raw_option_problem_lines(exc.problems))


def _input_forms(request: CompareRequest) -> list[SafetyWarning]:
    """読み込みで入力の形を戻した知らせごとの警告(ADR-0138 決定7)。detail は種類と数だけ。"""
    return [
        SafetyWarning("input_form", side, None, {"kind": note.kind, "count": note.count})
        for side in SIDES
        for note in getattr(request, side).notes
    ]


def _input_hints(request: CompareRequest) -> list[SafetyWarning]:
    """入力の様子から当てた案内ごとの警告(ADR-0138 決定7。REQ-189)。detail は種類と数(と幅・付け足しの形)だけ。"""
    warnings: list[SafetyWarning] = []
    for side in SIDES:
        for hint in diagnose_input(getattr(request, side).text):
            detail: dict = {"kind": hint.kind, "count": hint.count}
            if hint.width is not None:
                detail["width"] = hint.width
            if hint.form is not None:
                detail["form"] = hint.form
            warnings.append(SafetyWarning("input_hint", side, None, detail))
    return warnings


def _with_hints(error: CompareError, hints: list[SafetyWarning]) -> CompareError:
    """構造化比較の失敗の文の後ろに、案内を「 {側}: {文}」で足す。ほかのエラーと detail は変えない。"""
    if error.code != "structured_compare_failed":
        return error
    # messages は driftpilot.core を import するので、循環を避けるため呼ぶときに読む
    from driftpilot.messages import warning_message

    return dataclasses.replace(error, message=error.message + "".join(f" {warning_message(w)}" for w in hints))


def _unsupported_platform(mode, platform: str | None, stage: str, segmentation=None) -> CompareOutcome | None:
    """config の表がまだ無い機種(fujitsu_srs)なら status=error の結果、それ以外は None。"""
    try:
        flat_platform(platform)
    except UnsupportedConfigPlatform as exc:
        return _error(
            mode, "unsupported_config_platform", stage, str(exc), {"platform": platform}, segmentation=segmentation
        )
    return None


def _duplicate_error(mode, exc: DuplicateLineError) -> CompareError:
    """重複で構造化比較ができなかったときのエラー。code・stage は他の構造化の失敗と同じで、文面と detail だけを変える。

    detail の今のキー(stage・error_type・error)は残す。error_type は元の例外の型の名前、error はライブラリの例外文と
    同じ形(JSON の structured.error にもこれが入る)。行と親のパスは本文のまま出す。エラー文は LLM に送らないため
    (決定者の指示による REQ-26 §B-2 からの変更。docs/notes/req-26-duplicate-siblings.md)。
    """
    # messages は driftpilot.core を import するので、循環を避けるため呼ぶときに読む
    from driftpilot.messages import structured_duplicate_message

    cause = exc.__cause__ if exc.__cause__ is not None else exc
    detail = {
        "stage": "structured_compare",
        "error_type": type(cause).__name__,
        "error": exc.summary(),
        "side": exc.side,
        "scope": " > ".join(exc.scope) if exc.scope else None,
        "line": exc.line,
        "lines": list(exc.lines),
    }
    message = structured_duplicate_message(detail, mode)
    return CompareError("structured_compare_failed", "structured_compare", message, detail)


def _error(mode, code: str, stage: str, message: str, detail: dict | None = None, segmentation=None) -> CompareOutcome:
    return CompareOutcome(
        status="error",
        mode=mode,
        segmentation=segmentation,
        error=CompareError(code, stage, message, {"stage": stage, **(detail or {})}),
    )
