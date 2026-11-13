"""`driftpilot selfcheck`(REQ-38 / ADR-0036 決定6)。同梱し忘れた部品やデータが無いかを、その実行環境で確かめる。

exe(PyInstaller)の中にだけ起きる抜け(部品の版の情報、keyring の保管庫の部品など)を、ビルドのたびに見つけるためのもの。
項目は REQ-38 §2 の順。1項目を1つの関数にし、例外は項目ごとに受け止めて失敗にする(残りの項目も確かめる)。
gui / llm extra が入っていなければ、その項目は「入っていません」で OK 扱い(REQ-38 §6)。入っているかは
driftpilot の extra の依存のメタデータで決める(入れたつもりなのに import できない＝同梱し忘れ、を見分けるため)。
画面の部品(web)は、pywebview の版の情報が無くても webview のモジュールが見つかれば入っているとする
(gui extra は pywebview だけなので、版の情報の抜けを「入っていません」で見逃さないため。gui_installed)。
キーの値は読まない。保管庫は store_problem だけを見る(read_key は呼ばない)。通信しない。ファイルを書かない。
終了コードは、すべて OK なら 0、1つでも失敗なら 1。

入口(cli/__init__.py)は、本体(cli/commands.py)を読まずにこのモジュールの main を呼ぶ。そのため、ここを読み込むときには
標準ライブラリと、比較の部品を読まないモジュール(messages・cli/output)だけを読む。比較の部品や外の部品は、
確かめる関数の中で読む(欠けていても、その項目の失敗として出す)。
"""

import argparse
import base64
import importlib.util
import json
import os
import platform
import re
from dataclasses import dataclass
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from driftpilot import __version__
from driftpilot.cli.output import write_machine
from driftpilot.messages import (
    CLI_HELP,
    SELFCHECK_ALL_OK,
    SELFCHECK_BUNDLED,
    SELFCHECK_CISCO_FAMILY,
    SELFCHECK_GUI_WEB,
    SELFCHECK_HELP,
    SELFCHECK_ITEM_SEPARATOR,
    SELFCHECK_KEY_STORE,
    SELFCHECK_LLM,
    SELFCHECK_METADATA,
    SELFCHECK_PART_SEPARATOR,
    SELFCHECK_PATTERNS,
    SELFCHECK_SIGNATURE,
    SELFCHECK_TEMPLATES,
    SELFCHECK_VERSION,
    selfcheck_error,
    selfcheck_failed_count,
    selfcheck_fingerprint,
    selfcheck_import_failed,
    selfcheck_line,
    selfcheck_metadata_missing,
    selfcheck_not_installed,
    selfcheck_rules,
    selfcheck_signature,
    selfcheck_signature_failed,
    selfcheck_store,
    selfcheck_templates,
    selfcheck_templates_empty,
    selfcheck_templates_missing,
    selfcheck_unreadable,
    selfcheck_versions,
)

EXIT_OK = 0
EXIT_FAILED = 1

# 版の情報(メタデータ)が読めないといけない部品(REQ-38 §2)。ntc_templates は無いと起動しなかった実例がある
METADATA_PACKAGES = ("driftpilot", "ntc_templates", "hier_config", "textfsm")
# pywebview の画面のファイル(driftpilot.gui_web の中。REQ-103 §7)。exe に入れ忘れると窓が空になる
GUI_WEB_FILES = (
    "static/sample.html",
    "static/settings.html",
    "static/compare.html",
    "static/batch.html",
    "static/ai_pane.html",
    "static/pilot.css",
    "static/js/bridge.js",
    "static/js/dom.js",
    "static/js/dialog.js",
    "static/js/sample.js",
    "static/js/settings.js",
    "static/js/compare.js",
    "static/js/ai_view.js",
    "static/js/ai_pane.js",
    "static/js/answer_view.js",
    "static/js/answer_copy.js",
    "static/js/batch.js",
    "static/js/drop.js",
    "static/js/path_tail.js",
    "static/js/column_width.js",
    "static/js/pair_drag.js",
    "static/js/marks.js",
    "static/js/raw_options.js",
    "static/js/character.js",
    "static/js/theme.js",
    # キャラクターの絵と、元の名前との対応の説明(ADR-0174 決定4)
    "static/character/README.md",
    "static/character/ai-thinking.png",
    "static/character/ai-thinking-still.png",
    "static/character/batch-progress.png",
    "static/character/batch-progress-still.png",
    "static/character/no-diff.png",
    "static/character/no-diff-still.png",
    "static/character/splash.png",
    "static/character/warning.png",
    "static/character/welcome.png",
    # 答えの吹き出しの顔(ADR-0181 決定4)
    "static/character/avatar.png",
)
# 依存の1行(例: 'openai<4,>=3.31; extra == "llm"')の、先頭の名前と extra の印
_REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")
_EXTRA_MARKER = re.compile(r"""extra\s*==\s*["']([^"']+)["']""")


