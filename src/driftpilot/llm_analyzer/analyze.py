"""LLM 解析の本体(ADR-0018 決定4・決定5)。core の工程 llm から呼ぶ。

差分(structured / raw / show)は読むだけで書き換えない。回答は LlmAnalysis に入り、比較の判定には使わない。
どの失敗でも例外を外へ出さず、status=skipped と理由を返す(比較は完走する)。
プロバイダの例外の文面は残さない(認証エラーの文面にキーの一部が載ることがあるため)。残すのは種類と状態コードだけ。
"""

from collections.abc import Callable, Sequence
from datetime import datetime, timezone

from driftpilot import i18n
from driftpilot.diff_engine import CompareResult, RawCompareResult, ShowCompareResult
from driftpilot.llm_analyzer.api_error import ApiErrorText, for_display
from driftpilot.llm_analyzer.approval import PromptNotApproved
from driftpilot.llm_analyzer.client import LLMConnectionError, LLMResponseError, StreamStopped, ask_with_deltas
from driftpilot.llm_analyzer.mask_patterns import masking_fingerprint
from driftpilot.llm_analyzer.masking import Masker, normalize_words
from driftpilot.llm_analyzer.follow_up import check_question, prompt_too_long
from driftpilot.llm_analyzer.payload import MAX_FULL_TEXT_CHARS, WholeSection, build_payload, whole_body
from driftpilot.llm_analyzer.prompt import (
    WHOLE_VERSION_SUFFIX,
    build_prompt,
    diff_format_text,
    prompt_version_of,
    template_for,
    with_after_text,
    with_extra_instruction,
    with_premise,
    with_whole_rules,
)
from driftpilot.llm_analyzer.types import LlmAnalysis, LlmReason, LlmRequest


# 利用者に見える誤りの文(日本語・英語。REQ-258)。{value!r} は値の repr
_TEXTS_JA = {"max_total_diffs_invalid": "max_total_diffs は 1 以上の整数にしてください: {value!r}"}
_TEXTS_EN = {"max_total_diffs_invalid": "max_total_diffs must be an integer of 1 or more: {value!r}"}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def analyze(
    request: LlmRequest,
    *,
    structured: CompareResult | None,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
    before_lines: Sequence[str],
    after_lines: Sequence[str],
    before_source: Sequence[str] | None = None,
    after_source: Sequence[str] | None = None,
    clock: Callable[[], str] = _now,
) -> LlmAnalysis:
    """差分を伏せて送り、回答を注釈として返す。

    before_lines / after_lines は比較した区間の正規化後の全行(行単位の差分の行番号はこれを指す)。
    before_source / after_source は正規化前の全行で、PEM ブロックの範囲と祖先の行はこちらから求める(省略時は正規化後の行)。
    """
    sources = (before_source, after_source)
    try:
        return _analyze(request, structured, raw, show, before_lines, after_lines, sources, clock)
    except Exception as exc:  # noqa: BLE001 - 解析の失敗で比較を止めない(ADR-0018 決定4)
        return LlmAnalysis(status="skipped", reason="internal_error", detail={"error_type": type(exc).__name__})


