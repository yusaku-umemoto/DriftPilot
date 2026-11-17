"""Junos の木(junos_tree。2 段の文の木)を、IR の global・interfaces・static_routes・bgp・route_maps・prefix_lists・acls と
vendor_specific["juniper_junos"] に写す extractor(ADR-0224 決定 6 の 2026-11-17 の追記。REQ-322・REQ-324・REQ-325)。

よく使う文(REQ-324)は共通スキーマに写し、写した文は汎用の形から外す(2 か所の差分にしない)。ほかの文は汎用の形に残る。
文の語の並びで当てる(Si-R の平らな形と同じ考え方)。値は、引用符で囲まれた語(引用符は外す)か、空白を含まない 1 語だけ。
値の後ろに語が続く文は当てない。

- `global`(最上位の語 `system`): `host-name`→hostname、`domain-name`→domain_name、`name-server <アドレス>`→dns.servers
  (正規のアドレス。昇順・重ねない)、`ntp server <アドレス>[ <残り>]`→ntp_servers(出た順・重ねない)。アドレスが読めなければ写さない
- `interfaces`(最上位の語 `interfaces`。文の最初の語が物理の名前): 物理は description・`disable`(shutdown)・mtu。
  `unit <n>` は `<物理>.<n>` の別のインターフェース(parent_interface は物理の名前)で、description・`disable`・`vlan-id <数>`
  (encapsulation に原文)・`family inet address <A>/<長さ>`(行の順の 1 つ目が ip_address、2 つ目以降が secondary_addresses の
  昇順の集合)・`family inet6 address <A>/<長さ>`(ipv6_addresses。正規の書き方の昇順の集合)。
  type は名前で決める(ae→port_channel、lo→loopback、irb・vlan→svi、ほかは routed。論理は物理と同じ)。
  表の文が 1 つでも写せた名前ごとに 1 つ作る。後ろに語のあるアドレス(`vrrp-group …`・`primary` など)は写さない
- `static_routes`(最上位の語 `routing-options`): `static route <宛先> next-hop <アドレス>`(宛先・next-hop とも IPv4)と
  `rib inet6.0 static route <宛先> next-hop <アドレス>`(どちらも IPv6)。宛先はホストの部分が 0 のものだけ。
  `next-hop` ごとに 1 本(行の順。同じ宛先・next-hop は 1 本)。vrf・interface・distance・enabled は None
- `bgp`(最上位の語 `routing-options` と `protocols`。REQ-325): 印の掛かっていない `autonomous-system <数>`(後ろに語が無い。
  1〜4294967295 の 10 進の数だけ)が読めて、`protocols` に印の掛かっていない `bgp …` の文があるときだけ作る(無ければ None で、
  BGP の文・autonomous-system・router-id は汎用の形のまま)。local_as・router_id(IPv4)。`bgp group <名前> …` は
  neighbors[<名前>](kind peer_group。peer-as→remote_as、description、local-address→update_source、`neighbor` で始まらない
  ほかの文→options)、`… neighbor <アドレス> …` は neighbors[<アドレスの正規の書き方>](kind address、peer_group はグループ名。
  同じ欄は相手の直下、ほかの文→options)。`bgp` の直下のほかの文は bgp.options。`authentication-key …` は送らない側のまま
  (未解釈)で、その相手(グループ)の password_configured を真にする(値は持たない)
- `prefix_lists`(最上位の語 `policy-options`。REQ-325): `prefix-list <名前> <プレフィックス>`(`/長さ` のあるものだけ)→entries
  (permit・ge/le/eq は None)、`prefix-list <名前> description <文>`→description。`apply-path …` などは汎用の形のまま
- `route_maps`(最上位の語 `policy-options`。REQ-325): `policy-statement <名前> term <項> from|then <残り>`→項の match・action
  (accept→permit、reject→deny)・set、ほかの項の文→options。項の鍵は「項が文に初めて出た順の番号と項の名前」(`1 t1`)、
  項の無い from・then は鍵 `final`。action の決まらない項は permit に no-final-action
- `acls`(最上位の語 `firewall`。REQ-325): `family inet filter <名前> term <項> <残り>`→項ごとに 1 つの AclEntry
  (seq は項が初めて出た順の番号、text は `term <項>: ` + 項の中の文を `; ` でつないだもの。最後の語だけが違う文が続く間は昇順)。
  `family inet6` などと項の無い文は汎用の形のまま
- 同じ欄(hostname・domain_name・description・mtu・encapsulation・local_as・router_id・相手の欄・prefix-list の description・
  項の action)に違う値の文が 2 つ以上あれば、後の文の値を使い、DuplicateLine((最上位の語,), 行番号)を記録する(値は持たない)

無効の印: 同じ最上位の語の下の `deactivate <経路>`(最上位の語だけなら `deactivate`)が掛かった文(経路そのもの、または
経路 + 空白で始まる文)は写さず、汎用の形に残す。`deactivate …` の文そのものも汎用の形に残る。

写せなかった文(形が違う・値が読めない・送らない側・無効の印の掛かった文)は、先に写しの表を試した後、汎用の表で読む。

- `vendor_specific["juniper_junos"]["config"]` = `{最上位の語: {"lines": [文の昇順。同じ文字列は 1 つ]}}`。
  文は、最上位の語を除いた根からの残りの語を空白 1 つでつないだもの(`ge-0/0/0 unit 0 family inet address 192.0.2.1/24`)。
  `deactivate …` の文も同じ並びに入る。残りの語の無い最上位の語(`set foo`)は `{"lines": []}`
- `lines` は並びを無視して、要素ごとの追加・削除で比べる(core の `_SET_LISTS`)

送らない行は入れない(ADR-0224 決定 6): 写しにも汎用の形にも、文ごとに、`display set` の行の形 `set <最上位の語> <文>` を
Masker().judge_alone(行, ()) で判定し、decision が "plain" でなければ入れず、未解釈のままにする(AI に送る側と同じ判定)。
残りの語の無い最上位の行は `set <最上位の語>`。判定器は利用者が設定で足した語を使わない既定のもの。1 回の extract で 1 つ作る。
汎用の文が 0 件になった最上位の語(全部が写った・送らない側になった)は、鍵ごと置かない。

`set` で始まらない最上位の行(junos_tree の SCOPE_OTHER)と、`delete: `・`insert ` を含む文(SCOPE_UNREAD)は未解釈のまま。
未解釈の行は木の位置(`(最上位の語, 文)`)で IR の `unparsed` に入る。
"""

import re
from dataclasses import dataclass, field
from ipaddress import (
    AddressValueError,
    IPv4Address,
    IPv4Interface,
    IPv4Network,
    IPv6Address,
    IPv6Interface,
    IPv6Network,
    ip_network,
)
from typing import Any, cast

from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree
from driftpilot.config_parser.ir import (
    Acl,
    Bgp,
    BgpNeighbor,
    Global,
    Interface,
    Ipsec,
    Ospf,
    PrefixList,
    RouteMap,
    SdwanBlock,
    StaticRoute,
    Vlan,
    new_bgp,
    new_bgp_neighbor,
    new_global,
    new_interface,
    new_prefix_list,
    new_route_map_entry,
)
from driftpilot.config_parser.junos_tree import DEACTIVATE, SCOPE_TOP, SCOPE_UNREAD
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

