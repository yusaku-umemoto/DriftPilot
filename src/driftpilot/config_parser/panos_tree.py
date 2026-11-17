"""PAN-OS の config の形(XML・set・brace)を見分けて、同じ 2 段の文の木に組む(ADR-0226 決定 1〜5。REQ-327)。
hier_config は使わない。字下げは見ない。

機種に依らない部品の組み合わせ。機種の登録・IR への写しはここではしない(木を組むだけ)。

形の見分け(空行・空白だけの行を除く):
- 最初の文字が `<` なら XML(panos_xml.xml_statements)。根を閉じた後に空白でない文字があれば PanosStructureError("mixed")
- そうでなければ、空行と `#` で始まる行を飛ばし、右端の空白を除いて見る(引用符の中で始まる行は見ない。引用符が行をまたぐときは、閉じる行までを 1 行として見る)。
  `{` で終わる行だけがあれば brace、`set ` で始まる行だけがあれば set。
  `<` で始まる行と set・brace の行が混ざる、set と brace が混ざる、は PanosStructureError("mixed")。行番号は後に出た形の最初の行
- どれでもなければ set として読む(全部が最上位の行になり、写しで未解釈に残る)

2 段の木(どの形でも同じ。区切りの表は使わない。作りは junos_tree._Builder と同じ):
- 1 段目 = 最上位の語(文の最初の語)。暗黙のノード(line_no は None。scope_id は SCOPE_TOP)。同じ語は 1 つ
- 2 段目 = 文(最上位の語を除いた、根からの残りの語を空白 1 つでつないだもの。引用符は外さない)。line_no は元の行
  (XML は文を終えた要素の開きのタグの行、set はその行、brace は葉の行)
- 残りの語の無い最上位の語(brace の `foo;`・set の `set foo`・XML の `<foo/>`)は、1 段目のノードが実在の行(line_no あり・子なし)
- 同じ最上位の語の下に同じ文が 2 回出たら DuplicateLine((最上位の語,), 行番号の並び) を記録する(止まらない)
- `set ` で始まらない set の行は、最上位の実在の行(SCOPE_OTHER。テキストは行全体)

set: 元の行を 1 字ずつ読む(`_read_set`)。`set ` の後ろを語に分ける(引用符 `"` の中は 1 語。`\\"` では閉じない)。文の外では、空行と `#` で始まる行を飛ばす。
- 引用符が行の終わりまでに閉じなければ、次の行から閉じの引用符のある行まで改行ごと続けて 1 つの値として読む(続きの行は空行・`#`・字下げもそのまま値に入る)。
  閉じの引用符の後ろの語は同じ文の続き。ファイルの終わりまで閉じなければ PanosStructureError("quote", 開いた行)
- 改行を含む語は、XML の複数行の値と同じ `<複数行の値 <行数> 行 <sha256 の頭 16 字>>` の 1 語にする(multiline_word。値の中身は文に入れない)
- `[` と `]` の語で囲まれた並びは、1 つずつの文に展開する(書かれた順)。語の頭にくっついた `[`(`[apt-get`)は切り出し、リストが開いているとき語の終わりにくっついた `]`(`apt-get]`)は外して閉じにする。
  引用符の中の `[`・`]` は区切りにしない。リストが開いていないときの語の中の `]` は語のまま
- 引用符の外でリストが開いたまま行が終われば、何も挟まずに次の行へ続ける(端末の折り返しを戻す。次の行の頭の空白は区切り)。
  続きの行が `set ` で始まれば `]` の書き忘れとして PanosStructureError("list", 開いた行)。ファイルの終わりまで閉じなければ同じ
- 文の行番号は `set ` の行
`[` は 1 行(続けた分を含む)に 1 組で、`]` は文の終わりに置く。
中身が空・前の語が無い・2 組目・`]` の後ろの語・入れ子・対の無い `[` `]` は PanosStructureError("list")(推測で展開しない)。
brace: brace_tree.build_brace_tree(text, "paloalto_panos", multiline=multiline_word) で入れ子の木を作り(複数行の引用符の値を受ける)、根から葉までの見出しと葉のテキストをつないで文にする
(子の無い見出しは、その見出しまでの経路が 1 つの文)。`inactive: `・`delete: `・`insert ` のような印は特別に扱わない(文のテキストのまま入る)。
BraceStructureError は包まずにそのまま上に出す。

`vsys1` の段(REQ-330): XML は panos_xml が、根の機器の `vsys` の下で `vsys1` だけのときに外す(テンプレートの中は外さない)。
set・brace は、最上位の語が `vsys` の文の 2 語目がどれも `vsys1` のとき、`vsys vsys1 ` の頭を外して残りの語で文を作り直す(`_drop_single_vsys1`)。
`vsys2` などほかの名前が 1 つでもあれば、どの文も変えない。最上位の語が `template` の文は変えない。これで、vsys が 1 つの config は 3 つの形で同じ文になる。

TREE_VERSION は、brace_tree の版と、この見分け・XML の読み方・set の読み方・木の作り方の規則をまとめた版で、表の指紋に入るのはこの 1 つだけ。
brace_tree の版か、この規則(panos_xml を含む)を変えたら、この版を上げる。
"""

