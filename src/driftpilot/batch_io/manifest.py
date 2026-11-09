"""マニフェストの読み書き(ADR-0016 決定2)。ファイルを開くのは入口の責務なので core ではなくここに置く。"""

import os
from pathlib import Path
from typing import Any

import yaml

from driftpilot.core import Manifest, ManifestError, parse_manifest
from driftpilot.diff_engine import NormalizationRule, load_rules


class ManifestWriteError(Exception):
    """マニフェストを書けない(既にある、保存先のフォルダが無いなど)。"""


def load_manifest(path: str | os.PathLike[str]) -> Manifest:
    """マニフェストを読んで解釈する。相対パスはマニフェストファイルの位置を基準に解決する(作業ディレクトリではない)。"""
    source = Path(path)
    return parse_manifest(_read_yaml(source), base_dir=source.absolute().parent)


def load_manifest_data(path: str | os.PathLike[str]) -> Any:
    """マニフェストの YAML を読み、解釈する前の形(dict)で返す(一括比較の窓で組を直すため。REQ-212 §2)。

    読み方と ManifestError は load_manifest と同じ。両側の path と、job と defaults の rules が相対パスなら、
    マニフェストファイルの位置を基準に絶対パスにする(parse_manifest の解決と同じ)。形の誤りは直さない(検査は parse_manifest)。
    """
    source = Path(path)
    data = _read_yaml(source)
    base_dir = source.absolute().parent
    if not isinstance(data, dict):
        return data
    defaults = data.get("defaults")
    if isinstance(defaults, dict):
        _absolute_key(defaults, "rules", base_dir)
    jobs = data.get("jobs")
    if isinstance(jobs, list):
        for job in jobs:
            if not isinstance(job, dict):
                continue
            _absolute_key(job, "rules", base_dir)
            for side in ("before", "after"):
                if isinstance(job.get(side), dict):
                    _absolute_key(job[side], "path", base_dir)
    return data


def _absolute_key(data: dict, key: str, base_dir: Path) -> None:
    value = data.get(key)
    if isinstance(value, str) and value and not Path(value).is_absolute():
        data[key] = str((base_dir / value).absolute())


def _read_yaml(source: Path) -> Any:
    """YAML を読む(UTF-8・YAML の誤りは ManifestError)。"""
    try:
        text = source.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ManifestError([(str(source), "UTF-8 として読めません")]) from exc
    except OSError as exc:
        raise ManifestError([(str(source), f"読めません ({exc.strerror or exc})")]) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ManifestError([(str(source), f"YAML として読めません: {exc}")]) from exc
    return data


def load_job_rules(manifest: Manifest) -> dict[Path, list[NormalizationRule]]:
    """全 job の正規化ルールを、実行前に1ファイル1回ずつ読む。

    読めなければマニフェストのエラーにする(全 job が同じ理由で落ちるのを防ぐ。defaults の platform と同じ考え方)。
    """
    rules: dict[Path, list[NormalizationRule]] = {}
    problems: list[tuple[str, str]] = []
    for job in manifest.jobs:
        path = job.rules_path
        if path is None or path in rules or any(where == str(path) for where, _ in problems):
            continue
        try:
            rules[path] = load_rules(path)
        except UnicodeDecodeError:
            problems.append((str(path), "正規化ルールのファイルを UTF-8 として読めません"))
        except OSError as exc:
            problems.append((str(path), f"正規化ルールのファイルを読めません ({exc.strerror or exc})"))
        except ValueError as exc:
            problems.append((str(path), str(exc)))
    if problems:
        raise ManifestError(problems)
    return rules


def dump_manifest(data: dict[str, Any]) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)


def write_manifest(data: dict[str, Any], path: str | os.PathLike[str]) -> Path:
    """マニフェストを書き、書いたパスを返す。書く前に、書く位置を基準にして parse_manifest で検査する。

    既にあるファイルは上書きしない(排他作成)。親ディレクトリは自動では作らない(storage.save と同じ)。
    """
    target = Path(path)
    parse_manifest(data, base_dir=target.absolute().parent)
    try:
        with open(target, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(dump_manifest(data))
    except FileExistsError as exc:
        raise ManifestWriteError(f"{target}: 既にあります。上書きしません") from exc
    except FileNotFoundError as exc:
        raise ManifestWriteError(f"{target}: 書き出し先のフォルダがありません(自動では作りません)") from exc
    except OSError as exc:
        raise ManifestWriteError(f"{target}: 書けません ({exc.strerror or exc})") from exc
    return target
