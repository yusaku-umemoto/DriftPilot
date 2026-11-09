"""AI の接続のプロファイルと指示文のプロファイルを、1つのファイルにまとめて書き出す・取り込む(ADR-0082・REQ-119)。

ファイルの形(JSON。UTF-8・改行 \\n):
    {"format": "driftpilot-profiles", "version": 1,
     "connections": [{"name", "api_format", "model", "base_url", "auth_header", "wire", "custom", "timeout_seconds"}],
     "prompts": [{"name", "template"}]}

- 許す項目はこれだけ。知らない項目が1つでもあれば全部断る(api_key・cli_path・env_suffix・id・active も)。
  wire の中は llm-profiles.json と同じ名前と形(max_output_tokens・wire_names・thinking・thinking_param・query)、
  custom の中はちょうど5つの欄(url・auth・headers・body・answer_path)
- キーは書き出さない・取り込まない(ADR-0082 決定2)。書き出す前と取り込む前に、木を全部たどってキーらしいもの
  (項目の名前が is_keylike に当たる・文字列の値が "sk-" で始まるか "Bearer " を含む・custom の headers に認証らしい名前の
  見出しがある)を探し、あれば断る(check_keys)。文には場所と、何件目の接続か指示文か、一番上の項目の名前、理由の種類だけを
  入れる(値も、ファイルにある項目の名前も入れない)
- 書き出し(write_bundle)は llm-profiles.json と prompt-profiles.json だけを読む。保管庫・キーの環境変数・config.toml は読まない。
  最初から入っている指示文は書かない
- 取り込み(read_bundle → plan_names → import_bundle)は、読むときの決まりを llm_profiles・prompt_profiles と同じにし、
  1つでも違えば何も足さない。名前が重なれば「<名前> （取り込み）」(さらに重なれば番号)で足す。新しい id を作り、
  既存のプロファイルと使用中は変えない。取り込んだ接続にはキーが無い
- 取り込む接続があるのに llm-profiles.json が無いときは、呼ぶ側から受け取った処理(ensure_first)で先に1件目
  (id "default"・名前「既定」・使用中)を作ってから足す。名前はそのとき「既定」が既にあるものとして決める(ADR-0184 決定1)

読み書きを呼ぶのは画面の論理(screen_model/settings_model.py)だけ。
"""

import hashlib
import itertools
import json
import os
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from collections.abc import Callable
from typing import Any

from driftpilot.llm_analyzer.custom_http import CUSTOM_FIELDS, is_keylike_header
from driftpilot.llm_analyzer.wire import is_keylike, parse_json_text
from driftpilot.llm_profiles import (
    DEFAULT_PROFILE_NAME,
    LlmProfile,
    LlmProfiles,
    load_profiles,
    profile_from_item,
    wire_json,
)
from driftpilot.llm_profiles import add_imported_profiles as _add_profiles
from driftpilot.llm_settings import SettingsError
from driftpilot.messages import imported_profile_name
from driftpilot.prompt_profiles import (
    BUILTIN_PROFILES,
    PromptProfile,
    PromptProfiles,
    add_imported_prompt_profiles,
    load_prompt_profiles,
    prompt_from_item,
)

FORMAT = "driftpilot-profiles"
VERSION = 1
MAX_BYTES = 5 * 1024 * 1024
# 許す項目の名前
TOP_FIELDS = ("format", "version", "connections", "prompts")
CONNECTION_FIELDS = ("name", "api_format", "model", "base_url", "auth_header", "wire", "custom", "timeout_seconds")
CONNECTION_REQUIRED = ("name", "api_format", "model")
WIRE_FIELDS = ("max_output_tokens", "wire_names", "thinking", "thinking_param", "query")
PROMPT_FIELDS = ("name", "template")
# キーらしいものの種類と、断るときの文(値と、ファイルにある項目の名前は入れない)
_KEY_TEXT = {
    "name": "にキーらしい名前の項目があります",
    "value": "にキーらしい値（sk- で始まる値・「Bearer 」を含む値）があります",
    "header": "の headers に認証の見出しがあります",
}
_NOT_ALLOWED = "API キー・cli_path・env_suffix・id・active は入れられません"


class BundleChanged(SettingsError):
    """確かめた後に、ファイルか取り込む先(決めた名前)が変わった。"""


@dataclass(frozen=True)
class Bundle:
    """検査済みの中身。接続と指示文の id は ""(足すときに作る)。digest はファイルのバイト列の sha256。"""

    connections: tuple[LlmProfile, ...]
    prompts: tuple[PromptProfile, ...]
    digest: str


