"""config パーサーの被覆率の測定(ADR-0011)。"""

from driftpilot.coverage_report.fold import FOLD_VERSION, fold_line, fold_word, scope_label
from driftpilot.coverage_report.fortinet_dedicated import DEFAULT_TABLES, block_name, is_default_table
from driftpilot.coverage_report.measure import (
    CoverageReport,
    FileResult,
    Group,
    InputError,
    InputFile,
    measure,
    measure_text,
    resolve_inputs,
)
from driftpilot.coverage_report.render import to_json, to_text

__all__ = [
    "DEFAULT_TABLES",
    "FOLD_VERSION",
    "CoverageReport",
    "FileResult",
    "Group",
    "InputError",
    "InputFile",
    "block_name",
    "fold_line",
    "fold_word",
    "is_default_table",
    "measure",
    "measure_text",
    "resolve_inputs",
    "scope_label",
    "to_json",
    "to_text",
]
