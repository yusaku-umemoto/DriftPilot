"""一括比較の窓(pywebview)で組を直す論理(REQ-212 / ADR-0153 決定4〜8)。wx を読まない。gui_web を読まない。

窓はマニフェストと同じ形のデータ(schema_version / defaults / jobs の dict)を持ち、直すたびに parse_manifest と
load_job_rules で検査する(断られたら直す前のまま。ManifestError は呼び出し側で知らせにする)。
パスはすべて絶対パスで持ち、保存のときだけ書く位置からの相対パスにする。開いた組のファイルへは書き戻さない
(保存は write_manifest で新しいファイルへ。上書きしない)。

- 組を外す(excluded): 実行しない・保存に書かない。to_prepared の jobs には外した組も入れる(表と「比較画面で開く」のため)
- 機種の自動(auto_platform): 組ごとの印。data の job には platform を書かず、to_prepared で Job.platform を auto にする。
  判定は実行のときに比較の本体(run_compare)が REQ-209 の決まりのまま行う(判断の記録 1)。保存のときだけ、
  ファイルを読んで choose_platform で判定し、決まった機種名を書く(Cisco 系・判定できない組は書かない)
- 片方だけのファイル(only_before / only_after)を人の操作で組にする・解除する。hostname の候補は読むだけで組は変えない
- AI に組の案を聞くときに送るファイル名を決める(pair_ai_targets。REQ-219 / ADR-0159)。名前だけで、ファイルは読まない
- ★ LLM の指定は書けない(ADR-0031 決定6)。直せるキーは EDITABLE_JOB_KEYS だけ(_JOB_KEYS は変えない)
"""

import copy
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from driftpilot.batch_io import (
    Pairing,
    load_job_rules,
    load_manifest,
    load_manifest_data,
    manifest_path,
    pairing_manifest,
    write_manifest,
)
from driftpilot.core import (
    AUTO_PLATFORM,
    KINDS,
    MODES,
    AutoPlatform,
    CompareRequest,
    Job,
    ManifestError,
    choose_platform,
    parse_manifest,
)
from driftpilot.llm_analyzer import file_name_refused
from driftpilot.messages import smb_unknown_key
from driftpilot.messages import (
    BATCH_ALL_SETTINGS_TITLE,
    BATCH_NOTE_RAW_OPTIONS,
    BATCH_RAW_NONE,
    BATCH_COL_AFTER,
    BATCH_COL_BEFORE,
    BATCH_SETTINGS_ALL_NOTE,
    BATCH_SETTINGS_APPLY,
    BATCH_SETTINGS_ENABLE_RULE_NOTE,
    BATCH_SETTINGS_FIELD_AFTER,
    BATCH_SETTINGS_FIELD_BEFORE,
    BATCH_SETTINGS_FIELD_ENABLE_RULE,
    BATCH_SETTINGS_FIELD_ID,
    BATCH_SETTINGS_FIELD_LABEL,
    BATCH_SETTINGS_FIELD_PROMPT,
    BATCH_SETTINGS_FIELD_RULES,
    BATCH_SETTINGS_FIELD_SHOW_COMMAND,
    BATCH_SETTINGS_KEEP_DEFAULT,
    BATCH_SETTINGS_KEEP_PAIRS,
    BATCH_SETTINGS_PAIR_NOTE,
    BATCH_SETTINGS_PROMPT_NOTE,
    BATCH_SETTINGS_SELECTION_CHOICES,
    COMPARE_KIND,
    COMPARE_MODE,
    COMPARE_PLATFORM,
    DIALOG_CANCEL,
    PICK_BUTTON,
    UNPAIRED_SIDE_LABEL,
    batch_pair_settings_title,
    batch_raw_pairs_suffix,
)
from driftpilot.screen_model.batch_model import PreparedBatch, UnpairedRow, pair_rows, platform_undetected
from driftpilot.screen_model.model import KIND_CHOICES, MODE_CHOICES, WEB_PLATFORM_CHOICES, raw_options_summary
from driftpilot.core import DEFAULT_RAW_OPTIONS, RawOptions, RawOptionsError, options_from_plain
from driftpilot.segmentation.input_forms import InputDecodeError, read_config_input

SIDE_KEYS = ("before", "after")
# 窓で直せる job のキー(ADR-0153 決定4。id と path は変えない。llm は無い)
EDITABLE_JOB_KEYS = ("label", "mode", "kind", "platform", "show_command", "prompt", "rules", "enable_rule", "before", "after")
# 「全部の組の設定」で defaults に書くキー。platform と show_command は全部の組の job に同じ値を入れる(下の edit_defaults)
EDITABLE_DEFAULTS_KEYS = ("mode", "kind", "prompt", "rules", "enable_rule", "before", "after")
ALL_JOBS_KEYS = ("platform", "show_command")
_SELECTION_KEYS = ("command", "segment")
# 組の設定の欄の鍵(画面の値)。区間は選択(自動・コマンド名・番号)と文字の 2 つ
SETTINGS_KEYS = (
    "label",
    "mode",
    "kind",
    "platform",
    "show_command",
    "before_selection",
    "before_value",
    "after_selection",
    "after_value",
    "prompt",
    "rules",
    "enable_rule",
)
_TEXT_SETTINGS = ("label", "show_command", "before_value", "after_value", "prompt", "rules", "enable_rule")
# 比較方式と種類の選択肢の文(値の並びは MODES・KINDS。文は窓の選択肢と同じ)
MODE_CHOICES_BY_NAME = tuple((name, dict(MODE_CHOICES)[name]) for name in MODES)
KIND_CHOICES_BY_NAME = tuple((name, dict(KIND_CHOICES)[name]) for name in KINDS)


