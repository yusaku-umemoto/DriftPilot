"""ACL を IR の acls・ipv6_acls・mac_acls に写す extractor。

エントリの順序は意味を持つので、出現順のリストで持つ。中身(permit / deny の条件)は分解しない。
IPv4・IPv6・MAC の ACL は機器の中で名前の空間が別なので、カテゴリを分ける(ADR-0101 決定2、REQ-82)。
種類の語を書かない Arista・NX-OS の `ip access-list <名前>` は、拡張の ACL として acls に入れる(決定1)。
ただし子の行が無い `ip access-list persistent` は、IOS-XE の機器全体の設定なので読まない(ADR-0121、REQ-166)。
"""

import re
from dataclasses import dataclass, field

from hier_config import HConfig

from driftpilot.config_parser.ir import Acl, AclEntry, AclType
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_STANDARD_RANGES = ((1, 99), (1300, 1999))
_EXTENDED_RANGES = ((100, 199), (2000, 2699))
_PERSISTENT = "persistent"


@dataclass
class _Ctx:
    acls: dict[str, Acl]
    ipv6_acls: dict[str, Acl] = field(default_factory=dict)
    mac_acls: dict[str, Acl] = field(default_factory=dict)
    named: set[str] = field(default_factory=set)
    current: Acl | None = None
    # 読んでいる最上位の行が子を持つか
    has_children: bool = False


def _numbered_type(number: int) -> AclType | None:
    if any(low <= number <= high for low, high in _STANDARD_RANGES):
        return "standard"
    if any(low <= number <= high for low, high in _EXTENDED_RANGES):
        return "extended"
    return None


def _open(acls: dict[str, Acl], name: str, type_: AclType, ctx: _Ctx) -> bool:
    """名前付き ACL を作り、中の行の写し先にする。同じカテゴリにすでにある名前は上書きせず読まない。"""
    if name in acls:
        return False
    ctx.current = acls[name] = {"name": name, "type": type_, "numbered": False, "entries": []}
    return True


def _named(m: re.Match[str], ctx: _Ctx) -> bool:
    """名前付き ACL を作る。種類の語が無い見出しは extended(ADR-0101 決定1)。"""
    if not _open(ctx.acls, m["name"], m.groupdict().get("type") or "extended", ctx):
        return False
    ctx.named.add(m["name"])
    return True


def _named_untyped(m: re.Match[str], ctx: _Ctx) -> bool:
    """種類の語が無い見出し。子の無い `ip access-list persistent` は IOS-XE の機器全体の設定なので読まない
    (device_settings が読む。ADR-0121、REQ-166)。ほかは _named と同じ。"""
    if m["name"] == _PERSISTENT and not ctx.has_children:
        return False
    return _named(m, ctx)


def _ipv6_named(m: re.Match[str], ctx: _Ctx) -> bool:
    """IPv6 の ACL を作る。standard の語があれば standard、無ければ extended。"""
    return _open(ctx.ipv6_acls, m["name"], "standard" if m["type"] is not None else "extended", ctx)


def _mac_named(m: re.Match[str], ctx: _Ctx) -> bool:
    """MAC の ACL を作る。extended の語の有無によらず extended。"""
    return _open(ctx.mac_acls, m["name"], "extended", ctx)


def _numbered(m: re.Match[str], ctx: _Ctx) -> bool:
    """番号付き ACL の1行を、その番号の ACL のエントリに足す。

    番号が標準・拡張の番号帯に無い行と、同じ名前の名前付き ACL がある行は読まない。
    同じ番号の行が複数あるのは追加であり、衝突ではない。
    """
    type_ = _numbered_type(int(m["number"]))
    key = m["number"]
    if type_ is None or key in ctx.named:
        return False
    acl = ctx.acls.get(key)
    if acl is None:
        acl = ctx.acls[key] = {"name": key, "type": type_, "numbered": True, "entries": []}
    acl["entries"].append({"seq": None, "text": m["body"]})
    return True


def _entry(m: re.Match[str], ctx: _Ctx) -> bool:
    """名前付き ACL の子行をエントリとして出現順に足す。先頭の数字は seq、残りは text。"""
    seq = m["seq"]
    entry: AclEntry = {"seq": int(seq) if seq is not None else None, "text": m["text"]}
    ctx.current["entries"].append(entry)
    return True


