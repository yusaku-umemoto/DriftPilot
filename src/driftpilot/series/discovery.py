"""PartsPilot の CLI を探して、`pilot-call capabilities` で確かめる(ADR-0233 決定1 / REQ-341 §2)。

探す順(シリーズの合意の 10 章):

- Windows: (1) 設定の `[series] partspilot_cli` → (2) DriftPilot の exe のフォルダーの 1 つ上の `PartsPilot\\partspilot-cli.exe`
- Mac: (1) 設定 → (2) `DriftPilot.app` と同じフォルダーの `PartsPilot.app/Contents/MacOS/partspilot-cli` →
  (3) `/Applications`・`~/Applications`・`/Applications/PilotSeries`・`~/Applications/PilotSeries` の `PartsPilot.app` の中の CLI →
  (4) `mdfind` で bundle identifier から見つけた `.app` の中の CLI(`mdfind` が無い・失敗・5 秒で返らないときは飛ばす)
- (2) は配る形(PyInstaller で固めた形)のときだけ。ソースから動かしているときは飛ばす。Windows・Mac 以外の OS では (1) だけ

候補は、ある・実行できるファイルのときだけ `<CLI> pilot-call capabilities` で確かめる(標準入力を閉じて起動・10 秒で打ち切る。
Windows はコンソールを出さない)。標準出力の最初の JSON の行の `product` が `PartsPilot` で、終わりのコードが 0 なら使う。
違う・読めない・0 以外・時間切れなら次の候補へ。

自動で見つけた場所は、どこにも書かない(合意の 10 章)。このモジュールは設定を読み書きしない(設定の場所は引数で受ける)。
探すのは時間がかかるので、呼ぶ側は画面の操作を止めない裏のスレッドで呼ぶ。
場所の材料(Places)・実行できるファイルか(runner)・mdfind は差し替えられる(テストは本物の場所・本物の CLI を見ない)。
"""

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PRODUCT = "PartsPilot"
BUNDLE_ID = "io.github.yusaku-umemoto.partspilot"
VOICE_SERVE = "voice-serve"
PROBE_TIMEOUT = 10.0
MDFIND_TIMEOUT = 5.0
MAX_PROBE_OUTPUT = 1024 * 1024
MAX_VERSION_CHARS = 64

# どの順で見つけたか
FOUND_SETTINGS = "settings"
FOUND_BESIDE_APP = "beside_app"
FOUND_APPLICATIONS = "applications"
FOUND_MDFIND = "mdfind"

# 見つからない理由の種類
REASON_NOT_FOUND = "not_found"
REASON_NOT_PARTSPILOT = "not_partspilot"
REASON_NO_VOICE_SERVE = "no_voice_serve"

SYSTEM_WINDOWS = "windows"
SYSTEM_MAC = "mac"
SYSTEM_OTHER = "other"

WINDOWS_CLI = Path("PartsPilot") / "partspilot-cli.exe"
MAC_APP = "PartsPilot.app"
MAC_CLI = Path("Contents") / "MacOS" / "partspilot-cli"


@dataclass(frozen=True)
class Places:
    """探す場所の材料。既定は動いている本物の環境(from_runtime)。テストは全部差し替える。

    system は "windows"・"mac"・"other"、frozen は配る形か、executable は DriftPilot の実行ファイル、
    app_dirs は Mac の (3) の置き場所(None なら home と /Applications から決める)、
    mdfind は bundle identifier から .app の場所の並びを返す関数(None なら本物の mdfind)。
    """

    system: str
    frozen: bool
    executable: Path
    home: Path
    app_dirs: tuple[Path, ...] | None = None
    mdfind: Callable[[str], list[Path]] | None = None

    @classmethod
    def from_runtime(cls) -> "Places":
        if sys.platform.startswith("win"):
            system = SYSTEM_WINDOWS
        elif sys.platform == "darwin":
            system = SYSTEM_MAC
        else:
            system = SYSTEM_OTHER
        return cls(system, bool(getattr(sys, "frozen", False)), Path(sys.executable), Path.home())

    def application_dirs(self) -> tuple[Path, ...]:
        if self.app_dirs is not None:
            return self.app_dirs
        root, mine = Path("/Applications"), self.home / "Applications"
        return (root, mine, root / "PilotSeries", mine / "PilotSeries")


@dataclass(frozen=True)
class Discovery:
    """探した結果。usable は音声入力を始められる(PartsPilot が見つかり、voice-serve がある)。

    path は見つけた CLI の場所、order はどの順で見つけたか(FOUND_*)、version は PartsPilot の版、
    component_version は音声入力の部品の版(capabilities の operations["voice-serve"] にあれば)。
    reason は使えない理由の種類(REASON_*。使えるときは None)。argv は子を起動するときの先頭の並び。
    """

    usable: bool
    reason: str | None = None
    path: str = ""
    order: str = ""
    version: str = ""
    voice_serve: bool = False
    component_version: str = ""
    argv: tuple[str, ...] = field(default=(), repr=False)


NOT_FOUND = Discovery(False, REASON_NOT_FOUND)


@dataclass(frozen=True)
class Probe:
    """`pilot-call capabilities` の答え(product が文字で、終わりのコードが 0 のときだけ作る)。"""

    product: str
    version: str
    voice_serve: bool
    component_version: str


