"""トップレベルの `vlan <id>` 行を IR の vlans に写す extractor。

VLAN 定義として扱うのは `vlan` 行だけで、interface から言及されただけの VLAN は含めない。
中の行は `name`・`trunk group <名前>`・`state <状態>`・`vn-segment <数>`(NX-OS)を読む(ADR-0058、REQ-69)。
見出しが範囲なら、範囲の中のすべての VLAN に同じ値を写す。`trunk group`・`state`・`vn-segment` の行は、
子を持てば読まない(その行も子孫も未解釈)。子を持たない `private-vlan …`・`remote-span`・`mtu <数>`・`media <1語>`・
`shutdown`・`said <数>` と no 付きは options に原文で読む(ADR-0126 決定2、REQ-171)。
`address locking` などのほかの行は未解釈のまま。
字下げの無い `vlan database`(アライドテレシス AW+)の子の行は、`vlan <一覧> name <名前>`・`vlan <一覧> state <語>`・
`vlan <一覧>` を読む(機種の見立てに関係なく。ADR-0206 決定2、REQ-289)。同じ番号が複数の行や `vlan <番号>` の塊に
出たら、項目ごとに後に出た行が勝つ。
"""

import re
from bisect import insort
from dataclasses import dataclass, field

from hier_config import HConfig, HConfigChild
from hier_config.platforms.functions import expand_range

from driftpilot.config_parser.huawei import vlan_list
from driftpilot.config_parser.ir import Vlan, new_vlan
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines


@dataclass
class _Ctx:
    vlans: dict[str, Vlan]
    # `vlan <番号>` の塊(Cisco の形)がすでに定義した ID の文字列。重なる塊は読まない。
    # `vlan database` で作っただけの ID は含めない(重なるときはそのまま読み、項目ごとに後の行が勝つ。ADR-0206 決定2)
    defined: set[str] = field(default_factory=set)
    ids: list[int] = field(default_factory=list)
    # `vlan database` の子の行が読んだ番号と、その行が入れる項目(名前→値)。1行ごとに入れ替える
    db_ids: list[int] = field(default_factory=list)
    db_fields: dict[str, str] = field(default_factory=dict)
    name: str | None = None
    trunk_groups: list[str] = field(default_factory=list)
    state: str | None = None
    vn_segment: int | None = None
    options: list[str] = field(default_factory=list)
    has_children: bool = False
    # HPE Aruba AOS-Switch と見立てた config か(ADR-0228 決定 3、REQ-332)。`name "<名前>"` の引用符を外す
    aos_switch: bool = False
    # Huawei VRP と見立てた config か(ADR-0231 決定 3(2)、REQ-337)。`vlan batch` を読み、`name` は後の行が勝つ
    huawei: bool = False


def _definition(m: re.Match[str], ctx: _Ctx) -> bool:
    """範囲を展開する。展開できない・空・定義済みの ID と重なる行は読まない。"""
    try:
        ids = expand_range(m["range"])
    except ValueError:
        return False
    if not ids or any(str(vlan_id) in ctx.defined for vlan_id in ids):
        return False
    ctx.ids = list(ids)
    return True


def _database(m: re.Match[str], ctx: _Ctx) -> bool:
    """`vlan database` の見出し。子の行を `vlan database` の表で読む。"""
    return True


def _database_ids(range_text: str, ctx: _Ctx) -> bool:
    """子の行の一覧を番号に分ける。読めない・空の一覧の行と、子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    try:
        ids = expand_range(range_text)
    except ValueError:
        return False
    if not ids:
        return False
    ctx.db_ids = list(ids)
    return True


def _database_name(m: re.Match[str], ctx: _Ctx) -> bool:
    """名前は残りの全部(空白は1つにまとめる)。"""
    ctx.db_fields = {"name": " ".join(m["text"].split())}
    return _database_ids(m["range"], ctx)


def _database_state(m: re.Match[str], ctx: _Ctx) -> bool:
    """状態の語は原文のまま。"""
    ctx.db_fields = {"state": m["value"]}
    return _database_ids(m["range"], ctx)


def _database_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    """項目を持たず、番号の VLAN を作るだけ。"""
    ctx.db_fields = {}
    return _database_ids(m["range"], ctx)


def _huawei_batch(m: re.Match[str], ctx: _Ctx) -> bool:
    """Huawei VRP の `vlan batch <並び>`。各 ID の VLAN を、無ければ既定値で作る(あれば何も変えない)。

    見立てていない config・子を持つ行・並びが読めない行は読まない(どの ID も作らない)。作った ID は defined に入れない
    (後の `vlan <N>` の塊が項目を入れる。`vlan database` と同じ扱い。REQ-337)。
    """
    if not ctx.huawei or ctx.has_children:
        return False
    ids = vlan_list(m["list"])
    if ids is None:
        return False
    for vlan_id in ids:
        ctx.vlans.setdefault(str(vlan_id), new_vlan(vlan_id))
    return True


def _name(m: re.Match[str], ctx: _Ctx) -> bool:
    """最初の name だけを読む。二度目以降は上書きせず読まない(Huawei と見立てたときは後の行が勝つ)。"""
    if ctx.name is not None and not ctx.huawei:
        return False
    text = m["text"]
    if ctx.aos_switch and len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1]
    ctx.name = text
    return True


def _trunk_group(m: re.Match[str], ctx: _Ctx) -> bool:
    """名前を trunk_groups に足す。同じ名前は重ねず、昇順の位置に入れる。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    if m["name"] not in ctx.trunk_groups:
        insort(ctx.trunk_groups, m["name"])
    return True


