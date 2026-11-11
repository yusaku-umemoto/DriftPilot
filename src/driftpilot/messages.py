"""警告・注意の文章化。core は kind と detail の構造だけを返し、文面は入口が作る(ADR-0015 決定4)。

入口(CLI と GUI)の共通部分。同じ警告が CLI と GUI で違う言葉にならないよう、文面はここにだけ置く(ADR-0022 決定2)。

比較の部品(core・diff_engine・llm_analyzer。ntc_templates や hier_config を読む)は、読み込むときには読まない。
自己診断(`driftpilot selfcheck`。REQ-38)は、それらの部品が欠けていてもこの文面で結果を出すため。
型は注釈にだけ使う(注釈は使うときまで評価されない)。値が要る定数は、使う関数の中で読む。
"""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

# 上限の既定値と印の名前(ほかのモジュールを読み込まない小さなモジュール。比較の部品は読まない。ADR-0167)
from driftpilot.llm_constants import DEFAULT_MAX_DIFFS, DEFAULT_MAX_TOTAL_DIFFS, PROMPT_MARKS

if TYPE_CHECKING:
    from driftpilot.batch_io import Pairing
    from driftpilot.core import BatchLlmPreview, BatchProgress, BatchSummary, ComparisonExclusion, SideSelection
    from driftpilot.diff_engine import SafetyWarning
    from driftpilot.diff_engine.exposure_check import ExposureNotice
    from driftpilot.llm_analyzer import ConnectionCheckResult, LlmAnalysis, UnsentShape
    from driftpilot.segmentation.input_forms import InputHint


def _llm_give_up_after() -> int:
    from driftpilot.core.batch import LLM_GIVE_UP_AFTER

    return LLM_GIVE_UP_AFTER

_SIDE_LABEL = {"before": "変更前", "after": "変更後"}
_STATE_LABEL = {"normal": "通常版", "exposed": "既定値込み版", "unknown": "不明"}

# 結果の status の名前。CLI の一括比較のサマリと GUI のステータスバーで共有する
STATUS_LABEL = {"no_difference": "差分なし", "difference": "差分あり", "needs_selection": "選択待ち", "error": "失敗"}
# 表の操作の欄の「変更」。画面の論理の OP_LABEL と、HTML の出力で印を付ける行の見分けに使う(REQ-226)
OP_CHANGED = "変更"
# 表の操作の欄の「追加」「削除」。画面の論理の OP_LABEL と、HTML の出力の行の色分けに使う(REQ-243)
OP_ADDED = "追加"
OP_REMOVED = "削除"


def status_breakdown(structured_label: str, structured: int, raw: int) -> str:
    """pywebview の比較の画面の窓の下の文の内訳(REQ-113 §3)。「差分あり（構造化: なし／単純: 4 件）」の形。

    structured_label は「構造化」か「show」。0 件は「なし」と出す。
    """

    def count(n: int) -> str:
        return "なし" if n == 0 else f"{n} 件"

    return f"{STATUS_LABEL['difference']}（{structured_label}: {count(structured)}／単純: {count(raw)}）"


# 行の差分だけがあり、並べ替えると同じとき(REQ-113 §3)
STATUS_ORDER_ONLY = f"{STATUS_LABEL['difference']}（順番だけの違いです）"


def unreadable_utf8(path: object, hints: tuple[InputHint, ...] = ()) -> str:
    """入力を UTF-8 として読めなかったときの文。hints(読めないバイト列から当てた案内。REQ-189)があれば後ろに足す。"""
    text = f"{path}: UTF-8 として読めません"
    if not hints:
        return text
    return text + "。" + "".join(input_hint_text(h) for h in hints)


def unreadable(path: object, exc: OSError) -> str:
    """入力を読めなかったときの文(存在しない・権限がないなど)。"""
    return f"{path}: 読めません ({exc.strerror or exc})"


# --- pywebview の画面(ADR-0074 / REQ-103・REQ-107。`driftpilot gui`)。例外の文面・値はどの文にも入れない ---


def gui_web_unavailable() -> str:
    """pywebview の画面を開こうとしたが pywebview が無いときの文(`gui` extra)。"""
    return "pywebview が入っていません(pip install 'driftpilot[gui]')"


def webview2_missing() -> str:
    """Windows で WebView2 の実行部品が無いとき(pywebview が古い IE の部品に切り替わる。ADR-0074 決定7)。"""
    return "WebView2 Runtime が入っていません。Microsoft のサイトから入れてください"


def gui_web_start_failed(error_type: str) -> str:
    """表示部品を初期化できないとき(pythonnet・pyobjc の抜けなど)。例外は型名だけを添える。"""
    return f"画面の表示部品を使えません: {error_type}"


def gui_web_unknown_text() -> str:
    """画面が、ここに無い文の鍵を求めたとき(js_api の messages)。"""
    return "画面に出す文が見つかりません。"


def gui_web_call_failed() -> str:
    """画面から呼んだ処理の中で想定外の例外が出たとき(js_api)。例外の文面は画面に出さない。"""
    return "処理の途中で問題が起きました。"


# 画面に出す文(js_api の messages で画面へ渡す。HTML には文を書かない。REQ-103 §4)。
# 鍵は「画面.部品.名前」の形。見本のページ(static/sample.html)の文は sample. で始める。値はすべて架空
GUI_WEB_TEXTS: dict[str, str] = {
    "sample.heading": "画面の部品の見本",
    "sample.note": "pywebview の画面の土台（作りかけ）です。部品の見た目を確かめるためのページで、押しても何も処理しません。",
    "sample.info.driftpilot": "DriftPilot の版",
    "sample.info.python": "Python の版",
    "sample.info.renderer": "表示部品",
    "sample.section.buttons": "ボタン",
    "sample.button.primary": "実行",
    "sample.button.secondary": "選ぶ…",
    "sample.button.danger": "削除…",
    "sample.button.disabled": "保存",
    "sample.section.bands": "帯",
    "sample.band.info": "この 4 組を比較します。「実行」を押すまで、まだ1件も比較していません。",
    "sample.band.warning": "中身は FortiGate の config に見えます。FortiGate なら機種に fortinet を指定して比較し直してください。",
    "sample.band.error": "比較できませんでした。変更前のファイルを読めません。",
    "sample.section.table": "表",
    "sample.table.name": "機器",
    "sample.table.status": "結果",
    "sample.table.diffs": "差分",
    "sample.table.row1.name": "lab-sw1",
    "sample.table.row1.status": "差分あり",
    "sample.table.row1.diffs": "3",
    "sample.table.row2.name": "lab-sw2",
    "sample.table.row2.status": "差分なし",
    "sample.table.row2.diffs": "0",
    "sample.table.row3.name": "lab-fw1",
    "sample.table.row3.status": "失敗",
    "sample.table.row3.diffs": "—",
    "sample.table.row4.name": "lab-rt1",
    "sample.table.row4.status": "選択待ち",
    "sample.table.row4.diffs": "—",
    "sample.section.details": "折りたたみ",
    "sample.details.summary": "診断",
    "sample.details.body": "設定ファイルの場所と、読み元（上ほど優先）を出します。",
    "sample.section.fields": "入力の行",
    "sample.field.text": "名前",
    "sample.field.select": "形式",
    "sample.field.option1": "OpenAI 互換",
    "sample.field.option2": "Azure OpenAI",
    "sample.field.textarea": "伏せる語",
    "sample.field.check": "既定値込みの出力として読む",
    "sample.section.tabs": "タブ",
    "sample.tab.compare": "比較",
    "sample.tab.batch": "一括比較",
    "sample.tab.settings": "設定",
    "sample.tab.compare.body": "2つの config を比べる画面が入ります。",
    "sample.tab.batch.body": "フォルダの中の組をまとめて比べる画面が入ります。",
    "sample.tab.settings.body": "AI の接続と config.toml を変える画面が入ります。",
    "sample.section.progress": "進み具合",
    "sample.progress.label": "2 / 4 組を比較しました",
    "sample.footer.open": "フォルダを開く",
    "sample.footer.close": "閉じる",
    "sample.status.ready": "準備ができました。",
    # 比較の画面の部品(REQ-105 §5)
    "sample.section.compare": "比較の画面の部品",
    "sample.compare.before": "変更前",
    "sample.compare.after": "変更後",
    "sample.compare.path": "/Users/lab/configs/lab-sw1-before.txt",
    "sample.compare.browse": "参照…",
    "sample.compare.paste": "貼り付け…",
    "sample.compare.summary": "1,204 行",
    "sample.compare.mode": "比較方式",
    "sample.compare.mode.both": "両方",
    "sample.compare.mode.raw": "単純",
    "sample.compare.before_no": "前",
    "sample.compare.after_no": "後",
    "sample.compare.row1.no": "12",
    "sample.compare.row1.text": "hostname lab-sw1",
    "sample.compare.row2.mark": "~",
    "sample.compare.row2.no": "13",
    "sample.compare.row2.before": " name users",
    "sample.compare.row2.after": " name staff",
    "sample.compare.status": "差分あり",
    "sample.compare.counts": "構造化 1  単純 1",
    "sample.compare.paste.title": "変更前を貼り付け",
    "sample.compare.paste.prompt": "config か端末ログを貼り付けてください",
    "sample.compare.paste.ok": "OK",
    "sample.compare.paste.cancel": "キャンセル",
}


def save_caution() -> str:
    """保存した結果の扱いの注意(ADR-0017 決定4)。保存ファイルは入力の設定値を平文で含む。"""
    return "入力の設定値をそのまま含みます。元の config と同じ扱い(社外秘)にしてください"


def warning_message(w: SafetyWarning) -> str:
    if w.kind == "empty_category":
        return (
            f"{_SIDE_LABEL[w.side or 'before']}の {w.detail['subject']} がツリーに {w.detail['tree_count']} 件あるのに、"
            f"{w.category} を1件も読めていません。書式が変わった可能性があります。"
        )
    if w.kind == "duplicate_line":
        # 値は含めない(detail にはスコープと行番号しか無い)
        scope = w.detail["scope"] or "トップレベル"
        if not w.detail["lines"]:
            # A型の木は元の行番号を持たない(ADR-0030 決定5)
            return (
                f"{_SIDE_LABEL[w.side or 'before']}の {scope} の下に、同じ行が {w.detail.get('count', 2)} 回あります"
                "(この読み方では行番号は取れません)。どれも捨てていません。"
            )
        lines = "・".join(str(n) for n in w.detail["lines"])
        return (
            f"{_SIDE_LABEL[w.side or 'before']}の {scope} の下で、{lines} 行目が重複しています"
            "(同じ行、または同じ項目に違う値)。どちらも捨てていませんが、IR は後の行の値を使います。"
        )
    if w.kind == "platform_mismatch":
        return _platform_mismatch_message(w)
    if w.kind == "pager_marks":
        return (
            f"{_SIDE_LABEL[w.side or 'before']}のログから、ページ送りの印(--More--)を {w.detail['count']} か所外して"
            "比較しました。途中で表示を止めたログだと、設定の一部が欠けています。"
        )
    if w.kind == "input_form":
        return _input_form_message(w)
    if w.kind == "input_hint":
        d = w.detail
        return f"{_SIDE_LABEL[w.side or 'before']}: {_hint_text(d['kind'], d['count'], d.get('width'), d.get('form'))}"
    if w.kind == "platform_auto":
        return _platform_auto_message(w)
    if w.kind == "raw_ignored_many":
        d = w.detail
        return (
            f"{_SIDE_LABEL[w.side or 'before']}の行の半分を超える {d['ignored']} 行を、単純比較の設定で無視しています"
            f"（{d['lines']} 行のうち）。無視する行の式を確かめてください。"
        )
    return (
        f"ツリー差分は {w.detail['tree_diff_lines']} 行あるのに、structured 差分が0件です。"
        "読み取れていない設定の変更を見落としている可能性があります。"
    )


# 機種の表示名(ADR-0151 決定7)。None は指定なし(Cisco 系の読み方)。判定の a も同じ名前で出す
PLATFORM_LABEL: dict[str | None, str] = {"fortinet": "FortiGate", "fujitsu_sir": "Si-R", None: "Cisco 系・Arista"}
AUTO_PLATFORM_LABEL = "自動"


def platform_label(name: str | None) -> str:
    """系統や判定の名前の表示名。a(Cisco 系の判定)は指定なしと同じ名前。表に無い名前はそのまま出す。"""
    if name == "a":
        name = None
    return PLATFORM_LABEL.get(name, name or PLATFORM_LABEL[None])


def auto_platform_text(used: str | None) -> str:
    """機種を自動にして決めた系統の表示(「自動（FortiGate）」。ADR-0151 決定7)。"""
    return f"{AUTO_PLATFORM_LABEL}（{platform_label(used)}）"


def auto_platform_line(used: str | None) -> str:
    """CLI の text の、--platform auto で config と決まったときの1行(「機種: 自動（FortiGate）」)。"""
    return f"機種: {auto_platform_text(used)}"


def _platform_auto_message(w: SafetyWarning) -> str:
    """機種を自動にしたときの判定なし・食い違い(ADR-0151 決定2)。名前だけを出し、値は入れない。"""
    if w.detail.get("result") == "conflict":
        before = platform_label(w.detail.get("before"))
        after = platform_label(w.detail.get("after"))
        return (
            f"変更前と変更後で機種の判定が違います（変更前: {before}・変更後: {after}）。"
            "Cisco 系・Arista として読みました。AI には送りません。機種を確かめてください。"
        )
    return "機種を判定できませんでした。Cisco 系・Arista として読みました。違うときは機種を選んでください。"


def structured_duplicate_message(detail: dict[str, Any], mode: str) -> str:
    """同じ行の重複で構造化比較ができなかったときの文(REQ-26 §B-2)。

    何が起きたか・どこか・単純比較はどうなったか・どうすればよいかを言う。単純比較の言い方は比べ方で切り替える。
    構造化だけでは単純比較は走らない(§B-2b)。行と親のパスは本文のまま出す。この文は LLM に送らないため
    (決定者の指示による §B-2 からの変更。差分の表示が実際の値を出すのと同じ扱い)。
    """
    side = _SIDE_LABEL[detail["side"]]
    line = detail.get("line")
    if line is None:
        where = f"{side}で、場所は特定できませんでした。"
    else:
        block = f"「{detail['scope']}」ブロックの下" if detail.get("scope") else "トップレベル(字下げの無い行)"
        numbers = detail.get("lines") or []
        at = f"({'・'.join(str(n) for n in numbers)} 行目)" if numbers else ""
        where = f"場所: {side}の{block}の「{line}」{at}。"
    if mode == "structured":
        raw = "単純比較はしていません。比べ方(--mode)を「両方」か「単純」にして比べ直せば見られます。"
    else:
        raw = "単純比較の結果は出ています。"
    return (
        f"同じ行が2回以上書かれているため、構造化比較ができません。{where}{raw}"
        "その行を1つにして比べ直すか、単純比較で見てください。"
    )


# 経路の差分に添えた機種固有値の見出し(ADR-0072、REQ-94)。ほかのキーはキー名のまま出す
_ROUTE_DETAIL_LABEL = {"number": "番号", "edit": "edit", "name": "name"}


def route_detail_note(vendor_specific: dict[str, Any]) -> str:
    """足した・消した経路に添えた機種固有値の短い注記。例: 「（番号 7）」「（edit 7）」「（name EXAMPLE）」。

    値が None のキーは飛ばす。CLI の text と GUI の差分の一覧で、経路の値の後ろに付ける。
    """
    parts = []
    for detail in vendor_specific.values():
        if not isinstance(detail, dict):
            parts.append(f"（{detail}）")
            continue
        for key, value in detail.items():
            if value is not None:
                parts.append(f"（{_ROUTE_DETAIL_LABEL.get(key, key)} {value}）")
    return "".join(parts)


# 安全網5(REQ-25)の見立ての名前と、手がかりの説明(docs/notes/platform-sniff.md の表の ID)
_HINT_LABEL = {"a": "Cisco 系・Arista の config", "d": "FortiGate の config"}
_HINT_RULE = {"SNF-01": "先頭行が #config-version=", "SNF-02": "config … と end の対", "SNF-03": "interface・hostname・! の行"}


def _platform_mismatch_message(w: SafetyWarning) -> str:
    """何を疑ったか/どうすればよいか。値は使わない(detail には指定・見立て・手がかりの ID しか無い)。"""
    side = _SIDE_LABEL[w.side or "before"]
    hint, rule, specified = w.detail["hint"], w.detail["rule"], w.detail["specified"]
    looks = f"{_HINT_LABEL.get(hint, hint)} に見えます({_HINT_RULE.get(rule, rule)}。{rule})。"
    if specified is None:
        return (
            f"{side}の中身は {looks}"
            "機種(--platform)を指定していないため、Cisco 系・Arista のパーサーで読んでいます。"
            "FortiGate なら機種に fortinet を指定して比較し直してください。"
        )
    return f"機種に {specified} を指定していますが、{side}の中身は {looks}機種の指定が正しいか確かめてください。"


# 入力の形の知らせ(ADR-0138 決定7)の種類ごとの文。種類は分岐に使わない値なので、知らない種類も文にする
_INPUT_FORM_TEXT = {
    "encoding:cp932": "Shift_JIS",
    "encoding:utf-16": "UTF-16",
    "encoding:utf-8-bom": "BOM 付きの UTF-8",
}


def _input_form_message(w: SafetyWarning) -> str:
    side = _SIDE_LABEL[w.side or "before"]
    kind = w.detail["kind"]
    if kind in _INPUT_FORM_TEXT:
        return f"{side}のファイルは {_INPUT_FORM_TEXT[kind]} でした。"
    return f"{side}の入力の形を直しました({kind})。"


# 入力の案内(ADR-0138 決定7。REQ-189 §2 の表)。付け足しの形の名前
_HINT_FORM_TEXT = {"timestamp": "時刻", "line_number": "行番号", "mail_quote": "引用の印(> )"}


def input_hint_text(hint: InputHint) -> str:
    """入力の様子から当てた案内の文。入れるのは数と種類だけで、行の中身は入れない。"""
    return _hint_text(hint.kind, hint.count, hint.width, hint.form)


def _hint_text(kind: str, count: int, width: int | None, form: str | None) -> str:
    if kind == "wrap_suspected":
        return f"折り返しの疑いがあります(幅 {width} 桁の行が {count} 本)。行が割れていると、誤った差分が出ます。"
    if kind == "wrap_unrepaired":
        return f"折り返しをつなぎ直せなかった行が {count} 行あります。"
    if kind == "html_no_pre":
        return "HTML のようです。config の本文だけを保存してください。"
    if kind == "utf16_no_bom":
        return "BOM の無い UTF-16 のようです。"
    if kind == "partial_prefix":
        return (
            f"各行の頭に{_HINT_FORM_TEXT.get(form or '', form)}が付いているようです。"
            f"{count} 行は別の形だったので外していません。"
        )
    # 種類は分岐に使わない値なので、知らない種類も文にする
    return f"入力の形に気になる点があります({kind})。"


def notice_message(n: ExposureNotice) -> str:
    before: dict[str, Any] = n.detail["before"]
    after: dict[str, Any] = n.detail["after"]
    if n.kind == "mixed_exposure":
        return (
            f"通常版と既定値込み版を比較しています(変更前: {before['command']!r} は{_STATE_LABEL[before['state']]}、"
            f"変更後: {after['command']!r} は{_STATE_LABEL[after['state']]})。差分の多くは取得方法の違いによるものです"
        )
    if n.kind == "unknown_both":
        return "取得コマンド不明: 変更前・変更後とも、通常版か既定値込み版か判定できません"
    known, known_label, other_label = (
        (before, "変更前", "変更後") if before["state"] != "unknown" else (after, "変更後", "変更前")
    )
    head = "【既定値込み版】" if n.emphasized else ""
    return f"{head}{known_label}は {known['command']!r} で取得({_STATE_LABEL[known['state']]})。{other_label}の取得方法は不明です"


def notice_line(n: ExposureNotice) -> str:
    return ("警告: " if n.level == "warning" else "注意: ") + notice_message(n)


# 比較から外した理由(ADR-0025 決定6)。core の ComparisonExclusion.reason の値ごとの文面
_EXCLUSION_REASON = {
    "reencrypted_value": "FortiGate の再暗号化される値(ENC の付く値)は比較対象外です",
}


def exclusion_line(e: ComparisonExclusion) -> str:
    """比較から外した値の1行。本当に値を変えても差分に出ないことも言う(黙って無視しない)。"""
    reason = _EXCLUSION_REASON.get(e.reason, f"比較対象外の値があります(理由: {e.reason})")
    return (
        f"比較対象外: {reason}(変更前 {e.counts['before']} 件・変更後 {e.counts['after']} 件。ルール {e.rule_id})。"
        "この値を本当に変えても差分には出ません"
    )


def saved_notice(path: Path) -> str:
    """--save で保存したときの1行(ADR-0017 決定4)。保存ファイルは入力の設定値を平文で含む。"""
    return f"注意: 比較結果を {path} に保存しました。{save_caution()}"


def batch_saved_notice(folder: Path, count: int) -> str:
    """batch --save DIR で保存したときの1行。件数ごとに出さず1行にまとめる。"""
    return f"注意: 一括比較の結果 {count} 件を {folder} に保存しました。{save_caution()}"


def batch_summary_lines(summary: BatchSummary, exit_code: int) -> list[str]:
    """標準エラーに出すサマリ(1〜2行)。core が返すのは件数と所要時間の数値だけ。"""
    c = summary.counts
    breakdown = " / ".join(f"{label} {c[status]}" for status, label in STATUS_LABEL.items())
    lines = [
        f"一括比較: {summary.total}件中{summary.executed}件を実行({breakdown})、"
        f"{summary.elapsed_seconds:.1f}秒。終了コード {exit_code}"
    ]
    if summary.cancelled or summary.interrupted:
        lines.append(f"中断しました。残り {summary.total - summary.executed} 件は実行していません")
    return lines


def batch_cancel_accepted() -> str:
    return "中断を受け付けました。実行中の job が終わったら止めます(すぐに止めるには、もう一度 Ctrl+C)"


# --- 対の生成と一括比較画面(ADR-0020 / REQ-27 パート A)。件数の言い方を CLI と GUI で食い違わせない ---


def selection_text(selection: SideSelection, source: str | None) -> str:
    """区間の指定の表示(batch --dry-run と一括比較画面の「区間」の列)。"""
    if selection.command is not None:
        text = f"command {selection.command!r}"
    elif selection.number is not None:
        text = f"segment {selection.number}"
    else:
        return "自動(区間が1つのときだけ選ぶ)"
    return f"{text} (defaults)" if source == "defaults" else text


UNPAIRED_SIDE_LABEL = {"before": "変更前にしかない", "after": "変更後にしかない"}


def pairing_heading(pairing: "Pairing") -> list[str]:
    """変更前・変更後のディレクトリを並べた見出し。取り違えに気づけるようにする(ADR-0020 Action Item 3)。"""
    return [f"変更前: {pairing.before_dir}", f"変更後: {pairing.after_dir}"]


def paired_count_line(count: int) -> str:
    return f"対になったファイル: {count} 件(ファイル名が拡張子まで完全一致するもの)"


def unpaired_count_line(count: int) -> str:
    return f"対にならなかったファイル: {count} 件"


def unpaired_side_line(side: str, count: int) -> str:
    return f"{UNPAIRED_SIDE_LABEL[side]}: {count} 件"


def pairing_text(pairing: "Pairing") -> str:
    """--pair-by-name の一覧。見出し・件数の行は一括比較画面と同じ関数で作る(REQ-27 §A-2)。"""
    lines = [*pairing_heading(pairing), ""]
    lines.append(paired_count_line(len(pairing.pairs)))
    lines += [f"  {name}" for name in pairing.pairs]
    lines.append(unpaired_count_line(len(pairing.before_only) + len(pairing.after_only)))
    for side, names in (("before", pairing.before_only), ("after", pairing.after_only)):
        lines.append(f"  {unpaired_side_line(side, len(names))}")
        lines += [f"    {name}" for name in names]
    return "\n".join(lines) + "\n"


def pairing_summary_lines(pairing: "Pairing") -> list[str]:
    """一括比較画面の組の一覧の上に出す行。--pair-by-name の一覧の見出しと件数の行と同じもの(名前の一覧は画面のタブ)。"""
    sides = "・".join(unpaired_side_line(side, len(names)) for side, names in (("before", pairing.before_only), ("after", pairing.after_only)))
    return [
        *pairing_heading(pairing),
        paired_count_line(len(pairing.pairs)),
        f"{unpaired_count_line(len(pairing.before_only) + len(pairing.after_only))}({sides})",
    ]


def paired_tab_title(count: int) -> str:
    return f"比較するファイル {count} 件"


def unpaired_tab_title(count: int) -> str:
    """0 件でも出す(件数が見えることが大事。ADR-0020「必ず報告する」)。"""
    return f"片方だけのファイル {count} 件"


