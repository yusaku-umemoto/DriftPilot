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
表を引く。ADR-0199 決定3 の3接頭辞で始まる値も同じ入口とし、XML タグには value も使う(決定4)。
値の形の入口は3接頭辞のままで、ADR-0208 の `/etc/shadow` の欄2の方式の印(`$6$` など)は、ここには足さず、行の頭の形(GH-15)の
正規表現の中で見る(判定の処理は変えない)。足した頭の形(GH-15〜19)も、割れた秘密の行のかたまりの判定では今までの頭の形と同じく
秘密の行として扱う。続く行が新しい設定の行に見えない出力(shadow・.env・ヘッダーの並び)は、かたまりごと送らないことがある。
当たれば伏せて送るか送り、どれにも当たらなければ送らない。判定の順は、見えない文字 → 形の一覧 → (秘密語・行の頭の形・値の形に
当たらなければそのまま送る)→ 表 → 送らない → (そのまま送るになった行だけ)ログの行の段。

ログの行の段(ADR-0215。REQ-306): 上の判定で「そのまま送る」になった行だけ、行の頭のログの形(mask_patterns.LOG_PREFIXES。時刻・
ホスト名・プロセス名・`+ `・プロンプトなど)を外して、残りを同じ判定(祖先の行も同じ)にもう一度かける。残りが伏せるなら、外した頭を
元のまま前に戻して返す。残りが送らないなら送らない。残りもそのまま送る(か pass)なら、行の途中の `<名前>=<値>` を探す
(名前を `_`・`-`・`.` で区切った最後の部分が KEY・TOKEN・PASSWORD・PASSWD・PASS・PWD・SECRET・CREDENTIAL・CREDENTIALS)。
値の終わりがはっきりしない形(閉じない引用符・閉じた後ろに文字が続く・引用符の中の `\\`・引用符の無い値の途中の引用符か `\\`)が
1つでもあれば、行ごと送らない。改行を含む値は、行ごとにこの段を通し、どれかの行が伏せるか送らないになれば値ごと送らない。
今「伏せる」「送らない」「pass」の行は、この段に回さない(送る行は増えない)。この段の決まりを変えたら、
mask_patterns.LOG_LINE_RULES の版を上げる。

行の途中の秘密(ADR-0219。REQ-315): 上の `<名前>=<値>` に、行のどこにあっても秘密のヘッダー(`Authorization: …`・`X-API-Key: …`・
`Cookie: …`。mask_patterns.SECRET_HEADER_NAMES・AUTH_SCHEMES)とコマンドの秘密の引数(`--token <値>`・`curl -u user:<値>`・
`sshpass -p <値>`。mask_patterns.COMMAND_SECRET_OPTIONS)を足し、「伏せる」になった行(表の判定・頭を外した判定で伏せた後の文)にも
当てる。伏せた後の文では、値が記号(`<secret:N>`)だけのものは見ない。「送らない」「pass」の行は今のまま。記号の番号は、送らないと
決まらないと分かってから付ける(表で付けた番号は、最後に送らないになったら巻き戻す)。

