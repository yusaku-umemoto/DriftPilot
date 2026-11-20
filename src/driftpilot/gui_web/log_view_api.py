"""「ログを見る」の窓(static/log_view.html)の Python の側(ADR-0248 決定2 / REQ-377)。

比較の窓の「ログを見る…」で開く。1 つのログ(ファイルのドロップ・「開く…」・貼り付け)を、機器ごと・コマンドごとに分けて
手元で読む窓。門(比較と一括比較を同時に走らせない仕組み)は取らない。
REQ-382 で、右に AI の欄を足した。表示中の区間か、チェックした区間(複数の機器もまとめて)を、送る前の確認を通してから AI に質問できる。
送る中身の組み立て(伏せる処理・上限・指示文)・確認・送信は gui_web/log_ask.py の LogAsk が受け持つ(このファイルは AI の部品を読まない)。

- 画面から呼べるのは state・output・select・browse・paste・export_devices・remember_split・remember_lists_split と、
  AI の欄の口(REQ-382): set_check・set_device_check・set_all_checks(送る区間のチェック)・ask_log(確認を出す。送らない)・
  answer_log_ask(「送信する」を押したときだけ 1 回送る)・cancel_log_ask・clear_log_ask・remember_ai_split・set_ai_hidden と、
  Api の app_info・messages だけ。AI に送る口は ask_log と answer_log_ask の 2 つだけ(実際に送るのは answer_log_ask だけ)。
  内部の属性とメソッドは _ で始める(pywebview は js_api の _ で始まらない属性を画面に出す)
- 状態は state() が返す。出力の行は state に入れず、番号(output_revision)が変わったときだけ画面が output() を呼ぶ
  (比較の窓の result と同じ形)。表示するのは選んでいる区間の行だけ
- ロックはこの窓だけのもの。ダイアログを開いている間とファイルを読んでいる間は持たない。変わったら {"type": "log_view.changed"} を
  画面へ知らせる。画面は view の serial が小さいものを捨てる(遅れて届いた古い状態で描き直さない)
- 新しく入れたら前の表示を置き換え、1 台目の 1 つ目のコマンドを選び直す。読めなかったときは前の表示を残す
- 境目の位置(REQ-381): 作るときに ui-state.json の log_view_split_left(左の面の割合)・log_view_lists_split(機器の一覧の割合)を
  1 回だけ読み(範囲の外・数でないものは None=初めの値)、state の layout に入れる。layout には画面が境目を止める範囲(ui_state の定数)も入れる。
  画面が境目を動かし終えたときに remember_split・remember_lists_split を呼ぶ。小数 3 桁に丸め、覚えた値と違うときだけ書く。
  書けなくても ok を返し、知らせない(書けなかったときは覚えた値を変えない=次に同じ値でもう一度書こうとする。比較の窓と同じ)。窓が閉じた後は書かない
- AI の欄(REQ-382): 出力と AI の欄の境目(log_view_ai_split。出力の側の割合)と隠した状態(log_view_ai_hidden)も、同じ決まりで覚える
  (remember_ai_split・set_ai_hidden。作るときに 1 回だけ読み、state の layout の ai_split・ai_hidden・ai_split_range に入れる)
- 送る区間のチェックは Python が持つ(機器の番号と区間の番号の組の集まり)。機器を切り替えても残り、新しいログを入れたら全部外す。
  チェックが 0 なら表示中の 1 区間、1 つ以上ならチェックした区間全部を送る。チェックしても表示中の区間は変えない
- state の view.ai: 送り先の行(destination)・送る区間の行(scope)・やり取り(turns)・誤り・注意・送信中の文・押せるか・
  境目と隠した状態。送り先の行は最初の state で 1 回読んで覚え、送信のたびと、設定が変わった知らせ(settings・profiles)を受けたときに
  読み直す(違うときだけ状態の番号を進めて窓に知らせる)
- 失敗は bridge の _guarded と同じ形({"ok": false, "message": <文>})。例外の文面・ログの中身・パスは出さない
  (ファイルを読めない誤りの文は、比較の窓と同じく入力の読み方 read_file が作る文)
"""

