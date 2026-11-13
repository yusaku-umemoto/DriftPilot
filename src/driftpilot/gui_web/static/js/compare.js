// 比較の画面（REQ-105。配置は REQ-206 / ADR-0148）。対になる Python の側は driftpilot/gui_web/compare_api.py。
//
// することは3つだけ: data-text の鍵の文を取って入れる・Python が返した状態（view）を描く・操作を Python に渡す。
// 押せるか・どこへフォーカスを移すか・何を知らせるかは Python が決める（ADR-0074 決定3）。受け取った文字は textContent だけで描く。
// 入力の本文（config）は Python にだけある。貼り付けの文字は Python に渡したら残さない。
// 結果の行は view に入っていない。view.result_revision が変わったときだけ result() で取り、DocumentFragment でまとめて描く。
// まとめて比べたとき（REQ-122）は view.multi の一覧の行を左に描き、行を選ぶと select_pair を呼ぶ（選んだ組の結果は result() で取る）。
// 「全部」のタブ（ADR-0152 決定7）はまとめて比べたときだけ出し、行は view.multi.all_revision が変わったときだけ all_rows() で取る。
// 開くタブ（決定8）は Python が決め、view.multi.tab_revision が変わったときだけ移す（ここでは判断しない）。
// AI への送信の確認は、まとめて比べたときも全部の組をまとめた1件の確認（ai_view.js の showPreview。ADR-0152 決定3）。
// 画面だけで決めるのは、設定の欄の開閉・左右の面の幅（保存しない）・1つのボタンの名前（Python が渡す材料と、欄の文字の有無だけで決める）。
// AI の面（右の面）を描くのは js/ai_view.js（AI の窓と同じ部品。ADR-0160 決定2）。AI の面を別の窓で出している間（view.ai_detached）は
// 右の面と境目を隠して左を全幅にし、結果のタブの行の右端に「↗」（別の窓を前に出す）を出す。

import { call, on } from "./bridge.js";
import { notice, panel } from "./dialog.js";
import { confirm } from "./dialog.js"; // 「機器ごとに分けて…」の書き出す前の注意（REQ-267）
import { choose } from "./dialog.js"; // 「HTML・CSV で出力…」の形を選ぶ（ADR-0156）
import { make, text } from "./dom.js";
import { createAiView } from "./ai_view.js"; // AI の面（REQ-220 §5.1）
import { bindTail, showTail } from "./path_tail.js"; // ファイルの名前の欄は後ろを見せる（REQ-211 §4）
import { bindColumnWidths, makeGrip } from "./column_width.js"; // 5 つの表の列の幅の境目（REQ-224 / ADR-0163）
import { paintMarks } from "./marks.js"; // 変更の行で違う文字の印（REQ-226 / ADR-0165）
import { fillRawOptions, readRawOptions } from "./raw_options.js"; // 単純比較の設定の欄（REQ-227 / ADR-0166。設定の窓と同じ部品）
import "./drop.js"; // ファイルのドロップを印の要素へ送る（REQ-205 / ADR-0147）
import { playSplash, replayOnce, revealCharacters } from "./character.js"; // キャラクターの絵（ADR-0174 / REQ-236）

const $ = (id) => document.getElementById(id);
const SIDES = ["before", "after"];
const TABS = ["all", "structured", "raw", "unparsed"];
// 要素に結び付かない文（ダイアログに後から出すもの・表の列の境目の名前）
const DIALOG_TEXTS = [
  "compare.paste.prompt",
  "compare.dialog.ok",
  "compare.dialog.cancel",
  "compare.export.title",
  "compare.export.body",
  "compare.col.resize",
  "compare.multi.paired_by_hand", // 手で組にした組の印（REQ-269）
];
// 列の幅の境目を付ける 5 つの表（REQ-224 / ADR-0163）。fill は残りを埋める列の番号（無ければ最後の列）、fillMin はその最低の幅（px）。
// multi-table の fill は先頭の「AI に送る」のチェックの列を出しているときの番号（出していないときは 1 つ前。REQ-265）
const COLUMN_TABLES = [
  { id: "all-table", fillMin: 160 },
  { id: "structured-table", fillMin: 160 },
  { id: "raw-table", fillMin: 160 },
  { id: "unparsed-table", fillMin: 160 },
  { id: "multi-table", fill: 1, fillMin: 120 },
];
// AI の面から呼ぶメソッドのうち、form（比較の設定の欄の値）を足すもの。AI の窓では Python が持つ form を使う
const AI_FORM_METHODS = new Set([
  "send_llm",
  "ask_follow_up",
  "answer_preview",
  "set_full_text",
  "use_ai_profile",
  "use_prompt_profile",
]);
// 左右の面の境目（ADR-0148 決定2）。両側の最小の幅と、矢印のキーで動かす幅
const PANE_MIN = 220;
const SPLIT_STEP = 20;
// コマンドの組の一覧と比較の表の境目（REQ-224 / ADR-0163）。一覧の最小の幅（表の側の最小は PANE_MIN）
const MULTI_MIN = 160;
const statusMain = $("status-main");

let texts = {}; // messages.py から取った文
let view = null; // いま描いている状態
// 描いた状態の番号と、欄を入れ直した番号・フォーカスを移した番号・描いた結果の番号・出した知らせの番号
const shown = {
  revision: 0,
  fill: null,
  focus: 0,
  result: 0,
  rows: null, // 描いた表の中身の番号（Python の result の rows_revision）
  notice: null,
  multi: null,
  send: null, // 描いた一覧に「AI に送る」の列があったか（REQ-265）
  all: null, // 描いた「全部」のタブの行の番号
  tab: null, // 移したタブの番号（Python の tab_revision）
  rawOptions: null, // 単純比較の設定の欄を入れ直した番号（Python の raw_options.fill）
};
let rawRows = []; // 単純比較の行（下の2つの欄に全文を出すため）
let rawSelected = -1;
// 単純比較の行の要素を raw_rows の番号で引く配列（ADR-0155 決定4。折りたたみの行が入るので sectionRowIndex に頼らない）
let rawEls = [];
// 単純比較の変更の行の印を raw_rows の番号で引く Map（{before, after}。下の2つの欄にも使う。REQ-226 §4.3）
let rawMarks = new Map();
// 単純比較の設定で脇に置いた行（無視した行・同じとみなした行）の raw_rows の番号と、印の文（x は「無視」、= は「同じ」。REQ-227 §11.4）
let rawSetAside = new Set();
let rawMarkLabels = {};
let rawMoveLabels = new Map(); // 移動の行の印の文（raw_rows の番号 → 文。REQ-228）
// 構造化比較の行の値（カテゴリのチップで絞るとき、行の番号で category を比べる。ADR-0155 決定1）と、
// 選んでいるカテゴリ（null は「すべて」）・描いたチップ（ボタンと値）。絞り込みは保存しない
let structuredRows = [];
let structuredCategory = null;
let chips = [];
// キャラクターの絵（ADR-0174 決定2）。差分なしの絵（構造化と単純比較のタブ）の id、最後に描いた結果の場面、起動時の絵を出したか
const NO_DIFF_IDS = ["character-no-diff", "character-no-diff-still", "character-no-diff-raw", "character-no-diff-raw-still"];
const scene = { noDiff: false, neverCompared: true };
let splashPlayed = false;

