"""一括比較(ADR-0016 / REQ-15)。マニフェストの解釈と、job ごとに run_compare を呼ぶだけの層。

比較の中身を知らない(決定1)。ファイルを開かない(ADR-0015 決定5)。マニフェストの読み込み、入力の読み込み、
結果の書き出しは入口の責務で、ここは解釈済みのデータと、入口が渡す prepare / on_result を受け取る。
並列化はしない(決定7)。キーと既定値の一覧は docs/notes/req-15-batch.md。

LLM 解析(ADR-0031)は実行時の指定だけで、マニフェストには書けない(決定6)。入口が作った LlmRequest を1つ受け取り、
全 job で使い回す。2段目(送る段)では、job ごとに確認で承認した内容だけを送る包みを被せる。要約と承認の段取りは core.batch_llm。
"""

import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from driftpilot.config_parser.family_hint import AUTO_PLATFORM
from driftpilot.config_parser.platforms import CONFIG_PLATFORM_NAMES
from driftpilot.core.compare import run_compare
from driftpilot.core.types import (
    SIDES,
    CompareError,
    CompareOutcome,
    CompareRequest,
    KindChoice,
    Mode,
    SideInput,
    SideName,
    SideSelection,
    Status,
)
from driftpilot.diff_engine import NormalizationRule
from driftpilot.llm_analyzer import ApprovedPromptClient, LlmAnalysis, LlmRequest
from driftpilot.raw_keys import DEFAULT_RAW_OPTIONS, RawOptions, RawOptionsError, options_from_plain
from driftpilot.segmentation import InputNote

if TYPE_CHECKING:
    # core.compare_all はこのモジュールの _LlmPerJob を読むので、実行時は関数の中で読む(循環を避ける)
    from driftpilot.core.compare_all import MultiCompareOutcome

# マニフェストの版。結果の SCHEMA_VERSION とは別に上げ下げする
MANIFEST_SCHEMA_VERSION = "1"

# API の失敗がこの件数だけ続いたら、残りの job では LLM を呼ばない(ADR-0031 決定7)
LLM_GIVE_UP_AFTER = 3
_LLM_API_FAILURES = ("connection_error", "api_error")

STATUSES: tuple[Status, ...] = ("no_difference", "difference", "needs_selection", "error")
# 重い順(決定6)。見落としを防ぐため、重い状態を全体の状態にする
_PRIORITY: tuple[Status, ...] = ("error", "needs_selection", "difference", "no_difference")

SelectionSource = Literal["job", "defaults"]

MODES = ("structured", "raw", "both")
KINDS = ("config", "show", "auto")
DEFAULT_MODE: Mode = "both"
DEFAULT_KIND: KindChoice = "config"

_TOP_KEYS = ("schema_version", "defaults", "jobs")
_SCALAR_KEYS = ("mode", "kind", "platform", "show_command", "prompt", "rules", "enable_rule")
# 単純比較の設定(REQ-228 §7.1)。中身は options_to_plain の形(context_lines は書けない)。組に書けば defaults のものを丸ごと置き換える
RAW_OPTIONS_KEY = "raw_options"
_DEFAULTS_KEYS = (*_SCALAR_KEYS, "before", "after", RAW_OPTIONS_KEY)
_JOB_KEYS = ("id", "label", *_SCALAR_KEYS, "before", "after", RAW_OPTIONS_KEY)
_SELECTION_KEYS = ("command", "segment")
# job ごとに違うもの。defaults に書いても共通にする意味がない
_JOB_ONLY_KEYS = ("id", "label")


@dataclass(frozen=True)
class SideSpec:
    """job の片側。raw_path はマニフェストに書かれたまま、path は基準の位置で解決した絶対パス。

    selection_source は区間指定の出どころ(job / defaults)。指定が無い(自動)ときは None。
    """

    raw_path: str
    path: Path
    selection: SideSelection
    selection_source: SelectionSource | None


