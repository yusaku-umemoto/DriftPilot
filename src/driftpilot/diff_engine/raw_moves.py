"""単純比較の行の移動の検出(ADR-0166 決定6。REQ-228 §3.2)。標準ライブラリだけを使う。

差分の行(削除と追加)だけから、同じ鍵の行が 3 行以上続いた塊が別の場所へ動いたものを探す。見つけた移動は記録と表示に
使うだけで、差分(diffs)からは外さない(状態は変わらない)。

決まり(依頼書のくり返しと同じ結果になるよう、ヒープで速く探す):
1. 削除の行か追加の行が MOVE_MAX_LINES を超えたら探さない(None)
2. まだ使っていない削除の行と追加の行の組で、鍵が同じものから始めて、両側とも行番号が 1 つずつ続き、鍵が同じ間だけ伸ばす。
   いちばん長い塊を選ぶ(同じ長さなら変更前の行番号の小さいもの、次に変更後の行番号の小さいもの)。長さが MOVE_MIN_LINES
   未満なら終わる
3. 塊の鍵の strip() が全部 1 文字以下なら、移動にせず、使ったことにだけする
4. 選んだ行は使ったことにする(1 行は 1 つの移動にだけ入る)
5. 最後に変更前の行番号の順に number を振る
"""

import heapq
from collections import defaultdict
from collections.abc import Sequence
from typing import TypedDict

MOVE_MIN_LINES = 3
MOVE_MAX_LINES = 2_000  # 削除・追加の行がこれを超えたら探さない


class RawMove(TypedDict):
    number: int  # 1 始まり。変更前の行の順
    before_start: int  # 変更前の最初の行番号(1 始まり)
    after_start: int  # 変更後の最初の行番号
    count: int  # 行数


def find_moves(removed: Sequence[tuple[int, str]], added: Sequence[tuple[int, str]]) -> list[RawMove] | None:
    """(行番号, 鍵) の削除と追加の行から移動を探す。行が多すぎて探さないときは None。"""
    limit = MOVE_MAX_LINES  # テストで差し替えられるよう、呼ぶたびに読む
    least = MOVE_MIN_LINES
    if len(removed) > limit or len(added) > limit:
        return None
    before = dict(removed)
    after = dict(added)
    by_key: dict[str, list[int]] = defaultdict(list)
    for number, key in added:
        by_key[key].append(number)

    def same(r: int, a: int) -> bool:
        return r in before and a in after and before[r] == after[a]

    # 斜めに続く塊(前に伸ばせない組から始めた、いちばん長い続き)を順番待ちの列に入れる
    heap: list[tuple[int, int, int]] = []  # (-長さ, 変更前の最初の行, 変更後の最初の行)
    for r, key in removed:
        for a in by_key.get(key, ()):
            if same(r - 1, a - 1):
                continue
            length = 1
            while same(r + length, a + length):
                length += 1
            if length >= least:
                heapq.heappush(heap, (-length, r, a))

    used_before: set[int] = set()
    used_after: set[int] = set()
    found: list[tuple[int, int, int]] = []  # (変更前の最初の行, 変更後の最初の行, 行数)
    while heap:
        negative, r, a = heapq.heappop(heap)
        length = -negative
        # まだ使っていない行だけで、続きを分け直す
        runs: list[tuple[int, int]] = []  # (塊の中の始まり, 長さ)
        start: int | None = None
        for k in range(length):
            free = (r + k) not in used_before and (a + k) not in used_after
            if free and start is None:
                start = k
            elif not free and start is not None:
                runs.append((start, k - start))
                start = None
        if start is not None:
            runs.append((start, length - start))
        if runs == [(0, length)]:
            # 数え直しても同じ長さ: いまいちばん長い塊(同じ長さなら行番号の小さいもの)
            used_before.update(range(r, r + length))
            used_after.update(range(a, a + length))
            if any(len(before[r + k].strip()) > 1 for k in range(length)):
                found.append((r, a, length))
            continue
        for offset, size in runs:
            if size >= least:
                heapq.heappush(heap, (-size, r + offset, a + offset))
    found.sort()
    return [
        {"number": number, "before_start": r, "after_start": a, "count": count}
        for number, (r, a, count) in enumerate(found, start=1)
    ]