// data-text・data-placeholder・data-label の鍵の文を messages.py から取って入れる（HTML には文を書かない）。取れなければ false。
// data-label の文は title と aria-label に入れる（アイコンのボタン・境目・質問の欄）。
// data-title の文は title にだけ入れる（答えの吹き出しの顔。読み上げの名前にはしない。ADR-0181 決定1）。
// <html lang> に入れる言語の鍵（ADR-0190。"en"・"ja"）
const PAGE_LANG = "page.lang";

async function fillTexts() {
  const elements = Array.from(document.querySelectorAll("[data-text]"));
  const hinted = Array.from(document.querySelectorAll("[data-placeholder]"));
  const labelled = Array.from(document.querySelectorAll("[data-label]"));
  const titled = Array.from(document.querySelectorAll("[data-title]"));
  const keys = Array.from(
    new Set([
      ...elements.map((el) => el.dataset.text),
      ...hinted.map((el) => el.dataset.placeholder),
      ...labelled.map((el) => el.dataset.label),
      ...titled.map((el) => el.dataset.title),
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
  for (const el of hinted) {
    el.placeholder = texts[el.dataset.placeholder];
  }
  for (const el of labelled) {
    el.title = texts[el.dataset.label];
    el.setAttribute("aria-label", texts[el.dataset.label]);
  }
  for (const el of titled) {
    el.title = texts[el.dataset.title];
  }
  return true;
}

// 画面の欄の値（文字と選択の番号）。
function form() {
  const mode = document.querySelector('input[name="mode"]:checked');
  return {
    mode: mode ? mode.value : view.form.mode,
    kind: $("kind").value,
    config_platform: $("config-platform").selectedIndex,
    platform: $("platform").value,
    command: $("command").value,
    before_segment: $("before-segment").selectedIndex,
    after_segment: $("after-segment").selectedIndex,
    // 単純比較の設定の欄（REQ-227 §11.3。式の正しさは Python が確かめる）
    raw_options: readRawOptions(),
  };
}

// --- 描く ---

// 選択の欄の選択肢を描く（並びが変わったときだけ作り直す）。作り直したら true。choices は {value, label} か文字。
function renderChoices(select, choices) {
  const labels = choices.map((choice) => (typeof choice === "string" ? choice : choice.label));
  if (Array.from(select.options).map((option) => option.text).join("\n") === labels.join("\n")) {
    return false;
  }
  select.replaceChildren(
    ...choices.map((choice) => {
      const option = make("option", "", typeof choice === "string" ? choice : choice.label);
      option.value = typeof choice === "string" ? choice : choice.value;
      return option;
    }),
  );
  return true;
}

// 比較方式のラジオ（最初の1回だけ作る）。
function renderModes(modes) {
  const group = $("mode");
  if (group.querySelector("input")) {
    return;
  }
  for (const mode of modes) {
    const input = make("input", "pilot-check", null);
    input.type = "radio";
    input.name = "mode";
    input.value = mode.value;
    input.addEventListener("change", () => act("form_changed"));
    const label = make("label", "pilot-radio", null);
    label.append(input, make("span", "", mode.label));
    group.append(label);
  }
}

function fill(values) {
  for (const input of document.querySelectorAll('input[name="mode"]')) {
    input.checked = input.value === values.mode;
  }
  $("kind").value = values.kind;
  $("config-platform").selectedIndex = values.config_platform;
  $("platform").value = values.platform;
  $("command").value = values.command;
  $("before-segment").selectedIndex = values.before_segment;
  $("after-segment").selectedIndex = values.after_segment;
}

// 今の比較の設定の欄を開く・閉じる（「変更」。ADR-0148 決定1）。
function setSettingsOpen(open) {
  $("settings-panel").hidden = !open;
  $("settings-toggle").setAttribute("aria-expanded", String(open));
}

// Python が決めた欄へフォーカスを移す（選択待ちの区間の欄、種類を判定できないときの機種の欄。ADR-0022 決定5）。
// 先が閉じた設定の欄の中なら、先に開く（ADR-0148 決定1）。
function moveFocus(name) {
  const target = {
    before_segment: $("before-segment"),
    after_segment: $("after-segment"),
    platform: view.platform_is_choice ? $("config-platform") : $("platform"),
  }[name];
  if (target) {
    if ($("settings-panel").hidden && $("settings-panel").contains(target)) {
      setSettingsOpen(true);
    }
    target.focus();
  }
}

function render(next) {
  if (next.revision < shown.revision) {
    return; // 後から届いた古い状態は捨てる
  }
  shown.revision = next.revision;
  view = next;
  const busy = next.busy;

  renderModes(next.labels.modes);
  renderChoices($("kind"), next.labels.kinds);
  renderChoices($("config-platform"), next.labels.config_platforms);
  text($("save-note"), next.labels.save_note);

  for (const side of SIDES) {
    if ($(`${side}-name`).value !== next.inputs[side].name) {
      $(`${side}-name`).value = next.inputs[side].name;
      showTail($(`${side}-name`)); // 値を入れた後に後ろ（ファイル名の側）を見せる（REQ-211 §4）
    }
    text($(`${side}-summary`), next.inputs[side].summary);
    $(`${side}-browse`).disabled = busy;
    $(`${side}-paste`).disabled = busy;
    const select = $(`${side}-segment`);
    // 選択肢を作り直したら、選んでいる位置は Python の値に合わせる
    if (renderChoices(select, next.segments[side])) {
      select.selectedIndex = next.form[`${side}_segment`];
    }
    select.disabled = busy;
  }
  $("swap").disabled = busy;
  if (!next.can_swap) {
    $("swap").disabled = true; // 保存した結果の表示中も押せない（REQ-120。押せるかは Python が決める）
  }
  // 上の3行目: 比較の設定の要約（Python が作る文）と、区間の選択（どちらかの側に区間が複数あるときだけ）
  text($("settings-summary"), next.settings_summary);
  $("segment-line").hidden = !next.segments_shown;
  text($("segment-title"), next.segments.title);
  if (next.fill !== shown.fill) {
    shown.fill = next.fill;
    fill(next.form);
  }

  for (const input of document.querySelectorAll('input[name="mode"]')) {
    input.disabled = busy;
  }
  $("kind").disabled = busy;
  // config では機種を系統の選択で選び、show のコマンドは使わない。show・自動では機種を手入力する
  $("config-platform").hidden = !next.platform_is_choice;
  $("platform").hidden = next.platform_is_choice;
  $("platform-label").htmlFor = next.platform_is_choice ? "config-platform" : "platform";
  $("config-platform").disabled = busy;
  $("platform").disabled = busy;
  $("command").disabled = !next.command_enabled;

  // 起動したときに見つけた新しい版の知らせ（REQ-252 §5）。文は文字として描く
  $("update-notice").hidden = next.update_notice === null;
  text($("update-notice-text"), next.update_notice);
  // 自動送信がオンの間は常に出す（ADR-0080 決定2）
  $("auto-send").hidden = next.auto_send === null;
  text($("auto-send-text"), next.auto_send);
  // 保存した結果を開いて表示している間の帯（REQ-120）。文と注記は文字として描く
  $("opened").hidden = next.opened === null;
  text($("opened-text"), next.opened === null ? "" : next.opened.text);
  text($("opened-note"), next.opened === null ? "" : next.opened.note);
  $("banner").hidden = next.banner === null;
  text($("banner-text"), next.banner);
  // 複数の機器が入ったログの知らせとボタン（REQ-267）。押せないときは aria-disabled と title（理由）で表す
  $("devices").hidden = next.devices === null;
  text($("devices-text"), next.devices === null ? "" : next.devices.text);
  $("split-devices").setAttribute("aria-disabled", next.devices !== null && next.devices.enabled ? "false" : "true");
  $("split-devices").title = next.devices === null ? "" : next.devices.reason || "";
  renderRawOptions(next);

  $("run").disabled = !next.can_run;
  $("save").disabled = !next.can_save;
  $("open-result").disabled = !next.can_open;
  $("export").disabled = !next.can_export;
  $("cancel").disabled = !next.can_cancel;
  $("prev-diff").disabled = !next.can_jump;
  $("next-diff").disabled = !next.can_jump;
  // 差分のみ表示は、差分が無いときは押せない（can_jump と同じ条件。ADR-0155 決定2）。チェックの状態は変えない
  $("raw-diff-only").disabled = !next.can_jump;
  text(statusMain, next.status.main);
  text($("status-side"), next.status.side);
  // 比較中は結果の面の真ん中にスピナーと今の段階の文を重ねる（REQ-205 / ADR-0147 決定3）。クリックは受けない。
  // AI に送る間は重ねず、AI の面の答えの欄の下に出す（ai_view.js の #ai-busy。ADR-0164 決定5）。場所は Python が決める
  $("busy").hidden = !next.busy_result;
  text($("busy-text"), next.busy_result ? next.status.main : "");
  renderCharacters(next);

  if (next.focus_revision !== shown.focus) {
    shown.focus = next.focus_revision;
    if (next.focus !== null) {
      moveFocus(next.focus);
    }
  }
  renderDetached(next.ai_detached);
  renderMulti(next.multi);
  if (next.result_revision !== shown.result) {
    loadResult();
  }
  // AI の面（やり取り・途中の答え・操作・確認のダイアログ）。別の窓が開いている間、確認は Python が別の窓の view にだけ入れる
  aiView.render(next);
  if (next.notice !== null && next.notice.id !== shown.notice) {
    shown.notice = next.notice.id;
    notice(next.notice.title, next.notice.body, next.notice.ok);
  }
}

// キャラクターの絵（ADR-0174 決定2）。view.character が偽なら、どの絵も hidden のまま（今と同じ画面）。
// 起動時の絵は view.splash が真のときに 1 回だけ。空の窓の絵はまだ一度も比べていないとき、差分なしの絵は比べ終わって差分が 0 件のとき。
// どちらも比較中は隠す。差分なしの絵は出すたびに最初から 1 回だけ動かす
function renderCharacters(next) {
  const on = next.character === true;
  if (on && next.splash === true && !splashPlayed) {
    splashPlayed = true;
    playSplash($("splash"));
  }
  if (next.busy) {
    scene.neverCompared = false; // 比べ始めたら、結果を読み直す前でも空の窓の絵は出さない
  }
  revealCharacters(["character-welcome"], on && scene.neverCompared);
  const noDiff = on && scene.noDiff && !next.busy;
  const appeared = noDiff && $("character-no-diff").hidden;
  revealCharacters(NO_DIFF_IDS, noDiff);
  if (appeared) {
    replayOnce($("character-no-diff"));
    replayOnce($("character-no-diff-raw"));
  }
}

// 単純比較の設定（ADR-0166 / REQ-227 §11）。欄は番号が変わったときだけ入れ直す（打っている途中の文字を消さない）。
// 誤りの文・見出しの横の要約・窓の下の行（全文は title にも）は文字として描く
function renderRawOptions(next) {
  const raw = next.raw_options;
  if (raw.fill !== shown.rawOptions) {
    shown.rawOptions = raw.fill;
    fillRawOptions(raw.values, raw.examples);
  }
  const problems = $("rawopt-problems");
  problems.hidden = raw.problems.length === 0;
  text(problems, raw.problems.join("\n"));
  text($("raw-options-summary"), raw.summary);
  const line = $("raw-settings-line");
  line.hidden = next.raw_settings_line === null;
  text(line, next.raw_settings_line);
  line.title = next.raw_settings_line === null ? "" : next.raw_settings_line;
}

// --- AI の面を別の窓で出しているとき（ADR-0160 決定1） ---

// 別の窓が開いている間は、右の面と境目を隠して左の結果を全幅にし、結果のタブの行の右端に「↗」（別の窓を前に出す）を出す。
// 閉じたら右の面に戻す（答えとやり取りは Python に残っているので、同じものが出る）。
function renderDetached(detached) {
  $("ai-pane").hidden = detached;
  $("split-handle").hidden = detached;
  split.classList.toggle("pilot-split--detached", detached);
  $("ai-front").hidden = !detached;
}

// まとめての比較の一覧（REQ-122）。行は番号が変わったときだけ作り直し、選んでいる行は aria-selected で表す。
// 「全部」のタブ（ADR-0152 決定7）は一覧があるときだけ出す。無ければ隠し、開いていれば構造化のタブへ移す。
function renderMulti(multi) {
  $("multi").hidden = multi === null;
  $("multi-handle").hidden = multi === null;
  $("tab-all").hidden = multi === null;
  if (multi === null) {
    shown.multi = null;
    shown.send = null;
    shown.all = null;
    shown.tab = null;
    $("multi-pairing").hidden = true;
    if (!$("panel-all").hidden) {
      selectTab("structured");
    }
    return;
  }
  // 組の手での選び直し（REQ-269）。multi.pairing が null（開いた保存の結果・1 つのログの中で組にしたとき）なら並びごと隠す。
  // 押せるかは Python の値のまま
  const pairing = multi.pairing ?? null;
  $("multi-pairing").hidden = pairing === null;
  $("choose-partner").disabled = pairing === null || !pairing.choose;
  $("unpair").disabled = pairing === null || !pairing.unpair;
  text($("tab-all"), multi.all_title);
  if (multi.all_revision !== shown.all) {
    shown.all = multi.all_revision;
    loadAllRows();
  }
  // 開くタブは Python が決める（番号が変わったときだけ移す。矢印のキーで行を選んだときも同じ。ADR-0152 決定8）。
  // この時点のタブの aria-disabled は前の組の値なので、ここでは確かめない。押せないタブへ移っていないかは、
  // 続けて届く結果を描くとき（drawTabStates → keepSelectableTab。ADR-0164 決定1）に確かめる
  if (multi.tab_revision !== shown.tab) {
    shown.tab = multi.tab_revision;
    selectTab(multi.tab);
  }
  const body = $("multi-table").tBodies[0];
  // 「AI に送る」の列は、まとめて比べた結果のときだけ出す（開いた保存の結果では multi.send が null。REQ-265 §2）
  const withSend = multi.send !== null;
  setSendColumn(withSend);
  if (multi.revision !== shown.multi || withSend !== shown.send) {
    shown.multi = multi.revision;
    shown.send = withSend;
    const fragment = document.createDocumentFragment();
    for (const row of multi.rows) {
      const tr = document.createElement("tr");
      if (withSend) {
        tr.append(sendCell(row));
      }
      const status = make("td", "", row.status);
      if (row.manual) {
        // 手で組にした印（REQ-269）。状態の文の後ろに足す。textContent だけで描く
        status.append(" ", make("span", "pilot-manual-mark", texts["compare.multi.paired_by_hand"]));
      }
      tr.append(make("td", "", row.name), status, make("td", "pilot-num", row.diffs));
      tr.title = row.name;
      if (row.unpaired) {
        tr.dataset.unpaired = "true";
      }
      fragment.append(tr);
    }
    body.replaceChildren(fragment);
  }
  Array.from(body.rows).forEach((tr, index) => {
    tr.setAttribute("aria-selected", String(index === multi.selected));
  });
  drawSendChecks(multi, body);
}

// 行の先頭の「AI に送る」のチェックのセル。状態は描くたびに Python の値で入れる（drawSendChecks）。
// チェックのセルの中のクリックでは行を選ばない（行の選びとチェックは別の操作）
function sendCell(row) {
  const td = make("td", "pilot-check-cell", null);
  const check = make("input", "pilot-check", null);
  check.type = "checkbox";
  check.setAttribute("aria-label", `${texts["compare.multi.col.send"]}: ${row.name}`);
  check.addEventListener("change", () => {
    const tr = check.closest("tr");
    act("set_send_pair", tr.sectionRowIndex, check.checked);
  });
  td.append(check);
  return td;
}

// チェックの状態を Python の値（multi.send）で描く。rows は行ごとに true・false・null（送れない組。押せない）。
// 見出しは全部・一部（途中の印）・なし。押せる組の数とチェックの数から決める（batch.js の drawAllCheck と同じ決まり）
function drawSendChecks(multi, body) {
  const send = multi.send;
  const all = $("multi-send-all");
  if (send === null) {
    return;
  }
  const targets = send.rows.filter((value) => value !== null);
  const on = targets.filter((value) => value).length;
  all.checked = targets.length > 0 && on === targets.length;
  all.indeterminate = on > 0 && on < targets.length;
  all.disabled = targets.length === 0 || !send.enabled;
  Array.from(body.rows).forEach((tr, index) => {
    const check = tr.querySelector(".pilot-check-cell .pilot-check");
    if (check === null) {
      return;
    }
    const value = send.rows[index] ?? null;
    check.checked = value === true;
    check.disabled = value === null || !send.enabled;
  });
}

// 「全部」のタブの行を取って描く（ADR-0152 決定7）。行は result() とは別に取る（行を選ぶたびには取らない）。
// 呼んでいる途中なら、終わってからもう1回。描くのは番号が今の view のものと同じときだけ。
let allLoading = false;
let allAgain = false;

async function loadAllRows() {
  if (allLoading) {
    allAgain = true;
    return;
  }
  allLoading = true;
  try {
    do {
      allAgain = false;
      const answer = await call("all_rows");
      if (!answer.ok) {
        text(statusMain, answer.message);
        break;
      }
      if (answer.all.revision === shown.all) {
        fillTable(
          "all-table",
          answer.all.rows,
          (row) => [row.command, row.op, row.category, row.target, row.before, row.after],
          withTitles,
        );
        // 変更の行の違う文字に印を付ける（変更前は 5 列目、変更後は 6 列目。行を作ったときに 1 回だけ。REQ-226 §4.2）
        paintTableMarks("all-table", answer.all.rows, answer.all.marks, 4, 5);
      }
    } while (allAgain);
  } finally {
    allLoading = false;
  }
}

// --- 結果の表 ---

// 行をまとめて足す（数千〜数万行。DocumentFragment で1回だけ表に入れる）。cells は1行の文字の並びを返す。
function fillTable(id, rows, cells, decorate) {
  const fragment = document.createDocumentFragment();
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of cells(row)) {
      tr.append(make("td", "", value));
    }
    if (decorate) {
      decorate(tr, row);
    }
    fragment.append(tr);
  }
  $(id).tBodies[0].replaceChildren(fragment);
}

// 切れて見えないセルの全文は、指を置くと出る（title。文字のまま）
function withTitles(tr) {
  for (const td of tr.cells) {
    td.title = td.textContent;
  }
}

// 表の変更の行で、違う文字に印を付ける（REQ-226 / ADR-0165）。marks は Python が返した [{index, before, after}, …]。
// index は rows の番号（表の行の番号と同じ。カテゴリの絞り込みは hidden なので番号は変わらない）。
// before と after は変更前・変更後の列の番号（0 から）。セルの title（全文）はそのまま
function paintTableMarks(id, rows, marks, before, after) {
  const trs = $(id).tBodies[0].rows;
  for (const mark of marks || []) {
    const tr = trs[mark.index];
    const row = rows[mark.index];
    if (tr === undefined || row === undefined) {
      continue;
    }
    paintMarks(tr.cells[before], row.before, mark.before, "removed");
    paintMarks(tr.cells[after], row.after, mark.after, "added");
  }
}

function note(id, value) {
  $(id).hidden = value === null;
  text($(id), value);
}

// --- カテゴリのチップ（ADR-0155 決定1）。件数と文字は Python が作り、行を隠すのはここ ---

// 「すべて N」と、カテゴリごとの「名前 件数」のボタンを描く。値は data- の属性に入れず、ここ（chips）に持つ。
// 前に選んでいたカテゴリが新しい一覧にあれば同じ絞り込みをかけ、無ければ「すべて」に戻す。一覧が空なら枠を隠す。
function drawChips(categories, allText) {
  const box = $("structured-chips");
  if (!categories.some((category) => category.value === structuredCategory)) {
    structuredCategory = null;
  }
  box.hidden = categories.length === 0;
  chips = [{ value: null, label: allText }, ...categories].map((category) => {
    const button = make("button", "pilot-chip", category.label);
    button.type = "button";
    button.addEventListener("click", () => {
      structuredCategory = category.value;
      applyCategory();
    });
    return { button, value: category.value };
  });
  box.replaceChildren(...(categories.length === 0 ? [] : chips.map((chip) => chip.button)));
  applyCategory();
}

// 選んでいるカテゴリで、構造化比較の全部の行の hidden を付け替える（大きさは読まない）。押されたチップは aria-pressed で表す。
function applyCategory() {
  for (const chip of chips) {
    chip.button.setAttribute("aria-pressed", String(chip.value === structuredCategory));
  }
  const rows = $("structured-table").tBodies[0].rows;
  for (let index = 0; index < rows.length; index += 1) {
    rows[index].hidden = structuredCategory !== null && structuredRows[index].category !== structuredCategory;
  }
}

// --- 単純比較の表と「差分のみ表示」（ADR-0155 決定2〜5） ---

// 単純比較の行を 1 回だけ作る（折りたたみの行も一緒に作る）。行は raw_rows の番号を data-index に持ち、rawEls から番号で引く。
// 折りたたみの範囲の外の行（差分の行と前後 3 行）は data-keep を持つ。折りたたみの行は範囲の最初の行の直前に入れ、data-index を持たない。
// DocumentFragment に入れて、最後に 1 回だけ表に入れる。大きさを読むものは使わない
// 変更の行の印は、呼ぶ前に setRawMarks で rawMarks に入れておく。変更前の文字（3 列目）と変更後の文字（5 列目）を行を作るときに描く（REQ-226）
function fillRaw(rows, folds) {
  rawEls = [];
  const foldAt = new Map(folds.map((fold) => [fold.start, fold]));
  const fragment = document.createDocumentFragment();
  let hiddenUntil = -1; // 折りたたみの範囲の終わり（この番号の行から先は残す）
  rows.forEach((row, i) => {
    const fold = foldAt.get(i);
    if (fold !== undefined) {
      const foldRow = make("tr", "pilot-raw-fold", null);
      foldRow.dataset.keep = "true";
      const cell = make("td", "", fold.text);
      cell.colSpan = 5;
      foldRow.append(cell);
      fragment.append(foldRow);
      hiddenUntil = i + fold.count;
    }
    const tr = document.createElement("tr");
    // 印は記号のまま。無視した行（x）と同じとみなした行（=）だけ Python が渡す文（「無視」「同じ」）にする。
    // 移動元・移動先の行（REQ-228）は Python が渡す行ごとの文（「移動元 1」「移動先 1」）を先に引く
    const markText = rawMoveLabels.has(i)
      ? rawMoveLabels.get(i)
      : Object.hasOwn(rawMarkLabels, row.mark)
        ? rawMarkLabels[row.mark]
        : row.mark;
    for (const value of [markText, row.before_no === null ? "" : String(row.before_no), row.before_text, row.after_no === null ? "" : String(row.after_no), row.after_text]) {
      tr.append(make("td", "", value));
    }
    const mark = rawMarks.get(i);
    if (mark !== undefined) {
      paintMarks(tr.cells[2], row.before_text, mark.before, "removed");
      paintMarks(tr.cells[4], row.after_text, mark.after, "added");
    }
    tr.dataset.index = String(i);
    tr.setAttribute("aria-selected", "false");
    if (row.is_diff) {
      tr.dataset.diff = "true";
    }
    if (rawSetAside.has(i)) {
      tr.dataset.setAside = "true";
    }
    if (i >= hiddenUntil) {
      tr.dataset.keep = "true";
    }
    rawEls.push(tr);
    fragment.append(tr);
  });
  $("raw-table").tBodies[0].replaceChildren(fragment);
  applyDiffOnly();
}

// 単純比較の変更の行の印（Python が返した [{index, before, after}, …]）を raw_rows の番号で引ける Map にする（REQ-226）
function setRawMarks(marks) {
  rawMarks = new Map((marks || []).map((mark) => [mark.index, mark]));
}

// 「差分のみ表示」の切り替え。tbody のクラスを付け外しするだけ（隠す・出すのは pilot.css の 2 つの指定。ADR-0155 決定3）
function applyDiffOnly() {
  $("raw-table").tBodies[0].classList.toggle("pilot-diff-only", $("raw-diff-only").checked);
}

// 「無視した行 N 行…」のボタン（REQ-227 §11.4）。押している間は表を脇に置いた行（data-set-aside）だけにする。
// tbody のクラス pilot-set-aside-only を付け外しするだけ（隠すのは pilot.css）
function setAsideOnly() {
  return $("raw-set-aside").getAttribute("aria-pressed") === "true";
}

function applySetAside(pressed) {
  $("raw-set-aside").setAttribute("aria-pressed", String(pressed));
  $("raw-table").tBodies[0].classList.toggle("pilot-set-aside-only", pressed);
}

// 矢印のキーで次に選ぶ行。差分のみがオンなら、隠れた行（data-keep の無い行）を飛ばす
function nextRaw(step) {
  let index = rawSelected + step;
  if ($("raw-diff-only").checked) {
    while (index >= 0 && index < rawEls.length && !rawEls[index].dataset.keep) {
      index += step;
    }
  }
  if (setAsideOnly()) {
    while (index >= 0 && index < rawEls.length && !rawEls[index].dataset.setAside) {
      index += step;
    }
  }
  return index;
}

function drawResult(result) {
  // 表の中身の番号（rows_revision）が前に描いたものと同じなら、AI の答えだけが変わった（「AI で解析」の確認・答え。REQ-264 §2）。
  // 表・絞り込み・選んでいる行・スクロールには触らず、AI の答えとキャラクターの場面だけを描き直す
  if (result.rows_revision === shown.rows) {
    aiView.drawResult(result);
    scene.noDiff = result.no_diff === true;
    scene.neverCompared = result.never_compared === true;
    return;
  }
  shown.rows = result.rows_revision;
  // 「全部」のタブの題は view.multi から描く（renderMulti）。ここでは組ごとのタブだけ
  for (const tab of TABS.filter((name) => name !== "all")) {
    text($(`tab-${tab}`), result.titles[tab]);
  }
  drawTabStates(result);
  note("structured-warnings", result.warnings);
  text($("structured-note"), result.structured_note);
  // 機種違いの失敗のときだけ、エラーの全文と警告を「詳しい内容」に入れる（文字のまま。結果が変わるたびに閉じる）
  const detail = $("structured-detail");
  detail.open = false;
  detail.hidden = !result.structured_detail;
  text($("structured-detail-text"), result.structured_detail || "");
  fillTable(
    "structured-table",
    result.structured_rows,
    (row) => [row.op, row.category, row.target, row.item, row.before, row.after],
    withTitles,
  );
  // 変更の行の違う文字に印を付ける（変更前は 5 列目、変更後は 6 列目。行を作ったときに 1 回だけ。REQ-226 §4.2）
  paintTableMarks("structured-table", result.structured_rows, result.structured_marks, 4, 5);
  // 行を作り直したら、同じカテゴリがあれば同じ絞り込みをかける（無ければ「すべて」に戻す。ADR-0155 決定1）
  structuredRows = result.structured_rows;
  drawChips(result.categories, result.categories_all);
  text($("raw-note"), result.raw_note);
  // 無視した行・同じとみなした行の数のボタン（REQ-227 §11.4）。結果を描き直したら押していない状態に戻す
  const setAside = $("raw-set-aside");
  setAside.hidden = result.raw_set_aside === null;
  text(setAside, result.raw_set_aside);
  rawSetAside = new Set(result.raw_set_aside_rows);
  rawMarkLabels = result.raw_mark_labels || {};
  rawMoveLabels = new Map((result.raw_move_labels || []).map((item) => [item.index, item.text]));
  rawRows = result.raw_rows;
  rawSelected = -1;
  // 単純比較は専用の関数で描く（折りたたみの行と data-index。差分のみ表示のチェックは作り直した後も付け直す。ADR-0155 決定3〜5）
  // 変更の行の印（result.raw_marks）を番号で引けるように持ってから描く（下の2つの欄にも使う。REQ-226 §4.2・§4.3）
  setRawMarks(result.raw_marks);
  fillRaw(rawRows, result.raw_folds);
  applySetAside(false);
  // 下の2つの欄（読むだけの文字の箱。REQ-226 §4.3）は空にする
  $("raw-before").replaceChildren();
  $("raw-after").replaceChildren();
  text($("unparsed-note"), result.unparsed_note);
  fillTable("unparsed-table", result.unparsed_rows, (row) => [row.op, row.line, row.parents], withTitles);
  // AI の答えは AI の面の左の吹き出しに出す（ai_view.js。答えは textContent だけで描く）
  aiView.drawResult(result);
  // キャラクターの絵の場面（ADR-0174 決定2）。絵は次の render で出し分ける
  scene.noDiff = result.no_diff === true;
  scene.neverCompared = result.never_compared === true;
}

// 結果の番号が変わったら result() を呼ぶ。呼んでいる途中なら、終わってからもう1回。
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
        text(statusMain, answer.message);
        break;
      }
      if (answer.result.revision !== shown.result) {
        shown.result = answer.result.revision;
        drawResult(answer.result);
      }
      render(answer.view);
    } while (resultAgain);
  } finally {
    resultLoading = false;
  }
}

