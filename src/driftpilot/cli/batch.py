"""`driftpilot batch`(REQ-15 / ADR-0016・ADR-0020・ADR-0031)。

引数の組み合わせの検査、出力先を開く処理、文章化、Ctrl+C の処理、終了コードへの写しだけを持つ。
一括のロジックは core.batch、マニフェストと出力の形は driftpilot.batch_io にある(GUI と共通)。

LLM 解析(ADR-0031)は実行時の指定だけ(マニフェストには書けない)。確認は対の生成と同じ形にする:
--llm だけなら1段目(伏せるまで。送らない)を流して要約を出し、送るのは --confirm を付けたときだけ。
--llm --confirm では1段目・全体の上限の判定・2段目(承認した内容だけを送る)を続けて行い、行は最後に job の順で書く。
"""

import argparse
import json
import signal
import sys
from collections.abc import Callable
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from driftpilot.batch_io import (
    JsonLinesWriter,
    ManifestWriteError,
    PairingError,
    job_record,
    job_records,
    load_job_rules,
    load_manifest,
    make_prepare,
    pair_by_name,
    pairing_manifest,
    summary_dict,
    write_manifest,
)
from driftpilot.cli.commands import (
    EXIT_BY_STATUS,
    EXIT_ERROR,
    EXIT_SAME,
    CliError,
    add_llm_profile_argument,
    add_llm_no_premise_argument,
    add_llm_prompt_profile_argument,
    apply_premise,
    check_llm_args,
    load_llm_settings,
)
from driftpilot.messages import (
    batch_cancel_accepted,
    batch_llm_confirm_hint,
    batch_llm_gave_up,
    batch_llm_nothing_to_send,
    batch_llm_over_limit,
    batch_llm_preview_lines,
    batch_llm_preview_row,
    batch_saved_notice,
    batch_summary_lines,
    pairing_text,
    selection_text,
)
from driftpilot.cli.output import machine_stdout, open_machine_file
from driftpilot.core import (
    DEFAULT_MAX_TOTAL_DIFFS,
    BatchInterrupted,
    BatchLlmPreview,
    BatchSummary,
    CompareRequest,
    Job,
    JobResult,
    LlmRequest,
    Manifest,
    ManifestError,
    approved_payloads,
    combined_summary,
    final_results,
    gave_up_count,
    is_failed_with_payload,
    llm_preview,
    parse_manifest,
    run_batch,
    withhold,
)
from driftpilot.llm_analyzer import DEFAULT_MAX_DIFFS, LlmReason
from driftpilot.llm_settings import build_request as build_llm_request
from driftpilot.llm_settings import display_model, with_cli_limits
from driftpilot.storage import JOB_KEY, StorageError, default_filename, save

_LABEL = {"before": "変更前", "after": "変更後"}


def add_parser(sub: Any) -> None:
    bat = sub.add_parser("batch", help="組のファイルに書いた比較の組を順に実行する(1件1行の JSON Lines)")
    bat.add_argument("manifest", nargs="?", metavar="MANIFEST", help="組のファイル（マニフェスト。YAML）のパス")
    bat.add_argument("--output", metavar="PATH", help="JSON Lines の出力先(既定: 標準出力)。既にあるファイルは上書きしない")
    bat.add_argument(
        "--summary", metavar="PATH", help="サマリを JSON で書く(終了コードを含む)。既にあるファイルは上書きしない"
    )
    bat.add_argument("--quiet", action="store_true", help="標準エラーへのサマリの表示を抑える")
    bat.add_argument("--dry-run", action="store_true", help="解釈して比較の予定を一覧に出すだけ。比較しない(終了コード 0)")
    bat.add_argument("--save", metavar="DIR", help="各 job の結果を、このディレクトリに1件1ファイルで保存する(既定: 保存しない)")
    bat.add_argument(
        "--pair-by-name",
        nargs=2,
        metavar=("BEFORE_DIR", "AFTER_DIR"),
        help="直下のファイル名が完全一致するものを対にし、一覧を出す(比較しない)",
    )
    bat.add_argument(
        "--confirm",
        action="store_true",
        help="--pair-by-name の対で比較を実行する。--llm では、要約を確かめた後に AI への送信を実行する",
    )
    bat.add_argument("--write-manifest", metavar="PATH", help="--pair-by-name の対を組のファイルとして書いて終わる")
    llm = bat.add_mutually_exclusive_group()
    llm.add_argument(
        "--llm",
        action="store_true",
        help="各組の差分を AI に送り、注釈を付ける。--confirm が無ければ、送る内容の要約を出すだけで送らない",
    )
    llm.add_argument(
        "--llm-dry-run", action="store_true", help="AI に送る内容(マスク後)と、送る前の要約を出す。送信はしない"
    )
    bat.add_argument(
        "--llm-max-diffs",
        type=int,
        metavar="N",
        help=(
            "1組あたり AI に送る差分の件数の上限。1 以上"
            f"(既定: 利用者ごとの設定ファイル config.toml の llm.max_diffs、無ければ {DEFAULT_MAX_DIFFS})"
        ),
    )
    bat.add_argument(
        "--llm-max-total-diffs",
        type=int,
        metavar="N",
        help=(
            "全組で AI に送る差分の合計の上限。超えたら1件も送らない。1 以上"
            f"(既定: 利用者ごとの設定ファイル config.toml の llm.max_total_diffs、無ければ {DEFAULT_MAX_TOTAL_DIFFS})"
        ),
    )
    add_llm_profile_argument(bat)
    add_llm_prompt_profile_argument(bat)
    add_llm_no_premise_argument(bat)


