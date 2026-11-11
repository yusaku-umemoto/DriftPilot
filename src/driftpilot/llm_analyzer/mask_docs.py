"""伏せる表から一覧(Markdown / JSON)を生成する(ADR-0019 決定2)。config_parser/pattern_docs.py と同じ運用。

出力は決定的にする(並びと改行を固定)。docs/notes/masking-patterns.md はこの出力そのもので、ずれたらテストが失敗する。
"""

import json
from typing import Any

from driftpilot import i18n
from driftpilot.llm_analyzer.mask_patterns import (
    MASK_PATTERNS,
    SECRET_WORDS,
    UNSENT_FORM_SCOPES,
    UNSENT_FORMS,
    MaskPattern,
    compiled,
    masking_fingerprint,
    shown_example,
    shown_note,
    shown_source,
)

GENERATE_COMMAND = "driftpilot mask-patterns --format markdown --output docs/notes/masking-patterns.md"

_HEADER_JA = f"""<!-- 自動生成。手で編集しない。 -->
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

# 英語の版(REQ-259 / ADR-0190 決定3)。docs/notes/masking-patterns.md は日本語の版の出力で、言語が en のときだけ英語の版を出す
_HEADER_EN = f"""<!-- Generated automatically. Do not edit by hand. -->
# Table of what is masked before sending to the LLM

> **Generated automatically. Do not edit by hand.** It is made from the masking table (`MASK_PATTERNS` in `src/driftpilot/llm_analyzer/mask_patterns.py`).
> Regenerate: `{GENERATE_COMMAND}`
> If the table is changed and this file is out of date, a test fails.

The table is consulted only for lines that contain a secret word (ADR-0019 decision 4, ADR-0143). A line without a secret word is sent as it is, without consulting the table.
A line is split into words by spaces, and it is regarded as containing a secret word when a word matches a secret word exactly.
A word with neither `<` nor `>` is also split into runs of letters, digits, `_` and `-`, and the line is regarded as containing a secret word when any run matches a secret word ignoring case (`secret=…`, `Password:`, `"secret"`, `SECRET`. ADR-0143 decision 1).
`-` and `_` are not separators (`send-community` and `client-secret-name` do not contain one). Full-width words are not looked at (ADR-0143 decision 4).
For a word with `<` or `>`, only the XML tag check is made. If the line starts with that tag (`<phash>…</phash>` and so on) or has the closing tag on the same line,
the tag name is looked at as a word (ADR-0140 decision 2, ADR-0143 decision 2). A line with an invisible character is not sent, before the gate (ADR-0142).
For a line that contains a secret word, the first row of the table that matches its form decides the result.

- `mask`: the masked positions (named groups) are sent as `<secret:N>`. The same value gets the same number, and different values get different numbers
- `pass`: the line contains a secret word but has nothing to mask. It is sent as it is
- A line that matches none: **not sent**. Only the count and the form of the start of the line are left in the result (decision 5)

The form is a regular expression for the whole line (with the indent removed and the words separated by a single space).
scope is the parent block: `top` means there is no parent line, and `*` means anywhere. Otherwise, one of the ancestor lines must start with that word.
There is no processing that masks something because its form looks like a key (ADR-0019 decision 1). A PEM block is not sent as a whole block, separately from the table.
A group of secret lines that were split by wrapping at the terminal width is also not sent as a whole block, like PEM (ADR-0137 decision 2).
A line of a fixed form without a secret word (see "Forms that are not sent, without a secret word" below) is decided as not sent before the gate (ADR-0137 decision 3).
"""
_HEADER = i18n.choose(_HEADER_JA, _HEADER_EN)

# 表の上の見出し・案内・列の名前(言語で選ぶ。日本語のときの出力は今と同じ)
_DOC_JA = {
    "fingerprint": "表の指紋: `{digest}`({count} 件。mask {mask} 件 / pass {pass_} 件)。"
    "LLM 解析の結果の `llm.masking_fingerprint` と突き合わせる(docs/notes/req-16-llm.md)。\n",
    "words_title": "## 秘密語(初期値)\n",
    "words_note": "設定(`llm.extra_secret_words`)で語を足せる。初期値の語は外せない。\n",
    "table_title": "## 表\n",
    "table_head": "| ID | scope | 書式 | 種別 | 伏せる位置 | 例 | 出どころ | 備考 |",
    "unsent_title": "## 秘密語の無い、送らない形\n",
    "unsent_note": "行(語を1つの空白で区切ったもの)全体に当たれば、表を引かずに送らない。送らなかった記録には ID だけを残す"
    "(例: `[interface] UNS-01 …`)。\n",
    "unsent_head": "| ID | scope | 書式 |",
}
_DOC_EN = {
    "fingerprint": "Table fingerprint: `{digest}` ({count} rows. mask {mask} / pass {pass_}). "
    "Compare it with `llm.masking_fingerprint` in the LLM analysis result (docs/notes/req-16-llm.md).\n",
    "words_title": "## Secret words (initial values)\n",
    "words_note": "Words can be added in the settings (`llm.extra_secret_words`). The initial words cannot be removed.\n",
    "table_title": "## Table\n",
    "table_head": "| ID | scope | Form | Kind | Masked positions | Example | Source | Note |",
    "unsent_title": "## Forms that are not sent, without a secret word\n",
    "unsent_note": "A line (words separated by a single space) that matches as a whole is not sent, without consulting the table. "
    "Only the ID is left in the record of what was not sent (example: `[interface] UNS-01 …`).\n",
    "unsent_head": "| ID | scope | Form |",
}
_DOC = i18n.choose(_DOC_JA, _DOC_EN)


def _record(p: MaskPattern) -> dict[str, Any]:
    return {
        "id": p.id,
        "scope": p.scope,
        "match": p.match,
        "kind": p.kind,
        "groups": sorted(compiled(p.match).groupindex),
        "example": shown_example(p),
        "source": shown_source(p),
        "note": shown_note(p),
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
        _DOC["fingerprint"].format(digest=fp.digest, count=fp.count, mask=kinds["mask"], pass_=kinds["pass"]),
        _DOC["words_title"],
        " ".join(f"`{w}`" for w in SECRET_WORDS) + "\n",
        _DOC["words_note"],
        _DOC["table_title"],
        _DOC["table_head"],
        "|---|---|---|---|---|---|---|---|",
    ]
    for p in patterns:
        groups = ", ".join(sorted(compiled(p.match).groupindex)) or "-"
        out.append(
            f"| `{p.id}` | {_cell(p.scope)} | {_code(p.match)} | {p.kind} | {_cell(groups)} | {_code(shown_example(p))} "
            f"| {_cell(shown_source(p))} | {_cell(shown_note(p))} |"
        )
    out += [
        "",
        _DOC["unsent_title"],
        _DOC["unsent_note"],
        _DOC["unsent_head"],
        "|---|---|---|",
    ]
    for form_id, regex in UNSENT_FORMS:
        out.append(f"| `{form_id}` | {_cell(UNSENT_FORM_SCOPES.get(form_id, '*'))} | {_code(regex)} |")
    return "\n".join(out).rstrip("\n") + "\n"
