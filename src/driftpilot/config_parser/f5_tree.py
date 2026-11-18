"""F5 BIG-IP の config(`tmsh list`・`tmsh show running-config`・`bigip.conf`)を、F5 用の波かっこの木に組む部品(ADR-0235 決定 2。REQ-349)。
hier_config は使わない。機種の登録・IR への写しはここではしない(木を組むだけ)。

F5 の config は `<モジュール> <種類> <名前> {` … `}` の入れ子だが、1 行の中に波かっこを書く(`/Common/http { }`・`vlans { /Common/a /Common/b }`)ので、
Junos・PAN-OS の波かっこの木(brace_tree。1 行 1 文)では読めない。iRule・iApp のテンプレートの本文は Tcl の文で、波かっこの決まりでは読めない。
字下げは、Tcl の塊の終わりの確かめにだけ使う(ほかでは見ない)。

字句(行番号は元の行の 1 始まり。飛ばした行も数える):
- 引用符は `"`。`\\"` では閉じない。引用符の中の `{`・`}`・`#` は区切りにしない。行の終わりまでに閉じない引用符は、閉じの引用符のある行まで
  続けて 1 つの値として読む(PAN-OS と同じ。改行を含む語は panos_tree.multiline_word で 1 語に直す。行番号は始まった行)。
  ファイルの終わりまで閉じなければ、開いた行の F5StructureError("quote")
- Tcl の塊の外で、最初の空白でない字が `#` の行(`#TMSH-VERSION: …`)は木に入れない。空の行は飛ばす。行の途中の `#` は普通の字

文(1 行に複数の文があってよい。左から順に読む):
- **最上位の 1 行のまとまり**: 最上位(今開いている見出しが無い)の行で、`{`・`}` が行の中で全部対になる(数が途中で 0 より下がらず、最後に 0 に戻る)行
  (`ltm monitor http /Common/m1 { … }`・`auth user … { … partition-access { … } … }`・`ltm rule /Common/r1 { when … { … } }`・中が空の `ltm pool /Common/p1 { }`)は、
  見出しと葉に分けず、前後の空白を落とした行全体(語の間の空白は 1 つ)を最上位の葉 1 つにする(中身を構造化しない。値が変わると行の削除と追加になる)。
  最上位の葉なので重複の警告は出ない。依頼書 §2 への例外(ADR-0235 の追記の提案)
- **入れ子の段の 1 行の形**: 見出しの中の、`{`・`}` が行の中で全部対になる行は、行全体(`" ".join(語)`)を送る側の判定(Masker().judge_alone。祖先は今開いている見出しの
  テキスト)にかけ、「そのまま送る」(plain)でなければ、行全体を葉 1 つにする(`credentials { password … }`・`cert-key-chain { default { description x passphrase … } }`・
  `recv { a } description x password …` など、中の語 1 つずつ・`}` の前の語の並びでは秘密が漏れる行)。plain のときだけ次の決まりで分ける
  (いつも行全体にはしない。`vlans { /Common/a /Common/b }` が葉 2 つ、`members { … { address … } }` が見出し → 見出し → 葉になる)。判定器は 1 回の組み立てで 1 つ。
  前の行から続く `}` や次の行へ続く `{` を含む行(対にならない行)は、この判定を当てず次の決まりで分ける。
  行全体の判定が pass になる行(`description x password … recv { a }` のように description で始まる行)は、ここでは防げない(複数行の形で単独の description の行を送るのと同じ扱い)
- `{` の前の語の並びは見出しのテキスト。前に語が無い `{` は、名前の無い見出し `{}`(`unicast-address { { … } }` の中の `{ … }` など)
- 行が `<語>… { <語>… }` だけでできている(`{` の前に語があり、対が 1 組だけで、`}` の後ろに何も無い)ときは、見出し 1 つと、中の語 1 つずつの葉
  (中が空なら子の無い見出し 1 つ)。入れ子の段の行で、上の判定が plain のときだけ
- ほかの `{`・`}` を含む行は、左から順に見出しを開き(`{`)、`}` の前の語の並びを 1 つの葉にして、今開いている見出しを閉じる。
  行末の語の並びも 1 つの葉。行の終わりで開いたままの見出しは次の行に続く。`{`・`}` を含まない行は葉(語の間の空白は 1 つにする)
- 同じ親の下の同じテキストの見出しは 1 つのノードにまとめる(brace_tree と同じ。名前の無い見出しも)。葉は行ごとに作る
- 閉じの `}` が足りない・多いときは F5StructureError("unbalanced")(推測で閉じない)

行のまま持つ塊(Tcl の文):
- 最上位の見出しが `ltm rule `・`gtm rule `・`pem irule `・`sys icall script ` で始まるもの、と、どの段でも見出しのテキストが
  `implementation`・`presentation`・`html-help`・`definition`・`macro` のものは、中を文として読まない
- 塊が始まるのは、その見出しを開く `{` が行の最後の字のときだけ。同じ行の中で閉じる形は普通の文として読む。行の途中で開いて、閉じないまま
  行が終わるときは F5StructureError("tcl_inline")
- 次の行から、`\\` の付いていない `{` を +1、`}` を −1 と数える(最初の数は 1。引用符・`#` は区別しない。Tcl の決まり)。数が 0 に戻った行で塊を閉じる。
  その行が、開いた行と同じ字下げ(行頭の空白の字の数)の `}` だけの行でない、または数が 0 より下がったときは F5StructureError("tcl_close")。
  ファイルの終わりまで 0 に戻らなければ、開いた行の F5StructureError("tcl_open")
- 中の行は、空の行を除き、前後の空白を落とした行を 1 行ずつ葉にする(`#` の行も葉にする。行番号は元の行)

imish の部分:
- `!!! imish configuration starts here` の行(前後の空白は可)から後ろの行(空の行を除く)は、最上位の葉(scope_id が SCOPE_IMISH。未解釈)にする。
  Cisco 系の読み方は当てない。`!!! imish configuration ends here` で始まる行があれば、その行までを imish とし、後ろは F5 の木に戻る

重複の警告(DuplicateLine): F5 の見出し(SCOPE_HEADING)の直下の、同じテキストの葉が 2 つ以上あるもの。Tcl の塊の中・最上位の行・
名前の無い見出し `{}` の下(入れ子の下も)では出さない。値は持たない(見出しの道すじと行番号だけ)。

FlatNode の scope_id: SCOPE_HEADING(F5 の見出し)・SCOPE_TCL(Tcl の塊の見出し)・SCOPE_ANON(名前の無い見出し `{}`)・SCOPE_IMISH(imish の最上位の葉)。
葉は None(最上位の葉もそうで、parent が None)。子の無い見出しと葉は、この印で見分ける。

TREE_VERSION は、この木の組み立ての規則の版で、表の指紋に入る。規則(字句・Tcl の塊の見出しの一覧・imish・警告)を変えたら上げる。
brace_tree の `_words`(引用符の外の空白で語に分ける)と panos_tree.multiline_word を使うので、その決まりを変えたときも上げる。
"""

