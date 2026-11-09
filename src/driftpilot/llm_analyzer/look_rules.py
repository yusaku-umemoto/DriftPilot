"""送る直前に答えの見え方の決まり(LOOK_RULES)を足す(ADR-0150 決定1〜3)。

build_request が既定の作り方で作った本物の client だけを包む(client_factory を渡したときは包まない。
テストの偽物と接続テスト)。承認の照合(approval.ApprovedPromptClient)はこの外側にあるので、照合するのは足す前の文。
送信前の確認・--llm-dry-run・結果の payload は足す前の文のまま(足すのはこの包みの中だけ)。
画面から直接は呼ばない。
"""

from collections.abc import Callable
from typing import Any

from driftpilot.llm_analyzer.client import LLMClient, ask_with_deltas
from driftpilot.llm_analyzer.prompt import with_look_rules


class LookRulesClient:
    """本物の client を包み、送る文の後ろに LOOK_RULES を足す。"""

    def __init__(self, client: LLMClient) -> None:
        self.inner = client

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    def ask(self, prompt: str) -> str:
        return self.inner.ask(with_look_rules(prompt))

    @property
    def streams_thinking(self) -> bool:
        # 中の接続が考えの文字を渡せるときだけ真(__getattr__ で素通りさせず、True だけを真にする。ADR-0169 決定1)
        return getattr(self.inner, "streams_thinking", False) is True

    def ask_stream(
        self, prompt: str, on_delta: Callable[[str], None], *, on_thinking: Callable[[str], None] | None = None
    ) -> str:
        # 流すときも同じに足す(__getattr__ で inner の ask_stream へ素通りさせない。ADR-0157)
        return ask_with_deltas(self.inner, with_look_rules(prompt), on_delta, on_thinking)

    def __getattr__(self, name: str) -> Any:
        # inner がまだ無いとき(複製の途中など)に自分を呼び返し続けないため
        if name == "inner":
            raise AttributeError(name)
        return getattr(self.inner, name)
