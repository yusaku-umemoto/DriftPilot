// 結果の表の列の絞り込みの、値の一覧の小さな窓（REQ-391 / ADR-0256 決定2・3）。
//
// 先頭の 3 つの関数は DOM に触らない（node から import して確かめられる）。createColumnFilter が窓を動かす部分で、
// 窓の要素（#column-filter ほか。compare.html）は作るときに 1 度だけ引く。
// 窓の中の文字（値・件数・知らせ）は textContent だけで入れる。どの値を隠すかは Python が決める（ここは選んだ値を渡すだけ）。
// 窓は 1 つを 2 つの表の見出しのボタンで使い回す。

import { make, text } from "./dom.js";

// 検索の欄に文字を入れてから、一覧を読み直すまでの待ち（ミリ秒）
export const SEARCH_DELAY_MS = 150;

// 見出しのボタンから窓を開くキーか。Enter・Space・Alt+↓
export function opensFilter(event) {
  if (event.key === "Enter" || event.key === " ") {
    return true;
  }
  return event.key === "ArrowDown" && event.altKey === true;
}

// 一覧（{value, label, count, checked}）の全部にチェックがあるか。空の一覧は偽
export function allChecked(items) {
  return items.length > 0 && items.every((item) => item.checked === true);
}

// チェックのある値の一覧（一覧の順）
export function checkedValues(items) {
  return items.filter((item) => item.checked === true).map((item) => item.value);
}

// load(table, column, search) は Python の column_values を呼び、{items, over, over_text, column_label} か null を返す。
// apply(table, column, search, values) は Python の set_column_filter を呼ぶ。
// label(column_label) は窓の読み上げの名前（aria-label）の文。
export function createColumnFilter({ load, apply, label }) {
  const box = document.getElementById("column-filter");
  const search = document.getElementById("column-filter-search");
  const all = document.getElementById("column-filter-all");
  const list = document.getElementById("column-filter-list");
  const over = document.getElementById("column-filter-over");
  const ok = document.getElementById("column-filter-ok");
  const cancel = document.getElementById("column-filter-cancel");
  // 開いている間だけ持つ値: 開いたボタン・表と列・今出している一覧・検索の待ちの印・読み込みの番号（古い返事を捨てる）
  let current = null;
  let items = [];
  let timer = null;
  let serial = 0;

  function isOpen() {
    return current !== null;
  }

  function sync() {
    all.checked = allChecked(items);
    all.indeterminate = !all.checked && items.some((item) => item.checked === true);
    ok.disabled = checkedValues(items).length === 0;
  }

  function draw(data) {
    items = data.items.map((item) => ({ ...item }));
    const rows = items.map((item) => {
      const row = make("label", "pilot-column-filter-item", null);
      const input = make("input", "pilot-check", null);
      input.type = "checkbox";
      input.checked = item.checked === true;
      input.addEventListener("change", () => {
        item.checked = input.checked;
        sync();
      });
      row.append(input, make("span", "", item.label), make("span", "pilot-column-filter-count", String(item.count)));
      return row;
    });
    list.replaceChildren(...rows);
    over.hidden = data.over !== true;
    text(over, data.over === true ? data.over_text : "");
    sync();
  }

  function place(button) {
    const rect = button.getBoundingClientRect();
    const left = Math.max(0, Math.min(rect.left, window.innerWidth - box.offsetWidth));
    box.style.left = `${left}px`;
    box.style.top = `${rect.bottom}px`;
  }

  function stopListening() {
    document.removeEventListener("pointerdown", onOutside, true);
    document.removeEventListener("scroll", onScroll, true);
  }

  // 表のスクロールで閉じる（窓の中の一覧のスクロールでは閉じない）。フォーカスは動かさない
  function onScroll(event) {
    const target = event.target instanceof Node ? event.target : null;
    if (target !== null && box.contains(target)) {
      return;
    }
    close(false);
  }

  // 閉じる（キャンセルと同じ。何も Python に渡さない）。focus が真なら、フォーカスを開いたボタンへ戻す
  function close(focus) {
    if (current === null) {
      return;
    }
    const { button } = current;
    current = null;
    serial += 1; // 読み込み中の返事は捨てる
    if (timer !== null) {
      clearTimeout(timer);
      timer = null;
    }
    stopListening();
    box.hidden = true;
    items = [];
    list.replaceChildren();
    button.setAttribute("aria-expanded", "false");
    if (focus) {
      button.focus({ preventScroll: true });
    }
  }

  // 外を押したら閉じる（フォーカスは押した先のまま）。開いたボタンを押したときはボタンの click が閉じる
  function onOutside(event) {
    const target = event.target instanceof Element ? event.target : null;
    if (target !== null && (box.contains(target) || (current !== null && current.button.contains(target)))) {
      return;
    }
    close(false);
  }

  async function reload(searchText) {
    if (current === null) {
      return;
    }
    const at = (serial += 1);
    const { table, column } = current;
    const data = await load(table, column, searchText);
    if (data === null || at !== serial || current === null) {
      return;
    }
    draw(data);
  }

  async function open(button) {
    if (current !== null) {
      const same = current.button === button;
      close(false);
      if (same) {
        return; // 開いているボタンをもう一度押したら閉じる
      }
    }
    const table = button.dataset.filterTable;
    const column = button.dataset.filterColumn;
    const at = (serial += 1);
    const data = await load(table, column, "");
    if (data === null || at !== serial) {
      return;
    }
    current = { button, table, column };
    search.value = "";
    draw(data);
    box.setAttribute("aria-label", label(data.column_label));
    button.setAttribute("aria-expanded", "true");
    box.hidden = false;
    place(button);
    document.addEventListener("pointerdown", onOutside, true);
    document.addEventListener("scroll", onScroll, true);
    search.focus({ preventScroll: true });
  }

  search.addEventListener("input", () => {
    if (timer !== null) {
      clearTimeout(timer);
    }
    timer = setTimeout(() => {
      timer = null;
      reload(search.value);
    }, SEARCH_DELAY_MS);
  });
  // 「すべて選択」: 今出している項目のチェックを全部付ける・全部外す
  all.addEventListener("change", () => {
    for (const item of items) {
      item.checked = all.checked;
    }
    for (const input of list.querySelectorAll("input")) {
      input.checked = all.checked;
    }
    sync();
  });
  ok.addEventListener("click", async () => {
    if (current === null || ok.disabled) {
      return;
    }
    const { button, table, column } = current;
    const values = checkedValues(items);
    const searchText = search.value;
    close(true);
    await apply(table, column, searchText, values);
    button.focus({ preventScroll: true });
  });
  cancel.addEventListener("click", () => close(true));
  box.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close(true);
    }
  });
  // フォーカスが窓の外へ出たら閉じる（右クリックのメニューと同じ決まり。relatedTarget が無いときは閉じない。外を押したときは pointerdown が閉じる）
  box.addEventListener("focusout", (event) => {
    if (event.relatedTarget !== null && !box.contains(event.relatedTarget)) {
      close(false);
    }
  });

  return { open, close, isOpen };
}
