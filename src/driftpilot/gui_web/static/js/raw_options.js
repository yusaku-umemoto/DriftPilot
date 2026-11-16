// 単純比較の設定の欄（ADR-0166 / REQ-227 §11.2）。比較の窓（compare.html）と設定の窓（settings.html の「単純比較」のタブ）で使う。
//
// 外から使うのは readRawOptions（欄の値を Python に渡す形にする）と fillRawOptions（Python が返した値と例を欄に入れる）の 2 つだけ。
// 欄の id はどちらの窓も同じ（rawopt- で始まる）。式の正しさはここでは判断しない（Python が確かめて、誤りの文を返す）。
// 文字は textContent と value だけで入れる（HTML として扱わない）。例の文と式も Python から受け取る。
// 「例から足す…」・置き換えの行を足す・外すは、この部品の中で欄を組み替える。外したときと例から足したときは、
// 欄の外側（#rawopt-fields）へ change を送り、窓に知らせる（比較の窓は form_changed を呼ぶ。設定の窓は「保存」まで何もしない）。

// 「AI に式を頼む…」（ADR-0218 決定8 / REQ-314）も、この部品の中で動く（小さな窓 #rawopt-ai-dialog。段は入力・確認・待つ間・結果）。
// 送る・答えを読む・式を確かめるのは Python（ask_rule_ai・answer_rule_ai・close_rule_ai と rule_ai.done の知らせ）で、ここは描くだけ。
// 「欄に入れる」（addRawRule）は欄に足して欄の外側へ change を送るだけで、保存も比較もしない。

import { call, on } from "./bridge.js";
import { make, text } from "./dom.js";

const $ = (id) => document.getElementById(id);
// 置き換えの組の数の上限（Python の MAX_PATTERNS と同じ。超えたら Python が形の誤りとして断る）
const MAX_REPLACEMENTS = 20;
// 無視する行の式の数の上限（Python の MAX_PATTERNS と同じ）
const MAX_IGNORES = 20;

let examples = { ignore: [], replace: [] }; // Python から受け取った例
let bound = false;
// 「AI に式を頼む…」: 今の確認・送信の番号（無ければ null）・「欄に入れる」に使う値（式・置き換える文字・種類だけ）・
// 窓を開いた・閉じた回数（待っている間に閉じたとき、遅れて届いた答えを捨てるため）
let aiId = null;
let aiRule = null;
let aiRound = 0;
const AI_STAGES = ["input", "confirm", "waiting", "result"];

// 置き換えの表の 1 行（式・置き換える文字・「外す」）。
function replacementRow(pattern, replacement) {
  const tr = document.createElement("tr");
  const patternCell = document.createElement("td");
  const patternInput = make("input", "pilot-input pilot-mono pilot-wide", null);
  patternInput.type = "text";
  patternInput.spellcheck = false;
  patternInput.autocomplete = "off";
  patternInput.value = pattern;
  patternInput.dataset.part = "pattern";
  patternInput.setAttribute("aria-label", $("rawopt-replace-pattern-head").textContent);
  patternCell.append(patternInput);
  const textCell = document.createElement("td");
  const textInput = make("input", "pilot-input pilot-mono pilot-wide", null);
  textInput.type = "text";
  textInput.spellcheck = false;
  textInput.autocomplete = "off";
  textInput.value = replacement;
  textInput.dataset.part = "replacement";
  textInput.setAttribute("aria-label", $("rawopt-replace-text-head").textContent);
  textCell.append(textInput);
  const buttonCell = document.createElement("td");
  const remove = make("button", "pilot-button pilot-button--secondary", $("rawopt-remove-label").textContent);
  remove.type = "button";
  remove.dataset.action = "remove";
  buttonCell.append(remove);
  tr.append(patternCell, textCell, buttonCell);
  return tr;
}

function replaceBody() {
  return $("rawopt-replace").tBodies[0];
}

// 置き換えの行の数で「置き換えを足す」と例の選択を押せなくする（20 組まで）。
function updateAddable() {
  const full = replaceBody().rows.length >= MAX_REPLACEMENTS;
  $("rawopt-replace-add").disabled = full;
  $("rawopt-replace-example").disabled = full;
}

// 空白の違いが「空白の数を無視」「空白をすべて無視」の間だけ、字下げの注記を出す。
function updateSpaceNote() {
  const value = $("rawopt-space").value;
  $("rawopt-space-note").hidden = value !== "amount" && value !== "all";
}

