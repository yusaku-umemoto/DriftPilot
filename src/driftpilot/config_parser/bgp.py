"""トップレベルの `router bgp <AS>` を IR の bgp に写す extractor(ADR-0038 第1段 REQ-46、ADR-0045 第2段 REQ-56、
ADR-0046 第3段 REQ-57)。

第1段: `router bgp` の直下の router-id と `neighbor <鍵> …`、NX-OS の `neighbor <アドレス>` のブロックの子。
第2段: 直下のほかの子を持たない行(options)、`address-family` のブロック(address_families)、
`vrf <名前>` のブロック(vrfs)、NX-OS の相手のブロックの中の `address-family`。
Cisco の `address-family … vrf <名前>` は vrfs[<名前>].address_families に置く。
第3段: 直下の EVPN の `vlan <1語>`・`vlan-aware-bundle <1語>`・`vpws <1語>` のブロック(evpn_instances。鍵は見出しの原文)。
その中の子を持つ行(`pseudowire`・`comment`)と NX-OS の `template` などの子を持つ行は、見出しも中も未解釈。
パスワードの値は IR に入れない(相手は password_configured だけ。決定6)。`password` の語を含む行は options などに入れない。
ピアグループから受け継ぐ設定は解かない(決定7)。
"""

import re
from bisect import insort
from dataclasses import dataclass, replace
from ipaddress import AddressValueError, IPv4Address, ip_address

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.ir import (
    Bgp,
    BgpAddressFamily,
    BgpAfNeighbor,
    BgpEvpnInstance,
    BgpNeighbor,
    BgpNeighborKind,
    BgpVrf,
    new_bgp,
    new_bgp_address_family,
    new_bgp_af_neighbor,
    new_bgp_evpn_instance,
    new_bgp_neighbor,
    new_bgp_vrf,
)
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_MAX_AS = 4294967295
_MAX_AS_HALF = 65535

# 固定キーに写す属性の名前。これで始まるのに形が合わない行は options に入れず未解釈にする(読み違いを隠さない)
_FIXED_ATTRIBUTES = ("remote-as", "peer-group", "peer group", "description", "update-source", "shutdown")
# options に入れない行の先頭。password はどんな形でも入れない。NX-OS のブロックの子の address-family と no … は読まない
_NOT_OPTIONS = ("password", "address-family", "no ")

# 1行で書く相手の行の先頭。`/` を含む鍵(NX-OS の範囲での相手)は、この依頼では読まない
_NEIGHBOR = r"neighbor (?P<key>[^\s/]+) "

# 第2段の options に入れない行の頭の語(第1段の書式と、今の項目と同じ設定の形。書式に合わなかった行を隠さない)
_FIRST_OWNED = ("neighbor", "no neighbor", "router-id", "bgp router-id", "vrf", "address-family")
# 直下では第3段の見出しの頭も(書式に合わなかった見出し・子を持たない見出しを options に入れない。REQ-57)
_TOP_OWNED = (*_FIRST_OWNED, "vlan", "vlan-aware-bundle", "vpws")
_VRF_OWNED = (*_FIRST_OWNED, "rd", "route-target")
_AF_OWNED = ("neighbor", "no neighbor", "address-family")
_EVPN_OWNED = ("rd", "route-target")

_SCOPE_TOP = "router bgp"
_SCOPE_NEIGHBOR = "router bgp neighbor"
_SCOPE_VRF = "router bgp vrf"
_SCOPE_AF = "router bgp af"
_SCOPE_NEIGHBOR_AF = "router bgp neighbor af"
_SCOPE_EVPN = "router bgp evpn"


@dataclass
class _Ctx:
    bgp: Bgp | None = None
    headings: int = 0
    # 今読んでいる行(子があるかを見る)
    node: HConfigChild | None = None
    # 今読んでいる NX-OS の相手のブロックの鍵(正規の書き方)
    block_key: str | None = None
    # 第1段の行を書き込む先。直下では bgp、vrf の中ではその VRF(router_id・neighbors・address_families を同じ名前で持つ)
    target: Bgp | BgpVrf | None = None
    # 今読んでいる VRF・address-family・相手のブロックの中の address-family の相手
    vrf: BgpVrf | None = None
    af: BgpAddressFamily | None = None
    af_neighbor: BgpAfNeighbor | None = None
    # 今読んでいる EVPN の vlan・vlan-aware-bundle・vpws のブロック
    evpn: BgpEvpnInstance | None = None


