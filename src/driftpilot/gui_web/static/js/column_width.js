// 表の列の幅を、見出しの右端の境目で変える部品（REQ-211 §5 / ADR-0153。REQ-224 / ADR-0163 で表の幅の決め方を変えた）。
// 一括比較の窓の表と、比較の窓の 5 つの表が使う。
//
// 境目の要素（role="separator"・tabindex="0"・aria-orientation="vertical"）は呼ぶ側が作り（比較の窓は makeGrip）、ここは操作だけを受ける。
// ドラッグは pointer の事象、左右の矢印のキーで STEP px ずつ。最小は MIN px（埋める列は fillMin）。幅は保存しない。
// 幅は <col> の style.width と表の style.width に入れる（CSSOM。HTML の style 属性は書かない）。表の幅は全部の列の合計で、
// 枠より広いときは表の枠の中で横にスクロールする。
// 固める: 表の枠（.pilot-table-wrap）に幅が付いた最初のとき（ResizeObserver）に、全部の列を今の見た目の幅の px にする。
// 埋める列（fill）は、枠の幅からほかの列の合計を引いた残り（fillMin より狭くしない）。人がその列の境目を動かしたら埋めるのをやめる。
// 枠の幅が変わったときは、埋めている間だけ埋める列を合わせ直す。
// 今の幅は、列と対になる見出しのセル（<th>）で測る。<col> の大きさは測らない（WebKit（Mac の窓）では
// <col> の getBoundingClientRect().width が 0 になり、列がつぶれるため）。枠の幅は clientWidth で読む。
// 測るのは、固めるとき・境目を動かしたとき・枠の幅が変わったときだけ（行を描くたびには測らない）。

export const MIN = 48;
export const STEP = 20;

// 見出しのセルに入れる境目の要素を作る（比較の窓が使う。一括比較の窓は batch.js で作る）。label は読み上げの名前と title。
export function makeGrip(label) {
  const grip = document.createElement("span");
  grip.className = "pilot-col-resize";
  grip.setAttribute("role", "separator");
  grip.setAttribute("tabindex", "0");
  grip.setAttribute("aria-orientation", "vertical");
  grip.setAttribute("aria-label", label);
  grip.title = label;
  return grip;
}

// columns は出している列の全部を左から並べたもの: [{ col: その列の <col>, head: 対になる <th>, handle: 境目の要素か null }]。
// fill: 埋める列の番号（columns の中の番号。既定は最後の列）。fillMin: 埋める列の最低の幅（px。既定は MIN）。
// onChange は幅を変えた後に呼ぶ。返す値の disconnect() で、枠の大きさの見張りを止める（列を作り直す前に呼ぶ）。
// 列の組み立てを変えたとき（<col> を作り直したとき）は、disconnect() を呼んでから、境目も新しく作ってもう一度呼ぶ。
export function bindColumnWidths(table, columns, { onChange, fill, fillMin } = {}) {
  const frame = table.closest(".pilot-table-wrap");
  const fillIndex = fill ?? columns.length - 1;
  const fillLeast = fillMin ?? MIN;
  let frozen = false; // 全部の列を px にしたか
  let filling = true; // 埋める列が残りを埋めている間か（人がその列の境目を動かしたら false）
  let frameWidth = 0; // 前に見た枠の幅
  let connected = true;

  // 見出しのセルの今の幅（px）
  function measure(column) {
    return column.head.getBoundingClientRect().width;
  }

  function least(index) {
    return index === fillIndex ? fillLeast : MIN;
  }

  function widthOf(column) {
    return parseFloat(column.col.style.width) || 0;
  }

  // 埋める列のほかの列の幅の合計
  function others() {
    return columns.reduce((sum, column, index) => (index === fillIndex ? sum : sum + widthOf(column)), 0);
  }

  function setPx(column, px) {
    column.col.style.width = `${Math.round(px)}px`;
  }

  // 埋める列を、枠の幅からほかの列の合計を引いた残りにする（fillLeast より狭くしない）
  function fitFill() {
    setPx(columns[fillIndex], Math.max(fillLeast, frame.clientWidth - others()));
  }

  // 表の幅を全部の列の合計にして、onChange を呼ぶ
  function update() {
    const total = columns.reduce((sum, column) => sum + widthOf(column), 0);
    table.style.width = `${Math.round(total)}px`;
    if (onChange) {
      onChange();
    }
  }

  // 今の見た目の幅を px にして入れる。先に全部測ってから入れる。境目のある列は MIN より狭くしない。
  // 境目の無い列（一括比較の先頭のチェックの列）は測った幅のまま。埋める列は残りを埋める
  function freeze() {
    const widths = columns.map(measure);
    columns.forEach((column, index) => {
      if (index !== fillIndex) {
        setPx(column, column.handle === null ? widths[index] : Math.max(MIN, widths[index]));
      }
    });
    fitFill();
    frozen = true;
    frameWidth = frame.clientWidth;
  }

  // 境目を動かす: その列だけを変える（ほかの列は変えない）。埋めている間に合計が枠より狭くなったら、埋める列を広げて枠に合わせる
  function setWidth(index, px) {
    if (!frozen) {
      freeze();
    }
    setPx(columns[index], Math.max(least(index), px));
    if (index === fillIndex) {
      filling = false;
    } else if (filling && others() + widthOf(columns[fillIndex]) < frame.clientWidth) {
      fitFill();
    }
    update();
  }

  // 枠の大きさを見る。幅が 0（隠れたタブ）か前と同じ（高さだけが変わった）なら何もしない
  const observer = new ResizeObserver(() => {
    const width = frame.clientWidth;
    if (!connected || width === 0 || width === frameWidth) {
      return;
    }
    frameWidth = width;
    if (!frozen) {
      freeze();
      update();
    } else if (filling) {
      fitFill();
      update();
    }
  });
  observer.observe(frame);

  columns.forEach((column, index) => {
    const handle = column.handle;
    if (handle === null) {
      return;
    }
    let dragging = null;
    handle.addEventListener("pointerdown", (event) => {
      if (event.button !== 0) {
        return;
      }
      event.preventDefault();
      event.stopPropagation();
      handle.setPointerCapture(event.pointerId);
      dragging = { x: event.clientX, width: measure(column) };
    });
    handle.addEventListener("pointermove", (event) => {
      if (dragging !== null && connected) {
        setWidth(index, dragging.width + event.clientX - dragging.x);
      }
    });
    for (const name of ["pointerup", "pointercancel"]) {
      handle.addEventListener(name, (event) => {
        dragging = null;
        if (handle.hasPointerCapture(event.pointerId)) {
          handle.releasePointerCapture(event.pointerId);
        }
      });
    }
    // 見出しのクリック（行の選択など）に届かないようにする
    handle.addEventListener("click", (event) => event.stopPropagation());
    // 左右の矢印のキーで STEP px ずつ
    handle.addEventListener("keydown", (event) => {
      if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && connected) {
        event.preventDefault();
        setWidth(index, measure(column) + (event.key === "ArrowLeft" ? -STEP : STEP));
      }
    });
  });

  function disconnect() {
    connected = false;
    observer.disconnect();
  }

  return { disconnect };
}