// 欄の外側へ change を送る（窓が値を読み直す）。
function announce() {
  $("rawopt-fields").dispatchEvent(new Event("change", { bubbles: true }));
}

// 例の選択肢を作り直す（最初の「例から足す…」は HTML の項目のまま残す）。
function fillExamples(select, items) {
  const first = select.options[0];
  select.replaceChildren(
    first,
    ...items.map((item, index) => {
      const option = make("option", "", item.label);
      option.value = String(index);
      return option;
    }),
  );
  select.selectedIndex = 0;
}

// 無視する行の欄の、空白でない行の数（Python は行の両端の空白を除いて空の行を捨てる）。
function ignoreCount() {
  return $("rawopt-ignore")
    .value.split(/\r\n|\r|\n/)
    .filter((line) => line.trim() !== "").length;
}

// 式を欄に足す（「欄に入れる」）。置き換えの表に 1 行足すか、無視する行の欄の最後に 1 行足し、欄の外側へ change を送る。
// 同じ式が既に欄にあっても足す（判断は利用者）。欄が満ちていれば足さずに false。保存も比較もしない。
function addRawRule(kind, pattern, replacement) {
  if (kind === "replace") {
    if (replaceBody().rows.length >= MAX_REPLACEMENTS) {
      return false;
    }
    replaceBody().append(replacementRow(pattern, replacement));
    updateAddable();
  } else if (kind === "ignore") {
    if (ignoreCount() >= MAX_IGNORES) {
      return false;
    }
    const area = $("rawopt-ignore");
    const current = area.value.replace(/\n+$/, "");
    area.value = current === "" ? pattern : `${current}\n${pattern}`;
  } else {
    return false;
  }
  announce();
  return true;
}

function aiDialog() {
  return $("rawopt-ai-dialog");
}

function aiKind() {
  return document.querySelector('input[name="rawopt-ai-kind"]:checked').value;
}

function showAiStage(name) {
  for (const stage of AI_STAGES) {
    $(`rawopt-ai-${stage}`).hidden = stage !== name;
  }
}

// 満ちている種類（置き換えが 20 組・無視する行が 20 個）は選べない。両方満ちていれば「送る内容を確かめる…」も押せない。
function updateAiKinds() {
  const replaceFull = replaceBody().rows.length >= MAX_REPLACEMENTS;
  const ignoreFull = ignoreCount() >= MAX_IGNORES;
  const replace = $("rawopt-ai-kind-replace");
  const ignore = $("rawopt-ai-kind-ignore");
  replace.disabled = replaceFull;
  ignore.disabled = ignoreFull;
  if (replace.checked && replaceFull && !ignoreFull) {
    ignore.checked = true;
  } else if (ignore.checked && ignoreFull && !replaceFull) {
    replace.checked = true;
  }
  $("rawopt-ai-full").hidden = !replaceFull && !ignoreFull;
  $("rawopt-ai-check").disabled = replaceFull && ignoreFull;
}

function showAiRefused(message) {
  const band = $("rawopt-ai-refused");
  text(band, message);
  band.hidden = message === null || message === undefined || message === "";
}

function openAi() {
  aiRound += 1;
  aiId = null;
  aiRule = null;
  showAiRefused(null);
  showAiStage("input");
  updateAiKinds();
  aiDialog().showModal();
  $("rawopt-ai-lines").focus();
}

// 小さな窓を閉じる（Esc・「閉じる」・欄に入れた後）。Python に待っている確認と送信を捨てさせてから閉じ、貼った文字は消す。
async function closeAi() {
  aiRound += 1;
  const id = aiId;
  aiId = null;
  aiRule = null;
  try {
    await call("close_rule_ai", id);
  } catch (error) {
    // 呼べなくても窓は閉じる（Python は窓が閉じたときにも捨てる）
  }
  $("rawopt-ai-lines").value = "";
  $("rawopt-ai-description").value = "";
  $("rawopt-ai-prompt").value = "";
  if (aiDialog().open) {
    aiDialog().close();
  }
}

