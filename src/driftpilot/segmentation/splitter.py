"""端末ログをコマンド単位の区間に切り分ける(ADR-0012 決定2)。

規則は検証用プロトタイプから移したもの(プロトタイプは廃止済み。製品コードが唯一の実装)。
プロンプト候補の終端に `$` を加えている(ADR-0012 決定2b の手順1)。
候補の上限と、ホスト名が一致する候補のグループ採用は ADR-0014 決定1・2。
コマンドの正体への変換はしない(決定3b)。打ち間違いや補完候補も見分けず、全区間をそのまま持つ。
行頭のページ送りの印(--More--)は、制御文字を消す前に外し、外した数を持つ(ADR-0134 決定1・2)。
FortiGate の形のプロンプト(`<host> # `・`<host> (<文脈>) # `)は別の規則でも見分ける(ADR-0134 決定3)。
アラクサラ AX の形のプロンプト(`> `・`# `・`(config)# `・`!<host># `・20文字までのホスト名)も別の規則で見分ける。
Cisco IOS-XR の形のプロンプト(`RP/0/RSP0/CPU0:<host>#`・`RP/0/RP0/CPU0:<host>(config-if)#`)も別の規則で見分け、
設定モードの形の行のコマンドの頭の `do ` は外す(ADR-0211 決定7。場所の部分だけで 14〜15 文字あり、一般の候補の上限を超えるため)。
括弧の形のプロンプト(`[RTX1210] > `・`[RTX1210]# `・`[RTX830 Tokyo] > `。ヤマハ RTX の `console prompt`、Check Point Gaia の expert)も
別の規則で見分ける(ADR-0213 決定8)。一般の候補は頭の字が `[` の候補を数えないため。FortiGate の形では、`[` で始まるホスト名は数えない。
Cisco ASA の形のプロンプト(`<host>/pri/act# `・`<host>/sec/stby# `・`<host>/ctx1/pri/act(config-if)# `。フェイルオーバーの状態を出す設定)も
別の規則で見分け、設定モードの形の行のコマンドの頭の `do ` は外す(ADR-0214 決定7。`/pri/act` だけで 8 文字あり、一般の候補の上限を超えるため)。
設定モードの運用コマンドの頭の `$`(`(config)# $show ip arp`)は、区間のコマンドから外す(ADR-0207 決定1〜3)。
採ったプロンプトが設定モードの形(`Router(config)# `)のときは、モードを外したホスト名(`Router`)の
ほかのモードのプロンプトと `Router# `・`Router> ` も区切りにする(ADR-0209 決定7)。
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
# 頭の語が英小文字で始まり英小文字・数字・- が続き、そのあとは空白で区切って何でもよい(引数・パイプつきも可。ADR-0201 決定1)
COMMAND_SHAPED = re.compile(r"^[a-z][a-z0-9-]*(?:\s.*)?$")
# 出力が空でも区間を残すコマンドの頭の語(小文字)。プロンプトが 1 回だけのログの判定にも使う(ADR-0201 決定4・5)
KEEP_EMPTY_COMMANDS = frozenset({"show", "get", "display", "diagnose", "execute", "fnsysctl", "dir", "more", "cat"})
# 設定モードのプロンプト(ADR-0201 決定3): ホスト名に空白なしで (<モード>) が付き、終端が # か >。
# host が採ったプロンプトのホスト名のどれかと同じときだけ区切りにする
_MODE_HOST = re.compile(r"^(?P<host>[^\s()#>$]{1,35})\([^()\s]{1,30}\)$")
CONFIG_MODE_PROMPT = re.compile(r"^(?P<host>[^\s()#>$]{1,35})\((?P<mode>[^()\s]{1,30})\)[#>]\s?(?P<rest>.*)$")
# FortiGate の形のプロンプト(ADR-0134 決定3)。一般の候補の上限とは別に見分ける。
# ホスト名は空白なしの35文字まで、文脈は ( ) を含まない79文字まで。# の後ろは空白1つか行末
FORTIGATE_PROMPT = re.compile(r"^(?P<prefix>(?P<host>\S{1,35})(?: \([^()]{1,79}\))? #(?: |$))(?P<rest>.*)$")
# アラクサラ AX の形のプロンプト(ADR-0207 決定1)。一般の候補・FortiGate の形とは別に見分ける。
# 頭の `!` は未保存の印。ホスト名は空でもよい(裸のプロンプト)・20文字まで。(config…) は設定モード。終端の後ろは空白1つ
AX_PROMPT = re.compile(r"^(?P<prefix>!?(?P<host>[^\s#>()]{0,20})(?:\((?P<mode>config[^()\s]{0,30})\))?[#>] )(?P<rest>.*)$")

# Cisco IOS-XR の形のプロンプト(ADR-0211 決定7)。場所(`RP/0/RSP0/CPU0`・`LC/0/1/CPU0` など)+ `:` + ホスト名 + 設定モード(省略可)+ `#`。
# 場所だけで 14〜15 文字あり一般の候補の上限(20 文字)を超えるので、別に見分ける。`#` の後ろの空白は 0 個か 1 個。
# ホスト名の頭の字が文字か数字であることは _xr_match で確かめる
XR_PROMPT = re.compile(
    r"^(?P<prefix>(?P<loc>(?:RP|LC)/\d+/[A-Za-z0-9]+/CPU\d+):(?P<host>[^\s()#>$]{1,63})"
    r"(?:\((?P<mode>[^()\s]{1,40})\))?#\s?)(?P<rest>.*)$"
)

# 括弧の形のプロンプト(ADR-0213 決定8)。`[` + ホスト名(括弧の中の 1〜32 文字、`[`・`]` を含まない・頭の字は空白でない)+ `]` + 空白(0〜1 個)
# + 終端 `#` か `>` + 空白(0〜1 個)。ヤマハ RTX は `console prompt` の文字列の後に `>`(一般)か `#`(管理者)を付ける
BRACKET_PROMPT = re.compile(r"^(?P<prefix>\[(?P<host>[^\[\]\s][^\[\]]{0,31})\] ?[#>] ?)(?P<rest>.*)$")

# Cisco ASA の形のプロンプト(ADR-0214 決定7)。フェイルオーバーの状態を出す設定(`prompt hostname [context] [priority] state`)では
# `<host>[/<context>][/pri|/sec]/<state>[(<mode>)]#` になり、`/pri/act` だけで 8 文字ある(一般の候補の上限を超えるため別に見分ける)。
# 状態の語(act・stby・actNoFailover・stbyNoFailover)が最後の `/` の後に無い形(`/etc/freeradius#`・`asa01/admin#`)は当たらない。
# ホスト名に `@`・`:` は入らない。ctx と pri の組は `/pri/act` の `/pri` を ctx が取ることがあるので使わない(host・mode・prefix・state の終わりだけ使う)
ASA_PROMPT = re.compile(
    r"^(?P<prefix>(?P<host>[A-Za-z0-9][A-Za-z0-9._-]{0,62})(?P<ctx>/[A-Za-z0-9._-]{1,32})?(?P<pri>/(?:pri|sec))?"
    r"/(?P<state>act|stby|actNoFailover|stbyNoFailover)(?:\((?P<mode>[^()\s]{1,40})\))?[#>]\s?)(?P<rest>.*)$"
)

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


def _host_head_ok(host: str) -> bool:
    """ホスト名の頭の字を見て、プロンプトの候補として数えてよいか(ADR-0201 決定2)。

    ホスト名が空(`# `・`> ` だけ)なら数える。空でなければ、頭の字が文字か数字(str.isalnum)のときだけ数える。
    BGP 表の印(`*>`・`*>i`・`*>e`)や `[`・`(` などの記号で始まる候補を除くため。
    「英字・数字」は Python の文字・数字の判定で読むので、日本語のホスト名は今までどおり数える(決定者の回答)。
    """
    return not host or host[0].isalnum()


def _mode_base(host: str) -> str | None:
    """ホスト名が設定モードの形(`sw1(config)`)なら、モードを除いたホスト名(`sw1`)。そうでなければ None。"""
    m = _MODE_HOST.match(host)
    return m.group("host") if m else None


def _first_word(command: str | None) -> str:
    """コマンドの頭の語を小文字で返す。コマンドが無ければ空の文字列。"""
    parts = (command or "").split(None, 1)
    return parts[0].lower() if parts else ""


def _fortigate_match(line: str) -> re.Match | None:
    """行が FortiGate の形のプロンプトで始まるときだけ当たりを返す。ホスト名が `[` で始まるものは当てない(括弧の形で読む。ADR-0213 決定8)。"""
    m = FORTIGATE_PROMPT.match(line)
    if m and not m.group("host").startswith("["):
        return m
    return None


def _fortigate_forms(lines: list[str]) -> tuple[dict[str, collections.Counter], collections.Counter, collections.Counter]:
    """FortiGate の形の候補をホスト名ごとに数える(ADR-0134 決定3)。

    (ホスト名 → 形ごとの出現回数, ホスト名ごとの全部の回数, ホスト名ごとのコマンドの形の回数) を返す。
    回数は形をまたいで合計する。どれもログに先に出た順に並ぶ。
    """
    forms: dict[str, collections.Counter] = {}
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = _fortigate_match(line)
        if not m:
            continue
        host = m.group("host")
        forms.setdefault(host, collections.Counter())[m.group("prefix")] += 1
        total[host] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[host] += 1
    return forms, total, score


def _strip_dollar(command: str) -> str:
    """コマンドの頭の `$` を1つ外す(AX の設定モードの運用コマンド `$show ip arp`。ADR-0207 決定3)。

    前後の空白を除いてから外し、外したあとの空白を詰め直す。`$` が無ければ空白を除いただけで返す。
    """
    c = command.strip()
    if c.startswith("$"):
        c = " ".join(c[1:].split())
    return c


def _strip_do(command: str) -> str:
    """設定モードの行のコマンドの頭の `do `(運用コマンドを呼ぶ語。大文字・小文字は区別しない)を外す。後ろに語が無ければそのまま。"""
    head, _, tail = command.partition(" ")
    if head.lower() == "do" and tail:
        return tail
    return command


def _xr_match(line: str) -> re.Match | None:
    """行が XR の形のプロンプトで始まり、ホスト名の頭の字が文字か数字(ADR-0201 決定2)のときだけ当たりを返す。"""
    m = XR_PROMPT.match(line)
    if m and _host_head_ok(m.group("host")):
        return m
    return None


def _xr_command(m: re.Match) -> str:
    """XR の形の行の後ろのコマンド。`mode` があるときだけ頭の `do ` も外す。"""
    c = _strip_dollar(_display_command(m.group("rest")))
    return _strip_do(c) if m.group("mode") else c


def _xr_forms(lines: list[str]) -> tuple[dict[str, collections.Counter], collections.Counter, collections.Counter]:
    """XR の形の候補をホスト名ごとに数える(ADR-0211 決定7)。`_fortigate_forms` と同じ組を返す。

    場所と設定モードが違っても、同じホスト名にまとめる。コマンドの形は `_xr_command` で整えてから `COMMAND_SHAPED` で判定する。
    """
    forms: dict[str, collections.Counter] = {}
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = _xr_match(line)
        if not m:
            continue
        host = m.group("host")
        forms.setdefault(host, collections.Counter())[m.group("prefix")] += 1
        total[host] += 1
        if COMMAND_SHAPED.match(_xr_command(m)):
            score[host] += 1
    return forms, total, score


def _asa_command(m: re.Match) -> str:
    """ASA の形の行の後ろのコマンド。`mode` があるときだけ頭の `do ` も外す。"""
    c = _strip_dollar(_display_command(m.group("rest")))
    return _strip_do(c) if m.group("mode") else c


def _asa_forms(lines: list[str]) -> tuple[dict[str, collections.Counter], collections.Counter, collections.Counter]:
    """ASA の形の候補をホスト名ごとに数える(ADR-0214 決定7)。`_fortigate_forms` と同じ組を返す。

    コンテキスト・優先度・状態・設定モード・終端が違っても、同じホスト名にまとめる。コマンドの形は `_asa_command` で整えてから `COMMAND_SHAPED` で判定する。
    """
    forms: dict[str, collections.Counter] = {}
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = ASA_PROMPT.match(line)
        if not m:
            continue
        host = m.group("host")
        forms.setdefault(host, collections.Counter())[m.group("prefix")] += 1
        total[host] += 1
        if COMMAND_SHAPED.match(_asa_command(m)):
            score[host] += 1
    return forms, total, score


def _bracket_forms(lines: list[str]) -> tuple[dict[str, collections.Counter], collections.Counter, collections.Counter]:
    """括弧の形の候補をホスト名(括弧の中)ごとに数える(ADR-0213 決定8)。`_fortigate_forms` と同じ組を返す。

    `>`・`#`・空白の違いは、括弧の中が同じなら同じホスト名にまとめる。コマンドの形は `rest` の前後の空白を除いて `COMMAND_SHAPED` で判定する。
    """
    forms: dict[str, collections.Counter] = {}
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = BRACKET_PROMPT.match(line)
        if not m:
            continue
        host = m.group("host")
        forms.setdefault(host, collections.Counter())[m.group("prefix")] += 1
        total[host] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[host] += 1
    return forms, total, score


def _ax_forms(lines: list[str]) -> tuple[dict[str, collections.Counter], collections.Counter, collections.Counter]:
    """AX の形の候補をホスト名ごとに数える(ADR-0207 決定1)。`_fortigate_forms` と同じ組を返す。

    ホスト名が空(裸のプロンプト)も1つのホスト名として数える。空でなければ、頭の字が文字か数字のものだけ数える。
    コマンドの形は、後ろの頭の `$` を外してから `COMMAND_SHAPED` で判定する。
    """
    forms: dict[str, collections.Counter] = {}
    total: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = AX_PROMPT.match(line)
        if not m:
            continue
        host = m.group("host")
        if not _host_head_ok(host):
            continue
        forms.setdefault(host, collections.Counter())[m.group("prefix")] += 1
        total[host] += 1
        if COMMAND_SHAPED.match(_strip_dollar(m.group("rest"))):
            score[host] += 1
    return forms, total, score


def _ax_bang_forms(ax: dict[str, collections.Counter], host: str) -> list[str]:
    """一般の候補で決まったホスト名と同じ名前の、頭に `!` の付いた AX の形を出現の多い順に返す。

    設定モードの形(`sw1(config)`)で決まったときは、モードを除いたホスト名で比べる。
    """
    base = _mode_base(host) or host
    return [q for q in _by_count(ax.get(base, collections.Counter())) if q.startswith("!")]


def _by_count(counter: collections.Counter) -> list[str]:
    """出現の多い順。同じ回数ならログに先に出た順。"""
    return sorted(counter, key=lambda p: counter[p], reverse=True)


def detect_prompt(lines: list[str]) -> tuple[str, ...]:
    """このファイルのプロンプト文字列を自己校正で特定する(決定2b)。

    上限内の候補だけを対象にし、採用した候補とホスト名が一致する候補も加える(ADR-0014 決定2)。
    決まったら、ホスト名が同じ FortiGate の形を出現の多い順に後ろへ足す(ADR-0134 決定3 の1)。
    一般の候補で決まったときは、同じホスト名で頭に `!` の付いた AX の形も足す(未保存の印。ADR-0207 の運用での追加)。
    決まらなければ FortiGate の形だけをホスト名ごとに数え、同じ基準を満たす順位1位のホスト名の形を
    出現の多い順にすべて返す(決定3 の2)。
    それでも決まらなければ XR の形(`XR_PROMPT`)を同じ基準でホスト名ごとに数え、順位1位のホスト名の形を
    設定モードの形も含めてすべて返す(ADR-0211 決定7)。
    それでも決まらなければ括弧の形(`BRACKET_PROMPT`。`[RTX1210] > `)を括弧の中のホスト名ごとに同じ基準で数え、
    順位1位のホスト名の形を出現の多い順にすべて返す(ADR-0213 決定8)。FortiGate の形は、`[` で始まるホスト名を数えない。
    それでも決まらなければ ASA の形(`ASA_PROMPT`。`fw-01/pri/act# `)をホスト名ごとに同じ基準で数え、
    順位1位のホスト名の形を設定モードの形も含めてすべて返す(ADR-0214 決定7)。
    それでも決まらなければ AX の形(`AX_PROMPT`)を同じ基準でホスト名ごとに数え、順位1位のホスト名の形を
    設定モードの形も含めてすべて返す(ADR-0207 決定2)。
    最後に、プロンプトが 1 回だけのログの決まりを見る(`_detect_single_prompt`)。
    特定できなければ空のタプル。先頭は順位1位、一般の候補の残りは出現順。
    """
    return _detect(lines)[0]


def _detect(lines: list[str]) -> tuple[tuple[str, ...], str]:
    """(プロンプトの組, どの規則で決まったか) を返す。規則は general・fortigate・xr・bracket・asa・ax・single-xr・single-asa・single-other・none。"""
    freq: collections.Counter = collections.Counter()
    score: collections.Counter = collections.Counter()
    for line in lines:
        m = CANDIDATE.match(line)
        if not m or not _within_limit(m.group("prefix")) or not _host_head_ok(_host(m.group("prefix"))):
            continue
        freq[m.group("prefix")] += 1
        if COMMAND_SHAPED.match(m.group("rest").strip()):
            score[m.group("prefix")] += 1
    forms, fgt_total, fgt_score = _fortigate_forms(lines)
    xr, xr_total, xr_score = _xr_forms(lines)
    br, br_total, br_score = _bracket_forms(lines)
    asa, asa_total, asa_score = _asa_forms(lines)
    ax, ax_total, ax_score = _ax_forms(lines)
    ranked = sorted(freq, key=lambda p: (score[p], freq[p]), reverse=True)
    # 設定モードのプロンプト(sw1(config)#)は、同じ機器の通常のプロンプト(sw1#)が候補にあるときは、まず除いて順位を付ける。
    # 設定の行にもコマンドの形が続くので、そのままだと設定モードの形が通常のプロンプトを押しのけてしまう(ADR-0201 決定3)。
    # 除いたもので決まらなければ、全部の候補で今までどおり決める
    plain_hosts = {_host(p) for p in freq if _mode_base(_host(p)) is None}
    plain_ranked = [p for p in ranked if _mode_base(_host(p)) not in plain_hosts]
    for candidates in (plain_ranked, ranked):
        for p in candidates:
            if score[p] >= 1 and freq[p] >= 2:
                host = _host(p)
                if not host:
                    return (p,), "general"
                adopted = [p, *(q for q in freq if q != p and _host(q) == host)]
                adopted += [q for q in _by_count(forms.get(host, collections.Counter())) if q not in adopted]
                # 同じホスト名の、頭に `!` の付いた AX の形(未保存の印)も足す(決定者の回答による。ADR-0134 決定3 の足し方と同じ)
                adopted += [q for q in _ax_bang_forms(ax, host) if q not in adopted]
                return tuple(adopted), "general"
    hosts = sorted(fgt_total, key=lambda h: (fgt_score[h], fgt_total[h]), reverse=True)
    for host in hosts:
        if fgt_score[host] >= 1 and fgt_total[host] >= 2:
            return tuple(_by_count(forms[host])), "fortigate"
    xr_hosts = sorted(xr_total, key=lambda h: (xr_score[h], xr_total[h]), reverse=True)
    for host in xr_hosts:
        if xr_score[host] >= 1 and xr_total[host] >= 2:
            return tuple(_by_count(xr[host])), "xr"
    br_hosts = sorted(br_total, key=lambda h: (br_score[h], br_total[h]), reverse=True)
    for host in br_hosts:
        if br_score[host] >= 1 and br_total[host] >= 2:
            return tuple(_by_count(br[host])), "bracket"
    asa_hosts = sorted(asa_total, key=lambda h: (asa_score[h], asa_total[h]), reverse=True)
    for host in asa_hosts:
        if asa_score[host] >= 1 and asa_total[host] >= 2:
            return tuple(_by_count(asa[host])), "asa"
    ax_hosts = sorted(ax_total, key=lambda h: (ax_score[h], ax_total[h]), reverse=True)
    for host in ax_hosts:
        if ax_score[host] >= 1 and ax_total[host] >= 2:
            return tuple(_by_count(ax[host])), "ax"
    return _detect_single_prompt(lines, freq, forms, xr, br, asa, ax)


def _detect_single_prompt(
    lines: list[str],
    freq: collections.Counter,
    forms: dict[str, collections.Counter],
    xr: dict[str, collections.Counter],
    br: dict[str, collections.Counter],
    asa: dict[str, collections.Counter],
    ax: dict[str, collections.Counter],
) -> tuple[tuple[str, ...], str]:
    """プロンプトが 1 回だけのログ(ADR-0201 決定5)。最初の空でない行がプロンプト+出力が空でも残す語のコマンドなら採る。

    一般の候補を先に見て、次に FortiGate の形(`[` で始まるホスト名を除く)、XR の形、括弧の形、ASA の形、最後に AX の形を見る。同じホスト名の候補を後ろに足す。
    XR の形・ASA の形では、設定モードなら頭の `do ` を外してから頭の語を見る(ADR-0211 決定7・ADR-0214 決定7)。
    AX の形では、後ろの頭の `$` を外してから頭の語を見る(ADR-0207 決定2)。
    (プロンプトの組, 規則) を返す。XR の形で決まったときの規則は single-xr、ASA の形は single-asa、ほかは single-other、決まらなければ none。
    """
    first = next((line for line in lines if line.strip()), None)
    if first is None:
        return (), "none"
    m = CANDIDATE.match(first)
    if m and _within_limit(m.group("prefix")):
        prefix = m.group("prefix")
        host = _host(prefix)
        if host and _host_head_ok(host) and _first_word(m.group("rest")) in KEEP_EMPTY_COMMANDS:
            adopted = [prefix, *(q for q in freq if q != prefix and _host(q) == host)]
            adopted += [q for q in _ax_bang_forms(ax, host) if q not in adopted]
            return tuple(adopted), "single-other"
    m = _fortigate_match(first)
    if m and _first_word(m.group("rest")) in KEEP_EMPTY_COMMANDS:
        host = m.group("host")
        prefix = m.group("prefix")
        return (prefix, *(q for q in _by_count(forms.get(host, collections.Counter())) if q != prefix)), "single-other"
    m = _xr_match(first)
    if m and _first_word(_xr_command(m)) in KEEP_EMPTY_COMMANDS:
        prefix = m.group("prefix")
        return (prefix, *(q for q in _by_count(xr.get(m.group("host"), collections.Counter())) if q != prefix)), "single-xr"
    m = BRACKET_PROMPT.match(first)
    if m and _first_word(m.group("rest")) in KEEP_EMPTY_COMMANDS:
        prefix = m.group("prefix")
        return (prefix, *(q for q in _by_count(br.get(m.group("host"), collections.Counter())) if q != prefix)), "single-other"
    m = ASA_PROMPT.match(first)
    if m and _first_word(_asa_command(m)) in KEEP_EMPTY_COMMANDS:
        prefix = m.group("prefix")
        return (prefix, *(q for q in _by_count(asa.get(m.group("host"), collections.Counter())) if q != prefix)), "single-asa"
    m = AX_PROMPT.match(first)
    if m and _host_head_ok(m.group("host")) and _first_word(_strip_dollar(m.group("rest"))) in KEEP_EMPTY_COMMANDS:
        prefix = m.group("prefix")
        return (prefix, *(q for q in _by_count(ax.get(m.group("host"), collections.Counter())) if q != prefix)), "single-other"
    return (), "none"


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


def _plain_base_rest(line: str, bases_by_length: list[str]) -> str | None:
    """行が `<B>#` か `<B>>` で始まり、その次が空白か行の終わりなら、後ろの部分を返す。当たらなければ None。

    B はモードを外したホスト名(ADR-0209 決定7 の2)。`Router2# …`・`Router#show` は当たらない。
    """
    for b in bases_by_length:
        if line.startswith(b) and line[len(b) : len(b) + 1] in ("#", ">"):
            rest = line[len(b) + 1 :]
            if not rest or rest[0].isspace():
                return rest
    return None


def _is_xr_mode_prompt(prompt: str) -> bool:
    """XR の形で、設定モードのプロンプトか(`mode` があり、プロンプトの後ろが空)。"""
    m = _xr_match(prompt)
    return bool(m and m.group("mode") and not m.group("rest"))


def _is_asa_mode_prompt(prompt: str) -> bool:
    """ASA の形で、設定モードのプロンプトか(`mode` があり、プロンプトの後ろが空)。"""
    m = ASA_PROMPT.match(prompt)
    return bool(m and m.group("mode") and not m.group("rest"))


def split_log(text: str, prompt: str | Sequence[str] | None = None) -> SplitResult:
    """端末ログを区間に切り分ける。prompt を渡すと自己校正をせずそれを使う(1つでも複数でもよい)。"""
    text, marks = _strip_pager(text)
    lines = _clean_lines(text)
    prompts: tuple[str, ...] = ((prompt,) if prompt else ()) if isinstance(prompt, str) else tuple(p for p in (prompt or ()) if p)
    source: PromptSource = "manual" if prompts else "auto"
    # 設定モードの行の頭の `do ` を外す対象にする規則(XR・ASA の形で自分で決めたときと、手で渡されたときだけ。ADR-0211 決定7・ADR-0214 決定7)
    strip_do_ok = source == "manual"
    if not prompts:
        prompts, rule = _detect(lines)
        strip_do_ok = rule in ("xr", "single-xr", "asa", "single-asa")
    if not prompts:
        # 特定できなければ切り分けない。1ファイル=1区間(現在の動作への縮退)
        pre, body = _finish(lines)
        return SplitResult((), "none", (Segment(1, None, pre, body, len(body)),), marks)
    # 複数が一致するときは最長のものでコマンドとの境目を決める
    by_length = sorted(prompts, key=len, reverse=True)
    # 設定モードのプロンプト(sw1(config)#)で区切るための、使うプロンプトのホスト名(ADR-0201 決定3)。
    # 採ったホスト名が設定モードの形(`Router(config)`)なら、モードを外したホスト名(`Router`)も足す。
    # これで `Router(config-GigaEthernet0.0)# …` も同じ機器の設定モードのプロンプトとして区切れる(ADR-0209 決定7 の1)
    hosts = {h for h in (_host(p) for p in prompts) if h}
    bases = {b for b in (_mode_base(h) for h in hosts) if b}
    hosts |= bases
    # 長いホスト名から順に見る(`Router#` の判定で、より長い名前を先に当てるため)
    bases_by_length = sorted(bases, key=len, reverse=True)
    # XR・ASA の形で設定モードのプロンプト(`...(config-if)#`)。この行のコマンドは頭の `do ` を外す
    do_mode_prompts = frozenset(p for p in prompts if strip_do_ok and (_is_xr_mode_prompt(p) or _is_asa_mode_prompt(p)))

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
            c = _strip_dollar(_display_command(line[len(hit) :]))
            if hit in do_mode_prompts:
                c = _strip_do(c)
            if c:
                cmd = c
            continue
        mode = CONFIG_MODE_PROMPT.match(line)
        if mode and mode.group("host") in hosts:
            flush()
            c = _strip_dollar(_display_command(mode.group("rest")))
            c = _strip_do(c)
            if c:
                cmd = c
            continue
        plain_rest = _plain_base_rest(line, bases_by_length)
        if plain_rest is not None:
            # 設定モードを抜けた通常のプロンプト(`Router# `・`Router> `。ADR-0209 決定7 の2)
            flush()
            c = _strip_dollar(_display_command(plain_rest))
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
        elif name and _first_word(name) in KEEP_EMPTY_COMMANDS:
            # 出力が空でも、見るためのコマンドの区間は残す(ADR-0201 決定4)
            segments.append(Segment(len(segments) + 1, name, pre, (), 0))
    return SplitResult(prompts, source, tuple(segments), marks)
