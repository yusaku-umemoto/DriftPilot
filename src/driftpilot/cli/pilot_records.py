"""ほかの Pilot から呼ぶ入口の、作業の記録の退避の 4 つの操作(ADR-0253。REQ-386)。

- `pilot-call records-list`: 更新に失敗したときの記録(app_logs)の件数と大きさを数える(何も変えない)
- `pilot-call records-export`: output_dir に DriftPilot.zip(manifest.json と update/<名前>)を書く(元は消さない)
- `pilot-call records-delete`: 記録を消す(method は delete=完全に消す・trash=ごみ箱へ)。manifest があれば、その一覧の中で今も同じ物だけ
- `pilot-call records-import`: 書き出した ZIP から記録を戻す(今ある物は消さない・上書きしない)

扱うのは「準備の場所のすぐ下の last-failed.log と last-failed (N).log」だけ(N は 2 以上)。準備の場所は配った形(Windows の zip・Mac のアプリ)で
動いているときだけ求められる。求められないときは対象 0 件(誤りにしない)。
標準出力・標準エラー・誤りの文には、記録の名前・中身・準備の場所のパスを出さない(件数と大きさだけ。書き出した ZIP のパスは除く)。

manifest.json の形(シリーズで 2026-11-21 に決まった名前。判断の記録 2):
    {"schema": "pilot.records-part", "schema_version": "1", "product": "DriftPilot", "version": <DriftPilot の版>,
     "created_at": <UTC の ISO 8601。秒まで・+00:00 付き>,
     "files": [{"path": "update/<名前>", "category": "app_logs", "size": <整数>, "sha256": <小文字の 16 進 64 字>}]}
読む側(records-delete の manifest・records-import)が当てにするのはこの 6 つのキーと files の中の 4 つのキーだけ。知らないキーは無視する。
"""

import argparse
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import driftpilot
from driftpilot.cli import trash
from driftpilot.cli.pilot_call import (
    _M,
    SCHEMA,
    SCHEMA_VERSION,
    RECORDS_OPERATIONS,
    EXIT_CANCELLED,
    Reject,
    StdinReader,
    StdinWatcher,
    WriteFailed,
    _CallState,
    _check_caller,
    _fail,
    _interrupted,
    _is_absolute,
    _output_dir,
    _read_envelope,
    correlation_id_of,
    default_stdin_reader,
    emit,
    remove_files,
)

APP_LOGS = "app_logs"
ZIP_NAME = "DriftPilot.zip"
MANIFEST_NAME = "manifest.json"
RECORDS_SCHEMA = "pilot.records-part"
RECORDS_SCHEMA_VERSION = "1"
PRODUCT = "DriftPilot"
ZIP_DIR = "update"

MANIFEST_LIMIT = 1_048_576  # manifest の大きさの上限(1 MiB)
ZIP_LIMIT = 64 * 1024 * 1024  # 戻す ZIP のファイルの大きさの上限(ADR-0253 決定4)
ZIP_MEMBERS_LIMIT = 1_000  # 戻す ZIP の中のファイルの数の上限
MEMBER_LIMIT = 16 * 1024 * 1024  # 戻す ZIP の中の 1 つの、展開した大きさの上限

TARGET_NAME = re.compile(r"last-failed\.log|last-failed \((?:[2-9]|[1-9][0-9]+)\)\.log")  # fullmatch で使う(数字は ASCII だけ)
TARGET_NUMBER = re.compile(r"last-failed \(([0-9]+)\)\.log")
ZIP_PATH = re.compile(r"update/(last-failed\.log|last-failed \((?:[2-9]|[1-9][0-9]+)\)\.log)")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
DRIVE_PATTERN = re.compile(r"[A-Za-z]:")
SYMLINK_MODE = 0o120000
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)

METHODS = ("delete", "trash")
PARAM_NAMES = {
    "records-list": ("categories",),
    "records-export": ("categories",),
    "records-import": ("categories",),
    "records-delete": ("manifest", "categories", "method"),
}


