"""LLM への指示文(ADR-0018 決定1・決定3・決定5、ADR-0075)。1回送って1回答えをもらうだけで、会話はしない。

指示文はひな形(DEFAULT_PROMPT_TEMPLATE)に印を差し込んで作る。利用者は指示文のプロファイル(prompt-profiles.json。
ADR-0079)でひな形を書き換えられる(ADR-0075 決定1。差分の本文の印 {{diff}} だけはちょうど1回要る)。
最初から入っているひな形を変えたら PROMPT_VERSION を上げる(保存した結果の回答がどの指示文で得られたかを区別するため)。
利用者のひな形のときは prompt_version_of が custom- とハッシュの先頭12桁を返す(ADR-0075 決定5。本文は結果に入れない)。
指示文に載せる数字は件数だけで、値は載せない。差し込みは1回の走査で行い、差分の本文の中の印の形の文字は置き換えない。
答えの見え方の決まり(LOOK_RULES)はひな形に入れず、本物の client に送る直前に後ろへ足す(ADR-0150。
llm_analyzer.look_rules)。見え方の決まりを直したときも PROMPT_VERSION を上げる。

英語の版(REQ-259 / ADR-0190 決定3): 最初から入っている指示文 3 つ・見え方の決まり・印に差し込む文・節の見出しは、
日本語の版(`…_JA`)と英語の版(`…_EN`)を持ち、読み込むときに `i18n.choose` で 1 つ選んで今の名前(DEFAULT_PROMPT_TEMPLATE・
LOOK_RULES など)に入れる。2 つの版は、行の並び・差し込みの印 {{…}}・`<secret:N>` の書き方・字数と件数が同じ。
利用者が作ったひな形は、言語に関わらず書かれたとおりに送る。

REQ-260: 差分の本文に DriftPilot が入れる印(`@@ …行目 @@`・区切りの行・最後の行など)も、言語で 1 つ選ぶ(llm_analyzer.payload)。
全部の組をまとめて送るときの決まり(WHOLE_RULES)が引く区切りの行の形は、その言語の本文の形と同じ。差分の形式の説明
(_SOURCE_LABEL)は印の文字を引かないので、そのまま。
"""

import hashlib
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from driftpilot import i18n
from driftpilot.llm_analyzer.types import LlmSource

# 印の値の元(画面の「使える印」の表と誤りの知らせも同じ並びで作る。ADR-0167)
from driftpilot.llm_constants import (
    MARK_DIFF,
    MARK_DIFF_FORMAT,
    MARK_DIFF_TOTAL,
    MARK_MAX_DIFFS,
    MARK_NOTES,
    MARK_SENT_RANGE,
    PROMPT_MARKS,
)

if TYPE_CHECKING:
    from driftpilot.llm_analyzer.payload import AfterText

# 最初から入っている指示文の版。日本語の版と英語の版は別の文なので、言語が分かる形にする(REQ-259)。
# 最初から入っている3つは後ろに -incident・-status を、全部の組をまとめて送るときはさらに -whole を付ける
PROMPT_VERSION_JA = "req259-ja-v1"
PROMPT_VERSION_EN = "req260-en-v1"  # REQ-260: 差分の本文の印と、全部の組の決まりが引く区切りの行を英語にした
PROMPT_VERSION = i18n.choose(PROMPT_VERSION_JA, PROMPT_VERSION_EN)
# 利用者のひな形の prompt_version の頭と、ハッシュの桁数(ADR-0075 決定5)
CUSTOM_PROMPT_PREFIX = "custom-"
CUSTOM_PROMPT_DIGITS = 12
# ひな形の長さの上限(誤って大きな文を貼ったときに止めるだけの値。ADR-0075 決定3)
PROMPT_TEMPLATE_MAX_CHARS = 20_000

# ひな形の印(ADR-0075 決定1。MARK_* と PROMPT_MARKS)は上で driftpilot.llm_constants から読む。{{diff}} だけが必須で、ちょうど1回
# 印を探す形(custom-http-json の本文のひな形と同じ)
_MARK = re.compile(r"\{\{([^{}]*)\}\}")