@dataclass(frozen=True)
class EditableBatch:
    """窓で直せる組(REQ-212 §2)。直す関数はすべて新しい EditableBatch を返す(元は変えない)。

    data は schema_version / defaults / jobs の形(パスはすべて絶対パス。job にはすべて id がある)。
    excluded は外した組の id、auto_platform は機種が自動の組の id。edited は組を作った後に直したか(作り直す前に確かめる)。
    written は組のファイルから開いた組の、ファイルに書かれたままの両側のパス(job の id ごと)。結果の表示名(SideSpec.raw_path)を
    今までどおり書かれたままにするため(data は絶対パスで持つ)。
    """

    data: dict
    excluded: frozenset[str] = frozenset()
    auto_platform: frozenset[str] = frozenset()
    only_before: tuple[Path, ...] = ()
    only_after: tuple[Path, ...] = ()
    edited: bool = False
    written: Mapping[str, tuple[str, str]] = field(default_factory=dict)

    @property
    def jobs(self) -> list[dict]:
        jobs = self.data.get("jobs")
        return jobs if isinstance(jobs, list) else []

    @property
    def defaults(self) -> dict:
        defaults = self.data.get("defaults")
        return defaults if isinstance(defaults, dict) else {}

    def job_ids(self) -> list[str]:
        return [job["id"] for job in self.jobs]

    def job(self, job_id: str) -> dict | None:
        return next((job for job in self.jobs if job.get("id") == job_id), None)

    def included_ids(self) -> list[str]:
        return [job_id for job_id in self.job_ids() if job_id not in self.excluded]

    def to_prepared(self) -> PreparedBatch:
        """parse_manifest(base_dir=None)と load_job_rules で組にする。jobs は外した組も含む。機種が自動の組は platform を auto にする。

        組が 0 件(全部を解除した)なら検査せずに空の組を返す。断られたら ManifestError(規則のファイルが読めないときも)。
        """
        unpaired = [UnpairedRow(UNPAIRED_SIDE_LABEL["before"], path.name) for path in self.only_before]
        unpaired += [UnpairedRow(UNPAIRED_SIDE_LABEL["after"], path.name) for path in self.only_after]
        if self.data.get("jobs") == []:
            return PreparedBatch((), {}, [], unpaired)
        manifest = parse_manifest(self.data, base_dir=None)
        rules = load_job_rules(manifest)
        jobs = tuple(self._shown_job(job) for job in manifest.jobs)
        return PreparedBatch(jobs, rules, pair_rows(jobs), unpaired)

    def _shown_job(self, job: Job) -> Job:
        if job.id in self.auto_platform:
            job = replace(job, platform=AUTO_PLATFORM)
        if job.id in self.written:
            before, after = self.written[job.id]
            job = replace(job, before=replace(job.before, raw_path=before), after=replace(job.after, raw_path=after))
        return job


# --- 作る ---


def from_pairing(pairing: Pairing, *, auto: bool = True, raw_options: RawOptions = DEFAULT_RAW_OPTIONS) -> EditableBatch:
    """フォルダ 2 つを同じ名前で組にした結果から作る(pairing_manifest のまま。全部の組の機種を自動にする)。

    raw_options は単純比較の設定(設定の窓の既定。REQ-228 §7.2)。既定でなければ defaults.raw_options に入れる。
    """
    data = pairing_manifest(pairing, None, raw_options=raw_options)
    ids = frozenset(job["id"] for job in data["jobs"]) if auto else frozenset()
    return EditableBatch(
        data,
        auto_platform=ids,
        only_before=tuple(pairing.before_dir / name for name in pairing.before_only),
        only_after=tuple(pairing.after_dir / name for name in pairing.after_only),
    )


