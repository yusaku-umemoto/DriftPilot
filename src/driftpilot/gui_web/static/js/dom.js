// 受け取った文字を描く関数（REQ-103 §5）。REQ-104 以降の画面もこれを使う。
//
// config の行・機器の出力・AI の回答・ファイルの名前など、Python から受け取った文字は HTML として解釈しない。
// 画面に出すときは textContent だけを使い、HTML を解釈する書き方は使わない（ConvoyPilot と同じ決まり。
// 使わない書き方の一覧は docs/notes/pilot-ui-standard.md。テストが static/js/ に無いことを確かめる）。

// el に value を文字として入れる。null と undefined は空にする。
export function text(el, value) {
  el.textContent = value === null || value === undefined ? "" : String(value);
  return el;
}

// 要素を作り、value を文字として入れる。className は空でもよい。
export function make(tag, className, value) {
  const el = document.createElement(tag);
  if (className) {
    el.className = className;
  }
  return text(el, value);
}
