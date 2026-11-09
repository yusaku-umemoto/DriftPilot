"""人に渡す形の出力(HTML と CSV)を文字にする(ADR-0156 決定1・3〜6)。標準の html・csv だけを使う。

ここは文字にするだけで、伏せる判定はしない。差分の行は llm_analyzer/redact.py で伏せた RedactedResult を受け取る
(組の名前・ファイル名も伏せた後の文字を受け取る)。Report を作るのは画面の論理(screen_model)。CLI からも呼べる形にしておく
(ADR-0156 決定8。今は CLI に足さない)。

HTML(決定4・5):
- ★ 文字を出すのは _e(html.escape(…, quote=True))の 1 か所だけ。タグと属性は決まった文字だけを書く
- `<head>` に charset と CSP(default-src 'none'; style-src 'unsafe-inline')。外部の読み込み・スクリプト・画像は出さない。
  リンクはページ内の `href="#pair-N"` だけ(N は枠の番号。名前は id にもリンクにも入れない。ADR-0180 決定3)
- AI の答えは answer_format.format_answer のブロックから作る(画面と同じ意味のタグ)。[文](URL) や <img …> は文字のまま
- 変更の行は、違う文字を <mark class="del">・<mark class="ins"> で囲む(REQ-226 / ADR-0165)。範囲は inline_marks が
  伏せた後の文字で求める。中の文字も _e を通す
- 見た目(ADR-0180 / REQ-243): 題の帯・まとめのカード・組の一覧・組ごとの details の枠(1 組の比較は枠なし)。
  CSS は固定の文字だけ(値を CSS や style の属性に入れない)。class は決まった表からだけ選ぶ

CSV(決定6):
- UTF-8 の BOM(utf-8-sig)、QUOTE_ALL、改行は \\r\\n
- ★ 文字の欄は全部 _cell を通す(式の実行を防ぐ。先頭の空白を除いた最初の文字が式の頭なら `'` を前に付ける)。
  見出しの行も通す。行番号と件数は数のまま
"""

import csv
import html
import io
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import driftpilot.inline_marks as inline_marks
from driftpilot.llm_analyzer.answer_format import format_answer
from driftpilot.llm_analyzer.redact import OMITTED, RedactedRawRow, RedactedResult
from driftpilot.messages import (
    BATCH_COL_LLM,
    BATCH_COL_RAW,
    BATCH_COL_STATUS,
    BATCH_COL_STRUCTURED,
    BATCH_COL_UNPARSED,
    COMPARE_COL_AFTER,
    COMPARE_COL_AFTER_NO,
    COMPARE_COL_BEFORE,
    COMPARE_COL_BEFORE_NO,
    COMPARE_COL_CATEGORY,
    COMPARE_COL_ITEM,
    COMPARE_COL_OP,
    COMPARE_COL_TARGET,
    EXPORT_ANSWER,
    EXPORT_COL_AFTER_LINE,
    EXPORT_COL_BEFORE_LINE,
    EXPORT_COL_GROUP,
    EXPORT_COL_KIND,
    EXPORT_COL_PAIR,
    EXPORT_COUNTS,
    EXPORT_FIRST_REQUEST,
    EXPORT_GAP,
    EXPORT_GUIDE,
    EXPORT_KIND_RAW,
    EXPORT_KIND_STRUCTURED,
    EXPORT_NO_ROWS,
    EXPORT_PAIRS,
    EXPORT_RAW,
    EXPORT_SAVED_AT,
    EXPORT_STATUS,
    EXPORT_STRUCTURED,
    FOLLOW_UP_ANSWER_LABEL,
    FOLLOW_UP_QUESTION_LABEL,
    NO_COUNT,
    OP_ADDED,
    OP_CHANGED,
    OP_REMOVED,
    STATUS_LABEL,
    export_omitted,
)

