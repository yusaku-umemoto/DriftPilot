"""比較結果の保存と読み込み(ADR-0017 / REQ-14)。

保存は明示的な操作で、run_compare は保存しない(比較は副作用の無い処理のまま)。
入口は build_record で中身を組み立て、save で書く。REQ-15 の JSON Lines の1行は build_record の返り値の後ろに
最上位の job(JobMeta)を足したもの(docs/notes/req-15-batch.md)。
マスキングはしない(決定4)。保存ファイルは入力と同じ機密度(社外秘)として扱う。
形と全キーの意味は docs/notes/req-14-storage.md。
"""

import copy
import dataclasses
import hashlib
import json
import os
import re
import types
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
from typing import Any, Literal, NamedTuple, TypedDict, Union, get_args, get_origin, get_type_hints, is_typeddict

import driftpilot
from driftpilot.config_parser import platforms
from driftpilot.config_parser.family_hint import AUTO_PLATFORM
from driftpilot.core import SCHEMA_VERSION, CompareOutcome, CompareRequest
from driftpilot.core.compare import effective_rules
from driftpilot.core.types import SIDES, Conversation, KindChoice, Mode
from driftpilot.coverage_report import FOLD_VERSION
from driftpilot.diff_engine import NormalizationRule
from driftpilot.diff_engine.normalization import builtin_rules, select_rules
from driftpilot.messages import STORE_TEXTS as _T
from driftpilot.raw_keys import RawOptionsError, options_from_plain, options_to_plain
from driftpilot.segmentation import Exposure

# 最上位のキー。読み込みでは全部があることを確かめる(欠けたものを「無い」と読まない)
RECORD_KEYS = ("schema_version", "saved_at", "inputs", "settings", "tools", "contains_config_values", "result")
# 一括比較の1行だけが持つ最上位のキー(REQ-15)。あれば JobMeta として検査する。無いのが1件の比較
JOB_KEY = "job"
# REQ-269 で足した job の項目。比較の窓で手で組にした行だけ true を書く(自動の組の行・今の保存は今と1文字も変えない)。
# 版を据え置いたため、無い保存も読む(あれば true か false だけ受け取る)
PAIRED_BY_HAND_KEY = "paired_by_hand"
# REQ-16 で足した result の項目。版を据え置いたため、これが無い保存(REQ-16 より前)も読む(docs/notes/req-14-storage.md)
LLM_KEY = "llm"
# REQ-22 で足した result の項目。同じく版を据え置いたため、これが無い保存(REQ-22 より前)も空のリストとして読む
EXCLUSIONS_KEY = "exclusions"
# REQ-179 で足した result.segmentation.<側>.split の項目。同じく版を据え置いたため、これが無い保存(REQ-179 より前)も 0 として読む
PAGER_MARKS_KEY = "pager_marks"
# REQ-209 で足した項目。機種を自動にして config と決まったときだけ書く(自動でない保存は今と1文字も変えない)。
# 版を据え置いたため、無い保存も読む(result.auto_platform は None、settings.platform_used は無いまま)
AUTO_PLATFORM_KEY = "auto_platform"
PLATFORM_USED_KEY = "platform_used"
# REQ-227 で足した項目。単純比較の設定が既定でないときだけ書く(既定の保存は今と1文字も変えない)。
# 版を据え置いたため、無い保存も読む(result.raw.options・result.show.raw.options は None、settings.raw_options は無いまま)
RAW_OPTIONS_KEY = "raw_options"
RESULT_RAW_OPTIONS_KEY = "options"
# REQ-242 で足した result の項目(ADR-0179 決定2)。AI の答えがあり、やり取りのどれかに値があるときだけ書く
# (答えが無い保存・やり取りの無い保存は今と1文字も変えない)。版を据え置いたため、無い保存も読む(None)
CONVERSATION_KEY = "conversation"
DEFAULT_LABEL = "compare"
_UNSAFE_LABEL = re.compile(r"[^A-Za-z0-9._-]")
_MISSING = object()


