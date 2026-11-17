"""PAN-OS の XML の config を、自前の走査で「文の語の並び」に直す(ADR-0226 決定 2・5。REQ-327)。
標準ライブラリの XML の解析器(xml.etree など)は使わない。行番号を持つため、DTD・外部の実体を読まないため、壊れた XML を推測で閉じないため。

走査: 文字を先頭から読み、開きのタグ(属性つき)・閉じのタグ・空のタグ(`<x/>`)・文字・`<?…?>`・`<!-- … -->`・`<![CDATA[ … ]]>` を見分ける。
行番号は各タグが始まる行(1 始まり)。改行は CRLF・CR・LF のどれも 1 つの改行として数える。

構造のエラー(PanosStructureError。reason と行番号。文に差し込むのは行番号だけで、行の中身・値は入れない):
- dtd: `<!DOCTYPE`・`<!ENTITY` がある
- xml: 閉じのタグが今の開きのタグと違う、閉じていないタグが残る(開いた行)、`<` の後に `>` が無い、コメント・CDATA・`<?` が閉じない、
  引用符で囲まれていない属性の値・同じ属性が 2 つ・タグの名前が空、知らない名前の文字の参照・素の `&`、ほかの `<!…`
- mixed: 根の要素を閉じた後に空白でない文字がある(XML のあとに set などが続く形)
属性の値は `"` か `'` で囲まれたものだけ読む。文字の参照(`&amp;`・`&lt;`・`&gt;`・`&quot;`・`&apos;`・`&#数;`・`&#x16進;`)は戻す。
CDATA の中身はそのまま使う(参照は戻さない)。

文への直し:
- 根の要素は外す(`config` の属性も文にしない)。根が `config` でなければ、根の名前から経路を始める
- `devices` の下の `entry name="localhost.localdomain"` は、`devices` の語とともに経路から外す(どの深さでも。テンプレートの中でも。set の形と同じ)。
  この要素そのものが文を終える(空の要素)ときは外さない
- `vsys` の下の `entry name="vsys1"` の段(REQ-330)は、根の `config` の直下の `devices` > `entry localhost.localdomain` の下の `vsys` だけを見る。
  その `vsys` の下の文(`vsys <名前> …`)のうち、2 語目が `vsys1` でない文が 1 つも無いとき(`vsys1` だけのとき)、走査の終わりに `vsys vsys1` の 2 語を外す。
  `vsys2` などほかの `entry`・`entry` でない子・`vsys` に直に書いた値が 1 つでもあれば、どの文も外さない(`vsys vsys1 …`・`vsys vsys2 …`)。
  テンプレートの中・`device-group` の下・根が `config` でない XML の `vsys` は外さない。`<entry name="vsys1"/>` の空の要素は `vsys vsys1` のまま
- `entry` は `name` の属性の値を経路の語にする(`entry` の語は入れない)。`name` が無ければ `entry` を語にする
- `member` は、値を親の経路の後ろに足した 1 つの文にする。子の要素を持つ `member` は普通の要素(語 `member`)として読む
- 子の要素を持たず空白でない文字を持つ要素は、経路 + 要素の語 + 値の文。子も空白でない文字も持たない要素は、経路 + 要素の語の文
- 子の要素を持つ要素の中の文字は捨てる。`name` 以外の属性は文にしない
- 値の前後の空白を除く。空白か `"` があれば `"` で囲む(中の `"` は `\\"`)。空の値(`name=""` など)は `""`。set の形と同じ文になる
- 値に改行があるときは、値の代わりに `<複数行の値 <行数> 行 <sha256 の 16 進の頭 16 字>>` の 1 語にする(中身は出さない。英語で動かしても同じ文字)。
  行数は、前後の空白を除いた値を CRLF・CR・LF で分けた数。sha256 は、その行を LF でつないだ UTF-8 の SHA-256(保存し直して改行が変わっても同じ値)
- 文の行番号は、その文を終えた要素の開きのタグの行
"""

import hashlib
import re
from bisect import bisect_right

from driftpilot import i18n

PLATFORM = "paloalto_panos"
MULTILINE_WORD = "<複数行の値 {lines} 行 {digest}>"
_DIGEST_LENGTH = 16

