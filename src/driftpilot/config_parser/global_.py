"""機器全体の設定を IR の global に写す extractor。

機密に当たる値(username の secret、SNMP コミュニティ名など)は、読み取った値をそのまま保持する。
マスキングは LLM へ送る時にだけ行う(ADR-0004)ため、ここでは行わない。
書式に fullmatch した行だけを解釈済みにし、合わない行は unparsed に残す。
ただし認証サーバーの共有の鍵(ADR-0070)と SNMPv3 の利用者の鍵(ADR-0102)は、値を IR のどこにも入れず、
設定されているかの真偽値だけを持つ。
バナー(`banner <種類> …`)は区切りの間の本文を種類ごとに持ち、本文に秘密の語があれば入れない(ADR-0116 決定1)。
"""

import re
from bisect import insort
from collections.abc import Callable
from dataclasses import dataclass
from ipaddress import IPv4Address, ip_address
from typing import Literal

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.certificates import extract_certificate_chains
from driftpilot.config_parser.device_settings import extract_device_settings, has_secret
from driftpilot.config_parser.ir import AaaServer, AaaServerGroup, Global, new_global
from driftpilot.config_parser.management import extract_management
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines
from driftpilot.config_parser.trustpoints import extract_trustpoints

# 語のどれかがこれらの vtp の行は入れない(未解釈に残す。REQ-63 §3-2)。大文字小文字は区別しない
_SECRET_WORDS = frozenset({"password", "secret", "key", "shared-secret"})
# これらの語の後ろに値が続く service の行は入れない(未解釈に残す。REQ-74 §3)。大文字小文字は区別しない
_SERVICE_SECRET_WORDS = frozenset({"password", "secret", "key"})
# 認証サーバーのブロック(tacacs server・radius server で共通)とグループのブロックの中の行の scope(REQ-81)
_AAA_SERVER_SCOPE = "aaa server"
_AAA_GROUP_SCOPE = "aaa group server"
_SECRET_NOTE = "語のどれかが password・secret・key か、語の中に鍵が埋まった(key, や password= などの)"
# SNMPv3 の利用者の行で、auth・priv の直後に残す方式の名前(ADR-0102 決定2 の一覧と、Arista・NX-OS の書き方)と、
# aes の後ろの鍵の長さ(REQ-83 §3-2)。大文字小文字は区別しない。「sha・aes で始まる語」にしないのは、
# NX-OS の方式を省いた `priv <鍵>` で、sha・aes で始まる鍵の値を方式の名前と取り違えないため。
# 方式か鍵かは、後ろの語の値ではなく書式の位置で決める(_snmp_user_settings)
_SNMP_METHODS = frozenset(
    {
        "md5", "des", "3des",
        "sha", "sha224", "sha256", "sha384", "sha512", "sha-224", "sha-256", "sha-384", "sha-512",
        "aes", "aes128", "aes192", "aes256", "aes-128", "aes-192", "aes-256",
    }
)
_AES_BITS = frozenset({"128", "192", "256"})
# auth・priv より前にこれらの版の語がある行は Cisco・Arista の書き方で、auth・priv の直後には方式の名前が必ずある。
# 無い行は NX-OS の書き方で、priv の方式は省ける。書ける方式は des・aes-128・aes-256(Cisco の NX-OS の
# データモデルの列挙)。機器は語の一致を鍵より先に取るので、方式を省いた鍵の値がこれらの語になることはない
_SNMP_VERSIONS = frozenset({"v1", "v2c", "v3"})
_NXOS_PRIV_METHODS = frozenset({"des", "aes-128", "aes-256"})


def _hostname(m: re.Match[str], result: Global) -> bool:
    if result["hostname"] is not None:
        return False
    result["hostname"] = m["name"]
    return True


def _domain_name(m: re.Match[str], result: Global) -> bool:
    if result["domain_name"] is not None:
        return False
    result["domain_name"] = m["name"]
    return True


def _timezone(m: re.Match[str], result: Global) -> bool:
    if result["timezone"] is not None:
        return False
    result["timezone"] = m["value"]
    return True


def _append_once(values: list[str], text: str) -> None:
    """出現順の一覧に足す。同じ文字列は重ねない。"""
    if text not in values:
        values.append(text)


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _server_text(m: re.Match[str]) -> str:
    """「アドレス[ vrf <V>][ <残り>]」の文字列。vrf の組が無い書式もある。"""
    vrf = m.groupdict().get("vrf")
    vrf = f"vrf {vrf}" if vrf is not None else None
    return " ".join(part for part in (m["addr"], vrf, m["rest"]) if part is not None)


def _ntp_server(m: re.Match[str], result: Global) -> bool:
    _append_once(result["ntp_servers"], _server_text(m))
    return True


def _ntp_option(m: re.Match[str], result: Global) -> bool:
    if m["no"] is None:
        _add(result["ntp_options"], m["rest"])
        return True
    if has_secret(m.string):
        return False
    _add(result["ntp_options"], "no " + m["rest"])
    return True


def _logging_host(m: re.Match[str], result: Global) -> bool:
    _append_once(result["logging_hosts"], _server_text(m))
    return True


def _logging_option(m: re.Match[str], result: Global) -> bool:
    _add(result["logging_options"], m["rest"])
    return True


