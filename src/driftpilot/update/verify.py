"""署名と指紋を確かめる(ADR-0188 決定4 / REQ-253 §3)。

- SHA256SUMS.txt: 1 行に「小文字の 16 進 64 桁・空白 2 つ・ファイルの名前」。行の終わりは LF、ASCII
- SHA256SUMS.txt.sig: SHA256SUMS.txt のバイト列そのままへの Ed25519 の署名 64 バイトを base64 にした 1 行(末尾の改行は 1 つまで)
- 確かめる順は「署名 → 一覧に名前がある → 大きさ → 指紋」。verify_listing(署名と一覧)と verify_file(大きさと指紋)で守る。
  1 つでも違えば、その後に進まない

cryptography は verify_signature の中で読む(入っていなくても画面や CLI を読み込むところでは落ちない。
入っていないことは selfcheck の「署名の確かめ」の行で分かる)。
"""

import base64
import binascii
import hashlib
import re
from collections.abc import Mapping
from pathlib import Path

from driftpilot.update.errors import IntegrityError, SignatureError
from driftpilot.update.keys import TRUSTED_KEYS

SUMS_NAME = "SHA256SUMS.txt"
SIG_NAME = "SHA256SUMS.txt.sig"
SIGNATURE_BYTES = 64
_LINE = re.compile(r"[0-9a-f]{64}  [A-Za-z0-9._-]+")
_READ_CHUNK = 1024 * 1024


def _signature_bytes(sig: bytes) -> bytes:
    """.sig の中身を 64 バイトの署名にする。形が違えば SignatureError。"""
    if not isinstance(sig, (bytes, bytearray)):
        raise SignatureError
    data = bytes(sig)
    if data.endswith(b"\n"):
        data = data[:-1]
    try:
        text = data.decode("ascii")
        raw = base64.b64decode(text, validate=True)
    except (UnicodeDecodeError, binascii.Error, ValueError):
        raise SignatureError from None
    if len(raw) != SIGNATURE_BYTES:
        raise SignatureError
    return raw


def verify_signature(sums: bytes, sig: bytes, keys: Mapping[str, bytes] = TRUSTED_KEYS) -> str:
    """keys のどれかの鍵で署名が合えば、その鍵の名前を返す(並びの順に確かめる)。合わなければ SignatureError。

    keys は「名前 → 公開の鍵の 32 バイト」(テストは一時の鍵を渡す)。cryptography が無いときも SignatureError。
    """
    raw = _signature_bytes(sig)
    if not isinstance(sums, (bytes, bytearray)):
        raise SignatureError
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        raise SignatureError from None
    for name, public in keys.items():
        try:
            key = Ed25519PublicKey.from_public_bytes(bytes(public))
        except (ValueError, TypeError):
            continue
        try:
            key.verify(raw, bytes(sums))
        except InvalidSignature:
            continue
        return name
    raise SignatureError


def parse_sums(sums: bytes) -> dict[str, str]:
    """SHA256SUMS.txt を「名前 → 指紋」にする。空・空の行・同じ名前の 2 行・形の違う行・CR・最後の LF が無い、は IntegrityError。"""
    if not isinstance(sums, (bytes, bytearray)) or not sums:
        raise IntegrityError
    try:
        text = bytes(sums).decode("ascii")
    except UnicodeDecodeError:
        raise IntegrityError from None
    if not text.endswith("\n"):
        raise IntegrityError
    found: dict[str, str] = {}
    for line in text[:-1].split("\n"):
        if _LINE.fullmatch(line) is None:
            raise IntegrityError
        digest, name = line[:64], line[66:]
        if name in found:
            raise IntegrityError
        found[name] = digest
    return found


def sha256_file(path: Path | str) -> str:
    """ファイルの SHA-256(小文字の 16 進)。1 MiB ずつ読む。"""
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        while chunk := stream.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def verify_listing(sums: bytes, sig: bytes, name: str, keys: Mapping[str, bytes] = TRUSTED_KEYS) -> tuple[str, str]:
    """署名を確かめてから一覧を読み、name の指紋を返す: (合った鍵の名前, 期待する指紋)。

    署名が合わなければ一覧を読まない(SignatureError)。一覧に name が無ければ IntegrityError。
    """
    key = verify_signature(sums, sig, keys)
    digests = parse_sums(sums)
    if name not in digests:
        raise IntegrityError
    return key, digests[name]


def verify_file(path: Path | str, release_size: int, digest: str) -> None:
    """大きさが Release の size と同じか(違えば指紋を計算しない)、指紋が同じかを確かめる。違えば IntegrityError。"""
    try:
        size = Path(path).stat().st_size
    except OSError:
        raise IntegrityError from None
    if size != release_size:
        raise IntegrityError
    try:
        actual = sha256_file(path)
    except OSError:
        raise IntegrityError from None
    if actual != digest:
        raise IntegrityError
