"""一括比較画面の論理(REQ-27 パート A / ADR-0029 決定5〜9・ADR-0031)。wx を読まない。

対の生成・マニフェストの読み書き・一括実行・結果の書き出しは、既にある関数(batch_io / core)を呼ぶだけ。
組を作る(prepare_folders / prepare_manifest)のは一覧を出すためで、比較は1件もしない(実行は「実行」を押したときだけ)。
wx の画面では組を編集しない(ここの prepare_* と save_pairing_manifest のまま)。pywebview の一括比較の窓は、組を直す・外す・
組にする・機種を自動で選ぶ(REQ-212 / ADR-0153 決定4〜8)。その論理は screen_model/batch_edit.py にあり、ここは 1 つの表の行
(table_rows。外した組と「自動: <判定した名前>」)を足しただけ。
件数・状態・AI の列・回答の文面は messages(CLI と共通)から取る。ここに置くのは画面の言葉と、行の組み立てだけ。

LLM の段取り(ADR-0031 決定2・9): 1段目(送らない)→ 要約と全体の上限 → 確認の画面を1回 → 承認した組だけ2段目で送る。
マニフェストには LLM を書かない(決定6)。チェックは実行時の指定だけ。
"""

from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from driftpilot.batch_io import (
    ManifestWriteError,
    Pairing,
    PairingError,
    load_job_rules,
    load_manifest,
    make_prepare,
    pair_by_name,
    pairing_manifest,
    write_job_lines,
    write_manifest,
    write_text_file,
)
from driftpilot.core import (
    AUTO_PLATFORM,
    DEFAULT_MAX_TOTAL_DIFFS,
    AutoPlatform,
    BatchLlmPreview,
    CompareOutcome,
    CompareRequest,
    Job,
    JobResult,
    ManifestError,
    approved_payloads,
    final_results,
    job_pair_results,
    llm_preview,
    parse_manifest,
)
from driftpilot.screen_model.model import (
    CONFIG_PLATFORM_CHOICES,
    UNKNOWN_COMMAND,
    WEB_PLATFORM_CHOICES,
    KindChoice,
    LlmPlan,
    Mode,
    file_label,
    pair_report,
    plain_llm,
    read_counts,
    redact_text,
)
from driftpilot.core import DEFAULT_RAW_OPTIONS, RawOptions
from driftpilot.report_output import Report, ReportPair
from driftpilot.llm_analyzer import LlmAnalysis
from driftpilot.llm_analyzer.answer_format import first_sentence
from driftpilot.llm_settings import LlmSettings, default_base_url, display_model
from driftpilot.messages import (
    BATCH_AUTO_PLATFORM_MIXED,
    BATCH_JOB_EXCLUDED,
    BATCH_PLATFORM_UNDETECTED_ROW,
    BATCH_WAITING_FORMAT,
    BATCH_WAITING_NAMES_HIDDEN,
    EXPORT_TITLE_BATCH,
    JOB_RUNNING,
    JOB_WAITING,
    NO_COUNT,
    STATUS_LABEL,
    UNPAIRED_SIDE_LABEL,
    batch_all_tab_title,
    batch_diff_tab_title,
    batch_folder_not_found,
    batch_llm_nothing_to_send,
    batch_manifest_not_found,
    batch_llm_over_limit,
    batch_llm_payloads_text,
    batch_llm_preview_lines,
    batch_platform_auto_row,
    batch_waiting_segment,
    llm_column,
    llm_limit_lines,
    llm_section_lines,
    multi_pair_name,
    paired_tab_title,
    pairing_banner,
    platform_label,
    pairing_summary_lines,
    selection_text,
    unpaired_tab_title,
)

from driftpilot.messages import (
    BATCH_MANIFEST,
    SMB_LLM_CHECK_LABEL,
    SMB_MANIFEST_VALUES,
    SMB_SAVE_ONLY_FOLDER_PAIRS,
    SMB_SOURCE_FOLDERS,
    SMB_SOURCE_MANIFEST,
    SMB_TEXT_WILDCARD,
    smb_default_url,
    smb_manifest_error,
    smb_side_selection,
    smb_unparsed_counts,
)