# --- 差し替えられる部品(テストでは本物の場所・ごみ箱・時計を使わない) ---


def records_root() -> Path | None:
    """準備の場所。配った形(windows_zip・mac_app)で動いていない・求められないときは None(対象 0 件)。テストで差し替える。"""
    from driftpilot.update import check, prepare
    from driftpilot.update.errors import UpdateError

    kind = check.install_kind()
    if kind not in prepare.SUPPORTED_KINDS:
        return None
    try:
        return prepare.staging_root(kind, prepare.install_target(kind))
    except (UpdateError, OSError):
        return None


def _now() -> datetime:
    """今の日時(manifest の created_at)。テストで差し替える。"""
    return datetime.now(UTC)


def _unlink(path: Path) -> None:
    """完全に消す(method が delete のとき)。テストで差し替える。"""
    path.unlink()


# --- 依頼 ---


@dataclass(frozen=True)
class RecordsRequest:
    operation: str
    correlation_id: str
    categories: tuple[str, ...]  # 頼まれた種類(重なりなし。省略は app_logs だけ)
    output_dir: Path | None = None  # records-export だけ
    part: Path | None = None  # records-import だけ(戻す ZIP)
    manifest: Path | None = None  # records-delete だけ
    method: str = ""  # records-delete だけ


class Stopped(Exception):
    """標準入力が閉じられて止める。"""


def _categories(raw: dict[str, Any]) -> tuple[str, ...]:
    """params.categories。省略は app_logs だけ。文字列の配列でなければ request_invalid。重なった名前は 1 つにする。"""
    if "categories" not in raw:
        return (APP_LOGS,)
    value = raw["categories"]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise Reject("request_invalid", _M["invalid_param"].format(name="categories"))
    return tuple(dict.fromkeys(value))


def _inputs_part(data: dict[str, Any]) -> Path:
    """records-import の inputs は、役 part が 1 つだけ(絶対パス)。ファイルが無い・普通のファイルでないときは input_invalid。"""
    inputs = data.get("inputs")
    invalid = Reject("request_invalid", _M["invalid_inputs_records"])
    if not isinstance(inputs, list) or len(inputs) != 1 or not isinstance(inputs[0], dict):
        raise invalid
    if inputs[0].get("role") != "part" or not _is_absolute(inputs[0].get("path")):
        raise invalid
    path = Path(inputs[0]["path"])
    try:
        usable = path.is_file()
    except OSError:
        usable = False
    if not usable:
        raise Reject("input_invalid", _M["records_part_invalid"])
    return path


def _params(operation: str, data: dict[str, Any]) -> tuple[tuple[str, ...], Path | None, str]:
    """params を確かめ、(categories, manifest, method) を返す。"""
    raw = data.get("params", {})
    if not isinstance(raw, dict):
        raise Reject("request_invalid", _M["invalid_param"].format(name="params"))
    allowed = PARAM_NAMES[operation]
    if unknown := [name for name in raw if name not in allowed]:
        raise Reject("request_invalid", _M["unknown_param"].format(count=len(unknown), allowed=", ".join(allowed)))
    categories = _categories(raw)
    if operation != "records-delete":
        return categories, None, ""
    method = raw.get("method")
    if method not in METHODS:
        raise Reject("request_invalid", _M["invalid_param"].format(name="method"))
    manifest = raw.get("manifest")
    if "manifest" in raw and not _is_absolute(manifest):
        raise Reject("request_invalid", _M["invalid_param"].format(name="manifest"))
    if "manifest" not in raw and "categories" not in raw:
        raise Reject("request_invalid", _M["records_target_missing"])
    return categories, Path(manifest) if manifest is not None else None, method


