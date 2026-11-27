"""AI に送る差分の上限の既定値と、プロンプトの印の名前(ADR-0167)。ほかのモジュールを読み込まない。

messages.py が画面の文(「1 組あたり（既定 100 件）」、使える印の表と誤りの知らせ)をこの値から作る。
messages.py は比較の部品(core・diff_engine・llm_analyzer)を読み込むときに読まない(自己診断のため)ので、
値の元はここに置き、llm_analyzer.types・llm_analyzer.prompt・core.batch_llm はここから読む(値は変えない)。
"""

# 1組あたりに送る差分の上限の既定値(ADR-0018)
DEFAULT_MAX_DIFFS = 100
# 送る差分の合計の上限の既定値(ADR-0031 決定4)。1組の上限(既定 100)の10組分
DEFAULT_MAX_TOTAL_DIFFS = 1000

# AI に送る文全体の上限の既定値・範囲・全文の余白(ADR-0259 決定1・2)。全文の上限 = 送る文全体の上限 − 余白
DEFAULT_MAX_PROMPT_CHARS = 600_000
MIN_MAX_PROMPT_CHARS = 100_000
MAX_MAX_PROMPT_CHARS = 2_000_000
FULL_TEXT_MARGIN_CHARS = 50_000
# 既定の全文の上限(既定 − 余白)
DEFAULT_MAX_FULL_TEXT_CHARS = DEFAULT_MAX_PROMPT_CHARS - FULL_TEXT_MARGIN_CHARS
# 全文の上限が設定で変わる前の保存(補足の欄に使った上限が無い保存)が断ったときの固定の上限。古い保存の画面の文に使う
LEGACY_MAX_FULL_TEXT_CHARS = 150_000


def full_text_limit(max_prompt_chars: int = DEFAULT_MAX_PROMPT_CHARS) -> int:
    """全文の上限(送る文全体の上限 − 余白)。"""
    return max_prompt_chars - FULL_TEXT_MARGIN_CHARS


def is_prompt_chars(value: object) -> bool:
    """送る文全体の上限にできる値か(真偽値でない整数で、範囲の中。ADR-0259 決定1)。"""
    return type(value) is int and MIN_MAX_PROMPT_CHARS <= value <= MAX_MAX_PROMPT_CHARS


# ひな形の印(ADR-0075 決定1)。{{diff}} だけが必須で、ちょうど1回
MARK_DIFF = "diff"
MARK_DIFF_FORMAT = "diff_format"
MARK_DIFF_TOTAL = "diff_total"
MARK_SENT_RANGE = "sent_range"
MARK_MAX_DIFFS = "max_diffs"
MARK_NOTES = "notes"
PROMPT_MARKS = (MARK_DIFF, MARK_DIFF_FORMAT, MARK_DIFF_TOTAL, MARK_SENT_RANGE, MARK_MAX_DIFFS, MARK_NOTES)
