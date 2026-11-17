"""config の中身から系統を見立てる(安全網5。ADR-0027 決定2・7、REQ-25 パート B)。

show 出力の書式判定(`format_detect.sniff`)とは別物である。
見立ては「指定された系統と中身が食い違っていないか」を疑う警告に使う。
機種を「自動」(AUTO_PLATFORM)にしたときだけ、detect_platform の判定で系統を選ぶ(ADR-0151 決定1。ADR-0027 決定1 を改めた)。
自動でなければ系統を選ぶのは人である。

判定の表と根拠の件数は docs/notes/platform-sniff.md(SNF-01〜04)。表に無い手がかりは足さない。
- SNF-01・SNF-02・SNF-04 のどれかが当たれば D型、SNF-03 が当たれば A型
- SNF-04(全行が FortiOS の書式の断片)は、SNF-01・SNF-02 が当たらない入力だけで見る(ADR-0212 決定 1)
- D型の手がかりと A型の手がかりが両方当たった・どちらも当たらなかったときは判定しない(None)
- family_hint は B型(富士通)を判定しない(SR-S と Si-R を分けられないため)
- family_hint は B型(ヤマハ)も判定しない(ヤマハの config は系統 b のまま。安全網5 は今の決まりで動く)
- detect_platform は、Si-R の行頭スコープ表に合う行の割合(sir_share)が SIR_SHARE_MIN 以上なら fujitsu_sir とする。
  SR-S も Si-R として読む(確かめていない。ADR-0151 決定1)
- 次に、ヤマハの手がかりの行の割合(yamaha_share)が YAMAHA_SHARE_MIN 以上なら yamaha とする(ADR-0213 決定6。
  表と根拠の件数は docs/notes/platform-sniff.md)。Si-R の判定を先に見る
- 次に、Junos の手がかり(junos_hint。ADR-0225 決定1)と PAN-OS の手がかり(panos_hint。ADR-0227 決定1)を見る。
  両方当たれば判定しない(None)。片方だけなら、family_hint が None ならその機種(juniper_junos・paloalto_panos)、
  d なら判定しない、a なら A型の手がかりが `!` の行だけ(字下げを除いて `interface `・`hostname ` で始まる行が無い)ときだけ
  その機種、あれば判定しない(ADR-0227 決定3。ADR-0225 決定2 を改めた)
- family_hint は Junos を見立てない(系統の見立て SNF-01〜04 と安全網5 は変えない)
"""

import re
from dataclasses import dataclass
from typing import Literal

from driftpilot.config_parser.flat_scopes import SIR_SCOPES, compiled

Family = Literal["a", "d"]
Rule = Literal["SNF-01", "SNF-02", "SNF-03", "SNF-04"]
DetectedPlatform = Literal["fujitsu_sir", "yamaha", "fortinet", "juniper_junos", "paloalto_panos", "a"]

# 機種を中身から決める指定(--platform auto、組のファイルの platform: auto。ADR-0151 決定4)
AUTO_PLATFORM = "auto"
# Si-R と判定する、行頭スコープ表に合う行の割合の下限(ADR-0151 決定1)
SIR_SHARE_MIN = 0.5
# ヤマハと判定する、手がかりの行の割合の下限(ADR-0213 決定6)
YAMAHA_SHARE_MIN = 0.4

# Junos の手がかり(ADR-0225 決定1。表と根拠の件数は docs/notes/platform-sniff.md)
# set の形: 字下げなしの行がこの前置きで始まる行(JUNOS_SET_INTERFACE_UNIT の形も同じく数える)が JUNOS_SET_MIN 行以上
JUNOS_SET_HEADS = (
    "set system host-name ",
    "set routing-options ",
    "set protocols bgp ",
    "set protocols ospf ",
    "set protocols isis ",
    "set protocols lldp ",
    "set protocols mpls ",
    "set protocols rsvp ",
    "set protocols ldp ",
    "set policy-options ",
    "set security zones ",
    "set security policies ",
    "set security nat ",
    "set routing-instances ",
)
JUNOS_SET_INTERFACE_UNIT = re.compile(r"set interfaces \S+ unit ")
JUNOS_SET_MIN = 3
# brace の形: 字下げなしの `<語> {`(後ろの空白は可)の行の語。JUNOS_BRACE_KINDS_MIN 種類以上、
# または 1 種類以上と(version の行か JUNOS_COMMIT_HEADS の行)
JUNOS_BRACE_BLOCKS = (
    "system",
    "interfaces",
    "protocols",
    "routing-options",
    "policy-options",
    "security",
    "groups",
    "snmp",
    "firewall",
    "vlans",
    "chassis",
    "routing-instances",
)
JUNOS_BRACE_KINDS_MIN = 2
JUNOS_VERSION_LINE = re.compile(r"version \S+;")
JUNOS_COMMIT_HEADS = ("## Last commit: ", "## Last changed: ")

