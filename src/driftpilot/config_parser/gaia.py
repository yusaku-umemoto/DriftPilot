"""Check Point Gaia の config(`show configuration` の出力)の木と、共通スキーマ・汎用の形への写し(ADR-0234 決定 1〜3・5。REQ-344・345)。

木は Si-R と同じ行頭スコープ表の平らな読み方(flat_tree。ADR-0024)で組む。表は flat_scopes の GAIA_SCOPES。
`#` で始まる行(前の空白は可)と空の行は木に入れない(`# Configuration of …`・`# Language version: …`・`# Exported by … on <日時>` の
ヘッダーのコメントなど。行番号はもとの行のまま)。

写しは 2 段で読む(REQ-345。ADR-0234 決定 3)。Junos・PAN-OS と同じく、写しの表で書式に当たった行の写しが失敗すると後ろのパターンに進まないので、
葉ごとに次の順で試す。

1. よく使う葉を、共通スキーマ(interfaces・static_routes・global)へ IR の写しの表(`interfaces.gaia…`・`static_routes.gaia…`・`global.gaia…`)で写す。
   写せた葉は解釈済みにし、汎用の形には入れない。REQ-344・347 の二重の判定によらず入る(写す欄に秘密は無い。AI に送るときの欄の伏せ方は
   llm_analyzer/fields.py の決まり)。
2. 写せなかった葉(書式に合わない・範囲の外の数・アドレスとして読めない値・写す対象でないスコープ)は、次の汎用の形のパターンで読み直す(二重の判定つき)。

写す対象(最上位のスコープの種類で決める。ADR-0234 の表):

- インターフェース(スコープ `interface <名前>`): スコープ 1 つに Interface を 1 つ作る。state・ipv4-address・comments・mtu・link-speed・vlan を写す。
  type は名前で決める(`.` を含む名前〔`<名前>.<N>`〕は routed、`bond` + 数字で始まる名前は port_channel、`lo` で始まる名前〔`loopback` を含む〕は loopback、
  ほかは routed)
- ボンディング(スコープ `bonding group <N>`): スコープがあれば `bond<N>`(port_channel)を作り、`interface <名前>` の葉でその名前の Interface の
  channel_group を {"id": N, "mode": "bond"} にする
- スタティックルート(スコープ `static-route <宛先>`): 宛先は `default`(0.0.0.0/0)か、ホストの部分が 0 の IPv4 の `<A>/<長さ>`。読めない宛先は写さない。
  葉 1 つを経路 1 本にする(スコープの順、その中の葉の順)。priority は IR に欄が無いので distance に持つ。vrf はいつも None
- 全体(スコープ hostname・domainname・timezone・dns・ntp・syslog・snmp): global の同じ名前の欄。snmp は location・contact だけ
- 値を 1 つしか持てない欄に違う値が 2 回書かれたら、後の行の値を使い、両方の行番号を食い違い(DuplicateLine。値は持たない)として返す
  (Junos・PAN-OS・FortiGate と同じ。Cisco のホスト名などの「最初の行を使う」とは逆)。NTP・syslog の一覧は同じ欄の 2 行を両方入れる(Cisco と同じ)

写さなかった葉は、次の汎用の形で `vendor_specific["checkpoint_gaia"]` に写す(Junos。ADR-0224 決定 6 と同じ形)。

- `vendor_specific["checkpoint_gaia"]` = `{"config": {"lines": [葉のテキストの昇順。同じ文字列は 1 つ], "blocks": {スコープの名前: 同じ形}}}`。
  スコープの名前は表の name(`interface eth1`・`hostname`・`static-route default` など。set・add の動詞は入らない)。
  Gaia の表は最上位のスコープ 1 段だけなので、最上位の `lines` はいつも空(最上位の葉は表に当たらない行で、未解釈のまま)
- `lines` は並びを無視して、要素ごとの追加・削除で比べる(core の `_SET_LISTS`)。値だけが変わった葉は、削除 1 件と追加 1 件で出る
- 送らない行は入れない(ADR-0224 決定 6 と同じ。Gaia は判定を 2 つにする): 葉のテキストを、(1) 残りの部分だけをスコープの名前(外側から順)を
  祖先にして判定した結果と、(2) スコープの名前と残りをつないだ 1 行(`snmp usm user …`。動詞 set・add は付けない)を祖先なしで判定した結果の
  両方が、Masker().judge_alone で decision が "plain" のときだけ入れる。どちらかが plain でなければ入れず、未解釈のままにする。
  (2) は、語 `snmp` のように秘密を示す語がスコープの名前の側にあるとき、祖先の中では判定で見られず、SNMPv3 の合言葉・ハッシュ
  (`add snmp usm user … auth-pass-phrase …`・`privacy-pass-hash …`)が入ってしまうのを防ぐためのもの。伏せる行(mask)も入れない。
  判定器は既定の引数で、1 回の extract で 1 つ作る。
  同じ判定(first_not_plain)を、送る側(llm_analyzer の payload・redact・config-facts の伏せる入口)も行頭スコープ表で読む機種(checkpoint_gaia・fujitsu_sir。REQ-347)のときだけ
  未解釈の行に当てる。伏せる表・伏せる処理(mask_patterns・masking)は変えない。
  `snmp location`・`contact` は、つないだ 1 行が語 `snmp` で送らない側になるが、IR の snmp.location・contact に写す(Cisco の
  `snmp-server location`・`contact` と同じ扱い。欄は伏せない)ので、AI に送る側になる(REQ-345)。`community`・`usm user`・`traps` などほかの葉は今のまま
- 葉が 1 つも入らなかったスコープ(全部が送らない側だった・全部が共通スキーマに写った)は鍵ごと置かない。ただしスコープだけの実在の行(`set foo` で
  残りの語が無い行)は、判定が "plain" なら `{"lines": [], "blocks": {}}` で置く(interface・bonding group の実在の行は IR に写すので置かない)
- スコープだけの行の 2 回目以降(木では繰り返しのノード)は写さない(木の位置が 1 回目と同じなので、解釈済みの記録は 1 回目のまま。重複の警告は木が出す)
- 表に当たらない行(`delete …` など)は最上位の 1 つのノードになり、未解釈のまま unparsed に入る
"""