def from_manifest_file(path: str | Path) -> EditableBatch:
    """組のファイルから作る。値は書かれたとおり(platform が無い組は指定なし。CLI と同じ)。

    id の無い job には位置の id(parse_manifest の既定と同じ)を書いて持つ。platform: auto の組は自動の組にする
    (job に書いても defaults に書いても同じ)。読めない・断られたら ManifestError。
    """
    data = load_manifest_data(path)
    auto: set[str] = set()
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        jobs = data["jobs"]
        for index, job in enumerate(jobs):
            if isinstance(job, dict) and job.get("id") is None:
                jobs[index] = {"id": str(index + 1), **{k: v for k, v in job.items() if k != "id"}}
        defaults = data.get("defaults")
        default_auto = isinstance(defaults, dict) and defaults.get("platform") == AUTO_PLATFORM
        if default_auto:
            del defaults["platform"]
            _move_default_command(data)  # show_command だけを defaults に残さない(書かれた値のとおりに開く)
        for job in jobs:
            if not isinstance(job, dict):
                continue
            if job.get("platform") == AUTO_PLATFORM:
                del job["platform"]
                auto.add(job["id"])
            elif job.get("platform") is None and default_auto:
                auto.add(job["id"])
    # 書かれたままのパス(表示名)。id は上で書いた位置の id と同じ(parse_manifest の既定)
    written = {job.id: (job.before.raw_path, job.after.raw_path) for job in load_manifest(path).jobs}
    batch = EditableBatch(data, auto_platform=frozenset(auto), written=written)
    batch.to_prepared()  # 検査(断られたら ManifestError)
    return batch


# --- 直す(すべて新しい EditableBatch を返す。断られたら ManifestError で、元は変えない) ---


def _checked(batch: EditableBatch) -> EditableBatch:
    batch.to_prepared()
    return batch


def _unknown(keys: Iterable[str], allowed: Sequence[str], where: str) -> None:
    unknown = [key for key in keys if key not in allowed]
    if unknown:
        raise ManifestError([(f"{where}.{key}", smb_unknown_key(", ".join(allowed))) for key in unknown])


def _side_selection(side: dict, value: Mapping[str, Any] | None) -> None:
    for key in _SELECTION_KEYS:
        side.pop(key, None)
    if value:
        side.update({key: value[key] for key in _SELECTION_KEYS if value.get(key) is not None})


def _move_default_command(data: dict) -> None:
    """defaults.show_command を、show_command を書いていない全部の組の job へ写してから消す(どの組の Job.show_command も変わらない)。

    defaults.platform を消すときに使う。defaults に show_command だけが残ると、parse_manifest が
    「platform と show_command は両方セットで」と断るため(job に show_command だけを書くのは受け付ける)。
    """
    defaults = data.get("defaults")
    if not isinstance(defaults, dict) or defaults.get("show_command") is None:
        return
    command = defaults.pop("show_command")
    for job in data.get("jobs") or []:
        if isinstance(job, dict) and job.get("show_command") is None:
            job["show_command"] = command


def _move_default_platform(data: dict, keep: Collection[str]) -> None:
    """defaults.platform を、keep に無く platform を書いていない組の job へ写してから消す。

    defaults.show_command は、機種の名前の種類(系統の名前・auto・show の機種の名前)によらず、
    show_command を書いていない全部の組へ写してから消す(_move_default_command)。
    """
    defaults = data.get("defaults")
    if not isinstance(defaults, dict) or defaults.get("platform") is None:
        return
    platform = defaults.pop("platform")
    _move_default_command(data)
    for job in data["jobs"]:
        if job["id"] not in keep and job.get("platform") is None:
            job["platform"] = platform


def _set_job_platform(data: dict, auto: set[str], job: dict, value: str) -> None:
    """組の機種。auto は自動の組、"" は指定なし(defaults.platform があればほかの組へ写してから消す)、ほかは名前を書く。"""
    job_id = job["id"]
    job.pop("platform", None)
    if value == AUTO_PLATFORM:
        auto.add(job_id)
        return
    auto.discard(job_id)
    if value:
        job["platform"] = value
    else:
        _move_default_platform(data, keep={job_id, *auto})


def edit_job(batch: EditableBatch, job_id: str, changes: Mapping[str, Any]) -> EditableBatch:
    """組ごとの設定(REQ-212 §3)。changes は job のキー → 値(None・空は消す=defaults の値を使う)。

    before・after は区間だけ({"command": …} か {"segment": …}。None で自動)。platform は auto で自動の組、"" で指定なし。
    知らない job_id なら何も変えない。直せないキー(llm・id・path など)は ManifestError。
    """
    if batch.job(job_id) is None:
        return batch
    _unknown(changes, EDITABLE_JOB_KEYS, f"jobs[{job_id}]")
    data = copy.deepcopy(batch.data)
    auto = set(batch.auto_platform)
    job = next(job for job in data["jobs"] if job["id"] == job_id)
    for key, value in changes.items():
        if key == "platform":
            _set_job_platform(data, auto, job, value or "")
        elif key in SIDE_KEYS:
            _side_selection(job[key], value)
        elif value is None or value == "" or value == []:
            job.pop(key, None)
        else:
            job[key] = value
    return _checked(replace(batch, data=data, auto_platform=frozenset(auto), edited=True))