SCOPE_TOP_NODE = "top"
SCOPE_STATEMENT_NODE = "statement"
SCOPE_TOP_LINE_NODE = "top_line"

# 写しの表の scope = 最上位の語そのもの
TOP_SYSTEM = "system"
TOP_INTERFACES = "interfaces"
TOP_ROUTING = "routing-options"
TOP_PROTOCOLS = "protocols"
TOP_POLICY = "policy-options"
TOP_FIREWALL = "firewall"
_MAP_TOPS = (TOP_SYSTEM, TOP_INTERFACES, TOP_ROUTING, TOP_PROTOCOLS, TOP_POLICY, TOP_FIREWALL)

# 項が文に初めて出た順の番号を振る単位の種類(policy-statement の項・firewall filter の項)
ORDER_POLICY = "policy"
ORDER_FILTER = "filter"
# 項の無い policy-statement 直下の `from`・`then` をまとめる項の鍵
FINAL_TERM = "final"
NO_FINAL_ACTION = "no-final-action"

# 物理の名前の頭 → type(`irb`・`vlan` は名前そのもの)
_SVI_NAMES = ("irb", "vlan")


@dataclass
class _Entry:
    """1 つのインターフェースについて、写せた文から集めた値。values は欄 → (値, 行番号)。"""

    first: int
    order: int
    values: dict[str, tuple[Any, int | None]] = field(default_factory=dict)
    conflicts: dict[str, set[int]] = field(default_factory=dict)
    addresses: list[IPv4Interface] = field(default_factory=list)
    ipv6_addresses: set[str] = field(default_factory=set)


class _Fields:
    """1 つのまとまり(BGP の相手・policy の項・prefix-list など)の欄ごとの値(値, 行番号)と食い違い。top は食い違いの記録に使う最上位の語。"""

    def __init__(self, top: str) -> None:
        self.top = top
        self.values: dict[str, tuple[Any, int | None]] = {}
        self.conflicts: dict[str, set[int]] = {}

    def write(self, key: str, value: Any, line: int | None) -> None:
        _Mapped._write(self.values, self.conflicts, key, value, line)

    def get(self, key: str) -> Any:
        found = self.values.get(key)
        return found[0] if found is not None else None

    def duplicates(self) -> list[DuplicateLine]:
        return [DuplicateLine((self.top,), tuple(sorted(lines))) for lines in self.conflicts.values()]


@dataclass
class _Neighbor:
    """BGP の相手 1 つ(グループも)。options は昇順の集合にして出す。"""

    kind: str
    fields: _Fields = field(default_factory=lambda: _Fields(TOP_PROTOCOLS))
    options: set[str] = field(default_factory=set)
    password: bool = False


@dataclass
class _Term:
    """policy-statement の項 1 つ。"""

    fields: _Fields = field(default_factory=lambda: _Fields(TOP_POLICY))
    match: set[str] = field(default_factory=set)
    sets: set[str] = field(default_factory=set)
    options: set[str] = field(default_factory=set)


@dataclass
class _PrefixList:
    fields: _Fields = field(default_factory=lambda: _Fields(TOP_POLICY))
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass
class _FilterTerm:
    """firewall filter の項 1 つ。seq は項が文に初めて出た順の番号、rests は項の中の文(出た順)。"""

    seq: int
    rests: list[str] = field(default_factory=list)


class _Mapped:
    """写しの表で写せた文を集める。global の値・インターフェースの名前ごとの値・経路(行番号つき)・BGP・policy・prefix-list・filter。"""

    def __init__(self) -> None:
        self.entries: dict[str, _Entry] = {}
        self.routes: list[tuple[int, int, StaticRoute]] = []
        self.global_values: dict[str, tuple[Any, int | None]] = {}
        self.global_conflicts: dict[str, set[int]] = {}
        self.dns_servers: set[str] = set()
        self.ntp_servers: list[str] = []
        # routing-options の local_as・router_id
        self.routing = _Fields(TOP_ROUTING)
        self.bgp_options: set[str] = set()
        self.neighbors: dict[str, _Neighbor] = {}
        self.policies: dict[str, dict[str, _Term]] = {}
        self.prefix_lists: dict[str, _PrefixList] = {}
        self.filters: dict[str, dict[str, _FilterTerm]] = {}

    def group(self, name: str) -> _Neighbor:
        found = self.neighbors.get(name)
        if found is None:
            found = self.neighbors[name] = _Neighbor("peer_group")
        return found

    def neighbor(self, address: str, group: str, line: int | None) -> _Neighbor:
        """相手(鍵はアドレスの正規の書き方)。グループも、無ければ作る。"""
        self.group(group)
        found = self.neighbors.get(address)
        if found is None:
            found = self.neighbors[address] = _Neighbor("address")
        found.fields.write("peer_group", group, line)
        return found

    def term(self, policy: str, key: str) -> _Term:
        terms = self.policies.setdefault(policy, {})
        found = terms.get(key)
        if found is None:
            found = terms[key] = _Term()
        return found

    def prefix_list(self, name: str) -> _PrefixList:
        found = self.prefix_lists.get(name)
        if found is None:
            found = self.prefix_lists[name] = _PrefixList()
        return found

    def filter_term(self, name: str, term: str, seq: int) -> _FilterTerm:
        terms = self.filters.setdefault(name, {})
        found = terms.get(term)
        if found is None:
            found = terms[term] = _FilterTerm(seq)
        return found

    def bgp(self) -> Bgp | None:
        """local_as が読めたときだけ作る(BGP の文があるかは extract が先に調べる)。"""
        local_as = self.routing.get("local_as")
        if local_as is None:
            return None
        out = new_bgp(local_as)
        out["router_id"] = self.routing.get("router_id")
        out["options"] = sorted(self.bgp_options)
        for key, item in self.neighbors.items():
            neighbor: BgpNeighbor = new_bgp_neighbor(cast(Any, item.kind))
            for name in ("remote_as", "peer_group", "description", "update_source"):
                neighbor[name] = item.fields.get(name)  # type: ignore[literal-required]
            neighbor["password_configured"] = item.password
            neighbor["options"] = sorted(item.options)
            out["neighbors"][key] = neighbor
        return out

    def route_maps(self) -> dict[str, RouteMap]:
        out: dict[str, RouteMap] = {}
        for name, terms in self.policies.items():
            entries = {}
            for key, term in terms.items():
                action = term.fields.get("action")
                entry = new_route_map_entry(action or "permit")
                options = set(term.options)
                if action is None:
                    options.add(NO_FINAL_ACTION)
                entry["match"] = sorted(term.match)
                entry["set"] = sorted(term.sets)
                entry["options"] = sorted(options)
                entries[key] = entry
            out[name] = {"entries": entries}
        return out

    def prefix_lists_ir(self) -> dict[str, PrefixList]:
        out: dict[str, PrefixList] = {}
        for name, item in self.prefix_lists.items():
            plist = new_prefix_list()
            plist["description"] = item.fields.get("description")
            plist["entries"] = cast(Any, dict(item.entries))
            out[name] = plist
        return out

    def acls(self) -> dict[str, Acl]:
        out: dict[str, Acl] = {}
        for name, terms in self.filters.items():
            entries = []
            for term, item in sorted(terms.items(), key=lambda pair: pair[1].seq):
                text = f"term {term}:"
                if item.rests:
                    text += " " + "; ".join(_sorted_runs(item.rests))
                entries.append({"seq": item.seq, "text": text})
            out[name] = {"name": name, "type": "extended", "numbered": False, "entries": cast(Any, entries)}
        return out

    def entry(self, name: str, line: int | None) -> _Entry:
        found = self.entries.get(name)
        if found is None:
            found = self.entries[name] = _Entry(line or 0, len(self.entries))
        elif line is not None:
            found.first = min(found.first, line) if found.first else line
        return found

    @staticmethod
    def _write(
        values: dict[str, tuple[Any, int | None]], conflicts: dict[str, set[int]], key: str, value: Any, line: int | None
    ) -> None:
        """欄に値を書く。前の値と違えば、両方の行番号を食い違いとして記録し、後の値を使う(yamaha・fujitsu_sir と同じ)。"""
        previous = values.get(key)
        if previous is not None and previous[0] != value and previous[1] is not None and line is not None:
            conflicts.setdefault(key, {previous[1]}).add(line)
        values[key] = (value, line)

    def write(self, name: str, key: str, value: Any, line: int | None) -> None:
        entry = self.entry(name, line)
        self._write(entry.values, entry.conflicts, key, value, line)

    def write_global(self, key: str, value: Any, line: int | None) -> None:
        self._write(self.global_values, self.global_conflicts, key, value, line)

    def interfaces(self) -> dict[str, Interface]:
        """名前は最初に写せた文の順。"""
        out: dict[str, Interface] = {}
        for name, entry in sorted(self.entries.items(), key=lambda item: (item[1].first, item[1].order)):
            interface = new_interface(name)
            physical = name.split(".", 1)[0]
            interface["type"] = _interface_type(physical)
            for key in ("description", "mtu", "encapsulation"):
                if key in entry.values:
                    interface[key] = entry.values[key][0]
            interface["shutdown"] = "shutdown" in entry.values
            if entry.addresses:
                interface["ip_address"] = entry.addresses[0]
            interface["secondary_addresses"] = sorted(set(entry.addresses[1:]) - {entry.addresses[0]}) if entry.addresses else []
            interface["ipv6_addresses"] = sorted(entry.ipv6_addresses)
            out[name] = interface
        return out

    def static_routes(self) -> list[StaticRoute]:
        out: list[StaticRoute] = []
        seen: set[tuple[Any, Any]] = set()
        for _, _, route in sorted(self.routes, key=lambda item: (item[0], item[1])):
            key = (route["destination"], route["next_hop"])
            if key not in seen:
                seen.add(key)
                out.append(route)
        return out

    def global_(self) -> Global:
        out = new_global()
        for key in ("hostname", "domain_name"):
            if key in self.global_values:
                out[key] = self.global_values[key][0]
        out["dns"]["servers"] = sorted(self.dns_servers)
        out["ntp_servers"] = list(self.ntp_servers)
        return out

    def duplicates(self) -> list[DuplicateLine]:
        """値の食い違い(最上位の語と行番号だけ。値は持たない)。"""
        found = [DuplicateLine((TOP_SYSTEM,), tuple(sorted(lines))) for lines in self.global_conflicts.values()]
        for entry in self.entries.values():
            found += [DuplicateLine((TOP_INTERFACES,), tuple(sorted(lines))) for lines in entry.conflicts.values()]
        found += self.routing.duplicates()
        for neighbor in self.neighbors.values():
            found += neighbor.fields.duplicates()
        for terms in self.policies.values():
            for term in terms.values():
                found += term.fields.duplicates()
        for plist in self.prefix_lists.values():
            found += plist.fields.duplicates()
        return found


