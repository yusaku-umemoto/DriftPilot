// 設定の画面（REQ-104・REQ-100・REQ-101・REQ-102・REQ-116・REQ-214）。対になる Python の側は driftpilot/gui_web/settings_api.py。
//
// することは3つだけ: data-text の鍵の文を取って入れる・Python が返した状態（view）を描く・操作を Python に渡す。
// 押せるか・確かめるか・何を知らせるかは Python が決める（ADR-0074 決定3）。受け取った文字は textContent だけで描く。
// キーの入力欄は読んだらすぐに空にしてから送る。キーは変数にも残さない。

import { call, on } from "./bridge.js";
import { confirm, notice } from "./dialog.js";
import { choose } from "./dialog.js";
import { make, text } from "./dom.js";
import { bindTail, showTail } from "./path_tail.js"; // CLI のパスの欄は後ろを見せる（REQ-211 §4）
import { fillRawOptions, readRawOptions } from "./raw_options.js"; // 「単純比較」のタブの欄（REQ-227 / ADR-0166。比較の窓と同じ部品）

const $ = (id) => document.getElementById(id);
const status = $("status");
const keyInput = $("key-input");
// 窓のタブ（ADR-0154 / REQ-214）。id は settings-tab-○○、面は settings-panel-○○
// 「単純比較」（raw。REQ-227 §12）は「比較と自動の動き」と「設定ファイル」の間
const TABS = ["profiles", "prompt", "auto", "raw", "file"];

let view = null; // いま描いている状態
// 描いた状態の番号と、欄を入れ直した番号・キーの欄を空にした番号（変わったときだけ入れ直す）
const shown = {
  revision: 0,
  fillEditor: null,
  fillConfig: null,
  fillPrompt: null,
  fillPremise: null,
  clearKey: null,
  fillRaw: null,
};

// data-text の鍵の文を messages.py から取って入れる（HTML には文を書かない）。取れなければ false。
async function fillTexts() {
  const elements = Array.from(document.querySelectorAll("[data-text]"));
  const keys = Array.from(new Set(elements.map((el) => el.dataset.text)));
  const result = await call("messages", keys);
  if (!result.ok) {
    text(status, result.message);
    return false;
  }
  for (const el of elements) {
    text(el, result.messages[el.dataset.text]);
  }
  return true;
}

// 画面の欄の値（キーの入力欄は入れない）。
function form() {
  return {
    name: $("name").value,
    api_format: $("api-format").value,
    model: $("model").value,
    base_url: $("base-url").value,
    auth_header: $("auth-header").value,
    max_output_tokens: $("max-output-tokens").value,
    max_tokens_name: $("max-tokens-name").value,
    thinking: $("thinking").value,
    thinking_name: $("thinking-name").value,
    thinking_on: $("thinking-on").value,
    thinking_off: $("thinking-off").value,
    query: $("query").value,
    custom_url: $("custom-url").value,
    custom_auth: $("custom-auth").value,
    custom_headers: $("custom-headers").value,
    custom_body: $("custom-body").value,
    answer_path: $("answer-path").value,
    cli_path: $("cli-path").value,
    timeout_seconds: $("timeout-seconds").value,
    cli_effort: $("cli-effort").value,
    env_suffix: $("env-suffix").value,
    max_diffs: $("max-diffs").value,
    max_total_diffs: $("max-total-diffs").value,
    words: $("words").value,
    auto_compare: $("auto-compare").checked,
    auto_send_llm: $("auto-send-llm").checked,
    character: $("character").checked,
    prompt_name: $("prompt-name").value,
    prompt_template: $("prompt-template").value,
    premise_text: $("premise-text").value,
  };
}

// --- 描く ---

function band(id, value) {
  $(id).hidden = value === null || value === undefined || value === "";
  text($(`${id}-text`), value);
}

// 状態の印と接続テストの結果の行。state は ok（青系）・failed（黄系）・neutral（色なし）。
function mark(el, value, state) {
  el.classList.toggle("pilot-mark--ok", state === "ok");
  el.classList.toggle("pilot-mark--attention", state === "failed");
  text(el, value);
}

