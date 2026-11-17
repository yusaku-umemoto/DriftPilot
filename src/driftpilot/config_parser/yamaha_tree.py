"""ヤマハ RTX(NVR・FWX を含む)の config を、`pp select`・`tunnel select` の範囲で木に組む(ADR-0213 決定 2・5)。hier_config は使わない。

ヤマハの config は平らな 1 行のコマンドの並びで、`pp select <番号>`・`tunnel select <番号>` の後の行がその PPP の相手・
トンネルの設定になる。字下げは付くことも付かないこともあるので、字下げだけでは範囲を決めない。

行の見方: 1 行ずつ、空白を 1 つにまとめた文字列(以下「行」)で見る。字下げの有無は、元の行の頭の字(空白かタブ)で決める。

- 空の行と、行が `#` で始まる行(注釈。字下げのある `#` も)は木に入れない。行番号は元の本文の 1 始まりのまま
- 範囲を選ぶ行は、行全体が `pp select <数字>`・`pp select anonymous`・`pp select none`・`tunnel select <数字>`・
  `tunnel select none`(字下げは問わない)の行だけ。後ろにほかの語がある行(`pp select 1 <残り>`・`tunnel select anonymous` など)は
  範囲を選ぶ行にしない(下の「ほかの行」で置く)
  - `<数字>`・`anonymous` なら、範囲 `pp <数字>`・`pp anonymous`・`tunnel <数字>` を今の範囲にする。範囲のノードは最上位に、
    その範囲を選ぶ行が初めて出たところで作る(テキストは範囲の名前、scope_id は `YMH-pp`・`YMH-tunnel`、line_no は選ぶ行)
  - 同じ範囲をもう一度選んだときは、同じノードを今の範囲にし、選ぶ行は repeated_scope のノードとして範囲の子に出現順で置く
    (パスは範囲のノードと同じ。重複の警告にはしない)。選び直した後の行も同じ範囲の子になるので、2 つの塊は 1 つにまとまる
  - `none` なら今の範囲を無くす。`none` の行は木に入れない(行として数えない)
- ほかの行は、今の範囲があり、次のどちらかを満たせば範囲の子(テキストは行)、そうでなければ最上位の行にする
  - (a) 元の行に字下げがあり、直前の行(注釈・空の行・`none` の行を除く)が範囲の子か範囲を選ぶ行だった
  - (b) 行の頭が文脈の語: `pp `・`tunnel `(`pp select `・`tunnel select ` を除く)、`ppp `・`pppoe `・`ip pp `・`ipv6 pp `・
    `ip tunnel `・`ipv6 tunnel `・`ipsec tunnel `・`ipsec sa policy `・`ipsec ike `、`l2tp `(`l2tp service ` を除く)
  - 範囲が無い(まだ選んでいない・`none` の後)ときは、文脈の語でも最上位の行にする
- 範囲の子は 1 段だけ。字下げの深さは木にしない(2 字下げの行も範囲の 1 段目)

重複(同じ範囲の下の同じ行)は、最上位を含む範囲ごとに、テキストが同じ子が 2 つ以上あれば記録する(行番号だけで値は持たない)。
範囲を選ぶ行の選び直しは記録しない。flat_tree.find_duplicates(値に見える語の前を属性とみなす決まり)は使わない。

組み立てた木は B型・D型と同じ FlatTree / FlatNode。下流(extractor・被覆率・安全網)はそのまま動く。
"""

import re

from driftpilot.config_parser.flat_tree import DuplicateLine, FlatNode, FlatTree

PLATFORM = "yamaha"
# 組み立ての規則の版。規則を変えたら上げる(パターン表の指紋に入る)
TREE_VERSION = "yamaha-tree-v1"

SCOPE_ID_PP = "YMH-pp"
SCOPE_ID_TUNNEL = "YMH-tunnel"

# 範囲を選ぶ行(行全体)。none は範囲を無くす行
_SELECT = re.compile(r"(?P<kind>pp|tunnel) select (?P<key>\d+|anonymous|none)")
_SCOPE_IDS = {"pp": SCOPE_ID_PP, "tunnel": SCOPE_ID_TUNNEL}
# `tunnel select anonymous` は無い形(範囲を選ぶ行にしない)
_NO_ANONYMOUS = "tunnel"
# 文脈の語(行の頭)。字下げが無くても、範囲の中なら範囲の子にする
_CONTEXT_HEADS = (
    "pp ", "tunnel ", "ppp ", "pppoe ", "ip pp ", "ipv6 pp ", "ip tunnel ", "ipv6 tunnel ", "ipsec tunnel ",
    "ipsec sa policy ", "ipsec ike ", "l2tp ",
)
_CONTEXT_EXCEPTIONS = ("pp select ", "tunnel select ", "l2tp service ")


def _is_context(line: str) -> bool:
    return line.startswith(_CONTEXT_HEADS) and not line.startswith(_CONTEXT_EXCEPTIONS)


def build_yamaha_tree(text: str) -> FlatTree:
    """ヤマハの config のテキストを木にする(上の説明の規則)。"""
    tree = FlatTree(PLATFORM)
    scopes: dict[str, FlatNode] = {}
    current: FlatNode | None = None
    # 直前の行(注釈・空の行・none の行を除く)が範囲の子か範囲を選ぶ行だったか
    previous_in_scope = False
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = " ".join(raw.split())
        if not line or line.startswith("#"):
            continue
        indented = raw[:1] in (" ", "\t")
        select = _SELECT.fullmatch(line)
        if select is not None and not (select["kind"] == _NO_ANONYMOUS and select["key"] == "anonymous"):
            if select["key"] == "none":
                current = None
                continue
            name = f"{select['kind']} {select['key']}"
            node = scopes.get(name)
            if node is None:
                node = scopes[name] = tree._add(FlatNode(name, None, line_no, scope_id=_SCOPE_IDS[select["kind"]]))
            else:
                node._add(FlatNode(name, None, line_no, repeated_scope=True))
            current = node
            previous_in_scope = True
            continue
        if current is not None and ((indented and previous_in_scope) or _is_context(line)):
            current._add(FlatNode(line, current, line_no))
            previous_in_scope = True
        else:
            tree._add(FlatNode(line, None, line_no))
            previous_in_scope = False
    tree.add_duplicates(find_duplicates(tree))
    return tree


def find_duplicates(tree: FlatTree) -> list[DuplicateLine]:
    """同じ範囲(最上位を含む)の子のうち、テキストが同じ行が 2 つ以上あるものを返す。最上位のパスは `()`。

    範囲のノードと、範囲を選ぶ行の選び直し(repeated_scope)は比べない。
    """
    found: list[DuplicateLine] = []

    def visit(path: tuple[str, ...], nodes: list[FlatNode]) -> None:
        groups: dict[str, list[int]] = {}
        for node in nodes:
            if node.scope_id is None and not node.repeated_scope and node.line_no is not None:
                groups.setdefault(node.text, []).append(node.line_no)
        found.extend(DuplicateLine(path, tuple(lines)) for lines in groups.values() if len(lines) > 1)

    visit((), tree.children)
    for node in tree.children:
        if node.scope_id is not None:
            visit(node.path(), node.children)
    return found
