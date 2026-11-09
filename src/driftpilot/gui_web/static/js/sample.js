// 部品の見本のページ（REQ-103 §5）。使う js_api は app_info と messages の2つだけ。
// 表の行の選択・並べ替えの印・タブの切り替え・ダイアログは見た目だけ（処理はしない）。

import { call, on } from "./bridge.js";
import { confirm, notice } from "./dialog.js";
import { panel } from "./dialog.js"; // 大きいダイアログ（REQ-105）
import { make, text } from "./dom.js";

const status = document.getElementById("status");
// 要素に結び付かない文（ステータスの行・ダイアログに後から出すもの）
const READY = "sample.status.ready";
const DIALOG_TEXTS = [
  "sample.dialog.confirm.title",
  "sample.dialog.confirm.body",
  "sample.dialog.notice.title",
  "sample.dialog.notice.body",
  "sample.dialog.yes",
  "sample.dialog.no",
  "sample.dialog.ok",
  "sample.dialog.answer.yes",
  "sample.dialog.answer.no",
  "sample.compare.paste.title",
  "sample.compare.paste.prompt",
  "sample.compare.paste.ok",
  "sample.compare.paste.cancel",
];

// data-text の鍵の文を messages.py から取って入れる（HTML には文を書かない）。読むだけの欄の文は data-value の鍵。取れなければ null。
async function fillTexts() {
  const elements = Array.from(document.querySelectorAll("[data-text]"));
  const values = Array.from(document.querySelectorAll("[data-value]"));
  const keys = Array.from(
    new Set([...elements.map((el) => el.dataset.text), ...values.map((el) => el.dataset.value), READY, ...DIALOG_TEXTS]),
  );
  const result = await call("messages", keys);
  if (!result.ok) {
    text(status, result.message);
    return null;
  }
  for (const el of elements) {
    text(el, result.messages[el.dataset.text]);
  }
  for (const el of values) {
    el.value = result.messages[el.dataset.value];
  }
  return result.messages;
}

async function showAppInfo() {
  const info = await call("app_info");
  if (!info.ok) {
    text(status, info.message);
    return;
  }
  text(document.getElementById("info-driftpilot"), info.driftpilot);
  text(document.getElementById("info-python"), info.python);
  text(document.getElementById("info-renderer"), info.renderer);
}

function setUpTable() {
  const table = document.getElementById("sample-table");
  for (const row of table.tBodies[0].rows) {
    row.addEventListener("click", () => {
      for (const other of table.tBodies[0].rows) {
        other.setAttribute("aria-selected", String(other === row));
      }
    });
  }
  for (const th of table.querySelectorAll("th[aria-sort]")) {
    th.addEventListener("click", () => {
      const next = th.getAttribute("aria-sort") === "ascending" ? "descending" : "ascending";
      for (const other of table.querySelectorAll("th[aria-sort]")) {
        other.setAttribute("aria-sort", other === th ? next : "none");
      }
    });
  }
}

function setUpTabs() {
  const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
  for (const tab of tabs) {
    tab.addEventListener("click", () => {
      for (const other of tabs) {
        const selected = other === tab;
        other.setAttribute("aria-selected", String(selected));
        document.getElementById(other.getAttribute("aria-controls")).hidden = !selected;
      }
    });
  }
}

// 確かめ（ボタン2つ）と知らせ（ボタン1つ）のダイアログ（REQ-104 §5）。選んだほうをステータスの行に出す。
function setUpDialogs(messages) {
  document.getElementById("sample-confirm").addEventListener("click", async () => {
    const yes = await confirm(
      messages["sample.dialog.confirm.title"],
      messages["sample.dialog.confirm.body"],
      messages["sample.dialog.yes"],
      messages["sample.dialog.no"],
    );
    text(status, messages[yes ? "sample.dialog.answer.yes" : "sample.dialog.answer.no"]);
  });
  document.getElementById("sample-notice").addEventListener("click", () =>
    notice(messages["sample.dialog.notice.title"], messages["sample.dialog.notice.body"], messages["sample.dialog.ok"]),
  );
  // 大きいダイアログ（.pilot-dialog--wide。REQ-105 §5）。中の欄が残りの高さを使う。打った文字は使わない
  document.getElementById("sample-wide").addEventListener("click", async () => {
    const input = make("textarea", "pilot-input pilot-mono pilot-nowrap", null);
    await panel(
      messages["sample.compare.paste.title"],
      [make("p", "", messages["sample.compare.paste.prompt"]), input],
      messages["sample.compare.paste.ok"],
      messages["sample.compare.paste.cancel"],
      input,
    );
  });
}

// Python からの "status" の知らせで、ステータスの行の文を変える（REQ-104 以降の知らせの見本）。
on("status", (event) => text(status, event.text));

setUpTable();
setUpTabs();
const messages = await fillTexts();
if (messages !== null) {
  text(status, messages[READY]);
  setUpDialogs(messages);
  await showAppInfo();
}
