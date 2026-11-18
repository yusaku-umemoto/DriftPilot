"""F5 BIG-IP の木(f5_tree。波かっこの木)を、共通スキーマ(IR)と汎用の形 vendor_specific["f5_bigip"] に写す extractor
(ADR-0235 決定 2・3。REQ-349・350)。

写しは 2 段で読む(Gaia〔REQ-345〕と同じ考え方)。

1. よく使う物を、IR の写しの表(`global.f5.*`・`interfaces.f5.*`・`vlans.f5.*`・`static_routes.f5.*`)で共通スキーマへ写す(REQ-350)。
   写した行は解釈済みにし、汎用の形には入れない。REQ-349 の行全体の判定によらず入る(写す欄に秘密は無い。AI に送るときの欄の伏せ方は
   llm_analyzer/fields.py の決まり)。IR の欄は足さない。1 行で閉じるまとまり(木が行全体を 1 つの葉にしたもの)は、最上位でも入れ子でも写さない。
2. 写さなかった行(形に合わない・写す対象でない)は、次の汎用の形で読む(REQ-349)。

写す対象(最上位の見出しで決める):

- 全体: `sys global-settings`(hostname)・`sys ntp`(`servers`・timezone)・`sys dns`(`name-servers`)・`sys syslog`(`remote-servers` の下の名前の見出しの
  `host`・`remote-port`)・`sys snmp`(sys-location・sys-contact)
- 管理 IP とインターフェース: `sys management-ip <A>/<長さ>`(Interface `mgmt`)・`net interface <名前>`
- VLAN と Self IP: `net vlan <名前>`(tag → vlans。mtu・description → 同じ名前の Interface)・`net self <名前>`(address と vlan → Interface `<VLAN の名前>`)
- スタティックルート: `net route <名前>`(network と gw)

決まり:
- 名前は `/Common/` を含めた原文のまま。アドレスの後ろの `%<数>`(`<A>%<数>/<長さ>` と `<A>/<長さ>%<数>` の両方を受ける)は、外した値をアドレスにし、
  `<数>` を vrf の欄に持つ(欄がある項目だけ)。値を 1 つしか持てない欄に違う値が 2 回あれば後の行の値を使い、両方の行番号を食い違い(DuplicateLine。
  値は持たない)として返す(Gaia と同じ)
- 2 つの子がそろって初めて写るもの(Self IP の address と vlan・経路の network と gw・syslog の host)は、そろったときだけ写す。そろわなければ汎用の形に戻す
- アドレスや長さとして読めない値・範囲の外の数・ホストの部分が 0 でない宛先・宛先と gw で IPv4・IPv6 の種類が違う経路・network と gw の `%<数>` が
  違う経路・vrf の欄の無い dns・syslog・mgmt の `%` 付きのアドレスは写さず、今のとおり(汎用の形か未解釈)
- F5 で作る Interface の parent_interface は、いつも None(`1.1` の `.` から親を推定しない。FortiGate と同じ)
- `net interface mgmt` と `sys management-ip` は 1 つの Interface `mgmt` にまとまり、管理 IP があれば type は routed(書かれた順によらない。種類の食い違いの警告は無い)。
  IPv6 の管理 IP は ipv6_addresses。floating の Self IP は IPv6 でも virtual_addresses(ipv6_addresses には floating でないものだけが入る)
- 見出しの鍵(汎用の形): 写す行の親の見出しは、写さなかった子が残るときだけ鍵を置く(見出しの行も解釈済み)。子が 1 つも写らなかった見出しは、
  今(REQ-349)のとおり判定が通れば鍵を置く。経路・Self IP の名前は IR に欄が無いので、写した分は比べる対象から外れる(単純比較の差分には出る)
- 見出しの判定が「そのまま送る」でないもの(`sys snmp`)は、見出しもほかの子も未解釈のまま。sys-location・sys-contact の子だけを IR に写す

汎用の形(REQ-349):

- `vendor_specific["f5_bigip"]["config"]` = `{"lines": [葉のテキストの昇順。同じ文字列は 1 つ], "blocks": {見出しのテキスト: 同じ形}}`
  (Junos・Check Point Gaia と同じ汎用の形)。見出しは行そのもの(`ltm virtual /Common/vs-test1`・`profiles`・`/Common/http`・`{}`)で、語を削らない。
  最上位の `lines` は、最上位の葉(見出しに入らない行。取り込まれたプロンプトの行など)。imish の部分の最上位の葉は、未解釈のまま
- 見出しは、判定が通れば(下)鍵を置く。子の葉が全部送らない側でも、`{"lines": [], "blocks": {}}` で置く(見出しは実在の行なので、
  子の無い見出し `/Common/http { }` の有る無しも差分に出る)。同じテキストの見出しは木でまとまっているので、鍵は 1 つ
- 最上位の 1 行のまとまり(`ltm monitor http /Common/m1 { … }` など、同じ行の中で閉じる行)は、木(f5_tree)が行全体を最上位の葉 1 つにする。
  ここでは、祖先なしの判定が「そのまま送る」のときだけ最上位の `lines` に入る(中身は構造化しない。値が変わると行の削除と追加)。
  入れ子の段の 1 行の形で行全体の判定が「そのまま送る」でないものも、木が行全体を葉 1 つにするので、同じ判定で入らず未解釈になる
- iRule・iApp のテンプレートの本文(Tcl の塊。f5_tree)の行も、`{}`(名前の無い見出し)の中の葉も、同じ判定を通ってから入る
- `lines` は並びを無視して、要素ごとの追加・削除で比べる(core の `_SET_LISTS`)。値だけが変わった葉は、削除 1 件と追加 1 件で出る
- 名前の無い見出し `{}` は、同じ親の下の項目が 1 つにまとまる。どの葉がどの項目に属していたかは、汎用の形では分からなくなる
  (行は失わない。単純比較の差分には出る)

送らない行は入れない(ADR-0224 決定 6 と同じ): 葉のテキストを `Masker().judge_alone(テキスト, 祖先)` で判定し、decision が "plain" でなければ入れず、
未解釈のままにする(伏せる行も入れない)。祖先は見出しのテキスト(外側から順)。見出しのテキストも同じく判定し、plain でなければ、その中の行も
読まずに未解釈に残す(IR に写す子は除く)。F5 の見出しは行そのもの(語を削らない)なので、行頭スコープ表の機種の二重の判定(REQ-344・347)は当てない。
判定器は利用者が設定で足した語を使わない既定のもの。1 回の extract で 1 つ作る。
"""

