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
// AI の欄を隠している間（view.ai_hidden。ADR-0216 決定5）も同じ形で、タブの行に「« AI」（出す）を出す。

import { call, on } from "./bridge.js";
import { notice, panel } from "./dialog.js";
import { confirm } from "./dialog.js"; // 「機器ごとに分けて…」の書き出す前の注意（REQ-267）
import { choose } from "./dialog.js"; // 「HTML・CSV で出力…」の形を選ぶ（ADR-0156）
import { make, text } from "./dom.js";
import { createAiView } from "./ai_view.js"; // AI の面（REQ-220 §5.1）
import { bindTail, showTail } from "./path_tail.js"; // ファイルの名前の欄は後ろを見せる（REQ-211 §4）
import { bindColumnWidths, makeGrip } from "./column_width.js"; // 5 つの表の列の幅の境目（REQ-224 / ADR-0163）
import { paintMarks } from "./marks.js"; // 変更の行で違う文字の印（REQ-226 / ADR-0165）
import { nextSortDirection, sortOrder } from "./table_sort.js"; // 構造化比較の表の見出しの並べ替えの比べ方（REQ-369）
import { fillRawOptions, readRawOptions } from "./raw_options.js"; // 単純比較の設定の欄（REQ-227 / ADR-0166。設定の窓と同じ部品）
import { openRuleAiWith } from "./raw_options.js"; // 右クリックから「AI に式を頼む…」の小さな窓を値を入れて開く（REQ-360）
import { digitRule, escapeRegex, ignoreLineRule, ruleFits } from "./raw_rule_text.js"; // 右クリックから足すルールの式（REQ-366 §2〜§4）
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
  "compare.multi.show_raw", // show を単純比較で出した組の印（REQ-282）
  "compare.multi.show_both", // show を表と単純比較の両方で出した組の印（REQ-282）
];
// 一覧の行の show_method（Python が決める）から、印の文のキーを引く（REQ-282）
const SHOW_METHOD_MARKS = { raw: "compare.multi.show_raw", both: "compare.multi.show_both" };
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
  "ask_shown",
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
  openTab: null, // open_tab のタブを選んだ番号（Python の result の open_tab_revision。新しい結果を出したときだけ進む。REQ-282）
  notice: null,
  multi: null,
  send: null, // 描いた一覧に「AI に送る」の列があったか（REQ-265）
  all: null, // 描いた「全部」のタブの行の番号
  tab: null, // 移したタブの番号（Python の tab_revision）
  rawOptions: null, // 単純比較の設定の欄を入れ直した番号（Python の raw_options.fill）
};
let rawRows = []; // 単純比較の行（下の2つの欄に全文を出すため）
let rawSelected = -1;
// 右クリックのメニュー（REQ-360・REQ-366）が開いている間だけ持つ、選んだ文字の {index, side, text}（選んだ文字が無いときは text が null）と、
// 右クリックした行の {index, side}（開いていなければ null。side は右クリックしたセルの側で、分からなければ null）。閉じたときに戻すフォーカス
let rawMenuPick = null;
let rawMenuReturn = null;
// 行ごとに効いているルール（result.raw_line_rules を raw_rows の番号で引く Map。REQ-366 §5）
let rawLineRules = new Map();
// 右クリックのメニューの項目の id（上から順。矢印キーの移動順）
const RAW_MENU_ITEMS = [
  "raw-menu-ai",
  "raw-menu-replace",
  "raw-menu-digits",
  "raw-menu-ignore",
  "raw-menu-rules",
  "raw-menu-ask",
];
// 構造化比較のタブ・コマンドの組の一覧の右クリックのメニュー（REQ-367）が開いている間だけ持つ値。開いているメニューの要素・
// 開く前のフォーカス・右クリックした行の番号（構造化比較は行の番号、組の一覧は一覧の行の番号）。閉じたら null
const rightMenu = { menu: null, back: null, index: -1 };
// 構造化比較の行ごとの、show の表の変更の行の変わった列の名前（result.structured_columns）と、元の行を探せるか（result.can_find_source）
let structuredColumns = [];
let canFindSource = false;
// 選んだ文字をそのまま置き換えのルールにするときの、置き換える文字（REQ-366 §2）
const RAW_RULE_VALUE = "<\u5024>"; // 山かっこの中は「値」（U+5024。画面の言語によらず同じ文字。日本語の文字を JavaScript に書かない決まりのため、コードで書く）
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
// 構造化比較の表の並べ替え（REQ-369。画面の中だけ。Python には渡さない）。column は見出しの番号（-1 は元の順）、ariaSort は向き（aria-sort の値）。
// structuredSortFrom は一括の比較が終わった（「全部」のタブへ移した）とき、元の順へ戻す結果の番号（これ以上の番号の結果を描くときに戻す）。無ければ null。
// 古い結果の読み込みの答えが遅れて届いても、新しい結果より前のものでは戻さない
const structuredSort = { column: -1, ariaSort: "none" };
let structuredSortFrom = null;
// 数字の部分を数として比べる器（Gi1/0/2 < Gi1/0/10）。言語は並べ替えるときの <html lang>（起動のあとで画面の言語が入るため、ここでは作らない）。言語ごとに 1 つ持つ
const structuredCollators = new Map();
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
  applyStartSplit(next.split_left); // 開いたときの左の面の幅（最初の 1 回だけ。REQ-370）

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
  renderAiHidden(!next.ai_detached && next.ai_hidden === true);
  renderMulti(next.multi, next.result_revision);
  if (next.result_revision !== shown.result) {
    loadResult();
  }
  // AI の面（やり取り・途中の答え・操作・確認のダイアログ）。別の窓が開いている間、確認は Python が別の窓の view にだけ入れる
  aiView.render(next);
  reportShown();
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
  // 設定の窓で保存した設定を受けたときの帯（REQ-361）。ボタンは load が真のときだけ（文は textContent）
  const follow = next.raw_follow;
  $("raw-follow").hidden = follow === null;
  text($("raw-follow-text"), follow === null ? "" : follow.text);
  $("raw-follow-load").hidden = follow === null || !follow.load;
  const line = $("raw-settings-line");
  line.hidden = next.raw_settings_line === null;
  text(line, next.raw_settings_line);
  line.title = next.raw_settings_line === null ? "" : next.raw_settings_line;
  renderRawRules(raw.rules || [], raw.rules_title);
  renderSkipColumns(next.skip_columns || []);
}

// この窓だけの「比べない列」（REQ-367 §3）。単純比較の設定の欄（#rawopt-fields）の外の一覧。名前と「外す」のボタン（aria-label は「外す: UPTIME」）。
// 空なら「なし」。中身が変わったときだけ作り直す。文字は make と textContent だけで入れる
let shownSkipColumns = null;
function renderSkipColumns(columns) {
  const key = JSON.stringify(columns);
  if (key === shownSkipColumns) {
    return;
  }
  shownSkipColumns = key;
  const list = $("skip-columns-list");
  if (columns.length === 0) {
    list.replaceChildren(make("li", "", $("skip-columns-none").textContent));
    return;
  }
  list.replaceChildren(
    ...columns.map((name) => {
      const item = make("li", "", null);
      const button = make("button", "pilot-button pilot-button--secondary", $("skip-columns-remove").textContent);
      button.type = "button";
      button.setAttribute("aria-label", $("skip-columns-remove-template").textContent.replace("{column}", name));
      button.addEventListener("click", () => act("unskip_column", name));
      item.append(make("span", "pilot-mono", name), " ", button);
      return item;
    }),
  );
}

