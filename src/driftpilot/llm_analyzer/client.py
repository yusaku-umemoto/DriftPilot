"""LLM への接続の口(ADR-0018 決定7、2026-10-16 承認)。テキストを渡してテキストが返る、の1メソッドだけ。

口はこれ以上広げない。プロバイダごとの違いは実装(openai_client.py など)の中に閉じる。
CommandPilot などは自分の実装を作って渡せばよい。

例外は3つだけ。どれも例外の文(str・repr・args)に利用者に見せる文面やプロバイダの応答本文を持たない
(認証エラーの応答本文にはキーの一部が載ることがあるため)。
LLMResponseError だけは、掃除した後のエラーの文と待つ秒を属性 api_error に持つ(ADR-0168 決定1。api_error.py)。
生の本文は持たない。属性は画面と CLI の標準エラーに出すときだけ読み、保存・ログ・LlmAnalysis には入れない。
"""

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:  # 型の注記だけで読む(接続の口を軽いままにする)
    from driftpilot.llm_analyzer.api_error import ApiErrorText


@runtime_checkable
class LLMClient(Protocol):
    def ask(self, prompt: str) -> str: ...

    @property
    def model_name(self) -> str: ...


class LLMError(Exception):
    """接続の実装が送出する例外の基底。"""


class LLMConnectionError(LLMError):
    """通信できない(名前解決・接続・タイムアウト)。"""


class LLMResponseError(LLMError):
    """プロバイダがエラーを返した、または回答の形が違う。status_code は HTTP の状態コード(分かるときだけ)。

    api_error は掃除した後のエラーの文と待つ秒(ADR-0168 決定1。無ければ None)。例外の文には入れない。
    """

    def __init__(self, status_code: int | None = None, *, api_error: "ApiErrorText | None" = None) -> None:
        self.status_code = status_code
        self.api_error = api_error
        super().__init__(f"status_code={status_code}" if status_code is not None else "response error")


class LLMClientUnavailable(LLMError):
    """接続の実装が使えない(任意依存が入っていない)。"""


class StreamStopped(LLMError):
    """on_delta が「読むのをやめる」と伝える(中止。ADR-0157 決定1)。"""


class StreamingClient(Protocol):
    """届いたところから文字を on_delta に渡し、最後に全部の文字を返す(ADR-0157 決定1)。

    on_delta は届いた文字(届いていなければ空の文字)で呼ぶ。StreamStopped を出したら読むのをやめ、そのまま外へ出す。
    全部届いた印が無いまま終われば例外にする(途中の文字を答えにしない)。LLMClient の口は広げず、持つ接続だけが持つ。
    考えの文字(Thinking の途中の文)は、接続が streams_thinking を真で持つときだけ on_thinking に渡す(ADR-0169 決定1)。
    考えは on_delta には渡さない。考えの文字が届いた後も、再試行と ask への戻しをしない。
    """

    def ask_stream(
        self, prompt: str, on_delta: Callable[[str], None], *, on_thinking: Callable[[str], None] | None = None
    ) -> str: ...


def ask_with_deltas(
    client: LLMClient,
    prompt: str,
    on_delta: Callable[[str], None] | None,
    on_thinking: Callable[[str], None] | None = None,
) -> str:
    """on_delta があり、接続が ask_stream を持つときだけ流す。ほかは今の ask。

    on_thinking は、接続の streams_thinking が True のときだけ渡す(持たない外の実装は今の呼び方のまま。ADR-0169 決定1)。
    """
    if on_delta is None:
        return client.ask(prompt)
    stream = getattr(client, "ask_stream", None)
    if stream is None:
        return client.ask(prompt)
    if on_thinking is not None and getattr(client, "streams_thinking", False) is True:
        return stream(prompt, on_delta, on_thinking=on_thinking)
    return stream(prompt, on_delta)