# PAN-OS の手がかり(ADR-0227 決定1。表と根拠の件数は docs/notes/platform-sniff.md)
# XML の形: 空白を除いた最初の字が `<` で、PANOS_XML_ROOT で始まる要素と PANOS_XML_CHILDREN のどれかで始まる要素がある
PANOS_XML_ROOT = "<config"
PANOS_XML_CHILDREN = ("<mgt-config", "<deviceconfig", "<devices", "<shared")
# set の形: 字下げなしの行がこの前置き(PANOS_SET_PATTERN の形も同じく数える)で始まる行が PANOS_SET_MIN 行以上
PANOS_SET_HEADS = (
    "set deviceconfig ",
    "set network interface ",
    "set network virtual-router ",
    "set network profiles ",
    "set network ike ",
    "set network tunnel ",
    "set rulebase security ",
    "set rulebase nat ",
    "set rulebase decryption ",
    "set rulebase pbf ",
    "set rulebase qos ",
    "set mgt-config ",
    "set shared log-settings ",
    "set shared certificate ",
    "set shared address ",
    "set shared service ",
)
PANOS_SET_PATTERN = re.compile(r"set zone \S+ network |set vsys vsys\d+ ")
PANOS_SET_MIN = 3
# brace の形: 字下げが 0〜4 個の空白の `<語> {`(後ろの空白は可)の行の語。PANOS_BRACE_KINDS_MIN 種類以上
PANOS_BRACE_BLOCKS = ("deviceconfig", "mgt-config", "rulebase", "network", "shared")
PANOS_BRACE_KINDS_MIN = 2
_PANOS_BRACE_LINE = re.compile(r" {0,4}(\S+) \{")

_CONFIG = re.compile(r"config [a-z]")
_A_LINE = re.compile(r"interface \S|hostname \S|!")
# SNF-04: 字下げを除いた対象の行が取ってよい形(全体で当たること)
_FORTIOS_LINE = re.compile(r"(?:config|edit|set|unset|append)\s+\S.*|(?:next|end)\s*")
_FORTIOS_BODY = re.compile(r"(?:set|unset|edit)\s")


@dataclass(frozen=True)
class FamilyHint:
    """見立て1件。family は系統、rule はその見立ての手がかり(D型が2つとも当たれば表の順で SNF-01)。"""

    family: Family
    rule: Rule


def family_hint(text: str) -> FamilyHint | None:
    """区間を選んだ後の config の本文から系統を見立てる。判定しないときは None。"""
    rules = _rules(text)
    d_rules = [r for r in rules if r in ("SNF-01", "SNF-02", "SNF-04")]
    a_rules = [r for r in rules if r == "SNF-03"]
    if d_rules and not a_rules:
        return FamilyHint("d", d_rules[0])
    if a_rules and not d_rules:
        return FamilyHint("a", a_rules[0])
    return None


def _rules(text: str) -> list[Rule]:
    """当たった手がかりを表の順に返す(コーパスの回帰テストで「両方当たった」を数えるため)。"""
    lines = text.splitlines()
    rules: list[Rule] = []
    first = next((line for line in lines if line.strip()), "")
    if first.startswith("#config-version="):
        rules.append("SNF-01")
    configs = sum(1 for line in lines if _CONFIG.match(line))
    ends = sum(1 for line in lines if line.rstrip() == "end")
    sets = sum(1 for line in lines if line.lstrip().startswith("set "))
    if configs >= 2 and ends >= 1 and sets >= 2:
        rules.append("SNF-02")
    if not rules and _snf04(lines):
        rules.append("SNF-04")
    if any(_A_LINE.match(line) for line in lines):
        rules.append("SNF-03")
    return rules


def _snf04(lines: list[str]) -> bool:
    """全行が FortiOS の書式の行でできた断片か(SNF-04。空行と # で始まる行は数えない)。"""
    body = [line for line in lines if line.strip() and not line.lstrip().startswith("#")]
    if not body or not _CONFIG.match(body[0]):
        return False
    if not any(line.rstrip() == "end" for line in body):
        return False
    stripped = [line.strip() for line in body]
    if not any(_FORTIOS_BODY.match(line) for line in stripped):
        return False
    return all(_FORTIOS_LINE.fullmatch(line) for line in stripped)


def sir_share(text: str) -> float:
    """空行と ! か # で始まる行を除いた行のうち、SIR_SCOPES の match に合う行の割合。行が無ければ 0。"""
    counted = 0
    matched = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("!", "#")):
            continue
        counted += 1
        if any(compiled(scope.match).match(line) for scope in SIR_SCOPES):
            matched += 1
    return matched / counted if counted else 0.0


_YAMAHA_IF = r"(?:lan\d+(?:/\d+)?|vlan\d+|bridge\d+|loopback\d+|pp|tunnel)"
# ヤマハの手がかりの行。select の 2 つの形は行全体で当てる
_YAMAHA_SELECT = re.compile(r"(?:pp select (?:\d+|anonymous|none)|tunnel select (?:\d+|none))")
# そのほかは、空白を 1 つにまとめた行の頭で当てる。表は docs/notes/platform-sniff.md
_YAMAHA_HEAD = re.compile(
    rf"(?:ip|ipv6) {_YAMAHA_IF} "
    r"|(?:ip|ipv6) filter (?:dynamic )?\d+ "
    r"|nat descriptor |(?:ip|ipv6) route \S+ gateway "
    r"|ipsec (?:ike|sa policy|tunnel|auto refresh|transport) "
    r"|(?:pp|ppp|pppoe) \S|tunnel (?:enable|disable|encapsulation|endpoint|type) "
    r"|(?:dhcp|dns|l2tp|pptp|sshd|telnetd) service |dhcp scope "
    r"|login password(?: |$)|administrator password(?: |$)|console (?:prompt|character) "
    r"|provider (?:lan\d+|type|select) "
    r"|ip policy |dns (?:server select|static|host) "
)


