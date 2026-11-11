"""show 側の被覆率レポートの出力(text / json)。config 側のレポート(coverage_report)とは混ぜない。"""

import json
from typing import Any

from driftpilot import i18n
from driftpilot.coverage_show.commands import SHOW_COMMANDS
from driftpilot.coverage_show.measure import HEAD_LINES, ShowCoverageReport

LEAD = (
    "config 側の被覆率(driftpilot coverage)とは別の指標。"
    "テンプレート保有率が 0% でも比較は止まらない(raw 比較に落ちる)"
)
UNMEASURABLE = "測定不能(--platform が指定されていない。機種は推測しない)"
PLATFORM_SUSPECT = "特定できた区間がすべてテンプレートなし。platform 名を確認してください"

# 見出しと表の列の名前(開発者向けの出力なので、説明の文は日本語のまま残す。REQ-258 §3)。
# 言語に合わせて読み込むときに 1 回だけ選ぶ(messages は読まない決まりのため、ここに持つ)
_TEXTS_JA = {
    "title": "被覆率レポート(show)  platform: {platform}",
    "not_specified": "指定なし",
    "identification_heading": "-- 特定率(show の区間のうち、取得コマンドが取れた区間) --",
    "template_heading": "-- テンプレート保有率(特定できた区間のうち、ntc-templates にテンプレートがある区間) --",
    "unidentified_heading": "-- 特定できなかった区間(先頭{head_lines}行まで。全文は出さない) --",
    "segment": "区間 {number}",
    "none": "  なし",
    "no_template_heading": "-- テンプレートが無いコマンド(raw 比較に落ちる。多い順) --",
    "failed_extraction_heading": "-- テンプレートはあるが行を抽出できなかったコマンド(raw 比較に落ちる。多い順) --",
    "per_command_heading": "-- 分母の {count} コマンド別(出現区間数 / テンプレートあり) --",
    "unseen_heading": "-- 入力に1件も現れなかったコマンド(測れていない) --",
}
_TEXTS_EN = {
    "title": "Coverage report (show)  platform: {platform}",
    "not_specified": "not specified",
    "identification_heading": "-- Identification rate (show segments whose command could be identified) --",
    "template_heading": "-- Template coverage (identified segments that have an ntc-templates template) --",
    "unidentified_heading": "-- Segments that could not be identified (first {head_lines} lines only; the full text is not shown) --",
    "segment": "segment {number}",
    "none": "  none",
    "no_template_heading": "-- Commands without a template (fall back to raw comparison; most frequent first) --",
    "failed_extraction_heading": "-- Commands with a template but no rows extracted (fall back to raw comparison; most frequent first) --",
    "per_command_heading": "-- By each of the {count} commands in the denominator (segments seen / with template) --",
    "unseen_heading": "-- Commands that never appeared in the input (not measured) --",
}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

_COMMANDS = {c.id: c.command for c in SHOW_COMMANDS}


def _pct(ratio: float) -> str:
    return f"{ratio:.1%}"


def _notes(report: ShowCoverageReport) -> list[str]:
    return [PLATFORM_SUSPECT] if report.platform_suspect else []


def to_json(report: ShowCoverageReport) -> str:
    identified = report.identified
    payload: dict[str, Any] = {
        "kind": "show",
        "note": LEAD,
        "platform": report.platform,
        "inputs": {
            "files": report.files,
            "segments": len(report.segments),
            "config_excluded": report.config_excluded,
            "errors": [{"path": p, "error": e} for p, e in report.errors],
        },
        "identification": {
            "numerator": len(identified),
            "denominator": len(report.show_segments),
            "ratio": round(report.identification_ratio, 4),
        },
        "template": {
            "measurable": report.measurable,
            "numerator": report.with_template,
            "denominator": len(identified) if report.measurable else None,
            "ratio": None if report.template_ratio is None else round(report.template_ratio, 4),
            "structured": report.structured,
            "extract_failed": report.extract_failed,
        },
        "unidentified": [{"source": s.source, "segment": s.number, "head": list(s.head)} for s in report.unidentified],
        "no_template": [{"command": c, "count": n} for c, n in report.no_template],
        "extract_failed": [{"command": c, "count": n} for c, n in report.failed_extraction],
        "commands": [
            {"id": cid, "command": _COMMANDS[cid], "appeared": appeared, "with_template": with_template}
            for cid, appeared, with_template in report.per_command
        ],
        "unseen": report.unseen,
        "outside_list": report.outside_list,
        "notes": _notes(report),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def to_text(report: ShowCoverageReport) -> str:
    identified = report.identified
    show = report.show_segments
    out = [
        _T["title"].format(platform=report.platform or _T["not_specified"]),
        f"※ {LEAD}",
        "",
        f"入力: {report.files} ファイル、{len(report.segments)} 区間"
        f"(うち config と判定して分母から外した区間 {report.config_excluded})",
    ]
    out += [f"  読めなかったファイル: {p}  {e}" for p, e in report.errors]

    out += [
        "",
        _T["identification_heading"],
        f"  {_pct(report.identification_ratio):>6}  {len(identified)}/{len(show)}",
        "",
        _T["template_heading"],
    ]
    if report.template_ratio is None:
        out.append(f"  {UNMEASURABLE}")
    else:
        out.append(
            f"  {_pct(report.template_ratio):>6}  {report.with_template}/{len(identified)}"
            f"  (構造化できる {report.structured}、テンプレートはあるが行を抽出できない {report.extract_failed})"
        )
    out += [f"  注: {note}" for note in _notes(report)]

    out += ["", _T["unidentified_heading"].format(head_lines=HEAD_LINES)]
    for s in report.unidentified:
        out.append(f"  {s.source}  {_T['segment'].format(number=s.number)}")
        out += [f"    | {line}" for line in s.head]
    if not report.unidentified:
        out.append(_T["none"])

    out += ["", _T["no_template_heading"]]
    if not report.measurable:
        out.append(f"  {UNMEASURABLE}")
    else:
        out += [f"  {n:>7}  {c}" for c, n in report.no_template] or [_T["none"]]
        out += ["", _T["failed_extraction_heading"]]
        out += [f"  {n:>7}  {c}" for c, n in report.failed_extraction] or [_T["none"]]

    out += ["", _T["per_command_heading"].format(count=len(SHOW_COMMANDS))]
    for cid, appeared, with_template in report.per_command:
        template = "-" if with_template is None else str(with_template)
        out.append(f"  {cid:<34}{appeared:>6} / {template:<6}{_COMMANDS[cid]}")
    out.append(f"  一覧外のコマンド: {report.outside_list} 区間(保有率の分母には含む)")

    out += ["", _T["unseen_heading"]]
    out += [f"  {cid:<34}{_COMMANDS[cid]}" for cid in report.unseen] or [_T["none"]]
    return "\n".join(out) + "\n"