// 単純比較のタブの「効いているルール」の一覧（REQ-359 §4）。ルールが無ければ隠す。行は中身が変わったときだけ作り直す
// （描くたびに作り直さない）。文字は text() と textContent だけで入れる。作り直したときは、操作していたチェックへ
// 行の位置でフォーカスを戻す。チェックを切り替えたら、欄（#rawopt-fields）へ rawopt-rule-toggle を送る
// （欄の無効を切り替えて change を送るのは raw_options.js。そこから今の form_changed に乗る）。
let shownRules = null;
function renderRawRules(rules, title) {
  $("raw-rules").hidden = rules.length === 0;
  text($("raw-rules-title"), title);
  const key = JSON.stringify(rules);
  if (key === shownRules) {
    return;
  }
  shownRules = key;
  const body = $("raw-rules-table").tBodies[0];
  const active = document.activeElement;
  const focused = active && active.dataset && active.dataset.ruleCheck ? Number(active.dataset.ruleCheck) : -1;
  body.replaceChildren(
    ...rules.map((rule, index) => {
      const tr = document.createElement("tr");
      const checkCell = document.createElement("td");
      const check = make("input", "pilot-check", null);
      check.type = "checkbox";
      check.checked = rule.enabled;
      check.dataset.ruleCheck = String(index);
      check.setAttribute("aria-label", `${rule.kind_text}: ${rule.pattern}`);
      check.addEventListener("change", () => {
        $("rawopt-fields").dispatchEvent(
          new CustomEvent("rawopt-rule-toggle", {
            detail: { kind: rule.kind, pattern: rule.pattern, replacement: rule.replacement, enabled: check.checked },
          }),
        );
      });
      checkCell.append(check);
      const pattern = make("td", "pilot-mono", rule.pattern);
      pattern.title = rule.pattern;
      const replacement = make("td", "pilot-mono", rule.replacement);
      replacement.title = rule.replacement;
      tr.append(checkCell, make("td", "", rule.kind_text), pattern, replacement, make("td", "", rule.hits));
      return tr;
    }),
  );
  if (focused >= 0) {
    const again = body.querySelector(`input[data-rule-check="${focused}"]`);
    if (again !== null) {
      again.focus();
    }
  }
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

// AI の欄を隠している間（ADR-0216 決定5。別の窓で出しているときは renderDetached の決めのまま）は、右の面と境目を隠して左を全幅にし、
// 結果のタブの行に「« AI」（AI の欄を出す）を出す。隠したときに「»」にフォーカスがあれば「« AI」へ、出したときに「« AI」にあれば「»」へ移す
// （キーボードだけで戻れるように）。隠した状態は Python の ai_hidden が持つ（ここでは覚えない）。
function renderAiHidden(hidden) {
  const active = document.activeElement;
  const hadHide = active === $("ai-hide");
  const hadShow = active === $("ai-show");
  if (hidden) {
    $("ai-pane").hidden = true;
    $("split-handle").hidden = true;
  }
  split.classList.toggle("pilot-split--ai-hidden", hidden);
  $("ai-show").hidden = !hidden;
  if (hidden && hadHide) {
    $("ai-show").focus();
  } else if (!hidden && hadShow) {
    $("ai-hide").focus();
  }
}

// まとめての比較の一覧（REQ-122）。行は番号が変わったときだけ作り直し、選んでいる行は aria-selected で表す。
// 「全部」のタブ（ADR-0152 決定7）は一覧があるときだけ出す。無ければ隠し、開いていれば構造化のタブへ移す。
function renderMulti(multi, resultRevision) {
  $("multi").hidden = multi === null;
  $("multi-handle").hidden = multi === null;
  $("tab-all").hidden = multi === null;
  if (multi === null) {
    shown.multi = null;
    shown.send = null;
    shown.all = null;
    shown.tab = null;
    $("multi-pairing").hidden = true;
    renderExcluded([], false);
    closeRightMenu(false);
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
    // 「全部」のタブへ移るのは、一括の比較が終わったときと一括の保存を開いたときだけ（組を選び直したときは移らない）。
    // 新しい結果なので、構造化比較の表の並べ替えは元の順へ戻す（REQ-369）。その結果をもう描いていれば今戻し、
    // まだなら、その番号以上の結果を描くときに戻す（drawResult）
    if (multi.tab === "all") {
      if (resultRevision <= shown.result) {
        resetStructuredSort();
        applyStructuredSort();
      } else {
        structuredSortFrom = resultRevision;
      }
    }
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
      if (Object.hasOwn(SHOW_METHOD_MARKS, row.show_method)) {
        // show の比べ方の印（REQ-282）。「手で組にした」の印があればその後ろに足す。textContent だけで描く
        status.append(" ", make("span", "pilot-show-method-mark", texts[SHOW_METHOD_MARKS[row.show_method]]));
      }
      tr.append(make("td", "", row.name), status, make("td", "pilot-num", row.diffs));
      tr.title = row.name;
      if (row.unpaired) {
        tr.dataset.unpaired = "true";
      }
      fragment.append(tr);
    }
    body.replaceChildren(fragment);
    closeRightMenu(false); // 行を作り直したので、一覧の右クリックのメニューは閉じる（REQ-367）
  }
  Array.from(body.rows).forEach((tr, index) => {
    tr.setAttribute("aria-selected", String(index === multi.selected));
  });
  drawSendChecks(multi, body);
  renderExcluded(multi.excluded || [], multi.restore === true);
}

// 比べる対象から外した区間（REQ-367 §4）。一覧の下の折りたたみ「外した区間 {n}」。中は名前と「戻す」。0 個なら隠す。
// 中身が変わったときだけ作り直す。文字は make と textContent だけで入れる（外した順の番号 k を戻す口に渡す。戻せないときは押せない）
let shownExcluded = null;
function renderExcluded(excluded, restorable) {
  const box = $("multi-excluded");
  box.hidden = excluded.length === 0;
  const key = JSON.stringify([excluded, restorable]);
  if (key === shownExcluded) {
    return;
  }
  shownExcluded = key;
  text($("multi-excluded-title"), $("multi-excluded-title-template").textContent.replace("{n}", String(excluded.length)));
  $("multi-excluded-list").replaceChildren(
    ...excluded.map((item, k) => {
      const li = make("li", "", null);
      const restore = make("button", "pilot-button pilot-button--secondary", $("multi-excluded-restore").textContent);
      restore.type = "button";
      restore.disabled = !restorable;
      restore.setAttribute("aria-label", $("multi-excluded-restore-template").textContent.replace("{name}", item.name));
      restore.addEventListener("click", () => act("restore_excluded", k));
      li.append(make("span", "", item.name), " ", restore);
      return li;
    }),
  );
}

// 行の先頭の「AI に送る」のチェックのセル。状態は描くたびに Python の値で入れる（drawSendChecks）。
// チェックのセルの中のクリックでは行を選ばない（行の選びとチェックは別の操作）
function sendCell(row) {
  const td = make("td", "pilot-check-cell", null);
  const check = make("input", "pilot-check", null);
  check.type = "checkbox";
  check.setAttribute("aria-label", `${texts["compare.multi.col.send"]}: ${row.name}`);
  check.title = texts["compare.multi.col.send"];
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
          // 操作の列（1 列目）は鍵から印にし、対象・変更前・変更後（3・4・5 列目）は折り返す（REQ-276）
          opMarkDecorator(1, [3, 4, 5], answer.all.op_keys),
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
  let index = 0;
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const value of cells(row)) {
      tr.append(make("td", "", value));
    }
    if (decorate) {
      decorate(tr, row, index);
    }
    fragment.append(tr);
    index += 1;
  }
  $(id).tBodies[0].replaceChildren(fragment);
}

// 切れて見えないセルの全文は、指を置くと出る（title。文字のまま）
function withTitles(tr) {
  for (const td of tr.cells) {
    td.title = td.textContent;
  }
}

// 操作の列の印（REQ-276）。鍵（行の op_key。言語によらない）から決め、言葉では見分けない。印は言語によらない
const OP_MARKS = { added: "+", removed: "−", changed: "~" };

// 構造化比較・全コマンド比較結果の行の飾り（REQ-276）。opCol は操作の列の番号、wrapCols は折り返す列の番号、opKeys は行と同じ順の鍵の並び。
// まず withTitles を呼ぶ（この時点の操作のセルは言葉なので、title に言葉が入る）。鍵が OP_MARKS にあれば、セルを印に替え、
// 言葉を title と aria-label に入れる。鍵が空・並びが無い・短いときは言葉のまま。折り返す列には pilot-cell-wrap を付ける
function opMarkDecorator(opCol, wrapCols, opKeys) {
  return (tr, _row, index) => {
    withTitles(tr);
    const key = Array.isArray(opKeys) ? opKeys[index] : undefined;
    const cell = tr.cells[opCol];
    if (typeof key === "string" && Object.hasOwn(OP_MARKS, key) && cell !== undefined) {
      const label = cell.textContent;
      cell.textContent = OP_MARKS[key];
      cell.title = label;
      cell.setAttribute("aria-label", label);
    }
    for (const col of wrapCols) {
      tr.cells[col]?.classList.add("pilot-cell-wrap");
    }
  };
}

