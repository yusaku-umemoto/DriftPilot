"""平坦な config(B型)を、行頭スコープ表で木に組み直す(ADR-0024 決定1〜5)。hier_config は使わない。

組み立てた木は、下流(extractor・被覆率・安全網)が Cisco 側の木に使う5つの機能を同じ名前で持つ。
children / text / path() / all_children() / unified_diff()。

- 表に当たった行は、スコープのノードの子になる(スコープ部分を除いた残りが text)。残りが無い行はスコープのノードそのもの
- 親の見出し行が無いので、スコープのノードは暗黙に作る(implicit)。暗黙のノードは all_children() に出ない
- 添字の範囲・列挙は、表の expand の位置でだけ展開する。1行が N 個のスコープに入る(行番号は元の1行のもの)
- 表に無い行、展開できない範囲(逆順、MAX_EXPANSION を超える)は、行全体を1つのトップレベルのノードにする(未解釈)
- 同じスコープの下の重複(同じ行、同じ属性で値が食い違う行)は捨てずに残し、warnings に行番号だけを記録する
"""

import itertools
import re
from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from driftpilot.config_parser.flat_scopes import SIR_SCOPES, FlatScope, compiled

# ノードが行として木に付いた順の通し番号(行番号が同じノードの並びに使う。値そのものに意味は無い)
_ATTACH_ORDER = itertools.count()

# 1行を展開してできるスコープの数の上限。超える行は展開せずに未解釈にする
MAX_EXPANSION = 4096

# 値に見える語(IPv4 / IPv6 / MAC / 範囲・列挙 / 長い16進数)。数字だけの語は添字のことが多いので含めない
_VALUE_WORD = re.compile(
    r"\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?"
    r"|[0-9A-Fa-f]*:[0-9A-Fa-f:]*(?:/\d{1,3})?"
    r"|\d+(?:[-,]\d+)+"
    r"|[0-9A-Fa-f]{20,}"
)


@dataclass(frozen=True)
class DuplicateLine:
    """同じスコープの下の重複1件。値は持たない(スコープのパスと元の行番号だけ)。scope が空ならトップレベル。"""

    scope: tuple[str, ...]
    lines: tuple[int, ...]


class _Container:
    def __init__(self) -> None:
        self.children: list[FlatNode] = []
        self._scopes: dict[str, FlatNode] = {}

    def _add(self, node: "FlatNode") -> "FlatNode":
        self.children.append(node)
        return node

    def _scope(self, name: str, scope_id: str, parent: "FlatNode | None") -> "FlatNode":
        node = self._scopes.get(name)
        if node is None:
            node = self._add(FlatNode(name, parent, line_no=None, scope_id=scope_id))
            self._scopes[name] = node
        return node


class FlatNode(_Container):
    """木の1ノード。line_no は元の config の行番号(1始まり)。暗黙に作ったスコープのノードは None。

    repeated_scope は、スコープだけの行(`lan 0`)の2回目以降。捨てずに、スコープのノードの children に
    出現順のまま置く(all_children() の順序を元の行の順に保つため)。parent はスコープのノードの親なので、
    path() はスコープのノードと同じになる。子は持たず、スコープとしても属性の行としても扱わない
    (重複は build_flat_tree が別に記録する)。
    """

    def __init__(
        self,
        text: str,
        parent: "FlatNode | None",
        line_no: int | None,
        scope_id: str | None = None,
        *,
        repeated_scope: bool = False,
    ) -> None:
        super().__init__()
        self.text = text
        self.parent = parent
        self.line_no = line_no
        self.scope_id = scope_id
        self.repeated_scope = repeated_scope
        # 行として木に付いた順(all_children() で同じ行番号のノードを並べる)。暗黙のノードでは使わない
        self.order = next(_ATTACH_ORDER)

    @property
    def implicit(self) -> bool:
        """親の見出し行が無いために作ったノードか(実在する行ではない)。"""
        return self.line_no is None

    def path(self) -> tuple[str, ...]:
        texts: list[str] = []
        node: FlatNode | None = self
        while node is not None:
            texts.append(node.text)
            node = node.parent
        return tuple(reversed(texts))

    def __repr__(self) -> str:
        return f"FlatNode({' > '.join(self.path())!r}, line_no={self.line_no})"