def pairing_banner(count: int) -> str:
    """組の一覧の上の青い帯(ADR-0020 の「確認が既定、実行は --confirm」を画面にしたもの)。"""
    return (
        f"この {count} 組を比較します。「実行」を押すまで、まだ1件も比較していません。"
        "フォルダを選び間違えていないか確認してください。"
    )


def batch_folder_not_found(side: str, path: str) -> str:
    """一括比較画面のフォルダの欄が、まだ絶対パスの実在するフォルダでないとき(打っている途中など。REQ-97 §1)。"""
    return f"{_SIDE_LABEL[side]}のフォルダが見つかりません({path})。フォルダを選ぶか、パスを最後まで入れてください"


def batch_manifest_not_found(path: str) -> str:
    """一括比較画面のマニフェストの欄が、まだ絶対パスの実在するファイルでないとき(REQ-97 §1)。"""
    return f"組のファイルが見つかりません({path})。ファイルを選ぶか、パスを最後まで入れてください"


# フォルダ・マニフェストを選ぶ部品のボタンの文(wx の既定の「Browse」の代わり。REQ-97 §3)
PICK_BUTTON = "選ぶ…"


def batch_progress_text(progress: BatchProgress) -> str:
    """「5 / 12 件目」と、いま読んでいる組の名前。"""
    return f"{progress.index} / {progress.total} 件目: {progress.label or progress.job_id}"


def batch_cancel_note() -> str:
    """中止の意味(REQ-15。should_cancel は job の境界でだけ効く)。"""
    return "「中止」を押すと、いま読んでいる1件が終わってから止まります。途中で切ると中途半端な結果が残るためです。"


def handoff_note() -> str:
    """一括比較画面の「比較画面で開く」(ADR-0029 決定8。渡すのは入力だけ)。"""
    return "その2ファイルを比較画面の入力欄に入れるだけです(結果は持っていきません)"


def batch_handoff_note() -> str:
    """一括比較の窓(pywebview)の「比較画面で開く」の注記(ボタンの title。ADR-0170 決定6)。wx は handoff_note() のまま。"""
    return "その2ファイルを比較画面に入れて比べます。AI の答えがある組は、差分が送ったときと同じなら、その答えの続きから質問できます"


# 一括比較の結果の一覧の、まだ結果が無い行の状態と件数の欄
JOB_WAITING = "待機"
JOB_RUNNING = "実行中"
NO_COUNT = "—"

_LLM_FAILED = ("connection_error", "api_error", "internal_error")


def llm_column(llm: LlmAnalysis | None) -> str:
    """一括比較の結果の一覧の「AI 解析」の列。回答あり / 未実施(理由) / 失敗(理由) / 送らなかった。"""
    if llm is None or llm.reason == "disabled":
        return NO_COUNT
    if llm.status == "done":
        return "回答あり"
    if llm.reason == "in_whole":
        return LLM_IN_WHOLE
    if llm.reason == "not_approved":
        return "送らなかった"
    if llm.reason == "dry_run":
        return "確認待ち(まだ送っていません)"
    if llm.reason in _LLM_FAILED:
        return f"失敗({llm_reason(llm)})"
    return f"未実施({llm_reason(llm)})"


# --- 一括比較の LLM 解析(ADR-0031)。1段目の要約は CLI(標準エラー)と GUI(確認の画面の上)で同じ ---


def batch_llm_preview_lines(preview: BatchLlmPreview, *, model: str | None, base_url: str | None) -> list[str]:
    """送信前の確認の要約。送る組・合計・送らなかった行の形・伏せた件数・打ち切り・指紋・送り先(ADR-0031 決定3)。"""
    lines = [
        f"AI に送る組: {len(preview.jobs)} 件。送る差分の合計: {preview.total_diffs:,} 件"
        f"(全体の上限 {preview.max_total_diffs:,} 件)",
        f"伏せた値: {preview.masked:,} 件(<secret:N> に置き換えた数)",
        f"1組の上限で打ち切った組: {preview.truncated_jobs} 件",
    ]
    if preview.unsent_lines:
        lines.append(f"判定不能のため送らない行: {preview.unsent_lines:,} 行(差分としては結果に出ています)")
        lines += unsent_shape_lines(preview.unsent_shapes)
    else:
        lines.append("判定不能のため送らない行: なし")
    lines.append(
        f"伏せる表の指紋: {preview.masking_fingerprint or '-'}  モデル: {model or '-'}  接続先: {base_url or '(既定)'}"
    )
    return lines


def batch_llm_preview_row(job_id: str, label: str | None, sent: int, masked: int, unsent: int) -> str:
    """CLI の要約に並べる1組の行(GUI では一覧の1行になる)。"""
    name = f"{job_id}  {label}" if label else job_id
    return f"  {name}: 送る差分 {sent} 件 / 伏せた値 {masked} 件 / 送らない行 {unsent} 行"


def batch_llm_over_limit(total: int, limit: int) -> str:
    """全体の上限を超えたとき(ADR-0031 決定4)。打ち切って一部だけ送ることはしない。"""
    return (
        f"AI に送る差分が合計 {total:,} 件あり、全体の上限 {limit:,} 件を超えています。1件も送っていません。"
        "送るには、比べる組を絞るか、上限を上げて(--llm-max-total-diffs)実行し直してください"
    )


def batch_llm_confirm_hint() -> str:
    """CLI の batch --llm を --confirm なしで実行したとき。対の生成(--pair-by-name)と同じ形。"""
    return "AI にはまだ1件も送っていません。この内容で送るには --confirm を付けて実行してください"


def batch_llm_nothing_to_send() -> str:
    return "AI に送る内容がありません(差分が無いか、送れる行がありません)。送信していません"


def batch_llm_gave_up(count: int) -> str:
    """連続の失敗で残りをあきらめたとき(ADR-0031 決定7)。比較は完走している。"""
    return (
        f"AI への送信が {_llm_give_up_after()} 件続けて失敗したため、残りの {count} 組では AI を呼んでいません"
        "(比較は最後まで行いました)"
    )


def batch_llm_payloads_text(preview: BatchLlmPreview) -> str:
    """「内容をファイルに保存」で書く全文。組ごとに見出しを付け、送る内容(マスク後)をそのまま並べる。"""
    parts = [f"AI に送る内容(マスク後。このあとに答えの書き方の決まり(固定の文)だけを足して送ります)。{len(preview.jobs)} 組", ""]
    for job in preview.jobs:
        name = f"{job.job_id}  {job.label}" if job.label else job.job_id
        parts += [f"=== [{job.index}] {name} ===", job.payload, ""]
    return "\n".join(parts)


# --- LLM 解析(REQ-16) ---

_LLM_REASON = {
    "disabled": "AI 解析を指定していません",
    "dry_run": "送信内容の確認だけです(--llm-dry-run)。送信していません",
    "no_diff": "差分が無いため、AI を呼んでいません",
    "no_structured_diff": "構造化比較に差分が無いので送りませんでした（順番の違いなど）",
    "nothing_to_send": "送れる差分がありません(すべて認証情報の判定ができない行でした)",
    "no_api_key": (
        "API キーがありません(設定画面の「保管庫に保存」か、プロファイルごとの環境変数 DRIFTPILOT_LLM_API_KEY_<続き> に入れてください。"
        "プロファイルを使わないときは DRIFTPILOT_LLM_API_KEY か、利用者ごとの設定ファイルの llm.api_key)"
    ),
    "client_unavailable": "AI の接続部品が入っていません(pip install 'driftpilot[llm]')",
    "not_configured": "モデルが設定されていません(設定ファイルの llm.model)",
    "placeholder_base_url": "接続先の <リソース名> を書き換えてください。送っていません",
    "cli_not_found": "CLI が見つかりません(CLI の場所の欄か PATH を確かめてください)",
    "connection_error": "AI に接続できませんでした",
    "api_error": "AI がエラーを返しました",
    "internal_error": "AI 解析の処理に失敗しました",
    # 一括比較の2段の送信(ADR-0031)。どれも送っていない
    "not_approved": "送信前の確認で、送らないことにした組です",
    "changed_after_approval": "送ろうとした内容が、確認で承認した内容と違ったため、送っていません",
    # 件数は llm_reason で埋める(core を読み込むときに読まないため)
    "gave_up": "AI への送信が {give_up_after} 件続けて失敗したため、この組では呼んでいません",
    "over_total_limit": "送る差分の合計が全体の上限を超えたため、送っていません",
    "cancelled": "中止したため、送っていません",
    "compare_failed": "比較が失敗した組のため、送っていません",
    "platform_conflict": "変更前と変更後で機種の判定が違うため、AI に送っていません",
    "platform_not_sir": "機種に Si-R を指定していますが、中身が別の機種に見えるため、AI に送っていません",
    # 上限の文字数は llm_reason で埋める(読み込むときに llm_analyzer を読まないため。ADR-0158 決定5)
    "full_text_too_long": "全文が上限（{max_full_text_chars} 文字）を超えるので送っていません。チェックを外すと差分だけを送れます",
    # 毎回付ける前提の文が送れない(ADR-0172 決定4)。どの行かは出さない
    "premise_refused": (
        "毎回付ける前提の文に、伏せる対象か送れない行があるため、AI に送っていません。"
        "設定の窓の「プロンプト」のタブの「毎回付ける前提」を直してください"
    ),
    # 比較の窓で全部の組をまとめて1回で送ったときの、答えの無い組(ADR-0152 決定7)
    "in_whole": "全部の組をまとめて1回で送りました。答えは最初の組の行にあります",
}
# 全部の組をまとめて1回で送ったときの、組の一覧の AI の列(HTML と CSV・AI の別の窓。ADR-0152 決定10)
LLM_IN_WHOLE = "全部の組をまとめて1回で送りました"
# 理由が cancelled で、送った後(transmitted が真)のときの文(ADR-0157)
LLM_CANCELLED_AFTER_SEND = "送った後に中止したため、答えはありません"
# API が 429 を返したとき(ADR-0168 決定4)。どれも最後に今の「(HTTP 429)」を付ける。秒は保存しない
LLM_RATE_LIMITED = "AI の回数の上限に達しました"
RATE_LIMIT_STATUS = 429


def _rate_limited(llm: LlmAnalysis) -> bool:
    return llm.reason == "api_error" and llm.detail.get("status_code") == RATE_LIMIT_STATUS


def llm_rate_limited(retry_after: int | None, *, cli: bool = False) -> str:
    """429 の文(比較の窓の AI の面と CLI の標準エラー。ADR-0168 決定4)。秒が分かれば秒、無ければ契約と支払いの上限の案内。"""
    again = "もう一度実行してください" if cli else "もう一度押してください"
    if retry_after is not None:
        return f"{LLM_RATE_LIMITED}。{retry_after:,} 秒ほど待ってから、{again}"
    return f"{LLM_RATE_LIMITED}。しばらく待ってから、{again}。続くときは、契約と支払いの上限も確かめてください"


def cli_api_error_line(text: str) -> str:
    """CLI の compare --llm(1組)の標準エラーに出す、API が返したエラーの文の行(ADR-0168 決定5)。文は掃除した後のもの。"""
    return f"API からのエラーの文（短く切り、キーらしい文字は伏せています）: {text}"


def llm_reason(llm: LlmAnalysis) -> str:
    """未実施の理由の文。理由が未知の値でも落ちない(分岐に使わない列挙のため)。"""
    text = _LLM_REASON.get(llm.reason or "", f"理由: {llm.reason}")
    if llm.reason == "cancelled" and llm.transmitted:
        # 送った後、答えが届いている途中で中止した(ADR-0157。途中の文字は答えにしない)
        text = LLM_CANCELLED_AFTER_SEND
    if llm.reason == "gave_up":
        text = text.format(give_up_after=_llm_give_up_after())
    if llm.reason == "full_text_too_long":
        text = full_text_too_long()
    if _rate_limited(llm):
        # 保存した結果・一括比較の列・CLI の text の欄(秒は保存しないので出さない。ADR-0168 決定4)
        text = f"{LLM_RATE_LIMITED}。しばらく待ってから、もう一度試してください"
    status_code = llm.detail.get("status_code")
    return f"{text}(HTTP {status_code})" if status_code is not None else text


def premise_refused_text() -> str:
    """毎回付ける前提の文が送れないとき(ADR-0172 決定4)。一括比較の窓が確認を出さずに知らせる文。どの行かは出さない。"""
    return _LLM_REASON["premise_refused"]


# CLI で前提が送れないときに足す案内(REQ-234 §6)
PREMISE_REFUSED_CLI_HINT = "（前提を付けずに送るには --llm-no-premise を付けてください）"


def premise_refused_cli() -> str:
    """CLI が比較も送信もせずに 2 で終わるときの文(「エラー: 」は main が付ける)。"""
    return f"{premise_refused_text()}{PREMISE_REFUSED_CLI_HINT}"


# CLI の --llm-no-premise(compare・batch。ADR-0172 決定3)のヘルプと、--llm も --llm-dry-run も無いときの断り
LLM_NO_PREMISE_HELP = "毎回付ける前提（設定の窓の「プロンプト」のタブ）を付けずに AI へ送る。--llm か --llm-dry-run と一緒に使う"
LLM_NO_PREMISE_ALONE = "--llm-no-premise は --llm か --llm-dry-run と一緒に指定してください"


def full_text_too_long() -> str:
    """変更後の全文か全体が上限を超えたので送らなかったとき(ADR-0158 決定5)。最初の解析と追加の質問で同じ文。"""
    from driftpilot.llm_analyzer.payload import MAX_FULL_TEXT_CHARS

    return _LLM_REASON["full_text_too_long"].format(max_full_text_chars=f"{MAX_FULL_TEXT_CHARS:,}")


def llm_truncated_lines(llm: LlmAnalysis) -> list[str]:
    """打ち切ったときの案内(2026-10-16 承認の文面)。件数の上限は「比較の対象が違う」信号として利用者に返す。

    全部の組をまとめて送った解析(source が whole。ADR-0152)は、1組の上限で打ち切った組があることを言う
    (全体の件数を1組の上限と比べる文にしない)。続く行は同じ。
    """
    if llm.source == "whole":
        head = f"1組の上限 {llm.max_diffs:,} 件で打ち切った組があります。"
    else:
        head = f"差分が {llm.diff_total:,} 件あり、{llm.max_diffs:,} 件で打ち切りました。"
    return [
        head,
        "差分がこの件数になるのは、比較の対象が違う場合があります",
        "(通常版と既定値込み版、別の機器、対の取り違え)。",
        "安全網の警告と区間の選択を確認してください。",
    ]


def llm_unsent_line(llm: LlmAnalysis) -> str | None:
    """送らなかった行の件数(ADR-0019 決定5)。比較の結果は変わらず、LLM の意見が付かないだけであることも言う。"""
    unsent = llm.unsent
    if unsent is None or not (unsent.lines or unsent.context_lines):
        return None
    head = f"{unsent.lines} 行は判定不能のため送っていない"
    if unsent.blocks:
        head += f"(うち PEM ブロック {unsent.blocks} 件)"
    if unsent.context_lines:
        head += f"。前後の文脈の {unsent.context_lines} 行も送っていない"
    return head + "。差分としては上に出ています(AI の意見が付かないだけです)"


def llm_unsent_shape_lines(llm: LlmAnalysis) -> list[str]:
    """送らなかった行の形の一覧(値は含まない)。伏せる表に足す候補になる(ADR-0019 決定6)。"""
    if llm.unsent is None:
        return []
    return unsent_shape_lines(llm.unsent.shapes)


def unsent_shape_lines(shapes: "list[UnsentShape] | tuple[UnsentShape, ...]") -> list[str]:
    """送らなかった行の形の一覧の行。1件の比較と一括比較(全組の合算)で同じ書き方にする。"""
    if not shapes:
        return []
    lines = ["送らなかった行の形(値は含まない。伏せる表に足す候補):"]
    for s in shapes:
        if s.kind == "pem_block":
            lines.append(f"  {s.count:4d} ブロック({s.lines} 行)  [{s.scope}] {s.shape}")
        else:
            lines.append(f"  {s.count:4d} 行  [{s.scope}] {s.shape}")
    return lines


def llm_section_lines(llm: LlmAnalysis) -> list[str]:
    """text 出力の「== LLM 解析 ==」の欄。回答は注釈であり、差分の判定に使っていないことを書く。"""
    lines = ["== AI 解析 =="]
    if llm.status == "done":
        lines.append(f"モデル: {llm.model}  日時: {llm.asked_at}")
        lines.append("注意: AI の回答は同じ入力でも毎回同じにはなりません。差分の判定には使っていません(差分は上のとおり)")
    else:
        lines.append(f"AI 解析: 未実施({llm_reason(llm)})")
    if llm.truncated:
        lines += llm_truncated_lines(llm)
    if (unsent := llm_unsent_line(llm)) is not None:
        lines.append(unsent)
    if llm.status == "done":
        lines += ["--- 回答 ---", llm.answer or "", "--- ここまで ---"]
    if llm.reason == "dry_run":
        lines += ["--- 送信内容(マスク後。このあとに答えの書き方の決まり(固定の文)だけを足して送ります) ---", llm.payload or "", "--- ここまで ---"]
        lines += llm_unsent_shape_lines(llm)
    elif llm.reason == "nothing_to_send":
        # 送る内容が空でも(--llm-dry-run を含む)、送らなかった形は見せる。表に足す候補になる(ADR-0019 決定6)
        lines.append("送信内容: なし(送れる差分がありません。送信していません)")
        lines += llm_unsent_shape_lines(llm)
    return lines


def llm_screen_lines(llm: LlmAnalysis, *, retry_after: int | None = None) -> list[str]:
    """pywebview の比較の画面の AI の面に出す行(REQ-206 §8 / ADR-0148 決定8)。llm_section_lines から頭の行を除いたもの。

    除くのは「== LLM 解析 ==」、答えがあるときのモデルと日時の行・注意の行、「--- 回答 ---」「--- ここまで ---」。
    送らなかった理由(未実施)・打ち切り・送らなかった行・送る内容の文は llm_section_lines と同じに出す。
    CLI の text・wx の画面・保存は llm_section_lines のまま(変えない)。
    retry_after は API が 429 で返した待つ秒(画面の状態だけが持つ。ADR-0168 決定4)。429 のときだけ使う。
    """
    lines: list[str] = []
    if llm.status != "done" and retry_after is not None and _rate_limited(llm):
        lines.append(f"AI 解析: 未実施({llm_rate_limited(retry_after)}(HTTP {RATE_LIMIT_STATUS}))")
    elif llm.status != "done":
        lines.append(f"AI 解析: 未実施({llm_reason(llm)})")
    if llm.truncated:
        # 打ち切りの知らせの後ろに、上限を変えられる場所を1行添える(ADR-0167 決定6。CLI・wx・保存には足さない)
        lines += [*llm_truncated_lines(llm), LIMITS_SCREEN_HINT]
    if (unsent := llm_unsent_line(llm)) is not None:
        lines.append(unsent)
    if llm.status == "done":
        lines.append(llm.answer or "")
    if llm.reason == "dry_run":
        lines += ["--- 送信内容(マスク後。このあとに答えの書き方の決まり(固定の文)だけを足して送ります) ---", llm.payload or ""]
        lines += llm_unsent_shape_lines(llm)
    elif llm.reason == "nothing_to_send":
        lines.append("送信内容: なし(送れる差分がありません。送信していません)")
        lines += llm_unsent_shape_lines(llm)
    return lines


def workdir_api_key_ignored(path: Path) -> str:
    """作業フォルダの設定ファイルに api_key があったときの1行(値は出さない)。"""
    return (
        f"注意: {path} の llm.api_key は読みません（作業フォルダのファイルからキーを読まない設計です）。"
        "キーは設定画面の「保管庫に保存」か、プロファイルごとの環境変数 DRIFTPILOT_LLM_API_KEY_<続き> に入れてください。"
        "プロファイルを使わないときは DRIFTPILOT_LLM_API_KEY か、利用者ごとの設定ファイルの llm.api_key"
    )


def workdir_model_ignored(profile_name: str) -> str:
    """AI の接続のプロファイルを使っていて、作業フォルダの driftpilot.toml に model・base_url があったときの1行(REQ-42 §4)。"""
    return f"注意: プロファイル『{profile_name}』を使っているので、作業フォルダの driftpilot.toml の model・base_url は使っていません"


def common_env_ignored_note() -> str:
    """プロファイルがあり、共通の環境変数も設定されているときの1行(ADR-0077 決定3。CLI と設定画面の帯。キーの値は出さない)。"""
    return "注意: DRIFTPILOT_LLM_API_KEY は使っていません。プロファイルごとに環境変数の名前を決めてください"


def llm_profiles_missing(path: Path) -> str:
    """--llm-profile を付けたのに、プロファイルのファイルが無いとき(送らない)。"""
    return f"{path}: AI プロファイルがありません。--llm-profile は使えません(送っていません)"


def llm_profile_not_found(name: str, names: list[str]) -> str:
    """--llm-profile の名前が無いとき(送らない)。あるプロファイルの名前だけを並べる(値・キーは出さない)。"""
    listed = "、".join(f"「{n}」" for n in names)
    return f"--llm-profile「{name}」というプロファイルはありません(送っていません)。あるプロファイル: {listed}"


def workdir_api_key_reason() -> str:
    """作業フォルダからキーを読まない理由(設定画面で workdir_api_key_ignored に添える。REQ-27 §B-2)。"""
    return "作業フォルダのファイルは git に入りやすいため、ここに書かれたキーは読みません（書かれていますが、読んでいません）"


# --- API キーの保管庫と設定画面(REQ-34 / ADR-0033)。キーの値はどの文にも入れない ---


def keyring_not_installed() -> str:
    return "keyring が入っていません（pip install 'driftpilot[llm]'）"


def keyring_unusable() -> str:
    return "この環境では保管庫を使えません。環境変数を使ってください"


_KEYRING_ACTION = {"read": "読め", "save": "保存でき", "delete": "削除でき"}


def keyring_failed(action: str, error_type: str) -> str:
    """保管庫の操作が例外で失敗したとき。例外の本文は使わず、型名だけを添える(本文に値が入りうるため)。"""
    return f"保管庫を{_KEYRING_ACTION[action]}ませんでした（{error_type}）"


def api_key_empty() -> str:
    return "API キーを入れてください。保存していません"


def api_key_bad_length(length: int, low: int, high: int) -> str:
    return f"API キーは {low}〜{high} 文字にしてください（入れたものは {length} 文字です）。保存していません"


def api_key_has_space() -> str:
    return "API キーに空白や改行を含めないでください。保存していません"


def api_key_saved(store: str) -> str:
    return f"{store}に保存しました"


def api_key_saved_env_wins() -> str:
    return "保存しました。ただし環境変数のキーが優先されています"


def api_key_deleted(store: str) -> str:
    return f"{store}から削除しました"


def api_key_not_stored() -> str:
    return "保存されていません"


def api_key_delete_confirm(store: str) -> str:
    return f"{store}に保存した DriftPilot の API キーを削除しますか？"


def plaintext_api_key_band() -> str:
    return "設定ファイルに API キーが平文で書かれています。下の欄から保管庫へ保存し、設定ファイルの api_key の行は手で消してください"


# --- 利用者ごとの設定ファイル(config.toml)を設定画面から書く(REQ-93 / ADR-0071)。キーの値はどの文にも入れない ---