def edit_defaults(batch: EditableBatch, changes: Mapping[str, Any]) -> EditableBatch:
    """全部の組の設定(REQ-212 §3)。mode・kind・prompt・rules・enable_rule・区間は defaults に書く(組に書いたキーが優先する)。

    platform と show_command は全部の組の job に同じ値を入れる(defaults.platform は消す。§8)。
    defaults に show_command だけを書くと parse_manifest が断る(platform と対)ため、show_command も組ごとに書く。
    """
    _unknown(changes, (*EDITABLE_DEFAULTS_KEYS, *ALL_JOBS_KEYS), "defaults")
    data = copy.deepcopy(batch.data)
    auto = set(batch.auto_platform)
    if not isinstance(data.get("defaults"), dict):
        data["defaults"] = {}
    defaults = data["defaults"]
    for key, value in changes.items():
        if key == "platform":
            # defaults.platform は先に各組へ写して消す(show の機種の名前なら show_command も)。その後で全部の組に同じ値を入れる
            _move_default_platform(data, keep=())
            for job in data["jobs"]:
                _set_job_platform(data, auto, job, value or "")
        elif key == "show_command":
            for job in data["jobs"]:
                if value:
                    job["show_command"] = value
                else:
                    job.pop("show_command", None)
        elif key in SIDE_KEYS:
            selection = {k: value[k] for k in _SELECTION_KEYS if value and value.get(k) is not None}
            if selection:
                defaults[key] = selection
            else:
                defaults.pop(key, None)
        elif value is None or value == "" or value == []:
            defaults.pop(key, None)
        else:
            defaults[key] = value
    return _checked(replace(batch, data=data, auto_platform=frozenset(auto), edited=True))


def set_platform(batch: EditableBatch, job_id: str, value: str) -> EditableBatch:
    """表の機種の選択(§8)。value は WEB_PLATFORM_CHOICES の値(auto・""・系統の名前)。"""
    return edit_job(batch, job_id, {"platform": value})


def set_included(batch: EditableBatch, job_id: str, on: bool) -> EditableBatch:
    """組を外す・戻す(§4)。知らない job_id なら何も変えない。"""
    if batch.job(job_id) is None:
        return batch
    excluded = batch.excluded - {job_id} if on else batch.excluded | {job_id}
    if excluded == batch.excluded:
        return batch
    return replace(batch, excluded=excluded, edited=True)


def _unique_id(base: str, used: Collection[str]) -> str:
    if base not in used:
        return base
    number = 2
    while f"{base} ({number})" in used:
        number += 1
    return f"{base} ({number})"


def pair_files(batch: EditableBatch, before: Path, after: Path) -> EditableBatch:
    """片方だけのファイルを 2 つ選んで組にする(§6)。id は「<変更前の名前> / <変更後の名前>」(重なれば「 (2)」)。機種は自動。

    組の末尾に足し、両方のファイルを片方だけの一覧から除く。一覧に無いファイルなら何も変えない。比較はしない。
    """
    if before not in batch.only_before or after not in batch.only_after:
        return batch
    data = copy.deepcopy(batch.data)
    if not isinstance(data.get("jobs"), list):
        data["jobs"] = []
    job_id = _unique_id(f"{before.name} / {after.name}", {job["id"] for job in data["jobs"]})
    data["jobs"].append({"id": job_id, "before": {"path": str(before)}, "after": {"path": str(after)}})
    return _checked(
        replace(
            batch,
            data=data,
            auto_platform=batch.auto_platform | {job_id},
            only_before=tuple(path for path in batch.only_before if path != before),
            only_after=tuple(path for path in batch.only_after if path != after),
            edited=True,
        )
    )


def _by_name(paths: Iterable[Path]) -> tuple[Path, ...]:
    return tuple(sorted(paths, key=lambda path: (path.name, str(path))))


def unpair(batch: EditableBatch, job_id: str) -> EditableBatch:
    """組を消し、両方のファイルを片方だけの一覧へ戻す(名前の順。§6)。同じファイルを両側に書いた組は、両方の側に 1 つずつ入る。"""
    job = batch.job(job_id)
    if job is None:
        return batch
    data = copy.deepcopy(batch.data)
    data["jobs"] = [item for item in data["jobs"] if item["id"] != job_id]
    before, after = Path(job["before"]["path"]), Path(job["after"]["path"])
    return _checked(
        replace(
            batch,
            data=data,
            excluded=batch.excluded - {job_id},
            auto_platform=batch.auto_platform - {job_id},
            written={key: value for key, value in batch.written.items() if key != job_id},
            only_before=_by_name({*batch.only_before, before}),
            only_after=_by_name({*batch.only_after, after}),
            edited=True,
        )
    )


# --- 実行(§8) ---


def prepare_with_auto(prepare: Callable[[Job], CompareRequest], auto_ids: Collection[str]) -> Callable[[Job], CompareRequest]:
    """入力の読み込み(make_prepare)に被せる。機種が自動の組の要求の platform を auto にするだけ(判定は run_compare が 1 回)。

    ほかの組の要求は変えない。JobInputError はそのまま通す。AI の 1 段目と 2 段目も同じ包みを通る。
    """
    auto = frozenset(auto_ids)

    def prepared(job: Job) -> CompareRequest:
        request = prepare(job)
        if job.id in auto and request.platform != AUTO_PLATFORM:
            return replace(request, platform=AUTO_PLATFORM)
        return request

    return prepared


