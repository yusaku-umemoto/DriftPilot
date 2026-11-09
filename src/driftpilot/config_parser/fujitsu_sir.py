"""富士通 Si-R の木(flat_tree)を IR の interfaces・static_routes・bgp・vendor_specific に写す extractor
(REQ-21・REQ-30・REQ-47・REQ-49・REQ-51。ADR-0024 決定7〜8、ADR-0026 決定4〜6、ADR-0038 第1段、ADR-0039、ADR-0041)。

写すのは `ether <n> <n>`(FLAT-01)、`lan <n>`(FLAT-02、VRRP と経路を含む)、
`remote <n>`(FLAT-03)とその下の `remote <n> ap <m>`(FLAT-04)、最上位の `bgp as`・`bgp id` の行と `bgp neighbor <n>`(FLAT-07)、
ほかのスコープ(FLAT-05・06・08・09・10)と最上位の行(REQ-51)。
最上位のほかの `bgp <残り>` の行は bgp.options に写す(ADR-0121 決定3、REQ-166)。
行の判定は Cisco 側と同じ Pattern と apply_first で行い(ADR-0011)、書式に合って写せた行だけを解釈済みにする。

- 写した値はいったん「項目 → (値, 行番号)」に集め、最後に Interface を組む。同じ項目に違う値が来たら
  後の行の値を使い(Cisco 側と同じ)、食い違いとして行番号を記録する(値は記録しない)
- 共通スキーマに無いもの(ブロードキャストの種類、VLAN の割り当て、VRRP の use / trap / ad / vaddr)は
  vendor_specific["fujitsu_sir"]["interfaces"][名前] に置く
- VRRP の認証鍵は IR のどこにも入れない。`vrrp auth` の行は解釈済みにし、鍵があるか(none 以外か)だけを残す
- 経路はスコープの中に書かれる。interface にはスコープの名前を入れ、Interface の側には残さない。
  経路の番号と末尾2つの数(意味が未確認)は distance に写さず、vendor_specific["fujitsu_sir"]["static_routes"]
  (static_routes と同じ並び)に置く

BGP(REQ-47)は行の並びに依らないよう、行を集めてから決める。
- `bgp as` の行(AS 番号として読めるもの)が無ければ bgp は None で、`bgp id`・ほかの `bgp` の行と相手の行はすべて未解釈
- 相手の鍵は `address` の行で決める。`address` の無い番号と、同じアドレスを持つ番号は写さない(その番号の行はすべて未解釈)
- 認証鍵(`authentication …`)は IR のどこにも入れない。password_configured を True にし、行は解釈済みにする
- 相手の番号は定義の位置で、相手の同一性ではない。vendor_specific["fujitsu_sir"]["bgp_neighbors"][鍵] に置く
- 同じ属性の行が2回あれば後の行の値を使う。重複は木が警告し、木が見落とす `authentication` の重複
  (鍵が違う行)は、写した項目の行番号から記録する(鍵は記録しない)

相手(REQ-49。ADR-0039)は Interface を作らず、vendor_specific["fujitsu_sir"]["remotes"][番号] に置く。
- `remote <n>` の経路は今までどおり static_routes。`name` の行は name、ほかの行は attributes(空白を1つにした文字列)
- 接続先 `remote <n> ap <m>` は aps[番号] に、同じ形(name・attributes・secrets)で置く
- 秘密を含む行(12 種類)は値を持たず、コマンドの名前だけを secrets に置く。同じ名前の行が2回以上あれば
  (`ip ospf auth md5key` は鍵の番号も同じとき)、行番号だけを重複として記録する(鍵だけが違う行は木が見落とすため)
- `name`・`ip route` で始まるのに形が合わない行と、秘密の頭で始まる行は、どんな形でも attributes に入れない(未解釈)

残りの行(REQ-51。ADR-0041)は、値を解釈せずに行の集合(attributes・secrets)として写す。
- `ether`・`lan` の今の項目に写らない行は vendor_specific["fujitsu_sir"]["interfaces"][名前] の attributes・secrets
  (`ether` の欄はこれだけ)。今の項目の頭(`use`・`vlan`・`ip address` など)で始まるのに形が合わない行は未解釈
- ほかのスコープの行は vendor_specific["fujitsu_sir"]["scopes"][木のスコープ名]、最上位の行は ["settings"]
- 最上位の `bgp` の行は BGP の読み方のまま。行頭スコープ表の語で始まる最上位の行(木がスコープにできなかった行)は未解釈
- 秘密の行(lan 3 種類・最上位 19 種類)は remote と同じく名前(当たった頭の原文。番号を含む)だけを secrets に置く。
  省ける番号の位置(`<n>`)は無くても当てる。重複は remote と同じ(`answer ppp auth receive add` は利用者の ID も同じとき)
- 別の場所の秘密の頭で始まる行は、どんな形でも attributes に入れない(未解釈)
"""

import re
from bisect import insort
from collections import Counter
from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Interface, IPv4Network, ip_address
from typing import Any

from driftpilot.config_parser.bgp import _asn
from driftpilot.config_parser.device_settings import has_secret
from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree, expand_indexes
from driftpilot.config_parser.ir import (
    Bgp,
    Global,
    Interface,
    Ipsec,
    Ospf,
    PrefixList,
    RouteMap,
    SdwanBlock,
    StaticRoute,
    Vrrp,
    new_bgp,
    new_bgp_neighbor,
    new_global,
    new_interface,
)
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

