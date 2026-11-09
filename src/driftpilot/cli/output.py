"""機械が読む出力の書き出し口(REQ-19 / ADR-0023 決定1)。

JSON・JSON Lines・サマリ・自動生成するドキュメントは、ここを通して UTF-8 と `\\n` で書く。ロケールは見ない。
Windows では標準出力がパイプやファイルのとき `sys.stdout` がロケールの文字コード(日本語版では cp932)になり、
`\\n` も `\\r\\n` に変わるため、`print()` には頼らず `sys.stdout.buffer` を包んで書く。

人が読む出力(警告・注意・エラー・--format text・ヘルプ)はここを通さない。コンソールの文字コードのまま(決定2)。
storage と batch_io のファイル書き出しは cli を読み込まない層なので、それぞれが同じ指定(UTF-8・`\\n`)で開いている。
"""

import io
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TextIO

ENCODING = "utf-8"
NEWLINE = "\n"


@contextmanager
def machine_stdout() -> Iterator[TextIO]:
    """標準出力を UTF-8・`\\n` で書く口を貸す。終わったら外し、元の sys.stdout は閉じない。

    buffer を持たない標準出力(StringIO など)は、文字コードの無い文字列の口なのでそのまま使う。
    """
    stdout = sys.stdout
    buffer = getattr(stdout, "buffer", None)
    if buffer is None:
        yield stdout
        return
    stdout.flush()  # 先に print で書いた分を先に出す(順番を保つ)
    out = io.TextIOWrapper(buffer, encoding=ENCODING, newline=NEWLINE, write_through=True)
    try:
        yield out
    finally:
        try:
            out.flush()
        finally:
            out.detach()  # 外さないと、捨てるときに sys.stdout の buffer まで閉じる


def write_machine(text: str) -> None:
    """機械が読む文字列を、標準出力へ UTF-8・`\\n` で1回書く。"""
    with machine_stdout() as out:
        out.write(text)


def open_machine_file(path: str, mode: str) -> TextIO:
    """機械が読むファイルを UTF-8・`\\n` で開く。CLI が開く出力ファイルはすべてこれを通す。"""
    return open(path, mode, encoding=ENCODING, newline=NEWLINE)
