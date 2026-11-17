"""`{ }` の対で組む config(Junos の `show configuration` など)を、木に組む部品(ADR-0224 決定 1〜5)。hier_config は使わない。

機種に依らない部品。機種の登録・IR への写しはここではしない(木を組むだけ)。字下げは一切見ない。

字句(行番号は元の行の 1 始まり。飛ばした行も数える):
- 引用符は `"` だけ。`\\"` では閉じない。引用符の中の `;`・`{`・`}`・`#`・`[`・`]`・`/*` は区切りにしない。行の終わりまでに閉じなければ止める
- 最初の空白でない字が `#` の行(`##` も `#` 1 つも)は木に入れない。引用符の外で、行頭か空白の後ろにある
  `#` から行末までは落とす(`ge-0/0/0#1` のような語の中の `#` は落とさない)
- 注釈 `/* … */`(引用符の外。複数行も可)は木に入れない。閉じなければ止める
- 掃除して空になった行は飛ばす。`replace:` だけの行は木に入れない(次の行の頭に付く印として外す)
- 行頭の `replace: ` は外す。`inactive: `・`delete: `・`insert ` は外さない(読むかどうかは写しの側が決める)

文の区切り(1 行 1 文。掃除した後の行で見る):
- `{` で終わる行は見出し(`{` の前の語全体がテキスト)、`;` で終わる行は葉、`}` だけの行は見出しを閉じる
- `;`・`{`・`}` のどれでも終わらない行は、次の空でない行が `{` だけならその見出し(`{` を別の行に書く形)、
  そうでなければ `;` を書き忘れた葉として読む。`{` だけの行が見出しに使われずに残れば止める
- 行の途中に引用符の外の `{`・`}`・`;` がある行(1 行に 2 つ以上の文)は止める
- `[ a b ]` を含む葉は、前の語と各要素の組にした葉に展開する(要素の昇順。行番号は元の行)

組んだ木は B型・D型と同じ FlatTree / FlatNode。見出しも葉も scope_id は使わない。
形が崩れていれば BraceStructureError で止める。推測で閉じない(切り詰められたログを黙って読まない)。

複数行の引用符の値(build_brace_tree の multiline に関数を渡したときだけ。REQ-328。Junos は渡さない):
- 引用符が行の終わりまでに閉じなければ、次の行から閉じの引用符のある行まで続けて 1 つの値として読む。続きの行は字下げ・空行・`#` の行も含めてそのまま値に入る
  (`#` の行の読み飛ばし・コメントの扱いは当てない)。つないだものは 1 つの論理行で、行番号はその文が始まった行。閉じの引用符の後ろの `;` で葉が終わる
- 改行を含む語は、渡された関数で 1 語に直す(中身は文に入れない)。ファイルの終わりまで閉じなければ、引用符を開いた行の BraceStructureError("quote")
- 渡さない(既定)ときは、行の終わりまでに閉じない引用符は、その行の BraceStructureError("quote")
"""

from collections.abc import Callable

from driftpilot import i18n
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree

# 組み立ての規則の版。規則を変えたら上げる
TREE_VERSION = "brace-tree-v2"

_QUOTE = '"'
_REPLACE = "replace: "
_REPLACE_MARK = "replace:"
_DELIMITERS = frozenset("{};")


class BraceStructureError(ValueError):
    """`{ }` の対応・引用符・注釈・`[ ]`・1 行 1 文の形が崩れている。

    reason は分岐に使える理由(quote・comment・list・multi・unbalanced)、line_no は問題の行
    (閉じていないものは開いた行)。文面に差し込むのは行番号だけで、行の中身・値は入れない。
    文面は理由の鍵から作る(言語で日本語・英語を選ぶ)。
    """

    def __init__(self, reason: str, line_no: int) -> None:
        self.reason = reason
        self.line_no = line_no
        super().__init__(_STRUCTURE["head"].format(line=line_no) + _STRUCTURE[reason].format())


