// 一括比較の窓の組の行の右クリックのメニューの開け閉め（REQ-368 / ADR-0244 決定1・4）。
//
// 見た目の部品（.pilot-menu）と動きは比較の窓の右クリックのメニュー（compare.js）と同じ。
//   ・開く場所を窓の中に収める ・開いたら最初の項目にフォーカスする
//   ・上下の矢印・Home・End で項目の間を移る ・Esc で閉じて、開く前にあった場所へフォーカスを戻す
//   ・外を押す・スクロールで閉じる ・フォーカスがほかの要素へ出たら閉じる
// キー押下の受け手は、メニューの中だけに付ける（document・window には付けない）。
// batch.js はキーの扱いを持たない（キーを押すたびに組を作らない）ので、キーの動きはここに置く。
// 項目の文字は呼ぶ側（batch.js）が入れる。ここは文字を描かない（文字を HTML として読む書き方は使わない）。

// menu はメニューの要素（hidden で開け閉めする）。出す項目は呼ぶ側が hidden で決めてから open を呼ぶ。
export function bindRowMenu(menu) {
  let back = null; // 開く前にフォーカスがあった要素（Esc・項目を押したときに戻す）

  // 出している項目のボタン（上から順）
  const buttons = () => Array.from(menu.querySelectorAll("button")).filter((button) => !button.hidden);
  const isOpen = () => !menu.hidden;

  function close(restore) {
    if (!isOpen()) {
      return;
    }
    menu.hidden = true;
    document.removeEventListener("pointerdown", onOutside, true);
    document.removeEventListener("scroll", onScroll, true);
    const target = back;
    back = null;
    if (restore && target !== null && typeof target.focus === "function") {
      target.focus();
    }
  }

  function onOutside(event) {
    if (!menu.contains(event.target)) {
      close(false);
    }
  }

  function onScroll() {
    close(false);
  }

  // (x, y)（窓の座標）に出す。窓の外へはみ出すときは中へ寄せる。出す項目が 1 つも無ければ何もせず false
  function open(x, y) {
    if (buttons().length === 0) {
      return false;
    }
    close(false);
    back = document.activeElement;
    menu.hidden = false;
    const box = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(0, Math.min(x, window.innerWidth - box.width))}px`;
    menu.style.top = `${Math.max(0, Math.min(y, window.innerHeight - box.height))}px`;
    document.addEventListener("pointerdown", onOutside, true);
    document.addEventListener("scroll", onScroll, true);
    buttons()[0].focus();
    return true;
  }

  menu.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
      return;
    }
    const items = buttons();
    const at = items.indexOf(document.activeElement);
    let next = null;
    if (event.key === "ArrowDown") {
      next = items[(at + 1) % items.length];
    } else if (event.key === "ArrowUp") {
      next = items[(at - 1 + items.length) % items.length];
    } else if (event.key === "Home") {
      next = items[0];
    } else if (event.key === "End") {
      next = items[items.length - 1];
    }
    if (next !== null) {
      event.preventDefault();
      next.focus();
    }
  });
  // フォーカスがほかの要素へ出たら閉じる。relatedTarget が null のときは閉じない。WebKit（Mac の窓）では、button を押すと
  // フォーカスが外れて空になり、押している途中で focusout が relatedTarget なしで起きる。ここで閉じると click が届かない。
  // 外を押したときは、開いている間だけ付ける pointerdown の受け手が閉じる
  menu.addEventListener("focusout", (event) => {
    if (event.relatedTarget !== null && !menu.contains(event.relatedTarget)) {
      close(false);
    }
  });

  return { open, close, isOpen };
}