import math
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from driftpilot.batch_io import DeviceSplitError
from driftpilot.gui_web.bridge import Api, _guarded, notify
from driftpilot.gui_web.compare_api import DIALOG_FOLDER, DIALOG_OPEN, INPUT_FILE_TYPES
from driftpilot.gui_web.drop_order import dropped_paths
from driftpilot.gui_web.log_ask import LogAsk
from driftpilot.gui_web.settings_change import PROFILES, SETTINGS, SettingsChange
from driftpilot.messages import (
    COMPARE_DROP_FOLDER,
    COMPARE_DROP_NO_PATH,
    COMPARE_DROP_TITLE,
    COMPARE_READ_FAILED_TITLE,
    DEVICE_SPLIT_CONFIRM_YES,
    DIALOG_CANCEL,
    DIALOG_OK,
    LOG_VIEW_DROP_ONE_ONLY,
    LOG_VIEW_DROP_ONE_TITLE,
    LOG_VIEW_READ_FAILED,
    LOG_VIEW_SPLIT_TITLE,
    device_split_confirm_body,
    gui_web_call_failed,
    log_view_loaded,
    log_view_split_done,
)
from driftpilot.screen_model.log_view_model import (
    LoadedLog,
    Position,
    all_check,
    can_export,
    commands_check,
    device_rows,
    output_of,
    paste_log,
    read_log,
    scope_text,
    segment_rows,
    send_positions,
    summary_text,
    toggle_all,
    toggle_device,
    toggle_segment,
    valid_position,
    write_log_device_files,
)
from driftpilot.screen_model.model import InputReadError
from driftpilot.ui_state import (
    LOG_VIEW_AI_HIDDEN_KEY,
    LOG_VIEW_AI_SPLIT_KEY,
    LOG_VIEW_AI_SPLIT_MAX,
    LOG_VIEW_AI_SPLIT_MIN,
    LOG_VIEW_LISTS_SPLIT_KEY,
    LOG_VIEW_LISTS_SPLIT_MAX,
    LOG_VIEW_LISTS_SPLIT_MIN,
    LOG_VIEW_SPLIT_LEFT_KEY,
    LOG_VIEW_SPLIT_LEFT_MAX,
    LOG_VIEW_SPLIT_LEFT_MIN,
    load_log_view_ai_layout,
    load_log_view_layout,
    save_log_view_layout,
    ui_state_path,
)

CHANGED_EVENT = "log_view.changed"


