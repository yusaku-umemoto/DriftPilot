"""最上位の機器全体の設定の行とブロックを IR の global.device_settings に写す extractor(ADR-0069・ADR-0106、REQ-80・REQ-87)。

読むのは、決まった語で始まる子を持たない1行(`license …`・`switch <数> provision …`・`rmon …` など。no 付きも)と、
決まった見出しのブロック(`redundancy`・`call-home`・`event manager applet <1語>`・`evpn`・`daemon <1語>` など)の中の行。
1行は原文を、ブロックの中の子を持たない行は見出しからの道すじを ` > ` でつないだ文字列を、1つの集合にする。
種類ごとに項目を分けない(ADR-0069 Option DB を採らない)。Flexible NetFlow の `flow …` もここで読み、
Arista の traffic_monitoring(ADR-0061)とはそろえない。`sflow` は読まない(ADR-0061 の範囲)。
ほかの書式が読む `clock timezone …`・`system mtu …`・`logging event …` は書式に入れない。
語のどれかが password・secret・key の行と、語の中に鍵が埋まった行(`-cvauth=key,…` など)は入れない(ADR-0106 決定2)。
既定値込みの出力に出る `power xps …`・`ip tcp …`・`cns id …` などの語も、同じ読み方で読む(ADR-0111 決定5、REQ-158)。
Arista の `ip address virtual source-nat …`・`kernel …` の行と、`patch panel`・`cvx`・`dhcp server [<残り>]` の
ブロックも、同じ読み方で読む(ADR-0114 決定2、REQ-159)。
`ip cef`・`netconf-yang`・`alias …`・`virtual-service …` などの行も、同じ読み方で読む(ADR-0116 決定3、REQ-161)。
既定値込みの出力に並ぶ `buffers …`・`ip admission …`・`ethernet cfm …`・`cts …`・`lacp …` などの機器全体の行も、
同じ読み方で読む(ADR-0117 決定1、REQ-162)。
Catalyst のスタックの `stack-mac …` の行と `stack-power …` のブロックも、同じ読み方で読む(ADR-0120、REQ-165)。
既定値込みの出力の残りの `subscriber …`・`ip access-list persistent`・`dot1x …`・`power inline …` などの行と、
`ethernet cfm <残り>`・`ip sla server <残り>` のブロックも、同じ読み方で読む(ADR-0121 決定1、REQ-166)。
IBNS 2.0 の `class-map type control …`・`policy-map type control …`・`service-template …`、RA ガードの
`ipv6 nd raguard policy …`、`device-tracking policy …`、`vlan access-map …` のブロックと、子を持たない `track …` の行も、
同じ読み方で読む(ADR-0122 決定1・2、REQ-167)。QoS の見出しの書式に当たる行(ISG の1語の type control)は QoS に残す。
IOS-XE の `fhrp …`・`gnmi-yang`・`gnxi`・`iox`・`vlan filter …` の行と、`ipv6 dhcp pool <残り>`・`sampler <残り>`・
`app-hosting <残り>`・`router lisp[ <残り>]`・`gnmi-yang <残り>`・`gnxi <残り>` のブロックも、同じ読み方で読む
(ADR-0123 決定1、REQ-168)。
`password` が値ではなく語として入る、値の無い決まった形の行(`ip ssh server authenticate user password` など)は、
秘密の行とみなさない(is_secret_exempt。management の判定も使う。ADR-0124 決定1、REQ-169)。
`monitor session <数> source|destination|filter …`・`ip nat …`・`wireless …` の1行と、起動のマーカーなど行全体で
決まる行、`wireless <残り>`・`ip nat profile <名前>` のブロックも、同じ読み方で読む(ADR-0126 決定4・5、REQ-171)。
`monitor session` の1行は、名前でも数でもよい `monitor session <1語> <残り>` に広げる(ADR-0133 決定1、REQ-178)。
Catalyst の `udld …`・`switch <数> priority …`・`privilege …` の1行と、`ip sla <数>`・`track <数> …`・
`object-group network|service <名前>`・`time-range <名前>` のブロックも、同じ読み方で読む(ADR-0128 決定1、REQ-173)。
`key chain` のブロックは読まない(ADR-0128 決定4)。
Cisco の WAN と NX-OS の `sdm …`・`ip source binding …`・`dialer-list …`・`dialer watch-list …`・`nv overlay …`・
`fabric forwarding …`・`ipv6 general-prefix …`・`zone security …`・`zone-pair security …`・`parameter-map type inspect …`・
`parameter-map type inspect-global`・`vpc domain …` の1行と、`zone security <名前>`・`zone-pair security <残り>`・
`parameter-map type inspect[-global] [<残り>]`・`vpc domain <数>`・`controller T1|E1|T3|E3 <残り>`・`voice service <語>`・
`voice class <残り>`・`dial-peer voice <残り>`・`sip-ua` のブロックも、同じ読み方で読む(ADR-0136 決定1、REQ-182)。
`parameter-map type` の `inspect` 以外(`umbrella` など)と `controller Cellular …` は読まない(ADR-0136 決定4)。
"""