# ひな形の2行目と「ひな形を作る」の後の案内(ADR-0071 で、画面が決まった行だけを書くようになった)
CONFIG_TEMPLATE_NOTE = (
    "設定画面の『保存』で、DriftPilot が上限と伏せる語の行を書き換えます。"
    "『保管庫に移す』では api_key の行を消します。ほかの行とコメントは変えません。"
)
# 上限の出どころ(ADR-0071 決定5)
LIMIT_SOURCE_LABEL = {"default": "既定", "user_config": "設定ファイル", "cli": "コマンドの指定"}
LIMITS_CHANGE_HINT = "設定画面か設定ファイルで変えられます"
# pywebview の比較の窓で、1組の上限で打ち切ったことを出すときに添える文(ADR-0167 決定6)。CLI・wx・一括比較の窓は使わない
LIMITS_SCREEN_HINT = "上限は設定画面の「比較と自動の動き」のタブで変えられます"
# wx の設定ファイルの枠の見出し(pywebview の設定画面は CONFIG_PANE_WEB。ADR-0167 決定5)
CONFIG_PANE = "設定ファイル（config.toml）: AI 解析の上限・伏せる語の追加"
# pywebview の設定画面の「設定ファイル」のタブの見出しと、上限の欄を移したことの案内(ADR-0167 決定5)
CONFIG_PANE_WEB = "設定ファイル（config.toml）: 伏せる語の追加"
SETTINGS_NOTE_LIMITS_MOVED = "AI に送る差分の上限は「比較と自動の動き」のタブにあります（保存先は同じ設定ファイルです）"
SETTINGS_AUTO_COMPARE = "ファイルが2つそろったら自動で比較を始める"
# 自動送信(ADR-0080 決定2。pywebview の設定画面だけ)
SETTINGS_AUTO_SEND_LLM = "比較の後に自動で AI に送る"
# pywebview の設定画面のタブ(ADR-0154 / REQ-214 §5)。「AI プロファイル」「プロンプト」は PROFILES_TITLE・PROMPT_TEMPLATE_TITLE
SETTINGS_TAB_AUTO = "比較と自動の動き"
SETTINGS_TAB_FILE = "設定ファイル"
SETTINGS_NOTE_AUTO_SEND = "オンにすると、比較が終わるたびに確認なしで、使用中の AI プロファイルへ差分を送ります"
SETTINGS_NOTE_SAVE_BOTH = "「保存」は「比較と自動の動き」と「設定ファイル」の両方のタブの欄を書きます"
# 「キャラクター（Mirarin）を表示する」のチェックと注記(ADR-0174 決定1。[gui] の character)
SETTINGS_CHARACTER = "キャラクター（Mirarin）を表示する"
SETTINGS_NOTE_CHARACTER = (
    "起動したとき、AI の考え中、一括比較の実行中、差分が無いときなどに、Mirarin の絵を添えます。"
    "次に窓を開いたときから変わります。"
)
AUTO_SEND_CONFIRM_TITLE = "比較の後に自動で AI に送る"


def auto_send_confirm(name: str, destination: str) -> str:
    """自動送信をオフからオンにするときの確かめ(ADR-0080 決定2)。既定のボタンは「いいえ」。キーの値は入れない。"""
    return "\n".join(
        [
            "比較の後に自動で AI に送るようにしますか？",
            f"接続の名前: {name}",
            f"接続先: {destination}",
            "比較の画面で比較が終わるたびに、確認なしで送ります。",
            "伏せる処理と送る上限はそのままです。送った全文は AI 解析の枠で見られます。",
            "一括比較では、実行の後に送る内容のリストを出します。「送る」を押すまで送りません。コマンドでは送りません。",
        ]
    )


def auto_send_unusable(reason: str) -> str:
    """使用中の接続が使えないとき(キーが無いなど)。自動送信はオンにせず、何も書かない。"""
    return f"使用中の接続が使えないため、自動送信はオンにできません（{reason}）。保存していません"

# wx の設定画面の上限の見出しとラベル。入力の誤りの文(limit_invalid)と今の値の文(limit_now_line)も使う
LIMITS_TITLE = "AI 解析の上限"
LIMIT_PER_JOB = "1組あたり"
LIMIT_TOTAL = "全体"
# pywebview の設定画面の上限の見出しとラベル(「比較と自動の動き」のタブ。ADR-0167 決定5)。数は既定値の定数から作る
LIMITS_TITLE_WEB = "AI に送る差分の上限"
LIMIT_PER_JOB_WEB = f"1 組あたり（既定 {DEFAULT_MAX_DIFFS:,} 件）"
LIMIT_TOTAL_WEB = f"全体（既定 {DEFAULT_MAX_TOTAL_DIFFS:,} 件）"
SECRET_WORDS_TITLE = "伏せる語の追加"
# pywebview の設定画面の枠の下の文(比較の画面の行も書く。REQ-113 §5。AI への指示文は REQ-116 で別の枠に移した)。
# wx は CONFIG_SAVE_NOTE_WX のまま
CONFIG_SAVE_NOTE = (
    "「保存」を押したときだけ、設定ファイルの上限・伏せる語・比較の画面の行を書き換えます"
    "（ほかの行とコメントは変えません）"
)
CONFIG_SAVE_NOTE_WX = "「保存」を押したときだけ、設定ファイルの上限と伏せる語の行を書き換えます（ほかの行とコメントは変えません）"
CONFIG_NOT_CHANGED = "変更がありません。設定ファイルは書いていません"
CONFIG_CHANGED_ELSEWHERE = "設定ファイルがほかで変わりました。読み直してから保存してください"
CONFIG_SAVE_TITLE = "設定ファイル"
MIGRATE_TITLE = "保管庫に移す"


def template_created(path: Path) -> str:
    """「ひな形を作る」(pywebview の画面は「設定ファイルを作る」)の後の案内。"""
    return f"{path} を作りました。{CONFIG_TEMPLATE_NOTE}"


def llm_limit_lines(max_diffs: int, max_diffs_source: str, max_total_diffs: int, max_total_diffs_source: str) -> list[str]:
    """一括比較の送る前の確認に出す上限と出どころ(ADR-0071 決定5)。出どころが違うときは、それぞれに括弧を付ける。"""
    per_job, total = LIMIT_SOURCE_LABEL[max_diffs_source], LIMIT_SOURCE_LABEL[max_total_diffs_source]
    if per_job == total:
        line = f"上限: 1組 {max_diffs:,} 件・全体 {max_total_diffs:,} 件（{per_job}）"
    else:
        line = f"上限: 1組 {max_diffs:,} 件（{per_job}）・全体 {max_total_diffs:,} 件（{total}）"
    return [line, LIMITS_CHANGE_HINT]


def limit_now_line(label: str, value: int, source: str) -> str:
    """設定画面の上限の欄の下に薄く出す、今の値と出どころ。"""
    return f"{label}: 今は {value:,} 件（{LIMIT_SOURCE_LABEL[source]}）"


def workdir_limits_ignored(path: Path, names: "list[str] | tuple[str, ...]") -> str:
    """作業フォルダの driftpilot.toml に上限・指示文が書いてあったときの1行(読まない。CLI と画面で同じ文)。

    指示文(prompt_template)は指示文に合う文にする(REQ-113 §5)。上限と両方あるときは、2つを1行につなげる。
    """
    limits = [name for name in names if name != "prompt_template"]
    parts = []
    if limits:
        items = "・".join(f"llm.{name}" for name in limits)
        parts.append(f"{path} の {items} は読みません。上限は利用者ごとの設定ファイル（config.toml）かコマンドの指定で変えます")
    if "prompt_template" in names:
        parts.append(
            f"作業フォルダのプロンプトは使いません（{path} の llm.prompt_template）。"
            "プロンプトは設定画面の「プロンプト」の枠で変えます"
        )
    return "注意: " + "。".join(parts)


def limit_invalid(label: str) -> str:
    return f"AI 解析の上限（{label}）は 1 以上の整数にしてください。保存していません"


def secret_word_has_space(lines: "list[int]") -> str:
    numbers = "・".join(str(n) for n in lines)
    return f"伏せる語に空白を含む行があります（{numbers} 行目）。1行に1語で書いてください。保存していません"


def config_saved(path: Path) -> str:
    return f"{path} に保存しました"


def config_not_saved(error: str) -> str:
    return f"保存できませんでした: {error}"


def secret_words_in_effect(summary: str) -> str:
    """設定画面の伏せる語の欄の下に出す、いま効いている語(作業フォルダの語を含む)。"""
    return f"いま効いている語: {summary}"


# --- AI への指示文のひな形(REQ-109 / ADR-0075)。どの文にもひな形の中身は入れない(場所・項目名・理由だけ) ---

PROMPT_TEMPLATE_TITLE = "プロンプト"
# 設定画面の欄の下の説明(ADR-0167 決定1〜3)。太字の決まり1行・「使える印」の表(既定は折りたたみ)・箇条書きの注意2つ
PROMPT_DIFF_ONCE_NOTE = "{{diff}}（差分の本文）は必ず 1 回だけ書いてください"
PROMPT_MARKS_TITLE = "使える印"
PROMPT_MARKS_COL_MARK = "印"
PROMPT_MARKS_COL_MEANING = "意味"
# 印ごとの意味(表の行。並びは PROMPT_MARKS。テストで PROMPT_MARKS と鍵がそろうことを確かめる)
PROMPT_MARK_MEANINGS = {
    "diff": "差分の本文。必ず 1 回だけ書きます",
    "diff_format": "差分の形式の説明（構造化比較の差分か、行の差分かなど）",
    "diff_total": "差分の全部の件数",
    "sent_range": "先頭から送った件数（打ち切ったときは上限まで）",
    "max_diffs": "1 組あたりに送る差分の上限",
    "notes": "打ち切りと、送らなかった行の知らせ（無ければ空）",
}
PROMPT_NOTE_PLAIN = "この欄の文は伏せずに AI へ送ります。機器の設定やパスワードは書かないでください"
PROMPT_NOTE_PREMISE = "最初から入っている前提の文を消すと、AI が <secret:N> や打ち切りを取り違えることがあります"


def _mark(name: str) -> str:
    """印の書き方({{名前}})。"""
    return "{{" + name + "}}"


# ひな形が使えない理由の句(llm_analyzer.prompt.check_prompt_template の種類ごと)。項目名・見出しの後ろにつなぐ。
# unknown_mark は設定画面の「使える印」の表を指し、印の名前の並びは PROMPT_MARKS から作る(ADR-0167 決定4)
PROMPT_TEMPLATE_PROBLEM = {
    "type": "は文字列にしてください",
    "too_long": "は 20,000 文字以内にしてください",
    "unknown_mark": (
        f"に使えない印があります。使える印は欄の下の「{PROMPT_MARKS_TITLE}」の表にあります"
        f"（{'・'.join(_mark(name) for name in PROMPT_MARKS)}）"
    ),
    "no_diff": "に差分を入れる印 {{diff}} がありません",
    "repeated_diff": "の差分を入れる印 {{diff}} は1回だけにしてください",
}


def prompt_template_invalid(kind: str) -> str:
    """設定画面で、指示文の欄の文が使えないとき(書いていない)。"""
    return f"{PROMPT_TEMPLATE_TITLE}{PROMPT_TEMPLATE_PROBLEM[kind]}。保存していません"


# --- AI への指示文のプロファイル(REQ-116 / ADR-0079)。どの文にも指示文の本文は入れない(名前と理由だけ) ---

PROMPT_FIELD_TEMPLATE = "本文"
# 一覧の行の2行目(最初から入っている3つは id ごと、利用者のものは共通の文)
PROMPT_ROW_DETAIL = {
    "builtin-default": "差分の要点と影響（show の比較は「状態の確認」で送ります）・最初から入っています",
    "builtin-incident": "障害の調査の手がかり・最初から入っています",
    "builtin-status": "show の出力の前後の確認・最初から入っています",
}
PROMPT_ROW_USER = "自分で作ったプロンプト"
# 最初から入っている3つを選んだときに、欄の下に薄く出す文
PROMPT_BUILTIN_NOTE = "最初から入っているプロンプトは変えられません。変えるときは「複製」してから直してください"
PROMPT_BUILTIN_READ_ONLY = "最初から入っているプロンプトは保存・削除できません。変えるときは「複製」してから直してください"
PROMPT_DISCARD_CHANGES = "保存していない変更があります。変更を捨てて、別のプロンプトを選びますか？"


def prompt_profile_saved(name: str) -> str:
    return f"プロンプト「{name}」を保存しました"


def prompt_profile_added(name: str) -> str:
    return f"プロンプト「{name}」を作りました。名前と本文を直して「保存」を押してください"


def prompt_profile_duplicated(name: str) -> str:
    return f"プロンプト「{name}」を作りました"


def prompt_profile_deleted(name: str) -> str:
    return f"プロンプト「{name}」を削除しました"


def prompt_profile_now_in_use(name: str) -> str:
    return f"プロンプト「{name}」を使うようにしました"


# --- 毎回付ける前提(ADR-0172 決定6 / REQ-234 §5.3)。どの文にも前提の中身は入れない ---

SETTINGS_PREMISE_TITLE = "毎回付ける前提"
SETTINGS_PREMISE_SAVE = "前提を保存"
# 文字数の表示(JS が数字を差し込むだけ。判定は Python)
SETTINGS_PREMISE_COUNT = "{count} / {max} 文字"
# 欄の下の案内(3 行。view から 1 行ずつ li に入れる)
SETTINGS_PREMISE_NOTES = (
    "どのプロンプトでも、AI へ送る文に毎回付けます（比較・まとめての比較・追加の質問・一括比較・CLI）",
    "伏せずに送ります。実在のホスト名や IP を書けば AI へ送られます。パスワードや鍵は書かないでください",
    "4,000 文字まで。空にすると付けません。送信前の確認の全文に出ます",
)
# 保存の断りと結果
PREMISE_SECRET_REFUSED = "前提の文に、伏せる対象か送れない行があるため、保存していません（どの行かは出しません。追加の質問と同じ確かめです）"
PREMISE_TOO_LONG = "前提は 4,000 文字までです。保存していません"
PREMISE_SAVED = "前提を保存しました"
# 「閉じる」を押したとき、前提の欄に保存していない変更があるときの確かめ(ボタンは SETTINGS_BUTTON_CLOSE と DIALOG_CANCEL)
PREMISE_CLOSE_CONFIRM = "毎回付ける前提に、保存していない変更があります。保存せずに閉じますか？"


def premise_unreadable(error: str) -> str:
    """前提のファイルを読めないときの帯と、保存の断りの文。error は SettingsError の文(場所と理由の種類だけ)。"""
    return f"毎回付ける前提のファイルを読めません。{error}"


def premise_settings_unreadable(error: str) -> str:
    """設定ファイルを読めず、伏せる語が分からないので前提を保存しないときの文(確かめられないまま書かない)。"""
    return f"設定を読めないため、伏せる語で確かめられません。前提を保存していません。{error}"


def prompt_profile_delete_confirm(name: str) -> str:
    return f"プロンプト「{name}」を削除しますか？"


def prompt_profile_delete_in_use() -> str:
    """使用中の利用者の指示文を消そうとしたとき(断る。判断の記録 3)。"""
    return "使用中のプロンプトは削除できません。先に別のプロンプトを選んで「このプロファイルを使う」を押してください"


def prompt_name_taken(name: str) -> str:
    return f"「{name}」という名前のプロンプトは既にあります（最初から入っている名前とも重ねられません。大文字・小文字は区別しません）。保存していません"


def new_prompt_name(n: int) -> str:
    """指示文の「新規作成」の名前。重ならない番号を付ける(1 は番号なし)。"""
    return "新しいプロンプト" if n == 1 else f"新しいプロンプト {n}"


def prompt_profiles_unreadable(error: str) -> str:
    """指示文のプロファイルのファイルを読めないとき(設定画面の枠に出す。AI の接続のプロファイルは編集できる)。"""
    return f"プロンプトのプロファイルを読めません: {error}"


def prompt_profile_not_found(name: str, names: list[str]) -> str:
    """--llm-prompt-profile の名前が無いとき(送らない。別の文に切り替えない)。ある名前だけを並べる(本文は出さない)。"""
    listed = "、".join(f"「{n}」" for n in names)
    return f"--llm-prompt-profile「{name}」というプロンプトのプロファイルはありません(送っていません)。あるプロンプトのプロファイル: {listed}"


def prompt_profile_name_shadowed(name: str) -> str:
    """--llm-prompt-profile の名前が、最初から入っている指示文と利用者の指示文の両方に当たったときの1行(REQ-204。
    最初から入っているほうで送る。黙って別の文で送らないため。ADR-0079 決定2)。本文は出さない。"""
    return (
        f"注意: --llm-prompt-profile「{name}」は、最初から入っているプロンプトと自分で作ったプロンプトの両方の名前です。"
        "最初から入っているほうを使います。自分で作ったほうを使うときは、設定画面で名前を変えてください"
    )


def user_prompt_template_ignored(path: Path) -> str:
    """利用者ごとの設定ファイルに llm.prompt_template が書いてあったときの1行(読まない。ADR-0079 決定5)。"""
    return f"注意: {path} の llm.prompt_template は読みません。プロンプトは設定画面の「プロンプト」の枠で変えます"


def workdir_secret_words_line(words: "list[str] | tuple[str, ...]") -> str:
    """作業フォルダの driftpilot.toml の伏せる語(画面では変えない。読むだけで見せる)。"""
    listed = ", ".join(words) if words else "なし"
    return f"作業フォルダの driftpilot.toml の語（ここでは変えられません）: {listed}"


def migrate_confirm(store: str) -> str:
    return (
        f"設定ファイルの API キーを{store}の DriftPilot/LLM/default/APIKey に保存し、"
        "保存できたことを確かめてから、設定ファイルの api_key の行を消します。よろしいですか？"
    )


def migrate_overwrite_confirm(store: str) -> str:
    return f"{store}には、すでに別の API キーが保存されています。設定ファイルのキーで上書きしますか？"


def migrate_done(store: str) -> str:
    return f"API キーを{store}に移し、設定ファイルの api_key の行を消しました"


def migrate_store_unusable(reason: str) -> str:
    return f"保管庫を使えないので、「保管庫に移す」は使えません（{reason}）"


def migrate_save_failed(reason: str) -> str:
    return f"保管庫に保存できませんでした（{reason}）。設定ファイルは変えていません"


def migrate_mismatch() -> str:
    return "保管庫から読み直したキーが、設定ファイルのキーと一致しませんでした。設定ファイルは変えていません"


def migrate_file_changed() -> str:
    return f"{CONFIG_CHANGED_ELSEWHERE}。設定ファイルの api_key の行は消していません"


def migrate_remove_failed(error: str) -> str:
    return f"キーは保管庫に保存しましたが、設定ファイルの api_key の行を消せませんでした: {error}"


def migrate_no_key() -> str:
    return "設定ファイルに API キーがありません。設定ファイルは変えていません"


def migrate_overwrite_declined() -> str:
    return "上書きしませんでした。保管庫と設定ファイルは変えていません"


SETTINGS_TITLE = "DriftPilot — 設定"
STATUS_READY = "AI 解析を使えます"
# 足りないものの文。右上の状態の印と、接続テストの no_model / no_key / unavailable の書き出しで共有する(REQ-36 §5b)
STATUS_NO_MODEL = "モデルが設定されていません"
STATUS_NO_KEY = "API キーがありません"
NO_LLM_EXTRA = "AI の部品が入っていません"
LLM_EXTRA_INSTALL = "pip install 'driftpilot[llm]'"
STATUS_NO_LLM_EXTRA = f"{NO_LLM_EXTRA}（{LLM_EXTRA_INSTALL}）"
STATUS_UNREADABLE = "設定を読めません"
# 接続先に <リソース名> などの目印が残っているときの右上の印(REQ-108 §2)。接続テストの文の書き出しと同じ
STATUS_PLACEHOLDER = "接続先の <リソース名> を書き換えてください"
KEY_STATE_NONE = "なし"
KEY_STATE_IN_USE = "使用中"
KEY_STATE_UNUSED = "あり（使っていない）"
KEY_NOT_SET = "未設定"
READ_ONLY_NOTE = "1行に1語で書きます。空白を含む語は書けません"


def key_state_unusable(reason: str) -> str:
    return f"使えない（{reason}）"


def key_summary(display: str) -> str:
    return f"保存済み {display}"


def key_source_line(number: int, label: str, state: str) -> str:
    return f"{number} {label} — {state}"


def key_store_note(store: str, service: str) -> str:
    return f"保存先: {store}・名前 {service}。保存・削除のあと、入力欄は空になります"


def setx_command(env_name: str) -> str:
    """Windows で環境変数を足すコマンド。設定のしかたでは、これだけで1行にする(REQ-37 §3)。"""
    return f"setx {env_name} \"<キー>\""


def key_help_command_lines(env_name: str | None, platform: str) -> list[str]:
    """key_help_lines のうち、コマンドだけの行(画面で途中を折り返さない)。Windows の手順を出す OS で setx の1行。

    env_name が None(環境変数を使わないプロファイル)なら無い。
    """
    return [] if env_name is None or platform == "darwin" else [setx_command(env_name)]


def key_help_lines(env_name: str | None, platform: str) -> list[str]:
    """キーの設定のしかた(設定画面に常に出す。REQ-36 §3)。保管庫への保存が先、環境変数は補足。

    環境変数の手順は、その OS の分だけ出す(Windows・Mac のどちらでもなければ両方)。OS の見分け方は store_name と同じ。
    Windows のコマンドは説明の文と別の行にする(REQ-37 §3)。「PowerShell なら…」は文の中で改行し、新しい行で始める(REQ-92 §1)。
    env_name は選んだプロファイルの環境変数の名前(ADR-0077)。None なら環境変数の手順を出さない。
    """
    if env_name is None:
        return ["設定のしかた:", "① 上の欄にキーを貼り付けて「保管庫に保存」を押します"]
    windows = [
        "Windows: 「システムのプロパティ → 環境変数」で利用者の環境変数に足します。\n"
        "PowerShell なら次の1行です（開き直した画面から効きます）",
        setx_command(env_name),
    ]
    mac = [f"Mac: シェルの設定ファイル（~/.zshrc など）に export {env_name}=\"<キー>\" と書きます"]
    if platform.startswith("win"):
        steps = windows
    elif platform == "darwin":
        steps = mac
    else:
        steps = [*windows, *mac]
    return [
        "設定のしかた:",
        "① 上の欄にキーを貼り付けて「保管庫に保存」を押します",
        f"② コマンドや自動実行で使うときは、環境変数 {env_name} に入れます（保管庫より優先されます）",
        *steps,
    ]


# --- AI 接続のプロファイル(REQ-43 / ADR-0035 決定5)。設定画面の一覧と編集。キーの値はどの文にも入れない ---

PROFILES_TITLE = "AI プロファイル"
PROFILE_UNSAVED_DEFAULT = "既定（まだ保存していません）"
PROFILE_MARK_IN_USE = "使用中"
PROFILE_MARK_NO_KEY = "キー未設定"
PROFILE_ALREADY_IN_USE = "使用中です"
PROFILE_EDIT_TITLE = "選んだプロファイル"
PROFILE_KEY_BOX = "このプロファイルの API キー"
KEY_SOURCES_TITLE = "読み元（上ほど優先）"  # キーの枠の見出し(REQ-92 §1 で括弧を全角にした)
FINGERPRINTS_TITLE = "いま効いている表（不具合の報告に使います）"  # 「診断」の見出し
PROFILE_DISCARD_CHANGES = "保存していない変更があります。変更を捨てて、別のプロファイルを選びますか？"
PROFILE_SAVE_FIRST = "保存していない変更があります。先に「保存」を押してください"
# 設定画面のボタン・欄の名前・知らせの題(REQ-104 §4。pywebview の設定画面は HTML に文を書かず、ここから取る)。
# 文は wx の設定画面(gui/settings_frame.py)に書いてあるものと同じ(テストが同じであることを確かめる)
SETTINGS_BUTTON_MIGRATE = "保管庫に移す"
SETTINGS_BUTTON_ADD = "追加"  # wx の画面のボタン。pywebview の画面は SETTINGS_BUTTON_NEW(REQ-112)
SETTINGS_BUTTON_NEW = "新規作成"
SETTINGS_BUTTON_DUPLICATE = "複製"
SETTINGS_BUTTON_DELETE = "削除…"
SETTINGS_BUTTON_SAVE = "保存"
SETTINGS_BUTTON_USE = "このプロファイルを使う"
SETTINGS_BUTTON_SAVE_KEY = "保管庫に保存"
SETTINGS_BUTTON_OPEN_FOLDER = "フォルダを開く"
SETTINGS_BUTTON_TEMPLATE = "設定ファイルを作る"  # pywebview の画面だけ(REQ-113 §2)。wx の画面は「ひな形を作る」のまま
SETTINGS_BUTTON_CLOSE = "閉じる"
SETTINGS_FIELD_NAME = "名前"
SETTINGS_FIELD_API_FORMAT = "API の形式"
SETTINGS_FIELD_MODEL = "モデル"
SETTINGS_FIELD_BASE_URL = "接続先"
SETTINGS_API_KEY_TITLE = "API キー"
SETTINGS_API_KEY_DELETE_TITLE = "API キーの削除"
SETTINGS_TEMPLATE_FAILED_TITLE = "ひな形を作れません"
SETTINGS_TEMPLATE_CREATED_TITLE = "ひな形を作りました"
SETTINGS_DIAGNOSTICS = "診断"
SETTINGS_VERSIONS_TITLE = "版"
SETTINGS_CONFIG_FILE_TITLE = "設定ファイル"
SETTINGS_CONFIG_LOCATION = "場所"
# 確かめ・知らせのダイアログのボタン(wx の MessageDialog の「はい」「いいえ」・MessageBox の「OK」と同じ)
DIALOG_YES = "はい"
DIALOG_NO = "いいえ"
DIALOG_OK = "OK"


def fingerprint_line(count: int, digest: str) -> str:
    """「診断」の指紋の表の1行の値(件数と指紋)。"""
    return f"{count} 件  指紋 {digest}"
