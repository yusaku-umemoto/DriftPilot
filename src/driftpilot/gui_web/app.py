"""pywebview の窓を開く(ADR-0074 / REQ-103 §3・REQ-104 §3・REQ-105 §2・REQ-106 §2)。

既定は比較の画面(static/compare.html)。そこから設定の窓と一括比較の窓を開く(gui_web/session.py の WebSession)。
設定の画面だけ(static/settings.html)、一括比較の画面だけ(static/batch.html)と、見本のページ(static/sample.html)も単独で開ける。

- 表示部品は pywebview の既定(Windows は WebView2、Mac は WKWebView)。start に gui= を渡さない
- HTTP サーバーを使わない(start の http_server は既定の False のまま)。pywebview はファイルのパスを渡すと
  http_server=False でも内部のサーバーを立てるので、ページは file:// の URL で渡す(webview.util.is_local_url)
- 窓は、比較の画面が 1180×860(最小 800×600)、設定の画面が 960×760、一括比較の画面が 1180×900(最小 800×640。
  どれも今の wx の窓と同じ)、見本が 1180×860(どれも論理の大きさ)。作業領域が取れれば、その中に収めて中央に置く(REQ-95)。
  一括比較の窓も比較の窓の位置によらず作業領域の中央に置く(wx の窓が画面の下にはみ出す点を直す。ADR-0074 決定4)。
  比較の窓と一括比較の窓は文字を選べるようにする(text_select。差分の文字をコピーできるように。pywebview の既定は選べない)。
  作業領域が取れるのは Windows だけ(Screen.frame が WinForms の WorkingArea)。Mac の frame は画面全体なので飛ばす
- Windows で WebView2 が無いと、pywebview は古い IE の部品(mshtml)に切り替わる。そのときは窓を開かずに止める
- 比較の窓へのファイルのドロップと、一括比較の窓へのフォルダとマニフェストのドロップは、読み込みの後に watch_drop で
  印の要素(#drop-anchor)の drop を Python の側で受ける(REQ-110・REQ-115・REQ-205)。dragover と document の drop は
  画面のスクリプト(static/js/drop.js)が止め、ファイルの一覧だけを印の要素へ送る(ADR-0147)。
  一括比較の窓は単独で開いたとき(--batch)も受ける
- AI の窓(static/ai_pane.html。ADR-0160 / REQ-220)は比較の窓の AI の面の「↗」で開く。560×800(最小 360×480)、文字を選べる。
  比較の画面のときだけ WebSession に渡す(--batch・--settings では開かない)
- Windows の exe で動いているときだけ、start に storage_path として設定のフォルダーの webview を渡す(ADR-0186 決定2。
  WebView2 の作業のデータを、展開したフォルダーの外に作らない)。private_mode は渡さない(既定の真のまま)
- start が戻った後は、ほかの非デーモンのスレッドを合わせて最大2秒待ってから os._exit で終わる(REQ-111。pywebview の
  js_api の呼び出しのスレッドが、窓が閉じた後の evaluate_js で待ち続け、Python の終了処理を止めることがあるため)

webview は run() の中で読む(このモジュールは pywebview 無しでも読める。テストは偽物に差し替える)。
"""

import os
import platform
import sys
import threading
import time
from collections.abc import Callable
from importlib import resources
from pathlib import Path
from typing import Any

from driftpilot.gui_web.bridge import Api
from driftpilot.gui_web.compare_api import CompareApi
from driftpilot.gui_web.session import WebSession
from driftpilot.gui_web.settings_api import SettingsApi
from driftpilot.llm_settings import portable_settings_dir
from driftpilot.messages import COMPARE_TITLE, SETTINGS_TITLE, gui_web_start_failed, webview2_missing

TITLE = "DriftPilot"
SIZE = (1180, 860)
PAGE = "static/sample.html"
# 比較の画面(REQ-105)。窓の題・大きさ・最小の大きさは wx の比較画面と同じ
COMPARE_SIZE = (1180, 860)
COMPARE_MIN_SIZE = (800, 600)
COMPARE_PAGE = "static/compare.html"
# 設定の画面(REQ-104)。窓の題は wx の設定画面と同じ SETTINGS_TITLE
SETTINGS_SIZE = (960, 760)
SETTINGS_PAGE = "static/settings.html"
# 一括比較の画面(REQ-106)。窓の題(BATCH_TITLE)・大きさ・最小の大きさは wx の一括比較画面と同じ
BATCH_SIZE = (1180, 900)
BATCH_MIN_SIZE = (800, 640)
BATCH_PAGE = "static/batch.html"
# AI の窓(ADR-0160 決定2 / REQ-220)。比較の窓の AI の面の「↗」で開く。窓の題は AI_PANE_TITLE。位置と大きさは保存しない
AI_PANE_SIZE = (560, 800)
AI_PANE_MIN_SIZE = (360, 480)
AI_PANE_PAGE = "static/ai_pane.html"
# Windows の WebView2 の表示部品の名前(webview.renderer)。これ以外は WebView2 が無いとみなす
WEBVIEW2 = "edgechromium"
# Windows の exe のときに WebView2 の作業のデータを置く、設定のフォルダーの中のフォルダー(ADR-0186 決定2)
WEBVIEW_STORAGE_DIR = "webview"
# start が戻った後に、ほかの非デーモンのスレッドを待つ時間の合計の上限(秒。REQ-111)と、プロセスを終わらせる関数。
# どちらもテストで差し替える(tests/conftest.py で _exit を何もしない関数にする)
EXIT_WAIT_SECONDS = 2.0
_exit = os._exit