@dataclass(frozen=True)
class Job:
    id: str
    label: str | None
    index: int  # 1始まり。マニフェストに書かれた順
    before: SideSpec
    after: SideSpec
    mode: Mode = DEFAULT_MODE
    kind: KindChoice = DEFAULT_KIND
    platform: str | None = None
    show_command: str | None = None
    prompt: str | None = None
    rules_path: Path | None = None
    enabled_rule_ids: tuple[str, ...] | None = None
    # 単純比較の設定(REQ-228 §7.1)。組の raw_options、無ければ defaults の raw_options、それも無ければ既定
    raw_options: RawOptions = DEFAULT_RAW_OPTIONS


@dataclass(frozen=True)
class Manifest:
    schema_version: str
    jobs: tuple[Job, ...]


@dataclass(frozen=True)
class BatchProgress:
    """job 単位の進捗。index は1始まり(何件目/全何件)。"""

    index: int
    total: int
    job_id: str
    label: str | None


@dataclass(frozen=True)
class JobResult:
    """1 job の結果。unread_sides は入力を読めなかった側(request のその側のテキストは空)。

    multi は、区間の指定が無く区間が 2 つ以上ある組をまとめて比べた結果(compare_all。ADR-0178 決定1)。1 区間の組は None。
    まとめて比べた組の outcome は組の全体を表すだけの CompareOutcome(whole_outcome)で、status は multi.status。
    種類・区間・差分の結果は持たない(outcome だけを見る処理が 1 区間の比較の結果と取り違えないため)。
    AI の要求があれば、組の全体の解析(全部のコマンドの組を 1 つの文にした解析)が outcome.llm と multi.llm の両方に入る
    (ADR-0178 決定4・REQ-241 §2)。
    """

    job: Job
    request: CompareRequest
    outcome: CompareOutcome
    unread_sides: tuple[SideName, ...] = ()
    multi: "MultiCompareOutcome | None" = None


@dataclass(frozen=True)
class BatchSummary:
    """件数の内訳と所要時間。文章化は入口の責務(ADR-0015 決定4)。

    counts は STATUSES の4キーを常に持つ(0件も書く)。executed は結果を返し終えた件数。
    cancelled は should_cancel で job の境界で止めたこと、interrupted は KeyboardInterrupt で止まったこと。
    """

    total: int
    executed: int
    counts: dict[Status, int]
    elapsed_seconds: float
    cancelled: bool
    interrupted: bool
    overall_status: Status


class ManifestError(ValueError):
    """マニフェストとして成立していない。1件も実行しない(決定5)。problems は (場所, 内容) の一覧。"""

    def __init__(self, problems: Sequence[tuple[str, str]]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(f"{where}: {message}" for where, message in self.problems))


class JobInputError(Exception):
    """prepare が入力を読めなかったときに送出する。request は読めなかった側のテキストを空にしたもの。"""

    def __init__(self, sides: Sequence[SideName], message: str, request: CompareRequest) -> None:
        self.sides = tuple(sides)
        self.message = message
        self.request = request
        super().__init__(message)


class BatchInterrupted(Exception):
    """KeyboardInterrupt で止まった。summary は結果を返し終えた job までの集計。"""

    def __init__(self, summary: BatchSummary) -> None:
        self.summary = summary
        super().__init__(f"{summary.executed}/{summary.total} 件で中断しました")


# --- 全体の状態(決定6) ---


def overall_status(counts: Mapping[str, int]) -> Status:
    """error > needs_selection > difference > no_difference。1件も無ければ no_difference。"""
    for status in _PRIORITY:
        if counts.get(status, 0) > 0:
            return status
    return "no_difference"


# --- マニフェストの解釈(決定2) ---


class _Problems:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def add(self, where: str, message: str) -> None:
        self.items.append((where, message))


