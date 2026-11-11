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

- 起動したときの新しい版の確かめ(ADR-0188 決定2 / REQ-252 §5)。[gui] auto_update_check が真のときだけ、最初の窓
  (比較の窓。--batch だけのときは一括比較の窓)の最初の state() の後に、裏で check_latest を 1 回だけ呼ぶ(1 つの起動で 2 回呼ばない)。
  偽のときは、つなぐ関数を一度も呼ばない。結果が newer のときだけ知らせ(update_notice_view)を置き、比較の窓と一括比較の窓へ
  「状態が変わった」と知らせる。「閉じる」(dismiss_update_notice)の後は、その起動の間は出さない
- 更新の後の起動の知らせ(ADR-0188 決定3 / REQ-253 §7)。--update-failed・--update-started で起動したときは、
  app.run が launch_notice(知らせの文)を渡す。同じ帯(update_notice_view)に先に出し、そのときは自動の確かめの知らせは置かない。
  --update-started のときは started_marker(started-ok のパス)も渡り、比較の窓(--batch だけのときは一括比較の窓)の
  最初の state() の後に 1 回だけ書く(mark_update_started。入れ替える側はこれを見て控えを消す)
- 「全部閉じる」(close_all)。設定の窓の「更新」で新しい版が始まったら、比較・一括比較・AI・設定の窓を全部閉じる
  (窓が全部閉じると webview.start が戻り、app.run の _finish で終わる)
- 画面の色(ADR-0189 / REQ-256 §5)。設定の窓・一括比較の窓・AI の窓を開くたびに、設定ファイルの [gui] theme を読んで、
  create_window の url に付ける(theme.themed_window。"system" は付けない)。「表示」の「保存」の後は apply_theme が、
  開いている窓(比較・一括比較・AI の答え・設定)すべてに色を知らせる

