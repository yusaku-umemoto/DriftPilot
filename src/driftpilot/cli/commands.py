"""コマンドラインの本体(REQ-06 / REQ-13)。`driftpilot compare <変更前> <変更後>`。一括比較の `batch` は cli/batch.py。
AI 接続テストの `llm-check` は cli/llm_check.py(REQ-35)。自己診断の `selfcheck` は cli/selfcheck.py(REQ-38)。
入口の `driftpilot.cli.main` は、selfcheck 以外のときにだけこのモジュールを読む(比較の部品をここで読むため。cli/__init__.py)。

引数の解釈、ファイルの読み込み、整形(cli/render.py)、終了コードの写し込みだけを持つ。文章化は GUI と共通の driftpilot/messages.py にある。
比較の処理は driftpilot.core にある(ADR-0015)。保存(--save)は driftpilot.storage を呼ぶだけ(ADR-0017 決定7)。
LLM 解析(--llm / --llm-dry-run)は、ここで設定とキーを読んで client を組み立て、core に渡すだけ(REQ-16)。
比較画面(`driftpilot gui`)は pywebview の画面(driftpilot.gui_web)を呼ぶだけ。pywebview はそのときにだけ読む
(`gui` extra。ADR-0074 決定5 / REQ-107)。wxPython の画面は REQ-249 で消した(ADR-0185)。
"""

import argparse
import dataclasses
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import driftpilot
from driftpilot import coverage_show
from driftpilot.messages import (
    CLI_ERR_CONFIRM_ALONE,
    CLI_ERR_LLM_MAX_DIFFS_ALONE,
    CLI_ERR_LLM_MAX_DIFFS_RANGE,
    CLI_ERR_LLM_MAX_TOTAL_DIFFS_ALONE,
    CLI_ERR_LLM_MAX_TOTAL_DIFFS_RANGE,
    CLI_ERR_NO_FILE_MEASURED,
    CLI_ERR_NO_FILES,
    CLI_ERR_NO_SEGMENTS,
    CLI_ERR_SAVE_NEEDS_PATH,
    CLI_ERR_SEGMENT_NOT_SELECTED,
    CLI_ERR_SEGMENTS_AMBIGUOUS,
    CLI_ERR_TOP_FILES_NEGATIVE,
    CLI_ERR_TOP_NEGATIVE,
    CLI_ERROR_PREFIX,
    CLI_HELP,
    COMPARE_MULTI_NOTHING_TO_SAVE,
    LLM_NO_PREMISE_ALONE,
    LLM_NO_PREMISE_HELP,
    premise_refused_cli,
    batch_llm_gave_up,
    cli_api_error_line,
    cli_err_all_segments_conflict,
    cli_err_cannot_write,
    cli_err_platform_rejected,
    common_env_ignored_note,
    compare_llm_confirm_hint,
    gui_web_unavailable,
    llm_rate_limited,
    multi_unpaired_not_saved,
    raw_option_problem_lines,
    prompt_profile_name_shadowed,
    saved_notice,
    unreadable,
    unreadable_utf8,
    user_prompt_template_ignored,
    version_line,
    workdir_api_key_ignored,
    workdir_limits_ignored,
    workdir_model_ignored,
)
from driftpilot.cli.output import open_machine_file, write_machine
from driftpilot.cli.render import pair_label, to_json, to_json_all, to_text, to_text_all
from driftpilot.config_parser import flat_docs
from driftpilot.config_parser.pattern_docs import render_json, render_markdown
from driftpilot.config_parser.platforms import CONFIG_PLATFORMS
from driftpilot.batch_io import ResultsWriteError, multi_filename, pair_record, write_records
from driftpilot.core import (
    DEFAULT_MAX_TOTAL_DIFFS,
    CompareError,
    CompareOutcome,
    CompareRequest,
    MultiCompareOutcome,
    RawOptions,
    Replacement,
    SideInput,
    SideSelection,
    approved_payloads,
    check_raw_options,
    compare_all,
    final_results,
    gave_up_count,
    is_failed_with_payload,
    run_compare,
    withhold,
)
from driftpilot.coverage_report import InputError, measure, resolve_inputs
from driftpilot.coverage_report import to_json as coverage_json
from driftpilot.coverage_report import to_text as coverage_text
from driftpilot.coverage_report.render import DEFAULT_FILES, DEFAULT_TOP
from driftpilot.coverage_show import command_docs
from driftpilot.diff_engine import NormalizationRule, load_rules
from driftpilot.llm_analyzer import DEFAULT_MAX_DIFFS, ApiErrorText, LlmAnalysis, LlmRequest, mask_coverage, mask_docs
from driftpilot.llm_analyzer.mask_coverage import DEFAULT_TOP as MASK_TOP
from driftpilot.llm_settings import (
    WORKDIR_CONFIG,
    LlmSettings,
    SettingsError,
    load_settings,
    premise_problem,
    with_cli_limits,
)
from driftpilot.llm_settings import build_request as build_llm_request
from driftpilot.segmentation.input_forms import InputDecodeError, InputText, read_config_input
from driftpilot.storage import StorageError, build_record, save

