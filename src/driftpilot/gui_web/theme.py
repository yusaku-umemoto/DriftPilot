"""窓の画面の色(ADR-0189 / REQ-256 §5)。

- 設定ファイルの [gui] theme("system"・"light"・"dark")を読み(current_theme)、窓を開くときの URL の # の後ろに付ける
  (themed_url。"system" は付けない)。画面の側(static/js/theme.js)が # の後ろを読んで <html> の data-theme に当てる
- 「保存」の後は、開いている窓すべてに Python から知らせる(apply_theme。bridge.notify の {"type": "theme", "theme": 値})。
  画面の側は "system" なら data-theme を外し、OS の設定に従う

webview は読まない(窓の操作は bridge.notify を通す。テストは偽の窓を渡す)。
"""

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from driftpilot.gui_web.bridge import notify
from driftpilot.llm_settings import THEME_SYSTEM, THEMES, gui_theme

# Python から画面への知らせの種類(static/js/theme.js が受ける)
THEME_EVENT = "theme"
# URL の # の後ろの鍵(static/js/theme.js が読む)
THEME_FRAGMENT_KEY = "theme"


def current_theme(
    *, env: Mapping[str, str] | None = None, platform: str | None = None, home: Path | None = None
) -> str:
    """今の設定ファイルの画面の色。読めなければ "system"。"""
    return gui_theme(env=env, platform=platform, home=home)


def themed_url(url: str, theme: str) -> str:
    """窓の URL に画面の色を付ける。light・dark なら `<url>#theme=<値>`、"system"(とそれ以外)は付けない。

    元の URL に # の後ろがあれば外す(色の指定だけを残す)。
    """
    base = url.split("#", 1)[0]
    if theme in THEMES and theme != THEME_SYSTEM:
        return f"{base}#{THEME_FRAGMENT_KEY}={theme}"
    return base if "#" in url else url


def themed_window(args: Mapping[str, Any], theme: str) -> dict[str, Any]:
    """create_window に渡す引数の写し。"url" があれば themed_url に置き換える(元の dict は変えない)。"""
    result = dict(args)
    url = result.get("url")
    if isinstance(url, str):
        result["url"] = themed_url(url, theme)
    return result


def apply_theme(windows: Iterable[Any], theme: str) -> None:
    """開いている窓それぞれに、色を当てる知らせを送る。theme は THEMES のどれかだけ。1 つの窓の失敗は捨てて続ける。"""
    if theme not in THEMES:
        return
    for window in windows:
        try:
            notify(window, {"type": THEME_EVENT, "theme": theme})
        except Exception:  # noqa: BLE001 - 閉じかけの窓への知らせの失敗は捨てる
            pass
