"""安全網4: 通常版と既定値込み版の取り違えを知らせる(ADR-0012 決定4)。

行数の比による推定はしない。警告と注意は終了コードに影響させない。
文面は持たない。kind と detail の構造だけを返し、文章化は入口が行う(ADR-0015)。
"""

from dataclasses import dataclass
from typing import Any, Literal

from driftpilot.segmentation.default_exposure import Exposure


@dataclass(frozen=True)
class ExposureSide:
    """比較の片側。既定値込み版の判定は区間ではなく、この側の属性として持つ。"""

    command: str | None
    state: Exposure


@dataclass(frozen=True)
class ExposureNotice:
    level: Literal["warning", "notice"]
    kind: Literal["mixed_exposure", "unknown_both", "partially_unknown"]
    detail: dict[str, Any]  # 側ごとの command と state
    emphasized: bool = False


def _side(side: ExposureSide) -> dict[str, Any]:
    return {"command": side.command, "state": side.state}


def check_default_exposure(before: ExposureSide, after: ExposureSide) -> list[ExposureNotice]:
    states = {before.state, after.state}
    if len(states) == 1 and states != {"unknown"}:
        return []
    detail = {"before": _side(before), "after": _side(after)}
    if states == {"normal", "exposed"}:
        return [ExposureNotice("warning", "mixed_exposure", detail, emphasized=True)]
    if states == {"unknown"}:
        return [ExposureNotice("notice", "unknown_both", detail)]
    known = before if before.state != "unknown" else after
    return [ExposureNotice("notice", "partially_unknown", detail, emphasized=known.state == "exposed")]
