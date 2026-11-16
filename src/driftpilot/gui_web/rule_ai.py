"""単純比較の設定の「AI に式を頼む…」の確認と送信の入れ物(REQ-314 / ADR-0218 決定8)。比較の窓と設定の窓で共通。

窓ごとに RuleAi を 1 つ持つ(compare_api.CompareApi・settings_api.SettingsApi)。窓の状態(比較の門・保存)には触らない。

- ask(lines, description, kind): 設定を読み(押すたびに読む)、貼った文と説明を確かめ、貼った行を変更後の全文と同じ判定
  (build_after_text。伏せる・判定できない行を除く・設定の秘密の語)に通して、送る前の確認の中身を返す。送らない
- answer(number, yes): 「送信する」(yes が True)のときだけ、確認に出した文をスレッドで 1 回だけ送る。答えが届いたら、
  そのスレッドで読み取りと式の確かめをして、画面へ {"type": "rule_ai.done", "id": 番号, "result": …} を知らせる
- close(number)・closed(): 小さな窓・窓を閉じたとき。待っている確認を捨て、送信中なら取り消しの印を付けて元の行を捨てる。
  取り消した後・番号が違う答えは捨てて知らせない
- 自動送信の設定は読まない(オンでも確認を出す)。毎回付ける前提と見え方の決まりは付けない(plan_llm の見え方の決まりを足す引数を偽にする)。
  確認に出した文と送る文は 1 字も違わない。自動で送り直さない
- 元の行(伏せる前)は、確認の間と答えを待つ間だけ持ち、式の確かめにだけ使う。送らない・保存しない・知らせの失敗の文に入れない。
  答えを受けた後と、送らないとき・閉じたときに捨てる
- 答えの文・re.error の文・貼った行・説明は、ログにも例外の文にも失敗の知らせにも入れない(理由の種類だけ)
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from driftpilot.gui_web.bridge import _failure
from driftpilot.llm_analyzer import LLMClient, LLMConnectionError, LLMResponseError, normalize_newlines
from driftpilot.llm_analyzer.follow_up import MAX_PROMPT_CHARS, check_question, prompt_too_long
from driftpilot.llm_analyzer.masking import Masker, has_unseen_char, normalize_words
from driftpilot.llm_analyzer.payload import build_after_text
from driftpilot.llm_analyzer.rule_suggestion import KINDS, build_rule_prompt, check_rule, read_rule_answer
from driftpilot.llm_settings import SettingsError, display_model, load_settings
from driftpilot.messages import (
    RULE_AI_ASKING,
    RULE_AI_DESCRIPTION_REFUSED,
    RULE_AI_DONT_SEND,
    RULE_AI_NO_LINES,
    RULE_AI_NO_SENDABLE,
    RULE_AI_NOT_COMPARED,
    RULE_AI_NOTE,
    RULE_AI_PROMPT_TITLE,
    RULE_AI_SEND,
    RULE_AI_TITLE,
    RULE_AI_UNSEEN,
    gui_web_call_failed,
    rule_ai_destination,
    rule_ai_failed,
    rule_ai_match_count,
    rule_ai_omitted,
    rule_ai_problem,
    rule_ai_prompt_too_long,
    rule_ai_too_many_chars,
    rule_ai_too_many_lines,
    rule_ai_unavailable,
    rule_ai_unreadable,
)
from driftpilot.screen_model.batch_model import effective_base_url
from driftpilot.screen_model.model import plan_llm
from driftpilot.screen_model.settings_model import destination_of

# Python から画面への知らせの種類(static/js/raw_options.js が受ける)
DONE_EVENT = "rule_ai.done"
# 貼る行の上限(超えたら一部だけ送ることはせず、送らずに知らせる)
MAX_PASTED_LINES = 100
MAX_PASTED_CHARS = 20_000
MAX_DESCRIPTION_CHARS = 500


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass
class _Pending:
    """送る前の確認を待っているもの。送る文・client・元の行を持つ(送る・送らないの後に捨てる)。"""

    number: int
    prompt: str
    client: LLMClient
    kind: str
    lines: tuple[str, ...]


class _Asking:
    """AI に聞いている間のスレッド 1 本。元の行を持つ(答えを受けた後・取り消したときに空にする)。"""

    def __init__(self, number: int, kind: str, lines: tuple[str, ...]) -> None:
        self.number = number
        self.kind = kind
        self.lines = lines
        self.cancelled = False
        self.thread: threading.Thread | None = None


class RuleAi:
    """「AI に式を頼む…」の確認と送信。where は設定の読み元(cwd・env・platform・home)、client_factory は client を作る関数(テストで偽物)、
    notify は画面への知らせの口(窓が無い・閉じた後の失敗は呼ぶ側が握りつぶす)。"""

    def __init__(
        self,
        where: Mapping[str, Any],
        client_factory: Callable[..., LLMClient] | None,
        notify: Callable[[dict[str, Any]], Any],
    ) -> None:
        self._where = where
        self._client_factory = client_factory
        self._notify = notify
        self._lock = threading.RLock()  # この入れ物だけのロック(窓の状態のロックは使わない)
        self._serial = 0
        self._pending: _Pending | None = None
        self._asking: _Asking | None = None
        self._thread: threading.Thread | None = None  # 最後に始めた送信のスレッド(join が待つ。取り消した後も持つ)
        self._closed = False

    # --- 画面から(窓の公開のメソッドが _guarded で呼ぶ) ---

    def ask(self, lines: Any, description: Any, kind: Any) -> dict[str, Any]:
        """送る前の確認の中身を返す(送らない)。送れないときは理由の文だけ。形の違う引数は不正な呼び出し。"""
        if not isinstance(lines, str) or not isinstance(description, str) or kind not in KINDS:
            return _failure(gui_web_call_failed())
        with self._lock:
            if self._closed:
                return self._ok(None)
            self._drop()  # 新しい確認は、前の確認と送信を取り消す
            refused, pending, view = self._prepare(lines, description, kind)
            if refused is not None:
                return self._ok({"refused": refused})
            assert pending is not None and view is not None
            self._pending = pending
            return self._ok({"confirm": view})

    def answer(self, number: Any, yes: Any) -> dict[str, Any]:
        """確認の答え。待っている確認と同じ番号のときだけ受け、1 回答えたら消す。yes が True のときだけ 1 回送る。"""
        with self._lock:
            pending = self._pending
            if pending is None or self._closed or not _is_int(number) or number != pending.number:
                return self._ok(None)  # 待っている確認が無い・古い・違う番号。何も送らない
            self._pending = None
            if yes is not True:
                if not isinstance(yes, bool):
                    return _failure(gui_web_call_failed())
                return self._ok(None)  # 送らない。元の行は捨てた
            asking = _Asking(pending.number, pending.kind, pending.lines)
            self._asking = asking
            thread = threading.Thread(
                target=self._ask_in_thread, args=(asking, pending.client, pending.prompt), name="driftpilot-rule-ai", daemon=True
            )
            asking.thread = thread
            self._thread = thread
            thread.start()
            return self._ok({"asking": {"id": asking.number, "text": RULE_AI_ASKING}})

    def close(self, number: Any) -> dict[str, Any]:
        """小さな窓を閉じた。number が None なら今の確認と送信を全部、整数ならその番号のものだけ取り消す。"""
        if number is not None and not _is_int(number):
            return _failure(gui_web_call_failed())
        with self._lock:
            self._drop(number)
            return self._ok(None)

    def closed(self) -> None:
        """窓そのものを閉じた。以後は受け付けない。"""
        with self._lock:
            self._closed = True
            self._drop()

    # --- テストと窓の確かめのための読み取り ---

    def holds_lines(self) -> bool:
        """元の行(伏せる前)を持っているか。確認を待つ間と答えを待つ間だけ真。"""
        with self._lock:
            return self._pending is not None or (self._asking is not None and bool(self._asking.lines))

    def join(self, timeout: float | None = None) -> None:
        """最後に始めた送信のスレッドが終わるのを待つ(テストで使う)。答えの読み取り・式の確かめ・知らせを送り終えるまで待つ
        (答えを受けた時点で _asking は空になるので、_asking ではなくスレッドそのものを持つ)。"""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # --- 中身 ---

    @staticmethod
    def _ok(rule_ai: dict[str, Any] | None) -> dict[str, Any]:
        return {"ok": True, "rule_ai": rule_ai}

    def _drop(self, number: int | None = None) -> None:
        """ロックの中で呼ぶ。確認と送信を捨てる(番号を渡したときは、その番号のものだけ)。送信中は取り消しの印を付けて元の行を捨てる。"""
        if self._pending is not None and (number is None or self._pending.number == number):
            self._pending = None
        asking = self._asking
        if asking is not None and (number is None or asking.number == number):
            asking.cancelled = True
            asking.lines = ()
            self._asking = None

    def _prepare(
        self, pasted: str, description: str, kind: str
    ) -> tuple[str | None, _Pending | None, dict[str, Any] | None]:
        """(送れない理由の文, 確認を待つもの, 確認の中身)。ロックの中で呼ぶ。"""
        try:
            settings = load_settings(**self._where, read_prompt=False)
        except SettingsError as exc:
            return str(exc), None, None
        plan = plan_llm(settings, client_factory=self._client_factory, look_rules=False)
        if plan.send is None or plan.send.client is None:
            return rule_ai_unavailable(plan.first.unavailable), None, None
        text = normalize_newlines(pasted).rstrip("\n")
        if not text.strip():
            return RULE_AI_NO_LINES, None, None
        lines = tuple(text.split("\n"))
        if len(lines) > MAX_PASTED_LINES:
            return rule_ai_too_many_lines(len(lines), MAX_PASTED_LINES), None, None
        if len(text) > MAX_PASTED_CHARS:
            return rule_ai_too_many_chars(len(text), MAX_PASTED_CHARS), None, None
        if has_unseen_char(text):
            return RULE_AI_UNSEEN, None, None
        words = normalize_words(plan.send.extra_secret_words)
        refusal = check_question(description, words, max_chars=MAX_DESCRIPTION_CHARS)
        if refusal is not None:
            return RULE_AI_DESCRIPTION_REFUSED.get(refusal, RULE_AI_DESCRIPTION_REFUSED["unsent"]), None, None
        after = build_after_text(before_lines=[], after_lines=lines, masker=Masker(secret_words=words))
        if after.lines == 0:
            return RULE_AI_NO_SENDABLE, None, None
        prompt = build_rule_prompt(kind, description, after.body, after.omitted)
        if prompt_too_long(prompt):
            return rule_ai_prompt_too_long(MAX_PROMPT_CHARS), None, None
        self._serial += 1
        number = self._serial
        pending = _Pending(number, prompt, plan.send.client, kind, lines)
        view = {
            "id": number,
            "title": RULE_AI_TITLE,
            "prompt_title": RULE_AI_PROMPT_TITLE,
            "prompt": prompt,  # 送る全文(読むだけ。キー・CLI の場所は入れない)
            "destination": rule_ai_destination(
                destination_of(settings)[1], display_model(settings), effective_base_url(settings)
            ),
            "omitted": after.omitted,
            "omitted_text": rule_ai_omitted(after.omitted),
            "note": RULE_AI_NOTE,
            "send": RULE_AI_SEND,
            "dont_send": RULE_AI_DONT_SEND,
        }
        return None, pending, view

    def _ask_in_thread(self, asking: _Asking, client: LLMClient, prompt: str) -> None:
        """スレッドで 1 回だけ送る。失敗は理由の種類と型の名前・状態コードだけ。取り消した後・閉じた後に届いた答えは捨てる。"""
        answer: str | None = None
        failure: tuple[str, str, int | None] | None = None
        try:
            reply = client.ask(prompt)
        except LLMConnectionError as exc:
            failure = ("connection_error", type(exc).__name__, None)
        except LLMResponseError as exc:
            failure = ("api_error", type(exc).__name__, exc.status_code)
        except Exception as exc:  # noqa: BLE001 - 接続の実装の想定外の例外も、文面を出さない知らせにする
            failure = ("api_error", type(exc).__name__, None)
        else:
            if isinstance(reply, str):
                answer = reply
            else:
                failure = ("api_error", "InvalidAnswer", None)
        with self._lock:
            live = self._asking is asking and not asking.cancelled and not self._closed
            lines = asking.lines
            asking.lines = ()  # 答えを受けた後に元の行を捨てる(手元の確かめは下のローカル変数だけ)
            if self._asking is asking:
                self._asking = None
        if not live:
            return
        result = self._result(answer, failure, asking.kind, lines)
        del lines
        with self._lock:
            if asking.cancelled or self._closed:
                return  # 確かめている間に閉じた・取り消した
        try:
            self._notify({"type": DONE_EVENT, "id": asking.number, "result": result})
        except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
            pass

    @staticmethod
    def _result(
        answer: str | None, failure: tuple[str, str, int | None] | None, kind: str, lines: tuple[str, ...]
    ) -> dict[str, Any]:
        """画面へ知らせる結果。成功は式・置き換える文字・説明・当たった数の文・前と後の行・欄に入れる値(rule)、失敗は理由の文だけ。"""
        try:
            if failure is not None or answer is None:
                return {"ok": False, "message": rule_ai_failed(*(failure or ("api_error", "InvalidAnswer", None)))}
            reading = read_rule_answer(answer, kind)
            if reading.reason is not None:
                return {"ok": False, "message": rule_ai_unreadable(reading.reason)}
            checked = check_rule(reading.pattern, reading.replacement, kind, lines)
            if checked.problem is not None:
                return {"ok": False, "message": rule_ai_problem(checked.problem)}
            return {
                "ok": True,
                "kind": kind,
                "pattern": reading.pattern,
                "replacement": reading.replacement,
                "note": reading.note,
                "count": rule_ai_match_count(checked.matched, checked.total),
                "rows": [[before, RULE_AI_NOT_COMPARED if after is None else after] for before, after in checked.rows],
                # 「欄に入れる」に使う値(式・置き換える文字・種類だけ)
                "rule": {"kind": kind, "pattern": reading.pattern, "replacement": reading.replacement},
            }
        except Exception:  # noqa: BLE001 - 想定外の例外も、文面を出さない失敗にする
            return {"ok": False, "message": gui_web_call_failed()}