// 単純比較の行を選ぶ。下の2つの欄に、その行の変更前と変更後の全文を出す。index は raw_rows の番号で、行の要素は rawEls から引く
// （折りたたみの行が入っても番号がずれない。ADR-0155 決定4）。差分のみ表示で選んだ行が隠れても、選択は消さない。
// 変更の行なら、表と同じ印を欄にも付ける（欄は読むだけの文字の箱。REQ-226 §4.3）。
function selectRaw(index, scroll) {
  if (index < 0 || index >= rawRows.length || index >= rawEls.length) {
    return;
  }
  if (rawSelected >= 0 && rawEls[rawSelected]) {
    rawEls[rawSelected].setAttribute("aria-selected", "false");
  }
  rawSelected = index;
  const tr = rawEls[index];
  tr.setAttribute("aria-selected", "true");
  const mark = rawMarks.get(index);
  paintMarks($("raw-before"), rawRows[index].before_text, mark === undefined ? [] : mark.before, "removed");
  paintMarks($("raw-after"), rawRows[index].after_text, mark === undefined ? [] : mark.after, "added");
  if (scroll) {
    tr.scrollIntoView({ block: "nearest" });
  }
}

// 「前の差分」「次の差分」。どの行へ行くかは Python（find_diff）が決める。
async function jump(step) {
  const answer = await call("jump", rawSelected, step);
  if (!answer.ok) {
    text(statusMain, answer.message);
    return;
  }
  render(answer.view);
  if (answer.row !== null) {
    selectRaw(answer.row, true);
    $("raw-wrap").focus();
  }
}

