"""show 側の被覆率(特定率とテンプレート保有率)の測定(ADR-0011 決定8、REQ-17)。

config 側の被覆率(coverage_report)とは指標が別物のため、パッケージも出力も分ける。
"""

from driftpilot.coverage_show.commands import CATEGORIES, SHOW_COMMANDS, ShowCommand, match_command
from driftpilot.coverage_show.measure import (
    REJECTED_PLATFORMS,
    InputItem,
    SegmentResult,
    ShowCoverageReport,
    measure,
    resolve_inputs,
    template_status,
)
from driftpilot.coverage_show.render import to_json, to_text

__all__ = [
    "CATEGORIES",
    "REJECTED_PLATFORMS",
    "SHOW_COMMANDS",
    "InputItem",
    "SegmentResult",
    "ShowCommand",
    "ShowCoverageReport",
    "match_command",
    "measure",
    "resolve_inputs",
    "template_status",
    "to_json",
    "to_text",
]
