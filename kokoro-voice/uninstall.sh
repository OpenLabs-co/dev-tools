#!/usr/bin/env bash
# Kokoro Voice for Claude Code — uninstaller.
#   ~/.claude/tts/uninstall.sh          remove hooks, hotkeys, iTerm2 menu and the kit (~120 MB of model files)
#   ~/.claude/tts/uninstall.sh --keep   remove the hooks and helpers, keep ~/.claude/tts (fast reinstall)
set -euo pipefail
KEEP=0; [ "${1:-}" = "--keep" ] && KEEP=1
DEST="$HOME/.claude/tts"
SETTINGS="$HOME/.claude/settings.json"
LABEL="com.openlabs.kokoro-voice"

if [ -f "$SETTINGS" ]; then
  python3 - "$SETTINGS" <<'PYEOF'
import json, os, shutil, sys, time
path = sys.argv[1]
with open(path) as f:
    settings = json.load(f)
shutil.copy2(path, f"{path}.bak-kokoro-uninstall-{time.strftime('%Y%m%d-%H%M%S')}")
hooks = settings.get("hooks", {})
for event in list(hooks):
    kept = []
    for group in hooks[event]:
        group["hooks"] = [h for h in group.get("hooks", []) if "tts/speak.py" not in h.get("command", "")]
        if group["hooks"]:
            kept.append(group)
    if kept:
        hooks[event] = kept
    else:
        del hooks[event]
if not hooks:
    settings.pop("hooks", None)
tmp = f"{path}.tmp"
with open(tmp, "w") as f:
    json.dump(settings, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
PYEOF
  echo "Removed the Kokoro hooks from $SETTINGS (a backup is next to it)."
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
rm -f "$HOME/Library/Application Support/iTerm2/Scripts/AutoLaunch/kokoro_read_aloud.py"
for cmd in tts shh; do
  f="$HOME/.local/bin/$cmd"
  if [ -f "$f" ] && grep -q "speak.py\|\.local/bin/tts" "$f"; then rm -f "$f"; fi
done
rm -f "$HOME/.claude/tts-on"
[ -f "$DEST/playing.json" ] && "$DEST/venv/bin/python" "$DEST/speak.py" shh 2>/dev/null || true

if [ "$KEEP" = 0 ]; then rm -rf "$DEST"; echo "Removed $DEST."; fi
echo "Kokoro Voice is uninstalled. Restart iTerm2's script (Scripts → Manage) or iTerm2 itself to drop the menu items."