# {{diff_format}} に入る文。差分の種類の説明のあとに、読み違えやすい点の読み方を足す(ADR-0146 決定5。利用者の指示文にも効く)
_SOURCE_LABEL_JA: dict[LlmSource, str] = {
    "structured": (
        "構造化差分(1行が「[操作] カテゴリ 対象 / 項目: 変更前 -> 変更後」)"
        " 「変更前 -> 変更後」の順です。shutdown が true なら停止、false なら有効です。"
        "[added] と [removed] は、一覧の要素が1つ増えた・減ったことを表す場合があります。残りの要素は差分に出ません。"
        '[removed] … "telnet" -> null は telnet が外れたという意味で、項目全体が未設定になったとは限りません。'
    ),
    "raw": "行単位の差分(`-` は変更前にだけある行、`+` は変更後にだけある行、先頭が空白の行は前後の文脈。数字は行番号)",
    "show": (
        "show 出力の行の差分(1行が「[操作] キー: 列と値」)"
        ' [changed] の "列": ["A", "B"] は A が変更前、B が変更後です。'
    ),
}
_SOURCE_LABEL_EN: dict[LlmSource, str] = {
    "structured": (
        'Structured differences (one line is "[operation] category target / item: before -> after")'
        ' Read each in the order "before -> after". shutdown true means shut down, false means enabled. '
        "[added] and [removed] may mean that one element of a list was added or removed. The remaining elements do not appear in the differences. "
        '[removed] … "telnet" -> null means that telnet was removed, and does not always mean that the whole item became not set.'
    ),
    "raw": (
        "Line differences (`-` is a line only in the before side, `+` is a line only in the after side, "
        "a line starting with a space is the context around them. The numbers are line numbers)"
    ),
    "show": (
        'Differences of show output lines (one line is "[operation] key: columns and values")'
        ' In [changed], "column": ["A", "B"] means that A is before and B is after.'
    ),
}
_SOURCE_LABEL: dict[LlmSource, str] = i18n.choose(_SOURCE_LABEL_JA, _SOURCE_LABEL_EN)

# 答えの見え方の決まり(ADR-0150 決定1)。ひな形には入れず、本物の client に送る直前に後ろへ足す
# (look_rules.LookRulesClient。利用者のひな形にも足す)。送信前の確認・--llm-dry-run・結果の payload には出さない。
# 差し込みの印と <secret:N> は入れない。この文を変えたら PROMPT_VERSION を上げる
LOOK_RULES_JA = "\n".join(
    (
        "答えの見やすさのために、次の書き方だけを使ってください(画面で太字、赤字、表などとして表示されます)。",
        "- 1段落は1〜2文にする。話題が変わったら空行を入れて段落を分ける。",
        "- **太字**: 特に注意する語に使う。",
        "- <red>赤字</red>: 通信が止まる、管理できなくなる、守りが弱くなるなど、読む人が見落とすと困ることにだけ使う。短い語句を、全体で3か所まで。問題が無いときは使わない。",
        "- `コード`: コマンド、設定の行、設定の値に使う。",
        "- 表(| 列 | 列 |): 同じ項目で並べると読みやすいときに使う。1つの欄は30字以内。",
        "- 「- 」で始まる箇条書き。入れ子にしない。",
        "- 「### 」で始まる小見出し。短く、必要なときだけ。",
        "- ``` で囲むコマンドの塊。そのまま打てるものだけ。",
        "これ以外の装飾(HTML、絵文字、リンク)は使わない。",
    )
)
LOOK_RULES_EN = "\n".join(
    (
        "To make the answer easy to read, use only the following formats (they are displayed as bold text, red text, tables and so on on the screen).",
        "- Keep a paragraph to 1 or 2 sentences. When the topic changes, insert a blank line to start a new paragraph.",
        "- **Bold**: use for words that need special attention.",
        "- <red>Red text</red>: use only for things the reader would be in trouble for missing, such as communication stopping, management being lost or protection becoming weaker. Use short phrases, at most 3 places in total. Do not use it when there is no problem.",
        "- `Code`: use for commands, configuration lines and configuration values.",
        "- Table (| column | column |): use when listing the same items side by side is easier to read. Keep one cell within 30 characters.",
        '- Bulleted lists starting with "- ". Do not nest them.',
        '- Subheadings starting with "### ". Keep them short and use them only when needed.',
        "- Command blocks enclosed in ```. Only commands that can be typed as they are.",
        "Do not use other decoration (HTML, emoji, links).",
    )
)
LOOK_RULES = i18n.choose(LOOK_RULES_JA, LOOK_RULES_EN)