@dataclass(frozen=True)
class Check:
    """1項目の結果。installed=False は extra が入っていない(ok は True のまま)。"""

    name: str
    ok: bool
    detail: str
    installed: bool = True


class _Failed(Exception):
    """項目の失敗。本文はそのまま detail にする(利用者に見せる文)。"""


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--format", choices=["text", "json"], default="text", help=CLI_HELP["format_text"])


def add_parser(sub: Any) -> None:
    """全体のヘルプに出すための登録(cli/commands.py の build_parser から呼ぶ)。"""
    _add_arguments(sub.add_parser("selfcheck", help=SELFCHECK_HELP))


def build_parser() -> argparse.ArgumentParser:
    """`driftpilot selfcheck` だけの引数の解釈(入口から本体を読まずに使う)。"""
    parser = argparse.ArgumentParser(prog="driftpilot selfcheck", description=SELFCHECK_HELP)
    _add_arguments(parser)
    return parser


def _check_version() -> Check:
    # importlib.metadata ではなくコードに持つ版を出す(exe でも取れる。版の情報は別の項目で確かめる)
    return Check(SELFCHECK_VERSION, True, selfcheck_versions(__version__, platform.python_version()))


def _bundled_rules() -> str:
    import yaml

    from driftpilot.diff_engine.normalization import BUILTIN_RULES, parse_rules

    # builtin_rules はキャッシュ付きなので使わず、同梱のファイルをその場で読む
    try:
        text = resources.files("driftpilot.diff_engine").joinpath(BUILTIN_RULES).read_text(encoding="utf-8")
        count = len(parse_rules(yaml.safe_load(text)))
    except Exception as exc:  # noqa: BLE001 - 読めない・形が違うのどちらも失敗にする
        raise _Failed(selfcheck_unreadable(BUILTIN_RULES, type(exc).__name__)) from exc
    return selfcheck_rules(BUILTIN_RULES, count)


def _bundled_defaults() -> str:
    from driftpilot.format_detect import load_settings

    name = "defaults.yaml"
    try:
        load_settings()  # path なしなら同梱の defaults.yaml を importlib.resources で読む(キャッシュなし)
    except Exception as exc:  # noqa: BLE001
        raise _Failed(selfcheck_unreadable(name, type(exc).__name__)) from exc
    return name


def _check_bundled() -> Check:
    return Check(SELFCHECK_BUNDLED, True, SELFCHECK_PART_SEPARATOR.join([_bundled_rules(), _bundled_defaults()]))


def _check_patterns() -> Check:
    # `driftpilot patterns` と同じ材料(Cisco 系の表と、--platform で選べる各機種の表)
    from driftpilot.config_parser.platforms import CONFIG_PLATFORMS, patterns_fingerprint

    parts = []
    for name in (None, *CONFIG_PLATFORMS):
        fp = patterns_fingerprint(name)
        parts.append(selfcheck_fingerprint(name or SELFCHECK_CISCO_FAMILY, fp.digest, fp.count))
    return Check(SELFCHECK_PATTERNS, True, SELFCHECK_ITEM_SEPARATOR.join(parts))


def _template_dir() -> Path:
    """ntc_templates のテンプレートのフォルダー。ntc_templates.parse._get_template_dir と同じ決め方
    (環境変数 NTC_TEMPLATES_DIR が優先、無ければ部品の中の templates。ソースの置き場から探す分は見ない)。

    ntc_templates は読み込むときに自分の版の情報を読むので、読み込まずに場所だけを探す
    (版の情報の抜けは部品の版の情報の項目で失敗にし、こちらはフォルダーとテンプレートの有無だけを見る)。
    """
    if (env := os.environ.get("NTC_TEMPLATES_DIR")) is not None:
        return Path(env)
    spec = importlib.util.find_spec("ntc_templates")
    if spec is None or not spec.submodule_search_locations:
        raise _Failed(selfcheck_import_failed("ntc_templates", "ModuleNotFoundError"))
    return Path(next(iter(spec.submodule_search_locations))) / "templates"


def _check_templates() -> Check:
    folder = _template_dir()
    if not folder.is_dir():
        return Check(SELFCHECK_TEMPLATES, False, selfcheck_templates_missing(str(folder)))
    count = sum(1 for _ in folder.glob("*.textfsm"))
    if count == 0:
        return Check(SELFCHECK_TEMPLATES, False, selfcheck_templates_empty(str(folder)))
    return Check(SELFCHECK_TEMPLATES, True, selfcheck_templates(count, str(folder)))