class FlatTree(_Container):
    """組み立てた木。warnings は重複の記録(値を含めない)。"""

    def __init__(self, platform: str) -> None:
        super().__init__()
        self.platform = platform
        self.warnings: list[DuplicateLine] = []
        self._dup_lines: list[DuplicateLine] = []

    def all_children(self) -> Iterator[FlatNode]:
        """実在する行のノードを、元の config の行の順で返す(暗黙のノードは返さず、その子は返す)。

        離れて現れた同じスコープの行は木では合流するが、返す順は元の行番号の順にする(unparsed もこの順になる)。
        同じ行から展開したノードどうしは、展開した順(`ether 1 1-2 …` なら ether 1 1、ether 1 2 の順)。
        """

        def walk(nodes: list[FlatNode]) -> Iterator[FlatNode]:
            for node in nodes:
                if not node.implicit:
                    yield node
                yield from walk(node.children)

        return iter(sorted(walk(self.children), key=lambda node: (node.line_no, node.order)))

    def unified_diff(self, target: "FlatTree") -> Iterator[str]:
        """self から target への、行のパスの多重集合の差。`- パス` / `+ パス` の形(安全網3は行数だけを使う)。"""
        left = Counter(node.path() for node in self.all_children())
        right = Counter(node.path() for node in target.all_children())
        for paths, sign, order in ((left - right, "-", self), (right - left, "+", target)):
            remaining = Counter(paths)
            for node in order.all_children():
                path = node.path()
                if remaining[path] > 0:
                    remaining[path] -= 1
                    yield f"{sign} {' > '.join(path)}"

    def add_duplicates(self, found: Iterable[DuplicateLine]) -> None:
        """extractor が見つけた値の食い違いを足し、行を共有する記録を1件にまとめ直す。"""
        self._dup_lines.extend(found)
        self.warnings = _merge(self._dup_lines)

    def exclude_duplicates(self, scope: tuple[str, ...], lines: Iterable[int]) -> None:
        """extractor が自分の規則で重複を決める行を、それまでの記録から外す。1行だけになった記録は捨てる。

        木の判定(line_key)は値の位置を知らないため、何本も書ける行(利用者ごとの行など)を重複とみなすことがある。
        """
        drop = set(lines)
        kept: list[DuplicateLine] = []
        for record in self._dup_lines:
            rest = record.lines if record.scope != scope else tuple(line for line in record.lines if line not in drop)
            if len(rest) > 1:
                kept.append(DuplicateLine(record.scope, rest))
        self._dup_lines = kept
        self.warnings = _merge(kept)


def expand_indexes(token: str) -> list[int] | None:
    """`1-4` や `1,3` を数の並びにする。逆順の範囲、MAX_EXPANSION を超えるものは None。同じ数は1つにする。"""
    values: list[int] = []
    for part in token.split(","):
        low, dash, high = part.partition("-")
        if not dash:
            values.append(int(low))
            continue
        start, stop = int(low), int(high)
        if start > stop or len(values) + stop - start + 1 > MAX_EXPANSION:
            return None
        values.extend(range(start, stop + 1))
    return list(dict.fromkeys(values))


def _match(scopes: tuple[FlatScope, ...], line: str) -> tuple[FlatScope, re.Match[str]] | None:
    for scope in scopes:
        m = compiled(scope.match).match(line)
        if m is not None:
            return scope, m
    return None


