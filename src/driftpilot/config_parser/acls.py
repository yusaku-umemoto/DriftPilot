"""ACL を IR の acls・ipv6_acls・mac_acls に写す extractor。

エントリの順序は意味を持つので、出現順のリストで持つ。中身(permit / deny の条件)は分解しない。
IPv4・IPv6・MAC の ACL は機器の中で名前の空間が別なので、カテゴリを分ける(ADR-0101 決定2、REQ-82)。
種類の語を書かない Arista・NX-OS の `ip access-list <名前>` は、拡張の ACL として acls に入れる(決定1)。
ただし子の行が無い `ip access-list persistent` は、IOS-XE の機器全体の設定なので読まない(ADR-0121、REQ-166)。
"""

import re
from dataclasses import dataclass, field

from hier_config import HConfig

from driftpilot.config_parser.device_settings import has_mask_word
from driftpilot.config_parser.ir import Acl, AclEntry, AclType
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

_STANDARD_RANGES = ((1, 99), (1300, 1999))
_EXTENDED_RANGES = ((100, 199), (2000, 2699))
_PERSISTENT = "persistent"
# ASA の `access-list <名前>` の塊の子の頭の語で、読めるもの(REQ-303)
_ASA_KINDS = frozenset({"extended", "standard", "remark"})


@dataclass
class _Ctx:
    acls: dict[str, Acl]
    ipv6_acls: dict[str, Acl] = field(default_factory=dict)
    mac_acls: dict[str, Acl] = field(default_factory=dict)
    named: set[str] = field(default_factory=set)
    # NEC IX の1行の形で作った名前(IPv4・IPv6)。同じ名前の次の1行は、この集合にあれば足す
    single_line: set[str] = field(default_factory=set)
    single_line_v6: set[str] = field(default_factory=set)
    current: Acl | None = None
    # 読んでいる最上位の行が子を持つか
    has_children: bool = False
    # ASA と見立てた config か(REQ-303)と、読んでいる最上位の行の子の文
    asa: bool = False
    children: tuple[str, ...] = ()


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
    """名前付き ACL を作る。種類の語が無い見出しは extended(ADR-0101 決定1)。

    見出しの種類が standard・extended で、名前が ASCII の数字だけ、その数の番号帯の種類が見出しの種類と同じなら
    numbered を真にする(`ip access-list standard 10` は `access-list 10 …` と同じ ACL。ADR-0217 決定4、REQ-310)。
    """
    written = m.groupdict().get("type")
    if not _open(ctx.acls, m["name"], written or "extended", ctx):
        return False
    name = m["name"]
    if written in ("standard", "extended") and name.isascii() and name.isdigit() and _numbered_type(int(name)) == written:
        ctx.current["numbered"] = True
    ctx.named.add(name)
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


def _single_line(acls: dict[str, Acl], made: set[str], m: re.Match[str], ctx: _Ctx) -> bool:
    """NEC IX の1行の ACL を、名前の ACL のエントリに足す(出現順。同じ行が2回あれば2つ)。

    子を持つ行と、伏せる表の秘密語がある行(名前が `snmp` など)は読まない。同じカテゴリにすでにある名前は、
    1行の形で作った名前でなければ読まない(見出しの形・番号付きで先にできた ACL に足さない。ADR-0209 決定5、REQ-292)。
    """
    if ctx.has_children or has_mask_word(m.string):
        return False
    name = m["name"]
    acl = acls.get(name)
    if acl is None:
        acl = acls[name] = {"name": name, "type": "extended", "numbered": False, "entries": []}
        made.add(name)
    elif name not in made:
        return False
    acl["entries"].append({"seq": None, "text": " ".join(m["text"].split())})
    return True