def _analyze(request, structured, raw, show, before_lines, after_lines, sources, clock) -> LlmAnalysis:
    words = normalize_words(request.extra_secret_words)
    masker = Masker(secret_words=words)
    # 使用中が既定で show の比較なら「状態の確認」の本文で送る(ADR-0146 決定6)。送らないときの版も同じ決まりで決める。
    # 送る差分の種類は payload.source_of と同じ条件(構造化比較が無く、show の構造化の比較がある)で先に見る
    show_compare = structured is None and show is not None and show.method == "structured"
    template = template_for(request.prompt_template, "show" if show_compare else None)
    common = {
        "max_diffs": request.max_diffs,
        "masking_fingerprint": masking_fingerprint().digest,
        "secret_words": list(words),
        "prompt_version": prompt_version_of(template),
    }
    payload = build_payload(
        structured=structured,
        raw=raw,
        show=show,
        before_lines=before_lines,
        after_lines=after_lines,
        before_source=sources[0],
        after_source=sources[1],
        masker=masker,
        max_diffs=request.max_diffs,
        include_after_text=request.include_after_text,
    )
    reason: LlmReason
    if payload is None:
        # 行の差分はあるが、構造化比較が動いて 0 件(順番の違いなど)なら送らない(ADR-0080 決定3)
        reason = "no_structured_diff" if raw is not None and raw.diffs else "no_diff"
        return LlmAnalysis(status="skipped", reason=reason, diff_total=0, sent_diffs=0, **common)
    built = {
        "source": payload.source,
        "diff_total": payload.diff_total,
        "sent_diffs": payload.sent_diffs,
        "truncated": payload.truncated,
        "unsent": payload.unsent,
        **common,
    }
    if payload.sent_diffs == 0:
        return LlmAnalysis(status="skipped", reason="nothing_to_send", **built)
    if (refused := _premise_refusal(request, words, built)) is not None:
        return refused
    prompt = build_prompt(
        source=payload.source,
        body=payload.body,
        diff_total=payload.diff_total,
        max_diffs=request.max_diffs,
        truncated=payload.truncated,
        unsent_lines=payload.unsent.lines,
        template=template,
    )
    after_text = payload.after_text  # show の比較でも足す(ADR-0182 決定2)
    if after_text is not None:
        # 変更後の全文(ADR-0158 決定4・5)。上限を超えたら全文も差分も送らない(切り詰めない。送信前の確認にも進まない)
        if len(after_text.body) > MAX_FULL_TEXT_CHARS:
            return LlmAnalysis(status="skipped", reason="full_text_too_long", **built)
        prompt = with_after_text(prompt, after_text)
        if prompt_too_long(prompt):
            return LlmAnalysis(status="skipped", reason="full_text_too_long", **built)
    # 毎回付ける前提の文(ADR-0172 決定2)。全文の節の後ろ、打った文の前。送らない1回目にも入る(確認の全文と照合に同じ文が出る)
    prompt = with_premise(prompt, request.premise)
    if after_text is not None and request.premise is not None and prompt_too_long(prompt):
        # 全文を付けたときは、前提を足した後の全体ももう一度見る(超えれば全文も差分も送らない)
        return LlmAnalysis(status="skipped", reason="full_text_too_long", **built)
    # 利用者が解析の前に打った文(REQ-206)。前提の後ろに置く。送らない1回目にも入るので、送信前の確認の全文と送る直前の照合に同じ文が出る
    prompt = with_extra_instruction(prompt, request.extra_instruction)
    return _send(request, prompt, built, clock)


def _premise_refusal(request: LlmRequest, words: Sequence[str], built: dict) -> LlmAnalysis | None:
    """前提の文に伏せる対象か送れない行があれば、送る文を作らずに返す結果(ADR-0172 決定4)。無い・送れるなら None。

    確かめは追加の質問と同じ check_question で、語は Masker に使う語と同じ。dry_run でも同じに止める(確認にも進まない)。
    payload は入れず、detail は理由の種類だけ(前提の中身は入れない)。
    """
    if request.premise is None:
        return None
    refused = check_question(request.premise, words)
    if refused is None:
        return None
    return LlmAnalysis(status="skipped", reason="premise_refused", detail={"refused": refused}, **built)


