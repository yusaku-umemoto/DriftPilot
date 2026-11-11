# Troubleshooting

[Back to the guide](README.md)

## A warning appears the first time I open DriftPilot

DriftPilot is not code-signed by Apple or Microsoft, so the OS warns you once.

- **Windows:** "Windows protected your PC" → **More info** → **Run anyway**.
- **Mac:** "cannot verify the developer" → **System Settings → Privacy & Security** → **Open Anyway**.

Windows 10 may also need the Microsoft Edge WebView2 Runtime.

## The screen is in English and I want Japanese

**Settings… → Display → Language → 日本語 → Save**, then restart DriftPilot.

## "Check for updates" says "No published version was found"

The update check reads the latest release on GitHub. If you see this, check your network or proxy settings, or try again later.

## Updating failed

If an update cannot finish, DriftPilot puts the previous version back and shows "The update failed, so the previous version was restored. Log: …" with the path of a log file. Close every DriftPilot window (including the command-line version) and try again. You can always download the latest version from the [Releases page](https://github.com/yusaku-umemoto/DriftPilot/releases) and replace it by hand:

- **Windows:** unzip the new version to a new folder and copy the old `settings` folder next to the new `DriftPilot.exe`.
- **Mac:** drag the new `DriftPilot.app` to Applications and replace the old one.

Your settings and API keys stay.

## AI analysis does not work

1. In **Settings… → AI profiles**, check the kind, model and endpoint of the profile in use.
2. Save the API key with **Save to key store**.
3. Click **AI connection test**.
4. On the command line, `driftpilot llm-check` does the same check.

If your company uses a proxy, set the usual proxy environment variables (`HTTPS_PROXY` and so on). The update check also follows the OS proxy settings.

## Where are my settings?

- **Windows zip:** the `settings` folder next to `DriftPilot.exe`.
- **Mac:** `~/Library/Application Support/DriftPilot/`.
- **API keys:** Windows Credential Manager or Mac Keychain. Delete them with **Delete…** in the settings window.

**Settings… → Open folder** opens the settings folder.
