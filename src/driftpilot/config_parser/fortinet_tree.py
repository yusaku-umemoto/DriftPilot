"""FortiGate の config(D型)を、終端記号の対で木に組む(ADR-0025 決定1〜3・決定7)。hier_config は使わない。

字下げは一切見ない。埋め込まれた自由文の字下げは不揃いで、字下げ0の位置にも現れるため、
字下げで組むと HTML の1行が設定の子になる(docs/notes/corpus-findings.md §4)。

- `config <名前…>` … `end` がスコープ、`edit <キー>` … `next` がスコープ内の要素。行の第1語だけで判定する
- `edit` のノードのテキストは `edit <キー>`。キーは引用符を外した原文のまま(数字に変換しない。決定7)
- `set` / `unset` とその他の行は、今のスコープの子。最上位の未知の行は最上位のノード(未解釈)
- `end` / `next` はノードにしない(行として数えない)
- 対応が取れなければ FortinetStructureError で止める。推測で閉じない(切り詰められたログを黙って読まない)

引用符(`"` と `'`)は行をまたいで追う。`\\` の次の1文字は飛ばす(`\\"` では閉じない)。`''` は空の文字列。
`set` の値の引用符がその行で閉じなければ、閉じ引用符の行までを1つのノードにする(決定3)。テキストは
`set <attr> <embedded> lines <N> sha256 <64桁>` で、本文は木に入らない。ブロック名や中身の語では判定しない。

最上位の行の前処理(ADR-0129 決定1、REQ-174。保存のたびに変わる値を木に入れない):
- `#conf_file_ver=<数字>` の行は木に入れない(空行と同じく飛ばす。行番号の数え方は変えない)
- `#config-version=` の行の最後の `:user=<…>` を落とす
config の中の `#` の行は変えない。

組み立てた木は B型と同じ FlatTree / FlatNode。下流(extractor・被覆率・安全網)はそのまま動く。
"""

import hashlib
from dataclasses import dataclass

from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree

PLATFORM = "fortinet"
# 組み立ての規則の版。規則を変えたら上げる(パターン表の指紋に入る)
TREE_VERSION = "fortinet-tree-v2"
# 埋め込みの値のハッシュの取り方の版(docs/notes/req-22-fortigate.md)
HASH_INPUT_VERSION = 1
EMBEDDED = "<embedded>"

CONFIG = "config"
EDIT = "edit"
# マルチ VDOM の最上位の見出し(ADR-0135 決定1。重複の判定だけが特別扱いする)
VDOM = "config vdom"
_QUOTES = frozenset("\"'")
_CONF_FILE_VER = "#conf_file_ver="
_CONFIG_VERSION = "#config-version="
_USER = "user="


class FortinetStructureError(ValueError):
    """config / end、edit / next の対応が取れない、または引用符が閉じていない。

    reason は分岐に使える理由、line_no は問題の行(閉じていないものは開いた行)。文面に値は含めない。
    """

    def __init__(self, reason: str, line_no: int, message: str) -> None:
        self.reason = reason
        self.line_no = line_no
        super().__init__(f"FortiGate の config の {line_no} 行目: {message}")


@dataclass(frozen=True)
class EmbeddedValue:
    """行をまたぐ値1つ。sha256 は開き引用符から閉じ引用符まで(両端を含む)を LF でつないだ UTF-8 の SHA-256。"""

    sha256: str
    lines: int


class _Quotes:
    """引用符の状態を、行をまたいで追う。spans は閉じた文字列の (開き, 閉じ) の位置(つないだ文字列の中の位置)。"""

    def __init__(self) -> None:
        self.quote: str | None = None
        self.escape = False
        self.opened_at = 0
        self.pos = 0
        self.spans: list[tuple[int, int]] = []

    def feed(self, chunk: str) -> None:
        if not self.escape and self.quote is None and "\\" not in chunk and not _QUOTES & set(chunk):
            self.pos += len(chunk)
            return
        for ch in chunk:
            if self.escape:
                self.escape = False
            elif ch == "\\":
                self.escape = True
            elif self.quote is None:
                if ch in _QUOTES:
                    self.quote, self.opened_at = ch, self.pos
            elif ch == self.quote:
                self.spans.append((self.opened_at, self.pos))
                self.quote = None
            self.pos += 1


