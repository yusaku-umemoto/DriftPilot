// AI の答えの吹き出しとコピーの部品（REQ-206 / ADR-0148 決定8。REQ-213 で比較の窓と一括比較の窓の両方で使うように compare.js から移した）。
//
// ★ 要素は dom.js の make（createElement と textContent）だけで作る。文字列を HTML として解釈する書き方は使わない。
// 属性（title・aria-label）に入れるのは、呼ぶ側が渡す決まった文（messages.py の文）だけ。AI の答えは属性に入れない
// （答えはクリップボードと選択のコピーにだけ使う）。答えの中身を描くのは answer_view.js の renderAnswer。

import { make } from "./dom.js";

// コピーのアイコン（2つ重ねた四角を CSS で描く。文字は入れない）。
export function copyIcon() {
  return make("span", "pilot-icon-copy", null);
}

// 答えの吹き出しに付けるコピーのボタン。value は Python が渡した文（赤字の印を外した答え）。
// label は title と aria-label に入れる決まった文（「コピー」）。
export function copyButton(value, label) {
  const button = make("button", "pilot-icon-button", null);
  button.type = "button";
  button.title = label;
  button.setAttribute("aria-label", label);
  button.append(copyIcon());
  button.addEventListener("click", () => copyText(value, button));
  return button;
}

// 吹き出し1つ。kind は "ai"（左）か "me"（右）。label はやり取りの種類の名前（読み上げ用の決まった文）。
export function bubble(kind, label) {
  const el = make("div", `pilot-bubble pilot-bubble--${kind}`, null);
  el.setAttribute("aria-label", label);
  return el;
}

// 文をクリップボードへ入れる。navigator.clipboard が使えない・断られたときは、読むだけの欄を選んでコピーする（選択のコピー）。
export async function copyText(value, button) {
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(value);
      return;
    }
  } catch (_error) {
    // 下の選択のコピーへ
  }
  const buffer = make("textarea", "pilot-copy-buffer", null);
  buffer.value = value;
  buffer.readOnly = true;
  document.body.append(buffer);
  buffer.select();
  document.execCommand("copy");
  buffer.remove();
  button.focus();
}