def _no_logging_option(m: re.Match[str], result: Global) -> bool:
    if has_secret(m.string):
        return False
    _add(result["logging_options"], "no " + m["rest"])
    return True


def _dns_server(m: re.Match[str], result: Global) -> bool:
    words = m["rest"].split(" ")
    addresses: list[str] = []
    for word in words:
        try:
            addresses.append(str(ip_address(word)))
        except ValueError:
            break
    if not addresses:
        return False
    vrf = f"vrf {m['vrf']}" if m["vrf"] is not None else None
    rest = " ".join(words[len(addresses):]) or None
    for address in addresses:
        _add(result["dns"]["servers"], " ".join(part for part in (address, vrf, rest) if part is not None))
    return True


def _dns_option(m: re.Match[str], result: Global) -> bool:
    _add(result["dns"]["options"], m.string)
    return True


def _routing(m: re.Match[str], result: Global) -> bool:
    _add(result["routing"], m.string)
    return True


def _has_secret(text: str) -> bool:
    return any(word.lower() in _SECRET_WORDS for word in text.split())


def _vtp_mode(m: re.Match[str], result: Global) -> bool:
    if _has_secret(m.string):
        return False
    result["vtp"]["mode"] = m["mode"]
    return True


def _vtp_option(m: re.Match[str], result: Global) -> bool:
    if _has_secret(m.string):
        return False
    _add(result["vtp"]["options"], m["rest"])
    return True


def _system_mtu(m: re.Match[str], result: Global) -> bool:
    _add(result["system_mtu"], m["rest"])
    return True


def _service(m: re.Match[str], result: Global) -> bool:
    # 最後の語より前に秘密の語があれば、その後ろに値が続く
    if any(word.lower() in _SERVICE_SECRET_WORDS for word in m.string.split()[:-1]):
        return False
    _add(result["services"], m.string)
    return True


def _vlan_internal(m: re.Match[str], result: Global) -> bool:
    if result["vlan_internal"] is not None:
        return False
    result["vlan_internal"] = m["rest"]
    return True


def _virtual_router_mac(m: re.Match[str], result: Global) -> bool:
    if result["virtual_router_mac"] is not None:
        return False
    result["virtual_router_mac"] = m["mac"]
    return True


def _banner(m: re.Match[str], result: Global) -> bool:
    """区切りの間の本文を banners[種類] に写す(ADR-0116 決定1)。

    区切りは、種類の後ろが `^C` で始まれば `^C`、そうでなければ最初の1文字。本文は最初の区切りの後ろから
    最後の区切りの前まで(前後の改行を外す)。閉じていない・同じ種類の2回目・本文に秘密の語がある行は読まない。
    """
    rest = m["rest"]
    delimiter = "^C" if rest.startswith("^C") else rest[0]
    end = rest.rfind(delimiter)
    if end < len(delimiter):
        return False
    body = rest[len(delimiter):end].strip("\r\n")
    if m["kind"] in result["banners"] or has_secret(body):
        return False
    result["banners"][m["kind"]] = body
    return True


def _default_gateway(m: re.Match[str], result: Global) -> bool:
    if result["default_gateway"] is not None:
        return False
    try:
        result["default_gateway"] = str(IPv4Address(m["addr"]))
    except ValueError:
        return False
    return True


def _snmp_community(m: re.Match[str], result: Global) -> bool:
    result["snmp"]["communities"].append(
        {"name": m["name"], "access": m["access"].upper(), "acl": m["acl"], "view": m["view"]}
    )
    return True


def _snmp_community_group(m: re.Match[str], result: Global) -> bool:
    result["snmp"]["communities"].append(
        {"name": m["name"], "access": f"group {m['role']}", "acl": None, "view": None}
    )
    return True


def _snmp_method(words: list[str], i: int, optional: bool) -> bool:
    """auth・priv の直後の words[i] が方式の名前か(REQ-83 §3-2)。

    方式を省けない位置では、一覧の語なら方式とする(後ろの語の値は見ない)。
    方式を省ける位置(NX-OS の priv の後ろ)では、des・aes-128・aes-256 の語のときだけ方式とする。
    """
    if i >= len(words):
        return False
    word = words[i].lower()
    return word in (_NXOS_PRIV_METHODS if optional else _SNMP_METHODS)


def _snmp_key_end(words: list[str], i: int) -> int:
    """words[i] から始まる鍵の部分の次の位置。

    `key <型の番号1桁> <値>`(Arista)は3語、ほか(`<値>` だけ)は1語。鍵の値はどんな語でも読み飛ばす
    (auth・priv・key という語でも、書式の語として読み直さない)。
    """
    if (
        i + 2 < len(words)
        and words[i].lower() == "key"
        and len(words[i + 1]) == 1
        and words[i + 1].isdigit()
    ):
        return i + 3
    return i + 1


def _snmp_segment(words: list[str], i: int, kept: list[str], optional: bool) -> int:
    """words[i] の auth・priv の語と直後の方式の名前を kept に足し、鍵の部分を読み飛ばした次の位置を返す。"""
    kept.append(words[i])
    i += 1
    if _snmp_method(words, i, optional):
        kept.append(words[i])
        i += 1
        # Cisco の aes 128 のような2語の方式。長さの後ろに語(鍵の値)があるときだけ長さとする。
        # 後ろに語の無い `priv aes 128` は、Arista の aes の鍵の値が 128 の行
        if kept[-1].lower() == "aes" and i + 1 < len(words) and words[i] in _AES_BITS:
            kept.append(words[i])
            i += 1
    return _snmp_key_end(words, i)