# 全部の組をまとめて1回で送るときの決まり(ADR-0152 決定5・決定6・決定9)。どのひな形でも build_prompt の後ろに足す。
# 答える内容の話なので、LOOK_RULES と違い送る文(payload)に入る(送信前の確認の全文に出る)。PROMPT_VERSION は変えず、
# prompt_version の後ろに WHOLE_VERSION_SUFFIX を付けて区別する。差し込みの印と <secret:N> は入れない。
# 区切りの行を指す所は、本文に出る形(payload.whole_header。日本語は「--- コマンド: … ---」、英語は「--- Command: … ---」)を
# 言語ごとにそのまま引く(REQ-260 / ADR-0190 決定3)。決まりが引く区切りの行の形は、テストが payload.whole_header と合うことを確かめる
WHOLE_SEPARATOR_QUOTE_JA = "--- コマンド: … ---"
WHOLE_SEPARATOR_QUOTE_EN = "--- Command: … ---"
WHOLE_SEPARATOR_QUOTE = i18n.choose(WHOLE_SEPARATOR_QUOTE_JA, WHOLE_SEPARATOR_QUOTE_EN)
WHOLE_RULES_JA = "\n".join(
    (
        f"差分は、1つのログの複数のコマンドをまとめたものです。「{WHOLE_SEPARATOR_QUOTE_JA}」が区切りです。全体で1つの答えにしてください。",
        "- 設定の差分(config の差分)と状態の差分(show の差分)があるときは、設定の変更が状態に現れているか、食い違いが無いかを書く。食い違いは <red>赤字</red> にする。",
        "- 稼働時間、カウンタ、日時のように時間で変わって当然の差分は、食い違いに数えず、書かない。稼働時間が短くなったときは、再起動やセッションの切断として書く。",
        "- 差分の無かったコマンドは、最後に名前だけを書いています。",
    )
)
WHOLE_RULES_EN = "\n".join(
    (
        f'The differences combine several commands of one log. A line like "{WHOLE_SEPARATOR_QUOTE_EN}" is the separator. Give one answer for the whole.',
        "- When there are both configuration differences (config differences) and status differences (show differences), write whether the configuration changes appear in the status and whether there is any mismatch. Make a mismatch <red>red text</red>.",
        "- Do not count differences that naturally change with time, such as uptime, counters and dates, as mismatches, and do not write them. When uptime became shorter, write it as a restart or a session disconnection.",
        "- Commands with no differences are listed by name only at the end.",
    )
)
WHOLE_RULES = i18n.choose(WHOLE_RULES_JA, WHOLE_RULES_EN)
WHOLE_VERSION_SUFFIX = "-whole"


def diff_format_text(sources: "Sequence[LlmSource]") -> str:
    """全部の組をまとめて送るときの {{diff_format}} の文。使った種類のラベルを、改行を入れずに空白でつなぐ(ADR-0152)。"""
    return " ".join(_SOURCE_LABEL[source] for source in sources)


def with_whole_rules(prompt: str) -> str:
    """指示文の後ろに、空行1つを挟んで WHOLE_RULES を足した文(ADR-0152 決定5)。"""
    return prompt + "\n\n" + WHOLE_RULES


def with_look_rules(prompt: str) -> str:
    """送る文の後ろに、空行1つを挟んで LOOK_RULES を足した文(ADR-0150 決定1)。"""
    return prompt + "\n\n" + LOOK_RULES