import ipaddress
import re
from collections.abc import Sequence
from typing import Any, cast

from driftpilot.config_parser.flat_scopes import GAIA_SCOPES
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree, build_flat_tree
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

PLATFORM = "checkpoint_gaia"
# 木の組み立て(`#` の行の外し方)の版。表の指紋に入る。`#` の行の扱いを変えたら上げる
TREE_VERSION = "gaia-tree-v1"

SCOPE_NODE = "scope"
SCOPE_LEAF = "leaf"

# IR の写しの表の scope(写しの種類)。最上位のスコープの種類で決まる
KIND_INTERFACE = "interface"
KIND_INTERFACE_NODE = "interface_node"
KIND_BONDING = "bonding"
KIND_BONDING_NODE = "bonding_node"
KIND_ROUTE = "route"
# 全体の写しの種類は、スコープの名前そのもの(hostname・domainname・timezone・dns・ntp・syslog・snmp)
GLOBAL_KINDS = ("hostname", "domainname", "timezone", "dns", "ntp", "syslog", "snmp")

# 行頭スコープ表(GAIA_SCOPES)の ID
_ID_INTERFACE = "FLAT-11"
_ID_ROUTE = "FLAT-12"
_ID_BONDING = "FLAT-14"
_ID_OTHER = "FLAT-15"

_DEST_FORM = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}")
_VLAN_RANGE = range(1, 4095)
_PRIORITY_RANGE = range(1, 9)


def build_gaia_tree(text: str) -> FlatTree:
    """`#` で始まる行(前の空白は可)を空の行に置き換えて、行頭スコープ表で木にする。行の数は変わらないので行番号はもとのまま。"""
    lines = [("" if raw.lstrip().startswith("#") else raw) for raw in text.splitlines()]
    return build_flat_tree("\n".join(lines), GAIA_SCOPES, PLATFORM)


def _interface_type(name: str) -> str:
    """名前で決める type。上から順に当てる。`.` を含む名前(`<名前>.<N>`。Cisco のサブインターフェースと同じ)は routed。"""
    if "." in name:
        return "routed"
    if re.match(r"bond\d", name):
        return "port_channel"
    if name.startswith("lo"):
        return "loopback"
    return "routed"


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


