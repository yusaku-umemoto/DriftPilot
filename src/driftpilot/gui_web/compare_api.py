"""pywebview の比較画面の Python の側(REQ-105 / ADR-0074 決定3)。

画面の状態(入力・表示中の結果・実行中か・待っているプレビュー)はここに持ち、判断は screen_model/model.py の関数を
呼ぶだけにする(wx の比較画面 gui/app.py の ComparisonFrame と同じ動き)。比較は screen_model/worker.py の CompareWorker で動かす。
JavaScript(static/js/compare.js)は返した JSON を描くことと、操作をここへ渡すことだけをする。

- どのメソッドも {"ok": true, "view": {...}} を返す。知らせ(wx の MessageBox)は "notice" を足す。
  形の違う引数と想定外の例外は {"ok": false, "message": <文>}(bridge の _guarded)
- 入力の本文(config)はここ(SideSource の中)にだけ置く。画面に渡すのは名前と要約と、結果の行だけ。
  貼り付けの文字は属性・ログ・返す値に入れない
- 結果の行は view に入れない(数万行になる)。view には result_revision だけを入れ、画面は変わったときだけ result() を呼ぶ
- ワーカーのスレッドから状態を触るのはロックの中だけ。画面への知らせ {"type": "compare.changed"} はロックの外で送る
  (evaluate_js は画面の返事を待つことがある)。窓が閉じた後の知らせの失敗は握りつぶす
- 「AI に送る」(send_llm。ADR-0080 決定1): 比較の後に押す。チェックボックスは無く、form の llm は使わない
  (画面の入力 ScreenInput の llm はいつも偽なので、AI を送った後も比較したときの入力と同じで、保存できる)。
  押せるのは、最後の結果があり、入力が比較したときと同じで、送る差分があり(payload.source_of)、比較中でないとき(can_send_llm)。
  押すと最後の結果の入力で比べ直し、1回目は送らない(dry_run)
- 送信前のプレビュー(ADR-0018 決定6): 待っているプレビューがあり、入力が比較したときと同じで、
  「送信する」と答えたときだけ、承認した内容で2回目を始める(approve。送る直前の照合は core)。1つのプレビューで送れるのは1回だけ
- 自動送信([gui] auto_send_llm。ADR-0080 決定2): 「比較実行」・自動の比較が終わって結果を出した後、オンで can_send_llm なら、
  確認なしに plan_llm の send で送る比較を裏で始める(approve を通さない)。送っている間の状態の文は「AI に送っています」。
  届いた答えは、入力が比較したときと同じときだけ出す(違えば捨てる)。設定は決めるたびに読む。オンの間は帯に送り先を出す(auto_send)。
  一括比較の窓・CLI・wx の画面は読まない
- 追加の質問(ask_follow_up。REQ-118 / ADR-0081): 最後の結果の llm が送って答えが届いたもの(status=done・transmitted)で、
  入力が比較したときと同じで、比較中・質問中でないときだけ押せる(_can_ask)。質問は llm_analyzer/follow_up.py で確かめてから、
  最初に送った全文・これまでのやり取り・質問を1つの文にして、ワーカーのスレッドで plan_llm の send の client.ask に渡す
  (確認は出さない。ADR-0081 決定3)。送っている間は門を持ち、ほかの操作は止める。「中止」は届いた答えを捨てるだけ。
  やり取りは _turns にだけ持ち(答えが届いたものだけ)、比較を始める・結果を消す・入力を変える・窓を閉じると消す。
  質問の文はログ・知らせ・例外に残さない(送るときはスレッドのローカル変数だけ)
- 最初の依頼(ADR-0179 決定1 / REQ-242 §2): 承認した送信・自動送信・まとめての比較の全体の送信で答えが届いたとき、
  打った文(確かめを通った文そのもの)か「プロンプト『…』で解析」「自動で送りました（プロンプト『…』）」を _first_request に持ち、
  view の follow_up.first_request で渡す(画面は最初の答えの上の右の吹き出し)。やり取りと同じときに消す(_clear_follow_up)
- やり取りの保存(ADR-0179 決定2 / REQ-242 §3): 「結果を保存…」で、最初の依頼の文・送り先の名前・_turns を
  CompareOutcome.conversation に入れて渡す(書くかは storage の決まり。答えが無い・やり取りの無い保存は今と1文字も変えない)。
  まとめての比較は全体の答えを入れた行に入れる。考えている内容・接続先・キー・CLI の場所は入れない
- 開いた結果からの追加の質問(ADR-0179 決定3 / REQ-242 §4): 開いた保存に送って届いた AI の答えがあれば、_first_answer は
  その答え(.jsonl は全体の答えの行)を返し、保存からやり取り・最初の依頼・送り先の名前(名前で比べる)を戻す(_opened_saved)。
  入力の欄は「質問する」だけに使える。送る文は保存の payload・保存したやり取り・新しい質問だけ(入力のファイルは読まない)。
  伏せる語は保存の語と今の設定の語。保存に語が無ければ送る前に1回確かめる。送り先の記録が無いときも確かめる。
  上限には保存したやり取りも数える。「比較実行」「AI で解析」「入れ替え」「差分＋全文を送る」・出力は今どおり押せない。
  「結果を保存…」は、保存日時を今にし、やり取りだけを今のものに替えて新しいファイルに書く(上書きしない)
- ファイルのダイアログは、ここから窓の create_file_dialog で開く(開いている間はロックを持たない)
- 一括比較の「比較画面で開く」は _load_handoff(WebSession が呼ぶ。画面からは呼べない)。入力欄と設定だけを埋める(REQ-106 §2.2)。
  自動の比較は始めない(ADR-0076 決定2)。ただし一括比較の AI の答えを持ってきたときは比較を始め(RUN_HANDOFF。AI は呼ばない)、
  比べ直した差分の本文が一括比較で送った文と同じときだけ、その答えを結果に付ける(ADR-0170)。入力や比較の設定を変えたら答えは捨てる。
  まとめて比べた組の全体の答えは、比べ直したまとめての比較の全体の本文が同じときだけ全体の答えとして付ける(attach_whole_answer。REQ-241 §5)
- ファイルのドロップは _on_drop(app.watch_drop が窓の読み込みの後に印の要素(#drop-anchor)の drop に登録する。REQ-205・REQ-110 / ADR-0076 決定1)。
  パスは事象の dataTransfer.files[*].pywebviewFullPath だけから取り、画面のスクリプトからパスを受け取るメソッドは作らない。
  2つのときの順は drop_order.order_pair(更新日時・語・数字・名前の順。ADR-0078 決定1)。
  pywebview が事象ごとに別のスレッドで呼ぶので、状態に触るのはロックの中だけ。結果は compare.changed の知らせで画面に届ける
- 自動の比較(ADR-0076 決定2・ADR-0078 決定2・3): 「参照…」「貼り付け…」・ドロップ・「入れ替え」で入力を変えた後と、
  form_changed で比較の設定(比較方式・種類・機種・show のコマンド・区間)が前回と違うとき、[gui] auto_compare がオンで
  「比較実行」を押せるなら、「比較実行」と同じ処理(_begin)で始める。設定は始めるかどうかを決めるたびに読む。
  「比較実行」と同じく AI は呼ばない(ADR-0080 決定1)。比較中に設定を変えても、終わった後にやり直さない
- 「入れ替え」(swap。ADR-0078 決定2): 入力の中身と名前・区間の選択・要約を入れ替える。比較を始めなかったときは、
  向きの違う結果を残さないよう、表示中の結果を消して「結果なし」に戻す
- 「結果を開く…」(open_result。REQ-120 / ADR-0083): 窓のファイルのダイアログで選んだ保存ファイル(.json)を読むだけで表示する。
  パスは画面から受け取らない。比較中・質問中・閉じている途中は何もしない。200MB を超えるファイルは読まない。
  読めないときは決まった文の知らせ(ファイルの中身・パス・storage の例外の文を入れない)。表は比較したときと同じ view_outcome で作り、
  帯に保存日時と入力の名前・機密の注記を出す(opened)。表示中は _last が無いので「AI に送る」は出ず(追加の質問と保存し直しは
  保存に AI の答えがあるときだけ。REQ-242 §4)、「比較実行」「入れ替え」・自動の比較も始めない。開く前の入力(中身・名前・区間・要約)には触らない。
  「閉じる」(close_result)か、新しい入力(参照・貼り付け・ドロップ・一括比較からの引き渡し)で表示を消す。
  表示を消したその入力では自動の比較を始めない。保存した AI の答えは文字として出すだけ(送らない)
- 複数のコマンドのまとめての比較(REQ-122 / ADR-0084 決定3・5・6): 入力のどちらかに区間が複数あり、区間の選択が両方「自動」なら、
  比べる関数(model.compare_screen)が core.compare_all で組ごとに比べる。区間を選べば今の1組の比較(片側だけなら core の選択待ち)。
  結果は _multi(入力・要求・まとめた結果)に持ち、表示は _mview(一覧の行・全体の文)と選んでいる行 _pair。
  view の multi に一覧の行・選んでいる行・全体の文を入れる(行は短い)。result() は選んでいる組の分だけを渡す(相手なしの行は空の表と説明)。
  窓の下の左に全体の文、右に選んだ組の REQ-113 の内訳を出す。区間の選択欄には区間の一覧を入れ、選択は「自動」のまま
- まとめて比べたときの「AI に送る」(ADR-0152 決定3): 全部の組を1つの文にして1回で送る(core.analyze_all。答えは MultiCompareOutcome.llm)。
  1回目は送らずに比べ直し(dry_run)、送る文ができれば1組と同じ送信前の確認(_preview。全文を見せる)を1回だけ出す。
  全体の上限を超えた・送る内容が無いときは確認を出さずに一括比較と同じ文で知らせる。「送信する」で承認した文だけを送る(approve)。
  組のチェックは無い。自動送信は確認なしに1回で送る(model.auto_send_all)。どちらも届いたところから出す(ADR-0152 決定10)
- 追加の質問は全体の答えに対して1つのやり取り(_turns)。行を選び直しても AI の面とやり取りは変わらない。送り先の記録も全体で1つ(番号 0)
- まとめて比べたときの「結果を保存…」は JSON Lines(1行が1組。相手なしは入れず、あれば知らせる)。全体を送ったときは最初の組の行に
  全体の答え、ほかの組は in_whole(model.whole_pair_results)。「結果を開く…」は .jsonl も一覧で読む
- 「全部」のタブ(ADR-0152 決定7): view の multi に all_title・all_revision を入れ、行は all_rows() で渡す(行を選ぶたびに送らない)。
  行に合うタブ(決定8): view の multi の tab・tab_revision。行を選ぶと model.pair_tab のタブ、比べ終わる(keep が偽)と all。
  画面は tab_revision が変わったときだけタブを移す
- pywebview は js_api の _ で始まらない属性をたどって画面に出すので、内部の属性とメソッドは _ で始める
- AI の答えの欄(REQ-204 / ADR-0146): result() の llm は知らせの行と AI が返したままの答え、llm_blocks はそれを
  model.answer_blocks で読み取ったブロック。追加の質問の回答の行にも blocks を添える。画面(js/answer_view.js)は
  createElement と textContent だけで描き、答えを HTML として扱わない
- 窓の配置(REQ-206 / ADR-0148): result() の llm は頭の行を除いた行(model の llm_screen_lines)。llm_copy と追加の質問の
  回答の copy はコピーのアイコンで入れる文(<red> を外した答え)。view の ai は AI の面の操作(プロファイルと指示文の名前と使用中の
  位置・1つのボタンの判定の材料・欄を空にする番号)。名前だけを渡し、接続先の URL・キー・CLI の場所は渡さない
- 解析の前に打った文(send_llm の instruction。ADR-0148 決定6): check_question で確かめ、断れば送らない(追加の質問の断りと同じ知らせ)。
  通れば計画の1回目と2回目の両方に入れる(model.with_instruction)ので、送信前の確認の全文・送る文・答えの payload に入る。
  打った文を足した文が prompt_too_long を超えたら確認を出さずに知らせる。自動送信では足さない。承認して送った答えが届いたら欄を空にさせる
- 送り先の確かめ(ADR-0148 決定7): 承認して送った回と自動送信の回の送り先のプロファイルを組ごとに覚え、追加の質問の前に今の使用中と比べる。
  違えば送らずに confirm を返し、画面が同じ番号で呼び直したときだけ送る(その組の記録を今の送り先に替える)
- AI の面の切り替え(use_ai_profile・use_prompt_profile): 比較中・質問中は断る。settings_model の use_selected・use_prompt で
  使用中を変え、設定の窓が開いていれば WebSession.settings_changed で読み直させる。結果と答えは消さない
- 「HTML・CSV で出力…」(export_result。ADR-0156 / REQ-216): 押せるのは保存と同じ条件で、保存した結果を開いていないとき(can_export)。
  設定の秘密の語を読めなければ出さない。差分の行・組の名前・ファイル名は AI に送るときと同じ判定で伏せ(model の compare_report・
  multi_report)、新しいファイルに書く(上書きしない)。伏せずに出す口は無い

- 届いたところから答えを出す(ADR-0157 決定5〜8 / REQ-217 §5): 承認して送る2回目(_answer_preview)・1組の自動送信(_auto_send)・
  追加の質問(_ask_in_thread)だけ、要求に on_delta(_AnswerStream)を付ける。届いた文字は 0.15 秒に1回まで
  {"type": "compare.partial", "revision", "blocks", "target"} で画面に知らせ(ロックの外)、view の partial にも持つ。
  途中の文字は _turns・LlmAnalysis.answer・保存の結果・追加の質問の文・ログ・例外に入れない。全部届いたら・失敗したら partial は null。
  中止(比較の中止・質問の中止・窓を閉じる)では on_delta が StreamStopped を出して読むのをやめ、ためた文字があれば
  stopped と注記を付けて partial に残す(保存しない)。次の送信・結果を消す・入力を変えるで消す(1組の比較の組の切り替えは無い)。
  まとめての比較の全体の送信(承認した2回目・auto_send_all)にも付ける(ADR-0152 決定10)
- AI が考えている内容(ADR-0169 / REQ-231 §5): on_delta を付ける所に on_thinking(_AnswerStream.thinking)も付ける。
  考えは ThinkingBuffer にため(見えない文字を外し、20,000 文字で切る)、答えと同じ 0.15 秒の決まりで compare.partial と
  view の partial に "thinking": {text, cut, cut_note} か null を入れる(答えの文字が無いときの blocks は [])。
  全部の答えが結果ややり取りに入ったら、途中の答えを消す前に考えを _thinking に移し、view の thinking({…, target, revision})で返す。
  中止は partial に考えも残し、失敗は partial も thinking も消す。次の送信・結果を消す・入力を変える・閉じると消す。
  考えの文は _turns・LlmAnalysis・保存・追加の質問の文・コピーの文・ログ・例外に入れない
- 「差分＋全文を送る」(set_full_text。ADR-0158 / REQ-218): 状態は _full_text にだけ持ち、新しく比較を始める(_begin)・結果を消す
  (_clear_result。開いた結果を閉じたときも)・入力を変える(_set_source・_swap)と外す。押せるのは _full_text_state のときだけ
  (1組の config か show の比較か、まとめての比較で全体の答えが届き、選んでいる組が本文に入れた config か show の組。
  ADR-0182 決定1。押せないとき(まとめての最初の解析など)は理由を title に出す)。まとめての比較の全文は選んでいる組の変更後の区間だけ(follow_up.rebuild_whole_after_text)。
  最初の解析(_send_llm)では1回目と2回目の両方に include_after_text を付け、送信前の確認の先頭に帯を出す。自動送信は付けない。
  追加の質問では、最初の送信と同じ入力で全文を作り直し(follow_up.rebuild_after_text)、最初の送信の文の直後に足す。
  まだ確かめていない組の全文なら送らずに送信前の確認(_follow_up_preview)を出し、「送信する」で確認に出した文のまま送る
- AI の面を別の窓で出す(open_ai_pane。ADR-0160 / REQ-220): 窓は WebSession.open_ai_pane が開き、_ai_attached・_ai_detached で
  知らせる。状態と門はここに持ったまま。AI の窓の Python の側(ai_pane_api.AiPaneApi)は _call_as("ai", …) でここの処理を呼ぶ
  (form は _form_copy。返す view は "ai" 向け)。view は窓ごとに描く(_render(pane))。別の窓が開いている間は、比較の窓の view の
  preview・approval を null にし、送り先の確認も比較の窓には返さない。AI の知らせ(印 "pane": "ai" を付けたもの)は AI の窓の
  view にだけ入れる。compare.changed は両方の窓へ、compare.partial は AI の窓があればそこだけへ送る。
  AI の窓が開いている間は、片方の窓から状態を変える操作をしたら(組の切り替え・入れ替えなど、ワーカーを通らない操作も)、
  もう一方の窓へ compare.changed を送る(_call の後。読むだけの処理 _READ_ONLY では送らない)。
  AI の窓を閉じたら、待っている送信前の確認・全文の確認は「送らない」と同じにする。送っている途中なら止めない
- 比較の窓の小さな直し(ADR-0164 / REQ-225): result() の tabs に構造化比較のタブと構造化不可の行のタブの押せる・押せない
  (model.structured_off。比較方式が「単純」の組と show を単純比較で比べた組は押せない)と理由の文、fallback_tab に押せないタブを
  選んでいたときに移るタブ(raw)を入れる。view の busy_result は結果の面のクルクル(比較だけのとき)、ai_busy は AI に送る間の
  AI の面のクルクルの文(途中の答えが出ている間は null)。busy は今のまま入力の欄とボタンの押せる条件
- API が返したエラーの文(ADR-0168 決定5 / REQ-230 §7): 比較の窓が送る全部の道(承認した2回目・1組の自動送信・まとめての比較の
  全体の送信・追加の質問)で、LLMResponseError の api_error を利用者の秘密の語でもう一度掃除した文と秒を _api_error に持つ
  (解析は要求の on_api_error に _ApiErrorSink を付ける。追加の質問は例外から読む)。view の api_error は AI の面の折りたたみの
  題・文・注記・番号(文が無ければ null)。429 で秒があれば、結果の AI の行と追加の質問の知らせに秒を出す。
  次の送信・結果を消す・入力を変える・比較し直す・閉じる・答えがやり取りに入ると消す。ログ・例外・保存・_turns・コピーの文に入れない

webview は読まない(窓は app.py が開いて WebSession.attach_compare で渡す。テストは偽の窓を渡す)。
"""

import dataclasses
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

from driftpilot.batch_io import JSONL_SUFFIX, ResultsWriteError, write_new_bytes
from driftpilot.core import (
    DEFAULT_MAX_TOTAL_DIFFS,
    CompareCancelled,
    CompareOutcome,
    CompareRequest,
    Conversation,
    LlmRequest,
    MultiCompareOutcome,
    attach_whole_answer,
    llm_inputs,
    run_compare,
    whole_sections,
)
from driftpilot.gui_web.bridge import Api, _failure, _guarded, notify
from driftpilot.gui_web.drop_order import dropped_paths, order_pair
from driftpilot.gui_web.session import WebSession
from driftpilot.llm_analyzer import THINKING_CUT_NOTE, ApiErrorText, LLMClient, ThinkingBuffer, for_display
from driftpilot.llm_analyzer.client import LLMConnectionError, LLMResponseError, StreamStopped, ask_with_deltas
from driftpilot.llm_analyzer.follow_up import (
    ANSWER,
    MAX_PROMPT_CHARS,
    MAX_QUESTION_CHARS,
    MAX_TURNS,
    QUESTION,
    REBUILD_MISMATCH,
    REBUILD_TOO_LONG,
    build_follow_up,
    check_question,
    prompt_too_long,
    rebuild_after_text,
    rebuild_whole_after_text,
    same_sent_body,
)
from driftpilot.llm_analyzer.masking import normalize_words
from driftpilot.llm_analyzer.payload import AfterText, source_of
from driftpilot.llm_analyzer.prompt import with_after_text
from driftpilot.llm_analyzer.types import LlmAnalysis
from driftpilot.llm_settings import (
    WORKDIR_CONFIG,
    LlmSettings,
    SettingsError,
    auto_compare_enabled,
    auto_send_llm_enabled,
    auto_send_target,
    auto_update_check_enabled,
    character_enabled,
    load_settings,
)
from driftpilot.messages import (
    BATCH_NOT_SENT_TITLE,
    BATCH_SAVED_TITLE,
    COMPARE_AI_ANALYZE,
    COMPARE_AI_ANALYZE_CHARACTER,
    COMPARE_AI_PROFILE_FAILED_TITLE,
    COMPARE_API_ERROR_NOTE,
    COMPARE_API_ERROR_TITLE,
    COMPARE_AUTO_SENDING,
    COMPARE_DROP_FOLDER,
    COMPARE_DROP_NO_PATH,
    COMPARE_DROP_TITLE,
    COMPARE_DROP_TOO_MANY,
    COMPARE_FAILED_TITLE,
    COMPARE_FIRST_REQUEST_AUTO,
    COMPARE_FIRST_REQUEST_NO_PROMPT,
    COMPARE_FIRST_REQUEST_PROMPT,
    COMPARE_FOLLOW_UP_ASK,
    COMPARE_FOLLOW_UP_ASKING,
    COMPARE_FOLLOW_UP_NOTICE_TITLE,
    COMPARE_FULL_TEXT_BUSY,
    COMPARE_FULL_TEXT_MISMATCH,
    COMPARE_FULL_TEXT_MULTI_FIRST,
    COMPARE_FULL_TEXT_NOT_CONFIG,
    COMPARE_FULL_TEXT_NOT_SENT,
    COMPARE_FULL_TEXT_NOT_YET,
    COMPARE_FULL_TEXT_NOTE,
    COMPARE_FULL_TEXT_OPENED,
    COMPARE_FULL_TEXT_PAIR,
    COMPARE_FULL_TEXT_UNPAIRED,
    COMPARE_HANDED_ANSWER_TITLE,
    COMPARE_HANDED_ANSWER_UNUSED,
    COMPARE_LLM_SETTINGS_TITLE,
    COMPARE_MULTI_NOTHING_TO_SAVE,
    COMPARE_OPEN_FAILED_TITLE,
    COMPARE_PARTIAL_STOPPED,
    COMPARE_PROMPT_FAILED_TITLE,
    COMPARE_READ_FAILED_TITLE,
    COMPARE_SAVE_FAILED_TITLE,
    COMPARE_SAVED_NO_WORDS,
    COMPARE_SEGMENTS,
    COMPARE_SEGMENTS_WAITING,
    COMPARE_TAB_RAW,
    COMPARE_TAB_STRUCTURED,
    COMPARE_TAB_UNPARSED,
    DIALOG_NO,
    DIALOG_OK,
    DIALOG_YES,
    EXPORT_REDACT_FAILED,
    FOLLOW_UP_ANSWER_LABEL,
    FOLLOW_UP_QUESTION_LABEL,
    LIMITS_SCREEN_HINT,
    LLM_PREVIEW_DONT_SEND,
    LLM_PREVIEW_PAYLOAD,
    LLM_PREVIEW_SEND,
    LLM_PREVIEW_TITLE,
    all_tab_title,
    auto_send_band,
    auto_send_settings_unreadable,
    batch_llm_nothing_to_send,
    batch_llm_over_limit,
    compare_filter_all,
    compare_handed_answer_note,
    compare_paste_title,
    compare_raw_folded,
    compare_save_note,
    compare_saved_no_profile,
    compare_warning_line,
    export_saved,
    export_settings_unreadable,
    follow_up_destination_changed,
    follow_up_failed,
    follow_up_limit,
    follow_up_refused,
    follow_up_too_long,
    follow_up_unavailable,
    full_text_too_long,
    gui_web_call_failed,
    instruction_too_long,
    llm_full_text_preview,
    llm_screen_lines,
    multi_unpaired_not_saved,
    saved_notice,
    user_prompt_template_ignored,
    workdir_api_key_ignored,
    workdir_limits_ignored,
)
from driftpilot.screen_model.batch_model import JSONL_WILDCARD, limit_lines
from driftpilot.screen_model.model import (
    AUTO_PLATFORM,
    CANCELLED,
    EXPORT_FILE_TYPES,
    EXPORT_KINDS,
    EXPORT_PAIRS,
    KIND_CHOICES,
    MODE_CHOICES,
    PASTED_NAME,
    SIDE_LABEL,
    SIDES,
    InputReadError,
    LlmPlan,
    MultiView,
    OutcomeView,
    PreviewView,
    RedactError,
    SavedResultError,
    ScreenInput,
    Side,
    SideSource,
    SideView,
    WEB_PLATFORM_CHOICES,
    AllRow,
    all_marks,
    all_rows,
    answer_blocks,
    answer_copy_text,
    approve,
    with_current_premise,
    auto_send_all,
    build_request,
    build_save_record,
    category_counts,
    compare_report,
    compare_screen,
    export_bytes,
    export_choices,
    export_filename,
    export_words,
    find_diff,
    fold_ranges,
    input_summary,
    is_current,
    multi_report,
    multi_view,
    opened_multi,
    opened_view,
    opened_answer_index,
    pair_records,
    pair_tab,
    pasted,
    plan_llm,
    preview_view,
    raw_marks,
    read_file,
    read_saved_lines_raw,
    read_saved_raw,
    resaved_lines,
    save_multi,
    save_result,
    save_state,
    segment_index,
    segment_items,
    segment_number,
    segment_views,
    stage_label,
    structured_marks,
    suggested_filename,
    suggested_multi_filename,
    view_outcome,
    wants_preview,
    web_kind_platform,
    web_settings_summary,
    whole_answer_lines,
    whole_holder,
    with_full_text,
    with_instruction,
)
from driftpilot.screen_model.batch_model import CompareHandoff, HandedAnswer
from driftpilot.screen_model.model import (
    RAW_FOLD_CONTEXT,
    RAW_MARK_LABEL,
    RawForm,
    raw_examples,
    raw_form_defaults,
    raw_form_values,
    raw_move_labels,
    raw_options_summary,
    raw_set_aside_rows,
    read_context_lines,
    read_raw_form,
    saved_raw_settings_line,
)
from driftpilot.messages import RAW_OPTIONS_INVALID_TITLE
from driftpilot.screen_model.settings_model import (
    ProfileChoices,
    destination_of,
    llm_choices,
    prompt_choices,
    use_prompt,
    use_selected,
)
from driftpilot.screen_model.worker import CompareWorker
from driftpilot.storage import StorageError, StoredRecord, resaved_record