def page_path(page: str = PAGE) -> Path:
    return Path(str(resources.files("driftpilot.gui_web").joinpath(page))).resolve()


def page_url(page: str = PAGE) -> str:
    """ページの file:// の URL(省くと見本のページ)。"""
    return page_path(page).as_uri()


def _primary(screens: list[Any]) -> Any | None:
    """主の画面(WinForms の Screen.Primary)。分からなければ先頭。"""
    for screen in screens:
        if getattr(getattr(screen, "frame", None), "Primary", False) is True:
            return screen
    return screens[0] if screens else None


def work_area(screen: Any, system: str | None = None) -> tuple[int, int, int, int] | None:
    """画面の作業領域 (x, y, 幅, 高さ)。Windows で frame が X・Y・Width・Height を持つときだけ。取れなければ None。"""
    if (system or platform.system()) != "Windows" or screen is None:
        return None
    frame = getattr(screen, "frame", None)
    try:
        x, y, width, height = int(frame.X), int(frame.Y), int(frame.Width), int(frame.Height)
    except (AttributeError, TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    return x, y, width, height


def window_geometry(screens: list[Any], system: str | None = None, size: tuple[int, int] = SIZE) -> dict[str, int]:
    """create_window に渡す大きさと位置。作業領域が取れれば、幅と高さをその中に収めて中央に置く。

    単位は pywebview の Screen と同じ論理の大きさ(pywebview 自身の中央寄せと同じ計算)。
    作業領域が取れなければ大きさだけを返す(位置は pywebview の既定で画面の中央)。
    """
    width, height = size
    area = work_area(_primary(screens), system)
    if area is None:
        return {"width": width, "height": height}
    x, y, area_width, area_height = area
    width, height = min(width, area_width), min(height, area_height)
    return {"width": width, "height": height, "x": x + (area_width - width) // 2, "y": y + (area_height - height) // 2}


def uses_legacy_engine(system: str, renderer: str | None) -> bool:
    """Windows で WebView2 ではない表示部品(WebView2 Runtime が無い)になったか。"""
    return system == "Windows" and renderer != WEBVIEW2


# ドロップの印の要素(static/compare.html・batch.html の <div id="drop-anchor" hidden>。REQ-205 / ADR-0147 決定2)
DROP_ANCHOR = "#drop-anchor"


def watch_drop(window: Any, on_drop: Callable[[Any], None]) -> None:
    """比較の窓と一括比較の窓の印の要素(#drop-anchor)の drop を Python の側で受ける(REQ-110・REQ-115・REQ-205 / ADR-0147)。

    窓の読み込みの後(window.events.loaded)に呼ぶ。document の事象には登録しない(pywebview は事象のたびに事象の物を
    文字にするので、document に登録すると画面全体が文字になる)。dragover と document の drop は static/js/drop.js が
    既定の動きと親への伝わりを止め、ファイルの一覧だけを持つ新しい事象を印の要素へ送る。その事象は作り物なので、
    ここでは既定の動きを止めず、on_drop をそのまま登録する。pywebview は drop の事象の dataTransfer.files に
    pywebviewFullPath を足し、事象ごとに別のスレッドで on_drop を呼ぶ。

    印の要素が取れなければ登録しない(窓を落とさない)。DOM は読み込みのたびに登録を空にするので、読み込みのたびに
    登録し直しても二重には呼ばれない。
    """
    anchor = window.dom.get_element(DROP_ANCHOR)
    if anchor is None:
        return
    anchor.events.drop += on_drop


def _finish(code: int) -> None:
    """全部の窓が閉じた後に、プロセスを必ず終わらせる(REQ-111 §2)。

    ① ほかの非デーモンのスレッドを合わせて最大 EXIT_WAIT_SECONDS 秒待つ(保存の途中の呼び出しを終わらせるため)
    ② 標準出力と標準エラーを flush する(窓だけの配る形では None のことがある)
    ③ _exit(code)
    """
    deadline = time.monotonic() + EXIT_WAIT_SECONDS
    current, main = threading.current_thread(), threading.main_thread()
    for thread in threading.enumerate():
        if thread is current or thread is main or thread.daemon or not thread.is_alive():
            continue
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        thread.join(remaining)
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.flush()
        except (OSError, ValueError):  # 閉じた流れ。終わるのを止めない
            pass
    _exit(code)


def run(
    sample: bool = False,
    settings: bool = False,
    batch: bool = False,
    report: Callable[[str], None] | None = None,
) -> int:
    """窓を開き、閉じたら 0。表示部品が使えない・WebView2 が無ければ案内を出して 2。

    案内の文は report に渡す(省くと標準エラー。gui_web.print_error。REQ-107 §4)。

    start が戻った後は _finish でプロセスを終わらせる(REQ-111。_exit を差し替えたテストでは 0 を返す)。

    省くと比較の画面。sample なら見本のページ、settings なら設定の画面だけ、batch なら一括比較の画面だけ
    (設計側の確かめのため。比較の窓が無いので「比較画面で開く」は押せない)。
    """
    import webview

    from driftpilot.gui_web import print_error

    tell = report or print_error
    try:
        screens = list(webview.screens)  # 表示部品の初期化もここで起きる(webview.renderer が決まる)
    except Exception as exc:  # noqa: BLE001 - pythonnet・pyobjc の抜けは様々な例外になる。型名だけを添える
        tell(gui_web_start_failed(type(exc).__name__))
        return 2
    if uses_legacy_engine(platform.system(), webview.renderer):
        tell(webview2_missing())
        return 2
    settings_window = {"url": page_url(SETTINGS_PAGE), **window_geometry(screens, size=SETTINGS_SIZE)}
    batch_window = {
        "url": page_url(BATCH_PAGE),
        "min_size": BATCH_MIN_SIZE,
        "text_select": True,
        **window_geometry(screens, size=BATCH_SIZE),
    }
    ai_window = {
        "url": page_url(AI_PANE_PAGE),
        "min_size": AI_PANE_MIN_SIZE,
        "text_select": True,
        **window_geometry(screens, size=AI_PANE_SIZE),
    }
    if sample:
        webview.create_window(TITLE, url=page_url(PAGE), js_api=Api(), **window_geometry(screens, size=SIZE))
    elif settings:
        api = SettingsApi()
        window = webview.create_window(SETTINGS_TITLE, js_api=api, **settings_window)
        api._attach(window)  # 知らせ(送っている最中)と「閉じる」に使う
    elif batch:
        session = WebSession(
            create_window=webview.create_window,
            settings_window=settings_window,
            batch_window=batch_window,
            watch_drop=watch_drop,
        )
        session.open_batch()  # 比較の窓をつながないので「比較画面で開く」は押せない
    else:
        # 設定の窓と一括比較の窓は、比較の画面の「設定…」「一括比較…」で同じ create_window で開く(start の後でも呼べる)。
        # 一括比較の窓のドロップは、WebSession が窓の読み込みの後に watch_drop で受ける。
        # AI の窓(ADR-0160)も同じ create_window で開く。ドロップは受けない(画面の drop.js が既定の動きを止めるだけ)
        session = WebSession(
            create_window=webview.create_window,
            settings_window=settings_window,
            batch_window=batch_window,
            watch_drop=watch_drop,
            ai_window=ai_window,
        )
        compare = CompareApi(session)
        window = webview.create_window(
            COMPARE_TITLE,
            url=page_url(COMPARE_PAGE),
            js_api=compare,
            min_size=COMPARE_MIN_SIZE,
            text_select=True,
            **window_geometry(screens, size=COMPARE_SIZE),
        )
        session.attach_compare(compare, window)  # 知らせ・閉じる・ファイルのダイアログと、閉じたら設定・一括比較の窓も閉じる
        # ファイルのドロップは、読み込みの後に Python の側で受ける(画面のスクリプトからパスを受け取る口は作らない)
        window.events.loaded += lambda: watch_drop(window, compare._on_drop)
    try:
        # gui= と http_server= は渡さない(既定のまま)。Windows の exe のときだけ、WebView2 の作業のデータを
        # 設定のフォルダーの webview の中に置く(ADR-0186 決定2)。private_mode は渡さない(既定の真のまま)。
        # フォルダーが作れないときは pywebview が WebViewException を出し、下の「画面を開けない」の扱いに乗る
        portable = portable_settings_dir()
        if portable is None:
            webview.start()
        else:
            webview.start(storage_path=str(portable / WEBVIEW_STORAGE_DIR))
    except webview.WebViewException as exc:
        tell(gui_web_start_failed(type(exc).__name__))
        return 2
    _finish(0)
    return 0