# 環境変数の名前を決めていないプロファイルの、読み元の1行目の状態(ADR-0077。「環境変数 — 使いません」)
KEY_STATE_ENV_NOT_USED = "使いません"
# 設定ファイルの llm.api_key は id が default のプロファイルでだけ読む(REQ-42 §4)
KEY_STATE_NOT_FOR_PROFILE = "あり（このプロファイルでは使いません）"
# API の形式の表示名。一覧は短く、編集の選択肢は正式な名前を添える
API_FORMAT_SHORT = {
    "chat-completions": "OpenAI 互換",
    "responses": "OpenAI Responses",
    "anthropic-messages": "Claude",
    "custom-http-json": "カスタム HTTP JSON",
    "codex-cli": "Codex CLI",
    "claude-code-cli": "Claude Code CLI",
}
API_FORMAT_CHOICE = {
    "chat-completions": "OpenAI 互換",
    "responses": "OpenAI Responses",
    "anthropic-messages": "Claude（Anthropic Messages）",
    "custom-http-json": "カスタム HTTP JSON",
    "codex-cli": "Codex CLI",
    "claude-code-cli": "Claude Code CLI",
}
# 送り方の細かい設定(REQ-100 §3〜§6。pywebview の設定画面だけ)。欄の名前・注記・選択肢
SETTINGS_FIELD_TEMPLATE = "種類"  # プロファイルのひな形の選択(設定ファイルのひな形と取り違えないため)
SETTINGS_FIELD_AUTH = "認証"
SETTINGS_WIRE_PANE = "送り方の細かい設定（上限・Thinking）"  # pywebview の設定画面だけが読む(ADR-0157 決定12)
SETTINGS_FIELD_MAX_TOKENS = "送る上限"
SETTINGS_FIELD_MAX_TOKENS_NAME = "上限の送信名"
SETTINGS_NOTE_MAX_TOKENS_NAME = "空なら max_completion_tokens（Responses は max_output_tokens）"
SETTINGS_FIELD_THINKING = "thinking"
SETTINGS_FIELD_THINKING_NAME = "送る名前"
SETTINGS_FIELD_THINKING_ON = "ON の値（JSON）"
SETTINGS_FIELD_THINKING_OFF = "OFF の値（JSON）"
SETTINGS_NOTE_THINKING = "送る名前が空なら、thinking は送りません"
SETTINGS_FIELD_QUERY = "問い合わせの値"
SETTINGS_NOTE_QUERY = "1行に キー=値 で書きます。接続先の URL の後ろに付けて送ります"
AUTH_HEADER_CHOICE = {"bearer": "Authorization: Bearer", "api-key": "api-key（Azure OpenAI）"}
THINKING_CHOICE = {"off": "OFF", "on": "ON"}
# プロファイルのひな形の表示名(値と並びは screen_model/settings_model.py の PROFILE_TEMPLATES)
PROFILE_TEMPLATE_LABEL = {
    "openai": "OpenAI",
    "openai-responses": "OpenAI Responses",
    "azure-openai": "Azure OpenAI",
    "anthropic": "Claude（Anthropic）",
    "qwen": "Qwen",
    "deepseek": "DeepSeek",
    "local": "ローカル AI",
    "codex-cli": "Codex CLI",
    "claude-code-cli": "Claude Code CLI",
    "custom": "独自 API",
}
# 送り方の値で保存できないときに、どの欄かを言う名前(値は入れない)
_WIRE_FIELD_LABEL = {
    "wire_names": SETTINGS_FIELD_MAX_TOKENS_NAME,
    "thinking_param": f"thinking の{SETTINGS_FIELD_THINKING_NAME}",
    "query": f"{SETTINGS_FIELD_QUERY}のキー",
}


def wire_max_tokens_invalid() -> str:
    return f"{SETTINGS_FIELD_MAX_TOKENS}は 1 以上の整数にしてください（空なら送りません）。保存していません"


def wire_json_invalid(label: str) -> str:
    """thinking の ON の値・OFF の値が JSON として読めないとき。label は欄の名前。"""
    return f"thinking の「{label}」を JSON として読めません。保存していません"


def wire_reserved(field: str) -> str:
    """予約された名前(model・messages など)を使ったとき。field は wire_problem の欄の名前。"""
    return f"{_WIRE_FIELD_LABEL[field]}に、予約された名前（model・messages など）は使えません。保存していません"


def wire_keylike(field: str) -> str:
    """キーらしい名前を使ったとき。field は wire_problem の欄の名前。"""
    return f"{_WIRE_FIELD_LABEL[field]}に、キーらしい名前は書けません。キーは保管庫に保存してください。保存していません"


def query_line_invalid(number: int) -> str:
    return f"{SETTINGS_FIELD_QUERY}の {number} 行目が キー=値 の形ではありません。保存していません"


def query_key_empty(number: int) -> str:
    return f"{SETTINGS_FIELD_QUERY}の {number} 行目のキーが空です。保存していません"


def query_key_repeated(number: int) -> str:
    return f"{SETTINGS_FIELD_QUERY}の {number} 行目のキーは、前の行と同じです。保存していません"


def base_url_has_extras() -> str:
    return f"{SETTINGS_FIELD_BASE_URL}に ? と # は書けません。{SETTINGS_FIELD_QUERY}の欄に書いてください。保存していません"


# custom-http-json の定義の欄(REQ-101 §4。pywebview の設定画面だけ)。欄の名前・注記・選択肢・知らせ。値は入れない
SETTINGS_FIELD_CUSTOM_URL = "送り先の URL"
SETTINGS_FIELD_CUSTOM_HEADERS = "固定の見出し"
SETTINGS_FIELD_CUSTOM_BODY = "要求の本文"
SETTINGS_FIELD_ANSWER_PATH = "回答の位置"
SETTINGS_NOTE_CUSTOM_HEADERS = "1行に 名前: 値 で書きます。キーは書かずに、認証を選んで保管庫に保存してください"
SETTINGS_NOTE_CUSTOM_BODY = "JSON で書きます。文字列の中の {{model}}・{{prompt}}・{{max_output_tokens}} を差し込んで送ります"
SETTINGS_NOTE_ANSWER_PATH = "choices.0.message.content の形（. で区切り、数はリストの番号）"
SETTINGS_SHAPE_TITLE = "応答の形（選ぶと回答の位置に入ります。値は表示しません）"
SETTINGS_BUTTON_CUSTOM_EXPORT = "定義を書き出す…"
SETTINGS_BUTTON_CUSTOM_IMPORT = "定義を読み込む…"
CUSTOM_AUTH_CHOICE = {
    "bearer": "Authorization: Bearer",
    "api-key": "api-key",
    "x-api-key": "x-api-key",
    "none": "なし（キーを送らない）",
}
# 応答の形の木の型の名前(custom_http.SHAPE_KINDS)
SHAPE_TYPE_LABEL = {
    "string": "文字列",
    "number": "数",
    "boolean": "真偽",
    "null": "null",
    "object": "オブジェクト",
    "array": "リスト",
}
CUSTOM_FILE_TITLE = "カスタム HTTP JSON の定義"
CUSTOM_IMPORT_CHANGED = "確かめた後にファイルが変わったので、読み込んでいません。もう一度「定義を読み込む…」を押してください"
CUSTOM_FORMAT_REQUIRED = "API の形式を「カスタム HTTP JSON」にして保存してから使ってください"
_NOT_SAVED = "保存していません"
# 定義の欄が決まりに反したときの文(custom_http.custom_problem の欄と種類。固定の見出しは行の番号を添える)
_CUSTOM_PROBLEM_MESSAGE = {
    ("url", "empty"): f"{SETTINGS_FIELD_CUSTOM_URL} を入れてください",
    ("url", "fragment"): f"{SETTINGS_FIELD_CUSTOM_URL} に # は書けません",
    ("url", "invalid"): f"{SETTINGS_FIELD_CUSTOM_URL} に空白や ASCII でない文字は書けません",
    ("url", "scheme"): f"{SETTINGS_FIELD_CUSTOM_URL} は https にしてください（http は localhost だけ）",
    ("url", "keylike"): f"{SETTINGS_FIELD_CUSTOM_URL} の問い合わせに、キーらしい名前は書けません。キーは保管庫に保存してください",
    ("auth", "choice"): f"{SETTINGS_FIELD_AUTH}の選択が違います",
    ("headers", "line"): "が 名前: 値 の形ではありません",
    ("headers", "name_empty"): "の名前が空です",
    ("headers", "name"): "の名前に使えない文字があります",
    ("headers", "value"): "の値に、改行や ASCII でない文字は書けません",
    ("headers", "repeated"): "の名前は、前の行と同じです（大文字・小文字は区別しません）",
    ("headers", "keylike"): "に、キーらしい名前は書けません。キーは認証を選んで保管庫に保存してください",
    ("headers", "own"): "の名前は DriftPilot が付けるので書けません（Content-Type など）",
    ("body", "json"): f"{SETTINGS_FIELD_CUSTOM_BODY}を JSON として読めません",
    ("body", "object"): f"{SETTINGS_FIELD_CUSTOM_BODY}は JSON のオブジェクト（{{ }} で囲んだもの）にしてください",
    ("body", "unknown_mark"): (
        f"{SETTINGS_FIELD_CUSTOM_BODY}に知らない差し込みの印があります（{{{{model}}}}・{{{{prompt}}}}・"
        "{{max_output_tokens}} だけ使えます）"
    ),
    ("body", "no_prompt"): f"{SETTINGS_FIELD_CUSTOM_BODY}に {{{{prompt}}}} がありません",
    ("answer_path", "empty"): f"{SETTINGS_FIELD_ANSWER_PATH}を入れてください",
    ("answer_path", "shape"): f"{SETTINGS_FIELD_ANSWER_PATH}は choices.0.message.content の形にしてください",
}


def custom_field_invalid(field: str, kind: str, line: int | None = None) -> str:
    """定義の欄で保存できないときの文(どの欄か・何行目かだけ。値は入れない)。"""
    text = _CUSTOM_PROBLEM_MESSAGE.get((field, kind))
    if field == "headers":
        where = f"{SETTINGS_FIELD_CUSTOM_HEADERS}の {line} 行目" if line is not None else SETTINGS_FIELD_CUSTOM_HEADERS
        text = where + (text or "の形が違います")
    return f"{text or '定義の形が違います'}。{_NOT_SAVED}"


def custom_exported(path: Path) -> str:
    return f"定義を書き出しました（API キーは入れていません）: {path}"


def custom_export_failed(reason: str) -> str:
    return f"定義を書き出せません: {reason}"


def custom_import_confirm(name: str) -> str:
    return f"選んだプロファイル「{name}」の定義を置き換えます。よろしいですか？\nAPI キーは読み込みません"


def custom_imported(name: str) -> str:
    return f"プロファイル「{name}」の定義を読み込みました"


def custom_import_unreadable(reason: str) -> str:
    return f"定義のファイルを読み込めません: {reason}。読み込んでいません"


# プロファイルをファイルにまとめて書き出す・取り込む(ADR-0082・REQ-119。pywebview の設定画面の窓の下の操作の行)。
# API キーは入れない。文に API キーの値は入れない(ファイルの名前・接続先は利用者が確かめるために出す)
SETTINGS_BUTTON_EXPORT_PROFILES = "プロファイルを書き出す…"
SETTINGS_BUTTON_IMPORT_PROFILES = "プロファイルを取り込む…"
PROFILE_BUNDLE_TITLE = "プロファイルの書き出し・取り込み"
PROFILE_BUNDLE_NO_KEY = "API キーなし"
PROFILE_BUNDLE_DEFAULT_URL = "形式の既定の接続先"
PROFILE_BUNDLE_LOCAL_CLI = "手元の CLI"
PROFILE_BUNDLE_CHANGED = (
    "確かめた後にファイルか今のプロファイルが変わったので、取り込んでいません。もう一度「プロファイルを取り込む…」を押してください"
)


def imported_profile_name(name: str, n: int) -> str:
    """取り込んだプロファイルの名前が既存と重なるときの名前(1 は番号なし)。"""
    return f"{name} （取り込み）" if n == 1 else f"{name} （取り込み {n}）"


def profile_bundle_export_confirm(connections: int, prompts: int) -> str:
    return (
        f"保存してある AI プロファイル {connections} 件と、自分で作ったプロンプト {prompts} 件を書き出します。\n"
        "API キーは入りません。よろしいですか？"
    )


def profile_bundle_exported(path: Path, connections: int, prompts: int) -> str:
    return f"AI プロファイル {connections} 件とプロンプト {prompts} 件を書き出しました（API キーは入れていません）: {path}"


def profile_bundle_export_failed(reason: str) -> str:
    return f"プロファイルを書き出せません: {reason}。書き出していません"


def profile_bundle_connection_line(name: str, format_label: str, destination: str) -> str:
    """取り込みの確かめの一覧の、接続の1行。"""
    return f"・{name}（{format_label}）: {destination} — {PROFILE_BUNDLE_NO_KEY}"


def profile_bundle_import_confirm(connection_lines: list[str], prompt_names: list[str]) -> str:
    """取り込みの確かめ。接続は名前と接続先の一覧、指示文は名前の一覧。"""
    lines = [
        "次のプロファイルを取り込みます。既存のプロファイルは変えません。",
        "取り込んだ接続には API キーがありません。あとで保管庫に保存してください",
        "",
        f"AI プロファイル {len(connection_lines)} 件:",
        *(connection_lines or ["（なし）"]),
        "",
        f"プロンプト {len(prompt_names)} 件:",
        *([f"・{name}" for name in prompt_names] or ["（なし）"]),
        "",
        "よろしいですか？",
    ]
    return "\n".join(lines)


def profile_bundle_imported(connections: int, prompts: int) -> str:
    return (
        f"AI プロファイル {connections} 件とプロンプト {prompts} 件を取り込みました。"
        "取り込んだ接続には API キーがありません。保管庫に保存してから使ってください"
    )


def profile_bundle_partly_imported(connections: int, reason: str) -> str:
    """接続を足した後に、指示文を書けなかったとき。"""
    return f"AI プロファイル {connections} 件だけを取り込みました。プロンプトは取り込めません: {reason}"


def profile_bundle_unreadable(reason: str) -> str:
    return f"プロファイルのファイルを取り込めません: {reason}。何も取り込んでいません"


# 手元の CLI を呼ぶ形式(codex-cli・claude-code-cli。REQ-102 §4。pywebview の設定画面だけ)。欄の名前・注記・知らせ
SETTINGS_FIELD_CLI_PATH = "CLI の場所"
SETTINGS_BUTTON_CHOOSE_CLI = "選ぶ…"
SETTINGS_NOTE_CLI_PATH = "空なら PATH から codex（Claude Code CLI は claude）を探します"
SETTINGS_FIELD_TIMEOUT = "時間の上限（秒）"
SETTINGS_NOTE_CLI_LOGIN = "この形式は CLI のログインを使います。キーは使いません"
# Codex CLI は利用者の ~/.codex/config.toml を読まない(--ignore-user-config。判断の記録 5)。送る前の確認と設定画面に出す
CODEX_CONFIG_NOTE = "利用者の config.toml は読みません。送り先は Codex のログインの既定です"
_CLI_PROGRAM = {"codex-cli": "codex", "claude-code-cli": "claude"}


# プロファイルごとの環境変数の名前の欄(ADR-0077。pywebview の設定画面の「API キー」の枠の中)と知らせ
SETTINGS_FIELD_ENV_SUFFIX = "環境変数の名前"


def env_suffix_invalid() -> str:
    return (
        f"{SETTINGS_FIELD_ENV_SUFFIX}は英大文字・数字・_ の 1〜40 文字にしてください（空なら環境変数を使いません）。"
        "保存していません"
    )


def env_suffix_taken(name: str) -> str:
    """同じ環境変数の名前を、ほかのプロファイルで使っているとき(キーの値は入れない)。"""
    return f"この{SETTINGS_FIELD_ENV_SUFFIX}はプロファイル『{name}』で使っています。保存していません"


def cli_timeout_invalid() -> str:
    return f"{SETTINGS_FIELD_TIMEOUT}は 1 以上の整数にしてください（空なら 60）。保存していません"


# CLI の2形式の Thinking の深さの欄(cli_effort。ADR-0157 決定10〜12。pywebview の設定画面だけ)
SETTINGS_FIELD_CLI_EFFORT = "Thinking（深さ）"
SETTINGS_CLI_EFFORT_DEFAULT = "既定（指定を付けない）"
SETTINGS_NOTE_THINKING_COST = (
    "上げるほど、答えが出るまでの時間と利用枠（料金）が増えます。CLI の指定は版によって変わります。"
    "動かないときは「既定」に戻してください"
)


def cli_effort_invalid() -> str:
    """Thinking の深さが、その形式で選べる値でないとき(値は入れない)。"""
    return f"{SETTINGS_FIELD_CLI_EFFORT}は、この形式で選べる値にしてください（CLI の形式でないときは「既定」）。保存していません"


def cli_destination(api_format: str, cli_path: str | None) -> str:
    """CLI の形式の送り先の表示(送る前の確認の「接続先」の行。設定を読むときに接続先に入れる)。

    「Codex CLI（PATH の codex。利用者の config.toml は読みません。…）」「Claude Code CLI（/opt/…/claude）」の形。
    """
    where = cli_path or f"PATH の {_CLI_PROGRAM.get(api_format, api_format)}"
    if api_format == "codex-cli":
        where = f"{where}。{CODEX_CONFIG_NOTE}"
    return f"{API_FORMAT_SHORT.get(api_format, api_format)}（{where}）"


def profile_row_line(name: str, format_label: str, *, in_use: bool, has_key: bool) -> str:
    """一覧の1行(「▶ 業務用（Claude） · 使用中」)。使用中の行の頭には ▶ を付ける(キーの読み元の行と同じ印)。"""
    marks = ([PROFILE_MARK_IN_USE] if in_use else []) + ([] if has_key else [PROFILE_MARK_NO_KEY])
    head = "▶ " if in_use else "   "
    return f"{head}{name}（{format_label}）" + "".join(f" · {m}" for m in marks)


def profile_row_title(name: str, *, in_use: bool) -> str:
    """一覧の1件の1行目(REQ-53 §3)。使用中なら頭に ▶ を付ける(profile_row_line と同じ印)。"""
    return ("▶ " if in_use else "   ") + name


def profile_row_detail(format_label: str, *, in_use: bool, has_key: bool) -> str:
    """一覧の1件の2行目(「OpenAI 互換 · 使用中 · キー未設定」)。当てはまる印だけを添える。"""
    marks = ([PROFILE_MARK_IN_USE] if in_use else []) + ([] if has_key else [PROFILE_MARK_NO_KEY])
    return " · ".join([format_label, *marks])


def base_url_default_hint(url: str) -> str:
    """接続先の空欄に薄く出す文。"""
    return f"既定（{url}）"


def profile_name_empty() -> str:
    return "名前を入れてください。保存していません"


def profile_name_taken(name: str) -> str:
    return f"「{name}」という名前のプロファイルは既にあります（大文字・小文字は区別しません）。保存していません"


def profile_saved(name: str) -> str:
    return f"プロファイル「{name}」を保存しました"


# 「新しいプロファイルとして保存」で、元と同じ環境変数の名前を写さなかったときに添える文(REQ-199 §4)
ENV_SUFFIX_NOT_COPIED = "環境変数の名前は写していません。必要なら別の名前を決めてください"


def profile_added(name: str) -> str:
    return f"プロファイル「{name}」を追加しました。名前・形式・モデルを入れて「保存」を押してください"


def profile_duplicated(name: str) -> str:
    return f"プロファイル「{name}」を作りました。API キーは写していません"


def profile_deleted(name: str) -> str:
    return f"プロファイル「{name}」を削除しました"


def profile_now_in_use(name: str) -> str:
    return f"「{name}」を使うようにしました"


def profile_delete_confirm(name: str, store: str) -> str:
    return f"プロファイル「{name}」を削除しますか？\n{store}に保存した、このプロファイルの API キーも削除します"


# 上書きの確かめ(REQ-112。pywebview の設定画面だけ)の3つのボタン。取り消しは DIALOG_CANCEL
PROFILE_SAVE_AS_NEW = "新しいプロファイルとして保存"
PROFILE_OVERWRITE = "書き換える"


def profile_overwrite_confirm(name: str) -> str:
    """名前・形式・接続先を変えた保存と、保存していない変更があるときの「新規作成」「複製」の確かめ。name は保存した名前。"""
    return f"「{name}」を書き換えますか？"


def api_key_replace_confirm(name: str, tail: str | None) -> str:
    """「保管庫に保存」で、その接続にもうキーがあるときの確かめ。tail はキーの末尾4文字(短いキーは None で出さない)。"""
    if tail is None:
        return f"「{name}」のキーを置き換えますか？"
    return f"「{name}」のキー（末尾 {tail}）を置き換えますか？"


def profile_delete_in_use() -> str:
    return "使用中のプロファイルは削除できません。先に別のプロファイルを選んで「このプロファイルを使う」を押してください"


def profile_delete_last() -> str:
    return "最後のプロファイルは削除できません"


def profile_delete_key_failed(name: str, reason: str) -> str:
    """プロファイルは消したが、そのキーを保管庫から消せなかったとき。"""
    return f"プロファイル「{name}」は削除しましたが、API キーを保管庫から削除できませんでした（{reason}）"


def profile_unreadable(error: str) -> str:
    return f"設定を読めません: {error}"


def key_target_unknown(error: str) -> str:
    """プロファイルを読めず、どのプロファイルのキーか決められないとき(保管庫に触らない)。"""
    return f"設定を読めないので、どのプロファイルの API キーか決められません。保管庫には保存・削除していません（{error}）"


def new_profile_name(n: int) -> str:
    """「追加」の名前。重ならない番号を付ける(1 は番号なし)。"""
    return "新しいプロファイル" if n == 1 else f"新しいプロファイル {n}"


def template_profile_name(label: str, n: int) -> str:
    """ひな形で「追加」したときの名前(ひな形の表示名)。重ならない番号を付ける(1 は番号なし)。REQ-100 §5。"""
    return label if n == 1 else f"{label} {n}"


def copy_profile_name(name: str, n: int) -> str:
    """「複製」の名前。重ならない番号を付ける(1 は番号なし)。"""
    return f"{name} のコピー" if n == 1 else f"{name} のコピー {n}"


def key_profile_note() -> str:
    return f"このプロファイル専用の名前で保存します。「{SETTINGS_FIELD_ENV_SUFFIX}」の変数があれば、そちらが優先されます"


def status_ready(name: str) -> str:
    """右上の状態の印(プロファイルがあるとき。使うプロファイルの名前を添える)。"""
    return f"{STATUS_READY}（{name}）"


# --- AI 接続テスト(REQ-35 / ADR-0034)。設定画面のボタンと `driftpilot llm-check` で同じ文。キーの値と応答の本文は入れない ---

LLM_CHECK_TITLE = "AI 接続テスト"
LLM_CHECK_ASK = "送りますか？ [y/N] "
LLM_CHECK_NOT_A_TERMINAL = "確認できないので送りません。--yes を付けてください"
LLM_CHECK_DECLINED = "送りませんでした"
LLM_CHECK_SENDING = "送っています…"
LLM_CHECK_SEND = "送る"
LLM_CHECK_DONT_SEND = "送らない"
# 接続先に <リソース名> などの目印が残っているとき(送らない。REQ-100 §5。比較の結果の未実施の理由と同じ文)
PLACEHOLDER_BASE_URL = f"{STATUS_PLACEHOLDER}。送っていません"


def llm_check_key_source(source: str | None, store: str, env_name: str | None = None) -> str:
    """キーの読み元の名前(値は出さない)。store は保管庫の名前(key_store.store_name)。

    env_name は読んだ環境変数の名前(プロファイルごとの変数。ADR-0077)。省くと共通の変数の名前。
    """
    if source == "env":
        return f"環境変数 {env_name or 'DRIFTPILOT_LLM_API_KEY'}"
    if source == "keyring":
        return store
    if source == "user_config":
        return "設定ファイル"
    if source == "none":  # custom-http-json で認証が none(キーを送らない)
        return "使いません（認証なし）"
    if source == "cli":  # CLI の形式(CLI 自身のログインを使う。REQ-102)
        return "使いません（CLI のログイン）"
    return "未設定"


def llm_check_confirm_lines(
    base_url: str | None, model: str, prompt: str, key_source: str, *, default_base_url: str | None = None
) -> list[str]:
    """送る前の確認に出す行(REQ-35 §2 の 2)。key_source は llm_check_key_source の名前。

    default_base_url は API の形式ごとの既定の接続先(省くと OpenAI の既定。REQ-42 §6)。
    """
    if default_base_url is None:
        from driftpilot.llm_analyzer.openai_client import DEFAULT_BASE_URL as default_base_url  # openai は読まない

    return [
        "AI 接続テスト: 次の内容で AI に短い文を送ります",
        f"接続先: {base_url or f'既定（{default_base_url}）'}",
        f"モデル: {model}",
        f"送る文: {prompt}",
        f"キーの読み元: {key_source}（キーの値は表示しません）",
        "ごく少額ですが料金がかかることがあります",
        "1回だけ送り、自動の再試行はしません。結果は保存しません",
    ]


