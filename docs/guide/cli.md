# Using the command line (CLI)

[Back to the guide](README.md)

## Where the command is

| How you installed | Command |
|---|---|
| Windows zip | `driftpilot-cli.exe` in the unzipped folder (run it from PowerShell or Command Prompt) |
| Mac app | `/Applications/DriftPilot.app/Contents/MacOS/driftpilot-cli` |
| From source | `driftpilot` (inside your virtual environment) |

The examples below use `driftpilot`. Replace it with the command for your installation.

`driftpilot --help` lists all commands. `driftpilot <command> --help` shows the options of one command.

## Compare two files

```bash
driftpilot compare before.cfg after.cfg
```

Useful options:

| Option | Meaning |
|---|---|
| `--mode structured\|raw\|both` | Compare mode (default: both) |
| `--format text\|json` | Output format (default: text) |
| `--kind config\|show\|auto` | Input type (default: config) |
| `--platform NAME` | Device type: `fortinet`, `fujitsu_sir`, `auto`; for show output an ntc-templates platform such as `cisco_ios` |
| `--show-command TEXT` | The show command, for example `'show ip interface brief'` |
| `--save PATH` | Save the result as JSON (never overwrites) |
| `--ignore-line REGEX`, `--replace REGEX TEXT`, `--ignore-space …`, `--ignore-blank-lines`, `--ignore-case` | Simple-compare options |
| `--all-segments` | Compare every command output in the files, paired by command |

Example with JSON output saved to a file:

```bash
driftpilot compare before.cfg after.cfg --format json --save result.json
```

## AI analysis from the command line

```bash
driftpilot compare before.cfg after.cfg --llm-dry-run
driftpilot compare before.cfg after.cfg --llm
```

- `--llm-dry-run` shows what would be sent (after masking) and sends nothing. Use it first.
- `--llm` sends the differences to the AI profile in use.
- `--llm-profile NAME` / `--llm-prompt-profile NAME` choose a profile for this run only.
- `driftpilot llm-check` sends one short fixed text to check that the key and model work (a very small charge may apply).

## Batch compare

```bash
driftpilot batch --pair-by-name before_dir after_dir
driftpilot batch --pair-by-name before_dir after_dir --confirm
driftpilot batch pairs.yaml --output results.jsonl
```

- `--pair-by-name` pairs files with the same name and lists the pairs. Add `--confirm` to run the comparisons.
- `--write-manifest PATH` writes the pairs as a pair file (YAML) that you can edit and run later.
- `--save DIR` saves each result as a file. `--summary PATH` writes a JSON summary.
- With `--llm`, a summary of what would be sent is shown; nothing is sent without `--confirm`.

## Other commands

| Command | What it does |
|---|---|
| `selfcheck` | Checks that the bundled parts and data are present (no network, writes nothing) |
| `mask-patterns` | Shows the table of what is masked before sending to the AI |
| `patterns` | Shows what the config parser supports |
| `gui` | Opens the app (source installs) |

## Language

Commands follow the same language setting as the app (**Settings → Display → Language**). For one run, set `DRIFTPILOT_LANG=ja` or `DRIFTPILOT_LANG=en`:

```bash
DRIFTPILOT_LANG=ja driftpilot compare before.cfg after.cfg
```

On Windows PowerShell: `$env:DRIFTPILOT_LANG = "ja"` before running the command.