from driftpilot.config_parser.brace_tree import TREE_VERSION as BRACE_TREE_VERSION
from driftpilot.config_parser.brace_tree import _words, build_brace_tree
from driftpilot.config_parser.flat_tree import FlatNode, FlatTree
from driftpilot.config_parser.junos_tree import _Builder
from driftpilot.config_parser.panos_xml import PLATFORM, PanosStructureError, value_word, xml_statements

__all__ = [
    "COMPOSED_OF",
    "PLATFORM",
    "SCOPE_OTHER",
    "SCOPE_TOP",
    "TREE_VERSION",
    "PanosStructureError",
    "build_panos_tree",
]

# 見分けの規則・木の作り方と、brace_tree の版をまとめた版
TREE_VERSION = "panos-tree-v3"
# まとめた元の版(テストが固定して、変えたときに気づけるようにする)
COMPOSED_OF = (BRACE_TREE_VERSION,)

# FlatNode の scope_id に入れる印。最上位の語のノード・最上位の実在の行(未解釈)
SCOPE_TOP = "PANOS-top"
SCOPE_OTHER = "PANOS-other"

_QUOTE = '"'
_SET = "set "
_OPEN = "["
_CLOSE = "]"
_VSYS = "vsys"
_VSYS1 = "vsys1"


def _builder() -> _Builder:
    return _Builder(PLATFORM, SCOPE_TOP, SCOPE_OTHER)


def _still_quoted(raw: str, quoted: bool) -> bool:
    """1 行を読み終えたときに引用符の中か。quoted は行の頭で引用符の中だったか。`\\"` では閉じない。"""
    index = 0
    size = len(raw)
    while index < size:
        ch = raw[index]
        if quoted:
            if ch == "\\":
                index += 1
            elif ch == _QUOTE:
                quoted = False
        elif ch == _QUOTE:
            quoted = True
        index += 1
    return quoted


def _outer_lines(text: str, track: bool = True) -> list[tuple[int, str]]:
    """引用符の外で始まる行の (行番号, 行)。引用符が行をまたぐときは、閉じる行までを改行でつないで 1 つにする。

    引用符の外で `#` で始まる行は、引用符を数えない。ファイルの終わりまで閉じない引用符があれば、数えずに行ごとに分ける
    (形の見分けだけの話で、止めるのは読み方の側)。
    """
    found: list[tuple[int, str]] = []
    parts: list[str] = []
    start = 0
    quoted = False
    for line_no, raw in enumerate(text.splitlines(), 1):
        if not track or (not quoted and raw.lstrip().startswith("#")):
            found.append((line_no, raw))
            continue
        if not quoted:
            start = line_no
        quoted = _still_quoted(raw, quoted)
        parts.append(raw)
        if not quoted:
            found.append((start, "\n".join(parts)))
            parts = []
    if quoted:
        return _outer_lines(text, track=False)
    return found


def _significant(text: str) -> list[tuple[int, str]]:
    """空行と `#` で始まる行を飛ばした (行番号, 両端の空白を除いた行)。引用符の中の行は、その行を始めた行にまとめる。"""
    found: list[tuple[int, str]] = []
    for line_no, raw in _outer_lines(text):
        line = raw.strip()
        if line and not line.startswith("#"):
            found.append((line_no, line))
    return found


def _shapes(lines: list[tuple[int, str]]) -> dict[str, int]:
    """形ごとの最初の行番号(xml・set・brace)。無い形は入れない。"""
    first: dict[str, int] = {}
    for line_no, line in lines:
        if line.startswith("<"):
            first.setdefault("xml", line_no)
        if line.endswith("{"):
            first.setdefault("brace", line_no)
        if line.startswith(_SET):
            first.setdefault("set", line_no)
    return first


