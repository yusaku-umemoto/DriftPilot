"""パターン表から対応一覧(Markdown / JSON)を生成する(ADR-0011)。

出力は決定的にする(並びと改行を固定)。docs/notes/parser-patterns.md はこの出力そのもので、
ずれたらテストが失敗する。
"""

import json
from typing import Any

from driftpilot.config_parser.patterns import Pattern
from driftpilot.config_parser.registry import ALL_PATTERNS, CATEGORIES, patterns_fingerprint

GENERATE_COMMAND = "driftpilot patterns --format markdown --output docs/notes/parser-patterns.md"

_HEADER = f"""<!-- 自動生成。手で編集しない。 -->
# パーサー対応一覧(config)

> **自動生成。手で編集しない。** パターン表(`src/driftpilot/config_parser/` の各 extractor の `PATTERNS`)から作る。
> 再生成: `{GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

ここに載っていない書式は読まない(未解釈になる)。各表の並びが優先順位で、最初に書式が当たった行が結果を決める。
書式は行全体に対する正規表現。scope は、その行が現れる場所(親の行の種類)で、`top` はトップレベル。
「手続き」と書いたものは、正規表現だけでは決まらず、備考の手続きが働く。
"""


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
        f"パターン表の指紋: `{fp.digest}`({fp.count} 件)。"
        "保存した比較結果の `tools.patterns_fingerprint` と突き合わせる(docs/notes/req-14-storage.md)。\n",
    ]
    for category in CATEGORIES:
        rows = [p for p in patterns if p.category == category]
        if not rows:
            continue
        out.append(f"## {category}\n")
        out.append("| ID | scope | 書式 | 写し先 | 例 | 備考 |")
        out.append("|---|---|---|---|---|---|")
        for p in rows:
            match = _code(p.match) if p.match is not None else "(行は読まない)"
            out.append(
                f"| `{p.id}` | {_cell(p.scope)} | {match} | {_cell(p.target)} | {_code(p.example)} | {_cell(p.note)} |"
            )
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
