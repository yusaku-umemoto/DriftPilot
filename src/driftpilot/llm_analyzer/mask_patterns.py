"""LLM へ送る前に伏せる位置を決める表(ADR-0019 決定2・決定3)。書式は ADR-0011 のパターン表と同じ。

表を引くのは、秘密語ゲート(masking.py)を通った行、つまり秘密語をトークン完全一致で含む行
(と、名前が秘密語の XML のタグで始まるか閉じるタグを含む行。ADR-0140 決定2)だけである。
秘密語を含まない行は表を引かずにそのまま送る(決定4)。

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
)

# --- 利用者に見える説明の英語の版(REQ-259 / ADR-0190 決定3) ---
# 送る前の確かめ・`driftpilot mask-patterns` の出力に出る備考・出どころ・例を、言語に合わせて出す。
# 伏せる・送らないの判定(match・scope・kind・秘密語・送らない形・指紋の計算)には使わない。MaskPattern の値は日本語のまま変えない
# (例は表の自己照合とテストの入力に使う)。備考のある 44 件すべてに英語がある(ID → 英語。テストが鍵を照らす)
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
}
# 出どころの英語の版(日本語の出どころだけ。ほかはもともと英語)
_SOURCES_EN: dict[str, str] = {
    "共通": "Common",
    "富士通 Si-R": "Fujitsu Si-R",
    "富士通 SR-S": "Fujitsu SR-S",
    "富士通 SR-S / Si-R": "Fujitsu SR-S / Si-R",
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

_FORM_ID = re.compile(r"UNS-\d{2,}")


@lru_cache(maxsize=None)
def compiled(regex: str) -> re.Pattern[str]:
    return re.compile(regex)


def validate(patterns: tuple[MaskPattern, ...] = MASK_PATTERNS, secret_words: tuple[str, ...] = SECRET_WORDS) -> None:
    """表の形を検査する。ID の重複・形、mask と pass の名前付きグループ、例の自己照合とゲートの通過。"""
    seen: set[str] = set()
    words = set(secret_words)
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
        if not words.intersection(p.example.split()):
            raise ValueError(f"{p.id}: 例が秘密語を含みません(ゲートを通らない行は表を引きません)")


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
    変わるため含める(REQ-198)。count は表の件数。example / source / note は含めない(例や備考の修正で指紋を動かさない)。
    """
    table = MASK_PATTERNS if patterns is None else patterns
    items = [[p.id, p.scope, p.match, p.kind] for p in table]
    forms = [[form_id, regex, UNSENT_FORM_SCOPES.get(form_id, "*")] for form_id, regex in UNSENT_FORMS]
    heads = [[form_id, regex] for form_id, regex in UNSENT_FORM_HEADS]
    text = json.dumps(
        {"patterns": items, "unsent_forms": forms, "unsent_form_heads": heads}, ensure_ascii=False, separators=(",", ":")
    )
    return MaskingFingerprint(hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], len(table))


validate()
validate_forms()