def _combinations(scope: FlatScope, m: re.Match[str]) -> list[dict[str, str]] | None:
    """展開した添字の組み合わせ。展開できなければ None。"""
    groups = {k: v for k, v in m.groupdict().items() if v is not None}
    choices: list[list[tuple[str, str]]] = []
    total = 1
    for name in scope.expand:
        values = expand_indexes(groups[name])
        if values is None:
            return None
        total *= len(values)
        if total > MAX_EXPANSION:
            return None
        choices.append([(name, str(v)) for v in values])
    return [{**groups, **dict(combo)} for combo in itertools.product(*choices)]


def build_flat_tree(text: str, scopes: tuple[FlatScope, ...] = SIR_SCOPES, platform: str = "fujitsu_sir") -> FlatTree:
    """平坦な config のテキストを木にする。空行は飛ばす。行の前後の空白は落とす。"""
    tree = FlatTree(platform)
    by_id = {scope.id: scope for scope in scopes}
    bare: list[DuplicateLine] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        hit = _match(scopes, line)
        combos = _combinations(*hit) if hit is not None else None
        if hit is None or combos is None:
            tree._add(FlatNode(line, None, line_no))
            continue
        scope, m = hit
        rest = line[m.end():].strip()
        chain = [by_id[scope.parent], scope] if scope.parent else [scope]
        for groups in combos:
            container: _Container = tree
            node: FlatNode | None = None
            for entry in chain:
                node = container._scope(entry.name.format(**groups), entry.id, node)
                container = node
            if rest:
                node._add(FlatNode(rest, node, line_no))
            elif node.line_no is None:
                node.line_no = line_no
                node.order = next(_ATTACH_ORDER)
            else:  # スコープだけの行が2回ある。2回目も捨てずに、出現順の位置(スコープの children)に残す
                node._add(FlatNode(node.text, node.parent, line_no, repeated_scope=True))
                bare.append(DuplicateLine(node.path(), (node.line_no, line_no)))
    tree.add_duplicates([*bare, *find_duplicates(tree)])
    return tree


def line_key(text: str) -> tuple[str, ...]:
    """値の食い違いを比べるための「属性」の部分。値に見える最初の語より前、無ければ最後の語より前。

    残りが空になるとき(1語の行や、値から始まる行)は行全体を使う(同じ行どうしだけが重なる)。
    """
    words = tuple(text.split())
    for index, word in enumerate(words):
        if _VALUE_WORD.fullmatch(word):
            return words[:index] or words
    return words[:-1] or words


def find_duplicates(tree: FlatTree) -> list[DuplicateLine]:
    """同じスコープ(トップレベルを含む)の直下で、属性の部分が同じ行が2行以上あるものを返す。

    同じ行の重複と、同じ属性で値が食い違う行の両方が当たる。スコープのノード(暗黙のものを含む)と、
    スコープだけの行の2回目以降(build_flat_tree が記録済み)は比べない。
    """
    found: list[DuplicateLine] = []

    def visit(scope: tuple[str, ...], nodes: list[FlatNode]) -> None:
        groups: dict[tuple[str, ...], list[int]] = {}
        for node in nodes:
            if node.scope_id is not None:
                visit(node.path(), node.children)
            elif node.line_no is not None and not node.repeated_scope:
                groups.setdefault(line_key(node.text), []).append(node.line_no)
        found.extend(DuplicateLine(scope, tuple(lines)) for lines in groups.values() if len(lines) > 1)

    visit((), tree.children)
    return found


def _merge(records: list[DuplicateLine]) -> list[DuplicateLine]:
    """同じスコープで行番号を共有する記録を1件にまとめる。行番号の昇順、記録はスコープ・最初の行の順。"""
    by_scope: dict[tuple[str, ...], list[set[int]]] = {}
    for record in records:
        groups = by_scope.setdefault(record.scope, [])
        merged = set(record.lines)
        for group in [g for g in groups if g & merged]:
            merged |= group
            groups.remove(group)
        groups.append(merged)
    out = [DuplicateLine(scope, tuple(sorted(g))) for scope, groups in by_scope.items() for g in groups]
    return sorted(out, key=lambda d: (d.lines[0], d.scope))
