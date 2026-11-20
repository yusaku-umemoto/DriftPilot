// 「ログを見る」の窓の AI の欄（ADR-0251 決定3 / REQ-382）。対になる Python の側は driftpilot/gui_web/log_ask.py と log_view_api.py。
//
// 表示中の区間か、チェックした区間（複数の機器もまとめて）について AI に質問する。送る中身（伏せる処理・上限・指示文）の組み立てと、
// 送るかどうかは Python が決める。ここがするのは、質問の欄の文を Python に渡すことと、Python が返した送る前の確認を出して
// 「送信する」「送信しない」を Python に返すことと、会話（答え）・送信中・誤りを Python が返した状態から描くことだけ。
// 自動送信はしない（質問を送るたびに、確認を必ず出す）。
//
// ★ 文字は dom.js の make・text（textContent と value）だけで入れる。文字列を HTML として解釈する書き方は使わない。
//   答え（信用しない入力）は answer_view.js の renderAnswer（Python が読み取ったブロックを textContent だけで描く）で出す。
//   AI に送る口は ask_log（確認を出すだけ）と answer_log_ask（「送信する」を押したときだけ 1 回送る）の 2 つだけ。
//   ほかに呼ぶのは cancel_log_ask（中止）と clear_log_ask（会話を消す）だけ。
// 比較の窓の AI の面の部品を使う: 答え（answer_view.js）・吹き出しとコピー（answer_copy.js）・確認の窓（dialog.js の panel）。
// 比較の窓の AI の面を丸ごと動かす部品は使わない（比較の窓の状態の形と要素の id に結び付いているため）。送るキーの決まりは比較の窓と同じ
// （Shift+Enter で送る。Ctrl・Alt・⌘ を押しているときと、日本語の変換を確定する Enter では送らない）。

import { bubble, copyButton, copyText } from "./answer_copy.js";
import { renderAnswer } from "./answer_view.js";
import { call } from "./bridge.js";
import { panel } from "./dialog.js";
import { make, text } from "./dom.js";

const $ = (id) => document.getElementById(id);

// texts は messages.py から取った文（log_view.js が取って渡す）。show は Python が返した結果を窓に描く関数（log_view.js の handle）。
// 描く関数 render(ai) を返す。ai は状態の view.ai。
export function bindLogAsk({ texts, show }) {
  const question = $("ai-question");
  // いま描いているやり取りの番号（変わったときだけ描き直す）・いちばん新しい答えのコピーの文・確認を出して答えを待っている間
  let drawn = null;
  let lastCopy = "";
  let busy = false;
  let canAsk = false;

  function turnNode(turn) {
    const node = bubble(turn.kind === "answer" ? "ai" : "me", turn.label);
    if (turn.kind === "answer") {
      const view = make("div", "pilot-answer", null);
      renderAnswer(view, turn.blocks);
      node.append(view, copyButton(turn.copy, texts["compare.button.copy"]));
    } else {
      node.append(make("p", "pilot-lines", turn.text));
    }
    return node;
  }

  function drawTurns(turns) {
    $("ai-turns").replaceChildren(...turns.map(turnNode));
    const answers = turns.filter((turn) => turn.kind === "answer");
    lastCopy = answers.length === 0 ? "" : answers[answers.length - 1].copy;
    const chat = $("ai-chat");
    chat.scrollTop = chat.scrollHeight;
  }

  function line(id, value) {
    text($(id), value);
    $(id).hidden = value === null || value === undefined || value === "";
  }

  function renderButtons() {
    $("ai-send").disabled = !canAsk || busy || question.value.trim() === "";
  }

  function render(ai) {
    text($("ai-destination"), ai.destination);
    text($("ai-scope"), ai.scope);
    if (ai.revision !== drawn) {
      drawn = ai.revision;
      drawTurns(ai.turns);
    }
    $("ai-empty").hidden = ai.turns.length > 0 || ai.asking !== null;
    line("ai-note", ai.note);
    line("ai-error", ai.error);
    $("ai-busy").hidden = ai.asking === null;
    text($("ai-busy-text"), ai.asking);
    canAsk = ai.can_ask;
    $("ai-clear").disabled = !ai.can_clear;
    $("ai-copy").disabled = lastCopy === "";
    renderButtons();
  }

  // 送る前の確認。送る全文は読むだけの欄に value で入れる。安全なほう（送信しない）が既定
  async function confirmSend(confirm) {
    const prompt = make("textarea", "pilot-input pilot-mono pilot-wide pilot-nowrap", null);
    prompt.readOnly = true;
    prompt.rows = 14;
    prompt.spellcheck = false;
    prompt.value = confirm.prompt;
    const parts = [
      make("p", "pilot-lines", confirm.destination),
      make("p", "pilot-lines", confirm.sections_text),
      make("p", "pilot-lines", confirm.omitted_lines_text),
      make("p", "pilot-lines", confirm.omitted_sections_text),
      make("p", "pilot-note pilot-lines", confirm.note),
      make("p", "", confirm.prompt_title),
      prompt,
    ];
    const yes = await panel(confirm.title, parts, confirm.send, confirm.dont_send);
    prompt.value = "";
    return yes;
  }

  // 質問の送信（ボタンと Shift+Enter）。Python が確認を返したときだけ確認を出し、「送信する」のときだけ答えを Python に返す。
  async function send() {
    const value = question.value;
    if (busy || !canAsk || value.trim() === "") {
      return;
    }
    busy = true;
    renderButtons();
    try {
      const asked = await call("ask_log", value);
      await show(asked);
      if (!asked.ok || !asked.log_ask || !asked.log_ask.confirm) {
        return; // 断った（誤りの行に出る）・送れない
      }
      const confirm = asked.log_ask.confirm;
      const yes = await confirmSend(confirm);
      const answered = await call("answer_log_ask", confirm.id, yes);
      await show(answered);
      if (yes && answered.ok && answered.log_ask && answered.log_ask.asking) {
        question.value = ""; // 送り始めたときだけ欄を空にする（断られたときは残す）
      }
    } finally {
      busy = false;
      renderButtons();
    }
  }

  question.addEventListener("input", () => renderButtons());
  // Shift+Enter（Ctrl・Alt・⌘ なし）は送信のボタンと同じ動き。Enter だけは改行。日本語の変換を確定する Enter では、Shift を押していても送らない
  question.addEventListener("keydown", (event) => {
    if (
      event.key !== "Enter" ||
      !event.shiftKey ||
      event.ctrlKey ||
      event.altKey ||
      event.metaKey ||
      event.isComposing ||
      event.keyCode === 229
    ) {
      return;
    }
    event.preventDefault();
    send();
  });
  $("ai-send").addEventListener("click", () => send());
  $("ai-cancel").addEventListener("click", async () => show(await call("cancel_log_ask")));
  $("ai-clear").addEventListener("click", async () => show(await call("clear_log_ask")));
  $("ai-copy").addEventListener("click", () => {
    if (lastCopy !== "") {
      copyText(lastCopy, $("ai-copy"));
    }
  });
  return { render };
}
