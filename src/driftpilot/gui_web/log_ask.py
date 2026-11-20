"""「ログを見る」の窓で、表示中の区間かチェックした区間について AI に質問するための、送る中身・確認・送信(ADR-0251 決定3 / REQ-382)。

RuleAi(gui_web/rule_ai.py。「AI に式を頼む…」の確認と送信)と同じ作り。窓の状態(比較の門・保存)には触らない。
一括比較の窓・比較の窓の門(同時に比べない決まり)は取らない。

- build_log_prompt(log, positions, question, words, premise): 送る文を作る(ただの関数)。区間ごとに見出し(`### {機器の名前} / {元の行かコマンド}`)と
  伏せた後の本文を並べる。本文は比較の窓の全文と同じ判定(build_after_text。伏せる・判定できない行を除く・設定の秘密の語)に、見出しの元の行と
  機器の名前は 1 行ずつの判定(masker.judge)に通す(通らない名前は「判定できずに送らない名前」の文に置き換える)。
  判定器(Masker)は 1 つだけ作る(同じ値は全部の区間で同じ <secret:N>)。秘密の出るコマンド(show crypto isakmp key など)の区間は送らない。
  元の行(prompt_line)は見出しにだけ書き、本文の 1 行目には入れない(二重に入れない)
- LogAsk.ask(log, positions, question): 設定を読み(押すたびに読む。自動送信の設定は読まない)、質問と毎回付ける前提を確かめ、
  送る前の確認の中身を返す。送らない。続けての質問(最初の答えがあるとき)は、最初に送った文・これまでのやり取り・質問を 1 つの文にする
  (follow_up の部品。終わりの文はログ向け)。続けての質問にも毎回確認を出す
- LogAsk.answer(number, yes, log): 「送信する」(yes が True)のときだけ、確認に出した文と 1 字も違わない文をスレッドで 1 回だけ送る。
  送る直前に設定(秘密の語・使うプロファイル・上限・毎回付ける前提)を読み直し、確認を出したときと違えば送らない(REQ-371 と同じ決まり)
- cancel()・clear()・closed(): 取り消し・会話を消す・窓を閉じた。取り消した後・閉じた後に届いた答えは捨てる
- 元の行(伏せる前)は、確認の間だけ「今のログへの参照と区間の番号」として持つ。送る中身を作る間だけ読み、保存しない・ログや例外の文・失敗の知らせに入れない。
  持つのは区間の番号・質問・送った文(伏せた後)・やり取り・確認を出したときの設定の値(SendBasis)だけ
- キー・送った文・答えの文は、ログにも例外の文にも知らせにも入れない(理由の種類・例外の型の名前・状態コードだけ)
"""

import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from driftpilot.gui_web.bridge import _failure
from driftpilot.llm_analyzer import LLMClient, LLMConnectionError, LLMResponseError
from driftpilot.llm_analyzer.follow_up import (
    ANSWER,
    MAX_PROMPT_CHARS,
    MAX_QUESTION_CHARS,
    MAX_TURNS,
    QUESTION,
    build_follow_up,
    check_question,
    prompt_too_long,
    question_count,
)
from driftpilot.llm_analyzer.mask_patterns import secret_show_command
from driftpilot.llm_analyzer.masking import Masker, normalize_words
from driftpilot.llm_analyzer.payload import MAX_FULL_TEXT_CHARS, WHOLE_NAME_OMITTED, build_after_text
from driftpilot.llm_analyzer.prompt import with_premise
from driftpilot.llm_settings import SettingsError, current_premise, display_model, load_settings, premise_text_problem
from driftpilot.messages import (
    FOLLOW_UP_ANSWER_LABEL,
    FOLLOW_UP_QUESTION_LABEL,
    LOG_ASK_ASKING,
    LOG_ASK_CLOSING,
    LOG_ASK_CONFIRM_TITLE,
    LOG_ASK_DONT_SEND,
    LOG_ASK_INSTRUCTION,
    LOG_ASK_LOG_CHANGED,
    LOG_ASK_NO_LINES,
    LOG_ASK_NO_LOG,
    LOG_ASK_NOTE,
    LOG_ASK_PREMISE_CHANGED,
    LOG_ASK_PROMPT_TITLE,
    LOG_ASK_QUESTION_LABEL,
    LOG_ASK_SEND,
    LOG_ASK_WORDS_CHANGED,
    LOG_VIEW_NO_PROMPT,
    REREAD_NOT_SENT,
    batch_pair_ai_destination,
    follow_up_failed,
    follow_up_limit,
    follow_up_refused,
    follow_up_too_long,
    follow_up_unavailable,
    gui_web_call_failed,
    log_ask_destination,
    log_ask_omitted_lines,
    log_ask_omitted_sections,
    log_ask_over_limit,
    log_ask_sections,
    premise_refused_text,
)
from driftpilot.screen_model.batch_model import effective_base_url
from driftpilot.screen_model.log_view_model import LoadedLog, Position, device_name
from driftpilot.screen_model.model import (
    SendBasis,
    answer_blocks,
    answer_copy_text,
    basis_change,
    current_send_basis,
    plan_llm,
    send_basis,
)
from driftpilot.screen_model.settings_model import destination_of


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