import ipaddress
import re
from bisect import insort
from dataclasses import dataclass
from typing import Any, cast

from driftpilot.config_parser.f5_tree import SCOPE_HEADING, SCOPE_IMISH
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
    new_vlan,
)
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

PLATFORM = "f5_bigip"

SCOPE_BLOCK = "block"
SCOPE_LEAF = "leaf"
# 最上位の見出しの写しの種類を決める行の scope
SCOPE_TOP = "top"

# 写しの種類(最上位の見出しで決まる。入れ子の見出しの行の scope は「<種類>:block」)
KIND_GLOBAL_SETTINGS = "global_settings"
KIND_NTP = "ntp"
KIND_NTP_SERVERS = "ntp_servers"
KIND_DNS = "dns"
KIND_DNS_SERVERS = "dns_servers"
KIND_SYSLOG = "syslog"
KIND_SYSLOG_REMOTE = "syslog_remote"
KIND_SYSLOG_SERVER = "syslog_server"
KIND_SNMP = "snmp"
KIND_MGMT = "management_ip"
KIND_INTERFACE = "interface"
KIND_VLAN = "vlan"
KIND_SELF = "self_ip"
KIND_ROUTE = "route"

_BLOCK_SUFFIX = ":block"

_TARGET = "vendor_specific.f5_bigip.config"
_ID_GENERIC_BLOCK = "vendor_specific.f5_generic.block"

# 見出しそのものを IR に写すもの(子が 1 つも写らなくても、見出しの行は解釈済み)
_ID_MGMT = "interfaces.f5.management_ip"
_ID_INTERFACE = "interfaces.f5.interface"
_OWN_IDS = frozenset({_ID_MGMT, _ID_INTERFACE})
# 2 つの子がそろって初めて写るもの(読み終えてから、そろったときだけ解釈済みにする)
_DEFERRED_IDS = frozenset(
    {
        "global.f5.logging_host", "global.f5.logging_port",
        "interfaces.f5.self_address", "interfaces.f5.self_vlan", "interfaces.f5.self_floating",
        "static_routes.f5.network", "static_routes.f5.gateway",
    }
)

_VLAN_RANGE = range(1, 4095)
# アドレスの形: `<A>`・`<A>%<数>`・`<A>/<長さ>`・`<A>%<数>/<長さ>`・`<A>/<長さ>%<数>`
_ADDR_FORM = re.compile(r"(?P<a>[0-9A-Fa-f:.]+)(?:%(?P<r1>\d+))?(?:/(?P<n>\d+))?(?:%(?P<r2>\d+))?")


class _Mapped:
    """写した値の入れ物。項目ごとに (値, 行番号) を持ち、同じ項目に違う値が来たら後の値を使って両方の行番号を食い違いとして記録する。"""

    def __init__(self) -> None:
        self.values: dict[tuple[str, ...], tuple[Any, int | None]] = {}
        self.conflicts: list[DuplicateLine] = []

    def write(self, key: tuple[str, ...], value: Any, line: int | None, scope: tuple[str, ...]) -> None:
        """項目に値を書く。前の値と違えば、記録の scope は後の行のスコープ、行番号は前の行と後の行。値は記録しない。"""
        previous = self.values.get(key)
        if previous is not None and previous[0] != value and previous[1] is not None and line is not None:
            self.conflicts.append(DuplicateLine(scope, (previous[1], line)))
        self.values[key] = (value, line)


@dataclass
class _SelfIp:
    """読んだ Self IP 1 つ(全部読んだあとに、名前の昇順で Interface に当てる)。"""

    name: str
    vlan: str
    address: Any  # IPv4Address | IPv6Address
    length: int
    vrf: str | None
    floating: bool
    line: int | None
    scope: tuple[str, ...]


class _Ctx:
    """読んでいる間の状態。judge は送らない行の判定(テキスト・祖先 → 入れてよいか)、ancestors は今読んでいる行の祖先の見出しのテキスト
    (外側から順)、lines は葉を集める先(汎用の形)。

    IR の写しの表が使うもの: interfaces(名前 → Interface。初出の順)・routes・glob・dns(DNS の集合)・vlans・mapped(値の食い違いの記録)・
    selfs(Self IP の候補)・mgmt(管理 IP が写れたか)、今読んでいる見出しの種類(scope)と名前(name)・行番号(line)・2 つそろって写る子の候補(cur)。
    既定の値は、パターンの例の行を 1 つの ctx で通すテストのためのもの。
    """

    def __init__(self, judge: Any) -> None:
        self.judge = judge
        self.ancestors: tuple[str, ...] = ()
        self.lines: set[str] = set()
        self.interfaces: dict[str, Interface] = {}
        self.routes: list[StaticRoute] = []
        self.glob: Global = new_global()
        self.dns: set[str] = set()
        self.vlans: dict[str, Vlan] = {}
        self.vlan_tag: dict[str, int] = {}
        self.mapped = _Mapped()
        self.selfs: list[_SelfIp] = []
        self.mgmt = False
        self.scope = KIND_GLOBAL_SETTINGS
        self.name = "/Common/example"
        self.line: int | None = None
        self.cur: dict[str, list[tuple[Any, int | None]]] = {}

    def interface(self, name: str, type_: str = "switchport") -> Interface:
        """名前の Interface を返す(無ければ作る)。F5 では名前の `.` から親を決めないので、parent_interface はいつも None。"""
        found = self.interfaces.get(name)
        if found is None:
            found = new_interface(name)
            found["type"] = cast(Any, type_)
            found["parent_interface"] = None
            self.interfaces[name] = found
        return found

    def set_interface(self, name: str, field: str, value: Any) -> None:
        interface = self.interface(name)
        self.mapped.write(("interface", name, field), value, self.line, self.ancestors)
        cast(dict[str, Any], interface)[field] = value

    def set_global(self, key: str, value: Any) -> None:
        self.mapped.write(("global", key), value, self.line, self.ancestors)
        cast(dict[str, Any], self.glob)[key] = value

    def stash(self, key: str, value: Any) -> None:
        self.cur.setdefault(key, []).append((value, self.line))


