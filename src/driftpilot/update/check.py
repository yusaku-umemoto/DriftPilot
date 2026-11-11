"""新しい版を確かめる(ADR-0188 決定2 / REQ-252 §3)。

- 確かめる先は公開用のリポジトリの最新の Release(RELEASES_LATEST_URL)だけ。送るのは GET の要求だけで、
  中身として送るのは DriftPilot の版(User-Agent)だけ。config・設定・キーは送らない。認証のトークンは使わない
- つなぐのは fetch(URL を受けて応答の本文のバイト列を返す関数)だけ。既定は default_fetch(urllib の GET。
  待ち時間 10 秒、TLS は llm_analyzer.http_json.ssl_context、プロキシは urllib の既定、本文は 1 MB まで)。
  テストは偽の fetch を渡す(本物のネットワークにつながない)
- 結果は UpdateStatus の 3 つ: latest(最新)・newer(新しい版がある)・unavailable(確かめられない。reason に理由の文)
- 取ってくる・入れ替える・署名を確かめるのは REQ-253(ここではしない)。assets はそのために読んで持つだけ
- 開いてよい Releases のページは RELEASE_PAGE_PREFIX で始まる URL だけ(open_release_page)
- sys.frozen・sys.platform を読むのは _runtime だけ(テストはこの関数を差し替える。REQ-250 の portable_settings_dir と同じ形)
"""

import http.client
import json
import re
import sys
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from driftpilot import __version__
from driftpilot.llm_analyzer.http_json import ssl_context
from driftpilot.messages import (
    UPDATE_TEXTS,
    UPDATE_REASON_BUSY,
    UPDATE_REASON_NOT_FOUND,
    UPDATE_REASON_UNREACHABLE,
    UPDATE_REASON_UNREADABLE,
    update_reason_http,
)

# 決まった値(直書きしてよいのはここだけ。REQ-252 §3)
RELEASES_LATEST_URL = "https://api.github.com/repos/yusaku-umemoto/DriftPilot/releases/latest"
RELEASES_PAGE_URL = "https://github.com/yusaku-umemoto/DriftPilot/releases"
# 「Releases のページを開く」で開いてよい URL の頭(REQ-252 §4)
RELEASE_PAGE_PREFIX = "https://github.com/yusaku-umemoto/DriftPilot/"
# 既定の fetch がつないでよい URL の頭(GitHub の API だけ)
API_PREFIX = "https://api.github.com/"
TIMEOUT_SECONDS = 10
MAX_BODY_BYTES = 1024 * 1024  # 本文は 1 MB まで(上限より 1 バイト多く読み、超えていたら読めない扱い)
NOTES_MAX_CHARS = 2000  # 変更の内容は 2000 文字まで(長ければ 1999 文字と「…」)
NOTES_CUT_MARK = "…"
GITHUB_API_VERSION = "2022-11-28"
GITHUB_ACCEPT = "application/vnd.github+json"

STATE_LATEST = "latest"
STATE_NEWER = "newer"
STATE_UNAVAILABLE = "unavailable"

INSTALL_WINDOWS_ZIP = "windows_zip"
INSTALL_MAC_APP = "mac_app"
INSTALL_SOURCE = "source"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")

Fetch = Callable[[str], bytes]


class ResponseTooLarge(ValueError):
    """応答の本文が MAX_BODY_BYTES を超えた(読めない扱いにする)。"""


@dataclass(frozen=True)
class Asset:
    """Release に置いてある配る物(REQ-253 で使う)。"""

    name: str
    browser_download_url: str
    size: int


@dataclass(frozen=True)
class UpdateStatus:
    """確かめた結果(REQ-252 §3)。

    state は "latest"・"newer"・"unavailable"。latest は最新の版の文字(「0.2.0」の形。v は付けない)か None。
    published は公開日(YYYY-MM-DD)か None。notes は変更の内容(プレーンテキストのまま。2000 文字まで)。
    page_url はその Release の html_url(無ければ RELEASES_PAGE_URL)。assets は配る物の一覧。
    reason は unavailable の理由の文(ほかは None)。
    """

    state: str
    current: str
    latest: str | None = None
    published: str | None = None
    notes: str = ""
    page_url: str = RELEASES_PAGE_URL
    assets: tuple[Asset, ...] = ()
    reason: str | None = None


def parse_version(text: Any) -> tuple[int, int, int] | None:
    """「0.2.0」と「v0.2.0」の形だけを読む(^v?(\\d+)\\.(\\d+)\\.(\\d+)$)。ほか(文字でない・空も)は None。"""
    if not isinstance(text, str):
        return None
    match = _VERSION.fullmatch(text)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def _version_text(version: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in version)


def request_headers() -> dict[str, str]:
    """既定の fetch が付けるヘッダー。中身として送るのは DriftPilot の版(User-Agent)だけ。"""
    return {
        "Accept": GITHUB_ACCEPT,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": f"DriftPilot/{__version__}",
    }


