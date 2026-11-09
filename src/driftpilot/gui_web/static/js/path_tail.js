// パスを後ろ（ファイル名の側）から見せる部品（REQ-211 §4 / ADR-0153）。一括比較・比較・設定の窓が使う。
//
// 文字の向きを変える CSS（direction・unicode-bidi）は使わない（記号や括弧の並びが崩れるため）。
// - 文字の欄（<input>）: フォーカスが無いときだけ、いちばん右までスクロールする（打っている途中は動かさない）
// - 読むだけの場所: .pilot-path（flex で右に寄せ、左をはみ出させて切る）の中の span に文字を入れる。
//   切れているものには data-clipped="true" を付け、CSS が左端に「…」を出す。全文は title
// - 一括比較の窓は namePathCell で、ファイル名だけを太字（strong.pilot-path-name）にする（REQ-247 / ADR-0184 決定4）
// 文字は dom.js の make（textContent）だけで入れる。

import { make } from "./dom.js";

// 欄の後ろを見せる。フォーカスがあるときは動かさない。
export function showTail(input) {
  if (document.activeElement !== input) {
    input.scrollLeft = input.scrollWidth;
  }
}

// フォーカスが外れたときと窓の大きさが変わったときに後ろを見せる（欄ごとに 1 回だけ呼ぶ）。
export function bindTail(input) {
  input.addEventListener("blur", () => showTail(input));
  window.addEventListener("resize", () => showTail(input));
  showTail(input);
}

// 読むだけのパスの場所（div.pilot-path > span）。title に全文を入れる。
// start が真なら箱に pilot-path--start を足し、ファイル名を左に寄せる（切れているときは今と同じく後ろを見せる。
// 一括比較の窓の片方だけのファイルの 2 つの表。REQ-233 / ADR-0171 決定2）。偽のときは今と同じ形を作る。
export function pathCell(value, start = false) {
  const box = make("div", "pilot-path", null);
  if (start) {
    box.classList.add("pilot-path--start");
  }
  const inner = make("span", "", value);
  box.title = inner.textContent;
  box.append(inner);
  return box;
}

// pathCell と同じ箱（div.pilot-path > span。start・title も同じ）で、ファイル名だけを太字にする（REQ-247 / ADR-0184 決定4。
// 一括比較の窓の結果の表・組の候補・片方だけのファイルの表が使う）。中の span には、フォルダーの部分の span と
// ファイル名の strong.pilot-path-name を順に入れる。区切りは最後の「/」か「\」のうち後ろのもの。
// 区切りが無ければ全体をファイル名にし、区切りが最後の文字（ファイル名が空）なら全体をフォルダーの部分にして太字を作らない。
// 箱の最初の子は外側の span のままなので、fitPaths の判定（span の scrollWidth）はそのまま使える。
export function namePathCell(value, start = false) {
  const box = make("div", "pilot-path", null);
  if (start) {
    box.classList.add("pilot-path--start");
  }
  const whole = value === null || value === undefined ? "" : String(value);
  const cut = Math.max(whole.lastIndexOf("/"), whole.lastIndexOf("\\")) + 1;
  const folder = whole.slice(0, cut);
  const name = whole.slice(cut);
  const inner = make("span", "", null);
  if (folder !== "") {
    inner.append(make("span", "", folder));
  }
  if (name !== "") {
    inner.append(make("strong", "pilot-path-name", name));
  }
  box.title = whole;
  box.append(inner);
  return box;
}

// root の中の .pilot-path のうち、切れているものに data-clipped="true" を付ける（隠れている行は幅が 0 なので外す）。
export function fitPaths(root) {
  for (const box of root.querySelectorAll(".pilot-path")) {
    const inner = box.firstElementChild;
    if (inner !== null && box.clientWidth > 0 && inner.scrollWidth > box.clientWidth) {
      box.dataset.clipped = "true";
    } else {
      delete box.dataset.clipped;
    }
  }
}