# 最初から入っている3つのひな形(ADR-0146 決定1)。役割・書き方の決まり(_PROMPT_HEAD)と、
# 「前提:」から差分の終わりまで(_PROMPT_TAIL)は3つで同じで、答え方の部分だけが違う。
# {{notes}} は知らせの各行の前に改行を付けた文なので、知らせが無いときに空行が増えない
_PROMPT_HEAD_JA = (
    "あなたはネットワーク機器の設定の差分を読む補助です。読む人はネットワーク担当者です。以下は DriftPilot が2つの設定を比べて出した差分です。",
    "書き方の決まり:",
    "- 日本語で書く。null、true、false、JSON、差分の項目の名前は写さず、「未設定」「停止」のように言い換える。",
    "- `<secret:N>` の記号は書かず、「パスワードが変わった」のように書く。",
    "- 同じことを2回書かない。差分から確かに言えないこと、どの変更にも当てはまる一般論は書かない。名前から意図を推測しない。",
    "- 前提に「送っていません」とあるときだけ、そのことを最後に1文で書く。全部送ったときは送った件数に触れない。",
)
_PROMPT_TAIL_JA = (
    "",
    "前提:",
    "- `<secret:N>` は認証情報を伏せた記号です。同じ番号は同じ値、違う番号は違う値です。値そのものは分かりません。",
    "- IP アドレス、ホスト名、インターフェース名、説明文は伏せていません。",
    "- 差分の形式: {{diff_format}}",
    "- 差分の全件数は {{diff_total}} 件です。送ったのは先頭から {{sent_range}} 件の範囲です(上限 {{max_diffs}} 件)。{{notes}}",
    "",
    "=== 差分 ===",
    "{{diff}}",
    "=== ここまで ===",
)
_PROMPT_HEAD_EN = (
    "You help read differences between network device configurations. The reader is a network engineer. Below are the differences that DriftPilot produced by comparing two configurations.",
    "Writing rules:",
    '- Write in English. Do not copy null, true, false, JSON or the item names of the differences; rephrase them, for example as "not set" or "shut down".',
    '- Do not write the `<secret:N>` marker; write, for example, "the password changed".',
    "- Do not write the same thing twice. Do not write what the differences do not clearly show, or generalities that fit any change. Do not guess the intent from names.",
    '- Only when the premises say "not sent", write that in one sentence at the end. When everything was sent, do not mention the number sent.',
)
_PROMPT_TAIL_EN = (
    "",
    "Premises:",
    "- `<secret:N>` is a marker for a hidden credential. The same number is the same value, and different numbers are different values. The value itself is not known.",
    "- IP addresses, host names, interface names and descriptions are not hidden.",
    "- Difference format: {{diff_format}}",
    "- The total number of differences is {{diff_total}}. The ones sent are in the range of the first {{sent_range}} (limit {{max_diffs}}).{{notes}}",
    "",
    "=== Differences ===",
    "{{diff}}",
    "=== End ===",
)
_PROMPT_HEAD = i18n.choose(_PROMPT_HEAD_JA, _PROMPT_HEAD_EN)
_PROMPT_TAIL = i18n.choose(_PROMPT_TAIL_JA, _PROMPT_TAIL_EN)


def _builtin_template(head: tuple[str, ...], tail: tuple[str, ...], *body: str) -> str:
    return "\n".join([*head, *body, *tail])


