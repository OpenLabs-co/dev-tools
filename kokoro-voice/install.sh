#!/usr/bin/env bash
# Kokoro Voice for Claude Code — installer (macOS).
#
#   curl -fsSL https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice/install.sh | bash -s -- --hotkeys --iterm
#
# Options:
#   --hotkeys     build the ttsd helper (global hotkeys + mic pause) and start it at login
#   --iterm       add "Read Selection Aloud" / "Read Last Claude Reply" to iTerm2's right-click menu
#   --voice-off   install, but leave the voice switched off (turn it on with: tts on)
#   --no-test     skip the spoken test line at the end
#
# Safe to re-run. It backs up ~/.claude/settings.json and never removes your other hooks.
# Everything lands in ~/.claude/tts. Uninstall: ~/.claude/tts/uninstall.sh
set -euo pipefail

RAW="${KOKORO_VOICE_RAW:-https://raw.githubusercontent.com/OpenLabs-co/dev-tools/main/kokoro-voice}"
MODEL_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_SHA="6e742170d309016e5891a994e1ce1559c702a2ccd0075e67ef7157974f6406cb"
VOICES_SHA="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
KOKORO_ONNX_VERSION="0.6.1"
LABEL="com.openlabs.kokoro-voice"
LEGACY_LABEL="com.claude.ttsd"

DEST="$HOME/.claude/tts"
BIN="$HOME/.local/bin"
SETTINGS="$HOME/.claude/settings.json"

HOTKEYS=0 ITERM=0 VOICE=1 TEST=1
for arg in "$@"; do
  case "$arg" in
    --hotkeys) HOTKEYS=1 ;;
    --iterm) ITERM=1 ;;
    --voice-off) VOICE=0 ;;
    --no-test) TEST=0 ;;
    -h|--help) sed -n '2,13p' "$0" 2>/dev/null || true; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

say() { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die() { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. Preflight ─────────────────────────────────────────────────────────────
[ "$(uname -s)" = "Darwin" ] || die "This kit is for macOS (it plays audio with afplay)."

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$cand")"; break
  fi
done
[ -n "$PY" ] || die "Python 3.10 or later is required. Install it with: brew install python@3.12"
say "Using $("$PY" --version) at $PY"

command -v claude >/dev/null 2>&1 || warn "Claude Code ('claude') is not on your PATH. The hooks will still be installed."

mkdir -p "$DEST" "$DEST/queue" "$BIN" "$(dirname "$SETTINGS")"

# ── 2. Kit files (from a local checkout when run from one, else from GitHub) ─
HERE=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/speak.py" ]; then
  HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
fetch() { # fetch <repo path> <destination>
  if [ -n "$HERE" ]; then cp "$HERE/$1" "$2"; else curl -fsSL "$RAW/$1" -o "$2"; fi
}
say "Installing kit files into $DEST"
for f in speak.py ttsd.swift kokoro_read_aloud.py uninstall.sh; do fetch "$f" "$DEST/$f"; done
chmod +x "$DEST/uninstall.sh"

# ── 3. Python environment + Kokoro ───────────────────────────────────────────
if [ ! -x "$DEST/venv/bin/python" ]; then
  say "Creating a Python environment"
  "$PY" -m venv "$DEST/venv"
fi
say "Installing kokoro-onnx $KOKORO_ONNX_VERSION (this takes a minute the first time)"
"$DEST/venv/bin/python" -m pip install -q --disable-pip-version-check --upgrade pip
"$DEST/venv/bin/python" -m pip install -q --disable-pip-version-check "kokoro-onnx==$KOKORO_ONNX_VERSION" soundfile

sha_ok() { [ -f "$1" ] && [ "$(shasum -a 256 "$1" | cut -d' ' -f1)" = "$2" ]; }
get_model() { # get_model <file> <sha256>
  if sha_ok "$DEST/$1" "$2"; then return; fi
  say "Downloading $1"
  curl -fL --progress-bar "$MODEL_URL/$1" -o "$DEST/$1.part"
  sha_ok "$DEST/$1.part" "$2" || { rm -f "$DEST/$1.part"; die "Checksum mismatch for $1. Try again, or report it on GitHub."; }
  mv "$DEST/$1.part" "$DEST/$1"
}
get_model kokoro-v1.0.int8.onnx "$MODEL_SHA"
get_model voices-v1.0.bin "$VOICES_SHA"

# ── 4. Claude Code hooks (merged, never replaced) ────────────────────────────
say "Adding the Claude Code hooks to $SETTINGS"
"$PY" - "$SETTINGS" <<'PYEOF'
import json, os, shutil, sys, time
path = sys.argv[1]
settings = {}
if os.path.exists(path):
    with open(path) as f:
        text = f.read().strip()
    settings = json.loads(text) if text else {}
    shutil.copy2(path, f"{path}.bak-kokoro-{time.strftime('%Y%m%d-%H%M%S')}")

py = "~/.claude/tts/venv/bin/python ~/.claude/tts/speak.py"
wanted = [
    ("Stop", None, f"{py} hook", 10),
    ("UserPromptSubmit", None, f"{py} prompt", 5),
    ("PreToolUse", "ExitPlanMode", f"{py} plan", 10),
]
hooks = settings.setdefault("hooks", {})
for event, matcher, command, timeout in wanted:
    groups = hooks.setdefault(event, [])
    mode = command.rsplit(" ", 1)[1]
    present = any(
        "tts/speak.py" in h.get("command", "") and h.get("command", "").rstrip().endswith(f" {mode}")
        for g in groups for h in g.get("hooks", [])
    )
    if present:
        continue
    group = {"hooks": [{"type": "command", "command": command, "timeout": timeout}]}
    if matcher:
        group = {"matcher": matcher, **group}
    groups.append(group)

tmp = f"{path}.tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
PYEOF

# ── 5. `tts` and `shh` commands ──────────────────────────────────────────────
write_cmd() { # write_cmd <name> <body>; never clobbers someone else's command
  local target="$BIN/$1"
  if [ -e "$target" ] && ! grep -q "speak.py\|\.local/bin/tts" "$target" 2>/dev/null; then
    warn "$target already exists and isn't ours; leaving it alone."; return
  fi
  printf '%s\n' "$2" > "$target"; chmod +x "$target"
}
write_cmd tts '#!/bin/sh
# Kokoro Voice for Claude Code: tts on | off | skip | shh | replay | status
S="$HOME/.claude/tts/venv/bin/python $HOME/.claude/tts/speak.py"
case "$1" in
  on)   touch "$HOME/.claude/tts-on"; echo "voice on" ;;
  off)  rm -f "$HOME/.claude/tts-on"; $S shh; echo "voice off" ;;
  skip) $S skip ;;
  shh|stop) $S shh ;;
  replay) $S replay ;;
  status|"") [ -f "$HOME/.claude/tts-on" ] && echo "voice on" || echo "voice off"
        echo "queued: $(ls "$HOME"/.claude/tts/queue/*.json 2>/dev/null | wc -l | tr -d " ")" ;;
  *) echo "usage: tts on|off|skip|shh|replay|status" ;;