def parse_manifest(data: Any, *, base_dir: Path | None) -> Manifest:
    """解釈済みの YAML(dict)を検査して Manifest にする。違反はすべて集めて ManifestError で送出する。

    相対パスは base_dir(マニフェストファイルのあるディレクトリ)を基準に解決する。base_dir が None
    (GUI が画面で組んだものなど、基準の位置が無い)のとき、相対パスはエラーにする。
    """
    if not isinstance(data, dict):
        raise ManifestError([("(最上位)", "マッピング(schema_version / defaults / jobs)にしてください")])
    found = data.get("schema_version")
    if type(found) is not str or found != MANIFEST_SCHEMA_VERSION:
        # 版が違えば他のキーの意味も違いうるため、ほかの検査はしない
        shown = "(なし)" if "schema_version" not in data else repr(found)
        raise ManifestError(
            [
                (
                    "schema_version",
                    f"組のファイルの版が違います({shown}、読める版: {MANIFEST_SCHEMA_VERSION!r})。"
                    f'文字列で schema_version: "{MANIFEST_SCHEMA_VERSION}" と書いてください',
                )
            ]
        )
    problems = _Problems()
    _unknown_keys(data, _TOP_KEYS, "", problems)

    defaults = data.get("defaults")
    if defaults is None:
        defaults = {}
    elif not isinstance(defaults, dict):
        problems.add("defaults", "マッピングにしてください")
        defaults = {}
    else:
        defaults = _check_defaults(defaults, problems)

    jobs_data = data.get("jobs")
    jobs: list[Job] = []
    if not isinstance(jobs_data, list) or not jobs_data:
        problems.add("jobs", "比較の組(job)を1件以上、リストで書いてください")
    else:
        seen: dict[str, int] = {}
        for index, raw in enumerate(jobs_data, start=1):
            # 重複は、他の項目に誤りがある job も含めて調べる(直したあとで重複に気づく、を避ける)
            job_id = _job_id(raw, index)
            if job_id is not None:
                if job_id in seen:
                    problems.add(f"jobs[{index}].id", f"id {job_id!r} が jobs[{seen[job_id]}] と重複しています")
                else:
                    seen[job_id] = index
            job = _parse_job(raw, index, defaults, base_dir, problems)
            if job is not None:
                jobs.append(job)
    if problems.items:
        raise ManifestError(problems.items)
    return Manifest(found, tuple(jobs))


def _unknown_keys(data: dict, allowed: Sequence[str], where: str, problems: _Problems) -> None:
    for key in data:
        if key not in allowed:
            name = f"{where}.{key}" if where else str(key)
            problems.add(name, f"知らないキーです(書けるキー: {', '.join(allowed)})")


def _present(data: dict, key: str) -> bool:
    """値が null のキーは書いていないものとして扱う。"""
    return data.get(key) is not None


def _check_defaults(defaults: dict, problems: _Problems) -> dict:
    for key in defaults:
        if key in _JOB_ONLY_KEYS:
            problems.add(f"defaults.{key}", "job ごとに書いてください(defaults には書けません)")
        elif key not in _DEFAULTS_KEYS:
            problems.add(f"defaults.{key}", f"知らないキーです(書けるキー: {', '.join(_DEFAULTS_KEYS)})")
    checked = {k: v for k, v in defaults.items() if k in _DEFAULTS_KEYS and v is not None}
    for key in _SCALAR_KEYS:
        if key in checked and not _check_scalar(key, checked[key], f"defaults.{key}", problems):
            del checked[key]
    if RAW_OPTIONS_KEY in checked:
        options = _check_raw_options(checked[RAW_OPTIONS_KEY], f"defaults.{RAW_OPTIONS_KEY}", problems)
        if options is None:
            del checked[RAW_OPTIONS_KEY]
        else:
            checked[RAW_OPTIONS_KEY] = options
    # 片方だけを許すと、全 job が同じ「両方指定してください」で一斉に落ちる。
    # ただし config の系統の名前と機種の自動(auto)は show_command と対にしない(core と同じ規則。kind では分けない。
    # ADR-0027・ADR-0151 決定4)
    platform = defaults.get("platform")
    flat_name = isinstance(platform, str) and (platform in CONFIG_PLATFORM_NAMES or platform == AUTO_PLATFORM)
    if not flat_name and _present(defaults, "platform") != _present(defaults, "show_command"):
        problems.add("defaults", "platform と show_command は両方セットで書いてください(片方だけは書けません)")
    for side in SIDES:
        if side not in checked:
            continue
        value = checked[side]
        where = f"defaults.{side}"
        if not isinstance(value, dict):
            problems.add(where, "マッピング(command か segment)にしてください")
            del checked[side]
            continue
        for key in value:
            if key == "path":
                problems.add(f"{where}.path", "path は job ごとに書いてください(defaults には書けません)")
            elif key not in _SELECTION_KEYS:
                problems.add(f"{where}.{key}", f"知らないキーです(書けるキー: {', '.join(_SELECTION_KEYS)})")
        selection = _check_selection(value, where, problems)
        if selection is None:
            del checked[side]
        else:
            checked[side] = selection
    return checked


