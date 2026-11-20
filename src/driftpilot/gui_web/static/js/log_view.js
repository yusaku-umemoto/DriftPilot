// 「ログを見る」の窓（ADR-0248 決定2 / REQ-377）。対になる Python の側は driftpilot/gui_web/log_view_api.py。
//
// 比べずに、1 つのログ（ファイルのドロップ・「開く…」・貼り付け）を、機器ごと・コマンドごとに分けて手元で読む窓。
// 右の AI の欄（質問・確認・送信・会話。js/log_ask.js）と、その欄の境目・隠す・送る区間のチェック（REQ-382）もここで受け持つ。
// 状態は Python が持つ。画面は state() が返す JSON を描き、出力の行は output_revision が変わったときだけ output() で受け取る
// （比較の窓の result と同じ形）。描くのは選んでいる区間の行だけで、1 回に CHUNK 行ずつ、窓が固まらないように分けて描く。
//
// ★ 文字は dom.js の make と createElement・textContent・append（文字列は文字として入れる）だけで入れる。
//   文字列を HTML として解釈する書き方は使わない。Python から受け取った文字（ログの行・コマンド・ファイル名）は、
//   属性には title にだけ入れる。Python を呼ぶのは LogViewApi の口だけ。AI に質問する口は log_ask.js だけが呼ぶ
//   （この窓の AI に関わる口は、チェックと AI の欄の境目・隠す・覚えるだけ）。

import { call, on } from "./bridge.js";
import { confirm, notice, panel } from "./dialog.js";
import { make, text } from "./dom.js";
import { bindTail, showTail } from "./path_tail.js"; // ファイルの名前の欄は後ろを見せる（REQ-211 §4）
import { bindColumnWidths, makeGrip } from "./column_width.js"; // 3 つの表（2 つの一覧と出力）の列の幅の境目（REQ-365・REQ-381）
import { copyText } from "./answer_copy.js"; // コピー（クリップボードが使えないときは選択のコピー）
import { bindLogAsk } from "./log_ask.js"; // AI の欄（質問・確認・送信・会話。REQ-382）
import "./drop.js"; // ファイルのドロップを印の要素へ送る（REQ-205 / ADR-0147）

const $ = (id) => document.getElementById(id);
const statusMain = $("status-main");

// 要素に結び付かない文（ダイアログ・一覧の印・探すの数・列の境目の名前）。上下の境目の名前は data-label で取る
const DIALOG_TEXTS = [
  "log_view.paste.title",
  "log_view.paste.prompt",
  "compare.dialog.ok",
  "compare.dialog.cancel",
  "compare.col.resize",
  "log_view.mark.error",
  "log_view.mark.preamble",
  "log_view.find.count",
  "log_view.find.none",
  "log_view.status.loading",
  "log_view.output.prompt_row", // 出力の表の 1 行目（元の行）の読み上げの名前（REQ-380）
  "log_view.check.col", // 一覧の行のチェックの読み上げの名前（REQ-382）
  "compare.button.copy", // AI の答えの吹き出しのコピーのボタンの名前（log_ask.js が使う）
];
// 左右の面の境目（ADR-0148 決定2 と同じ）。両側の最小の幅と、矢印のキーで動かす幅
const PANE_MIN = 220;
const SPLIT_STEP = 20;
// 出力の行を 1 回に描く数。残りは続けて描く（窓を固めない）
const CHUNK = 1000;
// 出力の表の出力の列は、いちばん長い行の文字数（等幅の ch）に足す余白（ch）を最低の幅にする
const TEXT_MARGIN_CH = 4;
const TAB_WIDTH = 8;
// 列の境目のつかみの幅（pilot.css の .pilot-split--log .pilot-col-resize と同じ。見出しの文がつかみの下に隠れないように足す）
const GRIP_WIDTH = 10;
// 上下の境目（機器の一覧の高さ）を矢印のキーで動かす幅（px）
const LISTS_STEP = 20;
// 上下の境目の初めの割合（CSS の機器の一覧 2 : コマンドの一覧 3）
const DEFAULT_LISTS_RATIO = 0.4;
// 列の幅の境目を付ける 2 つの一覧。fill は残りを埋める列の番号、fillMin はその最低の幅（px）。
// 初めの幅は bindTableColumns が画面の言語の文を測って <col> に入れる（埋める列は入れない）
// （どちらも先頭は AI に送るチェックの列で、境目は付けない。埋める列は 1 番目＝ホスト名・コマンドの列）
const COLUMN_TABLES = [
  { id: "devices-table", fill: 1, fillMin: 80 },
  { id: "commands-table", fill: 1, fillMin: 100 },
];

let texts = {}; // messages.py から取った文
let view = null; // いま描いている状態
let logAsk = null; // AI の欄（bindLogAsk が返す。文を取った後に作る）
// 描いた一覧の中身（JSON の文字）・出力を取った番号・出した知らせの番号
const shown = { devices: null, commands: null, position: null, output: null, notice: null };
// いま描いている出力。lines は選んでいる区間の行、command はそのコマンド（区切りなしの区間は null）、
// prompt_line はその区間を始めたプロンプトの行（`sw-test1#show version`。無ければ null。REQ-380）
let output = { command: null, prompt_line: null, lines: [] };
let outputRevision = -1;
// 出力の描画の状態。token は新しく描き始めるたびに進め、古い続きを止める。rendered は描いた行の数、rowEls は描いた行の要素。
// promptEl は表の 1 行目の見出しの行（元の行があるときだけ。rowEls には入れない）
let paintToken = 0;
let rendered = 0;
let rowEls = [];
let promptEl = null;
// 探す: 今の区間の行の当たり [{line, start, end}]・行ごとの当たり・今の当たりの番号（無ければ -1）。
// line は「探す行の並び」の番号で、元の行があれば 0 番がその行、本文の行は 1 番から（無ければ本文の行が 0 番から）
let hits = [];
let hitsByLine = new Map();
let currentHit = -1;

// <html lang> に入れる言語の鍵（ADR-0190。"en"・"ja"）
const PAGE_LANG = "page.lang";