def _interface_type(physical: str) -> str:
    if physical.startswith("ae"):
        return "port_channel"
    if physical.startswith("lo"):
        return "loopback"
    if physical in _SVI_NAMES:
        return "svi"
    return "routed"


class _Ctx:
    """最上位の語の文を読んでいる間の状態。judge は送らない行を判定する関数(display set の行 → 入れてよいか)。

    node は今読んでいる文のノード(無ければ行番号は None)、mapped は写しの表で写せた文の集め先。
    """

    def __init__(self, judge: Any, sets: dict[str, set[str]]) -> None:
        self.judge = judge
        self.sets = sets
        self.top = ""
        self.mapped = _Mapped()
        self.node: FlatNode | None = None
        # BGP を写すか(extract が先に調べて決め直す。例のテストのため既定は真)
        self.bgp_ready = True
        # 項の番号(単位 → 項の名前 → 初めて出た順の番号。1 始まり)
        self.orders: dict[tuple[str, str], dict[str, int]] = {}

    def order(self, kind: str, name: str, term: str) -> int:
        """policy または filter の中で、項が文に初めて出た順の番号(まだなら振る)。"""
        terms = self.orders.setdefault((kind, name), {})
        if term not in terms:
            terms[term] = len(terms) + 1
        return terms[term]

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


# --- 写しの表(REQ-324)。値を読み、送らない文の判定をして、最後に書く ---


def _unquote(word: str) -> str | None:
    """引用符で囲まれていれば外す。空になる値は None(写さない)。"""
    if len(word) >= 2 and word[0] == '"' and word[-1] == '"':
        word = word[1:-1]
    return word or None


def _plain(top: str, m: re.Match[str], ctx: _Ctx) -> bool:
    """送らない行の判定(`set <最上位の語> <文>`)。汎用の形と同じ判定を、写す前に当てる。"""
    return bool(ctx.judge(f"set {top} {m.string}"))


def _hostname(key: str, m: re.Match[str], ctx: _Ctx) -> bool:
    value = _unquote(m["v"])
    if value is None or not _plain(TOP_SYSTEM, m, ctx):
        return False
    ctx.mapped.write_global(key, value, ctx.line_no)
    return True


def _ip_address(word: str) -> IPv4Address | IPv6Address | None:
    try:
        return IPv4Address(word) if ":" not in word else IPv6Address(word)
    except (AddressValueError, ValueError):
        return None


def _name_server(m: re.Match[str], ctx: _Ctx) -> bool:
    address = _ip_address(_unquote(m["v"]) or "")
    if address is None or not _plain(TOP_SYSTEM, m, ctx):
        return False
    ctx.mapped.dns_servers.add(str(address))
    return True


def _ntp_server(m: re.Match[str], ctx: _Ctx) -> bool:
    address = _ip_address(_unquote(m["addr"]) or "")
    if address is None or not _plain(TOP_SYSTEM, m, ctx):
        return False
    value = f"{address} {m['rest']}" if m["rest"] else str(address)
    if value not in ctx.mapped.ntp_servers:
        ctx.mapped.ntp_servers.append(value)
    return True


def _if_name(m: re.Match[str]) -> str:
    """物理の名前。`unit <n>` の文は `<物理>.<n>`(論理のインターフェース)。"""
    return f"{m['name']}.{m['unit']}" if m.groupdict().get("unit") is not None else m["name"]


def _if_description(m: re.Match[str], ctx: _Ctx) -> bool:
    value = _unquote(m["v"])
    if value is None or not _plain(TOP_INTERFACES, m, ctx):
        return False
    ctx.mapped.write(_if_name(m), "description", value, ctx.line_no)
    return True


