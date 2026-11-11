"""更新の誤りの型(REQ-253 §7)。

message は messages.py の決まった文だけ(URL・パス・例外の本文は入れない。書けない場所の文の {場所} だけは例外)。
画面は message をそのまま結果の行と知らせに出す。

「やめる」で止めたときは UpdateCancelled(UpdateError の子にしない。誤りの文を出さないため)。
"""

from driftpilot.messages import (
    UPDATE_CONTENT_FAILED,
    UPDATE_INTEGRITY_FAILED,
    UPDATE_NO_ASSET,
    UPDATE_REASON_UNREACHABLE,
    UPDATE_SIGNATURE_FAILED,
    UPDATE_WRONG_LOCATION,
)


class UpdateError(Exception):
    """更新を止めた理由。message は画面に出す決まった文。"""

    default_message = UPDATE_CONTENT_FAILED

    def __init__(self, message: str | None = None) -> None:
        self.message = self.default_message if message is None else message
        super().__init__(self.message)


class SignatureError(UpdateError):
    """署名が合わない・署名のファイルが読めない。"""

    default_message = UPDATE_SIGNATURE_FAILED


class IntegrityError(UpdateError):
    """一覧に無い・大きさや指紋が違う・一覧の形が違う・決まった URL でない。"""

    default_message = UPDATE_INTEGRITY_FAILED


class NoAssetError(UpdateError):
    """その OS に合う配る物が Release に無い(arm64 でない Mac は「この Mac に合う配る物がありません」)。"""

    default_message = UPDATE_NO_ASSET


class NotWritableError(UpdateError):
    """入れ替える場所に書けない(文は update_not_writable(場所) を渡す)。"""


class WrongLocationError(UpdateError):
    """Mac で dmg から直接動かしている・AppTranslocation の場所。"""

    default_message = UPDATE_WRONG_LOCATION


class ContentError(UpdateError):
    """zip・dmg が読めない、中身の形が違う。思いがけない誤りもこれにする。"""

    default_message = UPDATE_CONTENT_FAILED


class DownloadError(UpdateError):
    """取ってこられない(文は REQ-252 の理由の文と同じ)。"""

    default_message = UPDATE_REASON_UNREACHABLE


class NotNewerError(UpdateError):
    """新しい版が今の版より新しくない(文は update_is_latest(今の版))。"""


class UpdateCancelled(Exception):
    """「やめる」で止めた。誤りの文は出さない。"""