def _asn(text: str) -> int | None:
    """AS 番号を int にする。asdot(`<上位>.<下位>`)は `上位 * 65536 + 下位`(決定5)。読めなければ None。"""
    if text.isascii() and text.isdecimal():
        value = int(text)
        return value if value <= _MAX_AS else None
    high, dot, low = text.partition(".")
    if not (dot and high.isascii() and high.isdecimal() and low.isascii() and low.isdecimal()):
        return None
    if int(high) > _MAX_AS_HALF or int(low) > _MAX_AS_HALF:
        return None
    return int(high) * 65536 + int(low)


def _key(word: str) -> tuple[str, BgpNeighborKind]:
    """相手の鍵と種類。アドレスとして読めれば正規の書き方、読めなければピアグループ名(語のまま)。"""
    try:
        return str(ip_address(word)), "address"
    except ValueError:
        return word, "peer_group"


def _target(m: re.Match[str], ctx: _Ctx) -> tuple[str, BgpNeighborKind]:
    """1行の形は行の鍵、NX-OS のブロックの子はブロックの鍵。"""
    if "key" in m.re.groupindex:
        return _key(m["key"])
    return ctx.block_key, "address"


def _neighbor(ctx: _Ctx, key: str, kind: BgpNeighborKind) -> BgpNeighbor:
    """相手を返す。無ければ作る(行が解釈できたときにだけ呼ぶ)。"""
    neighbors = ctx.target["neighbors"]
    if key not in neighbors:
        neighbors[key] = new_bgp_neighbor(kind)
    return neighbors[key]


def _process(m: re.Match[str], ctx: _Ctx) -> bool:
    """bgp を作る。2つ目の見出しと、AS 番号として読めない見出しは読まない(その行と子は未解釈)。"""
    ctx.headings += 1
    local_as = _asn(m["asn"])
    if ctx.headings > 1 or local_as is None:
        return False
    ctx.bgp = new_bgp(local_as)
    return True