# CSV で式の頭とみなす文字(ADR-0156 決定6)
_FORMULA_HEADS = ("=", "+", "-", "@", "\t", "\r", "\n", "＝", "＋", "－", "＠")
# HTML の中に書く CSS(ADR-0180 決定1〜3。見本 docs/requests/assets/REQ-243/report-mock.html を元にする)。
# ★ 固定の文字だけ。外部の読み込み・画像・値は入れない。色は変数でライトとダーク。地の色は題の線・h2 の印・リンクだけ
_STYLE = """
:root {
  color-scheme: light dark;
  --text: #1f1f1f; --muted: #666666; --bg: #f6f7f6; --card: #ffffff; --surface: #f3f3f3;
  --border: #d9dcd9; --line: #c9c9c9; --brand: #3f7d5c; --brand-soft: #e3efe8;
  --add-bg: #e5f5e8; --add-bar: #2e7d32; --del-bg: #fde7e9; --del-bar: #c42b1c;
  --chg-bg: #fff4ce; --chg-bar: #c9a227; --mark-del: #f6c0c0; --mark-ins: #bfe8c6;
  --badge-diff: #c9a227; --badge-same: #2e7d32; --badge-wait: #6b6b6b; --badge-err: #c42b1c; --badge-other: #8a8a8a;
  --red: #c42b1c; --note-bg: #e6f0fa; --note-border: #9cc2ea; --answer-bg: #f3f6f4;
}
@media (prefers-color-scheme: dark) {
  :root {
    --text: #e6e6e6; --muted: #a0a0a0; --bg: #161816; --card: #1f2220; --surface: #2a2a2a;
    --border: #353a36; --line: #4a4a4a; --brand: #6fb38d; --brand-soft: #22352a;
    --add-bg: #1d3322; --add-bar: #57b45c; --del-bg: #3d1c1f; --del-bar: #e0605a;
    --chg-bg: #3a3014; --chg-bar: #d8b545; --mark-del: #6e2b2b; --mark-ins: #22522c;
    --badge-diff: #d8b545; --badge-same: #57b45c; --badge-wait: #8a8a8a; --badge-err: #e0605a; --badge-other: #5f5f5f;
    --red: #ff8f85; --note-bg: #1c2f45; --note-border: #2f5b8a; --answer-bg: #252a27;
  }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 14px/1.6 system-ui, -apple-system, "Segoe UI", "Yu Gothic UI", sans-serif; }
.page { max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }
code, td.text, pre { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12.5px; }

/* 題 */
header.top { display: flex; align-items: baseline; justify-content: space-between; gap: 12px; flex-wrap: wrap;
  border-bottom: 3px solid var(--brand); padding-bottom: 10px; margin-bottom: 18px; }
header.top h1 { margin: 0; font-size: 22px; letter-spacing: .02em; overflow-wrap: anywhere; }
header.top h1 .app { color: var(--brand); }
header.top .when { color: var(--muted); font-size: 13px; }

/* まとめのカード */
.card { background: var(--card); border: 1px solid var(--border); border-radius: 10px; padding: 16px 18px; margin-bottom: 16px; }
.summary { display: grid; grid-template-columns: auto 1fr; gap: 16px 24px; align-items: center; }
.summary .status { display: flex; flex-direction: column; gap: 6px; align-items: flex-start; }
.summary p { margin: 0; }
.counts { display: flex; gap: 10px; flex-wrap: wrap; }
.count { min-width: 104px; padding: 8px 12px; border-radius: 8px; background: var(--surface); border-left: 4px solid var(--line); }
.count b { display: block; font-size: 22px; line-height: 1.2; font-variant-numeric: tabular-nums; }
.count span { font-size: 12px; color: var(--muted); }
.count.diff { border-left-color: var(--badge-diff); }
.count.same { border-left-color: var(--badge-same); }
.count.wait { border-left-color: var(--badge-wait); }
.count.err { border-left-color: var(--badge-err); }
.files { grid-column: 1 / -1; display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 0; font-size: 13px; }
.files dt { color: var(--muted); }
.files dd { margin: 0; word-break: break-all; }
.note { margin: 12px 0 0; padding: 8px 12px; border-left: 4px solid var(--note-border); background: var(--note-bg); border-radius: 0 6px 6px 0; font-size: 13px; }

/* 状態の札 */
.badge { display: inline-block; padding: 1px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; color: #fff; white-space: nowrap; }
.badge.diff { background: var(--badge-diff); color: #2b2100; }
.badge.same { background: var(--badge-same); }
.badge.wait { background: var(--badge-wait); }
.badge.err { background: var(--badge-err); }
.badge.other { background: var(--badge-other); }
.badge.big { font-size: 14px; padding: 3px 14px; white-space: normal; }

/* 見出し */
h2 { font-size: 16px; margin: 26px 0 10px; padding-left: 10px; border-left: 4px solid var(--brand); }
h3 { font-size: 14px; margin: 18px 0 8px; color: var(--muted); font-weight: 600; }

/* 表 */
.table-wrap { overflow-x: auto; max-width: 100%; border: 1px solid var(--border); border-radius: 8px; background: var(--card); margin: 0 0 12px; }
table { border-collapse: collapse; width: 100%; }
th, td { padding: 6px 10px; text-align: left; vertical-align: top; border-bottom: 1px solid var(--border); }
thead th { position: sticky; top: 0; background: var(--surface); font-size: 12px; color: var(--muted); font-weight: 600; white-space: nowrap; }
tbody tr:last-child td { border-bottom: none; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.no { text-align: right; color: var(--muted); width: 1%; white-space: nowrap; font-variant-numeric: tabular-nums; }
td.text { white-space: pre-wrap; word-break: break-all; }
td.op { white-space: nowrap; width: 1%; font-weight: 600; }
tr.add td:first-child { box-shadow: inset 4px 0 var(--add-bar); }
tr.del td:first-child { box-shadow: inset 4px 0 var(--del-bar); }
tr.chg td:first-child { box-shadow: inset 4px 0 var(--chg-bar); }
tr.add { background: var(--add-bg); }
tr.del { background: var(--del-bg); }
tr.chg { background: var(--chg-bg); }
tr.gap td { text-align: center; color: var(--muted); background: var(--surface); padding: 2px; font-size: 12px; }
mark.del { background: var(--mark-del); color: inherit; text-decoration: underline; border-radius: 2px; }
mark.ins { background: var(--mark-ins); color: inherit; text-decoration: underline; border-radius: 2px; }
.pairs td:first-child { white-space: nowrap; }
.pairs a { color: var(--brand); text-decoration: none; font-weight: 600; }
.pairs a:hover { text-decoration: underline; }
.empty { color: var(--muted); margin: 6px 0; }

/* 組ごとの枠 */
details.pair { background: var(--card); border: 1px solid var(--border); border-radius: 10px; margin: 14px 0; }
details.pair > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: 12px; flex-wrap: wrap; padding: 12px 16px; }
details.pair > summary::-webkit-details-marker { display: none; }
details.pair > summary::before { content: "▸"; color: var(--muted); transition: transform .15s; }
details.pair[open] > summary::before { transform: rotate(90deg); }
details.pair > summary .name { font-weight: 700; font-size: 15px; overflow-wrap: anywhere; }
details.pair > summary .meta { color: var(--muted); font-size: 12.5px; margin-left: auto; overflow-wrap: anywhere; }
.pair-body { padding: 0 16px 16px; border-top: 1px solid var(--border); }
.cmd { margin: 14px 0 6px; font-weight: 600; font-size: 13px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.cmd code { background: var(--surface); padding: 1px 8px; border-radius: 4px; overflow-wrap: anywhere; }
.cmd .meta { color: var(--muted); font-weight: 400; font-size: 12.5px; }
.kind { font-size: 11.5px; color: var(--muted); border: 1px solid var(--line); border-radius: 4px; padding: 0 6px; font-weight: 500; }

/* AI の答え */
.chat > h4 { font-size: 11.5px; color: var(--muted); font-weight: 400; margin: 6px 0 2px; }
.answer { background: var(--answer-bg); border: 1px solid var(--border); border-radius: 10px; padding: 12px 16px; margin: 8px 0; overflow-x: auto; }
.answer p { margin: 0 0 8px; }
.answer table { width: auto; margin: 6px 0 10px; }
.answer th, .answer td { border: 1px solid var(--border); }
.answer thead th { position: static; }
.answer h4 { margin: 10px 0 4px; font-size: 13.5px; }
.answer ul { margin: 4px 0 0; padding-left: 20px; }
.answer pre { background: var(--surface); padding: 8px 10px; border-radius: 6px; white-space: pre-wrap; word-break: break-all; }
.red { color: var(--red); font-weight: 600; }
.qa { width: fit-content; max-width: 70%; margin: 10px 0 4px auto; background: var(--brand-soft); border: 1px solid var(--border); border-radius: 10px; padding: 6px 12px; }
.qa h4 { margin: 0; font-size: 11.5px; color: var(--muted); font-weight: 400; }
p.text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }

@media (max-width: 640px) {
  .page { padding: 16px 12px 32px; }
  .summary { grid-template-columns: 1fr; }
  details.pair > summary .meta { margin-left: 0; width: 100%; }
  .pair-body { padding: 0 10px 12px; }
  .qa { max-width: 100%; }
}
@media print {
  :root { color-scheme: light; }
  body { background: #fff; font-size: 11px; }
  .page { max-width: none; padding: 0; }
  .card, details.pair, .table-wrap, .answer { border-color: #bbb; }
  details.pair { break-before: page; }
  details.pair > summary::before { content: none; }
  details.pair:not([open]) > .pair-body { display: block; }
  thead th { position: static; }
  tr.add, tr.del, tr.chg, tr.gap, .badge, .count, mark { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
}
@media print {
  details.pair::details-content { content-visibility: visible; display: block; }
}
"""
# 状態の札の class(ADR-0180 決定1)。STATUS_LABEL の言葉で選び、どれでもなければ灰色(other)
_BADGE = {"difference": "diff", "no_difference": "same", "needs_selection": "wait", "error": "err"}
_BADGE_OTHER = "other"
# まとめた枠の状態の重さ(重い順に 失敗 > 選択待ち > 差分あり > 差分なし。ADR-0180 決定1 の 5)
_WEIGHT = {"no_difference": 0, "difference": 1, "needs_selection": 2, "error": 3}
# 構造化比較の表の行の色(操作の言葉から選ぶ)
_OP_ROW = {OP_ADDED: "add", OP_REMOVED: "del", OP_CHANGED: "chg"}
# 題の頭の「DriftPilot」を地の色の文字で出す
_APP = "DriftPilot"
# 違う文字の印の開きタグ(REQ-226 §5)。決まった文字だけを書く
_MARK_OPEN = {"del": '<mark class="del">', "ins": '<mark class="ins">'}


