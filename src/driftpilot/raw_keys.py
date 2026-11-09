"""単純比較の設定で、行から比べるための鍵を作る(ADR-0166 決定3・4・5。REQ-227 §2・§3)。

単純比較は、機種のルールを当てた行(表示に使う行)と、比べるための鍵を分ける。鍵は無視する行・置き換え・空白・空行・
大文字小文字の設定から作り、None の行は比べない(無視した行)。差分に出す文字は元の行のまま。

このファイルは標準ライブラリだけを読む(driftpilot.diff_engine は hier_config を読むので読まない。画面の論理と CLI と
core が同じ部品を使う)。

Python の re には時間切れが無いので、重い式は形で断る(check_raw_options の nested_repeat)。形の判定をすり抜ける式は
残る穴(窓は閉じれば終わる。CLI は Ctrl+C)。正規表現を当てるのは行の先頭から MAX_MATCH_CHARS 文字まで。
"""

import importlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, get_args

WhitespaceMode = Literal["off", "trailing", "amount", "all"]
WHITESPACE_MODES: tuple[str, ...] = get_args(WhitespaceMode)
# 比べ方(ADR-0166 決定7。REQ-228 §4)。standard は difflib の SequenceMatcher(autojunk=False。今までと同じ)
Algorithm = Literal["standard", "minimal", "patience"]
ALGORITHMS: tuple[str, ...] = get_args(Algorithm)
# 「差分のみ表示」の前後の行数(ADR-0166 決定8。REQ-228 §2)。表示だけの値で RawOptions には入れない
RAW_CONTEXT_DEFAULT = 3
RAW_CONTEXT_MAX = 20
MAX_PATTERNS = 20  # 無視する行の式の数、置き換えの組の数(それぞれ)
MAX_PATTERN_CHARS = 200  # 式 1 つ、置き換える文字 1 つの文字数
MAX_MATCH_CHARS = 1000  # 正規表現を当てるのは行の先頭からこの文字数まで

ProblemField = Literal["ignore_lines", "replacements", "whitespace", "algorithm"]
ProblemKind = Literal[
    "too_many", "too_long", "bad_pattern", "bad_replacement", "matches_empty", "nested_repeat", "bad_value"
]
ProblemPart = Literal["pattern", "replacement"]

_PLAIN_KEYS = (
    "ignore_lines", "replacements", "whitespace", "ignore_blank_lines", "ignore_case", "detect_moves", "algorithm"
)
_REPLACEMENT_KEYS = ("pattern", "replacement")


@dataclass(frozen=True)
class Replacement:
    pattern: str
    replacement: str


@dataclass(frozen=True)
class RawOptions:
    """単純比較の設定。すべて既定なら、鍵は行そのもの(今までの単純比較と同じ)。"""

    ignore_lines: tuple[str, ...] = ()
    replacements: tuple[Replacement, ...] = ()
    whitespace: WhitespaceMode = "off"
    ignore_blank_lines: bool = False
    ignore_case: bool = False
    # REQ-228(ADR-0166 決定6・7)。どちらも鍵は変えない(行の移動の検出と比べ方)
    detect_moves: bool = False
    algorithm: Algorithm = "standard"

    def is_default(self) -> bool:
        return self == DEFAULT_RAW_OPTIONS

    def keys_default(self) -> bool:
        """鍵に効く 5 つの設定(無視する行・置き換え・空白・空行・大文字小文字)がすべて既定か。真なら鍵は行そのもの。"""
        return (
            not self.ignore_lines
            and not self.replacements
            and self.whitespace == "off"
            and not self.ignore_blank_lines
            and not self.ignore_case
        )

    def uses_regex(self) -> bool:
        """利用者の正規表現を使うか(無視する行か置き換えがあるとき)。"""
        return bool(self.ignore_lines or self.replacements)


DEFAULT_RAW_OPTIONS = RawOptions()


@dataclass(frozen=True)
class RawOptionProblem:
    """設定の誤り 1 件。式そのものは持たない(detail は re.error の文だけ)。

    part は置き換えの組のうち、式(pattern)と置き換える文字(replacement)のどちらの誤りか(判断の記録 1。
    §2 の形に足した項目)。無視する行・空白の誤りでは None。
    """

    field: ProblemField
    index: int | None  # 1 始まり。欄全体の誤りは None
    kind: ProblemKind
    detail: str | None = None  # re.error の文(bad_pattern・bad_replacement のときだけ)
    part: ProblemPart | None = None


class RawOptionsError(ValueError):
    """設定が使えない。problems は中身の誤り(形の誤りでは空)。文は場所と種類だけで、式は入れない。"""

    def __init__(self, message: str, problems: Sequence[RawOptionProblem] = ()) -> None:
        super().__init__(message)
        self.problems: list[RawOptionProblem] = list(problems)


# --- 重い式の断り(§3) ---


def _load_parser() -> Any:
    """re._parser を読む。読み込めない版の Python では None(重い式の確かめを飛ばす)。"""
    try:
        return importlib.import_module("re._parser")
    except ImportError:
        return None