def _check_scalar(key: str, value: Any, where: str, problems: _Problems) -> bool:
    if key == "mode":
        ok = value in MODES and isinstance(value, str)
        if not ok:
            problems.add(where, f"{value!r} は使えません({' / '.join(MODES)})")
        return ok
    if key == "kind":
        ok = value in KINDS and isinstance(value, str)
        if not ok:
            problems.add(where, f"{value!r} は使えません({' / '.join(KINDS)})")
        return ok
    if key == "enable_rule":
        ok = isinstance(value, list) and all(isinstance(v, str) and v for v in value)
        if not ok:
            problems.add(where, "ルール ID(文字列)のリストにしてください")
        return ok
    if not isinstance(value, str) or not value:
        problems.add(where, "空でない文字列にしてください")
        return False
    return True


def _check_raw_options(value: Any, where: str, problems: _Problems) -> RawOptions | None:
    """raw_options を options_from_plain で確かめる。誤りは 1 件ずつ(文に式は入れない)。使えなければ None。"""
    try:
        return options_from_plain(value)
    except RawOptionsError as exc:
        if not exc.problems:
            problems.add(where, str(exc))  # 形の誤り(場所と種類だけ)
            return None
        # messages は driftpilot.core を import するので、循環を避けるため呼ぶときに読む
        from driftpilot.messages import raw_option_problem_lines

        for line in raw_option_problem_lines(exc.problems):
            problems.add(where, line)
        return None


def _check_selection(side: dict, where: str, problems: _Problems) -> SideSelection | None:
    """command / segment を検査する。両方なら None(エラー)。どちらも無ければ空の SideSelection。"""
    ok = True
    command = side.get("command")
    segment = side.get("segment")
    if command is not None and (not isinstance(command, str) or not command):
        problems.add(f"{where}.command", "空でない文字列にしてください")
        ok = False
    if segment is not None and (type(segment) is not int or segment < 1):
        problems.add(f"{where}.segment", "1 以上の整数にしてください")
        ok = False
    if command is not None and segment is not None:
        problems.add(where, "区間の指定は command と segment のどちらか一方にしてください")
        ok = False
    return SideSelection(command, segment) if ok else None


def _resolve(raw: str, base_dir: Path | None, where: str, problems: _Problems) -> Path | None:
    path = Path(raw)
    if path.is_absolute():
        return path
    if base_dir is None:
        problems.add(where, f"相対パス {raw!r} は使えません(基準になる組のファイルの位置がありません)。絶対パスにしてください")
        return None
    return (base_dir / path).absolute()


def _parse_side(
    raw: Any, side: SideName, defaults: dict, base_dir: Path | None, where: str, problems: _Problems
) -> SideSpec | None:
    if not isinstance(raw, dict):
        problems.add(where, "マッピング(path と、必要なら command か segment)にしてください")
        return None
    _unknown_keys(raw, ("path", *_SELECTION_KEYS), where, problems)
    raw_path = raw.get("path")
    path = None
    if not isinstance(raw_path, str) or not raw_path:
        problems.add(f"{where}.path", "入力ファイルのパスを書いてください")
    else:
        path = _resolve(raw_path, base_dir, f"{where}.path", problems)
    selection = _check_selection(raw, where, problems)
    if path is None or selection is None:
        return None
    # job 側に区間指定があれば、その側の defaults の指定は丸ごと置き換える(混ぜない)
    if selection.command is not None or selection.number is not None:
        return SideSpec(raw_path, path, selection, "job")
    inherited = defaults.get(side)
    if inherited is not None and (inherited.command is not None or inherited.number is not None):
        return SideSpec(raw_path, path, inherited, "defaults")
    return SideSpec(raw_path, path, SideSelection(), None)