def _check_args(args: argparse.Namespace) -> None:
    pairing = args.pair_by_name is not None
    llm = args.llm or args.llm_dry_run
    if pairing == (args.manifest is not None):
        raise CliError("MANIFEST か --pair-by-name BEFORE_DIR AFTER_DIR のどちらか一方を指定してください")
    if args.confirm and args.write_manifest is not None:
        raise CliError("--confirm と --write-manifest は一緒に使えません")
    if not pairing and args.write_manifest is not None:
        raise CliError("--write-manifest は --pair-by-name と一緒に使ってください")
    if args.confirm and not pairing and not args.llm:
        raise CliError("--confirm は --pair-by-name か --llm と一緒に使ってください")
    if pairing and args.dry_run:
        raise CliError("--pair-by-name は単独で一覧を出すだけです(--dry-run は要りません)")
    check_llm_args(args)
    if args.llm_max_total_diffs is not None:
        if not llm:
            raise CliError("--llm-max-total-diffs は --llm か --llm-dry-run と一緒に指定してください")
        if args.llm_max_total_diffs < 1:
            raise CliError("--llm-max-total-diffs は 1 以上にしてください(無制限の指定はありません)")
    if llm and (args.dry_run or args.write_manifest is not None):
        raise CliError("--llm と --llm-dry-run は比較を実行するときだけ使えます(--dry-run・--write-manifest とは一緒に使えません)")
    compares = not args.dry_run and (not pairing or args.confirm)
    if not compares:
        given = [
            name
            for name, value in (("--output", args.output), ("--summary", args.summary), ("--save", args.save))
            if value is not None
        ]
        if given:
            raise CliError(f"{', '.join(given)} は比較を実行するときだけ使えます")
    paths = (
        ("--output", args.output),
        ("--summary", args.summary),
        ("--save", args.save),
        ("--write-manifest", args.write_manifest),
    )
    for name, value in paths:
        if value is not None and not value:
            raise CliError(f"{name} にはパスを指定してください")
    if args.output is not None and args.summary is not None and Path(args.output).absolute() == Path(args.summary).absolute():
        raise CliError("--output と --summary には別のパスを指定してください")


def run(args: argparse.Namespace) -> int:
    _check_args(args)
    if args.pair_by_name is not None:
        return _run_pairing(args)
    try:
        manifest = load_manifest(args.manifest)
        rules = load_job_rules(manifest)
    except ManifestError as exc:
        return _manifest_failed(args, exc)
    if args.dry_run:
        sys.stdout.write(_dry_run_text(Path(args.manifest).absolute(), manifest))
        return EXIT_SAME
    return _execute(args, manifest, rules)


