// 音声入力のマイクのボタン（ADR-0233 決定3 / REQ-341）。対になる Python の側は driftpilot/gui_web/voice.py（状態は比較の窓の Python の側が 1 つ持つ）。
// 比較の窓（compare.js）と AI の窓（ai_pane.js）の両方が、ai_view.js の createAiView から使う。
//
// 録音・文字にすることは PartsPilot が行う。ここですることは次の 4 つだけ:
// - Python が返した状態（voice_state・"voice.changed"）でボタン・注記・音量の棒・経過の秒を描く（文はどれも Python が決める）
// - ボタンの押し方（view.press）に合わせて、押した・離した事象を voice_press に渡す
//   toggle: click で "toggle"。hold: pointerdown で "down"、pointerup・pointercancel・lostpointercapture（ボタンの外で離したとき）で "up"
// - 録音中の Esc（その窓のどこでも）を voice_cancel に渡す
// - "voice.text" で届いた文字を、入力欄のカーソルの位置に（選んだ文字があれば置き換えて）入れる。送信しない。
//   ただし AI に送っている間（options.sending() が真）に届いた文字は、欄に入れずに届いた順にためる。送った文が欄に残っていて、
//   答えが届くと欄が空になるため。送り終わった view を描いて欄を空にする処理が済んだ後に flush() が、ためた文字を順に
//   そのときのカーソルの位置へ入れる（失敗・中止で欄が空にならなかったときも同じ）
// - "voice.corrected"（LLM で文脈を踏まえて直した文字。REQ-342）で、入れた部分（位置と長さで覚える）を置き換える。
//   入れた後に欄が書き換えられていなければ置き換え、changed が真なら小さく「直す前に戻す」を出す。書き換えられていれば置き換えず
//   「LLM の直しを使う」を出す（押したとき、入れた位置の文字がまだ元のままなら置き換える。違えば何もしない。末尾には足さない）。
//   rejected があれば何もしない。直した文字に <secret:N> の記号があっても、そのまま入れる。2 つのボタンは、次の録音を始める・送信する
//   （dismiss）・自分以外の入力で消す
//
// ★ 受け取った文字は textContent と setRangeText だけで入れる（HTML として解釈しない）。話した文字は保存も記録もしない。

import { text } from "./dom.js";

const SECONDS = 60;

