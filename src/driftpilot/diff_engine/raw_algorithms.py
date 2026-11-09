"""単純比較の比べ方(ADR-0166 決定7。REQ-228 §4.2)。標準ライブラリだけを使う(新しい依存は足さない)。

- standard: difflib.SequenceMatcher(autojunk=False)。今までの単純比較と同じ
- minimal: 前と後ろの共通の行を除き、残りに Myers の方法(変更の数がいちばん少ない差分)を当てる。
  変更の数(削除と追加の行数の合計)が MINIMAL_MAX_EDITS を超えたらやめて standard で求め直す(使った比べ方は "standard")
- patience: 前と後ろの共通の行を除き、両側にちょうど 1 回ずつある行を目印にして、目印の組の最長の増える並びを求める。
  目印の間を同じ方法でくり返し比べる。目印の無い区間は standard で比べる

返す一覧は difflib の get_opcodes と同じ形((tag, i1, i2, j1, j2) の並び。tag は equal・delete・insert・replace)。
"""

import difflib
from bisect import bisect_left
from collections import Counter
from collections.abc import Hashable, Sequence

from driftpilot.raw_keys import Algorithm

MINIMAL_MAX_EDITS = 1_000

Opcode = tuple[str, int, int, int, int]
# 行ごとの操作: ("=", i, j)・("-", i, -1)・("+", -1, j)
_LineOp = tuple[str, int, int]


def opcodes(a: Sequence[Hashable], b: Sequence[Hashable], algorithm: Algorithm) -> tuple[list[Opcode], Algorithm]:
    """difflib の get_opcodes と同じ形の一覧と、実際に使った比べ方を返す。"""
    if algorithm == "minimal":
        ops = _minimal(a, b)
        if ops is None:
            return _standard(a, b), "standard"
        return _group(ops), "minimal"
    if algorithm == "patience":
        return _group(_patience(a, b)), "patience"
    return _standard(a, b), "standard"


def _standard(a: Sequence[Hashable], b: Sequence[Hashable]) -> list[Opcode]:
    return list(difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes())


def _standard_ops(a: Sequence[Hashable], b: Sequence[Hashable], a0: int, a1: int, b0: int, b1: int) -> list[_LineOp]:
    """a[a0:a1] と b[b0:b1] を standard で比べ、行ごとの操作(元の番号)にする。"""
    ops: list[_LineOp] = []
    for tag, i1, i2, j1, j2 in _standard(a[a0:a1], b[b0:b1]):
        if tag == "equal":
            ops += [("=", a0 + i1 + k, b0 + j1 + k) for k in range(i2 - i1)]
            continue
        ops += [("-", a0 + i, -1) for i in range(i1, i2)]
        ops += [("+", -1, b0 + j) for j in range(j1, j2)]
    return ops


def _group(ops: Sequence[_LineOp]) -> list[Opcode]:
    """行ごとの操作を get_opcodes の形にまとめる。続いた同じ行は equal、equal にはさまれた削除と追加は 1 つの replace
    (片側だけなら delete か insert)。
    """
    result: list[Opcode] = []
    i = j = 0
    k = 0
    total = len(ops)
    while k < total:
        if ops[k][0] == "=":
            start_i, start_j = i, j
            while k < total and ops[k][0] == "=":
                i += 1
                j += 1
                k += 1
            result.append(("equal", start_i, i, start_j, j))
            continue
        start_i, start_j = i, j
        while k < total and ops[k][0] != "=":
            if ops[k][0] == "-":
                i += 1
            else:
                j += 1
            k += 1
        if i > start_i and j > start_j:
            tag = "replace"
        elif i > start_i:
            tag = "delete"
        else:
            tag = "insert"
        result.append((tag, start_i, i, start_j, j))
    return result


def _trim(a: Sequence[Hashable], b: Sequence[Hashable], a0: int, a1: int, b0: int, b1: int) -> tuple[int, int, int, int, int]:
    """前と後ろの共通の行を数える。(前の共通の数, 新しい a0, a1, b0, b1)を返す(後ろの共通の数は a1 の差で分かる)。"""
    head = 0
    while a0 + head < a1 and b0 + head < b1 and a[a0 + head] == b[b0 + head]:
        head += 1
    a0, b0 = a0 + head, b0 + head
    while a1 > a0 and b1 > b0 and a[a1 - 1] == b[b1 - 1]:
        a1 -= 1
        b1 -= 1
    return head, a0, a1, b0, b1


# --- minimal(Myers の方法) ---


def _minimal(a: Sequence[Hashable], b: Sequence[Hashable]) -> list[_LineOp] | None:
    """変更の数がいちばん少ない行ごとの操作。変更の数が MINIMAL_MAX_EDITS を超えたら None。"""
    limit = MINIMAL_MAX_EDITS  # テストで差し替えられるよう、呼ぶたびに読む
    n, m = len(a), len(b)
    head, a0, a1, b0, b1 = _trim(a, b, 0, n, 0, m)
    middle = _myers(a[a0:a1], b[b0:b1], limit)
    if middle is None:
        return None
    ops: list[_LineOp] = [("=", k, k) for k in range(head)]
    for op, i, j in middle:
        ops.append((op, a0 + i if i >= 0 else -1, b0 + j if j >= 0 else -1))
    ops += [("=", a1 + k, b1 + k) for k in range(n - a1)]
    return ops