def llm_check_unreadable(error: str) -> str:
    """設定を読めないとき(送らない)。"""
    return f"設定を読めません: {error}。送っていません"


def llm_check_failed(error_type: str) -> str:
    """画面のワーカーで想定外の例外が出たとき。例外の本文は使わず、型名だけを添える。"""
    return f"接続テストを最後まで行えませんでした（{error_type}）"


LLM_CHECK_CLI_NOT_FOUND = "CLI が見つかりません。CLI の場所の欄か PATH を確かめてください。送っていません"
LLM_CHECK_CLI_TIMEOUT = "CLI が時間の上限までに終わらなかったため、止めました"
LLM_CHECK_CLI_ERROR = (
    "CLI がエラーで終わったか、回答が空でした。モデルの名前（Claude Code なら sonnet など）と CLI のログインを確かめてください"
)
# CLI の形式のモデルの欄に薄く出す例の文(REQ-113 §4。pywebview の設定画面だけ)。ほかの形式は出さない
CLI_MODEL_HINT = {
    "claude-code-cli": "例: sonnet（空なら CLI の既定のモデルで送ります）",
    "codex-cli": "空のままでかまいません（空なら CLI の既定のモデルで送ります）",
}


def llm_check_result(result: ConnectionCheckResult, *, config_path: Path, cli: bool = False) -> str:
    """分類ごとに、何が起きたかと次にすること。config_path は利用者ごとの設定ファイル(no_model の案内)。

    cli は CLI の形式か(REQ-102)。CLI が見つからない・時間切れ・エラーで終わったときは CLI 向けの文にする。
    """
    kind = result.kind
    if kind == "cli_not_found" or (cli and kind == "unavailable"):
        return LLM_CHECK_CLI_NOT_FOUND
    if cli and kind == "connection":
        return LLM_CHECK_CLI_TIMEOUT
    if cli and kind in ("error", "auth", "not_found", "rate_limited"):
        return LLM_CHECK_CLI_ERROR
    if kind == "ok":
        return "つながりました。この設定で AI 解析を使えます（応答の中身は表示しません）"
    if kind == "auth":
        return "キーが受け付けられませんでした。キーが正しいか、そのキーにこのモデルを使う権限があるかを確かめてください"
    if kind == "not_found":
        return "モデルか接続先が見つかりませんでした（404）。設定ファイルの llm.model と llm.base_url を確かめてください"
    if kind == "rate_limited":
        return "回数の上限か、支払いの上限に達しています（429）。しばらく待つか、契約と支払いの状態を確かめてください"
    if kind == "connection":
        return "接続先につながりませんでした。ネットワークと、設定ファイルの llm.base_url を確かめてください"
    if kind == "error":
        code = f"（状態コード {result.status_code}）" if result.status_code is not None else ""
        return f"接続先がエラーを返しました{code}。しばらく待ってから、もう一度試してください"
    if kind == "no_key":
        return (
            f"{STATUS_NO_KEY}。送っていません。"
            "設定画面の「保管庫に保存」でキーを保存するか、プロファイルの環境変数の名前を決めてください"
        )
    if kind == "no_model":
        return f"{STATUS_NO_MODEL}。送っていません。設定ファイル（{config_path}）の llm.model にモデル名を書いてください"
    if kind == "unavailable":
        return f"{NO_LLM_EXTRA}。送っていません。{LLM_EXTRA_INSTALL} で入れてください"
    if kind == "placeholder_base_url":
        return PLACEHOLDER_BASE_URL
    raise ValueError(f"未知の分類: {kind}")


def llm_check_no_model_profile() -> str:
    """プロファイルを使っていて、モデルが無いとき(送らない。設定画面の接続テスト。REQ-43)。"""
    return f"{STATUS_NO_MODEL}。送っていません。このプロファイルの「モデル」にモデル名を入れて「保存」を押してください"


def llm_check_outcome_line(result: ConnectionCheckResult, text: str, time: str) -> str:
    """設定画面の接続テストの結果の1行。成功は短く「つながりました（12:41）」、失敗は理由の文に時刻を添える。"""
    return f"つながりました（{time}）" if result.ok else f"{text}（{time}）"


# --- 自己診断(REQ-38 / ADR-0036 決定6)。`driftpilot selfcheck` の文。キーの値はどの文にも入れない ---

SELFCHECK_HELP = "同梱の部品やデータが揃っているかを確かめる(通信しない・ファイルを書かない)"
SELFCHECK_VERSION = "版"
SELFCHECK_BUNDLED = "同梱の表"
SELFCHECK_PATTERNS = "パターン表"
SELFCHECK_TEMPLATES = "show のテンプレート"
SELFCHECK_METADATA = "部品の版の情報"
SELFCHECK_GUI_WEB = "画面の部品（web）"
SELFCHECK_LLM = "AI の部品"
SELFCHECK_KEY_STORE = "キーの保管庫"
SELFCHECK_ALL_OK = "すべて OK"


def selfcheck_not_installed(extra: str) -> str:
    """extra が入っていないとき(失敗にしない。REQ-38 §6)。"""
    return f"入っていません（pip install 'driftpilot[{extra}]'）"


def selfcheck_line(name: str, ok: bool, detail: str, installed: bool) -> str:
    """人が読む形の1行。OK・失敗（理由）・入っていません。"""
    if not installed:
        return f"{name}: {detail}"
    if ok:
        return f"{name}: OK（{detail}）" if detail else f"{name}: OK"
    return f"{name}: 失敗（{detail}）"


def selfcheck_failed_count(count: int) -> str:
    return f"{count} 項目が失敗"


def selfcheck_versions(driftpilot: str, python: str) -> str:
    return f"driftpilot {driftpilot}・Python {python}"


def version_line(driftpilot: str) -> str:
    """`driftpilot --version` の1行(REQ-97)。版は selfcheck と同じ driftpilot.__version__。"""
    return f"driftpilot {driftpilot}"


def selfcheck_rules(file: str, count: int) -> str:
    return f"{file} {count} 件"


def selfcheck_unreadable(file: str, error_type: str) -> str:
    """同梱のファイルが読めない・形が違うとき。例外は型名だけを添える。"""
    return f"{file} を読めません: {error_type}"


def selfcheck_fingerprint(platform: str, digest: str, count: int) -> str:
    return f"{platform} {digest} {count} 件"


def selfcheck_error(error_type: str) -> str:
    """確かめの途中で想定外の例外が出たとき。例外の本文は使わず、型名だけを添える。"""
    return f"確かめられません: {error_type}"


def selfcheck_templates(count: int, folder: str) -> str:
    return f"{count} 個・{folder}"


def selfcheck_templates_missing(folder: str) -> str:
    return f"テンプレートのフォルダーがありません: {folder}"


def selfcheck_templates_empty(folder: str) -> str:
    return f"テンプレートが1個もありません: {folder}"


def selfcheck_metadata_missing(names: list[str]) -> str:
    return f"版の情報がありません: {'、'.join(names)}"


def selfcheck_import_failed(module: str, error_type: str) -> str:
    """extra は入っているのに import できないとき(exe の同梱し忘れ)。"""
    return f"{module} を読み込めません: {error_type}"


def selfcheck_store(store: str, problem: str | None) -> str:
    """保管庫の名前と、使えない理由(使えるなら名前だけ)。"""
    return store if problem is None else f"{store}: {problem}"


# --- 比較画面の文(REQ-105。wx の gui/app.py・gui/dialogs.py と pywebview の static/compare.html で共有する) ---
# 文は wx の比較画面に書いてあったものと同じ(移しただけ。文は変えない)

COMPARE_TITLE = "DriftPilot — 比較"
COMPARE_INPUTS = "入力"
COMPARE_SEGMENTS = "区間の選択"
COMPARE_SEGMENTS_WAITING = "(選んでください)"  # 選択待ちのとき、区間の枠の見出しの後ろに足す
COMPARE_SETTINGS = "比較の設定"
COMPARE_MODE = "比較方式"
COMPARE_KIND = "種類"
COMPARE_PLATFORM = "機種"
COMPARE_PLATFORM_HINT = "例: cisco_ios"
COMPARE_COMMAND = "show のコマンド"
COMPARE_COMMAND_HINT = "例: show ip interface brief"
COMPARE_LLM_CHECK = "AI 解析(送る前に内容を確認します)"
COMPARE_TAB_STRUCTURED = "構造化比較"
COMPARE_TAB_RAW = "単純比較"
COMPARE_TAB_UNPARSED = "構造化不可の行"
# 構造化比較が動いていない組の、押せないタブに出す文(ADR-0164 決定1)。前者は決定者の文のまま(括弧も変えない)
COMPARE_TAB_OFF_SHOW = "設定確認コマンド（show running-config 等）は構造化比較可能です"
COMPARE_TAB_OFF_MODE_RAW = "比較方式が「単純」のため、構造化比較をしていません"
COMPARE_COL_OP = "操作"
COMPARE_COL_CATEGORY = "カテゴリ"
COMPARE_COL_TARGET = "対象"
COMPARE_COL_ITEM = "項目"
COMPARE_COL_BEFORE = "変更前"
COMPARE_COL_AFTER = "変更後"
COMPARE_COL_BEFORE_NO = "前"
COMPARE_COL_AFTER_NO = "後"
COMPARE_COL_LINE = "行"
COMPARE_COL_PARENTS = "親"
COMPARE_BUTTON_BROWSE = "参照…"
COMPARE_BUTTON_PASTE = "貼り付け…"
COMPARE_BUTTON_PREV = "前の差分"
COMPARE_BUTTON_NEXT = "次の差分"
COMPARE_BUTTON_RUN = "比較実行"
COMPARE_BUTTON_SAVE = "結果を保存…"
COMPARE_BUTTON_CANCEL = "中止"
COMPARE_BUTTON_BATCH = "一括比較…"
COMPARE_BUTTON_SETTINGS = "設定…"
COMPARE_BUTTON_SWAP = "入れ替え"  # pywebview の比較の画面だけ(ADR-0078 決定2。wx の画面には無い)
# pywebview の比較の画面だけ(ADR-0080 決定1・2。wx の画面はチェックボックスのまま)
COMPARE_BUTTON_SEND_LLM = "AI に送る"
COMPARE_AUTO_SENDING = "AI に送っています"
COMPARE_LLM_PANE = "AI 解析"
# 知らせ(wx の MessageBox)の題
COMPARE_READ_FAILED_TITLE = "読み込めません"
COMPARE_LLM_SETTINGS_TITLE = "AI の設定を読めません"
COMPARE_FAILED_TITLE = "比較に失敗しました"
COMPARE_SAVE_FAILED_TITLE = "保存できません"
# pywebview の比較の窓へのファイルのドロップ(REQ-110 / ADR-0076 決定1)。入力は変えずに、この知らせを出す
COMPARE_DROP_TITLE = "ドロップできません"
COMPARE_DROP_TOO_MANY = "ドロップできるファイルは1つか2つです。入力は変えていません"
COMPARE_DROP_FOLDER = "フォルダは入力にできません。ファイルをドロップしてください。入力は変えていません"
COMPARE_DROP_NO_PATH = "ドロップしたファイルの場所が分かりません。「参照…」で選んでください。入力は変えていません"
# 貼り付けと送信前のプレビューのダイアログ(wx の PasteDialog・LlmPreviewDialog)
PASTE_PROMPT = "config か端末ログを貼り付けてください"
LLM_PREVIEW_TITLE = "AI に送る内容の確認"
LLM_PREVIEW_PAYLOAD = "送信内容(マスク後。このあとに答えの書き方の決まり(固定の文)だけを足して送ります):"
LLM_PREVIEW_SEND = "送信する"
LLM_PREVIEW_DONT_SEND = "送信しない"
DIALOG_CANCEL = "キャンセル"  # pywebview のダイアログの取り消しのボタン(wx は OS の標準のボタン)


def compare_save_note() -> str:
    """操作の行の注記(保存した結果の扱い。ADR-0017 決定4)。"""
    return f"保存した結果は{save_caution()}"


def compare_save_title() -> str:
    """保存のダイアログの題(wx)。"""
    return f"結果を保存 — {save_caution()}"


def compare_browse_title(side_label: str) -> str:
    """参照のダイアログの題(wx)。side_label は「変更前」「変更後」。"""
    return f"{side_label}のファイルを選ぶ"


def compare_paste_title(side_label: str) -> str:
    """貼り付けのダイアログの題。"""
    return f"{side_label}を貼り付け"


def compare_warning_line(warning: str) -> str:
    """構造化比較のタブの上に出す警告の1行。"""
    return f"警告: {warning}"


def auto_send_band(name: str | None) -> str:
    """自動送信がオンの間、比較の画面に常に出す帯(ADR-0080 決定2)。name は使用中の接続の名前。

    None は AI の接続のプロファイルを読めないとき(送らない)。
    """
    if name is None:
        return "自動送信: オン（AI プロファイルを読めないため、送りません）"
    return f"自動送信: オン（送り先: {name}）"


def auto_send_settings_unreadable(error: str) -> str:
    """自動送信で、設定を読めなかったとき(AI 解析の枠に出す。送っていない)。"""
    return f"自動送信: 設定を読めないため送っていません（{error}）"


# pywebview の比較の画面の「結果を開く…」(REQ-120 / ADR-0083。wx の画面には無い)。
# 知らせの文には、ファイルの中身もパスも入れない(決まった文だけ)
COMPARE_BUTTON_OPEN_RESULT = "保存した結果を開く…"  # REQ-206 / ADR-0148 決定3 で「結果を開く…」から変えた(wx は使っていない)
COMPARE_BUTTON_CLOSE_RESULT = "閉じる"
COMPARE_OPEN_FAILED_TITLE = "結果を開けません"
OPEN_RESULT_VERSION = "保存した結果の版が、このツールが読める版と違うため開けません。変換して読むと、欠けた項目を「無い」と誤読するおそれがあるためです"
OPEN_RESULT_NOT_JSON = "JSON として読めないため開けません。保存した結果のファイルを選んでください"
OPEN_RESULT_FORMAT = "保存した結果の形になっていないため開けません。保存した結果のファイルを選んでください"
OPEN_RESULT_UNREADABLE = "ファイルを読めないため開けません"


def open_result_too_large(limit_mb: int) -> str:
    """保存した結果のファイルが上限より大きいとき(読んでいない)。"""
    return f"ファイルが大きすぎるため開けません（上限 {limit_mb}MB）"


def opened_band(saved_at: str, before_name: str, after_name: str) -> str:
    """保存した結果を表示している間の帯の文(ADR-0083 決定1)。saved_at は保存ファイルの文字列のまま。"""
    return f"保存した結果を表示しています（保存日時: {saved_at}・変更前: {before_name}・変更後: {after_name}）"


# 複数のコマンドのまとめての比較(REQ-122 / ADR-0084 決定3・5・6)。pywebview の比較の画面と CLI の compare --all-segments。
# 要約・上限超え・送る内容が無いときの文は、一括比較の文(batch_llm_*)をそのまま使う
COMPARE_MULTI_TITLE = "コマンドの組"
COMPARE_MULTI_COL_COMMAND = "コマンド"
COMPARE_MULTI_COL_STATUS = "状態"
COMPARE_MULTI_COL_DIFFS = "差分"
# 一覧の、片方のログにしかないコマンドの区間の行の状態(ADR-0164 決定3)。無い側を言う
MULTI_NO_AFTER = "変更後なし"  # 変更前にだけある区間
MULTI_NO_BEFORE = "変更前なし"  # 変更後にだけある区間
# 1つのログの中で組にしたとき(ADR-0152 決定2)の、1回だけのコマンドの区間の行の状態と、選んだときの説明
MULTI_SINGLE = "1 回だけ"
MULTI_SINGLE_NOTE = "このコマンドはログに 1 回だけなので、比べていません"
# 「全部」のタブ(ADR-0152 決定7)。比較の窓でまとめて比べたときだけ、結果のタブの先頭に出す
COMPARE_TAB_ALL = "全部"
# 開いた .jsonl の帯に足す文(相手なしの区間は保存しない。あったかどうかはファイルから分からないので、いつも出す)
MULTI_UNPAIRED_NOT_IN_FILE = "相手なしの区間は保存に入っていません"
COMPARE_MULTI_NOTHING_TO_SAVE = "相手のある区間が無いため、保存できる組がありません。保存していません"


def multi_overall(status: str, total: int, count: int) -> str:
    """まとめての比較の全体の文(窓の下)。total は一覧の行の数(組と相手なし)。

    差分ありは差分ありの組と相手なしの数、失敗は失敗と選択待ちの組の数を count に渡す。差分なしは count を使わない。
    """
    if status == "no_difference":
        return f"{STATUS_LABEL['no_difference']}（{total} 組）"
    return f"{STATUS_LABEL[status]}（{total} 組中 {count} 組）"


def multi_unpaired_not_saved(count: int) -> str:
    """まとめての比較を保存したとき、相手なしの区間があった(保存には入れない。画面の知らせと CLI の標準エラー)。"""
    return f"相手なしの {count} 区間は保存に入っていません"


def single_pair_name(command: str, before: int, after: int) -> str:
    """1つのログの中で組にした組の一覧の名前(ADR-0152 決定2)。「show vlan（区間 2 → 7）」の形。"""
    return f"{command}（区間 {before} → {after}）"


def single_row_name(number: int, command: str) -> str:
    """1つのログの中で組にしたときの、1回だけのコマンドの区間の行の名前(ADR-0152 決定2)。"""
    return f"区間 {number}: {command}"


def all_tab_title(count: int) -> str:
    """「全部」のタブの題(ADR-0152 決定7)。count は表の行の数。"""
    return f"{COMPARE_TAB_ALL} ({count})"


def unpaired_status(side: str) -> str:
    """一覧の相手なしの行の状態(ADR-0164 決定3)。変更前にだけある区間は「変更後なし」、変更後にだけある区間は「変更前なし」。"""
    return MULTI_NO_AFTER if side == "before" else MULTI_NO_BEFORE


def unpaired_place(side: str, number: int) -> str:
    """「変更前 区間 3」の形(ADR-0164 決定4)。unpaired_row_name の頭と同じ。"""
    return f"{_SIDE_LABEL[side]} 区間 {number}"


def unpaired_row_name(side: str, number: int, command: str) -> str:
    """一覧の相手なしの行の名前。「変更前 区間 4: show vlan」の形(CLI の text の相手なしの行と同じ並び)。"""
    return f"{unpaired_place(side, number)}: {command}"


def unpaired_segment_note(side: str, number: int, command: str, line_count: int) -> str:
    """一覧で相手なしの行を選んだときの説明(表は空)。"""
    other = _SIDE_LABEL["after" if side == "before" else "before"]
    return f"{_SIDE_LABEL[side]}の区間 {number}（{command}・{line_count} 行）は、{other}に同じコマンドの区間が無いため比べていません"


def compare_llm_confirm_hint() -> str:
    """CLI の compare --all-segments --llm を --confirm なしで実行したとき(一括比較の batch_llm_confirm_hint と同じ形)。"""
    return "AI にはまだ1件も送っていません。この内容で送るには --all-segments --llm に --confirm を付けて実行してください"


# pywebview の比較の画面の、AI の答えへの追加の質問(REQ-118 / ADR-0081。wx の画面には無い)。
# どの文にも質問の中身を入れない
COMPARE_FOLLOW_UP_TITLE = "追加の質問"
COMPARE_FOLLOW_UP_ASK = "質問する"
COMPARE_FOLLOW_UP_ASKING = "AI に質問しています"
COMPARE_FOLLOW_UP_NOTICE_TITLE = "AI への質問"
FOLLOW_UP_QUESTION_LABEL = "質問"
FOLLOW_UP_ANSWER_LABEL = "回答"
_FOLLOW_UP_REWRITE = "送っていません。言い換えてから、もう一度質問してください"
_FOLLOW_UP_REFUSED = {
    "blank": "質問を入力してください。送っていません",
    "mask": (
        "質問に、伏せる対象の形（パスワード・鍵・コミュニティなどの設定の行）が入っています。"
        f"{_FOLLOW_UP_REWRITE}（設定の語や <secret:N> の記号は、日本語の言葉で書いてください）"
    ),
    "unsent": (
        "質問に、AI に送らない語（password・secret・key などの秘密の語や、送らない形の行）が入っています。"
        f"{_FOLLOW_UP_REWRITE}（設定の語や <secret:N> の記号は、日本語の言葉で書いてください）"
    ),
    "pem": f"質問に、鍵や証明書の始まりの行（-----BEGIN …）が入っています。{_FOLLOW_UP_REWRITE}",
}


def follow_up_refused(kind: str, max_chars: int) -> str:
    """質問を送らなかった理由(書き直しのお願い)。kind は llm_analyzer/follow_up.py の REFUSE_*。質問の中身は入れない。"""
    if kind == "too_long":
        return f"質問は {max_chars:,} 文字までにしてください。送っていません"
    return _FOLLOW_UP_REFUSED.get(kind, f"質問を送れませんでした。{_FOLLOW_UP_REWRITE}")


def follow_up_too_long(max_chars: int) -> str:
    """これまでのやり取りと合わせた文が上限を超えたとき(送っていない。やり取りは消していない)。"""
    return f"これまでのやり取りと合わせた文が {max_chars:,} 文字を超えるため、送っていません。比較し直すと、最初から質問できます"


def follow_up_limit(max_turns: int) -> str:
    """追加の質問が上限の回数に達したとき(AI 解析の枠に出す)。"""
    return f"追加の質問は {max_turns} 回までです。続けるときは、比較し直してから質問してください"


def follow_up_unavailable(reason: str | None) -> str:
    """質問を送る接続が使えないとき(キーが無いなど。送っていない)。理由の文は AI 解析の枠と同じ。"""
    return f"AI に質問できません: {_LLM_REASON.get(reason or '', f'理由: {reason}')}"


def follow_up_failed(
    reason: str,
    error_type: str,
    status_code: int | None,
    *,
    retry_after: int | None = None,
    has_api_text: bool = False,
) -> str:
    """質問の送信の失敗(ADR-0018 決定7 と同じく、理由の文と例外の型の名前・HTTP の状態コードだけ。例外の文面は入れない)。

    429 なら理由の文を llm_rate_limited にする(ADR-0168 決定4)。has_api_text が真なら、AI の面の折りたたみの案内を足す(決定5)。
    """
    detail = f"種類: {error_type}" + (f"、HTTP {status_code}" if status_code is not None else "")
    text = _LLM_REASON.get(reason, f"理由: {reason}")
    if reason == "api_error" and status_code == RATE_LIMIT_STATUS:
        text = llm_rate_limited(retry_after)
    body = f"AI に質問できませんでした: {text}（{detail}）"
    if has_api_text:
        body += f"。AI の面の下の「{COMPARE_API_ERROR_TITLE}」に、API が返した文があります"
    return body


# 比較の窓の AI の面の折りたたみ(ADR-0168 決定5)。中には掃除した後の文だけを出す。保存しない
COMPARE_API_ERROR_TITLE = "API からのエラーの文"
COMPARE_API_ERROR_NOTE = "API が返した文を短く切って出しています。キーらしい文字は伏せています。保存しません"

# 比較の窓の AI の面の「考えている内容」の折りたたみ(ADR-0169 決定4)。考えの文は保存しない。
# 長くて切ったときの断り書きは llm_analyzer.thinking.THINKING_CUT_NOTE(Python が view に入れる)
COMPARE_AI_THINKING_TITLE = "考えている内容"
COMPARE_AI_THINKING_NOTE = "AI が答えの前に考えた内容です。英語のことがあります。保存しません"

GUI_WEB_TEXTS.update(
    {
        "compare.ai.thinking.title": COMPARE_AI_THINKING_TITLE,
        "compare.ai.thinking.note": COMPARE_AI_THINKING_NOTE,
    }
)


