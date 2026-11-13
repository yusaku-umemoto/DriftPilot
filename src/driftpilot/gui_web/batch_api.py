"""pywebview の一括比較画面の Python の側(REQ-106 / ADR-0074 決定3・ADR-0029 決定5〜9・ADR-0031)。

画面の状態(欄・組・結果・実行中か・待っている送信前の確認)はここに持ち、判断は screen_model/batch_model.py の関数を
呼ぶだけにする(wx の一括比較画面 gui/batch_frame.py の BatchFrame と同じ動き)。実行は screen_model/batch_worker.py の BatchWorker。
JavaScript(static/js/batch.js)は返した JSON を描くことと、操作をここへ渡すことだけをする。

- どのメソッドも {"ok": true, "view": {...}} を返す。知らせ(wx の MessageBox)は "notice" を足す。
  形の違う引数と想定外の例外は {"ok": false, "message": <文>}
- 選んだだけでは比較しない(ADR-0020)。組の一覧を出すだけ。欄に打っている途中は組を作らず、ステータスに案内を出す(REQ-97 §1)
- 組の一覧と結果は 1 つの表(REQ-211 / ADR-0153 決定1〜3。行は batch_model.table_rows)。表の行は view に入れない。
  view には pairs_revision・result_revision だけを入れ、画面は変わったときだけ result() を呼ぶ。行は組の順で、
  select_result と handoff の番号も組の順。タブの題と片方だけのファイルの見出しは Python が返す
- 比べるもの(form.source)のラジオは画面に置かない。「選ぶ…」と文字の欄の確定で、フォルダの欄なら folders、
  組のファイルの欄なら manifest にそろえる(画面の側も同じにする)。swap はフォルダの欄を入れ替えて組を作り直すだけで、実行はしない
- ワーカーのスレッドから状態を触るのはロックの中だけ。画面への知らせ {"type": "batch.changed"} はロックの外で送る
- 送信前の確認(ADR-0031): 全部の組をまとめて1回だけ。待っている確認があるときだけ答えを受け付け、1回答えたら消す。
  確認に出していない組は送らない。確認の間も門は持ったまま(比較の画面は走らせない。欄と切り替えも止める)。閉じたら送らずに門を返す
- 門(RunGate)は WebSession のロックの中で触る。門が変わったら gate_changed で比較の窓へ知らせる
- ファイルのダイアログは、ここから窓の create_file_dialog で開く(開いている間はロックを持たない)。
  pywebview のダイアログには題を渡す口が無い(wx の「変更前のフォルダを選ぶ」などは出せない)
- フォルダとマニフェストのドロップは _on_drop(app.watch_drop が窓の読み込みの後に印の要素(#drop-anchor)の drop に登録する。REQ-205・REQ-115 /
  ADR-0078 決定4)。パスは事象の dataTransfer.files[*].pywebviewFullPath だけから取り、画面からパスを受け取るメソッドは作らない。
  入れた後は「選ぶ…」と同じく組を作り直すだけで、実行は始めない。実行中・確認の待ち中は何も変えない
- pywebview は js_api の _ で始まらない属性をたどって画面に出すので、内部の属性とメソッドは _ で始める
- 組は窓の中で直せる(REQ-212 / ADR-0153 決定4〜8。ADR-0029 決定5 を改めた)。持ち方は screen_model/batch_edit.py の
  EditableBatch(self._edit)で、self._prepared は直すたびに to_prepared() で作り直す。門・ワーカー・確認の流れは変えない。
  組ごとの設定・全部の組の設定・組を外す・機種の選択・片方だけのファイルを組にする・解除・hostname の候補を受ける。
  どの編集も、実行中でも確認の待ち中でも閉じている途中でもないときだけ受ける(それ以外は何も変えない)。
  開いた組のファイルへは書き戻さない(「この組を保存…」は新しいファイルへ。上書きしない)。
  組の設定を直したら、その組の結果を消して待機の行にする(外す・戻すでは消さない)。
  直した組があるときの「組を作り直す」・「選ぶ…」・入れ替えは、Python が確かめ(confirm)を返し、画面が「はい」のときだけ
  confirmed=True で呼び直す。ドロップは確かめを出せないので、入力を変えずに知らせを出す
- 機種が自動の組(フォルダの組と手で組にした組の既定)は、platform を auto のまま比較の本体へ渡し、判定は run_compare が
  REQ-209 の決まりのまま 1 回だけ行う(判断の記録 1)。行の「自動: <判定した名前>」は結果の outcome.auto_platform から作る
- 画面の上の機種・比較方式・種類の表示は無くした(組の設定で直す)。form は source・before_dir・after_dir・manifest だけ
- AI の場所(REQ-213 / ADR-0153 決定9・10)。「LLM 解析」のチェックボックスは無くした(wx は残す)。「実行」は比較だけで、
  AI の設定を読まない(plan_llm も呼ばない)。AI へは実行の後に窓の下の「AI で解析」(send_llm)で送る。対象(_ai_targets)は、
  外していない組のうち、結果があり、失敗でなく、送る差分(payload.source_of)がある組(まとめて比べた組は、送れるコマンドの組の
  送る差分の合計がある組。組ごとに全体の文 1 つで送る。REQ-241)。対象の組だけで 1 段目(送らない)を走らせ、
  全部の組をまとめた送信前の確認を 1 回出し、「送る」でチェックした組だけ 2 段目で送る(ADR-0031 決定2 のまま)。
  「内容を確かめる…」(check_llm)は同じ手順で確認を出す(view.approval.check_only が真。send は None、close は「閉じる」)。
  答えが何でも送らず、1 段目の結果を捨てて前の結果と行に戻す
- AI の段の結果は、対象の組の分だけ入れる(対象でない組の結果と行は残す。件数の文は全部の組から作る)。2 段目で走った組は
  失敗も含めて置き換える。それ以外の組は、前の答え(llm.status が done)があれば前の結果のまま、無ければ今の理由(送らなかった・
  上限超えなど)を入れる
- 自動送信([gui] auto_send_llm)がオンなら、実行の結果を出して門を返した後に、AI で解析と同じ入口で確認のリストを開く。
  送るのは確認で「送る」を押したときだけ(確認を飛ばす経路は無い)。中止した実行と AI の段の終わりからは始めない。
  設定が読めなければ送らず、理由を view.ai.notes に出す
- view.ai は AI の場所の状態(題・プロファイルとプロンプトの {id, name} と使用中の id・押せるか・自動送信の文・注記)。
  接続先の URL・キー・CLI の場所は入れない。切り替え(use_ai_profile・use_prompt)は実行中と確認の待ち中は断る。
  settings_model の use_selected・use_prompt で使用中を変え、設定の窓が開いていれば読み直させる。結果と答えは消さない
- view.answer は選んだ組の答え {blocks, plain, text}(model.answer_blocks で読み取ったブロック・<red> を外したコピーの文・
  答えが無いときの理由の文)。頭の行(== LLM 解析 == など)は入れない(wx は answer_lines のまま)。追加の質問の欄は置かない(決定10)
- 「HTML・CSV で出力…」(export_result。ADR-0156 / REQ-216): 押せるのは実行中でも確認の待ち中でもなく結果があるとき(can_export)。
  組の順と外した組の扱いは「結果を保存…」と同じ。差分の行・組の名前・ファイル名は AI に送るときと同じ判定で伏せ
  (batch_model.batch_report)、新しいファイルに書く(上書きしない)。設定の秘密の語を読めなければ出さない
- 「AI に案を聞く…」(ask_pair_ai。REQ-219 / ADR-0159)。片方だけのファイルの名前(Path.name)だけを、毎回の送信前の確認
  (view.pair_ai_preview に送る全文・送り先・送らないファイル)の後に 1 回だけ送る。自動送信の設定は読まない。
  hostname の候補は作らず(出ている候補の行は消さない)、候補のファイルも含めて片方だけのファイルを全部送る(ADR-0177)。
  送らないのは、同じ側で名前が重なるファイル・秘密語の確かめで断った名前(制御文字を含む)だけ。
  この送信だけ、答えの見え方の決まり(ADR-0150)を足さない(送るのは確認に出した文だけ)。
  確認の間と送っている間は門を持つ(組を直す操作と切り替えは止まる)。送るのは確認に出した文そのもの(画面から文を受け取らない)。
  答えは決まった形のときだけ候補(source が ai。チェックの既定はオフ)に足し、組にするのは「選んだ候補を組にする」だけ。
  hostname の候補と同じ組は AI の行にしない。選んだ候補に同じファイルが 2 回あれば何も組にせず知らせる。
  答えの文は view・知らせ・ログに出さない。案と答えは保存しない。中止・窓を閉じた・一覧が変わった後に届いた答えは捨てる

webview は読まない(窓は WebSession.open_batch が開いて _attach で渡す。テストは偽の窓を渡す)。
"""

import threading
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from driftpilot.batch_io import ManifestWriteError, ResultsWriteError, read_hostname, write_new_bytes
from driftpilot.core import (
    AUTO_PLATFORM,
    BatchLlmPreview,
    BatchProgress,
    BatchSummary,
    Job,
    JobResult,
    LlmRequest,
    ManifestError,
    MultiCompareOutcome,
    combined_summary,
    gave_up_count,
    run_batch,
    whole_sections,
)
from driftpilot.gui_web.bridge import Api, _failure, _guarded, notify
from driftpilot.gui_web.drop_order import dropped_paths, order_pair
from driftpilot.gui_web.session import WebSession
from driftpilot.llm_analyzer import (
    MAX_PAIR_NAMES,
    LLMClient,
    LLMConnectionError,
    LLMResponseError,
    build_pair_prompt,
    read_pair_answer,
)
from driftpilot.llm_analyzer.follow_up import MAX_PROMPT_CHARS, prompt_too_long
from driftpilot.llm_analyzer.masking import normalize_words
from driftpilot.llm_analyzer.payload import source_of
from driftpilot.llm_settings import (
    WORKDIR_CONFIG,
    LlmSettings,
    SettingsError,
    auto_send_llm_enabled,
    auto_update_check_enabled,
    character_enabled,
    display_model,
    load_settings,
    premise_problem,
)
from driftpilot.messages import (
    BATCH_AI_REASON_APPROVAL,
    BATCH_AI_REASON_COMPARE,
    BATCH_AI_REASON_NO_TARGET,
    BATCH_AI_REASON_NOT_RUN,
    BATCH_AI_REASON_RUNNING,
    BAPI_SENTENCE_BREAK,
    BATCH_ALL_WAITING,
    BATCH_ALL_WAITING_TITLE,
    BATCH_APPROVAL_CLOSE,
    BATCH_APPROVAL_COL_JOB,
    BATCH_APPROVAL_COL_MASKED,
    BATCH_APPROVAL_COL_SENT,
    BATCH_APPROVAL_COL_UNSENT,
    BATCH_APPROVAL_DONT_SEND,
    BATCH_APPROVAL_ROWS,
    BATCH_APPROVAL_SAVE,
    BATCH_APPROVAL_SEND,
    BATCH_APPROVAL_TITLE,
    BATCH_AUTO_SEND_ON,
    BATCH_DROP_NO_PATH,
    BATCH_DROP_TITLE,
    BATCH_DROP_UNSUPPORTED,
    BATCH_FAILED_TITLE,
    BATCH_LLM_SETTINGS_TITLE,
    BATCH_NOT_SENT_TITLE,
    BATCH_PAIR_AI_ASKING,
    BATCH_PAIR_AI_HELD_BACK,
    BATCH_PAIR_AI_HINT,
    BATCH_PAIR_AI_NO_FILES,
    BATCH_PAIR_AI_NONE,
    BATCH_PAIR_AI_NOTE,
    BATCH_PAIR_AI_PROMPT_TITLE,
    BATCH_PAIR_AI_TITLE,
    BATCH_SAVED_TITLE,
    BATCH_DROP_EDITED,
    BATCH_REBUILD_CONFIRM_BODY,
    BATCH_REBUILD_CONFIRM_TITLE,
    BATCH_REBUILD_CONFIRM_YES,
    BATCH_SELECT_PROMPT,
    BATCH_SELECT_TITLE,
    BATCH_SETTINGS_REJECTED_TITLE,
    BATCH_SETTINGS_SELECT_PROMPT,
    BATCH_SETUP_FAILED_TITLE,
    BATCH_SUGGEST_OVERLAP,
    BATCH_SUGGEST_TITLE,
    BATCH_UNPAIR_SELECT_PROMPT,
    COMPARE_AI_PROFILE_FAILED_TITLE,
    COMPARE_PROMPT_FAILED_TITLE,
    COMPARE_SAVE_FAILED_TITLE,
    DIALOG_CANCEL,
    DIALOG_OK,
    EXPORT_REDACT_FAILED,
    auto_send_settings_unreadable,
    premise_refused_text,
    batch_ai_title,
    batch_hostname_counts,
    batch_hostname_hint,
    batch_hostname_none,
    batch_llm_gave_up,
    batch_manifest_saved_with,
    batch_pair_ai_destination,
    batch_pair_ai_failed,
    batch_pair_ai_partial,
    batch_pair_ai_too_long,
    batch_pair_ai_too_many,
    batch_pair_ai_unavailable,
    batch_pair_ai_unreadable,
    batch_payloads_saved,
    batch_platform_undetected,
    batch_progress_text,
    batch_rest_not_run,
    batch_run_label,
    batch_save_note,
    batch_saved_notice,
    batch_settings_rejected,
    export_saved,
    export_settings_unreadable,
    batch_handoff_note,
    gui_web_call_failed,
    llm_column,
    unpaired_side_line,
    user_prompt_template_ignored,
    workdir_api_key_ignored,
    workdir_limits_ignored,
)
from driftpilot.screen_model import batch_edit
from driftpilot.screen_model.batch_edit import EditableBatch
from driftpilot.screen_model.batch_model import (
    JSONL_WILDCARD,
    MANIFEST_WILDCARD,
    SOURCE_CHOICES,
    TEXT_WILDCARD,
    ApprovalView,
    BatchSetupError,
    BatchTableRow,
    PreparedBatch,
    after_first_stage,
    approval_view,
    batch_report,
    declined,
    effective_base_url,
    finish,
    folder_guide,
    handed_answer,
    handoff_for,
    limit_lines,
    manifest_guide,
    platform_undetected,
    prepare_folders,
    prepare_manifest,
    save_payloads,
    save_results,
    second_stage,
    status_counts_text,
    table_rows,
    table_titles,
    waiting_reason,
)
from driftpilot.screen_model.batch_worker import BatchWorker
from driftpilot.screen_model.model import (
    CANCELLED,
    raw_defaults,
    EXPORT_FILE_TYPES,
    EXPORT_KINDS,
    RedactError,
    WEB_PLATFORM_CHOICES,
    LlmPlan,
    answer_blocks,
    answer_copy_text,
    export_bytes,
    export_choices,
    export_filename,
    export_words,
    plan_llm,
    with_current_premise,
)
from driftpilot.screen_model.settings_model import (
    ProfileChoices,
    destination_of,
    llm_choices,
    prompt_choices,
    use_prompt,
    use_selected,
)

