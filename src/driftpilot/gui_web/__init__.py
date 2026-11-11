"""pywebview の画面(ADR-0074 / REQ-103〜REQ-107)。`driftpilot gui` と `python -m driftpilot.gui_web` の入口。

画面は static/ の HTML・CSS・JavaScript で描く(組み立ての道具を使わない。ADR-0074 決定2)。
Python と画面のやりとりは gui_web/bridge.py の形だけ(js_api と、window.driftpilot.receive への知らせ。決定3)。
窓を開くのは gui_web/app.py。処理は持たない(core・messages を呼ぶだけ。ADR-0022 決定2)。

wx は読まない(wxPython の画面は REQ-249 で消した。ADR-0185)。
このパッケージ自体は pywebview 無しで読める。pywebview を読むのは main() から呼ぶ app.run() の中だけ。
開けないときの文は、既定で標準エラーに出す(print_error)。配る形の画面版は report に関数を渡し、
OS のメッセージの窓で出す(packaging/gui_main.py。REQ-107 §4)。
"""

import argparse
import importlib.util
import sys
from collections.abc import Callable


def available() -> bool:
    """pywebview が入っているか。読み込まずに調べる。"""
    return importlib.util.find_spec("webview") is not None


def print_error(text: str) -> None:
    """開けないときの文を標準エラーに出す(既定。`driftpilot gui` の出力の形)。"""
    from driftpilot.messages import GW_ERROR_PREFIX

    print(f"{GW_ERROR_PREFIX}{text}", file=sys.stderr)


def main(
    argv: list[str] | None = None,
    report: Callable[[str], None] | None = None,
    *,
    update_started: str | None = None,
    update_failed: str | None = None,
) -> int:
    """pywebview の画面を開き、閉じたら 0 を返す。pywebview が無い・表示部品が使えなければ案内を出して 2。

    開くのは比較の画面(REQ-105。設定の窓と一括比較の窓はそこから開く)。argv に --sample があれば見本のページ
    (`python -m driftpilot.gui_web --sample`)、--settings があれば設定の画面だけ、--batch があれば一括比較の画面だけ
    (設計側の確かめのため。REQ-106)。どれか1つだけ。
    argv を省くと引数なし(`driftpilot gui` と配る形の画面版から呼ぶとき。driftpilot の CLI の引数は読まない)。
    report を渡すと、開けないときの文(messages.py の決まった文。例外は型名だけ)をその関数に渡す。
    省くと標準エラーに出す(print_error)。
    update_started・update_failed は、配る形の画面版が --update-started・--update-failed で起動したときのパス
    (REQ-253 §7。packaging/gui_main.py が渡す)。渡されたときだけ、起動の知らせを app.run に渡す(launch)。
    update_failed は名前が last-failed.log のときだけ知らせに出す。
    """
    from driftpilot.messages import GUIARG_BATCH, GUIARG_SAMPLE, GUIARG_SETTINGS, gui_web_unavailable

    parser = argparse.ArgumentParser(prog="python -m driftpilot.gui_web")
    page = parser.add_mutually_exclusive_group()
    page.add_argument("--sample", action="store_true", help=GUIARG_SAMPLE)
    page.add_argument("--settings", action="store_true", help=GUIARG_SETTINGS)
    page.add_argument("--batch", action="store_true", help=GUIARG_BATCH)
    args = parser.parse_args([] if argv is None else argv)
    if not available():
        (report or print_error)(gui_web_unavailable())
        return 2
    from driftpilot.gui_web.app import run

    extra: dict = {} if report is None else {"report": report}  # 渡されたときだけ渡す(既定の呼び方は今のまま)
    launch = _launch(update_started, update_failed)
    if launch is not None:
        extra["launch"] = launch
    if args.batch:
        return run(batch=True, **extra)
    return run(sample=args.sample, settings=args.settings, **extra)


def _launch(update_started: str | None, update_failed: str | None) -> dict | None:
    """更新の後の起動の知らせ(REQ-253 §7)。どちらも無い・記録のファイルの名前が違えば None。"""
    if update_started is None and update_failed is None:
        return None
    from driftpilot import __version__
    from driftpilot.messages import update_done_notice, update_failed_notice
    from driftpilot.update.apply import read_failed_log_arg

    if update_started is not None:
        return {"notice": update_done_notice(__version__), "started_marker": update_started}
    log = read_failed_log_arg(update_failed)
    if log is None:
        return None
    return {"notice": update_failed_notice(log), "started_marker": None}