def _has_nested_repeat(pattern: str) -> bool:
    """上限の無い繰り返しの中に、さらに繰り返しか選択(|)がある式か。re._parser が無ければ False。"""
    parser = _load_parser()
    if parser is None:
        return False
    try:
        tree = parser.parse(pattern)
    except Exception:  # 読めない式は bad_pattern で先に断っている
        return False
    names = {
        name: getattr(parser, name, None)
        for name in (
            "MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT", "SUBPATTERN", "BRANCH",
            "ASSERT", "ASSERT_NOT", "ATOMIC_GROUP", "GROUPREF_EXISTS",
        )
    }
    repeats = {names["MAX_REPEAT"], names["MIN_REPEAT"], names["POSSESSIVE_REPEAT"]} - {None}
    unbounded = getattr(parser, "MAXREPEAT", None)

    def walk(items: Any, inside: bool) -> bool:
        for op, av in items:
            if op in repeats:
                if inside:
                    return True
                if walk(av[2], av[1] == unbounded):
                    return True
            elif op == names["BRANCH"]:
                if inside:
                    return True
                if any(walk(branch, inside) for branch in av[1]):
                    return True
            elif op == names["SUBPATTERN"]:
                if walk(av[-1], inside):
                    return True
            elif op in (names["ASSERT"], names["ASSERT_NOT"]):
                if walk(av[1], inside):
                    return True
            elif op == names["ATOMIC_GROUP"] and op is not None:
                if walk(av, inside):
                    return True
            elif op == names["GROUPREF_EXISTS"] and op is not None:
                if walk(av[1], inside) or (av[2] is not None and walk(av[2], inside)):
                    return True
        return False

    return walk(tree, False)


# --- 確かめ(§2.1) ---


def _compile(pattern: str) -> tuple[re.Pattern[str] | None, str | None]:
    try:
        return re.compile(pattern), None
    except re.error as exc:
        return None, str(exc)
    except (RecursionError, OverflowError, ValueError) as exc:
        return None, type(exc).__name__


def _check_ignore(index: int, pattern: str) -> RawOptionProblem | None:
    def problem(kind: ProblemKind, detail: str | None = None) -> RawOptionProblem:
        return RawOptionProblem("ignore_lines", index, kind, detail)

    if len(pattern) > MAX_PATTERN_CHARS:
        return problem("too_long")
    compiled, error = _compile(pattern)
    if compiled is None:
        return problem("bad_pattern", error)
    # 重い式を一度も走らせないよう、空の文字に当てる前に形で断る
    if _has_nested_repeat(pattern):
        return problem("nested_repeat")
    if compiled.search("") is not None:
        return problem("matches_empty")
    return None


def _check_replacement(index: int, item: Replacement) -> RawOptionProblem | None:
    def problem(kind: ProblemKind, part: ProblemPart, detail: str | None = None) -> RawOptionProblem:
        return RawOptionProblem("replacements", index, kind, detail, part)

    if len(item.pattern) > MAX_PATTERN_CHARS:
        return problem("too_long", "pattern")
    if len(item.replacement) > MAX_PATTERN_CHARS:
        return problem("too_long", "replacement")
    if item.pattern == "":
        return problem("matches_empty", "pattern")
    compiled, error = _compile(item.pattern)
    if compiled is None:
        return problem("bad_pattern", "pattern", error)
    if _has_nested_repeat(item.pattern):
        return problem("nested_repeat", "pattern")
    try:
        compiled.sub(item.replacement, "")
    except re.error as exc:
        return problem("bad_replacement", "replacement", str(exc))
    except IndexError as exc:
        return problem("bad_replacement", "replacement", str(exc))
    return None


def check_raw_options(options: RawOptions) -> list[RawOptionProblem]:
    """設定の誤りの一覧(無ければ空)。式 1 つ・組 1 つにつき最初の 1 件だけを出す。"""
    problems: list[RawOptionProblem] = []
    if len(options.ignore_lines) > MAX_PATTERNS:
        problems.append(RawOptionProblem("ignore_lines", None, "too_many"))
    else:
        for i, pattern in enumerate(options.ignore_lines, start=1):
            if (found := _check_ignore(i, pattern)) is not None:
                problems.append(found)
    if len(options.replacements) > MAX_PATTERNS:
        problems.append(RawOptionProblem("replacements", None, "too_many"))
    else:
        for i, item in enumerate(options.replacements, start=1):
            if (found := _check_replacement(i, item)) is not None:
                problems.append(found)
    if options.whitespace not in WHITESPACE_MODES:
        problems.append(RawOptionProblem("whitespace", None, "bad_value"))
    if options.algorithm not in ALGORITHMS:
        problems.append(RawOptionProblem("algorithm", None, "bad_value"))
    return problems


def require_valid(options: RawOptions) -> None:
    """誤りがあれば RawOptionsError(入口で先に止める。ここは守り)。"""
    problems = check_raw_options(options)
    if problems:
        raise RawOptionsError(f"単純比較の設定に誤りがあります({len(problems)} 件)", problems)