// --- 左右の面の境目（ADR-0148 決定2）。幅は要素の CSS 変数に入れるだけで、保存しない ---

const split = $("split");
// 名前は handle にしない（後ろの関数 handle と同じ名前になり、モジュールが読み込めなくなるため）
const splitHandle = $("split-handle");
let leftWidth = null; // 動かした後の左の面の幅（px）。動かす前は CSS の初めの幅（58%）
let dragging = null;

// 左の面の幅を px で決める。両側とも PANE_MIN より狭くしない。境目が隠れている（縦に積んでいる）ときは何もしない。
function setLeftWidth(px) {
  const total = split.clientWidth - splitHandle.offsetWidth;
  if (splitHandle.offsetParent === null || total <= 0) {
    return;
  }
  const width = Math.round(Math.min(Math.max(px, PANE_MIN), Math.max(PANE_MIN, total - PANE_MIN)));
  leftWidth = width;
  split.style.setProperty("--pilot-split-left", `${width}px`);
  splitHandle.setAttribute("aria-valuenow", String(Math.round((width / total) * 100)));
}

function currentLeftWidth() {
  return $("result-pane").getBoundingClientRect().width;
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
    setLeftWidth(dragging.width + event.clientX - dragging.x);
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
    setLeftWidth(currentLeftWidth() + (event.key === "ArrowLeft" ? -SPLIT_STEP : SPLIT_STEP));
  }
});
// 窓の大きさが変わったら、動かした幅を最小の幅の中に詰め直す
window.addEventListener("resize", () => {
  if (leftWidth !== null) {
    setLeftWidth(leftWidth);
  }
});

