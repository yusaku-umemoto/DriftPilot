// 単純比較の設定の欄（ADR-0166 / REQ-227 §11.2）。比較の窓（compare.html）と設定の窓（settings.html の「単純比較」のタブ）で使う。
//
// 外から使うのは readRawOptions（欄の値を Python に渡す形にする）・fillRawOptions（Python が返した値と例を欄に入れる）・
// openRuleAiWith（単純比較のタブの右クリックから、行・説明・選んだ文字を入れて「AI に式を頼む…」の小さな窓を開く。REQ-360）の 3 つだけ。
// 欄の id はどちらの窓も同じ（rawopt- で始まる）。式の正しさはここでは判断しない（Python が確かめて、誤りの文を返す）。
// 文字は textContent と value だけで入れる（HTML として扱わない）。例の文と式も Python から受け取る。
// 「例から足す…」・置き換えの行を足す・外すは、この部品の中で欄を組み替える。外したときと例から足したときは、
// 欄の外側（#rawopt-fields）へ change を送り、窓に知らせる（比較の窓は form_changed を呼ぶ。設定の窓は「保存」まで何もしない）。

// 「AI に式を頼む…」（ADR-0218 決定8 / REQ-314）も、この部品の中で動く（小さな窓 #rawopt-ai-dialog。段は入力・確認・待つ間・結果）。
// 送る・答えを読む・式を確かめるのは Python（ask_rule_ai・answer_rule_ai・close_rule_ai と rule_ai.done の知らせ）で、ここは描くだけ。
// 「欄に入れる」（addRawRule）は欄に足して欄の外側へ change を送るだけで、保存も比較もしない。
// 同じ式（置き換えは式と置き換える文字の組）が既に欄にあれば、例から足すときも「欄に入れる」ときも足さずに知らせる（ADR-0236 / REQ-353）。

// ルールごとの有効・無効（REQ-359 / ADR-0240 決定1）。置き換えの表の「有効」のチェックと、無視する行の無効の式の集まり（ignoreOff）が
// 持ち、readRawOptions の disabled で Python に渡す。式を書き換えたら新しいルールとして有効に戻す。比較の窓の「効いているルール」の
// 一覧（compare.js）は、欄の外側（#rawopt-fields）へ rawopt-rule-toggle を送って切り替える（export は足さない）。

import { call, on } from "./bridge.js";
import { make, text } from "./dom.js";

const $ = (id) => document.getElementById(id);
// 置き換えの組の数の上限（Python の MAX_PATTERNS と同じ。超えたら Python が形の誤りとして断る）
const MAX_REPLACEMENTS = 20;
// 無視する行の式の数の上限（Python の MAX_PATTERNS と同じ）
const MAX_IGNORES = 20;

let examples = { ignore: [], replace: [] }; // Python から受け取った例
let bound = false;
// 無効にした無視する行の式（REQ-359）。欄の文字の一部として持つ（置き換えの無効は、行の「有効」のチェックが持つ）。
// 欄に無くなった式は、入力のたびに捨てる（式を書き換えたら新しいルールで有効）
let ignoreOff = new Set();
// 「AI に式を頼む…」: 今の確認・送信の番号（無ければ null）・「欄に入れる」に使う値（式・置き換える文字・種類だけ）・
// 窓を開いた・閉じた回数（待っている間に閉じたとき、遅れて届いた答えを捨てるため）
let aiId = null;
let aiRule = null;
let aiRound = 0;
// 右クリックから開いたときの、選んだ文字（REQ-360）。「確認」で ask_rule_ai の 4 つ目の引数に渡す。閉じたら捨てる。無ければ null
let aiSelection = null;
const AI_STAGES = ["input", "confirm", "waiting", "result"];

// 置き換えの表の 1 行（有効のチェック・式・置き換える文字・「外す」）。enabled を省くと有効（REQ-359）。
function replacementRow(pattern, replacement, enabled = true) {
  const tr = document.createElement("tr");
  const enabledCell = document.createElement("td");
  const enabledInput = make("input", "pilot-check", null);
  enabledInput.type = "checkbox";
  enabledInput.checked = enabled;
  enabledInput.dataset.part = "enabled";
  enabledInput.setAttribute("aria-label", $("rawopt-replace-enabled-head").textContent);
  enabledCell.append(enabledInput);
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
  tr.append(enabledCell, patternCell, textCell, buttonCell);
  return tr;
}