# --- 保存・設定ファイルの形(§2.3) ---


def options_to_plain(options: RawOptions) -> dict[str, Any]:
    """7 つのキーを全部書く(REQ-227 の 5 つと、REQ-228 の detect_moves・algorithm)。"""
    return {
        "ignore_lines": list(options.ignore_lines),
        "replacements": [{"pattern": r.pattern, "replacement": r.replacement} for r in options.replacements],
        "whitespace": options.whitespace,
        "ignore_blank_lines": options.ignore_blank_lines,
        "ignore_case": options.ignore_case,
        "detect_moves": options.detect_moves,
        "algorithm": options.algorithm,
    }


def _shape_error(where: str, reason: str) -> RawOptionsError:
    return RawOptionsError(f"{where} {reason}")


def options_from_plain(data: object) -> RawOptions:
    """§2.3 の形を読む。無いキーは既定。形が違う・中身に誤りがあれば RawOptionsError(式は文に入れない)。"""
    if not isinstance(data, Mapping):
        raise _shape_error("raw_options", "は表(オブジェクト)にしてください")
    unknown = [k for k in data if k not in _PLAIN_KEYS]
    if unknown:
        raise _shape_error("raw_options", "に知らないキーがあります")
    ignore = data.get("ignore_lines", [])
    if not isinstance(ignore, list) or not all(isinstance(x, str) for x in ignore):
        raise _shape_error("raw_options.ignore_lines", "は文字列の配列にしてください")
    items = data.get("replacements", [])
    if not isinstance(items, list):
        raise _shape_error("raw_options.replacements", "は配列にしてください")
    replacements: list[Replacement] = []
    for i, item in enumerate(items):
        if (
            not isinstance(item, Mapping)
            or set(item) != set(_REPLACEMENT_KEYS)
            or not all(isinstance(item[k], str) for k in _REPLACEMENT_KEYS)
        ):
            raise _shape_error(f"raw_options.replacements[{i}]", "は pattern と replacement の文字列だけの表にしてください")
        replacements.append(Replacement(item["pattern"], item["replacement"]))
    whitespace = data.get("whitespace", "off")
    if not isinstance(whitespace, str):
        raise _shape_error("raw_options.whitespace", "は文字列にしてください")
    algorithm = data.get("algorithm", "standard")
    if not isinstance(algorithm, str):
        raise _shape_error("raw_options.algorithm", "は文字列にしてください")
    flags = {}
    for key in ("ignore_blank_lines", "ignore_case", "detect_moves"):
        value = data.get(key, False)
        if type(value) is not bool:
            raise _shape_error(f"raw_options.{key}", "は true か false にしてください")
        flags[key] = value
    options = RawOptions(
        ignore_lines=tuple(ignore),
        replacements=tuple(replacements),
        whitespace=whitespace,  # type: ignore[arg-type]  # 値は check_raw_options が確かめる
        algorithm=algorithm,  # type: ignore[arg-type]  # 値は check_raw_options が確かめる
        **flags,
    )
    problems = check_raw_options(options)
    if problems:
        kinds = sorted({p.kind for p in problems})
        raise RawOptionsError(f"raw_options の中身が使えません({', '.join(kinds)})", problems)
    return options


# --- 鍵(§2.2) ---

_SPACES = re.compile(r"[ \t]+")


def line_keys(lines: Sequence[str], options: RawOptions) -> list[str | None]:
    """行ごとの比べる鍵。None は無視した行。すべて既定なら行そのもの。

    順番: 無視する行 → 置き換え(上から順) → 空白 → 空行 → 大文字小文字。正規表現は行の先頭 MAX_MATCH_CHARS 文字だけに当てる。
    行の移動の検出と比べ方(REQ-228)は鍵を変えないので、鍵に効く 5 つが既定なら行そのもの。
    """
    if options.keys_default():
        return list(lines)
    ignores = [re.compile(p) for p in options.ignore_lines]
    replacements = [(re.compile(r.pattern), r.replacement) for r in options.replacements]
    keys: list[str | None] = []
    for line in lines:
        head = line[:MAX_MATCH_CHARS]
        if any(p.search(head) for p in ignores):
            keys.append(None)
            continue
        key = line
        for pattern, text in replacements:
            key = pattern.sub(text, key[:MAX_MATCH_CHARS]) + key[MAX_MATCH_CHARS:]
        if options.whitespace == "trailing":
            key = key.rstrip(" \t")
        elif options.whitespace == "amount":
            key = _SPACES.sub(" ", key).rstrip(" ")
        elif options.whitespace == "all":
            key = _SPACES.sub("", key)
        if options.ignore_blank_lines and not key.strip(" \t"):
            keys.append(None)
            continue
        if options.ignore_case:
            key = key.casefold()
        keys.append(key)
    return keys


def compute_keys(
    before: Sequence[str], after: Sequence[str], options: RawOptions
) -> tuple[list[str | None], list[str | None]]:
    """両側の鍵(line_keys を 2 回呼ぶだけ。今のプロセス・今のスレッド)。"""
    return line_keys(before, options), line_keys(after, options)
