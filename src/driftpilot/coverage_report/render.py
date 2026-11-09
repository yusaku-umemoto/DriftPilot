"""被覆率レポートの出力(text / json)。主はファイル単位の数字で、合計は参考値として後ろに出す。

B型(platform あり)のときだけ、json に platform とファイルごとの duplicates を足す。A型の出力は変えない。
fortinet のときだけ、専用に読めた割合(text は参考値の次の1行、json は fortinet_dedicated)と、
汎用の形の行が多いブロックの上位(json の fortinet_generic_blocks だけ)を足す(REQ-190、ADR-0139 決定1)。
"""

import json
from typing import Any

from driftpilot.config_parser.fortinet_tree import PLATFORM as FORTINET
from driftpilot.config_parser.platforms import config_patterns
from driftpilot.coverage_report.measure import (
    OUT_OF_SCOPE_RATIO,
    CoverageReport,
    FileResult,
    Group,
)

DEFAULT_TOP = 30
DEFAULT_FILES = 50

EXPOSURE_LABELS = {"normal": "通常版", "exposed": "既定値込み版", "unknown": "判定不能"}


def _pct(part: int, whole: int) -> str:
    return f"{part / whole:.1%}" if whole else "-"


def _top_shapes(group: Group, top: int) -> tuple[list[tuple[str, str, int]], int, int]:
    """上位 top 件の (scope, 形, 件数) と、残り(その他)の行数・形の種類数を返す。"""
    ranked = sorted(group.shapes.items(), key=lambda kv: (-kv[1], kv[0]))
    head = [(scope, shape, count) for (scope, shape), count in ranked[:top]]
    rest = ranked[top:]
    return head, sum(count for _, count in rest), len(rest)


def _fgt_ratio(group: Group) -> float:
    """FortiGate の専用に読めた割合。分母(既定の表を除いた行)が 0 なら 0.0。"""
    return group.fgt_dedicated / group.fgt_base if group.fgt_base else 0.0


def _fgt_text_line(group: Group) -> str:
    return (
        f"  FortiGate の専用に読めた割合(既定の表を除く): {group.fgt_dedicated} / {group.fgt_base} 行"
        f" ({_fgt_ratio(group):.1%})"
    )


def _fgt_generic_blocks(group: Group, top: int) -> list[dict[str, Any]]:
    """汎用の形の行が多いブロックの上位 top 件。同じ数なら名前の順。既定の表のブロックは数えた時点で除いてある。"""
    ranked = sorted(group.fgt_generic_blocks.items(), key=lambda kv: (-kv[1], kv[0]))
    return [{"block": name, "lines": count} for name, count in ranked[:top]]


def _file_record(f: FileResult, platform: str | None) -> dict[str, Any]:
    record = {
        "path": f.path,
        "error": f.error,
        "total": f.total,
        "interpreted": f.interpreted,
        "ratio": None if f.error else round(f.ratio, 4),
        "possibly_out_of_scope": f.possibly_out_of_scope,
    }
    if platform is not None:
        record["duplicates"] = f.duplicates
    return record


