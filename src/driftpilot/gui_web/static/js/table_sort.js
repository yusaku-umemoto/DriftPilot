// 構造化比較の表の見出しの並べ替えの比べ方（REQ-369）。画面の中だけの並べ替えで、DOM は触らない（node から import して確かめられる）。
// 並べ替えの値も向きも Python には渡さない。保存・報告書・AI に送る差分の順は変わらない。

const DIRECTIONS = ["none", "ascending", "descending"];

// 見出しを押すたびの向き。none → ascending → descending → none
export function nextSortDirection(current) {
  const at = DIRECTIONS.indexOf(current);
  return DIRECTIONS[(at + 1) % DIRECTIONS.length];
}

// 空の値（null・undefined・空白だけの文字）
function isBlank(value) {
  return value === null || value === undefined || String(value).trim() === "";
}

// 元の行の番号の並びを返す。values は元の番号の順に並べた各行の値。
// ・direction が "none" なら 0..n-1 のまま
// ・空の値は昇順でも降順でも最後（空どうしは元の順）
// ・空でない値は collator.compare で比べる（数字の部分を数として比べる。降順は符号を逆にする）
// ・同じ値のときは元の番号の小さい方が先（毎回同じ並びになる）
export function sortOrder(values, direction, collator) {
  const order = values.map((_value, index) => index);
  if (direction !== "ascending" && direction !== "descending") {
    return order;
  }
  const compare = collator || new Intl.Collator(undefined, { numeric: true });
  const sign = direction === "ascending" ? 1 : -1;
  return order.sort((a, b) => {
    const blankA = isBlank(values[a]);
    const blankB = isBlank(values[b]);
    if (blankA || blankB) {
      return blankA === blankB ? a - b : blankA ? 1 : -1;
    }
    const result = compare.compare(String(values[a]), String(values[b]));
    return result !== 0 ? sign * result : a - b;
  });
}
