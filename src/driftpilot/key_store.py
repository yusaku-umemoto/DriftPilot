"""API キーの OS の保管庫(REQ-34 / ADR-0033 決定2)。読む・保存する・削除するだけ。

保管庫は keyring を通して使う(Windows は資格情報マネージャー、Mac はキーチェーン)。keyring は llm extra の依存で、
入っていなくても比較は動くよう、関数の中で import する。保管庫が使えないときは理由の文を返し、例外を外に出さない
(比較や AI 解析を止めない。ADR-0018 決定4)。

キーの値は戻り値の key にだけ入れる。理由・結果の文には入れない(例外は型名だけを使い、本文は捨てる)。
保存・削除は設定画面からだけ呼ぶ(コマンドの口は作らない)。
保存名は AI の接続のプロファイルごとに DriftPilot/LLM/<id>/APIKey(ADR-0035 決定3。REQ-42 §5)。
id を省くと "default" で、今までの保存名と同じ(今保存してあるキーはそのまま使える)。
"""

import re
import sys
from dataclasses import dataclass, field
from typing import Any

from driftpilot.messages import (
    KEYSTORE_BAD_PROFILE_ID,
    KEYSTORE_KEY_REMAINS,
    KEYSTORE_NAME_MAC,
    KEYSTORE_NAME_OTHER,
    KEYSTORE_NAME_WINDOWS,
    api_key_bad_length,
    api_key_deleted,
    api_key_empty,
    api_key_has_space,
    api_key_not_stored,
    api_key_saved,
    keyring_failed,
    keyring_not_installed,
    keyring_unusable,
)

# 保存名(keyring の service と username)。CommandPilot の CommandPilot/LLM/<ID>/APIKey と同じ形(ADR-0033 決定2)
SERVICE = "DriftPilot/LLM/default/APIKey"
USERNAME = "api_key"
DEFAULT_PROFILE_ID = "default"
# 受け付けるプロファイルの id(llm-profiles.json と同じ。"default" か uuid4().hex)。保存名に変な文字を入れない
PROFILE_ID_PATTERN = re.compile(r"default|[0-9a-f]{32}")
# 保存できるキーの長さ(CommandPilot の LlmProfileApiKeys と同じ決まり)
MIN_KEY_LENGTH = 8
MAX_KEY_LENGTH = 2560


@dataclass(frozen=True)
class StoredKey:
    """保管庫を読んだ結果。problem は使えない・読めなかった理由(そのとき key は None)。"""

    key: str | None = field(default=None, repr=False)  # repr に出さない
    problem: str | None = None

    def __repr__(self) -> str:
        # 値(末尾4文字も)は出さず、有無だけを出す(REQ-36 §5)。dataclass は自分で書いた __repr__ を上書きしない
        return f"StoredKey(has_key={self.key is not None}, problem={self.problem!r})"


@dataclass(frozen=True)
class StoreResult:
    """保存・削除の結果。message は利用者に見せる文(値は入れない)。"""

    ok: bool
    message: str


def is_profile_id(value: object) -> bool:
    """プロファイルの id の形か("default" か 32 桁の小文字の16進)。"""
    return isinstance(value, str) and PROFILE_ID_PATTERN.fullmatch(value) is not None


def service_name(profile_id: str = DEFAULT_PROFILE_ID) -> str:
    """プロファイルの保存名(keyring の service)。形の違う id は ValueError で断る(文に id は入れない)。"""
    if not is_profile_id(profile_id):
        raise ValueError(KEYSTORE_BAD_PROFILE_ID)
    return f"DriftPilot/LLM/{profile_id}/APIKey"


def store_name(platform: str | None = None) -> str:
    """画面に出す保管庫の名前。"""
    platform = sys.platform if platform is None else platform
    if platform.startswith("win"):
        return KEYSTORE_NAME_WINDOWS
    if platform == "darwin":
        return KEYSTORE_NAME_MAC
    return KEYSTORE_NAME_OTHER


