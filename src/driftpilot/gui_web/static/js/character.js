// キャラクター（Mirarin）の絵を出す部品（ADR-0174 / REQ-236）。比較の窓（compare.js）・一括比較の窓（batch.js）・AI の面（ai_view.js）が使う。
//
// 絵は HTML に <img ... class="pilot-character" hidden> で書いてあり、ここでは hidden を外す・付けるだけ（文字は入れない。style の属性も使わない）。
// 出すかどうかは Python が返す view の character（その窓を最初に描いたときの [gui] character）で決まる。偽なら絵は hidden のまま。
// 動く絵（.pilot-character--motion）と止まった絵（.pilot-character--still）は両方の hidden を外し、どちらを見せるかは
// pilot.css の prefers-reduced-motion の決まりに任せる。

// 起動時の絵を出しておく時間と、薄くして消す時間（ミリ秒。ADR-0174 決定2）
const SPLASH_SHOW_MS = 1200;
const SPLASH_FADE_MS = 300;
const REDUCED_MOTION = "(prefers-reduced-motion: reduce)";

let replays = 0; // 1 回だけ動く絵を最初から動かし直した回数（src に付ける番号）

function reducedMotion() {
  return typeof window.matchMedia === "function" && window.matchMedia(REDUCED_MOTION).matches;
}

// ids の絵の hidden を切り替える（on が真なら出す）。無い id は飛ばす
export function revealCharacters(ids, on) {
  for (const id of ids) {
    const img = document.getElementById(id);
    if (img) {
      img.hidden = !on;
    }
  }
}

// 1 回だけ動く絵（差分なし）を最初から動かす。同じ絵を読み直させるため、src に番号を付けて入れ直す（同じ窓のファイルのまま）
export function replayOnce(img) {
  if (!img) {
    return;
  }
  const base = img.getAttribute("src").split("?")[0];
  replays += 1;
  img.src = `${base}?replay=${replays}`;
}

// 起動時の絵（#splash）を出す。SPLASH_SHOW_MS の後に SPLASH_FADE_MS で薄くして消す。クリックかキーで、すぐ消す。
// 動きを減らす設定のときは薄くせずに消す。消えるまでは下の窓を操作できない（重ねた要素がクリックとキーを受ける）
export function playSplash(splash) {
  if (!splash) {
    return;
  }
  const img = splash.querySelector("img");
  let done = false;
  let timer = 0;
  const finish = () => {
    if (done) {
      return;
    }
    done = true;
    clearTimeout(timer);
    splash.removeEventListener("click", skip);
    document.removeEventListener("keydown", skip, true);
    splash.hidden = true;
    if (img) {
      img.hidden = true;
    }
  };
  const skip = (event) => {
    event.preventDefault();
    event.stopPropagation();
    finish();
  };
  splash.addEventListener("click", skip);
  document.addEventListener("keydown", skip, true);
  if (img) {
    img.hidden = false;
  }
  splash.hidden = false;
  timer = setTimeout(() => {
    if (done) {
      return;
    }
    if (reducedMotion() || typeof splash.animate !== "function") {
      finish();
      return;
    }
    const fade = splash.animate([{ opacity: 1 }, { opacity: 0 }], {
      duration: SPLASH_FADE_MS,
      easing: "ease-out",
      fill: "forwards",
    });
    fade.addEventListener("finish", () => {
      finish();
      fade.cancel(); // 次に出すことは無いが、薄くした決まりは残さない
    });
  }, SPLASH_SHOW_MS);
}
