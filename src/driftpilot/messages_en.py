"""Warning and notice texts. core returns only kind and detail; the wording is built at the entry points (ADR-0015 decision 4).

Shared by both entry points (CLI and GUI). The wording lives only here so the same warning is never worded differently in the CLI and the GUI (ADR-0022 decision 2).

The comparison parts (core, diff_engine, llm_analyzer, which load ntc_templates and hier_config) are not imported at load time.
The self-check (`driftpilot selfcheck`, REQ-38) must be able to report results with these texts even when those parts are missing.
Types are used only in annotations (annotations are not evaluated until used). Constants whose values are needed are imported inside the function that uses them.
"""

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Default limits and mark names (a small module that imports nothing else and none of the comparison parts. ADR-0167)
from driftpilot.llm_constants import DEFAULT_MAX_DIFFS, DEFAULT_MAX_TOTAL_DIFFS, PROMPT_MARKS

if TYPE_CHECKING:
    from driftpilot.batch_io import Pairing
    from driftpilot.core import BatchLlmPreview, BatchProgress, BatchSummary, ComparisonExclusion, SideSelection
    from driftpilot.diff_engine import SafetyWarning
    from driftpilot.diff_engine.exposure_check import ExposureNotice
    from driftpilot.llm_analyzer import ConnectionCheckResult, LlmAnalysis, UnsentShape
    from driftpilot.segmentation.input_forms import InputHint


def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _llm_give_up_after() -> int:
    from driftpilot.core.batch import LLM_GIVE_UP_AFTER

    return LLM_GIVE_UP_AFTER

_SIDE_LABEL = {"before": "Before", "after": "After"}
_STATE_LABEL = {"normal": "Normal version", "exposed": "Version with defaults", "unknown": "Unknown"}

# Names for the result status. Shared by the CLI batch compare summary and the GUI status bar
STATUS_LABEL = {"no_difference": "No differences", "difference": "Differences found", "needs_selection": "Needs selection", "error": "Failed"}
# "Changed" in the table's operation column. Used by the screen logic's OP_LABEL and to tell which rows to mark in HTML output (REQ-226)
OP_CHANGED = "Changed"
# "Added" and "Removed" in the table's operation column. Used by the screen logic's OP_LABEL and for row colors in HTML output (REQ-243)
OP_ADDED = "Added"
OP_REMOVED = "Removed"


def status_breakdown(structured_label: str, structured: int, raw: int) -> str:
    """Breakdown in the text under the pywebview compare window (REQ-113 section 3), e.g. "Differences found (Structured: none / Simple: 4)".

    structured_label is "Structured" or "show". A count of 0 is shown as "none".
    """

    def count(n: int) -> str:
        return "none" if n == 0 else f"{n}"

    return f"{STATUS_LABEL['difference']} ({structured_label}: {count(structured)} / Simple: {count(raw)})"


# When only line differences exist and the content is the same after reordering (REQ-113 section 3)
STATUS_ORDER_ONLY = f"{STATUS_LABEL['difference']} (the order is the only difference)"


def unreadable_utf8(path: object, hints: tuple[InputHint, ...] = ()) -> str:
    """Text for input that could not be read as UTF-8. Hints (guidance guessed from the unreadable bytes, REQ-189) are appended if present."""
    text = f"{path}: cannot be read as UTF-8"
    if not hints:
        return text
    return text + ". " + "".join(input_hint_text(h) for h in hints)


def unreadable(path: object, exc: OSError) -> str:
    """Text for input that could not be read (missing file, no permission, etc.)."""
    return f"{path}: cannot be read ({exc.strerror or exc})"


# --- pywebview window (ADR-0074 / REQ-103, REQ-107. `driftpilot gui`). Exception messages and values never go into any text ---


def gui_web_unavailable() -> str:
    """Text for when the pywebview window was requested but pywebview is not installed (the `gui` extra)."""
    return "pywebview is not installed (pip install 'driftpilot[gui]')"


def webview2_missing() -> str:
    """Text for when the WebView2 runtime is missing on Windows (pywebview falls back to the old IE component. ADR-0074 decision 7)."""
    return "WebView2 Runtime is not installed. Install it from the Microsoft website."


def gui_web_start_failed(error_type: str) -> str:
    """Text for when the display component cannot be initialized (missing pythonnet or pyobjc, etc.). Only the exception type name is added."""
    return f"Cannot use the display component: {error_type}"


def gui_web_files_missing(first: str, rest: int) -> str:
    """Text for when bundled files are missing in a Windows exe (ADR-0192). first is the first missing file; rest is how many more (0 adds no "and N more")."""
    more = f" and {rest} more" if rest else ""
    return (
        f"Some DriftPilot files are missing ({first}{more}). The zip was probably not fully extracted. "
        "Delete the DriftPilot folder and extract the zip again with PowerShell (Expand-Archive) or 7-Zip."
    )


def gui_web_files_blocked(first: str) -> str:
    """Text for when bundled files in a Windows exe still carry the "downloaded from the internet" mark (ADR-0196). first is the first marked file."""
    return (
        f"Windows is blocking DriftPilot files downloaded from the internet ({first}). "
        "Delete the DriftPilot folder, right-click the zip → Properties → check Unblock → OK, and extract it again. "
        "Or run Get-ChildItem -Recurse -File | Unblock-File in PowerShell in the DriftPilot folder."
    )


def gui_web_unknown_text() -> str:
    """Text for when the window asks for a text key that does not exist here (js_api messages)."""
    return "The text for this window could not be found."


def gui_web_call_failed() -> str:
    """Text for an unexpected exception inside a call from the window (js_api). The exception message is not shown in the window."""
    return "Something went wrong while processing."


# Texts shown in the window (passed to the window through js_api messages. No text is written in the HTML. REQ-103 section 4).
# Keys have the form "screen.part.name". Texts for the sample page (static/sample.html) start with sample. All values are fictional
GUI_WEB_TEXTS: dict[str, str] = {
    "sample.heading": "Window component samples",
    "sample.note": "This is the base of the pywebview window (work in progress). It is a page for checking how components look; pressing them does nothing.",
    "sample.info.driftpilot": "DriftPilot version",
    "sample.info.python": "Python version",
    "sample.info.renderer": "Display component",
    "sample.section.buttons": "Buttons",
    "sample.button.primary": "Run",
    "sample.button.secondary": "Choose…",
    "sample.button.danger": "Delete…",
    "sample.button.disabled": "Save",
    "sample.section.bands": "Banners",
    "sample.band.info": "These 4 pairs will be compared. Nothing has been compared yet; press 'Run' to start.",
    "sample.band.warning": "The content looks like a FortiGate config. If it is a FortiGate, set the device type to fortinet and compare again.",
    "sample.band.error": "Could not compare. The Before file cannot be read.",
    "sample.section.table": "Table",
    "sample.table.name": "Device",
    "sample.table.status": "Result",
    "sample.table.diffs": "Differences",
    "sample.table.row1.name": "lab-sw1",
    "sample.table.row1.status": "Differences found",
    "sample.table.row1.diffs": "3",
    "sample.table.row2.name": "lab-sw2",
    "sample.table.row2.status": "No differences",
    "sample.table.row2.diffs": "0",
    "sample.table.row3.name": "lab-fw1",
    "sample.table.row3.status": "Failed",
    "sample.table.row3.diffs": "—",
    "sample.table.row4.name": "lab-rt1",
    "sample.table.row4.status": "Needs selection",
    "sample.table.row4.diffs": "—",
    "sample.section.details": "Collapsible",
    "sample.details.summary": "Diagnostics",
    "sample.details.body": "Shows the location of the settings file and where settings are read from (higher is higher priority).",
    "sample.section.fields": "Input rows",
    "sample.field.text": "Name",
    "sample.field.select": "Format",
    "sample.field.option1": "OpenAI compatible",
    "sample.field.option2": "Azure OpenAI",
    "sample.field.textarea": "Words to mask",
    "sample.field.check": "Read as output that includes defaults",
    "sample.section.tabs": "Tabs",
    "sample.tab.compare": "Compare",
    "sample.tab.batch": "Batch compare",
    "sample.tab.settings": "Settings",
    "sample.tab.compare.body": "The window for comparing two configs goes here.",
    "sample.tab.batch.body": "The window for comparing the pairs in a folder all at once goes here.",
    "sample.tab.settings.body": "The window for changing the AI connection and config.toml goes here.",
    "sample.section.progress": "Progress",
    "sample.progress.label": "2 / 4 pairs compared",
    "sample.footer.open": "Open folder",
    "sample.footer.close": "Close",
    "sample.status.ready": "Ready.",
    # Compare window components (REQ-105 section 5)
    "sample.section.compare": "Compare window components",
    "sample.compare.before": "Before",
    "sample.compare.after": "After",
    "sample.compare.path": "/Users/lab/configs/lab-sw1-before.txt",
    "sample.compare.browse": "Browse…",
    "sample.compare.paste": "Paste…",
    "sample.compare.summary": "1,204 lines",
    "sample.compare.mode": "Compare mode",
    "sample.compare.mode.both": "Both",
    "sample.compare.mode.raw": "Simple",
    "sample.compare.before_no": "Before",
    "sample.compare.after_no": "After",
    "sample.compare.row1.no": "12",
    "sample.compare.row1.text": "hostname lab-sw1",
    "sample.compare.row2.mark": "~",
    "sample.compare.row2.no": "13",
    "sample.compare.row2.before": " name users",
    "sample.compare.row2.after": " name staff",
    "sample.compare.status": "Differences found",
    "sample.compare.counts": "Structured 1  Simple 1",
    "sample.compare.paste.title": "Paste Before",
    "sample.compare.paste.prompt": "Paste a config or terminal log",
    "sample.compare.paste.ok": "OK",
    "sample.compare.paste.cancel": "Cancel",
}


def save_caution() -> str:
    """Caution about handling saved results (ADR-0017 decision 4). The saved file contains the input's setting values in plain text."""
    return "This contains the input's setting values as they are. Treat it the same as the original config (confidential)."


def warning_message(w: SafetyWarning) -> str:
    if w.kind == "empty_category":
        return (
            f"In {_SIDE_LABEL[w.side or 'before']}, the tree has {w.detail['tree_count']} {w.detail['subject']}, "
            f"but no {w.category} could be read. The format may have changed."
        )
    if w.kind == "duplicate_line":
        # Values are not included (detail has only the scope and line numbers)
        scope = w.detail["scope"] or "the top level"
        if not w.detail["lines"]:
            # A-type trees do not keep the original line numbers (ADR-0030 decision 5)
            return (
                f"In {_SIDE_LABEL[w.side or 'before']}, the same line appears {w.detail.get('count', 2)} times under {scope} "
                "(line numbers are not available with this reading method). None of them were dropped."
            )
        lines = ", ".join(str(n) for n in w.detail["lines"])
        return (
            f"In {_SIDE_LABEL[w.side or 'before']}, under {scope}, {_plural(len(w.detail['lines']), 'line', 'lines')} {lines} "
            f"{_plural(len(w.detail['lines']), 'is a duplicate', 'are duplicates')} "
            "(the same line, or the same item with different values). Neither was dropped, but the IR uses the value from the later line."
        )
    if w.kind == "platform_mismatch":
        return _platform_mismatch_message(w)
    if w.kind == "pager_marks":
        return (
            f"In {_SIDE_LABEL[w.side or 'before']}, paging marks (--More--) were removed at {w.detail['count']} "
            f"{_plural(w.detail['count'], 'place', 'places')} before comparing. "
            "If the log was stopped partway, some settings are missing."
        )
    if w.kind == "input_form":
        return _input_form_message(w)
    if w.kind == "input_hint":
        d = w.detail
        return f"{_SIDE_LABEL[w.side or 'before']}: {_hint_text(d['kind'], d['count'], d.get('width'), d.get('form'))}"
    if w.kind == "platform_auto":
        return _platform_auto_message(w)
    if w.kind == "raw_ignored_many":
        d = w.detail
        return (
            f"In {_SIDE_LABEL[w.side or 'before']}, {d['ignored']} {_plural(d['ignored'], 'line', 'lines')}, more than half of the lines, "
            f"are ignored by the Simple comparison settings (out of {d['lines']}). "
            "Check the patterns for the lines to ignore."
        )
    return (
        f"The tree has {w.detail['tree_diff_lines']} different {_plural(w.detail['tree_diff_lines'], 'line', 'lines')}, but there are 0 structured differences. "
        "Changes to settings that could not be read may have been missed."
    )


# Display names for device types (ADR-0151 decision 7). None means not specified (the Cisco-family reading). The detection result "a" is shown with the same name
PLATFORM_LABEL: dict[str | None, str] = {"fortinet": "FortiGate", "fujitsu_sir": "Si-R", None: "Cisco family / Arista"}
AUTO_PLATFORM_LABEL = "Auto"


def platform_label(name: str | None) -> str:
    """Display name of a family or detection result. "a" (Cisco-family detection) has the same name as not specified. Names not in the table are shown as they are."""
    if name == "a":
        name = None
    return PLATFORM_LABEL.get(name, name or PLATFORM_LABEL[None])


def auto_platform_text(used: str | None) -> str:
    """Display of the family chosen when the device type is Auto (e.g. "Auto (FortiGate)". ADR-0151 decision 7)."""
    return f"{AUTO_PLATFORM_LABEL} ({platform_label(used)})"


def auto_platform_line(used: str | None) -> str:
    """The one CLI text line for when --platform auto settled on a config (e.g. "Device type: Auto (FortiGate)")."""
    return f"Device type: {auto_platform_text(used)}"


def _platform_auto_message(w: SafetyWarning) -> str:
    """No detection or a mismatch when the device type is Auto (ADR-0151 decision 2). Only names are shown, never values."""
    if w.detail.get("result") == "conflict":
        before = platform_label(w.detail.get("before"))
        after = platform_label(w.detail.get("after"))
        return (
            f"The detected device type differs between Before and After (Before: {before}, After: {after}). "
            "Read as Cisco family / Arista. Not sent to the AI. Check the device type."
        )
    return "Could not detect the device type. Read as Cisco family / Arista. If that is wrong, choose the device type."


def structured_duplicate_message(detail: dict[str, Any], mode: str) -> str:
    """Text for when the Structured comparison could not run because of duplicate lines (REQ-26 section B-2).

    Says what happened, where, what happened to the Simple comparison, and what to do. The wording about the Simple comparison depends on the mode.
    Structured alone does not run the Simple comparison (section B-2b). The line and its parent path are shown as written. This text is not sent to the LLM
    (a change from section B-2 by the decision maker's instruction; same treatment as the difference display showing actual values).
    """
    side = _SIDE_LABEL[detail["side"]]
    line = detail.get("line")
    if line is None:
        where = f"{side}: the location could not be determined."
    else:
        block = f'under the "{detail["scope"]}" block' if detail.get("scope") else "at the top level (a line with no indent)"
        numbers = detail.get("lines") or []
        number_text = ", ".join(str(n) for n in numbers)
        at = f" ({_plural(len(numbers), 'line', 'lines')} {number_text})" if numbers else ""
        where = f'Location: {side}, {block}, the line "{line}"{at}.'
    if mode == "structured":
        raw = "The Simple comparison was not run. Compare again with the mode (--mode) set to Both or Simple to see it."
    else:
        raw = "The Simple comparison result is shown."
    return (
        f"The same line is written 2 or more times, so the Structured comparison could not run. {where} {raw} "
        "Merge the duplicates into one line and compare again, or look at the Simple comparison."
    )


# Headings for device-specific values attached to route differences (ADR-0072, REQ-94). Other keys are shown as the key name
_ROUTE_DETAIL_LABEL = {"number": "number", "edit": "edit", "name": "name"}


def route_detail_note(vendor_specific: dict[str, Any]) -> str:
    """Short device-specific note attached to an added or removed route. Examples: "(number 7)", "(edit 7)", "(name EXAMPLE)".

    Keys whose value is None are skipped. Appended after the route value in CLI text and in the GUI difference list.
    """
    parts = []
    for detail in vendor_specific.values():
        if not isinstance(detail, dict):
            parts.append(f" ({detail})")
            continue
        for key, value in detail.items():
            if value is not None:
                parts.append(f" ({_ROUTE_DETAIL_LABEL.get(key, key)} {value})")
    return "".join(parts)


# Names of the guesses made by safety net 5 (REQ-25), and descriptions of the clues (IDs in the table of docs/notes/platform-sniff.md)
_HINT_LABEL = {"a": "a Cisco-style or Arista config", "d": "a FortiGate config"}
_HINT_RULE = {"SNF-01": "first line starts with #config-version=", "SNF-02": "config ... and end come in pairs", "SNF-03": "lines for interface, hostname and !"}


def _platform_mismatch_message(w: SafetyWarning) -> str:
    """What was suspected and what to do. No values are used (detail holds only the specified type, the guess and the clue ID)."""
    side = _SIDE_LABEL[w.side or "before"]
    hint, rule, specified = w.detail["hint"], w.detail["rule"], w.detail["specified"]
    looks = f"looks like {_HINT_LABEL.get(hint, hint)} ({_HINT_RULE.get(rule, rule)}; {rule})."
    if specified is None:
        return (
            f"The contents of {side} {looks} "
            "No device type (--platform) was specified, so it was read with the Cisco-style/Arista parser. "
            "If it is FortiGate, compare again with the device type set to fortinet."
        )
    return f"The device type is set to {specified}, but the contents of {side} {looks} Check that the device type is correct."


# Sentence for each kind of input-form notice (ADR-0138 decision 7). The kind is not used for branching, so unknown kinds also become a sentence
_INPUT_FORM_TEXT = {
    "encoding:cp932": "Shift_JIS",
    "encoding:utf-16": "UTF-16",
    "encoding:utf-8-bom": "UTF-8 with BOM",
}


def _input_form_message(w: SafetyWarning) -> str:
    side = _SIDE_LABEL[w.side or "before"]
    kind = w.detail["kind"]
    if kind in _INPUT_FORM_TEXT:
        return f"The {side} file was {_INPUT_FORM_TEXT[kind]}."
    return f"The input form of {side} was corrected ({kind})."


# Input hints (ADR-0138 decision 7; table in REQ-189 section 2). Names of the kinds of added prefix
_HINT_FORM_TEXT = {"timestamp": "a timestamp", "line_number": "a line number", "mail_quote": "a quote mark (> )"}


def input_hint_text(hint: InputHint) -> str:
    """Hint sentence guessed from the input. Contains only counts and kinds, never line contents."""
    return _hint_text(hint.kind, hint.count, hint.width, hint.form)


def _hint_text(kind: str, count: int, width: int | None, form: str | None) -> str:
    if kind == "wrap_suspected":
        return f"Line wrapping is suspected ({count} lines of width {width}). Broken lines produce wrong differences."
    if kind == "wrap_unrepaired":
        return f"{count} lines could not be joined back after wrapping."
    if kind == "html_no_pre":
        return "This looks like HTML. Save only the body of the config."
    if kind == "utf16_no_bom":
        return "This looks like UTF-16 without a BOM."
    if kind == "partial_prefix":
        return (
            f"Each line seems to start with {_HINT_FORM_TEXT.get(form or '', form)}. "
            f"{count} lines had a different form, so they were not stripped."
        )
    # The kind is not used for branching, so unknown kinds also become a sentence
    return f"Something about the input form needs attention ({kind})."


def notice_message(n: ExposureNotice) -> str:
    before: dict[str, Any] = n.detail["before"]
    after: dict[str, Any] = n.detail["after"]
    if n.kind == "mixed_exposure":
        return (
            f"Comparing a normal version with a version with defaults (Before: {before['command']!r} is {_STATE_LABEL[before['state']]}, "
            f"After: {after['command']!r} is {_STATE_LABEL[after['state']]}). Most differences come from how the config was obtained"
        )
    if n.kind == "unknown_both":
        return "Unknown retrieval command: cannot tell whether Before and After are a normal version or a version with defaults"
    known, known_label, other_label = (
        (before, "Before", "After") if before["state"] != "unknown" else (after, "After", "Before")
    )
    head = "[Version with defaults] " if n.emphasized else ""
    return f"{head}{known_label} was obtained with {known['command']!r} ({_STATE_LABEL[known['state']]}). How {other_label} was obtained is unknown"


def notice_line(n: ExposureNotice) -> str:
    return ("Warning: " if n.level == "warning" else "Note: ") + notice_message(n)


# Reasons for excluding values from comparison (ADR-0025 decision 6). Text for each value of core's ComparisonExclusion.reason
_EXCLUSION_REASON = {
    "reencrypted_value": "FortiGate values that are re-encrypted (values with ENC) are excluded from comparison",
}


def exclusion_line(e: ComparisonExclusion) -> str:
    """One line for values excluded from comparison. Also says that really changing them will not show as a difference (never ignored silently)."""
    reason = _EXCLUSION_REASON.get(e.reason, f"Some values are excluded from comparison (reason: {e.reason})")
    return (
        f"Excluded from comparison: {reason} (Before: {e.counts['before']}, After: {e.counts['after']}; rule {e.rule_id}). "
        "Even if these values really change, no difference will be shown"
    )


def saved_notice(path: Path) -> str:
    """One line shown after saving with --save (ADR-0017 decision 4). The saved file contains the input settings in plain text."""
    return f"Note: the comparison result was saved to {path}. {save_caution()}"


def batch_saved_notice(folder: Path, count: int) -> str:
    """One line shown after saving with batch --save DIR. One line in total, not one per result."""
    return f"Note: saved {count} batch comparison {_plural(count, 'result', 'results')} to {folder}. {save_caution()}"


def batch_summary_lines(summary: BatchSummary, exit_code: int) -> list[str]:
    """Summary written to standard error (1 or 2 lines). core returns only counts and elapsed time as numbers."""
    c = summary.counts
    breakdown = " / ".join(f"{label} {c[status]}" for status, label in STATUS_LABEL.items())
    lines = [
        f"Batch compare: ran {summary.executed} of {summary.total} ({breakdown}), "
        f"{summary.elapsed_seconds:.1f} s. Exit code {exit_code}"
    ]
    if summary.cancelled or summary.interrupted:
        lines.append(f"Stopped. The remaining {summary.total - summary.executed} were not run")
    return lines


def batch_cancel_accepted() -> str:
    return "Stop request accepted. It will stop when the running job finishes (press Ctrl+C again to stop immediately)"


# --- Pair generation and the batch compare window (ADR-0020 / REQ-27 part A). Keep the way counts are worded the same in CLI and GUI ---


def selection_text(selection: SideSelection, source: str | None) -> str:
    """Display of the section selection (batch --dry-run and the "Section" column of the batch compare window)."""
    if selection.command is not None:
        text = f"command {selection.command!r}"
    elif selection.number is not None:
        text = f"segment {selection.number}"
    else:
        return "Automatic (chosen only when there is a single section)"
    return f"{text} (defaults)" if source == "defaults" else text


UNPAIRED_SIDE_LABEL = {"before": "Only in Before", "after": "Only in After"}


def pairing_heading(pairing: "Pairing") -> list[str]:
    """Heading listing the Before and After directories, so that a mix-up is noticed (ADR-0020 Action Item 3)."""
    return [f"Before: {pairing.before_dir}", f"After: {pairing.after_dir}"]


def paired_count_line(count: int) -> str:
    return f"Paired files: {count} (file names match exactly, including the extension)"


def unpaired_count_line(count: int) -> str:
    return f"Unpaired files: {count}"


def unpaired_side_line(side: str, count: int) -> str:
    return f"{UNPAIRED_SIDE_LABEL[side]}: {count}"


def pairing_text(pairing: "Pairing") -> str:
    """List for --pair-by-name. The heading and count lines are built by the same functions as the batch compare window (REQ-27 section A-2)."""
    lines = [*pairing_heading(pairing), ""]
    lines.append(paired_count_line(len(pairing.pairs)))
    lines += [f"  {name}" for name in pairing.pairs]
    lines.append(unpaired_count_line(len(pairing.before_only) + len(pairing.after_only)))
    for side, names in (("before", pairing.before_only), ("after", pairing.after_only)):
        lines.append(f"  {unpaired_side_line(side, len(names))}")
        lines += [f"    {name}" for name in names]
    return "\n".join(lines) + "\n"


def pairing_summary_lines(pairing: "Pairing") -> list[str]:
    """Lines shown above the pair list in the batch compare window. Same as the heading and count lines of the --pair-by-name list (the name lists are tabs in the window)."""
    sides = ", ".join(unpaired_side_line(side, len(names)) for side, names in (("before", pairing.before_only), ("after", pairing.after_only)))
    return [
        *pairing_heading(pairing),
        paired_count_line(len(pairing.pairs)),
        f"{unpaired_count_line(len(pairing.before_only) + len(pairing.after_only))} ({sides})",
    ]


def paired_tab_title(count: int) -> str:
    return f"Files to compare: {count}"


def unpaired_tab_title(count: int) -> str:
    """Shown even for 0 (what matters is that the count is visible; ADR-0020 "always report")."""
    return f"Files in one side only: {count}"


def pairing_banner(count: int) -> str:
    """Blue banner above the pair list (ADR-0020 "confirm by default, run with --confirm" turned into a window)."""
    return (
        f"{count} {_plural(count, 'pair', 'pairs')} will be compared. Nothing has been compared yet; that starts when you click \"Run\". "
        "Check that you did not pick the wrong folders."
    )


def batch_folder_not_found(side: str, path: str) -> str:
    """When the folder field of the batch compare window is not yet an existing absolute folder path (for example while typing; REQ-97 section 1)."""
    return f"The {_SIDE_LABEL[side]} folder was not found ({path}). Choose a folder, or finish typing the full path"


def batch_manifest_not_found(path: str) -> str:
    """When the manifest field of the batch compare window is not yet an existing absolute file path (REQ-97 section 1)."""
    return f"The pair file was not found ({path}). Choose a file, or finish typing the full path"


# Button text of the folder/manifest picker (instead of wx's default "Browse"; REQ-97 section 3)
PICK_BUTTON = "Browse…"


def batch_progress_text(progress: BatchProgress) -> str:
    """Text like "5 / 12" followed by the name of the pair being read."""
    return f"{progress.index} / {progress.total}: {progress.label or progress.job_id}"


def batch_cancel_note() -> str:
    """Meaning of cancel (REQ-15; should_cancel only takes effect at job boundaries)."""
    return "Clicking \"Cancel\" stops after the pair currently being read has finished, because stopping midway would leave a partial result."


def handoff_note() -> str:
    """"Open in compare window" of the batch compare window (ADR-0029 decision 8; only the inputs are handed over)."""
    return "This only puts those two files into the input fields of the compare window (results are not carried over)"


def batch_handoff_note() -> str:
    """Note for "Open in compare window" in the batch compare window (pywebview) (button title; ADR-0170 decision 6). wx keeps using handoff_note()."""
    return "Puts those two files into the compare window and compares them. For a pair with an AI answer, if the differences are the same as when sent, you can continue asking from that answer"


# State and count columns of a row in the batch result list that has no result yet
JOB_WAITING = "Waiting"
JOB_RUNNING = "Running"
NO_COUNT = "—"

_LLM_FAILED = ("connection_error", "api_error", "internal_error")


def llm_column(llm: LlmAnalysis | None) -> str:
    """"AI analysis" column of the batch result list: answered / not run (reason) / failed (reason) / not sent."""
    if llm is None or llm.reason == "disabled":
        return NO_COUNT
    if llm.status == "done":
        return "Answered"
    if llm.reason == "in_whole":
        return LLM_IN_WHOLE
    if llm.reason == "not_approved":
        return "Not sent"
    if llm.reason == "dry_run":
        return "Awaiting confirmation (not sent yet)"
    if llm.reason in _LLM_FAILED:
        return f"Failed ({llm_reason(llm)})"
    return f"Not run ({llm_reason(llm)})"


# --- LLM analysis for batch compare (ADR-0031). The first-stage summary is the same in the CLI (standard error) and the GUI (top of the confirmation window) ---


def batch_llm_preview_lines(preview: BatchLlmPreview, *, model: str | None, base_url: str | None) -> list[str]:
    """Summary for confirmation before sending: pairs to send, total, shape of unsent lines, masked count, truncation, fingerprint, destination (ADR-0031 decision 3)."""
    lines = [
        f"Pairs to send to AI: {len(preview.jobs)}. Total differences to send: {preview.total_diffs:,}"
        f" (overall limit {preview.max_total_diffs:,})",
        f"Masked values: {preview.masked:,} (number replaced with <secret:N>)",
        f"Pairs truncated by the per-pair limit: {preview.truncated_jobs}",
    ]
    if preview.unsent_lines:
        lines.append(f"Lines not sent because they could not be classified: {preview.unsent_lines:,} (they still appear in the result as differences)")
        lines += unsent_shape_lines(preview.unsent_shapes)
    else:
        lines.append("Lines not sent because they could not be classified: none")
    lines.append(
        f"Masking table fingerprint: {preview.masking_fingerprint or '-'}  Model: {model or '-'}  Destination: {base_url or '(default)'}"
    )
    return lines


def batch_llm_preview_row(job_id: str, label: str | None, sent: int, masked: int, unsent: int) -> str:
    """One pair row listed in the CLI summary (one row of the list in the GUI)."""
    name = f"{job_id}  {label}" if label else job_id
    return f"  {name}: differences to send {sent} / masked values {masked} / lines not sent {unsent}"


def batch_llm_over_limit(total: int, limit: int) -> str:
    """Message for when the overall limit is exceeded (ADR-0031 decision 4). Nothing is sent, not even a part."""
    return (
        f"There are {total:,} differences to send to the AI in total, which exceeds the overall limit of {limit:,}. Nothing was sent. "
        "To send, narrow down the pairs to compare, or raise the limit (--llm-max-total-diffs) and run again"
    )