@dataclass(frozen=True)
class BuiltPrompt:
    """送る文と、確認に出す数。body は区間の塊(指示文・質問を除く)。送る行が無い・上限を超えるときは作らない(build_log_prompt が理由の文を返す)。"""

    prompt: str
    body: str
    sections: int  # 送る区間の数(秘密の出るコマンドの区間を除く)
    devices: int  # 送る区間のある機器の数
    omitted_lines: int  # 判定できずに送らなかった行の数(本文で除いた行と、置き換えた名前の数)
    omitted_sections: int  # 秘密の出るコマンドのため送らなかった区間の数


def _judged(masker: Masker, line: str) -> str | None:
    """見出しの 1 行(機器の名前・元の行)を判定に通した文字。判定できず送らないときは None。"""
    return masker.judge(line, ()).text


def build_log_prompt(
    log: LoadedLog,
    positions: Sequence[Position],
    question: str,
    *,
    words: Sequence[str],
    premise: str | None,
) -> BuiltPrompt | str:
    """送る文を作る。作れないとき(送る行が無い・上限を超える)は知らせの文。

    区間ごとに次の順で処理する(順を決めておくのは、作り直したときに同じ <secret:N> の番号になるように):
    1. 秘密の出るコマンドの区間は、見出しごと送らず、数だけ数える
    2. 機器の名前を判定する(通らなければ WHOLE_NAME_OMITTED。数える)
    3. 見出しの元の行(prompt_line。無ければコマンドの文。どちらも無ければ「区切りなし」)を判定する(同じ)
    4. 本文を build_after_text(before_lines=[], after_lines=本文, show_command=その区間のコマンド)に通す
    上限は比較の窓の全文と同じ値で、文字の数で数える(塊が MAX_FULL_TEXT_CHARS・送る文全体が MAX_PROMPT_CHARS)。超えたら一部だけ送らない。
    words は normalize_words を通した秘密の語(設定の語)。
    """
    masker = Masker(secret_words=words)
    parts: list[str] = []
    sent_lines = omitted_lines = omitted_sections = 0
    device_set: set[int] = set()
    for device, index in positions:
        segment = log.devices[device].segments[index]
        command = segment.command if segment.command else None
        if secret_show_command(command) is not None:
            omitted_sections += 1
            continue
        hostname = log.devices[device].hostname
        if hostname is None:
            name = device_name(log, device)  # 決まった文(ログの文字ではない)
        else:
            judged = _judged(masker, hostname)
            if judged is None:
                omitted_lines += 1
            name = judged if judged is not None else WHOLE_NAME_OMITTED
        head_source = segment.prompt_line or command
        if head_source is None:
            head = LOG_VIEW_NO_PROMPT
        else:
            judged = _judged(masker, head_source)
            if judged is None:
                omitted_lines += 1
            head = judged if judged is not None else WHOLE_NAME_OMITTED
        after = build_after_text(before_lines=[], after_lines=list(segment.lines), masker=masker, show_command=command)
        sent_lines += after.lines
        omitted_lines += after.omitted
        device_set.add(device)
        heading = f"### {name} / {head}"
        parts.append(f"{heading}\n{after.body}" if after.body else heading)
    if sent_lines == 0:
        return LOG_ASK_NO_LINES
    body = "\n\n".join(parts)
    if len(body) > MAX_FULL_TEXT_CHARS:
        return log_ask_over_limit(len(body), MAX_FULL_TEXT_CHARS)
    prompt = f"{with_premise(LOG_ASK_INSTRUCTION, premise)}\n\n{body}\n\n{LOG_ASK_QUESTION_LABEL}{question}"
    if prompt_too_long(prompt):
        return log_ask_over_limit(len(prompt), MAX_PROMPT_CHARS)
    return BuiltPrompt(prompt, body, len(parts), len(device_set), omitted_lines, omitted_sections)


