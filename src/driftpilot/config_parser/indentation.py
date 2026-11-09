"""既定値込みの出力の字下げの崩れを、A型の木を作った後・抽出の前に直す前処理(ADR-0121 決定2、REQ-166)。

既定値込みの出力では、子を持たない1行の後ろの行が1段深く字下げされ、前の行の子になることがある
(例 `ip dhcp ping timeout 2` の次の ` ip dhcp auto-broadcast`)。次の条件をすべて満たす行 X の子を、X の親の子に戻す。
1. X が、その段の1行の書式に当たる(最上位: device_setting・logging_option・no_logging_option・dns_option。
   インターフェースの中: ip_options・port_options)
2. X がブロックの見出しの書式(global.device_block)に当たらない
3. X の子がすべて、子を持たず、同じ段の1行の書式に当たる。語のどれかが password・secret・key か、
   語の中に鍵が埋まった(key, や password= などの)子は無い
移す先(X の親)に同じ文字列の行がすでにある子は移さない(X の下に残り、X と共に未解釈)。
条件に当たらない行は、今までどおり子も含めて未解釈になる。
移した子は X のすぐ後ろに元の並びで置く。未解釈の一覧は config の出現順で、行の識別はルートからの道すじなので、
親を正しく持つ新しい行として作り直す(hier_config の move は行の親を書き換えないため使わない)。
正規表現は表(PATTERNS)の書式を ID で引いて使い、表の外に新しく書かない。
"""

import re

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser import device_settings, global_, interfaces
from driftpilot.config_parser.device_settings import has_secret

_TOP_LINE_IDS = ("global.device_setting", "global.logging_option", "global.no_logging_option", "global.dns_option")
_INTERFACE_LINE_IDS = ("interfaces.ip_options", "interfaces.port_options")
_BLOCK_ID = "global.device_block"
_INTERFACE_ID = "interfaces.interface"


def _compile(*ids: str) -> tuple[re.Pattern[str], ...]:
    table = {p.id: p for p in (*device_settings.PATTERNS, *global_.PATTERNS, *interfaces.PATTERNS)}
    return tuple(re.compile(table[pattern_id].match) for pattern_id in ids)


_TOP_LINES = _compile(*_TOP_LINE_IDS)
_INTERFACE_LINES = _compile(*_INTERFACE_LINE_IDS)
(_BLOCK,) = _compile(_BLOCK_ID)
(_INTERFACE,) = _compile(_INTERFACE_ID)


def _matches(regexes: tuple[re.Pattern[str], ...], text: str) -> bool:
    return any(regex.fullmatch(text) for regex in regexes)


def _movable(line: HConfigChild, regexes: tuple[re.Pattern[str], ...]) -> bool:
    """line の子を親に戻してよいか(条件 1〜3)。"""
    if not line.children or not _matches(regexes, line.text) or _BLOCK.fullmatch(line.text):
        return False
    return all(
        not child.children and _matches(regexes, child.text) and not has_secret(child.text)
        for child in line.children
    )


def _repair_level(parent: HConfig | HConfigChild, regexes: tuple[re.Pattern[str], ...]) -> None:
    """parent の直下の行のうち条件に当たる行の子を、parent の子として、その行のすぐ後ろに置き直す。"""
    targets = [line for line in parent.children if _movable(line, regexes)]
    if not targets:
        return
    present = {line.text for line in parent.children}
    moved: dict[int, list[HConfigChild]] = {}
    for line in targets:
        for child in list(line.children):
            if child.text in present:
                continue
            present.add(child.text)
            moved.setdefault(id(line), []).append(parent.instantiate_child(child.text))
            child.delete()
    order: list[HConfigChild] = []
    for line in parent.children:
        order.append(line)
        order.extend(moved.get(id(line), ()))
    parent.children.clear()
    for line in order:
        parent.children.append(line)


def repair_indentation(tree: HConfig) -> None:
    """最上位の行と、インターフェースの見出しの直下の行について、字下げの崩れを木の上で直す。"""
    _repair_level(tree, _TOP_LINES)
    for block in tree.children:
        if _INTERFACE.fullmatch(block.text):
            _repair_level(block, _INTERFACE_LINES)