def _manifest_failed(args: argparse.Namespace, exc: ManifestError) -> int:
    """マニフェストとして成立していない。1件も実行せずに 2(決定5)。"""
    print(f"エラー: 組のファイルを読めません({len(exc.problems)} 件)。1件も実行していません", file=sys.stderr)
    for where, message in exc.problems:
        print(f"  {where}: {message}", file=sys.stderr)
    if args.summary is not None:
        _write_summary(args.summary, summary_dict(None, exit_code=EXIT_ERROR, manifest_error=str(exc)))
    return EXIT_ERROR


# --- 実行 ---


class _Interrupts:
    """実行中の Ctrl+C。1回目は job の境界で止める合図(実行中の job は終える)、2回目は即座に止める(Q1 A)。

    2回目は、どこで受けても、それ以上の比較も保存もしない。ただし KeyboardInterrupt を起こすのは armed の間だけ:

    - armed(入力の読み込み・比較・行の組み立て・--save の保存): すぐ KeyboardInterrupt を起こす。
      行を書く前なら、その job は行も件数も残らない。保存中なら on_result が受け止めて保存を打ち切る
    - armed でない(1行を書いている間と、書いた後に core.run_batch が数えるまで): 起こさない。起こすと、書き終えた行が
      集計と終了コードから漏れる(core は on_result が返った結果を数える)。この間に受けたときは保存をせずに on_result を返し、
      core が数えた直後の should_cancel で止まる。止まるまでに比較も保存もしない

    KeyboardInterrupt を起こすときは armed を外す。続けて押しても、受け止めている途中で二重に起こさないため。
    """

    def __init__(self) -> None:
        self.count = 0
        self.armed = False

    def handle(self, signum: int, frame: Any) -> None:
        self.count += 1
        if self.count == 1:
            print(batch_cancel_accepted(), file=sys.stderr)
            return
        if self.armed:
            self.armed = False
            raise KeyboardInterrupt

    def should_cancel(self) -> bool:
        return self.count > 0

    def stopping(self) -> bool:
        return self.count >= 2

    def arm(self) -> None:
        """ここから2回目の Ctrl+C ですぐ止める。既に受けていれば、ここで止める。"""
        self.armed = True
        if self.stopping():
            self.armed = False
            raise KeyboardInterrupt

    def disarm(self) -> None:
        self.armed = False


@contextmanager
def _sigint(interrupts: _Interrupts):
    """実行中だけ SIGINT の処理を差し替え、終わったら元に戻す。"""
    try:
        previous = signal.signal(signal.SIGINT, interrupts.handle)
    except ValueError:  # メインスレッド以外。差し替えられないときは既定の扱いのまま
        yield
        return
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def _prepare_outputs(args: argparse.Namespace) -> None:
    if args.save is not None and not Path(args.save).is_dir():
        raise CliError(f"--save {args.save}: 既存のディレクトリを指定してください(自動では作りません)")
    for name, value in (("--output", args.output), ("--summary", args.summary)):
        if value is not None and Path(value).exists():
            raise CliError(f"{name} {value}: 既にあります。上書きしません")


def _open_output(path: str) -> TextIO:
    try:
        return open_machine_file(path, "x")
    except FileExistsError as exc:
        raise CliError(f"--output {path}: 既にあります。上書きしません") from exc
    except OSError as exc:
        raise CliError(f"--output {path}: 書けません ({exc.strerror or exc})") from exc


@dataclass(frozen=True)
class _LlmPlan:
    """batch の LLM の進め方。first は1段目(dry_run)か、client を作れなかったときの1回だけの要求。

    send があるときだけ2段目で送る(--llm --confirm)。preview は1段目の要約を標準エラーに出すか。
    """

    first: LlmRequest
    send: LlmRequest | None
    preview: bool
    max_total_diffs: int
    model: str | None
    base_url: str | None