def batch_llm_confirm_hint() -> str:
    """Hint for CLI batch --llm run without --confirm. Same form as pair generation (--pair-by-name)."""
    return "Nothing has been sent to the AI yet. To send this content, run again with --confirm"


def batch_llm_nothing_to_send() -> str:
    return "There is nothing to send to the AI (no differences, or no lines that can be sent). Nothing was sent"


def batch_llm_gave_up(count: int) -> str:
    """Message for when the rest was given up after consecutive failures (ADR-0031 decision 7). The comparison finished."""
    return (
        f"Sending to the AI failed {_llm_give_up_after()} times in a row, so the AI was not called for the remaining {count} "
        f"{_plural(count, 'pair', 'pairs')} (the comparison ran to the end)"
    )


def batch_llm_payloads_text(preview: BatchLlmPreview) -> str:
    """Full text written by 'Save content to file': a heading per pair, then the content to send (after masking)."""
    n = len(preview.jobs)
    parts = [
        "Content to send to the AI (after masking; only the fixed answer-format instructions are added when sending). "
        f"{n} {_plural(n, 'pair', 'pairs')}",
        "",
    ]
    for job in preview.jobs:
        name = f"{job.job_id}  {job.label}" if job.label else job.job_id
        parts += [f"=== [{job.index}] {name} ===", job.payload, ""]
    return "\n".join(parts)


# --- LLM analysis (REQ-16) ---

_LLM_REASON = {
    "disabled": "AI analysis was not requested",
    "dry_run": "Preview of the content to send only (--llm-dry-run). Nothing was sent",
    "no_diff": "There are no differences, so the AI was not called",
    "no_structured_diff": "The structured comparison has no differences, so nothing was sent (for example, only the order differs)",
    "nothing_to_send": "There are no differences that can be sent (all lines were ones whose credentials could not be judged)",
    "no_api_key": (
        "No API key (use 'Save to key store' in the settings window, or put it in the per-profile environment variable DRIFTPILOT_LLM_API_KEY_<suffix>. "
        "When not using a profile, use DRIFTPILOT_LLM_API_KEY or llm.api_key in the per-user settings file)"
    ),
    "client_unavailable": "The AI connection component is not installed (pip install 'driftpilot[llm]')",
    "not_configured": "No model is set (llm.model in the settings file)",
    "placeholder_base_url": "Replace <resource name> in the connection URL. Nothing was sent",
    "cli_not_found": "CLI not found (check the CLI location field or PATH)",
    "connection_error": "Could not connect to the AI",
    "api_error": "The AI returned an error",
    "internal_error": "AI analysis processing failed",
    # Two-step sending for Batch compare (ADR-0031). None of these were sent
    "not_approved": "This pair was not sent, by choice at the confirmation before sending",
    "changed_after_approval": "Nothing was sent, because the content about to be sent differed from what was approved at confirmation",
    # The count is filled in by llm_reason (not read at core import time)
    "gave_up": "Sending to the AI failed {give_up_after} times in a row, so it was not called for this pair",
    "over_total_limit": "Nothing was sent, because the total number of differences to send exceeds the overall limit",
    "cancelled": "Nothing was sent, because it was cancelled",
    "compare_failed": "Nothing was sent, because the comparison of this pair failed",
    "platform_conflict": "Nothing was sent to the AI, because the device type detected differs between Before and After",
    "platform_not_sir": "Nothing was sent to the AI, because the device type is set to Si-R but the content looks like a different device type",
    # The character limit is filled in by llm_reason (llm_analyzer is not read at import time; ADR-0158 decision 5)
    "full_text_too_long": "Nothing was sent, because the full text exceeds the limit ({max_full_text_chars} characters). Clear the checkbox to send only the differences",
    # The always-attached premise text cannot be sent (ADR-0172 decision 4). The line is not shown
    "premise_refused": (
        "Nothing was sent to the AI, because the always-attached premise text has something to mask or a line that cannot be sent. "
        "Fix 'Always-attached premise' on the 'Prompt' tab of the settings window"
    ),
    # Pair without an answer when all pairs were sent in one request in the compare window (ADR-0152 decision 7)
    "in_whole": "All pairs were sent together in one request. The answer is in the first pair's row",
}
# AI column of the pair list when all pairs were sent in one request (HTML, CSV, and the separate AI window; ADR-0152 decision 10)
LLM_IN_WHOLE = "All pairs were sent together in one request"
# Text for reason cancelled after sending (transmitted is true) (ADR-0157)
LLM_CANCELLED_AFTER_SEND = "Cancelled after sending, so there is no answer"
# When the API returns 429 (ADR-0168 decision 4). "(HTTP 429)" is always appended last. Seconds are not saved
LLM_RATE_LIMITED = "The AI rate limit was reached"
RATE_LIMIT_STATUS = 429


def _rate_limited(llm: LlmAnalysis) -> bool:
    return llm.reason == "api_error" and llm.detail.get("status_code") == RATE_LIMIT_STATUS


def llm_rate_limited(retry_after: int | None, *, cli: bool = False) -> str:
    """Text for 429 (AI pane of the compare window and CLI stderr; ADR-0168 decision 4). Seconds if known, else billing-limit hint."""
    again = "run it again" if cli else "press the button again"
    if retry_after is not None:
        return f"{LLM_RATE_LIMITED}. Wait about {retry_after:,} {_plural(retry_after, 'second', 'seconds')}, then {again}"
    return f"{LLM_RATE_LIMITED}. Wait a while, then {again}. If it keeps happening, also check your plan and billing limits"


def cli_api_error_line(text: str) -> str:
    """Line for the API error text on stderr of CLI compare --llm (one pair; ADR-0168 decision 5). The text is already cleaned."""
    return f"Error text from the API (cut short; key-like characters are masked): {text}"


def llm_reason(llm: LlmAnalysis) -> str:
    """Text for why the AI was not run. Does not fail on an unknown reason (the value is not used for branching)."""
    text = _LLM_REASON.get(llm.reason or "", f"Reason: {llm.reason}")
    if llm.reason == "cancelled" and llm.transmitted:
        # Cancelled after sending, while the answer was arriving (ADR-0157; partial text is not used as the answer)
        text = LLM_CANCELLED_AFTER_SEND
    if llm.reason == "gave_up":
        text = text.format(give_up_after=_llm_give_up_after())
    if llm.reason == "full_text_too_long":
        text = full_text_too_long()
    if _rate_limited(llm):
        # Saved result, Batch compare column, CLI text field (seconds are not saved, so not shown; ADR-0168 decision 4)
        text = f"{LLM_RATE_LIMITED}. Wait a while, then try again"
    status_code = llm.detail.get("status_code")
    return f"{text} (HTTP {status_code})" if status_code is not None else text


def premise_refused_text() -> str:
    """Text for when the always-attached premise cannot be sent (ADR-0172 decision 4). Shown by the Batch compare window without a confirmation."""
    return _LLM_REASON["premise_refused"]


# Hint added when the premise cannot be sent in the CLI (REQ-234 section 6)
PREMISE_REFUSED_CLI_HINT = " (To send without the premise, add --llm-no-premise)"


def premise_refused_cli() -> str:
    """Text for when the CLI ends with exit code 2 without comparing or sending (main adds the 'Error: ' prefix)."""
    return f"{premise_refused_text()}{PREMISE_REFUSED_CLI_HINT}"


# Help for CLI --llm-no-premise (compare, batch; ADR-0172 decision 3) and the refusal when neither --llm nor --llm-dry-run is given
LLM_NO_PREMISE_HELP = "Send to the AI without the always-attached premise ('Prompt' tab of the settings window). Use together with --llm or --llm-dry-run"
LLM_NO_PREMISE_ALONE = "Specify --llm-no-premise together with --llm or --llm-dry-run"


def full_text_too_long() -> str:
    """Text for when nothing was sent because the After full text or the whole exceeds the limit (ADR-0158 decision 5)."""
    from driftpilot.llm_analyzer.payload import MAX_FULL_TEXT_CHARS

    return _LLM_REASON["full_text_too_long"].format(max_full_text_chars=f"{MAX_FULL_TEXT_CHARS:,}")


def llm_truncated_lines(llm: LlmAnalysis) -> list[str]:
    """Notice for when the result was cut off (wording approved 2026-10-16).

    The difference-count limit is returned to the user as a signal that the compared targets may differ.
    For an analysis that sent all pairs together (source is whole; ADR-0152), say that some pairs were
    cut off at the per-pair limit (do not compare the overall count with the per-pair limit). The other lines are the same.
    """
    if llm.source == "whole":
        head = f"Some pairs were cut off at the per-pair limit of {llm.max_diffs:,} {_plural(llm.max_diffs, 'difference', 'differences')}."
    else:
        head = (
            f"There are {llm.diff_total:,} {_plural(llm.diff_total, 'difference', 'differences')}; "
            f"cut off at {llm.max_diffs:,}."
        )
    return [
        head,
        "A difference count this large can mean the compared targets are different",
        "(Normal version vs Version with defaults, a different device, or mismatched pairs).",
        "Check the safety-net warning and the section selection.",
    ]


def llm_unsent_line(llm: LlmAnalysis) -> str | None:
    """Count of lines not sent (ADR-0019 decision 5). Also says the comparison result is unchanged; only the LLM opinion is missing."""
    unsent = llm.unsent
    if unsent is None or not (unsent.lines or unsent.context_lines):
        return None
    head = f"{unsent.lines} {_plural(unsent.lines, 'line', 'lines')} not sent because they could not be judged"
    if unsent.blocks:
        head += f" (including {unsent.blocks} PEM {_plural(unsent.blocks, 'block', 'blocks')})"
    if unsent.context_lines:
        head += f". {unsent.context_lines} surrounding context {_plural(unsent.context_lines, 'line', 'lines')} not sent either"
    return head + ". They still appear above as differences (only the AI's opinion is missing)"


def llm_unsent_shape_lines(llm: LlmAnalysis) -> list[str]:
    """List of shapes of lines not sent (no values). Candidates to add to the masking table (ADR-0019 decision 6)."""
    if llm.unsent is None:
        return []
    return unsent_shape_lines(llm.unsent.shapes)


def unsent_shape_lines(shapes: "list[UnsentShape] | tuple[UnsentShape, ...]") -> list[str]:
    """Lines listing shapes of lines not sent. Same form for a single comparison and Batch compare (all pairs combined)."""
    if not shapes:
        return []
    lines = ["Shapes of lines not sent (no values; candidates to add to the masking table):"]
    for s in shapes:
        if s.kind == "pem_block":
            lines.append(f"  {s.count:4d} {_plural(s.count, 'block', 'blocks')} ({s.lines} {_plural(s.lines, 'line', 'lines')})  [{s.scope}] {s.shape}")
        else:
            lines.append(f"  {s.count:4d} {_plural(s.count, 'line', 'lines')}  [{s.scope}] {s.shape}")
    return lines


def llm_section_lines(llm: LlmAnalysis) -> list[str]:
    """The '== AI analysis ==' section of text output. States that the answer is a note and not used to judge differences."""
    lines = ["== AI analysis =="]
    if llm.status == "done":
        lines.append(f"Model: {llm.model}  Date/time: {llm.asked_at}")
        lines.append("Note: the AI's answer may differ each time, even for the same input. It is not used to judge differences (the differences are as shown above)")
    else:
        lines.append(f"AI analysis: not run ({llm_reason(llm)})")
    if llm.truncated:
        lines += llm_truncated_lines(llm)
    if (unsent := llm_unsent_line(llm)) is not None:
        lines.append(unsent)
    if llm.status == "done":
        lines += ["--- Answer ---", llm.answer or "", "--- End ---"]
    if llm.reason == "dry_run":
        lines += ["--- Content to send (after masking; only the fixed answer-format instructions are added when sending) ---", llm.payload or "", "--- End ---"]
        lines += llm_unsent_shape_lines(llm)
    elif llm.reason == "nothing_to_send":
        # Even if the content to send is empty (including --llm-dry-run), show the shapes not sent. They are candidates to add to the table (ADR-0019 decision 6)
        lines.append("Content to send: none (there are no differences that can be sent. Nothing was sent)")
        lines += llm_unsent_shape_lines(llm)
    return lines


def llm_screen_lines(llm: LlmAnalysis, *, retry_after: int | None = None) -> list[str]:
    """Lines for the AI pane of the pywebview compare screen (REQ-206 section 8 / ADR-0148 decision 8). llm_section_lines minus the head lines.

    Omitted: the '== AI analysis ==' heading, the model and date/time line and the note line when there is an answer,
    and the '--- Answer ---' and '--- End ---' lines.
    The reason for not sending (not run), truncation, lines not sent, and the content to send are shown the same as in llm_section_lines.
    CLI text, the wx window, and saving stay as in llm_section_lines (unchanged).
    retry_after is the wait in seconds the API returned with 429 (held only by the screen state; ADR-0168 decision 4). Used only for 429.
    """
    lines: list[str] = []
    if llm.status != "done" and retry_after is not None and _rate_limited(llm):
        lines.append(f"AI analysis: not run ({llm_rate_limited(retry_after)} (HTTP {RATE_LIMIT_STATUS}))")
    elif llm.status != "done":
        lines.append(f"AI analysis: not run ({llm_reason(llm)})")
    if llm.truncated:
        # After the truncation notice, add one line pointing to where the limit can be changed (ADR-0167 decision 6; not added to CLI, wx, or saved output)
        lines += [*llm_truncated_lines(llm), LIMITS_SCREEN_HINT]
    if (unsent := llm_unsent_line(llm)) is not None:
        lines.append(unsent)
    if llm.status == "done":
        lines.append(llm.answer or "")
    if llm.reason == "dry_run":
        lines += ["--- Content to send (after masking; only the fixed answer-format instructions are added when sending) ---", llm.payload or ""]
        lines += llm_unsent_shape_lines(llm)
    elif llm.reason == "nothing_to_send":
        lines.append("Content to send: none (there are no differences that can be sent. Nothing was sent)")
        lines += llm_unsent_shape_lines(llm)
    return lines


def workdir_api_key_ignored(path: Path) -> str:
    """One line for when the working-folder settings file has an api_key (the value is not shown)."""
    return (
        f"Note: llm.api_key in {path} is not read (by design, keys are not read from files in the working folder). "
        "Put the key in 'Save to key store' in the settings window, or in the per-profile environment variable DRIFTPILOT_LLM_API_KEY_<suffix>. "
        "When not using a profile, use DRIFTPILOT_LLM_API_KEY or llm.api_key in the per-user settings file"
    )


def workdir_model_ignored(profile_name: str) -> str:
    """One line for when an AI connection profile is in use and the working-folder driftpilot.toml has model or base_url (REQ-42 section 4)."""
    return f"Note: the profile '{profile_name}' is in use, so model and base_url in the working-folder driftpilot.toml are not used"


def common_env_ignored_note() -> str:
    """One line for when profiles exist and the shared environment variable is also set (ADR-0077 decision 3; CLI and settings banner; the key value is not shown)."""
    return "Note: DRIFTPILOT_LLM_API_KEY is not used. Decide the environment variable name for each profile"


def llm_profiles_missing(path: Path) -> str:
    """When --llm-profile is given but the profile file does not exist (nothing is sent)."""
    return f"{path}: there are no AI profiles. --llm-profile cannot be used (nothing was sent)"


def llm_profile_not_found(name: str, names: list[str]) -> str:
    """When the --llm-profile name does not exist (nothing is sent). Lists only the profile names (no values or keys)."""
    listed = ", ".join(f'"{n}"' for n in names)
    return f'There is no profile named "{name}" for --llm-profile (nothing was sent). Available profiles: {listed}'


def workdir_api_key_reason() -> str:
    """Why keys are not read from the working folder (added to workdir_api_key_ignored in the settings window; REQ-27 section B-2)."""
    return "Files in the working folder are easily committed to git, so a key written there is not read (one is written, but it was not read)"


# --- API key store and settings window (REQ-34 / ADR-0033). No sentence includes the key value ---


def keyring_not_installed() -> str:
    return "keyring is not installed (pip install 'driftpilot[llm]')"


def keyring_unusable() -> str:
    return "The key store cannot be used in this environment. Use an environment variable"


_KEYRING_ACTION = {"read": "read", "save": "save", "delete": "delete"}


def keyring_failed(action: str, error_type: str) -> str:
    """When a vault operation failed with an exception. Only the type name is added, not the message (it may contain a value)."""
    return f"Could not {_KEYRING_ACTION[action]} the key store ({error_type})"


def api_key_empty() -> str:
    return "Enter an API key. Not saved."


def api_key_bad_length(length: int, low: int, high: int) -> str:
    return f"The API key must be between {low} and {high} characters (you entered {length}). Not saved."


def api_key_has_space() -> str:
    return "The API key must not contain spaces or line breaks. Not saved."


def api_key_saved(store: str) -> str:
    return f"Saved to {store}."


def api_key_saved_env_wins() -> str:
    return "Saved. However, the key in the environment variable takes priority."


def api_key_deleted(store: str) -> str:
    return f"Deleted from {store}."


def api_key_not_stored() -> str:
    return "Not stored."


def api_key_delete_confirm(store: str) -> str:
    return f"Delete the DriftPilot API key stored in {store}?"


def plaintext_api_key_band() -> str:
    return "The API key is written in plain text in the settings file. Save it to the key store with the field below, then delete the api_key line in the settings file by hand."


# --- Writing the per-user settings file (config.toml) from the settings window (REQ-93 / ADR-0071). Never put the key value in any sentence ---

# Second line of the template and the guidance after "Create template" (ADR-0071: the window writes only fixed lines)
CONFIG_TEMPLATE_NOTE = (
    "When you click 'Save' in the settings window, DriftPilot rewrites the limit and masked-word lines. "
    "'Move to key store' removes the api_key line. Other lines and comments are left as they are."
)
# Where a limit comes from (ADR-0071 decision 5)
LIMIT_SOURCE_LABEL = {"default": "default", "user_config": "settings file", "cli": "command option"}
LIMITS_CHANGE_HINT = "You can change them in the settings window or the settings file."
# Added when the compare window (pywebview) shows that a pair was cut off at the limit (ADR-0167 decision 6). Not used by the CLI, wx, or the batch compare window
LIMITS_SCREEN_HINT = "You can change the limits on the 'Compare and automation' tab of the settings window."
# Heading of the settings file box in wx (the pywebview settings window uses CONFIG_PANE_WEB. ADR-0167 decision 5)
CONFIG_PANE = "Settings file (config.toml): AI analysis limits and extra masked words"
# Heading of the "Settings file" tab in the pywebview settings window, and the note that the limit fields moved (ADR-0167 decision 5)
CONFIG_PANE_WEB = "Settings file (config.toml): extra masked words"
SETTINGS_NOTE_LIMITS_MOVED = "The limits on differences sent to the AI are on the 'Compare and automation' tab (saved to the same settings file)."
SETTINGS_AUTO_COMPARE = "Start comparing automatically once both files are ready"
# Auto-send (ADR-0080 decision 2. pywebview settings window only)
SETTINGS_AUTO_SEND_LLM = "Send to the AI automatically after comparing"
# Tabs of the pywebview settings window (ADR-0154 / REQ-214 section 5). "AI profile" and "Prompt" are PROFILES_TITLE and PROMPT_TEMPLATE_TITLE
SETTINGS_TAB_AUTO = "Compare and automation"
SETTINGS_TAB_FILE = "Settings file"
SETTINGS_NOTE_AUTO_SEND = "When on, the differences are sent to the AI profile in use every time a comparison finishes, without asking."
SETTINGS_NOTE_SAVE_BOTH = "'Save' writes the fields on both the 'Compare and automation' and 'Settings file' tabs."
# Checkbox and note for "Show the character (Mirarin)" (ADR-0174 decision 1. [gui] character)
SETTINGS_CHARACTER = "Show the character (Mirarin)"
SETTINGS_NOTE_CHARACTER = (
    "Mirarin's picture appears at startup, while the AI is thinking, while a batch compare is running, when there are no differences, and so on. "
    "The change takes effect the next time you open the window."
)
AUTO_SEND_CONFIRM_TITLE = "Send to the AI automatically after comparing"


def auto_send_confirm(name: str, destination: str) -> str:
    """Confirmation when turning auto-send from off to on (ADR-0080 decision 2). The default button is No. No key value."""
    return "\n".join(
        [
            "Send to the AI automatically after comparing?",
            f"Connection name: {name}",
            f"Destination: {destination}",
            "Every time a comparison finishes in the compare window, the differences are sent without asking.",
            "Masking and the send limits stay the same. You can see the full text that was sent in the AI analysis box.",
            "For a batch compare, a list of what will be sent is shown after the run. Nothing is sent until you click 'Send'. The command never sends.",
        ]
    )


def auto_send_unusable(reason: str) -> str:
    """The connection in use cannot be used (for example, no key). Auto-send is not turned on and nothing is written."""
    return f"Auto-send cannot be turned on because the connection in use is unavailable ({reason}). Not saved."

# Heading and labels for the limits in the wx settings window. Also used by the input error message (limit_invalid) and the current-value line (limit_now_line)
LIMITS_TITLE = "AI analysis limits"
LIMIT_PER_JOB = "Per pair"
LIMIT_TOTAL = "Total"
# Heading and labels for the limits in the pywebview settings window ("Compare and automation" tab. ADR-0167 decision 5). Numbers come from the default constants
LIMITS_TITLE_WEB = "Limits on differences sent to the AI"
LIMIT_PER_JOB_WEB = f"Per pair (default {DEFAULT_MAX_DIFFS:,})"
LIMIT_TOTAL_WEB = f"Total (default {DEFAULT_MAX_TOTAL_DIFFS:,})"
SECRET_WORDS_TITLE = "Extra masked words"
# Text under the box in the pywebview settings window (also writes the compare window lines. REQ-113 section 5. The AI instruction text moved to another box in REQ-116).
# wx keeps CONFIG_SAVE_NOTE_WX
CONFIG_SAVE_NOTE = (
    "Only when you click 'Save', the limits, masked words, and compare window lines in the settings file are rewritten "
    "(other lines and comments are left as they are)."
)
CONFIG_SAVE_NOTE_WX = "Only when you click 'Save', the limit and masked-word lines in the settings file are rewritten (other lines and comments are left as they are)."
CONFIG_NOT_CHANGED = "No changes. The settings file was not written."
CONFIG_CHANGED_ELSEWHERE = "The settings file was changed elsewhere. Reload it, then save."
CONFIG_SAVE_TITLE = "Settings file"
MIGRATE_TITLE = "Move to key store"


def template_created(path: Path) -> str:
    """Guidance after "Create template" (the pywebview window says "Create settings file")."""
    return f"Created {path}. {CONFIG_TEMPLATE_NOTE}"


def llm_limit_lines(max_diffs: int, max_diffs_source: str, max_total_diffs: int, max_total_diffs_source: str) -> list[str]:
    """Limits and their sources shown in the confirmation before a batch compare sends. If the sources differ, each gets its own parentheses."""
    per_job, total = LIMIT_SOURCE_LABEL[max_diffs_source], LIMIT_SOURCE_LABEL[max_total_diffs_source]
    if per_job == total:
        line = f"Limits: {max_diffs:,} per pair, {max_total_diffs:,} total ({per_job})"
    else:
        line = f"Limits: {max_diffs:,} per pair ({per_job}), {max_total_diffs:,} total ({total})"
    return [line, LIMITS_CHANGE_HINT]


def limit_now_line(label: str, value: int, source: str) -> str:
    """Current value and its source, shown faintly under a limit field in the settings window."""
    return f"{label}: currently {value:,} ({LIMIT_SOURCE_LABEL[source]})"


def workdir_limits_ignored(path: Path, names: "list[str] | tuple[str, ...]") -> str:
    """One line when driftpilot.toml in the working folder has limits or a prompt (not read. Same text for the CLI and the window).

    The prompt (prompt_template) gets its own wording (REQ-113 section 5). If both are present, the two are joined into one line.
    """
    limits = [name for name in names if name != "prompt_template"]
    parts = []
    if limits:
        items = ", ".join(f"llm.{name}" for name in limits)
        parts.append(f"{items} in {path} is not read. Change the limits in the per-user settings file (config.toml) or with a command option")
    if "prompt_template" in names:
        parts.append(
            f"The prompt in the working folder is not used (llm.prompt_template in {path}). "
            "Change the prompt in the 'Prompt' box of the settings window"
        )
    return "Note: " + ". ".join(parts)


def limit_invalid(label: str) -> str:
    return f"The AI analysis limit ({label}) must be an integer of 1 or more. Not saved."


def secret_word_has_space(lines: "list[int]") -> str:
    numbers = ", ".join(str(n) for n in lines)
    return f"The masked words contain a space on {_plural(len(lines), 'line', 'lines')} {numbers}. Write one word per line. Not saved."


def config_saved(path: Path) -> str:
    return f"Saved to {path}."


def config_not_saved(error: str) -> str:
    return f"Could not save: {error}"


def secret_words_in_effect(summary: str) -> str:
    """Words currently in effect (including words from the working folder), shown under the masked words field in the settings window."""
    return f"Words in effect: {summary}"


# --- Prompt templates for the AI (REQ-109 / ADR-0075). No sentence includes the template contents (only place, item name, and reason) ---

PROMPT_TEMPLATE_TITLE = "Prompt"
# Explanation under the field in the settings window (ADR-0167 decisions 1-3). One bold rule, a table of "Available marks" (collapsed by default), and two bullet notes
PROMPT_DIFF_ONCE_NOTE = "Write {{diff}} (the body of the differences) exactly once."
PROMPT_MARKS_TITLE = "Available marks"
PROMPT_MARKS_COL_MARK = "Mark"
PROMPT_MARKS_COL_MEANING = "Meaning"
# Meaning of each mark (table rows. Order follows PROMPT_MARKS. A test checks that the keys match PROMPT_MARKS)
PROMPT_MARK_MEANINGS = {
    "diff": "The body of the differences. Write it exactly once.",
    "diff_format": "A description of the difference format (structured comparison differences, line differences, and so on).",
    "diff_total": "The total number of differences.",
    "sent_range": "The number sent from the start (up to the limit if cut off).",
    "max_diffs": "The limit on differences sent per pair.",
    "notes": "Notice about cut-off and lines not sent (empty if none).",
}
PROMPT_NOTE_PLAIN = "The text in this field is sent to the AI without masking. Do not write device settings or passwords."
PROMPT_NOTE_PREMISE = "If you delete the built-in premise text, the AI may misread <secret:N> or cut-off notices."


def _mark(name: str) -> str:
    """How to write a mark ({{name}})."""
    return "{{" + name + "}}"


# Phrases for why a template cannot be used (by kind from llm_analyzer.prompt.check_prompt_template). Each is a full predicate that follows the item name or heading.
# unknown_mark points to the "Available marks" table in the settings window, and the list of mark names is built from PROMPT_MARKS (ADR-0167 decision 4)
PROMPT_TEMPLATE_PROBLEM = {
    "type": "must be a string",
    "too_long": "must be 20,000 characters or fewer",
    "unknown_mark": (
        f"contains a mark that cannot be used. The available marks are in the '{PROMPT_MARKS_TITLE}' table under the field "
        f"({', '.join(_mark(name) for name in PROMPT_MARKS)})"
    ),
    "no_diff": "has no {{diff}} mark for inserting the differences",
    "repeated_diff": "must use the {{diff}} mark for inserting the differences only once",
}


def prompt_template_invalid(kind: str) -> str:
    """The text in the prompt field cannot be used (settings window). Not saved."""
    return f"{PROMPT_TEMPLATE_TITLE} {PROMPT_TEMPLATE_PROBLEM[kind]}. Not saved."


# --- Prompt profiles for the AI (REQ-116 / ADR-0079). No sentence includes the prompt body (only name and reason) ---

PROMPT_FIELD_TEMPLATE = "Body"
# Second line of a list row (the three built-in ones by id, user-made ones share one text)
PROMPT_ROW_DETAIL = {
    "builtin-default": "Key points and impact of the differences (show comparisons are sent with 'Check status'). Built in.",
    "builtin-incident": "Clues for investigating an incident. Built in.",
    "builtin-status": "Before and after checks of show output. Built in.",
}
PROMPT_ROW_USER = "A prompt you created"
# Shown faintly under the field when one of the three built-in prompts is selected
PROMPT_BUILTIN_NOTE = "Built-in prompts cannot be changed. To change one, 'Duplicate' it first, then edit the copy."
PROMPT_BUILTIN_READ_ONLY = "Built-in prompts cannot be saved or deleted. To change one, 'Duplicate' it first, then edit the copy."
PROMPT_DISCARD_CHANGES = "You have unsaved changes. Discard them and select another prompt?"


def prompt_profile_saved(name: str) -> str:
    return f'Saved the prompt "{name}".'


def prompt_profile_added(name: str) -> str:
    return f'Created the prompt "{name}". Edit the name and body, then click "Save".'


def prompt_profile_duplicated(name: str) -> str:
    return f'Created the prompt "{name}".'


def prompt_profile_deleted(name: str) -> str:
    return f'Deleted the prompt "{name}".'


def prompt_profile_now_in_use(name: str) -> str:
    return f'Now using the prompt "{name}".'


# --- Premise added every time (ADR-0172 decision 6 / REQ-234 section 5.3). No sentence includes the premise contents ---