// 対象と項目を 1 つの列にまとめた文字（両方あれば「対象 › 項目」。片方だけならその片方。両方空なら空）
function targetItemText(target, item) {
  if (target && item) {
    return `${target} › ${item}`;
  }
  return target || item || "";
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
  // 行は並べ替えで動くので、位置ではなく元の番号（data-index）で structuredRows から引く（REQ-369）
  for (const tr of rows) {
    const index = Number(tr.dataset.index);
    tr.hidden = structuredCategory !== null && structuredRows[index].category !== structuredCategory;
  }
  reportShown();
}

// --- 構造化比較の表の並べ替え（REQ-369。画面の中だけ。Python には渡さない） ---

// 今の画面の言語（<html lang>）の、数字の部分を数として比べる器。画面の言語は起動のあとで入るので、並べ替えるたびに取る
function structuredCollator() {
  const lang = document.documentElement.lang || "";
  if (!structuredCollators.has(lang)) {
    structuredCollators.set(lang, new Intl.Collator(lang || undefined, { numeric: true }));
  }
  return structuredCollators.get(lang);
}

// 並べ替えを元の順へ戻す（表の描き直しは呼び側）
function resetStructuredSort() {
  structuredSort.column = -1;
  structuredSort.ariaSort = "none";
}

// 列ごとの並べ替えの値（元の行の番号の順）。表の文字ではなく、Python が返した行から取る
// （操作の列のセルは印に替わっているため言葉の row.op を使う）
const STRUCTURED_SORT_VALUES = [
  (row) => row.op,
  (row) => row.category,
  (row) => targetItemText(row.target, row.item),
  (row) => row.before,
  (row) => row.after,
];

// 見出しの aria-sort を今の並べ替えに合わせ、行を並べ替える。行の要素は作り直さず、動かすだけ（選んだ行・印・フォーカスはそのまま付いてくる）。
// 隠れている行（カテゴリで絞った行）も一緒に並べるので、絞り込みを変えても並びは保たれる。元の順は data-index の順。
// 変更の行の文字の印は td の中にあるので、行を動かしても同じ行に付いたまま
function applyStructuredSort() {
  const table = $("structured-table");
  const heads = table.tHead.rows[0].cells;
  for (let column = 0; column < heads.length; column += 1) {
    heads[column].setAttribute("aria-sort", column === structuredSort.column ? structuredSort.ariaSort : "none");
  }
  const body = table.tBodies[0];
  const trs = Array.from(body.rows);
  if (trs.length === 0) {
    return;
  }
  const byIndex = new Map(trs.map((tr) => [Number(tr.dataset.index), tr]));
  const column = structuredSort.column;
  const values =
    column < 0 || structuredSort.ariaSort === "none"
      ? []
      : structuredRows.map((row) => STRUCTURED_SORT_VALUES[column](row));
  const order =
    values.length === 0
      ? structuredRows.map((_row, index) => index)
      : sortOrder(values, structuredSort.ariaSort, structuredCollator());
  const next = order.map((index) => byIndex.get(index)).filter((tr) => tr !== undefined);
  if (next.length !== trs.length || next.every((tr, at) => tr === trs[at])) {
    return; // すでにその並び（行を動かさない）
  }
  // フォーカスのある行は、動かした後に同じ行へ戻す（フォーカスのある要素を DOM で動かすと外れる窓があるため）。見出しにあるときは見出しのまま
  const active = document.activeElement;
  const focusedRow = active instanceof Element && body.contains(active) ? active.closest("tr") : null;
  const fragment = document.createDocumentFragment();
  for (const tr of next) {
    fragment.append(tr); // 引数に広げない（数万行でも引数の数の上限に当たらないように）
  }
  body.append(fragment);
  closeRightMenu(false);
  if (focusedRow !== null) {
    focusedRow.focus({ preventScroll: true });
  }
}