def _llm_plan(args: argparse.Namespace) -> _LlmPlan | None:
    if not (args.llm or args.llm_dry_run):
        return None
    # 比較より前に読む(無い名前なら、接続のプロファイルも指示文のプロファイルも1件も比較しない)
    settings = load_llm_settings(args.llm_profile, args.llm_prompt_profile)
    # 上限はコマンドの指定 ＞ 利用者ごとの設定ファイル ＞ 既定(ADR-0071 決定5)
    settings = with_cli_limits(settings, max_diffs=args.llm_max_diffs, max_total_diffs=args.llm_max_total_diffs)
    # 毎回付ける前提(ADR-0172)。--llm-no-premise なら外し、送れない前提なら1件も比較せずに止める(compare --all-segments も通る)
    settings = apply_premise(settings, args)
    common = {
        "max_total_diffs": settings.max_total_diffs,
        "model": display_model(settings),  # CLI の形式でモデルが無いときは「CLI の既定のモデル」(REQ-102)
        "base_url": settings.base_url,
    }
    dry = build_llm_request(settings, dry_run=True)
    if args.llm_dry_run or not args.confirm:
        return _LlmPlan(first=dry, send=None, preview=True, **common)
    send = build_llm_request(settings)
    if send.client is None:
        # 送れない(キーやモデルが無いなど)。1回だけ流し、各行の llm に「未実施(理由)」を残す
        return _LlmPlan(first=send, send=None, preview=False, **common)
    return _LlmPlan(first=dry, send=send, preview=True, **common)


def _print_preview(plan: _LlmPlan, results: list[JobResult]) -> BatchLlmPreview:
    """1段目の要約を標準エラーに出す。--quiet でも消さない(送る前の確認のため。対の一覧と同じ扱い)。"""
    pv = llm_preview(results, max_total_diffs=plan.max_total_diffs)
    for line in batch_llm_preview_lines(pv, model=plan.model, base_url=plan.base_url):
        print(line, file=sys.stderr)
    for job in pv.jobs:
        print(batch_llm_preview_row(job.job_id, job.label, job.sent_diffs, job.masked, job.unsent_lines), file=sys.stderr)
    if pv.over_limit:
        print(batch_llm_over_limit(pv.total_diffs, pv.max_total_diffs), file=sys.stderr)
    elif not pv.jobs:
        print(batch_llm_nothing_to_send(), file=sys.stderr)
    return pv


def _two_stages(
    manifest: Manifest,
    plan: _LlmPlan,
    run_stage: Callable[..., BatchSummary],
    emit: Callable[[JobResult], None],
    collected: list[JobResult],
) -> BatchSummary:
    """--llm --confirm。1段目で伏せる → 要約と全体の上限 → 2段目で承認した内容だけを送る → job の順に書く。

    1段目を中断したときと、上限を超えたときは送らない(送らなかった理由を各行の llm に入れる)。
    """
    first: list[JobResult] = []
    second: list[JobResult] = []
    s1 = run_stage(manifest.jobs, first.append, plan.first)
    later: list[BatchSummary] = []
    approved: dict[str, str] = {}
    reason: LlmReason = "cancelled"
    if not (s1.cancelled or s1.interrupted):
        pv = _print_preview(plan, first)
        reason = "over_total_limit" if pv.over_limit else "not_approved"
        # CLI は --confirm が承認(要約に出した組をすべて送る)。上限を超えていれば空になる
        approved = approved_payloads(pv, {job.job_id for job in pv.jobs})
        if approved:
            jobs = [job for job in manifest.jobs if job.id in approved]
            later.append(run_stage(jobs, second.append, plan.send, approved))
    results = final_results(first, second, approved=approved, reason=reason)
    for result in results:
        emit(result)
    collected.extend(results)
    return combined_summary(s1, results, later=later)


