// 片方だけのファイルの行を、反対側の列の行へドラッグして組にする（REQ-233 / ADR-0171）。
// pointer の事象だけを使う。HTML5 のドラッグ（属性・始まりの事象・運ぶデータ・落とす事象）は使わない
// （窓の外からのファイルのドロップは drop.js が受ける。pointer の事象は窓の外からのドロップの事象を起こさないので混ざらない）。
//
// 行は batch.js の drawUnpaired が作る tr で、tr.dataset.side（before・after）と tr.dataset.index（その側の一覧の番号）を持つ。
// 事象は 2 つの枠に 1 回だけ登録する（行を描き直しても登録し直さない）。組にするかは Python が決める（番号だけを渡す）。
// - 押す: 主ボタンで、options.enabled() が真で、行の上で、チェックボックスの上でないときだけ、始まりの位置と行を覚える
// - 動かす: 始まりから DRAG_START_PX 以上動いたらドラッグを始める。印（data-drag-source・data-drop-target・data-drop-blocked・
//   body の data-pair-dragging）と札（.pilot-drag-ghost。文字は dom.js の make で入れる）を出す。
//   指の下の反対側の行に data-drop-hover。指が反対側の枠の上下の端から EDGE_PX 以内なら、動かすたびに EDGE_STEP_PX スクロールする（時計は使わない）
// - 離す: 離した位置で指の下の行を取り直し、反対側の行があれば options.onPair(変更前の番号, 変更後の番号)（どちらの向きにドラッグしても変更前・変更後の順）
// - 取り消し（pointercancel・lostpointercapture・ドラッグ中の Esc）: 組にせず、印と札を消す
// - ドラッグを始めたら今の文字の選択を消し、ドラッグの間は selectstart を止める（CSS も body の data-pair-dragging の間は
//   user-select: none。ADR-0176 決定1）
// ドラッグを始めなかった（DRAG_START_PX 未満）ときは何もしない（クリックとチェックの今の動きのまま）。

import { make } from "./dom.js";

export const DRAG_START_PX = 6; // これだけ動いたらドラッグを始める
export const EDGE_PX = 24; // 反対側の枠の上下の端からこの幅の中ならスクロールする
export const EDGE_STEP_PX = 12; // 動かすたびにスクロールする量
export const GHOST_OFFSET_PX = 12; // 札を指の右下にずらす量

const SIDES = ["before", "after"];

