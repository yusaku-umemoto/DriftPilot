"""複数の機器が入ったログを、機器ごとのファイルに分けて書き出す(ADR-0194 決定4。REQ-267 §4)。

書くのは、比べたときの文だけ(元のファイルは開かない)。選んだフォルダーの下に新しいフォルダー
`driftpilot-split-<YYYYMMDD-HHMMSS>`(同じ名前があれば `-2`・`-3`)を作り、その中の `before`・`after` に
`<hostname>.log` を書く。UTF-8(BOM なし)・改行はそのまま・上書きしない。書けなかったときは、途中まで書いたものを消さずに止める。
"""

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from driftpilot.messages import device_split_failed
from driftpilot.segmentation import DeviceText, device_filenames, split_by_device

FOLDER_PREFIX = "driftpilot-split-"
BEFORE_DIR = "before"
AFTER_DIR = "after"
_MAX_SUFFIX = 1000


@dataclass(frozen=True)
class DeviceSplitWritten:
    """書いた場所と台数。before_dir・after_dir は、機器ごとのファイルを書いたフォルダー。"""

    folder: Path
    before_dir: Path
    after_dir: Path
    before_count: int
    after_count: int


class DeviceSplitError(Exception):
    """書けなかった。folder は途中まで書いたフォルダー(消さない)。"""

    def __init__(self, reason: str, folder: Path | None) -> None:
        super().__init__(device_split_failed(reason, folder))
        self.reason = reason
        self.folder = folder


def _new_folder(parent: Path, now: datetime) -> Path:
    base = f"{FOLDER_PREFIX}{now:%Y%m%d-%H%M%S}"
    for n in range(1, _MAX_SUFFIX + 1):
        folder = parent / (base if n == 1 else f"{base}-{n}")
        try:
            folder.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return folder
    raise FileExistsError(base)


def _write_side(folder: Path, devices: tuple[DeviceText, ...], names: dict[str, str]) -> None:
    folder.mkdir(exist_ok=False)
    for device in devices:
        # 上書きしない("x")。BOM なしの UTF-8、改行はそのまま
        with open(folder / names[device.hostname], "x", encoding="utf-8", newline="") as handle:
            handle.write(device.text)


def write_device_split(
    before_text: str,
    after_text: str,
    parent: str | os.PathLike[str],
    now: datetime | None = None,
) -> DeviceSplitWritten:
    """before_text・after_text を機器ごとに分けて、parent の下の新しいフォルダーに書く。書けなければ DeviceSplitError。"""
    parent = Path(parent)
    before = split_by_device(before_text)
    after = split_by_device(after_text)
    names = device_filenames([d.hostname for d in before], [d.hostname for d in after])
    folder: Path | None = None
    try:
        folder = _new_folder(parent, now or datetime.now())
        before_dir = folder / BEFORE_DIR
        after_dir = folder / AFTER_DIR
        _write_side(before_dir, before, names)
        _write_side(after_dir, after, names)
    except OSError as exc:
        raise DeviceSplitError(exc.strerror or str(exc), folder) from exc
    return DeviceSplitWritten(folder, before_dir, after_dir, len(before), len(after))