def _snmp_user_settings(words: list[str]) -> tuple[str, bool, bool]:
    """最初の auth・priv より後ろは、auth・priv の語と直後の方式の名前だけを残した文字列と、auth・priv の有無。

    語を位置で読む: [前の語…] auth <方式> <鍵の部分> … priv [方式] <鍵の部分> …。
    方式か鍵かは位置で決め、鍵の値(localizedkey・engineID・auth・priv などの語でも)は見ない。
    priv の方式を省けるのは、版の語(v1・v2c・v3)が前に無い NX-OS の書き方だけ。
    鍵の部分の後ろの語(localizedkey・engineID・access など)は捨てる。auth の後ろの priv は、
    鍵の部分の後ろで最初に現れる priv の語(並びの決まった書き方では鍵の部分のすぐ後ろ)。
    """
    first = next((i for i, word in enumerate(words) if word.lower() in ("auth", "priv")), len(words))
    kept = words[:first]  # 最初の auth・priv より前の語はそのまま
    nxos = not any(word.lower() in _SNMP_VERSIONS for word in kept)
    auth = priv = False
    i = first
    if i < len(words) and words[i].lower() == "auth":
        auth = True
        i = _snmp_segment(words, i, kept, optional=False)
        while i < len(words) and words[i].lower() != "priv":
            i += 1
    if i < len(words) and words[i].lower() == "priv":
        priv = True
        _snmp_segment(words, i, kept, optional=nxos)
    return " ".join(kept), auth, priv


def _snmp_user(m: re.Match[str], result: Global) -> bool:
    words = m["rest"].split(" ")
    first = next((i for i, word in enumerate(words) if word.lower() in ("auth", "priv")), len(words))
    # 最初の auth・priv より前に秘密の語があれば、値が残りうるので読まない
    if has_secret(m["name"], *words[:first]):
        return False
    settings, auth, priv = _snmp_user_settings(words)
    users = result["snmp"]["users"]
    user = next((user for user in users if user["name"] == m["name"]), None)
    if user is None:
        user = {"name": m["name"], "settings": [], "auth_configured": False, "priv_configured": False}
        users.append(user)
    _add(user["settings"], settings)
    user["auth_configured"] = user["auth_configured"] or auth
    user["priv_configured"] = user["priv_configured"] or priv
    return True


def _snmp_set(key: Literal["groups", "views", "options"], group: str | None) -> Callable[[re.Match[str], Global], bool]:
    """snmp の集合に、行の残り(group が None なら行の原文)を足す関数。秘密の語がある行は読まない(未解釈)。"""

    def apply(m: re.Match[str], result: Global) -> bool:
        if has_secret(m.string):
            return False
        _add(result["snmp"][key], m.string if group is None else m[group])
        return True

    return apply


def _snmp_location(m: re.Match[str], result: Global) -> bool:
    if result["snmp"]["location"] is not None:
        return False
    result["snmp"]["location"] = m["text"]
    return True


def _snmp_contact(m: re.Match[str], result: Global) -> bool:
    if result["snmp"]["contact"] is not None:
        return False
    result["snmp"]["contact"] = m["text"]
    return True


def _snmp_host(m: re.Match[str], result: Global) -> bool:
    options = " ".join(part for part in (m["mode"].strip(), m["rest"]) if part)
    result["snmp"]["hosts"].append(
        {"host": m["host"], "community": m["community"], "options": options or None}
    )
    return True


def _aaa_new_model(m: re.Match[str], result: Global) -> bool:
    result["aaa"]["new_model"] = True
    return True


def _aaa_no_new_model(m: re.Match[str], result: Global) -> bool:
    result["aaa"]["new_model"] = False
    return True


def _aaa_line(m: re.Match[str], result: Global) -> bool:
    result["aaa"]["lines"].append(m.string)
    return True


@dataclass
class _AaaBlock:
    """認証サーバー・グループのブロックの中の行を読む間の状態。item は見出しで作った項目。"""

    item: AaaServer | AaaServerGroup
    has_children: bool = False


def _new_aaa_server(result: Global, kind: str, name: str) -> AaaServer:
    """servers["<種類> <名前またはアドレス>"]。同じ鍵があれば同じ項目にまとめる。"""
    return result["aaa_servers"]["servers"].setdefault(f"{kind} {name}", {"settings": [], "key_configured": False})


def _aaa_server(m: re.Match[str], result: Global) -> bool:
    _new_aaa_server(result, m["kind"], m["name"])
    return True


def _aaa_server_key(m: re.Match[str], ctx: _AaaBlock) -> bool:
    if ctx.has_children:
        return False
    ctx.item["key_configured"] = True
    return True


def _aaa_setting(m: re.Match[str], ctx: _AaaBlock) -> bool:
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.item["settings"], m.string)
    return True


