"""送り方の細かい値と、本文・URL の組み立て(ADR-0073 決定2〜4。REQ-100 §3・§4)。

プロファイル(llm-profiles.json)の auth_header・max_output_tokens・wire_names・thinking・thinking_param・query を、
接続の実装が送る本文と URL に写す。どの値も、無ければ今と同じ送信になる(既定値は何も足さない)。

決まり(読むときも書くときも、形式に関係なく確かめる。wire_problem):
- 置き換え先(wire_names の max_output_tokens の先)と thinking の送る名前は、予約された名前を使えない
- thinking の送る名前は、送る上限の名前(LIMIT_NAMES)も使えない(上限の値を黙って上書きしないため)
- 問い合わせの値のキー・thinking の送る名前・置き換え先に、キーらしい名前は書けない(キーは保管庫だけ)。
  名前を _ - . と大文字の境目で語に分け、語が KEYLIKE_WORDS のどれかと一致したらキーらしい名前とする
"""

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

CHAT_COMPLETIONS = "chat-completions"
RESPONSES = "responses"
ANTHROPIC_MESSAGES = "anthropic-messages"
# OpenAI 互換の形式(認証の見出し・置き換え・問い合わせの値が意味を持つ)
OPENAI_FORMATS = (CHAT_COMPLETIONS, RESPONSES)

AUTH_BEARER = "bearer"
AUTH_API_KEY = "api-key"
AUTH_HEADERS = (AUTH_BEARER, AUTH_API_KEY)
THINKING_OFF = "off"
THINKING_ON = "on"
THINKING_VALUES = (THINKING_OFF, THINKING_ON)

# 置き換えの元(今の DriftPilot が送る単純な値はこれだけ)と、形式ごとの既定の送信名
MAX_OUTPUT_TOKENS = "max_output_tokens"
DEFAULT_MAX_TOKENS_NAME = {CHAT_COMPLETIONS: "max_completion_tokens", RESPONSES: "max_output_tokens"}
RESERVED_NAMES = frozenset(
    {
        "model",
        "messages",
        "input",
        "instructions",
        "system",
        "stream",
        "tools",
        "tool_choice",
        "response_format",
        "text",
        "reasoning",
    }
)
# 送る上限の名前(形式に関係なく、thinking の送る名前には使えない。置き換え先には使える。REQ-108 §4)
LIMIT_NAMES = frozenset({"max_tokens", "max_completion_tokens", "max_output_tokens"})
KEYLIKE_WORDS = frozenset({"key", "token", "secret", "password", "authorization", "apikey"})
# 語の分け方: 大文字の続きの後に大文字+小文字が来たらそこで切る(APIKey → API・Key)
_WORD = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|[0-9]+|[^A-Za-z0-9]+")


@dataclass(frozen=True)
class ThinkingParam:
    """thinking を送る名前と、ON の値・OFF の値(任意の JSON の値)。"""

    name: str
    on: Any
    off: Any

    def __hash__(self) -> int:  # on・off は表や配列のことがある(JSON の文字で比べる)
        return hash((self.name, _canonical(self.on), _canonical(self.off)))


@dataclass(frozen=True)
class WireOptions:
    """送る中身の差を吸収する値。既定値(WireOptions())は今と同じ送信。

    max_tokens_name は wire_names の max_output_tokens の置き換え先(無ければ形式ごとの既定の名前)。
    query は接続先の URL の後ろに付ける (キー, 値) の並び。
    """

    max_output_tokens: int | None = None
    max_tokens_name: str | None = None
    thinking: str = THINKING_OFF
    thinking_param: ThinkingParam | None = None
    query: tuple[tuple[str, str], ...] = ()


DEFAULT_WIRE = WireOptions()


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_text(value: Any, *, indent: int | None = None) -> str:
    """thinking の ON の値・OFF の値(と custom-http-json の本文。indent で字下げ)を、画面の欄に出す JSON の文字にする。"""
    return json.dumps(value, ensure_ascii=False, indent=indent)


def _reject_constant(name: str) -> Any:
    raise ValueError(name)  # NaN・Infinity は JSON の値ではない


def parse_json_text(text: str) -> tuple[bool, Any]:
    """画面の欄の JSON の文字を値にする。(読めたか, 値)。"""
    try:
        return True, json.loads(text, parse_constant=_reject_constant)
    except ValueError:
        return False, None


def extra_body(api_format: str, wire: WireOptions) -> dict[str, Any]:
    """本文の一番上の階層に足す値。上限は OpenAI 互換の2形式だけ(Claude は max_tokens に使う)。"""
    body: dict[str, Any] = {}
    if api_format in OPENAI_FORMATS and wire.max_output_tokens is not None:
        body[wire.max_tokens_name or DEFAULT_MAX_TOKENS_NAME[api_format]] = wire.max_output_tokens
    param = wire.thinking_param
    if param is not None:
        body[param.name] = param.on if wire.thinking == THINKING_ON else param.off
    return body


