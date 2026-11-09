// AI の面を描く部品（ADR-0160 決定2 / REQ-220 §5.1）。比較の窓（compare.js）の右の面と、AI の窓（ai_pane.js）の両方が使う。
// REQ-206 で compare.js に書いた AI の面の部分（吹き出し・1つのボタン・Enter・コピー・選択肢・途中の答え・確認のダイアログ）を移した。
//
// ★ 受け取った文字は textContent だけで描く（dom.js の make・text と answer_view.js の renderAnswer）。HTML として解釈する書き方は使わない。
// 押せるか・送るか・確かめるかは Python が決める。ここで決めるのは1つのボタンの名前（Python が渡す材料と、欄の文字の有無だけで決める）。
// 打った文と質問の文は Python に渡すだけで、ここには残さない（窓を移ると消える。ADR-0160 決定5）。
//
// createAiView に渡すもの:
// - $: id で要素を取る関数
// - call(name, ...args): Python のメソッドを呼ぶ口。form（比較の設定の欄の値）は渡さない
//   （比較の窓は form が要るメソッドに form() を足す包みを渡し、AI の窓は Python が持つ form を使う）
// - act(name, ...args): call してから、その窓の handle（知らせを出して描き直す）に渡す口
// - on: 知らせの登録の口（受け取るだけ。"compare.changed"・"compare.partial" の登録は各ページが行う）
// - afterPreview(result): 確認のダイアログの答えを渡した後の返事を出す、その窓の handle
// 文はページが data-text・data-label で要素に入れたものを読む（吹き出しの名前は #ai-title、コピーの名前は #llm-copy の title）。
// 1つのページで1回だけ呼ぶ（状態はこのモジュールに持つ）。

import { make, text } from "./dom.js";
import { confirm, notice, panel } from "./dialog.js";
import { renderAnswer } from "./answer_view.js";
import { bubble, copyButton, copyIcon, copyText } from "./answer_copy.js";
import { revealCharacters } from "./character.js"; // キャラクターの絵（ADR-0174 / REQ-236）

let $ = null;
let call = null;
let act = null;
let afterPreview = null;
let question = null;

let view = null; // いま描いている状態
// 描いたやり取りの番号と数・欄を空にした番号・出した確認の番号・描いた結果の番号
const shown = {
  followUp: null,
  turns: 0,
  clear: null,
  preview: null,
  result: 0,
  apiError: null, // 描いた API のエラーの文の番号（ADR-0168 決定5）
};
let llmCopy = null; // 最初の答えのコピーの文（Python が渡した、赤字の印を外した答え）
// 届いたところまでの答え（ADR-0157 決定5〜7）。吹き出しの要素・描いた（か消した）番号・結果を読んだ後に消す印
let partialBubble = null;
let partialAvatar = null; // 途中の答えの吹き出しの上の顔と名前の行（キャラクターがオンのときだけ。ADR-0181 決定1）
let partialRevision = 0;
let partialDropAfterResult = false;
// AI が考えている内容（ADR-0169 決定4）。折りたたみの要素（1つを使い回す）・自動でたたんだか・
// 届いている途中の考えか（真の間はクルクルを隠す）・全部届いた後に描いた考えの番号（view.thinking.revision）
let thinkingBox = null;
let thinkingFolded = false;
let thinkingLive = false;
let thinkingRevision = null;
let aiPending = false; // AI の面のボタンの呼び出しを待っている間（Enter の二度押しで重ねて呼ばない）
// AI に送る間のクルクル（ADR-0164 決定5）。最後の view の ai_busy（{text} か null。出すかは Python が決める）
let aiBusy = null;
// キャラクターの絵を出すか（view.character。ADR-0174 決定1）。考え中の絵（スピナーの代わり）と警告の絵に使う
let characterOn = false;
const THINKING_IDS = ["character-ai-thinking", "character-ai-thinking-still"];