from typing import Any

from driftpilot import i18n
from driftpilot.config_parser.brace_tree import _words
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree
from driftpilot.config_parser.panos_tree import multiline_word

__all__ = [
    "PLATFORM",
    "SCOPE_ANON",
    "SCOPE_HEADING",
    "SCOPE_IMISH",
    "SCOPE_TCL",
    "TREE_VERSION",
    "ANON_TEXT",
    "F5StructureError",
    "build_f5_tree",
]

PLATFORM = "f5_bigip"
TREE_VERSION = "f5-tree-v1"

# FlatNode の scope_id に入れる印
SCOPE_HEADING = "F5-heading"
SCOPE_TCL = "F5-tcl"
SCOPE_ANON = "F5-anon"
SCOPE_IMISH = "F5-imish"

# 名前の無い見出しのテキスト
ANON_TEXT = "{}"

# 最上位の見出しがこれで始まるとき、中を Tcl の塊として行のまま持つ
TCL_TOP_PREFIXES = ("ltm rule ", "gtm rule ", "pem irule ", "sys icall script ")
# どの段でも、見出しのテキストがこれのとき、中を Tcl の塊として行のまま持つ
TCL_HEADINGS = frozenset({"implementation", "presentation", "html-help", "definition", "macro"})

_QUOTE = '"'
_IMISH_START = "!!! imish configuration starts here"
_IMISH_END = "!!! imish configuration ends here"