_ROOT = "config"
_ENTRY = "entry"
_MEMBER = "member"
_NAME = "name"
# 経路から外す段(親の語, 外す entry の name)。`vsys` > `entry vsys1` は走査の終わりに決める(REQ-330)
_SKIPPED = {"devices": "localhost.localdomain"}
_DEVICES = "devices"
_LOCALHOST = "localhost.localdomain"
_VSYS = "vsys"
_VSYS1 = "vsys1"

_NEWLINES = re.compile(r"\r\n|\r|\n")
_NAMED_REFERENCES = {"amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'"}
_REFERENCE = re.compile(r"&(#[0-9]+|#x[0-9a-fA-F]+|[A-Za-z_][A-Za-z0-9._-]*);")
_NAME_STOP = frozenset(" \t\r\n/>=<\"'")


class PanosStructureError(ValueError):
    """PAN-OS の config の形が崩れている(XML の対応・DTD・形の混ざり・引用符・`[ ]`)。

    reason は分岐に使える理由(dtd・xml・mixed・quote・list)、line_no は問題の行(閉じていないものは開いた行)。
    文面に差し込むのは行番号だけで、行の中身・値は入れない。文面は理由の鍵から作る(言語で日本語・英語を選ぶ)。
    """

    def __init__(self, reason: str, line_no: int) -> None:
        self.reason = reason
        self.line_no = line_no
        super().__init__(_STRUCTURE["head"].format(line=line_no) + _STRUCTURE[reason].format())


# 形の誤りの文。理由の鍵 → 文の形。head は頭の文。`{{` `}}` は format で `{` `}` になる
_STRUCTURE_JA = {
    "head": "config の {line} 行目: ",
    "dtd": "DTD(<!DOCTYPE・<!ENTITY)は読みません",
    "xml": "XML のタグの対応が取れないか、書き方が壊れています(ファイルが途中で切れている可能性があります)",
    "mixed": "XML の形・set の形・{{ }} の形が 1 つの config に混ざっています",
    "quote": "引用符が行の終わりまでに閉じていません",
    "list": "[ と ] が同じ行の中で対になっていません",
}
_STRUCTURE_EN = {
    "head": "config, line {line}: ",
    "dtd": "a DTD (<!DOCTYPE or <!ENTITY) is not read",
    "xml": "the XML tags do not match or are broken (the file may have been cut off)",
    "mixed": "the XML form, the set form and the {{ }} form are mixed in one config",
    "quote": "a quotation mark is not closed by the end of the line",
    "list": "[ and ] are not paired within the same line",
}
_STRUCTURE = i18n.choose(_STRUCTURE_JA, _STRUCTURE_EN)


def quote_word(value: str) -> str:
    """値を語にする。空白か `"` があれば(空の値も)`"` で囲み、中の `"` は `\\"` にする。"""
    if not value or any(ch.isspace() for ch in value) or '"' in value:
        return '"' + value.replace('"', '\\"') + '"'
    return value


def multiline_word(value: str) -> str:
    """複数行の値を 1 語にする(中身は出さない)。value は前後の空白を除いたもの。"""
    lines = _NEWLINES.split(value)
    digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:_DIGEST_LENGTH]
    return MULTILINE_WORD.format(lines=len(lines), digest=digest)


def value_word(value: str) -> str:
    """要素の値(前後の空白は除いたもの)を文の語にする。改行があれば 1 語に直す。"""
    if _NEWLINES.search(value):
        return multiline_word(value)
    return quote_word(value)


class _Frame:
    """開いている要素 1 つ。"""

    def __init__(self, tag: str, name: str | None, line: int) -> None:
        self.tag = tag
        self.name = name  # entry の name の属性の値
        self.line = line
        self.has_children = False
        self.text: list[str] = []

    def word(self) -> str:
        """経路に入る語。entry は name の値、ほかは要素の名前。"""
        if self.tag == _ENTRY and self.name is not None:
            return quote_word(self.name)
        return self.tag