def _single_line_v4(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ip access-list <名前> permit|deny <残り>`。名前は ctx.named にも入れる(同じ番号の `access-list` 行は読まない)。"""
    if not _single_line(ctx.acls, ctx.single_line, m, ctx):
        return False
    ctx.named.add(m["name"])
    return True


def _single_line_v6(m: re.Match[str], ctx: _Ctx) -> bool:
    """`ipv6 access-list <名前> permit|deny <残り>`。"""
    return _single_line(ctx.ipv6_acls, ctx.single_line_v6, m, ctx)


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


def _asa_named(m: re.Match[str], ctx: _Ctx) -> bool:
    """ASA の `access-list <名前>` の塊(木にする前にまとめた形。asa.py)を、名前(番号の名前を含む)の ACL にする。

    ASA と見立てた config で、子が 1 つ以上あり、子の頭の語が全部 extended・standard・remark のどれかで、
    remark でない子の頭の語が全部同じで、同じ名前がまだ無いときだけ読む。type は remark でない最初の子の頭の語
    (全部 remark なら extended)、numbered は偽。ほかの塊(webtype・ethertype などの子がある、extended と standard が
    混ざる)は読まず、見出しと子は未解釈のまま(REQ-303)。
    """
    heads = [words[0] if (words := child.split()) else "" for child in ctx.children]
    if not ctx.asa or not heads or any(head not in _ASA_KINDS for head in heads):
        return False
    kinds = [head for head in heads if head != "remark"]
    if len(set(kinds)) > 1:
        return False
    type_: AclType = "standard" if kinds and kinds[0] == "standard" else "extended"
    return _open(ctx.acls, m["name"], type_, ctx)


def _asa_entry(m: re.Match[str], ctx: _Ctx) -> bool:
    """ASA の `access-list <名前>` の塊の子を、エントリとして出現順に足す(同じ文の子も両方)。seq は取らない。"""
    ctx.current["entries"].append({"seq": None, "text": m["text"]})
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
        "acls.single_line", "top", r"ip access-list (?!dynamic )(?P<name>\S+) (?P<text>(?:permit|deny) .+)",
        "acls[name].entries", "ip access-list ACL-VPN permit ip src 198.51.100.0/24 dest 203.0.113.0/24",
        _single_line_v4,
        note=(
            "手続き。NEC IX の1行の ACL(子を持たない行)。名前が初めてなら type を extended・numbered を偽にして作り、"
            "`permit|deny <残り>`(空白は1つ)を seq の無いエントリとして出現順に足す(同じ行が2回あれば2つ。ADR-0209 決定5、REQ-292)。"
            "子を持つ行と、伏せる表の秘密語がある行(名前が snmp など)は未解釈。同じ名前がすでにあれば、"
            "1行の形で作った名前でなければ未解釈(後から来る同じ名前の見出しの形も acls.named 系の決まりで未解釈)。"
            "名前が dynamic の行と、名前の次の語が permit・deny でない行は当たらず未解釈"
        ),
    ),
    Pattern(
        "acls.named", "top", r"ip access-list (?P<type>standard|extended|role-based) (?P<name>\S+)", "acls[name]",
        "ip access-list extended OUTSIDE_IN", _named,
        note=(
            "手続き。同じ名前がすでにあれば読まない(その行と子行は未解釈)。"
            "numbered は、見出しの種類が standard か extended で、名前が ASCII の数字だけで、その数の番号帯の種類"
            "(標準 1-99, 1300-1999 / 拡張 100-199, 2000-2699)が見出しの種類と同じときだけ真、それ以外は偽"
            "(`ip access-list standard 10` は真、`ip access-list extended 10`・`role-based 10` は偽。ADR-0217 決定4、REQ-310)"
        ),
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
        "acls.asa_named", "top", r"access-list (?P<name>\S+)", "acls[name]", "access-list OUT", _asa_named,
        note=(
            "手続き。Cisco ASA と見立てた config だけ(木にする前に、`access-list <名前> <文>` の行を名前ごとに"
            "`access-list <名前>` の塊にまとめている。asa.py)。子が 1 つ以上あり、子の頭の語が全部 extended・standard・"
            "remark のどれかで、remark でない子の頭の語が全部同じで、同じ名前がまだ acls に無いときだけ読む。"
            "type は remark でない最初の子の頭の語(全部 remark なら extended)、numbered は偽(番号の名前も同じ)。"
            "ほかの塊(webtype・ethertype などの子がある、extended と standard が混ざる)と、"
            "ASA と見立てない config の 2 語だけの行は読まず、見出しと子は未解釈(ADR-0214 決定4、REQ-303)"
        ),
    ),
    Pattern(
        "acls.asa_entries", "access-list", r"(?P<text>\S.*)", "acls[name].entries", "extended permit ip any any",
        _asa_entry,
        note=(
            "手続き。ASA の `access-list <名前>` の塊の子を、seq の無いエントリとして出現順に足す"
            "(同じ文の子も両方。頭の数字は seq に取らない)。中身は分解しない(REQ-303)"
        ),
    ),
    Pattern(
        "acls.entries", "ip access-list", r"(?:(?P<seq>\d+) )?(?P<text>\S.*)", "acls[name].entries",
        "10 permit ip any any", _entry,
        note="手続き。名前付き ACL の子行はすべてエントリとして出現順に持つ。中身は分解しない",
    ),
    Pattern(
        "ipv6_acls.single_line", "top", r"ipv6 access-list (?!dynamic )(?P<name>\S+) (?P<text>(?:permit|deny) .+)",
        "ipv6_acls[name].entries", "ipv6 access-list ACL6-VPN permit icmp src any dest any", _single_line_v6,
        note=(
            "手続き。acls.single_line の IPv6 版(ADR-0209 決定5、REQ-292)。type は extended・numbered は偽。"
            "子を持つ行と、伏せる表の秘密語がある行は未解釈。同じ名前がすでにあれば、1行の形で作った名前でなければ未解釈"
        ),
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
    "acls.asa_named": "access-list",
    "ipv6_acls.named": "ipv6 access-list",
    "mac_acls.named": "mac access-list",
}


def extract_acls(
    tree: HConfig, interpreted: InterpretedLines, asa: bool = False
) -> tuple[dict[str, Acl], dict[str, Acl], dict[str, Acl]]:
    """IPv4(名前付き・番号付き)・IPv6・MAC の ACL を、名前(番号の文字列)をキーにした辞書の組で返す。

    同じカテゴリの中でキーが衝突したら、後の方は上書きせず unparsed に残す。
    同じ番号の `access-list` 行が複数あるのは追加であり、衝突ではない。
    asa は Cisco ASA と見立てた config か。真のとき、`access-list <名前>` の塊(asa.py がまとめた形)を読む(REQ-303)。
    """
    ctx = _Ctx({}, asa=asa)
    for block in tree.children:
        ctx.has_children = bool(block.children)
        ctx.children = tuple(child.text for child in block.children)
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)
        if (scope := _ENTRY_SCOPES.get(heading.id)) is not None:
            for line in block.children:
                if (pattern := apply_first(PATTERNS, scope, line.text, ctx)) is not None:
                    interpreted.mark(line, pattern.id)
    return ctx.acls, ctx.ipv6_acls, ctx.mac_acls
