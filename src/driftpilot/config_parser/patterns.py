"""行の書式をID付きの表にするための型と、表を順に試す共通ループ(ADR-0011)。

各 extractor は PATTERNS(Pattern のタプル)を持ち、行の判定は apply_first だけで行う。
表の並びが優先順位である(最初に match が当たった Pattern が結果を決める)。表に無い書式は読まない。
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


@dataclass(frozen=True)
class Pattern:
    """読む書式1つ。

    id:      "<カテゴリ>.<名前>"。カテゴリは被覆率の内訳に使う
    scope:   この行が現れる場所(親の行の種類)。"top" はトップレベル
    match:   行全体に fullmatch する正規表現。行を読まない派生値(type など)は None
    target:  写し先の IR のキー(人が読む説明)
    example: 解釈される行の例。テストが自動でこの行を通す
    apply:   (Match, ctx) -> bool。IR に写せたら True。手続きを伴うものは関数を指す
    note:    手続きの説明など
    """

    id: str
    scope: str
    match: str | None
    target: str
    example: str
    apply: Callable[[Any, Any], bool]
    note: str = ""

    @property
    def category(self) -> str:
        return self.id.split(".", 1)[0]


@lru_cache(maxsize=None)
def _compiled(regex: str) -> re.Pattern[str]:
    return re.compile(regex)


def apply_first(patterns: Sequence[Pattern], scope: str, text: str, ctx: Any) -> Pattern | None:
    """scope の表を順に試し、最初に match が当たった Pattern の apply を呼ぶ。

    apply が True を返したらその Pattern を返す。False なら後ろの Pattern へは進まず None を返す
    (書式に合って写せなかった行は、別の書式として読み直さない)。どれにも当たらなければ None。
    """
    for pattern in patterns:
        if pattern.scope != scope or pattern.match is None:
            continue
        m = _compiled(pattern.match).fullmatch(text)
        if m is None:
            continue
        return pattern if pattern.apply(m, ctx) else None
    return None