def _send(request: LlmRequest, prompt: str, built: dict, clock: Callable[[], str]) -> LlmAnalysis:
    """送る段(1組の解析と、全部の組をまとめた解析で共有する)。dry_run・client なし・承認と違う・中止・接続の失敗を同じ形にする。

    built は結果に入れる件数などの値(source・diff_total・sent_diffs・truncated・unsent と共通の値)。
    """
    if request.dry_run:
        return LlmAnalysis(status="skipped", reason="dry_run", payload=prompt, **built)
    client = request.client
    if client is None:
        return LlmAnalysis(status="skipped", reason=request.unavailable, payload=prompt, **built)
    model = _model_name(client)
    asked_at = clock()
    api_text = None  # API が返したエラーの文と秒(結果には入れず、on_api_error にだけ渡す。ADR-0168 決定5)
    try:
        # on_delta があり接続が流せるなら、届いたところから渡す(ADR-0157)。答えに入るのは全部届いた文字だけ
        answer = ask_with_deltas(client, prompt, request.on_delta, request.on_thinking)
    except PromptNotApproved:
        # 承認した内容と違うため、送る前に止めた(送っていない)
        return LlmAnalysis(status="skipped", reason="changed_after_approval", model=model, payload=prompt, **built)
    except StreamStopped:
        # 答えが届いている途中で中止した。途中の文字は答えに入れない(ADR-0157 決定3・決定8)
        return LlmAnalysis(
            status="skipped", reason="cancelled", model=model, asked_at=asked_at, transmitted=True, payload=prompt, **built
        )
    except LLMConnectionError as exc:
        reason, detail = "connection_error", {"error_type": type(exc).__name__}
    except LLMResponseError as exc:
        reason, detail = "api_error", {"error_type": type(exc).__name__, "status_code": exc.status_code}
        api_text = getattr(exc, "api_error", None)
    except Exception as exc:  # noqa: BLE001 - 入口が渡した実装の想定外の例外も、比較を止めない
        reason, detail = "api_error", {"error_type": type(exc).__name__}
    else:
        if not isinstance(answer, str):
            reason, detail = "api_error", {"error_type": "InvalidAnswer"}
        else:
            return LlmAnalysis(
                status="done", model=model, asked_at=asked_at, answer=answer, transmitted=True, payload=prompt, **built
            )
    _tell_api_error(request, api_text)
    return LlmAnalysis(
        status="skipped", reason=reason, detail=detail, model=model, asked_at=asked_at, transmitted=True, payload=prompt, **built
    )


def _tell_api_error(request: LlmRequest, api_text: object) -> None:
    """except の外で、利用者の秘密の語でもう一度掃除した文と秒を on_api_error に渡す(ADR-0168 決定3・5)。

    渡す先が無い・掃除して何も残らないときは呼ばない。呼んだ関数の例外は握りつぶす(解析の結果は変えない)。
    """
    if request.on_api_error is None or not isinstance(api_text, ApiErrorText):
        return
    shown = for_display(api_text, request.extra_secret_words)
    if shown is None:
        return
    try:
        request.on_api_error(shown)
    except Exception:  # noqa: BLE001 - 受け取り口の失敗で比較を止めない
        pass


def analyze_whole(
    request: LlmRequest,
    sections: Sequence[WholeSection],
    *,
    max_total_diffs: int,
    unchanged: Sequence[str | None] = (),
    clock: Callable[[], str] = _now,
) -> LlmAnalysis:
    """全部の組を1つの文にして1回で送る(比較の窓のまとめての比較。ADR-0152 決定3〜6・決定9)。

    sections は送ってよい組(比較の失敗・選択待ち・機種の判定の食い違いは呼ぶ側が外す)。unchanged は両側があって
    差分の無かった組のコマンド(最後の行に名前だけを載せる)。伏せる記号の表は全部の組で1つ(Masker を1つだけ作る)。
    送った件数の和が max_total_diffs を超えたら送らない(over_total_limit。数え方は core.batch_llm.llm_preview と同じ)。
    analyze と同じく、どの失敗でも例外を外へ出さない。結果の source は whole。
    """
    return analyze_whole_sent(request, sections, max_total_diffs=max_total_diffs, unchanged=unchanged, clock=clock)[0]


