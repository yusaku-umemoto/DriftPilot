"""A型のトップレベルと NXOS の VRF スコープにあるスタティックルート。"""

from dataclasses import dataclass, field
from ipaddress import IPv4Address, IPv4Network, IPv6Address, ip_address, ip_network
from typing import Any
import re

from hier_config import HConfig

from driftpilot.config_parser.ir import StaticRoute
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

# name の後ろで、名前の終わりとみなす任意引数の語（ADR-0127）
_NAME_STOP = frozenset({"metric", "tag", "track", "permanent", "multicast", "global"})
# 機種固有値に、その語がある経路にだけ足すキー（ADR-0127）
_EXTRA_KEYS = ("track_object", "metric", "permanent", "multicast", "global", "dhcp")


@dataclass
class _Ctx:
    routes: list[StaticRoute] = field(default_factory=list)
    details: list[dict[str, Any]] = field(default_factory=list)
    scope_vrf: str | None = None
    asa: bool = False
    huawei: bool = False


def _address(token: str, version: int) -> IPv4Address | IPv6Address | None:
    try:
        value = ip_address(token)
    except ValueError:
        return None
    return value if value.version == version else None


def _number(token: str) -> bool:
    return token.isascii() and token.isdigit()


def _route(m: re.Match[str], ctx: _Ctx, version: int) -> bool:
    body = m["body"]
    parts = body.split()
    vrf = ctx.scope_vrf
    vrf_written = False
    if parts[0] == "vrf":
        if vrf is not None or len(parts) < 3:
            return False
        vrf, parts = parts[1], parts[2:]
        vrf_written = True
    if len(parts) < 2:
        return False
    destination_text = parts.pop(0)
    try:
        if "/" in destination_text:
            destination = ip_network(destination_text, strict=False)
        elif version == 4 and destination_text == "0.0.0.0":
            # IOS のマスク形を先に読む。EOS のマスク無し既定経路も同じ値へ写す。
            if parts and parts[0] == "0.0.0.0":
                parts.pop(0)
            destination = IPv4Network("0.0.0.0/0")
        elif version == 4 and _address(destination_text, 4) is not None:
            mask = parts.pop(0)
            if _address(mask, 4) is None:
                return False
            destination = ip_network(f"{destination_text}/{mask}", strict=False)
        else:
            return False
    except ValueError:
        return False
    if destination.version != version or not parts:
        return False

    interface: str | None = None
    next_hop: IPv4Address | IPv6Address | None = None
    extra: dict[str, Any] = {}
    first = parts.pop(0)
    if (address := _address(first, version)) is not None:
        next_hop = address
    elif version == 4 and first == "dhcp":
        extra["dhcp"] = True
    elif _is_interface(first):
        interface = first
        if parts and (address := _address(parts[0], version)) is not None:
            next_hop = address
            parts.pop(0)
    else:
        return False

    # 任意引数は順序を問わず、それぞれ1回まで読む。ほかの語は行ごと未解釈（ADR-0127）
    seen: set[str] = set()
    distance: int | None = None
    name: str | None = None
    tag: int | None = None
    track: bool | None = None
    while parts:
        word = parts.pop(0)
        # 距離の数は空の語として数える（split の語は空にならないので、ほかの語と重ならない）
        kind = "" if _number(word) else word
        if kind in seen:
            return False
        seen.add(kind)
        if not kind:
            distance = int(word)
        elif word == "track":
            value = parts.pop(0) if parts else ""
            if value == "bfd":
                track = True
            elif _number(value):
                extra["track_object"] = int(value)
            else:
                return False
        elif word in ("tag", "metric"):
            if not parts or not _number(parts[0]):
                return False
            if word == "tag":
                tag = int(parts.pop(0))
            else:
                extra["metric"] = int(parts.pop(0))
        elif word == "name":
            if not parts:
                return False
            # 1語目は必ず名前。次の任意引数の語の前までを空白1つでつなぐ
            words = [parts.pop(0)]
            while parts and parts[0] not in _NAME_STOP:
                words.append(parts.pop(0))
            name = " ".join(words)
        elif word in ("permanent", "multicast"):
            extra[word] = True
        elif word == "global" and vrf_written:
            extra["global"] = True
        else:
            return False

    ctx.routes.append({
        "destination": destination, "next_hop": next_hop, "interface": interface,
        "vrf": vrf, "distance": distance, "enabled": None,
    })
    detail: dict[str, Any] = {"name": name, "tag": tag, "track": track}
    detail.update((key, extra[key]) for key in _EXTRA_KEYS if key in extra)
    ctx.details.append(detail)
    return True