def _execute(
    args: argparse.Namespace,
    manifest: Manifest,
    rules: dict,
    unpaired: dict[str, list[str]] | None = None,
) -> int:
    plan = _llm_plan(args)
    _prepare_outputs(args)
    # JSON Lines の出力先は --output のファイルか、UTF-8・\n に固定した標準出力(ADR-0023 決定1)。抜けるときに閉じる・外す
    outputs = ExitStack()
    stream = outputs.enter_context(_open_output(args.output) if args.output is not None else machine_stdout())
    writer = JsonLinesWriter(stream)
    saved_count = 0
    save_failures: list[dict[str, str]] = []
    interrupts = _Interrupts()
    read_inputs = make_prepare(rules)
    width = len(str(len(manifest.jobs)))

    def prepare(job: Job) -> CompareRequest:
        interrupts.arm()  # 入力の読み込みから行を組み立て終えるまで、2回目の Ctrl+C ですぐ止める(まだ何も書いていない)
        return read_inputs(job)

    def save_failed(job: Job, message: str) -> None:
        # 残りは続ける。保存を頼まれたのに残らなかったことは、最後に終了コード 2 で知らせる
        print(f"エラー: job {job.id}: {message}", file=sys.stderr)
        save_failures.append({"id": job.id, "error": message})

    def on_result(result: JobResult) -> None:
        # まとめて比べた組は、コマンドの組ごとの行(ADR-0178 決定3)。1 区間の組は今のまま 1 行
        records = job_records(result) if result.multi is not None else [job_record(result)]
        interrupts.disarm()  # ここから core が数えるまでは KeyboardInterrupt を起こさない(書いた行を集計から漏らさない)
        for record in records:
            writer.write(record)
        if args.save is None:
            return
        for record in records:
            if not save_one(result.job, record):
                return

    def save_one(job: Job, record: dict[str, Any]) -> bool:
        """--save の 1 ファイル。中断で止めたときは False(残りの行も保存しない)。"""
        nonlocal saved_count
        if interrupts.stopping():  # 行を書いている間に2回目を受けた。保存せずに止める
            save_failed(job, "中断したため保存していません")
            return False
        # 保存名は <日時>_<ラベル>.json で、ラベルの記号は _ に置き換わる(sw/01 と sw:01 が同じになる)。
        # job の順番(マニフェスト内で一意)を前に付け、同じ秒に保存しても job ごとに名前が一意になるようにする。
        # まとめて比べた組の行は job.id が <組の id>/<コマンドの組の番号> なので、行ごとに名前が一意になる
        target = Path(args.save) / default_filename(record, f"{job.index:0{width}d}-{record[JOB_KEY]['id']}")
        existed = target.exists()
        try:
            try:
                interrupts.arm()  # 保存中も2回目ですぐ止める。ここで受け止めて、行は数えたまま on_result を返す
                save(record, target)
            finally:
                interrupts.disarm()  # どの抜け方でも外す。外す前に受けた2回目も、下の except で受け止める
        except KeyboardInterrupt:
            leftover = f"。書きかけの {target} が残っている場合があります" if not existed and target.exists() else ""
            save_failed(job, f"保存の途中で中断しました{leftover}")
            return False
        except StorageError as exc:
            save_failed(job, str(exc))
        else:
            saved_count += 1
        return True

    def run_stage(jobs, on_job_result, llm: LlmRequest | None, approved: dict[str, str] | None = None) -> BatchSummary:
        options: dict[str, Any] = {} if llm is None else {"llm": llm, "approved": approved}
        try:
            return run_batch(
                jobs, prepare=prepare, on_result=on_job_result, should_cancel=interrupts.should_cancel, **options
            )
        except BatchInterrupted as exc:
            return exc.summary
        finally:
            interrupts.disarm()  # 段の間と最後の書き出しの前は、2回目の Ctrl+C で KeyboardInterrupt を起こさない

    collected: list[JobResult] = []
    stage_one: BatchLlmPreview | None = None
    try:
        with outputs, _sigint(interrupts):
            if plan is None or plan.send is None:
                # 今までどおり1件ずつ書く。LLM の要約を出すときだけ、結果を手元にも残す

                def collect(result: JobResult) -> None:
                    if plan is not None and is_failed_with_payload(result):
                        result = withhold(result, "compare_failed")  # 比較が失敗した組は送る対象にしない
                    if plan is not None and plan.preview:
                        collected.append(result)
                    on_result(result)

                summary = run_stage(manifest.jobs, collect, plan.first if plan is not None else None)
                if plan is not None and plan.preview:
                    stage_one = _print_preview(plan, collected)
            else:
                summary = _two_stages(manifest, plan, run_stage, on_result, collected)
    except OSError as exc:
        raise CliError(f"結果を書けません ({exc.strerror or exc})") from exc

    if args.llm and not args.confirm and stage_one is not None and stage_one.jobs and not stage_one.over_limit:
        print(batch_llm_confirm_hint(), file=sys.stderr)
    if gave_up := gave_up_count(collected):
        print(batch_llm_gave_up(gave_up), file=sys.stderr)
    exit_code = EXIT_BY_STATUS[summary.overall_status]
    if save_failures:
        exit_code = EXIT_ERROR  # REQ-14 と同じく、保存の失敗は比較結果の終了コードより優先する
    # --summary を先に書く。書けなければ 2 にしてから、確定した終了コードを標準エラーに出す(表示と実際を食い違わせない)。
    # 書けたファイルの exit_code は、書けた場合の終了コード(=実際の終了コード)と一致する
    if args.summary is not None:
        saved = None
        if args.save is not None:
            saved = {"dir": str(Path(args.save).absolute()), "count": saved_count, "failures": save_failures}
        data = summary_dict(summary, exit_code=exit_code, saved=saved, unpaired=unpaired)
        if not _write_summary(args.summary, data):
            exit_code = EXIT_ERROR
    if not args.quiet:
        for line in batch_summary_lines(summary, exit_code):
            print(line, file=sys.stderr)
    if saved_count:
        print(batch_saved_notice(Path(args.save), saved_count), file=sys.stderr)
    return exit_code


