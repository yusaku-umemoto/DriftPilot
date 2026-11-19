// 単純比較の右クリックのメニューで、選んだ文字・行から式を作る関数（REQ-366 §2〜§4）。DOM は読まない（compare.js が import する）。
//
// 式は JavaScript で作る（メニューを出す前に、式が 200 文字を超えないかを確かめるため）。逃がす規則は Python 3.14 の re.escape と同じ
// （tests/test_req366_right_click_raw.py が、印字できる ASCII の全部・タブ・全角の文字で node の結果と Python の結果を比べる）。
// 式の長さは Python の MAX_PATTERN_CHARS（200）と同じ上限。JavaScript の length は Python より大きく数えることがあり、超えない側に倒れる。

export const RULE_MAX_CHARS = 200;

// Python 3.14 の re.escape が逃がす文字: ()[]{}?*+-|^$\.&~# と、空白・タブ・改行（\n \r）・\v・\f
const ESCAPED = new Set("()[]{}?*+-|^$\\.&~# \t\n\r\v\f");

// Python の re.escape と同じに、式の特別な文字の前に \ を付ける
export function escapeRegex(value) {
  let out = "";
  for (const ch of value) {
    out += ESCAPED.has(ch) ? `\\${ch}` : ch;
  }
  return out;
}

// 逃がした文字の中の、続いた数字（0〜9）を \d+ にする。逃がしても数字は変わらないので、元の文字の数字がそのまま \d+ になる
export function digitRule(value) {
  return escapeRegex(value).replace(/[0-9]+/g, "\\d+");
}

// 行の文字（前後の空白を除く）から「無視する行」の式を作る。続いた数字は \d+、続いた空白（逃がされる空白）は \s+、
// ほかの文字は escapeRegex で逃がし、^\s* と \s*$ で囲む。「逃がした後に置き換える」と同じ結果になる
export function ignoreLineRule(line) {
  const body = line.trim();
  let rule = "";
  for (const part of body.match(/[0-9]+|[ \t\n\r\v\f]+|[^]/gu) ?? []) {
    if (/^[0-9]/.test(part)) {
      rule += "\\d+";
    } else if (/^[ \t\n\r\v\f]/.test(part)) {
      rule += "\\s+";
    } else {
      rule += escapeRegex(part);
    }
  }
  return `^\\s*${rule}\\s*$`;
}

// 式が欄に入れられる長さか（200 文字以下）
export function ruleFits(pattern) {
  return pattern.length <= RULE_MAX_CHARS;
}
