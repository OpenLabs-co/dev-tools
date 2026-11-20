#!/usr/bin/env python3
"""iTerm2 right-click menu items that read terminal text aloud with Kokoro.

  Read Selection Aloud      the highlighted text in this tab. In Claude Code's fullscreen mode the
                            highlight is drawn by Claude Code (iTerm2 only sees the word under a
                            right-click), and Claude Code copies it to the clipboard on mouse release,
                            so a one-word or empty iTerm2 selection falls back to the clipboard
  Read Last Claude Reply    the newest reply of the Claude Code session in this tab

Both hand off to ~/.claude/tts/speak.py (its own venv), so they share the
hooks' queue, voice and `shh`/`skip` controls. Install by symlinking this
file into ~/Library/Application Support/iTerm2/Scripts/AutoLaunch/.
"""
import subprocess
from pathlib import Path

import iterm2

TTS = Path.home() / ".claude" / "tts"
SPEAK = [str(TTS / "venv" / "bin" / "python"), str(TTS / "speak.py")]


def clipboard() -> str:
    try:
        return subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=2).stdout or ""
    except Exception:
        return ""


def pick_text(selected: str) -> str:
    """The text to read: iTerm2's selection, unless it is empty or one word (fullscreen Claude Code owns the
    highlight and a right-click makes iTerm2 select the word under the pointer); then the clipboard, where
    Claude Code puts the highlight on mouse release, when it holds that word or iTerm2 saw nothing."""
    sel = (selected or "").strip()
    if sel and any(ch.isspace() for ch in sel):
        return sel
    clip = clipboard().strip()
    if clip and (not sel or sel.lower() in clip.lower()) and len(clip) > len(sel):
        return clip
    return sel


def spawn(args, stdin_text=None):
    p = subprocess.Popen(SPEAK + args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    p.communicate((stdin_text or "").encode())


async def main(connection):
    app = await iterm2.async_get_app(connection)

    @iterm2.ContextMenuProviderRPC
    async def kokoro_read_selection(session_id=iterm2.Reference("id")):
        session = app.get_session_by_id(session_id)
        if not session:
            return
        selection = await session.async_get_selection()
        text = pick_text(await session.async_get_selection_text(selection))
        if text:
            spawn(["read"], text)

    @iterm2.ContextMenuProviderRPC
    async def kokoro_read_last_reply(session_id=iterm2.Reference("id")):
        session = app.get_session_by_id(session_id)
        if not session:
            return
        tty = await session.async_get_variable("tty")
        if tty:
            spawn(["last-tty", tty])

    await kokoro_read_selection.async_register(
        connection, display_name="Read Selection Aloud",
        unique_identifier="com.openlabs.kokoro-voice.read-selection")
    await kokoro_read_last_reply.async_register(
        connection, display_name="Read Last Claude Reply",
        unique_identifier="com.openlabs.kokoro-voice.read-last-reply")


iterm2.run_forever(main)
