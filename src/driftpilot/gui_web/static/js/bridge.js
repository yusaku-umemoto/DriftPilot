// Python と画面のやりとり（ADR-0074 決定3 / REQ-103 §4）。対になる Python の側は driftpilot/gui_web/bridge.py。
//
// - Python から画面: Python は window.driftpilot.receive(event) だけを呼ぶ。event は {type: 種類, ...}。
//   on(type, handler) で登録した関数へ渡す。知らない種類は捨てる
// - 画面から Python: call(name, ...args) で js_api のメソッドを呼ぶ。返るのは {ok: true, ...} か {ok: false, message}
//
// 文字から JavaScript を作って動かすこと（eval など）はしない。CSP の 'unsafe-eval' は pywebview の内部のためだけ。

const handlers = new Map();

// 知らせの種類 type に、受ける関数を足す。
export function on(type, handler) {
  if (!handlers.has(type)) {
    handlers.set(type, []);
  }
  handlers.get(type).push(handler);
}

function receive(event) {
  if (event === null || typeof event !== "object" || typeof event.type !== "string") {
    return;
  }
  for (const handler of handlers.get(event.type) || []) {
    handler(event);
  }
}

window.driftpilot = Object.freeze({ receive });

function apiReady() {
  return Boolean(window.pywebview && window.pywebview.api && typeof window.pywebview.api.app_info === "function");
}

// window.pywebview.api ができるまで待つ。pywebviewready の知らせがもう来ていれば、すぐに返す。
export function ready() {
  return new Promise((resolve) => {
    if (apiReady()) {
      resolve(window.pywebview.api);
      return;
    }
    window.addEventListener("pywebviewready", () => resolve(window.pywebview.api), { once: true });
  });
}

// js_api のメソッド name を呼ぶ。
export async function call(name, ...args) {
  const api = await ready();
  if (typeof api[name] !== "function") {
    throw new Error(`js_api に ${name} がありません`);
  }
  return api[name](...args);
}