# 要点と影響(既定)の答え方
_DEFAULT_BODY_JA = (
    "差分の内容と、変更による影響を、次の形で答えてください。",
    "- 影響は、通信が止まる・管理できなくなる・守りが弱くなる可能性を考えて書く。段階(大・中・小など)は書かない。ポートやポリシーの停止、経路の変更は、そのおそれがあるものとして書く。アクセス制御、認証、暗号、管理の経路に関わる変更は、強くなったか弱くなったかも書く。適用先が差分に無いなど判断できないときは、そう書く。",
    "- 1行目に、何が変わったかを1文で書く。",
    "- 変更が1つなら、影響を1文で添えて終える(影響が無いなら添えない)。",
    "- 変更が2つ以上なら、続けて「変更・影響」の表を、影響のありそうな順に書く。各欄は30字以内で、1行目に書いたことを繰り返さない。",
    "- 空行や並び順だけで動作が変わらないときは、影響は無いと1文で書いて終える。ただし、ポリシーや ACL のように順番で動作が変わるものは、順番の変更として挙げる。",
    "- 通信が止まる、管理できなくなる、守りが弱くなるおそれがあるときだけ、最後に「**確かめること**」として、この差分に固有の確認を2つまで箇条書きにする。",
)
_DEFAULT_BODY_EN = (
    "Answer with the content of the differences and the impact of the change, in the following form.",
    "- Write the impact by considering the possibility that communication stops, that management is lost, or that protection becomes weaker. Do not write grades (high, medium, low and so on). Write port or policy shutdowns and route changes as things that may cause that. For changes that touch access control, authentication, encryption or management paths, also write whether they became stronger or weaker. When it cannot be judged, for example because the target of application is not in the differences, say so.",
    "- On the first line, write in one sentence what changed.",
    "- If there is one change, finish by adding the impact in one sentence (do not add it if there is no impact).",
    '- If there are two or more changes, follow with a "Change / Impact" table, in the order of likely impact. Keep each cell within 30 characters and do not repeat what the first line says.',
    "- If only blank lines or the order changed and the behavior does not change, finish by writing in one sentence that there is no impact. However, for things whose behavior changes with the order, such as policies and ACLs, list it as a change of order.",
    '- Only when communication may stop, management may be lost or protection may become weaker, end with "**Things to check**" and up to 2 bullets of checks specific to this difference.',
)
# 障害調査の答え方
_INCIDENT_BODY_JA = (
    "障害の原因を探している人に向けて答えてください。",
    "- 通信や冗長に影響しうる変更を、疑わしい順に3つまで、「順・変更・起きうる症状」の表にする。各欄は30字以内。",
    "- 表の後に、確かめるコマンドを ``` で囲んで、疑わしい順に1行ずつ書く。この機種のコマンドにする。",
    "- 残りの変更があるときだけ、「ほかの変更: 」に続けて短く並べる。",
    "- 影響しうる変更が無ければ、「障害の原因になりそうな変更はありません」のあとに変更の内容を1文で書いて終える。",
)
_INCIDENT_BODY_EN = (
    "Answer for someone who is looking for the cause of an incident.",
    '- Make a "Rank / Change / Possible symptom" table of up to 3 changes that may affect communication or redundancy, in the order of suspicion. Keep each cell within 30 characters.',
    "- After the table, write the commands to check, enclosed in ```, one line each in the order of suspicion. Use commands for this device model.",
    '- Only when other changes remain, list them briefly after "Other changes: ".',
    '- If no change may have an effect, write "No change looks like a cause of the incident" followed by one sentence about what changed, and finish.',
)
# 状態の確認(show の比較で、使用中が既定のときもこれで送る。ADR-0146 決定6)の答え方
_STATUS_BODY_JA = (
    "差分は、機器の状態を表示するコマンドの出力です。作業の前後を確かめる人に向けて答えてください。",
    "- 1行目に、悪くなったものや再起動があるか、無いかを1文で書く。あれば <red>赤字</red> にする。",
    "- 続けて「対象・変更前・変更後」の表を書く。悪くなったもの(down、Idle、err-disabled、経路数の減少、再起動など)を先に書く。同じ対象は1行にまとめる。",
    "- 項目の名前は「状態」「VLAN」「経路数」のように短く言い換える。機器の状態の語はそのまま書く。",
    "- リンクの状態に伴って変わっただけの項目(速度、デュプレックス)は省く。稼働時間やカウンタの増加のように変わって当然のものは省く。稼働時間が短くなったときは、再起動やセッションの切断として書く。",
    "- 表のあとの説明は、原因の手がかりになることだけを2行まで。小見出しは付けない。変わっていないものは表にも説明にも書かない。",
)
_STATUS_BODY_EN = (
    "The differences are the output of commands that display the status of the device. Answer for someone who is checking before and after the work.",
    "- On the first line, write in one sentence whether anything got worse or there was a restart, or not. If so, make it <red>red text</red>.",
    '- Then write a "Target / Before / After" table. Write the things that got worse (down, Idle, err-disabled, fewer routes, a restart and so on) first. Merge the same target into one row.',
    '- Rephrase item names briefly, such as "Status", "VLAN" and "Route count". Write device status words as they are.',
    "- Omit items that changed only along with the link state (speed, duplex). Omit things that naturally change, such as uptime and counter increases. When uptime became shorter, write it as a restart or a session disconnection.",
    "- After the table, give only clues to the cause, up to 2 lines. Do not add subheadings. Do not write what did not change, in the table or in the explanation.",
)

