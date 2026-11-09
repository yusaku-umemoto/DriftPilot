"""伏せる表の被覆の測定(ADR-0019 決定6、ADR-0011 の被覆率と同じ運用)。`driftpilot mask-coverage` が使う。

config の全行を、差分と同じ3段(ゲート → 表 → 送らない)に通し、4区分の行数と、送らなかった形の上位を出す。
送らなかった形は表に足す候補である。形は行頭から最初の秘密語までで、値を含まない。
空行は数えない。PEM ブロックの行は「送らない」に入れ、ブロック数を別に数える。
割れた秘密の行のかたまり(ADR-0137)の行も「送らない」に入れるが、PEM の数には入れない。形は `<折り返しの続きがある秘密の行>` だけで、
行の中身を残さない。
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from driftpilot.coverage_report import FOLD_VERSION
from driftpilot.llm_analyzer.mask_patterns import MASK_PATTERNS, masking_fingerprint
from driftpilot.llm_analyzer.masking import (
    WRAPPED_SHAPE,
    Masker,
    ancestors_by_indent,
    block_index,
    pem_blocks,
    pem_start_shape,
    scope_label,
)

CATEGORIES = ("plain", "mask", "pass", "unsent")
_LABEL = {
    "plain": "秘密語なし → そのまま送る",
    "mask": "秘密語あり → 表で伏せる位置が決まった",
    "pass": "秘密語あり → 表で「伏せる対象なし」と決まった",
    "unsent": "秘密語あり → 判定不能で送らない(PEM ブロックを含む)",
}
DEFAULT_TOP = 30


@dataclass
class MaskCoverage:
    files: int = 0
    failed: list[str] = field(default_factory=list)
    counts: Counter[str] = field(default_factory=Counter)
    pem_blocks: int = 0
    pem_lines: int = 0
    hits: Counter[str] = field(default_factory=Counter)  # パターン ID → 行数
    shapes: Counter[tuple[str, str]] = field(default_factory=Counter)  # (scope, 形) → 行数

    @property
    def total(self) -> int:
        return sum(self.counts[c] for c in CATEGORIES)


def measure_text(text: str, report: MaskCoverage, masker: Masker) -> None:
    lines = text.splitlines()
    ancestors = ancestors_by_indent(lines, masker)
    blocks = block_index(lines, masker)
    pem = {index for start, end in pem_blocks(lines) for index in range(start, end + 1)}
    for index, line in enumerate(lines):
        if index in pem:
            report.counts["unsent"] += 1
            report.pem_lines += 1
            if blocks[index] == index:
                report.pem_blocks += 1
                report.shapes[(scope_label(ancestors[index]), pem_start_shape(line))] += 1
            continue
        if index in blocks:  # 割れた秘密の行のかたまり
            report.counts["unsent"] += 1
            report.shapes[(scope_label(ancestors[index]), WRAPPED_SHAPE)] += 1
            continue
        if not line.strip():
            continue
        judged = masker.judge(line, ancestors[index])
        report.counts[judged.decision] += 1
        if judged.pattern_id:
            report.hits[judged.pattern_id] += 1
        if judged.decision == "unsent":
            scope, _, shape = judged.shape.partition("] ")
            report.shapes[(scope.lstrip("["), shape)] += 1


def resolve_paths(paths: list[str]) -> list[Path]:
    """ファイルはそのまま、ディレクトリは下の `*.txt` を名前順に(`_` で始まる名前は除く)。"""
    found: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            found.extend(p for p in sorted(path.rglob("*.txt")) if not p.name.startswith("_"))
        elif path.is_file():
            found.append(path)
        else:
            raise FileNotFoundError(f"{raw}: 読めません(存在しないか、ファイルでもディレクトリでもありません)")
    return found


def measure(paths: list[Path], *, secret_words: tuple[str, ...] | None = None) -> MaskCoverage:
    masker = Masker(secret_words=secret_words) if secret_words else Masker()
    report = MaskCoverage()
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.failed.append(f"{path}: 読めません ({type(exc).__name__})")
            continue
        report.files += 1
        measure_text(text, report, masker)
    return report


def _ratio(n: int, total: int) -> str:
    return f"{n / total * 100:.2f}%" if total else "-"


def to_text(report: MaskCoverage, top: int = DEFAULT_TOP) -> str:
    fp = masking_fingerprint()
    total = report.total
    out = [
        f"伏せる表の被覆: {report.files} ファイル、{total:,} 行(空行を除く)。表の指紋 {fp.digest}({fp.count} 件)、{FOLD_VERSION}",
        "",
    ]
    for c in CATEGORIES:
        out.append(f"  {_LABEL[c]}: {report.counts[c]:,} 行 ({_ratio(report.counts[c], total)})")
    out.append(f"  うち PEM ブロック: {report.pem_blocks} 件 / {report.pem_lines:,} 行")
    unhit = [p.id for p in MASK_PATTERNS if report.hits[p.id] == 0]
    out.append(f"  一度も当たらなかった表の行: {', '.join(unhit) if unhit else 'なし'}")
    out.append("")
    out.append(f"送らなかった形の上位 {top} 件(表に足す候補。値は含まない):")
    for (scope, shape), n in report.shapes.most_common(top):
        out.append(f"  {n:6d}  [{scope}] {shape}")
    for message in report.failed:
        out.append(f"エラー: {message}")
    return "\n".join(out) + "\n"


def to_json(report: MaskCoverage, top: int = DEFAULT_TOP) -> str:
    fp = masking_fingerprint()
    payload = {
        "masking_fingerprint": fp.digest,
        "patterns_count": fp.count,
        "fold_version": FOLD_VERSION,
        "files": report.files,
        "total": report.total,
        "counts": {c: report.counts[c] for c in CATEGORIES},
        "pem": {"blocks": report.pem_blocks, "lines": report.pem_lines},
        "hits": {p.id: report.hits[p.id] for p in MASK_PATTERNS},
        "unsent_shapes": [{"scope": s, "shape": shape, "lines": n} for (s, shape), n in report.shapes.most_common(top)],
        "failed": report.failed,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
