"""AI の答えへの追加の質問の文(REQ-118 §2 / ADR-0081)。比較の画面(gui_web/compare_api.py)だけが使う。

- 送る文は、最初に送った全文(LlmAnalysis.payload。伏せた後の文)をそのまま先頭に置き、これまでのやり取りと今の質問を足した1つの文
  (プロバイダの会話の機能は使わない。ADR-0081 決定2)
- やり取りは (種類, 文) の並び。最初の答えから始まり、答えと質問が交互に並び、答えで終わる
- 質問の文は、送る前に今の Masker で1行ずつ確かめ、伏せる対象が見つかれば送らない(書き直しを求める。ADR-0081 決定4)。
  記号の番号がずれるので、置き換えて送ることはしない
- 見えない文字(ADR-0142)は、行に分ける前の質問の全文でも見て断る(改行として扱われる制御文字で行が分かれて消えるため)
- 追加の質問は MAX_TURNS 回まで。合わせた文が MAX_PROMPT_CHARS 文字を超えれば送らない。古いやり取りを黙って落とさない
- 断るときも例外も、質問とやり取りの中身を文に入れない(理由の種類だけ)
"""

from collections.abc import Sequence

from driftpilot.diff_engine import CompareResult, RawCompareResult, ShowCompareResult
from driftpilot.llm_analyzer.masking import (
    PEM_BEGIN,
    Masker,
    ancestors_by_indent,
    has_unseen_char,
    normalize_words,
    wrapped_secret_groups,
)
from driftpilot.llm_analyzer.payload import (
    MAX_FULL_TEXT_CHARS,
    AfterText,
    LlmInputs,
    WholeSection,
    build_after_text,
    build_payload,
    whole_body,
)
from driftpilot.llm_analyzer.types import LlmAnalysis

# 追加の質問の回数・1つの質問の文字数・合わせた文の文字数の上限(どれも str の文字数)
MAX_TURNS = 20
MAX_QUESTION_CHARS = 4000
MAX_PROMPT_CHARS = 200_000
# やり取りの種類
ANSWER = "answer"
QUESTION = "question"
# 断る理由の種類。空白だけ・長すぎる・伏せる形に当たる行・送らない行(秘密の語・形の一覧)・PEM の開始行
REFUSE_BLANK = "blank"
REFUSE_TOO_LONG = "too_long"
REFUSE_MASK = "mask"
REFUSE_UNSENT = "unsent"
REFUSE_PEM = "pem"

HISTORY_HEADER = "=== これまでのやり取り ==="
QUESTION_HEADER = "=== 追加の質問 ==="
END_HEADER = "=== ここまで ==="
CLOSING = "上の差分とやり取りだけをもとに答えてください。分からないことは分からないと答えてください。"
# 変更後の全文も送るときの終わりの文(ADR-0158 決定6)。全文を送らないときは CLOSING のまま
CLOSING_FULL = "上の差分と全文とやり取りだけをもとに答えてください。分からないことは分からないと答えてください。"
# 全文を作り直せなかった理由(rebuild_after_text)。番号がずれる・入力が無い / 全文が上限を超える
REBUILD_MISMATCH = "mismatch"
REBUILD_TOO_LONG = "too_long"
_LABEL = {ANSWER: "回答", QUESTION: "質問"}


def question_count(turns: Sequence[tuple[str, str]]) -> int:
    """やり取りの中の追加の質問の回数。"""
    return sum(1 for kind, _text in turns if kind == QUESTION)


def _check_turns(turns: Sequence[tuple[str, str]]) -> None:
    """答えから始まり、答えと質問が交互に並び、答えで終わるか。違えば ValueError(文に中身を入れない)。"""
    if not turns or len(turns) % 2 == 0:
        raise ValueError("やり取りの形が違います")
    for index, turn in enumerate(turns):
        if not isinstance(turn, tuple) or len(turn) != 2 or not isinstance(turn[1], str):
            raise ValueError("やり取りの形が違います")
        if turn[0] != (ANSWER if index % 2 == 0 else QUESTION):
            raise ValueError("やり取りの形が違います")