PLATFORM = "fujitsu_sir"
# 写すスコープ(行頭スコープ表の ID → この表の scope)
SCOPES = {
    "FLAT-01": "ether", "FLAT-02": "lan", "FLAT-03": "remote", "FLAT-07": "bgp neighbor",
    # REQ-51 §2-2。行はすべて行の集合として scopes に写す
    "FLAT-05": "other_scope", "FLAT-06": "other_scope", "FLAT-08": "other_scope", "FLAT-09": "other_scope",
    "FLAT-10": "other_scope",
}
# FLAT-04(remote N ap M)は SCOPES に入れず、remote のスコープの子として読む。この表の scope は remote_ap
_REMOTE_AP_SCOPE_ID = "FLAT-04"
# 固定キーに写す相手の属性。これで始まるのに形が合わない行は options に入れず未解釈にする(authentication はどんな形でも)
_BGP_FIXED_ATTRIBUTES = ("address", "as", "source", "authentication")
# 秘密を含む行(REQ-49 §2-3。ADR-0039 決定4)。(パターンの id の名前, 行の頭, 例)。値は IR のどこにも入れない
_REMOTE_SECRETS = (
    ("secret_ospf_textkey", "ip ospf auth textkey", "ip ospf auth textkey text EXAMPLE-ONLY encrypted"),
    ("secret_ospf_md5key", "ip ospf auth md5key", "ip ospf auth md5key 1 EXAMPLE-ONLY encrypted"),
    ("secret_rip_use", "ip rip use", "ip rip use on on 1 off EXAMPLE-ONLY"),
)
_AP_SECRETS = (
    ("secret_ppp_auth_send", "ppp auth send", "ppp auth send user-a EXAMPLE-ONLY encrypted"),
    ("secret_ppp_auth_receive", "ppp auth receive", "ppp auth receive user-a EXAMPLE-ONLY encrypted"),
    ("secret_ipsec_send_encrypt", "ipsec send encrypt", "ipsec send encrypt aes-cbc-256 text EXAMPLE-ONLY encrypted"),
    ("secret_ipsec_send_auth", "ipsec send auth", "ipsec send auth hmac-sha256 text EXAMPLE-ONLY encrypted"),
    ("secret_ipsec_receive_encrypt", "ipsec receive encrypt", "ipsec receive encrypt aes-cbc-256 text EXAMPLE-ONLY encrypted"),
    ("secret_ipsec_receive_auth", "ipsec receive auth", "ipsec receive auth hmac-sha256 text EXAMPLE-ONLY encrypted"),
    ("secret_ike_shared_key", "ike shared key", "ike shared key text EXAMPLE-ONLY encrypted"),
    ("secret_ike_eap_auth_send", "ike eap auth send", "ike eap auth send user-a EXAMPLE-ONLY encrypted"),
    ("secret_v6plus_auth", "v6plus auth", "v6plus auth user-a EXAMPLE-ONLY encrypted"),
)
# REQ-51 §2-4。行の頭の `<n>` は省ける番号の位置(数の語が無くても当てる)
_LAN_SECRETS = (
    ("secret_lan_ospf_textkey", "ip ospf auth textkey", "ip ospf auth textkey text EXAMPLEONLY encrypted"),
    ("secret_lan_ospf_md5key", "ip ospf auth md5key", "ip ospf auth md5key 1 EXAMPLEONLY encrypted"),
    ("secret_lan_rip_use", "ip rip use", "ip rip use v2 v2 1 off EXAMPLEONLY"),
)
_TOP_SECRETS = (
    ("secret_password_admin", "password admin set", "password admin set EXAMPLEONLY encrypted"),
    ("secret_password_user", "password user set", "password user set EXAMPLEONLY encrypted"),
    ("secret_answer_ppp_auth", "answer ppp auth receive add", "answer ppp auth receive add user-a EXAMPLEONLY encrypted"),
    ("secret_template_dvpn_auth", "template <n> dvpn server <n> auth", "template 0 dvpn server 0 auth user-a EXAMPLEONLY encrypted"),
    ("secret_template_ike_shared_key", "template <n> ike shared key", "template 0 ike shared key text EXAMPLEONLY encrypted"),
    ("secret_dvpn_client_auth", "dvpn client <n> server <n> auth", "dvpn client 0 server 0 auth user-a EXAMPLEONLY encrypted"),
    ("secret_aaa_user_password", "aaa <n> user <n> password", "aaa 0 user 0 password EXAMPLEONLY encrypted"),
    ("secret_aaa_user_ike_shared_key", "aaa <n> user <n> ike shared key", "aaa 0 user 0 ike shared key text EXAMPLEONLY encrypted"),
    (
        "secret_aaa_radius_client_info", "aaa <n> radius server client-info <n> secret",
        "aaa 0 radius server client-info 0 secret EXAMPLEONLY encrypted",
    ),
    (
        "secret_aaa_radius_auth", "aaa <n> radius client server-info auth <n> secret",
        "aaa 0 radius client server-info auth 0 secret EXAMPLEONLY encrypted",
    ),
    (
        "secret_aaa_radius_accounting", "aaa <n> radius client server-info accounting <n> secret",
        "aaa 0 radius client server-info accounting 0 secret EXAMPLEONLY encrypted",
    ),
    ("secret_macauth_password", "macauth password", "macauth password EXAMPLEONLY encrypted"),
    ("secret_arpauth_password", "arpauth password", "arpauth password EXAMPLEONLY encrypted"),
    ("secret_snmp_manager", "snmp manager <n>", "snmp manager 0 192.0.2.10 EXAMPLEONLY off disable"),
    ("secret_snmp_user_auth", "snmp user <n> auth", "snmp user 0 auth sha EXAMPLEONLY encrypted"),
    ("secret_snmp_user_priv", "snmp user <n> priv", "snmp user 0 priv aes EXAMPLEONLY encrypted"),
    ("secret_updateinfo", "updateinfo", "updateinfo 192.0.2.20 user-a EXAMPLEONLY /update"),
    ("secret_domainlistinfo", "domainlistinfo", "domainlistinfo 192.0.2.20 user-a EXAMPLEONLY /list"),
    ("secret_sim_apn", "sim apn <n>", "sim apn 1 name apn.example user user-a password EXAMPLEONLY"),
)
# どの場所の秘密の頭でも、汎用の行(attributes)には入れない
_ALL_SECRETS = (*_REMOTE_SECRETS, *_AP_SECRETS, *_LAN_SECRETS, *_TOP_SECRETS)
# 何本も書ける秘密の行。重複は次の語(鍵の番号・利用者の ID)も同じ行だけ。次の語は IR にも警告にも入れない
_KEY_ID_SECRETS = ("ip ospf auth md5key", "answer ppp auth receive add")
# `ip rip use` は後ろの語がこの数以上のとき(5つ目がパスワード)だけ秘密
_RIP_USE_SECRET_WORDS = 5
# 今の項目の頭(REQ-51 §2-1)。表に当たらなければ attributes に入れず未解釈
_ETHER_FIXED_HEADS = ("use", "vlan untag", "vlan tag")
_LAN_FIXED_HEADS = ("ip address", "ip route", "vlan", "vrrp")
# 最上位で読まない行の1語目(行頭スコープ表の語。REQ-51 §2-3)。大文字・小文字は区別する
_TOP_SCOPE_WORDS = frozenset((
    "ether", "lan", "remote", "bridgegroup", "pseudo-ether", "bgp", "serverinfo", "routemanage", "wan", "etheroam",
    "backup", "ethergroup",
))


