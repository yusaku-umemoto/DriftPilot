"""raw 比較の前に適用する正規化ルールの読み込みと適用(ADR-0007)。

機種のルール(任意のキー `platform` を持つもの。ADR-0025 決定5、REQ-22 §3b)は、config の機種が一致したときにだけ
対象になり、そのときは default_enabled が効く。core は structured の前の本文にも行数を変えずに当てる。
機種が「当てない」とする行(FortiGate の行をまたぐ値の本文)には当てない(apply_rules_traced)。
`platform` の無いルールの扱いは今までどおり(config は既定で正規化しない。show は default_enabled)。

機種のルールはパッケージに同梱する(platform_rules.yaml。builtin_rules())。`--rules` を省いたときに使い、
指定したときはそのファイルだけを使う(既定を外す手段。docs/notes/req-22-fortigate.md)。
"""

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal, get_args

import yaml

from driftpilot.config_parser.platforms import FLAT_PLATFORMS

SCOPES = ("config", "show", "both")
KINDS = ("config", "show")
# 比較から外した理由(ADR-0025 決定6)。分岐に使わない列挙のため、足しても schema_version は上げない
ExclusionReason = Literal["reencrypted_value"]
EXCLUSION_REASONS: tuple[str, ...] = get_args(ExclusionReason)
BUILTIN_RULES = "platform_rules.yaml"
# 置換の雛形の中の改行(`\n` / `\r` の書き方を含む)。機種のルールは行数を変えてはいけない
_NEWLINE_ESCAPE = re.compile(r"(?<!\\)(?:\\\\)*\\[nr]")


@dataclass(frozen=True)
class NormalizationRule:
    id: str
    description: str
    pattern: re.Pattern[str]
    replacement: str
    default_enabled: bool
    scope: str
    # 機種のルール(config の機種が一致したときだけ対象)。None は機種を問わないルール
    platform: str | None = None
    # 値を比較から外すルールの理由。結果の exclusions に件数と一緒に出す(黙って無視しない)
    exclusion_reason: ExclusionReason | None = None


