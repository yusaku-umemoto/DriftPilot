"""AI の窓(static/ai_pane.html)の Python の側(ADR-0160 決定3 / REQ-220 §4)。

状態・門・確かめ・送信の決まりは比較の窓の Python の側(compare_api.CompareApi)が持ち、ここは AI の面の操作だけを渡す薄い口。
入力を変える口(browse・paste・run・swap)や、保存・結果の行・ほかの窓を開く口は持たない。

- どのメソッドも CompareApi の同じ処理を _call_as("ai", …) で呼ぶ。返す view は AI の窓向け(送信前の確認・
  送り先の確認・AI の知らせは、AI の窓が開いている間はこちらにだけ入る。ADR-0160 決定6)。まとめて比べたときも
  全部の組をまとめた送信前の確認は1件で、まとめた確認(組のチェック)は無い(ADR-0152 決定3)
- form は CompareApi が持っている最後の値(_form_copy。AI の窓に比較の設定の欄は無い)
- ai_result() は結果の番号と AI の答えの部分だけを返す(結果の行は渡さない)。接続先の URL・キー・CLI の場所も渡さない
- 想定外の例外は bridge の _guarded と同じ形({"ok": false, "message": <文>})
- pywebview は js_api の _ で始まらない属性をたどって画面に出すので、内部の属性とメソッドは _ で始める

窓は WebSession.open_ai_pane が開き、_attach で渡す。閉じたあとの処理(右の面に戻す)は、窓の closed の知らせから session が行う。
"""

from collections.abc import Callable
from typing import Any

from driftpilot.gui_web.bridge import Api
from driftpilot.gui_web.compare_api import PANE_AI, CompareApi


class AiPaneApi(Api):
    """AI の窓から呼べるメソッド(window.pywebview.api.<名前>)。REQ-103 の app_info・messages も使える。"""

    def __init__(self, compare: CompareApi) -> None:
        self._compare = compare
        self._window: Any = None

    def _attach(self, window: Any) -> None:
        """pywebview の窓(「閉じる」に使う)。WebSession.open_ai_pane が渡す。"""
        self._window = window

    # --- 画面から呼ぶメソッド ---

    def state(self) -> dict[str, Any]:
        """今の状態(AI の窓向けの view)。読むだけ。"""
        return self._compare._call(self._compare._state_for, PANE_AI)

    def ai_result(self) -> dict[str, Any]:
        """結果の番号と AI の答え(llm・llm_blocks・llm_copy)だけ。結果の行は入れない。"""
        return self._compare._call_as(PANE_AI, self._compare._ai_result)

    def send_llm(self, instruction: Any = "") -> dict[str, Any]:
        """「AI で解析」(比較の窓の send_llm と同じ。確かめと送信前の確認も同じ)。"""
        return self._with_form(self._compare._send_llm, instruction)

    def ask_follow_up(self, question: Any, confirm: Any = None) -> dict[str, Any]:
        """「質問する」(比較の窓の ask_follow_up と同じ)。送り先が変わっていれば confirm を返し、「はい」で番号を付けて呼び直す。"""
        return self._with_form(self._compare._ask_follow_up, question, confirm)

    def use_ai_profile(self, index: Any, label: Any) -> dict[str, Any]:
        """「AI プロファイル」を選んだとき(比較の窓と同じく使用中を変える)。"""
        return self._with_form(self._compare._use_profile, index, label, "llm")

    def use_prompt_profile(self, index: Any, label: Any) -> dict[str, Any]:
        """「プロンプト」を選んだとき(比較の窓と同じく使用中を変える)。"""
        return self._with_form(self._compare._use_profile, index, label, "prompt")

    def answer_preview(self, yes: Any) -> dict[str, Any]:
        """送信前の確認の答え。「送信する」(yes が真)のときだけ送る。"""
        return self._with_form(self._compare._answer_preview, yes)

    def cancel(self) -> dict[str, Any]:
        """「中止」(比較の窓と同じ。送った要求は止められないので、届いた答えを捨てる)。"""
        return self._compare._call_as(PANE_AI, self._compare._cancel)

    def set_full_text(self, checked: Any) -> dict[str, Any]:
        """「差分＋全文を送る」を変えたとき(比較の窓と同じ)。"""
        return self._with_form(self._compare._set_full_text, checked)

    def close(self) -> dict[str, Any]:
        """この窓を閉じる。比較の窓の右の面に戻す処理は、窓の closed の知らせから行う。"""
        return self._compare._call_as(PANE_AI, self._close)

    # --- 中で使う ---

    def _with_form(self, func: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        """form を付けて CompareApi の処理を AI の窓として呼ぶ。form は呼ぶ直前に CompareApi の最後の値を写す。"""
        compare = self._compare
        return compare._call_as(PANE_AI, lambda: func(compare._form_copy(), *args))

    def _close(self) -> dict[str, Any]:
        result = self._compare._state_for(PANE_AI)
        window = self._window
        if window is not None:
            window.destroy()
        return result
