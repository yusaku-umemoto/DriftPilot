"""端末ログをコマンド単位の区間に切り分ける(ADR-0012 決定2)。

規則は検証用プロトタイプから移したもの(プロトタイプは廃止済み。製品コードが唯一の実装)。
プロンプト候補の終端に `$` を加えている(ADR-0012 決定2b の手順1)。
候補の上限と、ホスト名が一致する候補のグループ採用は ADR-0014 決定1・2。
コマンドの正体への変換はしない(決定3b)。打ち間違いや補完候補も見分けず、全区間をそのまま持つ。
行頭のページ送りの印(--More--)は、制御文字を消す前に外し、外した数を持つ(ADR-0134 決定1・2)。
FortiGate の形のプロンプト(`<host> # `・`<host> (<文脈>) # `)は別の規則でも見分ける(ADR-0134 決定3)。
"""

import collections
import re
from dataclasses import dataclass
from collections.abc import Sequence
from typing import Literal

# 端末制御文字: ESC シーケンス、ベル、バックスペース、復帰。ESC が落ちた状態で残る "[D[K" も消す
CONTROL = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-Z\\-_]|[\x07\x08\r]|\[D\[K")

# ページ送りの印(ADR-0134 決定1)。行頭(前の空白は可)の大文字の --More-- だけで、行の途中の印は変えない。
# 3つ目のグループは、印を消すための制御文字と改行(read_text で \r が改行に変わった形も含む)
PAGER = re.compile(r"^([ \t]*)--More--( *)((?:[\r\n\x08]+(?:[ \t]*[\r\n\x08]+)?)?)", re.M)
# 制御文字の落ちたログで、端末が印を消すために書いた空白のうち、前の空白の数に足す分(印の後ろの1個 + 印の8文字 + 1個)
PAGER_ERASE_EXTRA = 1 + 8 + 1

# プロンプト候補: 行頭から最初の # / > / $ まで(長さの上限は _within_limit で判定する)
# 反復は上限(MAX_PROMPT_LEN)で打ち切り、長い非候補行でバックトラッキングが膨らまないようにする
CANDIDATE = re.compile(r"^(?P<prefix>\S[^\n]{0,19}?\s{0,20}[#>$]\s?)(?P<rest>.*)$")
# 候補の上限(ADR-0014 決定1)。終端記号とその直後の空白1つまでを含めた文字列に対して判定する。
# コーパス 1,104 件の実測で、本物のプロンプトは最長16文字・空白2個。
# 危険な候補(設定値などを取り違えるもの)は中央25文字・最大42文字だった。
MAX_PROMPT_LEN = 20
MAX_PROMPT_SPACES = 2
# プロンプトの後ろに来るのはコマンド。小文字英字で始まる語である、という一点だけで判定する
COMMAND_SHAPED = re.compile(r"^[a-z][a-z0-9][a-z0-9 _.,|/+-]*$")
# FortiGate の形のプロンプト(ADR-0134 決定3)。一般の候補の上限とは別に見分ける。
# ホスト名は空白なしの35文字まで、文脈は ( ) を含まない79文字まで。# の後ろは空白1つか行末
FORTIGATE_PROMPT = re.compile(r"^(?P<prefix>(?P<host>\S{1,35})(?: \([^()]{1,79}\))? #(?: |$))(?P<rest>.*)$")

LOGIN = re.compile(r"^(Login|Username|Password)\s*:\s*$", re.IGNORECASE)
BARE_COMMAND = re.compile(r"^(show|get|display|dir|more|cat)\b.*$", re.IGNORECASE)

# 出力の前置き行(決定2c)。本文から外してメタデータに移す
PREAMBLE = (
    re.compile(r"^Building configuration\.\.\.\s*$"),
    re.compile(r"^Current configuration.*bytes\s*$"),
    re.compile(r"^Using \d+ out of \d+ bytes\s*$"),
    re.compile(r"^#(config-version|conf_file_ver|buildno|global_vdom|vdom)=.*$"),
)

PromptSource = Literal["auto", "manual", "none"]


@dataclass(frozen=True)
class Segment:
    """コマンド1つ分の区間。既定値込み版かどうかは持たない(比較の各側の属性)。"""

    number: int  # 1始まり
    command: str | None  # 原文のコマンド。取得できないときは None
    preamble: tuple[str, ...]  # 本文から外した前置き行
    body: tuple[str, ...]
    line_count: int  # 本文の行数

    @property
    def text(self) -> str:
        """前置き行を外した本文。比較エンジンへはこれを渡す。"""
        return "\n".join(self.body) + "\n" if self.body else ""


@dataclass(frozen=True)
class SplitResult:
    prompts: tuple[str, ...]  # 採用したプロンプト文字列(ホスト名が同じ複数形を含む)。切り分けなしなら空
    prompt_source: PromptSource
    segments: tuple[Segment, ...]
    pager_marks: int = 0  # ファイル全体で外したページ送りの印の数(ADR-0134 決定2)

    @property
    def prompt(self) -> str | None:
        """採用した先頭のプロンプト文字列。切り分けなしなら None。"""
        return self.prompts[0] if self.prompts else None


def _within_limit(prefix: str) -> bool:
    return len(prefix) <= MAX_PROMPT_LEN and prefix.count(" ") <= MAX_PROMPT_SPACES


def _host(prefix: str) -> str:
    """ホスト名部分: 終端記号と前後の空白を除いた部分。"""
    return prefix.strip()[:-1].strip()


def _fortigate_forms(lines: list[str]) -> tuple[dict[str, collections.Counter], collections.Counter, collections.Counter]:
    """FortiGate の形の候補をホスト名ごとに数える(ADR-0134 決定3)。

    (ホスト名 → 形ごとの出現回数, ホスト名ごとの全部の回数, ホスト名ごとのコマンドの形の回数) を返す。
    回数は形をまたいで合計する。どれもログに先に出た順に並ぶ。
    """
    forms: dict[str, collections.Counter] = {}
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = FORTIGATE_PROMPT.match(line)
        if not m:
            continue
        host = m.group("host")
        forms.setdefault(host, collections.Counter())[m.group("prefix")] += 1
        total[host] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[host] += 1
    return forms, total, score


def _by_count(counter: collections.Counter) -> list[str]:
    """出現の多い順。同じ回数ならログに先に出た順。"""
    return sorted(counter, key=lambda p: counter[p], reverse=True)


def detect_prompt(lines: list[str]) -> tuple[str, ...]:
    """このファイルのプロンプト文字列を自己校正で特定する(決定2b)。

    上限内の候補だけを対象にし、採用した候補とホスト名が一致する候補も加える(ADR-0014 決定2)。
    決まったら、ホスト名が同じ FortiGate の形を出現の多い順に後ろへ足す(ADR-0134 決定3 の1)。
    決まらなければ FortiGate の形だけをホスト名ごとに数え、同じ基準を満たす順位1位のホスト名の形を
    出現の多い順にすべて返す(決定3 の2)。
    特定できなければ空のタプル。先頭は順位1位、一般の候補の残りは出現順。
    """
    freq: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = CANDIDATE.match(line)
        if not m or not _within_limit(m.group("prefix")):
            continue
        freq[m.group("prefix")] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[m.group("prefix")] += 1
    forms, fgt_total, fgt_score = _fortigate_forms(lines)
    ranked = sorted(freq, key=lambda p: (score[p], freq[p]), reverse=True)
    for p in ranked:
        if score[p] >= 1 and freq[p] >= 2:
            host = _host(p)
            if not host:
                return (p,)
            adopted = [p, *(q for q in freq if q != p and _host(q) == host)]
            adopted += [q for q in _by_count(forms.get(host, collections.Counter())) if q not in adopted]
            return tuple(adopted)
    hosts = sorted(fgt_total, key=lambda h: (fgt_score[h], fgt_total[h]), reverse=True)
    for host in hosts:
        if fgt_score[host] >= 1 and fgt_total[host] >= 2:
            return tuple(_by_count(forms[host]))
    return ()


def _erase_pager(m: re.Match) -> str:
    """印を外す。消すための制御文字・改行があれば丸ごと消し、無ければ端末が消した分の空白だけ消して字下げを残す。"""
    if m.group(3):
        return ""
    return " " * max(0, len(m.group(2)) - (len(m.group(1)) + PAGER_ERASE_EXTRA))


def _strip_pager(text: str) -> tuple[str, int]:
    """行頭のページ送りの印を外す。(外した後の文字列, 外した数) を返す。"""
    return PAGER.subn(_erase_pager, text)


def _clean_lines(text: str) -> list[str]:
    return [CONTROL.sub("", line) for line in text.replace("\r\n", "\n").split("\n")]


def _is_preamble(line: str) -> bool:
    return any(r.match(line) for r in PREAMBLE)


def _finish(body: list[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """前置き行を本文から外し、末尾の空行を落とす。(前置き行, 本文) を返す。"""
    pre = tuple(line for line in body if _is_preamble(line))
    rest = [line for line in body if not _is_preamble(line)]
    while rest and not rest[-1].strip():
        rest.pop()
    return pre, tuple(rest)


def _display_command(raw: str) -> str:
    return " ".join(raw.split())


def split_log(text: str, prompt: str | Sequence[str] | None = None) -> SplitResult:
    """端末ログを区間に切り分ける。prompt を渡すと自己校正をせずそれを使う(1つでも複数でもよい)。"""
    text, marks = _strip_pager(text)
    lines = _clean_lines(text)
    prompts: tuple[str, ...] = ((prompt,) if prompt else ()) if isinstance(prompt, str) else tuple(p for p in (prompt or ()) if p)
    source: PromptSource = "manual" if prompts else "auto"
    if not prompts:
        prompts = detect_prompt(lines)
    if not prompts:
        # 特定できなければ切り分けない。1ファイル=1区間(現在の動作への縮退)
        pre, body = _finish(lines)
        return SplitResult((), "none", (Segment(1, None, pre, body, len(body)),), marks)
    # 複数が一致するときは最長のものでコマンドとの境目を決める
    by_length = sorted(prompts, key=len, reverse=True)

    sections: list[tuple[str | None, list[str]]] = []
    cmd: str | None = None
    buf: list[str] = []

    def flush() -> None:
        nonlocal cmd, buf
        name, body = cmd, buf
        cmd, buf = None, []
        if name is None:
            if not any(line.strip() for line in body):
                return
            head = next(i for i, line in enumerate(body) if line.strip())
            if BARE_COMMAND.match(body[head].strip()):
                name, body = _display_command(body[head]), body[head + 1 :]
        sections.append((name, body))

    for line in lines:
        if LOGIN.match(line.strip()):
            flush()
            continue
        hit = next((p for p in by_length if line.startswith(p)), None)
        if hit is not None:
            flush()
            c = _display_command(line[len(hit) :])
            if c:
                cmd = c
            continue
        buf.append(line)
    flush()

    segments: list[Segment] = []
    for name, body in sections:
        pre, rest = _finish(body)
        if any(line.strip() for line in rest):
            segments.append(Segment(len(segments) + 1, name, pre, rest, len(rest)))
    return SplitResult(prompts, source, tuple(segments), marks)
