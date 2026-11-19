"""「ログを見る」の窓の画面の論理(ADR-0248 決定2 / REQ-377)。pywebview も画面のパッケージも読まない。

1 つのログ(ファイルまたは貼り付けた文)を機器ごと・コマンドごとに分けて見せる。分け方は core.log_devices
(`split_by_device` と `split_log`。比較のときと同じ決まり)。ここは画面向けの形(機器の名前が無いときの「(機器の名前なし)」・
コマンドが無いときの「区切りなし」・上の行の文・書き出せるか)に直すだけで、何も送らない。

- ファイルは今の比較の窓の入力と同じ read_file で読む(文字コード・誤りの文も同じ)。貼り付けは normalize_text を通す
- 書き出しは batch_io.write_device_split_single を包む(今の比較の窓の書き出しと同じ決まり。before・after は作らない)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftpilot.batch_io import DeviceSplitSingleWritten, write_device_split_single
from driftpilot.core import LogDevice, LogSegment, log_devices
from driftpilot.messages import (
    LOG_VIEW_NO_DEVICE,
    LOG_VIEW_NO_PROMPT,
    LOG_VIEW_PASTED_NAME,
    log_view_summary,
)
from driftpilot.screen_model.model import read_file
from driftpilot.segmentation.input_forms import normalize_text

# 機器が何台以上なら機器ごとの書き出しを押せるか(1 台のログを分けても意味が無い)
EXPORT_MIN_DEVICES = 2


@dataclass(frozen=True)
class LoadedLog:
    """読み込んだ 1 つのログ。text は機器ごとの書き出しに使う(読み込み後の文字)。total_lines はファイル全体の行の数。"""

    name: str
    text: str
    devices: tuple[LogDevice, ...]
    total_lines: int


def load_log(text: str, name: str) -> LoadedLog:
    """text を機器ごと・コマンドごとに分けて持つ。"""
    return LoadedLog(name=name, text=text, devices=log_devices(text), total_lines=len(text.splitlines()))


def read_log(path: str | Path) -> LoadedLog:
    """ファイルを読む(比較の窓の入力と同じ read_file。読めなければ InputReadError)。"""
    source = read_file(path)
    return load_log(source.text, source.name)


def paste_log(text: str) -> LoadedLog:
    """貼り付けた文(文字コードの後の工程 normalize_text を通す。表示名は固定)。"""
    return load_log(normalize_text(text).text, LOG_VIEW_PASTED_NAME)


def summary_text(log: LoadedLog) -> str:
    """上の行の「機器 {n} 台・{m} 行」。"""
    return log_view_summary(len(log.devices), log.total_lines)


def can_export(log: LoadedLog | None) -> bool:
    """機器ごとの書き出しを押せるか(機器が 2 台以上のときだけ)。"""
    return log is not None and len(log.devices) >= EXPORT_MIN_DEVICES


def device_rows(log: LoadedLog) -> list[dict[str, Any]]:
    """機器の一覧の行(ホスト名・コマンドの数)。名前が無い全体の 1 台は「（機器の名前なし）」。"""
    return [
        {"name": device.hostname if device.hostname is not None else LOG_VIEW_NO_DEVICE, "commands": len(device.segments)}
        for device in log.devices
    ]


def _segment_row(segment: LogSegment) -> dict[str, Any]:
    return {
        "command": segment.command if segment.command else LOG_VIEW_NO_PROMPT,
        "lines": segment.line_count,
        "error": segment.is_error,
        "preamble": segment.has_preamble,
    }


def segment_rows(log: LoadedLog, device: int) -> list[dict[str, Any]]:
    """選んだ機器のコマンドの一覧の行。プロンプトが無く区切れなかった区間のコマンドは「区切りなし」。"""
    return [_segment_row(segment) for segment in log.devices[device].segments]


def valid_position(log: LoadedLog | None, device: Any, segment: Any) -> bool:
    """(device, segment) が読み込んだログの中の場所か。bool と整数でない値は受け付けない。"""
    if log is None:
        return False
    for value in (device, segment):
        if isinstance(value, bool) or not isinstance(value, int):
            return False
    if not 0 <= device < len(log.devices):
        return False
    return 0 <= segment < len(log.devices[device].segments)


def output_of(log: LoadedLog, device: int, segment: int) -> dict[str, Any]:
    """選んでいる区間のコマンドと出力の行。コマンドが無い区間(区切りなし)の command は None。区間が無ければ空。"""
    segments = log.devices[device].segments
    if not 0 <= segment < len(segments):
        return {"command": None, "lines": []}
    chosen = segments[segment]
    return {"command": chosen.command if chosen.command else None, "lines": list(chosen.lines)}


def write_log_device_files(text: str, parent: str | Path) -> DeviceSplitSingleWritten:
    """1 つの文を機器ごとのファイルに書き出す(書けなければ batch_io.DeviceSplitError)。"""
    return write_device_split_single(text, parent)