async function askAi() {
  const round = aiRound;
  showAiRefused(null);
  const result = await call("ask_rule_ai", $("rawopt-ai-lines").value, $("rawopt-ai-description").value, aiKind());
  const info = result.ok ? result.rule_ai : null;
  if (round !== aiRound) {
    // 待っている間に窓を閉じた。出てきた確認は送らずに捨てる
    if (info && info.confirm) {
      await call("close_rule_ai", info.confirm.id);
    }
    return;
  }
  if (!result.ok) {
    showAiRefused(result.message);
    return;
  }
  if (info === null) {
    return;
  }
  if (info.refused !== undefined) {
    showAiRefused(info.refused);
    return;
  }
  const confirm = info.confirm;
  aiId = confirm.id;
  text($("rawopt-ai-prompt-title"), confirm.prompt_title);
  $("rawopt-ai-prompt").value = confirm.prompt;
  text($("rawopt-ai-destination"), confirm.destination);
  text($("rawopt-ai-omitted"), confirm.omitted_text);
  text($("rawopt-ai-note"), confirm.note);
  text($("rawopt-ai-send"), confirm.send);
  text($("rawopt-ai-dont-send"), confirm.dont_send);
  showAiStage("confirm");
  $("rawopt-ai-dont-send").focus();
}

// 「送信する」。確認に出した文を 1 回だけ送る（答えは rule_ai.done の知らせで届く）。
async function sendAi() {
  const round = aiRound;
  const id = aiId;
  // 答えの知らせ（rule_ai.done）が返事より先に届いても受けられるよう、呼ぶ前に待つ間の段にする
  showAiStage("waiting");
  $("rawopt-ai-wait-close").focus();
  const result = await call("answer_rule_ai", id, true);
  if (round !== aiRound || $("rawopt-ai-waiting").hidden) {
    return; // 閉じた、または知らせが先に届いて結果の段になっている
  }
  if (!result.ok) {
    aiId = null;
    showAiResult({ ok: false, message: result.message });
    return;
  }
  if (result.rule_ai === null) {
    // 待っていた確認が無かった（古い番号）。送っていないので、入力の段に戻る
    aiId = null;
    showAiStage("input");
  }
}

// 「送信しない」。送らずに、入れた文字を残したまま入力の段に戻る。
async function dontSendAi() {
  const id = aiId;
  aiId = null;
  await call("answer_rule_ai", id, false);
  showAiStage("input");
  $("rawopt-ai-lines").focus();
}

// 答えの結果を描く。成功は式・置き換える文字・説明・当たった数・当たった行の前と後の表と「欄に入れる」「入れない」、
// 失敗は理由の文と「閉じる」だけ（どれも textContent）。
function showAiResult(result) {
  const ok = result.ok === true;
  const failure = $("rawopt-ai-failure");
  text(failure, ok ? "" : result.message);
  failure.hidden = ok;
  $("rawopt-ai-found").hidden = !ok;
  $("rawopt-ai-add").hidden = !ok;
  $("rawopt-ai-skip").hidden = !ok;
  $("rawopt-ai-result-close").hidden = ok;
  aiRule = null;
  if (ok) {
    aiRule = result.rule;
    text($("rawopt-ai-pattern"), result.pattern);
    const replacing = result.kind === "replace";
    $("rawopt-ai-replacement-label").hidden = !replacing;
    $("rawopt-ai-replacement").hidden = !replacing;
    text($("rawopt-ai-replacement"), replacing ? result.replacement : "");
    const hasNote = typeof result.note === "string" && result.note !== "";
    $("rawopt-ai-note-label").hidden = !hasNote;
    $("rawopt-ai-result-note").hidden = !hasNote;
    text($("rawopt-ai-result-note"), hasNote ? result.note : "");
    text($("rawopt-ai-count"), result.count);
    $("rawopt-ai-table").tBodies[0].replaceChildren(
      ...result.rows.map(([before, after]) => {
        const tr = document.createElement("tr");
        tr.append(make("td", "pilot-mono", before), make("td", "pilot-mono", after));
        return tr;
      }),
    );
  }
  showAiStage("result");
  (ok ? $("rawopt-ai-skip") : $("rawopt-ai-result-close")).focus();
}

// 「欄に入れる」。欄に足してから閉じる（保存も比較もしない）。欄が満ちていて足せなければ、閉じずに理由を出す。
async function addAiRule() {
  const rule = aiRule;
  if (rule === null) {
    return;
  }
  if (addRawRule(rule.kind, rule.pattern, rule.replacement)) {
    await closeAi();
    return;
  }
  showAiResult({ ok: false, message: $("rawopt-ai-full").textContent });
}

