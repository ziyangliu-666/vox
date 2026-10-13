"""Global hotkey: one key or a combination, in hold or toggle mode.

pynput does not suppress keys, so the hotkey still reaches the focused app.
That is why a single letter or digit is refused as a hotkey: holding it
would type it over and over.
"""

import sys
import threading
import time

from pynput import keyboard

IS_MAC = sys.platform == "darwin"

# Left-side modifiers come in under two names; fold them into one id.
_ALIASES = {"alt_l": "alt", "ctrl_l": "ctrl", "shift_l": "shift", "cmd_l": "cmd",
            "alt_gr": "alt_r"}
_MODIFIERS = {"alt", "alt_r", "ctrl", "ctrl_r", "shift", "shift_r", "cmd", "cmd_r"}

if IS_MAC:
    _LABELS = {"alt": "⌥", "alt_r": "Right ⌥", "ctrl": "⌃", "ctrl_r": "Right ⌃",
               "shift": "⇧", "shift_r": "Right ⇧", "cmd": "⌘", "cmd_r": "Right ⌘"}
else:
    _LABELS = {"alt": "Alt", "alt_r": "Right Alt", "ctrl": "Ctrl", "ctrl_r": "Right Ctrl",
               "shift": "Shift", "shift_r": "Right Shift", "cmd": "Win", "cmd_r": "Right Win"}
_LABELS.update({"space": "Space", "caps_lock": "Caps Lock", "tab": "Tab",
                "enter": "Return" if IS_MAC else "Enter", "esc": "Esc"})


# A press shorter than TAP_S counts as a tap; a second press within
# DOUBLE_S of a tap's release is a double-tap.
TAP_S, DOUBLE_S = 0.3, 0.4


def key_id(key) -> tuple[str, str | None]:
    """Return (id, label for character keys) for a pynput key."""
    if isinstance(key, keyboard.Key):
        return _ALIASES.get(key.name, key.name), None
    char = getattr(key, "char", None)
    label = char.upper() if char and char.isprintable() else None
    if getattr(key, "vk", None) is not None:
        return f"vk{key.vk}", label
    return f"c:{char}", label


def label(ids: list[str], char_labels: dict[str, str]) -> str:
    def one(i: str) -> str:
        if i in _LABELS:
            return _LABELS[i]
        if i in char_labels:
            return char_labels[i]
        if i.startswith("c:"):
            return i[2:].upper()
        if i[:1] == "f" and i[1:].isdigit():
            return i.upper()
        return i.replace("_", " ").title()
    return " + ".join(one(i) for i in ids)


# Combinations the system or common apps already use. vox cannot block a
# key from reaching the focused app, so these would trigger both.
_TAKEN = {
    frozenset({"ctrl", "space"}): "Also switches input source",
    frozenset({"cmd", "space"}): "Also opens Spotlight",
    frozenset({"alt", "space"}): "Also used by many apps",
    frozenset({"ctrl", "shift", "space"}): "Also used by many apps",
}


def warning(ids: list[str]) -> str | None:
    if not IS_MAC and frozenset(ids) == frozenset({"cmd", "space"}):
        return "Also switches keyboard layout"
    return _TAKEN.get(frozenset(ids))


def _ordered(ids) -> list[str]:
    # Modifiers first, as people read shortcuts: "⌃ + Space", not "Space + ⌃".
    return sorted(ids, key=lambda i: (i not in _MODIFIERS, i))


