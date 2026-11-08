"""Read Claude Code responses aloud with Kokoro (local, offline).

Every session's finished reply goes into one shared queue; a single player
process speaks them one at a time, announcing the session title first.

Modes:
  speak.py hook     Stop hook: queue the last reply (hook JSON on stdin)
  speak.py plan     PreToolUse hook on ExitPlanMode: queue the plan
  speak.py prompt   UserPromptSubmit hook: silence/drop this session's speech
  speak.py shh      Stop everything and clear the queue (also: `shh` on PATH)
  speak.py skip     Skip the current reply, keep the rest of the queue
  speak.py toggle   Turn voice on/off (plays a chime)
  speak.py replay   Say the last reply again
  speak.py mic-on   Mic opened (dictation): silence now, hold the queue
  speak.py mic-off  Mic closed: let the queue continue
  speak.py player   Internal: drain the queue
  speak.py say      Speak text from stdin, no title (testing)
  speak.py read     Speak text from stdin NOW (iTerm right-click "Read selection")
  speak.py last-tty /dev/ttysNNN   Speak the last reply of the Claude session in that tab (macOS/Linux)
  speak.py last     Speak the newest reply from any Claude session
  speak.py clip     Speak whatever text is on the clipboard (copy a selection, then run this)

Runs on macOS and Windows. macOS plays audio with afplay; Windows with the
built-in winsound module. Paths are the same: ~/.claude/tts on both.

Toggle: replies are only queued while ~/.claude/tts-on exists.
Config (env vars): KOKORO_VOICE (af_heart), KOKORO_SPEED (1.1),
KOKORO_MAX_CHARS (1200), KOKORO_PLAN_MAX_CHARS (8000), KOKORO_ANNOUNCE (1 = say the session title)
"""

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

IS_WIN = sys.platform == "win32"
if IS_WIN:
    import msvcrt
    import threading
    import winsound
    # Hook input and transcripts are UTF-8; Windows Python defaults to the ANSI
    # code page. pythonw has no stdin at all, hence the guard.
    if sys.stdin is not None:
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")
else:
    import fcntl

HERE = Path(__file__).resolve().parent
TOGGLE = Path.home() / ".claude" / "tts-on"
QUEUE = HERE / "queue"
LOCK = HERE / "player.lock"
PLAYING = HERE / "playing.json"
LAST = HERE / "last.json"
MIC = HERE / "mic-active"
# pythonw on Windows so the background player never opens a console window.
PYTHON = HERE / "venv" / ("Scripts/pythonw.exe" if IS_WIN else "bin/python")

VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
SPEED = float(os.environ.get("KOKORO_SPEED", "1.1"))
MAX_CHARS = int(os.environ.get("KOKORO_MAX_CHARS", "1200"))
PLAN_MAX_CHARS = int(os.environ.get("KOKORO_PLAN_MAX_CHARS", "8000"))
READ_MAX_CHARS = int(os.environ.get("KOKORO_READ_MAX_CHARS", "20000"))
SESSIONS = Path.home() / ".claude" / "sessions"
PROJECTS = Path.home() / ".claude" / "projects"
ANNOUNCE = os.environ.get("KOKORO_ANNOUNCE", "1") != "0"


