"""区間の選択(ADR-0012 決定5・7)。黙って選ばない。"""

from dataclasses import dataclass

from driftpilot import i18n
from driftpilot.segmentation.splitter import Segment

# 利用者に見える誤りの文。言語に合わせて読み込むときに 1 回だけ選ぶ(messages は読まない決まりのため、ここに持つ)
_TEXTS_JA = {
    "both": "区間の指定は --*-command と --*-segment のどちらか一方にしてください",
    "none": "比較できる区間がありません",
    "out_of_range": "区間 {number} はありません(1〜{count})",
    "no_match": "コマンドに {command} を含む区間がありません",
}
_TEXTS_EN = {
    "both": "Specify the segment with either --*-command or --*-segment, not both",
    "none": "There is no segment to compare",
    "out_of_range": "Segment {number} does not exist (1 to {count})",
    "no_match": "No segment has a command containing {command}",
}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)


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
        raise SelectionError(_T["both"])
    if not segments:
        raise SelectionError(_T["none"])
    if number is not None:
        if not 1 <= number <= len(segments):
            raise SelectionError(_T["out_of_range"].format(number=number, count=len(segments)))
        return segments[number - 1]
    if command is not None:
        hits = tuple(s for s in segments if s.command is not None and command in s.command)
        if not hits:
            raise SelectionError(_T["no_match"].format(command=repr(command)))
        return hits[0] if len(hits) == 1 else Candidates(hits)
    return segments[0] if len(segments) == 1 else Candidates(segments)