def _inner_cli(path: Path, system: str) -> Path:
    """Mac で .app を渡されたときは、中の CLI にする(ADR-0233 の「参照…」)。"""
    if system == SYSTEM_MAC and path.suffix == ".app":
        return path / MAC_CLI
    return path


def candidates(setting: str, places: Places) -> Iterator[tuple[str, Path]]:
    """候補の (見つけた順の名前, CLI の場所) を、探す順に出す(同じ場所は 1 回だけ)。mdfind は最後まで来たときだけ呼ぶ。"""
    seen: set[Path] = set()

    def fresh(order: str, path: Path) -> Iterator[tuple[str, Path]]:
        if path not in seen:
            seen.add(path)
            yield order, path

    if setting.strip():
        yield from fresh(FOUND_SETTINGS, _inner_cli(Path(setting.strip()).expanduser(), places.system))
    if places.system == SYSTEM_WINDOWS:
        if places.frozen:
            yield from fresh(FOUND_BESIDE_APP, places.executable.resolve().parent.parent / WINDOWS_CLI)
        return
    if places.system != SYSTEM_MAC:
        return
    if places.frozen:
        app = next((parent for parent in places.executable.parents if parent.suffix == ".app"), None)
        if app is not None:
            yield from fresh(FOUND_BESIDE_APP, app.parent / MAC_APP / MAC_CLI)
    for folder in places.application_dirs():
        yield from fresh(FOUND_APPLICATIONS, folder / MAC_APP / MAC_CLI)
    finder = places.mdfind or _mdfind
    for found in finder(BUNDLE_ID):
        yield from fresh(FOUND_MDFIND, Path(found) / MAC_CLI)


def _mdfind(bundle_id: str) -> list[Path]:
    """`mdfind "kMDItemCFBundleIdentifier == '…'"` の結果の .app の並び。無い・失敗・5 秒で返らないときは空。"""
    try:
        done = subprocess.run(
            ["mdfind", f"kMDItemCFBundleIdentifier == '{bundle_id}'"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=MDFIND_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if done.returncode != 0:
        return []
    lines = done.stdout.decode("utf-8", errors="replace").splitlines()
    return [Path(line.strip()) for line in lines if line.strip().endswith(".app")]


def default_runner(path: Path) -> list[str] | None:
    """ある・実行できるファイルのときだけ、起動の先頭の並びにする。テストは差し替える。"""
    try:
        if path.is_file() and os.access(path, os.X_OK):
            return [str(path)]
    except OSError:
        pass
    return None


def hidden_console() -> dict[str, Any]:
    """Windows ではコンソールを出さない(CREATE_NO_WINDOW)。ほかの OS では何も足さない。"""
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return {"creationflags": flag} if flag else {}


def parse_capabilities(stdout: bytes) -> Probe | None:
    """標準出力の最初の JSON のオブジェクトの行を読む。product が文字でなければ None。"""
    if len(stdout) > MAX_PROBE_OUTPUT:
        return None
    for line in stdout.splitlines():
        try:
            data = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        product = data.get("product")
        if not isinstance(product, str):
            return None
        version = data.get("version")
        operations = data.get("operations")
        component = ""
        if isinstance(operations, dict):
            has_voice = VOICE_SERVE in operations
            detail = operations.get(VOICE_SERVE)
            value = detail.get("component_version") if isinstance(detail, dict) else None
            component = value if isinstance(value, str) else ""
        elif isinstance(operations, list):
            has_voice = VOICE_SERVE in operations
        else:
            has_voice = False
        # 版は画面の状態の行に文字として出すだけ(長さを切る)
        return Probe(product, version[:MAX_VERSION_CHARS] if isinstance(version, str) else "", has_voice, component[:MAX_VERSION_CHARS])
    return None


def probe(argv: list[str], timeout: float = PROBE_TIMEOUT) -> Probe | None:
    """`<CLI> pilot-call capabilities` を起動して読む。起動できない・時間切れ・0 以外・読めないときは None。"""
    try:
        done = subprocess.run(
            [*argv, "pilot-call", "capabilities"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            **hidden_console(),
        )
    except (OSError, subprocess.SubprocessError, ValueError):
        return None
    if done.returncode != 0:
        return None
    return parse_capabilities(done.stdout)


def discover(
    setting: str,
    places: Places,
    runner: Callable[[Path], list[str] | None] = default_runner,
    *,
    timeout: float = PROBE_TIMEOUT,
) -> Discovery:
    """候補を順に確かめ、最初に PartsPilot と答えたもので止める。設定には何も書かない。

    voice-serve が無ければ usable は偽で reason は no_voice_serve。PartsPilot が無く、違う product の候補があれば
    not_partspilot、ほかは not_found。
    """
    other_product = False
    for order, path in candidates(setting, places):
        argv = runner(path)
        if argv is None:
            continue
        answer = probe(argv, timeout)
        if answer is None:
            continue
        if answer.product != PRODUCT:
            other_product = True
            continue
        return Discovery(
            usable=answer.voice_serve,
            reason=None if answer.voice_serve else REASON_NO_VOICE_SERVE,
            path=str(path),
            order=order,
            version=answer.version,
            voice_serve=answer.voice_serve,
            component_version=answer.component_version,
            argv=tuple(argv),
        )
    return Discovery(False, REASON_NOT_PARTSPILOT if other_product else REASON_NOT_FOUND)


def discover_default(setting: str) -> Discovery:
    """本物の環境で探す入口(どこからも差し替えられなかったときだけ使う)。"""
    return discover(setting, Places.from_runtime())
