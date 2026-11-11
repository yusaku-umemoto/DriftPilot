"""利用者ごとの設定ファイル(config.toml)を設定画面から書く(REQ-93 / ADR-0071 決定2〜4)。

書けるのは [llm] の max_diffs・max_total_diffs・extra_secret_words(WRITABLE)と、
[gui] の auto_compare・character・auto_update_check(GUI_WRITABLE。update_gui_items。ADR-0076 決定3・ADR-0174 決定1・ADR-0188 決定2)と、
[gui] の auto_send_llm(update_gui_auto_send。ADR-0080 決定2。設定画面がオンにする前に確かめを出す)と、
[gui] の theme(update_gui_theme。ADR-0189。"system"・"light"・"dark" だけ。真偽値とは別の口)だけ。
api_key は消す方向だけ(remove_api_key)。prompt_template は消す関数だけを残す(remove_llm_item。ADR-0075 決定3)。
AI への指示文は指示文のプロファイル(prompt-profiles.json)に移ったので、prompt_template は書かず、読みもしない(ADR-0079 決定5)。
model・base_url・api_key を書く関数は作らない(ADR-0071 決定2。平文のキーをツールが書く経路を作らない)。

- tomlkit で読み、変える項目だけを置き換え・足し・消す。コメント・並び・空行・ほかの項目・ほかの表は変えない
  (伏せる語の配列は丸ごと置き換えず、変わった語だけを足し・消す。配列の中のコメントと空行も残す)
- 読み込み(tomllib)が受け付ける書き方は、書くときも扱う: [llm] が点で区切ったキー(llm.model = …)で書いてあるとき、
  子の表([llm.sub])が先にあるときなど、[llm] が文書の中で分かれていても、その項目のある部分だけを書き換える
- 書く直前に読み直し、画面が読んだときの内容のハッシュ(sha256)と違えば書かない(ConfigChanged。決定4)
- 同じフォルダの一時ファイルに UTF-8・改行 \\n で書いてから os.replace で置き換える(途中で落ちても元のファイルが残る。決定3)
- ファイルが無ければ [llm] と変えた項目だけのファイルを作る([gui] も同じ)。[gui] が無ければ文書の最後に足す

値の決まりは読み込み(llm_settings)と同じ関数で確かめる。キーの値はどの戻り値の表示・文にも入れない
(read_api_key の戻り値にだけ入る)。例外の文は場所と理由だけで、tomlkit の例外の本文は使わない。
画面のモジュールはファイルを書かない。このモジュールの関数を settings_model が呼ぶ(ADR-0022)。
"""

import difflib
import hashlib
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import tomlkit
from tomlkit.container import OutOfOrderTableProxy
from tomlkit.items import Array, InlineTable, Table, Whitespace

from driftpilot.llm_settings import (
    AUTO_COMPARE_KEY,
    AUTO_SEND_LLM_KEY,
    AUTO_UPDATE_CHECK_KEY,
    CHARACTER_KEY,
    GUI_SECTION,
    PROMPT_TEMPLATE_KEY,
    SECTION,
    LANGUAGE_KEY,
    LANGUAGES,
    THEME_KEY,
    THEMES,
    SettingsError,
    auto_compare_of,
    auto_send_llm_of,
    auto_update_check_of,
    character_of,
    is_limit,
    language_of,
    is_secret_word,
    limit_of,
    parse_section,
    read_bytes,
    theme_of,
    words_of,
)
from driftpilot.messages import CONFIG_CHANGED_ELSEWHERE, gui_language_invalid
from driftpilot.messages import UCW_TEXTS as _T

# 画面から書ける項目(ADR-0071 決定1)。これ以外の名前は update_llm_items が断る(prompt_template は ADR-0079 決定5 で外した)
WRITABLE = ("max_diffs", "max_total_diffs", "extra_secret_words")
# 消す関数だけを残す項目(ADR-0075 決定3。REQ-116 §2 で関数は残す)。api_key は remove_api_key だけで消す
REMOVABLE = (PROMPT_TEMPLATE_KEY,)
# 画面から書ける [gui] の項目(ADR-0076 決定3。character は ADR-0174 決定1、auto_update_check は ADR-0188 決定2)。[llm] の WRITABLE とは混ぜない。これ以外の名前は update_gui_items が断る
GUI_WRITABLE = (AUTO_COMPARE_KEY, CHARACTER_KEY, AUTO_UPDATE_CHECK_KEY)


