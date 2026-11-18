"""1 つの config を「道すじ・値」の一覧にする(ADR-0223。REQ-319)。

入口(pilot-call config-facts)が読み込んだテキストを受け取り、compare の `kind: config` と同じ切り分け・機種の自動の後、
IR(共通スキーマ)を葉まで辿った一覧を作る。返すのは、伏せない result.json の中身と、AI に渡す伏せた masked_facts.json の中身。

- 切り分け: split_log の後、select で区間を 1 つにする。区間が 2 つ以上なら FactsError("multiple_segments")。
- 機種: auto なら detect_platform を両側同じ値として combine_platforms に渡す(compare と同じ)。指定ならその名前。
- 読み取り: parse_config(本文, 機種)。例外は型の名前だけを持つ FactsError("parse_failed")(中身・例外の文は持たない)。
  正規化ルールは当てない(一覧は config に書かれた値のまま)。
- 一覧: IR の型(get_type_hints)と一緒に辿る。TypedDict は欄の名前、そのほかの dict(と Any)はキー、辞書を含むリストは番号。
  葉は文字列・数・真偽(IP アドレスの型などは str にする)と、それだけを持つリスト・タプル・集合。
  None・空のリスト・タプル・集合・辞書・空の文字列は出さない。False と 0 は出す。
- 伏せる: 判定は llm_analyzer.payload.FactMasker(今の構造化の差分の判定を包んだもの)を呼ぶだけ。新しい決まりは持たない。

messages は読まない(循環を避けるため)。誤りの文は入口が code と FactsError の中身から作る。依頼や入力の中身は持ち出さない。
"""

import json
import types
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Union, get_args, get_origin, get_type_hints, is_typeddict

from driftpilot.config_parser import parse_config
from driftpilot.config_parser.family_hint import AUTO_PLATFORM, detect_platform
from driftpilot.config_parser.ir import IR
from driftpilot.config_parser.platforms import patterns_fingerprint
from driftpilot.core.compare import (
    _input_forms,
    _input_hints,
    _pager_marks,
    _platform_auto,
    _platform_mismatch,
    combine_platforms,
)
from driftpilot.core.types import CompareRequest, SideInput
from driftpilot.diff_engine import SafetyWarning
from driftpilot.llm_analyzer.mask_patterns import masking_fingerprint
from driftpilot.llm_analyzer.payload import FactMasker
from driftpilot.segmentation import Candidates, InputNote, SelectionError, select, split_log

SCHEMA = "driftpilot.config-facts"
SCHEMA_VERSION = "1"

_EXCLUDED_TOP = ("vendor_specific", "unparsed")
# 型に無い欄(IR に型を足し忘れた欄)の総称のパス。何も決めていない値なので vendor_specific と同じく行として判定する(伏せ漏れを防ぐ)
_UNTYPED = "vendor_specific.*"
# 名前が行の中にあるかを見る範囲(今の構造化の差分と同じ。ACL の名前と ipsec の塊の鍵)
_NAMED_ACLS = ("acls", "ipv6_acls")


