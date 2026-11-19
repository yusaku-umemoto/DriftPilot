"""1 つのログを、機器ごと・コマンドごとに分けて見るための分け方(ADR-0248 決定2 / REQ-377 §4)。

機器は `split_by_device`、各機器のコマンドは `split_log`(Tab の跡・ページ送りの印・打ち間違いのエラーの区間の印など、
比較のときと同じ決まり)。ここは使うだけで、区間分け・機器の見分け方は変えない。
画面の側(screen_model・gui_web)は `split_log` を直接読めないので、この関数を通す(ADR-0015 の層の決まり)。
"""

from dataclasses import dataclass

from driftpilot.segmentation import is_error_output, split_by_device, split_log


@dataclass(frozen=True)
class LogSegment:
    """1 つのコマンドの区間。command は原文のコマンド(プロンプトが見つからない区間は None)。lines は出力の本文の行。"""

    number: int
    command: str | None
    lines: tuple[str, ...]
    has_preamble: bool
    is_error: bool

    @property
    def line_count(self) -> int:
        return len(self.lines)


@dataclass(frozen=True)
class LogDevice:
    """1 台分。hostname は機器の名前(1 台も見つからなかったときの全体の 1 台は None)。"""

    hostname: str | None
    segments: tuple[LogSegment, ...]


def log_devices(text: str) -> tuple[LogDevice, ...]:
    """text を機器ごと・コマンドごとに分ける。常に 1 台以上を返す(機器が 1 台も見つからなければ、全体を 1 台にする)。"""
    parts = [(part.hostname, part.text) for part in split_by_device(text)]
    if not parts:
        parts = [(None, text)]
    devices = []
    for hostname, body in parts:
        result = split_log(body)
        segments = tuple(
            LogSegment(
                number=segment.number,
                command=segment.command,
                lines=tuple(segment.body),
                has_preamble=bool(segment.preamble),
                is_error=is_error_output(segment),
            )
            for segment in result.segments
        )
        devices.append(LogDevice(hostname, segments))
    return tuple(devices)