@dataclass
class _Ctx:
    """1つのスコープ(ether N N / lan N / remote N / remote N ap M / ほかのスコープ)か、最上位の行を読んでいる間の状態。

    attributes は行の集合(空白を1つにした文字列)。secrets は秘密の行の
    (コマンドの名前, 鍵の番号・利用者の ID(_KEY_ID_SECRETS だけ)) → 行番号。鍵の値は持たない。
    """

    scope_name: str = ""
    node: FlatNode | None = None
    interface: Interface | None = None
    values: dict[tuple[str, ...], tuple[Any, int | None]] = field(default_factory=dict)
    conflicts: dict[tuple[str, ...], set[int]] = field(default_factory=dict)
    routes: list[StaticRoute] = field(default_factory=list)
    route_details: list[dict[str, Any]] = field(default_factory=list)
    attributes: set[str] = field(default_factory=set)
    secrets: dict[tuple[str, str | None], list[int]] = field(default_factory=dict)

    def write(self, key: tuple[str, ...], value: Any) -> bool:
        line = self.node.line_no if self.node is not None else None
        previous = self.values.get(key)
        if previous is not None and previous[0] != value and previous[1] is not None and line is not None:
            self.conflicts.setdefault(key, {previous[1]}).add(line)
        self.values[key] = (value, line)
        return True

    def get(self, *key: str) -> Any:
        found = self.values.get(key)
        return None if found is None else found[0]

    def groups(self) -> list[str]:
        """VRRP の Si-R のグループ番号(出てきた順ではなく番号順)。"""
        return sorted({key[2] for key in self.values if key[:2] == ("vrrp", "group")}, key=int)

    def generic(self) -> dict[str, list[str]]:
        """行の集合の欄。attributes・secrets は文字列の昇順(同じ文字列は1つ)。"""
        return {"attributes": sorted(self.attributes), "secrets": sorted({name for name, _ in self.secrets})}

    def remote(self) -> dict[str, Any]:
        """相手・接続先の欄(aps は呼んだ側が足す)。"""
        return {"name": self.get("name"), **self.generic()}

    def secret_duplicates(self, scope: tuple[str, ...]) -> list[DuplicateLine]:
        """同じ秘密の行の重複(スコープと行番号だけ。鍵は持たない)。木の記録と行を共有すれば1件にまとまる。"""
        return [DuplicateLine(scope, tuple(sorted(lines))) for lines in self.secrets.values() if len(lines) > 1]

    def keyed_secret_lines(self) -> list[int]:
        """何本も書ける秘密の行(_KEY_ID_SECRETS)の行番号。重複は鍵の番号・利用者の ID で決めるため、木の判定から外す。"""
        return [line for (name, _), lines in self.secrets.items() if name in _KEY_ID_SECRETS for line in lines]


@dataclass
class _BgpCtx:
    """最上位の `bgp as`・`bgp id`・ほかの `bgp` の行、または1つの `bgp neighbor <n>` の行を集める状態。

    lines は表に当たって値を写せた行と、そのパターンの ID。解釈済みにするかは集め終えてから決める。
    written は項目ごとの行番号。同じ項目の行が2行以上あれば、値を比べずに重複として記録する
    (`authentication` は鍵を持たないので値で比べられず、木の検出も鍵を属性の一部とみなして見落とすため)。
    """

    scope: tuple[str, ...] = ()
    node: FlatNode | None = None
    values: dict[str, Any] = field(default_factory=dict)
    options: list[str] = field(default_factory=list)
    lines: list[tuple[FlatNode, str]] = field(default_factory=list)
    written: dict[str, list[int]] = field(default_factory=dict)

    def write(self, key: str, value: Any) -> bool:
        self.values[key] = value
        if self.node is not None and self.node.line_no is not None:
            self.written.setdefault(key, []).append(self.node.line_no)
        return True

    def duplicates(self) -> list[DuplicateLine]:
        """同じ項目の行の重複(スコープと行番号だけ。値は持たない)。木の記録と行を共有すれば1件にまとまる。"""
        return [DuplicateLine(self.scope, tuple(sorted(lines))) for lines in self.written.values() if len(lines) > 1]


def _on_off(state: str) -> bool:
    return state == "on"


# --- ether ---


def _ether_use(m: re.Match[str], ctx: _Ctx) -> bool:
    # use on はポートが有効(shutdown=False)。逆向きにすると差分が全部反転する
    return ctx.write(("shutdown",), not _on_off(m["state"]))


def _vlan_untag(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("vlan_untag",), int(m["id"]))


def _vlan_tag(m: re.Match[str], ctx: _Ctx) -> bool:
    vlans = expand_indexes(m["list"])
    if vlans is None:
        return False
    return ctx.write(("vlan_tag",), tuple(vlans))


def _ether_type(m: None, ctx: _Ctx) -> bool:
    """type と switchport.mode を決める(行は読まない)。tag があれば trunk、untag だけなら access。"""
    interface = ctx.interface
    interface["type"] = "switchport"
    untag, tag = ctx.get("vlan_untag"), ctx.get("vlan_tag")
    switchport = interface["switchport"]
    if tag is not None:
        switchport.update(mode="trunk", trunk_allowed_vlan=tag, native_vlan=untag)
    elif untag is not None:
        switchport.update(mode="access", access_vlan=untag)
    return True


# --- lan ---