// 一覧の行を描く（接続のプロファイルと指示文で同じ形。REQ-113 §1 の帯とバッジ）。onSelect は行を押したときの操作。
function renderRows(tableId, rows, onSelect) {
  const body = $(tableId).tBodies[0];
  body.replaceChildren();
  for (const row of rows) {
    const tr = document.createElement("tr");
    tr.setAttribute("aria-selected", String(row.selected));
    // 使用中の行は左の緑の帯（CSS が data-in-use で描く）と、名前の右の「使用中」のバッジ（REQ-113 §1）
    tr.dataset.inUse = String(row.in_use);
    const td = document.createElement("td");
    // 1行目は名前（使用中は太字）とバッジ、2行目は形式と印（REQ-53 §3）。バッジの文字は色だけに頼らないために必ず出す
    const title = make("div", row.in_use ? "pilot-strong" : "", row.title);
    if (row.badge) {
      title.append(make("span", "pilot-badge pilot-badge--in-use", row.badge));
    }
    td.append(title, make("div", "pilot-subline", row.detail));
    tr.append(td);
    tr.addEventListener("click", () => onSelect(row.id));
    body.append(tr);
  }
}

function renderProfiles(rows) {
  renderRows("profile-list", rows, selectProfile);
}

// 「AI への指示文」の枠（REQ-116 §3）。最初から入っている3つ（readonly）は欄を読むだけにし、「保存」「削除…」を押せなくする。
function renderPrompt(next) {
  const prompt = next.prompt;
  renderRows("prompt-list", prompt.rows, selectPrompt);
  band("band-prompt-user-config", prompt.user_config_note);
  band("prompt-error", prompt.error);
  if (next.fill_prompt !== shown.fillPrompt) {
    shown.fillPrompt = next.fill_prompt;
    $("prompt-name").value = prompt.values.prompt_name;
    $("prompt-template").value = prompt.values.prompt_template;
  }
  $("prompt-name").readOnly = prompt.readonly;
  $("prompt-template").readOnly = prompt.readonly;
  $("prompt-builtin-note").hidden = !prompt.readonly || prompt.error !== null;
  $("save-prompt").disabled = !prompt.can_save;
  $("delete-prompt").disabled = !prompt.can_delete;
  text($("prompt-in-use-text"), prompt.in_use_text);
}

// 「毎回付ける前提」（ADR-0172 決定6 / REQ-234 §5）。案内の行は li に textContent で入れる。
// 文字数は表示だけ（判定は Python）。Python と同じく文字（コードポイント）で数える
function showPremiseCount() {
  if (view === null) {
    return;
  }
  const premise = view.premise;
  const count = [...$("premise-text").value].length;
  text(
    $("premise-count"),
    premise.count_format
      .replace("{count}", count.toLocaleString("en-US"))
      .replace("{max}", premise.max_chars.toLocaleString("en-US")),
  );
}

function renderPremise(next) {
  const premise = next.premise;
  band("premise-error", premise.error);
  if (next.fill_premise !== shown.fillPremise) {
    shown.fillPremise = next.fill_premise;
    $("premise-text").value = premise.text;
  }
  $("premise-notes").replaceChildren(
    ...premise.notes.map((line) => {
      const li = document.createElement("li");
      li.textContent = line;
      return li;
    }),
  );
  showPremiseCount();
}

// 選択の欄の選択肢を描く（値の並びが変わったときだけ作り直す）。作り直したら true。
function renderChoices(select, choices) {
  const values = Array.from(select.options).map((option) => option.value);
  if (values.join("\n") === choices.map((choice) => choice.value).join("\n")) {
    return false;
  }
  select.replaceChildren(
    ...choices.map((choice) => {
      const option = make("option", "", choice.label);
      option.value = choice.value;
      return option;
    }),
  );
  return true;
}