def _aaa_server_host(m: re.Match[str], result: Global) -> bool:
    words = m["rest"].split(" ") if m["rest"] is not None else []
    # key の語から行の終わりまでを外す(鍵の値を IR に入れない)
    at = next((i for i, word in enumerate(words) if word.lower() == "key"), len(words))
    rest = " ".join(words[:at])
    if has_secret(m["name"], rest):
        return False
    server = _new_aaa_server(result, m["kind"], m["name"])
    if rest:
        _add(server["settings"], rest)
    if at < len(words):
        server["key_configured"] = True
    return True


def _aaa_group(m: re.Match[str], result: Global) -> bool:
    result["aaa_servers"]["groups"].setdefault(f"{m['kind']} {m['name']}", {"settings": []})
    return True


def _aaa_option(m: re.Match[str], result: Global) -> bool:
    if has_secret(m.string):
        return False
    _add(result["aaa_servers"]["options"], m.string)
    return True


def _username(m: re.Match[str], result: Global) -> bool:
    if m["name"] in result["users"]:
        return False
    if m["role1"] is not None and m["role2"] is not None:
        return False
    privilege = m["privilege"]
    if m["kind"] is None:  # nopassword
        credential, secret_type, secret = "none", None, None
    elif m["b"] is None:  # 型の無い `secret <値>`
        credential, secret_type, secret = m["kind"], None, m["a"]
    else:
        credential, secret_type, secret = m["kind"], m["a"], m["b"]
    result["users"][m["name"]] = {
        "name": m["name"],
        "privilege": int(privilege) if privilege is not None else None,
        "role": m["role1"] or m["role2"],
        "credential": credential,
        "secret_type": secret_type,
        "secret": secret,
    }
    return True


def _enable(key: Literal["enable_secret", "enable_password"]) -> Callable[[re.Match[str], Global], bool]:
    """enable secret / enable password の行を写す関数。二度目の行は読まない(未解釈)。"""

    def apply(m: re.Match[str], result: Global) -> bool:
        if result[key] is not None:
            return False
        result[key] = {"type": m["type"], "value": m["value"]}
        return True

    return apply


def _no_enable(m: re.Match[str], result: Global) -> bool:
    return True


_ONCE = "二度目に現れた行は上書きせず読まない(未解釈)"