// data-text・data-label の鍵の文を messages.py から取って入れる（HTML には文を書かない）。取れなければ false。
async function fillTexts() {
  const elements = Array.from(document.querySelectorAll("[data-text]"));
  const labelled = Array.from(document.querySelectorAll("[data-label]"));
  const hinted = Array.from(document.querySelectorAll("[data-placeholder]")); // 入力欄の薄い案内の文（質問の欄）
  const keys = Array.from(
    new Set([
      ...elements.map((el) => el.dataset.text),
      ...labelled.map((el) => el.dataset.label),
      ...hinted.map((el) => el.dataset.placeholder),
      ...DIALOG_TEXTS,
    ]),
  );
  const result = await call("messages", [...keys, PAGE_LANG]);
  if (result.ok) {
    document.documentElement.lang = result.messages[PAGE_LANG];
  }
  if (!result.ok) {
    text(statusMain, result.message);
    return false;
  }
  texts = result.messages;
  for (const el of elements) {
    text(el, texts[el.dataset.text]);
  }
  for (const el of labelled) {
    el.title = texts[el.dataset.label];
    el.setAttribute("aria-label", texts[el.dataset.label]);
  }
  for (const el of hinted) {
    el.placeholder = texts[el.dataset.placeholder];
  }
  return true;
}

// --- 状態を描く ---

function selectedRow(body, index) {
  for (const [at, row] of Array.from(body.rows).entries()) {
    row.setAttribute("aria-selected", String(at === index));
  }
}

// AI に送るチェックのセル（REQ-382）。state は "on"・"some"（一部だけ。中間の印）・"off"、または真偽値（コマンドの 1 区間）。
// チェックの操作は行を選ぶ操作とは別（クリックを行に伝えない。表示中の区間は変えない）。change で Python にチェックを渡す
function checkCell(state, name, onChange) {
  const cell = make("td", "pilot-check-cell", null);
  const box = make("input", "pilot-check", null);
  box.type = "checkbox";
  box.checked = state === "on" || state === true;
  box.indeterminate = state === "some";
  box.setAttribute("aria-label", `${texts["log_view.check.col"]}: ${name}`);
  box.addEventListener("click", (event) => event.stopPropagation());
  box.addEventListener("change", () => onChange(box.checked));
  cell.addEventListener("click", (event) => event.stopPropagation());
  cell.append(box);
  return cell;
}

// 一覧を描き直すとき、チェックにフォーカスがあれば、描き直した後の同じ行のチェックへ戻す（キーボードで続けて押せるように）
function refocusCheck(body, rebuild) {
  const active = document.activeElement;
  const at = active !== null && active.classList.contains("pilot-check") && body.contains(active) ? active.closest("tr").rowIndex - 1 : -1;
  rebuild();
  if (at >= 0) {
    body.rows[at]?.querySelector("input.pilot-check")?.focus();
  }
}

function drawDevices(rows) {
  const body = $("devices-table").tBodies[0];
  refocusCheck(body, () => {
    body.replaceChildren();
    for (const [index, row] of rows.entries()) {
      const tr = document.createElement("tr");
      const name = make("td", "", row.name);
      name.title = row.name;
      tr.append(
        checkCell(row.check, row.name, (on) => setDeviceCheck(index, on)),
        name,
        make("td", "pilot-num", row.commands),
      );
      tr.addEventListener("click", () => selectPosition(index, 0));
      body.append(tr);
    }
  });
}

function drawCommands(rows, device) {
  const body = $("commands-table").tBodies[0];
  refocusCheck(body, () => {
    body.replaceChildren();
    for (const [index, row] of rows.entries()) {
      const tr = document.createElement("tr");
      const command = make("td", "", row.command);
      command.title = row.command;
      const marks = make("td", "", null);
      if (row.error) {
        marks.append(make("span", "pilot-mark pilot-mark--attention", texts["log_view.mark.error"]));
      }
      if (row.preamble) {
        marks.append(" ", make("span", "pilot-mark", texts["log_view.mark.preamble"]));
      }
      tr.append(
        checkCell(row.checked, row.command, (on) => setSegmentCheck(device, index, on)),
        command,
        make("td", "pilot-num", row.lines),
        marks,
      );
      tr.addEventListener("click", () => selectPosition(device, index));
      body.append(tr);
    }
  });
}

// 見出しの「全部」のチェック。state は on・some・off
function drawHeadCheck(box, state, enabled) {
  box.checked = state === "on";
  box.indeterminate = state === "some";
  box.disabled = !enabled;
}

// 状態を描く。古い状態（遅れて届いたもの）は捨てる。
function render(next) {
  if (view !== null && next.serial < view.serial) {
    return;
  }
  view = next;
  if (layout === null) {
    applyStartLayout(next.layout); // 覚えた境目の位置は、開いたときの最初の状態から 1 回だけ入れる（REQ-381）
    applyStartAi(next.ai); // AI の欄の境目の位置も同じ（REQ-382）
  }
  const name = $("file-name");
  name.value = next.name === null ? "" : next.name;
  showTail(name);
  text($("summary"), next.summary === null ? "" : next.summary);
  $("empty").hidden = next.loaded;
  $("output-wrap").hidden = !next.loaded;
  $("export").disabled = !next.can_export;
  text(statusMain, next.loading ? texts["log_view.status.loading"] : next.status);

  const devicesKey = JSON.stringify(next.devices);
  if (devicesKey !== shown.devices) {
    shown.devices = devicesKey;
    drawDevices(next.devices);
  }
  const commandsKey = `${next.device}:${JSON.stringify(next.commands)}`;
  if (commandsKey !== shown.commands) {
    shown.commands = commandsKey;
    drawCommands(next.commands, next.device);
  }
  fitLists(); // 行が描かれて最小の高さが変わったときも、一覧が中身より低くならないようにする
  // 見出しの「全部」のチェック（全部の機器の全部のコマンド・表示中の機器の全部のコマンド）と AI の欄
  drawHeadCheck($("check-all-devices"), next.check_all, next.loaded);
  drawHeadCheck($("check-all-commands"), next.commands_check, next.loaded);
  renderAiHidden(next.ai.hidden);
  fitAi();
  if (logAsk !== null) {
    logAsk.render(next.ai);
  }
  selectedRow($("devices-table").tBodies[0], next.device);
  selectedRow($("commands-table").tBodies[0], next.segment);
  // 選ぶ場所が変わったときだけ、選んだ行を一覧の見える所へ出す（利用者が動かしたスクロールを戻さない）
  const position = `${next.device}:${next.segment}:${commandsKey}`;
  if (position !== shown.position) {
    shown.position = position;
    for (const [table, index] of [
      ["devices-table", next.device],
      ["commands-table", next.segment],
    ]) {
      const row = $(table).tBodies[0].rows[index];
      if (row !== undefined) {
        row.scrollIntoView({ block: "nearest" });
      }
    }
  }

  if (next.output_revision !== shown.output) {
    shown.output = next.output_revision;
    loadOutput();
  }
  if (next.notice !== null && next.notice.id !== shown.notice) {
    shown.notice = next.notice.id;
    notice(next.notice.title, next.notice.body, next.notice.ok);
  }
}