def _ip_address(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        address = IPv4Interface(f"{m['addr']}/{m['len']}")
    except ValueError:
        return False
    broadcast = int(m["broadcast"]) if m["broadcast"] is not None else None
    return ctx.write(("ip_address",), (address, broadcast))


def _lan_vlan(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("vlan",), int(m["id"]))


def _vrrp_use(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("vrrp_use",), _on_off(m["state"]))


def _vrrp_auth(m: re.Match[str], ctx: _Ctx) -> bool:
    # 鍵(none 以外の語)は持たない。あるかどうかだけ
    return ctx.write(("vrrp_auth_configured",), m["rest"].split()[0] != "none")


def _vrrp_trap(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("vrrp_trap",), m["value"])


def _vrrp_id(m: re.Match[str], ctx: _Ctx) -> bool:
    try:
        addresses = tuple(IPv4Address(a) for a in m["addresses"].split())
    except ValueError:
        return False
    return ctx.write(("vrrp", "group", m["g"], "id"), (int(m["vrid"]), int(m["priority"]), addresses))


def _vrrp_preempt(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("vrrp", "group", m["g"], "preempt"), _on_off(m["state"]))


def _vrrp_ad(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("vrrp", "group", m["g"], "ad"), m["value"])


def _vrrp_vaddr(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("vrrp", "group", m["g"], f"vaddr_{m['proto']}"), m["value"])


def _lan_type(m: None, ctx: _Ctx) -> bool:
    """type を svi にする(行は読まない)。"""
    ctx.interface["type"] = "svi"
    return True


# --- 経路(lan・remote) ---


def _route(m: re.Match[str], ctx: _Ctx) -> bool:
    """default は 0.0.0.0/0 に揃える。次ホップの組が無い書式(remote)は next_hop=None。"""
    groups = m.groupdict()
    try:
        # ホスト部が立っていても読む(A型と同じ strict=False)
        destination = IPv4Network("0.0.0.0/0" if m["dest"] == "default" else m["dest"], strict=False)
        next_hop = IPv4Address(groups["nh"]) if "nh" in groups else None
    except ValueError:
        return False
    ctx.routes.append({
        "destination": destination, "next_hop": next_hop, "interface": ctx.scope_name,
        "vrf": None, "distance": None, "enabled": None,
    })
    ctx.route_details.append({"number": int(m["number"]), "trailing_numbers": [int(m["n1"]), int(m["n2"])]})
    return True


# --- 相手(remote)と接続先(remote ap)、残りの行(行の集合) ---


def _secret(m: re.Match[str], ctx: _Ctx) -> bool:
    """秘密の行。コマンドの名前(語の間の空白は1つ)だけを持ち、値は捨てる。

    md5key は鍵の番号ごと、`answer ppp auth receive add` は利用者の ID ごとに数える。
    """
    lines = ctx.secrets.setdefault((" ".join(m["head"].split()), m.groupdict().get("key_id")), [])
    if ctx.node is not None and ctx.node.line_no is not None:
        lines.append(ctx.node.line_no)
    return True


def _remote_name(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.write(("name",), m["value"])


def _starts_with(words: list[str], head: str) -> bool:
    """語の並びが行の頭で始まるか(語の単位で比べる)。頭の `<n>` は数の語1つで、無くてもよい。"""
    index = 0
    for token in head.split():
        if token == "<n>":
            if index < len(words) and words[index].isdecimal():
                index += 1
        elif index < len(words) and words[index] == token:
            index += 1
        else:
            return False
    return True


def _is_secret(words: list[str], secrets: tuple[tuple[str, str, str], ...] = (*_REMOTE_SECRETS, *_AP_SECRETS)) -> bool:
    """秘密の頭(場所に依らず secrets のどれか)で始まる行か。`ip rip use` は後ろが5語以上のときだけ。"""
    for _, head, _ in secrets:
        if not _starts_with(words, head):
            continue
        if head != "ip rip use" or len(words) - len(head.split()) >= _RIP_USE_SECRET_WORDS:
            return True
    return False


def _remote_attribute(m: re.Match[str], ctx: _Ctx) -> bool:
    """name・経路・秘密の書式に当たらなかった行(空白は1つにそろえる)を attributes に足す。

    `name`・`ip route` で始まる行(固定の項目の書式に合わなかった行)と、秘密の頭で始まる行
    (別のスコープに書かれた秘密)は、鍵を拾わないよう入れずに未解釈にする。
    """
    words = m["rest"].split()
    if words[0] == "name" or words[:2] == ["ip", "route"] or _is_secret(words):
        return False
    ctx.attributes.add(" ".join(words))
    return True


def _add_generic(
    m: re.Match[str], ctx: _Ctx, fixed_heads: tuple[str, ...] = (), first_words: frozenset[str] = frozenset()
) -> bool:
    """表のほかの書式に当たらなかった行(空白は1つにそろえる)を attributes に足す(REQ-51)。

    今の項目の頭で始まる行(形が合わなかった行)、1語目が first_words の行、どの場所の秘密の頭で始まる行も、
    鍵や壊れた行を拾わないよう入れずに未解釈にする。
    """
    words = m["rest"].split()
    if words[0] in first_words or any(_starts_with(words, head) for head in fixed_heads) or _is_secret(words, _ALL_SECRETS):
        return False
    ctx.attributes.add(" ".join(words))
    return True


def _ether_generic(m: re.Match[str], ctx: _Ctx) -> bool:
    return _add_generic(m, ctx, fixed_heads=_ETHER_FIXED_HEADS)


def _lan_generic(m: re.Match[str], ctx: _Ctx) -> bool:
    return _add_generic(m, ctx, fixed_heads=_LAN_FIXED_HEADS)


def _scope_generic(m: re.Match[str], ctx: _Ctx) -> bool:
    return _add_generic(m, ctx)


def _top_generic(m: re.Match[str], ctx: _Ctx) -> bool:
    # `bgp` の行(BGP の読み方で集める)もここで断る。ctx には触れない
    return _add_generic(m, ctx, first_words=_TOP_SCOPE_WORDS)


# --- BGP ---


def _bgp_as(m: re.Match[str], ctx: _BgpCtx) -> bool:
    local_as = _asn(m["asn"])
    return local_as is not None and ctx.write("local_as", local_as)


def _bgp_id(m: re.Match[str], ctx: _BgpCtx) -> bool:
    try:
        return ctx.write("router_id", str(IPv4Address(m["addr"])))
    except ValueError:
        return False


def _bgp_top_option(m: re.Match[str], ctx: _BgpCtx) -> bool:
    """最上位のほかの `bgp` の行の残り(空白は1つにそろえる)を options に足す(同じ文字列は重ねない。昇順)。"""
    if has_secret(m.string):
        return False
    rest = " ".join(m["rest"].split())
    if rest not in ctx.options:
        insort(ctx.options, rest)
    return True


def _bgp_address(m: re.Match[str], ctx: _BgpCtx) -> bool:
    try:
        return ctx.write("key", str(ip_address(m["addr"])))
    except ValueError:
        return False


def _bgp_remote_as(m: re.Match[str], ctx: _BgpCtx) -> bool:
    remote_as = _asn(m["asn"])
    return remote_as is not None and ctx.write("remote_as", remote_as)


def _bgp_source(m: re.Match[str], ctx: _BgpCtx) -> bool:
    return ctx.write("update_source", m["value"])


def _bgp_authentication(m: re.Match[str], ctx: _BgpCtx) -> bool:
    # 鍵の値は持たない。あるかどうかだけ
    return ctx.write("password_configured", True)


def _bgp_options(m: re.Match[str], ctx: _BgpCtx) -> bool:
    """固定キーに無い属性の行(番号の後ろの残り。空白は1つにそろえる)を options に足す(同じ文字列は重ねない)。"""
    words = m["rest"].split()
    if words[0] in _BGP_FIXED_ATTRIBUTES:
        return False
    rest = " ".join(words)
    if rest not in ctx.options:
        ctx.options.append(rest)
    return True


_IPV4 = r"\d{1,3}(?:\.\d{1,3}){3}"
_ROUTE_DEST = rf"(?P<dest>default|{_IPV4}/\d{{1,2}})"
_ROUTE_TAIL = r"(?P<n1>\d+) (?P<n2>\d+)"
_ROUTE_NOTE = (
    "末尾2つの数は意味が未確認のため distance に写さず vendor_specific.trailing_numbers に置く。"
    "default は 0.0.0.0/0 に揃える。interface にはスコープの名前を入れる"
)
_SECRET_NOTE = (
    "手続き。秘密の行は名前だけ(値は IR のどこにも入れない。ADR-0039 決定4)。行の頭を語の単位で比べ、後ろの形は問わない。"
    "どんな形でも attributes に入れない。同じ名前の行が2回以上あれば、行番号だけを重複として記録する"
)
_ATTRIBUTES_NOTE = (
    "手続き。経路・秘密・name に当たらない行(空白は1つにそろえる)を attributes に足す。並びは文字列の昇順、同じ文字列は1つ。"
    "`name`・`ip route` で始まる行は attributes に入れない(形が合わなければ未解釈)。"
    "秘密の頭で始まる行(別のスコープに書かれた秘密)も入れず未解釈にする"
)


_GENERIC_NOTE = (
    "手続き。表のほかの書式に当たらない行(空白は1つにそろえる。値は解釈しない)を attributes に足す(ADR-0041)。"
    "並びは文字列の昇順、同じ文字列は1つ。秘密の頭(どの場所のものでも)で始まる行は入れず未解釈にする"
)
_SECRET_TARGETS = {
    "remote": "vendor_specific.remotes[].secrets",
    "remote_ap": "vendor_specific.remotes[].aps[].secrets",
    "lan": "vendor_specific.interfaces[].secrets",
    "top": "vendor_specific.settings.secrets",
}


def _secret_pattern(prefix: str, scope: str, name: str, head: str, example: str) -> Pattern:
    """秘密の行の Pattern。行の頭を語の単位で当てる(語の間の空白はいくつでもよい。後ろは空白か行末)。

    頭の `<n>` は省ける番号の位置(数の語があってもなくてもよい)。名前は当たった頭の原文(番号を含む)。
    """
    head_regex = "".join(
        r"(?:\s+\d+)?" if word == "<n>" else (r"\s+" if index else "") + word for index, word in enumerate(head.split())
    )
    match, note = rf"(?P<head>{head_regex})(?:\s.*)?", _SECRET_NOTE
    if "<n>" in head:
        note += "。`<n>` の位置の番号は省いた形でも当て、名前には当たった原文(番号を含む)を入れる"
    if head in _KEY_ID_SECRETS:
        match = rf"(?P<head>{head_regex})(?:\s+(?P<key_id>\S+))?(?:\s.*)?"
        if head == "ip ospf auth md5key":
            note += "。鍵の番号ごとに何本も書けるため、重複は鍵の番号も同じ行だけ"
        else:
            note += "。利用者ごとに何本も書けるため、重複は利用者の ID も同じ行だけ(ID は IR にも警告にも入れない)"
    elif head == "ip rip use":
        match = rf"(?P<head>{head_regex})(?:\s+\S+){{{_RIP_USE_SECRET_WORDS},}}"
        note += "。後ろの語が5つ以上(5つ目がパスワード)のときだけ秘密。4つ以下はふつうの行として attributes に入れる"
    return Pattern(f"{prefix}.{name}", scope, match, _SECRET_TARGETS[scope], example, _secret, note=note)

# 並び＝優先順位。scope は SCOPES の値
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "interfaces.sir_ether.use", "ether", r"use (?P<state>on|off)", "shutdown", "use off", _ether_use,
        note="use on → shutdown=False(ポート有効)、use off → shutdown=True",
    ),
    Pattern(
        "interfaces.sir_ether.vlan_untag", "ether", r"vlan untag (?P<id>\d+)",
        "switchport.access_vlan / switchport.native_vlan", "vlan untag 2", _vlan_untag,
        note="同じポートに vlan tag があれば native_vlan、無ければ access_vlan",
    ),
    Pattern(
        "interfaces.sir_ether.vlan_tag", "ether", r"vlan tag (?P<list>\d[\d,\-]*)", "switchport.trunk_allowed_vlan",
        "vlan tag 10,20-30", _vlan_tag,
        note="値の位置のリストは VLAN の一覧として読む(スコープには展開しない)。展開できない書式は未解釈",
    ),
    Pattern(
        "interfaces.sir_ether.type", "ether", None, "type / switchport.mode(行は読まない。派生値)",
        "ether 1 1 vlan tag 10", _ether_type,
        note="手続き。type=switchport。vlan tag があれば mode=trunk、vlan untag だけなら mode=access、どちらも無ければ None",
    ),
    Pattern(
        "interfaces.sir_lan.ip_address", "lan",
        rf"ip address (?P<addr>{_IPV4})/(?P<len>\d{{1,2}})(?: (?P<broadcast>[0-3]))?",
        "ip_address / vendor_specific.ip_broadcast_type", "ip address 192.0.2.1/24 3", _ip_address,
        note="末尾の数はブロードキャストアドレスの種類(0〜3)で vendor_specific に置く。0〜3 以外や IPv4 として読めないものは未解釈",
    ),
    Pattern(
        "interfaces.sir_lan.vlan", "lan", r"vlan (?P<id>\d+)", "vendor_specific.vlan", "vlan 1", _lan_vlan,
        note="VLAN の割り当て。Cisco の SVI は VLAN 番号を名前に持つため対応する共通キーが無い",
    ),
    Pattern("interfaces.sir_lan.vrrp_use", "lan", r"vrrp use (?P<state>on|off)", "vendor_specific.vrrp.use",
            "vrrp use off", _vrrp_use),
    Pattern(
        "interfaces.sir_lan.vrrp_auth", "lan", r"vrrp auth (?P<rest>\S.*)", "vendor_specific.vrrp.auth_configured",
        "vrrp auth none", _vrrp_auth,
        note="鍵は IR に持たない。none 以外なら auth_configured=True。鍵の変更は structured 差分に出ない(raw と安全網3で見える)",
    ),
    Pattern("interfaces.sir_lan.vrrp_trap", "lan", r"vrrp trap (?P<value>\S+)", "vendor_specific.vrrp.trap",
            "vrrp trap old", _vrrp_trap),
    Pattern(
        "interfaces.sir_lan.vrrp_id", "lan",
        rf"vrrp group (?P<g>\d+) id (?P<vrid>\d+) (?P<priority>\d+) (?P<addresses>{_IPV4}(?: {_IPV4})*)",
        "vrrp[].group / priority / virtual_addresses", "vrrp group 0 id 1 100 192.0.2.254", _vrrp_id,
        note="group には VRID を入れる(Si-R のグループ番号 0〜3 ではない)。id の行があるグループだけが vrrp[] に入る",
    ),
    Pattern(
        "interfaces.sir_lan.vrrp_preempt", "lan", r"vrrp group (?P<g>\d+) preempt (?P<state>on|off)",
        "vrrp[].preempt", "vrrp group 0 preempt on", _vrrp_preempt,
        note="id の行が無いグループの preempt は vendor_specific.vrrp.groups に置く",
    ),
    Pattern("interfaces.sir_lan.vrrp_ad", "lan", r"vrrp group (?P<g>\d+) ad (?P<value>\S+)",
            "vendor_specific.vrrp.groups[].ad", "vrrp group 0 ad 1s", _vrrp_ad),
    Pattern(
        "interfaces.sir_lan.vrrp_vaddr", "lan", r"vrrp group (?P<g>\d+) vaddr (?P<proto>icmp|etherip) (?P<value>\S+)",
        "vendor_specific.vrrp.groups[].vaddr_icmp / vaddr_etherip", "vrrp group 0 vaddr icmp discard", _vrrp_vaddr,
    ),
    Pattern(
        "interfaces.sir_lan.type", "lan", None, "type(行は読まない。派生値)", "lan 0 vlan 1", _lan_type,
        note="手続き。lan はすべて type=svi(VLAN を割り当てる論理インターフェース)",
    ),
    Pattern(
        "static_routes.sir_lan.route", "lan", rf"ip route (?P<number>\d+) {_ROUTE_DEST} (?P<nh>{_IPV4}) {_ROUTE_TAIL}",
        "static_routes[] / vendor_specific.static_routes[].number・trailing_numbers",
        "ip route 0 default 192.0.2.254 1 1", _route, note=_ROUTE_NOTE,
    ),
    Pattern(
        "static_routes.sir_remote.route", "remote", rf"ip route (?P<number>\d+) {_ROUTE_DEST} {_ROUTE_TAIL}",
        "static_routes[](next_hop=None) / vendor_specific.static_routes[].number・trailing_numbers",
        "ip route 0 198.51.100.0/24 1 1", _route, note=_ROUTE_NOTE,
    ),
    # BGP。top は最上位のスコープでない行、bgp neighbor は FLAT-07 のスコープの下
    Pattern(
        "bgp.sir_as", "top", r"bgp as (?P<asn>\S+)", "bgp.local_as", "bgp as 0.64512", _bgp_as,
        note="`bgp as` が無ければ BGP は読まない(bgp は None。`bgp id` と相手の行は未解釈)。"
        "AS 番号は4オクテットの形(上位.下位)で 上位*65536+下位。`0.0` は local_as=0。読めない値は未解釈",
    ),
    Pattern(
        "bgp.sir_id", "top", r"bgp id (?P<addr>\S+)", "bgp.router_id", "bgp id 192.0.2.1", _bgp_id,
        note="IPv4 の正規の書き方。読めない値は未解釈",
    ),
    Pattern(
        "bgp.sir_option", "top", r"bgp (?!(?:as|id|neighbor)(?: |$))(?P<rest>\S.*)", "bgp.options",
        "bgp ip network igp on", _bgp_top_option,
        note="手続き。最上位のほかの `bgp` の行の残り(空白は1つ)を options の集合に足す(重ねない。昇順。"
        "ADR-0121 決定3、REQ-166)。`as`・`id`・`neighbor` の語で始まる残りは当てない。`bgp as` が無ければ未解釈。"
        "語のどれかが password・secret・key か、語の中に鍵が埋まった(key, や password= などの)行は未解釈",
    ),
    Pattern(
        "bgp.sir_neighbor.address", "bgp neighbor", r"address (?P<addr>\S+)",
        "bgp.neighbors の鍵 / vendor_specific.bgp_neighbors[].number", "address 198.51.100.1", _bgp_address,
        note="鍵はアドレスの正規の書き方。`address` の無い番号・同じアドレスの番号は写さない(その番号の行はすべて未解釈)。"
        "番号は定義の位置で、相手の同一性ではない(vendor_specific に置き、突き合わせには使わない)",
    ),
    Pattern(
        "bgp.sir_neighbor.as", "bgp neighbor", r"as (?P<asn>\S+)", "bgp.neighbors[].remote_as", "as 0.64513",
        _bgp_remote_as, note="AS 番号は 上位*65536+下位。読めない値は未解釈",
    ),
    Pattern(
        "bgp.sir_neighbor.source", "bgp neighbor", r"source (?P<value>\S+)", "bgp.neighbors[].update_source",
        "source 192.0.2.1", _bgp_source, note="文字列のまま",
    ),
    Pattern(
        "bgp.sir_neighbor.authentication", "bgp neighbor", r"authentication .+", "bgp.neighbors[].password_configured",
        "authentication <値> encrypted", _bgp_authentication,
        note="手続き。`authentication` の値は持たない(IR のどこにも入れない。ADR-0038 決定6)。"
        "password_configured を True にし、行は解釈済みにする。どんな形でも options に入れない",
    ),
    Pattern(
        "bgp.sir_neighbor.options", "bgp neighbor", r"(?P<rest>.+)", "bgp.neighbors[].options", "timers 10s 30s",
        _bgp_options,
        note="手続き。固定キーに無い属性の行(番号の後ろの残り。空白は1つにそろえる)を options に足す(ADR-0038 決定4)。"
        "同じ文字列は重ねない。address / as / source / authentication で始まるのに形が合わない行は入れず未解釈にする",
    ),
    # 相手(REQ-49)。remote は FLAT-03 のスコープの下(経路の行は上の static_routes.sir_remote.route が先に当たる)、
    # remote_ap は FLAT-04 のスコープの下
    *(_secret_pattern("vendor_specific.sir_remote", "remote", *secret) for secret in _REMOTE_SECRETS),
    Pattern(
        "vendor_specific.sir_remote.name", "remote", r"name\s+(?P<value>\S.*)", "vendor_specific.remotes[].name",
        "name VPN-A", _remote_name, note="`name ` の後ろの残り(原文のまま)。2回あり値が違えば後の行を使い、食い違いを記録する",
    ),
    Pattern(
        "vendor_specific.sir_remote.attributes", "remote", r"(?P<rest>.+)", "vendor_specific.remotes[].attributes",
        "ip msschange 1300", _remote_attribute, note=_ATTRIBUTES_NOTE,
    ),
    *(_secret_pattern("vendor_specific.sir_remote_ap", "remote_ap", *secret) for secret in _AP_SECRETS),
    Pattern(
        "vendor_specific.sir_remote_ap.name", "remote_ap", r"name\s+(?P<value>\S.*)",
        "vendor_specific.remotes[].aps[].name", "name VPN-A-AP", _remote_name,
        note="`name ` の後ろの残り(原文のまま)。2回あり値が違えば後の行を使い、食い違いを記録する",
    ),
    Pattern(
        "vendor_specific.sir_remote_ap.attributes", "remote_ap", r"(?P<rest>.+)",
        "vendor_specific.remotes[].aps[].attributes", "tunnel remote 198.51.100.1", _remote_attribute,
        note=_ATTRIBUTES_NOTE,
    ),
    # 残りの行(REQ-51)。同じ scope の今の項目・BGP・経路の後ろで、秘密 → 汎用の順に当てる
    *(_secret_pattern("vendor_specific.sir_generic", "lan", *secret) for secret in _LAN_SECRETS),
    Pattern(
        "vendor_specific.sir_generic.interface_ether", "ether", r"(?P<rest>.+)", "vendor_specific.interfaces[].attributes",
        "mode auto", _ether_generic,
        note=_GENERIC_NOTE + "。`use`・`vlan untag`・`vlan tag` で始まる行は attributes に入れない(形が合わなければ未解釈)",
    ),
    Pattern(
        "vendor_specific.sir_generic.interface_lan", "lan", r"(?P<rest>.+)", "vendor_specific.interfaces[].attributes",
        "ip nat mode multi", _lan_generic,
        note=_GENERIC_NOTE + "。`ip address`・`ip route`・`vlan`・`vrrp` で始まる行は attributes に入れない(形が合わなければ未解釈)",
    ),
    Pattern(
        "vendor_specific.sir_generic.scope", "other_scope", r"(?P<rest>.+)", "vendor_specific.scopes[].attributes",
        "ip routing on", _scope_generic,
        note=_GENERIC_NOTE + "。scopes のキーは木のスコープ名(`bridgegroup 0`・`routemanage ip` など)。スコープだけの行は未解釈",
    ),
    *(_secret_pattern("vendor_specific.sir_generic", "top", *secret) for secret in _TOP_SECRETS),
    Pattern(
        "vendor_specific.sir_generic.top", "top", r"(?P<rest>.+)", "vendor_specific.settings.attributes",
        "syslog facility 23", _top_generic,
        note=_GENERIC_NOTE + "。`bgp` の行は BGP の読み方のまま(BGP の表で読めない `bgp` の行は未解釈)。"
        "行頭スコープ表の語(`ether`・`lan`・`remote` など 12 語)で始まる行は、木がスコープにできなかった壊れた行として未解釈",
    ),
)