# 並び＝優先順位(現在の if / elif の順)。aaa.new_model は aaa.line より前に、
# aaa.no_new_model は aaa.no_line より前に置く。
PATTERNS: tuple[Pattern, ...] = (
    Pattern("global.hostname", "top", r"hostname (?P<name>\S+)", "hostname", "hostname R1", _hostname, note=_ONCE),
    Pattern(
        "global.domain_name", "top", r"ip domain name (?P<name>\S+)", "domain_name",
        "ip domain name example.com", _domain_name, note=_ONCE,
    ),
    Pattern(
        "global.domain_name_legacy", "top", r"ip domain-name (?P<name>\S+)", "domain_name",
        "ip domain-name example.net", _domain_name,
        note=(
            "ip domain name(新しい書き方)と同じ項目。"
            "どちらの書き方でも二度目に現れた行は上書きせず読まない(未解釈)"
        ),
    ),
    Pattern(
        "global.dns_domain", "top", r"dns domain (?P<name>\S+)", "domain_name", "dns domain example.org",
        _domain_name,
        note="ip domain name と同じ項目。どの書き方でも二度目に現れた行は上書きせず読まない(未解釈)",
    ),
    Pattern(
        "global.timezone", "top", r"clock timezone (?P<value>\S.*)", "timezone", "clock timezone JST 9 0",
        _timezone, note=_ONCE,
    ),
    # サーバーや送り先の書式は、どれも「ほかの指定」(ntp_option・logging_option)より前に置く。
    # 形の合わないサーバーや送り先の行は「ほかの指定」に入れず未解釈に残す
    Pattern(
        "global.ntp_server", "top",
        r"ntp server (?:vrf (?P<vrf>\S+) )?(?!vrf(?: |$))(?P<addr>\S+)(?: (?P<rest>\S.*))?",
        "ntp_servers", "ntp server vrf MGMT 192.0.2.10 prefer", _ntp_server,
        note="手続き。「アドレス[ vrf <V>][ <残り>]」を出現順に足す(重ねない)。後ろに何も無い行はアドレスだけ",
    ),
    Pattern(
        "global.ntp_option", "top", r"(?P<no>no )?ntp (?!server(?: |$)|authentication-key(?: |$))(?P<rest>\S.*)",
        "ntp_options", "ntp source Loopback0", _ntp_option,
        note=(
            "手続き。行の ntp の後ろを集合に足す(重ねない。昇順)。ntp authentication-key は読まない(鍵の値を入れない)。"
            "no 付きの行は、no ntp の後ろに no を付けて(no <残り>)足す(no logging と同じ持ち方。ADR-0121、REQ-166)。"
            "no ntp server・no ntp authentication-key は読まない。no 付きで、語のどれかが password・secret・key か、"
            "語の中に鍵が埋まった(key, や password= などの)行は未解釈"
        ),
    ),
    Pattern(
        "global.logging_host", "top", r"logging host (?P<addr>\S+)(?: (?P<rest>\S.*))?", "logging_hosts",
        "logging host 192.0.2.20 transport udp port 514", _logging_host,
        note="手続き。「アドレス[ <残り>]」を出現順に足す(重ねない)",
    ),
    Pattern(
        "global.logging_vrf_host", "top", r"logging vrf (?P<vrf>\S+) host (?P<addr>\S+)(?: (?P<rest>\S.*))?",
        "logging_hosts", "logging vrf MGMT host 192.0.2.21", _logging_host,
        note="手続き。Arista の書き方。「アドレス vrf <V>[ <残り>]」を出現順に足す(重ねない)",
    ),
    Pattern(
        "global.logging_server", "top", r"logging server (?P<addr>\S+)(?: (?P<rest>\S.*))?", "logging_hosts",
        "logging server 192.0.2.22 5 use-vrf management", _logging_host,
        note="手続き。NX-OS の書き方。「アドレス[ <残り>]」を出現順に足す(重ねない)",
    ),
    Pattern(
        "global.logging_option", "top",
        r"logging (?!host(?: |$)|server(?: |$)|vrf \S+ host(?: |$))(?P<rest>\S.*)",
        "logging_options", "logging buffered 64000 debugging", _logging_option,
        note="手続き。行の logging の後ろを集合に足す(重ねない。昇順)",
    ),
    Pattern(
        "global.no_logging_option", "top",
        r"no logging (?!host(?: |$)|server(?: |$)|vrf \S+ host(?: |$))(?P<rest>\S.*)",
        "logging_options", "no logging event link-status default", _no_logging_option,
        note=(
            "手続き。行の no logging の後ろに no を付けて(no <残り>)、logging_options の集合に足す(重ねない。昇順)。"
            "no logging host・server・vrf <V> host は読まない。語のどれかが password・secret・key か、"
            "語の中に鍵が埋まった(key, や password= などの)行は未解釈(ADR-0106 の補足)"
        ),
    ),
    Pattern(
        "global.dns_server", "top", r"ip name-server (?:vrf (?P<vrf>\S+) )?(?P<rest>\S.*)", "dns.servers",
        "ip name-server vrf MGMT 192.0.2.53 198.51.100.53", _dns_server,
        note=(
            "手続き。先頭から続くアドレスごとに「正規のアドレス[ vrf <V>][ <残り>]」を集合に足す(重ねない。昇順)。"
            "アドレスが1つも無い行は読まない(未解釈)"
        ),
    ),
    Pattern(
        "global.dns_option", "top",
        r"(?:no )?ip domain lookup(?: \S.*)?|(?:no )?ip domain-lookup(?: \S.*)?|ip domain-list \S+"
        r"|(?:no )?ip domain (?:multicast|periodic-resolve|recursive|round-robin)(?: \S.*)?|(?:no )?ip hostname strict",
        "dns.options", "no ip domain lookup", _dns_option,
        note=(
            "手続き。行の原文を集合に足す(重ねない。昇順)。ip domain-lookup <残り>・ip domain multicast・"
            "periodic-resolve・recursive・round-robin[ <残り>]・ip hostname strict は ADR-0121、REQ-166"
        ),
    ),
    Pattern(
        "global.routing", "top", r"(?:no )?(?:ip routing|ipv6 unicast-routing)(?: \S.*)?", "routing",
        "ip routing", _routing, note="手続き。行の原文を集合に足す(重ねない。昇順)",
    ),
    Pattern(
        "global.default_gateway", "top", r"ip default-gateway (?P<addr>\S+)", "default_gateway",
        "ip default-gateway 192.0.2.1", _default_gateway,
        note=_ONCE + "。IPv4 のアドレスでない行も読まない",
    ),
    Pattern(
        "global.snmp.community", "top",
        r"snmp-server community (?P<name>\S+) (?:view (?P<view>\S+) )?(?P<access>RO|RW|ro|rw)(?: (?P<acl>\S+))?",
        "snmp.communities", "snmp-server community public RO", _snmp_community,
        note="手続き。access は小文字の ro・rw も大文字にそろえる。view の語が無ければ view は None(ADR-0102 決定1)",
    ),
    Pattern(
        "global.snmp.community_group", "top", r"snmp-server community (?P<name>\S+) group (?P<role>\S+)",
        "snmp.communities", "snmp-server community EXAMPLE-NX group network-operator", _snmp_community_group,
        note="手続き。NX-OS の書き方。access を「group <役割>」にし、acl・view は None。子を持たない行だけ読む",
    ),
    Pattern(
        "global.snmp.location", "top", r"snmp-server location (?P<text>\S.*)", "snmp.location",
        "snmp-server location Tokyo", _snmp_location, note=_ONCE,
    ),
    Pattern(
        "global.snmp.contact", "top", r"snmp-server contact (?P<text>\S.*)", "snmp.contact",
        "snmp-server contact admin@example.com", _snmp_contact, note=_ONCE,
    ),
    Pattern(
        "global.snmp.host", "top",
        r"snmp-server host (?P<host>\S+)"
        # auth / noauth / priv は version 3 のときだけセキュリティ指定。1 / 2c ではコミュニティ名になりうる
        r"(?P<mode>(?: traps| informs)?(?: version (?:(?:1|2c)|3(?: auth| noauth| priv)?))?)"
        r" (?P<community>\S+)(?: (?P<rest>\S.*))?",
        "snmp.hosts", "snmp-server host 192.0.2.30 version 2c public", _snmp_host,
        note="手続き。traps / informs と version は options にまとめ、その後ろの語は options の続きにする",
    ),
    # v3 の利用者・グループ・ビューと、ほかの snmp の行(REQ-83)。今の community・location・contact・host より後ろに置く
    Pattern(
        "global.snmp.user", "top", r"snmp-server user (?P<name>\S+) (?P<rest>\S.*)", "snmp.users",
        "snmp-server user ops usergroup v3 auth sha key 7 EXAMPLE priv aes192 key 7 EXAMPLE", _snmp_user,
        note=(
            "手続き。同じ名前の要素が無ければ足し(出現順)、残りを1つの文字列にして settings の集合に足す(重ねない。昇順)。"
            "最初の auth・priv より前の語はそのまま。後ろは auth・priv の語と、直後の方式の名前"
            "(md5・sha・sha224〜sha512・sha-224〜sha-512・des・3des・aes・aes128〜aes256・aes-128〜aes-256 と、"
            "aes の後ろの 128・192・256。長さは後ろに語があるときだけ)を残し、鍵の値などほかの語は捨てる。"
            "方式か鍵かは後ろの語の値でなく位置で決める。auth の直後と、版の語(v1・v2c・v3)のある行の priv の直後は"
            "方式の位置。版の語の無い NX-OS の行の priv の直後は、des・aes-128・aes-256 のときだけ方式で、ほかは方式を省いた鍵。"
            "鍵の部分(`key <1桁の型> <値>` か `<値>`)は、値が auth・priv・localizedkey などの語でも読み飛ばす。"
            "auth の後ろは、鍵の部分の後ろで最初の priv だけを読む"
            "(ADR-0102 決定2)。auth・priv の語があれば auth_configured・priv_configured を True にする。"
            f"最初の auth・priv より前に、{_SECRET_NOTE}語がある行は未解釈。子を持たない行だけ読む"
        ),
    ),
    Pattern(
        "global.snmp.group", "top", r"snmp-server group (?P<rest>\S.*)", "snmp.groups",
        "snmp-server group G1 v3 priv read V1", _snmp_set("groups", "rest"),
        note=f"手続き。行の snmp-server group の後ろを集合に足す(重ねない。昇順)。{_SECRET_NOTE}行は未解釈。子を持たない行だけ読む",
    ),
    Pattern(
        "global.snmp.view", "top", r"snmp-server view (?P<rest>\S.*)", "snmp.views",
        "snmp-server view V1 iso included", _snmp_set("views", "rest"),
        note=f"手続き。行の snmp-server view の後ろを集合に足す(重ねない。昇順)。{_SECRET_NOTE}行は未解釈。子を持たない行だけ読む",
    ),
    Pattern(
        "global.snmp.option", "top",
        r"(?:no )?snmp-server (?!(?:community|user|host)(?: |$))\S.*|(?:no )?snmp \S.*", "snmp.options",
        "snmp-server enable traps snmp", _snmp_set("options", None),
        note=(
            "手続き。ほかの snmp-server・snmp の行の原文を集合に足す(重ねない。昇順)。"
            "形の合わない community・user・host の行は入れない(未解釈。名前を原文のまま持たない)。"
            f"{_SECRET_NOTE}行は未解釈。子を持たない行だけ読む。"
            "no 付きの行も原文のまま足す(no snmp-server community・user・host は読まない。ADR-0121、REQ-166)"
        ),
    ),
    Pattern(
        "global.aaa.new_model", "top", r"aaa new-model", "aaa.new_model", "aaa new-model", _aaa_new_model,
    ),
    Pattern(
        "global.aaa.no_new_model", "top", r"no aaa new-model", "aaa.new_model", "no aaa new-model",
        _aaa_no_new_model, note="IOS の既定(AAA を使わない)を明示した行。行が無いときと同じ False",
    ),
    # 認証サーバーとグループ(REQ-81)は aaa.no_line・aaa.line より前に置く(子を持つ aaa group server を aaa.lines に取られない)
    Pattern(
        "global.aaa_server", "top", r"(?P<kind>tacacs|radius) server (?P<name>\S+)", "aaa_servers.servers",
        "tacacs server TAC-1", _aaa_server,
        note=(
            "手続き。子を持つ見出しだけ読み、servers[\"<種類> <名前>\"] を作って中の行を読む。"
            "子の無い見出しは未解釈。同じ鍵の見出しは同じ項目にまとめる"
        ),
    ),
    Pattern(
        "global.aaa_server_key", _AAA_SERVER_SCOPE, r"(?:pac )?key \S.*", "aaa_servers.servers.*.key_configured",
        "key 7 EXAMPLE", _aaa_server_key,
        note="手続き。子を持たない行だけ、key_configured を True にする。鍵の値は IR のどこにも入れない(ADR-0070 決定2)",
    ),
    Pattern(
        "global.aaa_server_setting", _AAA_SERVER_SCOPE, r".+", "aaa_servers.servers.*.settings",
        "address ipv4 192.0.2.10", _aaa_setting,
        note=f"手続き。子を持たない行の原文を settings の集合に足す(重ねない。昇順)。子を持つ行と、{_SECRET_NOTE}行は未解釈",
    ),
    Pattern(
        "global.aaa_server_host", "top", r"(?P<kind>tacacs|radius)-server host (?P<name>\S+)(?: (?P<rest>\S.*))?",
        "aaa_servers.servers", "radius-server host 192.0.2.20 vrf MGMT key 7 EXAMPLE", _aaa_server_host,
        note=(
            "手続き。servers[\"<種類> <名前またはアドレス>\"] に写す(ブロックの書き方と同じ項目にまとめる)。"
            "残りに key の語(大文字小文字は区別しない)があれば、key から行の終わりまでを外して key_configured を True にする。"
            f"外した後の残り(空でなければ)を settings の集合に1つの文字列で足す。それでも{_SECRET_NOTE}行は未解釈"
        ),
    ),
    Pattern(
        "global.aaa_group", "top", r"aaa group server (?P<kind>\S+) (?P<name>\S+)", "aaa_servers.groups",
        "aaa group server tacacs+ TAC-GROUP", _aaa_group,
        note=(
            "手続き。子を持つ見出しだけ読み、groups[\"<種類> <名前>\"] を作って中の行を読む。"
            "子の無い行は今までどおり aaa.line で aaa.lines に入る"
        ),
    ),
    Pattern(
        "global.aaa_group_setting", _AAA_GROUP_SCOPE, r".+", "aaa_servers.groups.*.settings", "server name TAC-1",
        _aaa_setting,
        note=f"手続き。子を持たない行の原文を settings の集合に足す(重ねない。昇順)。子を持つ行と、{_SECRET_NOTE}行は未解釈",
    ),
    Pattern(
        "global.aaa_option", "top", r"(?:no )?(?:ip radius|ip tacacs|radius-server|tacacs-server) \S.*",
        "aaa_servers.options", "ip radius vrf MGMT source-interface Management1", _aaa_option,
        note=f"手続き。子を持たない行の原文を options の集合に足す(重ねない。昇順)。{_SECRET_NOTE}行は未解釈",
    ),
    Pattern(
        "global.aaa.no_line", "top", r"no aaa \S.*", "aaa.lines", "no aaa root",
        _aaa_line, note="no aaa new-model 以外の no aaa 行は、aaa 行と同じ一覧に行の原文を出現順に持つ(ADR-0049 決定3)",
    ),
    Pattern(
        "global.aaa.line", "top", r"aaa \S.*", "aaa.lines", "aaa authentication login default local",
        _aaa_line, note="aaa new-model 以外の aaa 行は分解せず、行の原文を出現順に持つ",
    ),
    Pattern(
        "global.username", "top",
        r"username (?P<name>\S+)(?: privilege (?P<privilege>\d+))?(?: role (?P<role1>\S+))?"
        # 行全体に当てるので、型・値・後ろの role の語の数から読み方は1つに決まる(値が role という語でもよい)
        r" (?:(?P<kind>secret|password) (?P<a>\S+)(?: (?P<b>\S+))?|(?P<nopw>nopassword))"
        r"(?: role (?P<role2>\S+))?",
        "users", "username admin privilege 15 secret 9 $9$EXAMPLE", _username,
        note=(
            "手続き。secret / password の後ろが2語なら型と値、1語なら値だけ(型は None)。"
            "nopassword は credential を none にし、型と値は None。"
            "role は資格の前か後ろの1回だけ読み、両方にある行と同じ名前の二度目は読まない(未解釈)。"
            "値は読み取ったまま持つ"
        ),
    ),
    Pattern(
        "global.enable_secret", "top", r"enable secret (?!level )(?:(?P<type>\S+) )?(?P<value>\S+)",
        "enable_secret", "enable secret 9 $9$EXAMPLE", _enable("enable_secret"),
        note=_ONCE + "。level を含む行は読まない",
    ),
    Pattern(
        "global.enable_password", "top", r"enable password (?!level )(?:(?P<type>\S+) )?(?P<value>\S+)",
        "enable_password", "enable password 7 EXAMPLE", _enable("enable_password"),
        note=_ONCE + "。level を含む行は読まない",
    ),
    Pattern(
        "global.no_enable", "top", r"no enable (?:secret|password)", "enable_secret / enable_password",
        "no enable password", _no_enable, note="設定されていないことを明示した行。値は None のまま",
    ),
    # vtp_mode は vtp_option より前に置く(REQ-63)
    Pattern(
        "global.vtp_mode", "top", r"vtp mode (?P<mode>\S+)", "vtp.mode", "vtp mode transparent", _vtp_mode,
        note=(
            "方式の語を原文のまま持つ。二度目に現れた行は後の行の値で上書きする。"
            "語のどれかが password・secret・key・shared-secret の行は未解釈"
        ),
    ),
    Pattern(
        "global.vtp_option", "top", r"vtp (?!mode \S+$)(?P<rest>\S.*)", "vtp.options", "vtp domain EXAMPLE",
        _vtp_option,
        note=(
            "手続き。行の vtp の後ろを集合に足す(重ねない。昇順)。"
            "語のどれかが password・secret・key・shared-secret の行(vtp password <値> など)は未解釈"
        ),
    ),
    Pattern(
        "global.system_mtu", "top", r"system mtu (?P<rest>\S.*)", "system_mtu", "system mtu 9198", _system_mtu,
        note="手続き。行の system mtu の後ろを集合に足す(重ねない。昇順)",
    ),
    Pattern(
        "global.service", "top", r"(?:no )?service \S.*", "services", "service timestamps log datetime msec",
        _service,
        note=(
            "手続き。行の原文を集合に足す(重ねない。昇順)。"
            "password・secret・key の語の後ろに値が続く行は未解釈(service password-encryption は読む)"
        ),
    ),
    Pattern(
        "global.vlan_internal", "top", r"vlan internal (?P<rest>\S.*)", "vlan_internal",
        "vlan internal order ascending range 1006 1199", _vlan_internal, note=_ONCE,
    ),
    Pattern(
        "global.virtual_router_mac", "top", r"ip virtual-router mac-address (?P<mac>\S+)", "virtual_router_mac",
        "ip virtual-router mac-address 00:1c:73:00:00:99", _virtual_router_mac, note=_ONCE,
    ),
    # バナーは、区切りまでの複数行が改行でつながった1つの行(ADR-0030)。(?s) で . を改行にも当てる
    Pattern(
        "global.banner", "top", r"(?s)banner (?P<kind>\S+) (?P<rest>.+)", "banners",
        "banner motd ^C\nEXAMPLE notice\n^C", _banner,
        note=(
            "手続き。区切りは、種類の後ろが ^C で始まれば ^C、そうでなければ最初の1文字。"
            "最初の区切りの後ろから最後の区切りの前までを、前後の改行を外して banners[<種類>] に写す"
            "(区切りの違いは持たない。ADR-0116 決定1)。区切りが閉じていない行、同じ種類の2回目、"
            "本文の語のどれかが password・secret・key か、本文に鍵が埋まった(key, や password= などの)形がある行は未解釈。"
            "子を持たない行だけ読む"
        ),
    ),
)

