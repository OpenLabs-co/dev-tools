# Kokoro Voice for Claude Code

Hear Claude Code's replies and plans read aloud by [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), a natural voice that runs entirely on your Mac. There's no API key, no per-word cost, and nothing is sent anywhere.

Install page with a Claude Code install prompt: https://openluxe.co/developers/tools/kokoro-voice

## Install

```sh
curl -fsSL https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice/install.sh | bash -s -- --hotkeys --iterm
```

Drop `--hotkeys` or `--iterm` if you don't want those extras. Requirements:

- macOS 13 or later, with Python 3.10 or later and Claude Code
- Xcode Command Line Tools, only for `--hotkeys`
- iTerm2 3.4 or later, only for `--iterm`

The installer is safe to re-run. It backs up `~/.claude/settings.json` and adds its hooks alongside your existing ones.

## What you get

- **Replies read aloud** when Claude Code finishes. Code blocks, tables and links are skipped. The session name is spoken first.
- **Plans read aloud** when Claude Code finishes a plan and waits for approval.
- **One queue for all sessions**, so ten sessions take turns instead of talking over each other.
- **It stops when you start:** typing into a session silences it, and dictation pauses everything.
- **Global hotkeys** (`--hotkeys`):

  | Keys | Action |
  |---|---|
  | ⌃⌥T | Voice on or off |
  | ⌃⌥S | Stop and clear the queue |
  | ⌃⌥N | Skip to the next reply |
  | ⌃⌥R | Replay the last reply |
  | Double-tap ⌃ / ⌥ / ⇧ | Stop / skip / replay (needs Input Monitoring) |

- **iTerm2 right-click menu** (`--iterm`): **Read Selection Aloud** and **Read Last Claude Reply** (the newest reply in the tab you clicked). After installing, turn on iTerm2 → Settings → General → Magic → **Enable Python API**, then choose **Scripts → AutoLaunch → kokoro_read_aloud.py**.
- **Commands:** `tts on`, `tts off`, `tts replay`, `tts status`, `shh`.

## How it works

| File | Role |
|---|---|
| `speak.py` | Claude Code hooks (`Stop`, `UserPromptSubmit`, `PreToolUse` on `ExitPlanMode`), the shared queue and the single player process. |
| `ttsd.swift` | A small background helper: global hotkeys, double-tap modifiers, and pausing while the mic is on. |
| `kokoro_read_aloud.py` | iTerm2 Python API script that adds the two right-click items. It finds a tab's Claude session through `~/.claude/sessions/<pid>.json`. |
| `install.sh` / `uninstall.sh` | Install and remove everything. |

Everything lives in `~/.claude/tts/`. The voice is on while the file `~/.claude/tts-on` exists.

## Settings

Environment variables, read when speech starts:

- `KOKORO_VOICE`: the voice (default `af_heart`).
- `KOKORO_SPEED`: the speaking speed (default `1.1`).
- `KOKORO_MAX_CHARS`: how much of a reply is read (default `1200`).
- `KOKORO_PLAN_MAX_CHARS`: how much of a plan is read (default `8000`).
- `KOKORO_READ_MAX_CHARS`: the limit for right-click reads (default `20000`).
- `KOKORO_ANNOUNCE`: set to `0` to skip saying the session name.

## Uninstall

```sh
~/.claude/tts/uninstall.sh          # everything, including the ~120 MB of model files
~/.claude/tts/uninstall.sh --keep   # hooks and helpers only
```

## Licenses

- The kit's code is MIT-licensed.
- The Kokoro model is Apache-2.0, and [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) is MIT.
- kokoro-onnx uses an eSpeak NG phonemizer, which is GPL-3.0. It is installed from PyPI into the kit's own Python environment and is not part of this repo.