def _if_disable(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(TOP_INTERFACES, m, ctx):
        return False
    ctx.mapped.write(_if_name(m), "shutdown", True, ctx.line_no)
    return True


def _if_mtu(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(TOP_INTERFACES, m, ctx):
        return False
    ctx.mapped.write(_if_name(m), "mtu", int(m["mtu"]), ctx.line_no)
    return True


def _if_vlan_id(m: re.Match[str], ctx: _Ctx) -> bool:
    if not _plain(TOP_INTERFACES, m, ctx):
        return False
    ctx.mapped.write(_if_name(m), "encapsulation", f"vlan-id {m['vlan']}", ctx.line_no)
    return True


def _if_inet_address(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        address = IPv4Interface(m["addr"])
    except ValueError:
        return False
    if not _plain(TOP_INTERFACES, m, ctx):
        return False
    entry = ctx.mapped.entry(_if_name(m), ctx.line_no)
    if address not in entry.addresses:
        entry.addresses.append(address)
    return True


def _if_inet6_address(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        address = IPv6Interface(m["addr"])
    except ValueError:
        return False
    if not _plain(TOP_INTERFACES, m, ctx):
        return False
    ctx.mapped.entry(_if_name(m), ctx.line_no).ipv6_addresses.add(str(address))
    return True


def _route(version: int, m: re.Match[str], ctx: _Ctx) -> bool:
    destination: IPv4Network | IPv6Network
    next_hop: IPv4Address | IPv6Address
    try:
        word, _, length = m["dest"].partition("/")
        if not length.isdecimal():
            return False
        # strict=True: ホストの部分が 0 でない宛先は読まない(汎用の形のまま)
        if version == 4:
            destination = IPv4Network(f"{word}/{length}", strict=True)
            next_hop = IPv4Address(m["nh"])
        else:
            destination = IPv6Network(f"{word}/{length}", strict=True)
            next_hop = IPv6Address(m["nh"])
    except (AddressValueError, ValueError):
        return False
    if not _plain(TOP_ROUTING, m, ctx):
        return False
    route: StaticRoute = {
        "destination": destination,
        "next_hop": next_hop,
        "interface": None,
        "vrf": None,
        "distance": None,
        "enabled": None,
    }
    ctx.mapped.routes.append((ctx.line_no or 0, len(ctx.mapped.routes), route))
    return True


# --- BGP・policy-statement・prefix-list・firewall filter(REQ-325) ---


def _as_number(word: str) -> int | None:
    """AS 番号。1〜4294967295 の 10 進の数だけ(asdot の `上位.下位` は読まない。FortiGate と同じ)。"""
    if not word.isascii() or not word.isdecimal():
        return None
    number = int(word)
    return number if 1 <= number <= 4294967295 else None


def _address_text(word: str) -> str | None:
    address = _ip_address(_unquote(word) or "")
    return None if address is None else str(address)


def _is_auth_key(rest: str) -> bool:
    """`authentication-key …`(伏せる判定で送らない側。`authentication-key-chain` は別の語)。"""
    return rest == "authentication-key" or rest.startswith("authentication-key ")


def _local_as(m: re.Match[str], ctx: _Ctx) -> bool:
    number = _as_number(m["v"])
    if not ctx.bgp_ready or number is None or not _plain(TOP_ROUTING, m, ctx):
        return False
    ctx.mapped.routing.write("local_as", number, ctx.line_no)
    return True


def _router_id(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        address = IPv4Address(m["v"])
    except (AddressValueError, ValueError):
        return False
    if not ctx.bgp_ready or not _plain(TOP_ROUTING, m, ctx):
        return False
    ctx.mapped.routing.write("router_id", str(address), ctx.line_no)
    return True


def _bgp_target(m: re.Match[str], ctx: _Ctx) -> _Neighbor | None:
    """文が掛かるグループか相手(`neighbor <アドレス>` があれば相手。アドレスが読めなければ None)。BGP を写さないときも None。"""
    if not ctx.bgp_ready or not _plain(TOP_PROTOCOLS, m, ctx):
        return None
    groups = m.groupdict()
    if groups.get("a") is None:
        return ctx.mapped.group(m["g"])
    address = _ip_address(m["a"])
    if address is None:
        return None
    return ctx.mapped.neighbor(str(address), m["g"], ctx.line_no)


def _bgp_value(key: str, parse: Any) -> Any:
    def apply(m: re.Match[str], ctx: _Ctx) -> bool:
        value = parse(m["v"])
        if value is None:
            return False
        target = _bgp_target(m, ctx)
        if target is None:
            return False
        target.fields.write(key, value, ctx.line_no)
        return True

    return apply


def _bgp_option(m: re.Match[str], ctx: _Ctx) -> bool:
    """グループ・相手・bgp の直下のほかの文(`authentication-key …` は写さない)。"""
    rest = m["rest"]
    if _is_auth_key(rest):
        return False
    if m.groupdict().get("g") is None:
        if not ctx.bgp_ready or not _plain(TOP_PROTOCOLS, m, ctx):
            return False
        ctx.mapped.bgp_options.add(rest)
        return True
    target = _bgp_target(m, ctx)
    if target is None:
        return False
    target.options.add(rest)
    return True


def _bgp_node(m: re.Match[str], ctx: _Ctx) -> bool:
    """`bgp group <名前>`・`bgp group <名前> neighbor <アドレス>`・`bgp` だけの文。"""
    if m.groupdict().get("g") is None:
        return ctx.bgp_ready and _plain(TOP_PROTOCOLS, m, ctx)
    return _bgp_target(m, ctx) is not None


def _pl_description(m: re.Match[str], ctx: _Ctx) -> bool:
    value = _unquote(m["v"])
    if value is None or not _plain(TOP_POLICY, m, ctx):
        return False
    ctx.mapped.prefix_list(m["name"]).fields.write("description", value, ctx.line_no)
    return True


def _pl_entry(m: re.Match[str], ctx: _Ctx) -> bool:
    word, slash, length = m["p"].partition("/")
    if not slash or not length.isdecimal() or not length.isascii():
        return False
    try:
        network = ip_network(f"{word}/{length}", strict=False)
    except ValueError:
        return False
    if not _plain(TOP_POLICY, m, ctx):
        return False
    key = str(network)
    ctx.mapped.prefix_list(m["name"]).entries[key] = {"action": "permit", "prefix": key, "ge": None, "le": None, "eq": None}
    return True


def _policy_term(m: re.Match[str], ctx: _Ctx) -> _Term | None:
    """文が掛かる項。`term <項>` が無ければ最後の動作(鍵 final)。送らない文なら None。"""
    if not _plain(TOP_POLICY, m, ctx):
        return None
    if m.groupdict().get("t") is None:
        return ctx.mapped.term(m["name"], FINAL_TERM)
    number = ctx.order(ORDER_POLICY, m["name"], m["t"])
    return ctx.mapped.term(m["name"], f"{number} {m['t']}")


def _policy_node(m: re.Match[str], ctx: _Ctx) -> bool:
    """`policy-statement <名前>`(項の無いまま)と `… term <項>` だけの文。"""
    if m.groupdict().get("t") is None:
        if not _plain(TOP_POLICY, m, ctx):
            return False
        ctx.mapped.policies.setdefault(m["name"], {})
        return True
    return _policy_term(m, ctx) is not None


def _policy_from(m: re.Match[str], ctx: _Ctx) -> bool:
    term = _policy_term(m, ctx)
    if term is None:
        return False
    term.match.add(m["rest"])
    return True


def _policy_then(m: re.Match[str], ctx: _Ctx) -> bool:
    term = _policy_term(m, ctx)
    if term is None:
        return False
    rest = m["rest"]
    if rest in ("accept", "reject"):
        term.fields.write("action", "permit" if rest == "accept" else "deny", ctx.line_no)
    else:
        term.sets.add(rest)
    return True


def _policy_option(m: re.Match[str], ctx: _Ctx) -> bool:
    term = _policy_term(m, ctx)
    if term is None:
        return False
    term.options.add(m["rest"])
    return True


def _filter_term(m: re.Match[str], ctx: _Ctx) -> bool:
    """`family inet filter <名前> term <項> [<残り>]`。残りの無い文は項だけを作る。"""
    if not _plain(TOP_FIREWALL, m, ctx):
        return False
    item = ctx.mapped.filter_term(m["name"], m["t"], ctx.order(ORDER_FILTER, m["name"], m["t"]))
    rest = m.groupdict().get("rest")
    if rest is not None:
        item.rests.append(rest)
    return True


def _sorted_runs(rests: list[str]) -> list[str]:
    """文に出た順のまま。ただし、最後の語だけが違う文が続く間(brace の `[ a b ]` を開いた形)は、その中だけを昇順に並べる。

    brace の木は `[ ]` の要素を昇順に開き、set は書いた順に出すので、同じ config の 2 つの形で ACL の text が食い違わないようにする。
    """
    out: list[str] = []
    index = 0
    while index < len(rests):
        end = index + 1
        head = rests[index].rpartition(" ")[0]
        while head and end < len(rests) and rests[end].rpartition(" ")[0] == head:
            end += 1
        out.extend(sorted(rests[index:end]))
        index = end
    return out


_V = r'"(?:[^"\\]|\\.)*"|[^\s"]\S*'
_NAME = r"(?P<name>[^\s\"]+)"
_GROUP = r"(?P<g>[^\s\"]+)"
_PEER = r"(?P<a>[^\s\"]+)"
_TERM = r"(?P<t>[^\s\"]+)"
_REST = r"(?P<rest>\S.*)"
_UNIT = rf"{_NAME} unit (?P<unit>\d+)"
_ADDR = r"(?P<addr>[^\s/]+/\d+)"
_MAP_NOTE = (
    "手続き。値が読めない文・送らない文(AI に送る側の判定が「そのまま送る」でない文)・無効の印(`deactivate <経路>`)の掛かった文は"
    "写さず、汎用の形に残す(未解釈のままの文もある)。値は、引用符で囲まれた語(引用符は外す)か空白を含まない 1 語。"
    "値の後ろに語が続く文は当てない。"
)
_CONFLICT_NOTE = "同じ欄に違う値の文が 2 つ以上あれば、後の文の値を使い、食い違いを記録する(値は持たない)。"
_IF_NOTE = "文の最初の語がインターフェースの名前(形は問わない)。"
_UNIT_NOTE = "`unit <n>` は `<物理の名前>.<n>` の別のインターフェース(parent_interface は物理の名前、type は物理と同じ)。"
_ROUTE_NOTE = (
    "宛先はネットワークとして読めるもの(ホストの部分が 0)だけ。`next-hop` ごとに 1 本(同じ宛先の `next-hop` の文が 2 つなら 2 本)。"
    "vrf・interface・distance・enabled は None。`discard`・`reject`・`preference`・`qualified-next-hop` などほかの文は写さない"
)

# 並び＝優先順位。写しの表(global・interfaces・static_routes)が先、汎用の表が後。extract は 2 段で読む
_MAP_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "global.junos.hostname", TOP_SYSTEM, rf"host-name (?P<v>{_V})", "global.hostname", "host-name r1-test",
        lambda m, ctx: _hostname("hostname", m, ctx), note=_MAP_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "global.junos.domain_name", TOP_SYSTEM, rf"domain-name (?P<v>{_V})", "global.domain_name", "domain-name example.test",
        lambda m, ctx: _hostname("domain_name", m, ctx), note=_MAP_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "global.junos.name_server", TOP_SYSTEM, rf"name-server (?P<v>{_V})", "global.dns.servers", "name-server 192.0.2.53",
        _name_server, note=_MAP_NOTE + "正規のアドレスの文字列を、昇順・重ねずに置く。アドレスとして読めない文は写さない",
    ),
    Pattern(
        "global.junos.ntp_server", TOP_SYSTEM, rf"ntp server (?P<addr>{_V})(?: (?P<rest>\S.*))?", "global.ntp_servers",
        "ntp server 192.0.2.123 prefer", _ntp_server,
        note=_MAP_NOTE + "`<アドレス>[ <残り>]`(アドレスは正規の書き方。後ろが無ければアドレスだけ)を、出た順に重ねずに置く。"
        "アドレスとして読めない文は写さない",
    ),
    Pattern(
        "interfaces.junos.description", TOP_INTERFACES, rf"{_NAME} description (?P<v>{_V})", "interfaces[].description",
        "ge-0/0/0 description uplink", _if_description, note=_MAP_NOTE + _IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.junos.disable", TOP_INTERFACES, rf"{_NAME} disable", "interfaces[].shutdown", "ge-0/0/2 disable", _if_disable,
        note=_MAP_NOTE + _IF_NOTE + "文があれば shutdown が真(無ければ偽)",
    ),
    Pattern(
        "interfaces.junos.mtu", TOP_INTERFACES, rf"{_NAME} mtu (?P<mtu>\d+)", "interfaces[].mtu", "ge-0/0/0 mtu 9192", _if_mtu,
        note=_MAP_NOTE + _IF_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.junos.unit_description", TOP_INTERFACES, rf"{_UNIT} description (?P<v>{_V})", "interfaces[].description",
        "ge-0/0/0 unit 0 description link-a", _if_description, note=_MAP_NOTE + _UNIT_NOTE + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.junos.unit_disable", TOP_INTERFACES, rf"{_UNIT} disable", "interfaces[].shutdown", "ge-0/0/2 unit 0 disable",
        _if_disable, note=_MAP_NOTE + _UNIT_NOTE + "文があれば shutdown が真(無ければ偽)",
    ),
    Pattern(
        "interfaces.junos.unit_vlan_id", TOP_INTERFACES, rf"{_UNIT} vlan-id (?P<vlan>\d+)", "interfaces[].encapsulation",
        "ge-0/0/3 unit 100 vlan-id 100", _if_vlan_id,
        note=_MAP_NOTE + _UNIT_NOTE + "原文の `vlan-id <数>` を置く(機種の書き方はそろえない)。" + _CONFLICT_NOTE,
    ),
    Pattern(
        "interfaces.junos.unit_inet_address", TOP_INTERFACES, rf"{_UNIT} family inet address {_ADDR}",
        "interfaces[].ip_address / secondary_addresses", "ge-0/0/0 unit 0 family inet address 192.0.2.1/24", _if_inet_address,
        note=_MAP_NOTE + _UNIT_NOTE + "`<A>/<長さ>` が IPv4 のインターフェースとして読めるものだけ。行の順の 1 つ目が ip_address、"
        "2 つ目以降(重ねない)が secondary_addresses(昇順)。後ろに語が続く形(`vrrp-group …`・`primary`・`preferred`)は写さない",
    ),
    Pattern(
        "interfaces.junos.unit_inet6_address", TOP_INTERFACES, rf"{_UNIT} family inet6 address {_ADDR}",
        "interfaces[].ipv6_addresses", "ge-0/0/0 unit 0 family inet6 address 2001:db8::1/64", _if_inet6_address,
        note=_MAP_NOTE + _UNIT_NOTE + "`<A>/<長さ>` が IPv6 のインターフェースとして読めるものだけ。IPv6Interface の正規の書き方の"
        "昇順の集合。後ろに語が続く形は写さない",
    ),
    Pattern(
        "static_routes.junos.route", TOP_ROUTING, r"static route (?P<dest>\S+) next-hop (?P<nh>\S+)", "static_routes[]",
        "static route 0.0.0.0/0 next-hop 192.0.2.254", lambda m, ctx: _route(4, m, ctx),
        note=_MAP_NOTE + "宛先と next-hop がどちらも IPv4 のときだけ。" + _ROUTE_NOTE,
    ),
    Pattern(
        "static_routes.junos.inet6_route", TOP_ROUTING, r"rib inet6\.0 static route (?P<dest>\S+) next-hop (?P<nh>\S+)",
        "static_routes[]", "rib inet6.0 static route 2001:db8:1::/48 next-hop 2001:db8::1", lambda m, ctx: _route(6, m, ctx),
        note=_MAP_NOTE + "宛先と next-hop がどちらも IPv6 のときだけ。" + _ROUTE_NOTE,
    ),
)

_BGP_NOTE = (
    "手続き。値が読めない文・送らない文・無効の印(`deactivate <経路>`)の掛かった文・BGP を写さない config(最上位の `routing-options` の"
    "`autonomous-system <数>` が無いか読めない、`protocols` に `bgp` の文が無い)の文は写さず、汎用の形に残す。"
)
_GROUP_NOTE = "`bgp group <名前> …`。neighbors[<グループ名>](kind は peer_group)に写す。`neighbor` で始まる文は相手の文。"
_NEIGHBOR_NOTE = (
    "`bgp group <名前> neighbor <アドレス> …`。neighbors[<アドレスの正規の書き方>](kind は address、peer_group はグループ名)に写す。"
    "アドレスとして読めない文は写さない。"
)
_AS_NOTE = "AS 番号は 1〜4294967295 の 10 進の数だけ(asdot は読まない)。"
_SAME_NOTE = "同じ欄に違う値の文が 2 つ以上あれば、後の文の値を使い、食い違いを記録する(値は持たない)。"
_POLICY_NOTE = (
    "手続き。値が読めない文・送らない文・無効の印の掛かった文は写さず、汎用の形に残す。項の鍵は、項が文に初めて出た順の番号(1 始まり。"
    "無効の印の掛かった項・送らない文も数える)と項の名前を空白でつないだもの。項の無い `from`・`then` は鍵 `final` の項にまとめる。"
    "`accept` の無い・`reject` の無い項は action を permit にして options に `no-final-action` を入れる。"
)

# BGP・prefix-list・policy-statement・firewall filter の写し(REQ-325)。routing-options・protocols・policy-options・firewall の文
_EXT_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "bgp.junos.local_as", TOP_ROUTING, r"autonomous-system (?P<v>\S+)", "bgp.local_as", "autonomous-system 64496", _local_as,
        note=_BGP_NOTE + _AS_NOTE + "後ろに語が続く形(`loops <数>` など)は写さない。" + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.router_id", TOP_ROUTING, r"router-id (?P<v>\S+)", "bgp.router_id", "router-id 192.0.2.1", _router_id,
        note=_BGP_NOTE + "IPv4 のアドレスだけ。BGP を写さない config では、この文は汎用の形のまま。" + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.group_peer_as", TOP_PROTOCOLS, rf"bgp group {_GROUP} peer-as (?P<v>\S+)", "bgp.neighbors[].remote_as",
        "bgp group IBGP peer-as 64496", _bgp_value("remote_as", _as_number), note=_BGP_NOTE + _GROUP_NOTE + _AS_NOTE + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.group_description", TOP_PROTOCOLS, rf"bgp group {_GROUP} description (?P<v>{_V})", "bgp.neighbors[].description",
        'bgp group IBGP description "core peers"', _bgp_value("description", _unquote),
        note=_BGP_NOTE + _GROUP_NOTE + "引用符で囲まれていれば外す。" + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.group_local_address", TOP_PROTOCOLS, rf"bgp group {_GROUP} local-address (?P<v>\S+)",
        "bgp.neighbors[].update_source", "bgp group IBGP local-address 192.0.2.1", _bgp_value("update_source", _address_text),
        note=_BGP_NOTE + _GROUP_NOTE + "アドレスとして読めない文は写さない(正規の書き方で置く)。" + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.group_option", TOP_PROTOCOLS, rf"bgp group {_GROUP} (?!neighbor(?: |$)){_REST}", "bgp.neighbors[].options",
        "bgp group IBGP type internal", _bgp_option,
        note=_BGP_NOTE + _GROUP_NOTE + "`group <名前>` の後ろの残りを options(昇順の集合)に入れる。`authentication-key …` は写さない"
        "(伏せる判定で送らない側。その文があれば password_configured を真にする。値は持たない)",
    ),
    Pattern(
        "bgp.junos.group", TOP_PROTOCOLS, rf"bgp group {_GROUP}", "bgp.neighbors[]", "bgp group IBGP", _bgp_node,
        note=_BGP_NOTE + _GROUP_NOTE + "`bgp group <名前>` だけの文もグループを作る",
    ),
    Pattern(
        "bgp.junos.neighbor_peer_as", TOP_PROTOCOLS, rf"bgp group {_GROUP} neighbor {_PEER} peer-as (?P<v>\S+)",
        "bgp.neighbors[].remote_as", "bgp group EBGP neighbor 198.51.100.1 peer-as 64497", _bgp_value("remote_as", _as_number),
        note=_BGP_NOTE + _NEIGHBOR_NOTE + _AS_NOTE + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.neighbor_description", TOP_PROTOCOLS, rf"bgp group {_GROUP} neighbor {_PEER} description (?P<v>{_V})",
        "bgp.neighbors[].description", 'bgp group EBGP neighbor 198.51.100.1 description "peer-a"',
        _bgp_value("description", _unquote), note=_BGP_NOTE + _NEIGHBOR_NOTE + "引用符で囲まれていれば外す。" + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.neighbor_local_address", TOP_PROTOCOLS, rf"bgp group {_GROUP} neighbor {_PEER} local-address (?P<v>\S+)",
        "bgp.neighbors[].update_source", "bgp group EBGP neighbor 198.51.100.1 local-address 198.51.100.2",
        _bgp_value("update_source", _address_text),
        note=_BGP_NOTE + _NEIGHBOR_NOTE + "正規の書き方で置く。" + _SAME_NOTE,
    ),
    Pattern(
        "bgp.junos.neighbor_option", TOP_PROTOCOLS, rf"bgp group {_GROUP} neighbor {_PEER} {_REST}", "bgp.neighbors[].options",
        "bgp group EBGP neighbor 198.51.100.1 export EXPORT-A", _bgp_option,
        note=_BGP_NOTE + _NEIGHBOR_NOTE + "`neighbor <アドレス>` の後ろの残りを options(昇順の集合)に入れる。`authentication-key …` は写さない"
        "(伏せる判定で送らない側。その文があれば password_configured を真にする。値は持たない)",
    ),
    Pattern(
        "bgp.junos.neighbor", TOP_PROTOCOLS, rf"bgp group {_GROUP} neighbor {_PEER}", "bgp.neighbors[]",
        "bgp group EBGP neighbor 198.51.100.1", _bgp_node, note=_BGP_NOTE + _NEIGHBOR_NOTE + "`neighbor <アドレス>` だけの文も相手を作る",
    ),
    Pattern(
        "bgp.junos.option", TOP_PROTOCOLS, rf"bgp (?!group(?: |$)){_REST}", "bgp.options", "bgp log-updown", _bgp_option,
        note=_BGP_NOTE + "`bgp` の直下の `group` で始まらない文の残りを bgp.options(昇順の集合)に入れる。`authentication-key …` は写さない",
    ),
    Pattern(
        "bgp.junos.bgp", TOP_PROTOCOLS, r"bgp", "bgp", "bgp", _bgp_node, note=_BGP_NOTE + "`bgp` だけの文(中身が空の BGP)も bgp を作る",
    ),
    Pattern(
        "prefix_lists.junos.description", TOP_POLICY, rf"prefix-list {_NAME} description (?P<v>{_V})", "prefix_lists[].description",
        'prefix-list PL-A description "internal"', _pl_description,
        note="手続き。値が読めない文・送らない文・無効の印の掛かった文は写さず、汎用の形に残す。引用符で囲まれていれば外す。" + _SAME_NOTE,
    ),
    Pattern(
        "prefix_lists.junos.entry", TOP_POLICY, rf"prefix-list {_NAME} (?P<p>[^\s\"]+)", "prefix_lists[].entries[]",
        "prefix-list PL-A 192.0.2.0/24", _pl_entry,
        note="手続き。`<アドレス>/<長さ>` として読めるもの(IPv4・IPv6 とも。`/` の無い語は写さない)だけ。鍵は正規の書き方"
        "(str(ip_network(…, strict=False)))。action は permit、ge・le・eq は None。`apply-path …` などほかの文は写さない。"
        "値が読めない文・送らない文・無効の印の掛かった文は汎用の形に残す",
    ),
    Pattern(
        "route_maps.junos.term_from", TOP_POLICY, rf"policy-statement {_NAME} term {_TERM} from {_REST}",
        "route_maps[].entries[].match", "policy-statement POL-A term t1 from protocol direct", _policy_from, note=_POLICY_NOTE,
    ),
    Pattern(
        "route_maps.junos.term_then", TOP_POLICY, rf"policy-statement {_NAME} term {_TERM} then {_REST}",
        "route_maps[].entries[].action / set", "policy-statement POL-A term t1 then accept", _policy_then,
        note=_POLICY_NOTE + "`then accept` は action permit、`then reject` は deny、ほかの残りは set に入れる。"
        "同じ項に違う action が 2 つ以上あれば、後の文の値を使い、食い違いを記録する(値は持たない)",
    ),
    Pattern(
        "route_maps.junos.term_option", TOP_POLICY,
        rf"policy-statement {_NAME} term {_TERM} (?!(?:from|then)(?: |$)){_REST}", "route_maps[].entries[].options",
        "policy-statement POL-A term t1 to level 2", _policy_option,
        note=_POLICY_NOTE + "項の直下の `from`・`then` 以外の文の残りを options に入れる",
    ),
    Pattern(
        "route_maps.junos.term", TOP_POLICY, rf"policy-statement {_NAME} term {_TERM}", "route_maps[].entries[]",
        "policy-statement POL-A term t2", _policy_node, note=_POLICY_NOTE + "`term <項>` だけの文も項を作る",
    ),
    Pattern(
        "route_maps.junos.final_from", TOP_POLICY, rf"policy-statement {_NAME} from {_REST}", "route_maps[].entries[final].match",
        "policy-statement POL-A from protocol static", _policy_from, note=_POLICY_NOTE,
    ),
    Pattern(
        "route_maps.junos.final_then", TOP_POLICY, rf"policy-statement {_NAME} then {_REST}",
        "route_maps[].entries[final].action / set", "policy-statement POL-A then reject", _policy_then,
        note=_POLICY_NOTE + "`then accept` は action permit、`then reject` は deny、ほかの残りは set に入れる",
    ),
    Pattern(
        "route_maps.junos.policy", TOP_POLICY, rf"policy-statement {_NAME}", "route_maps[]", "policy-statement POL-B", _policy_node,
        note=_POLICY_NOTE + "`policy-statement <名前>` だけの文は entries が空の route_maps[<名前>] を作る",
    ),
    Pattern(
        "acls.junos.term", TOP_FIREWALL, rf"family inet filter {_NAME} term {_TERM} {_REST}", "acls[].entries[]",
        "family inet filter FILTER-A term t1 from protocol tcp", _filter_term,
        note="手続き。値が読めない文・送らない文・無効の印の掛かった文は写さず、汎用の形に残す。type は extended、numbered は偽。"
        "項ごとに 1 つの entries(seq は項が文に初めて出た順の番号〔1 始まり。無効の印の掛かった項・送らない文も数える〕、"
        "text は `term <項>: ` の後に項の中の文を出た順に `; ` でつないだもの。最後の語だけが違う文が続く間はその中だけ昇順)。"
        "`family inet6` などほかの family と、項の無い文(`interface-specific` など)は写さない",
    ),
    Pattern(
        "acls.junos.term_only", TOP_FIREWALL, rf"family inet filter {_NAME} term {_TERM}", "acls[].entries[]",
        "family inet filter FILTER-A term t2", _filter_term,
        note="`term <項>` だけの文も項を作る(text は `term <項>:`)。ほかは acls.junos.term と同じ",
    ),
)

_GENERIC_PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "vendor_specific.junos_generic.top", SCOPE_TOP_NODE, r".+", "vendor_specific.juniper_junos.config",
        "interfaces", _top,
        note="最上位の語のノード(文の最初の語。暗黙のノードで、同じ語は 1 つ)。鍵 `<最上位の語>` は、文が 1 つ以上入ったとき"
        "(か、残りの語の無い最上位の行が入ったとき)に置く。汎用の文が 0 件の最上位の語(全部が写った・送らない側になった)は鍵ごと置かない",
    ),
    Pattern(
        "vendor_specific.junos_generic.statement", SCOPE_STATEMENT_NODE, r".+", "vendor_specific.juniper_junos.config(.lines)",
        "ge-0/0/0 unit 0 family inet address 192.0.2.1/24", _statement,
        note="文のノード(最上位の語を除いた残りの語を空白 1 つでつないだもの。`deactivate …` の文も含む)で、写しの表に当たらなかった文"
        "(写せなかった文・無効の印の掛かった文を含む)。文の集合(昇順・同じ文字列は 1 つ)に足す。"
        "送らない文(`set <最上位の語> <文>` の判定が「そのまま送る」でないもの)は置かず、未解釈のまま",
    ),
    Pattern(
        "vendor_specific.junos_generic.top_line", SCOPE_TOP_LINE_NODE, r".+", "vendor_specific.juniper_junos.config",
        "foo", _top_line,
        note="残りの語の無い最上位の語の行(brace の `foo;`、set の `set foo`)。実在の行のノード。鍵を `{\"lines\": []}` で置く。"
        "送らない行は置かず、未解釈のまま",
    ),
)