// 描くときに読む決まった文（ページが data-text・data-label で入れた文）
function answerLabel() {
  return $("ai-title").textContent;
}

function copyLabel() {
  return $("llm-copy").title;
}

// 答えの吹き出しの上の顔と名前の行（ADR-0181 決定1）。キャラクターがオンのときだけ呼ぶ。
// ページの #llm-avatar と同じ形を make（createElement と textContent）だけで作る。title と名前は、
// ページが data-title・data-text で #llm-avatar の中に入れた文を読む（顔の絵は文の代わりにしない。alt は空で読み上げない）。
function avatarRow() {
  const source = $("llm-avatar");
  const face = make("img", "pilot-avatar", null);
  face.src = "character/avatar.png";
  face.alt = "";
  face.setAttribute("aria-hidden", "true");
  face.title = source.querySelector(".pilot-avatar").title;
  const row = make("div", "pilot-avatar-row", null);
  row.append(face, make("span", "pilot-avatar-name", source.querySelector(".pilot-avatar-name").textContent));
  return row;
}

// 最初の答えの吹き出しの上の行（#llm-avatar）は、キャラクターがオンで、#llm-bubble が出ているときだけ出す。
function syncAvatar() {
  $("llm-avatar").hidden = !characterOn || $("llm-bubble").hidden;
}

function note(id, value) {
  $(id).hidden = value === null;
  text($(id), value);
}

// 選択の欄の選択肢を描く（並びが変わったときだけ作り直す）。names は文字の並び。
function renderChoices(select, names) {
  if (Array.from(select.options).map((option) => option.text).join("\n") === names.join("\n")) {
    return;
  }
  select.replaceChildren(
    ...names.map((name) => {
      const option = make("option", "", name);
      option.value = name;
      return option;
    }),
  );
}

// view の AI の面の部分を描く（確認のダイアログは番号が変わったときだけ出す）。
function render(next) {
  view = next;
  aiBusy = next.ai_busy;
  characterOn = next.character === true;
  // キャラクターがオンなら、空の面の案内を Mirarin の文に替える（ADR-0181 決定2。オフのときは触らない）
  if (characterOn) {
    text($("ai-empty"), $("ai-empty-character").textContent);
  }
  syncAvatar();
  renderFirstRequest(next.follow_up);
  renderFollowUp(next.follow_up);
  renderApiError(next.api_error ?? null);
  renderPartial(next);
  renderThinking(next);
  paintBusy();
  renderAi(next.ai);
  renderFullText(next.full_text);
  // まとめて比べたときも、全部の組をまとめた送る文の全文を見せる1件の確認（ADR-0152 決定3。組のチェックは無い）
  if (next.preview !== null && next.preview.id !== shown.preview) {
    shown.preview = next.preview.id;
    showPreview(next.preview);
  }
}

// --- AI の面（ADR-0148 決定4・5・8） ---
// 吹き出し（bubble）・コピーのボタン（copyButton。title と aria-label は「コピー」）・アイコン（copyIcon）・
// クリップボードへのコピー（copyText）は answer_copy.js（一括比較の窓と同じ部品。REQ-213）

// 最初の依頼（ADR-0179 決定1）。#ai-empty と #llm-bubble の間の右の吹き出し（#first-request。追加の質問と同じ形）に、
// Python が渡した文（follow_up.first_request）を textContent だけで（dom.js の text で）入れる。null（と答えが無いとき）は隠す。
// ★ 最初の解析の考えの折りたたみは #llm-bubble の直前、途中の吹き出しは #follow-up-turns の後に置くので、
// どちらもこの吹き出しより下になる（placeThinking・paintPartial の置き場所は変えない）。
function renderFirstRequest(followUp) {
  const value = followUp === null ? null : (followUp.first_request ?? null);
  $("first-request").hidden = value === null;
  text($("first-request-text"), value);
}

