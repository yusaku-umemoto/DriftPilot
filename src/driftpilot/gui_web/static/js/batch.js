// 一括比較の画面（REQ-106。REQ-211 で組の一覧と結果を 1 つの表にした）。対になる Python の側は driftpilot/gui_web/batch_api.py。
//
// することは3つだけ: data-text・data-label の鍵の文を取って入れる・Python が返した状態（view）を描く・操作を Python に渡す。
// 押せるか・組を作るか・何を知らせるか・送ってよいかは Python が決める（ADR-0074 決定3）。受け取った文字は textContent だけで描く。
// 組を作り直すのは、文字の欄を確定したとき（change。Enter かフォーカスが外れたとき）と、
// 「組を作り直す」・入れ替えを押したときだけ。キーを押すたびには作らない（フォルダを読むので）。
// 直した組があるときは、Python が確かめ（confirm）を返す。dialog.js の confirm で聞き、「はい」のときだけ confirmed=true で呼び直す。
// 組を直す（REQ-212）: 表の先頭のチェック・機種の選択、「組の設定…」「全部の組の設定…」（panel）、「組を解除」、
// 片方だけのファイルのチェックと「選んだ 2 つを組にする」、hostname の候補。どれも Python に渡すだけで、組にするファイルのパスは渡さない。
// 片方だけのファイルは左右 2 列（変更前・変更後の表。REQ-233 / ADR-0171）。行を反対側の列の行へドラッグしても組にできる
// （js/pair_drag.js。描いたときの組の一覧の番号と、側ごとの一覧の番号だけを pair_dropped で渡す）。
// 比べるもの（form.source）は画面に出さない。フォルダの欄を確定したら folders、組のファイルの欄なら manifest にする（Python も同じ）。
// 表の行は view に入っていない。番号が変わったときだけ result() で取り、DocumentFragment でまとめて描く。
// 行は組の順。「差分がある組」のタブは、差分の無い行を隠す（tr.hidden）だけ。行の番号は組の順のまま Python に渡す。
// AI の場所（REQ-213）: 選んだ組の答え（view.answer のブロックを answer_view.js の renderAnswer で描く。コピーは answer_copy.js）と、
// AI の行（view.ai）。「AI で解析」「内容を確かめる…」・プロファイルとプロンプトの選択は Python に渡すだけで、送るかは Python が決める。
// 「内容を確かめる…」の確認（approval.check_only）はボタンが 1 つのダイアログ（dialog.js の view）で、閉じたら送らない答えを渡す。
// 行の複数選択（ADR-0176）: ⌘（Windows は Ctrl）・Shift を押しながらのクリックで行を複数選ぶ。「組を解除」は選んだ組の id の並びを渡す。
// 「組の設定…」「比較画面で開く」・ダブルクリックは、2 行以上を選んでいたら Python を呼ばずに知らせる。答えの欄は主の行（最後にクリックした行）。
// 見出しの「全部」のチェック: 組の表は見えている行を set_included_many で 1 回で渡し、候補の表は画面の中だけで付け外しする。
// 「設定…」は open_settings を呼ぶだけ（設定の窓で保存したら、Python から batch.changed が届く）。
// 「AI に案を聞く…」（REQ-219）: 押したら Python に渡すだけ。Python が返した確認（view.pair_ai_preview）を panel で開き、
// 送る全文を読むだけの欄に出す。答えは番号と「送る」かだけを渡す（文は渡さない。既定のフォーカスと Esc は「送らない」）。

import { call, on } from "./bridge.js";
import { confirm, notice, panel } from "./dialog.js";
import { view as viewOnly } from "./dialog.js";
import { choose } from "./dialog.js"; // 「HTML・CSV で出力…」の形を選ぶ（ADR-0156）
import { make, text } from "./dom.js";
import { renderAnswer } from "./answer_view.js";
import { copyIcon, copyText } from "./answer_copy.js";
import { bindTail, fitPaths, namePathCell, showTail } from "./path_tail.js";
import { bindColumnWidths } from "./column_width.js";
import { bindPairDrag } from "./pair_drag.js"; // 片方だけのファイルの行のドラッグで組にする（REQ-233 / ADR-0171）
import "./drop.js"; // フォルダとマニフェストのドロップを印の要素へ送る（REQ-205 / ADR-0147）
import { playSplash, revealCharacters } from "./character.js"; // キャラクターの絵（ADR-0174 / REQ-236）

const $ = (id) => document.getElementById(id);
let splashPlayed = false; // 起動時の絵を出したか（ADR-0174 決定2。1 回だけ）
// 要素に結び付かない文（進み具合の読み上げの名前・入力の枠の名前・列の境目の名前）
const EXTRA_TEXTS = [
  "batch.progress",
  "batch.source",
  "batch.col.resize",
  "batch.col.included",
  "batch.col.pair_check",
  "batch.col.suggest_check",
  "batch.col.row_platform",
  "batch.answer",
  "batch.export.title",
  "batch.export.body",
  // 行の複数選択と見出しの「全部」のチェック（ADR-0176 決定2・3）
  "batch.select.title",
  "batch.select.one",
  "batch.dialog.ok",
  "batch.col.included_all",
  "batch.col.suggest_all",
  // キャラクターがオンのときの「AI で解析」の文（ADR-0181 決定2）
  "batch.button.ai_send.character",
];
// 文字の欄と「選ぶ…」「開く…」のボタン（Python の欄の名前と、確定したときの比べるもの）
const PATHS = [
  { input: "before-dir", pick: "before-pick", field: "before_dir", source: "folders" },
  { input: "after-dir", pick: "after-pick", field: "after_dir", source: "folders" },
  { input: "manifest", pick: "manifest-pick", field: "manifest", source: "manifest" },
];
// 表の列（見出しの data-col と同じ並び）。width は初めの幅（無い列は残りの幅）。名前の列は名前のある組があるときだけ。
// AI の要点の列は残りの幅を埋める列で、min はその最低の幅（px。REQ-224 / ADR-0163 決定3）
const COLUMNS = [
  { key: "label", width: "120px", labelOnly: true },
  { key: "before", width: "22%", path: true },
  { key: "after", width: "22%", path: true },
  { key: "platform", width: "120px" },
  { key: "status", width: "80px" },
  { key: "structured", width: "72px", numeric: true },
  { key: "raw", width: "72px", numeric: true },
  { key: "unparsed", width: "90px" },
  { key: "ai_point", width: null, min: 360 },
];
const TABS = ["all", "diff"];
// 片方だけのファイルの 2 つの表の側（左が変更前、右が変更後。表の id は unpaired-<側>-table）
const UNPAIRED_SIDES = ["before", "after"];
const unpairedTable = (side) => $(`unpaired-${side}-table`);
const table = $("result-table");
const statusBar = $("status");