def unquote(word: str) -> str:
    """両端の対になった引用符だけを外す。中は原文のまま(`\\X` も解かない。違う原文のキーが同じ名前にならないように)。

    引用符で囲まれていない語はそのまま。
    """
    if len(word) >= 2 and word[0] in _QUOTES and word[-1] == word[0]:
        return word[1:-1]
    return word


def embedded_lines(text: str) -> frozenset[int]:
    """行をまたぐ引用符の値が占める行の番号(1始まり。開き引用符の行から閉じ引用符の行まで)。

    機種の正規化ルールはこの行に当てない(本文はハッシュで比べるため。ADR-0025 決定3)。build_fortinet_tree と同じ字句解析で、
    止めずに読む: 引用符が閉じないままファイルが終われば、開いた行から末尾までを返す。
    """
    lines = text.splitlines()
    found: set[int] = set()
    index = 0
    while index < len(lines):
        start = index
        quotes = _Quotes()
        quotes.feed(lines[index])
        while quotes.quote is not None and index + 1 < len(lines):
            index += 1
            quotes.feed("\n" + lines[index])
        if index > start or quotes.quote is not None:
            found.update(range(start + 1, index + 2))
        index += 1
    return frozenset(found)


def embedded_value(value: str) -> EmbeddedValue:
    """埋め込みの値(両端の引用符を含む)のハッシュと行数。改行を LF に揃える以外は正規化しない(hash_input_version=1)。"""
    lines = value.splitlines()
    joined = "\n".join(lines)
    return EmbeddedValue(hashlib.sha256(joined.encode("utf-8")).hexdigest(), len(lines))


def _embedded_text(logical: str, quotes: _Quotes) -> str:
    """行をまたぐ値を持つ行のテキスト。値は最初に改行を含む文字列の開きから、最後に改行を含む文字列の閉じまで。"""
    multi = [(o, c) for o, c in quotes.spans if "\n" in logical[o:c]]
    start, end = multi[0][0], multi[-1][1]
    value = embedded_value(logical[start : end + 1])
    head = logical[:start].strip()
    tail = logical[end + 1 :].strip()
    text = f"{head} {EMBEDDED} lines {value.lines} sha256 {value.sha256}"
    return f"{text} {tail}" if tail else text


def _preprocess_top(line: str) -> str | None:
    """最上位の行の前処理(ADR-0129 決定1)。None は木に入れない行。ほかの行はそのまま返す。"""
    if line.startswith(_CONF_FILE_VER):
        number = line[len(_CONF_FILE_VER) :]
        if number.isascii() and number.isdigit():
            return None
    elif line.startswith(_CONFIG_VERSION):
        head, sep, last = line.rpartition(":")
        if sep and last.startswith(_USER):
            return head
    return line


