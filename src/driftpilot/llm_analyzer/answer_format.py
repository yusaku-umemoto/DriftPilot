"""AI の答えを、決まった書き方だけ読み取ってブロックの一覧にする(ADR-0146 決定2・決定4。REQ-204 §2)。

★ ここで HTML を作らない。答えは信用しない入力(比較した設定の説明文などの影響を受ける)なので、画面は
ブロックの文字を textContent で入れる。読めない書き方は文字のまま p に残し、エラーにしない。

| ブロック | 形 | 元の書き方 |
|---|---|---|
| p | {"type": "p", "spans": [...]} | ふつうの行(1行ずつ) |
| heading | {"type": "heading", "spans": [...]} | 行頭の #〜### と空白 |
| list | {"type": "list", "items": [[...], ...]} | 行頭の "- " か "* " が続く行 |
| table | {"type": "table", "head": [[...], ...], "rows": [[[...], ...], ...]} | | で始まる行の次が区切りの行 |
| code | {"type": "code", "text": "..."} | ``` で囲む行(閉じていなければ文字のまま) |

行の中の spans は {"text": str, "bold": bool, "red": bool, "code": bool} の一覧。**語** は太字、<red>語</red> は赤字
(中に ** と ` を入れてよい)、`語` はコード(中は読み取らない)。閉じていない印、<red> 以外のタグ、[文](URL) は文字のまま。

どの処理も正規表現の組み合わせを使わず、行の長さに比例する時間で終わる(長い * の並びなどで遅くならない)。
MAX_FORMAT_CHARS を超える答えは読み取らず、1つの p にする。

driftpilot のほかのモジュールは読まない。画面の側は screen_model を通して使う(gui の境界のテスト)。
"""

import re
from typing import Any

# これより長い答えは読み取らない(1つの p にする)
MAX_FORMAT_CHARS = 200_000

RED_OPEN = "<red>"
RED_CLOSE = "</red>"
_BOLD = "**"
_FENCE = "```"
_HEADING_MAX = 3
_LIST_MARKS = ("- ", "* ")
# 表の区切りの行の1つの欄(短い欄にだけ使う。組み合わせの無い形)
_SEPARATOR_CELL = re.compile(r":?-+:?")
# 行の中で印になりうる文字(1文字の文字クラスなので、探すのは行の長さに比例する)
_SPECIAL = re.compile(r"[*<`]")

# 1回目の走査の区切りの種類
_TEXT = "text"
_CODE = "code"
_BOLD_MARK = "bold"
_RED_OPEN_MARK = "red_open"
_RED_CLOSE_MARK = "red_close"


def plain_answer(text: str) -> str:
    """<red> と </red> だけを外した文字列(CLI の text の出力と wx の画面用。REQ-204 §5)。ほかの印は文字のまま。"""
    return text.replace(RED_OPEN, "").replace(RED_CLOSE, "")


def format_answer(text: str) -> list[dict[str, Any]]:
    """答えの文字列をブロックの一覧にする。空なら []。長すぎるときは読み取らず1つの p にする。"""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > MAX_FORMAT_CHARS:
        return [{"type": "p", "spans": [_span(text)]}]
    if not text.strip():
        return []
    lines = text.split("\n")
    # 閉じの ``` があるかは、最後に現れる ``` の行の位置と比べて決める(先に1回だけ求める)
    last_fence = max((i for i, line in enumerate(lines) if line.startswith(_FENCE)), default=-1)
    blocks: list[dict[str, Any]] = []
    i = 0
    count = len(lines)
    while i < count:
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith(_FENCE) and i < last_fence:
            end = i + 1
            while not lines[end].startswith(_FENCE):
                end += 1
            blocks.append({"type": "code", "text": "\n".join(lines[i + 1 : end])})
            i = end + 1
            continue
        if line.startswith("|") and i + 1 < count and _is_separator(lines[i + 1]):
            head = _cells(line)
            rows = []
            i += 2
            while i < count and lines[i].startswith("|"):
                rows.append(_cells(lines[i]))
                i += 1
            blocks.append({"type": "table", "head": head, "rows": rows})
            continue
        heading = _heading_text(line)
        if heading is not None:
            blocks.append({"type": "heading", "spans": parse_spans(heading)})
            i += 1
            continue
        if line.startswith(_LIST_MARKS):
            items = []
            while i < count and lines[i].startswith(_LIST_MARKS):
                items.append(parse_spans(lines[i][2:]))
                i += 1
            blocks.append({"type": "list", "items": items})
            continue
        blocks.append({"type": "p", "spans": parse_spans(line)})
        i += 1
    return blocks


# 要点(first_sentence)の長さの上限(超えたら上限 - 1 文字と「…」にする)
POINT_MAX_CHARS = 80
_ELLIPSIS = "…"
# 文の終わり。全角は最初の位置で切る。半角は次が空白か文の終わりのときだけ切る(192.0.2.1 や 7.6.8 の点では切らない)
_FULL_STOPS = "。！？"
_HALF_STOPS = ".!?"


def first_sentence(text: str) -> str | None:
    """答えの最初の文(一括比較の窓の「AI の要点」の列。REQ-211 §3)。読めないときは None。

    format_answer のブロックを頭から見て、見出しは飛ばし、最初の p の文か list の最初の項目の文を使う。先に表かコードに
    当たれば None。印(**・<red>・`)は spans の text をつなぐので残らない。POINT_MAX_CHARS を超えたら切って「…」を付ける。
    """
    for block in format_answer(text):
        kind = block["type"]
        if kind == "heading":
            continue
        if kind == "p":
            spans = block["spans"]
        elif kind == "list" and block["items"]:
            spans = block["items"][0]
        else:
            return None  # 表・コード(・項目の無いリスト)
        return _point("".join(span["text"] for span in spans))
    return None