# pywebview の比較の画面の配置(REQ-206 / ADR-0148)。上の3行・左右の面と境目・AI の面の操作。wx の画面には無い。
# どの文にも打った文・質問の中身・接続先の URL・キーを入れない
COMPARE_BUTTON_SWAP_ICON = "⇅"  # 入れ替えのアイコン。title と aria-label は COMPARE_BUTTON_SWAP
COMPARE_SETTINGS_CHANGE = "変更"  # 比較の設定の要約の横。今の設定の欄を開閉する
COMPARE_SPLIT_LABEL = "比較の表と AI の面の境目"
# まとめて比べたときの、コマンドの組の一覧と比較の表の境目(REQ-224 / ADR-0163)
COMPARE_MULTI_SPLIT_LABEL = "コマンドの組と比較の表の境目"
# 届いたところまでの答えを中止したときの注記(ADR-0157 決定7)。途中の答えの吹き出しの下に出す
COMPARE_PARTIAL_STOPPED = "中止しました。途中までの答えです。保存しません"
COMPARE_AI_EMPTY = "比較した後に「AI で解析」を押すと、ここに AI の答えが出ます"
COMPARE_AI_INPUT = "AI への質問や指示"
COMPARE_AI_INPUT_HINT = "Enter で送信、Shift+Enter で改行"
COMPARE_AI_PROFILE = "AI プロファイル"
COMPARE_AI_PROMPT = "プロンプト"
COMPARE_AI_ANALYZE = "AI で解析"
# キャラクターがオンのときの答えの吹き出しの名前と顔の title、ボタンと空の面の案内(ADR-0181 決定1・2)。
# 窓に出すだけ。保存・HTML・CSV・コピー・送る文には入れない(決定3)
CHARACTER_NAME = "Mirarin"
CHARACTER_AVATAR_TITLE = "答えは選んだ AI プロファイルのモデルが書いています"
COMPARE_AI_ANALYZE_CHARACTER = "AI（Mirarin）で解析"
COMPARE_AI_EMPTY_CHARACTER = "比較した後に「AI（Mirarin）で解析」を押すと、ここに Mirarin が答えます"
COMPARE_COPY = "コピー"
COMPARE_AI_PROFILE_FAILED_TITLE = "AI プロファイルを切り替えられません"
COMPARE_PROMPT_FAILED_TITLE = "プロンプトを切り替えられません"
# 機種違い・種類違いの窓の案内(ADR-0151 決定8)。pywebview の比較の窓だけ。CLI と wx は今の文のまま
COMPARE_PLATFORM_TROUBLE = "機種の選択が中身と合っていないようです。機種を「自動」に戻すか、選び直してください。"
COMPARE_KIND_TROUBLE = "変更前と変更後で種類の判定が違います。種類を選んでください。"
COMPARE_DETAILS = "詳しい内容"
# 差分の要約と絞り込み(ADR-0155 / REQ-215)。pywebview の比較の窓だけ。CLI と wx は変えない
COMPARE_RAW_DIFF_ONLY = "差分のみ表示"
COMPARE_FILTER_TITLE = "カテゴリで絞る"


def compare_filter_all(total: int) -> str:
    """構造化比較の表の上のチップの「すべて N」(ADR-0155 決定1)。total は構造化比較の行の総数。"""
    return f"すべて {total}"


def compare_raw_folded(count: int) -> str:
    """「差分のみ表示」で折りたたんだ行の文(ADR-0155 決定2)。count は隠した行数。"""
    return f"… {count} 行を省略"


def follow_up_destination_changed(before: str, now: str) -> str:
    """追加の質問の前に、最初に送ったときと今の AI プロファイルが違うときの確認(ADR-0148 決定7)。名前だけを出す。"""
    return f"送り先が変わりました（{before} → {now}）。これまでの差分と答えも一緒に送ります。送りますか？"


# 最初の依頼をチャットに残す・保存したやり取りから続けて質問する(ADR-0179 / REQ-242)。pywebview の比較の窓と AI の窓だけ。
# 打った文は view にだけ入れ、ここの文には入れない(名前はプロンプトとプロファイルの名前だけ)
def COMPARE_FIRST_REQUEST_PROMPT(name: str) -> str:  # noqa: N802 (名前は REQ-242 §2 のとおり)
    """打たずに「AI で解析」で送ったときの最初の依頼の吹き出しの文(決定1)。name は送ったときのプロンプトの名前。"""
    return f"プロンプト『{name}』で解析"


def COMPARE_FIRST_REQUEST_AUTO(name: str) -> str:  # noqa: N802 (名前は REQ-242 §2 のとおり)
    """自動送信のときの最初の依頼の吹き出しの文(決定1)。name は送ったときのプロンプトの名前。"""
    return f"自動で送りました（プロンプト『{name}』）"


# 最初の依頼の吹き出しの読み上げの名前(追加の質問の吹き出しと同じ「質問」)
COMPARE_FIRST_REQUEST_LABEL = FOLLOW_UP_QUESTION_LABEL
# プロンプトの名前が分からない(プロンプトのファイルが無い)ときに出す名前(compare_handed_answer_note と同じ「-」)
COMPARE_FIRST_REQUEST_NO_PROMPT = "-"
# 保存した結果を開いて質問するとき、保存に送ったときの伏せる語が無い(古い保存)ときの送る前の確認(決定3)
COMPARE_SAVED_NO_WORDS = "この保存には送ったときの伏せる語が残っていません。今の設定の語だけで確かめました。送りますか"


def compare_saved_no_profile(now: str) -> str:
    """保存した結果を開いて質問するとき、保存に送り先の記録が無いときの確認(決定3)。名前だけを出す。"""
    return f"この保存には送り先の記録がありません（今の送り先: {now}）。これまでの差分と答えも一緒に送ります。送りますか？"


GUI_WEB_TEXTS.update({"compare.ai.first_request": COMPARE_FIRST_REQUEST_LABEL})


def instruction_too_long(max_chars: int) -> str:
    """解析の前に打った文と合わせた送る文が上限を超えたとき(送っていない。ADR-0148 決定6)。"""
    return f"打った文と合わせた送る文が {max_chars:,} 文字を超えるため、送っていません。打った文を短くしてから、もう一度押してください"


# 「差分＋全文を送る」(ADR-0158 / REQ-218)。pywebview の比較の窓だけ。CLI・一括比較・wx には無い。
# どの文にも全文・質問の中身を入れない
COMPARE_FULL_TEXT = "差分＋全文を送る"
# オンの間だけチェックボックスの下に出す短い案内(決定10)
COMPARE_FULL_TEXT_NOTE = "全文を送ると、伏せられない形の秘密に当たる行が増えます。IP アドレス、ホスト名、説明文はそのまま送ります"
# 追加の質問で全文を作り直したら、番号がずれていた・作れなかった(決定7)
COMPARE_FULL_TEXT_MISMATCH = "全文を作れませんでした。比較し直してから送ってください"
# 押せない理由(チェックのすぐ下の注記と title に出す。決定9・ADR-0175 決定2)
COMPARE_FULL_TEXT_MULTI_FIRST = "まとめて比べたときは、追加の質問のときだけ選べます"
COMPARE_FULL_TEXT_NOT_YET = "比べた後に選べます"
COMPARE_FULL_TEXT_BUSY = "比較や送信が終わってから選べます"
COMPARE_FULL_TEXT_OPENED = "保存した結果を開いている間は選べません"
COMPARE_FULL_TEXT_UNPAIRED = "相手なしの行では選べません"
# 種類が config でも show でもないとき(種類が決まらないなど)だけ(ADR-0182 決定1)
COMPARE_FULL_TEXT_NOT_CONFIG = "config か show の比較のときだけ選べます"
COMPARE_FULL_TEXT_NOT_SENT = "AI に送っていない組では選べません"


def COMPARE_FULL_TEXT_PAIR(name: str) -> str:  # noqa: N802 (名前は REQ-237 §2 のとおり)
    """まとめての比較で全文を選べるときの注記(ADR-0175 決定2)。name は一覧の行の名前(画面に出している文)。"""
    return f"全文を付ける組: {name}"


# 一括比較の答えを比較の窓へ持っていく(ADR-0170 / REQ-232)。pywebview の比較の窓だけ。差分の本文・質問の中身は入れない
COMPARE_HANDED_ANSWER_TITLE = "一括比較の答え"
# 比べ直した差分が一括比較で送ったときと違い、答えを付けなかった(決定3)
COMPARE_HANDED_ANSWER_UNUSED = (
    "一括比較で送ったときと差分が違うため、一括比較の答えは使いませんでした。「AI で解析」から送ってください"
)


def compare_handed_answer_note(profile_name: str, prompt_name: str | None) -> str:
    """AI の面の答えの上に出す注記(ADR-0170 決定5)。名前だけで、接続先とキーは入れない。プロンプトの名前が無ければ「-」。"""
    prompt = prompt_name if prompt_name else "-"
    return f"一括比較で届いた答えです（AI プロファイル: {profile_name}、プロンプト: {prompt}）。続けて質問できます"


def llm_full_text_preview(chars: int) -> str:
    """送信前の確認の先頭に出す帯の文(ADR-0158 決定6)。chars は送る文の全体の文字数。"""
    return f"変更後の全文を送ります（全体で {chars:,} 文字）。語の無い秘密など、止められない形の秘密が含まれることがあります"


# pywebview の比較画面(static/compare.html。REQ-105)の文。半角の括弧を含む文(COMPARE_LLM_CHECK・LLM_PREVIEW_PAYLOAD・
# compare_save_note など)はここに入れず、CompareApi が返す状態に入れて渡す(画面の文の括弧は全角という決まりのテストがあるため)
GUI_WEB_TEXTS.update(
    {
        "compare.inputs": COMPARE_INPUTS,
        "compare.side.before": _SIDE_LABEL["before"],
        "compare.side.after": _SIDE_LABEL["after"],
        "compare.button.browse": COMPARE_BUTTON_BROWSE,
        "compare.button.paste": COMPARE_BUTTON_PASTE,
        "compare.button.swap": COMPARE_BUTTON_SWAP,
        "compare.button.swap_icon": COMPARE_BUTTON_SWAP_ICON,
        "compare.segments": COMPARE_SEGMENTS,
        "compare.settings.change": COMPARE_SETTINGS_CHANGE,
        "compare.split.label": COMPARE_SPLIT_LABEL,
        "compare.multi.split.label": COMPARE_MULTI_SPLIT_LABEL,
        "compare.ai.empty": COMPARE_AI_EMPTY,
        # キャラクターがオンのとき(ADR-0181 決定1・2)。名前と顔の title は比較の窓・別の窓・一括比較の窓で共通
        "compare.ai.empty.character": COMPARE_AI_EMPTY_CHARACTER,
        "character.name": CHARACTER_NAME,
        "character.avatar.title": CHARACTER_AVATAR_TITLE,
        "compare.ai.input": COMPARE_AI_INPUT,
        "compare.ai.input.hint": COMPARE_AI_INPUT_HINT,
        "compare.ai.profile": COMPARE_AI_PROFILE,
        "compare.ai.prompt": COMPARE_AI_PROMPT,
        "compare.button.copy": COMPARE_COPY,
        "compare.settings": COMPARE_SETTINGS,
        "compare.mode": COMPARE_MODE,
        "compare.kind": COMPARE_KIND,
        "compare.platform": COMPARE_PLATFORM,
        "compare.platform.hint": COMPARE_PLATFORM_HINT,
        "compare.command": COMPARE_COMMAND,
        "compare.command.hint": COMPARE_COMMAND_HINT,
        "compare.tab.all": COMPARE_TAB_ALL,
        "compare.tab.structured": COMPARE_TAB_STRUCTURED,
        "compare.tab.raw": COMPARE_TAB_RAW,
        "compare.tab.unparsed": COMPARE_TAB_UNPARSED,
        "compare.col.op": COMPARE_COL_OP,
        "compare.col.category": COMPARE_COL_CATEGORY,
        "compare.col.target": COMPARE_COL_TARGET,
        "compare.col.item": COMPARE_COL_ITEM,
        "compare.col.before": COMPARE_COL_BEFORE,
        "compare.col.after": COMPARE_COL_AFTER,
        "compare.col.before_no": COMPARE_COL_BEFORE_NO,
        "compare.col.after_no": COMPARE_COL_AFTER_NO,
        "compare.col.line": COMPARE_COL_LINE,
        "compare.col.parents": COMPARE_COL_PARENTS,
        "compare.button.prev": COMPARE_BUTTON_PREV,
        "compare.button.next": COMPARE_BUTTON_NEXT,
        "compare.llm": COMPARE_LLM_PANE,
        "compare.follow_up.title": COMPARE_FOLLOW_UP_TITLE,
        "compare.button.run": COMPARE_BUTTON_RUN,
        "compare.button.save": COMPARE_BUTTON_SAVE,
        "compare.button.open_result": COMPARE_BUTTON_OPEN_RESULT,
        "compare.button.close_result": COMPARE_BUTTON_CLOSE_RESULT,
        "compare.button.cancel": COMPARE_BUTTON_CANCEL,
        "compare.button.batch": COMPARE_BUTTON_BATCH,
        "compare.button.settings": COMPARE_BUTTON_SETTINGS,
        "compare.multi.title": COMPARE_MULTI_TITLE,
        "compare.multi.col.command": COMPARE_MULTI_COL_COMMAND,
        "compare.multi.col.status": COMPARE_MULTI_COL_STATUS,
        "compare.multi.col.diffs": COMPARE_MULTI_COL_DIFFS,
        "compare.paste.prompt": PASTE_PROMPT,
        "compare.dialog.ok": DIALOG_OK,
        "compare.dialog.cancel": DIALOG_CANCEL,
        "compare.details": COMPARE_DETAILS,
        # 差分の要約と絞り込み(ADR-0155 / REQ-215)
        "compare.raw.diff_only": COMPARE_RAW_DIFF_ONLY,
        "compare.filter.title": COMPARE_FILTER_TITLE,
        # 「差分＋全文を送る」(ADR-0158 / REQ-218)
        "compare.llm.full_text": COMPARE_FULL_TEXT,
    }
)


# --- 一括比較画面の文(REQ-106。wx の gui/batch_frame.py・gui/dialogs.py と pywebview の static/batch.html で共有する) ---
# 文は wx の一括比較画面に書いてあったものと同じ(移しただけ。文は変えない)

BATCH_TITLE = "DriftPilot — 一括比較"
BATCH_SOURCE = "入力"
BATCH_BEFORE_DIR = "変更前のフォルダ"
BATCH_AFTER_DIR = "変更後のフォルダ"
BATCH_MANIFEST = "組のファイル"
# 選ぶダイアログの題(wx。pywebview のダイアログには題を渡せない)
BATCH_PICK_BEFORE = "変更前のフォルダを選ぶ"
BATCH_PICK_AFTER = "変更後のフォルダを選ぶ"
BATCH_PICK_MANIFEST = "組のファイルを選ぶ"
BATCH_REBUILD = "組を作り直す"
BATCH_RUN = "実行"
BATCH_SAVE_MANIFEST = "この組を保存…"
BATCH_SAVE_RESULTS = "結果を保存…"
BATCH_CANCEL = "中止"
BATCH_HANDOFF = "比較画面で開く"
# 組の一覧の列
BATCH_COL_INDEX = "#"
BATCH_COL_BEFORE = "変更前のファイル"
BATCH_COL_AFTER = "変更後のファイル"
BATCH_COL_SELECTION = "区間"
BATCH_COL_SIDE = "どちらにあるか"
BATCH_COL_NAME = "ファイル名"
# 結果の一覧の列
BATCH_COL_STATUS = "状態"
BATCH_COL_FILE = "ファイル"
BATCH_COL_STRUCTURED = "構造化比較の差分"
BATCH_COL_RAW = "単純比較の差分"
BATCH_COL_UNPARSED = "構造化不可の行"
BATCH_COL_LLM = "AI 解析"
BATCH_COL_PLATFORM = "機種"  # pywebview の結果の表だけ(wx の列は足さない。ADR-0151 決定7)
# pywebview の一括比較の窓の 1 つの表と上の行(REQ-211 / ADR-0153 決定1〜3。wx の画面には無い)
BATCH_COL_LABEL = "名前"
BATCH_COL_AI_POINT = "AI の要点"
BATCH_COL_RESIZE = "列の幅を変える"  # 見出しの境目の読み上げの名前(title と aria-label)
BATCH_OPEN_MANIFEST = "または 組のファイルを開く"
BATCH_OPEN = "開く…"
BATCH_DROP_GUIDE = "ファイルやフォルダをドロップできます"
BATCH_PROGRESS_NAME = "一括比較の進捗"  # 進み具合の読み上げの名前(aria-label)
BATCH_RESULTS = "結果"
BATCH_ANSWER = "選んだ組の AI の回答"
# 知らせ(wx の MessageBox)の題と文
BATCH_SETUP_FAILED_TITLE = "組を作れません"
BATCH_LLM_SETTINGS_TITLE = "AI 解析の設定を読めません"
BATCH_NOT_SENT_TITLE = "AI には送っていません"
BATCH_FAILED_TITLE = "一括比較に失敗しました"
BATCH_SELECT_TITLE = "組を選んでください"
BATCH_SELECT_PROMPT = "比較画面で開く組を、一覧で選んでください"
# pywebview の一括比較の窓へのドロップ(REQ-115 / ADR-0078 決定4)。入力は変えずに、この知らせを出す
BATCH_DROP_TITLE = "ドロップできません"
BATCH_DROP_UNSUPPORTED = "ドロップできるのは、フォルダ1つか2つか、組のファイル（.yaml か .yml）1つです。入力は変えていません"
BATCH_DROP_NO_PATH = "ドロップしたものの場所が分かりません。「選ぶ…」で選んでください。入力は変えていません"
# 送信前の確認(wx の BatchLlmApprovalDialog。ADR-0031)
BATCH_APPROVAL_TITLE = "AI に送る内容の確認(一括)"
BATCH_APPROVAL_ROWS = "送る組(チェックを外した組は送りません):"
BATCH_APPROVAL_COL_JOB = "組"
BATCH_APPROVAL_COL_SENT = "送る差分"
BATCH_APPROVAL_COL_MASKED = "伏せた値"
BATCH_APPROVAL_COL_UNSENT = "送らない行"
BATCH_APPROVAL_SAVE = "内容をファイルに保存…"
BATCH_APPROVAL_SEND = "送る"
BATCH_APPROVAL_DONT_SEND = "送らない"
BATCH_PAYLOADS_SAVE_TITLE = "送る内容を保存"  # 保存のダイアログの題(wx)
BATCH_SAVED_TITLE = "保存しました"


# pywebview の一括比較の窓の AI の場所(REQ-213 / ADR-0153 決定9・10)。wx の画面には無い。
# どの文にも接続先の URL・キー・CLI の場所を入れない
BATCH_AI_CHECK = "内容を確かめる…"
BATCH_APPROVAL_CLOSE = "閉じる"  # 「内容を確かめる…」の確認のボタン(送らない)
BATCH_AI_FOLLOW_UP_NOTE = "質問するときは、ダブルクリックして比較の窓で開きます"
BATCH_AUTO_SEND_ON = "自動で AI に送る: オン（実行の後に送る内容のリストを出します。「送る」を押すまで送りません）"
# 「AI で解析」「内容を確かめる…」を押せない理由(AI の行の下の注記とボタンの title に出す。ADR-0175 決定3)
BATCH_AI_REASON_NOT_RUN = "実行した後に送れます"
BATCH_AI_REASON_RUNNING = "実行が終わってから送れます"
BATCH_AI_REASON_APPROVAL = "送る前の確認と送信が終わってから送れます"
BATCH_AI_REASON_COMPARE = "比較の窓で AI に送っている間は送れません"
BATCH_AI_REASON_NO_TARGET = "送れる差分のある組がありません"


def batch_ai_title(pairs: int, diffs: int) -> str:
    """AI の場所の題。外していない組のうち、結果があり、失敗でなく、送る差分がある組の数と差分の合計。"""
    return f"AI で解析する組: {pairs} 件・差分 {diffs} 件"


def batch_run_label(count: int) -> str:
    """「実行」のボタン(組の件数を添える)。"""
    return f"{BATCH_RUN}({count} 件)"


def batch_all_tab_title(count: int) -> str:
    """一括比較の窓(pywebview)の表のタブ「すべて」。0 件でも出す(REQ-211 §2)。"""
    return f"すべて ({count})"


def batch_diff_tab_title(count: int) -> str:
    """一括比較の窓(pywebview)の表のタブ「差分がある組」。0 件でも出す(REQ-211 §2)。"""
    return f"差分がある組 ({count})"


def batch_save_note() -> str:
    """操作の行と送信前の確認の注記(保存したファイルの扱い。ADR-0017 決定4)。"""
    return f"保存したファイルは{save_caution()}"


def batch_save_manifest_title() -> str:
    """「この組をマニフェストとして保存…」の保存のダイアログの題(wx)。"""
    return f"この組を保存 — {save_caution()}"


def batch_manifest_saved(path: Path) -> str:
    return f"組のファイルを {path} に書きました。比較は実行していません"


def batch_rest_not_run(count: int) -> str:
    """中止したときの、実行しなかった残りの件数(「中止しました。」の後ろに付ける)。"""
    return f"残り {count} 件は実行していません"


def batch_payloads_saved(path: Path) -> str:
    """送信前の確認の「内容をファイルに保存…」で書いたとき。"""
    return f"{path} に保存しました"


# pywebview の一括比較画面(static/batch.html。REQ-106)の文。半角の括弧を含む文(LLM_CHECK_LABEL・MANIFEST_VALUES・
# handoff_note・batch_save_note・batch_run_label・確認の題など)はここに入れず、BatchApi が返す状態に入れて渡す
GUI_WEB_TEXTS.update(
    {
        "batch.source": BATCH_SOURCE,
        "batch.before_dir": BATCH_BEFORE_DIR,
        "batch.after_dir": BATCH_AFTER_DIR,
        "batch.manifest": BATCH_MANIFEST,
        "batch.button.pick": PICK_BUTTON,
        "batch.kind": COMPARE_KIND,
        "batch.platform": COMPARE_PLATFORM,
        "batch.mode": COMPARE_MODE,
        "batch.button.rebuild": BATCH_REBUILD,
        "batch.col.index": BATCH_COL_INDEX,
        "batch.col.before": BATCH_COL_BEFORE,
        "batch.col.after": BATCH_COL_AFTER,
        "batch.col.selection": BATCH_COL_SELECTION,
        "batch.col.side": BATCH_COL_SIDE,
        "batch.col.name": BATCH_COL_NAME,
        "batch.col.status": BATCH_COL_STATUS,
        "batch.col.file": BATCH_COL_FILE,
        "batch.col.structured": BATCH_COL_STRUCTURED,
        "batch.col.raw": BATCH_COL_RAW,
        "batch.col.unparsed": BATCH_COL_UNPARSED,
        "batch.col.llm": BATCH_COL_LLM,
        "batch.col.platform": BATCH_COL_PLATFORM,
        "batch.progress": BATCH_PROGRESS_NAME,
        "batch.cancel_note": batch_cancel_note(),
        "batch.results": BATCH_RESULTS,
        "batch.answer": BATCH_ANSWER,
        "batch.button.run": BATCH_RUN,
        "batch.button.save_manifest": BATCH_SAVE_MANIFEST,
        "batch.button.save_results": BATCH_SAVE_RESULTS,
        "batch.button.cancel": BATCH_CANCEL,
        "batch.button.handoff": BATCH_HANDOFF,
        # 1 つの表と上の行(REQ-211)
        "batch.col.label": BATCH_COL_LABEL,
        "batch.col.ai_point": BATCH_COL_AI_POINT,
        "batch.col.resize": BATCH_COL_RESIZE,
        # 比較の窓の 5 つの表の列の境目(REQ-224 / ADR-0163)。文は一括比較の窓と同じ
        "compare.col.resize": BATCH_COL_RESIZE,
        "batch.open_manifest": BATCH_OPEN_MANIFEST,
        "batch.button.open": BATCH_OPEN,
        "batch.drop_guide": BATCH_DROP_GUIDE,
        "batch.button.swap": COMPARE_BUTTON_SWAP,
        "batch.button.swap_icon": COMPARE_BUTTON_SWAP_ICON,
        # AI の場所(REQ-213)。プロファイルとプロンプトの名前・「AI で解析」・コピーは比較の窓と同じ文
        "batch.ai.profile": COMPARE_AI_PROFILE,
        "batch.ai.prompt": COMPARE_AI_PROMPT,
        "batch.button.ai_check": BATCH_AI_CHECK,
        "batch.button.ai_send": COMPARE_AI_ANALYZE,
        # キャラクターがオンのときの「AI で解析」(ADR-0181 決定2。batch.js が view の character で替える)
        "batch.button.ai_send.character": COMPARE_AI_ANALYZE_CHARACTER,
        "batch.button.copy": COMPARE_COPY,
        "batch.ai.follow_up_note": BATCH_AI_FOLLOW_UP_NOTE,
    }
)


