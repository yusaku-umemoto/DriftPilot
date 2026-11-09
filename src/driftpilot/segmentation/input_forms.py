"""入力の形を戻す工程(ADR-0138 決定1・3・7。REQ-185)。比較に使うファイルの読み込みの入口を1つにする。

工程の順(ADR-0138 決定1): ファイル(バイト列) → ① 文字コード → ②〜⑤(REQ-186〜188) → ページ送りの印 → 切り分け。
CLI・一括・画面のファイルは read_config_input、画面の貼り付けは normalize_text を通す。

① 文字コード(決定3): UTF-8 の BOM → UTF-16 の BOM → UTF-8 → cp932 の順に試す。UTF-8 以外で読んだときは
知らせ(InputNote)を付ける。入口はこれを SideInput.notes に入れ、core が警告 input_form にする(決定7)。
どれでも読めなければ UTF-8 として読んだときの UnicodeDecodeError を上げる(入口の文は今までどおり)。
改行は Path.read_text と同じく \\r\\n と \\r を \\n にそろえる(UTF-8 のときに今までと同じ文字列にするため)。

normalize_text は文字コードの後の工程の入口(②〜⑤)。

② RTF・HTML(決定4。REQ-186)。どちらか一方だけを行い、どちらにも当たらなければ何も変えない。
- RTF: 左の空白を除いた先頭が {\\rtf のとき。字句を順に読み、本文だけを取り出す(_rtf_text)。知らせ rtf
- HTML: 左の空白を除いた先頭が < で、<pre …> と、その後ろに </pre> があるとき。中を取り、直後の改行1つを捨て
  (HTML の決まりと同じ。判断の記録 1)、タグを消してから html.unescape する。知らせ html

③ 全行の付け足し(決定5。REQ-187)。空でない行(空白・タブ・\\r のほかに何かある行)が 3 行以上で、そのすべてが同じ形に
当たるときだけ外す。形は _PREFIXES を上から試し、当たった最初の1つだけ。知らせは種類と外した行の数。
- 印の後ろの空白は、本文がある行のうち一番短い数にそろえて外す(REQ-187 の判断の記録 1)。印だけの行は数に入れない。
  これで `1: ` や cat -n(数字の後ろがタブ)でも、本文の頭の空白(Cisco の階層)を取らない
- 行番号は、空でない行の数字が上から 1 ずつ増えるときだけ外す(判断の記録 2)。表の 10・20・30 の列を消さない
④ 共通の字下げ(決定5。REQ-187)。③ の後で、空でない行が 3 行以上あり、そのすべての先頭が空白かタブなら、
先頭の空白・タブの一番少ない数だけ外す。知らせ indent(数は空でない行の数)
③・④ は行を \\n だけで分け、外した部分のほかは変えない(行末の \\r も残す)。空の行・空白だけの行は数えない。

⑤ 折り返し(決定6。REQ-188)。FortiGate の形(字下げの無い `config ` の行と、字下げのある `edit `・`set ` の行がある)だけ。
幅は右の空白を落とした一番長い行の長さで、40 未満なら何もしない。証拠は幅ちょうどの行が 3 本以上。
引用符(`"` と `'`。`\\` の次の1文字は飛ばす)を行をまたいで追い、次の行が空でない間、次の条件でつなぐ。知らせ rejoined(つないだ数)
- 引用符の外: 次の行の最初の語が _HEADS のどれでもなく、`#` で始まらず、直前の1本(端末の1行)が 幅 - 8 以上で、
  今の行が config の塊の中にあるか、今の行(つないでいる途中の行)が左の空白を除いて `#` で始まる見出しの行のとき
  (塊の外の出力の行・プロンプトの行をつながないため。決定6 に足した条件。REQ-188 の判断の記録 3)。
  塊の深さは、文の頭の語(引用符の中で始まった行とつないだ続きは数えない)が config なら 1 増やし、end なら 1 減らす
  (0 より下にはしない。字下げは問わないので、マルチ VDOM の `config vdom` の中の字下げの無い config・end も数える)
- 引用符の中: つないでいる途中の行の長さが幅の倍数で、証拠があるときだけ。依頼書の「set の行で開いた値」の条件は入れない
  (折り返しの無い config の鍵の行がつながるため。REQ-188 の判断の記録 1)
長さは行末の \\r を除いた文字の数。間に何も入れず、つないだ側の行末の \\r は落とす(最後の1本の \\r は残す)。

案内(決定7。REQ-189)。入力は直さず、比較を止める条件も増やさない。diagnose_input は形を戻した後のテキスト(①〜⑤の後)を見て、
当たった案内(InputHint)を表の順に返す。core が側ごとに警告 input_hint にし、構造のエラーの文の後ろにも足す。
案内に入るのは種類と数(と幅・付け足しの形)だけで、行の中身は入れない。
- wrap_suspected: FortiGate の形でなく、幅(⑤ と同じ数え方)が 40 以上で、幅ちょうどの行が 3 本以上。数は幅ちょうどの行
- wrap_unrepaired: FortiGate の形で、⑤ の後も、引用符の外で始まり、config の塊の中にあり、文の頭(_starts_statement)で
  ない行が残る。塊の深さは ⑤ と同じく数える。行頭のページ送りの印は外してから数える(REQ-189 の判断の記録 1)
- html_no_pre: 左の空白を除いた先頭が < で、<html がある(大文字・小文字は問わない)。そして <pre(② と同じ正規表現)が無い
- utf16_no_bom: 先頭の 200 文字の半分以上が NUL。英数字だけの BOM の無い UTF-16 は UTF-8 として読めてしまい、
  1 バイトが 1 文字になるので、バイト列で見るのと同じ(判断の記録 2)。読めないときは diagnose_bytes がバイト列で見て、
  InputDecodeError の hints に入れる(入口が読めない文の後ろに足す)
- partial_prefix: 空でない行が 3 行以上で、③ の形を上から試し、8 割以上が当たった最初の1つで、全部ではないとき。
  数は当たらなかった行。行番号は ③ と同じく、当たった行の数字が 1 ずつ増えるときだけ
"""

