# Using the app (GUI)

[Back to the guide](README.md)

## Start the app

- **Windows (zip):** first unblock the zip (right-click → **Properties** → **Unblock**, see [Download](../../README.md#download)), then unzip the folder and open `DriftPilot.exe`.
- **Mac:** open `DriftPilot.app` from Applications.
- **From source:** `driftpilot gui`.

The app has three windows: the **compare window** (opens first), the **batch compare** window and the **settings** window.

## 1. Compare two files

1. In **Inputs**, set **Before** and **After**:
   - **Browse…** to choose a file (`.txt`, `.log`, `.cfg`, `.conf`, or any file), or
   - **Paste…** to paste a config or a terminal log, or
   - drop one or two files onto the window. With two files, the older one (or the one named like `before` / `pre` / `old`) becomes **Before**.
   - **⇅** swaps Before and After.
2. Click **Compare**. If both files are set, the comparison starts automatically (you can turn this off in Settings).
3. **Compare settings → Change** opens the options:
   - **Compare mode:** both / structured / simple.
   - **Type:** config / show / auto (default: auto).
   - **Device type:** auto, Cisco-style and Arista, FortiGate (`fortinet`), Fujitsu Si-R (`fujitsu_sir`). For `show` output, type the platform name (for example `cisco_ios`) and the **show command** (for example `show ip interface brief`).
   - When a file has several command outputs, **Select sections** lets you choose which part to compare.

### Read the results

- **Structured compare** — differences by meaning: **Operation**, **Category**, **Target**, **Item**, **Before**, **After**. Use the category chips to filter.
- **Simple compare** — line-by-line differences. **Show differences only** folds unchanged lines. **Previous difference** / **Next difference** move between changes. Select a row to see both lines in full, with the changed characters marked.
- **Lines not structured** — lines the structured compare could not read (they are still in the simple compare).
- When you compare files with many commands, **Command pairs** on the left lists each command and its status.
- The status line at the bottom shows the result and the counts.

The simple-compare options (ignore lines by regular expression, replace text before comparing, ignore spaces / blank lines / case, detect moved lines, diff algorithm) are under the simple compare settings. You can set their defaults in **Settings… → Simple comparison**.

### Save and export

- **Save result…** saves the result as JSON. It contains the original config values, so treat it like the config itself.
- **Open saved result…** shows a saved result (read only).
- **Export HTML/CSV…** writes an HTML report or CSV. Secret lines are always masked in these files. Files are never overwritten.

## 2. AI analysis

You need an AI profile first (see [Settings](#4-settings)).

1. After comparing, choose an **AI profile** and a **Prompt** on the right side.
2. Click **Analyze with AI**.
3. A **Confirm what will be sent to AI** dialog always appears. It shows the exact text after masking. Click **Send** or **Don't send**.
4. The answer appears on the right. Type in **Question or instruction for the AI** to ask a follow-up question (Enter to send, Shift+Enter for a new line).

What DriftPilot does before sending:

- Values on lines with secret words (passwords, keys, community strings and so on) are replaced with `<secret:N>`. The same value gets the same number.
- Lines it cannot mask safely (unknown secret formats, PEM blocks, lines with invisible characters) are not sent at all.
- You can add your own words to mask in **Settings… → Settings file → Extra masked words**.

Built-in prompts: **Key points and impact** (default), **Incident investigation**, **Check status**. You can write your own in **Settings… → Prompt**.

Other options:

- **Send differences + full text** also sends the whole files (masked). More secret formats may be involved, so it is off by default.
- **Send to the AI automatically after comparing** (Settings → Compare and automation) skips the confirmation. It is off by default.
- **↗** opens the AI area in a separate window.

## 3. Batch compare

Open it with **Batch compare…**.

1. Choose a **Before folder** and an **After folder** (**Browse…**), or **Open a pair file** (YAML). Files with exactly the same name are paired.
2. Files without a partner are listed separately. You can pair them by selecting two and clicking **Pair the 2 selected**, by dragging, with **Suggest by hostname**, or with **Ask AI for suggestions…** (only file names are sent, after confirmation).
3. Click **Run**. **Cancel** stops after the current pair.
4. The **Results** list shows each pair's status and counts. Double-click a pair (or **Open in compare window**) to look at it in detail.
5. **Save these pairs…** saves the pairs as YAML, **Save results…** saves JSON Lines, **Export HTML/CSV…** writes a report.

**Analyze with AI** in the batch window sends all pairs after one confirmation list. To ask follow-up questions, open the pair in the compare window.

## 4. Settings

Open with **Settings…**. Tabs:

- **AI profiles** — where to send AI requests. Choose a kind (OpenAI, Azure OpenAI, Claude, Qwen, DeepSeek, local AI, Codex CLI, Claude Code CLI, custom API and others), then the model and endpoint. Save the API key with **Save to key store** (Mac Keychain or Windows Credential Manager). **AI connection test** sends one short text to check the connection. **Use this profile** makes it active.
- **Prompt** — your own prompts and the premise added to every request.
- **Compare and automation** — automatic compare, automatic sending to the AI, the character (Mirarin), and limits on how many differences are sent.
- **Simple comparison** — defaults for the simple compare.
- **Settings file** — extra masked words, create the settings file, diagnostics.
- **Display** — **Screen colors** (match system settings / light / dark) and **Language** (English / 日本語 / match system setting; restart to apply).
- **Update** — check for a new version and update (see below).

**Export profiles…** / **Import profiles…** move AI profiles between computers. API keys are not included.

## 5. Update DriftPilot

**Settings… → Update → Check for updates.** If a new version exists, click **Update**: DriftPilot downloads it, checks its signature, closes, replaces itself and opens the new version. Your settings and API keys stay.

You can also turn on **Check for a new version at startup** to get a notice when a new version is out.

If you installed from source, update with `git pull` and `pip install -e ".[gui,llm]"`.
