// AI の窓（ADR-0160 / REQ-220 §5.2）。対になる Python の側は driftpilot/gui_web/ai_pane_api.py（状態は比較の窓の Python の側が持つ）。
//
// することは比較の窓と同じ3つだけ: data-text の鍵の文を取って入れる・Python が返した状態（view）を描く・AI の面の操作を Python に渡す。
// AI の面の描き方は js/ai_view.js（比較の窓の右の面と同じ部品）。受け取った文字は textContent だけで描く。
// 結果の行は受け取らない。view.result_revision が変わったときだけ ai_result()（AI の答えの部分だけ）を呼ぶ。
// 比較の設定の欄は無い（form は Python が持つ最後の値を使う）。入力・比較・保存はこの窓からは操作しない。

import { call, on } from "./bridge.js";
import { notice } from "./dialog.js";
import { text } from "./dom.js";
import { createAiView } from "./ai_view.js";
import "./drop.js"; // ドロップしても既定の動き（ファイルを開く）を止めるだけ。印の要素が無いので Python へは送らない

const $ = (id) => document.getElementById(id);
const statusMain = $("status-main");

let view = null; // いま描いている状態
// 描いた状態の番号・描いた結果の番号・出した知らせの番号
const shown = { revision: 0, result: 0, notice: null };

// data-text・data-placeholder・data-label の鍵の文を messages.py から取って入れる（HTML には文を書かない）。取れなければ false。
// data-title の文は title にだけ入れる（答えの吹き出しの顔。読み上げの名前にはしない。ADR-0181 決定1）。
async function fillTexts() {
  const elements = Array.from(document.querySelectorAll("[data-text]"));
  const hinted = Array.from(document.querySelectorAll("[data-placeholder]"));
  const labelled = Array.from(document.querySelectorAll("[data-label]"));
  const titled = Array.from(document.querySelectorAll("[data-title]"));
  const keys = Array.from(
    new Set([
      ...elements.map((el) => el.dataset.text),
      ...hinted.map((el) => el.dataset.placeholder),
      ...labelled.map((el) => el.dataset.label),
      ...titled.map((el) => el.dataset.title),
    ]),
  );
  const result = await call("messages", keys);
  if (!result.ok) {
    text(statusMain, result.message);
    return false;
  }
  const texts = result.messages;
  for (const el of elements) {
    text(el, texts[el.dataset.text]);
  }
  for (const el of hinted) {
    el.placeholder = texts[el.dataset.placeholder];
  }
  for (const el of labelled) {
    el.title = texts[el.dataset.label];
    el.setAttribute("aria-label", texts[el.dataset.label]);
  }
  for (const el of titled) {
    el.title = texts[el.dataset.title];
  }
  return true;
}

function render(next) {
  if (next.revision < shown.revision) {
    return; // 後から届いた古い状態は捨てる
  }
  shown.revision = next.revision;
  view = next;
  text(statusMain, next.status.main);
  if (next.result_revision !== shown.result) {
    loadResult();
  }
  aiView.render(next);
  if (next.notice !== null && next.notice.id !== shown.notice) {
    shown.notice = next.notice.id;
    notice(next.notice.title, next.notice.body, next.notice.ok);
  }
}

// 結果の番号が変わったら ai_result() を呼ぶ。呼んでいる途中なら、終わってからもう1回。
let resultLoading = false;
let resultAgain = false;

async function loadResult() {
  if (resultLoading) {
    resultAgain = true;
    return;
  }
  resultLoading = true;
  try {
    do {
      resultAgain = false;
      const answer = await call("ai_result");
      if (!answer.ok) {
        text(statusMain, answer.message);
        break;
      }
      if (answer.result.revision !== shown.result) {
        shown.result = answer.result.revision;
        aiView.drawResult(answer.result);
      }
      render(answer.view);
    } while (resultAgain);
  } finally {
    resultLoading = false;
  }
}

// 返ってきたものを出す。知らせは出してから描く（比較の窓と同じ順）。
async function handle(result) {
  if (!result.ok) {
    text(statusMain, result.message);
    return;
  }
  if (result.notice) {
    await notice(result.notice.title, result.notice.body, result.notice.ok);
  }
  render(result.view);
}

async function act(name, ...args) {
  if (view === null) {
    return; // まだ最初の状態が届いていない
  }
  await handle(await call(name, ...args));
}

// Python からの知らせ（"compare.changed"）で state() を呼び直す。呼んでいる途中なら、終わってからもう1回。
let loading = false;
let again = false;

async function reload() {
  if (loading) {
    again = true;
    return;
  }
  loading = true;
  try {
    do {
      again = false;
      await handle(await call("state"));
    } while (again);
  } finally {
    loading = false;
  }
}

// AI の面（比較の窓の右の面と同じ部品）。form は渡さない（Python が持つ最後の値を使う）
const aiView = createAiView({ $, call, act, on, afterPreview: handle });
const { drawPartial } = aiView;

on("compare.changed", () => reload());
on("compare.partial", drawPartial); // 届いたところまでの答え（ADR-0157 決定5）
// 設定の窓でプロファイルを変えた後や、一括比較が門を取った後に合わせる（窓が前に来たら状態を読み直す）
window.addEventListener("focus", () => {
  if (view !== null) {
    reload();
  }
});

if (await fillTexts()) {
  await reload();
}