// AI の答えへの追加の質問（ADR-0081）。やり取りは番号が変わったときだけ描き直し、質問は右・回答は左の吹き出しで、
// 1本のスクロールに順に並べる（文字は textContent だけ）。増えたら下まで送る。
// 追加の質問の文は、答えが届いてやり取りが増えたときだけ欄から消す（失敗・中止・断られたときは欄に残す）。
function renderFollowUp(followUp) {
  if (followUp === null) {
    if (shown.followUp !== null) {
      $("follow-up-turns").replaceChildren();
    }
    shown.followUp = null;
    shown.turns = 0;
    note("follow-up-note", null);
    return;
  }
  if (followUp.revision !== shown.followUp) {
    shown.followUp = followUp.revision;
    const fragment = document.createDocumentFragment();
    for (const turn of followUp.turns) {
      if (turn.blocks) {
        // 回答は答えの欄と同じ形で描く。質問の文は今のまま文字で描く（REQ-204 §3）
        const answer = make("div", "pilot-answer", null);
        renderAnswer(answer, turn.blocks);
        const item = bubble("ai", turn.label);
        item.append(answer, copyButton(turn.copy, copyLabel()));
        // キャラクターがオンなら、回答の吹き出しの上に顔と名前の行を足す（ADR-0181 決定1。コピーの文は変えない）
        if (characterOn) {
          fragment.append(avatarRow());
        }
        fragment.append(item);
      } else {
        const item = bubble("me", turn.label);
        item.append(make("p", "pilot-lines", turn.text));
        fragment.append(item);
      }
    }
    $("follow-up-turns").replaceChildren(fragment);
    if (followUp.turns.length > shown.turns) {
      question.value = "";
      scrollChat();
    }
    shown.turns = followUp.turns.length;
  }
  note("follow-up-note", followUp.note);
}

// API が返したエラーの文の折りたたみ（ADR-0168 決定5）。null なら隠す。題・文・注記は textContent だけで入れる。
// 既定は閉じる。番号（revision）が変わったときだけ閉じ直す（利用者が開いたままなら、ほかの描き直しでは閉じない）。
// キャラクターがオンなら、文を出している間だけ文の左に警告の絵を出す（ADR-0174 決定2。#api-error-row を横に並べる印を付ける）。
function renderApiError(apiError) {
  const box = $("api-error");
  const warning = characterOn && apiError !== null;
  revealCharacters(["character-warning"], warning);
  if (warning) {
    $("api-error-row").dataset.character = "on";
  } else {
    delete $("api-error-row").dataset.character;
  }
  if (apiError === null) {
    box.hidden = true;
    box.open = false;
    shown.apiError = null;
    return;
  }
  text($("api-error-title"), apiError.title);
  text($("api-error-text"), apiError.text);
  text($("api-error-note"), apiError.note);
  if (apiError.revision !== shown.apiError) {
    shown.apiError = apiError.revision;
    box.open = false;
  }
  box.hidden = false;
}

// AI の面の答えの欄を一番下まで送る。
function scrollChat() {
  const chat = $("ai-chat");
  chat.scrollTop = chat.scrollHeight;
}

// --- 届いたところまでの答え（ADR-0157 決定5〜7） ---
// 吹き出しはやり取りの最後（追加の質問のやり取りの後ろ）の左に1つだけ置き、Python が読み取ったブロックを renderAnswer で描く。
// コピーのアイコンは付けない（全部届いた答えだけ）。中止したときは注記を吹き出しの下に文字で出す。

// 答えの欄のスクロールが、いちばん下から 40px 以内か（そうなら描いた後も下に送る）。
function chatAtBottom() {
  const chat = $("ai-chat");
  return chat.scrollHeight - chat.scrollTop - chat.clientHeight <= 40;
}