# 日本語の版と英語の版の3つのひな形(テストが両方を見る)。公開の名前には、今の言語の版を入れる
DEFAULT_PROMPT_TEMPLATE_JA = _builtin_template(_PROMPT_HEAD_JA, _PROMPT_TAIL_JA, *_DEFAULT_BODY_JA)
DEFAULT_PROMPT_TEMPLATE_EN = _builtin_template(_PROMPT_HEAD_EN, _PROMPT_TAIL_EN, *_DEFAULT_BODY_EN)
INCIDENT_PROMPT_TEMPLATE_JA = _builtin_template(_PROMPT_HEAD_JA, _PROMPT_TAIL_JA, *_INCIDENT_BODY_JA)
INCIDENT_PROMPT_TEMPLATE_EN = _builtin_template(_PROMPT_HEAD_EN, _PROMPT_TAIL_EN, *_INCIDENT_BODY_EN)
STATUS_PROMPT_TEMPLATE_JA = _builtin_template(_PROMPT_HEAD_JA, _PROMPT_TAIL_JA, *_STATUS_BODY_JA)
STATUS_PROMPT_TEMPLATE_EN = _builtin_template(_PROMPT_HEAD_EN, _PROMPT_TAIL_EN, *_STATUS_BODY_EN)

# 要点と影響(既定)
DEFAULT_PROMPT_TEMPLATE = i18n.choose(DEFAULT_PROMPT_TEMPLATE_JA, DEFAULT_PROMPT_TEMPLATE_EN)
# 障害調査
INCIDENT_PROMPT_TEMPLATE = i18n.choose(INCIDENT_PROMPT_TEMPLATE_JA, INCIDENT_PROMPT_TEMPLATE_EN)
# 状態の確認(show の比較で、使用中が既定のときもこれで送る。ADR-0146 決定6)
STATUS_PROMPT_TEMPLATE = i18n.choose(STATUS_PROMPT_TEMPLATE_JA, STATUS_PROMPT_TEMPLATE_EN)
# 最初から入っている本文の prompt_version(ADR-0079。本文を変えたら版を上げる)。今の言語の 3 つだけを持つ
BUILTIN_PROMPT_VERSIONS: dict[str, str] = {
    DEFAULT_PROMPT_TEMPLATE: PROMPT_VERSION,
    INCIDENT_PROMPT_TEMPLATE: f"{PROMPT_VERSION}-incident",
    STATUS_PROMPT_TEMPLATE: f"{PROMPT_VERSION}-status",
}


def template_for(template: str | None, source: LlmSource | None) -> str | None:
    """送るひな形。使用中が既定(None)で show の比較のときだけ「状態の確認」の本文にする(ADR-0146 決定6)。

    ほかの指示文を選んでいれば、show の比較でもそのまま使う。
    """
    if template is None and source == "show":
        return STATUS_PROMPT_TEMPLATE
    return template


def check_prompt_template(text: object) -> str | None:
    """ひな形に使えるか。問題が無ければ None、あれば理由の種類(文に中身を入れないため、種類だけを返す)。

    type: 文字列でない / too_long: 上限の文字数を超える / unknown_mark: 6つ以外の印がある /
    no_diff: {{diff}} が無い / repeated_diff: {{diff}} が2回以上ある
    """
    if not isinstance(text, str):
        return "type"
    if len(text) > PROMPT_TEMPLATE_MAX_CHARS:
        return "too_long"
    names = _MARK.findall(text)
    if any(name not in PROMPT_MARKS for name in names):
        return "unknown_mark"
    count = names.count(MARK_DIFF)
    if count == 0:
        return "no_diff"
    if count > 1:
        return "repeated_diff"
    return None