@dataclass
class _Pending:
    """送る前の確認を待っているもの。送る文・client・今のログへの参照と区間の番号を持つ(送る・送らないの後に捨てる)。"""

    number: int
    follow: bool  # 続けての質問か
    prompt: str
    client: LLMClient
    log: LoadedLog
    positions: tuple[Position, ...]
    question: str
    words: tuple[str, ...]  # 送る文を作った秘密の語(normalize_words の後)
    premise: str | None  # 最初の質問のときの毎回付ける前提(続けてのときは最初の中身が持つ)
    basis: SendBasis
    summary: BuiltPrompt | None  # 最初の質問のとき、確認に出した数


@dataclass
class _First:
    """最初に送った中身(続けての質問の土台)。送った文(伏せた後)・区間の番号・質問・使った語・前提だけ。元の行は持たない。"""

    prompt: str
    positions: tuple[Position, ...]
    question: str
    words: tuple[str, ...]
    premise: str | None
    summary: BuiltPrompt


class _Asking:
    """AI に聞いている間のスレッド 1 本の印。送る文・質問は持たない(スレッドの関数のローカル変数だけ)。"""

    def __init__(self, number: int) -> None:
        self.number = number
        self.cancelled = False


class LogAsk:
    """「ログを見る」の窓の、AI への質問の確認と送信。where は設定の読み元(cwd・env・platform・home)、client_factory は client を作る関数
    (テストで偽物。省くと本物の接続)、notify は状態が変わったときに窓へ知らせる口(ロックの外で呼ぶ)。"""

    def __init__(
        self,
        where: Mapping[str, Any],
        client_factory: Callable[..., LLMClient] | None = None,
        notify: Callable[[], Any] | None = None,
    ) -> None:
        self._where = dict(where)
        self._client_factory = client_factory
        self._notify = notify
        self._lock = threading.RLock()  # この入れ物だけのロック(窓の状態のロックは、呼ぶ側が先に取る)
        self._serial = 0
        self._revision = 0  # 会話・誤り・送信中の表示が変わるたびに進める
        self._pending: _Pending | None = None
        self._asking: _Asking | None = None
        self._thread: threading.Thread | None = None  # 最後に始めた送信のスレッド(join が待つ)
        self._first: _First | None = None
        self._turns: list[tuple[str, str]] = []  # 答え・質問・答え…(最初の質問は _first.question)
        self._shown: list[dict[str, Any]] = []  # 会話の表示(質問と答えを交互に。答えは読み取ったブロックとコピーの文を添える)
        self._error: str | None = None
        self._note: str | None = None
        self._closed = False

    # --- 窓から(LogViewApi がロックを取って呼ぶ) ---

    def destination_line(self) -> str:
        """AI の欄の送り先の行(使用中の AI プロファイルの表示名とモデル)。設定を読めないときはその理由の文。"""
        try:
            settings = load_settings(**self._where, read_prompt=False)
        except SettingsError as exc:
            return str(exc)
        return log_ask_destination(destination_of(settings)[1], display_model(settings))

    def view(self) -> dict[str, Any]:
        """AI の欄の会話の状態(読むだけ)。"""
        with self._lock:
            return {
                "turns": [dict(turn) for turn in self._shown],
                "error": self._error,
                "note": self._note,
                "asking": LOG_ASK_ASKING if self._asking is not None else None,
                "can_clear": bool(self._shown) or self._asking is not None or self._pending is not None,
                "revision": self._revision,
            }

    def ask(self, log: LoadedLog | None, positions: Sequence[Position], question: Any) -> dict[str, Any]:
        """送る前の確認の中身を返す(送らない)。送れないときは理由の文だけ。形の違う引数は不正な呼び出し。"""
        if not isinstance(question, str):
            return _failure(gui_web_call_failed())
        with self._lock:
            if self._closed or self._asking is not None:
                return self._ok(None)
            self._pending = None  # 新しい確認は、前の確認を取り消す
            self._error = None
            self._note = None
            refused, pending, view = self._prepare(log, positions, question)
            self._revision += 1
            if refused is not None:
                self._note = refused
                return self._ok({"refused": refused})
            assert pending is not None and view is not None
            self._pending = pending
            return self._ok({"confirm": view})

    def answer(self, number: Any, yes: Any, log: LoadedLog | None) -> dict[str, Any]:
        """確認の答え。待っている確認と同じ番号のときだけ受け、1 回答えたら消す。yes が True のときだけ 1 回送る。"""
        with self._lock:
            pending = self._pending
            if pending is None or self._closed or not _is_int(number) or number != pending.number:
                return self._ok(None)  # 待っている確認が無い・古い・違う番号。何も送らない
            if not isinstance(yes, bool):
                return _failure(gui_web_call_failed())
            self._pending = None
            self._revision += 1
            if not yes:
                return self._ok(None)  # 送らない。今のログへの参照は捨てた
            stopped, words = self._reread(pending, log)
            if stopped is not None:
                self._note = stopped
                return self._ok({"refused": stopped})
            asking = _Asking(pending.number)
            self._asking = asking
            self._error = None
            self._note = None
            thread = threading.Thread(
                target=self._ask_in_thread, args=(asking, pending, words), name="driftpilot-log-ask", daemon=True
            )
            self._thread = thread
            thread.start()
            return self._ok({"asking": {"id": asking.number, "text": LOG_ASK_ASKING}})

    def cancel(self) -> dict[str, Any]:
        """送信中の印に取り消しを付ける(取り消した後に届いた答えは捨てる)。"""
        with self._lock:
            if self._asking is not None:
                self._asking.cancelled = True
                self._asking = None
                self._revision += 1
            return self._ok(None)

    def clear(self) -> dict[str, Any]:
        """会話と最初に送った中身と待っている確認を捨て、送信中なら取り消す。新しいログを入れたときにも呼ぶ。"""
        with self._lock:
            self._drop()
            return self._ok(None)

    def closed(self) -> None:
        """窓そのものを閉じた。以後は受け付けない。待っている確認を捨て、送信中なら取り消す。"""
        with self._lock:
            self._closed = True
            self._pending = None
            if self._asking is not None:
                self._asking.cancelled = True
                self._asking = None

    # --- テストと窓の確かめのための読み取り ---

    def holds_log(self) -> bool:
        """今のログ(元の行)への参照を持っているか。確認を待つ間だけ真(送る・送らない・閉じる・会話を消すと偽)。"""
        with self._lock:
            return self._pending is not None

    def join(self, timeout: float | None = None) -> None:
        """最後に始めた送信のスレッドが終わるのを待つ(テストで使う)。"""
        with self._lock:
            thread = self._thread
        if thread is not None:
            thread.join(timeout)

    # --- 中身 ---

    @staticmethod
    def _ok(log_ask: dict[str, Any] | None) -> dict[str, Any]:
        return {"ok": True, "log_ask": log_ask}

    def _drop(self) -> None:
        """ロックの中で呼ぶ。会話・最初に送った中身・確認待ちを捨て、送信中なら取り消す。"""
        self._pending = None
        if self._asking is not None:
            self._asking.cancelled = True
            self._asking = None
        self._first = None
        self._turns = []
        self._shown = []
        self._error = None
        self._note = None
        self._revision += 1

    def _prepare(
        self, log: LoadedLog | None, positions: Sequence[Position], question: str
    ) -> tuple[str | None, _Pending | None, dict[str, Any] | None]:
        """(送れない理由の文, 確認を待つもの, 確認の中身)。ロックの中で呼ぶ。"""
        if log is None:
            return LOG_ASK_NO_LOG, None, None
        try:
            settings = load_settings(**self._where, read_prompt=False)
            premise = current_premise(env=self._where.get("env"), platform=self._where.get("platform"), home=self._where.get("home"))
        except SettingsError as exc:
            return str(exc), None, None
        plan = plan_llm(settings, client_factory=self._client_factory)
        if plan.send is None or plan.send.client is None:
            return follow_up_unavailable(plan.first.unavailable), None, None
        first = self._first if self._turns else None
        settings_words = normalize_words(plan.send.extra_secret_words)
        if first is None:
            if premise is not None and premise_text_problem(premise, plan.send.extra_secret_words) is not None:
                return premise_refused_text(), None, None
            refusal = check_question(question, settings_words)
            if refusal is not None:
                return follow_up_refused(refusal, MAX_QUESTION_CHARS), None, None
            built = build_log_prompt(log, positions, question, words=settings_words, premise=premise)
            if isinstance(built, str):
                return built, None, None
            prompt, words, summary = built.prompt, settings_words, built
        else:
            if question_count(self._turns) >= MAX_TURNS:
                return follow_up_limit(MAX_TURNS), None, None
            words = normalize_words([*first.words, *plan.send.extra_secret_words])
            refusal = check_question(question, words)
            if refusal is not None:
                return follow_up_refused(refusal, MAX_QUESTION_CHARS), None, None
            composed = self._compose_follow_up(log, first, question, words)
            if composed is None:
                return LOG_ASK_WORDS_CHANGED, None, None
            if composed[0] is not None:
                return composed[0], None, None
            prompt, summary = composed[1], first.summary
        self._serial += 1
        number = self._serial
        pending = _Pending(
            number=number,
            follow=first is not None,
            prompt=prompt,
            client=plan.send.client,
            log=log,
            positions=tuple(positions),
            question=question,
            words=words,
            premise=first.premise if first is not None else premise,
            basis=send_basis(settings),
            summary=summary,
        )
        view = {
            "id": number,
            "title": LOG_ASK_CONFIRM_TITLE,
            "prompt_title": LOG_ASK_PROMPT_TITLE,
            "prompt": prompt,  # 送る全文(読むだけ。キー・CLI の場所は入れない)
            "destination": batch_pair_ai_destination(
                destination_of(settings)[1], display_model(settings), effective_base_url(settings)
            ),
            "sections": summary.sections,
            "devices": summary.devices,
            "omitted_lines": summary.omitted_lines,
            "omitted_sections": summary.omitted_sections,
            "sections_text": log_ask_sections(summary.sections, summary.devices),
            "omitted_lines_text": log_ask_omitted_lines(summary.omitted_lines),
            "omitted_sections_text": log_ask_omitted_sections(summary.omitted_sections),
            "note": LOG_ASK_NOTE,
            "send": LOG_ASK_SEND,
            "dont_send": LOG_ASK_DONT_SEND,
        }
        return None, pending, view

    def _compose_follow_up(
        self, log: LoadedLog, first: _First, question: str, words: Sequence[str]
    ) -> tuple[str | None, str] | None:
        """続けての質問の送る文。最初の中身を words(最初の語と今の語)で作り直して、最初に送った文と同じか確かめてから作る。

        (None, 送る文) = 作れた。(理由の文, "") = 作れない(長すぎる)。None = 語が変わって最初の文と違う。ロックの中で呼ぶ。
        """
        rebuilt = build_log_prompt(log, first.positions, first.question, words=words, premise=first.premise)
        if isinstance(rebuilt, str) or rebuilt.prompt != first.prompt:
            return None
        try:
            prompt = build_follow_up(first.prompt, self._turns, question, closing=LOG_ASK_CLOSING)
        except ValueError:
            return follow_up_limit(MAX_TURNS), ""
        if prompt_too_long(prompt):
            return follow_up_too_long(MAX_PROMPT_CHARS), ""
        return None, prompt

    def _reread(self, pending: _Pending, log: LoadedLog | None) -> tuple[str | None, tuple[str, ...]]:
        """ロックの中で呼ぶ。承認した送信の直前に、設定を読み直して確認を出したときと比べる(REQ-371)。
        (送らないなら知らせの文 または None, 送る文を作った語)。

        ログが確認のときと違う物なら送らない。使用中のプロファイルの接続先かモデル・上限が違えば送らない。毎回付ける前提が違えば送らない
        (最初の質問のとき)。秘密の語が違えば、今の語で送る文を作り直し、断られるか確認の文と 1 字でも違えば送らない(同じなら送ってよい)。
        設定を読めないときは、理由の文(_prepare と同じ)。
        """
        if log is not pending.log:
            return LOG_ASK_LOG_CHANGED, pending.words
        try:
            now = current_send_basis(**self._where)
            premise_now = (
                None
                if pending.follow
                else current_premise(env=self._where.get("env"), platform=self._where.get("platform"), home=self._where.get("home"))
            )
        except SettingsError as exc:
            return str(exc), pending.words
        kind = basis_change(pending.basis, now)
        if kind in ("profile", "limits"):
            return REREAD_NOT_SENT[kind], pending.words
        if not pending.follow and premise_now != pending.premise:
            return LOG_ASK_PREMISE_CHANGED, pending.words
        if kind != "secret_words":
            return None, pending.words
        refused = REREAD_NOT_SENT["secret_words"]
        first = self._first
        try:
            if pending.follow:
                if first is None:
                    return refused, pending.words
                words = normalize_words([*first.words, *now.secret_words, *pending.words])
                if check_question(pending.question, words) is not None:
                    return refused, pending.words
                composed = self._compose_follow_up(log, first, pending.question, words)
                if composed is None or composed[0] is not None or composed[1] != pending.prompt:
                    return refused, pending.words
            else:
                words = normalize_words(now.secret_words)
                if pending.premise is not None and premise_text_problem(pending.premise, now.secret_words) is not None:
                    return refused, pending.words
                if check_question(pending.question, words) is not None:
                    return refused, pending.words
                built = build_log_prompt(log, pending.positions, pending.question, words=words, premise=pending.premise)
                if isinstance(built, str) or built.prompt != pending.prompt:
                    return refused, pending.words
        except Exception:  # noqa: BLE001 - 作り直せないときも、送らない
            return refused, pending.words
        return None, tuple(words)

    def _ask_in_thread(self, asking: _Asking, pending: _Pending, words: tuple[str, ...]) -> None:
        """スレッドで 1 回だけ送る。失敗は理由の種類と型の名前・状態コードだけ。取り消した後・閉じた後に届いた答えは捨てる。"""
        answer: str | None = None
        failure: tuple[str, str, int | None] | None = None
        client, prompt = pending.client, pending.prompt
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
        blocks: list[dict[str, Any]] | None = None
        copy = ""
        if answer is not None:
            try:  # 整える・コピーの文は、ロックの外で作る(長い答えでもほかの操作を止めない)
                blocks, copy = answer_blocks(answer), answer_copy_text(answer)
            except Exception:  # noqa: BLE001 - 読み取れない答えは、文面を出さない失敗にする
                answer, failure = None, ("api_error", "InvalidAnswer", None)
        with self._lock:
            live = self._asking is asking and not asking.cancelled and not self._closed
            if self._asking is asking:
                self._asking = None
            if not live:
                return  # 取り消した後・閉じた後に届いた答えは捨てる
            if answer is None:
                self._error = follow_up_failed(*(failure or ("api_error", "InvalidAnswer", None)))
            else:
                if pending.follow:
                    self._turns += [(QUESTION, pending.question), (ANSWER, answer)]
                else:
                    assert pending.summary is not None
                    self._first = _First(
                        pending.prompt, pending.positions, pending.question, words, pending.premise, pending.summary
                    )
                    self._turns = [(ANSWER, answer)]
                self._shown += [
                    {"kind": "question", "label": FOLLOW_UP_QUESTION_LABEL, "text": pending.question},
                    {"kind": "answer", "label": FOLLOW_UP_ANSWER_LABEL, "text": answer, "blocks": blocks, "copy": copy},
                ]
            self._revision += 1
        notify = self._notify
        if notify is not None:
            try:
                notify()  # ロックの外で窓に知らせる
            except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
                pass
