#!/bin/zsh
# DriftPilot の Mac 版を作り、dmg にする（ADR-0036）。リポジトリのどこから呼んでもよい。
#
#   packaging/build_mac.sh
#
# 使う Python は PYTHON で変えられる（Python 3.14 系。共有ライブラリつきのもの）。
# 指定が無ければ、開発環境（.venv）の元になった Python を使う。
# できるもの: dist/DriftPilot.app、dist/DriftPilot-<版>-macos-<arch>.dmg
# 署名はしない（ADR-0036 決定7）。作業の場所は build/ と dist/（どちらも .gitignore 済み）。
set -euo pipefail

ROOT=${0:A:h:h}
cd "$ROOT"

if [[ -z "${PYTHON:-}" ]]; then
  if [[ -x .venv/bin/python ]]; then
    PYTHON="$(.venv/bin/python -c 'import sys; print(sys.base_prefix)')/bin/python3.14"
  else
    PYTHON=python3.14
  fi
fi
"$PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 14), sys.version' || {
  echo "Python 3.14 が見つかりません。PYTHON=<python3.14 のパス> を付けて呼んでください" >&2; exit 1; }

VENV=build/pkg-venv
echo "== 1/6 ビルド用の環境（$VENV）"
rm -rf "$VENV" build/pyinstaller dist/DriftPilot dist/DriftPilot.app
"$PYTHON" -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip
"$VENV/bin/python" -m pip install -q -c packaging/constraints.txt ".[gui,llm]" -r packaging/requirements-build.txt pip-licenses

echo "== 2/6 PyInstaller"
"$VENV/bin/pyinstaller" --noconfirm --log-level WARN --distpath dist --workpath build/pyinstaller packaging/DriftPilot.spec

APP=dist/DriftPilot.app
CLI="$APP/Contents/MacOS/driftpilot-cli"
VERSION="$("$VENV/bin/python" -c 'import importlib.metadata as m; print(m.version("driftpilot"))')"
ARCH="$(uname -m)"

echo "== 3/6 中身の確かめ"
"$ROOT/packaging/smoke_test.sh" "$CLI"

echo "== 4/6 ライセンスの文"
STAGE=build/dmg
rm -rf "$STAGE"; mkdir -p "$STAGE"
# .app の中には入れない（作った後に .app を書き換えると、PyInstaller が付けた署名が崩れて起動しなくなる）
"$VENV/bin/pip-licenses" --from=mixed --with-license-file --no-license-path --format=plain-vertical \
  --packages $("$VENV/bin/python" packaging/runtime_packages.py) > "$STAGE/THIRD_PARTY_LICENSES.txt"
{ echo; echo "Python $("$VENV/bin/python" -c 'import platform; print(platform.python_version())')"; echo "PSF License"; cat "$("$VENV/bin/python" -c 'import sys; print(sys.base_prefix)')/lib/python3.14/LICENSE.txt"; } >> "$STAGE/THIRD_PARTY_LICENSES.txt"

echo "== 5/6 dmg"
cp -R "$APP" "$STAGE/"
cp packaging/README-first.txt "$STAGE/はじめにお読みください.txt"
ln -s /Applications "$STAGE/Applications"
DMG="dist/DriftPilot-$VERSION-macos-$ARCH.dmg"
rm -f "$DMG"
hdiutil create -quiet -volname "DriftPilot $VERSION" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

echo "== 6/6 できたもの"
du -sh "$APP" "$DMG"
echo "コマンド版: $CLI"