// 接続先の空欄に、選んでいる形式の既定の URL を薄く出す。CLI の形式は、モデルの空欄に例の文を薄く出す（REQ-113 §4）。
function showHint() {
  if (view === null) {
    return;
  }
  const format = $("api-format").value;
  const choice = view.format_choices.find((item) => item.value === format);
  $("base-url").placeholder = choice ? choice.hint : "";
  $("model").placeholder = view.model_hints[format] || "";
}

// 選んでいる形式で使う送り方の欄だけを出す（どの欄を出すかは Python の表 view.format_fields。REQ-100 §6.2）。
function showFields() {
  if (view === null) {
    return;
  }
  const names = view.format_fields[$("api-format").value] || [];
  for (const el of document.querySelectorAll("[data-field]")) {
    el.hidden = !names.includes(el.dataset.field);
  }
}

// Thinking の深さの選択肢を、選んでいる形式のものに入れ替える（ADR-0157 決定12）。CLI でない形式は「既定」だけ。
// 今の値が新しい選択肢に無ければ空（既定）に戻す。
function showEffortChoices() {
  if (view === null) {
    return;
  }
  const select = $("cli-effort");
  const current = select.value;
  const choices = view.cli_effort_choices[$("api-format").value] || [{ value: "", label: "" }];
  renderChoices(select, choices);
  select.value = choices.some((choice) => choice.value === current) ? current : "";
}

// 接続テストの応答の形の木（custom-http-json。REQ-101 §4.2）。Python が作った名前・型・道筋だけを描く。
// 道筋のある項目は押せる文字にし、押すと「回答の位置」の欄に道筋を入れるだけ（保存は利用者が「保存」を押す）。
function shapeItem(node) {
  const li = document.createElement("li");
  const label = node.name === "" ? node.type_label : `${node.name}: ${node.type_label}`;
  if (node.path === null) {
    li.append(make("span", "pilot-tree-label", label));
  } else {
    const button = make("button", "pilot-tree-item", label);
    button.type = "button";
    button.addEventListener("click", () => {
      $("answer-path").value = node.path;
    });
    li.append(button);
  }
  if (node.children.length > 0) {
    const list = document.createElement("ul");
    list.append(...node.children.map(shapeItem));
    li.append(list);
  }
  return li;
}

function renderShape(shape) {
  $("response-shape").hidden = shape === null;
  $("response-shape-tree").replaceChildren(...(shape === null ? [] : [shapeItem(shape)]));
}

function renderInfo(id, rows) {
  const list = $(id);
  list.replaceChildren();
  for (const [label, value] of rows) {
    list.append(make("dt", "", label), make("dd", "pilot-lines", value));
  }
}