class LogViewApi(Api):
    """「ログを見る」の窓から呼べるメソッド(window.pywebview.api.<名前>)。REQ-103 の app_info・messages も使える。"""

    def __init__(
        self,
        *,
        env: Mapping[str, str] | None = None,
        platform: str | None = None,
        home: Path | None = None,
        cwd: Path | None = None,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._lock = threading.RLock()
        # 境目の位置の置き場所(省けば今の利用者の場所。テストで一時フォルダーを渡す)。開くときに 1 回だけ読む(REQ-381)。
        # _remembered は最後に読んだ・書いた値(同じ値なら書かないために持つ)
        self._where: dict[str, Any] = {"env": env, "platform": platform, "home": home}
        self._split_left, self._lists_split = load_log_view_layout(ui_state_path(**self._where))
        self._ai_split, self._ai_hidden = load_log_view_ai_layout(ui_state_path(**self._where))
        self._remembered: dict[str, Any] = {
            LOG_VIEW_SPLIT_LEFT_KEY: self._split_left,
            LOG_VIEW_LISTS_SPLIT_KEY: self._lists_split,
            LOG_VIEW_AI_SPLIT_KEY: self._ai_split,
            LOG_VIEW_AI_HIDDEN_KEY: self._ai_hidden,
        }
        self._window: Any = None
        self._log: LoadedLog | None = None
        self._device = 0
        self._segment = 0
        self._checked: frozenset[Position] = frozenset()  # AI に送る区間のチェック(機器の番号と区間の番号の組)。新しいログで全部外す
        self._serial = 0  # 状態が変わるたびに進める(読み込み中の印・状態の行・知らせも含む)
        self._output_revision = 0  # 出力の行が変わる(置き換え・選び直し)たびに進める
        self._status = ""
        self._loading = 0  # 読んでいる最中のファイルの数
        self._notice_id = 0
        self._late_notice: dict[str, Any] | None = None  # ドロップの知らせ(画面は番号で 1 回だけ出す)
        self._closing = False
        # AI の欄(REQ-382)。設定の読み元は作業フォルダーも含む。送り先の行は最初の state で 1 回読んで覚える
        self._ask = LogAsk({**self._where, "cwd": cwd}, client_factory, self._ask_changed)
        self._destination: str | None = None

    def _attach(self, window: Any) -> None:
        """pywebview の窓(知らせ・ファイルのダイアログに使う)。WebSession.open_log_view が渡す。"""
        self._window = window

    def _on_settings_change(self, change: SettingsChange) -> None:
        """設定が変わった知らせ(WebSession.publish。ADR-0250 決定2・REQ-379)。

        AI の欄の送り先の行(使用中の AI プロファイルの表示名とモデル)は、settings か profiles の知らせで読み直す。
        違うときだけ状態の番号を進めて窓に知らせる(同じなら何もしない)。ほかの種類は何もしない。
        """
        if SETTINGS not in change.kinds and PROFILES not in change.kinds:
            return
        if self._refresh_destination():
            self._changed()

    def _refresh_destination(self) -> bool:
        """送り先の行を読み直す(ロックの外で呼ぶ)。違ったときだけ状態の番号を進めて真。"""
        line = self._ask.destination_line()
        with self._lock:
            if self._destination is None or line == self._destination:
                return False  # 最初の state がまだ(読んだときに覚える)・同じ
            self._destination = line
            self._bump()
            return True

    def _ask_changed(self) -> None:
        """AI の欄の会話・送信中の表示が変わった(LogAsk がロックの外で呼ぶ)。状態の番号を進めて窓に知らせる。"""
        with self._lock:
            if self._closing:
                return
            self._bump()
        self._changed()

    # --- 画面から呼ぶメソッド ---

    def state(self) -> dict[str, Any]:
        """今の状態(一覧・選んでいる位置・状態の行・書き出せるか・出力の番号)。読むだけ。"""
        return _guarded(self._state)

    def output(self) -> dict[str, Any]:
        """選んでいる区間のコマンドと出力の行だけ。読むだけ。"""
        return _guarded(self._output)

    def select(self, device: Any, segment: Any) -> dict[str, Any]:
        """機器とコマンドを選ぶ(どちらも 0 から数えた番号)。"""
        return _guarded(self._select, device, segment)

    def browse(self) -> dict[str, Any]:
        """「開く…」。窓のファイルのダイアログで選び、読み込んで前の表示を置き換える。"""
        return _guarded(self._browse)

    def paste(self, text: Any) -> dict[str, Any]:
        """貼り付けの「OK」。空の文は何もしない。"""
        return _guarded(self._paste, text)

    def export_devices(self, confirmed: Any = False) -> dict[str, Any]:
        """「機器ごとのファイルに書き出す…」。1 回目はフォルダーを選ぶ前の確かめを split_confirm で返す。
        画面が「フォルダーを選ぶ…」を押したときだけ confirmed=True で呼び直し、フォルダーを選んで書く(一括比較の窓は開かない)。
        """
        return _guarded(self._export, confirmed)

    def remember_split(self, ratio: Any) -> dict[str, Any]:
        """左右の境目(左の面の割合)を動かし終えたとき。ui-state.json に覚える(書けなくても ok)。"""
        return _guarded(self._remember, LOG_VIEW_SPLIT_LEFT_KEY, ratio)

    def remember_lists_split(self, ratio: Any) -> dict[str, Any]:
        """上下の境目(機器の一覧の割合)を動かし終えたとき。ui-state.json に覚える(書けなくても ok)。"""
        return _guarded(self._remember, LOG_VIEW_LISTS_SPLIT_KEY, ratio)

    def remember_ai_split(self, ratio: Any) -> dict[str, Any]:
        """出力と AI の欄の境目(出力の側の割合)を動かし終えたとき。ui-state.json に覚える(書けなくても ok)。"""
        return _guarded(self._remember, LOG_VIEW_AI_SPLIT_KEY, ratio)

    def set_ai_hidden(self, hidden: Any) -> dict[str, Any]:
        """AI の欄を隠す・出す。隠した状態を ui-state.json に覚える(書けなくても ok)。"""
        return _guarded(self._set_ai_hidden, hidden)

    def set_check(self, device: Any, segment: Any, on: Any) -> dict[str, Any]:
        """区間 1 つの「AI に送る」チェックを付ける・外す。表示中の区間は変えない。"""
        return _guarded(self._set_check, device, segment, on)

    def set_device_check(self, device: Any, on: Any) -> dict[str, Any]:
        """機器のチェック: その機器の全部のコマンドを入れる・外す。"""
        return _guarded(self._set_device_check, device, on)

    def set_all_checks(self, on: Any) -> dict[str, Any]:
        """見出しの「全部」: 全部の機器の全部のコマンドを入れる・外す。"""
        return _guarded(self._set_all_checks, on)

    def ask_log(self, question: Any) -> dict[str, Any]:
        """AI への質問(質問の欄の送信)。設定を読み(押すたびに読む)、送る中身を作って、送る前の確認を返す。★ 送らない。"""
        return _guarded(self._ask_log, question)

    def answer_log_ask(self, number: Any, yes: Any) -> dict[str, Any]:
        """送る前の確認の答え。★ AI に送るのは、「送信する」(yes が True)のときだけの 1 回。"""
        return _guarded(self._answer_log_ask, number, yes)

    def cancel_log_ask(self) -> dict[str, Any]:
        """送信中の質問を中止する(中止した後に届いた答えは捨てる)。"""
        return _guarded(self._cancel_log_ask)

    def clear_log_ask(self) -> dict[str, Any]:
        """「会話を消す」。会話と最初に送った中身を捨てる(送信中なら取り消す)。"""
        return _guarded(self._clear_log_ask)

    # --- 内部 ---

    def _bump(self) -> None:
        """ロックの中で呼ぶ。状態の番号を進める。"""
        self._serial += 1

    def _view(self) -> dict[str, Any]:
        """ロックの中で呼ぶ。画面に渡す状態(出力の行は入れない)。"""
        log = self._log
        return {
            "serial": self._serial,
            "output_revision": self._output_revision,
            "loaded": log is not None,
            "name": log.name if log is not None else None,
            "summary": summary_text(log) if log is not None else None,
            "devices": device_rows(log, self._checked) if log is not None else [],
            "commands": segment_rows(log, self._device, self._checked) if log is not None else [],
            # AI に送るチェック(REQ-382)。見出しの「全部」(全部の機器の全部のコマンド・表示中の機器の全部のコマンド)の状態(on・some・off)
            "check_all": all_check(log, self._checked),
            "commands_check": commands_check(log, self._checked, self._device),
            "device": self._device,
            "segment": self._segment,
            "can_export": can_export(log),
            "status": self._status,
            "loading": self._loading > 0,
            "notice": self._late_notice,
            "layout": {
                "split_left": self._split_left,
                "lists_split": self._lists_split,
                "split_left_range": [LOG_VIEW_SPLIT_LEFT_MIN, LOG_VIEW_SPLIT_LEFT_MAX],
                "lists_split_range": [LOG_VIEW_LISTS_SPLIT_MIN, LOG_VIEW_LISTS_SPLIT_MAX],
            },
            "ai": self._ai_view(),
        }

    def _ai_view(self) -> dict[str, Any]:
        """ロックの中で呼ぶ。AI の欄の状態。送り先の行は最初の state で 1 回読んで覚える。"""
        if self._destination is None:
            self._destination = self._ask.destination_line()
        ask = self._ask.view()
        return {
            "destination": self._destination,
            "scope": scope_text(self._log, self._checked, self._device, self._segment),
            "turns": ask["turns"],
            "error": ask["error"],
            "note": ask["note"],
            "asking": ask["asking"],
            "can_ask": self._log is not None and ask["asking"] is None and not self._closing,
            "can_clear": ask["can_clear"],
            "revision": ask["revision"],
            "hidden": self._ai_hidden,
            "split": self._ai_split,
            "split_range": [LOG_VIEW_AI_SPLIT_MIN, LOG_VIEW_AI_SPLIT_MAX],
        }

    def _ok(self, **extra: Any) -> dict[str, Any]:
        """ロックの中で呼ぶ。"""
        return {"ok": True, "view": self._view(), **extra}

    @staticmethod
    def _dialog(body: str, title: str) -> dict[str, Any]:
        return {"title": title, "body": body, "ok": DIALOG_OK}

    def _state(self) -> dict[str, Any]:
        with self._lock:
            return self._ok()

    def _output(self) -> dict[str, Any]:
        with self._lock:
            log = self._log
            output = (
                output_of(log, self._device, self._segment)
                if log is not None
                else {"command": None, "prompt_line": None, "lines": []}
            )
            return {"ok": True, "output": {"revision": self._output_revision, **output}}

    def _select(self, device: Any, segment: Any) -> dict[str, Any]:
        with self._lock:
            if not valid_position(self._log, device, segment):
                raise ValueError("position")
            if (device, segment) != (self._device, self._segment):
                self._device, self._segment = device, segment
                self._output_revision += 1
                self._bump()
            return self._ok()

    def _remember(self, key: str, ratio: Any) -> dict[str, Any]:
        """境目の割合を覚える。数でない値(真偽値も)は誤り。丸めて覚えた値と違うときだけ書く。書けなくても ok(知らせない)。"""
        if isinstance(ratio, bool) or not isinstance(ratio, (int, float)) or not math.isfinite(ratio):
            raise ValueError("ratio")
        rounded = round(float(ratio), 3)
        with self._lock:
            if self._closing:
                return self._ok()
            self._write_if_changed(key, rounded)
            return self._ok()

    def _write_if_changed(self, key: str, value: float | bool) -> None:
        """ロックの中で呼ぶ。覚えた値と違うときだけ書く。書けなくても画面は止めず、知らせない(覚えた値は変えない)。"""
        if value == self._remembered.get(key) and type(value) is type(self._remembered.get(key)):
            return
        try:
            save_log_view_layout(ui_state_path(**self._where), {key: value})
        except (OSError, ValueError):
            pass
        else:
            self._remembered[key] = value

    def _set_ai_hidden(self, hidden: Any) -> dict[str, Any]:
        """AI の欄を隠す・出す。隠した状態は ui-state.json に覚える(窓が閉じた後は変えない・書かない)。"""
        if not isinstance(hidden, bool):
            raise ValueError("hidden")
        with self._lock:
            if self._closing:
                return self._ok()
            if hidden != self._ai_hidden:
                self._ai_hidden = hidden
                self._bump()
            self._write_if_changed(LOG_VIEW_AI_HIDDEN_KEY, hidden)
            return self._ok()

    # --- 送る区間のチェックと AI への質問(REQ-382) ---

    def _set_check(self, device: Any, segment: Any, on: Any) -> dict[str, Any]:
        if not isinstance(on, bool):
            raise ValueError("on")
        with self._lock:
            if not valid_position(self._log, device, segment):
                raise ValueError("position")
            if not self._closing:
                self._checked = toggle_segment(self._checked, device, segment, on)
                self._bump()
            return self._ok()

    def _set_device_check(self, device: Any, on: Any) -> dict[str, Any]:
        if not isinstance(on, bool):
            raise ValueError("on")
        with self._lock:
            log = self._log
            if log is None or isinstance(device, bool) or not isinstance(device, int) or not 0 <= device < len(log.devices):
                raise ValueError("device")
            if not self._closing:
                self._checked = toggle_device(log, self._checked, device, on)
                self._bump()
            return self._ok()

    def _set_all_checks(self, on: Any) -> dict[str, Any]:
        if not isinstance(on, bool):
            raise ValueError("on")
        with self._lock:
            if not self._closing:
                self._checked = toggle_all(self._log, self._checked, on)
                self._bump()
            return self._ok()

    def _ask_log(self, question: Any) -> dict[str, Any]:
        if not isinstance(question, str):
            raise ValueError("question")
        with self._lock:
            if self._closing:
                return self._ok()
            log = self._log
            positions = send_positions(log, self._checked, self._device, self._segment)
            result = self._ask.ask(log, positions, question)  # 設定を読んで確認の中身を作る。送らない
            if not result["ok"]:
                return result
            self._refresh_destination()  # 送るたびに送り先の行を読み直す
            self._bump()
            return self._ok(log_ask=result["log_ask"])

    def _answer_log_ask(self, number: Any, yes: Any) -> dict[str, Any]:
        if isinstance(number, bool) or not isinstance(number, int) or not isinstance(yes, bool):
            raise ValueError("answer")
        with self._lock:
            if self._closing:
                return self._ok()
            result = self._ask.answer(number, yes, self._log)  # 「送信する」のときだけ、読み直してから 1 回送る
            if not result["ok"]:
                return result
            self._refresh_destination()
            self._bump()
            return self._ok(log_ask=result["log_ask"])

    def _cancel_log_ask(self) -> dict[str, Any]:
        with self._lock:
            self._ask.cancel()
            self._bump()
            return self._ok()

    def _clear_log_ask(self) -> dict[str, Any]:
        with self._lock:
            self._ask.clear()
            self._bump()
            return self._ok()

    def _changed(self) -> None:
        """画面に「状態が変わった」とだけ知らせる(ロックの外で呼ぶ)。窓が閉じた後の失敗は握りつぶす。"""
        window = self._window
        if window is None:
            return
        try:
            notify(window, {"type": CHANGED_EVENT})
        except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
            pass

    def _replace(self, log: LoadedLog) -> None:
        """ロックの中で呼ぶ。前の表示を置き換え、1 台目の 1 つ目のコマンドを選び直す。"""
        self._log = log
        self._device = 0
        self._segment = 0
        self._checked = frozenset()  # 新しいログを入れたら、AI に送るチェックを全部外し、会話も消す(REQ-382)
        self._ask.clear()
        self._output_revision += 1
        self._status = log_view_loaded(log.name)
        self._bump()

    def _choose_file(self, kind: int, file_types: tuple[str, ...]) -> Path | None:
        """窓のファイルのダイアログで選んだ場所。取り消した・窓が無いときは None。ロックの外で呼ぶ。

        選んだときは、ほかは文字列の並びを返す(pywebview 6.2.1。比較の窓と同じ)。
        """
        window = self._window
        if window is None:
            return None
        chosen = window.create_file_dialog(kind, directory="", allow_multiple=False, save_filename="", file_types=file_types)
        if isinstance(chosen, (list, tuple)):
            chosen = chosen[0] if chosen else None
        return Path(chosen) if isinstance(chosen, str) and chosen else None

    def _read(self, path: Path, *, more_dropped: bool = False) -> dict[str, Any] | None:
        """ファイルを読んで置き換える(読む間はロックを持たない)。読めなかったときは知らせ(題・本文・OK)を返し、前の表示を残す。

        more_dropped は 2 つ以上のファイルをドロップされたとき(最初の 1 つだけ読んだ知らせを出す)。
        """
        with self._lock:
            self._loading += 1
            self._bump()
        self._changed()  # 読み込み中を画面へ
        failure: dict[str, Any] | None = None
        log: LoadedLog | None = None
        try:
            log = read_log(path)
        except InputReadError as exc:
            failure = self._dialog(str(exc), COMPARE_READ_FAILED_TITLE)
        with self._lock:
            self._loading -= 1
            if log is not None and not self._closing:
                self._replace(log)
                if more_dropped:
                    self._notice_id += 1
                    self._late_notice = {"id": self._notice_id, **self._dialog(LOG_VIEW_DROP_ONE_ONLY, LOG_VIEW_DROP_ONE_TITLE)}
            elif failure is not None:
                self._status = LOG_VIEW_READ_FAILED
            self._bump()
        return failure

    def _browse(self) -> dict[str, Any]:
        with self._lock:
            if self._closing:
                return self._ok()
        path = self._choose_file(DIALOG_OPEN, INPUT_FILE_TYPES)  # 開いている間はロックを持たない
        if path is None:
            return self._state()  # 取り消した
        failure = self._read(path)
        with self._lock:
            return self._ok(notice=failure) if failure is not None else self._ok()

    def _paste(self, text: Any) -> dict[str, Any]:
        if not isinstance(text, str):
            raise ValueError("text")
        with self._lock:
            if self._closing or not text.strip():
                return self._ok()
        log = paste_log(text)  # 分ける間はロックを持たない
        with self._lock:
            if not self._closing:
                self._replace(log)
            return self._ok()

    def _export(self, confirmed: Any) -> dict[str, Any]:
        if not isinstance(confirmed, bool):
            raise ValueError("confirmed")
        with self._lock:
            log = self._log
            if self._closing or log is None or not can_export(log):
                return self._ok()
            if not confirmed:
                # フォルダーを選ぶ前の確かめ(社外秘の注意は書いた後の知らせに付く)。「フォルダーを選ぶ…」のときだけ呼び直す
                confirm = {
                    "title": LOG_VIEW_SPLIT_TITLE,
                    "body": device_split_confirm_body(),
                    "yes": DEVICE_SPLIT_CONFIRM_YES,
                    "no": DIALOG_CANCEL,
                }
                return self._ok(split_confirm=confirm)
        parent = self._choose_file(DIALOG_FOLDER, ())  # 開いている間はロックを持たない
        with self._lock:
            if parent is None or self._log is not log:
                return self._ok()  # 選ばなかった・選んでいる間に表示が変わった(何も書かない)
        try:
            written = write_log_device_files(log.text, parent)
        except DeviceSplitError as exc:
            # 書いた途中のものは残す(消さない)
            with self._lock:
                return self._ok(notice=self._dialog(str(exc), LOG_VIEW_SPLIT_TITLE))
        with self._lock:
            self._status = log_view_split_done(written.folder, written.count)
            self._bump()
            return self._ok()

    # --- ファイルのドロップ(pywebview が事象のスレッドから呼ぶ。画面からは呼べない) ---

    def _on_drop(self, event: Any) -> None:
        """窓へのドロップ(app.watch_drop が印の要素(#drop-anchor)の drop に登録する)。想定外の例外は文面を出さない知らせにする。"""
        try:
            self._drop(event)
        except Exception:  # noqa: BLE001 - 事象のスレッドの例外は画面に届かないので、文面を出さない知らせにする
            self._drop_notice(gui_web_call_failed(), COMPARE_DROP_TITLE)

    def _drop_notice(self, body: str, title: str) -> None:
        """表示を変えずに知らせを出す(ロックの外で呼ぶ。画面は番号で 1 回だけ出す)。"""
        with self._lock:
            self._notice_id += 1
            self._late_notice = {"id": self._notice_id, **self._dialog(body, title)}
            self._bump()
        self._changed()

    def _drop(self, event: Any) -> None:
        with self._lock:
            if self._closing:
                return
        paths = dropped_paths(event)
        if not paths:
            self._drop_notice(COMPARE_DROP_NO_PATH, COMPARE_DROP_TITLE)
            return
        first = paths[0]  # 2 つ以上なら最初の 1 つだけ読む
        if first.is_dir():
            self._drop_notice(COMPARE_DROP_FOLDER, COMPARE_DROP_TITLE)
            return
        failure = self._read(first, more_dropped=len(paths) > 1)
        if failure is not None:
            self._drop_notice(failure["body"], failure["title"])
            return
        self._changed()

    def _closed(self) -> None:
        """窓が閉じた(WebSession が窓の closed の知らせで呼ぶ)。これからの操作とドロップは受けない。"""
        with self._lock:
            self._closing = True
            self._ask.closed()  # 待っている確認を捨て、送信中なら取り消す(REQ-382)
