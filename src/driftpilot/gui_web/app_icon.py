"""動いている間のアイコン(ADR-0246 / REQ-373)。

アプリのファイルのアイコン(Mac の DriftPilot.icns・Windows の DriftPilot.ico)は、ピクセル風のフォーマル版で固定する。
設定の「キャラクターを表示する」がオンのときだけ、動いている間のアイコン(Mac の Dock・Windows の窓の左上とタスクバー)を
キャラクター版にする。画像は static/character/ の 4 つ(app-icon-character.png・.ico、app-icon-business.png・.ico)。

- 起動のとき: AppIcon.startup() が設定を読み、webview.start(icon=) に渡すパスを返す(app.run)
- 保存のとき: AppIcon.follow(windows) が設定を読み直し、キャラクターのオン・オフが変わったときだけ切り替える
  (WebSession.settings_saved から。--settings だけのときは SettingsApi の保存の後の口から)
- OS ごとの呼び出しはこのモジュールの中に閉じる(AppKit・System は呼ぶときにだけ読む。テストは mac_setter・windows_setter を偽物に替える)
  - Mac: 主の糸(PyObjCTools.AppHelper.callAfter)で NSApplication.setApplicationIconImage_ (pywebview の cocoa と同じ形)
  - Windows: 開いている窓の native(Form)の Icon を、UI の糸(Form.Invoke)で System.Drawing.Icon に替える(pywebview の winforms と同じ形)
  - ほかの OS・呼び出しの失敗: 何もしない(知らせない。例外を外へ出さない)

後から開く窓のアイコン: pywebview は窓を 1 つ作るたびに、webview._state['icon'](start の icon= を覚えた物)を Dock・窓のアイコンに
当て直す(cocoa.py の BrowserView.__init__、winforms.py の BrowserForm)。そのため、保存の後に開いた窓が起動のときのアイコンに
戻らないよう、切り替えるときにこの記録も新しいパスに書き換える。_state は pywebview が公開していない名前なので、次の点に頼っている:
  - 窓を作るときに _state['icon'] を読むこと
  - _state という名前と 'icon' の鍵が変わっていないこと
pywebview の版を上げるときは、この 2 点を確かめる(tests/test_req373_app_icon.py が本物の pywebview の _state と 'icon' の有無を確かめる)。
名前が無い版では、書き換えを飛ばす(例外は出さない)。

日本語の文は持たない(利用者への知らせは出さない)。
"""

import platform
import threading
from collections.abc import Callable, MutableMapping, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

# static/character/ の下の画像の名前
CHARACTER_PNG = "static/character/app-icon-character.png"
CHARACTER_ICO = "static/character/app-icon-character.ico"
BUSINESS_PNG = "static/character/app-icon-business.png"
BUSINESS_ICO = "static/character/app-icon-business.ico"

MAC = "Darwin"
WINDOWS = "Windows"


def icon_folder() -> Path:
    """画像を探すフォルダー(driftpilot.gui_web のパッケージのフォルダー。テストで差し替える)。"""
    return Path(str(resources.files("driftpilot.gui_web"))).resolve()


def icon_path(character: bool, system: str | None = None, folder: Path | None = None) -> Path | None:
    """webview.start(icon=) に渡す画像のパス。Mac は PNG、Windows は .ico。ほかの OS・ファイルが無いときは None。"""
    system = system or platform.system()
    if system == MAC:
        name = CHARACTER_PNG if character else BUSINESS_PNG
    elif system == WINDOWS:
        name = CHARACTER_ICO if character else BUSINESS_ICO
    else:
        return None
    path = (folder if folder is not None else icon_folder()) / name
    return path if path.is_file() else None


def _default_character() -> bool:
    from driftpilot import llm_settings

    return llm_settings.character_enabled()


