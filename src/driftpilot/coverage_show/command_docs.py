"""show コマンドの表から一覧(Markdown / JSON)を生成する(REQ-17)。

出力は決定的にする(並びと改行を固定)。docs/notes/show-commands.md はこの出力そのもので、
ずれたらテストが失敗する(ADR-0011 の parser-patterns.md と同じ運用)。
"""

import json
from typing import Any

from driftpilot.coverage_show.commands import CATEGORIES, SHOW_COMMANDS, ShowCommand

GENERATE_COMMAND = "driftpilot show-commands --format markdown --output docs/notes/show-commands.md"

_HEADER = f"""<!-- 自動生成。手で編集しない。 -->
# 比較対象とする show コマンドの一覧(show 側の被覆率の分母)

> **自動生成。手で編集しない。** コード内の表(`src/driftpilot/coverage_show/commands.py` の `SHOW_COMMANDS`)から作る。
> 再生成: `{GENERATE_COMMAND}`
> 表を変えたのにこのファイルがずれていると、テストが失敗する。

`driftpilot coverage-show` の特定率・テンプレート保有率の分母(ADR-0011 決定8)。この一覧は設計側が管理する。
対象に入れることは「今すぐ実装する」ことを意味しない。

**テンプレートの有無はこの表に持たない。** `driftpilot coverage-show --platform NAME` で実測する
(ntc-templates の `parse_output` を呼んで判定する。REQ-07 の比較本体と同じ経路)。
テンプレートが無いコマンドは raw 比較に落ちる(比較は止まらない)。
2026-10-16 時点の機種別の記録は docs/notes/req-17-show-coverage.md に移した。

**`cisco_xe` という platform は存在しない。** IOS-XE の出力は `cisco_ios` で解析する(`--platform cisco_xe` は拒否する)。

入力のコマンドとこの表の突き合わせは、語ごとの前方一致で行う(`sh ip int br` は `show ip interface brief` に当たる)。
`|` 以降は見ない。引数の入ったコマンドは当てない。
"""


def _record(command: ShowCommand) -> dict[str, Any]:
    return {"id": command.id, "category": command.category, "command": command.command, "note": command.note}


def render_json(commands: tuple[ShowCommand, ...] = SHOW_COMMANDS) -> str:
    payload = {"count": len(commands), "commands": [_record(c) for c in commands]}
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(commands: tuple[ShowCommand, ...] = SHOW_COMMANDS) -> str:
    out = [_HEADER, f"{len(commands)} コマンド。\n"]
    for category, title in CATEGORIES.items():
        rows = [c for c in commands if c.category == category]
        if not rows:
            continue
        out.append(f"## {category}: {title}\n")
        out.append("| ID | コマンド | 備考 |")
        out.append("|---|---|---|")
        out += [f"| `{c.id}` | `{_cell(c.command)}` | {_cell(c.note)} |" for c in rows]
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