def _router_id(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        ctx.target["router_id"] = str(IPv4Address(m["addr"]))
    except AddressValueError:
        return False
    return True


def _neighbor_block(m: re.Match[str], ctx: _Ctx) -> bool:
    """NX-OS の相手のブロック。鍵がアドレスで、子の行を持つときだけ読む。"""
    key, kind = _key(m["key"])
    if kind != "address" or not ctx.node.children:
        return False
    _neighbor(ctx, key, kind)
    ctx.block_key = key
    return True


def _remote_as(m: re.Match[str], ctx: _Ctx) -> bool:
    remote_as = _asn(m["asn"])
    if remote_as is None:
        return False
    _neighbor(ctx, *_target(m, ctx))["remote_as"] = remote_as
    return True


def _peer_group_definition(m: re.Match[str], ctx: _Ctx) -> bool:
    """ピアグループの定義。アドレスはピアグループになれないので、鍵がアドレスなら読まない。"""
    key, kind = _target(m, ctx)
    if kind != "peer_group":
        return False
    _neighbor(ctx, key, kind)
    return True


def _peer_group(m: re.Match[str], ctx: _Ctx) -> bool:
    """ピアグループへの所属。鍵がピアグループなら読まない。"""
    key, kind = _target(m, ctx)
    if kind != "address":
        return False
    _neighbor(ctx, key, kind)["peer_group"] = m["name"]
    return True


def _description(m: re.Match[str], ctx: _Ctx) -> bool:
    _neighbor(ctx, *_target(m, ctx))["description"] = m["text"]
    return True


def _update_source(m: re.Match[str], ctx: _Ctx) -> bool:
    _neighbor(ctx, *_target(m, ctx))["update_source"] = m["value"]
    return True


def _shutdown(m: re.Match[str], ctx: _Ctx) -> bool:
    _neighbor(ctx, *_target(m, ctx))["shutdown"] = True
    return True


def _password(m: re.Match[str], ctx: _Ctx) -> bool:
    """値は IR のどこにも入れず、password_configured を True にするだけ。行は解釈済みにする。"""
    _neighbor(ctx, *_target(m, ctx))["password_configured"] = True
    return True


def _options(m: re.Match[str], ctx: _Ctx) -> bool:
    """固定キーに無い行の残りを、空白を1つにそろえて options に足す(同じ文字列は重ねない)。"""
    rest = " ".join(m["rest"].split())
    if rest.startswith(_NOT_OPTIONS) or any(rest == a or rest.startswith(a + " ") for a in _FIXED_ATTRIBUTES):
        return False
    options = _neighbor(ctx, *_target(m, ctx))["options"]
    if rest not in options:
        options.append(rest)
    return True


# --- 第2段 ---


def _add(values: list[str], text: str) -> None:
    """空白を1つにそろえて、文字列の昇順の位置に足す(同じ文字列は重ねない)。"""
    text = " ".join(text.split())
    if text not in values:
        insort(values, text)


def _has_password(text: str) -> bool:
    return any(word.lower() == "password" for word in text.split())


def _starts_with(text: str, heads: tuple[str, ...]) -> bool:
    words = text.split()
    return any(words[: len(head.split())] == head.split() for head in heads)


def _plain(ctx: _Ctx, text: str, owned: tuple[str, ...] = ()) -> bool:
    """options などの原文の集合に入れてよい行か。子を持つ行・password の語を含む行・owned で始まる行は入れない。"""
    return not (ctx.node.children or _has_password(text) or _starts_with(text, owned))


def _family(text: str) -> str | None:
    """address-family の種類(語を空白1つでつないだ原文)。`vrf` の語が残る見出しは読まない。"""
    words = text.split()
    return None if "vrf" in words else " ".join(words)


def _vrf_of(ctx: _Ctx, name: str) -> BgpVrf:
    """vrfs[<名前>] を返す。無ければ既定値で作る(同じ名前の見出しが2回あれば同じ項目)。"""
    vrfs = ctx.bgp["vrfs"]
    if name not in vrfs:
        vrfs[name] = new_bgp_vrf()
    return vrfs[name]


def _option(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m["line"], _TOP_OWNED):
        return False
    _add(ctx.bgp["options"], m["line"])
    return True


def _vrf(m: re.Match[str], ctx: _Ctx) -> bool:
    """vrfs[<名前>] を作り、子の行をその VRF に読む。子の行が無くても既定値の項目を作る。"""
    if _has_password(m.string):
        return False
    ctx.vrf = _vrf_of(ctx, m["name"])
    ctx.target = ctx.vrf
    return True


def _address_family(m: re.Match[str], ctx: _Ctx) -> bool:
    """address_families[<種類>] を作り、子の行をそこへ読む。子の行が無くても空の項目を作る。

    Cisco の `… vrf <名前>` は、その VRF(無ければ既定値で作る)の address_families に置く。
    """
    family = _family(m["family"])
    if family is None or _has_password(m.string):
        return False
    owner = _vrf_of(ctx, m["vrf"]) if "vrf" in m.re.groupindex else ctx.target
    families = owner["address_families"]
    if family not in families:
        families[family] = new_bgp_address_family()
    ctx.af = families[family]
    return True


def _add_af_neighbor_option(m: re.Match[str], ctx: _Ctx, prefix: str) -> bool:
    """neighbors[<鍵>].options に `<prefix><残り>` を足す。

    残りが password で始まる行と、鍵も含めた行全体のどの語かが password の行は読まない(値を IR に入れない)。
    """
    rest = " ".join(m["rest"].split())
    if ctx.node.children or rest.lower().startswith("password") or _has_password(m.string):
        return False
    key, _ = _key(m["key"])
    neighbors = ctx.af["neighbors"]
    if key not in neighbors:
        neighbors[key] = new_bgp_af_neighbor()
    _add(neighbors[key]["options"], prefix + rest)
    return True


def _af_neighbor(m: re.Match[str], ctx: _Ctx) -> bool:
    return _add_af_neighbor_option(m, ctx, "")


def _af_no_neighbor(m: re.Match[str], ctx: _Ctx) -> bool:
    return _add_af_neighbor_option(m, ctx, "no ")


def _af_option(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m["line"], _AF_OWNED):
        return False
    _add(ctx.af["options"], m["line"])
    return True


def _neighbor_block_af(m: re.Match[str], ctx: _Ctx) -> bool:
    """NX-OS の相手のブロックの中の address-family。address_families[<種類>].neighbors[<相手の鍵>] を作る。

    置く先は、相手のブロックが直下なら bgp、vrf の中ならその VRF。中の行が無くても作る。
    """
    family = _family(m["family"])
    if family is None or _has_password(m.string):
        return False
    families = ctx.target["address_families"]
    if family not in families:
        families[family] = new_bgp_address_family()
    neighbors = families[family]["neighbors"]
    if ctx.block_key not in neighbors:
        neighbors[ctx.block_key] = new_bgp_af_neighbor()
    ctx.af_neighbor = neighbors[ctx.block_key]
    return True


def _neighbor_block_af_option(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m["line"]):
        return False
    _add(ctx.af_neighbor["options"], m["line"])
    return True


def _vrf_rd(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m.string):
        return False
    ctx.vrf["rd"] = m["value"]
    return True


def _vrf_route_target(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m.string):
        return False
    _add(ctx.vrf["route_targets"], m["rest"])
    return True


def _vrf_option(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m["line"], _VRF_OWNED):
        return False
    _add(ctx.vrf["options"], m["line"])
    return True


def _vrf_rd_other(m: re.Match[str], ctx: _Ctx) -> bool:
    """`rd` の後ろが2語以上の行(Arista の `rd evpn domain all <値>` など)は、形の違う書き方として options に。"""
    if not _plain(ctx, m.string):
        return False
    _add(ctx.vrf["options"], m.string)
    return True


# --- 第3段 ---


def _evpn_instance(m: re.Match[str], ctx: _Ctx) -> bool:
    """evpn_instances[<見出しの原文>] を作り、子の行をそこへ読む。子を持たない見出しは読まない。

    同じ見出しが2回あれば同じ項目にまとめる。
    """
    if not ctx.node.children or _has_password(m.string):
        return False
    key = " ".join(m.string.split())
    instances = ctx.bgp["evpn_instances"]
    if key not in instances:
        instances[key] = new_bgp_evpn_instance()
    ctx.evpn = instances[key]
    return True


def _evpn_rd(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m.string):
        return False
    ctx.evpn["rd"] = m["value"]
    return True


def _evpn_rd_other(m: re.Match[str], ctx: _Ctx) -> bool:
    """`rd` の後ろが2語以上の行(`rd evpn domain remote <値>` など)は、形の違う書き方として options に。"""
    if not _plain(ctx, m.string):
        return False
    _add(ctx.evpn["options"], m.string)
    return True


def _evpn_route_target(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m.string):
        return False
    _add(ctx.evpn["route_targets"], m["rest"])
    return True


def _evpn_option(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(ctx, m["line"], _EVPN_OWNED):
        return False
    _add(ctx.evpn["options"], m["line"])
    return True


_NOTE_STAGE = (
    "第1段(直下の router-id・相手と NX-OS の相手のブロックの子)、第2段(直下のほかの行・address-family・vrf)、"
    "第3段(直下の EVPN の vlan・vlan-aware-bundle・vpws)を読む。"
    "EVPN のブロックの中の子を持つ行(pseudowire・comment)と template などの子を持つ行は、見出しも中も未解釈"
)
_NOTE_PASSWORD = (
    "手続き。値は IR のどこにも持たず、password_configured を True にするだけ(ADR-0038 決定6)。"
    "行は解釈済みにする。どんな形でも options に入れない"
)
_NOTE_OPTIONS = (
    "手続き。固定キーに無い行の残り(鍵の後ろ。空白は1つにそろえる)を options に足す(ADR-0038 決定4)。"
    "同じ文字列は重ねない。固定キーの属性名(remote-as / peer-group / peer group / description / update-source / "
    "shutdown)で始まるのに形が合わない行、password・address-family・no で始まる行は入れず未解釈にする"
)
_NOTE_REMOTE_AS = "AS 番号は整数、asdot(上位.下位)は 上位*65536+下位。読めない値(internal など)は未解釈にする"
_NOTE_SET = "空白は1つにそろえ、同じ文字列は重ねず、文字列の昇順に入れる(比較では集合)"
_NOTE_NO_PASSWORD = (
    "子を持つ行と、行全体(鍵や見出しの語も含む)の語のどれかが password の行は入れず未解釈にする(値を IR に入れない)"
)
_NOTE_AF_HEADING = (
    "手続き。種類は address-family の後ろの語を空白1つでつないだ原文(そろえない)。同じ種類の見出しは1つにまとめ、"
    "子の行が無くても空の項目を作る。`vrf` の語が残る見出しと、語のどれかが password の見出しは未解釈(中も未解釈)"
)

# 並び＝優先順位。options は残りすべてに当たるので、各 scope の最後に置く。
_FIRST_STAGE: tuple[Pattern, ...] = (
    Pattern(
        "bgp.router_id", _SCOPE_TOP, r"(?:bgp )?router-id (?P<addr>\S+)", "router_id", "bgp router-id 192.0.2.1",
        _router_id, note="Cisco の bgp router-id と Arista・NX-OS の router-id。IPv4 として読めない値は未解釈にする",
    ),
    Pattern(
        "bgp.neighbor_block", _SCOPE_TOP, r"neighbor (?P<key>[^\s/]+)", "neighbors", "neighbor 192.0.2.2",
        _neighbor_block,
        note="手続き。NX-OS の相手のブロック。鍵がアドレスで子の行を持つときだけ読み、子を相手の属性として読む"
        "(子の address-family は bgp.neighbor_block.af で読む)",
    ),
    Pattern(
        "bgp.neighbor.remote_as", _SCOPE_TOP, _NEIGHBOR + r"remote-as (?P<asn>\S+)",
        "neighbors[].remote_as", "neighbor 192.0.2.3 remote-as 65002", _remote_as, note=_NOTE_REMOTE_AS,
    ),
    Pattern(
        "bgp.neighbor.peer_group_definition", _SCOPE_TOP, _NEIGHBOR + r"peer[- ]group",
        "neighbors[].kind = peer_group", "neighbor SPINE peer group", _peer_group_definition,
        note="手続き。古い書き方 peer-group も同じ。鍵がアドレスなら未解釈(アドレスはピアグループになれない)",
    ),
    Pattern(
        "bgp.neighbor.peer_group", _SCOPE_TOP, _NEIGHBOR + r"peer[- ]group (?P<name>\S+)",
        "neighbors[].peer_group", "neighbor 192.0.2.2 peer group SPINE", _peer_group,
        note="手続き。ピアグループへの所属。鍵がピアグループなら未解釈。ピアグループの設定は受け継がせない",
    ),
    Pattern(
        "bgp.neighbor.description", _SCOPE_TOP, _NEIGHBOR + r"description (?P<text>.+)",
        "neighbors[].description", "neighbor 192.0.2.3 description to-core", _description,
    ),
    Pattern(
        "bgp.neighbor.update_source", _SCOPE_TOP, _NEIGHBOR + r"update-source (?P<value>\S+)",
        "neighbors[].update_source", "neighbor SPINE update-source Loopback0", _update_source,
    ),
    Pattern(
        "bgp.neighbor.shutdown", _SCOPE_TOP, _NEIGHBOR + r"shutdown", "neighbors[].shutdown",
        "neighbor 192.0.2.3 shutdown", _shutdown,
    ),
    Pattern(
        "bgp.neighbor.password", _SCOPE_TOP, _NEIGHBOR + r"password .+", "neighbors[].password_configured",
        "neighbor 192.0.2.3 password 7 <値>", _password, note=_NOTE_PASSWORD,
    ),
    Pattern(
        "bgp.neighbor.options", _SCOPE_TOP, _NEIGHBOR + r"(?P<rest>.+)", "neighbors[].options",
        "neighbor 192.0.2.3 send-community both", _options, note=_NOTE_OPTIONS,
    ),
)


def _in_vrf(pattern: Pattern) -> Pattern:
    """第1段の直下のパターンを、vrf の中で同じ処理関数で読む写しにする(ID は bgp.vrf.<元の名前>)。"""
    return replace(pattern, id="bgp.vrf." + pattern.id.removeprefix("bgp."), scope=_SCOPE_VRF)


PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "bgp.process", "top", r"router bgp (?P<asn>\S+)", "bgp", "router bgp 65000", _process,
        note="手続き。2つ目の router bgp は読まない(その行と子は未解釈)。AS 番号として読めない見出しはブロックごと読まない。"
        + _NOTE_STAGE,
    ),
    *_FIRST_STAGE,
    # NX-OS の相手のブロックの子。上の1行の形から鍵を除いた書式で、同じ処理関数で写す。NX-OS にピアグループは無い
    Pattern(
        "bgp.neighbor_block.remote_as", _SCOPE_NEIGHBOR, r"remote-as (?P<asn>\S+)",
        "neighbors[].remote_as", "remote-as 65001", _remote_as, note=_NOTE_REMOTE_AS,
    ),
    Pattern(
        "bgp.neighbor_block.description", _SCOPE_NEIGHBOR, r"description (?P<text>.+)",
        "neighbors[].description", "description to-spine", _description,
    ),
    Pattern(
        "bgp.neighbor_block.update_source", _SCOPE_NEIGHBOR, r"update-source (?P<value>\S+)",
        "neighbors[].update_source", "update-source loopback0", _update_source,
    ),
    Pattern(
        "bgp.neighbor_block.shutdown", _SCOPE_NEIGHBOR, r"shutdown", "neighbors[].shutdown",
        "shutdown", _shutdown,
    ),
    Pattern(
        "bgp.neighbor_block.password", _SCOPE_NEIGHBOR, r"password .+", "neighbors[].password_configured",
        "password 3 <値>", _password, note=_NOTE_PASSWORD,
    ),
    Pattern(
        "bgp.neighbor_block.af", _SCOPE_NEIGHBOR, r"address-family (?P<family>.+)",
        "address_families[].neighbors[<相手>]", "address-family ipv4 unicast", _neighbor_block_af,
        note="手続き。NX-OS の相手のブロックの中の address-family(REQ-56 §3-4)。種類は語を空白1つでつないだ原文。"
        "相手のブロックが直下なら bgp の、vrf の中ならその VRF の address_families[<種類>].neighbors[<相手の鍵>] を、"
        "子の行が無くても作る。`vrf` の語か password の語を含む見出しは未解釈",
    ),
    Pattern(
        "bgp.neighbor_block.options", _SCOPE_NEIGHBOR, r"(?P<rest>.+)", "neighbors[].options",
        "bfd", _options, note=_NOTE_OPTIONS,
    ),
    # 第2段: 直下のほかの行と、address-family・vrf のブロック
    Pattern(
        "bgp.vrf", _SCOPE_TOP, r"vrf (?P<name>\S+)", "vrfs[]", "vrf TENANT", _vrf,
        note="手続き。vrfs[<名前>] を作り、子の行をその VRF に読む(vrfs カテゴリには入れない。ADR-0045)。"
        "同じ名前の見出しは1つにまとめ、子の行が無くても既定値の項目を作る。語のどれかが password の見出しは未解釈(中も未解釈)",
    ),
    Pattern(
        "bgp.af_vrf", _SCOPE_TOP, r"address-family (?P<family>.+) vrf (?P<vrf>\S+)",
        "vrfs[].address_families[]", "address-family ipv4 vrf RED", _address_family,
        note="手続き。Cisco IOS の address-family … vrf <名前> は、vrf <名前> を外した残りを種類にし、"
        "vrfs[<名前>].address_families[<種類>] に置く(VRF が無ければ既定値で作る。ADR-0045 決定2)。" + _NOTE_AF_HEADING,
    ),
    Pattern(
        "bgp.af", _SCOPE_TOP, r"address-family (?P<family>.+)", "address_families[]", "address-family evpn",
        _address_family, note=_NOTE_AF_HEADING,
    ),
    # 第3段(REQ-57): 直下の EVPN のブロック
    Pattern(
        "bgp.evpn_instance", _SCOPE_TOP, r"(?:vlan|vlan-aware-bundle|vpws) \S+", "evpn_instances[]", "vlan 10",
        _evpn_instance,
        note="手続き。evpn_instances[<見出しの原文(空白は1つ)>] を作り、子の行をそこへ読む(ADR-0046 決定1)。"
        "vlan 10 と vlan-aware-bundle 10 は別の鍵。同じ見出しは1つにまとめる。子を持たない見出し・後ろが2語以上の見出し・"
        "語のどれかが password の見出しは未解釈(中も未解釈)。router bgp の外の vlan(VLAN の定義)は vlans カテゴリが読む",
    ),
    Pattern(
        "bgp.option", _SCOPE_TOP, r"(?P<line>.+)", "options", "bgp log-neighbor-changes", _option,
        note="手続き。直下のほかの子を持たない行の原文を options に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD
        + "。第1段の書式(neighbor・router-id・bgp router-id)と no neighbor、vrf・address-family・vlan・"
        "vlan-aware-bundle・vpws で始まる行も入れない。template などの子を持つ行は見出しも中も未解釈",
    ),
    # address-family の中(直下・vrf の中・Cisco の … vrf <名前> で共通)
    Pattern(
        "bgp.af.no_neighbor", _SCOPE_AF, r"no neighbor (?P<key>[^\s/]+) (?P<rest>.+)",
        "address_families[].neighbors[].options", "no neighbor SPINE activate", _af_no_neighbor,
        note="手続き。neighbors[<鍵>].options に `no <残り>` を足す。鍵は第1段と同じ(アドレスは正規の書き方)。"
        + _NOTE_SET + "。残りが password で始まる行と、" + _NOTE_NO_PASSWORD,
    ),
    Pattern(
        "bgp.af.neighbor", _SCOPE_AF, r"neighbor (?P<key>[^\s/]+) (?P<rest>.+)",
        "address_families[].neighbors[].options", "neighbor SPINE activate", _af_neighbor,
        note="手続き。neighbors[<鍵>].options に `<残り>` を足す。鍵は第1段と同じ(アドレスは正規の書き方。"
        "`/` を含む鍵は読まない)。" + _NOTE_SET + "。残りが password で始まる行と、" + _NOTE_NO_PASSWORD,
    ),
    Pattern(
        "bgp.af.option", _SCOPE_AF, r"(?P<line>.+)", "address_families[].options", "network 192.0.2.0/24",
        _af_option,
        note="手続き。address-family の中のほかの行の原文を options に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD
        + "。書式に合わなかった neighbor・no neighbor の行(neighbor <鍵> だけの行など)も入れない",
    ),
    # NX-OS の相手のブロックの中の address-family の中
    Pattern(
        "bgp.neighbor_block.af.option", _SCOPE_NEIGHBOR_AF, r"(?P<line>.+)",
        "address_families[].neighbors[<相手>].options", "send-community", _neighbor_block_af_option,
        note="手続き。行の原文をその相手の options に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD,
    ),
    # vrf の中
    Pattern("bgp.vrf.rd", _SCOPE_VRF, r"rd (?P<value>\S+)", "vrfs[].rd", "rd 192.0.2.1:10", _vrf_rd,
            note="手続き。原文。2回あれば後の行。" + _NOTE_NO_PASSWORD),
    Pattern(
        "bgp.vrf.rd_other", _SCOPE_VRF, r"rd \S+(?: \S+)+", "vrfs[].options", "rd evpn domain all 192.0.2.1:10",
        _vrf_rd_other,
        note="手続き。rd の後ろが2語以上の行(Arista の rd evpn domain all <値> など)は、形の違う書き方として"
        "行の原文を options に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD,
    ),
    Pattern(
        "bgp.vrf.route_target", _SCOPE_VRF, r"route-target (?P<rest>.+)", "vrfs[].route_targets",
        "route-target import evpn 10:10", _vrf_route_target,
        note="手続き。route-target の後ろの原文を route_targets に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD,
    ),
    *(_in_vrf(p) for p in _FIRST_STAGE),
    Pattern(
        "bgp.vrf.af", _SCOPE_VRF, r"address-family (?P<family>.+)", "vrfs[].address_families[]",
        "address-family ipv4 unicast", _address_family, note=_NOTE_AF_HEADING,
    ),
    Pattern(
        "bgp.vrf.option", _SCOPE_VRF, r"(?P<line>.+)", "vrfs[].options", "redistribute connected", _vrf_option,
        note="手続き。vrf の中のほかの子を持たない行の原文を options に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD
        + "。第1段の書式と no neighbor、vrf・address-family・rd・route-target で始まる行(書式に合わなかったもの)も入れない",
    ),
    # EVPN の vlan・vlan-aware-bundle・vpws の中
    Pattern("bgp.evpn_rd", _SCOPE_EVPN, r"rd (?P<value>\S+)", "evpn_instances[].rd", "rd 192.0.2.1:10010",
            _evpn_rd, note="手続き。原文。2回あれば後の行。" + _NOTE_NO_PASSWORD),
    Pattern(
        "bgp.evpn_rd_other", _SCOPE_EVPN, r"rd \S+(?: \S+)+", "evpn_instances[].options",
        "rd evpn domain remote 192.0.2.1:10010", _evpn_rd_other,
        note="手続き。rd の後ろが2語以上の行(rd evpn domain remote <値> など)は、形の違う書き方として"
        "行の原文を options に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD,
    ),
    Pattern(
        "bgp.evpn_route_target", _SCOPE_EVPN, r"route-target (?P<rest>.+)", "evpn_instances[].route_targets",
        "route-target both 10:10010", _evpn_route_target,
        note="手続き。route-target の後ろの原文を route_targets に足す。" + _NOTE_SET + "。" + _NOTE_NO_PASSWORD,
    ),
    Pattern(
        "bgp.evpn_option", _SCOPE_EVPN, r"(?P<line>.+)", "evpn_instances[].options", "redistribute learned",
        _evpn_option,
        note="手続き。中のほかの子を持たない行の原文を options に足す。vlan <範囲> は展開しない。" + _NOTE_SET + "。"
        + _NOTE_NO_PASSWORD + "。書式に合わなかった rd・route-target の行(rd だけ・route-target だけ)も入れない。"
        "子を持つ pseudowire・comment は見出しも中も読まない(ADR-0046 決定2)",
    ),
)

