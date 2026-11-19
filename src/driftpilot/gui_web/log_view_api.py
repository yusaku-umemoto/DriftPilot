"""「ログを見る」の窓(static/log_view.html)の Python の側(ADR-0248 決定2 / REQ-377)。

比較の窓の「ログを見る…」で開く。1 つのログ(ファイルのドロップ・「開く…」・貼り付け)を、機器ごと・コマンドごとに分けて
手元で読むだけの窓。AI に送る・門(比較と一括比較を同時に走らせない仕組み)を取る・伏せる処理を通す、のどれもしない。

- 画面から呼べるのは state・output・select・browse・paste・export_devices と、Api の app_info・messages だけ。
  内部の属性とメソッドは _ で始める(pywebview は js_api の _ で始まらない属性を画面に出す)
- 状態は state() が返す。出力の行は state に入れず、番号(output_revision)が変わったときだけ画面が output() を呼ぶ
  (比較の窓の result と同じ形)。表示するのは選んでいる区間の行だけ
- ロックはこの窓だけのもの。ダイアログを開いている間とファイルを読んでいる間は持たない。変わったら {"type": "log_view.changed"} を
  画面へ知らせる。画面は view の serial が小さいものを捨てる(遅れて届いた古い状態で描き直さない)
- 新しく入れたら前の表示を置き換え、1 台目の 1 つ目のコマンドを選び直す。読めなかったときは前の表示を残す
- 失敗は bridge の _guarded と同じ形({"ok": false, "message": <文>})。例外の文面・ログの中身・パスは出さない
  (ファイルを読めない誤りの文は、比較の窓と同じく入力の読み方 read_file が作る文)
"""

import threading
from pathlib import Path
from typing import Any

from driftpilot.batch_io import DeviceSplitError
from driftpilot.gui_web.bridge import Api, _guarded, notify
from driftpilot.gui_web.compare_api import DIALOG_FOLDER, DIALOG_OPEN, INPUT_FILE_TYPES
from driftpilot.gui_web.drop_order import dropped_paths
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
    can_export,
    device_rows,
    output_of,
    paste_log,
    read_log,
    segment_rows,
    summary_text,
    valid_position,
    write_log_device_files,
)
from driftpilot.screen_model.model import InputReadError

CHANGED_EVENT = "log_view.changed"


class LogViewApi(Api):
    """「ログを見る」の窓から呼べるメソッド(window.pywebview.api.<名前>)。REQ-103 の app_info・messages も使える。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._window: Any = None
        self._log: LoadedLog | None = None
        self._device = 0
        self._segment = 0
        self._serial = 0  # 状態が変わるたびに進める(読み込み中の印・状態の行・知らせも含む)
        self._output_revision = 0  # 出力の行が変わる(置き換え・選び直し)たびに進める
        self._status = ""
        self._loading = 0  # 読んでいる最中のファイルの数
        self._notice_id = 0
        self._late_notice: dict[str, Any] | None = None  # ドロップの知らせ(画面は番号で 1 回だけ出す)
        self._closing = False

    def _attach(self, window: Any) -> None:
        """pywebview の窓(知らせ・ファイルのダイアログに使う)。WebSession.open_log_view が渡す。"""
        self._window = window

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
            "devices": device_rows(log) if log is not None else [],
            "commands": segment_rows(log, self._device) if log is not None else [],
            "device": self._device,
            "segment": self._segment,
            "can_export": can_export(log),
            "status": self._status,
            "loading": self._loading > 0,
            "notice": self._late_notice,
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
                output_of(log, self._device, self._segment) if log is not None else {"command": None, "lines": []}
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