// thinking は考えの形（{text, cut, cut_note} か null。ADR-0169 決定4）。答えのブロックが空なら吹き出しは作らない
// （考えだけのとき。中止の注記を出すときだけは、注記のために作る）。
function paintPartial(blocks, stoppedNote, thinking) {
  const follow = chatAtBottom();
  const hasAnswer = Array.isArray(blocks) && blocks.length > 0;
  if (hasAnswer || stoppedNote) {
    if (partialBubble === null) {
      partialBubble = bubble("ai", answerLabel());
      $("follow-up-turns").after(partialBubble);
      // キャラクターがオンなら、吹き出しの直前に顔と名前の行を足す（ADR-0181 決定1。外すときは一緒に外す）
      if (characterOn) {
        partialAvatar = avatarRow();
        partialBubble.before(partialAvatar);
      }
    }
    const answer = make("div", "pilot-answer", null);
    renderAnswer(answer, blocks);
    const parts = [answer];
    if (stoppedNote) {
      parts.push(make("p", "pilot-note pilot-lines", stoppedNote));
    }
    partialBubble.replaceChildren(...parts);
  } else if (partialBubble !== null) {
    // 考えだけの知らせで、前の流れの吹き出しが残っていれば外す
    partialBubble.remove();
    partialBubble = null;
    dropPartialAvatar();
    partialDropAfterResult = false;
  }
  if (thinking) {
    thinkingLive = true;
    thinkingRevision = null;
    paintThinking(thinking);
    // 置き場所: 途中の答えの吹き出しの直前、無ければやり取りの直後（吹き出しが入る場所）
    placeThinking(partialBubble, partialBubble === null);
    // 答えのブロックが初めて空でなくなったら1回だけたたむ（その後は利用者の操作に任せる）
    if (hasAnswer && !thinkingFolded) {
      thinkingBox.open = false;
      thinkingFolded = true;
    }
  } else {
    dropThinking();
  }
  // 途中の答えが出たらクルクルは隠す（知らせは view を伴わないので、ここでも同じ決まりで描く）
  paintBusy();
  if (follow) {
    scrollChat();
  }
}

// --- AI が考えている内容（ADR-0169 決定4〜6） ---
// 灰色の折りたたみ（details.pilot-thinking）を1つだけ JS が作って使い回す。中は題（summary）・考えの文・切ったときの断り書き・注記。
// どれも textContent だけで入れる（整形しない。answer_blocks に通さない）。題と注記はページの隠した要素の文を読む。

function paintThinking(thinking) {
  if (thinkingBox === null) {
    thinkingBox = make("details", "pilot-thinking", null);
    thinkingBox.open = true; // 作ったときは開いて出す
    thinkingFolded = false;
  }
  const parts = [make("summary", "", $("ai-thinking-title").textContent), make("p", "pilot-lines", thinking.text)];
  if (thinking.cut && thinking.cut_note) {
    parts.push(make("p", "pilot-note", thinking.cut_note));
  }
  parts.push(make("p", "pilot-note", $("ai-thinking-note").textContent));
  thinkingBox.replaceChildren(...parts);
}

// 折りたたみを anchor の直前に置く（移す必要があるときだけ動かす。開き具合はそのまま）。
// afterTurns が真なら、anchor が無くても #follow-up-turns の直後に置く。
// anchor（吹き出し）のすぐ上に顔と名前の行があれば、行のさらに上に置く（「顔の行 → 吹き出し」を離さない。ADR-0181 決定1）。
function placeThinking(anchor, afterTurns) {
  if (afterTurns) {
    const turns = $("follow-up-turns");
    if (turns.nextSibling !== thinkingBox) {
      turns.after(thinkingBox);
    }
    return;
  }
  const above = anchor ? anchor.previousElementSibling : null;
  if (above !== null && above.classList.contains("pilot-avatar-row")) {
    anchor = above;
  }
  if (anchor && thinkingBox.nextSibling !== anchor) {
    anchor.before(thinkingBox);
  }
}

