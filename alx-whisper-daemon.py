#!/usr/bin/env python3
"""
alx-whisper daemon — GNOME Wayland edition (no root, no input group)
- Registers a GNOME custom keyboard shortcut via gsettings
- Shortcut sends SIGUSR1 to this process
- Records audio from default mic on toggle
- POSTs to OpenAI-compatible /audio/transcriptions
- Injects result via clipboard + simulated Ctrl+V
"""

from __future__ import annotations

import io
import os
import sys
import time
import wave
import signal
import logging
import threading
import subprocess
import json
from pathlib import Path

# ---------- audio deps (must be available after install.sh) ----------
try:
    import sounddevice as sd
except Exception as e:
    sys.stderr.write(f"[alx-whisper] audio deps missing: {e}\n")
    sys.exit(1)

import requests

# ---------- config from environment ----------
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "alx-whisper"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
PID_FILE = RUNTIME_DIR / "alx-whisper.pid"
LOG_FILE = DATA_DIR / "alx-whisper.log"

API_KEY = os.environ.get("ALX_WHISPER_API_KEY", "").strip()
API_URL = os.environ.get("ALX_WHISPER_API_URL", "https://api.openai.com/v1/audio/transcriptions").strip()
MODEL = os.environ.get("ALX_WHISPER_MODEL", "whisper-1").strip()
LANGUAGE = os.environ.get("ALX_WHISPER_LANGUAGE", "").strip()
HOTKEY = os.environ.get("ALX_WHISPER_HOTKEY", "ctrl+shift+space").strip()
SAMPLE_RATE = int(os.environ.get("ALX_WHISPER_SAMPLE_RATE", "16000"))
MAX_SECONDS = int(os.environ.get("ALX_WHISPER_MAX_RECORD_SECONDS", "120"))
RESTORE_DELAY = float(os.environ.get("ALX_WHISPER_RESTORE_CLIPBOARD_DELAY", "2.0"))

# GNOME gsettings paths
GS_MEDIA_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
GS_BINDING_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/alx-whisper/"

# ---------- logging ----------
DATA_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler(sys.stderr)],
)
log = logging.getLogger("alx-whisper")

# ---------- hotkey registration via gsettings ----------

def parse_hotkey_to_gsettings(hotkey: str) -> str:
    """Convert 'ctrl+shift+space' → '<Control><Shift>space'."""
    parts = hotkey.lower().split("+")
    mapped = []
    for p in parts:
        p = p.strip()
        if p in ("ctrl", "control"):
            mapped.append("<Control>")
        elif p in ("shift",):
            mapped.append("<Shift>")
        elif p in ("alt",):
            mapped.append("<Alt>")
        elif p in ("super", "meta", "win", "mod4"):
            mapped.append("<Super>")
        else:
            mapped.append(p)
    return "".join(mapped)


def register_hotkey() -> None:
    """Register a GNOME custom shortcut that sends SIGUSR1 to this process."""
    pid = os.getpid()
    cmd = f"kill -USR1 {pid}"
    binding = parse_hotkey_to_gsettings(HOTKEY)

    # 1. Get current custom keybinding paths
    current_raw = subprocess.run(
        ["gsettings", "get", GS_MEDIA_KEYS, "custom-keybindings"],
        capture_output=True, text=True,
    ).stdout.strip()

    # Parse the GVariant array (e.g., "[]" or "['/path/foo/', '/path/bar/']")
    existing: list[str] = []
    if current_raw.startswith("[") and current_raw != "[]" and current_raw != "@as []":
        try:
            import ast
            existing = ast.literal_eval(current_raw)
        except Exception:
            log.warning(f"could not parse existing shortcuts: {current_raw}")

    # 2. Add our path
    if GS_BINDING_PATH not in existing:
        existing.append(GS_BINDING_PATH)

    # 3. Write the array back
    arr_str = "[" + ", ".join(f"'{p}'" for p in existing) + "]" if existing else "[]"
    subprocess.run(
        ["gsettings", "set", GS_MEDIA_KEYS, "custom-keybindings", arr_str],
        check=True,
    )

    # 4. Set the individual binding properties
    base = f"org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{GS_BINDING_PATH}"
    subprocess.run(["gsettings", "set", base, "name", "alx-whisper"], check=True)
    subprocess.run(["gsettings", "set", base, "command", cmd], check=True)
    subprocess.run(["gsettings", "set", base, "binding", binding], check=True)

    log.info(f"hotkey registered: {HOTKEY} → gsettings binding '{binding}'")


def unregister_hotkey() -> None:
    """Remove our shortcut from GNOME custom keybindings."""
    current_raw = subprocess.run(
        ["gsettings", "get", GS_MEDIA_KEYS, "custom-keybindings"],
        capture_output=True, text=True,
    ).stdout.strip()

    existing: list[str] = []
    if current_raw.startswith("[") and current_raw != "[]" and current_raw != "@as []":
        try:
            import ast
            existing = ast.literal_eval(current_raw)
        except Exception:
            pass

    if GS_BINDING_PATH in existing:
        existing.remove(GS_BINDING_PATH)
        arr_str = "[" + ", ".join(f"'{p}'" for p in existing) + "]" if existing else "[]"
        subprocess.run(
            ["gsettings", "set", GS_MEDIA_KEYS, "custom-keybindings", arr_str],
            check=True,
        )
        log.info("hotkey unregistered")

    # Clean up PID file
    PID_FILE.unlink(missing_ok=True)


# ---------- clipboard helpers ----------