窓を作る関数(create_window)は差し替えられる(テストは偽物を渡す)。省くと webview.create_window(使うときに読む)。
窓の Python の側が自分のロックを持つときは、そのロックの中からここを呼んでよい(ここから窓の Python の側を呼ぶのはロックの外だけ)。
"""

import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from driftpilot import __version__
from driftpilot.gui_web import theme as theme_module
from driftpilot.gui_web.settings_api import SettingsApi
from driftpilot.messages import AI_PANE_TITLE, BATCH_TITLE, SETTINGS_TITLE, update_notice_text
from driftpilot.update import STATE_NEWER, UpdateStatus, check_latest
from driftpilot.update.apply import write_started_ok
from driftpilot.screen_model.worker import RunGate

if TYPE_CHECKING:  # compare_api は session を読むので、型の注記のときだけ読む
    from driftpilot.gui_web.compare_api import CompareApi


def _webview_create_window(*args: Any, **kwargs: Any) -> Any:
    import webview

    return webview.create_window(*args, **kwargs)


def _in_daemon_thread(work: Callable[[], None]) -> None:
    """裏で動かす(デーモンのスレッド。アプリを閉じるときに待たせない)。"""
    threading.Thread(target=work, name="driftpilot-update-check", daemon=True).start()


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
    update_fetch は起動したときの新しい版の確かめでつなぐ関数(REQ-252 §5。None なら check_latest の既定。テストは偽物を渡す)。
    run_in_background はその確かめを裏で動かす関数(既定はデーモンのスレッド。テストはその場で動かす関数を渡す)。
    launch_notice は更新の後の起動の知らせの文(REQ-253 §7。無ければ None)。started_marker は --update-started で
    渡された started-ok のパス(無ければ None)。write_started はそれを書く関数(テストで差し替える)。
    theme_of は窓を開くときに画面の色を読む関数(省くと、そのときの theme.current_theme。読めなければ "system")。
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
        update_fetch: Callable[[str], bytes] | None = None,
        run_in_background: Callable[[Callable[[], None]], None] = _in_daemon_thread,
        launch_notice: str | None = None,
        started_marker: str | None = None,
        write_started: Callable[[str], Any] = write_started_ok,
        theme_of: Callable[[], str] | None = None,
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
        # 起動したときの新しい版の確かめ(REQ-252 §5): つなぐ関数・裏で動かす関数・1 回始めたか・知らせの文・閉じたか
        self._update_fetch = update_fetch
        self._run_in_background = run_in_background
        self._update_started = False
        self._update_notice: str | None = None
        self._update_dismissed = False
        # 更新の後の起動の知らせ・started-ok のパス・書いたか(REQ-253 §7)
        self._launch_notice = launch_notice
        self._started_marker = started_marker
        self._write_started = write_started
        self._started_marked = False
        self._theme_of = theme_of

    # --- 画面の色(ADR-0189) ---

    def _current_theme(self) -> str:
        """窓を開くときの画面の色。差し替えが無ければ、呼ぶときに theme.current_theme を引く。読めなければ "system"。"""
        try:
            return (self._theme_of or theme_module.current_theme)()
        except Exception:  # noqa: BLE001 - 色を読めなくても窓は開く
            return "system"

    def apply_theme(self, theme: str) -> None:
        """開いている窓(比較・一括比較・AI の答え・設定)すべてに、色を当てる(ロックの外で知らせる)。失敗は捨てる。"""
        with self.lock:
            windows = []
            if self._compare is not None and not self._compare_closed:
                windows.append(self._compare[0])
            windows.extend(pair[0] for pair in (self._batch, self._ai, self._settings) if pair is not None)
        theme_module.apply_theme(windows, theme)

    # --- 起動時の絵(ADR-0174 決定2) ---

    def _take_splash(self) -> bool:
        """最初に呼んだ1回だけ真を返し、使ったことを覚える(アプリ1回の起動で1回だけ)。画面からは呼ばない。

        比較の窓(--batch のときは一括比較の窓)が最初の状態を返すときに呼ぶ。キャラクターがオフでも番は使う。
        """
        with self.lock:
            first = not self._splash_taken
            self._splash_taken = True
            return first

    # --- 起動したときの新しい版の確かめ(ADR-0188 決定2 / REQ-252 §5) ---

    def start_update_check(self, enabled: Callable[[], bool]) -> None:
        """最初に呼んだ 1 回だけ、enabled() が真なら裏で check_latest を呼ぶ(窓の Python の側が、ロックを外してから呼ぶ)。

        2 回目からは何もしない(enabled も読まない)。enabled() が偽なら check_latest もつなぐ関数も呼ばない。
        設定を読めないときも偽(auto_update_check_enabled が偽を返す)。
        """
        with self.lock:
            if self._update_started:
                return
            self._update_started = True
        try:
            on = enabled()
        except Exception:  # noqa: BLE001 - 設定を読めないときはつながない
            on = False
        if not on:
            return
        self._run_in_background(self._run_update_check)

    def _run_update_check(self) -> None:
        """裏で 1 回だけ動く。newer のときだけ知らせを置き、両方の窓へ知らせる。latest・unavailable・例外は何も出さない。"""
        try:
            status: UpdateStatus = check_latest(__version__, self._update_fetch)
        except Exception:  # noqa: BLE001 - 自動のときに確かめられなくても知らせない(ADR-0188 決定2)
            return
        if status.state != STATE_NEWER or status.latest is None:
            return
        with self.lock:
            if self._update_dismissed or self._launch_notice is not None:
                return  # 更新の後の起動の知らせを出しているときは置かない
            self._update_notice = update_notice_text(status.latest, status.current)
        self.settings_saved()  # 比較の窓と一括比較の窓に「状態が変わった」と知らせる(同じ口)

    def update_notice_view(self) -> str | None:
        """比較の窓と一括比較の窓の上に出す知らせの文。出さないときは None。更新の後の起動の知らせを先に返す。"""
        with self.lock:
            if self._update_dismissed:
                return None
            return self._launch_notice if self._launch_notice is not None else self._update_notice

    def mark_update_started(self) -> None:
        """--update-started で起動したとき、最初に呼んだ 1 回だけ started-ok を書く(窓の Python の側が、ロックを外してから呼ぶ)。

        比較の窓(--batch だけのときは一括比較の窓)の最初の state() の後に呼ぶ(窓が出た後、の印)。失敗は捨てる。
        """
        with self.lock:
            if self._started_marked or self._started_marker is None:
                return
            self._started_marked = True
            marker = self._started_marker
        try:
            self._write_started(marker)
        except Exception:  # noqa: BLE001 - 書けなくても窓は止めない(入れ替える側が 120 秒で控えを残して終わる)
            pass

    def close_all(self) -> None:
        """比較・一括比較・AI・設定の窓を全部閉じる(設定の窓の「更新」で新しい版が始まった後。ロックの外で呼ぶ)。"""
        with self.lock:
            windows = [pair[0] for pair in (self._settings, self._batch, self._ai, self._compare) if pair is not None]
        for window in windows:
            _destroy(window)

    def dismiss_update_notice(self) -> None:
        """知らせの「閉じる」。その起動の間は出さない。両方の窓へ知らせて描き直させる(ロックの外で呼ぶ)。"""
        with self.lock:
            self._update_dismissed = True
            self._update_notice = None
        self.settings_saved()

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

    def open_settings(self, tab: str | None = None) -> None:
        """設定の窓を開く。開いていれば前に出す(pywebview に「前に出す」の口は無いので show と restore)。

        tab は開くタブ(知らせの「更新…」は "update"。REQ-252 §5)。省けば今と同じ(タブは変えない)。
        新しく作るときは _set_initial_tab を、開いているときは前に出してから _show_tab を呼ぶ(テストの偽物に口が無くても動く)。
        """
        with self.lock:
            if self._compare_closed or self._opening:
                return
            current = self._settings
            if current is None:
                self._opening = True
        if current is not None:
            _bring_to_front(current[0])
            if tab is not None:
                show_tab = getattr(current[1], "_show_tab", None)
                if show_tab is not None:
                    try:
                        show_tab(tab)
                    except Exception:  # noqa: BLE001 - 閉じかけの窓への知らせの失敗は捨てる
                        pass
            return
        try:
            api = self._settings_api()
            if tab is not None:
                set_initial_tab = getattr(api, "_set_initial_tab", None)
                if set_initial_tab is not None:
                    set_initial_tab(tab)
            window = self._create_window(
                SETTINGS_TITLE, js_api=api, **theme_module.themed_window(self._settings_window, self._current_theme())
            )
            api._attach(window)  # 「閉じる」・接続テストの知らせ・ファイルのダイアログに使う
            # 保存できたら、開いている比較の窓と一括比較の窓へ知らせる(ADR-0176 決定4。テストの偽物に口が無くても動く)
            set_on_saved = getattr(api, "_set_on_saved", None)
            if set_on_saved is not None:
                set_on_saved(self.settings_saved)
            # 「表示」の「保存」で色が変わったら、開いている窓すべてに当てる(ADR-0189。テストの偽物に口が無くても動く)
            set_on_theme = getattr(api, "_set_on_theme", None)
            if set_on_theme is not None:
                set_on_theme(self.apply_theme)
            # 「更新」で新しい版が始まったら、窓を全部閉じる(REQ-253 §6。テストの偽物に口が無くても動く)
            set_close_all = getattr(api, "_set_close_all", None)
            if set_close_all is not None:
                set_close_all(self.close_all)
            window.events.closed += lambda: self._on_settings_closed(window, api)
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

    def _on_settings_closed(self, window: Any, api: Any = None) -> None:
        """設定の窓が閉じた(画面の「閉じる」でも、窓の上の × でも)。記録を外し、Python の側の _on_closed をロックの外で呼ぶ。

        _on_closed は「更新する」の準備の途中なら止め、確かめを待っている準備を捨てる(REQ-253 §5。テストの偽物に口が無くても動く)。
        """
        with self.lock:
            if self._settings is not None and self._settings[0] is window:
                self._settings = None
        on_closed = getattr(api, "_on_closed", None)
        if on_closed is None:
            return
        try:
            on_closed()
        except Exception:  # noqa: BLE001 - 閉じた窓の後始末の失敗は捨てる(準備のフォルダーは次の起動の掃除で消える)
            pass

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
            window = self._create_window(
                AI_PANE_TITLE, js_api=api, **theme_module.themed_window(self._ai_window, self._current_theme())
            )
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
            window = self._create_window(
                BATCH_TITLE, js_api=api, **theme_module.themed_window(self._batch_window, self._current_theme())
            )
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