def _job_id(raw: Any, index: int) -> str | None:
    """job の id。省略時は位置(1始まり)の文字列。id として使えない値なら None。"""
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id")
    if job_id is None:
        return str(index)
    return job_id if isinstance(job_id, str) and job_id else None


def _parse_job(raw: Any, index: int, defaults: dict, base_dir: Path | None, problems: _Problems) -> Job | None:
    where = f"jobs[{index}]"
    if not isinstance(raw, dict):
        problems.add(where, "マッピングにしてください")
        return None
    _unknown_keys(raw, _JOB_KEYS, where, problems)
    ok = True
    job_id = _job_id(raw, index)
    if job_id is None:
        job_id = raw.get("id")
        hint = f'(数字だけの id は引用符で囲んでください: id: "{job_id}")' if isinstance(job_id, (int, float)) else ""
        problems.add(f"{where}.id", f"空でない文字列にしてください{hint}")
        ok = False
    label = raw.get("label")
    if label is not None and not isinstance(label, str):
        problems.add(f"{where}.label", "文字列にしてください")
        ok = False
    values: dict[str, Any] = {}
    for key in _SCALAR_KEYS:
        if _present(raw, key):
            if _check_scalar(key, raw[key], f"{where}.{key}", problems):
                values[key] = raw[key]
            else:
                ok = False
        elif key in defaults:
            values[key] = defaults[key]
    raw_options = defaults.get(RAW_OPTIONS_KEY, DEFAULT_RAW_OPTIONS)
    if _present(raw, RAW_OPTIONS_KEY):
        # 組に書けば defaults のものを丸ごと置き換える(キーごとに混ぜない)
        found = _check_raw_options(raw[RAW_OPTIONS_KEY], f"{where}.{RAW_OPTIONS_KEY}", problems)
        if found is None:
            ok = False
        else:
            raw_options = found
    sides: dict[str, SideSpec | None] = {}
    for side in SIDES:
        if side not in raw or raw[side] is None:
            problems.add(f"{where}.{side}", "書いてください(path は必須です)")
            sides[side] = None
        else:
            sides[side] = _parse_side(raw[side], side, defaults, base_dir, f"{where}.{side}", problems)
    rules_path = None
    if "rules" in values:
        rules_path = _resolve(values["rules"], base_dir, f"{where}.rules", problems)
        ok = ok and rules_path is not None
    if not ok or sides["before"] is None or sides["after"] is None:
        return None
    enabled = values.get("enable_rule")
    return Job(
        id=job_id,
        label=label,
        index=index,
        before=sides["before"],
        after=sides["after"],
        mode=values.get("mode", DEFAULT_MODE),
        kind=values.get("kind", DEFAULT_KIND),
        platform=values.get("platform"),
        show_command=values.get("show_command"),
        prompt=values.get("prompt"),
        rules_path=rules_path,
        enabled_rule_ids=tuple(enabled) if enabled is not None else None,
        raw_options=raw_options,
    )


# --- 実行(決定1・5・8) ---


def request_for(
    job: Job,
    before_text: str,
    after_text: str,
    rules: list[NormalizationRule] | None = None,
    *,
    before_notes: tuple[InputNote, ...] = (),
    after_notes: tuple[InputNote, ...] = (),
) -> CompareRequest:
    """job と読み込み済みのテキストから、1件の比較の要求を作る。表示名はマニフェストに書かれたパス。

    notes は読み込みで入力の形を戻した知らせ(ADR-0138 決定7)。
    """
    return CompareRequest(
        before=SideInput(before_text, job.before.raw_path, before_notes),
        after=SideInput(after_text, job.after.raw_path, after_notes),
        mode=job.mode,
        kind=job.kind,
        platform=job.platform,
        show_command=job.show_command,
        prompt=job.prompt,
        before_selection=job.before.selection,
        after_selection=job.after.selection,
        rules=rules,
        enabled_rule_ids=list(job.enabled_rule_ids) if job.enabled_rule_ids is not None else None,
        raw_options=job.raw_options,
    )


