// AI の答えを整えて描く（REQ-204 §3 / ADR-0146 決定2・決定4）。
//
// 答えは Python（llm_analyzer/answer_format.py）が決まった書き方だけを読み取ったブロックの一覧で受け取る。
// ★ 答えは信用しない入力なので、要素は createElement で作り、文字は textContent だけで入れる。
// 文字列を HTML として解釈する書き方は使わない（使わない書き方の一覧は docs/notes/pilot-ui-standard.md）。
// 属性も付けない（className だけ）。リンク・画像は作らない（[文](URL) や <img> は文字のまま届く）。

import { make } from "./dom.js";

const RED = "pilot-answer-red";

// 行の中の1つの区切り。コードは code、太字は strong（コードかつ太字は strong の中に code）、ほかは span。赤字は className。
function spanNode(span) {
  let node;
  if (span.code) {
    node = make("code", "", span.text);
    if (span.bold) {
      const strong = make("strong", "", null);
      strong.append(node);
      node = strong;
    }
  } else {
    node = make(span.bold ? "strong" : "span", "", span.text);
  }
  if (span.red) {
    node.className = RED;
  }
  return node;
}

// spans を el の中に入れて返す
function withSpans(el, spans) {
  for (const span of spans) {
    el.append(spanNode(span));
  }
  return el;
}

function tableNode(block) {
  const table = make("table", "", null);
  const thead = make("thead", "", null);
  const headRow = make("tr", "", null);
  for (const cell of block.head) {
    headRow.append(withSpans(make("th", "", null), cell));
  }
  thead.append(headRow);
  const tbody = make("tbody", "", null);
  for (const row of block.rows) {
    const tr = make("tr", "", null);
    for (const cell of row) {
      tr.append(withSpans(make("td", "", null), cell));
    }
    tbody.append(tr);
  }
  table.append(thead, tbody);
  return table;
}

function blockNode(block) {
  switch (block.type) {
    case "heading":
      return withSpans(make("h4", "", null), block.spans);
    case "list": {
      const ul = make("ul", "", null);
      for (const item of block.items) {
        ul.append(withSpans(make("li", "", null), item));
      }
      return ul;
    }
    case "table":
      return tableNode(block);
    case "code": {
      const pre = make("pre", "", null);
      pre.append(make("code", "", block.text));
      return pre;
    }
    default:
      return withSpans(make("p", "", null), block.spans || []);
  }
}

// container の中身を blocks で描き直す。blocks が null なら空にする。
export function renderAnswer(container, blocks) {
  const fragment = document.createDocumentFragment();
  for (const block of blocks || []) {
    fragment.append(blockNode(block));
  }
  container.replaceChildren(fragment);
}
