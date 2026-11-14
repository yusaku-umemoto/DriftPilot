"""LLM へ送る前に伏せる位置を決める表(ADR-0019 決定2・決定3)。書式は ADR-0011 のパターン表と同じ。

表を引くのは、秘密語ゲート(masking.py)を通った行、つまり秘密語をトークン完全一致で含む行
(と、名前が秘密語の XML のタグで始まるか閉じるタグを含む行。ADR-0140 決定2)だけである。
秘密語を含まない行は表を引かずにそのまま送る(決定4)。ただし、秘密語が無くても決まった頭の形の行
(下の `GATE_HEADS`。ADR-0198 決定2)は表を引く。

- 行(字下げを除いたもの)に fullmatch した最初の1件が結果を決める。表の並びが優先順位
- mask: 名前付きグループの位置を伏せる(複数可)。pass: 秘密語を含むが伏せる対象が無い
- どれにも当たらない行は送らない(決定5)。「書いていないから分からない」と「分かった上で伏せる対象が無い」を、
  表の上で区別する(決定3)
- scope: `top` は親の行が無いこと、`*` はどこでもよいこと。それ以外は、祖先の行のどれかがその語で始まること
  (`key chain` なら `key chain KC1` の下の行。孫の行でもよい)
- 例(example)に値は書かない。伏せる位置には `<値>` を置く

形が「鍵らしい」ことを根拠に伏せる処理は置かない(ADR-0019 決定1)。値の形ではなく、行頭からの形で決める。
"""

import hashlib
import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

from driftpilot import i18n

MaskKind = Literal["mask", "pass"]

# 秘密語の初期値(ADR-0019 決定4)。encrypted-password と api-key は REQ-16 の Q3 で追加した(Junos / FortiGate)。
# snmp は REQ-20 で追加した。富士通 SR-S の `snmp manager <n> <ip> <コミュニティ> …` は community という語を使わず、
# 他に当たる語が無いため。snmp を含む無害な行は表に pass で書く(書き漏らすと送らない側に落ちる)。
# phash 以降は ADR-0140 決定3 で追加した(PAN-OS は公開スキーマの秘密の印から、ASA は語の中の password などが
# 語の完全一致に当たらないため)。
# client-secret 以降は ADR-0145 で追加した(FortiGate で ENC の無い形が送る側に判定されていたため)。
# hello-password 以降は ADR-0198 決定1(IOS-XR の IS-IS の認証。REQ-277)。
# privacy-key 以降は ADR-0198 決定1(ヤマハ・NEC・Huawei・Gaia・F5・Junos・Aruba など。REQ-278)。
# authentication-password 以降の 8 語は ADR-0198 の追記(Junos・PAN-OS の set の形。書式が未確認なので表の行は無く、送らない側に落ちる)。
# 設定で追加はできるが、削除はできない
SECRET_WORDS: tuple[str, ...] = (
    "secret",
    "password",
    "passwd",
    "passphrase",
    "community",
    "key",
    "key-string",
    "key-hash",
    "shared-key",
    "psksecret",
    "ENC",
    "auth",
    "priv",
    "authentication-key",
    "message-digest-key",
    "engineID",
    "ssh-rsa",
    "ssh-ed25519",
    "pre-shared-key",
    "private-key",
    "md5",
    "sha256",
    "sha512",
    "encrypted-password",
    "api-key",
    "snmp",
    "phash",
    "bind-password",
    "authpwd",
    "privpwd",
    "secure-proxy-password",
    "agent-user-override-key",
    "passcode",
    "uninstall-password",
    "snmp-community-string",
    "vm-auth-key",
    "authkey",
    "ldap-login-password",
    "radius-common-pw",
    "policy-server-secret",
    "storage-key",
    "client-secret",
    "psk",
    "access-key",
    "hello-password",
    "lsp-password",
    "privacy-key",
    "hello-authentication-key",
    "preauthentication-secret",
    "plain-text-password-value",
    "simple-password",
    "local-password",
    "SECRET-DATA",
    "community-name",
    "snmp-community",
    "password-hash",
    "expert-password-hash",
    "grub2-password-hash",
    "bind-pw",
    "auth-password-encrypted",
    "privacy-password-encrypted",
    "cookie-encryption-passphrase",
    "encrypt-cookie-secret",
    "secret-password",
    "manualkey",
    "pin-code",
    "puk",
    "smtp-auth",
    "wpa-psk",
    "encrypted-key",
    "encrypted-secret",
    "shared-secret",
    "auth-code",
    "license-key",
    "preshared-key",
    "string-key",
    "cipher",
    "authtype",
    "blob",
    "authentication-password",
    "privacy-password",
    "plain-text-password",
    "default-chap-secret",
    "default-password",
    "auth-password",
    "priv-password",
    "service-passphrase",
)


@dataclass(frozen=True)
class MaskPattern:
    """伏せる表の1行。

    id:      "MSK-NN"
    scope:   親ブロック。`top` / `*` / 祖先の行の先頭の語(例: `router bgp`、`config system admin`)
    match:   行(字下げを除く)に fullmatch する正規表現。mask は伏せる位置を名前付きグループで持つ
    kind:    mask(伏せる)/ pass(伏せる対象なし)
    example: 実データに基づく例。値は含めない(伏せる位置は `<値>`)。テストがこの行を自分の書式に通す
    source:  出どころ(機種)
    note:    補足
    """

    id: str
    scope: str
    match: str
    kind: MaskKind
    example: str
    source: str
    note: str = ""


# 暗号化の型を表す語(Cisco / Arista の 0・5・7・8・9 など、Arista の 8a、sha512)。値の前に任意で入る
_T = r"(?: (?:\d+|8a|sha512))?"
_V = r"(?P<secret>\S+)"
_LEVEL = r"(?: level-[12])?"
# 値の後ろに許す形。列挙した語だけで、列挙に無い語(大文字・小文字や秘密語の有無によらない)が続く行は表に当たらず送らない
# (書式を確かめていない形は足さない。未知の後続の値を送らないため)。
# MSK-101(Cisco 系): `[udp-port <番号>]` と、`snmp-server host` の後ろの通知の種類。
# 通知の種類は `snmp-server enable traps` と `snmp-server host` で綴りが違うものがある(例: `mpls traffic-eng` と `mpls-traffic-eng`)ため、
# 公開のコーパスの実例で確かめた 3 語だけにする(docs/notes/masking-holes-2026-11.md の H-01: `udp-port 162 bgp config`・
# `udp-port 162 config`・`… <値> snmp`)。公式の綴りを確かめていない種類は足さない(表に当たらず送らない側に落ちる)。
# 足すときは、Cisco の snmp-server host のコマンドリファレンスの綴りで確かめてから足す
_TRAP_TYPES = "bgp|config|snmp"
_TAIL = rf"(?: udp-port \d+)?(?: (?:{_TRAP_TYPES}))*"
# MSK-103(NEC の `snmp-agent ip trap <値>`): 後ろは `link-status` だけ(穴の一覧は「link-status 等」。等は未確認なので足さない)
_TAIL_NEC = r"(?: link-status)?"
# snmp-server host の値の位置に来ても値ではない語(値として伏せて、後ろの本当の値を送ってしまうのを防ぐ。MSK-100・101)
_KW = r"(?!(?:vrf|traps|informs|version|clear|encrypted|use-vrf|filter-vrf|source-interface)(?: |$))"
# MSK-117・120・150・156〜163(暗号のアルゴリズム名の一覧の pass)の 1 語。秘密語を語として含む語は通さない(REQ-278 のレビュー)。
# 一覧の後ろに `password <値>` のような秘密の続きが付いた行を、pass として値ごと送らないため。判定は秘密語のゲートと同じく、
# 語を英数字・`_`・`-` のかたまりに分けて大文字・小文字を区別せずに見る。md5・sha256・sha512 は暗号の名前の一部(`SHA256:ECDHE-…` など)
# としてかたまりでは通すが、それだけの語(`… md5 <値>` の形)は通さない。語の一覧は読み込み時の SECRET_WORDS で、設定で足した語は含まない
_ALGO_WORDS = frozenset({"md5", "sha256", "sha512"})
_SECRET_PIECES = "|".join(re.escape(w) for w in sorted(set(SECRET_WORDS) - _ALGO_WORDS, key=lambda w: (-len(w), w)))
_ALGO = (
    rf"(?!\S*(?<![A-Za-z0-9_-])(?i:{_SECRET_PIECES})(?![A-Za-z0-9_-]))"
    r"(?!(?i:md5|sha256|sha512)(?: |$))\S+"
)

