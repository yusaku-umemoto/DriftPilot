"""show 側の被覆率を測る(ADR-0011 決定8、REQ-17)。

区間ごとに次の順で判定する。
1. sniff(Tier 1)が config と判定した区間は分母から外し、件数だけ数える。ambiguous は show 側に含める
2. 取得コマンド(Segment.command)があれば「特定できた」。無ければ「特定できなかった」
   (sniff はどのコマンドの出力かを返さないため、本文からのコマンド特定はしない。decisions.md Q1)
3. platform があれば、parse_output でテンプレートの有無を判定する(REQ-07 の diff_engine/show.py と同じ経路)

platform は推測しない。与えられなければテンプレート保有率は「測定不能」とする。
"""

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from ntc_templates.parse import ParsingException, parse_output
from textfsm import TextFSMError
from textfsm.clitable import CliTableError

from driftpilot import i18n
from driftpilot.coverage_report.measure import INDEX_NAME, INPUT_TEXTS, NO_PROMPT, InputError
from driftpilot.coverage_show.commands import SHOW_COMMANDS, match_command
from driftpilot.format_detect import DetectSettings, Verdict, load_settings, sniff
from driftpilot.segmentation import split_log

# none: テンプレートなし(ParsingException)。extract_failed: テンプレートはあるが行を抽出できない(raw に落ちる)
TemplateStatus = Literal["structured", "extract_failed", "none"]

# 特定できなかった区間の一覧に出す行数と、1行の長さの上限(全文は出さない)
HEAD_LINES = 3
HEAD_WIDTH = 120

# 存在しない platform 名 → 案内。ntc-templates の index は読まないため、判別できるのはここに書いたものだけ
_REJECTED_JA = {"cisco_xe": "IOS-XE は cisco_ios を指定してください"}
_REJECTED_EN = {"cisco_xe": "Use cisco_ios for IOS-XE"}
REJECTED_PLATFORMS = i18n.choose(_REJECTED_JA, _REJECTED_EN)


@dataclass(frozen=True)
class InputItem:
    """測る入力1つ。index_row が True なら _index.tsv の1行で、ファイル全体を1区間とする。"""

    path: Path
    index_row: bool = False
    command: str | None = None


@dataclass(frozen=True)
class SegmentResult:
    source: str
    number: int
    command: str | None
    verdict: Verdict
    head: tuple[str, ...]
    status: TemplateStatus | None = None  # platform が無い・特定できない・config のときは None
    matched_id: str | None = None  # 表(SHOW_COMMANDS)のどれに当たったか

    @property
    def is_show(self) -> bool:
        return self.verdict != "config"

    @property
    def identified(self) -> bool:
        return self.is_show and self.command is not None


@dataclass
class ShowCoverageReport:
    platform: str | None
    segments: list[SegmentResult] = field(default_factory=list)
    files: int = 0  # 読めたファイルの数
    errors: list[tuple[str, str]] = field(default_factory=list)  # (パス, 理由)

    @property
    def measurable(self) -> bool:
        """テンプレート保有率が測れるか。platform が無ければ測定不能。"""
        return self.platform is not None

    @property
    def config_excluded(self) -> int:
        return sum(1 for s in self.segments if not s.is_show)

    @property
    def show_segments(self) -> list[SegmentResult]:
        return [s for s in self.segments if s.is_show]

    @property
    def identified(self) -> list[SegmentResult]:
        return [s for s in self.segments if s.identified]

    @property
    def unidentified(self) -> list[SegmentResult]:
        return [s for s in self.segments if s.is_show and not s.identified]

    @property
    def identification_ratio(self) -> float:
        whole = len(self.show_segments)
        return len(self.identified) / whole if whole else 0.0

    def _count(self, status: TemplateStatus) -> int:
        return sum(1 for s in self.identified if s.status == status)

    @property
    def with_template(self) -> int | None:
        """保有率の分子。テンプレートが無い(none)以外。測定不能なら None。"""
        if not self.measurable:
            return None
        return self._count("structured") + self._count("extract_failed")

    @property
    def structured(self) -> int | None:
        return self._count("structured") if self.measurable else None

    @property
    def extract_failed(self) -> int | None:
        return self._count("extract_failed") if self.measurable else None

    @property
    def template_ratio(self) -> float | None:
        if not self.measurable:
            return None
        whole = len(self.identified)
        return (self.with_template or 0) / whole if whole else 0.0

    def _by_command(self, status: TemplateStatus) -> list[tuple[str, int]]:
        counts = Counter(s.command for s in self.identified if s.status == status)
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))

    @property
    def no_template(self) -> list[tuple[str, int]]:
        """テンプレートが無いコマンドと区間数(多い順)。raw 比較に落ちる。"""
        return self._by_command("none")

    @property
    def failed_extraction(self) -> list[tuple[str, int]]:
        """テンプレートはあるが行を抽出できなかったコマンドと区間数(多い順)。raw 比較に落ちる。"""
        return self._by_command("extract_failed")

    @property
    def per_command(self) -> list[tuple[str, int, int | None]]:
        """表の各コマンドの (ID, 出現区間数, テンプレートあり区間数)。測定不能ならテンプレートありは None。"""
        rows = []
        for c in SHOW_COMMANDS:
            hits = [s for s in self.identified if s.matched_id == c.id]
            with_template = sum(1 for s in hits if s.status in ("structured", "extract_failed"))
            rows.append((c.id, len(hits), with_template if self.measurable else None))
        return rows

    @property
    def unseen(self) -> list[str]:
        """表のコマンドのうち、入力に1件も現れなかったものの ID。"""
        return [cid for cid, appeared, _ in self.per_command if appeared == 0]

    @property
    def outside_list(self) -> int:
        """特定できたが表のどれにも当たらなかった区間の数(保有率の分母には含む)。"""
        return sum(1 for s in self.identified if s.matched_id is None)

    @property
    def platform_suspect(self) -> bool:
        """特定できた区間がすべてテンプレートなし。platform 名の誤りが疑われる。"""
        identified = self.identified
        return self.measurable and bool(identified) and all(s.status == "none" for s in identified)