def build_follow_up(
    payload: str, turns: Sequence[tuple[str, str]], question: str, *, full_text: bool = False
) -> str:
    """送る文。payload(最初に送った全文)をそのまま先頭に置き、やり取りと今の質問を足す。

    full_text が真(変更後の全文も送る。ADR-0158 決定6)のときだけ、終わりの文を CLOSING_FULL にする。偽なら今の文のまま。
    やり取りの形が違うとき、追加の質問がすでに MAX_TURNS 回あるときは ValueError(古いやり取りを落として続けない)。
    """
    _check_turns(turns)
    if question_count(turns) >= MAX_TURNS:
        raise ValueError(f"追加の質問は {MAX_TURNS} 回までです")
    closing = CLOSING_FULL if full_text else CLOSING
    lines = [HISTORY_HEADER, *(f"{_LABEL[kind]}: {text}" for kind, text in turns), QUESTION_HEADER, question, END_HEADER, closing]
    separator = "\n" if payload.endswith("\n") else "\n\n"
    return payload + separator + "\n".join(lines) + "\n"


def rebuild_after_text(
    *,
    structured: CompareResult | None,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
    inputs: LlmInputs | None,
    first: LlmAnalysis,
    extra_words: Sequence[str],
) -> AfterText | str:
    """追加の質問で送る変更後の全文(ADR-0158 決定7)。作れなければ理由(REBUILD_MISMATCH・REBUILD_TOO_LONG)。

    記号の対応表は残さないので、新しい Masker で最初の送信と同じ入力・同じ順に build_payload をやり直し、続けて全文を判定する
    (順番が同じなので、同じ値は最初の送信と同じ <secret:N> になる)。語は最初に送ったときの語と今の設定の語を合わせる。
    やり直した差分の本文が最初に送った文(first.payload)に無ければ、番号がずれているので送らない(REBUILD_MISMATCH)。
    送った差分の件数か、送らなかった差分の行数が最初の送信と違うとき(語を足して差分の行が減ったなど)も REBUILD_MISMATCH。
    入力が無い・最初の上限の件数が無い・送る差分が無い・全文ができないときも REBUILD_MISMATCH。全文が上限を超えれば REBUILD_TOO_LONG。
    """
    if inputs is None or first.max_diffs is None or not isinstance(first.payload, str):
        return REBUILD_MISMATCH
    words = normalize_words([*(first.secret_words or ()), *extra_words])
    payload = build_payload(
        structured=structured,
        raw=raw,
        show=show,
        before_lines=inputs.before_lines,
        after_lines=inputs.after_lines,
        before_source=inputs.before_source,
        after_source=inputs.after_source,
        masker=Masker(secret_words=words),
        max_diffs=first.max_diffs,
        include_after_text=True,
    )
    if payload is None or payload.after_text is None or payload.body not in first.payload:
        return REBUILD_MISMATCH
    # 差分の行が減っただけだと、残った本文は最初の文の一部として見つかる。送った件数と送らなかった差分の行数も同じかを見る
    first_unsent = first.unsent.lines if first.unsent is not None else 0
    if payload.sent_diffs != first.sent_diffs or payload.unsent.lines != first_unsent:
        return REBUILD_MISMATCH
    if len(payload.after_text.body) > MAX_FULL_TEXT_CHARS:
        return REBUILD_TOO_LONG
    return payload.after_text


