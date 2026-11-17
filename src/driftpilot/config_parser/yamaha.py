"""ヤマハ RTX の木(yamaha_tree)を IR の interfaces・static_routes・vendor_specific["yamaha"] に写す extractor
(REQ-300・REQ-301。ADR-0213 決定 3・4・7)。

写すのは、よく使うインターフェースの行と `ip route … gateway …` の行(REQ-301)。ほかの行は値を解釈せずに、
範囲ごとの行の集合として写す(汎用の形)。bgp・ospf などは空で返す。

- インターフェース(`interfaces`): 最上位の `ip <IF> address|mtu|secure filter`・`ipv6 <IF> address`(`<IF>` は `lan<数字>`・
  `lan<数字>/<数字>`・`vlan<数字>`・`bridge<数字>`・`loopback<数字>`)と、`pp`・`tunnel` の範囲の中の
  `ip pp|tunnel address|mtu|secure filter`・`ipv6 pp|tunnel address`。範囲の中のインターフェースの名前は範囲の名前(`pp 1`・`tunnel 1`)。
  表の行が 1 つでも写せた名前ごとに 1 つ作る。同じ欄に違う値の行が 2 つ以上あれば後の行の値を使い、食い違いとして行番号を記録する
- スタティックルート(`static_routes`): 最上位と範囲の中の `ip route <宛先> gateway <ゲートウェイ>`(後ろに何も無い形)
- `vendor_specific["yamaha"]["settings"]["attributes"]`: 上で写さなかった最上位の行
- `vendor_specific["yamaha"]["pp"][<キー>]["attributes"]`・`["tunnel"][<キー>]["attributes"]`: 上で写さなかった範囲の子の行。
  キーは範囲の名前の後ろの語(`1`・`anonymous`)。子が無い範囲も空のリストで置く。範囲が無くても `pp`・`tunnel` は `{}` で置く
- attributes は行の集合(空白を 1 つにまとめた文字列の昇順。同じ文字列は 1 つ)
- 範囲を選ぶ行(範囲のノードと選び直しのノード)は値を写さず、解釈済みにするだけ

読み方は 2 段。先に写しの表(interfaces・static_routes)だけを試し、当たらない行と、形は合っても値が読めない行
(`/33` のアドレス、ホストの部分が 0 でない宛先など)は、汎用の表で読む。行を失わない(ADR-0213 決定 3)。

送らない行は入れない(ADR-0213 決定 4): 行を Masker().judge_alone(行, 祖先) で判定し、decision が "plain" でなければ
写しにも集合にも入れず、未解釈のままにする(AI に送る側と同じ判定。伏せる行も、表で「そのまま送る」と決まる行も入れない)。
祖先は、範囲の子ならその範囲を選ぶ行(`pp select 1` など)の 1 つ、最上位の行は無し。
判定器は利用者が設定で足した語を使わない既定のもの。1 回の extract で 1 つ作る。
"""

import re
from dataclasses import dataclass, field
from ipaddress import AddressValueError, IPv4Address, IPv4Interface, IPv4Network, IPv6Interface
from typing import Any

from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree
from driftpilot.config_parser.ir import (
    Acl,
    Bgp,
    Global,
    Interface,
    Ipsec,
    Ospf,
    PrefixList,
    RouteMap,
    SdwanBlock,
    StaticRoute,
    Vlan,
    new_global,
    new_interface,
)
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

SCOPE_SELECT = "select"
SCOPE_TOP = "top"
# 範囲の名前(pp・tunnel)→ 写しの表の scope
_CHILD_SCOPES = ("pp", "tunnel")
# インターフェースの名前の頭 → type
_TYPES = {"lan": "routed", "pp": "routed", "tunnel": "routed", "vlan": "svi", "bridge": "svi", "loopback": "loopback"}