PATTERNS: tuple[Pattern, ...] = _MAP_PATTERNS + _EXT_PATTERNS + _GENERIC_PATTERNS
# extract が写しの表として試す並び(最上位の語ごとの scope で絞られる)
_MAP_TABLE = _MAP_PATTERNS + _EXT_PATTERNS


def _judge_function() -> Any:
    """送らない行の判定(display set の行 → 入れてよいか)。判定器は 1 つだけ作る。"""
    # llm_analyzer.masking は config_parser を読み込むため、輪を避けて呼ぶときに読む(yamaha._judge_function と同じ)
    from driftpilot.llm_analyzer.masking import Masker

    masker = Masker()

    def judge(line: str) -> bool:
        return masker.judge_alone(line, ()).decision == "plain"

    return judge


def _is_deactivate(text: str) -> bool:
    return text == DEACTIVATE or text.startswith(f"{DEACTIVATE} ")


def _inactive_paths(nodes: list[FlatNode]) -> list[str]:
    """同じ最上位の語の下の `deactivate <経路>` の経路の並び(最上位の語だけの `deactivate` は空文字列)。"""
    return [node.text[len(DEACTIVATE):].strip() for node in nodes if _is_deactivate(node.text)]


def _is_inactive(text: str, paths: list[str]) -> bool:
    """文が無効の印の経路に掛かるか(経路そのもの、または経路 + 空白で始まる。空の経路は全部)。"""
    return any(not path or text == path or text.startswith(f"{path} ") for path in paths)


