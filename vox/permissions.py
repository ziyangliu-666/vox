"""macOS privacy permissions that vox needs, and how to get them.

- Microphone: to record.
- Accessibility: to send Cmd+V to another app.
- Input Monitoring: to see the hotkey while another app has focus.

Windows has no such gate for these actions, so every check there is
"granted".
"""

import ctypes
import subprocess
import sys

IS_MAC = sys.platform == "darwin"

_PANES = {
    "microphone": "Privacy_Microphone",
    "accessibility": "Privacy_Accessibility",
    "input_monitoring": "Privacy_ListenEvent",
}
# IOKit IOHIDRequestType / IOHIDAccessType values.
_LISTEN_EVENT = 1
_GRANTED, _DENIED = 0, 1


def _iokit():
    lib = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/IOKit.framework/IOKit")
    lib.IOHIDCheckAccess.restype = ctypes.c_int
    lib.IOHIDCheckAccess.argtypes = [ctypes.c_int]
    lib.IOHIDRequestAccess.restype = ctypes.c_bool
    lib.IOHIDRequestAccess.argtypes = [ctypes.c_int]
    return lib


def _ax():
    lib = ctypes.cdll.LoadLibrary(
        "/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
    lib.AXIsProcessTrusted.restype = ctypes.c_bool
    return lib


def status(keys_seen: bool = False) -> dict[str, str]:
    """keys_seen: the hotkey listener got a key event, so input monitoring works,
    whatever IOHIDCheckAccess says (it answers from the state at launch)."""
    if not IS_MAC:
        return {}
    import AVFoundation

    mic = AVFoundation.AVCaptureDevice.authorizationStatusForMediaType_(
        AVFoundation.AVMediaTypeAudio)
    listen = _iokit().IOHIDCheckAccess(_LISTEN_EVENT)
    return {
        "microphone": "granted" if mic == 3 else "missing",
        "accessibility": "granted" if _ax().AXIsProcessTrusted() else "missing",
        "input_monitoring": "granted" if keys_seen
        else {_GRANTED: "granted", _DENIED: "missing"}.get(listen, "unknown"),
    }


def request(kind: str) -> None:
    """Show the system prompt when macOS still allows one, else open the right Settings pane."""
    if not IS_MAC:
        return
    if kind == "microphone":
        import AVFoundation

        dev = AVFoundation.AVCaptureDevice
        # 0 is "not determined": the system prompt can still appear. After a
        # denial macOS never prompts again, so only Settings can fix it.
        if dev.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeAudio) == 0:
            dev.requestAccessForMediaType_completionHandler_(
                AVFoundation.AVMediaTypeAudio, lambda granted: None)
            return
    elif kind == "input_monitoring":
        if _iokit().IOHIDCheckAccess(_LISTEN_EVENT) not in (_GRANTED, _DENIED):
            _iokit().IOHIDRequestAccess(_LISTEN_EVENT)
            return
    subprocess.Popen(["open", "x-apple.systempreferences:com.apple.preference.security?"
                      + _PANES[kind]])