def normalize_newlines(text: str) -> str:
    """改行を \\n にそろえる(設定画面の欄・設定ファイルに書く前。TOML は読むときに \\r\\n を \\n にするため)。"""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def prompt_version_of(template: str | None) -> str:
    """結果の prompt_version。既定のひな形(None)は PROMPT_VERSION、最初から入っている本文はその版、
    利用者のひな形は custom- と SHA-256 の先頭12桁。"""
    if template is None:
        return PROMPT_VERSION
    builtin = BUILTIN_PROMPT_VERSIONS.get(template)
    if builtin is not None:
        return builtin
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()
    return f"{CUSTOM_PROMPT_PREFIX}{digest[:CUSTOM_PROMPT_DIGITS]}"


# {{notes}} に入る 2 つの知らせの形(rest は送らなかった件数、lines は送らなかった行数)
_NOTE_TRUNCATED_JA = "- 上限で打ち切ったため、残りの {rest} 件は送っていません。全体について断定せず、送った範囲について答えてください。"
_NOTE_TRUNCATED_EN = "- The remaining {rest} differences were not sent because the limit was reached. Do not draw conclusions about the whole; answer about the range that was sent."
_NOTE_UNSENT_JA = "- 認証情報を含むかどうか判定できなかった {lines} 行は送っていません。"
_NOTE_UNSENT_EN = "- {lines} lines for which it could not be judged whether they contain credentials were not sent."
_NOTE_TRUNCATED = i18n.choose(_NOTE_TRUNCATED_JA, _NOTE_TRUNCATED_EN)
_NOTE_UNSENT = i18n.choose(_NOTE_UNSENT_JA, _NOTE_UNSENT_EN)


def _notes(*, diff_total: int, max_diffs: int, truncated: bool, unsent_lines: int, omitted: int | None = None) -> str:
    """{{notes}} に入る文。知らせの各行の前に改行を付けてつなぐ(無ければ空文字)。

    omitted は打ち切って送らなかった件数(全部の組をまとめて送るときに組ごとの和を渡す。ADR-0152)。省けば diff_total - max_diffs。
    """
    lines = []
    if truncated:
        rest = diff_total - max_diffs if omitted is None else omitted
        lines.append(_NOTE_TRUNCATED.format(rest=rest))
    if unsent_lines:
        lines.append(_NOTE_UNSENT.format(lines=unsent_lines))
    return "".join(f"\n{line}" for line in lines)


# 利用者が解析の前に打った文の見出し(REQ-206 / ADR-0148 決定6)。指示文の後ろに、空行1つを挟んで足す
EXTRA_INSTRUCTION_HEADER = i18n.choose("利用者からの追加の指示:", "Additional instructions from the user:")


def with_extra_instruction(prompt: str, text: str | None) -> str:
    """指示文の後ろに、見出しの行と利用者の打った文を足した文。text が None なら prompt のまま。

    区切りは追加の質問の文(follow_up.build_follow_up)と同じく空行1つ。打った文は確かめた後の文をそのまま入れる
    (伏せる処理は通さない。入口が check_question で確かめる)。prompt_version は変えない。
    """
    if text is None:
        return prompt
    separator = "\n" if prompt.endswith("\n") else "\n\n"
    return f"{prompt}{separator}{EXTRA_INSTRUCTION_HEADER}\n{text}"


# 利用者が毎回付ける前提の文の節(ADR-0172 決定2)。ひな形・全文の節・WHOLE_RULES の後ろ、打った文の前に、空行1つを挟んで足す。
# ひな形・PROMPT_VERSION・prompt_version は変えない
PREMISE_HEADER = i18n.choose("=== 前提 ===", "=== Premises ===")
PREMISE_NOTE = i18n.choose(
    "次の文は、利用者が毎回付けている前提です。差分の説明に使ってください。",
    "The following text is a premise that the user attaches every time. Use it to explain the differences.",
)
PREMISE_FOOTER = i18n.choose("=== 前提ここまで ===", "=== End of premises ===")


