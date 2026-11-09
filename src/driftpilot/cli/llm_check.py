"""`driftpilot llm-check`(REQ-35 / ADR-0034)。AI に決まった短い文を1回だけ送り、つながるかを確かめる。

設定とキーは比較の --llm と同じ読み方(load_llm_settings → llm_settings.prepare_connection_check)。
送る前に確認の内容を標準エラーに出し、[y/N] で聞く。--yes で確認を飛ばす。入力が端末でなく --yes も無ければ送らない。
結果の文だけを標準出力に出す(JSON は作らない)。ファイルにもログにも書かない(ADR-0034 決定4)。
終了コードは、つながった 0・送って失敗 1・送らなかった 2(このコマンドだけの決まり。REQ-35 §4)。
"""

import argparse
import sys
from typing import Any

from driftpilot.cli.commands import EXIT_ERROR, add_llm_profile_argument, load_llm_settings
from driftpilot.llm_analyzer import ConnectionCheckResult, send_connection_check
from driftpilot.llm_settings import prepare_connection_check, store_name
from driftpilot.messages import (
    LLM_CHECK_ASK,
    LLM_CHECK_DECLINED,
    LLM_CHECK_NOT_A_TERMINAL,
    LLM_CHECK_SENDING,
    llm_check_confirm_lines,
    llm_check_key_source,
    llm_check_result,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_SENT = EXIT_ERROR  # 2


def add_parser(sub: Any) -> None:
    chk = sub.add_parser(
        "llm-check",
        help="AI に決まった短い文を1回だけ送り、キーとモデルでつながるかを確かめる(ごく少額の料金がかかりうる)",
    )
    chk.add_argument("--yes", action="store_true", help="送る前の確認を飛ばす(入力が端末でないときは必須)")
    add_llm_profile_argument(chk)


def _confirmed(yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(LLM_CHECK_NOT_A_TERMINAL, file=sys.stderr)
        return False
    print(LLM_CHECK_ASK, end="", file=sys.stderr, flush=True)
    answer = sys.stdin.readline()  # 入力の終わり(EOF)は "" になり、送らない
    if answer.strip().lower() == "y":
        return True
    print(LLM_CHECK_DECLINED, file=sys.stderr)
    return False


def run(args: argparse.Namespace) -> int:
    # 読めない・無い名前なら CliError(「エラー: …」で 2)。接続テストの文は決まっているので、指示文のファイルは読まない
    settings = load_llm_settings(args.llm_profile, read_prompt=False)
    plan = prepare_connection_check(settings)
    if isinstance(plan, ConnectionCheckResult):
        print(llm_check_result(plan, config_path=settings.user_config), file=sys.stderr)
        return EXIT_NOT_SENT
    source = llm_check_key_source(plan.key_source, store_name(), plan.key_env_name)
    for line in llm_check_confirm_lines(
        plan.base_url, plan.model, plan.prompt, source, default_base_url=plan.default_base_url
    ):
        print(line, file=sys.stderr)
    if not _confirmed(args.yes):
        return EXIT_NOT_SENT
    print(LLM_CHECK_SENDING, file=sys.stderr, flush=True)
    result = send_connection_check(plan.client)
    print(llm_check_result(result, config_path=settings.user_config, cli=plan.cli))
    if not result.sent:  # 作った後で部品が無いと分かったとき(送っていない)
        return EXIT_NOT_SENT
    return EXIT_OK if result.ok else EXIT_FAILED
