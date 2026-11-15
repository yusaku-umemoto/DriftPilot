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
IOS-XR は、木にする前に閉じの語(`end-policy`・`end-set` など)の行を消し、XR と見立てた config だけ最上位の同じ見出しの
塊を 1 つにまとめる(config_parser/iosxr.py。ADR-0211。ルート直下の重複の扱いはその後の文字列に対して今のまま)。
A型では、この木を抽出の前に字下げの崩れを直してから使う(ADR-0121 決定2、indentation.py)。
文章を字下げで数えた値は、木の中身の数ではない。木について数えるときは build_tree() を通す。
"""

import ast
import re
from functools import lru_cache

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


@lru_cache(maxsize=None)
def _line_rules() -> tuple[PerLineSubRule, ...]:
    """木にする前に各行へ当てる、行を消す規則(ライブラリの規則と、DriftPilot が足した規則)。"""
    rules = get_hconfig_driver(Platform.CISCO_IOS).rules
    return (*rules.per_line_sub, *_EIGRP_EXIT_RULES)


def is_dropped_line(line: str) -> bool:
    """この行が、木にする前の前処理で消える(木に残らない)か。バナーの外の行について答える。

    ライブラリと同じく、先頭の空白を残して残りの空白を1つにまとめてから、規則を順に当てる。
    字下げの無い `!`・`#` のコメントや `end`・`version …` がこれに当たる(AW+ のポートの範囲の分割が使う。REQ-289)。
    """
    indent = len(line) - len(line.lstrip())
    text = " " * indent + " ".join(line.split())
    for rule in _line_rules():
        text = re.sub(rule.search, rule.replace, text)
    return not text.rstrip()


def banner_line_kinds(lines: list[str]) -> list[int]:
    """各行がバナーの中か。0 は外、1 はバナーの最初の行(`banner …`)、2 はバナーの続き(終わりの行を含む)。

    ライブラリがバナーを1つの子にまとめる規則と同じに数える。終わりの印は、`^` で始まる行・`EOF`・`%`・`!`・
    最初の行の3語目(区切りの印)の1文字目と2文字目・その3語目を含む行で、印は次のバナーにも引き継がれる。
    最初の行そのものでは終わらない。終わりが無ければ最後の行までバナー。
    """
    kinds: list[int] = []
    end_lines = {"EOF", "%", "!"}
    end_contains: list[str] = []
    in_banner = False
    for line in lines:
        if in_banner:
            kinds.append(2)
            if line.startswith("^") or line in end_lines or any(marker in line for marker in end_contains):
                in_banner = False
            continue
        if line.startswith("banner ") and line != "banner motd ##":
            in_banner = True
            kinds.append(1)
            words = line.split()
            if len(words) > 2:
                end_contains.append(words[2])
                if words[2].startswith('"'):
                    end_contains.append('"')
                end_lines.add(words[2][:1])
                end_lines.add(words[2][:2])
            continue
        kinds.append(0)
    return kinds


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