def same_sent_body(
    *,
    structured: CompareResult | None,
    raw: RawCompareResult | None,
    show: ShowCompareResult | None,
    inputs: LlmInputs | None,
    first: LlmAnalysis,
) -> bool:
    """比較の窓で比べ直した結果の差分が、一括比較で送った文(first.payload)と同じか(ADR-0170 決定3)。

    rebuild_after_text と同じく、新しい Masker で最初の送信と同じ入力・同じ順に build_payload をやり直す(全文は作らない)。
    ★ 語は一括比較で送ったときの語(first.secret_words)だけ。今の設定の語は足さない(足すと伏せ方が変わり、送った文と比べられない)。
    次の全部が合うときだけ真: やり直した差分の本文が送った文の中にある・送った差分の件数・全部の差分の件数・
    送らなかった差分の行数・送る差分の種類(構造化・単純・show)・打ち切ったか。
    入力が無い・最初の上限の件数が無い・送った文が無い・送る差分が無いときと、例外のときは偽。どの文字もログに出さない。
    """
    if inputs is None or type(first.max_diffs) is not int or first.max_diffs < 1 or not isinstance(first.payload, str):
        return False
    try:
        payload = build_payload(
            structured=structured,
            raw=raw,
            show=show,
            before_lines=inputs.before_lines,
            after_lines=inputs.after_lines,
            before_source=inputs.before_source,
            after_source=inputs.after_source,
            masker=Masker(secret_words=normalize_words(first.secret_words or ())),
            max_diffs=first.max_diffs,
            include_after_text=False,
        )
        if payload is None or payload.body not in first.payload:
            return False
        first_unsent = first.unsent.lines if first.unsent is not None else 0
        return (
            payload.sent_diffs == first.sent_diffs
            and payload.diff_total == first.diff_total
            and payload.unsent.lines == first_unsent
            and payload.source == first.source
            and payload.truncated == first.truncated
        )
    except Exception:  # noqa: BLE001 - 確かめられなければ使わない(中身を例外の文やログに出さない)
        return False


def same_sent_whole_body(
    *,
    sections: Sequence[WholeSection],
    unchanged: Sequence[str | None],
    first: LlmAnalysis,
) -> tuple[int, ...] | None:
    """比較の窓で比べ直したまとめての比較の全体の本文が、一括比較で送った全体の文(first.payload)と同じか(REQ-241 §5)。

    same_sent_body の考えを全部の組をまとめた本文に広げたもの。同じなら本文に入れた組の sections の位置(whole_body の sent)、
    違えば None。first が全体の解析(source が whole)でなければ None(1 組の答えは全体に付けない)。
    新しい Masker で、一括比較で送ったときと同じ入力・同じ順に whole_body をやり直す(同じ値は同じ <secret:N>)。
    ★ 語は一括比較で送ったときの語(first.secret_words)だけ。今の設定の語は足さない(足すと伏せ方が変わり、送った文と比べられない)。
    次の全部が合うときだけ位置を返す: やり直した本文が空でない・送った文の中にある・送った差分の件数・全部の差分の件数・
    送らなかった差分の行数・打ち切ったか。最初の上限の件数が無い・送った文が無いときと、例外のときは None。どの文字もログに出さない。
    """
    if first.source != "whole" or type(first.max_diffs) is not int or first.max_diffs < 1:
        return None
    if not isinstance(first.payload, str):
        return None
    try:
        masker = Masker(secret_words=normalize_words(first.secret_words or ()))
        whole = whole_body(sections, masker, first.max_diffs, unchanged=unchanged)
        if not whole.sent or not whole.body or whole.body not in first.payload:
            return None
        first_unsent = first.unsent.lines if first.unsent is not None else 0
        same = (
            whole.sent_diffs == first.sent_diffs
            and whole.diff_total == first.diff_total
            and whole.unsent.lines == first_unsent
            and whole.truncated == first.truncated
        )
        return whole.sent if same else None
    except Exception:  # noqa: BLE001 - 確かめられなければ使わない(中身を例外の文やログに出さない)
        return None


