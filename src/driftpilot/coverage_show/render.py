"""show 側の被覆率レポートの出力(text / json)。config 側のレポート(coverage_report)とは混ぜない。"""

import json
from typing import Any

from driftpilot.coverage_show.commands import SHOW_COMMANDS
from driftpilot.coverage_show.measure import HEAD_LINES, ShowCoverageReport

LEAD = (
    "config 側の被覆率(driftpilot coverage)とは別の指標。"
    "テンプレート保有率が 0% でも比較は止まらない(raw 比較に落ちる)"
)
UNMEASURABLE = "測定不能(--platform が指定されていない。機種は推測しない)"
PLATFORM_SUSPECT = "特定できた区間がすべてテンプレートなし。platform 名を確認してください"

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
        f"被覆率レポート(show)  platform: {report.platform or '指定なし'}",
        f"※ {LEAD}",
        "",
        f"入力: {report.files} ファイル、{len(report.segments)} 区間"
        f"(うち config と判定して分母から外した区間 {report.config_excluded})",
    ]
    out += [f"  読めなかったファイル: {p}  {e}" for p, e in report.errors]

    out += [
        "",
        "-- 特定率(show の区間のうち、取得コマンドが取れた区間) --",
        f"  {_pct(report.identification_ratio):>6}  {len(identified)}/{len(show)}",
        "",
        "-- テンプレート保有率(特定できた区間のうち、ntc-templates にテンプレートがある区間) --",
    ]
    if report.template_ratio is None:
        out.append(f"  {UNMEASURABLE}")
    else:
        out.append(
            f"  {_pct(report.template_ratio):>6}  {report.with_template}/{len(identified)}"
            f"  (構造化できる {report.structured}、テンプレートはあるが行を抽出できない {report.extract_failed})"
        )
    out += [f"  注: {note}" for note in _notes(report)]

    out += ["", f"-- 特定できなかった区間(先頭{HEAD_LINES}行まで。全文は出さない) --"]
    for s in report.unidentified:
        out.append(f"  {s.source}  区間 {s.number}")
        out += [f"    | {line}" for line in s.head]
    if not report.unidentified:
        out.append("  なし")

    out += ["", "-- テンプレートが無いコマンド(raw 比較に落ちる。多い順) --"]
    if not report.measurable:
        out.append(f"  {UNMEASURABLE}")
    else:
        out += [f"  {n:>7}  {c}" for c, n in report.no_template] or ["  なし"]
        out += ["", "-- テンプレートはあるが行を抽出できなかったコマンド(raw 比較に落ちる。多い順) --"]
        out += [f"  {n:>7}  {c}" for c, n in report.failed_extraction] or ["  なし"]

    out += ["", f"-- 分母の {len(SHOW_COMMANDS)} コマンド別(出現区間数 / テンプレートあり) --"]
    for cid, appeared, with_template in report.per_command:
        template = "-" if with_template is None else str(with_template)
        out.append(f"  {cid:<34}{appeared:>6} / {template:<6}{_COMMANDS[cid]}")
    out.append(f"  一覧外のコマンド: {report.outside_list} 区間(保有率の分母には含む)")

    out += ["", "-- 入力に1件も現れなかったコマンド(測れていない) --"]
    out += [f"  {cid:<34}{_COMMANDS[cid]}" for cid in report.unseen] or ["  なし"]
    return "\n".join(out) + "\n"