function render(next) {
  if (next.revision < shown.revision) {
    return; // 後から届いた古い状態は捨てる
  }
  shown.revision = next.revision;
  view = next;

  mark($("status-mark"), next.status.text, next.status.ok ? "ok" : "failed");
  band("band-plaintext", next.bands.plaintext);
  band("band-workdir-key", next.bands.workdir_key);
  band("band-model", next.bands.model);
  band("band-limits", next.bands.limits);
  band("band-common-env", next.bands.common_env);
  band("error", next.error);
  // 保管庫を使えないときは、ボタンの代わりに理由を出す（押せないボタンは作らない）
  $("migrate-row").hidden = next.migrate === null;
  $("migrate").hidden = next.migrate === null || !next.migrate.available;
  $("migrate-reason").hidden = next.migrate === null || next.migrate.available;
  text($("migrate-reason"), next.migrate ? next.migrate.reason : "");

  renderProfiles(next.profiles);
  // 編集の枠の見出しの横は、保存した名前（打っている途中の名前ではない。REQ-112）
  text($("editor-name"), next.editor_name);
  renderChoices($("api-format"), next.format_choices);
  renderChoices($("auth-header"), next.auth_choices);
  renderChoices($("thinking"), next.thinking_choices);
  renderChoices($("custom-auth"), next.custom_auth_choices);
  // ひな形の選択は、最初に描くときだけ既定にする（後は利用者が選んだまま）
  if (renderChoices($("profile-template"), next.template_choices)) {
    $("profile-template").value = next.template_default;
  }
  if (next.fill_editor !== shown.fillEditor) {
    shown.fillEditor = next.fill_editor;
    const values = next.editor_values;
    $("name").value = values.name;
    $("api-format").value = values.api_format;
    $("model").value = values.model;
    $("base-url").value = values.base_url;
    $("auth-header").value = values.auth_header;
    $("max-output-tokens").value = values.max_output_tokens;
    $("max-tokens-name").value = values.max_tokens_name;
    $("thinking").value = values.thinking;
    $("thinking-name").value = values.thinking_name;
    $("thinking-on").value = values.thinking_on;
    $("thinking-off").value = values.thinking_off;
    $("query").value = values.query;
    $("custom-url").value = values.custom_url;
    $("custom-auth").value = values.custom_auth;
    $("custom-headers").value = values.custom_headers;
    $("custom-body").value = values.custom_body;
    $("answer-path").value = values.answer_path;
    $("cli-path").value = values.cli_path;
    showTail($("cli-path")); // 値を入れた後に後ろ（ファイル名の側）を見せる（REQ-211 §4）
    $("timeout-seconds").value = values.timeout_seconds;
    // 選択肢を形式のものにしてから値を入れる（無い値なら空に戻る）
    showEffortChoices();
    $("cli-effort").value = values.cli_effort;
    if ($("cli-effort").value !== values.cli_effort) {
      $("cli-effort").value = "";
    }
    $("env-suffix").value = values.env_suffix;
    // Thinking を ON にしているプロファイルは、送り方の細かい設定を開いて見せる（ADR-0157 決定12）
    if (values.thinking === "on") {
      $("wire-pane").open = true;
    }
  }
  showEffortChoices();
  showHint();
  showFields();
  text($("in-use-text"), next.in_use_text);

  // 環境変数の名前の固定の頭の部分（ADR-0077）。続きは欄に入れる
  text($("env-prefix"), next.key.env_prefix);
  text($("key-summary"), next.key.summary);
  $("key-sources").replaceChildren(
    ...next.key.sources.map((row) => make("div", row.in_use ? "pilot-lines pilot-strong" : "pilot-lines", row.text)),
  );
  // コマンドの行は途中で改行しない（REQ-37 §3）
  $("key-help").replaceChildren(
    ...next.key.help.map((line) => make("p", line.command ? "pilot-command" : "pilot-note pilot-lines", line.text)),
  );
  text($("store-note"), next.key.store_note);
  text($("profile-key-note"), next.key.profile_note);
  if (next.clear_key !== shown.clearKey) {
    shown.clearKey = next.clear_key;
    keyInput.value = "";
  }

  if (next.fill_config !== shown.fillConfig) {
    shown.fillConfig = next.fill_config;
    $("max-diffs").value = next.config.values.max_diffs;
    $("max-total-diffs").value = next.config.values.max_total_diffs;
    $("words").value = next.config.values.words;
    $("auto-compare").checked = next.config.values.auto_compare;
    $("auto-send-llm").checked = next.auto_send.checked;
    $("character").checked = next.character.checked;
  }
  text($("limits-note"), next.config.limits_note);
  text($("words-note"), next.config.words_note);
  text($("words-in-effect"), next.config.words_in_effect);
  renderPrompt(next);
  renderPremise(next);
  renderRaw(next.raw);

  renderInfo("diag-fingerprints", next.diagnostics.fingerprints);
  renderInfo("diag-versions", next.diagnostics.versions);
  text($("diag-location"), next.diagnostics.location);

  $("template").disabled = !next.can_write_template;
  $("check").disabled = next.sending; // 送っている間は押せない
  $("check-result").hidden = next.check === null;
  if (next.check !== null) {
    mark($("check-result"), next.check.text, next.check.state);
  }
  renderShape(next.response_shape);
}

// --- 操作を Python に渡す ---