esac'
write_cmd shh '#!/bin/sh
exec "$HOME/.local/bin/tts" shh'
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH. Add it to use 'tts' and 'shh': echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc" ;; esac

if [ "$VOICE" = 1 ]; then touch "$HOME/.claude/tts-on"; else rm -f "$HOME/.claude/tts-on"; fi

# ── 6. Optional: global hotkeys (ttsd) ───────────────────────────────────────
if [ "$HOTKEYS" = 1 ]; then
  if ! xcode-select -p >/dev/null 2>&1 || ! command -v swiftc >/dev/null 2>&1; then
    warn "Skipping hotkeys: the Xcode Command Line Tools are missing. Run 'xcode-select --install', then re-run with --hotkeys."
  else
    say "Building the hotkey helper"
    swiftc -O "$DEST/ttsd.swift" -o "$DEST/ttsd"
    AGENTS="$HOME/Library/LaunchAgents"; PLIST="$AGENTS/$LABEL.plist"
    mkdir -p "$AGENTS"
    if [ -f "$AGENTS/$LEGACY_LABEL.plist" ]; then
      warn "An older ttsd LaunchAgent ($LEGACY_LABEL) is already installed; leaving it in place so the hotkeys don't fire twice."
    else
      cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$DEST/ttsd</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardErrorPath</key><string>$DEST/ttsd.log</string>
</dict></plist>
PLIST
      if [ -z "${KOKORO_VOICE_NO_LAUNCHCTL:-}" ]; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
        launchctl bootstrap "gui/$(id -u)" "$PLIST"
        say "Hotkeys are on: ⌃⌥T on/off · ⌃⌥S stop · ⌃⌥N skip · ⌃⌥R replay"
        echo "    Double-tap ⌃/⌥/⇧ needs Input Monitoring: System Settings → Privacy & Security → Input Monitoring → allow 'ttsd'."
      fi
    fi
  fi
fi

# ── 7. Optional: iTerm2 right-click menu ─────────────────────────────────────
if [ "$ITERM" = 1 ]; then
  ITERM_SCRIPTS="$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch"
  if [ ! -d "/Applications/iTerm.app" ] && [ ! -d "$HOME/Applications/iTerm.app" ]; then
    warn "Skipping the iTerm2 menu: iTerm2 isn't installed."
  else
    mkdir -p "$ITERM_SCRIPTS"
    ln -sf "$DEST/kokoro_read_aloud.py" "$ITERM_SCRIPTS/kokoro_read_aloud.py"
    say "iTerm2 menu installed. Two clicks are left for you (no restart needed):"
    echo "    1. iTerm2 → Settings → General → Magic → turn on 'Enable Python API'"
    echo "    2. Scripts → AutoLaunch → kokoro_read_aloud.py (accept the Python runtime download the first time)"
  fi
fi

# ── 8. Done ──────────────────────────────────────────────────────────────────
if [ "$TEST" = 1 ]; then
  say "Speaking a test line"
  echo "Kokoro voice is installed." | "$DEST/venv/bin/python" "$DEST/speak.py" read || warn "The test line failed; run it again with: tts replay"
fi

cat <<DONE

Kokoro Voice is installed. Start a new Claude Code session to hear replies.
  tts on | tts off     turn the voice on or off
  shh                  stop talking now
  tts replay           hear the last reply again
  Uninstall            ~/.claude/tts/uninstall.sh

If this saves you time, you can support it at https://openluxe.co/developers/tools/kokoro-voice
DONE
