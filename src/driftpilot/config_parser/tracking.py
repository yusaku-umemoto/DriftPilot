"""extractor が解釈した行を記録し、未解釈の行を取り出す基盤(ADR-0009 決定3)。

行はノードではなく、ルートからのパス(tuple(node.path()))で識別する。
hier_config のノードの hash/eq は行の文字列と子の内容で決まるため、
別の interface の下にある同じ行(例: "switchport mode trunk")を区別できないからである。

ただし、Arista の `comment` 〜 `EOF` の本文は、同じ段のほかの行と同じ文字列になりうる。本文の行は
パスではなく出現(ノードそのもの)で記録し、パスの記録より先に見る(REQ-178)。

すべての extractor は同じ InterpretedLines のインスタンスを受け取り、
IR に写せた行だけを mark する。
"""

from hier_config import HConfig, HConfigChild


class InterpretedLines:
    """解釈済みの行のパスを集める。"""

    def __init__(self) -> None:
        self.consumed: set[tuple[str, ...]] = set()
        # パス → 解釈したパターンの ID(被覆率のパターン別ヒット数に使う)
        self.pattern_ids: dict[tuple[str, ...], str] = {}
        # 出現ごとの記録。id(node) → (node, パターンの ID)。ID が None の行は未解釈に残す(REQ-178)。
        # node も持つのは、記録の間に id が別のノードに使い回されないようにするため
        self._occurrences: dict[int, tuple[HConfigChild, str | None]] = {}

    def mark(self, node: HConfigChild, pattern_id: str) -> None:
        """node の行を pattern_id として解釈済みと記録する。子行は含めない。

        同じ行を二度記録したら、最初の ID を残す。
        """
        path = tuple(node.path())
        self.consumed.add(path)
        self.pattern_ids.setdefault(path, pattern_id)

    def mark_occurrence(self, node: HConfigChild, pattern_id: str) -> None:
        """node の出現だけを pattern_id として解釈済みと記録する。同じパスのほかの出現には及ばない。子行は含めない。"""
        self._occurrences.setdefault(id(node), (node, pattern_id))

    def reserve(self, node: HConfigChild) -> None:
        """node の出現と子孫を、未解釈のまま後ろの extractor にも読ませない行として記録する(unparsed には残る)。"""
        for each in (node, *node.all_children()):
            self._occurrences.setdefault(id(each), (each, None))

    def pattern_id(self, node: HConfigChild) -> str | None:
        """node を解釈したパターンの ID。未解釈なら None。出現の記録があればそれを、無ければパスの記録を返す。"""
        if (occurrence := self._occurrences.get(id(node))) is not None:
            return occurrence[1]
        return self.pattern_ids.get(tuple(node.path()))

    def is_interpreted(self, node: HConfigChild) -> bool:
        return self.pattern_id(node) is not None

    def is_claimed(self, node: HConfigChild) -> bool:
        """解釈済みか、未解釈に残すと決めた行なら True。残りの行を読む extractor が読み飛ばす判定に使う。"""
        return id(node) in self._occurrences or self.is_interpreted(node)

    def unparsed(self, tree: HConfig) -> list[tuple[str, ...]]:
        """tree の全行のうち、解釈済みでない行のパスを config の出現順で返す。

        親行が解釈済みでも、子行は個別に判定する。
        """
        return [tuple(node.path()) for node in tree.all_children() if self.pattern_id(node) is None]
