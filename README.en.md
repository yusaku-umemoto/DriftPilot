# DriftPilot

[日本語](README.md)

DriftPilot compares network device configs and `show` command output, and extracts the differences that actually matter.
It can optionally ask an AI model to analyze the differences, using your own API key.

> The user interface and messages are currently in Japanese.

## Features

- **Config comparison:** Compares Cisco-style configs (indented configs such as IOS and IOS-XE), FortiGate and Fujitsu Si-R configs by meaning rather than by line order (structured comparison). Parts it cannot parse are compared line by line (raw comparison).
- **show comparison:** Compares `show` command output as tables where possible.
- **Logs with many commands:** If one file contains several configs and `show` outputs, they are split by command and compared one by one.
- **Batch comparison:** Pick a "before" folder and an "after" folder; files are paired by file name or hostname and compared together.
- **AI analysis (optional):** Ask an AI model for the key points and impact of the differences. Works with OpenAI-compatible and Anthropic APIs, and with local CLIs.
- **Output:** Save results as JSON, HTML or CSV.

## How secrets are protected

- Only the differences are sent to the AI. The full config is sent only when you choose to.
- Passwords, keys, SNMP communities and similar values are masked before sending (`<secret:N>`). Lines whose secrets cannot be masked are not sent.
- The full text to be sent is shown for your approval before anything is sent.
- Without AI and update checks, DriftPilot makes no network connections.

The masking code lives in `src/driftpilot/llm_analyzer/`, so anyone can review it.

## Getting started

### Prebuilt apps (no Python needed)

Download the file for your OS from [Releases](../../releases).

| OS | File | Notes |
|---|---|---|
| Windows 10 / 11 (x64) | `DriftPilot-<version>-windows-x64.zip` | Extract and open `DriftPilot.exe`. Windows 10 may need the WebView2 Runtime. |
| macOS (Apple silicon) | `DriftPilot-<version>-macos-arm64.dmg` | Move `DriftPilot.app` to Applications and open it. |

The apps are not code-signed, so your OS will warn you the first time:

- **Windows:** "Windows protected your PC" → "More info" → "Run anyway"
- **macOS:** "cannot verify the developer" → System Settings → Privacy & Security → "Open Anyway"

### Where settings are stored

- **Windows:** the `settings\` folder next to `DriftPilot.exe`. Nothing is written outside the extracted folder (except the API key).
- **macOS:** `~/Library/Application Support/DriftPilot/`
- **API keys:** Windows Credential Manager or the macOS Keychain. You can delete them with "削除…" (Delete) in the settings window.

### Checking for new versions

- "更新を確かめる" (Check for updates) on the "更新" (Update) tab of the settings window checks GitHub for a newer release.
- By default, DriftPilot checks only when you press that button.
- If you turn on "起動したときに新しい版を確かめる（自動アップデートチェック）" (automatic update check) and save, DriftPilot checks at every start and shows a notice above the compare window when a newer version is available.
- The only thing sent is the DriftPilot version (no configs, settings or API keys).
- The Windows zip and the macOS app can download a newer version with "更新する" (Update), verify its signature, and replace themselves (DriftPilot closes once and the new version opens).
- Version 0.1.0 has no update feature. To go from 0.1.0 to 0.2.0, replace it by hand as described under "■ 更新するとき" (Updating) in `はじめにお読みください.txt` (Read me first), which comes with the download.

### From source

Requires Python 3.14.

```bash
git clone https://github.com/umemonmon/DriftPilot.git
cd DriftPilot
python3.14 -m venv .venv
.venv/bin/python -m pip install -e ".[gui,llm]"
.venv/bin/driftpilot gui
```

On Windows, use `.venv\Scripts\python.exe` and `.venv\Scripts\driftpilot.exe`.

Command-line examples:

```bash
driftpilot compare before.cfg after.cfg
driftpilot compare before.cfg after.cfg --format json --save result.json
driftpilot batch --pair-by-name before_dir after_dir --confirm
```

Run `driftpilot --help` to see all commands.

## Status

Version 0.2.0 (preview). The UI and output formats may still change.

## License

[Apache License 2.0](LICENSE). Licenses of third-party components bundled in the prebuilt apps are listed in `THIRD_PARTY_LICENSES.txt` inside each distribution.
