"""AI が考えている内容(Thinking の途中の文)をためる(ADR-0169 決定5)。

考えの文は画面の状態にだけ持つ。保存・HTML・CSV・コピー・追加の質問のやり取り・送る文・ログ・例外に入れない。
見えない文字(Unicode の Cc・Cf。改行とタブは残す)を外してから足す。上限は THINKING_MAX_CHARS 文字で、
超えた分は足さずに cut を立てる(画面は cut のとき THINKING_CUT_NOTE を最後に出す)。
"""

import unicodedata

from driftpilot import i18n

THINKING_MAX_CHARS = 20_000
# 切ったときに画面が最後に出す断り書き(日本語・英語。REQ-258)
_TEXTS_JA = {"cut_note": "（長いため、ここから先は出しません）"}
_TEXTS_EN = {"cut_note": "(Too long: the rest is not shown)"}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)
THINKING_CUT_NOTE = _T["cut_note"]

_KEEP = frozenset({"\n", "\t"})
_HIDDEN_CATEGORIES = frozenset({"Cc", "Cf"})


def _visible(text: str) -> str:
    return "".join(ch for ch in text if ch in _KEEP or unicodedata.category(ch) not in _HIDDEN_CATEGORIES)


class ThinkingBuffer:
    """考えの文字をためる。text と cut は読むだけ。repr に文を出さない。"""

    __slots__ = ("_pieces", "_length", "_cut")

    def __init__(self) -> None:
        self._pieces: list[str] = []
        self._length = 0
        self._cut = False

    def add(self, delta: str) -> None:
        """見えない文字を外して足す。上限を超える分は足さず、cut を立てる(立った後は何もしない)。"""
        if self._cut or not isinstance(delta, str) or not delta:
            return
        clean = _visible(delta)
        if not clean:
            return
        room = THINKING_MAX_CHARS - self._length
        if len(clean) > room:
            clean = clean[:room]
            self._cut = True
        if clean:
            self._pieces.append(clean)
            self._length += len(clean)

    @property
    def text(self) -> str:
        if len(self._pieces) > 1:  # 読むたびにつながないよう、つないだものを1つにしておく
            self._pieces = ["".join(self._pieces)]
        return self._pieces[0] if self._pieces else ""

    @property
    def cut(self) -> bool:
        return self._cut

    def __len__(self) -> int:
        return self._length

    def __repr__(self) -> str:  # 文を出さない
        return f"ThinkingBuffer(chars={self._length}, cut={self._cut})"