# 並び＝優先順位(現在の if / elif の順)。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "acls.named", "top", r"ip access-list (?P<type>standard|extended|role-based) (?P<name>\S+)", "acls[name]",
        "ip access-list extended OUTSIDE_IN", _named,
        note="手続き。同じ名前がすでにあれば読まない(その行と子行は未解釈)",
    ),
    Pattern(
        "acls.named_untyped", "top", r"ip access-list (?!(?:standard|extended|role-based)$)(?P<name>\S+)",
        "acls[name]", "ip access-list ACL-EXAMPLE", _named_untyped,
        note=(
            "手続き。Arista・NX-OS の種類の語を書かない見出し。type を extended にして acls に入れる(ADR-0101 決定1)。"
            "名前が standard・extended・role-based の1語だけの行は読まない。"
            "同じ名前がすでにあれば読まない(その行と子行は未解釈)。"
            "子の行が無い `ip access-list persistent` は IOS-XE の機器全体の設定なので読まない"
            "(device_settings が読む。ADR-0121、REQ-166)"
        ),
    ),
    Pattern(
        "acls.numbered", "top", r"access-list (?P<number>\d+) (?P<body>\S.*)", "acls[number].entries",
        "access-list 10 permit 192.0.2.0 0.0.0.255", _numbered,
        note=(
            "手続き。番号帯で type を決める(標準 1-99, 1300-1999 / 拡張 100-199, 2000-2699)。"
            "帯に無い番号と、同名の名前付き ACL がある番号は読まない。連番は付けない"
        ),
    ),
    Pattern(
        "acls.entries", "ip access-list", r"(?:(?P<seq>\d+) )?(?P<text>\S.*)", "acls[name].entries",
        "10 permit ip any any", _entry,
        note="手続き。名前付き ACL の子行はすべてエントリとして出現順に持つ。中身は分解しない",
    ),
    Pattern(
        "ipv6_acls.named", "top", r"ipv6 access-list (?:(?P<type>standard) )?(?!standard$)(?P<name>\S+)",
        "ipv6_acls[name]", "ipv6 access-list ACL6-EXAMPLE", _ipv6_named,
        note=(
            "手続き。standard の語があれば type を standard、無ければ extended にする(ADR-0101 決定2)。"
            "名前が standard の1語だけの行は読まない。同じ名前がすでにあれば読まない(その行と子行は未解釈)"
        ),
    ),
    Pattern(
        "ipv6_acls.entries", "ipv6 access-list", r"(?:(?P<seq>\d+) )?(?P<text>\S.*)", "ipv6_acls[name].entries",
        "10 permit ipv6 2001:db8::/32 any", _entry,
        note="手続き。acls.entries と同じく、子行はすべてエントリとして出現順に持つ。中身は分解しない",
    ),
    Pattern(
        "mac_acls.named", "top", r"mac access-list (?:extended )?(?!extended$)(?P<name>\S+)", "mac_acls[name]",
        "mac access-list extended MACL-EXAMPLE", _mac_named,
        note=(
            "手続き。extended の語の有無によらず type を extended にする(ADR-0101 決定2)。"
            "名前が extended の1語だけの行は読まない。同じ名前がすでにあれば読まない(その行と子行は未解釈)"
        ),
    ),
    Pattern(
        "mac_acls.entries", "mac access-list", r"(?:(?P<seq>\d+) )?(?P<text>\S.*)", "mac_acls[name].entries",
        "permit any any", _entry,
        note="手続き。acls.entries と同じく、子行はすべてエントリとして出現順に持つ。中身は分解しない",
    ),
)
# 見出しの id → 中の行の scope
_ENTRY_SCOPES = {
    "acls.named": "ip access-list",
    "acls.named_untyped": "ip access-list",
    "ipv6_acls.named": "ipv6 access-list",
    "mac_acls.named": "mac access-list",
}


def extract_acls(
    tree: HConfig, interpreted: InterpretedLines
) -> tuple[dict[str, Acl], dict[str, Acl], dict[str, Acl]]:
    """IPv4(名前付き・番号付き)・IPv6・MAC の ACL を、名前(番号の文字列)をキーにした辞書の組で返す。

    同じカテゴリの中でキーが衝突したら、後の方は上書きせず unparsed に残す。
    同じ番号の `access-list` 行が複数あるのは追加であり、衝突ではない。
    """
    ctx = _Ctx({})
    for block in tree.children:
        ctx.has_children = bool(block.children)
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)
        if (scope := _ENTRY_SCOPES.get(heading.id)) is not None:
            for line in block.children:
                if (pattern := apply_first(PATTERNS, scope, line.text, ctx)) is not None:
                    interpreted.mark(line, pattern.id)
    return ctx.acls, ctx.ipv6_acls, ctx.mac_acls
