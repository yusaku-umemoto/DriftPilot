// ファイルのドロップの受け方（REQ-205 / ADR-0147）。比較の窓（compare.js）と一括比較の窓（batch.js）が読む。
//
// pywebview の事象の登録（DOMEventHandler）は、事象のたびに事象の物を文字にして Python へ送る。document に登録すると
// currentTarget（document）が子孫ごと文字になり、ドラッグ中に画面全体（結果の表の全部の行）が文字になって窓が固まる。
// そこで次のようにする。
//
// - dragover: ここで既定の動き（ファイルを開く）と親への伝わりを止めるだけ。Python は呼ばない（ADR-0147 決定1）
// - drop: ここで既定の動きと親への伝わりを止め、ファイルの一覧だけを持つ新しい事象を、中身の無い印の要素
//   （<div id="drop-anchor" hidden>）へ送る。Python の側（app.watch_drop）はその要素の drop だけに登録する（決定2）。
//   文字になるのは小さい印の要素と、ファイルの一覧だけになる
//
// ADR-0147 決定2 のコード片との違い: ファイルの一覧には、名前だけの物ではなく、ドロップで受けた本物のファイルの物
// （File）をそのまま入れる。Windows の窓（WebView2）は、pywebview の JavaScript が Python を呼ぶときに事象のファイルの
// 一覧を WebView2 へ渡し、本物のファイルの物からだけパスを記録するため（名前だけの物ではパスが取れない）。
// Mac の窓（WKWebView）は窓の側でパスを記録するので、どちらの形でも同じに動く。ファイルの物はページの要素ではないので、
// 文字にしても名前・大きさ・種類・更新日時くらいの小さい物にしかならない。
//
// 親への伝わりは stopPropagation で止める。同じ要素に付いたほかの受け手まで止める書き方は使わない
// （pywebview の受け手を止めないため。Windows ではその受け手が WebView2 にパスの記録（FilesDropped）を頼む）。

const ANCHOR_ID = "drop-anchor";

document.addEventListener("dragover", (event) => {
  event.preventDefault();
  event.stopPropagation();
});

document.addEventListener("drop", (event) => {
  event.preventDefault();
  event.stopPropagation();
  const anchor = document.getElementById(ANCHOR_ID);
  if (!anchor || !event.dataTransfer) {
    return;
  }
  // new Event は既定で親へ伝わらない（bubbles が false）ので、document の drop に戻って繰り返すことはない
  const slim = new Event("drop");
  slim.dataTransfer = { files: Array.from(event.dataTransfer.files) };
  anchor.dispatchEvent(slim);
});
