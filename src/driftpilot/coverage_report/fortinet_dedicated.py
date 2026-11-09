"""FortiGate の「専用に読めた割合」の定義(REQ-190、ADR-0139 決定1)。

既定の表(置換メッセージ・ISDB の表など)は名前の一覧で決める(値を見ない)。
最上位のブロックの名前が一覧の名前と同じか、`<名前> ` で始まれば既定の表とする。
専用 = 汎用の形(`vendor_specific.fgt_generic.*`)以外のパターンで解釈された行。
"""

import re

# 汎用の形のパターン ID の頭(config_parser/fortinet.py の _GEN_ID と同じ)
GENERIC_PREFIX = "vendor_specific.fgt_generic."

# 既定の表の名前(REQ-190 §2。並びは依頼書のまま)
DEFAULT_TABLES: tuple[str, ...] = (
    "system replacemsg",
    "system replacemsg-image",
    "firewall internet-service-name",
    "firewall internet-service-definition",
    "firewall service category",
    "wanopt content-delivery-network-rule",
    "report layout",
    "system session-helper",
    "webfilter search-engine",
    "webfilter ftgd-risk-level",
    "log threat-weight",
    "dlp filepattern",
    "dlp data-type",
    "firewall wildcard-fqdn custom",
    "system custom-language",
    "firewall ssh local-key",
    "firewall ssh local-ca",
)

# 汎用の形の見出しのパターンと同じ書き方(config の後ろの空白はいくつでもよい)
_BLOCK = re.compile(r"config\s+(?P<name>.+)")


def block_name(top_line: str) -> str | None:
    """最上位の行からブロックの名前を取る(`config router bgp` → `router bgp`)。config の行でなければ None。"""
    match = _BLOCK.fullmatch(top_line.strip())
    return match.group("name").strip() if match else None


def is_default_table(name: str | None) -> bool:
    """ブロックの名前が既定の表に当たるか。一覧の名前と同じか、`<名前> ` で始まれば当たる。"""
    if name is None:
        return False
    return any(name == table or name.startswith(f"{table} ") for table in DEFAULT_TABLES)


def is_generic(pattern_id: str) -> bool:
    """汎用の形のパターンか。"""
    return pattern_id.startswith(GENERIC_PREFIX)
