// 確かめと知らせのダイアログ（REQ-104 §5）と、中身を置けるダイアログ（REQ-105 §4）。ほかの画面もこれを使う。
//
// 窓の中に <dialog class="pilot-dialog"> を作って showModal() で開き、閉じたら消す。
// 安全なほう（「いいえ」「送らない」「OK」「キャンセル」）を右に置き、開いたときにフォーカスする。Esc で閉じたら安全なほうを選んだのと同じ。
// 3択の確かめ（choose。REQ-112）だけは、開いたときのフォーカスを Python が決める（Esc は「キャンセル」）。
// 題・本文・ボタンの名前は、Python から受け取った文を textContent で入れる（dom.js の make）。
// pywebview の create_confirmation_dialog は使わない（既定のボタンを選べず、OS で見た目が違うため）。

import { make } from "./dom.js";

// buttons は [{label, value}] を左から順に。safe は安全なほうの番号。選んだボタンの value で解決する。
// body は本文の要素。options.wide なら大きいダイアログ（.pilot-dialog--wide）、options.focus は開いたときにフォーカスする要素、
// options.focusButton は開いたときにフォーカスするボタンの番号（どちらも省くと安全なほうのボタン）。
function open(title, body, buttons, safe, options = {}) {
  return new Promise((resolve) => {
    const dialog = make("dialog", "pilot-dialog", null);
    if (options.wide) {
      dialog.classList.add("pilot-dialog--wide");
    }
    const row = make("div", "pilot-dialog-buttons", null);
    let answer = buttons[safe].value;
    const elements = buttons.map((button) => {
      const el = make("button", "pilot-button pilot-button--secondary", button.label);
      el.type = "button";
      el.addEventListener("click", () => {
        answer = button.value;
        dialog.close();
      });
      row.append(el);
      return el;
    });
    dialog.append(make("h2", "pilot-dialog-title", title), body, row);
    // Esc（cancel の知らせ）は安全なほう
    dialog.addEventListener("cancel", () => {
      answer = buttons[safe].value;
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      resolve(answer);
    });
    document.body.append(dialog);
    dialog.showModal();
    (options.focus || elements[options.focusButton ?? safe]).focus();
  });
}

// 3つ以上のボタンから選ぶ確かめ（上書きの確かめ。REQ-112）。choices は [{value, label}] を左から順に。
// 開いたときは focusValue のボタンにフォーカスし、Esc は cancelValue を選んだのと同じ。選んだボタンの value で解決する。
export function choose(title, body, choices, focusValue, cancelValue) {
  const buttons = choices.map((choice) => ({ label: choice.label, value: choice.value }));
  const safe = buttons.findIndex((button) => button.value === cancelValue);
  const focusButton = buttons.findIndex((button) => button.value === focusValue);
  return open(title, make("p", "pilot-dialog-body", body), buttons, safe, { focusButton: focusButton < 0 ? safe : focusButton });
}

// はい・いいえの確かめ。「はい」なら true。既定（フォーカス・Esc）は noLabel のほう。
export function confirm(title, body, yesLabel, noLabel) {
  return open(title, make("p", "pilot-dialog-body", body), [{ label: yesLabel, value: true }, { label: noLabel, value: false }], 1);
}

// OK だけの知らせ。OK を押す（または Esc）まで待つ。
export async function notice(title, body, okLabel) {
  await open(title, make("p", "pilot-dialog-body", body), [{ label: okLabel, value: true }], 0);
}

// 中身を置けるボタンが 1 つの大きいダイアログ（一括比較の「内容を確かめる…」。REQ-213 §5）。parts は本文に縦に並べる要素。
// closeLabel のボタンか Esc で閉じるまで待つ。何も選ばせない（送る・送らないのボタンは無い）。
export async function view(title, parts, closeLabel) {
  const body = make("div", "pilot-dialog-body", null);
  body.append(...parts);
  await open(title, body, [{ label: closeLabel, value: true }], 0, { wide: true });
}

// 中身を置ける大きいダイアログ（貼り付け・送信前のプレビュー）。parts は本文に縦に並べる要素。
// 2つのボタンのうち、右（noLabel）が安全なほう（Esc も同じ）。左（yesLabel）を押したら true。
// focus は開いたときにフォーカスする要素（省くと安全なほうのボタン）。
export function panel(title, parts, yesLabel, noLabel, focus = null) {
  const body = make("div", "pilot-dialog-body", null);
  body.append(...parts);
  return open(title, body, [{ label: yesLabel, value: true }, { label: noLabel, value: false }], 1, { wide: true, focus });
}