# 形の誤りの文。理由の鍵 → 文の形。head は頭の文。`{{` `}}` は format で `{` `}` になる
_STRUCTURE_JA = {
    "head": "config の {line} 行目: ",
    "quote": "引用符が行の終わりまでに閉じていません",
    "comment": "注釈(/* … */)が閉じていません(ファイルが途中で切れている可能性があります)",
    "list": "[ と ] が同じ行の中で対になっていません",
    "multi": "1 行が 1 つの文({{ で終わる見出し・; で終わる文・}} だけの行)になっていません",
    "unbalanced": "{{ と }} の対応が取れません(}} が多いか、閉じていない見出しがあります。ファイルが途中で切れている可能性があります)",
}
_STRUCTURE_EN = {
    "head": "config, line {line}: ",
    "quote": "a quotation mark is not closed by the end of the line",
    "comment": "a comment (/* ... */) is not closed (the file may have been cut off)",
    "list": "[ and ] are not paired within the same line",
    "multi": "a line is not a single statement (a heading ending with {{, a statement ending with ;, or a line holding only }})",
    "unbalanced": "{{ and }} do not match (there is an extra }}, or a heading is not closed; the file may have been cut off)",
}
_STRUCTURE = i18n.choose(_STRUCTURE_JA, _STRUCTURE_EN)


def _clean_lines(text: str, multiline: Callable[[str], str] | None = None) -> list[tuple[int, str]]:
    """注釈・`#` のコメントを落とし、空でなくなった行を (行番号, 掃除した行) で返す。引用符は外さない。

    multiline が None でなければ、閉じない引用符は次の行へ続け、つないだものを 1 行(行番号は始まった行)にする。
    """
    out_lines: list[tuple[int, str]] = []
    in_comment = False
    comment_line = 0
    carried: list[str] | None = None  # 引用符の中で終わった行までの出力(次の行へ続ける)
    carried_line = 0
    for line_no, raw in enumerate(text.splitlines(), 1):
        out: list[str]
        if carried is not None:
            out = carried
            quoted = True
            start_line = carried_line
            carried = None
        else:
            if not in_comment and raw.lstrip().startswith("#"):
                continue
            out = []
            quoted = False
            start_line = line_no
        index = 0
        size = len(raw)
        while index < size:
            ch = raw[index]
            if in_comment:
                if raw.startswith("*/", index):
                    in_comment = False
                    out.append(" ")
                    index += 2
                else:
                    index += 1
            elif quoted:
                out.append(ch)
                if ch == "\\" and index + 1 < size:
                    out.append(raw[index + 1])
                    index += 2
                    continue
                if ch == _QUOTE:
                    quoted = False
                index += 1
            elif ch == _QUOTE:
                quoted = True
                out.append(ch)
                index += 1
            elif raw.startswith("/*", index):
                in_comment = True
                comment_line = line_no
                out.append(" ")
                index += 2
            elif ch == "#" and (not out or out[-1].isspace()):
                break
            else:
                out.append(ch)
                index += 1
        if quoted:
            if multiline is None:
                raise BraceStructureError("quote", line_no)
            out.append("\n")
            carried = out
            carried_line = start_line
            continue
        line = "".join(out).strip()
        if line and line != _REPLACE_MARK:
            out_lines.append((start_line, line))
    if carried is not None:
        raise BraceStructureError("quote", carried_line)
    if in_comment:
        raise BraceStructureError("comment", comment_line)
    return out_lines


def _outside(line: str) -> list[tuple[int, str]]:
    """引用符の外にある区切りの字 `{ } ; [ ]` の (位置, 字)。引用符は掃除の段階で閉じているのが前提。"""
    found: list[tuple[int, str]] = []
    quoted = False
    index = 0
    while index < len(line):
        ch = line[index]
        if quoted:
            if ch == "\\":
                index += 1
            elif ch == _QUOTE:
                quoted = False
        elif ch == _QUOTE:
            quoted = True
        elif ch in "{};[]":
            found.append((index, ch))
        index += 1
    return found


def _words(text: str) -> list[str]:
    """引用符の外の空白で区切った語。引用符の中の空白は変えない(値の違いを隠さないため)。"""
    words: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        ch = text[index]
        if quoted:
            current.append(ch)
            if ch == "\\" and index + 1 < len(text):
                current.append(text[index + 1])
                index += 1
            elif ch == _QUOTE:
                quoted = False
        elif ch.isspace():
            if current:
                words.append("".join(current))
                current = []
        else:
            current.append(ch)
            if ch == _QUOTE:
                quoted = True
        index += 1
    if current:
        words.append("".join(current))
    return words


def _one_line(words: list[str], multiline: Callable[[str], str] | None) -> list[str]:
    """改行を含む語を multiline で 1 語に直す。multiline が None なら、そのまま。"""
    if multiline is None:
        return words
    return [multiline(word) if "\n" in word else word for word in words]


