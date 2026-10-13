"""The vox desktop app: menu bar icon, main window, flow bar, ChatGPT engine.

Threads:
- main thread: the GUI loop (pywebview / AppKit). Nothing else may block it.
- pynput thread: hotkey events. Callbacks only flip state and hand off.
- worker threads: transcription, engine monitor, HUD level pump.
"""

import json
import os
import sys
import threading
import time
from pathlib import Path

import webview

from vox import audio, chatgpt, hotkey, permissions, sounds
from vox.config import Config, data_dir
from vox.history import History
from vox.tray import Tray

IS_MAC = sys.platform == "darwin"
VERSION = "0.2.0"
UI = Path(__file__).with_name("ui")
HUD_W, HUD_H = 360, 72
# How long the result or the error stays in the flow bar.
DONE_S, ERROR_S, CANCEL_S = 1.5, 3.0, 0.6


class Api:
    """Methods the main window calls through window.pywebview.api."""

    def __init__(self, app: "App") -> None:
        self._app = app

    def get_state(self):
        return self._app.state()

    def login(self):
        self._app.login()

    def logout(self):
        self._app.logout()

    def capture_hotkey(self):
        self._app.capture_hotkey()

    def cancel_capture(self):
        self._app.cancel_capture()

    def set_output(self, output):
        self._app.set_output(output)

    def set_sounds(self, on):
        self._app.set_sounds(on)

    def set_show_bar(self, on):
        self._app.set_show_bar(on)

    def get_history(self, query="", offset=0, limit=50):
        return self._app.history.query(query, int(offset or 0), int(limit or 50))

    def delete_history(self, item_id):
        self._app.history.delete(item_id)
        self._app.push()

    def clear_history(self):
        self._app.history.clear()
        self._app.push()

    def open_permission(self, kind):
        permissions.request(kind)

    def copy(self, text):
        audio.copy(text)


class HudApi:
    """Methods the flow bar page calls through window.pywebview.api (macOS)."""

    def __init__(self, app: "App") -> None:
        self._app = app

    def pill_rect(self, rect):
        if self._app.flowbar:
            self._app.flowbar.pill_rect(rect)

    def save_place(self, place):
        if self._app.flowbar:
            self._app.flowbar.save_place(place)

    def drag_end(self):
        if self._app.flowbar:
            self._app.flowbar.drag_end()

    def clicked(self):
        if self._app.flowbar:
            self._app.flowbar.clicked()