class F5StructureError(ValueError):
    """F5 の config の形が崩れている(`{` `}` の対応・引用符・Tcl の塊の閉じ)。

    reason は分岐に使える理由(quote・unbalanced・tcl_open・tcl_close・tcl_inline)、line_no は問題の行(閉じていないものは開いた行)。
    文面に差し込むのは行番号だけで、行の中身・値は入れない。文面は理由の鍵から作る(言語で日本語・英語を選ぶ)。
    """

    def __init__(self, reason: str, line_no: int) -> None:
        self.reason = reason
        self.line_no = line_no
        super().__init__(_STRUCTURE["head"].format(line=line_no) + _STRUCTURE[reason].format())


# 形の誤りの文。理由の鍵 → 文の形。head は頭の文。`{{` `}}` は format で `{` `}` になる
_STRUCTURE_JA = {
    "head": "config の {line} 行目: ",
    "quote": "引用符が閉じないまま、ファイルの終わりになりました",
    "unbalanced": "{{ と }} の対応が取れません(}} が多いか、閉じていない見出しがあります。ファイルが途中で切れている可能性があります)",
    "tcl_open": "iRule などの Tcl の本文が閉じていません(ファイルが途中で切れている可能性があります)",
    "tcl_close": "iRule などの Tcl の本文の閉じが、開いた行と同じ字下げの }} だけの行になっていません",
    "tcl_inline": "iRule などの Tcl の本文が、行の途中の {{ で開いたまま、その行が終わっています",
}
_STRUCTURE_EN = {
    "head": "config, line {line}: ",
    "quote": "a quotation mark is not closed by the end of the file",
    "unbalanced": "{{ and }} do not match (there is an extra }}, or a heading is not closed; the file may have been cut off)",
    "tcl_open": "the Tcl body of an iRule or the like is not closed (the file may have been cut off)",
    "tcl_close": "the Tcl body of an iRule or the like is not closed by a line holding only }} at the same indentation as the opening line",
    "tcl_inline": "the Tcl body of an iRule or the like is opened by a {{ in the middle of a line, and the line ends with it still open",
}
_STRUCTURE = i18n.choose(_STRUCTURE_JA, _STRUCTURE_EN)


def _indent(raw: str) -> int:
    """行頭の空白の字の数。"""
    return len(raw) - len(raw.lstrip())


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


def _delimiters(line: str) -> list[tuple[int, str]]:
    """引用符の外にある `{`・`}` の (位置, 字)。`\\"` では引用符を閉じない。"""
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
        elif ch in "{}":
            found.append((index, ch))
        index += 1
    return found


def _word_list(raw: str) -> list[str]:
    """引用符の外の空白で語に分ける。改行を含む語(複数行の引用符の値)は 1 語に直す。"""
    return [multiline_word(word) if "\n" in word else word for word in _words(raw)]


def _tcl_depth_change(line: str) -> list[int]:
    """行の字ごとの、`\\` の付かない `{`(+1)・`}`(−1)の変化の並び(変化のある字だけ)。`\\` の次の字は数えない。"""
    changes: list[int] = []
    index = 0
    size = len(line)
    while index < size:
        ch = line[index]
        if ch == "\\":
            index += 2
            continue
        if ch == "{":
            changes.append(1)
        elif ch == "}":
            changes.append(-1)
        index += 1
    return changes


def _is_tcl_heading(text: str, top: bool) -> bool:
    return text in TCL_HEADINGS or (top and text.startswith(TCL_TOP_PREFIXES))


