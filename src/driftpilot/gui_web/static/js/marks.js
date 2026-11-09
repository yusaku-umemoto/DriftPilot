// 変更の行で違う文字の印（ADR-0165 決定4・5 / REQ-226）。
//
// 違う範囲を決めるのは Python（driftpilot/inline_marks.py）。ここは渡された範囲で文字を切って描くだけ。
// 文字は文字の小片（createTextNode）と <mark> の textContent だけで入れる（ADR-0146 決定4）。
// 範囲の位置は JavaScript の文字の位置（UTF-16 の単位）。Python が utf16_ranges で直して渡す。

// kind ごとの <mark> のクラス
const CLASS = { removed: "pilot-mark pilot-mark--removed", added: "pilot-mark pilot-mark--added" };

// ranges が [[始まり, 終わり], …] で、どれも 0 ≦ 始まり < 終わり ≦ length の整数、昇順で重ならないときだけ true
function validRanges(ranges, length) {
  if (!Array.isArray(ranges) || ranges.length === 0) {
    return false;
  }
  let last = 0;
  for (const range of ranges) {
    if (!Array.isArray(range) || range.length !== 2) {
      return false;
    }
    const [start, end] = range;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start < last || start >= end || end > length) {
      return false;
    }
    last = end;
  }
  return true;
}

// cell の中身を、text を ranges（[[始まり, 終わり], …]。JavaScript の文字の位置）で切った文字の小片と <mark> で作り直す。
// kind は "removed"（変更前）か "added"（変更後）。ranges が空・並びが崩れている・text の外を指すときは text だけを入れる。
// セルの title には触らない（全文の title は今のまま）。
export function paintMarks(cell, text, ranges, kind) {
  const value = typeof text === "string" ? text : "";
  if ((kind !== "removed" && kind !== "added") || !validRanges(ranges, value.length)) {
    cell.replaceChildren(document.createTextNode(value));
    return;
  }
  const parts = [];
  let at = 0;
  for (const [start, end] of ranges) {
    if (start > at) {
      parts.push(document.createTextNode(value.slice(at, start)));
    }
    const mark = document.createElement("mark");
    mark.className = CLASS[kind];
    mark.textContent = value.slice(start, end);
    parts.push(mark);
    at = end;
  }
  if (at < value.length) {
    parts.push(document.createTextNode(value.slice(at)));
  }
  cell.replaceChildren(...parts);
}