def _asa_route(m: re.Match[str], ctx: _Ctx, version: int) -> bool:
    """ASA の `route <nameif> …`・`ipv6 route <nameif> …`。ASA と見立てた config だけ読む(REQ-304)。"""
    if not ctx.asa:
        return False
    groups = m.groupdict()
    try:
        if version == 4:
            # マスクは IPv4 のアドレスの形だけ読む(`24` のような長さは未解釈。今の `ip route` の `_route` と同じ確かめ方)
            if _address(groups["mask"], 4) is None:
                return False
            destination = ip_network(f"{groups['dest']}/{groups['mask']}", strict=False)
        else:
            destination = ip_network(groups["dest"], strict=False)
    except ValueError:
        return False
    if destination.version != version:
        return False
    next_hop = _address(groups["hop"], version)
    if next_hop is None:
        return False
    distance = groups["distance"]
    ctx.routes.append({
        "destination": destination, "next_hop": next_hop, "interface": groups["nameif"],
        "vrf": None, "distance": int(distance) if distance else None, "enabled": None,
    })
    detail: dict[str, Any] = {"name": None, "tag": None, "track": None}
    if groups.get("track"):
        detail["track_object"] = int(groups["track"])
    ctx.details.append(detail)
    return True


# Huawei の出口インターフェースの名前で、Cisco の決まりに当たらない速度の名前(ADR-0231 決定 1。`10GE1/0/1` など)
_HUAWEI_SPEED_NAMES = ("10GE", "25GE", "40GE", "100GE")


def _is_huawei_interface(token: str) -> bool:
    if _is_interface(token):
        return True
    for prefix in _HUAWEI_SPEED_NAMES:
        rest = token.removeprefix(prefix)
        if rest != token and rest[:1].isascii() and rest[:1].isdigit() and not rest.strip("0123456789/.:"):
            return True
    return False


def _huawei_route(m: re.Match[str], ctx: _Ctx, version: int) -> bool:
    """Huawei の `ip route-static`・`ipv6 route-static`。Huawei と見立てた config だけ読む(ADR-0231 決定 3(3)、REQ-338)。"""
    if not ctx.huawei:
        return False
    parts = m["body"].split()
    vrf: str | None = None
    if parts[0] == "vpn-instance":
        if len(parts) < 2:
            return False
        vrf, parts = parts[1], parts[2:]
    if len(parts) < 3:
        return False
    destination_text, length_text = parts[0], parts[1]
    parts = parts[2:]
    if _address(destination_text, version) is None:
        return False
    try:
        if _number(length_text):
            length = int(length_text)
            if length > (32 if version == 4 else 128):
                return False
            destination = ip_network(f"{destination_text}/{length}", strict=False)
        elif version == 4 and _address(length_text, 4) is not None:
            destination = ip_network(f"{destination_text}/{length_text}", strict=False)
        else:
            return False
    except ValueError:
        return False

    interface: str | None = None
    next_hop: IPv4Address | IPv6Address | None = None
    first = parts.pop(0)
    if (address := _address(first, version)) is not None:
        next_hop = address
    elif _is_huawei_interface(first):
        interface = first
        if parts and (address := _address(parts[0], version)) is not None:
            next_hop = address
            parts.pop(0)
    else:
        return False

    distance: int | None = None
    while parts:
        if parts[0] != "preference" or distance is not None or len(parts) < 2 or not _number(parts[1]):
            return False
        distance = int(parts[1])
        if not 1 <= distance <= 255:
            return False
        del parts[:2]

    ctx.routes.append({
        "destination": destination, "next_hop": next_hop, "interface": interface,
        "vrf": vrf, "distance": distance, "enabled": None,
    })
    # 差分の処理が経路の一覧と位置で対応付けるので、ASA と同じく全部 None の値を積んで長さを揃える
    ctx.details.append({"name": None, "tag": None, "track": None})
    return True


