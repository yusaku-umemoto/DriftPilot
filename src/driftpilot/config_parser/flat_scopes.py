"""平坦な config(B型)の行頭スコープ表(ADR-0024 決定2〜4)。

行頭の「語 ＋ 添字」がスコープを表し、残りがそのスコープの子の行になる。
表の並びが当てる順(最初に当たったエントリが結果を決める)。表に無い行頭は読まない(未解釈になる)。

- match: 行頭からの正規表現(スコープ部分だけ)。添字は名前付きの組。直後は空白か行末でなければならない
- name:  木でのスコープ名の雛形(str.format)。親があるときは自分の段の名前だけ
- expand: 範囲(`1-4`)と列挙(`1,3`)を展開してよい添字の組名。ここに無い位置は展開しない(値の位置のリストを誤って展開しないため)
- parent: 親のエントリの ID。match は親の添字の組も含む。深さは2段まで
"""

import re
import string
from dataclasses import dataclass
from functools import lru_cache

# 添字の形(ADR-0024 決定2b・決定3)
NUM = r"\d+"
LIST = r"\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*"
WORD = r"[a-z][a-z0-9-]*"

_ID = re.compile(r"FLAT-\d{2}")
MAX_DEPTH = 2


@dataclass(frozen=True)
class FlatScope:
    """行頭のスコープ1つ。example は実データに基づく行の例(値は含めない)。テストが自動でこの行を通す。"""

    id: str
    platform: str
    match: str
    name: str
    example: str
    note: str = ""
    parent: str | None = None
    expand: tuple[str, ...] = ()


@lru_cache(maxsize=None)
def compiled(regex: str) -> re.Pattern[str]:
    """スコープ部分の直後が空白か行末であることを足してコンパイルする(`ether 1 10` を `ether 1 1` と読まない)。"""
    return re.compile(rf"(?:{regex})(?= |$)")


SIR_SCOPES: tuple[FlatScope, ...] = (
    FlatScope(
        "FLAT-01", "fujitsu_sir", rf"ether (?P<unit>{LIST}) (?P<port>{LIST})", "ether {unit} {port}",
        "ether 1 1 use on", expand=("unit", "port"),
        note="添字2つ(ユニット＋ポート)。2つ目の添字を属性として読まない。範囲・列挙はこの2つの位置だけで展開する",
    ),
    FlatScope("FLAT-02", "fujitsu_sir", rf"lan (?P<n>{NUM})", "lan {n}", "lan 0 mtu 1500"),
    # FLAT-04 は FLAT-03 の子。FLAT-03 より先に当てる(先に当てると `ap N` が属性の行になる)
    FlatScope(
        "FLAT-04", "fujitsu_sir", rf"remote (?P<remote>{NUM}) ap (?P<ap>{NUM})", "ap {ap}",
        "remote 0 ap 0 datalink type ipsec", parent="FLAT-03",
        note="FLAT-03 の子。`remote N` の行が無くても親のスコープを暗黙に作る",
    ),
    FlatScope("FLAT-03", "fujitsu_sir", rf"remote (?P<remote>{NUM})", "remote {remote}", "remote 0 mtu 1500"),
    FlatScope("FLAT-05", "fujitsu_sir", rf"bridgegroup (?P<n>{NUM})", "bridgegroup {n}", "bridgegroup 0 ip routing on"),
    FlatScope("FLAT-06", "fujitsu_sir", rf"pseudo-ether (?P<n>{NUM})", "pseudo-ether {n}", "pseudo-ether 1 use off"),
    FlatScope(
        "FLAT-07", "fujitsu_sir", rf"bgp neighbor (?P<n>{NUM})", "bgp neighbor {n}", "bgp neighbor 0 family ipv4",
        note="スコープの語が2語。`bgp as`・`bgp id` は最上位の行として写しの表で読む。ほかの `bgp` の行は未解釈",
    ),
    FlatScope(
        "FLAT-08", "fujitsu_sir", rf"serverinfo (?P<w>{WORD})", "serverinfo {w}", "serverinfo ssh ip on",
        note="添字が語(dns / ssh / ftp など)",
    ),
    FlatScope(
        "FLAT-09", "fujitsu_sir", rf"routemanage (?P<w>{WORD})", "routemanage {w}", "routemanage ip ecmp mode off",
        note="添字が語(ip / ipv6)",
    ),
    FlatScope(
        "FLAT-10", "fujitsu_sir", rf"(?P<kw>wan|etheroam|backup|ethergroup) (?P<n>{NUM})", "{kw} {n}",
        "wan 1 use off", note="語の選択肢4つ(wan / etheroam / backup / ethergroup)。添字1つ",
    ),
)


def _fields(template: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(template) if name}


def validate(scopes: tuple[FlatScope, ...] = SIR_SCOPES) -> None:
    """ID の重複と形、親の存在と順序、深さ、雛形と展開の組名を検査する。"""
    by_id: dict[str, int] = {}
    for index, scope in enumerate(scopes):
        if scope.id in by_id:
            raise ValueError(f"スコープ ID が重複しています: {scope.id}")
        if not _ID.fullmatch(scope.id):
            raise ValueError(f"スコープ ID の形が不正です: {scope.id}")
        by_id[scope.id] = index
        groups = set(compiled(scope.match).groupindex)
        if not _fields(scope.name) <= groups:
            raise ValueError(f"{scope.id}: スコープ名の雛形に、match に無い組があります: {scope.name}")
        if not set(scope.expand) <= groups:
            raise ValueError(f"{scope.id}: expand に、match に無い組があります: {scope.expand}")
    for index, scope in enumerate(scopes):
        if scope.parent is None:
            continue
        if scope.parent not in by_id:
            raise ValueError(f"{scope.id}: 親 {scope.parent} が表にありません")
        parent = scopes[by_id[scope.parent]]
        if parent.parent is not None:
            raise ValueError(f"{scope.id}: 深さが {MAX_DEPTH} 段を超えます")
        if by_id[scope.parent] < index:
            raise ValueError(f"{scope.id}: 親 {scope.parent} より前に置いてください(親が先に当たると子が属性の行になる)")
        if not _fields(parent.name) <= set(compiled(scope.match).groupindex):
            raise ValueError(f"{scope.id}: match に親のスコープ名の組がありません")


validate()
