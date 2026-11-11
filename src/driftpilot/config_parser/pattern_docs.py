"""パターン表から対応一覧(Markdown / JSON)を生成する(ADR-0011)。

出力は決定的にする(並びと改行を固定)。docs/notes/parser-patterns.md はこの出力そのもので、
ずれたらテストが失敗する。
"""

import json
from typing import Any

from driftpilot import i18n
from driftpilot.config_parser.patterns import Pattern
from driftpilot.config_parser.registry import ALL_PATTERNS, CATEGORIES, patterns_fingerprint

GENERATE_COMMAND = "driftpilot patterns --format markdown --output docs/notes/parser-patterns.md"

_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(config)

> **自動生成。手で編集しない。** パターン表(`src/driftpilot/config_parser/` の各 extractor の `PATTERNS`)から作る。
> 再生成: `{GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

ここに載っていない書式は読まない(未解釈になる)。各表の並びが優先順位で、最初に書式が当たった行が結果を決める。
書式は行全体に対する正規表現。scope は、その行が現れる場所(親の行の種類)で、`top` はトップレベル。
「手続き」と書いたものは、正規表現だけでは決まらず、備考の手続きが働く。
"""

# 英語の版(REQ-259 / ADR-0190 決定3)。見出し・案内・列の名前だけを英語にする。各行の備考(note)・写し先(target)は日本語のまま
# (写し先は表の指紋の計算に入っているので、言語で変えない)。docs/notes の生成文書は日本語の版の出力
_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Parser coverage list (config)

> **Generated automatically. Do not edit by hand.** It is made from the pattern tables (`PATTERNS` of each extractor under `src/driftpilot/config_parser/`).
> Regenerate: `{GENERATE_COMMAND}`
> If the tables are changed and this file is out of date, a test fails.

Forms that are not listed here are not read (they become uninterpreted). The order of each table is the priority, and the first row whose form matches a line decides the result.
The form is a regular expression for the whole line. scope is the place where the line appears (the kind of parent line), and `top` is the top level.
Rows marked "procedure" are not decided by the regular expression alone; the procedure in the note works.
The notes and the targets in the table rows are developer notes and are shown in Japanese.
"""
_HEADER = i18n.choose(_HEADER_JA, _HEADER_EN)

# 見出し・列の名前(日本語のときの出力は今と同じ。flat_docs も使う)
_DOC_JA = {
    "fingerprint": "パターン表の指紋: `{digest}`({count} 件)。"
    "保存した比較結果の `tools.patterns_fingerprint` と突き合わせる(docs/notes/req-14-storage.md)。\n",
    "table_head": "| ID | scope | 書式 | 写し先 | 例 | 備考 |",
    "no_line": "(行は読まない)",
}
_DOC_EN = {
    "fingerprint": "Pattern table fingerprint: `{digest}` ({count} rows). "
    "Compare it with `tools.patterns_fingerprint` of a saved comparison result (docs/notes/req-14-storage.md).\n",
    "table_head": "| ID | scope | Form | Target | Example | Note |",
    "no_line": "(the line is not read)",
}
_DOC = i18n.choose(_DOC_JA, _DOC_EN)


def _record(pattern: Pattern) -> dict[str, Any]:
    return {
        "id": pattern.id,
        "category": pattern.category,
        "scope": pattern.scope,
        "match": pattern.match,
        "target": pattern.target,
        "example": pattern.example,
        "note": pattern.note,
    }


def render_json(patterns: tuple[Pattern, ...] = ALL_PATTERNS) -> str:
    fp = patterns_fingerprint(patterns)
    payload = {"fingerprint": fp.digest, "count": fp.count, "patterns": [_record(p) for p in patterns]}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _code(text: str) -> str:
    return f"`{_cell(text)}`" if text else ""


def render_markdown(patterns: tuple[Pattern, ...] = ALL_PATTERNS) -> str:
    fp = patterns_fingerprint(patterns)
    out = [
        _HEADER,
        _DOC["fingerprint"].format(digest=fp.digest, count=fp.count),
    ]
    for category in CATEGORIES:
        rows = [p for p in patterns if p.category == category]
        if not rows:
            continue
        out.append(f"## {category}\n")
        out.append(_DOC["table_head"])
        out.append("|---|---|---|---|---|---|")
        for p in rows:
            match = _code(p.match) if p.match is not None else _DOC["no_line"]
            out.append(
                f"| `{p.id}` | {_cell(p.scope)} | {match} | {_cell(p.target)} | {_code(p.example)} | {_cell(p.note)} |"
            )
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