def _unquote(text: str) -> str:
    """両端が `"` のときだけ外す。"""
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


def _parse_address(text: str, with_length: bool) -> tuple[Any, int | None, str | None] | None:
    """`<A>[%<数>][/<長さ>][%<数>]` を (アドレス, 長さ, vrf) にする。読めなければ None。
    with_length が真なら長さが要り、偽なら長さが無い。`%<数>` は 2 か所のどちらかに書け、両方あって違えば読めない。"""
    m = _ADDR_FORM.fullmatch(text)
    if m is None:
        return None
    if (m["n"] is not None) != with_length:
        return None
    if m["r2"] is not None and m["n"] is None:
        return None
    first = str(int(m["r1"])) if m["r1"] is not None else None
    second = str(int(m["r2"])) if m["r2"] is not None else None
    if first is not None and second is not None and first != second:
        return None
    try:
        address = ipaddress.ip_address(m["a"])
        length = int(m["n"]) if m["n"] is not None else None
        if length is not None:
            ipaddress.ip_interface(f"{address}/{length}")
    except ValueError:
        return None
    return address, length, first if first is not None else second


def _last(ctx: _Ctx, key: tuple[str, ...], items: list[tuple[Any, int | None]], scope: tuple[str, ...]) -> Any:
    """同じ項目の候補を行の順に書き、後の値を返す(違う値があれば食い違いに記録する)。"""
    for value, line in items:
        ctx.mapped.write(key, value, line, scope)
    return items[-1][0]


# --- 汎用の形(REQ-349) ---


def _block(m: Any, ctx: _Ctx) -> bool:
    """見出し。送らない行でなければ、まとまりを置く(呼び側が鍵を置く)。送らない行は未解釈のまま。"""
    return bool(ctx.judge(m.string, ctx.ancestors))


def _leaf(m: Any, ctx: _Ctx) -> bool:
    """葉。送らない行(伏せる行も)でなければ、その見出しの葉の集合に足す。送らない行は未解釈のまま。"""
    if not ctx.judge(m.string, ctx.ancestors):
        return False
    ctx.lines.add(m.string)
    return True


# --- 見出し(写しの種類を決める) ---


def _kind(kind: str):
    """見出し(最上位・入れ子)の行。今読んでいる見出しの種類と名前(最初の括弧)を置く。"""

    def apply(m: Any, ctx: _Ctx) -> bool:
        ctx.scope = kind
        ctx.name = m[1] if m.re.groups else ""
        return True

    return apply


def _management_ip(m: Any, ctx: _Ctx) -> bool:
    parsed = _parse_address(m[1], True)
    if parsed is None or parsed[2] is not None:  # vrf の欄の無い項目の `%` 付きは写さない
        return False
    address, length, _ = parsed
    ctx.scope = KIND_MGMT
    ctx.name = "mgmt"
    ctx.interface("mgmt", "routed")
    ctx.mgmt = True
    value = ipaddress.ip_interface(f"{address}/{length}")
    if value.version == 4:
        ctx.set_interface("mgmt", "ip_address", value)
    else:
        interface = ctx.interface("mgmt")
        if interface["ipv6_addresses"] is None:
            interface["ipv6_addresses"] = []
        text = str(value)
        if text not in interface["ipv6_addresses"]:
            insort(interface["ipv6_addresses"], text)
    return True


def _interface_heading(m: Any, ctx: _Ctx) -> bool:
    ctx.scope = KIND_INTERFACE
    ctx.name = m[1]
    ctx.interface(m[1])
    return True


# --- 全体 ---


def _hostname(m: Any, ctx: _Ctx) -> bool:
    ctx.set_global("hostname", m[1])
    return True


def _timezone(m: Any, ctx: _Ctx) -> bool:
    ctx.set_global("timezone", " ".join(m[1].split()))
    return True


def _ntp_server(m: Any, ctx: _Ctx) -> bool:
    if m[1] not in ctx.glob["ntp_servers"]:
        ctx.glob["ntp_servers"].append(m[1])
    return True


def _dns_server(m: Any, ctx: _Ctx) -> bool:
    try:
        address = ipaddress.ip_address(m[1])
    except ValueError:  # `%` 付きなどは vrf の欄の無い項目なので写さない
        return False
    ctx.dns.add(str(address))
    ctx.glob["dns"]["servers"] = sorted(ctx.dns)
    return True


def _logging_host(m: Any, ctx: _Ctx) -> bool:
    try:
        ipaddress.ip_address(m[1])
    except ValueError:
        return False
    ctx.stash("host", m[1])
    return True


def _logging_port(m: Any, ctx: _Ctx) -> bool:
    ctx.stash("port", m[1])
    return True


def _snmp_text(key: str):
    def apply(m: Any, ctx: _Ctx) -> bool:
        text = _unquote(m[1])
        if not text:
            return False
        ctx.mapped.write(("global", "snmp", key), text, ctx.line, ctx.ancestors)
        ctx.glob["snmp"][key] = text  # type: ignore[literal-required]
        return True

    return apply


# --- インターフェース ---


def _description(m: Any, ctx: _Ctx) -> bool:
    text = _unquote(m[1])
    if not text:
        return False
    ctx.set_interface(ctx.name, "description", text)
    return True