@dataclass(frozen=True)
class ReportPair:
    """出力の 1 組。文字はどれも伏せた後のもの(名前とファイル名は判定に通した後)。

    redacted が None の組は、比較が失敗したか選択待ちの組で、状態だけを出す。structured と raw は件数(比べていなければ None)。
    unparsed は構造化不可の行の数の文、ai は「回答あり」などの状態の文(答えの文は入れない)。turns は (質問, 回答) の並び。
    before_name と after_name は組ごとのファイル名(一括比較だけ。ほかは空)。
    """

    name: str
    status: str
    counts: str
    structured: int | None
    raw: int | None
    unparsed: str
    ai: str
    redacted: RedactedResult | None
    answer: str | None = None
    turns: tuple[tuple[str, str], ...] = ()
    before_name: str = ""
    after_name: str = ""
    # 単純比較の設定の要約と、無視した行・同じとみなした行の数の文(ADR-0166。REQ-227 §13)。件数だけで式は入らない
    raw_lines: tuple[str, ...] = ()
    # 一括比較でまとめて比べた組のコマンドの組が属する組の名前(判定に通した後。ADR-0178 決定3)。1 区間の組とほかの出力は None
    group: str | None = None
    # 最初の依頼の文(ADR-0179 決定1)。比較の窓で答えが届いた組だけ。AI の答えの見出しの後、答えの前に出す。ほかは None
    first_request: str | None = None