// Python が返した結果を描く。失敗は下の行に出す。知らせ（読めなかった・書けなかった）は描いた後に出す。
async function handle(result) {
  if (!result.ok) {
    text(statusMain, result.message);
    return;
  }
  render(result.view);
  if (result.notice) {
    await notice(result.notice.title, result.notice.body, result.notice.ok);
  }
}

async function selectPosition(device, segment) {
  if (view === null) {
    return;
  }
  await handle(await call("select", device, segment));
}

// AI に送るチェック（REQ-382）。チェックは Python が持ち、返った状態で描き直す（表示中の区間は変わらない）
async function setDeviceCheck(device, on) {
  await handle(await call("set_device_check", device, on));
}

async function setSegmentCheck(device, segment, on) {
  await handle(await call("set_check", device, segment, on));
}

async function setAllChecks(on) {
  await handle(await call("set_all_checks", on));
}

// Python からの知らせ（"log_view.changed"）で state() を呼び直す。呼んでいる途中なら、終わってからもう 1 回。
let loading = false;
let again = false;

async function reload() {
  if (loading) {
    again = true;
    return;
  }
  loading = true;
  try {
    do {
      again = false;
      await handle(await call("state"));
    } while (again);
  } finally {
    loading = false;
  }
}

// --- 出力（選んでいる区間の行だけ） ---

async function loadOutput() {
  const result = await call("output");
  if (!result.ok) {
    text(statusMain, result.message);
    return;
  }
  if (result.output.revision < outputRevision) {
    return; // 遅れて届いた古い出力
  }
  outputRevision = result.output.revision;
  drawOutput(result.output);
}

// 文字の幅（等幅の文字数）。Tab は次の TAB_WIDTH の倍数まで進む。全角の文字は 2 文字分として見積もる。
function visualWidth(line) {
  if (!/[^\x20-\x7e]/.test(line)) {
    return line.length;
  }
  let width = 0;
  for (const char of line) {
    if (char === "\t") {
      width += TAB_WIDTH - (width % TAB_WIDTH);
    } else {
      width += char.codePointAt(0) > 0x2e7f ? 2 : 1;
    }
  }
  return width;
}

// 探す行の並び（元の行があれば先頭に付ける）の中の、本文の行が始まる番号
function bodyOffset() {
  return output.prompt_line === null || output.prompt_line === undefined ? 0 : 1;
}

// 探す行の並びの index の文字（元の行も本文の行も同じに引く）
function lineText(index) {
  const offset = bodyOffset();
  return offset === 1 && index === 0 ? output.prompt_line : output.lines[index - offset];
}

function searchableCount() {
  return output.lines.length + bodyOffset();
}

// 見えない測り用の要素（span.pilot-log-probe）を host に一時的に入れて幅（px）を読み、すぐ外す（同じ処理の中なので画面は描かれない）。
// content は文字か要素。host の書体（と太さ）で測る。font を渡したときは、その書体の指定（computed style の font-family と font-size）で測る
function probeWidth(host, content, font = null) {
  const probe = make("span", "pilot-log-probe", null);
  probe.append(content);
  if (font !== null) {
    probe.style.fontFamily = font.fontFamily;
    probe.style.fontSize = font.fontSize;
  }
  host.append(probe);
  const width = probe.getBoundingClientRect().width;
  probe.remove();
  return width;
}

// 表のセルの左右の余白の合計（th と td は同じ余白）
function cellPadding(cell) {
  const style = getComputedStyle(cell);
  return parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
}

// 見出しの文が切れずに入る列の幅: 文の幅 + セルの左右の余白 + つかみの幅（文がつかみの下に隠れないように）
function headNeed(head) {
  return Math.ceil(probeWidth(head, head.textContent) + cellPadding(head) + GRIP_WIDTH);
}

// 出力の表の列の境目の結び付け。区間を選び直すたびに外して付け直す（column_width.js の使い方。列を作り直すときは disconnect してから呼び直す）
let outputBinding = null;
// 行の番号の列を人が動かしたか（押したまま動かした・左右の矢印のキー・Enter・ダブルクリック）。動かしていれば区間を変えても今の幅のまま、
// 動かしていなければ新しい区間の桁に合わせ直す
let numberMoved = false;

function unbindOutputColumns() {
  if (outputBinding !== null) {
    outputBinding.disconnect();
    outputBinding = null;
  }
  for (const grip of $("output-table").querySelectorAll("thead .pilot-col-resize")) {
    grip.remove();
  }
}

// 行の番号の列のつかみを人が動かしたら numberMoved を立てる
function watchNumberGrip(grip) {
  let pressed = false;
  grip.addEventListener("pointerdown", () => {
    pressed = true;
  });
  grip.addEventListener("pointermove", () => {
    if (pressed) {
      numberMoved = true;
    }
  });
  for (const name of ["pointerup", "pointercancel"]) {
    grip.addEventListener(name, () => {
      pressed = false;
    });
  }
  grip.addEventListener("dblclick", () => {
    numberMoved = true;
  });
  grip.addEventListener("keydown", (event) => {
    if (["ArrowLeft", "ArrowRight", "Enter"].includes(event.key)) {
      numberMoved = true;
    }
  });
}