# --- 保存(§5) ---


def _read_text(path: Path) -> str | None:
    try:
        return read_config_input(path).text
    except (InputDecodeError, OSError):
        return None


def judge_for_save(
    batch: EditableBatch, read: Callable[[Path], str | None] = _read_text
) -> dict[str, AutoPlatform | None]:
    """保存のときの機種の判定。外していない自動の組のうち、種類が config の組だけ。読めない組は None。

    ファイルを読むので、呼ぶ側はロックの外で呼ぶ。判定の決まりは REQ-209 の choose_platform のまま(区間を選ぶ前の本文全体)。
    """
    judged: dict[str, AutoPlatform | None] = {}
    for job in batch.to_prepared().jobs:
        if job.id in batch.excluded or job.id not in batch.auto_platform or job.kind != "config":
            continue
        before, after = read(job.before.path), read(job.after.path)
        judged[job.id] = None if before is None or after is None else choose_platform(before, after)
    return judged


def save_undetected(judged: Mapping[str, AutoPlatform | None]) -> int:
    """保存で機種を書けなかった(判定なし・食い違い・読めない)組の数。Cisco 系は数えない(書かないのが正しい)。"""
    return sum(1 for auto in judged.values() if platform_undetected(auto))


def manifest_for_save(batch: EditableBatch, base_dir: Path | None, judged: Mapping[str, AutoPlatform | None]) -> dict:
    """書く形(§5)。外した組を書かない。パスと rules は base_dir からの相対パス。

    自動の組: 種類が config なら判定した機種名を書く(Cisco 系・判定できない組は書かない)。種類が auto なら platform: auto
    (CLI で同じ結果にするため)。show なら書かない。defaults.platform があって自動の組が 1 つでもあれば、defaults.platform を
    消して自動でない組の job へ写す。キーは EDITABLE_JOB_KEYS と id・path だけ(LLM は書かない)。
    """
    kinds = {job.id: job.kind for job in batch.to_prepared().jobs}
    data = copy.deepcopy(batch.data)
    data["jobs"] = [job for job in data["jobs"] if job["id"] not in batch.excluded]
    auto = [job["id"] for job in data["jobs"] if job["id"] in batch.auto_platform]
    if auto:
        _move_default_platform(data, keep=set(auto))
    for job in data["jobs"]:
        if job["id"] in batch.auto_platform:
            job.pop("platform", None)
            kind = kinds.get(job["id"])
            if kind == "auto":
                job["platform"] = AUTO_PLATFORM
            elif kind == "config":
                chosen = judged.get(job["id"])
                if chosen is not None and not chosen.conflict and chosen.used is not None:
                    job["platform"] = chosen.used
        for side in SIDE_KEYS:
            job[side]["path"] = manifest_path(Path(job[side]["path"]), base_dir)
        if isinstance(job.get("rules"), str):
            job["rules"] = manifest_path(Path(job["rules"]), base_dir)
    defaults = data.get("defaults")
    if isinstance(defaults, dict) and isinstance(defaults.get("rules"), str):
        defaults["rules"] = manifest_path(Path(defaults["rules"]), base_dir)
    _drop_default_raw_options(data)
    return data


def _is_default_raw(plain: Any) -> bool:
    """raw_options の値が書いていないもの(null)か、既定の設定か。読めない値は既定でないとみなす(検査は parse_manifest)。"""
    if plain is None:
        return True
    try:
        return options_from_plain(plain).is_default()
    except RawOptionsError:
        return False


def _drop_default_raw_options(data: dict) -> None:
    """保存で、既定の単純比較の設定を書かない(REQ-228 §7.1。既定のときは今と同じ中身)。

    defaults.raw_options は既定なら消す。組の raw_options は、既定で、しかも defaults が既定のときだけ消す
    (defaults が既定でないときに消すと、その組は defaults の設定に変わってしまうため)。
    """
    defaults = data.get("defaults")
    defaults_default = True
    if isinstance(defaults, dict) and "raw_options" in defaults:
        defaults_default = _is_default_raw(defaults["raw_options"])
        if defaults_default:
            del defaults["raw_options"]
    for job in data.get("jobs") or []:
        if not isinstance(job, dict) or "raw_options" not in job:
            continue
        if job["raw_options"] is None or (defaults_default and _is_default_raw(job["raw_options"])):
            del job["raw_options"]


def raw_settings_line(batch: EditableBatch | None) -> str | None:
    """一括比較の窓の下の行(REQ-228 §7.2)。defaults の設定の要約(無ければ「単純比較の設定: なし」)の後ろに、自分の
    raw_options を持つ組(外した組は数えない)があれば「（組ごとの設定: N 組）」を足す。全部既定なら None。
    """
    if batch is None:
        return None
    plain = batch.defaults.get("raw_options")
    summary = raw_options_summary(plain) if plain is not None else None
    count = sum(
        1 for job in batch.jobs if job.get("id") not in batch.excluded and job.get("raw_options") is not None
    )
    if summary is None and count == 0:
        return None
    return (summary or BATCH_RAW_NONE) + (batch_raw_pairs_suffix(count) if count else "")