@dataclass(frozen=True)
class ImportResult:
    """足したもの。prompt_error は、接続を足した後に指示文を書けなかったときの理由(接続は足してある)。"""

    connections: tuple[LlmProfile, ...]
    prompts: tuple[PromptProfile, ...]
    prompt_error: str | None = None


def _name_key(name: str) -> str:
    return name.strip().casefold()


# --- キーの確かめ(書き出し・取り込みの両方) ---


def _key_kind(value: Any) -> str | None:
    """木の中のキーらしいものの種類("name"・"value")。無ければ None。名前と値は返さない。"""
    if isinstance(value, dict):
        for name, item in value.items():
            if isinstance(name, str) and is_keylike(name):
                return "name"
            if (kind := _key_kind(item)) is not None:
                return kind
    elif isinstance(value, list):
        for item in value:
            if (kind := _key_kind(item)) is not None:
                return kind
    elif isinstance(value, str) and (value.startswith("sk-") or "Bearer " in value):
        return "value"
    return None


def _item_key(item: Any) -> tuple[str, str] | None:
    """接続・指示文の1件の中の (一番上の項目の名前の頭, 種類)。項目の名前がキーらしいときは名前を返さない。"""
    if not isinstance(item, dict):
        kind = _key_kind(item)
        return None if kind is None else ("", kind)
    for field, value in item.items():
        if isinstance(field, str) and is_keylike(field):
            return "", "name"
        if (kind := _key_kind(value)) is not None:
            return f".{field}", kind
        if field == "custom" and isinstance(value, dict) and isinstance(value.get("headers"), dict):
            if any(isinstance(name, str) and is_keylike_header(name) for name in value["headers"]):
                return f".{field}", "header"
    return None


def _find_key(data: Any) -> tuple[str, str] | None:
    """(場所, 種類)。場所は「connections[0].custom」「prompts[1].template」の形で、一番外なら ""。"""
    if not isinstance(data, dict):
        kind = _key_kind(data)
        return None if kind is None else ("", kind)
    for name, value in data.items():
        if isinstance(name, str) and is_keylike(name):
            return "", "name"
        if name in ("connections", "prompts") and isinstance(value, list):
            for index, item in enumerate(value):
                if (found := _item_key(item)) is not None:
                    return f"{name}[{index}]{found[0]}", found[1]
        elif (kind := _key_kind(value)) is not None:
            return "", kind
    return None


def check_keys(data: Any, where: str) -> None:
    """キーらしいものがあれば SettingsError(where と場所と理由の種類だけ)。判定は簡単な形(努力義務。REQ-119 §3)。"""
    try:
        found = _find_key(data)
    except RecursionError:
        raise SettingsError(f"{where}: 入れ子が深すぎます") from None
    if found is not None:
        place, kind = found
        raise SettingsError(f"{where}: {place or '一番外'} {_KEY_TEXT[kind]}。API キーはファイルに入れられません")


# --- 書き出し ---


def _connection_json(p: LlmProfile) -> dict[str, Any]:
    """1件の接続。base_url・auth_header・wire はいつも書き、custom・timeout_seconds は値があるときだけ書く。

    id・cli_path・env_suffix は書かない(ADR-0082 決定3)。cli_effort(Thinking の深さ)も書かない(ADR-0157 決定12)。
    """
    item: dict[str, Any] = {
        "name": p.name,
        "api_format": p.api_format,
        "model": p.model or "",
        "base_url": p.base_url,
        "auth_header": p.auth_header,
        "wire": wire_json(p.wire),
    }
    if p.custom is not None:
        item["custom"] = p.custom.to_json()
    if p.timeout_seconds is not None:
        item["timeout_seconds"] = p.timeout_seconds
    return item


def bundle_data(*, llm_path: Path, prompt_path: Path) -> dict[str, Any]:
    """書き出す中身。llm-profiles.json と prompt-profiles.json だけを読む(保管庫・環境変数・config.toml は読まない)。

    llm-profiles.json が無ければ接続は 0 件。読めない・キーらしいものがあるときは SettingsError。
    最初から入っている指示文は入れない(利用者のものだけ)。
    """
    profiles = load_profiles(llm_path)
    prompts = load_prompt_profiles(prompt_path)
    data = {
        "format": FORMAT,
        "version": VERSION,
        "connections": [_connection_json(p) for p in (profiles.profiles if profiles is not None else ())],
        "prompts": [{"name": p.name, "template": p.template} for p in prompts.profiles],
    }
    check_keys(data, "書き出す内容")
    return data