import re
from bisect import insort
from dataclasses import dataclass, field, replace

from hier_config import HConfig, HConfigChild

from driftpilot.config_parser.patterns import Pattern, apply_first
from driftpilot.config_parser.qos import PATTERNS as _QOS_PATTERNS
from driftpilot.config_parser.tracking import InterpretedLines

# 語のどれかがこれらの行は入れない(未解釈に残す。REQ-80 §3)。大文字小文字は区別しない
_SECRET_WORDS = frozenset({"password", "secret", "key"})
# 語の中に鍵が埋まった形。行のどこかに含まれれば入れない(未解釈に残す。REQ-87 §3)。
# `(?i)(key|password|passwd|secret)[,=:]` と同じ判定を、表の外の正規表現を使わずに書く
_EMBEDDED_SECRETS = tuple(word + sep for word in ("key", "password", "passwd", "secret") for sep in ",=:")
# 秘密の行とみなさない、値の無い決まった形(ADR-0124 決定1、REQ-169)。語の並びで比べる。大文字小文字は区別する。
# 頭の後ろに方式の語が1つ以上続き、どれも方式の語なら当たる
_EXEMPT_METHODS = frozenset({"password", "publickey", "keyboard"})
_EXEMPT_METHOD_HEADS = (
    ("ip", "ssh", "server", "authenticate", "user"),
    ("ip", "ssh", "server", "algorithm", "authentication"),
)
# 語の並びがこのとおりなら当たる
_EXEMPT_LINES = frozenset({
    ("mpls", "ldp", "logging", "password", "configuration"),
    ("mpls", "ldp", "logging", "password", "rollover"),
})
_SEPARATOR = " > "
_BLOCK_SCOPE = "device block"
# QoS が読む見出しの書式(qos.class_map・qos.policy_map)。当たるかだけを確かめるので、写す手続きは呼ばない。
# 行全体が当たる見出しは QoS に残し、ここでは読まない(同じ行を2か所で読まない。REQ-167)
_QOS_HEADINGS = tuple(
    replace(pattern, apply=lambda m, ctx: True)
    for pattern in _QOS_PATTERNS
    if pattern.id in ("qos.class_map", "qos.policy_map")
)


@dataclass
class _Ctx:
    values: list[str] = field(default_factory=list)
    has_children: bool = False
    # 見出しを読んだら True。中の行を読む
    in_block: bool = False
    # 中の行を読んでいる間の状態。見出しからその行までの行の文字列
    path: list[str] = field(default_factory=list)


def is_secret_exempt(text: str) -> bool:
    """行の語の並び全体が、秘密の行とみなさない決まった形(`[no] ip ssh server authenticate user password` など)なら True。"""
    words = tuple(text.split())
    if words[:1] == ("no",):
        words = words[1:]
    if words in _EXEMPT_LINES:
        return True
    return any(
        words[:len(head)] == head
        and len(words) > len(head)
        and all(word in _EXEMPT_METHODS for word in words[len(head):])
        for head in _EXEMPT_METHOD_HEADS
    )


def has_secret(*texts: str) -> bool:
    """語のどれかが password・secret・key か、語の中に鍵が埋まった形(`key,`・`password=` など)があれば True。

    is_secret_exempt に当たる text は判定から外す(ADR-0124 決定1、REQ-169)。
    """
    return any(
        not is_secret_exempt(text)
        and (
            any(form in text.casefold() for form in _EMBEDDED_SECRETS)
            or any(word.lower() in _SECRET_WORDS for word in text.split())
        )
        for text in texts
    )


def _add(values: list[str], text: str) -> None:
    """集合に足す。同じ文字列は重ねず、文字列の昇順の位置に入れる。"""
    if text not in values:
        insort(values, text)


