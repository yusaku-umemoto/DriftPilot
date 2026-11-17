"""PAN-OS の木(panos_tree。2 段の文の木)を、IR の global・interfaces・static_routes と vendor_specific["paloalto_panos"] に
写す extractor(ADR-0226 決定 6。REQ-327・REQ-329)。

よく使う文(REQ-329)は共通スキーマに写し、写した文は汎用の形から外す(2 か所の差分にしない)。ほかの文は汎用の形に残る。
文の語の並びで当てる(Junos の REQ-324 と同じ考え方)。値は、引用符で囲まれた語(引用符は外す)か、空白を含まない 1 語だけ。
値の後ろに語が続く文は当てない。

- `global`(最上位の語 `deviceconfig`): `system hostname <名前>`→hostname、`system domain <名前>`→domain_name、
  `system dns-setting servers primary|secondary <アドレス>`→dns.servers(正規のアドレス。昇順・重ねない)、
  `system ntp-servers primary|secondary-ntp-server ntp-server-address <アドレスか名前>`→ntp_servers(アドレスは正規の書き方。
  使う値の文の行の順・重ねない)、`system default-gateway <IPv4>`→default_gateway。アドレスが読めなければ写さない
- `interfaces`(最上位の語 `network`): `interface ethernet|aggregate-ethernet <名前> …` の comment(description)・link-speed(speed)・
  `layer3 ip <A>/<長さ>`(行の順の 1 つ目が ip_address、2 つ目以降が secondary_addresses の昇順の集合)・`layer3 mtu`。
  `… layer3 units <名前.数> …` と `interface loopback|tunnel|vlan units <名前.数> …` は `<名前.数>` の別のインターフェース
  (parent_interface は物理の名前。loopback などは種類の語)で、ip・comment・tag(encapsulation に原文の `tag <数>`。ethernet などの
  layer3 units だけ)・mtu(loopback などの units だけ)。`interface loopback|tunnel|vlan ip <A>/<長さ>` は種類の語の名前の物理の ip_address。
  type は名前と種類で決める(ae か aggregate-ethernet→port_channel、loopback→loopback、vlan→svi、ほかは routed。論理は物理と同じ)。
  表の文が 1 つでも写せた名前ごとに 1 つ作る。`ip <A>/<長さ>` が IPv4 として読めない文(アドレスのオブジェクトの名前など)は写さない
- `static_routes`(最上位の語 `network`): `virtual-router <VR> routing-table ip|ipv6 static-route <名前> …` を、同じ <VR>・ip か ipv6・<名前> ごとに
  1 本にまとめる。`destination <宛先>/<長さ>`(ホストの部分が 0)・`nexthop ip-address|ipv6-address <アドレス>`・`interface <名前>`・
  `admin-dist <数>`。vrf は <VR> が `default` なら None、ほかは <VR> の名前。destination の読めない経路は、どの文も写さない。enabled は None
- 同じ欄に違う値の文が 2 つ以上あれば、後の文の値を使い、DuplicateLine((最上位の語,), 行番号)を記録する(値は持たない)

写せなかった文(形が違う・値が読めない・送らない側の文)は、先に写しの表を試した後、汎用の表で読む。

- `vendor_specific["paloalto_panos"]["config"]` = `{最上位の語: {"lines": [文の昇順。同じ文字列は 1 つ]}}`。
  文は、最上位の語を除いた根からの残りの語を空白 1 つでつないだもの(`rulebase`・`security rules R1 action allow`)。
  残りの語の無い最上位の語(`set foo`・`<foo/>`)は `{"lines": []}`
- `lines` は並びを無視して、要素ごとの追加・削除で比べる(diff_engine の `_SET_LISTS`)
- 複数行の値(PEM など)の文の語は、中身ではなく `<複数行の値 <行数> 行 <sha256 の 16 進の頭 16 字>>` の 1 語(panos_xml)。中身が変われば差分に出る

送らない行は入れない(ADR-0226 決定 6): 写しにも汎用の形にも、文ごとに、`set` の行の形 `set <最上位の語> <文>` を
Masker().judge_alone(行, ()) で判定し、decision が "plain" でなければ入れず、未解釈のままにする(AI に送る側と同じ判定)。
残りの語の無い最上位の行は `set <最上位の語>`。判定器は利用者が設定で足した語を使わない既定のもの。1 回の extract で 1 つ作る。
汎用の文が 0 件になった最上位の語(全部が写った・送らない側になった)は、鍵ごと置かない。

`set` で始まらない最上位の行(panos_tree の SCOPE_OTHER)は未解釈のまま。未解釈の行は木の位置(`(最上位の語, 文)`)で IR の `unparsed` に入る。
"""

