"""送信前の確認で承認した内容だけを送る(ADR-0018 決定6・ADR-0031 決定2)。

承認は「この内容を送ってよい」という承認である。送ろうとした内容が承認した内容と違えば、送らない。
比較画面(1件)と一括比較(組ごと)の両方で使う。analyze は PromptNotApproved を changed_after_approval として記録する。
"""

from collections.abc import Callable

from driftpilot.llm_analyzer.client import LLMClient, LLMError, ask_with_deltas


class PromptNotApproved(LLMError):
    """プレビューで承認した内容と、送ろうとした内容が違う。送らない。"""


class ApprovedPromptClient:
    """client を包み、プレビューで承認した内容だけを送る。違えば送らずに PromptNotApproved。"""

    def __init__(self, client: LLMClient, approved: str) -> None:
        self._client = client
        self._approved = approved

    @property
    def model_name(self) -> str:
        return self._client.model_name

    def ask(self, prompt: str) -> str:
        if prompt != self._approved:
            raise PromptNotApproved()
        return self._client.ask(prompt)

    @property
    def streams_thinking(self) -> bool:
        """中の接続が考えの文字を渡せるときだけ真(ADR-0169 決定1)。"""
        return getattr(self._client, "streams_thinking", False) is True

    def ask_stream(
        self, prompt: str, on_delta: Callable[[str], None], *, on_thinking: Callable[[str], None] | None = None
    ) -> str:
        """流すときも同じに照合する。違えば送らず、on_delta も on_thinking も呼ばない(ADR-0157 決定1・ADR-0169)。"""
        if prompt != self._approved:
            raise PromptNotApproved()
        return ask_with_deltas(self._client, prompt, on_delta, on_thinking)