@dataclass(frozen=True)
class Report:
    """出力の全体。before_name と after_name は入力のファイル名だけ(一括比較は空で、組ごとに持つ)。

    multiple は組が複数ある形(まとめて比較・一括比較)。組の列と組の一覧を出す。
    status_counts は状態ごとの (言葉, 数) で、一括比較だけ(状態の文と同じ数。STATUS_LABEL の順)。HTML のまとめのカードの
    件数の箱にする(REQ-243 判断の記録 2)。ほかの出力は空で、状態の札を出す。
    """

    title: str
    status: str
    counts: str
    pairs: tuple[ReportPair, ...]
    multiple: bool
    before_name: str = ""
    after_name: str = ""
    status_counts: tuple[tuple[str, int], ...] = ()

    @property
    def omitted_lines(self) -> int:
        return sum(pair.redacted.omitted_lines for pair in self.pairs if pair.redacted is not None)


# --- HTML ---


def _e(value: Any) -> str:
    """★ HTML に文字を出す唯一の場所。"""
    return html.escape(str(value), quote=True)


def _el(tag: str, value: Any, cls: str = "") -> str:
    """決まったタグの中に文字を 1 つ入れる。tag と cls はこのモジュールの決まった文字だけ。"""
    attr = f' class="{cls}"' if cls else ""
    return f"<{tag}{attr}>{_e(value)}</{tag}>"


def _spans(spans: Iterable[dict[str, Any]]) -> str:
    out = []
    for span in spans:
        part = _e(span["text"])
        if span.get("code"):
            part = f"<code>{part}</code>"
        if span.get("bold"):
            part = f"<strong>{part}</strong>"
        if span.get("red"):
            part = f'<span class="red">{part}</span>'
        out.append(part)
    return "".join(out)


def _answer_html(text: str) -> list[str]:
    """AI の答え(と回答)を format_answer のブロックから作る(ADR-0156 決定4)。読めない書き方は文字のまま p に入る。"""
    out: list[str] = []
    for block in format_answer(text):
        kind = block["type"]
        if kind == "heading":
            out.append(f"<h4>{_spans(block['spans'])}</h4>")
        elif kind == "list":
            items = "".join(f"<li>{_spans(item)}</li>" for item in block["items"])
            out.append(f"<ul>{items}</ul>")
        elif kind == "table":
            head = "".join(f"<th>{_spans(cell)}</th>" for cell in block["head"])
            rows = "".join("<tr>" + "".join(f"<td>{_spans(cell)}</td>" for cell in row) + "</tr>" for row in block["rows"])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
        elif kind == "code":
            out.append(f"<pre><code>{_e(block['text'])}</code></pre>")
        else:
            out.append(f"<p>{_spans(block['spans'])}</p>")
    return out


@dataclass(frozen=True)
class _MarkedText:
    """違う文字の印を付けて描くセルの文字(REQ-226 §5)。ranges は Python の文字の位置、cls は "del" か "ins"。"""

    text: str
    ranges: tuple[tuple[int, int], ...]
    cls: str


def _marked(text: str, ranges: Sequence[tuple[int, int]], cls: str) -> str:
    """文字を範囲で切り、範囲の中を <mark class="del|ins"> で囲む(REQ-226 §5)。

    中の文字も外の文字も全部 _e を通す。タグは決まった文字(_MARK_OPEN と </mark>)だけ。
    """
    opening = _MARK_OPEN[cls]
    out: list[str] = []
    at = 0
    for start, end in ranges:
        out.append(_e(text[at:start]))
        out.append(f"{opening}{_e(text[start:end])}</mark>")
        at = end
    out.append(_e(text[at:]))
    return "".join(out)