// wraps: { before: <変更前の列の .pilot-table-wrap>, after: <変更後の列の .pilot-table-wrap> }
// options: { enabled: () => boolean, onPair: (beforeIndex, afterIndex) => void }
export function bindPairDrag(wraps, options) {
  // 押している間の状態（押していなければ null）。dragging はドラッグを始めたか
  let press = null;
  let ghost = null;
  let hover = null;

  const otherSide = (side) => (side === "before" ? "after" : "before");

  // 枠 wrap の中の、tbody の行（無ければ null）
  function rowIn(wrap, element) {
    if (!(element instanceof Element)) {
      return null;
    }
    const tr = element.closest("tbody tr");
    return tr !== null && wrap.contains(tr) ? tr : null;
  }

  function setHover(tr) {
    if (hover === tr) {
      return;
    }
    if (hover !== null) {
      delete hover.dataset.dropHover;
    }
    hover = tr;
    if (hover !== null) {
      hover.dataset.dropHover = "true";
    }
  }

  // 印と札を消し、覚えた状態を戻す（ドラッグを始めていなくても呼んでよい）
  function clear() {
    const current = press;
    press = null;
    setHover(null);
    if (ghost !== null) {
      ghost.remove();
      ghost = null;
    }
    delete document.body.dataset.pairDragging;
    for (const side of SIDES) {
      delete wraps[side].dataset.dropBlocked;
      for (const tr of wraps[side].querySelectorAll("tbody tr")) {
        delete tr.dataset.dragSource;
        delete tr.dataset.dropTarget;
      }
    }
    if (current !== null && current.wrap.hasPointerCapture(current.pointerId)) {
      current.wrap.releasePointerCapture(current.pointerId);
    }
  }

  // ドラッグを始める（印と札を出す）。なぞった範囲の文字が選ばれていれば消す（ADR-0176 決定1）
  function begin() {
    window.getSelection()?.removeAllRanges();
    press.dragging = true;
    const target = wraps[otherSide(press.side)];
    press.row.dataset.dragSource = "true";
    for (const tr of target.querySelectorAll("tbody tr")) {
      tr.dataset.dropTarget = "true";
    }
    press.wrap.dataset.dropBlocked = "true";
    document.body.dataset.pairDragging = "true";
    const label = press.row.querySelector(".pilot-path > span");
    ghost = make("div", "pilot-drag-ghost", label !== null ? label.textContent : "");
    ghost.setAttribute("aria-hidden", "true");
    document.body.append(ghost);
  }

  // ドラッグ中に指が動いた（札・指の下の行・端のスクロール）
  // 指の位置 (x, y) の下にある反対側の行を取り直して強調する（スクロールした後と離したときにも呼ぶ。行は指の下で入れ替わるため）
  function pick(x, y) {
    setHover(rowIn(wraps[otherSide(press.side)], document.elementFromPoint(x, y)));
  }

  // 端のスクロール（先にスクロールしてから指の下の行を取る。取った後にスクロールすると、強調と離したときの行が見えている行とずれる）
  function follow(event) {
    ghost.style.left = `${event.clientX + GHOST_OFFSET_PX}px`;
    ghost.style.top = `${event.clientY + GHOST_OFFSET_PX}px`;
    const target = wraps[otherSide(press.side)];
    const rect = target.getBoundingClientRect();
    if (event.clientX >= rect.left && event.clientX <= rect.right) {
      if (event.clientY >= rect.top && event.clientY < rect.top + EDGE_PX) {
        target.scrollTop -= EDGE_STEP_PX;
      } else if (event.clientY <= rect.bottom && event.clientY > rect.bottom - EDGE_PX) {
        target.scrollTop += EDGE_STEP_PX;
      }
    }
    pick(event.clientX, event.clientY);
  }

  function onDown(side, event) {
    if (press !== null || event.button !== 0 || !options.enabled()) {
      return;
    }
    const wrap = wraps[side];
    const row = rowIn(wrap, event.target);
    if (row === null || row.dataset.side !== side || event.target.closest("input") !== null) {
      return;
    }
    press = { side, wrap, row, pointerId: event.pointerId, x: event.clientX, y: event.clientY, dragging: false };
    wrap.setPointerCapture(event.pointerId);
  }

  function onMove(event) {
    if (press === null || event.pointerId !== press.pointerId) {
      return;
    }
    if (!press.row.isConnected) {
      clear(); // 押している間に一覧が描き直された（番号が変わったかもしれないので組にしない）
      return;
    }
    if (!press.dragging) {
      if (Math.hypot(event.clientX - press.x, event.clientY - press.y) < DRAG_START_PX) {
        return;
      }
      begin();
    }
    follow(event);
  }

  function onUp(event) {
    if (press === null || event.pointerId !== press.pointerId) {
      return;
    }
    if (press.dragging) {
      pick(event.clientX, event.clientY); // 離した位置の行で組にする（前の pointermove の後に枠がスクロールしていても、見えている行）
    }
    // 描き直された行（外れた要素）の番号は使わない
    const pair = press.dragging && hover !== null && press.row.isConnected && hover.isConnected ? [press.row, hover] : null;
    const side = press.side;
    clear();
    if (pair === null || !options.enabled()) {
      return;
    }
    const [from, to] = pair;
    const indexes = { [side]: Number(from.dataset.index), [otherSide(side)]: Number(to.dataset.index) };
    options.onPair(indexes.before, indexes.after);
  }

  function onCancel(event) {
    if (press !== null && event.pointerId === press.pointerId) {
      clear();
    }
  }

  for (const side of SIDES) {
    const wrap = wraps[side];
    wrap.addEventListener("pointerdown", (event) => onDown(side, event));
    wrap.addEventListener("pointermove", onMove);
    wrap.addEventListener("pointerup", onUp);
    wrap.addEventListener("pointercancel", onCancel);
    wrap.addEventListener("lostpointercapture", onCancel);
  }
  // ドラッグ中の Esc だけを受ける（ドラッグしていないときのキーは触らない）
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && press !== null && press.dragging) {
      event.preventDefault();
      clear();
    }
  });
  // ドラッグの間だけ文字の選択を始めさせない（ADR-0176 決定1。ドラッグしていないときは何もしない。
  // 終わったら clear() が body の data-pair-dragging を外すので、また選べる）
  document.addEventListener("selectstart", (event) => {
    if (press !== null && press.dragging) {
      event.preventDefault();
    }
  });
}
