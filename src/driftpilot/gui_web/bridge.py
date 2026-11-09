"""Python と画面のやりとりの形(ADR-0074 決定3 / REQ-103 §4)。REQ-104 以降はここに足していく。

- 画面から Python: create_window の js_api に渡す Api のメソッドだけ。返すのは JSON にできる値だけで、
  成功は {"ok": true, ...}、失敗は {"ok": false, "message": <messages.py の文>}。例外の文面・値は画面に出さない
- Python から画面: notify(window, event) で window.driftpilot.receive(event) を呼ぶ形だけ。
  event は {"type": <種類>, ...}。式は json.dumps で逃がした値を埋めるだけで、文字を組み立てない

pywebview は読まない(画面を開かずにテストする)。表示部品の名前は、読み込み済みの webview からだけ取る。
pywebview は js_api の _ で始まらない属性をたどって画面に出すので、内部の属性とメソッドは _ で始める。
"""

import json
import platform
import sys
from collections.abc import Callable
from typing import Any

from driftpilot import __version__
from driftpilot.messages import GUI_WEB_TEXTS, gui_web_call_failed, gui_web_unknown_text

# 画面の側で知らせを受ける、決まった1つの関数(static/js/bridge.js が定義する)
RECEIVER = "window.driftpilot.receive"


def _failure(message: str) -> dict[str, Any]:
    return {"ok": False, "message": message}


def _guarded(func: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
    """js_api の1つの呼び出し。想定外の例外は受け止めて、文面を出さない失敗の形にする。"""
    try:
        return func(*args)
    except Exception:  # noqa: BLE001 - どの例外も画面には同じ文で返す(例外の文面・値を出さない)
        return _failure(gui_web_call_failed())


def _renderer() -> str:
    """表示部品の名前(edgechromium・wkwebview など)。webview がまだ読まれていない・決まっていなければ空。"""
    webview = sys.modules.get("webview")
    name = getattr(webview, "renderer", None) if webview is not None else None
    return name if isinstance(name, str) else ""


class Api:
    """画面から呼べるメソッド(window.pywebview.api.<名前>)。"""

    def app_info(self) -> dict[str, Any]:
        """版・Python の版・表示部品の名前。見本のページの上部に出す。"""
        return _guarded(self._app_info)

    def messages(self, keys: Any) -> dict[str, Any]:
        """画面に出す文。keys は鍵の文字の list。1つでも知らない鍵があれば失敗(どれも返さない)。"""
        return _guarded(self._messages, keys)

    def _app_info(self) -> dict[str, Any]:
        return {"ok": True, "driftpilot": __version__, "python": platform.python_version(), "renderer": _renderer()}

    def _messages(self, keys: Any) -> dict[str, Any]:
        if not isinstance(keys, list) or not all(isinstance(key, str) and key in GUI_WEB_TEXTS for key in keys):
            return _failure(gui_web_unknown_text())
        return {"ok": True, "messages": {key: GUI_WEB_TEXTS[key] for key in keys}}


def receive_script(event: dict[str, Any]) -> str:
    """window.driftpilot.receive(<JSON>) の式。event は "type" が文字の dict に限る。

    json.dumps の既定(ensure_ascii=True)で、引用符・改行・</script>・U+2028 なども式を壊さない形に逃がす。
    """
    if not isinstance(event, dict) or not isinstance(event.get("type"), str):
        raise ValueError("event は type が文字の dict にする")
    return f"{RECEIVER}({json.dumps(event)})"


def notify(window: Any, event: dict[str, Any]) -> Any:
    """Python から画面へ知らせる(進み具合など)。window は pywebview の Window。evaluate_js の戻り値を返す。"""
    return window.evaluate_js(receive_script(event))
