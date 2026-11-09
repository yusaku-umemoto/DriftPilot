#!/bin/zsh
# できたコマンド版（driftpilot-cli）の中身を確かめる（ADR-0036 決定6）。Mac 用。Windows は smoke_test.ps1。
#   packaging/smoke_test.sh <driftpilot-cli のパス>
# 通信しない。キーを読み書きしない。値はすべて架空（tests/fixtures）。
set -euo pipefail
CLI=$1
ROOT=${0:A:h:h}
FX="$ROOT/tests/fixtures"
fail() { echo "確かめ失敗: $*" >&2; exit 1; }

# 1. 自己診断（REQ-38 の selfcheck。無い版では飛ばす）
if "$CLI" selfcheck --help >/dev/null 2>&1; then
  "$CLI" selfcheck || fail "selfcheck"
else
  echo "（selfcheck が無い版なので飛ばします）"
fi

# 2. 比較（開発環境と同じ結果になること。2026-10-29 の値）
OUT=$("$CLI" compare "$FX/hier_config_check/running.txt" "$FX/hier_config_check/intended.txt" --format json || true)
echo "$OUT" | /usr/bin/python3 -c '
import json, sys
d = json.load(sys.stdin)
got = (d["status"], len(d["structured"]["diffs"]), len(d["raw"]["diffs"]))
assert got == ("difference", 4, 9), got
print("比較: OK", got)' || fail "比較の結果が違う"

# 3. FortiGate（D型）を同じもの同士で比べて差分が無いこと
"$CLI" compare "$FX/fortinet/running.txt" "$FX/fortinet/running.txt" --platform fortinet --format json \
  | /usr/bin/python3 -c 'import json, sys; s = json.load(sys.stdin)["status"]; assert s == "no_difference", s; print("FortiGate: OK")' \
  || fail "FortiGate"

# 4. 接続テストは、設定もキーも無い一時のホームで、送らずに終了コード 2 で終わること
TMPHOME=$(mktemp -d)
set +e
HOME="$TMPHOME" env -u DRIFTPILOT_LLM_API_KEY "$CLI" llm-check </dev/null >/dev/null 2>&1
CODE=$?
set -e
rm -rf "$TMPHOME"
[[ $CODE == 2 ]] || fail "llm-check の終了コードが $CODE（2 のはず）"
echo "llm-check: OK（送らずに 2）"

# 5. pywebview の画面の部品（REQ-103）。selfcheck の「pywebview <版>」の項目が OK であること。
#    gui_web と webview が読み込めて、static/ のファイルが入っていることになる。窓は開かない
if "$CLI" selfcheck --help >/dev/null 2>&1; then
  "$CLI" selfcheck --format json | /usr/bin/python3 -c '
import json, sys
items = [i for i in json.load(sys.stdin) if i["detail"].startswith("pywebview ")]
assert len(items) == 1 and items[0]["ok"], items
print("画面の部品（web）: OK", items[0]["detail"])' || fail "pywebview の画面の部品"
fi
echo "確かめ: すべて OK"
