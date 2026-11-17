"""Junos の config の形(brace か set か)を見分けて、同じ 2 段の文の木に組む(ADR-0224 決定 1・4・6 の 2026-11-17 の追記。REQ-322)。
hier_config は使わない。

機種に依らない部品。機種の登録・IR への写しはここではしない(木を組むだけ)。

形の見分け(空行・`#` で始まる行・注釈 `/* … */`〔複数行の中も含む〕・引用符の外の ` ## …` を除き、右端の空白を除いて見る):
- `{` で終わる行があり、`set `・`deactivate ` で始まる行が無ければ brace
- `set `・`deactivate ` で始まる行があり、`{` で終わる行が無ければ set
- 両方あれば JunosStructureError("mixed")。行番号は、後に出た形の最初の行
- どちらも無ければ(空・コメントだけ・ほかの行だけ)set として読む(全部が最上位の実在の行になり、写しで未解釈に残る)

2 段の木(どちらの形でも同じ。区切りの表は使わない):
- 1 段目 = 最上位の語(文の最初の語)。暗黙のノード(line_no は None。scope_id は SCOPE_TOP)。同じ語は 1 つ
- 2 段目 = 文(最上位の語を除いた、根からの残りの語を空白 1 つでつないだもの。引用符は外さない)。line_no は元の行
  (brace は葉の行、set はその行)
- 残りの語の無い最上位の語(brace の `foo;`・set の `set foo`)は、1 段目のノードが実在の行(line_no あり・子なし)。
  2 回目は子に「選び直し」を置く(flat_tree と同じ)
- brace は brace_tree.build_brace_tree で入れ子の木を作り、根から葉までの見出しと葉のテキストをつないで文にする
  (子の無い見出しは、その見出しまでの経路が 1 つの文)。見出しか葉のテキストが `delete: `・`insert ` で始まるものは、
  文のテキストのまま入れて SCOPE_UNREAD を付ける(最上位の要素なら文に分けず、最上位の実在の行 SCOPE_OTHER にする)
- 無効の印: brace の `inactive: ` の付いた見出しか葉、set の `deactivate <経路>` は、`deactivate <最上位の語を除いた残りの経路>`
  の文を足す(経路が最上位の語だけなら `deactivate`)。brace の `inactive: ` の付いた要素の中の文は、`inactive: ` を外した
  経路でも普通の文として置く。行番号は brace は印の付いた行、set は `deactivate` の行。経路が木に無くてもエラーにしない
- `set`・`deactivate`・`protect`・`annotate` のどれでも始まらない set の行は、最上位の実在の行(SCOPE_OTHER。テキストは行全体)
- 同じ最上位の語の下に同じ文が 2 回出たら DuplicateLine((最上位の語,), 行番号の並び) を記録する(止まらない)

BraceStructureError・JunosSetStructureError は包まずにそのまま上に出す。

TREE_VERSION は、brace_tree の版・junos_set_tree の版・この見分けと木の作り方の規則をまとめた版で、表の指紋に入るのは
この 1 つだけ。brace_tree・junos_set_tree のどちらかの版や、この規則を変えたら、この版を上げる。
"""

from driftpilot import i18n
from driftpilot.config_parser.brace_tree import TREE_VERSION as BRACE_TREE_VERSION
from driftpilot.config_parser.brace_tree import _words, build_brace_tree
from driftpilot.config_parser.flat_tree import _ATTACH_ORDER, DuplicateLine, FlatNode, FlatTree
from driftpilot.config_parser.junos_set_tree import KIND_DEACTIVATE, KIND_SET, read_set_lines
from driftpilot.config_parser.junos_set_tree import TREE_VERSION as SET_TREE_VERSION

# 見分けの規則・木の作り方と、上の 2 つの版をまとめた版(`brace-tree-v2` + `junos-set-tree-v2` + 規則 → この版)
TREE_VERSION = "junos-tree-v3"
# まとめた元の版(テストが固定して、変えたときに気づけるようにする)
COMPOSED_OF = (BRACE_TREE_VERSION, SET_TREE_VERSION)

