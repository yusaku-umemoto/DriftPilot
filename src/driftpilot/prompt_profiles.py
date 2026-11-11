"""AI への指示文のプロファイル(prompt-profiles.json)の読み書き(ADR-0079 決定1・決定5。REQ-116 §2)。

置き場所は AI の接続のプロファイル(llm-profiles.json)と同じフォルダ。DriftPilot が書く専用のファイルで、キーは入らない。
利用者の config.toml の [llm].prompt_template は読まない(書いてあれば llm_settings が印を立て、入口が注意を1行出す)。
作業フォルダの設定ファイルからも読まない。

最初から入っている3つ(BUILTIN_PROFILES)はコードの定数で、ファイルに書かない。id は builtin- で始まり、active にだけ書ける。
変えられない・消せないが、複製はできる。ファイルが無ければ、使用中は「要点と影響」(builtin-default)。
REQ-204(ADR-0146)で消した builtin-review・builtin-security(RETIRED_BUILTIN_IDS)が active なら、読むときに
builtin-default とみなす(ファイルは書き換えない。次に保存するときに直る)。
名前は最初から入っているものも含めて重ならない(前後の空白を除き、大文字・小文字を区別しない)。作る・保存する・複製する・
取り込むときに確かめる。ただし読むときは、利用者の指示文の名前が最初から入っている名前と重なっていても止めない
(REQ-204 で最初から入る名前が変わったため。find は最初から入っているほうを先に当てる。shadowed で重なりが分かる)。
本文(template)は llm_analyzer.check_prompt_template で確かめる。読むときに違えば SettingsError(場所と項目と理由の種類だけ。
本文は文に入れない)。

書く関数は設定画面(screen_model/settings_model.py)が使う。どれもファイルを読み直してから直し、同じフォルダの一時ファイルに
書いて os.replace で置き換える(llm_profiles と同じ書き方。途中で落ちても壊れたファイルを残さない)。UTF-8・改行 \\n。
"""

import hashlib
import json
import os
import re
import tempfile
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from driftpilot import i18n
from driftpilot.llm_analyzer import (
    DEFAULT_PROMPT_TEMPLATE,
    INCIDENT_PROMPT_TEMPLATE,
    STATUS_PROMPT_TEMPLATE,
    check_prompt_template,
    normalize_newlines,
)
from driftpilot.llm_settings import SettingsError, user_config_path
from driftpilot.messages import LLMP_ERRORS, PROMPT_TEMPLATE_PROBLEM, PROMPTP_ERRORS

PROMPT_PROFILES_NAME = "prompt-profiles.json"
SCHEMA_VERSION = 1
BUILTIN_PREFIX = "builtin-"
BUILTIN_DEFAULT_ID = "builtin-default"
BUILTIN_INCIDENT_ID = "builtin-incident"
BUILTIN_STATUS_ID = "builtin-status"
# REQ-204(ADR-0146 決定1)で消した id。active に書いてあれば、読むときに builtin-default とみなす
RETIRED_BUILTIN_IDS = ("builtin-review", "builtin-security")
# 利用者のプロファイルの id(uuid4().hex。後から変えない)
_USER_ID = re.compile(r"[0-9a-f]{32}")

_UNSET: Any = object()  # update_prompt_profile で「渡していない」を区別する


def _err(key: str, **values: object) -> str:
    """誤りの文の {名前} を埋める。接続のプロファイルと共通の文(LLMP_ERRORS)に無い鍵は、指示文だけの文(PROMPTP_ERRORS)から引く。"""
    table = PROMPTP_ERRORS if key in PROMPTP_ERRORS else LLMP_ERRORS
    return table[key].format(**values)


def _reason(exc: OSError) -> str:
    return exc.strerror or type(exc).__name__


@dataclass(frozen=True)
class PromptProfile:
    """1つの指示文。builtin は最初から入っているもの(変えられない・消せない)。"""

    id: str
    name: str
    template: str
    builtin: bool = False
    # 最初から入っているものだけが持つ、もう一方の言語の名前(ADR-0190。名前で探す・重なりを見るときに、表示名と同じに扱う)
    other_names: tuple[str, ...] = ()


# 最初から入っている3つの名前(id → (日本語, 英語))。英語の版のソースに日本語を置けないので、messages_en ではなくここに置く(REQ-258)。
# 表示は今の言語の名前。名前で探す・重なりを見るときは、両方の言語の名前を使う(最初から入っているものだけ)
_BUILTIN_NAMES: dict[str, tuple[str, str]] = {
    BUILTIN_DEFAULT_ID: ("要点と影響", "Key points and impact"),
    BUILTIN_INCIDENT_ID: ("障害調査", "Incident investigation"),
    BUILTIN_STATUS_ID: ("状態の確認", "Check status"),
}