class App:
    def __init__(self, demo_file: str | None = None) -> None:
        self.cfg = Config.load()
        sounds.enabled = self.cfg.sounds
        self.demo_file = demo_file
        # "learning" until the first probe answers: it shows "Getting
        # ready" instead of a login button that would flash and vanish.
        self.account = "learning"
        self.account_error: str | None = None
        self.capturing = False
        self.history = History()
        self.perms = permissions.status()
        self._lock = threading.RLock()
        self._engine_lock = threading.Lock()  # One transcription at a time.
        self._last_failed: bytes | None = None
        self._retry_after_login = False
        self._level = 0.0
        self._hud_phase = "idle"
        self._hud_token = 0  # Newest HUD phase; older timed resets must not fire.

        self.main = webview.create_window(
            "vox", html=_page("index.html"), js_api=Api(self),
            width=400, height=520, resizable=False)
        self.main.events.closing += self._on_main_closing
        self.hud = webview.create_window(
            "vox flow bar", html=_page("hud.html"), js_api=HudApi(self),
            width=HUD_W, height=HUD_H,
            frameless=True, transparent=True, on_top=True, focus=False,
            easy_drag=False, shadow=False, hidden=True, background_color="#000000")
        # The bar page starts hidden; show the idle capsule once it can draw.
        self.hud.events.loaded += self._hud_loaded
        self.engine = chatgpt.ChatGPTWindow(hidden=True)
        self.engine.window.events.closing += self._on_engine_closing
        self.tray = _NoTray()  # _boot builds the real one on the GUI loop.
        self.flowbar = None  # macOS only; built in _place_hud.
        self.recorder = audio.Recorder(on_level=self._set_level)
        self.keys = hotkey.Hotkey(self.cfg.hotkey, self.cfg.mode,
                                  self._on_start, self._on_stop, self._on_cancel,
                                  on_double=self._on_double)

    # ---- state -------------------------------------------------------------

    def state(self) -> dict:
        return {
            "platform": "mac" if IS_MAC else "win",
            "account": self.account,
            "account_error": self.account_error,
            "hotkey": {"label": self.key_label(), "warning": hotkey.warning(self.cfg.hotkey)},
            "capturing": self.capturing,
            "mode": self.cfg.mode,
            "output": self.cfg.output,
            "sounds": self.cfg.sounds,
            "permissions": self.perms,
            "history": self.history.recent(5),
            "history_total": self.history.total,
            "show_bar": self.cfg.show_bar,
            "version": VERSION,
        }

    def key_label(self) -> str:
        return hotkey.label(self.cfg.hotkey, self.cfg.hotkey_labels)

    def push(self) -> None:
        payload = json.dumps({"type": "state", "state": self.state()})
        try:
            self.main.run_js(f"window.vox && window.vox.emit({payload})")
        except Exception:
            pass  # The page is still loading; it calls get_state() itself.

    def set_account(self, account: str, error: str | None = None) -> None:
        with self._lock:
            changed = (account, error) != (self.account, self.account_error)
            self.account, self.account_error = account, error
        if changed:
            print(f"[vox] account: {account}" + (f" ({error})" if error else ""), flush=True)
            self.push()

    # ---- flow bar (HUD) ----------------------------------------------------

    def hud_set(self, phase: str, **extra) -> int:
        if phase == "idle" and not self.cfg.show_bar:
            phase = "hidden"
        if self.flowbar:
            # An invisible bar must not catch the mouse.
            self.flowbar.grabbable = phase != "hidden"
        with self._lock:
            self._hud_phase = phase
            self._hud_token += 1
            token = self._hud_token
        payload = json.dumps({"phase": phase, "mode": self.cfg.mode, "key": self.key_label(),
                              "output": self.cfg.output, **extra})  # extra may override mode
        try:
            self.hud.run_js(f"window.hud && window.hud.set({payload})")
        except Exception:
            pass
        self.tray.set_state({"idle": "idle", "listening": "listening",
                             "transcribing": "busy", "done": "idle",
                             "cancelled": "idle", "error": "error"}.get(phase, "idle"))
        return token

    def hud_later(self, token: int, seconds: float) -> None:
        def reset():
            time.sleep(seconds)
            with self._lock:
                stale = token != self._hud_token
            if not stale:
                self.hud_set("idle")
        threading.Thread(target=reset, daemon=True).start()

    def _set_level(self, level: float) -> None:
        self._level = level  # Audio thread: store only; the pump sends it.

    def _level_pump(self) -> None:
        while True:
            time.sleep(0.05)
            if self._hud_phase == "listening":
                try:
                    self.hud.run_js(f"window.hud && window.hud.level({self._level:.3f})")
                except Exception:
                    pass

    def _place_hud(self) -> None:
        if not IS_MAC:
            screen = webview.screens[0]
            self.hud.move(screen.x + (screen.width - HUD_W) // 2,
                          screen.y + screen.height - HUD_H - 16)
            self.hud.show()
            return
        from PyObjCTools import AppHelper

        from vox.flowbar import FlowBar

        def save(place):
            self.cfg.flow_bar = place
            self.cfg.save()

        _wait_native(self.hud)
        self.flowbar = FlowBar(self.hud, self.cfg.flow_bar, save, on_click=self.show_main)

        def native():
            win = self.hud.native
            # pywebview's show() activates the app, which takes focus from
            # the app the user dictates into. orderFrontRegardless shows the
            # bar without activating vox.
            # pywebview's frameless window is a titled window with a hidden
            # title bar, and AppKit keeps titled windows off the screen edge,
            # 30 pt short of it. A borderless window may touch the edge.
            win.setStyleMask_(0)  # NSWindowStyleMaskBorderless
            win.setIgnoresMouseEvents_(True)  # FlowBar turns it off on hover.
            win.setHasShadow_(False)
            win.setLevel_(25)  # NSStatusWindowLevel: above normal windows.
            # All Spaces, stationary, over full-screen apps, not in Cmd+`.
            win.setCollectionBehavior_((1 << 0) | (1 << 4) | (1 << 6) | (1 << 8))
            win.orderFrontRegardless()
        AppHelper.callAfter(native)
        self.flowbar.start()
        threading.Thread(target=self.flowbar.run, daemon=True).start()

    def _hud_loaded(self) -> None:
        if self.flowbar:
            self.flowbar.sync_page()
        self.hud_set("idle")

    def reset_flow_bar(self) -> None:
        if self.flowbar:
            self.flowbar.reset()

    # ---- hotkey ------------------------------------------------------------

    def _on_start(self, locked: bool = False) -> bool:
        print("[vox] hotkey start", flush=True)
        with self._lock:
            if self.recorder.active:
                return False
            ready = self.account == "ready"
        if not ready:
            # Only the bar: opening the main window would take focus from
            # the app the user is typing in.
            token = self.hud_set("error", message="Log in to vox first")
            self.hud_later(token, ERROR_S)
            return False
        # Show the bar before the microphone opens, which can take a moment:
        # the user sees the press at once.
        if locked:
            # Hands-free looks like toggle mode: press the key to stop.
            self.hud_set("listening", locked=True, mode="toggle")
        else:
            self.hud_set("listening")
        sounds.start()
        try:
            self.recorder.start()
        except Exception:
            token = self.hud_set("error", message="No microphone")
            self.hud_later(token, ERROR_S)
            return False
        return True

    def _on_double(self) -> str | None:
        # Double-tap: hands-free. The clip runs on after the key is up and
        # stops at the next press.
        return "lock" if self._on_start(locked=True) else None

    def _on_stop(self) -> None:
        wav = self.recorder.stop()
        print(f"[vox] hotkey stop, clip {len(wav or b'')} bytes", flush=True)
        sounds.stop()
        if not wav:
            self.hud_set("idle")
            return
        threading.Thread(target=self._transcribe, args=(wav,), daemon=True).start()

    def _on_cancel(self) -> None:
        self.recorder.stop()
        token = self.hud_set("cancelled")
        self.hud_later(token, CANCEL_S)

    def retry_last(self) -> None:
        wav = self._last_failed
        if self.recorder.active:
            return
        if not wav:
            token = self.hud_set("error", message="Nothing to retry")
            self.hud_later(token, ERROR_S)
            return
        threading.Thread(target=self._transcribe, args=(wav,), daemon=True).start()

    def _transcribe(self, wav: bytes) -> None:
        # One clip at a time, in order; a new recording may start meanwhile.
        with self._engine_lock:
            self._transcribe_one(wav)

    def _transcribe_one(self, wav: bytes) -> None:
        # While the user already records the next clip, the bar belongs to
        # that clip: this one finishes silently.
        quiet = lambda: self.recorder.active  # noqa: E731
        if not quiet():
            self.hud_set("transcribing")
        try:
            text = self.engine.transcribe(wav).strip()
        except Exception as exc:
            print(f"[vox] transcription failed: {exc}", file=sys.stderr)
            self._last_failed = wav
            message = _short_error(exc)
            if message == "Log in again":
                # The kept clip goes out again by itself after the login.
                self._retry_after_login = True
                self.set_account("logged_out")
            else:
                message += " · Retry in menu"
            # The error stays until the next press; the audio is kept for
            # "Retry last clip" in the menu. A clip being recorded now owns
            # the bar, so then only the menu bar icon shows the error.
            if not quiet():
                self.hud_set("error", message=message)
            self.tray.set_state("error")
            return
        if not text:
            if not quiet():
                token = self.hud_set("error", message="Heard nothing")
                self.hud_later(token, ERROR_S)
            return
        self._last_failed = None
        print(f"[vox] transcribed {len(text)} chars, {self.cfg.output}", flush=True)
        if self.cfg.output == "paste":
            audio.paste(text)
        else:
            audio.copy(text)
        self.history.add(text)
        self.push()
        if not quiet():
            token = self.hud_set("done", text=text)
            self.hud_later(token, DONE_S)

    # ---- actions from the UI and the menu ----------------------------------

    def login(self) -> None:
        self.set_account("logging_in")
        self.engine.show()

    def logout(self) -> None:
        self.engine.logout()
        self.set_account("logged_out")

    def capture_hotkey(self) -> None:
        def done(ids, labels):
            with self._lock:
                self.capturing = False
                if ids:
                    self.cfg.hotkey, self.cfg.hotkey_labels = ids, labels
                    self.cfg.save()
                    self.keys.set(self.cfg.hotkey, self.cfg.mode)
            self.push()

        with self._lock:
            self.capturing = True
        self.keys.capture(done)
        self.push()

    def cancel_capture(self) -> None:
        self.keys.cancel_capture()
        with self._lock:
            self.capturing = False
        self.push()

    def set_show_bar(self, on: bool) -> None:
        self.cfg.show_bar = bool(on)
        self.cfg.save()
        if self._hud_phase in ("idle", "hidden"):
            self.hud_set("idle")
        self.push()

    def toggle_bar(self) -> None:
        self.set_show_bar(not self.cfg.show_bar)

    def set_sounds(self, on: bool) -> None:
        self.cfg.sounds = sounds.enabled = bool(on)
        self.cfg.save()
        self.push()

    def set_output(self, output: str) -> None:
        if output in ("paste", "copy"):
            self.cfg.output = output
            self.cfg.save()
            self.push()

    def show_main(self) -> None:
        self.main.show()
        if IS_MAC:
            from AppKit import NSApp
            from PyObjCTools import AppHelper

            def front():
                # Open on the Space the user is on, not the one vox started on.
                self.main.native.setCollectionBehavior_(1 << 1)  # MoveToActiveSpace
                self.main.native.makeKeyAndOrderFront_(None)
                # A menu bar app is not active by default, so its window
                # would open behind the frontmost app.
                NSApp.activateIgnoringOtherApps_(True)
            AppHelper.callAfter(front)

    def quit(self) -> None:
        # pynput and AppKit threads do not all stop on request; a hard exit
        # after the windows close is the reliable way out.
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass
        threading.Timer(0.5, lambda: os._exit(0)).start()

    def _on_main_closing(self):
        # Closing the window keeps vox running in the menu bar.
        self.main.hide()
        return False

    def _on_engine_closing(self):
        self.engine.hide()
        if self.account == "logging_in":
            self.set_account("logged_out")
        return False

    # ---- background --------------------------------------------------------

    def _monitor(self) -> None:
        """Follow the login state of the engine window and the permission state."""
        started = time.monotonic()
        while True:
            probe = self.engine.probe()
            with self._lock:
                account = self.account
            if probe == "ready" and account in ("logging_in", "learning", "logged_out", "error"):
                self.set_account("learning")
                self.engine.hide()
                try:
                    self.engine.learn()
                    self.set_account("ready")
                    if self._retry_after_login:
                        self._retry_after_login = False
                        self.retry_last()
                    if self.demo_file:
                        threading.Thread(target=self._demo, daemon=True).start()
                except Exception as exc:
                    self.set_account("error", _short_error(exc))
            elif probe == "logged_out" and account in ("ready", "learning"):
                self.set_account("logged_out")
            elif probe == "loading" and account == "learning" \
                    and time.monotonic() - started > 30:
                self.set_account("error", "ChatGPT did not load")
            perms = permissions.status()
            if perms != self.perms:
                self.perms = perms
                self.push()
            time.sleep(1)

    def _demo(self) -> None:
        """Play a file through the full flow bar path, for UI review without a voice."""
        time.sleep(1)
        self.hud_set("listening")
        for i in range(30):
            self._level = 0.25 + 0.6 * abs(((i * 7) % 10) / 10 - 0.5)
            time.sleep(0.07)
        self._transcribe(Path(self.demo_file).read_bytes())

    def _boot(self) -> None:
        if IS_MAC:
            from AppKit import NSApp
            from PyObjCTools import AppHelper

            # 1 = accessory: a menu bar app with no Dock icon, also when
            # run from source without the bundle's LSUIElement flag.
            AppHelper.callAfter(NSApp.setActivationPolicy_, 1)
            # From source, macOS shows the Python icon; the bundle sets its
            # own. Use the built icon set when there is one.
            icns = Path(__file__).resolve().parent.parent / "assets" / "vox.icns"
            if icns.exists():
                from AppKit import NSImage

                AppHelper.callAfter(NSApp.setApplicationIconImage_,
                                    NSImage.alloc().initWithContentsOfFile_(str(icns)))
        self.tray = Tray([
            ("Open vox", self.show_main),
            ("Log in to ChatGPT", self.login),
            ("Retry last clip", self.retry_last),
            ("Show or hide flow bar", self.toggle_bar),
            ("Reset flow bar position", self.reset_flow_bar),
            (None, None),
            ("Quit vox", self.quit),
        ])
        self._place_hud()
        _wait_native(self.engine.window)
        self.engine.hide()
        self.show_main()
        self.keys.start()
        for target in (self._monitor, self._level_pump):
            threading.Thread(target=target, daemon=True).start()
        self.push()

    def run(self) -> None:
        webview.start(self._boot, private_mode=False,
                      storage_path=str(chatgpt.profile_dir()))


