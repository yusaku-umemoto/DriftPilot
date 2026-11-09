"""`crypto pki certificate chain` の証明書ブロックを、ハッシュ値1つにして IR に写す。

16進数の行は1行ずつ解釈せず、ブロック全体をハッシュにする(中身の差分は意味を持たないため)。
ハッシュの入力(hash_input_version=1): 各16進数の行から空白をすべて除いて連結し、
大文字にして ASCII でエンコードしたもの。`quit` は含めない。アルゴリズムは SHA-256。
入力の作り方を変えるときは hash_input_version を上げ、保存済みのハッシュと比較不能と判定できるようにする。

hier_config のツリーでは `quit` は最後の16進数の行の子になる(REQ-08)。
証明書行の最後の子として現れる `quit` も許容する。
"""

import hashlib
import re
from dataclasses import dataclass, field
from typing import Literal

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.ir import Certificate, CertificateChain
from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.tracking import InterpretedLines

HASH_ALGORITHM: Literal["sha256"] = "sha256"
HASH_INPUT_VERSION: Literal[1] = 1


def hash_hex_lines(lines: list[str]) -> str:
    """16進数の行のリストから、版1のハッシュ(小文字の hex digest)を作る。"""
    joined = "".join("".join(line.split()) for line in lines).upper()
    return hashlib.sha256(joined.encode("ascii")).hexdigest()


@dataclass
class _Ctx:
    chains: dict[str, CertificateChain]
    current: CertificateChain | None = None
    # 証明書1つを読んでいる間の状態
    certificate: HConfigChild | None = None
    hex_lines: list[str] = field(default_factory=list)
    is_last: bool = False
    interpreted: InterpretedLines | None = None


def _chain(m: re.Match[str], ctx: _Ctx) -> bool:
    """チェーンを作る。同じ名前がすでにあれば読まない(その行と子行は未解釈)。"""
    if m["name"] in ctx.chains:
        return False
    ctx.current = {"name": m["name"], "certificates": []}
    ctx.chains[m["name"]] = ctx.current
    return True


def _body(m: re.Match[str], ctx: _Ctx) -> bool:
    ctx.hex_lines.append(m.string)
    return True


def _quit(m: re.Match[str], ctx: _Ctx) -> bool:
    return ctx.is_last


def _read_body(certificate: HConfigChild, interpreted: InterpretedLines, ctx: _Ctx) -> bool:
    """証明書の中身を読む。決まった形なら全行を解釈済みにして True を返す。

    字下げが揃っていないと、16進数の行が前の行の子になる。行の並びは先行順(config の出現順)で見る。
    中身が決まった形でなければ何も記録せず False を返す(その証明書は解釈しない)。
    """
    nodes = list(certificate.all_children())
    ctx.hex_lines = []
    matched: list[tuple[HConfigChild, Pattern]] = []
    for index, node in enumerate(nodes):
        ctx.is_last = index == len(nodes) - 1
        pattern = apply_first(PATTERNS, "certificate", node.text, ctx)
        if pattern is None:
            return False
        matched.append((node, pattern))
    if not ctx.hex_lines:
        return False
    for node, pattern in matched:
        interpreted.mark(node, pattern.id)
    return True


def _certificate(m: re.Match[str], ctx: _Ctx) -> bool:
    """証明書1つを読む。中身が決まった形でなければ読まない。

    中身の行(16進数と終端の quit)の記録と、ハッシュの計算まで行う。
    """
    interpreted = ctx.interpreted
    if not _read_body(ctx.certificate, interpreted, ctx):
        return False
    ctx.current["certificates"].append(
        {
            "kind": m["kind"],
            "serial": m["serial"],
            "hash_algorithm": HASH_ALGORITHM,
            "hash_input_version": HASH_INPUT_VERSION,
            "hash": hash_hex_lines(ctx.hex_lines),
        }
    )
    return True


# 並び＝優先順位。certificates.certificate は子行(body / quit)を読み込む手続きである。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "certificates.chain", "top", r"crypto pki certificate chain (?P<name>\S+)", "global.certificate_chains[name]",
        "crypto pki certificate chain TP-1", _chain,
        note="手続き。同じ名前が二度目に現れたら読まない。中身が読めない証明書があってもチェーン自体は読む",
    ),
    Pattern(
        "certificates.certificate", "crypto pki certificate chain",
        r"certificate (?:(?P<kind>self-signed|ca) )?(?P<serial>\S+)", "certificate_chains[name].certificates",
        "certificate self-signed 01", _certificate,
        note=(
            "手続き。本文(certificates.body)がすべて16進数の行(最後の quit は除く)のときだけ読む。"
            "本文が読めない証明書は、その行も本文も未解釈にする"
        ),
    ),
    Pattern(
        "certificates.body", "certificate", r"[0-9A-Fa-f]{1,8}(?: [0-9A-Fa-f]{1,8})*",
        "certificates[].hash",
        "3082022B 30820194 A0030201",
        _body,
        note=(
            "16進数の行(8桁以下の組を空白で区切る)。1行ずつは IR に写さず、全行から"
            "ハッシュ(SHA-256, hash_input_version=1)を作る。入力は空白を除いて連結し大文字にしたもの"
        ),
    ),
    Pattern(
        "certificates.quit", "certificate", r"quit", "(写さない。証明書の終端)", "quit", _quit,
        note="証明書の最後の行としてだけ読む。ハッシュには含めない",
    ),
)


def extract_certificate_chains(
    tree: HConfig, interpreted: InterpretedLines
) -> dict[str, CertificateChain]:
    """証明書チェーンを、名前をキーにした辞書で返す。"""
    ctx = _Ctx({})
    ctx.interpreted = interpreted
    for block in tree.children:
        heading = apply_first(PATTERNS, "top", block.text, ctx)
        if heading is None:
            continue
        interpreted.mark(block, heading.id)
        for certificate in block.children:
            ctx.certificate = certificate
            if (pattern := apply_first(PATTERNS, "crypto pki certificate chain", certificate.text, ctx)) is not None:
                interpreted.mark(certificate, pattern.id)
    return ctx.chains