def _interface_speed(m: Any, ctx: _Ctx) -> bool:
    ctx.set_interface(ctx.name, "speed", m[1])
    return True


def _mtu(m: Any, ctx: _Ctx) -> bool:
    ctx.set_interface(ctx.name, "mtu", int(m[1]))
    return True


def _shutdown(m: Any, ctx: _Ctx) -> bool:
    ctx.set_interface(ctx.name, "shutdown", m[1] == "disabled")
    return True


# --- VLAN ---


def _vlan_tag(m: Any, ctx: _Ctx) -> bool:
    number = int(m[1])
    if number not in _VLAN_RANGE:
        return False
    previous = ctx.vlan_tag.get(ctx.name)
    ctx.mapped.write(("vlan", "tag", ctx.name), number, ctx.line, ctx.ancestors)
    if previous is not None and previous != number and (old := ctx.vlans.get(str(previous))) is not None and old["name"] == ctx.name:
        del ctx.vlans[str(previous)]
    ctx.vlan_tag[ctx.name] = number
    ctx.mapped.write(("vlan", str(number)), ctx.name, ctx.line, ctx.ancestors)
    vlan = new_vlan(number)
    vlan["name"] = ctx.name
    ctx.vlans[str(number)] = vlan
    return True


def _vlan_interface_description(m: Any, ctx: _Ctx) -> bool:
    text = _unquote(m[1])
    if not text:
        return False
    ctx.interface(ctx.name, "svi")
    ctx.set_interface(ctx.name, "description", text)
    return True


def _vlan_interface_mtu(m: Any, ctx: _Ctx) -> bool:
    ctx.interface(ctx.name, "svi")
    ctx.set_interface(ctx.name, "mtu", int(m[1]))
    return True


# --- Self IP ---


def _self_address(m: Any, ctx: _Ctx) -> bool:
    parsed = _parse_address(m[1], True)
    if parsed is None:
        return False
    ctx.stash("address", parsed)
    return True


def _self_vlan(m: Any, ctx: _Ctx) -> bool:
    ctx.stash("vlan", m[1])
    return True


def _self_floating(m: Any, ctx: _Ctx) -> bool:
    ctx.stash("floating", m[1] == "enabled")
    return True


# --- スタティックルート ---


def _route_network(m: Any, ctx: _Ctx) -> bool:
    text = m[1]
    if text == "default":
        ctx.stash("network", (ipaddress.IPv4Network("0.0.0.0/0"), None))
        return True
    if text == "default-inet6":
        ctx.stash("network", (ipaddress.IPv6Network("::/0"), None))
        return True
    parsed = _parse_address(text, True)
    if parsed is None:
        return False
    address, length, vrf = parsed
    try:
        network = ipaddress.ip_network(f"{address}/{length}", strict=True)  # ホストの部分が 0 でない宛先は写さない
    except ValueError:
        return False
    ctx.stash("network", (network, vrf))
    return True


def _route_gateway(m: Any, ctx: _Ctx) -> bool:
    parsed = _parse_address(m[1], False)
    if parsed is None:
        return False
    ctx.stash("gateway", (parsed[0], parsed[2]))
    return True