class _Builder:
    """木を作る。stack は今開いている見出し(ノード, 開いた行)。"""

    def __init__(self) -> None:
        self.tree = FlatTree(PLATFORM)
        self.stack: list[tuple[FlatNode, int]] = []
        self.headings: dict[tuple[int, str], FlatNode] = {}
        # (入れ物の id, 葉のテキスト) → 行番号の並び・入れ物の道すじ。警告の対象の葉だけ入れる
        self.leaves: dict[tuple[int, str], list[int]] = {}
        self.leaf_scope: dict[tuple[int, str], tuple[str, ...]] = {}
        self._masker: Any = None

    def is_plain(self, text: str) -> bool:
        """行全体(葉のテキスト)を、送る側の判定(Masker().judge_alone。祖先は今開いている見出しのテキスト)にかけて「そのまま送る」か。
        判定器は 1 回の組み立てで 1 つ。llm_analyzer.masking は config_parser を読み込むため、輪を避けて呼ぶときに読む(f5.py と同じ)。"""
        if self._masker is None:
            from driftpilot.llm_analyzer.masking import Masker

            self._masker = Masker()
        ancestors = tuple(node.text for node, _ in self.stack)
        return bool(self._masker.judge_alone(text, ancestors).decision == "plain")

    @property
    def parent(self) -> FlatNode | None:
        return self.stack[-1][0] if self.stack else None

    def _container(self) -> FlatTree | FlatNode:
        return self.stack[-1][0] if self.stack else self.tree

    def _warns(self) -> bool:
        """今の入れ物の直下の葉が、重複の警告の対象か(F5 の見出しの直下で、名前の無い見出しの下ではない)。"""
        if not self.stack:
            return False
        if self.stack[-1][0].scope_id != SCOPE_HEADING:
            return False
        return all(node.scope_id != SCOPE_ANON for node, _ in self.stack)

    def leaf(self, text: str, line_no: int, scope_id: str | None = None) -> None:
        parent = self.parent
        container = self._container()
        container._add(FlatNode(text, parent, line_no, scope_id=scope_id))
        if scope_id is None and self._warns():
            key = (id(container), text)
            self.leaves.setdefault(key, []).append(line_no)
            self.leaf_scope[key] = parent.path() if parent is not None else ()

    def heading(self, text: str, line_no: int, scope_id: str) -> FlatNode:
        parent = self.parent
        container = self._container()
        key = (id(container), text)
        node = self.headings.get(key)
        if node is None:
            node = container._add(FlatNode(text, parent, line_no, scope_id=scope_id))
            self.headings[key] = node
        return node

    def open(self, text: str, line_no: int) -> FlatNode:
        """見出しを開く(同じ親の下の同じテキストの見出しは、前のノードを使う)。"""
        if text:
            node = self.heading(text, line_no, SCOPE_HEADING)
        else:
            node = self.heading(ANON_TEXT, line_no, SCOPE_ANON)
        self.stack.append((node, line_no))
        return node

    def close(self, line_no: int) -> None:
        if not self.stack:
            raise F5StructureError("unbalanced", line_no)
        self.stack.pop()

    def finish(self) -> FlatTree:
        if self.stack:
            raise F5StructureError("unbalanced", self.stack[-1][1])
        self.tree.add_duplicates(
            DuplicateLine(self.leaf_scope[key], tuple(lines)) for key, lines in self.leaves.items() if len(lines) > 1
        )
        return self.tree


def _is_balanced(delims: list[tuple[int, str]]) -> bool:
    """`{`・`}` が 1 つ以上あり、行の中で全部が対になっているか(数が途中で 0 より下がらず、最後に 0 に戻る)。"""
    depth = 0
    for _, ch in delims:
        depth += 1 if ch == "{" else -1
        if depth < 0:
            return False
    return bool(delims) and depth == 0


def _is_simple(line: str, delims: list[tuple[int, str]]) -> bool:
    """行が `<語>… { <語>… }` だけでできているか(`{` の前に語があり、対が 1 組だけで、`}` の後ろに何も無い)。"""
    if len(delims) != 2 or delims[0][1] != "{" or delims[1][1] != "}":
        return False
    return bool(line[: delims[0][0]].strip()) and not line[delims[1][0] + 1 :].strip()