def _setting_line(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行だけ、原文を集合に足す。子を持つ行は子も含めて未解釈。"""
    if ctx.has_children or has_secret(m.string):
        return False
    _add(ctx.values, m.string)
    return True


def _block(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持つ見出しだけ読む。見出しは文字列にしない。子の無い見出しと、QoS が読む見出しは未解釈。"""
    if not ctx.has_children or has_secret(m.string):
        return False
    if apply_first(_QOS_HEADINGS, "top", m.string, None) is not None:
        return False
    ctx.in_block = True
    return True


def _block_setting(m: re.Match[str], ctx: _Ctx) -> bool:
    """子を持たない行は見出しからの道すじを集合に足す。子を持つ行は解釈済みにだけする。"""
    if has_secret(*ctx.path):
        return False
    if not ctx.has_children:
        _add(ctx.values, _SEPARATOR.join(ctx.path))
    return True


_SECRET_NOTE = "語のどれかが password・secret・key か、語の中に鍵が埋まった(key, や password= などの)"

# 並び＝優先順位。最上位の行は、子を持たなければ1行の書式だけ、子を持てば見出しの書式だけに通す
# (`hardware …`・`queue-monitor …` は両方の書き出しに当たる)。中の行は scope が違う。
PATTERNS: tuple[Pattern, ...] = (
    Pattern(
        "global.device_setting", "top",
        r"(?:no )?(?:switch \d+ provision |license |diagnostic |login on-success|login on-failure|login block-for"
        r"|login delay|login quiet-mode|platform |ip forward-protocol|hw-module |power redundancy-mode"
        r"|ip dhcp snooping|ip dhcp relay|ip arp inspection|device-tracking |errdisable "
        r"|rmon |copp profile |cfs |boot nxos |boot system |boot kickstart |transceiver qsfp |hardware "
        r"|queue-monitor |load-interval |system (?!mtu(?: |$))|lldp |cdp |ip ftp |ip tftp "
        r"|clock (?!timezone(?: |$))|power xps |cef table |ip tcp |ip telnet |mac address-table notification"
        r"|login password-warning-interval|mab request|cns id |ip address virtual source-nat |kernel "
        r"|multilink bundle-name|ip icmp rate-limit |ip cef|ipv6 cef|ip bgp-community |subscriber templating"
        r"|memory free |alias |netconf-yang|restconf|virtual-service "
        r"|buffers |ipc |spd |ip spd |ipv6 spd |memory |parser |process |process-max-time |scheduler "
        r"|exception |exception-slave |default-value |downward-compatible-config |file |audit |bulkstat "
        r"|shell |scripting |setup |issu |software auto-upgrade|configuration mode |autoconf "
        r"|service-routing |routing-default-optimize|route-tag |object-group knob|metadata "
        r"|ethernet cfm |event manager (?!applet(?: |$))|monitor event-trace |location |nmsp |wsma |xmpp "
        r"|xdr |netconf |cns image |l2 traceroute|rep "
        r"|ip admission |ip dhcp (?!snooping|relay|pool(?: |$))|ip dhcp-client |ip dhcp-server |ip rsvp "
        r"|ip accounting-threshold |ip gratuitous-arps|ip icmp |ip source-route|ip subnet-zero|ip classless"
        r"|ip bootp |ip finger|ip sla (?!server(?: |$))|ip arp (?!inspection)|ip device tracking|ip nbar "
        r"|ip nsf-converge|ip sap |ip sticky-arp|psp |ppp "
        r"|ipv6 nd |ipv6 dhcp |ipv6 dhcp-relay |ipv6 icmp |ipv6 hop-limit |ipv6 reassembly-queue"
        r"|ipv6 traffic |ipv6 snooping |ipv6 nsf-converge"
        r"|cts |authentication |access-session |mab |device-sensor |device classifier|cisp |crypto pki crl "
        r"|crypto mib |crypto call |crypto engine |mka |mvrp |clns "
        r"|port-channel |lacp |stack-mac "
        r"|subscriber |transport type |ip route static |ip access-list helper |ip access-list persistent"
        r"|ip access-list logging |ipv6 access-list log-update |ipv6 neighbor binding|ipv6 neighbor tracking"
        r"|ip verify drop-rate |isis display |power inline |boot auto-copy-sw|boot config|eap |dot1x |ipv6 rip "
        r"|login password-reuse-interval|track |fhrp |(?:gnmi-yang|gnxi|iox)(?: |$)|vlan filter "
        r"|monitor session \S+ |ip nat |wireless "
        r"|udld |switch \d+ priority |privilege "
        r"|sdm |ip source binding |dialer-list |dialer watch-list |nv overlay |fabric forwarding |ipv6 general-prefix "
        r"|zone security |zone-pair security |parameter-map type inspect |vpc domain ).*"
        r"|(?:no )?(?:vlan dot1Q tag native|redundancy|ip prefix-list sequence-number|ipv6 prefix-list sequence-number"
        r"|boot-start-marker|boot-end-marker|parameter-map type inspect-global)",
        "global.device_settings", "license boot level network-advantage", _setting_line,
        note=(
            "手続き。子を持たない行の原文を device_settings の集合に足す(重ねない。昇順)。"
            f"子を持つ行と、{_SECRET_NOTE}行は、子も含めて未解釈。"
            "clock timezone・system mtu(no 付きも)と logging event は、ほかの書式が読むので当てない。"
            "power xps・cef table・ip tcp・ip telnet・mac address-table notification・"
            "login password-warning-interval・mab request・cns id は ADR-0111 決定5。"
            "ip address virtual source-nat・kernel は ADR-0114 決定2。"
            "multilink bundle-name・ip icmp rate-limit・ip cef・ipv6 cef・ip bgp-community・subscriber templating・"
            "memory free・alias・netconf-yang・restconf・virtual-service は ADR-0116 決定3。"
            "既定値込みの出力の語(資源・処理の buffers・memory・parser など、見張り・管理の ethernet cfm・"
            "event manager など、IP の ip admission・ip dhcp・ip icmp など、IPv6 の ipv6 nd・ipv6 dhcp など、"
            "認証・暗号の cts・authentication・crypto engine など、束ねの port-channel・lacp)は ADR-0117 決定1。"
            "stack-mac(後ろの語が要る)は ADR-0120、REQ-165。"
            "subscriber・transport type・ip route static・ip access-list helper・ip access-list persistent・"
            "ip access-list logging・ipv6 access-list log-update・ipv6 neighbor binding・ipv6 neighbor tracking・"
            "ip verify drop-rate・isis display・power inline・boot auto-copy-sw・boot config・eap・dot1x・ipv6 rip・"
            "login password-reuse-interval は ADR-0121 決定1、REQ-166(語の後ろに空白があるものは後ろの語が要る)。"
            "track(後ろの語が要る。子を持たない形)は ADR-0122 決定2、REQ-167。"
            "fhrp・vlan filter(後ろの語が要る)と gnmi-yang・gnxi・iox(その語だけの行も後ろの語がある行も。語の途中で切らない)は "
            "ADR-0123 決定1、REQ-168。cts manual の中と ntp authentication-key は読まない。"
            "event manager applet・ip dhcp pool・ip sla server は当てない。"
            "ip nat・wireless(後ろの語が要る)と、行全体が "
            "vlan dot1Q tag native・redundancy・ip prefix-list sequence-number・ipv6 prefix-list sequence-number・"
            "boot-start-marker・boot-end-marker の行(no 付きも。後ろに語がある行は当てない)は ADR-0126 決定4、REQ-171。"
            "udld・switch <数> priority・privilege(後ろの語が要る)は ADR-0128 決定1、REQ-173。"
            "monitor session <名前か数> <残り>(後ろの語が要る。後ろの語は何でもよい)は ADR-0133 決定1、REQ-178。"
            "Cisco の WAN と NX-OS の sdm・ip source binding・dialer-list・dialer watch-list・nv overlay・fabric forwarding・"
            "ipv6 general-prefix・zone security・zone-pair security・parameter-map type inspect・vpc domain(後ろの語が要る)と、"
            "行全体が parameter-map type inspect-global の行(no 付きも。後ろに語がある子の無い行は当てない)は "
            "ADR-0136 決定1、REQ-182"
        ),
    ),
    Pattern(
        "global.device_block", "top",
        r"redundancy|transceiver type \S+|call-home|event manager applet \S+|telemetry ietf subscription \S+"
        r"|flow (?:record|monitor|exporter) \S+|ip dhcp pool \S+|monitor session \d+ .+|archive|stackwise-virtual"
        r"|vdc \S+(?: id \d+)?|evpn|daemon \S+|stun|monitor connectivity|lldp|queue-monitor .+|hardware .+"
        r"|patch panel|cvx|dhcp server(?: .+)?|stack-power .+|ethernet cfm .+|ip sla server .+"
        r"|class-map type control .+|policy-map type control .+|service-template .+|ipv6 nd raguard policy .+"
        r"|device-tracking policy .+|vlan access-map .+|ipv6 dhcp pool .+|sampler .+|app-hosting .+"
        r"|router lisp(?: .+)?|gnmi-yang .+|gnxi .+|wireless .+|ip nat profile \S+"
        r"|ip sla \d+|track \d+ .+|object-group (?:network|service) \S+|time-range \S+"
        r"|zone security \S+|zone-pair security .+|parameter-map type inspect(?:-global)?(?: .+)?|vpc domain \d+"
        r"|controller (?:T1|E1|T3|E3) .+|voice service \S+|voice class .+|dial-peer voice .+|sip-ua",
        "global.device_settings", "redundancy", _block,
        note=(
            "手続き。子を持つ見出しだけ読み、中の行を device_settings に写す。見出しは文字列にしないが解釈済みにする。"
            f"子の無い見出しと、{_SECRET_NOTE}見出しは、見出しも中も未解釈。sflow は読まない。"
            "patch panel・cvx・dhcp server(後ろに語があっても)は ADR-0114 決定2。"
            "stack-power <残り> は ADR-0120、REQ-165。"
            "ethernet cfm <残り>・ip sla server <残り> は ADR-0121 決定1、REQ-166。"
            "class-map type control・policy-map type control は、QoS の見出しの書式(qos.class_map・qos.policy_map)に"
            "当たる行(ISG の1語の type control)を読まず QoS に残す(同じ行を2か所で読まない)。"
            "service-template・ipv6 nd raguard policy・device-tracking policy・vlan access-map とともに "
            "ADR-0122 決定1、REQ-167。"
            "ipv6 dhcp pool <残り>・sampler <残り>・app-hosting <残り>・router lisp(後ろに語があっても)・"
            "gnmi-yang <残り>・gnxi <残り> は ADR-0123 決定1、REQ-168。"
            "wireless <残り>・ip nat profile <名前> は ADR-0126 決定4・5、REQ-171(本物のブロックの中の行を、"
            "字下げの崩れの直しで兄弟に戻さないためにも足す)。"
            "ip sla <数>・track <数> <残り>・object-group network <名前>・object-group service <名前>・time-range <名前> は "
            "ADR-0128 決定1、REQ-173(子を持たない ip sla <数>・track … は今までどおり global.device_setting が読む。"
            "中の行を字下げの崩れの直しで兄弟に戻さない)。key chain は見出しも中も鍵の語なので読まない(ADR-0128 決定4)。"
            "Cisco の WAN と NX-OS の zone security <名前>・zone-pair security <残り>・"
            "parameter-map type inspect [<残り>]・parameter-map type inspect-global [<残り>]・vpc domain <数>・"
            "controller T1|E1|T3|E3 <残り>・voice service <語>・voice class <残り>・dial-peer voice <残り>・sip-ua は "
            "ADR-0136 決定1、REQ-182(子を持たない zone security …・zone-pair security …・parameter-map type inspect …・"
            "vpc domain … は global.device_setting が読む)。parameter-map type の inspect 以外(umbrella など)と "
            "controller Cellular … は読まない(ADR-0136 決定4)"
        ),
    ),
    Pattern(
        "global.device_block.setting", _BLOCK_SCOPE, r".+", "global.device_settings", "mode sso", _block_setting,
        note=(
            "手続き。ブロックの中の子を持たない行を、見出しからの道すじを ` > ` でつないだ文字列にして集合に足す"
            "(見出しを含める。同じ文字列は重ねず、文字列の昇順)。子を持つ行は文字列にしないが解釈済みにする。"
            f"道すじのどの段かが、{_SECRET_NOTE}行なら、その行も子孫も未解釈(同じブロックのほかの行は読む)"
        ),
    ),
)
_LEAF_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id != "global.device_block")
_BLOCK_PATTERNS = tuple(pattern for pattern in PATTERNS if pattern.id != "global.device_setting")


def _read_block(node: HConfigChild, prefix: list[str], ctx: _Ctx, interpreted: InterpretedLines) -> None:
    """ブロックの中の行を出現順に読む。読めなかった行の子孫は読まない(未解釈に残す)。"""
    for child in node.children:
        ctx.path = [*prefix, child.text]
        ctx.has_children = bool(child.children)
        pattern = apply_first(PATTERNS, _BLOCK_SCOPE, child.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(child, pattern.id)
        if child.children:
            _read_block(child, ctx.path, ctx, interpreted)


def extract_device_settings(tree: HConfig, interpreted: InterpretedLines) -> list[str]:
    """最上位の機器全体の設定の行とブロックを、文字列の集合(昇順)にして返す。"""
    ctx = _Ctx()
    for block in tree.children:
        ctx.has_children = bool(block.children)
        ctx.in_block = False
        patterns = _BLOCK_PATTERNS if block.children else _LEAF_PATTERNS
        pattern = apply_first(patterns, "top", block.text, ctx)
        if pattern is None:
            continue
        interpreted.mark(block, pattern.id)
        if ctx.in_block:
            _read_block(block, [block.text], ctx, interpreted)
    return ctx.values