_ETHER_TYPE = next(p for p in PATTERNS if p.id == "interfaces.sir_ether.type")
_LAN_TYPE = next(p for p in PATTERNS if p.id == "interfaces.sir_lan.type")


def _finish_ether(name: str, ctx: _Ctx) -> Interface:
    ctx.interface = interface = new_interface(name)
    interface["shutdown"] = bool(ctx.get("shutdown"))
    _ETHER_TYPE.apply(None, ctx)
    return interface


def _finish_lan(name: str, ctx: _Ctx) -> tuple[Interface, dict[str, Any]]:
    ctx.interface = interface = new_interface(name)
    _LAN_TYPE.apply(None, ctx)
    address = ctx.get("ip_address")
    interface["ip_address"] = address[0] if address else None
    vrrp: list[Vrrp] = []
    groups: dict[str, dict[str, Any]] = {}
    for g in ctx.groups():
        ident = ctx.get("vrrp", "group", g, "id")
        preempt = ctx.get("vrrp", "group", g, "preempt")
        # キーは常に同じ。id の行があるグループの preempt は vrrp[] 側に置き、ここでは None(差分が2件に割れないように)
        groups[g] = {
            "vrid": ident[0] if ident is not None else None,
            "preempt": preempt if ident is None else None,
            "ad": ctx.get("vrrp", "group", g, "ad"),
            "vaddr_icmp": ctx.get("vrrp", "group", g, "vaddr_icmp"),
            "vaddr_etherip": ctx.get("vrrp", "group", g, "vaddr_etherip"),
        }
        if ident is not None:
            vrid, priority, addresses = ident
            vrrp.append({
                "group": vrid, "priority": priority, "virtual_addresses": list(addresses), "preempt": preempt,
                "options": None,
            })
    interface["vrrp"] = vrrp
    vendor = {
        "ip_broadcast_type": address[1] if address else None,
        "vlan": ctx.get("vlan"),
        "vrrp": {
            "use": ctx.get("vrrp_use"),
            "auth_configured": ctx.get("vrrp_auth_configured"),
            "trap": ctx.get("vrrp_trap"),
            "groups": groups,
        },
        **ctx.generic(),
    }
    return interface, vendor