// 見出しを押す・Enter・Space で、その列の向きを進める（昇順 → 降順 → 元の順。別の列を押したら昇順から。ほかの列の並べ替えは外れる）。
// 列の境目（.pilot-col-resize）の上の事象では並べ替えない
function onStructuredHeadActivate(event) {
  const target = event.target instanceof Element ? event.target : null;
  if (target === null || target.closest(".pilot-col-resize") !== null) {
    return;
  }
  const th = target.closest("th");
  if (th === null) {
    return;
  }
  if (event.type === "keydown") {
    if (event.key !== "Enter" && event.key !== " ") {
      return;
    }
    event.preventDefault(); // Space で枠がスクロールしない
  }
  const column = th.cellIndex;
  if (column === structuredSort.column) {
    structuredSort.ariaSort = nextSortDirection(structuredSort.ariaSort);
    if (structuredSort.ariaSort === "none") {
      structuredSort.column = -1;
    }
  } else {
    structuredSort.column = column;
    structuredSort.ariaSort = "ascending";
  }
  applyStructuredSort();
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
  // 一括の比較が終わった（structuredSortFrom の番号以上の）結果を描くときは、構造化比較の表の並べ替えを元の順へ戻す（REQ-369）
  const sortReset = structuredSortFrom !== null && result.revision >= structuredSortFrom;
  if (sortReset) {
    structuredSortFrom = null;
    resetStructuredSort();
  }
  // 表の中身の番号（rows_revision）が前に描いたものと同じなら、AI の答えだけが変わった（「AI で解析」の確認・答え。REQ-264 §2）。
  // 表・絞り込み・選んでいる行・スクロールには触らず、AI の答えとキャラクターの場面だけを描き直す
  if (result.rows_revision === shown.rows) {
    if (sortReset) {
      applyStructuredSort(); // 表は同じだが、並べ替えは元の順へ戻す
    }
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
  // 1組を比べたとき、show が both なら表、raw なら単純比較のタブを開く（REQ-282。まとめて比べているときは null）。
  // 新しい結果を出したとき（open_tab_revision が進んだとき）だけ選ぶ。前後の行数を変えただけの描き直し（rows_revision だけ進む）では
  // 選び直さない。選んだあとで、押せるタブの確かめを通す
  if (result.open_tab_revision !== shown.openTab) {
    shown.openTab = result.open_tab_revision;
    // 新しい結果（比べ直した・保存の結果を開いた）なので、構造化比較の表の並べ替えは元の順へ戻す（REQ-369）
    resetStructuredSort();
    if (result.open_tab) {
      selectTab(result.open_tab);
      keepSelectableTab(result.fallback_tab);
    }
  }
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
    (row) => [row.op, row.category, targetItemText(row.target, row.item), row.before, row.after],
    // 操作の列（1 列目）は鍵から印にし、対象・項目、変更前、変更後（3・4・5 列目）は折り返す（REQ-276）
    opMarkDecorator(0, [2, 3, 4], result.structured_op_keys),
  );
  // 変更の行の違う文字に印を付ける（変更前は 4 列目、変更後は 5 列目。行を作ったときに 1 回だけ。REQ-226 §4.2・REQ-276）
  paintTableMarks("structured-table", result.structured_rows, result.structured_marks, 3, 4);
  // 構造化比較の行の右クリック（REQ-367）。行を描き直したので、前のメニューと知らせは閉じて隠す。
  // 行はフォーカスできる行（tabindex="-1"）にする。キーボードのメニューのキー・Shift+F10 は、フォーカスのある行で開く
  // 行には元の番号（Python が返した行の番号）を data-index に持たせる。並べ替えても、右クリック・絞り込み・並べ替えはこの番号で行を引く（REQ-369）
  let structuredIndex = 0;
  for (const tr of $("structured-table").tBodies[0].rows) {
    tr.tabIndex = -1;
    tr.dataset.index = String(structuredIndex);
    structuredIndex += 1;
  }
  structuredColumns = result.structured_columns || [];
  canFindSource = result.can_find_source === true;
  closeRightMenu(false);
  hideStructuredNote();
  // 行を作り直したら、同じカテゴリがあれば同じ絞り込みをかける（無ければ「すべて」に戻す。ADR-0155 決定1）
  structuredRows = result.structured_rows;
  drawChips(result.categories, result.categories_all);
  // 選んだ並べ替えを新しい表にかけ直す（元の順のときも aria-sort を描き直すため呼ぶ。一括の比較が終わったときは、関数の先頭で元の順へ戻してある）
  applyStructuredSort();
  text($("raw-note"), result.raw_note);
  // 無視した行・同じとみなした行の数のボタン（REQ-227 §11.4）。結果を描き直したら押していない状態に戻す
  const setAside = $("raw-set-aside");
  setAside.hidden = result.raw_set_aside === null;
  text(setAside, result.raw_set_aside);
  rawSetAside = new Set(result.raw_set_aside_rows);
  rawMarkLabels = result.raw_mark_labels || {};
  rawMoveLabels = new Map((result.raw_move_labels || []).map((item) => [item.index, item.text]));
  closeRawMenu(false); // 行を描き直すので、右クリックのメニューは閉じる（REQ-360）
  hideRawRuleNote(); // 足せなかった知らせも、描き直したら隠す（REQ-366 §2〜§4）
  rawLineRules = new Map((result.raw_line_rules || []).map((item) => [item.index, item]));
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

// --- 単純比較のタブで選んだ文字を、右クリックから「AI に式を頼む…」へ渡す（REQ-360 / ADR-0240 決定3） ---
// 式の上限（Python の MAX_PATTERN_CHARS）と、貼る行の上限（Python の MAX_PASTED_LINES・MAX_PASTED_CHARS）。
// JavaScript の文字の長さは Python より大きく数えることがあり、超えない側に倒れる
const RAW_SELECTION_MAX_CHARS = 200;
const RULE_AI_MAX_LINES = 100;
const RULE_AI_MAX_CHARS = 20000;

// 選んだ範囲の端の要素が入っている「1 つの行の片側の文字」の箱。次のどちらかなら { index, side }、それ以外は null。
// ・#raw-table の本体の 1 つの行（data-index がある行）の、変更前の文字（3 列目）か変更後の文字（5 列目）のセル
// ・全文の表示（#raw-before・#raw-after）。行は今選んでいる行（rawSelected）
function rawBoxOf(node) {
  const element = node instanceof Element ? node : node === null ? null : node.parentElement;
  if (element === null) {
    return null;
  }
  const cell = element.closest("#raw-table tbody td");
  if (cell !== null) {
    const tr = cell.parentElement;
    const side = cell.cellIndex === 2 ? "before" : cell.cellIndex === 4 ? "after" : null;
    if (side === null || tr.dataset.index === undefined) {
      return null;
    }
    return { index: Number(tr.dataset.index), side };
  }
  const full = element.closest("#raw-before, #raw-after");
  if (full !== null && rawSelected >= 0) {
    return { index: rawSelected, side: full.id === "raw-before" ? "before" : "after" };
  }
  return null;
}

// 今の選択が、メニューを出してよいものなら { index, side, text, range }、そうでなければ null。
// 全部を満たすときだけ: 範囲が 1 つで閉じていない・文字が空でない（前後の空白だけでない）・200 字以下・改行を含まない・
// 範囲の始まりと終わりが同じ 1 つの行の片側の文字の中にある
function rawSelectionAt() {
  const selection = window.getSelection();
  if (selection === null || selection.rangeCount !== 1 || selection.isCollapsed) {
    return null;
  }
  // 文字は range.toString()（文字の節の元の中身）から取る。選択そのものの toString は画面に出ている文を返すので、
  // 表のセル（white-space: nowrap）では続く空白が 1 つにまとまり、行の元の文字と合わなくなる
  const range = selection.getRangeAt(0);
  const value = range.toString();
  if (value.trim() === "" || value.length > RAW_SELECTION_MAX_CHARS || /[\r\n]/.test(value)) {
    return null;
  }
  const start = rawBoxOf(range.startContainer);
  const end = rawBoxOf(range.endContainer);
  if (start === null || end === null || start.index !== end.index || start.side !== end.side) {
    return null;
  }
  if (rawRows[start.index] === undefined) {
    return null;
  }
  return { index: start.index, side: start.side, text: value, range };
}

// 小さな窓に入れる行。選んだ側のコマンドの出力の行（その側の行番号がある行を、行番号の順に）から、選んだ行を中心に
// 前の行と後ろの行を 1 行ずつ交互に足して、100 行・20,000 字（各行の長さの合計＋行の数−1）まで。
// 選んだ行が変更の行（相手の側の行があり、文字が違う）なら、相手の側のその行を選んだ行のすぐ後に入れる（上限に入るときだけ）。
// 片方が尽きた・次の行が入らないときは、もう片方だけ続ける。選んだ行そのものが上限を超えるときも、その行は入れる（Python が断る）
function ruleAiLines(pick) {
  const side = pick.side;
  const other = side === "before" ? "after" : "before";
  const items = [];
  rawRows.forEach((row, index) => {
    if (row[`${side}_no`] !== null) {
      items.push({ index, no: row[`${side}_no`], text: row[`${side}_text`] });
    }
  });
  items.sort((a, b) => a.no - b.no);
  const at = items.findIndex((item) => item.index === pick.index);
  const chosen = rawRows[pick.index];
  if (at < 0) {
    return [chosen[`${side}_text`]];
  }
  const head = []; // 前の行（近い行から遠い行の順）
  const tail = []; // 後ろの行（選んだ行のすぐ後の相手の側の行を、先頭に入れる）
  let count = 1; // 入れた行の数
  let size = chosen[`${side}_text`].length; // 各行の長さの合計＋行の数−1
  const fits = (line) => count < RULE_AI_MAX_LINES && size + line.length + 1 <= RULE_AI_MAX_CHARS;
  const add = (list, line) => {
    list.push(line);
    count += 1;
    size += line.length + 1;
  };
  if (chosen[`${other}_no`] !== null && chosen[`${other}_text`] !== chosen[`${side}_text`] && fits(chosen[`${other}_text`])) {
    add(tail, chosen[`${other}_text`]);
  }
  let before = at - 1; // 次に足す前の行（尽きたら -1）
  let after = at + 1; // 次に足す後ろの行（尽きたら items.length）
  let wantBefore = true;
  while (before >= 0 || after < items.length) {
    // 順番の側が尽きていれば、もう片方を使う
    const useBefore = wantBefore ? before >= 0 : after >= items.length;
    wantBefore = !wantBefore;
    const line = useBefore ? items[before].text : items[after].text;
    if (!fits(line)) {
      // この側は次の行が入らない。尽きたことにして、もう片方だけ続ける
      if (useBefore) {
        before = -1;
      } else {
        after = items.length;
      }
      continue;
    }
    if (useBefore) {
      add(head, line);
      before -= 1;
    } else {
      add(tail, line);
      after += 1;
    }
  }
  return [...head.reverse(), chosen[`${side}_text`], ...tail];
}

function rawMenuOpen() {
  return !$("raw-menu").hidden;
}

// メニューを閉じる。restore が真なら、開く前にあったフォーカスへ戻す（Esc のとき）
function closeRawMenu(restore) {
  if (!rawMenuOpen()) {
    return;
  }
  $("raw-menu").hidden = true;
  document.removeEventListener("pointerdown", onRawMenuOutside, true);
  document.removeEventListener("scroll", onRawMenuScroll, true);
  const back = rawMenuReturn;
  rawMenuPick = null;
  rawMenuReturn = null;
  if (restore && back !== null && typeof back.focus === "function") {
    back.focus();
  }
}

function onRawMenuOutside(event) {
  if (!$("raw-menu").contains(event.target)) {
    closeRawMenu(false);
  }
}

function onRawMenuScroll() {
  closeRawMenu(false);
}

// 右クリックした行（{index, side}）。右クリックしたセルで決める（REQ-366）。
// ・表の本文の 1 つの行（data-index がある行）: 変更前の番号か文字のセルなら "before"、変更後の番号か文字のセルなら "after"、印のセルは側が無い（null）
// ・全文の表示（#raw-before・#raw-after）: 今選んでいる行のその側
// ・キーボードのメニューのキー（座標が無く、表そのものにフォーカスがある）: 今選んでいる行。側は分からない（null）
// どれでもなければ null（折りたたみの行・表の外・行を選んでいないキーボード）
function rawRowAt(event) {
  const element = event.target instanceof Element ? event.target : null;
  if (element === null) {
    return null;
  }
  const cell = element.closest("#raw-table tbody td");
  if (cell !== null) {
    const tr = cell.parentElement;
    if (tr.dataset.index === undefined || rawRows[Number(tr.dataset.index)] === undefined) {
      return null;
    }
    const side = cell.cellIndex === 1 || cell.cellIndex === 2 ? "before" : cell.cellIndex === 3 || cell.cellIndex === 4 ? "after" : null;
    return { index: Number(tr.dataset.index), side };
  }
  const full = element.closest("#raw-before, #raw-after");
  if (full !== null && rawSelected >= 0) {
    return { index: rawSelected, side: full.id === "raw-before" ? "before" : "after" };
  }
  if (element.id === "raw-wrap" && rawSelected >= 0 && rawRows[rawSelected] !== undefined) {
    return { index: rawSelected, side: null };
  }
  return null;
}

// その行のその側の文字（無ければ null。その側の行番号が無い側）
function rawSideText(row) {
  if (row === null || row.side === null || rawRows[row.index] === undefined) {
    return null;
  }
  const item = rawRows[row.index];
  return item[`${row.side}_no`] === null ? null : item[`${row.side}_text`];
}

// 出す項目の id の集まり（REQ-360・REQ-366 §2〜§6）。式が 200 文字を超えるときは、その項目を出さない
function rawMenuAvailable(pick, row) {
  const ids = new Set();
  if (pick !== null) {
    ids.add("raw-menu-ai");
    if (ruleFits(escapeRegex(pick.text))) {
      ids.add("raw-menu-replace");
    }
    if (/[0-9]/.test(pick.text) && ruleFits(digitRule(pick.text))) {
      ids.add("raw-menu-digits");
    }
  }
  const line = rawSideText(row);
  if (line !== null && line.trim() !== "" && ruleFits(ignoreLineRule(line))) {
    ids.add("raw-menu-ignore");
  }
  if (row !== null && rawLineRules.has(row.index)) {
    ids.add("raw-menu-rules");
  }
  if (row !== null && rawRows[row.index].is_diff && view !== null && view.ai.can_ask_shown) {
    ids.add("raw-menu-ask");
  }
  return ids;
}

// 出している項目のボタン（上から順）
function rawMenuButtons() {
  return RAW_MENU_ITEMS.map((id) => $(id)).filter((button) => !button.hidden);
}

// メニューを (x, y)（窓の座標）に出す。窓の外へはみ出すときは中へ寄せる。選んだ内容は、押したときに選択が消えても使えるよう、ここで持つ。
// ids は出す項目。出さない項目は隠す
function openRawMenu(pick, row, ids, x, y) {
  closeRawMenu(false);
  closeRightMenu(false); // 構造化比較・一覧のメニューが開いていれば閉じる（REQ-367）
  hideRawRuleNote(); // 次にメニューを開いたときに、前の知らせを隠す（REQ-366）
  rawMenuPick = {
    index: pick !== null ? pick.index : row.index,
    side: pick !== null ? pick.side : row.side,
    text: pick !== null ? pick.text : null,
    row,
  };
  rawMenuReturn = document.activeElement;
  const menu = $("raw-menu");
  for (const id of RAW_MENU_ITEMS) {
    $(id).hidden = !ids.has(id);
  }
  menu.hidden = false;
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(0, Math.min(x, window.innerWidth - box.width))}px`;
  menu.style.top = `${Math.max(0, Math.min(y, window.innerHeight - box.height))}px`;
  document.addEventListener("pointerdown", onRawMenuOutside, true);
  document.addEventListener("scroll", onRawMenuScroll, true);
  rawMenuButtons()[0].focus();
}

// 右クリック（キーボードのメニューのキーも同じ contextmenu）。出す項目が 1 つも無ければ、何もしない（preventDefault しない。
// ブラウザの今のメニューのまま）。あるときだけ、ブラウザのメニューを止めて、窓の中のメニューを出す
function onRawContextMenu(event) {
  const pick = rawSelectionAt();
  const row = rawRowAt(event);
  const ids = rawMenuAvailable(pick, row);
  if (ids.size === 0) {
    return;
  }
  event.preventDefault();
  let x = event.clientX;
  let y = event.clientY;
  if (x === 0 && y === 0) {
    // キーボードから（座標が無い）。選んだ範囲の左下（選んでいなければ選んでいる行の左下）に出す
    const rect = pick !== null ? pick.range.getBoundingClientRect() : rawEls[row.index].getBoundingClientRect();
    x = rect.left;
    y = rect.bottom;
  }
  openRawMenu(pick, row, ids, x, y);
}

// 「選んだ文字を正規表現で消す…」。メニューを閉じてから、行・説明・種類・選んだ文字を入れて小さな窓を開く
function chooseRawMenu() {
  const pick = rawMenuPick;
  closeRawMenu(false);
  if (pick === null || pick.text === null || rawRows[pick.index] === undefined) {
    return;
  }
  openRuleAiWith({
    lines: ruleAiLines(pick).join("\n"),
    description: $("raw-menu-description").textContent,
    kind: "replace",
    selection: pick.text,
  });
}

// --- 構造化比較のタブ・コマンドの組の一覧の右クリック（REQ-367 / ADR-0244 決定 1・3） ---
// 開閉・矢印キー・Esc・外を押す・スクロールの動きは単純比較のメニューと同じ。#structured-menu と #multi-menu の 2 つで使う小さな共通の関数

function rightMenuOpen() {
  return rightMenu.menu !== null;
}

// 出している項目のボタン（上から順）
function rightMenuButtons() {
  return rightMenu.menu === null ? [] : Array.from(rightMenu.menu.querySelectorAll("button")).filter((button) => !button.hidden);
}

// メニューを閉じる。restore が真なら、開く前にあったフォーカスへ戻す（Esc・項目を押したとき）
function closeRightMenu(restore) {
  if (!rightMenuOpen()) {
    return;
  }
  rightMenu.menu.hidden = true;
  document.removeEventListener("pointerdown", onRightMenuOutside, true);
  document.removeEventListener("scroll", onRightMenuScroll, true);
  const back = rightMenu.back;
  rightMenu.menu = null;
  rightMenu.back = null;
  rightMenu.index = -1;
  if (restore && back !== null && typeof back.focus === "function") {
    back.focus();
  }
}

function onRightMenuOutside(event) {
  if (rightMenuOpen() && !rightMenu.menu.contains(event.target)) {
    closeRightMenu(false);
  }
}

function onRightMenuScroll() {
  closeRightMenu(false);
}

// メニューを (x, y)（窓の座標）に出す。窓の外へはみ出すときは中へ寄せる。index は右クリックした行の番号（押したときに使う）。
// 出す項目（hidden を外したボタン）は呼ぶ側が決めておく
function openRightMenu(menu, index, x, y) {
  closeRawMenu(false);
  closeRightMenu(false);
  rightMenu.menu = menu;
  rightMenu.back = document.activeElement;
  rightMenu.index = index;
  menu.hidden = false;
  const box = menu.getBoundingClientRect();
  menu.style.left = `${Math.max(0, Math.min(x, window.innerWidth - box.width))}px`;
  menu.style.top = `${Math.max(0, Math.min(y, window.innerHeight - box.height))}px`;
  document.addEventListener("pointerdown", onRightMenuOutside, true);
  document.addEventListener("scroll", onRightMenuScroll, true);
  rightMenuButtons()[0].focus();
}

// 右クリックした場所（座標が無いキーボードのメニューのキーは、行の左下）
function menuPosition(event, tr) {
  if (event.clientX === 0 && event.clientY === 0) {
    const rect = tr.getBoundingClientRect();
    return [rect.left, rect.bottom];
  }
  return [event.clientX, event.clientY];
}

function hideStructuredNote() {
  $("structured-menu-note").hidden = true;
  text($("structured-menu-note"), "");
}

function showStructuredNote(value) {
  text($("structured-menu-note"), value);
  $("structured-menu-note").hidden = false;
}

// 右クリックした構造化比較の行（tbody の tr）。キーボードのメニューのキー・Shift+F10（座標が無い）では、フォーカスのある行。
// 表の外枠（#structured-wrap）にだけフォーカスがあるときは、見えている最初の行（上下の矢印で行を移れる）。見える行が無ければ null
function structuredRowAt(event) {
  const element = event.target instanceof Element ? event.target : null;
  const tr = element === null ? null : element.closest("#structured-table tbody tr");
  if (tr !== null || event.clientX !== 0 || event.clientY !== 0) {
    return tr;
  }
  const active = document.activeElement;
  const focused = active instanceof Element ? active.closest("#structured-table tbody tr") : null;
  return focused !== null ? focused : visibleStructuredRows()[0] || null;
}

// カテゴリのチップで隠れていない構造化比較の行（上から順）
function visibleStructuredRows() {
  return Array.from($("structured-table").tBodies[0].rows).filter((tr) => !tr.hidden);
}

// 表（#structured-wrap）にフォーカスがあるとき、上下の矢印・Home・End で、見えている行の間をフォーカスが移る
function onStructuredKeydown(event) {
  if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) {
    return;
  }
  const rows = visibleStructuredRows();
  if (rows.length === 0) {
    return;
  }
  const at = rows.indexOf(document.activeElement);
  let next;
  if (event.key === "Home") {
    next = 0;
  } else if (event.key === "End") {
    next = rows.length - 1;
  } else if (at < 0) {
    next = event.key === "ArrowDown" ? 0 : rows.length - 1;
  } else {
    next = Math.max(0, Math.min(rows.length - 1, at + (event.key === "ArrowDown" ? 1 : -1)));
  }
  event.preventDefault();
  rows[next].focus();
}

// 構造化比較の行の右クリック。出す項目が 1 つも無ければ、何もしない（preventDefault しない。今の右クリックのまま）。
// ・「元の行を単純比較のタブで見る」: その行の上で、元の行を探せるとき（result.can_find_source。比較方式が構造化だけのときは出さない）
// ・「この列をこの窓だけ比べない」: show の表の変更の行の上で、変わった列ごと（result.structured_columns。追加・削除・config の行・見出しの上では出さない）
function onStructuredContextMenu(event) {
  const tr = structuredRowAt(event);
  if (tr === null) {
    return;
  }
  const index = Number(tr.dataset.index); // 並べ替えた後も元の行の番号（REQ-369）
  const columns = structuredColumns[index] || [];
  if (!canFindSource && columns.length === 0) {
    return;
  }
  event.preventDefault();
  hideStructuredNote(); // 次にメニューを開いたときに、前の知らせを隠す
  $("structured-menu-source").hidden = !canFindSource;
  const template = $("structured-menu-skip-template").textContent;
  $("structured-menu-columns").replaceChildren(
    ...columns.map((name) => {
      const button = make("button", "pilot-menu-item", template.replace("{column}", name));
      button.type = "button";
      button.setAttribute("role", "menuitem");
      button.addEventListener("click", () => chooseSkipColumn(name));
      return button;
    }),
  );
  const [x, y] = menuPosition(event, tr);
  openRightMenu($("structured-menu"), index, x, y);
}

// 「元の行を単純比較のタブで見る」。Python が単純比較の行から探した番号（structured_source）を受け、見つからなければ知らせる。
// 見つかったら単純比較のタブを開き、その行が差分のみ表示（か「無視した行」だけの表示）で隠れていれば、その表示を切ってから、選んで見える所まで動かす
async function chooseStructuredSource() {
  const index = rightMenu.index;
  closeRightMenu(true);
  if (view === null || index < 0) {
    return;
  }
  const answer = await call("structured_source", form(), index);
  if (!answer.ok) {
    text(statusMain, answer.message);
    return;
  }
  render(answer.view);
  const row = answer.source.row;
  if (row === null || rawEls[row] === undefined) {
    showStructuredNote($("structured-menu-not-found").textContent);
    return;
  }
  hideStructuredNote();
  selectTab("raw");
  if ($("raw-diff-only").checked && !rawEls[row].dataset.keep) {
    $("raw-diff-only").checked = false;
    applyDiffOnly();
  }
  if (setAsideOnly() && !rawEls[row].dataset.setAside) {
    applySetAside(false);
  }
  selectRaw(row, true);
  $("raw-wrap").focus();
}

// 「この列をこの窓だけ比べない: 列」。skip_column で足す（Python が重複・20 個までを決める）。足せなければ知らせを出す
async function chooseSkipColumn(name) {
  closeRightMenu(true);
  if (view === null) {
    return;
  }
  const answer = await call("skip_column", form(), name);
  await handle(answer);
  if (answer.ok && (answer.skip_column === "duplicate" || answer.skip_column === "full")) {
    showStructuredNote($(answer.skip_column === "duplicate" ? "structured-menu-skip-duplicate" : "structured-menu-skip-full").textContent);
  }
}

// コマンドの組の一覧の行の右クリック。外せるときだけ（view.multi.exclude と行ごとの exclude_rows。比較中・開いた保存の結果・最後の1行は出さない）。
// キーボードのメニューのキーでは、選んでいる行（view.multi.selected）
function onMultiContextMenu(event) {
  if (view === null || view.multi === null) {
    return;
  }
  const element = event.target instanceof Element ? event.target : null;
  const body = $("multi-table").tBodies[0];
  let tr = element === null ? null : element.closest("#multi-table tbody tr");
  if (tr === null && event.clientX === 0 && event.clientY === 0 && view.multi.selected >= 0) {
    tr = body.rows[view.multi.selected] || null;
  }
  if (tr === null) {
    return;
  }
  const index = tr.sectionRowIndex;
  if (view.multi.exclude !== true || (view.multi.exclude_rows || [])[index] !== true) {
    return;
  }
  event.preventDefault();
  const [x, y] = menuPosition(event, tr);
  openRightMenu($("multi-menu"), index, x, y);
}

// 「この区間を比べる対象から外す」。Python が外して一覧を作り直す（exclude_row）
function chooseMultiExclude() {
  const index = rightMenu.index;
  closeRightMenu(true);
  if (index >= 0) {
    act("exclude_row", index);
  }
}

// --- 右クリックから、ルールを足す・効いているルールを見る・AI に質問する（REQ-366 / ADR-0244 決定 1・2） ---

function hideRawRuleNote() {
  $("raw-rule-note").hidden = true;
  text($("raw-rule-note"), "");
}

// ルールを欄に足す（#rawopt-fields へ rawopt-rule-add を送る。足すのは raw_options.js の addRawRule で、結果は detail.result に返る）。
// 足せたら change が窓に届き、今の form_changed になる（自動の比較がオンなら比べ直す。保存はしない）。
// 同じ式が既にあれば足さず、満ちていれば足さない。どちらも欄が閉じていても分かるよう、#raw-rule-note にその文を出す
function addRuleFromMenu(kind, pattern, replacement) {
  const detail = { kind, pattern, replacement, result: null };
  $("rawopt-fields").dispatchEvent(new CustomEvent("rawopt-rule-add", { detail }));
  if (detail.result === "duplicate" || detail.result === "full") {
    text($("raw-rule-note"), $(detail.result === "duplicate" ? "rawopt-duplicate" : "rawopt-ai-full").textContent);
    $("raw-rule-note").hidden = false;
  } else {
    hideRawRuleNote();
  }
}

// 押したときの選んだ内容を取り出してメニューを閉じる。フォーカスは開く前の場所へ戻す
function takeRawMenuPick() {
  const pick = rawMenuPick;
  closeRawMenu(true);
  return pick;
}

// §2: 選んだ文字をそのまま置き換えのルールにする（式は re.escape と同じ逃がし方。置き換える文字は <値>）
function chooseRawReplace() {
  const pick = takeRawMenuPick();
  if (pick !== null && pick.text !== null) {
    addRuleFromMenu("replace", escapeRegex(pick.text), RAW_RULE_VALUE);
  }
}

// §3: 選んだ文字の数字を \d+ にして置き換えのルールにする
function chooseRawDigits() {
  const pick = takeRawMenuPick();
  if (pick !== null && pick.text !== null) {
    addRuleFromMenu("replace", digitRule(pick.text), RAW_RULE_VALUE);
  }
}

// §4: この行（右クリックした側の文字）を無視する行のルールにする
function chooseRawIgnore() {
  const pick = takeRawMenuPick();
  const line = pick === null ? null : rawSideText(pick.row);
  if (line !== null && line.trim() !== "") {
    addRuleFromMenu("ignore", ignoreLineRule(line), "");
  }
}

// 小さな窓を閉じたら、フォーカスを表に戻す
function closeLineRules() {
  const dialog = $("raw-line-rules");
  if (dialog.open) {
    dialog.close();
  }
}

// §5: この行に効いているルールを小さな窓に出す。側（変更前・変更後）ごとに、当たったルールの種類・式・置き換える文字。
// 文字は textContent だけで入れる（式・置き換える文字は利用者の文字）
function chooseRawRules() {
  const pick = takeRawMenuPick();
  const entry = pick === null ? undefined : rawLineRules.get(pick.row.index);
  if (entry === undefined) {
    return;
  }
  const body = $("raw-line-rules-body");
  body.replaceChildren(
    ...entry.sides.map((side) => {
      const section = document.createElement("section");
      section.append(make("h3", "pilot-dialog-subtitle", side.title));
      if (side.rules.length === 0) {
        section.append(make("p", "pilot-note", side.none));
        return section;
      }
      const list = document.createElement("ul");
      for (const rule of side.rules) {
        const item = document.createElement("li");
        item.append(make("span", "", rule.kind_text), " ", make("code", "pilot-mono", rule.pattern));
        if (rule.kind === "replace") {
          item.append(" → ", make("code", "pilot-mono", rule.replacement));
        }
        list.append(item);
      }
      section.append(list);
      return section;
    }),
  );
  $("raw-line-rules").showModal();
  $("raw-line-rules-close").focus();
}

// §6: この差分について AI に質問する。AI の欄を隠していれば先に出し、Python に対象の行を知らせる（shown_changed の 4 つ目）。
// 知らせた後、AI を別の窓で出していればその窓を前に出す。入力欄に決まった質問を入れて欄に移るのは ai_view.js（番号が変わったときだけ）。
// 送るのは利用者が送ったときだけ（ここでは送らない）
async function chooseRawAsk() {
  const pick = takeRawMenuPick();
  const item = pick === null ? undefined : rawRows[pick.row.index];
  if (item === undefined || !item.is_diff) {
    return;
  }
  if (view !== null && view.ai_hidden === true && !view.ai_detached) {
    await act("set_ai_hidden", false);
  }
  await act("shown_changed", "raw", null, { before_no: item.before_no, after_no: item.after_no });
  if (view !== null && view.ai_detached) {
    await handle(await call("open_ai_pane"));
  }
}

// --- 左右の面の境目（ADR-0148 決定2）。幅は要素の CSS 変数に入れる。動かし終えたら Python が窓の配置のファイル（ui-state.json）に覚える（REQ-370・REQ-374） ---

const split = $("split");
// 名前は handle にしない（後ろの関数 handle と同じ名前になり、モジュールが読み込めなくなるため）
const splitHandle = $("split-handle");
// 利用者が動かした後（開いたときに覚えた割合があればその値）の左の面の幅の割合（決めた幅 ÷ 全体の幅。ADR-0216 決定4）。
// 動かす前で覚えた割合も無いときは null で、CSS の初めの幅（72%）のまま。
// 窓の大きさが変わっても同じ割合で決め直す。最小の幅に詰めた値では上書きしない
let leftRatio = null;
let dragging = null;
let startSplitApplied = false;

// 開いたときの幅。Python が設定から読んだ割合（view.split_left。無い・範囲の外は null）を、最初の状態が届いたときに 1 回だけ入れる。
// 割合（%）で入れるので、AI の欄を隠して始めても、あとで出したときにその幅になる。最小の幅は CSS の min-width が守る
function applyStartSplit(value) {
  if (startSplitApplied) {
    return;
  }
  startSplitApplied = true;
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return;
  }
  leftRatio = value;
  split.style.setProperty("--pilot-split-left", `${value * 100}%`);
  splitHandle.setAttribute("aria-valuenow", String(Math.round(value * 100)));
}

// 動かし終えた割合を Python に渡して窓の配置のファイル（ui-state.json。REQ-374）に覚えさせる。AI を別の窓に出している間・まだ割合が無いときは何もしない
function rememberSplit() {
  if (view === null || view.ai_detached || leftRatio === null) {
    return;
  }
  act("remember_split", Math.round(leftRatio * 1000) / 1000);
}

// 左の面の幅を px で決める。両側とも PANE_MIN より狭くしない。境目が隠れている（縦に積んでいる）ときは何もしない（null を返す）。
// 決めた幅 ÷ 全体の幅を返す。覚えるのは利用者が動かしたときだけ（moveLeftWidth）。
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
    moveLeftWidth(dragging.width + event.clientX - dragging.x);
  }
});
for (const name of ["pointerup", "pointercancel"]) {
  splitHandle.addEventListener(name, (event) => {
    const wasDragging = dragging !== null;
    dragging = null;
    if (splitHandle.hasPointerCapture(event.pointerId)) {
      splitHandle.releasePointerCapture(event.pointerId);
    }
    if (wasDragging) {
      rememberSplit(); // ドラッグを離したときに覚える（REQ-370）
    }
  });
}
// 左右の矢印のキーで SPLIT_STEP px ずつ動かす
splitHandle.addEventListener("keydown", (event) => {
  if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
    event.preventDefault();
    moveLeftWidth(currentLeftWidth() + (event.key === "ArrowLeft" ? -SPLIT_STEP : SPLIT_STEP));
    rememberSplit(); // 矢印のキーで動かしたときも覚える（REQ-370）
  }
});
// 窓の大きさが変わったら、覚えた割合から今の全体の幅で幅を決め直し、最小の幅の中に詰める（割合は書き換えない）
window.addEventListener("resize", () => {
  if (leftRatio !== null) {
    setLeftWidth(leftRatio * (split.clientWidth - splitHandle.offsetWidth));
  }
});

// --- コマンドの組の一覧と比較の表の境目（REQ-224 / ADR-0163）。幅は #multi の CSS 変数に入れるだけで、保存しない ---

const resultPane = $("result-pane");
const multiHandle = $("multi-handle");
// 利用者が動かした後の一覧の幅の割合（決めた幅 ÷ 全体の幅。ADR-0216 決定4）。動かす前は null で、CSS の初めの幅（--pilot-split-width）のまま
let multiRatio = null;
let multiDragging = null;

// 一覧の幅を px で決める。一覧は MULTI_MIN、表の側は PANE_MIN より狭くしない（合計が足りなければ MULTI_MIN）。
// 境目が隠れている（まとめて比べていない・縦に積んでいる）ときは何もしない（null を返す）。決めた幅 ÷ 全体の幅を返す。
function setMultiWidth(px) {
  const total = resultPane.clientWidth - multiHandle.offsetWidth;
  if (multiHandle.offsetParent === null || total <= 0) {
    return null;
  }
  const width = Math.round(Math.min(Math.max(px, MULTI_MIN), Math.max(MULTI_MIN, total - PANE_MIN)));
  $("multi").style.setProperty("--pilot-split-width", `${width}px`);
  multiHandle.setAttribute("aria-valuenow", String(Math.round((width / total) * 100)));
  return width / total;
}

// 利用者がドラッグ・矢印のキーで動かした幅を決め、割合を覚える（境目が隠れているときは今の割合のまま）
function moveMultiWidth(px) {
  const ratio = setMultiWidth(px);
  if (ratio !== null) {
    multiRatio = ratio;
  }
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
    moveMultiWidth(multiDragging.width + event.clientX - multiDragging.x);
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
    moveMultiWidth(currentMultiWidth() + (event.key === "ArrowLeft" ? -SPLIT_STEP : SPLIT_STEP));
  }
});
// 窓の大きさが変わったら、覚えた割合から今の全体の幅で幅を決め直し、最小の幅の中に詰める（割合は書き換えない）
window.addEventListener("resize", () => {
  if (multiRatio !== null) {
    const multiWidth = multiRatio * (resultPane.clientWidth - multiHandle.offsetWidth);
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
  closeRawMenu(false); // タブを変えたら右クリックのメニューを閉じる（REQ-360）
  closeRightMenu(false); // 構造化比較・一覧のメニューも閉じる（REQ-367）
  for (const tab of TABS) {
    $(`tab-${tab}`).setAttribute("aria-selected", String(tab === name));
    $(`panel-${tab}`).hidden = tab !== name;
  }
  reportShown();
}

// 今表示している差分（タブと構造化比較の表のカテゴリの絞り込み）を Python に知らせる（REQ-312 §7 / ADR-0218 決定2）。
// 状態は Python が持つ。Python が最後に受けた値（view.ai.shown）と今の画面が違うときだけ shown_changed を呼ぶ。
// タブの押し下げ・チップの選択・チップが消えて「すべて」に戻る・Python がタブを移す・比べ直したのどれでも、
// 描いた後にここを通るので 1 回だけ届く。カテゴリは構造化比較のタブのときだけ送る。
// 同じ view のうちは同じ値を重ねて呼ばない（reportedShown に、送った値とその時の view の番号を持つ）
let reportedShown = null;

function currentShown() {
  const tab = TABS.find((name) => $(`tab-${name}`).getAttribute("aria-selected") === "true");
  if (tab === undefined) {
    return null;
  }
  return { tab, category: tab === "structured" ? structuredCategory : null };
}

function reportShown() {
  const now = currentShown();
  if (view === null || now === null) {
    return;
  }
  const known = view.ai.shown;
  if (known.tab === now.tab && known.category === now.category) {
    return;
  }
  const key = `${view.revision}:${now.tab}:${now.category}`;
  if (reportedShown === key) {
    return;
  }
  reportedShown = key;
  act("shown_changed", now.tab, now.category);
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
// 文字を選んだ右クリック（キーボードのメニューのキーも contextmenu）で、窓の中のメニューを出す（REQ-360 / ADR-0240 決定3）。
// 表（#raw-wrap。キーボードでフォーカスがあるのもここ）と、全文の表示（#raw-before・#raw-after）で受ける
for (const id of ["raw-wrap", "raw-before", "raw-after"]) {
  $(id).addEventListener("contextmenu", onRawContextMenu);
}
$("raw-menu-ai").addEventListener("click", chooseRawMenu); // 押す・Enter（button の既定の動き）
$("raw-menu-replace").addEventListener("click", chooseRawReplace);
$("raw-menu-digits").addEventListener("click", chooseRawDigits);
$("raw-menu-ignore").addEventListener("click", chooseRawIgnore);
$("raw-menu-rules").addEventListener("click", chooseRawRules);
$("raw-menu-ask").addEventListener("click", chooseRawAsk);
// 「この行に効いているルールを見る」の小さな窓。Esc（dialog の既定）か「閉じる」で閉じ、閉じたらフォーカスを表に戻す
$("raw-line-rules-close").addEventListener("click", closeLineRules);
$("raw-line-rules").addEventListener("close", () => $("raw-wrap").focus());
// Esc で閉じてフォーカスを戻す。上下の矢印・Home・End で項目を移る。キー押下の受け手は、メニューの中だけに付ける（document・window には付けない）
$("raw-menu").addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    event.preventDefault();
    closeRawMenu(true);
    return;
  }
  const buttons = rawMenuButtons();
  const at = buttons.indexOf(document.activeElement);
  let next = null;
  if (event.key === "ArrowDown") {
    next = buttons[(at + 1) % buttons.length];
  } else if (event.key === "ArrowUp") {
    next = buttons[(at - 1 + buttons.length) % buttons.length];
  } else if (event.key === "Home") {
    next = buttons[0];
  } else if (event.key === "End") {
    next = buttons[buttons.length - 1];
  }
  if (next !== null) {
    event.preventDefault();
    next.focus();
  }
});
// フォーカスがほかの要素へ出たら閉じる。relatedTarget が null のときは閉じない。WebKit（Mac の窓）では、button を押すと
// フォーカスが外れて空になり、押している途中（mousedown）で focusout が relatedTarget なしで起きる。ここで閉じると
// click が届かない。外を押したときは、開いている間だけ付ける pointerdown の受け手が閉じる
$("raw-menu").addEventListener("focusout", (event) => {
  if (event.relatedTarget !== null && !$("raw-menu").contains(event.relatedTarget)) {
    closeRawMenu(false);
  }
});
// 構造化比較のタブの行・コマンドの組の一覧の行の右クリック（REQ-367）。キーボードのメニューのキーも contextmenu。
// 開いたメニューの矢印・Home・End・Esc・フォーカスが外へ出たときは、単純比較のメニューと同じ動き（メニューの中だけに付ける）
$("structured-wrap").addEventListener("contextmenu", onStructuredContextMenu);
$("structured-wrap").addEventListener("keydown", onStructuredKeydown);
// 構造化比較の表の見出し（REQ-369）。押す・Enter・Space で並べ替える（見出しと colgroup は作り直さないので、起動時に 1 回だけ付ける）
for (const th of $("structured-table").tHead.rows[0].cells) {
  th.addEventListener("click", onStructuredHeadActivate);
  th.addEventListener("keydown", onStructuredHeadActivate);
}
$("multi-wrap").addEventListener("contextmenu", onMultiContextMenu);
$("structured-menu-source").addEventListener("click", chooseStructuredSource);
$("multi-menu-exclude").addEventListener("click", chooseMultiExclude);
for (const id of ["structured-menu", "multi-menu"]) {
  $(id).addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      closeRightMenu(true);
      return;
    }
    const buttons = rightMenuButtons();
    const at = buttons.indexOf(document.activeElement);
    let next = null;
    if (event.key === "ArrowDown") {
      next = buttons[(at + 1) % buttons.length];
    } else if (event.key === "ArrowUp") {
      next = buttons[(at - 1 + buttons.length) % buttons.length];
    } else if (event.key === "Home") {
      next = buttons[0];
    } else if (event.key === "End") {
      next = buttons[buttons.length - 1];
    }
    if (next !== null) {
      event.preventDefault();
      next.focus();
    }
  });
  // relatedTarget が null のときは閉じない（単純比較のメニューと同じ理由。外を押したときは pointerdown の受け手が閉じる）
  $(id).addEventListener("focusout", (event) => {
    if (event.relatedTarget !== null && !$(id).contains(event.relatedTarget)) {
      closeRightMenu(false);
    }
  });
}
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
// 帯の「いつも効く設定を読み込む」は「いつも効く設定に戻す」と同じ処理（REQ-361 §3）。帯は Python が消す
bind("raw-follow-load", async () => handle(await call("reset_raw_options")));
// 「いつも効く設定に足す」（REQ-361 §5b）。1 回目で Python が足す式の確認（raw_add_confirm）を返したら、dialog.js の confirm で聞き
// （本文は textContent。既定は「やめる」）、「足す」のときだけ confirmed=true で呼び直す。足すものが無いときは知らせだけ
bind("rawopt-add-defaults", async () => {
  if (view === null) {
    return;
  }
  const result = await call("add_raw_to_defaults", form(), false);
  if (result.ok && result.raw_add_confirm) {
    const ask = result.raw_add_confirm;
    render(result.view);
    if (await confirm(ask.title, ask.body, ask.yes, ask.no)) {
      await handle(await call("add_raw_to_defaults", form(), true));
    }
    return;
  }
  await handle(result);
});
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
// AI の欄を隠す「»」と出す「« AI」（ADR-0216 決定5）。隠した状態は Python が持ち、返した view で描き直す
bind("ai-hide", () => act("set_ai_hidden", true));
bind("ai-show", () => act("set_ai_hidden", false));
bind("cancel", async () => handle(await call("cancel")));
bind("open-log-view", async () => handle(await call("open_log_view")));
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