def get_clipboard() -> str:
    for tool in (["wl-paste", "-n"], ["xclip", "-selection", "clipboard", "-o"]):
        try:
            r = subprocess.run(tool, capture_output=True, text=True)
            if r.returncode == 0:
                return r.stdout
        except FileNotFoundError:
            continue
    return ""


def set_clipboard(text: str) -> None:
    for tool in (["wl-copy"], ["xclip", "-selection", "clipboard"]):
        try:
            r = subprocess.run(tool, input=text, text=True)
            if r.returncode == 0:
                return
        except FileNotFoundError:
            continue
    log.warning("no clipboard tool found (wl-copy / xclip)")


def paste_via_ctrl_v() -> None:
    """Paste clipboard via Ctrl+V keystroke injection (Wayland-safe).

    We set the clipboard via wl-copy (Unicode-preserving), then inject
    Ctrl+V so the focused app pastes — preserving accents and special
    characters that ydotool type loses on non-US layouts.
    """
    # ydotool key: 29=KEY_LEFTCTRL, 47=KEY_V
    # :1 = press, :0 = release
    r = subprocess.run(
        ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        log.warning(f"ydotool key failed: {r.stderr.strip()}")
        log.warning("Text left in clipboard — paste manually with Ctrl+V.")


# ---------- audio ----------

def record_until_stopped(stop_event: threading.Event) -> bytes:
    """Record audio until stop_event is set or MAX_SECONDS reached."""
    log.info("recording started")
    chunks = []
    max_frames = int(SAMPLE_RATE * MAX_SECONDS)
    sample_count = 0

    def cb(indata, frames, time_info, status):
        nonlocal sample_count
        if status:
            log.debug(f"audio status: {status}")
        chunks.append(indata.copy())
        sample_count += len(indata)

    try:
        with sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=cb):
            while not stop_event.is_set() and sample_count < max_frames:
                time.sleep(0.05)
    except Exception as e:
        log.error(f"audio capture failed: {e}")
        return b""

    if not chunks:
        return b""

    import numpy as np
    audio = np.concatenate(chunks, axis=0)

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(audio.tobytes())

    duration = len(audio) / SAMPLE_RATE
    log.info(f"recording stopped ({duration:.1f}s)")
    return buf.getvalue()


# ---------- API ----------

def transcribe(wav_bytes: bytes) -> str:
    if not API_KEY:
        log.error("ALX_WHISPER_API_KEY not set")
        return ""
    headers = {"Authorization": f"Bearer {API_KEY}"}
    files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
    data = {"model": MODEL}
    if LANGUAGE:
        data["language"] = LANGUAGE

    try:
        r = requests.post(API_URL, headers=headers, data=data, files=files, timeout=60)
        if r.status_code >= 400:
            log.error(f"API {r.status_code}: {r.text[:500]}")
            return ""
        return (r.json().get("text") or "").strip()
    except requests.RequestException as e:
        log.error(f"API request failed: {e}")
        return ""


# ---------- main state ----------

class ToggleState:
    def __init__(self) -> None:
        self.recording = False
        self.stop_event: threading.Event | None = None
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()

    def toggle(self, *args) -> None:
        with self.lock:
            if self.recording:
                if self.stop_event:
                    self.stop_event.set()
                log.info("stop signal sent")
            else:
                self.stop_event = threading.Event()
                self.thread = threading.Thread(target=self._record_and_inject, daemon=True)
                self.thread.start()

    def _record_and_inject(self) -> None:
        self.recording = True
        try:
            wav = record_until_stopped(self.stop_event)
            if not wav:
                log.warning("no audio captured")
                return
            text = transcribe(wav)
            if not text:
                return
            preview = text[:120] + ("..." if len(text) > 120 else "")
            log.info(f"transcribed: {preview}")

            prior = get_clipboard()
            set_clipboard(text)
            paste_via_ctrl_v()

            if RESTORE_DELAY > 0 and prior is not None:
                def restore() -> None:
                    time.sleep(RESTORE_DELAY)
                    set_clipboard(prior)
                    log.debug("clipboard restored")
                threading.Thread(target=restore, daemon=True).start()
        except Exception:
            log.exception("record/inject failed")
        finally:
            self.recording = False


# ---------- signals ----------

_state: ToggleState | None = None


def handle_sigusr1(signum, frame):
    log.info("hotkey pressed (SIGUSR1)")
    if _state:
        _state.toggle()


def handle_sigterm(signum, frame):
    log.info("shutting down")
    unregister_hotkey()
    PID_FILE.unlink(missing_ok=True)
    sys.exit(0)


# ---------- entry point ----------

def main() -> int:
    global _state

    # Write PID file for the shortcut command to reference
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))

    # Check API key
    if not API_KEY or "sk-your-key-here" in API_KEY:
        log.warning(
            "API key has placeholder or is empty. "
            "Edit ~/.config/alx-whisper/.env and restart the service."
        )

    # Register hotkey via gsettings (no root needed)
    try:
        register_hotkey()
    except Exception as e:
        log.error(f"failed to register hotkey: {e}")
        log.error(
            "Make sure gnome-settings-daemon is running. "
            "Try: systemctl --user status gnome-session"
        )
        return 1

    # Set up signal handlers
    _state = ToggleState()
    signal.signal(signal.SIGUSR1, handle_sigusr1)
    signal.signal(signal.SIGTERM, handle_sigterm)
    signal.signal(signal.SIGINT, handle_sigterm)

    log.info(
        f"alx-whisper ready. Hotkey={HOTKEY}  Endpoint={API_URL}  Model={MODEL}"
    )
    log.info("Press Ctrl+Shift+Space to toggle recording.")

    # Sleep forever, waking only for signals
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