PLATFORM = "juniper_junos"

# FlatNode の scope_id に入れる印。最上位の語のノード・写しで読まずに未解釈に残す文・最上位の実在の行(未解釈)
SCOPE_TOP = "JUNOS-top"
SCOPE_UNREAD = "JUNOS-unread"
SCOPE_OTHER = "JUNOS-other"

INACTIVE = "inactive: "
DEACTIVATE = "deactivate"

# 読まずに未解釈に残す要素の頭(ADR-0224 決定 2)
_UNREAD_HEADS = ("delete: ", "insert ")

_QUOTE = '"'


class JunosStructureError(ValueError):
    """1 つの config に brace と set が混ざっている。

    reason は分岐に使える理由(mixed)、line_no は後に出た形の最初の行。
    文面に差し込むのは行番号だけで、行の中身・値は入れない。文面は理由の鍵から作る(言語で日本語・英語を選ぶ)。
    """

    def __init__(self, reason: str, line_no: int) -> None:
        self.reason = reason
        self.line_no = line_no
        super().__init__(_STRUCTURE["head"].format(line=line_no) + _STRUCTURE[reason].format())


# 形の誤りの文。理由の鍵 → 文の形。head は頭の文
_STRUCTURE_JA = {
    "head": "config の {line} 行目: ",
    "mixed": "{{ }} の形(show configuration)と set の形(display set)が 1 つの config に混ざっています",
}
_STRUCTURE_EN = {
    "head": "config, line {line}: ",
    "mixed": "the {{ }} form (show configuration) and the set form (display set) are mixed in one config",
}
_STRUCTURE = i18n.choose(_STRUCTURE_JA, _STRUCTURE_EN)


def _shape_text(line: str, state: dict[str, bool]) -> str:
    """見分けに使う行の姿。引用符の外の注釈 `/* … */`(state["comment"] は複数行をまたぐ印)と ` ## …` を落とし、両端の空白を除く。"""
    out: list[str] = []
    quoted = False
    index = 0
    size = len(line)
    while index < size:
        ch = line[index]
        if state["comment"]:
            if line.startswith("*/", index):
                state["comment"] = False
                out.append(" ")
                index += 2
            else:
                index += 1
        elif quoted:
            out.append(ch)
            if ch == "\\" and index + 1 < size:
                out.append(line[index + 1])
                index += 2
                continue
            if ch == _QUOTE:
                quoted = False
            index += 1
        elif ch == _QUOTE:
            quoted = True
            out.append(ch)
            index += 1
        elif line.startswith("/*", index):
            state["comment"] = True
            out.append(" ")
            index += 2
        elif line.startswith("##", index) and (not out or out[-1].isspace()):
            break
        else:
            out.append(ch)
            index += 1
    return "".join(out).strip()


def _shapes(text: str) -> tuple[int | None, int | None]:
    """(brace の形の最初の行番号, set の形の最初の行番号)。無ければ None。"""
    first_brace: int | None = None
    first_set: int | None = None
    state = {"comment": False}
    for line_no, raw in enumerate(text.splitlines(), 1):
        if not state["comment"] and raw.lstrip().startswith("#"):
            continue
        line = _shape_text(raw, state)
        if not line:
            continue
        if line.endswith("{"):
            first_brace = first_brace or line_no
        if line.startswith(("set ", "deactivate ")):
            first_set = first_set or line_no
    return first_brace, first_set