// 返ってきたものを出す。知らせは出してから描く（wx と同じ順）。確かめは答えを2回目の呼び出しで渡す。
// 3択の確かめ（choices。REQ-112）は選んだボタンの値を、ほかの確かめは真偽値を渡す。
async function handle(result) {
  if (!result.ok) {
    text(status, result.message);
    return;
  }
  text(status, "");
  // 窓を出さない知らせ（「新規作成」の結果。REQ-199 §5）は窓の下の文に出す
  if (result.status_text) {
    text(status, result.status_text);
  }
  if (result.notice) {
    await notice(result.notice.title, result.notice.body, result.notice.ok);
  }
  render(result.view);
  // 「新規作成」の後は「名前」の欄にフォーカスを移し、全部を選ぶ（REQ-112）。隠れた面の欄には移せないので、先にそのタブを選ぶ（REQ-214 §4）
  if (result.focus === "name") {
    selectTab("profiles");
    $("name").focus();
    $("name").select();
  }
  // 指示文の「新規作成」の後も、名前の欄にフォーカスを移して全部を選ぶ（REQ-116 §3）
  if (result.focus === "prompt_name") {
    selectTab("prompt");
    $("prompt-name").focus();
    $("prompt-name").select();
  }
  if (result.confirm) {
    const asked = result.confirm;
    const answer = asked.choices
      ? await choose(asked.title, asked.body, asked.choices, asked.focus, asked.cancel)
      : await confirm(asked.title, asked.body, asked.yes, asked.no);
    await handle(await call("answer", form(), asked.action, asked.target, answer));
  }
  // 「保管庫に保存」の2段目: キーの入力欄を読んですぐ空にしてから送る（REQ-112）
  if (result.proceed === "save_key") {
    const key = keyInput.value;
    keyInput.value = "";
    await handle(await call("save_key", form(), key));
  }
}

async function act(name, ...args) {
  if (view === null) {
    return; // まだ最初の状態が届いていない
  }
  await handle(await call(name, form(), ...args));
}

function selectProfile(id) {
  if (view !== null && !view.profiles.some((row) => row.id === id && row.selected)) {
    act("select_profile", id);
  }
}

function selectPrompt(id) {
  if (view !== null && !view.prompt.rows.some((row) => row.id === id && row.selected)) {
    act("select_prompt_profile", id);
  }
}

// 「単純比較」のタブ（REQ-227 §12）。欄は番号が変わったときだけ入れ直す（窓を開いたとき・保存の後）。
// 誤りの文と、既定のファイルを読めないときの帯の文は文字として描く
function renderRaw(raw) {
  if (raw.fill !== shown.fillRaw) {
    shown.fillRaw = raw.fill;
    fillRawOptions(raw.values, raw.examples);
  }
  const problems = $("rawopt-problems");
  problems.hidden = raw.problems.length === 0;
  text(problems, raw.problems.join("\n"));
  $("raw-defaults-error").hidden = raw.error === null;
  text($("raw-defaults-error-text"), raw.error);
}

// 一覧の矢印キー（rows は今の一覧、select は選ぶ操作）
function moveIn(rows, select, step) {
  const index = rows.findIndex((row) => row.selected);
  const next = rows[index + step];
  if (next) {
    select(next.id);
  }
}

function moveSelection(step) {
  if (view === null) {
    return;
  }
  moveIn(view.profiles, selectProfile, step);
}

function bind(id, handler) {
  $(id).addEventListener("click", handler);
}

// タブの切り替え（ADR-0154 決定7）。compare.js と同じく aria-selected と面の hidden だけを変え、Python は呼ばない。
// 欄は消さずに隠すだけなので、form() はどのタブの欄も読む。開いたときは HTML の初めの値のまま profiles
function selectTab(name) {
  for (const tab of TABS) {
    $(`settings-tab-${tab}`).setAttribute("aria-selected", String(tab === name));
    $(`settings-panel-${tab}`).hidden = tab !== name;
  }
}

