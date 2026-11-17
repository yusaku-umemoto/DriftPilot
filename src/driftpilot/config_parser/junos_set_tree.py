"""Junos の `show configuration | display set` の形(1 行が根からの経路)を、行ごとの語の並びに読む部品
(ADR-0224 決定 1・3 の追記。REQ-321 §2・REQ-322 §3)。hier_config は使わない。

機種に依らない部品。木を組むのは junos_tree(brace の形と同じ 2 段の文の木)。ここは行を読むだけで、区切りの表は持たない
(REQ-322 で、set の語の列を区切りの表でノードに分ける作りをやめた。`set` の後ろの最初の語が最上位の語、残りの語が文)。

行の読み方(行番号は元の行の 1 始まり。飛ばした行も数える):
- 空行と、最初の空白でない字が `#` の行は飛ばす。`set `・`deactivate `・`protect `・`annotate ` のどれでも始まらない行は、
  種類 `other`(テキストは行全体。写しで未解釈に残る)にする。後ろに語が無い `set`・`deactivate` も同じ
- 後ろを語に分ける。引用符は `"` だけで、引用符の中は 1 語(引用符は語に残す)。`\\"` では閉じない。行の終わりまでに閉じなければ
  JunosSetStructureError("quote")。引用符の外の ` ## …`(行末まで)は落とす。語が `[` か `]` だけなら JunosSetStructureError("list")
- `protect`・`annotate` は木に入れない(語に分けて、閉じない引用符と `[ ]` だけを確かめる)
"""

from driftpilot import i18n

# 組み立ての規則の版。行の読み方を変えたら上げる
TREE_VERSION = "junos-set-tree-v2"

KIND_SET = "set"
KIND_DEACTIVATE = "deactivate"
KIND_OTHER = "other"

_QUOTE = '"'
_COMMENT = "##"


class JunosSetStructureError(ValueError):
    """set の形が崩れている(引用符が閉じない・`[` `]` が残っている)。

    reason は分岐に使える理由(quote・list)、line_no は問題の行。
    文面に差し込むのは行番号だけで、行の中身・値は入れない。文面は理由の鍵から作る(言語で日本語・英語を選ぶ)。
    """

    def __init__(self, reason: str, line_no: int) -> None:
        self.reason = reason
        self.line_no = line_no
        super().__init__(_STRUCTURE["head"].format(line=line_no) + _STRUCTURE[reason].format())


# 形の誤りの文。理由の鍵 → 文の形。head は頭の文
_STRUCTURE_JA = {
    "head": "config の {line} 行目: ",
    "quote": "引用符が行の終わりまでに閉じていません",
    "list": "set の行に [ や ] だけの語があります(set の形では [ ] は 1 つずつの set に展開されているはずです)",
}
_STRUCTURE_EN = {
    "head": "config, line {line}: ",
    "quote": "a quotation mark is not closed by the end of the line",
    "list": "a set line has a word that is only [ or ] (in the set form, [ ] is expanded to one set per element)",
}
_STRUCTURE = i18n.choose(_STRUCTURE_JA, _STRUCTURE_EN)


def _split_words(text: str, line_no: int) -> list[str]:
    """引用符の外の空白で区切った語。引用符は語に残し、引用符の中の空白は変えない。引用符の外の `##` から行末は落とす。"""
    words: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    size = len(text)
    while index < size:
        ch = text[index]
        if quoted:
            current.append(ch)
            if ch == "\\" and index + 1 < size:
                current.append(text[index + 1])
                index += 1
            elif ch == _QUOTE:
                quoted = False
        elif ch.isspace():
            if current:
                words.append("".join(current))
                current = []
        elif not current and text.startswith(_COMMENT, index):
            break
        else:
            current.append(ch)
            if ch == _QUOTE:
                quoted = True
        index += 1
    if quoted:
        raise JunosSetStructureError("quote", line_no)
    if current:
        words.append("".join(current))
    return words


def read_set_lines(text: str) -> list[tuple[int, str, list[str], str]]:
    """set の形の config を行ごとに読む。(行番号, 種類, 語の並び, 行のテキスト)の並びを出た順に返す。木は組まない。

    種類は `set`・`deactivate`・`other`(`set`・`deactivate`・`protect`・`annotate` のどれでも始まらない行と、後ろに語が無い行。
    語の並びは空で、テキストは行全体)。`protect`・`annotate` は返さない。空行と `#` で始まる行は飛ばす。
    """
    found: list[tuple[int, str, list[str], str]] = []
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if parts[0] not in ("set", "deactivate", "protect", "annotate") or len(parts) < 2:
            found.append((line_no, KIND_OTHER, [], line))
            continue
        keyword = parts[0]
        words = _split_words(parts[1], line_no)
        if any(word in ("[", "]") for word in words):
            raise JunosSetStructureError("list", line_no)
        if not words:
            found.append((line_no, KIND_OTHER, [], line))
        elif keyword in (KIND_SET, KIND_DEACTIVATE):
            found.append((line_no, keyword, words, line))
    return found
