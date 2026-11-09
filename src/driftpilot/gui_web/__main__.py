"""`python -m driftpilot.gui_web` の入口。引数なしで比較の画面、`--settings` で設定の画面、`--batch` で一括比較の画面、
`--sample` で見本のページ(REQ-105 §2.1・REQ-106 §2.1)。"""

import sys

from driftpilot.gui_web import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