def _text(raw: str, multiline: Callable[[str], str] | None = None) -> str:
    """語の間の空白を 1 つにし、頭の `replace: ` を外す。改行を含む語は multiline で 1 語に直す。"""
    text = " ".join(_one_line(_words(raw), multiline))
    if text.startswith(_REPLACE):
        text = text[len(_REPLACE) :].strip()
    return text


def _leaf_texts(
    before: str,
    delims: list[tuple[int, str]],
    line_no: int,
    multiline: Callable[[str], str] | None = None,
) -> list[str]:
    """葉の `;` の前の部分から、葉のテキストを作る。`[ a b ]` は要素ごとに展開し、昇順にする。"""
    brackets = [(pos, ch) for pos, ch in delims if ch in "[]"]
    if not brackets:
        text = _text(before, multiline)
        if not text:
            raise BraceStructureError("multi", line_no)
        return [text]
    opens = [pos for pos, ch in brackets if ch == "["]
    closes = [pos for pos, ch in brackets if ch == "]"]
    if len(opens) != 1 or len(closes) != 1 or opens[0] > closes[0] or before[closes[0] + 1 :].strip():
        raise BraceStructureError("list", line_no)
    head = _text(before[: opens[0]], multiline)
    elements = _one_line(_words(before[opens[0] + 1 : closes[0]]), multiline)
    if not head or not elements:
        raise BraceStructureError("list", line_no)
    return [f"{head} {element}" for element in sorted(elements)]


def build_brace_tree(text: str, platform: str, *, multiline: Callable[[str], str] | None = None) -> FlatTree:
    """`{ }` の形の config(1区間)を、`{ }` の対で木にする。空行は飛ばす。字下げは見ない。

    multiline に関数を渡すと、複数行の引用符の値を受け、改行を含む語をその関数で 1 語に直す(PAN-OS 用。既定の None は Junos の読み方)。
    """
    tree = FlatTree(platform)
    stack: list[tuple[FlatNode, int]] = []
    headings: dict[tuple[int, str], FlatNode] = {}
    leaves: dict[tuple[int, str], list[int]] = {}
    leaf_scope: dict[tuple[int, str], tuple[str, ...]] = {}
    lines = _clean_lines(text, multiline)
    index = 0
    while index < len(lines):
        line_no, line = lines[index]
        index += 1
        delims = _outside(line)
        if sum(1 for _, ch in delims if ch == "[") != sum(1 for _, ch in delims if ch == "]"):
            raise BraceStructureError("list", line_no)
        parent = stack[-1][0] if stack else None
        container = parent if parent is not None else tree
        key_base = id(container)
        if line == "}":
            if not stack:
                raise BraceStructureError("unbalanced", line_no)
            stack.pop()
            continue
        if line == "{":
            # 直前の文の行に使われなかった `{` だけの行(直前が ; { } で終わる行・最初の行)
            raise BraceStructureError("multi", line_no)
        heading_only = False
        if line.endswith("{") or line.endswith(";"):
            before = line[:-1]
            inner = [(pos, ch) for pos, ch in delims if pos < len(line) - 1]
        else:
            before = line
            inner = delims
            heading_only = index < len(lines) and lines[index][1] == "{"
        if any(ch in _DELIMITERS for _, ch in inner):
            raise BraceStructureError("multi", line_no)
        if line.endswith("{") or heading_only:
            if any(ch in "[]" for _, ch in inner):
                raise BraceStructureError("list", line_no)
            heading = _text(before, multiline)
            if not heading:
                raise BraceStructureError("multi", line_no)
            if heading_only:
                index += 1
            node = headings.get((key_base, heading))
            if node is None:
                node = container._add(FlatNode(heading, parent, line_no))
                headings[(key_base, heading)] = node
            stack.append((node, line_no))
            continue
        for leaf in _leaf_texts(before, inner, line_no, multiline):
            container._add(FlatNode(leaf, parent, line_no))
            leaves.setdefault((key_base, leaf), []).append(line_no)
            leaf_scope[(key_base, leaf)] = parent.path() if parent is not None else ()
    if stack:
        raise BraceStructureError("unbalanced", stack[-1][1])
    tree.add_duplicates(
        DuplicateLine(leaf_scope[key], tuple(lines)) for key, lines in leaves.items() if len(lines) > 1
    )
    return tree