@dataclass
class _Entry:
    """1 つのインターフェースについて、写せた行から集めた値。values は欄 → (値, 行番号)。"""

    path: tuple[str, ...]
    first: int
    order: int
    values: dict[str, tuple[Any, int | None]] = field(default_factory=dict)
    conflicts: dict[str, set[int]] = field(default_factory=dict)
    access_groups: list[str] = field(default_factory=list)
    ipv6_addresses: set[str] = field(default_factory=set)


class _Mapped:
    """写しの表で写せた行を集める。インターフェースの名前ごとの値と、経路(行番号つき)。"""

    def __init__(self) -> None:
        self.entries: dict[str, _Entry] = {}
        self.routes: list[tuple[int, int, StaticRoute]] = []

    def entry(self, name: str, ctx: "_Ctx") -> _Entry:
        line = ctx.line_no
        found = self.entries.get(name)
        if found is None:
            found = self.entries[name] = _Entry(ctx.path, line or 0, len(self.entries))
        elif line is not None:
            found.first = min(found.first, line) if found.first else line
        return found

    def write(self, name: str, key: str, value: Any, ctx: "_Ctx") -> None:
        """欄に値を書く。前の値と違えば、両方の行番号を食い違いとして記録し、後の値を使う(fujitsu_sir._Ctx.write と同じ)。"""
        entry = self.entry(name, ctx)
        line = ctx.line_no
        previous = entry.values.get(key)
        if previous is not None and previous[0] != value and previous[1] is not None and line is not None:
            entry.conflicts.setdefault(key, {previous[1]}).add(line)
        entry.values[key] = (value, line)

    def interfaces(self) -> dict[str, Interface]:
        """名前は最初に写せた行の順。"""
        out: dict[str, Interface] = {}
        for name, entry in sorted(self.entries.items(), key=lambda item: (item[1].first, item[1].order)):
            interface = new_interface(name)
            interface["type"] = _TYPES[re.match(r"[a-z]+", name)[0]]
            for key in ("ip_address", "address_mode", "mtu"):
                if key in entry.values:
                    interface[key] = entry.values[key][0]
            interface["access_groups"] = list(entry.access_groups)
            interface["ipv6_addresses"] = sorted(entry.ipv6_addresses)
            out[name] = interface
        return out

    def duplicates(self) -> list[DuplicateLine]:
        """値の食い違い(範囲のパスと行番号だけ。値は持たない)。"""
        return [
            DuplicateLine(entry.path, tuple(sorted(lines)))
            for entry in self.entries.values()
            for lines in entry.conflicts.values()
        ]

    def static_routes(self) -> list[StaticRoute]:
        return [route for _, _, route in sorted(self.routes, key=lambda item: (item[0], item[1]))]


class _Ctx:
    """1 つの範囲(か最上位)の行を集めている間の状態。judge は送らない行を判定する関数(行 → 入れてよいか)。

    node は今読んでいる行のノード(無ければ行番号は None)、mapped は写しの表で写せた行の集め先(範囲どうしで共有)、
    path は範囲のパス(最上位は `()`)。
    """

    def __init__(
        self,
        judge: Any,
        ancestors: tuple[str, ...] = (),
        mapped: _Mapped | None = None,
        path: tuple[str, ...] = (),
    ) -> None:
        self.judge = judge
        self.ancestors = ancestors
        self.mapped = _Mapped() if mapped is None else mapped
        self.path = path
        self.node: FlatNode | None = None
        self.attributes: set[str] = set()

    @property
    def line_no(self) -> int | None:
        return self.node.line_no if self.node is not None else None

    def generic(self) -> dict[str, list[str]]:
        return {"attributes": sorted(self.attributes)}

    def interface_name(self, kind: str) -> str:
        """範囲の中の行のインターフェースの名前(`pp select 1` の中の `ip pp …` なら `pp 1`)。祖先が無ければ範囲の種類の語。"""
        if not self.ancestors:
            return kind
        return f"{kind} {self.ancestors[0].rpartition(' ')[2]}"


def _select_line(m: re.Match[str], ctx: Any) -> bool:
    """範囲を選ぶ行(木では範囲の名前のノード)。値を写さず解釈済みにする。"""
    return True


