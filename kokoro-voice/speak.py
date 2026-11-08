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
  speak.py last-tty /dev/ttysNNN   Speak the last reply of the Claude session in that tab

Toggle: replies are only queued while ~/.claude/tts-on exists.
Config (env vars): KOKORO_VOICE (af_heart), KOKORO_SPEED (1.1),
KOKORO_MAX_CHARS (1200), KOKORO_PLAN_MAX_CHARS (8000), KOKORO_ANNOUNCE (1 = say the session title)
"""

import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOGGLE = Path.home() / ".claude" / "tts-on"
QUEUE = HERE / "queue"
LOCK = HERE / "player.lock"
PLAYING = HERE / "playing.json"
LAST = HERE / "last.json"
MIC = HERE / "mic-active"
PYTHON = HERE / "venv" / "bin" / "python"

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
    with open(path) as f:
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
        pgid = int(json.loads(PLAYING.read_text())["pgid"])
        os.killpg(pgid, signal.SIGTERM)
        for _ in range(40):  # wait for it to exit so its queue lock is free
            time.sleep(0.05)
            os.killpg(pgid, 0)
    except (FileNotFoundError, ValueError, KeyError, ProcessLookupError, PermissionError):
        pass
    PLAYING.unlink(missing_ok=True)


def ensure_player() -> None:
    subprocess.Popen(
        [str(PYTHON), str(Path(__file__).resolve()), "player"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


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
            body = Path(path).read_text() if path else ""
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
        subprocess.run(["afplay", "/System/Library/Sounds/Pop.aiff"])
    else:
        TOGGLE.touch()
        subprocess.run(["afplay", "/System/Library/Sounds/Tink.aiff"])


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
            player = subprocess.Popen(["afplay", str(wav)])
        if player:
            player.wait()
    finally:
        for f in tmp.glob("*.wav"):
            f.unlink(missing_ok=True)
        tmp.rmdir()


def load_kokoro():
    from kokoro_onnx import Kokoro

    return Kokoro(str(HERE / "kokoro-v1.0.int8.onnx"), str(HERE / "voices-v1.0.bin"))


def player() -> None:
    QUEUE.mkdir(exist_ok=True)
    lock = open(LOCK, "w")
    kokoro = None
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return  # another player is already draining the queue
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
            PLAYING.write_text(json.dumps({"pgid": os.getpgid(0), "session": item["session"]}))
            kokoro = kokoro or load_kokoro()
            text = f"From {item['title']}. {item['text']}" if ANNOUNCE and item.get("title") else item["text"]
            synth_and_play(kokoro, text)
        PLAYING.unlink(missing_ok=True)
        fcntl.flock(lock, fcntl.LOCK_UN)
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


def say() -> None:
    text = sys.stdin.read().strip()
    if text:
        synth_and_play(load_kokoro(), text)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "say"
    {
        "hook": hook, "plan": plan, "prompt": prompt, "shh": shh, "skip": skip, "toggle": toggle,
        "replay": replay, "mic-on": mic_on, "mic-off": mic_off, "player": player, "say": say,
        "read": read, "last-tty": last_tty,
    }[mode]()