PATTERNS: tuple[Pattern, ...] = (
    # --- 最上位の見出し(写しの種類を決める) ---
    Pattern(
        "global.f5.global_settings", SCOPE_TOP, r"sys global-settings", "global",
        "sys global-settings", _kind(KIND_GLOBAL_SETTINGS),
        note="見出し。中の `hostname` を global.hostname に写す。ほかの子は汎用の形(写さなかった子が残るときだけ鍵を置く)",
    ),
    Pattern(
        "global.f5.ntp", SCOPE_TOP, r"sys ntp", "global", "sys ntp", _kind(KIND_NTP),
        note="見出し。中の `servers { … }` と `timezone` を写す。ほかの子は汎用の形",
    ),
    Pattern(
        "global.f5.dns", SCOPE_TOP, r"sys dns", "global", "sys dns", _kind(KIND_DNS),
        note="見出し。中の `name-servers { … }` を写す。`search …` などほかの子は汎用の形",
    ),
    Pattern(
        "global.f5.syslog", SCOPE_TOP, r"sys syslog", "global", "sys syslog", _kind(KIND_SYSLOG),
        note="見出し。中の `remote-servers` の下の各見出しの `host`・`remote-port` を写す。ほかの子は汎用の形",
    ),
    Pattern(
        "global.f5.snmp", SCOPE_TOP, r"sys snmp", "global", "sys snmp", _kind(KIND_SNMP),
        note="見出し。語 snmp は AI に送らない側の判定になるので、見出しもほかの子(`communities`・`users`・`agent-addresses` など)も未解釈のまま。"
        "`sys-location`・`sys-contact` の子だけを写す(行全体の判定によらず IR に入る)",
    ),
    Pattern(
        "interfaces.f5.management_ip", SCOPE_TOP, r"sys management-ip (\S+)", "interfaces.mgmt.ip_address / ipv6_addresses",
        "sys management-ip 192.0.2.5/24", _management_ip,
        note="見出しそのものを写す。Interface `mgmt`(type は routed)の ip_address(IPv4)か ipv6_addresses(IPv6。正規の書き方の文字列。昇順)。"
        "`net interface mgmt` があれば同じ `mgmt` にまとめ、管理 IP があれば type は書かれた順によらず routed(種類の食い違いの警告は出さない)。"
        "`<A>/<長さ>` として読めない・`%<数>` 付きの見出しは写さず、汎用の形のまま",
    ),
    Pattern(
        "interfaces.f5.interface", SCOPE_TOP, r"net interface (\S+)", "interfaces.<名前>",
        "net interface 1.1", _interface_heading,
        note="見出しそのものを写す。Interface `<名前>`(type は switchport)を作る。名前に `.` があっても親は決めない(parent_interface はいつも None)",
    ),
    Pattern(
        "vlans.f5.vlan", SCOPE_TOP, r"net vlan (\S+)", "vlans", "net vlan /Common/vlan-test1", _kind(KIND_VLAN),
        note="見出し。中の `tag`(→ vlans)・`mtu`・`description`(→ 同じ名前の Interface。type は svi)を写す。`tag` の無い VLAN は vlans に入れない",
    ),
    Pattern(
        "interfaces.f5.self_ip", SCOPE_TOP, r"net self (\S+)", "interfaces.<VLAN の名前>", "net self /Common/self-a", _kind(KIND_SELF),
        note="見出し。中の `address` と `vlan` がそろったときだけ、Interface `<VLAN の名前>`(type は svi。無ければ作る)に写す。"
        "`traffic-group`・`allow-service` などほかの子は汎用の形。`vlan` の無い・`address` の形に合わない Self IP は写さない",
    ),
    Pattern(
        "static_routes.f5.route", SCOPE_TOP, r"net route (\S+)", "static_routes[]", "net route /Common/default", _kind(KIND_ROUTE),
        note="見出し。中の `network` と `gw` がそろったときだけ、経路 1 本に写す(見出しの出た順)。`gw` の無い経路(`pool`・`interface` で送るもの)は写さない",
    ),
    # --- 入れ子の見出し ---
    Pattern(
        "global.f5.ntp_servers", KIND_NTP + _BLOCK_SUFFIX, r"servers", "global.ntp_servers", "servers", _kind(KIND_NTP_SERVERS),
        note="`sys ntp` の下の `servers { … }`。中の語を写す",
    ),
    Pattern(
        "global.f5.dns_name_servers", KIND_DNS + _BLOCK_SUFFIX, r"name-servers", "global.dns.servers", "name-servers", _kind(KIND_DNS_SERVERS),
        note="`sys dns` の下の `name-servers { … }`。中の語を写す",
    ),
    Pattern(
        "global.f5.syslog_remote_servers", KIND_SYSLOG + _BLOCK_SUFFIX, r"remote-servers", "global.logging_hosts", "remote-servers",
        _kind(KIND_SYSLOG_REMOTE), note="`sys syslog` の下の `remote-servers { … }`。下の各名前の見出しを読む",
    ),
    Pattern(
        "global.f5.syslog_server", KIND_SYSLOG_REMOTE + _BLOCK_SUFFIX, r"(\S+)", "global.logging_hosts[]", "/Common/r1",
        _kind(KIND_SYSLOG_SERVER),
        note="`remote-servers` の下の名前の見出し。中の `host` が写れたとき(`remote-port` があれば付けて)1 つの要素にする",
    ),
    # --- 全体の子 ---
    Pattern(
        "global.f5.hostname", KIND_GLOBAL_SETTINGS, r"hostname (\S+)", "global.hostname", "hostname bigip-test1.example.test", _hostname,
        note="`sys global-settings` の `hostname <名前>`。違う値が 2 回あれば後の行の値を使い、食い違いの警告に出す",
    ),
    Pattern(
        "global.f5.timezone", KIND_NTP, r"timezone (\S.*)", "global.timezone", "timezone Asia/Tokyo", _timezone,
        note="`sys ntp` の `timezone <文>`(空白は 1 つにする)。食い違いの扱いはホスト名と同じ",
    ),
    Pattern(
        "global.f5.ntp_server", KIND_NTP_SERVERS, r"(\S+)", "global.ntp_servers[]", "192.0.2.123", _ntp_server,
        note="`servers { … }` の中の語(アドレスか名前)。原文のまま、書かれた順に入れ、同じものは重ねない",
    ),
    Pattern(
        "global.f5.dns_server", KIND_DNS_SERVERS, r"(\S+)", "global.dns.servers[]", "192.0.2.53", _dns_server,
        note="`name-servers { … }` の中の語。IPv4・IPv6 として読めるものだけを正規の書き方にし、昇順に重ねずに入れる(Cisco と同じ並び)。"
        "`%` 付きなど読めない語は写さず、汎用の形",
    ),
    Pattern(
        "global.f5.logging_host", KIND_SYSLOG_SERVER, r"host (\S+)", "global.logging_hosts[]", "host 192.0.2.20", _logging_host,
        note="`remote-servers` の下の名前の見出しの `host <A>`。「`<A>`[ `remote-port <N>`]」を書かれた順に入れ、同じものは重ねない。"
        "A は IPv4・IPv6 として読めるものだけで、原文のまま(`%` 付きは写さない)。`host` が写れたときだけ `remote-port` も写す",
    ),
    Pattern(
        "global.f5.logging_port", KIND_SYSLOG_SERVER, r"remote-port (\d+)", "global.logging_hosts[]", "remote-port 514", _logging_port,
        note="同じ見出しの `remote-port <N>`。`host` が写れたときだけ、その要素の後ろに ` remote-port <N>` を付ける(`host` が無ければ汎用の形)",
    ),
    Pattern(
        "global.f5.snmp_location", KIND_SNMP, r"sys-location (.+)", "global.snmp.location", 'sys-location "LAB-ROOM"', _snmp_text("location"),
        note="文の両端の `\"` を外す(空になれば写さない)。見出し `sys snmp` は送らない側の判定でも、この子は IR に写す(欄は伏せない。AI に送る側になる)",
    ),
    Pattern(
        "global.f5.snmp_contact", KIND_SNMP, r"sys-contact (.+)", "global.snmp.contact", 'sys-contact "NOC-TEAM"', _snmp_text("contact"),
        note="snmp.location と同じ(文の両端の `\"` を外し、欄は伏せない)",
    ),
    # --- 管理 IP・インターフェース ---
    Pattern(
        "interfaces.f5.management_description", KIND_MGMT, r"description (.+)", "interfaces.mgmt.description", 'description "mgmt"', _description,
        note="`sys management-ip` の `description <文>`(両端の `\"` を外す。空になれば写さない)。`net interface mgmt` の description と違えば、後の行の値を使い食い違いの警告に出す",
    ),
    Pattern(
        "interfaces.f5.speed", KIND_INTERFACE, r"media-fixed (\S+)", "interfaces.<名前>.speed", "media-fixed 10000T-FD", _interface_speed,
        note="speed = 語の原文",
    ),
    Pattern(
        "interfaces.f5.mtu", KIND_INTERFACE, r"mtu (\d+)", "interfaces.<名前>.mtu", "mtu 9000", _mtu, note="mtu = 数",
    ),
    Pattern(
        "interfaces.f5.description", KIND_INTERFACE, r"description (.+)", "interfaces.<名前>.description", 'description "uplink"', _description,
        note="description = 文(両端の `\"` を外す。空になれば写さない)",
    ),
    Pattern(
        "interfaces.f5.shutdown", KIND_INTERFACE, r"(disabled|enabled)", "interfaces.<名前>.shutdown", "disabled", _shutdown,
        note="`disabled` は shutdown = 真、`enabled` は偽。行が無ければ今の既定の値(偽)。違う値が 2 回あれば後の行の値を使い、食い違いの警告に出す",
    ),
    # --- VLAN・Self IP ---
    Pattern(
        "vlans.f5.tag", KIND_VLAN, r"tag (\d+)", "vlans[<N>]", "tag 101", _vlan_tag,
        note="`net vlan <名前>` の `tag <N>`(1〜4094)。vlans[\"<N>\"] の id = N、name = `<名前>`(`/Common/` を含む原文)。範囲の外は写さない。"
        "同じ N の VLAN が 2 つあれば後の行の値を使い、食い違いの警告に出す",
    ),
    Pattern(
        "interfaces.f5.vlan_mtu", KIND_VLAN, r"mtu (\d+)", "interfaces.<VLAN の名前>.mtu", "mtu 1500", _vlan_interface_mtu,
        note="その名前の Interface(Self IP と同じ鍵。無ければ作る。type は svi)の mtu",
    ),
    Pattern(
        "interfaces.f5.vlan_description", KIND_VLAN, r"description (.+)", "interfaces.<VLAN の名前>.description", 'description "servers"',
        _vlan_interface_description, note="その名前の Interface(type は svi)の description(両端の `\"` を外す。空になれば写さない)",
    ),
    Pattern(
        "interfaces.f5.self_address", KIND_SELF, r"address (\S+)", "interfaces.<VLAN の名前>.ip_address / secondary_addresses / ipv6_addresses / virtual_addresses",
        "address 198.51.100.2/24", _self_address,
        note="`address <A>/<長さ>[%<数>]`(`<A>%<数>/<長さ>` も可)。`vlan` とそろったときだけ、全部読んだあとに Self IP の名前の昇順で Interface `<VLAN の名前>`(type は svi)に当てる。"
        "floating でない IPv4 は ip_address(2 つ目からは secondary_addresses)、floating の Self IP は IPv4・IPv6 とも virtual_addresses(`<A>/<長さ>` の文字列)、"
        "floating でない IPv6 は ipv6_addresses(文字列)。`%<数>` は Interface の vrf(違う値が 2 回あれば後の行の値を使い食い違いの警告に出す)。"
        "アドレスや長さとして読めない値は写さず、汎用の形",
    ),
    Pattern(
        "interfaces.f5.self_vlan", KIND_SELF, r"vlan (\S+)", "interfaces.<VLAN の名前>", "vlan /Common/vlan-test1", _self_vlan,
        note="Self IP の `vlan <VLAN の名前>`(`/Common/` を含む原文)。写し先の Interface の鍵。`address` とそろったときだけ写す",
    ),
    Pattern(
        "interfaces.f5.self_floating", KIND_SELF, r"floating (enabled|disabled)", "interfaces.<VLAN の名前>.virtual_addresses", "floating enabled",
        _self_floating,
        note="`floating enabled` はその Self IP を virtual_addresses に入れる印。`floating disabled`・行が無いときは機器ごとのアドレス。その Self IP を写したときだけ解釈済みにする",
    ),
    # --- スタティックルート ---
    Pattern(
        "static_routes.f5.network", KIND_ROUTE, r"network (\S+)", "static_routes[].destination", "network 198.51.100.0/24", _route_network,
        note="`network default`(0.0.0.0/0)・`network default-inet6`(::/0)・`network <A>/<長さ>[%<数>]`(ホストの部分が 0 のもの)。"
        "`gw` とそろったときだけ経路 1 本にする。`%<数>` は vrf",
    ),
    Pattern(
        "static_routes.f5.gateway", KIND_ROUTE, r"gw (\S+)", "static_routes[].next_hop", "gw 192.0.2.254", _route_gateway,
        note="`gw <A>[%<数>]`。next_hop = A。宛先と IPv4・IPv6 の種類が違う、または network と `%<数>` が違うときは、その経路は写さず汎用の形。"
        "interface・distance・enabled は None(Cisco の既定)。経路の機種固有値は置かない(Gaia と同じ)",
    ),
    # --- 汎用の形(写さなかった行)。最後に残す ---
    Pattern(
        _ID_GENERIC_BLOCK, SCOPE_BLOCK, r".+", _TARGET + "(.blocks)",
        "ltm virtual /Common/vs-test1", _block,
        note="見出しのノード(行そのもの。語を削らない。F5 の見出し・Tcl の塊の見出し・名前の無い見出し `{}`)。まとまりの鍵は見出しのテキスト。"
        "判定が「そのまま送る」なら、子の葉が全部送らない側でも `{\"lines\": [], \"blocks\": {}}` で置く。"
        "送らない行(見出しのテキストの判定が「そのまま送る」でないもの)は置かず、その中の行も読まずに未解釈のまま。"
        "同じ親の下の同じテキストの見出しは木で 1 つにまとまる(名前の無い見出し `{}` も)。IR に写す子を持つ見出しは、写さなかった子が残るときだけ鍵を置く",
    ),
    Pattern(
        "vendor_specific.f5_generic.leaf", SCOPE_LEAF, r".+", _TARGET + "(.lines / .blocks.<見出し>.lines)",
        "destination /Common/192.0.2.10:443", _leaf,
        note="葉のノード(見出しの子の行。語の間の空白は 1 つ。Tcl の本文の行は前後の空白だけ落とした行そのもの)。葉の集合(昇順・同じ文字列は 1 つ)に足す。"
        "最上位の葉(見出しに入らない行。最上位の 1 行のまとまりの行全体を含む)は最上位の `lines` に入る。imish の部分の最上位の葉は読まず、未解釈のまま。"
        "送らない行(見出しのテキストを祖先にした判定が「そのまま送る」でないもの。伏せる行を含む)は置かず、未解釈のまま。"
        "行頭スコープ表の機種の二重の判定は当てない",
    ),
)