Source = Literal["folders", "manifest"]
SOURCE_CHOICES: tuple[tuple[Source, str], ...] = (
    ("folders", SMB_SOURCE_FOLDERS),
    ("manifest", SMB_SOURCE_MANIFEST),
)
# 種類は config に固定する(フォルダの組は config の比較。マニフェストではマニフェストの値を使う)
KIND_LABEL = "config"
MANIFEST_VALUES = SMB_MANIFEST_VALUES
MANIFEST_LABEL = BATCH_MANIFEST
LLM_CHECK_LABEL = SMB_LLM_CHECK_LABEL
JSONL_WILDCARD = "JSON Lines (*.jsonl)|*.jsonl"
MANIFEST_WILDCARD = "YAML (*.yaml;*.yml)|*.yaml;*.yml"
TEXT_WILDCARD = SMB_TEXT_WILDCARD


class BatchSetupError(Exception):
    """組を作れない(フォルダが無い・マニフェストが読めないなど)。比較は1件もしていない。"""


# --- 組の一覧(ADR-0020) ---


@dataclass(frozen=True)
class PairRow:
    index: int
    before: str
    after: str
    selection: str


@dataclass(frozen=True)
class UnpairedRow:
    side: str
    name: str


@dataclass(frozen=True)
class PreparedBatch:
    """「組を作り直す」の結果。比較はまだ1件もしていない。

    pairing はフォルダから作ったときだけ(マニフェストとして保存できる)。mode と platform はそのときの選択。
    """

    jobs: tuple[Job, ...]
    rules: Mapping[Path, list]
    pair_rows: list[PairRow]
    unpaired_rows: list[UnpairedRow]
    pairing: Pairing | None = None
    mode: Mode = "both"
    platform: str | None = None
    manifest_path: Path | None = None

    @property
    def paired_title(self) -> str:
        return paired_tab_title(len(self.jobs))

    @property
    def unpaired_title(self) -> str:
        return unpaired_tab_title(len(self.unpaired_rows))

    @property
    def banner(self) -> str:
        return pairing_banner(len(self.jobs))

    @property
    def summary_lines(self) -> list[str]:
        """組の一覧の上の見出しと件数。フォルダから作ったときは --pair-by-name の一覧と同じ行(同じ関数)。"""
        if self.pairing is not None:
            return pairing_summary_lines(self.pairing)
        return [f"{MANIFEST_LABEL}: {self.manifest_path}"] if self.manifest_path is not None else []

    @property
    def can_save_manifest(self) -> bool:
        return self.pairing is not None and bool(self.jobs)

    def prepare(self) -> Callable[[Job], CompareRequest]:
        """run_batch に渡す入力の読み込み(CLI と同じ関数)。"""
        return make_prepare(self.rules)


def _side_selection(job: Job) -> str:
    before = selection_text(job.before.selection, job.before.selection_source)
    after = selection_text(job.after.selection, job.after.selection_source)
    return before if before == after else smb_side_selection(before, after)


def pair_rows(jobs: Iterable[Job]) -> list[PairRow]:
    return [PairRow(job.index, str(job.before.path), str(job.after.path), _side_selection(job)) for job in jobs]


def _manifest_error_text(exc: ManifestError) -> str:
    lines = [smb_manifest_error(len(exc.problems))]
    lines += [f"  {where}: {message}" for where, message in exc.problems]
    return "\n".join(lines)


def folder_guide(before: str, after: str) -> str | None:
    """フォルダの欄から組を作ってよいか(REQ-97 §1)。両方が絶対パスの実在するフォルダなら None(組を作る)。

    欄が空なら空文字(組を空にし、案内は出さない)。打っている途中などで、まだその形でない側があれば、
    その側の案内の文(ステータスバー用。変更前を先に見る)。今のフォルダを基準に探さないため、相対パスは見ない。
    """
    if not before or not after:
        return ""
    for side, text in (("before", before), ("after", after)):
        path = Path(text)
        if not (path.is_absolute() and path.is_dir()):
            return batch_folder_not_found(side, text)
    return None