def with_premise(prompt: str, text: str | None) -> str:
    """指示文の後ろに、前提の節(見出し・案内・前提の文・終わりの行)を足した文。text が None なら prompt のまま。

    区切りは with_extra_instruction と同じく空行1つ。前提は伏せる処理を通さずにそのまま入れる(core が check_question で
    確かめてから呼ぶ)。
    """
    if text is None:
        return prompt
    separator = "\n" if prompt.endswith("\n") else "\n\n"
    return f"{prompt}{separator}" + "\n".join([PREMISE_HEADER, PREMISE_NOTE, text, PREMISE_FOOTER])


# 変更後の全文の節(ADR-0158 決定4)。指示文(ひな形)の後ろに、空行1つを挟んで足す。ひな形と PROMPT_VERSION は変えない
AFTER_TEXT_FOOTER = i18n.choose("=== 全文ここまで ===", "=== End of full text ===")
AFTER_TEXT_NOTE = i18n.choose(
    "上の全文は参考です。差分の説明は差分をもとにし、関連する行の確認に全文を使ってください。",
    "The full text above is for reference. Base the explanation on the differences, and use the full text to check related lines.",
)
_AFTER_TEXT_HEADER = i18n.choose(
    "=== 変更後の全文（伏せた後。判定できなかった {omitted} 行は除いています）===",
    "=== Full text after the change (after masking. {omitted} lines that could not be judged are excluded) ===",
)


def after_text_header(omitted: int) -> str:
    """全文の節の見出しの行。omitted は判定できずに除いた行数(0 行でも書く)。"""
    return _AFTER_TEXT_HEADER.format(omitted=omitted)


def after_text_section(text: "AfterText") -> str:
    """変更後の全文の節(見出し・行・終わりの行・使い方の1文)。行は伏せた後の文のまま入れる。"""
    return "\n".join([after_text_header(text.omitted), text.body, AFTER_TEXT_FOOTER, AFTER_TEXT_NOTE])


def with_after_text(prompt: str, text: "AfterText") -> str:
    """指示文の後ろに、空行1つを挟んで全文の節を足した文(区切りは with_extra_instruction と同じ)。"""
    separator = "\n" if prompt.endswith("\n") else "\n\n"
    return f"{prompt}{separator}{after_text_section(text)}"


def build_prompt(
    *,
    source: LlmSource,
    body: str,
    diff_total: int,
    max_diffs: int,
    truncated: bool,
    unsent_lines: int,
    template: str | None = None,
    diff_format: str | None = None,
    sent_range: int | None = None,
    omitted: int | None = None,
) -> str:
    """ひな形(None なら既定)に差し込んだ指示文。使えないひな形は ValueError(文に中身を入れない)。

    diff_format・sent_range・omitted は全部の組をまとめて送るとき(ADR-0152)に、{{diff_format}} の文・{{sent_range}} の件数・
    打ち切って送らなかった件数を渡す。省けば今と同じ文(source のラベル・min(diff_total, max_diffs)・diff_total - max_diffs)。
    """
    if template is None:
        template = DEFAULT_PROMPT_TEMPLATE
    problem = check_prompt_template(template)
    if problem is not None:
        raise ValueError(f"指示文のひな形が使えません: {problem}")
    values = {
        MARK_DIFF: body,
        MARK_DIFF_FORMAT: _SOURCE_LABEL[source] if diff_format is None else diff_format,
        MARK_DIFF_TOTAL: str(diff_total),
        MARK_SENT_RANGE: str(min(diff_total, max_diffs) if sent_range is None else sent_range),
        MARK_MAX_DIFFS: str(max_diffs),
        MARK_NOTES: _notes(
            diff_total=diff_total, max_diffs=max_diffs, truncated=truncated, unsent_lines=unsent_lines, omitted=omitted
        ),
    }
    # 1回の走査で差し込む(差し込んだ値の中は見ない。差分の本文の {{diff_total}} のような文字は置き換えない)
    return _MARK.sub(lambda match: values[match.group(1)], template)