# メタ情報の形。読み込みでは result と同じく型ヒントに従って検査し、欠けた項目・型の違いを拒否する。
# build_record が書くキーとの一致はテストで検査している
class InputMeta(TypedDict):
    name: str
    path: str | None
    # null は入力を読めなかった側だけ(result.error.stage が read_input で、error.detail.sides にある側。REQ-15)。
    # それ以外の null は拒否する
    sha256: str | None
    line_count: int | None
    command: str | None
    default_exposure: Exposure | None


class InputsMeta(TypedDict):
    before: InputMeta
    after: InputMeta


class SettingsMeta(TypedDict):
    mode: Mode
    kind: KindChoice
    platform: str | None
    show_command: str | None
    enabled_rule_ids: list[str] | None
    effective_rule_ids: list[str] | None
    rules_fingerprint: str | None


class ToolsMeta(TypedDict):
    driftpilot: str
    hier_config: str | None
    patterns_fingerprint: str
    patterns_count: int
    fold_version: str


class RecordMeta(TypedDict):
    schema_version: str
    saved_at: str
    inputs: InputsMeta
    settings: SettingsMeta
    tools: ToolsMeta
    contains_config_values: Literal[True]


class SelectionSourceMeta(TypedDict):
    before: Literal["job", "defaults"] | None
    after: Literal["job", "defaults"] | None


class JobMeta(TypedDict):
    """一括比較(REQ-15)の1行だけが持つ最上位の job。1件の比較(compare --save)には無い。"""

    id: str
    label: str | None
    index: int
    selection_source: SelectionSourceMeta


class StorageError(Exception):
    """保存・読み込みの失敗。入口は利用者向けのエラーとして扱う。"""


class StorageFormatError(StorageError):
    """版は合っているが、中身が保存の形になっていない(項目の欠け、型の違い)。"""


class UnsupportedSchemaVersion(StorageError):
    """版が現在のものと違う。変換せずに拒否する(決定5)。"""

    def __init__(self, where: str, found: Any) -> None:
        self.where = where
        self.found = found
        self.expected = SCHEMA_VERSION
        shown = _T["none_label"] if found is _MISSING else repr(found)
        super().__init__(_T["version_mismatch"].format(where=where, shown=shown, expected=repr(SCHEMA_VERSION)))


class StoredRecord(NamedTuple):
    """読み込んだ保存ファイル。meta は result 以外の最上位の項目、outcome は組み立て直した結果。"""

    meta: dict[str, Any]
    outcome: CompareOutcome


# --- 組み立て ---


