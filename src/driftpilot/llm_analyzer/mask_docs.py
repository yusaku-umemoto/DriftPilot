"""伏せる表から一覧(Markdown / JSON)を生成する(ADR-0019 決定2)。config_parser/pattern_docs.py と同じ運用。

出力は決定的にする(並びと改行を固定)。docs/notes/masking-patterns.md はこの出力そのもので、ずれたらテストが失敗する。
"""

import json
from typing import Any

from driftpilot.llm_analyzer.mask_patterns import (
    MASK_PATTERNS,
    SECRET_WORDS,
    UNSENT_FORM_SCOPES,
    UNSENT_FORMS,
    MaskPattern,
    compiled,
    masking_fingerprint,
)

GENERATE_COMMAND = "driftpilot mask-patterns --format markdown --output docs/notes/masking-patterns.md"

_HEADER = f"""<!-- 自動生成。手で編集しない。 -->
# LLM へ送る前に伏せる表

> **自動生成。手で編集しない。** 伏せる表(`src/driftpilot/llm_analyzer/mask_patterns.py` の `MASK_PATTERNS`)から作る。
> 再生成: `{GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

表を引くのは、秘密語を含む行だけである(ADR-0019 決定4、ADR-0143)。秘密語を含まない行は、表を引かずにそのまま送る。
行を空白で語に分け、語が秘密語と完全一致すれば含むとみなす。`<` も `>` も無い語は、英数字・`_`・`-` のかたまりに分け、
どれかが秘密語と大文字・小文字を区別せずに一致しても含むとみなす(`secret=…`・`Password:`・`"secret"`・`SECRET`。ADR-0143 決定1)。
`-` と `_` は区切りにしない(`send-community`・`client-secret-name` は含まない)。全角の語は見ない(ADR-0143 決定4)。
`<` か `>` のある語は XML のタグの判定だけを見る。行がそのタグ(`<phash>…</phash>` など)で始まるか同じ行に閉じるタグがあれば、
タグの名前を語として見る(ADR-0140 決定2、ADR-0143 決定2)。見えない文字のある行は、ゲートより前に送らない(ADR-0142)。
秘密語を含む行は、表の上から試して最初に書式が当たった行が結果を決める。

- `mask`: 伏せる位置(名前付きグループ)を `<secret:N>` にして送る。同じ値は同じ番号、違う値は違う番号
- `pass`: 秘密語を含むが、伏せる対象が無い。そのまま送る
- どれにも当たらない行: **送らない**。件数と行頭の形だけを結果に残す(決定5)

書式は行(字下げを除き、語を1つの空白で区切ったもの)全体に対する正規表現。
scope は親ブロックで、`top` は親の行が無いこと、`*` はどこでもよいこと。それ以外は、祖先の行のどれかがその語で始まること。
形が「鍵らしい」ことを根拠に伏せる処理は無い(ADR-0019 決定1)。PEM ブロックは表とは別に、ブロックごと送らない。
端末の幅で折り返されて割れた秘密の行のかたまりも、PEM と同じにブロックごと送らない(ADR-0137 決定2)。
秘密語の無い決まった形の行(下の「秘密語の無い、送らない形」)は、ゲートの前で送らないと決める(ADR-0137 決定3)。
"""


def _record(p: MaskPattern) -> dict[str, Any]:
    return {
        "id": p.id,
        "scope": p.scope,
        "match": p.match,
        "kind": p.kind,
        "groups": sorted(compiled(p.match).groupindex),
        "example": p.example,
        "source": p.source,
        "note": p.note,
    }


def render_json(patterns: tuple[MaskPattern, ...] = MASK_PATTERNS) -> str:
    fp = masking_fingerprint(patterns)
    payload = {
        "fingerprint": fp.digest,
        "count": fp.count,
        "secret_words": list(SECRET_WORDS),
        "patterns": [_record(p) for p in patterns],
        "unsent_forms": [
            {"id": form_id, "match": regex, "scope": UNSENT_FORM_SCOPES.get(form_id, "*")} for form_id, regex in UNSENT_FORMS
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _code(text: str) -> str:
    return f"`{_cell(text)}`" if text else ""


def render_markdown(patterns: tuple[MaskPattern, ...] = MASK_PATTERNS) -> str:
    fp = masking_fingerprint(patterns)
    kinds = {k: sum(1 for p in patterns if p.kind == k) for k in ("mask", "pass")}
    out = [
        _HEADER,
        f"表の指紋: `{fp.digest}`({fp.count} 件。mask {kinds['mask']} 件 / pass {kinds['pass']} 件)。"
        "LLM 解析の結果の `llm.masking_fingerprint` と突き合わせる(docs/notes/req-16-llm.md)。\n",
        "## 秘密語(初期値)\n",
        " ".join(f"`{w}`" for w in SECRET_WORDS) + "\n",
        "設定(`llm.extra_secret_words`)で語を足せる。初期値の語は外せない。\n",
        "## 表\n",
        "| ID | scope | 書式 | 種別 | 伏せる位置 | 例 | 出どころ | 備考 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in patterns:
        groups = ", ".join(sorted(compiled(p.match).groupindex)) or "-"
        out.append(
            f"| `{p.id}` | {_cell(p.scope)} | {_code(p.match)} | {p.kind} | {_cell(groups)} | {_code(p.example)} "
            f"| {_cell(p.source)} | {_cell(p.note)} |"
        )
    out += [
        "",
        "## 秘密語の無い、送らない形\n",
        "行(語を1つの空白で区切ったもの)全体に当たれば、表を引かずに送らない。送らなかった記録には ID だけを残す"
        "(例: `[interface] UNS-01 …`)。\n",
        "| ID | scope | 書式 |",
        "|---|---|---|",
    ]
    for form_id, regex in UNSENT_FORMS:
        out.append(f"| `{form_id}` | {_cell(UNSENT_FORM_SCOPES.get(form_id, '*'))} | {_code(regex)} |")
    return "\n".join(out).rstrip("\n") + "\n"
