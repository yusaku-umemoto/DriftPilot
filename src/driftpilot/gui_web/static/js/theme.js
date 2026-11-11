// 窓の画面の色（ADR-0189 / REQ-256 §5）。対になる Python の側は driftpilot/gui_web/theme.py。
//
// - 窓を開くとき: URL の # の後ろ（#theme=light・#theme=dark）を読み、<html> の data-theme に当てる。"system" は URL に付かない
//   （data-theme を付けず、OS の設定に従う）。pilot.css の前に blocking="render" で読み込み、最初の描画の前に当てる
// - 「表示」の「保存」の後: Python が {type: "theme", theme: ...} を知らせる。light・dark なら data-theme を付け、
//   system（とそれ以外）なら外す
//
// 受け取った文字は、light・dark のどちらかと一致するときだけ属性の値に使う（HTML やコードとして動かさない）。

import { on } from "./bridge.js";

const root = document.documentElement;

export function applyTheme(theme) {
  if (theme === "light" || theme === "dark") {
    root.setAttribute("data-theme", theme);
  } else {
    root.removeAttribute("data-theme");
  }
}

applyTheme(new URLSearchParams(location.hash.slice(1)).get("theme"));
on("theme", (event) => applyTheme(event.theme));