def _read_statements(builder: _Builder, line: str, line_no: int) -> FlatNode | None:
    """`{`・`}` を含む 1 行(論理行)を左から順に読む。行末の `{` が開いた見出しのノードを返す(それ以外は None)。

    戻り値は、その行の最後の字が見出しを開く `{` で、その見出しが Tcl の塊になるときだけ呼び側が使う。
    行の途中で Tcl の塊の見出しを開いたまま行が終わるときは、F5StructureError("tcl_inline")。
    """
    delims = _delimiters(line)
    if _is_balanced(delims):
        # 同じ行の中で開いて閉じる `{ … }` を含む行(`{`・`}` が行の中で対になり、前の行から続く `}` も後ろの行へ続く `{` も無い)
        text = " ".join(_word_list(line))
        if not builder.stack or not builder.is_plain(text):
            # 最上位の 1 行のまとまりと、入れ子の段で行全体の判定が「そのまま送る」でない行は、行全体を 1 つの葉にする(中身を構造化しない)
            builder.leaf(text, line_no)
            return None
    if _is_simple(line, delims):
        head = " ".join(_word_list(line[: delims[0][0]]))
        builder.open(head, line_no)
        for word in _word_list(line[delims[0][0] + 1 : delims[1][0]]):
            builder.leaf(word, line_no)
        builder.close(line_no)
        return None
    stripped_end = len(line.rstrip())
    start = 0
    tcl_open: FlatNode | None = None  # 行末の `{` が開いた Tcl の塊の見出し
    inline: list[FlatNode] = []  # 行の途中の `{` が開いた Tcl の塊の見出し
    for pos, ch in delims:
        words = _word_list(line[start:pos])
        text = " ".join(words)
        if ch == "{":
            top = not builder.stack
            node = builder.open(text, line_no)
            if text and _is_tcl_heading(text, top):
                if pos == stripped_end - 1:
                    tcl_open = node
                else:
                    inline.append(node)
        else:
            if text:
                builder.leaf(text, line_no)
            builder.close(line_no)
        start = pos + 1
    tail = " ".join(_word_list(line[start:]))
    if tail:
        builder.leaf(tail, line_no)
    for node in inline:
        if any(open_node is node for open_node, _ in builder.stack):
            raise F5StructureError("tcl_inline", line_no)
    return tcl_open


def build_f5_tree(text: str) -> FlatTree:
    """F5 BIG-IP の config のテキストを木にする(上の説明)。形が崩れていれば F5StructureError で止める。"""
    builder = _Builder()
    raws = text.splitlines()
    total = len(raws)
    index = 0
    while index < total:
        raw = raws[index]
        line_no = index + 1
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue
        if stripped == _IMISH_START:
            index = _read_imish(builder, raws, index)
            continue
        if stripped.startswith("#"):
            index += 1
            continue
        # 論理行: 引用符が行の終わりまでに閉じなければ、閉じの引用符のある行まで続ける
        parts = [raw]
        quoted = _still_quoted(raw, False)
        while quoted:
            index += 1
            if index >= total:
                raise F5StructureError("quote", line_no)
            parts.append(raws[index])
            quoted = _still_quoted(raws[index], True)
        index += 1
        logical = "\n".join(parts)
        if "{" not in logical and "}" not in logical:
            builder.leaf(" ".join(_word_list(logical)), line_no)
            continue
        tcl = _read_statements(builder, logical, line_no)
        if tcl is not None:
            index = _read_tcl(builder, tcl, raws, index, line_no, _indent(raw))
            builder.close(line_no)
    return builder.finish()


def _read_imish(builder: _Builder, raws: list[str], start: int) -> int:
    """imish の部分(開始の印の行 raws[start] から、終わりの印の行まで。無ければファイルの終わりまで)を最上位の葉にする。次に読む行の添字を返す。"""
    index = start
    while index < len(raws):
        line = raws[index].strip()
        index += 1
        if not line:
            continue
        builder.tree._add(FlatNode(line, None, index, scope_id=SCOPE_IMISH))
        if line.startswith(_IMISH_END):
            break
    return index


def _read_tcl(builder: _Builder, node: FlatNode, raws: list[str], start: int, open_line: int, indent: int) -> int:
    """Tcl の塊の中(raws[start] から)を、閉じの行まで葉にする。閉じの行の次の添字を返す(止める形は F5StructureError)。"""
    depth = 1
    index = start
    while index < len(raws):
        raw = raws[index]
        line_no = index + 1
        index += 1
        for change in _tcl_depth_change(raw):
            depth += change
            if depth <= 0:
                break
        if depth <= 0:
            if depth < 0 or raw.strip() != "}" or _indent(raw) != indent:
                raise F5StructureError("tcl_close", line_no)
            node.scope_id = SCOPE_TCL
            return index
        line = raw.strip()
        if line:
            node._add(FlatNode(line, node, line_no))
    raise F5StructureError("tcl_open", open_line)