def _huawei_ipv4(m: re.Match[str], ctx: _Ctx) -> bool:
    return _huawei_route(m, ctx, 4)


def _huawei_ipv6(m: re.Match[str], ctx: _Ctx) -> bool:
    return _huawei_route(m, ctx, 6)


def _asa_ipv4(m: re.Match[str], ctx: _Ctx) -> bool:
    return _asa_route(m, ctx, 4)


def _asa_ipv6(m: re.Match[str], ctx: _Ctx) -> bool:
    return _asa_route(m, ctx, 6)


def _ipv4(m: re.Match[str], ctx: _Ctx) -> bool:
    return _route(m, ctx, 4)


def _ipv6(m: re.Match[str], ctx: _Ctx) -> bool:
    return _route(m, ctx, 6)


# 出口IFの名前の決まり（ADR-0127 決定2）。表の note にも同じ決まりを書く
def _is_interface(token: str) -> bool:
    letters = token.rstrip("0123456789/.:")
    rest = token[len(letters):]
    return (
        bool(letters) and letters[0].isascii() and letters[0].isalpha()
        and all((c.isascii() and c.isalpha()) or c == "-" for c in letters)
        and rest[:1].isdigit()
    )


_ROUTE_NOTE = (
    "出口IFは英字で始まり英字か - が続き、数字で始まって数字・/・.・: が続く語。"
    "任意引数は順序を問わずそれぞれ1回まで: <数>(距離)・track bfd・track <数>・tag <数>・metric <数>・"
    "name <名前>(次の metric・tag・track・permanent・multicast・global の前まで)・permanent・multicast・"
    "global(vrf のある行だけ)。ほかの語は行ごと未解釈"
)

_HUAWEI_NOTE = (
    "Huawei VRP と見立てた config だけ(ADR-0231 決定 3(3)、REQ-338)。"
    "`[vpn-instance <V>] <宛先> <マスク|長さ> <次>[ <次のアドレス>] [preference <1〜255>]`。vpn-instance は vrf に、"
    "preference は distance に写す。次はアドレスなら next_hop、インターフェースの名前(Cisco の出口IFの決まりか、"
    "10GE・25GE・40GE・100GE の後に数字が続く語)なら interface(後ろにアドレスが続けば next_hop も)。"
    "次の位置の vpn-instance(VPN の間の経路)・description・tag・track・bfd・permanent・inherit-cost など上に無い語・"
    "形に合わないアドレス・マスク・長さ・数(雛形の行を含む)は行ごと未解釈。機種固有値は全部 None の値を積んで一覧と長さを揃える"
)