class FactsError(Exception):
    """一覧を作れなかった。code は multiple_segments か parse_failed。count は区間の数、error_type は例外の型の名前だけ。"""

    def __init__(self, code: str, *, count: int = 0, error_type: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.count = count
        self.error_type = error_type


@dataclass(frozen=True)
class Fact:
    """一覧の 1 件。generic は葉の総称のパス(fields.py の書き方。JSON には出さない)。"""

    path: tuple[str | int, ...]
    path_text: str
    value: Any
    generic: str

    def as_json(self, value: Any = None) -> dict[str, Any]:
        return {"path": list(self.path), "path_text": self.path_text, "value": self.value if value is None else value}


@dataclass(frozen=True)
class FactsOutcome:
    """result_text は result.json、masked_text は masked_facts.json の中身(末尾の改行を含む)。"""

    result_text: str
    masked_text: str
    used: str | None
    counts: dict[str, int]
    unsent: int
    fingerprints: dict[str, str]


# --- IR を葉まで辿る ---


@lru_cache(maxsize=None)
def _fields_of(hint: Any) -> dict[str, Any] | None:
    return get_type_hints(hint) if is_typeddict(hint) else None


def _strip_optional(hint: Any) -> Any:
    """`X | None` は X。ほかの和は、入れ物の型があればそれ、なければ Any。"""
    if get_origin(hint) in (Union, types.UnionType):
        args = [a for a in get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        return next((a for a in args if is_typeddict(a) or get_origin(a) in (dict, list)), Any)
    return hint


def _dict_hint(hint: Any) -> Any:
    args = get_args(hint) if get_origin(hint) is dict else ()
    return args[1] if len(args) == 2 else Any


def _item_hint(hint: Any) -> Any:
    args = get_args(hint) if get_origin(hint) in (list, tuple, set, frozenset) else ()
    return args[0] if args else Any


def _is_container(value: Any) -> bool:
    return isinstance(value, (dict, list, tuple, set, frozenset))


def _is_scalar(value: Any) -> bool:
    return value is not None and not _is_container(value)


def _json_scalar(value: Any) -> Any:
    """JSON にできる値へ(文字列・数・真偽はそのまま、IP アドレスの型などは str。compare の result.json と同じ)。"""
    return value if isinstance(value, (str, int, float, bool)) else str(value)


def _walk(value: Any, hint: Any, parts: tuple[str | int, ...], text: str, generic: str, out: list[Fact]) -> None:
    if value is None:
        return
    hint = _strip_optional(hint)
    if isinstance(value, dict):
        fields = _fields_of(hint)
        for key, item in value.items():
            name = str(key)
            if fields is not None:
                known = name in fields
                child_hint = fields.get(name, Any)
                child_generic = (f"{generic}.{name}" if generic else name) if known else _UNTYPED
                child_text = f"{text}.{name}" if text else name
            else:
                child_hint = _dict_hint(hint)
                child_generic = f"{generic}.*"
                child_text = f"{text}[{json.dumps(name, ensure_ascii=False)}]"
            _walk(item, child_hint, (*parts, name), child_text, child_generic, out)
        return
    if isinstance(value, (list, tuple, set, frozenset)):
        items = sorted(value, key=str) if isinstance(value, (set, frozenset)) else list(value)
        if not items:
            return
        if all(_is_scalar(item) for item in items):
            out.append(Fact(parts, text, [_json_scalar(item) for item in items], generic))
            return
        item_hint = _item_hint(hint)
        for index, item in enumerate(items):
            _walk(item, item_hint, (*parts, index), f"{text}[{index}]", f"{generic}[]", out)
        return
    if isinstance(value, str) and not value:
        return
    out.append(Fact(parts, text, _json_scalar(value), generic))


def facts_of(ir: dict[str, Any]) -> list[Fact]:
    """IR の共通スキーマ(vendor_specific・unparsed を除くトップのキー)の一覧。IR を辿った順。"""
    hints = get_type_hints(IR)
    out: list[Fact] = []
    for key, value in ir.items():
        if key in _EXCLUDED_TOP:
            continue
        _walk(value, hints.get(key, Any), (key,), key, key, out)
    return out


def vendor_facts_of(ir: dict[str, Any]) -> list[Fact]:
    """vendor_specific の一覧。キーはどれも ["キー"]、先頭は vendor_specific。"""
    out: list[Fact] = []
    _walk(ir.get("vendor_specific"), dict[str, Any], ("vendor_specific",), "vendor_specific", "vendor_specific", out)
    return out


def unparsed_of(ir: dict[str, Any]) -> list[list[str]]:
    """読めなかった行。要素ごとに、祖先の行から順に並べた行の並び(最後がその行)。"""
    return [[str(line) for line in lines] for lines in ir.get("unparsed", [])]


# --- 知らせ(compare と同じ形・同じ判定) ---


def _notice_json(warning: SafetyWarning) -> dict[str, Any]:
    return {"kind": warning.kind, "side": None, "category": warning.category, "detail": warning.detail}


def _notices(
    text: str, name: str, notes: Sequence[InputNote], body: str, split_pager: Any, platform: str, auto: Any
) -> list[dict[str, Any]]:
    """compare の extra と同じ並び(input_form・input_hint・platform_auto か platform_mismatch・pager_marks)。

    1 つの入力は compare の変更前の側として判定し、その側の知らせだけを取って side を null にする。判定は書き直さない。
    """
    request = CompareRequest(
        before=SideInput(text, name, tuple(notes)), after=SideInput("", ""), kind="config", platform=platform
    )
    platform_notes = (
        _platform_auto(auto)
        if auto is not None
        else _platform_mismatch(platform, {"before": body, "after": ""})
    )
    found = [
        *_input_forms(request),
        *_input_hints(request),
        *platform_notes,
        *_pager_marks({"before": split_pager, "after": split_log("")}),
    ]
    return [_notice_json(w) for w in found if w.side in ("before", None)]


# --- 伏せた一覧 ---


def _in_block(fact: Fact, masker: FactMasker, cache: dict[tuple[str, str], bool]) -> bool:
    """名前が割れた秘密の行のかたまり・PEM のブロックの行の中にある ACL・ipsec の塊の下の葉か(今の構造化の差分と同じ範囲)。"""
    path = fact.path
    if len(path) >= 2 and path[0] in _NAMED_ACLS:
        kind, name = "acl", str(path[1])
    elif len(path) >= 3 and path[0] == "ipsec" and path[1] == "blocks":
        kind, name = "ipsec", str(path[2])
    else:
        return False
    if (kind, name) not in cache:
        if kind == "acl":
            cache[(kind, name)] = masker.name_in_block(name)
        else:
            cache[(kind, name)] = masker.key_in_block(name)
    return cache[(kind, name)]


def _masked_facts(facts: Sequence[Fact], masker: FactMasker, cache: dict[tuple[str, str], bool]) -> tuple[list[Fact], int]:
    """伏せた一覧と、入れなかった値の数。入れなかった葉の値は、どこにも書かない。"""
    kept: list[Fact] = []
    unsent = 0
    for fact in facts:
        if _in_block(fact, masker, cache):
            # 入れなかった値を 1 つずつ数える(リストの葉は要素の数。判定で要素を落としたときと同じ数え方)
            unsent += len(fact.value) if isinstance(fact.value, list) else 1
            continue
        judged = masker.value(fact.value, fact.generic)
        unsent += judged.dropped
        if judged.sent:
            kept.append(Fact(fact.path, fact.path_text, judged.value, fact.generic))
    return kept, unsent


def _dumps(obj: dict[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def config_facts(
    text: str,
    name: str,
    notes: Sequence[InputNote],
    platform: str,
    words: Sequence[str] = (),
) -> FactsOutcome:
    """1 つの config を一覧にする。失敗は FactsError(multiple_segments・parse_failed)。"""
    split = split_log(text)
    try:
        chosen = select(split.segments)
    except SelectionError as exc:
        raise FactsError("parse_failed", error_type=type(exc).__name__) from None
    if isinstance(chosen, Candidates):
        raise FactsError("multiple_segments", count=len(chosen.segments))
    body = chosen.text

    auto = None
    if platform == AUTO_PLATFORM:
        detected = detect_platform(body)
        auto = combine_platforms(detected, detected)
        used = auto.used
    else:
        used = platform
    notices = _notices(text, name, notes, body, split, platform, auto)
    try:
        ir = parse_config(body, used)
    except Exception as exc:  # noqa: BLE001 - 中身・例外の文は持ち出さず、型の名前だけを返す
        raise FactsError("parse_failed", error_type=type(exc).__name__) from None

    facts = facts_of(ir)
    vendor = vendor_facts_of(ir)
    unparsed = unparsed_of(ir)
    hostname = ir["global"]["hostname"] or None
    platform_json = {"requested": platform, "used": used}
    fingerprints = {"patterns": patterns_fingerprint(used).digest, "masking": masking_fingerprint().digest}
    counts = {"facts": len(facts), "vendor_specific": len(vendor), "unparsed": len(unparsed)}
    result = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "device": {"hostname": hostname},
        "platform": platform_json,
        "notices": notices,
        "fingerprints": fingerprints,
        "facts": [f.as_json() for f in facts],
        "vendor_specific": [f.as_json() for f in vendor],
        "unparsed": [{"lines": lines} for lines in unparsed],
        "counts": counts,
    }

    # 伏せた一覧: 判定器は 1 つ。facts → vendor_specific → unparsed の順に通す(同じ値は同じ <secret:N>)
    masker = FactMasker(body.splitlines(), words, used)
    cache: dict[tuple[str, str], bool] = {}
    kept_facts, unsent = _masked_facts(facts, masker, cache)
    kept_vendor, unsent_vendor = _masked_facts(vendor, masker, cache)
    unsent += unsent_vendor
    kept_unparsed: list[dict[str, Any]] = []
    for lines in unparsed:
        judged = masker.unparsed(lines)
        if judged is None:
            unsent += 1
            continue
        shown, omitted = judged
        unsent += omitted
        kept_unparsed.append({"lines": shown})
    masked_host = next((f.value for f in kept_facts if f.path == ("global", "hostname")), None)
    masked = {
        "fingerprints": {"masking": fingerprints["masking"], "patterns": fingerprints["patterns"]},
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "device": {"hostname": masked_host},
        "platform": platform_json,
        "notices": notices,
        "facts": [f.as_json() for f in kept_facts],
        "vendor_specific": [f.as_json() for f in kept_vendor],
        "unparsed": kept_unparsed,
        "counts": {
            "facts": len(kept_facts),
            "vendor_specific": len(kept_vendor),
            "unparsed": len(kept_unparsed),
            "unsent": unsent,
        },
    }
    return FactsOutcome(_dumps(result), _dumps(masked), used, counts, unsent, fingerprints)