def _set_mac_icon(path: str) -> None:
    """Dock のアイコンを替える(主の糸で。失敗は捨てる)。"""
    try:
        import AppKit
        from PyObjCTools import AppHelper
    except Exception:  # noqa: BLE001 - pyobjc が無い
        return

    def apply() -> None:
        try:
            image = AppKit.NSImage.alloc().initByReferencingFile_(path)
            AppKit.NSApplication.sharedApplication().setApplicationIconImage_(image)
        except Exception:  # noqa: BLE001 - 主の糸の中の失敗は捨てる
            pass

    AppHelper.callAfter(apply)


def _set_windows_icon(path: str, windows: Sequence[Any]) -> None:
    """開いている窓の Form.Icon を替える(UI の糸で。窓ごとに失敗は捨てる)。"""
    try:
        from System import Func, Type  # type: ignore[import-not-found]
        from System.Drawing import Icon  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - pythonnet が無い
        return
    for window in windows:
        try:
            form = getattr(window, "native", None)
            if form is None:
                continue

            def apply(form: Any = form) -> None:
                form.Icon = Icon(path)

            if form.InvokeRequired:
                form.Invoke(Func[Type](apply))
            else:
                apply()
        except Exception:  # noqa: BLE001 - 閉じかけの窓は飛ばす
            continue


class AppIcon:
    """動いている間のアイコンの選び方と切り替え。

    system は OS の名前(省くと platform.system())。character_of はキャラクターのオン・オフを読む関数(省くと
    llm_settings.character_enabled)。webview_module は後から開く窓のために _state['icon'] を書き換える相手(省くと書き換えない)。
    mac_setter(path)・windows_setter(path, windows) は OS の呼び出し(省くと本物。テストは偽物を渡す)。
    folder は画像を探すフォルダー(省くと icon_folder())。
    """

    def __init__(
        self,
        *,
        system: str | None = None,
        character_of: Callable[[], bool] | None = None,
        webview_module: Any = None,
        mac_setter: Callable[[str], None] | None = None,
        windows_setter: Callable[[str, Sequence[Any]], None] | None = None,
        folder: Path | None = None,
    ) -> None:
        self._system = system or platform.system()
        self._character_of = character_of or _default_character
        self._webview = webview_module
        self._mac_setter = mac_setter or _set_mac_icon
        self._windows_setter = windows_setter or _set_windows_icon
        self._folder = folder
        self._lock = threading.Lock()
        self._applied: bool | None = None  # 最後に当てたキャラクターのオン・オフ

    def _path(self, character: bool) -> Path | None:
        return icon_path(character, self._system, self._folder if self._folder is not None else icon_folder())

    def startup(self) -> str | None:
        """今の設定を読んで覚え、webview.start(icon=) に渡すパスを返す(無ければ None)。"""
        character = bool(self._character_of())
        with self._lock:
            self._applied = character
        path = self._path(character)
        return str(path) if path is not None else None

    def follow(self, windows: Sequence[Any] = ()) -> None:
        """設定を読み直し、覚えた値から変わっていたときだけ、動いている間のアイコンを切り替える。例外は外へ出さない。"""
        try:
            character = bool(self._character_of())
        except Exception:  # noqa: BLE001
            return
        with self._lock:
            if character == self._applied:
                return
            self._applied = character
        path = self._path(character)
        if path is None:  # 画像が無い・対象の OS ではない: そのまま(ピクセル風のまま)
            return
        text = str(path)
        # 後から開く窓が起動のときのアイコンに戻らないよう、pywebview の記録も書き換える(モジュールの docstring)
        try:
            state = getattr(self._webview, "_state", None)
            # 本物の _state は dict ではなく UserDict の仲間(webview.util.ImmutableDict。既にある鍵の書き換えだけできる)
            if isinstance(state, MutableMapping) and "icon" in state:
                state["icon"] = text
        except Exception:  # noqa: BLE001
            pass
        try:
            if self._system == MAC:
                self._mac_setter(text)
            elif self._system == WINDOWS:
                self._windows_setter(text, list(windows))
        except Exception:  # noqa: BLE001
            pass
