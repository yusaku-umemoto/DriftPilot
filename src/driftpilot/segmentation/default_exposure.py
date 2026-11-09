"""既定値込み版かどうかの判定(ADR-0012 決定3b)。config と判定された区間にだけ適用する。"""

from typing import Literal

Exposure = Literal["normal", "exposed", "unknown"]

# 既定値まで含めて出力させる語。増えたときはここに足す(足し忘れは「通常版」と誤判定されうるため、
# 追加の判断は ADR-0012 Action Item 4b に従う)
EXPOSURE_WORDS = ("all", "full-configuration")


def classify_exposure(command: str | None) -> Exposure:
    if not command:
        return "unknown"
    words = command.lower().split()
    if any(w in EXPOSURE_WORDS for w in words):
        return "exposed"
    # 末尾の語が "all" の真の接頭辞(入力途中の可能性)。通常版とも既定値込み版とも取れる
    if words and "all".startswith(words[-1]) and words[-1] != "all":
        return "unknown"
    return "normal"