# Python から画面への知らせの種類。static/js/compare.js が受け、state() を呼び直す
CHANGED_EVENT = "compare.changed"
# 「設定…」で開けるタブ(省くと今と同じ。知らせの「更新…」は "update"。REQ-252 §5)
SETTINGS_TABS = (None, "update")
# 届いたところまでの答えの知らせ(ADR-0157 決定5)。static/js/compare.js が受け、最後の左の吹き出しに描く
PARTIAL_EVENT = "compare.partial"
# 途中の答えを知らせる間隔の下限(秒)。時計はテストで差し替える
STREAM_PAINT_SECONDS = 0.15
_clock = time.monotonic
# 途中の答えをどこに描くか(最初の解析の答えか、追加の質問の答えか)
TARGET_ANALYSIS, TARGET_FOLLOW_UP = "analysis", "follow_up"
# 窓のファイルのダイアログの種類(webview.FileDialog の OPEN・SAVE の値。webview は読まない)と、選べるファイルの種類(wx と同じ)
DIALOG_OPEN = 10
DIALOG_SAVE = 30
INPUT_FILE_TYPES = ("テキスト (*.txt;*.log;*.cfg;*.conf)", "すべてのファイル (*.*)")
SAVE_FILE_TYPES = ("JSON (*.json)",)
# まとめて比べた結果の保存(JSON Lines。一括比較の窓と同じ)
MULTI_SAVE_FILE_TYPES = (JSONL_WILDCARD.split("|")[0],)
OPEN_RESULT_FILE_TYPES = ("保存した結果 (*.json;*.jsonl)",)
MODE_VALUES = tuple(value for value, _ in MODE_CHOICES)
KIND_VALUES = tuple(value for value, _ in KIND_CHOICES)
TAB_ORDER = ("structured", "raw", "unparsed")
# 「全部」のタブ(ADR-0152 決定7)。まとめて比べたときだけ結果のタブの先頭に出す。行は all_rows() で渡す(TAB_ORDER には入れない)
TAB_ALL = "all"
# 結果が無いときのタブの題(wx の最初の題と同じ)
EMPTY_TITLES = {"structured": COMPARE_TAB_STRUCTURED, "raw": COMPARE_TAB_RAW, "unparsed": COMPARE_TAB_UNPARSED}
# 画面の欄の値(form)の既定。AI のチェックボックスは無い(「AI に送る」のボタン。ADR-0080 決定1)。
# 種類と機種は「自動」(ADR-0151 決定4。wx の既定は config・指定なしのまま)。config_platform は WEB_PLATFORM_CHOICES の番号で、0 が自動
DEFAULT_FORM: dict[str, Any] = {
    "mode": MODE_VALUES[0],
    "kind": "auto",
    "config_platform": 0,
    "platform": "",
    "command": "",
    "before_segment": 0,
    "after_segment": 0,
}
# 比較を始めた理由(終わったときの扱いが違う)。比較だけ・送信前の確認の1回目・承認した2回目・自動送信
RUN_COMPARE, RUN_PREVIEW, RUN_APPROVED, RUN_AUTO_SEND = "compare", "preview", "approved", "auto_send"
# 一括比較の答えを持ってきた「比較画面で開く」の後の比較(ADR-0170 決定2)。表示は RUN_COMPARE と同じ。AI は呼ばず、自動送信もしない
RUN_HANDOFF = "handoff"
# view を描く窓(ADR-0160 決定6)。比較の窓と AI の窓。AI の知らせには印 NOTICE_PANE: PANE_AI を付ける(画面には出さない)
PANE_COMPARE, PANE_AI = "compare", "ai"
NOTICE_PANE = "pane"


class _BadRequest(Exception):
    """画面から受け取った引数の形が違う。"""


def _filters(view: OutcomeView | None, context: int = RAW_FOLD_CONTEXT) -> dict[str, Any]:
    """result() に足す、差分の要約と絞り込みの値(ADR-0155 決定1・2 / REQ-215 §2)。結果が無ければ空の一覧。

    - raw_folds: 「差分のみ表示」で折りたたむ範囲(raw_rows の番号)と、その行の文。context は前後の行数(REQ-228 §2)
    - categories: 構造化比較の表の上のチップ(件数の多い順、同数は名前の順)。種類が 2 つ以上のときだけ入れ、1 つ以下は []
    - categories_all: 「すべて N」の文(N は構造化比較の行の総数)
    件数と文字は Python が作り、行を隠すのは画面が行う。
    """
    if view is None:
        return {"raw_folds": [], "categories": [], "categories_all": compare_filter_all(0)}
    counts = category_counts(view.structured_rows)
    return {
        "raw_folds": [
            {"start": fold.start, "count": fold.count, "text": compare_raw_folded(fold.count)}
            for fold in fold_ranges(view.raw_rows, context)
        ],
        "categories": [{"value": name, "label": f"{name} {count}"} for name, count in counts] if len(counts) >= 2 else [],
        "categories_all": compare_filter_all(len(view.structured_rows)),
    }


# 押せないタブを選んでいたときに移るタブ(ADR-0164 決定1)
FALLBACK_TAB = "raw"


def _tab_states(off: str | None) -> dict[str, Any]:
    """result() に足す、構造化比較のタブと構造化不可の行のタブの押せる・押せない(ADR-0164 決定1・2)。

    off は OutcomeView.structured_off(押せない理由の文。押せるなら None)。2 つのタブは同じ値。
    fallback_tab は、選んでいるタブが押せなくなったときに画面が移るタブ。
    """
    state = {"disabled": off is not None, "reason": off}
    return {"tabs": {"structured": dict(state), "unparsed": dict(state)}, "fallback_tab": FALLBACK_TAB}


def _run_at_once(fn: Callable[..., Any], *args: Any) -> None:
    """CompareWorker の post。wx の wx.CallAfter の代わりに、受け取った関数をそのまま(ワーカーのスレッドで)呼ぶ。"""
    fn(*args)