def _check_metadata() -> Check:
    found, missing = [], []
    for name in METADATA_PACKAGES:
        try:
            found.append(f"{name} {metadata.version(name)}")
        except metadata.PackageNotFoundError:
            missing.append(name)
    if missing:
        return Check(SELFCHECK_METADATA, False, selfcheck_metadata_missing(missing))
    return Check(SELFCHECK_METADATA, True, SELFCHECK_PART_SEPARATOR.join(found))


# RFC 8032 の 7.1 の TEST 1(公開の鍵・空の文・署名)。署名の確かめの部品(cryptography)が動くかを見る(REQ-253 §10)
RFC8032_TEST1_PUBLIC = "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
RFC8032_TEST1_SIGNATURE = (
    "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
)


def _check_signature() -> Check:
    """署名の確かめ(REQ-253 §10)。RFC 8032 の TEST 1 が通り、1 バイト変えた文は通らず、keys.py の 2 つの鍵の印が合うこと。

    cryptography が入っていない(exe に入れ忘れた)ときは、ここで失敗になる。通信しない・ファイルを書かない。
    """
    try:
        import cryptography  # noqa: F401 - 入っているかだけを見る(PyInstaller に入れさせる)
    except ImportError as exc:
        return Check(SELFCHECK_SIGNATURE, False, selfcheck_import_failed("cryptography", type(exc).__name__))
    from driftpilot.update.errors import SignatureError
    from driftpilot.update.keys import KEYS, key_mark
    from driftpilot.update.verify import verify_signature

    test_keys = {"rfc8032": bytes.fromhex(RFC8032_TEST1_PUBLIC)}
    signature = base64.b64encode(bytes.fromhex(RFC8032_TEST1_SIGNATURE))
    try:
        verify_signature(b"", signature, test_keys)
    except SignatureError:
        raise _Failed(selfcheck_signature_failed("RFC 8032 TEST 1")) from None
    try:
        verify_signature(b"x", signature, test_keys)
    except SignatureError:
        pass
    else:
        raise _Failed(selfcheck_signature_failed("RFC 8032 TEST 1"))
    marks = []
    for key in KEYS:
        raw = key.raw()
        if len(raw) != 32 or key_mark(raw) != key.mark:
            raise _Failed(selfcheck_signature_failed(key.name))
        marks.append((key.name, key.mark))
    return Check(SELFCHECK_SIGNATURE, True, selfcheck_signature(marks))


def _extra_requirements(extra: str) -> list[str]:
    """driftpilot の extra の依存の名前。driftpilot の版の情報が無ければ空(その抜けは部品の版の情報の項目で失敗になる)。"""
    try:
        requires = metadata.requires("driftpilot") or []
    except metadata.PackageNotFoundError:
        return []
    names = []
    for line in requires:
        marker = _EXTRA_MARKER.search(line)
        name = _REQUIREMENT_NAME.match(line)
        if marker and name and marker.group(1) == extra:
            names.append(name.group(1))
    return names


def extra_installed(extra: str) -> bool:
    """extra の依存のうち1つでも版の情報があれば「入っている」(一部だけ入っている抜けも、各項目で失敗として見つける)。"""
    for name in _extra_requirements(extra):
        try:
            metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        return True
    return False


def gui_web_module_found() -> bool:
    """webview のモジュールが見つかるか(読み込まずに探すだけ。窓を開かない・重い読み込みをしない)。

    gui extra は pywebview だけなので、pywebview の版の情報が抜けると extra_installed("gui") が偽になり、
    抜けを「入っていません」で見逃す。モジュールが見つかれば「入っている」として、版の情報の抜けを失敗にする
    (exe に pywebview の版の情報を入れ忘れたことを見つけるため。ADR-0074 決定7 / REQ-250 判断の記録 2)。
    """
    try:
        return importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError):  # 探す途中の抜け・壊れた登録は「見つからない」(版の情報の有無で決める)
        return False


def gui_installed() -> bool:
    """画面の部品(web)の行を「入っている」として確かめるか。

    gui extra の依存のどれかに版の情報があれば入っている(extra_installed)。版の情報が無くても、driftpilot の
    依存に gui extra が書かれていて、webview のモジュールが見つかれば入っている(版の情報の抜けを失敗にするため)。
    driftpilot の依存に gui extra が無い(CLI だけの形)ときは、モジュールを探さない。
    """
    if extra_installed("gui"):
        return True
    return bool(_extra_requirements("gui")) and gui_web_module_found()


def _import_version(name: str, module: str) -> Check:
    try:
        mod = __import__(module)
    except ImportError as exc:
        return Check(name, False, selfcheck_import_failed(module, type(exc).__name__))
    return Check(name, True, f"{module} {getattr(mod, '__version__', '?')}")


