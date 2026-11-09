"""未解釈行を「形」に畳み込む規則 fold-v1(ADR-0011 決定5、REQ-10)。

形は「その行そのもの」から計算し、親の行は含めない。親は scope_label が粗いラベルとして別に出す。
規則を変えたら FOLD_VERSION を上げる(版の違うレポートは頻度ランキングを比べられないため)。
"""

import re

FOLD_VERSION = "fold-v1"

_IPV4 = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}(?:/\d{1,2})?")
_MAC = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}\.[0-9A-Fa-f]{4}")
_RANGE = re.compile(r"[0-9,\-]+")
_CISCO_HASH = re.compile(r"\$\d+\$.*")
_HEX = re.compile(r"[0-9A-Fa-f]{20,}")
_BASE64 = re.compile(r"[A-Za-z0-9+/=]{20,}")
# GigabitEthernet1/0/10 のような名前。英字 + 数字 +(/数字)*+(.数字)?
_INTERFACE_NAME = re.compile(r"[A-Za-z]+\d+(?:/\d+)*(?:\.\d+)?")
_DIGITS = re.compile(r"\d+")


def fold_word(word: str) -> str:
    """1語を畳み込む。最初に当たった規則を適用する。"""
    if _IPV4.fullmatch(word):
        return "<ipv4>"
    if _MAC.fullmatch(word):
        return "<mac>"
    if _RANGE.fullmatch(word) and ("," in word or "-" in word):
        return "<range>"
    if _CISCO_HASH.fullmatch(word):
        return "<hash>"
    if _HEX.fullmatch(word):
        return "<hash>"
    if (
        _BASE64.fullmatch(word)
        and any(c.isdigit() for c in word)
        and any(c.isupper() for c in word)
        and any(c.islower() for c in word)
        and not _INTERFACE_NAME.fullmatch(word)
    ):
        return "<hash>"
    if _DIGITS.fullmatch(word):
        return "<num>"
    return word


def fold_line(text: str) -> str:
    """行の形を返す。語は空白で区切り、1つの空白でつなぎ直す。"""
    return " ".join(fold_word(word) for word in text.split())


def scope_label(path: tuple[str, ...]) -> str:
    """未解釈行の粗いスコープ。トップレベルは "top"、子行はルートの行から付ける。

    ルートが `router <proto>` なら "router ospf" のように2語、それ以外は先頭の1語(interface, line, vlan など)。
    """
    if len(path) <= 1:
        return "top"
    words = path[0].split()
    if not words:
        return "top"
    if words[0] == "router" and len(words) > 1:
        return f"router {words[1]}"
    return words[0]
