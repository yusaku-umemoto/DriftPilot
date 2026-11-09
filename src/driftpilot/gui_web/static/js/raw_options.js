// 単純比較の設定の欄（ADR-0166 / REQ-227 §11.2）。比較の窓（compare.html）と設定の窓（settings.html の「単純比較」のタブ）で使う。
//
// 外から使うのは readRawOptions（欄の値を Python に渡す形にする）と fillRawOptions（Python が返した値と例を欄に入れる）の 2 つだけ。
// 欄の id はどちらの窓も同じ（rawopt- で始まる）。式の正しさはここでは判断しない（Python が確かめて、誤りの文を返す）。
// 文字は textContent と value だけで入れる（HTML として扱わない）。例の文と式も Python から受け取る。
// 「例から足す…」・置き換えの行を足す・外すは、この部品の中で欄を組み替える。外したときと例から足したときは、
// 欄の外側（#rawopt-fields）へ change を送り、窓に知らせる（比較の窓は form_changed を呼ぶ。設定の窓は「保存」まで何もしない）。

import { make } from "./dom.js";

const $ = (id) => document.getElementById(id);
// 置き換えの組の数の上限（Python の MAX_PATTERNS と同じ。超えたら Python が形の誤りとして断る）
const MAX_REPLACEMENTS = 20;

let examples = { ignore: [], replace: [] }; // Python から受け取った例
let bound = false;

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

function bindOnce() {
  if (bound) {
    return;
  }
  bound = true;
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
