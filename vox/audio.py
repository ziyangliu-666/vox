"""Microphone capture and text output."""

import io
import sys
import time
import wave

import numpy as np
import pyperclip
import sounddevice as sd
from pynput import keyboard

SAMPLE_RATE = 16_000
# Clips shorter than this are almost always an accidental key tap.
MIN_SECONDS = 0.3
IS_MAC = sys.platform == "darwin"


class Recorder:
    """Record mono 16 kHz audio and report a 0..1 level for the HUD waveform."""

    def __init__(self, on_level=None) -> None:
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._on_level = on_level

    def _callback(self, data, *_):
        self._chunks.append(data.copy())
        if self._on_level:
            rms = float(np.sqrt(np.mean(data.astype(np.float32) ** 2))) / 32768
            # Speech RMS sits around 0.01..0.1; a log curve spreads that
            # range over the whole bar instead of a flat line.
            self._on_level(min(1.0, max(0.0, (np.log10(rms + 1e-5) + 3.2) / 2.2)))

    def start(self) -> None:
        self._chunks = []
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1, dtype="int16",
            blocksize=SAMPLE_RATE // 20, callback=self._callback)
        self._stream.start()

    @property
    def active(self) -> bool:
        return self._stream is not None

    def stop(self) -> bytes | None:
        """Return the clip as WAV bytes, or None if it is too short."""
        if self._stream is None:
            return None
        self._stream.stop()
        self._stream.close()
        self._stream = None
        if not self._chunks:
            return None
        audio = np.concatenate(self._chunks)
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            return None
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(audio.tobytes())
        return buf.getvalue()


def _cmd_v_mac() -> None:
    """Send Cmd+V with Quartz events.

    pynput's Controller looks up the key code for "v" through the current
    input source (TSMGetInputSourceProperty). macOS allows that call only on
    the main thread and kills the process with SIGTRAP when it comes from a
    worker thread, which is where paste runs. A raw key code needs no lookup.
    """
    import Quartz

    v = 9  # kVK_ANSI_V
    for down in (True, False):
        event = Quartz.CGEventCreateKeyboardEvent(None, v, down)
        Quartz.CGEventSetFlags(event, Quartz.kCGEventFlagMaskCommand)
        Quartz.CGEventPost(Quartz.kCGHIDEventTap, event)


def copy(text: str) -> None:
    pyperclip.copy(text)


def paste(text: str, restore_clipboard: bool = True) -> None:
    """Paste through the clipboard, because typing key by key breaks on CJK and emoji."""
    previous = pyperclip.paste() if restore_clipboard else None
    pyperclip.copy(text)
    if IS_MAC:
        _cmd_v_mac()
    else:
        ctl = keyboard.Controller()
        with ctl.pressed(keyboard.Key.ctrl):
            ctl.tap("v")
    if previous is not None:
        # The target app reads the clipboard asynchronously; restoring at
        # once makes it paste the old content instead of the transcript.
        time.sleep(0.3)
        pyperclip.copy(previous)