import html
import re
from dataclasses import dataclass
from pathlib import Path

from driftpilot.segmentation.splitter import _strip_pager

ENCODING_UTF8_BOM = "encoding:utf-8-bom"
ENCODING_UTF16 = "encoding:utf-16"
ENCODING_CP932 = "encoding:cp932"

# Python の cp932 は、表に無い1バイト(0x80・0xA0・0xFD〜0xFF)をエラーにせず U+0080・U+F8F0〜U+F8F3 に変える。
# これが1つでもあれば cp932 として読めないことにする(判断の記録 1)
_CP932_UNMAPPED = frozenset("\x80\uf8f0\uf8f1\uf8f2\uf8f3")

_REPLACEMENT = "\ufffd"

RTF = "rtf"
HTML = "html"

# RTF の字句。順に \'xx・制御語(直後の空白1つは区切り)・行末の \・制御記号・段・字句でない改行・文字
_RTF_TOKEN = re.compile(
    r"\\'(?P<hex>[0-9a-fA-F]{2})"
    r"|\\(?P<word>[a-zA-Z]+)(?P<num>-?\d+)? ?"
    r"|\\(?P<eol>\r\n|\n|\r)"
    r"|\\(?P<symbol>.?)"
    r"|(?P<group>[{}])"
    r"|(?P<newline>\r\n|\r|\n)"
    r"|(?P<text>[^\\{}\r\n]+)",
    re.DOTALL,
)
# 段の最初の字句がこれなら、その段を丸ごと捨てる(\* は制御記号)
_RTF_SKIP_WORDS = frozenset({"fonttbl", "colortbl", "stylesheet", "info", "expandedcolortbl", "pict"})
_RTF_NEWLINE_WORDS = frozenset({"par", "line"})
_RTF_LITERAL_SYMBOLS = frozenset("\\{}")

