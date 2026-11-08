"""ttsd for Windows: global hotkeys for Kokoro Voice (speak.py).

  Ctrl+Alt+T  voice on/off          Ctrl+Alt+S  stop and clear the queue
  Ctrl+Alt+N  skip to the next one  Ctrl+Alt+R  read the newest Claude reply
  Ctrl+Alt+V  read the clipboard aloud (select text, Ctrl+C, then Ctrl+Alt+V)

Uses the Win32 RegisterHotKey API through ctypes: no extra packages, no admin
rights, no keyboard hook. install.ps1 -Hotkeys starts it at sign-in from the
Startup folder with pythonw.exe, so it has no window.

Note: Ctrl+Alt equals AltGr on some keyboard layouts. If a hotkey collides with
a character you type, change KEYS below.
"""

import ctypes
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHONW = HERE / "venv" / "Scripts" / "pythonw.exe"
SPEAK = HERE / "speak.py"
LOG = HERE / "ttsd.log"

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY = 0x0312

# (virtual-key code, speak.py mode)
KEYS = [
    (ord("T"), "toggle"),
    (ord("S"), "shh"),
    (ord("N"), "skip"),
    (ord("R"), "last"),
    (ord("V"), "clip"),
]


def log(msg: str) -> None:
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(msg + "\n")


def speak(mode: str) -> None:
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen([str(PYTHONW), str(SPEAK), mode], creationflags=flags, close_fds=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    if sys.platform != "win32":
        print("ttsd_win.py is for Windows; on macOS use ttsd.swift.")
        return 1

    user32 = ctypes.windll.user32
    registered = {}
    for i, (vk, mode) in enumerate(KEYS, start=1):
        if user32.RegisterHotKey(None, i, MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, vk):
            registered[i] = mode
        else:
            log(f"ttsd: Ctrl+Alt+{chr(vk)} is taken by another app; '{mode}' has no hotkey")
    if not registered:
        log("ttsd: no hotkeys could be registered; exiting")
        return 1
    log("ttsd: listening for " + ", ".join(f"Ctrl+Alt+{chr(KEYS[i - 1][0])}" for i in registered))

    msg = wintypes.MSG()
    try:
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY and msg.wParam in registered:
                speak(registered[msg.wParam])
    finally:
        for i in registered:
            user32.UnregisterHotKey(None, i)
    return 0


if __name__ == "__main__":
    sys.exit(main())