def _enclosed(word: str) -> bool:
    """語の全体が 1 組の引用符で囲まれているか(最初の引用符が語の最後の字で閉じる)。`\\"` では閉じない。"""
    if len(word) < 2 or word[0] != _QUOTE:
        return False
    index = 1
    while index < len(word):
        if word[index] == "\\":
            index += 1
        elif word[index] == _QUOTE:
            return index == len(word) - 1
        index += 1
    return False


def multiline_word(word: str) -> str:
    """改行を含む語(set・brace の複数行の引用符の値)を、XML の複数行の値と同じ 1 語に直す(中身は出さない)。

    語全体が引用符で囲まれていれば中身を取り出し、`\\"` を `"` に戻す(XML の値は `"` を素のまま持つので、同じ値が同じ語になる)。
    前後の空白を除き、panos_xml.value_word に渡す。`a"…"b` のように引用符が語の途中にある形は、語全体を値にする。
    """
    value = word[1:-1].replace('\\"', _QUOTE) if _enclosed(word) else word
    return value_word(value.strip())


def _read_set(lines: list[str], start: int) -> tuple[list[str], int]:
    """`set ` の行(lines[start])から文 1 つ分の語を読む。(語の並び, 次に読む行の添字)を返す。

    引用符の外の空白で語に分ける。引用符は外さない。`\\"` では閉じない。
    - 引用符が行の終わりまでに閉じなければ、次の行から閉じる行まで改行ごと 1 語に続ける(続きの行はそのまま)。改行を含む語は 1 語に直す
    - 語の頭の `[` は、リストの開きとして切り出す。リストが開いているとき、語の終わりにくっついた `]` は外して閉じにする
    - 引用符の外でリストが開いたまま行が終われば、何も挟まずに次の行へ続ける(端末の折り返しを戻す)。次の行の頭の空白は区切りになる。
      続きの行が `set ` で始まれば `]` の書き忘れとして list のエラー
    - ファイルの終わりまで閉じなければ、引用符は quote、リストは list のエラー(開いた行の番号)
    """
    words: list[str] = []
    current: list[str] = []
    quoted = False
    quote_line = 0
    list_open = False
    list_line = 0
    index = start
    text = lines[start].lstrip()[len(_SET) :]

    def flush() -> None:
        nonlocal list_open
        if not current:
            return
        word = "".join(current)
        current.clear()
        closing = list_open and word.endswith(_CLOSE)
        if closing:
            list_open = False
            word = word[:-1]
        if word:
            words.append(multiline_word(word) if "\n" in word else word)
        if closing:
            words.append(_CLOSE)

    while True:
        line_no = index + 1
        pos = 0
        size = len(text)
        while pos < size:
            ch = text[pos]
            if quoted:
                current.append(ch)
                if ch == "\\" and pos + 1 < size:
                    current.append(text[pos + 1])
                    pos += 1
                elif ch == _QUOTE:
                    quoted = False
            elif ch.isspace():
                flush()
            elif ch == _OPEN and not current:
                words.append(_OPEN)
                list_open = True
                list_line = line_no
            else:
                current.append(ch)
                if ch == _QUOTE:
                    quoted = True
                    quote_line = line_no
            pos += 1
        if not quoted and list_open and current and current[-1] == _CLOSE:
            flush()  # 行の終わりの `]` は、リストの閉じ
        if quoted:
            current.append("\n")
        elif list_open:
            pass  # 語の途中で折り返された可能性があるので、語を閉じずに次の行へ続ける
        else:
            flush()
            return words, index + 1
        index += 1
        if index >= len(lines):
            raise PanosStructureError("quote" if quoted else "list", quote_line if quoted else list_line)
        text = lines[index]
        if not quoted and text.lstrip().startswith(_SET):
            raise PanosStructureError("list", list_line)


def _expand(words: list[str], line_no: int) -> list[list[str]]:
    """`[ a b ]` を 1 つずつの文に展開する(書かれた順)。`[`・`]` が無ければ語の並びそのまま 1 つ。"""
    opens = [i for i, word in enumerate(words) if word == _OPEN]
    closes = [i for i, word in enumerate(words) if word == _CLOSE]
    if not opens and not closes:
        return [words]
    if len(opens) != 1 or len(closes) != 1:
        raise PanosStructureError("list", line_no)
    start, end = opens[0], closes[0]
    if start == 0 or end < start + 2 or end != len(words) - 1:
        raise PanosStructureError("list", line_no)
    head = words[:start]
    return [[*head, element] for element in words[start + 1 : end]]