# <pre の後に空白か > が続くものだけ(<pre-shared-key> は数えない)
_PRE_OPEN = re.compile(r"<pre(\s[^>]*)?>")
_PRE_CLOSE = "</pre>"
_FIRST_NEWLINE = re.compile(r"\r\n|\n|\r")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")

TIMESTAMP = "timestamp"
LINE_NUMBER = "line_number"
MAIL_QUOTE = "mail_quote"
INDENT = "indent"

# 全行の付け足し(REQ-187 §2 の表と同じ形。後ろの空白だけを sep に分ける)。上から試す
_PREFIXES = (
    (TIMESTAMP, re.compile(r"^\[?\d{4}[-/]\d{2}[-/]\d{2}[ T]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\]?(?P<sep>\s?)")),
    (TIMESTAMP, re.compile(r"^\[\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\](?P<sep>\s?)")),
    (LINE_NUMBER, re.compile(r"^\s*(?P<num>\d+)[:|]?(?P<sep>\s{1,2}|$)")),
    (MAIL_QUOTE, re.compile(r"^>(?P<sep> ?)")),
)
_MIN_LINES = 3
_BLANK = " \t\r"
_INDENT_CHARS = " \t"

REJOINED = "rejoined"

# 折り返し(REQ-188 §2)
_MIN_WIDTH = 40
_EVIDENCE_LINES = 3
_OUTSIDE_SLACK = 8
_HEADS = frozenset({"set", "unset", "config", "edit", "next", "end", "append", "purge"})
_COMMENT = "#"
_CONFIG = "config"
_END = "end"
_REJOIN_QUOTES = frozenset("\"'")
_FGT_CONFIG = re.compile(r"^config ", re.MULTILINE)
_FGT_ENTRY = re.compile(r"^[ \t]+(?:edit|set) ", re.MULTILINE)

# 案内(REQ-189 §2)
WRAP_SUSPECTED = "wrap_suspected"
WRAP_UNREPAIRED = "wrap_unrepaired"
HTML_NO_PRE = "html_no_pre"
UTF16_NO_BOM = "utf16_no_bom"
PARTIAL_PREFIX = "partial_prefix"
_HTML_OPEN = "<html"
_UTF16_HEAD = 200
_BOMS = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_PARTIAL_SHARE = (4, 5)  # 8 割


@dataclass(frozen=True)
class InputNote:
    """入力の形を戻したことの知らせ1件。kind は種類(分岐に使わない文字列)、count は数(文字コードは 1)。"""

    kind: str
    count: int


@dataclass(frozen=True)
class InputHint:
    """入力の様子から当てた案内1件(REQ-189)。kind は種類(分岐に使わない文字列)、count は数。

    width は wrap_suspected の幅、form は partial_prefix の形(timestamp・line_number・mail_quote)。ほかの種類では None。
    """

    kind: str
    count: int
    width: int | None = None
    form: str | None = None


class InputDecodeError(UnicodeDecodeError):
    """読めないときの例外。UTF-8 として読んだときの UnicodeDecodeError と同じ中身に、案内(hints)を足したもの。"""

    def __init__(self, cause: UnicodeDecodeError, hints: tuple[InputHint, ...]) -> None:
        super().__init__(cause.encoding, cause.object, cause.start, cause.end, cause.reason)
        self.hints = hints


@dataclass(frozen=True)
class InputText:
    """形を戻した後のテキストと、知らせ(工程の順)。"""

    text: str
    notes: tuple[InputNote, ...] = ()


def read_config_input(path: str | Path) -> InputText:
    """ファイルをバイト列で読み、形を戻す。OSError はそのまま、読めないときは InputDecodeError を上げる(文は入口ごと)。"""
    return normalize_input(Path(path).read_bytes())