class ConfigChanged(SettingsError):
    """画面が読んだ後に、設定ファイルがほかで変わった(書いていない)。"""


@dataclass(frozen=True)
class UserConfigSnapshot:
    """設定ファイルを読んだときの内容。digest はバイト列の sha256(ファイルが無ければ None)。

    上限は書いてあるときだけ値(無ければ None)。キーは有無だけを持つ。指示文のひな形は読まない(ADR-0079 決定5)。
    auto_compare は [gui] の自動の比較(無ければ真。形が違えば偽で、比較の画面と同じ決まり。ADR-0076 決定3)。
    auto_send_llm は [gui] の自動送信(true のときだけ真。比較の画面と同じ決まり。ADR-0080 決定2)。
    character は [gui] のキャラクターの絵(true のときだけ真。ADR-0174 決定1)。
    auto_update_check は [gui] の自動アップデートチェック(true のときだけ真。ADR-0188 決定2)。
    theme は [gui] の画面の色("system"・"light"・"dark"。ほかは "system"。ADR-0189)。
    language は [gui] の画面の言語("en"・"ja"・"system"。ほかは "en"。ADR-0190)。
    """

    path: Path
    digest: str | None
    max_diffs: int | None = None
    max_total_diffs: int | None = None
    extra_secret_words: tuple[str, ...] = ()
    has_api_key: bool = False
    auto_compare: bool = True
    auto_send_llm: bool = False
    character: bool = False
    auto_update_check: bool = False
    theme: str = "system"
    language: str = "en"


@dataclass(frozen=True)
class PlaintextKey:
    """設定ファイルの平文のキーと、読んだときのハッシュ(「保管庫に移す」で使う)。key は repr に出さない。"""

    digest: str | None
    key: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"PlaintextKey(digest={self.digest!r}, has_key={self.key is not None})"


