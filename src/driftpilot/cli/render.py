"""結果(CompareOutcome)の整形。text と json(ADR-0015 決定4・決定8)。

JSON には警告・注意の文面(message)を含めない。文面は text のときだけ付ける。
LLM 解析(REQ-16)は JSON では常に最後の llm に入り、text では指定したときだけ「== LLM 解析 ==」の欄を出す。
比較から外した値(ADR-0025 決定6)は、JSON では notices の後ろの exclusions、text では「比較対象外:」の行に出す。
全部の比較(--all-segments。REQ-121)は to_json_all・to_text_all。組ごとの中身は1組の整形と同じものを使う。
外したページ送りの印の数(ADR-0134 決定2)は、JSON では segmentation の各側の pager_marks に出す。text では警告の行を、
構造化の欄があればその中、無ければ(show・raw だけ)「== segmentation ==」の前に出す。
機種を自動(--platform auto)にして config と決まったとき(ADR-0151 決定7)は、text では先頭に「機種: 自動（FortiGate）」の1行、
JSON では exclusions の後ろに platform_used(使った系統の名前。指定なしは null)を出す。自動でないときは今のまま。
"""

import json
from dataclasses import asdict, replace
from typing import Any

from driftpilot.llm_analyzer import LlmAnalysis
from driftpilot.llm_analyzer.answer_format import plain_answer
from driftpilot.messages import (
    auto_platform_line,
    exclusion_line,
    llm_section_lines,
    notice_line,
    RAW_MOVES_SKIPPED_CLI,
    raw_ignored_counts_line,
    raw_move_cli_line,
    raw_options_summary_text,
    route_detail_note,
    warning_message,
)
from driftpilot.core import SCHEMA_VERSION, CompareOutcome, MultiCompareOutcome, PairResult, SideSegmentation
from driftpilot.diff_engine import CompareResult, RawCompareResult, SafetyWarning, ShowCompareResult
from driftpilot.segmentation import Segment

SIDES = (("before", "変更前"), ("after", "変更後"))


def _plain(value: Any) -> Any:
    """JSON にできる形へ変える(タプルはリスト、dataclass は辞書)。"""
    if hasattr(value, "__dataclass_fields__"):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    # IPv4Interface / IPv4Address など、JSON にできない型は文字列にする
    return str(value)


def _diff_value(d: Any, side: str) -> str:
    """差分の値の表示。足した・消した経路に添えた機種固有値は、辞書から外して短い注記にする(ADR-0072)。"""
    value = d[side]
    if d["category"] == "static_routes" and isinstance(value, dict) and "vendor_specific" in value:
        rest = {k: v for k, v in value.items() if k != "vendor_specific"}
        return f"{rest!r}{route_detail_note(value['vendor_specific'])}"
    return repr(value)


def _structured_text(result: CompareResult | None, error: str | None, warnings: list[SafetyWarning]) -> list[str]:
    """warnings は結果全体の警告。structured が失敗したときだけ使う(安全網5の見立て。REQ-97)。"""
    lines = ["== structured =="]
    if result is None:
        return [*lines, f"エラー: {error}", *(f"警告: {warning_message(w)}" for w in warnings)]
    if not result.diffs:
        lines.append("差分なし")
    for d in result.diffs:
        where = d["category"] + (f" {d['name']}" if d["name"] else "")
        path = " > ".join(d["path"])
        before, after = _diff_value(d, "before"), _diff_value(d, "after")
        lines.append(f"[{d['op']}] {where}{' / ' + path if path else ''}: {before} -> {after}")
    counts = result.unparsed_counts
    lines.append(f"構造化不可の行数: 変更前 {counts['before']} / 変更後 {counts['after']}")
    lines.extend(f"警告: {warning_message(w)}" for w in result.warnings)
    return lines


def _raw_text(result: RawCompareResult) -> list[str]:
    lines = ["== raw =="]
    if not result.diffs:
        lines.append("差分なし")
    for d in result.diffs:
        if d["op"] == "removed":
            lines.append(f"- {d['before_line_no']}: {d['text']}")
        else:
            lines.append(f"+ {d['after_line_no']}: {d['text']}")
    rec = result.normalization
    lines.append("有効なルール: " + (", ".join(rec.enabled_rule_ids) or "なし"))
    for side, label in SIDES:
        for rule_id, count in rec.applied[side].items():
            lines.append(f"適用 ({label}): {rule_id} {count} 行")
    if result.options is not None:
        # 単純比較の設定(ADR-0166)。既定の設定では出さない(今の text のまま)
        opt = result.options
        # 最小が標準に戻ったときは「比べ方: 最小（変更が多いため標準で比べました）」(REQ-228 §6)
        lines.append(raw_options_summary_text(dict(opt.options), opt.algorithm_used) or "")
        lines.append(
            raw_ignored_counts_line(len(opt.ignored.get("before", [])), len(opt.ignored.get("after", [])), len(opt.equated))
        )
        if opt.moves_skipped:
            lines.append(RAW_MOVES_SKIPPED_CLI)
        for move in opt.moves:
            lines.append(raw_move_cli_line(move["number"], move["before_start"], move["after_start"], move["count"]))
    return lines