for (const tab of TABS) {
  bind(`settings-tab-${tab}`, () => selectTab(tab));
}

$("profile-list-wrap").addEventListener("keydown", (event) => {
  if (event.key === "ArrowDown" || event.key === "ArrowUp") {
    event.preventDefault();
    moveSelection(event.key === "ArrowDown" ? 1 : -1);
  }
});
$("prompt-list-wrap").addEventListener("keydown", (event) => {
  if (view !== null && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
    event.preventDefault();
    moveIn(view.prompt.rows, selectPrompt, event.key === "ArrowDown" ? 1 : -1);
  }
});
$("api-format").addEventListener("change", () => {
  showEffortChoices(); // 形式を変えたら Thinking の深さの選択肢を入れ替える（無くなった値は空に戻す）
  showHint();
  showFields();
});
bind("save-profile", () => act("save_profile"));
bind("add-profile", () => act("add_profile", $("profile-template").value));
bind("duplicate-profile", () => act("duplicate_profile"));
bind("delete-profile", () => act("delete_profile"));
bind("use", () => act("use_profile"));
// 1段目はキーを送らない（保管庫にキーがあれば置き換えの確かめ。送るのは handle の proceed のとき）
// 欄が空なら1段目を飛ばし、空の文字を送って Python の断りの文を出す（REQ-199 §5）
bind("save-key", () => (keyInput.value.trim() === "" ? act("save_key", "") : act("save_key", null)));
bind("delete-key", () => {
  keyInput.value = "";
  act("delete_key");
});
bind("check", () => act("check_connection"));
bind("export-custom", () => act("export_custom"));
bind("import-custom", () => act("import_custom"));
bind("choose-cli", () => act("choose_cli_path"));
bindTail($("cli-path"));
bind("migrate", () => act("migrate"));
// 「保存」は「比較と自動の動き」と「設定ファイル」の両方のタブにあり、どちらも同じ save_config を呼ぶ（REQ-214 §3）
bind("save-auto", () => act("save_config"));
bind("save-config", () => act("save_config"));
// 「単純比較」のタブの「保存」（REQ-227 §12）。raw-compare.json だけを書く（form() は渡さない。config.toml は書かない）
bind("save-raw", async () => {
  if (view !== null) {
    await handle(await call("save_raw_defaults", readRawOptions()));
  }
});
// 「AI への指示文」の枠の操作（REQ-116 §3）
bind("add-prompt", () => act("add_prompt_profile"));
bind("duplicate-prompt", () => act("duplicate_prompt_profile"));
bind("delete-prompt", () => act("delete_prompt_profile"));
bind("save-prompt", () => act("save_prompt_profile"));
bind("use-prompt", () => act("use_prompt_profile"));
// 「毎回付ける前提」の保存（ADR-0172 決定6）。文字数は打つたびに出し直す（表示だけ）
bind("save-premise", () => act("save_premise"));
$("premise-text").addEventListener("input", showPremiseCount);
bind("template", () => act("create_template"));
bind("open-folder", async () => handle(await call("open_folder")));
// プロファイルをまとめて書き出す・取り込む（REQ-119 §4）。ファイルの窓は Python が開き、確かめは handle の confirm で出す
bind("export-profiles", () => act("export_profiles"));
bind("import-profiles", () => act("import_profiles"));
// 「閉じる」。状態が届いていれば欄の値を渡し、前提の欄に保存していない変更があれば Python が確かめを返す
// （確かめは handle の confirm で出し、答えは answer に行く）。届いていなければ今までどおりすぐ閉じる
bind("close", () => {
  keyInput.value = "";
  if (view === null) {
    call("close");
    return;
  }
  act("close");
});
// 窓が前に来たら、設定ファイルが外で変わっていないかを Python に見てもらう（ADR-0071 決定4）
window.addEventListener("focus", () => act("focus"));
// 送っている最中の状態（Python から。"settings.view"）
on("settings.view", (event) => render(event.view));

if (await fillTexts()) {
  await handle(await call("state"));
}