def clean(text: str, limit: int = MAX_CHARS) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)           # fenced code
    text = re.sub(r"^\s*\|.*\|\s*$", " ", text, flags=re.M)      # table rows
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)        # links/images -> label
    text = re.sub(r"https?://\S+", "", text)                      # bare URLs
    text = re.sub(r"`([^`]*)`", r"\1", text)                      # inline code -> content
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)         # headings
    text = re.sub(r"^\s*([-*+]|\d+\.)\s+", "", text, flags=re.M)  # list markers
    text = re.sub(r"(\*\*|__|\*|_|~~)(\S.*?\S|\S)\1", r"\2", text)  # emphasis
    text = text.replace("~/", "home/").replace("→", " to ").replace("≈", " about ").replace("~", "about ")
    text = re.sub(r"[<>|#*_=]+", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", ". ", text).replace("\n", " ")
    text = re.sub(r"\.\s*\.", ".", text).strip()
    if len(text) > limit:
        cut = text[:limit]
        end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
        text = (cut[: end + 1] if end > limit // 3 else cut) + " ... The rest is on screen."
    return text


def read_transcript(path: str | None) -> tuple[str, str]:
    """Return (last assistant text, session title) from a transcript JSONL."""
    if not path or not os.path.exists(path):
        return "", ""
    last, ai_title, custom_title = "", "", ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"assistant"' not in line and 'Title"' not in line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            kind = entry.get("type")
            if kind == "ai-title":
                ai_title = entry.get("aiTitle") or ai_title
            elif kind == "custom-title":
                custom_title = entry.get("customTitle") or custom_title
            elif kind == "assistant":
                m = entry.get("message") or {}
                content = m.get("content")
                parts = [content] if isinstance(content, str) else [
                    c.get("text", "") for c in content or [] if c.get("type") == "text"
                ]
                joined = "\n".join(p for p in parts if p.strip())
                if joined:
                    last = joined
    return last, custom_title or ai_title


def spoken_title(title: str, cwd: str | None) -> str:
    if not title:
        title = Path(cwd).name if cwd else "Claude"
    return re.sub(r"[-_]+", " ", title).strip()


def kill_player() -> None:
    try:
        info = json.loads(PLAYING.read_text())
    except (FileNotFoundError, ValueError):
        PLAYING.unlink(missing_ok=True)
        return
    try:
        if IS_WIN:
            # TerminateProcess; the audio dies with it (winsound plays in-process),
            # and Windows releases the queue lock when the process ends.
            os.kill(int(info["pid"]), signal.SIGTERM)
            time.sleep(0.2)
        else:
            pgid = int(info["pgid"])
            os.killpg(pgid, signal.SIGTERM)
            for _ in range(40):  # wait for it to exit so its queue lock is free
                time.sleep(0.05)
                os.killpg(pgid, 0)
    except (ValueError, KeyError, ProcessLookupError, PermissionError, OSError):
        pass
    PLAYING.unlink(missing_ok=True)


def ensure_player() -> None:
    subprocess.Popen(
        [str(PYTHON), str(Path(__file__).resolve()), "player"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **detached(),
    )


def detached() -> dict:
    """Popen kwargs that let the player outlive the hook that started it."""
    if IS_WIN:
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True}


def chime(on: bool) -> None:
    if IS_WIN:
        winsound.MessageBeep(winsound.MB_OK if on else winsound.MB_ICONHAND)
    else:
        subprocess.run(["afplay", "/System/Library/Sounds/" + ("Tink.aiff" if on else "Pop.aiff")])


def queued(session: str | None = None) -> list[Path]:
    items = sorted(QUEUE.glob("*.json"))
    return [p for p in items if session is None or p.stem.endswith(f"-{session}")]


def hook() -> None:
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if not TOGGLE.exists() or payload.get("stop_hook_active"):
        return
    from_transcript, title = read_transcript(payload.get("transcript_path"))
    reply = payload.get("last_assistant_message")
    text = clean(reply if isinstance(reply, str) and reply.strip() else from_transcript)
    if not text:
        return
    enqueue(payload, title, text)


def enqueue(payload: dict, title: str, text: str) -> None:
    session = payload.get("session_id") or "unknown"
    QUEUE.mkdir(exist_ok=True)
    # A newer reply from the same session replaces one still waiting in line.
    for old in queued(session):
        old.unlink(missing_ok=True)
    item = {"session": session, "title": spoken_title(title, payload.get("cwd")), "text": text}
    tmp = QUEUE / f".{session}.tmp"
    tmp.write_text(json.dumps(item))
    tmp.rename(QUEUE / f"{time.time_ns()}-{session}.json")
    ensure_player()


def plan() -> None:
    """Plan mode finished: read the plan out (Stop does not fire while it waits for approval)."""
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return
    if not TOGGLE.exists():
        return
    tool_input = payload.get("tool_input") or {}
    body = tool_input.get("plan") or ""
    if not body.strip():
        path = tool_input.get("planFilePath")
        if not path:  # some calls carry no input; fall back to the newest plan file
            plans = sorted((Path.home() / ".claude" / "plans").glob("*.md"), key=lambda f: f.stat().st_mtime)
            path = plans[-1] if plans and time.time() - plans[-1].stat().st_mtime < 3600 else None
        try:
            body = Path(path).read_text(encoding="utf-8", errors="replace") if path else ""
        except OSError:
            body = ""
    # Headings and bullets have no closing punctuation; give each a pause.
    body = re.sub(r"^(\s*(?:#{1,6}|[-*+]|\d+\.)\s+.*?[\w)`*])\s*$", r"\1.", body, flags=re.M)
    text = clean(body, PLAN_MAX_CHARS)
    if not text:
        return
    _, title = read_transcript(payload.get("transcript_path"))
    enqueue(payload, title, "Here is the plan. " + text)


def prompt() -> None:
    """You typed in a session: drop its queued reply and stop it if it's talking."""
    try:
        session = json.load(sys.stdin).get("session_id")
    except ValueError:
        return
    if not session:
        return
    for p in queued(session):
        p.unlink(missing_ok=True)
    try:
        playing = json.loads(PLAYING.read_text()).get("session")
    except (FileNotFoundError, ValueError):
        return
    if playing == session:
        kill_player()
        if queued():
            ensure_player()


def shh() -> None:
    for p in queued():
        p.unlink(missing_ok=True)
    kill_player()


def skip() -> None:
    kill_player()
    if queued():
        ensure_player()


def toggle() -> None:
    if TOGGLE.exists():
        TOGGLE.unlink()
        shh()
        chime(False)
    else:
        TOGGLE.touch()
        chime(True)


def replay() -> None:
    if not LAST.exists():
        return
    kill_player()
    QUEUE.mkdir(exist_ok=True)
    # Queue at the front so it plays before anything already waiting.
    LAST.replace(QUEUE / f"0-{json.loads(LAST.read_text())['session']}.json")
    ensure_player()


def mic_on() -> None:
    MIC.touch()
    kill_player()


def mic_off() -> None:
    MIC.unlink(missing_ok=True)
    if queued():
        ensure_player()


def mic_busy() -> bool:
    # Ignore a flag left behind by a crashed watcher.
    try:
        return time.time() - MIC.stat().st_mtime < 600
    except FileNotFoundError:
        return False


class _Thread:
    """Gives a playback thread the same .wait() as a Popen."""

    def __init__(self, thread):
        self.thread = thread

    def wait(self):
        self.thread.join()


def play_async(wav: Path):
    """Start playing a wav and return something with .wait()."""
    if IS_WIN:
        t = threading.Thread(target=winsound.PlaySound, args=(str(wav), winsound.SND_FILENAME), daemon=True)
        t.start()
        return _Thread(t)
    return subprocess.Popen(["afplay", str(wav)])


def synth_and_play(kokoro, text: str) -> None:
    import soundfile as sf

    # Synthesize sentence groups one at a time so playback starts quickly and
    # the next chunk renders while the current one plays.
    chunks, buf = [], ""
    for s in re.split(r"(?<=[.!?])\s+", text):
        if buf and len(buf) + len(s) > 300:
            chunks.append(buf)
            buf = s
        else:
            buf = f"{buf} {s}".strip()
    if buf:
        chunks.append(buf)

    tmp = Path(tempfile.mkdtemp(prefix="kokoro-"))
    player = None
    try:
        for i, chunk in enumerate(chunks):
            samples, rate = kokoro.create(chunk, voice=VOICE, speed=SPEED, lang="en-us")
            wav = tmp / f"{i}.wav"
            sf.write(wav, samples, rate)
            if player:
                player.wait()
            player = play_async(wav)
        if player:
            player.wait()
    finally:
        for f in tmp.glob("*.wav"):
            f.unlink(missing_ok=True)
        tmp.rmdir()


def load_kokoro():
    from kokoro_onnx import Kokoro

    return Kokoro(str(HERE / "kokoro-v1.0.int8.onnx"), str(HERE / "voices-v1.0.bin"))


def lock_nb(fh) -> None:
    """Non-blocking exclusive lock; raises if another player holds it."""
    if IS_WIN:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(fh) -> None:
    if IS_WIN:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fh, fcntl.LOCK_UN)


def acquire(fh, seconds: float = 4.0) -> bool:
    """Take the queue lock, waiting briefly for a player that is being stopped.

    A stopped player can take a moment to die (longer on a busy machine). Giving
    up at once would strand whatever was queued behind it, because the dying
    player never looks at the queue again. A healthy player keeps the lock, so
    this only costs a few seconds of a redundant process.
    """
    deadline = time.time() + seconds
    while True:
        try:
            lock_nb(fh)
            return True
        except (BlockingIOError, OSError):
            if time.time() >= deadline:
                return False
            time.sleep(0.1)


def player() -> None:
    QUEUE.mkdir(exist_ok=True)
    lock = open(LOCK, "w")
    kokoro = None
    while True:
        if not acquire(lock):
            return  # another player is already draining the queue
        if not IS_WIN:  # Windows ends the process outright; kill_player tidies up instead
            signal.signal(signal.SIGTERM, lambda *_: (PLAYING.unlink(missing_ok=True), os._exit(0)))
        while items := queued():
            if mic_busy():  # you're dictating; wait until the mic closes
                time.sleep(0.3)
                continue
            path = items[0]
            try:
                item = json.loads(path.read_text())
            except (FileNotFoundError, ValueError):
                path.unlink(missing_ok=True)
                continue
            path.unlink(missing_ok=True)
            LAST.write_text(json.dumps(item))
            PLAYING.write_text(json.dumps({
                "pid": os.getpid(),
                "pgid": None if IS_WIN else os.getpgid(0),
                "session": item["session"],
            }))
            kokoro = kokoro or load_kokoro()
            text = f"From {item['title']}. {item['text']}" if ANNOUNCE and item.get("title") else item["text"]
            synth_and_play(kokoro, text)
        PLAYING.unlink(missing_ok=True)
        unlock(lock)
        # A reply may have been queued between our last check and the unlock,
        # while its hook's player saw the lock held and gave up. Re-check.
        if not queued():
            return


def play_now(text: str) -> None:
    """You asked for this: interrupt whatever is talking and speak it first.
    Ignores the on/off toggle, since a click is an explicit request."""
    if not text:
        return
    kill_player()
    QUEUE.mkdir(exist_ok=True)
    tmp = QUEUE / ".manual.tmp"
    tmp.write_text(json.dumps({"session": "manual", "title": "", "text": text}))
    tmp.rename(QUEUE / f"0-{time.time_ns()}-manual.json")
    ensure_player()


def terminal_text(text: str) -> str:
    """Strip Claude Code's on-screen chrome from text copied out of the terminal."""
    text = re.sub(r"[⏺●⎿│┃╭╮╰╯─━┌┐└┘├┤✻✽✶✳·⏵❯›]", " ", text)
    text = re.sub(r"\n[ \t]*\n", "\n\n", text)          # keep paragraph breaks
    return text


def read() -> None:
    play_now(clean(terminal_text(sys.stdin.read()), READ_MAX_CHARS))


def session_for_tty(tty: str) -> dict | None:
    """The Claude Code session running in a terminal tab, via ~/.claude/sessions/<pid>.json."""
    out = subprocess.run(["ps", "-t", tty.removeprefix("/dev/"), "-o", "pid="],
                         capture_output=True, text=True).stdout.split()
    found = []
    for pid in out:
        try:
            found.append(json.loads((SESSIONS / f"{pid}.json").read_text()))
        except (OSError, ValueError):
            continue
    found = [f for f in found if f.get("sessionId")]
    return max(found, key=lambda f: f.get("updatedAt", 0)) if found else None


def last_tty() -> None:
    tty = sys.argv[2] if len(sys.argv) > 2 else ""
    info = session_for_tty(tty) if tty else None
    transcripts = sorted(PROJECTS.glob(f"*/{info['sessionId']}.jsonl"),
                         key=lambda f: f.stat().st_mtime) if info else []
    text, _ = read_transcript(str(transcripts[-1])) if transcripts else ("", "")
    play_now(clean(text, READ_MAX_CHARS) if text else "There's no Claude reply in this tab yet.")


def last() -> None:
    """The newest reply from any session (Windows has no tab-to-session link)."""
    transcripts = sorted(PROJECTS.glob("*/*.jsonl"), key=lambda f: f.stat().st_mtime)
    text, _ = read_transcript(str(transcripts[-1])) if transcripts else ("", "")
    play_now(clean(text, READ_MAX_CHARS) if text else "There's no Claude reply yet.")


def clipboard_text() -> str:
    if IS_WIN:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"]
        out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                             creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        out = subprocess.run(["pbpaste"], capture_output=True, text=True)
    return out.stdout or ""


def clip() -> None:
    """Read the clipboard aloud: the Windows stand-in for "Read Selection Aloud"."""
    play_now(clean(terminal_text(clipboard_text()), READ_MAX_CHARS))


def say() -> None:
    text = sys.stdin.read().strip()
    if text:
        synth_and_play(load_kokoro(), text)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "say"
    {
        "hook": hook, "plan": plan, "prompt": prompt, "shh": shh, "skip": skip, "toggle": toggle,
        "replay": replay, "mic-on": mic_on, "mic-off": mic_off, "player": player, "say": say,
        "read": read, "last-tty": last_tty, "last": last, "clip": clip,
    }[mode]()
