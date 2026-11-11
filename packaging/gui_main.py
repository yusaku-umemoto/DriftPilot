"""exe・アプリの画面版の入口（ADR-0036 / ADR-0074 決定7 / REQ-107）。`driftpilot gui` と同じ pywebview の比較の画面を開く。

画面版はコンソールの窓を持たない（DriftPilot.spec の console=False）ので、標準エラーの文は誰にも見えない
（sys.stderr が None のこともある）。そこで、画面を開けずに 0 以外で終わるときは、理由の文を OS のメッセージの窓で出す
（REQ-107 §4）。文は messages.py の決まった文と例外の型名だけ（gui_web がそう作る）。

更新（ADR-0188 決定3 / REQ-253 §6・§7）:
- 引数がちょうど「--apply-update <plan.json>」のときだけ、driftpilot.update.apply の main を呼び、画面（pywebview）を読み込まずに終わる
  （準備のフォルダーから起動した新しい版が、今の版の中身を入れ替える側として動く）
- 「--update-started <started-ok>」「--update-failed <last-failed.log>」のときは、画面を開いて起動の知らせを出す
- ほかの引数のときは今と同じ（引数は読まない）
- --update-started のとき以外は、画面を開く前に古い準備のフォルダーと控えを消す（driftpilot.update.prepare.cleanup_stale。
  消せなくても止めない）。--update-started のときは入れ替えの最中の控えを消さないように飛ばす（次の起動で消える）
- driftpilot.update は関数の中で読む（先頭で読むのは gui_web だけ。PyInstaller は関数の中の import もたどる）
"""

import subprocess
import sys
from collections.abc import Callable

from driftpilot import gui_web

TITLE = "DriftPilot"
FLAG_APPLY = "--apply-update"
FLAG_STARTED = "--update-started"
FLAG_FAILED = "--update-failed"
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


def _cleanup_stale() -> None:
    """古い準備のフォルダーと控えを消す（配った物で動いているときだけ。例外は捨てる）。"""
    try:
        from driftpilot.update import prepare

        prepare.cleanup_stale()
    except Exception:  # noqa: BLE001 - 掃除できなくても画面は開く（次の起動でまた消す）
        pass


def main(show: Callable[[str], None] = show_error, argv: list[str] | None = None) -> int:
    """pywebview の比較の画面を開き、終了コードを返す。0 以外で終わり、理由の文があれば show で1回だけ出す。

    argv は起動の引数（省くと引数なし。__main__ からは sys.argv[1:]）。「--apply-update <plan.json>」のときは
    画面を開かずに入れ替える側として動く（REQ-253 §6）。
    """
    args = [] if argv is None else list(argv)
    if len(args) == 2 and args[0] == FLAG_APPLY:
        from driftpilot.update import apply

        return apply.main(args[1])
    launch: dict[str, str] = {}
    if len(args) == 2 and args[0] == FLAG_STARTED:
        launch["update_started"] = args[1]
    elif len(args) == 2 and args[0] == FLAG_FAILED:
        launch["update_failed"] = args[1]
    if "update_started" not in launch:
        _cleanup_stale()
    lines: list[str] = []

    def report(text: str) -> None:
        lines.append(text)
        if sys.stderr is not None:  # コンソールから起動したときは、今までどおり標準エラーにも出す
            try:
                gui_web.print_error(text)
            except (OSError, ValueError):
                pass

    code = gui_web.main(report=report, **launch)
    if code != 0 and lines:
        show("\n".join(lines))
    return code


if __name__ == "__main__":
    sys.exit(main(argv=sys.argv[1:]))