def _builtin(profile_id: str, template: str) -> "PromptProfile":
    ja, en = _BUILTIN_NAMES[profile_id]
    shown, other = (en, ja) if i18n.current_language() == i18n.EN else (ja, en)
    return PromptProfile(profile_id, shown, template, True, (other,))


# 最初から入っている3つ(この順で一覧の先頭に出す)
BUILTIN_PROFILES: tuple[PromptProfile, ...] = (
    _builtin(BUILTIN_DEFAULT_ID, DEFAULT_PROMPT_TEMPLATE),
    _builtin(BUILTIN_INCIDENT_ID, INCIDENT_PROMPT_TEMPLATE),
    _builtin(BUILTIN_STATUS_ID, STATUS_PROMPT_TEMPLATE),
)
BUILTIN_IDS = tuple(p.id for p in BUILTIN_PROFILES)


@dataclass(frozen=True)
class PromptProfiles:
    """使用中の id と、利用者のプロファイル(ファイルの順)。digest は読んだファイルのバイト列の sha256(無ければ None)。"""

    active: str = BUILTIN_DEFAULT_ID
    profiles: tuple[PromptProfile, ...] = ()
    digest: str | None = None

    def all(self) -> tuple[PromptProfile, ...]:
        """最初から入っている3つを先に、その後に利用者のプロファイルをファイルの順で。"""
        return (*BUILTIN_PROFILES, *self.profiles)

    @property
    def active_profile(self) -> PromptProfile:
        profile = self.get(self.active)
        assert profile is not None  # 読むときに確かめている
        return profile

    def get(self, profile_id: str) -> PromptProfile | None:
        return next((p for p in self.all() if p.id == profile_id), None)

    def find(self, name: str) -> PromptProfile | None:
        """表示名で探す(前後の空白を除き、大文字・小文字を区別しない)。最初から入っているものは、もう一方の言語の名前でも当てる。"""
        key = _name_key(name)
        return next((p for p in self.all() if key in name_keys(p)), None)

    def names(self) -> list[str]:
        return [p.name for p in self.all()]

    def shadowed(self, name: str) -> bool:
        """名前が、最初から入っている指示文(両方の言語の名前)と利用者の指示文の両方に当たるか(find は最初から入っているほうを返す)。"""
        key = _name_key(name)
        return any(key in name_keys(p) for p in BUILTIN_PROFILES) and any(_name_key(p.name) == key for p in self.profiles)


def _name_key(name: str) -> str:
    return name.strip().casefold()


def name_keys(profile: PromptProfile) -> set[str]:
    """名前で比べるときの鍵。表示名と other_names(最初から入っているものだけが持つ。利用者の指示文は表示名だけ)。"""
    return {_name_key(profile.name), *(_name_key(other) for other in profile.other_names)}


def builtin_name_variants() -> list[str]:
    """最初から入っている指示文の、両方の言語の名前(取り込むときに名前を避けるのに使う)。"""
    return [name for p in BUILTIN_PROFILES for name in (p.name, *p.other_names)]


def is_builtin(profile_id: str) -> bool:
    return profile_id in BUILTIN_IDS


def prompt_profiles_path(
    *, platform: str | None = None, env: Mapping[str, str] | None = None, home: Path | None = None
) -> Path:
    """prompt-profiles.json の場所(llm-profiles.json・利用者ごとの設定ファイルと同じフォルダ)。"""
    return user_config_path(platform=platform, env=env, home=home).parent / PROMPT_PROFILES_NAME


# --- 読む ---


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SettingsError(_err("unreadable", path=path, reason=_reason(exc))) from None


def prompt_file_digest(path: Path) -> str | None:
    """今のファイルのバイト列の sha256(無ければ None)。読めなければ SettingsError。"""
    raw = _read(path)
    return None if raw is None else hashlib.sha256(raw).hexdigest()


def _template_problem(prefix: str, kind: str) -> str:
    """本文が使えないときの文(場所と項目と理由だけ。本文は入れない)。"""
    return f"{prefix}template {PROMPT_TEMPLATE_PROBLEM[kind]}"


