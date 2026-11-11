"""新しい版の確かめ(ADR-0188 決定2 / REQ-252)と、署名つきのその場の更新(決定3・4 / REQ-253)。

画面(gui_web)はここを呼ぶ。
- check.py: 最新の Release を確かめる・入れ方(install_kind)
- keys.py・verify.py: 公開の鍵 2 つと、SHA256SUMS.txt の署名と指紋の確かめ
- download.py: 決まった URL の頭からだけ取ってくる・zip を安全に展開する・dmg から .app を写す
- prepare.py: 入れ替えの準備(今の版が行う)・新しい版の起動・起動時の掃除
- apply.py: 新しい版が入れ替える(packaging/gui_main.py の --apply-update から呼ぶ)

関数の download・extract_zip はここから出さない(同じ名前のモジュール driftpilot.update.download を隠さないため)。
"""

from driftpilot.update.check import (
    INSTALL_MAC_APP,
    INSTALL_SOURCE,
    INSTALL_WINDOWS_ZIP,
    RELEASE_PAGE_PREFIX,
    RELEASES_LATEST_URL,
    RELEASES_PAGE_URL,
    STATE_LATEST,
    STATE_NEWER,
    STATE_UNAVAILABLE,
    Asset,
    UpdateStatus,
    check_latest,
    default_fetch,
    install_kind,
    open_release_page,
    parse_version,
)
from driftpilot.update.download import DOWNLOAD_PREFIX
from driftpilot.update.errors import UpdateCancelled, UpdateError
from driftpilot.update.keys import TRUSTED_KEYS
from driftpilot.update.prepare import (
    STAGE_DOWNLOADING,
    STAGE_PREPARING,
    STAGE_VERIFYING,
    PreparedUpdate,
    cleanup_stale,
    discard,
    prepare_update,
    start_applier,
)
from driftpilot.update.verify import parse_sums, sha256_file, verify_signature

__all__ = [
    "DOWNLOAD_PREFIX",
    "INSTALL_MAC_APP",
    "INSTALL_SOURCE",
    "INSTALL_WINDOWS_ZIP",
    "RELEASE_PAGE_PREFIX",
    "RELEASES_LATEST_URL",
    "RELEASES_PAGE_URL",
    "STAGE_DOWNLOADING",
    "STAGE_PREPARING",
    "STAGE_VERIFYING",
    "STATE_LATEST",
    "STATE_NEWER",
    "STATE_UNAVAILABLE",
    "TRUSTED_KEYS",
    "Asset",
    "PreparedUpdate",
    "UpdateCancelled",
    "UpdateError",
    "UpdateStatus",
    "check_latest",
    "cleanup_stale",
    "default_fetch",
    "discard",
    "install_kind",
    "open_release_page",
    "parse_sums",
    "parse_version",
    "prepare_update",
    "sha256_file",
    "start_applier",
    "verify_signature",
]