# 見出しのパターン → 子の行を読む scope
_CHILD_SCOPES = {
    "bgp.neighbor_block": _SCOPE_NEIGHBOR,
    "bgp.vrf.neighbor_block": _SCOPE_NEIGHBOR,
    "bgp.neighbor_block.af": _SCOPE_NEIGHBOR_AF,
    "bgp.vrf": _SCOPE_VRF,
    "bgp.af_vrf": _SCOPE_AF,
    "bgp.af": _SCOPE_AF,
    "bgp.vrf.af": _SCOPE_AF,
    "bgp.evpn_instance": _SCOPE_EVPN,
}


def _read(node: HConfigChild, scope: str, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    for line in node.children:
        ctx.node = line
        if scope == _SCOPE_TOP:
            ctx.target, ctx.vrf = ctx.bgp, None
        pattern = apply_first(PATTERNS, scope, line.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(line, pattern.id)
        if pattern.id in _CHILD_SCOPES:
            _read(line, _CHILD_SCOPES[pattern.id], ctx, interpreted)


def extract_bgp(tree: HConfig, interpreted: InterpretedLines) -> Bgp | None:
    """最上位の1つ目の `router bgp` を Bgp に写す。無い、または AS 番号として読めなければ None。"""
    ctx = _Ctx()
    for block in tree.children:
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)
        _read(block, _SCOPE_TOP, ctx, interpreted)
    return ctx.bgp