SETTINGS_PREMISE_TITLE = "Premise added every time"
SETTINGS_PREMISE_SAVE = "Save premise"
# Character count display (JS only fills in the numbers. Python decides)
SETTINGS_PREMISE_COUNT = "{count} / {max} characters"
# Notes under the field (3 lines. The view puts each into its own li)
SETTINGS_PREMISE_NOTES = (
    "It is added every time to the text sent to the AI, whichever prompt is used (compare, batch of comparisons, follow-up questions, batch compare, CLI).",
    "It is sent without masking. If you write a real host name or IP, it is sent to the AI. Do not write passwords or keys.",
    "Up to 4,000 characters. If left empty, nothing is added. It appears in the full text shown before sending.",
)
# Refusal and result of saving
PREMISE_SECRET_REFUSED = "Not saved because the premise contains something to be masked or a line that cannot be sent (the line is not shown. This is the same check as for follow-up questions)."
PREMISE_TOO_LONG = "The premise can be up to 4,000 characters. Not saved."
PREMISE_SAVED = "Saved the premise."
# Confirmation when you click "Close" with unsaved changes in the premise field (buttons are SETTINGS_BUTTON_CLOSE and DIALOG_CANCEL)
PREMISE_CLOSE_CONFIRM = "The premise added every time has unsaved changes. Close without saving?"


def premise_unreadable(error: str) -> str:
    """Band and refusal text when the premise file cannot be read. error is the SettingsError text (only the place and kind of reason)."""
    return f"Cannot read the premise file. {error}"


def premise_settings_unreadable(error: str) -> str:
    """Text when the premise is not saved because the settings file cannot be read and the masked words are unknown (never write unchecked)."""
    return f"Cannot check against the masked words because the settings cannot be read. The premise was not saved. {error}"


def prompt_profile_delete_confirm(name: str) -> str:
    return f'Delete the prompt "{name}"?'


def prompt_profile_delete_in_use() -> str:
    """A user prompt that is in use cannot be deleted (refused. Decision record 3)."""
    return "The prompt in use cannot be deleted. Select another prompt first and click 'Use this profile'."


def prompt_name_taken(name: str) -> str:
    return f'A prompt named "{name}" already exists (a built-in name cannot be reused either. Case is ignored). Not saved.'


def new_prompt_name(n: int) -> str:
    """Name for a new prompt. Gets a number so names do not collide (1 has no number)."""
    return "New prompt" if n == 1 else f"New prompt {n}"


def prompt_profiles_unreadable(error: str) -> str:
    """Prompt profile file cannot be read (shown in the settings window box)."""
    return f"Cannot read the prompt profiles: {error}"


def prompt_profile_not_found(name: str, names: list[str]) -> str:
    """The --llm-prompt-profile name does not exist (nothing is sent). Lists names only."""
    listed = ", ".join(f'"{n}"' for n in names)
    return f'There is no prompt profile named "{name}" for --llm-prompt-profile (nothing was sent). Available prompt profiles: {listed}'


def prompt_profile_name_shadowed(name: str) -> str:
    """One line when the --llm-prompt-profile name matches both a built-in and a user prompt."""
    return (
        f'Note: --llm-prompt-profile "{name}" is the name of both a built-in prompt and one you created. '
        "The built-in one is used. To use yours, rename it in the settings window"
    )


def user_prompt_template_ignored(path: Path) -> str:
    """One line when the per-user settings file has llm.prompt_template (it is not read)."""
    return f'Note: llm.prompt_template in {path} is ignored. Change prompts in the "Prompt" box of the settings window'


def workdir_secret_words_line(words: "list[str] | tuple[str, ...]") -> str:
    """Masked words from driftpilot.toml in the working folder (shown read-only)."""
    listed = ", ".join(words) if words else "none"
    return f"Words in driftpilot.toml in the working folder (cannot be changed here): {listed}"


def migrate_confirm(store: str) -> str:
    return (
        f"The API key in the settings file will be saved to DriftPilot/LLM/default/APIKey in {store}. "
        "After confirming it was saved, the api_key line in the settings file will be removed. Continue?"
    )


def migrate_overwrite_confirm(store: str) -> str:
    return f"A different API key is already saved in {store}. Overwrite it with the key from the settings file?"


def migrate_done(store: str) -> str:
    return f"Moved the API key to {store} and removed the api_key line from the settings file"


def migrate_store_unusable(reason: str) -> str:
    return f'The key store is not available, so "Move to key store" cannot be used ({reason})'


def migrate_save_failed(reason: str) -> str:
    return f"Could not save to the key store ({reason}). The settings file was not changed"


def migrate_mismatch() -> str:
    return "The key read back from the key store did not match the key in the settings file. The settings file was not changed"


def migrate_file_changed() -> str:
    return f"{CONFIG_CHANGED_ELSEWHERE}. The api_key line in the settings file was not removed"


def migrate_remove_failed(error: str) -> str:
    return f"The key was saved to the key store, but the api_key line in the settings file could not be removed: {error}"


def migrate_no_key() -> str:
    return "There is no API key in the settings file. The settings file was not changed"


def migrate_overwrite_declined() -> str:
    return "Not overwritten. The key store and the settings file were not changed"


SETTINGS_TITLE = "DriftPilot — Settings"
STATUS_READY = "AI analysis is available"
# Messages for what is missing. Shared by the top-right status mark and the openings of the connection test no_model / no_key / unavailable
STATUS_NO_MODEL = "No model is set"
STATUS_NO_KEY = "No API key"
NO_LLM_EXTRA = "The AI components are not installed"
LLM_EXTRA_INSTALL = "pip install 'driftpilot[llm]'"
STATUS_NO_LLM_EXTRA = f"{NO_LLM_EXTRA} ({LLM_EXTRA_INSTALL})"
STATUS_UNREADABLE = "Cannot read the settings"
# Top-right mark when a placeholder such as <resource-name> is left in the endpoint. Same as the opening of the connection test message
STATUS_PLACEHOLDER = "Replace <resource-name> in the Base URL"
KEY_STATE_NONE = "None"
KEY_STATE_IN_USE = "In use"
KEY_STATE_UNUSED = "Present (not used)"
KEY_NOT_SET = "Not set"
READ_ONLY_NOTE = "Write one word per line. Words containing spaces are not allowed"


def key_state_unusable(reason: str) -> str:
    return f"Unusable ({reason})"


def key_summary(display: str) -> str:
    return f"Saved {display}"


def key_source_line(number: int, label: str, state: str) -> str:
    return f"{number} {label} — {state}"


def key_store_note(store: str, service: str) -> str:
    return f"Stored in: {store}, name {service}. The input box is cleared after saving or deleting"


def setx_command(env_name: str) -> str:
    """Command that adds an environment variable on Windows. Kept on its own line in the setup help."""
    return f"setx {env_name} \"<key>\""


def key_help_command_lines(env_name: str | None, platform: str) -> list[str]:
    """Command-only lines of key_help_lines (not wrapped in the window).

    None if env_name is None (a profile that uses no environment variable).
    """
    return [] if env_name is None or platform == "darwin" else [setx_command(env_name)]


def key_help_lines(env_name: str | None, platform: str) -> list[str]:
    """How to set the key (always shown in the settings window). Key store first, environment variable as a note.

    Environment variable steps are shown only for the current OS (both if neither Windows nor Mac).
    The Windows command is on its own line, separate from the explanation.
    env_name is the environment variable name of the selected profile. If None, no environment variable steps.
    """
    if env_name is None:
        return ["How to set it up:", '1. Paste the key into the box above and click "Save to key store"']
    windows = [
        "Windows: Add it to your user environment variables in System Properties > Environment Variables.\n"
        "In PowerShell, run this one line (it takes effect in windows opened afterward)",
        setx_command(env_name),
    ]
    mac = [f"Mac: Add export {env_name}=\"<key>\" to your shell config file (such as ~/.zshrc)"]
    if platform.startswith("win"):
        steps = windows
    elif platform == "darwin":
        steps = mac
    else:
        steps = [*windows, *mac]
    return [
        "How to set it up:",
        '1. Paste the key into the box above and click "Save to key store"',
        f"2. To use it from commands or automated runs, put it in the environment variable {env_name} (it takes priority over the key store)",
        *steps,
    ]


# --- AI connection profiles. List and editing in the settings window. No key value goes into any message ---

PROFILES_TITLE = "AI profiles"
PROFILE_UNSAVED_DEFAULT = "Default (not saved yet)"
PROFILE_MARK_IN_USE = "In use"
PROFILE_MARK_NO_KEY = "No key set"
PROFILE_ALREADY_IN_USE = "Already in use"
PROFILE_EDIT_TITLE = "Selected profile"
PROFILE_KEY_BOX = "API key for this profile"
KEY_SOURCES_TITLE = "Sources (higher is preferred)"  # heading of the key box
FINGERPRINTS_TITLE = "Tables currently in effect (use for bug reports)"  # heading of "Diagnostics"
PROFILE_DISCARD_CHANGES = "There are unsaved changes. Discard them and select another profile?"
PROFILE_SAVE_FIRST = 'There are unsaved changes. Click "Save" first'
# Button labels, field names, and notice titles of the settings window. The pywebview window takes its text from here, not from HTML.
# The text is the same as in the wx settings window (gui/settings_frame.py); a test checks they match
SETTINGS_BUTTON_MIGRATE = "Move to key store"
SETTINGS_BUTTON_ADD = "Add"  # button of the wx window. The pywebview window uses SETTINGS_BUTTON_NEW
SETTINGS_BUTTON_NEW = "New"
SETTINGS_BUTTON_DUPLICATE = "Duplicate"
SETTINGS_BUTTON_DELETE = "Delete…"
SETTINGS_BUTTON_SAVE = "Save"
SETTINGS_BUTTON_USE = "Use this profile"
SETTINGS_BUTTON_SAVE_KEY = "Save to key store"
SETTINGS_BUTTON_OPEN_FOLDER = "Open folder"
SETTINGS_BUTTON_TEMPLATE = "Create settings file"  # pywebview window only. The wx window keeps its own template label
SETTINGS_BUTTON_CLOSE = "Close"
SETTINGS_FIELD_NAME = "Name"
SETTINGS_FIELD_API_FORMAT = "API format"
SETTINGS_FIELD_MODEL = "Model"
SETTINGS_FIELD_BASE_URL = "Base URL"
SETTINGS_API_KEY_TITLE = "API key"
SETTINGS_API_KEY_DELETE_TITLE = "Delete API key"
SETTINGS_TEMPLATE_FAILED_TITLE = "Cannot create the template"
SETTINGS_TEMPLATE_CREATED_TITLE = "Template created"
SETTINGS_DIAGNOSTICS = "Diagnostics"
SETTINGS_VERSIONS_TITLE = "Versions"
SETTINGS_CONFIG_FILE_TITLE = "Settings file"
SETTINGS_CONFIG_LOCATION = "Location"
# Buttons of confirmation and notice dialogs (same as the Yes / No of the wx MessageDialog and the OK of MessageBox)
DIALOG_YES = "Yes"
DIALOG_NO = "No"
DIALOG_OK = "OK"


def fingerprint_line(count: int, digest: str) -> str:
    """One row value (count and fingerprint) of the fingerprint table in Diagnostics."""
    return f"{count} {_plural(count, 'item', 'items')}  fingerprint {digest}"
# State of the first source line for a profile with no environment variable name (shown as "Environment variable — Not used")
KEY_STATE_ENV_NOT_USED = "Not used"
# llm.api_key in the settings file is read only for the profile whose id is default
KEY_STATE_NOT_FOR_PROFILE = "Present (not used by this profile)"
# Display names of the API formats. The list is short; the edit choices add the formal name
API_FORMAT_SHORT = {
    "chat-completions": "OpenAI compatible",
    "responses": "OpenAI Responses",
    "anthropic-messages": "Claude",
    "custom-http-json": "Custom HTTP JSON",
    "codex-cli": "Codex CLI",
    "claude-code-cli": "Claude Code CLI",
}
API_FORMAT_CHOICE = {
    "chat-completions": "OpenAI compatible",
    "responses": "OpenAI Responses",
    "anthropic-messages": "Claude (Anthropic Messages)",
    "custom-http-json": "Custom HTTP JSON",
    "codex-cli": "Codex CLI",
    "claude-code-cli": "Claude Code CLI",
}
# Detailed request settings (pywebview settings window only). Field names, notes, choices
SETTINGS_FIELD_TEMPLATE = "Type"  # choice of profile template (not to be confused with the settings file template)
SETTINGS_FIELD_AUTH = "Authentication"
SETTINGS_WIRE_PANE = "Detailed request settings (limit, Thinking)"  # read only by the pywebview settings window
SETTINGS_FIELD_MAX_TOKENS = "Max tokens"
SETTINGS_FIELD_MAX_TOKENS_NAME = "Max tokens name"
SETTINGS_NOTE_MAX_TOKENS_NAME = "If empty, max_completion_tokens (max_output_tokens for Responses)"
SETTINGS_FIELD_THINKING = "thinking"
SETTINGS_FIELD_THINKING_NAME = "Name to send"
SETTINGS_FIELD_THINKING_ON = "ON value (JSON)"
SETTINGS_FIELD_THINKING_OFF = "OFF value (JSON)"
SETTINGS_NOTE_THINKING = "If the name to send is empty, thinking is not sent"
SETTINGS_FIELD_QUERY = "Query parameters"
SETTINGS_NOTE_QUERY = "Write one key=value per line. They are appended to the Base URL when sending"
AUTH_HEADER_CHOICE = {"bearer": "Authorization: Bearer", "api-key": "api-key (Azure OpenAI)"}
THINKING_CHOICE = {"off": "OFF", "on": "ON"}
# Display names of the profile templates (values and order follow PROFILE_TEMPLATES in screen_model/settings_model.py)
PROFILE_TEMPLATE_LABEL = {
    "openai": "OpenAI",
    "openai-responses": "OpenAI Responses",
    "azure-openai": "Azure OpenAI",
    "anthropic": "Claude (Anthropic)",
    "qwen": "Qwen",
    "deepseek": "DeepSeek",
    "local": "Local AI",
    "codex-cli": "Codex CLI",
    "claude-code-cli": "Claude Code CLI",
    "custom": "Custom API",
}
# Names that say which field is at fault when request values cannot be saved (no value is included)
_WIRE_FIELD_LABEL = {
    "wire_names": SETTINGS_FIELD_MAX_TOKENS_NAME,
    "thinking_param": f"thinking: {SETTINGS_FIELD_THINKING_NAME}",
    "query": f"{SETTINGS_FIELD_QUERY} (keys)",
}


def wire_max_tokens_invalid() -> str:
    return f"{SETTINGS_FIELD_MAX_TOKENS} must be an integer of 1 or more (leave empty to not send). Not saved"


def wire_json_invalid(label: str) -> str:
    """The thinking ON value or OFF value cannot be read as JSON. label is the field name."""
    return f'The thinking "{label}" cannot be read as JSON. Not saved'


def wire_reserved(field: str) -> str:
    """A reserved name (model, messages, etc.) was used. field is the field name from wire_problem."""
    return f"A reserved name (such as model or messages) cannot be used in {_WIRE_FIELD_LABEL[field]}. Not saved"


def wire_keylike(field: str) -> str:
    """A key-like name was used. field is the field name from wire_problem."""
    return f"A key-like name cannot be used in {_WIRE_FIELD_LABEL[field]}. Save keys in the key store. Not saved"


def query_line_invalid(number: int) -> str:
    return f"Line {number} of {SETTINGS_FIELD_QUERY} is not in the form key=value. Not saved"


def query_key_empty(number: int) -> str:
    return f"The key on line {number} of {SETTINGS_FIELD_QUERY} is empty. Not saved"


def query_key_repeated(number: int) -> str:
    return f"The key on line {number} of {SETTINGS_FIELD_QUERY} is the same as in an earlier line. Not saved"


def base_url_has_extras() -> str:
    return f"{SETTINGS_FIELD_BASE_URL} cannot contain ? or #. Write them in the {SETTINGS_FIELD_QUERY} field. Not saved"


# Fields for custom-http-json definitions (pywebview settings window only). Field names, notes, choices, messages. No values.
SETTINGS_FIELD_CUSTOM_URL = "Destination URL"
SETTINGS_FIELD_CUSTOM_HEADERS = "Fixed headers"
SETTINGS_FIELD_CUSTOM_BODY = "Request body"
SETTINGS_FIELD_ANSWER_PATH = "Answer path"
SETTINGS_NOTE_CUSTOM_HEADERS = "Write one per line as name: value. Don't write keys here; choose an authentication and save the key in the key store"
SETTINGS_NOTE_CUSTOM_BODY = "Write it as JSON. {{model}}, {{prompt}} and {{max_output_tokens}} inside strings are filled in when sending"
SETTINGS_NOTE_ANSWER_PATH = "Like choices.0.message.content (separate with '.'; numbers are list indexes)"
SETTINGS_SHAPE_TITLE = "Response shape (choose one to put it in Answer path; values are not shown)"
SETTINGS_BUTTON_CUSTOM_EXPORT = "Export definition…"
SETTINGS_BUTTON_CUSTOM_IMPORT = "Import definition…"
CUSTOM_AUTH_CHOICE = {
    "bearer": "Authorization: Bearer",
    "api-key": "api-key",
    "x-api-key": "x-api-key",
    "none": "None (no key sent)",
}
# Type names in the response shape tree (custom_http.SHAPE_KINDS)
SHAPE_TYPE_LABEL = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "null": "null",
    "object": "object",
    "array": "array",
}
CUSTOM_FILE_TITLE = "Custom HTTP JSON definition"
CUSTOM_IMPORT_CHANGED = "The file changed after it was checked, so it was not imported. Click 'Import definition…' again"
CUSTOM_FORMAT_REQUIRED = "Set the API format to 'Custom HTTP JSON' and save before using this"
_NOT_SAVED = "Not saved"
# Messages when a definition field breaks the rules (field and kind from custom_http.custom_problem).
# For fixed headers, the text is a fragment that follows the field name and line number.
_CUSTOM_PROBLEM_MESSAGE = {
    ("url", "empty"): f"Enter a {SETTINGS_FIELD_CUSTOM_URL}",
    ("url", "fragment"): f"{SETTINGS_FIELD_CUSTOM_URL} can't contain #",
    ("url", "invalid"): f"{SETTINGS_FIELD_CUSTOM_URL} can't contain spaces or non-ASCII characters",
    ("url", "scheme"): f"{SETTINGS_FIELD_CUSTOM_URL} must use https (http is allowed only for localhost)",
    ("url", "keylike"): f"The query of {SETTINGS_FIELD_CUSTOM_URL} can't contain key-like names. Save keys in the key store",
    ("auth", "choice"): f"The {SETTINGS_FIELD_AUTH} choice is not valid",
    ("headers", "line"): "is not in the name: value form",
    ("headers", "name_empty"): "has an empty name",
    ("headers", "name"): "has a name with characters that can't be used",
    ("headers", "value"): "has a value with a line break or non-ASCII characters",
    ("headers", "repeated"): "has the same name as an earlier line (case is ignored)",
    ("headers", "keylike"): "has a key-like name, which is not allowed. Choose an authentication and save the key in the key store",
    ("headers", "own"): "has a name that DriftPilot sets itself, so it can't be used (Content-Type, etc.)",
    ("body", "json"): f"{SETTINGS_FIELD_CUSTOM_BODY} can't be read as JSON",
    ("body", "object"): f"{SETTINGS_FIELD_CUSTOM_BODY} must be a JSON object (enclosed in {{ }})",
    ("body", "unknown_mark"): (
        f"{SETTINGS_FIELD_CUSTOM_BODY} has an unknown placeholder (only {{{{model}}}}, {{{{prompt}}}} and "
        "{{max_output_tokens}} can be used)"
    ),
    ("body", "no_prompt"): f"{SETTINGS_FIELD_CUSTOM_BODY} doesn't contain {{{{prompt}}}}",
    ("answer_path", "empty"): f"Enter an {SETTINGS_FIELD_ANSWER_PATH}",
    ("answer_path", "shape"): f"{SETTINGS_FIELD_ANSWER_PATH} must look like choices.0.message.content",
}


def custom_field_invalid(field: str, kind: str, line: int | None = None) -> str:
    """Message when a definition field can't be saved (which field and line only; no values)."""
    text = _CUSTOM_PROBLEM_MESSAGE.get((field, kind))
    if field == "headers":
        where = f"{SETTINGS_FIELD_CUSTOM_HEADERS} (line {line})" if line is not None else SETTINGS_FIELD_CUSTOM_HEADERS
        text = where + " " + (text or "has the wrong format")
    return f"{text or 'The definition has the wrong format'}. {_NOT_SAVED}"


def custom_exported(path: Path) -> str:
    return f"Exported the definition (no API key included): {path}"


def custom_export_failed(reason: str) -> str:
    return f"Can't export the definition: {reason}"


def custom_import_confirm(name: str) -> str:
    return f"This replaces the definition of the selected profile '{name}'. Continue?\nThe API key is not imported"


def custom_imported(name: str) -> str:
    return f"Imported the definition for profile '{name}'"


def custom_import_unreadable(reason: str) -> str:
    return f"Can't read the definition file: {reason}. Not imported"


# Export/import profiles as one file (ADR-0082, REQ-119; the row of actions at the bottom of the pywebview settings window).
# API keys are not included. Sentences never contain an API key value (file names and destinations are shown so the user can check).
SETTINGS_BUTTON_EXPORT_PROFILES = "Export profiles…"
SETTINGS_BUTTON_IMPORT_PROFILES = "Import profiles…"
PROFILE_BUNDLE_TITLE = "Export/import profiles"
PROFILE_BUNDLE_NO_KEY = "No API key"
PROFILE_BUNDLE_DEFAULT_URL = "Default destination for the format"
PROFILE_BUNDLE_LOCAL_CLI = "Local CLI"
PROFILE_BUNDLE_CHANGED = (
    "The file or the current profiles changed after they were checked, so nothing was imported. Click 'Import profiles…' again"
)


def imported_profile_name(name: str, n: int) -> str:
    """Name for an imported profile whose name is already taken (1 means no number)."""
    return f"{name} (imported)" if n == 1 else f"{name} (imported {n})"


def profile_bundle_export_confirm(connections: int, prompts: int) -> str:
    return (
        f"This exports the saved AI profiles ({connections}) and the prompts you created ({prompts}).\n"
        "API keys are not included. Continue?"
    )


def profile_bundle_exported(path: Path, connections: int, prompts: int) -> str:
    return f"Exported AI profiles ({connections}) and prompts ({prompts}) (no API key included): {path}"


def profile_bundle_export_failed(reason: str) -> str:
    return f"Can't export the profiles: {reason}. Not exported"


def profile_bundle_connection_line(name: str, format_label: str, destination: str) -> str:
    """One connection line in the import confirmation list."""
    return f"- {name} ({format_label}): {destination} — {PROFILE_BUNDLE_NO_KEY}"


def profile_bundle_import_confirm(connection_lines: list[str], prompt_names: list[str]) -> str:
    """Import confirmation: connections as a list of names and destinations, prompts as a list of names."""
    lines = [
        "These profiles will be imported. Existing profiles are not changed.",
        "Imported connections have no API key. Save one in the key store afterward.",
        "",
        f"AI profiles ({len(connection_lines)}):",
        *(connection_lines or ["(none)"]),
        "",
        f"Prompts ({len(prompt_names)}):",
        *([f"- {name}" for name in prompt_names] or ["(none)"]),
        "",
        "Continue?",
    ]
    return "\n".join(lines)


def profile_bundle_imported(connections: int, prompts: int) -> str:
    return (
        f"Imported AI profiles ({connections}) and prompts ({prompts}). "
        "Imported connections have no API key. Save one in the key store before using them."
    )


def profile_bundle_partly_imported(connections: int, reason: str) -> str:
    """When the prompts could not be written after the connections were added."""
    return f"Imported only the AI profiles ({connections}). Can't import the prompts: {reason}"


def profile_bundle_unreadable(reason: str) -> str:
    return f"Can't import the profile file: {reason}. Nothing was imported"


# Formats that call a local CLI (codex-cli, claude-code-cli; REQ-102 section 4; pywebview settings window only). Field names, notes, messages.
SETTINGS_FIELD_CLI_PATH = "CLI location"
SETTINGS_BUTTON_CHOOSE_CLI = "Choose…"
SETTINGS_NOTE_CLI_PATH = "If empty, looks for codex on PATH (claude for Claude Code CLI)"
SETTINGS_FIELD_TIMEOUT = "Time limit (seconds)"
SETTINGS_NOTE_CLI_LOGIN = "This format uses the CLI's login. No key is used"
# Codex CLI does not read the user's ~/.codex/config.toml (--ignore-user-config; decision record 5). Shown in the pre-send confirmation and the settings window.
CODEX_CONFIG_NOTE = "Your config.toml is not read. The destination is the default of the Codex login"
_CLI_PROGRAM = {"codex-cli": "codex", "claude-code-cli": "claude"}


# Per-profile environment variable name field (ADR-0077; inside the "API key" box of the pywebview settings window) and messages
SETTINGS_FIELD_ENV_SUFFIX = "Environment variable name"


def env_suffix_invalid() -> str:
    return (
        f"{SETTINGS_FIELD_ENV_SUFFIX} must be 1 to 40 characters of uppercase letters, digits and _ (if empty, no environment variable is used). "
        "Not saved"
    )


def env_suffix_taken(name: str) -> str:
    """When another profile already uses the same environment variable name (no key value)."""
    return f"This {SETTINGS_FIELD_ENV_SUFFIX.lower()} is used by profile '{name}'. Not saved"


def cli_timeout_invalid() -> str:
    return f"{SETTINGS_FIELD_TIMEOUT} must be an integer of 1 or more (if empty, 60). Not saved"


# Thinking depth field for the two CLI formats (cli_effort; ADR-0157 decisions 10 to 12; pywebview settings window only)
SETTINGS_FIELD_CLI_EFFORT = "Thinking (depth)"
SETTINGS_CLI_EFFORT_DEFAULT = "Default (no setting passed)"
SETTINGS_NOTE_THINKING_COST = (
    "Higher values increase the time to an answer and the usage quota (cost). CLI options change between versions. "
    "If it doesn't work, set it back to 'Default'"
)


def cli_effort_invalid() -> str:
    """When the Thinking depth is not a value this format can use (no value shown)."""
    return f"{SETTINGS_FIELD_CLI_EFFORT} must be a value this format can use (or 'Default' for non-CLI formats). Not saved"


def cli_destination(api_format: str, cli_path: str | None) -> str:
    """Display of the destination for CLI formats (the 'Destination' line of the pre-send confirmation; set when settings are loaded).

    Looks like 'Codex CLI (codex on PATH. Your config.toml is not read. ...)' or 'Claude Code CLI (/opt/.../claude)'.
    """
    where = cli_path or f"{_CLI_PROGRAM.get(api_format, api_format)} on PATH"
    if api_format == "codex-cli":
        where = f"{where}. {CODEX_CONFIG_NOTE}"
    return f"{API_FORMAT_SHORT.get(api_format, api_format)} ({where})"


def profile_row_line(name: str, format_label: str, *, in_use: bool, has_key: bool) -> str:
    """One list row ('▶ Work (Claude) · In use'). A row in use starts with ▶ (same mark as the key source row)."""
    marks = ([PROFILE_MARK_IN_USE] if in_use else []) + ([] if has_key else [PROFILE_MARK_NO_KEY])
    head = "▶ " if in_use else "   "
    return f"{head}{name} ({format_label})" + "".join(f" · {m}" for m in marks)


def profile_row_title(name: str, *, in_use: bool) -> str:
    """First line of one list item (REQ-53 section 3). Starts with ▶ if in use (same mark as profile_row_line)."""
    return ("▶ " if in_use else "   ") + name


def profile_row_detail(format_label: str, *, in_use: bool, has_key: bool) -> str:
    """Second line of one list item ('OpenAI compatible · In use · No key'). Only applicable marks are added."""
    marks = ([PROFILE_MARK_IN_USE] if in_use else []) + ([] if has_key else [PROFILE_MARK_NO_KEY])
    return " · ".join([format_label, *marks])


def base_url_default_hint(url: str) -> str:
    """Faint hint shown in the empty destination field."""
    return f"Default ({url})"


def profile_name_empty() -> str:
    return "Enter a name. Not saved"


def profile_name_taken(name: str) -> str:
    return f"A profile named '{name}' already exists (case is ignored). Not saved"


def profile_saved(name: str) -> str:
    return f"Saved profile '{name}'"


# Added when 'Save as new profile' did not copy the original's environment variable name (REQ-199 section 4)
ENV_SUFFIX_NOT_COPIED = "The environment variable name was not copied. Choose another name if you need one"


def profile_added(name: str) -> str:
    return f"Added profile '{name}'. Enter a name, format and model, then click 'Save'"


def profile_duplicated(name: str) -> str:
    return f"Created profile '{name}'. The API key was not copied"


def profile_deleted(name: str) -> str:
    return f"Deleted profile '{name}'"


def profile_now_in_use(name: str) -> str:
    return f"Now using '{name}'"


def profile_delete_confirm(name: str, store: str) -> str:
    return f"Delete profile '{name}'?\nThe API key of this profile saved in {store} is also deleted"


# The three buttons of the overwrite confirmation (REQ-112; pywebview settings window only). Cancel is DIALOG_CANCEL
PROFILE_SAVE_AS_NEW = "Save as new profile"
PROFILE_OVERWRITE = "Overwrite"


def profile_overwrite_confirm(name: str) -> str:
    """Confirmation for a save that changes name, format or destination, and for 'New'/'Duplicate' with unsaved changes. name is the saved name."""
    return f"Overwrite '{name}'?"