PATTERNS: tuple[Pattern, ...] = (
    Pattern("static_routes.ipv4", "top", r"ip route (?P<body>(?:vrf \S+ )?[0-9.]+(?:/[0-9]+)?(?: .+)?)",
            "static_routes[]", "ip route vrf MGMT 0.0.0.0/0 10.0.0.1", _ipv4,
            note="宛先・マスク・出口IF・次ホップ(dhcp も)・任意引数を検証してから写す。" + _ROUTE_NOTE),
    Pattern("static_routes.ipv6", "top", r"ipv6 route (?P<body>(?:vrf \S+ )?[0-9a-fA-F:]+/[0-9]+(?: .+)?)",
            "static_routes[]", "ipv6 route ::/0 2001:db8::1", _ipv6,
            note="IPv6 の宛先・出口IF・次ホップ・任意引数を検証してから写す。" + _ROUTE_NOTE),
    Pattern("static_routes.ipv4_in_vrf_context", "vrf context",
            r"ip route (?P<body>[0-9.]+(?:/[0-9]+)?(?: .+)?)", "static_routes[]",
            "ip route 0.0.0.0/0 10.0.0.2", _ipv4,
            note="見出しの VRF 名を経路に写す。見出しもこのパターン ID で解釈済みにする"),
    Pattern("static_routes.ipv6_in_vrf_context", "vrf context",
            r"ipv6 route (?P<body>[0-9a-fA-F:]+/[0-9]+(?: .+)?)", "static_routes[]",
            "ipv6 route ::/0 2001:db8::2", _ipv6,
            note="見出しの VRF 名を経路に写す。見出しもこのパターン ID で解釈済みにする"),
    Pattern("static_routes.asa_route", "top",
            r"route (?P<nameif>\S+) (?P<dest>\S+) (?P<mask>\S+) (?P<hop>\S+)"
            r"(?: (?P<distance>[0-9]+))?(?: track (?P<track>[0-9]+))?",
            "static_routes[]", "route outside 0.0.0.0 0.0.0.0 192.0.2.1 1", _asa_ipv4,
            note=(
                "ASA と見立てた config だけ。interface は nameif(インターフェースとは結び付けない)、vrf は None。"
                "track <数> は機種固有値 track_object。tunneled など語が続く行・次のアドレスが読めない行は未解釈"
            )),
    Pattern("static_routes.asa_ipv6_route", "top",
            r"ipv6 route (?P<nameif>\S+) (?P<dest>[^\s/]+/[0-9]+) (?P<hop>\S+)(?: (?P<distance>[0-9]+))?",
            "static_routes[]", "ipv6 route outside ::/0 2001:db8::1", _asa_ipv6,
            note="ASA と見立てた config だけ。interface は nameif、vrf は None。次のアドレスが読めない行は未解釈"),
    Pattern("static_routes.huawei_ipv4", "top", r"ip route-static (?P<body>\S.*)",
            "static_routes[]", "ip route-static 0.0.0.0 0.0.0.0 192.0.2.1", _huawei_ipv4,
            note=_HUAWEI_NOTE),
    Pattern("static_routes.huawei_ipv6", "top", r"ipv6 route-static (?P<body>\S.*)",
            "static_routes[]", "ipv6 route-static 2001:db8:1:: 64 2001:db8::1", _huawei_ipv6,
            note=_HUAWEI_NOTE + "。宛先は IPv6 のアドレスと長さ 0〜128、next_hop は IPv6 のアドレス"),
)


def extract_static_routes(
    tree: HConfig, interpreted: InterpretedLines, asa: bool = False, huawei: bool = False
) -> tuple[list[StaticRoute], list[dict[str, Any]]]:
    ctx = _Ctx(asa=asa, huawei=huawei)
    for block in tree.children:
        ctx.scope_vrf = None
        if (pattern := apply_first(PATTERNS, "top", block.text, ctx)) is not None:
            interpreted.mark(block, pattern.id)
        if block.text.startswith("vrf context "):
            name = block.text.removeprefix("vrf context ")
            if not name or " " in name:
                continue
            ctx.scope_vrf = name
            found: Pattern | None = None
            for line in block.children:
                if (pattern := apply_first(PATTERNS, "vrf context", line.text, ctx)) is not None:
                    interpreted.mark(line, pattern.id)
                    found = found or pattern
            if found is not None:
                interpreted.mark(block, found.id)
    return ctx.routes, ctx.details