def normalize_input(data: bytes) -> InputText:
    """文字コードを決めて読み、normalize_text に渡す。文字コードの知らせを前に付けて返す。

    読めないときは、バイト列から当てた案内(diagnose_bytes)を持たせた InputDecodeError を上げる。
    """
    try:
        text, kind = _decode(data)
    except UnicodeDecodeError as exc:
        raise InputDecodeError(exc, diagnose_bytes(data)) from None
    result = normalize_text(text.replace("\r\n", "\n").replace("\r", "\n"))
    if kind is None:
        return result
    return InputText(result.text, (InputNote(kind, 1), *result.notes))


def normalize_text(text: str) -> InputText:
    """文字コードの後の工程(ADR-0138 決定1 の ②〜⑤)。② RTF・HTML(REQ-186)、③ 付け足し、④ 字下げ(REQ-187)、⑤ 折り返し(REQ-188)。"""
    notes: list[InputNote] = []
    text = _markup_text(text, notes)
    text = _strip_prefix(text, notes)
    text = _strip_indent(text, notes)
    text = _rejoin(text, notes)
    return InputText(text, tuple(notes))


def _markup_text(text: str, notes: list[InputNote]) -> str:
    """② RTF・HTML の本文。どちらにも当たらなければそのまま。"""
    head = text.lstrip()
    if head.startswith("{\\rtf"):
        notes.append(InputNote(RTF, 1))
        return _rtf_text(head)
    if head.startswith("<"):
        body = _pre_text(text)
        if body is not None:
            notes.append(InputNote(HTML, 1))
            return body
    return text


def _split_cr(line: str) -> tuple[str, str]:
    """(行末の \\r を除いた行, \\r か空)。"""
    return (line[:-1], "\r") if line.endswith("\r") else (line, "")


def _strip_prefix(text: str, notes: list[InputNote]) -> str:
    """③ 全行に付いた時刻・行番号・引用を外す。空でない行のすべてが同じ形に当たったときだけ。"""
    lines = text.split("\n")
    filled = [i for i, line in enumerate(lines) if line.strip(_BLANK)]
    if len(filled) < _MIN_LINES:
        return text
    for kind, pattern in _PREFIXES:
        matches = [pattern.match(_split_cr(lines[i])[0]) for i in filled]
        if not all(matches):
            continue
        if kind == LINE_NUMBER and not _counts_up([int(m.group("num")) for m in matches]):
            continue
        # 本文がある行の、印の後ろの空白の一番短い数(判断の記録 1)。印だけの行は数えない
        seps = [len(m.group("sep")) for m in matches if m.string[m.end() :].strip(_INDENT_CHARS)]
        keep = min(seps) if seps else None
        for i, m in zip(filled, matches):
            body, cr = _split_cr(lines[i])
            sep = len(m.group("sep")) if keep is None else min(len(m.group("sep")), keep)
            lines[i] = body[m.start("sep") + sep :] + cr
        notes.append(InputNote(kind, len(filled)))
        return "\n".join(lines)
    return text


def _counts_up(numbers: list[int]) -> bool:
    """上から 1 ずつ増えるか(始まりの数は問わない)。"""
    return all(b == a + 1 for a, b in zip(numbers, numbers[1:]))


def _indent_width(line: str) -> int:
    return len(line) - len(line.lstrip(_INDENT_CHARS))


def _strip_indent(text: str, notes: list[InputNote]) -> str:
    """④ すべての空でない行の先頭に空白かタブがあれば、一番少ない数だけ外す。空白だけの行はある分だけ外す。"""
    lines = text.split("\n")
    filled = [line for line in lines if line.strip(_BLANK)]
    if len(filled) < _MIN_LINES:
        return text
    width = min(_indent_width(line) for line in filled)
    if width == 0:
        return text
    notes.append(InputNote(INDENT, len(filled)))
    return "\n".join(line[min(width, _indent_width(line)) :] for line in lines)


def _is_fortigate(text: str) -> bool:
    return _FGT_CONFIG.search(text) is not None and _FGT_ENTRY.search(text) is not None


def _first_word(line: str) -> str | None:
    words = line.split(None, 1)
    return words[0] if words else None