def default_fetch(url: str) -> bytes:
    """GitHub の API に GET して本文を返す。待ち時間 10 秒、TLS は ssl_context、プロキシは urllib の既定(OS の設定・環境変数)。

    本文は MAX_BODY_BYTES まで(超えていれば ResponseTooLarge)。HTTP の誤りは urllib の HTTPError のまま上げる。
    GitHub の API でない URL にはつながない(ValueError)。
    """
    if not url.startswith(API_PREFIX):
        raise ValueError(UPDATE_TEXTS["url_not_api"])
    request = urllib.request.Request(url, headers=request_headers(), method="GET")
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS, context=ssl_context()) as response:
        body = response.read(MAX_BODY_BYTES + 1)
    if len(body) > MAX_BODY_BYTES:
        raise ResponseTooLarge
    return body


def _http_reason(code: int) -> str:
    if code == 404:
        return UPDATE_REASON_NOT_FOUND
    if code in (403, 429):
        return UPDATE_REASON_BUSY
    return update_reason_http(code)


def _notes(body: Any) -> str:
    if not isinstance(body, str):
        return ""
    if len(body) <= NOTES_MAX_CHARS:
        return body
    return body[: NOTES_MAX_CHARS - len(NOTES_CUT_MARK)] + NOTES_CUT_MARK


def _published(value: Any) -> str | None:
    if isinstance(value, str) and _DATE.match(value):
        return value[:10]
    return None


def _assets(value: Any) -> tuple[Asset, ...]:
    """形の合う要素だけを読む(name・browser_download_url は文字、size は真偽値でない整数)。リストでなければ空。"""
    if not isinstance(value, list):
        return ()
    found = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name, url, size = item.get("name"), item.get("browser_download_url"), item.get("size")
        if isinstance(name, str) and isinstance(url, str) and isinstance(size, int) and not isinstance(size, bool):
            found.append(Asset(name, url, size))
    return tuple(found)


def check_latest(current: str, fetch: Fetch | None = None) -> UpdateStatus:
    """最新の Release を確かめる。最新が今の版より新しいときだけ newer。同じか古いときは latest。

    fetch を省くと、そのときの default_fetch を使う(呼ぶときに読むので、テストはモジュールの default_fetch を差し替えられる)。

    確かめられないとき(HTTP の誤り・つながらない・時間切れ・JSON でない・欄が無い・版が読めない)は unavailable。
    """

    def unavailable(reason: str) -> UpdateStatus:
        return UpdateStatus(STATE_UNAVAILABLE, current, reason=reason)

    current_version = parse_version(current)
    if current_version is None:
        return unavailable(UPDATE_REASON_UNREADABLE)
    try:
        body = (fetch or default_fetch)(RELEASES_LATEST_URL)
    except urllib.error.HTTPError as exc:  # URLError の子なので先に見る
        return unavailable(_http_reason(exc.code))
    except (urllib.error.URLError, OSError, http.client.HTTPException):  # 時間切れ(TimeoutError)も OSError
        return unavailable(UPDATE_REASON_UNREACHABLE)
    except ValueError:  # 本文が大きすぎる
        return unavailable(UPDATE_REASON_UNREADABLE)
    try:
        data = json.loads(body)
    except (ValueError, TypeError):  # JSON でない・UTF-8 でない(UnicodeDecodeError も ValueError)
        return unavailable(UPDATE_REASON_UNREADABLE)
    if not isinstance(data, dict):
        return unavailable(UPDATE_REASON_UNREADABLE)
    latest_version = parse_version(data.get("tag_name"))
    if latest_version is None:
        return unavailable(UPDATE_REASON_UNREADABLE)
    html_url = data.get("html_url")
    return UpdateStatus(
        STATE_NEWER if latest_version > current_version else STATE_LATEST,
        current,
        latest=_version_text(latest_version),
        published=_published(data.get("published_at")),
        notes=_notes(data.get("body")),
        page_url=html_url if isinstance(html_url, str) and html_url else RELEASES_PAGE_URL,
        assets=_assets(data.get("assets")),
    )


def _runtime() -> tuple[bool, str]:
    """動いている形 (PyInstaller で作った実行ファイルか, OS の名前)。sys.frozen・sys.platform を読むのはここだけ。"""
    return bool(getattr(sys, "frozen", False)), sys.platform


def install_kind() -> str:
    """入れ方。"windows_zip"(固めた exe で Windows)・"mac_app"(固めたアプリで Mac)・"source"(ほか)。"""
    frozen, platform = _runtime()
    if frozen and platform.startswith("win"):
        return INSTALL_WINDOWS_ZIP
    if frozen and platform == "darwin":
        return INSTALL_MAC_APP
    return INSTALL_SOURCE


def is_release_page(url: Any) -> bool:
    """開いてよい Releases のページか(RELEASE_PAGE_PREFIX で始まり、制御文字・空白を含まない)。"""
    return (
        isinstance(url, str)
        and url.startswith(RELEASE_PAGE_PREFIX)
        and not any(ch.isspace() or ord(ch) < 0x20 or ord(ch) == 0x7F for ch in url)
    )


def open_release_page(url: Any, opener: Callable[[str], Any] | None = None) -> bool:
    """url が開いてよい Releases のページのときだけ既定のブラウザーで開き、真を返す。ほかは何も開かずに偽。

    opener は開く関数(省くとそのときの webbrowser.open。テストで差し替える)。
    """
    if not is_release_page(url):
        return False
    (opener or webbrowser.open)(url)
    return True