def digest_of(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str | None:
    """今の設定ファイルのハッシュ(無ければ None)。読めなければ SettingsError。"""
    return digest_of(read_bytes(path))


def _file_key(section: Mapping[str, object]) -> str | None:
    value = section.get("api_key")
    return value.strip() if isinstance(value, str) and value.strip() else None


def read_user_config(path: Path) -> UserConfigSnapshot:
    """設定ファイルを読む(書かない)。値の形が違えば、読み込み(load_settings)と同じ SettingsError。"""
    data = read_bytes(path)
    section = parse_section(data, path)
    return UserConfigSnapshot(
        path=path,
        digest=digest_of(data),
        max_diffs=limit_of(section, "max_diffs", path),
        max_total_diffs=limit_of(section, "max_total_diffs", path),
        extra_secret_words=words_of(section, path),
        has_api_key=_file_key(section) is not None,
        auto_compare=auto_compare_of(data),
        auto_send_llm=auto_send_llm_of(data),
        character=character_of(data),
        auto_update_check=auto_update_check_of(data),
        theme=theme_of(data),
        language=language_of(data),
    )


def read_api_key(path: Path) -> PlaintextKey:
    """設定ファイルの平文のキー(前後の空白は除く)と、同じバイト列のハッシュ。"""
    data = read_bytes(path)
    return PlaintextKey(digest_of(data), _file_key(parse_section(data, path)))


def _check(values: Mapping[str, object], path: Path) -> dict[str, object]:
    """書く値を確かめる。WRITABLE 以外の名前は ValueError(名前だけを出す)。値の形は読み込みと同じ決まり。"""
    unknown = sorted(set(values) - set(WRITABLE))
    if unknown:
        raise ValueError(_T["not_writable_item"].format(names=", ".join(unknown)))
    checked: dict[str, object] = {}
    for key, value in values.items():
        if key == "extra_secret_words":
            words = list(value) if isinstance(value, (list, tuple)) else value
            if not isinstance(words, list) or not all(is_secret_word(w) for w in words):
                raise SettingsError(_T["extra_secret_words"].format(path=path, section=SECTION))
            checked[key] = words
        else:
            if not is_limit(value):
                raise SettingsError(_T["positive_integer"].format(path=path, section=SECTION, key=key))
            checked[key] = value
    return checked


def _load(path: Path, expected_digest: str | None) -> tuple[bytes | None, tomlkit.TOMLDocument]:
    """書く直前に読み直す。ハッシュが違えば ConfigChanged。改行は \\n にそろえてから読む(ADR-0071 決定3)。"""
    data = read_bytes(path)
    if digest_of(data) != expected_digest:
        raise ConfigChanged(CONFIG_CHANGED_ELSEWHERE)
    if data is None:
        return None, tomlkit.document()
    parse_section(data, path)  # 読み込みと同じ決まりで、TOML として読めるか・[llm] が表かを先に見る
    try:
        text = data.decode("utf-8").replace("\r\n", "\n")
        return data, tomlkit.parse(text)
    except Exception:  # noqa: BLE001 - tomlkit の例外の本文は使わない(値が入りうるため)
        raise SettingsError(_T["toml_unreadable"].format(path=path)) from None


Section = Table | InlineTable | OutOfOrderTableProxy


def _section(doc: tomlkit.TOMLDocument, path: Path, *, create: bool, name: str = SECTION) -> Section | None:
    """[llm](name で [gui] も)の表。文書の中で分かれている表(点で区切ったキー・子の表が先など)は、tomlkit がまとめた形で返る。

    無くて create なら文書の最後に足す。
    """
    table = doc.get(name)
    if table is None:
        if not create:
            return None
        table = tomlkit.table()
        doc[name] = table
        return doc[name]
    if not isinstance(table, (Table, InlineTable, OutOfOrderTableProxy)):
        raise SettingsError(_T["not_table"].format(path=path, name=name))
    return table


def _parts(doc: tomlkit.TOMLDocument, table: Section, name: str = SECTION) -> list[Table | InlineTable]:
    """[llm](name で [gui] も)の表の、文書の中の部分(書いてある順)。分かれていなければ、その表だけ。

    項目を書き換える・消すのは、その項目がある部分に直接行う。まとめた形を通して消すと、空になった部分の
    見出し([llm] の行)まで消えるため。
    """
    if isinstance(table, OutOfOrderTableProxy):
        return [
            item
            for key, item in doc.body
            if key is not None and key.key == name and isinstance(item, (Table, InlineTable))
        ]
    return [table]


# --- 伏せる語の配列を、変わった語だけ足し・消す(配列の中のコメントと空行を残す) ---
# tomlkit の配列は、語ごとに (前の空白, 値, カンマ, 行末のコメント) の組を _value に持つ。語の前の空行は、その語の
# 「前の空白」に入っている。tomlkit の足す・消すだけでは、消した語の前の空行が一緒に消え、間に足した語は後ろの語の
# 空行を写すので、この2つだけを _value を見て直す(tomlkit は pyproject.toml で 0.15.2 に固定。出力はテストで確かめる)


def _blank_lines(indent: Whitespace | None) -> str:
    """語の前の空白のうち、最後の改行より前(空行の分)。空行が無ければ空。"""
    text = indent.s if indent is not None else ""
    return text[: text.rfind("\n")] if text.count("\n") >= 2 else ""


def _delete_word(array: Array, index: int) -> None:
    """index 番目の語を消す(その語の行と、行末のコメント)。語の前にあった空行は、次の語の前に移して残す。"""
    position = array._index_map[index]
    blank = _blank_lines(array._value[position].indent)
    following = array._value[position + 1] if position + 1 < len(array._value) else None
    keep = blank and following is not None and following.indent is not None and "\n" in following.indent.s
    del array[index]
    if keep:
        following.indent = Whitespace(blank + following.indent.s)


def _insert_word(array: Array, index: int, word: str) -> None:
    """index 番目に語を足す。前の空白は写した先の空行を除き、改行と字下げだけにする(空行を増やさない)。"""
    array.insert(index, word)
    group = array._value[array._index_map[index]]
    if _blank_lines(group.indent):
        group.indent = Whitespace(group.indent.s[group.indent.s.rfind("\n") :])


def _replace_word(array: Array, index: int, word: str) -> None:
    """index 番目の語を入れ替える。値だけを替え、前の空白(空行と字下げ)・カンマ・行末のコメントはそのまま残す。"""
    array[index] = word


def _edit_words(array: Array, words: Sequence[str]) -> None:
    """配列を words にする。残る語の行(行末のコメントを含む)と、語の間のコメント・空行はそのまま。

    今の並びと新しい並びの差(difflib)を、後ろから順に当てる(前の語の位置が変わらないように)。
    別の語に変わったところは、その場所で語の値だけを入れ替える(前の空行と行末のコメントは変えない。
    依頼書 §3「コメント・並び・空行を変えない」)。数が違う分は消す・足す。
    """
    old = [str(w) for w in array]
    for tag, i1, i2, j1, j2 in reversed(difflib.SequenceMatcher(None, old, list(words), autojunk=False).get_opcodes()):
        if tag == "equal":
            continue
        common = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for k in range(common):
            _replace_word(array, i1 + k, words[j1 + k])
        for index in reversed(range(i1 + common, i2)):
            _delete_word(array, index)
        for offset, word in enumerate(words[j1 + common : j2]):
            _insert_word(array, i1 + common + offset, word)


def _write(path: Path, text: str) -> str:
    """同じフォルダの一時ファイルに書いてから os.replace で置き換える。返すのは書いた内容のハッシュ。

    失敗したら一時ファイルを消し、元のファイルはそのまま残す(SettingsError。文は場所と理由だけ)。
    一時ファイルは mkstemp で作るので、置き換えた後のファイルは持ち主だけが読める権限になる。
    """
    data = text.encode("utf-8")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    except OSError as exc:
        raise SettingsError(_T["cannot_write"].format(path=path, reason=exc.strerror or type(exc).__name__)) from None
    temp = Path(name)
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        os.replace(temp, path)
    except OSError as exc:
        temp.unlink(missing_ok=True)
        raise SettingsError(_T["cannot_write"].format(path=path, reason=exc.strerror or type(exc).__name__)) from None
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return digest_of(data)


def update_llm_items(path: Path, expected_digest: str | None, values: Mapping[str, object]) -> str:
    """[llm] の決まった項目(WRITABLE)だけを置き換える・足す。返すのは書いた後のハッシュ。

    expected_digest は画面が読んだときのハッシュ(ファイルが無かったときは None)。違えば書かない(ConfigChanged)。
    既定と同じ値でも、渡した項目は書いて残す(ADR-0071 決定3)。
    """
    checked = _check(values, path)
    _, doc = _load(path, expected_digest)
    parts = _parts(doc, _section(doc, path, create=True))
    added: dict[str, object] = {}
    for key, value in checked.items():
        part = next((p for p in parts if key in p), None)
        if part is None:
            added[key] = value
        elif key == "extra_secret_words" and isinstance(part[key], Array):
            _edit_words(part[key], value)
        else:
            part[key] = value  # 置き換えは、その行末のコメントを残す
    if added:
        # 無い項目は [llm] に足す。分かれた表では、値の行がある部分を tomlkit が選ぶ(部分を直した後の形で取り直す)
        table = _section(doc, path, create=True)
        for key, value in added.items():
            table[key] = value
    return _write(path, tomlkit.dumps(doc))


def update_gui_items(path: Path, expected_digest: str | None, values: Mapping[str, object]) -> str:
    """[gui] の決まった項目(GUI_WRITABLE)だけを置き換える・足す。返すのは書いた後のハッシュ(ADR-0076 決定3)。

    GUI_WRITABLE 以外の名前は ValueError(名前だけを出す)。値が真偽値でなければ SettingsError。
    expected_digest は画面が読んだときのハッシュ。違えば書かない(ConfigChanged)。[gui] が無ければ文書の最後に足す。
    """
    unknown = sorted(set(values) - set(GUI_WRITABLE))
    if unknown:
        raise ValueError(_T["not_writable_item"].format(names=", ".join(unknown)))
    return _write_gui(path, expected_digest, values)


def update_gui_auto_send(path: Path, expected_digest: str | None, value: bool) -> str:
    """[gui] の auto_send_llm だけを置き換える・足す。返すのは書いた後のハッシュ(ADR-0080 決定2)。

    GUI_WRITABLE とは別の関数にする(オンにする前の確かめは設定画面が出す。この関数は確かめない)。
    値が真偽値でなければ SettingsError。ハッシュが違えば書かない(ConfigChanged)。
    """
    return _write_gui(path, expected_digest, {AUTO_SEND_LLM_KEY: value})


def update_gui_theme(path: Path, expected_digest: str | None, theme: object) -> str:
    """[gui] の theme だけを置き換える・足す。返すのは書いた後のハッシュ(ADR-0189)。

    真偽値の項目(GUI_WRITABLE)とは別の口。theme が "system"・"light"・"dark" のどれでもなければ SettingsError。
    ハッシュが違えば書かない(ConfigChanged)。コメント・並びは、ほかの [gui] の項目と同じく保つ。
    """
    if not isinstance(theme, str) or theme not in THEMES:
        raise SettingsError(_T["one_of"].format(path=path, section=GUI_SECTION, key=THEME_KEY, choices=", ".join(THEMES)))
    return _put_gui(path, expected_digest, {THEME_KEY: theme})


def update_gui_display(path: Path, expected_digest: str | None, values: Mapping[str, object]) -> str:
    """[gui] の theme と language のうち、渡したものだけを 1 回で置き換える・足す。返すのは書いた後のハッシュ(ADR-0190)。

    values の名前は theme・language だけ(ほかは ValueError)。値が 3 つのどれかでなければ SettingsError(書かない)。
    ハッシュが違えば書かない(ConfigChanged)。コメント・並びは、ほかの [gui] の項目と同じく保つ。
    """
    unknown = sorted(set(values) - {THEME_KEY, LANGUAGE_KEY})
    if unknown:
        raise ValueError(_T["not_writable_item"].format(names=", ".join(unknown)))
    theme = values.get(THEME_KEY)
    if THEME_KEY in values and (not isinstance(theme, str) or theme not in THEMES):
        raise SettingsError(_T["one_of"].format(path=path, section=GUI_SECTION, key=THEME_KEY, choices=", ".join(THEMES)))
    language = values.get(LANGUAGE_KEY)
    if LANGUAGE_KEY in values and (not isinstance(language, str) or language not in LANGUAGES):
        raise SettingsError(gui_language_invalid(path))
    return _put_gui(path, expected_digest, dict(values))


def _write_gui(path: Path, expected_digest: str | None, values: Mapping[str, object]) -> str:
    """[gui] の真偽値の項目を置き換える・足す(名前は呼ぶ側で確かめる)。値が真偽値でなければ SettingsError。"""
    for key, value in values.items():
        if not isinstance(value, bool):
            raise SettingsError(_T["true_or_false"].format(path=path, section=GUI_SECTION, key=key))
    return _put_gui(path, expected_digest, values)


def _put_gui(path: Path, expected_digest: str | None, values: Mapping[str, object]) -> str:
    """[gui] の項目を置き換える・足す(名前と値は呼ぶ側で確かめる)。[gui] が無ければ文書の最後に足す。"""
    _, doc = _load(path, expected_digest)
    parts = _parts(doc, _section(doc, path, create=True, name=GUI_SECTION), GUI_SECTION)
    added: dict[str, object] = {}
    for key, value in values.items():
        part = next((p for p in parts if key in p), None)
        if part is None:
            added[key] = value
        else:
            part[key] = value  # 置き換えは、その行末のコメントを残す
    if added:
        table = _section(doc, path, create=True, name=GUI_SECTION)
        for key, value in added.items():
            table[key] = value
    return _write(path, tomlkit.dumps(doc))


def remove_api_key(path: Path, expected_digest: str | None) -> str:
    """[llm] の api_key の行だけを消す(「保管庫に移す」。ADR-0071 決定2)。返すのは書いた後のハッシュ。

    ハッシュが違えば消さない(ConfigChanged)。api_key が無ければ書かずに今のハッシュを返す。
    """
    return _remove_item(path, expected_digest, "api_key")


def remove_llm_item(path: Path, expected_digest: str | None, key: str) -> str:
    """[llm] の決まった項目(REMOVABLE)の行だけを消す。返すのは書いた後のハッシュ。

    REMOVABLE 以外の名前は ValueError(名前だけを出す)。ハッシュが違えば消さない(ConfigChanged)。
    項目が無ければ書かずに今のハッシュを返す。
    """
    if key not in REMOVABLE:
        raise ValueError(_T["not_removable_item"].format(key=key))
    return _remove_item(path, expected_digest, key)


def _remove_item(path: Path, expected_digest: str | None, key: str) -> str:
    """[llm] の key の行を、その項目がある部分からだけ消す。無ければ書かずに今のハッシュを返す。"""
    data, doc = _load(path, expected_digest)
    table = _section(doc, path, create=False)
    parts = [p for p in _parts(doc, table) if key in p] if table is not None else []
    if not parts:
        return digest_of(data)
    for part in parts:
        del part[key]
    return _write(path, tomlkit.dumps(doc))