def _collect(ctx: _BgpCtx, scope: str, line: FlatNode) -> None:
    ctx.node = line
    if (pattern := apply_first(PATTERNS, scope, line.text, ctx)) is not None:
        ctx.lines.append((line, pattern.id))


def _finish_bgp(
    top: _BgpCtx, neighbors: dict[str, _BgpCtx], interpreted: InterpretedLines
) -> tuple[Bgp | None, dict[str, dict[str, str]]]:
    """集めた行から bgp と vendor_specific の bgp_neighbors を組み、写した行だけを解釈済みにする。"""
    numbers: dict[str, dict[str, str]] = {}
    if "local_as" not in top.values:
        return None, numbers
    bgp = new_bgp(top.values["local_as"])
    bgp["router_id"] = top.values.get("router_id")
    bgp["options"] = top.options
    written = [top]
    # 同じアドレスを持つ番号は、どちらが正しいか決められないので写さない
    keys = Counter(ctx.values["key"] for ctx in neighbors.values() if "key" in ctx.values)
    for number, ctx in neighbors.items():
        key = ctx.values.get("key")
        if key is None or keys[key] > 1:
            continue
        neighbor = new_bgp_neighbor("address")
        neighbor["remote_as"] = ctx.values.get("remote_as")
        neighbor["update_source"] = ctx.values.get("update_source")
        neighbor["password_configured"] = ctx.values.get("password_configured", False)
        neighbor["options"] = ctx.options
        bgp["neighbors"][key] = neighbor
        numbers[key] = {"number": number}
        written.append(ctx)
    for ctx in written:
        for line, pattern_id in ctx.lines:
            interpreted.mark(line, pattern_id)
    return bgp, numbers