def build_fortinet_tree(text: str) -> FlatTree:
    """FortiGate の config(1区間)を木にする。空行は飛ばす。行の前後の空白は落とす。"""
    tree = FlatTree(PLATFORM)
    stack: list[FlatNode] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line_no = index + 1
        quotes = _Quotes()
        quotes.feed(lines[index])
        logical = lines[index]
        while quotes.quote is not None:
            index += 1
            if index >= len(lines):
                raise FortinetStructureError(
                    "unclosed_quote", line_no, "引用符が閉じていません(ファイルが途中で切れている可能性があります)"
                )
            quotes.feed("\n" + lines[index])
            logical += "\n" + lines[index]
        index += 1
        line = logical.strip()
        if not line:
            continue
        if not stack and (line := _preprocess_top(line)) is None:
            continue
        parent = stack[-1] if stack else None
        container = parent if parent is not None else tree
        word = line.split(None, 1)[0]
        if "\n" in logical:
            if word != "set":
                raise FortinetStructureError(
                    "multiline_outside_set", line_no, "set 以外の行で、引用符が行をまたいでいます"
                )
            container._add(FlatNode(_embedded_text(logical, quotes), parent, line_no))
        elif word == CONFIG:
            stack.append(container._add(FlatNode(line, parent, line_no, scope_id=CONFIG)))
        elif word == EDIT:
            if parent is None or parent.scope_id != CONFIG:
                raise FortinetStructureError("edit_outside_config", line_no, "edit が config の直下にありません")
            key = unquote(line.split(None, 1)[1].strip()) if " " in line else ""
            stack.append(container._add(FlatNode(f"{EDIT} {key}", parent, line_no, scope_id=EDIT)))
        elif line == "next":
            if parent is None:
                raise FortinetStructureError("unmatched_next", line_no, "対応する edit の無い next があります")
            if parent.scope_id != EDIT:
                raise FortinetStructureError(
                    "next_in_config", line_no, f"{parent.line_no} 行目の config の中に、edit の無い next があります"
                )
            stack.pop()
        elif line == "end":
            if parent is None:
                raise FortinetStructureError("unmatched_end", line_no, "対応する config の無い end があります")
            if parent.scope_id != CONFIG:
                raise FortinetStructureError(
                    "end_in_edit", line_no, f"{parent.line_no} 行目の edit を閉じる next がありません"
                )
            stack.pop()
        else:
            container._add(FlatNode(line, parent, line_no))
    if stack:
        opened = stack[-1]
        closer = "end" if opened.scope_id == CONFIG else "next"
        raise FortinetStructureError(
            "unclosed_block",
            opened.line_no or 0,
            f"この {opened.scope_id} を閉じる {closer} がありません(ファイルが途中で切れている可能性があります)",
        )
    tree.add_duplicates(find_duplicates(tree))
    return tree


def line_key(text: str) -> tuple[str, ...]:
    """重複を比べる「属性」。set / unset は属性名(同じ属性の set と unset も重なる)、ほかの行は行全体。"""
    words = text.split()
    if len(words) >= 2 and words[0] in ("set", "unset"):
        return ("attr", words[1])
    return ("line", *words)


def find_duplicates(tree: FlatTree) -> list[DuplicateLine]:
    """同じスコープ(最上位を含む)の直下で、同じ属性・同じ行・同じ見出し(config / edit のキー)が2回以上あるもの。

    マルチ VDOM(ADR-0135 決定1): 最上位の `config vdom` が2つ以上あるときは、その見出しの重複は数えない。
    代わりに、すべての `config vdom` の直下の edit をキーごとに集め、子を持つ edit が同じキーで2つ以上あれば、
    スコープ `config vdom` で警告する(宣言だけの空の edit は数えない)。`config vdom` が1つなら今までどおり。
    """
    found: list[DuplicateLine] = []

    def visit(scope: tuple[str, ...], nodes: list[FlatNode]) -> None:
        groups: dict[tuple[str, ...], list[int]] = {}
        for node in nodes:
            key = ("scope", node.text) if node.scope_id is not None else line_key(node.text)
            groups.setdefault(key, []).append(node.line_no or 0)
            if node.scope_id is not None:
                visit(node.path(), node.children)
        found.extend(DuplicateLine(scope, tuple(lines)) for lines in groups.values() if len(lines) > 1)

    visit((), tree.children)
    vdoms = [node for node in tree.children if node.scope_id == CONFIG and node.text == VDOM]
    if len(vdoms) < 2:
        return found
    vdom_lines = {node.line_no or 0 for node in vdoms}
    found = [d for d in found if d.scope or not set(d.lines) <= vdom_lines]
    edits: dict[str, list[int]] = {}
    for vdom in vdoms:
        for child in vdom.children:
            if child.scope_id == EDIT and child.children:
                edits.setdefault(child.text, []).append(child.line_no or 0)
    found.extend(DuplicateLine((VDOM,), tuple(lines)) for lines in edits.values() if len(lines) > 1)
    return found
