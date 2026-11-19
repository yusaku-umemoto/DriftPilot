// 「ログを見る」の窓（ADR-0248 決定2 / REQ-377）。対になる Python の側は driftpilot/gui_web/log_view_api.py。
//
// 比べずに、1 つのログ（ファイルのドロップ・「開く…」・貼り付け）を、機器ごと・コマンドごとに分けて手元で読むだけの窓。何も送らない。
// 状態は Python が持つ。画面は state() が返す JSON を描き、出力の行は output_revision が変わったときだけ output() で受け取る
// （比較の窓の result と同じ形）。描くのは選んでいる区間の行だけで、1 回に CHUNK 行ずつ、窓が固まらないように分けて描く。
//
// ★ 文字は dom.js の make と createElement・textContent・append（文字列は文字として入れる）だけで入れる。
//   文字列を HTML として解釈する書き方は使わない。Python から受け取った文字（ログの行・コマンド・ファイル名）は、
//   属性には title にだけ入れる。Python を呼ぶのは LogViewApi の口だけ（AI に送る口は呼ばない）。

import { call, on } from "./bridge.js";
import { confirm, notice, panel } from "./dialog.js";
import { make, text } from "./dom.js";
import { bindTail, showTail } from "./path_tail.js"; // ファイルの名前の欄は後ろを見せる（REQ-211 §4）
import { bindColumnWidths, makeGrip } from "./column_width.js"; // 2 つの一覧の列の幅の境目（REQ-365）
import { copyText } from "./answer_copy.js"; // コピー（クリップボードが使えないときは選択のコピー）
import "./drop.js"; // ファイルのドロップを印の要素へ送る（REQ-205 / ADR-0147）

const $ = (id) => document.getElementById(id);
const statusMain = $("status-main");

// 要素に結び付かない文（ダイアログ・一覧の印・探すの数・列の境目の名前）
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
];
// 左右の面の境目（ADR-0148 決定2 と同じ）。両側の最小の幅と、矢印のキーで動かす幅
const PANE_MIN = 220;
const SPLIT_STEP = 20;
// 出力の行を 1 回に描く数。残りは続けて描く（窓を固めない）
const CHUNK = 1000;
// 出力の表の幅は、いちばん長い行の文字数（等幅の ch）に足す余白と、行番号の列の余白
const TEXT_MARGIN_CH = 4;
const NUMBER_MARGIN_CH = 2;
const TAB_WIDTH = 8;
// 列の幅の境目を付ける 2 つの表。fill は残りを埋める列の番号、fillMin はその最低の幅（px）
const COLUMN_TABLES = [
  { id: "devices-table", fill: 0, fillMin: 80 },
  { id: "commands-table", fill: 0, fillMin: 100 },
];

let texts = {}; // messages.py から取った文
let view = null; // いま描いている状態
// 描いた一覧の中身（JSON の文字）・出力を取った番号・出した知らせの番号
const shown = { devices: null, commands: null, position: null, output: null, notice: null };
// いま描いている出力。lines は選んでいる区間の行、command はそのコマンド（区切りなしの区間は null）
let output = { command: null, lines: [] };
let outputRevision = -1;
// 出力の描画の状態。token は新しく描き始めるたびに進め、古い続きを止める。rendered は描いた行の数、rowEls は描いた行の要素
let paintToken = 0;
let rendered = 0;
let rowEls = [];
// 探す: 今の区間の行の当たり [{line, start, end}]・行ごとの当たり・今の当たりの番号（無ければ -1）
let hits = [];
let hitsByLine = new Map();
let currentHit = -1;

// <html lang> に入れる言語の鍵（ADR-0190。"en"・"ja"）
const PAGE_LANG = "page.lang";

// data-text・data-label の鍵の文を messages.py から取って入れる（HTML には文を書かない）。取れなければ false。
async function fillTexts() {
  const elements = Array.from(document.querySelectorAll("[data-text]"));
  const labelled = Array.from(document.querySelectorAll("[data-label]"));
  const keys = Array.from(
    new Set([...elements.map((el) => el.dataset.text), ...labelled.map((el) => el.dataset.label), ...DIALOG_TEXTS]),
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
  return true;
}

// --- 状態を描く ---

function selectedRow(body, index) {
  for (const [at, row] of Array.from(body.rows).entries()) {
    row.setAttribute("aria-selected", String(at === index));
  }
}

function drawDevices(rows) {
  const body = $("devices-table").tBodies[0];
  body.replaceChildren();
  for (const [index, row] of rows.entries()) {
    const tr = document.createElement("tr");
    const name = make("td", "", row.name);
    name.title = row.name;
    tr.append(name, make("td", "pilot-num", row.commands));
    tr.addEventListener("click", () => selectPosition(index, 0));
    body.append(tr);
  }
}

function drawCommands(rows, device) {
  const body = $("commands-table").tBodies[0];
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
    tr.append(command, make("td", "pilot-num", row.lines), marks);
    tr.addEventListener("click", () => selectPosition(device, index));
    body.append(tr);
  }
}