def to_json(report: CoverageReport, top: int = DEFAULT_TOP) -> str:
    groups: dict[str, Any] = {}
    patterns = config_patterns(report.platform)
    for exposure, group in report.groups.items():
        if not group.files:
            continue
        head, rest_lines, rest_kinds = _top_shapes(group, top)
        hits = group.hits
        groups[exposure] = {
            "files": [_file_record(f, report.platform) for f in sorted(group.files, key=lambda f: (f.error is not None, f.ratio, f.path))],
            "reference_total": {
                "note": "ファイル群の合計(参考値)。機種が混ざるため、次に実装するものの判断には使わない",
                "total": group.total,
                "interpreted": group.interpreted,
                "ratio": round(group.ratio, 4) if group.total else None,
            },
            "categories": dict(sorted(group.categories.items())),
            "patterns": {p.id: hits[p.id] for p in patterns if p.match is not None},
            "unhit_patterns": group.unhit_patterns,
            "unparsed_shapes": [{"scope": s, "shape": sh, "count": c} for s, sh, c in head],
            "unparsed_other": {"lines": rest_lines, "shapes": rest_kinds},
        }
        if report.platform == FORTINET:
            groups[exposure]["fortinet_dedicated"] = {
                "dedicated": group.fgt_dedicated,
                "total": group.fgt_base,
                "ratio": round(_fgt_ratio(group), 4),
            }
            groups[exposure]["fortinet_generic_blocks"] = _fgt_generic_blocks(group, top)
    payload: dict[str, Any] = {"fold_version": report.fold_version}
    if report.platform is not None:
        payload["platform"] = report.platform
    payload["groups"] = groups
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def to_text(report: CoverageReport, top: int = DEFAULT_TOP, files: int = DEFAULT_FILES) -> str:
    out = [f"被覆率レポート(config パーサー)  畳み込み規則: {report.fold_version}"]
    if report.platform is not None:
        kind = "FortiGate" if report.platform == FORTINET else "平坦 config"
        out[0] += f"  パーサー: {report.platform}({kind})"
    patterns = config_patterns(report.platform)
    for exposure, group in report.groups.items():
        if not group.files:
            continue
        failed = group.failed
        out += [
            "",
            f"== {EXPOSURE_LABELS[exposure]} ({exposure}): {len(group.files)} ファイル"
            + (f"(うち解析失敗 {len(failed)})" if failed else "")
            + " ==",
            "",
            "-- ファイル単位(主)。解釈済み率の低い順 --",
        ]
        rows = sorted(group.measured, key=lambda f: (f.ratio, f.path))
        for f in rows[:files]:
            mark = "  ※パーサー対象外の可能性" if f.possibly_out_of_scope else ""
            dup = f"  重複 {f.duplicates}" if report.platform is not None else ""
            out.append(f"  {_pct(f.interpreted, f.total):>6}  {f.interpreted}/{f.total}  {f.path}{dup}{mark}")
        if len(rows) > files:
            out.append(f"  ... 残り {len(rows) - files} ファイル(--files で件数を変える。json は全件)")
        for f in failed[:files]:
            out.append(f"     失敗  {f.path}  {f.error}")
        if len(failed) > files:
            out.append(f"  ... 解析失敗の残り {len(failed) - files} ファイル")
        parser = report.platform or "Cisco IOS"
        out.append(f"  (※: 解釈済み率 {OUT_OF_SCOPE_RATIO:.0%} 未満。このパーサーは {parser} 専用)")

        out += [
            "",
            "-- 参考値: ファイル群の合計(機種が混ざるため、この数字で次の実装を決めない) --",
            f"  解釈済み {group.interpreted} / 全 {group.total} 行 ({_pct(group.interpreted, group.total)})",
        ]
        if report.platform == FORTINET:
            out.append(_fgt_text_line(group))
        out += [
            "",
            "-- カテゴリ別(解釈済み行数) --",
        ]
        categories = group.categories
        out += [f"  {name:<14}{count}" for name, count in sorted(categories.items())] or ["  なし"]
        out.append(f"  {'未解釈':<12}{group.total - group.interpreted}")

        hits = group.hits
        out += ["", "-- パターン別ヒット数(!! は一度も当たっていないパターン) --"]
        for p in patterns:
            if p.match is None:
                continue
            count = hits[p.id]
            out.append(f"  {'!!' if count == 0 else '  '} {p.id:<46}{count}")

        head, rest_lines, rest_kinds = _top_shapes(group, top)
        out += ["", f"-- 未解釈行の形 上位{top} (規則 {report.fold_version}。scope は親の粗いラベル) --"]
        out += [f"  {count:>7}  [{scope}] {shape}" for scope, shape, count in head] or ["  なし"]
        if rest_kinds:
            out.append(f"  その他: {rest_lines} 行({rest_kinds} 種)")
    return "\n".join(out) + "\n"