def to_plain(value: Any) -> Any:
    """JSON にできる形へ変える。dataclass は項目名をそのままキーにする(asdict 相当)。

    タプルはリスト、IPv4Interface などは文字列にする。None はキーを残して null にする。
    """
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_plain(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(k): to_plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def rules_fingerprint(rules: list[NormalizationRule] | None) -> str | None:
    """読み込んだ正規化ルールの指紋。読み込み順に依存しないよう id 順に並べる。ルールなしは None。

    flags を含めるのは、core はコンパイル済みの正規表現を受け取れるため、re.IGNORECASE などの違いが
    正規表現の文字列だけでは見えないから。platform / exclusion_reason は値があるときだけ含める
    (REQ-22 より前のルールの指紋を動かさない)。
    """
    if not rules:
        return None
    items = []
    for r in sorted(rules, key=lambda r: r.id):
        item: dict[str, Any] = {
            "id": r.id,
            "scope": r.scope,
            "pattern": r.pattern.pattern,
            "flags": r.pattern.flags,
            "replacement": r.replacement,
            "default_enabled": r.default_enabled,
        }
        if r.platform is not None:
            item["platform"] = r.platform
        if r.exclusion_reason is not None:
            item["exclusion_reason"] = r.exclusion_reason
        items.append(item)
    text = json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _effective_rule_ids(request: CompareRequest, outcome: CompareOutcome) -> list[str] | None:
    """kind が決まっていれば、select_rules で選ばれるルールの ID。決まる前に止まったときは None。

    ルールは core と同じく、要求に無ければ同梱の機種のルール。config では --platform を機種として渡す(REQ-22)。
    """
    if outcome.kind is None:
        return None
    platform = _used_platform(request, outcome) if outcome.kind == "config" else None
    try:
        return [r.id for r in select_rules(effective_rules(request), outcome.kind, request.enabled_rule_ids, platform)]
    except ValueError:  # 指定の誤り。raw を実行しない mode では比較が止まらないため、ここに来うる
        return None


def _rules_fingerprint(request: CompareRequest, effective_ids: list[str] | None) -> str | None:
    """要求のルールの指紋。要求に無いときは、同梱のルールが選ばれたときだけ同梱のルールの指紋(A型・B型は None のまま)。"""
    if request.rules is not None:
        return rules_fingerprint(request.rules)
    return rules_fingerprint(builtin_rules()) if effective_ids else None


def _unread_sides(outcome: CompareOutcome) -> set[str]:
    """結果が「読めなかった」と言っている側。sha256 / line_count を null にしてよいのは、この側だけ。

    入力を読む前に止まった結果(error.stage が read_input)の error.detail.sides に書かれた側。それ以外は空。
    """
    error = outcome.error
    if outcome.status != "error" or error is None or error.stage != "read_input":
        return set()
    sides = error.detail.get("sides")
    if not isinstance(sides, list):
        return set()
    return {side for side in sides if side in SIDES}


def _config_platform(request: CompareRequest, outcome: CompareOutcome) -> str | None:
    """指紋を取る表の機種。config として比べた(または比べようとした)ときだけ --platform を系統として読む(REQ-21)。

    show と判定された結果や、種類が決まる前に止まった auto の結果は A型の表(今までどおり)。
    表が無い機種(fujitsu_srs)はエラーの結果なので、A型の表にする。
    """
    kind = outcome.kind or (request.kind if request.kind != "auto" else None)
    platform = _used_platform(request, outcome)
    if kind != "config" or platform not in platforms.FLAT_PLATFORMS:
        return None
    return platform


def _used_platform(request: CompareRequest, outcome: CompareOutcome) -> str | None:
    """config の系統として読む名前。機種が自動なら判定で使った系統(ADR-0151 決定4)、ほかは --platform のまま。"""
    if request.platform != AUTO_PLATFORM:
        return request.platform
    return outcome.auto_platform.used if outcome.auto_platform is not None else None


def _package_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _path(source_paths: Mapping[str, str | os.PathLike[str] | None], side: str) -> str | None:
    value = source_paths.get(side)
    if value is None:
        return None
    text = os.fspath(value)
    if not text:
        raise ValueError(_T["source_path_empty"].format(side=side))
    return text


def build_record(
    request: CompareRequest,
    outcome: CompareOutcome,
    *,
    source_paths: Mapping[str, str | os.PathLike[str] | None],
    saved_at: datetime | None = None,
    unread_sides: Iterable[str] = (),
) -> dict[str, Any]:
    """保存する中身を組み立てる。source_paths は入口が渡す入力のパス(貼り付けなどパスが無い側は None)。

    unread_sides は入力を読めなかった側(REQ-15)。その側の sha256 / line_count は null にする
    (空のテキストのハッシュを書くと「空の入力だった」と読めてしまうため)。結果が読めなかったと言っている側
    (error.stage が read_input の error.detail.sides)と一致させる。
    """
    unknown = set(source_paths) - set(SIDES)
    if unknown:
        raise ValueError(_T["source_paths_keys"].format(names=sorted(unknown)))
    unread = set(unread_sides)
    if unread - set(SIDES):
        raise ValueError(_T["unread_sides_keys"].format(names=sorted(unread - set(SIDES))))
    if unread != _unread_sides(outcome):
        raise ValueError(
            _T["unread_sides_mismatch"].format(unread=sorted(unread), declared=sorted(_unread_sides(outcome)))
        )
    if saved_at is None:
        saved_at = datetime.now(timezone.utc).astimezone()
    elif saved_at.tzinfo is None or saved_at.utcoffset() is None:
        raise ValueError(_T["saved_at_timezone"])

    sides = {"before": request.before, "after": request.after}
    inputs: dict[str, Any] = {}
    for side in SIDES:
        text = sides[side].text
        seg = outcome.segmentation[side] if outcome.segmentation else None
        exposure = outcome.default_exposure[side] if outcome.default_exposure else None
        read = side not in unread
        inputs[side] = {
            "name": sides[side].name,
            "path": _path(source_paths, side),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest() if read else None,
            "line_count": len(text.splitlines()) if read else None,
            "command": seg.selected.command if seg and seg.selected else None,
            "default_exposure": exposure.state if exposure else None,
        }
    fp = platforms.patterns_fingerprint(_config_platform(request, outcome))
    effective_ids = _effective_rule_ids(request, outcome)
    settings: dict[str, Any] = {
        "mode": request.mode,
        "kind": request.kind,
        "platform": request.platform,
        "show_command": request.show_command,
        "enabled_rule_ids": list(request.enabled_rule_ids) if request.enabled_rule_ids is not None else None,
        "effective_rule_ids": effective_ids,
        "rules_fingerprint": _rules_fingerprint(request, effective_ids),
    }
    result = to_plain(outcome)
    if outcome.auto_platform is not None:
        settings[PLATFORM_USED_KEY] = outcome.auto_platform.used
    else:
        del result[AUTO_PLATFORM_KEY]
    if not request.raw_options.is_default():
        settings[RAW_OPTIONS_KEY] = options_to_plain(request.raw_options)
    for raw in _raw_dicts(result):
        if raw.get(RESULT_RAW_OPTIONS_KEY, _MISSING) is None:
            del raw[RESULT_RAW_OPTIONS_KEY]
    conversation = conversation_plain(outcome)
    if conversation is None:
        del result[CONVERSATION_KEY]  # 答えが無い・やり取りの無い保存は今と1文字も変えない(auto_platform と同じ)
    else:
        result[CONVERSATION_KEY] = conversation
    return {
        "schema_version": SCHEMA_VERSION,
        "saved_at": saved_at.isoformat(timespec="seconds"),
        "inputs": inputs,
        "settings": settings,
        "tools": {
            "driftpilot": driftpilot.__version__,
            "hier_config": _package_version("hier-config"),
            "patterns_fingerprint": fp.digest,
            "patterns_count": fp.count,
            "fold_version": FOLD_VERSION,
        },
        "contains_config_values": True,
        "result": result,
    }


def conversation_plain(outcome: CompareOutcome) -> dict[str, Any] | None:
    """保存の result.conversation に書く形(ADR-0179 決定2)。書かないときは None。

    書くのは、AI の答えがあり(llm の状態が done で、答えが文字)、最初の依頼・送り先の名前・やり取りのどれかに値があるときだけ。
    形は {"first_request", "profile", "turns": [{"question", "answer"}, …]}。
    """
    llm = outcome.llm
    answered = llm is not None and llm.status == "done" and isinstance(llm.answer, str)
    return _plain_conversation(outcome.conversation, answered)


def _plain_conversation(conversation: Conversation | None, answered: bool) -> dict[str, Any] | None:
    """conversation_plain の本体(答えがあるかは呼ぶ側が決める)。"""
    if conversation is None or not answered:
        return None
    if conversation.first_request is None and conversation.profile is None and not conversation.turns:
        return None
    return {
        "first_request": conversation.first_request,
        "profile": conversation.profile,
        "turns": [{"question": question, "answer": answer} for question, answer in conversation.turns],
    }


def resaved_record(record: Mapping[str, Any], conversation: Conversation | None, saved_at: datetime) -> dict[str, Any]:
    """開いた保存(読み込んだ dict)を保存し直す中身(REQ-242 §4)。元の dict は変えずに写しを返す。

    替えるのは最上位の saved_at(同じ場所に入れる)と result.conversation(conversation_plain と同じ決まりで、置き換えるか、
    足すか、消す)だけ。ほかのキー・値・並びはそのまま残す。答えがあるかは保存の result.llm で決める。
    """
    if saved_at.tzinfo is None or saved_at.utcoffset() is None:
        raise ValueError(_T["saved_at_timezone"])
    copied = copy.deepcopy(dict(record))
    copied["saved_at"] = saved_at.isoformat(timespec="seconds")
    result = copied["result"]
    llm = result.get(LLM_KEY)
    answered = isinstance(llm, dict) and llm.get("status") == "done" and isinstance(llm.get("answer"), str)
    plain = _plain_conversation(conversation, answered)
    if plain is None:
        result.pop(CONVERSATION_KEY, None)
    else:
        result[CONVERSATION_KEY] = plain
    return copied


# --- 書き出し ---


def default_filename(record: Mapping[str, Any], label: str | None = None) -> str:
    """`<saved_at の YYYYMMDD-HHMMSS>_<ラベル>.json`。ラベルの記号は `_` に置き換える。"""
    stamp = datetime.fromisoformat(record["saved_at"]).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{_UNSAFE_LABEL.sub('_', label or DEFAULT_LABEL)}.json"


def save(record: Mapping[str, Any], path: str | os.PathLike[str], *, label: str | None = None) -> Path:
    """record を UTF-8 の JSON で書き、書いたパスを返す。path が既存のディレクトリなら名前を自動で付ける。

    既にあるファイルは上書きしない(排他作成)。親ディレクトリも自動では作らない(決定3)。
    """
    target = Path(path)
    if target.is_dir():
        target = target / default_filename(record, label)
    text = json.dumps(record, ensure_ascii=False, indent=2) + "\n"
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
    except FileExistsError as exc:
        raise StorageError(_T["exists"].format(target=target)) from exc
    except FileNotFoundError as exc:
        raise StorageError(_T["no_folder"].format(target=target)) from exc
    except OSError as exc:
        raise StorageError(_T["cannot_write"].format(target=target, reason=exc.strerror or exc)) from exc
    return target


# --- 読み込み ---


def load(path: str | os.PathLike[str]) -> StoredRecord:
    """保存ファイルを読む。版が違えば UnsupportedSchemaVersion、形が違えば StorageFormatError。"""
    return load_raw(path)[1]


def load_raw(path: str | os.PathLike[str]) -> tuple[Any, StoredRecord]:
    """load と同じ決まりで読み、読み込んだ JSON の値(dict)も返す(開いた保存を保存し直すため。REQ-242 §4)。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise StorageFormatError(_T["not_utf8"].format(path=path)) from exc
    except OSError as exc:
        raise StorageError(_T["cannot_read"].format(path=path, reason=exc.strerror or exc)) from exc
    try:
        record = json.loads(text)
    except json.JSONDecodeError as exc:
        raise StorageFormatError(_T["not_json"].format(path=path, error=exc)) from exc
    return record, from_record(record)


def from_record(record: Any) -> StoredRecord:
    """build_record の形の dict を読む。版を最初に確かめ、違えば組み立てずに拒否する。"""
    if not isinstance(record, dict):
        raise StorageFormatError(_T["file_not_object"])
    found = record.get("schema_version", _MISSING)
    if found != SCHEMA_VERSION or type(found) is not str:
        raise UnsupportedSchemaVersion("schema_version", found)
    result = record.get("result")
    if isinstance(result, dict):
        found = result.get("schema_version", _MISSING)
        if found != SCHEMA_VERSION or type(found) is not str:
            raise UnsupportedSchemaVersion("result.schema_version", found)
    missing = [k for k in RECORD_KEYS if k not in record]
    if missing:
        raise StorageFormatError(_T["file_missing_items"].format(names=", ".join(missing)))
    # 再現に必要な情報(ハッシュ・指紋・版)が欠けたファイルを、正常な保存結果として受け取らない
    meta = _build(RecordMeta, {k: v for k, v in record.items() if k != "result"}, "")
    try:
        saved_at = datetime.fromisoformat(meta["saved_at"])
    except ValueError as exc:
        raise _fail("saved_at", _T["date_unreadable"].format(value=meta["saved_at"])) from exc
    if saved_at.utcoffset() is None:
        raise _fail("saved_at", _T["no_timezone"])
    if isinstance(result, dict) and LLM_KEY not in result:
        # REQ-16 より前の保存には result.llm が無い。欠けを受け入れるのはこの項目と下の exclusions・pager_marks だけで、
        # None(未記録)として読む
        result = {**result, LLM_KEY: None}
    if isinstance(result, dict) and EXCLUSIONS_KEY not in result:
        # REQ-22 より前の保存には result.exclusions が無い。その頃は比較から外す仕組みが無かったので、空として読む
        result = {**result, EXCLUSIONS_KEY: []}
    if isinstance(result, dict) and AUTO_PLATFORM_KEY not in result:
        # 機種が自動でない保存と REQ-209 より前の保存には result.auto_platform が無い。None(自動でない)として読む
        result = {**result, AUTO_PLATFORM_KEY: None}
    if isinstance(result, dict):
        result = _fill_pager_marks(result)
        result = _fill_raw_options(result)
        # REQ-242 より前の保存・答えが無い保存・やり取りの無い保存には result.conversation が無い。None として読む
        # (auto_platform と同じ扱い)。あれば形を確かめてから、(質問, 回答) の並びに直して組み立てる
        result = {**result, CONVERSATION_KEY: _read_conversation(result.get(CONVERSATION_KEY))}
    outcome = _build(CompareOutcome, result, "result")
    declared = _unread_sides(outcome)
    for side in SIDES:
        side_meta = meta["inputs"][side]
        unread = side_meta["sha256"] is None
        if unread != (side_meta["line_count"] is None):
            raise _fail(f"inputs.{side}", _T["sha_line_both"])
        # 再現に必要なハッシュの欠けを許すのは、結果が読めなかったと言っている側だけ。逆に、読めなかった側に値があるのも形の違い
        if unread and side not in declared:
            raise _fail(
                f"inputs.{side}.sha256",
                _T["null_only_unread"],
            )
        if not unread and side in declared:
            raise _fail(f"inputs.{side}.sha256", _T["hash_on_unread"])
    settings = record["settings"]
    if PLATFORM_USED_KEY in settings:
        # SettingsMeta には無いキー(今のキーを固定するため)。あれば str か null だけを受け取る
        used = settings[PLATFORM_USED_KEY]
        if used is not None and not isinstance(used, str):
            raise _fail(f"settings.{PLATFORM_USED_KEY}", _T["str_or_null"].format(value=used))
        meta["settings"][PLATFORM_USED_KEY] = used
    if RAW_OPTIONS_KEY in settings:
        # SettingsMeta には無いキー(platform_used と同じ扱い)。形と中身を設定の決まりで確かめる(文に式は入れない)
        try:
            options_from_plain(settings[RAW_OPTIONS_KEY])
        except RawOptionsError:
            raise _fail(f"settings.{RAW_OPTIONS_KEY}", _T["raw_options_bad"]) from None
        meta["settings"][RAW_OPTIONS_KEY] = settings[RAW_OPTIONS_KEY]
    if JOB_KEY in record:
        meta[JOB_KEY] = _build(JobMeta, record[JOB_KEY], JOB_KEY)
        job = record[JOB_KEY]
        if PAIRED_BY_HAND_KEY in job:
            # JobMeta には無いキー(platform_used と同じ扱い)。あれば真偽値だけを受け取る(REQ-269 §4)
            meta[JOB_KEY][PAIRED_BY_HAND_KEY] = _build(bool, job[PAIRED_BY_HAND_KEY], f"{JOB_KEY}.{PAIRED_BY_HAND_KEY}")
    return StoredRecord(meta, outcome)


def _read_conversation(value: Any) -> Any:
    """result.conversation の値を、組み立て(_build)が読む形に直す。無い・null は None。

    turns は [{"question", "answer"}, …] の並びで、各要素はオブジェクトでなければ断る(StorageFormatError)。
    質問と回答は [質問, 回答] に直し、型(文字列か)と first_request・profile の型と欠けは組み立てで確かめる。知らないキーは無視する。
    """
    where = f"result.{CONVERSATION_KEY}"
    if value is None:
        return None
    if not isinstance(value, dict):
        raise _fail(where, _T["not_object"])
    turns = value.get("turns", _MISSING)
    if turns is _MISSING:
        raise _fail(where, _T["missing_turns"])
    if not isinstance(turns, list):
        raise _fail(f"{where}.turns", _T["not_array"])
    pairs = []
    for index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            raise _fail(f"{where}.turns[{index}]", _T["not_object"])
        absent = [name for name in ("question", "answer") if name not in turn]
        if absent:
            raise _fail(f"{where}.turns[{index}]", _T["missing_items"].format(names=", ".join(absent)))
        pairs.append([turn["question"], turn["answer"]])
    return {**value, "turns": pairs}


def _fill_pager_marks(result: dict[str, Any]) -> dict[str, Any]:
    """REQ-179 より前の保存には result.segmentation.<側>.split.pager_marks が無い。その頃は印を外す仕組みが無かったので、
    0 として読む。元の dict は変えずに写しを返す。形の違い(split が無いなど)はここでは扱わず、後の検査に任せる。
    """
    segmentation = result.get("segmentation")
    if not isinstance(segmentation, dict):
        return result
    filled = dict(segmentation)
    for side in SIDES:
        seg = filled.get(side)
        if isinstance(seg, dict) and isinstance(split := seg.get("split"), dict) and PAGER_MARKS_KEY not in split:
            filled[side] = {**seg, "split": {**split, PAGER_MARKS_KEY: 0}}
    return {**result, "segmentation": filled}


def _raw_dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    """result.raw と result.show.raw のうち、辞書のもの。"""
    found = []
    if isinstance(raw := result.get("raw"), dict):
        found.append(raw)
    if isinstance(show := result.get("show"), dict) and isinstance(raw := show.get("raw"), dict):
        found.append(raw)
    return found


def _fill_raw_options(result: dict[str, Any]) -> dict[str, Any]:
    """REQ-227 より前の保存と既定の設定の保存には result.raw.options(と result.show.raw.options)が無い。None として読む。
    元の dict は変えずに写しを返す(_fill_pager_marks と同じ書き方)。

    REQ-227 の形の保存には、REQ-228 で足した項目が無い。options の中の detect_moves・algorithm は False・"standard"、
    moves・moves_skipped・algorithm_used は []・False・options.algorithm(それも無ければ "standard")を入れてから組み立てる。
    """
    filled = dict(result)
    if isinstance(raw := filled.get("raw"), dict):
        filled["raw"] = _fill_raw_dict(raw)
    show = filled.get("show")
    if isinstance(show, dict) and isinstance(raw := show.get("raw"), dict):
        filled["show"] = {**show, "raw": _fill_raw_dict(raw)}
    return filled


def _fill_raw_dict(raw: dict[str, Any]) -> dict[str, Any]:
    """result.raw(か result.show.raw)の写しに、無い項目を入れる(_fill_raw_options)。"""
    if RESULT_RAW_OPTIONS_KEY not in raw:
        return {**raw, RESULT_RAW_OPTIONS_KEY: None}
    record = raw[RESULT_RAW_OPTIONS_KEY]
    if not isinstance(record, dict):
        return raw  # 形の誤りは組み立てで断る
    plain = record.get("options")
    if isinstance(plain, dict):
        plain = {"detect_moves": False, "algorithm": "standard", **plain}
    algorithm = plain.get("algorithm", "standard") if isinstance(plain, dict) else "standard"
    filled = {"moves": [], "moves_skipped": False, "algorithm_used": algorithm, **record}
    if isinstance(plain, dict):
        filled["options"] = plain
    return {**raw, RESULT_RAW_OPTIONS_KEY: filled}


def _fail(where: str, message: str) -> StorageFormatError:
    return StorageFormatError(f"{where or _T['top_level']}: {message}")


def _build(tp: Any, value: Any, where: str) -> Any:
    """型ヒントに従って JSON の値を組み立て直す。Any は JSON の値のまま返す。"""
    if tp is Any:
        return value
    origin = get_origin(tp)
    if origin is Literal:
        # 型も比べる(1 == True のため、`in` だけでは Literal[True] に 1 が通る)
        if not any(type(value) is type(arg) and value == arg for arg in get_args(tp)):
            raise _fail(where, _T["unexpected_value"].format(value=value))
        return value
    if origin in (Union, types.UnionType):
        args = get_args(tp)
        if value is None and type(None) in args:
            return None
        for arg in (a for a in args if a is not type(None)):
            try:
                return _build(arg, value, where)
            except StorageFormatError:
                continue
        raise _fail(where, _T["no_type_matches"].format(value=value))
    if dataclasses.is_dataclass(tp) or is_typeddict(tp):
        if not isinstance(value, dict):
            raise _fail(where, _T["not_object"])
        hints = get_type_hints(tp)
        names = [f.name for f in dataclasses.fields(tp)] if dataclasses.is_dataclass(tp) else list(hints)
        # 欠けた項目を既定値で補わない(欠けは「無い」ではなく、形が違うことを意味する)。知らないキーは無視する
        absent = [n for n in names if n not in value]
        if absent:
            raise _fail(where, _T["missing_items"].format(names=", ".join(absent)))
        built = {n: _build(hints[n], value[n], f"{where}.{n}" if where else n) for n in names}
        return tp(**built) if dataclasses.is_dataclass(tp) else built
    if origin is list:
        if not isinstance(value, list):
            raise _fail(where, _T["not_array"])
        (item,) = get_args(tp)
        return [_build(item, v, f"{where}[{i}]") for i, v in enumerate(value)]
    if origin is tuple:
        if not isinstance(value, list):
            raise _fail(where, _T["not_array"])
        args = get_args(tp)
        if len(args) == 2 and args[1] is Ellipsis:
            return tuple(_build(args[0], v, f"{where}[{i}]") for i, v in enumerate(value))
        if len(args) != len(value):
            raise _fail(where, _T["count_differs"].format(actual=len(value), expected=len(args)))
        return tuple(_build(a, v, f"{where}[{i}]") for i, (a, v) in enumerate(zip(args, value)))
    if origin is dict:
        if not isinstance(value, dict):
            raise _fail(where, _T["not_object"])
        key_tp, value_tp = get_args(tp)
        return {_build(key_tp, k, f"{where}.{k}"): _build(value_tp, v, f"{where}.{k}") for k, v in value.items()}
    if tp is bool:
        if not isinstance(value, bool):
            raise _fail(where, _T["not_bool"])
        return value
    if tp is int:
        if not isinstance(value, int) or isinstance(value, bool):
            raise _fail(where, _T["not_int"])
        return value
    if tp is str:
        if not isinstance(value, str):
            raise _fail(where, _T["not_str"])
        return value
    raise _fail(where, _T["unsupported_type"].format(tp=tp))