# 子を持たない行だけ読む書式(REQ-61 §3、REQ-63 §3-2・§3-3、REQ-74 §3、REQ-87、REQ-81 §3、REQ-83 §3、REQ-161 §3-1)。
# 子を持つ行は、子も含めて未解釈
_LEAF_ONLY = frozenset(
    {
        "global.dns_domain",
        "global.ntp_server",
        "global.ntp_option",
        "global.logging_host",
        "global.logging_vrf_host",
        "global.logging_server",
        "global.logging_option",
        "global.no_logging_option",
        "global.dns_server",
        "global.dns_option",
        "global.routing",
        "global.default_gateway",
        "global.vtp_mode",
        "global.vtp_option",
        "global.system_mtu",
        "global.service",
        "global.vlan_internal",
        "global.virtual_router_mac",
        "global.aaa_server_host",
        "global.aaa_option",
        "global.snmp.community_group",
        "global.snmp.user",
        "global.snmp.group",
        "global.snmp.view",
        "global.snmp.option",
        "global.banner",
    }
)
# 子を持つ行だけ読む書式(REQ-81 §3)。子の無い行は、表の後ろの書式(aaa.line など)に通すか未解釈
_BLOCK_ONLY = frozenset({"global.aaa_server", "global.aaa_group"})
_BLOCK_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id not in _LEAF_ONLY)
_LEAF_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id not in _BLOCK_ONLY)