// --- コマンドの組の一覧と比較の表の境目（REQ-224 / ADR-0163）。幅は #multi の CSS 変数に入れるだけで、保存しない ---

const resultPane = $("result-pane");
const multiHandle = $("multi-handle");
let multiWidth = null; // 動かした後の一覧の幅（px）。動かす前は CSS の初めの幅（--pilot-split-width）
let multiDragging = null;

// 一覧の幅を px で決める。一覧は MULTI_MIN、表の側は PANE_MIN より狭くしない（合計が足りなければ MULTI_MIN）。
// 境目が隠れている（まとめて比べていない・縦に積んでいる）ときは何もしない。
function setMultiWidth(px) {
  const total = resultPane.clientWidth - multiHandle.offsetWidth;
  if (multiHandle.offsetParent === null || total <= 0) {
    return;
  }
  const width = Math.round(Math.min(Math.max(px, MULTI_MIN), Math.max(MULTI_MIN, total - PANE_MIN)));
  multiWidth = width;
  $("multi").style.setProperty("--pilot-split-width", `${width}px`);
  multiHandle.setAttribute("aria-valuenow", String(Math.round((width / total) * 100)));
}

function currentMultiWidth() {
  return $("multi").getBoundingClientRect().width;
}

multiHandle.addEventListener("pointerdown", (event) => {
  if (event.button !== 0) {
    return;
  }
  event.preventDefault();
  multiHandle.setPointerCapture(event.pointerId);
  multiDragging = { x: event.clientX, width: currentMultiWidth() };
});
multiHandle.addEventListener("pointermove", (event) => {
  if (multiDragging !== null) {
    setMultiWidth(multiDragging.width + event.clientX - multiDragging.x);
  }
});
for (const name of ["pointerup", "pointercancel"]) {
  multiHandle.addEventListener(name, (event) => {
    multiDragging = null;
    if (multiHandle.hasPointerCapture(event.pointerId)) {
      multiHandle.releasePointerCapture(event.pointerId);
    }
  });
}
// 左右の矢印のキーで SPLIT_STEP px ずつ動かす
multiHandle.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    setMultiWidth(currentMultiWidth() + (event.key === "ArrowLeft" ? -SPLIT_STEP : SPLIT_STEP));
  }
});
// 窓の大きさが変わったら、動かした幅を最小の幅の中に詰め直す
window.addEventListener("resize", () => {
  if (multiWidth !== null) {
    setMultiWidth(multiWidth);
  }
});