class _Builder:
    """2 段の木を作る。最上位の語のノード(暗黙)の下に文のノードを置く。brace と set の両方がここを通る。"""

    def __init__(
        self, platform: str = PLATFORM, scope_top: str = SCOPE_TOP, scope_other: str = SCOPE_OTHER
    ) -> None:
        # 3 つの引数の既定は Junos の値。PAN-OS(panos_tree)は自分の機種名・印を渡して同じ作りを使う(REQ-327。Junos の結果は変わらない)
        self.scope_top = scope_top
        self.scope_other = scope_other
        self.tree = FlatTree(platform)
        # (最上位の語, 文) → 出た行番号の並び(同じ文が 2 回以上出たら DuplicateLine にする)
        self.statements: dict[tuple[str, str], list[int]] = {}
        # 残りの語の無い最上位の語 → 出た行番号の並び
        self.top_lines: dict[str, list[int]] = {}

    def add(self, top: str, rest: list[str], line_no: int, scope_id: str | None = None) -> None:
        node = self.tree._scope(top, self.scope_top, None)
        if rest:
            text = " ".join(rest)
            node._add(FlatNode(text, node, line_no, scope_id=scope_id))
            self.statements.setdefault((top, text), []).append(line_no)
            return
        lines = self.top_lines.setdefault(top, [])
        if node.line_no is None:
            node.line_no = line_no
            node.order = next(_ATTACH_ORDER)
        else:  # 同じ最上位の語だけの行が 2 回ある。2 回目も捨てずに、出た順の位置に残す(flat_tree と同じ)
            node._add(FlatNode(node.text, node.parent, line_no, repeated_scope=True))
        lines.append(line_no)

    def other(self, text: str, line_no: int) -> None:
        """最上位の実在の行(写しで未解釈に残る)。"""
        self.tree._add(FlatNode(text, None, line_no, scope_id=self.scope_other))

    def finish(self) -> FlatTree:
        found = [DuplicateLine((top,), tuple(lines)) for (top, _), lines in self.statements.items() if len(lines) > 1]
        found += [DuplicateLine((top,), tuple(lines)) for top, lines in self.top_lines.items() if len(lines) > 1]
        self.tree.add_duplicates(found)
        return self.tree


def _build_set(text: str) -> FlatTree:
    builder = _Builder()
    for line_no, kind, words, line in read_set_lines(text):
        if kind == KIND_SET:
            builder.add(words[0], words[1:], line_no)
        elif kind == KIND_DEACTIVATE:
            builder.add(words[0], [DEACTIVATE, *words[1:]], line_no)
        else:
            builder.other(line, line_no)
    return builder.finish()


def _split_path(elements: list[str]) -> tuple[list[str], str, list[str], bool]:
    """見出しと葉のテキストの並び(根から)を、(`inactive: ` を外した並び, 最上位の語, 残りの語, 読まずに残すか)にする。"""
    stripped = [element.removeprefix(INACTIVE) for element in elements]
    words = _words(stripped[0])
    unread = any(item.startswith(_UNREAD_HEADS) for item in stripped)
    return stripped, (words[0] if words else ""), [*words[1:], *stripped[1:]], unread


def _build_brace(text: str) -> FlatTree:
    builder = _Builder()

    def visit(node: FlatNode, parents: list[str]) -> None:
        elements = [*parents, node.text]
        stripped, top, rest, unread = _split_path(elements)
        line_no = node.line_no or 0
        if unread and stripped[0].startswith(_UNREAD_HEADS):
            if not node.children:
                builder.other(" ".join(stripped), line_no)
        else:
            if node.text.startswith(INACTIVE) and not unread:
                builder.add(top, [DEACTIVATE, *rest], line_no)
            if not node.children:
                builder.add(top, rest, line_no, SCOPE_UNREAD if unread else None)
        for child in node.children:
            visit(child, elements)

    for node in build_brace_tree(text, PLATFORM).children:
        visit(node, [])
    return builder.finish()


def build_junos_tree(text: str) -> FlatTree:
    """Junos の config のテキストを 2 段の木にする。形(brace か set)は中身から見分ける。"""
    first_brace, first_set = _shapes(text)
    if first_brace is not None and first_set is None:
        return _build_brace(text)
    if first_brace is not None and first_set is not None:
        raise JunosStructureError("mixed", max(first_brace, first_set))
    return _build_set(text)
