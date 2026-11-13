"""端末ログの切り分け(ADR-0012)。"""

from driftpilot.segmentation.default_exposure import EXPOSURE_WORDS, Exposure, classify_exposure
from driftpilot.segmentation.devices import (
    DeviceText,
    device_filenames,
    find_devices,
    is_multi_device,
    split_by_device,
)
from driftpilot.segmentation.error_output import INPUT_ERROR_PATTERNS, INPUT_ERROR_PHRASES, is_error_output
from driftpilot.segmentation.input_forms import (
    InputHint,
    InputNote,
    InputText,
    diagnose_input,
    normalize_input,
    normalize_text,
    read_config_input,
)
from driftpilot.segmentation.pairing import SegmentPair, command_key, has_repeats, pair_segments, pair_within
from driftpilot.segmentation.selection import Candidates, SelectionError, select
from driftpilot.segmentation.splitter import Segment, SplitResult, split_log

__all__ = [
    "EXPOSURE_WORDS",
    "INPUT_ERROR_PATTERNS",
    "INPUT_ERROR_PHRASES",
    "Candidates",
    "DeviceText",
    "Exposure",
    "InputHint",
    "InputNote",
    "InputText",
    "Segment",
    "SegmentPair",
    "SelectionError",
    "SplitResult",
    "classify_exposure",
    "command_key",
    "device_filenames",
    "diagnose_input",
    "find_devices",
    "is_error_output",
    "is_multi_device",
    "normalize_input",
    "normalize_text",
    "has_repeats",
    "pair_segments",
    "pair_within",
    "read_config_input",
    "select",
    "split_by_device",
    "split_log",
]