def manifest_guide(path: str) -> str | None:
    """マニフェストの欄から組を作ってよいか(REQ-97 §1)。絶対パスの実在するファイルなら None。空なら空文字、それ以外は案内の文。"""
    if not path:
        return ""
    return None if Path(path).is_absolute() and Path(path).is_file() else batch_manifest_not_found(path)


def prepare_folders(before_dir: str | Path, after_dir: str | Path, *, mode: Mode, platform: str | None) -> PreparedBatch:
    """フォルダ2つの直下を同じ名前で組にする(pair_by_name の規則のまま)。比較はしない。

    機種と比較方式はマニフェストの defaults に入れる(job ごとに書かない)。LLM の項目は書かない。
    """
    try:
        pairing = pair_by_name(before_dir, after_dir)
    except PairingError as exc:
        raise BatchSetupError(str(exc)) from exc
    unpaired = [UnpairedRow(UNPAIRED_SIDE_LABEL["before"], n) for n in pairing.before_only]
    unpaired += [UnpairedRow(UNPAIRED_SIDE_LABEL["after"], n) for n in pairing.after_only]
    jobs: tuple[Job, ...] = ()
    if pairing.pairs:
        data = pairing_manifest(pairing, None, mode=mode, platform=platform or None)
        try:
            jobs = parse_manifest(data, base_dir=None).jobs
        except ManifestError as exc:  # 画面の選択肢から作る限り起きない
            raise BatchSetupError(_manifest_error_text(exc)) from exc
    return PreparedBatch(jobs, {}, pair_rows(jobs), unpaired, pairing, mode, platform or None)


def prepare_manifest(path: str | Path) -> PreparedBatch:
    """マニフェストのファイルを読む(CLI と同じ関数)。値はマニフェストのまま使う(wx の画面では編集しない。pywebview の窓は batch_edit.from_manifest_file で直せる形にする)。"""
    try:
        manifest = load_manifest(path)
        rules = load_job_rules(manifest)
    except ManifestError as exc:
        raise BatchSetupError(_manifest_error_text(exc)) from exc
    return PreparedBatch(manifest.jobs, rules, pair_rows(manifest.jobs), [], manifest_path=Path(path).absolute())


def save_pairing_manifest(prepared: PreparedBatch, target: str | Path) -> Path:
    """「この組をマニフェストとして保存」。書く位置からの相対パスで書く。上書きしない(ManifestWriteError)。"""
    if prepared.pairing is None:
        raise ManifestWriteError(SMB_SAVE_ONLY_FOLDER_PAIRS)
    path = Path(target)
    data = pairing_manifest(prepared.pairing, path.absolute().parent, mode=prepared.mode, platform=prepared.platform)
    return write_manifest(data, path)


# --- 結果の一覧(REQ-27 §A-4) ---


@dataclass(frozen=True)
class ResultRow:
    index: int
    status: str
    name: str
    structured: str
    raw: str
    unparsed: str
    llm: str
    # 比べた機種(pywebview の結果の表の「機種」の列だけ。wx の列は足さない。ADR-0151 決定7)
    platform: str = NO_COUNT


def job_name(job: Job) -> str:
    return job.label or job.id


def waiting_row(job: Job) -> ResultRow:
    return ResultRow(job.index, JOB_WAITING, job_name(job), NO_COUNT, NO_COUNT, NO_COUNT, NO_COUNT)


def running_row(job: Job) -> ResultRow:
    return ResultRow(job.index, JOB_RUNNING, job_name(job), NO_COUNT, NO_COUNT, NO_COUNT, NO_COUNT)


def _count(value: int | None) -> str:
    return NO_COUNT if value is None else str(value)


def _sum(values: Iterable[int | None]) -> int | None:
    """数のある値の合計。どれにも数が無ければ None(列は —)。"""
    found = [value for value in values if value is not None]
    return sum(found) if found else None