// 出力の表の列の幅（px）を、行の番号の桁数といちばん長い行（元の行も含む）に合わせて入れ、境目を付ける。
// 行の番号の列: 見出し「行」と今の区間のいちばん大きい行の番号（等幅）の大きい方（人が動かしていれば今の幅のまま）。
// 出力の列: 残りを埋める列。最低の幅はいちばん長い行（今の横スクロールは残る）。出力の行が無い間は結び付けない
function sizeOutputTable(lines) {
  unbindOutputColumns();
  if (searchableCount() === 0) {
    return;
  }
  let longest = 0;
  for (const line of lines) {
    longest = Math.max(longest, visualWidth(line));
  }
  if (bodyOffset() === 1) {
    longest = Math.max(longest, visualWidth(output.prompt_line));
  }
  const table = $("output-table");
  const heads = Array.from(table.tHead.rows[0].cells);
  const mono = getComputedStyle(table); // 出力の表の書体（等幅）で 1 文字の幅を測る
  const chars = 20;
  const ch = probeWidth(table, "0".repeat(chars), mono) / chars;
  const pad = cellPadding(heads[0]);
  const digits = String(Math.max(lines.length, 1)).length;
  const numberNeed = Math.ceil(Math.max(headNeed(heads[0]), digits * ch + pad));
  const numberCol = $("output-number-col");
  const kept = parseFloat(numberCol.style.width);
  const numberPx = numberMoved && kept > 0 ? kept : numberNeed;
  const textMin = Math.ceil((longest + TEXT_MARGIN_CH) * ch + pad);
  numberCol.style.width = `${numberPx}px`;
  // 出力の列と表の幅は空けて、残りを埋める列にする（結び付けた部品が最初に幅を決める）
  $("output-text-col").style.width = "";
  table.style.width = "";
  const columns = [
    { col: numberCol, head: heads[0], handle: makeGrip(texts["compare.col.resize"]) },
    { col: $("output-text-col"), head: heads[1], handle: makeGrip(texts["compare.col.resize"]) },
  ];
  for (const column of columns) {
    column.head.append(column.handle);
  }
  watchNumberGrip(columns[0].handle);
  outputBinding = bindColumnWidths(table, columns, { fill: 1, fillMin: textMin });
}

// 1 行の文字を入れる。探している文字に当たった所は <mark> で印を付け、今の当たりにはもう 1 つ印を付ける。
function fillLine(cell, index) {
  const line = lineText(index);
  const found = hitsByLine.get(index);
  cell.replaceChildren();
  if (found === undefined) {
    cell.append(line);
    return;
  }
  let at = 0;
  for (const hit of found) {
    if (hit.start > at) {
      cell.append(line.slice(at, hit.start));
    }
    const mark = make("mark", "pilot-mark pilot-mark--added", line.slice(hit.start, hit.end));
    if (hit.id === currentHit) {
      mark.classList.add("pilot-log-hit-current");
    }
    cell.append(mark);
    at = hit.end;
  }
  if (at < line.length) {
    cell.append(line.slice(at));
  }
}

// index は本文の行の番号（探す行の並びの番号は bodyOffset() を足す）
function rowFor(index) {
  const tr = document.createElement("tr");
  const cell = make("td", "pilot-log-line", null);
  fillLine(cell, index + bodyOffset());
  tr.append(make("td", "pilot-num pilot-log-number", index + 1), cell);
  return tr;
}

// 表の 1 行目の見出しの行（元の行。行の番号の列は空）。文字は textContent で入れる（fillLine）。rowEls には入れない
function promptRowFor() {
  const tr = make("tr", "pilot-log-prompt", null);
  tr.setAttribute("aria-label", texts["log_view.output.prompt_row"]);
  const cell = make("td", "pilot-log-line", null);
  fillLine(cell, 0);
  tr.append(make("td", "pilot-num pilot-log-number", ""), cell);
  return tr;
}

// 描いていない行を limit 行ぶん描く。続きがあれば次の番に回す。token が古ければ止める
function paintMore(token, limit) {
  if (token !== paintToken) {
    return;
  }
  const end = Math.min(output.lines.length, rendered + limit);
  const fragment = document.createDocumentFragment();
  for (let index = rendered; index < end; index += 1) {
    const tr = rowFor(index);
    rowEls.push(tr);
    fragment.append(tr);
  }
  $("output-table").tBodies[0].append(fragment);
  rendered = end;
  if (rendered < output.lines.length) {
    setTimeout(() => paintMore(token, CHUNK), 0);
  }
}

// 探す行の並びの index の行まで描き終える（当たりへ飛ぶとき、まだ描いていない行を待たない。元の行は最初から描いてある）
function ensureRendered(index) {
  const body = index - bodyOffset();
  while (rendered <= body && rendered < output.lines.length) {
    paintMore(paintToken, Math.max(CHUNK, body + 1 - rendered));
  }
}

// 探す行の並びの index の行の要素（まだ描いていなければ undefined）
function elementFor(index) {
  const offset = bodyOffset();
  return offset === 1 && index === 0 ? (promptEl ?? undefined) : rowEls[index - offset];
}

function drawOutput(next) {
  output = next;
  paintToken += 1;
  rendered = 0;
  rowEls = [];
  promptEl = null;
  $("output-table").tBodies[0].replaceChildren();
  $("output-wrap").scrollTop = 0;
  $("output-wrap").scrollLeft = 0;
  sizeOutputTable(next.lines);
  $("copy-output").disabled = next.lines.length === 0;
  $("copy-with-command").disabled = next.lines.length === 0;
  runFind(false); // 探す文字は変えず、今の区間の行で探し直す（描く前に当たりを作る）
  if (bodyOffset() === 1) {
    promptEl = promptRowFor(); // 表の 1 行目。本文の行は paintMore がこの後ろに足す
    $("output-table").tBodies[0].append(promptEl);
  }
  paintMore(paintToken, CHUNK);
  if (currentHit >= 0) {
    showCurrentHit();
  }
}

// --- 探す（文字そのまま・大文字と小文字を区別しない。範囲は今の区間の行だけ） ---