def _myers(a: Sequence[Hashable], b: Sequence[Hashable], limit: int) -> list[_LineOp] | None:
    """Myers の O(ND) の方法。編集の数 D が limit を超えたら None。d ごとに V の写しを持ち、後ろからたどる。"""
    n, m = len(a), len(b)
    if n == 0 and m == 0:
        return []
    most = min(n + m, limit)
    offset = most + 1
    v = [0] * (2 * most + 3)
    trace: list[list[int]] = []
    found = -1
    for d in range(most + 1):
        trace.append(v[:])
        for k in range(-d, d + 1, 2):
            if k == -d or (k != d and v[offset + k - 1] < v[offset + k + 1]):
                x = v[offset + k + 1]
            else:
                x = v[offset + k - 1] + 1
            y = x - k
            while x < n and y < m and a[x] == b[y]:
                x += 1
                y += 1
            v[offset + k] = x
            if x >= n and y >= m:
                found = d
                break
        if found >= 0:
            break
    if found < 0:
        return None
    ops: list[_LineOp] = []
    x, y = n, m
    for d in range(found, 0, -1):
        prev = trace[d]
        k = x - y
        if k == -d or (k != d and prev[offset + k - 1] < prev[offset + k + 1]):
            prev_k = k + 1
        else:
            prev_k = k - 1
        prev_x = prev[offset + prev_k]
        prev_y = prev_x - prev_k
        while x > prev_x and y > prev_y:
            x -= 1
            y -= 1
            ops.append(("=", x, y))
        if prev_k == k + 1:
            ops.append(("+", -1, prev_y))
        else:
            ops.append(("-", prev_x, -1))
        x, y = prev_x, prev_y
    while x > 0 and y > 0:
        x -= 1
        y -= 1
        ops.append(("=", x, y))
    ops.reverse()
    return ops


# --- patience ---


def _anchors(a: Sequence[Hashable], b: Sequence[Hashable], a0: int, a1: int, b0: int, b1: int) -> list[tuple[int, int]]:
    """両側の区間にちょうど 1 回ずつある行の組のうち、最長の増える並び(patience sorting と二分探索)。"""
    count_a = Counter(a[a0:a1])
    count_b = Counter(b[b0:b1])
    where_b = {b[j]: j for j in range(b0, b1) if count_b[b[j]] == 1}
    pairs = [(i, where_b[a[i]]) for i in range(a0, a1) if count_a[a[i]] == 1 and a[i] in where_b]
    if not pairs:
        return []
    tops: list[int] = []  # 山ごとのいちばん上の j
    top_index: list[int] = []  # 山ごとのいちばん上の組の番号(pairs の位置)
    back: list[int] = [-1] * len(pairs)
    for index, (_i, j) in enumerate(pairs):
        pile = bisect_left(tops, j)
        if pile > 0:
            back[index] = top_index[pile - 1]
        if pile == len(tops):
            tops.append(j)
            top_index.append(index)
        else:
            tops[pile] = j
            top_index[pile] = index
    chain: list[tuple[int, int]] = []
    index = top_index[-1]
    while index >= 0:
        chain.append(pairs[index])
        index = back[index]
    chain.reverse()
    return chain


def _patience(a: Sequence[Hashable], b: Sequence[Hashable]) -> list[_LineOp]:
    """patience の行ごとの操作。くり返しは手元の積み上げで行う(深い呼び出しにしない)。"""
    ops: list[_LineOp] = []
    # 積み上げの 1 つ: ("range", a0, a1, b0, b1)・("standard", a0, a1, b0, b1)・("equal", i, j, 0, 0)。逆の順に積む
    stack: list[tuple[str, int, int, int, int]] = [("range", 0, len(a), 0, len(b))]
    while stack:
        task, p, q, r, s = stack.pop()
        if task == "equal":
            ops.append(("=", p, q))
            continue
        if task == "standard":
            ops += _standard_ops(a, b, p, q, r, s)
            continue
        head, a0, a1, b0, b1 = _trim(a, b, p, q, r, s)
        tail = q - a1  # 後ろの共通の行の数
        tasks: list[tuple[str, int, int, int, int]] = [("equal", p + k, r + k, 0, 0) for k in range(head)]
        chain = _anchors(a, b, a0, a1, b0, b1) if a0 < a1 and b0 < b1 else []
        if not chain:
            # 目印の無い区間(片側が空の区間も)は standard で比べる
            tasks.append(("standard", a0, a1, b0, b1))
        else:
            prev_i, prev_j = a0, b0
            for i, j in chain:
                tasks.append(("range", prev_i, i, prev_j, j))
                tasks.append(("equal", i, j, 0, 0))
                prev_i, prev_j = i + 1, j + 1
            tasks.append(("range", prev_i, a1, prev_j, b1))
        tasks += [("equal", a1 + k, b1 + k, 0, 0) for k in range(tail)]
        stack.extend(reversed(tasks))
    return ops