def validate(operation: str, path: str) -> RecordsRequest:
    """依頼のファイルを確かめる(compare と同じ順)。合わなければ Reject(読めた correlation_id を持たせる)。"""
    data = _read_envelope(path)
    try:
        if data.get("schema") != SCHEMA or data.get("schema_version") != SCHEMA_VERSION:
            raise Reject("unsupported_schema_version", _M["unsupported_schema"])
        if data.get("operation") != operation:
            raise Reject("operation_mismatch", _M["unsupported_operation_records"].format(operation=operation))
        _check_caller(data)
        part = _inputs_part(data) if operation == "records-import" else None
        output_dir = _output_dir(data) if operation == "records-export" else None
        if data.get("llm", "off") != "off":
            raise Reject("llm_not_supported", _M["llm_not_supported"])
        if data.get("secret_on_stdin", False) is not False:
            raise Reject("secret_not_supported", _M["secret_not_supported"])
        categories, manifest, method = _params(operation, data)
    except Reject as exc:
        exc.correlation_id = correlation_id_of(data)
        raise
    return RecordsRequest(operation, correlation_id_of(data), categories, output_dir, part, manifest, method)


# --- 対象のファイル(ADR-0253 決定2) ---


@dataclass(frozen=True)
class Target:
    name: str
    path: Path
    size: int


def _name_order(name: str) -> tuple[int, str]:
    """last-failed.log を先に、last-failed (N).log を N の小さい順に並べる鍵。"""
    match = TARGET_NUMBER.fullmatch(name)
    return (int(match.group(1)) if match else 1, name)


def list_targets(root: Path | None) -> list[Target]:
    """準備の場所のすぐ下の、対象の名前の普通のファイル(シンボリックリンク・フォルダーは除く)。名前の順。"""
    if root is None:
        return []
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found: list[Target] = []
    for name in names:
        if TARGET_NAME.fullmatch(name) is None:
            continue
        path = root / name
        try:
            info = path.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode):
            found.append(Target(name, path, info.st_size))
    return sorted(found, key=lambda target: _name_order(target.name))


def _zip_path_of(name: str) -> str:
    return f"{ZIP_DIR}/{name}"


# --- manifest ---


class ManifestInvalid(Exception):
    """manifest が読めない・形が合わない。"""


@dataclass(frozen=True)
class Entry:
    path: str
    category: str
    size: int
    sha256: str


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_iso_with_zone(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text).tzinfo is not None
    except ValueError:
        return False


def parse_manifest(raw: bytes) -> dict[str, Entry]:
    """manifest の中身を読む。返すのは path → 項目(形の合う項目だけ。同じ path は最初の 1 つ)。
    6 つのキーのどれかが欠けている・型か値が違う・files が配列でないときは ManifestInvalid。"""
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, RecursionError):
        raise ManifestInvalid from None
    if not isinstance(doc, dict):
        raise ManifestInvalid
    if doc.get("schema") != RECORDS_SCHEMA or doc.get("schema_version") != RECORDS_SCHEMA_VERSION:
        raise ManifestInvalid
    if doc.get("product") != PRODUCT:
        raise ManifestInvalid
    if not isinstance(doc.get("version"), str) or not _is_iso_with_zone(doc.get("created_at")):
        raise ManifestInvalid
    files = doc.get("files")
    if not isinstance(files, list):
        raise ManifestInvalid
    entries: dict[str, Entry] = {}
    for item in files:
        if not isinstance(item, dict):
            continue
        path, category, size, digest = item.get("path"), item.get("category"), item.get("size"), item.get("sha256")
        if not isinstance(path, str) or not isinstance(category, str) or not _is_int(size) or size < 0:
            continue
        if not isinstance(digest, str) or SHA256_PATTERN.fullmatch(digest) is None:
            continue
        entries.setdefault(path, Entry(path, category, size, digest))
    return entries