function escapePattern(query) {
  return query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function findHits(query) {
  const found = [];
  if (query === "") {
    return found;
  }
  const pattern = new RegExp(escapePattern(query), "gi");
  // 元の行（見出しの行）も最初の行として探す。当たった数に入る
  for (let line = 0; line < searchableCount(); line += 1) {
    const value = lineText(line);
    pattern.lastIndex = 0;
    for (const match of value.matchAll(pattern)) {
      found.push({ id: found.length, line, start: match.index, end: match.index + match[0].length });
    }
  }
  return found;
}

function showFindCount(query) {
  let label = "";
  if (query !== "") {
    label =
      hits.length === 0
        ? texts["log_view.find.none"]
        : texts["log_view.find.count"].replace("{current}", String(currentHit + 1)).replace("{total}", String(hits.length));
  }
  text($("find-count"), label);
  $("find-prev").disabled = hits.length === 0;
  $("find-next").disabled = hits.length === 0;
}

// 描いてある行のうち、lines の行を描き直す
function refreshLines(lines) {
  for (const line of lines) {
    const el = elementFor(line);
    if (el !== undefined && line < searchableCount()) {
      fillLine(el.cells[1], line);
    }
  }
}

// 当たりを求め直して印を付ける。jump が真なら、最初の当たりへ移る。
function runFind(jump) {
  const query = $("find").value;
  const touched = new Set(hitsByLine.keys());
  hits = findHits(query);
  hitsByLine = new Map();
  for (const hit of hits) {
    if (!hitsByLine.has(hit.line)) {
      hitsByLine.set(hit.line, []);
    }
    hitsByLine.get(hit.line).push(hit);
    touched.add(hit.line);
  }
  currentHit = hits.length === 0 ? -1 : 0;
  refreshLines(touched);
  showFindCount(query);
  if (jump && currentHit >= 0) {
    showCurrentHit();
  }
}

// 今の当たりの行を見える所へ出す（まだ描いていなければ、そこまで描く）
function showCurrentHit() {
  const hit = hits[currentHit];
  if (hit === undefined) {
    return;
  }
  ensureRendered(hit.line);
  elementFor(hit.line)?.scrollIntoView({ block: "center", inline: "nearest" });
}

// 次へ・前へ（当たりの先頭と最後はつなぐ）。今の当たりの印を付け替える
function stepFind(step) {
  if (hits.length === 0) {
    return;
  }
  const before = hits[currentHit].line;
  currentHit = (currentHit + step + hits.length) % hits.length;
  ensureRendered(hits[currentHit].line);
  refreshLines(new Set([before, hits[currentHit].line]));
  showFindCount($("find").value);
  showCurrentHit();
}

// --- コピー（出力だけ／コマンドの行も付けて。「出力をコピー」は本文だけ。「コマンドの行も付けて」は、元の行（prompt_line）があればそれを、無ければ今のコマンドの行を先頭に付ける） ---

function copyOutput(withCommand, button) {
  if (output.lines.length === 0) {
    return Promise.resolve();
  }
  let value = `${output.lines.join("\n")}\n`;
  if (withCommand) {
    const head = bodyOffset() === 1 ? output.prompt_line : output.command;
    if (head !== null) {
      value = `${head}\n${value}`;
    }
  }
  return copyText(value, button);
}

// --- 入れ方・書き出し ---

// 貼り付け。既定のフォーカスは欄。「OK」なら文字を Python に渡す（ここには残さない）。
async function pasteLog() {
  const input = make("textarea", "pilot-input pilot-mono pilot-nowrap", null);
  input.spellcheck = false;
  const ok = await panel(
    texts["log_view.paste.title"],
    [make("p", "", texts["log_view.paste.prompt"]), input],
    texts["compare.dialog.ok"],
    texts["compare.dialog.cancel"],
    input,
  );
  const value = input.value;
  input.value = "";
  if (ok) {
    await handle(await call("paste", value));
  }
}

// 「機器ごとのファイルに書き出す…」。1 回目は Python が返す確かめを出し、「フォルダーを選ぶ…」のときだけ呼び直す
async function exportDevices() {
  const first = await call("export_devices", false);
  await handle(first);
  if (!first.ok || !first.split_confirm) {
    return;
  }
  const ask = first.split_confirm;
  if (await confirm(ask.title, ask.body, ask.yes, ask.no)) {
    await handle(await call("export_devices", true));
  }
}

// --- 左右の面の境目（ADR-0148 決定2 と同じ動き）と、機器の一覧とコマンドの一覧の上下の境目（REQ-381）。
// 幅は要素の CSS 変数・flex-grow に入れる。動かし終えたときに、位置を割合で Python に覚えてもらう（ui-state.json）。
// 動かせる範囲は、覚えた位置を戻せる範囲（state の layout の split_left_range・lists_split_range）で止める ---

const split = $("split");
const splitHandle = $("split-handle");
const listsHandle = $("lists-handle");
// 開いたときの状態の layout（覚えた位置と、境目を動かせる範囲。最初の状態を描くときに 1 回だけ入れる）
let layout = null;
// 利用者が動かした後の左の面の幅の割合（決めた幅 ÷ 全体の幅）。動かす前は null で、CSS の初めの幅（32%。覚えた位置があればそれ）のまま
let leftRatio = null;
let dragging = null;
let listsDragging = null;

// 割合を小数 3 桁に丸め、範囲 [low, high] の中に収める（丸めで範囲の外にならないように、丸めた後に収める）
function rememberable(ratio, [low, high]) {
  return Math.min(Math.max(Math.round(ratio * 1000) / 1000, low), high);
}

// 動かし終えた位置を Python に覚えてもらう（書けなくても画面は止めず、知らせない。Python は同じ値なら書かない）
function remember(name, ratio) {
  call(name, ratio).catch(() => {});
}

// 左の面の幅を px で決める。下限は PANE_MIN と全体の split_left_range の下端の大きい方、上限は全体から PANE_MIN を引いた幅と
// 全体の split_left_range の上端の小さい方（上限が下限より小さいときは下限）。境目が隠れている（縦に積んでいる）ときは何もしない（null を返す）。
function setLeftWidth(px) {
  const total = split.clientWidth - splitHandle.offsetWidth;
  if (layout === null || splitHandle.offsetParent === null || total <= 0) {
    return null;
  }
  const [lowRatio, highRatio] = layout.split_left_range;
  const low = Math.max(PANE_MIN, total * lowRatio);
  // AI の欄が出ている間は、その幅（と境目）も残す（出力は PANE_MIN より狭くしない）
  const aiTaken = aiPane.hidden ? 0 : aiPane.offsetWidth + aiHandle.offsetWidth;
  const high = Math.max(low, Math.min(total - PANE_MIN - aiTaken, total * highRatio));
  const width = Math.round(Math.min(Math.max(px, low), high));
  split.style.setProperty("--pilot-split-left", `${width}px`);
  splitHandle.setAttribute("aria-valuenow", String(Math.round((width / total) * 100)));
  return width / total;
}

// 利用者がドラッグ・矢印のキーで動かした幅を決め、割合を覚える（境目が隠れているときは今の割合のまま）。動かせたら割合を返す
function moveLeftWidth(px) {
  const ratio = setLeftWidth(px);
  if (ratio !== null) {
    leftRatio = ratio;
  }
  return ratio;
}

// 動かし終えた左右の境目の位置を Python に覚えてもらう
function rememberSplit() {
  if (leftRatio !== null && layout !== null) {
    remember("remember_split", rememberable(leftRatio, layout.split_left_range));
  }
}

function currentLeftWidth() {
  return $("lists").getBoundingClientRect().width;
}

splitHandle.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }
  event.preventDefault();
  splitHandle.setPointerCapture(event.pointerId);
  dragging = { x: event.clientX, width: currentLeftWidth(), moved: false };
});
splitHandle.addEventListener("pointermove", (event) => {
  if (dragging !== null) {
    dragging.moved = true;
    moveLeftWidth(dragging.width + event.clientX - dragging.x);
  }
});
for (const name of ["pointerup", "pointercancel"]) {
  splitHandle.addEventListener(name, (event) => {
    const moved = dragging !== null && dragging.moved;
    dragging = null;
    if (splitHandle.hasPointerCapture(event.pointerId)) {
      splitHandle.releasePointerCapture(event.pointerId);
    }
    if (moved) {
      rememberSplit(); // ドラッグを離したときだけ覚える
    }
  });
}
// 左右の矢印のキーで SPLIT_STEP px ずつ動かす
splitHandle.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    if (moveLeftWidth(currentLeftWidth() + (event.key === "ArrowLeft" ? -SPLIT_STEP : SPLIT_STEP)) !== null) {
      rememberSplit();
    }
  }
});
// 窓の大きさが変わったら、覚えた割合から今の全体の幅で幅を決め直し、同じ範囲と最小の幅の中に詰める（割合は書き換えない・覚え直さない）
window.addEventListener("resize", () => {
  if (leftRatio !== null) {
    setLeftWidth(leftRatio * (split.clientWidth - splitHandle.offsetWidth));
  }
});