def with_query(url: str, query: tuple[tuple[str, str], ...]) -> str:
    """URL に ?キー=値&… を付ける(無ければそのまま)。"""
    return f"{url}?{urlencode(list(query))}" if query else url


def chat_text(data: Any) -> str | None:
    """chat.completions の応答の choices[0].message.content。形が違えば None。"""
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    return content if isinstance(content, str) else None


def responses_text(data: Any) -> str | None:
    """responses の応答の output のうち、type が message の content の output_text をつなげる(openai の output_text と同じ)。

    1つも無ければ None。
    """
    output = data.get("output") if isinstance(data, dict) else None
    if not isinstance(output, list):
        return None
    texts = [
        part["text"]
        for item in output
        if isinstance(item, dict) and item.get("type") == "message" and isinstance(item.get("content"), list)
        for part in item["content"]
        if isinstance(part, dict) and part.get("type") == "output_text" and isinstance(part.get("text"), str)
    ]
    return "".join(texts) if texts else None


def chat_stream_event(data: Any) -> tuple[str, str | None]:
    """chat.completions を流したときの塊1つから (文字, 終わりの種類)(ADR-0157 決定3)。

    文字は choices[0].delta.content。finish_reason が文字なら "done"。error があれば "error"。
    choices が空の塊(使った量だけの塊など)は ("", None)。reasoning_content は読まない。
    """
    if not isinstance(data, dict):
        return "", None
    if data.get("error") is not None:
        return "", "error"
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return "", None
    choice = choices[0]
    delta = choice.get("delta")
    content = delta.get("content") if isinstance(delta, dict) else None
    text = content if isinstance(content, str) else ""
    return text, "done" if isinstance(choice.get("finish_reason"), str) else None


def responses_stream_event(data: Any) -> tuple[str, str | None]:
    """responses を流したときの事象1つから (文字, 終わりの種類)(ADR-0157 決定3)。

    文字は response.output_text.delta の delta。response.completed で "done"。response.failed・error で "error"。
    """
    if not isinstance(data, dict):
        return "", None
    kind = data.get("type")
    if kind == "response.output_text.delta":
        delta = data.get("delta")
        return (delta if isinstance(delta, str) else ""), None
    if kind == "response.completed":
        return "", "done"
    if kind in ("response.failed", "error"):
        return "", "error"
    return "", None


def chat_thinking_event(data: Any) -> str:
    """chat.completions を流したときの塊1つから考えの文字(ADR-0169 決定2)。

    choices[0].delta.reasoning_content、それが文字でなければ choices[0].delta.reasoning。どちらも文字でなければ ""。
    答えの文字(chat_stream_event)とは別に読む(答えに考えを混ぜない)。
    """
    if not isinstance(data, dict):
        return ""
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return ""
    delta = choices[0].get("delta")
    if not isinstance(delta, dict):
        return ""
    for name in ("reasoning_content", "reasoning"):
        value = delta.get(name)
        if isinstance(value, str):
            return value
    return ""


RESPONSES_THINKING_EVENTS = frozenset({"response.reasoning_summary_text.delta", "response.reasoning_text.delta"})


def responses_thinking_event(data: Any) -> str:
    """responses を流したときの事象1つから考えの文字(ADR-0169 決定2)。

    type が response.reasoning_summary_text.delta か response.reasoning_text.delta の delta(文字のときだけ)。ほかは ""。
    """
    if not isinstance(data, dict):
        return ""
    kind = data.get("type")
    # 種別が文字でない事象(配列・オブジェクトなど)は、集合と照合する前に外す(ハッシュできない値で落とさない)
    if not isinstance(kind, str) or kind not in RESPONSES_THINKING_EVENTS:
        return ""
    delta = data.get("delta")
    return delta if isinstance(delta, str) else ""


def name_words(name: str) -> list[str]:
    """名前を _ - . と大文字の境目で語に分ける(apiKey → api・Key、APIKey → API・Key、x-api-key → x・api・key)。"""
    return [word for part in re.split(r"[_\-.]", name) for word in _WORD.findall(part)]


def is_keylike(name: str) -> bool:
    """キーらしい名前か(語が KEYLIKE_WORDS のどれかと一致する。大文字・小文字を問わない)。"""
    return any(word.casefold() in KEYLIKE_WORDS for word in name_words(name))


def wire_problem(wire: WireOptions) -> tuple[str, str] | None:
    """決まりに反する欄の名前("wire_names" / "thinking_param" / "query")と種類("reserved" / "keylike")。無ければ None。"""
    param = wire.thinking_param
    target = wire.max_tokens_name
    if target is not None:
        if target in RESERVED_NAMES or (param is not None and target == param.name):
            return "wire_names", "reserved"
        if is_keylike(target):
            return "wire_names", "keylike"
    if param is not None:
        if param.name in RESERVED_NAMES or param.name in LIMIT_NAMES:
            return "thinking_param", "reserved"
        if is_keylike(param.name):
            return "thinking_param", "keylike"
    if any(is_keylike(key) for key, _ in wire.query):
        return "query", "keylike"
    return None