# Python から画面への知らせの種類。static/js/batch.js が受け、state() を呼び直す
CHANGED_EVENT = "batch.changed"
# 「設定…」で開けるタブ(省くと今と同じ。知らせの「更新…」は "update"。REQ-252 §5)
SETTINGS_TABS = (None, "update")
# 窓のファイルのダイアログの種類(webview.FileDialog の OPEN・FOLDER・SAVE の値。webview は読まない)
DIALOG_OPEN = 10
DIALOG_FOLDER = 20
DIALOG_SAVE = 30
# 選べるファイルの種類(wx の wildcard の「|」の前。pywebview の形「説明 (*.拡張子;…)」)
MANIFEST_FILE_TYPES = (MANIFEST_WILDCARD.split("|")[0],)
JSONL_FILE_TYPES = (JSONL_WILDCARD.split("|")[0],)
TEXT_FILE_TYPES = (TEXT_WILDCARD.split("|")[0],)
# ドロップでマニフェストとみなす拡張子(選ぶダイアログと同じ。casefold して比べる)
MANIFEST_SUFFIXES = tuple(pattern.lstrip("*").casefold() for pattern in MANIFEST_WILDCARD.split("|")[1].split(";"))
# 既定のファイルの名前(wx と同じ)
MANIFEST_FILENAME = "pairs.yaml"
RESULTS_FILENAME = "batch-results.jsonl"
PAYLOADS_FILENAME = "llm-payloads.txt"
SOURCE_VALUES = tuple(value for value, _ in SOURCE_CHOICES)
# 「選ぶ…」で入れる欄(フォルダ2つとマニフェスト)
PICK_FIELDS = ("before_dir", "after_dir", "manifest")
# 欄ごとの比べるもの(ラジオの代わり。REQ-211 §6)
FIELD_SOURCE = {"before_dir": "folders", "after_dir": "folders", "manifest": "manifest"}
# 画面の欄の値(form)の既定。機種・比較方式は組の設定で直す(REQ-212 §2)。AI のチェックボックスは無い(REQ-213 §2。
# 実行は比較だけで、AI へは「AI で解析」で送る)
DEFAULT_FORM: dict[str, Any] = {
    "source": SOURCE_VALUES[0],
    "before_dir": "",
    "after_dir": "",
    "manifest": "",
}
# AI の段の種類(_ai_action)。None は「実行」(比較だけ)
AI_SEND = "send"
AI_CHECK = "check"
# 答えの場所の空の形(AI の結果が無い組・選んでいないとき。画面は答えの場所を隠す)
EMPTY_ANSWER: dict[str, Any] = {"blocks": [], "plain": "", "text": None}
FORM_KEYS = tuple(DEFAULT_FORM)
# フォルダから作った組の機種を自動にするか(ADR-0153 決定8。wx の既定は指定なしのまま)
DEFAULT_FOLDER_AUTO = True
SIDES = ("before", "after")
# 候補の source。hostname の候補と、AI の案(REQ-219。同じ形で足す)
SUGGESTION_SOURCE = "hostname"
AI_SUGGESTION_SOURCE = "ai"


class _BadRequest(Exception):
    """画面から受け取った引数の形が違う。"""