def _row_counts(result: JobResult) -> tuple[int | None, int | None, dict[str, int] | None]:
    """件数の列の値 (構造化比較の差分, 単純比較の差分, 構造化不可の行)。core の結果を読むだけ(数え直さない)。

    まとめて比べた組は、全部のコマンドの組の read_counts の合計(ADR-0178 決定2)。構造化比較の列は、structured が無ければ
    show の件数(1 区間の組と同じ読み方)。構造化不可の行は前と後を別々に足す。
    """
    multi = result.multi
    if multi is None:
        counts = read_counts(result.outcome)
        return (counts.structured if counts.structured is not None else counts.show), counts.raw, counts.unparsed
    each = [read_counts(pair.outcome) for pair in multi.pairs]
    structured = _sum(c.structured if c.structured is not None else c.show for c in each)
    unparsed_list = [c.unparsed for c in each if c.unparsed is not None]
    unparsed = None
    if unparsed_list:
        unparsed = {side: sum(u[side] for u in unparsed_list) for side in ("before", "after")}
    return structured, _sum(c.raw for c in each), unparsed


def result_row(result: JobResult) -> ResultRow:
    """件数は core の結果を読むだけ(数え直さない)。状態の名前は STATUS_LABEL(CLI と同じ)。"""
    structured, raw, unparsed = _row_counts(result)
    return ResultRow(
        result.job.index,
        STATUS_LABEL[result.outcome.status],
        job_name(result.job),
        _count(structured),
        _count(raw),
        NO_COUNT if unparsed is None else smb_unparsed_counts(unparsed["before"], unparsed["after"]),
        llm_column(result.outcome.llm),
        result_platform(result),
    )


def _config_pairs(result: JobResult) -> list[CompareOutcome]:
    """まとめて比べた組のうち、config と決まったコマンドの組の結果(組の順)。1 区間の組は空。"""
    if result.multi is None:
        return []
    return [pair.outcome for pair in result.multi.pairs if pair.outcome.kind == "config"]


def result_platform(result: JobResult) -> str:
    """結果の表の「機種」の列(ADR-0151 決定7)。自動で config なら判定した名前、手で選べばその名前、show や種類が
    決まる前に止まった結果は —。表に無い名前はそのまま出す。

    まとめて比べた組は、config のコマンドがあれば手で選んだ名前(自動の組は _auto_row_platform)、無ければ —。
    """
    outcome = result.outcome
    if result.multi is not None:
        return platform_label(result.job.platform) if _config_pairs(result) else NO_COUNT
    if outcome.kind != "config":
        return NO_COUNT
    if outcome.auto_platform is not None:
        return platform_label(outcome.auto_platform.used)
    return platform_label(result.job.platform)


# --- pywebview の一括比較の窓の 1 つの表(REQ-211 / ADR-0153 決定1〜2)。wx は上の ResultRow・result_row・pair_rows のまま ---


@dataclass(frozen=True)
class BatchTableRow:
    """組の一覧と結果を 1 つにした表の 1 行。行は組の順で、index は組の番号(job.index)。

    数と機種と状態は、結果があれば result_row と同じ値。has_diff は状態が「差分あり」(difference)のとき真。
    機種が自動の組は、結果があれば「自動: <判定した名前>」か「自動: 判定できません」(REQ-212 §8)。
    job_id は組の id、included は外していないか(外した組は状態が「外した組」で数は —。REQ-212 §4)。
    platform_index は機種の列の選択の番号(WEB_PLATFORM_CHOICES。種類が config で選択肢にある値のときだけ。それ以外は None で、
    機種の列は文字で読むだけ)。
    """

    index: int
    label: str
    before: str
    after: str
    platform: str
    status: str
    structured: str
    raw: str
    unparsed: str
    ai_point: str
    has_diff: bool
    job_id: str = ""
    included: bool = True
    platform_index: int | None = None


def platform_undetected(auto: AutoPlatform | None) -> bool:
    """機種の自動で判定できなかったか(判定なし・食い違い・判定していない)。Cisco 系(used が None で判定あり)は判定できた。"""
    return auto is None or auto.conflict or (auto.before is None and auto.after is None)