def _write_summary(path: str, data: dict[str, Any]) -> bool:
    try:
        with open_machine_file(path, "x") as fh:
            fh.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    except FileExistsError:
        print(f"エラー: --summary {path}: 既にあります。上書きしません", file=sys.stderr)
        return False
    except OSError as exc:
        print(f"エラー: --summary {path}: 書けません ({exc.strerror or exc})", file=sys.stderr)
        return False
    return True


# --- --dry-run(決定3) ---


def _value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, tuple):
        return ", ".join(value) if value else "(なし)"
    return str(value)


def _dry_run_text(manifest_path: Path, manifest: Manifest) -> str:
    lines = [f"一括比較の予定(比較は実行していません): {manifest_path}", ""]
    missing: set[Path] = set()
    for job in manifest.jobs:
        head = f"[{job.index}] {job.id}"
        lines.append(f"{head}  {job.label}" if job.label else head)
        for side in ("before", "after"):
            spec = getattr(job, side)
            found = spec.path.is_file()
            if not found:
                missing.add(spec.path)
            state = "あり" if found else "見つかりません"
            lines.append(
                f"  {_LABEL[side]}: {spec.path} ({state})  区間: {selection_text(spec.selection, spec.selection_source)}"
            )
        lines.append(
            f"  mode: {job.mode} / kind: {job.kind} / platform: {_value(job.platform)} / "
            f"show_command: {_value(job.show_command)}"
        )
        extra = [
            f"{name}: {_value(value)}"
            for name, value in (("prompt", job.prompt), ("rules", job.rules_path), ("enable_rule", job.enabled_rule_ids))
            if value is not None
        ]
        if extra:
            lines.append("  " + " / ".join(extra))
    lines.append("")
    lines.append(f"合計 {len(manifest.jobs)} 件。見つからないファイル {len(missing)} 件")
    return "\n".join(lines) + "\n"


# --- --pair-by-name(ADR-0020) ---


def _run_pairing(args: argparse.Namespace) -> int:
    try:
        pairing = pair_by_name(*args.pair_by_name)
    except PairingError as exc:
        raise CliError(str(exc)) from exc
    # 件数の言い方は GUI の一括比較画面と同じ関数から作る(REQ-27 §A-2)
    listing = pairing_text(pairing)
    if not args.confirm and args.write_manifest is None:
        sys.stdout.write(listing)
        print("比較は実行していません。この対で実行するには --confirm、組のファイルとして保存するには --write-manifest PATH を付けてください")
        return EXIT_SAME
    if not pairing.pairs:
        (sys.stdout if args.write_manifest is not None else sys.stderr).write(listing)
        raise CliError("対になったファイルがありません")
    if args.write_manifest is not None:
        sys.stdout.write(listing)
        target = Path(args.write_manifest)
        try:
            written = write_manifest(pairing_manifest(pairing, target.absolute().parent), target)
        except (ManifestWriteError, ManifestError) as exc:
            raise CliError(str(exc)) from exc
        print(f"組のファイルを {written} に書きました。比較は実行していません")
        return EXIT_SAME
    # --confirm: 標準出力は JSON Lines なので、一覧は標準エラーに出す。--quiet でも消さない
    sys.stderr.write(listing)
    manifest = parse_manifest(pairing_manifest(pairing, None), base_dir=None)
    unpaired = {"before_only": list(pairing.before_only), "after_only": list(pairing.after_only)}
    return _execute(args, manifest, {}, unpaired=unpaired)