def api_key_replace_confirm(name: str, tail: str | None) -> str:
    """Confirmation when 'Save to key store' finds the connection already has a key. tail is the last 4 characters of the key (None for short keys, not shown)."""
    if tail is None:
        return f"Replace the key of '{name}'?"
    return f"Replace the key of '{name}' (ends with {tail})?"


def profile_delete_in_use() -> str:
    return "The profile in use can't be deleted. Select another profile and click 'Use this profile' first"


def profile_delete_last() -> str:
    return "The last profile can't be deleted"


def profile_delete_key_failed(name: str, reason: str) -> str:
    """When the profile was deleted but its key could not be deleted from the key store."""
    return f"Deleted profile '{name}', but the API key could not be deleted from the key store ({reason})"


def profile_unreadable(error: str) -> str:
    return f"Can't read the settings: {error}"


def key_target_unknown(error: str) -> str:
    """When profiles can't be read and the profile for the key can't be decided (the key store is not touched)."""
    return f"The settings can't be read, so the profile for the API key can't be decided. Nothing was saved to or deleted from the key store ({error})"


def new_profile_name(n: int) -> str:
    """Name for "Add"; appends a number so names do not collide (no number for 1)."""
    return "New profile" if n == 1 else f"New profile {n}"


def template_profile_name(label: str, n: int) -> str:
    """Name when added from a template (the template's display name); numbered to avoid collisions."""
    return label if n == 1 else f"{label} {n}"


def copy_profile_name(name: str, n: int) -> str:
    """Name for "Duplicate"; appends a number so names do not collide (no number for 1)."""
    return f"Copy of {name}" if n == 1 else f"Copy of {name} {n}"


def key_profile_note() -> str:
    return f'Saved under a name used only by this profile. If a "{SETTINGS_FIELD_ENV_SUFFIX}" variable exists, it takes priority.'


def status_ready(name: str) -> str:
    """Status mark at the top right (when a profile exists; shows the profile in use)."""
    return f"{STATUS_READY} ({name})"


# --- AI connection test: same text for the settings button and `driftpilot llm-check`. Never includes key values or response bodies ---

LLM_CHECK_TITLE = "AI connection test"
LLM_CHECK_ASK = "Send it? [y/N] "
LLM_CHECK_NOT_A_TERMINAL = "Cannot ask for confirmation, so nothing was sent. Add --yes"
LLM_CHECK_DECLINED = "Not sent"
LLM_CHECK_SENDING = "Sending…"
LLM_CHECK_SEND = "Send"
LLM_CHECK_DONT_SEND = "Don't send"
# When the base URL still has a placeholder such as <resource name> (not sent; same text as the comparison's "not run" reason)
PLACEHOLDER_BASE_URL = f"{STATUS_PLACEHOLDER}. Nothing was sent"


def llm_check_key_source(source: str | None, store: str, env_name: str | None = None) -> str:
    """Name of where the key was read from (never the value). store is the key store name.

    env_name is the environment variable that was read (per-profile variable). If omitted, the shared variable name.
    """
    if source == "env":
        return f"Environment variable {env_name or 'DRIFTPILOT_LLM_API_KEY'}"
    if source == "keyring":
        return store
    if source == "user_config":
        return "Settings file"
    if source == "none":  # custom-http-json with auth none (no key sent)
        return "Not used (no authentication)"
    if source == "cli":  # CLI type (uses the CLI's own login)
        return "Not used (CLI login)"
    return "Not set"


def llm_check_confirm_lines(
    base_url: str | None, model: str, prompt: str, key_source: str, *, default_base_url: str | None = None
) -> list[str]:
    """Lines shown to confirm before sending. key_source is a name from llm_check_key_source.

    default_base_url is the default base URL for each API type (OpenAI's default if omitted).
    """
    if default_base_url is None:
        from driftpilot.llm_analyzer.openai_client import DEFAULT_BASE_URL as default_base_url  # does not load openai

    return [
        "AI connection test: a short text will be sent to the AI with the following content",
        f"Base URL: {base_url or f'default ({default_base_url})'}",
        f"Model: {model}",
        f"Text to send: {prompt}",
        f"Key source: {key_source} (the key value is not shown)",
        "The cost is very small, but charges may apply.",
        "Sent once only, with no automatic retry. The result is not saved.",
    ]


def llm_check_unreadable(error: str) -> str:
    """When the settings cannot be read (nothing is sent)."""
    return f"Cannot read the settings: {error}. Nothing was sent"


def llm_check_failed(error_type: str) -> str:
    """When the window worker raised an unexpected exception. Only the type name is shown, not the message."""
    return f"The connection test could not finish ({error_type})"


LLM_CHECK_CLI_NOT_FOUND = "CLI not found. Check the CLI location field or PATH. Nothing was sent"
LLM_CHECK_CLI_TIMEOUT = "The CLI did not finish within the time limit, so it was stopped"
LLM_CHECK_CLI_ERROR = (
    "The CLI ended with an error or returned an empty answer. Check the model name (for example sonnet for Claude Code) and the CLI login"
)
# Faint example text in the model field for CLI types (settings window only). Not shown for other types
CLI_MODEL_HINT = {
    "claude-code-cli": "Example: sonnet (if empty, the CLI's default model is used)",
    "codex-cli": "Can be left empty (if empty, the CLI's default model is used)",
}


def llm_check_result(result: ConnectionCheckResult, *, config_path: Path, cli: bool = False) -> str:
    """For each kind, what happened and what to do next. config_path is the per-user settings file (hint for no_model).

    cli is whether the type is a CLI type. For CLI not found, timeout, or error, a CLI-specific message is used.
    """
    kind = result.kind
    if kind == "cli_not_found" or (cli and kind == "unavailable"):
        return LLM_CHECK_CLI_NOT_FOUND
    if cli and kind == "connection":
        return LLM_CHECK_CLI_TIMEOUT
    if cli and kind in ("error", "auth", "not_found", "rate_limited"):
        return LLM_CHECK_CLI_ERROR
    if kind == "ok":
        return "Connected. AI analysis can be used with these settings (the response content is not shown)."
    if kind == "auth":
        return "The key was not accepted. Check that the key is correct and that it has permission to use this model."
    if kind == "not_found":
        return "The model or base URL was not found (404). Check llm.model and llm.base_url in the settings file."
    if kind == "rate_limited":
        return "The rate limit or billing limit has been reached (429). Wait a while, or check your plan and billing status."
    if kind == "connection":
        return "Could not connect to the base URL. Check the network and llm.base_url in the settings file."
    if kind == "error":
        code = f" (status code {result.status_code})" if result.status_code is not None else ""
        return f"The base URL returned an error{code}. Wait a while, then try again."
    if kind == "no_key":
        return (
            f"{STATUS_NO_KEY}. Nothing was sent. "
            'Save the key with "Save to key store" in the settings window, or set the profile\'s environment variable name.'
        )
    if kind == "no_model":
        return f"{STATUS_NO_MODEL}. Nothing was sent. Write the model name in llm.model of the settings file ({config_path})."
    if kind == "unavailable":
        return f"{NO_LLM_EXTRA}. Nothing was sent. Install it with {LLM_EXTRA_INSTALL}."
    if kind == "placeholder_base_url":
        return PLACEHOLDER_BASE_URL
    raise ValueError(f"Unknown kind: {kind}")


def llm_check_no_model_profile() -> str:
    """When a profile is in use and has no model (nothing is sent; settings window connection test)."""
    return f'{STATUS_NO_MODEL}. Nothing was sent. Enter a model name in "Model" for this profile, then press "Save".'


def llm_check_outcome_line(result: ConnectionCheckResult, text: str, time: str) -> str:
    """One-line result of the settings window connection test. Success is short, e.g. "Connected (12:41)"; failure adds the time to the reason."""
    return f"Connected ({time})" if result.ok else f"{text} ({time})"


# --- Self-check: text for `driftpilot selfcheck`. No key value appears in any text ---

SELFCHECK_HELP = "Check that the bundled parts and data are all present (no network, no files written)"
SELFCHECK_VERSION = "Version"
SELFCHECK_BUNDLED = "Bundled tables"
SELFCHECK_PATTERNS = "Pattern tables"
SELFCHECK_TEMPLATES = "show templates"
SELFCHECK_METADATA = "Component version info"
SELFCHECK_GUI_WEB = "Window components (web)"
SELFCHECK_LLM = "AI components"
SELFCHECK_KEY_STORE = "Key store"
SELFCHECK_ALL_OK = "All OK"


def selfcheck_not_installed(extra: str) -> str:
    """When the extra is not installed (not treated as a failure)."""
    return f"Not installed (pip install 'driftpilot[{extra}]')"


def selfcheck_line(name: str, ok: bool, detail: str, installed: bool) -> str:
    """One human-readable line: OK, Failed (reason), or Not installed."""
    if not installed:
        return f"{name}: {detail}"
    if ok:
        return f"{name}: OK ({detail})" if detail else f"{name}: OK"
    return f"{name}: Failed ({detail})"


def selfcheck_failed_count(count: int) -> str:
    return f"Failed items: {count}"


def selfcheck_versions(driftpilot: str, python: str) -> str:
    return f"driftpilot {driftpilot}, Python {python}"


def version_line(driftpilot: str) -> str:
    """One line for `driftpilot --version`. The version is the same driftpilot.__version__ as selfcheck."""
    return f"driftpilot {driftpilot}"


def selfcheck_rules(file: str, count: int) -> str:
    return f"{file}: {count} entries"


def selfcheck_unreadable(file: str, error_type: str) -> str:
    """When a bundled file cannot be read or has the wrong shape. Only the exception type name is shown."""
    return f"Cannot read {file}: {error_type}"


def selfcheck_fingerprint(platform: str, digest: str, count: int) -> str:
    return f"{platform} {digest}: {count} entries"


def selfcheck_error(error_type: str) -> str:
    """When an unexpected exception occurred during the check. Only the type name is shown, not the message."""
    return f"Cannot check: {error_type}"


def selfcheck_templates(count: int, folder: str) -> str:
    return f"{count}, {folder}"


def selfcheck_templates_missing(folder: str) -> str:
    return f"Template folder not found: {folder}"


def selfcheck_templates_empty(folder: str) -> str:
    return f"No templates found: {folder}"


def selfcheck_metadata_missing(names: list[str]) -> str:
    return f"Version info missing: {', '.join(names)}"


def selfcheck_import_failed(module: str, error_type: str) -> str:
    """When the extra is installed but cannot be imported (a part missing from the exe bundle)."""
    return f"Cannot import {module}: {error_type}"


def selfcheck_store(store: str, problem: str | None) -> str:
    """Key store name, plus the reason it cannot be used (just the name if it can)."""
    return store if problem is None else f"{store}: {problem}"


# --- Compare window text (shared by the wx gui/app.py and gui/dialogs.py and the pywebview static/compare.html) ---
# Same text as the wx compare window used (moved over, not reworded)

COMPARE_TITLE = "DriftPilot — Compare"
COMPARE_INPUTS = "Inputs"
COMPARE_SEGMENTS = "Select sections"
COMPARE_SEGMENTS_WAITING = " (select one)"  # appended after the section frame heading while waiting for selection
COMPARE_SETTINGS = "Compare settings"
COMPARE_MODE = "Compare mode"
COMPARE_KIND = "Type"
COMPARE_PLATFORM = "Device type"
COMPARE_PLATFORM_HINT = "Example: cisco_ios"
COMPARE_COMMAND = "show command"
COMPARE_COMMAND_HINT = "Example: show ip interface brief"
COMPARE_LLM_CHECK = "AI analysis (you confirm the content before it is sent)"
COMPARE_TAB_STRUCTURED = "Structured compare"
COMPARE_TAB_RAW = "Simple compare"
COMPARE_TAB_UNPARSED = "Lines not structured"
# Text on the disabled tab when a pair is not being compared in structured mode. The first is the decision-maker's wording (parentheses unchanged)
COMPARE_TAB_OFF_SHOW = "Structured compare is available for settings-check commands (show running-config, etc.)"
COMPARE_TAB_OFF_MODE_RAW = 'Structured compare is off because the compare mode is "Simple"'
COMPARE_COL_OP = "Operation"
COMPARE_COL_CATEGORY = "Category"
COMPARE_COL_TARGET = "Target"
COMPARE_COL_ITEM = "Item"
COMPARE_COL_TARGET_ITEM = "Target / item"
COMPARE_COL_BEFORE = "Before"
COMPARE_COL_AFTER = "After"
COMPARE_COL_BEFORE_NO = "Bef."
COMPARE_COL_AFTER_NO = "Aft."
COMPARE_COL_LINE = "Line"
COMPARE_COL_PARENTS = "Parent"
COMPARE_BUTTON_BROWSE = "Browse…"
COMPARE_BUTTON_PASTE = "Paste…"
COMPARE_BUTTON_PREV = "Previous difference"
COMPARE_BUTTON_NEXT = "Next difference"
COMPARE_BUTTON_RUN = "Compare"
COMPARE_BUTTON_SAVE = "Save result…"
COMPARE_BUTTON_CANCEL = "Cancel"
COMPARE_BUTTON_BATCH = "Batch compare…"
COMPARE_BUTTON_SETTINGS = "Settings…"
COMPARE_BUTTON_SWAP = "Swap"  # pywebview compare window only (not in the wx window)
# pywebview compare window only (the wx window keeps a checkbox)
COMPARE_BUTTON_SEND_LLM = "Send to AI"
COMPARE_AUTO_SENDING = "Sending to AI"
# Shown in the AI pane while the pre-send confirmation is being built (REQ-264. The comparison is not redone, so no comparison stage name is shown)
COMPARE_AI_PREPARING = "Preparing what to send to AI"
COMPARE_LLM_PANE = "AI analysis"
# Titles of notices (wx MessageBox)
COMPARE_READ_FAILED_TITLE = "Cannot read"
COMPARE_LLM_SETTINGS_TITLE = "Cannot read AI settings"
COMPARE_FAILED_TITLE = "Compare failed"
COMPARE_SAVE_FAILED_TITLE = "Cannot save"
# Dropping files onto the pywebview compare window. The inputs are left unchanged and this notice is shown
COMPARE_DROP_TITLE = "Cannot drop"
COMPARE_DROP_TOO_MANY = "You can drop one or two files. The inputs were not changed."
COMPARE_DROP_FOLDER = "A folder cannot be used as an input. Drop a file instead. The inputs were not changed."
COMPARE_DROP_NO_PATH = 'The location of the dropped file is unknown. Choose it with "Browse…". The inputs were not changed.'
# Paste dialog and pre-send preview dialog (wx PasteDialog and LlmPreviewDialog)
PASTE_PROMPT = "Paste a config or a terminal log"
LLM_PREVIEW_TITLE = "Confirm what will be sent to AI"
LLM_PREVIEW_PAYLOAD = "Content to send (after masking; only the fixed answer-format rules are added after this):"
LLM_PREVIEW_SEND = "Send"
LLM_PREVIEW_DONT_SEND = "Don't send"
# The confirmation before sending lets the user edit the instructions (REQ-266 / ADR-0193 decision 4). pywebview compare window and AI window only
LLM_PREVIEW_INSTRUCTIONS = "Instructions (editable)"
LLM_PREVIEW_DIFF = "Differences (masked, read only)"
LLM_PREVIEW_FULL = "Full text after the change (masked, read only)"
LLM_PREVIEW_RESET = "Reset"
LLM_PREVIEW_EDIT_NOTE = "You can edit the instructions for this send only. The differences cannot be edited."
DIALOG_CANCEL = "Cancel"  # cancel button of the pywebview dialogs (wx uses the OS standard button)


def compare_save_note() -> str:
    """Note on the actions row (how saved results are handled)."""
    return f"Saved results: {save_caution()}"


def compare_save_title() -> str:
    """Title of the save dialog (wx)."""
    return f"Save result — {save_caution()}"


def compare_browse_title(side_label: str) -> str:
    """Title of the browse dialog (wx). side_label is "Before" or "After"."""
    return f"Choose the {side_label} file"


def compare_paste_title(side_label: str) -> str:
    """Title of the paste dialog."""
    return f"Paste {side_label}"


def compare_warning_line(warning: str) -> str:
    """One warning line shown above the structured compare tab."""
    return f"Warning: {warning}"


def auto_send_band(name: str | None) -> str:
    """Band always shown in the compare window while auto-send is on (ADR-0080 decision 2).

    None means the AI profile could not be read (nothing is sent).
    """
    if name is None:
        return "Auto-send: On (the AI profile could not be read, so nothing is sent)"
    return f"Auto-send: On (destination: {name})"


def auto_send_settings_unreadable(error: str) -> str:
    """Auto-send could not read the settings (shown in the AI area; nothing was sent)."""
    return f"Auto-send: nothing was sent because the settings could not be read ({error})"


# "Open result..." in the pywebview compare window (REQ-120 / ADR-0083; not in the wx window).
# Notices contain neither file contents nor paths (fixed text only)
COMPARE_BUTTON_OPEN_RESULT = "Open saved result…"  # changed from "Open result..." in REQ-206 / ADR-0148 decision 3 (not used by wx)
COMPARE_BUTTON_CLOSE_RESULT = "Close"
COMPARE_OPEN_FAILED_TITLE = "Cannot open result"
OPEN_RESULT_VERSION = "Cannot open: the saved result has a version this tool cannot read. Converting it could misread missing items as absent."
OPEN_RESULT_NOT_JSON = "Cannot open: the file cannot be read as JSON. Choose a saved result file."
OPEN_RESULT_FORMAT = "Cannot open: the file is not in the format of a saved result. Choose a saved result file."
OPEN_RESULT_UNREADABLE = "Cannot open: the file cannot be read."


def open_result_too_large(limit_mb: int) -> str:
    """The saved result file is larger than the limit (not read)."""
    return f"Cannot open: the file is too large (limit {limit_mb} MB)."


def opened_band(saved_at: str, before_name: str, after_name: str) -> str:
    """Band text while a saved result is shown (ADR-0083 decision 1). saved_at is the string from the saved file as is."""
    return f"Showing a saved result (saved at: {saved_at}, Before: {before_name}, After: {after_name})"


# Batch compare of multiple commands (REQ-122 / ADR-0084 decisions 3, 5, 6). pywebview compare window and CLI compare --all-segments.
# Summary, over-limit and nothing-to-send texts reuse the batch compare texts (batch_llm_*)
COMPARE_MULTI_TITLE = "Command pairs"
COMPARE_MULTI_COL_COMMAND = "Command"
COMPARE_MULTI_COL_STATUS = "Status"
COMPARE_MULTI_COL_DIFFS = "Differences"
COMPARE_MULTI_COL_SEND = "Send to AI"
COMPARE_MULTI_SEND_ALL = "Select or clear all"
# Status of a list row for a command section present in only one log (ADR-0164 decision 3). Names the missing side
MULTI_NO_AFTER = "No After"  # section only in Before
MULTI_NO_BEFORE = "No Before"  # section only in After
# Status of a row for a command section that appears only once when paired within one log (ADR-0152 decision 2), and the note shown on selection
MULTI_SINGLE = "Once only"
MULTI_SINGLE_NOTE = "This command appears only once in the log, so nothing was compared."
# Status of a row for a section of error output from a mistyped command (ADR-0195 decision 1; left out of automatic pairing, not compared)
MULTI_ERROR_OUTPUT = "Error output (not compared)"
# The "All" tab (ADR-0152 decision 7). Shown first among the result tabs only when compared in batch in the compare window
COMPARE_TAB_ALL = "All command results"
# Text added to the band of an opened .jsonl (unpaired sections are not saved; the file cannot tell whether any existed, so this is always shown)
MULTI_UNPAIRED_NOT_IN_FILE = "Unpaired sections are not included in the save"
COMPARE_MULTI_NOTHING_TO_SAVE = "There are no sections with a counterpart, so there are no pairs to save. Nothing was saved."
# Choosing pairs by hand (REQ-269 / ADR-0195 decision 2). Buttons under the list, the mark on a hand-made pair, and the partner dialog
COMPARE_MULTI_CHOOSE_PARTNER = "Choose partner…"
COMPARE_MULTI_UNPAIR = "Unpair"
MULTI_PAIRED_BY_HAND = "Paired by hand"
COMPARE_PARTNER_TITLE = "Choose partner"


def compare_partner_prompt(other_side: str, row_name: str) -> str:
    """Prompt of the partner dialog. other_side is the side to choose from ("before"/"after"); row_name is the selected row's name."""
    return f"Choose a partner for {row_name} from the {_SIDE_LABEL[other_side]} sections"


def partner_state_paired(place: str, command: str) -> str:
    """State text of a candidate that is in another pair now. Choosing it leaves the rest of that pair without a partner."""
    return f"Paired (choosing it leaves {place}: {command} without a partner)"


def partner_state_paired_error(place: str, command: str) -> str:
    """Same, when the remaining section is error output: it returns to error output instead of being left without a partner."""
    return f"Paired (choosing it returns {place}: {command} to error output)"


def multi_overall(status: str, total: int, count: int) -> str:
    """Overall text for a batch compare (bottom of the window). total is the number of list rows (pairs and unpaired).

    For differences found, pass the number of pairs with differences plus unpaired rows as count; for failed, the number of failed and needs-selection pairs. For no differences, count is not used.
    """
    if status == "no_difference":
        return f"{STATUS_LABEL['no_difference']} ({total} {_plural(total, 'pair', 'pairs')})"
    return f"{STATUS_LABEL[status]} ({count} of {total} {_plural(total, 'pair', 'pairs')})"


def multi_unpaired_not_saved(count: int) -> str:
    """Unpaired sections existed when saving a batch compare (not saved; shown as a notice and on CLI stderr)."""
    return f"{count} unpaired {_plural(count, 'section', 'sections')} not included in the save"


def single_pair_name(command: str, before: int, after: int) -> str:
    """Name of a pair in the list when paired within one log (ADR-0152 decision 2). Form: "show vlan (section 2 -> 7)"."""
    return f"{command} (section {before} -> {after})"


def single_row_name(number: int, command: str) -> str:
    """Row name for a once-only command section when paired within one log (ADR-0152 decision 2)."""
    return f"Section {number}: {command}"


def all_tab_title(count: int) -> str:
    """Title of the "All" tab (ADR-0152 decision 7). count is the number of table rows."""
    return f"{COMPARE_TAB_ALL} ({count})"


def unpaired_status(side: str) -> str:
    """Status of an unpaired list row (ADR-0164 decision 3). A section only in Before is "No After"; only in After is "No Before"."""
    return MULTI_NO_AFTER if side == "before" else MULTI_NO_BEFORE


def unpaired_place(side: str, number: int) -> str:
    """Form "Before section 3" (ADR-0164 decision 4). Same as the start of unpaired_row_name."""
    return f"{_SIDE_LABEL[side]} section {number}"


def unpaired_row_name(side: str, number: int, command: str) -> str:
    """Name of an unpaired list row. Form "Before section 4: show vlan" (same order as unpaired rows in CLI text)."""
    return f"{unpaired_place(side, number)}: {command}"


def unpaired_segment_note(side: str, number: int, command: str, line_count: int) -> str:
    """Note shown when an unpaired row is selected in the list (the table is empty)."""
    other = _SIDE_LABEL["after" if side == "before" else "before"]
    return (
        f"{_SIDE_LABEL[side]} section {number} ({command}, {line_count} {_plural(line_count, 'line', 'lines')}) "
        f"was not compared because {other} has no section with the same command."
    )


def error_output_note(command: str, line_count: int) -> str:
    """Note shown when an error-output row is selected in the list (the table is empty; ADR-0195 decision 1)."""
    return (
        f"This section ({command}, {line_count} {_plural(line_count, 'line', 'lines')}) "
        "is error output from a mistyped command, so it was not compared"
    )


def compare_llm_confirm_hint() -> str:
    """CLI compare --all-segments --llm run without --confirm (same form as batch compare batch_llm_confirm_hint)."""
    return "Nothing has been sent to the AI yet. To send this content, run again with --confirm added to --all-segments --llm."


# Follow-up questions on the AI answer in the pywebview compare window (REQ-118 / ADR-0081; not in the wx window).
# No text includes the content of the question
COMPARE_FOLLOW_UP_TITLE = "Follow-up question"
COMPARE_FOLLOW_UP_ASK = "Ask"
COMPARE_FOLLOW_UP_ASKING = "Asking the AI"
COMPARE_FOLLOW_UP_NOTICE_TITLE = "Question to the AI"
FOLLOW_UP_QUESTION_LABEL = "Question"
FOLLOW_UP_ANSWER_LABEL = "Answer"
_FOLLOW_UP_REWRITE = "Nothing was sent. Reword it and ask again."
_FOLLOW_UP_REFUSED = {
    "blank": "Enter a question. Nothing was sent.",
    "mask": (
        "The question contains something that would be masked (a settings line such as a password, key or community). "
        f"{_FOLLOW_UP_REWRITE} (Describe settings keywords and <secret:N> markers in plain words.)"
    ),
    "unsent": (
        "The question contains words that are not sent to the AI (secret words such as password, secret or key, or lines of a type that is not sent). "
        f"{_FOLLOW_UP_REWRITE} (Describe settings keywords and <secret:N> markers in plain words.)"
    ),
    "pem": f"The question contains the start line of a key or certificate (-----BEGIN …). {_FOLLOW_UP_REWRITE}",
}


def follow_up_refused(kind: str, max_chars: int) -> str:
    """Why the question was not sent (a request to rewrite). kind is a REFUSE_* from llm_analyzer/follow_up.py. The question itself is not included."""
    if kind == "too_long":
        return f"Keep the question to {max_chars:,} characters or fewer. Nothing was sent."
    return _FOLLOW_UP_REFUSED.get(kind, f"The question could not be sent. {_FOLLOW_UP_REWRITE}")


def follow_up_too_long(max_chars: int) -> str:
    """The text combined with the conversation so far exceeds the limit (not sent; the conversation is kept)."""
    return f"Nothing was sent because the text combined with the conversation so far exceeds {max_chars:,} characters. Compare again to start asking from the beginning."


def follow_up_limit(max_turns: int) -> str:
    """The follow-up question limit was reached (shown in the AI area)."""
    return f"Follow-up questions are limited to {max_turns}. To continue, compare again and then ask."


def follow_up_unavailable(reason: str | None) -> str:
    """The connection for sending the question is unavailable (no key, etc.; nothing was sent). Reason text is the same as in the AI area."""
    return f"Cannot ask the AI: {_LLM_REASON.get(reason or '', f'Reason: {reason}')}"


def follow_up_failed(
    reason: str,
    error_type: str,
    status_code: int | None,
    *,
    retry_after: int | None = None,
    has_api_text: bool = False,
) -> str:
    """Failure to send the question (as in ADR-0018 decision 7: only the reason text, exception type name and HTTP status code; no exception message).

    For 429, the reason text becomes llm_rate_limited (ADR-0168 decision 4). If has_api_text is true, add a pointer to the collapsible section of the AI area (decision 5).
    """
    detail = f"Type: {error_type}" + (f", HTTP {status_code}" if status_code is not None else "")
    text = _LLM_REASON.get(reason, f"Reason: {reason}")
    if reason == "api_error" and status_code == RATE_LIMIT_STATUS:
        text = llm_rate_limited(retry_after)
    body = f"Could not ask the AI: {text} ({detail})"
    if has_api_text:
        body += f'. The text returned by the API is in "{COMPARE_API_ERROR_TITLE}" below the AI area'
    return body


# Collapsible section of the AI area in the compare window (ADR-0168 decision 5). Only cleaned text is shown inside. Not saved
COMPARE_API_ERROR_TITLE = "Error text from the API"
COMPARE_API_ERROR_NOTE = "The text returned by the API is shown shortened. Key-like characters are masked. Not saved."

# "Thinking" collapsible section of the AI area in the compare window (ADR-0169 decision 4). The thinking text is not saved.
# The note for text cut for length is llm_analyzer.thinking.THINKING_CUT_NOTE (Python puts it into the view)
COMPARE_AI_THINKING_TITLE = "Thinking"
COMPARE_AI_THINKING_NOTE = "What the AI thought before answering. It may be in another language. Not saved."

GUI_WEB_TEXTS.update(
    {
        "compare.ai.thinking.title": COMPARE_AI_THINKING_TITLE,
        "compare.ai.thinking.note": COMPARE_AI_THINKING_NOTE,
    }
)


# Layout of the pywebview compare window (REQ-206 / ADR-0148). Top three rows, left/right areas and divider, AI area controls. Not in the wx window.
# No text includes typed text, question content, destination URLs or keys
COMPARE_BUTTON_SWAP_ICON = "\u21c5"  # swap icon. The title and aria-label are COMPARE_BUTTON_SWAP
COMPARE_SETTINGS_CHANGE = "Change"  # next to the compare settings summary. Opens/closes the current settings field
COMPARE_SPLIT_LABEL = "Divider between the compare table and the AI area"
# Divider between the command pair list and the compare table when compared in batch (REQ-224 / ADR-0163)
COMPARE_MULTI_SPLIT_LABEL = "Divider between the command pairs and the compare table"
# Note when the answer is stopped at what has arrived (ADR-0157 decision 7). Shown below the partial answer bubble
COMPARE_PARTIAL_STOPPED = "Stopped. This is a partial answer. Not saved."
COMPARE_AI_EMPTY = 'After comparing, click "Analyze with AI" and the AI answer appears here.'
COMPARE_AI_INPUT = "Question or instruction for the AI"
COMPARE_AI_INPUT_HINT = "Enter to send, Shift+Enter for a new line"
COMPARE_AI_PROFILE = "AI profile"
COMPARE_AI_PROMPT = "Prompt"
COMPARE_AI_ANALYZE = "Analyze with AI"
# Choose which command pairs to send to AI in a multi-command compare (REQ-265, ADR-0193 decision 3)
COMPARE_AI_NO_PAIRS = "Select at least one command pair to send to AI."