import re
from dataclasses import dataclass, field
from ipaddress import (
    AddressValueError,
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Network,
)
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
from driftpilot.config_parser.panos_tree import SCOPE_TOP
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

SCOPE_TOP_NODE = "top"
SCOPE_STATEMENT_NODE = "statement"
SCOPE_TOP_LINE_NODE = "top_line"

# 写しの表の scope = 最上位の語そのもの
TOP_DEVICECONFIG = "deviceconfig"
TOP_NETWORK = "network"
_MAP_TOPS = (TOP_DEVICECONFIG, TOP_NETWORK)

_TARGET = "vendor_specific.paloalto_panos.config"

# 経路の鍵(VR の名前・ip か ipv6・経路の名前)
_RouteKey = tuple[str, str, str]


@dataclass
class _Entry:
    """1 つのインターフェースについて、写せた文から集めた値。values は欄 → (値, 行番号)。"""

    first: int
    order: int
    kind: str
    parent: str | None
    values: dict[str, tuple[Any, int | None]] = field(default_factory=dict)
    conflicts: dict[str, set[int]] = field(default_factory=dict)
    addresses: list[IPv4Interface] = field(default_factory=list)


@dataclass
class _Route:
    """スタティックルート 1 本(同じ VR・ip か ipv6・名前の文をまとめたもの)。"""

    first: int
    order: int
    values: dict[str, tuple[Any, int | None]] = field(default_factory=dict)
    conflicts: dict[str, set[int]] = field(default_factory=dict)


class _Mapped:
    """写しの表で写せた文を集める。global の値・インターフェースの名前ごとの値・経路(行番号つき)。"""

    def __init__(self) -> None:
        self.entries: dict[str, _Entry] = {}
        self.routes: dict[_RouteKey, _Route] = {}
        self.global_values: dict[str, tuple[Any, int | None]] = {}
        self.global_conflicts: dict[str, set[int]] = {}

    @staticmethod
    def _write(
        values: dict[str, tuple[Any, int | None]], conflicts: dict[str, set[int]], key: str, value: Any, line: int | None
    ) -> None:
        """欄に値を書く。前の値と違えば、両方の行番号を食い違いとして記録し、後の値を使う(Junos・yamaha と同じ)。"""
        previous = values.get(key)
        if previous is not None and previous[0] != value and previous[1] is not None and line is not None:
            conflicts.setdefault(key, {previous[1]}).add(line)
        values[key] = (value, line)

    def write_global(self, key: str, value: Any, line: int | None) -> None:
        self._write(self.global_values, self.global_conflicts, key, value, line)

    def entry(self, name: str, kind: str, parent: str | None, line: int | None) -> _Entry:
        found = self.entries.get(name)
        if found is None:
            found = self.entries[name] = _Entry(line or 0, len(self.entries), kind, parent)
        elif line is not None:
            found.first = min(found.first, line) if found.first else line
        return found

    def write(self, name: str, kind: str, parent: str | None, key: str, value: Any, line: int | None) -> None:
        entry = self.entry(name, kind, parent, line)
        self._write(entry.values, entry.conflicts, key, value, line)

    def route(self, key: _RouteKey, line: int | None) -> _Route:
        found = self.routes.get(key)
        if found is None:
            found = self.routes[key] = _Route(line or 0, len(self.routes))
        elif line is not None:
            found.first = min(found.first, line) if found.first else line
        return found

    def write_route(self, key: _RouteKey, field_name: str, value: Any, line: int | None) -> None:
        route = self.route(key, line)
        self._write(route.values, route.conflicts, field_name, value, line)

    def interfaces(self) -> dict[str, Interface]:
        """名前は最初に写せた文の順。"""
        out: dict[str, Interface] = {}
        for name, entry in sorted(self.entries.items(), key=lambda item: (item[1].first, item[1].order)):
            interface = new_interface(name)
            interface["parent_interface"] = entry.parent
            interface["type"] = _interface_type(entry.parent or name, entry.kind)  # type: ignore[typeddict-item]
            for key in ("description", "mtu", "speed", "encapsulation"):
                if key in entry.values:
                    interface[key] = entry.values[key][0]  # type: ignore[literal-required]
            if entry.addresses:
                interface["ip_address"] = entry.addresses[0]
            interface["secondary_addresses"] = (
                sorted(set(entry.addresses[1:]) - {entry.addresses[0]}) if entry.addresses else []
            )
            out[name] = interface
        return out

    def static_routes(self) -> list[StaticRoute]:
        out: list[StaticRoute] = []
        for key, route in sorted(self.routes.items(), key=lambda item: (item[1].first, item[1].order)):
            if "destination" not in route.values:
                continue
            vrf = None if key[0] == "default" else key[0]
            out.append(
                {
                    "destination": route.values["destination"][0],
                    "next_hop": route.values["next_hop"][0] if "next_hop" in route.values else None,
                    "interface": route.values["interface"][0] if "interface" in route.values else None,
                    "vrf": vrf,
                    "distance": route.values["distance"][0] if "distance" in route.values else None,
                    "enabled": None,
                }
            )
        return out

    def global_(self) -> Global:
        out = new_global()
        for key in ("hostname", "domain_name", "default_gateway"):
            if key in self.global_values:
                out[key] = self.global_values[key][0]  # type: ignore[literal-required]
        out["dns"]["servers"] = sorted({self.global_values[key][0] for key in ("dns_primary", "dns_secondary") if key in self.global_values})
        ntp = sorted(
            (self.global_values[key] for key in ("ntp_primary", "ntp_secondary") if key in self.global_values),
            key=lambda item: item[1] or 0,
        )
        servers: list[str] = []
        for value, _ in ntp:
            if value not in servers:
                servers.append(value)
        out["ntp_servers"] = servers
        return out

    def duplicates(self) -> list[DuplicateLine]:
        """値の食い違い(最上位の語と行番号だけ。値は持たない)。"""
        found = [DuplicateLine((TOP_DEVICECONFIG,), tuple(sorted(lines))) for lines in self.global_conflicts.values()]
        for entry in self.entries.values():
            found += [DuplicateLine((TOP_NETWORK,), tuple(sorted(lines))) for lines in entry.conflicts.values()]
        for route in self.routes.values():
            found += [DuplicateLine((TOP_NETWORK,), tuple(sorted(lines))) for lines in route.conflicts.values()]
        return found