def _judge_function() -> Any:
    """送らない行の判定(テキスト・祖先 → 入れてよいか)。判定器は 1 つだけ作る。"""
    # llm_analyzer.masking は config_parser を読み込むため、輪を避けて呼ぶときに読む(junos._judge_function と同じ)
    from driftpilot.llm_analyzer.masking import Masker

    masker = Masker()

    def judge(text: str, ancestors: tuple[str, ...]) -> bool:
        return masker.judge_alone(text, ancestors).decision == "plain"

    return judge


# --- 2 つの子がそろって写るものの仕上げ(そろったら True) ---


def _finish_self(name: str, node: FlatNode, scope: tuple[str, ...], cur: dict[str, list[tuple[Any, int | None]]], ctx: _Ctx) -> bool:
    if "address" not in cur or "vlan" not in cur:
        return False
    address, length, vrf = _last(ctx, ("net self", name, "address"), cur["address"], scope)
    vlan = _last(ctx, ("net self", name, "vlan"), cur["vlan"], scope)
    floating = _last(ctx, ("net self", name, "floating"), cur["floating"], scope) if "floating" in cur else False
    ctx.selfs.append(_SelfIp(name, vlan, address, length, vrf, bool(floating), node.line_no, scope))
    return True


def _finish_route(name: str, node: FlatNode, scope: tuple[str, ...], cur: dict[str, list[tuple[Any, int | None]]], ctx: _Ctx) -> bool:
    if "network" not in cur or "gateway" not in cur:
        return False
    destination, network_vrf = cur["network"][-1][0]
    next_hop, gateway_vrf = cur["gateway"][-1][0]
    if destination.version != next_hop.version:
        return False
    if network_vrf is not None and gateway_vrf is not None and network_vrf != gateway_vrf:
        return False
    _last(ctx, ("net route", name, "network"), cur["network"], scope)
    _last(ctx, ("net route", name, "gateway"), cur["gateway"], scope)
    ctx.routes.append(
        {
            "destination": destination,
            "next_hop": next_hop,
            "interface": None,
            "vrf": network_vrf if network_vrf is not None else gateway_vrf,
            "distance": None,
            "enabled": None,
        }
    )
    return True


