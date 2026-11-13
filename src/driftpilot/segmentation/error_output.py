"""打ち間違いのエラーの出力の区間を見分ける(ADR-0195 決定1)。

コマンドがある区間で、本文の空でない行(`^` と空白だけの行は数えない)が 3 行まで、かつそのどれかが入力の誤りの文で始まるとき、
その区間はエラーの出力。自動の組にも 1 つのログの中の組にも入れない(core.compare_all)。
入力の誤りの文は、前方一致の一覧(INPUT_ERROR_PHRASES)のほかに、形で見る一覧(INPUT_ERROR_PATTERNS。
富士通 Si-R の `<ERROR> xxx : Unknown command` など)でも見分ける。
"""

import re

from driftpilot.segmentation.splitter import Segment

# 入力の誤りの文(小文字。行頭の空白を除き、大文字・小文字を問わず前方一致で見る)。後から足せるようにここ 1 か所にまとめる。
# 「% BGP not active」「% No such interface」のような、この一覧に無い % の行は当てない。
INPUT_ERROR_PHRASES: tuple[str, ...] = (
    "% invalid input detected",
    "% incomplete command",
    "% ambiguous command",
    "% invalid command",
    "% unrecognized command",
    "% unknown command",
    "unknown action",
    "command parse error",
    "syntax error",
    "unknown command",
)

# 入力の誤りの形(正規表現。行頭の空白を除き、小文字にした行に当てる。大文字・小文字は問わない)。後から形を足せるようにここ 1 か所にまとめる。
# 富士通 Si-R: `<ERROR> adfjhashdf : Unknown command`(間に打ったコマンドが入る。`:` の前後の空白の数は問わない)。
# `<ERROR>` で始まっても `Unknown command` で終わらない行(`<ERROR> invalid parameter` など)は、意味のある表示かもしれないので当てない。
INPUT_ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"^<error>\s+.*:\s*unknown command\s*$", re.IGNORECASE),)

# 本文の空でない行の数の上限
MAX_ERROR_LINES = 3


def _meaningful(line: str) -> bool:
    """空の行と、`^` と空白だけの行は数えない。"""
    return bool(line.strip().strip("^").strip())


def is_error_output(segment: Segment) -> bool:
    """区間が打ち間違いなどのエラーの出力か。"""
    if segment.command is None:
        return False
    lines = [line.strip().lower() for line in segment.body if _meaningful(line)]
    if not 1 <= len(lines) <= MAX_ERROR_LINES:
        return False
    return any(line.startswith(INPUT_ERROR_PHRASES) or any(p.match(line) for p in INPUT_ERROR_PATTERNS) for line in lines)