function dropThinking() {
  thinkingLive = false;
  thinkingRevision = null;
  if (thinkingBox === null) {
    return;
  }
  thinkingBox.remove();
  thinkingBox = null;
  thinkingFolded = false;
}

// 全部の答えが届いた後の考え（view.thinking）。途中の答えがある間は知らせの側で描く。知らせより古い view は捨てる。
// 置き場所は、解析の答えなら #llm-bubble の直前、追加の質問なら最後の左の吹き出しの直前。
// 同じ番号なら開き具合を変えない。新しい番号で、まだ自動でたたんでいなければたたむ（答えはもう届いている）。
function renderThinking(next) {
  if (next.partial_revision < partialRevision || next.partial !== null) {
    return;
  }
  const thinking = next.thinking ?? null;
  if (thinking === null) {
    dropThinking();
    return;
  }
  thinkingLive = false;
  paintThinking(thinking);
  let anchor = $("llm-bubble");
  if (thinking.target === "follow_up") {
    const answers = $("follow-up-turns").querySelectorAll(".pilot-bubble--ai");
    anchor = answers.length > 0 ? answers[answers.length - 1] : null;
  }
  placeThinking(anchor, anchor === null);
  if (thinking.revision !== thinkingRevision) {
    thinkingRevision = thinking.revision;
    if (!thinkingFolded) {
      thinkingBox.open = false;
      thinkingFolded = true;
    }
  }
}

// 途中の答えの吹き出しの上の顔と名前の行を外す（吹き出しを外すときに一緒に呼ぶ）。
function dropPartialAvatar() {
  if (partialAvatar !== null) {
    partialAvatar.remove();
    partialAvatar = null;
  }
}

function dropPartial() {
  partialDropAfterResult = false;
  if (partialBubble === null) {
    return;
  }
  partialBubble.remove();
  partialBubble = null;
  dropPartialAvatar();
  paintBusy(); // 答えが無ければ案内に戻す（まだ送っている間ならクルクルに戻す）
}

// --- AI に送る間のクルクル（ADR-0164 決定5） ---
// 答えの欄の下（やり取りと途中の答えの後ろ）に、小さいクルクルとステータスの行と同じ文を出す。
// view.ai_busy が null のときと、途中の答えの吹き出しか届いている途中の考えがあるときは隠す。
// 隠れていたものを出したときだけ見える所まで送る。
function paintBusy() {
  const busy = $("ai-busy");
  const show = aiBusy !== null && partialBubble === null && !(thinkingLive && thinkingBox !== null);
  const appeared = show && busy.hidden;
  busy.hidden = !show;
  text($("ai-busy-text"), show ? aiBusy.text : "");
  // キャラクターがオンなら、スピナーを隠して考え中の絵を出す（文はそのまま。ADR-0174 決定2）
  $("ai-busy-spinner").hidden = characterOn;
  revealCharacters(THINKING_IDS, characterOn);
  syncEmpty();
  if (appeared) {
    busy.scrollIntoView({ block: "nearest" });
  }
}

// 答えが無いときの案内（#ai-empty）。答え・途中の答え・考えの折りたたみ・クルクルのどれかが出ている間は隠す。
function syncEmpty() {
  $("ai-empty").hidden =
    !$("llm-bubble").hidden || partialBubble !== null || thinkingBox !== null || !$("ai-busy").hidden;
}

// Python からの知らせ（"compare.partial"）。古い番号は捨てる。考え（thinking）も同じ知らせで届く（ADR-0169 決定4）。
function drawPartial(event) {
  if (event.revision <= partialRevision) {
    return;
  }
  partialRevision = event.revision;
  paintPartial(event.blocks, null, event.thinking ?? null);
}