class _Ctx:
    """読んでいる間の状態。judge は送らない行の判定(テキスト・祖先 → 入れてよいか)。node は今読んでいるノード、
    ancestors は node の祖先のスコープの名前(外側から順)、lines はノードがスコープのとき葉を集める先。

    IR の写しの表が使うもの: interfaces(名前 → Interface。初出の順)・routes・glob・dns(欄 → 要素)・mapped(値の食い違いの記録)、
    今読んでいるスコープの名前(name はインターフェース、group はボンディングの番号、dest は経路の宛先)、今読んでいる行の行番号(line)。
    既定の値(eth1・グループ 1・宛先 default)は、パターンの例の行を 1 つの ctx で通すテストのためのもの。
    """

    def __init__(self, judge: Any) -> None:
        self.judge = judge
        self.node: FlatNode | None = None
        self.ancestors: tuple[str, ...] = ()
        self.lines: set[str] = set()
        self.block: dict[str, Any] | None = None
        self.interfaces: dict[str, Interface] = {}
        self.routes: list[StaticRoute] = []
        self.glob: Global = new_global()
        self.dns: dict[str, str] = {}
        self.mapped = _Mapped()
        self.name = "eth1"
        self.group = 1
        self.dest: ipaddress.IPv4Network = ipaddress.IPv4Network("0.0.0.0/0")
        self.line: int | None = None

    def interface(self, name: str) -> Interface:
        """名前の Interface を返す(無ければ作る。type は名前で決める)。"""
        found = self.interfaces.get(name)
        if found is None:
            found = new_interface(name)
            found["type"] = cast(Any, _interface_type(name))
            self.interfaces[name] = found
        return found

    def set_interface(self, name: str, field: str, value: Any) -> None:
        interface = self.interface(name)
        self.mapped.write(("interface", name, field), value, self.line, self.ancestors)
        cast(dict[str, Any], interface)[field] = value

    def set_global(self, key: str, value: Any) -> None:
        self.mapped.write(("global", key), value, self.line, self.ancestors)
        cast(dict[str, Any], self.glob)[key] = value


def _unquote(text: str) -> str:
    """両端が `"` のときだけ外す。"""
    if len(text) >= 2 and text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    return text


# --- 汎用の形(REQ-344) ---


def _scope(m: Any, ctx: _Ctx) -> bool:
    """スコープのノード。実在の行(残りの語の無い行)だけがここに来る。送らない行でなければ、空のまとまりを置く。"""
    if not ctx.judge(m.string, ctx.ancestors):
        return False
    ctx.block = {"lines": [], "blocks": {}}
    return True


def _leaf(m: Any, ctx: _Ctx) -> bool:
    """葉。送らない行(伏せる行も)でなければ、そのスコープの文の集合に足す。送らない行は未解釈のまま。"""
    if not ctx.judge(m.string, ctx.ancestors):
        return False
    ctx.lines.add(m.string)
    return True


# --- インターフェース(スコープ `interface <名前>`) ---


def _interface_scope(m: Any, ctx: _Ctx) -> bool:
    ctx.interface(m[1])
    return True


def _interface_state(m: Any, ctx: _Ctx) -> bool:
    ctx.set_interface(ctx.name, "shutdown", m[1] == "off")
    return True


def _interface_ipv4(m: Any, ctx: _Ctx) -> bool:
    try:
        length = int(m[2])
        address = ipaddress.IPv4Address(m[1])
        if not 0 <= length <= 32:
            return False
        value = ipaddress.IPv4Interface(f"{address}/{length}")
    except ValueError:
        return False
    ctx.set_interface(ctx.name, "ip_address", value)
    return True


def _interface_comments(m: Any, ctx: _Ctx) -> bool:
    text = _unquote(m[1])
    if not text:
        return False
    ctx.set_interface(ctx.name, "description", text)
    return True


def _interface_mtu(m: Any, ctx: _Ctx) -> bool:
    ctx.set_interface(ctx.name, "mtu", int(m[1]))
    return True


def _interface_link_speed(m: Any, ctx: _Ctx) -> bool:
    ctx.set_interface(ctx.name, "speed", m[1])
    return True


def _interface_vlan(m: Any, ctx: _Ctx) -> bool:
    number = int(m[1])
    if number not in _VLAN_RANGE:
        return False
    ctx.interface(ctx.name)
    child = ctx.interface(f"{ctx.name}.{number}")
    child["parent_interface"] = ctx.name
    return True