class _NoTray:
    def set_state(self, state: str) -> None:
        pass


def _wait_native(window, timeout_s: float = 10) -> None:
    """Wait until pywebview has made the window's NSWindow.

    _boot can run before a hidden window exists; AppKit calls on it would
    then fail without a sound.
    """
    end = time.monotonic() + timeout_s
    while window.native is None and time.monotonic() < end:
        time.sleep(0.05)


def _page(name: str) -> str:
    """Return a UI page with its stylesheet inlined.

    Loading the page as a string keeps pywebview from starting a localhost
    HTTP server for the ui folder: one less open port, and no port clash
    when a second copy of vox starts.
    """
    html = (UI / name).read_text(encoding="utf-8")
    css = (UI / "style.css").read_text(encoding="utf-8")
    return html.replace('<link rel="stylesheet" href="style.css">', f"<style>{css}</style>")


def _single_instance() -> bool:
    """Hold a lock for the life of the process; False if another vox has it."""
    global _lock_file
    _lock_file = open(data_dir() / "vox.lock", "a+")
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(_lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


_lock_file = None


def _short_error(exc: Exception) -> str:
    """Map an engine error to a flow bar message of a few words."""
    msg = str(exc)
    if "401" in msg or "403" in msg or "log in" in msg.lower():
        return "Log in again"
    if "HTTP 429" in msg:
        return "Too many requests"
    if "HTTP" in msg or "did not answer" in msg or "Failed to fetch" in msg:
        return "Network error"
    return "Something went wrong"


def main(demo_file: str | None = None) -> None:
    if not _single_instance():
        print("vox is already running.", file=sys.stderr)
        return
    App(demo_file).run()