MASK_PATTERNS: tuple[MaskPattern, ...] = (
    # --- 利用者・特権(Cisco / Arista / NX-OS) ---
    MaskPattern(
        "MSK-01", "top", rf"username \S+(?: privilege \d+)?(?: role \S+)?(?: privilege \d+)? secret{_T} {_V}", "mask",
        "username admin privilege 15 role network-admin secret sha512 <値>", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-02", "top", rf"username \S+(?: privilege \d+)? password(?: \d+)? {_V}(?: role \S+)?", "mask",
        "username admin password 5 <値> role network-admin", "Cisco / NX-OS",
    ),
    MaskPattern(
        "MSK-03", "top", rf"username \S+ ssh-key(?: secondary)? (?:ssh-rsa|ssh-ed25519) {_V}(?: \S+)?", "mask",
        "username admin ssh-key secondary ssh-rsa <値> admin@host", "Arista",
        note="公開鍵は秘密ではないが、値の中身に意味が無くトークンを食うため伏せる(ADR-0018 決定2b)。末尾の注釈は送る",
    ),
    MaskPattern(
        "MSK-04", "top", r"username \S+ passphrase lifetime \d+ warntime \d+ gracetime \d+", "pass",
        "username admin passphrase lifetime 99999 warntime 14 gracetime 3", "NX-OS", note="パスワードの有効期限の設定",
    ),
    MaskPattern("MSK-05", "top", rf"enable (?:secret|password){_T} {_V}", "mask", "enable secret 9 <値>", "Cisco / Arista"),
    MaskPattern("MSK-06", "top", r"no enable (?:secret|password)", "pass", "no enable password", "Arista"),
    MaskPattern("MSK-07", "top", rf"boot secret{_T} {_V}", "mask", "boot secret sha512 <値>", "Arista"),
    MaskPattern("MSK-08", "top", rf"aaa root secret{_T} {_V}", "mask", "aaa root secret sha512 <値>", "Arista"),
    MaskPattern(
        "MSK-09", "ip ssh pubkey-chain", rf"key-hash (?:ssh-rsa|ssh-ed25519) {_V}(?: \S+)?", "mask",
        "key-hash ssh-rsa <値>", "Cisco / Arista",
    ),
    MaskPattern("MSK-10", "line", rf"password(?: \d+)? {_V}", "mask", "password 7 <値>", "Cisco", note="line vty / con の下"),
    MaskPattern("MSK-11", "top", r"no password strength-check", "pass", "no password strength-check", "NX-OS"),
    # --- BGP ---
    MaskPattern(
        "MSK-12", "router bgp", rf"neighbor \S+ password{_T} {_V}", "mask",
        "neighbor EVPN-OVERLAY-PEERS password 7 <値>", "BGP", note="vrf の下の neighbor も同じ(祖先に router bgp がある)",
    ),
    MaskPattern(
        "MSK-13", "router bgp", r"neighbor \S+ password shared-secret profile \S+ algorithm \S+", "pass",
        "neighbor EVPN-OVERLAY-PEERS password shared-secret profile profile1 algorithm aes-128-cmac-96", "Arista",
        note="鍵の本体は profile 側にある",
    ),
    # --- SNMP ---
    MaskPattern(
        "MSK-14", "top", rf"snmp-server community {_V}(?: \S+)*", "mask", "snmp-server community <値> RO", "Cisco / Arista / NX-OS",
        note="コミュニティ名の後ろ(RO / RW / view / group / ACL 名)は送る",
    ),
    MaskPattern(
        "MSK-15", "top",
        r"snmp-server user \S+(?: \S+)?(?: remote \S+(?: udp-port \d+)?)?(?: v3)?(?: localized (?P<secret3>\S+))?"
        r" auth (?:md5|sha\d*)(?: key \d+)? (?P<secret>\S+)"
        r"(?: priv(?: (?:des|3des|aes\d*|aes-\d+))?(?: key \d+)? (?P<secret2>\S+))?"
        r"(?: localizedkey)?(?: engineID (?P<secret4>\S+))?",
        "mask", "snmp-server user admin network-admin auth md5 <値> priv aes-128 <値> localizedkey", "NX-OS / Arista",
        note="auth と priv の鍵、localized / engineID の値を伏せる",
    ),
    MaskPattern(
        "MSK-16", "top", r"snmp-server engineID (?:local|remote \S+(?: udp-port \d+)?) (?P<secret>\S+)", "mask",
        "snmp-server engineID local <値>", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-17", "top",
        r"snmp-server host \S+(?: vrf \S+)?(?: (?:traps|informs))? version 3 (?:auth|noauth|priv) \S+(?: \S+)*", "pass",
        "snmp-server host 192.0.2.30 vrf MGMT version 3 auth USER-WRITE", "Arista",
        note="version 3 の auth / priv はセキュリティの水準で、後ろは利用者名",
    ),
    # --- 秘密語の無い SNMP の通知先(ADR-0198 決定2・4。GATE_HEADS の GH-01〜03 の頭に当たって表を引く) ---
    MaskPattern(
        "MSK-99", "top",
        r'snmp-server host \S+ (?:traps|informs) "(?P<secret>[^"]+)"(?: version (?:1|2c))?', "mask",
        'snmp-server host 192.0.2.1 traps "<値>" version 2c', "Alaxala AX", note='`"` の中だけを伏せる',
    ),
    MaskPattern(
        "MSK-100", "top",
        r"snmp-server host \S+(?: (?:clear|encrypted)(?: (?:traps|informs))?(?: version 2c)?"
        rf"| (?:traps|informs) version 2c (?:clear|encrypted)) {_KW}(?P<secret>[^\"\s]\S*)",
        "mask", "snmp-server host 192.0.2.1 traps version 2c encrypted <値>", "Cisco IOS-XR",
        note="`clear`・`encrypted` は値の型の語",
    ),
    MaskPattern(
        "MSK-101", "top",
        r"snmp-server host (?!.* community(?: |$))\S+(?: vrf \S+)?(?: (?:traps|informs))?(?: version (?:1|2c))?"
        rf" {_KW}(?P<secret>[^\"\s]\S*){_TAIL}",
        "mask", "snmp-server host 192.0.2.1 traps version 1 <値> udp-port 162 bgp config",
        "Cisco IOS / IOS-XE / NX-OS / Arista / Allied Telesis AW+",
        note="コミュニティ名の後ろ(udp-port・通知の種類)は送る。`community` の語のある行(ASA の形)には当てない",
    ),
    MaskPattern(
        "MSK-102", "top", r"snmp-agent (?:ip|ipv6) host \S+ (?P<secret>\S+)(?: version (?:1|2))?", "mask",
        "snmp-agent ip host 192.0.2.1 <値> version 2", "NEC",
    ),
    MaskPattern(
        "MSK-103", "top", rf"snmp-agent (?:ip|ipv6) trap (?P<secret>\S+){_TAIL_NEC}", "mask",
        "snmp-agent ip trap <値> link-status", "NEC",
    ),
    MaskPattern(
        "MSK-104", "*", r"rmon event \d+(?: log)? trap (?P<secret>\S+)(?: description \S+)?(?: owner \S+)?", "mask",
        "rmon event 1 log trap <値> description link-down owner admin", "Alaxala AX",
    ),
    MaskPattern(
        "MSK-18", "top", r"snmp-server group \S+ v3 (?:auth|noauth|priv)(?: \S+)*", "pass",
        "snmp-server group GRP-READ-ONLY v3 priv read v3read", "Arista",
    ),
    # 秘密語 snmp(REQ-20)で表を引くようになった Cisco / Arista の行
    MaskPattern(
        "MSK-92", "interface", r"(?:no )?snmp trap (?:link-status|link-change|mac-notification change (?:added|removed))",
        "pass", "snmp trap link-status", "Cisco / Arista", note="インターフェースの trap の有効化",
    ),
    MaskPattern(
        "MSK-93", "top",
        r"(?:no )?snmp (?:mib (?:notification-log (?:globalsize|globalageout) \d+|flowmon alarmhistorysize \d+|nhrp)"
        r"|monitor response \d+)",
        "pass", "snmp mib notification-log globalsize 500", "Cisco",
        note="`snmp mib community-map <コミュニティ>` を含めないため、語を列挙する",
    ),
    MaskPattern(
        "MSK-94", "top", r"snmp-server enable traps snmp(?: (?:link-down|link-up))*", "pass",
        "snmp-server enable traps snmp link-down", "Arista",
        note="trap の種類。後ろを任意の語にすると、秘密語と値を含む未知の形まで送るため、語を列挙する",
    ),
    # --- OSPF / IS-IS / VRRP(interface の下) ---
    MaskPattern(
        "MSK-19", "interface", rf"ip ospf message-digest-key \d+ (?:md5|sha1|sha256|sha384|sha512)(?: (?:\d+|8a))? {_V}",
        "mask", "ip ospf message-digest-key 1 sha512 7 <値>", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-20", "interface", rf"ip ospf authentication-key(?: (?:\d+|8a))? {_V}", "mask",
        "ip ospf authentication-key 7 <値>", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-21", "interface", rf"isis authentication key(?: (?:\d+|8a))? {_V}{_LEVEL}", "mask",
        "isis authentication key 7 <値> level-2", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-22", "interface", rf"isis authentication key-id \d+ algorithm \S+(?: rfc-5310)? key(?: (?:\d+|8a))? {_V}{_LEVEL}",
        "mask", "isis authentication key-id 2 algorithm sha-512 rfc-5310 key 7 <値>", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-23", "interface",
        r"isis authentication mode (?:md5|text|sha(?: key-id \d+)?|shared-secret profile \S+ algorithm \S+)(?: rx-disabled)?"
        + _LEVEL,
        "pass", "isis authentication mode md5 rx-disabled level-1", "Cisco / Arista", note="認証の方式(算法)",
    ),
    MaskPattern(
        "MSK-24", "interface",
        rf"vrrp \d+(?: peer)? authentication (?:ietf-md5|md5|text) key-string(?: \d+)? {_V}", "mask",
        "vrrp 1 peer authentication ietf-md5 key-string 7 <値>", "Arista",
    ),
    MaskPattern(
        "MSK-25", "router isis", rf"authentication key(?: (?:\d+|8a))? {_V}{_LEVEL}", "mask",
        "authentication key 0 <値> level-1", "Arista",
    ),
    MaskPattern(
        "MSK-26", "router isis", rf"authentication key-id \d+ algorithm \S+(?: rfc-5310)? key(?: (?:\d+|8a))? {_V}{_LEVEL}",
        "mask", "authentication key-id 2 algorithm sha-512 key 0 <値>", "Arista",
    ),
    MaskPattern(
        "MSK-27", "router isis",
        r"authentication mode (?:md5|text|sha(?: key-id \d+)?|shared-secret profile \S+ algorithm \S+)(?: rx-disabled)?"
        + _LEVEL,
        "pass", "authentication mode shared-secret profile test1 algorithm md5 level-1", "Arista",
    ),
    # --- 秘密語の無い認証の行(ADR-0198 決定2・4。GATE_HEADS の GH-04〜06 と、秘密語 hello-password・lsp-password) ---
    MaskPattern(
        "MSK-105", "*", r"vrrp \d+ authentication (?P<secret>\S+)", "mask", "vrrp 1 authentication <値>", "Alaxala AX / NEC",
    ),
    MaskPattern(
        "MSK-106", "vrrp", r"authentication text (?:plaintext|ciphertext) (?P<secret>\S+)", "mask",
        "authentication text plaintext <値>", "Aruba AOS-CX", note="VRRP のアドレスファミリーの下",
    ),
    MaskPattern(
        "MSK-107", "*", r"ip rip authentication (?:text|string) (?P<secret>\S+)", "mask",
        "ip rip authentication text <値>", "NEC / Allied Telesis AW+",
    ),
    MaskPattern(
        "MSK-108", "router isis", r"hello-password (?:hmac-md5|text) (?:clear|encrypted) (?P<secret>\S+)", "mask",
        "hello-password hmac-md5 encrypted <値>", "Cisco IOS-XR",
    ),
    MaskPattern(
        "MSK-109", "router isis", r"lsp-password (?:hmac-md5|text) (?:clear|encrypted) (?P<secret>\S+)", "mask",
        "lsp-password hmac-md5 encrypted <値>", "Cisco IOS-XR",
    ),
    # --- AAA サーバー・NTP ---
    MaskPattern(
        "MSK-28", "top",
        r"(?:radius|tacacs)-server(?: host \S+(?: (?:vrf \S+|timeout \d+|retransmit \d+|single-connection|auth-port \d+"
        rf"|acct-port \d+|port \d+))*)? key{_T} {_V}",
        "mask", "tacacs-server host 192.0.2.40 vrf MGMT key 7 <値>", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-29", "top", rf"ntp authentication-key \d+ (?:md5|sha1|sha256){_T} {_V}", "mask",
        "ntp authentication-key 1 md5 7 <値>", "Cisco / Arista",
    ),
    MaskPattern(
        "MSK-30", "top", r"ntp server(?: vrf \S+)? \S+(?: \S+)* key \d+(?: \S+)*", "pass",
        "ntp server vrf MGMT 192.0.2.10 iburst key 1", "Arista", note="key の後ろは鍵の番号で、鍵の本体ではない",
    ),
    # --- IPsec / 鍵 ---
    MaskPattern("MSK-31", "ip security", rf"shared-key{_T} {_V}", "mask", "shared-key 7 <値>", "Cisco / Arista"),
    MaskPattern("MSK-32", "ip security", r"key controller", "pass", "key controller", "Arista"),
    MaskPattern(
        "MSK-33", "ip security", r"(?:esp )?integrity (?:md5|sha1|sha256|sha384|sha512)(?: \S+)*", "pass",
        "esp integrity sha256", "Arista", note="算法の指定",
    ),
    MaskPattern("MSK-34", "key chain", rf"key-string(?: \d+)? {_V}", "mask", "key-string 7 <値>", "Cisco"),
    MaskPattern(
        "MSK-35", "crypto keyring", rf"pre-shared-key address \S+(?: \S+)? key {_V}", "mask",
        "pre-shared-key address 192.0.2.50 key <値>", "Cisco",
    ),
    MaskPattern(
        "MSK-36", "top", rf"crypto isakmp key(?: \d+)? {_V} address \S+(?: \S+)?", "mask",
        "crypto isakmp key <値> address 192.0.2.50", "Cisco",
    ),
    MaskPattern("MSK-37", "interface", r"no mka pre-shared-key", "pass", "no mka pre-shared-key", "Cisco IOS-XE"),
    MaskPattern(
        "MSK-38", "crypto ikev2 proposal", r"integrity (?:md5|sha1|sha256|sha384|sha512)(?: \S+)*", "pass",
        "integrity sha512 sha384", "Cisco IOS-XE",
    ),
    MaskPattern(
        "MSK-39", "mac security", rf"key \S+(?: \d+)? {_V}(?: fallback)?", "mask", "key 1234a 7 <値> fallback", "Arista",
        note="MACsec の鍵。key の次は鍵の名前(CKN)",
    ),
    # --- 管理系(Arista) ---
    MaskPattern(
        "MSK-40", "management security", r"password encryption-key common", "pass",
        "password encryption-key common", "Arista",
    ),
    MaskPattern(
        "MSK-41", "management security", r"password (?:minimum length \d+|policy \S+|encryption reversible \S+)", "pass",
        "password minimum length 8", "Arista",
    ),
    MaskPattern(
        "MSK-42", "management security", r"certificate \S+ key \S+", "pass", "certificate eAPI.crt key eAPI.key", "Arista",
        note="証明書と鍵のファイル名",
    ),
    MaskPattern("MSK-43", "management security", r"key \S+", "pass", "key SSL_KEY", "Arista", note="auto-certificate の鍵の名前"),
    MaskPattern(
        "MSK-81", "management security", r"digest (?:sha1|sha256|sha384|sha512)", "pass",
        "digest sha512", "Arista", note="算法の指定",
    ),
    MaskPattern(
        "MSK-44", "management security", rf"credentials (?:enroll|re-enroll)(?: username \S+)? secret(?: \d+)? {_V}", "mask",
        "credentials enroll username est_authentication secret 7 <値>", "Arista",
    ),
    MaskPattern(
        "MSK-45", "management security", rf"secret \S+(?: (?:\d+|8a))? {_V}(?: \S+)*", "mask",
        "secret Secret1 7 <値> receive-lifetime infinite transmit-lifetime infinite", "Arista",
        note="session shared-secret profile の鍵。secret の次は鍵の名前、後ろは有効期間",
    ),
    MaskPattern("MSK-46", "management defaults", r"secret hash (?:md5|sha512)", "pass", "secret hash sha512", "Arista"),
    MaskPattern(
        "MSK-47", "management ssh", r"authentication protocol(?: \S+)+", "pass",
        "authentication protocol keyboard-interactive password public-key", "Arista",
    ),
    MaskPattern(
        "MSK-48", "management ldap", rf"search username \S+ password(?: \d+)? {_V}", "mask",
        "search username cn=admin,dc=example,dc=com password 7 <値>", "Arista",
    ),
    MaskPattern(
        "MSK-49", "hardware tcam", r"key (?:field|size limit)(?: \S+)+", "pass",
        "key field dscp dst-ip", "Arista", note="TCAM のキーの項目",
    ),
    MaskPattern(
        "MSK-50", "radius proxy", rf"client (?:ipv4|ipv6|host) \S+(?: vrf \S+)? key(?: \d+)? {_V}", "mask",
        "client ipv4 192.0.2.60 vrf MGMT key 7 <値>", "Arista",
    ),
    MaskPattern("MSK-51", "radius proxy", rf"client key(?: \d+)? {_V}", "mask", "client key 7 <値>", "Arista"),
    MaskPattern(
        "MSK-52", "monitor server radius", rf"probe method access-request username \S+ password(?: \d+)? {_V}", "mask",
        "probe method access-request username arista password 7 <値>", "Arista",
    ),
    MaskPattern(
        "MSK-53", "mpls rsvp", rf"authentication index \d+ password(?: \d+)? {_V}", "mask",
        "authentication index 1 password 7 <値>", "Arista",
    ),
    MaskPattern(
        "MSK-54", "mpls rsvp", r"(?:neighbor \S+ )?authentication type md5", "pass",
        "neighbor 192.0.2.70 authentication type md5", "Arista",
    ),
    MaskPattern("MSK-55", "cvx", rf"redis password(?: \d+)? {_V}", "mask", "redis password 7 <値>", "Arista"),
    MaskPattern(
        "MSK-56", "cvx", rf"username \S+ tenant \S+ password(?: \d+)? {_V}", "mask",
        "username admin tenant TENANT1 password 7 <値>", "Arista",
    ),
    MaskPattern("MSK-57", "vmtracer session", rf"password(?: \d+)? {_V}", "mask", "password 7 <値>", "Arista"),
    MaskPattern(
        "MSK-58", "dot1x", rf"passphrase(?: \d+)? {_V}", "mask",
        "passphrase 7 <値>", "Arista", note="supplicant profile の下",
    ),
    MaskPattern(
        "MSK-59", "monitor telemetry influx", rf"username \S+ password(?: \d+)? {_V}", "mask",
        "username test password 7 <値>", "Arista",
    ),
    # --- BGP の属性・VLAN の種別(community という語を含むが秘密ではない) ---
    MaskPattern(
        "MSK-60", "route-map", r"set community(?: \S+)+", "pass",
        "set community no-advertise additive", "Cisco / Arista", note="BGP のコミュニティ属性",
    ),
    MaskPattern(
        "MSK-61", "route-map", r"match community \S+(?: exact-match)?", "pass",
        "match community LIST-COM", "Cisco / Arista", note="コミュニティリストの名前",
    ),
    MaskPattern(
        "MSK-62", "vlan", r"private-vlan community primary vlan \d+", "pass",
        "private-vlan community primary vlan 100", "Arista", note="VLAN の種別",
    ),
    # --- その他の Cisco の行 ---
    MaskPattern(
        "MSK-63", "top", r"ip ssh server authenticate user (?:password|publickey|keyboard)", "pass",
        "ip ssh server authenticate user password", "Cisco",
    ),
    MaskPattern(
        "MSK-64", "top", r"ip ssh server algorithm (?:hostkey|publickey|authentication)(?: \S+)+", "pass",
        "ip ssh server algorithm hostkey x509v3-ssh-rsa ssh-rsa", "Cisco", note="算法の一覧",
    ),
    MaskPattern(
        "MSK-65", "top", r"mpls ldp logging password (?:configuration|rollover)", "pass",
        "mpls ldp logging password rollover", "Cisco IOS-XE",
    ),
    MaskPattern(
        "MSK-66", "top", r"ip (?:http digest algorithm|rsvp authentication type) md5", "pass",
        "ip rsvp authentication type md5", "Cisco IOS-XE",
    ),
    MaskPattern(
        "MSK-67", "top", r"logging level auth \d+", "pass",
        "logging level auth 3", "NX-OS", note="ログの設備(facility)名",
    ),
    MaskPattern(
        "MSK-68", "*", r"description(?: .*)?", "pass", "description Inheriting auth from VRF", "共通",
        note="説明文は伏せない(ADR-0018 決定2)。秘密語を含まない説明文と同じ扱い",
    ),
    # --- 富士通 Si-R ---
    MaskPattern(
        "MSK-69", "top", rf"remote \d+ ap \d+ ike shared key text {_V}(?: encrypted)?", "mask",
        "remote 0 ap 0 ike shared key text <値> encrypted", "富士通 Si-R",
    ),
    MaskPattern(
        "MSK-70", "top",
        r"(?:(?:lan|remote) \d+ ip ospf auth type \S+|lan \d+ vrrp auth \S+|remote \d+ ap \d+ ppp auth type \S+"
        r"|remote \d+ ap \d+ ipsec (?:send|receive) auth \S+|remote \d+ ap \d+ ipsec ike auth \S+"
        r"|remote \d+ ap \d+ ike proposal \d+ auth-method shared-key|answer ppp auth type \S+"
        r"|dvpn server auth (?:aaa \d+|use (?:on|off))|password authtype \S+)",
        "pass", "remote 0 ap 0 ike proposal 0 auth-method shared-key", "富士通 Si-R", note="認証の方式",
    ),
    MaskPattern(
        "MSK-71", "top", r"bgp neighbor \d+ community (?:on|off)", "pass",
        "bgp neighbor 0 community on", "富士通 Si-R",
    ),
    MaskPattern(
        "MSK-72", "top", r"password format common", "pass",
        "password format common", "富士通 Si-R", note="表示形式の設定(ADR-0019 実測2)",
    ),
    # --- 富士通 SR-S / Si-R(REQ-20。秘密語 snmp で表を引くようになった行) ---
    MaskPattern(
        "MSK-85", "top", r"snmp manager \d+ \S+ (?P<secret>\S+)(?: (?:off|v1|v2c))?(?: (?:disable|enable))?", "mask",
        "snmp manager 0 192.0.2.80 <値> off disable", "富士通 SR-S",
        note="3番目の語がコミュニティ名。後ろ(trap / setrequest の指定)は語を列挙して送る",
    ),
    MaskPattern(
        "MSK-86", "top", r"password \S+ set (?P<secret>\S+) encrypted", "mask",
        "password admin set <値> encrypted", "富士通 SR-S",
    ),
    MaskPattern(
        "MSK-87", "top", r"snmp agent (?:sysname|address) \S+", "pass",
        "snmp agent sysname SR-S", "富士通 SR-S", note="機器名とエージェントのアドレス",
    ),
    MaskPattern(
        "MSK-88", "top", r"snmp service (?:on|off|enable|disable)", "pass", "snmp service on", "富士通 SR-S / Si-R",
    ),
    MaskPattern(
        "MSK-89", "top",
        r"snmp trap (?:coldstart|linkup|linkdown|authfail|noserror|vrrpauthfail|vrrpnewmaster|vrrpprotoerror)"
        r" (?:enable|disable)",
        "pass", "snmp trap coldstart enable", "富士通 Si-R", note="コーパスで観測した trap の種類",
    ),
    MaskPattern(
        "MSK-90", "top",
        r"(?:(?:lan|wan|remote|pseudo-ether) \d+(?: ap \d+)?|ether \d+(?: \d+)?) snmp trap (?:linkup|linkdown) (?:enable|disable)",
        "pass", "lan 0 snmp trap linkdown enable", "富士通 Si-R",
    ),
    MaskPattern(
        "MSK-91", "top", r"(?:lan|remote) \d+ ip nat appli snmp (?:on|off)", "pass",
        "lan 0 ip nat appli snmp on", "富士通 Si-R", note="NAT の SNMP 用の変換",
    ),
    # --- FortiGate(set の行は config ブロックの中に現れる。行頭の set で形が決まるため scope を問わない) ---
    MaskPattern(
        "MSK-73", "*", rf"set \S+ ENC {_V}", "mask", "set password ENC <値>", "FortiGate",
        note="FortiGate は暗号化した値の前に ENC を置く。項目名によらず ENC の次を伏せる",
    ),
    MaskPattern(
        "MSK-74", "*", rf"set (?:password|passwd|psksecret|private-key|api-key) {_V}", "mask", "set psksecret <値>", "FortiGate",
        note="平文(ENC なし)の値",
    ),
    MaskPattern("MSK-75", "*", r"set type password", "pass", "set type password", "FortiGate", note="利用者の認証の種類"),
    MaskPattern(
        "MSK-82", "*", r"set (?:auth (?:enable|disable)|auth-mode-l[12] (?:password|md5|none))", "pass",
        "set auth-mode-l1 password", "FortiGate", note="機能の有効化と認証の方式",
    ),
    MaskPattern(
        "MSK-76", "top", r'config system replacemsg auth "[^"]+"', "pass",
        'config system replacemsg auth "auth-login-page"', "FortiGate", note="認証画面の文面のブロック",
    ),
    MaskPattern(
        "MSK-83", "top", r"config system snmp community", "pass", "config system snmp community", "FortiGate",
        note="ブロックの見出し。コミュニティ名は子の set name の行にあり、秘密語を含まない(docs/notes/req-16-llm.md)",
    ),
    MaskPattern(
        "MSK-95", "top", r"config (?:system snmp (?:sysinfo|user)|wireless-controller snmp)", "pass",
        "config system snmp sysinfo", "FortiGate", note="ブロックの見出し",
    ),
    MaskPattern(
        "MSK-96", "*",
        r"set allowaccess(?: (?:ping|https|ssh|snmp|http|fgfm|capwap|fabric|auto-ipsec|probe-response))+", "pass",
        "set allowaccess ping https ssh snmp", "FortiGate",
        note="管理用に許可するプロトコルの一覧。コーパスで観測したプロトコル名に限る",
    ),
    # --- Junos ---
    MaskPattern(
        "MSK-77", "system", r'encrypted-password (?P<secret>"[^"]*"|[^";\s]+);?(?: ## SECRET-DATA)?', "mask",
        'encrypted-password "<値>"; ## SECRET-DATA', "Junos",
    ),
    MaskPattern(
        "MSK-78", "top",
        r'set system (?:root-authentication|login user \S+ authentication) encrypted-password (?P<secret>"[^"]*"|\S+)', "mask",
        'set system login user admin authentication encrypted-password "<値>"', "Junos",
    ),
    MaskPattern(
        "MSK-79", "top", rf"set snmp community {_V}(?: \S+)*", "mask",
        "set snmp community <値> authorization read-only", "Junos",
    ),
    MaskPattern(
        "MSK-84", "snmp", r"community (?P<secret>[^\s;{]+)(?: \{|;)?", "mask",
        "community <値> {", "Junos", note="波括弧の形",
    ),
    MaskPattern("MSK-97", "top", r"snmp \{", "pass", "snmp {", "Junos", note="ブロックの見出し"),
    MaskPattern(
        "MSK-98", "top", r"set snmp view \S+ oid \S+(?: (?:include|exclude))?", "pass",
        "set snmp view VIEW-1 oid .1 include", "Junos",
    ),
    MaskPattern(
        "MSK-80", "top",
        r"set policy-options (?:community \S+ members \S+|policy-statement \S+ term \S+"
        r" (?:from community \S+|then community (?:add|delete|set) \S+))",
        "pass", "set policy-options policy-statement EXPORT term 1 from community CL-1", "Junos", note="BGP のコミュニティ",
    ),
    # --- MSK-110〜163 は ADR-0198 決定1・3(REQ-278)。書式を確かめた形(docs/notes/masking-holes-2026-11.md の「確」)だけを足す。
    # 値の位置の書き方: `"` の形は `"` の中だけを伏せる。Junos の形は MSK-77 と同じ(`"…"` か裸の語、後ろに `;` と `## SECRET-DATA`)。
    # それ以外は `\S+`。書式が未確認の形(「未」)は足さず、判定に回って送らない側に落ちる ---
    # Check Point Gaia(H-06・H-23)
    MaskPattern(
        "MSK-110", "*",
        r"add mcvr vrid \d+(?: priority \d+)?(?: priority-delta \d+)? authtype simple (?P<secret>\S+)(?: hello-interval \d+)?",
        "mask", "add mcvr vrid 10 priority 100 priority-delta 10 authtype simple <値> hello-interval 1", "Check Point Gaia",
    ),
    MaskPattern(
        "MSK-111", "*", r"set mcvr vrid \d+ authtype simple (?P<secret>\S+)", "mask",
        "set mcvr vrid 10 authtype simple <値>", "Check Point Gaia",
    ),
    MaskPattern(
        "MSK-112", "*", r'set ospf interface \S+ authtype simple "(?P<secret>[^"]+)"', "mask",
        'set ospf interface eth0 authtype simple "<値>"', "Check Point Gaia", note='`"` の中だけを伏せる',
    ),
    MaskPattern(
        "MSK-113", "*",
        r"(?:add mcvr vrid \d+(?: priority \d+)?(?: priority-delta \d+)? authtype none(?: hello-interval \d+)?"
        r"|set mcvr vrid \d+ authtype none|set ospf interface \S+ authtype none)",
        "pass", "set mcvr vrid 10 authtype none", "Check Point Gaia", note="認証なしの指定(値なし)",
    ),
    MaskPattern(
        "MSK-114", "*", r"set user \S+ password-hash (?P<secret>\S+)", "mask",
        "set user admin password-hash <値>", "Check Point Gaia",
    ),
    MaskPattern(
        "MSK-115", "*", r"set expert-password-hash (?P<secret>\S+)", "mask", "set expert-password-hash <値>", "Check Point Gaia",
    ),
    MaskPattern(
        "MSK-116", "*", r"set grub2-password-hash (?P<secret>\S+)", "mask", "set grub2-password-hash <値>", "Check Point Gaia",
    ),
    MaskPattern(
        "MSK-117", "*", rf"set ssh server cipher {_ALGO} (?:on|off)", "pass", "set ssh server cipher aes256-ctr on", "Check Point Gaia",
        note="暗号のアルゴリズム名の指定(公開のコーパスの実例)",
    ),
    # Huawei VRP(H-07)。算法の名前は確かめた語だけ(aes256 などは送らない側に落ちる)
    MaskPattern(
        "MSK-118", "*", r"snmp-agent usm-user v3 \S+ authentication-mode (?:sha|sha2-256) cipher (?P<secret>\S+)", "mask",
        "snmp-agent usm-user v3 U1 authentication-mode sha cipher <値>", "Huawei VRP",
    ),
    MaskPattern(
        "MSK-119", "*", r"snmp-agent usm-user v3 \S+ privacy-mode aes128 cipher (?P<secret>\S+)", "mask",
        "snmp-agent usm-user v3 U1 privacy-mode aes128 cipher <値>", "Huawei VRP",
    ),
    MaskPattern(
        "MSK-120", "*", rf"ssh (?:server|client) cipher {_ALGO}(?: {_ALGO})*", "pass", "ssh server cipher aes256_gcm aes128_gcm", "Huawei VRP",
        note="暗号のアルゴリズム名の一覧(秘密ではない)",
    ),
    # NEC UNIVERGE IX(H-11・H-13・H-15)
    MaskPattern(
        "MSK-121", "*", r"authentication secret-password [01] \S+ (?P<secret>\S+)", "mask",
        "authentication secret-password 0 user1 <値>", "NEC UNIVERGE IX",
    ),
    MaskPattern(
        "MSK-122", "*", r"web-auth username \S+ secret-password (?P<secret>\S+)", "mask",
        "web-auth username user1 secret-password <値>", "NEC UNIVERGE IX",
    ),
    MaskPattern(
        "MSK-123", "*", r"http-server cross-site secret-password (?P<secret>\S+)", "mask",
        "http-server cross-site secret-password <値>", "NEC UNIVERGE IX",
    ),
    MaskPattern(
        "MSK-124", "*", r"tunnel v6pv user \S+ secret-password (?P<secret>\S+)", "mask",
        "tunnel v6pv user user1 secret-password <値>", "NEC UNIVERGE IX",
    ),
    MaskPattern(
        "MSK-125", "*",
        r"ipsec manualkey \S+(?: key-type \S+)? (?:esp-(?:des|3des) (?P<secret>\S+)(?: (?:esp-md5|ah-md5) (?P<secret2>\S+))?"
        r"|(?:esp-md5|ah-md5) (?P<secret3>\S+))",
        "mask", "ipsec manualkey TUN1 key-type char esp-des <値> esp-md5 <値>", "NEC UNIVERGE IX",
        note="鍵は 2 か所ある。確かめた語(esp-des・esp-3des・esp-md5・ah-md5)だけで、esp-aes などは表に当たらず送らない",
    ),
    MaskPattern(
        "MSK-126", "*", r"mobile pin-code plain (?P<secret>\S+)", "mask", "mobile pin-code plain <値>", "NEC UNIVERGE IX",
    ),
    MaskPattern(
        "MSK-127", "*", r"mobile puk (?P<secret>\S+) (?P<secret2>\S+)", "mask", "mobile puk <値> <値>", "NEC UNIVERGE IX",
        note="PIN と PUK の 2 語とも伏せる",
    ),
    MaskPattern(
        "MSK-128", "*", r"mobile pin-auth enable (?P<secret>\S+)", "mask", "mobile pin-auth enable <値>", "NEC UNIVERGE IX",
    ),
    # ヤマハ RTX / NVR(H-16〜H-19)
    MaskPattern(
        "MSK-129", "*", r"login user \S+ encrypted (?P<secret>\S+)", "mask", "login user user1 encrypted <値>", "ヤマハ RTX / NVR",
    ),
    MaskPattern(
        "MSK-130", "*", r"login user \S+ (?!encrypted(?: |$))(?P<secret>\S+)", "mask", "login user user1 <値>", "ヤマハ RTX / NVR",
        note="実機の show config では値が `*` になる。`*` も伏せる(値の形は見ない)",
    ),
    MaskPattern(
        "MSK-131", "*",
        r"snmpv3 usm user \d+ \S+(?: group \S+)? (?:sha|md5) (?P<secret>\S+)(?: (?:aes128-cfb|des-cbc) (?P<secret2>\S+))?",
        "mask", "snmpv3 usm user 1 user1 group grp1 sha <値> aes128-cfb <値>", "ヤマハ RTX / NVR",
    ),
    MaskPattern(
        "MSK-132", "*", r"snmpv3 usm user \d+ \S+(?: group \S+)?", "pass", "snmpv3 usm user 1 user1 group grp1", "ヤマハ RTX / NVR",
        note="認証の無い利用者(公式の書式で、認証は省ける)",
    ),
    MaskPattern(
        "MSK-133", "*",
        r"mail server smtp \d+ \S+(?: port=\d+)? smtp-auth \S+ (?P<secret>\S+)(?: (?:cram-md5|digest-md5|plain))?"
        r"(?: pop-before-smtp)?(?: smtps)?",
        "mask", "mail server smtp 1 mail.example.com port=587 smtp-auth user1 <値> cram-md5 smtps", "ヤマハ RTX / NVR",
    ),
    MaskPattern(
        "MSK-134", "*", r"mail server pop \d+ \S+(?: port=\d+)? (?:pop3|apop) \S+ (?P<secret>\S+)", "mask",
        "mail server pop 1 mail.example.com apop user1 <値>", "ヤマハ RTX / NVR",
    ),
    MaskPattern(
        "MSK-135", "*", r"ipsec ike xauth myname \S+ \S+ (?P<secret>\S+)", "mask",
        "ipsec ike xauth myname 192.0.2.1 user1 <値>", "ヤマハ RTX / NVR",
    ),
    MaskPattern(
        "MSK-136", "*", r"ipsec ike eap myname \S+ \S+ (?P<secret>\S+)", "mask",
        "ipsec ike eap myname 192.0.2.1 user1 <値>", "ヤマハ RTX / NVR",
    ),
    # Aruba AOS-Switch(H-21)
    MaskPattern(
        "MSK-137", "*", r'radius-server(?: host \S+)? encrypted-key "(?P<secret>[^"]+)"', "mask",
        'radius-server host 192.0.2.1 encrypted-key "<値>"', "Aruba AOS-Switch", note='`"` の中だけを伏せる',
    ),
    MaskPattern(
        "MSK-138", "*", r'tacacs-server(?: host \S+)? encrypted-key "(?P<secret>[^"]+)"', "mask",
        'tacacs-server host 192.0.2.2 encrypted-key "<値>"', "Aruba AOS-Switch", note='`"` の中だけを伏せる',
    ),
    MaskPattern(
        "MSK-139", "*", r'aaa port-access supplicant \S+ identity "[^"]*" encrypted-secret "(?P<secret>[^"]+)"', "mask",
        'aaa port-access supplicant 1 identity "user1" encrypted-secret "<値>"', "Aruba AOS-Switch", note='`"` の中だけを伏せる',
    ),
    MaskPattern(
        "MSK-140", "*", r'encrypted-key "(?P<secret>[^"]+)"', "mask", 'encrypted-key "<値>"', "Aruba AOS-Switch",
        note='macsec の鍵。`"` の中だけを伏せる',
    ),
    # F5 BIG-IP・Junos(H-24〜H-29)
    MaskPattern(
        "MSK-141", "*", r"auth-password-encrypted (?P<secret>\S+)", "mask", "auth-password-encrypted <値>", "F5 BIG-IP",
    ),
    MaskPattern(
        "MSK-142", "*", r"privacy-password-encrypted (?P<secret>\S+)", "mask", "privacy-password-encrypted <値>", "F5 BIG-IP",
    ),
    MaskPattern("MSK-143", "*", r"bind-pw (?P<secret>\S+)", "mask", "bind-pw <値>", "F5 BIG-IP"),
    MaskPattern(
        "MSK-144", "*", r"cookie-encryption-passphrase (?P<secret>\S+)", "mask", "cookie-encryption-passphrase <値>", "F5 BIG-IP",
    ),
    MaskPattern(
        "MSK-145", "*", r"encrypt-cookie-secret (?P<secret>\S+)", "mask", "encrypt-cookie-secret <値>", "F5 BIG-IP",
    ),
    MaskPattern(
        "MSK-146", "*", r'community-name (?P<secret>"[^"]*"|[^";\s]+);(?: ## SECRET-DATA)?', "mask",
        'community-name "<値>"; ## SECRET-DATA', "Junos", note="SNMPv3 の snmp-community の下。行の終わりが `;`",
    ),
    MaskPattern("MSK-147", "*", r"community-name (?P<secret>\S+)", "mask", "community-name <値>", "F5 BIG-IP"),
    MaskPattern(
        "MSK-148", "ltm monitor", r"snmp-community (?P<secret>\S+)", "mask", "snmp-community <値>", "F5 BIG-IP",
        note="`ltm monitor` の下だけ。Junos の `snmp-community <名> {` には当てない",
    ),
    MaskPattern("MSK-149", "sys dynad key", r"blob (?P<secret>\S+)", "mask", "blob <値>", "F5 BIG-IP"),
    MaskPattern(
        "MSK-150", "ltm cipher", rf"cipher {_ALGO}(?: {_ALGO})*", "pass", "cipher DEFAULT:!SSLV3", "F5 BIG-IP",
        note="`ltm cipher` の下の暗号の指定(秘密ではない)",
    ),
    # Junos(H-34〜H-38・H-40)
    MaskPattern(
        "MSK-151", "*", r'privacy-key (?P<secret>"[^"]*"|[^";\s]+);?(?: ## SECRET-DATA)?', "mask",
        'privacy-key "<値>"; ## SECRET-DATA', "Junos",
    ),
    MaskPattern(
        "MSK-152", "*", r'hello-authentication-key (?P<secret>"[^"]*"|[^";\s]+);?(?: ## SECRET-DATA)?', "mask",
        'hello-authentication-key "<値>"; ## SECRET-DATA', "Junos",
    ),
    MaskPattern(
        "MSK-153", "*", r'plain-text-password-value (?P<secret>"[^"]*"|[^";\s]+);?(?: ## SECRET-DATA)?', "mask",
        'plain-text-password-value "<値>"; ## SECRET-DATA', "Junos",
    ),
    MaskPattern(
        "MSK-154", "*", r'simple-password (?P<secret>"[^"]*"|[^";\s]+);?(?: ## SECRET-DATA)?', "mask",
        'simple-password "<値>"; ## SECRET-DATA', "Junos",
    ),
    MaskPattern(
        "MSK-155", "*",
        r'(?:ssh-dsa|ssh-ecdsa|ecdsa-sha2-nistp256-key|public-key) (?P<secret>"[^"]*"|[^";\s]+);?(?: ## SECRET-DATA)?',
        "mask", 'ssh-dsa "<値>"; ## SECRET-DATA', "Junos",
        note="公開鍵は秘密ではないが、MSK-03 と同じく鍵の本文を伏せる。Junos は種類と注釈も `\"` の中にあるため、`\"` の中を全部伏せる",
    ),
    # 語を足したことで判定に回る、今の対応機種などの無害な行(今までどおり送る)
    MaskPattern(
        "MSK-156", "management ssh", rf"cipher {_ALGO}(?: {_ALGO})*", "pass", "cipher aes256-ctr aes128-ctr", "Arista",
        note="SSH の暗号のアルゴリズム名の一覧(秘密ではない)",
    ),
    MaskPattern(
        "MSK-157", "mac security", rf"cipher {_ALGO}", "pass", "cipher aes256-gcm-xpn", "Arista",
        note="MACsec の暗号のアルゴリズム名(秘密ではない)",
    ),
    MaskPattern(
        "MSK-158", "management security", r"session shared-secret profile \S+", "pass",
        "session shared-secret profile Secret1", "Arista", note="プロファイルの見出し(鍵は子の secret の行にある。MSK-45)",
    ),
    MaskPattern(
        "MSK-159", "config system lte-modem", r"set authtype(?: (?:none|pap|chap))+", "pass", "set authtype pap", "FortiGate",
        note="LTE モデムの認証の方式(値なし)",
    ),
    MaskPattern(
        "MSK-160", "top", r"config (?:vpn ipsec manualkey|switch-controller snmp-community)", "pass",
        "config vpn ipsec manualkey", "FortiGate", note="ブロックの見出し。行の終わりが秘密語で、値は無い",
    ),
    MaskPattern(
        "MSK-161", "*", rf"ssh (?:(?:server|client) algorithms|server enable) cipher(?: {_ALGO})+", "pass",
        "ssh server algorithms cipher aes256-ctr aes128-ctr", "Cisco IOS-XR", note="SSH の暗号のアルゴリズム名の一覧(秘密ではない)",
    ),
    MaskPattern(
        "MSK-162", "*", rf"(?:ssl cipher(?: {_ALGO})+|ssh cipher (?:encryption|integrity)(?: {_ALGO})+)", "pass",
        "ssh cipher encryption high", "Cisco ASA", note="SSL・SSH の暗号の指定(秘密ではない)",
    ),
    MaskPattern(
        "MSK-163", "management security", rf"cipher v1\.[0-3](?: {_ALGO})+", "pass", "cipher v1.3 TLS_AES_256_GCM_SHA384", "Arista",
        note="SSL プロファイルの暗号の指定(秘密ではない)",
    ),
    # H-11。REQ-278 の設計側回答で承認された ADR-0198 の範囲外の追加。
    MaskPattern(
        "MSK-164", "*", r"usbmem authentication password-file \S+ plain (?P<secret>\S+)", "mask",
        "usbmem authentication password-file pw.txt plain <値>", "NEC UNIVERGE IX",
        note="設計側の承認で追加。plain の値だけを伏せ、未知の前後の語や plain 以外は送らない",
    ),
)