_AS_STATEMENT = re.compile(r"autonomous-system (?P<v>\S+)")
_ORDER_POLICY = re.compile(rf"policy-statement (?P<n>{_V}) term (?P<t>{_V})(?: .*)?")
_ORDER_FILTER = re.compile(rf"family inet filter (?P<n>{_V}) term (?P<t>{_V})(?: .*)?")
_PASSWORD = re.compile(rf"bgp group (?P<g>[^\s\"]+)(?: neighbor (?P<a>[^\s\"]+))? authentication-key(?: .*)?")


def _statements(top_node: FlatNode) -> list[FlatNode]:
    """最上位の語の下の文(重なった文・読まない文を除く)。"""
    return [node for node in top_node.children if not node.repeated_scope and node.scope_id != SCOPE_UNREAD]


def _live(node: FlatNode, paths: list[str]) -> bool:
    """`deactivate …` の文でも、無効の印の掛かった文でもない。"""
    return not _is_deactivate(node.text) and not _is_inactive(node.text, paths)


def _bgp_ready(tree: FlatTree, judge: Any) -> bool:
    """BGP を写すか。印の掛かっていない `routing-options` の `autonomous-system <数>`(読めて、送る側の判定が「そのまま送る」のもの)と、
    印の掛かっていない `protocols` の `bgp`・`bgp …` の文が、どちらもあるとき。"""
    has_as = has_bgp = False
    for top_node in tree.children:
        if top_node.scope_id != SCOPE_TOP or top_node.text not in (TOP_ROUTING, TOP_PROTOCOLS):
            continue
        nodes = _statements(top_node)
        paths = _inactive_paths(nodes)
        for node in nodes:
            if not _live(node, paths):
                continue
            if top_node.text == TOP_PROTOCOLS:
                has_bgp = has_bgp or node.text == "bgp" or node.text.startswith("bgp ")
                continue
            found = _AS_STATEMENT.fullmatch(node.text)
            if found and _as_number(found["v"]) is not None and judge(f"set {TOP_ROUTING} {node.text}"):
                has_as = True
    return has_as and has_bgp


