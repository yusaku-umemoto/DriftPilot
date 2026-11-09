"""config テキストを hier_config のツリーにする。IR 用に、情報を変える既定の動作を外す。

hier_config の Cisco IOS ドライバは、ツリー化の後に次の加工をする。
- 名前付き ACL の remark を消す
- 名前付き ACL の permit / deny に連番を振る
IR はこれらを `remark` と「連番が無いこと」(seq=None)として保持したいので、この2つは外す。

ツリーは入力のとおりに持ち、同じ親の下の同じ行(重複)は捨てずに両方残す。重複は diff_engine が警告で知らせる
(ADR-0030 決定2・決定5)。証明書の16進数の行(折り返した行が同じ値になる)も、この扱いに含まれる。
ただしルート直下(字下げの無い行)の重複は、ライブラリが規則を見ないため、今までどおり DuplicateChildError になる
(ADR-0030 決定3。例外の内側への差し替えはしない)。

hier_config はツリーにする前に、テキストを前処理する。木の中身は入力の文章と同じではない(ADR-0030 決定8)。
- 各行の空白を1つにまとめ、ドライバの per_line_sub で行を消す。Cisco IOS では `!`・`#` で始まる行
  (`!!!!!` を含む)、`end`、`version …`、` exit-address-family`、` exit-peer-policy`、` exit-peer-session`、
  ` no ip address…` など。加えて DriftPilot が、名前付き EIGRP の終わりの印 ` exit-af-topology`、
  ` exit-sf-topology`、` exit-af-interface`、` exit-sf-interface`、` exit-service-family` を消す規則を足す
  (ADR-0067 決定2。ライブラリの規則は残したまま足す)
- バナー(`banner … ^C` から終わりの区切りまで)を、改行を含む1つの子にまとめる
- 木を組んだ後に、ブロックの最後の子が `exit` なら消す(同じ `exit` が2つ並べば、後ろの1つだけが消える)
A型では、この木を抽出の前に字下げの崩れを直してから使う(ADR-0121 決定2、indentation.py)。
文章を字下げで数えた値は、木の中身の数ではない。木について数えるときは build_tree() を通す。
"""

import ast

from hier_config import HConfig, MatchRule, Platform, get_hconfig, get_hconfig_driver
from hier_config.models import ParentAllowsDuplicateChildRule, PerLineSubRule

_DISABLED_CALLBACKS = frozenset({"_remove_ipv4_acl_remarks", "_add_acl_sequence_numbers"})

# 重複を許す親の深さの上限。ライブラリが規則と親の深さを完全一致で見るため(child.py の is_lineage_match)、
# 親を限定しない規則を深さ1からこの段数まで1つずつ並べる。コーパスの木は最も深いもので5段。
# これより深い親の下の重複は DuplicateChildError になり、構造化比較の失敗の文面(どのブロックの下か)に落ちる。
# 取りこぼしではなく設計上の落ち先である(REQ-26 §A-1)
_DUPLICATE_RULE_DEPTH = 16

_DUPLICATE_RULES = tuple(
    ParentAllowsDuplicateChildRule(match_rules=(MatchRule(),) * depth) for depth in range(1, _DUPLICATE_RULE_DEPTH + 1)
)

# 名前付き EIGRP の終わりの印。値を持たず、同じ親の下に何度も並ぶので、BGP の exit-address-family と同じく
# 前処理で消す(ADR-0067 決定2)
_EIGRP_EXIT_RULES = tuple(
    PerLineSubRule(search=rf"^\s*{marker}$", replace="")
    for marker in (
        "exit-af-topology",
        "exit-sf-topology",
        "exit-af-interface",
        "exit-sf-interface",
        "exit-service-family",
    )
)

_DUPLICATE_PREFIX = "Found a duplicate section: "


def build_tree(text: str) -> HConfig:
    """Cisco IOS / IOS-XE の config テキストをツリーにする。ルート直下の重複は DuplicateChildError。"""
    driver = get_hconfig_driver(Platform.CISCO_IOS)
    rules = driver.rules
    # rules は frozen なので、リストを置き換えずに中身を直す(ドライバーはこの呼び出し専用の新しいインスタンス)
    rules.post_load_callbacks[:] = [
        callback
        for callback in rules.post_load_callbacks
        if getattr(callback, "__name__", "") not in _DISABLED_CALLBACKS
    ]
    rules.parent_allows_duplicate_child.extend(_DUPLICATE_RULES)
    rules.per_line_sub.extend(_EIGRP_EXIT_RULES)
    return get_hconfig(driver, text)


def duplicate_path(exc: Exception) -> tuple[str, ...] | None:
    """DuplicateChildError の例外文から、ルートから重複した行までのパスを取り出す。取れなければ None。

    ライブラリは 'Found a duplicate section: (親…, 行)' の形で例外文を作る。例外文の形に頼るのはここだけ。
    """
    message = str(exc)
    if not message.startswith(_DUPLICATE_PREFIX):
        return None
    try:
        path = ast.literal_eval(message[len(_DUPLICATE_PREFIX) :])
    except (ValueError, SyntaxError):
        return None
    if not isinstance(path, tuple) or not path or not all(isinstance(p, str) for p in path):
        return None
    return path
