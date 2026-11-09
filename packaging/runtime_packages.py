"""配るものに入る部品（driftpilot[gui,llm] の依存をたどったもの）の名前を、空白区切りで出す（ADR-0036 決定8）。

ビルドの道具（PyInstaller など）は入らない。依存の条件（OS など）は、この Python で評価する。
ライセンスの文を集める pip-licenses の --packages に渡す。driftpilot 自身は入れない。
"""

import importlib.metadata as md
import re
from collections import deque

from packaging.requirements import Requirement


def runtime_packages(root: str = "driftpilot", extras: tuple[str, ...] = ("gui", "llm")) -> list[str]:
    seen: dict[str, str] = {}
    queue = deque([(root, extras)])
    while queue:
        name, wanted = queue.popleft()
        key = re.sub(r"[-_.]+", "-", name).lower()
        if key in seen:
            continue
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue
        seen[key] = dist.metadata["Name"]
        for text in dist.requires or []:
            req = Requirement(text)
            if req.marker is None or any(req.marker.evaluate({"extra": e}) for e in (wanted or ("",))):
                queue.append((req.name, tuple(req.extras)))
    return sorted(v for k, v in seen.items() if k != root)


if __name__ == "__main__":
    print(" ".join(runtime_packages()))