class _PairAsking:
    """AI に組の案を聞いている間のワーカー(スレッド1本。compare_api の _Asking と同じ形)。送る文は持たない
    (スレッドに渡す関数のローカル変数だけ)。

    cancelled は「中止」の印(送った要求は止められないので、届いた答えを捨てる)。revision は送ったときの組の一覧の番号
    (違えば届いた答えを捨てる)。number は確認の番号(候補の id に使う)。status はステータスの元の文。
    """

    def __init__(self, run: Callable[["_PairAsking"], None], revision: int, number: int, status: str) -> None:
        self.cancelled = False
        self.revision = revision
        self.number = number
        self.status = status
        self._thread = threading.Thread(target=run, args=(self,), name="driftpilot-pair-ai", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()


def _run_at_once(fn: Callable[..., Any], *args: Any) -> None:
    """BatchWorker の post。wx の wx.CallAfter の代わりに、受け取った関数をそのまま(ワーカーのスレッドで)呼ぶ。"""
    fn(*args)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _multi_diff_count(multi: MultiCompareOutcome) -> int:
    """まとめて比べた組の送る差分の数。全体の文に入れられるコマンドの組(whole_sections)ごとの source_of の件数の合計。"""
    _positions, sections, _unchanged = whole_sections(multi)
    total = 0
    for section in sections:
        found = source_of(section.structured, section.raw, section.show)
        if found is not None:
            total += len(found[1])
    return total


def _choice_items(choices: ProfileChoices) -> list[dict[str, str]]:
    """切り替えの選択肢を画面に渡す形({id, name} の並び)。名前のほかは入れない。"""
    return [{"id": choice_id, "name": name} for choice_id, name in zip(choices.ids, choices.names)]


def _active_id(choices: ProfileChoices) -> str | None:
    """使用中の id(無ければ None)。"""
    return choices.ids[choices.active] if 0 <= choices.active < len(choices.ids) else None


class BatchApi(Api):
    """一括比較画面から呼べるメソッド(window.pywebview.api.<名前>)。REQ-103 の app_info・messages も使える。

    session は窓をまたいで共有する門と窓(WebSession)。
    cwd・env・platform・home は LLM の設定の読み元(テストで一時フォルダを渡す。省くと利用者の設定)。
    client_factory は LLM の client を作る関数(テストで偽物にする)。post は BatchWorker の戻し方、runner は一括比較の関数。
    """

    def __init__(
        self,
        session: WebSession,
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        platform: str | None = None,
        home: Path | None = None,
        client_factory: Callable[..., LLMClient] | None = None,
        post: Callable[..., Any] = _run_at_once,
        runner: Callable[..., BatchSummary] = run_batch,
    ) -> None:
        self._session = session
        self._where: dict[str, Any] = {"cwd": cwd, "env": env, "platform": platform, "home": home}
        self._client_factory = client_factory
        self._post = post
        self._runner = runner
        self._window: Any = None
        # 状態はここだけに持つ(batch_frame.py の属性と同じ)。js_api の呼び出しとワーカーのコールバックは別々のスレッドで動くので、
        # 触るのはロックの中だけ。ロックの順は、このロック → session.lock だけ(session からこの窓を呼ぶのはロックの外)
        self._lock = threading.RLock()
        self._form: dict[str, Any] = dict(DEFAULT_FORM)
        # 窓で直せる組(REQ-212)。_prepared は _edit.to_prepared() で作り直す
        self._edit: EditableBatch | None = None
        self._prepared: PreparedBatch | None = None
        # 片方だけのファイルのチェック(側ごとの一覧の番号)と、組の候補((id, source, 変更前, 変更後, 手がかりの文))。
        # source は hostname か ai(REQ-219)
        self._only_checked: dict[str, set[int]] = {side: set() for side in SIDES}
        self._suggestions: list[tuple[str, str, Path, Path, str]] = []
        # 片方だけのファイルと候補の折りたたみの初めの値(REQ-224 §5.2)。画面は revision が変わったときだけ open を入れる。
        # 前に描いたときの片方だけのファイルの数と候補の id(増えたかを比べる)
        self._folds: dict[str, dict[str, Any]] = {
            "unpaired": {"open": False, "revision": 0},
            "suggestions": {"open": False, "revision": 0},
        }
        self._fold_unpaired = 0
        self._fold_suggestion_ids: set[str] = set()
        # AI に組の案を聞く(REQ-219)。待っている確認: (番号, 送る文, (変更前の 名前→パス, 変更後の 名前→パス), 計画, 組の一覧の番号)。
        # 確認の画面(view.pair_ai_preview)と、送っている間のスレッド
        self._pair_ai: tuple[int, str, tuple[dict[str, Path], dict[str, Path]], LlmPlan, int] | None = None
        self._pair_ai_view: dict[str, Any] | None = None
        self._pair_asking: _PairAsking | None = None
        self._worker: BatchWorker | None = None
        self._closing = False
        # 実行の状態。results は表示中の最終の結果(job の id ごと)
        self._results: dict[str, JobResult] = {}
        self._first: list[JobResult] = []
        self._second: list[JobResult] = []
        self._first_summary: BatchSummary | None = None
        self._plan: LlmPlan | None = None
        self._settings: LlmSettings | None = None
        self._approved: dict[str, str] = {}
        # 一括比較の答えの送り先(ADR-0170 / REQ-232 §2): 2 段目で送っている送り先 (AI プロファイル (id, 名前), プロンプトの名前)と、
        # 答えが届いた組(job の id)ごとの送り先。「比較画面で開く」で答えと一緒に比較の窓へ渡す
        self._sending: tuple[tuple[str, str], str | None] | None = None
        self._answer_sources: dict[str, tuple[tuple[str, str], str | None]] = {}
        # AI の段の種類(None・AI_SEND・AI_CHECK)。AI の段の間も _results は始めたときのまま(終わったら対象の組の分だけ入れる)
        self._ai_action: str | None = None
        self._notes: list[str] = []  # AI の場所の下に出す注記(作業フォルダのキーの注記・自動送信で設定が読めない理由)
        # 表示(表の結果・実行中の組・件数の文・選んだ行・進み具合・ステータス・帯)。shown は job の id ごとの表示する結果
        self._shown: dict[str, JobResult] = {}
        self._running: str | None = None
        self._counts = ""
        self._selected = -1  # 表で選んだ行(組の順の番号。未選択は -1)。答えの場所はこの行の _results から作る
        self._progress = [0, 1]  # 値と最大
        self._progress_text = ""
        self._status = ""
        self._banner_shown = False  # 実行を始めたら隠し、終わっても隠したまま。組を作り直すとまた出す(REQ-97 §2)
        # 単純比較の既定のファイル(raw-compare.json)を読めないときの帯の文(REQ-228 §7.2)。次に組を作り直すまで残す
        self._raw_band: str | None = None
        # 待っている送信前の確認: (番号, 送る内容の全体, 確認の画面, 1段目の要約)
        self._approval: tuple[int, BatchLlmPreview, ApprovalView, BatchSummary] | None = None
        self._late_notice: dict[str, Any] | None = None  # ワーカーから戻った知らせ(画面は番号で1回だけ出す)
        # 番号(画面は変わったときだけ描き直す・入れ直す)
        self._revision = 0
        self._fill = 0  # 変わったら、画面は欄に form を入れ直す
        self._pairs_revision = 0
        self._result_revision = 0
        self._serial = 0  # 確認・知らせの番号
        self._character: bool | None = None  # キャラクターの設定(最初に描いたときに読む。ADR-0174 決定1)
        session.watch(self, self._changed)

    def _attach(self, window: Any) -> None:
        """pywebview の窓(知らせ・閉じる・ファイルのダイアログに使う)。WebSession.open_batch が渡す。"""
        self._window = window

    # --- 画面から呼ぶメソッド(form は画面の欄の値の dict) ---

    def state(self) -> dict[str, Any]:
        """今の状態。読むだけ。"""
        return self._call(self._state)

    def pick(self, form: Any, field: Any, confirmed: Any = False) -> dict[str, Any]:
        """「選ぶ…」。窓のダイアログで選んだフォルダかマニフェストを欄に入れ、組を作り直す。

        直した組があって confirmed が偽なら、ダイアログを開かずに確かめ(confirm)を返す(画面は「はい」で呼び直す)。
        """
        return self._call(self._pick, form, field, confirmed)

    def rebuild(self, form: Any, confirmed: Any = False) -> dict[str, Any]:
        """組を作り直す(文字の欄を確定したとき・「組を作り直す」)。比較はしない。全部の設定は既定に戻る。

        直した組があって confirmed が偽なら、作り直さずに確かめ(confirm)を返す。
        """
        return self._call(self._rebuild_call, form, confirmed)

    def run(self, form: Any) -> dict[str, Any]:
        """「実行(N 件)」。"""
        return self._call(self._run, form)

    def cancel(self) -> dict[str, Any]:
        """「中止」。いま読んでいる1件が終わってから止まる。"""
        return self._call(self._cancel)

    def send_llm(self, form: Any) -> dict[str, Any]:
        """「AI で解析」。対象の組だけで送らずに比べ、送信前の確認を 1 回出す(送るのは確認で「送る」を押したときだけ)。"""
        return self._call(self._begin_ai, form, AI_SEND)

    def check_llm(self, form: Any) -> dict[str, Any]:
        """「内容を確かめる…」。「AI で解析」と同じ確認を出すが、どう答えても送らない(check_only)。"""
        return self._call(self._begin_ai, form, AI_CHECK)

    def use_ai_profile(self, profile_id: Any) -> dict[str, Any]:
        """AI プロファイルの選択(profile_id は view.ai.profiles の id)。実行中と確認の待ち中は何も変えない。"""
        return self._call(self._use_profile, profile_id, "llm")

    def use_prompt(self, prompt_id: Any) -> dict[str, Any]:
        """プロンプトの選択(prompt_id は view.ai.prompts の id)。実行中と確認の待ち中は何も変えない。"""
        return self._call(self._use_profile, prompt_id, "prompt")

    def answer_approval(self, checked: Any, yes: Any) -> dict[str, Any]:
        """送信前の確認の答え。checked はチェックを残した組の id の list。「送る」(yes が真)のときだけ、その組を送る。

        「内容を確かめる…」の確認は、どう答えても送らない(画面は閉じたら [] と false を渡す)。
        """
        return self._call(self._answer_approval, checked, yes)

    def save_approval_payloads(self) -> dict[str, Any]:
        """送信前の確認の「内容をファイルに保存…」。確認は閉じない。"""
        return self._call(self._save_approval_payloads)

    def swap(self, form: Any, confirmed: Any = False) -> dict[str, Any]:
        """入れ替え(⇅)。変更前と変更後のフォルダの欄を入れ替えて組を作り直す。比較はしない(確かめは rebuild と同じ)。"""
        return self._call(self._swap, form, confirmed)

    # --- 組を直す(REQ-212。どれも実行中・確認の待ち中・閉じている途中は何も変えない。知らない job_id も何も変えない) ---

    def set_included(self, job_id: Any, on: Any) -> dict[str, Any]:
        """表の先頭のチェック。外した組は実行しない・保存に書かない。"""
        return self._call(self._set_included, job_id, on)

    def pair_settings(self, job_id: Any) -> dict[str, Any]:
        """「組の設定…」(job_id は選んだ行の組の id。空の文字は未選択)と「全部の組の設定…」(job_id が None)の中身。"""
        return self._call(self._pair_settings, job_id)

    def edit_pair(self, job_id: Any, values: Any) -> dict[str, Any]:
        """組の設定の「反映する」。断られたら知らせと、打った値で開き直す中身(reopen)を返し、組は直す前のまま。"""
        return self._call(self._edit_pair, job_id, values)

    def edit_defaults(self, values: Any) -> dict[str, Any]:
        """全部の組の設定の「反映する」。"""
        return self._call(self._edit_defaults, values)

    def pick_rules(self) -> dict[str, Any]:
        """組の設定の正規化の規則の「選ぶ…」。窓のダイアログで選んだファイルのパスを返すだけ(組は変えない)。"""
        return self._call(self._pick_rules)

    def set_platform(self, job_id: Any, index: Any) -> dict[str, Any]:
        """表の機種の選択(WEB_PLATFORM_CHOICES の番号。0 が自動)。種類が config の組だけ。"""
        return self._call(self._set_platform, job_id, index)

    def set_only_checked(self, side: Any, index: Any, on: Any) -> dict[str, Any]:
        """片方だけのファイルの行のチェック(side は before か after、index はその側の一覧の番号)。view.can_pair を返す。"""
        return self._call(self._set_only_checked, side, index, on)

    def pair_files(self) -> dict[str, Any]:
        """「選んだ 2 つを組にする」。Python が持っているチェックで組にする(パスは画面から受け取らない)。比較はしない。"""
        return self._call(self._pair_files)

    def pair_dropped(self, revision: Any, before_index: Any, after_index: Any) -> dict[str, Any]:
        """片方だけのファイルのドラッグ(REQ-233)。revision は描いたときの result の pairs_revision、番号は側ごとの一覧の番号。
        Python が持っている一覧で組にする(パスは画面から受け取らない)。比較はしない。"""
        return self._call(self._pair_dropped, revision, before_index, after_index)

    def set_included_many(self, job_ids: Any, on: Any) -> dict[str, Any]:
        """見出しの「全部」のチェック(ADR-0176 決定3)。job_ids は今のタブで見えている組の id の list、on は付けるか外すか。

        知らない id は飛ばす。結果は消さない(set_included と同じ)。
        """
        return self._call(self._set_included_many, job_ids, on)

    def unpair(self, job_ids: Any) -> dict[str, Any]:
        """「組を解除」。job_ids は表で選んだ組の id の list(ADR-0176 決定2)。組を消し、両方のファイルを片方だけの一覧へ戻す。

        全部か無しか(1 つでも解除できなければ何も変えずに知らせる)。
        """
        return self._call(self._unpair, job_ids)

    def suggest_hostnames(self) -> dict[str, Any]:
        """「hostname で候補を出す」。読むだけで組は変えない(候補は result() の suggestions)。"""
        return self._call(self._suggest_hostnames)

    def accept_suggestions(self, ids: Any) -> dict[str, Any]:
        """「選んだ候補を組にする」。ids は今の候補の id の list。"""
        return self._call(self._accept_suggestions, ids)

    def dismiss_suggestions(self) -> dict[str, Any]:
        """「候補を閉じる」。"""
        return self._call(self._dismiss_suggestions)

    def ask_pair_ai(self) -> dict[str, Any]:
        """「AI に案を聞く…」(REQ-219)。送らずに、送る全文の確認(view.pair_ai_preview)を出すだけ。"""
        return self._call(self._ask_pair_ai)

    def answer_pair_ai(self, number: Any, yes: Any) -> dict[str, Any]:
        """AI に案を聞く確認の答え。number は view.pair_ai_preview.id。「送る」(yes が True)のときだけ、確認に出した文を送る。"""
        return self._call(self._answer_pair_ai, number, yes)

    def result(self) -> dict[str, Any]:
        """1 つの表の行(組の順)と、対にならなかったファイルの行。"""
        return self._call(self._result)

    def select_result(self, index: Any) -> dict[str, Any]:
        """表で行を選んだとき。答えの場所の中身 {blocks, plain, text} を answer で返す(未選択は -1)。"""
        return self._call(self._select_result, index)

    def handoff(self, form: Any, index: Any) -> dict[str, Any]:
        """「比較画面で開く」と表の行のダブルクリック。index は表で選んだ行の番号(組の順。未選択は -1)。"""
        return self._call(self._handoff, form, index)

    def save_manifest(self, form: Any) -> dict[str, Any]:
        """「この組を保存…」。窓で直した組を新しいファイルへ書く(開いた組のファイルへは書き戻さない。上書きしない)。"""
        return self._call(self._save_manifest, form)

    def save_results(self, form: Any) -> dict[str, Any]:
        """「結果を保存…」。"""
        return self._call(self._save_results, form)

    def export_result(self, form: Any, kind: Any) -> dict[str, Any]:
        """「HTML・CSV で出力…」(ADR-0156)。kind は "html"・"csv"・"csv_pairs"。組の順は「結果を保存…」と同じ。

        秘密の行は AI に送るときと同じ判定で必ず伏せる(伏せずに出す口は無い)。上書きしない。
        """
        return self._call(self._export_result, form, kind)

    def open_settings(self, tab: Any = None) -> dict[str, Any]:
        """「設定…」(ADR-0176 決定4)。設定の窓を開く(開いていれば前に出す)。比較の窓の「設定…」と同じ口。

        tab は開くタブ(知らせの「更新…」は "update"。REQ-252 §5)。省けば今と同じ。None・"update" のほかは受け付けない。
        """
        return self._call(self._open_settings, tab)

    def dismiss_update_notice(self) -> dict[str, Any]:
        """新しい版の知らせの「閉じる」(REQ-252 §5)。その起動の間は、比較の窓にも一括比較の窓にも出さない。"""
        return self._call(self._dismiss_update_notice)

    def close(self) -> dict[str, Any]:
        """窓を閉じる。実行中なら中止してから。"""
        return self._call(self._close)

    # --- 呼び出しの共通 ---

    def _call(self, func: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            try:
                return func(*args)
            except _BadRequest:
                return _failure(gui_web_call_failed())

        return _guarded(run)

    def _ok(self, notice: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        result = {"ok": True, "view": self._render(), **extra}
        if notice is not None:
            result["notice"] = notice
        return result

    def _notice(self, body: str, title: str) -> dict[str, Any]:
        return {"title": title, "body": body, "ok": DIALOG_OK}

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def _checked_form(self, form: Any) -> dict[str, Any]:
        """画面の欄の値の形を確かめる(受け取らない)。形が違えば _BadRequest。"""
        if not isinstance(form, dict):
            raise _BadRequest
        source = form.get("source")
        if not isinstance(source, str) or source not in SOURCE_VALUES:
            raise _BadRequest
        if not all(isinstance(form.get(name), str) for name in PICK_FIELDS):
            raise _BadRequest
        return {name: form[name] for name in FORM_KEYS}

    def _take(self, form: Any) -> None:
        """画面の欄の値を受け取る。形が違えば _BadRequest。"""
        self._form = self._checked_form(form)

    def _changed(self) -> None:
        """画面に「状態が変わった」とだけ知らせる(ロックの外で呼ぶ)。窓が閉じた後の失敗は握りつぶす。"""
        window = self._window
        if window is None:
            return
        try:
            notify(window, {"type": CHANGED_EVENT})
        except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
            pass

    def _choose(self, kind: int, save_filename: str, file_types: tuple[str, ...]) -> Path | None:
        """窓のダイアログで選んだ場所。取り消した・窓が無いときは None。ロックの外で呼ぶ。

        選んだときは、Mac の保存のダイアログが文字列を、ほかは文字列の並びを返す(pywebview 6.2.1)。
        """
        window = self._window
        if window is None:
            return None
        chosen = window.create_file_dialog(
            kind, directory="", allow_multiple=False, save_filename=save_filename, file_types=file_types
        )
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return Path(chosen) if isinstance(chosen, str) and chosen else None

    # --- 状態(wx の BatchFrame と同じ判断) ---

    def _idle(self) -> bool:
        """実行中でなく、門を持っていない(送信前の確認を待っている間は門を持っている)。"""
        return self._worker is None and self._session.owner is not self

    def _table(self) -> list[BatchTableRow]:
        """ロックの中で呼ぶ。1 つの表の行(組の順)。"""
        jobs = self._prepared.jobs if self._prepared is not None else ()
        return table_rows(jobs, self._shown, self._running, self._excluded())

    def _excluded(self) -> frozenset[str]:
        return self._edit.excluded if self._edit is not None else frozenset()

    def _included_jobs(self) -> list[Job]:
        """ロックの中で呼ぶ。外していない組(組の順。実行に渡すのはこれだけ)。"""
        if self._prepared is None:
            return []
        excluded = self._excluded()
        return [job for job in self._prepared.jobs if job.id not in excluded]

    def _can_edit(self) -> bool:
        """ロックの中で呼ぶ。組を直してよいか(組があり、実行中でも確認の待ち中でも閉じている途中でもない)。"""
        return self._edit is not None and self._idle() and not self._closing

    def _only(self, side: str) -> tuple[Path, ...]:
        if self._edit is None:
            return ()
        return self._edit.only_before if side == "before" else self._edit.only_after

    def _can_pair(self) -> bool:
        """変更前だけ 1 つと変更後だけ 1 つが選ばれているときだけ。"""
        return self._can_edit() and all(len(self._only_checked[side]) == 1 for side in SIDES)

    def _can_ask_pair_ai(self) -> bool:
        """ロックの中で呼ぶ。「AI に案を聞く…」を押せるか(組を直せて、片方だけのファイルが両側にあり、比較の画面が走っていない)。"""
        return (
            self._can_edit()
            and bool(self._only("before"))
            and bool(self._only("after"))
            and self._session.free_for(self)
        )

    def _render(self) -> dict[str, Any]:
        """画面に描く状態。表の行は入れない(result() で渡す)。押せるかは wx の _refresh_controls と同じ条件。"""
        self._revision += 1
        idle = self._idle() and not self._closing
        folders = self._form["source"] == "folders"
        prepared = self._prepared
        jobs = len(prepared.jobs) if prepared is not None else 0
        included = len(self._included_jobs())
        can_edit = self._can_edit()
        worker = self._worker
        approval = None
        if self._approval is not None:
            number, _preview, shown, _summary = self._approval
            # 「内容を確かめる…」はボタンが 1 つの確認(送るボタンもチェックの列も出さない。保存はできる)
            check_only = self._ai_action == AI_CHECK
            approval = {
                "id": number,
                "title": BATCH_APPROVAL_TITLE,
                "summary": "\n".join(shown.summary),
                "limits": "\n".join(shown.limits) if shown.limits else None,
                "rows_title": BATCH_APPROVAL_ROWS,
                "columns": [BATCH_APPROVAL_COL_JOB, BATCH_APPROVAL_COL_SENT, BATCH_APPROVAL_COL_MASKED, BATCH_APPROVAL_COL_UNSENT],
                "rows": [asdict(row) for row in shown.rows],
                "save": BATCH_APPROVAL_SAVE,
                "save_note": batch_save_note(),
                "check_only": check_only,
                "send": None if check_only else BATCH_APPROVAL_SEND,
                "dont_send": None if check_only else BATCH_APPROVAL_DONT_SEND,
                "close": BATCH_APPROVAL_CLOSE if check_only else None,
            }
        return {
            "revision": self._revision,
            # 半角の括弧を含む文と、画面の論理(batch_model.py・model.py)の文。決まった文は data-text で messages から取る
            "labels": {
                "handoff_note": batch_handoff_note(),
                "save_note": batch_save_note(),
                # 「実行(N 件)」の N は外していない組の数(REQ-212 §4)
                "run": batch_run_label(included),
                # 表の機種の選択肢(先頭が自動。自動の組は先頭の文を行の文「自動: FortiGate」などにして出す)
                "row_platforms": [label for _value, label in WEB_PLATFORM_CHOICES],
                # 「HTML・CSV で出力…」の形の選択肢(HTML・差分の行・組の一覧・やめる。ADR-0156 決定1)
                "export_choices": export_choices(True),
            },
            "form": dict(self._form),
            "fill": self._fill,
            "folders": folders,
            "busy": worker is not None,
            "inputs_enabled": idle,
            "can_rebuild": idle,
            # 「実行」は外していない組があり、実行中でなく、比較の画面が走っていないときだけ(ADR-0029 決定9)
            "can_run": idle and included > 0 and self._session.free_for(self),
            # 組のファイルから開いた組も保存できる(ADR-0153 決定4)。全部外すと押せない(決定5)
            "can_save_manifest": idle and self._edit is not None and included > 0,
            "can_save_results": idle and any(job.id in self._results for job in self._included_jobs()),
            # 「HTML・CSV で出力…」(ADR-0156)。実行中でも確認の待ち中でもなく、結果があるとき
            "can_export": idle and bool(self._results),
            # AI に案を聞いている間も「中止」で届いた答えを捨てられる(REQ-219)
            "can_cancel": (worker is not None and not worker.cancel_requested)
            or (self._pair_asking is not None and not self._pair_asking.cancelled),
            # 組を直す(REQ-212 §3〜§7)。行を選ぶ必要がある操作は、選んでいないとき Python が知らせる
            "can_edit": can_edit and jobs > 0,
            "can_pair": self._can_pair(),
            "can_suggest": can_edit and bool(self._only("before")) and bool(self._only("after")),
            "has_suggestions": bool(self._suggestions),
            # 「AI に案を聞く…」(REQ-219)と、待っている送信前の確認(無ければ None)
            "can_ask_pair_ai": self._can_ask_pair_ai(),
            "pair_ai_preview": dict(self._pair_ai_view) if self._pair_ai_view is not None else None,
            # 比較の窓が無い(単独で開いた)ときは押せない
            "can_handoff": jobs > 0 and self._session.has_compare(),
            # 名前の列は、名前(label)のある組が1つ以上あるときだけ出す
            "has_labels": prepared is not None and any(job.label for job in prepared.jobs),
            "banner": self._banner_text(),
            # 窓の下の行に出す、単純比較の設定の要約と組ごとの設定の数(REQ-228 §7.2)。全部既定なら null
            "raw_settings_line": batch_edit.raw_settings_line(self._edit),
            # タブ(すべて・差分がある組)と片方だけのファイルの見出し。0 件でも出す(ADR-0020「必ず報告する」)
            "pairs_titles": table_titles(self._table(), len(prepared.unpaired_rows) if prepared is not None else 0),
            # 片方だけのファイルの左右 2 列の見出し(「変更前にしかない: N 件」の形。0 件でも出す。REQ-233 / ADR-0171 決定1)
            "unpaired_titles": {side: unpaired_side_line(side, len(self._only(side))) for side in SIDES},
            "pairs_revision": self._pairs_revision,
            # 片方だけのファイルと候補の折りたたみ(REQ-224 §5.2)。画面は revision が変わったときだけ open を入れる
            "folds": self._fold_states(),
            "progress": {"value": self._progress[0], "max": self._progress[1]},
            "progress_text": self._progress_text,
            "counts": self._counts,
            # 選んだ組の答え(REQ-213 §8)と AI の場所(§3)
            "answer": self._answer_view(),
            "ai": self._ai_view(),
            "result_revision": self._result_revision,
            "status": self._status,
            "approval": approval,
            "notice": self._late_notice,
            # キャラクターの絵を出すか(ADR-0174 決定1。この窓を最初に描いたときの設定)。偽なら絵の要素は hidden のまま
            "character": self._character_on(),
            # 起動時の絵を出すか(ADR-0174 決定2)。真にするのは --batch で最初に開いた窓の最初の state() だけ(_state)
            "splash": False,
            # 起動したときに見つけた新しい版の知らせの文(REQ-252 §5)。無ければ null。画面は textContent だけで描く
            "update_notice": self._session.update_notice_view(),
        }

    def _character_on(self) -> bool:
        """ロックの中で呼ぶ。キャラクターの設定。最初に描いたときに読み、この窓の間は変えない(ADR-0174 決定1)。"""
        if self._character is None:
            self._character = character_enabled(
                env=self._where["env"], platform=self._where["platform"], home=self._where["home"]
            )
        return self._character

    def _banner_text(self) -> str | None:
        """ロックの中で呼ぶ。帯の文(組の帯と、単純比較の既定のファイルを読めないときの帯。REQ-228 §7.2)。無ければ None。"""
        prepared = self._prepared
        banner = prepared.banner if prepared is not None and prepared.jobs and self._banner_shown else None
        if self._raw_band is None:
            return banner
        return "\n".join([*([banner] if banner else []), self._raw_band])

    def _fold_states(self) -> dict[str, dict[str, Any]]:
        """ロックの中で呼ぶ(_render から)。片方だけのファイルと候補の折りたたみの開け閉めの初めの値(REQ-224 §5.2)。

        片方だけのファイルの数が前より増えたら開き、0 件になったら閉じる。候補に前に無かった id が入ったら候補を開く。
        どれも番号(revision)を上げる。それ以外は変えない(人が開け閉めしたままにする)。
        """
        prepared = self._prepared
        unpaired = len(prepared.unpaired_rows) if prepared is not None else 0
        if unpaired > self._fold_unpaired:
            self._set_fold("unpaired", True)
        elif unpaired == 0 and self._fold_unpaired > 0:
            self._set_fold("unpaired", False)
        self._fold_unpaired = unpaired
        ids = {suggestion[0] for suggestion in self._suggestions}
        if ids - self._fold_suggestion_ids:
            self._set_fold("suggestions", True)
        self._fold_suggestion_ids = ids
        return {name: dict(state) for name, state in self._folds.items()}

    def _set_fold(self, name: str, open_: bool) -> None:
        """ロックの中で呼ぶ。折りたたみの開け閉めを決めて番号を上げる。"""
        self._folds[name] = {"open": open_, "revision": self._folds[name]["revision"] + 1}

    # --- AI の場所(REQ-213 / ADR-0153 決定9) ---

    def _ai_targets(self) -> list[tuple[Job, int]]:
        """ロックの中で呼ぶ。AI で解析する組と送る差分の数(組の順)。

        外していない組のうち、結果があり、比較が失敗でなく、送る差分(payload.source_of。構造化比較が動いて 0 件なら無い)がある組。
        まとめて比べた組(result.multi がある)は、全体の文に入れられるコマンドの組(whole_sections。失敗したコマンドの組は入らない)
        ごとの送る差分の数の合計。合計が 0 なら入れない(組の全体の状態が「失敗」でも、送れるコマンドの組があれば入れる。REQ-241 §2)。
        """
        targets = []
        for job in self._included_jobs():
            result = self._results.get(job.id)
            if result is None:
                continue
            if result.multi is not None:
                count = _multi_diff_count(result.multi)
                if count:
                    targets.append((job, count))
                continue
            if result.outcome.status == "error":
                continue
            outcome = result.outcome
            found = source_of(outcome.structured, outcome.raw, outcome.show)
            if found is not None:
                targets.append((job, len(found[1])))
        return targets

    def _can_ai(self) -> bool:
        """ロックの中で呼ぶ。「AI で解析」と「内容を確かめる…」を押せるか(自動送信も同じ条件)。"""
        return bool(self._ai_targets()) and self._idle() and not self._closing and self._session.free_for(self)

    def _ai_reason(self) -> str | None:
        """ロックの中で呼ぶ。「AI で解析」「内容を確かめる…」を押せない理由の文(ADR-0175 決定3)。押せるとき・閉じている途中は None。

        順は閉じている途中 → 比較だけの実行中 → AI の段の比較中・送る前の確認の途中・送っている間(門を持っている)
        → 結果が無い → 比較の窓が門を持っている → 送れる差分のある組が無い。
        「実行」は始めたときに結果を空にするので、実行中を結果が無いより先に見る(判断の記録 1)。
        閉じている途中のほかは、None のときと _can_ai() が真のときが一致する(押せる条件は _can_ai() のまま)。
        """
        if self._closing:
            return None
        if self._worker is not None and self._ai_action is None:
            return BATCH_AI_REASON_RUNNING
        if not self._idle():
            return BATCH_AI_REASON_APPROVAL
        if not self._results:
            return BATCH_AI_REASON_NOT_RUN
        if not self._session.free_for(self):
            return BATCH_AI_REASON_COMPARE
        if not self._ai_targets():
            return BATCH_AI_REASON_NO_TARGET
        return None

    def _can_switch(self) -> bool:
        """ロックの中で呼ぶ。AI プロファイルとプロンプトを選べるか(実行中・確認の待ち中・閉じている途中は選べない)。"""
        return self._idle() and not self._closing

    def _profile_where(self) -> dict[str, Any]:
        """プロファイルのファイルと利用者ごとの設定ファイルの場所(作業フォルダは見ない)。"""
        where = self._where
        return {"env": where["env"], "platform": where["platform"], "home": where["home"]}

    def _ai_view(self) -> dict[str, Any]:
        """ロックの中で呼ぶ。AI の場所の状態。描くたびにプロファイルのファイルと自動送信の設定を読む(設定の窓で変えた後に合わせる)。

        選択肢は id と名前だけ(接続先の URL・キー・CLI の場所は入れない)。
        """
        targets = self._ai_targets()
        can = self._can_ai()
        where = self._profile_where()
        profiles = llm_choices(**where)
        prompts = prompt_choices(**where)
        problems = [problem for problem in (profiles.problem, prompts.problem) if problem is not None]
        return {
            "title": batch_ai_title(len(targets), sum(count for _job, count in targets)),
            "profiles": _choice_items(profiles),
            "active_profile": _active_id(profiles),
            "prompts": _choice_items(prompts),
            "active_prompt": _active_id(prompts),
            "can_send": can,
            "can_check": can,
            "reason": self._ai_reason(),  # 押せない理由(AI の行の下の注記とボタンの title。ADR-0175 決定3)
            "can_switch": self._can_switch(),
            "auto_send": BATCH_AUTO_SEND_ON if auto_send_llm_enabled(**where) else None,
            "notes": [*self._notes, *problems],
        }

    def _answer_view(self) -> dict[str, Any]:
        """ロックの中で呼ぶ。選んだ組の答えの場所 {blocks, plain, text}(_results から作る。AI の段の途中は前の答え)。

        答えがある組は読み取ったブロックと <red> を外したコピーの文。答えが無く理由がある組は llm_column の文。
        AI の結果が無い組と選んでいないときは空(画面は答えの場所を隠す)。頭の行(== LLM 解析 == など)は入れない。
        選択待ちの組は、理由と次にすることの文(waiting_reason。ADR-0178 決定5)。区間のコマンドの名前は秘密の語の判定に通す。
        """
        jobs = self._prepared.jobs if self._prepared is not None else ()
        if not 0 <= self._selected < len(jobs):
            return dict(EMPTY_ANSWER)
        result = self._results.get(jobs[self._selected].id)
        if result is not None and result.outcome.status == "needs_selection":
            reason = waiting_reason(result.outcome, self._secret_words())
            if reason is not None:
                return {"blocks": [], "plain": "", "text": reason}
        llm = result.outcome.llm if result is not None else None
        if llm is None or llm.reason == "disabled":
            return dict(EMPTY_ANSWER)
        if llm.status == "done" and llm.answer:
            return {"blocks": answer_blocks(llm.answer), "plain": answer_copy_text(llm.answer), "text": None}
        return {"blocks": [], "plain": "", "text": llm_column(llm)}

    def _secret_words(self) -> list[str] | None:
        """選択待ちの理由に出す名前を通す秘密の語(設定の extra_secret_words。ADR-0178「秘密の語の判定」)。

        読めなければ None(名前は出さない。伏せられないなら出さない側に倒す)。選択待ちの組を選んだときだけ読む。
        """
        try:
            return list(load_settings(**self._where, read_prompt=False).extra_secret_words)
        except SettingsError:
            return None

    def _is_job(self, job_id: str) -> bool:
        """ロックの中で呼ぶ。今の組の一覧にある job の id か。"""
        return self._prepared is not None and any(job.id == job_id for job in self._prepared.jobs)

    # --- 開く・欄 ---

    def _state(self) -> dict[str, Any]:
        with self._lock:
            # 起動時の絵の番は、最初に読み込まれた窓が使う(--batch のときはこの窓。オフでも使う。ADR-0174 決定2)
            first = self._session._take_splash()
            result = self._ok()
            if first and result["view"]["character"]:
                result["view"]["splash"] = True
        # 起動したときの新しい版の確かめ(REQ-252 §5)。--batch だけのときはこの窓が最初に呼ぶ。比較の窓の後に開いたときは
        # session の 1 回だけの印で止まる。ロックの外で呼ぶ
        self._session.start_update_check(
            partial(auto_update_check_enabled, env=self._where["env"], platform=self._where["platform"], home=self._where["home"])
        )
        # --update-started で起動したときの started-ok(REQ-253 §7)。窓が出た後の印として、1 つの起動で 1 回だけ書く。ロックの外で呼ぶ
        mark_update_started = getattr(self._session, "mark_update_started", None)
        if mark_update_started is not None:
            mark_update_started()
        return result

    # --- 組(比較はしない) ---

    def _rebuild(self) -> dict[str, Any] | None:
        """ロックの中で呼ぶ。いまの欄で組を作り直す(wx の _rebuild)。一覧を出すだけで、比較は1件も走らせない。

        組を作れなかったときは知らせを返す。欄に打っている途中(実在しない・相対のパス)では組を作らず、知らせも出さない(REQ-97 §1)。
        """
        if not self._idle() or self._closing:
            return None
        self._prepared = None
        self._edit = None
        self._results = {}
        self._answer_sources = {}  # 組を作り直したら結果と一緒に消す(REQ-232 §2)
        self._status = ""
        self._raw_band = None
        form = self._form
        folders = form["source"] == "folders"
        guide = folder_guide(form["before_dir"], form["after_dir"]) if folders else manifest_guide(form["manifest"])
        edit = None
        notice = None
        if guide is not None:
            self._status = guide
        else:
            try:
                if folders:
                    # 組の作り方は pair_by_name のまま(prepare_folders)。設定は既定(比較方式は両方、機種は自動。REQ-212 §2・§8)
                    pairing = prepare_folders(form["before_dir"], form["after_dir"], mode="both", platform=None).pairing
                    assert pairing is not None
                    # 単純比較の設定は設定の窓の既定(REQ-228 §7.2)。読めなければ既定(すべてオフ)で作り、帯の文を出す
                    raw_options, self._raw_band = raw_defaults(**self._profile_where())
                    edit = batch_edit.from_pairing(pairing, auto=DEFAULT_FOLDER_AUTO, raw_options=raw_options)
                else:
                    prepare_manifest(form["manifest"])  # 読めるかと、読めないときの文は CLI と同じ関数で
                    edit = batch_edit.from_manifest_file(form["manifest"])
            except BatchSetupError as exc:
                notice = self._notice(str(exc), BATCH_SETUP_FAILED_TITLE)
            except ManifestError as exc:
                notice = self._notice(str(exc), BATCH_SETUP_FAILED_TITLE)
        self._edit = edit
        self._prepared = edit.to_prepared() if edit is not None else None
        self._only_checked = {side: set() for side in SIDES}
        self._suggestions = []
        self._shown, self._running = {}, None
        self._counts = ""
        self._selected = -1
        self._notes = []
        self._progress_text = ""
        self._progress = [0, self._progress[1]]
        self._banner_shown = True
        self._pairs_revision += 1
        self._result_revision += 1
        return notice

    def _confirm_first(self, form: Any, confirmed: Any) -> dict[str, Any] | None:
        """ロックの中で呼ぶ。直した組を作り直す前の確かめ(ADR-0153 決定4)。確かめるときは form を受け取らずに返す。

        画面は欄を入れ直し(fill)、dialog.js の confirm で聞いて、「はい」のときだけ confirmed=True で呼び直す(既定は「いいえ」)。
        """
        checked = self._checked_form(form)
        if not isinstance(confirmed, bool):
            raise _BadRequest
        if confirmed or self._edit is None or not self._edit.edited or not self._idle() or self._closing:
            self._form = checked
            return None
        self._fill += 1  # 打った欄を元に戻させる(「いいえ」なら何も変わらない)
        confirm = {
            "title": BATCH_REBUILD_CONFIRM_TITLE,
            "body": BATCH_REBUILD_CONFIRM_BODY,
            "yes": BATCH_REBUILD_CONFIRM_YES,
            "no": DIALOG_CANCEL,
        }
        return self._ok(confirm=confirm)

    def _rebuild_call(self, form: Any, confirmed: Any = False) -> dict[str, Any]:
        with self._lock:
            if (asked := self._confirm_first(form, confirmed)) is not None:
                return asked
            return self._ok(self._rebuild())

    def _swap(self, form: Any, confirmed: Any = False) -> dict[str, Any]:
        with self._lock:
            if (asked := self._confirm_first(form, confirmed)) is not None:
                return asked
            if not self._idle() or self._closing:
                return self._ok()  # 実行中・送信前の確認の待ち中・閉じている途中は何も変えない
            self._form["before_dir"], self._form["after_dir"] = self._form["after_dir"], self._form["before_dir"]
            self._form["source"] = "folders"
            self._fill += 1
            return self._ok(self._rebuild())  # 組を作り直すだけで、実行は始めない

    def _pick(self, form: Any, field: Any, confirmed: Any = False) -> dict[str, Any]:
        with self._lock:
            if not isinstance(field, str) or field not in PICK_FIELDS:
                self._checked_form(form)
                raise _BadRequest
            if (asked := self._confirm_first(form, confirmed)) is not None:
                return asked  # ダイアログを開く前に聞く
            if not self._idle() or self._closing:
                return self._ok()
        # 開いている間はロックを持たない
        if field == "manifest":
            path = self._choose(DIALOG_OPEN, "", MANIFEST_FILE_TYPES)
        else:
            path = self._choose(DIALOG_FOLDER, "", ())
        with self._lock:
            if path is None or not self._idle() or self._closing:
                return self._ok()  # 取り消した
            self._form[field] = str(path)
            self._form["source"] = FIELD_SOURCE[field]  # 選んだ欄で比べるものをそろえる(ラジオの代わり)
            self._fill += 1
            return self._ok(self._rebuild())

    # --- フォルダとマニフェストのドロップ(REQ-115 / ADR-0078 決定4。pywebview が事象のスレッドから呼ぶ。画面からは呼べない) ---

    def _on_drop(self, event: Any) -> None:
        """窓へのドロップ(app.watch_drop が印の要素(#drop-anchor)の drop に登録する。REQ-205)。想定外の例外は文面を出さない知らせにする。"""
        try:
            self._drop(event)
        except Exception:  # noqa: BLE001 - 事象のスレッドの例外は画面に届かないので、文面を出さない知らせにする
            self._drop_notice(gui_web_call_failed())

    def _drop_notice(self, body: str) -> None:
        """入力を変えずに知らせを出す(ロックの外で呼ぶ。画面は番号で1回だけ出す)。"""
        with self._lock:
            self._late_notice = {"id": self._next_serial(), **self._notice(body, BATCH_DROP_TITLE)}
        self._changed()

    def _drop(self, event: Any) -> None:
        with self._lock:
            if not self._idle() or self._closing:
                return  # 実行中・送信前の確認の待ち中・閉じている途中は受け付けない(知らせも出さない)
            edited = self._edit is not None and self._edit.edited
        if edited:
            # 直した組がある。ドロップは確かめを出せないので、入力を変えずに知らせる(REQ-212 §2)
            self._drop_notice(BATCH_DROP_EDITED)
            return
        paths = dropped_paths(event)  # フォルダかファイルかを見る間はロックを持たない
        if not paths:
            self._drop_notice(BATCH_DROP_NO_PATH)
            return
        changes: dict[str, Any] = {}
        one_folder: str | None = None  # フォルダ1つ(入れる側は、ロックの中で今の欄を見て決める)
        if len(paths) == 2 and all(path.is_dir() for path in paths):
            before, after = order_pair(*paths)  # 比較の窓の2つのファイルと同じ順(ADR-0078 決定1)
            changes = {"source": "folders", "before_dir": str(before), "after_dir": str(after)}
        elif len(paths) == 1 and paths[0].is_dir():
            changes = {"source": "folders"}
            one_folder = str(paths[0])
        elif len(paths) == 1 and paths[0].is_file() and paths[0].suffix.casefold() in MANIFEST_SUFFIXES:
            changes = {"source": "manifest", "manifest": str(paths[0])}
        else:
            # ファイル2つ・3つ以上・フォルダとファイルの混ざり・マニフェストでないファイル
            self._drop_notice(BATCH_DROP_UNSUPPORTED)
            return
        with self._lock:
            if not self._idle() or self._closing or (self._edit is not None and self._edit.edited):
                return
            if one_folder is not None:
                # 変更前が空なら変更前、そうでなければ変更後
                changes["after_dir" if self._form["before_dir"] else "before_dir"] = one_folder
            self._form.update(changes)
            self._fill += 1
            notice = self._rebuild()  # 「選ぶ…」と同じ。組を作り直すだけで、実行は始めない
            if notice is not None:
                self._late_notice = {"id": self._next_serial(), **notice}
        self._changed()

    def _load_split_folders(self, before: Any, after: Any) -> bool:
        """機器ごとに分けて書いた before・after のフォルダーを入れる(REQ-267 §5。比較の窓から WebSession を通して呼ぶ。画面からは呼べない)。

        フォルダーを 2 つ落としたときと同じ扱い: 実行中・送信前の確認の待ち中・閉じている途中は何も変えない。
        組を直している途中も、落としたときと同じく確かめを出せないので入力を変えない。どちらも False を返す。
        それ以外は、組が入っていても確かめずに置き換える。before と after はそのまま入れる(落とした順の並べ替えはしない)。
        比較は始めない。
        """
        with self._lock:
            if not self._idle() or self._closing:
                return False
            if self._edit is not None and self._edit.edited:
                return False
            self._form.update({"source": "folders", "before_dir": str(before), "after_dir": str(after)})
            self._fill += 1
            notice = self._rebuild()  # 組を作り直すだけで、実行は始めない
            if notice is not None:
                self._late_notice = {"id": self._next_serial(), **notice}
        self._changed()
        return True

    # --- 実行(wx の _on_run と同じ順) ---

    def _run(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            prepared = self._prepared
            jobs = self._included_jobs()  # 外した組はワーカーに渡さない(REQ-212 §4)
            if prepared is None or not jobs or not self._idle() or self._closing or not self._session.free_for(self):
                return self._ok()
            # 比較だけを走らせる。AI の設定は読まない(読めなくても比較は動く。AI へは「AI で解析」で送る。REQ-213 §2)
            self._plan, self._settings, self._notes, self._ai_action = None, None, [], None
            self._first, self._second, self._approved, self._results = [], [], {}, {}
            self._answer_sources = {}  # 結果を置き換えるので、答えの送り先の記録も消す(REQ-232 §2)
            self._first_summary = None
            self._shown, self._running = {}, None
            self._counts = ""
            self._result_revision += 1
            started = self._start(jobs, None, None, "first")
            if started:
                # 帯は実行前の確認(ADR-0020)。始まったら隠し、終わっても隠したまま(組を作り直すとまた出る。REQ-97 §2)
                self._banner_shown = False
            result = self._ok()
        if started:
            self._session.gate_changed(self)
        return result

    def _start(self, jobs: list[Job], llm: LlmRequest | None, approved: dict[str, str] | None, stage: str) -> bool:
        """ロックの中で呼ぶ。門を取れたら一括比較を始めて True。"""
        assert self._prepared is not None and self._edit is not None
        if not self._session.acquire(self):
            return False  # 比較の画面が走っている
        self._late_notice = None
        self._progress = [0, max(len(jobs), 1)]
        self._worker = BatchWorker(
            jobs,
            # 機種が自動の組は auto のまま本体へ渡す(判定は run_compare が 1 回。AI の 1 段目と 2 段目も同じ。判断の記録 1)
            prepare=batch_edit.prepare_with_auto(self._prepared.prepare(), self._edit.auto_platform),
            llm=llm,
            approved=approved,
            post=self._post,
            on_progress=self._on_progress,
            on_result=lambda result: self._on_result(stage, result),
            on_done=lambda summary: self._on_stage_done(stage, summary),
            on_failed=self._on_failed,
            runner=self._runner,
        )
        self._worker.start()
        return True

    # --- AI で解析・内容を確かめる(REQ-213 §4〜§6。送るかどうかの判断は _answer_approval だけ) ---

    def _begin_ai(self, form: Any, action: str) -> dict[str, Any]:
        """「AI で解析」(AI_SEND)と「内容を確かめる…」(AI_CHECK)。押せないときは何もしない。

        設定が読めなければ知らせて何もしない。読めたら対象の組だけで送らずに比べ(1 段目)、終わったら確認を出す。
        """
        with self._lock:
            self._take(form)
            if not self._can_ai():
                return self._ok()
            problem = self._prepare_ai()
            if problem is not None:
                # 前提の文が送れないときは確認を出さずに知らせる(送らない。ADR-0172 決定4)
                title = BATCH_NOT_SENT_TITLE if problem == premise_refused_text() else BATCH_LLM_SETTINGS_TITLE
                return self._ok(self._notice(problem, title))
            started = self._start_ai(action)
            result = self._ok()
        if started:
            self._session.gate_changed(self)
        return result

    def _prepare_ai(self) -> str | None:
        """ロックの中で呼ぶ。AI の設定を読んで計画を作る。読めなければ理由の文を返す(計画は作らない)。

        注記(作業フォルダのキー・上限・指示文を読まなかったこと)は _notes に入れる(AI の場所の下に出す)。
        上限は押すたびに読む設定の値(利用者ごとの設定ファイルか既定。ADR-0071 決定5)。
        毎回付ける前提の文(ADR-0172)が送れないときも計画を作らず、premise_refused_text() を返す(送信前の確認の 1 段目を出さない)。
        """
        self._plan, self._settings = None, None
        try:
            settings = load_settings(**self._where)
        except SettingsError as exc:
            return str(exc)
        if premise_problem(settings) is not None:
            return premise_refused_text()
        notes = []
        workdir = (self._where["cwd"] or Path.cwd()) / WORKDIR_CONFIG
        if settings.ignored_workdir_api_key:
            notes.append(workdir_api_key_ignored(workdir.absolute()))
        if settings.ignored_workdir_limits:
            notes.append(workdir_limits_ignored(workdir.absolute(), settings.ignored_workdir_limits))
        if settings.ignored_user_prompt_template and settings.user_config is not None:
            notes.append(user_prompt_template_ignored(settings.user_config))
        self._notes = notes
        self._settings = settings
        self._plan = plan_llm(settings, client_factory=self._client_factory)
        return None

    def _start_ai(self, action: str) -> bool:
        """ロックの中で呼ぶ(_prepare_ai の後)。対象の組だけで 1 段目(送らない)を始める。始めたら True。

        _results は始めたときのまま持つ(前の答えを確かめるため。終わったら対象の組の分だけ入れる)。
        """
        assert self._plan is not None
        jobs = [job for job, _count in self._ai_targets()]
        if not jobs:
            return False
        self._first, self._second, self._approved = [], [], {}
        self._first_summary = None
        self._running = None
        self._ai_action = action
        started = self._start(jobs, self._plan.first, None, "first")
        if not started:
            self._ai_action = None
        return started

    def _auto_send(self) -> None:
        """ロックの中で呼ぶ。実行の結果を出して門を返した後の自動送信(REQ-213 §6)。

        [gui] auto_send_llm がオン(押すたびに読む)で、「AI で解析」を押せるときだけ、同じ入口で 1 段目を始める。
        送るのは確認で「送る」を押したときだけ(確認を飛ばす経路は無い)。設定が読めなければ送らず、理由を注記に出す。
        """
        if not auto_send_llm_enabled(**self._profile_where()) or not self._can_ai():
            return
        problem = self._prepare_ai()
        if problem is not None:
            # 前提の文が送れないときは、その文をそのまま注記に出す(「設定を読めない」の型で包まない)
            self._notes = [problem if problem == premise_refused_text() else auto_send_settings_unreadable(problem)]
            return
        self._start_ai(AI_SEND)

    def _use_profile(self, choice_id: Any, which: str) -> dict[str, Any]:
        """AI プロファイル(which=llm)かプロンプト(which=prompt)の使用中を変える(REQ-213 §7。設定の窓と同じ保存先に書く)。

        実行中・確認の待ち中・閉じている途中、今の一覧に無い id、使用中と同じ id は何も書かない。
        失敗したら知らせる(画面の選択は view の使用中に戻る)。結果と答えには触らない。書いたら設定の窓に読み直させる(ロックの外)。
        """
        if not isinstance(choice_id, str):
            raise _BadRequest
        written = False
        with self._lock:
            if not self._can_switch():
                return self._ok()
            where = self._profile_where()
            choices = llm_choices(**where) if which == "llm" else prompt_choices(**where)
            if choice_id not in choices.ids or choice_id == _active_id(choices):
                return self._ok()
            if which == "llm":
                action = use_selected(choice_id, **where)
                title = COMPARE_AI_PROFILE_FAILED_TITLE
            else:
                action = use_prompt(choice_id, **where)
                title = COMPARE_PROMPT_FAILED_TITLE
            written = action.ok
            result = self._ok() if action.ok else self._ok(self._notice(action.message, title))
        if written:
            self._session.settings_changed()
        return result

    def _cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._worker is not None:
                self._worker.cancel()  # いま読んでいる1件が終わってから止まる
            if self._pair_asking is not None:
                # 送った要求は止められない。答えが届くまで門を持ち、届いた答えは捨てる(compare_api の追加の質問と同じ)
                self._pair_asking.cancelled = True
            return self._ok()

    # --- ワーカーのスレッドから(状態はロックの中だけ。知らせはロックの外) ---

    def _on_progress(self, progress: BatchProgress) -> None:
        with self._lock:
            if self._closing or self._prepared is None:
                return
            self._progress[0] = progress.index - 1
            self._progress_text = batch_progress_text(progress)
            if self._is_job(progress.job_id):
                self._running = progress.job_id
                self._result_revision += 1
        self._changed()

    def _on_result(self, stage: str, result: JobResult) -> None:
        with self._lock:
            (self._first if stage == "first" else self._second).append(result)
            if self._closing:
                return
            self._progress[0] += 1
            if self._is_job(result.job.id):
                self._shown[result.job.id] = result
                if self._running == result.job.id:
                    self._running = None
                self._result_revision += 1
        self._changed()

    def _on_stage_done(self, stage: str, summary: BatchSummary) -> None:
        with self._lock:
            self._worker = None
            if self._closing:
                self._session.release(self)  # 窓を閉じた後に戻ってきた。結果は捨て、門だけ返す
                released = True
            else:
                was_run = stage == "first" and self._ai_action is None
                released = self._after_stage(stage, summary)
                if released and was_run and not summary.cancelled:
                    # 実行の結果を出して門を返した後に、自動送信なら確認のリストを開く(中止した実行と AI の段からは始めない)
                    self._auto_send()
        if released:
            self._session.gate_changed(self)
        self._changed()

    def _after_stage(self, stage: str, summary: BatchSummary) -> bool:
        """ロックの中で呼ぶ(wx の _on_stage_done と同じ判断)。門を返したら True(送信前の確認を待つ間は返さない)。"""
        if stage == "second":
            assert self._first_summary is not None
            self._remember_sources()
            final = finish(self._first, self._second, self._approved)
            self._finish(final, combined_summary(self._first_summary, final, later=[summary]))
            return True
        self._first_summary = summary
        if self._ai_action is None:
            self._finish(list(self._first), summary)  # 「実行」は比較だけ
            return True
        assert self._plan is not None and self._settings is not None
        decision = after_first_stage(
            self._plan,
            self._first,
            cancelled=summary.cancelled,
            max_total_diffs=self._settings.max_total_diffs,
            limits=limit_lines(self._settings),
        )
        if decision.final is not None:
            if self._ai_action == AI_CHECK:
                # 確かめるだけ。結果は変えない。接続が使えないときは、行が変わらないので理由を知らせる
                message = decision.message
                if message is None and self._plan.send is None and self._first:
                    message = llm_column(self._first[0].outcome.llm)
                self._end_check(message)
                return True
            self._finish(decision.final, combined_summary(summary, decision.final), decision.message)
            return True
        # 送信前の確認は全部の組をまとめて1回だけ(ADR-0031 決定2)。この間も門は持ったまま(比較の画面は走らせない)
        assert decision.preview is not None and self._plan.send is not None
        self._approval = (self._next_serial(), decision.preview, approval_view(decision.preview, self._settings), summary)
        return False

    def _remember_sources(self) -> None:
        """ロックの中で呼ぶ(2 段目の後)。2 段目で走った組のうち、送って答えが届いた組に送り先を覚え、届かなかった組の記録は消す。

        2 段目で走らなかった組(前の答えを残した組など)の記録には触らない(前の送り先のまま。REQ-232 §2)。
        """
        for r in self._second:
            llm = r.outcome.llm
            if self._sending is not None and llm is not None and llm.status == "done" and llm.transmitted:
                self._answer_sources[r.job.id] = self._sending
            else:
                self._answer_sources.pop(r.job.id, None)

    def _end_check(self, message: str | None = None) -> None:
        """ロックの中で呼ぶ。「内容を確かめる…」の終わり。1 段目の結果を捨て、前の結果と行に戻して門を返す(送らない)。"""
        self._session.release(self)
        self._ai_action = None
        self._shown, self._running = dict(self._results), None
        self._progress_text = ""
        self._result_revision += 1
        if message:
            self._late_notice = {"id": self._next_serial(), **self._notice(message, BATCH_NOT_SENT_TITLE)}

    def _merged(self, final: list[JobResult]) -> dict[str, JobResult]:
        """ロックの中で呼ぶ。AI の段の結果を、対象の組の分だけ今の結果に入れた dict(新しく作る。対象でない組はそのまま)。

        2 段目で走った組は、失敗も含めて 2 段目の結果。それ以外の組は、前の答え(llm.status が done)があれば前の結果のまま、
        無ければ今の結果(送らなかった・上限超え・中止・接続が使えない理由)。
        """
        resent = {r.job.id for r in self._second}
        merged = dict(self._results)
        for r in final:
            previous = merged.get(r.job.id)
            kept = previous is not None and previous.outcome.llm is not None and previous.outcome.llm.status == "done"
            if r.job.id in resent or not kept:
                merged[r.job.id] = r
        return merged

    def _finish(self, final: list[JobResult], summary: BatchSummary, message: str | None = None) -> None:
        """ロックの中で呼ぶ。結果を入れて門を返す(wx の _finish)。

        「実行」は結果を置き換える。AI の段は対象の組の分だけ入れ(_merged)、件数の文は全部の組の結果から作る。
        「実行」を最後まで流し、実行した組(外した組は渡していない)が全部選択待ちなら知らせる(ADR-0178 決定5)。
        """
        was_run = self._ai_action is None
        if self._ai_action is None:
            self._results = {r.job.id: r for r in final}
            counts = summary.counts
        else:
            self._results = self._merged(final)
            counts = combined_summary(summary, list(self._results.values())).counts
        self._ai_action = None
        self._session.release(self)
        assert self._prepared is not None
        self._shown, self._running = dict(self._results), None
        self._counts = status_counts_text(counts)
        self._result_revision += 1
        status = []
        if summary.cancelled:
            status.append(f"{CANCELLED}{BAPI_SENTENCE_BREAK}{batch_rest_not_run(summary.total - summary.executed)}")
        if gave_up := gave_up_count(final):
            status.append(batch_llm_gave_up(gave_up))
        # 機種が自動で、config と決まったのに判定できなかった組の数(REQ-209 のとおり指定なしで比べた。REQ-212 §8)
        undetected = sum(
            1
            for r in self._results.values()
            if r.job.platform == AUTO_PLATFORM and r.outcome.kind == "config" and platform_undetected(r.outcome.auto_platform)
        )
        if undetected:
            status.append(batch_platform_undetected(undetected))
        self._progress_text = ""
        self._status = " ".join(status)
        if message:
            self._late_notice = {"id": self._next_serial(), **self._notice(message, BATCH_NOT_SENT_TITLE)}
        elif was_run and not summary.cancelled and final and all(r.outcome.status == "needs_selection" for r in final):
            self._late_notice = {"id": self._next_serial(), **self._notice(BATCH_ALL_WAITING, BATCH_ALL_WAITING_TITLE)}

    def _on_failed(self, exc: BaseException) -> None:
        with self._lock:
            self._worker = None
            self._session.release(self)
            if self._ai_action is not None:
                # AI の段で止まった。結果は始めたときのまま(前の結果と行に戻す)
                self._ai_action = None
                self._shown = dict(self._results)
                self._result_revision += 1
            if self._running is not None:
                self._running = None  # 止まった組を「実行中」のまま残さない
                self._result_revision += 1
            if not self._closing:
                body = f"{type(exc).__name__}: {exc}"
                self._late_notice = {"id": self._next_serial(), **self._notice(body, BATCH_FAILED_TITLE)}
        self._session.gate_changed(self)
        self._changed()

    # --- 送信前の確認(ADR-0031。送るかどうかの判断はここだけ) ---

    def _answer_approval(self, checked: Any, yes: Any) -> dict[str, Any]:
        with self._lock:
            pending = self._approval
            if pending is None or self._closing or self._worker is not None:
                return self._ok()  # 待っている確認が無い。何も送らない
            self._approval = None  # 答えたら消す(1つの確認で送れるのは1回だけ)
            _number, preview, shown, summary = pending
            offered = {row.job_id for row in shown.rows}
            valid = (
                isinstance(checked, list)
                and all(isinstance(job_id, str) for job_id in checked)
                and set(checked) <= offered
                and isinstance(yes, bool)
            )
            checking = self._ai_action == AI_CHECK
            approved = second_stage(preview, set(checked)) if valid and yes and not checking else {}
            if checking:
                # 「内容を確かめる…」は checked と yes がどんな値でも送らない。1 段目の結果を捨て、前の結果と行に戻す
                self._end_check()
                result = self._ok()
                released = True
            elif not approved:
                # 「送らない」・チェックを全部外した・確認に出していない組がある。比較の結果だけが残る
                # (前の答えがある組は前の結果のまま。_finish の _merged)
                final = declined(self._first)
                self._finish(final, combined_summary(summary, final))
                result = self._ok() if valid else _failure(gui_web_call_failed())
                released = True
            else:
                self._approved = approved
                assert self._prepared is not None and self._plan is not None
                jobs = [job for job in self._prepared.jobs if job.id in approved]
                assert self._settings is not None
                # 2 段目の送り先(答えが届いた組に覚える。ADR-0170 決定1)
                self._sending = (destination_of(self._settings), self._settings.prompt_profile_name)
                # 前提の文は送る直前に読み直す。確認の後に書き換えた・消したときは、どの組も承認した文と違うので送らない(ADR-0172 決定5)
                send = with_current_premise(self._plan.send, **self._profile_where())
                self._start(jobs, send, approved, "second")  # 門は持ったまま2段目へ
                result = self._ok()
                released = False
        if released:
            self._session.gate_changed(self)
            if not result["ok"]:
                self._changed()  # 画面は失敗の文だけを受け取るので、読み直させる
        return result

    def _save_approval_payloads(self) -> dict[str, Any]:
        with self._lock:
            pending = self._approval
            if pending is None or self._closing:
                return self._ok()
        # 上書きの確認は付けない。書き出しが既存のファイルを拒否する(上書きしない)
        target = self._choose(DIALOG_SAVE, PAYLOADS_FILENAME, TEXT_FILE_TYPES)  # 開いている間はロックを持たない
        with self._lock:
            if target is None:
                return self._ok()  # 取り消した
            try:
                written = save_payloads(pending[1], target)
            except ResultsWriteError as exc:
                return self._ok(self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
            return self._ok(self._notice(batch_payloads_saved(written), BATCH_SAVED_TITLE))

    # --- 結果 ---

    def _result(self) -> dict[str, Any]:
        with self._lock:
            prepared = self._prepared
            result = {
                "revision": self._result_revision,
                "pairs_revision": self._pairs_revision,
                "table": [asdict(row) for row in self._table()],
                "unpaired": [asdict(row) for row in prepared.unpaired_rows] if prepared is not None else [],
                # 片方だけのファイルの行のチェック(unpaired と同じ並び。パスは title に出す全文。REQ-212 §6)
                "unpaired_items": [
                    {"side": side, "index": index, "checked": index in self._only_checked[side], "path": str(path)}
                    for side in SIDES
                    for index, path in enumerate(self._only(side))
                ],
                # 組の候補(REQ-212 §7。REQ-219 の AI の案も同じ形で足す)。チェックは画面が持ち、既定はオフ
                "suggestions": [
                    {
                        "id": suggestion_id,
                        "source": source,
                        "before": before.name,
                        "after": after.name,
                        "before_path": str(before),
                        "after_path": str(after),
                        "hint": hint,
                    }
                    for suggestion_id, source, before, after, hint in self._suggestions
                ],
            }
            return self._ok(result=result)

    def _select_result(self, index: Any) -> dict[str, Any]:
        with self._lock:
            jobs = self._prepared.jobs if self._prepared is not None else ()
            if not _is_int(index) or not -1 <= index < len(jobs):
                raise _BadRequest
            self._selected = index
            # 答えの場所は整えた答え(頭の行は出さない。wx は answer_lines のまま。REQ-213 §8)
            return self._ok(answer=self._answer_view())

    def _handoff(self, form: Any, index: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not _is_int(index):
                raise _BadRequest
            if not self._session.has_compare():
                return self._ok()  # 比較の窓が無い(単独で開いた)
            jobs = self._prepared.jobs if self._prepared is not None else ()
            if not 0 <= index < len(jobs):
                return self._ok(self._notice(BATCH_SELECT_PROMPT, BATCH_SELECT_TITLE))
            job = jobs[index]
            handoff = handoff_for(job)
            # その組の AI の答え(送って答えが届いた組だけ。無ければ None。ADR-0170 決定1)。区間は渡さないので、
            # まとめて比べた組は比較の窓でもまとめての比較になり、全体の答え(outcome.llm と同じ multi.llm)を持っていく(REQ-241 §5)
            result = self._results.get(job.id)
            answer = handed_answer(result.outcome.llm if result is not None else None, self._answer_sources.get(job.id))
        self._session.handoff(handoff, answer)  # 比較の窓の Python の側を呼ぶ間はロックを持たない
        with self._lock:
            return self._ok()

    def _open_settings(self, tab: Any) -> dict[str, Any]:
        if tab not in SETTINGS_TABS:
            raise _BadRequest
        # 窓を作る間はロックを持たない(--batch だけの起動でも開く)。tab を省いたときは今と同じく引数なしで呼ぶ
        if tab is None:
            self._session.open_settings()
        else:
            self._session.open_settings(tab)
        with self._lock:
            return self._ok()

    def _dismiss_update_notice(self) -> dict[str, Any]:
        self._session.dismiss_update_notice()  # 両方の窓へ知らせる。ロックの外で呼ぶ
        with self._lock:
            return self._ok()

    # --- 保存(上書きしない) ---

    def _save_manifest(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            edit = self._edit
            if edit is None or not edit.included_ids() or not self._idle() or self._closing:
                return self._ok()
        target = self._choose(DIALOG_SAVE, MANIFEST_FILENAME, MANIFEST_FILE_TYPES)  # 開いている間はロックを持たない
        if target is None:
            with self._lock:
                return self._ok()  # 取り消した
        # 自動の組の機種は、保存のときにファイルを読んで判定する(ダイアログと同じくロックを持たずに読む。REQ-212 §5)
        judged = batch_edit.judge_for_save(edit)
        with self._lock:
            if self._edit is not edit:
                return self._ok()  # 選んでいる間に組が変わった
            try:
                written, excluded, undetected = batch_edit.save_edited_manifest(edit, target, judged)
            except ManifestWriteError as exc:
                return self._ok(self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
            except ManifestError as exc:
                return self._ok(self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
            self._status = batch_manifest_saved_with(written, excluded, undetected)
            return self._ok()

    def _save_results(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            prepared, results = self._prepared, self._results
            included = self._included_jobs()
            if prepared is None or not any(j.id in results for j in included) or not self._idle() or self._closing:
                return self._ok()
        target = self._choose(DIALOG_SAVE, RESULTS_FILENAME, JSONL_FILE_TYPES)  # 開いている間はロックを持たない
        with self._lock:
            if target is None or self._results is not results:
                return self._ok()  # 取り消した・選んでいる間に結果が変わった
            ordered = [results[j.id] for j in included if j.id in results]  # 外した組の結果は書かない
            try:
                written = save_results(ordered, target)
            except ResultsWriteError as exc:
                return self._ok(self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
            self._status = batch_saved_notice(written, len(ordered))
            return self._ok()

    # --- HTML と CSV の出力(ADR-0156 / REQ-216。上書きしない。秘密の行は必ず伏せる) ---

    def _export_result(self, form: Any, kind: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(kind, str) or kind not in EXPORT_KINDS:
                raise _BadRequest
            results = self._results
            if not results or not self._idle() or self._closing:
                return self._ok()
            # 組の順と外した組の扱いは「結果を保存…」と同じ。比較が失敗した組は状態だけを出す
            ordered = [results[job.id] for job in self._included_jobs() if job.id in results]
            if not ordered:
                return self._ok()
        # 秘密の語は今の設定の語と送った AI 解析の語。設定が読めなければ、語が足りないまま出さない(ADR-0156 決定2)
        try:
            settings = load_settings(**self._where, read_prompt=False)
        except SettingsError as exc:
            with self._lock:
                return self._ok(self._notice(export_settings_unreadable(str(exc)), COMPARE_SAVE_FAILED_TITLE))
        saved_at = datetime.now(timezone.utc).astimezone()
        filename = export_filename("batch", kind, saved_at)  # ホスト名は入れない
        target = self._choose(DIALOG_SAVE, filename, EXPORT_FILE_TYPES[kind])  # 開いている間はロックを持たない
        with self._lock:
            if target is None or self._results is not results or not self._idle() or self._closing:
                return self._ok()  # 取り消した・選んでいる間に結果が変わった
        words = export_words(settings.extra_secret_words, (result.outcome.llm for result in ordered))
        try:
            # 大きいときも画面を止めないよう、組み立てと書き出しはロックの外で行う
            data = export_bytes(batch_report(ordered, words), kind, saved_at)
        except RedactError:
            with self._lock:
                return self._ok(self._notice(EXPORT_REDACT_FAILED, COMPARE_SAVE_FAILED_TITLE))  # 行の中身は入れない
        try:
            written = write_new_bytes(data, target)
        except ResultsWriteError as exc:
            with self._lock:
                return self._ok(self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
        with self._lock:
            self._status = export_saved(written)
            return self._ok()

    # --- 組を直す(REQ-212 / ADR-0153 決定4〜8) ---

    def _apply(self, edit: EditableBatch, *, cleared: Iterable[str] = (), pairs: bool = False) -> None:
        """ロックの中で呼ぶ。直した組を入れ、表を作り直す。cleared の組は結果を消して待機の行にする。

        pairs は組の一覧が変わった(組にした・解除した)とき。片方だけのファイルのチェックと候補を消す。
        """
        self._edit = edit
        self._prepared = edit.to_prepared()
        gone = set(cleared)
        for job_id in gone:
            self._answer_sources.pop(job_id, None)  # 結果を消した組の答えの送り先も消す(REQ-232 §2)
        if gone &(set(self._results) | set(self._shown)):
            self._results = {job_id: r for job_id, r in self._results.items() if job_id not in gone}
            self._shown = {job_id: r for job_id, r in self._shown.items() if job_id not in gone}
        if pairs:
            self._only_checked = {side: set() for side in SIDES}
            self._suggestions = []
            self._selected = -1  # 行の並びが変わった(画面も選択を消す)
            self._notes = []
            self._pairs_revision += 1
        self._result_revision += 1

    def _rejected(self, exc: ManifestError, **extra: Any) -> dict[str, Any]:
        """断られた(組は直す前のまま)。知らせは ManifestError.problems の文。"""
        return self._ok(self._notice(batch_settings_rejected(exc.problems), BATCH_SETTINGS_REJECTED_TITLE), **extra)

    def _set_included(self, job_id: Any, on: Any) -> dict[str, Any]:
        if not isinstance(job_id, str) or not isinstance(on, bool):
            raise _BadRequest
        with self._lock:
            if not self._can_edit():
                return self._ok()
            assert self._edit is not None
            edit = batch_edit.set_included(self._edit, job_id, on)
            if edit is not self._edit:
                self._edit = edit  # 結果は消さない(戻すとまた出る)
                self._result_revision += 1
            return self._ok()

    def _set_included_many(self, job_ids: Any, on: Any) -> dict[str, Any]:
        if not isinstance(job_ids, list) or not all(isinstance(job_id, str) for job_id in job_ids):
            raise _BadRequest
        if not isinstance(on, bool):
            raise _BadRequest
        with self._lock:
            if not self._can_edit():
                return self._ok()
            assert self._edit is not None
            edit = self._edit
            for job_id in job_ids:
                edit = batch_edit.set_included(edit, job_id, on)  # 知らない id は何も変えない
            if edit is not self._edit:
                self._edit = edit  # 結果は消さない(戻すとまた出る)
                self._result_revision += 1  # 何組変えても 1 回だけ
            return self._ok()

    def _pair_settings(self, job_id: Any) -> dict[str, Any]:
        if job_id is not None and not isinstance(job_id, str):
            raise _BadRequest
        with self._lock:
            if not self._can_edit():
                return self._ok()
            assert self._edit is not None
            if job_id == "":
                return self._ok(self._notice(BATCH_SETTINGS_SELECT_PROMPT, BATCH_SELECT_TITLE))
            if job_id is not None and self._edit.job(job_id) is None:
                return self._ok()
            return self._ok(settings=batch_edit.settings_form(self._edit, job_id))

    def _edit_settings(self, job_id: str | None, values: Any) -> dict[str, Any]:
        try:
            values = batch_edit.check_settings(values, for_defaults=job_id is None)
        except ValueError:
            raise _BadRequest from None
        with self._lock:
            if not self._can_edit():
                return self._ok()
            assert self._edit is not None
            if job_id is not None and self._edit.job(job_id) is None:
                return self._ok()
            changes = batch_edit.settings_changes(self._edit, job_id, values)
            if not changes:
                return self._ok()
            try:
                if job_id is None:
                    edit = batch_edit.edit_defaults(self._edit, changes)
                else:
                    edit = batch_edit.edit_job(self._edit, job_id, changes)
            except ManifestError as exc:
                # 打った値を入れて開き直す
                return self._rejected(exc, reopen=batch_edit.settings_form(self._edit, job_id, values))
            # 直した組の結果を消す(全部の組の設定は全部の組)
            self._apply(edit, cleared=self._edit.job_ids() if job_id is None else [job_id])
            return self._ok()

    def _edit_pair(self, job_id: Any, values: Any) -> dict[str, Any]:
        if not isinstance(job_id, str):
            raise _BadRequest
        return self._edit_settings(job_id, values)

    def _edit_defaults(self, values: Any) -> dict[str, Any]:
        return self._edit_settings(None, values)

    def _pick_rules(self) -> dict[str, Any]:
        with self._lock:
            if not self._can_edit():
                return self._ok(path=None)
        path = self._choose(DIALOG_OPEN, "", MANIFEST_FILE_TYPES)  # 開いている間はロックを持たない
        with self._lock:
            return self._ok(path=str(path) if path is not None else None)

    def _set_platform(self, job_id: Any, index: Any) -> dict[str, Any]:
        if not isinstance(job_id, str) or not _is_int(index) or not 0 <= index < len(WEB_PLATFORM_CHOICES):
            raise _BadRequest
        with self._lock:
            if not self._can_edit() or self._prepared is None:
                return self._ok()
            assert self._edit is not None
            job = next((job for job in self._prepared.jobs if job.id == job_id), None)
            value = WEB_PLATFORM_CHOICES[index][0]
            if job is None or job.kind != "config" or value == (job.platform or ""):
                return self._ok()  # 知らない組・config でない組(文字で読むだけ)・同じ値
            try:
                edit = batch_edit.set_platform(self._edit, job_id, value)
            except ManifestError as exc:
                return self._rejected(exc)
            self._apply(edit, cleared=[job_id])
            return self._ok()

    def _set_only_checked(self, side: Any, index: Any, on: Any) -> dict[str, Any]:
        if not isinstance(side, str) or side not in SIDES or not _is_int(index) or not isinstance(on, bool):
            raise _BadRequest
        with self._lock:
            if self._can_edit() and 0 <= index < len(self._only(side)):
                if on:
                    self._only_checked[side].add(index)
                else:
                    self._only_checked[side].discard(index)
            return self._ok()

    def _pair_paths(self, pairs: list[tuple[Path, Path]]) -> dict[str, Any]:
        """ロックの中で呼ぶ。片方だけのファイルを組にする(組の末尾に足す。機種は自動。比較はしない)。"""
        assert self._edit is not None
        edit = self._edit
        try:
            for before, after in pairs:
                edit = batch_edit.pair_files(edit, before, after)
        except ManifestError as exc:
            return self._rejected(exc)
        self._apply(edit, pairs=True)
        return self._ok()

    def _pair_files(self) -> dict[str, Any]:
        with self._lock:
            if not self._can_pair():
                return self._ok()
            [before] = self._only_checked["before"]
            [after] = self._only_checked["after"]
            return self._pair_paths([(self._only("before")[before], self._only("after")[after])])

    def _pair_dropped(self, revision: Any, before_index: Any, after_index: Any) -> dict[str, Any]:
        """ドラッグで組にする(REQ-233 / ADR-0171 決定5)。チェック(_only_checked)は見ない。
        組を直せないとき・描いたときから組の一覧が変わったとき・番号が一覧の外のときは、知らせを出さずに何も変えない。"""
        if not (_is_int(revision) and _is_int(before_index) and _is_int(after_index)):
            raise _BadRequest
        with self._lock:
            only_before, only_after = self._only("before"), self._only("after")
            if (
                not self._can_edit()
                or revision != self._pairs_revision
                or not 0 <= before_index < len(only_before)
                or not 0 <= after_index < len(only_after)
            ):
                return self._ok()
            return self._pair_paths([(only_before[before_index], only_after[after_index])])

    def _unpair(self, job_ids: Any) -> dict[str, Any]:
        """選んだ組をまとめて解除する(ADR-0176 決定2)。全部か無しか。

        形が違えば _BadRequest。組を直せないときと、知らない id が含まれるときは何も変えない。空なら選ぶように知らせる。
        途中で解除できない組があれば、何も変えずに断りの知らせ。全部できたら 1 回だけ入れる(pairs_revision は 1 回だけ進む)。
        """
        if not isinstance(job_ids, list) or not all(isinstance(job_id, str) for job_id in job_ids):
            raise _BadRequest
        with self._lock:
            if not self._can_edit():
                return self._ok()
            assert self._edit is not None
            if not job_ids:
                return self._ok(self._notice(BATCH_UNPAIR_SELECT_PROMPT, BATCH_SELECT_TITLE))
            if any(self._edit.job(job_id) is None for job_id in job_ids):
                return self._ok()
            ids = list(dict.fromkeys(job_ids))  # 同じ id が重なっていれば 1 つにする(順は残す)
            edit = self._edit
            try:
                for job_id in ids:
                    edit = batch_edit.unpair(edit, job_id)
            except ManifestError as exc:
                return self._rejected(exc)  # 手元の edit は捨てる(組は直す前のまま)
            self._apply(edit, cleared=ids, pairs=True)
            return self._ok()

    def _same_lists(self, edit: EditableBatch, revision: int) -> bool:
        """ロックの中で呼ぶ。組の一覧が edit と revision のときから変わっていないか(片方だけのファイルの一覧も同じか)。"""
        return (
            self._edit is not None
            and self._pairs_revision == revision
            and (self._edit.only_before, self._edit.only_after) == (edit.only_before, edit.only_after)
        )

    @staticmethod
    def _hostname_rows(found: batch_edit.Candidates) -> list[tuple[str, str, Path, Path, str]]:
        """hostname の候補の行(id・source・変更前・変更後・手がかりの文)。"""
        return [
            (f"{SUGGESTION_SOURCE}-{number}", SUGGESTION_SOURCE, item.before, item.after, batch_hostname_hint(item.value))
            for number, item in enumerate(found.items, start=1)
        ]

    def _suggest_hostnames(self) -> dict[str, Any]:
        with self._lock:
            if not self._can_edit() or not self._only("before") or not self._only("after"):
                return self._ok()
            assert self._edit is not None
            edit, revision = self._edit, self._pairs_revision
        # ファイルはロックの外で読む。読むだけで組は変えない。値はログに出さない
        found = batch_edit.hostname_candidates(edit.only_before, edit.only_after, read_hostname)
        with self._lock:
            if not self._same_lists(edit, revision) or not self._can_edit():
                return self._ok()  # 読んでいる間に一覧が変わった
            self._suggestions = self._hostname_rows(found)  # 候補は全部入れ替える(AI の案も消す)
            self._result_revision += 1
            if not found.items:
                return self._ok(self._notice(batch_hostname_none(found.unread, found.duplicated), BATCH_SUGGEST_TITLE))
            self._status = batch_hostname_counts(found.unread, found.duplicated) if found.unread or found.duplicated else ""
            return self._ok()

    def _accept_suggestions(self, ids: Any) -> dict[str, Any]:
        if not isinstance(ids, list) or not all(isinstance(item, str) for item in ids):
            raise _BadRequest
        with self._lock:
            if not self._can_edit():
                return self._ok()
            # AI の案も hostname の候補と同じに、ふつうの組として足す(AI の案だった印は持たない。ADR-0159 決定7)
            chosen = [(before, after) for suggestion_id, _source, before, after, _hint in self._suggestions if suggestion_id in ids]
            if not chosen:
                return self._ok()
            chosen = list(dict.fromkeys(chosen))  # 全く同じ組が 2 回選ばれていれば 1 つにする(順は残す)
            befores = [before for before, _after in chosen]
            afters = [after for _before, after in chosen]
            if len(set(befores)) != len(befores) or len(set(afters)) != len(afters):
                # 同じファイルを含む候補を 2 つ以上選んだ。どれを組にするかは決めず、何も組にしない(ADR-0177 決定2)
                return self._ok(self._notice(BATCH_SUGGEST_OVERLAP, BATCH_SUGGEST_TITLE))
            return self._pair_paths(chosen)

    def _dismiss_suggestions(self) -> dict[str, Any]:
        with self._lock:
            if self._suggestions and self._can_edit():
                self._suggestions = []
                self._result_revision += 1
            return self._ok()

    # --- AI に組の案を聞く(REQ-219 / ADR-0159。送るかどうかの判断は _answer_pair_ai だけ) ---

    def _pair_ai_notice(self, body: str) -> dict[str, Any]:
        return self._ok(self._notice(body, BATCH_PAIR_AI_TITLE))

    def _ask_pair_ai(self) -> dict[str, Any]:
        """「AI に案を聞く…」。送らずに、送る全文の確認を出す(確認の間は門を持つ)。押せないときは何もしない。

        hostname の候補は作らない(ファイルを読まない)。出ている候補の行はそのまま残す(ADR-0177 決定1)。
        送るのは、片方だけのファイルから、同じ側で名前が重なるファイル・断った名前(制御文字を含む)を除いた名前だけ。
        hostname の候補のファイルも送る。確定した組のファイルは片方だけの一覧に無いので送らない。
        見え方の決まり(ADR-0150)は足さない(ADR-0177 決定4)。
        設定は押すたびに読む。自動送信の設定は読まない(オンでも確認を出す。ADR-0159 決定4)。
        """
        with self._lock:
            if not self._can_ask_pair_ai():
                return self._ok()
            assert self._edit is not None
            edit = self._edit
            try:
                settings = load_settings(**self._where)
            except SettingsError as exc:
                return self._ok(self._notice(str(exc), BATCH_LLM_SETTINGS_TITLE))
            # 組の案を聞く送信だけ、答えの見え方の決まりを足さない(送るのは確認に出した文そのもの。ADR-0177 決定4)
            plan = plan_llm(settings, client_factory=self._client_factory, look_rules=False)
            if plan.send is None or plan.send.client is None:
                return self._pair_ai_notice(batch_pair_ai_unavailable(plan.first.unavailable))
            words = normalize_words(plan.send.extra_secret_words)
            targets = batch_edit.pair_ai_targets(edit.only_before, edit.only_after, words)
            if not targets.before or not targets.after:
                return self._pair_ai_notice(BATCH_PAIR_AI_NO_FILES)
            total = len(targets.before) + len(targets.after)
            if total > MAX_PAIR_NAMES:
                return self._pair_ai_notice(batch_pair_ai_too_many(total, MAX_PAIR_NAMES))  # 一部だけ送ることはしない
            prompt = build_pair_prompt(list(targets.before), list(targets.after))
            if prompt_too_long(prompt):
                return self._pair_ai_notice(batch_pair_ai_too_long(MAX_PROMPT_CHARS))
            # 確認の間は門を持つ(比較の画面は走らせない。欄・切り替え・組を直す操作も止まる)
            if not self._session.acquire(self):
                return self._ok()
            number = self._next_serial()
            self._pair_ai = (number, prompt, (targets.before, targets.after), plan, self._pairs_revision)
            name = destination_of(settings)[1]
            # 送る全文・送り先・送らないファイル。キー・CLI の場所は入れない。
            # この送信は見え方の決まりを足さないので、client に渡る文はこの prompt と 1 文字も違わない(ADR-0177 決定4)
            self._pair_ai_view = {
                "id": number,
                "title": BATCH_PAIR_AI_TITLE,
                "prompt_title": BATCH_PAIR_AI_PROMPT_TITLE,
                "prompt": prompt,
                "destination": batch_pair_ai_destination(name, display_model(settings), effective_base_url(settings)),
                "held_back_title": BATCH_PAIR_AI_HELD_BACK,
                "held_back": list(targets.held_back),
                "note": BATCH_PAIR_AI_NOTE,
                "send": BATCH_APPROVAL_SEND,
                "dont_send": BATCH_APPROVAL_DONT_SEND,
            }
            self._late_notice = None
            result = self._ok()
        self._session.gate_changed(self)
        return result

    def _answer_pair_ai(self, number: Any, yes: Any) -> dict[str, Any]:
        """確認の答え。待っている確認と同じ番号のときだけ受け、1 回答えたら消す。

        yes が True のときだけ、確認に出した文をワーカーのスレッドで 1 回送る(門は持ったまま。答えが届いたら返す)。
        「送らない」・Esc(yes が偽)と形の違う yes は送らずに門を返す。番号が違う・整数でないときは何もしない。
        """
        with self._lock:
            pending = self._pair_ai
            if pending is None or self._closing or self._pair_asking is not None:
                return self._ok()  # 待っている確認が無い。何も送らない
            if not _is_int(number) or number != pending[0]:
                return self._ok()  # 古い確認・違う確認の答え(待っている確認はそのまま)
            self._pair_ai, self._pair_ai_view = None, None  # 答えたら消す(1つの確認で送れるのは1回だけ)
            if yes is not True:
                self._session.release(self)
                result = self._ok() if isinstance(yes, bool) else _failure(gui_web_call_failed())
                released = True
            else:
                sent_number, prompt, (before, after), plan, revision = pending
                assert plan.send is not None and plan.send.client is not None
                self._start_pair_asking(plan.send.client, prompt, before, after, revision, sent_number)
                result = self._ok()
                released = False
        if released:
            self._session.gate_changed(self)
            if not result["ok"]:
                self._changed()  # 画面は失敗の文だけを受け取るので、読み直させる
        return result

    def _start_pair_asking(
        self,
        client: LLMClient,
        prompt: str,
        before: dict[str, Path],
        after: dict[str, Path],
        revision: int,
        number: int,
    ) -> None:
        """ロックの中で呼ぶ(門を持っている)。送るスレッドを始める。送る文はスレッドの関数のローカル変数だけ。"""

        def run(asking: _PairAsking) -> None:
            self._pair_ask_in_thread(asking, client, prompt, before, after)

        self._pair_asking = _PairAsking(run, revision, number, self._status)
        self._status = BATCH_PAIR_AI_ASKING
        self._pair_asking.start()

    def _pair_ask_in_thread(
        self,
        asking: _PairAsking,
        client: LLMClient,
        prompt: str,
        before: dict[str, Path],
        after: dict[str, Path],
    ) -> None:
        """ワーカーのスレッドで 1 回だけ送る。門はどの終わり方でも返す。失敗は理由の種類と型の名前・状態コードだけにする。

        中止した・窓を閉じた・組の一覧が変わった後に届いた答えは捨てる。答えの文は view・知らせ・ログに入れない。
        """
        answer: str | None = None
        failure: tuple[str, str, int | None] | None = None
        try:
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
        finally:
            with self._lock:
                if self._pair_asking is asking:
                    self._pair_asking = None
                self._session.release(self)
                if not self._closing:
                    self._status = CANCELLED if asking.cancelled else asking.status
                    keep = (
                        not asking.cancelled
                        and self._edit is not None
                        and self._pairs_revision == asking.revision
                        and set(self._edit.only_before) >= set(before.values())
                        and set(self._edit.only_after) >= set(after.values())
                    )
                    if keep:
                        self._take_pair_answer(asking.number, answer, failure, before, after)
            self._session.gate_changed(self)
            self._changed()

    def _take_pair_answer(
        self,
        number: int,
        answer: str | None,
        failure: tuple[str, str, int | None] | None,
        before: dict[str, Path],
        after: dict[str, Path],
    ) -> None:
        """ロックの中で呼ぶ。届いた答えのうち読めた組を候補にする(組は変えない。ADR-0162)。

        全体を読めないか、読めた組が 0 なら何も変えずに知らせる。読めなかった組があれば種類と数を知らせる。
        知らせには答えの本文も答えの中の名前も入れない。
        """
        body: str | None = None
        if failure is not None or answer is None:
            body = batch_pair_ai_failed(*(failure or ("api_error", "InvalidAnswer", None)))
        else:
            reading = read_pair_answer(answer, list(before), list(after))
            pairs = reading.pairs
            if reading.reason is not None:
                body = batch_pair_ai_unreadable(reading.reason, ())  # 組も候補も変えない
            elif not pairs:
                body = batch_pair_ai_unreadable(None, reading.skipped) if reading.skipped else BATCH_PAIR_AI_NONE
            else:
                if reading.skipped:
                    body = batch_pair_ai_partial(len(pairs), reading.skipped)
                # hostname の候補は残し、前の AI の案は入れ替える。チェックの既定はオフ(画面が持つ)
                kept = [row for row in self._suggestions if row[1] != AI_SUGGESTION_SOURCE]
                # hostname の候補と変更前・変更後が同じ組は AI の行にしない(1 行にする。ADR-0177 決定2)
                shown = {(row[2], row[3]) for row in kept if row[1] == SUGGESTION_SOURCE}
                rows = [
                    (f"{AI_SUGGESTION_SOURCE}-{number}-{index}", AI_SUGGESTION_SOURCE, before[b], after[a], BATCH_PAIR_AI_HINT)
                    for index, (b, a) in enumerate(pairs, start=1)
                    if (before[b], after[a]) not in shown
                ]
                self._suggestions = [*kept, *rows]
                self._result_revision += 1
        if body is not None:
            self._late_notice = {"id": self._next_serial(), **self._notice(body, BATCH_PAIR_AI_TITLE)}

    # --- 閉じる ---

    def _closed(self) -> None:
        """窓が閉じた(WebSession が窓の closed の知らせで呼ぶ)。実行中なら中止を頼み、戻ってきた結果は捨てる。

        送信前の確認(AI に案を聞く確認も)を待っていたら、送らずに門を返す。実行中の門は、ワーカーが戻ったときに返す
        (AI に案を聞いている門は、答えが届いたときにスレッドが返す)。2回目からは何もしない。
        """
        with self._lock:
            if self._closing:
                return
            if self._worker is not None:
                self._worker.cancel()  # job の境界で止まる
            if self._pair_asking is not None:
                self._pair_asking.cancelled = True  # AI に案を聞いている。届いた答えは捨て、門はスレッドが返す
            self._closing = True
            # 送信前の確認(AI で解析)か、AI に案を聞く確認を待っていたら、送らずに門を返す
            waiting = (self._approval is not None and self._worker is None) or self._pair_ai is not None
            self._approval = None
            self._pair_ai, self._pair_ai_view = None, None
            if waiting:
                self._session.release(self)
        self._session.unwatch(self)
        if waiting:
            self._session.gate_changed(self)

    def _close(self) -> dict[str, Any]:
        self._closed()
        with self._lock:
            result = self._ok()
        if self._window is not None:
            self._window.destroy()
        return result
