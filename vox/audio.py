"""Microphone capture and text output.

macOS records with AVAudioEngine (maccapture.py), Windows with PortAudio
through sounddevice. All work on the audio device runs on one worker
thread, and the hotkey thread waits for it only up to a time limit. When the
audio system does not answer, Recorder raises AudioStuck: the hotkey keeps
working, and the app restarts itself to get a fresh audio client.
"""

import concurrent.futures
import io
import queue
import sys
import threading
import time
import wave

import numpy as np
import pyperclip
from pynput import keyboard

SAMPLE_RATE = 16_000
# Clips shorter than this are almost always an accidental key tap.
MIN_SECONDS = 0.3
IS_MAC = sys.platform == "darwin"
# A Bluetooth headset can take a few seconds to switch to its microphone
# profile, so opening gets more time than closing. Closing a healthy stream
# takes milliseconds.
OPEN_S, CLOSE_S, LIST_S = 10.0, 4.0, 4.0

if IS_MAC:
    from vox import maccapture
else:
    import sounddevice as sd


class AudioStuck(Exception):
    """The audio system did not answer in time.

    In this process the audio device can stay locked for good (see
    maccapture.py), so the caller must restart the app. clip holds the
    WAV of what was recorded before, or None.
    """

    def __init__(self, what: str, clip: bytes | None = None) -> None:
        super().__init__(f"the audio system did not answer ({what})")
        self.clip = clip


# Windows stand-ins for "the default device": the picker has its own entry.
_ALIASES = ("Microsoft Sound Mapper", "Primary Sound Capture Driver")


def _inputs() -> list[tuple[int, str]]:
    """(index, name) of each input device that the recorder can open.

    macOS: CoreAudio device IDs. Windows: PortAudio lists a device once per
    host API (MME, DirectSound, WASAPI, WDM-KS). The recorder opens the
    default API, so only its devices count.
    """
    if IS_MAC:
        return maccapture.inputs()
    out, seen = [], set()
    for i in sd.query_hostapis(sd.default.hostapi)["devices"]:
        d = sd.query_devices(i)
        if (d["max_input_channels"] > 0 and d["name"] not in seen
                and not d["name"].startswith(_ALIASES)):
            seen.add(d["name"])
            out.append((i, d["name"]))
    return out


def default_input() -> str | None:
    if IS_MAC:
        try:
            return maccapture.default_input()
        except Exception:
            return None
    try:
        name = sd.query_devices(kind="input")["name"]
    except Exception:
        return None  # No input device at all.
    return None if name.startswith(_ALIASES) else name


def _resample(audio: np.ndarray, rate: float) -> np.ndarray:
    """Band-limited resampling to SAMPLE_RATE, through the spectrum.

    The ratio is fixed for a clip and the clip is short, so one FFT does it
    without a filter design.
    """
    if rate == SAMPLE_RATE or audio.size == 0:
        return audio
    n_out = max(1, round(audio.size * SAMPLE_RATE / rate))
    spectrum = np.fft.rfft(audio)[: n_out // 2 + 1]
    return np.fft.irfft(spectrum, n_out) * (n_out / audio.size)


class Recorder:
    """Record mono 16 kHz audio and report a 0..1 level for the HUD waveform.

    start(), stop() and inputs() are safe to call from the hotkey thread:
    the device work runs on the worker thread, and they never wait longer
    than OPEN_S, CLOSE_S or LIST_S.
    """

    def __init__(self, on_level=None) -> None:
        self._chunks: list[np.ndarray] = []
        self._rate = float(SAMPLE_RATE)
        self._stream = None
        self._on_level = on_level
        self.device: str | None = None  # Name; None is the system default.
        self.stuck = False  # The worker did not come back; restart the app.
        # A daemon thread, not a ThreadPoolExecutor: the interpreter joins
        # executor threads at exit, and a stuck one would hang the exit.
        self._jobs: queue.Queue = queue.Queue()
        threading.Thread(target=self._work, name="vox-audio", daemon=True).start()
        self._names: list[str] = []

    def _work(self) -> None:
        while True:
            fn, future = self._jobs.get()
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as exc:
                future.set_exception(exc)

    def _run(self, fn, timeout: float, what: str):
        if self.stuck:
            raise AudioStuck(what)
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._jobs.put((fn, future))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            self.stuck = True
            print(f"[vox] audio {what} did not return in {timeout:.0f} s", flush=True)
            raise AudioStuck(what) from None

    def inputs(self, refresh: bool = False) -> list[str]:
        """Input device names. refresh rescans, so a new headset shows up.

        On Windows PortAudio reads the device list once; a rescan restarts
        it, which must not happen while a stream is open. CoreAudio on
        macOS always answers with the live list. When the worker is busy
        or stuck, the last list comes back.
        """
        def scan():
            if refresh and not IS_MAC and self._stream is None:
                sd._terminate()
                sd._initialize()
            return [name for _, name in _inputs()]

        try:
            self._names = self._run(scan, LIST_S, "device list")
        except AudioStuck:
            pass
        except Exception:
            self._names = []
        return self._names

    def _device_index(self) -> int | None:
        if not self.device:
            return None
        for i, name in _inputs():
            if name == self.device:
                return i
        return None  # Unplugged: fall back to the system default.

    def _level(self, block: np.ndarray) -> None:
        if self._on_level:
            rms = float(np.sqrt(np.mean(block ** 2)))
            # Speech RMS sits around 0.01..0.1; a log curve spreads that
            # range over the whole bar instead of a flat line.
            self._on_level(min(1.0, max(0.0, (np.log10(rms + 1e-5) + 3.2) / 2.2)))

    def _mac_block(self, block: np.ndarray, rate: float) -> None:
        self._rate = rate
        self._chunks.append(block)
        self._level(block)

    def _win_block(self, data, *_) -> None:
        block = data[:, 0].astype(np.float32) / 32768
        self._chunks.append(block)
        self._level(block)

    def _open(self) -> None:
        device = self._device_index()
        if IS_MAC:
            stream = maccapture.Stream(device, self._mac_block)
            self._rate = stream.rate
        else:
            stream = sd.InputStream(
                device=device, samplerate=SAMPLE_RATE, channels=1, dtype="int16",
                blocksize=SAMPLE_RATE // 20, callback=self._win_block)
            self._rate = float(SAMPLE_RATE)
        stream.start()
        self._stream = stream

    def _close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is None:
            return
        if IS_MAC:
            stream.close()
        else:
            stream.stop()
            stream.close()

    def start(self) -> None:
        """Open the microphone. Raises AudioStuck, or another error when there is none."""
        self._chunks = []
        self._run(self._open, OPEN_S, "open")

    @property
    def active(self) -> bool:
        return self._stream is not None

    def stop(self) -> bytes | None:
        """Return the clip as WAV bytes, or None if it is too short.

        Raises AudioStuck with the clip recorded so far when the device does
        not close: the blocks are already in memory, so the words are kept.
        """
        if self._stream is None:
            return None
        try:
            self._run(self._close, CLOSE_S, "close")
        except AudioStuck as exc:
            exc.clip = self._wav()
            raise
        return self._wav()

    def _wav(self) -> bytes | None:
        chunks = list(self._chunks)
        if not chunks:
            return None
        audio = _resample(np.concatenate(chunks), self._rate)
        if len(audio) < SAMPLE_RATE * MIN_SECONDS:
            return None
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(SAMPLE_RATE)
            wav.writeframes(pcm.tobytes())
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