// --- 機器の一覧とコマンドの一覧の上下の境目。2 つの一覧の高さの比（flex-grow を r と 1−r。r は機器の一覧の割合）で持つ。
// 比で持つので、窓の高さが変わっても割合が保たれる。初めは CSS の 2:3（r=0.4）か、覚えた位置 ---

const devicesList = $("devices-list");
// 利用者が決めた（または覚えていた）機器の一覧の割合。動かす前で覚えた位置も無いときは null で、初めの 2:3（DEFAULT_LISTS_RATIO）のまま
let listsRatio = null;
const commandsList = $("commands-list");

// 表の枠の外の高さ（機器の一覧は題を含む）: 表の枠以外の子（題）の高さと余白 + 表の枠の枠線・横のスクロールバー。
// 一覧の今の高さからは求めない（一覧が中身より低くなっているときも、同じ値になるように）
function listOutside(section, wrap) {
  let outside = wrap.offsetHeight - wrap.clientHeight;
  for (const child of section.children) {
    if (child !== wrap) {
      const style = getComputedStyle(child);
      outside += child.getBoundingClientRect().height + parseFloat(style.marginTop) + parseFloat(style.marginBottom);
    }
  }
  return outside;
}

// 一覧の高さの下限: 見出しの行 + 2 行（行がまだ無ければ見出しの行の高さで見積もる。CSS の表の枠の最小の高さより低くはしない）+ 表の枠の外
function listMinHeight(section) {
  const wrap = section.querySelector(".pilot-table-wrap");
  const table = wrap.querySelector("table");
  const headHeight = table.tHead.rows[0].getBoundingClientRect().height;
  const row = table.tBodies[0].rows[0];
  const rowHeight = row === undefined ? headHeight : row.getBoundingClientRect().height;
  const frameMin = parseFloat(getComputedStyle(wrap).minHeight) || 0;
  return Math.max(headHeight + 2 * rowHeight, frameMin) + listOutside(section, wrap);
}

function devicesHeight() {
  return devicesList.getBoundingClientRect().height;
}

// 機器の一覧の割合 r を入れる（2 つの一覧の flex-grow を r と 1−r にする）
function applyListsRatio(ratio) {
  devicesList.style.flexGrow = String(ratio);
  commandsList.style.flexGrow = String(1 - ratio);
  listsHandle.setAttribute("aria-valuenow", String(Math.round(ratio * 100)));
}

// 機器の一覧の高さを px で決める。下限は「最小の高さと全体の lists_split_range の下端」の大きい方、上限は「全体からコマンドの一覧の
// 最小の高さを引いた値と全体の lists_split_range の上端」の小さい方（上限が下限より小さいときは下限）。決めた割合を返す
function setListsHeight(px) {
  const total = devicesHeight() + commandsList.getBoundingClientRect().height;
  if (layout === null || total <= 0) {
    return null;
  }
  const [lowRatio, highRatio] = layout.lists_split_range;
  const low = Math.max(listMinHeight(devicesList), total * lowRatio);
  const high = Math.max(low, Math.min(total - listMinHeight(commandsList), total * highRatio));
  const height = Math.min(Math.max(px, low), high);
  const ratio = height / total;
  applyListsRatio(ratio);
  return ratio;
}

// 利用者がドラッグ・矢印のキーで動かした高さを決め、決めた割合を持つ（listsRatio）。動かせたら割合を返す
function moveListsHeight(px) {
  const ratio = setListsHeight(px);
  if (ratio !== null) {
    listsRatio = ratio;
  }
  return ratio;
}

// 窓の大きさが変わったとき・開いたときに覚えた位置を入れるとき・一覧の行を描き直したときに、決めた割合（無ければ初めの 2:3）から
// 今の全体の高さで高さを決め直し、setListsHeight と同じ下限・上限で画面に出す割合だけを詰める（決めた割合は書き換えない・覚え直さない）
function fitLists() {
  const total = devicesHeight() + commandsList.getBoundingClientRect().height;
  if (total > 0) {
    setListsHeight((listsRatio ?? DEFAULT_LISTS_RATIO) * total);
  }
}