# 一括比較の窓(pywebview)で組を直す・外す・組にする・機種を自動で選ぶ(REQ-212 / ADR-0153 決定4〜8)
BATCH_JOB_EXCLUDED = "外した組"  # 表の状態の列
BATCH_PLATFORM_UNDETECTED_ROW = "自動: 判定できません"  # 自動の組で、判定なし・食い違いのときの機種の列
# まとめて比べた組(ADR-0178 決定2)で、config のコマンドごとに機種が分かれたときの機種の列
BATCH_AUTO_PLATFORM_MIXED = "自動（複数）"
# 選択待ちの組を選んだときの答えの欄の理由(ADR-0178 決定5)。{count} は区間の数、{commands} は区間のコマンドの名前の並び
BATCH_WAITING_SEGMENT = (
    "このファイルには区間が {count} 個あり、区間の指定に合う区間を 1 つに決められませんでした。"
    "「組の設定…」の区間を直してください（区間: {commands}）"
)
BATCH_WAITING_FORMAT = "config か show か決められませんでした。「組の設定…」で種類を選んでください"
# 実行した組(外した組を除く)が全部選択待ちだったときの知らせ(ADR-0178 決定5)。題は状態の名前
BATCH_ALL_WAITING = "全部の組が選択待ちでした。組を選ぶと理由が出ます"
BATCH_ALL_WAITING_TITLE = STATUS_LABEL["needs_selection"]
# 秘密の語の設定を読めず、区間のコマンドの名前を判定に通せないときに名前の代わりに出す文(伏せられないなら出さない)
BATCH_WAITING_NAMES_HIDDEN = "…"
# 選択待ちの理由に並べる区間のコマンドの名前の数(超えたら「ほか N 個」)
BATCH_WAITING_NAMES_SHOWN = 10


def batch_waiting_segment(count: int, names: list[str]) -> str:
    """区間が決まらない選択待ちの理由(BATCH_WAITING_SEGMENT)。名前は判定に通した後の文を「、」で並べる。

    BATCH_WAITING_NAMES_SHOWN 個を超えたら、先頭のその数と「ほか N 個」。
    """
    shown = list(names[:BATCH_WAITING_NAMES_SHOWN])
    rest = len(names) - len(shown)
    if rest > 0:
        shown.append(f"ほか {rest} 個")
    return BATCH_WAITING_SEGMENT.format(count=count, commands="、".join(shown))


def multi_pair_name(group: str, command: str | None) -> str:
    """まとめて比べた組のコマンドの組の名前(保存の job.label と出力の組の名前。ADR-0178 決定3)。

    `<組の名前>: <コマンド>`。コマンドが分からないときは組の名前だけ。
    """
    return group if command is None else f"{group}: {command}"
BATCH_PAIR_SETTINGS = "組の設定…"
BATCH_ALL_SETTINGS = "全部の組の設定…"
BATCH_UNPAIR = "組を解除"
BATCH_SUGGEST_HOSTNAMES = "hostname で候補を出す"
BATCH_PAIR_FILES = "選んだ 2 つを組にする"
# 片方だけのファイルの左右 2 列の上の案内(REQ-233 / ADR-0171。行のドラッグで組にする)
BATCH_UNPAIRED_DRAG_NOTE = "行を反対側の行へドラッグしても組にできます"
BATCH_ACCEPT_SUGGESTIONS = "選んだ候補を組にする"
BATCH_DISMISS_SUGGESTIONS = "候補を閉じる"
# 組の候補の一覧の題(REQ-247 / ADR-0184 決定3。hostname の候補と AI の案の両方が入るため)
BATCH_SUGGESTIONS = "自動検出の組の候補"
BATCH_COL_INCLUDED = "実行する"  # 表の先頭のチェックの列の読み上げの名前
BATCH_COL_PAIR_CHECK = "組にする"  # 片方だけのファイルの行のチェックの読み上げの名前
BATCH_COL_SUGGEST_CHECK = "組にする候補"  # 候補の行のチェックの読み上げの名前
BATCH_COL_HINT = "手がかり"
BATCH_COL_SIDE_BEFORE = "変更前"
BATCH_COL_SIDE_AFTER = "変更後"
BATCH_ROW_PLATFORM = "機種"  # 表の機種の選択の読み上げの名前(行の名前の前に付ける)
# 組の設定のダイアログ
BATCH_PAIR_SETTINGS_TITLE = "組の設定"
BATCH_ALL_SETTINGS_TITLE = "全部の組の設定"
BATCH_SETTINGS_APPLY = "反映する"
BATCH_SETTINGS_FIELD_ID = "id"
BATCH_SETTINGS_FIELD_LABEL = "名前"
BATCH_SETTINGS_FIELD_SHOW_COMMAND = COMPARE_COMMAND
BATCH_SETTINGS_FIELD_BEFORE = "変更前の区間"
BATCH_SETTINGS_FIELD_AFTER = "変更後の区間"
BATCH_SETTINGS_FIELD_PROMPT = "機器のプロンプト"
BATCH_SETTINGS_FIELD_RULES = "正規化の規則"
BATCH_SETTINGS_FIELD_ENABLE_RULE = "有効にする規則"
BATCH_SETTINGS_KEEP_DEFAULT = "既定のまま"
BATCH_SETTINGS_KEEP_PAIRS = "組ごとのまま"
BATCH_SETTINGS_SELECTION_CHOICES = ("自動", "コマンド名", "番号")
BATCH_SETTINGS_PROMPT_NOTE = "ログの区切りに使う機器のプロンプトです（AI に送る指示文ではありません）"
BATCH_SETTINGS_ENABLE_RULE_NOTE = "規則の ID をカンマで分けて書きます"
BATCH_SETTINGS_PAIR_NOTE = "空の欄と「既定のまま」は、全部の組の設定を使います"
BATCH_SETTINGS_ALL_NOTE = "組ごとに書いた値は、こちらより優先します。機種と show のコマンドは、選ぶと全部の組に同じ値を入れます"
BATCH_SETTINGS_REJECTED_TITLE = "この値にはできません"
BATCH_SETTINGS_SELECT_PROMPT = "設定を直す組を、一覧で選んでください"
BATCH_UNPAIR_SELECT_PROMPT = "解除する組を、一覧で選んでください"
# 行を 2 つ以上選んでいるときに、1 組だけの操作(組の設定・比較画面で開く)を押した(ADR-0176 決定2)
BATCH_SELECT_ONE = "この操作は 1 つの組だけに使えます。組を 1 つだけ選んでください"
# 見出しの「全部」のチェックの読み上げの名前(ADR-0176 決定3)
BATCH_INCLUDED_ALL = "全部を実行する"
BATCH_SUGGEST_ALL = "全部を組にする候補にする"
# 組を作り直す前の確かめ(直した設定があるとき)
BATCH_REBUILD_CONFIRM_TITLE = "組を作り直しますか"
BATCH_REBUILD_CONFIRM_BODY = "組を作り直すと、直した設定・外した組・手で組にした組は消えて、既定に戻ります。"
BATCH_REBUILD_CONFIRM_YES = "作り直す"
BATCH_DROP_EDITED = "組を直している途中なので、ドロップでは組を作り直しません。入力は変えていません"
BATCH_SUGGEST_TITLE = "hostname の候補"
# 選んだ候補に同じファイル(変更前どうし・変更後どうし)が 2 回以上ある(何も組にしない。ADR-0177 決定2)
BATCH_SUGGEST_OVERLAP = "同じファイルを含む候補を 2 つ以上選んでいます。どちらか 1 つにしてください"


def batch_platform_auto_row(used: str | None) -> str:
    """自動の組の、実行の後の機種の列(「自動: FortiGate」。ADR-0153 決定8)。"""
    return f"{AUTO_PLATFORM_LABEL}: {platform_label(used)}"


def batch_platform_undetected(count: int) -> str:
    """実行の後の窓の下の文。自動の組で機種を判定できなかった数(REQ-209 のとおり指定なしで比べた)。"""
    return f"機種を判定できなかった組が {count} 件あります（Cisco 系・Arista の読み方で比べました）"


def batch_manifest_saved_with(path: Path, excluded: int, undetected: int) -> str:
    """「この組を保存…」の後の文。書かなかった外した組の数と、機種を書かなかった判定できない組の数を足す(0 なら足さない)。"""
    parts = [f"組のファイルを {path} に書きました。"]
    if excluded:
        parts.append(f"外した {excluded} 組は書いていません。")
    if undetected:
        parts.append(f"機種を判定できなかった {undetected} 組には機種を書いていません。")
    parts.append("比較は実行していません")
    return "".join(parts)


def batch_hostname_hint(value: str) -> str:
    """候補の行の手がかり(画面の表だけに出す。ログ・保存には書かない)。"""
    return f"hostname: {value}"


def batch_hostname_none(unread: int, duplicated: int) -> str:
    """hostname の候補が 0 件のときの知らせ。"""
    return f"hostname が同じ組の候補はありません。{batch_hostname_counts(unread, duplicated)}"


def batch_hostname_counts(unread: int, duplicated: int) -> str:
    """候補にしなかったファイルの数(ADR-0153 決定7 の「黙って選ばない」)。"""
    return f"hostname を読めなかったファイル {unread} 件、同じ側で hostname が重なったファイル {duplicated} 件は候補にしていません"


def batch_settings_rejected(problems: list[tuple[str, str]]) -> str:
    """組の設定を断ったときの知らせ(ManifestError.problems の文)。組は直す前のまま。"""
    lines = [f"この値では組を直せません（{len(problems)} 件）。組は直す前のままです"]
    lines += [f"  {where}: {message}" for where, message in problems]
    return "\n".join(lines)


def batch_pair_settings_title(job_id: str) -> str:
    return f"{BATCH_PAIR_SETTINGS_TITLE} — {job_id}"


# 片方だけのファイルについて AI に組の案を聞く(REQ-219 / ADR-0159)。どの文にも AI の答え・例外の文面・キーを入れない
BATCH_PAIR_AI_BUTTON = "AI に案を聞く…"
BATCH_PAIR_AI_TITLE = "AI に組の案を聞く"  # 送信前の確認と知らせの題
BATCH_PAIR_AI_NOTE = "ファイル名にホスト名が入っていないか確かめてください"
BATCH_PAIR_AI_HELD_BACK = "送らないファイル"
# 送る全文の欄の見出し。組の案を聞く送信には答えの見え方の決まり(LOOK_RULES)を足さないので、この文だけを送る(ADR-0177 決定4)
BATCH_PAIR_AI_PROMPT_TITLE = "送る文（この文だけを送ります）:"
BATCH_PAIR_AI_NO_FILES = "送るファイルがありません"
BATCH_PAIR_AI_UNREADABLE = "AI の案を読めませんでした。何も組にしていません"
BATCH_PAIR_AI_NONE = "AI の案はありませんでした"
# 答えを読めなかった理由と、読めなかった組の種類(REQ-223 / ADR-0162。llm_analyzer/pair_suggestion.py の REASON_*・SKIP_*)
PAIR_AI_REASON_TEXT = {
    "no_json": "答えに JSON が見つかりません",
    "many_json": "答えに JSON が 2 つ以上あります",
    "shape": "JSON の形が違います",
    "too_long": "答えが長すぎます",
}
PAIR_AI_SKIP_TEXT = {"shape": "形が違う", "unknown_name": "一覧に無い名前", "overlap": "名前の重なり"}
BATCH_PAIR_AI_HINT = "AI の案（ファイル名だけで判断）"  # 候補の行の手がかり(hostname の候補と分ける。決定6)
BATCH_PAIR_AI_ASKING = "AI に組の案を聞いています…"


def batch_pair_ai_destination(name: str, model: str | None, base_url: str | None) -> str:
    """送信前の確認の送り先(AI プロファイルの名前・モデル・接続先。ADR-0031 決定3 と同じ出し方)。キーは入れない。"""
    return f"送り先: {name}  モデル: {model or '-'}  接続先: {base_url or '(既定)'}"


def batch_pair_ai_unavailable(reason: str | None) -> str:
    """案を聞く接続が使えないとき(キーが無いなど。送っていない)。理由の文は AI 解析の枠と同じ。"""
    return f"AI に案を聞けません: {_LLM_REASON.get(reason or '', f'理由: {reason}')}"


def batch_pair_ai_too_many(count: int, limit: int) -> str:
    """送る名前が上限を超えたとき(一部だけ送ることはしない。ADR-0159 決定2)。"""
    return f"送るファイル名が {count:,} 個あり、上限の {limit:,} 個を超えています。1 つも送っていません"


def batch_pair_ai_too_long(limit: int) -> str:
    return f"送る文が {limit:,} 文字を超えるため、送っていません"


def batch_pair_ai_failed(reason: str, error_type: str, status_code: int | None) -> str:
    """送信の失敗(follow_up_failed と同じく、理由の文と例外の型の名前・HTTP の状態コードだけ。例外の文面は入れない)。"""
    detail = f"種類: {error_type}" + (f"、HTTP {status_code}" if status_code is not None else "")
    return f"AI に案を聞けませんでした: {_LLM_REASON.get(reason, f'理由: {reason}')}（{detail}）"


def batch_pair_ai_skipped(skipped: Sequence[tuple[str, int]]) -> str:
    """(種類, 数) の並びを「一覧に無い名前 2、名前の重なり 1」の形にする(並びはそのまま)。答えの中身は入れない。"""
    return "、".join(f"{PAIR_AI_SKIP_TEXT.get(kind, kind)} {count}" for kind, count in skipped)


def batch_pair_ai_unreadable(reason: str | None, skipped: Sequence[tuple[str, int]]) -> str:
    """AI の案を 1 つも使えなかったとき。reason があれば理由の文、無ければ読めなかった組の数と種類を付ける。"""
    if reason is not None:
        return f"{BATCH_PAIR_AI_UNREADABLE}（理由: {PAIR_AI_REASON_TEXT.get(reason, reason)}）"
    total = sum(count for _kind, count in skipped)
    return f"{BATCH_PAIR_AI_UNREADABLE}（読めなかった組 {total} つ: {batch_pair_ai_skipped(skipped)}）"


def batch_pair_ai_partial(read: int, skipped: Sequence[tuple[str, int]]) -> str:
    """AI の案の一部だけを候補に足したとき(読めた組の数と、読めなかった組の数と種類)。"""
    total = sum(count for _kind, count in skipped)
    return f"AI の案のうち {read} 組を候補に足しました。読めなかった組が {total} つあります（{batch_pair_ai_skipped(skipped)}）"


GUI_WEB_TEXTS.update(
    {
        "batch.button.pair_settings": BATCH_PAIR_SETTINGS,
        "batch.button.all_settings": BATCH_ALL_SETTINGS,
        "batch.button.unpair": BATCH_UNPAIR,
        "batch.button.suggest": BATCH_SUGGEST_HOSTNAMES,
        "batch.button.ask_pair_ai": BATCH_PAIR_AI_BUTTON,
        "batch.button.pair_files": BATCH_PAIR_FILES,
        "batch.unpaired.drag_note": BATCH_UNPAIRED_DRAG_NOTE,
        "batch.button.accept_suggestions": BATCH_ACCEPT_SUGGESTIONS,
        "batch.button.dismiss_suggestions": BATCH_DISMISS_SUGGESTIONS,
        "batch.suggestions": BATCH_SUGGESTIONS,
        "batch.col.included": BATCH_COL_INCLUDED,
        "batch.col.pair_check": BATCH_COL_PAIR_CHECK,
        "batch.col.suggest_check": BATCH_COL_SUGGEST_CHECK,
        "batch.col.hint": BATCH_COL_HINT,
        "batch.col.side_before": BATCH_COL_SIDE_BEFORE,
        "batch.col.side_after": BATCH_COL_SIDE_AFTER,
        "batch.col.row_platform": BATCH_ROW_PLATFORM,
        # 行の複数選択・見出しの「全部」のチェック・「設定…」(ADR-0176 / REQ-238)
        "batch.select.title": BATCH_SELECT_TITLE,
        "batch.select.one": BATCH_SELECT_ONE,
        "batch.dialog.ok": DIALOG_OK,
        "batch.col.included_all": BATCH_INCLUDED_ALL,
        "batch.col.suggest_all": BATCH_SUGGEST_ALL,
        "batch.button.settings": COMPARE_BUTTON_SETTINGS,
    }
)


# pywebview の設定画面(static/settings.html。REQ-104)と、見本のページの確かめのダイアログの文。
# 上の定数を使うので、GUI_WEB_TEXTS(ファイルの先頭)に、ここで足す
GUI_WEB_TEXTS.update(
    {
        "settings.title": SETTINGS_TITLE,
        "settings.button.migrate": SETTINGS_BUTTON_MIGRATE,
        "settings.profiles": PROFILES_TITLE,
        "settings.button.add": SETTINGS_BUTTON_NEW,
        "settings.button.duplicate": SETTINGS_BUTTON_DUPLICATE,
        "settings.button.delete_profile": SETTINGS_BUTTON_DELETE,
        "settings.edit": PROFILE_EDIT_TITLE,
        "settings.field.name": SETTINGS_FIELD_NAME,
        "settings.field.api_format": SETTINGS_FIELD_API_FORMAT,
        "settings.field.model": SETTINGS_FIELD_MODEL,
        "settings.field.base_url": SETTINGS_FIELD_BASE_URL,
        "settings.button.save_profile": SETTINGS_BUTTON_SAVE,
        "settings.field.template": SETTINGS_FIELD_TEMPLATE,
        "settings.field.auth_header": SETTINGS_FIELD_AUTH,
        "settings.wire": SETTINGS_WIRE_PANE,
        "settings.field.max_output_tokens": SETTINGS_FIELD_MAX_TOKENS,
        "settings.field.max_tokens_name": SETTINGS_FIELD_MAX_TOKENS_NAME,
        "settings.note.max_tokens_name": SETTINGS_NOTE_MAX_TOKENS_NAME,
        "settings.field.thinking": SETTINGS_FIELD_THINKING,
        "settings.field.thinking_name": SETTINGS_FIELD_THINKING_NAME,
        "settings.field.thinking_on": SETTINGS_FIELD_THINKING_ON,
        "settings.field.thinking_off": SETTINGS_FIELD_THINKING_OFF,
        "settings.note.thinking": SETTINGS_NOTE_THINKING,
        "settings.field.query": SETTINGS_FIELD_QUERY,
        "settings.note.query": SETTINGS_NOTE_QUERY,
        "settings.field.custom_url": SETTINGS_FIELD_CUSTOM_URL,
        "settings.field.custom_auth": SETTINGS_FIELD_AUTH,
        "settings.field.custom_headers": SETTINGS_FIELD_CUSTOM_HEADERS,
        "settings.note.custom_headers": SETTINGS_NOTE_CUSTOM_HEADERS,
        "settings.field.custom_body": SETTINGS_FIELD_CUSTOM_BODY,
        "settings.note.custom_body": SETTINGS_NOTE_CUSTOM_BODY,
        "settings.field.answer_path": SETTINGS_FIELD_ANSWER_PATH,
        "settings.note.answer_path": SETTINGS_NOTE_ANSWER_PATH,
        "settings.button.custom_export": SETTINGS_BUTTON_CUSTOM_EXPORT,
        "settings.button.custom_import": SETTINGS_BUTTON_CUSTOM_IMPORT,
        "settings.shape": SETTINGS_SHAPE_TITLE,
        "settings.field.cli_path": SETTINGS_FIELD_CLI_PATH,
        "settings.button.choose_cli": SETTINGS_BUTTON_CHOOSE_CLI,
        "settings.note.cli_path": SETTINGS_NOTE_CLI_PATH,
        "settings.field.timeout_seconds": SETTINGS_FIELD_TIMEOUT,
        "settings.field.cli_effort": SETTINGS_FIELD_CLI_EFFORT,
        "settings.note.thinking_cost": SETTINGS_NOTE_THINKING_COST,
        "settings.note.cli_login": SETTINGS_NOTE_CLI_LOGIN,
        "settings.note.codex_config": CODEX_CONFIG_NOTE,
        "settings.key": PROFILE_KEY_BOX,
        "settings.key.sources": KEY_SOURCES_TITLE,
        "settings.field.env_suffix": SETTINGS_FIELD_ENV_SUFFIX,
        "settings.button.save_key": SETTINGS_BUTTON_SAVE_KEY,
        "settings.button.delete_key": SETTINGS_BUTTON_DELETE,
        "settings.button.check": LLM_CHECK_TITLE,
        "settings.button.use": SETTINGS_BUTTON_USE,
        # 窓のタブ(ADR-0154 / REQ-214 §5)
        "settings.tab.profiles": PROFILES_TITLE,
        "settings.tab.prompt": PROMPT_TEMPLATE_TITLE,
        "settings.tab.auto": SETTINGS_TAB_AUTO,
        "settings.tab.file": SETTINGS_TAB_FILE,
        "settings.note.auto_send": SETTINGS_NOTE_AUTO_SEND,
        "settings.note.save_both": SETTINGS_NOTE_SAVE_BOTH,
        "settings.note.character": SETTINGS_NOTE_CHARACTER,
        # 上限の欄は「比較と自動の動き」のタブ(ADR-0167 決定5)。鍵の名前は変えず、pywebview だけの文を指す
        "settings.config": CONFIG_PANE_WEB,
        "settings.config.limits": LIMITS_TITLE_WEB,
        "settings.config.per_job": LIMIT_PER_JOB_WEB,
        "settings.config.total": LIMIT_TOTAL_WEB,
        "settings.note.limits_moved": SETTINGS_NOTE_LIMITS_MOVED,
        "settings.config.words": SECRET_WORDS_TITLE,
        "settings.config.auto_compare": SETTINGS_AUTO_COMPARE,
        "settings.config.auto_send_llm": SETTINGS_AUTO_SEND_LLM,
        "settings.config.character": SETTINGS_CHARACTER,
        "settings.button.save_config": SETTINGS_BUTTON_SAVE,
        # 「AI への指示文」の枠(REQ-116 §3。設定ファイルの枠の外の、独立した枠)
        "settings.prompt": PROMPT_TEMPLATE_TITLE,
        "settings.prompt.button.add": SETTINGS_BUTTON_NEW,
        "settings.prompt.button.duplicate": SETTINGS_BUTTON_DUPLICATE,
        "settings.prompt.button.delete": SETTINGS_BUTTON_DELETE,
        "settings.prompt.button.save": SETTINGS_BUTTON_SAVE,
        "settings.prompt.button.use": SETTINGS_BUTTON_USE,
        "settings.prompt.field.name": SETTINGS_FIELD_NAME,
        "settings.prompt.field.template": PROMPT_FIELD_TEMPLATE,
        "settings.note.prompt_builtin": PROMPT_BUILTIN_NOTE,
        # 欄の下の説明(ADR-0167 決定1〜3)。印ごとの行の鍵は下で PROMPT_MARKS から作る
        "settings.note.prompt_diff_once": PROMPT_DIFF_ONCE_NOTE,
        "settings.prompt.marks": PROMPT_MARKS_TITLE,
        "settings.prompt.marks.col_mark": PROMPT_MARKS_COL_MARK,
        "settings.prompt.marks.col_meaning": PROMPT_MARKS_COL_MEANING,
        **{f"settings.prompt.mark.{name}.name": _mark(name) for name in PROMPT_MARKS},
        **{f"settings.prompt.mark.{name}": PROMPT_MARK_MEANINGS[name] for name in PROMPT_MARKS},
        "settings.note.prompt_plain": PROMPT_NOTE_PLAIN,
        "settings.note.prompt_premise": PROMPT_NOTE_PREMISE,
        # 「プロンプト」のタブの「毎回付ける前提」(ADR-0172 決定6 / REQ-234 §5)。案内の 3 行は view から入れる
        "settings.premise": SETTINGS_PREMISE_TITLE,
        "settings.premise.button.save": SETTINGS_PREMISE_SAVE,
        "settings.diagnostics": SETTINGS_DIAGNOSTICS,
        "settings.diagnostics.fingerprints": FINGERPRINTS_TITLE,
        "settings.diagnostics.versions": SETTINGS_VERSIONS_TITLE,
        "settings.diagnostics.config_file": SETTINGS_CONFIG_FILE_TITLE,
        "settings.diagnostics.location": SETTINGS_CONFIG_LOCATION,
        "settings.button.open_folder": SETTINGS_BUTTON_OPEN_FOLDER,
        # 窓の下の操作の行の「プロファイルを書き出す…」「プロファイルを取り込む…」(REQ-119 §4)
        "settings.button.export_profiles": SETTINGS_BUTTON_EXPORT_PROFILES,
        "settings.button.import_profiles": SETTINGS_BUTTON_IMPORT_PROFILES,
        "settings.button.template": SETTINGS_BUTTON_TEMPLATE,
        "settings.button.close": SETTINGS_BUTTON_CLOSE,
        "sample.section.dialog": "確かめのダイアログ",
        "sample.dialog.confirm": "削除…",
        "sample.dialog.notice": "知らせを出す",
        "sample.dialog.confirm.title": "プロファイルの削除",
        "sample.dialog.confirm.body": "プロファイル「lab」を削除しますか？\nこのプロファイルの API キーも削除します",
        "sample.dialog.notice.title": "AI プロファイル",
        "sample.dialog.notice.body": "プロファイル「lab」を保存しました",
        "sample.dialog.yes": DIALOG_YES,
        "sample.dialog.no": DIALOG_NO,
        "sample.dialog.ok": DIALOG_OK,
        "sample.dialog.answer.yes": "「はい」を選びました。",
        "sample.dialog.answer.no": "「いいえ」を選びました。",
    }
)


