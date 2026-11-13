# Using the command line (CLI)

[Back to the guide](README.md)

The command line does everything the app does except the windows: compare two files, compare whole folders, split terminal logs by command, and ask an AI to explain the differences. It is designed to be driven by scripts and AI agents as well as by people: results go to standard output, notes go to standard error, the exit code tells you the result, and JSON output is available for every comparison.

- [Where the command is](#where-the-command-is)
- [Quick reference (for scripts and AI agents)](#quick-reference-for-scripts-and-ai-agents)
- [Compare two config files](#compare-two-config-files)
- [Choose the device type](#choose-the-device-type)
- [Read the text output](#read-the-text-output)
- [JSON output and saved results](#json-output-and-saved-results)
- [Simple-compare options](#simple-compare-options)
- [Terminal logs with several commands](#terminal-logs-with-several-commands)
- [Compare show output as a table](#compare-show-output-as-a-table)
- [Batch compare (folders and manifests)](#batch-compare-folders-and-manifests)
- [AI analysis](#ai-analysis)
- [Other commands](#other-commands)
- [Language](#language)
- [Recipes](#recipes)

## Where the command is

| How you installed | Command |
|---|---|
| Windows zip | `driftpilot-cli.exe` in the extracted `DriftPilot` folder (run it from PowerShell or Command Prompt) |
| Mac app | `/Applications/DriftPilot.app/Contents/MacOS/driftpilot-cli` |
| From source | `driftpilot` (inside your virtual environment) |

The examples below use `driftpilot`. Replace it with the command for your installation, for example:

```bash
# Mac app
alias driftpilot=/Applications/DriftPilot.app/Contents/MacOS/driftpilot-cli
```

```powershell
# Windows (PowerShell), in the extracted folder
.\driftpilot-cli.exe compare before.cfg after.cfg
```

`driftpilot --help` lists all commands. `driftpilot <command> --help` shows every option of one command. `driftpilot --version` prints the version (`driftpilot 0.2.3`).

All the examples on this page use the sample files in [`examples/`](../../examples/README.md) and were run with version 0.2.3. The outputs are copied from real runs (long lines are shortened with `…`).

## Quick reference (for scripts and AI agents)

| Topic | Rule |
|---|---|
| Exit code of `compare` and `batch` | `0` no differences · `1` differences found · `2` error (the message is on standard error) · `3` needs a selection (the file has several command outputs; choose one, see [Terminal logs](#terminal-logs-with-several-commands)). `batch` returns the most serious status of all its jobs (2 > 3 > 1 > 0). |
| Exit code of `llm-check` | `0` connected · `1` sent but failed · `2` not sent (no model, no key, or not confirmed) |
| Exit code of `selfcheck` | `0` all OK · `1` something is missing |
| Standard output | The result only (text or JSON). Safe to pipe or redirect. |
| Standard error | Notes, warnings, summaries and errors (for example "saved to …", the batch summary). |
| Network | None, unless you pass `--llm` (AI analysis) or run `llm-check`. `--llm-dry-run` never sends. |
| Files | Nothing is written unless you pass `--save`, `--output`, `--summary`, `--write-manifest` or `--output` of the table commands. Existing files are **never overwritten**; the command stops with exit code 2 instead. A `--save DIR` folder must already exist. |
| Input | Files only; standard input (`-`) is not supported. UTF-8 (with or without BOM), UTF-16 with BOM and Shift_JIS (cp932) are read. Text copied in odd forms is restored before comparing: RTF, HTML `<pre>`, line numbers or the same prefix on every line, a common indent, and FortiGate lines wrapped by the terminal. A warning tells you when this happened. |
| Default comparison | `--mode both` (structured + simple), `--kind config`, Cisco-family parser. Use `--platform auto` to detect FortiGate / Si-R. |
| Secrets in results | Text and JSON results and saved files contain the original values, including passwords. Treat them like the configs. Only what is sent to the AI is masked. |

Minimal commands:

```bash
driftpilot compare before.cfg after.cfg --platform auto              # text result
driftpilot compare before.cfg after.cfg --platform auto --format json  # JSON result
driftpilot compare before.cfg after.cfg --platform auto --mode structured  # only the meaning-level changes
echo $?                                                              # 0 / 1 / 2 / 3
```

## Compare two config files

```bash
driftpilot compare cisco/before.cfg cisco/after.cfg
```

Output (exit code 1, differences found):

```text
== structured ==
[added] acls LAN-IN / entries > 25: None -> {'seq': 25, 'text': 'permit tcp 192.0.2.0 0.0.0.255 any eq 22'}
[added] bgp / neighbors > 198.51.100.1: None -> {'kind': 'address', 'remote_as': 64501, 'peer_group': None, 'description': 'ISP-B', …, 'password_configured': True, 'options': []}
[added] global / snmp > communities: None -> {'name': 'Example-RO-2026', 'access': 'RO', 'acl': None, 'view': None}
[removed] global / snmp > communities: {'name': 'Example-RO-2025', 'access': 'RO', 'acl': None, 'view': None} -> None
[added] interfaces GigabitEthernet0/0/2: None -> {'type': 'routed', 'parent_interface': None, 'description': 'Uplink to ISP-B', 'shutdown': False, 'vrf': None, 'ip_address': IPv4Interface('198.51.100.2/30'), …}
Lines that could not be structured: Before 0 / After 0
== raw ==
+ 18: interface GigabitEthernet0/0/2
+ 19:  description Uplink to ISP-B
+ 20:  ip address 198.51.100.2 255.255.255.252
+ 21:  no shutdown
+ 22: !
- 24:  neighbor 203.0.113.1 password Old-BGP-Key-2025
+ 29:  neighbor 203.0.113.1 password New-BGP-Key-2026
+ 31:  neighbor 198.51.100.1 remote-as 64501
+ 32:  neighbor 198.51.100.1 password ISP-B-Shared-Key
+ 33:  neighbor 198.51.100.1 description ISP-B
+ 38:  25 permit tcp 192.0.2.0 0.0.0.255 any eq 22
- 32: snmp-server community Example-RO-2025 RO
+ 41: snmp-server community Example-RO-2026 RO
Enabled rules: none
== segmentation ==
Before: cisco/before.cfg  Prompt: none (not split) (none)
  * 1. (unknown command)  39 lines
After: cisco/after.cfg  Prompt: none (not split) (none)
  * 1. (unknown command)  48 lines
Check for defaults: Before Unknown / After Unknown
Note: Unknown retrieval command: cannot tell whether Before and After are a normal version or a version with defaults
```

Comparing a file with itself prints `No differences` under `== structured ==` and returns exit code 0.

Options of `compare`:

| Option | Meaning | Default |
|---|---|---|
| `before`, `after` | The two files (positional) | — |
| `--mode structured\|raw\|both` | `structured`: compare by meaning. `raw`: compare line by line (the "simple compare" of the app). `both`: both. | `both` |
| `--format text\|json` | Output format | `text` |
| `--kind config\|show\|auto` | Kind of input. `auto` decides from the content. | `config` |
| `--platform NAME` | Device type, see [below](#choose-the-device-type) | Cisco family |
| `--show-command TEXT` | The show command, for a table comparison of show output (with `--platform`) | — |
| `--before-command TEXT` / `--after-command TEXT` | Choose a command output in a log by part of the command text | — |
| `--before-segment N` / `--after-segment N` | Choose a command output in a log by its position (from 1) | — |
| `--all-segments` | Pair every command output of both logs by command and compare all of them | off |
| `--save PATH` | Save the result as JSON (a folder gets an automatic name). Never overwrites. | not saved |
| `--rules PATH`, `--enable-rule ID` | Normalization rules (advanced; see `--help`) | bundled rules |
| `--prompt TEXT` | The device prompt, when automatic detection fails (for example `'edge-rtr-01#'`) | automatic |
| Simple-compare options | [see below](#simple-compare-options) | off |
| AI options | [see below](#ai-analysis) | off |

## Choose the device type

| `--platform` | Parser | Use it for |
|---|---|---|
| (not given) | Cisco family (indented configs) | Cisco IOS / IOS-XE / NX-OS-style, Arista EOS and other indented configs |
| `auto` | Decided from the content | Mixed devices, or when you do not know. Prints `Device type: Auto (FortiGate)` and so on. |
| `fortinet` | FortiGate | `show full-configuration` / `show` output of FortiOS |
| `fujitsu_sir` | Flat config | Fujitsu Si-R |
| an ntc-templates name such as `cisco_ios` | show tables | Only for show output, together with `--kind show --show-command …` |

```bash
driftpilot compare fortigate/before.conf fortigate/after.conf --platform auto --mode structured
```

```text
Device type: Auto (FortiGate)
== structured ==
[added] vendor_specific / fortinet > firewall > policies > 1 > service: None -> 'HTTP'
[changed] vendor_specific / fortinet > interfaces > port1 > allowaccess: ['ping', 'https'] -> ['ping', 'https', 'ssh']
Lines that could not be structured: Before 0 / After 0
…
```

```bash
driftpilot compare fujitsu-sir/before.txt fujitsu-sir/after.txt --platform auto --mode structured
```

```text
Device type: Auto (Si-R)
== structured ==
[added] interfaces lan 2: None -> {'type': 'svi', …, 'ip_address': IPv4Interface('198.51.100.6/30'), …}
[added] vendor_specific / fujitsu_sir > interfaces > lan 2: None -> {'ip_broadcast_type': 3, …}
…
```

Without `--platform auto` (or `fortinet` / `fujitsu_sir`), FortiGate and Si-R files are read with the Cisco-family parser and the structured result is not meaningful. When in doubt, pass `--platform auto`.

## Read the text output

The text output has up to four blocks:

| Block | Content |
|---|---|
| `== structured ==` | One line per change: `[operation] category target / item: before -> after`. Operations are `added`, `removed` and `changed`. `None` means "not set". Lines like `Lines that could not be structured: Before 0 / After 0` show how many lines the parser could not read (they are still in the simple compare). |
| `== raw ==` | The line-by-line differences: `+ N: text` (added, line N of After), `- N: text` (removed, line N of Before). With `--detect-moves`, moved blocks are shown as moved. |
| `== segmentation ==` | How each file was split. `*` marks the part that was compared. `Prompt:` is the device prompt that DriftPilot found (`none (not split)` for a plain config). |
| Notes | `Check for defaults` tells whether each file looks like a normal config or one with default values (`show running-config all` / `full-configuration`), which helps avoid comparing two different kinds. |

## JSON output and saved results

```bash
driftpilot compare cisco/before.cfg cisco/after.cfg --format json > result.json
```

Main fields (`schema_version` `"1"`):

| Field | Meaning |
|---|---|
| `status` | `no_difference`, `difference`, `needs_selection` or `error` (the same as the exit code) |
| `has_diff` | `true` / `false` |
| `mode`, `kind` | What was compared |
| `structured.diffs[]` | `{op, category, name, path, before, after}` — for example `{"op": "added", "category": "bgp", "name": null, "path": ["neighbors", "198.51.100.1"], "before": null, "after": {…}}` |
| `structured.unparsed_counts` | `{before, after}` — lines that could not be structured |
| `raw.diffs[]` | `{op, before_line_no, after_line_no, text}` — for example `{"op": "removed", "before_line_no": 24, "after_line_no": null, "text": " neighbor 203.0.113.1 password Old-BGP-Key-2025"}` |
| `show` | The table comparison of show output (when used) |
| `segmentation.before` / `.after` | `file`, `prompt`, `segments[]`, `selected` |
| `notices[]` | Notes such as `unknown_both` (cannot tell normal / with-defaults) |
| `llm` | The AI result. `status` is `skipped` when AI analysis was not used. |

Example with `jq` — list only the structured changes:

```bash
driftpilot compare before.cfg after.cfg --platform auto --format json \
  | jq -r '.structured.diffs[] | "\(.op)\t\(.category)\t\(.path | map(tostring) | join(" > "))"'
```

`--save PATH` writes a fuller record (inputs, settings, tool versions and the result) that the app can open with **Open saved result…**:

```bash
mkdir -p results
driftpilot compare before.cfg after.cfg --save results/
# Note (standard error): the comparison result was saved to results/20261113-190143_compare.json. …
```

## Simple-compare options

These change only the line-by-line (raw) comparison. The structured comparison is not affected.

| Option | Effect |
|---|---|
| `--ignore-line REGEX` | Do not compare lines that match (repeatable) |
| `--replace REGEX TEXT` | Replace the matching part before comparing (repeatable, applied in order) |
| `--ignore-space off\|trailing\|amount\|all` | Ignore spaces at the end of lines / the amount of spaces / all spaces |
| `--ignore-blank-lines` | Do not compare blank lines |
| `--ignore-case` | Ignore upper / lower case |
| `--detect-moves` | Show blocks of 3 or more lines that moved as moved (a move still counts as a difference) |
| `--diff-algorithm standard\|minimal\|patience` | How the line diff is computed |

```bash
# Ignore BGP password lines and timestamps, and trailing spaces
driftpilot compare before.cfg after.cfg --mode raw \
  --ignore-line '^\s*neighbor .* password ' \
  --replace '^! Last configuration change at .*' '! (timestamp)' \
  --ignore-space trailing
```

```text
== raw ==
+ 18: interface GigabitEthernet0/0/2
…
Simple comparison settings: Ignore lines: 1
Ignored lines: Before 1, After 2. Lines treated as equal: 0 pairs
```

The defaults that you set in the app (**Settings… → Simple comparison**) are **not** used by the command line; pass the options each time.

## Terminal logs with several commands

A terminal log (a saved SSH session) often contains several commands. DriftPilot finds the device prompt (for example `edge-rtr-01#`) and splits the log into one part per command.

```text
edge-rtr-01#show ip interface brief
Interface              IP-Address      OK? Method Status                Protocol
GigabitEthernet0/0/2   unassigned      YES unset  administratively down down
…
edge-rtr-01#show running-config | section snmp
snmp-server community Example-RO-2025 RO
…
```

If you compare two such logs without choosing, the command stops with exit code **3** and lists the parts:

```bash
driftpilot compare before.log after.log
```

```text
Error: There are multiple segments. Choose one with --before-segment / --after-segment or similar
== segmentation ==
Before has 2 segments. Please select one:
Before: before.log  Prompt: 'edge-rtr-01#' (auto)
    1. show ip interface brief  4 lines
    2. show running-config | section snmp  2 lines
…
```

Choose one part on each side, by number or by part of the command:

```bash
driftpilot compare before.log after.log --before-segment 2 --after-segment 2
driftpilot compare before.log after.log --before-command "section snmp" --after-command "section snmp"
```

Or compare every command at once. Parts are paired by command; configuration commands (such as `show running-config`) are compared as configs and the others as show output:

```bash
driftpilot compare before.log after.log --all-segments
```

```text
=== 1. show ip interface brief (Before segment 1 / After segment 1) ===
== show structured ==
Fell back to the raw comparison: The platform and the command are not specified
== raw ==
- 4: GigabitEthernet0/0/2   unassigned      YES unset  administratively down down
+ 4: GigabitEthernet0/0/2   198.51.100.2    YES manual up                    up
…
=== 2. show running-config | section snmp (Before segment 2 / After segment 2) ===
== structured ==
[added] global / snmp > communities: None -> {'name': 'Example-RO-2026', …}
[removed] global / snmp > communities: {'name': 'Example-RO-2025', …} -> None
…
```

- With `--all-segments --save FILE`, one line of JSON Lines is written per pair (parts without a partner are not included).
- Parts that are error output from a mistyped command (`% Invalid input detected`, Si-R `<ERROR> … : Unknown command` and so on) are not paired or compared; they are listed under `=== Error output (not compared) ===`.
- `--all-segments` cannot be combined with `--before-segment`, `--after-segment`, `--before-command`, `--after-command` or `--show-command`.
- If the prompt is not found, pass it with `--prompt 'edge-rtr-01#'`.
- A log that contains several devices can be split into one file per device in the app (**Split by device and open in Batch compare…**); the command line compares one device per log.

## Compare show output as a table

For `show` output, DriftPilot can read the output into a table with [ntc-templates](https://github.com/networktocode/ntc-templates) and compare row by row. Give the platform and the exact show command:

```bash
driftpilot compare before.log after.log \
  --before-command "ip interface brief" --after-command "ip interface brief" \
  --kind show --platform cisco_ios --show-command "show ip interface brief"
```

```text
== show structured ==
[changed] GigabitEthernet0/0/2: ip_address: 'unassigned' -> '198.51.100.2', status: 'administratively down' -> 'up', proto: 'down' -> 'up'
== raw ==
- 4: GigabitEthernet0/0/2   unassigned      YES unset  administratively down down
+ 4: GigabitEthernet0/0/2   198.51.100.2    YES manual up                    up
…
```

`--platform` and `--show-command` must be given together for show output (`Error: Specify both --platform and --show-command`). Without them, show output is compared line by line. `driftpilot show-commands` lists the show commands that have templates.

## Batch compare (folders and manifests)

### Pair two folders by file name

```bash
driftpilot batch --pair-by-name before after
```

```text
Before: /path/to/before
After: /path/to/after

Paired files: 3 (file names match exactly, including the extension)
  branch-rt01.txt
  core-sw01.cfg
  edge-fw01.conf
Unpaired files: 0
  Only in Before: 0
  Only in After: 0
The comparison has not been run. Add --confirm to run it for these pairs, or --write-manifest PATH to save them as a manifest file
```

This only lists the pairs (exit code 0). Then either run them:

```bash
driftpilot batch --pair-by-name before after --confirm > results.jsonl
# standard error: Batch compare: ran 3 of 3 (No differences 0 / Differences found 3 / Needs selection 0 / Failed 0), 0.0 s. Exit code 1
```

or save the pairs as a manifest file that you can edit and run later:

```bash
driftpilot batch --pair-by-name before after --write-manifest pairs.yaml
```

> **Device type in batch:** `--pair-by-name --confirm` uses the default (Cisco-family) parser for every pair. If the folders contain FortiGate or Si-R files, write a manifest and add `platform: auto` under `defaults` (below). The app's batch window detects the device type automatically.

### Manifest file (YAML)

The file that `--write-manifest` writes:

```yaml
schema_version: '1'
defaults:
  mode: both
  kind: config
jobs:
- id: branch-rt01.txt
  before:
    path: before/branch-rt01.txt
  after:
    path: after/branch-rt01.txt
- id: core-sw01.cfg
  before:
    path: before/core-sw01.cfg
  after:
    path: after/core-sw01.cfg
```

A fuller example (every key below was tested):

```yaml
schema_version: '1'
defaults:                 # applies to every job unless the job sets its own value
  mode: both              # structured | raw | both
  kind: config            # config | show | auto
  platform: auto          # auto | fortinet | fujitsu_sir | (ntc-templates name for show)
  raw_options:            # simple-compare options (a job's raw_options replaces these as a whole)
    ignore_lines:
      - '^\s*!'
    whitespace: trailing  # off | trailing | amount | all
    # replacements: [{pattern: 'REGEX', replacement: 'TEXT'}]
    # ignore_blank_lines: true
    # ignore_case: true
    # detect_moves: true
    # algorithm: standard  # standard | minimal | patience
jobs:
  - id: core-sw01                     # unique name of the job
    label: Core switch (Cisco)        # free text shown in results
    before: {path: before/core-sw01.cfg}
    after: {path: after/core-sw01.cfg}
  - id: edge-fw01
    label: Edge firewall (FortiGate)
    platform: fortinet                # job-level setting
    before: {path: before/edge-fw01.conf}
    after: {path: after/edge-fw01.conf}
  - id: edge-rtr01-ifbrief
    label: Interface status from a terminal log
    kind: show
    platform: cisco_ios
    show_command: show ip interface brief
    before: {path: logs/before.log, command: ip interface brief}   # choose a part by command text
    after: {path: logs/after.log, command: ip interface brief}
  - id: edge-rtr01-snmp
    before: {path: logs/before.log, segment: 2}                    # or by position
    after: {path: logs/after.log, segment: 2}
```

- Allowed keys: top level `schema_version`, `defaults`, `jobs`; in `defaults` and in each job `mode`, `kind`, `platform`, `show_command`, `prompt`, `rules`, `enable_rule`, `before`, `after`, `raw_options`; in each job also `id` and `label`; in `before` / `after` `path`, `command`, `segment`. Unknown keys are errors.
- Relative paths are relative to the manifest file.
- For show tables, `platform` and `show_command` go together.

Run it:

```bash
driftpilot batch pairs.yaml --dry-run                         # list the plan only (exit code 0)
driftpilot batch pairs.yaml > results.jsonl                   # JSON Lines to standard output
driftpilot batch pairs.yaml --output results.jsonl --summary summary.json --save saved/
```

Options of `batch`:

| Option | Meaning |
|---|---|
| `MANIFEST` | The manifest file (not needed with `--pair-by-name`) |
| `--pair-by-name BEFORE_DIR AFTER_DIR` | Pair files with exactly the same name (only directly under the folders) and list them |
| `--confirm` | Run the pairs of `--pair-by-name`; with `--llm`, also send |
| `--write-manifest PATH` | Write the pairs of `--pair-by-name` as a manifest and stop |
| `--dry-run` | List the planned comparisons without running them |
| `--output PATH` | Write the JSON Lines to a file instead of standard output |
| `--summary PATH` | Write a JSON summary (below) |
| `--save DIR` | Save each job's full result as its own file (the folder must exist) |
| `--quiet` | Do not print the summary to standard error |
| AI options | `--llm`, `--llm-dry-run`, `--llm-max-diffs N`, `--llm-max-total-diffs N`, `--llm-profile`, `--llm-prompt-profile`, `--llm-no-premise` ([AI analysis](#ai-analysis)) |

Each line of the JSON Lines has `job` (`id`, `label`, `index`), `settings` (`mode`, `kind`, `platform`, …), `inputs`, `result` (the same shape as `compare --format json`) and `tools`. Example: list each job and its status:

```bash
driftpilot batch pairs.yaml --quiet \
  | jq -r '"\(.job.id)\t\(.result.status)\t\(.result.structured.diffs | length)"'
```

The summary file:

```json
{
  "schema_version": "1",
  "total": 3,
  "executed": 3,
  "cancelled": false,
  "interrupted": false,
  "counts": {"no_difference": 0, "difference": 3, "needs_selection": 0, "error": 0},
  "elapsed_seconds": 0.013,
  "exit_code": 1,
  "manifest_error": null,
  "saved": {"dir": "/path/to/saved", "count": 3, "failures": []},
  "unpaired": null
}
```

## AI analysis

### Set up the AI connection

The easiest way is the app: **Settings… → AI profiles** (choose a kind, a model, save the API key to the key store, and click **Use this profile**). The command line uses the same profiles.

Without the app, write the model in the settings file and pass the key in an environment variable:

- Settings file: `config.toml` in the settings folder (Windows zip: `settings\` next to the exe; Mac: `~/Library/Application Support/DriftPilot/`). `llm-check` prints the exact path when the model is missing.

```toml
[llm]
model = "gpt-4o-mini"
base_url = "https://api.openai.com/v1"      # any OpenAI-compatible endpoint
# extra_secret_words = ["site-token"]       # more words whose lines are masked
# max_diffs = 100                           # per pair
# max_total_diffs = 1000                    # across all pairs
```

- API key: `DRIFTPILOT_LLM_API_KEY` (or the key store through the app). Writing `api_key` in the file also works but is less safe.

Check the connection (one short fixed text is sent; a very small charge may apply):

```bash
driftpilot llm-check            # asks before sending
driftpilot llm-check --yes      # no question (needed in scripts)
```

If nothing is set up, it ends without sending (exit code 2): `No model is set. Nothing was sent. Write the model name in llm.model of the settings file (…/config.toml).`

### Preview, then send

Always preview first. `--llm-dry-run` shows exactly what would be sent, after masking, and sends nothing (no model or key is needed):

```bash
driftpilot compare cisco/before.cfg cisco/after.cfg --llm-dry-run
```

```text
…
== AI analysis ==
AI analysis: not run (Preview of the content to send only (--llm-dry-run). Nothing was sent)
--- Content to send (after masking; only the fixed answer-format instructions are added when sending) ---
You help read differences between network device configurations. …
=== Differences ===
[added] acls LAN-IN / entries > 25: null -> {"seq": 25, "text": "permit tcp 192.0.2.0 0.0.0.255 any eq 22"}
[added] global / snmp > communities: null -> {"name": "<secret:1>", "access": "RO", "acl": null, "view": null}
[removed] global / snmp > communities: {"name": "<secret:2>", "access": "RO", "acl": null, "view": null} -> null
…
=== End ===
```

The SNMP community names became `<secret:1>` and `<secret:2>`. Then send:

```bash
driftpilot compare before.cfg after.cfg --platform auto --llm
```

| Option | Meaning |
|---|---|
| `--llm` | Send the differences to the AI and add its explanation to the result |
| `--llm-dry-run` | Show what would be sent; send nothing |
| `--llm-max-diffs N` | Upper limit of differences sent (default 100 or `max_diffs`) |
| `--llm-profile NAME` | Use another AI profile for this run only |
| `--llm-prompt-profile NAME` | Use another prompt for this run only (built-in: Key points and impact, Incident investigation, Check status, and your own) |
| `--llm-no-premise` | Do not attach the premise text from the **Prompt** tab |
| `--all-segments … --llm --confirm` | For logs: show a summary of what would be sent, and send only with `--confirm` |

In `batch`, `--llm` without `--confirm` only shows a summary; with `--confirm` it sends each pair. `--llm-max-total-diffs N` stops sending when the total over all pairs is larger.

What is masked: values on lines with secret words (passwords, keys, SNMP communities and so on) become `<secret:N>`; the same value gets the same number. Lines that cannot be masked safely are not sent. `driftpilot mask-patterns` prints the full table.

## Other commands

| Command | What it does |
|---|---|
| `selfcheck` | Checks that the bundled parts and data are present. No network, writes nothing. `--format json` is available. |
| `mask-patterns` | Prints the table of what is masked before sending to the AI (`--format markdown\|json`, `--output PATH`) |
| `patterns` | Prints what the config parser supports (`--platform fortinet\|fujitsu_sir` for those parsers) |
| `show-commands` | Prints the table of show commands that DriftPilot knows how to compare |
| `coverage`, `coverage-show`, `mask-coverage` | Measure how much of your own configs the parser / templates / masking table cover (for checking real configs locally; `--help` for details) |
| `gui` | Opens the app (source installs) |

`selfcheck` output:

```text
Version: OK (driftpilot 0.2.3, Python 3.14.8)
Bundled tables: OK (platform_rules.yaml: 2 entries, defaults.yaml)
Pattern tables: OK (Cisco family …: 352 entries; fortinet …: 246 entries; fujitsu_sir …: 76 entries)
show templates: OK (979, …)
Component version info: OK (driftpilot 0.2.3, ntc_templates 9.3.0, hier_config 3.7.1, textfsm 2.1.0)
Signature check: OK (Ed25519, main 5c50466f5b7455a6, backup 80db1dccd1059a79)
Window components (web): OK (pywebview 6.2.1)
AI components: OK (openai 3.32.0)
Key store: OK (Mac Keychain)
All OK
```

## Language

Commands follow the same language setting as the app (**Settings… → Display → Language**). For one run, set `DRIFTPILOT_LANG=ja` or `DRIFTPILOT_LANG=en`. This also changes the language of the AI's answer.

```bash
DRIFTPILOT_LANG=ja driftpilot compare before.cfg after.cfg
```

On Windows PowerShell:

```powershell
$env:DRIFTPILOT_LANG = "ja"
.\driftpilot-cli.exe compare before.cfg after.cfg
```

## Recipes

**Fail a CI job when the config drifted from the intended one**

```bash
driftpilot compare intended.cfg running.cfg --platform auto --mode structured
case $? in
  0) echo "no drift" ;;
  1) echo "drift found"; exit 1 ;;
  *) echo "could not compare"; exit 2 ;;
esac
```

PowerShell: check `$LASTEXITCODE` after the command.

**Compare last night's backups with today's**

```bash
driftpilot batch --pair-by-name backups/2026-11-12 backups/2026-11-13 --write-manifest pairs.yaml
# add "platform: auto" under defaults in pairs.yaml, then:
driftpilot batch pairs.yaml --output results.jsonl --summary summary.json
jq '.counts' summary.json
```

**Only the changed lines, ignoring comments**

```bash
driftpilot compare before.cfg after.cfg --mode raw --ignore-line '^\s*!' --ignore-blank-lines
```

**One command from two terminal logs, as JSON**

```bash
driftpilot compare before.log after.log \
  --before-command "show running-config" --after-command "show running-config" \
  --format json | jq '.structured.diffs'
```

**Ask the AI, after checking what will be sent**

```bash
driftpilot compare before.cfg after.cfg --platform auto --llm-dry-run   # read it
driftpilot compare before.cfg after.cfg --platform auto --llm --format json | jq -r '.llm.answer'
```
