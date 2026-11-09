"""show 側の被覆率の分母: 比較対象とする show コマンドの表(ADR-0011 決定8、REQ-17)。

この一覧は設計側が管理する。docs/notes/show-commands.md はこの表から生成する(command_docs.py)。
platform ごとのテンプレートの有無は持たない。それは coverage-show が parse_output で実測する。
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ShowCommand:
    """比較対象とする show コマンド1つ。

    id:      "<分類>.<名前>"。分類は内訳に使う(Pattern.id と同じ規則)
    command: 比較対象とする表記
    note:    FortiGate の別コマンド名など
    """

    id: str
    command: str
    note: str = ""

    @property
    def category(self) -> str:
        return self.id.split(".", 1)[0]


# 分類 ID → 見出し。並びが出力の順
CATEGORIES: dict[str, str] = {
    "routing": "経路・近隣系",
    "interfaces": "インターフェース状態系",
    "l2": "L2・冗長系",
    "device": "機器・負荷系",
}

SHOW_COMMANDS: tuple[ShowCommand, ...] = (
    ShowCommand("routing.ip_route", "show ip route"),
    ShowCommand("routing.ip_arp", "show ip arp", "FortiGate は `get system arp`"),
    ShowCommand("routing.cdp_neighbors", "show cdp neighbors"),
    ShowCommand("routing.lldp_neighbors", "show lldp neighbors"),
    ShowCommand("interfaces.status", "show interfaces status", "FortiGate は `get system interface physical`"),
    ShowCommand("interfaces.ip_interface_brief", "show ip interface brief", "FortiGate は `get system interface`"),
    ShowCommand("interfaces.description", "show interfaces description"),
    ShowCommand("interfaces.etherchannel_summary", "show etherchannel summary"),
    ShowCommand(
        "l2.mac_address_table",
        "show mac address-table",
        "ntc-templates のテンプレート名はハイフン表記(`cisco_ios_show_mac-address-table.textfsm`)。"
        "コマンドからファイル名を組み立てない",
    ),
    ShowCommand("l2.vlan", "show vlan"),
    ShowCommand("l2.spanning_tree", "show spanning-tree"),
    ShowCommand("l2.standby_brief", "show standby brief", "HSRP"),
    ShowCommand(
        "l2.vrrp",
        "show vrrp",
        "cisco_ios のテンプレートは下位コマンド(`show vrrp brief` / `show vrrp all`)にだけある。"
        "FortiGate の `get system ha status` は HA のみ",
    ),
    ShowCommand("device.version", "show version", "FortiGate は `get system status`"),
    ShowCommand("device.inventory", "show inventory", "FortiGate は `get hardware nic`"),
    ShowCommand(
        "device.environment",
        "show environment",
        "cisco_ios のテンプレートは下位コマンド(`show environment temperature` / `show environment power all`)にだけある",
    ),
    ShowCommand("device.processes_cpu", "show processes cpu", "FortiGate は `diagnose sys top`"),
    ShowCommand("device.processes_memory", "show processes memory", "FortiGate は `diagnose sys top`"),
)


def match_command(text: str, commands: tuple[ShowCommand, ...] = SHOW_COMMANDS) -> ShowCommand | None:
    """区間のコマンドが表のどの表記に当たるかを返す。内訳と「現れなかった一覧」にだけ使う。

    `|` 以降は除く。語の数が同じで、各語が表記の語の先頭と一致すれば一致とする(`sh ip int br`)。
    引数の入ったもの(`show int Gi1/0/1 status`)は一致させない。複数に当たるときは None。
    テンプレートの有無の判定には使わない(それは parse_output の責務)。
    """
    words = text.split("|", 1)[0].lower().split()
    if not words:
        return None
    hits = [
        c
        for c in commands
        if len(c.command.split()) == len(words) and all(full.startswith(w) for w, full in zip(words, c.command.split()))
    ]
    return hits[0] if len(hits) == 1 else None