def analyze_whole_sent(
    request: LlmRequest,
    sections: Sequence[WholeSection],
    *,
    max_total_diffs: int,
    unchanged: Sequence[str | None] = (),
    clock: Callable[[], str] = _now,
) -> tuple[LlmAnalysis, tuple[int, ...]]:
    """analyze_whole と同じ解析と、送る文の本文に入れた組の sections の位置(送る文ができなかったときは空)。"""
    if type(max_total_diffs) is not int or max_total_diffs < 1:
        raise ValueError(_T["max_total_diffs_invalid"].format(value=max_total_diffs))
    try:
        return _analyze_whole(request, sections, max_total_diffs, unchanged, clock)
    except Exception as exc:  # noqa: BLE001 - 解析の失敗で比較を止めない(ADR-0018 決定4)
        return LlmAnalysis(status="skipped", reason="internal_error", detail={"error_type": type(exc).__name__}), ()


def _analyze_whole(request, sections, max_total_diffs, unchanged, clock) -> tuple[LlmAnalysis, tuple[int, ...]]:
    words = normalize_words(request.extra_secret_words)
    masker = Masker(secret_words=words)
    whole = whole_body(sections, masker, request.max_diffs, unchanged=unchanged)
    # 使用中が既定で、本文に入れた組の種類が全部 show なら「状態の確認」の本文(ADR-0152 決定5・ADR-0146 決定6)。
    # 種類は組の種類で見る(テンプレートが無く単純比較に戻った show の組も show。差分の形式 sources では見ない)
    all_show = bool(whole.kinds) and all(kind == "show" for kind in whole.kinds)
    template = template_for(request.prompt_template, "show" if all_show else None)
    common = {
        "max_diffs": request.max_diffs,
        "masking_fingerprint": masking_fingerprint().digest,
        "secret_words": list(words),
        "prompt_version": prompt_version_of(template) + WHOLE_VERSION_SUFFIX,
    }
    built = {
        "source": "whole",
        "diff_total": whole.diff_total,
        "sent_diffs": whole.sent_diffs,
        "truncated": whole.truncated,
        "unsent": whole.unsent,
        **common,
    }
    reason: LlmReason
    if not whole.sent:
        # 送る本文が無い(名前だけでは送らない。決定9)。理由は1組の解析と同じ種類
        if whole.found:
            return LlmAnalysis(status="skipped", reason="nothing_to_send", **built), ()
        reason = "no_structured_diff" if whole.structured_zero else "no_diff"
        return LlmAnalysis(status="skipped", reason=reason, diff_total=0, sent_diffs=0, **common), ()
    if whole.sent_diffs > max_total_diffs:
        # 全体の上限を超えたら1件も送らない(ADR-0031 決定4。打ち切って一部だけ送ることはしない)
        return LlmAnalysis(status="skipped", reason="over_total_limit", **built), ()
    if (refused := _premise_refusal(request, words, built)) is not None:
        return refused, ()
    prompt = build_prompt(
        source=whole.sources[0],
        body=whole.body,
        diff_total=whole.diff_total,
        max_diffs=request.max_diffs,
        truncated=whole.truncated,
        unsent_lines=whole.unsent.lines,
        template=template,
        diff_format=diff_format_text(whole.sources),
        sent_range=whole.sent_range,
        omitted=whole.omitted,
    )
    # 答える内容の決まりは送る文に入れる(確認の全文に出る)。見え方の決まり(LOOK_RULES)は本物の client が後ろに足す。
    # 前提の文(ADR-0172 決定2)は WHOLE_RULES の後ろ、打った文の前
    prompt = with_extra_instruction(with_premise(with_whole_rules(prompt), request.premise), request.extra_instruction)
    return _send(request, prompt, built, clock), whole.sent


def _model_name(client) -> str | None:
    try:
        name = client.model_name
    except Exception:  # noqa: BLE001
        return None
    return name if isinstance(name, str) else None