def _starts_statement(line: str) -> bool:
    """文の頭の行か(左の空白を除いた最初の語が _HEADS と完全に同じか、先頭が #)。"""
    if line.lstrip(_INDENT_CHARS).startswith(_COMMENT):
        return True
    return _first_word(line) in _HEADS


def _block_depth(depth: int, head: str | None) -> int:
    """文の頭の語が config なら深さを 1 増やし、end なら 1 減らす(0 より下にはしない)。字下げは問わない。"""
    if head == _CONFIG:
        return depth + 1
    if head == _END:
        return max(depth - 1, 0)
    return depth


def _feed_quotes(chunk: str, quote: str | None, escape: bool) -> tuple[str | None, bool]:
    """文字を読んで、引用符の状態(開いている引用符, 次の1文字を飛ばすか)を進める。fortinet_tree._Quotes と同じ決まり。"""
    for ch in chunk:
        if escape:
            escape = False
        elif ch == "\\":
            escape = True
        elif quote is None:
            if ch in _REJOIN_QUOTES:
                quote = ch
        elif ch == quote:
            quote = None
    return quote, escape


def _rejoin(text: str, notes: list[InputNote]) -> str:
    """⑤ FortiGate の形の入力だけ、端末の幅で割れた行をつなぎ直す(REQ-188 §2)。"""
    if not _is_fortigate(text):
        return text
    lines = text.split("\n")
    lengths = [len(line.rstrip()) for line in lines]
    width = max(lengths)
    if width < _MIN_WIDTH:
        return text
    evidence = lengths.count(width) >= _EVIDENCE_LINES
    out: list[str] = []
    joined = 0
    depth = 0  # config の塊の深さ(今の行の前まで)
    current, cr = _split_cr(lines[0])
    head = _first_word(current)  # 今の行の文の頭の語。引用符の中で始まった行は None
    quote, escape = _feed_quotes(current, None, False)
    piece = len(current)  # 直前の1本(端末の1行)の長さ
    for line in lines[1:]:
        body, line_cr = _split_cr(line)
        if not line.strip(_BLANK):
            join = False
        elif quote is None:
            # 塊の外の出力の行・プロンプトの行はつながない。# の見出しは塊の前でもつなぐ(REQ-188 の判断の記録 3)
            in_block = depth > 0 or head == _CONFIG or current.lstrip(_INDENT_CHARS).startswith(_COMMENT)
            join = in_block and not _starts_statement(body) and piece >= width - _OUTSIDE_SLACK
        else:
            join = evidence and len(current) % width == 0
        if join:
            current += body
            joined += 1
        else:
            out.append(current + cr)
            depth = _block_depth(depth, head)
            current = body
            # 本物の改行。\ の後なら、これが飛ばす1文字になる(木の組み立てと同じ)
            quote, escape = _feed_quotes("\n", quote, escape)
            head = None if quote is not None else _first_word(body)
        cr = line_cr
        quote, escape = _feed_quotes(body, quote, escape)
        piece = len(body)
    out.append(current + cr)
    if not joined:
        return text
    notes.append(InputNote(REJOINED, joined))
    return "\n".join(out)


def diagnose_input(text: str) -> tuple[InputHint, ...]:
    """形を戻した後のテキストから案内を当てる(REQ-189 §2)。並びは表の順。入力は変えない。"""
    checks = (_wrap_suspected, _wrap_unrepaired, _html_no_pre, _utf16_text, _partial_prefix)
    return tuple(hint for check in checks if (hint := check(text)) is not None)


def diagnose_bytes(data: bytes) -> tuple[InputHint, ...]:
    """読めなかったバイト列から案内を当てる。BOM で始まらず、先頭の 200 バイトの半分以上が 0 なら utf16_no_bom。"""
    head = data[:_UTF16_HEAD]
    if data.startswith(_BOMS) or not _mostly_nul(head.count(0), len(head)):
        return ()
    return (InputHint(UTF16_NO_BOM, 1),)