def _check_gui_web(installed: bool) -> Check:
    """pywebview の画面の部品(REQ-103 §7)。窓は開かない。gui extra で入っているかを決める。"""
    if not installed:
        return Check(SELFCHECK_GUI_WEB, True, selfcheck_not_installed("gui"), installed=False)
    # Windows の exe で同梱のファイルが欠けていれば、取り込みの失敗ではなくファイルの抜けとして出す(ADR-0192 決定3。
    # Windows の exe でなければ空の一覧で、結果は今のまま)。webview はこのモジュールを読まない形のまま
    from driftpilot.gui_web import bundled_files

    missing = bundled_files.missing_bundled_files()
    if missing:
        raise _Failed(selfcheck_unreadable(bundled_files.windows_name(missing[0]), "FileNotFoundError"))
    # import は文字ではなく文で書く(PyInstaller がコマンド版に gui_web と webview を入れるように)
    module = "driftpilot.gui_web"
    try:
        import driftpilot.gui_web.app  # noqa: F401 - 窓を開く部品(webview は run の中で読む)

        module = "webview"
        import webview  # noqa: F401
    except ImportError as exc:
        return Check(SELFCHECK_GUI_WEB, False, selfcheck_import_failed(module, type(exc).__name__))
    package = resources.files("driftpilot.gui_web")
    for name in GUI_WEB_FILES:
        if not package.joinpath(name).is_file():
            raise _Failed(selfcheck_unreadable(name, "FileNotFoundError"))
    try:
        version = metadata.version("pywebview")  # webview には __version__ が無い。exe は copy_metadata で入れる
    except metadata.PackageNotFoundError:
        return Check(SELFCHECK_GUI_WEB, False, selfcheck_metadata_missing(["pywebview"]))
    return Check(SELFCHECK_GUI_WEB, True, f"pywebview {version}")


def _check_llm(installed: bool) -> Check:
    if not installed:
        return Check(SELFCHECK_LLM, True, selfcheck_not_installed("llm"), installed=False)
    return _import_version(SELFCHECK_LLM, "openai")


def _check_key_store(installed: bool) -> Check:
    if not installed:  # 保管庫に触らない
        return Check(SELFCHECK_KEY_STORE, True, selfcheck_not_installed("llm"), installed=False)
    from driftpilot import key_store

    # 使えるかだけを見る。キーの値は読まない(read_key は呼ばない。安全の決まり3)
    problem = key_store.store_problem()
    return Check(SELFCHECK_KEY_STORE, problem is None, selfcheck_store(key_store.store_name(), problem))


def _guarded(name: str, check: Any, *args: Any) -> Check:
    """1項目を確かめる。例外は受け止めて、その項目の失敗にする(例外の本文は使わず、型名だけを添える)。"""
    try:
        return check(*args)
    except _Failed as exc:
        return Check(name, False, str(exc))
    except Exception as exc:  # noqa: BLE001 - 部品の抜けは様々な例外になる。どれも失敗として出す
        return Check(name, False, selfcheck_error(type(exc).__name__))


def run_checks() -> list[Check]:
    """REQ-38 §2 の順に全項目を確かめる。"""
    gui = gui_installed()
    llm = extra_installed("llm")
    return [
        _guarded(SELFCHECK_VERSION, _check_version),
        _guarded(SELFCHECK_BUNDLED, _check_bundled),
        _guarded(SELFCHECK_PATTERNS, _check_patterns),
        _guarded(SELFCHECK_TEMPLATES, _check_templates),
        _guarded(SELFCHECK_METADATA, _check_metadata),
        _guarded(SELFCHECK_SIGNATURE, _check_signature),  # REQ-253 で足した(判断の記録 2)
        _guarded(SELFCHECK_GUI_WEB, _check_gui_web, gui),
        _guarded(SELFCHECK_LLM, _check_llm, llm),
        _guarded(SELFCHECK_KEY_STORE, _check_key_store, llm),
    ]


def to_json(checks: list[Check]) -> str:
    return json.dumps([{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks], ensure_ascii=False, indent=2)


def run(args: argparse.Namespace) -> int:
    checks = run_checks()
    failed = sum(1 for c in checks if not c.ok)
    if args.format == "json":
        write_machine(to_json(checks) + "\n")  # UTF-8・\n(ADR-0023 決定1)
    else:
        # 人が読む出力はコンソールの文字コードのまま(ADR-0023 決定2)
        for c in checks:
            print(selfcheck_line(c.name, c.ok, c.detail, c.installed))
        print(selfcheck_failed_count(failed) if failed else SELFCHECK_ALL_OK)
    return EXIT_FAILED if failed else EXIT_OK


def main(argv: list[str]) -> int:
    """`driftpilot selfcheck` の引数(selfcheck より後ろ)を解釈して実行する。入口(cli/__init__.py)から呼ぶ。

    引数の誤りは 2(argparse の決まり。本体の main と同じ)、--help は 0。
    """
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    return run(args)