def _aaa_block_item(heading: str, text: str, result: Global) -> tuple[str, AaaServer | AaaServerGroup]:
    """見出しの行で作った項目と、中の行の scope。鍵は表で形を確かめた見出しの語の位置から取る。"""
    words = text.split(" ")
    if heading == "global.aaa_server":  # <種類> server <名前>
        return _AAA_SERVER_SCOPE, result["aaa_servers"]["servers"][f"{words[0]} {words[2]}"]
    # aaa group server <種類> <名前>
    return _AAA_GROUP_SCOPE, result["aaa_servers"]["groups"][f"{words[3]} {words[4]}"]


def _read_aaa_block(block: HConfigChild, heading: str, result: Global, interpreted: InterpretedLines) -> None:
    """認証サーバー・グループのブロックの中の行を読む。子を持つ中の行は、子も含めて未解釈。"""
    scope, item = _aaa_block_item(heading, block.text, result)
    for line in block.children:
        ctx = _AaaBlock(item, bool(line.children))
        if (pattern := apply_first(PATTERNS, scope, line.text, ctx)) is not None:
            interpreted.mark(line, pattern.id)


def extract_global(tree: HConfig, interpreted: InterpretedLines) -> Global:
    """tree のトップレベルの行から Global を返す。

    証明書チェーンと機器全体の設定(device_settings)、管理の入口(management)と feature(features)、
    証明書の trustpoint(trustpoints)も含む。
    """
    result = new_global()
    for block in tree.children:
        patterns = _BLOCK_PATTERNS if block.children else _LEAF_PATTERNS
        if (pattern := apply_first(patterns, "top", block.text, result)) is not None:
            interpreted.mark(block, pattern.id)
            if pattern.id in _BLOCK_ONLY:
                _read_aaa_block(block, pattern.id, result, interpreted)
    result["certificate_chains"] = extract_certificate_chains(tree, interpreted)
    result["device_settings"] = extract_device_settings(tree, interpreted)
    result["management"], result["features"] = extract_management(tree, interpreted)
    result["trustpoints"] = extract_trustpoints(tree, interpreted)
    return result