// view の partial に合わせる。知らせより古い view は捨てる。null になったら吹き出しを消す
// （結果の番号が変わったときは、結果を読み終えてから消す。答えが一瞬消えないように）。
function renderPartial(next) {
  if (next.partial_revision <= partialRevision) {
    return;
  }
  partialRevision = next.partial_revision;
  if (next.partial === null) {
    if (next.result_revision !== shown.result) {
      partialDropAfterResult = true;
    } else {
      dropPartial();
    }
    return;
  }
  paintPartial(next.partial.blocks, next.partial.stopped ? next.partial.note : null, next.partial.thinking ?? null);
}

// AI の面の操作（ADR-0148 決定4・5）。選択肢は Python が渡した名前と使用中の位置で描き、描くたびに位置を合わせ直す。
function renderAi(ai) {
  for (const [id, names, index] of [
    ["ai-profile", ai.profiles, ai.profile],
    ["prompt-profile", ai.prompts, ai.prompt],
  ]) {
    const select = $(id);
    renderChoices(select, names);
    select.selectedIndex = index;
    select.disabled = !ai.can_choose || names.length === 0;
  }
  note("ai-note", ai.note);
  question.disabled = !ai.input_enabled;
  // 承認して送った答えが届いたら、解析の前に打った文を欄から消す（「送信しない」・中止・失敗では残す。REQ-206 §6）
  if (ai.clear !== shown.clear) {
    if (shown.clear !== null) {
      question.value = "";
    }
    shown.clear = ai.clear;
  }
  renderAsk();
}

// 「差分＋全文を送る」（ADR-0158 決定1・9・10）。チェック・押せるか・押せない理由（title）・案内は Python の値で描くだけ（判断はしない）。
function renderFullText(fullText) {
  const check = $("send-full-text");
  check.checked = fullText.checked;
  check.disabled = !fullText.enabled;
  // 押せない理由（fullText.reason）か、まとめての比較で全文を付ける組（fullText.target_note）を
  // チェックのすぐ下の注記と title に出す（ADR-0175 決定1・2）。文は Python が決め、ここでは textContent で入れるだけ
  const reason = fullText.reason !== null ? fullText.reason : fullText.target_note;
  note("send-full-text-reason", reason);
  check.title = reason === null ? "" : reason;
  check.parentElement.title = check.title;
  note("send-full-text-note", fullText.note);
}

// 1つのボタンの名前と押せるか（ADR-0148 決定5）。「質問する」は can_ask が真で欄に文字があるときだけ。
// ほかは「AI で解析」で、can_send_llm が真のときだけ押せる。判定の材料は Python が渡し、ここでは欄の文字の有無だけを見る。
function askMode() {
  return view.ai.can_ask && question.value.trim() !== "" ? "ask" : "send";
}

function renderAsk() {
  if (view === null) {
    return;
  }
  const ask = askMode() === "ask";
  text($("ai-ask"), ask ? view.ai.ask_label : view.ai.send_label);
  $("ai-ask").disabled = aiPending || (ask ? false : !view.ai.can_send_llm);
}

// ボタン（と Enter）の動き。「質問する」は ask_follow_up、「AI で解析」は send_llm に欄の文を渡す（確かめと送るかは Python が決める）。
async function aiAction() {
  if (view === null || $("ai-ask").disabled) {
    return;
  }
  aiPending = true;
  renderAsk();
  try {
    if (askMode() === "ask") {
      await askFollowUp(question.value, null);
    } else {
      await act("send_llm", question.value);
    }
  } finally {
    aiPending = false;
    renderAsk();
  }
}

// 追加の質問。送り先のプロファイルが変わっていれば Python が confirm を返すので、確かめて「はい」のときだけ番号を付けて呼び直す。
async function askFollowUp(value, confirmId) {
  const result = await call("ask_follow_up", value, confirmId);
  if (result.ok && result.confirm) {
    await afterPreview({ ok: true, view: result.view });
    const yes = await confirm(result.confirm.title, result.confirm.body, result.confirm.yes, result.confirm.no);
    if (yes) {
      await askFollowUp(value, result.confirm.id);
    }
    return;
  }
  await afterPreview(result);
}