let texts = {}; // messages.py から取った文
let view = null; // いま描いている状態
let source = "folders"; // 比べるもの（form.source）
// 描いた状態の番号と、欄を入れ直した番号・描いた表の番号・出した確認と知らせの番号
// folds は片方だけのファイルと候補の折りたたみに入れた番号（Python の folds の revision。REQ-224 §5.2）
const shown = {
  revision: 0,
  fill: null,
  pairs: 0,
  result: 0,
  approval: null,
  pairAi: null,
  notice: null,
  folds: { unpaired: null, suggestions: null },
};
// 表で選んでいる行（ADR-0176 決定2）。selected は主の行（最後にクリックした行。答えの欄はこの行。組の順の番号）、
// selection は選んでいる行の番号の集まり（足した順）、anchor は最後に修飾キーなしで選んだ行（Shift の起点）。どれも無ければ -1・空
let selected = -1;
let selection = new Set();
let anchor = -1;
// 続けて押したクリックの 1 回目の前の選択 { selected, selection, anchor }（ダブルクリックを始めたときの選択。無ければ null）
let clickStart = null;
let tab = "all"; // 選んでいるタブ
let columns = []; // いま出している列
let columnsWithLabel = null; // 列を作ったときの名前の列の有無
let columnWidths = null; // bindColumnWidths が返した値（列を作り直す前に disconnect() を呼ぶ）
let tableRows = []; // 描いている表の行（組の id を持つ）
let suggestionKey = null; // 描いている候補の id の並び（同じなら描き直さず、チェックを残す）
let answerKey = null; // 描いている答えの場所（同じなら描き直さない）
let answerPlain = ""; // コピーのアイコンで入れる文（<red> を外した答え。Python が渡す）
const choiceKeys = {}; // AI プロファイルとプロンプトの選択肢の並び（同じなら作り直さない）

