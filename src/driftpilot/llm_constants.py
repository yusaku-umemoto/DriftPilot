"""AI に送る差分の上限の既定値と、プロンプトの印の名前(ADR-0167)。ほかのモジュールを読み込まない。

messages.py が画面の文(「1 組あたり（既定 100 件）」、使える印の表と誤りの知らせ)をこの値から作る。
messages.py は比較の部品(core・diff_engine・llm_analyzer)を読み込むときに読まない(自己診断のため)ので、
値の元はここに置き、llm_analyzer.types・llm_analyzer.prompt・core.batch_llm はここから読む(値は変えない)。
"""

# 1組あたりに送る差分の上限の既定値(ADR-0018)
DEFAULT_MAX_DIFFS = 100
# 送る差分の合計の上限の既定値(ADR-0031 決定4)。1組の上限(既定 100)の10組分
DEFAULT_MAX_TOTAL_DIFFS = 1000

# ひな形の印(ADR-0075 決定1)。{{diff}} だけが必須で、ちょうど1回
MARK_DIFF = "diff"
MARK_DIFF_FORMAT = "diff_format"
MARK_DIFF_TOTAL = "diff_total"
MARK_SENT_RANGE = "sent_range"
MARK_MAX_DIFFS = "max_diffs"
MARK_NOTES = "notes"
PROMPT_MARKS = (MARK_DIFF, MARK_DIFF_FORMAT, MARK_DIFF_TOTAL, MARK_SENT_RANGE, MARK_MAX_DIFFS, MARK_NOTES)