def resolve_inputs(paths: list[str]) -> list[InputItem]:
    """渡されたパスを測る入力にする(config 側と同じ受け口)。

    ディレクトリは下にある _index.tsv を読み、各行のファイルを1区間とする(split_log はかけ直さない)。
    command 列が「(プロンプト行なし)」なら取得コマンドなしとする。ファイルは split_log で区間に分ける。
    """
    items: list[InputItem] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            indexes = sorted(path.rglob(INDEX_NAME))
            if not indexes:
                raise InputError(INPUT_TEXTS["index_missing"].format(raw=raw, index_name=INDEX_NAME))
            for index in indexes:
                items.extend(_read_index(index))
        elif path.is_file():
            items.append(InputItem(path))
        else:
            raise InputError(INPUT_TEXTS["unreadable_input"].format(raw=raw))
    return items


def _read_index(index: Path) -> list[InputItem]:
    try:
        with index.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(INPUT_TEXTS["unreadable_index"].format(index=index, error=exc)) from exc
    items: list[InputItem] = []
    for row in rows:
        name = row.get("file")
        if not name:
            continue
        command = (row.get("command") or "").strip() or None
        if command == NO_PROMPT:
            command = None
        items.append(InputItem(index.parent / name, index_row=True, command=command))
    return items


def template_status(platform: str, command: str, text: str) -> TemplateStatus:
    """テンプレートの有無を、diff_engine/show.py の compare_show と同じ経路で判定する。

    parse_output が ParsingException なら none(index に組がない)。CliTableError / TextFSMError、
    または非空の本文から1行も取れなければ extract_failed(compare_show は raw に落とす)。それ以外は structured。
    省略形の解決は parse_output に任せる。
    """
    try:
        rows = parse_output(platform=platform, command=command, data=text)
    except ParsingException:
        return "none"
    except (CliTableError, TextFSMError):
        return "extract_failed"
    if text.strip() and not rows:
        return "extract_failed"
    return "structured"


def _head(body: tuple[str, ...]) -> tuple[str, ...]:
    lines = [line for line in body if line.strip()][:HEAD_LINES]
    return tuple(line if len(line) <= HEAD_WIDTH else line[:HEAD_WIDTH] + "…" for line in lines)


def measure_segment(
    source: str,
    number: int,
    command: str | None,
    body: tuple[str, ...],
    platform: str | None,
    settings: DetectSettings,
) -> SegmentResult:
    text = "\n".join(body) + "\n"
    verdict = sniff(text, settings=settings).verdict
    head = _head(body)
    if verdict == "config" or command is None:
        return SegmentResult(source, number, command, verdict, head)
    matched = match_command(command)
    status = template_status(platform, command, text) if platform else None
    return SegmentResult(source, number, command, verdict, head, status, matched.id if matched else None)


def _segments(item: InputItem, text: str) -> list[tuple[int, str | None, tuple[str, ...]]]:
    if item.index_row:
        body = text.replace("\r\n", "\n").split("\n")
        while body and not body[-1].strip():
            body.pop()
        return [(1, item.command, tuple(body))] if any(line.strip() for line in body) else []
    # 切り分けなしの縮退(1ファイル=1区間)では本文が空の区間も返るため、空は外す
    return [(s.number, s.command, s.body) for s in split_log(text).segments if any(line.strip() for line in s.body)]


def measure(items: list[InputItem], platform: str | None = None) -> ShowCoverageReport:
    """入力群を測る。1ファイルの失敗では全体を止めない(config 側と同じ)。"""
    settings = load_settings()
    report = ShowCoverageReport(platform)
    for item in items:
        source = str(item.path)
        try:
            text = item.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            report.errors.append((source, INPUT_TEXTS["unreadable_file"].format(error=type(exc).__name__)))
            continue
        try:
            results = [
                measure_segment(source, number, command, body, platform, settings)
                for number, command, body in _segments(item, text)
            ]
        except Exception as exc:  # noqa: BLE001 - 1ファイルの解析失敗で全体を止めない
            report.errors.append((source, INPUT_TEXTS["parse_failed"].format(error=type(exc).__name__)))
            continue
        report.files += 1
        report.segments.extend(results)
    return report