def _td(value: Any, kind: str) -> str:
    """表の 1 つのセル。印の付いた文字(_MarkedText)は _marked で描き、ほかは _el で描く。"""
    if isinstance(value, _MarkedText):
        attr = f' class="{kind}"' if kind else ""
        return f"<td{attr}>{_marked(value.text, value.ranges, value.cls)}</td>"
    return _el("td", value, kind)


def _row(cells: Sequence[tuple[Any, str]], cls: str = "") -> str:
    attr = f' class="{cls}"' if cls else ""
    return f"<tr{attr}>" + "".join(_td(value, kind) for value, kind in cells) + "</tr>"


class _MarkBudget:
    """1 つの表で印を求める行を先頭から数える(REQ-226 §5)。MAX_MARKED_ROWS 行を超えた分は塗らない。"""

    def __init__(self) -> None:
        self._left = inline_marks.MAX_MARKED_ROWS  # テストで差し替えられるよう、表ごとに読む

    def pair(self, before: str, after: str) -> tuple[Any, Any]:
        """印を付ける対象の行の (変更前のセル, 変更後のセル)。上限を超えたか、塗らない行なら文字のまま。"""
        if self._left <= 0:
            return before, after
        self._left -= 1
        found = inline_marks.line_marks(before, after)
        if found is None:
            return before, after
        return _MarkedText(before, found.before, "del"), _MarkedText(after, found.after, "ins")


def _head(names: Sequence[str | tuple[str, str]]) -> str:
    """表の見出しの行。(文字, class) を渡すと class を付ける(class はこのモジュールの決まった文字だけ)。"""
    cells = [_el("th", name[0], name[1]) if isinstance(name, tuple) else _el("th", name) for name in names]
    return "<thead><tr>" + "".join(cells) + "</tr></thead>"


def _table(head: str, rows: Sequence[str], cls: str = "") -> list[str]:
    """枠(div.table-wrap)で包んだ表。幅の狭い画面では枠の中で横にスクロールする(ADR-0180 決定1 の 6)。"""
    attr = f' class="{cls}"' if cls else ""
    return [f'<div class="table-wrap"><table{attr}>', head, "<tbody>", *rows, "</tbody></table></div>"]


def _number(value: int | None) -> str:
    return NO_COUNT if value is None else str(value)


def _status_key(text: str) -> str | None:
    """状態の文の頭の言葉の STATUS_LABEL のキー(「差分あり（3 組中 2 組）」なら difference)。どれでもなければ None。"""
    for key, label in STATUS_LABEL.items():
        if text.startswith(label):
            return key
    return None


def _badge(text: str, big: bool = False) -> str:
    """状態の札。class は _BADGE の決まった文字だけから選び、文字は _e を通す。"""
    key = _status_key(text)
    cls = _BADGE[key] if key is not None else _BADGE_OTHER
    return f'<span class="badge {cls}{" big" if big else ""}">{_e(text)}</span>'


def _pairs_table(report: Report, numbers: Sequence[int]) -> list[str]:
    """組の一覧。名前はその組の枠へのページ内のリンク(番号だけの #pair-N)、状態は札。numbers は組ごとの枠の番号。"""
    head = [
        EXPORT_COL_PAIR,
        BATCH_COL_STATUS,
        (BATCH_COL_STRUCTURED, "num"),
        (BATCH_COL_RAW, "num"),
        BATCH_COL_UNPARSED,
        BATCH_COL_LLM,
    ]
    rows = [
        f'<tr><td><a href="#pair-{int(number)}">{_e(pair.name)}</a></td><td>{_badge(pair.status)}</td>'
        + _td(_number(pair.structured), "num")
        + _td(_number(pair.raw), "num")
        + _td(pair.unparsed, "")
        + _td(pair.ai, "")
        + "</tr>"
        for pair, number in zip(report.pairs, numbers, strict=True)
    ]
    return [_el("h2", EXPORT_PAIRS), *_table(_head(head), rows, "pairs")]


def _structured_table(redacted: RedactedResult) -> list[str]:
    out = [_el("h3", EXPORT_STRUCTURED)]
    if not redacted.diffs:
        return [*out, _el("p", EXPORT_NO_ROWS, "empty")]
    head = [COMPARE_COL_OP, COMPARE_COL_CATEGORY, COMPARE_COL_TARGET, COMPARE_COL_ITEM, COMPARE_COL_BEFORE, COMPARE_COL_AFTER]
    budget = _MarkBudget()
    rows: list[str] = []
    for d in redacted.diffs:
        before: Any = d.before
        after: Any = d.after
        # 変更の行で、送らない形(omitted)でない行だけ、違う文字に印を付ける(伏せた後の文字で求める。REQ-226 §5)
        if d.op == OP_CHANGED and not d.omitted:
            before, after = budget.pair(d.before, d.after)
        cells = [(d.op, "op"), (d.category, ""), (d.target, "text"), (d.item, "text"), (before, "text"), (after, "text")]
        rows.append(_row(cells, _OP_ROW.get(d.op, "")))
    return [*out, *_table(_head(head), rows)]


