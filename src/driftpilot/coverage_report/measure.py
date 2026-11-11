"""config のパーサーの被覆率を測る(ADR-0011 決定6・6b)。

分母は「渡したファイル群」で、機種では絞り込まない。主はファイル単位の数字で、合計は参考値である。
通常版 / 既定値込み版 / 判定不能は別に集計し、混ぜない。

platform(fujitsu_sir など)を渡すと B型のパーサーで測る。B型は元の行の単位で数える(範囲を展開した1行は1行)。
展開してできたノードがすべて解釈済みのときだけ、その行を解釈済みとする。暗黙に作ったスコープは数えない。
fortinet(D型)も同じ経路で測る。数えるのは木のノードの行で、`end` / `next` と埋め込みの値の続きの行は数えない。
fortinet では同じ行の単位で「既定の表を除いた行のうち、専用に読めた割合」も数える(REQ-190、ADR-0139 決定1)。
解釈済み率の数え方は変えない。
"""

import csv
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from driftpilot import i18n
from driftpilot.config_parser import FlatNode, FlatTree, InterpretedLines, parse_config_traced
from driftpilot.config_parser.fortinet_tree import PLATFORM as FORTINET
from driftpilot.config_parser.platforms import config_patterns
from driftpilot.coverage_report.fold import FOLD_VERSION, fold_line, scope_label
from driftpilot.coverage_report.fortinet_dedicated import block_name, is_default_table, is_generic
from driftpilot.segmentation import classify_exposure

EXPOSURES = ("normal", "exposed", "unknown")
INDEX_NAME = "_index.tsv"
# 区間のコマンドを持たないことを示す _index.tsv の値(廃止した試作 split_terminal_log.py が書いた値)。判定の対象外なので unknown にする
NO_PROMPT = "(プロンプト行なし)"
# 入力の誤りの文(REQ-258。開発者向けのコマンドの誤りだが、英語のときに日本語を混ぜない)
_TEXTS_JA = {
    "index_missing": "{raw}: {index_name} が見つかりません(切り出し済みのディレクトリを指定してください)",
    "unreadable_input": "{raw}: 読めません(存在しないか、ファイルでもディレクトリでもありません)",
    "unreadable_index": "{index}: 読めません ({error})",
    "unreadable_file": "読めません ({error})",
    "parse_failed": "解析失敗 ({error})",
}
_TEXTS_EN = {
    "index_missing": "{raw}: {index_name} was not found (give a directory that has been split up)",
    "unreadable_input": "{raw}: cannot be read (it does not exist, or is neither a file nor a directory)",
    "unreadable_index": "{index}: cannot be read ({error})",
    "unreadable_file": "Cannot be read ({error})",
    "parse_failed": "Parsing failed ({error})",
}
INPUT_TEXTS = i18n.choose(_TEXTS_JA, _TEXTS_EN)
# 解釈済み率がこれ未満のファイルには「パーサー対象外の可能性」の印を付ける
OUT_OF_SCOPE_RATIO = 0.10


class InputError(Exception):
    """入力のパスが読めない・測れるものが無い。"""


@dataclass(frozen=True)
class InputFile:
    path: Path
    exposure: str


@dataclass
class FileResult:
    path: str
    exposure: str
    total: int = 0
    interpreted: int = 0
    error: str | None = None
    categories: Counter[str] = field(default_factory=Counter)  # カテゴリ → 解釈済み行数
    hits: Counter[str] = field(default_factory=Counter)  # パターン ID → ヒット数
    shapes: Counter[tuple[str, str]] = field(default_factory=Counter)  # (scope, 形) → 未解釈の行数
    duplicates: int = 0  # B型の重複の警告の件数(REQ-21 §7)。A型は常に 0
    # FortiGate の専用に読めた割合(REQ-190、ADR-0139 決定1)。fortinet のときだけ数え、ほかは 0 / 空のまま
    fgt_dedicated: int = 0  # 既定の表を除いた行のうち、汎用の形以外のパターンで解釈された行
    fgt_base: int = 0  # 既定の表を除いた行(割合の分母)
    fgt_generic_blocks: Counter[str] = field(default_factory=Counter)  # ブロック名 → 汎用の形の行数(既定の表は除く)

    @property
    def ratio(self) -> float:
        return self.interpreted / self.total if self.total else 0.0

    @property
    def possibly_out_of_scope(self) -> bool:
        return self.error is None and self.total > 0 and self.ratio < OUT_OF_SCOPE_RATIO


