"""入力が config か show 出力かの識別(ADR-0004 Tier 1)。LLM 判定(Tier 2)は作らない。"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml

from driftpilot import i18n

# 利用者に見える誤りの文。言語に合わせて読み込むときに 1 回だけ選ぶ(messages は読まない決まりのため、ここに持つ)
_TEXTS_JA = {
    "unit": "{name} は 0〜1 の数値にしてください: {value}",
    "settings_dict": "設定は辞書にしてください",
    "signatures_dict": "signatures は辞書にしてください",
    "features_list": "signatures.{name}.features はリストにしてください",
    "pattern_string": "{where}.pattern は文字列にしてください",
    "pattern_invalid": "{where}.pattern が不正です: {error}",
    "settings_unreadable": "設定を読めません: {error}",
    "ambiguous": "判定が曖昧です。--platform と --show-command を指定してください (スコア: {scores})",
}
_TEXTS_EN = {
    "unit": "{name} must be a number from 0 to 1: {value}",
    "settings_dict": "The settings must be a mapping",
    "signatures_dict": "signatures must be a mapping",
    "features_list": "signatures.{name}.features must be a list",
    "pattern_string": "{where}.pattern must be a string",
    "pattern_invalid": "{where}.pattern is invalid: {error}",
    "settings_unreadable": "Cannot read the settings: {error}",
    "ambiguous": "The detection is ambiguous. Specify --platform and --show-command (scores: {scores})",
}
_T = i18n.choose(_TEXTS_JA, _TEXTS_EN)

Verdict = Literal["config", "show", "ambiguous"]

__all__ = [
    "AmbiguousFormatError",
    "DetectSettings",
    "Detection",
    "SniffResult",
    "detect_format",
    "load_settings",
    "register",
    "sniff",
]


class AmbiguousFormatError(ValueError):
    """判定が曖昧で、機種とコマンドの指定が要るとき。"""

    def __init__(self, message: str, scores: dict[str, float] | None = None):
        super().__init__(message)
        self.scores = scores or {}


@dataclass
class DetectSettings:
    threshold: float
    margin: float
    # シグネチャ名 -> [(パターン, 重み)]
    signatures: dict[str, list[tuple[str, float]]] = field(default_factory=dict)


@dataclass
class SniffResult:
    scores: dict[str, float]
    best: str | None
    verdict: Verdict


@dataclass
class Detection:
    kind: Literal["config", "show"]
    sniff: SniffResult
    # 曖昧だったが利用者の指定で show とした場合 True
    overridden: bool = False


def _unit(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError(_T["unit"].format(name=name, value=repr(value)))
    return float(value)


def _parse_settings(data: Any) -> DetectSettings:
    if not isinstance(data, dict):
        raise ValueError(_T["settings_dict"])
    threshold = _unit(data.get("threshold"), "threshold")
    margin = _unit(data.get("margin"), "margin")
    raw_sigs = data.get("signatures", {})
    if not isinstance(raw_sigs, dict):
        raise ValueError(_T["signatures_dict"])
    sigs: dict[str, list[tuple[str, float]]] = {}
    for name, body in raw_sigs.items():
        features = body.get("features") if isinstance(body, dict) else None
        if not isinstance(features, list):
            raise ValueError(_T["features_list"].format(name=name))
        parsed = []
        for i, feat in enumerate(features):
            where = f"signatures.{name}.features[{i}]"
            if not isinstance(feat, dict) or not isinstance(feat.get("pattern"), str):
                raise ValueError(_T["pattern_string"].format(where=where))
            try:
                re.compile(feat["pattern"])
            except re.error as exc:
                raise ValueError(_T["pattern_invalid"].format(where=where, error=exc)) from exc
            parsed.append((feat["pattern"], _unit(feat.get("weight"), f"{where}.weight")))
        sigs[str(name)] = parsed
    return DetectSettings(threshold=threshold, margin=margin, signatures=sigs)


def load_settings(path: str | Path | None = None) -> DetectSettings:
    """設定を読む。path なしなら同梱の defaults.yaml。不正な値は ValueError。"""
    if path is None:
        text = resources.files(__package__).joinpath("defaults.yaml").read_text(encoding="utf-8")
    else:
        text = Path(path).read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(_T["settings_unreadable"].format(error=exc)) from exc
    return _parse_settings(data)


# register() で追加されたシグネチャ: 名前 -> fn(text) -> 0〜1
_REGISTRY: dict[str, Callable[[str], float]] = {}


def register(name: str, fn: Callable[[str], float]) -> None:
    """シグネチャを登録する。fn は text を受け取り 0〜1 の確信度を返す。"""
    _REGISTRY[name] = fn


def _pattern_signature(features: list[tuple[str, float]]) -> Callable[[str], float]:
    compiled = [(re.compile(p, re.MULTILINE), w) for p, w in features]

    def score(text: str) -> float:
        return min(1.0, sum(w for rx, w in compiled if rx.search(text)))

    return score


def sniff(text: str, *, settings: DetectSettings | None = None) -> SniffResult:
    """全シグネチャで採点し、config / show / ambiguous を返す。"""
    settings = settings or load_settings()
    scores = {name: _pattern_signature(f)(text) for name, f in settings.signatures.items()}
    scores.update({name: fn(text) for name, fn in _REGISTRY.items()})
    if not scores:
        return SniffResult(scores, None, "show")
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best, top = ranked[0]
    if top <= 0:
        verdict: Verdict = "show"
    elif top < settings.threshold:
        verdict = "ambiguous"
    elif len(ranked) >= 2 and top - ranked[1][1] < settings.margin:
        verdict = "ambiguous"
    else:
        verdict = "config"
    return SniffResult(scores, best, verdict)


def detect_format(
    text: str,
    *,
    platform: str | None = None,
    command: str | None = None,
    settings: DetectSettings | None = None,
) -> Detection:
    """入力の種類を決める。曖昧で機種とコマンドの指定がなければ AmbiguousFormatError。"""
    result = sniff(text, settings=settings)
    if result.verdict == "config":
        return Detection("config", result)
    if result.verdict == "show":
        return Detection("show", result)
    if platform and command:
        return Detection("show", result, overridden=True)
    scores = ", ".join(f"{k}={v:.2f}" for k, v in result.scores.items())
    raise AmbiguousFormatError(_T["ambiguous"].format(scores=scores), result.scores)