def _gap(previous: tuple[int | None, int | None] | None, row: Any) -> bool:
    """出す行の間に出さない行があるか。差分の無い行は両側の行番号を持つので、どちらかの番号が飛べば間がある。"""
    if previous is None:
        return (row.before_no or 1) > 1 or (row.after_no or 1) > 1
    last_before, last_after = previous
    return (row.before_no is not None and last_before is not None and row.before_no > last_before + 1) or (
        row.after_no is not None and last_after is not None and row.after_no > last_after + 1
    )


def _raw_table(redacted: RedactedResult) -> list[str]:
    """単純比較の差分の行と前後の文脈の行。離れたところは「…」の行にする。"""
    out = [_el("h3", EXPORT_RAW)]
    if not redacted.raw_rows:
        return [*out, _el("p", EXPORT_NO_ROWS, "empty")]
    head = [COMPARE_COL_OP, COMPARE_COL_BEFORE_NO, COMPARE_COL_BEFORE, COMPARE_COL_AFTER_NO, COMPARE_COL_AFTER]
    rows: list[str] = []
    last: tuple[int | None, int | None] | None = None
    budget = _MarkBudget()
    for row in redacted.raw_rows:
        if _gap(last, row):
            rows.append(f'<tr class="gap"><td colspan="5">{_e(EXPORT_GAP)}</td></tr>')
        before: Any = row.before_text
        after: Any = row.after_text
        # 変更の行(差分で両側の行番号がある行。印の文字には頼らない)で、どちらも送らない形でない行だけ印を付ける(REQ-226 §5)
        if (
            row.is_diff
            and row.before_no is not None
            and row.after_no is not None
            and row.before_text != OMITTED
            and row.after_text != OMITTED
        ):
            before, after = budget.pair(row.before_text, row.after_text)
        rows.append(
            _row(
                [
                    (row.mark, "op"),
                    ("" if row.before_no is None else row.before_no, "no"),
                    (before, "text"),
                    ("" if row.after_no is None else row.after_no, "no"),
                    (after, "text"),
                ],
                _raw_row_class(row),
            )
        )
        last = (
            row.before_no if row.before_no is not None else (last[0] if last else None),
            row.after_no if row.after_no is not None else (last[1] if last else None),
        )
    return [*out, *_table(_head(head), rows)]


def _raw_row_class(row: RedactedRawRow) -> str:
    """単純比較の行の色。差分の行は行番号で選ぶ(両側=変更・変更前だけ=削除(移動元も)・変更後だけ=追加(移動先も))。

    文脈の行・無視した行・同じとみなした行(差分でない行)は色を付けない。印の文字には頼らない。
    """
    if not row.is_diff:
        return ""
    if row.before_no is not None and row.after_no is not None:
        return "chg"
    return "del" if row.before_no is not None else "add"


def _answer_part(pair: ReportPair) -> list[str]:
    """AI の答え(ADR-0180 決定1 の 7)。最初の依頼と追加の質問は右寄せの吹き出し(div.qa)、答えと回答は div.answer。

    見出しの文とタグ(h3・h4・p.text)は今のまま。中の答えは _answer_html(画面と同じ意味のタグ)。
    """
    if pair.answer is None:
        return []
    out = ['<div class="chat">', _el("h3", EXPORT_ANSWER)]
    if pair.first_request is not None:
        # 最初の依頼(ADR-0179 決定1)。追加の質問の文と同じく、送る前に確かめた文をそのまま出す
        out.append('<div class="qa">' + _el("h4", EXPORT_FIRST_REQUEST) + _el("p", pair.first_request, "text") + "</div>")
    out += ['<div class="answer">', *_answer_html(pair.answer), "</div>"]
    for question, answer in pair.turns:
        out.append('<div class="qa">' + _el("h4", FOLLOW_UP_QUESTION_LABEL) + _el("p", question, "text") + "</div>")
        out.append(_el("h4", FOLLOW_UP_ANSWER_LABEL))
        out += ['<div class="answer">', *_answer_html(answer), "</div>"]
    out.append("</div>")
    return out


def _pair_content(pair: ReportPair) -> list[str]:
    """1 組の中身。今の順(単純比較の設定の文・構造化比較の表・単純比較の表・AI の答え)。"""
    out = [_el("p", line) for line in pair.raw_lines]
    if pair.redacted is not None:
        out += _structured_table(pair.redacted)
        out += _raw_table(pair.redacted)
    return out + _answer_part(pair)