def _manifest_text(files: list[tuple[str, int, str]]) -> str:
    created = _now().astimezone(UTC).replace(microsecond=0).isoformat()
    doc = {
        "schema": RECORDS_SCHEMA,
        "schema_version": RECORDS_SCHEMA_VERSION,
        "product": PRODUCT,
        "version": driftpilot.__version__,
        "created_at": created,
        "files": [
            {"path": _zip_path_of(name), "category": APP_LOGS, "size": size, "sha256": digest} for name, size, digest in files
        ],
    }
    return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- 共通の部品 ---


@dataclass(frozen=True)
class Done:
    """操作の結果。app_logs は summary.categories.app_logs の中身(頼まれていないときは None)。"""

    outcome: str
    app_logs: dict[str, Any] | None
    files: tuple[str, ...] = ()


def _check_stop(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise Stopped


def _summary(call: RecordsRequest, app_logs: dict[str, Any] | None) -> dict[str, Any]:
    """summary.categories。頼まれた種類の順。app_logs 以外の名前は {"skipped": true}(断らない)。"""
    block: dict[str, Any] = {}
    for name in call.categories:
        if name != APP_LOGS:
            block[name] = {"skipped": True}
        elif app_logs is not None:
            block[name] = app_logs
    return {"categories": block}


# --- records-list ---


def _list(call: RecordsRequest, cancel: threading.Event) -> Done:
    if APP_LOGS not in call.categories:
        return Done("ok", None)
    _check_stop(cancel)
    targets = list_targets(records_root())
    count = len(targets)
    return Done(
        "ok",
        {
            "count": count,
            "bytes": sum(target.size for target in targets),
            "where": _M["where_app_logs"],
            "deletable_count": count,
            "kept_count": 0,
        },
    )


# --- records-export ---


def _export(call: RecordsRequest, cancel: threading.Event, state: _CallState) -> Done:
    assert call.output_dir is not None
    if APP_LOGS not in call.categories:
        return Done("ok", None)
    targets = list_targets(records_root())
    blobs: list[tuple[str, bytes]] = []
    unreadable = 0
    for target in targets:
        _check_stop(cancel)
        try:
            blobs.append((target.name, target.path.read_bytes()))
        except OSError:
            unreadable += 1
    if unreadable:
        raise Reject("input_unreadable", _M["records_unreadable"].format(count=unreadable))
    meta = [(name, len(data), _sha256(data)) for name, data in blobs]
    zip_path = call.output_dir / ZIP_NAME
    try:
        with open(zip_path, "xb") as fh:
            state.written.append(zip_path)
            with zipfile.ZipFile(fh, "w", zipfile.ZIP_DEFLATED) as archive:
                _write_member(archive, MANIFEST_NAME, _manifest_text(meta).encode("utf-8"))
                for name, data in blobs:
                    _check_stop(cancel)
                    _write_member(archive, _zip_path_of(name), data)
    except OSError as exc:
        raise WriteFailed(zip_path, exc) from exc
    _check_stop(cancel)  # 書き終えた後・知らせる前にも見る(止めたら ZIP を残さない)
    return Done("ok", {"count": len(blobs), "bytes": sum(size for _, size, _ in meta)}, (str(zip_path),))


def _write_member(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, data)


# --- records-delete ---


def _read_manifest_file(path: Path) -> dict[str, Entry]:
    """records-delete の manifest(ZIP から出した物)を読む。読めない・1 MiB を超える・形が合わないときは input_invalid。"""
    invalid = Reject("input_invalid", _M["records_manifest_invalid"])
    try:
        if not path.is_file():
            raise invalid
        with path.open("rb") as fh:
            raw = fh.read(MANIFEST_LIMIT + 1)
    except OSError:
        raise invalid from None
    if len(raw) > MANIFEST_LIMIT:
        raise invalid
    try:
        return parse_manifest(raw)
    except ManifestInvalid:
        raise invalid from None


@dataclass
class _Counts:
    deleted: int = 0
    in_use: int = 0
    trash_unavailable: int = 0
    changed: int = 0


def _same_as_listed(target: Path, entry: Entry) -> str:
    """今のファイルが一覧の物と同じか。"same"・"changed"・"gone"(もう無い)・"in_use"(確かめのために読めない)。"""
    try:
        info = target.lstat()
    except FileNotFoundError:
        return "gone"
    except OSError:
        return "in_use"
    if not stat.S_ISREG(info.st_mode) or info.st_size != entry.size:
        return "changed"
    try:
        data = target.read_bytes()
    except FileNotFoundError:
        return "gone"
    except OSError:
        return "in_use"
    return "same" if len(data) == entry.size and _sha256(data) == entry.sha256 else "changed"


def _remove(path: Path, method: str, counts: _Counts) -> None:
    if method == "trash":
        try:
            moved = bool(trash.move_to_trash(path))
        except Exception:  # noqa: BLE001 - ごみ箱の処理の失敗は、使えなかったものとして数える(完全には消さない)
            moved = False
        if moved:
            counts.deleted += 1
        else:
            counts.trash_unavailable += 1
        return
    try:
        _unlink(path)
    except FileNotFoundError:
        return  # もう無い物は、どこにも数えない
    except OSError:
        counts.in_use += 1
        return
    counts.deleted += 1


def _delete(call: RecordsRequest, cancel: threading.Event) -> Done:
    wanted = APP_LOGS in call.categories
    root = records_root()
    counts = _Counts()
    if call.manifest is not None:
        entries = _read_manifest_file(call.manifest)
        _check_stop(cancel)
        listed = sorted(
            (
                entry
                for entry in entries.values()
                if wanted and entry.category == APP_LOGS and ZIP_PATH.fullmatch(entry.path) is not None
            ),
            key=lambda entry: _name_order(entry.path[len(ZIP_DIR) + 1 :]),
        )
        for entry in listed:
            _check_stop(cancel)
            if root is None:
                continue
            target = root / entry.path[len(ZIP_DIR) + 1 :]
            verdict = _same_as_listed(target, entry)
            if verdict == "same":
                _remove(target, call.method, counts)
            elif verdict == "changed":
                counts.changed += 1
            elif verdict == "in_use":
                counts.in_use += 1
    elif wanted:
        for target_file in list_targets(root):
            _check_stop(cancel)
            _remove(target_file.path, call.method, counts)
    if not wanted:
        return Done("ok", None)
    review = counts.in_use or counts.trash_unavailable or counts.changed
    return Done(
        "review" if review else "ok",
        {
            "deleted": counts.deleted,
            "kept": {"count": 0, "codes": []},
            "in_use": counts.in_use,
            "trash_unavailable": counts.trash_unavailable,
            "changed": counts.changed,
        },
    )


# --- records-import ---


def _part_invalid() -> Reject:
    return Reject("input_invalid", _M["records_part_invalid"])


def _read_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes | None:
    """ZIP の中の 1 つを limit+1 バイトまで読む。読めない(壊れている・暗号化・未対応の圧縮)ときは None。"""
    try:
        with archive.open(info) as fh:
            return fh.read(limit + 1)
    except Exception:  # noqa: BLE001 - zipfile は BadZipFile・RuntimeError・NotImplementedError・zlib.error・EOFError などを上げる
        return None


def _unsafe_name(name: str) -> bool:
    """絶対パス・ドライブの文字・\\ を含む・.. の区切りを含む。"""
    if name.startswith("/") or DRIVE_PATTERN.match(name) or "\\" in name:
        return True
    return ".." in name.split("/")


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == SYMLINK_MODE


def _publish(temp: str, destination: Path) -> bool:
    """一時ファイルを destination という名前にする。上書きしない。名前がふさがっていたら偽。"""
    try:
        if os.name == "nt":
            os.rename(temp, destination)  # Windows では、同じ名前があれば FileExistsError
        else:
            os.link(temp, destination)  # 同じ名前があれば FileExistsError
    except FileExistsError:
        return False
    return True


def _create_new(root: Path, name: str, data: bytes) -> bool:
    """root の中に name で、上書きせずに置く。同じ名前がもうあれば偽(何も置かない)。書けないときは OSError。"""
    descriptor, temp = tempfile.mkstemp(dir=root, prefix=".restore-")
    try:
        with os.fdopen(descriptor, "wb") as fh:
            fh.write(data)
        return _publish(temp, root / name)
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass


def _has_same_content(root: Path, data: bytes) -> bool:
    digest = _sha256(data)
    for target in list_targets(root):
        if target.size != len(data):
            continue
        try:
            if _sha256(target.path.read_bytes()) == digest:
                return True
        except OSError:
            continue
    return False


def _free_name(root: Path) -> str:
    number = 2
    while os.path.lexists(root / f"last-failed ({number}).log"):
        number += 1
    return f"last-failed ({number}).log"


@dataclass
class _Restored:
    restored: int = 0
    same: int = 0
    renamed: int = 0
    skipped: int = 0


def _restore_one(root: Path, name: str, data: bytes, counts: _Restored) -> None:
    """1 つ戻す。同じ名前が無ければ置く・同じ中身があれば何もしない・違えば空いている名前で置く。"""
    destination = root / name
    if not os.path.lexists(destination):
        if _create_new(root, name, data):
            counts.restored += 1
            return
    elif _same_name_content(destination, data):
        counts.same += 1
        return
    if _has_same_content(root, data):  # 同じ並びのほかの名前に同じ中身があれば、2 回戻しても数が増えない
        counts.same += 1
        return
    while True:
        free = _free_name(root)
        if _create_new(root, free, data):
            counts.renamed += 1
            return


def _same_name_content(path: Path, data: bytes) -> bool:
    try:
        return path.read_bytes() == data
    except OSError:
        return False


def _checked_members(
    archive: zipfile.ZipFile, entries: dict[str, Entry], counts: _Restored, cancel: threading.Event
) -> list[tuple[str, bytes]]:
    """ZIP の中を 1 つずつ確かめる。合う物の (名前, 中身) を返す。合わない物は counts.skipped に数える。"""
    good: list[tuple[str, bytes]] = []
    present = {info.filename for info in archive.infolist()}  # ZIP にある名前(合わなくて skipped にした物も含む)
    seen: set[str] = set()
    for info in archive.infolist():
        if info.filename == MANIFEST_NAME and info.filename not in seen:
            seen.add(info.filename)
            continue
        if info.is_dir():
            continue  # フォルダーの項目は数えない
        _check_stop(cancel)
        names = {info.filename, info.orig_filename}
        if any(_unsafe_name(name) for name in names) or _is_symlink(info):
            counts.skipped += 1
            continue
        match = ZIP_PATH.fullmatch(info.filename)
        if match is None or info.filename in seen:
            counts.skipped += 1
            continue
        seen.add(info.filename)
        entry = entries.get(info.filename)
        if entry is None or entry.category != APP_LOGS:
            counts.skipped += 1
            continue
        if info.file_size != entry.size:
            counts.skipped += 1
            continue
        data = _read_member(archive, info, MEMBER_LIMIT)
        if data is None or len(data) != entry.size or _sha256(data) != entry.sha256:
            counts.skipped += 1
            continue
        good.append((match.group(1), data))
    for entry in entries.values():  # manifest に載っているのに ZIP に無い物
        if entry.category == APP_LOGS and ZIP_PATH.fullmatch(entry.path) is not None and entry.path not in present:
            counts.skipped += 1
    return sorted(good, key=lambda item: _name_order(item[0]))


def _open_part(path: Path) -> tuple[zipfile.ZipFile, dict[str, Entry]]:
    """戻す ZIP を開き、上限と manifest を確かめる。合わなければ input_invalid(何も戻さない)。"""
    try:
        if path.stat().st_size > ZIP_LIMIT:
            raise _part_invalid()
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError, ValueError, EOFError, NotImplementedError, OverflowError):
        raise _part_invalid() from None
    try:
        members = archive.infolist()
        if len(members) > ZIP_MEMBERS_LIMIT or any(info.file_size > MEMBER_LIMIT for info in members):
            raise _part_invalid()
        manifest = next((info for info in members if info.filename == MANIFEST_NAME), None)
        raw = _read_member(archive, manifest, MANIFEST_LIMIT) if manifest is not None else None
        if raw is None or len(raw) > MANIFEST_LIMIT:
            raise _part_invalid()
        try:
            return archive, parse_manifest(raw)
        except ManifestInvalid:
            raise _part_invalid() from None
    except BaseException:
        archive.close()
        raise