// 動かし終えた上下の境目の位置を Python に覚えてもらう
function rememberLists(ratio) {
  if (ratio !== null && layout !== null) {
    remember("remember_lists_split", rememberable(ratio, layout.lists_split_range));
  }
}

listsHandle.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }
  event.preventDefault();
  listsHandle.setPointerCapture(event.pointerId);
  listsDragging = { y: event.clientY, height: devicesHeight(), ratio: null };
});
listsHandle.addEventListener("pointermove", (event) => {
  if (listsDragging !== null) {
    listsDragging.ratio = moveListsHeight(listsDragging.height + event.clientY - listsDragging.y);
  }
});
for (const name of ["pointerup", "pointercancel"]) {
  listsHandle.addEventListener(name, (event) => {
    const ratio = listsDragging === null ? null : listsDragging.ratio;
    listsDragging = null;
    if (listsHandle.hasPointerCapture(event.pointerId)) {
      listsHandle.releasePointerCapture(event.pointerId);
    }
    rememberLists(ratio); // ドラッグを離したときだけ覚える（動かしていなければ ratio は null）
  });
}
// 上下の矢印のキーで LISTS_STEP px ずつ動かす（機器・コマンドを選ぶ一覧の上下の矢印のキーは表の枠の上だけで受けるので、ぶつからない）
listsHandle.addEventListener("keydown", (event) => {
  if (event.key === "ArrowUp" || event.key === "ArrowDown") {
    event.preventDefault();
    rememberLists(moveListsHeight(devicesHeight() + (event.key === "ArrowUp" ? -LISTS_STEP : LISTS_STEP)));
  }
});

// 窓の大きさが変わったら、上下の境目も決めた割合から決め直す（最小の高さで詰める。割合は書き換えない・覚え直さない）
window.addEventListener("resize", () => fitLists());

// --- 出力と AI の欄の境目と、AI の欄を隠す・出す（REQ-382）。AI の欄の幅は CSS 変数 --pilot-log-ai-width に入れる（初めは窓の 28%）。
// 位置は「出力の側の割合」（窓の左の端から境目までの幅 ÷ 窓の幅）で持ち、動かし終えたときに Python に覚えてもらう（ui-state.json）。
// 動かせる範囲は、覚えた位置を戻せる範囲（state の ai.split_range）で止め、3 つの面はどれも PANE_MIN より狭くしない ---

const aiPane = $("ai-pane");
const aiHandle = $("ai-split-handle");
// 利用者が決めた（または覚えていた）出力の側の割合。動かす前で覚えた位置も無いときは null で、CSS の初めの幅のまま
let outputRatio = null;
let aiDragging = null;
// 開いたときに Python が返した AI の欄の動かせる範囲（出力の側の割合の下端と上端）
let aiRange = null;

// AI の欄の幅を px で決める。下限は PANE_MIN と範囲の上端から決まる幅の大きい方、上限は範囲の下端から決まる幅と
// 「全体から左の面と出力の最小の幅を引いた値」の小さい方（上限が下限より小さいときは下限）。決めた出力の側の割合を返す。
// AI の欄が隠れている（縦に積んでいる）ときは何もしない（null を返す）
function setAiWidth(px) {
  const total = split.clientWidth;
  if (aiRange === null || aiPane.hidden || aiHandle.offsetParent === null || total <= 0) {
    return null;
  }
  const [lowRatio, highRatio] = aiRange;
  const low = Math.max(PANE_MIN, total * (1 - highRatio));
  const room = total - $("lists").getBoundingClientRect().width - splitHandle.offsetWidth - aiHandle.offsetWidth - PANE_MIN;
  const high = Math.max(low, Math.min(total * (1 - lowRatio), room));
  const width = Math.round(Math.min(Math.max(px, low), high));
  split.style.setProperty("--pilot-log-ai-width", `${width}px`);
  aiHandle.setAttribute("aria-valuenow", String(Math.round((1 - width / total) * 100)));
  return 1 - width / total;
}

// 利用者がドラッグ・矢印のキーで動かした幅を決め、割合を持つ。動かせたら割合を返す
function moveAiWidth(px) {
  const ratio = setAiWidth(px);
  if (ratio !== null) {
    outputRatio = ratio;
  }
  return ratio;
}

// 決めた割合（無ければ CSS の初めの幅のまま）から今の窓の幅で幅を決め直す。覚え直さない（窓の大きさが変わったとき・隠す／出すとき・状態を描くとき）
function fitAi() {
  if (outputRatio !== null) {
    setAiWidth((1 - outputRatio) * split.clientWidth);
  }
}

// 動かし終えた位置を Python に覚えてもらう
function rememberAi() {
  if (outputRatio !== null && aiRange !== null) {
    remember("remember_ai_split", rememberable(outputRatio, aiRange));
  }
}

aiHandle.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }
  event.preventDefault();
  aiHandle.setPointerCapture(event.pointerId);
  aiDragging = { x: event.clientX, width: aiPane.getBoundingClientRect().width, moved: false };
});
aiHandle.addEventListener("pointermove", (event) => {
  if (aiDragging !== null) {
    aiDragging.moved = true;
    moveAiWidth(aiDragging.width - (event.clientX - aiDragging.x)); // 右へ動かすと AI の欄は狭くなる
  }
});
for (const name of ["pointerup", "pointercancel"]) {
  aiHandle.addEventListener(name, (event) => {
    const moved = aiDragging !== null && aiDragging.moved;
    aiDragging = null;
    if (aiHandle.hasPointerCapture(event.pointerId)) {
      aiHandle.releasePointerCapture(event.pointerId);
    }
    if (moved) {
      rememberAi(); // ドラッグを離したときだけ覚える
    }
  });
}
// 左右の矢印のキーで SPLIT_STEP px ずつ動かす（左へ動かすと AI の欄は広くなる）
aiHandle.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    const width = aiPane.getBoundingClientRect().width + (event.key === "ArrowLeft" ? SPLIT_STEP : -SPLIT_STEP);
    if (moveAiWidth(width) !== null) {
      rememberAi();
    }
  }
});
window.addEventListener("resize", () => fitAi());