@dataclass
class Group:
    """同じ区分(normal / exposed / unknown)のファイルの集計。"""

    exposure: str
    files: list[FileResult]
    platform: str | None = None

    @property
    def measured(self) -> list[FileResult]:
        return [f for f in self.files if f.error is None]

    @property
    def failed(self) -> list[FileResult]:
        return [f for f in self.files if f.error is not None]

    @property
    def total(self) -> int:
        return sum(f.total for f in self.measured)

    @property
    def interpreted(self) -> int:
        return sum(f.interpreted for f in self.measured)

    @property
    def ratio(self) -> float:
        return self.interpreted / self.total if self.total else 0.0

    def _sum(self, attr: str) -> Counter:
        total: Counter = Counter()
        for f in self.measured:
            total.update(getattr(f, attr))
        return total

    @property
    def categories(self) -> Counter[str]:
        return self._sum("categories")

    @property
    def hits(self) -> Counter[str]:
        return self._sum("hits")

    @property
    def shapes(self) -> Counter[tuple[str, str]]:
        return self._sum("shapes")

    @property
    def fgt_dedicated(self) -> int:
        return sum(f.fgt_dedicated for f in self.measured)

    @property
    def fgt_base(self) -> int:
        return sum(f.fgt_base for f in self.measured)

    @property
    def fgt_generic_blocks(self) -> Counter[str]:
        return self._sum("fgt_generic_blocks")

    @property
    def unhit_patterns(self) -> list[str]:
        """一度も当たらなかったパターンの ID(行を読むパターンだけ)。"""
        hits = self.hits
        return [p.id for p in config_patterns(self.platform) if p.match is not None and hits[p.id] == 0]


@dataclass
class CoverageReport:
    fold_version: str
    groups: dict[str, Group]
    platform: str | None = None

    @property
    def files(self) -> list[FileResult]:
        return [f for g in self.groups.values() for f in g.files]


def resolve_inputs(paths: list[str], exposure: str = "normal") -> list[InputFile]:
    """渡されたパスを測定するファイルにする。

    ディレクトリは下にある _index.tsv を読み、command 列を classify_exposure に通して区分を決める。
    ファイルはそのまま、指定された区分(既定 normal)で測る。
    """
    inputs: list[InputFile] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            indexes = sorted(path.rglob(INDEX_NAME))
            if not indexes:
                raise InputError(INPUT_TEXTS["index_missing"].format(raw=raw, index_name=INDEX_NAME))
            for index in indexes:
                inputs.extend(_read_index(index))
        elif path.is_file():
            inputs.append(InputFile(path, exposure))
        else:
            raise InputError(INPUT_TEXTS["unreadable_input"].format(raw=raw))
    return inputs


def _read_index(index: Path) -> list[InputFile]:
    try:
        with index.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh, delimiter="\t"))
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(INPUT_TEXTS["unreadable_index"].format(index=index, error=exc)) from exc
    result: list[InputFile] = []
    for row in rows:
        name = row.get("file")
        if not name:
            continue
        command = row.get("command")
        if command == NO_PROMPT:
            command = None
        result.append(InputFile(index.parent / name, classify_exposure(command)))
    return result


def measure_text(text: str, path: str = "", exposure: str = "normal", platform: str | None = None) -> FileResult:
    """config のテキスト1つを測る。"""
    result = FileResult(path, exposure)
    _, tree, interpreted = parse_config_traced(text, platform)
    if isinstance(tree, FlatTree):
        _measure_flat(result, tree, interpreted)
        return result
    for node in tree.all_children():
        result.total += 1
        key = tuple(node.path())
        pattern_id = interpreted.pattern_id(node)
        if pattern_id is None:
            result.shapes[(scope_label(key), fold_line(key[-1]))] += 1
            continue
        result.interpreted += 1
        result.hits[pattern_id] += 1
        result.categories[pattern_id.split(".", 1)[0]] += 1
    return result