// --- 5 つの表の列の幅（REQ-224 / ADR-0163）。読み込んだときに 1 回だけ付ける（表を描き直しても見出しと <colgroup> は作り直さない） ---

// 見出しのセルに境目を足し、列の幅の部品につなぐ。隠れたタブの表は、開いて枠に幅が付いたときに固まる。
// 先頭の「AI に送る」のチェックの列（pilot-col-check）は幅を決め打ちで、境目は付けない（REQ-265）。
// only は 1 つの表だけつなぎ直すときの表の id（multi-table はチェックの列を足し引きするので、そのたびにつなぎ直す）。
const columnBindings = new Map();

function bindTableColumns(only = null) {
  for (const item of COLUMN_TABLES) {
    if (only !== null && item.id !== only) {
      continue;
    }
    const target = $(item.id);
    const cols = Array.from(target.querySelectorAll("colgroup > col"));
    const heads = Array.from(target.tHead.rows[0].cells);
    const columns = heads.map((head, index) => {
      if (head.classList.contains("pilot-col-check")) {
        return { col: cols[index], head, handle: null };
      }
      const grip = makeGrip(texts["compare.col.resize"]);
      head.append(grip);
      return { col: cols[index], head, handle: grip };
    });
    // チェックの列を出していないとき（開いた保存の結果）は、埋める列の番号を 1 つ前にする
    const lead = heads.filter((head) => head.classList.contains("pilot-col-check")).length;
    const fill = item.fill === undefined ? undefined : item.fill - (1 - lead);
    columnBindings.set(item.id, bindColumnWidths(target, columns, { fill, fillMin: item.fillMin }));
  }
}