def yamaha_share(text: str) -> float:
    """空行と、前後の空白を除いて # か ! で始まる行を除いた行のうち、ヤマハの手がかりの行の割合。行が無ければ 0。

    手がかりは、空白を 1 つにまとめた行が _YAMAHA_SELECT に全体で当たる行か、行の頭が _YAMAHA_HEAD に当たる行
    (docs/notes/platform-sniff.md の表)。
    """
    counted = 0
    matched = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("!", "#")):
            continue
        counted += 1
        squeezed = " ".join(stripped.split())
        if _YAMAHA_SELECT.fullmatch(squeezed) or _YAMAHA_HEAD.match(squeezed):
            matched += 1
    return matched / counted if counted else 0.0


def junos_hint(text: str) -> bool:
    """Junos の手がかり(ADR-0225 決定1)が当たるか。set の形か brace の形のどちらかに当たれば真。

    字下げのある行は数えない。splitlines で分けるので CRLF でも同じに判定する。
    """
    set_lines = 0
    blocks: set[str] = set()
    marker = False
    for line in text.splitlines():
        if line.startswith(JUNOS_SET_HEADS) or JUNOS_SET_INTERFACE_UNIT.match(line):
            set_lines += 1
        body = line.rstrip()
        if body.endswith(" {") and body[:-2] in JUNOS_BRACE_BLOCKS:
            blocks.add(body[:-2])
        elif JUNOS_VERSION_LINE.fullmatch(body) or line.startswith(JUNOS_COMMIT_HEADS):
            marker = True
    if set_lines >= JUNOS_SET_MIN:
        return True
    return len(blocks) >= JUNOS_BRACE_KINDS_MIN or (len(blocks) >= 1 and marker)


def panos_hint(text: str) -> bool:
    """PAN-OS の手がかり(ADR-0227 決定1)が当たるか。XML・set・brace の形のどれかに当たれば真。

    set の形は字下げのある行を数えない。brace の形は字下げ 0〜4 個の空白(タブは数えない)。
    splitlines で分けるので CRLF でも同じに判定する。
    """
    if text.lstrip().startswith("<") and PANOS_XML_ROOT in text:
        if any(child in text for child in PANOS_XML_CHILDREN):
            return True
    set_lines = 0
    blocks: set[str] = set()
    for line in text.splitlines():
        if line.startswith(PANOS_SET_HEADS) or PANOS_SET_PATTERN.match(line):
            set_lines += 1
        match = _PANOS_BRACE_LINE.fullmatch(line.rstrip())
        if match and match.group(1) in PANOS_BRACE_BLOCKS:
            blocks.add(match.group(1))
    return set_lines >= PANOS_SET_MIN or len(blocks) >= PANOS_BRACE_KINDS_MIN


def _has_interface_or_hostname_line(text: str) -> bool:
    """字下げを除いて `interface `・`hostname ` で始まる行があるか(A型の手がかりが `!` の行だけかを見分ける。ADR-0227 決定3)。"""
    return any(line.lstrip().startswith(("interface ", "hostname ")) for line in text.splitlines())


def detect_platform(text: str) -> DetectedPlatform | None:
    """片側の機種の判定(ADR-0151 決定1、ADR-0213 決定6、ADR-0225 決定2、ADR-0227 決定2・3)。値は返さず、読み方も呼ばない。

    ① sir_share が SIR_SHARE_MIN 以上 → fujitsu_sir ② yamaha_share が YAMAHA_SHARE_MIN 以上 → yamaha
    ③ junos_hint と panos_hint が両方真 → None。片方だけ真なら family_hint が None → その機種(juniper_junos・paloalto_panos)、
       d → None、a → `interface `・`hostname ` の行が無いときだけその機種、あれば None
    ④ family_hint が d → fortinet、a → a、無ければ None。
    """
    if sir_share(text) >= SIR_SHARE_MIN:
        return "fujitsu_sir"
    if yamaha_share(text) >= YAMAHA_SHARE_MIN:
        return "yamaha"
    hint = family_hint(text)
    junos = junos_hint(text)
    panos = panos_hint(text)
    if junos and panos:
        return None
    if junos or panos:
        platform: DetectedPlatform = "juniper_junos" if junos else "paloalto_panos"
        if hint is None:
            return platform
        if hint.family == "a" and not _has_interface_or_hostname_line(text):
            return platform
        return None
    if hint is None:
        return None
    return "fortinet" if hint.family == "d" else "a"