def flat_scope_label(path: tuple[str, ...], platform: str | None = None) -> str:
    """B型・D型の未解釈行の粗いスコープ。トップレベルは "top"、それ以外はスコープの名前を畳んだもの(例: remote <num> > ap <num>)。

    fortinet では edit のキーを `edit <key>` に畳む(キーは名前なので、値ごとに形が散らばらないように)。
    """
    if len(path) <= 1:
        return "top"
    return " > ".join(_fold_flat(scope, platform) for scope in path[:-1])


def _fold_flat(text: str, platform: str | None) -> str:
    """B型・D型の行の形。fortinet の edit の行はキーを `<key>` にする(A型・B型は fold_line のまま)。"""
    if platform == FORTINET and text.startswith("edit "):
        return "edit <key>"
    return fold_line(text)


def _measure_flat(result: FileResult, tree: FlatTree, interpreted: InterpretedLines) -> None:
    by_line: dict[int | None, list[FlatNode]] = {}
    for node in tree.all_children():
        by_line.setdefault(node.line_no, []).append(node)
    for nodes in by_line.values():
        result.total += 1
        ids = [interpreted.pattern_ids.get(node.path()) for node in nodes]
        if tree.platform == FORTINET:
            _count_fortinet_dedicated(result, nodes[0].path()[0], ids)
        missed = next((node for node, pattern_id in zip(nodes, ids) if pattern_id is None), None)
        if missed is not None:
            result.shapes[(flat_scope_label(missed.path(), tree.platform), _fold_flat(missed.text, tree.platform))] += 1
            continue
        result.interpreted += 1
        for pattern_id in dict.fromkeys(ids):
            result.hits[pattern_id] += 1
        result.categories[ids[0].split(".", 1)[0]] += 1
    result.duplicates = len(tree.warnings)


def _count_fortinet_dedicated(result: FileResult, top_line: str, ids: list[str | None]) -> None:
    """FortiGate の1行を、専用に読めた割合の数に足す(REQ-190、ADR-0139 決定1)。

    最上位のブロックが既定の表なら、どこにも数えない(専用でも分子に入れない)。
    それ以外は分母に入れ、解釈済みなら、汎用の形のパターンが1つも無いときだけ専用とする。
    汎用の形が1つでもあれば、ブロック別の汎用の形の行数に足す。未解釈の行は分母にだけ入る。
    """
    name = block_name(top_line)
    if is_default_table(name):
        return
    result.fgt_base += 1
    if any(pattern_id is None for pattern_id in ids):
        return
    if any(is_generic(pattern_id) for pattern_id in ids):  # type: ignore[arg-type]
        result.fgt_generic_blocks[name if name is not None else top_line] += 1
    else:
        result.fgt_dedicated += 1


def measure_file(item: InputFile, platform: str | None = None) -> FileResult:
    try:
        text = item.path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return FileResult(str(item.path), item.exposure, error=INPUT_TEXTS["unreadable_file"].format(error=type(exc).__name__))
    try:
        return measure_text(text, str(item.path), item.exposure, platform)
    except Exception as exc:  # noqa: BLE001 - 1ファイルの解析失敗で全体を止めない
        return FileResult(str(item.path), item.exposure, error=INPUT_TEXTS["parse_failed"].format(error=type(exc).__name__))


def measure(inputs: list[InputFile], platform: str | None = None) -> CoverageReport:
    """ファイル群を測る。区分ごとに別の Group にする(空の区分も持つ)。platform は B型の機種(省略は A型)。"""
    groups = {e: Group(e, [], platform) for e in EXPOSURES}
    for item in inputs:
        groups[item.exposure].files.append(measure_file(item, platform))
    return CoverageReport(FOLD_VERSION, groups, platform)