// --- 結果の AI の答え ---

// AI の答えは AI の面の左の吹き出しに出す（頭の行は Python が除いた。ADR-0148 決定8）。答えは Python が読み取ったブロックを
// textContent だけで描く（REQ-204 §3）。答えが無いときは短い案内を出す（考えの折りたたみがあるときも隠す。syncEmpty）。
// result は result() か ai_result() の結果（llm の3つと番号）。
function drawResult(result) {
  shown.result = result.revision;
  $("llm-bubble").hidden = result.llm === null;
  syncAvatar(); // 顔と名前の行は吹き出しと一緒に出し入れする（ADR-0181 決定1）
  syncEmpty();
  renderAnswer($("llm-view"), result.llm === null ? null : result.llm_blocks);
  llmCopy = result.llm_copy;
  $("llm-copy").hidden = llmCopy === null;
  if (partialDropAfterResult) {
    dropPartial(); // 全部届いた答えを描いた後に、途中の答えの吹き出しを消す
  }
}

// --- 確認のダイアログ ---

function area(value, className) {
  const el = make("textarea", className, null);
  el.value = value;
  el.readOnly = true;
  el.spellcheck = false;
  return el;
}

// 送信前のプレビュー（ADR-0018 決定6）。既定のフォーカスと Esc は「送信しない」。答えを Python に渡すだけで、送るかは Python が決める。
async function showPreview(preview) {
  const parts = [];
  if (preview.full_text !== null) {
    // 変更後の全文も送るときは、先頭に目立つ帯で出す（ADR-0158 決定6）
    parts.push(make("p", "pilot-band pilot-band--warning", preview.full_text));
  }
  parts.push(make("p", "", preview.summary));
  if (preview.notes.length > 0) {
    parts.push(make("p", "pilot-lines", preview.notes.join("\n")));
  }
  parts.push(make("p", "", preview.payload_title), area(preview.payload, "pilot-input pilot-mono pilot-nowrap"));
  if (preview.unsent_shapes.length > 0) {
    parts.push(area(preview.unsent_shapes.join("\n"), "pilot-input pilot-mono pilot-nowrap"));
  }
  const yes = await panel(preview.title, parts, preview.send, preview.dont_send);
  await afterPreview(await call("answer_preview", yes));
}

// --- 作る ---

export function createAiView(options) {
  ({ $, call, act, afterPreview } = options);
  question = $("follow-up-question");
  $("llm-copy").append(copyIcon());
  // AI の面の1つのボタン（ADR-0148 決定5）。打った文と質問の文は Python に渡すだけ（確かめと送るかどうかは Python が決める）
  $("ai-ask").addEventListener("click", () => aiAction());
  // 「差分＋全文を送る」を変えたら Python に渡す（入れられるかは Python が決め、返した値で描き直す）
  $("send-full-text").addEventListener("change", () => act("set_full_text", $("send-full-text").checked));
  question.addEventListener("input", () => renderAsk());
  // Enter はボタンが押せるときだけボタンと同じ動き。日本語の変換を確定する Enter（isComposing・229）と Shift+Enter（改行）では送らない
  question.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) {
      return;
    }
    if (!$("ai-ask").disabled) {
      event.preventDefault();
      aiAction();
    }
  });
  // AI プロファイル・プロンプトの切り替え。使用中を変えるかどうかは Python が決める（位置と名前を渡す）
  for (const [id, method] of [
    ["ai-profile", "use_ai_profile"],
    ["prompt-profile", "use_prompt_profile"],
  ]) {
    $(id).addEventListener("change", () => {
      const select = $(id);
      const option = select.options[select.selectedIndex];
      act(method, select.selectedIndex, option ? option.text : "");
    });
  }
  $("llm-copy").addEventListener("click", () => {
    if (llmCopy !== null) {
      copyText(llmCopy, $("llm-copy"));
    }
  });
  return { render, drawResult, drawPartial };
}
