"""AI 接続テスト(REQ-35 / ADR-0034)。決まった短い文を1回だけ送り、結果を分類する。

設定は読まない(送る準備は llm_settings.prepare_connection_check)。入口(CLI と GUI)の外に置き、両方から呼ぶ。
送るのは CONNECTION_CHECK_PROMPT だけ。利用者のデータ・設定・キーは含めない(ADR-0034 決定1)。
応答の本文は見ない・持たない(ADR-0034 決定3)。再試行はしない(client は max_retries=0 で作る。ADR-0034 決定2)。
custom-http-json の client(ask_with_shape を持つ)だけ、応答の形(キーの名前と値の型)を返す。値は持たない(REQ-101 §4.2)。
"""

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol, runtime_checkable

from driftpilot.llm_analyzer.client import (
    LLMClient,
    LLMClientUnavailable,
    LLMConnectionError,
    LLMError,
    LLMResponseError,
)
from driftpilot.llm_analyzer.custom_http import ShapeNode

CONNECTION_CHECK_PROMPT = "This is a connection test. Reply with the single word OK."

CheckKind = Literal[
    "ok",
    "auth",
    "not_found",
    "rate_limited",
    "connection",
    "error",
    "no_key",
    "no_model",
    "unavailable",
    "placeholder_base_url",
    "cli_not_found",
]
# 送らずに終わった分類(placeholder_base_url は接続先に <リソース名> などの目印が残っているとき。REQ-100 §5。
# cli_not_found は CLI の形式で CLI のプログラムが見つからないとき。REQ-102)
NOT_SENT: frozenset[str] = frozenset({"no_key", "no_model", "unavailable", "placeholder_base_url", "cli_not_found"})


@dataclass(frozen=True)
class ConnectionCheckResult:
    """接続テストの結果。応答の本文は入れない。status_code は error のときだけ(分かれば)。

    response_shape は custom-http-json で応答の JSON が読めたときの形の木(名前と型だけ)。ほかは None。
    """

    kind: CheckKind
    status_code: int | None = None
    response_shape: ShapeNode | None = None

    @property
    def sent(self) -> bool:
        return self.kind not in NOT_SENT

    @property
    def ok(self) -> bool:
        return self.kind == "ok"


@runtime_checkable
class ShapedClient(Protocol):
    """接続テストで応答の形の木も返せる client(custom-http-json)。"""

    def ask_with_shape(self, prompt: str) -> tuple[str | LLMError, ShapeNode | None]: ...


def _from_status(status_code: int | None) -> ConnectionCheckResult:
    if status_code in (401, 403):
        return ConnectionCheckResult("auth")
    if status_code == 404:
        return ConnectionCheckResult("not_found")
    if status_code == 429:
        return ConnectionCheckResult("rate_limited")
    return ConnectionCheckResult("error", status_code)


def _classified(send: Callable[[], object]) -> ConnectionCheckResult:
    try:
        send()
    except LLMResponseError as exc:
        return _from_status(exc.status_code)
    except LLMConnectionError:
        return ConnectionCheckResult("connection")
    except LLMClientUnavailable:
        return ConnectionCheckResult("unavailable")
    return ConnectionCheckResult("ok")


def send_connection_check(client: LLMClient) -> ConnectionCheckResult:
    """CONNECTION_CHECK_PROMPT を1回だけ送る。応答は捨てる(中身は見ない)。繰り返さない。"""
    if not isinstance(client, ShapedClient):
        return _classified(lambda: client.ask(CONNECTION_CHECK_PROMPT))
    shape: ShapeNode | None = None

    def send() -> None:
        nonlocal shape
        answer, shape = client.ask_with_shape(CONNECTION_CHECK_PROMPT)
        if isinstance(answer, LLMError):
            raise answer

    result = _classified(send)
    return result if shape is None else replace(result, response_shape=shape)