def _interface_type(physical: str, kind: str) -> str:
    if kind == "aggregate-ethernet" or physical.startswith("ae"):
        return "port_channel"
    if kind == "loopback":
        return "loopback"
    if kind == "vlan":
        return "svi"
    return "routed"


class _Ctx:
    """最上位の語の文を読んでいる間の状態。judge は送らない行を判定する関数(set の行 → 入れてよいか)。

    mapped は写しの表で写せた文の集め先、node は今読んでいる文のノード(無ければ行番号は None)。
    routes_ready は destination の読める経路の鍵の集合(extract が先に調べて決め直す。None は全部受ける。例のテストのため)。
    """

    def __init__(self, judge: Any, sets: dict[str, set[str]]) -> None:
        self.judge = judge
        self.sets = sets
        self.top = ""
        self.mapped = _Mapped()
        self.node: FlatNode | None = None
        self.routes_ready: set[_RouteKey] | None = None

    @property
    def line_no(self) -> int | None:
        return self.node.line_no if self.node is not None else None


def _top(m: Any, ctx: _Ctx) -> bool:
    """最上位の語のノード。暗黙のノードで、鍵は文が入ったときに置く(ここでは何も置かない)。"""
    return True


def _statement(m: Any, ctx: _Ctx) -> bool:
    """送らない文でなければ、最上位の語の文の集合に足す。送らない文は未解釈のまま。"""
    if not ctx.judge(f"set {ctx.top} {m.string}"):
        return False
    ctx.sets.setdefault(ctx.top, set()).add(m.string)
    return True


def _top_line(m: Any, ctx: _Ctx) -> bool:
    """残りの語の無い最上位の行。送らない行でなければ、鍵を空の文の集合で置く。"""
    if not ctx.judge(f"set {ctx.top}"):
        return False
    ctx.sets.setdefault(ctx.top, set())
    return True


# --- 写しの表(REQ-329)。値を読み、送らない文の判定をして、最後に書く ---