// 一覧の「AI に送る」のチェックの列を出す・外す（REQ-265）。列の組み立てが変わるので、幅の部品を止めてから境目も作り直してつなぎ直す
// （column_width.js の決まり）。外した列は持っておき、戻すときに先頭へ戻す。幅は決め直す（動かした幅は覚えない）
const sendColumn = { col: null, head: null, shown: true };

function setSendColumn(visible) {
  if (visible === sendColumn.shown) {
    return;
  }
  const table = $("multi-table");
  const colgroup = table.querySelector("colgroup");
  const headRow = table.tHead.rows[0];
  columnBindings.get("multi-table")?.disconnect();
  for (const old of headRow.querySelectorAll(".pilot-col-resize")) {
    old.remove();
  }
  if (visible) {
    colgroup.prepend(sendColumn.col);
    headRow.prepend(sendColumn.head);
  } else {
    sendColumn.col = colgroup.querySelector("col.pilot-col-check");
    sendColumn.head = $("multi-send-col");
    sendColumn.col.remove();
    sendColumn.head.remove();
  }
  sendColumn.shown = visible;
  for (const col of colgroup.querySelectorAll("col")) {
    col.style.width = "";
  }
  table.style.width = "";
  bindTableColumns("multi-table");
}

// --- ダイアログ ---

// 貼り付け。既定のフォーカスは欄。「OK」なら文字を Python に渡す（ここには残さない）。
async function paste(side) {
  const input = make("textarea", "pilot-input pilot-mono pilot-nowrap", null);
  input.spellcheck = false;
  const ok = await panel(
    view.labels.paste_titles[side],
    [make("p", "", texts["compare.paste.prompt"]), input],
    texts["compare.dialog.ok"],
    texts["compare.dialog.cancel"],
    input,
  );
  const value = input.value;
  input.value = "";
  if (ok) {
    await act("paste", side, value);
  }
}

// 「相手を選ぶ…」（REQ-269）。選んでいる行の相手の候補（もう片方の側の区間のすべてと、それぞれの今の状態）を Python から受け取り、
// ラジオボタンの一覧を出す。既定のフォーカスは「キャンセル」。OK で選ばれていれば、選んだ区間の番号を Python に渡す（組にして比べるのは Python）
async function choosePartner() {
  if (view === null || view.multi === null || view.multi.pairing === null || !view.multi.pairing.choose) {
    return;
  }
  const row = view.multi.selected;
  const result = await call("partner_choices", row);
  if (!result.ok) {
    await handle(result);
    return;
  }
  const partners = result.partners ?? null;
  if (partners === null) {
    return;
  }
  let picked = null;
  const list = make("div", "pilot-stack", null);
  for (const choice of partners.choices) {
    const line = make("label", "pilot-radio", null);
    const input = make("input", "pilot-check", null);
    input.type = "radio";
    input.name = "partner-choice";
    input.addEventListener("change", () => {
      picked = choice.number;
    });
    line.append(input, make("span", "pilot-mono", choice.label), make("span", "pilot-note", choice.state));
    list.append(line);
  }
  const ok = await panel(
    partners.title,
    [make("p", "", partners.prompt), list],
    texts["compare.dialog.ok"],
    texts["compare.dialog.cancel"],
  );
  if (ok && picked !== null) {
    await act("choose_partner", row, picked);
  }
}

// 「HTML・CSV で出力…」（ADR-0156）。本文は伏せる決まりの案内（決定9）、選択肢は Python が渡す。既定のフォーカスは HTML、Esc は「やめる」。
// 「やめる」でなければ選んだ形を Python に渡す（出すかどうか・保存の場所・伏せることは Python が決める）。
async function exportResult() {
  if (view === null) {
    return;
  }
  const kind = await choose(texts["compare.export.title"], texts["compare.export.body"], view.labels.export_choices, "html", "cancel");
  if (kind !== "cancel") {
    await act("export_result", kind);
  }
}

// 「機器ごとに分けて一括比較で開く…」（REQ-267）。押せないときは何もしない。1 回目で Python が書き出す前の注意（split_confirm）を返したら、
// dialog.js の confirm で聞き（既定は「キャンセル」）、「フォルダーを選ぶ…」のときだけ confirmed=true で呼び直す。フォルダーは Python が選ぶ
async function splitDevices() {
  if (view === null || $("split-devices").getAttribute("aria-disabled") === "true") {
    return;
  }
  const result = await call("split_devices", form());
  if (result.ok && result.split_confirm) {
    const ask = result.split_confirm;
    render(result.view);
    if (await confirm(ask.title, ask.body, ask.yes, ask.no)) {
      await handle(await call("split_devices", form(), true));
    }
    return;
  }
  await handle(result);
}

// --- 操作を Python に渡す ---

// 返ってきたものを出す。知らせは出してから描く（wx と同じ順）。
async function handle(result) {
  if (!result.ok) {
    text(statusMain, result.message);
    return;
  }
  if (result.notice) {
    await notice(result.notice.title, result.notice.body, result.notice.ok);
  }
  render(result.view);
}

async function act(name, ...args) {
  if (view === null) {
    return; // まだ最初の状態が届いていない
  }
  await handle(await call(name, form(), ...args));
}

// Python からの知らせ（"compare.changed"）で state() を呼び直す。呼んでいる途中なら、終わってからもう1回。
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

// AI の面から Python を呼ぶ口。form が要るメソッドには、この窓の欄の値を足す
function aiCall(name, ...args) {
  return AI_FORM_METHODS.has(name) ? call(name, form(), ...args) : call(name, ...args);
}

// AI の面（ai_view.js。AI の窓と同じ部品）。描き直しと知らせはこの窓の handle で出す
const aiView = createAiView({ $, call: aiCall, act, on, afterPreview: handle });
const { drawPartial } = aiView;

function selectTab(name) {
  for (const tab of TABS) {
    $(`tab-${tab}`).setAttribute("aria-selected", String(tab === name));
    $(`panel-${tab}`).hidden = tab !== name;
  }
}

