"""更新の署名を確かめる公開の鍵(ADR-0188 決定4 / REQ-253 §3)。

鍵は 2 つ(普段の main と控えの backup)。どちらかの署名が合えば信じる。秘密の鍵はここに無い(決定者だけが持つ)。
印は鍵の 32 バイトの SHA-256 の頭 16 桁(tools/release/make_signing_key.py が出す印と同じ)。
"""

import base64
import hashlib
from dataclasses import dataclass


@dataclass(frozen=True)
class TrustedKey:
    """信じる公開の鍵。name は名前、public_b64 は Ed25519 の公開の鍵 32 バイトの base64、mark は印。"""

    name: str
    public_b64: str
    mark: str

    def raw(self) -> bytes:
        """公開の鍵の 32 バイト。"""
        return base64.b64decode(self.public_b64, validate=True)


def key_mark(raw: bytes) -> str:
    """公開の鍵の印(SHA-256 の頭 16 桁)。"""
    return hashlib.sha256(raw).hexdigest()[:16]


MAIN_KEY = TrustedKey("main", "87mdqqFmD6/FrWg08mCph6qAOzlIJYCWGd50E0WL6rE=", "5c50466f5b7455a6")
BACKUP_KEY = TrustedKey("backup", "tYTvYvSD5Si7CqWYvFy1RvKfDo2jCJd1ts11pc7mZPo=", "80db1dccd1059a79")
KEYS: tuple[TrustedKey, ...] = (MAIN_KEY, BACKUP_KEY)

# 名前 → 公開の鍵の 32 バイト(main、backup の順)。verify_signature の既定
TRUSTED_KEYS: dict[str, bytes] = {key.name: key.raw() for key in KEYS}
