"""pywebview の窓をまたいで共有する物(REQ-105 §2.3・REQ-106 §2)。比較の窓が1つ作り、窓の Python の側に渡す。

- 門(RunGate。比較と一括比較を同時に走らせない。ADR-0029 決定9)。RunGate は「メインスレッドからだけ触る」物だが、
  pywebview の js_api は別々のスレッドで動くので、RunGate に触るのはこのロックの中だけにする。RunGate の中身は変えない。
  subscribe は使わない(門が変わったら、変えた窓が gate_changed を呼び、ほかの窓へ知らせて読み直させる)
- 設定の窓(比較の画面と一括比較の画面の「設定…」で開く。1つだけ。ADR-0176 決定4)。開いていれば前に出す。
  比較の窓を閉じたら一緒に閉じる(wx の親子の窓と同じ)。設定の窓で保存できたら、開いている比較の窓と一括比較の窓へ知らせる
- 一括比較の窓(比較の画面の「一括比較…」で開く。1つだけ。REQ-106)。開いていれば前に出す。比較の窓を閉じたら一緒に閉じる
  (実行中なら中止する)。「比較画面で開く」の受け渡し(handoff)は、ここから比較の窓の Python の側へ渡す。
  watch_drop を渡されたら、一括比較の窓の読み込みの後にドロップを受ける(REQ-115 / ADR-0078 決定4)
- AI の窓(比較の窓の AI の面の「↗」で開く。1つだけ。ADR-0160 / REQ-220)。開いていれば前に出す。比較の窓がつながって
  いないとき(--batch・--settings)は開かない。比較の窓を閉じたら一緒に閉じる。状態は比較の窓の Python の側(CompareApi)が持ち、
  AI の窓の Python の側(AiPaneApi)は薄い口だけ。開いたら CompareApi._ai_attached、閉じたら CompareApi._ai_detached を
  ロックの外で呼ぶ

窓を作る関数(create_window)は差し替えられる(テストは偽物を渡す)。省くと webview.create_window(使うときに読む)。
窓の Python の側が自分のロックを持つときは、そのロックの中からここを呼んでよい(ここから窓の Python の側を呼ぶのはロックの外だけ)。
"""

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from driftpilot.gui_web.settings_api import SettingsApi
from driftpilot.messages import AI_PANE_TITLE, BATCH_TITLE, SETTINGS_TITLE
from driftpilot.screen_model.worker import RunGate

if TYPE_CHECKING:  # compare_api は session を読むので、型の注記のときだけ読む
    from driftpilot.gui_web.compare_api import CompareApi


def _webview_create_window(*args: Any, **kwargs: Any) -> Any:
    import webview

    return webview.create_window(*args, **kwargs)


def _new_batch_api(session: "WebSession") -> Any:
    """一括比較の画面の Python の側(batch_api は session を読むので、使うときに読む)。"""
    from driftpilot.gui_web.batch_api import BatchApi

    return BatchApi(session)


def _new_ai_api(compare: "CompareApi") -> Any:
    """AI の窓の Python の側(ai_pane_api は compare_api を読むので、使うときに読む。ADR-0160 決定3)。"""
    from driftpilot.gui_web.ai_pane_api import AiPaneApi

    return AiPaneApi(compare)


