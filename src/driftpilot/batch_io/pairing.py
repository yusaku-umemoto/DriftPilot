"""同名で対にする(ADR-0020)。規則は「ファイル名が拡張子まで完全一致」の1つだけ。

推測しない: 正規化しない(大文字小文字・全角半角・空白の違いを吸収しない)。再帰しない(直下のみ)。
対にならなかったファイルは必ず返す(黙って飛ばさない)。規則を増やすときは ADR を書く(決定6)。
core には置かない(ファイルシステムを読む処理。決定5)。
"""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftpilot.core import MANIFEST_SCHEMA_VERSION
from driftpilot.core.batch import DEFAULT_KIND, DEFAULT_MODE
from driftpilot.core.types import Mode
from driftpilot.raw_keys import DEFAULT_RAW_OPTIONS, RawOptions, options_to_plain


class PairingError(Exception):
    """対を作れない(ディレクトリが無いなど)。"""


@dataclass(frozen=True)
class Pairing:
    """before_dir / after_dir は絶対パス。pairs は両方にあるファイル名、*_only は片側にしかないファイル名(名前の順)。"""

    before_dir: Path
    after_dir: Path
    pairs: tuple[str, ...]
    before_only: tuple[str, ...]
    after_only: tuple[str, ...]


def _names(folder: Path) -> set[str]:
    if not folder.is_dir():
        raise PairingError(f"{folder}: ディレクトリがありません")
    try:
        return {p.name for p in folder.iterdir() if p.is_file()}
    except OSError as exc:
        raise PairingError(f"{folder}: 読めません ({exc.strerror or exc})") from exc


def pair_by_name(before_dir: str | os.PathLike[str], after_dir: str | os.PathLike[str]) -> Pairing:
    """両方のディレクトリの直下にある通常ファイルを、名前の文字列が完全に一致するものどうしで対にする。

    名前は文字列として比べる(ファイルシステムに問い合わせない)。大文字小文字を区別しないファイルシステムでも、
    SW01.txt と sw01.txt は対にならない。
    """
    before = Path(before_dir).absolute()
    after = Path(after_dir).absolute()
    before_names = _names(before)
    after_names = _names(after)
    return Pairing(
        before_dir=before,
        after_dir=after,
        pairs=tuple(sorted(before_names & after_names)),
        before_only=tuple(sorted(before_names - after_names)),
        after_only=tuple(sorted(after_names - before_names)),
    )


def manifest_path(path: Path, base_dir: Path | None) -> str:
    """マニフェストに書くパス。base_dir(書く位置)からの相対パス(/ 区切り)。base_dir が None か、相対にできなければ絶対パス。"""
    if base_dir is None:
        return str(path)
    try:
        return Path(os.path.relpath(path, base_dir)).as_posix()
    except ValueError:  # Windows でドライブが違うなど、相対にできない
        return str(path)


_manifest_path = manifest_path  # 前の名前(動きは同じ)


def pairing_manifest(
    pairing: Pairing,
    base_dir: Path | None,
    *,
    mode: Mode = DEFAULT_MODE,
    platform: str | None = None,
    raw_options: RawOptions = DEFAULT_RAW_OPTIONS,
) -> dict[str, Any]:
    """ADR-0016 決定2 の形のマニフェスト(dict)。id はファイル名、label は書かない。defaults は明示して書く。

    path は base_dir(マニフェストを書く位置)からの相対パス。base_dir が None なら絶対パス。
    比較方式と機種(config の系統。None は指定なし=A型)は defaults に書く(job ごとには書かない。REQ-27 §A-2)。
    LLM の指定は書かない(マニフェストには書けない。ADR-0031 決定6)。
    単純比較の設定(REQ-228 §7.1)は既定でないときだけ defaults.raw_options に書く(既定なら今と同じ中身)。
    """
    defaults: dict[str, Any] = {"mode": mode, "kind": DEFAULT_KIND}
    if platform is not None:
        defaults["platform"] = platform
    if not raw_options.is_default():
        defaults["raw_options"] = options_to_plain(raw_options)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "defaults": defaults,
        "jobs": [
            {
                "id": name,
                "before": {"path": _manifest_path(pairing.before_dir / name, base_dir)},
                "after": {"path": _manifest_path(pairing.after_dir / name, base_dir)},
            }
            for name in pairing.pairs
        ],
    }