def _show_text(result: ShowCompareResult) -> list[str]:
    lines = ["== show structured =="]
    if result.method == "raw":
        return [*lines, f"raw 比較にフォールバックしました: {result.fallback_reason}"]
    if not result.diffs:
        lines.append("差分なし")
    for d in result.diffs:
        label = d["key"] if d["key"] is not None else "(行全体)"
        if d["op"] == "changed":
            detail = ", ".join(f"{c}: {b!r} -> {a!r}" for c, (b, a) in d["fields"].items())
        else:
            detail = repr(d["before"] if d["op"] == "removed" else d["after"])
        lines.append(f"[{d['op']}] {label}: {detail}")
    return lines


def _segment_json(seg: Segment) -> dict[str, Any]:
    return {"number": seg.number, "command": seg.command, "line_count": seg.line_count, "preamble": list(seg.preamble)}


def _split_json(side: SideSegmentation) -> dict[str, Any]:
    split = side.split
    return {
        "file": side.name,
        "prompt": split.prompt,
        "prompts": list(split.prompts),
        "prompt_source": split.prompt_source,
        "selected": side.selected.number if side.selected else None,
        "segments": [_segment_json(s) for s in split.segments],
        "pager_marks": split.pager_marks,
    }


def _split_text(label: str, side: SideSegmentation) -> list[str]:
    split = side.split
    prompt = ", ".join(repr(p) for p in split.prompts) if split.prompts else "なし(切り分けなし)"
    lines = [f"{label}: {side.name}  プロンプト: {prompt} ({split.prompt_source})"]
    for s in split.segments:
        mark = "*" if side.selected is not None and s.number == side.selected.number else " "
        lines.append(f"  {mark} {s.number}. {s.command if s.command is not None else '(コマンド不明)'}  {s.line_count} 行")
        if s.preamble:
            lines.append("      前置き: " + " | ".join(p.strip() for p in s.preamble))
    return lines


def _error_json(outcome: CompareOutcome) -> dict[str, Any] | None:
    e = outcome.error
    return {"code": e.code, "stage": e.stage, "message": e.message, "detail": _plain(e.detail)} if e else None


def _structured_error(outcome: CompareOutcome) -> str | None:
    d = outcome.error.detail if outcome.error else {}
    return f"{d['error_type']}: {d['error']}" if "error_type" in d else None


def to_json(outcome: CompareOutcome) -> str:
    """schema_version と status は必須。比較できたときは、既存のキー(mode〜show)の順序を保つため、その後ろに置く。"""
    return json.dumps(_payload(outcome), ensure_ascii=False, indent=2)


def _payload(outcome: CompareOutcome) -> dict[str, Any]:
    """1組の JSON の中身(to_json と、--all-segments の results の各要素で共通)。"""
    head: dict[str, Any] = {"schema_version": outcome.schema_version, "status": outcome.status}
    payload: dict[str, Any] = {}
    seg = outcome.segmentation
    if outcome.status == "needs_selection":
        payload = {**head}
        payload["reason"] = outcome.selection_reason
        payload["candidates"] = [{"side": c.side, **_segment_json(c.segment)} for c in outcome.candidates]
        if outcome.selection_reason == "ambiguous_format":
            payload["scores"] = outcome.selection_detail.get("scores", {})
    elif outcome.kind is None:  # 比較に入る前のエラー
        payload = {**head, "mode": outcome.mode}
    else:
        payload |= {"mode": outcome.mode, "kind": outcome.kind, "has_diff": outcome.has_diff}
        run_structured = outcome.mode in ("structured", "both")
        structured_json = None
        if run_structured and outcome.kind != "show":
            structured_json = _plain(outcome.structured) if outcome.structured else {"error": _structured_error(outcome)}
        payload["structured"] = structured_json
        payload["raw"] = _plain(outcome.raw) if outcome.raw else None
        if payload["raw"] is not None and payload["raw"].get("options", "") is None:
            # 既定の設定の単純比較では options のキーを書かない(今と同じ JSON。ADR-0166 決定12)
            del payload["raw"]["options"]
        if outcome.kind == "show":
            show = outcome.show
            payload["show"] = (
                {k: _plain(getattr(show, k)) for k in ("method", "key_field", "fallback_reason", "diffs")}
                if show
                else None
            )
        payload |= head
        payload["segmentation"] = {side: _split_json(seg[side]) for side, _ in SIDES}
        payload["default_exposure"] = (
            {s: {"command": e.command, "state": e.state} for s, e in outcome.default_exposure.items()}
            if outcome.default_exposure
            else None
        )
        payload["notices"] = [_plain(n) for n in outcome.notices]
        payload["exclusions"] = [_plain(e) for e in outcome.exclusions]
        if outcome.auto_platform is not None:
            payload["platform_used"] = outcome.auto_platform.used
    if seg and "segmentation" not in payload:
        payload["segmentation"] = {side: _split_json(seg[side]) for side, _ in SIDES}
    if outcome.error:
        payload["error"] = _error_json(outcome)
    # LLM 解析の注釈は常に最後に置く(比較まで進まなかった結果では null)
    payload["llm"] = _plain(outcome.llm)
    return payload