# --- 利用者に見える説明の英語の版(REQ-259 / ADR-0190 決定3) ---
# 送る前の確かめ・`driftpilot mask-patterns` の出力に出る備考・出どころ・例を、言語に合わせて出す。
# 伏せる・送らないの判定(match・scope・kind・秘密語・送らない形・指紋の計算)には使わない。MaskPattern の値は日本語のまま変えない
# (例は表の自己照合とテストの入力に使う)。備考のある 73 件すべてに英語がある(ID → 英語。テストが鍵を照らす)
MASK_NOTES_EN: dict[str, str] = {
    "MSK-03": "A public key is not a secret, but the value has no meaning and uses up tokens, so it is masked (ADR-0018 decision 2b). The trailing comment is sent",
    "MSK-04": "Password expiry settings",
    "MSK-10": "Under line vty / con",
    "MSK-12": "The same applies to a neighbor under a vrf (router bgp is an ancestor)",
    "MSK-13": "The body of the key is in the profile",
    "MSK-14": "What follows the community name (RO / RW / view / group / ACL name) is sent",
    "MSK-15": "Masks the auth and priv keys and the localized / engineID values",
    "MSK-17": "For version 3, auth / priv is the security level, and what follows is the user name",
    "MSK-92": "Enabling traps on an interface",
    "MSK-99": "Only what is inside the double quotes is masked",
    "MSK-100": "`clear` and `encrypted` are words for the type of the value",
    "MSK-101": "What follows the community name (udp-port, notification types) is sent. Not applied to a line that has the word `community` (the ASA form)",
    "MSK-106": "Under the address family of VRRP",
    "MSK-93": "Words are listed so that `snmp mib community-map <community>` is not included",
    "MSK-94": "Trap types. If what follows were any word, unknown forms containing a secret word and a value would be sent, so the words are listed",
    "MSK-23": "Authentication method (algorithm)",
    "MSK-30": "What follows key is the key number, not the body of the key",
    "MSK-33": "Algorithm specification",
    "MSK-39": "MACsec key. What follows key is the key name (CKN)",
    "MSK-42": "File names of the certificate and the key",
    "MSK-43": "Key name of auto-certificate",
    "MSK-81": "Algorithm specification",
    "MSK-45": "Key of the session shared-secret profile. What follows secret is the key name, followed by the validity period",
    "MSK-49": "TCAM key fields",
    "MSK-58": "Under the supplicant profile",
    "MSK-60": "BGP community attribute",
    "MSK-61": "Name of the community list",
    "MSK-62": "VLAN type",
    "MSK-64": "List of algorithms",
    "MSK-67": "Log facility name",
    "MSK-68": "A description is not masked (ADR-0018 decision 2). It is handled like a description that has no secret word",
    "MSK-70": "Authentication method",
    "MSK-72": "Display format setting (ADR-0019 measurement 2)",
    "MSK-85": "The third word is the community name. What follows (the trap / setrequest specification) is sent with its words listed",
    "MSK-87": "Device name and the address of the agent",
    "MSK-89": "Trap types observed in the corpus",
    "MSK-91": "NAT translation for SNMP",
    "MSK-73": "FortiGate puts ENC before an encrypted value. The word after ENC is masked whatever the item name is",
    "MSK-74": "Plain text value (without ENC)",
    "MSK-75": "Type of user authentication",
    "MSK-82": "Enabling a feature and the authentication method",
    "MSK-76": "Block of the authentication screen text",
    "MSK-83": "Block heading. The community name is on the child set name line, which has no secret word (docs/notes/req-16-llm.md)",
    "MSK-95": "Block heading",
    "MSK-96": "List of protocols allowed for management. Limited to the protocol names observed in the corpus",
    "MSK-84": "Curly brace form",
    "MSK-97": "Block heading",
    "MSK-80": "BGP community",
    "MSK-112": "Only what is inside the double quotes is masked",
    "MSK-113": "Authentication set to none (no value)",
    "MSK-117": "Specification of a cipher algorithm name (an example from the public corpus)",
    "MSK-120": "List of cipher algorithm names (not a secret)",
    "MSK-125": "There are two keys on the line. Only the confirmed words (esp-des, esp-3des, esp-md5, ah-md5) are listed, so a form such as esp-aes does not match the table and is not sent",
    "MSK-127": "Both the PIN and the PUK are masked",
    "MSK-130": "In the `show config` of a real device the value is `*`. `*` is masked too (the shape of the value is not looked at)",
    "MSK-132": "A user without authentication (the official syntax allows authentication to be omitted)",
    "MSK-137": "Only what is inside the double quotes is masked",
    "MSK-138": "Only what is inside the double quotes is masked",
    "MSK-139": "Only what is inside the double quotes is masked",
    "MSK-140": "MACsec key. Only what is inside the double quotes is masked",
    "MSK-146": "Under snmp-community of SNMPv3. The line ends with `;`",
    "MSK-148": "Only under `ltm monitor`. Not applied to the Junos form `snmp-community <name> {`",
    "MSK-150": "Cipher specification under `ltm cipher` (not a secret)",
    "MSK-155": "A public key is not a secret, but the body of the key is masked, as in MSK-03. In Junos the key type and the comment are also inside the double quotes, so everything inside the quotes is masked",
    "MSK-156": "List of SSH cipher algorithm names (not a secret)",
    "MSK-157": "Name of the MACsec cipher algorithm (not a secret)",
    "MSK-158": "Heading of the profile (the key is on the child secret line. MSK-45)",
    "MSK-159": "Authentication method of the LTE modem (no value)",
    "MSK-160": "Block heading. The line ends with a secret word and has no value",
    "MSK-161": "List of SSH cipher algorithm names (not a secret)",
    "MSK-162": "Specification of the SSL and SSH ciphers (not a secret)",
    "MSK-163": "Cipher specification of the SSL profile (not a secret)",
    "MSK-164": "Approved addition by the designer. Only the plain value is masked; unknown preceding or following words and forms other than plain are not sent",
}
# 出どころの英語の版(日本語の出どころだけ。ほかはもともと英語)
_SOURCES_EN: dict[str, str] = {
    "共通": "Common",
    "富士通 Si-R": "Fujitsu Si-R",
    "富士通 SR-S": "Fujitsu SR-S",
    "富士通 SR-S / Si-R": "Fujitsu SR-S / Si-R",
    "ヤマハ RTX / NVR": "Yamaha RTX / NVR",
}
# 例の中の「伏せる位置」の印(日本語の版は `<値>`)。英語の版の表示だけ `<value>` にする
_EXAMPLE_MARK_JA = "<値>"
_EXAMPLE_MARK_EN = "<value>"