// 状態を描く。古い状態（遅れて届いたもの）は捨てる。
function render(next) {
  if (view !== null && next.serial < view.serial) {
    return;
  }
  view = next;
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

// 出力の表の幅を、行番号の桁数といちばん長い行に合わせる（列の幅は ch。表の書体は等幅）
function sizeOutputTable(lines) {
  let longest = 0;
  for (const line of lines) {
    longest = Math.max(longest, visualWidth(line));
  }
  const digits = String(Math.max(lines.length, 1)).length;
  const numberWidth = digits + NUMBER_MARGIN_CH;
  const textWidth = longest + TEXT_MARGIN_CH;
  $("output-number-col").style.width = `${numberWidth}ch`;
  $("output-text-col").style.width = `${textWidth}ch`;
  $("output-table").style.width = `${numberWidth + textWidth}ch`;
}

// 1 行の文字を入れる。探している文字に当たった所は <mark> で印を付け、今の当たりにはもう 1 つ印を付ける。
function fillLine(cell, index) {
  const line = output.lines[index];
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

function rowFor(index) {
  const tr = document.createElement("tr");
  const cell = make("td", "pilot-log-line", null);
  fillLine(cell, index);
  tr.append(make("td", "pilot-num pilot-log-number", index + 1), cell);
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

// index の行まで描き終える（当たりへ飛ぶとき、まだ描いていない行を待たない）
function ensureRendered(index) {
  while (rendered <= index && rendered < output.lines.length) {
    paintMore(paintToken, Math.max(CHUNK, index + 1 - rendered));
  }
}

function drawOutput(next) {
  output = next;
  paintToken += 1;
  rendered = 0;
  rowEls = [];
  $("output-table").tBodies[0].replaceChildren();
  $("output-wrap").scrollTop = 0;
  $("output-wrap").scrollLeft = 0;
  sizeOutputTable(next.lines);
  $("copy-output").disabled = next.lines.length === 0;
  $("copy-with-command").disabled = next.lines.length === 0;
  runFind(false); // 探す文字は変えず、今の区間の行で探し直す（描く前に当たりを作る）
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
  for (const [line, value] of output.lines.entries()) {
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
    if (line < rendered) {
      fillLine(rowEls[line].cells[1], line);
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
  rowEls[hit.line].scrollIntoView({ block: "center", inline: "nearest" });
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

// --- コピー（出力だけ／コマンドの行も付けて。元のプロンプトは持たないので付けない） ---

function copyOutput(withCommand, button) {
  if (output.lines.length === 0) {
    return Promise.resolve();
  }
  let value = `${output.lines.join("\n")}\n`;
  if (withCommand && output.command !== null) {
    value = `${output.command}\n${value}`;
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

// --- 左右の面の境目（ADR-0148 決定2 と同じ動き）。幅は要素の CSS 変数に入れるだけで、保存しない ---

const split = $("split");
const splitHandle = $("split-handle");
// 利用者が動かした後の左の面の幅の割合（決めた幅 ÷ 全体の幅）。動かす前は null で、CSS の初めの幅（32%）のまま
let leftRatio = null;
let dragging = null;

// 左の面の幅を px で決める。両側とも PANE_MIN より狭くしない。境目が隠れている（縦に積んでいる）ときは何もしない（null を返す）。
function setLeftWidth(px) {
  const total = split.clientWidth - splitHandle.offsetWidth;
  if (splitHandle.offsetParent === null || total <= 0) {
    return null;
  }
  const width = Math.round(Math.min(Math.max(px, PANE_MIN), Math.max(PANE_MIN, total - PANE_MIN)));
  split.style.setProperty("--pilot-split-left", `${width}px`);
  splitHandle.setAttribute("aria-valuenow", String(Math.round((width / total) * 100)));
  return width / total;
}

// 利用者がドラッグ・矢印のキーで動かした幅を決め、割合を覚える（境目が隠れているときは今の割合のまま）
function moveLeftWidth(px) {
  const ratio = setLeftWidth(px);
  if (ratio !== null) {
    leftRatio = ratio;
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
  dragging = { x: event.clientX, width: currentLeftWidth() };
});
splitHandle.addEventListener("pointermove", (event) => {
  if (dragging !== null) {
    moveLeftWidth(dragging.width + event.clientX - dragging.x);
  }
});
for (const name of ["pointerup", "pointercancel"]) {
  splitHandle.addEventListener(name, (event) => {
    dragging = null;
    if (splitHandle.hasPointerCapture(event.pointerId)) {
      splitHandle.releasePointerCapture(event.pointerId);
    }
  });
}
// 左右の矢印のキーで SPLIT_STEP px ずつ動かす
splitHandle.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    moveLeftWidth(currentLeftWidth() + (event.key === "ArrowLeft" ? -SPLIT_STEP : SPLIT_STEP));
  }
});
// 窓の大きさが変わったら、覚えた割合から今の全体の幅で幅を決め直し、最小の幅の中に詰める（割合は書き換えない）
window.addEventListener("resize", () => {
  if (leftRatio !== null) {
    setLeftWidth(leftRatio * (split.clientWidth - splitHandle.offsetWidth));
  }
});

// --- 2 つの一覧の列の幅の境目（REQ-365 の部品）。幅は保存しない ---

function bindTableColumns() {
  for (const item of COLUMN_TABLES) {
    const target = $(item.id);
    const cols = Array.from(target.querySelectorAll("colgroup > col"));
    const heads = Array.from(target.tHead.rows[0].cells);
    const columns = heads.map((head, index) => {
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
on("log_view.changed", () => reload());

if (await fillTexts()) {
  bindTableColumns();
  showFindCount("");
  await reload();
}
