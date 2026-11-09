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
)


def extract_static_routes(tree: HConfig, interpreted: InterpretedLines) -> tuple[list[StaticRoute], list[dict[str, Any]]]:
    ctx = _Ctx()
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