def compare_send_pairs(on: int, total: int) -> str:
    """Shown in the AI pane as "Pairs to send: 3 / 12". total is the number of pairs that can be sent."""
    return f"Pairs to send: {on} / {total}"


# Name and face title of the answer bubble when the character is on, plus the button and empty-area hint (ADR-0181 decisions 1, 2).
# Shown in the window only. Not included in saves, HTML, CSV, copies or sent text (decision 3)
CHARACTER_NAME = "Mirarin"
CHARACTER_AVATAR_TITLE = "The answer is written by the model of the selected AI profile"
COMPARE_AI_ANALYZE_CHARACTER = "Analyze with AI (Mirarin)"
COMPARE_AI_EMPTY_CHARACTER = 'After comparing, click "Analyze with AI (Mirarin)" and Mirarin answers here.'
COMPARE_COPY = "Copy"
COMPARE_AI_PROFILE_FAILED_TITLE = "Cannot switch the AI profile"
COMPARE_PROMPT_FAILED_TITLE = "Cannot switch the prompt"
# Hints for a device type or kind mismatch (ADR-0151 decision 8). pywebview compare window only. CLI and wx keep the current text
COMPARE_PLATFORM_TROUBLE = 'The device type selection does not seem to match the content. Set the device type back to "Auto" or choose again.'
COMPARE_KIND_TROUBLE = "The kind detected differs between Before and After. Choose a kind."
COMPARE_DETAILS = "Details"
# Difference summary and filter (ADR-0155 / REQ-215). pywebview compare window only. CLI and wx are unchanged
COMPARE_RAW_DIFF_ONLY = "Show differences only"
COMPARE_FILTER_TITLE = "Filter by category"


def compare_filter_all(total: int) -> str:
    """The "All N" chip above the structured compare table (ADR-0155 decision 1). total is the total number of structured rows."""
    return f"All {total}"


def compare_raw_folded(count: int) -> str:
    """Text for rows folded by "Show differences only" (ADR-0155 decision 2). count is the number of hidden rows."""
    return f"… {count} {_plural(count, 'line', 'lines')} omitted"


def follow_up_destination_changed(before: str, now: str) -> str:
    """Confirmation before a follow-up question when the AI profile differs from the one first sent to (ADR-0148 decision 7). Shows names only."""
    return f"The destination changed ({before} -> {now}). The differences and answers so far will be sent too. Send?"


# Keep the first request in the chat / continue asking from a saved conversation (ADR-0179 / REQ-242). pywebview compare window and AI window only.
# Typed text goes only into the view, not into these texts (names are only prompt and profile names)
def COMPARE_FIRST_REQUEST_PROMPT(name: str) -> str:  # noqa: N802 (name as in REQ-242 section 2)
    """Text of the first request bubble when sent with "Analyze with AI" without typing (decision 1). name is the prompt name used."""
    return f'Analyzed with prompt "{name}"'


def COMPARE_FIRST_REQUEST_AUTO(name: str) -> str:  # noqa: N802 (name as in REQ-242 section 2)
    """Text of the first request bubble for auto-send (decision 1). name is the prompt name used."""
    return f'Sent automatically (prompt "{name}")'


def COMPARE_FIRST_REQUEST_EDITED(text: str) -> str:  # noqa: N802 (name as in REQ-266 section 4)
    """Text of the first request bubble when sent with edited instructions (REQ-266 section 4). text is the unedited text, which this adds to."""
    return f"{text} (instructions edited)"


# Spoken name of the first request bubble (same "Question" as the follow-up bubble)
COMPARE_FIRST_REQUEST_LABEL = FOLLOW_UP_QUESTION_LABEL
# Name shown when the prompt name is unknown (the prompt file is missing) (same "-" as compare_handed_answer_note)
COMPARE_FIRST_REQUEST_NO_PROMPT = "-"
# Confirmation before sending when asking from an opened saved result whose save has no masked words from when it was sent (an old save) (decision 3)
COMPARE_SAVED_NO_WORDS = "This save does not keep the words that were masked when it was sent. It was checked with the words in the current settings only. Send?"


def compare_saved_no_profile(now: str) -> str:
    """Confirmation when asking from an opened saved result whose save has no record of the destination (decision 3). Shows names only."""
    return f"This save has no record of the destination (current destination: {now}). The differences and answers so far will be sent too. Send?"


GUI_WEB_TEXTS.update({"compare.ai.first_request": COMPARE_FIRST_REQUEST_LABEL})


def instruction_too_long(max_chars: int) -> str:
    """The text to send combined with the text typed before analysis exceeds the limit (not sent; ADR-0148 decision 6)."""
    return f"Nothing was sent because the text to send, combined with the typed text, exceeds {max_chars:,} characters. Shorten the typed text and press again."


# "Send differences + full text" (ADR-0158 / REQ-218). pywebview compare window only. Not in CLI, batch compare or wx.
# No sentence includes the full text or the question contents.
COMPARE_FULL_TEXT = "Send differences + full text"
# Short note under the checkbox, shown only while it is on
COMPARE_FULL_TEXT_NOTE = "Sending the full text adds more lines that match secrets that cannot be masked. IP addresses, hostnames and descriptions are sent as they are."
# Rebuilding the full text for a follow-up question failed or the numbering was off
COMPARE_FULL_TEXT_MISMATCH = "Could not build the full text. Compare again, then send."
# Reasons the option cannot be selected (note right under the checkbox, and title)
COMPARE_FULL_TEXT_MULTI_FIRST = "After a multi-file compare, this can be selected only for a follow-up question."
COMPARE_FULL_TEXT_NOT_YET = "Available after comparing."
COMPARE_FULL_TEXT_BUSY = "Available after the comparison or sending finishes."
COMPARE_FULL_TEXT_OPENED = "Not available while a saved result is open."
COMPARE_FULL_TEXT_UNPAIRED = "Not available for rows with no counterpart."
# Only when the kind is neither config nor show (for example, when it cannot be determined)
COMPARE_FULL_TEXT_NOT_CONFIG = "Available only for config or show comparisons."
COMPARE_FULL_TEXT_NOT_SENT = "Not available for pairs not sent to AI."


def COMPARE_FULL_TEXT_PAIR(name: str) -> str:  # noqa: N802 (name fixed by REQ-237 section 2)
    """Note shown when the full text can be chosen in a multi-file compare."""
    return f"Pair with full text: {name}"


# Carry the batch compare answer over to the compare window (ADR-0170 / REQ-232). pywebview compare window only.
COMPARE_HANDED_ANSWER_TITLE = "Batch compare answer"
# The re-compared differences differ from what batch compare sent, so the answer was not attached
COMPARE_HANDED_ANSWER_UNUSED = (
    "The differences are different from what batch compare sent, so the batch compare answer was not used. Send again with \"Analyze with AI\"."
)


def compare_handed_answer_note(profile_name: str, prompt_name: str | None) -> str:
    """Note shown above the AI pane answer. Names only; "-" when there is no prompt name."""
    prompt = prompt_name if prompt_name else "-"
    return f"This answer came from batch compare (AI profile: {profile_name}, prompt: {prompt}). You can keep asking questions."


def llm_full_text_preview(chars: int) -> str:
    """Banner shown at the top of the pre-send confirmation. chars is the total length of the text to send."""
    return f"The full After text will be sent ({chars:,} characters in total). It may include secrets that cannot be stopped, such as ones without keywords."


# Texts for the pywebview compare screen (static/compare.html, REQ-105).
GUI_WEB_TEXTS.update(
    {
        "compare.inputs": COMPARE_INPUTS,
        "compare.side.before": _SIDE_LABEL["before"],
        "compare.side.after": _SIDE_LABEL["after"],
        "compare.button.browse": COMPARE_BUTTON_BROWSE,
        "compare.button.paste": COMPARE_BUTTON_PASTE,
        "compare.button.swap": COMPARE_BUTTON_SWAP,
        "compare.button.swap_icon": COMPARE_BUTTON_SWAP_ICON,
        "compare.segments": COMPARE_SEGMENTS,
        "compare.settings.change": COMPARE_SETTINGS_CHANGE,
        "compare.split.label": COMPARE_SPLIT_LABEL,
        "compare.multi.split.label": COMPARE_MULTI_SPLIT_LABEL,
        "compare.ai.empty": COMPARE_AI_EMPTY,
        # When the character is on (ADR-0181). Name and face title are shared by the compare, other and batch windows
        "compare.ai.empty.character": COMPARE_AI_EMPTY_CHARACTER,
        "character.name": CHARACTER_NAME,
        "character.avatar.title": CHARACTER_AVATAR_TITLE,
        "compare.ai.input": COMPARE_AI_INPUT,
        "compare.ai.input.hint": COMPARE_AI_INPUT_HINT,
        "compare.ai.profile": COMPARE_AI_PROFILE,
        "compare.ai.prompt": COMPARE_AI_PROMPT,
        "compare.button.copy": COMPARE_COPY,
        "compare.settings": COMPARE_SETTINGS,
        "compare.mode": COMPARE_MODE,
        "compare.kind": COMPARE_KIND,
        "compare.platform": COMPARE_PLATFORM,
        "compare.platform.hint": COMPARE_PLATFORM_HINT,
        "compare.command": COMPARE_COMMAND,
        "compare.command.hint": COMPARE_COMMAND_HINT,
        "compare.tab.all": COMPARE_TAB_ALL,
        "compare.tab.structured": COMPARE_TAB_STRUCTURED,
        "compare.tab.raw": COMPARE_TAB_RAW,
        "compare.tab.unparsed": COMPARE_TAB_UNPARSED,
        "compare.col.op": COMPARE_COL_OP,
        "compare.col.category": COMPARE_COL_CATEGORY,
        "compare.col.target": COMPARE_COL_TARGET,
        "compare.col.item": COMPARE_COL_ITEM,
        "compare.col.target_item": COMPARE_COL_TARGET_ITEM,
        "compare.col.before": COMPARE_COL_BEFORE,
        "compare.col.after": COMPARE_COL_AFTER,
        "compare.col.before_no": COMPARE_COL_BEFORE_NO,
        "compare.col.after_no": COMPARE_COL_AFTER_NO,
        "compare.col.line": COMPARE_COL_LINE,
        "compare.col.parents": COMPARE_COL_PARENTS,
        "compare.button.prev": COMPARE_BUTTON_PREV,
        "compare.button.next": COMPARE_BUTTON_NEXT,
        "compare.llm": COMPARE_LLM_PANE,
        "compare.follow_up.title": COMPARE_FOLLOW_UP_TITLE,
        "compare.button.run": COMPARE_BUTTON_RUN,
        "compare.button.save": COMPARE_BUTTON_SAVE,
        "compare.button.open_result": COMPARE_BUTTON_OPEN_RESULT,
        "compare.button.close_result": COMPARE_BUTTON_CLOSE_RESULT,
        "compare.button.cancel": COMPARE_BUTTON_CANCEL,
        "compare.button.batch": COMPARE_BUTTON_BATCH,
        "compare.button.settings": COMPARE_BUTTON_SETTINGS,
        "compare.multi.title": COMPARE_MULTI_TITLE,
        "compare.multi.col.command": COMPARE_MULTI_COL_COMMAND,
        "compare.multi.col.status": COMPARE_MULTI_COL_STATUS,
        "compare.multi.col.diffs": COMPARE_MULTI_COL_DIFFS,
        "compare.multi.col.send": COMPARE_MULTI_COL_SEND,
        "compare.multi.send_all": COMPARE_MULTI_SEND_ALL,
        "compare.multi.choose_partner": COMPARE_MULTI_CHOOSE_PARTNER,
        "compare.multi.unpair": COMPARE_MULTI_UNPAIR,
        "compare.multi.paired_by_hand": MULTI_PAIRED_BY_HAND,
        "compare.paste.prompt": PASTE_PROMPT,
        "compare.dialog.ok": DIALOG_OK,
        "compare.dialog.cancel": DIALOG_CANCEL,
        "compare.details": COMPARE_DETAILS,
        # Difference summary and filter (ADR-0155 / REQ-215)
        "compare.raw.diff_only": COMPARE_RAW_DIFF_ONLY,
        "compare.filter.title": COMPARE_FILTER_TITLE,
        # "Send differences + full text" (ADR-0158 / REQ-218)
        "compare.llm.full_text": COMPARE_FULL_TEXT,
    }
)


# --- Batch compare screen texts (REQ-106). Shared by wx gui/batch_frame.py, gui/dialogs.py and pywebview static/batch.html ---
# The texts are the same as those on the wx batch compare screen (moved, not changed)

BATCH_TITLE = "DriftPilot — Batch compare"
BATCH_SOURCE = "Input"
BATCH_BEFORE_DIR = "Before folder"
BATCH_AFTER_DIR = "After folder"
BATCH_MANIFEST = "Pair file"
# Titles of the pick dialogs (wx; pywebview dialogs cannot take a title)
BATCH_PICK_BEFORE = "Choose the Before folder"
BATCH_PICK_AFTER = "Choose the After folder"
BATCH_PICK_MANIFEST = "Choose the pair file"
BATCH_REBUILD = "Rebuild pairs"
BATCH_RUN = "Run"
BATCH_SAVE_MANIFEST = "Save these pairs…"
BATCH_SAVE_RESULTS = "Save results…"
BATCH_CANCEL = "Cancel"
BATCH_HANDOFF = "Open in compare window"
# Columns of the pair list
BATCH_COL_INDEX = "#"
BATCH_COL_BEFORE = "Before file"
BATCH_COL_AFTER = "After file"
BATCH_COL_SELECTION = "Section"
BATCH_COL_SIDE = "Found in"
BATCH_COL_NAME = "File name"
# Columns of the result list
BATCH_COL_STATUS = "Status"
BATCH_COL_FILE = "File"
BATCH_COL_STRUCTURED = "Structured differences"
BATCH_COL_RAW = "Simple differences"
BATCH_COL_UNPARSED = "Unparsed lines"
BATCH_COL_LLM = "AI analysis"
BATCH_COL_PLATFORM = "Device type"  # pywebview result table only (no wx column; ADR-0151)
# The single table and top row of the pywebview batch compare window (REQ-211 / ADR-0153; not in the wx screen)
BATCH_COL_LABEL = "Name"
BATCH_COL_AI_POINT = "AI key points"
BATCH_COL_RESIZE = "Resize column"  # Screen reader name of the header border (title and aria-label)
BATCH_OPEN_MANIFEST = "Or open a pair file"
BATCH_OPEN = "Open…"
BATCH_DROP_GUIDE = "You can drop files or folders here"
BATCH_PROGRESS_NAME = "Batch compare progress"  # Screen reader name of the progress (aria-label)
BATCH_RESULTS = "Results"
BATCH_ANSWER = "AI answer for the selected pair"
# Titles and texts of notices (wx MessageBox)
BATCH_SETUP_FAILED_TITLE = "Cannot build pairs"
BATCH_LLM_SETTINGS_TITLE = "Cannot read AI analysis settings"
BATCH_NOT_SENT_TITLE = "Not sent to AI"
BATCH_FAILED_TITLE = "Batch compare failed"
BATCH_SELECT_TITLE = "Select a pair"
BATCH_SELECT_PROMPT = "Select a pair in the list to open in the compare window."
# Drops onto the pywebview batch compare window (REQ-115 / ADR-0078). The input stays unchanged and this notice is shown
BATCH_DROP_TITLE = "Cannot drop"
BATCH_DROP_UNSUPPORTED = "You can drop one or two folders, or one pair file (.yaml or .yml). The input was not changed."
BATCH_DROP_NO_PATH = "Could not find the location of what you dropped. Use \"Browse…\" instead. The input was not changed."
# Pre-send confirmation (wx BatchLlmApprovalDialog, ADR-0031)
BATCH_APPROVAL_TITLE = "Review what will be sent to AI (batch)"
BATCH_APPROVAL_ROWS = "Pairs to send (unchecked pairs are not sent):"
BATCH_APPROVAL_COL_JOB = "Pair"
BATCH_APPROVAL_COL_SENT = "Differences sent"
BATCH_APPROVAL_COL_MASKED = "Masked values"
BATCH_APPROVAL_COL_UNSENT = "Lines not sent"
BATCH_APPROVAL_SAVE = "Save contents to file…"
BATCH_APPROVAL_SEND = "Send"
BATCH_APPROVAL_DONT_SEND = "Don't send"
BATCH_PAYLOADS_SAVE_TITLE = "Save what will be sent"  # Title of the save dialog (wx)
BATCH_SAVED_TITLE = "Saved"


# AI area of the pywebview batch compare window (REQ-213 / ADR-0153). Not in the wx screen.
# No sentence includes the connection URL, key or CLI location.
BATCH_AI_CHECK = "Review contents…"
BATCH_APPROVAL_CLOSE = "Close"  # Button of the "Review contents…" confirmation (nothing is sent)
BATCH_AI_FOLLOW_UP_NOTE = "To ask a question, double-click to open it in the compare window."
BATCH_AUTO_SEND_ON = "Send to AI automatically: On (after a run, a list of what will be sent is shown. Nothing is sent until you press \"Send\".)"
# Reasons "Analyze with AI" and "Review contents…" cannot be pressed (note under the AI row and button title; ADR-0175)
BATCH_AI_REASON_NOT_RUN = "Available after a run."
BATCH_AI_REASON_RUNNING = "Available after the run finishes."
BATCH_AI_REASON_APPROVAL = "Available after the pre-send confirmation and sending finish."
BATCH_AI_REASON_COMPARE = "Not available while the compare window is sending to AI."
BATCH_AI_REASON_NO_TARGET = "No pairs have differences that can be sent."


def batch_ai_title(pairs: int, diffs: int) -> str:
    """Title of the AI area: pairs not unchecked that have a result, did not fail and have differences to send, and the total differences."""
    return f"Pairs to analyze with AI: {pairs}, {_plural(diffs, 'difference', 'differences')}: {diffs}"


def batch_run_label(count: int) -> str:
    """The Run button (with the number of pairs)."""
    return f"{BATCH_RUN} ({count} {_plural(count, 'pair', 'pairs')})"


def batch_all_tab_title(count: int) -> str:
    """The "All" tab of the pywebview batch compare table. Shown even for 0 (REQ-211)."""
    return f"All ({count})"


def batch_diff_tab_title(count: int) -> str:
    """The "Pairs with differences" tab of the pywebview batch compare table. Shown even for 0 (REQ-211)."""
    return f"Pairs with differences ({count})"


def batch_save_note() -> str:
    """Note on the action row and pre-send confirmation (handling of saved files; ADR-0017)."""
    return f"Saved files: {save_caution()}"


def batch_save_manifest_title() -> str:
    """Title of the save dialog for "Save these pairs…" (wx)."""
    return f"Save these pairs — {save_caution()}"


def batch_manifest_saved(path: Path) -> str:
    return f"Wrote the pair file to {path}. No comparison was run."


def batch_rest_not_run(count: int) -> str:
    """Number of remaining pairs not run after cancelling (appended after "Cancelled.")."""
    return f"{count} remaining {_plural(count, 'pair was', 'pairs were')} not run."


def batch_payloads_saved(path: Path) -> str:
    """When "Save contents to file…" in the pre-send confirmation has written the file."""
    return f"Saved to {path}."


# Texts for the pywebview batch compare screen (static/batch.html, REQ-106).
GUI_WEB_TEXTS.update(
    {
        "batch.source": BATCH_SOURCE,
        "batch.before_dir": BATCH_BEFORE_DIR,
        "batch.after_dir": BATCH_AFTER_DIR,
        "batch.manifest": BATCH_MANIFEST,
        "batch.button.pick": PICK_BUTTON,
        "batch.kind": COMPARE_KIND,
        "batch.platform": COMPARE_PLATFORM,
        "batch.mode": COMPARE_MODE,
        "batch.button.rebuild": BATCH_REBUILD,
        "batch.col.index": BATCH_COL_INDEX,
        "batch.col.before": BATCH_COL_BEFORE,
        "batch.col.after": BATCH_COL_AFTER,
        "batch.col.selection": BATCH_COL_SELECTION,
        "batch.col.side": BATCH_COL_SIDE,
        "batch.col.name": BATCH_COL_NAME,
        "batch.col.status": BATCH_COL_STATUS,
        "batch.col.file": BATCH_COL_FILE,
        "batch.col.structured": BATCH_COL_STRUCTURED,
        "batch.col.raw": BATCH_COL_RAW,
        "batch.col.unparsed": BATCH_COL_UNPARSED,
        "batch.col.llm": BATCH_COL_LLM,
        "batch.col.platform": BATCH_COL_PLATFORM,
        "batch.progress": BATCH_PROGRESS_NAME,
        "batch.cancel_note": batch_cancel_note(),
        "batch.results": BATCH_RESULTS,
        "batch.answer": BATCH_ANSWER,
        "batch.button.run": BATCH_RUN,
        "batch.button.save_manifest": BATCH_SAVE_MANIFEST,
        "batch.button.save_results": BATCH_SAVE_RESULTS,
        "batch.button.cancel": BATCH_CANCEL,
        "batch.button.handoff": BATCH_HANDOFF,
        # The single table and top row (REQ-211)
        "batch.col.label": BATCH_COL_LABEL,
        "batch.col.ai_point": BATCH_COL_AI_POINT,
        "batch.col.resize": BATCH_COL_RESIZE,
        # Column borders of the 5 tables in the compare window (REQ-224 / ADR-0163). Same text as the batch window
        "compare.col.resize": BATCH_COL_RESIZE,
        "batch.open_manifest": BATCH_OPEN_MANIFEST,
        "batch.button.open": BATCH_OPEN,
        "batch.drop_guide": BATCH_DROP_GUIDE,
        "batch.button.swap": COMPARE_BUTTON_SWAP,
        "batch.button.swap_icon": COMPARE_BUTTON_SWAP_ICON,
        # AI area (REQ-213). Profile and prompt names, "Analyze with AI" and copy use the same texts as the compare window
        "batch.ai.profile": COMPARE_AI_PROFILE,
        "batch.ai.prompt": COMPARE_AI_PROMPT,
        "batch.button.ai_check": BATCH_AI_CHECK,
        "batch.button.ai_send": COMPARE_AI_ANALYZE,
        # "Analyze with AI" when the character is on (ADR-0181; batch.js swaps it by the view character)
        "batch.button.ai_send.character": COMPARE_AI_ANALYZE_CHARACTER,
        "batch.button.copy": COMPARE_COPY,
        "batch.ai.follow_up_note": BATCH_AI_FOLLOW_UP_NOTE,
    }
)


# In the batch compare window (pywebview): fix pairs, exclude them, make pairs, pick the device type automatically (REQ-212 / ADR-0153 decisions 4-8)
BATCH_JOB_EXCLUDED = "Excluded pair"  # status column of the table
BATCH_PLATFORM_UNDETECTED_ROW = "Auto: could not detect"  # device type column of an auto pair with no detection or a mismatch
# Device type column when the commands in a config split across device types in a batch-compared pair (ADR-0178 decision 2)
BATCH_AUTO_PLATFORM_MIXED = "Auto (multiple)"
# Reason shown in the answer field when a "Needs selection" pair is chosen (ADR-0178 decision 5). {count} is the number of sections, {commands} is the list of section command names
BATCH_WAITING_SEGMENT = (
    "This file has {count} section(s), and the section setting did not match exactly one of them. "
    "Fix the section in 'Pair settings…' (sections: {commands})"
)
BATCH_WAITING_FORMAT = "Could not decide between config and show. Choose the type in 'Pair settings…'"
# Notice when every pair that was run (excluding excluded pairs) needed selection (ADR-0178 decision 5). The title is the status name
BATCH_ALL_WAITING = "All pairs need selection. Select a pair to see the reason"
BATCH_ALL_WAITING_TITLE = STATUS_LABEL["needs_selection"]
# Text shown in place of the names when the secret-word settings cannot be read and the section command names cannot be checked (not shown if they cannot be masked)
BATCH_WAITING_NAMES_HIDDEN = "…"
# Number of section command names listed in the reason for "Needs selection" (beyond that: "N more")
BATCH_WAITING_NAMES_SHOWN = 10


def batch_waiting_segment(count: int, names: list[str]) -> str:
    """Reason for "Needs selection" when the section is not decided (BATCH_WAITING_SEGMENT).

    Names (already checked) are joined with commas; beyond BATCH_WAITING_NAMES_SHOWN, the first ones plus "N more".
    """
    shown = list(names[:BATCH_WAITING_NAMES_SHOWN])
    rest = len(names) - len(shown)
    if rest > 0:
        shown.append(f"{rest} more")
    return BATCH_WAITING_SEGMENT.format(count=count, commands=", ".join(shown))


def multi_pair_name(group: str, command: str | None) -> str:
    """Name of a command pair in a batch-compared pair (saved job.label and output pair name; ADR-0178 decision 3).

    `<pair name>: <command>`. Just the pair name when the command is unknown.
    """
    return group if command is None else f"{group}: {command}"
BATCH_PAIR_SETTINGS = "Pair settings…"
BATCH_ALL_SETTINGS = "All pairs settings…"
BATCH_UNPAIR = "Unpair"
BATCH_SUGGEST_HOSTNAMES = "Suggest by hostname"
BATCH_PAIR_FILES = "Pair the 2 selected"
# Note above the two columns of unpaired files (REQ-233 / ADR-0171. Drag a row to make a pair)
BATCH_UNPAIRED_DRAG_NOTE = "You can also make a pair by dragging a row onto a row on the other side"
BATCH_ACCEPT_SUGGESTIONS = "Pair the selected suggestions"
BATCH_DISMISS_SUGGESTIONS = "Close suggestions"
# Title of the pair suggestion list (REQ-247 / ADR-0184 decision 3. Holds both hostname suggestions and AI suggestions)
BATCH_SUGGESTIONS = "Auto-detected pair suggestions"
BATCH_COL_INCLUDED = "Run"  # screen-reader name of the checkbox column at the start of the table
BATCH_COL_PAIR_CHECK = "Make pair"  # screen-reader name of the checkbox on a row of an unpaired file
BATCH_COL_SUGGEST_CHECK = "Suggested pair"  # screen-reader name of the checkbox on a suggestion row
BATCH_COL_HINT = "Clue"
BATCH_COL_SIDE_BEFORE = "Before"
BATCH_COL_SIDE_AFTER = "After"
BATCH_ROW_PLATFORM = "Device type"  # screen-reader name of the device type select in the table (placed before the row name)
# Pair settings dialog
BATCH_PAIR_SETTINGS_TITLE = "Pair settings"
BATCH_ALL_SETTINGS_TITLE = "All pairs settings"
BATCH_SETTINGS_APPLY = "Apply"
BATCH_SETTINGS_FIELD_ID = "id"
BATCH_SETTINGS_FIELD_LABEL = "Name"
BATCH_SETTINGS_FIELD_SHOW_COMMAND = COMPARE_COMMAND
BATCH_SETTINGS_FIELD_BEFORE = "Before section"
BATCH_SETTINGS_FIELD_AFTER = "After section"
BATCH_SETTINGS_FIELD_PROMPT = "Device prompt"
BATCH_SETTINGS_FIELD_RULES = "Normalization rules"
BATCH_SETTINGS_FIELD_ENABLE_RULE = "Rules to enable"
BATCH_SETTINGS_KEEP_DEFAULT = "Keep default"
BATCH_SETTINGS_KEEP_PAIRS = "Keep per pair"
BATCH_SETTINGS_SELECTION_CHOICES = ("Auto", "Command name", "Number")
BATCH_SETTINGS_PROMPT_NOTE = "The device prompt used to split the log (not the instruction text sent to the AI)."
BATCH_SETTINGS_ENABLE_RULE_NOTE = "Write the rule IDs separated by commas."
BATCH_SETTINGS_PAIR_NOTE = "Empty fields and 'Keep default' use the all-pairs settings."
BATCH_SETTINGS_ALL_NOTE = "Values written per pair take priority over these. If you choose a device type or show command, the same value is applied to all pairs."
BATCH_SETTINGS_REJECTED_TITLE = "This value is not allowed"
BATCH_SETTINGS_SELECT_PROMPT = "Select the pair to edit in the list."
BATCH_UNPAIR_SELECT_PROMPT = "Select the pair to unpair in the list."
# An action for a single pair (Pair settings / Open in compare window) was pressed with 2 or more rows selected (ADR-0176 decision 2)
BATCH_SELECT_ONE = "This action works on one pair only. Select just one pair."
# Screen-reader name of the "all" checkbox in the header (ADR-0176 decision 3)
BATCH_INCLUDED_ALL = "Run all"
BATCH_SUGGEST_ALL = "Select all as suggested pairs"
# Confirmation before rebuilding the pairs (when there are edited settings)
BATCH_REBUILD_CONFIRM_TITLE = "Rebuild the pairs?"
BATCH_REBUILD_CONFIRM_BODY = "Rebuilding the pairs discards edited settings, excluded pairs, and pairs made by hand, and returns to the defaults."
BATCH_REBUILD_CONFIRM_YES = "Rebuild"
BATCH_DROP_EDITED = "You are editing the pairs, so dropping files does not rebuild them. The input is unchanged."
BATCH_SUGGEST_TITLE = "Hostname suggestions"
# The selected suggestions contain the same file twice or more (Before with Before, After with After). Nothing is paired (ADR-0177 decision 2)
BATCH_SUGGEST_OVERLAP = "You selected 2 or more suggestions that contain the same file. Choose only one of them."


