#!/usr/bin/env python3
"""iTerm2 right-click menu items that read terminal text aloud with Kokoro.

  Read Selection Aloud      the highlighted text in this tab
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
        text = await session.async_get_selection_text(selection)
        if text and text.strip():
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