def pair_raw_line(batch: EditableBatch, job_id: str | None) -> str:
    """「組の設定」のダイアログの読むだけの 1 行。その組で使う設定(組のもの、無ければ defaults のもの)の要約。
    全部の組の設定では defaults の要約。無ければ「単純比較の設定: なし」。
    """
    plain = batch.defaults.get("raw_options")
    if job_id is not None:
        job = batch.job(job_id) or {}
        if job.get("raw_options") is not None:
            plain = job["raw_options"]
    summary = raw_options_summary(plain) if plain is not None else None
    return summary or BATCH_RAW_NONE


def save_edited_manifest(
    batch: EditableBatch, target: str | Path, judged: Mapping[str, AutoPlatform | None]
) -> tuple[Path, int, int]:
    """「この組を保存…」。write_manifest で書く(検査と、上書きしないのは今のまま。ManifestWriteError)。

    戻り値は (書いたパス, 書かなかった外した組の数, 機種を書けなかった組の数)。
    """
    path = Path(target)
    data = manifest_for_save(batch, path.absolute().parent, judged)
    written = write_manifest(data, path)
    excluded = sum(1 for job_id in batch.job_ids() if job_id in batch.excluded)
    return written, excluded, save_undetected(judged)


# --- hostname の候補(§7) ---


@dataclass(frozen=True)
class HostnameCandidate:
    before: Path
    after: Path
    value: str


@dataclass(frozen=True)
class Candidates:
    """候補(変更前の一覧の順)と、候補にしなかったファイルの数(読めなかった・同じ側で値が重なった)。"""

    items: tuple[HostnameCandidate, ...]
    unread: int
    duplicated: int


def hostname_candidates(
    only_before: Sequence[Path], only_after: Sequence[Path], read: Callable[[Path], str | None]
) -> Candidates:
    """値が完全に同じで、両側に 1 つずつあるものだけを候補にする(文字をそろえない。ADR-0153 決定7)。組は変えない。"""

    def side(paths: Sequence[Path]) -> tuple[dict[str, list[Path]], int]:
        found: dict[str, list[Path]] = {}
        unread = 0
        for path in paths:
            value = read(path)
            if value is None:
                unread += 1
            else:
                found.setdefault(value, []).append(path)
        return found, unread

    before, unread_before = side(only_before)
    after, unread_after = side(only_after)
    duplicated = sum(len(paths) for found in (before, after) for paths in found.values() if len(paths) > 1)
    items = tuple(
        HostnameCandidate(paths[0], after[value][0], value)
        for value, paths in before.items()
        if len(paths) == 1 and len(after.get(value, ())) == 1
    )
    return Candidates(items, unread_before + unread_after, duplicated)


# --- AI に組の案を聞くときに送るファイル名(REQ-219 / ADR-0159 決定2・3) ---


@dataclass(frozen=True)
class PairAiTargets:
    """AI に送る名前(側ごとに 名前 → パス。一覧の順)と、送らない名前(変更前、変更後の順)。

    送らない名前は、同じ側で名前が重なるファイルと、file_name_refused で断った名前(確認の画面に出す。手元の画面なので名前を出してよい)。
    """

    before: dict[str, Path]
    after: dict[str, Path]
    held_back: tuple[str, ...]


def pair_ai_targets(
    only_before: Sequence[Path], only_after: Sequence[Path], secret_words: Sequence[str]
) -> PairAiTargets:
    """片方だけのファイルから、AI に送る名前を決める。組は変えない。ファイルは読まない。

    hostname の候補のファイルも除かない(ADR-0177 決定1)。
    1. 同じ側で Path.name が重なるファイルを除く 2. 残りを file_name_refused(秘密の語・制御文字)で分ける。
    送るのはフォルダを除いた名前(Path.name)だけ。
    """
    def side(paths: Sequence[Path]) -> tuple[dict[str, Path], list[str]]:
        rest = list(paths)
        counts: dict[str, int] = {}
        for path in rest:
            counts[path.name] = counts.get(path.name, 0) + 1
        chosen: dict[str, Path] = {}
        held: list[str] = []
        for path in rest:
            name = path.name
            if counts[name] > 1 or file_name_refused(name, secret_words):
                if name not in held:  # 重なった名前は 1 回だけ出す
                    held.append(name)
                continue
            chosen[name] = path
        return chosen, held

    before, held_before = side(only_before)
    after, held_after = side(only_after)
    return PairAiTargets(before, after, (*held_before, *held_after))


# --- 組の設定の欄(§3。文字は Python が持つ) ---


