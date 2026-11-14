"""伏せる判定の3段: 秘密語ゲート → 伏せる表 → 送らない(ADR-0019 決定2〜5)。

1. ゲート: 行を空白で語に分け、語が秘密語と完全一致するかを見る。`<` も `>` も無い語は、さらに英数字・`_`・`-` の
   かたまりに分け、どれかが秘密語と大文字・小文字を区別せずに一致するかを見る(`secret=…`・`Password:`・`"secret"`・
   `SECRET` は当たる。ADR-0143 決定1)。`-` と `_` は区切りにしないので、部分文字列では当たらない(`community` は当たるが
   `send-community` は当たらない)。`<` か `>` のある語は、XML のタグ(`<phash>…</phash>` など)の判定だけを見る。行がその
   タグで始まるか同じ行に閉じるタグがあれば、タグの名前を語として見る(ADR-0140 決定2、ADR-0143 決定2)。全角は見ない
   (ADR-0143 決定4)。当たらない行は表を一度も引かずにそのまま送る
2. 表: ゲートを通った行だけ、mask_patterns.MASK_PATTERNS を上から試す。mask なら名前付きグループの位置を記号にし、
   pass ならそのまま送る
3. 表に無い行は送らない。送らなかったことは件数と行頭の形(値を含まない)で残す

記号は「同じ値は同じ記号、違う値は違う記号」(ADR-0018 決定3)。番号は1回の解析の中だけで通じ、対応表は残さない。
PEM ブロック(`-----BEGIN ` から `-----END ` まで)は行単位の表で扱えないため、ブロックごと送らない
(docs/notes/req-16-llm.md の「複数行の秘密」)。端末の幅で折り返されて割れた秘密の行のかたまりも、PEM と同じに
ブロックごと送らない(ADR-0137 決定2)。秘密語の無い決まった形の行(mask_patterns.UNSENT_FORMS)は、ゲートの前で送らないと決める
(ADR-0137 決定3)。形の一覧の行と形の頭(mask_patterns.UNSENT_FORM_HEADS)も、かたまりの判定では秘密の行として扱う
(REQ-198)。どちらも「送らない」にしかできない(送る側に戻す道は無い)。

秘密語が無くても、行の頭の形(mask_patterns.GATE_HEADS。ADR-0198 決定2)に頭から当たる行は、秘密語に当たったときと同じく
表を引く。当たれば伏せて送るか送り、どれにも当たらなければ送らない。判定の順は、見えない文字 → 形の一覧 → (秘密語か行の頭の形に
当たらなければそのまま送る)→ 表 → 送らない。

見えない文字(Unicode の種類が Cc・Cf の文字。タブ・LF・CR を除く)のある行は、形の一覧とゲートより前に送らないと決める
(ADR-0142 決定1)。BOM の無い UTF-16 の NUL や語の中のゼロ幅の文字で、秘密語の判定を外れるため。文字の種類だけを見ていて、
値の形は見ない(ADR-0019 決定1)。形には行の中身を残さない(ADR-0142 決定2)。
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from driftpilot.coverage_report.fold import fold_word
from driftpilot.llm_analyzer.mask_patterns import (
    GATE_HEAD_SCOPES,
    GATE_HEADS,
    MASK_PATTERNS,
    SECRET_WORDS,
    UNSENT_FORM_HEADS,
    UNSENT_FORM_SCOPES,
    UNSENT_FORMS,
    MaskPattern,
    compiled,
)

Decision = Literal["plain", "mask", "pass", "unsent"]

PEM_BEGIN = "-----BEGIN "
PEM_END = "-----END "
# 送らなかった行の形に残す、秘密語より前の語の数の上限
_SHAPE_HEAD_WORDS = 8
# これより長い語は形に残さない(HTML の文面などで形が読めなくなるため)
_SHAPE_WORD_CHARS = 40
_LONG_WORD = "<長い語>"

# 割れた秘密の行のかたまり(ADR-0137 決定2)。幅がこれ以上のときだけ「長い行」を考える
_WRAP_MIN = 40
# 長い行は、幅との差がこの文字数以内(端末が行末の空白を落とすため)
_WRAP_SLACK = 8
# 幅ちょうどの行がこの本数以上あれば、折り返しのファイルとみなす
_WRAP_EVIDENCE = 3
# かたまりの行の記録の形(値も行頭の語も残さない。ADR-0137 決定4)
WRAPPED_SHAPE = "<折り返しの続きがある秘密の行>"
# 1語だけで新しい設定の行に見える語
_FRESH_WORDS = frozenset({"next", "end", "exit", "!", "shutdown", "exit-address-family", "login"})
# 2語以上の行は、最初の語が英小文字とハイフンだけなら新しい設定の行に見える(行の頭の判定で、値の形は見ない)
_FRESH_HEAD = re.compile(r"[a-z][a-z-]*")
# 語の中の XML のタグの名前(開くタグと閉じるタグ。ADR-0140 決定2)
_TAG_NAME = re.compile(r"</?([A-Za-z][\w-]*)")
# 語を分けるかたまり(英数字・`_`・`-`。`-` と `_` は区切りにしない。ADR-0143 決定1)。語の名前を取り出すだけで、値の形は見ない
_WORD_PIECE = re.compile(r"[A-Za-z0-9_-]+")
# 見えない文字のある行の記録の形(行の中身を残さない。ADR-0142 決定2)
UNSEEN_SHAPE = "<見えない文字のある行>"
# 見えない文字から除く文字(タブ・LF・CR。ADR-0142 決定1)
_SEEN_CONTROLS = frozenset("\t\n\r")


@dataclass(frozen=True)
class LineJudgement:
    """1行の判定。text は送る文字列(字下げは元のまま)。unsent のときは None で、shape に行頭の形が入る。"""

    decision: Decision
    text: str | None
    pattern_id: str | None = None
    shape: str | None = None


class SymbolTable:
    """値 → `<secret:N>`。1回の解析の中だけで使い、外へは出さない(値との対応表を残さない)。"""

    def __init__(self) -> None:
        self._numbers: dict[str, int] = {}

    def symbol(self, value: str) -> str:
        number = self._numbers.setdefault(value, len(self._numbers) + 1)
        return f"<secret:{number}>"

    def __len__(self) -> int:
        return len(self._numbers)

    def __repr__(self) -> str:  # 値を表示しない
        return f"SymbolTable({len(self)} 件)"


def has_unseen_char(text: str) -> bool:
    """Unicode の種類が Cc・Cf の文字(タブ・LF・CR を除く)があるか(ADR-0142 決定1)。文字の種類だけを見る。"""
    return any(ch not in _SEEN_CONTROLS and unicodedata.category(ch) in ("Cc", "Cf") for ch in text)


def normalize_words(extra: Iterable[str] = ()) -> tuple[str, ...]:
    """初期値の後ろに追加の語を足す(重複は除く)。削除はできない。"""
    words = list(SECRET_WORDS)
    for word in extra:
        if not isinstance(word, str) or not word or word != word.strip() or len(word.split()) != 1:
            raise ValueError(f"秘密語は空白を含まない1語にしてください: {word!r}")
        if word not in words:
            words.append(word)
    return tuple(words)


@lru_cache(maxsize=32)
def _folded_words(words: frozenset[str]) -> dict[str, str]:
    """小文字にした秘密語 → 秘密語の一覧の文字列。語の集まりごとに1回だけ作る(行ごとに作り直さない)。

    大文字・小文字だけが違う語が一覧に2つあるときは、並べて先に来る語を残す(毎回同じ結果にするため)。
    返した辞書は共有するので、呼び出し側で変えない。
    """
    folded: dict[str, str] = {}
    for word in sorted(words):
        folded.setdefault(word.lower(), word)
    return folded


def _secret_hit(text: str, words: frozenset[str]) -> tuple[int, str] | None:
    """最初に秘密語に当たった語の位置と、記録の形に残す文字列。

    空白で区切った語ごとに、次の順で見る(ADR-0143)。
    1. 語がそのまま秘密語なら当たる。残すのはその語
    2. 語に `<` か `>` があれば、XML のタグの判定だけを見る(ADR-0140 決定2)。残すのは `<名前>`
    3. それ以外は、語を英数字・`_`・`-` のかたまりに分け、どれかが秘密語と大文字・小文字を区別せずに一致すれば当たる。
       残すのは秘密語の一覧のほうの文字列(元の語は `secret=<値>` のように値を含みうるため。決定3)
    """
    head = text.lstrip()
    folded: dict[str, str] | None = None
    for index, word in enumerate(text.split()):
        if word in words:
            return index, word
        if "<" in word or ">" in word:
            for name in _TAG_NAME.findall(word):
                if name in words and (head.startswith("<" + name) or f"</{name}>" in text):
                    return index, f"<{name}>"
            continue
        if folded is None:
            folded = _folded_words(words)
        for piece in _WORD_PIECE.findall(word):
            listed = folded.get(piece.lower())
            if listed is not None:
                return index, listed
    return None


def first_secret_word(text: str, words: frozenset[str]) -> int | None:
    """行の語のうち、最初に秘密語に当たった語の位置。無ければ None。

    語が秘密語と完全一致するとき。`<` も `>` も無い語は、英数字・`_`・`-` のかたまりのどれかが秘密語と大文字・小文字を
    区別せずに一致するときも当たる(`secret=…`・`Password:`・`SECRET`。ADR-0143 決定1)。`-` と `_` は区切りにしないので
    `send-community`・`client-secret-name` は当たらない。`<` か `>` のある語は、語の中の XML のタグの名前が秘密語で、行が
    そのタグで始まるか同じ行に閉じるタグがあるときだけ当たる(ADR-0140 決定2、ADR-0143 決定2)。本文の中で値の代わりに
    書いた `crypto isakmp <key>` は当たらない。全角の語は当たらない(ADR-0143 決定4)。
    """
    hit = _secret_hit(text, words)
    return None if hit is None else hit[0]


def scope_matches(scope: str, ancestors: Sequence[str]) -> bool:
    """scope が祖先の行(外側から順、字下げを除いたもの)に合うか。"""
    if scope == "*":
        return True
    if scope == "top":
        return not ancestors
    return any(a == scope or a.startswith(scope + " ") for a in ancestors)


def scope_label(ancestors: Sequence[str]) -> str:
    """送らなかった行の場所の粗いラベル。トップレベルは top、それ以外は一番外側の祖先の先頭の語。

    `router <proto>` と `config <語> <語>`(FortiGate)は語を足して区別する。祖先の行の値は含めない。
    """
    if not ancestors:
        return "top"
    words = ancestors[0].split()
    if not words:
        return "top"
    if words[0] == "router" and len(words) > 1:
        return f"router {words[1]}"
    if words[0] == "config" and len(words) > 1:
        return " ".join(words[:3])
    return words[0]


def unsent_shape(text: str, words: frozenset[str]) -> str:
    """行頭から最初の秘密語までの形。前の語は fold-v1 で畳み、秘密語より後ろは `…` にする(値を残さない)。"""
    parts = text.split()
    hit = _secret_hit(text, words)
    if hit is None:  # 呼び出し側はゲートを通った行だけを渡す
        return "…"
    index, shown = hit  # タグで当たった語は値を含みうるため、`<名前>` だけを残す
    before = [_LONG_WORD if len(w) > _SHAPE_WORD_CHARS else fold_word(w) for w in parts[:index]]
    if len(before) > _SHAPE_HEAD_WORDS:  # 文面のような長い行は先頭だけ残す
        before = [*before[:_SHAPE_HEAD_WORDS], "…"]
    head = [*before, shown]
    if index + 1 < len(parts) or shown != parts[index]:
        head.append("…")
    return " ".join(head)


# 形の一覧は表とは別に、読み込み時にコンパイルしておく(ゲートを通らない行で表を引かないことと分けるため)
_FORMS = tuple((form_id, re.compile(regex), UNSENT_FORM_SCOPES.get(form_id, "*")) for form_id, regex in UNSENT_FORMS)


_HEADS = tuple((form_id, re.compile(regex)) for form_id, regex in UNSENT_FORM_HEADS)

# 行の頭の形(秘密語が無くても表を引く入口。ADR-0198 決定2)。読み込み時にコンパイルしておく
_GATE_HEADS = tuple(
    (head_id, re.compile(regex), GATE_HEAD_SCOPES.get(head_id, "*")) for head_id, regex in GATE_HEADS
)
# 列の名前を分ける区切り(`_`・`-`・空白)
_COLUMN_SPLIT = re.compile(r"[_\-\s]+")


def gate_head(line: str, ancestors: Sequence[str] | None = None) -> str | None:
    """行が行の頭の形(GATE_HEADS)に頭から当たれば、その ID。行は語を1つの空白で区切った形にして match する。

    ancestors が None のときは場所の条件を見ない(かたまりの判定。形の一覧の行と同じ扱い)。
    """
    body = " ".join(line.split())
    for head_id, regex, scope in _GATE_HEADS:
        if (ancestors is None or scope_matches(scope, ancestors)) and regex.match(body):
            return head_id
    return None


def _form_line_ids(text: str) -> set[str]:
    """行が当たる形の一覧の ID(場所の条件は見ない)と、形の頭の ID。かたまりの判定だけで使う(REQ-198)。"""
    body = " ".join(text.split())
    hits = {form_id for form_id, regex, _ in _FORMS if regex.fullmatch(body)}
    return hits | {form_id for form_id, regex in _HEADS if regex.fullmatch(body)}


def _is_form_head(text: str) -> bool:
    """行が形の頭に当たるか(値が次の行にある形。REQ-198)。"""
    body = " ".join(text.split())
    return any(regex.fullmatch(body) for _, regex in _HEADS)


def unsent_form(line: str, ancestors: Sequence[str]) -> str | None:
    """行が形の一覧(秘密語の無い、送らない形)に当たれば、その ID。行は語を1つの空白で区切った形にして fullmatch する。"""
    body = " ".join(line.split())
    for form_id, regex, scope in _FORMS:
        if scope_matches(scope, ancestors) and regex.fullmatch(body):
            return form_id
    return None


class Masker:
    """1回の解析の判定器。記号表を持つ(構造化差分と行単位の差分で共有する)。"""

    def __init__(
        self,
        *,
        secret_words: Sequence[str] = SECRET_WORDS,
        patterns: Sequence[MaskPattern] = MASK_PATTERNS,
    ) -> None:
        self.secret_words = tuple(secret_words)
        self._words = frozenset(self.secret_words)
        self._patterns = tuple(patterns)
        self.symbols = SymbolTable()

    def gated(self, text: str) -> bool:
        """見えない文字がある(ADR-0142)か、秘密語に当たるか。真なら表を引くか、送らない側へ進む。

        show の差分の道(payload._show_body)は judge を通らずこれだけを見るため、見えない文字もここで見る(ADR-0143 決定5)。
        """
        return has_unseen_char(text) or first_secret_word(text, self._words) is not None

    def is_secret_column(self, name: object) -> bool:
        """show の表の列の名前が秘密か。`_`・`-`・空白で区切った部分のどれかが秘密語に(大文字・小文字を問わず)当たる(REQ-277)。"""
        folded = _folded_words(self._words)
        return any(piece.lower() in folded for piece in _COLUMN_SPLIT.split(str(name)) if piece)

    def judge(self, line: str, ancestors: Sequence[str] = ()) -> LineJudgement:
        """1行を判定する。ancestors は祖先の行(外側から順、字下げを除く)。

        見えない文字のある行は、何よりも先に送らない(ADR-0142)。
        改行を含む値(バナーの本文など。REQ-161)は、表を当てずに改行で分けた行ごとに形の一覧とゲートだけを通す。
        どれかの行が形の一覧に当たるか秘密語を含めば値ごと送らない(1つの空白にまとめると、先頭の行に当たる pass の書式
        (`description …` など)が後ろの行の秘密を通してしまうため)。
        """
        return self._judge(line, ancestors, self.symbols)

    def is_pass(self, line: str) -> bool:
        """表の pass に当たる行か(祖先なしで判定する)。記号表の番号を使わないよう、使い捨ての記号表で判定する。"""
        return self._judge(line, (), SymbolTable()).decision == "pass"

    def _judge(self, line: str, ancestors: Sequence[str], symbols: SymbolTable) -> LineJudgement:
        if has_unseen_char(line):
            return LineJudgement("unsent", None, None, f"[{scope_label(ancestors)}] {UNSEEN_SHAPE}")
        if "\n" in line:
            for part in line.splitlines():
                form_id = unsent_form(part, ancestors)
                if form_id is not None:
                    return LineJudgement("unsent", None, form_id, f"[{scope_label(ancestors)}] {form_id} …")
            # 行の頭の形(ADR-0198)に当たる部分があれば、値ごと送らない。記録は頭の形の ID だけにする(秘密語より前に値があり得るため)
            for part in line.splitlines():
                head_id = gate_head(part, ancestors)
                if head_id is not None:
                    return LineJudgement("unsent", None, None, f"[{scope_label(ancestors)}] {head_id} …")
            gated = next((part for part in line.splitlines() if self.gated(part)), None)
            if gated is not None:
                body = " ".join(gated.split())
                return LineJudgement("unsent", None, None, f"[{scope_label(ancestors)}] {unsent_shape(body, self._words)}")
            return LineJudgement("plain", line)
        form_id = unsent_form(line, ancestors)
        if form_id is not None:
            return LineJudgement("unsent", None, form_id, f"[{scope_label(ancestors)}] {form_id} …")
        head_id = gate_head(line, ancestors)
        if head_id is None and not self.gated(line):
            return LineJudgement("plain", line)
        # 表は語を1つの空白で区切った形に当てる(`password 5 <値>  role …` のような空白の重なりで外さない)。
        # 伏せた行はこの形で送る。pass の行は元のまま送る
        body = " ".join(line.split())
        indent = line[: len(line) - len(line.lstrip())]
        for pattern in self._patterns:
            if not scope_matches(pattern.scope, ancestors):
                continue
            m = compiled(pattern.match).fullmatch(body)
            if m is None:
                continue
            if pattern.kind == "pass":
                return LineJudgement("pass", line, pattern.id)
            spans = sorted(m.span(name) for name in m.groupdict() if m.group(name) is not None)
            out, last = [], 0
            for start, end in spans:
                out.append(body[last:start])
                out.append(symbols.symbol(body[start:end]))
                last = end
            out.append(body[last:])
            return LineJudgement("mask", indent + "".join(out), pattern.id)
        if head_id is not None:  # 頭の形に当たった行は、秘密語があっても頭の形の ID だけを残す(語より前に値があり得るため)
            return LineJudgement("unsent", None, None, f"[{scope_label(ancestors)}] {head_id} …")
        shape = f"[{scope_label(ancestors)}] {unsent_shape(body, self._words)}"
        return LineJudgement("unsent", None, None, shape)

    def mask_value(self, value: object) -> str:
        """構造化差分の伏せる項目(基準1)の値を記号にする。"""
        return self.symbols.symbol(str(value))


def ancestors_by_indent(lines: Sequence[str], masker: Masker | None = None) -> list[tuple[str, ...]]:
    """各行の祖先の行(外側から順、字下げを除く)を字下げから決める。空行は直前の状態を引き継ぐ。

    PEM ブロックと割れた秘密の行のかたまりの2行目以降は字下げが無いことが多いため、祖先の計算に使わない
    (開始行の祖先を引き継ぐ)。かたまりは masker の秘密語で求める(None なら初期値の語)。
    """
    blocks = block_index(lines, masker)
    stack: list[tuple[int, str]] = []
    out: list[tuple[str, ...]] = []
    for index, line in enumerate(lines):
        body = line.strip()
        if not body or blocks.get(index, index) != index:
            out.append(out[blocks[index]] if index in blocks else tuple(s for _, s in stack))
            continue
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        out.append(tuple(s for _, s in stack))
        stack.append((indent, body))
    return out


def pem_blocks(lines: Sequence[str]) -> list[tuple[int, int]]:
    """PEM ブロックの範囲(開始と終了の行の位置。0始まり、両端を含む)。

    `-----BEGIN ` を含む行から `-----END ` を含む行まで。終端が無ければ最後の行まで。ラベル(鍵か証明書か)は問わない。
    """
    blocks: list[tuple[int, int]] = []
    start: int | None = None
    for index, line in enumerate(lines):
        if start is None:
            if PEM_BEGIN in line:
                start = index
                if PEM_END in line[line.index(PEM_BEGIN) + len(PEM_BEGIN) :]:  # 1行に収まったブロック
                    blocks.append((start, index))
                    start = None
        elif PEM_END in line:
            blocks.append((start, index))
            start = None
    if start is not None:
        blocks.append((start, len(lines) - 1))
    return blocks


def _looks_like_fresh_line(line: str) -> bool:
    """新しい設定の行に見えるか(ADR-0137 決定2)。行の頭だけを見る。値が鍵らしいかどうかは見ない(ADR-0019 決定1)。"""
    parts = line.split()
    if not parts:
        return True
    if len(parts) == 1:
        return parts[0] in _FRESH_WORDS
    head = parts[0]
    return _FRESH_HEAD.fullmatch(head) is not None or head.startswith(("!", "#"))


def wrapped_secret_groups(
    lines: Sequence[str], words: Iterable[str], is_pass: Callable[[str], bool]
) -> list[tuple[int, int]]:
    """割れた秘密の行のかたまりの範囲(開始と終了の行の位置。0始まり、両端を含む。ADR-0137 決定2)。

    行は右の空白を落として見る。幅は一番長い行の長さ。行 i から、次の行が空でなければ次の順に見る。
    かたまりができたら、その終わりの次の行へ進む。

    - B: 長い行で、その行にも次の行にも秘密語が無く、2行をつなぐと秘密語が現れる → 2行
    - A: 秘密語のある長い行 → 続く長い行と、その次の行まで(折り返しのファイルでなければ、次の行が新しい設定の行に見えるところで止める)
    - C: 秘密語で終わるか、秘密語 + 1桁の数字で終わる行(表の pass に当たる行を除く) → 2行
    - D: 秘密語のある行で、次の行が新しい設定の行に見えない → 2行

    形の一覧の行(場所の条件は見ない)と形の頭の行も「秘密語のある行」として扱う(REQ-198)。形の頭の行は C でも 2行にする。
    場所の条件が無い形の行で始まる A は、次の行が新しい設定の行に見えても伸ばす(値に小文字の語が続く形があるため)。
    祖先は見ない(ancestors_by_indent がこの関数を使うため)。
    """
    secret = frozenset(words)
    rows = [line.rstrip() for line in lines]
    width = max((len(row) for row in rows), default=0)
    wide = width >= _WRAP_MIN
    wrapped_file = wide and sum(1 for row in rows if len(row) == width) >= _WRAP_EVIDENCE

    def is_long(row: str) -> bool:
        return wide and len(row) >= width - _WRAP_SLACK

    def has_secret(text: str) -> bool:
        return (
            first_secret_word(text, secret) is not None or bool(_form_line_ids(text)) or gate_head(text) is not None
        )

    def unscoped_form(text: str) -> bool:
        return any(form_id not in UNSENT_FORM_SCOPES for form_id in _form_line_ids(text))

    groups: list[tuple[int, int]] = []
    i = 0
    while i < len(rows) - 1:
        row, following = rows[i], rows[i + 1]
        group: tuple[int, int] | None = None
        if following.strip():
            found = has_secret(row)
            if is_long(row) and not found and not has_secret(following) and has_secret(row + following.lstrip()):
                group = (i, i + 1)
            elif is_long(row) and found:
                keep = wrapped_file or unscoped_form(row)
                j = i
                while (
                    j + 1 < len(rows)
                    and is_long(rows[j])
                    and rows[j + 1].strip()
                    and (keep or not _looks_like_fresh_line(rows[j + 1]))
                ):
                    j += 1
                if j > i:
                    group = (i, j)
            if group is None and found and not is_pass(row):
                parts = row.split()
                last_digit = len(parts[-1]) == 1 and parts[-1] in "0123456789"
                if parts[-1] in secret or (len(parts) >= 2 and parts[-2] in secret and last_digit) or _is_form_head(row):
                    group = (i, i + 1)
            if group is None and found and not _looks_like_fresh_line(following):
                group = (i, i + 1)
        if group is None:
            i += 1
        else:
            groups.append(group)
            i = group[1] + 1
    return groups


def block_index(lines: Sequence[str], masker: Masker | None = None) -> dict[int, int]:
    """行の位置 → その行を含むブロックの開始位置。PEM ブロックを先に入れ、割れた秘密の行のかたまりを足す。

    かたまりは masker の秘密語と pass の判定で求める(利用者が足した語も効く。None なら初期値の語)。
    """
    masker = masker if masker is not None else Masker()
    found: dict[int, int] = {}
    for start, end in pem_blocks(lines):
        for index in range(start, end + 1):
            found[index] = start
    for start, end in wrapped_secret_groups(lines, masker.secret_words, masker.is_pass):
        for index in range(start, end + 1):
            found.setdefault(index, start)
    return found


def pem_start_shape(line: str) -> str:
    """PEM ブロックの開始行の形。`-----BEGIN` より前の語と、ラベルまでを残す(本体は含まない)。"""
    body = line.strip()
    head, _, rest = body.partition(PEM_BEGIN)
    label = rest.split("-----", 1)[0]
    words = [fold_word(w) for w in head.split()]
    return " ".join([*words, f"{PEM_BEGIN}{label}-----", "…"])