def shown_note(p: MaskPattern) -> str:
    """利用者に見せる備考。英語のときは MASK_NOTES_EN(備考のある表の行に英語が無ければ日本語のまま)。"""
    if p.note and i18n.current_language() == i18n.EN:
        return MASK_NOTES_EN.get(p.id, p.note)
    return p.note


def shown_source(p: MaskPattern) -> str:
    """利用者に見せる出どころ。英語のときは日本語の出どころだけ英語にする。"""
    if i18n.current_language() == i18n.EN:
        return _SOURCES_EN.get(p.source, p.source)
    return p.source


def shown_example(p: MaskPattern) -> str:
    """利用者に見せる例。英語のときだけ `<値>` を `<value>` にする(MaskPattern.example は変えない)。"""
    if i18n.current_language() == i18n.EN:
        return p.example.replace(_EXAMPLE_MARK_JA, _EXAMPLE_MARK_EN)
    return p.example


_ID = re.compile(r"MSK-\d{2,}")

# 秘密語は無いが、値に秘密か認証の ID が入る行の形(ADR-0137 決定3)。行(語を1つの空白で区切ったもの)に fullmatch すれば、
# 表を引かずに送らない。新しい形が分かったら足す。UNS-06〜09 は ADR-0140 決定4(ASA)。
# UNS-09 は URL の中のパスワード(`<英字>://<語>:<語>@`)を行のどこかで探すため、前後を `.*` にする(行は改行を含まない)
# UNS-10〜14 は ADR-0141(HSRP・VRRP の認証とセルラーのモデム)。key-string の行は表の MSK-24 で伏せるため除く
# UNS-15 は ADR-0161(FortiGate の SNMP コミュニティ名。v1・v2c では実質のパスワード)。語 name を秘密語にすると
# ほとんどの名前の行が送れなくなるため、場所を `config system snmp community` に限る(場所が分からない入力では当たらない)
# UNS-16 は ADR-0191(富士通 Si-R の BGP の認証の鍵。`bgp neighbor <n> authentication <値> [encrypted]`)。語が authentication で
# 秘密語に当たらず、語を秘密語にすると無害な行まで当たるため、形で送らない。Si-R の config は字下げが無いので場所の条件は付けない
UNSENT_FORMS: tuple[tuple[str, str], ...] = (
    ("UNS-01", r"ip nhrp authentication .+"),
    ("UNS-02", r"glbp \d+ authentication .+"),
    ("UNS-03", r"token .+"),
    ("UNS-04", r"cellular .* lte profile .*"),
    ("UNS-05", r"ppp (?:chap hostname|pap sent-username|eap identity) .+"),
    ("UNS-06", r"logging ftp-server .+"),
    ("UNS-07", r"license .+"),
    ("UNS-08", r"ssh authentication publickey .+"),
    ("UNS-09", r".*[a-z]+://[^/\s:@]+:[^/\s@]+@.*"),
    ("UNS-10", r"standby (?:\d+ )?authentication (?!.*\bkey-string\b).+"),
    ("UNS-11", r"vrrp \d+ (?:peer )?authentication text (?!.*\bkey-string\b).+"),
    ("UNS-12", r"authentication (?!.*\bkey-string\b).+"),
    ("UNS-13", r"(?:no )?(?:lte|gsm|cdma) .+"),
    ("UNS-14", r"profile id \d+ apn .+"),
    ("UNS-15", r"set name .+"),
    ("UNS-16", r"bgp neighbor \d+ authentication .+"),
)
# 形の一覧の場所の条件(書き方は表の scope と同じ)。ここに無い形はどこでも当てる
UNSENT_FORM_SCOPES: dict[str, str] = {
    "UNS-03": "parameter-map type",
    "UNS-07": "scansafe",
    "UNS-12": "hsrp",
    "UNS-15": "config system snmp community",
}
# 形の一覧の頭(REQ-198)。割れた秘密の行のかたまりの判定にだけ使い、値が次の行にある「頭だけの行」を見つける。
# 形の一覧の全部の ID に1つずつ置く。UNS-09 は行が URL の途中(`<英字>://` の後ろに `/` が無い)で終わる形
UNSENT_FORM_HEADS: tuple[tuple[str, str], ...] = (
    ("UNS-01", r"ip nhrp authentication"),
    ("UNS-02", r"glbp \d+ authentication"),
    ("UNS-03", r"token"),
    ("UNS-04", r"cellular .* lte profile"),
    ("UNS-05", r"ppp (?:chap hostname|pap sent-username|eap identity)"),
    ("UNS-06", r"logging ftp-server"),
    ("UNS-07", r"license"),
    ("UNS-08", r"ssh authentication publickey"),
    ("UNS-09", r".*[a-z]+://[^/\s@]*"),
    ("UNS-10", r"standby (?:\d+ )?authentication"),
    ("UNS-11", r"vrrp \d+ (?:peer )?authentication(?: text)?"),
    ("UNS-12", r"authentication"),
    ("UNS-13", r"(?:no )?(?:lte|gsm|cdma)"),
    ("UNS-14", r"profile id \d+(?: apn)?"),
    ("UNS-15", r"set name"),
    ("UNS-16", r"bgp neighbor \d+ authentication"),
)