# --- 窓から HTML と CSV で出力する(ADR-0156 / REQ-216)。pywebview の比較の窓と一括比較の窓だけ。CLI と wx には足さない ---
# 人に渡す文書の文。どの文にもパス以外の値(設定の行・秘密の語・AI の答え)を入れない

EXPORT_BUTTON = "HTML・CSV で出力…"
EXPORT_DIALOG_TITLE = "HTML・CSV で出力"
# 形を選ぶダイアログの本文と、HTML の案内(ADR-0156 決定9)
EXPORT_GUIDE = (
    "秘密の行は AI に送るときと同じ判定で伏せます。語の無い秘密や IP アドレス、ホスト名、説明文は伏せません。"
    "渡す前に中身を確かめてください"
)
# 形の選択肢(ADR-0156 決定1)。組の一覧はまとめて比較と一括比較のときだけ出す
EXPORT_CHOICE_HTML = "HTML"
EXPORT_CHOICE_DIFFS = "CSV（差分の行）"
EXPORT_CHOICE_PAIRS = "CSV（組の一覧）"
EXPORT_CHOICE_CANCEL = "やめる"
# HTML の題と見出し
EXPORT_TITLE_COMPARE = "DriftPilot 比較の結果"
EXPORT_TITLE_BATCH = "DriftPilot 一括比較の結果"
EXPORT_SAVED_AT = "出力の日時"
EXPORT_STATUS = "状態"
EXPORT_COUNTS = "件数"
EXPORT_PAIRS = "組の一覧"
EXPORT_STRUCTURED = "構造化比較の差分"
EXPORT_RAW = "単純比較の差分"
EXPORT_ANSWER = "AI の答え"
# AI の答えの見出しの後、答えの前に出す最初の依頼の見出し(ADR-0179 決定1)
EXPORT_FIRST_REQUEST = "最初の依頼"
EXPORT_NO_ROWS = "差分はありません"
EXPORT_GAP = "…"
# CSV の列(BATCH_COL_* と COMPARE_COL_* に無いもの)
EXPORT_COL_PAIR = "組"
EXPORT_COL_GROUP = "元の組"  # 組の一覧の CSV で、まとめて比べた組のコマンドの組が属する組(ADR-0178 決定3)
EXPORT_COL_KIND = "種類"
EXPORT_COL_BEFORE_LINE = "変更前の行"
EXPORT_COL_AFTER_LINE = "変更後の行"
EXPORT_KIND_STRUCTURED = COMPARE_TAB_STRUCTURED
EXPORT_KIND_RAW = COMPARE_TAB_RAW
# 知らせ(題は COMPARE_SAVE_FAILED_TITLE)
EXPORT_REDACT_FAILED = "秘密の行を伏せる判定ができないため、出力していません"


def export_omitted(count: int) -> str:
    """HTML の要約の、省いた行の数(ADR-0156 決定2。行の形は出さない)。"""
    return f"秘密を含むかもしれないため、{count:,} 行の中身を出力していません"


def export_settings_unreadable(reason: str) -> str:
    """設定が読めないときの知らせ(ADR-0156 決定2。秘密の語が足りないまま出さない)。reason は SettingsError の文。"""
    return f"設定を読めないため、出力していません。秘密の語が足りないまま出さないためです。{reason}"


def export_saved(path: Path) -> str:
    """出力した後の窓の下の文。"""
    return f"{path} に出力しました。秘密の行は伏せましたが、渡す前に中身を確かめてください"


GUI_WEB_TEXTS.update(
    {
        "compare.button.export": EXPORT_BUTTON,
        "compare.export.title": EXPORT_DIALOG_TITLE,
        "compare.export.body": EXPORT_GUIDE,
        "batch.button.export": EXPORT_BUTTON,
        "batch.export.title": EXPORT_DIALOG_TITLE,
        "batch.export.body": EXPORT_GUIDE,
    }
)


# AI の面を別の窓で出す(ADR-0160 / REQ-220)。pywebview の比較の窓と AI の窓だけ。CLI・一括比較・wx には無い
AI_PANE_TITLE = "DriftPilot — AI 解析"
COMPARE_AI_DETACH = "別の窓で開く"  # AI の面の見出しの右端の「↗」の title と aria-label
COMPARE_AI_FRONT = "別の窓を前に出す"  # 別の窓が開いている間、結果のタブの行の右端に出す「↗」の title と aria-label
COMPARE_AI_DETACH_ICON = "↗"  # どちらのアイコンも同じ文字(HTML に文を書かない決まりのため、data-text で入れる)

GUI_WEB_TEXTS.update(
    {
        "compare.ai.detach": COMPARE_AI_DETACH,
        "compare.ai.front": COMPARE_AI_FRONT,
        "compare.ai.detach_icon": COMPARE_AI_DETACH_ICON,
    }
)


# --- 単純比較の設定(ADR-0166 / REQ-227)。CLI・比較の窓・設定の窓・HTML の出力で同じ言葉を使う ---
# 文に利用者の式と置き換える文字は入れない(件数と種類だけ。re.error の文だけは括弧で添える)

RAW_OPTIONS_TITLE = "単純比較の設定"
RAW_SETTINGS_PREFIX = "単純比較の設定: "
RAW_FIELD_IGNORE = "無視する行"
RAW_FIELD_REPLACE = "置き換え"
RAW_FIELD_SPACE = "空白の違い"
# 空白の 4 つの名前(ADR-0166 決定3 の表の画面の名前)。llm_analyzer/payload.py の表と同じ(テストで確かめる)
RAW_SPACE_LABEL = {
    "off": "空白も比べる",
    "trailing": "行末の空白を無視",
    "amount": "空白の数を無視",
    "all": "空白をすべて無視",
}
RAW_BLANK_LABEL = "空行を無視"
RAW_CASE_LABEL = "大文字と小文字を無視"
RAW_MARK_IGNORED = "無視"
RAW_MARK_EQUATED = "同じ"
# 行の移動と比べ方(REQ-228 §8.3)。llm_analyzer/payload.py の言葉と同じ(テストで確かめる)
RAW_MOVES_LABEL = "移動を検出"
RAW_ALGORITHM_PART = {"minimal": "比べ方: 最小", "patience": "比べ方: patience"}
RAW_ALGORITHM_FELL_BACK = "比べ方: 最小（変更が多いため標準で比べました）"
RAW_MOVES_COUNT = "移動 {count} か所"
RAW_MOVES_SKIPPED = "移動は差分が多いため探していません"
# 表の印の文(REQ-228 §8.2)。{number} は移動の番号
RAW_MARK_MOVED_FROM = "移動元 {number}"
RAW_MARK_MOVED_TO = "移動先 {number}"
# CLI の text の、移動を探さなかったときの行
RAW_MOVES_SKIPPED_CLI = "移動の検出: 差分の行が多いため探していません"
# 差分のみ表示の前後の行数の誤り(REQ-228 §2)
RAW_CONTEXT_PROBLEM = "差分のみ表示の前後の行数は 0 から 20 にしてください"
RAW_FIELD_ALGORITHM = "比べ方"
RAW_ALGORITHM_LABEL = {
    "standard": "標準",
    "minimal": "最小（変更の行がいちばん少ない）",
    "patience": "patience（同じ行が多い config で見やすい）",
}
# 一括比較の窓(REQ-228 §7.2)
BATCH_RAW_NONE = "単純比較の設定: なし"
BATCH_RAW_PAIRS = "（組ごとの設定: {count} 組）"
BATCH_NOTE_RAW_OPTIONS = "単純比較の設定は、設定画面の「単純比較」の既定か、組のファイルの raw_options で変えます"
RAW_SETTINGS_SAVED_PREFIX = "保存した結果の"
RAW_OPTIONS_INVALID_TITLE = "単純比較の設定"
RAW_DEFAULTS_TITLE = "単純比較の既定"
SETTINGS_TAB_RAW = "単純比較"
SETTINGS_NOTE_RAW_DEFAULTS = (
    "比較の窓を開いたときの設定です。比較の窓で変えた設定は、その窓を閉じるまで使います。"
    "コマンド（CLI）はこの既定を読みません"
)
SETTINGS_NOTE_SAVE_RAW = "このタブの「保存」は raw-compare.json だけを書きます（ほかのタブの欄は書きません）"

# 例(文と式の組)。どれも check_raw_options を通る(テストで確かめる)
RAW_IGNORE_EXAMPLES: tuple[tuple[str, str], ...] = (
    ("「! Last configuration change」の行", r"^! Last configuration change at "),
    ("「! NVRAM config last updated」の行", r"^! NVRAM config last updated at "),
    ("「Current configuration : … bytes」の行", r"^Current configuration : \d+ bytes"),
    ("「ntp clock-period」の行", r"^ntp clock-period "),
    ("稼働時間（uptime）の行", r"\buptime is "),
    ("FortiGate の「#config-version=」の行", r"^#config-version="),
)
# 例(文・式・置き換える文字)
RAW_REPLACE_EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ("時刻（12:34:56）を <時刻> にする", r"\b\d{1,2}:\d{2}:\d{2}(\.\d+)?\b", "<時刻>"),
    ("稼働時間の値を <稼働時間> にする", r"(uptime is ).*", r"\1<稼働時間>"),
    ("数（カウンタなど）を <数> にする（すべての数に当たります）", r"\b\d+\b", "<数>"),
)


def raw_defaults_unreadable(reason: str) -> str:
    """既定のファイルが読めないときの帯(RAW_DEFAULTS_UNREADABLE)。reason は SettingsError の文(場所と理由の種類だけ)。"""
    return f"単純比較の既定のファイル（raw-compare.json）を読めません。すべてオフで比べます: {reason}"


RAW_DEFAULTS_UNREADABLE = raw_defaults_unreadable("{理由}")  # 文の形(画面は raw_defaults_unreadable で作る)


def raw_defaults_saved(path: Path) -> str:
    return f"{path} に保存しました"


def _raw_position(problem: Any) -> str:
    """「無視する行の 2 行目の式」「置き換えの 3 組目の置き換える文字」の形。"""
    if problem.field == "ignore_lines":
        return f"{RAW_FIELD_IGNORE}の {problem.index} 行目の式"
    target = "置き換える文字" if getattr(problem, "part", None) == "replacement" else "式"
    return f"{RAW_FIELD_REPLACE}の {problem.index} 組目の{target}"


def raw_option_problem_line(problem: Any) -> str:
    """設定の誤り 1 件の文(RawOptionProblem)。式は入れない。"""
    from driftpilot.raw_keys import MAX_PATTERN_CHARS, MAX_PATTERNS

    kind = problem.kind
    if problem.field == "algorithm":
        return f"{RAW_FIELD_ALGORITHM}の値を使えません（標準・最小・patience のどれかにしてください）"
    if problem.field == "whitespace":
        return f"{RAW_FIELD_SPACE}の値を使えません（{'・'.join(RAW_SPACE_LABEL.values())}のどれかにしてください）"
    if kind == "too_many":
        unit = "個" if problem.field == "ignore_lines" else "組"
        name = RAW_FIELD_IGNORE if problem.field == "ignore_lines" else RAW_FIELD_REPLACE
        return f"{name}は {MAX_PATTERNS} {unit}までです"
    where = _raw_position(problem)
    if kind == "too_long":
        return f"{where}は {MAX_PATTERN_CHARS} 文字までです"
    if kind == "bad_pattern":
        return f"{where}を読めません（{problem.detail}）"
    if kind == "bad_replacement":
        return f"{where}を使えません（{problem.detail}）"
    if kind == "matches_empty":
        return f"{where}は空の行にも当たるため使えません"
    if kind == "nested_repeat":
        return (
            f"{where}は、繰り返しの中に繰り返しがあり、比べるのに時間がかかりすぎるおそれがあるため使えません"
            "（例: (a+)+）"
        )
    return f"{where}を使えません"


def raw_option_problem_lines(problems: "Sequence[Any]") -> list[str]:
    """設定の誤りの文(1 件 1 行)。"""
    return [raw_option_problem_line(p) for p in problems]


def raw_options_parts(plain: "dict[str, Any]", algorithm_used: str | None = None) -> list[str]:
    """既定でない設定の名前(「無視する行 2 件」「置き換え 1 組」「空白の数を無視」…)。plain は options_to_plain の形。

    REQ-228: 「移動を検出」と比べ方(標準は書かない)を後ろに足す。algorithm_used(結果の実際に使った比べ方)を渡すと、
    最小が標準に戻ったときは「比べ方: 最小（変更が多いため標準で比べました）」にする。
    """
    parts: list[str] = []
    if plain.get("ignore_lines"):
        parts.append(f"{RAW_FIELD_IGNORE} {len(plain['ignore_lines'])} 件")
    if plain.get("replacements"):
        parts.append(f"{RAW_FIELD_REPLACE} {len(plain['replacements'])} 組")
    space = plain.get("whitespace", "off")
    if space != "off":
        parts.append(RAW_SPACE_LABEL.get(space, space))
    if plain.get("ignore_blank_lines"):
        parts.append(RAW_BLANK_LABEL)
    if plain.get("ignore_case"):
        parts.append(RAW_CASE_LABEL)
    if plain.get("detect_moves"):
        parts.append(RAW_MOVES_LABEL)
    algorithm = plain.get("algorithm", "standard")
    if algorithm == "minimal" and algorithm_used == "standard":
        parts.append(RAW_ALGORITHM_FELL_BACK)
    elif algorithm in RAW_ALGORITHM_PART:
        parts.append(RAW_ALGORITHM_PART[algorithm])
    return parts


def raw_options_summary_text(plain: "dict[str, Any]", algorithm_used: str | None = None) -> str | None:
    """「単純比較の設定: 無視する行 2 件・置き換え 1 組・空白の数を無視」の形。既定なら None。"""
    parts = raw_options_parts(plain, algorithm_used)
    return RAW_SETTINGS_PREFIX + "・".join(parts) if parts else None


def raw_moves_part(moves: int, skipped: bool) -> str | None:
    """「移動 2 か所」か「移動は差分が多いため探していません」。移動が無ければ None(REQ-228 §8.3)。"""
    if skipped:
        return RAW_MOVES_SKIPPED
    return RAW_MOVES_COUNT.format(count=moves) if moves else None


def raw_move_mark(mark: str, number: int) -> str:
    """表の印の文(「移動元 1」「移動先 1」)。mark は "<"(移動元)か ">"(移動先)。"""
    return (RAW_MARK_MOVED_FROM if mark == "<" else RAW_MARK_MOVED_TO).format(number=number)


def raw_move_cli_line(number: int, before_start: int, after_start: int, count: int) -> str:
    """CLI の text の移動の 1 行(「移動 1: 変更前 10〜14 行目 → 変更後 30〜34 行目」)。"""
    return (
        f"移動 {number}: 変更前 {before_start}〜{before_start + count - 1} 行目 → "
        f"変更後 {after_start}〜{after_start + count - 1} 行目"
    )


def batch_raw_pairs_suffix(count: int) -> str:
    """一括比較の窓の下の行の後ろ(「（組ごとの設定: 3 組）」)。"""
    return BATCH_RAW_PAIRS.format(count=count)


def raw_set_aside_line(before: int, after: int, equated: int) -> str | None:
    """「無視した行 12 行（変更前 7・変更後 5）・同じとみなした行 3 組」。どれも 0 なら None。"""
    if before == 0 and after == 0 and equated == 0:
        return None
    return f"無視した行 {before + after} 行（変更前 {before}・変更後 {after}）・同じとみなした行 {equated} 組"


def raw_ignored_counts_line(before: int, after: int, equated: int) -> str:
    """CLI の text の 2 行目(「無視した行: 変更前 N 行・変更後 M 行。同じとみなした行: K 組」)。"""
    return f"無視した行: 変更前 {before} 行・変更後 {after} 行。同じとみなした行: {equated} 組"


GUI_WEB_TEXTS.update(
    {
        "raw_options.title": RAW_OPTIONS_TITLE,
        "raw_options.ignore": "無視する行（正規表現。1 行に 1 つ）",
        "raw_options.ignore.note": (
            "当たる行は比べません。空の行に当たる式（.* など）は使えません。空行は「空行を無視」を使ってください"
        ),
        "raw_options.example": "例から足す…",
        "raw_options.replace": "置き換えてから比べる（正規表現）",
        "raw_options.replace.pattern": "式",
        "raw_options.replace.text": "置き換える文字",
        "raw_options.replace.add": "置き換えを足す",
        "raw_options.replace.remove": "外す",
        "raw_options.space": RAW_FIELD_SPACE,
        "raw_options.space.off": RAW_SPACE_LABEL["off"],
        "raw_options.space.trailing": RAW_SPACE_LABEL["trailing"],
        "raw_options.space.amount": RAW_SPACE_LABEL["amount"],
        "raw_options.space.all": RAW_SPACE_LABEL["all"],
        "raw_options.space.indent_note": "字下げの違いも無視します。config の親子の違いが見えなくなることがあります",
        "raw_options.blank": RAW_BLANK_LABEL,
        "raw_options.case": RAW_CASE_LABEL,
        "raw_options.reset": "既定に戻す",
        "raw_options.defaults_title": RAW_DEFAULTS_TITLE,
        "settings.tab.raw": SETTINGS_TAB_RAW,
        "settings.note.raw_defaults": SETTINGS_NOTE_RAW_DEFAULTS,
        "settings.note.save_raw": SETTINGS_NOTE_SAVE_RAW,
        "settings.button.save_raw": SETTINGS_BUTTON_SAVE,
        # REQ-228 §8.4
        "raw_options.context": "差分のみ表示の前後の行数",
        "raw_options.moves": "行の移動を見つける",
        "raw_options.moves.note": (
            "3 行以上の塊が別の場所へ動いたとき、「移動元」「移動先」と出します。移動も差分として数えます"
        ),
        "raw_options.algorithm": RAW_FIELD_ALGORITHM,
        "raw_options.algorithm.standard": RAW_ALGORITHM_LABEL["standard"],
        "raw_options.algorithm.minimal": RAW_ALGORITHM_LABEL["minimal"],
        "raw_options.algorithm.patience": RAW_ALGORITHM_LABEL["patience"],
        "raw_options.algorithm.note": "最小は、変更が 1,000 行を超えると標準で比べます",
        "batch.note.raw_options": BATCH_NOTE_RAW_OPTIONS,
    }
)


# --- 新しい版の確かめ(ADR-0188 決定2 / REQ-252) ---

# 確かめられないときの理由(REQ-252 §3 の表)。update.check が UpdateStatus.reason に入れる
UPDATE_REASON_NOT_FOUND = "公開されている版が見つかりません"
UPDATE_REASON_BUSY = "GitHub が混み合っています。しばらくしてから確かめてください"
UPDATE_REASON_UNREACHABLE = "GitHub につながりませんでした。ネットワークとプロキシの設定を確かめてください"
UPDATE_REASON_UNREADABLE = "GitHub の答えを読めませんでした"


def update_reason_http(code: int) -> str:
    """404・403・429 のほかの HTTP の誤り(REQ-252 §3)。"""
    return f"GitHub から答えがありませんでした（HTTP {code}）"


# 設定の窓の「更新」タブ(REQ-252 §4)
SETTINGS_TAB_UPDATE = "更新"
UPDATE_CHECKING = "確かめています…"
# ソースから入れた人への案内(install_kind が "source" のときだけ出す。ADR-0188 決定3)
UPDATE_SOURCE_NOTE = 'ソースから入れた場合は、git pull の後に pip install -e ".[gui,llm]" で新しくしてください'


def update_current_version(version: str) -> str:
    """「更新」タブのいちばん上の行。"""
    return f"今の版: {version}"


def update_is_latest(version: str) -> str:
    """確かめた結果が最新のとき。"""
    return f"最新の版です（{version}）"


def update_is_newer(latest: str, published: str | None) -> str:
    """確かめた結果、新しい版があるとき。公開日が分からなければ括弧を出さない。"""
    if published:
        return f"新しい版 {latest} があります（{published} 公開）"
    return f"新しい版 {latest} があります"


def update_notice_text(latest: str, current: str) -> str:
    """起動したときの知らせ(比較の窓と一括比較の窓の上の帯。REQ-252 §5)。"""
    return f"新しい版 {latest} があります（今は {current}）"


GUI_WEB_TEXTS.update(
    {
        "settings.tab.update": SETTINGS_TAB_UPDATE,
        "settings.button.check_update": "更新を確かめる",
        "settings.button.open_releases": "Releases のページを開く",
        "settings.config.auto_update_check": "起動したときに新しい版を確かめる（自動アップデートチェック）",
        "settings.note.auto_update_check": (
            "オンにすると、起動のたびに GitHub（api.github.com）につなぎます。送るのは DriftPilot の版だけです"
        ),
        # 比較の窓と一括比較の窓の上の知らせ(REQ-252 §5)
        "update_notice.open": "更新…",
        "update_notice.dismiss": "閉じる",
    }
)


# 設定の窓の「表示」タブ(ADR-0189 / REQ-256 §4)。「保存」の文は「更新」タブと同じ settings.button.save_config
SETTINGS_TAB_DISPLAY = "表示"
DISPLAY_TITLE = "画面の色"
DISPLAY_THEME_SYSTEM = "システム設定に合わせる"
DISPLAY_THEME_LIGHT = "白モード"
DISPLAY_THEME_DARK = "黒モード"
DISPLAY_FRAME_NOTE = "窓の枠（タイトルバー）の色は、OS の設定のままです"

GUI_WEB_TEXTS.update(
    {
        "settings.tab.display": SETTINGS_TAB_DISPLAY,
        "settings.display.title": DISPLAY_TITLE,
        "settings.display.system": DISPLAY_THEME_SYSTEM,
        "settings.display.light": DISPLAY_THEME_LIGHT,
        "settings.display.dark": DISPLAY_THEME_DARK,
        "settings.note.display_frame": DISPLAY_FRAME_NOTE,
    }
)


# --- 署名つきのその場の更新(ADR-0188 決定3・4 / REQ-253) ---

# 誤りの文(REQ-253 §7 の表)。update.errors の型が持つ
UPDATE_SIGNATURE_FAILED = "配る物の署名を確かめられませんでした。入れ替えません"
UPDATE_INTEGRITY_FAILED = "取ってきた物が壊れているか、違う物です。入れ替えません"
UPDATE_NO_ASSET = "この OS に合う配る物がありません"
UPDATE_NO_ASSET_MAC = "この Mac に合う配る物がありません"
UPDATE_WRONG_LOCATION = "アプリケーションフォルダーに DriftPilot を入れてから更新してください"
UPDATE_CONTENT_FAILED = "配る物の中身を読めませんでした。入れ替えません"
UPDATE_NOT_STARTED = "更新を始められませんでした"


def update_not_writable(place: str) -> str:
    """入れ替える場所に書けないとき(REQ-253 §5)。"""
    return f"この場所の DriftPilot は入れ替えられません（{place} に書けません）。Releases のページから入れ直してください"


# 「更新」タブの「更新する」の進み具合と確かめの窓(REQ-253 §7)
UPDATE_TITLE = "更新"
UPDATE_VERIFYING = "確かめています…"
UPDATE_PREPARING = "準備しています…"
UPDATE_APPLY_YES = "入れ替える"
UPDATE_APPLY_NO = "やめる"
_MIB = 1024 * 1024


def update_downloading(done: int, total: int) -> str:
    """取ってきている間の進み具合(1 MB = 1,048,576 バイト。小数 1 桁)。"""
    return f"取ってきています… {done / _MIB:.1f} / {total / _MIB:.1f} MB"


def update_apply_confirm(version: str) -> str:
    """準備ができた後の確かめの窓の本文。"""
    return (
        f"DriftPilot を閉じて {version} に入れ替えます。保存していない比較の結果は消えます。"
        "入れ替えた後に、新しい版が開きます。"
    )


def update_failed_notice(log: str) -> str:
    """--update-failed で起動したときの知らせ(比較の窓と一括比較の窓の上)。"""
    return f"更新できなかったので、元の版に戻しました。記録: {log}"


def update_done_notice(version: str) -> str:
    """--update-started で起動したときの知らせ(比較の窓と一括比較の窓の上)。"""
    return f"{version} に更新しました"


GUI_WEB_TEXTS.update(
    {
        "settings.button.start_update": "更新する",
        "settings.button.cancel_update": "やめる",
    }
)

# 自己診断の「署名の確かめ」の行(REQ-253 §10)
SELFCHECK_SIGNATURE = "署名の確かめ"


def selfcheck_signature(marks: list[tuple[str, str]]) -> str:
    """署名の確かめが通ったときの詳しい欄(例: Ed25519・main 5c50466f5b7455a6・backup 80db1dccd1059a79)。"""
    return "・".join(["Ed25519", *(f"{name} {mark}" for name, mark in marks)])


def selfcheck_signature_failed(what: str) -> str:
    """署名の確かめが合わないとき(what は確かめたものの名前)。"""
    return f"署名の確かめが合いません: {what}"
