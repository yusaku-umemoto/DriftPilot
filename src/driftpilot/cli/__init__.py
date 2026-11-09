"""コマンドラインの入口(REQ-06 / REQ-13)。`driftpilot compare <変更前> <変更後>` など。本体は cli/commands.py。

ここは、比較の部品(core・diff_engine。読み込むときに ntc_templates や hier_config を読む)を読まない。
自己診断の `driftpilot selfcheck`(REQ-38 / ADR-0036 決定6)は、本体を読まずに cli/selfcheck.py へ渡す。
部品やその版の情報が欠けていて本体を読み込めない exe でも、自己診断の結果と終了コード 1 を返すため。
selfcheck 以外は本体の main に渡す。`from driftpilot.cli import EXIT_ERROR` などは、本体の名前をそのまま返す。
"""

import importlib
import sys
from typing import Any

SELFCHECK = "selfcheck"


def main(argv: list[str] | None = None) -> int:
    """コマンドを実行して終了コードを返す(selfcheck は cli/selfcheck.py、それ以外は cli/commands.py の main)。"""
    args = sys.argv[1:] if argv is None else list(argv)
    if args[:1] == [SELFCHECK]:  # 全体の引数は -h と --version だけなので、selfcheck は必ず先頭に来る
        from driftpilot.cli.selfcheck import main as selfcheck_main

        return selfcheck_main(args[1:])
    from driftpilot.cli.commands import main as commands_main

    return commands_main(args)


def __getattr__(name: str) -> Any:
    """本体(cli/commands.py)の名前を、`driftpilot.cli` の名前としても使えるようにする(初めて使うときに本体を読む)。"""
    if name.startswith("__"):  # 道具が調べる特別な名前では本体を読まない
        raise AttributeError(name)
    # `from driftpilot.cli import commands` と書くと、この関数がまた呼ばれる
    commands = importlib.import_module("driftpilot.cli.commands")
    return getattr(commands, name)