# 木に足す操作 1 つ。("add", 最上位の語, 残りの語, 行番号) か ("other", 行のテキスト, 行番号)
_Op = tuple[str, str, list[str], int]


def _drop_single_vsys1(ops: list[_Op]) -> list[_Op]:
    """vsys が 1 つ(2 語目が `vsys1` だけ)のとき、最上位の語が `vsys` の文から `vsys1` の語を外す(REQ-330。set・brace)。

    最上位の語が `vsys` の文の 2 語目(つないだ文の頭の語)を見る。`vsys1` 以外が 1 つでもあれば何も変えない。
    2 語目の無い文(`set vsys`)は数えず変えない。`vsys1` の後ろに語が続く文は、`vsys vsys1 ` の頭を外し、次の語を最上位の語にして作り直す。
    最上位の語が `template` の文は変えない。残りの文字列はそのまま渡す(引用符の中・複数行の値の語は崩さない)。
    """
    heads: list[str] = []
    for kind, top, rest, _ in ops:
        if kind == "add" and top == _VSYS and rest:
            words = _words(rest[0])
            heads.append(words[0] if words else "")
    if not heads or any(head != _VSYS1 for head in heads):
        return ops
    out: list[_Op] = []
    for kind, top, rest, line_no in ops:
        if kind == "add" and top == _VSYS and rest:
            words = _words(rest[0])
            items = ([" ".join(words[1:])] if len(words) > 1 else []) + rest[1:]
            if items:
                first = _words(items[0])
                if first:
                    items = ([" ".join(first[1:])] if len(first) > 1 else []) + items[1:]
                    out.append(("add", first[0], items, line_no))
                    continue
        out.append((kind, top, rest, line_no))
    return out


def _replay(ops: list[_Op]) -> FlatTree:
    """貯めた操作を順に _Builder へ流す。"""
    builder = _builder()
    for kind, top, rest, line_no in ops:
        if kind == "add":
            builder.add(top, rest, line_no)
        else:
            builder.other(top, line_no)
    return builder.finish()


def _build_set(text: str) -> FlatTree:
    """set の形として読む。元の行のまま 1 字ずつ読む(複数行の値の中の空行・`#` の行・字下げは値に入る)。"""
    ops: list[_Op] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line_no = index + 1
        line = lines[index].strip()
        if not line or line.startswith("#"):
            index += 1
            continue
        if not line.startswith(_SET):
            ops.append(("other", line, [], line_no))
            index += 1
            continue
        words, index = _read_set(lines, index)
        for statement in _expand(words, line_no):
            ops.append(("add", statement[0], statement[1:], line_no))
    return _replay(_drop_single_vsys1(ops))


def _build_xml(text: str) -> FlatTree:
    builder = _builder()
    for words, line_no in xml_statements(text):
        builder.add(words[0], words[1:], line_no)
    return builder.finish()


def _build_brace(text: str) -> FlatTree:
    ops: list[_Op] = []

    def visit(node: FlatNode, parents: list[str]) -> None:
        elements = [*parents, node.text]
        if not node.children:
            words = _words(elements[0])
            ops.append(("add", words[0] if words else "", [*words[1:], *elements[1:]], node.line_no or 0))
        for child in node.children:
            visit(child, elements)

    for node in build_brace_tree(text, PLATFORM, multiline=multiline_word).children:
        visit(node, [])
    return _replay(_drop_single_vsys1(ops))


def build_panos_tree(text: str) -> FlatTree:
    """PAN-OS の config のテキストを 2 段の木にする。形(XML・set・brace)は中身から見分ける。"""
    stripped = text.lstrip()
    if stripped.startswith("<"):
        return _build_xml(text)
    lines = _significant(text)
    first = _shapes(lines)
    if len(first) > 1:
        # 後に出た形の最初の行(最初の形の次に出た形の最初の行)
        raise PanosStructureError("mixed", sorted(first.values())[1])
    if "xml" in first:
        # `<` で始まる行だけがあり、ほかの形は無い。最初の文字が `<` でなければ XML とは読まず、全部が最上位の行になる
        return _build_set(text)
    if "brace" in first:
        return _build_brace(text)
    return _build_set(text)