// data-text・data-label の鍵の文を messages.py から取って入れる（HTML には文を書かない）。取れなければ false。
// data-label の文は title と aria-label に入れる（アイコンのボタン）。
// data-title の文は title にだけ入れる（答えの吹き出しの顔。読み上げの名前にはしない。ADR-0181 決定1）。
async function fillTexts() {
  const elements = Array.from(document.querySelectorAll("[data-text]"));
  const labelled = Array.from(document.querySelectorAll("[data-label]"));
  const titled = Array.from(document.querySelectorAll("[data-title]"));
  const keys = Array.from(
    new Set([
      ...elements.map((el) => el.dataset.text),
      ...labelled.map((el) => el.dataset.label),
      ...titled.map((el) => el.dataset.title),
      ...EXTRA_TEXTS,
    ]),
  );
  const result = await call("messages", keys);
  if (!result.ok) {
    text(statusBar, result.message);
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
  for (const el of titled) {
    el.title = texts[el.dataset.title];
  }
  $("progress").setAttribute("aria-label", texts["batch.progress"]);
  $("inputs").setAttribute("aria-label", texts["batch.source"]);
  // チェックの列の見出しは文を出さず、読み上げの名前だけ
  $("col-included").setAttribute("aria-label", texts["batch.col.included"]);
  for (const side of UNPAIRED_SIDES) {
    $(`col-pair-check-${side}`).setAttribute("aria-label", texts["batch.col.pair_check"]);
  }
  $("col-suggest-check").setAttribute("aria-label", texts["batch.col.suggest_check"]);
  // 見出しの「全部」のチェックの読み上げの名前（ADR-0176 決定3）
  $("included-all").setAttribute("aria-label", texts["batch.col.included_all"]);
  $("suggest-all").setAttribute("aria-label", texts["batch.col.suggest_all"]);
  // 答えの吹き出しの読み上げの名前（決まった文）。コピーのボタンのアイコンは CSS で描く（文字は入れない）
  $("ai-answer-bubble").setAttribute("aria-label", texts["batch.answer"]);
  $("ai-copy").append(copyIcon());
  return true;
}

// 画面の欄の値（文字）。
function form() {
  return {
    source,
    before_dir: $("before-dir").value,
    after_dir: $("after-dir").value,
    manifest: $("manifest").value,
  };
}

// --- 描く ---

// 選択の欄の選択肢を描く。
function options(select, labels) {
  select.replaceChildren(...labels.map((label) => make("option", "", label)));
}

function fill(values) {
  source = values.source;
  for (const path of PATHS) {
    $(path.input).value = values[path.field];
    showTail($(path.input)); // 値を入れた後に後ろ（ファイル名の側）を見せる
  }
}

// --- AI の場所（REQ-213） ---

// 選んだ組の答え。blocks は Python が読み取ったブロック（renderAnswer が textContent だけで描く）、text は答えが無いときの理由の文、
// plain はコピーの文。どれも無ければ答えの場所を隠す。前と同じなら描き直さない。
function drawAnswer(answer) {
  const key = JSON.stringify(answer);
  if (key === answerKey) {
    return;
  }
  answerKey = key;
  const hasBlocks = answer.blocks.length > 0;
  const hasText = answer.text !== null;
  $("ai-answer").hidden = !hasBlocks && !hasText;
  renderAnswer($("ai-answer-view"), hasBlocks ? answer.blocks : null);
  $("ai-answer-view").hidden = !hasBlocks;
  text($("ai-answer-text"), answer.text);
  $("ai-answer-text").hidden = !hasText;
  answerPlain = answer.plain;
  $("ai-copy").hidden = answerPlain === "";
  $("ai-answer").scrollTop = 0;
}

// AI プロファイルとプロンプトの選択。option の value は id、文字は名前（どちらも Python が渡す。URL・キーは来ない）。
// 選択は使用中に合わせる（切り替えに失敗したときも、描き直すと使用中に戻る）。
function drawChoices(select, items, active, enabled) {
  const key = JSON.stringify(items);
  if (choiceKeys[select.id] !== key) {
    choiceKeys[select.id] = key;
    select.replaceChildren(
      ...items.map((item) => {
        const option = make("option", "", item.name);
        option.value = item.id;
        return option;
      }),
    );
  }
  select.value = active === null ? "" : active;
  if (active === null) {
    select.selectedIndex = -1;
  }
  select.disabled = !enabled || items.length === 0;
}

function renderAi(ai) {
  text($("ai-title"), ai.title);
  drawChoices($("ai-profile"), ai.profiles, ai.active_profile, ai.can_switch);
  drawChoices($("ai-prompt"), ai.prompts, ai.active_prompt, ai.can_switch);
  $("ai-check").disabled = !ai.can_check;
  $("ai-send").disabled = !ai.can_send;
  // 押せない理由（ADR-0175 決定3）。文は Python が決め、AI の行の下の注記とボタンの title に textContent で入れるだけ
  $("ai-reason").hidden = ai.reason === null;
  text($("ai-reason"), ai.reason);
  $("ai-check").title = ai.reason === null ? "" : ai.reason;
  $("ai-send").title = $("ai-check").title;
  $("ai-auto-send").hidden = ai.auto_send === null;
  text($("ai-auto-send"), ai.auto_send);
  $("ai-notes").hidden = ai.notes.length === 0;
  text($("ai-notes"), ai.notes.join("\n"));
}

function render(next) {
  if (next.revision < shown.revision) {
    return; // 後から届いた古い状態は捨てる
  }
  shown.revision = next.revision;
  view = next;
  const labels = next.labels;

  text($("run"), labels.run);
  // 注記はボタンの title
  $("handoff").title = labels.handoff_note;
  $("save-manifest").title = labels.save_note;
  $("save-results").title = labels.save_note;
  if (next.fill !== shown.fill) {
    shown.fill = next.fill;
    fill(next.form);
  }

  // 組のファイルを開いているときだけ、そのパスを出す
  $("manifest").hidden = next.folders;
  if (!next.folders) {
    showTail($("manifest"));
  }
  for (const path of PATHS) {
    $(path.input).disabled = !next.inputs_enabled;
    $(path.pick).disabled = !next.inputs_enabled;
  }
  $("swap").disabled = !next.inputs_enabled;
  $("rebuild").disabled = !next.can_rebuild;
  // 組を直す操作（実行中・確認の待ち中は押せない）。行の中のチェックと機種の選択も同じ
  for (const id of ["pair-settings", "all-settings", "unpair"]) {
    $(id).disabled = !next.can_edit;
  }
  for (const control of table.tBodies[0].querySelectorAll("input, select")) {
    control.disabled = !next.can_edit;
  }
  for (const side of UNPAIRED_SIDES) {
    for (const control of unpairedTable(side).tBodies[0].querySelectorAll("input")) {
      control.disabled = !next.inputs_enabled;
    }
  }
  $("pair-files").disabled = !next.can_pair;
  $("suggest").disabled = !next.can_suggest;
  $("ask-pair-ai").disabled = !next.can_ask_pair_ai;
  $("accept-suggestions").disabled = !next.inputs_enabled;
  $("dismiss-suggestions").disabled = !next.inputs_enabled;

  // 起動したときに見つけた新しい版の知らせ（REQ-252 §5）。文は文字として描く
  $("update-notice").hidden = next.update_notice === null;
  text($("update-notice-text"), next.update_notice);
  $("banner").hidden = next.banner === null;
  text($("banner-text"), next.banner);
  text($("tab-all"), next.pairs_titles.all);
  text($("tab-diff"), next.pairs_titles.diff);
  text($("unpaired-title"), next.pairs_titles.unpaired);
  // 左右 2 列の見出し（「変更前にしかない: N 件」の形。0 件でも出す。REQ-233）
  for (const side of UNPAIRED_SIDES) {
    text($(`unpaired-${side}-title`), next.unpaired_titles[side]);
  }
  // 片方だけのファイルと候補の折りたたみ。開け閉めの初めの値は Python が決め、番号が変わったときだけ入れる（人の開け閉めを残す）
  if (next.folds.unpaired.revision !== shown.folds.unpaired) {
    shown.folds.unpaired = next.folds.unpaired.revision;
    $("unpaired-fold").open = next.folds.unpaired.open;
  }
  if (next.folds.suggestions.revision !== shown.folds.suggestions) {
    shown.folds.suggestions = next.folds.suggestions.revision;
    $("suggestions").open = next.folds.suggestions.open;
  }
  // 進み具合は実行中だけ
  $("progress-row").hidden = !next.busy;
  $("cancel-note").hidden = !next.busy;
  $("progress").max = next.progress.max;
  $("progress").value = next.progress.value;
  text($("progress-text"), next.progress_text);
  // キャラクターの絵（ADR-0174 決定2）。実行中の絵は進み具合の行の中なので、行が出ている間だけ見える。
  // 起動時の絵は --batch で最初に開いたとき（view.splash が真）に 1 回だけ。view.character が偽なら hidden のまま
  revealCharacters(["character-batch-progress", "character-batch-progress-still"], next.character === true);
  if (next.character === true && next.splash === true && !splashPlayed) {
    splashPlayed = true;
    playSplash($("splash"));
  }
  // 答えの吹き出しの上の顔と名前（ADR-0181 決定1）。行は #ai-answer の中なので、答えの吹き出しが出ている間だけ見える。
  // 「AI で解析」の文はオンのときだけ「AI（Mirarin）で解析」に替える（決定2）。オフのときは隠したまま・文に触らない
  $("ai-answer-avatar").hidden = next.character !== true;
  if (next.character === true) {
    text($("ai-send"), texts["batch.button.ai_send.character"]);
  }
  text($("counts"), next.counts);
  // 単純比較の設定の要約と組ごとの設定の数（REQ-228 §7.2）。全部既定なら隠す。全文は title
  const rawLine = $("raw-settings-line");
  rawLine.hidden = next.raw_settings_line === null;
  text(rawLine, next.raw_settings_line);
  rawLine.title = next.raw_settings_line === null ? "" : next.raw_settings_line;
  drawAnswer(next.answer);
  renderAi(next.ai);

  $("run").disabled = !next.can_run;
  $("save-manifest").disabled = !next.can_save_manifest;
  $("save-results").disabled = !next.can_save_results;
  $("export").disabled = !next.can_export;
  $("cancel").disabled = !next.can_cancel;
  $("handoff").disabled = !next.can_handoff;
  text(statusBar, next.status);
  drawIncludedAll();

  if (next.pairs_revision !== shown.pairs || next.result_revision !== shown.result) {
    loadResult();
  }
  if (next.approval !== null && next.approval.id !== shown.approval) {
    shown.approval = next.approval.id;
    showApproval(next.approval);
  }
  if (next.pair_ai_preview !== null && next.pair_ai_preview.id !== shown.pairAi) {
    shown.pairAi = next.pair_ai_preview.id;
    showPairAi(next.pair_ai_preview);
  }
  if (next.notice !== null && next.notice.id !== shown.notice) {
    shown.notice = next.notice.id;
    notice(next.notice.title, next.notice.body, next.notice.ok);
  }
}

// --- パスの「…」（描いた後・列の幅を変えた後・窓の大きさが変わったとき。1 回にまとめる） ---

let fitPending = false;

function scheduleFit() {
  if (fitPending) {
    return;
  }
  fitPending = true;
  requestAnimationFrame(() => {
    fitPending = false;
    fitPaths(document.body);
  });
}

// --- 1 つの表 ---

// 列を作る（名前の列の有無が変わったときだけ）。<col> の初めの幅と、見出しの右端の境目（最後の AI の要点の列にも付ける）。
// 先頭のチェックの列（REQ-212 §4）は幅を決めた列で、境目を付けない。AI の要点の列は残りを埋める列（最低 360px。ADR-0163 決定3）。
// 作り直す前に、前の列の枠の見張りを止める（disconnect）。
function buildColumns(withLabel) {
  if (columnsWithLabel === withLabel) {
    return;
  }
  columnsWithLabel = withLabel;
  if (columnWidths !== null) {
    columnWidths.disconnect();
    columnWidths = null;
  }
  columns = COLUMNS.filter((column) => withLabel || !column.labelOnly);
  for (const old of table.tHead.querySelectorAll(".pilot-col-resize")) {
    old.remove();
  }
  table.querySelector('th[data-col="label"]').hidden = !withLabel;
  const colgroup = table.querySelector("colgroup");
  const checkCol = make("col", "pilot-col-check", null);
  const cols = columns.map((column) => {
    const col = document.createElement("col");
    if (column.width !== null) {
      col.style.width = column.width;
    }
    return col;
  });
  colgroup.replaceChildren(checkCol, ...cols);
  table.style.width = "";
  // 列ごとに <col> と対になる見出しのセル（幅はこちらで測る）と境目
  const entries = columns.map((column, index) => {
    const head = table.querySelector(`th[data-col="${column.key}"]`);
    const grip = make("span", "pilot-col-resize", null);
    grip.setAttribute("role", "separator");
    grip.setAttribute("tabindex", "0");
    grip.setAttribute("aria-orientation", "vertical");
    grip.setAttribute("aria-label", texts["batch.col.resize"]);
    grip.title = texts["batch.col.resize"];
    head.append(grip);
    return { col: cols[index], head, handle: grip };
  });
  const checkEntry = { col: checkCol, head: $("col-included"), handle: null };
  // 埋める列（AI の要点）の番号は [checkEntry, ...entries] の中の番号（先頭のチェックの列も数える。今は最後の列）
  const fillIndex = 1 + columns.findIndex((column) => column.key === "ai_point");
  columnWidths = bindColumnWidths(table, [checkEntry, ...entries], { onChange: scheduleFit, fill: fillIndex, fillMin: 360 });
}

// 行の名前（読み上げの名前に使う）。
function rowName(row) {
  return row.label || row.job_id;
}

// 表の先頭のチェック（外した組は実行しない・保存に書かない）。変えたら Python に渡すだけ。
function includedCell(row, editable) {
  const td = make("td", "pilot-check-cell", null);
  const check = make("input", "pilot-check", null);
  check.type = "checkbox";
  check.checked = row.included;
  check.disabled = !editable;
  check.setAttribute("aria-label", `${texts["batch.col.included"]}: ${rowName(row)}`);
  check.addEventListener("change", async () => handle(await call("set_included", row.job_id, check.checked)));
  check.addEventListener("dblclick", (event) => event.stopPropagation()); // チェックのダブルクリックで比較画面を開かない
  td.append(check);
  return td;
}

// 機種の列。種類が config の組は選択（先頭が自動。自動の組は先頭の文を行の文「自動: FortiGate」などにする）、ほかは文字。
function platformCell(row, editable, labels) {
  if (row.platform_index === null) {
    const td = make("td", "", row.platform);
    td.title = td.textContent;
    return td;
  }
  const td = make("td", "pilot-select-cell", null);
  const select = make("select", "pilot-input pilot-cell-select", null);
  options(select, labels.map((label, index) => (index === 0 && row.platform_index === 0 ? row.platform : label)));
  select.selectedIndex = row.platform_index;
  select.disabled = !editable;
  select.title = row.platform;
  select.setAttribute("aria-label", `${texts["batch.col.row_platform"]}: ${rowName(row)}`);
  select.addEventListener("change", async () => handle(await call("set_platform", row.job_id, select.selectedIndex)));
  select.addEventListener("dblclick", (event) => event.stopPropagation());
  td.append(select);
  return td;
}

// 表の行をまとめて描く。パスのセルは後ろを見せる場所、ほかは文字と title（全文）。
function drawTable(rows, state) {
  tableRows = rows;
  if (selected >= rows.length || Array.from(selection).some((index) => index >= rows.length)) {
    clearSelection();
  }
  const fragment = document.createDocumentFragment();
  rows.forEach((row, index) => {
    const tr = document.createElement("tr");
    tr.append(includedCell(row, state.can_edit));
    for (const column of columns) {
      if (column.key === "platform") {
        tr.append(platformCell(row, state.can_edit, state.labels.row_platforms));
        continue;
      }
      const td = make("td", column.numeric ? "pilot-num" : "", null);
      if (column.path) {
        td.append(namePathCell(row[column.key]));
      } else {
        text(td, row[column.key]);
        td.title = td.textContent; // 切れて見えないセルの全文は、指を置くと出る（文字のまま）
      }
      tr.append(td);
    }
    tr.dataset.hasDiff = String(row.has_diff);
    tr.dataset.included = String(row.included);
    tr.setAttribute("aria-selected", String(selection.has(index)));
    fragment.append(tr);
  });
  table.tBodies[0].replaceChildren(fragment);
  applyTab();
  scheduleFit();
}

// 片方だけのファイル（チェック・ファイル名）を左右 2 つの表に分けて描く（REQ-233）。items は同じ並びの側・番号・チェック・全文のパス。
// 行には側（data-side）とその側の一覧の番号（data-index）を入れる（ドラッグで組にするときに pair_drag.js が読む）。
// ファイル名は左に寄せ、切れているときは後ろを見せる（namePathCell の第 2 引数）。全文は title
function drawUnpaired(rows, items, editable) {
  const fragments = { before: document.createDocumentFragment(), after: document.createDocumentFragment() };
  rows.forEach((row, index) => {
    const item = items[index];
    const tr = document.createElement("tr");
    tr.dataset.side = item.side;
    tr.dataset.index = String(item.index);
    const cell = make("td", "pilot-check-cell", null);
    const check = make("input", "pilot-check", null);
    check.type = "checkbox";
    check.checked = item.checked;
    check.disabled = !editable;
    check.setAttribute("aria-label", `${texts["batch.col.pair_check"]}: ${row.side} ${row.name}`);
    check.addEventListener("change", async () => handle(await call("set_only_checked", item.side, item.index, check.checked)));
    cell.append(check);
    const name = make("td", "", null);
    name.append(namePathCell(row.name, true));
    name.title = item.path;
    tr.append(cell, name);
    fragments[item.side].append(tr);
  });
  for (const side of UNPAIRED_SIDES) {
    unpairedTable(side).tBodies[0].replaceChildren(fragments[side]);
  }
  scheduleFit();
}

// 組の候補（hostname の候補と AI の案。手がかりの文は Python が渡す。チェックの既定はオフ）。同じ候補なら描き直さない（チェックを残す）。
// どちらのときも見出しの「全部」のチェックの印は描き直す（ADR-0176 決定3）。
function drawSuggestions(suggestions) {
  const key = suggestions.map((item) => item.id).join("\n");
  $("suggestions").hidden = suggestions.length === 0;
  if (key === suggestionKey) {
    drawSuggestAll();
    return;
  }
  suggestionKey = key;
  const fragment = document.createDocumentFragment();
  for (const item of suggestions) {
    const tr = document.createElement("tr");
    const cell = make("td", "pilot-check-cell", null);
    const check = make("input", "pilot-check", null);
    check.type = "checkbox";
    check.checked = false;
    check.value = item.id;
    check.setAttribute("aria-label", `${texts["batch.col.suggest_check"]}: ${item.before} / ${item.after}`);
    check.addEventListener("change", drawSuggestAll); // 画面の中のチェックだけ（Python は呼ばない）
    cell.append(check);
    const before = make("td", "", null);
    before.append(namePathCell(item.before));
    before.title = item.before_path;
    const after = make("td", "", null);
    after.append(namePathCell(item.after));
    after.title = item.after_path;
    const hint = make("td", "", item.hint);
    hint.title = hint.textContent;
    tr.append(cell, before, after, hint);
    fragment.append(tr);
  }
  $("suggestion-table").tBodies[0].replaceChildren(fragment);
  drawSuggestAll();
  scheduleFit();
}

// --- 見出しの「全部」のチェック（ADR-0176 決定3） ---

// 印あり・途中の印（indeterminate）・印なしを、対象の数と印の付いた数で描く。対象が 0 なら押せない。
function drawAllCheck(box, total, on, enabled) {
  box.checked = total > 0 && on === total;
  box.indeterminate = on > 0 && on < total;
  box.disabled = total === 0 || !enabled;
}

// 今のタブで見えている表の行（組の順。Python が返した行）
function visibleRows() {
  return Array.from(table.tBodies[0].rows)
    .filter((tr) => !tr.hidden && tr.sectionRowIndex < tableRows.length)
    .map((tr) => tableRows[tr.sectionRowIndex]);
}

// 組の表の見出しのチェック。対象は今のタブで見えている行。組を直せない（view.can_edit が偽）ときは押せない
function drawIncludedAll() {
  const rows = visibleRows();
  drawAllCheck($("included-all"), rows.length, rows.filter((row) => row.included).length, view !== null && view.can_edit);
}

// 候補の表の見出しのチェック。対象は画面の中の候補のチェック
function suggestionChecks() {
  return Array.from($("suggestion-table").tBodies[0].querySelectorAll("input.pilot-check"));
}

function drawSuggestAll() {
  const checks = suggestionChecks();
  drawAllCheck($("suggest-all"), checks.length, checks.filter((check) => check.checked).length, true);
}

// --- 行の選択（ADR-0176 決定2） ---

// 選択を全部外す（Python には知らせない。呼ぶ側が決める）
function clearSelection() {
  selected = -1;
  selection = new Set();
  anchor = -1;
  clickStart = null; // 組やタブが変わった後に、前の選択へ戻さない
}

// 覚えた選択 saved に戻す（2 行以上を選んでいたダブルクリックを断るとき）。主の行が変わったら select_result を呼ぶ
async function restoreSelection(saved) {
  const before = selected;
  selected = saved.selected;
  selection = new Set(saved.selection);
  anchor = saved.anchor;
  paintSelection();
  if (selected !== before) {
    await handle(await call("select_result", selected));
  }
}

// 選んでいる行に aria-selected="true"（色は今の選択の CSS のまま）
function paintSelection() {
  for (const tr of table.tBodies[0].rows) {
    tr.setAttribute("aria-selected", String(selection.has(tr.sectionRowIndex)));
  }
}

// 選んでいる行の組の id を行の順に（「組を解除」に渡す）
function selectedJobIds() {
  return Array.from(selection)
    .filter((index) => index >= 0 && index < tableRows.length)
    .sort((a, b) => a - b)
    .map((index) => tableRows[index].job_id);
}

// 主の行の組の id（選んでいなければ空の文字。知らせは Python が出す）。
function selectedJobId() {
  return selected >= 0 && selected < tableRows.length ? tableRows[selected].job_id : "";
}

// 1 組だけの操作（「組の設定…」「比較画面で開く」・ダブルクリック）の前に見る。2 行以上を選んでいたら知らせて false（Python は呼ばない）
function onlyOneSelected() {
  if (selection.size > 1) {
    notice(texts["batch.select.title"], texts["batch.select.one"], texts["batch.dialog.ok"]);
    return false;
  }
  return true;
}

// 今のタブで見えている、番号 from から to までの行の番号（両端を含む。組の順）
function visibleBetween(from, to) {
  const [low, high] = from <= to ? [from, to] : [to, from];
  return Array.from(table.tBodies[0].rows)
    .filter((tr) => !tr.hidden && tr.sectionRowIndex >= low && tr.sectionRowIndex <= high)
    .map((tr) => tr.sectionRowIndex);
}

// 行のクリック。修飾キーなしはその行だけ、⌘（metaKey）か Ctrl（ctrlKey）はその行を足すか外す、
// Shift（shiftKey）は anchor からその行までの見えている行。主の行が変わったときだけ select_result を呼ぶ
async function clickRow(index, event) {
  const before = selected;
  const isShown = (row) => row >= 0 && row < table.tBodies[0].rows.length && !table.tBodies[0].rows[row].hidden;
  if (event.shiftKey && isShown(anchor)) {
    selection = new Set(visibleBetween(anchor, index));
    selected = index;
  } else if (event.metaKey || event.ctrlKey) {
    if (selection.has(index)) {
      selection.delete(index);
      if (selected === index) {
        const rest = Array.from(selection);
        selected = rest.length > 0 ? rest[rest.length - 1] : -1; // 残りの最後（最後に足した）行を主にする
      }
    } else {
      selection.add(index);
      selected = index;
    }
  } else {
    selection = new Set([index]);
    selected = index;
    anchor = index;
  }
  paintSelection();
  if (selected !== before) {
    await handle(await call("select_result", selected));
  }
}

// タブに合わせて行を隠す。選んでいる行のどれかが隠れたら選択を全部外す。
function applyTab() {
  let hidden = false;
  for (const tr of table.tBodies[0].rows) {
    tr.hidden = tab === "diff" && tr.dataset.hasDiff !== "true";
    if (tr.hidden && selection.has(tr.sectionRowIndex)) {
      hidden = true;
    }
  }
  if (hidden) {
    clearSelection();
    paintSelection();
    call("select_result", -1).then(handle);
  }
  drawIncludedAll();
}

function selectTab(name) {
  tab = name;
  for (const item of TABS) {
    $(`tab-${item}`).setAttribute("aria-selected", String(item === name));
  }
  applyTab();
  scheduleFit();
}

// 番号が変わったら result() を呼ぶ。呼んでいる途中なら、終わってからもう1回。
let resultLoading = false;
let resultAgain = false;

async function loadResult() {
  if (resultLoading) {
    resultAgain = true;
    return;
  }
  resultLoading = true;
  try {
    do {
      resultAgain = false;
      const answer = await call("result");
      if (!answer.ok) {
        text(statusBar, answer.message);
        break;
      }
      const result = answer.result;
      const pairsChanged = result.pairs_revision !== shown.pairs;
      // 名前の列の有無は、組の名前を直したときにも変わる
      buildColumns(answer.view.has_labels);
      if (pairsChanged) {
        // 組を作り直した・組にした・解除した。選択を消し、片方だけのファイルを描き直す
        shown.pairs = result.pairs_revision;
        clearSelection();
        drawUnpaired(result.unpaired, result.unpaired_items, answer.view.inputs_enabled);
      }
      if (pairsChanged || result.revision !== shown.result) {
        shown.result = result.revision;
        drawTable(result.table, answer.view);
      }
      drawSuggestions(result.suggestions);
      render(answer.view);
    } while (resultAgain);
  } finally {
    resultLoading = false;
  }
}

// --- 送信前の確認（ADR-0031） ---

function area(value, className) {
  const el = make("textarea", className, null);
  el.value = value;
  el.readOnly = true;
  el.spellcheck = false;
  return el;
}

// 全部の組をまとめて1回だけ出す。チェックの既定は全部オン。既定のフォーカスと Esc は「送らない」（dialog.js の panel）。
// 全文は画面に出さない（「内容をファイルに保存…」で書き出して見る）。答えを Python に渡すだけで、送るかは Python が決める。
// 「内容を確かめる…」（check_only）はチェックの列を出さず、ボタンが 1 つのダイアログ（dialog.js の view）で開く。
// 閉じたら（Esc も）送らない答え（[] と false）を渡す（Python も check のときは答えによらず送らない）。
async function showApproval(approval) {
  const checkOnly = approval.check_only;
  const parts = [area(approval.summary, "pilot-input pilot-mono pilot-nowrap")];
  if (approval.limits !== null) {
    parts.push(make("p", "pilot-lines", approval.limits));
  }
  parts.push(make("p", "", approval.rows_title));
  const approvalTable = make("table", "pilot-table", null);
  const head = document.createElement("tr");
  if (!checkOnly) {
    head.append(make("th", "", null));
  }
  for (const column of approval.columns) {
    const th = make("th", "", column);
    th.scope = "col";
    head.append(th);
  }
  approvalTable.createTHead().append(head);
  const body = approvalTable.createTBody();
  const checks = approval.rows.map((row) => {
    const check = make("input", "pilot-check", null);
    check.type = "checkbox";
    check.checked = true;
    check.setAttribute("aria-label", row.name);
    const tr = document.createElement("tr");
    if (!checkOnly) {
      const cell = document.createElement("td");
      cell.append(check);
      tr.append(cell);
    }
    tr.append(make("td", "", row.name));
    for (const value of [row.sent, row.masked, row.unsent]) {
      tr.append(make("td", "pilot-num", String(value)));
    }
    body.append(tr);
    return { check, jobId: row.job_id };
  });
  const wrap = make("div", "pilot-table-wrap", null);
  wrap.append(approvalTable);
  const save = make("button", "pilot-button pilot-button--secondary", approval.save);
  save.type = "button";
  // 保存のダイアログは Python が開く。確認は閉じない（知らせを上に重ねて出す）
  save.addEventListener("click", async () => {
    const result = await call("save_approval_payloads");
    if (!result.ok) {
      text(statusBar, result.message);
    } else if (result.notice) {
      await notice(result.notice.title, result.notice.body, result.notice.ok);
    }
  });
  const tools = make("div", "pilot-row", null);
  tools.append(save, make("span", "pilot-note pilot-grow", approval.save_note));
  parts.push(wrap, tools);
  if (checkOnly) {
    await viewOnly(approval.title, parts, approval.close);
    await handle(await call("answer_approval", [], false));
    return;
  }
  const yes = await panel(approval.title, parts, approval.send, approval.dont_send);
  const checked = yes ? checks.filter((item) => item.check.checked).map((item) => item.jobId) : [];
  await handle(await call("answer_approval", checked, yes));
}

// 「AI に案を聞く…」の送信前の確認（REQ-219 / ADR-0159 決定4）。送り先・送る全文（読むだけの欄）・送らないファイル・注意の文。
// 送らないファイルの名前は JSON の文字列の形で 1 行ずつ出す（改行などを含む名前も 1 行に見える）。
// 既定のフォーカスと Esc は「送らない」（dialog.js の panel）。答えは番号と「送る」かだけ。送る文は Python が持っている。
async function showPairAi(preview) {
  // 全文の見出しは、送る直前に答えの書き方の決まり（固定の文）を足すことの知らせ（ADR-0150 決定2）
  const parts = [
    make("p", "pilot-lines", preview.destination),
    make("p", "", preview.prompt_title),
    area(preview.prompt, "pilot-input pilot-mono pilot-nowrap"),
  ];
  if (preview.held_back.length > 0) {
    parts.push(make("p", "", preview.held_back_title));
    parts.push(area(preview.held_back.map((name) => JSON.stringify(name)).join("\n"), "pilot-input pilot-mono pilot-nowrap"));
  }
  parts.push(make("p", "pilot-note", preview.note));
  const yes = await panel(preview.title, parts, preview.send, preview.dont_send);
  await handle(await call("answer_pair_ai", preview.id, yes));
}

// --- 操作を Python に渡す ---

// 返ってきたものを出す。知らせは出してから描く（wx と同じ順）。
async function handle(result) {
  if (!result.ok) {
    text(statusBar, result.message);
    return;
  }
  if (result.notice) {
    await notice(result.notice.title, result.notice.body, result.notice.ok);
  }
  render(result.view);
}

// 欄の値を添えて呼ぶ。直した組があって Python が確かめ（confirm）を返したら、欄を元に戻してから聞き（既定は「いいえ」）、
// 「はい」のときだけ、打った欄の値で confirmed=true を付けて呼び直す（rebuild・pick・swap。confirmed は最後の引数）。
async function act(name, ...args) {
  if (view === null) {
    return; // まだ最初の状態が届いていない
  }
  const typed = form();
  const result = await call(name, typed, ...args);
  if (result.ok && result.confirm) {
    render(result.view);
    const ask = result.confirm;
    if (await confirm(ask.title, ask.body, ask.yes, ask.no)) {
      source = typed.source;
      await handle(await call(name, typed, ...args, true));
    }
    return;
  }
  await handle(result);
}

// --- 組の設定（REQ-212 §3。panel で開く。欄の文・選択肢・今の値は Python が返す） ---

function textInput(id, value) {
  const input = make("input", "pilot-input", null);
  input.type = "text";
  input.id = id;
  input.value = value;
  input.autocomplete = "off";
  input.spellcheck = false;
  return input;
}

function choiceSelect(id, labels, index) {
  const select = make("select", "pilot-input", null);
  select.id = id;
  options(select, labels);
  select.selectedIndex = index === null ? -1 : index;
  return select;
}

// 欄を 1 つ作る。戻り値は { element: 置く要素, focus: 最初に触る要素, read: 値を読む関数（{ 鍵: 値 }）, kind?: 種類の選択 }。
function settingsField(field, settings, controls) {
  const id = `setting-${field.key}`;
  if (field.type === "text") {
    const input = textInput(id, field.value);
    controls[field.key] = input;
    return { element: input, focus: input, read: () => ({ [field.key]: input.value }) };
  }
  if (field.type === "select") {
    const select = choiceSelect(id, field.choices, field.value);
    controls[field.key] = select;
    return { element: select, focus: select, read: () => ({ [field.key]: select.selectedIndex }) };
  }
  if (field.type === "platform") {
    // 種類が config なら機種の選択、それ以外は文字（切り替えは種類の欄の change）
    const wrap = make("span", "pilot-settings-control", null);
    const select = choiceSelect(id, field.choices, field.index);
    const input = textInput(`${id}-text`, field.text);
    input.setAttribute("aria-label", field.label);
    wrap.append(select, input);
    controls.platformSelect = select;
    controls.platformText = input;
    const read = () => {
      if (!select.hidden) {
        return { platform: select.selectedIndex >= 0 ? select.selectedIndex : field.text };
      }
      return { platform: input.value };
    };
    return { element: wrap, focus: select, read };
  }
  if (field.type === "selection") {
    // 区間: 自動・コマンド名・番号の選択と文字（両方は書けない）。自動のときは文字を押せない
    const wrap = make("span", "pilot-settings-control", null);
    const select = choiceSelect(id, field.choices, field.index);
    const input = textInput(`${id}-text`, field.text);
    input.setAttribute("aria-label", field.label);
    input.disabled = select.selectedIndex === 0;
    select.addEventListener("change", () => {
      input.disabled = select.selectedIndex === 0;
    });
    wrap.append(select, input);
    const read = () => ({ [`${field.key}_selection`]: select.selectedIndex, [`${field.key}_value`]: input.value });
    return { element: wrap, focus: select, read };
  }
  // 正規化の規則: パスの欄と「選ぶ…」（ダイアログは Python が開く）
  const wrap = make("span", "pilot-settings-control", null);
  const input = textInput(id, field.value);
  const pick = make("button", "pilot-button pilot-button--secondary", field.pick);
  pick.type = "button";
  pick.addEventListener("click", async () => {
    const result = await call("pick_rules");
    if (result.ok && result.path) {
      input.value = result.path;
    }
  });
  wrap.append(input, pick);
  return { element: wrap, focus: input, read: () => ({ [field.key]: input.value }) };
}

// 種類に合わせて、機種を選択か文字にし、show のコマンドを押せるかを決める。
function applyKind(settings, controls) {
  const index = controls.kind.selectedIndex;
  const kind = index > 0 ? settings.kinds[index - 1] : settings.default_kind;
  const config = kind === "config";
  controls.platformSelect.hidden = !config;
  controls.platformText.hidden = config;
  controls.show_command.disabled = config;
}

async function openSettings(settings) {
  const parts = [];
  if (settings.readonly.length > 0) {
    const known = make("div", "pilot-settings-grid", null);
    for (const item of settings.readonly) {
      const value = make("span", "pilot-mono pilot-settings-value", item.value);
      value.title = item.value;
      known.append(make("span", "", item.label), value);
    }
    parts.push(known);
  }
  // 単純比較の設定（REQ-228 §7.2）。読むだけの 1 行と注記（この窓では直さない）
  if (settings.raw_line) {
    parts.push(make("p", "pilot-settings-raw", settings.raw_line), make("p", "pilot-note", settings.raw_note));
  }
  const grid = make("div", "pilot-settings-grid", null);
  const controls = {};
  const readers = [];
  let focus = null;
  for (const field of settings.fields) {
    const built = settingsField(field, settings, controls);
    const label = make("label", "", field.label);
    label.htmlFor = `setting-${field.key}`;
    grid.append(label, built.element);
    if (field.note) {
      grid.append(make("span", "", null), make("span", "pilot-note", field.note));
    }
    readers.push(built.read);
    focus = focus || built.focus;
  }
  parts.push(grid, make("p", "pilot-note", settings.note));
  applyKind(settings, controls);
  controls.kind.addEventListener("change", () => applyKind(settings, controls));
  const yes = await panel(settings.title, parts, settings.apply, settings.cancel, focus);
  if (!yes) {
    return;
  }
  const values = Object.assign({}, ...readers.map((read) => read()));
  const result =
    settings.job_id === null ? await call("edit_defaults", values) : await call("edit_pair", settings.job_id, values);
  await handle(result);
  if (result.ok && result.reopen) {
    await openSettings(result.reopen); // 断られた。打った値を入れて開き直す
  }
}

// 「組の設定…」（job_id は主の行の組。選んでいなければ空の文字で、Python が知らせる。2 行以上なら呼ぶ前に onlyOneSelected が断る）と
// 「全部の組の設定…」（null）。
async function showSettings(jobId) {
  const result = await call("pair_settings", jobId);
  await handle(result);
  if (result.ok && result.settings) {
    await openSettings(result.settings);
  }
}

// 「選んだ候補を組にする」。チェックした候補の id だけを渡す（パスは渡さない）。
async function acceptSuggestions() {
  const ids = Array.from($("suggestion-table").querySelectorAll("tbody input:checked")).map((check) => check.value);
  await handle(await call("accept_suggestions", ids));
}

// Python からの知らせ（"batch.changed"）で state() を呼び直す。呼んでいる途中なら、終わってからもう1回だけ。
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

function bind(id, handler) {
  $(id).addEventListener("click", handler);
}

for (const item of TABS) {
  bind(`tab-${item}`, () => selectTab(item));
}
for (const path of PATHS) {
  bindTail($(path.input));
  // 確定したとき（Enter・フォーカスが外れたとき）だけ、比べるものをその欄にそろえて組を作り直す。打っている途中（input）では呼ばない
  $(path.input).addEventListener("change", () => {
    source = path.source;
    act("rebuild");
  });
  bind(path.pick, () => act("pick", path.field));
}
bind("rebuild", () => act("rebuild"));
bind("swap", () => act("swap"));
bind("pair-settings", () => {
  if (onlyOneSelected()) {
    showSettings(selectedJobId());
  }
});
bind("all-settings", () => showSettings(null));
// 選んだ組を全部まとめて解除する（id の並び。全部か無しかは Python が決める。ADR-0176 決定2）
bind("unpair", async () => handle(await call("unpair", selectedJobIds())));
// 見出しの「全部」のチェック（ADR-0176 決定3）。印ありなら全部外し、それ以外は全部付ける。
// 組の表は見えている行の id を Python に 1 回で渡し、返ったら描き直す（何も変わらなかったときに見た目を戻す）
$("included-all").addEventListener("change", async () => {
  const rows = visibleRows();
  const on = !(rows.length > 0 && rows.every((row) => row.included));
  await handle(await call("set_included_many", rows.map((row) => row.job_id), on));
  drawIncludedAll();
});
// 候補の表は画面の中のチェックだけを付け外しする（Python は呼ばない）
$("suggest-all").addEventListener("change", () => {
  const checks = suggestionChecks();
  const on = !(checks.length > 0 && checks.every((check) => check.checked));
  for (const check of checks) {
    check.checked = on;
  }
  drawSuggestAll();
});
bind("pair-files", async () => handle(await call("pair_files")));
bind("suggest", async () => handle(await call("suggest_hostnames")));
bind("ask-pair-ai", async () => handle(await call("ask_pair_ai")));
bind("accept-suggestions", () => acceptSuggestions());
bind("dismiss-suggestions", async () => handle(await call("dismiss_suggestions")));
// 片方だけのファイルと候補の折りたたみを開け閉めしたら（人の操作でも Python の folds でも）、パスの「…」を測り直す。
// 閉じている間に窓の幅が変わると、fitPaths は隠れたセルの印（data-clipped）を外すので、開き直したときに付け直す（REQ-224）
for (const id of ["unpaired-fold", "suggestions"]) {
  $(id).addEventListener("toggle", scheduleFit);
}
// 片方だけのファイルの行を反対側の列の行へドラッグして組にする（REQ-233 / ADR-0171 決定3〜5）。登録は 1 回だけ。
// 渡すのは描いたときの組の一覧の番号（shown.pairs）と側ごとの一覧の番号だけ。組にするか・パスは Python が決める
bindPairDrag(
  { before: unpairedTable("before").parentElement, after: unpairedTable("after").parentElement },
  {
    enabled: () => view !== null && view.inputs_enabled && view.can_edit,
    onPair: async (b, a) => handle(await call("pair_dropped", shown.pairs, b, a)),
  },
);
// 行のクリック（⌘／Ctrl・Shift で複数選ぶ。ADR-0176 決定2）。続けて押したクリックの 1 回目（event.detail が 1 以下）の前に、
// 選択を clickStart に覚える（ダブルクリックは click・click・dblclick の順に届き、先の click が選択を 1 行に減らすため）
table.tBodies[0].addEventListener("click", async (event) => {
  const tr = event.target.closest("tr");
  if (tr) {
    if (event.detail <= 1) {
      clickStart = { selected, selection: new Set(selection), anchor };
    }
    await clickRow(tr.sectionRowIndex, event);
  }
});
// Shift を押しながらの押し下げは、窓の文字が範囲で選ばれないよう既定の動きを止める（行の中のチェックと選択は除く）
table.tBodies[0].addEventListener("mousedown", (event) => {
  if (event.shiftKey && event.target.closest("input, select") === null) {
    event.preventDefault();
  }
});
// 行のダブルクリックは「比較画面で開く」と同じ（開けるかと知らせは Python が決める）。
// ダブルクリックを始めたとき（clickStart）か今、2 行以上を選んでいたら呼ばない。始めたときに 2 行以上なら、
// 先の click で減った選択を元に戻してから知らせる（何もしない。ADR-0176 決定2）
table.tBodies[0].addEventListener("dblclick", async (event) => {
  const tr = event.target.closest("tr");
  if (!tr) {
    return;
  }
  const start = clickStart;
  if (start !== null && start.selection.size > 1) {
    const restored = restoreSelection(start); // 選択は呼んだ時点で戻る（Python への select_result は後から）
    onlyOneSelected();
    await restored;
    return;
  }
  if (onlyOneSelected()) {
    act("handoff", selected);
  }
});
bind("run", () => act("run"));
bind("cancel", async () => handle(await call("cancel")));
bind("save-manifest", () => act("save_manifest"));
bind("save-results", () => act("save_results"));
// 「HTML・CSV で出力…」（ADR-0156）。本文は伏せる決まりの案内（決定9）、選択肢は Python が渡す。「やめる」でなければ形を渡す
bind("export", async () => {
  if (view === null) {
    return;
  }
  const kind = await choose(texts["batch.export.title"], texts["batch.export.body"], view.labels.export_choices, "html", "cancel");
  if (kind !== "cancel") {
    await act("export_result", kind);
  }
});
bind("handoff", () => {
  if (onlyOneSelected()) {
    act("handoff", selected);
  }
});
// 「設定…」（ADR-0176 決定4）。設定の窓は WebSession が開く（開いていれば前に出す）
bind("open-settings", async () => handle(await call("open_settings")));
// 新しい版の知らせ（REQ-252 §5）。「更新…」は設定の窓を「更新」のタブで開き、「閉じる」はその起動の間は出さない
bind("update-open", async () => handle(await call("open_settings", "update")));
bind("update-dismiss", async () => handle(await call("dismiss_update_notice")));
// AI の場所（REQ-213）。押せるか・送るかは Python が決める
bind("ai-check", () => act("check_llm"));
bind("ai-send", () => act("send_llm"));
$("ai-profile").addEventListener("change", async () => handle(await call("use_ai_profile", $("ai-profile").value)));
$("ai-prompt").addEventListener("change", async () => handle(await call("use_prompt", $("ai-prompt").value)));
bind("ai-copy", () => {
  if (answerPlain !== "") {
    copyText(answerPlain, $("ai-copy"));
  }
});
window.addEventListener("resize", scheduleFit);
on("batch.changed", () => reload());

if (await fillTexts()) {
  buildColumns(false);
  await reload();
}