def _index(value: Any, names: Sequence[str]) -> int:
    """選択の番号(0 は既定のまま。続いて names)。"""
    return names.index(value) + 1 if isinstance(value, str) and value in names else 0


def _selection_value(side: Mapping[str, Any] | None) -> tuple[int, str]:
    if not isinstance(side, Mapping):
        return 0, ""
    if side.get("command") is not None:
        return 1, str(side["command"])
    if side.get("segment") is not None:
        return 2, str(side["segment"])
    return 0, ""


def _platform_index(value: str) -> int | None:
    return next((index for index, (name, _label) in enumerate(WEB_PLATFORM_CHOICES) if name == value), None)


def settings_values(batch: EditableBatch, job_id: str | None) -> dict[str, Any]:
    """欄の今の値(画面の値の形)。job_id が None なら全部の組の設定。

    機種は、選択のときの番号(platform_index。選択肢に無い名前は None)と、文字のときの値(platform)の 2 つ。
    全部の組の設定の機種と show のコマンドは「組ごとのまま」(番号 0・空の文字)から始める。
    """
    if job_id is None:
        source: Mapping[str, Any] = batch.defaults
        platform_index: int | None = 0
        platform_text = ""
        show_command = ""
    else:
        source = batch.job(job_id) or {}
        if job_id in batch.auto_platform:
            platform_text = AUTO_PLATFORM
        else:
            value = source.get("platform", batch.defaults.get("platform"))
            platform_text = value if isinstance(value, str) else ""
        platform_index = _platform_index(platform_text)
        show_command = str(source.get("show_command") or "")
    values: dict[str, Any] = {
        "mode": _index(source.get("mode"), MODES),
        "kind": _index(source.get("kind"), KINDS),
        "platform_index": platform_index,
        "platform": platform_text,
        "show_command": show_command,
        "prompt": str(source.get("prompt") or ""),
        "rules": str(source.get("rules") or ""),
        "enable_rule": ", ".join(source.get("enable_rule") or []) if isinstance(source.get("enable_rule"), list) else "",
    }
    if job_id is not None:
        values["label"] = str(source.get("label") or "")
    for side in SIDE_KEYS:
        values[f"{side}_selection"], values[f"{side}_value"] = _selection_value(source.get(side))
    return values


def default_kind(batch: EditableBatch, job_id: str | None) -> str:
    """「既定のまま」のときの種類(組ごとの設定では defaults の値、全部の組の設定では既定の config)。"""
    kind = batch.defaults.get("kind") if job_id is not None else None
    return kind if isinstance(kind, str) and kind in KINDS else "config"