_H = CLI_HELP  # 説明の文(言語で選んだ辞書)

EXIT_SAME = 0
EXIT_DIFF = 1
EXIT_ERROR = 2
EXIT_NEEDS_SELECTION = 3  # 入力の指定が足りない。区間や機種・コマンドを指定すれば進める(比較はしていない)

# 結果の status から終了コードへの写し。判定は core が済ませている
EXIT_BY_STATUS = {
    "no_difference": EXIT_SAME,
    "difference": EXIT_DIFF,
    "error": EXIT_ERROR,
    "needs_selection": EXIT_NEEDS_SELECTION,
}


class CliError(Exception):
    """利用者に見せるエラー。標準エラーに書いて終了コード 2 にする。"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="driftpilot", description=_H["main.description"])
    # 版は selfcheck と同じ出どころ(配る形の driftpilot-cli でも同じ。REQ-97 §5)
    parser.add_argument("--version", action="version", version=version_line(driftpilot.__version__), help=_H["main.version"])
    sub = parser.add_subparsers(dest="command", required=True)
    cmp = sub.add_parser("compare", help=_H["compare.help"])
    cmp.add_argument("before", help=_H["compare.before"])
    cmp.add_argument("after", help=_H["compare.after"])
    cmp.add_argument("--mode", choices=["structured", "raw", "both"], default="both", help=_H["compare.mode"])
    cmp.add_argument("--format", choices=["text", "json"], default="text", help=_H["format_text"])
    cmp.add_argument("--kind", choices=["config", "show", "auto"], default="config", help=_H["compare.kind"])
    cmp.add_argument("--platform", metavar="NAME", help=_H["compare.platform"])
    cmp.add_argument("--show-command", metavar="TEXT", dest="show_command", help=_H["compare.show_command"])
    for side in ("before", "after"):
        cmp.add_argument(f"--{side}-command", metavar="TEXT", help=_H[f"compare.{side}_command"])
        cmp.add_argument(f"--{side}-segment", type=int, metavar="N", help=_H[f"compare.{side}_segment"])
    cmp.add_argument("--all-segments", action="store_true", help=_H["compare.all_segments"])
    cmp.add_argument("--confirm", action="store_true", help=_H["compare.confirm"])
    cmp.add_argument(
        "--llm-max-total-diffs",
        type=int,
        metavar="N",
        help=_H["compare.llm_max_total_diffs"].format(default=DEFAULT_MAX_TOTAL_DIFFS),
    )
    cmp.add_argument("--prompt", metavar="TEXT", help=_H["compare.prompt"])
    cmp.add_argument("--rules", metavar="PATH", help=_H["compare.rules"])
    cmp.add_argument("--enable-rule", action="append", metavar="ID", help=_H["compare.enable_rule"])
    cmp.add_argument("--save", metavar="PATH", help=_H["compare.save"])
    llm = cmp.add_mutually_exclusive_group()
    llm.add_argument("--llm", action="store_true", help=_H["compare.llm"])
    llm.add_argument("--llm-dry-run", action="store_true", help=_H["compare.llm_dry_run"])
    cmp.add_argument(
        "--llm-max-diffs",
        type=int,
        metavar="N",
        help=_H["compare.llm_max_diffs"].format(default=DEFAULT_MAX_DIFFS),
    )
    # 単純比較の設定(ADR-0166。REQ-227 §8)。既定の設定のファイル(raw-compare.json)は読まない
    cmp.add_argument("--ignore-line", action="append", metavar="REGEX", help=_H["compare.ignore_line"])
    cmp.add_argument("--replace", action="append", nargs=2, metavar=("REGEX", "TEXT"), help=_H["compare.replace"])
    cmp.add_argument(
        "--ignore-space",
        choices=["off", "trailing", "amount", "all"],
        default="off",
        help=_H["compare.ignore_space"],
    )
    cmp.add_argument("--ignore-blank-lines", action="store_true", help=_H["compare.ignore_blank_lines"])
    cmp.add_argument("--ignore-case", action="store_true", help=_H["compare.ignore_case"])
    # REQ-228 §6。行の移動の検出と比べ方(どちらも単純比較だけに効く)
    cmp.add_argument("--detect-moves", action="store_true", help=_H["compare.detect_moves"])
    cmp.add_argument(
        "--diff-algorithm",
        choices=["standard", "minimal", "patience"],
        default="standard",
        help=_H["compare.diff_algorithm"],
    )
    add_llm_profile_argument(cmp)
    add_llm_prompt_profile_argument(cmp)
    add_llm_no_premise_argument(cmp)
    pat = sub.add_parser("patterns", help=_H["patterns.help"])
    pat.add_argument("--format", choices=["markdown", "json"], default="markdown", help=_H["format_markdown"])
    pat.add_argument("--output", metavar="PATH", help=_H["output"])
    pat.add_argument("--platform", choices=CONFIG_PLATFORMS, help=_H["patterns.platform"])
    cov = sub.add_parser("coverage", help=_H["coverage.help"])
    cov.add_argument("paths", nargs="+", metavar="PATH", help=_H["coverage.paths"])
    cov.add_argument("--exposure", choices=["normal", "exposed"], default="normal", help=_H["coverage.exposure"])
    cov.add_argument("--format", choices=["text", "json"], default="text", help=_H["format_text"])
    cov.add_argument("--platform", choices=CONFIG_PLATFORMS, help=_H["coverage.platform"])
    cov.add_argument(
        "--top",
        type=int,
        default=DEFAULT_TOP,
        metavar="N",
        help=_H["coverage.top"].format(default=DEFAULT_TOP),
    )
    cov.add_argument(
        "--files",
        type=int,
        default=DEFAULT_FILES,
        metavar="N",
        help=_H["coverage.files"].format(default=DEFAULT_FILES),
    )
    # show 側は指標が別物のため、coverage に相乗りさせず別コマンドにする(REQ-17)。名前は一覧で coverage の隣に並ぶように
    covs = sub.add_parser("coverage-show", help=_H["coverage_show.help"])
    covs.add_argument("paths", nargs="+", metavar="PATH", help=_H["coverage_show.paths"])
    covs.add_argument("--platform", metavar="NAME", help=_H["coverage_show.platform"])
    covs.add_argument("--format", choices=["text", "json"], default="text", help=_H["format_text"])
    scmd = sub.add_parser("show-commands", help=_H["show_commands.help"])
    scmd.add_argument("--format", choices=["markdown", "json"], default="markdown", help=_H["format_markdown"])
    scmd.add_argument("--output", metavar="PATH", help=_H["output"])
    mpat = sub.add_parser("mask-patterns", help=_H["mask_patterns.help"])
    mpat.add_argument("--format", choices=["markdown", "json"], default="markdown", help=_H["format_markdown"])
    mpat.add_argument("--output", metavar="PATH", help=_H["output"])
    mcov = sub.add_parser("mask-coverage", help=_H["mask_coverage.help"])
    mcov.add_argument("paths", nargs="+", metavar="PATH", help=_H["mask_coverage.paths"])
    mcov.add_argument("--format", choices=["text", "json"], default="text", help=_H["format_text"])
    mcov.add_argument(
        "--top", type=int, default=MASK_TOP, metavar="N", help=_H["mask_coverage.top"].format(default=MASK_TOP)
    )
    sub.add_parser("gui", help=_H["gui.help"])
    from driftpilot.cli import batch, llm_check, selfcheck  # batch と llm_check はこのモジュールを読むため、ここで読む

    batch.add_parser(sub)
    llm_check.add_parser(sub)
    selfcheck.add_parser(sub)
    return parser


def _read(path: str) -> InputText:
    """比較に使うファイルを読む(ADR-0138 決定1。文字コードは REQ-185)。読めないときは文の後ろに案内を足す(REQ-189)。"""
    try:
        return read_config_input(path)
    except InputDecodeError as exc:
        raise CliError(unreadable_utf8(path, exc.hints)) from exc
    except OSError as exc:
        raise CliError(unreadable(path, exc)) from exc


def _load_rules(path: str) -> list[NormalizationRule]:
    try:
        return load_rules(path)
    except UnicodeDecodeError as exc:
        raise CliError(unreadable_utf8(path)) from exc
    except OSError as exc:
        raise CliError(unreadable(path, exc)) from exc


def _run_patterns(args: argparse.Namespace) -> int:
    if args.platform:
        text = flat_docs.render_json(args.platform) if args.format == "json" else flat_docs.render_markdown(args.platform)
    else:
        text = render_json() if args.format == "json" else render_markdown()
    return _write_output(args, text)


def _run_mask_patterns(args: argparse.Namespace) -> int:
    text = mask_docs.render_json() if args.format == "json" else mask_docs.render_markdown()
    return _write_output(args, text)


def _run_mask_coverage(args: argparse.Namespace) -> int:
    if args.top < 0:
        raise CliError(CLI_ERR_TOP_NEGATIVE)
    try:
        paths = mask_coverage.resolve_paths(args.paths)
    except FileNotFoundError as exc:
        raise CliError(str(exc)) from exc
    if not paths:
        raise CliError(CLI_ERR_NO_FILES)
    report = mask_coverage.measure(paths)
    if args.format == "json":
        write_machine(mask_coverage.to_json(report, args.top))
    else:
        print(mask_coverage.to_text(report, args.top), end="")
    if report.files == 0:
        raise CliError(CLI_ERR_NO_FILE_MEASURED)
    return EXIT_SAME


def _write_output(args: argparse.Namespace, text: str) -> int:
    if args.output:
        try:
            with open_machine_file(args.output, "w") as fh:
                fh.write(text)
        except OSError as exc:
            raise CliError(cli_err_cannot_write(args.output, str(exc.strerror or exc))) from exc
    else:
        write_machine(text)  # 標準出力でも --output と同じバイト列にする(ADR-0023 決定1)
    return EXIT_SAME


def _run_coverage(args: argparse.Namespace) -> int:
    if args.top < 0 or args.files < 0:
        raise CliError(CLI_ERR_TOP_FILES_NEGATIVE)
    try:
        inputs = resolve_inputs(args.paths, exposure=args.exposure)
    except InputError as exc:
        raise CliError(str(exc)) from exc
    if not inputs:
        raise CliError(CLI_ERR_NO_FILES)
    report = measure(inputs, platform=args.platform)
    if args.format == "json":
        write_machine(coverage_json(report, args.top))
    else:
        print(coverage_text(report, args.top, args.files), end="")
    if all(f.error is not None for f in report.files):
        raise CliError(CLI_ERR_NO_FILE_MEASURED)
    return EXIT_SAME


def _run_show_commands(args: argparse.Namespace) -> int:
    text = command_docs.render_json() if args.format == "json" else command_docs.render_markdown()
    return _write_output(args, text)


def _run_coverage_show(args: argparse.Namespace) -> int:
    platform = args.platform.strip() if args.platform else None
    if platform and platform.lower() in coverage_show.REJECTED_PLATFORMS:
        raise CliError(cli_err_platform_rejected(platform, coverage_show.REJECTED_PLATFORMS[platform.lower()]))
    try:
        inputs = coverage_show.resolve_inputs(args.paths)
    except InputError as exc:
        raise CliError(str(exc)) from exc
    if not inputs:
        raise CliError(CLI_ERR_NO_FILES)
    report = coverage_show.measure(inputs, platform or None)
    if args.format == "json":
        write_machine(coverage_show.to_json(report))
    else:
        print(coverage_show.to_text(report), end="")
    # 1区間も特定できなくても 0(0% を返す)。測れたファイルが無いときだけエラー
    if report.files == 0:
        raise CliError(CLI_ERR_NO_FILE_MEASURED)
    return EXIT_SAME


def _request(args: argparse.Namespace) -> CompareRequest:
    """引数とファイルから core への要求を組み立てる。ファイルの読み込みはここまで。"""
    rules = _load_rules(args.rules) if args.rules else None
    return CompareRequest(
        before=_side(args.before),
        after=_side(args.after),
        mode=args.mode,
        kind=args.kind,
        platform=args.platform,
        show_command=args.show_command,
        prompt=args.prompt,
        before_selection=SideSelection(args.before_command, args.before_segment),
        after_selection=SideSelection(args.after_command, args.after_segment),
        rules=rules,
        enabled_rule_ids=args.enable_rule,
        raw_options=_raw_options(args),
    )


def _raw_options(args: argparse.Namespace) -> RawOptions:
    """--ignore-line・--replace・--ignore-space・--ignore-blank-lines・--ignore-case・--detect-moves・--diff-algorithm から
    単純比較の設定を作る。
    """
    return RawOptions(
        ignore_lines=tuple(args.ignore_line or ()),
        replacements=tuple(Replacement(pattern, text) for pattern, text in (args.replace or ())),
        whitespace=args.ignore_space,
        ignore_blank_lines=args.ignore_blank_lines,
        ignore_case=args.ignore_case,
        detect_moves=args.detect_moves,
        algorithm=args.diff_algorithm,
    )


def _check_raw_options(args: argparse.Namespace) -> bool:
    """単純比較の設定の誤りを、1 件 1 行で標準エラーに出す。誤りがあれば偽(比べずに、引数の誤りと同じ 2 で終える)。

    ファイルや AI の設定を読む前に確かめる。JSON の結果は出さない(引数の誤りと同じ扱い)。
    """
    problems = check_raw_options(_raw_options(args))
    for line in raw_option_problem_lines(problems):
        print(f"{CLI_ERROR_PREFIX}{line}", file=sys.stderr)
    return not problems


def _side(path: str) -> SideInput:
    read = _read(path)
    return SideInput(read.text, path, read.notes)


def check_llm_args(args: argparse.Namespace) -> None:
    """--llm-max-diffs と --llm-no-premise の検査(compare と batch で共通)。"""
    if args.llm_max_diffs is not None:
        if not (args.llm or args.llm_dry_run):
            raise CliError(CLI_ERR_LLM_MAX_DIFFS_ALONE)
        if args.llm_max_diffs < 1:
            raise CliError(CLI_ERR_LLM_MAX_DIFFS_RANGE)
    if getattr(args, "llm_no_premise", False) and not (args.llm or args.llm_dry_run):
        raise CliError(LLM_NO_PREMISE_ALONE)


def add_llm_no_premise_argument(parser: argparse.ArgumentParser) -> None:
    """--llm-no-premise(compare・batch。ADR-0172 決定3)。--llm か --llm-dry-run と一緒のときだけ使える。"""
    parser.add_argument("--llm-no-premise", action="store_true", help=LLM_NO_PREMISE_HELP)


def apply_premise(settings: LlmSettings, args: argparse.Namespace) -> LlmSettings:
    """毎回付ける前提の扱い(ADR-0172 決定3・4。compare・--all-segments・batch で共通。load_llm_settings の後、比較の前に呼ぶ)。

    --llm-no-premise があれば前提を外した設定を返す。無くて前提が送れない(premise_problem が断る)ときは、比較も送信もせずに
    CliError(「エラー: 」＋断りの文＋--llm-no-premise の案内で 2)。--llm-dry-run でも同じ。core の確かめは残る。
    """
    if getattr(args, "llm_no_premise", False):
        return dataclasses.replace(settings, premise=None)
    if premise_problem(settings) is not None:
        raise CliError(premise_refused_cli())
    return settings


def add_llm_profile_argument(parser: argparse.ArgumentParser) -> None:
    """--llm-profile(compare・batch・llm-check で共通。REQ-42 §7)。マニフェストには書けない(ADR-0031 決定6)。"""
    parser.add_argument(
        "--llm-profile",
        metavar="NAME",
        help=_H["llm_profile"],
    )


def add_llm_prompt_profile_argument(parser: argparse.ArgumentParser) -> None:
    """--llm-prompt-profile(compare・batch。REQ-116 §4)。--llm か --llm-dry-run のときに効く。"""
    parser.add_argument(
        "--llm-prompt-profile",
        metavar="NAME",
        help=_H["llm_prompt_profile"],
    )


def load_llm_settings(
    profile: str | None = None, prompt_profile: str | None = None, *, read_prompt: bool = True
) -> LlmSettings:
    """設定とキーを読む(compare・batch・llm-check で共通)。作業フォルダの api_key は読まず、その旨を標準エラーに出す。

    profile は --llm-profile、prompt_profile は --llm-prompt-profile の名前。読めない・無い名前は CliError
    (「エラー: …」で 2。送らない。無い指示文の名前で別の文に切り替えない。ADR-0079 決定2)。
    read_prompt=False(llm-check)は指示文のファイルを読まない(接続テストの文は変えないため)。
    """
    try:
        settings = load_settings(profile=profile, prompt_profile=prompt_profile, read_prompt=read_prompt)
    except SettingsError as exc:
        raise CliError(str(exc)) from exc
    if settings.ignored_workdir_api_key:
        print(workdir_api_key_ignored(Path(WORKDIR_CONFIG).absolute()), file=sys.stderr)
    if settings.ignored_workdir_model:
        print(workdir_model_ignored(settings.profile_name), file=sys.stderr)
    if settings.common_env_ignored:
        # プロファイルがあるときは共通の変数を読まない(ADR-0077 決定3)。キーの値は出さない
        print(common_env_ignored_note(), file=sys.stderr)
    if settings.ignored_workdir_limits:
        print(workdir_limits_ignored(Path(WORKDIR_CONFIG).absolute(), settings.ignored_workdir_limits), file=sys.stderr)
    if read_prompt and settings.ignored_user_prompt_template and settings.user_config is not None:
        # config.toml の [llm].prompt_template は読まない(ADR-0079 決定5)
        print(user_prompt_template_ignored(settings.user_config), file=sys.stderr)
    if settings.prompt_name_shadowed and prompt_profile is not None:
        # 名前が最初から入っている指示文と利用者の指示文の両方に当たった。黙って別の文で送らない(ADR-0079 決定2・REQ-204)
        print(prompt_profile_name_shadowed(prompt_profile), file=sys.stderr)
    return settings


def _llm_request(args: argparse.Namespace) -> LlmRequest | None:
    """--llm / --llm-dry-run の指定から LlmRequest を組み立てる。設定とキーを読むのはここだけ(core は読まない)。

    1組の上限は --llm-max-diffs ＞ 利用者ごとの設定ファイル ＞ 既定(ADR-0071 決定5)。
    """
    check_llm_args(args)
    if not (args.llm or args.llm_dry_run):
        return None
    settings = with_cli_limits(
        load_llm_settings(args.llm_profile, args.llm_prompt_profile), max_diffs=args.llm_max_diffs
    )
    settings = apply_premise(settings, args)  # 前提を外す・送れない前提なら比較の前に止める(ADR-0172)
    return build_llm_request(settings, dry_run=args.llm_dry_run)


def check_all_segments_llm_args(args: argparse.Namespace) -> None:
    """--confirm と --llm-max-total-diffs の検査(REQ-122 §5)。どちらも --all-segments の LLM 解析のときだけ使える。"""
    if args.confirm and not (args.all_segments and args.llm):
        raise CliError(CLI_ERR_CONFIRM_ALONE)
    if args.llm_max_total_diffs is not None:
        if not (args.all_segments and (args.llm or args.llm_dry_run)):
            raise CliError(CLI_ERR_LLM_MAX_TOTAL_DIFFS_ALONE)
        if args.llm_max_total_diffs < 1:
            raise CliError(CLI_ERR_LLM_MAX_TOTAL_DIFFS_RANGE)


def _compare_all_with_llm(request: CompareRequest, plan: Any) -> tuple[MultiCompareOutcome, Any]:
    """--all-segments の LLM 解析(REQ-122 §5)。一括比較の batch --llm と同じ2段と確認の決まり。返すのは (結果, 1段目の要約)。

    plan は cli.batch の _llm_plan(--llm だけ・--llm-dry-run は確認だけ、--llm --confirm だけ送る)。要約は標準エラーに出す。
    送れない設定(client を作れない)のときは1回だけ比べ、送れない理由を各組の AI の欄に残す。
    """
    from driftpilot.cli import batch  # cli.batch はこのモジュールを読むため、ここで読む

    first = compare_all(request, llm=plan.first)
    if not plan.preview:
        return first, None
    preview = batch._print_preview(plan, first.pairs)
    if plan.send is None:
        # 確認だけ。比較が失敗した組は送る対象にしない(一括比較と同じ)
        pairs = [withhold(p, "compare_failed") if is_failed_with_payload(p) else p for p in first.pairs]
        return dataclasses.replace(first, pairs=pairs), preview
    # --confirm が承認(要約に出した組をすべて送る)。上限を超えていれば空になり、1件も送らない
    approved = approved_payloads(preview, {job.job_id for job in preview.jobs})
    second = compare_all(request, llm=plan.send, approved=approved).pairs if approved else []
    # 2段目は全部の組を比べ直すが、結果として使うのは承認した組だけ(一括比較が2段目で承認した job だけを実行するのと同じ)。
    # ほかの組は1段目の結果に理由を入れる(比較が失敗した組は compare_failed、承認しなかった組は not_approved か over_total_limit)
    second = [pair for pair in second if pair.job.id in approved]
    reason = "over_total_limit" if preview.over_limit else "not_approved"
    pairs = final_results(first.pairs, second, approved=approved, reason=reason)
    return dataclasses.replace(first, pairs=pairs), preview


def _save_all(args: argparse.Namespace, outcome: MultiCompareOutcome) -> bool:
    """--all-segments --save(REQ-122 §3・§5)。組ごとに1行の JSON Lines。相手なしは入れず、あれば標準エラーで知らせる。上書きしない。

    書けなかったら偽(終了コードを 2 にする)。組が無ければ書かない。
    """
    if not outcome.pairs:
        print(f"{CLI_ERROR_PREFIX}{COMPARE_MULTI_NOTHING_TO_SAVE}", file=sys.stderr)
        return False
    paths = {"before": str(Path(args.before).absolute()), "after": str(Path(args.after).absolute())}
    saved_at = datetime.now(timezone.utc).astimezone()
    records = [pair_record(pair, source_paths=paths, saved_at=saved_at) for pair in outcome.pairs]
    target = Path(args.save)
    if target.is_dir():
        target = target / multi_filename(records[0])
    try:
        written = write_records(records, target)
    except ResultsWriteError as exc:
        print(f"{CLI_ERROR_PREFIX}{exc}", file=sys.stderr)
        return False
    print(saved_notice(written), file=sys.stderr)
    if outcome.unpaired:
        print(multi_unpaired_not_saved(len(outcome.unpaired)), file=sys.stderr)
    return True


def _run_compare_all(args: argparse.Namespace) -> int:
    """compare --all-segments(REQ-121・REQ-122)。組ごとの比較は core.compare_all。終了コードは全体の status の今の決まりどおり。

    --save の失敗は、比較結果の終了コードより優先して 2 にする(1組の --save と同じ)。
    """
    conflicts = (
        ("--before-segment", args.before_segment is not None),
        ("--after-segment", args.after_segment is not None),
        ("--before-command", args.before_command is not None),
        ("--after-command", args.after_command is not None),
        ("--show-command", args.show_command is not None),
    )
    if used := [name for name, given in conflicts if given]:
        raise CliError(cli_err_all_segments_conflict(used))
    if not _check_raw_options(args):
        return EXIT_ERROR
    check_llm_args(args)
    check_all_segments_llm_args(args)
    if args.save is not None and not args.save:
        raise CliError(CLI_ERR_SAVE_NEEDS_PATH)
    plan = None
    if args.llm or args.llm_dry_run:
        from driftpilot.cli import batch  # cli.batch はこのモジュールを読むため、ここで読む

        plan = batch._llm_plan(args)  # 比較より前に設定を読む(無い名前なら1組も比較しない)
    try:
        request = _request(args)
    except (CliError, ValueError) as exc:
        if args.format != "json":
            raise
        # 読み込みの失敗も JSON の契約(schema_version と status)に載せる。メッセージは標準エラーにも出す
        write_machine(to_json_all(MultiCompareOutcome("error", [], [])) + "\n")
        print(f"{CLI_ERROR_PREFIX}{exc}", file=sys.stderr)
        return EXIT_BY_STATUS["error"]
    preview = None
    if plan is None:
        outcome = compare_all(request)
    else:
        outcome, preview = _compare_all_with_llm(request, plan)
    if args.format == "json":
        write_machine(to_json_all(outcome) + "\n")
    elif body := to_text_all(outcome):
        print(body)
    if not outcome.pairs and not outcome.unpaired:
        print(f"{CLI_ERROR_PREFIX}{CLI_ERR_NO_SEGMENTS}", file=sys.stderr)
    for index, pair in enumerate(outcome.pairs, start=1):
        result = pair.outcome
        if result.status == "needs_selection":
            message = result.selection_detail.get("message", CLI_ERR_SEGMENT_NOT_SELECTED)
            print(f"{CLI_ERROR_PREFIX}[{pair_label(index, pair)}] {message}", file=sys.stderr)
        elif result.error:
            print(f"{CLI_ERROR_PREFIX}[{pair_label(index, pair)}] {result.error.message}", file=sys.stderr)
    if args.llm and not args.confirm and preview is not None and preview.jobs and not preview.over_limit:
        print(compare_llm_confirm_hint(), file=sys.stderr)
    if gave_up := gave_up_count(outcome.pairs):
        print(batch_llm_gave_up(gave_up), file=sys.stderr)
    if args.save is not None and not _save_all(args, outcome):
        return EXIT_ERROR
    return EXIT_BY_STATUS[outcome.status]


def _api_error_lines(llm: LlmAnalysis | None, notes: list[ApiErrorText]) -> list[str]:
    """compare --llm(1組)の標準エラーに出す行(ADR-0168 決定4・5)。429 の文(最後に「(HTTP 429)」)と、API が返した文。

    notes は on_api_error が受け取った、利用者の秘密の語で掃除した後の値。届いていなければ何も出さない。
    """
    if not notes:
        return []
    note = notes[-1]
    lines: list[str] = []
    status_code = llm.detail.get("status_code") if llm is not None and llm.reason == "api_error" else None
    if status_code == 429:
        lines.append(f"{llm_rate_limited(note.retry_after, cli=True)}(HTTP 429)")
    if note.message:
        lines.append(cli_api_error_line(note.message))
    return lines


def _run_compare(args: argparse.Namespace) -> int:
    if args.all_segments:
        return _run_compare_all(args)
    if not _check_raw_options(args):
        return EXIT_ERROR
    check_all_segments_llm_args(args)
    if args.save is not None and not args.save:
        raise CliError(CLI_ERR_SAVE_NEEDS_PATH)
    llm = _llm_request(args)
    try:
        request = _request(args)
    except (CliError, ValueError) as exc:
        if args.format != "json":
            raise
        # 読み込みの失敗も JSON の契約(schema_version と status)に載せる。メッセージは標準エラーにも出す
        error = CompareError("input_error", "read_input", str(exc))
        write_machine(to_json(CompareOutcome(status="error", mode=args.mode, error=error)) + "\n")
        print(f"{CLI_ERROR_PREFIX}{exc}", file=sys.stderr)
        return EXIT_BY_STATUS["error"]
    # API が返したエラーの文と待つ秒(ADR-0168 決定5)。送る要求のときだけ集め、標準エラーにだけ出す(標準出力・JSON・保存には入れない)
    notes: list[ApiErrorText] = []
    if llm is not None and not llm.dry_run and llm.client is not None:
        llm = dataclasses.replace(llm, on_api_error=notes.append)
    # 終了コードは比較の結果のまま。LLM 解析の成否では変えない(ADR-0018 決定4・決定5)
    outcome = run_compare(request, llm=llm)
    if args.format == "json":
        write_machine(to_json(outcome) + "\n")
    elif body := to_text(outcome):
        print(body)
    for line in _api_error_lines(outcome.llm, notes):
        print(line, file=sys.stderr)
    if outcome.status == "needs_selection":
        if outcome.selection_reason == "ambiguous_format":
            print(f"{CLI_ERROR_PREFIX}{outcome.selection_detail['message']}", file=sys.stderr)
        else:
            print(f"{CLI_ERROR_PREFIX}{CLI_ERR_SEGMENTS_AMBIGUOUS}", file=sys.stderr)
    elif outcome.error:
        print(f"{CLI_ERROR_PREFIX}{outcome.error.message}", file=sys.stderr)
    if args.save is not None:
        paths = {"before": str(Path(args.before).absolute()), "after": str(Path(args.after).absolute())}
        try:
            saved = save(build_record(request, outcome, source_paths=paths), args.save)
        except StorageError as exc:
            # 保存の失敗は、比較結果の終了コード(1 や 3)より優先して 2 にする。
            # 保存を頼まれたのに残らなかったことを、差分ありの 1 に紛れさせないため
            print(f"{CLI_ERROR_PREFIX}{exc}", file=sys.stderr)
            return EXIT_ERROR
        print(saved_notice(saved), file=sys.stderr)
    return EXIT_BY_STATUS[outcome.status]


def _run_gui(args: argparse.Namespace) -> int:
    """pywebview の比較画面を開き、閉じたら 0。pywebview が無ければ案内を出して 2(driftpilot.gui_web 自体は pywebview 無しで読める)。

    開けないとき(WebView2 が無い・表示部品の初期化に失敗)も、理由を出して 2(REQ-107 §2)。
    """
    from driftpilot import gui_web

    if not gui_web.available():
        raise CliError(gui_web_unavailable())
    return gui_web.main()


def _run(args: argparse.Namespace) -> int:
    if args.command == "patterns":
        return _run_patterns(args)
    if args.command == "coverage":
        return _run_coverage(args)
    if args.command == "coverage-show":
        return _run_coverage_show(args)
    if args.command == "show-commands":
        return _run_show_commands(args)
    if args.command == "mask-patterns":
        return _run_mask_patterns(args)
    if args.command == "mask-coverage":
        return _run_mask_coverage(args)
    if args.command == "batch":
        from driftpilot.cli import batch

        return batch.run(args)
    if args.command == "llm-check":
        from driftpilot.cli import llm_check

        return llm_check.run(args)
    if args.command == "selfcheck":
        from driftpilot.cli import selfcheck

        return selfcheck.run(args)
    if args.command == "gui":
        return _run_gui(args)
    return _run_compare(args)


def main(argv: list[str] | None = None) -> int:
    """比較を実行して終了コード(差分なし 0 / 差分あり 1 / エラー 2 / 選択待ち 3)を返す。"""
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else EXIT_ERROR
    try:
        return _run(args)
    except (CliError, ValueError) as exc:
        print(f"{CLI_ERROR_PREFIX}{exc}", file=sys.stderr)
        return EXIT_ERROR