// 開いたときの状態の ai を 1 回だけ入れる（覚えた位置があれば。% で入れ、すぐ px で詰め直す）
function applyStartAi(next) {
  aiRange = next.split_range;
  if (next.split !== null) {
    outputRatio = next.split;
    split.style.setProperty("--pilot-log-ai-width", `${(1 - next.split) * 100}%`);
    aiHandle.setAttribute("aria-valuenow", String(Math.round(next.split * 100)));
  }
}

// AI の欄を隠している間は、面と境目を隠して出力を広げ、出力の道具の行に「« AI」（出す）を出す。隠したときに「»」にフォーカスがあれば
// 「« AI」へ、出したときに「« AI」にあれば「»」へ移す（キーボードだけで戻れるように）。隠した状態は Python の ai.hidden が持つ
function renderAiHidden(hidden) {
  const active = document.activeElement;
  const hadHide = active === $("ai-hide");
  const hadShow = active === $("log-ai-show");
  aiPane.hidden = hidden;
  aiHandle.hidden = hidden;
  $("log-ai-show").hidden = !hidden;
  if (hidden && hadHide) {
    $("log-ai-show").focus();
  } else if (!hidden && hadShow) {
    $("ai-hide").focus();
  }
}

// 開いたときの状態の layout を 1 回だけ入れる（覚えた位置があれば。% で入れる）
function applyStartLayout(next) {
  layout = next;
  if (next.split_left !== null) {
    split.style.setProperty("--pilot-split-left", `${next.split_left * 100}%`);
    splitHandle.setAttribute("aria-valuenow", String(Math.round(next.split_left * 100)));
  }
  if (next.lists_split !== null) {
    listsRatio = next.lists_split;
    applyListsRatio(listsRatio);
  }
  fitLists(); // 今の窓の高さで最小の高さより低くならないように、画面に出す割合だけを詰める
}

// --- 2 つの一覧の列の幅の境目（REQ-365 の部品）。初めの幅は画面の言語の文を測って決める。動かした幅は保存しない ---

// 一覧の列の初めの幅（px。埋める列は null）。見出しの文が切れずに入る幅を土台にする（headNeed）
function initialWidths(id, heads) {
  // 先頭の列は AI に送るチェックの列（CSS の幅のまま。境目は付けない）
  if (id === "devices-table") {
    // 「コマンド」の列は見出しが入る幅、ホスト名の列は残り全部
    return [null, null, headNeed(heads[2])];
  }
  const pad = cellPadding(heads[1]);
  const wrap = $("commands-wrap");
  // 「行」の列は見出しと 4 桁の数、「印」の列は見出しと一番長い印の文（印の形の枠を含む）、コマンドの列は残り全部
  const digits = probeWidth(wrap, "0000") + pad;
  const marks = ["log_view.mark.error", "log_view.mark.preamble"].map(
    (key) => probeWidth(wrap, make("span", "pilot-mark", texts[key])) + pad,
  );
  return [null, null, Math.ceil(Math.max(headNeed(heads[2]), digits)), Math.ceil(Math.max(headNeed(heads[3]), ...marks))];
}

function bindTableColumns() {
  for (const item of COLUMN_TABLES) {
    const target = $(item.id);
    const cols = Array.from(target.querySelectorAll("colgroup > col"));
    const heads = Array.from(target.tHead.rows[0].cells);
    // 幅は bindColumnWidths を呼ぶ前に <col> へ入れる（部品が最初に幅を固めるときにこの幅が使われる）。つかみを付ける前に測る
    initialWidths(item.id, heads).forEach((px, index) => {
      if (px !== null) {
        cols[index].style.width = `${px}px`;
      }
    });
    const columns = heads.map((head, index) => {
      if (index === 0) {
        return { col: cols[0], head, handle: null }; // チェックの列には境目を付けない
      }
      const grip = makeGrip(texts["compare.col.resize"]);
      head.append(grip);
      return { col: cols[index], head, handle: grip };
    });
    bindColumnWidths(target, columns, { fill: item.fill, fillMin: item.fillMin });
  }
}

// 一覧の上下の矢印のキーで、前後の機器・コマンドを選ぶ
function bindArrowKeys(wrapId, step) {
  $(wrapId).addEventListener("keydown", (event) => {
    if (view === null || (event.key !== "ArrowUp" && event.key !== "ArrowDown")) {
      return;
    }
    event.preventDefault();
    step(event.key === "ArrowUp" ? -1 : 1);
  });
}

bindArrowKeys("devices-wrap", (delta) => {
  const next = view.device + delta;
  if (next >= 0 && next < view.devices.length) {
    selectPosition(next, 0);
  }
});
bindArrowKeys("commands-wrap", (delta) => {
  const next = view.segment + delta;
  if (next >= 0 && next < view.commands.length) {
    selectPosition(view.device, next);
  }
});

// --- つなぐ ---

bindTail($("file-name"));
$("open").addEventListener("click", async () => handle(await call("browse")));
$("paste").addEventListener("click", () => pasteLog());
$("export").addEventListener("click", () => exportDevices());
$("find").addEventListener("input", () => runFind(true));
$("find").addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    stepFind(event.shiftKey ? -1 : 1);
  }
});
$("find-next").addEventListener("click", () => stepFind(1));
$("find-prev").addEventListener("click", () => stepFind(-1));
$("copy-output").addEventListener("click", (event) => copyOutput(false, event.currentTarget));
$("copy-with-command").addEventListener("click", (event) => copyOutput(true, event.currentTarget));
$("check-all-devices").addEventListener("change", (event) => setAllChecks(event.currentTarget.checked));
$("check-all-commands").addEventListener("change", (event) => {
  if (view !== null) {
    setDeviceCheck(view.device, event.currentTarget.checked); // 表示中の機器の全部のコマンド
  }
});
$("ai-hide").addEventListener("click", async () => handle(await call("set_ai_hidden", true)));
$("log-ai-show").addEventListener("click", async () => handle(await call("set_ai_hidden", false)));
on("log_view.changed", () => reload());

if (await fillTexts()) {
  logAsk = bindLogAsk({ texts, show: handle });
  bindTableColumns();
  showFindCount("");
  await reload();
}