// 押せないタブ（ADR-0164 決定1・2）。構造化比較が動いていない組では、構造化比較と構造化不可の行のタブを押せなくする。
// disabled 属性は使わない（フォーカスは止まれ、理由は title で読める）。押せるかと理由の文は Python が決める（result.tabs）
const OFF_TABS = ["structured", "unparsed"];

function tabOff(name) {
  return $(`tab-${name}`).getAttribute("aria-disabled") === "true";
}

function drawTabStates(result) {
  for (const tab of OFF_TABS) {
    const state = result.tabs[tab];
    const button = $(`tab-${tab}`);
    button.setAttribute("aria-disabled", state.disabled ? "true" : "false");
    button.title = state.reason || "";
  }
  keepSelectableTab(result.fallback_tab);
}

// 選んでいるタブが押せなくなったら、fallback_tab（単純比較）へ移す。
// まとめての比較で Python がタブを移したとき（multi.tab_revision）も、行を選ぶと結果の番号が進み、ここを通る
function keepSelectableTab(fallback) {
  const selected = TABS.find((tab) => $(`tab-${tab}`).getAttribute("aria-selected") === "true");
  if (selected !== undefined && tabOff(selected)) {
    selectTab(fallback);
  }
}

for (const tab of TABS) {
  // 押せないタブは押しても（Enter・Space も click として届く）移らない
  bind(`tab-${tab}`, () => {
    if (tabOff(tab)) {
      return;
    }
    selectTab(tab);
  });
}
for (const side of SIDES) {
  bindTail($(`${side}-name`));
  bind(`${side}-browse`, () => act("browse", side));
  bind(`${side}-paste`, () => paste(side));
  $(`${side}-segment`).addEventListener("change", () => act("form_changed"));
}
for (const id of ["kind", "config-platform"]) {
  $(id).addEventListener("change", () => act("form_changed"));
}
for (const id of ["platform", "command"]) {
  // 打っている途中では送らない（Enter かフォーカスが外れたときの change だけ。値が変われば Python が比較を始める。ADR-0078 決定3）
  $(id).addEventListener("change", () => act("form_changed"));
  // Enter は「比較実行」と同じ（wx の既定のボタン）。押せないときは何もしない
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.isComposing && view !== null && view.can_run) {
      event.preventDefault();
      act("run");
    }
  });
}
// 単純比較の行のクリックは data-index（raw_rows の番号）を読む。折りたたみの行は data-index が無いので何もしない（ADR-0155 決定4）
$("raw-table").tBodies[0].addEventListener("click", (event) => {
  const tr = event.target.closest("tr");
  if (tr && tr.dataset.index !== undefined) {
    selectRaw(Number(tr.dataset.index), false);
  }
});
$("raw-wrap").addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    selectRaw(nextRaw(event.key === "ArrowDown" ? 1 : -1), true);
  }
});
$("raw-diff-only").addEventListener("change", () => applyDiffOnly());
// 「無視した行」のボタンとはどちらか 1 つ（REQ-227 §11.4）。差分のみ表示を付けたら、ボタンは押していない状態に戻す
$("raw-diff-only").addEventListener("change", () => {
  if ($("raw-diff-only").checked && setAsideOnly()) {
    applySetAside(false);
  }
});
bind("raw-set-aside", () => {
  const pressed = !setAsideOnly();
  if (pressed && $("raw-diff-only").checked) {
    $("raw-diff-only").checked = false;
    applyDiffOnly();
  }
  applySetAside(pressed);
});
// 単純比較の設定の欄が変わったら（打ち終わった change・例から足す・置き換えを外す）Python に渡す。値が変われば Python が比較を始める
$("rawopt-fields").addEventListener("change", () => act("form_changed"));
bind("rawopt-reset", async () => handle(await call("reset_raw_options")));
// まとめての比較の一覧の行を選ぶ（どの組の結果を出すかは Python が決め、result() で取る）。矢印のキーでも動かす
$("multi-table").tBodies[0].addEventListener("click", (event) => {
  if (event.target.closest(".pilot-check-cell") !== null) {
    return; // 「AI に送る」のチェックのセルでは行を選ばない（チェックは別の操作。REQ-265）
  }
  const tr = event.target.closest("tr");
  if (tr) {
    act("select_pair", tr.sectionRowIndex);
  }
});
// 見出しのチェック: すべて選ぶ・すべて外す（全部チェック済みなら外す。それ以外は選ぶ）。結果は Python の値で描き直す
$("multi-send-all").addEventListener("change", () => {
  const rows = view !== null && view.multi !== null && view.multi.send !== null ? view.multi.send.rows : [];
  const targets = rows.filter((value) => value !== null);
  const on = !(targets.length > 0 && targets.every((value) => value));
  act("set_send_all", on);
});
$("multi-wrap").addEventListener("keydown", (event) => {
  if ((event.key === "ArrowDown" || event.key === "ArrowUp") && view !== null && view.multi !== null) {
    event.preventDefault();
    const next = view.multi.selected + (event.key === "ArrowDown" ? 1 : -1);
    if (next >= 0 && next < view.multi.rows.length) {
      act("select_pair", next);
    }
  }
});
// 組の手での選び直し（REQ-269）。押せるかと、組にして比べるかは Python が決める
bind("choose-partner", () => choosePartner());
bind("unpair", () => {
  if (view !== null && view.multi !== null) {
    return act("unpair", view.multi.selected);
  }
});
bind("swap", () => act("swap"));
bind("settings-toggle", () => setSettingsOpen($("settings-panel").hidden));
bind("prev-diff", () => jump(-1));
bind("next-diff", () => jump(1));
bind("run", () => act("run"));
bind("save", () => act("save"));
// 開くファイルは Python が窓のダイアログで選ぶ（パスは渡さない。REQ-120）
bind("open-result", () => act("open_result"));
bind("close-result", () => act("close_result"));
bind("export", () => exportResult());
bind("split-devices", () => splitDevices());
// AI の面を別の窓で開く「↗」と、別の窓が開いている間の「↗」（前に出す）。どちらも同じメソッド（開くか前に出すかは Python が決める）
bind("ai-detach", async () => handle(await call("open_ai_pane")));
bind("ai-front", async () => handle(await call("open_ai_pane")));
bind("cancel", async () => handle(await call("cancel")));
bind("open-batch", async () => handle(await call("open_batch")));
bind("open-settings", async () => handle(await call("open_settings")));
// 新しい版の知らせ（REQ-252 §5）。「更新…」は設定の窓を「更新」のタブで開き、「閉じる」はその起動の間は出さない
bind("update-open", async () => handle(await call("open_settings", "update")));
bind("update-dismiss", async () => handle(await call("dismiss_update_notice")));
on("compare.changed", () => reload());
on("compare.partial", drawPartial); // 届いたところまでの答え（ADR-0157 決定5）
// 設定の窓で自動送信やプロファイルを変えた後に、帯と切り替えを合わせる（窓が前に来たら状態を読み直す）
window.addEventListener("focus", () => {
  if (view !== null) {
    reload();
  }
});

if (await fillTexts()) {
  bindTableColumns();
  await reload();
}
