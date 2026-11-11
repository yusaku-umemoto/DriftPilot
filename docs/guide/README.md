# DriftPilot user guide

DriftPilot compares two network device configurations (or two `show` outputs) and shows what changed. It can also ask an AI to explain the differences, after hiding secrets.

- [Using the app (GUI)](gui.md) — compare two files, read the results, AI analysis, batch compare, settings
- [Using the command line (CLI)](cli.md) — `driftpilot` / `driftpilot-cli` commands
- [Troubleshooting](troubleshooting.md) — first-launch warnings, updates, AI connection

日本語の手引きは [ja/](ja/README.md) にあります。

## Language

The screen, the command line and the AI answers are in English by default.
To use Japanese, open **Settings… → Display**, choose **日本語** under **Language**, click **Save**, and restart DriftPilot.
For a single command, you can also set the environment variable `DRIFTPILOT_LANG=ja` (or `en`).

## What is sent outside your computer

- Nothing, unless you use AI analysis or the update check.
- **AI analysis** sends only the differences (or, if you choose, the full text) to the AI service you configured, after masking secrets. You always see what will be sent and confirm it first (unless you turn on automatic sending yourself).
- **Update check** sends only DriftPilot's version number to GitHub.