def _point(line: str) -> str | None:
    """1つの文に切り、長ければ縮める。空なら None。1回の走査で文の終わりを探す。"""
    body = line.strip()
    length = len(body)
    for i, char in enumerate(body):
        if char in _FULL_STOPS or (char in _HALF_STOPS and (i + 1 == length or body[i + 1].isspace())):
            body = body[: i + 1]
            break
    body = body.strip()
    if not body:
        return None
    if len(body) > POINT_MAX_CHARS:
        body = body[: POINT_MAX_CHARS - 1] + _ELLIPSIS
    return body


def _heading_text(line: str) -> str | None:
    """行頭が #〜### と空白なら、その後ろの文字。#### 以上や、空白が続かないときは None。"""
    level = 0
    while level < len(line) and line[level] == "#" and level <= _HEADING_MAX:
        level += 1
    if not 1 <= level <= _HEADING_MAX or level >= len(line) or line[level] not in " \t":
        return None
    return line[level:].strip()


def _strip_pipes(line: str) -> str:
    """前後の空白と、両端の | を1つずつ除く。"""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return body


def _is_separator(line: str) -> bool:
    """表の区切りの行(|---|:--:| など)か。"""
    if "-" not in line:
        return False
    cells = _strip_pipes(line).split("|")
    return all(_SEPARATOR_CELL.fullmatch(cell.strip()) is not None for cell in cells)


def _cells(line: str) -> list[list[dict[str, Any]]]:
    """表の1行の欄。両端の | を除いて | で分けるだけ(列の数がそろわなくてもそのまま)。"""
    return [parse_spans(cell.strip()) for cell in _strip_pipes(line).split("|")]


def _span(text: str, *, bold: bool = False, red: bool = False, code: bool = False) -> dict[str, Any]:
    return {"text": text, "bold": bold, "red": red, "code": code}


def _tokens(line: str) -> list[tuple[str, str]]:
    """1回目: 行を「文字」「`…`」「**」「<red>」「</red>」の区切りに分ける。`…` は同じ行に閉じがあるときだけ。"""
    tokens: list[tuple[str, str]] = []
    text: list[str] = []
    i = 0
    length = len(line)
    while i < length:
        found = _SPECIAL.search(line, i)
        if found is None:
            text.append(line[i:])
            break
        start = found.start()
        if start > i:
            text.append(line[i:start])
        i = start
        mark: tuple[str, str] | None = None
        if line.startswith(_BOLD, i):
            mark, step = (_BOLD_MARK, _BOLD), 2
        elif line.startswith(RED_OPEN, i):
            mark, step = (_RED_OPEN_MARK, RED_OPEN), len(RED_OPEN)
        elif line.startswith(RED_CLOSE, i):
            mark, step = (_RED_CLOSE_MARK, RED_CLOSE), len(RED_CLOSE)
        elif line[i] == "`":
            end = line.find("`", i + 1)
            if end > i + 1:
                mark, step = (_CODE, line[i + 1 : end]), end + 1 - i
            elif end == i + 1:
                # 中身の無い `` は文字のまま
                text.append("``")
                i += 2
                continue
            else:
                text.append("`")
                i += 1
                continue
        if mark is None:
            text.append(line[i])
            i += 1
            continue
        if text:
            tokens.append((_TEXT, "".join(text)))
            text = []
        tokens.append(mark)
        i += step
    if text:
        tokens.append((_TEXT, "".join(text)))
    return tokens


def parse_spans(line: str) -> list[dict[str, Any]]:
    """1行の中の印を読み取って spans にする。組にならない印は文字のまま。中身の無い span は出さない。"""
    tokens = _tokens(line)
    # 2回目: ** は前から2つずつ組にする(余った最後の1つは文字)。<red> と </red> も前から組にする
    literal: set[int] = set()
    bold_marks = [i for i, (kind, _) in enumerate(tokens) if kind == _BOLD_MARK]
    if len(bold_marks) % 2 == 1:
        literal.add(bold_marks[-1])
    pending: int | None = None
    for i, (kind, _) in enumerate(tokens):
        if kind == _RED_OPEN_MARK:
            if pending is None:
                pending = i
            else:
                literal.add(i)
        elif kind == _RED_CLOSE_MARK:
            if pending is None:
                literal.add(i)
            else:
                pending = None
    if pending is not None:
        literal.add(pending)

    spans: list[dict[str, Any]] = []
    bold = red = False
    for i, (kind, value) in enumerate(tokens):
        if kind == _CODE:
            spans.append(_span(value, bold=bold, red=red, code=True))
            continue
        if kind == _TEXT or i in literal:
            if not value:
                continue
            last = spans[-1] if spans else None
            if last is not None and not last["code"] and last["bold"] == bold and last["red"] == red:
                last["text"] += value
            else:
                spans.append(_span(value, bold=bold, red=red))
            continue
        if kind == _BOLD_MARK:
            bold = not bold
        elif kind == _RED_OPEN_MARK:
            red = True
        elif kind == _RED_CLOSE_MARK:
            red = False
    return spans