def batch_platform_auto_row(used: str | None) -> str:
    """Device type column of an auto pair after a run ("Auto: FortiGate"; ADR-0153 decision 8)."""
    return f"{AUTO_PLATFORM_LABEL}: {platform_label(used)}"


def batch_platform_undetected(count: int) -> str:
    """Text under the window after a run: number of auto pairs whose device type was not detected (compared with no device type, as in REQ-209)."""
    return f"{count} {_plural(count, 'pair', 'pairs')} could not be matched to a device type (compared using the Cisco-family and Arista parsing)"


def batch_manifest_saved_with(path: Path, excluded: int, undetected: int) -> str:
    """Text after 'Save these pairs…'. Adds the number of excluded pairs not written and of undetected pairs written without a device type (nothing added for 0)."""
    parts = [f"Wrote the pair file to {path}."]
    if excluded:
        parts.append(f"Did not write {excluded} excluded {_plural(excluded, 'pair', 'pairs')}.")
    if undetected:
        parts.append(f"Did not write a device type for {undetected} {_plural(undetected, 'pair', 'pairs')} whose device type could not be detected.")
    parts.append("No comparison was run.")
    return " ".join(parts)


def batch_hostname_hint(value: str) -> str:
    """Clue on a suggestion row (shown only in the on-screen table; not written to the log or saved files)."""
    return f"hostname: {value}"


def batch_hostname_none(unread: int, duplicated: int) -> str:
    """Notice when there are 0 hostname suggestions."""
    return f"No pair suggestions with the same hostname. {batch_hostname_counts(unread, duplicated)}"


def batch_hostname_counts(unread: int, duplicated: int) -> str:
    """Number of files left out of the suggestions (the 'never choose silently' rule of ADR-0153 decision 7)."""
    return f"Not suggested: files whose hostname could not be read: {unread}; files whose hostname repeats on the same side: {duplicated}"


def batch_settings_rejected(problems: list[tuple[str, str]]) -> str:
    """Notice when the pair settings are rejected (the ManifestError.problems text). The pairs stay as they were."""
    lines = [f"These values cannot be applied to the pairs ({len(problems)} {_plural(len(problems), 'problem', 'problems')}). The pairs are unchanged."]
    lines += [f"  {where}: {message}" for where, message in problems]
    return "\n".join(lines)


def batch_pair_settings_title(job_id: str) -> str:
    return f"{BATCH_PAIR_SETTINGS_TITLE} — {job_id}"


# Ask the AI for pair suggestions about unpaired files (REQ-219 / ADR-0159). No sentence includes the AI's answer, exception text, or keys
BATCH_PAIR_AI_BUTTON = "Ask AI for suggestions…"
BATCH_PAIR_AI_TITLE = "Ask AI for pair suggestions"  # title of the pre-send confirmation and notices
BATCH_PAIR_AI_NOTE = "Check that the file names do not contain host names."
BATCH_PAIR_AI_HELD_BACK = "Files not sent"
# Heading of the full-text box to send. The request for pair suggestions does not add the answer-look rules (LOOK_RULES), so only this text is sent (ADR-0177 decision 4)
BATCH_PAIR_AI_PROMPT_TITLE = "Text to send (only this text is sent):"
BATCH_PAIR_AI_NO_FILES = "There are no files to send."
BATCH_PAIR_AI_UNREADABLE = "Could not read the AI suggestions. No pairs were made."
BATCH_PAIR_AI_NONE = "The AI had no suggestions."
# Why the answer could not be read, and the kinds of pairs that could not be read (REQ-223 / ADR-0162. REASON_* and SKIP_* in llm_analyzer/pair_suggestion.py)
PAIR_AI_REASON_TEXT = {
    "no_json": "no JSON found in the answer",
    "many_json": "the answer has 2 or more JSON objects",
    "shape": "the JSON has the wrong shape",
    "too_long": "the answer is too long",
}
PAIR_AI_SKIP_TEXT = {"shape": "wrong shape", "unknown_name": "name not in the list", "overlap": "overlapping names"}
BATCH_PAIR_AI_HINT = "AI suggestion (judged from file names only)"  # clue on a suggestion row (kept separate from hostname suggestions; decision 6)
BATCH_PAIR_AI_ASKING = "Asking the AI for pair suggestions…"


def batch_pair_ai_destination(name: str, model: str | None, base_url: str | None) -> str:
    """Destination in the pre-send confirmation (AI profile name, model, endpoint; same format as ADR-0031 decision 3). No key."""
    return f"Destination: {name}  Model: {model or '-'}  Endpoint: {base_url or '(default)'}"


def batch_pair_ai_unavailable(reason: str | None) -> str:
    """When the connection for asking is not usable (no key, etc.; nothing was sent). Reason text is the same as in the AI analysis frame."""
    return f"Cannot ask the AI for suggestions: {_LLM_REASON.get(reason or '', f'Reason: {reason}')}"


def batch_pair_ai_too_many(count: int, limit: int) -> str:
    """When the number of names to send is over the limit (never send only part of them; ADR-0159 decision 2)."""
    return f"There are {count:,} file names to send, over the limit of {limit:,}. Nothing was sent."


def batch_pair_ai_too_long(limit: int) -> str:
    return f"The text to send is over {limit:,} characters, so it was not sent."


def batch_pair_ai_failed(reason: str, error_type: str, status_code: int | None) -> str:
    """Send failure (as in follow_up_failed: only the reason text, the exception type name, and the HTTP status code; no exception text)."""
    detail = f"type: {error_type}" + (f", HTTP {status_code}" if status_code is not None else "")
    return f"Could not ask the AI for suggestions: {_LLM_REASON.get(reason, f'Reason: {reason}')} ({detail})"


def batch_pair_ai_skipped(skipped: Sequence[tuple[str, int]]) -> str:
    """Format (kind, count) pairs as 'name not in the list 2, overlapping names 1' (order kept). No answer content."""
    return ", ".join(f"{PAIR_AI_SKIP_TEXT.get(kind, kind)} {count}" for kind, count in skipped)


def batch_pair_ai_unreadable(reason: str | None, skipped: Sequence[tuple[str, int]]) -> str:
    """When none of the AI suggestions were usable. With a reason, give it; otherwise add the number and kinds of unreadable pairs."""
    if reason is not None:
        return f"{BATCH_PAIR_AI_UNREADABLE} (Reason: {PAIR_AI_REASON_TEXT.get(reason, reason)})"
    total = sum(count for _kind, count in skipped)
    return f"{BATCH_PAIR_AI_UNREADABLE} (Unreadable pairs: {total}; {batch_pair_ai_skipped(skipped)})"


def batch_pair_ai_partial(read: int, skipped: Sequence[tuple[str, int]]) -> str:
    """When only some of the AI suggestions were added as candidates (number read, and number and kinds that could not be read)."""
    total = sum(count for _kind, count in skipped)
    return f"Added {read} {_plural(read, 'pair', 'pairs')} from the AI suggestions as candidates. {total} {_plural(total, 'pair', 'pairs')} could not be read ({batch_pair_ai_skipped(skipped)})."


GUI_WEB_TEXTS.update(
    {
        "batch.button.pair_settings": BATCH_PAIR_SETTINGS,
        "batch.button.all_settings": BATCH_ALL_SETTINGS,
        "batch.button.unpair": BATCH_UNPAIR,
        "batch.button.suggest": BATCH_SUGGEST_HOSTNAMES,
        "batch.button.ask_pair_ai": BATCH_PAIR_AI_BUTTON,
        "batch.button.pair_files": BATCH_PAIR_FILES,
        "batch.unpaired.drag_note": BATCH_UNPAIRED_DRAG_NOTE,
        "batch.button.accept_suggestions": BATCH_ACCEPT_SUGGESTIONS,
        "batch.button.dismiss_suggestions": BATCH_DISMISS_SUGGESTIONS,
        "batch.suggestions": BATCH_SUGGESTIONS,
        "batch.col.included": BATCH_COL_INCLUDED,
        "batch.col.pair_check": BATCH_COL_PAIR_CHECK,
        "batch.col.suggest_check": BATCH_COL_SUGGEST_CHECK,
        "batch.col.hint": BATCH_COL_HINT,
        "batch.col.side_before": BATCH_COL_SIDE_BEFORE,
        "batch.col.side_after": BATCH_COL_SIDE_AFTER,
        "batch.col.row_platform": BATCH_ROW_PLATFORM,
        # Multi-row selection, the "all" checkbox in the header, and "Settings…" (ADR-0176 / REQ-238)
        "batch.select.title": BATCH_SELECT_TITLE,
        "batch.select.one": BATCH_SELECT_ONE,
        "batch.dialog.ok": DIALOG_OK,
        "batch.col.included_all": BATCH_INCLUDED_ALL,
        "batch.col.suggest_all": BATCH_SUGGEST_ALL,
        "batch.button.settings": COMPARE_BUTTON_SETTINGS,
    }
)


# Texts for the pywebview settings window (static/settings.html. REQ-104) and the confirmation dialog on the sample page.
# They use the constants above, so they are added here to GUI_WEB_TEXTS (defined at the top of the file)
GUI_WEB_TEXTS.update(
    {
        "settings.title": SETTINGS_TITLE,
        "settings.button.migrate": SETTINGS_BUTTON_MIGRATE,
        "settings.profiles": PROFILES_TITLE,
        "settings.button.add": SETTINGS_BUTTON_NEW,
        "settings.button.duplicate": SETTINGS_BUTTON_DUPLICATE,
        "settings.button.delete_profile": SETTINGS_BUTTON_DELETE,
        "settings.edit": PROFILE_EDIT_TITLE,
        "settings.field.name": SETTINGS_FIELD_NAME,
        "settings.field.api_format": SETTINGS_FIELD_API_FORMAT,
        "settings.field.model": SETTINGS_FIELD_MODEL,
        "settings.field.base_url": SETTINGS_FIELD_BASE_URL,
        "settings.button.save_profile": SETTINGS_BUTTON_SAVE,
        "settings.field.template": SETTINGS_FIELD_TEMPLATE,
        "settings.field.auth_header": SETTINGS_FIELD_AUTH,
        "settings.wire": SETTINGS_WIRE_PANE,
        "settings.field.max_output_tokens": SETTINGS_FIELD_MAX_TOKENS,
        "settings.field.max_tokens_name": SETTINGS_FIELD_MAX_TOKENS_NAME,
        "settings.note.max_tokens_name": SETTINGS_NOTE_MAX_TOKENS_NAME,
        "settings.field.thinking": SETTINGS_FIELD_THINKING,
        "settings.field.thinking_name": SETTINGS_FIELD_THINKING_NAME,
        "settings.field.thinking_on": SETTINGS_FIELD_THINKING_ON,
        "settings.field.thinking_off": SETTINGS_FIELD_THINKING_OFF,
        "settings.note.thinking": SETTINGS_NOTE_THINKING,
        "settings.field.query": SETTINGS_FIELD_QUERY,
        "settings.note.query": SETTINGS_NOTE_QUERY,
        "settings.field.custom_url": SETTINGS_FIELD_CUSTOM_URL,
        "settings.field.custom_auth": SETTINGS_FIELD_AUTH,
        "settings.field.custom_headers": SETTINGS_FIELD_CUSTOM_HEADERS,
        "settings.note.custom_headers": SETTINGS_NOTE_CUSTOM_HEADERS,
        "settings.field.custom_body": SETTINGS_FIELD_CUSTOM_BODY,
        "settings.note.custom_body": SETTINGS_NOTE_CUSTOM_BODY,
        "settings.field.answer_path": SETTINGS_FIELD_ANSWER_PATH,
        "settings.note.answer_path": SETTINGS_NOTE_ANSWER_PATH,
        "settings.button.custom_export": SETTINGS_BUTTON_CUSTOM_EXPORT,
        "settings.button.custom_import": SETTINGS_BUTTON_CUSTOM_IMPORT,
        "settings.shape": SETTINGS_SHAPE_TITLE,
        "settings.field.cli_path": SETTINGS_FIELD_CLI_PATH,
        "settings.button.choose_cli": SETTINGS_BUTTON_CHOOSE_CLI,
        "settings.note.cli_path": SETTINGS_NOTE_CLI_PATH,
        "settings.field.timeout_seconds": SETTINGS_FIELD_TIMEOUT,
        "settings.field.cli_effort": SETTINGS_FIELD_CLI_EFFORT,
        "settings.note.thinking_cost": SETTINGS_NOTE_THINKING_COST,
        "settings.note.cli_login": SETTINGS_NOTE_CLI_LOGIN,
        "settings.note.codex_config": CODEX_CONFIG_NOTE,
        "settings.key": PROFILE_KEY_BOX,
        "settings.key.sources": KEY_SOURCES_TITLE,
        "settings.field.env_suffix": SETTINGS_FIELD_ENV_SUFFIX,
        "settings.button.save_key": SETTINGS_BUTTON_SAVE_KEY,
        "settings.button.delete_key": SETTINGS_BUTTON_DELETE,
        "settings.button.check": LLM_CHECK_TITLE,
        "settings.button.use": SETTINGS_BUTTON_USE,
        # Window tabs (ADR-0154 / REQ-214 section 5)
        "settings.tab.profiles": PROFILES_TITLE,
        "settings.tab.prompt": PROMPT_TEMPLATE_TITLE,
        "settings.tab.auto": SETTINGS_TAB_AUTO,
        "settings.tab.file": SETTINGS_TAB_FILE,
        "settings.note.auto_send": SETTINGS_NOTE_AUTO_SEND,
        "settings.note.save_both": SETTINGS_NOTE_SAVE_BOTH,
        "settings.note.character": SETTINGS_NOTE_CHARACTER,
        # The limit fields are in the "Compare and auto behavior" tab (ADR-0167 decision 5). Key names are unchanged; they point to pywebview-only texts
        "settings.config": CONFIG_PANE_WEB,
        "settings.config.limits": LIMITS_TITLE_WEB,
        "settings.config.per_job": LIMIT_PER_JOB_WEB,
        "settings.config.total": LIMIT_TOTAL_WEB,
        "settings.note.limits_moved": SETTINGS_NOTE_LIMITS_MOVED,
        "settings.config.words": SECRET_WORDS_TITLE,
        "settings.config.auto_compare": SETTINGS_AUTO_COMPARE,
        "settings.config.auto_send_llm": SETTINGS_AUTO_SEND_LLM,
        "settings.config.character": SETTINGS_CHARACTER,
        "settings.button.save_config": SETTINGS_BUTTON_SAVE,
        # The "AI instructions" frame (REQ-116 section 3. Separate from the settings file frame)
        "settings.prompt": PROMPT_TEMPLATE_TITLE,
        "settings.prompt.button.add": SETTINGS_BUTTON_NEW,
        "settings.prompt.button.duplicate": SETTINGS_BUTTON_DUPLICATE,
        "settings.prompt.button.delete": SETTINGS_BUTTON_DELETE,
        "settings.prompt.button.save": SETTINGS_BUTTON_SAVE,
        "settings.prompt.button.use": SETTINGS_BUTTON_USE,
        "settings.prompt.field.name": SETTINGS_FIELD_NAME,
        "settings.prompt.field.template": PROMPT_FIELD_TEMPLATE,
        "settings.note.prompt_builtin": PROMPT_BUILTIN_NOTE,
        # Notes under the fields (ADR-0167 decisions 1-3). The per-mark row keys are built below from PROMPT_MARKS
        "settings.note.prompt_diff_once": PROMPT_DIFF_ONCE_NOTE,
        "settings.prompt.marks": PROMPT_MARKS_TITLE,
        "settings.prompt.marks.col_mark": PROMPT_MARKS_COL_MARK,
        "settings.prompt.marks.col_meaning": PROMPT_MARKS_COL_MEANING,
        **{f"settings.prompt.mark.{name}.name": _mark(name) for name in PROMPT_MARKS},
        **{f"settings.prompt.mark.{name}": PROMPT_MARK_MEANINGS[name] for name in PROMPT_MARKS},
        "settings.note.prompt_plain": PROMPT_NOTE_PLAIN,
        "settings.note.prompt_premise": PROMPT_NOTE_PREMISE,
        # "Premise attached every time" in the "Prompt" tab (ADR-0172 decision 6 / REQ-234 section 5). The 3 guide lines come from the view
        "settings.premise": SETTINGS_PREMISE_TITLE,
        "settings.premise.button.save": SETTINGS_PREMISE_SAVE,
        "settings.diagnostics": SETTINGS_DIAGNOSTICS,
        "settings.diagnostics.fingerprints": FINGERPRINTS_TITLE,
        "settings.diagnostics.versions": SETTINGS_VERSIONS_TITLE,
        "settings.diagnostics.config_file": SETTINGS_CONFIG_FILE_TITLE,
        "settings.diagnostics.location": SETTINGS_CONFIG_LOCATION,
        "settings.button.open_folder": SETTINGS_BUTTON_OPEN_FOLDER,
        # "Export profiles…" and "Import profiles…" in the bottom row of the window (REQ-119 section 4)
        "settings.button.export_profiles": SETTINGS_BUTTON_EXPORT_PROFILES,
        "settings.button.import_profiles": SETTINGS_BUTTON_IMPORT_PROFILES,
        "settings.button.template": SETTINGS_BUTTON_TEMPLATE,
        "settings.button.close": SETTINGS_BUTTON_CLOSE,
        "sample.section.dialog": "Confirmation dialog",
        "sample.dialog.confirm": "Delete…",
        "sample.dialog.notice": "Show notice",
        "sample.dialog.confirm.title": "Delete profile",
        "sample.dialog.confirm.body": "Delete profile 'lab'?\nThe API key for this profile is also deleted.",
        "sample.dialog.notice.title": "AI profile",
        "sample.dialog.notice.body": "Saved profile 'lab'.",
        "sample.dialog.yes": DIALOG_YES,
        "sample.dialog.no": DIALOG_NO,
        "sample.dialog.ok": DIALOG_OK,
        "sample.dialog.answer.yes": "You chose 'Yes'.",
        "sample.dialog.answer.no": "You chose 'No'.",
    }
)


# --- HTML and CSV export from the window (ADR-0156 / REQ-216). pywebview compare and batch compare windows only. Not added to the CLI or wx ---
# Text for documents handed to other people. No sentence includes values other than paths (settings lines, secret words, AI answers)

EXPORT_BUTTON = "Export HTML/CSV…"
EXPORT_DIALOG_TITLE = "Export HTML/CSV"
# Body of the format-choice dialog, and the guide in the HTML (ADR-0156 decision 9)
EXPORT_GUIDE = (
    "Secret lines are masked by the same rule used when sending to the AI. Secrets without a keyword, IP addresses, host names, and descriptions are not masked. "
    "Check the contents before you hand it over."
)
# Format choices (ADR-0156 decision 1). The pair list is offered only for grouped compare and batch compare
EXPORT_CHOICE_HTML = "HTML"
EXPORT_CHOICE_DIFFS = "CSV (difference lines)"
EXPORT_CHOICE_PAIRS = "CSV (pair list)"
EXPORT_CHOICE_CANCEL = "Cancel"
# HTML title and headings
EXPORT_TITLE_COMPARE = "DriftPilot comparison result"
EXPORT_TITLE_BATCH = "DriftPilot batch comparison result"
EXPORT_SAVED_AT = "Exported at"
EXPORT_STATUS = "Status"
EXPORT_COUNTS = "Counts"
EXPORT_PAIRS = "Pair list"
EXPORT_STRUCTURED = "Differences (Structured)"
EXPORT_RAW = "Differences (Simple)"
EXPORT_ANSWER = "AI answer"
# Heading of the first request, shown after the AI answer heading and before the answer (ADR-0179 decision 1)
EXPORT_FIRST_REQUEST = "First request"
EXPORT_NO_ROWS = "No differences"
EXPORT_GAP = "…"
# CSV columns (those not in BATCH_COL_* and COMPARE_COL_*)
EXPORT_COL_PAIR = "Pair"
EXPORT_COL_GROUP = "Original pair"  # In the pair-list CSV, the pair that a command pair from a grouped compare belongs to (ADR-0178 decision 3)
EXPORT_COL_KIND = "Type"
EXPORT_COL_BEFORE_LINE = "Before line"
EXPORT_COL_AFTER_LINE = "After line"
EXPORT_KIND_STRUCTURED = COMPARE_TAB_STRUCTURED
EXPORT_KIND_RAW = COMPARE_TAB_RAW
# Notices (title is COMPARE_SAVE_FAILED_TITLE)
EXPORT_REDACT_FAILED = "Nothing was exported because secret lines cannot be masked."


def export_omitted(count: int) -> str:
    """Number of omitted lines in the HTML summary (ADR-0156 decision 2; line shapes are not shown)."""
    return f"The contents of {count:,} lines were not exported because they may contain secrets."


def export_settings_unreadable(reason: str) -> str:
    """Notice when settings cannot be read; reason is the SettingsError message."""
    return f"Nothing was exported because the settings cannot be read. This avoids exporting with missing secret keywords. {reason}"


def export_saved(path: Path) -> str:
    """Text at the bottom of the window after exporting."""
    return f"Exported to {path}. Secret lines were masked, but check the contents before you hand it over."


GUI_WEB_TEXTS.update(
    {
        "compare.button.export": EXPORT_BUTTON,
        "compare.export.title": EXPORT_DIALOG_TITLE,
        "compare.export.body": EXPORT_GUIDE,
        "batch.button.export": EXPORT_BUTTON,
        "batch.export.title": EXPORT_DIALOG_TITLE,
        "batch.export.body": EXPORT_GUIDE,
    }
)


# Show the AI pane in a separate window (ADR-0160 / REQ-220). pywebview compare window and AI window only. Not in CLI, batch compare, or wx
AI_PANE_TITLE = "DriftPilot — AI analysis"
COMPARE_AI_DETACH = "Open in a separate window"  # title and aria-label of the arrow icon at the right end of the AI pane heading
COMPARE_AI_FRONT = "Bring the separate window to front"  # title and aria-label of the arrow icon at the right end of the result tab row while the separate window is open
COMPARE_AI_DETACH_ICON = "↗"  # Both icons use the same character (set via data-text because the HTML holds no text)

GUI_WEB_TEXTS.update(
    {
        "compare.ai.detach": COMPARE_AI_DETACH,
        "compare.ai.front": COMPARE_AI_FRONT,
        "compare.ai.detach_icon": COMPARE_AI_DETACH_ICON,
    }
)


# --- Simple comparison settings (ADR-0166 / REQ-227). The CLI, compare window, settings window, and HTML export use the same words ---
# Sentences never include the user's patterns or replacement text (only counts and kinds; only the re.error message is added in parentheses)

RAW_OPTIONS_TITLE = "Simple comparison settings"
RAW_SETTINGS_PREFIX = "Simple comparison settings: "
RAW_FIELD_IGNORE = "Ignore lines"
RAW_FIELD_REPLACE = "Replacements"
RAW_FIELD_SPACE = "Whitespace differences"
# The four whitespace names (screen names in the table of ADR-0166 decision 3). Same as the table in llm_analyzer/payload.py (checked by tests)
RAW_SPACE_LABEL = {
    "off": "Compare whitespace",
    "trailing": "Ignore trailing whitespace",
    "amount": "Ignore amount of whitespace",
    "all": "Ignore all whitespace",
}
RAW_BLANK_LABEL = "Ignore blank lines"
RAW_CASE_LABEL = "Ignore case"
RAW_MARK_IGNORED = "Ignored"
RAW_MARK_EQUATED = "Same"
# Line moves and algorithm (REQ-228 section 8.3). Same words as llm_analyzer/payload.py (checked by tests)
RAW_MOVES_LABEL = "Detect moves"
RAW_ALGORITHM_PART = {"minimal": "Algorithm: minimal", "patience": "Algorithm: patience"}
RAW_ALGORITHM_FELL_BACK = "Algorithm: minimal (compared with standard because there are many changes)"
RAW_MOVES_COUNT = "Moves: {count}"
RAW_MOVES_SKIPPED = "Moves were not searched because there are many differences."
# Mark text in the table (REQ-228 section 8.2). {number} is the move number
RAW_MARK_MOVED_FROM = "Moved from {number}"
RAW_MARK_MOVED_TO = "Moved to {number}"
# Line in the CLI text output when moves were not searched
RAW_MOVES_SKIPPED_CLI = "Move detection: not searched because there are many difference lines"
# Error for the context line count of difference-only display (REQ-228 section 2)
RAW_CONTEXT_PROBLEM = "Set the number of context lines for difference-only display to 0-20."
RAW_FIELD_ALGORITHM = "Algorithm"
RAW_ALGORITHM_LABEL = {
    "standard": "Standard",
    "minimal": "Minimal (fewest changed lines)",
    "patience": "patience (easier to read for configs with many identical lines)",
}
# Batch compare window (REQ-228 section 7.2)
BATCH_RAW_NONE = "Simple comparison settings: none"
BATCH_RAW_PAIRS = "(Per-pair settings: {count} pair(s))"
BATCH_NOTE_RAW_OPTIONS = 'Change simple comparison settings in the defaults under "Simple comparison" in the settings window, or in raw_options in the pair file.'
RAW_SETTINGS_SAVED_PREFIX = "Saved result — "
RAW_OPTIONS_INVALID_TITLE = "Simple comparison settings"
RAW_DEFAULTS_TITLE = "Simple comparison defaults"
SETTINGS_TAB_RAW = "Simple comparison"
SETTINGS_NOTE_RAW_DEFAULTS = (
    "These are the settings used when the compare window opens. Settings changed in the compare window are used until you close that window. "
    "The command line (CLI) does not read these defaults."
)
SETTINGS_NOTE_SAVE_RAW = 'Save on this tab writes only raw-compare.json (fields on other tabs are not written).'

# Examples (description and pattern pairs). All pass check_raw_options (checked by tests)
RAW_IGNORE_EXAMPLES: tuple[tuple[str, str], ...] = (
    ('Lines with "! Last configuration change"', r"^! Last configuration change at "),
    ('Lines with "! NVRAM config last updated"', r"^! NVRAM config last updated at "),
    ('Lines with "Current configuration : … bytes"', r"^Current configuration : \d+ bytes"),
    ('Lines with "ntp clock-period"', r"^ntp clock-period "),
    ("Uptime lines", r"\buptime is "),
    ('FortiGate lines with "#config-version="', r"^#config-version="),
)
# Examples (description, pattern, replacement text)
RAW_REPLACE_EXAMPLES: tuple[tuple[str, str, str], ...] = (
    ("Replace times (12:34:56) with <time>", r"\b\d{1,2}:\d{2}:\d{2}(\.\d+)?\b", "<time>"),
    ("Replace uptime values with <uptime>", r"(uptime is ).*", r"\1<uptime>"),
    ("Replace numbers (counters, etc.) with <number> (matches every number)", r"\b\d+\b", "<number>"),
)


def raw_defaults_unreadable(reason: str) -> str:
    """Banner when the defaults file cannot be read; reason is the SettingsError message (location and kind of reason only)."""
    return f"Cannot read the simple comparison defaults file (raw-compare.json). Comparing with everything off: {reason}"


RAW_DEFAULTS_UNREADABLE = raw_defaults_unreadable("{reason}")  # Shape of the sentence (the window builds it with raw_defaults_unreadable)


def raw_defaults_saved(path: Path) -> str:
    return f"Saved to {path}."


def _raw_position(problem: Any) -> str:
    """Phrase locating the problem, e.g. the pattern on line 2 of Ignore lines, or the replacement text in pair 3 of Replacements."""
    if problem.field == "ignore_lines":
        return f"The pattern on line {problem.index} of {RAW_FIELD_IGNORE}"
    target = "replacement text" if getattr(problem, "part", None) == "replacement" else "pattern"
    return f"The {target} in pair {problem.index} of {RAW_FIELD_REPLACE}"


def raw_option_problem_line(problem: Any) -> str:
    """Sentence for one settings problem (RawOptionProblem). The pattern is not included."""
    from driftpilot.raw_keys import MAX_PATTERN_CHARS, MAX_PATTERNS

    kind = problem.kind
    if problem.field == "algorithm":
        return f"The {RAW_FIELD_ALGORITHM} value cannot be used (choose Standard, Minimal, or patience)."
    if problem.field == "whitespace":
        return f"The {RAW_FIELD_SPACE} value cannot be used (choose one of: {', '.join(RAW_SPACE_LABEL.values())})."
    if kind == "too_many":
        unit = "patterns" if problem.field == "ignore_lines" else "pairs"
        name = RAW_FIELD_IGNORE if problem.field == "ignore_lines" else RAW_FIELD_REPLACE
        return f"{name} allows up to {MAX_PATTERNS} {unit}."
    where = _raw_position(problem)
    if kind == "too_long":
        return f"{where} can be up to {MAX_PATTERN_CHARS} characters."
    if kind == "bad_pattern":
        return f"{where} cannot be read ({problem.detail})."
    if kind == "bad_replacement":
        return f"{where} cannot be used ({problem.detail})."
    if kind == "matches_empty":
        return f"{where} cannot be used because it also matches empty lines."
    if kind == "nested_repeat":
        return (
            f"{where} cannot be used because it repeats inside a repeat, which may make the comparison take too long"
            " (example: (a+)+)."
        )
    return f"{where} cannot be used."


def raw_option_problem_lines(problems: "Sequence[Any]") -> list[str]:
    """Sentences for the settings problems (one line per problem)."""
    return [raw_option_problem_line(p) for p in problems]


