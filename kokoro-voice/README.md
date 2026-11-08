# Kokoro Voice for Claude Code

Hear Claude Code's replies and plans read aloud by [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), a natural voice that runs entirely on your computer. There's no API key, no per-word cost, and nothing is sent anywhere.

Install page with a Claude Code install prompt: https://openluxe.co/developers/tools/kokoro-voice

Works on **macOS** and **Windows** (Windows support is new and in beta: please [open an issue](https://github.com/OpenLabs-co/dev-tools/issues) if anything goes wrong).

## Install on macOS

```sh
curl -fsSL https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice/install.sh | bash -s -- --hotkeys --iterm
```

Drop `--hotkeys` or `--iterm` if you don't want those extras. Requirements:

- macOS 13 or later, with Python 3.10 or later and Claude Code
- Xcode Command Line Tools, only for `--hotkeys`
- iTerm2 3.4 or later, only for `--iterm`. After installing, turn on iTerm2 → Settings → General → Magic → **Enable Python API**, then choose **Scripts → AutoLaunch → kokoro_read_aloud.py**.

## Install on Windows (beta)

In **PowerShell** (not as administrator):

```powershell
& ([scriptblock]::Create((irm https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice/install.ps1))) -Hotkeys
```

Or [download `install.ps1`](https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice/install.ps1) and run `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Hotkeys`, or [download the whole kit as a ZIP](https://github.com/OpenLabs-co/dev-tools/archive/refs/heads/main.zip) and run `kokoro-voice\install.ps1` from it.

Requirements:

- Windows 10 or 11, 64-bit
- Python 3.10 or later (64-bit): `winget install Python.Python.3.12`
- Claude Code

Drop `-Hotkeys` if you don't want global hotkeys. The installer needs no admin rights, is safe to re-run, backs up `%USERPROFILE%\.claude\settings.json`, and adds its hooks alongside your existing ones. Open a new terminal afterwards so `tts` and `shh` are on your PATH.

Windows Terminal has no right-click menu API, so on Windows you read a selection by copying it (**Ctrl+C**) and pressing **Ctrl+Alt+V**.

## What you get

- **Replies read aloud** when Claude Code finishes. Code blocks, tables and links are skipped. The session name is spoken first.
- **Plans read aloud** when Claude Code finishes a plan and waits for approval.
- **One queue for all sessions**, so ten sessions take turns instead of talking over each other.
- **It stops when you start:** typing into a session silences it. On macOS, dictation pauses everything too.
- **Global hotkeys:**

  | macOS (`--hotkeys`) | Windows (`-Hotkeys`) | Action |
  |---|---|---|
  | ⌃⌥T | Ctrl+Alt+T | Voice on or off |
  | ⌃⌥S | Ctrl+Alt+S | Stop and clear the queue |
  | ⌃⌥N | Ctrl+Alt+N | Skip to the next reply |
  | ⌃⌥R | Ctrl+Alt+R | Replay (macOS) / read the newest reply (Windows) |
  | Double-tap ⌃ / ⌥ / ⇧ | — | Stop / skip / replay (needs Input Monitoring) |
  | — | Ctrl+Alt+V | Read the clipboard aloud |

  On some Windows keyboard layouts Ctrl+Alt is the same as AltGr. If a hotkey clashes with a character you type, change `KEYS` in `ttsd_win.py`.

- **Read text aloud:** on macOS with iTerm2, right-click → **Read Selection Aloud** or **Read Last Claude Reply** (the newest reply in the tab you clicked). On any system, copy text and run `tts clip`.
- **Commands:** `tts on`, `tts off`, `tts replay`, `tts last`, `tts clip`, `tts status`, `shh`.

## How it works

| File | Role |
|---|---|
| `speak.py` | One file for both systems: the Claude Code hooks (`Stop`, `UserPromptSubmit`, `PreToolUse` on `ExitPlanMode`), the shared queue and the single player process. Plays audio with `afplay` on macOS and the built-in `winsound` on Windows. |
| `ttsd.swift` | macOS helper: global hotkeys, double-tap modifiers, and pausing while the mic is on. |
| `ttsd_win.py` | Windows helper: global hotkeys through the Win32 `RegisterHotKey` API. No extra packages or admin rights. Starts at sign-in from your Startup folder. |
| `kokoro_read_aloud.py` | iTerm2 (macOS) right-click menu. |
| `install.sh` / `uninstall.sh` | macOS install and removal. |
| `install.ps1` / `uninstall.ps1` | Windows install and removal. Hooks are written in Claude Code's exec form (the Python path plus arguments), so they work whether Claude Code uses Git Bash or PowerShell. |

Everything lives in `~/.claude/tts/` (`%USERPROFILE%\.claude\tts\` on Windows). The voice is on while the file `~/.claude/tts-on` exists.

## Settings

Environment variables, read when speech starts:

- `KOKORO_VOICE`: the voice (default `af_heart`).
- `KOKORO_SPEED`: the speaking speed (default `1.1`).
- `KOKORO_MAX_CHARS`: how much of a reply is read (default `1200`).
- `KOKORO_PLAN_MAX_CHARS`: how much of a plan is read (default `8000`).
- `KOKORO_READ_MAX_CHARS`: the limit for read-aloud of selections and the clipboard (default `20000`).
- `KOKORO_ANNOUNCE`: set to `0` to skip saying the session name.

## Uninstall

macOS:

```sh
~/.claude/tts/uninstall.sh          # everything, including the ~120 MB of model files
~/.claude/tts/uninstall.sh --keep   # hooks and helpers only
```

Windows (PowerShell):

```powershell
& "$env:USERPROFILE\.claude\tts\uninstall.ps1"         # everything
& "$env:USERPROFILE\.claude\tts\uninstall.ps1" -Keep   # hooks and helpers only
```

## Licenses

- The kit's code is MIT-licensed.
- The Kokoro model is Apache-2.0, and [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) is MIT.
- kokoro-onnx uses an eSpeak NG phonemizer, which is GPL-3.0. It is installed from PyPI into the kit's own Python environment and is not part of this repo.
