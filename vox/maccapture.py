"""Microphone capture on macOS with AVAudioEngine, and the CoreAudio device list.

vox used PortAudio (through sounddevice) on macOS too, until a clip froze
the whole app. PortAudio watches each stream with a listener for
kAudioOutputUnitProperty_IsRunning, and that listener calls
AudioUnitGetProperty. CoreAudio runs the listener on its IO thread while
it holds the device's IO lock. When the default input changed at the
moment vox stopped a clip (a Bluetooth headset that connects, or switches
to its headset profile), the stopping thread held the audio unit's lock and
waited for the IO lock, and the IO thread held the IO lock and waited for
the audio unit's lock. Neither thread ever ran again. The WebKit view of
chatgpt.com then asked CoreAudio about the same device on the main thread
and blocked too, so the whole app hung until it was killed.

AVAudioEngine installs no such listener. The device list comes straight
from CoreAudio, so a new headset shows up without a PortAudio restart.
"""

import ctypes
import threading
import warnings

import numpy as np

_CA = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")
_CF = ctypes.cdll.LoadLibrary(
    "/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")


class _Address(ctypes.Structure):
    _fields_ = [("selector", ctypes.c_uint32), ("scope", ctypes.c_uint32),
                ("element", ctypes.c_uint32)]


_CA.AudioObjectGetPropertyDataSize.restype = ctypes.c_int32
_CA.AudioObjectGetPropertyDataSize.argtypes = [
    ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32)]
_CA.AudioObjectGetPropertyData.restype = ctypes.c_int32
_CA.AudioObjectGetPropertyData.argtypes = [
    ctypes.c_uint32, ctypes.POINTER(_Address), ctypes.c_uint32, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
_CF.CFStringGetLength.restype = ctypes.c_long
_CF.CFStringGetLength.argtypes = [ctypes.c_void_p]
_CF.CFStringGetCString.restype = ctypes.c_bool
_CF.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long,
                                   ctypes.c_uint32]
_CF.CFRelease.argtypes = [ctypes.c_void_p]


def _fourcc(code: str) -> int:
    return int.from_bytes(code.encode("ascii"), "big")


_SYSTEM = 1  # kAudioObjectSystemObject
_GLOBAL, _INPUT = _fourcc("glob"), _fourcc("inpt")
_DEVICES, _DEFAULT_INPUT = _fourcc("dev#"), _fourcc("dIn ")
_NAME, _STREAMS = _fourcc("lnam"), _fourcc("stm#")
_UTF8 = 0x08000100  # kCFStringEncodingUTF8


def _size(obj: int, selector: int, scope: int = _GLOBAL) -> int:
    addr = _Address(selector, scope, 0)
    size = ctypes.c_uint32(0)
    if _CA.AudioObjectGetPropertyDataSize(obj, ctypes.byref(addr), 0, None,
                                          ctypes.byref(size)):
        return 0
    return size.value


def _u32s(obj: int, selector: int, scope: int = _GLOBAL) -> list[int]:
    n = _size(obj, selector, scope) // 4
    if not n:
        return []
    buf = (ctypes.c_uint32 * n)()
    size = ctypes.c_uint32(n * 4)
    addr = _Address(selector, scope, 0)
    if _CA.AudioObjectGetPropertyData(obj, ctypes.byref(addr), 0, None,
                                      ctypes.byref(size), buf):
        return []
    return list(buf[: size.value // 4])


def _name(device: int) -> str | None:
    ref = ctypes.c_void_p()
    size = ctypes.c_uint32(ctypes.sizeof(ref))
    addr = _Address(_NAME, _GLOBAL, 0)
    if _CA.AudioObjectGetPropertyData(device, ctypes.byref(addr), 0, None,
                                      ctypes.byref(size), ctypes.byref(ref)) or not ref.value:
        return None
    try:
        cap = _CF.CFStringGetLength(ref) * 4 + 1  # UTF-8 needs at most 4 bytes a unit.
        buf = ctypes.create_string_buffer(cap)
        if not _CF.CFStringGetCString(ref, buf, cap, _UTF8):
            return None
        return buf.value.decode("utf-8")
    finally:
        _CF.CFRelease(ref)


def inputs() -> list[tuple[int, str]]:
    """(AudioDeviceID, name) of each device that can record."""
    out = []
    for dev in _u32s(_SYSTEM, _DEVICES):
        if _u32s(dev, _STREAMS, _INPUT):
            name = _name(dev)
            if name:
                out.append((dev, name))
    return out


def default_input() -> str | None:
    ids = _u32s(_SYSTEM, _DEFAULT_INPUT)
    return _name(ids[0]) if ids and ids[0] else None


class Stream:
    """One clip from one input device, delivered as float32 mono blocks.

    callback(block, rate) runs on an AVAudioEngine thread, once per block.
    """

    def __init__(self, device: int | None, callback) -> None:
        from AVFoundation import AVAudioEngine

        self._callback = callback
        self._engine = AVAudioEngine.alloc().init()
        node = self._engine.inputNode()
        if device is not None:
            # PyObjC returns (ok, error) or only ok, by the metadata it has.
            result = node.AUAudioUnit().setDeviceID_error_(device, None)
            ok, err = result if isinstance(result, tuple) else (result, None)
            if not ok:
                raise RuntimeError(f"cannot select input device {device}: {err}")
        fmt = node.outputFormatForBus_(0)
        self.rate = float(fmt.sampleRate())
        if self.rate <= 0 or fmt.channelCount() < 1:
            raise RuntimeError("no input device")
        self._node = node
        self._tapped = False
        self._lock = threading.Lock()

    def _tap(self, buf, _when) -> None:
        n = buf.frameLength()
        if not n:
            return
        with warnings.catch_warnings():
            # floatChannelData is a C array of channel pointers; PyObjC
            # warns each time it hands out a raw pointer.
            warnings.simplefilter("ignore")
            ch0 = buf.floatChannelData()[0]
        # Channel 0 only, as PortAudio gave with channels=1.
        block = np.frombuffer(ch0.as_buffer(n), dtype=np.float32).copy()
        self._callback(block, self.rate)

    def start(self) -> None:
        # None: take the node's own format. A tap on the input node cannot
        # change the sample rate; the clip is resampled when it ends.
        self._node.installTapOnBus_bufferSize_format_block_(0, 2048, None, self._tap)
        self._tapped = True
        self._engine.prepare()
        ok, err = self._engine.startAndReturnError_(None)
        if not ok:
            self.close()
            raise RuntimeError(f"cannot start the microphone: {err}")

    def close(self) -> None:
        with self._lock:
            if self._tapped:
                self._node.removeTapOnBus_(0)
                self._tapped = False
            self._engine.stop()