def _unquote(word: str) -> str | None:
    """引用符で囲まれていれば外す。空になる値は None(写さない)。"""
    if len(word) >= 2 and word[0] == '"' and word[-1] == '"':
        word = word[1:-1]
    return word or None


def _plain(top: str, m: re.Match[str], ctx: _Ctx) -> bool:
    """送らない行の判定(`set <最上位の語> <文>`)。汎用の形と同じ判定を、写す前に当てる。"""
    return bool(ctx.judge(f"set {top} {m.string}"))


def _ip_address(word: str) -> IPv4Address | IPv6Address | None:
    try:
        return IPv4Address(word) if ":" not in word else IPv6Address(word)
    except (AddressValueError, ValueError):
        return None


def _text_global(key: str) -> Any:
    """hostname・domain_name(引用符を外した文字列)。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        value = _unquote(m["v"])
        if value is None or not _plain(TOP_DEVICECONFIG, m, ctx):
            return False
        ctx.mapped.write_global(key, value, ctx.line_no)
        return True

    return apply


def _dns_server(m: re.Match[str], ctx: _Ctx) -> bool:
    address = _ip_address(_unquote(m["v"]) or "")
    if address is None or not _plain(TOP_DEVICECONFIG, m, ctx):
        return False
    ctx.mapped.write_global(f"dns_{m['f']}", str(address), ctx.line_no)
    return True


def _ntp_server(m: re.Match[str], ctx: _Ctx) -> bool:
    word = _unquote(m["v"])
    if word is None or not _plain(TOP_DEVICECONFIG, m, ctx):
        return False
    address = _ip_address(word)
    ctx.mapped.write_global(f"ntp_{m['f']}", str(address) if address is not None else word, ctx.line_no)
    return True


def _default_gateway(m: re.Match[str], ctx: _Ctx) -> bool:
    word = _unquote(m["v"]) or ""
    try:
        address = IPv4Address(word)
    except (AddressValueError, ValueError):
        return False
    if not _plain(TOP_DEVICECONFIG, m, ctx):
        return False
    ctx.mapped.write_global("default_gateway", str(address), ctx.line_no)
    return True


def _address(m: re.Match[str]) -> IPv4Interface | None:
    try:
        return IPv4Interface(m["addr"])
    except ValueError:
        return None


def _if_target(m: re.Match[str]) -> tuple[str, str, str | None]:
    """(インターフェースの名前, 種類, 親の物理の名前)。`unit` があれば論理(名前は `<名前.数>`)、無ければ物理。"""
    groups = m.groupdict()
    kind = m["kind"]
    physical = groups.get("name") or kind
    if groups.get("unit") is not None:
        return m["unit"], kind, physical
    return physical, kind, None


def _if_text(key: str) -> Any:
    """description・speed(引用符を外した文字列)。"""

    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        value = _unquote(m["v"])
        if value is None or not _plain(TOP_NETWORK, m, ctx):
            return False
        name, kind, parent = _if_target(m)
        ctx.mapped.write(name, kind, parent, key, value, ctx.line_no)
        return True

    return apply


def _if_mtu(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(TOP_NETWORK, m, ctx):
        return False
    name, kind, parent = _if_target(m)
    ctx.mapped.write(name, kind, parent, "mtu", int(m["mtu"]), ctx.line_no)
    return True


def _if_tag(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(TOP_NETWORK, m, ctx):
        return False
    name, kind, parent = _if_target(m)
    ctx.mapped.write(name, kind, parent, "encapsulation", f"tag {m['tag']}", ctx.line_no)
    return True


def _if_ip(m: re.Match[str], ctx: _Ctx) -> bool:
    address = _address(m)
    if address is None or not _plain(TOP_NETWORK, m, ctx):
        return False
    name, kind, parent = _if_target(m)
    entry = ctx.mapped.entry(name, kind, parent, ctx.line_no)
    if address not in entry.addresses:
        entry.addresses.append(address)
    return True


def _route_key(m: re.Match[str]) -> _RouteKey | None:
    vr = _unquote(m["vr"])
    name = _unquote(m["rn"])
    if vr is None or name is None:
        return None
    return vr, m["af"], name


def _destination(af: str, word: str) -> IPv4Network | IPv6Network | None:
    """宛先。`<アドレス>/<長さ>` で、ホストの部分が 0 のものだけ(strict)。ip は IPv4、ipv6 は IPv6。"""
    base, _, length = word.partition("/")
    if not length.isascii() or not length.isdecimal():
        return None
    try:
        return IPv4Network(f"{base}/{length}", strict=True) if af == "ip" else IPv6Network(f"{base}/{length}", strict=True)
    except ValueError:
        return None


def _route_target(m: re.Match[str], ctx: _Ctx) -> _RouteKey | None:
    """文が掛かる経路の鍵。destination の読めない経路・送らない文なら None。"""
    key = _route_key(m)
    if key is None or (ctx.routes_ready is not None and key not in ctx.routes_ready):
        return None
    if not _plain(TOP_NETWORK, m, ctx):
        return None
    return key


def _route_destination(m: re.Match[str], ctx: _Ctx) -> bool:
    key = _route_key(m)
    if key is None:
        return False
    destination = _destination(key[1], m["dest"])
    if destination is None or not _plain(TOP_NETWORK, m, ctx):
        return False
    ctx.mapped.write_route(key, "destination", destination, ctx.line_no)
    return True


def _route_nexthop(m: re.Match[str], ctx: _Ctx) -> bool:
    key = _route_target(m, ctx)
    if key is None:
        return False
    word = m["v"]
    try:
        if key[1] == "ip" and m["kind"] == "ip-address":
            address: IPv4Address | IPv6Address = IPv4Address(word)
        elif key[1] == "ipv6" and m["kind"] == "ipv6-address":
            address = IPv6Address(word)
        else:
            return False
    except (AddressValueError, ValueError):
        return False
    ctx.mapped.write_route(key, "next_hop", address, ctx.line_no)
    return True


def _route_interface(m: re.Match[str], ctx: _Ctx) -> bool:
    key = _route_target(m, ctx)
    value = _unquote(m["v"])
    if key is None or value is None:
        return False
    ctx.mapped.write_route(key, "interface", value, ctx.line_no)
    return True


def _route_distance(m: re.Match[str], ctx: _Ctx) -> bool:
    key = _route_target(m, ctx)
    if key is None:
        return False
    ctx.mapped.write_route(key, "distance", int(m["dist"]), ctx.line_no)
    return True


_V = r'"(?:[^"\\]|\\.)*"|[^\s"]\S*'
_NAME = r"(?P<name>[^\s\"]+)"
_UNIT = r"(?P<unit>[^\s\"]+)"
_ADDR = r"(?P<addr>[^\s/]+/[0-9]+)"
_PHYS = rf"interface (?P<kind>ethernet|aggregate-ethernet) {_NAME}"
_L3UNIT = rf"{_PHYS} layer3 units {_UNIT}"
_VUNIT = rf"interface (?P<kind>loopback|tunnel|vlan) units {_UNIT}"
_ROUTE = rf"virtual-router (?P<vr>{_V}) routing-table (?P<af>ip|ipv6) static-route (?P<rn>{_V})"

_MAP_NOTE = (
    "手続き。値が読めない文・送らない文(AI に送る側の判定が「そのまま送る」でない文)は写さず、汎用の形に残す"
    "(未解釈のままの文もある)。値は、引用符で囲まれた語(引用符は外す)か空白を含まない 1 語。値の後ろに語が続く文は当てない。"
)
_CONFLICT_NOTE = "同じ欄に違う値の文が 2 つ以上あれば、後の文の値を使い、食い違いを記録する(値は持たない)。"
_PHYS_NOTE = "`interface ethernet|aggregate-ethernet <名前> …`(物理のインターフェース)。"
_L3UNIT_NOTE = (
    "`interface ethernet|aggregate-ethernet <名前> layer3 units <名前.数> …`。`<名前.数>` の別のインターフェース"
    "(parent_interface は物理の名前、type は物理と同じ)。"
)
_VUNIT_NOTE = (
    "`interface loopback|tunnel|vlan units <名前.数> …`。`<名前.数>` の別のインターフェース(parent_interface は種類の語、type は物理と同じ)。"
)
_IP_NOTE = (
    "`<A>/<長さ>` が IPv4 のインターフェースとして読めるものだけ(アドレスのオブジェクトの名前などは写さない)。"
    "行の順の 1 つ目が ip_address、2 つ目以降(重ねない)が secondary_addresses(昇順)。"
)
_ROUTE_NOTE = (
    "`virtual-router <VR> routing-table ip|ipv6 static-route <名前> …`。同じ <VR>・ip か ipv6・<名前> の文を 1 本の経路にまとめる。"
    "destination が無いか読めない経路(ネットワークとして読めない・ホストの部分が 0 でない)は、どの文も写さず汎用の形に残す。"
    "vrf は <VR> が `default` なら None、ほかは <VR> の名前。enabled は None。`metric`・`path-monitor …`・`bfd …`・`nexthop next-vr …` などは写さない。"
)

# 並び＝優先順位。写しの表(global・interfaces・static_routes)が先、汎用の表が後。extract は 2 段で読む
_MAP_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "global.panos.hostname", TOP_DEVICECONFIG, rf"system hostname (?P<v>{_V})", "global.hostname", "system hostname pa-test1",
        _text_global("hostname"), note=_MAP_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "global.panos.domain_name", TOP_DEVICECONFIG, rf"system domain (?P<v>{_V})", "global.domain_name",
        "system domain example.test", _text_global("domain_name"), note=_MAP_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "global.panos.dns_server", TOP_DEVICECONFIG, rf"system dns-setting servers (?P<f>primary|secondary) (?P<v>{_V})",
        "global.dns.servers", "system dns-setting servers primary 192.0.2.53", _dns_server,
        note=_MAP_NOTE + "primary と secondary は別の欄。正規のアドレスの文字列を、昇順・重ねずに置く。アドレスとして読めない文は写さない。"
        + _CONFLICT_NOTE,
    ),
    Pattern(
        "global.panos.ntp_server", TOP_DEVICECONFIG,
        rf"system ntp-servers (?P<f>primary|secondary)-ntp-server ntp-server-address (?P<v>{_V})", "global.ntp_servers",
        "system ntp-servers primary-ntp-server ntp-server-address 192.0.2.123", _ntp_server,
        note=_MAP_NOTE + "primary と secondary は別の欄。アドレスなら正規の書き方、名前ならそのまま置く。使う値の文の行の順に、重ねずに置く。"
        "`authentication-type` などほかの文は写さない。" + _CONFLICT_NOTE,
    ),
    Pattern(
        "global.panos.default_gateway", TOP_DEVICECONFIG, rf"system default-gateway (?P<v>{_V})", "global.default_gateway",
        "system default-gateway 192.0.2.254", _default_gateway,
        note=_MAP_NOTE + "IPv4 のアドレスだけ(正規の書き方)。読めない文は写さない。" + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.comment", TOP_NETWORK, rf"{_PHYS} comment (?P<v>{_V})", "interfaces[].description",
        "interface ethernet ethernet1/1 comment uplink", _if_text("description"), note=_MAP_NOTE + _PHYS_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.link_speed", TOP_NETWORK, rf"{_PHYS} link-speed (?P<v>{_V})", "interfaces[].speed",
        "interface ethernet ethernet1/1 link-speed 1000", _if_text("speed"), note=_MAP_NOTE + _PHYS_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.layer3_ip", TOP_NETWORK, rf"{_PHYS} layer3 ip {_ADDR}", "interfaces[].ip_address / secondary_addresses",
        "interface ethernet ethernet1/1 layer3 ip 192.0.2.1/24", _if_ip, note=_MAP_NOTE + _PHYS_NOTE + _IP_NOTE,
    ),
    Pattern(
        "interfaces.panos.layer3_mtu", TOP_NETWORK, rf"{_PHYS} layer3 mtu (?P<mtu>[0-9]+)", "interfaces[].mtu",
        "interface ethernet ethernet1/1 layer3 mtu 1500", _if_mtu, note=_MAP_NOTE + _PHYS_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.unit_ip", TOP_NETWORK, rf"{_L3UNIT} ip {_ADDR}", "interfaces[].ip_address / secondary_addresses",
        "interface ethernet ethernet1/2 layer3 units ethernet1/2.100 ip 198.51.100.1/24", _if_ip,
        note=_MAP_NOTE + _L3UNIT_NOTE + _IP_NOTE,
    ),
    Pattern(
        "interfaces.panos.unit_tag", TOP_NETWORK, rf"{_L3UNIT} tag (?P<tag>[0-9]+)", "interfaces[].encapsulation",
        "interface ethernet ethernet1/2 layer3 units ethernet1/2.100 tag 100", _if_tag,
        note=_MAP_NOTE + _L3UNIT_NOTE + "原文の `tag <数>` を置く(機種の書き方はそろえない)。" + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.unit_comment", TOP_NETWORK, rf"{_L3UNIT} comment (?P<v>{_V})", "interfaces[].description",
        "interface ethernet ethernet1/2 layer3 units ethernet1/2.100 comment vlan100", _if_text("description"),
        note=_MAP_NOTE + _L3UNIT_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.virtual_unit_ip", TOP_NETWORK, rf"{_VUNIT} ip {_ADDR}", "interfaces[].ip_address / secondary_addresses",
        "interface loopback units loopback.1 ip 192.0.2.10/32", _if_ip, note=_MAP_NOTE + _VUNIT_NOTE + _IP_NOTE,
    ),
    Pattern(
        "interfaces.panos.virtual_unit_comment", TOP_NETWORK, rf"{_VUNIT} comment (?P<v>{_V})", "interfaces[].description",
        "interface loopback units loopback.1 comment router-id", _if_text("description"),
        note=_MAP_NOTE + _VUNIT_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.virtual_unit_mtu", TOP_NETWORK, rf"{_VUNIT} mtu (?P<mtu>[0-9]+)", "interfaces[].mtu",
        "interface tunnel units tunnel.1 mtu 1400", _if_mtu, note=_MAP_NOTE + _VUNIT_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.panos.virtual_ip", TOP_NETWORK, rf"interface (?P<kind>loopback|tunnel|vlan) ip {_ADDR}",
        "interfaces[].ip_address / secondary_addresses", "interface loopback ip 192.0.2.11/32", _if_ip,
        note=_MAP_NOTE + "`interface loopback|tunnel|vlan ip <A>/<長さ>`。名前が種類の語の物理のインターフェースに置く。" + _IP_NOTE,
    ),
    Pattern(
        "static_routes.panos.destination", TOP_NETWORK, rf"{_ROUTE} destination (?P<dest>\S+)", "static_routes[].destination",
        "virtual-router default routing-table ip static-route default-route destination 0.0.0.0/0", _route_destination,
        note=_MAP_NOTE + _ROUTE_NOTE + "ip は IPv4、ipv6 は IPv6 のネットワークとして読めるもの(ホストの部分が 0)だけ。" + _CONFLICT_NOTE,
    ),
    Pattern(
        "static_routes.panos.nexthop", TOP_NETWORK, rf"{_ROUTE} nexthop (?P<kind>ip-address|ipv6-address) (?P<v>\S+)",
        "static_routes[].next_hop", "virtual-router default routing-table ip static-route default-route nexthop ip-address 192.0.2.254",
        _route_nexthop,
        note=_MAP_NOTE + _ROUTE_NOTE + "ip の経路は `ip-address`(IPv4)、ipv6 の経路は `ipv6-address`(IPv6)だけ。読めない値(オブジェクトの名前など)は写さない。"
        + _CONFLICT_NOTE,
    ),
    Pattern(
        "static_routes.panos.interface", TOP_NETWORK, rf"{_ROUTE} interface (?P<v>{_V})", "static_routes[].interface",
        "virtual-router default routing-table ip static-route default-route interface ethernet1/1", _route_interface,
        note=_MAP_NOTE + _ROUTE_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "static_routes.panos.admin_dist", TOP_NETWORK, rf"{_ROUTE} admin-dist (?P<dist>[0-9]+)", "static_routes[].distance",
        "virtual-router default routing-table ip static-route default-route admin-dist 10", _route_distance,
        note=_MAP_NOTE + _ROUTE_NOTE + _CONFLICT_NOTE,
    ),
)

_GENERIC_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vendor_specific.panos_generic.top", SCOPE_TOP_NODE, r".+", _TARGET,
        "deviceconfig", _top,
        note="最上位の語のノード(文の最初の語。暗黙のノードで、同じ語は 1 つ)。鍵 `<最上位の語>` は、文が 1 つ以上入ったとき"
        "(か、残りの語の無い最上位の行が入ったとき)に置く。汎用の文が 0 件の最上位の語(全部が写った・送らない側になった)は鍵ごと置かない",
    ),
    Pattern(
        "vendor_specific.panos_generic.statement", SCOPE_STATEMENT_NODE, r".+", _TARGET + "(.lines)",
        "rulebase security rules R1 action allow", _statement,
        note="文のノード(最上位の語を除いた残りの語を空白 1 つでつないだもの)で、写しの表に当たらなかった文"
        "(写せなかった文を含む)。文の集合(昇順・同じ文字列は 1 つ)に足す。"
        "送らない文(`set <最上位の語> <文>` の判定が「そのまま送る」でないもの)は置かず、未解釈のまま",
    ),
    Pattern(
        "vendor_specific.panos_generic.top_line", SCOPE_TOP_LINE_NODE, r".+", _TARGET,
        "foo", _top_line,
        note="残りの語の無い最上位の語の行(set の `set foo`、brace の `foo;`、XML の `<foo/>`)。実在の行のノード。"
        "鍵を `{\"lines\": []}` で置く。送らない行は置かず、未解釈のまま",
    ),
)

PATTERNS: tuple[Pattern, ...] = _MAP_PATTERNS + _GENERIC_PATTERNS


def _judge_function() -> Any:
    """送らない行の判定(set の行 → 入れてよいか)。判定器は 1 つだけ作る。"""
    # llm_analyzer.masking は config_parser を読み込むため、輪を避けて呼ぶときに読む(junos._judge_function と同じ)
    from driftpilot.llm_analyzer.masking import Masker

    masker = Masker()

    def judge(line: str) -> bool:
        return masker.judge_alone(line, ()).decision == "plain"

    return judge


_ROUTE_DESTINATION = re.compile(_ROUTE + r" destination (?P<dest>\S+)")


def _routes_ready(top_node: FlatNode, judge: Any) -> set[_RouteKey]:
    """destination が読めて、送る側の判定が「そのまま送る」の文を持つ経路の鍵の集合。"""
    ready: set[_RouteKey] = set()
    for node in top_node.children:
        if node.repeated_scope:
            continue
        found = _ROUTE_DESTINATION.fullmatch(node.text)
        if not found:
            continue
        key = _route_key(found)
        if key is None or _destination(key[1], found["dest"]) is None:
            continue
        if judge(f"set {TOP_NETWORK} {node.text}"):
            ready.add(key)
    return ready


def extract(
    tree: FlatTree, interpreted: InterpretedLines
) -> tuple[
    dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
    dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Vlan], dict[str, Acl],
    dict[str, Any], list[DuplicateLine],
]:
    """(interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, global, vlans, acls,
    vendor_specific["paloalto_panos"], 値の食い違い) を返す。bgp・ospf・ipsec・sdwan・route_maps・prefix_lists・ipv6_prefix_lists・vlans・
    acls はいつも空。

    interfaces・static_routes・global は写しの表で写せた文から作る(上の説明)。残りは
    vendor_specific["paloalto_panos"] = {"config": {最上位の語: {"lines": [文の昇順]}}}(上の説明)。
    値の食い違いは、同じ欄に違う値の文が 2 つ以上ある場合(行番号だけ)。同じ最上位の語の下の同じ文の重複は木(panos_tree)が記録する。
    """
    sets: dict[str, set[str]] = {}
    ctx = _Ctx(_judge_function(), sets)
    for top_node in tree.children:
        if top_node.scope_id != SCOPE_TOP:
            continue
        ctx.top = top_node.text
        ctx.node = top_node
        if top_node.line_no is None:
            apply_first(_GENERIC_PATTERNS, SCOPE_TOP_NODE, top_node.text, ctx)
        else:
            pattern = apply_first(_GENERIC_PATTERNS, SCOPE_TOP_LINE_NODE, top_node.text, ctx)
            if pattern is not None:
                interpreted.mark(top_node, pattern.id)
        mapping = top_node.text in _MAP_TOPS
        if top_node.text == TOP_NETWORK:
            ctx.routes_ready = _routes_ready(top_node, ctx.judge)
        for node in top_node.children:
            if node.repeated_scope:
                continue
            ctx.node = node
            pattern = apply_first(_MAP_PATTERNS, top_node.text, node.text, ctx) if mapping else None
            if pattern is None:
                pattern = apply_first(_GENERIC_PATTERNS, SCOPE_STATEMENT_NODE, node.text, ctx)
            if pattern is not None:
                interpreted.mark(node, pattern.id)
    config = {top: {"lines": sorted(lines)} for top, lines in sets.items()}
    mapped = ctx.mapped
    return (
        mapped.interfaces(), mapped.static_routes(), None, {}, None, {}, {}, {}, {}, mapped.global_(), {}, {}, {"config": config},
        mapped.duplicates(),
    )