def job_platform_index(job: Job) -> int | None:
    """表の機種の選択の番号。種類が config で、値(auto・指定なし・系統の名前)が窓の選択肢にあるときだけ。"""
    if job.kind != "config":
        return None
    value = job.platform or ""
    return next((index for index, (name, _label) in enumerate(WEB_PLATFORM_CHOICES) if name == value), None)


def _auto_text(outcome: CompareOutcome) -> str:
    """機種が自動で config と決まった結果の機種の文(「自動: …」か「自動: 判定できません」)。"""
    if platform_undetected(outcome.auto_platform):
        return BATCH_PLATFORM_UNDETECTED_ROW
    assert outcome.auto_platform is not None
    return batch_platform_auto_row(outcome.auto_platform.used)


def _auto_row_platform(result: JobResult, fallback: str) -> str:
    """機種が自動の組の、結果の機種の列(config と決まったときだけ「自動: …」。show などは result_row のまま)。

    まとめて比べた組(ADR-0178 決定2)は、config のコマンドの組ごとの文が 1 つならその文、2 つ以上なら「自動（複数）」、
    config のコマンドが無ければ「自動」(job_platform_text と同じ)。
    """
    outcome = result.outcome
    if result.job.platform != AUTO_PLATFORM:
        return fallback
    if result.multi is not None:
        texts = list(dict.fromkeys(_auto_text(pair) for pair in _config_pairs(result)))
        if not texts:
            return job_platform_text(result.job)
        return texts[0] if len(texts) == 1 else BATCH_AUTO_PLATFORM_MIXED
    if outcome.kind != "config":
        return fallback
    return _auto_text(outcome)


def job_platform_text(job: Job) -> str:
    """組に書かれた機種の文(実行の前と実行中の「機種」の列)。窓の機種の選択肢の文にする(auto は「自動」)。

    値が無い組は config の機種の選択肢の先頭(指定なし。CLI と同じく指定なしで比べる)。選択肢に無い名前はそのまま出す。
    """
    if not job.platform:
        return CONFIG_PLATFORM_CHOICES[0][1]
    return next((label for value, label in WEB_PLATFORM_CHOICES if value == job.platform), job.platform)


def ai_point(llm: LlmAnalysis | None) -> str:
    """「AI の要点」の列。答えがあれば最初の文(first_sentence)、読めなければ・答えが無ければ llm_column の文。

    答えを読むだけで、AI を呼び直さない。答えの文字は変えない(保存する結果はそのまま)。
    """
    if llm is not None and llm.status == "done" and llm.answer:
        point = first_sentence(llm.answer)
        if point is not None:
            return point
    return llm_column(llm)


def table_rows(
    jobs: Iterable[Job], shown: Mapping[str, JobResult], running: str | None, excluded: Collection[str] = ()
) -> list[BatchTableRow]:
    """1 つの表の行(組の順)。shown は job の id ごとの表示する結果、running はいま実行中の job の id、excluded は外した組の id。

    外した組を先に見る(状態は「外した組」、数は —。結果は消さずに残し、戻すとまた出す)。次に実行中の組(2段目の AI の送信の間も
    「実行中」)。結果のある組は result_row の値、それ以外は「待機」と —。
    """
    rows = []
    for job in jobs:
        label, before, after = job.label or "", str(job.before.path), str(job.after.path)
        extra = {"job_id": job.id, "included": job.id not in excluded, "platform_index": job_platform_index(job)}
        result = shown.get(job.id)
        if job.id in excluded or job.id == running or result is None:
            if job.id in excluded:
                status = BATCH_JOB_EXCLUDED
            else:
                status = JOB_RUNNING if job.id == running else JOB_WAITING
            rows.append(
                BatchTableRow(
                    job.index,
                    label,
                    before,
                    after,
                    job_platform_text(job),
                    status,
                    NO_COUNT,
                    NO_COUNT,
                    NO_COUNT,
                    NO_COUNT,
                    False,
                    **extra,
                )
            )
            continue
        row = result_row(result)
        rows.append(
            BatchTableRow(
                job.index,
                label,
                before,
                after,
                _auto_row_platform(result, row.platform),
                row.status,
                row.structured,
                row.raw,
                row.unparsed,
                ai_point(result.outcome.llm),
                result.outcome.status == "difference",
                **extra,
            )
        )
    return rows