class Hotkey:
    """Watch the keyboard and call on_start/on_stop/on_cancel for the hotkey.

    Callbacks run on the pynput thread, so they must return quickly.
    on_start returns False when it did not start recording; the hotkey then
    stays inactive, so the next press is a new start and not a stop.

    Hold mode also knows a double-tap. The first tap starts and stops a clip
    too short to keep, so nothing is lost by waiting for the second press.
    on_double returns "lock" when it started a hands-free clip (it then runs
    on after the key is up, until the next press) or "done" when it ran
    some other action.
    """

    def __init__(self, ids, mode, on_start, on_stop, on_cancel, on_double=None) -> None:
        self.ids = set(ids)
        self.mode = mode
        self._on_start, self._on_stop, self._on_cancel = on_start, on_stop, on_cancel
        self._on_double = on_double
        self._locked = False  # Hands-free: recording goes on after the key is up.
        self._swallow = False  # Ignore the release of a press that ran an action.
        self._press_t = 0.0
        self._tap_t = 0.0  # When the last short tap ended.
        self._down: set[str] = set()
        self._active = False
        self._armed = True  # Toggle mode: wait for a full release between presses.
        self._capture = None  # (callback, best combo, labels) while capturing.
        self._lock = threading.Lock()
        self.events_seen = False
        self._listener = keyboard.Listener(on_press=self._press, on_release=self._release)

    def start(self) -> None:
        self._listener.start()

    def set(self, ids, mode) -> None:
        with self._lock:
            self.ids, self.mode = set(ids), mode
            self._active, self._armed, self._locked = False, True, False

    def capture(self, callback) -> None:
        """Record the next key combination; callback(ids, labels) or callback(None, None) on Esc."""
        with self._lock:
            self._capture = [callback, set(), {}]
            if self._active:
                self._active = self._locked = False
                self._on_cancel()

    def cancel_capture(self) -> None:
        with self._lock:
            self._capture = None

    def _press(self, key) -> None:
        if not self.events_seen:
            self.events_seen = True
            print("[vox] keyboard events arriving", flush=True)
        kid, char_label = key_id(key)
        with self._lock:
            self._down.add(kid)
            if self._capture is not None:
                cb, best, labels = self._capture
                if kid == "esc" and len(self._down) == 1:
                    self._capture = None
                    threading.Thread(target=cb, args=(None, None), daemon=True).start()
                    return
                if char_label:
                    labels[kid] = char_label
                if len(self._down) >= len(best):
                    best.clear()
                    best.update(self._down)
                return
            if kid == "esc" and self._active:
                self._active = self._locked = False
                self._on_cancel()
                return
            if not self.ids <= self._down:
                return
            if self.mode == "hold":
                if self._locked:  # A press ends a hands-free clip.
                    self._active = self._locked = False
                    self._swallow = True
                    self._on_stop()
                elif not self._active:
                    now = time.monotonic()
                    if self._on_double and now - self._tap_t < DOUBLE_S:
                        self._tap_t = 0.0
                        result = self._on_double()
                        if result == "lock":
                            self._active = self._locked = True
                            return
                        if result == "done":
                            self._swallow = True
                            return
                    self._press_t = now
                    self._active = self._on_start() is not False
            elif self._armed:
                self._armed = False
                if self._active:
                    self._active = False
                    self._on_stop()
                else:
                    self._active = self._on_start() is not False

    def _release(self, key) -> None:
        kid, _ = key_id(key)
        with self._lock:
            self._down.discard(kid)
            if self._capture is not None:
                cb, best, labels = self._capture
                if self._down:
                    return
                combo = _ordered(best)
                single_char = len(combo) == 1 and (combo[0].startswith("vk")
                                                   or combo[0].startswith("c:"))
                if not combo or single_char:
                    best.clear()  # Refused; keep capturing.
                    return
                self._capture = None
                keep = {k: v for k, v in labels.items() if k in combo}
                threading.Thread(target=cb, args=(combo, keep), daemon=True).start()
                return
            if kid not in self.ids:
                return
            if self._swallow:
                self._swallow = bool(self.ids & self._down)
                return
            if self.mode == "hold" and self._active and not self._locked:
                self._active = False
                now = time.monotonic()
                if now - self._press_t < TAP_S:
                    self._tap_t = now
                self._on_stop()
            if self.mode == "toggle" and not (self.ids & self._down):
                self._armed = True
