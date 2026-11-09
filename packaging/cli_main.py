"""exe のコマンド版（driftpilot-cli）の入口（ADR-0036）。`driftpilot` と同じ。"""

import sys

from driftpilot.cli import main

sys.exit(main())
