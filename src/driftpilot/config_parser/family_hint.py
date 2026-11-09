"""config の中身から系統を見立てる(安全網5。ADR-0027 決定2・7、REQ-25 パート B)。

show 出力の書式判定(`format_detect.sniff`)とは別物である。
見立ては「指定された系統と中身が食い違っていないか」を疑う警告に使う。
機種を「自動」(AUTO_PLATFORM)にしたときだけ、detect_platform の判定で系統を選ぶ(ADR-0151 決定1。ADR-0027 決定1 を改めた)。
自動でなければ系統を選ぶのは人である。

判定の表と根拠の件数は docs/notes/platform-sniff.md(SNF-01〜03)。表に無い手がかりは足さない。
- SNF-01 か SNF-02 が当たれば D型、SNF-03 が当たれば A型
- D型の手がかりと A型の手がかりが両方当たった・どちらも当たらなかったときは判定しない(None)
- family_hint は B型(富士通)を判定しない(SR-S と Si-R を分けられないため)
- detect_platform は、Si-R の行頭スコープ表に合う行の割合(sir_share)が SIR_SHARE_MIN 以上なら fujitsu_sir とする。
  SR-S も Si-R として読む(確かめていない。ADR-0151 決定1)
"""

import re
from dataclasses import dataclass
from typing import Literal

from driftpilot.config_parser.flat_scopes import SIR_SCOPES, compiled

Family = Literal["a", "d"]
Rule = Literal["SNF-01", "SNF-02", "SNF-03"]
DetectedPlatform = Literal["fujitsu_sir", "fortinet", "a"]

# 機種を中身から決める指定(--platform auto、組のファイルの platform: auto。ADR-0151 決定4)
AUTO_PLATFORM = "auto"
# Si-R と判定する、行頭スコープ表に合う行の割合の下限(ADR-0151 決定1)
SIR_SHARE_MIN = 0.5

_CONFIG = re.compile(r"config [a-z]")
_A_LINE = re.compile(r"interface \S|hostname \S|!")


@dataclass(frozen=True)
class FamilyHint:
    """見立て1件。family は系統、rule はその見立ての手がかり(D型が2つとも当たれば表の順で SNF-01)。"""

    family: Family
    rule: Rule


def family_hint(text: str) -> FamilyHint | None:
    """区間を選んだ後の config の本文から系統を見立てる。判定しないときは None。"""
    rules = _rules(text)
    d_rules = [r for r in rules if r in ("SNF-01", "SNF-02")]
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
    if any(_A_LINE.match(line) for line in lines):
        rules.append("SNF-03")
    return rules


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


def detect_platform(text: str) -> DetectedPlatform | None:
    """片側の機種の判定(ADR-0151 決定1)。値は返さず、読み方も呼ばない。

    ① sir_share が SIR_SHARE_MIN 以上 → fujitsu_sir ② family_hint が d → fortinet ③ a → a ④ どれでもなければ None。
    """
    if sir_share(text) >= SIR_SHARE_MIN:
        return "fujitsu_sir"
    hint = family_hint(text)
    if hint is None:
        return None
    return "fortinet" if hint.family == "d" else "a"