class _Scanner:
    def __init__(self, text: str) -> None:
        self.text = _NEWLINES.sub("\n", text)
        self.size = len(self.text)
        self.newlines = [match.start() for match in re.finditer("\n", self.text)]
        self.pos = 0
        self.stack: list[_Frame] = []
        self.root_closed = False
        self.root_seen = False
        self.statements: list[tuple[list[str], int]] = []
        # 根の機器(config > devices > entry localhost.localdomain)の vsys の下の文の位置(REQ-330)
        self.vsys_positions: list[int] = []
        self.other_vsys = False

    def line_of(self, pos: int) -> int:
        return bisect_right(self.newlines, pos - 1) + 1

    # --- 文字の参照 ---

    def decode(self, raw: str, base: int) -> str:
        """文字の参照を戻す。知らない名前・壊れた参照・素の `&` は xml のエラー(行は参照のある行。base は raw の先頭の位置)。"""
        if "&" not in raw:
            return raw
        out: list[str] = []
        last = 0
        for match in re.finditer("&", raw):
            if match.start() < last:
                continue
            where = self.line_of(base + match.start())
            found = _REFERENCE.match(raw, match.start())
            if found is None:
                raise PanosStructureError("xml", where)
            body = found.group(1)
            if body.startswith("#"):
                try:
                    code = int(body[2:], 16) if body[1:2] == "x" else int(body[1:])
                    if code == 0 or 0xD800 <= code <= 0xDFFF:
                        raise ValueError
                    char = chr(code)
                except (ValueError, OverflowError):
                    raise PanosStructureError("xml", where) from None
            elif body in _NAMED_REFERENCES:
                char = _NAMED_REFERENCES[body]
            else:
                raise PanosStructureError("xml", where)
            out.append(raw[last : match.start()])
            out.append(char)
            last = found.end()
        out.append(raw[last:])
        return "".join(out)

    # --- 走査 ---

    def run(self) -> list[tuple[list[str], int]]:
        while self.pos < self.size:
            index = self.text.find("<", self.pos)
            chunk = self.text[self.pos : self.size if index < 0 else index]
            if chunk:
                self.on_text(chunk, self.pos)
            if index < 0:
                break
            self.pos = index
            self.on_markup(index)
        if self.stack:
            raise PanosStructureError("xml", self.stack[-1].line)
        self.drop_single_vsys1()
        return self.statements

    def drop_single_vsys1(self) -> None:
        """根の機器の vsys が `vsys1` だけなら、その下の文から `vsys vsys1` の 2 語を外す(REQ-330)。"""
        if self.other_vsys:
            return
        for position in self.vsys_positions:
            words, line = self.statements[position]
            if len(words) > 2:
                self.statements[position] = (words[2:], line)

    def on_text(self, chunk: str, start: int) -> None:
        if not self.stack:
            if chunk.strip():
                raise PanosStructureError("mixed" if self.root_closed else "xml", self.line_of(start + len(chunk) - len(chunk.lstrip())))
            return
        self.stack[-1].text.append(self.decode(chunk, start))

    def on_markup(self, index: int) -> None:
        text = self.text
        if text.startswith("<!--", index):
            end = text.find("-->", index + 4)
            if end < 0:
                raise PanosStructureError("xml", self.line_of(index))
            self.pos = end + 3
            return
        if text.startswith("<![CDATA[", index):
            end = text.find("]]>", index + 9)
            if end < 0 or not self.stack:
                raise PanosStructureError("xml", self.line_of(index))
            self.stack[-1].text.append(text[index + 9 : end])
            self.pos = end + 3
            return
        if text.startswith("<?", index):
            end = text.find("?>", index + 2)
            if end < 0:
                raise PanosStructureError("xml", self.line_of(index))
            self.pos = end + 2
            return
        if text.startswith("<!", index):
            head = text[index + 2 : index + 9].upper()
            if head.startswith(("DOCTYPE", "ENTITY")):
                raise PanosStructureError("dtd", self.line_of(index))
            raise PanosStructureError("xml", self.line_of(index))
        if text.startswith("</", index):
            self.on_close(index)
            return
        self.on_open(index)

    def read_name(self, pos: int, tag_start: int) -> tuple[str, int]:
        end = pos
        while end < self.size and self.text[end] not in _NAME_STOP:
            end += 1
        if end == pos:
            raise PanosStructureError("xml", self.line_of(tag_start))
        return self.text[pos:end], end

    def skip_space(self, pos: int) -> int:
        while pos < self.size and self.text[pos] in " \t\n":
            pos += 1
        return pos

    def on_close(self, index: int) -> None:
        name, end = self.read_name(index + 2, index)
        end = self.skip_space(end)
        if end >= self.size or self.text[end] != ">":
            raise PanosStructureError("xml", self.line_of(index))
        if not self.stack or self.stack[-1].tag != name:
            raise PanosStructureError("xml", self.line_of(index))
        self.pos = end + 1
        frame = self.stack.pop()
        self.finish(frame, empty=False)
        if not self.stack:
            self.root_closed = True

    def on_open(self, index: int) -> None:
        tag, pos = self.read_name(index + 1, index)
        attributes: dict[str, str] = {}
        while True:
            pos = self.skip_space(pos)
            if pos >= self.size:
                raise PanosStructureError("xml", self.line_of(index))
            ch = self.text[pos]
            if ch == ">":
                self_closing = False
                pos += 1
                break
            if ch == "/":
                if pos + 1 >= self.size or self.text[pos + 1] != ">":
                    raise PanosStructureError("xml", self.line_of(index))
                self_closing = True
                pos += 2
                break
            key, pos = self.read_name(pos, index)
            pos = self.skip_space(pos)
            if pos >= self.size or self.text[pos] != "=":
                raise PanosStructureError("xml", self.line_of(index))
            pos = self.skip_space(pos + 1)
            if pos >= self.size or self.text[pos] not in "\"'":
                raise PanosStructureError("xml", self.line_of(index))
            quote = self.text[pos]
            close = self.text.find(quote, pos + 1)
            if close < 0:
                raise PanosStructureError("xml", self.line_of(index))
            if key in attributes:
                raise PanosStructureError("xml", self.line_of(index))
            attributes[key] = self.decode(self.text[pos + 1 : close], pos + 1)
            pos = close + 1
            if pos < self.size and self.text[pos] not in " \t\n/>":
                raise PanosStructureError("xml", self.line_of(index))
        self.pos = pos
        if not self.stack and self.root_seen:
            raise PanosStructureError("xml", self.line_of(index))
        self.root_seen = True
        if self.stack:
            self.stack[-1].has_children = True
        frame = _Frame(tag, attributes.get(_NAME) if tag == _ENTRY else None, self.line_of(index))
        if self_closing:
            self.finish(frame, empty=True)
            if not self.stack:
                self.root_closed = True
            return
        self.stack.append(frame)

    # --- 文への直し ---

    def ancestor_words(self) -> list[str]:
        """開いている要素(今の要素の親まで)の経路の語。根は外す(`config` のとき)。外す段の 2 語も外す。"""
        frames = self.stack
        words: list[str] = []
        for depth, frame in enumerate(frames):
            if depth == 0 and frame.tag == _ROOT:
                continue
            nxt = frames[depth + 1] if depth + 1 < len(frames) else None
            if nxt is not None and self.is_skipped(frame, nxt):
                continue
            if depth > 0 and self.is_skipped(frames[depth - 1], frame):
                continue
            words.append(frame.word())
        return words

    @staticmethod
    def is_skipped(parent: _Frame, child: _Frame) -> bool:
        return child.tag == _ENTRY and parent.tag in _SKIPPED and child.name == _SKIPPED[parent.tag]

    def finish(self, frame: _Frame, empty: bool) -> None:
        """要素を終えた。文を終える要素(値のある要素・空の要素・member)なら、文を足す。frame は stack から外した後。"""
        if frame.has_children:
            return
        value = "".join(frame.text).strip()
        # 根の要素の `config` は外す(文にならない)
        if not self.stack and frame.tag == _ROOT:
            return
        words = self.ancestor_words()
        if frame.tag == _MEMBER and value:
            words.append(value_word(value))
        elif value:
            words.extend([frame.word(), value_word(value)])
        else:
            words.append(frame.word())
        self.note_vsys_statement(words, frame)
        self.statements.append((words, frame.line))

    def note_vsys_statement(self, words: list[str], frame: _Frame) -> None:
        """文が根の機器の vsys の下なら位置を覚え、2 語目が `vsys1` でなければ「ほかの vsys がある」と記録する(REQ-330)。"""
        chain = [*self.stack, frame]
        if len(chain) < 4 or chain[0].tag != _ROOT:
            return
        devices, device, vsys = chain[1], chain[2], chain[3]
        if devices.tag != _DEVICES or device.tag != _ENTRY or device.name != _LOCALHOST or vsys.tag != _VSYS:
            return
        if len(words) < 2 or words[0] != _VSYS:
            return
        self.vsys_positions.append(len(self.statements))
        if words[1] != _VSYS1:
            self.other_vsys = True


def xml_statements(text: str) -> list[tuple[list[str], int]]:
    """XML の config を、(文の語の並び, 行番号) の並びにする(出た順)。形が崩れていれば PanosStructureError。"""
    return _Scanner(text).run()