// 無視する行の欄の、空でない行（両端の空白を除く。Python の欄の読み方と同じ）。
function ignoreLines() {
  return $("rawopt-ignore")
    .value.split(/\r\n|\r|\n/)
    .map((line) => line.trim())
    .filter((line) => line !== "");
}

// 欄に無くなった式を無効の集まりから捨て、残った数を返す（式を書き換えたら新しいルールで有効）。
function pruneIgnoreOff() {
  const present = new Set(ignoreLines());
  ignoreOff = new Set(Array.from(ignoreOff).filter((pattern) => present.has(pattern)));
  return ignoreOff.size;
}

// 無効にした無視する行の式があるときだけ、欄の下に注記を出す（文の雛形の {count} を数に直す）。
function updateIgnoreDisabled() {
  const count = pruneIgnoreOff();
  const note = $("rawopt-ignore-disabled");
  note.hidden = count === 0;
  text(note, count === 0 ? "" : $("rawopt-ignore-disabled-template").textContent.replace("{count}", String(count)));
}

// 単純比較のタブの「効いているルール」の一覧（compare.js）がチェックを切り替えたときの知らせ。欄の無効を切り替え、窓に change を送る。
function toggleRule(detail) {
  if (detail.kind === "ignore") {
    if (detail.enabled) {
      ignoreOff.delete(detail.pattern);
    } else {
      ignoreOff.add(detail.pattern);
    }
    updateIgnoreDisabled();
  } else if (detail.kind === "replace") {
    for (const tr of replaceBody().rows) {
      if (
        tr.querySelector('input[data-part="pattern"]').value === detail.pattern &&
        tr.querySelector('input[data-part="replacement"]').value === detail.replacement
      ) {
        tr.querySelector('input[data-part="enabled"]').checked = detail.enabled;
      }
    }
  } else {
    return;
  }
  announce();
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

// 足す式が、無視する行の欄に既にあるか（各行を trim() して、足す式を trim() したものと同じものがあるか。空の式は重複としない）。
function hasIgnore(pattern) {
  const wanted = pattern.trim();
  if (wanted === "") {
    return false;
  }
  return $("rawopt-ignore")
    .value.split(/\r\n|\r|\n/)
    .some((line) => line.trim() === wanted);
}

// 足す組が、置き換えの表に既にあるか（式と置き換える文字の両方がちょうど同じ組。式が空の組は比べない）。
function hasReplacement(pattern, replacement) {
  if (pattern === "") {
    return false;
  }
  return Array.from(replaceBody().rows).some(
    (tr) =>
      tr.querySelector('input[data-part="pattern"]').value === pattern &&
      tr.querySelector('input[data-part="replacement"]').value === replacement,
  );
}

// 「同じ式が既にあるため、足していません」の注記（#rawopt-duplicate）を出す・隠す。
function showDuplicateNote(shown) {
  $("rawopt-duplicate").hidden = !shown;
}

// 式を欄に足す（「欄に入れる」）。置き換えの表に 1 行足すか、無視する行の欄の最後に 1 行足し、欄の外側へ change を送る。
// 足したら "added"。足さないときは理由を返す: 種類が違う "kind"・欄が満ちている "full"・同じ式が既に欄にある "duplicate"
// （確かめる順は 種類 → 満ちている → 重複。ADR-0236）。保存も比較もしない。
function addRawRule(kind, pattern, replacement) {
  if (kind === "replace") {
    if (replaceBody().rows.length >= MAX_REPLACEMENTS) {
      return "full";
    }
    if (hasReplacement(pattern, replacement)) {
      return "duplicate";
    }
    replaceBody().append(replacementRow(pattern, replacement));
    updateAddable();
  } else if (kind === "ignore") {
    if (ignoreCount() >= MAX_IGNORES) {
      return "full";
    }
    if (hasIgnore(pattern)) {
      return "duplicate";
    }
    const area = $("rawopt-ignore");
    const current = area.value.replace(/\n+$/, "");
    area.value = current === "" ? pattern : `${current}\n${pattern}`;
  } else {
    return "kind";
  }
  announce();
  return "added";
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

// 選んだ文字（REQ-360）を持ち、小さな窓の入力の段の「消したい部分: …」に出す。null なら隠す。表示は textContent だけ。
function setAiSelection(value) {
  aiSelection = value;
  text($("rawopt-ai-selection-text"), value === null ? "" : value);
  $("rawopt-ai-selection").hidden = value === null;
}

// 小さな窓を開く（ボタンからも、右クリックの openRuleAiWith からも同じ。段・番号・確認と結果を捨てる・種類の確かめ・showModal）。
// 選んだ文字は呼ぶ側が setAiSelection で決める（ここでは触らない）
function openAiDialog() {
  aiRound += 1;
  aiId = null;
  aiRule = null;
  showAiRefused(null);
  showAiStage("input");
  updateAiKinds();
  aiDialog().showModal();
}

// 「AI に式を頼む…」のボタン。選んだ文字は無い（今までと同じ）
function openAi() {
  openAiDialog();
  setAiSelection(null);
  $("rawopt-ai-lines").focus();
}

// 小さな窓を閉じる（Esc・「閉じる」・欄に入れた後）。Python に待っている確認と送信を捨てさせてから閉じ、貼った文字は消す。
async function closeAi() {
  aiRound += 1;
  const id = aiId;
  aiId = null;
  aiRule = null;
  setAiSelection(null); // 窓を閉じたら、選んだ文字は捨てる（REQ-360）
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
  // 4 つ目は右クリックから開いたときの選んだ文字（ボタンから開いたときは null で、Python では None）
  const result = await call("ask_rule_ai", $("rawopt-ai-lines").value, $("rawopt-ai-description").value, aiKind(), aiSelection);
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
    return;
  }
  if (result.rule_ai.refused !== undefined) {
    // 確認の後に設定が変わった・設定を読めなかったため、送っていない（REQ-371）。貼った行と説明は残して入力の段に戻り、断りの帯に文を出す。
    // もう一度「確認」を押すと、今の設定で作った確認が出る
    aiId = null;
    showAiStage("input");
    showAiRefused(result.rule_ai.refused);
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

// 「欄に入れる」。欄に足してから閉じる（保存も比較もしない）。欄が満ちている・同じ式が既にあって足せなければ、閉じずに理由を出す。
async function addAiRule() {
  const rule = aiRule;
  if (rule === null) {
    return;
  }
  const added = addRawRule(rule.kind, rule.pattern, rule.replacement);
  if (added === "added") {
    await closeAi();
    return;
  }
  const reason = added === "duplicate" ? $("rawopt-ai-duplicate") : $("rawopt-ai-full");
  showAiResult({ ok: false, message: reason.textContent });
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
    if (hasIgnore(item.pattern)) {
      // 同じ式が既にある。欄は変えずに注記を出す（選択の change は外側へ届くが、欄は同じなので窓の動きは変わらない）
      showDuplicateNote(true);
      return;
    }
    const area = $("rawopt-ignore");
    const current = area.value.replace(/\n+$/, "");
    area.value = current === "" ? item.pattern : `${current}\n${item.pattern}`;
    showDuplicateNote(false);
    // 選択の change は外側へ届くので、窓はここで足した後の値を読む
  });
  $("rawopt-replace-example").addEventListener("change", (event) => {
    const select = event.target;
    const item = examples.replace[Number(select.value)];
    select.selectedIndex = 0;
    if (item === undefined || replaceBody().rows.length >= MAX_REPLACEMENTS) {
      return;
    }
    if (hasReplacement(item.pattern, item.replacement)) {
      showDuplicateNote(true);
      return;
    }
    replaceBody().append(replacementRow(item.pattern, item.replacement));
    updateAddable();
    showDuplicateNote(false);
  });
  // 欄が変わったら注記を隠す（欄に書く・外す・「欄に入れる」）。例の選択から届いた change は除く（出した注記がその場で消えるため）
  $("rawopt-fields").addEventListener("input", () => showDuplicateNote(false));
  $("rawopt-fields").addEventListener("change", (event) => {
    if (event.target.id !== "rawopt-ignore-example" && event.target.id !== "rawopt-replace-example") {
      showDuplicateNote(false);
    }
  });
  $("rawopt-replace-add").addEventListener("click", () => {
    if (replaceBody().rows.length >= MAX_REPLACEMENTS) {
      return;
    }
    const row = replacementRow("", "");
    replaceBody().append(row);
    updateAddable();
    showDuplicateNote(false);
    // 行の先頭は「有効」のチェック（REQ-359）。フォーカスは式の欄へ
    row.querySelector('input[data-part="pattern"]').focus();
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
  // 無効のルール（REQ-359）。式か置き換える文字を書き換えたら、その行は新しいルールとして有効に戻す（change より先の input で戻す）。
  // 無視する行の欄は、書き換えたときに無効の式を捨てて注記の数を直す。一覧のチェックは rawopt-rule-toggle で届く
  replaceBody().addEventListener("input", (event) => {
    const part = event.target.dataset ? event.target.dataset.part : undefined;
    if (part === "pattern" || part === "replacement") {
      event.target.closest("tr").querySelector('input[data-part="enabled"]').checked = true;
    }
  });
  $("rawopt-ignore").addEventListener("input", updateIgnoreDisabled);
  $("rawopt-fields").addEventListener("rawopt-rule-toggle", (event) => toggleRule(event.detail));
  // 単純比較のタブの右クリックから足すルール（REQ-366 §2〜§4）。今の addRawRule で欄に足し、結果（"added"・"full"・"duplicate"・"kind"）を
  // detail.result に返す。足せたら announce の change が窓に届く（保存はしない）。同じ式が既にあれば足さずに注記（#rawopt-duplicate）を出す
  $("rawopt-fields").addEventListener("rawopt-rule-add", (event) => {
    const { kind, pattern, replacement } = event.detail;
    const result = addRawRule(kind, pattern, replacement);
    showDuplicateNote(result === "duplicate");
    event.detail.result = result;
  });
}

// 欄の値（Python の read_raw_form の形）。置き換えの行は空の行も含めて並びのまま渡す（空の式の組は Python が捨てる）。
// 前後の行数は Number(value) を送るだけで、範囲は判断しない（Python が 0〜20 を確かめて誤りの文を返す。REQ-228 §2）。
// disabled（REQ-359）は無効のルール。無視する行は欄にある無効の式、置き換えは「有効」を切った行の [式, 置き換える文字]
// （欄に無いものは送らない。送っても Python が捨てる）
export function readRawOptions() {
  pruneIgnoreOff();
  return {
    ignore_lines: $("rawopt-ignore").value,
    replacements: Array.from(replaceBody().rows).map((tr) => [
      tr.querySelector('input[data-part="pattern"]').value,
      tr.querySelector('input[data-part="replacement"]').value,
    ]),
    disabled: {
      ignore_lines: Array.from(ignoreOff),
      replacements: Array.from(replaceBody().rows)
        .filter((tr) => !tr.querySelector('input[data-part="enabled"]').checked)
        .map((tr) => [
          tr.querySelector('input[data-part="pattern"]').value,
          tr.querySelector('input[data-part="replacement"]').value,
        ]),
    },
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
  // 無効のルール（REQ-359）。disabled が無い値（古い呼び出し）は全部有効
  const off = values.disabled || { ignore_lines: [], replacements: [] };
  ignoreOff = new Set(off.ignore_lines);
  const replaceOff = new Set(off.replacements.map(([pattern, replacement]) => JSON.stringify([pattern, replacement])));
  replaceBody().replaceChildren(
    ...values.replacements.map(([pattern, replacement]) =>
      replacementRow(pattern, replacement, !replaceOff.has(JSON.stringify([pattern, replacement]))),
    ),
  );
  updateIgnoreDisabled();
  $("rawopt-space").value = values.whitespace;
  $("rawopt-blank").checked = values.ignore_blank_lines;
  $("rawopt-case").checked = values.ignore_case;
  $("rawopt-context").value = String(values.context_lines);
  $("rawopt-moves").checked = values.detect_moves;
  $("rawopt-algorithm").value = values.algorithm;
  updateAddable();
  updateSpaceNote();
  showDuplicateNote(false);
}

// 「AI に式を頼む…」の小さな窓を、値を入れて開く（REQ-360 / ADR-0240 決定3）。ボタンから開くときと同じ開き方で、
// 行の欄・説明の欄・種類（replace か ignore）に値を入れ、選んだ文字は別に持って「消したい部分: …」に出す（確認で Python に渡す）。
// 送るのは「送る内容を確かめる…」から先で、ここでは送らない。窓が既に開いていれば何もしない。フォーカスは「送る内容を確かめる…」
export function openRuleAiWith({ lines, description, kind, selection }) {
  bindOnce();
  if (aiDialog().open) {
    return;
  }
  openAiDialog();
  $("rawopt-ai-lines").value = lines;
  $("rawopt-ai-description").value = description;
  $(kind === "ignore" ? "rawopt-ai-kind-ignore" : "rawopt-ai-kind-replace").checked = true;
  updateAiKinds(); // 置き換えが 20 組で満ちていれば、今の決まりで「行ごと比べない」に切り替わる
  setAiSelection(selection);
  $("rawopt-ai-check").focus();
}