def _profile(item: Any, index: int, path: Path) -> PromptProfile:
    where = f"{path}: profiles[{index}]"
    if not isinstance(item, dict):
        raise SettingsError(_err("where_not_object", where=where))
    profile_id = item.get("id")
    if not isinstance(profile_id, str) or _USER_ID.fullmatch(profile_id) is None:
        # builtin- の id は最初から入っているものだけ(ファイルの profiles には書けない)
        raise SettingsError(_err("id_form", where=where))
    return replace(prompt_from_item(item.get("name"), item.get("template"), where), id=profile_id)


def prompt_from_item(name: Any, template: Any, where: str) -> PromptProfile:
    """名前と本文を、読むときの決まりで確かめて指示文にする。id は "" で返す。

    prompt-profiles.json の読み込みと、まとめたファイルの取り込み(profile_bundle。REQ-119)で同じ決まりを使う。
    違えば SettingsError(where と項目と理由の種類だけ。本文は入れない)。
    """
    if not isinstance(name, str) or not name.strip():
        raise SettingsError(_err("where_name", where=where))
    if (problem := check_prompt_template(template)) is not None:
        raise SettingsError(_template_problem(f"{where}.", problem))
    return PromptProfile(id="", name=name.strip(), template=template)


def _check_unique(profiles: list[PromptProfile], path: Path) -> None:
    """読むときの重なりの確かめ。利用者の指示文どうしの id と名前だけを見る。

    最初から入っている名前との重なりは、読むときは止めない(REQ-204 で最初から入る名前が変わり、前から作ってあった
    利用者の指示文が読めなくならないように。判断の記録 2)。作る・保存するときは _checked が断る。
    """
    ids: set[str] = set()
    names: set[str] = set()
    for index, profile in enumerate(profiles):
        if profile.id in ids:
            raise SettingsError(_err("dup_id", path=path, index=index))
        if _name_key(profile.name) in names:
            raise SettingsError(_err("dup_name", path=path, index=index))
        ids.add(profile.id)
        names.add(_name_key(profile.name))


def load_prompt_profiles(path: Path) -> PromptProfiles:
    """prompt-profiles.json を読む。無ければ使用中が「既定」で、利用者のプロファイルは無し。

    読めない・形が違うときは SettingsError(場所と、どの項目が違うかだけ。本文は入れない)。
    """
    raw = _read(path)
    if raw is None:
        return PromptProfiles()
    try:
        data = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise SettingsError(_err("not_utf8", path=path)) from None
    except (ValueError, RecursionError):
        raise SettingsError(_err("not_json", path=path)) from None
    if not isinstance(data, dict):
        raise SettingsError(_err("top_not_object", path=path))
    version = data.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise SettingsError(_err("schema_version", path=path, version=SCHEMA_VERSION))
    items = data.get("profiles")
    if not isinstance(items, list):
        raise SettingsError(PROMPTP_ERRORS["profiles_array"].format(path=path))
    profiles = [_profile(item, i, path) for i, item in enumerate(items)]
    _check_unique(profiles, path)
    active = data.get("active")
    if active in RETIRED_BUILTIN_IDS:
        # 消した最初から入っている指示文は既定とみなす(ファイルは書き換えない。次に保存するときに直る)
        active = BUILTIN_DEFAULT_ID
    if not isinstance(active, str) or active not in {*BUILTIN_IDS, *(p.id for p in profiles)}:
        raise SettingsError(_err("active_id", path=path))
    return PromptProfiles(active=active, profiles=tuple(profiles), digest=hashlib.sha256(raw).hexdigest())


# --- 書く(設定画面用。REQ-116 §3) ---