# 行の頭の形の入口(ADR-0198 決定2。REQ-277)。秘密語が無くても、行(字下げを除き、語を1つの空白で区切ったもの)の頭から
# match するなら、秘密語に当たったときと同じく表を引く(mask なら伏せて送り、pass なら送り、どれにも当たらなければ送らない)。
# 形の一覧(UNSENT_FORMS)は「当たれば送らない」までで、表を引く入口はここだけ。値の形は見ない(ADR-0019 決定1)。
# GH-01 は version 3 の行(利用者名だけで秘密が無い。MSK-17 の pass)を外す。外すのは本当の v3 の構文
# (`<アドレス> [vrf <名>] [traps|informs] version 3 auth|noauth|priv `)だけ。行のどこかに `version 3` があるだけでは外さない
# (`traps "<値> version 3" version 2c` のように、コミュニティ名の中に現れうるため)。先読みを `\S+` より前に置くのは、
# `\S+` が後ろへ戻って照合し直し、v3 の行に当たるのを防ぐため。穴の番号は docs/notes/masking-holes-2026-11.md
GATE_HEADS: tuple[tuple[str, str], ...] = (
    (
        "GH-01",
        r"snmp-server host (?!\S+(?: vrf \S+)?(?: (?:traps|informs))? version 3 (?:auth|noauth|priv) )\S+",
    ),  # H-01・H-02(Cisco 系・AW+・AX・IOS-XR)
    ("GH-02", r"snmp-agent (?:ip|ipv6) (?:host|trap) "),  # H-12(NEC)
    ("GH-03", r"rmon event \d+ .*\btrap "),  # H-03(AX)
    ("GH-04", r"vrrp \d+ authentication "),  # H-04(AX・NEC)
    ("GH-05", r"authentication text (?:plaintext|ciphertext) "),  # H-05(AOS-CX)
    ("GH-06", r"ip rip authentication (?:text|string) "),  # H-14(NEC・AW+)
    # GH-07〜12 は REQ-278(ADR-0198 決定2)。ヤマハ・NEC・Si-R
    ("GH-07", r"login user \S+ "),  # H-16(ヤマハ。名前だけの行 `login user <名>` は頭に当たらない)
    ("GH-08", r"snmpv3 usm user "),  # H-17(ヤマハ)
    ("GH-09", r"mail server pop \d+ "),  # H-18(ヤマハ)
    ("GH-10", r"ipsec ike (?:xauth|eap) myname "),  # H-19(ヤマハ)
    ("GH-11", r"mobile pin-auth enable "),  # H-15(NEC)
    ("GH-12", r"ike preshared-key add "),  # H-43(Si-R。書式が未確認なので表の行は足さず、送らない側に落とす)
    ("GH-13", r"usbmem authentication "),  # H-11。設計側の承認による ADR-0198 範囲外の追加
)
# 行の頭の形の場所の条件(書き方は UNSENT_FORM_SCOPES と同じ)。ここに無い形はどこでも当てる
GATE_HEAD_SCOPES: dict[str, str] = {
    "GH-05": "vrrp",
}