def _write_text(path: Path, text: str) -> None:
    """同じフォルダの一時ファイルに書いてから os.replace で置き換える。失敗したら一時ファイルを消す(元のファイルは残る)。"""
    tmp: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False
        ) as fh:
            tmp = Path(fh.name)
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        raise SettingsError(f"{path}: 書けません ({exc.strerror or type(exc).__name__})") from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def write_bundle(target: Path, *, llm_path: Path, prompt_path: Path) -> tuple[int, int]:
    """全部の接続と利用者の指示文を target に書き、(接続の件数, 指示文の件数) を返す。書けないときは SettingsError。"""
    data = bundle_data(llm_path=llm_path, prompt_path=prompt_path)
    _write_text(target, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return len(data["connections"]), len(data["prompts"])


# --- 取り込み ---


def _read_bytes(path: Path) -> bytes:
    """上限+1 バイトまで読み、上限を超えれば断る。"""
    try:
        with path.open("rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
    except OSError as exc:
        raise SettingsError(f"{path}: 読めません ({exc.strerror or type(exc).__name__})") from None
    if len(raw) > MAX_BYTES:
        raise SettingsError(f"{path}: 大きすぎます（5MB まで）")
    return raw


def _check_names(data: Any, path: Path) -> None:
    """項目の名前の範囲と、format・version。違えば SettingsError(ファイルにある項目の名前は入れない)。"""
    if not isinstance(data, dict) or set(data) != set(TOP_FIELDS):
        raise SettingsError(f"{path}: 一番外は {'・'.join(TOP_FIELDS)} だけの表にしてください（{_NOT_ALLOWED}）")
    if data["format"] != FORMAT:
        raise SettingsError(f"{path}: format は {FORMAT} にしてください")
    if type(data["version"]) is not int or data["version"] != VERSION:
        raise SettingsError(f"{path}: version は {VERSION} にしてください")
    for name in ("connections", "prompts"):
        if not isinstance(data[name], list):
            raise SettingsError(f"{path}: {name} は配列にしてください")
    for index, item in enumerate(data["connections"]):
        where = f"{path}: connections[{index}]"
        if not isinstance(item, dict):
            raise SettingsError(f"{where} は表(オブジェクト)にしてください")
        if set(item) - set(CONNECTION_FIELDS):
            raise SettingsError(f"{where} に知らない項目があります（{_NOT_ALLOWED}）")
        if missing := [name for name in CONNECTION_REQUIRED if name not in item]:
            raise SettingsError(f"{where} に {'・'.join(missing)} がありません")
        if "wire" in item and (not isinstance(item["wire"], dict) or set(item["wire"]) - set(WIRE_FIELDS)):
            raise SettingsError(f"{where}.wire は {'・'.join(WIRE_FIELDS)} の中だけの表にしてください")
        if "custom" in item and (not isinstance(item["custom"], dict) or set(item["custom"]) != set(CUSTOM_FIELDS)):
            raise SettingsError(f"{where}.custom は {', '.join(CUSTOM_FIELDS)} の5つの欄の表にしてください")
    for index, item in enumerate(data["prompts"]):
        if not isinstance(item, dict) or set(item) != set(PROMPT_FIELDS):
            raise SettingsError(f"{path}: prompts[{index}] は {'・'.join(PROMPT_FIELDS)} だけの表にしてください")


def _connection(item: dict[str, Any], where: str) -> LlmProfile:
    """llm_profiles の読むときの決まりで確かめる(wire の表は llm-profiles.json と同じ平らな欄にして渡す)。"""
    flat = {name: value for name, value in item.items() if name != "wire"}
    flat.update(item.get("wire", {}))
    return profile_from_item(flat, where)


def _check_unique(names: list[str], label: str, path: Path) -> None:
    seen: set[str] = set()
    for index, name in enumerate(names):
        if _name_key(name) in seen:
            raise SettingsError(f"{path}: {label}[{index}].name が重複しています(大文字・小文字は区別しません)")
        seen.add(_name_key(name))


def read_bundle(path: Path) -> Bundle:
    """ファイルを読んで確かめる(大きさ → UTF-8 → JSON → 項目の名前 → キー → 読むときの決まり → 名前の重なり)。

    1つでも違えば SettingsError(場所と項目と理由の種類だけ。値は入れない)。
    """
    raw = _read_bytes(path)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SettingsError(f"{path}: UTF-8 として読めません") from None
    try:
        ok, data = parse_json_text(text)
    except RecursionError:  # 入れ子が深すぎる
        ok = False
    if not ok:
        raise SettingsError(f"{path}: JSON として読めません")
    _check_names(data, path)
    check_keys(data, str(path))
    connections = [_connection(item, f"{path}: connections[{i}]") for i, item in enumerate(data["connections"])]
    prompts = [
        prompt_from_item(item["name"], item["template"], f"{path}: prompts[{i}]") for i, item in enumerate(data["prompts"])
    ]
    _check_unique([p.name for p in connections], "connections", path)
    _check_unique([p.name for p in prompts], "prompts", path)
    return Bundle(tuple(connections), tuple(prompts), digest)


def _free_names(names: tuple[str, ...], taken: list[str]) -> tuple[str, ...]:
    """重ならない名前。重なれば「<名前> （取り込み）」、それも重なれば「（取り込み 2）」…。決めた名前も次から避ける。"""
    used = {_name_key(name) for name in taken}
    chosen: list[str] = []
    for name in names:
        fresh = name
        if _name_key(name) in used:
            candidates = (imported_profile_name(name, n) for n in itertools.count(1))
            fresh = next(c for c in candidates if _name_key(c) not in used)
        used.add(_name_key(fresh))
        chosen.append(fresh)
    return tuple(chosen)


def plan_names(
    bundle: Bundle, profiles: LlmProfiles | None, prompts: PromptProfiles
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(接続の名前, 指示文の名前)。既存の名前(指示文は最初から入っている名前も)と、先に決めた名前を避ける。

    llm-profiles.json が無く(profiles が None)取り込む接続があるときは、取り込む前に1件目「既定」を作るので、
    既存の名前を「既定」1つとして決める(ADR-0184 決定1)。接続が無ければファイルを作らないので、既存の名前は無い。
    """
    if profiles is not None:
        existing = [p.name for p in profiles.profiles]
    else:
        existing = [DEFAULT_PROFILE_NAME] if bundle.connections else []
    return (
        _free_names(tuple(p.name for p in bundle.connections), existing),
        _free_names(tuple(p.name for p in bundle.prompts), [p.name for p in (*BUILTIN_PROFILES, *prompts.profiles)]),
    )


def import_bundle(
    source: Path,
    digest: str,
    names: tuple[tuple[str, ...], tuple[str, ...]],
    *,
    llm_path: Path,
    prompt_path: Path,
    ensure_first: Callable[[], object] | None = None,
) -> ImportResult:
    """確かめの後に足す。ファイルを読み直し、ハッシュと決めた名前が確かめたときと同じときだけ足す(違えば BundleChanged)。

    接続は llm-profiles.json に1回で、そのあと指示文を prompt-profiles.json に1回で書く。確かめはすべて書く前に済ませる。
    取り込む接続があるのに llm-profiles.json が無いときは、ensure_first を呼んで1件目(「既定」・使用中)を作ってから足す
    (ADR-0184 決定1。作った1件目は、あとで断られても残す)。ensure_first を渡さずに呼ばれたときは断る(守り)。
    指示文だけのときは llm-profiles.json を作らない。
    """
    bundle = read_bundle(source)
    if bundle.digest != digest:
        raise BundleChanged(f"{source}: 確かめた後に変わりました")
    profiles = load_profiles(llm_path)
    prompts = load_prompt_profiles(prompt_path)
    if plan_names(bundle, profiles, prompts) != names:
        raise BundleChanged(f"{source}: 確かめた後に、今のプロファイルの名前が変わりました")
    if bundle.connections and profiles is None:
        if ensure_first is None:
            raise SettingsError(f"{llm_path}: ありません。先に1件目を作ってください")
        ensure_first()
    connection_names, prompt_names = names
    added: tuple[LlmProfile, ...] = ()
    if bundle.connections:
        renamed = tuple(replace(p, name=n) for p, n in zip(bundle.connections, connection_names, strict=True))
        added = _add_profiles(llm_path, renamed)
    if not bundle.prompts:
        return ImportResult(added, ())
    renamed_prompts = tuple(replace(p, name=n) for p, n in zip(bundle.prompts, prompt_names, strict=True))
    try:
        return ImportResult(added, add_imported_prompt_profiles(prompt_path, renamed_prompts))
    except SettingsError as exc:
        if not added:
            raise
        return ImportResult(added, (), str(exc))