def _mostly_nul(nul: int, size: int) -> bool:
    return size > 0 and nul * 2 >= size


def _wrap_suspected(text: str) -> InputHint | None:
    """FortiGate 以外の折り返しの疑い。幅は ⑤ と同じく、右の空白を落とした一番長い行の長さ。"""
    if _is_fortigate(text):
        return None
    lengths = [len(line.rstrip()) for line in text.split("\n")]
    width = max(lengths)
    count = lengths.count(width)
    if width < _MIN_WIDTH or count < _EVIDENCE_LINES:
        return None
    return InputHint(WRAP_SUSPECTED, count, width=width)


def _wrap_unrepaired(text: str) -> InputHint | None:
    """FortiGate の形で、⑤ の後も塊の中に残った、文の頭でない行(引用符の外で始まるもの)。印は外してから数える。"""
    if not _is_fortigate(text):
        return None
    stripped, _ = _strip_pager(text)
    count = 0
    depth = 0  # config の塊の深さ(今の行の前まで)
    quote: str | None = None
    escape = False
    for line in stripped.split("\n"):
        body, _ = _split_cr(line)
        if quote is None and line.strip(_BLANK):
            if depth > 0 and not _starts_statement(body):
                count += 1
            depth = _block_depth(depth, _first_word(body))
        quote, escape = _feed_quotes(body + "\n", quote, escape)
    return InputHint(WRAP_UNREPAIRED, count) if count else None


def _html_no_pre(text: str) -> InputHint | None:
    """<pre の無い HTML。<pre の有無は ② の取り出しと同じ正規表現で見る(<pre-shared-key> は数えない)。"""
    if not text.lstrip().startswith("<") or _HTML_OPEN not in text.lower() or _PRE_OPEN.search(text):
        return None
    return InputHint(HTML_NO_PRE, 1)


def _utf16_text(text: str) -> InputHint | None:
    """UTF-8 として読めてしまった BOM の無い UTF-16(判断の記録 2)。先頭の 200 文字の半分以上が NUL。"""
    head = text[:_UTF16_HEAD]
    return InputHint(UTF16_NO_BOM, 1) if _mostly_nul(head.count("\0"), len(head)) else None


def _partial_prefix(text: str) -> InputHint | None:
    """③ の形が 8 割以上の行に付いているが、全部ではない(③ は外さない)。数は当たらなかった行。"""
    filled = [_split_cr(line)[0] for line in text.split("\n") if line.strip(_BLANK)]
    if len(filled) < _MIN_LINES:
        return None
    share, whole = _PARTIAL_SHARE
    for kind, pattern in _PREFIXES:
        matches = [m for line in filled if (m := pattern.match(line))]
        if len(matches) * whole < len(filled) * share:
            continue
        if kind == LINE_NUMBER and not _counts_up([int(m.group("num")) for m in matches]):
            continue
        missed = len(filled) - len(matches)
        return InputHint(PARTIAL_PREFIX, missed, form=kind) if missed else None
    return None


def _pre_text(text: str) -> str | None:
    """最初の <pre …> から </pre> までの本文。</pre> が無ければ None。"""
    opened = _PRE_OPEN.search(text)
    if opened is None:
        return None
    end = text.find(_PRE_CLOSE, opened.end())
    if end < 0:
        return None
    body = text[opened.end() : end]
    first = _FIRST_NEWLINE.match(body)
    if first is not None:
        body = body[first.end() :]
    return html.unescape(_HTML_TAG.sub("", body))


def _decode_rtf_bytes(data: bytearray) -> str:
    """\\'xx で集めたバイトを cp932 で読む。読めない文字と表に無い1バイトは U+FFFD にする(判断の記録 1)。"""
    text = bytes(data).decode("cp932", errors="replace")
    return "".join(_REPLACEMENT if c in _CP932_UNMAPPED else c for c in text)