_FORM_ID = re.compile(r"UNS-\d{2,}")
_GATE_HEAD_ID = re.compile(r"GH-\d{2,}")


@lru_cache(maxsize=None)
def compiled(regex: str) -> re.Pattern[str]:
    return re.compile(regex)


def validate(
    patterns: tuple[MaskPattern, ...] = MASK_PATTERNS,
    secret_words: tuple[str, ...] = SECRET_WORDS,
    gate_heads: tuple[tuple[str, str], ...] = GATE_HEADS,
) -> None:
    """表の形を検査する。ID の重複・形、mask と pass の名前付きグループ、例の自己照合とゲートの通過。

    ゲートの通過は、例が秘密語を含むか、行の頭の形(gate_heads)のどれかに頭から当たること(ADR-0198 決定2)。
    """
    seen: set[str] = set()
    words = set(secret_words)
    heads = [compiled(regex) for _, regex in gate_heads]
    for p in patterns:
        if not _ID.fullmatch(p.id):
            raise ValueError(f"伏せる表の ID の形が不正です: {p.id}")
        if p.id in seen:
            raise ValueError(f"伏せる表の ID が重複しています: {p.id}")
        seen.add(p.id)
        if p.kind not in ("mask", "pass"):
            raise ValueError(f"{p.id}: kind は mask か pass にしてください: {p.kind!r}")
        if not p.scope or p.scope != p.scope.strip():
            raise ValueError(f"{p.id}: scope が空か、前後に空白があります")
        regex = compiled(p.match)
        if p.kind == "mask" and not regex.groupindex:
            raise ValueError(f"{p.id}: mask には伏せる位置(名前付きグループ)が要ります")
        if p.kind == "pass" and regex.groupindex:
            raise ValueError(f"{p.id}: pass に名前付きグループがあります(伏せる対象なしの行です)")
        if regex.fullmatch(p.example) is None:
            raise ValueError(f"{p.id}: 例が自分の書式に当たりません: {p.example!r}")
        example = " ".join(p.example.split())
        if not words.intersection(p.example.split()) and not any(head.match(example) for head in heads):
            raise ValueError(f"{p.id}: 例が秘密語も行の頭の形も含みません(ゲートを通らない行は表を引きません)")


