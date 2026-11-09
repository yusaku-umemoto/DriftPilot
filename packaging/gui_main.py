"""exe・アプリの画面版の入口（ADR-0036 / ADR-0074 決定7 / REQ-107）。`driftpilot gui` と同じ pywebview の比較の画面を開く。

画面版はコンソールの窓を持たない（DriftPilot.spec の console=False）ので、標準エラーの文は誰にも見えない
（sys.stderr が None のこともある）。そこで、画面を開けずに 0 以外で終わるときは、理由の文を OS のメッセージの窓で出す
（REQ-107 §4）。文は messages.py の決まった文と例外の型名だけ（gui_web がそう作る）。
"""

import subprocess
import sys
from collections.abc import Callable

from driftpilot import gui_web

TITLE = "DriftPilot"
MB_OK = 0x0
MB_ICONERROR = 0x10


def show_error(text: str, platform: str | None = None) -> None:
    """理由の文を OS のメッセージの窓で出す。出せなくても例外にしない（止まる理由の文を出す最後の手だてなので）。"""
    platform = sys.platform if platform is None else platform
    try:
        if platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, text, TITLE, MB_OK | MB_ICONERROR)
        elif platform == "darwin":
            # 文は AppleScript の本文に埋め込まず、引数（argv）で渡す。shell は使わない
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    "on run argv",
                    "-e",
                    "display alert (item 1 of argv) message (item 2 of argv) as critical",
                    "-e",
                    "end run",
                    TITLE,
                    text,
                ],
                check=False,
                capture_output=True,
            )
    except (OSError, AttributeError, subprocess.SubprocessError):
        pass


def main(show: Callable[[str], None] = show_error) -> int:
    """pywebview の比較の画面を開き、終了コードを返す。0 以外で終わり、理由の文があれば show で1回だけ出す。"""
    lines: list[str] = []

    def report(text: str) -> None:
        lines.append(text)
        if sys.stderr is not None:  # コンソールから起動したときは、今までどおり標準エラーにも出す
            try:
                gui_web.print_error(text)
            except (OSError, ValueError):
                pass

    code = gui_web.main(report=report)
    if code != 0 and lines:
        show("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main())