def _state(m: re.Match[str], ctx: _Ctx) -> bool:
    """最初の state だけを原文で読む。二度目以降と子を持つ行は読まない。"""
    if ctx.has_children or ctx.state is not None:
        return False
    ctx.state = m["value"]
    return True


def _vn_segment(m: re.Match[str], ctx: _Ctx) -> bool:
    """最初の vn-segment だけを整数で読む。二度目以降と子を持つ行は読まない。"""
    if ctx.has_children or ctx.vn_segment is not None:
        return False
    ctx.vn_segment = int(m["value"])
    return True


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    """原文(空白は1つ)を options に足す。同じ文字列は重ねず、昇順の位置に入れる。子を持つ行は読まない。"""
    if ctx.has_children:
        return False
    text = " ".join(m.string.split())
    if text not in ctx.options:
        insort(ctx.options, text)
    return True


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vlans.database", "top", r"vlan database", "vlans[id]", "vlan database", _database,
        note="手続き。アライドテレシス AW+ の VLAN の定義の塊の見出し。子の行は次の3つのパターンが読む。"
        "機種の見立てに関係なく読む(ADR-0206 決定2、REQ-289)",
    ),
    Pattern(
        "vlans.database_name", "vlan database", r"vlan (?P<range>\d[\d,\-]*) name (?P<text>.+)",
        "vlans[id].name", "vlan 10 name office", _database_name,
        note="手続き。一覧を展開して全 ID の VLAN を作り、名前(残りの全部。空白は1つ)を入れる。"
        "同じ項目が複数の行に出たら後の行が勝つ。展開できない・空の一覧、子を持つ行は未解釈(ADR-0206 決定2、REQ-289)",
    ),
    Pattern(
        "vlans.database_state", "vlan database", r"vlan (?P<range>\d[\d,\-]*) state (?P<value>\S+)",
        "vlans[id].state", "vlan 10 state enable", _database_state,
        note="手続き。一覧を展開して全 ID の VLAN を作り、状態の語(原文)を入れる。"
        "同じ項目が複数の行に出たら後の行が勝つ。後ろが1語でない行・展開できない・空の一覧、子を持つ行は未解釈"
        "(ADR-0206 決定2、REQ-289)",
    ),
    Pattern(
        "vlans.database_vlan", "vlan database", r"vlan (?P<range>\d[\d,\-]*)", "vlans[id]", "vlan 10",
        _database_vlan,
        note="手続き。一覧を展開して全 ID の VLAN を既定値で作る(すでにあれば何も変えない)。"
        "展開できない・空の一覧、子を持つ行は未解釈(ADR-0206 決定2、REQ-289)",
    ),
    Pattern(
        "vlans.huawei_batch", "top", r"vlan batch (?P<list>.+)", "vlans[id]", "vlan batch 10 20 to 22", _huawei_batch,
        note="手続き。Huawei VRP と見立てた config だけ(ADR-0231 決定 3(2)、REQ-337)。並び(空白で区切った ID と "
        "`<A> to <B>`。ID は 1〜4094、A ≤ B)の各 ID の VLAN を既定値で作る(すでにあれば何も変えない)。"
        "後の `vlan <N>` の塊の項目はその VLAN に入る。2 行以上あれば ID を足し合わせる。"
        "形に合わない語・範囲の外の ID・A > B を含む行・見立てていない config・子を持つ行は、行ごと未解釈",
    ),
    Pattern(
        "vlans.definition", "top", r"vlan (?P<range>\d[\d,\-]*)", "vlans[id]", "vlan 10", _definition,
        note="手続き。範囲(10,20-22)は展開し、全 ID に同じ name を付ける。定義済みの ID と重なる行は読まない",
    ),
    Pattern(
        "vlans.name", "vlan", r"name (?P<text>.+)", "vlans[id].name", "name USERS", _name,
        note="最初の name だけを読む(Huawei VRP と見立てた config だけ、後の行が勝つ。REQ-337)。HPE Aruba AOS-Switch と見立てた config だけ、名前の前後の `\"` を外す"
        "(`name \"users\"` → `users`。ADR-0228 決定 3、REQ-332)",
    ),
    Pattern(
        "vlans.trunk_group", "vlan", r"trunk group (?P<name>\S+)", "vlans[id].trunk_groups",
        "trunk group MLAG-PEER", _trunk_group,
        note="手続き。名前を集合に足す(同じ名前は重ねず昇順。ADR-0058)。範囲の見出しなら全 ID に写す。"
        "後ろが1語でない行・子を持つ行は未解釈",
    ),
    Pattern(
        "vlans.state", "vlan", r"state (?P<value>\S+)", "vlans[id].state", "state suspend", _state,
        note="手続き。最初の行だけを原文で読む(ADR-0058)。2回目・後ろが1語でない行・子を持つ行は未解釈",
    ),
    Pattern(
        "vlans.vn_segment", "vlan", r"vn-segment (?P<value>\d+)", "vlans[id].vn_segment", "vn-segment 10010",
        _vn_segment,
        note="手続き。NX-OS の VLAN と VNI の対応。最初の行だけを整数で読む(ADR-0058)。"
        "2回目・後ろが数でない行・子を持つ行は未解釈",
    ),
    Pattern(
        "vlans.option", "vlan",
        r"(?:no )?(?:private-vlan .+|remote-span|mtu \d+|media \S+|shutdown|said \d+)",
        "vlans[id].options", "private-vlan primary", _option,
        note="手続き。private VLAN・RSPAN などの行の原文(空白は1つ)を集合に足す(同じ文字列は重ねず昇順。"
        "ADR-0126 決定2、REQ-171)。範囲の見出しなら全 ID に写す。子を持つ行・`address locking` などのほかの行は未解釈",
    ),
)