// options: $（id で要素を取る）・call（Python のメソッドを呼ぶ口。form は渡さない）・on（知らせの登録）・question（入力欄）
export function createVoice(options) {
  const { $, call, on, question, sending } = options;
  const pending = []; // AI に送っている間に届いた文字（届いた順）。{id, text, revertAfter}
  const placed = new Map(); // 入れた部分（発話の id → {start, length, text, after}）。古いものから捨てる
  const PLACED_LIMIT = 8;
  let own = false; // この部品が起こした input の最中（自分以外の入力と見分ける）
  let offered = null; // 出している「直す前に戻す」「LLM の直しを使う」の持ち物 {kind, id, text}
  let view = null; // いま描いている状態
  let shownRevision = -1;
  let started = false;
  let held = false; // hold で押している最中
  let timer = null;
  let beganAt = 0;
  let levelRevision = null;

  const button = () => $("voice-button");

  function stopTimer() {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
  }

  function showElapsed() {
    const seconds = Math.max(0, Math.floor((Date.now() - beganAt) / 1000));
    text($("voice-elapsed"), `${Math.floor(seconds / SECONDS)}:${String(seconds % SECONDS).padStart(2, "0")}`);
  }

  function setLevel(rms) {
    // 小さな音も見えるように平方根で伸ばす（0〜1 に収める）
    const value = Math.min(1, Math.sqrt(Math.max(0, Number(rms) || 0)));
    $("voice-level-bar").style.width = `${Math.round(value * 100)}%`;
  }

  function render(next) {
    if (next.revision < shownRevision) {
      return; // 後から届いた古い状態は捨てる
    }
    shownRevision = next.revision;
    view = next;
    const recording = next.state === "recording";
    if (recording) {
      dismiss(); // 次の録音を始めたら、2 つのボタンを消す
    }
    button().hidden = next.state === "hidden";
    button().disabled = !next.can_press;
    button().title = next.title;
    button().classList.toggle("pilot-voice-button--recording", recording);
    button().setAttribute("aria-pressed", String(recording));
    $("voice-note").hidden = !next.note;
    text($("voice-note"), next.note);
    $("voice-limit-note").hidden = !next.limit_note;
    text($("voice-limit-note"), next.limit_note);
    $("voice-status").hidden = !recording;
    if (recording) {
      // 録音が始まった（recording の知らせで番号が進む）ときに、音量の棒と経過の秒を 0 に戻す
      if (next.level_revision !== levelRevision || timer === null) {
        levelRevision = next.level_revision;
        beganAt = Date.now();
        setLevel(0);
        showElapsed();
      }
      if (timer === null) {
        timer = setInterval(showElapsed, 250);
      }
    } else {
      stopTimer();
      levelRevision = null;
      held = false;
    }
  }

  async function press(action) {
    if (view === null) {
      return;
    }
    const result = await call("voice_press", action);
    if (result.ok) {
      render(result.voice);
    }
  }

  async function cancel() {
    const result = await call("voice_cancel");
    if (result.ok) {
      render(result.voice);
    }
  }

  // 入力の変化の処理（今の欄の input）を走らせる。自分が起こした input は、自分以外の入力とは見なさない
  function notifyInput() {
    own = true;
    try {
      question.dispatchEvent(new Event("input", { bubbles: true }));
    } finally {
      own = false;
    }
  }

  // 入力欄のカーソルの位置に入れる（選んだ文字があれば置き換える）。送信しない。入力の変化の処理は今の欄の input をそのまま走らせる。
  // id があれば、入れた部分を位置と長さで覚える（LLM の直しで置き換えるため）。入れた部分の記録を返す（覚えなければ null）
  function insert(value, id = null) {
    if (typeof value !== "string" || value === "") {
      return null;
    }
    const start = question.selectionStart ?? question.value.length;
    const end = question.selectionEnd ?? start;
    question.focus();
    question.setRangeText(value, start, end, "end");
    notifyInput();
    if (id === null) {
      return null;
    }
    const record = { start, length: value.length, text: value, after: question.value };
    placed.set(id, record);
    while (placed.size > PLACED_LIMIT) {
      placed.delete(placed.keys().next().value);
    }
    return record;
  }

  // 入れた部分を value で置き換える（位置と長さで）。置き換えた後の記録に直す。呼ぶ前に、置き換えてよいか確かめておく
  function replacePlaced(record, value) {
    question.setRangeText(value, record.start, record.start + record.length, "preserve");
    record.length = value.length;
    record.text = value;
    record.after = question.value;
    notifyInput();
  }

  function hideOffered() {
    $("voice-revert").hidden = true;
    $("voice-use-correction").hidden = true;
  }

  // 「直す前に戻す」「LLM の直しを使う」を消す（次の録音を始める・送信する・自分以外の入力）
  function dismiss() {
    offered = null;
    hideOffered();
  }

  function offer(kind, id, value) {
    hideOffered();
    offered = { kind, id, text: value };
    $(kind === "revert" ? "voice-revert" : "voice-use-correction").hidden = false;
  }

  function isId(value) {
    return typeof value === "string" || (typeof value === "number" && Number.isFinite(value));
  }

  // 直した文字（voice.corrected）。置き換えず何も出さないのは rejected が null でないとき
  function corrected(event) {
    if (!isId(event.id) || typeof event.text !== "string") {
      return;
    }
    if (event.rejected !== null && event.rejected !== undefined) {
      return; // 置き換えず、何も出さない
    }
    const before = typeof event.before === "string" ? event.before : null;
    // (1) まだ欄に入れずにためてあるとき: 入れる前の文字を直した文字に替える（書き換えられていない扱い）
    const queued = pending.find((item) => item.id === event.id);
    if (queued !== undefined) {
      queued.revertAfter = event.changed === true ? { before: before ?? queued.text } : null;
      queued.text = event.text;
      return;
    }
    const record = placed.get(event.id);
    if (record === undefined) {
      return;
    }
    if (question.value === record.after) {
      // (2) 入れた後に書き換えられていない: 置き換える
      const original = before ?? record.text;
      replacePlaced(record, event.text);
      if (event.changed === true) {
        offer("revert", event.id, original);
      }
    } else {
      // (3) 書き換えられている: 置き換えず、「LLM の直しを使う」を出す
      offer("use", event.id, event.text);
    }
  }

  // 「直す前に戻す」: 欄が置き換えた直後のままのときだけ、入れた部分を直す前に戻す
  function revert() {
    if (offered === null || offered.kind !== "revert") {
      return;
    }
    const record = placed.get(offered.id);
    if (record !== undefined && question.value === record.after) {
      replacePlaced(record, offered.text);
      dismiss();
    }
  }

  // 「LLM の直しを使う」: 入れた位置の文字がまだ元のままのときだけ置き換える（違えば何もしない。末尾には足さない）
  function useCorrection() {
    if (offered === null || offered.kind !== "use") {
      return;
    }
    const record = placed.get(offered.id);
    if (record === undefined) {
      return;
    }
    const current = question.value.slice(record.start, record.start + record.length);
    if (current === record.text) {
      replacePlaced(record, offered.text);
      dismiss();
    }
  }

  function hold() {
    return view !== null && view.press === "hold";
  }

  function bind() {
    const el = button();
    // toggle: 押すたびに（キーボードの Enter・Space の click も）。hold のときは click では何もしない（pointer と keydown・keyup で扱う）
    el.addEventListener("click", (event) => {
      if (!hold()) {
        press("toggle");
      }
      event.preventDefault();
    });
    // hold: 押したとき start、離したとき（ボタンの外で離したときも）stop
    el.addEventListener("pointerdown", (event) => {
      if (!hold() || event.button !== 0 || held) {
        return;
      }
      held = true;
      try {
        el.setPointerCapture(event.pointerId);
      } catch (_error) {
        // 取れなくても、pointerup・pointercancel で止まる
      }
      press("down");
    });
    for (const type of ["pointerup", "pointercancel", "lostpointercapture"]) {
      el.addEventListener(type, () => {
        if (held) {
          held = false;
          press("up");
        }
      });
    }
    // hold のキーボード操作（Space・Enter を押している間だけ録る）
    el.addEventListener("keydown", (event) => {
      if (hold() && (event.key === " " || event.key === "Enter")) {
        event.preventDefault();
        if (!event.repeat && !held) {
          held = true;
          press("down");
        }
      }
    });
    el.addEventListener("keyup", (event) => {
      if (hold() && (event.key === " " || event.key === "Enter")) {
        event.preventDefault();
        if (held) {
          held = false;
          press("up");
        }
      }
    });
    // 録音中の Esc（その窓のどこでも）は取り消し
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && !event.isComposing && view !== null && view.state === "recording") {
        cancel();
      }
    });
    on("voice.changed", (event) => render(event.voice));
    on("voice.level", (event) => {
      if (view !== null && view.state === "recording") {
        setLevel(event.rms);
      }
    });
    on("voice.text", (event) => {
      // 直す頼み（correcting）の発話だけ、入れた部分を覚える（id は発話ごと）
      const id = event.correcting === true && isId(event.id) ? event.id : null;
      if (typeof event.text === "string" && event.text !== "" && sending()) {
        pending.push({ id, text: event.text, revertAfter: null });
      } else {
        insert(event.text, id);
      }
    });
    on("voice.corrected", corrected);
    $("voice-revert").addEventListener("click", revert);
    $("voice-use-correction").addEventListener("click", useCorrection);
    // 自分以外の入力（打つ・貼る・消す）があったら、2 つのボタンを消す（自分が起こした input は own で見分ける）
    question.addEventListener("input", () => {
      if (!own) {
        dismiss();
      }
    });
  }

  // 最初の状態を取る（Python の側が、裏で PartsPilot を探して起動する）。1 回だけ呼ぶ
  async function start() {
    if (started) {
      return;
    }
    started = true;
    bind();
    const result = await call("voice_state");
    if (result.ok) {
      render(result.voice);
    }
  }

  // ためた文字を順に入れる（送り終わったときだけ。ai_view.js の render の最後、欄を空にする処理の後に呼ぶ）
  function flush() {
    while (pending.length > 0 && !sending()) {
      const item = pending.shift();
      const record = insert(item.text, item.id);
      if (record !== null && item.revertAfter) {
        offer("revert", item.id, item.revertAfter.before);
      }
    }
  }

  return { start, render, flush, dismiss };
}