def _counts_text(pair: ReportPair) -> str:
    return f"{EXPORT_COUNTS}: {pair.counts}" if pair.counts else ""


def _files_text(pair: ReportPair) -> str:
    if not (pair.before_name or pair.after_name):
        return ""
    return f"{COMPARE_COL_BEFORE}: {pair.before_name}　{COMPARE_COL_AFTER}: {pair.after_name}"


def _frames(pairs: Sequence[ReportPair]) -> list[list[ReportPair]]:
    """組ごとの枠に分ける。同じ group が続く組は 1 つの枠(ADR-0178 の まとめて比べた組)、group の無い組は 1 組で 1 つ。"""
    frames: list[list[ReportPair]] = []
    for pair in pairs:
        if pair.group is not None and frames and frames[-1][0].group == pair.group:
            frames[-1].append(pair)
        else:
            frames.append([pair])
    return frames


def _heaviest(statuses: Sequence[str]) -> str:
    """まとめた枠の状態。中の組の一番重い状態(失敗 > 選択待ち > 差分あり > 差分なし)。ほかの文は一番軽い扱い。"""
    def weight(text: str) -> int:
        key = _status_key(text)
        return _WEIGHT[key] if key is not None and text == STATUS_LABEL[key] else -1

    return max(statuses, key=weight)


def _command_of(pair: ReportPair) -> str:
    """まとめた枠の中の小見出しのコマンドの文。組の名前が「<group>: 」で始まればその残り、ほかは組の名前のまま。"""
    head = f"{pair.group}: "
    return pair.name[len(head) :] if pair.group is not None and pair.name.startswith(head) else pair.name


def _frame_html(number: int, pairs: Sequence[ReportPair]) -> list[str]:
    """組ごとの枠(details.pair#pair-N)。差分なしの枠は閉じ、ほか(差分あり・失敗・選択待ち)は開く(ADR-0180 決定1 の 4・5)。

    id は番号だけ。summary に名前・状態の札・件数とファイル名。まとめた枠は中にコマンドごとの小見出しと今の表と答え。
    """
    first = pairs[0]
    grouped = first.group is not None
    name = first.group if grouped else first.name
    status = _heaviest([pair.status for pair in pairs]) if grouped else first.status
    opened = "" if status == STATUS_LABEL["no_difference"] else " open"
    meta = "　".join(text for text in ([_files_text(first)] if grouped else [_counts_text(first), _files_text(first)]) if text)
    out = [
        f'<details class="pair" id="pair-{int(number)}"{opened}>',
        "<summary>" + _el("span", name, "name") + _badge(status) + (_el("span", meta, "meta") if meta else "") + "</summary>",
        '<div class="pair-body">',
    ]
    if not grouped:
        out += _pair_content(first)
    else:
        for pair in pairs:
            kind = EXPORT_KIND_STRUCTURED if pair.structured is not None else EXPORT_KIND_RAW
            counts = _counts_text(pair)
            out.append(
                '<div class="cmd">'
                + _el("code", _command_of(pair))
                + _el("span", kind, "kind")
                + _badge(pair.status)
                + (_el("span", counts, "meta") if counts else "")
                + "</div>"
            )
            out += _pair_content(pair)
    out += ["</div>", "</details>"]
    return out


def _title_html(title: str) -> str:
    """題。頭が「DriftPilot 」なら DriftPilot を地の色の文字で分けて出し、ほかは題全体を地の色の文字で出す。"""
    if title.startswith(f"{_APP} "):
        return f'<h1><span class="app">{_e(_APP)}</span> {_e(title[len(_APP) + 1 :])}</h1>'
    return f'<h1><span class="app">{_e(title)}</span></h1>'


def _card_html(report: Report) -> list[str]:
    """まとめのカード。状態の札(一括比較は代わりに件数の箱)・件数の文・ファイル名・案内・省いた行の数。"""
    out = ['<section class="card">', '<div class="summary">']
    if report.status_counts:
        out.append('<div class="status">' + _el("span", EXPORT_STATUS, "label") + "</div>")
        boxes = []
        for label, count in report.status_counts:
            key = next((k for k, text in STATUS_LABEL.items() if text == label), None)
            cls = _BADGE[key] if key is not None else _BADGE_OTHER
            boxes.append(f'<div class="count {cls}">' + _el("b", int(count)) + _el("span", label) + "</div>")
        out.append('<div class="counts">' + "".join(boxes) + "</div>")
    else:
        out.append('<div class="status">' + _el("span", EXPORT_STATUS, "label") + _badge(report.status, big=True) + "</div>")
        if report.counts:
            out.append(_el("p", f"{EXPORT_COUNTS}: {report.counts}"))
    if report.before_name or report.after_name:
        out.append(
            '<dl class="files">'
            + _el("dt", COMPARE_COL_BEFORE)
            + _el("dd", report.before_name)
            + _el("dt", COMPARE_COL_AFTER)
            + _el("dd", report.after_name)
            + "</dl>"
        )
    out.append("</div>")
    out.append(_el("p", EXPORT_GUIDE, "note"))
    if report.omitted_lines:
        out.append(_el("p", export_omitted(report.omitted_lines), "note"))
    out.append("</section>")
    return out


