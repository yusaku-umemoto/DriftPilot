"""LLM 解析の要求と結果の型(ADR-0018 決定4〜7、ADR-0019 決定5)。

結果(LlmAnalysis)は CompareOutcome の別のフィールドに入る注釈で、差分の件数も内容も変えない。
送らなかった行は件数と行頭の形だけを持ち、値は持たない。記号(`<secret:N>`)と値の対応表は持たない。
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from driftpilot.llm_analyzer.client import LLMClient

if TYPE_CHECKING:  # api_error は redact → payload → types を読む(循環を避けて型の注記だけ)
    from driftpilot.llm_analyzer.api_error import ApiErrorText

# 値の元は driftpilot.llm_constants(画面のラベル「1 組あたり（既定 100 件）」も同じ値から作る。ADR-0167)
from driftpilot.llm_constants import DEFAULT_MAX_DIFFS

LlmStatus = Literal["done", "skipped"]
# 未実施の理由。分岐には status を使い、reason は表示用(docs/notes/req-13-core.md の列挙の区分)
LlmReason = Literal[
    "disabled",  # LLM 解析を頼まれていない
    "dry_run",  # 送信内容の確認だけ(送らない)
    "no_diff",  # 差分が無い(LLM を呼ばない)
    "no_structured_diff",  # 構造化比較が動いて 0 件(順番の違いなど)。行の差分はあっても送らない(ADR-0080 決定3)
    "nothing_to_send",  # 差分はあるが、送れる行が1件も無い(すべて判定不能)
    "no_api_key",  # API キーが無い
    "client_unavailable",  # 接続の実装が使えない(openai が入っていない)
    "not_configured",  # モデルが設定されていない
    "placeholder_base_url",  # 接続先に書き換える目印(<リソース名> など)が残っている(REQ-100 §5)
    "cli_not_found",  # CLI の形式で、CLI のプログラムが見つからない(REQ-102)
    "connection_error",  # 通信できない(タイムアウトを含む)
    "api_error",  # プロバイダがエラーを返した、または回答の形が違う
    "internal_error",  # 伏せる処理などの想定外の失敗(比較は止めない)
    # 一括比較の2段の送信(ADR-0031)。どれも送っていない
    "not_approved",  # 送信前の確認で送らないことにした(チェックを外した・「送らない」を選んだ)
    "changed_after_approval",  # 送ろうとした内容が、確認で承認した内容と違った
    "gave_up",  # API の失敗が続いたため、残りの組では呼ばなかった(ADR-0031 決定7)
    "over_total_limit",  # 送る差分の合計が全体の上限を超えた(ADR-0031 決定4)
    "cancelled",  # 送る前に中止した。transmitted が真なら、送った後に答えの途中で中止した(ADR-0157)
    "compare_failed",  # 比較の結果が失敗の組。送る内容ができていても送らない(REQ-27 §A-8b)
    "platform_conflict",  # 機種が自動で、変更前と変更後の判定が違った。送らない(ADR-0151 決定2)
    # 機種に Si-R を指定して、中身が FortiGate か Cisco 系と判定された。送らない(ADR-0161 の追記。REQ-222)。
    # core が送る文を作らずに結果へ直接入れるので、UnavailableReason には無い
    "platform_not_sir",
    # 「差分＋全文を送る」で、変更後の全文か全体が上限を超えた。全文も差分も送っていない(ADR-0158 決定5)
    "full_text_too_long",
    # 毎回付ける前提の文に、伏せる対象か送れない行がある。送る文を作らず、送っていない(ADR-0172 決定4)。
    # core が送る文を作らずに結果へ直接入れるので、UnavailableReason には無い
    "premise_refused",
    # 比較の窓で全部の組をまとめて1回で送った。答えは本文の最初の組の行にある(ADR-0152 決定7。保存と出力の形だけで使う)
    "in_whole",
]
# client を作れない・渡さないときの理由(LlmRequest.unavailable)。not_approved と gave_up は一括比較が組ごとに入れ、
# compare_failed は skip_failed の要求で比較が失敗したときに core が入れる。
# platform_conflict は機種が自動で両側の判定が違ったときに core が入れる(ADR-0151 決定2)
UnavailableReason = Literal[
    "no_api_key",
    "client_unavailable",
    "not_configured",
    "placeholder_base_url",
    "cli_not_found",
    "not_approved",
    "gave_up",
    "compare_failed",
    "platform_conflict",
]
# whole は比較の窓で全部の組をまとめて1回で送った解析(ADR-0152 決定3)
LlmSource = Literal["structured", "raw", "show", "whole"]
UnsentKind = Literal["line", "pem_block"]


@dataclass(frozen=True)
class LlmRequest:
    """入口が組み立てて core に渡す。client を作れなかったときは unavailable に理由を入れる。

    core は設定もキーも読まない(ADR-0015 決定5)。client は入口が作ったものをそのまま使う。
    """

    client: LLMClient | None = None
    unavailable: UnavailableReason | None = None
    max_diffs: int = DEFAULT_MAX_DIFFS
    dry_run: bool = False
    extra_secret_words: tuple[str, ...] = ()
    # 比較の結果が失敗(status=error)なら送らない(compare_failed)。一括比較の送る段で使う(REQ-27 §A-8b)
    skip_failed: bool = False
    # 指示文のひな形(利用者ごとの設定ファイルの [llm].prompt_template。None なら既定の文。ADR-0075)
    prompt_template: str | None = None
    # 利用者が解析の前に打った文(比較の画面の AI の面。REQ-206 / ADR-0148 決定6)。指示文の後ろに見出しを付けて足す。
    # None なら足さない(CLI・一括比較・wx・自動送信は足さない)。入口が check_question で確かめてから入れる
    extra_instruction: str | None = None
    # 変更後の全文も送る(比較の窓の「差分＋全文を送る」。ADR-0158)。偽なら今と同じ文。CLI・一括比較・wx・自動送信は付けない。
    # show の比較でも足す(ADR-0182 決定2)
    include_after_text: bool = False
    # 利用者が毎回付ける前提の文(llm-premise.txt。ADR-0172)。None なら付けない。入口が設定から入れ(build_request)、
    # core が check_question で確かめてから、指示文と打った文の間に入れる。表示しない(中身を repr に出さない)
    premise: str | None = field(default=None, repr=False)
    # 届いたところから答えの文字を受け取る(比較の窓。ADR-0157 決定5)。None なら今の ask で最後にまとめて受け取る。
    # 比べるとき・表示するときには使わない
    on_delta: Callable[[str], None] | None = field(default=None, compare=False, repr=False)
    # 届いたところから AI の考えの文字を受け取る(比較の窓。ADR-0169 決定1)。接続が streams_thinking を持つときだけ届く。
    # 比べるとき・表示するときには使わない。結果(LlmAnalysis)には入れない
    on_thinking: Callable[[str], None] | None = field(default=None, compare=False, repr=False)
    # API が返したエラーの文と待つ秒を受け取る(比較の窓の AI の面と CLI の compare --llm の標準エラー。ADR-0168 決定5)。
    # 利用者の秘密の語でもう一度掃除した後の値(api_error.for_display)だけが届く。結果(LlmAnalysis)には入れない
    on_api_error: "Callable[[ApiErrorText], None] | None" = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        # 無制限の経路を作らない(0 や負の数を受け付けない)
        if type(self.max_diffs) is not int or self.max_diffs < 1:
            raise ValueError(f"max_diffs は 1 以上の整数にしてください: {self.max_diffs!r}")
        if self.prompt_template is not None:
            from driftpilot.llm_analyzer.prompt import check_prompt_template  # prompt がこのモジュールを読むため

            problem = check_prompt_template(self.prompt_template)
            if problem is not None:
                # 文にひな形の中身を入れない(理由の種類だけ)
                raise ValueError(f"prompt_template が使えません: {problem}")
        if self.extra_instruction is not None and (
            not isinstance(self.extra_instruction, str) or not self.extra_instruction.strip()
        ):
            # 文に中身を入れない
            raise ValueError("extra_instruction は空でない文字列にしてください")
        if self.premise is not None:
            # 上限は追加の質問の1つの上限と同じ値(premise.PREMISE_MAX_CHARS。core は premise を読まないので follow_up から取る)
            from driftpilot.llm_analyzer.follow_up import MAX_QUESTION_CHARS  # follow_up がこのモジュールを読むため

            if not isinstance(self.premise, str) or not self.premise.strip() or len(self.premise) > MAX_QUESTION_CHARS:
                # 文に中身を入れない
                raise ValueError(f"premise は空でない {MAX_QUESTION_CHARS} 文字以下の文字列にしてください")
        if not self.dry_run and self.client is None and self.unavailable is None:
            raise ValueError("client が無いときは unavailable に理由を入れてください")


@dataclass(frozen=True)
class UnsentShape:
    """送らなかった行の形1つ。shape は行頭から最初の秘密語までで、値を含まない。

    kind が pem_block のときは、shape はブロックの開始行の形、count はブロック数、lines はブロックの行数の合計。
    """

    kind: UnsentKind
    scope: str
    shape: str
    count: int
    lines: int


@dataclass(frozen=True)
class Unsent:
    """送らなかったものの記録。

    lines:         送らなかった差分の行数(PEM ブロックの中の差分の行を含む)
    context_lines: 差分の前後の文脈・親の見出しのうち、送らなかった行数
    blocks:        送らなかった PEM ブロックの数
    shapes:        形ごとの件数(表に足す候補。ADR-0019 決定6)
    """

    lines: int = 0
    context_lines: int = 0
    blocks: int = 0
    shapes: list[UnsentShape] = field(default_factory=list)


@dataclass(frozen=True)
class LlmAnalysis:
    """LLM 解析の結果。status=skipped のときは reason に理由がある(黙って省略しない)。

    payload は送る内容(マスク後)。transmitted は実際に送ろうとしたか(dry_run や未実施では False)。
    detail はエラーの種類と HTTP の状態コードだけで、プロバイダの文面は持たない(キーの一部が載ることがあるため)。
    nondeterministic は常に True(同じ入力でも同じ回答にならない)。
    """

    status: LlmStatus
    reason: LlmReason | None = None
    detail: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    asked_at: str | None = None
    nondeterministic: bool = True
    answer: str | None = None
    transmitted: bool = False
    payload: str | None = None
    source: LlmSource | None = None
    diff_total: int | None = None
    sent_diffs: int | None = None
    max_diffs: int | None = None
    truncated: bool = False
    unsent: Unsent | None = None
    masking_fingerprint: str | None = None
    secret_words: list[str] | None = None
    prompt_version: str | None = None


DISABLED = LlmAnalysis(status="skipped", reason="disabled")