def table_titles(rows: Sequence[BatchTableRow], unpaired_count: int) -> dict[str, str]:
    """タブ「すべて (N)」「差分がある組 (N)」と、片方だけのファイルの見出し。どれも 0 件でも出す(ADR-0020)。"""
    return {
        "all": batch_all_tab_title(len(rows)),
        "diff": batch_diff_tab_title(sum(1 for row in rows if row.has_diff)),
        "unpaired": unpaired_tab_title(unpaired_count),
    }


def status_counts_text(counts: Mapping[str, int]) -> str:
    """状態ごとの件数。0 件の状態も出す(core の BatchSummary.counts は4キーを常に持つ)。終了コードは出さない。"""
    return " / ".join(f"{label} {counts.get(status, 0)}" for status, label in STATUS_LABEL.items())


def answer_lines(result: JobResult | None) -> list[str]:
    """行を選んだときに一覧の下に出す回答。CLI の text 出力の「== LLM 解析 ==」と同じ関数で作る。

    答えは <red>・</red> だけを外す(wx・pywebview の一括比較の窓。整えた表示はしない。REQ-204 判断の記録 1)。
    保存する結果(save_results)は AI が返したまま。
    """
    if result is None or result.outcome.llm is None or result.outcome.llm.reason == "disabled":
        return []
    return llm_section_lines(plain_llm(result.outcome.llm))


def save_results(results: Iterable[JobResult], target: str | Path) -> Path:
    """「結果を保存」。CLI の --output と同じ1件1行(AI の回答を含む)。上書きしない。件数だけのファイルは書かない。"""
    return write_job_lines(results, target)


def batch_report(results: Sequence[JobResult], words: Sequence[str]) -> Report:
    """一括比較の出力(ADR-0156 / REQ-216)。results は組の順(「結果を保存」と同じ。外した組は入れない)。

    組の名前は job_name、ファイル名は組ごとの変更前と変更後のファイル名だけ(どちらも判定に通す)。比較が失敗した組は状態だけ。
    上の要約は状態ごとの件数(status_counts_text。まとめて比べた組も 1 組)。全体の入力の名前は出さない(組ごとに持つ)。

    まとめて比べた組は、コマンドの組ごとに 1 組(名前は `<組の名前>: <コマンド>`、group は判定に通した組の名前。ADR-0178 決定3)。
    並びと AI の答えは保存と同じ(job_pair_results。全体の答えは最初のコマンドの組、ほかは「全体の答えに含む」)。相手なしの区間は入れない。
    """
    pairs: list[ReportPair] = []
    for result in results:
        job = result.job
        names = {
            "before_name": file_label(str(job.before.path), words),
            "after_name": file_label(str(job.after.path), words),
        }
        if result.multi is None:
            pairs.append(pair_report(job_name(job), result.outcome, words, **names))
            continue
        group = redact_text(job_name(job), words)
        for pair in job_pair_results(result.multi):
            shown = pair_report(multi_pair_name(job_name(job), pair.command), pair.outcome, words, **names)
            # コマンドは組の名前の形に頼らず別に持つ(出力の小見出しに出す。ADR-0190)
            command = redact_text(pair.command, words) if pair.command is not None else ""
            pairs.append(replace(shown, group=group, command=command))
    counts = {status: sum(1 for r in results if r.outcome.status == status) for status in STATUS_LABEL}
    # 件数の箱(REQ-243)は状態の文と同じ数から作る(STATUS_LABEL の順)。(鍵, 文, 数)。箱の色は鍵で選ぶ(ADR-0190)
    boxes = tuple((status, label, counts[status]) for status, label in STATUS_LABEL.items())
    return Report(EXPORT_TITLE_BATCH, status_counts_text(counts), "", tuple(pairs), True, status_counts=boxes)