def _finish_syslog_server(
    name: str, node: FlatNode, scope: tuple[str, ...], cur: dict[str, list[tuple[Any, int | None]]], ctx: _Ctx
) -> bool:
    if "host" not in cur:
        return False
    value = _last(ctx, ("sys syslog", name, "host"), cur["host"], scope)
    if "port" in cur:
        value += " remote-port " + _last(ctx, ("sys syslog", name, "port"), cur["port"], scope)
    if value not in ctx.glob["logging_hosts"]:
        ctx.glob["logging_hosts"].append(value)
    return True


_FINISH = {KIND_SELF: _finish_self, KIND_ROUTE: _finish_route, KIND_SYSLOG_SERVER: _finish_syslog_server}


def _generic_child(
    child: FlatNode, ancestors: tuple[str, ...], interpreted: InterpretedLines, ctx: _Ctx, lines: set[str], blocks: dict[str, Any]
) -> None:
    """子 1 つを、汎用の形で読む(葉は葉の集合へ、見出しは判定が通れば鍵を置く)。"""
    ctx.ancestors = ancestors
    if child.scope_id is None:
        ctx.lines = set()
        pattern = apply_first(PATTERNS, SCOPE_LEAF, child.text, ctx)
        if pattern is not None:
            lines |= ctx.lines
            interpreted.mark(child, pattern.id)
        return
    pattern = apply_first(PATTERNS, SCOPE_BLOCK, child.text, ctx)
    if pattern is None:
        return
    interpreted.mark(child, pattern.id)
    sub_lines, sub_blocks = _read(child.children, (*ancestors, child.text), interpreted, ctx)
    blocks[child.text] = {"lines": sub_lines, "blocks": sub_blocks}