def _import(call: RecordsRequest, cancel: threading.Event) -> Done:
    assert call.part is not None
    archive, entries = _open_part(call.part)
    counts = _Restored()
    with archive:
        if APP_LOGS not in call.categories:
            return Done("ok", None)
        good = _checked_members(archive, entries, counts, cancel)
    root = records_root()
    if root is None:
        counts.skipped += len(good)
        good = []
    elif good:
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError:
            counts.skipped += len(good)
            good = []
    for name, data in good:
        _check_stop(cancel)
        assert root is not None
        try:
            _restore_one(root, name, data, counts)
        except OSError:
            counts.skipped += 1  # OS が断った物は、数えて続ける
    return Done(
        "review" if counts.skipped else "ok",
        {"restored": counts.restored, "same": counts.same, "renamed": counts.renamed, "skipped": counts.skipped},
    )


# --- 入口 ---


def run_records_call(args: argparse.Namespace, stdin_reader: StdinReader | None = None) -> int:
    state = _CallState()
    try:
        return _run(args, stdin_reader, state)
    except KeyboardInterrupt:
        return _interrupted(state)


def _run(args: argparse.Namespace, stdin_reader: StdinReader | None, state: _CallState) -> int:
    operation = args.pilot_call_command
    assert operation in RECORDS_OPERATIONS
    try:
        call = validate(operation, args.request)
    except Reject as exc:
        return _fail(exc.code, exc.message, exc.correlation_id)
    cid = call.correlation_id
    state.correlation_id = cid
    emit({"type": "accepted", "correlation_id": cid})

    reader = stdin_reader if stdin_reader is not None else default_stdin_reader()
    watcher = StdinWatcher(reader) if reader is not None else None
    if watcher is not None:
        watcher.start()
    cancel = watcher.closed if watcher is not None else threading.Event()
    try:
        _check_stop(cancel)
        if operation == "records-list":
            done = _list(call, cancel)
        elif operation == "records-export":
            done = _export(call, cancel, state)
        elif operation == "records-delete":
            done = _delete(call, cancel)
        else:
            done = _import(call, cancel)
        emit(
            {
                "type": "result",
                "correlation_id": cid,
                "outcome": done.outcome,
                "summary": _summary(call, done.app_logs),
                "files": list(done.files),
            }
        )
        return 1 if done.outcome == "review" else 0
    except Reject as exc:
        remove_files(state.written)
        return _fail(exc.code, exc.message, cid)
    except WriteFailed as exc:
        remove_files(state.written)
        return _fail("write_failed", _M["write_failed"].format(path=exc.path, reason=exc.reason), cid)
    except Stopped:
        remove_files(state.written)
        return _fail("stopped_by_caller", _M["cancelled_records"], cid, EXIT_CANCELLED)
    except Exception as exc:  # noqa: BLE001 - 受け付けた後の予期しない失敗は、書きかけを消して error の行で終える
        remove_files(state.written)
        return _fail("internal_error", _M["internal_error"].format(error_type=type(exc).__name__), cid)