def run_batch(
    jobs: Sequence[Job],
    *,
    prepare: Callable[[Job], CompareRequest],
    on_result: Callable[[JobResult], None],
    on_progress: Callable[[BatchProgress], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
    llm: LlmRequest | None = None,
    approved: Mapping[str, str] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> BatchSummary:
    """job を順に実行し、1件終わるたびに on_result を呼ぶ。結果は保持せず、件数だけ数える。

    llm は入口が組み立てた LLM 解析の要求(ADR-0031)。1つを全 job で使い回す(client を作り直さない)。
    送る要求では、比較が失敗した job には送らない(llm に compare_failed が残る)。
    None なら run_compare に渡さない(今までどおり)。approved は送る段で使う「job の id → 確認で承認した送信内容」で、
    渡したときは承認した内容だけを送る(違えば送らない。書かれていない job は送らない)。
    API の失敗が LLM_GIVE_UP_AFTER 件続いたら、残りの job では LLM を呼ばない(比較は続ける。status は変えない)。

    1件が失敗しても残りを続ける(status=error の結果として返す)。should_cancel は各 job の前にだけ確かめ、
    真なら残りを実行せずに cancelled=True で返す。job の途中では止めない(run_compare には渡さない)。
    KeyboardInterrupt は、それまでの集計を持たせた BatchInterrupted にして送出する。

    件数は on_result が返った結果を数える(on_result が返った = 入口が結果を書き終えた、とする)。
    そのため入口は、on_result の中で結果を書き始めた後や、on_result が返った後に KeyboardInterrupt を起こさないこと。
    起こすと、書いた行が集計と終了コードから漏れる。CLI は、比較の途中でだけ2回目の Ctrl+C で止め、
    結果の書き出し中に受けたときは、書き終えてから次の job の境界で止める。
    """
    total = len(jobs)
    counts: dict[Status, int] = dict.fromkeys(STATUSES, 0)
    executed = 0
    cancelled = False
    llm_per_job = _LlmPerJob(llm, approved)
    start = clock()

    def summary(interrupted: bool = False) -> BatchSummary:
        return BatchSummary(
            total=total,
            executed=executed,
            counts=dict(counts),
            elapsed_seconds=clock() - start,
            cancelled=cancelled,
            interrupted=interrupted,
            overall_status=overall_status(counts),
        )

    try:
        for index, job in enumerate(jobs, start=1):
            if should_cancel is not None and should_cancel():
                cancelled = True
                break
            if on_progress is not None:
                on_progress(BatchProgress(index, total, job.id, job.label))
            result = _run_one(job, prepare, llm_per_job.request(job.id))
            llm_per_job.record(result.outcome.llm)
            on_result(result)
            counts[result.outcome.status] += 1
            executed += 1
    except KeyboardInterrupt as exc:
        raise BatchInterrupted(summary(interrupted=True)) from exc
    return summary()


class _LlmPerJob:
    """job ごとの LLM の要求。入口の要求を使い回し、承認と連続失敗に応じて「送らない」理由に差し替える。

    送る要求には skip_failed を付ける。比較が失敗した組は、承認した内容と同じでも送らない(REQ-27 §A-8b。
    1段目で成功して承認された組が、2段目の比較し直しで失敗したときも送らない)。client は作り直さない。
    まとめての比較(core.compare_all。REQ-122)も、組の番号の文字列を id にして同じものを使う。
    """

    def __init__(self, llm: LlmRequest | None, approved: Mapping[str, str] | None) -> None:
        if llm is not None and llm.client is not None and not llm.dry_run and not llm.skip_failed:
            llm = replace(llm, skip_failed=True)
        self.llm = llm
        self.approved = approved
        self.failures = 0

    def request(self, job_id: str) -> LlmRequest | None:
        llm = self.llm
        if llm is None or llm.dry_run or llm.client is None:
            return llm  # 送らない要求(確認だけ・client を作れなかった)はそのまま使い回す
        payload = None
        if self.approved is not None:
            payload = self.approved.get(job_id)
            if payload is None:
                return replace(llm, client=None, unavailable="not_approved")
        if self.failures >= LLM_GIVE_UP_AFTER:
            return replace(llm, client=None, unavailable="gave_up")
        if payload is not None:
            return replace(llm, client=ApprovedPromptClient(llm.client, payload))
        return llm

    def record(self, analysis: LlmAnalysis | None) -> None:
        """API を呼んだ job だけを数える。成功で数え直す。呼ばなかった job(差分なし・送らない・比較の失敗)は数えない。"""
        if analysis is None or not analysis.transmitted:
            return
        if analysis.status == "done":
            self.failures = 0
        elif analysis.reason in _LLM_API_FAILURES:
            self.failures += 1


def _run_one(job: Job, prepare: Callable[[Job], CompareRequest], llm: LlmRequest | None = None) -> JobResult:
    stages: list[str] = []
    request: CompareRequest | None = None
    # llm が無いときは run_compare に渡さない(渡すのは on_progress だけ。should_cancel は渡さない)
    options: dict[str, Any] = {"llm": llm} if llm is not None else {}
    try:
        request = prepare(job)
        # 区間の指定が無く区間が 2 つ以上あれば、比較の窓と同じ部品でまとめて比べる(ADR-0178 決定1)
        from driftpilot.core.compare_all import analyze_all, compare_all, wants_all

        if wants_all(request):
            # 中止は job の境界でだけ確かめる(ADR-0016 決定8)ので should_cancel は渡さない
            multi = compare_all(request, on_progress=stages.append)
            if llm is not None:
                # 組ごとに全体の送る文を 1 つ作って 1 回で送る(比較の窓の analyze_all と同じ作り方。ADR-0178 決定4・REQ-241 §2)。
                # ★ 1 組の中では全体の上限を見ない(sys.maxsize)。全体の上限は一括比較の全部の組の合計で llm_preview が判定し、
                # 超えたら 1 件も送らない(ADR-0031 決定4)。上限は送る文の中身を変えないので、比較の窓と同じ文になる
                stages.append("llm")
                multi = analyze_all(multi, llm, max_total_diffs=sys.maxsize)
            # 全体の答えは multi.llm と outcome.llm の両方に入る(要約・承認・照合・連続失敗の数え方が組の単位でそのまま効く)
            return JobResult(job, request, whole_outcome(multi, request, multi.llm), multi=multi)
        return JobResult(job, request, run_compare(request, on_progress=stages.append, **options))
    except JobInputError as exc:
        error = CompareError("input_error", "read_input", exc.message, {"stage": "read_input", "sides": list(exc.sides)})
        return JobResult(job, exc.request, _error_outcome(job, error), exc.sides)
    except Exception as exc:  # noqa: BLE001 - 1件の失敗で全体を止めない(決定5)
        stage = stages[-1] if stages else "read_input"
        detail: dict[str, Any] = {"stage": stage, "error_type": type(exc).__name__, "error": str(exc)}
        # prepare の中で失敗したときは、入力を両側とも読めていない。読めなかった側を detail.sides に書く
        unread = SIDES if request is None else ()
        if unread:
            detail["sides"] = list(unread)
        message = f"予期しないエラーで比較できませんでした: {type(exc).__name__}: {exc}"
        error = CompareError("unexpected_error", stage, message, detail)
        return JobResult(job, request or request_for(job, "", ""), _error_outcome(job, error), unread)


def _error_outcome(job: Job, error: CompareError) -> CompareOutcome:
    return CompareOutcome(status="error", mode=job.mode, error=error)


def whole_outcome(multi: "MultiCompareOutcome", request: CompareRequest, llm: LlmAnalysis | None = None) -> CompareOutcome:
    """まとめて比べた組の全体を表す CompareOutcome(REQ-240 §2)。

    status は multi.status、has_diff は差分ありのときだけ真。種類・区間・差分の結果・エラー・機種の判定は持たない(None)。
    outcome だけを見る今の処理(件数の列・送る差分・機種の列)は、1 区間の比較の結果と取り違えない(件数は無し・送る差分は無し)。
    llm は組の全体の解析(multi.llm と同じもの。REQ-241 §2)。
    """
    return CompareOutcome(status=multi.status, mode=request.mode, has_diff=multi.status == "difference", llm=llm)
