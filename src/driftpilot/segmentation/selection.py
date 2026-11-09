"""区間の選択(ADR-0012 決定5・7)。黙って選ばない。"""

from dataclasses import dataclass

from driftpilot.segmentation.splitter import Segment


class SelectionError(ValueError):
    """範囲外・一致0件・指定の併用。呼び出し側ではエラー(終了コード 2)にする。"""


@dataclass(frozen=True)
class Candidates:
    """選択が必要。比較せずに一覧を出す(終了コード 3)。"""

    segments: tuple[Segment, ...]


def select(
    segments: tuple[Segment, ...], *, command: str | None = None, number: int | None = None
) -> Segment | Candidates:
    if command is not None and number is not None:
        raise SelectionError("区間の指定は --*-command と --*-segment のどちらか一方にしてください")
    if not segments:
        raise SelectionError("比較できる区間がありません")
    if number is not None:
        if not 1 <= number <= len(segments):
            raise SelectionError(f"区間 {number} はありません(1〜{len(segments)})")
        return segments[number - 1]
    if command is not None:
        hits = tuple(s for s in segments if s.command is not None and command in s.command)
        if not hits:
            raise SelectionError(f"コマンドに {command!r} を含む区間がありません")
        return hits[0] if len(hits) == 1 else Candidates(hits)
    return segments[0] if len(segments) == 1 else Candidates(segments)