def _register_terms(top: str, nodes: list[FlatNode], ctx: _Ctx) -> None:
    """項が文に初めて出た順の番号を、印や送る判定の前に振る(無効の印・送らない文も数える。`deactivate` の文は数えない)。"""
    pattern, kind = (_ORDER_POLICY, ORDER_POLICY) if top == TOP_POLICY else (_ORDER_FILTER, ORDER_FILTER)
    for node in nodes:
        found = pattern.fullmatch(node.text)
        if found:
            ctx.order(kind, found["n"], found["t"])


def _note_password(text: str, ctx: _Ctx) -> None:
    """`bgp group <名前> [neighbor <アドレス>] authentication-key …` があれば、その相手(グループ)の password_configured を真にする。
    文は送らない側のまま(値は持たない)。"""
    found = _PASSWORD.fullmatch(text)
    if not found:
        return
    if found["a"] is None:
        ctx.mapped.group(found["g"]).password = True
        return
    address = _ip_address(found["a"])
    if address is not None:
        ctx.mapped.neighbor(str(address), found["g"], ctx.line_no).password = True


def extract(
    tree: FlatTree, interpreted: InterpretedLines
) -> tuple[
    dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
    dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Vlan], dict[str, Acl],
    dict[str, Any], list[DuplicateLine],
]:
    """(interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, global, vlans, acls,
    vendor_specific["juniper_junos"], 値の食い違い) を返す。ospf・ipsec・sdwan・ipv6_prefix_lists・vlans はいつも空。

    interfaces・static_routes・global・bgp・route_maps・prefix_lists・acls は写しの表で写せた文から作る(上の説明)。残りは
    vendor_specific["juniper_junos"] = {"config": {最上位の語: {"lines": [文の昇順]}}}(上の説明)。
    値の食い違いは、同じ欄に違う値の文が 2 つ以上ある場合(行番号だけ)。同じ最上位の語の下の同じ文の重複は木(junos_tree)が記録する。
    """
    sets: dict[str, set[str]] = {}
    ctx = _Ctx(_judge_function(), sets)
    ctx.bgp_ready = _bgp_ready(tree, ctx.judge)
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
        nodes = _statements(top_node)
        mapping = top_node.text in _MAP_TOPS
        paths = _inactive_paths(nodes) if mapping else []
        if top_node.text in (TOP_POLICY, TOP_FIREWALL):
            _register_terms(top_node.text, nodes, ctx)
        for node in nodes:
            ctx.node = node
            pattern = None
            if mapping and _live(node, paths):
                if ctx.bgp_ready and top_node.text == TOP_PROTOCOLS:
                    _note_password(node.text, ctx)
                pattern = apply_first(_MAP_TABLE, top_node.text, node.text, ctx)
            if pattern is None:
                pattern = apply_first(_GENERIC_PATTERNS, SCOPE_STATEMENT_NODE, node.text, ctx)
            if pattern is not None:
                interpreted.mark(node, pattern.id)
    config = {top: {"lines": sorted(lines)} for top, lines in sets.items()}
    mapped = ctx.mapped
    return (
        mapped.interfaces(), mapped.static_routes(), mapped.bgp() if ctx.bgp_ready else None, {}, None, {}, mapped.route_maps(),
        mapped.prefix_lists_ir(), {}, mapped.global_(), {}, mapped.acls(), {"config": config}, mapped.duplicates(),
    )