# --- ボンディング(スコープ `bonding group <N>`) ---


def _bonding_scope(m: Any, ctx: _Ctx) -> bool:
    ctx.interface(f"bond{int(m[1])}")
    return True


def _bonding_member(m: Any, ctx: _Ctx) -> bool:
    ctx.set_interface(m[1], "channel_group", {"id": ctx.group, "mode": "bond"})
    return True


# --- スタティックルート(スコープ `static-route <宛先>`) ---


def _add_route(ctx: _Ctx, next_hop: Any, interface: str | None, distance: int | None, enabled: bool | None) -> None:
    ctx.routes.append(
        {
            "destination": ctx.dest,
            "next_hop": next_hop,
            "interface": interface,
            "vrf": None,
            "distance": distance,
            "enabled": enabled,
        }
    )


def _priority(text: str | None) -> tuple[bool, int | None]:
    """(読めたか, priority)。priority の語が無ければ (True, None)。1〜8 の外は読めない。"""
    if text is None:
        return True, None
    number = int(text)
    return (number in _PRIORITY_RANGE), number


def _route_gateway_address(m: Any, ctx: _Ctx) -> bool:
    try:
        address = ipaddress.IPv4Address(m[1])
    except ValueError:
        return False
    ok, distance = _priority(m[2])
    if not ok:
        return False
    _add_route(ctx, address, None, distance, m[3] == "on")
    return True


def _route_gateway_logical(m: Any, ctx: _Ctx) -> bool:
    ok, distance = _priority(m[2])
    if not ok:
        return False
    _add_route(ctx, None, m[1], distance, m[3] == "on")
    return True


def _route_discard(m: Any, ctx: _Ctx) -> bool:
    _add_route(ctx, None, m[1], None, None)
    return True


# --- 全体 ---


def _global_hostname(m: Any, ctx: _Ctx) -> bool:
    ctx.set_global("hostname", m[1])
    return True


def _global_domain_name(m: Any, ctx: _Ctx) -> bool:
    ctx.set_global("domain_name", m[1])
    return True


def _global_timezone(m: Any, ctx: _Ctx) -> bool:
    ctx.set_global("timezone", " ".join(m[1].split()))
    return True


def _global_dns_server(m: Any, ctx: _Ctx) -> bool:
    try:
        address = ipaddress.ip_address(m[2])
    except ValueError:
        return False
    value = f"{address} {m[1]}"
    ctx.mapped.write(("global", "dns", m[1]), value, ctx.line, ctx.ancestors)
    ctx.dns[m[1]] = value
    ctx.glob["dns"]["servers"] = sorted(ctx.dns.values())
    return True


