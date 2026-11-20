"""「ログを見る」の窓の画面の論理(ADR-0248 決定2 / REQ-377)。pywebview も画面のパッケージも読まない。

1 つのログ(ファイルまたは貼り付けた文)を機器ごと・コマンドごとに分けて見せる。分け方は core.log_devices
(`split_by_device` と `split_log`。比較のときと同じ決まり)。ここは画面向けの形(機器の名前が無いときの「(機器の名前なし)」・
コマンドが無いときの「区切りなし」・上の行の文・書き出せるか・AI に送る区間のチェックと送る区間の行の文)に直すだけで、何も送らない
(AI に送る処理は gui_web/log_ask.py。REQ-382)。

- ファイルは今の比較の窓の入力と同じ read_file で読む(文字コード・誤りの文も同じ)。貼り付けは normalize_text を通す
- 書き出しは batch_io.write_device_split_single を包む(今の比較の窓の書き出しと同じ決まり。before・after は作らない)
"""

from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from driftpilot.batch_io import DeviceSplitSingleWritten, write_device_split_single
from driftpilot.core import LogDevice, LogSegment, log_devices
from driftpilot.messages import (
    LOG_VIEW_NO_DEVICE,
    LOG_VIEW_NO_PROMPT,
    LOG_VIEW_PASTED_NAME,
    log_view_scope_checked,
    log_view_scope_shown,
    log_view_summary,
)
from driftpilot.screen_model.model import read_file
from driftpilot.segmentation.input_forms import normalize_text

# 機器が何台以上なら機器ごとの書き出しを押せるか(1 台のログを分けても意味が無い)
EXPORT_MIN_DEVICES = 2
# 機器の番号と区間の番号(どちらも 0 から数える)。AI に送る区間のチェックの単位(REQ-382)
Position = tuple[int, int]


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


def device_name(log: LoadedLog, device: int) -> str:
    """機器の表示名。名前が無い全体の 1 台は「（機器の名前なし）」。"""
    hostname = log.devices[device].hostname
    return hostname if hostname is not None else LOG_VIEW_NO_DEVICE


def device_rows(log: LoadedLog, checked: Collection[Position] = frozenset()) -> list[dict[str, Any]]:
    """機器の一覧の行(ホスト名・コマンドの数・AI に送るチェックの状態 check)。名前が無い全体の 1 台は「（機器の名前なし）」。"""
    return [
        {"name": device_name(log, index), "commands": len(device.segments), "check": device_check(log, checked, index)}
        for index, device in enumerate(log.devices)
    ]


def _segment_row(segment: LogSegment) -> dict[str, Any]:
    return {
        "command": segment.command if segment.command else LOG_VIEW_NO_PROMPT,
        "lines": segment.line_count,
        "error": segment.is_error,
        "preamble": segment.has_preamble,
    }


def segment_rows(log: LoadedLog, device: int, checked: Collection[Position] = frozenset()) -> list[dict[str, Any]]:
    """選んだ機器のコマンドの一覧の行(checked はその区間が AI に送るチェックを付けているか)。
    プロンプトが無く区切れなかった区間のコマンドは「区切りなし」。"""
    return [
        {**_segment_row(segment), "checked": (device, index) in checked}
        for index, segment in enumerate(log.devices[device].segments)
    ]


# --- AI に送る区間のチェック(REQ-382 §3)。チェックは「(機器の番号, 区間の番号)の組」の集まりで、画面ではなく Python が持つ ---

CHECK_ON = "on"
CHECK_SOME = "some"
CHECK_OFF = "off"


def all_positions(log: LoadedLog | None) -> list[Position]:
    """全部の機器の全部の区間(機器の順・区間の順)。"""
    if log is None:
        return []
    return [(d, s) for d, device in enumerate(log.devices) for s in range(len(device.segments))]


def _state_of(total: int, checked: int) -> str:
    if total == 0 or checked == 0:
        return CHECK_OFF
    return CHECK_ON if checked == total else CHECK_SOME


def device_check(log: LoadedLog, checked: Collection[Position], device: int) -> str:
    """機器のチェックの状態: on(その機器の全部のコマンドが入っている)・some(一部だけ)・off。"""
    total = len(log.devices[device].segments)
    return _state_of(total, sum(1 for s in range(total) if (device, s) in checked))


def all_check(log: LoadedLog | None, checked: Collection[Position]) -> str:
    """全部の機器の全部のコマンドに対するチェックの状態(on・some・off)。"""
    positions = all_positions(log)
    return _state_of(len(positions), sum(1 for position in positions if position in checked))


def commands_check(log: LoadedLog | None, checked: Collection[Position], device: int) -> str:
    """表示中の機器の全部のコマンドに対するチェックの状態(コマンドの一覧の見出しの「全部」)。"""
    if log is None or not 0 <= device < len(log.devices):
        return CHECK_OFF
    return device_check(log, checked, device)


def toggle_segment(checked: frozenset[Position], device: int, segment: int, on: bool) -> frozenset[Position]:
    """区間 1 つのチェックを付ける・外す(位置が正しいかは呼ぶ側が valid_position で確かめる)。"""
    return checked | {(device, segment)} if on else checked - {(device, segment)}


def toggle_device(log: LoadedLog, checked: frozenset[Position], device: int, on: bool) -> frozenset[Position]:
    """機器のチェック: その機器の全部のコマンドを入れる・外す。"""
    positions = {(device, s) for s in range(len(log.devices[device].segments))}
    return checked | positions if on else checked - positions


def toggle_all(log: LoadedLog | None, checked: frozenset[Position], on: bool) -> frozenset[Position]:
    """見出しの「全部」: 全部の機器の全部のコマンドを入れる・外す。"""
    positions = set(all_positions(log))
    return checked | positions if on else checked - positions


def send_positions(log: LoadedLog | None, checked: Collection[Position], device: int, segment: int) -> list[Position]:
    """AI に送る区間。チェックが 0 なら表示中の 1 区間、1 つ以上ならチェックした区間全部(機器の順・区間の順)。"""
    if log is None:
        return []
    found = [position for position in all_positions(log) if position in checked]
    if found:
        return found
    return [(device, segment)] if valid_position(log, device, segment) else []


def scope_text(log: LoadedLog | None, checked: Collection[Position], device: int, segment: int) -> str | None:
    """送る区間の行の文(AI の欄に出す)。ログが無ければ None。"""
    if log is None:
        return None
    found = [position for position in all_positions(log) if position in checked]
    if found:
        return log_view_scope_checked(len(found), len({d for d, _s in found}))
    if not valid_position(log, device, segment):
        return None
    chosen = log.devices[device].segments[segment]
    return log_view_scope_shown(device_name(log, device), chosen.command if chosen.command else LOG_VIEW_NO_PROMPT)


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
    """選んでいる区間のコマンド・元の行・出力の行。コマンドが無い区間(区切りなし)の command は None。区間が無ければ空。

    prompt_line はその区間を始めたプロンプトの行(`sw-test1#show version`。無ければ None。見せるためだけ。REQ-380)。
    """
    segments = log.devices[device].segments
    if not 0 <= segment < len(segments):
        return {"command": None, "prompt_line": None, "lines": []}
    chosen = segments[segment]
    return {
        "command": chosen.command if chosen.command else None,
        "prompt_line": chosen.prompt_line,
        "lines": list(chosen.lines),
    }


def write_log_device_files(text: str, parent: str | Path) -> DeviceSplitSingleWritten:
    """1 つの文を機器ごとのファイルに書き出す(書けなければ batch_io.DeviceSplitError)。"""
    return write_device_split_single(text, parent)