def load_rules(path: str | Path) -> list[NormalizationRule]:
    """ルールファイルを yaml.safe_load で読み、検証済みのルール一覧を返す。"""
    with open(path, encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ValueError(f"ルールファイルを YAML として読めません: {e}") from e
    return parse_rules(data)


@lru_cache(maxsize=1)
def _builtin() -> tuple[NormalizationRule, ...]:
    text = resources.files(__package__).joinpath(BUILTIN_RULES).read_text(encoding="utf-8")
    return tuple(parse_rules(yaml.safe_load(text)))


def builtin_rules() -> list[NormalizationRule]:
    """パッケージに同梱した機種のルール(--rules を省いたときに使う)。"""
    return list(_builtin())


def parse_rules(data: Any) -> list[NormalizationRule]:
    """読み込んだ YAML の値を検証し、ルール一覧にする。"""
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError("ルールファイルの最上位は `rules:` のリストにしてください")
    rules: list[NormalizationRule] = []
    seen: set[str] = set()
    for index, raw in enumerate(data["rules"]):
        if not isinstance(raw, dict):
            raise ValueError(f"rules[{index}] はマッピングにしてください")
        for key in ("id", "description", "pattern", "scope", "default_enabled"):
            if key not in raw:
                raise ValueError(f"rules[{index}] に必須キー {key} がありません")
        rule_id = raw["id"]
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(f"rules[{index}] の id は空でない文字列にしてください")
        if rule_id in seen:
            raise ValueError(f"ルール ID が重複しています: {rule_id}")
        seen.add(rule_id)
        if raw["scope"] not in SCOPES:
            raise ValueError(f"{rule_id}: scope が不正です: {raw['scope']!r}")
        if not isinstance(raw["default_enabled"], bool):
            raise ValueError(f"{rule_id}: default_enabled は真偽値にしてください")
        if not isinstance(raw["description"], str) or not isinstance(raw["pattern"], str):
            raise ValueError(f"{rule_id}: description と pattern は文字列にしてください")
        replacement = raw.get("replacement", "")
        if not isinstance(replacement, str):
            raise ValueError(f"{rule_id}: replacement は文字列にしてください")
        try:
            pattern = re.compile(raw["pattern"])
            pattern.sub(replacement, "")
        except (re.error, IndexError) as e:
            raise ValueError(f"{rule_id}: pattern または replacement が不正です: {e}") from e
        platform = raw.get("platform")
        if platform is not None:
            if not isinstance(platform, str) or not platform.strip():
                raise ValueError(f"{rule_id}: platform は空でない文字列にしてください")
            if raw["scope"] != "config":
                raise ValueError(f"{rule_id}: platform を付けるルールの scope は config にしてください")
            if "\n" in replacement or "\r" in replacement or _NEWLINE_ESCAPE.search(replacement):
                raise ValueError(f"{rule_id}: platform を付けるルールの replacement に改行は書けません(行数が変わるため)")
        reason = raw.get("exclusion_reason")
        if reason is not None:
            if reason not in EXCLUSION_REASONS:
                raise ValueError(
                    f"{rule_id}: exclusion_reason が不正です: {reason!r}(使える値: {', '.join(EXCLUSION_REASONS)})"
                )
            if platform is None:
                raise ValueError(f"{rule_id}: exclusion_reason は platform と一緒に指定してください")
        rules.append(
            NormalizationRule(
                id=rule_id,
                description=raw["description"],
                pattern=pattern,
                replacement=replacement,
                default_enabled=raw["default_enabled"],
                scope=raw["scope"],
                platform=platform,
                exclusion_reason=reason,
            )
        )
    return rules


def select_rules(
    rules: list[NormalizationRule], kind: str, enabled_ids: list[str] | None = None, platform: str | None = None
) -> list[NormalizationRule]:
    """kind に対して適用するルールを決める。enabled_ids があれば既定値を上書きする。

    機種のルールは、kind=config で platform が一致したときだけ対象になり、既定では default_enabled が効く。
    """
    if kind not in KINDS:
        raise ValueError(f"kind は config か show にしてください: {kind!r}")
    in_scope = [
        r
        for r in rules
        if r.scope in (kind, "both") and (r.platform is None or (kind == "config" and r.platform == platform))
    ]
    if enabled_ids is None:
        if kind == "show":
            return [r for r in in_scope if r.default_enabled]
        return [r for r in in_scope if r.platform is not None and r.default_enabled]
    by_id = {r.id: r for r in in_scope}
    selected: list[NormalizationRule] = []
    seen: set[str] = set()
    for rule_id in enabled_ids:
        if rule_id in seen:
            raise ValueError(f"enabled_ids にルール ID の重複があります: {rule_id}")
        seen.add(rule_id)
        if rule_id not in by_id:
            raise ValueError(f"kind={kind} で使えないルール ID です: {rule_id}")
        selected.append(by_id[rule_id])
    return selected


def apply_rules_traced(
    text: str, rules: list[NormalizationRule]
) -> tuple[list[str], dict[str, list[int]]]:
    """ルールを適用し、正規化後の行と、ルール ID 別の「変わった行」の元の行番号(1始まり)を返す。

    機種のルールは、その機種が「当てない」とする行(FortiGate では行をまたぐ値の本文。protected_lines)には当てない。
    本文はハッシュで比べるので、本文の中の行を書き換えると本文の変更が見えなくなるため(件数にも数えない)。
    """
    changed: dict[str, list[int]] = {r.id: [] for r in rules}
    protected = {p: _protected_lines(text, p) for p in {r.platform for r in rules if r.platform is not None}}
    lines: list[str] = []
    for line_no, original in enumerate(text.splitlines(), start=1):
        line = original
        for rule in rules:
            if rule.platform is not None and line_no in protected[rule.platform]:
                continue
            new = rule.pattern.sub(rule.replacement, line)
            if new != line:
                changed[rule.id].append(line_no)
                line = new
        lines.append(line)
    return lines, changed


def _protected_lines(text: str, platform: str) -> frozenset[int]:
    """機種の正規化ルールを当てない行の番号。機種がその関数を持たなければ空(全行に当てる)。"""
    flat = FLAT_PLATFORMS.get(platform)
    if flat is None or flat.protected_lines is None:
        return frozenset()
    return flat.protected_lines(text)


def apply_rules(text: str, rules: list[NormalizationRule]) -> tuple[list[str], dict[str, int]]:
    """正規化後の行と、ルール ID 別の適用行数を返す(効かなかったルールも 0 で載せる)。"""
    lines, changed = apply_rules_traced(text, rules)
    return lines, {rule_id: len(nos) for rule_id, nos in changed.items()}


def normalize_text(text: str, rules: list[NormalizationRule]) -> tuple[str, dict[str, int]]:
    """structured 比較の前に本文を正規化する。行数は変えない(安全網3が行数を数えるため)。

    返すのは、行を LF でつないだ本文と、ルール ID 別の適用行数。置換で行が割れたら ValueError。
    """
    lines, counts = apply_rules(text, rules)
    for line in lines:
        if line and line.splitlines() != [line]:
            raise ValueError("正規化ルールの置換で行が割れました(機種のルールは行数を変えてはいけません)")
    return "\n".join(lines), counts