def rebuild_whole_after_text(
    *,
    sections: Sequence[WholeSection],
    unchanged: Sequence[str | None],
    selected: int,
    first: LlmAnalysis,
    extra_words: Sequence[str],
) -> AfterText | str:
    """全部の組をまとめて送った答えへの追加の質問で送る、選んでいる組の変更後の全文(ADR-0152 決定10・ADR-0158 決定7)。

    selected は sections の中の選んでいる組の位置。記号の対応表は残さないので、新しい Masker で最初の送信(analyze_whole)と
    同じ入力・同じ順に whole_body をやり直し、続けて同じ Masker で選んでいる組の変更後の全文を判定する(同じ値は同じ <secret:N>)。
    やり直した本文が最初の送信の文に無い・送った件数か送らなかった差分の行数が違う・選んでいる組が本文に入っていないときは
    REBUILD_MISMATCH(番号がずれるので送らない)。全文が上限を超えれば REBUILD_TOO_LONG。
    """
    if first.max_diffs is None or not isinstance(first.payload, str) or not 0 <= selected < len(sections):
        return REBUILD_MISMATCH
    words = normalize_words([*(first.secret_words or ()), *extra_words])
    masker = Masker(secret_words=words)
    whole = whole_body(sections, masker, first.max_diffs, unchanged=unchanged)
    if not whole.sent or selected not in whole.sent or whole.body not in first.payload:
        return REBUILD_MISMATCH
    first_unsent = first.unsent.lines if first.unsent is not None else 0
    if whole.sent_diffs != first.sent_diffs or whole.unsent.lines != first_unsent:
        return REBUILD_MISMATCH
    section = sections[selected]
    after_text = build_after_text(
        before_lines=section.before_lines,
        after_lines=section.after_lines,
        before_source=section.before_source,
        after_source=section.after_source,
        masker=masker,
    )
    if len(after_text.body) > MAX_FULL_TEXT_CHARS:
        return REBUILD_TOO_LONG
    return after_text


def prompt_too_long(text: str) -> bool:
    """合わせた文が上限を超えるか(超えれば送らない)。"""
    return len(text) > MAX_PROMPT_CHARS


def check_question(question: str, secret_words: Sequence[str]) -> str | None:
    """質問の文を確かめる。送ってよければ None、断るなら理由の種類(REFUSE_*。質問の中身は返さない)。

    1行ずつ、比較のときと同じ秘密の語の Masker で、祖先の行なしと、比較と同じく字下げから求めた祖先の行付き
    (ancestors_by_indent)の両方で判定し、どちらかで伏せる行(mask)か送らない行(unsent)になれば、最初に当たった行の種類で断る。
    PEM の開始行(`-----BEGIN `)は行の判定ではそのまま送る側になるため、別に断る(比較では PEM のかたまりを送らない。ADR-0019)。
    割れた秘密の行のかたまり(ADR-0137 決定2)に当たる行があれば、送らない行として断る。
    見えない文字(ADR-0142)は、行に分ける前の全文で見て、送らない行として断る(垂直タブや NEL などで行が分かれて消えるため)。
    """
    if not question.strip():
        return REFUSE_BLANK
    if len(question) > MAX_QUESTION_CHARS:
        return REFUSE_TOO_LONG
    if has_unseen_char(question):
        return REFUSE_UNSENT
    masker = Masker(secret_words=secret_words)
    lines = question.splitlines()
    if any(PEM_BEGIN in line for line in lines):
        return REFUSE_PEM
    # 上の行で場所が決まる形(parameter-map type の下の token など)も当てるため、祖先の行を付けた判定もする。
    # 場所の条件が付いた pass に当たって緩くならないよう、祖先の行なしの判定と両方を見る
    for line, ancestors in zip(lines, ancestors_by_indent(lines, masker), strict=True):
        decisions = [masker.judge(line, ()).decision]
        if ancestors:
            decisions.append(masker.judge(line, ancestors).decision)
        if "mask" in decisions:
            return REFUSE_MASK
        if "unsent" in decisions:
            return REFUSE_UNSENT
    if wrapped_secret_groups(lines, masker.secret_words, masker.is_pass):
        return REFUSE_UNSENT
    return None