def _read_mapped(
    node: FlatNode, ancestors: tuple[str, ...], interpreted: InterpretedLines, ctx: _Ctx, scope: str, name: str, heading_id: str
) -> tuple[int, dict[str, Any] | None]:
    """IR に写す見出し(node)を読む。(写した子の数〔見出しそのものを写すときは +1〕, 汎用の形に置くまとまり)を返す。

    子はまず scope の IR の写しの表で試し、写せたものは解釈済みにする。写せなかった子は、見出しの判定が通っていれば汎用の形で読む。
    子が 1 つも写らなかった(見出しそのものも写さない)ときは、今(REQ-349)のとおり、判定が通れば鍵を置く。
    写したものがあるときは、写さなかった子が残るときだけ鍵を置き、見出しの行は解釈済みにする(判定が「そのまま送る」でない見出しは未解釈のまま。
    写した子だけが IR に入る)。
    """
    names = (*ancestors, node.text)
    judged = bool(ctx.judge(node.text, ancestors))
    own = heading_id in _OWN_IDS
    outer = ctx.cur
    ctx.cur = cur = {}
    lines: set[str] = set()
    blocks: dict[str, Any] = {}
    mapped = 0
    pending: list[tuple[FlatNode, Pattern]] = []
    for child in node.children:
        if child.scope_id == SCOPE_IMISH:
            continue
        ctx.ancestors = names
        ctx.line = child.line_no
        ctx.name = name
        ctx.scope = scope
        if child.scope_id is None:
            pattern = apply_first(PATTERNS, scope, child.text, ctx)
            if pattern is not None:
                if pattern.id in _DEFERRED_IDS:
                    pending.append((child, pattern))
                else:
                    interpreted.mark(child, pattern.id)
                    mapped += 1
                continue
            if judged:
                _generic_child(child, names, interpreted, ctx, lines, blocks)
            continue
        pattern = apply_first(PATTERNS, scope + _BLOCK_SUFFIX, child.text, ctx)
        if pattern is None:
            if judged:
                _generic_child(child, names, interpreted, ctx, lines, blocks)
            continue
        sub_scope, sub_name = ctx.scope, ctx.name
        count, block = _read_mapped(child, names, interpreted, ctx, sub_scope, sub_name, pattern.id)
        if count:
            mapped += 1
        if block is not None:
            blocks[child.text] = block
        ctx.cur = cur
    finish = _FINISH.get(scope)
    ctx.cur = outer
    ctx.ancestors = names
    complete = bool(pending) and finish is not None and finish(name, node, names, cur, ctx)
    for child, pattern in pending:
        if complete:
            interpreted.mark(child, pattern.id)
            mapped += 1
        elif judged:
            _generic_child(child, names, interpreted, ctx, lines, blocks)
    if mapped or own:
        if own or judged:
            interpreted.mark(node, heading_id)
        left = {"lines": sorted(lines), "blocks": blocks}
        return mapped + (1 if own else 0), (left if judged and (lines or blocks) else None)
    if not judged:
        return 0, None
    interpreted.mark(node, _ID_GENERIC_BLOCK)
    return 0, {"lines": sorted(lines), "blocks": blocks}


def _read(
    children: list[FlatNode], ancestors: tuple[str, ...], interpreted: InterpretedLines, ctx: _Ctx
) -> tuple[list[str], dict[str, Any]]:
    """見出し(か最上位)の子を読み、({葉の昇順}, {見出しのテキスト: まとまり}) を返す。入れた分だけ解釈済みにする。
    最上位の見出しが IR に写す種類(sys ntp・net self など)なら、IR の写しの表で読む。"""
    lines: set[str] = set()
    blocks: dict[str, Any] = {}
    for child in children:
        if child.scope_id == SCOPE_IMISH:
            continue
        if not ancestors and child.scope_id == SCOPE_HEADING:
            ctx.ancestors = ()
            ctx.line = child.line_no
            top = apply_first(PATTERNS, SCOPE_TOP, child.text, ctx)
            if top is not None:
                _, block = _read_mapped(child, ancestors, interpreted, ctx, ctx.scope, ctx.name, top.id)
                if block is not None:
                    blocks[child.text] = block
                continue
        _generic_child(child, ancestors, interpreted, ctx, lines, blocks)
    return sorted(lines), blocks


def _apply_selfs(ctx: _Ctx) -> None:
    """Self IP を、Interface `<VLAN の名前>`(type は svi)に当てる。vrf は行の順に書き(食い違いは後の行の値)、アドレスは Self IP の名前の昇順で当てる。"""
    for item in ctx.selfs:
        ctx.interface(item.vlan, "svi")
        if item.vrf is not None:
            ctx.mapped.write(("interface", item.vlan, "vrf"), item.vrf, item.line, item.scope)
            ctx.interface(item.vlan)["vrf"] = item.vrf
    for item in sorted(ctx.selfs, key=lambda each: each.name):
        interface = ctx.interface(item.vlan)
        value = ipaddress.ip_interface(f"{item.address}/{item.length}")
        if item.floating:
            if interface["virtual_addresses"] is None:
                interface["virtual_addresses"] = []
            interface["virtual_addresses"].append(str(value))
        elif value.version == 6:
            if interface["ipv6_addresses"] is None:
                interface["ipv6_addresses"] = []
            interface["ipv6_addresses"].append(str(value))
        elif interface["ip_address"] is None:
            interface["ip_address"] = value  # type: ignore[assignment]
        else:
            if interface["secondary_addresses"] is None:
                interface["secondary_addresses"] = []
            interface["secondary_addresses"].append(value)  # type: ignore[arg-type]


def extract(
    tree: FlatTree, interpreted: InterpretedLines
) -> tuple[
    dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
    dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Vlan], dict[str, Acl],
    dict[str, Any], list[DuplicateLine],
]:
    """(interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, global, vlans, acls,
    vendor_specific["f5_bigip"], 値の食い違い) を返す。bgp・ospf・acls などはいつも空(写さない)。

    interfaces は名前の初出の順、static_routes は見出しの出た順。global は new_global() に写した値を入れたもの。
    vendor_specific["f5_bigip"] = {"config": {"lines": [最上位の葉], "blocks": {見出しのテキスト: {"lines": …, "blocks": …}}}}(上の説明)。
    写さなかった行だけを持ち、写す行の親の見出しは、写さなかった子が残るときだけ鍵を置く。
    値の食い違いは、1 つしか持てない欄に違う値が書かれた記録(後の行のスコープと、前の行・後の行の行番号。値は持たない)。
    同じ見出しの下の同じ葉の重複は木が警告に出す。
    """
    ctx = _Ctx(_judge_function())
    lines, blocks = _read(tree.children, (), interpreted, ctx)
    _apply_selfs(ctx)
    if ctx.mgmt:
        ctx.interface("mgmt")["type"] = "routed"
    config = {"lines": lines, "blocks": blocks}
    return (
        ctx.interfaces, ctx.routes, None, {}, None, {}, {}, {}, {}, ctx.glob, ctx.vlans, {}, {"config": config}, ctx.mapped.conflicts,
    )
