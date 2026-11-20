"""設定が変わったときの窓への知らせの形(ADR-0250 決定1・REQ-379 §2)。

設定を書く側(設定の窓・比較の窓・一括比較の窓・新しい版の確かめ)は、書けた後に WebSession.publish へ
「変わった種類」の集まりと、変えた窓(origin)を渡す。開いている窓は、それぞれ _on_settings_change(SettingsChange) で受ける。

種類は次の 4 つだけ(ADR-0250 決定1 の表):
- SETTINGS: 設定の窓で保存した(AI・秘密の語・上限・キャラクター・音声入力・自動の比較などの config.toml と AI のプロファイルのファイル)
- RAW_DEFAULTS: 単純比較のいつも効く設定(raw-compare.json)を書いた(設定の窓・比較の窓の「いつも効く設定に足す」)
- PROFILES: 使う AI・指示文のプロファイルを切り替えた(比較の窓・一括比較の窓)
- NOTICE: 新しい版の知らせが出た・閉じた(描き直すだけ)

色(ADR-0189)は値を運んで窓の外に当てるだけなので、この知らせには入れない。標準ライブラリだけを読む。
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

SETTINGS = "settings"
RAW_DEFAULTS = "raw_defaults"
PROFILES = "profiles"
NOTICE = "notice"

# 表の 4 つ(これ以外の種類は作れない)
KINDS: frozenset[str] = frozenset({SETTINGS, RAW_DEFAULTS, PROFILES, NOTICE})

# 1 回の知らせに複数の種類があるとき、窓が種類ごとの処理を行う順(描き直しは最後に 1 回だけ)
ORDER: tuple[str, ...] = (RAW_DEFAULTS, PROFILES, SETTINGS, NOTICE)


def quietly(work: Callable[[], object]) -> None:
    """窓が知らせを受けて行う処理を 1 つ行い、失敗は捨てる(閉じかけの窓の失敗は今までどおり捨てる。ADR-0250 決定2)。

    1 つの処理が失敗しても、同じ窓のほかの処理と描き直しは続ける。
    """
    try:
        work()
    except Exception:  # noqa: BLE001 - 閉じかけの窓への知らせの失敗は捨てる
        pass


@dataclass(frozen=True)
class SettingsChange:
    """変わった種類(1 つ以上)と、変えた窓(origin。窓の口のオブジェクトか None)。変えられない。

    表に無い種類・空の集まりは作れない(ValueError)。kinds には文字列の集まりなら何を渡してもよく、frozenset に直す。
    """

    kinds: frozenset[str]
    origin: Any = None

    def __post_init__(self) -> None:
        raw: Any = self.kinds
        if isinstance(raw, str):
            raise ValueError("kinds must be a collection of kinds, not a string")
        kinds = frozenset(raw) if isinstance(raw, Iterable) else frozenset()
        if not kinds:
            raise ValueError("kinds must not be empty")
        unknown = kinds - KINDS
        if unknown:
            raise ValueError(f"unknown settings change kind: {sorted(map(str, unknown))}")
        object.__setattr__(self, "kinds", kinds)

    def has(self, kind: str) -> bool:
        """この知らせにその種類が入っているか。"""
        return kind in self.kinds

    def ordered(self) -> list[str]:
        """入っている種類を、窓が処理する順(ORDER)に並べる。"""
        return [kind for kind in ORDER if kind in self.kinds]