def validate_gate_heads(
    heads: tuple[tuple[str, str], ...] = GATE_HEADS, scopes: dict[str, str] | None = None
) -> None:
    """行の頭の形を検査する。ID の形・重複、正規表現が読めること、場所の条件が一覧の ID を指し、空でないこと。"""
    scopes = GATE_HEAD_SCOPES if scopes is None else scopes
    seen: set[str] = set()
    for head_id, regex in heads:
        if not _GATE_HEAD_ID.fullmatch(head_id):
            raise ValueError(f"行の頭の形の ID の形が不正です: {head_id}")
        if head_id in seen:
            raise ValueError(f"行の頭の形の ID が重複しています: {head_id}")
        seen.add(head_id)
        try:
            compiled(regex)
        except re.error as exc:
            raise ValueError(f"{head_id}: 正規表現が読めません: {exc}") from exc
    unknown = sorted(set(scopes) - seen)
    if unknown:
        raise ValueError(f"場所の条件が行の頭の形に無い ID を指しています: {unknown}")
    for head_id, scope in scopes.items():
        if not scope or scope != scope.strip():
            raise ValueError(f"{head_id}: 場所の条件が空か、前後に空白があります")


def validate_forms(
    forms: tuple[tuple[str, str], ...] = UNSENT_FORMS, heads: tuple[tuple[str, str], ...] = UNSENT_FORM_HEADS
) -> None:
    """形の一覧を検査する。ID の形・重複、正規表現が読めること、場所の条件が一覧の ID を指すこと。

    形の頭(REQ-198)は、一覧の全部の ID に1つずつあり、余分な ID が無く、正規表現が読めること。
    """
    seen: set[str] = set()
    for form_id, regex in forms:
        if not _FORM_ID.fullmatch(form_id):
            raise ValueError(f"形の一覧の ID の形が不正です: {form_id}")
        if form_id in seen:
            raise ValueError(f"形の一覧の ID が重複しています: {form_id}")
        seen.add(form_id)
        compiled(regex)
    unknown = sorted(set(UNSENT_FORM_SCOPES) - seen)
    if unknown:
        raise ValueError(f"場所の条件が形の一覧に無い ID を指しています: {unknown}")
    head_ids: set[str] = set()
    for form_id, regex in heads:
        if form_id in head_ids:
            raise ValueError(f"形の頭の ID が重複しています: {form_id}")
        head_ids.add(form_id)
        compiled(regex)
    missing = sorted(seen - head_ids)
    if missing:
        raise ValueError(f"形の一覧の ID に頭がありません: {missing}")
    extra = sorted(head_ids - seen)
    if extra:
        raise ValueError(f"形の頭が形の一覧に無い ID を指しています: {extra}")