class WebSession:
    """窓をまたいで共有する門・ロック・開いている窓。

    settings_window は設定の窓の create_window に足す引数(url と大きさ。app.run が渡す)。
    settings_api は設定の画面の Python の側を作る関数(開くたびに新しく作る。テストで差し替える)。
    batch_window は一括比較の窓の create_window に足す引数(url・大きさ・位置・min_size・text_select。app.run が渡す)。
    batch_api は一括比較の画面の Python の側を作る関数(session を受け取る。開くたびに新しく作る。テストで差し替える)。
    watch_drop は一括比較の窓の読み込みの後にドロップを受ける関数(app.watch_drop。窓と Python の側の _on_drop を渡す。
    REQ-115)。省くとドロップを受けない。
    ai_window は AI の窓の create_window に足す引数(url・大きさ・位置・min_size・text_select。app.run が渡す。ADR-0160 決定2)。
    ai_api は AI の窓の Python の側を作る関数(比較の窓の CompareApi を受け取る。開くたびに新しく作る。テストで差し替える)。
    """

    def __init__(
        self,
        *,
        create_window: Callable[..., Any] | None = None,
        settings_window: dict[str, Any] | None = None,
        settings_api: Callable[[], Any] = SettingsApi,
        batch_window: dict[str, Any] | None = None,
        batch_api: Callable[["WebSession"], Any] = _new_batch_api,
        watch_drop: Callable[[Any, Callable[[Any], None]], None] | None = None,
        ai_window: dict[str, Any] | None = None,
        ai_api: Callable[["CompareApi"], Any] = _new_ai_api,
    ) -> None:
        self.lock = threading.RLock()
        self._gate = RunGate()
        self._create_window = create_window or _webview_create_window
        self._settings_window = dict(settings_window or {})
        self._settings_api = settings_api
        self._batch_window = dict(batch_window or {})
        self._batch_api = batch_api
        self._watch_drop = watch_drop
        self._watchers: list[tuple[object, Callable[[], None]]] = []
        self._compare: tuple[Any, Any] | None = None  # (比較の窓, その Python の側)
        self._compare_closed = False
        self._settings: tuple[Any, Any] | None = None  # (設定の窓, その Python の側)
        self._opening = False  # 設定の窓を作っている途中(二重に開かない)
        self._batch: tuple[Any, Any] | None = None  # (一括比較の窓, その Python の側)
        self._batch_opening = False  # 一括比較の窓を作っている途中(二重に開かない)
        self._ai_window = dict(ai_window or {})
        self._ai_api = ai_api
        self._ai: tuple[Any, Any] | None = None  # (AI の窓, その Python の側)
        self._ai_opening = False  # AI の窓を作っている途中(二重に開かない)
        self._splash_taken = False  # 起動時の絵(キャラクター)の番を、最初の窓が使ったか(ADR-0174 決定2)

    # --- 起動時の絵(ADR-0174 決定2) ---

    def _take_splash(self) -> bool:
        """最初に呼んだ1回だけ真を返し、使ったことを覚える(アプリ1回の起動で1回だけ)。画面からは呼ばない。

        比較の窓(--batch のときは一括比較の窓)が最初の状態を返すときに呼ぶ。キャラクターがオフでも番は使う。
        """
        with self.lock:
            first = not self._splash_taken
            self._splash_taken = True
            return first

    # --- 門 ---

    def free_for(self, who: object) -> bool:
        with self.lock:
            return self._gate.free_for(who)

    def acquire(self, who: object) -> bool:
        with self.lock:
            return self._gate.acquire(who)

    def release(self, who: object) -> None:
        with self.lock:
            self._gate.release(who)

    @property
    def owner(self) -> object | None:
        with self.lock:
            return self._gate.owner

    def watch(self, who: object, changed: Callable[[], None]) -> None:
        """門が変わったときに呼ぶ関数(窓へ知らせて読み直させる)。who が変えたときは呼ばない。"""
        with self.lock:
            self._watchers.append((who, changed))

    def unwatch(self, who: object) -> None:
        """who の登録を外す(窓を閉じたとき)。"""
        with self.lock:
            self._watchers = [(owner, changed) for owner, changed in self._watchers if owner is not who]

    def gate_changed(self, who: object) -> None:
        """who が門を取った・返した後に呼ぶ(ロックの外で)。ほかの窓へ知らせる。知らせの失敗は握りつぶす。"""
        with self.lock:
            others = [changed for owner, changed in self._watchers if owner is not who]
        for changed in others:
            try:
                changed()
            except Exception:  # noqa: BLE001 - 閉じた窓への知らせの失敗は捨てる
                pass

    # --- 比較の窓 ---

    def attach_compare(self, api: Any, window: Any) -> None:
        """比較の窓をつなぐ(app.run が create_window の後に呼ぶ)。窓を閉じたら、設定の窓と一括比較の窓も閉じる。"""
        api._attach(window)
        with self.lock:
            self._compare = (window, api)
        window.events.closed += self._on_compare_closed

    def has_compare(self) -> bool:
        """比較の窓がつながっていて、まだ閉じていないか(一括比較の「比較画面で開く」を押せるか)。"""
        with self.lock:
            return self._compare is not None and not self._compare_closed

    def _on_compare_closed(self) -> None:
        with self.lock:
            self._compare_closed = True
            compare, settings, batch, ai = self._compare, self._settings, self._batch, self._ai
            self._settings = None
            self._batch = None
            self._ai = None
        if compare is not None:
            compare[1]._closed()
        if ai is not None:
            # AI の窓は比較の窓の _closed の後に閉じる(閉じた窓への知らせを止めてから。ADR-0160 決定5)
            if compare is not None:
                compare[1]._ai_detached()
            _destroy(ai[0])
        if settings is not None:
            _destroy(settings[0])
        if batch is not None:
            batch[1]._closed()  # 実行中なら中止を頼む。門はワーカーが戻ったときに返す
            _destroy(batch[0])

    def handoff(self, handoff: Any, answer: Any = None) -> bool:
        """一括比較の「比較画面で開く」。比較の窓の入力欄と設定を埋め、比較の窓を前に出す。比較の窓が無ければ False。

        answer はその組の AI の答え(batch_model.HandedAnswer。無ければ None)。比較の窓が比べ直して、差分が同じときだけ使う(ADR-0170)。
        """
        with self.lock:
            compare = self._compare if not self._compare_closed else None
        if compare is None:
            return False
        if compare[1]._load_handoff(handoff, answer):
            _bring_to_front(compare[0])
        return True

    # --- 設定の窓 ---

    def open_settings(self) -> None:
        """設定の窓を開く。開いていれば前に出す(pywebview に「前に出す」の口は無いので show と restore)。"""
        with self.lock:
            if self._compare_closed or self._opening:
                return
            current = self._settings
            if current is None:
                self._opening = True
        if current is not None:
            _bring_to_front(current[0])
            return
        try:
            api = self._settings_api()
            window = self._create_window(SETTINGS_TITLE, js_api=api, **self._settings_window)
            api._attach(window)  # 「閉じる」・接続テストの知らせ・ファイルのダイアログに使う
            # 保存できたら、開いている比較の窓と一括比較の窓へ知らせる(ADR-0176 決定4。テストの偽物に口が無くても動く)
            set_on_saved = getattr(api, "_set_on_saved", None)
            if set_on_saved is not None:
                set_on_saved(self.settings_saved)
            window.events.closed += lambda: self._on_settings_closed(window)
            with self.lock:
                closed = self._compare_closed
                if not closed:
                    self._settings = (window, api)
            if closed:
                _destroy(window)  # 開いている間に比較の窓が閉じた
        finally:
            with self.lock:
                self._opening = False

    def settings_changed(self) -> None:
        """比較の窓で AI プロファイル・プロンプトの使用中を変えた後に呼ぶ(ロックの外で。REQ-206 §5)。

        設定の窓が開いていれば、保存していない編集を残して読み直させる(SettingsApi._profiles_changed)。失敗は握りつぶす。
        """
        with self.lock:
            settings = self._settings
        if settings is None:
            return
        refresh = getattr(settings[1], "_profiles_changed", None)
        if refresh is None:
            return
        try:
            refresh()
        except Exception:  # noqa: BLE001 - 閉じかけの窓への知らせの失敗は捨てる
            pass

    def settings_saved(self) -> None:
        """設定の窓で保存できた後に呼ぶ(ロックの外で。ADR-0176 決定4)。

        開いている比較の窓(AI の窓が開いていればそちらにも届く)と一括比較の窓へ「状態が変わった」と知らせ、
        state() を読み直して描き直させる(結果・答え・入力は消さない)。知らせの失敗は握りつぶす。
        """
        with self.lock:
            targets = []
            if self._compare is not None and not self._compare_closed:
                targets.append(self._compare[1])
            if self._batch is not None:
                targets.append(self._batch[1])
        for api in targets:
            changed = getattr(api, "_changed", None)
            if changed is None:
                continue
            try:
                changed()
            except Exception:  # noqa: BLE001 - 閉じかけの窓への知らせの失敗は捨てる
                pass

    def _on_settings_closed(self, window: Any) -> None:
        with self.lock:
            if self._settings is not None and self._settings[0] is window:
                self._settings = None

    # --- AI の窓(ADR-0160) ---

    def open_ai_pane(self) -> None:
        """AI の窓を開く。開いていれば前に出す(open_settings と同じ形。ADR-0160 決定2)。

        比較の窓がつながっていない(--batch・--settings)・閉じた後・作っている途中は何もしない。
        窓を作る間はロックを持たない。開いたら比較の窓の Python の側の _ai_attached をロックの外で呼ぶ。
        """
        with self.lock:
            if self._compare is None or self._compare_closed or self._ai_opening:
                return
            compare = self._compare[1]
            current = self._ai
            if current is None:
                self._ai_opening = True
        if current is not None:
            _bring_to_front(current[0])
            return
        attached = None
        try:
            api = self._ai_api(compare)
            window = self._create_window(AI_PANE_TITLE, js_api=api, **self._ai_window)
            api._attach(window)  # 「閉じる」・ファイルのダイアログに使う
            window.events.closed += lambda: self._on_ai_closed(window)
            with self.lock:
                closed = self._compare_closed
                if not closed:
                    self._ai = (window, api)
            if closed:
                _destroy(window)  # 開いている間に比較の窓が閉じた
            else:
                attached = window
        finally:
            with self.lock:
                self._ai_opening = False
        if attached is not None:
            compare._ai_attached(attached)
            with self.lock:
                gone = self._ai is None or self._ai[0] is not attached
            if gone:
                compare._ai_detached()  # つなぐ前に閉じた(閉じた窓を残さない)

    def _on_ai_closed(self, window: Any) -> None:
        """AI の窓が閉じた。記録している窓と同じときだけ外し、比較の窓の右の面に戻す(ロックの外で _ai_detached)。"""
        with self.lock:
            if self._ai is None or self._ai[0] is not window:
                return
            self._ai = None
            compare = self._compare[1] if self._compare is not None else None
        if compare is not None:
            compare._ai_detached()

    # --- 一括比較の窓 ---

    def open_batch(self) -> None:
        """一括比較の窓を開く。開いていれば前に出す。Python の側は開くたびに新しく作る(wx も開くたびに作る)。

        比較の窓が無くても開ける(`python -m driftpilot.gui_web --batch`。そのときは「比較画面で開く」を押せない)。
        """
        with self.lock:
            if self._compare_closed or self._batch_opening:
                return
            current = self._batch
            if current is None:
                self._batch_opening = True
        if current is not None:
            _bring_to_front(current[0])
            return
        try:
            api = self._batch_api(self)
            window = self._create_window(BATCH_TITLE, js_api=api, **self._batch_window)
            api._attach(window)  # 知らせ・閉じる・ファイルのダイアログに使う
            watch_drop = self._watch_drop
            if watch_drop is not None:
                # フォルダとマニフェストのドロップは、読み込みの後に Python の側で受ける(REQ-115)
                window.events.loaded += lambda: watch_drop(window, api._on_drop)
            window.events.closed += lambda: self._on_batch_closed(window)
            with self.lock:
                closed = self._compare_closed
                if not closed:
                    self._batch = (window, api)
            if closed:
                api._closed()  # 開いている間に比較の窓が閉じた
                _destroy(window)
        finally:
            with self.lock:
                self._batch_opening = False

    def _on_batch_closed(self, window: Any) -> None:
        with self.lock:
            batch = self._batch
            if batch is None or batch[0] is not window:
                return
            self._batch = None
        batch[1]._closed()  # 実行中なら中止を頼む。待っている確認は送らずに門を返す


def _bring_to_front(window: Any) -> None:
    """pywebview に「前に出す」の口は無いので show と restore。"""
    try:
        window.show()
        window.restore()
    except Exception:  # noqa: BLE001 - 閉じかけの窓は前に出せなくてよい
        pass


def _destroy(window: Any) -> None:
    try:
        window.destroy()
    except Exception:  # noqa: BLE001 - もう閉じた窓
        pass