def to_json_all(outcome: MultiCompareOutcome) -> str:
    """--all-segments の JSON。results の各要素は1組の JSON と同じ形、unpaired の各要素は候補の要素と同じ形。"""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": outcome.status,
        "results": [_payload(p.outcome) for p in outcome.pairs],
        "unpaired": [{"side": u.side, **_segment_json(u.segment)} for u in outcome.unpaired],
    }
    if outcome.single_log:
        # 1つのログの中で組にしたとき(ADR-0152 決定2)だけ足す。unpaired は1回だけのコマンドとコマンド不明の区間
        payload["single_log"] = True
    return json.dumps(payload, ensure_ascii=False, indent=2)


def pair_label(index: int, pair: PairResult) -> str:
    """組の見出しの名前(「1. show version」)。標準エラーの文にも使う。"""
    return f"{index}. {pair.command if pair.command is not None else '(コマンド不明)'}"


def to_text_all(outcome: MultiCompareOutcome) -> str:
    """--all-segments の text。組ごとに見出しを付けて今の text を並べ、最後に相手なしの一覧を出す。"""
    out: list[str] = []
    for index, pair in enumerate(outcome.pairs, start=1):
        out.append(f"=== {pair_label(index, pair)} (変更前 区間 {pair.before_number} / 変更後 区間 {pair.after_number}) ===")
        if body := to_text(pair.outcome):
            out.append(body)
    if outcome.unpaired:
        out.append("=== 相手なし ===")
        labels = dict(SIDES)
        for u in outcome.unpaired:
            command = u.segment.command if u.segment.command is not None else "(コマンド不明)"
            out.append(f"{labels[u.side]} 区間 {u.segment.number}: {command}  {u.segment.line_count} 行")
    return "\n".join(out)


def to_text(outcome: CompareOutcome) -> str:
    """text 出力。エラーだけで本文がないときは空文字列。"""
    seg = outcome.segmentation
    if outcome.status == "needs_selection":
        if outcome.selection_reason == "ambiguous_format":
            return ""
        out = ["== segmentation =="]
        waiting = {c.side for c in outcome.candidates}
        for side, label in SIDES:
            if side in waiting:
                n = sum(1 for c in outcome.candidates if c.side == side)
                out.append(f"{label}は {n} 区間あります。選択してください:")
            out += _split_text(label, seg[side])
        return "\n".join(out)
    if outcome.kind is None or seg is None:
        return ""
    out: list[str] = []
    if outcome.auto_platform is not None:
        out.append(auto_platform_line(outcome.auto_platform.used))
    structured_section = outcome.show is None and outcome.mode in ("structured", "both")
    if outcome.show is not None:
        out += _show_text(outcome.show)
    elif structured_section:
        out += _structured_text(outcome.structured, _structured_error(outcome), outcome.warnings)
    if outcome.raw is not None:
        out += _raw_text(outcome.raw)
    if not structured_section:
        # 構造化の欄が無いとき(show・raw だけ)は、警告(ページ送りの印など。ADR-0134 決定2)をここに出す
        out += [f"警告: {warning_message(w)}" for w in outcome.warnings]
    out.append("== segmentation ==")
    for side, label in SIDES:
        out += _split_text(label, seg[side])
    exposure = outcome.default_exposure
    if exposure:
        states = {"normal": "通常版", "exposed": "既定値込み版", "unknown": "不明"}
        out.append("既定値込みの判定: " + " / ".join(f"{lb} {states[exposure[sd].state]}" for sd, lb in SIDES))
    out += [notice_line(n) for n in outcome.notices]
    out += [exclusion_line(e) for e in outcome.exclusions]
    if outcome.llm is not None and outcome.llm.reason != "disabled":
        out += llm_section_lines(_plain_llm(outcome.llm))
    return "\n".join(out)


def _plain_llm(llm: LlmAnalysis) -> LlmAnalysis:
    """text の出力用に、答えだけ <red>・</red> を外した写し(REQ-204 §5)。json と保存の結果は AI が返したまま。"""
    return llm if llm.answer is None else replace(llm, answer=plain_answer(llm.answer))