def _is_index(value: Any, count: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < count


def _renamed_paste(source: SideSource | None, side: Side) -> SideSource | None:
    """入れ替えで side に入る入力。貼り付け(path が無い)なら名前をその側の PASTED_NAME にする。ファイルはそのまま。"""
    if source is None or source.path is not None:
        return source
    return dataclasses.replace(source, name=PASTED_NAME[side])


def _answered(llm: LlmAnalysis | None) -> bool:
    """送って答えが届いた AI 解析か(追加の質問ができる結果。ADR-0081 決定1)。"""
    return (
        llm is not None
        and llm.status == "done"
        and llm.transmitted
        and isinstance(llm.payload, str)
        and isinstance(llm.answer, str)
    )


@dataclasses.dataclass(frozen=True, eq=False)
class _OpenedSaved:
    """開いた保存の結果(REQ-242 §4)。保存し直しと、保存の答えへの追加の質問に使う。

    raw は読み込んだ JSON の値(1件は dict、.jsonl は行の dict の並び)、records は組み立てた行(1件は1つ)、
    whole は答えを持つ行の位置(1件は 0、.jsonl は全体の答えを入れた行。無ければ None)、multi は .jsonl か。
    同じ開いた結果かは is で比べる(eq=False)。
    """

    raw: Any
    records: tuple[StoredRecord, ...]
    whole: int | None
    multi: bool

    @property
    def llm(self) -> LlmAnalysis | None:
        """答えを持つ行の AI 解析(無ければ None)。"""
        return None if self.whole is None else self.records[self.whole].outcome.llm

    @property
    def conversation(self) -> Conversation | None:
        """答えを持つ行の保存したやり取り(無ければ None)。"""
        return None if self.whole is None else self.records[self.whole].outcome.conversation


def _prompt_name(settings: LlmSettings) -> str:
    """最初の依頼の文に入れるプロンプトの名前(送ったときの設定の名前。無ければ「-」。ADR-0179 決定1)。"""
    return settings.prompt_profile_name or COMPARE_FIRST_REQUEST_NO_PROMPT


class _Asking:
    """追加の質問を送っている間のワーカー(スレッド1本)。送る文と質問は持たない(スレッドに渡す関数のローカル変数だけ)。

    cancelled は「中止」の印(送った要求は止められないので、届いた答えを捨てる)。
    epoch はやり取りを始めた番号(やり取りを消すと変わる。違えば届いた答えを捨てる)。status はステータスの左の元の文。
    pair は送ったときに選んでいた一覧の行(まとめての比較。違う組に切り替えた後に届いた答えは捨てる。1組の比較は 0)。
    """

    def __init__(self, run: Callable[["_Asking"], None], epoch: int, status: str, pair: int = 0) -> None:
        self.cancelled = False
        self.epoch = epoch
        self.status = status
        self.pair = pair
        self._thread = threading.Thread(target=run, args=(self,), name="driftpilot-follow-up", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def is_alive(self) -> bool:
        return self._thread.is_alive()


class _AnswerStream:
    """要求の on_delta(ADR-0157 決定5)。届いた文字をため、STREAM_PAINT_SECONDS に1回まで画面に知らせる。

    止める条件(比較の中止・質問の中止・窓を閉じている途中)なら StreamStopped を出す(接続が読むのをやめる)。
    文字はこの小さなロックの中でため、知らせは api の _partial_changed がロックの外で送る。
    worker・asking は始めた後に api がロックの中で入れる。ためた文字は text で読む(ログ・例外には入れない)。

    thinking は要求の on_thinking(ADR-0169)。考えの文字を ThinkingBuffer にため(見えない文字を外し、上限で切る)、
    答えと同じ STREAM_PAINT_SECONDS の決まりで知らせる(考えだけが届いている間も)。考えの文は thinking_view で読む。
    """

    def __init__(self, api: "CompareApi", target: str) -> None:
        self._api = api
        self.target = target
        self.worker: CompareWorker | None = None
        self.asking: _Asking | None = None
        self._lock = threading.Lock()
        self._text = ""
        self._thinking = ThinkingBuffer()
        self._changes = 0  # 答えの文字・考えの文字・切った印が変わった回数
        self._painted = 0  # 前に知らせたときの変わった回数
        self._painted_at: float | None = None

    @property
    def text(self) -> str:
        with self._lock:
            return self._text

    def thinking_view(self) -> dict[str, Any] | None:
        """考えの形 {text, cut, cut_note}。cut_note は切ったときだけ THINKING_CUT_NOTE(ほかは None)。考えが無ければ None。"""
        with self._lock:
            return _thinking_dict(self._thinking.text, self._thinking.cut)

    def _paint(self) -> tuple[str, dict[str, Any] | None] | None:
        """小さなロックの中で呼ぶ。知らせる時なら (答えの文字, 考えの形) を返し、知らせた印を進める。ほかは None。"""
        now = _clock()
        if self._changes > self._painted and (
            self._painted_at is None or now - self._painted_at >= STREAM_PAINT_SECONDS
        ):
            self._painted = self._changes
            self._painted_at = now
            return self._text, _thinking_dict(self._thinking.text, self._thinking.cut)
        return None

    def stop_requested(self) -> bool:
        worker, asking = self.worker, self.asking
        return (
            (worker is not None and worker.cancel_requested)
            or (asking is not None and asking.cancelled)
            or self._api._closing
        )

    def __call__(self, delta: str) -> None:
        if self.stop_requested():
            raise StreamStopped()
        with self._lock:
            if delta:
                self._text += delta
                self._changes += 1
            paint = self._paint()
        if paint is not None:
            self._api._partial_changed(self, *paint)

    def thinking(self, delta: str) -> None:
        """要求の on_thinking(ADR-0169 決定4)。止める条件なら StreamStopped。足したら答えと同じ間隔で知らせる。"""
        if self.stop_requested():
            raise StreamStopped()
        with self._lock:
            before = (len(self._thinking), self._thinking.cut)
            self._thinking.add(delta)
            if (len(self._thinking), self._thinking.cut) != before:
                self._changes += 1
            paint = self._paint()
        if paint is not None:
            self._api._partial_changed(self, *paint)


def _thinking_dict(text: str, cut: bool) -> dict[str, Any] | None:
    """考えの形 {text, cut, cut_note}(判断の記録 1: 断り書きの文は THINKING_CUT_NOTE だけ。切っていなければ None)。"""
    if not text:
        return None
    return {"text": text, "cut": cut, "cut_note": THINKING_CUT_NOTE if cut else None}


def _done_thought(thought: dict[str, Any] | None, llm: LlmAnalysis | None) -> dict[str, Any] | None:
    """全部の答えが結果に入ったときだけ考えを残す(AI 解析が done のとき。失敗・中止・送らなかったときは None)。"""
    if thought is None or llm is None or llm.status != "done":
        return None
    return thought


class _ApiErrorSink:
    """要求の on_api_error(ADR-0168 決定5)。掃除した後のエラーの文と秒を、今の送信の受け取り口のときだけ api に持たせる。

    target は "analysis"(解析)か "follow_up"(追加の質問)。文はログ・例外に入れない。
    """

    def __init__(self, api: "CompareApi", target: str) -> None:
        self._api = api
        self.target = target

    def __call__(self, error: ApiErrorText) -> None:
        api = self._api
        with api._lock:
            if api._api_error_sink is not self or api._closing:
                return  # 消した後・次の送信を始めた後・閉じている途中に届いたものは捨てる
            api._set_api_error(error, self.target)


class CompareApi(Api):
    """比較画面から呼べるメソッド(window.pywebview.api.<名前>)。REQ-103 の app_info・messages も使える。

    session は窓をまたいで共有する門と設定の窓(WebSession)。
    cwd・env・platform・home は LLM の設定の読み元(テストで一時フォルダを渡す。省くと利用者の設定)。
    client_factory は LLM の client を作る関数(テストで偽物にする)。post は CompareWorker の戻し方、runner は比較の関数。
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
        runner: Callable[..., CompareOutcome] = run_compare,
    ) -> None:
        self._session = session
        self._where: dict[str, Any] = {"cwd": cwd, "env": env, "platform": platform, "home": home}
        self._client_factory = client_factory
        self._post = post
        self._runner = runner
        self._window: Any = None
        # 状態はここだけに持つ。js_api の呼び出しとワーカーのコールバックは別々のスレッドで動くので、触るのはロックの中だけ。
        # ロックの順は、このロック → session.lock だけ(session からこの窓を呼ぶのはロックの外)
        self._lock = threading.RLock()
        self._sources: dict[Side, SideSource | None] = {"before": None, "after": None}
        self._side_views: dict[Side, SideView | None] = {"before": None, "after": None}
        self._summaries: dict[Side, str] = {side: input_summary(None) for side in SIDES}
        self._form: dict[str, Any] = dict(DEFAULT_FORM)
        # 単純比較の設定(ADR-0166 決定10。REQ-227 §11)。窓を開いたときは設定の窓の既定(raw-compare.json)。窓を閉じるまで保つ。
        # _raw は読んだ設定(誤りがあれば options が None)、_raw_values は欄の値(打っている途中の文字を消さないよう受け取ったまま)、
        # _raw_band は既定のファイルを読めないときの帯の文、_raw_fill は変わったら画面が欄に値を入れ直す番号
        # _raw_context は「差分のみ表示」の前後の行数(REQ-228 §2。表示だけの値。変えても比べ直さない)
        raw_options, self._raw_context, self._raw_band = raw_form_defaults(env=env, platform=platform, home=home)
        self._raw = RawForm(raw_options, [], self._raw_context)
        self._raw_values: dict[str, Any] = raw_form_values(raw_options, self._raw_context)
        self._raw_fill = 0
        self._kind_shown = self._form["kind"]  # 機種の欄を合わせた種類(show から変わったときだけ系統の選択を戻す)
        self._last: tuple[ScreenInput, CompareRequest, CompareOutcome] | None = None
        self._outcome: OutcomeView | None = None  # 表示中の結果
        self._llm_text: str | None = None  # LLM 解析の欄の文(作業フォルダの無視の注記を前に足したもの。頭の行は除く)
        self._llm_copy: str | None = None  # 最初の答えのコピーの文(<red> を外した答え。答えが無ければ None)
        self._result_status = ""  # 表示中の結果の状態の文(入力を元に戻したときにステータスへ戻す)
        self._stale = False  # ステータスに「前の入力の結果」と出している
        self._can_save = False
        self._worker: CompareWorker | None = None
        self._run_kind = RUN_COMPARE  # 走っている(最後に始めた)比較の理由
        self._llm_notes: list[str] = []
        self._closing = False
        self._status = ["", ""]  # ステータスの左(状態の文)と右(件数の文)
        self._banner: list[str] = []
        self._segment_title = COMPARE_SEGMENTS
        # 待っている送信前のプレビュー: (番号, 計画, 比較した入力, 要求, 1回目の LLM の結果, 送り先のプロファイル, 全体の上限)
        # 全体の上限はまとめての比較の承認した2回目に使う(1組の比較では使わない)
        self._preview: tuple[int, LlmPlan, ScreenInput, CompareRequest, Any, tuple[str, str], int] | None = None
        self._late_notice: dict[str, Any] | None = None  # ワーカーから戻った知らせ(画面は番号で1回だけ出す)
        # 追加の質問(ADR-0081): 答えが届いたやり取り (質問, 回答) の並び・送っている間のワーカー・やり取りの番号(消すと変わる)
        self._turns: list[tuple[str, str]] = []
        self._asking: _Asking | None = None
        self._follow_up_epoch = 0
        self._follow_up_revision = 0
        # 送り先(ADR-0148 決定7): 答えが届いた送信の AI の接続のプロファイル (id, 名前)。組の番号(1組の比較は 0)ごと。
        # 追加の質問の前に今の使用中と比べ、違えば確かめる。待っている確かめは (番号, 今の id, 組の番号)。質問の文は持たない
        self._sent_profiles: dict[int, tuple[str, str]] = {}
        self._destination_confirm: tuple[int, str, int] | None = None
        # 届いたところまでの答え(ADR-0157 決定5〜7): 今読んでいる流れ・画面に出す途中の答え・知らせの番号(増え続ける)
        self._stream: _AnswerStream | None = None
        self._partial: dict[str, Any] | None = None
        self._partial_revision = 0
        # 全部の答えが届いた後に残す考え(ADR-0169 決定4): {"text", "cut", "cut_note", "target", "revision"}。保存しない。
        # 番号は移すたびに進む(画面は同じ番号なら開き具合を変えない)
        self._thinking: dict[str, Any] | None = None
        self._thinking_revision = 0
        # API が返したエラーの文(ADR-0168 決定5): {"text", "retry_after", "target"}(掃除した後の文。保存しない)・
        # 今の送信の受け取り口(これ以外から届いたものは捨てる)・折りたたみを閉じ直す番号(入れるたびに進む)
        self._api_error: dict[str, Any] | None = None
        self._api_error_sink: _ApiErrorSink | None = None
        self._api_error_revision = 0
        # 解析の前に打った文の欄を空にする番号(承認して送った答えが届いたときだけ増やす。REQ-206 §6)
        self._instruction_clear = 0
        # 「差分＋全文を送る」(ADR-0158): チェックの状態(窓の間だけ。新しく比較を始める・入力を変えると偽)・
        # 最初の解析で全文を送ったか(組の番号ごと)・追加の質問で作った全文(組の番号ごと)・送信前の確認で確かめた組の番号・
        # 待っている追加の質問の確認 (番号, 送る文, 質問, client, 結果, 組の番号, やり取りの番号, 秘密の語)。
        # 質問の文は確認を待っている間だけ持ち、答えたら・やり取りを消したら・窓を閉じたら消す(ログ・例外・知らせに入れない)
        self._full_text = False
        self._first_full_text: dict[int, bool] = {}
        self._after_sections: dict[int, AfterText] = {}
        self._full_text_confirmed: set[int] = set()
        self._follow_up_preview: tuple[int, str, str, LLMClient, object, int, int, tuple[str, ...]] | None = None
        self._focus: str | None = None
        # 待っている一括比較の答え(ADR-0170): (持ってきたときの入力, 答え)。比べ終わったら必ず消す(使うのは入力が同じときだけ)。
        # 入力や比較の設定を変えた・結果を消した・窓を閉じたときも消す(決定4)
        self._handed: tuple[ScreenInput, HandedAnswer] | None = None
        # 開いた保存の結果(REQ-120)の帯の文。表示していなければ None
        self._opened: str | None = None
        # 開いた保存の中身(REQ-242 §4。読み込んだ JSON と組み立てた行)。保存し直しと、保存の答えへの追加の質問に使う。
        # _opened と同じときに入れ、結果を消すと消える
        self._opened_saved: _OpenedSaved | None = None
        # 保存から戻した送り先の記録(名前だけ)を、名前で比べるか(id が分からないため。ADR-0179 決定3)
        self._sent_name_only = False
        # 保存に送ったときの伏せる語が無いときの確認(ADR-0179 決定3): 待っている確認の番号と、開いた結果で確かめたか(1回だけ)
        self._words_confirm: int | None = None
        self._saved_words_confirmed = False
        # 最初の依頼の文(ADR-0179 決定1)。答えが届いたとき(承認した送信・自動送信・まとめての比較の全体の送信)に決め、
        # 追加の質問のやり取りと同じときに消す(_clear_follow_up)。保存した結果を開いたときは保存から戻す。
        # _preview_first_request は送信前の確認と組で持つ文(承認した2回目に運ぶ)
        self._first_request: str | None = None
        self._preview_first_request: str | None = None
        # まとめての比較(REQ-122): 最後の結果(入力・要求・まとめた結果。比べたときだけ)・表示中の一覧(比べた結果か開いた .jsonl)・
        # 選んでいる行・組ごとの表示・相手なしの行を選んだときの説明
        self._multi: tuple[ScreenInput, CompareRequest, MultiCompareOutcome] | None = None
        self._mview: MultiView | None = None
        self._pair = 0
        self._pair_views: dict[int, OutcomeView] = {}
        self._unpaired_note: str | None = None
        # 「全部」のタブ(ADR-0152 決定7)の行の番号と、作った行(番号ごとに1回だけ作る)。行に合うタブ(決定8)とその番号
        self._all_revision = 0
        self._all_rows: tuple[int, list[AllRow]] | None = None
        # 違う文字の印(ADR-0165 / REQ-226)。求めた元(表示中の結果の view か「全部」のタブの行の並び)と印の組。
        # 元が同じもの(is)の間は使い回し、変わったときだけ求め直す
        self._view_marks: tuple[OutcomeView, list[dict[str, Any]], list[dict[str, Any]]] | None = None
        self._all_marks: tuple[list[AllRow], list[dict[str, Any]]] | None = None
        self._tab = TAB_ALL
        self._tab_revision = 0
        # 番号(画面は変わったときだけ描き直す・入れ直す)
        self._revision = 0
        self._result_revision = 0
        self._fill = 0  # 変わったら、画面は欄に form を入れ直す
        self._focus_revision = 0
        self._multi_revision = 0  # 変わったら、画面は一覧の行を作り直す
        self._serial = 0  # プレビュー・確認・知らせの番号
        # AI の窓(ADR-0160)。開いている間だけ窓を持つ。どの窓からの呼び出しかはスレッドごとの値で持つ(_call_as)
        self._ai_window: Any = None
        self._pane = threading.local()
        # キャラクターの絵(ADR-0174 決定1)。窓ごとに、最初に描いたときの [gui] character を覚える(開いている窓は変えない)。
        # 鍵は窓("compare"・"ai")。AI の窓を開き直したら、その窓の分だけ読み直す
        self._characters: dict[str, bool] = {}
        # この窓で一度でも比べたか(始めた・保存した結果を開いた・一括比較から受け取った)。空の窓の絵に使う(ADR-0174 決定2)
        self._compared_once = False
        session.watch(self, self._changed)

    def _attach(self, window: Any) -> None:
        """pywebview の窓(知らせ・閉じる・ファイルのダイアログに使う)。WebSession.attach_compare が渡す。"""
        self._window = window

    # --- 画面から呼ぶメソッド(form は画面の欄の値の dict) ---

    def state(self) -> dict[str, Any]:
        """今の状態。読むだけ。"""
        return self._call(self._state)

    def form_changed(self, form: Any) -> dict[str, Any]:
        """欄が変わったとき。種類の扱いと保存の止め方を見直す。比較の設定が変わっていれば自動の比較を始める(ADR-0078 決定3)。"""
        return self._call(self._form_changed, form)

    def swap(self, form: Any) -> dict[str, Any]:
        """「入れ替え」。変更前と変更後の入力と区間の選択を入れ替える(ADR-0078 決定2)。"""
        return self._call(self._swap, form)

    def browse(self, form: Any, side: Any) -> dict[str, Any]:
        """「参照…」。窓のファイルのダイアログで選んだファイルを読む。"""
        return self._call(self._browse, form, side)

    def paste(self, form: Any, side: Any, text: Any) -> dict[str, Any]:
        """貼り付けのダイアログの「OK」。text は入力にするだけ(残さない)。"""
        return self._call(self._paste, form, side, text)

    def run(self, form: Any) -> dict[str, Any]:
        """「比較実行」。"""
        return self._call(self._run, form)

    def cancel(self) -> dict[str, Any]:
        """「中止」。工程の境目で止まる。"""
        return self._call(self._cancel)

    def send_llm(self, form: Any, instruction: Any = "") -> dict[str, Any]:
        """「AI で解析」。最後の結果の入力で比べ直し、送信前のプレビューで止まる(確認なしに送らない。ADR-0080 決定1)。

        instruction は AI の面の欄に打った文(REQ-206 §6)。空白だけなら足さない。確かめて断れば送らない(返す値・知らせ・属性に残さない)。
        """
        return self._call(self._send_llm, form, instruction)

    def answer_preview(self, form: Any, yes: Any) -> dict[str, Any]:
        """送信前のプレビューの答え。「送信する」(yes が真)のときだけ、待っているプレビューの内容を送る。"""
        return self._call(self._answer_preview, form, yes)

    def select_pair(self, form: Any, index: Any) -> dict[str, Any]:
        """まとめての比較の一覧で行を選んだとき(REQ-122 §2)。index は一覧の行の番号(0始まり)。"""
        return self._call(self._select_pair, form, index)

    def ask_follow_up(self, form: Any, question: Any, confirm: Any = None) -> dict[str, Any]:
        """「質問する」。AI の答えに追加で質問する(ADR-0081)。question は確かめて送るだけ(返す値・知らせ・属性に残さない)。

        送り先のプロファイルが最初の送信と違えば送らずに "confirm"(番号・題・本文・はい・いいえ)を返す(ADR-0148 決定7)。
        「はい」のとき、画面はその番号を confirm に入れて呼び直す。
        """
        return self._call(self._ask_follow_up, form, question, confirm)

    def set_full_text(self, form: Any, checked: Any) -> dict[str, Any]:
        """AI の面の「差分＋全文を送る」を変えたとき(ADR-0158 決定1)。checked は真偽値。押せないときは入れない(外すのはいつでも)。"""
        return self._call(self._set_full_text, form, checked)

    def use_ai_profile(self, form: Any, index: Any, label: Any) -> dict[str, Any]:
        """AI の面の「AI プロファイル」を選んだとき(ADR-0148 決定4)。index は選択肢の位置、label はその名前。使用中を変える。"""
        return self._call(self._use_profile, form, index, label, "llm")

    def use_prompt_profile(self, form: Any, index: Any, label: Any) -> dict[str, Any]:
        """AI の面の「プロンプト」を選んだとき(ADR-0148 決定4)。index は選択肢の位置、label はその名前。使用中を変える。"""
        return self._call(self._use_profile, form, index, label, "prompt")

    def reset_raw_options(self) -> dict[str, Any]:
        """単純比較の設定の「既定に戻す」。既定のファイルを読み直して欄に入れる(比べ直しはしない。ADR-0166 決定10)。"""
        return self._call(self._reset_raw_options)

    def result(self) -> dict[str, Any]:
        """表示中の結果の行・説明・警告・タブの題・LLM 解析の文。"""
        return self._call(self._result)

    def all_rows(self) -> dict[str, Any]:
        """「全部」のタブの行(ADR-0152 決定7)。まとめて比べていなければ空。画面は view の multi の all_revision が変わったときだけ呼ぶ。"""
        return self._call(self._all)

    def jump(self, current: Any, step: Any) -> dict[str, Any]:
        """「前の差分」(step=-1)・「次の差分」(step=1)。current は選んでいる単純比較の行(未選択は -1)。"""
        return self._call(self._jump, current, step)

    def save(self, form: Any) -> dict[str, Any]:
        """「結果を保存…」。"""
        return self._call(self._save, form)

    def export_result(self, form: Any, kind: Any) -> dict[str, Any]:
        """「HTML・CSV で出力…」(ADR-0156)。kind は "html"・"csv"・"csv_pairs"(組の一覧はまとめて比べたときだけ)。

        秘密の行は AI に送るときと同じ判定で必ず伏せる(伏せずに出す口は無い)。上書きしない。
        """
        return self._call(self._export_result, form, kind)

    def open_result(self, form: Any) -> dict[str, Any]:
        """「結果を開く…」。窓のファイルのダイアログで選んだ保存した結果を、読むだけで表示する(REQ-120)。"""
        return self._call(self._open_result, form)

    def close_result(self, form: Any) -> dict[str, Any]:
        """帯の「閉じる」。開いた結果の表示を消す(入力は残す。自動の比較は始めない)。"""
        return self._call(self._close_result, form)

    def open_batch(self) -> dict[str, Any]:
        """「一括比較…」。一括比較の窓を開く(開いていれば前に出す。REQ-106)。"""
        return self._call(self._open_batch)

    def open_settings(self, tab: Any = None) -> dict[str, Any]:
        """「設定…」。設定の窓を開く(開いていれば前に出す)。

        tab は開くタブ(知らせの「更新…」は "update"。REQ-252 §5)。省けば今と同じ。None・"update" のほかは受け付けない。
        """
        return self._call(self._open_settings, tab)

    def dismiss_update_notice(self) -> dict[str, Any]:
        """新しい版の知らせの「閉じる」(REQ-252 §5)。その起動の間は、比較の窓にも一括比較の窓にも出さない。"""
        return self._call(self._dismiss_update_notice)

    def open_ai_pane(self) -> dict[str, Any]:
        """AI の面の「↗」と、結果のタブの行の「↗」。AI の窓を開く(開いていれば前に出す。ADR-0160 決定1・2)。"""
        return self._call(self._open_ai_pane)

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

        result = _guarded(run)
        if getattr(func, "__func__", None) not in _READ_ONLY:
            # 呼んだ窓は返した view で描き直す。もう一方の窓(AI の窓が開いているときだけ)へは、ここで知らせて読み直させる
            # (組の切り替え・入れ替え・欄の変更など、ワーカーを通らない操作も別の窓に届ける。ADR-0160 決定5・7)
            self._notify_other(self._current_pane())
        return result

    def _notify_other(self, pane: str) -> None:
        """ロックの外で呼ぶ。pane の窓から操作した後に、AI の窓が開いていれば、もう一方の窓へ compare.changed を送る。

        AI の窓が無ければ何もしない(比較の窓だけのときの動きは今のまま)。閉じた窓への知らせの失敗は捨てる。
        """
        with self._lock:
            ai_window = self._ai_window
            other = ai_window if pane == PANE_COMPARE else self._window
        if ai_window is None or other is None:
            return
        try:
            notify(other, {"type": CHANGED_EVENT})
        except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
            pass

    def _call_as(self, pane: str, func: Callable[..., dict[str, Any]], *args: Any) -> dict[str, Any]:
        """pane の窓からの呼び出し(AI の窓の口が "ai" で呼ぶ。ADR-0160 決定3・6)。その間だけ、返す view を pane 向けに描く。"""
        previous = getattr(self._pane, "name", PANE_COMPARE)
        self._pane.name = pane
        try:
            return self._call(func, *args)
        finally:
            self._pane.name = previous

    def _current_pane(self) -> str:
        """今のスレッドの呼び出しの窓(比較の窓・ワーカーのスレッドは "compare")。"""
        return getattr(self._pane, "name", PANE_COMPARE)

    def _ok(self, **extra: Any) -> dict[str, Any]:
        pane = self._current_pane()
        if "confirm" in extra and pane == PANE_COMPARE and self._ai_window is not None:
            # 送り先の確認は AI の窓にだけ出す(ADR-0160 決定6)。比較の窓には返さない(送らない)
            extra = {key: value for key, value in extra.items() if key != "confirm"}
        return {"ok": True, "view": self._render(pane), **extra}

    def _reply(self, notice: dict[str, Any] | None) -> dict[str, Any]:
        """知らせがあれば "notice" を足した、今の状態。"""
        return self._ok(notice=notice) if notice is not None else self._ok()

    def _notice(self, body: str, title: str) -> dict[str, Any]:
        return {"title": title, "body": body, "ok": DIALOG_OK}

    def _ai_late_notice(self, body: str, title: str) -> dict[str, Any]:
        """ロックの中で呼ぶ。ワーカーから戻った AI の知らせ(印を付ける。AI の窓が開いていれば、そこにだけ出す。ADR-0160 決定6)。"""
        return {"id": self._next_serial(), **self._notice(body, title), NOTICE_PANE: PANE_AI}

    def _next_serial(self) -> int:
        self._serial += 1
        return self._serial

    def _take(self, form: Any) -> None:
        """画面の欄の値を受け取る。形が違えば _BadRequest。種類を show から自動か config に変えたときだけ、機種の選択を戻す。

        自動と config の間では機種の選択を保つ(どちらも機種を選ぶ欄を出すため。ADR-0151 決定4)。
        """
        if not isinstance(form, dict):
            raise _BadRequest
        previous = self._settings_key()  # 比較の設定が変わったら、待っている一括比較の答えを捨てる(下の _drop_handed_if_changed)
        mode, kind = form.get("mode"), form.get("kind")
        if not isinstance(mode, str) or mode not in MODE_VALUES or not isinstance(kind, str) or kind not in KIND_VALUES:
            raise _BadRequest
        if not isinstance(form.get("platform"), str) or not isinstance(form.get("command"), str):
            raise _BadRequest
        # form の llm は使わない(古い画面から来ても読まない。「AI に送る」のボタン。ADR-0080 決定1)
        if not _is_index(form.get("config_platform"), len(WEB_PLATFORM_CHOICES)):
            raise _BadRequest
        for side in SIDES:
            if not _is_index(form.get(f"{side}_segment"), len(segment_items(self._side_views[side]))):
                raise _BadRequest
        values = {name: form[name] for name in DEFAULT_FORM}
        if "raw_options" in form:
            # 単純比較の設定の欄(REQ-227 §11.3)。無いときは今の値のまま(古い画面の form もそのまま受ける)
            try:
                self._raw = read_raw_form(form["raw_options"])
            except ValueError:
                raise _BadRequest from None
            self._raw_values = dict(form["raw_options"])
            # 前後の行数(REQ-228 §2)。正しい値に変わったら結果の版を上げて表を描き直させる(比べ直さない。_settings_key に入れない)
            context = read_context_lines(form["raw_options"]["context_lines"])
            if context is not None and context != self._raw_context:
                self._raw_context = context
                self._result_revision += 1
        if kind != "show" and self._kind_shown == "show":
            # show の手入力の値を系統として持ち越さない(既定の選択から選び直す)
            values["config_platform"] = DEFAULT_FORM["config_platform"]
            self._fill += 1
        self._kind_shown = kind
        self._form = values
        self._drop_handed_if_changed(previous)
        if self._opened_saved is None and not self._input_unchanged():
            # 入力を変えたらやり取りを消す(元に戻しても消えたまま)。開いた保存の結果のやり取りは入力によらない
            # (新しい入力を入れると開いた結果の表示ごと消える。REQ-242 §4)
            self._clear_follow_up()

    def _drop_handed_if_changed(self, previous: tuple[Any, ...]) -> None:
        """ロックの中で呼ぶ。比較の設定(_settings_key)が previous から変わっていれば、待っている一括比較の答えを捨てる。

        比較方式・種類・機種・show のコマンド・区間・単純比較の設定のどれを、どの操作で変えても同じ(欄の値を受け取るどの呼び出しも、
        「既定に戻す」も)。比較中に変えたときも捨てるので、比べ終わっても答えは付かず、知らせも出ない(ADR-0170 決定4)。
        前後の行数は表示だけの値なので _settings_key に入っておらず、変えても捨てない。
        """
        if self._settings_key() != previous:
            self._handed = None

    def _side(self, side: Any) -> Side:
        if not isinstance(side, str) or side not in SIDES:
            raise _BadRequest
        return side  # type: ignore[return-value]

    def _changed(self) -> None:
        """画面に「状態が変わった」とだけ知らせる(ロックの外で呼ぶ)。窓が閉じた後の失敗は握りつぶす。

        比較の窓と AI の窓(開いていれば)の両方へ送る。片方の失敗で他方を止めない(ADR-0160 決定7)。
        """
        for window in (self._window, self._ai_window):
            if window is None:
                continue
            try:
                notify(window, {"type": CHANGED_EVENT})
            except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
                pass

    def _choose_file(self, kind: int, save_filename: str, file_types: tuple[str, ...]) -> Path | None:
        """窓のファイルのダイアログで選んだ場所。取り消した・窓が無いときは None。ロックの外で呼ぶ。

        選んだときは、Mac の保存のダイアログが文字列を、ほかは文字列の並びを返す(pywebview 6.2.1)。
        pywebview のダイアログには題を渡す口が無い(wx の「変更前のファイルを選ぶ」「結果を保存 — …」は出せない)。
        AI の窓からの呼び出しは、AI の窓のダイアログで開く(ADR-0160)。
        """
        window = self._window
        if self._current_pane() == PANE_AI and self._ai_window is not None:
            window = self._ai_window
        if window is None:
            return None
        chosen = window.create_file_dialog(
            kind, directory="", allow_multiple=False, save_filename=save_filename, file_types=file_types
        )
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return Path(chosen) if isinstance(chosen, str) and chosen else None

    # --- 状態(wx の ComparisonFrame と同じ判断) ---

    def _busy(self) -> bool:
        """比較中か、追加の質問を送っている間(どちらも門を持っている)。"""
        return self._worker is not None or self._asking is not None

    def _busy_place(self) -> str | None:
        """ロックの中で呼ぶ。動いている処理の表示の場所(ADR-0164 決定5)。結果の面 "result"・AI の面 "ai"・無し None。

        比較だけのときは結果の面に重ね、AI に送る間(送信前の確認を作る・承認して送る・自動送信・追加の質問)は AI の面に出す。
        """
        if self._asking is not None:
            return "ai"
        if self._worker is None:
            return None
        if self._run_kind in (RUN_COMPARE, RUN_HANDOFF):
            return "result"
        return "ai"

    def _last_input(self) -> ScreenInput | None:
        """最後に比較したときの入力(1組の比較か、まとめての比較)。無ければ None。"""
        if self._last is not None:
            return self._last[0]
        return self._multi[0] if self._multi is not None else None

    def _result_key(self) -> object | None:
        """最後の結果(届いた答えを残すか決めるときに、同じ結果かを比べる)。開いた保存の結果ではその中身(REQ-242 §4)。"""
        if self._opened_saved is not None:
            return self._opened_saved
        return self._last if self._last is not None else self._multi

    def _input_unchanged(self) -> bool:
        """最後の結果があり、いまの入力が比較したときと同じか。"""
        last_input = self._last_input()
        return last_input is not None and is_current(last_input, self._current_input())

    def _first_answer(self) -> LlmAnalysis | None:
        """追加の質問ができる最後の結果の AI 解析(送って答えが届いたもの)。無ければ None。

        まとめて比べたときは、全部の組をまとめて送った全体の AI 解析(ADR-0152 決定3。選んでいる行によらない)。
        開いた保存の結果では、保存の答え(.jsonl は全体の答えを入れた行)が送って届いたものならそれ(REQ-242 §4)。
        """
        llm = None
        if self._opened_saved is not None:
            llm = self._opened_saved.llm
        elif self._last is not None:
            llm = self._last[2].llm
        elif self._multi is not None:
            llm = self._multi[2].llm
        return llm if _answered(llm) else None

    def _follow_up_turns(self, first: LlmAnalysis) -> list[tuple[str, str]]:
        """送る文に入れるやり取り。最初の答えから始まり、答えと質問が交互に並び、答えで終わる。"""
        assert first.answer is not None
        turns = [(ANSWER, first.answer)]
        for question, answer in self._turns:
            turns += [(QUESTION, question), (ANSWER, answer)]
        return turns

    def _can_ask(self) -> bool:
        """「質問する」を押せるか(REQ-118 §3)。最後の結果の AI 解析が送って答えが届いたもので、入力が比較したときと同じで、
        比較中・質問中でなく、閉じている途中でなく、一括比較が門を持っておらず、追加の質問が上限の回数に達していない。
        開いた保存の結果では入力を問わない(保存の答えと保存したやり取りから送る。上限には保存したやり取りも数える。REQ-242 §4)。
        """
        if self._busy() or self._closing or not self._session.free_for(self):
            return False
        if self._first_answer() is None:
            return False
        if self._opened_saved is None and not self._input_unchanged():
            return False
        return len(self._turns) < MAX_TURNS

    def _clear_follow_up(self) -> None:
        """ロックの中で呼ぶ。追加の質問のやり取りを消す。送っている途中なら、届いた答えは捨てる(番号を変える)。"""
        if self._turns or self._asking is not None:
            self._turns = []
            self._follow_up_epoch += 1
            self._follow_up_revision += 1
        self._first_request = None  # 最初の依頼の文もやり取りと同じときに消す(ADR-0179 決定1)
        # 全文の節・確かめた印・待っている確認(質問の文を持つ)も消す(ADR-0158 決定6)
        self._after_sections = {}
        self._full_text_confirmed = set()
        self._follow_up_preview = None
        self._clear_partial()
        self._clear_thinking()
        self._clear_api_error()

    def _clear_thinking(self) -> None:
        """ロックの中で呼ぶ。全部届いた後に残した考えを消す(ADR-0169 決定4・6)。

        消したときは途中の答えの番号も進める(画面がこの view を古いとみなさず、折りたたみを外すため)。
        """
        if self._thinking is not None:
            self._thinking = None
            self._partial_revision += 1

    def _keep_thinking(self, thought: dict[str, Any] | None, target: str) -> None:
        """ロックの中で呼ぶ。全部の答えが結果ややり取りに入ったとき、流れの考えを残す(無ければ消す)。番号を進める。"""
        if thought is None:
            self._thinking = None
            return
        self._thinking_revision += 1
        self._thinking = {**thought, "target": target, "revision": self._thinking_revision}

    def _clear_api_error(self) -> None:
        """ロックの中で呼ぶ。API が返したエラーの文を消し、受け取り口を外す(後から届いたものは捨てる)。"""
        self._api_error = None
        self._api_error_sink = None

    def _set_api_error(self, error: ApiErrorText | None, target: str) -> None:
        """ロックの中で呼ぶ。掃除した後の文と秒を持つ(文も秒も無ければ消す)。入れるたびに番号を進める。"""
        if error is None or (error.message is None and error.retry_after is None):
            self._api_error = None
            return
        self._api_error_revision += 1
        self._api_error = {"text": error.message, "retry_after": error.retry_after, "target": target}

    def _api_error_view(self) -> dict[str, Any] | None:
        """view の api_error(AI の面の折りたたみ)。文が無ければ null。"""
        error = self._api_error
        if error is None or not error["text"]:
            return None
        return {
            "title": COMPARE_API_ERROR_TITLE,
            "text": error["text"],
            "note": COMPARE_API_ERROR_NOTE,
            "revision": self._api_error_revision,
        }

    def _rate_limit_lines(self, llm: LlmAnalysis | None) -> list[str] | None:
        """ロックの中で呼ぶ。解析が 429 で、API が待つ秒を返していれば、その秒を出した AI の面の行。ほかは None(今の行のまま)。"""
        error = self._api_error
        if llm is None or llm.status == "done" or error is None:
            return None
        if error["target"] != TARGET_ANALYSIS or error["retry_after"] is None:
            return None
        return llm_screen_lines(llm, retry_after=error["retry_after"])

    def _clear_partial(self) -> None:
        """ロックの中で呼ぶ。途中の答えを消し、読んでいる流れの知らせを捨てる(番号を進める。古い知らせは画面が捨てる)。"""
        if self._partial is not None or self._stream is not None:
            self._partial = None
            self._stream = None
            self._partial_revision += 1

    def _keep_stopped_partial(self, stream: _AnswerStream) -> None:
        """ロックの中で呼ぶ。中止したとき、ためた文字か考えがあれば「中止しました」の注記を付けて残す(保存しない)。無ければ消す。"""
        if self._stream is not stream:
            return  # もう消した(次の送信・入力を変えた)
        self._stream = None
        self._partial_revision += 1
        text = stream.text
        thought = stream.thinking_view()
        if not text and thought is None:
            self._partial = None
            return
        self._partial = {
            "revision": self._partial_revision,
            "blocks": answer_blocks(text) if text else [],
            "target": stream.target,
            "stopped": True,
            "note": COMPARE_PARTIAL_STOPPED,
            "thinking": thought,
        }

    def _partial_changed(self, stream: _AnswerStream, text: str, thinking: dict[str, Any] | None = None) -> None:
        """ワーカーのスレッドから。届いたところまでの答えと考えを持ち、ロックの外で画面に知らせる。知らせの失敗は握りつぶす。

        thinking は考えの形 {text, cut, cut_note} か None(ADR-0169 決定4)。答えの文字がまだ無いときの blocks は []。
        """
        with self._lock:
            if self._stream is not stream or self._closing:
                return  # 消した後・閉じている途中に届いた文字は出さない
            self._partial_revision += 1
            blocks = answer_blocks(text) if text else []
            self._partial = {
                "revision": self._partial_revision,
                "blocks": blocks,
                "target": stream.target,
                "stopped": False,
                "note": None,
                "thinking": thinking,
            }
            event = {
                "type": PARTIAL_EVENT,
                "revision": self._partial_revision,
                "blocks": blocks,
                "target": stream.target,
                "thinking": thinking,
            }
            # AI の窓が開いていればそこだけへ、無ければ比較の窓へ(ADR-0160 決定7)
            window = self._ai_window if self._ai_window is not None else self._window
        if window is None:
            return
        try:
            notify(window, event)
        except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
            pass

    def _can_run(self) -> bool:
        """「比較実行」を押せるか。入力がそろい、実行中でなく、閉じている途中でなく、一括比較が走っていない(ADR-0029 決定9)。

        開いた保存の結果を表示している間も押せない(REQ-120。自動の比較も始めない)。
        """
        if self._opened is not None:
            return False
        return all(self._sources.values()) and not self._busy() and not self._closing and self._session.free_for(self)

    def _can_send_llm(self) -> bool:
        """「AI に送る」を押せるか(自動送信も同じ条件。ADR-0080 決定1・2)。

        最後の結果があり、いまの入力が比較したときと同じで、その結果に送る差分があり(構造化比較が動いて 0 件なら送らない)、
        実行中でなく、閉じている途中でなく、一括比較が走っていない。まとめて比べたときは、どれか1組に送る差分があること(REQ-122 §4)。
        """
        last_input = self._last_input()
        if last_input is None or self._busy() or self._closing or not self._session.free_for(self):
            return False
        if not is_current(last_input, self._current_input()):
            return False
        if self._last is not None:
            outcomes = [self._last[2]]
        else:
            assert self._multi is not None
            outcomes = [pair.outcome for pair in self._multi[2].pairs]
        return any(source_of(o.structured, o.raw, o.show) is not None for o in outcomes)

    def _current_input(self) -> ScreenInput | None:
        """いまの画面の入力。両側の入力がそろっていなければ None。

        llm はいつも偽(AI の有無で「入力が変わった」としない。AI を送った後も保存できる。ADR-0080 決定1)。
        """
        before, after = self._sources["before"], self._sources["after"]
        if before is None or after is None:
            return None
        if self._raw.options is None:
            # 単純比較の設定に誤りがある間は、比べられる入力が無い(保存・AI に送る・追加の質問を止める)
            return None
        form = self._form
        kind, platform = web_kind_platform(form["kind"], form["platform"], form["config_platform"])
        return ScreenInput(
            before=before,
            after=after,
            mode=form["mode"],
            kind=kind,
            platform=platform,
            # show のコマンドは種類が show のときだけ(自動・config では欄を使えない)
            show_command=form["command"] if kind == "show" else "",
            before_segment=segment_number(self._side_views["before"], form["before_segment"]),
            after_segment=segment_number(self._side_views["after"], form["after_segment"]),
            raw_options=self._raw.options,
        )

    def _summary_outcome(self) -> CompareOutcome | None:
        """ロックの中で呼ぶ。設定の要約に使う結果。表示中の結果が今の種類と機種で比べたものなら、1組の比較はその結果、
        まとめての比較は選んだ行の組の結果(相手なしの行は None)。開いた保存の結果や、種類・機種を変えた後は None。
        """
        if self._opened is not None:
            return None
        last = self._last_input()
        current = web_kind_platform(self._form["kind"], self._form["platform"], self._form["config_platform"])
        if last is None or (last.kind, last.platform) != current:
            return None
        if self._last is not None:
            return self._last[2]
        if self._mview is not None and 0 <= self._pair < len(self._mview.entries):
            return self._mview.entries[self._pair].outcome
        return None

    def _can_resave(self) -> bool:
        """ロックの中で呼ぶ。開いた保存の結果を保存し直せるか(REQ-242 §4)。開いた結果に送って届いた AI の答えがあり、
        比較中・質問中でなく、閉じている途中でないとき。
        """
        return self._opened_saved is not None and self._first_answer() is not None and not self._busy() and not self._closing

    def _can_export(self) -> bool:
        """「HTML・CSV で出力…」を押せるか(ADR-0156 決定7)。比較中でなく、保存できる(結果があり入力が比較したときと同じ)で、
        保存した結果を開いて読んでいないとき。
        """
        return not self._busy() and self._can_save and self._opened is None

    def _refresh_save(self) -> None:
        """保存は、表示中の結果がいまの入力の結果のときだけ。入力や設定を変えたら、比較し直すまで止める。"""
        if self._busy():
            return  # 実行中は押せない
        state = save_state(self._last_input(), self._current_input(), self._result_status, self._stale)
        self._can_save = state.enabled
        self._stale = state.stale
        if state.status is not None:
            self._status[0] = state.status

    def _set_source(self, side: Side, source: SideSource) -> None:
        self._sources[side] = source
        self._summaries[side] = input_summary(source)
        self._clear_follow_up()
        self._handed = None  # 入力を変えたら一括比較の答えは捨てる(知らせは出さない。ADR-0170 決定4)
        self._full_text = False  # 入力を変えたら「差分＋全文を送る」は外す(ADR-0158 決定1)
        # 入力を変えたら区間は「自動」に戻す(区間数は次の比較の結果で分かる)
        self._side_views[side] = None
        self._form[f"{side}_segment"] = 0
        self._fill += 1
        self._refresh_save()

    def _name(self, side: Side) -> str:
        source = self._sources[side]
        return source.name if source is not None else ""

    def _render(self, pane: str = PANE_COMPARE) -> dict[str, Any]:
        """画面に描く状態。結果の行と入力の本文は入れない。

        pane は描く窓(比較の窓 "compare" か AI の窓 "ai"。ADR-0160 決定6)。AI の窓が開いている間は、送信前の確認を
        AI の窓の view にだけ入れる(比較の窓の view では null)。AI の窓が無ければ今のまま比較の窓に入れる。
        """
        self._revision += 1
        busy = self._busy()
        busy_place = self._busy_place()
        is_show = self._form["kind"] == "show"
        worker = self._worker
        detached = self._ai_window is not None
        # 確認を出す窓(1つだけ)。AI の窓が無いときは比較の窓
        confirms_here = pane == (PANE_AI if detached else PANE_COMPARE)
        preview = None
        if confirms_here and self._follow_up_preview is not None:
            # 追加の質問で、まだ確かめていない全文を送る前の確認(ADR-0158 決定6)。要約と注記は最初の送信のもの、全文は送る文
            number, prompt, *_rest = self._follow_up_preview
            first = self._first_answer()
            if first is not None:
                preview = self._preview_dict(number, preview_view(first), prompt, llm_full_text_preview(len(prompt)))
        elif confirms_here and self._preview is not None:
            # まとめて比べたときも、全部の組をまとめた送る文の全文を見せる1件の確認(ADR-0152 決定3)
            number, plan, _inp, _request, llm, _destination, _total = self._preview
            shown = preview_view(llm)
            # 全文も送るときは、先頭に目立つ帯を出す(ADR-0158 決定6)。文字数は送る文の全体
            band = llm_full_text_preview(len(shown.payload)) if plan.first.include_after_text else None
            preview = self._preview_dict(number, shown, shown.payload, band)
        return {
            "revision": self._revision,
            # 半角の括弧を含む文と、画面の論理(model.py)の文。決まった文は data-text で messages から取る
            "labels": {
                "modes": [{"value": value, "label": label} for value, label in MODE_CHOICES],
                "kinds": [{"value": value, "label": label} for value, label in KIND_CHOICES],
                "config_platforms": [label for _value, label in WEB_PLATFORM_CHOICES],
                "save_note": compare_save_note(),
                "paste_titles": {side: compare_paste_title(SIDE_LABEL[side]) for side in SIDES},
                # 「HTML・CSV で出力…」の形の選択肢(ADR-0156 決定1。組の一覧はまとめて比べたときだけ)
                "export_choices": export_choices(self._multi is not None),
            },
            # 片側の名前(ファイルのパスか「貼り付け(変更前)」)と要約。本文は渡さない
            "inputs": {side: {"name": self._name(side), "summary": self._summaries[side]} for side in SIDES},
            "segments": {
                "title": self._segment_title,
                **{side: segment_items(self._side_views[side]) for side in SIDES},
            },
            # 上の3行目(REQ-206 §2): 比較の設定の要約の文と、区間の選択を出すか(どちらかの側に区間が複数あるとき)
            # 自動のときは、表示中の結果が今の種類と機種で作ったものなら、決まった種類と機種も出す(ADR-0151 決定7)
            "settings_summary": web_settings_summary(
                self._form["mode"],
                self._form["kind"],
                self._form["config_platform"],
                self._form["platform"],
                self._form["command"],
                self._summary_outcome(),
                self._raw.options,
            ),
            "segments_shown": self._segments_shown(),
            "form": dict(self._form),
            "fill": self._fill,
            "platform_is_choice": not is_show,
            # 入力の欄とボタンの押せる条件に使う(比較中か、AI に送っている間)
            "busy": busy,
            # 処理中の表示の場所(ADR-0164 決定5)。比較だけのときは結果の面のクルクル(#busy)
            "busy_result": busy_place == "result",
            # AI に送る間の AI の面のクルクルの文(ステータスの行と同じ)。途中の答えが出ている間と、ほかのときは null
            "ai_busy": {"text": self._status[0]} if busy_place == "ai" and self._partial is None else None,
            # 「比較実行」は、入力がそろい、実行中でなく、一括比較が走っていないときだけ(ADR-0029 決定9)
            "can_run": self._can_run(),
            # 開いた保存の結果に AI の答えがあれば、やり取りを入れて新しいファイルに保存し直せる(REQ-242 §4)
            "can_save": not busy and (self._can_save or self._can_resave()),
            # 「HTML・CSV で出力…」は保存と同じ条件で、保存した結果を開いて読んでいる間は押せない(ADR-0156 決定7)
            "can_export": self._can_export(),
            # 「結果を開く…」は、実行中でなく、閉じている途中でないとき。「入れ替え」は開いた結果の表示中も押せない(REQ-120)
            "can_open": not busy and not self._closing,
            "can_swap": not busy and self._opened is None,
            # 「AI に送る」は、最後の結果がいまの入力の結果で、送る差分があり、実行中でないときだけ(ADR-0080 決定1)
            "can_send_llm": self._can_send_llm(),
            "can_cancel": (worker is not None and not worker.cancel_requested)
            or (self._asking is not None and not self._asking.cancelled),
            "command_enabled": is_show and not busy,
            "can_jump": self._outcome is not None and any(row.is_diff for row in self._outcome.raw_rows),
            # 単純比較の既定のファイルを読めないときの帯(RAW_DEFAULTS_UNREADABLE)は、結果の帯の後ろにつなぐ
            "banner": "\n".join(self._banner_lines()) or None,
            # 自動送信がオンの間は常に出す帯(送り先の名前。ADR-0080 決定2)。オフなら null
            "auto_send": self._auto_send_band(),
            "focus": self._focus,
            "focus_revision": self._focus_revision,
            "status": {"main": self._status[0], "side": self._status[1]},
            "result_revision": self._result_revision,
            "preview": preview,
            "notice": self._notice_for(pane, detached),
            # 追加の質問(ADR-0081)。最後の結果の AI 解析が送って答えが届いたものでなければ null
            "follow_up": self._follow_up_view(),
            # AI の面の操作(REQ-206 §5)。選択肢は名前だけ。1つのボタンの判定の材料も Python が渡す
            "ai": self._ai_view(busy, pane),
            # 「差分＋全文を送る」(ADR-0158 決定1・9・10)。押せるか・押せない理由・案内も Python が決める
            "full_text": self._full_text_view(),
            # 開いた保存の結果(REQ-120)の帯の文と機密の注記。表示していなければ null
            "opened": {"text": self._opened, "note": compare_save_note()} if self._opened is not None else None,
            # まとめての比較(REQ-122)の一覧の行・選んでいる行・全体の文・「全部」のタブ・開くタブ。まとめて比べていなければ null
            "multi": self._multi_view(),
            # 届いたところまでの答え(ADR-0157 決定5〜7)。無ければ null。番号は消したときも進む(古い知らせを捨てるため)
            "partial": dict(self._partial) if self._partial is not None else None,
            "partial_revision": self._partial_revision,
            # 全部の答えが届いた後に残す考え(ADR-0169 決定4)。{text, cut, cut_note, target, revision}。無ければ null。
            # 画面は textContent だけで描く。保存・コピー・やり取りには入れない
            "thinking": dict(self._thinking) if self._thinking is not None else None,
            # API が返したエラーの文の折りたたみ(ADR-0168 決定5)。{title, text, note, revision}。文が無ければ null。
            # 両方の窓の view に入れる(AI の面を描く窓だけが使う)。画面は textContent だけで描く
            "api_error": self._api_error_view(),
            # AI の面を別の窓で出しているか(ADR-0160 決定1)。真の間、比較の窓は右の面を隠し、タブの行の右端に「↗」を出す
            "ai_detached": detached,
            # 単純比較の設定の欄(ADR-0166 / REQ-227 §11)。values は欄の値、problems は誤りの文、summary は折りたたみの見出しの
            # 要約、examples は例から足す選択肢、fill は変わったら画面が欄に値を入れ直す番号
            "raw_options": {
                "values": dict(self._raw_values),
                "problems": list(self._raw.problems),
                "summary": raw_options_summary(self._raw.options),
                "examples": raw_examples(),
                "fill": self._raw_fill,
            },
            # 窓の下の行に出す、使っている単純比較の設定の要約(ADR-0166 決定11-1)。既定なら null
            "raw_settings_line": self._raw_settings_line(),
            # キャラクターの絵を出すか(ADR-0174 決定1。その窓を最初に描いたときの設定)。偽なら絵の要素は hidden のまま
            "character": self._character_for(pane),
            # 起動時の絵を出すか(ADR-0174 決定2)。真にするのは比較の窓の最初の state() だけ(_state)
            "splash": False,
            # 起動したときに見つけた新しい版の知らせの文(REQ-252 §5)。無ければ null。画面は textContent だけで描く
            "update_notice": self._session.update_notice_view(),
        }

    def _character_for(self, pane: str) -> bool:
        """ロックの中で呼ぶ。pane の窓のキャラクターの設定。最初に描いたときに読み、その窓の間は変えない(ADR-0174 決定1)。"""
        if pane not in self._characters:
            self._characters[pane] = character_enabled(
                env=self._where["env"], platform=self._where["platform"], home=self._where["home"]
            )
        return self._characters[pane]

    def _mark_compared(self, *, tell: bool = False) -> None:
        """ロックの中で呼ぶ。この窓で比べた(空の窓の絵を隠す)。

        比較を始めたとき・保存した結果を開いたときは結果の番号を変えない(画面は比較中の間も絵を隠し、開いたときは番号が進む)。
        tell は、比較も結果の表示もしないで受け取ったとき(一括比較の「比較画面で開く」で答えが無いとき)。初めてのときだけ
        結果の番号を進め、画面に result() を読み直させる(結果の行は変わらない。変わるのは never_compared だけ)。
        """
        if tell and not self._compared_once:
            self._result_revision += 1
        self._compared_once = True

    def _banner_lines(self) -> list[str]:
        """ロックの中で呼ぶ。帯の行(結果の帯と、単純比較の既定のファイルを読めないときの帯)。"""
        return [*self._banner, *([self._raw_band] if self._raw_band is not None else [])]

    def _raw_settings_line(self) -> str | None:
        """ロックの中で呼ぶ。窓の下の行の文。保存した結果を開いているときは保存の設定(「保存した結果の」を頭に付ける)、
        ほかは今の設定の要約。既定・誤りのある間は None。
        """
        if self._opened is not None:
            if self._mview is not None and 0 <= self._pair < len(self._mview.entries):
                return saved_raw_settings_line(self._mview.entries[self._pair].outcome)
            return self._outcome.raw_settings_saved if self._outcome is not None else None
        return raw_options_summary(self._raw.options)

    def _notice_for(self, pane: str, detached: bool) -> dict[str, Any] | None:
        """ロックの中で呼ぶ。pane の窓の view に入れる知らせ(ADR-0160 決定6)。形は今と同じ(印のキーは外す)。

        AI の知らせ(印の付いたもの)は、AI の窓が開いていれば AI の窓にだけ、無ければ比較の窓に出す。ほかは比較の窓だけ。
        """
        notice = self._late_notice
        if notice is None:
            return None
        target = PANE_AI if detached and notice.get(NOTICE_PANE) == PANE_AI else PANE_COMPARE
        if pane != target:
            return None
        return {key: value for key, value in notice.items() if key != NOTICE_PANE}

    @staticmethod
    def _preview_dict(number: int, shown: PreviewView, payload: str, full_text: str | None) -> dict[str, Any]:
        """送信前の確認の状態。payload は確認に出す送る文の全部、full_text は全文を送るときの帯の文(送らないなら None)。"""
        return {
            "id": number,
            "title": LLM_PREVIEW_TITLE,
            "full_text": full_text,
            "summary": shown.summary,
            # 打ち切りの知らせの後ろに、上限を変えられる場所を添える(ADR-0167 決定6。preview_view は wx と共通なので変えない)
            "notes": [
                *shown.truncated,
                *([LIMITS_SCREEN_HINT] if shown.truncated else []),
                *([shown.unsent] if shown.unsent is not None else []),
            ],
            "payload_title": LLM_PREVIEW_PAYLOAD,
            "payload": payload,
            "unsent_shapes": shown.unsent_shapes,
            "send": LLM_PREVIEW_SEND,
            "dont_send": LLM_PREVIEW_DONT_SEND,
        }

    def _full_text_state(self) -> tuple[bool, str | None]:
        """ロックの中で呼ぶ。「差分＋全文を送る」を押せるかと、押せない理由の文(ADR-0158 決定9・ADR-0175 決定2)。

        比較中・質問中・閉じている途中・開いた結果の表示中は押せない。1組の比較は最後の結果が config か show のとき、
        まとめての比較は選んでいる組が config か show で(ADR-0182 決定1)、全体の答えが届いていて(追加の質問ができる)、その組を送る文の本文に入れたときだけ
        押せる(ADR-0152 決定10。全文は選んでいる組の変更後の区間だけ)。
        押せないときは理由の文を必ず返す(チェックのすぐ下の注記と title に出す)。閉じている途中だけは理由も None。
        押せるときは (True, None)。
        """
        if self._closing:
            return False, None
        if self._busy():
            return False, COMPARE_FULL_TEXT_BUSY
        if self._opened is not None:
            return False, COMPARE_FULL_TEXT_OPENED
        if self._last is not None:
            outcome: CompareOutcome | None = self._last[2]
            multi = False
        elif self._multi is not None and self._mview is not None and 0 <= self._pair < len(self._mview.entries):
            outcome = self._mview.entries[self._pair].outcome
            multi = True
        else:
            return False, COMPARE_FULL_TEXT_NOT_YET
        if outcome is None:
            return False, COMPARE_FULL_TEXT_UNPAIRED
        if outcome.kind not in ("config", "show"):
            return False, COMPARE_FULL_TEXT_NOT_CONFIG  # 種類が決まらないなど(ADR-0182 決定1)
        if multi and self._first_answer() is None:
            return False, COMPARE_FULL_TEXT_MULTI_FIRST
        if multi and (self._multi is None or self._pair not in self._multi[2].llm_pairs):
            return False, COMPARE_FULL_TEXT_NOT_SENT  # 全体の送信の本文に入れていない組(差分なし・送れる差分が無い組)
        return True, None

    def _full_text_target(self, enabled: bool) -> str | None:
        """ロックの中で呼ぶ。全文を付ける組の名前(一覧の行の名前と同じ文。ADR-0175 決定2)。

        まとめての比較で押せるときだけ。1組の比較・押せないときは None。
        """
        if not enabled or self._last is not None or self._multi is None or self._mview is None:
            return None
        if not 0 <= self._pair < len(self._mview.entries):
            return None
        return self._mview.entries[self._pair].row.name

    def _full_text_view(self) -> dict[str, Any]:
        """「差分＋全文を送る」の状態。注記には reason(押せないとき)か target_note(まとめての比較で押せるとき)を出す。"""
        enabled, reason = self._full_text_state()
        target = self._full_text_target(enabled)
        return {
            "checked": self._full_text,
            "enabled": enabled,
            "note": COMPARE_FULL_TEXT_NOTE if self._full_text else None,
            "reason": reason,
            "target": target,
            "target_note": COMPARE_FULL_TEXT_PAIR(target) if target is not None else None,
        }

    def _multi_view(self) -> dict[str, Any] | None:
        """一覧の状態。行(名前・状態・差分の件数・相手なしか)は短いので view に入れる。revision が変わったら画面は行を作り直す。

        「全部」のタブ(ADR-0152 決定7)の題と行の番号(行は all_rows() で渡す。番号が変わったときだけ画面が呼ぶ)、
        開くタブ(決定8)とその番号(変わったときだけ画面がタブを移す)も入れる。
        """
        mv = self._mview
        if mv is None:
            return None
        return {
            "revision": self._multi_revision,
            "rows": [asdict(entry.row) for entry in mv.entries],
            "selected": self._pair,
            "overall": mv.overall,
            "all_title": all_tab_title(len(self._current_all_rows())),
            "all_revision": self._all_revision,
            "tab": self._tab,
            "tab_revision": self._tab_revision,
        }

    def _current_all_rows(self) -> list[AllRow]:
        """ロックの中で呼ぶ。「全部」のタブの行。番号ごとに1回だけ作る(描くたびに作り直さない)。"""
        mv = self._mview
        if mv is None:
            return []
        if self._all_rows is None or self._all_rows[0] != self._all_revision:
            self._all_rows = (self._all_revision, all_rows(mv.entries))
        return self._all_rows[1]

    def _current_all_marks(self) -> list[dict[str, Any]]:
        """ロックの中で呼ぶ。「全部」のタブの行の印。行を作り直したときだけ求め直す(REQ-226 §3)。"""
        rows = self._current_all_rows()
        if self._all_marks is None or self._all_marks[0] is not rows:
            self._all_marks = (rows, all_marks(rows))
        return self._all_marks[1]

    def _current_view_marks(self, view: OutcomeView) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """ロックの中で呼ぶ。表示中の結果の構造化比較と単純比較の印。結果が同じ間は使い回す(REQ-226 §3)。"""
        if self._view_marks is None or self._view_marks[0] is not view:
            self._view_marks = (view, structured_marks(view.structured_rows), raw_marks(view.raw_rows))
        return self._view_marks[1], self._view_marks[2]

    def _segments_shown(self) -> bool:
        """上の3行目に区間の選択を出すか。どちらかの側に区間が複数あるか、区間を選んでいるとき(REQ-206 §2)。"""
        for side in SIDES:
            view = self._side_views[side]
            if view is not None and (len(view.choices) > 1 or self._form[f"{side}_segment"] != 0):
                return True
        return False

    def _ai_view(self, busy: bool, pane: str) -> dict[str, Any]:
        """AI の面の操作の状態(REQ-206 §5)。描くたびにプロファイルのファイルを読む(設定の窓で変えた後に合わせる)。

        選択肢は名前と使用中の位置だけ(接続先の URL・キー・CLI の場所は入れない)。比較中・質問中・閉じている途中は選べない。
        1つのボタン: 「質問する」は can_ask が真で欄に文字があるとき(画面は欄の文字の有無だけを見る)。ほかは「AI で解析」で、
        can_send_llm が真のときだけ押せる。clear が変わったら画面は欄を空にする。
        """
        where = self._profile_where()
        profiles = llm_choices(**where)
        prompts = prompt_choices(**where)
        notes = [problem for problem in (profiles.problem, prompts.problem) if problem is not None]
        return {
            "profiles": list(profiles.names),
            "profile": profiles.active,
            "prompts": list(prompts.names),
            "prompt": prompts.active,
            "can_choose": not busy and not self._closing,
            "note": "\n".join(notes) if notes else None,
            "can_ask": self._can_ask(),
            "can_send_llm": self._can_send_llm(),
            "ask_label": COMPARE_FOLLOW_UP_ASK,
            # pane の窓のキャラクターがオンなら「AI（Mirarin）で解析」(ADR-0181 決定2。設定はその窓を最初に描いたときのもの)
            "send_label": COMPARE_AI_ANALYZE_CHARACTER if self._character_for(pane) else COMPARE_AI_ANALYZE,
            # 開いた保存の結果では、送って届いた AI の答えがあるときだけ欄を使える(「質問する」だけ。REQ-242 §4)
            "input_enabled": not busy
            and not self._closing
            and (self._opened is None or self._first_answer() is not None),
            "clear": self._instruction_clear,
        }

    def _profile_where(self) -> dict[str, Any]:
        where = self._where
        return {"env": where["env"], "platform": where["platform"], "home": where["home"]}

    def _follow_up_view(self) -> dict[str, Any] | None:
        """追加の質問の枠の状態。やり取り(質問と回答を交互に)・押せるか・質問中か・上限の案内・描き直す番号・
        最初の依頼の文(first_request。文か None。ADR-0179 決定1。画面は最初の答えの上の右の吹き出しに textContent で出す)。
        """
        if self._first_answer() is None:
            return None
        turns: list[dict[str, Any]] = []
        for question, answer in self._turns:
            # 回答だけ読み取ったブロック(blocks)とコピーの文(copy。<red> を外す)を添える。質問の文は今のまま文字で出す(REQ-204 §3)
            turns += [
                {"label": FOLLOW_UP_QUESTION_LABEL, "text": question},
                {
                    "label": FOLLOW_UP_ANSWER_LABEL,
                    "text": answer,
                    "blocks": answer_blocks(answer),
                    "copy": answer_copy_text(answer),
                },
            ]
        return {
            "turns": turns,
            "can_ask": self._can_ask(),
            "asking": self._asking is not None,
            "note": follow_up_limit(MAX_TURNS) if len(self._turns) >= MAX_TURNS else None,
            "revision": self._follow_up_revision,
            "first_request": self._first_request,
        }

    def _auto_send_band(self) -> str | None:
        """自動送信の帯の文。描くたびに設定を読む(設定の窓で変えた後に合わせる)。オフなら None。"""
        where = self._where
        try:
            name = auto_send_target(env=where["env"], platform=where["platform"], home=where["home"])
        except SettingsError:
            return auto_send_band(None)  # プロファイルを読めない(load_settings も止まるので送らない)
        return None if name is None else auto_send_band(name)

    def _show(self, inp: ScreenInput, view: OutcomeView, llm: LlmAnalysis | None = None) -> None:
        """結果を表示する状態にする(wx の _show と同じ判断)。行は result() で渡す。

        llm は view の元の AI 解析(429 の待つ秒を AI の面の行に出すときだけ使う。ADR-0168 決定4)。
        """
        self._outcome = view
        self._result_revision += 1
        # 窓の下の文は、差分ありの内訳を添えた pywebview の文(REQ-113 §3。wx は status_text のまま)
        self._result_status = view.status_text_web
        self._stale = False
        self._status = [view.status_text_web, view.counts_text]
        if view.sides is not None:
            for side in SIDES:
                side_view = view.sides[side]
                self._side_views[side] = side_view
                self._form[f"{side}_segment"] = segment_index(side_view, None if side_view.waiting else inp.segment(side))
                self._summaries[side] = input_summary(inp.side(side), side_view.segment_count)
            self._fill += 1
        banner = list(view.banner)
        selection = view.selection
        self._segment_title = COMPARE_SEGMENTS + (COMPARE_SEGMENTS_WAITING if selection and selection.waiting else "")
        focus = None
        if selection is not None:
            # 選択待ちはダイアログにしない。帯とステータスで促し、選ぶ欄へ移す(ADR-0022 決定5)
            banner.append(selection.prompt)
            if selection.detail:
                banner.append(selection.detail)
            if selection.waiting:
                focus = f"{selection.waiting[0]}_segment"
            elif selection.focus_format_fields:
                focus = "platform"
        self._banner = banner
        self._focus = focus
        if focus is not None:
            self._focus_revision += 1
        self._set_llm(view, self._llm_notes, llm)

    def _set_llm(self, view: OutcomeView, notes: list[str], llm: LlmAnalysis | None = None) -> None:
        """ロックの中で呼ぶ。AI の面の文とコピーの文。頭の行を除いた行(llm_screen_lines。ADR-0148 決定8)の前に注記を足す。

        llm があり、解析が 429 で API が待つ秒を返していれば、その秒を出した行にする(ADR-0168 決定4。秒は保存しない)。
        """
        lines = view.llm_screen_lines
        if lines is not None and (limited := self._rate_limit_lines(llm)) is not None:
            lines = limited
        self._llm_text = "\n".join([*notes, *lines]) if lines is not None else None
        self._llm_copy = view.llm_copy

    def _show_opened(self, view: OutcomeView, band: str) -> None:
        """ロックの中で呼ぶ。開いた保存の結果を表示する状態にする(REQ-120)。行は result() で渡す。

        区間の選択肢・区間の選択・入力・要約には触らない(開く前の入力のまま)。フォーカスも移さない。
        """
        self._opened = band
        self._outcome = view
        self._result_revision += 1
        self._result_status = view.status_text_web
        self._status = [view.status_text_web, view.counts_text]
        banner = list(view.banner)
        if view.selection is not None:
            banner.append(view.selection.prompt)
            if view.selection.detail:
                banner.append(view.selection.detail)
        self._banner = banner
        self._set_llm(view, [])

    def _show_multi(self, inp: ScreenInput | None, mv: MultiView, *, keep: bool = False) -> None:
        """ロックの中で呼ぶ。まとめての比較の一覧を表示する状態にする(REQ-122 §2)。行の結果は result() で渡す。

        inp が None なのは開いた .jsonl(区間の選択肢・入力・要約には触らない。REQ-120)。比べた結果では、区間の選択欄に
        区間の一覧を入れ、選択は「自動」のままにする(「選んでください」は出さない)。keep が真で行の数が同じなら、選んでいる行を残す
        (AI に送った後など、同じ入力で比べ直したとき)。ほかは最初の差分ありの組を選ぶ。
        keep が偽(比べ終わった・開いた)なら「全部」のタブを開く(ADR-0152 決定7)。keep が真(AI の後)はタブを変えない(決定8)。
        """
        keep_pair = keep and self._mview is not None and len(self._mview.entries) == len(mv.entries)
        self._mview = mv
        self._pair = self._pair if keep_pair else mv.first
        self._pair_views = {}
        self._multi_revision += 1
        self._all_revision += 1
        if not keep:
            self._tab = TAB_ALL
            self._tab_revision += 1
        self._result_status = mv.overall
        self._stale = False
        self._segment_title = COMPARE_SEGMENTS
        self._focus = None
        if inp is not None and self._multi is not None:
            views = segment_views(self._multi[2], self._multi[1])
            for side in SIDES:
                self._side_views[side] = views[side]
                self._form[f"{side}_segment"] = 0
                self._summaries[side] = input_summary(inp.side(side), views[side].segment_count)
            self._fill += 1
        self._show_pair()

    def _show_pair(self) -> None:
        """ロックの中で呼ぶ。選んでいる行の結果を表示する状態にする。窓の下の左は全体の文、右は選んだ組の REQ-113 の内訳。

        相手なしの行は表を空にし、区間の説明を出す。比較中・質問中は左の文(「AI に送っています」など)を変えない。
        AI の欄は全体の答え(MultiView.llm。ADR-0152 決定3)で、行によらず同じ。組ごとの理由の文は出さない。
        全体の答えが無い一覧(開いた一括比較の .jsonl・古い保存)だけ、今までどおり選んだ組の AI の欄を出す。
        """
        mv = self._mview
        assert mv is not None
        entry = mv.entries[self._pair]
        self._result_revision += 1
        main = self._status[0] if self._busy() else mv.overall
        notes = self._llm_notes if self._opened is None else []
        if entry.outcome is None:
            self._outcome = None
            self._unpaired_note = entry.note
            self._status = [main, entry.row.status]
            self._banner = []
            self._set_whole_llm(mv, notes)
            return
        view = self._pair_views.get(self._pair)
        if view is None:
            view = self._pair_views[self._pair] = view_outcome(entry.outcome)
        self._outcome = view
        self._unpaired_note = None
        self._status = [main, view.status_text_web]
        banner = list(view.banner)
        if view.selection is not None:
            banner.append(view.selection.prompt)
            if view.selection.detail:
                banner.append(view.selection.detail)
        self._banner = banner
        if mv.llm is None and self._multi is None:
            self._set_llm(view, notes)  # 全体の答えの無い開いた .jsonl は組ごと(今までどおり)
        else:
            self._set_whole_llm(mv, notes)

    def _set_whole_llm(self, mv: MultiView, notes: list[str]) -> None:
        """ロックの中で呼ぶ。AI の面に全体の答えを出す(頭の行を除いた行の前に注記を足す。_set_llm と同じ形)。"""
        lines, copy = whole_answer_lines(mv.llm)
        if lines is not None and (limited := self._rate_limit_lines(mv.llm)) is not None:
            lines = limited  # 全体の送信が 429 で、API が待つ秒を返したとき(ADR-0168 決定4)
        self._llm_text = "\n".join([*notes, *lines]) if lines is not None else None
        self._llm_copy = copy

    def _drop_multi(self) -> None:
        """ロックの中で呼ぶ。まとめての比較の結果・一覧・「全部」のタブの行を消す。"""
        if self._mview is not None or self._multi is not None:
            self._multi_revision += 1
        self._multi = None
        self._mview = None
        self._pair = 0
        self._pair_views = {}
        self._unpaired_note = None
        self._all_rows = None
        self._all_marks = None

    def _leave_opened(self) -> bool:
        """ロックの中で呼ぶ。開いた保存の結果を表示していれば消して True(「結果なし」に戻す。入力は残す)。"""
        if self._opened is None:
            return False
        self._opened = None
        self._clear_result()
        return True

    # --- 開く・欄 ---

    def _state(self) -> dict[str, Any]:
        with self._lock:
            # 起動時の絵の番は、最初に読み込まれた窓が使う(キャラクターがオフでも使う。ADR-0174 決定2)
            first = self._session._take_splash()
            result = self._ok()
            if first and result["view"]["character"]:
                result["view"]["splash"] = True
        # 起動したときの新しい版の確かめ(REQ-252 §5)。1 つの起動で 1 回だけ(2 回目からは session が止める)。ロックの外で呼ぶ
        self._session.start_update_check(
            partial(auto_update_check_enabled, env=self._where["env"], platform=self._where["platform"], home=self._where["home"])
        )
        # --update-started で起動したときの started-ok(REQ-253 §7)。窓が出た後の印として、1 つの起動で 1 回だけ書く。ロックの外で呼ぶ
        mark_update_started = getattr(self._session, "mark_update_started", None)
        if mark_update_started is not None:
            mark_update_started()
        return result

    def _settings_key(self) -> tuple[Any, ...]:
        """自動の比較を始める比較の設定(比較方式・種類・実際の機種・show のコマンド・両側の区間の番号)。

        区間は選択欄の位置ではなく番号で比べる(結果で区間の欄を埋め直しても、古い位置の form_changed で始めない)。
        """
        form = self._form
        # 種類は欄の選択のまま比べる(自動と config を切り替えたら、比べる要求が同じでも比べ直す)
        _kind, platform = web_kind_platform(form["kind"], form["platform"], form["config_platform"])
        return (
            form["mode"],
            form["kind"],
            platform,
            form["command"],
            *(segment_number(self._side_views[side], form[f"{side}_segment"]) for side in SIDES),
            # 単純比較の設定(誤りのある間は None。自動の比較は始めない)。前後の行数は表示だけの値なので入れない
            # (前後の行数の誤りだけなら、読めた設定のまま。REQ-228 §2)
            self._raw.compare_options,
        )

    def _reset_raw_options(self) -> dict[str, Any]:
        with self._lock:
            previous = self._settings_key()
            options, context, self._raw_band = raw_form_defaults(
                env=self._where["env"], platform=self._where["platform"], home=self._where["home"]
            )
            if context != self._raw_context:
                self._raw_context = context
                self._result_revision += 1
            self._raw = RawForm(options, [], context)
            self._raw_values = raw_form_values(options, context)
            self._raw_fill += 1
            # 「既定に戻す」で単純比較の設定が変わったら、一括比較の答えは捨てる(知らせは出さない。ADR-0170 決定4)
            self._drop_handed_if_changed(previous)
            self._refresh_save()
            return self._ok()

    def _form_changed(self, form: Any) -> dict[str, Any]:
        started = False
        with self._lock:
            previous = self._settings_key()  # 前回の値(run・browse・paste・swap の _take でも変わる)
            blocked = self._raw.options is None  # 単純比較の設定の誤りで止まっていたか
            self._take(form)  # 比較の設定を変えたら一括比較の答えは捨てる(_take の中。知らせは出さない。ADR-0170 決定4)
            self._refresh_save()
            notice = None
            # 前後の行数の誤りだけを直したときは設定の値が同じ。その間に比べる設定を変えていて、最後に比べた入力と違うときだけ始める
            unblocked = blocked and self._raw.options is not None and not self._input_unchanged()
            if self._settings_key() != previous or unblocked:
                # 比較中は _can_run が偽なので始めない(終わった後にやり直さない)。選択待ちで区間を選んだときも、ここで1回だけ
                notice, started = self._auto_begin()
            result = self._reply(notice)
        if started:
            self._session.gate_changed(self)
        return result

    def _swap(self, form: Any) -> dict[str, Any]:
        started = False
        with self._lock:
            self._take(form)
            self._handed = None  # 入れ替えを押したら一括比較の答えは捨てる(知らせは出さない。ADR-0170 決定4)
            if self._busy() or self._closing or self._opened is not None:
                return self._ok()  # 開いた保存の結果の表示中も入れ替えない(REQ-120)
            # 入力の中身と名前・区間の選択肢・要約・区間の選択を入れ替える(片方だけでも入れ替える)
            # 貼り付けの入力の名前は、入った側の名前に直す(REQ-199 §6)。ファイルの名前はそのまま動く
            self._sources = {
                side: _renamed_paste(self._sources[other], side) for side, other in (("before", "after"), ("after", "before"))
            }
            self._side_views = {"before": self._side_views["after"], "after": self._side_views["before"]}
            self._summaries = {"before": self._summaries["after"], "after": self._summaries["before"]}
            self._form["before_segment"], self._form["after_segment"] = self._form["after_segment"], self._form["before_segment"]
            self._fill += 1
            self._clear_follow_up()
            self._full_text = False  # 入力が変わったので「差分＋全文を送る」は外す(ADR-0158 決定1)
            notice, started = self._auto_begin()
            if not started:
                # 向きの違う結果を残さない(オフのときと、オンでも始められなかったとき)
                self._clear_result()
            result = self._reply(notice)
        if started:
            self._session.gate_changed(self)
        return result

    def _clear_result(self) -> None:
        """ロックの中で呼ぶ。表示中の結果を消して「結果なし」に戻す(表・タブの題・AI の文・保存の可否)。区間の選択肢は残す。"""
        self._outcome = None
        self._last = None
        self._drop_multi()
        self._llm_text = None
        self._llm_copy = None
        self._llm_notes = []
        self._preview = None
        self._preview_first_request = None
        self._sent_profiles = {}
        self._sent_name_only = False
        self._destination_confirm = None
        self._clear_follow_up()
        # 開いた保存の中身と、伏せる語の確認も消す(開いた結果を閉じた・新しい入力・別の結果を開くもここを通る。REQ-242 §4)
        self._opened_saved = None
        self._words_confirm = None
        self._saved_words_confirmed = False
        self._handed = None  # 一括比較の答えも捨てる(「結果を開く…」もここを通る。ADR-0170 決定4)
        # 「差分＋全文を送る」は外す(開いた結果を閉じたときも。ADR-0158 決定1)
        self._full_text = False
        self._first_full_text = {}
        self._can_save = False
        self._result_status = ""
        self._stale = False
        self._status = ["", ""]
        self._banner = []
        self._segment_title = COMPARE_SEGMENTS
        self._focus = None
        self._result_revision += 1

    # --- 入力 ---

    def _browse(self, form: Any, side: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            side = self._side(side)
            if self._busy() or self._closing:
                return self._ok()
        path = self._choose_file(DIALOG_OPEN, "", INPUT_FILE_TYPES)  # 開いている間はロックを持たない
        if path is None:
            with self._lock:
                return self._ok()  # 取り消した
        try:
            source = read_file(path)
        except InputReadError as exc:
            with self._lock:
                return self._ok(notice=self._notice(str(exc), COMPARE_READ_FAILED_TITLE))
        started = False
        with self._lock:
            notice = None
            if not self._busy():
                left = self._leave_opened()  # 開いた結果の表示を消したその入力では、自動の比較を始めない(REQ-120)
                self._set_source(side, source)
                if not left:
                    notice, started = self._auto_begin()  # form は受け取った今の値
            result = self._reply(notice)
        if started:
            self._session.gate_changed(self)
        return result

    def _paste(self, form: Any, side: Any, text: Any) -> dict[str, Any]:
        started = False
        with self._lock:
            self._take(form)
            side = self._side(side)
            if not isinstance(text, str):
                raise _BadRequest
            notice = None
            if not self._busy() and not self._closing:
                left = self._leave_opened()  # 開いた結果の表示を消したその入力では、自動の比較を始めない(REQ-120)
                self._set_source(side, pasted(text, side))
                if not left:
                    notice, started = self._auto_begin()  # form は受け取った今の値
            result = self._reply(notice)
        if started:
            self._session.gate_changed(self)
        return result

    # --- ファイルのドロップ(REQ-110 / ADR-0076 決定1。pywebview が事象のスレッドから呼ぶ。画面からは呼べない) ---

    def _on_drop(self, event: Any) -> None:
        """窓へのドロップ(app.watch_drop が印の要素(#drop-anchor)の drop に登録する。REQ-205)。想定外の例外は文面を出さない知らせにする。"""
        try:
            self._drop(event)
        except Exception:  # noqa: BLE001 - 事象のスレッドの例外は画面に届かないので、文面を出さない知らせにする
            with self._lock:
                self._late_notice = {"id": self._next_serial(), **self._notice(gui_web_call_failed(), COMPARE_DROP_TITLE)}
            self._changed()

    def _drop_notice(self, body: str, title: str) -> None:
        """入力を変えずに知らせを出す(ロックの外で呼ぶ。画面は番号で1回だけ出す)。"""
        with self._lock:
            self._late_notice = {"id": self._next_serial(), **self._notice(body, title)}
        self._changed()

    def _drop(self, event: Any) -> None:
        with self._lock:
            if self._busy() or self._closing:
                return  # 比較中・閉じている途中は受け付けない
        paths = dropped_paths(event)
        if not paths:
            self._drop_notice(COMPARE_DROP_NO_PATH, COMPARE_DROP_TITLE)
            return
        if len(paths) > len(SIDES):
            self._drop_notice(COMPARE_DROP_TOO_MANY, COMPARE_DROP_TITLE)
            return
        if any(path.is_dir() for path in paths):
            self._drop_notice(COMPARE_DROP_FOLDER, COMPARE_DROP_TITLE)
            return
        if len(paths) == len(SIDES):
            paths = list(order_pair(*paths))  # 2つなら更新日時・語・数字・名前の順で決める(ADR-0078 決定1)
        try:
            sources = [read_file(path) for path in paths]  # 読む間はロックを持たない
        except InputReadError as exc:
            # 片方だけは入れない(どちらも入れない)。文は「参照…」と同じ
            self._drop_notice(str(exc), COMPARE_READ_FAILED_TITLE)
            return
        started = False
        with self._lock:
            if self._busy() or self._closing:
                return
            left = self._leave_opened()  # 開いた結果の表示を消したその入力では、自動の比較を始めない(REQ-120)
            if len(sources) == len(SIDES):
                for side, source in zip(SIDES, sources, strict=True):
                    self._set_source(side, source)
            else:
                self._set_source("before" if self._sources["before"] is None else "after", sources[0])
            # 2つを同時に入れても、判断は入れ終わった後の1回だけ。form は Python が持っている最後の値
            notice, started = (None, False) if left else self._auto_begin()
            if notice is not None:
                self._late_notice = {"id": self._next_serial(), **notice}
        if started:
            self._session.gate_changed(self)
        self._changed()

    # --- 実行(wx の _on_run と同じ順) ---

    def _run(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            notice, started = self._begin()
            result = self._reply(notice)
        if started:
            self._session.gate_changed(self)
        return result

    def _auto_begin(self) -> tuple[dict[str, Any] | None, bool]:
        """ロックの中で呼ぶ。入力を変えた後の自動の比較(ADR-0076 決定2)。返すのは (知らせ, 始めたか)。

        「比較実行」を押せて、[gui] auto_compare がオンのときだけ、「比較実行」と同じ処理(_begin)で始める。
        設定は決めるたびに読む(設定の窓で変えると次の判断から効く。読めなければ始めない)。
        """
        if not self._can_run():
            return None, False
        if self._raw.options is None:
            return None, False  # 単純比較の設定に誤りがある間は始めない(誤りは欄の帯に出ている)
        where = self._where
        if not auto_compare_enabled(env=where["env"], platform=where["platform"], home=where["home"]):
            return None, False
        return self._begin()

    def _begin(self) -> tuple[dict[str, Any] | None, bool]:
        """ロックの中で呼ぶ。「比較実行」の処理。返すのは (知らせ, 始めたか)。

        AI は呼ばない(「AI に送る」のボタンと自動送信だけが呼ぶ。ADR-0080 決定1)。門が取れなければ始めない。
        """
        if not self._can_run():
            return None, False
        if self._raw.options is None:
            # 単純比較の設定に誤りがあれば比べない(誤りの文を知らせる。REQ-227 §11.3)
            return self._notice("\n".join(self._raw.problems), RAW_OPTIONS_INVALID_TITLE), False
        inp = self._current_input()
        assert inp is not None
        self._llm_notes = []
        # 新しく比較を始めたら「差分＋全文を送る」は外す(前の config のつもりで別の config を送らない。ADR-0158 決定1)
        self._full_text = False
        return None, self._start(inp, build_request(inp), None, None, RUN_COMPARE)

    def _llm_plan(self) -> tuple[LlmPlan, LlmSettings] | str:
        """ロックの中で呼ぶ。LLM の設定を読んで送り方と設定を返す(作業フォルダの無視の注記は _llm_notes に置く)。

        設定が読めなければ理由の文を返す(CLI と同じく、そのときは送らない)。設定はまとめての比較の全体の上限と確認の要約に使う。
        """
        loaded = self._load_plan()
        if isinstance(loaded, str):
            return loaded
        plan, notes, settings = loaded
        self._llm_notes = notes
        return plan, settings

    def _load_plan(self) -> tuple[LlmPlan, list[str], LlmSettings] | str:
        """LLM の設定を読んで、送り方と作業フォルダの無視の注記と設定を返す(状態は変えない)。読めなければ理由の文。"""
        try:
            settings = load_settings(**self._where)
        except SettingsError as exc:
            return str(exc)
        notes: list[str] = []
        workdir = (self._where["cwd"] or Path.cwd()) / WORKDIR_CONFIG
        if settings.ignored_workdir_api_key:
            notes.append(workdir_api_key_ignored(workdir.absolute()))
        if settings.ignored_workdir_limits:
            notes.append(workdir_limits_ignored(workdir.absolute(), settings.ignored_workdir_limits))
        if settings.ignored_user_prompt_template and settings.user_config is not None:
            notes.append(user_prompt_template_ignored(settings.user_config))
        # 1組の上限は設定の値(利用者ごとの設定ファイルか既定。ADR-0071 決定5)
        return plan_llm(settings, client_factory=self._client_factory), notes, settings

    def _send_llm(self, form: Any, instruction: Any) -> dict[str, Any]:
        started = False
        with self._lock:
            self._take(form)
            if not isinstance(instruction, str):
                raise _BadRequest
            if not self._can_send_llm():
                return self._ok()
            planned = self._llm_plan()
            if isinstance(planned, str):
                return self._ok(notice=self._notice(planned, COMPARE_LLM_SETTINGS_TITLE))
            plan, settings = planned
            # 最初の依頼の文(ADR-0179 決定1)。打っていなければ送ったときのプロンプトの名前の文。答えが届いたときに窓に持つ
            first_request = COMPARE_FIRST_REQUEST_PROMPT(_prompt_name(settings))
            if instruction.strip():
                # 解析の前に打った文(REQ-206 §6 / ADR-0148 決定6)。追加の質問と同じ確かめを、設定の秘密の語で通す。
                # 断ったら送らず、追加の質問の断りと同じ形で知らせる(文は属性・知らせ・返す値に入れない)
                refused = check_question(instruction, normalize_words(plan.first.extra_secret_words))
                if refused is not None:
                    return self._follow_up_notice(follow_up_refused(refused, MAX_QUESTION_CHARS))
                plan = with_instruction(plan, instruction)
                first_request = instruction  # 確かめを通って送る文に入れた文そのもの(既に payload に入る)
            if self._full_text and self._last is not None and self._full_text_state()[0]:
                # 「差分＋全文を送る」(ADR-0158 決定6)。1回目と2回目の両方に付ける(同じ文になり、承認の照合が一致する)。
                # まとめて比べたときの最初の解析では付けない(押せない。決定9)
                plan = with_full_text(plan)
            last = self._last if self._last is not None else self._multi
            assert last is not None
            inp, request, _outcome = last
            # 1回目は送らない(dry_run)。送る内容があれば送信前のプレビューで止まる(ADR-0018 決定6。まとめて比べたときも
            # 全部の組をまとめた文の1件の確認。ADR-0152 決定3)。接続が使えない(send が無い)ときは1回で終わり、理由が AI 解析の枠に出る
            started = self._start(
                inp,
                request,
                plan.first,
                plan,
                RUN_PREVIEW,
                settings=settings,
                destination=destination_of(settings),
                first_request=first_request,
            )
            result = self._ok()
        if started:
            self._session.gate_changed(self)
        return result

    def _start(
        self,
        inp: ScreenInput,
        request: CompareRequest,
        llm: LlmRequest | None,
        plan: LlmPlan | None,
        kind: str,
        *,
        settings: LlmSettings | None = None,
        runner: Callable[..., Any] | None = None,
        destination: tuple[str, str] | None = None,
        max_total_diffs: int | None = None,
        first_request: str | None = None,
    ) -> bool:
        """ロックの中で呼ぶ。門を取れたら比較を始めて True。kind は始めた理由(RUN_*)。

        比べる関数は既定で model.compare_screen(区間が複数で自動ならまとめて比べ、ほかは self._runner)。
        settings は「AI に送る」の1回目で読んだ設定(まとめて比べたときの全体の上限と知らせの文に使う)。
        destination は送り先の AI の接続のプロファイル (id, 名前)(答えが届いたら覚える。ADR-0148 決定7)。
        max_total_diffs はまとめて比べたときの全体の上限(省けば settings の値、それも無ければ既定)。
        first_request は最初の依頼の文(ADR-0179 決定1)。送信前の確認の1回目なら確認と組で持ち、承認した2回目と自動送信で
        答えが届いたら窓に持つ。
        """
        if not self._session.acquire(self):
            return False  # 一括比較が走っている
        if max_total_diffs is None:
            max_total_diffs = settings.max_total_diffs if settings is not None else DEFAULT_MAX_TOTAL_DIFFS
        self._preview = None
        self._clear_follow_up()  # 比較し直すと、追加の質問のやり取りは消える(ADR-0081 決定5)
        self._late_notice = None
        self._run_kind = kind
        self._mark_compared()
        # 全文も付けた要求か(承認して送った回の答えなら、追加の質問でその全文を確かめ直さない。ADR-0158 決定6)
        full_text = llm is not None and llm.include_after_text
        self._worker = CompareWorker(
            request,
            llm=llm,
            post=self._post,
            on_progress=self._on_progress,
            on_done=lambda outcome: self._on_done(
                inp, request, outcome, plan, kind, settings, destination, full_text, max_total_diffs, first_request
            ),
            on_cancelled=self._on_cancelled,
            on_failed=self._on_failed,
            runner=runner
            if runner is not None
            else partial(compare_screen, runner=self._runner, max_total_diffs=max_total_diffs),
        )
        if kind == RUN_AUTO_SEND:
            # 結果は出したまま。状態の文は「AI に送っています」に固定し、件数の文は残す(ADR-0080 決定2)
            self._status[0] = COMPARE_AUTO_SENDING
        else:
            self._status[1] = ""
        self._worker.start()
        return True

    def _start_streaming(
        self, stream: _AnswerStream, *args: Any, sink: _ApiErrorSink | None = None, **kwargs: Any
    ) -> bool:
        """ロックの中で呼ぶ。_start で始め、始めたら stream を今の流れにする(_start が前の途中の答えを消した後に入れる)。

        sink は要求の on_api_error に付けた受け取り口(ADR-0168 決定5)。始めたら今の受け取り口にする(_start が前の文を消した後)。
        """
        started = self._start(*args, **kwargs)
        if started:
            stream.worker = self._worker
            self._stream = stream
            if sink is not None:
                self._api_error_sink = sink
        return started

    def _auto_send(self) -> bool:
        """ロックの中で呼ぶ。比較だけの結果を出した後の自動送信(ADR-0080 決定2)。始めたら True。

        [gui] auto_send_llm がオン(決めるたびに読む)で、「AI に送る」を押せるときだけ、確認なしに送る比較を裏で始める
        (approve を通さない)。接続が使えない(send が無い)ときは first で1回だけ比べ、理由が AI 解析の枠に出る(送らない)。
        設定が読めなければ送らず、理由を AI 解析の枠に出す。
        まとめて比べたときは、全部の組をまとめて1回で送る(auto_send_all。全体の上限を超えれば送らない。ADR-0152 決定3)。
        送るときは1組と同じく届いたところから出す(ADR-0152 決定10)。
        """
        where = self._where
        if not auto_send_llm_enabled(env=where["env"], platform=where["platform"], home=where["home"]):
            return False
        if not self._can_send_llm():
            return False
        planned = self._llm_plan()
        if isinstance(planned, str):
            self._llm_text = auto_send_settings_unreadable(planned)
            self._llm_copy = None
            self._result_revision += 1
            return False
        # 解析の前に打った文は足さない(欄を読まない。REQ-206 §6)
        plan, settings = planned
        destination = destination_of(settings)
        # 最初の依頼の文は「自動で送りました（プロンプト『…』）」(ADR-0179 決定1。答えが届いたら窓に持つ)
        first_request = COMPARE_FIRST_REQUEST_AUTO(_prompt_name(settings))
        if self._multi is not None:
            inp, request, _multi = self._multi
            total = settings.max_total_diffs
            if plan.send is None:
                runner = partial(auto_send_all, plan=plan, max_total_diffs=total)
                return self._start(inp, request, None, None, RUN_AUTO_SEND, runner=runner, destination=destination)
            stream = _AnswerStream(self, TARGET_ANALYSIS)
            sink = _ApiErrorSink(self, TARGET_ANALYSIS)
            streaming = LlmPlan(
                first=plan.first,
                send=dataclasses.replace(plan.send, on_delta=stream, on_thinking=stream.thinking, on_api_error=sink),
            )
            runner = partial(auto_send_all, plan=streaming, max_total_diffs=total)
            return self._start_streaming(
                stream,
                inp,
                request,
                None,
                None,
                RUN_AUTO_SEND,
                runner=runner,
                destination=destination,
                sink=sink,
                first_request=first_request,
            )
        assert self._last is not None
        inp, request, _outcome = self._last
        if plan.send is None:
            return self._start(inp, request, plan.first, None, RUN_AUTO_SEND, destination=destination)
        # 送るときは届いたところから出す(ADR-0157 決定5)
        stream = _AnswerStream(self, TARGET_ANALYSIS)
        sink = _ApiErrorSink(self, TARGET_ANALYSIS)
        llm = dataclasses.replace(plan.send, on_delta=stream, on_thinking=stream.thinking, on_api_error=sink)
        return self._start_streaming(
            stream, inp, request, llm, None, RUN_AUTO_SEND, destination=destination, sink=sink, first_request=first_request
        )

    def _cancel(self) -> dict[str, Any]:
        with self._lock:
            if self._worker is not None:
                self._worker.cancel()
            if self._asking is not None:
                # 送った要求は止められない。答えが届くまで門を持ち、届いた答えは捨てる(比較の「中止」が工程の境目まで待つのと同じ)
                self._asking.cancelled = True
            return self._ok()

    # --- ワーカーのスレッドから(状態はロックの中だけ。知らせはロックの外) ---

    def _on_progress(self, stage: str) -> None:
        with self._lock:
            if self._closing:
                return
            if self._run_kind != RUN_AUTO_SEND:
                self._status[0] = stage_label(stage)  # 自動送信の間は「AI に送っています」のまま
        self._changed()

    def _finish(self) -> bool:
        """ロックの中で呼ぶ。門を返し、実行中でなくする。窓を閉じた後なら True(結果は捨てる)。"""
        self._worker = None
        self._session.release(self)
        if self._closing:
            return True
        self._refresh_save()
        return False

    def _on_done(
        self,
        inp: ScreenInput,
        request: CompareRequest,
        outcome: CompareOutcome | MultiCompareOutcome,
        plan: LlmPlan | None,
        kind: str,
        settings: LlmSettings | None = None,
        destination: tuple[str, str] | None = None,
        full_text: bool = False,
        max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS,
        first_request: str | None = None,
    ) -> None:
        with self._lock:
            # 流れの考えは途中の答えを消す前に読む(結果に入れるときだけ残す。ADR-0169 決定4)
            stream = self._stream
            thought = stream.thinking_view() if stream is not None else None
            self._clear_partial()  # 全部届いた答えは結果に入る(途中の答えは消す)
            # 待っている一括比較の答えは、どの比較が終わっても1回で消える(ADR-0170)。確かめるのは、比較だけの回(「比較実行」と
            # 「比較画面で開く」の後の比較)で、持ってきたときと同じ入力を比べたときだけ
            handed, self._handed = self._handed, None
            check = handed is not None and kind in (RUN_COMPARE, RUN_HANDOFF) and is_current(handed[0], inp)
            if not self._finish():
                if kind == RUN_AUTO_SEND and not is_current(inp, self._current_input()):
                    pass  # 送っている間に入力を変えた。届いた答えは捨てる(保存の可否は _finish で見直した)
                elif isinstance(outcome, MultiCompareOutcome):
                    self._first_full_text = {}  # まとめての比較の最初の解析では全文を送らない(ADR-0158 決定9)
                    whole: MultiCompareOutcome | None = None
                    if check:
                        assert handed is not None
                        # ★ 一括比較で送った全体の答えだけを、比べ直した全体の本文を送ったときの語と件数の上限で伏せ直し、
                        # 送った文と同じときだけ付ける(REQ-241 §5。1組の答えは付けない。ADR-0170 決定3)
                        whole = attach_whole_answer(outcome, handed[1].llm)
                    if whole is not None:
                        assert handed is not None
                        outcome = whole
                        # 付けた答えは比較の窓で送った全体の答えと同じに扱う。注記は描く前に入れる(ADR-0170 決定5)
                        self._llm_notes = [compare_handed_answer_note(handed[1].profile[1], handed[1].prompt_name)]
                    self._keep_thinking(_done_thought(thought, outcome.llm), TARGET_ANALYSIS)
                    # 確かめた回は自動送信しない(ADR-0170 決定3)
                    auto_send = kind == RUN_COMPARE and not check
                    self._done_multi(
                        inp,
                        request,
                        outcome,
                        plan,
                        kind,
                        settings,
                        destination,
                        max_total_diffs,
                        auto_send=auto_send,
                        first_request=first_request,
                    )
                    if whole is not None:
                        assert handed is not None
                        # 送り先は一括比較の送り先(全体で1つ。番号 0。ADR-0148 決定7・ADR-0152 決定3)
                        self._sent_profiles = {0: handed[1].profile}
                    elif check:
                        self._late_notice = self._ai_late_notice(COMPARE_HANDED_ANSWER_UNUSED, COMPARE_HANDED_ANSWER_TITLE)
                else:
                    attached: HandedAnswer | None = None
                    if check:
                        assert handed is not None
                        # ★ 比べ直した差分を一括比較で送ったときの語と件数の上限で伏せ直し、送った文と同じときだけ付ける(決定3)
                        if same_sent_body(
                            structured=outcome.structured,
                            raw=outcome.raw,
                            show=outcome.show,
                            inputs=llm_inputs(request, outcome),
                            first=handed[1].llm,
                        ):
                            attached = handed[1]
                            outcome = dataclasses.replace(outcome, llm=attached.llm)
                    self._drop_multi()
                    self._last = (inp, request, outcome)
                    # 承認して送った回で全文も付けたなら、その答えの送った文には確かめた全文が入っている
                    self._first_full_text = {0: True} if kind == RUN_APPROVED and full_text else {}
                    self._remember_destination(kind, destination, 1, [outcome.llm])
                    self._remember_first_request(kind, first_request, outcome.llm)
                    if attached is not None:
                        # 付けた答えは比較の窓で送った答えと同じに扱う。送り先は一括比較の送り先(ADR-0170 決定5・ADR-0148 決定7)
                        self._sent_profiles = {0: attached.profile}
                        self._llm_notes = [compare_handed_answer_note(attached.profile[1], attached.prompt_name)]
                    self._keep_thinking(_done_thought(thought, outcome.llm), TARGET_ANALYSIS)
                    self._show(inp, view_outcome(outcome), outcome.llm)
                    self._refresh_save()  # 区間の欄を結果で埋め直した後の入力で判定する
                    if wants_preview(plan, outcome):
                        assert plan is not None and outcome.llm is not None and destination is not None
                        if self._instruction_too_long(plan, [outcome.llm]):
                            # 打った文と合わせた文が上限を超えた。確認を出さずに知らせる(送らない。REQ-206 §6)
                            body = instruction_too_long(MAX_PROMPT_CHARS)
                            self._late_notice = self._ai_late_notice(body, COMPARE_FOLLOW_UP_NOTICE_TITLE)
                        else:
                            self._preview = (
                                self._next_serial(), plan, inp, request, outcome.llm, destination, max_total_diffs
                            )
                            self._preview_first_request = first_request  # 承認した2回目に運ぶ(ADR-0179 決定1)
                    elif check and attached is None:
                        # 差分が違うので使わなかった。★ 自動送信がオンでも、この比較の後には送らない(ADR-0170 決定3)
                        self._late_notice = self._ai_late_notice(COMPARE_HANDED_ANSWER_UNUSED, COMPARE_HANDED_ANSWER_TITLE)
                    elif kind == RUN_COMPARE and not check:
                        self._auto_send()  # 比較の結果を先に出してから、裏で送る(自動送信の結果からは始めない)
        self._session.gate_changed(self)
        self._changed()

    def _remember_destination(
        self, kind: str, destination: tuple[str, str] | None, count: int, analyses: list[LlmAnalysis | None]
    ) -> None:
        """ロックの中で呼ぶ。新しい結果に入れ替えたときの送り先の記録と、打った文の欄を空にする番号(ADR-0148 決定7・決定6)。

        承認して送った回と自動送信の回だけ、送り先を組ごと(count 組)に覚える。ほかの回(比較だけ・送らない1回目)は記録を消す。
        欄は、承認して送った回で答えが届いた(status=done)ときだけ空にする(「送信しない」・中止・失敗では文字を残す)。
        """
        self._destination_confirm = None
        if kind in (RUN_APPROVED, RUN_AUTO_SEND) and destination is not None:
            self._sent_profiles = {index: destination for index in range(count)}
        else:
            self._sent_profiles = {}
        if kind == RUN_APPROVED and any(llm is not None and llm.status == "done" for llm in analyses):
            self._instruction_clear += 1

    def _remember_first_request(self, kind: str, first_request: str | None, llm: LlmAnalysis | None) -> None:
        """ロックの中で呼ぶ。最初の依頼の文を窓に持つ(ADR-0179 決定1)。

        承認して送った回と自動送信の回で、送って答えが届いた(追加の質問ができる)ときだけ。ほかは消したまま(_start で消した)。
        一括比較から持ってきた答え(比較だけの回)には付けない。
        """
        if kind in (RUN_APPROVED, RUN_AUTO_SEND) and _answered(llm):
            self._first_request = first_request
        else:
            self._first_request = None

    @staticmethod
    def _instruction_too_long(plan: LlmPlan, analyses: list[LlmAnalysis | None]) -> bool:
        """解析の前に打った文を足した送る文が、上限(追加の質問と同じ prompt_too_long)を超えたか。文を足していなければ偽。"""
        if plan.first.extra_instruction is None:
            return False
        return any(llm is not None and isinstance(llm.payload, str) and prompt_too_long(llm.payload) for llm in analyses)

    def _done_multi(
        self,
        inp: ScreenInput,
        request: CompareRequest,
        outcome: MultiCompareOutcome,
        plan: LlmPlan | None,
        kind: str,
        settings: LlmSettings | None,
        destination: tuple[str, str] | None = None,
        max_total_diffs: int = DEFAULT_MAX_TOTAL_DIFFS,
        *,
        auto_send: bool = True,
        first_request: str | None = None,
    ) -> None:
        """ロックの中で呼ぶ。まとめて比べた結果を出す(REQ-122)。

        「AI に送る」の1回目で、全部の組をまとめた送る文ができた(dry_run)なら、1組と同じ送信前の確認(_preview)を1回だけ出す
        (ADR-0152 決定3)。打った文と合わせて長すぎるときは確認を出さずに知らせる(REQ-206 §6)。全体の上限を超えた・送る内容が
        無いときは、一括比較と同じ文で知らせて送らない(理由は全体の AI の欄に残る)。
        auto_send が偽なら、比較だけの回でも自動送信しない(一括比較の答えを確かめた回。ADR-0170 決定3)。
        """
        self._last = None
        llm = outcome.llm
        message = None
        pending = False
        if kind == RUN_PREVIEW and plan is not None and plan.send is not None and llm is not None:
            if llm.reason == "dry_run" and llm.payload is not None:
                if self._instruction_too_long(plan, [llm]):
                    # 打った文と合わせた文が上限を超えた。確認を出さずに知らせる(送らない。REQ-206 §6)
                    self._late_notice = self._ai_late_notice(
                        instruction_too_long(MAX_PROMPT_CHARS), COMPARE_FOLLOW_UP_NOTICE_TITLE
                    )
                else:
                    pending = True
            elif llm.reason == "over_total_limit":
                limits = limit_lines(settings) if settings is not None else []
                message = "\n".join([batch_llm_over_limit(llm.sent_diffs or 0, max_total_diffs), *limits])
            elif llm.reason in ("no_diff", "no_structured_diff", "nothing_to_send"):
                message = batch_llm_nothing_to_send()
        self._multi = (inp, request, outcome)
        mv = multi_view(outcome)
        # 送り先は全体で1つ(番号 0。ADR-0152 決定3)
        self._remember_destination(kind, destination, 1, [llm])
        self._remember_first_request(kind, first_request, llm)  # 全体の答えに1つ(ADR-0179 決定1)
        self._show_multi(inp, mv, keep=kind not in (RUN_COMPARE, RUN_HANDOFF))
        self._refresh_save()
        if pending:
            assert plan is not None and destination is not None
            # 全部の組をまとめた文の確認は1回だけ。「送信する」まで送らない(ADR-0018 決定6)
            self._preview = (self._next_serial(), plan, inp, request, llm, destination, max_total_diffs)
            self._preview_first_request = first_request  # 承認した2回目に運ぶ(ADR-0179 決定1)
        elif message:
            self._late_notice = self._ai_late_notice(message, BATCH_NOT_SENT_TITLE)
        elif kind == RUN_COMPARE and auto_send:
            self._auto_send()  # 比較の結果を先に出してから、裏で送る(自動送信の結果からは始めない)

    def _on_cancelled(self, _exc: CompareCancelled) -> None:
        with self._lock:
            stream = self._stream
            if stream is not None:
                self._keep_stopped_partial(stream)  # ためた文字があれば「中止しました」を付けて残す(保存しない)
            if not self._finish():
                self._status[0] = CANCELLED  # 前の結果は残したまま
        self._session.gate_changed(self)
        self._changed()

    def _on_failed(self, exc: BaseException) -> None:
        with self._lock:
            self._clear_partial()
            self._clear_thinking()  # 失敗は途中の答えも考えも消す(ADR-0169 決定6)
            if not self._finish():
                self._status[0] = ""
                body = f"{type(exc).__name__}: {exc}"
                self._late_notice = {"id": self._next_serial(), **self._notice(body, COMPARE_FAILED_TITLE)}
        self._session.gate_changed(self)
        self._changed()

    # --- 送信前のプレビュー(ADR-0018 決定6) ---

    def _answer_preview(self, form: Any, yes: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(yes, bool):
                raise _BadRequest
            if self._follow_up_preview is not None:
                # 追加の質問で全文を送る前の確認(ADR-0158 決定6)を先に見る
                result, started = self._answer_follow_up_preview(yes)
            else:
                result, started = self._answer_analysis_preview(yes)
        if started:
            self._session.gate_changed(self)  # 門を取った(ロックの外で知らせる)
        return result

    def _answer_analysis_preview(self, yes: bool) -> tuple[dict[str, Any], bool]:
        """ロックの中で呼ぶ。最初の解析の送信前の確認の答え。返すのは (返す値, 始めたか)。"""
        pending, self._preview = self._preview, None  # 答えたら消す(1つのプレビューで送れるのは1回だけ)
        first_request, self._preview_first_request = self._preview_first_request, None  # 確認と組の最初の依頼の文
        if not yes or pending is None or self._busy() or self._closing:
            return self._ok(), False
        _number, plan, inp, request, llm, destination, total = pending
        if not is_current(inp, self._current_input()):
            return self._ok(), False  # 比較したときと入力が違う
        # 承認した内容だけを送る2回目(送る直前に、プレビューと同じ内容かを確かめる)。届いたところから出す(ADR-0157 決定5)。
        # まとめて比べたときも同じ(全部の組をまとめた文を1回だけ送る。全体の上限は確認のときと同じ値。ADR-0152 決定3)
        stream = _AnswerStream(self, TARGET_ANALYSIS)
        sink = _ApiErrorSink(self, TARGET_ANALYSIS)  # API が返したエラーの文(ADR-0168 決定5)
        # 前提の文は送る直前に読み直す。確認の後に書き換えた・消したときは承認した文と違うので送らない(ADR-0172 決定5)
        where = {key: self._where[key] for key in ("env", "platform", "home")}
        approved = dataclasses.replace(
            with_current_premise(approve(plan, llm), **where),
            on_delta=stream,
            on_thinking=stream.thinking,
            on_api_error=sink,
        )
        started = self._start_streaming(
            stream,
            inp,
            request,
            approved,
            None,
            RUN_APPROVED,
            destination=destination,
            max_total_diffs=total,
            sink=sink,
            first_request=first_request,
        )
        return self._ok(), started

    def _answer_follow_up_preview(self, yes: bool) -> tuple[dict[str, Any], bool]:
        """ロックの中で呼ぶ。追加の質問で全文を送る前の確認の答え(ADR-0158 決定6)。答えたら確認(と質問の文)を消す。

        「送信する」で、質問できる状態のままで、結果・組・やり取りが確認を出したときと同じなら、確認に出した文のまま送り、
        その組の全文を確かめた印を付ける(同じやり取りの間は確かめ直さない)。「送信しない」は消すだけ(送らない)。
        返すのは (返す値, 始めたか)。
        """
        pending, self._follow_up_preview = self._follow_up_preview, None
        assert pending is not None
        _number, prompt, question, client, last, pair, epoch, words = pending
        if not yes or self._closing or not self._can_ask():
            return self._ok(), False
        if self._result_key() is not last or self._pair != pair or self._follow_up_epoch != epoch:
            return self._ok(), False  # 確認を出した後に結果・組・やり取りが変わった。送らない
        if not self._send_follow_up(client, prompt, question, last, words):
            return self._ok(), False  # 一括比較が走っている
        self._full_text_confirmed.add(pair)
        return self._ok(), True

    # --- まとめての比較の一覧(REQ-122 §2) ---

    def _select_pair(self, form: Any, index: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            mv = self._mview
            if mv is None or self._closing:
                return self._ok()
            if not _is_index(index, len(mv.entries)):
                raise _BadRequest
            if index != self._pair:
                # AI の答えと追加の質問のやり取りは全体で1つ(ADR-0152 決定3)。行を選び直しても、やり取りと途中の答えは消さない。
                # 待っている全文の確認は前の組の全文なので送らない(全文の確認は組ごと。ADR-0152 決定10)
                self._follow_up_preview = None
                self._pair = index
                self._show_pair()
                # 行に合うタブを開く(ADR-0152 決定8。相手なし・1回だけの行は今のタブのまま)
                tab = pair_tab(mv.entries[index].outcome)
                if tab is not None:
                    self._tab = tab
                    self._tab_revision += 1
            return self._ok()

    # --- 追加の質問(REQ-118 / ADR-0081) ---

    def _follow_up_notice(self, body: str) -> dict[str, Any]:
        return self._ok(notice=self._notice(body, COMPARE_FOLLOW_UP_NOTICE_TITLE))

    def _ask_follow_up(self, form: Any, question: Any, confirm: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(question, str):
                raise _BadRequest
            if confirm is not None and (isinstance(confirm, bool) or not isinstance(confirm, int)):
                raise _BadRequest
            self._follow_up_preview = None  # 待っている全文の確認は、新しい質問で置き換える
            if not self._can_ask():
                return self._ok()
            loaded = self._load_plan()  # 質問のたびに設定を読む(作業フォルダの注記は書き換えない)
            if isinstance(loaded, str):
                return self._ok(notice=self._notice(loaded, COMPARE_LLM_SETTINGS_TITLE))
            plan, _notes, settings = loaded
            if plan.send is None or plan.send.client is None:
                return self._follow_up_notice(follow_up_unavailable(plan.first.unavailable))
            first = self._first_answer()  # まとめて比べたときは、選んでいる組の AI 解析(REQ-122 §4)
            assert first is not None and first.payload is not None and self._result_key() is not None
            # 秘密の語は、最初に送ったときの語と今の設定の語の両方(比較のときと同じ判定。ADR-0081 決定4)
            words = normalize_words([*(first.secret_words or ()), *plan.send.extra_secret_words])
            refused = check_question(question, words)
            if refused is not None:
                return self._follow_up_notice(follow_up_refused(refused, MAX_QUESTION_CHARS))
            confirmed = True
            if self._full_text and self._full_text_state()[0]:
                # 「差分＋全文を送る」(ADR-0158 決定6・7)。最初の解析で全文を送っていれば、その文にもう入っている(足さない)
                pair = self._pair
                if self._first_full_text.get(pair):
                    base = first.payload
                else:
                    section = self._after_sections.get(pair)
                    if section is None:
                        built = self._rebuild_after_text(first, plan.send.extra_secret_words)
                        if built == REBUILD_MISMATCH:
                            return self._follow_up_notice(COMPARE_FULL_TEXT_MISMATCH)
                        if built == REBUILD_TOO_LONG:
                            return self._follow_up_notice(full_text_too_long())
                        assert isinstance(built, AfterText)
                        section = self._after_sections[pair] = built
                    base = with_after_text(first.payload, section)  # 全文の節は最初に送った文の直後
                    confirmed = pair in self._full_text_confirmed
                prompt = build_follow_up(base, self._follow_up_turns(first), question, full_text=True)
            else:
                prompt = build_follow_up(first.payload, self._follow_up_turns(first), question)
            if prompt_too_long(prompt):
                return self._follow_up_notice(follow_up_too_long(MAX_PROMPT_CHARS))  # 古いやり取りは落とさない
            # 保存に送ったときの伏せる語が無い開いた結果は、送る前に1回だけ確かめる(ADR-0179 決定3)。
            # 確かめに答えた番号は、送り先の確かめには使わない(confirm を None に戻す)
            words_confirm, confirm = self._saved_words_check(first, confirm)
            if words_confirm is not None:
                return self._ok(confirm=words_confirm)  # 「はい」で呼び直すまで送らない
            asking_confirm = self._destination_check(destination_of(settings), confirm)
            if asking_confirm is not None:
                return self._ok(confirm=asking_confirm)  # 送り先が変わった。「はい」で呼び直すまで送らない
            client, last = plan.send.client, self._result_key()
            if not confirmed:
                # まだ確かめていない全文は送らずに、送信前の確認を出す(ADR-0081 決定3 の例外。ADR-0158 決定6)。
                # 質問の文は確認を待っている間だけ持つ
                self._follow_up_preview = (
                    self._next_serial(), prompt, question, client, last, self._pair, self._follow_up_epoch, words
                )
                return self._ok()
            if not self._send_follow_up(client, prompt, question, last, words):
                return self._ok()  # 一括比較が走っている
            result = self._ok()
        self._session.gate_changed(self)
        return result

    def _send_follow_up(
        self, client: LLMClient, prompt: str, question: str, last: object, words: tuple[str, ...] = ()
    ) -> bool:
        """ロックの中で呼ぶ。門を取れたら、質問を送るスレッドを始めて True。送る文と質問はスレッドの関数のローカル変数だけ。

        words は質問を確かめた秘密の語(API が返したエラーの文をもう一度掃除するのに使う。ADR-0168 決定3)。
        """
        if not self._session.acquire(self):
            return False  # 一括比較が走っている
        self._clear_partial()  # 次の送信では前の途中の答えを消す
        self._clear_thinking()  # 前の答えの考えも消す(ADR-0169 決定4)
        self._clear_api_error()  # 前の送信のエラーの文も消す
        stream = _AnswerStream(self, TARGET_FOLLOW_UP)

        def run(asking: _Asking) -> None:
            self._ask_in_thread(asking, client, prompt, question, last, stream, words)

        self._asking = _Asking(run, self._follow_up_epoch, self._status[0], self._pair)
        stream.asking = self._asking
        self._stream = stream
        self._late_notice = None
        self._status[0] = COMPARE_FOLLOW_UP_ASKING
        self._asking.start()
        return True

    def _rebuild_after_text(self, first: LlmAnalysis, extra_words: tuple[str, ...]) -> AfterText | str:
        """ロックの中で呼ぶ。選んでいる組の変更後の全文を、最初の送信と同じ入力・同じ順で作り直す(ADR-0158 決定7)。

        まとめて比べたときは、全部の組をまとめた本文を新しい Masker で同じ順に作り直してから、選んでいる組の全文を作る
        (記号の番号を最初の送信とそろえる。ADR-0152 決定10)。
        """
        if self._last is not None:
            _inp, request, outcome = self._last
        else:
            assert self._multi is not None
            positions, sections, unchanged = whole_sections(self._multi[2])
            if self._pair not in positions:
                return REBUILD_MISMATCH
            return rebuild_whole_after_text(
                sections=sections,
                unchanged=unchanged,
                selected=positions.index(self._pair),
                first=first,
                extra_words=extra_words,
            )
        return rebuild_after_text(
            structured=outcome.structured,
            raw=outcome.raw,
            show=outcome.show,
            inputs=llm_inputs(request, outcome),
            first=first,
            extra_words=extra_words,
        )

    def _set_full_text(self, form: Any, checked: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(checked, bool):
                raise _BadRequest
            if checked and not self._full_text_state()[0]:
                return self._ok()  # 押せないときは入れない(画面は Python の値で描き直す)
            self._full_text = checked
            return self._ok()

    def _destination_check(self, current: tuple[str, str], confirm: int | None) -> dict[str, Any] | None:
        """ロックの中で呼ぶ。追加の質問の送り先の確かめ(ADR-0148 決定7)。送ってよければ None、確かめるならその内容。

        最初の送信(と「はい」で送った後)の送り先の id と、今の使用中の id を比べる。同じなら聞かない。
        違えば番号を付けた確かめを返す。画面が同じ番号を返し、今の使用中もそのときと同じときだけ送ってよい
        (そのとき、この組の記録を今の送り先に替える。同じ送り先へは、このやり取りの間は聞かない)。
        番号が違う・古いときは、もう一度確かめる。記録が無いときは聞かない(答えが届いた結果には、いつも記録がある)。
        開いた保存の結果(ADR-0179 決定3)では、保存から戻した記録を名前で比べ、記録が無いときも確かめる(compare_saved_no_profile)。
        """
        # 送り先の記録は1つ(番号 0)。1組の比較も、全部の組をまとめて送ったまとめての比較も同じ(ADR-0152 決定3)
        key = 0
        sent = self._sent_profiles.get(key)
        if sent is None:
            if self._opened_saved is None:
                return None
            # 開いた保存の結果で、保存に送り先の記録が無い。今の使用中と違うときと同じに確かめる(ADR-0179 決定3)
            body = compare_saved_no_profile(current[1])
        elif self._sent_name_only and key == 0:
            # 保存から戻した記録は名前だけなので、名前で比べる(ADR-0179 の残っている穴)
            if sent[1] == current[1]:
                return None
            body = follow_up_destination_changed(sent[1], current[1])
        elif sent[0] == current[0]:
            return None
        else:
            body = follow_up_destination_changed(sent[1], current[1])
        pending = self._destination_confirm
        if confirm is not None and pending == (confirm, current[0], key):
            self._destination_confirm = None
            self._sent_profiles[key] = current
            self._sent_name_only = False  # 今の送り先(id と名前)に替えた
            return None
        number = self._next_serial()
        self._destination_confirm = (number, current[0], key)
        return {
            "id": number,
            "title": COMPARE_FOLLOW_UP_NOTICE_TITLE,
            "body": body,
            "yes": DIALOG_YES,
            "no": DIALOG_NO,
        }

    def _saved_words_check(self, first: LlmAnalysis, confirm: int | None) -> tuple[dict[str, Any] | None, int | None]:
        """ロックの中で呼ぶ。開いた保存の結果で、保存に送ったときの伏せる語(secret_words)が無いときの送る前の確認(ADR-0179 決定3)。

        返すのは (確認の内容か None, 送り先の確かめに渡す confirm)。開いた結果でない・語がある・もう確かめたときは (None, confirm)。
        画面が同じ番号で呼び直したら確かめた印を付け (None, None)(確かめは開いた結果1つにつき1回)。ほかは番号を付けた確認を返す。
        """
        if self._opened_saved is None or first.secret_words or self._saved_words_confirmed:
            return None, confirm
        if confirm is not None and confirm == self._words_confirm:
            self._words_confirm = None
            self._saved_words_confirmed = True
            return None, None
        number = self._next_serial()
        self._words_confirm = number
        return {
            "id": number,
            "title": COMPARE_FOLLOW_UP_NOTICE_TITLE,
            "body": COMPARE_SAVED_NO_WORDS,
            "yes": DIALOG_YES,
            "no": DIALOG_NO,
        }, None

    # --- AI の面の切り替え(REQ-206 §5 / ADR-0148 決定4) ---

    def _use_profile(self, form: Any, index: Any, label: Any, which: str) -> dict[str, Any]:
        """「AI プロファイル」(which=llm)か「プロンプト」(which=prompt)の使用中を変える。設定の窓と同じ保存先に書く。

        比較中・質問中・閉じている途中は断る(何も書かない)。Python が読み直した一覧で、位置と名前が合うときだけ書く
        (合わなければ一覧が変わったので、書かずに描き直させる)。失敗したら知らせる(選択は使用中の位置に戻る)。
        結果・答え・やり取りには触らない。書いたら、設定の窓が開いていれば読み直させる(ロックの外)。
        """
        written = False
        with self._lock:
            self._take(form)
            if isinstance(index, bool) or not isinstance(index, int) or not isinstance(label, str):
                raise _BadRequest
            if self._busy() or self._closing:
                return self._ok()
            where = self._profile_where()
            choices: ProfileChoices = llm_choices(**where) if which == "llm" else prompt_choices(**where)
            if not _is_index(index, len(choices.ids)) or choices.names[index] != label:
                return self._ok()
            if index == choices.active:
                return self._ok()  # 使用中のまま(書かない)
            if which == "llm":
                action = use_selected(choices.ids[index], **where)
                title = COMPARE_AI_PROFILE_FAILED_TITLE
            else:
                action = use_prompt(choices.ids[index], **where)
                title = COMPARE_PROMPT_FAILED_TITLE
            written = action.ok
            result = self._ok() if action.ok else self._ok(notice=self._notice(action.message, title))
        if written:
            self._session.settings_changed()
        return result

    def _ask_in_thread(
        self,
        asking: _Asking,
        client: LLMClient,
        prompt: str,
        question: str,
        last: object,
        stream: _AnswerStream | None = None,
        words: tuple[str, ...] = (),
    ) -> None:
        """ワーカーのスレッドで1回だけ送る。門はどの終わり方でも返す。失敗は理由の種類と型の名前・状態コードだけにする。

        stream があれば届いたところから出す(ADR-0157)。StreamStopped(中止・閉じる)は失敗にせず、中止として扱う。
        API が返したエラーの文と秒は、words でもう一度掃除してから AI の面の折りたたみと知らせに使う(ADR-0168 決定5)。
        """
        answer: str | None = None
        failure: tuple[str, str, int | None] | None = None
        stopped = False
        raw_api_error: object = None
        shown: ApiErrorText | None = None
        try:
            try:
                reply = ask_with_deltas(client, prompt, stream, stream.thinking if stream is not None else None)
            except StreamStopped:
                stopped = True  # 中止した。途中の文字はやり取りに入れない
            except LLMConnectionError as exc:
                failure = ("connection_error", type(exc).__name__, None)
            except LLMResponseError as exc:
                failure = ("api_error", type(exc).__name__, exc.status_code)
                raw_api_error = getattr(exc, "api_error", None)
            except Exception as exc:  # noqa: BLE001 - 接続の実装の想定外の例外も、文面を出さない知らせにする
                failure = ("api_error", type(exc).__name__, None)
            else:
                if isinstance(reply, str):
                    answer = reply
                else:
                    failure = ("api_error", "InvalidAnswer", None)
            if isinstance(raw_api_error, ApiErrorText):
                shown = for_display(raw_api_error, words)  # except の外で掃除する
        finally:
            with self._lock:
                if self._asking is asking:
                    self._asking = None
                self._session.release(self)
                if not self._closing:
                    # 中止した・やり取りを消した(比較し直した・入力を変えた)・結果が変わった後に届いた答えは捨てる。
                    # まとめての比較のやり取りは全体で1つなので、行を選び直した後に届いた答えも残す(ADR-0152 決定3)。
                    # 開いた保存の結果は入力によらない(同じ開いた結果のままなら残す。REQ-242 §4)
                    opened = self._opened_saved is not None
                    keep = (
                        not asking.cancelled
                        and asking.epoch == self._follow_up_epoch
                        and self._result_key() is last
                        and (opened or self._multi is not None or asking.pair == self._pair)
                        and (opened or self._input_unchanged())
                    )
                    self._status[0] = CANCELLED if asking.cancelled or stopped else asking.status
                    # 流れの考えは途中の答えを消す前に読む(やり取りに入れるときだけ残す。ADR-0169 決定4)
                    thought = stream.thinking_view() if stream is not None and self._stream is stream else None
                    if stream is not None:
                        if (asking.cancelled or stopped) and self._stream is stream:
                            self._keep_stopped_partial(stream)  # ためた文字か考えがあれば注記を付けて残す(保存しない)
                        elif self._stream is stream:
                            self._clear_partial()  # 全部届いた答えはやり取りに入る。失敗も消す
                    if keep and answer is not None:
                        # 考えは画面の状態にだけ持つ(_turns には質問と答えだけ。ADR-0169 決定5)
                        self._keep_thinking(thought, TARGET_FOLLOW_UP)
                        self._turns.append((question, answer))
                        self._follow_up_revision += 1
                        self._clear_api_error()  # 答えがやり取りに入った
                    elif keep and failure is not None:
                        self._set_api_error(shown, TARGET_FOLLOW_UP)
                        body = follow_up_failed(
                            *failure,
                            retry_after=shown.retry_after if shown is not None else None,
                            has_api_text=shown is not None and bool(shown.message),
                        )
                        self._late_notice = self._ai_late_notice(body, COMPARE_FOLLOW_UP_NOTICE_TITLE)
                    self._refresh_save()
            self._session.gate_changed(self)
            self._changed()

    # --- 結果 ---

    def _result(self) -> dict[str, Any]:
        with self._lock:
            view = self._outcome
            if view is None:
                # 結果が無いとき。まとめての比較で相手なしの行を選んだときは、空の表とその区間の説明(REQ-122 §2)。
                # AI の欄は全体の答え(行を選び直しても変わらない。ADR-0152 決定3)。結果が無い窓では _llm_text も None
                result: dict[str, Any] = {
                    "revision": self._result_revision,
                    "titles": dict(EMPTY_TITLES),
                    "warnings": None,
                    "structured_note": self._unpaired_note,
                    "structured_detail": None,
                    "structured_rows": [],
                    "raw_note": None,
                    "raw_rows": [],
                    "unparsed_note": None,
                    "unparsed_rows": [],
                    "llm": self._llm_text,
                    "llm_blocks": answer_blocks(self._llm_text),
                    "llm_copy": self._llm_copy,
                    **_filters(None),
                    # 結果が無いときはどのタブも押せる(ADR-0164 決定1)
                    **_tab_states(None),
                    # 違う文字の印(REQ-226)。結果が無いときは空
                    "structured_marks": [],
                    "raw_marks": [],
                    # 単純比較の設定で脇に置いた行(ADR-0166 決定11-2)。結果が無いときは無し
                    "raw_set_aside": None,
                    "raw_set_aside_rows": [],
                    "raw_mark_labels": dict(RAW_MARK_LABEL),
                    "raw_move_labels": [],
                }
            else:
                structured_ranges, raw_ranges = self._current_view_marks(view)
                result = {
                    "revision": self._result_revision,
                    "titles": {key: view.titles[key] for key in TAB_ORDER},
                    # 機種違いで失敗したときは短い文だけを出し、全文と警告は「詳しい内容」に入れる(ADR-0151 決定8)
                    "warnings": "\n".join(compare_warning_line(w) for w in (view.warnings if view.warnings_web is None else view.warnings_web)) or None,
                    "structured_note": view.structured_note_web,
                    "structured_detail": view.structured_detail_web,
                    "structured_rows": [
                        {
                            "op": row.op,
                            "category": row.category,
                            "target": row.target,
                            "item": row.item,
                            "before": row.before,
                            "after": row.after,
                        }
                        for row in view.structured_rows
                    ],
                    "raw_note": view.raw_note,
                    "raw_rows": [
                        {
                            "mark": row.mark,
                            "before_no": row.before_no,
                            "before_text": row.before_text,
                            "after_no": row.after_no,
                            "after_text": row.after_text,
                            "is_diff": row.is_diff,
                            # 移動の番号(REQ-228。移動元・移動先の行だけ。ほかは null)
                            "move": row.move,
                        }
                        for row in view.raw_rows
                    ],
                    "unparsed_note": view.unparsed_note,
                    "unparsed_rows": [{"op": row.op, "line": row.line, "parents": row.parents} for row in view.unparsed_rows],
                    # llm は知らせの行と AI が返したままの答え(頭の行は除く。ADR-0148 決定8)。llm_blocks はそれを読み取った
                    # ブロック(画面は textContent だけで描く。REQ-204 §3・ADR-0146 決定4)。llm_copy はコピーの文(<red> を外した答え)
                    "llm": self._llm_text,
                    "llm_blocks": answer_blocks(self._llm_text),
                    "llm_copy": self._llm_copy,
                    # 差分の要約と絞り込み(ADR-0155 / REQ-215)。既存のキーの形は変えず、別のキーで渡す
                    **_filters(view, self._raw_context),
                    # 構造化比較が動いていない組で押せなくするタブ(ADR-0164 決定1・2)。別のキーで渡す
                    **_tab_states(view.structured_off),
                    # 変更の行で違う文字の範囲(ADR-0165 / REQ-226)。行の番号は structured_rows・raw_rows の番号。
                    # 位置は JavaScript の文字の位置。既存のキーの形は変えず、別のキーで渡す
                    "structured_marks": structured_ranges,
                    "raw_marks": raw_ranges,
                    # 単純比較の設定で無視した行・同じとみなした行の数の文と、その行の番号(raw_rows の番号。ADR-0166 決定11-2)。
                    # 印の文(x は「無視」、= は「同じ」)。既存のキーと raw_rows の形は変えず、別のキーで渡す
                    "raw_set_aside": view.raw_set_aside,
                    "raw_set_aside_rows": raw_set_aside_rows(view.raw_rows),
                    "raw_mark_labels": dict(RAW_MARK_LABEL),
                    # 移動元・移動先の行の印の文(「移動元 1」「移動先 1」。REQ-228 §8.2)。[{index, text}](index は raw_rows の番号)
                    "raw_move_labels": raw_move_labels(view.raw_rows),
                }
            # キャラクターの絵の場面(ADR-0174 決定2)。差分なしの絵は、比べ終わって差分が 0 件のとき。
            # 空の窓の絵は、この窓でまだ一度も比べていないとき。既存のキーの形は変えず、別のキーで渡す
            result["no_diff"] = view is not None and view.status == "no_difference"
            result["never_compared"] = not self._compared_once
            return self._ok(result=result)

    def _ai_result(self) -> dict[str, Any]:
        """AI の窓の ai_result()(ADR-0160 決定3 / REQ-220 §6.3)。結果の番号と AI の答えの部分だけ。結果の行は入れない。

        まとめての比較で相手なしの行を選んでいるときも、全体の答えを出す(result() と同じ。ADR-0152 決定3)。
        """
        with self._lock:
            shown = self._outcome is not None or self._mview is not None
            result = {
                "revision": self._result_revision,
                "llm": self._llm_text if shown else None,
                "llm_blocks": answer_blocks(self._llm_text) if shown else None,
                "llm_copy": self._llm_copy if shown else None,
            }
            return self._ok(result=result)

    def _all(self) -> dict[str, Any]:
        """「全部」のタブの行(ADR-0152 決定7)。読むだけ。文字は画面が textContent だけで描く。"""
        with self._lock:
            rows = [asdict(row) for row in self._current_all_rows()]
            # marks は変更の行で違う文字の範囲(REQ-226)。rows の形は変えず、別のキーで渡す
            return self._ok(all={"revision": self._all_revision, "rows": rows, "marks": self._current_all_marks()})

    def _jump(self, current: Any, step: Any) -> dict[str, Any]:
        with self._lock:
            rows = self._outcome.raw_rows if self._outcome is not None else []
            if isinstance(current, bool) or not isinstance(current, int) or not -1 <= current < len(rows):
                raise _BadRequest
            if isinstance(step, bool) or step not in (1, -1):
                raise _BadRequest
            return self._ok(row=find_diff(rows, current, step))

    # --- 保存(ADR-0017。自動保存はしない) ---

    def _conversation(self) -> Conversation:
        """ロックの中で呼ぶ。保存に入れる AI とのやり取り(ADR-0179 決定2)。最初の依頼の文・送り先の名前・やり取り。

        送り先は名前だけ(接続先・キー・CLI の場所は入れない)。考えている内容は入れない。書くかは storage の決まり。
        """
        sent = self._sent_profiles.get(0)
        return Conversation(
            first_request=self._first_request,
            profile=sent[1] if sent is not None else None,
            turns=tuple(self._turns),
        )

    def _save(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            multi = self._multi
            opened = self._opened_saved
        if opened is not None:
            return self._resave(opened)
        if multi is not None:
            return self._save_multi(multi)
        with self._lock:
            last = self._last
            if last is None or self._busy() or self._closing:
                return self._ok()
            inp, request, outcome = last
            if not is_current(inp, self._current_input()):
                # ボタンは止めてあるが、念のため保存の直前にも確かめる(古い結果を新しい入力の結果として残さない)
                self._refresh_save()
                return self._ok()
            # やり取りも入れる(AI の答えがあり、どれかに値があるときだけ書く。ADR-0179 決定2)
            record = build_save_record(inp, request, outcome, conversation=self._conversation())
            filename = suggested_filename(record)
        # 上書きの確認は付けない。storage が既存のファイルへの上書きを拒否する(ADR-0017 決定3)
        target = self._choose_file(DIALOG_SAVE, filename, SAVE_FILE_TYPES)  # 開いている間はロックを持たない
        with self._lock:
            if target is None:
                return self._ok()  # 取り消した
            if self._last is not last or self._busy() or not is_current(inp, self._current_input()):
                self._refresh_save()  # 選んでいる間に結果か入力が変わった
                return self._ok()
            try:
                saved = save_result(record, target)
            except StorageError as exc:
                return self._ok(notice=self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
            self._status[0] = saved
            return self._ok()

    def _save_multi(self, multi: tuple[ScreenInput, CompareRequest, MultiCompareOutcome]) -> dict[str, Any]:
        """まとめて比べた結果の保存(REQ-122 §3)。JSON Lines(1行が1組)。相手なしは入れず、あれば知らせる。上書きしない。"""
        inp, _request, outcome = multi
        with self._lock:
            if self._busy() or self._closing:
                return self._ok()
            if not is_current(inp, self._current_input()):
                self._refresh_save()  # 古い結果を新しい入力の結果として残さない
                return self._ok()
            if not outcome.pairs:
                return self._ok(notice=self._notice(COMPARE_MULTI_NOTHING_TO_SAVE, COMPARE_SAVE_FAILED_TITLE))
            # やり取りは全体の答えを入れた行にだけ入れる(ADR-0179 決定2)
            records = pair_records(outcome, inp, conversation=self._conversation())
            filename = suggested_multi_filename(records)
        target = self._choose_file(DIALOG_SAVE, filename, MULTI_SAVE_FILE_TYPES)  # 開いている間はロックを持たない
        with self._lock:
            if target is None:
                return self._ok()  # 取り消した
            if self._multi is not multi or self._busy() or not is_current(inp, self._current_input()):
                self._refresh_save()  # 選んでいる間に結果か入力が変わった
                return self._ok()
            try:
                written = save_multi(records, target)
            except ResultsWriteError as exc:
                return self._ok(notice=self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
            self._status[0] = saved_notice(written)
            if outcome.unpaired:
                # ファイルに残らない情報なので、保存した人がその場で気づけるように知らせる(決定者の判断。REQ-122)
                return self._ok(notice=self._notice(multi_unpaired_not_saved(len(outcome.unpaired)), BATCH_SAVED_TITLE))
            return self._ok()

    def _resave(self, opened: _OpenedSaved) -> dict[str, Any]:
        """開いた保存の結果を、やり取りを入れて新しいファイルに保存し直す(REQ-242 §4。開いたファイルは上書きしない)。

        保存日時は今にし(ダイアログの既定の名前もその日時)、ほかの項目は開いた保存のまま(storage.resaved_record)。
        .jsonl はどの行も同じ保存日時にし、やり取りは全体の答えの行にだけ入れる。押せるのは開いた結果に AI の答えがあるときだけ。
        """
        with self._lock:
            if self._opened_saved is not opened or not self._can_resave():
                return self._ok()
            saved_at = datetime.now(timezone.utc).astimezone()
            stamp = {"saved_at": saved_at.isoformat(timespec="seconds")}
            if opened.multi:
                filename, file_types = suggested_multi_filename([stamp]), MULTI_SAVE_FILE_TYPES
            else:
                filename, file_types = suggested_filename(stamp), SAVE_FILE_TYPES
        target = self._choose_file(DIALOG_SAVE, filename, file_types)  # 開いている間はロックを持たない
        with self._lock:
            if target is None:
                return self._ok()  # 取り消した
            if self._opened_saved is not opened or not self._can_resave():
                return self._ok()  # 選んでいる間に開いた結果が変わった・質問を始めた
            conversation = self._conversation()
            if opened.multi:
                records = resaved_lines(opened.raw, opened.whole, conversation, saved_at)
                try:
                    written = save_multi(records, target)
                except ResultsWriteError as exc:
                    return self._ok(notice=self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
                self._status[0] = saved_notice(written)
                return self._ok()
            record = resaved_record(opened.raw, conversation, saved_at)
            try:
                saved = save_result(record, target)
            except StorageError as exc:
                return self._ok(notice=self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
            self._status[0] = saved
            return self._ok()

    # --- HTML と CSV の出力(ADR-0156 / REQ-216。上書きしない。秘密の行は必ず伏せる) ---

    def _export_ready(self, inp: ScreenInput | None) -> bool:
        """ロックの中で呼ぶ。出力してよいか(押せる状態で、閉じている途中でなく、入力が比較したときと同じ)。"""
        return self._can_export() and not self._closing and inp is not None and is_current(inp, self._current_input())

    def _export_result(self, form: Any, kind: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if not isinstance(kind, str) or kind not in EXPORT_KINDS:
                raise _BadRequest
            if kind == EXPORT_PAIRS and self._multi is None:
                raise _BadRequest  # 組の一覧はまとめて比べたときだけ(ADR-0156 決定1)
            key, inp = self._result_key(), self._last_input()
            if key is None or not self._export_ready(inp):
                return self._ok()
        # 秘密の語は今の設定の語と送った AI 解析の語。設定が読めなければ、語が足りないまま出さない(ADR-0156 決定2)
        try:
            settings = load_settings(**self._where, read_prompt=False)
        except SettingsError as exc:
            with self._lock:
                return self._ok(notice=self._notice(export_settings_unreadable(str(exc)), COMPARE_SAVE_FAILED_TITLE))
        saved_at = datetime.now(timezone.utc).astimezone()
        filename = export_filename("compare", kind, saved_at)  # ホスト名は入れない
        # 上書きの確認は付けない。書き出しが既存のファイルを拒否する(ADR-0017 決定3)
        target = self._choose_file(DIALOG_SAVE, filename, EXPORT_FILE_TYPES[kind])  # 開いている間はロックを持たない
        with self._lock:
            if target is None:
                return self._ok()  # 取り消した
            if self._result_key() is not key or not self._export_ready(inp):
                self._refresh_save()  # 選んでいる間に結果か入力が変わった
                return self._ok()
            assert inp is not None
            if self._last is not None:
                analyses = [self._last[2].llm]
                # 最初の依頼の文も AI の答えの前に出す(ADR-0179 決定1)
                build = partial(
                    compare_report, inp, self._last[2], turns=list(self._turns), first_request=self._first_request
                )
            else:
                assert self._multi is not None
                multi = self._multi[2]
                analyses = [*(pair.outcome.llm for pair in multi.pairs), multi.llm]
                # やり取りは全体で1つ。全体の答えを入れた組(本文の最初の組)の行に付ける(ADR-0152 決定7・決定10)
                holder = whole_holder(multi)
                turns = {holder: list(self._turns)} if holder is not None else {}
                first_requests = {holder: self._first_request} if holder is not None else {}
                build = partial(multi_report, inp, multi, turns=turns, first_requests=first_requests)
        words = export_words(settings.extra_secret_words, analyses)
        try:
            # 大きいときも画面を止めないよう、組み立てと書き出しはロックの外で行う
            data = export_bytes(build(words=words), kind, saved_at)
        except RedactError:
            with self._lock:
                return self._ok(notice=self._notice(EXPORT_REDACT_FAILED, COMPARE_SAVE_FAILED_TITLE))  # 行の中身は入れない
        try:
            written = write_new_bytes(data, target)
        except ResultsWriteError as exc:
            with self._lock:
                return self._ok(notice=self._notice(str(exc), COMPARE_SAVE_FAILED_TITLE))
        with self._lock:
            self._status[0] = export_saved(written)
            return self._ok()

    # --- 保存した結果を開く(REQ-120 / ADR-0083。読むだけ) ---

    def _open_result(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            if self._busy() or self._closing:
                return self._ok()
        path = self._choose_file(DIALOG_OPEN, "", OPEN_RESULT_FILE_TYPES)  # 開いている間はロックを持たない
        if path is None:
            with self._lock:
                return self._ok()  # 取り消した
        multi = path.suffix.casefold() == JSONL_SUFFIX  # まとめて比べた結果と一括比較の結果(1行が1組。REQ-122 §3)
        try:
            # 読む間はロックを持たない。読み込んだ JSON も持つ(保存し直すため。REQ-242 §4)
            saved: _OpenedSaved
            if multi:
                raw_lines, records = read_saved_lines_raw(path)
                opened = opened_multi(records)
                saved = _OpenedSaved(raw_lines, tuple(records), opened_answer_index(records), True)
            else:
                raw, record = read_saved_raw(path)
                opened = opened_view(record)
                saved = _OpenedSaved(raw, (record,), 0, False)
        except SavedResultError as exc:
            with self._lock:
                return self._ok(notice=self._notice(str(exc), COMPARE_OPEN_FAILED_TITLE))  # 画面の状態は変えない
        with self._lock:
            if self._busy() or self._closing:
                return self._ok()  # 選んでいる間に比較が始まった
            self._clear_result()  # 今の結果・送信前のプレビュー・追加の質問を消す
            self._mark_compared()
            if isinstance(opened[0], MultiView):
                self._opened = opened[1]
                self._show_multi(None, opened[0])
            else:
                self._show_opened(opened[0], opened[1])
            self._restore_conversation(saved)
            return self._ok()

    def _restore_conversation(self, saved: _OpenedSaved) -> None:
        """ロックの中で呼ぶ。開いた保存の中身を持ち、送って届いた AI の答えがあれば、保存したやり取り・最初の依頼の文・
        送り先の記録(名前だけ。名前で比べる)を戻す(REQ-242 §4)。送り先の名前が保存に無ければ記録しない。
        保存の答えは送らない(続けて質問したときだけ送る)。
        """
        self._opened_saved = saved
        if self._first_answer() is None:
            return
        conversation = saved.conversation
        if conversation is not None:
            self._turns = list(conversation.turns)
            self._first_request = conversation.first_request
            if conversation.profile is not None:
                self._sent_profiles = {0: ("", conversation.profile)}
                self._sent_name_only = True
        self._follow_up_revision += 1  # やり取りの欄を描き直させる

    def _close_result(self, form: Any) -> dict[str, Any]:
        with self._lock:
            self._take(form)
            self._leave_opened()  # 入力は残す。自動の比較は始めない
            return self._ok()

    # --- ほかの窓・閉じる ---

    def _open_batch(self) -> dict[str, Any]:
        self._session.open_batch()  # 窓を作る間はロックを持たない
        with self._lock:
            return self._ok()

    def _open_settings(self, tab: Any) -> dict[str, Any]:
        if tab not in SETTINGS_TABS:
            raise _BadRequest
        # 窓を作る間はロックを持たない。tab を省いたときは今と同じく引数なしで呼ぶ
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

    # --- AI の窓(ADR-0160 / REQ-220。窓は WebSession が開き、ここへ知らせる) ---

    def _open_ai_pane(self) -> dict[str, Any]:
        self._session.open_ai_pane()  # 窓を作る間はロックを持たない
        with self._lock:
            return self._ok()

    def _state_for(self, pane: str) -> dict[str, Any]:
        """pane の窓向けの今の状態(AI の窓の state()。読むだけ)。"""
        with self._lock:
            return {"ok": True, "view": self._render(pane)}

    def _form_copy(self) -> dict[str, Any]:
        """AI の窓からの操作に渡す form(比較の窓から受け取った最後の値。AI の窓に比較の設定の欄は無い。ADR-0160 決定3)。"""
        with self._lock:
            return dict(self._form)

    def _ai_attached(self, window: Any) -> None:
        """AI の窓を開いた(WebSession.open_ai_pane がロックの外で呼ぶ)。比較の窓の右の面を隠させる。

        比較の窓に出した AI の知らせが残っていれば消す(新しい窓でもう1回出さない)。
        """
        with self._lock:
            self._ai_window = window
            self._drop_ai_notice()
            # 開き直した AI の窓は、キャラクターの設定を読み直す(比較の窓の値は変えない。ADR-0174 決定1)
            self._characters.pop(PANE_AI, None)
        self._changed()

    def _ai_detached(self) -> None:
        """AI の窓が閉じた(WebSession がロックの外で呼ぶ)。比較の窓の右の面に戻す(ADR-0160 決定5)。

        ★ 待っている送信前の確認(全文の確認・まとめて比べたときの全体の確認も)は「送らない」と答えたのと同じにする。
        送り先の確認も消す。送っている途中なら止めない(答えは右の面に出る)。答えとやり取りは残す。
        AI の窓に出した知らせが残っていれば消す(比較の窓でもう1回出さない)。
        """
        with self._lock:
            self._ai_window = None
            self._preview = None
            self._follow_up_preview = None  # 質問の文を持つ確認も消す(ADR-0158 決定6)
            self._destination_confirm = None
            self._words_confirm = None  # 伏せる語の確認も消す(ADR-0179 決定3)
            self._drop_ai_notice()
            closing = self._closing
        if not closing:
            self._changed()

    def _drop_ai_notice(self) -> None:
        """ロックの中で呼ぶ。AI の知らせ(印の付いたもの)が残っていれば消す(窓を移るときに2回出さない)。"""
        if self._late_notice is not None and self._late_notice.get(NOTICE_PANE) == PANE_AI:
            self._late_notice = None

    def _load_handoff(self, handoff: CompareHandoff, answer: HandedAnswer | None = None) -> bool:
        """一括比較の「比較画面で開く」(WebSession.handoff がロックの外で呼ぶ。画面からは呼べない)。

        wx の load_handoff と同じ: 実行中なら何もしない。読めなければ「読み込めません」の知らせ。入力欄と設定だけを埋め、
        区間は自動にする(AI のチェックボックスは無い。ADR-0080 決定1)。表示中の結果には触らない(入力が変わるので、保存は比較し直すまで止まる。ADR-0029 決定8)。
        ただし開いた保存の結果の表示は、ほかの新しい入力と同じく消す(REQ-120)。
        answer はその組の一括比較の答え(ADR-0170)。あれば待っている答えにして、比較は答えを持ってきたときだけ _begin_handoff で始める
        (auto_compare は読まない。決定2)。答えは比べ終わったときに、差分の本文が送ったときと同じなら付ける(決定3)。
        答えが無ければ今のまま比較を始めない(ADR-0076 決定2)。
        比較の窓を前に出すなら True(埋めたか、知らせを出した)。
        """
        with self._lock:
            if self._busy() or self._closing:
                return False  # 待っている答えも変えない
            self._handed = None  # 前に持ってきた答えは、読めない知らせの道でも捨てる
        try:
            sources = {"before": read_file(handoff.before), "after": read_file(handoff.after)}  # 読む間はロックを持たない
        except InputReadError as exc:
            with self._lock:
                self._late_notice = {"id": self._next_serial(), **self._notice(str(exc), COMPARE_READ_FAILED_TITLE)}
            self._changed()
            return True
        started = False
        with self._lock:
            if self._busy() or self._closing:
                return False
            values = dict(self._form)
            values["kind"] = handoff.kind
            self._kind_shown = handoff.kind
            names = [value for value, _label in WEB_PLATFORM_CHOICES]
            if handoff.kind == "config":
                # auto は自動(0)、空は指定なし。選択肢に無い名前は指定なしにする
                values["config_platform"] = names.index(handoff.platform) if handoff.platform in names else names.index("")
            elif handoff.kind == "auto" and handoff.platform in ("", AUTO_PLATFORM):
                values["config_platform"] = names.index(AUTO_PLATFORM)
            elif handoff.kind == "auto" and handoff.platform in names:
                values["config_platform"] = names.index(handoff.platform)
            else:
                values["platform"] = handoff.platform
                values["command"] = handoff.show_command
            values["mode"] = handoff.mode
            self._form = values
            # 単純比較の設定は組の設定にする(既定のファイルは読まない。REQ-228 §7.2)。前後の行数は今のまま
            self._raw = RawForm(handoff.raw_options, [], self._raw_context)
            self._raw_values = raw_form_values(handoff.raw_options, self._raw_context)
            self._raw_fill += 1
            self._leave_opened()  # 開いた保存の結果の表示は消す(REQ-120)
            # 一括比較から受け取った(空の窓の絵は隠す。ADR-0174 決定2)。比較を始めないときも画面へ伝える
            self._mark_compared(tell=True)
            for side in SIDES:
                self._set_source(side, sources[side])  # 区間は自動に戻し、欄を入れ直させ、保存を見直す
            inp = self._current_input()
            if answer is not None and inp is not None:
                # 欄を埋めた後の入力と一緒に待たせる(_set_source が捨てるので、その後で入れる)
                self._handed = (inp, answer)
                started = self._begin_handoff()
        if started:
            self._session.gate_changed(self)
        self._changed()
        return True

    def _begin_handoff(self) -> bool:
        """ロックの中で呼ぶ。一括比較の答えを持ってきた「比較画面で開く」の後の比較(ADR-0170 決定2)。始めたら True。

        「比較実行」を押せないとき(一括比較が門を持っている など)は始めない。待っている答えは残し、次に「比較実行」を押した
        比較で確かめる。auto_compare の設定は読まない。AI は呼ばない。
        """
        if not self._can_run():
            return False
        inp = self._current_input()
        if inp is None:
            return False
        self._llm_notes = []
        self._full_text = False  # 新しく比較を始めたら「差分＋全文を送る」は外す(ADR-0158 決定1。_begin と同じ)
        return self._start(inp, build_request(inp), None, None, RUN_HANDOFF)

    def _closed(self) -> None:
        """窓が閉じた(WebSession が窓の closed の知らせで呼ぶ)。実行中なら中止を頼み、戻ってきた結果は捨てる。"""
        with self._lock:
            if self._worker is not None:
                self._worker.cancel()  # 工程の境目で止まる。門はワーカーが戻ったときに返す
            if self._asking is not None:
                self._asking.cancelled = True  # 送った要求は止められない。門は答えが届いたときに返す
            self._closing = True
            self._preview = None
            self._opened = None
            self._opened_saved = None
            self._handed = None  # 窓を閉じたら一括比較の答えは捨てる(ADR-0170 決定4)
            self._clear_follow_up()

    def _close(self) -> dict[str, Any]:
        self._closed()
        with self._lock:
            result = self._ok()
        if self._window is not None:
            self._window.destroy()
        return result


# 読むだけの処理(状態を変えない)。呼んだ後に、もう一方の窓へ知らせない(2つの窓が読み直しを送り合わないように。ADR-0160)
_READ_ONLY = frozenset(
    {CompareApi._state, CompareApi._state_for, CompareApi._result, CompareApi._ai_result, CompareApi._jump, CompareApi._all}
)