def _backend() -> tuple[Any, str | None]:
    """使う keyring の backend と、使えない理由。fail(保管庫が無い)と null(無効にしてある)は使えないとみなす。"""
    try:
        import keyring
        from keyring.backends import fail, null
    except ImportError:
        return None, keyring_not_installed()
    try:
        backend = keyring.get_keyring()
    except Exception as exc:  # noqa: BLE001 - backend の初期化の失敗も「使えない」にする
        return None, keyring_failed("read", type(exc).__name__)
    if isinstance(backend, (fail.Keyring, null.Keyring)):
        return None, keyring_unusable()
    return backend, None


def store_problem() -> str | None:
    """保管庫が使えない理由。使えるなら None。"""
    return _backend()[1]


def read_key(profile_id: str = DEFAULT_PROFILE_ID) -> StoredKey:
    """保管庫のキーを読む。無ければ key も problem も None。空白だけの値は無いとみなす。"""
    service = service_name(profile_id)
    backend, problem = _backend()
    if problem is not None:
        return StoredKey(problem=problem)
    try:
        value = backend.get_password(service, USERNAME)
    except Exception as exc:  # noqa: BLE001 - 例外の本文は使わない
        return StoredKey(problem=keyring_failed("read", type(exc).__name__))
    if not isinstance(value, str) or not value.strip():
        return StoredKey()
    return StoredKey(key=value.strip())


def validate_key(text: str) -> str | None:
    """保存できる形か(前後の空白を除いた後で 8〜2560 文字、空白・改行なし)。合わなければ理由の文。"""
    value = text.strip()
    if not value:
        return api_key_empty()
    if any(ch.isspace() for ch in value):
        return api_key_has_space()
    if not MIN_KEY_LENGTH <= len(value) <= MAX_KEY_LENGTH:
        return api_key_bad_length(len(value), MIN_KEY_LENGTH, MAX_KEY_LENGTH)
    return None


def save_key(text: str, *, profile_id: str = DEFAULT_PROFILE_ID, platform: str | None = None) -> StoreResult:
    """保管庫に保存する(前後の空白は除く)。形が合わない・使えないときは保存せず理由を返す。"""
    service = service_name(profile_id)
    if (reason := validate_key(text)) is not None:
        return StoreResult(False, reason)
    backend, problem = _backend()
    if problem is not None:
        return StoreResult(False, problem)
    try:
        backend.set_password(service, USERNAME, text.strip())
    except Exception as exc:  # noqa: BLE001 - 例外の本文は使わない
        return StoreResult(False, keyring_failed("save", type(exc).__name__))
    return StoreResult(True, api_key_saved(store_name(platform)))


def delete_key(*, profile_id: str = DEFAULT_PROFILE_ID, platform: str | None = None) -> StoreResult:
    """保管庫から削除する。保存されていなければ「保存されていません」(落ちない)。"""
    service = service_name(profile_id)
    backend, problem = _backend()
    if problem is not None:
        return StoreResult(False, problem)
    from keyring.errors import PasswordDeleteError

    error: str | None = None
    try:
        if backend.get_password(service, USERNAME) is None:
            return StoreResult(True, api_key_not_stored())
        try:
            backend.delete_password(service, USERNAME)
        except PasswordDeleteError as exc:
            # Mac の keyring は削除の失敗もこの例外にする。「無かった」とは限らないので、読み直して決める
            error = type(exc).__name__
        # 削除の後に読み直し、キーが残っていれば失敗にする(成功と表示しない)
        if backend.get_password(service, USERNAME) is not None:
            return StoreResult(False, keyring_failed("delete", error or KEYSTORE_KEY_REMAINS))
    except Exception as exc:  # noqa: BLE001 - 例外の本文は使わない
        return StoreResult(False, keyring_failed("delete", type(exc).__name__))
    if error is not None:
        return StoreResult(True, api_key_not_stored())  # 読んだ後に別の手で消えていた
    return StoreResult(True, api_key_deleted(store_name(platform)))