def _append_once(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _global_ntp_server(m: Any, ctx: _Ctx) -> bool:
    value = f"{m[2]} {m[1]}"
    if m[3]:
        value += " " + " ".join(m[3].split())
    _append_once(ctx.glob["ntp_servers"], value)
    return True


def _global_logging_host(m: Any, ctx: _Ctx) -> bool:
    try:
        ipaddress.ip_address(m[1])
    except ValueError:
        return False
    value = m[1]
    if m[2]:
        value += " " + " ".join(m[2].split())
    _append_once(ctx.glob["logging_hosts"], value)
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


PATTERNS: tuple[Pattern, ...] = (
    # --- インターフェース ---
    Pattern(
        "interfaces.gaia.scope", KIND_INTERFACE_NODE, r"interface (\S+)", "interfaces.<名前>",
        "interface eth1", _interface_scope,
        note="スコープだけの実在の行(`add interface eth1`)。Interface を作る(無ければ)。スコープがあれば、実在の行が無くても葉を読む前に Interface を 1 つ作る。"
        "type は名前で決める(`.` を含む名前は routed、`bond` + 数字は port_channel、`lo` で始まる名前は loopback、ほかは routed)",
    ),
    Pattern(
        "interfaces.gaia.state", KIND_INTERFACE, r"state (on|off)", "interfaces.<名前>.shutdown",
        "state on", _interface_state,
        note="`state on` は shutdown = 偽、`state off` は shutdown = 真。行が無ければ今の既定の値(偽)。同じ名前に違う値が 2 回あれば後の行の値を使い、食い違いの警告に出す",
    ),
    Pattern(
        "interfaces.gaia.ipv4_address", KIND_INTERFACE, r"ipv4-address (\S+) mask-length (\d+)", "interfaces.<名前>.ip_address",
        "ipv4-address 192.0.2.1 mask-length 24", _interface_ipv4,
        note="ip_address = `<A>/<N>`。A が IPv4 のアドレスとして読めない、または N が 0〜32 の外なら写さず、汎用の形のまま",
    ),
    Pattern(
        "interfaces.gaia.comments", KIND_INTERFACE, r"comments (.+)", "interfaces.<名前>.description",
        'comments "Uplink to core"', _interface_comments,
        note="description = 文。両端が `\"` のときだけ外す。外して空になれば写さず、汎用の形のまま",
    ),
    Pattern(
        "interfaces.gaia.mtu", KIND_INTERFACE, r"mtu (\d+)", "interfaces.<名前>.mtu",
        "mtu 1500", _interface_mtu, note="mtu = 数",
    ),
    Pattern(
        "interfaces.gaia.link_speed", KIND_INTERFACE, r"link-speed (\S+)", "interfaces.<名前>.speed",
        "link-speed 1000M/full", _interface_link_speed, note="speed = 語の原文(`1000M/full` など)",
    ),
    Pattern(
        "interfaces.gaia.vlan", KIND_INTERFACE, r"vlan (\d+)", "interfaces.<名前>.<N>(parent_interface)",
        "vlan 100", _interface_vlan,
        note="`add interface <名前> vlan <N>`。Interface `<名前>.<N>`(type は routed。Cisco のサブインターフェースと同じ)を作り(無ければ)、parent_interface = `<名前>`。N が 1〜4094 の外なら写さず、汎用の形のまま",
    ),
    # --- ボンディング ---
    Pattern(
        "interfaces.gaia_bonding.scope", KIND_BONDING_NODE, r"bonding group (\d+)", "interfaces.bond<N>",
        "bonding group 1", _bonding_scope,
        note="スコープだけの実在の行(`add bonding group 1`)。Interface `bond<N>`(type は port_channel)を作る(無ければ)。"
        "スコープがあれば、実在の行が無くても葉を読む前に `bond<N>` を作る",
    ),
    Pattern(
        "interfaces.gaia_bonding.member", KIND_BONDING, r"interface (\S+)", "interfaces.<名前>.channel_group",
        "interface eth3", _bonding_member,
        note="`add bonding group <N> interface <名前>`。その名前の Interface(無ければ作る。type は名前で決める)の channel_group = {\"id\": N, \"mode\": \"bond\"}。"
        "同じ名前が 2 つのグループにあれば後の行の値を使い、食い違いの警告に出す。`mode …`・`lacp-rate …`・`mii-interval …` などほかの葉は汎用の形",
    ),
    # --- スタティックルート ---
    Pattern(
        "static_routes.gaia.gateway_address", KIND_ROUTE, r"nexthop gateway address (\S+)(?: priority (\d+))? (on|off)", "static_routes[]",
        "nexthop gateway address 192.0.2.254 on", _route_gateway_address,
        note="next_hop = A、interface = None、distance = priority(無ければ None)、enabled = `on` なら真・`off` なら偽、vrf = None。"
        "destination は `default`(0.0.0.0/0)か、ホストの部分が 0 の IPv4 の `<A>/<長さ>`(読めない宛先のスコープは全部の葉を汎用の形のまま)。"
        "priority は Gaia の同じ宛先の次の候補の優先度(1〜8)で、IR に欄が無いので distance に持つ。A が読めない・priority が範囲の外なら写さない。"
        "経路は葉の順(スコープの順、その中の葉の順)に 1 本ずつ。経路の機種固有値(route_vendor)は置かない",
    ),
    Pattern(
        "static_routes.gaia.gateway_logical", KIND_ROUTE, r"nexthop gateway logical (\S+)(?: priority (\d+))? (on|off)", "static_routes[]",
        "nexthop gateway logical eth1 on", _route_gateway_logical,
        note="next_hop = None、interface = 名前。distance・enabled・vrf は gateway address と同じ。priority が範囲の外なら写さない",
    ),
    Pattern(
        "static_routes.gaia.discard", KIND_ROUTE, r"nexthop (blackhole|reject)", "static_routes[]",
        "nexthop blackhole", _route_discard,
        note="next_hop = None、interface = `blackhole` / `reject`、distance = None、enabled = None、vrf = None。`off` だけの葉(経路そのものを止める)・ほかの形は汎用の形のまま",
    ),
    # --- 全体 ---
    Pattern(
        "global.gaia.hostname", "hostname", r"(\S+)", "global.hostname",
        "gw-test1", _global_hostname,
        note="スコープ `hostname` の葉。違う値が 2 回あれば後の行の値を使い、食い違いの警告に出す(Cisco と逆。木はこの食い違いを見つけられないので、写す側で足す)",
    ),
    Pattern(
        "global.gaia.domain_name", "domainname", r"(\S+)", "global.domain_name",
        "example.test", _global_domain_name, note="スコープ `domainname` の葉。食い違いの扱いはホスト名と同じ",
    ),
    Pattern(
        "global.gaia.timezone", "timezone", r"(\S.*)", "global.timezone",
        "Asia / Tokyo", _global_timezone,
        note="スコープ `timezone` の葉の原文(空白は 1 つにする)。食い違いの扱いはホスト名と同じ",
    ),
    Pattern(
        "global.gaia.dns_server", "dns", r"(primary|secondary|tertiary) (\S+)", "global.dns.servers[]",
        "primary 192.0.2.53", _global_dns_server,
        note="`<欄> <A>` → 「`<A>`(正規の書き方) `<欄>`」(FortiGate の dns.servers と同じ形)。欄ごとに後の行の値を使い(違う値は食い違いの警告に出す)、"
        "文字列の昇順に並べる。IPv4・IPv6 として読めなければ写さない",
    ),
    Pattern(
        "global.gaia.ntp_server", "ntp", r"server (primary|secondary) (\S+)(?: (.+))?", "global.ntp_servers[]",
        "server primary 192.0.2.123 version 4", _global_ntp_server,
        note="`server <欄> <A または名前> [<残り>]` → 「`<A または名前>` `<欄>`[ `<残り>`]」(原文のまま。残りは語を空白 1 つでつなぐ)。"
        "出た順に並べ、同じ文字列は重ねない(Cisco と同じ。同じ欄の 2 行は両方を入れる)。`ntp active on|off` はこの表に当たらず、汎用の形",
    ),
    Pattern(
        "global.gaia.logging_host", "syslog", r"log-remote-address (\S+)(?: (.+))?", "global.logging_hosts[]",
        "log-remote-address 192.0.2.20 level info", _global_logging_host,
        note="`log-remote-address <A> [<残り>]` → 「`<A>`[ `<残り>`]」(A は IPv4・IPv6 として読めるものだけで、原文のまま。残りは語を空白 1 つでつなぐ)。"
        "出た順に並べ、同じ文字列は重ねない。`syslog` のほかの葉は汎用の形",
    ),
    Pattern(
        "global.gaia.snmp_location", "snmp", r"location (.+)", "global.snmp.location",
        'location "LAB-ROOM"', _snmp_text("location"),
        note="文の両端の `\"` を外す(空になれば写さない)。スコープ `snmp` の葉は、つないだ 1 行が語 `snmp` で送らない側になり、REQ-344 では汎用の形に入らなかったが、"
        "IR の snmp.location に写すと Cisco の `snmp-server location` と同じ扱い(欄は伏せない)になり、AI に送る側になる。"
        "`community`・`usm user`・`traps` などほかの葉は写さない(今のまま)",
    ),
    Pattern(
        "global.gaia.snmp_contact", "snmp", r"contact (.+)", "global.snmp.contact",
        'contact "NOC-TEAM"', _snmp_text("contact"),
        note="snmp.location と同じ(文の両端の `\"` を外し、欄は伏せない)",
    ),
    # --- 汎用の形(写さなかった葉) ---
    Pattern(
        "vendor_specific.gaia_generic.scope", SCOPE_NODE, r".+", "vendor_specific.checkpoint_gaia.config(.blocks)",
        "hostname", _scope,
        note="スコープのノード(行頭スコープ表の name。set・add の動詞は入らない)。まとまりの鍵はスコープの名前。"
        "葉が 1 つ以上入ったとき(か、残りの語の無い実在の行が入ったとき)に置く。暗黙のノード(実在の行でない)は何も置かない。"
        "葉が全部送らない側だったスコープは鍵ごと置かない。スコープだけの行の 2 回目以降は写さない(木の位置が 1 回目と同じなので、解釈済みの記録は 1 回目のまま)。"
        "送らない行(スコープ名の判定が「そのまま送る」でないもの)は置かず、未解釈のまま。interface・bonding group の実在の行は IR の写しの表が読む",
    ),
    Pattern(
        "vendor_specific.gaia_generic.leaf", SCOPE_LEAF, r".+", "vendor_specific.checkpoint_gaia.config(.blocks.<名前>.lines)",
        "ipv4-address 192.0.2.1 mask-length 24", _leaf,
        note="スコープの子の葉のうち、IR の写しの表に当たらない・写せなかったもの(スコープの部分を除いた残りの語。前後の空白だけ外れ、中の空白はそのまま)。"
        "葉の集合(昇順・同じ文字列は 1 つ)に足す。送らない行は置かず、未解釈のまま。送らない行は、次の 2 つの判定の"
        "どちらかが「そのまま送る」でないもの(伏せる行を含む): 残りの部分をスコープの名前を祖先にして判定した結果、"
        "スコープの名前と残りをつないだ 1 行(`snmp usm user …`)を祖先なしで判定した結果",
    ),
)


def joined_line(ancestors: Sequence[str], text: str) -> str:
    """スコープの名前(外側から順)と残りを空白 1 つでつないだ 1 行(`snmp usm user …`)。動詞(set・add)は付けない。"""
    return " ".join([*(a.strip() for a in ancestors), text.strip()])


def first_not_plain(masker: Any, text: str, ancestors: Sequence[str]) -> Any:
    """Gaia の行の判定。次の 2 つがどちらも「そのまま送る」(plain)なら None、そうでなければ plain でなかった方の判定の結果を返す。

    1. 残りの部分を、スコープの名前を祖先にして判定した結果(今までの判定)
    2. スコープの名前と残りをつないだ 1 行(joined_line)を、祖先なしで判定した結果

    2 は、語 `snmp` のように秘密を示す語がスコープの名前の側にあるとき、祖先の中では判定で見られない(`set snmp usm user … privacy-pass-phrase …` など)
    ことを補う。祖先が無い行(1 つ目と同じ行)では 2 を省く。判定は使い捨ての記号表(judge_alone)で行い、判定器の記号表を変えない。
    伏せる表と伏せる処理(mask_patterns・masking)は変えず、この関数は呼ぶ側(読み方と送る側)に置く。
    """
    judged = masker.judge_alone(text, tuple(ancestors))
    if judged.decision != "plain":
        return judged
    if not ancestors:
        return None
    joined = masker.judge_alone(joined_line(ancestors, text), ())
    return None if joined.decision == "plain" else joined


def _judge_function() -> Any:
    """送らない行の判定(テキスト・祖先 → 入れてよいか)。判定器は 1 つだけ作る。"""
    # llm_analyzer.masking は config_parser を読み込むため、輪を避けて呼ぶときに読む(junos._judge_function と同じ)
    from driftpilot.llm_analyzer.masking import Masker

    masker = Masker()

    def judge(text: str, ancestors: tuple[str, ...]) -> bool:
        return first_not_plain(masker, text, ancestors) is None

    return judge


def _kind_of(node: FlatNode, ancestors: tuple[str, ...], ctx: _Ctx) -> str | None:
    """最上位のスコープの写しの種類。写す対象でなければ None。スコープの名前から、読むスコープの値(name・group・dest)を ctx に置く。"""
    if ancestors or node.scope_id is None:
        return None
    if node.scope_id == _ID_INTERFACE:
        ctx.name = node.text.removeprefix("interface ")
        return KIND_INTERFACE
    if node.scope_id == _ID_BONDING:
        ctx.group = int(node.text.removeprefix("bonding group "))
        return KIND_BONDING
    if node.scope_id == _ID_ROUTE:
        dest = node.text.removeprefix("static-route ")
        # 宛先は `default` か `<4 つの数>/<1〜2 桁の数>` だけ(長さの無いアドレスやマスクの書き方は、書かれた値を書き換えないので読まない)
        if dest != "default" and _DEST_FORM.fullmatch(dest) is None:
            return None
        try:
            ctx.dest = ipaddress.IPv4Network("0.0.0.0/0" if dest == "default" else dest, strict=True)
        except ValueError:  # 読めない宛先・ホストの部分が 0 でない宛先(書かれた値を書き換えない)
            return None
        return KIND_ROUTE
    if node.scope_id == _ID_OTHER and node.text in GLOBAL_KINDS:
        return node.text
    return None


def _read_scope(node: FlatNode, ancestors: tuple[str, ...], interpreted: InterpretedLines, ctx: _Ctx) -> dict[str, Any] | None:
    """スコープのノード 1 つを、{"lines": …, "blocks": …} にする。置くものが無ければ None(葉は入れた分だけ解釈済みにする)。

    葉は先に IR の写しの表で試し、写せなければ汎用の形で読む。
    """
    ctx.node = node
    ctx.ancestors = ancestors
    ctx.block = None
    kind = _kind_of(node, ancestors, ctx)
    if kind == KIND_INTERFACE:
        ctx.interface(ctx.name)
    elif kind == KIND_BONDING:
        ctx.interface(f"bond{ctx.group}")
    bare = False
    if node.line_no is not None:
        ctx.line = node.line_no
        node_kind = {KIND_INTERFACE: KIND_INTERFACE_NODE, KIND_BONDING: KIND_BONDING_NODE}.get(kind or "")
        pattern = apply_first(PATTERNS, node_kind or SCOPE_NODE, node.text, ctx)
        if pattern is not None:
            # interface・bonding group の実在の行は IR に写す(汎用の形には置かない)
            bare = node_kind is None
            interpreted.mark(node, pattern.id)
    names = (*ancestors, node.text)
    lines: set[str] = set()
    blocks: dict[str, Any] = {}
    for child in node.children:
        if child.repeated_scope:
            continue
        if child.scope_id is not None:
            sub = _read_scope(child, names, interpreted, ctx)
            if sub is not None:
                blocks[child.text] = sub
            continue
        ctx.node = child
        ctx.ancestors = names
        ctx.line = child.line_no
        if kind is not None:
            mapped = apply_first(PATTERNS, kind, child.text, ctx)
            if mapped is not None:
                interpreted.mark(child, mapped.id)
                continue
        ctx.lines = set()
        pattern = apply_first(PATTERNS, SCOPE_LEAF, child.text, ctx)
        if pattern is not None:
            lines |= ctx.lines
            interpreted.mark(child, pattern.id)
    if not (bare or lines or blocks):
        return None
    return {"lines": sorted(lines), "blocks": blocks}


def extract(
    tree: FlatTree, interpreted: InterpretedLines
) -> tuple[
    dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
    dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Vlan], dict[str, Acl],
    dict[str, Any], list[DuplicateLine],
]:
    """(interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, global, vlans, acls,
    vendor_specific["checkpoint_gaia"], 値の食い違い) を返す。bgp・ospf・acls などはいつも空(写さない)。

    interfaces は名前の初出の順、static_routes はスコープの順(その中は葉の順)。global は new_global() に写した値を入れたもの。
    vendor_specific["checkpoint_gaia"] = {"config": {"lines": [最上位の葉。いつも空], "blocks": {スコープの名前: {"lines": …, "blocks": …}}}}。
    写さなかった葉だけを持ち、葉が 1 つも残らなかったスコープは鍵ごと置かない。
    値の食い違いは、1 つしか持てない欄に違う値が書かれた記録(後の行のスコープと、前の行・後の行の行番号。値は持たない)。
    同じスコープの重複は木が警告に出し、行番号を共有する記録は 1 件にまとまる。
    """
    ctx = _Ctx(_judge_function())
    blocks: dict[str, Any] = {}
    for node in tree.children:
        if node.scope_id is None:
            continue
        block = _read_scope(node, (), interpreted, ctx)
        if block is not None:
            blocks[node.text] = block
    config = {"lines": [], "blocks": blocks}
    return (
        ctx.interfaces, ctx.routes, None, {}, None, {}, {}, {}, {}, ctx.glob, {}, {}, {"config": config}, ctx.mapped.conflicts,
    )