def render_html(report: Report, *, saved_at: datetime) -> str:
    """1 つの HTML の文字(ADR-0180)。順は 題の帯(題・出力の日時)・まとめのカード(状態・件数・ファイル名・案内・省いた行数)・
    組の一覧・組ごとの枠(表と AI の答え)。1 組の比較(multiple が偽)は枠を使わず、カードの後に今の順で出す。
    """
    out = [
        "<!DOCTYPE html>",
        '<html lang="ja">',
        "<head>",
        '<meta charset="utf-8">',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'\">",
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        _el("title", report.title),
        f"<style>{_STYLE}</style>",
        "</head>",
        "<body>",
        '<div class="page">',
        '<header class="top">',
        _title_html(report.title),
        _el("span", f"{EXPORT_SAVED_AT}: {saved_at.isoformat(timespec='seconds')}", "when"),
        "</header>",
        *_card_html(report),
    ]
    if report.multiple:
        frames = _frames(report.pairs)
        numbers = [number for number, frame in enumerate(frames, start=1) for _pair in frame]
        out += _pairs_table(report, numbers)
        for number, frame in enumerate(frames, start=1):
            out += _frame_html(number, frame)
    else:
        for pair in report.pairs:
            out += ["<section>", *_pair_content(pair), "</section>"]
    out += ["</div>", "</body>", "</html>", ""]
    return "\n".join(out)


# --- CSV ---


def _cell(value: str) -> str:
    """文字の欄。先頭の空白(半角と全角)を除いた最初の文字が式の頭なら、前に `'` を付ける(ADR-0156 決定6)。

    タブ・CR・LF が先頭の値を lstrip で落とさないよう、落とす文字は半角と全角の空白だけにする。
    """
    return "'" + value if value.lstrip(" 　").startswith(_FORMULA_HEADS) else value


def _csv_value(value: Any) -> Any:
    """数(行番号と件数)はそのまま、ほかは文字にして _cell を通す。"""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return _cell("" if value is None else str(value))


def _csv(rows: Iterable[Sequence[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for row in rows:
        writer.writerow([_csv_value(value) for value in row])
    return buffer.getvalue().encode("utf-8-sig")


def _line(value: int | None) -> Any:
    return NO_COUNT if value is None else value


def render_diffs_csv(report: Report) -> bytes:
    """差分の行の CSV。単純比較は差分の行だけ(文脈の行は入れない)。複数の組のときだけ先頭に「組」の列。"""
    head = [
        EXPORT_COL_KIND,
        COMPARE_COL_OP,
        COMPARE_COL_CATEGORY,
        COMPARE_COL_TARGET,
        COMPARE_COL_ITEM,
        COMPARE_COL_BEFORE,
        COMPARE_COL_AFTER,
        EXPORT_COL_BEFORE_LINE,
        EXPORT_COL_AFTER_LINE,
    ]
    rows: list[list[Any]] = [[EXPORT_COL_PAIR, *head] if report.multiple else head]
    for pair in report.pairs:
        if pair.redacted is None:
            continue
        lead = [pair.name] if report.multiple else []
        for d in pair.redacted.diffs:
            rows.append([*lead, EXPORT_KIND_STRUCTURED, d.op, d.category, d.target, d.item, d.before, d.after, NO_COUNT, NO_COUNT])
        for r in pair.redacted.raw_rows:
            if not r.is_diff:
                continue
            rows.append([*lead, EXPORT_KIND_RAW, r.mark, "", "", "", r.before_text, r.after_text, _line(r.before_no), _line(r.after_no)])
    return _csv(rows)


def render_pairs_csv(report: Report) -> bytes:
    """組の一覧の CSV。AI の列は状態だけで、答えの文は入れない。件数は数のまま。

    属する組(group)を持つ組が 1 つでもあれば、最後に列「元の組」を足す(ADR-0178 決定3。1 区間の組のその欄は空)。
    1 つも無い出力(1 区間の組だけの一括比較・比較の窓)は今のまま。
    """
    grouped = any(pair.group is not None for pair in report.pairs)
    head = [EXPORT_COL_PAIR, BATCH_COL_STATUS, BATCH_COL_STRUCTURED, BATCH_COL_RAW, BATCH_COL_UNPARSED, BATCH_COL_LLM]
    rows: list[list[Any]] = [[*head, EXPORT_COL_GROUP] if grouped else head]
    for pair in report.pairs:
        row = [pair.name, pair.status, _line(pair.structured), _line(pair.raw), pair.unparsed, pair.ai]
        rows.append([*row, pair.group or ""] if grouped else row)
    return _csv(rows)