def _generic(m: re.Match[str], ctx: _Ctx) -> bool:
    """送らない行でなければ、行(空白を 1 つにまとめたもの)を集合に足す。送らない行は未解釈のまま。"""
    line = " ".join(m["rest"].split())
    if not ctx.judge(line, ctx.ancestors):
        return False
    ctx.attributes.add(line)
    return True


# --- インターフェース(REQ-301 §2) ---


def _plain(m: re.Match[str], ctx: _Ctx) -> bool:
    """送らない行の判定を先に当てる(決定 4)。写さない行は汎用の形でもう一度判定されて落ちる。"""
    return bool(ctx.judge(" ".join(m.string.split()), ctx.ancestors))


def _name_of(m: re.Match[str], ctx: _Ctx, kind: str | None) -> str:
    return m["name"] if kind is None else ctx.interface_name(kind)


def _address(kind: str | None, m: re.Match[str], ctx: _Ctx) -> bool:
    length = m.groupdict().get("len")
    try:
        address = IPv4Interface(f"{m['addr']}/{length if length is not None else 32}")
    except ValueError:
        return False
    if not _plain(m, ctx):
        return False
    ctx.mapped.write(_name_of(m, ctx, kind), "ip_address", address, ctx)
    return True


def _address_dhcp(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(m, ctx):
        return False
    ctx.mapped.write(m["name"], "address_mode", " ".join(["dhcp", *(m["rest"] or "").split()]), ctx)
    return True


def _mtu(kind: str | None, m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(m, ctx):
        return False
    ctx.mapped.write(_name_of(m, ctx, kind), "mtu", int(m["mtu"]), ctx)
    return True


def _secure_filter(kind: str | None, m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(m, ctx):
        return False
    entry = ctx.mapped.entry(_name_of(m, ctx, kind), ctx)
    line = " ".join(m.string.split())
    if line not in entry.access_groups:
        entry.access_groups.append(line)
    return True


def _ipv6_address(kind: str | None, m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(m, ctx):
        return False
    word = m["addr"]
    # `<アドレス>/<長さ>` として読めるものだけ正規の書き方にする。長さの無い語(`/128` を足すと別の値になる)は原文のまま
    if word.rpartition("/")[2].isdecimal() and "/" in word:
        try:
            word = str(IPv6Interface(word))
        except ValueError:
            pass
    ctx.mapped.entry(_name_of(m, ctx, kind), ctx).ipv6_addresses.add(word)
    return True


# --- スタティックルート(REQ-301 §3) ---


def _route(m: re.Match[str], ctx: _Ctx) -> bool:
    dest, gateway = m["dest"], m["gw"]
    try:
        if dest == "default":
            destination = IPv4Network("0.0.0.0/0")
        else:
            address, _, length = dest.partition("/")
            if "/" in dest and not length.isdecimal():
                return False
            # strict=True: ホストの部分が 0 でない宛先は読まない(汎用の形のまま)
            destination = IPv4Network(f"{address}/{length or 32}", strict=True)
        next_hop = IPv4Address(gateway) if gateway[0].isdecimal() else None
    except (AddressValueError, ValueError):
        return False
    if not _plain(m, ctx):
        return False
    route: StaticRoute = {
        "destination": destination,
        "next_hop": next_hop,
        "interface": " ".join(gateway.split()) if next_hop is None else None,
        "vrf": None,
        "distance": None,
        "enabled": None,
    }
    ctx.mapped.routes.append((ctx.line_no or 0, len(ctx.mapped.routes), route))
    return True


def _top(fn: Any) -> Any:
    return lambda m, ctx: fn(None, m, ctx)


def _in(kind: str, fn: Any) -> Any:
    return lambda m, ctx: fn(kind, m, ctx)


_IF = r"(?P<name>lan\d+(?:/\d+)?|vlan\d+|bridge\d+|loopback\d+)"
_GW = r"(?P<gw>\d{1,3}(?:\.\d{1,3}){3}|pp (?:\d+|anonymous)|tunnel \d+)"
_ROUTE = rf"ip route (?P<dest>\S+) gateway {_GW}"
_MAP_NOTE = (
    "手続き。送らない行(AI に送る側の判定が「そのまま送る」でない行)は写さず、汎用の形に戻す(未解釈のまま)。"
    "形は合っても値が読めない行も、汎用の形に戻す。"
)
_IF_NOTE = "インターフェースの名前は `lan<数字>`・`lan<数字>/<数字>`・`vlan<数字>`・`bridge<数字>`・`loopback<数字>`。"
_SCOPE_IF_NOTE = "インターフェースの名前は範囲の名前(`pp 1`・`pp anonymous`・`tunnel 1`)。範囲の外の行は当てない。"
_CONFLICT_NOTE = "同じインターフェースの同じ欄に違う値の行が 2 つ以上あれば、後の行の値を使い、食い違いを記録する(値は持たない)。"
_ROUTE_NOTE = (
    "`default` は 0.0.0.0/0、アドレスだけなら /32。ホストの部分が 0 でない宛先は写さない。"
    "ゲートウェイが IPv4 なら next_hop、`pp <数字>`・`pp anonymous`・`tunnel <数字>` なら interface(空白は 1 つ)。"
    "後ろに語が続く形・`gateway dhcp <IF>`・`ipv6 route` は写さず汎用の形のまま。出てきた順。vrf・distance・enabled は None"
)
_GENERIC_NOTE = (
    "行は解釈せず、空白を 1 つにまとめた文字列を集合(昇順・同じ文字列は 1 つ)として置く。"
    "AI に送る側の判定(Masker.judge_alone)が「そのまま送る」でない行は集合に入れず、未解釈のまま"
)

# 並び＝優先順位。写しの表(interfaces・static_routes)が先、汎用の表(ymh_generic)が後。_read は 2 段で読む
_MAP_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "interfaces.ymh.address_dhcp", SCOPE_TOP, rf"ip {_IF} address dhcp(?: (?P<rest>.+))?", "interfaces[].address_mode",
        "ip lan2 address dhcp", _address_dhcp,
        note=_MAP_NOTE + "`dhcp [<残り>]`(空白は 1 つ。Cisco の `ip address dhcp` と同じ値)。" + _IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.ymh.address", SCOPE_TOP, rf"ip {_IF} address (?P<addr>[^\s/]+)/(?P<len>\d+)", "interfaces[].ip_address",
        "ip lan1 address 192.0.2.1/24", _top(_address),
        note=_MAP_NOTE + "`<A>/<長さ>` が IPv4 のインターフェースとして読めるものだけ。" + _IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.ymh.mtu", SCOPE_TOP, rf"ip {_IF} mtu (?P<mtu>\d+)", "interfaces[].mtu", "ip lan1 mtu 1500", _top(_mtu),
        note=_MAP_NOTE + _IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.ymh.secure_filter", SCOPE_TOP, rf"ip {_IF} secure filter (?:in|out) \S.*", "interfaces[].access_groups",
        "ip lan2 secure filter in 100 101 200", _top(_secure_filter),
        note=_MAP_NOTE + "行の原文(空白は 1 つ)を、出てきた順に足す(同じ文字列は 1 つ)。" + _IF_NOTE,
    ),
    Pattern(
        "interfaces.ymh.ipv6_address", SCOPE_TOP, rf"ipv6 {_IF} address (?P<addr>\S+)", "interfaces[].ipv6_addresses",
        "ipv6 lan3 address 2001:db8::1/64", _top(_ipv6_address),
        note=_MAP_NOTE + "`<アドレス>/<長さ>` として読めれば IPv6 の正規の書き方、読めなければ原文の語。文字列の昇順の集合。" + _IF_NOTE,
    ),
    Pattern(
        "static_routes.ymh.route", SCOPE_TOP, _ROUTE, "static_routes[]", "ip route default gateway pp 1", _route,
        note=_MAP_NOTE + _ROUTE_NOTE,
    ),
    Pattern(
        "interfaces.ymh.pp_address", "pp", r"ip pp address (?P<addr>[^\s/]+)(?:/(?P<len>\d+))?", "interfaces[].ip_address",
        "ip pp address 203.0.113.1", _in("pp", _address),
        note=_MAP_NOTE + "長さが無ければ /32。" + _SCOPE_IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.ymh.pp_mtu", "pp", r"ip pp mtu (?P<mtu>\d+)", "interfaces[].mtu", "ip pp mtu 1454", _in("pp", _mtu),
        note=_MAP_NOTE + _SCOPE_IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.ymh.pp_secure_filter", "pp", r"ip pp secure filter (?:in|out) \S.*", "interfaces[].access_groups",
        "ip pp secure filter in 200 201", _in("pp", _secure_filter),
        note=_MAP_NOTE + "行の原文(空白は 1 つ)を、出てきた順に足す(同じ文字列は 1 つ)。" + _SCOPE_IF_NOTE,
    ),
    Pattern(
        "interfaces.ymh.pp_ipv6_address", "pp", r"ipv6 pp address (?P<addr>\S+)", "interfaces[].ipv6_addresses",
        "ipv6 pp address 2001:db8::2/64", _in("pp", _ipv6_address),
        note=_MAP_NOTE + "`<アドレス>/<長さ>` として読めれば IPv6 の正規の書き方、読めなければ原文の語。" + _SCOPE_IF_NOTE,
    ),
    Pattern(
        "static_routes.ymh.pp_route", "pp", _ROUTE, "static_routes[]", "ip route 198.51.100.0/24 gateway pp 1", _route,
        note=_MAP_NOTE + _ROUTE_NOTE,
    ),
    Pattern(
        "interfaces.ymh.tunnel_address", "tunnel", r"ip tunnel address (?P<addr>[^\s/]+)/(?P<len>\d+)",
        "interfaces[].ip_address", "ip tunnel address 192.0.2.253/30", _in("tunnel", _address),
        note=_MAP_NOTE + _SCOPE_IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.ymh.tunnel_mtu", "tunnel", r"ip tunnel mtu (?P<mtu>\d+)", "interfaces[].mtu", "ip tunnel mtu 1280",
        _in("tunnel", _mtu), note=_MAP_NOTE + _SCOPE_IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.ymh.tunnel_secure_filter", "tunnel", r"ip tunnel secure filter (?:in|out) \S.*",
        "interfaces[].access_groups", "ip tunnel secure filter in 300", _in("tunnel", _secure_filter),
        note=_MAP_NOTE + "行の原文(空白は 1 つ)を、出てきた順に足す(同じ文字列は 1 つ)。" + _SCOPE_IF_NOTE,
    ),
    Pattern(
        "interfaces.ymh.tunnel_ipv6_address", "tunnel", r"ipv6 tunnel address (?P<addr>\S+)", "interfaces[].ipv6_addresses",
        "ipv6 tunnel address 2001:db8::3/64", _in("tunnel", _ipv6_address),
        note=_MAP_NOTE + "`<アドレス>/<長さ>` として読めれば IPv6 の正規の書き方、読めなければ原文の語。" + _SCOPE_IF_NOTE,
    ),
    Pattern(
        "static_routes.ymh.tunnel_route", "tunnel", _ROUTE, "static_routes[]", "ip route 198.51.100.0/24 gateway tunnel 1",
        _route, note=_MAP_NOTE + _ROUTE_NOTE,
    ),
)

_GENERIC_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vendor_specific.ymh_generic.select", SCOPE_SELECT, r"(?:pp (?:\d+|anonymous)|tunnel \d+)",
        "vendor_specific.pp[] / vendor_specific.tunnel[]", "tunnel 1", _select_line,
        note="範囲を選ぶ行(`pp select 1` など)は、木では範囲の名前(`pp 1`・`tunnel 1`)のノードになる。"
        "選び直しの行も同じ。値は写さず、範囲のキーを作って解釈済みにする",
    ),
    Pattern(
        "vendor_specific.ymh_generic.pp", "pp", r"(?P<rest>.+)", "vendor_specific.pp[].attributes",
        "pp always-on on", _generic, note="`pp` の範囲の子の行(写しの表に当たらなかった行)。" + _GENERIC_NOTE,
    ),
    Pattern(
        "vendor_specific.ymh_generic.tunnel", "tunnel", r"(?P<rest>.+)", "vendor_specific.tunnel[].attributes",
        "tunnel enable 1", _generic, note="`tunnel` の範囲の子の行(写しの表に当たらなかった行)。" + _GENERIC_NOTE,
    ),
    Pattern(
        "vendor_specific.ymh_generic.top", SCOPE_TOP, r"(?P<rest>.+)", "vendor_specific.settings.attributes",
        "dns server 192.0.2.53", _generic, note="最上位の行(写しの表に当たらなかった行)。" + _GENERIC_NOTE,
    ),
)