class _RtfOut:
    """取り出した文字。\\'xx のバイトと、\\uN の高位サロゲート(ペアの前半)は、続きが来るまで持っておく。"""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.pending_bytes = bytearray()
        self.high: int | None = None

    def _flush_bytes(self) -> None:
        if self.pending_bytes:
            self.parts.append(_decode_rtf_bytes(self.pending_bytes))
            self.pending_bytes.clear()

    def _flush_high(self) -> None:
        if self.high is not None:
            self.parts.append(_REPLACEMENT)
            self.high = None

    def flush(self) -> None:
        self._flush_bytes()
        self._flush_high()

    def add(self, text: str) -> None:
        self.flush()
        self.parts.append(text)

    def add_byte(self, value: int) -> None:
        self._flush_high()
        self.pending_bytes.append(value)

    def add_unicode(self, code: int) -> None:
        """\\uN の文字。高位と低位のサロゲートが続けば1文字にし、ペアにならないものは U+FFFD にする。"""
        self._flush_bytes()
        if 0xDC00 <= code <= 0xDFFF:
            if self.high is None:
                self.parts.append(_REPLACEMENT)
            else:
                self.parts.append(chr(0x10000 + ((self.high - 0xD800) << 10) + (code - 0xDC00)))
                self.high = None
            return
        self._flush_high()
        if 0xD800 <= code <= 0xDBFF:
            self.high = code
        else:
            self.parts.append(chr(code))

    def text(self) -> str:
        self.flush()
        return "".join(self.parts)


def _rtf_text(text: str) -> str:
    """RTF の本文(REQ-186 §2 の表)。いちばん外の段が閉じたら、その後ろは読まない。"""
    out = _RtfOut()
    depth = 0
    skip_depth = 0  # 捨てている段の深さ。0 なら捨てていない
    group_start = False  # 段の最初の字句を待っている
    after_unicode = False  # 直前が \uN(後ろの ? を1つ捨てる)
    for m in _RTF_TOKEN.finditer(text):
        # 数の付いた制御語は lastgroup が num になるので、word を先に見る
        kind = "word" if m.group("word") is not None else m.lastgroup
        if kind == "newline":
            continue
        value = m.group(kind)
        if kind == "group":
            group_start = False
            after_unicode = False
            if value == "{":
                depth += 1
                group_start = skip_depth == 0
                continue
            if skip_depth and depth == skip_depth:
                skip_depth = 0
            depth -= 1
            if depth <= 0:
                break
            continue
        if group_start:
            group_start = False
            if (kind == "word" and value in _RTF_SKIP_WORDS) or (kind == "symbol" and value == "*"):
                skip_depth = depth
        if skip_depth:
            continue
        if kind == "text":
            if after_unicode and value.startswith("?"):
                value = value[1:]
            after_unicode = False
            if value:
                out.add(value)
            continue
        after_unicode = False
        if kind == "hex":
            out.add_byte(int(value, 16))
        elif kind == "word" and value == "u" and m.group("num") is not None:
            out.add_unicode(int(m.group("num")) & 0xFFFF)
            after_unicode = True
        elif kind == "word" and value in _RTF_NEWLINE_WORDS or kind == "eol":
            out.add("\n")
        elif kind == "word" and value == "tab":
            out.add("\t")
        elif kind == "symbol" and value in _RTF_LITERAL_SYMBOLS:
            out.add(value)
        else:
            out.flush()
    return out.text()


def _decode(data: bytes) -> tuple[str, str | None]:
    """(テキスト, 文字コードの知らせの種類)。BOM の後が読めないときは次の手順に進む。"""
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig"), ENCODING_UTF8_BOM
        except UnicodeDecodeError:
            pass
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16"), ENCODING_UTF16
        except UnicodeDecodeError:
            pass
    try:
        return data.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        utf8_error = exc
    try:
        text = data.decode("cp932")
    except UnicodeDecodeError:
        raise utf8_error from None
    if _CP932_UNMAPPED.intersection(text):
        raise utf8_error
    return text, ENCODING_CP932