def _to_json(profiles: PromptProfiles) -> dict[str, Any]:
    """最初から入っている3つは書かない(active にだけ id が入る)。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "active": profiles.active,
        "profiles": [{"id": p.id, "name": p.name, "template": p.template} for p in profiles.profiles],
    }


def _write(path: Path, profiles: PromptProfiles) -> None:
    """同じフォルダの一時ファイルに書いてから os.replace で置き換える。失敗したら一時ファイルを消す(元のファイルは残る)。"""
    text = json.dumps(_to_json(profiles), ensure_ascii=False, indent=2) + "\n"
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
        raise SettingsError(_err("unwritable", path=path, reason=_reason(exc))) from None
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)


def _require_profile(profiles: PromptProfiles, profile_id: Any, path: Path) -> PromptProfile:
    profile = profiles.get(profile_id) if isinstance(profile_id, str) else None
    if profile is None:
        raise SettingsError(_err("no_such_profile", path=path))
    return profile


def _refuse_builtin(profile: PromptProfile, path: Path) -> None:
    if profile.builtin:
        raise SettingsError(_err("builtin_readonly", path=path))


def _checked(profile: PromptProfile, profiles: PromptProfiles, path: Path) -> PromptProfile:
    """書く前の検査。名前は前後の空白を除き、空と重複(最初から入っている名前も。大文字・小文字を区別しない)を断る。

    本文は改行を \\n にそろえてから確かめる(文に本文は入れない)。
    """
    if not isinstance(profile.name, str) or not profile.name.strip():
        raise SettingsError(_err("name_required", path=path))
    name = profile.name.strip()
    # 最初から入っているものは両方の言語の名前で断る(find が最初から入っているほうへ当たる名前を作れると、名前で選べなくなるため)
    if any(_name_key(name) in name_keys(o) for o in profiles.all() if o.id != profile.id):
        raise SettingsError(_err("name_duplicate", path=path))
    template = normalize_newlines(profile.template) if isinstance(profile.template, str) else profile.template
    if (problem := check_prompt_template(template)) is not None:
        raise SettingsError(_template_problem(f"{path}: ", problem))
    return replace(profile, name=name, template=template, builtin=False)


def add_prompt_profile(path: Path, *, name: str, template: str = DEFAULT_PROMPT_TEMPLATE) -> PromptProfile:
    """プロファイルを足す(既定の文で作る)。id は uuid4().hex(後から変えない)。"""
    profiles = load_prompt_profiles(path)
    profile = _checked(PromptProfile(id=uuid.uuid4().hex, name=name, template=template), profiles, path)
    _write(path, replace(profiles, profiles=(*profiles.profiles, profile)))
    return profile


def add_imported_prompt_profiles(path: Path, profiles: tuple[PromptProfile, ...]) -> tuple[PromptProfile, ...]:
    """取り込んだ指示文を、新しい id(uuid4().hex)で1回で足す(REQ-119 §3)。使用中と既存の指示文は変えない。

    1件でも決まり(名前の重なりも。最初から入っている名前とも)に反すれば、何も書かずに SettingsError。
    """
    current = load_prompt_profiles(path)
    added: list[PromptProfile] = []
    for profile in profiles:
        fresh = PromptProfile(id=uuid.uuid4().hex, name=profile.name, template=profile.template)
        added.append(_checked(fresh, replace(current, profiles=(*current.profiles, *added)), path))
    _write(path, replace(current, profiles=(*current.profiles, *added)))
    return tuple(added)


def duplicate_prompt_profile(path: Path, profile_id: str, *, name: str) -> PromptProfile:
    """複製する。新しい id で本文を写す。最初から入っている3つも複製できる。"""
    profiles = load_prompt_profiles(path)
    source = _require_profile(profiles, profile_id, path)
    profile = _checked(PromptProfile(id=uuid.uuid4().hex, name=name, template=source.template), profiles, path)
    _write(path, replace(profiles, profiles=(*profiles.profiles, profile)))
    return profile


def update_prompt_profile(path: Path, profile_id: str, *, name: str = _UNSET, template: str = _UNSET) -> PromptProfile:
    """渡した項目だけを変える。id は変えない。最初から入っている3つは断る。"""
    profiles = load_prompt_profiles(path)
    current = _require_profile(profiles, profile_id, path)
    _refuse_builtin(current, path)
    changes = {k: v for k, v in (("name", name), ("template", template)) if v is not _UNSET}
    profile = _checked(replace(current, **changes), profiles, path)
    _write(path, replace(profiles, profiles=tuple(profile if p.id == profile_id else p for p in profiles.profiles)))
    return profile


def delete_prompt_profile(path: Path, profile_id: str) -> PromptProfile:
    """プロファイルを消し、消したものを返す。最初から入っている3つと使用中は断る。"""
    profiles = load_prompt_profiles(path)
    target = _require_profile(profiles, profile_id, path)
    _refuse_builtin(target, path)
    if profile_id == profiles.active:
        raise SettingsError(_err("active_not_deletable", path=path))
    _write(path, replace(profiles, profiles=tuple(p for p in profiles.profiles if p.id != profile_id)))
    return target


def set_active_prompt(path: Path, profile_id: str) -> PromptProfiles:
    """使う指示文を切り替える(最初から入っている3つも選べる)。"""
    profiles = load_prompt_profiles(path)
    _require_profile(profiles, profile_id, path)
    updated = replace(profiles, active=profile_id)
    _write(path, updated)
    return updated