def settings_form(batch: EditableBatch, job_id: str | None, typed: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """組の設定のダイアログの中身(文字・選択肢・今の値)。typed は断られたときに入れ直す打った値。"""
    values = settings_values(batch, job_id)
    for key, value in (typed or {}).items():
        if key == "platform":
            values["platform" if isinstance(value, str) else "platform_index"] = value
        elif key in SETTINGS_KEYS:
            values[key] = value
    keep = BATCH_SETTINGS_KEEP_DEFAULT
    platform_choices = [label for _value, label in WEB_PLATFORM_CHOICES]
    if job_id is None:
        platform_choices = [BATCH_SETTINGS_KEEP_PAIRS, *platform_choices]
    selection = list(BATCH_SETTINGS_SELECTION_CHOICES)
    fields: list[dict[str, Any]] = []
    if job_id is not None:
        fields.append({"key": "label", "type": "text", "label": BATCH_SETTINGS_FIELD_LABEL, "value": values["label"]})
    fields += [
        {"key": "mode", "type": "select", "label": COMPARE_MODE, "choices": [keep, *(label for _v, label in MODE_CHOICES_BY_NAME)], "value": values["mode"]},
        {"key": "kind", "type": "select", "label": COMPARE_KIND, "choices": [keep, *(label for _v, label in KIND_CHOICES_BY_NAME)], "value": values["kind"]},
        {
            "key": "platform",
            "type": "platform",
            "label": COMPARE_PLATFORM,
            "choices": platform_choices,
            "index": values["platform_index"],
            "text": values["platform"] if isinstance(values["platform"], str) else "",
        },
        {"key": "show_command", "type": "text", "label": BATCH_SETTINGS_FIELD_SHOW_COMMAND, "value": values["show_command"]},
        {
            "key": "before",
            "type": "selection",
            "label": BATCH_SETTINGS_FIELD_BEFORE,
            "choices": selection,
            "index": values["before_selection"],
            "text": values["before_value"],
        },
        {
            "key": "after",
            "type": "selection",
            "label": BATCH_SETTINGS_FIELD_AFTER,
            "choices": selection,
            "index": values["after_selection"],
            "text": values["after_value"],
        },
        {"key": "prompt", "type": "text", "label": BATCH_SETTINGS_FIELD_PROMPT, "value": values["prompt"], "note": BATCH_SETTINGS_PROMPT_NOTE},
        {"key": "rules", "type": "rules", "label": BATCH_SETTINGS_FIELD_RULES, "value": values["rules"], "pick": PICK_BUTTON},
        {
            "key": "enable_rule",
            "type": "text",
            "label": BATCH_SETTINGS_FIELD_ENABLE_RULE,
            "value": values["enable_rule"],
            "note": BATCH_SETTINGS_ENABLE_RULE_NOTE,
        },
    ]
    readonly: list[dict[str, str]] = []
    if job_id is not None:
        job = batch.job(job_id) or {}
        readonly = [
            {"label": BATCH_SETTINGS_FIELD_ID, "value": job_id},
            {"label": BATCH_COL_BEFORE, "value": str(job.get("before", {}).get("path", ""))},
            {"label": BATCH_COL_AFTER, "value": str(job.get("after", {}).get("path", ""))},
        ]
    return {
        "job_id": job_id,
        "title": batch_pair_settings_title(job_id) if job_id is not None else BATCH_ALL_SETTINGS_TITLE,
        "note": BATCH_SETTINGS_PAIR_NOTE if job_id is not None else BATCH_SETTINGS_ALL_NOTE,
        "readonly": readonly,
        "default_kind": default_kind(batch, job_id),
        "kinds": list(KINDS),
        "fields": fields,
        # 単純比較の設定(REQ-228 §7.2)。読むだけの 1 行と注記(この窓では直せない)
        "raw_line": pair_raw_line(batch, job_id),
        "raw_note": BATCH_NOTE_RAW_OPTIONS,
        "apply": BATCH_SETTINGS_APPLY,
        "cancel": DIALOG_CANCEL,
    }



def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_settings(values: Any, *, for_defaults: bool) -> dict[str, Any]:
    """画面から受け取った欄の値の形を確かめる。知らない鍵(llm など)・形の違う値は ValueError。"""
    if not isinstance(values, dict):
        raise ValueError("values")
    allowed = tuple(key for key in SETTINGS_KEYS if not (for_defaults and key == "label"))
    for key, value in values.items():
        if key not in allowed:
            raise ValueError(key)
        if key in _TEXT_SETTINGS:
            ok = isinstance(value, str)
        elif key == "mode":
            ok = _is_int(value) and 0 <= value <= len(MODES)
        elif key == "kind":
            ok = _is_int(value) and 0 <= value <= len(KINDS)
        elif key == "platform":
            limit = len(WEB_PLATFORM_CHOICES) + (1 if for_defaults else 0)
            ok = isinstance(value, str) or (_is_int(value) and 0 <= value < limit)
        else:  # 区間の選択
            ok = _is_int(value) and 0 <= value < len(BATCH_SETTINGS_SELECTION_CHOICES)
        if not ok:
            raise ValueError(key)
    for side in SIDE_KEYS:
        if f"{side}_value" in values and f"{side}_selection" not in values:
            raise ValueError(side)
    return values


def _selection(index: int, text: str) -> dict[str, Any] | None:
    text = text.strip()
    if index == 0 or not text:
        return None
    if index == 1:
        return {"command": text}
    # 数字だけなら整数。ほかは文字のまま渡し、parse_manifest の「1 以上の整数」で断ってもらう
    return {"segment": int(text) if text.isascii() and text.isdigit() else text}


def settings_changes(batch: EditableBatch, job_id: str | None, values: Mapping[str, Any]) -> dict[str, Any]:
    """欄の値(check_settings を通したもの)を、変わった欄だけ job(または defaults)のキーの値にする。空の欄は None(キーを書かない)。"""
    current = settings_values(batch, job_id)
    changes: dict[str, Any] = {}
    for key in ("label", "show_command", "prompt", "rules"):
        if key in values and values[key].strip() != current.get(key, ""):
            text = values[key].strip()
            if job_id is None and key == "show_command":
                if text:
                    changes[key] = text  # 全部の組の show のコマンドは、空なら組ごとのまま
            else:
                changes[key] = text or None
    if "enable_rule" in values:
        items = [item.strip() for item in values["enable_rule"].split(",") if item.strip()]
        if ", ".join(items) != current["enable_rule"]:
            changes["enable_rule"] = items or None
    for key, names in (("mode", MODES), ("kind", KINDS)):
        if key in values and values[key] != current[key]:
            changes[key] = names[values[key] - 1] if values[key] else None
    if "platform" in values:
        value = values["platform"]
        if isinstance(value, str):
            text = value.strip()
            if text != current["platform"] and not (job_id is None and not text):
                changes["platform"] = text
        elif value != current["platform_index"]:
            if job_id is None:
                if value:
                    changes["platform"] = WEB_PLATFORM_CHOICES[value - 1][0]
            else:
                changes["platform"] = WEB_PLATFORM_CHOICES[value][0]
    for side in SIDE_KEYS:
        key = f"{side}_selection"
        if key in values:
            index, text = values[key], values.get(f"{side}_value", "")
            if (index, text.strip()) != (current[key], current[f"{side}_value"]):
                changes[side] = _selection(index, text)
    return changes