def raw_options_parts(plain: "dict[str, Any]", algorithm_used: str | None = None) -> list[str]:
    """Names of non-default settings (e.g. "Ignore lines: 2", "Replacements: 1", "Ignore amount of whitespace"). plain has the shape from options_to_plain.

    REQ-228: "Detect moves" and the algorithm (standard is not written) are added at the end. When algorithm_used
    (the algorithm actually used for the result) is given and minimal fell back to standard, the part is
    "Algorithm: minimal (compared with standard because there are many changes)".
    """
    parts: list[str] = []
    if plain.get("ignore_lines"):
        parts.append(f"{RAW_FIELD_IGNORE}: {len(plain['ignore_lines'])}")
    if plain.get("replacements"):
        parts.append(f"{RAW_FIELD_REPLACE}: {len(plain['replacements'])}")
    space = plain.get("whitespace", "off")
    if space != "off":
        parts.append(RAW_SPACE_LABEL.get(space, space))
    if plain.get("ignore_blank_lines"):
        parts.append(RAW_BLANK_LABEL)
    if plain.get("ignore_case"):
        parts.append(RAW_CASE_LABEL)
    if plain.get("detect_moves"):
        parts.append(RAW_MOVES_LABEL)
    algorithm = plain.get("algorithm", "standard")
    if algorithm == "minimal" and algorithm_used == "standard":
        parts.append(RAW_ALGORITHM_FELL_BACK)
    elif algorithm in RAW_ALGORITHM_PART:
        parts.append(RAW_ALGORITHM_PART[algorithm])
    return parts


def raw_options_summary_text(plain: "dict[str, Any]", algorithm_used: str | None = None) -> str | None:
    """Summary like "Simple comparison settings: Ignore lines: 2, Replacements: 1, Ignore amount of whitespace". None when all defaults."""
    parts = raw_options_parts(plain, algorithm_used)
    return RAW_SETTINGS_PREFIX + ", ".join(parts) if parts else None


def raw_moves_part(moves: int, skipped: bool) -> str | None:
    """Return the moves part ("2 moves" or the skipped notice), or None if there are no moves."""
    if skipped:
        return RAW_MOVES_SKIPPED
    return RAW_MOVES_COUNT.format(count=moves) if moves else None


def raw_move_mark(mark: str, number: int) -> str:
    """Return the table mark text; mark is "<" (moved from) or ">" (moved to)."""
    return (RAW_MARK_MOVED_FROM if mark == "<" else RAW_MARK_MOVED_TO).format(number=number)


def raw_move_cli_line(number: int, before_start: int, after_start: int, count: int) -> str:
    """Return one move line for the CLI text output."""
    return (
        f"Move {number}: Before lines {before_start}-{before_start + count - 1} -> "
        f"After lines {after_start}-{after_start + count - 1}"
    )


def batch_raw_pairs_suffix(count: int) -> str:
    """Return the suffix for the line under the batch compare window."""
    return BATCH_RAW_PAIRS.format(count=count)


def raw_set_aside_line(before: int, after: int, equated: int) -> str | None:
    """Return the ignored/equated lines summary, or None if all are 0."""
    if before == 0 and after == 0 and equated == 0:
        return None
    return (
        f"Ignored lines: {before + after} (Before {before}, After {after}), "
        f"lines treated as equal: {equated} {_plural(equated, 'pair', 'pairs')}"
    )


def raw_ignored_counts_line(before: int, after: int, equated: int) -> str:
    """Return the second line of the CLI text output."""
    return (
        f"Ignored lines: Before {before}, After {after}. "
        f"Lines treated as equal: {equated} {_plural(equated, 'pair', 'pairs')}"
    )


GUI_WEB_TEXTS.update(
    {
        "raw_options.title": RAW_OPTIONS_TITLE,
        "raw_options.ignore": "Lines to ignore (regular expressions, one per line)",
        "raw_options.ignore.note": (
            "Matching lines are not compared. Patterns that match an empty line (such as .*) can't be used. "
            'To skip blank lines, use "Ignore blank lines"'
        ),
        "raw_options.example": "Add from an example…",
        "raw_options.replace": "Replace before comparing (regular expressions)",
        "raw_options.replace.pattern": "Pattern",
        "raw_options.replace.text": "Replacement text",
        "raw_options.replace.add": "Add replacement",
        "raw_options.replace.remove": "Remove",
        "raw_options.space": RAW_FIELD_SPACE,
        "raw_options.space.off": RAW_SPACE_LABEL["off"],
        "raw_options.space.trailing": RAW_SPACE_LABEL["trailing"],
        "raw_options.space.amount": RAW_SPACE_LABEL["amount"],
        "raw_options.space.all": RAW_SPACE_LABEL["all"],
        "raw_options.space.indent_note": (
            "Indentation differences are also ignored. This can hide differences in the config hierarchy"
        ),
        "raw_options.blank": RAW_BLANK_LABEL,
        "raw_options.case": RAW_CASE_LABEL,
        "raw_options.reset": "Restore defaults",
        "raw_options.defaults_title": RAW_DEFAULTS_TITLE,
        "settings.tab.raw": SETTINGS_TAB_RAW,
        "settings.note.raw_defaults": SETTINGS_NOTE_RAW_DEFAULTS,
        "settings.note.save_raw": SETTINGS_NOTE_SAVE_RAW,
        "settings.button.save_raw": SETTINGS_BUTTON_SAVE,
        # REQ-228 section 8.4
        "raw_options.context": "Lines of context around differences (differences-only view)",
        "raw_options.moves": "Detect moved lines",
        "raw_options.moves.note": (
            'When a block of 3 or more lines moves elsewhere, it is shown as "Moved from" and "Moved to". '
            "Moves are also counted as differences"
        ),
        "raw_options.algorithm": RAW_FIELD_ALGORITHM,
        "raw_options.algorithm.standard": RAW_ALGORITHM_LABEL["standard"],
        "raw_options.algorithm.minimal": RAW_ALGORITHM_LABEL["minimal"],
        "raw_options.algorithm.patience": RAW_ALGORITHM_LABEL["patience"],
        "raw_options.algorithm.note": "Minimal falls back to standard when there are more than 1,000 changed lines",
        "batch.note.raw_options": BATCH_NOTE_RAW_OPTIONS,
    }
)


# --- Checking for a new version (ADR-0188 decision 2 / REQ-252) ---

# Reasons the check could not be done (REQ-252 section 3). update.check puts these in UpdateStatus.reason
UPDATE_REASON_NOT_FOUND = "No published version was found"
UPDATE_REASON_BUSY = "GitHub is busy. Please try again later"
UPDATE_REASON_UNREACHABLE = "Could not connect to GitHub. Check your network and proxy settings"
UPDATE_REASON_UNREADABLE = "Could not read the response from GitHub"


def update_reason_http(code: int) -> str:
    """Return the reason for HTTP errors other than 404, 403 and 429."""
    return f"No response from GitHub (HTTP {code})"


# "Update" tab of the settings window (REQ-252 section 4)
SETTINGS_TAB_UPDATE = "Update"
UPDATE_CHECKING = "Checking…"
# Note for users who installed from source (shown only when install_kind is "source"; ADR-0188 decision 3)
UPDATE_SOURCE_NOTE = 'If you installed from source, run git pull and then update with pip install -e ".[gui,llm]"'


def update_current_version(version: str) -> str:
    """Return the top line of the Update tab."""
    return f"Current version: {version}"


def update_is_latest(version: str) -> str:
    """Return the text for when the check shows the latest version."""
    return f"You have the latest version ({version})"


def update_is_newer(latest: str, published: str | None) -> str:
    """Return the text for when a newer version exists; omit parentheses if no publish date."""
    if published:
        return f"A new version {latest} is available (published {published})"
    return f"A new version {latest} is available"


def update_notice_text(latest: str, current: str) -> str:
    """Return the startup notice shown above the compare and batch compare windows."""
    return f"A new version {latest} is available (current: {current})"


GUI_WEB_TEXTS.update(
    {
        "settings.tab.update": SETTINGS_TAB_UPDATE,
        "settings.button.check_update": "Check for updates",
        "settings.button.open_releases": "Open the Releases page",
        "settings.config.auto_update_check": "Check for a new version at startup (automatic update check)",
        "settings.note.auto_update_check": (
            "When on, DriftPilot connects to GitHub (api.github.com) at every startup. "
            "Only the DriftPilot version is sent"
        ),
        # Notice above the compare and batch compare windows (REQ-252 section 5)
        "update_notice.open": "Update…",
        "update_notice.dismiss": "Close",
    }
)


# "Display" tab of the settings window (ADR-0189 / REQ-256 section 4). The Save text is the same settings.button.save_config as the Update tab
SETTINGS_TAB_DISPLAY = "Display"
DISPLAY_TITLE = "Screen colors"
DISPLAY_THEME_SYSTEM = "Match system settings"
DISPLAY_THEME_LIGHT = "Light mode"
DISPLAY_THEME_DARK = "Dark mode"
DISPLAY_FRAME_NOTE = "The color of the window frame (title bar) follows your OS settings"

GUI_WEB_TEXTS.update(
    {
        "settings.tab.display": SETTINGS_TAB_DISPLAY,
        "settings.display.title": DISPLAY_TITLE,
        "settings.display.system": DISPLAY_THEME_SYSTEM,
        "settings.display.light": DISPLAY_THEME_LIGHT,
        "settings.display.dark": DISPLAY_THEME_DARK,
        "settings.note.display_frame": DISPLAY_FRAME_NOTE,
    }
)


# --- Signed in-place update (ADR-0188 decisions 3 and 4 / REQ-253) ---

# Error texts (REQ-253 section 7 table). Held by the types in update.errors
UPDATE_SIGNATURE_FAILED = "Could not verify the signature of the download. Nothing was replaced"
UPDATE_INTEGRITY_FAILED = "The downloaded file is corrupted or is not the expected one. Nothing was replaced"
UPDATE_NO_ASSET = "There is no download for this OS"
UPDATE_NO_ASSET_MAC = "There is no download for this Mac"
UPDATE_WRONG_LOCATION = "Install DriftPilot in the Applications folder, then update"
UPDATE_CONTENT_FAILED = "Could not read the contents of the download. Nothing was replaced"
UPDATE_NOT_STARTED = "Could not start the update"


def update_not_writable(place: str) -> str:
    """Return the text for when the install location is not writable (REQ-253 section 5)."""
    return (
        f"DriftPilot in this location can't be replaced (cannot write to {place}). "
        "Please reinstall from the Releases page"
    )


# Progress of "Update" on the Update tab and the confirmation window (REQ-253 section 7)
UPDATE_TITLE = "Update"
UPDATE_VERIFYING = "Verifying…"
UPDATE_PREPARING = "Preparing…"
UPDATE_APPLY_YES = "Replace"
UPDATE_APPLY_NO = "Cancel"
_MIB = 1024 * 1024


def update_downloading(done: int, total: int) -> str:
    """Return download progress (1 MB = 1,048,576 bytes, one decimal place)."""
    return f"Downloading… {done / _MIB:.1f} / {total / _MIB:.1f} MB"


def update_apply_confirm(version: str) -> str:
    """Return the body of the confirmation window shown once the download is ready."""
    return (
        f"DriftPilot will close and be replaced with {version}. Unsaved comparison results will be lost. "
        "The new version opens after the replacement."
    )


def update_failed_notice(log: str) -> str:
    """Return the notice shown when started with --update-failed (above the compare windows)."""
    return f"The update failed, so the previous version was restored. Log: {log}"


def update_done_notice(version: str) -> str:
    """Return the notice shown when started with --update-started (above the compare windows)."""
    return f"Updated to {version}"


GUI_WEB_TEXTS.update(
    {
        "settings.button.start_update": "Update",
        "settings.button.cancel_update": "Cancel",
    }
)

# "Signature check" row of the self-check (REQ-253 section 10)
SELFCHECK_SIGNATURE = "Signature check"


def selfcheck_signature(marks: list[tuple[str, str]]) -> str:
    """Return the detail field when the signature check passed (e.g. Ed25519, main 5c50466f5b7455a6, backup 80db1dccd1059a79)."""
    return ", ".join(["Ed25519", *(f"{name} {mark}" for name, mark in marks)])


def selfcheck_signature_failed(what: str) -> str:
    """Return the text for when the signature check does not match (what is the name of the item checked)."""
    return f"Signature check failed: {what}"


# --- Display language (ADR-0190 / REQ-257): the language choice on the Display tab and the HTML lang ---
HTML_LANG = "en"


def gui_language_invalid(path: object) -> str:
    """When [gui] language cannot be written (a value other than the three allowed)."""
    return f"{path}: gui.language must be one of en, ja, system"


GUI_WEB_TEXTS.update(
    {
        "page.lang": HTML_LANG,
        "settings.display.language_title": "Language",
        "settings.display.language_en": "English",
        # Shown the same way in both languages, so people can find their own language.
        "settings.display.language_ja": "日本語",
        "settings.display.language_system": "Match system setting",
        "settings.note.display_language": "Restart DriftPilot to apply the language change.",
    }
)


# --- Batch compare screen logic (screen_model/batch_model.py, batch_edit.py) and the compare window's Python side (gui_web/compare_api.py) ---
SMB_SOURCE_FOLDERS = "Pick two folders and pair files with the same name"
SMB_SOURCE_MANIFEST = "Use a pair file"
SMB_MANIFEST_VALUES = "Uses the values written in the pair file (cannot be changed on this screen)"
SMB_LLM_CHECK_LABEL = "Analyze with AI (all pairs are confirmed together once before anything is sent)"
SMB_TEXT_WILDCARD = "Text (*.txt)|*.txt"
SMB_SAVE_ONLY_FOLDER_PAIRS = "Only pairs made from folders can be saved"


def smb_side_selection(before: str, after: str) -> str:
    """Table text when the sections picked on each side differ."""
    return f"Before {before} / After {after}"


def smb_manifest_error(count: int) -> str:
    """First line when the pair file cannot be read."""
    return f"Cannot read the pair file ({count} {_plural(count, 'problem', 'problems')}). No pairs were compared"


def smb_default_url(url: str) -> str:
    """Add the default mark to the destination when the setting is empty."""
    return f"{url} (default)"


def smb_unparsed_counts(before: int, after: int) -> str:
    """Table text for the number of lines that could not be structured."""
    return f"Before {before} / After {after}"


def smb_unknown_key(allowed: str) -> str:
    """Text for a key that cannot be edited in the window. allowed is the editable keys joined by ", "."""
    return f"This key cannot be edited in this window (editable keys: {allowed})"


CAPI_INPUT_FILE_TYPES = ("Text (*.txt;*.log;*.cfg;*.conf)", "All files (*.*)")
CAPI_OPEN_RESULT_FILE_TYPES = ("Saved results (*.json;*.jsonl)",)

# --- gui_web extras (gui_web/__init__.py, batch_api.py) ---
GW_ERROR_PREFIX = "Error: "
BAPI_SENTENCE_BREAK = ". "


# --- Texts of the settings screen logic (screen_model/settings_model.py) ---
SMS_ENV_SOURCE_LABEL = "environment variable"
SMS_KEY_SOURCE_KEYRING = "OS key store"
SMS_KEY_SOURCE_USER_CONFIG = "settings file"
SMS_KEY_SOURCE_NONE = "not set"
SMS_FILE_SOURCE_WORKDIR = "settings file in the work folder"
SMS_FILE_SOURCE_USER_CONFIG = "per-user settings file"
SMS_NOT_SET = "(not set)"
SMS_DEFAULT_MARK = "(default)"
SMS_UNREADABLE = "(cannot read settings)"
SMS_SECRET_WORDS_NOTE = "Lines containing these words are masked before they are sent to the AI"
SMS_PATTERN_TABLE_A = "Read table (Cisco family, Arista = type A)"
SMS_PATTERN_TABLE_FUJITSU_SIR = "Read table (Fujitsu Si-R)"
SMS_PATTERN_TABLE_FORTINET = "Read table (FortiGate)"
SMS_MASKING_TABLE = "Mask table"
SMS_AZURE_BASE_URL_EXAMPLE = "https://<resource-name>.openai.azure.com/openai/v1"
SMS_CONFIG_ABSENT = "none"
SMS_NOT_INSTALLED = "(not installed)"
SMS_UNKNOWN_TEMPLATE = "Unknown template type"


def sms_config_present(stamp: str) -> str:
    """The settings file exists (stamp is the modified time)."""
    return f"exists (modified {stamp})"


def sms_secret_words(words: "list[str]") -> str:
    """Summary of the masked words in effect (count and list)."""
    return f"{len(words)} {_plural(len(words), 'word', 'words')}" + (f": {', '.join(words)}" if words else "")


def sms_default_destination(url: str) -> str:
    """The default destination, shown when none is set."""
    return f"default ({url})"


def sms_no_profile(path: Path) -> str:
    return f"{path}: no profile with that id"


def sms_no_prompt_profile(path: Path) -> str:
    return f"{path}: no prompt profile with that id"


def sms_template_exists(path: Path) -> str:
    return f"{path}: already exists. Not overwritten"


def sms_append_note(message: str, note: str) -> str:
    """Follow a sentence with a note sentence."""
    return f"{message}. {note}"


# --- Screen texts of screen_model/model.py (SM_ / sm_) ---
SM_SIDE_LABEL = {"before": "Before", "after": "After"}
SM_MODE_BOTH = "Both"
SM_MODE_STRUCTURED = "Structured"
SM_MODE_RAW = "Simple"
# First device type choice when the type is config (not specified)
SM_PLATFORM_UNSPECIFIED = "Cisco family / Arista (not specified)"
SM_PASTED_NAME = {"before": "Pasted (Before)", "after": "Pasted (After)"}
# Progress step names (same order as STAGES in core)
SM_STAGE_LABEL = {
    "split": "Splitting into sections",
    "select": "Selecting sections",
    "detect_kind": "Detecting the input type",
    "compare": "Comparing",
    "safety_checks": "Running safety checks",
    "llm": "Running AI analysis",
    "done": "Showing the result",
}
SM_PICK_SEGMENT = "(select one)"
SM_UNKNOWN_COMMAND = "(unknown command)"
SM_WHOLE_ROW = "(whole line)"
SM_NO_INPUT = "None selected"
SM_CANCELLED = "Stopped"
SM_STALE_RESULT = "The input or settings have changed. The result shown is for the previous input. To save it, compare again"
# Short name for the device type when config has none specified, and the summary separator
SM_DEFAULT_PLATFORM_SHORT = "Cisco family"
SM_SUMMARY_SEPARATOR = ", "
# Separator between the parts of the simple compare settings summary (ignored lines, equated lines, moves, algorithm)
SM_SET_ASIDE_SEPARATOR = ", "
# Category shown in the "All" tab for a simple compare difference row
SM_ALL_RAW_CATEGORY = "(line)"
# "Structured" in the status text breakdown
SM_BREAKDOWN_STRUCTURED = "Structured"
SM_RAW_OFF_BY_MODE = 'Simple compare was not run because the compare mode is "Structured"'
SM_UNPARSED_OFF = "Not applicable because structured compare was not run"
SM_UNPARSED_NOTE = (
    "The list shows only lines that could not be structured and differ between Before and After. "
    "Lines that could not be structured and are the same on both sides are counted only in the heading"
)
SM_SELECT_KIND_PROMPT = (
    'Cannot tell the input type. Enter the "Device type" and the "show command", '
    "or set the type to config or show, then compare again"
)


def sm_input_lines(count: int) -> str:
    """Line count beside the input field."""
    return f"{count:,} {_plural(count, 'line', 'lines')}"


def sm_input_sections(count: int) -> str:
    """Section count appended after the line count."""
    return f" / {count} {_plural(count, 'section', 'sections')}"


def sm_count_structured(count: int) -> str:
    return f"Structured {count}"


def sm_count_show(count: int) -> str:
    return f"show Structured {count}"


def sm_count_raw(count: int) -> str:
    return f"Simple {count}"


def sm_unparsed_pair(before: int, after: int) -> str:
    """Counts of lines not structured, Before and After."""
    return f"Before {before} / After {after}"


def sm_count_unparsed(before: int, after: int) -> str:
    return f"Lines not structured Before {before} / After {after}"


def sm_count_warnings(count: int) -> str:
    return f"{_plural(count, 'Warning', 'Warnings')} {count}"


def sm_count_notices(count: int) -> str:
    return f"{_plural(count, 'Notice', 'Notices')} {count}"


def sm_show_fell_back(reason: str) -> str:
    """When show could not be read with a template and Simple compare was used."""
    return f"Could not read with the show template, so Simple compare was used: {reason}"


def sm_select_segment_prompt(side_labels: list[str]) -> str:
    """Prompt to choose a section, naming the sides (Before / After) that have several."""
    return f"{' and '.join(side_labels)} has more than one section. Choose the section to compare, then compare again"


def sm_auto_decided_config(platform: str) -> str:
    """Start of the summary when the device type is Auto and the input was judged to be config."""
    return f"{AUTO_PLATFORM_LABEL} (config{SM_SUMMARY_SEPARATOR}{platform})"


def sm_auto_decided_show() -> str:
    """Start of the summary when the device type is Auto and the input was judged to be show."""
    return f"{AUTO_PLATFORM_LABEL} (show)"


def sm_preview_summary(sent: int, total: int) -> str:
    """First line of the preview before sending (differences to send and total)."""
    return f"Sending {sent} of {total} {_plural(total, 'difference', 'differences')}"


def compare_pair_progress(index: int, total: int, command: str | None) -> str:
    """Progress text while comparing all commands (REQ-264 section 3), e.g. "Comparing (12 / 40): show ip route". An unknown command shows SM_UNKNOWN_COMMAND."""
    return f"Comparing ({index} / {total}): {command if command else SM_UNKNOWN_COMMAND}"


# --- Common prefixes of the commands (REQ-258) ---
CLI_ERROR_PREFIX = "Error: "
CLI_WARNING_PREFIX = "Warning: "


# --- Command descriptions (argparse description and help. REQ-258). `{default}` is filled in with .format by the caller ---
CLI_HELP = {
    "main.description": "Compare network device configurations and show the differences",
    "main.version": "Show the version and exit",
    "format_text": "Output format (default: text)",
    "format_markdown": "Output format (default: markdown)",
    "output": "Write to this file in UTF-8 instead of standard output",
    "compare.help": "Compare two files",
    "compare.before": "File before the change",
    "compare.after": "File after the change",
    "compare.mode": "Comparison mode (default: both)",
    "compare.kind": "Kind of input. auto decides from the content (default: config)",
    "compare.platform": (
        "For a config, choose a parser family other than the default. fujitsu_sir uses the flat config parser, "
        "and fortinet uses the FortiGate parser "
        "(--show-command is not needed. Leaving it out, or any other name, means the Cisco family, which needs no option). "
        "For a show output, give the device type as an ntc-templates platform name (e.g. cisco_ios), together with --show-command. "
        "auto decides the device type from the content of the config (--show-command is not needed. A show output is compared as plain text without a device type)"
    ),
    "compare.show_command": "The show command (e.g. 'show ip interface brief')",
    "compare.before_command": "Choose the segment of the before side by a partial match of the command text",
    "compare.before_segment": "Choose the segment of the before side by its position (starting from 1)",
    "compare.after_command": "Choose the segment of the after side by a partial match of the command text",
    "compare.after_segment": "Choose the segment of the after side by its position (starting from 1)",
    "compare.all_segments": (
        "Pair the segments of both sides by command and compare all of them. Pairs of a configuration command (such as running-config) are compared as a config, "
        "and the others as show output. --kind only affects pairs whose command is unknown. "
        "--save writes one line of JSON Lines per pair (segments without a partner are not included). "
        "As with the batch comparison, --llm only shows a summary of what would be sent, and sends only when --confirm is given. "
        "Cannot be used together with --before-segment, --after-segment, --before-command, --after-command, or --show-command"
    ),
    "compare.confirm": "With --all-segments --llm, send to the AI after checking the summary (use together with --all-segments and --llm)",
    "compare.llm_max_total_diffs": (
        "With --all-segments, the upper limit of the total number of differences sent to the AI across all pairs. If it is exceeded, nothing is sent. 1 or more "
        "(default: llm.max_total_diffs in the per-user settings file config.toml, otherwise {default})"
    ),
    "compare.prompt": "Prompt text (overrides the self-calibration. Applied to both sides)",
    "compare.rules": (
        "Normalization rule file. If omitted, the bundled rules for the device type are used (for a config with --platform fortinet, "
        "two rules, such as the one that excludes values marked ENC from the comparison, are on by default, and the number of excluded items is shown in the result). "
        "To turn these defaults off, pass your own rule file with --rules that does not include those rules or sets default_enabled: false"
    ),
    "compare.enable_rule": "Rule ID to enable (can be repeated). If given, the rules enabled by default are not used and only the IDs given here are used",
    "compare.save": "Save the comparison result as JSON. If the path is an existing directory, a name is given automatically. Never overwrites (default: not saved)",
    "compare.llm": "Send the differences to the AI and add an explanation of their meaning as annotations (credentials are masked. The differences do not change)",
    "compare.llm_dry_run": "Show what would be sent to the AI (after masking) and the shapes of the lines that are not sent. Nothing is sent",
    "compare.llm_max_diffs": (
        "Upper limit of the number of differences sent to the AI. 1 or more "
        "(default: llm.max_diffs in the per-user settings file config.toml, otherwise {default}). There is no way to make it unlimited"
    ),
    "compare.ignore_line": (
        "In the plain comparison, do not compare lines that match this regular expression (can be repeated). "
        "Affects only the plain comparison. The structured comparison does not change. The default settings file (Plain comparison in the settings screen) is not read"
    ),
    "compare.replace": "In the plain comparison, replace the part of a line that matches REGEX with TEXT before comparing (can be repeated. Applied from the top in order)",
    "compare.ignore_space": "In the plain comparison, ignore whitespace differences. trailing is at the end of a line, amount is the amount, all is everything (default: off)",
    "compare.ignore_blank_lines": "In the plain comparison, do not compare blank lines",
    "compare.detect_moves": "In the plain comparison, show blocks of 3 or more lines that moved to another place as 'Moved' (a move is also counted as a difference)",
    "compare.ignore_case": "In the plain comparison, do not distinguish upper and lower case",
    "compare.diff_algorithm": (
        "How the plain comparison works. minimal gives the difference with the fewest changed lines (standard when there are more than 1,000 changed lines), "
        "patience uses lines that appear exactly once on each side as anchors (default: standard)"
    ),
    "llm_profile": "Name of the AI profile to use only for this run (not case-sensitive). The settings of the profile in use do not change",
    "llm_prompt_profile": (
        "Name of the prompt profile to use only for this run (not case-sensitive. If omitted, the one in use). "
        "If there is no such name, it ends without comparing or sending"
    ),
    "patterns.help": "Output the list of what the config parser supports (the pattern table)",
    "patterns.platform": "Output the table of the flat config or FortiGate parser (default: the Cisco family table. docs/notes/flat-patterns.md, fortinet-patterns.md)",
    "coverage.help": "Measure the coverage of the config parser",
    "coverage.paths": "Config files, or directories of split files (reads _index.tsv)",
    "coverage.exposure": "Category when files are given directly (default: normal). For a directory it is decided by the command column of _index.tsv",
    "coverage.platform": "Measure with the flat config or FortiGate parser (default: the Cisco family parser)",
    "coverage.top": "Number of top entries for unparsed shapes and for blocks with many lines of the generic FortiGate shape (default: {default})",
    "coverage.files": "Number of file lines shown in text (default: {default})",
    "coverage_show.help": "Measure the coverage of the show side (identification rate and template coverage)",
    "coverage_show.paths": "Terminal log files, or directories of split files (reads _index.tsv)",
    "coverage_show.platform": "ntc-templates platform name (e.g. cisco_ios). If omitted, the template coverage is reported as 'not measurable'",
    "show_commands.help": "Output the denominator of the show-side coverage (the table of show commands to compare)",
    "mask_patterns.help": "Output the table of what is masked before sending to the AI (MSK)",
    "mask_coverage.help": "Measure the coverage of the masking table (sent as is / masked / nothing to mask / not sent)",
    "mask_coverage.paths": "Config files, or directories (reads *.txt below them)",
    "mask_coverage.top": "Number of top entries for the shapes that were not sent (default: {default})",
    "gui.help": "Open the comparison screen (needs pip install 'driftpilot[gui]')",
    "llm_check.help": "Send one short fixed text to the AI to check that the key and model connect (a very small charge may apply)",
    "llm_check.yes": "Skip the confirmation before sending (required when the input is not a terminal)",
}

# --- Error texts of the commands (after "Error: ". REQ-258) ---
CLI_ERR_TOP_NEGATIVE = "--top must be 0 or more"
CLI_ERR_TOP_FILES_NEGATIVE = "--top and --files must be 0 or more"
CLI_ERR_NO_FILES = "There are no files to measure"
CLI_ERR_NO_FILE_MEASURED = "No file could be measured"
CLI_ERR_NO_SEGMENTS = "There are no segments that can be compared"
CLI_ERR_SEGMENT_NOT_SELECTED = "Could not choose the segment"
CLI_ERR_SEGMENTS_AMBIGUOUS = "There are multiple segments. Choose one with --before-segment / --after-segment or similar"
CLI_ERR_SAVE_NEEDS_PATH = "--save needs a destination"
CLI_ERR_LLM_MAX_DIFFS_ALONE = "--llm-max-diffs must be given together with --llm or --llm-dry-run"
CLI_ERR_LLM_MAX_DIFFS_RANGE = "--llm-max-diffs must be 1 or more (there is no way to make it unlimited)"
CLI_ERR_CONFIRM_ALONE = "--confirm must be given together with --all-segments and --llm"
CLI_ERR_LLM_MAX_TOTAL_DIFFS_ALONE = "--llm-max-total-diffs must be given together with --all-segments and with --llm or --llm-dry-run"
CLI_ERR_LLM_MAX_TOTAL_DIFFS_RANGE = "--llm-max-total-diffs must be 1 or more (there is no way to make it unlimited)"