def _read(scope_node: FlatNode, scope: str, ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """スコープの直下の属性の行を表で読み、写せた行を解釈済みにする。

    スコープだけの行の2回目以降(属性の行ではない)と子のスコープ(remote N ap M)は読まない。
    """
    for line in scope_node.children:
        if line.repeated_scope or line.scope_id is not None:
            continue
        ctx.node = line
        if (pattern := apply_first(PATTERNS, scope, line.text, ctx)) is not None:
            interpreted.mark(line, pattern.id)


def _conflicts(tree: FlatTree, scope: tuple[str, ...], ctx: _Ctx) -> list[DuplicateLine]:
    """値の食い違い(lan の仕組みと同じ)と秘密の行の重複。どちらも行番号だけ。

    何本も書ける秘密の行は、木の判定(値に見える語の前までを比べる。`answer ppp auth receive add` の ID が
    アドレスの形なら ID の違いを見落とす)から外し、鍵の番号・利用者の ID で決めた記録だけを残す。
    """
    tree.exclude_duplicates(scope, ctx.keyed_secret_lines())
    found = [DuplicateLine(scope, tuple(sorted(lines))) for lines in ctx.conflicts.values()]
    return found + ctx.secret_duplicates(scope)


def extract(
    tree: FlatTree, interpreted: InterpretedLines
) -> tuple[
    dict[str, Interface], list[StaticRoute], Bgp | None, dict[str, Ospf], Ipsec | None, dict[str, SdwanBlock],
    dict[str, RouteMap], dict[str, PrefixList], dict[str, PrefixList], Global, dict[str, Any], list[DuplicateLine],
]:
    """(interfaces, static_routes, bgp, ospf, ipsec, sdwan, route_maps, prefix_lists, ipv6_prefix_lists, global,
    vendor_specific["fujitsu_sir"], 値の食い違い) を返す。

    ospf は読まない(いつも空の辞書。FortiGate と同じ形で返すため。REQ-192)。ipsec も読まない(いつも None。REQ-193)。
    sdwan も読まない(いつも空の辞書。REQ-194)。route_maps・prefix_lists・ipv6_prefix_lists も読まない(いつも空の辞書。REQ-195)。
    global も読まない(いつも new_global()。REQ-196)。

    remote のスコープでは Interface は作らず、経路を static_routes に、ほかの行と接続先(ap)を remotes に写す。
    相手・接続先は木のスコープ(暗黙のものを含む)ごとに1つ作り、キーはスコープ名の番号の原文。remote が無くても remotes は空の辞書で置く。
    経路はスコープの順、その中の行の順に並べる。
    BGP の行は木の全体から集めてから写す(`bgp as` が後ろにあっても同じ結果になる)。BGP が無くても bgp_neighbors は空の辞書で置く。
    ほかのスコープは木のスコープごとに scopes に1つ作る(無ければ空の辞書)。最上位の `bgp` 以外の行は settings に写す(行が無くても置く)。
    """
    interfaces: dict[str, Interface] = {}
    routes: list[StaticRoute] = []
    route_details: list[dict[str, Any]] = []
    vendor: dict[str, Any] = {}
    remotes: dict[str, dict[str, Any]] = {}
    conflicts: list[DuplicateLine] = []
    scopes: dict[str, dict[str, list[str]]] = {}
    settings = _Ctx()
    bgp_top = _BgpCtx()
    bgp_neighbors: dict[str, _BgpCtx] = {}
    for block in tree.children:
        if block.scope_id is None:
            if block.text.split()[0] == "bgp":
                _collect(bgp_top, "top", block)
                continue
            settings.node = block
            if (pattern := apply_first(PATTERNS, "top", block.text, settings)) is not None:
                interpreted.mark(block, pattern.id)
            continue
        scope = SCOPES.get(block.scope_id)
        if scope is None:
            continue
        if scope == "bgp neighbor":
            # 番号の原文はスコープ名の最後の語
            neighbor = bgp_neighbors[block.text.split()[-1]] = _BgpCtx(block.path())
            for line in block.children:
                if not line.repeated_scope and line.scope_id is None:
                    _collect(neighbor, scope, line)
            continue
        ctx = _Ctx(block.text)
        _read(block, scope, ctx, interpreted)
        if scope == "ether":
            interfaces[block.text] = _finish_ether(block.text, ctx)
            vendor[block.text] = ctx.generic()
        elif scope == "other_scope":
            scopes[block.text] = ctx.generic()
        elif scope == "lan":
            interfaces[block.text], vendor[block.text] = _finish_lan(block.text, ctx)
        elif scope == "remote":
            remote = remotes[block.text.split()[-1]] = {**ctx.remote(), "aps": {}}
            for ap in block.children:
                if ap.scope_id != _REMOTE_AP_SCOPE_ID or ap.repeated_scope:
                    continue
                ap_ctx = _Ctx(ap.text)
                _read(ap, "remote_ap", ap_ctx, interpreted)
                remote["aps"][ap.text.split()[-1]] = ap_ctx.remote()
                conflicts.extend(_conflicts(tree, ap.path(), ap_ctx))
        routes.extend(ctx.routes)
        route_details.extend(ctx.route_details)
        conflicts.extend(_conflicts(tree, block.path(), ctx))
    bgp, numbers = _finish_bgp(bgp_top, bgp_neighbors, interpreted)
    for bgp_ctx in (bgp_top, *bgp_neighbors.values()):
        conflicts.extend(bgp_ctx.duplicates())
    conflicts.extend(_conflicts(tree, (), settings))
    vendor_specific = {
        "interfaces": vendor, "static_routes": route_details, "bgp_neighbors": numbers, "remotes": remotes,
        "scopes": scopes, "settings": settings.generic(),
    }
    return interfaces, routes, bgp, {}, None, {}, {}, {}, {}, new_global(), vendor_specific, conflicts