PATTERNS: tuple[Pattern, ...] = _MAP_PATTERNS + _GENERIC_PATTERNS


def _judge_function() -> Any:
    """送らない行の判定(行, 祖先 → 入れてよいか)。判定器は 1 つだけ作る。"""
    # llm_analyzer.masking は config_parser を読み込むため、輪を避けて呼ぶときに読む(device_settings.has_mask_word と同じ)
    from driftpilot.llm_analyzer.masking import Masker

    masker = Masker()

    def judge(line: str, ancestors: tuple[str, ...]) -> bool:
        return masker.judge_alone(line, ancestors).decision == "plain"

    return judge


def _read(node: FlatNode, scope: str, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """写しの表を先に試し、写せなかった行(当たらない・値が読めない・送らない)は汎用の表で読む。"""
    ctx.node = node
    pattern = apply_first(_MAP_PATTERNS, scope, node.text, ctx)
    if pattern is None:
        pattern = apply_first(_GENERIC_PATTERNS, scope, node.text, ctx)
    if pattern is not None:
        interpreted.mark(node, pattern.id)


def extract(
    tree: FlatTree, interpreted: InterpretedLines
) -> tuple[
    dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
    dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Vlan], dict[str, Acl],
    dict[str, Any], list[DuplicateLine],
]:
    """(interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, global, vlans, acls,
    vendor_specific["yamaha"], 値の食い違い) を返す。bgp から acls まではいつも空(global は new_global())。acls は Junos だけが写す。

    interfaces・static_routes は写しの表で写せた行から作る(上の説明)。値の食い違いは、同じインターフェースの同じ欄に
    違う値の行が 2 つ以上ある場合(行番号だけ)。同じ文字列の行の重複は木(yamaha_tree)が記録する。
    """
    judge = _judge_function()
    mapped = _Mapped()
    settings = _Ctx(judge, mapped=mapped)
    ranges: dict[str, dict[str, dict[str, list[str]]]] = {name: {} for name in _CHILD_SCOPES}
    for block in tree.children:
        if block.scope_id is None:
            _read(block, SCOPE_TOP, settings, interpreted)
            continue
        kind, _, key = block.text.partition(" ")
        select = " ".join((kind, "select", key))
        ctx = _Ctx(judge, (select,), mapped, block.path())
        for line in block.children:
            _read(line, SCOPE_SELECT if line.repeated_scope else kind, ctx, interpreted)
        _read(block, SCOPE_SELECT, ctx, interpreted)
        ranges[kind][key] = ctx.generic()
    vendor_specific = {"settings": settings.generic(), **ranges}
    return (
        mapped.interfaces(), mapped.static_routes(), None, {}, None, {}, {}, {}, {}, new_global(), {}, {}, vendor_specific,
        mapped.duplicates(),
    )