def cli_err_cannot_write(path: str, reason: str) -> str:
    """When an output file could not be written."""
    return f"{path}: could not write ({reason})"


def cli_err_platform_rejected(platform: str, reason: str) -> str:
    """When a --platform name that cannot be used is given to coverage-show. reason is why it cannot be used."""
    return f"--platform {platform} cannot be used. {reason}"


def cli_err_all_segments_conflict(names: list[str]) -> str:
    """When options (names) that cannot be used with --all-segments are given."""
    return f"--all-segments cannot be used together with {', '.join(names)}"


# --- Separators and names of the self-check (REQ-258) ---
SELFCHECK_CISCO_FAMILY = "Cisco family"
SELFCHECK_PART_SEPARATOR = ", "
SELFCHECK_ITEM_SEPARATOR = "; "

# --- Names and texts of the key store (REQ-258) ---
KEYSTORE_NAME_WINDOWS = "Windows Credential Manager"
KEYSTORE_NAME_MAC = "Mac Keychain"
KEYSTORE_NAME_OTHER = "OS key store"
KEYSTORE_BAD_PROFILE_ID = "The profile id has the wrong form"
KEYSTORE_KEY_REMAINS = "the key remains"

# --- Descriptions of the startup arguments of the screen (python -m driftpilot.gui_web. REQ-258) ---
GUIARG_SAMPLE = "Open the page of sample parts"
GUIARG_SETTINGS = "Open only the settings screen"
GUIARG_BATCH = "Open only the batch comparison screen"


# --- Descriptions, output and errors of driftpilot batch (REQ-258) ---
# argparse help. The {default} of --llm-max-diffs and --llm-max-total-diffs is filled in by the caller with the default count
CLI_BATCH_HELP = {
    "command": "Run the comparison pairs written in a manifest file, one after another (JSON Lines, one line per job)",
    "manifest": "Path of the manifest file (YAML)",
    "output": "Where to write the JSON Lines (default: standard output). An existing file is not overwritten",
    "summary": "Write the summary as JSON (includes the exit code). An existing file is not overwritten",
    "quiet": "Do not print the summary to standard error",
    "dry_run": "Only list the planned comparisons without running them (exit code 0)",
    "save": "Save each job's result as one file per job in this directory (default: do not save)",
    "pair_by_name": "Pair the files directly under the two folders whose names match exactly, and list them (does not compare). The device type of each pair is detected from its content (to use a fixed type, edit platform in the manifest written by --write-manifest).",
    "confirm": "Run the comparison for the pairs of --pair-by-name. With --llm, send to the AI after you have checked the summary",
    "write_manifest": "Write the pairs of --pair-by-name as a manifest file and finish",
    "llm": "Send the differences of each pair to the AI and add notes. Without --confirm, only the summary of what would be sent is shown and nothing is sent",
    "llm_dry_run": "Show what would be sent to the AI (after masking) and the summary before sending. Nothing is sent",
    "llm_max_diffs": "Maximum number of differences to send to the AI per pair. 1 or more (default: llm.max_diffs in the per-user settings file config.toml, otherwise {default})",
    "llm_max_total_diffs": "Maximum total number of differences to send to the AI across all pairs. If exceeded, nothing is sent. 1 or more (default: llm.max_total_diffs in the per-user settings file config.toml, otherwise {default})",
}

# Errors for combinations of arguments
CLI_BATCH_ERR_ONE_SOURCE = "Specify either MANIFEST or --pair-by-name BEFORE_DIR AFTER_DIR, not both and not neither"
CLI_BATCH_ERR_CONFIRM_WITH_WRITE = "--confirm and --write-manifest cannot be used together"
CLI_BATCH_ERR_WRITE_NEEDS_PAIR = "Use --write-manifest together with --pair-by-name"
CLI_BATCH_ERR_CONFIRM_NEEDS = "Use --confirm together with --pair-by-name or --llm"
CLI_BATCH_ERR_PAIR_NO_DRY_RUN = "--pair-by-name only lists the pairs by itself (--dry-run is not needed)"
CLI_BATCH_ERR_TOTAL_NEEDS_LLM = "Specify --llm-max-total-diffs together with --llm or --llm-dry-run"
CLI_BATCH_ERR_TOTAL_MIN = "--llm-max-total-diffs must be 1 or more (there is no way to specify unlimited)"
CLI_BATCH_ERR_LLM_ONLY_COMPARING = "--llm and --llm-dry-run can be used only when running the comparison (not together with --dry-run or --write-manifest)"
CLI_BATCH_ERR_SAME_PATH = "Specify different paths for --output and --summary"
CLI_BATCH_NO_PAIRS = "There are no paired files"
CLI_BATCH_PAIR_NOT_RUN = "The comparison has not been run. Add --confirm to run it for these pairs, or --write-manifest PATH to save them as a manifest file"
CLI_BATCH_SAVE_STOPPED = "Not saved because the run was stopped"
CLI_BATCH_NONE = "(none)"


def cli_batch_err_only_when_comparing(names: str) -> str:
    """Error when output destinations are given but no comparison runs. names is the list such as "--output, --summary"."""
    return f"{names} can be used only when running the comparison"


def cli_batch_err_path_needed(name: str) -> str:
    """Error when an output destination is empty."""
    return f"Specify a path for {name}"


def cli_batch_err_save_dir(path: str) -> str:
    """Error when the --save directory does not exist."""
    return f"--save {path}: specify an existing directory (it is not created automatically)"


def cli_batch_err_exists(name: str, path: str) -> str:
    """Error when the output destination already exists."""
    return f"{name} {path}: already exists. It is not overwritten"


def cli_batch_err_cannot_write(name: str, path: str, reason: str) -> str:
    """Error when the output destination cannot be written."""
    return f"{name} {path}: cannot write ({reason})"


def cli_batch_err_write_result(reason: str) -> str:
    """Error when the JSON Lines result cannot be written."""
    return f"Cannot write the result ({reason})"


def cli_batch_manifest_failed(count: int) -> str:
    """First line when the manifest file cannot be read (after the error prefix)."""
    return f"Cannot read the manifest file ({count} {_plural(count, 'problem', 'problems')}). No job was run"


def cli_batch_save_cut(target: str, partial_left: bool) -> str:
    """Text when saving was stopped halfway. partial_left says whether a half-written file may remain."""
    leftover = f". A half-written {target} may remain" if partial_left else ""
    return f"Stopped while saving{leftover}"


def cli_batch_file_state(found: bool) -> str:
    """Whether the file exists, in the --dry-run list."""
    return "found" if found else "not found"


def cli_batch_dry_head(path: str) -> str:
    """First line of the --dry-run list."""
    return f"Planned batch comparison (not run): {path}"


def cli_batch_dry_side(before: bool, path: str, state: str, selection: str) -> str:
    """Line for the before / after file in the --dry-run list. If before is False, it is After."""
    label = "Before" if before else "After"
    return f"  {label}: {path} ({state})  segment: {selection}"


def cli_batch_dry_total(total: int, missing: int) -> str:
    """Last line of the --dry-run list."""
    return f"Total {total} {_plural(total, 'job', 'jobs')}. Missing files: {missing}"


def cli_batch_manifest_written(path: str) -> str:
    """Text when the manifest file was written."""
    return f"Wrote the manifest file to {path}. The comparison has not been run"


# --- Text output of the commands (cli/render.py. REQ-258) ---
CLI_RENDER_SIDE_BEFORE = "Before"
CLI_RENDER_SIDE_AFTER = "After"
CLI_RENDER_NO_DIFF = "No differences"
# The structured value is absent (e.g. the "before" of an added row. REQ-276)
STRUCTURED_VALUE_NOT_SET = "(not set)"
CLI_RENDER_NONE = "none"
CLI_RENDER_NO_PROMPT = "none (not split)"
CLI_RENDER_UNKNOWN_COMMAND = "(unknown command)"
CLI_RENDER_UNPAIRED_HEAD = "=== Unpaired ==="
CLI_RENDER_ERROR_OUTPUT_HEAD = "=== Error output (not compared) ==="


def cli_render_unparsed(before: int, after: int) -> str:
    """Number of lines that could not be structured (before and after)."""
    return f"Lines that could not be structured: Before {before} / After {after}"


def cli_render_enabled_rules(rule_ids: list[str]) -> str:
    """The normalization rules enabled for the raw comparison. "none" if there are none."""
    return "Enabled rules: " + (", ".join(rule_ids) or CLI_RENDER_NONE)


def cli_render_applied(before: bool, rule_id: str, count: int) -> str:
    """Number of lines a normalization rule was applied to. If before is False, it is After."""
    label = CLI_RENDER_SIDE_BEFORE if before else CLI_RENDER_SIDE_AFTER
    return f"Applied ({label}): {rule_id} {count} {_plural(count, 'line', 'lines')}"


def cli_render_show_fallback(reason: str) -> str:
    """When the show comparison fell back to the raw comparison."""
    return f"Fell back to the raw comparison: {reason}"


def cli_render_split_head(label: str, name: str, prompt: str, source: str) -> str:
    """Heading of one side of the segment list (file name and prompt)."""
    return f"{label}: {name}  Prompt: {prompt} ({source})"


def cli_render_segment_line(mark: str, number: int, command: str, line_count: int) -> str:
    """One line of the segment list. mark is "*" for the selected segment, otherwise a space."""
    return f"  {mark} {number}. {command}  {line_count} {_plural(line_count, 'line', 'lines')}"


def cli_render_preamble(text: str) -> str:
    """Line for the preamble of a segment."""
    return f"      Preamble: {text}"


def cli_render_select(label: str, count: int) -> str:
    """Guidance for the side that has several segments. label is Before or After."""
    return f"{label} has {count} segments. Please select one:"


def cli_render_pair_head(label: str, before_number: int, after_number: int) -> str:
    """Heading of each pair in --all-segments. label looks like "1. show version"."""
    return f"=== {label} (Before segment {before_number} / After segment {after_number}) ==="


def cli_render_unpaired_line(before: bool, number: int, command: str, line_count: int) -> str:
    """Line of an unpaired segment in --all-segments. If before is False, it is After."""
    label = CLI_RENDER_SIDE_BEFORE if before else CLI_RENDER_SIDE_AFTER
    return f"{label} segment {number}: {command}  {line_count} {_plural(line_count, 'line', 'lines')}"


def cli_render_exposure(before_state: str, after_state: str) -> str:
    """Line for the check of the version with defaults. The states are normal, exposed and unknown (others are "Unknown")."""
    states = {"normal": "Normal version", "exposed": "Version with defaults", "unknown": "Unknown"}
    return (
        "Check for defaults: "
        f"{CLI_RENDER_SIDE_BEFORE} {states.get(before_state, states['unknown'])} / "
        f"{CLI_RENDER_SIDE_AFTER} {states.get(after_state, states['unknown'])}"
    )


# --- Connection profiles (llm-profiles.json, llm_profiles.py. REQ-258). Error texts fill {name} with .format. Values are never put in a text ---
# Written only when the first profile is newly created (the name in an existing file is never changed)
LLM_DEFAULT_PROFILE_NAME = "Default"
# Separator when listing the allowed values ("a / b")
LLMP_CHOICE_SEPARATOR = " / "
# Text for a destination that has a user name or password (no value is put in. REQ-199 section 2)
LLMP_URL_CREDENTIALS_RULE = "base_url must not contain a user name or password (store the key in the key vault)"
# The rule for the shape of env_suffix (follows "...env_suffix")
LLMP_ENV_SUFFIX_RULE = " must be 1 to 40 characters of uppercase letters, digits and _"

LLMP_ERRORS = {
    # Reading and writing files (shared with the prompt profiles, the bundle file and the settings file)
    "unreadable": "{path}: cannot be read ({reason})",
    "not_utf8": "{path}: cannot be read as UTF-8",
    "not_json": "{path}: cannot be read as JSON",
    "unwritable": "{path}: cannot be written ({reason})",
    "top_not_object": "{path}: the outermost value must be an object",
    "schema_version": "{path}: schema_version must be {version}",
    "already_exists": "{path}: already exists. Not overwriting",
    "where_not_object": "{where} must be an object",
    # Send options (prefix is "<place>: profiles[i]." and the like)
    "base_url_extras": "{prefix}base_url must not contain ? or # (put query values in query)",
    "auth_header": "{prefix}auth_header must be {choices}",
    "wire_shape": "{prefix}wire has the wrong shape",
    "max_output_tokens": "{prefix}max_output_tokens must be an integer of 1 or more",
    "wire_names_name": "{prefix}the name to send in wire_names must be a non-empty string",
    "thinking": "{prefix}thinking must be {choices}",
    "thinking_param_name": "{prefix}name in thinking_param must be a non-empty string",
    "query_shape": "{prefix}query must be an object from non-empty string keys to string values",
    "wire_reserved": "{prefix}{field} cannot use a reserved name",
    "wire_keylike": "{prefix}{field} must not contain a name that looks like a key (store the key in the key vault)",
    # custom
    "custom_missing": "{prefix}custom is missing ({format_name} needs a definition of the destination, authentication, body and answer location)",
    "custom_auth_choice": "auth must be {choices}",
    "custom_other": "{field} has the wrong shape",
    "custom_table": "{where} must be an object with exactly these 5 fields: {fields}",
    "custom_headers_table": "{where}.headers must be an object from strings to strings",
    "custom_string_field": "{where}.{name} must be a string",
    "custom_body_object": "{where}.body must be a JSON object",
    "custom_file_shape": "{path}: must be an object with only {mark} and {fields}",
    "custom_file_version": "{path}: {mark} must be {version}",
    # CLI fields
    "cli_path": "{prefix}cli_path must be a non-empty string",
    "timeout_seconds": "{prefix}timeout_seconds must be an integer of 1 or more",
    "cli_effort_not_cli": "{prefix}cli_effort can be written only for a CLI format (codex-cli, claude-code-cli)",
    "cli_effort_choice": "{prefix}cli_effort must be {choices}",
    # Fields of the file
    "wire_names_shape": '{where}.wire_names must look like {{"{token}": "<name to send>"}}',
    "thinking_param_shape": '{where}.thinking_param must look like {{"name": <name to send>, "on": <value>, "off": <value>}}',
    "where_query_shape": "{where}.query must be an object from non-empty string keys to string values",
    "where_max_output_tokens": "{where}.max_output_tokens must be an integer of 1 or more",
    "id_form": '{where}.id must be "default" or 32 hexadecimal digits',
    "where_name": "{where}.name must be a non-empty string",
    "where_api_format": "{where}.api_format must be {choices}",
    "where_model": "{where}.model must be a string",
    "where_base_url": "{where}.base_url must be a non-empty string or null",
    # Duplicates and the whole file
    "dup_id": "{path}: profiles[{index}].id is duplicated",
    "dup_name": "{path}: profiles[{index}].name is duplicated (upper and lower case are not told apart)",
    "dup_env_suffix": "{path}: profiles[{index}].env_suffix is duplicated",
    "profiles_array": "{path}: profiles must be a non-empty array",
    "active_id": "{path}: active must be an id in profiles",
    # Writing (for the settings screen)
    "missing_file": "{path}: does not exist. Create the first profile first",
    "no_such_profile": "{path}: there is no profile with the given id",
    "name_required": "{path}: name must be a non-empty string",
    "api_format": "{path}: api_format must be {choices}",
    "model_string": "{path}: model must be a string",
    "base_url_form": "{path}: base_url must be a non-empty string or null",
    "name_duplicate": "{path}: name is duplicated (upper and lower case are not told apart)",
    "env_suffix_duplicate": "{path}: env_suffix is duplicated",
    "last_profile": "{path}: the last profile cannot be deleted",
    "active_profile": "{path}: the profile in use cannot be deleted. Use another profile first",
}

# Texts for breaking a rule of custom (only which field. No value is put in. .format is not used). Keys are (field, kind)
LLMP_CUSTOM_PROBLEMS = {
    ("url", "empty"): "url must be a non-empty string",
    ("url", "fragment"): "url must not contain #",
    ("url", "invalid"): "url must be a URL without spaces or non-ASCII characters",
    ("url", "scheme"): "url must be https (http only for localhost)",
    ("url", "keylike"): "url must not contain a name that looks like a key in its query (store the key in the key vault)",
    ("headers", "shape"): "headers must be an object from strings to strings",
    ("headers", "name_empty"): "a name in headers cannot be empty",
    ("headers", "name"): "a name in headers has characters that cannot be used",
    ("headers", "value"): "a value in headers must be an ASCII string without line breaks",
    ("headers", "repeated"): "headers has the same name twice (upper and lower case are not told apart)",
    ("headers", "keylike"): "headers must not contain a name that looks like a key (store the key in the key vault)",
    ("headers", "own"): "headers must not contain a header that DriftPilot adds itself (such as Content-Type)",
    ("body", "object"): "body must be a JSON object",
    ("body", "unknown_mark"): "body has an unknown placeholder (only {{model}}, {{prompt}} and {{max_output_tokens}} can be used)",
    ("body", "no_prompt"): "body does not have {{prompt}}",
    ("answer_path", "empty"): "answer_path cannot be empty",
    ("answer_path", "shape"): "answer_path must look like choices.0.message.content",
}


# --- Error texts of the prompt profiles (prompt-profiles.json), the bundle file (profile_bundle.py) and the settings file (llm_settings.py) (REQ-258). Filled with .format. Values are never put in a text ---
PROMPTP_ERRORS = {
    "id_form": "{where}.id must be 32 hexadecimal digits",
    "profiles_array": "{path}: profiles must be an array",
    "active_id": "{path}: active must be the id of a built-in prompt or an id in profiles",
    "no_such_profile": "{path}: there is no prompt profile with the given id",
    "builtin_readonly": "{path}: a built-in prompt cannot be changed (duplicate it first and edit the copy)",
    "active_not_deletable": "{path}: the prompt in use cannot be deleted. Use another prompt first",
}

# Separator when listing words in the bundle file
BUNDLE_LIST_SEPARATOR = ", "
# Name of the place when the content to export is checked
BUNDLE_EXPORT_PLACE = "the content to export"
# How to say the place when it is the outermost level
BUNDLE_OUTERMOST = "the outermost level"
# Kinds of key-like things and the text for refusing them (neither the value nor an item name in the file is put in)
BUNDLE_KEY_KINDS = {
    "name": "has an item with a key-like name",
    "value": 'has a key-like value (a value starting with sk- or containing "Bearer ")',
    "header": "has an authentication header in its headers",
}
BUNDLE_NOT_ALLOWED = "API keys, cli_path, env_suffix, id and active cannot be included"
BUNDLE_ERRORS = {
    "too_deep": "{where}: nested too deeply",
    "key_found": "{where}: {place} {detail}. API keys cannot be put in the file",
    "too_big": "{path}: too large (up to 5 MB)",
    "top_fields": "{path}: the outermost value must be an object with only {fields} ({not_allowed})",
    "format": "{path}: format must be {format_name}",
    "version": "{path}: version must be {version}",
    "array": "{path}: {name} must be an array",
    "unknown_field": "{where} has an unknown item ({not_allowed})",
    "missing": "{where} is missing {names}",
    "wire_table": "{where}.wire must be an object with only {fields}",
    "prompt_table": "{path}: prompts[{index}] must be an object with only {fields}",
    "dup_name": "{path}: {label}[{index}].name is duplicated (upper and lower case are not told apart)",
    "changed": "{source}: changed after it was checked",
    "names_changed": "{source}: the name of an existing profile changed after it was checked",
}

# Errors of the settings file (config.toml). section is the name of a table such as [llm]
LLMSET_UNSET = "(not set)"
LLMSET_ERRORS = {
    "not_toml": "{path}: cannot be read as TOML",
    "section_not_table": "{path}: [{section}] must be a table",
    "non_empty_string": "{path}: {section}.{key} must be a non-empty string",
    "extra_secret_words": "{path}: {section}.extra_secret_words must be a list of words without spaces",
    "positive_int": "{path}: {section}.{key} must be an integer of 1 or more",
    "api_key_string": "{path}: {section}.api_key must be a non-empty string",
}


# --- Error texts of the saved record (storage/record.py) (REQ-258). The caller fills them with .format ---
STORE_TEXTS = {
    "none_label": "(none)",
    "top_level": "(top level)",
    "version_mismatch": "Not reading the saved file because its version differs ({where}: {shown}; version this tool can read: {expected}). Converting it could misread a missing item as absent",
    "source_path_empty": "source_paths[{side!r}] is empty. Use None when there is no path",
    "source_paths_keys": "The keys of source_paths must be before / after: {names}",
    "unread_sides_keys": "unread_sides must be before / after: {names}",
    "unread_sides_mismatch": "unread_sides must match error.detail.sides of a result whose input could not be read (error.stage is read_input) (unread_sides: {unread}; detail.sides: {declared})",
    "saved_at_timezone": "saved_at must be a datetime with a time zone",
    "exists": "{target}: already exists. Not overwriting",
    "no_folder": "{target}: the destination folder does not exist (it is not created automatically)",
    "cannot_write": "{target}: cannot write ({reason})",
    "not_utf8": "{path}: cannot be read as UTF-8",
    "cannot_read": "{path}: cannot read ({reason})",
    "not_json": "{path}: cannot be read as JSON: {error}",
    "file_not_object": "The top level of the saved file is not an object",
    "file_missing_items": "The saved file is missing items: {names}",
    "date_unreadable": "Cannot be read as a datetime: {value!r}",
    "no_timezone": "No time zone",
    "sha_line_both": "sha256 and line_count must be both null (the side whose input could not be read) or both values",
    "null_only_unread": "null is allowed only for a side listed in error.detail.sides of a result whose input could not be read (error.stage is read_input)",
    "hash_on_unread": "This side is listed as unread in error.detail.sides, but it has a hash",
    "str_or_null": "Must be a string or null: {value!r}",
    "raw_options_bad": "The simple comparison settings have the wrong shape or cannot be used",
    "not_object": "Not an object",
    "missing_turns": "Missing item: turns",
    "not_array": "Not an array",
    "missing_items": "Missing items: {names}",
    "unexpected_value": "Unexpected value: {value!r}",
    "no_type_matches": "Does not match any type: {value!r}",
    "count_differs": "Wrong number of elements ({actual}, expected {expected})",
    "not_bool": "Not a boolean",
    "not_int": "Not an integer",
    "not_str": "Not a string",
    "unsupported_type": "Type not supported by the loader: {tp!r}",
}


# --- Error texts of the update code (update/) and the reasons for refusing plan.json (REQ-258). The caller fills them with .format ---
UPDATE_TEXTS = {
    "plan_name": "name",
    "plan_unreadable": "unreadable",
    "plan_too_large": "too large",
    "plan_not_json": "not JSON",
    "plan_missing_field": "missing field",
    "plan_type": "type",
    "plan_kind": "install kind",
    "plan_version": "version",
    "plan_version_form": "version format",
    "plan_location": "location",
    "plan_location_file": "location of plan.json",
    "plan_staging_name": "name of the staging folder",
    "plan_staging_location": "location of the staging folder",
    "no_new_content": "There is no new content",
    "ditto_failed": "ditto exited with {code}",
    "url_not_api": "Not connecting to a URL that is not the GitHub API",
    "redirect_not_https": "Not following a redirect that is not https",
}

# --- Error texts of the premise file (premise.py) ---
PREMISE_TEXTS = {
    "too_long": "{path}: the premise can be at most {limit} characters",
    "cannot_read": "{path}: cannot read ({reason})",
    "not_utf8": "{path}: cannot be read as UTF-8",
    "cannot_write": "{path}: cannot write ({reason})",
}

# --- Error texts of the default simple comparison settings file (raw_settings.py) ---
RAWSET_TEXTS = {
    "cannot_read": "{path}: cannot read ({reason})",
    "not_utf8": "{path}: cannot be read as UTF-8",
    "not_json": "{path}: cannot be read as JSON",
    "not_object": "{path}: the outermost value must be a table (object)",
    "schema_version": "{path}: schema_version must be {version}",
    "no_raw_options": "{path}: raw_options is missing",
    "context_lines": "{path}: context_lines must be an integer from 0 to {maximum}",
    "kinds_separator": ", ",
    "bad_options": "{path}: raw_options has settings that cannot be used ({kinds})",
    "form_error": "{path}: {error}",
    "cannot_write": "{path}: cannot write ({reason})",
}

# --- Texts of the batch input and output (batch_io/) ---
BATCHIO_SIDE_LABEL = {"before": "Before", "after": "After"}
BATCHIO_TEXTS = {
    "cannot_read_input": "{label}: {path}: cannot read ({reason})",
    "not_utf8": "Cannot be read as UTF-8",
    "cannot_read": "Cannot read ({reason})",
    "not_yaml": "Cannot be read as YAML: {error}",
    "rules_not_utf8": "The normalization rules file cannot be read as UTF-8",
    "rules_cannot_read": "Cannot read the normalization rules file ({reason})",
    "manifest_exists": "{target}: already exists. Not overwriting",
    "manifest_no_folder": "{target}: the destination folder does not exist (it is not created automatically)",
    "manifest_cannot_write": "{target}: cannot write ({reason})",
    "no_directory": "{folder}: no such directory",
    "folder_cannot_read": "{folder}: cannot read ({reason})",
    "results_exists": "{target}: already exists. Not overwriting",
    "results_no_folder": "{target}: the destination folder does not exist (it is not created automatically)",
    "results_cannot_write": "{target}: cannot write ({reason})",
}

# --- Error texts of writing the settings file (user_config_writer.py) ---
UCW_TEXTS = {
    "not_writable_item": "These items cannot be written from the settings window: {names}",
    "not_removable_item": "This item cannot be removed from the settings window: {key}",
    "extra_secret_words": "{path}: {section}.extra_secret_words must be a list of words without whitespace",
    "positive_integer": "{path}: {section}.{key} must be an integer of 1 or more",
    "toml_unreadable": "{path}: cannot be read as TOML",
    "not_table": "{path}: [{name}] must be a table",
    "cannot_write": "{path}: cannot write ({reason})",
    "one_of": "{path}: {section}.{key} must be one of {choices}",
    "true_or_false": "{path}: {section}.{key} must be true or false",
}

# --- Errors about the shape of values from the window's fields (screen_model/model.py) ---
SMODEL_TEXTS = {
    "raw_form_shape": "The simple comparison settings have the wrong shape",
    "raw_ignore_shape": "The ignored lines have the wrong shape",
    "raw_replace_shape": "The replacements have the wrong shape",
    "raw_whitespace_shape": "The whitespace setting has the wrong shape",
    "raw_blank_case_shape": "The blank lines and letter case settings have the wrong shape",
    "raw_moves_shape": "The line moves setting has the wrong shape",
    "raw_algorithm_shape": "The comparison method has the wrong shape",
    "no_plan_or_preview": "There is no plan and preview that can be sent",
    "export_kind": "Wrong export form: {kind!r}",
}


# --- Split a log that contains several devices into one file per device and open it in Batch compare (ADR-0194 / REQ-267) ---
DEVICE_SPLIT_BUTTON = "Split by device and open in Batch compare…"
DEVICE_NAMES_LIMIT = 5  # at most this many device names per side in the notice (the rest is "and N more")
DEVICE_NONE = "none"
DEVICE_SPLIT_NO_DEVICE = "Cannot split: no device (prompt hostname) was found in the Before log or the After log"
DEVICE_SPLIT_SAME_LOG = "Cannot split: Before and After are the same text (pairing inside one log)"
DEVICE_SPLIT_BUSY = "Cannot split while comparing, sending, or while a batch comparison is running"
DEVICE_SPLIT_CONFIRM_TITLE = "Split by device and open in Batch compare"
DEVICE_SPLIT_CONFIRM_YES = "Choose a folder…"


def _device_names(names: Sequence[str]) -> str:
    """Device names of one side in the notice. Shows up to DEVICE_NAMES_LIMIT names and "and N more" for the rest."""
    if not names:
        return DEVICE_NONE
    shown = ", ".join(names[:DEVICE_NAMES_LIMIT])
    rest = len(names) - DEVICE_NAMES_LIMIT
    return f"{shown}, and {rest} more" if rest > 0 else shown


def device_notice_text(before: Sequence[str], after: Sequence[str]) -> str:
    """Notice of the compare window that the log has several devices. A side without devices shows "none"."""
    return f"This log contains more than one device (Before: {_device_names(before)} / After: {_device_names(after)})."


def device_split_confirm_body() -> str:
    """Body of the dialog before writing (with the note to treat the files like the original config)."""
    return (
        f"The files split by device: {save_caution()} "
        "Choose the folder to write to. A new folder is created under it (the original files are not changed)."
    )


def device_split_done(folder: Path, before_count: int, after_count: int) -> str:
    """Text under the window when writing finished."""
    return (
        f"Split by device: {folder} (Before: {before_count} {_plural(before_count, 'device', 'devices')}"
        f" / After: {after_count} {_plural(after_count, 'device', 'devices')})"
    )


def device_split_failed(reason: str, folder: Path | None) -> str:
    """When the files could not be written. Says that what was written so far was left (not deleted)."""
    base = f"Could not write the files for each device: {reason}"
    if folder is None:
        return base
    return f"{base}. What was written so far is left in {folder} (nothing was deleted)"


def device_split_not_opened(folder: Path) -> str:
    """When the files were written but could not be put into the Batch compare window."""
    return (
        f"Wrote the files for each device to {folder}. "
        "The folders were not put into Batch compare because its pairs are being edited or a comparison is running"
    )


GUI_WEB_TEXTS.update({"compare.split_devices": DEVICE_SPLIT_BUTTON})
