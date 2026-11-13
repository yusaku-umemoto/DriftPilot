"""一括比較の入口の共通部分(REQ-15)。CLI と GUI の両方から呼ぶ。

マニフェストの YAML の読み書き、入力・正規化ルールの読み込み、JSON Lines の1行の組み立て、サマリの JSON、
同名で対にする補助(ADR-0020)を持つ。比較と集計は core.batch にある(ADR-0016 決定1)。
"""

from driftpilot.batch_io.device_split import DeviceSplitError, DeviceSplitWritten, write_device_split
from driftpilot.batch_io.hostnames import read_hostname
from driftpilot.batch_io.inputs import make_prepare
from driftpilot.batch_io.manifest import (
    ManifestWriteError,
    dump_manifest,
    load_job_rules,
    load_manifest,
    load_manifest_data,
    write_manifest,
)
from driftpilot.batch_io.pairing import Pairing, PairingError, manifest_path, pair_by_name, pairing_manifest
from driftpilot.batch_io.records import (
    JSONL_SUFFIX,
    SUMMARY_SCHEMA_VERSION,
    JsonLinesWriter,
    ResultsWriteError,
    job_record,
    job_records,
    multi_filename,
    pair_record,
    summary_dict,
    write_job_lines,
    write_new_bytes,
    write_records,
    write_text_file,
)
from driftpilot.segmentation import DeviceText, device_filenames, find_devices, is_multi_device, split_by_device

__all__ = [
    "DeviceSplitError",
    "DeviceSplitWritten",
    "DeviceText",
    "device_filenames",
    "find_devices",
    "is_multi_device",
    "split_by_device",
    "write_device_split",
    "JSONL_SUFFIX",
    "SUMMARY_SCHEMA_VERSION",
    "JsonLinesWriter",
    "ManifestWriteError",
    "Pairing",
    "PairingError",
    "ResultsWriteError",
    "dump_manifest",
    "job_record",
    "job_records",
    "load_job_rules",
    "load_manifest",
    "load_manifest_data",
    "make_prepare",
    "manifest_path",
    "multi_filename",
    "pair_by_name",
    "pair_record",
    "pairing_manifest",
    "read_hostname",
    "summary_dict",
    "write_job_lines",
    "write_manifest",
    "write_new_bytes",
    "write_records",
    "write_text_file",
]
