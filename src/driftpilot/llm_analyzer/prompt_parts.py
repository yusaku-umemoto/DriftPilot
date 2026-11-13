"""送る文を、直せる指示の部分と、直せない部分に区切る(REQ-266 / ADR-0193 決定4)。

比較の窓の送信前の確認は、送る文を「指示」「差分」「全文」の部分の並びとして見せる。指示の部分だけを、この1回の送信に限って
直せる。差分(伏せた後)と全文(伏せた後)は直せない。部分は元の並びのままで、つなぐと送る文と同じになる。

- PromptParts: 区切った部分の並び。compose は指示の部分だけを置き換えてつなぐ(数が合わなければ ValueError)
- InstructionEdit: 画面から core に渡す「直した指示」。確認に出した文(shown)・直した指示の部分の並び(parts)・画面の側で
  つないだ直した後の文(text)。core は組み立て直した文が shown と同じで、parts でつないだ文が text と同じときだけ送る

文の中身はどの誤りの文にも入れない。
"""

from collections.abc import Sequence
from dataclasses import dataclass

# 部分の種類。直せるのは instruction だけ
INSTRUCTION = "instruction"
DIFF = "diff"
FULL = "full"
READ_ONLY_KINDS = (DIFF, FULL)
_KINDS = (INSTRUCTION, *READ_ONLY_KINDS)


@dataclass(frozen=True)
class PromptParts:
    """送る文を元の並びのまま区切った部分の並び。pieces は (種類, 文)。つなぐと送る文と同じになる。"""

    pieces: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        for piece in self.pieces:
            if not isinstance(piece, tuple) or len(piece) != 2 or piece[0] not in _KINDS or not isinstance(piece[1], str):
                raise ValueError("prompt parts have a wrong shape")

    @property
    def text(self) -> str:
        """部分をつないだ文(送る文と同じ)。"""
        return "".join(text for _kind, text in self.pieces)

    @property
    def instructions(self) -> tuple[str, ...]:
        """指示の部分の文の並び(元の並びのまま)。"""
        return tuple(text for kind, text in self.pieces if kind == INSTRUCTION)

    def compose(self, instructions: Sequence[str]) -> str:
        """指示の部分だけを instructions に置き換えてつないだ文。数が合わない・文字列でないときは ValueError。"""
        if isinstance(instructions, str) or len(instructions) != len(self.instructions):
            raise ValueError("instruction count does not match")
        if any(not isinstance(item, str) for item in instructions):
            raise ValueError("instruction must be a string")
        replacements = iter(instructions)
        return "".join(next(replacements) if kind == INSTRUCTION else text for kind, text in self.pieces)

    @classmethod
    def from_spans(cls, text: str, spans: Sequence[tuple[int, int, str]]) -> "PromptParts":
        """text を、直せない範囲 spans((始まり, 終わり, 種類 DIFF か FULL)。位置の順で重ならない)で区切る。

        範囲の外は指示の部分(空なら作らない)。直せない範囲どうしの間に挟まる文が空白と改行だけ(差分と全文の間の区切りの空行)のときは、
        直せない欄の中に入れる(後ろの範囲を前へ広げる)。文があるとき(利用者のひな形で差分の後ろに書いた指示)は指示の部分にする。
        どちらの範囲も無ければ全体が1つの指示の部分。
        """
        pieces: list[tuple[str, str]] = []
        position = 0
        for start, end, kind in spans:
            if kind not in READ_ONLY_KINDS or not 0 <= start <= end <= len(text) or start < position:
                raise ValueError("prompt span is wrong")
            if start > position:
                gap = text[position:start]
                if pieces and pieces[-1][0] != INSTRUCTION and not gap.strip():
                    start = position  # 直せない範囲の間が空白と改行だけ(区切り)なら、後ろの範囲に入れる
                else:
                    pieces.append((INSTRUCTION, gap))  # 文があれば指示の部分(直せる)
            if end > start:
                pieces.append((kind, text[start:end]))
            position = max(position, end)
        if position < len(text):
            pieces.append((INSTRUCTION, text[position:]))
        return cls(tuple(pieces))


@dataclass(frozen=True)
class InstructionEdit:
    """直した指示(この1回の送信だけ)。shown は確認に出した文、parts は直した指示の部分の並び、text は画面の側でつないだ直した後の文。"""

    shown: str
    parts: tuple[str, ...]
    text: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.shown, str)
            or not isinstance(self.text, str)
            or not isinstance(self.parts, tuple)
            or any(not isinstance(item, str) for item in self.parts)
        ):
            # 文に中身を入れない
            raise ValueError("instruction edit has a wrong shape")