# --- 選択待ちの理由(ADR-0178 決定5) ---


def waiting_reason(outcome: CompareOutcome, words: Sequence[str] | None) -> str | None:
    """選択待ちの組の、答えの欄に出す理由と次にすることの文。選択待ちでなければ None。

    種類の判定が割れた(ambiguous_format)なら BATCH_WAITING_FORMAT。区間が決まらない(segment)なら、候補のある側
    (両側にあれば変更前)の区間の数と、全部の区間のコマンドの名前(窓と同じ「(コマンド不明)」)で BATCH_WAITING_SEGMENT。
    名前は 1 つずつ秘密の語の判定(redact_text)に通す。words が None(語を読めなかった)なら名前は出さず BATCH_WAITING_NAMES_HIDDEN にする
    (伏せられないなら出さない)。
    """
    if outcome.status != "needs_selection":
        return None
    if outcome.selection_reason == "ambiguous_format":
        return BATCH_WAITING_FORMAT
    side = next((c.side for c in outcome.candidates if c.side == "before"), None)
    if side is None:
        side = outcome.candidates[0].side if outcome.candidates else "before"
    seg = outcome.segmentation
    if seg is not None:
        segments = list(seg[side].split.segments)
    else:
        segments = [c.segment for c in outcome.candidates if c.side == side]
    if words is None:
        return batch_waiting_segment(len(segments), [BATCH_WAITING_NAMES_HIDDEN])
    names = [redact_text(s.command if s.command is not None else UNKNOWN_COMMAND, words) for s in segments]
    return batch_waiting_segment(len(segments), names)


# --- 比較画面で開く(ADR-0029 決定8。入力だけを渡す) ---


@dataclass(frozen=True)
class CompareHandoff:
    """比較画面の入力欄に入れるもの。2つのパスと比較の設定だけで、結果は持たない。"""

    before: Path
    after: Path
    kind: KindChoice
    platform: str
    mode: Mode
    show_command: str
    # 単純比較の設定(REQ-228 §7.2)。比較の窓は受け取ったら欄をこの値にする(既定のファイルの値ではない)
    raw_options: RawOptions = DEFAULT_RAW_OPTIONS


def handoff_for(job: Job) -> CompareHandoff:
    return CompareHandoff(
        before=job.before.path,
        after=job.after.path,
        kind=job.kind,
        platform=job.platform or "",
        mode=job.mode,
        show_command=job.show_command or "",
        raw_options=job.raw_options,
    )


@dataclass(frozen=True)
class HandedAnswer:
    """一括比較の組の AI の答え(ADR-0170)。比較の窓が比べ直して、差分の本文が同じときだけ使う。

    pywebview の一括比較の窓だけが作る(wx の「比較画面で開く」は CompareHandoff だけを渡す。ADR-0153 決定11)。
    """

    llm: LlmAnalysis
    profile: tuple[str, str]  # 送り先の AI プロファイル (id, 名前)
    prompt_name: str | None


def handed_answer(
    llm: LlmAnalysis | None, source: tuple[tuple[str, str], str | None] | None
) -> HandedAnswer | None:
    """送って答えが届いた答え(status が done・transmitted・payload と answer が文字・max_diffs が整数)で、送り先の記録があるときだけ。

    source は (送り先の AI プロファイル (id, 名前), プロンプトの名前)。どちらかが無ければ None(答えは渡さない。ADR-0170 決定1)。
    """
    if source is None or llm is None:
        return None
    if llm.status != "done" or not llm.transmitted:
        return None
    if not isinstance(llm.payload, str) or not isinstance(llm.answer, str):
        return None
    if type(llm.max_diffs) is not int:
        return None
    profile, prompt_name = source
    return HandedAnswer(llm=llm, profile=profile, prompt_name=prompt_name)


# --- LLM の段取り(ADR-0031) ---