残る穴(ADR-0220。REQ-316): 同じ段に、行のどこにあってもパスワードのハッシュ(`$6$<salt>$<hash>`・`$apr1$…`など。mask_patterns.
CRYPT_HASH_SCHEMES・CRYPT_HASH。`$<方式>$` から続きの終わりまでを1つの記号にする)を足した。コマンドの語は頭の `"`・`'`・`(`・`` ` ``・
`$(` を外して見る(`sh -c "curl -u …"`)。`curl -U`・`curl -b`・`redis-cli -a` は続け書きも当てる。ヘッダーは、エスケープした引用符
(`{\\"Authorization\\": \\"Bearer …\\"}`)・タプルの形(`('Authorization', 'Bearer …')`)・引用符の無い値の方式の語(`"Authorization": Bearer …`
は方式の語を残して次の語を伏せる)も当てる。「送らない」「pass」の行は今のまま。名前の前だけがエスケープした引用符で名前の後ろに閉じる
引用符が無い形(`\\"Authorization: …\\"`)と、コマンドの値の後ろに閉じの引用符がくっつく形は、はっきりしない形として行ごと送らない。

残る穴(ADR-0221。REQ-317): コマンドの語は、語の中の最後の区切りの記号(`|`・`;`・`&`・`(`・`` ` ``・`"`・`'`・`=`)までを外して見る
(`RESULT=$(curl -u …)`・`x|curl -u …`・`ok;redis-cli -a …`)。パスワードのハッシュの前の文字は、英数字と `_` でなければよい(行の頭も含む。
`<hash>$6$…</hash>`・`x admin:!$6$…`)。値の取り方は今のまま(`(redis-cli -a <値>)` の閉じかっこは値に含まれる)。「送らない」「pass」の行は今のまま。

見えない文字(Unicode の種類が Cc・Cf の文字。タブ・LF・CR を除く)のある行は、形の一覧とゲートより前に送らないと決める
(ADR-0142 決定1)。BOM の無い UTF-16 の NUL や語の中のゼロ幅の文字で、秘密語の判定を外れるため。文字の種類だけを見ていて、
値の形は ADR-0199 決定3 の3接頭辞だけを例外として見る。形には行の中身を残さない(ADR-0142 決定2)。
"""

import re
import unicodedata
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from driftpilot.coverage_report.fold import fold_word
from driftpilot.llm_analyzer.mask_patterns import (
    AUTH_SCHEMES,
    COMMAND_SECRET_OPTIONS,
    CRYPT_HASH,
    GATE_HEAD_SCOPES,
    GATE_HEADS,
    LOG_PREFIXES,
    MASK_PATTERNS,
    SECRET_HEADER_NAMES,
    SECRET_WORDS,
    SECRET_VALUE_PREFIXES,
    XML_SECRET_NAMES,
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

    def _truncate(self, count: int) -> None:
        """記号表の件数を count に戻す(その後に足した値を消す)。送らないと決まった行の値を残さないための内部用。"""
        for value in list(self._numbers)[count:]:
            del self._numbers[value]

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
                if (name in words or name in XML_SECRET_NAMES) and (head.startswith("<" + name) or f"</{name}>" in text):
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


def first_secret_value(text: str) -> int | None:
    """ADR-0199 決定3 の3接頭辞だけを見る。二重引用符は先頭の1文字だけ除く。"""
    return next(
        (i for i, word in enumerate(text.split()) if word.removeprefix('"').startswith(SECRET_VALUE_PREFIXES)),
        None,
    )


def unsent_shape(text: str, words: frozenset[str]) -> str:
    """行頭から最初の秘密語までの形。前の語は fold-v1 で畳み、秘密語より後ろは `…` にする(値を残さない)。"""
    parts = text.split()
    hit = _secret_hit(text, words)
    value_index = first_secret_value(text)
    if value_index is not None and (hit is None or value_index <= hit[0]):
        # 秘密語より前の値も記録しない。値の語そのものは一切残さない。
        return "…"
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


_WORD = re.compile(r"\S+")

# ログの行の頭の形(ADR-0215 決定2)。読み込み時にコンパイルしておく
_LOG_PREFIXES = tuple((prefix_id, re.compile(regex), kind) for prefix_id, regex, kind in LOG_PREFIXES)
# 行の途中の `<名前>=<値>`(ADR-0215 決定4)。名前は語の頭から。名前を `_`・`-`・`.` で区切った最後の部分が秘密を示す語なら値を伏せる。
# この一覧や値の決まりを変えたら mask_patterns.LOG_LINE_RULES を上げる
_KV_NAME = re.compile(r"(?<!\S)([A-Za-z_][\w.-]*)=")
_KV_SPLIT = re.compile(r"[_.-]")
_KV_SECRET_PARTS = frozenset({"key", "token", "password", "passwd", "pass", "pwd", "secret", "credential", "credentials"})


def _log_head_end(line: str, start: int) -> int:
    """行の start(字下げの後ろ)から、ログの頭の形を当たる限り順に外した後の位置。外せなければ start。"""
    pos = start
    after_time = False
    while True:
        for _, regex, kind in _LOG_PREFIXES:
            if kind == "after_time" and not after_time:
                continue
            m = regex.match(line, pos)
            if m is None or m.end() == pos:
                continue
            pos = m.end()
            if kind == "time":
                after_time = True
            elif kind == "any":
                after_time = False
            break
        else:
            return pos


def _secret_pairs(line: str) -> tuple[list[tuple[int, int, str]], str | None]:
    """行の中の秘密の `<名前>=<値>` の、(値の始まり, 終わり, 名前)の並びと、値の終わりがはっきりしない語の名前(無ければ None)。

    空白で区切った語の頭の `<名前>=` だけを見る。値が空(`""`・`''` を含む)なら伏せる対象が無い。引用符で正しく閉じた値は
    中(空白も含む)、引用符の無い値は空白までの1語を伏せる。はっきりしない形(閉じない引用符・閉じた後ろに文字が続く・
    引用符の中の `\\`・引用符の無い値の途中の引用符か `\\`)があれば、その語の名前を返す(呼び出し側は行ごと送らない)。
    """
    found: list[tuple[int, int, str]] = []
    pos = 0
    while True:
        m = _KV_NAME.search(line, pos)
        if m is None:
            return found, None
        name, value_start = m.group(1), m.end()
        pos = value_start
        if _KV_SPLIT.split(name)[-1].lower() not in _KV_SECRET_PARTS:
            continue
        if value_start >= len(line) or line[value_start].isspace():
            continue
        quote = line[value_start]
        if quote in "\"'":
            close = line.find(quote, value_start + 1)
            after = close + 1
            if close < 0 or "\\" in line[value_start + 1 : close] or (after < len(line) and not line[after].isspace()):
                return found, name
            if close > value_start + 1:
                found.append((value_start + 1, close, name))
            pos = after
        else:
            end = value_start
            while end < len(line) and not line[end].isspace():
                end += 1
            if any(ch in line[value_start:end] for ch in "\"'\\"):
                return found, name
            found.append((value_start, end, name))
            pos = end


# 行の途中の秘密のヘッダー・コマンドの秘密の引数(ADR-0219)。この一覧や値の決まりを変えたら mask_patterns.LOG_LINE_RULES を上げる
# ヘッダーの名前: 名前の前は行の頭・空白・`"`・`'`・`{`・`(`・`,`・`[`(語の途中には当てない)。名前の後ろは `:`(名前が引用符で囲まれた形は
# 閉じる引用符の後ろの `:`)と、0 個以上の空白
_HEADER = re.compile(r"""(?<![^\s"'{(,\[])(?P<name>[A-Za-z][A-Za-z0-9-]*)(?P<close>["']?):[^\S\n]*""")
_HEADER_NAMES = frozenset(name.lower() for name in SECRET_HEADER_NAMES)
_AUTH_SCHEMES = frozenset(scheme.lower() for scheme in AUTH_SCHEMES)
_SCHEME_AND_SPACE = re.compile(r"\S+\s+")
# エスケープした引用符(`\"`・`\'`)・タプルの形(ADR-0220 決定4・5)。名前を同じ種類の引用符で囲み、閉じの後ろが `:` と空白(名前の前が
# エスケープした引用符のときだけ)、または空白 0 個以上・`,`・空白 0 個以上。引用符で囲んだ名前の `:` の形は `_HEADER` が受け持つ。
# 名前の前だけがエスケープした引用符で後ろに閉じる引用符が無い形(`\"Authorization: …\"`)は、ここに入れず `_HEADER` の決まりのままにする
_HEADER_QUOTED = re.compile(
    r"""(?P<open>\\["']|["'])(?P<name>[A-Za-z][A-Za-z0-9-]*)(?P<close>\\["']|["'])"""
    r"""(?:(?P<colon>:[^\S\n]*)|(?P<comma>[^\S\n]*,[^\S\n]*))"""
)
_QUOTE_AT = re.compile(r"""\\["']|["']""")
# 方式の語の後ろの空白と次の語(`,`・`}`・`]`・`)`・空白の前まで)
_NEXT_WORD = re.compile(r"[^\S\n]+([^\s,}\])]+)")
# 値が記号(`<secret:N>`)だけのものは、伏せ直さない
_SYMBOL_ONLY = re.compile(r"<secret:\d+>")
# 長い名前の引数(`--<名前>=<値>`・`--<名前> <値>`)。名前を `-`・`_` で区切った最後の部分が秘密を示す語なら値を伏せる
_LONG_OPTION = re.compile(r"--(?P<name>[A-Za-z][\w-]*)(?:=|$)")
_OPTION_SPLIT = re.compile(r"[-_]")
# コマンドの語の、最後に出る区切りの記号(`|`・`;`・`&`・`(`・`` ` ``・`"`・`'`・`=`)までを外す(ADR-0221 決定1。REQ-317)。
# `sh -c "curl …"`・`(curl …)`・`` `curl …` ``・`RESULT=$(curl …)`・`x|curl …`・`ok;redis-cli …`・`a&&sshpass …`。
# 最長一致なので、記号が続いても語の中で最後の記号までを外す。記号が無ければ何も外さない
_COMMAND_LEAD = re.compile(r"""^.*[|;&(`"'=]""")
# パスワードのハッシュ(ADR-0220 決定1)。読み込み時にコンパイルしておく
_CRYPT_HASH = re.compile(CRYPT_HASH)


def _crypt_hashes(line: str) -> list[tuple[int, int, str]]:
    """行の中のパスワードのハッシュの、(始まり, 終わり, 記録の語 `$<方式>$…`)の並び。`$<方式>$` から続きの終わりまでを1つにする。"""
    return [(m.start(), m.end(), f"${m.group('scheme')}$…") for m in _CRYPT_HASH.finditer(line)]


def _shown_name(name: str) -> str:
    """記録に残す名前。長い名前は `<長い語>` にする(HTML の文面などで形が読めなくなるため)。"""
    return _LONG_WORD if len(name) > _SHAPE_WORD_CHARS else name


def _is_secret_header(name: str) -> bool:
    lowered = name.lower()
    return lowered in _HEADER_NAMES or ("-" in name and lowered.rsplit("-", 1)[-1] in _KV_SECRET_PARTS)


def _closing_quote(line: str, start: int, quote: str) -> int | None:
    """start(引用符の次の位置)から、同じ引用符が閉じる位置。閉じない・中に `\\` がある(はっきりしない形)なら None。"""
    close = line.find(quote, start)
    if close < 0 or "\\" in line[start:close]:
        return None
    return close


def _header_value_span(line: str, start: int, end: int) -> tuple[int, int] | None:
    """ヘッダーの値の範囲 [start, end) から、伏せる範囲を決める。伏せる部分が空か記号だけなら None。

    末尾の空白は含めない。値の最初の語が認証の方式の語で後ろに語が続くなら、方式の語と空白を残して後ろを全部。ほかは値の全部。
    """
    # 引用符の中の値は、空白やタブから始まることがある。先頭と末尾の空白を除いた範囲で見る
    value = line[start:end]
    start += len(value) - len(value.lstrip())
    end = start + len(line[start:end].rstrip())
    if end <= start:
        return None
    value = line[start:end]
    first = value.split(None, 1)[0]
    scheme = _SCHEME_AND_SPACE.match(value)
    if first.lower() in _AUTH_SCHEMES and scheme is not None:
        start += scheme.end()
    if _SYMBOL_ONLY.fullmatch(line[start:end]):
        return None
    return start, end


def _secret_headers(line: str) -> tuple[list[tuple[int, int, str]], str | None]:
    """行の中の秘密のヘッダーの、(値の伏せる始まり, 終わり, 記録の語 `名前: …`)の並びと、値の終わりがはっきりしない形の記録の語。

    名前: SECRET_HEADER_NAMES、または `-` を含み最後の部分が `_KV_SECRET_PARTS` の語(大文字・小文字を問わない)。
    値の範囲: 名前の直前が引用符 q で、名前の後ろに q が無い形は次の q の前まで(無いか、中に `\\` があれば、はっきりしない形)。
    名前が引用符 q で囲まれた形は、`:` の後ろの空白の次が引用符 r なら次の r の前まで(閉じないか中に `\\` があれば、はっきりしない形)、
    引用符でなければ次の `,`・`}`・空白の前まで(最初の語が認証の方式の語で後ろに語が続けば、方式の語を残して次の語だけ。ADR-0220 決定6)。
    ほか(引用符の無い形)は行の終わりまで。
    もう1回、`_HEADER_QUOTED` で、名前を同じ種類のエスケープした引用符(`\\"`・`\\'`)か引用符で囲み、閉じの後ろが `:`(名前の前が
    エスケープした引用符のときだけ)か `,`(タプルの形)の形を見る(ADR-0220 決定4・5)。値の取り方は上と同じで、引用符の値は同じ引用符の前まで。
    """
    found: list[tuple[int, int, str]] = []
    for m in _HEADER.finditer(line):
        name = m.group("name")
        if not _is_secret_header(name):
            continue
        close = m.group("close")
        name_start = m.start("name")
        opened = line[name_start - 1] if name_start > 0 and line[name_start - 1] in "\"'" else ""
        label = f"{_shown_name(name)}: …"
        value_start = m.end()
        if opened and not close:  # 名前が引用符の中で始まる形(`"Authorization: Bearer …"`)
            end = _closing_quote(line, value_start, opened)
            if end is None:
                return found, label
        elif opened and close == opened:  # 名前が引用符で囲まれた形(`'Authorization': 'Bearer …'`)
            if value_start >= len(line):
                continue
            if line[value_start] in "\"'":
                value_start += 1
                end = _closing_quote(line, value_start, line[value_start - 1])
                if end is None:
                    return found, label
            else:
                span = _unquoted_header_span(line, value_start)
                if span is not None:
                    found.append((span[0], span[1], label))
                continue
        elif not opened and not close:  # 引用符の無い形は行の終わりまで
            end = len(line)
        else:
            continue
        span = _header_value_span(line, value_start, end)
        if span is not None:
            found.append((span[0], span[1], label))
    for m in _HEADER_QUOTED.finditer(line):  # エスケープした引用符・タプルの形(ADR-0220 決定4・5)
        name = m.group("name")
        if not _is_secret_header(name) or m.group("open") != m.group("close"):
            continue
        label = f"{_shown_name(name)}: …"
        value_start = m.end()
        if value_start >= len(line):
            continue
        quote = _QUOTE_AT.match(line, value_start)
        if quote is None:
            if m.group("comma") is not None or not m.group("open").startswith("\\"):
                continue  # 引用符の無い値は、名前もエスケープした `:` の形だけ(引用符で囲んだ名前の `:` は上で見た)
            span = _unquoted_header_span(line, value_start)
            if span is not None:
                found.append((span[0], span[1], label))
            continue
        if m.group("colon") is not None and not m.group("open").startswith("\\"):
            continue  # 引用符で囲んだ名前の `:` の形は上で見た
        token = quote.group()
        content = quote.end()
        close_at = line.find(token, content)
        if close_at < 0 or "\\" in line[content:close_at]:
            return found, label
        span = _header_value_span(line, content, close_at)
        if span is not None:
            found.append((span[0], span[1], label))
    return found, None


def _unquoted_header_span(line: str, start: int) -> tuple[int, int] | None:
    """引用符の無いヘッダーの値(start から)の、伏せる範囲。無ければ None(ADR-0220 決定6)。

    値は `,`・`}`・空白の前まで。その最初の語が認証の方式の語で、後ろに空白と語が続くなら、方式の語を残して次の語
    (`,`・`}`・`]`・`)`・空白の前まで)だけを伏せる。次の語が記号だけなら何もしない。ほかは最初の語の全部。
    """
    end = start
    while end < len(line) and line[end] not in ",}" and not line[end].isspace():
        end += 1
    if line[start:end].lower() in _AUTH_SCHEMES:
        word = _NEXT_WORD.match(line, end)
        if word is not None:
            return None if _SYMBOL_ONLY.fullmatch(word.group(1)) else (word.start(1), word.end(1))
    if end <= start or _SYMBOL_ONLY.fullmatch(line[start:end]):
        return None
    return start, end


def _option_value(line: str, start: int) -> tuple[int, int] | Literal["vague"] | None:
    """引数の値の範囲 [始まり, 終わり)。値が無い・空なら None、はっきりしない形なら "vague"(`_secret_pairs` の値と同じ決まり)。

    正しく閉じた引用符は中を伏せる。引用符の無い値は空白までの1語で、途中に引用符か `\\` があればはっきりしない形。
    """
    if start >= len(line) or line[start].isspace():
        return None
    quote = line[start]
    if quote in "\"'":
        close = line.find(quote, start + 1)
        after = close + 1
        if close < 0 or "\\" in line[start + 1 : close] or (after < len(line) and not line[after].isspace()):
            return "vague"
        return (start + 1, close) if close > start + 1 else None
    end = start
    while end < len(line) and not line[end].isspace():
        end += 1
    if any(ch in line[start:end] for ch in "\"'\\"):
        return "vague"
    return start, end


def _secret_options(line: str) -> tuple[list[tuple[int, int, str]], str | None]:
    """行の中のコマンドの秘密の引数の、(値の伏せる始まり, 終わり, 記録の語 `引数 …`)の並びと、はっきりしない形の記録の語。

    長い名前の引数: 空白で区切った語が `--<名前>=<値>`、または語 `--<名前>` の次の語が `<値>`。名前を `-`・`_` で区切った最後の部分が
    `_KV_SECRET_PARTS` の語なら値を伏せる。次の語が `-` で始まるか無ければ何もしない。
    COMMAND_SECRET_OPTIONS の引数: そのコマンドの語(前にパスが付いてもよい。語の中の最後の区切りの記号(`|`・`;`・`&`・`(`・`` ` ``・`"`・`'`・`=`)までは外して見る。
    ADR-0220 決定2・ADR-0221 決定1)が同じ行の前のほうにあるときだけ当てる。値の後ろに閉じの引用符がくっつく形は、はっきりしない形(行ごと送らない)。
    """
    found: list[tuple[int, int, str]] = []
    words = list(_WORD.finditer(line))
    first_seen: dict[str, int] = {}  # コマンドの語(パスを除く) → 最初に出る語の位置
    for position, word in enumerate(words):
        # 語の中の最後の区切りの記号(`|`・`;`・`&`・`(`・`` ` ``・`"`・`'`・`=`)までを外してから、`/` の前のパスを除いて比べる(ADR-0221 決定1)
        first_seen.setdefault(_COMMAND_LEAD.sub("", word.group()).rsplit("/", 1)[-1], position)

    def take(label: str, value_start: int, mode: str) -> str | None:
        """値を調べて found に足す。はっきりしない形なら記録の語を返す。"""
        value = _option_value(line, value_start)
        if value == "vague":
            return label
        if value is None:
            return None
        start, end = value
        if mode == "after-colon":
            colon = line.find(":", start, end)
            if colon < 0:
                return None
            start = colon + 1
        elif mode == "if-equals" and "=" not in line[start:end]:
            return None
        if start < end and not _SYMBOL_ONLY.fullmatch(line[start:end]):
            found.append((start, end, label))
        return None

    for index, word in enumerate(words):
        text = word.group()
        following = words[index + 1] if index + 1 < len(words) else None
        long = _LONG_OPTION.match(text)
        if long is not None and _OPTION_SPLIT.split(long.group("name"))[-1].lower() in _KV_SECRET_PARTS:
            label = f"{_shown_name('--' + long.group('name'))} …"
            if long.end() > len(long.group("name")) + 2:  # `--<名前>=<値>`
                vague = take(label, word.start() + long.end(), "all")
            elif following is not None and not line[following.start()] == "-":
                vague = take(label, following.start(), "all")
            else:
                vague = None
            if vague is not None:
                return found, vague
        for command, option, mode, attached in COMMAND_SECRET_OPTIONS:
            if first_seen.get(command, index) >= index:  # そのコマンドの語が、この語より前に無い
                continue
            label = f"{option} …"
            if text == option:
                vague = take(label, following.start(), mode) if following is not None else None
            elif option.startswith("--") and text.startswith(option + "="):
                vague = take(label, word.start() + len(option) + 1, mode)
            elif attached and text.startswith(option) and not text.startswith("--") and len(text) > len(option):
                vague = take(label, word.start() + len(option), mode)
            else:
                continue
            if vague is not None:
                return found, vague
    return found, None


def _midline_secrets(text: str, *, masked: bool) -> tuple[list[tuple[int, int, str]], str | None]:
    """行の途中の秘密(`<名前>=<値>`・ヘッダー・引数・パスワードのハッシュ)の、(伏せる始まり, 終わり, 記録の語)の並びと、はっきりしない形の記録の語。

    並びは始まりの順で、重なる範囲は1つにまとめる(記録の語は先のもの)。記録の語は `名前=…`・`名前: …`・`引数 …`・`$<方式>$…`。
    3つのどれかにはっきりしない形があれば、並びは空で記録の語を返す(呼び出し側は行ごと送らない。ハッシュにははっきりしない形が無い)。
    masked が真(伏せた後の文)のときは、値が記号だけの `<名前>=<値>` を除く(今伏せた値を伏せ直さない)。
    そのまま送る行(masked が偽)の `<名前>=<値>` は、REQ-306 の決まりのまま。
    """
    pairs, vague_pair = _secret_pairs(text)
    headers, vague_header = _secret_headers(text)
    options, vague_option = _secret_options(text)
    if vague_pair is not None:
        return [], f"{_shown_name(vague_pair)}=…"
    if vague_header is not None:
        return [], vague_header
    if vague_option is not None:
        return [], vague_option
    spans = [
        (start, end, f"{_shown_name(name)}=…")
        for start, end, name in pairs
        if not (masked and _SYMBOL_ONLY.fullmatch(text[start:end]))
    ]
    spans = sorted([*spans, *headers, *options, *_crypt_hashes(text)], key=lambda span: (span[0], span[1]))
    merged: list[tuple[int, int, str]] = []
    for start, end, label in spans:
        if merged and start < merged[-1][1]:
            previous = merged[-1]
            merged[-1] = (previous[0], max(previous[1], end), previous[2])
        else:
            merged.append((start, end, label))
    return merged, None


def _original_value(line: str, start: int, end: int) -> str:
    """語を1つの空白で区切った形(body)の位置 [start, end) に当たる、元の行の文字列(空白の重なり・タブを残したもの)。

    body は `" ".join(line.split())` なので、語ごとに位置を対応させる。語の途中は同じ幅で、語の間の空白は元の空白の始まりに寄せる。
    """
    words = [(m.start(), m.end()) for m in _WORD.finditer(line)]
    shift = 0  # body の中の、いまの語の始まり
    starts: list[tuple[int, int, int]] = []  # (bodyの始まり, bodyの終わり, 元の始まり)
    for original_start, original_end in words:
        length = original_end - original_start
        starts.append((shift, shift + length, original_start))
        shift += length + 1

    def locate(position: int, *, is_end: bool) -> int:
        for body_start, body_end, original_start in starts:
            if body_start <= position < body_end or (is_end and position == body_end):
                return original_start + (position - body_start)
        for body_start, body_end, original_start in starts:  # 語の間(空白の位置)。その前の語の終わりへ
            if position == body_end:
                return original_start + (body_end - body_start)
        return len(line)

    return line[locate(start, is_end=False) : locate(end, is_end=True)]


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
        """見えない文字・秘密語(XML の専用名を含む)・3接頭辞の値に当たるか。真なら表を引くか送らない側へ進む。

        show の差分の道(payload._show_body)は judge を通らずこれだけを見るため、見えない文字もここで見る(ADR-0143 決定5)。
        """
        return (
            has_unseen_char(text) or first_secret_word(text, self._words) is not None
            or first_secret_value(text) is not None
        )

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

    def judge_alone(self, line: str, ancestors: Sequence[str] = ()) -> LineJudgement:
        """1行を使い捨ての記号表で判定する。番号が行の間で続かず、この判定器の記号表も変えない(`is_pass` と同じ形)。"""
        return self._judge(line, ancestors, SymbolTable())

    def _judge(self, line: str, ancestors: Sequence[str], symbols: SymbolTable) -> LineJudgement:
        """今の判定(_judge_base)の後に、行の途中の秘密の段(ADR-0215・ADR-0219)を当てる。unsent・pass は変えない。

        「そのまま送る」の行はログの行の段(頭を外した判定 → 行の途中の `<名前>=<値>`・ヘッダー・引数)、「伏せる」の行は伏せた後の文に
        行の途中の秘密の決まりを当てる。最後の結果が「伏せる」でなければ、途中で記号表に足した値を戻す(送らない行の値を残さない)。
        """
        mark = len(symbols)
        base = self._judge_base(line, ancestors, symbols)
        if base.decision == "mask" and base.text is not None:
            result = self._midline(base.text, ancestors, symbols, True, base.pattern_id) or base
        elif base.decision == "plain":
            if "\n" in line:
                return self._log_stage_value(line, ancestors, base)
            result = self._log_stage_line(line, ancestors, symbols) or base
        else:
            return base
        if result.decision != "mask":
            symbols._truncate(mark)
        return result

    def _midline(
        self, text: str, ancestors: Sequence[str], symbols: SymbolTable, masked: bool, pattern_id: str | None
    ) -> LineJudgement | None:
        """行の途中の秘密(`<名前>=<値>`・ヘッダー・引数・パスワードのハッシュ)を伏せるか送らないにする。当たらなければ None。

        はっきりしない形が1つでもあれば行ごと送らない。記録は場所と記録の語(名前だけで、値は残さない)。
        記号の番号は、送らないと決まらないと分かってから付ける。
        """
        spans, vague = _midline_secrets(text, masked=masked)
        if vague is not None:
            return LineJudgement("unsent", None, None, f"[{scope_label(ancestors)}] {vague}")
        if not spans:
            return None
        out, last = [], 0
        for start, end, _ in spans:
            out.append(text[last:start])
            out.append(symbols.symbol(text[start:end]))
            last = end
        out.append(text[last:])
        return LineJudgement("mask", "".join(out), pattern_id)

    def _log_stage_line(self, line: str, ancestors: Sequence[str], symbols: SymbolTable) -> LineJudgement | None:
        """1行のログの行の段。伏せるか送らないになれば、その判定。変わらなければ None(呼び出し側が元の判定を返す)。"""
        start = len(line) - len(line.lstrip())
        head_end = _log_head_end(line, start)
        if head_end > start:
            inner = self._judge_base(line[head_end:], ancestors, symbols)
            if inner.decision == "mask" and inner.text is not None:
                text = line[:head_end] + inner.text
                return self._midline(text, ancestors, symbols, True, inner.pattern_id) or LineJudgement(
                    "mask", text, inner.pattern_id
                )
            if inner.decision == "unsent":
                return inner
        return self._midline(line, ancestors, symbols, False, None)

    def _log_stage_value(self, value: str, ancestors: Sequence[str], base: LineJudgement) -> LineJudgement:
        """改行を含む値のログの行の段。行ごとに使い捨ての記号表で当て、どれかが伏せるか送らないになれば値ごと送らない(記録に値を残さない)。"""
        label = scope_label(ancestors)
        for part in value.splitlines():
            found = self._log_stage_line(part, ancestors, SymbolTable())
            if found is None:
                continue
            if found.decision == "unsent":
                return LineJudgement("unsent", None, found.pattern_id, found.shape)
            if found.pattern_id is not None:  # 表の行(頭を外した後の伏せる)。記録は表の ID だけ
                return LineJudgement("unsent", None, None, f"[{label}] {found.pattern_id} …")
            words = [word for _, _, word in _midline_secrets(part, masked=False)[0]]  # 行の途中の秘密の伏せる。記録は名前だけ
            return LineJudgement("unsent", None, None, f"[{label}] {words[0] if words else '…'}")
        return base

    def _judge_base(self, line: str, ancestors: Sequence[str], symbols: SymbolTable) -> LineJudgement:
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
                # 記号の番号は、空白を詰める前の元の行の値で決める(引用符の中の空白だけが違う値を同じ記号にしない。ADR-0018 決定3)
                out.append(symbols.symbol(_original_value(line, start, end)))
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
    かたまりができたら、その終わりの次の行へ進む。ただし、かたまりの最後の行にも秘密語があれば、その行から
    もう一度見て、かたまりができれば広げる(広げたものを 1 つのかたまりとして返す。ADR-0222)。

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
            first_secret_word(text, secret) is not None or first_secret_value(text) is not None
            or bool(_form_line_ids(text)) or gate_head(text) is not None
        )

    def unscoped_form(text: str) -> bool:
        return any(form_id not in UNSENT_FORM_SCOPES for form_id in _form_line_ids(text))

    def group_at(i: int) -> tuple[int, int] | None:
        """行 i から A〜D の順に見て、かたまり (i, j) か None を返す。"""
        if i >= len(rows) - 1:
            return None
        row, following = rows[i], rows[i + 1]
        if not following.strip():
            return None
        group: tuple[int, int] | None = None
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
        return group

    groups: list[tuple[int, int]] = []
    i = 0
    while i < len(rows) - 1:
        group = group_at(i)
        if group is None:
            i += 1
            continue
        start, end = group
        # かたまりの最後の行にも秘密語があれば、その行から続けて見て広げる(ADR-0222)
        seg_start = start
        while end > seg_start and has_secret(rows[end]):
            chained = group_at(end)
            if chained is None:
                break
            seg_start, end = end, chained[1]
        groups.append((start, end))
        i = end + 1
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
