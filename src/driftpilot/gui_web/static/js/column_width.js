// 表の列の幅を、見出しの右端の境目で変える部品（REQ-211 §5 / ADR-0153。REQ-224 / ADR-0163 で表の幅の決め方を変えた）。
// 一括比較の窓の表と、比較の窓の 5 つの表が使う。
//
// 境目の要素（role="separator"・tabindex="0"・aria-orientation="vertical"）は呼ぶ側が作り（比較の窓は makeGrip）、ここは操作だけを受ける。
// ドラッグは pointer の事象、左右の矢印のキーで STEP px ずつ。最小は MIN px（埋める列は fillMin）。幅は保存しない。
// 幅は <col> の style.width と表の style.width に入れる（CSSOM。HTML の style 属性は書かない）。表の幅は全部の列の合計で、
// 枠より広いときは表の枠の中で横にスクロールする。
// 固める: 表の枠（.pilot-table-wrap）に幅が付いた最初のとき（ResizeObserver）に、全部の列を今の見た目の幅の px にする。
// 埋める列（fill）は、枠の幅からほかの列の合計を引いた残り（fillMin より狭くしない）。人がその列の境目を動かしたら埋めるのをやめる。
// 決め直す（REQ-365）: 枠の幅が変わったときは、人が境目を動かしていない列（境目の無い列・埋める列を除く）を、
// 固めたときの幅 × （新しい枠の幅 ÷ 固めたときの枠の幅）にする（MIN より狭くしない）。人が動かした列（ドラッグ・矢印のキー・
// ダブルクリックのどれでも）は今の px のまま。埋める列は、埋めている間だけ「枠の幅 − ほかの列の合計」に合わせ直す。
// 割合は毎回の前の幅からでなく、固めたときの幅から測る（窓を動かして何十回も呼ばれても丸めがたまらず、
// 1 度 MIN まで縮んだ列も、枠を広げれば元の割合に戻る）。
// ダブルクリック（と Enter）: 境目の列の幅を、見出しとその列の全部のセルの文字が切れずに入る幅にする。
// scrollWidth は今の列の幅より小さい値を返さないので、測る前にその列を一時的に最小の幅にする（同じ処理の中で入れ直すので画面は描かれない）。
// 折り返して全部出すセル（pilot-cell-wrap）か選択の箱（.pilot-cell-select）がある列は、中身の長さを測れないので今の幅より狭めない
// （折り返す列はダブルクリックで変わらない。見出しが長いときだけ広がる）。パスの箱（.pilot-path）は中の文字の幅で測る。
// 測る間に縮む横のスクロール位置は、最後に元へ戻す。上限は枠の幅の 9 割。ダブルクリックした列は、人が動かした列として覚える。
// 今の幅は、列と対になる見出しのセル（<th>）で測る。<col> の大きさは測らない（WebKit（Mac の窓）では
// <col> の getBoundingClientRect().width が 0 になり、列がつぶれるため）。枠の幅は clientWidth で読む。
// 測るのは、固めるとき・境目を動かしたとき・枠の幅が変わったとき・ダブルクリックのときだけ（行を描くたびには測らない）。

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
  const moved = new Set(); // 人が境目を動かした列の番号
  let base = []; // 固めたときの各列の幅（丸める前）
  let baseFrame = 0; // 固めたときの枠の幅
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
    base = widths.map((width, index) => (columns[index].handle === null ? width : Math.max(MIN, width)));
    columns.forEach((column, index) => {
      if (index !== fillIndex) {
        setPx(column, column.handle === null ? widths[index] : Math.max(MIN, widths[index]));
      }
    });
    fitFill();
    frozen = true;
    frameWidth = frame.clientWidth;
    baseFrame = frameWidth;
  }

  // 枠の幅が変わったとき: 人が動かしていない列（埋める列・境目の無い列を除く）を、固めたときからの枠の割合で決め直す。
  // 埋める列は、埋めている間だけ残りを埋める
  function rescale(width) {
    if (baseFrame === 0) {
      baseFrame = width;
    }
    columns.forEach((column, index) => {
      if (index === fillIndex || column.handle === null || moved.has(index)) {
        return;
      }
      setPx(column, Math.max(MIN, (base[index] * width) / baseFrame));
    });
    if (filling) {
      fitFill();
    }
  }

  // 境目を動かす: その列だけを変える（ほかの列は変えない）。埋めている間に合計が枠より狭くなったら、埋める列を広げて枠に合わせる
  function setWidth(index, px) {
    moved.add(index);
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

  // その列のセル（今描いている全部の行。セルの数が列の数と違う行は飛ばす）
  function cellsOf(index) {
    const cells = [];
    for (const row of table.querySelectorAll("tbody tr")) {
      if (row.cells.length === columns.length) {
        cells.push(row.cells[index]);
      }
    }
    return cells;
  }

  // セルの文字が切れずに入る幅。パスの箱は中の文字の幅に、セルの左右の余白を足す
  function contentWidth(cell) {
    const box = cell.querySelector(".pilot-path");
    if (box !== null && box.firstElementChild !== null) {
      return box.firstElementChild.scrollWidth + (cell.clientWidth - box.clientWidth);
    }
    return cell.scrollWidth;
  }

  // ダブルクリック・Enter: 列の幅を中身に合わせる（ほかの列は変えない）
  function fitContent(index) {
    if (!frozen) {
      freeze();
    }
    const column = columns[index];
    const cells = cellsOf(index);
    // 中身の長さを測れないセルがある列は、今の幅より狭めない:
    // - 折り返して全部出すセル（pilot-cell-wrap）: 最小の幅で測ると行が縦に伸びる
    // - 選択の箱（.pilot-cell-select。一括比較の窓の機種の列）: 箱の幅がセルの幅に合わせて縮むので、scrollWidth が中身を表さない
    const unmeasurable = cells.some(
      (cell) => cell.classList.contains("pilot-cell-wrap") || cell.querySelector(".pilot-cell-select") !== null,
    );
    const floor = unmeasurable ? widthOf(column) : least(index);
    // 測る間は列と表が狭くなり、横にスクロールできる幅が縮んで位置が詰められるので、位置を覚えて最後に戻す
    const scrollLeft = frame.scrollLeft;
    // scrollWidth は今の幅より小さくならないので、測る前に最小の幅にする。表の幅も合計に入れ直す（固定の割り付けで余りが配られないように）
    setPx(column, least(index));
    table.style.width = `${Math.round(columns.reduce((sum, item) => sum + widthOf(item), 0))}px`;
    // 数万行でも引数の数の上限に当たらないよう、reduce で最大を取る
    const need = cells.reduce((most, cell) => Math.max(most, contentWidth(cell)), column.head.scrollWidth);
    setWidth(index, Math.max(floor, Math.min(need, frame.clientWidth * 0.9)));
    frame.scrollLeft = scrollLeft;
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
    } else {
      rescale(width);
    }
    update();
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
    // ダブルクリックで列の幅を中身に合わせる。行の選択・比較画面を開く動き・列の並べ替えに届かないようにする
    handle.addEventListener("dblclick", (event) => {
      event.preventDefault();
      event.stopPropagation();
      if (connected) {
        fitContent(index);
      }
    });
    // 左右の矢印のキーで STEP px ずつ。Enter でダブルクリックと同じ
    handle.addEventListener("keydown", (event) => {
      if ((event.key === "ArrowLeft" || event.key === "ArrowRight") && connected) {
        event.preventDefault();
        setWidth(index, measure(column) + (event.key === "ArrowLeft" ? -STEP : STEP));
      } else if (event.key === "Enter" && connected) {
        event.preventDefault();
        event.stopPropagation();
        fitContent(index);
      }
    });
  });

  function disconnect() {
    connected = false;
    observer.disconnect();
  }

  return { disconnect };
}