@dataclass(frozen=True)
class FirstStage:
    """1段目の後にどうするか。final が None なら確認の画面を出す(preview がある)。message は画面に出す一言。"""

    preview: BatchLlmPreview | None
    final: list[JobResult] | None
    message: str | None = None


def limit_lines(settings: LlmSettings) -> list[str]:
    """今の上限と出どころ、変えられる場所の案内(ADR-0071 決定5。送る前の確認と、上限を超えたときの文に出す)。"""
    return llm_limit_lines(
        settings.max_diffs, settings.max_diffs_source, settings.max_total_diffs, settings.max_total_diffs_source
    )


def after_first_stage(
    plan: LlmPlan,
    results: Sequence[JobResult],
    *,
    cancelled: bool,
    max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS,
    limits: Sequence[str] = (),
) -> FirstStage:
    """全体の上限は確認の画面を出す前に判定する。超えたら1件も送らない(打ち切って一部だけ送ることはしない)。

    max_total_diffs は設定の値(LlmSettings.max_total_diffs)。limits は上限を超えたときの文に添える行(limit_lines)。
    """
    if plan.send is None:
        return FirstStage(None, list(results))  # client を作れなかった。各行の llm に送れなかった理由が残っている
    if cancelled:
        return FirstStage(None, final_results(results, reason="cancelled"))
    pv = llm_preview(results, max_total_diffs=max_total_diffs)
    if pv.over_limit:
        message = "\n".join([batch_llm_over_limit(pv.total_diffs, pv.max_total_diffs), *limits])
        return FirstStage(pv, final_results(results, reason="over_total_limit"), message)
    if not pv.jobs:
        return FirstStage(pv, final_results(results), batch_llm_nothing_to_send())
    return FirstStage(pv, None)


def second_stage(preview: BatchLlmPreview, checked: Collection[str]) -> dict[str, str]:
    """確認でチェックを残した組の「id → 承認した送信内容」。空なら送らない。"""
    return approved_payloads(preview, checked)


def declined(results: Sequence[JobResult]) -> list[JobResult]:
    """確認で「送らない」を選んだ。比較の結果だけが残る(送る内容があった組は「送らなかった」)。"""
    return final_results(results, reason="not_approved")


def finish(first: Sequence[JobResult], second: Sequence[JobResult], approved: Collection[str]) -> list[JobResult]:
    """1段目と2段目を job の順に並べる。チェックを外した組は「送らなかった」、2段目を中止した残りは cancelled。"""
    return final_results(first, second, approved=approved)


@dataclass(frozen=True)
class ApprovalRow:
    job_id: str
    name: str
    sent: int
    masked: int
    unsent: int


@dataclass(frozen=True)
class ApprovalView:
    """確認の画面。要約を上、1行1組の一覧を下。全文は画面に出さない(ファイルに書き出して見る)。

    limits は要約の下に出す、今の上限と出どころ・変えられる場所の案内(ADR-0071 決定5)。
    """

    summary: list[str]
    rows: list[ApprovalRow]
    limits: list[str] = field(default_factory=list)


def effective_base_url(settings: LlmSettings) -> str:
    return settings.base_url or smb_default_url(default_base_url(settings.api_format))


def approval_view(preview: BatchLlmPreview, settings: LlmSettings) -> ApprovalView:
    """差分が無い組・比較まで進まなかった組は出さない(preview.jobs は送る内容がある組だけ)。"""
    # CLI の形式でモデルが無いときは「CLI の既定のモデル」(REQ-102)
    summary = batch_llm_preview_lines(preview, model=display_model(settings), base_url=effective_base_url(settings))
    rows = [
        ApprovalRow(j.job_id, f"{j.job_id}  {j.label}" if j.label else j.job_id, j.sent_diffs, j.masked, j.unsent_lines)
        for j in preview.jobs
    ]
    return ApprovalView(summary, rows, limit_lines(settings))


def save_payloads(preview: BatchLlmPreview, target: str | Path) -> Path:
    """「内容をファイルに保存」。送る内容(マスク後)の全文。上書きしない。"""
    return write_text_file(batch_llm_payloads_text(preview), target)