@dataclass(frozen=True)
class MaskingFingerprint:
    """伏せる表の指紋。digest は SHA-256 の先頭16文字、count は件数。"""

    digest: str
    count: int


def masking_fingerprint(patterns: tuple[MaskPattern, ...] | None = None) -> MaskingFingerprint:
    """id / scope / match / kind を表の順序のまま並べてハッシュする(ADR-0011 の指紋と同じ考え方)。

    形の一覧(id / 正規表現 / 場所の条件)もいつも含める(ADR-0137 決定3)。形の頭(id / 正規表現)も、かたまりの判定が
    変わるため含める(REQ-198)。行の頭の形(id / 正規表現 / 場所の条件。ADR-0198)も含める。count は表の件数。example / source / note は含めない(例や備考の修正で指紋を動かさない)。
    """
    table = MASK_PATTERNS if patterns is None else patterns
    items = [[p.id, p.scope, p.match, p.kind] for p in table]
    forms = [[form_id, regex, UNSENT_FORM_SCOPES.get(form_id, "*")] for form_id, regex in UNSENT_FORMS]
    heads = [[form_id, regex] for form_id, regex in UNSENT_FORM_HEADS]
    gate_heads = [[head_id, regex, GATE_HEAD_SCOPES.get(head_id, "*")] for head_id, regex in GATE_HEADS]
    document: dict[str, object] = {"patterns": items, "unsent_forms": forms, "unsent_form_heads": heads}
    document["gate_heads"] = gate_heads  # 行の頭の形は判定を変えるため含める(ADR-0198 決定2。REQ-277)
    text = json.dumps(document, ensure_ascii=False, separators=(",", ":"))
    return MaskingFingerprint(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], len(table))


validate_gate_heads()
validate()
validate_forms()