def extract_vlans(
    tree: HConfig, interpreted: InterpretedLines, *, aos_switch: bool = False, huawei: bool = False
) -> dict[str, Vlan]:
    """tree の `vlan` 行から、VLAN ID の文字列をキーにした Vlan の辞書を返す。

    すでに定義されたIDと重なる行は上書きせず、その行と子行を unparsed に残す。
    aos_switch は、HPE Aruba AOS-Switch と見立てた config か(`name "<名前>"` の引用符を外す。ADR-0228 決定 3、REQ-332)。
    huawei は、Huawei VRP と見立てた config か(`vlan batch <並び>` を読み、`name` は後の行が勝つ。ADR-0231 決定 3(2)、REQ-337)。
    """
    vlans: dict[str, Vlan] = {}
    defined: set[str] = set()
    for block in tree.children:
        ctx = _Ctx(vlans, defined, aos_switch=aos_switch, huawei=huawei)
        ctx.has_children = bool(block.children)  # 見出しの判定に使うのは `vlan batch` だけ(子を持てば読まない。REQ-337)
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)

        if heading.id == "vlans.database":
            _read_database(block, ctx, vlans, interpreted)
            continue
        for line in block.children:
            ctx.has_children = bool(line.children)
            if (pattern := apply_first(PATTERNS, "vlan", line.text, ctx)) is not None:
                interpreted.mark(line, pattern.id)
        for vlan_id in ctx.ids:
            # `vlan database` が先に作った VLAN には、この塊が読んだ項目を上書きして入れる(後の行が勝つ)
            vlan = vlans.get(str(vlan_id)) or new_vlan(vlan_id)
            if ctx.name is not None:
                vlan["name"] = ctx.name
            if ctx.state is not None:
                vlan["state"] = ctx.state
            vlan["trunk_groups"] = list(ctx.trunk_groups)
            vlan["vn_segment"] = ctx.vn_segment
            vlan["options"] = list(ctx.options)
            vlans[str(vlan_id)] = vlan
            defined.add(str(vlan_id))
    return vlans


def _read_database(block: HConfigChild, ctx: _Ctx, vlans: dict[str, Vlan], interpreted: InterpretedLines) -> None:
    """`vlan database` の子の行を読み、番号ごとに VLAN を取り出すか作って項目を入れる(後の行が勝つ)。"""
    for line in block.children:
        ctx.has_children = bool(line.children)
        if (pattern := apply_first(PATTERNS, "vlan database", line.text, ctx)) is None:
            continue
        interpreted.mark(line, pattern.id)
        for vlan_id in ctx.db_ids:
            vlan = vlans.get(str(vlan_id)) or new_vlan(vlan_id)
            for key, value in ctx.db_fields.items():
                vlan[key] = value  # type: ignore[literal-required]
            vlans[str(vlan_id)] = vlan