function bindAiOnce() {
  $("rawopt-ai").addEventListener("click", openAi);
  $("rawopt-ai-check").addEventListener("click", askAi);
  $("rawopt-ai-send").addEventListener("click", sendAi);
  $("rawopt-ai-dont-send").addEventListener("click", dontSendAi);
  $("rawopt-ai-add").addEventListener("click", addAiRule);
  for (const id of ["rawopt-ai-input-close", "rawopt-ai-wait-close", "rawopt-ai-skip", "rawopt-ai-result-close"]) {
    $(id).addEventListener("click", closeAi);
  }
  // Esc も、Python に捨てさせてから閉じる
  aiDialog().addEventListener("cancel", (event) => {
    event.preventDefault();
    closeAi();
  });
  // Python が知らせた答え。今の送信の番号と合い、待っている間だけ描く（閉じた後・古い番号は捨てる）
  on("rule_ai.done", (event) => {
    if (!aiDialog().open || aiId === null || event.id !== aiId || $("rawopt-ai-waiting").hidden) {
      return;
    }
    aiId = null;
    showAiResult(event.result);
  });
}

function bindOnce() {
  if (bound) {
    return;
  }
  bound = true;
  bindAiOnce();
  $("rawopt-ignore-example").addEventListener("change", (event) => {
    const select = event.target;
    const item = examples.ignore[Number(select.value)];
    select.selectedIndex = 0;
    if (item === undefined) {
      return;
    }
    const area = $("rawopt-ignore");
    const current = area.value.replace(/\n+$/, "");
    area.value = current === "" ? item.pattern : `${current}\n${item.pattern}`;
    // 選択の change は外側へ届くので、窓はここで足した後の値を読む
  });
  $("rawopt-replace-example").addEventListener("change", (event) => {
    const select = event.target;
    const item = examples.replace[Number(select.value)];
    select.selectedIndex = 0;
    if (item === undefined || replaceBody().rows.length >= MAX_REPLACEMENTS) {
      return;
    }
    replaceBody().append(replacementRow(item.pattern, item.replacement));
    updateAddable();
  });
  $("rawopt-replace-add").addEventListener("click", () => {
    if (replaceBody().rows.length >= MAX_REPLACEMENTS) {
      return;
    }
    const row = replacementRow("", "");
    replaceBody().append(row);
    updateAddable();
    row.querySelector("input").focus();
  });
  replaceBody().addEventListener("click", (event) => {
    const button = event.target.closest('button[data-action="remove"]');
    if (button === null) {
      return;
    }
    button.closest("tr").remove();
    updateAddable();
    announce();
  });
  $("rawopt-space").addEventListener("change", updateSpaceNote);
}

// 欄の値（Python の read_raw_form の形）。置き換えの行は空の行も含めて並びのまま渡す（空の式の組は Python が捨てる）。
// 前後の行数は Number(value) を送るだけで、範囲は判断しない（Python が 0〜20 を確かめて誤りの文を返す。REQ-228 §2）。
export function readRawOptions() {
  return {
    ignore_lines: $("rawopt-ignore").value,
    replacements: Array.from(replaceBody().rows).map((tr) => [
      tr.querySelector('input[data-part="pattern"]').value,
      tr.querySelector('input[data-part="replacement"]').value,
    ]),
    whitespace: $("rawopt-space").value,
    ignore_blank_lines: $("rawopt-blank").checked,
    ignore_case: $("rawopt-case").checked,
    context_lines: Number($("rawopt-context").value),
    detect_moves: $("rawopt-moves").checked,
    algorithm: $("rawopt-algorithm").value,
  };
}

// Python が返した欄の値と例を入れる（窓を開いたとき・「既定に戻す」・保存の後）。
export function fillRawOptions(values, items) {
  bindOnce();
  examples = items || { ignore: [], replace: [] };
  fillExamples($("rawopt-ignore-example"), examples.ignore);
  fillExamples($("rawopt-replace-example"), examples.replace);
  $("rawopt-ignore").value = values.ignore_lines;
  replaceBody().replaceChildren(...values.replacements.map(([pattern, replacement]) => replacementRow(pattern, replacement)));
  $("rawopt-space").value = values.whitespace;
  $("rawopt-blank").checked = values.ignore_blank_lines;
  $("rawopt-case").checked = values.ignore_case;
  $("rawopt-context").value = String(values.context_lines);
  $("rawopt-moves").checked = values.detect_moves;
  $("rawopt-algorithm").value = values.algorithm;
  updateAddable();
  updateSpaceNote();
}
