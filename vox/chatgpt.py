"""Transcribe through a logged-in chatgpt.com window instead of an API key.

The window is a real system browser engine (WebKit on macOS, WebView2 on
Windows) with a persistent profile, so the login survives restarts. The
request comes from inside the page, with the page's own cookies and
headers. vox does not read, store, or forward the session token.
"""

import base64
import json
import time
import uuid
from pathlib import Path

import sys

import webview

IS_MAC = sys.platform == "darwin"
if IS_MAC:
    from PyObjCTools import AppHelper

from vox.config import data_dir

BRIDGE_JS = (Path(__file__).with_name("bridge.js")).read_text(encoding="utf-8")
HOME = "https://chatgpt.com/"
# A long clip takes a few seconds to upload and transcribe; past this the
# request is almost surely stuck.
TIMEOUT_S = 90

# Logged out, ChatGPT shows one of several screens: a login button, an
# account chooser ("Remove account"), or a log-in modal. Dictate alone is
# not enough either, because a logged-out page can show it too.
_PROBE = """(() => {
  if (!window.__vox) return 'loading';
  const labels = [...document.querySelectorAll('button, a')]
    .map((b) => (b.getAttribute('aria-label') || b.textContent || '').trim().toLowerCase());
  const loggedOut = document.querySelector('[data-testid="login-button"]')
    || labels.some((l) => l === 'log in' || l === 'sign up' || l === 'remove account');
  if (loggedOut) return 'logged_out';
  return document.querySelector('button[aria-label="Dictate"]') ? 'ready' : 'loading';
})()"""


def profile_dir() -> Path:
    # WebView2 keeps its profile here. On macOS pywebview uses the default
    # WebKit store of the process, so this folder stays empty there.
    path = data_dir() / "webview"
    path.mkdir(parents=True, exist_ok=True)
    return path


class ChatGPTWindow:
    def __init__(self, hidden: bool = False) -> None:
        self.window = webview.create_window(
            "Log in to ChatGPT", HOME, width=480, height=720, hidden=hidden)
        # "loaded" fires after each full navigation, and a navigation drops
        # the injected script, so inject it every time. bridge.js ignores a
        # second injection into the same page.
        self.window.events.loaded += self._inject

    def _inject(self) -> None:
        url = self.window.get_current_url() or ""
        # Login passes through other hosts (auth.openai.com, Google,
        # Microsoft). The bridge is only for chatgpt.com.
        if url.startswith(HOME):
            self.window.run_js(BRIDGE_JS)

    def _js(self, expr: str):
        """Run expr and return its value.

        evaluate_js() wraps the script in eval(), and chatgpt.com's CSP blocks
        eval, so use run_js(), which passes the script to the engine as is.
        WebView2 returns the value JSON-encoded; WebKit returns it raw.
        """
        value = self.window.run_js(expr)
        # WebKit returns JS null as an NSNull object, not as None.
        if type(value).__name__ == "NSNull":
            return None
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                pass
        return value

    def probe(self) -> str:
        """Return "ready", "logged_out", or "loading"."""
        url = self.window.get_current_url() or ""
        if not url.startswith(HOME):
            # Somewhere in the login flow on another host.
            return "logged_out"
        try:
            return self._js(_PROBE) or "loading"
        except Exception:
            return "loading"  # The page is between navigations.

    def wait_until_ready(self, timeout_s: float) -> None:
        end = time.monotonic() + timeout_s
        while time.monotonic() < end:
            if self.probe() == "ready":
                return
            time.sleep(1)
        raise RuntimeError(f"ChatGPT window not ready after {timeout_s:.0f}s; log in first")

    def _job(self, start_js: str) -> str:
        job = uuid.uuid4().hex
        self.window.run_js(start_js.replace("$ID", json.dumps(job)))
        end = time.monotonic() + TIMEOUT_S
        result = None
        while result is None:
            if time.monotonic() > end:
                raise RuntimeError("ChatGPT did not answer")
            time.sleep(0.2)
            result = self._js(f"window.__vox && window.__vox.result({json.dumps(job)})")
        # The JSON string can arrive encoded once more (WebView2).
        if isinstance(result, str):
            result = json.loads(result)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "unknown error"))
        return result["value"]

    def learn(self) -> None:
        self._job("window.__vox.startLearn($ID)")

    def transcribe(self, wav: bytes) -> str:
        b64 = base64.b64encode(wav).decode("ascii")
        return self._job(f"window.__vox.start($ID, {json.dumps(b64)}, 'audio/wav', 'audio.wav')")

    def show(self) -> None:
        """Bring the window up for the user to log in."""
        if IS_MAC:
            AppHelper.callAfter(self._reveal)
        self.window.show()

    def hide(self) -> None:
        """Take the window away from the user, but keep the page running.

        A window that is ordered out makes the page "hidden", and ChatGPT
        then never draws the Dictate button. A fully transparent window that
        ignores the mouse stays "visible" to the page and invisible to the
        user.
        """
        if IS_MAC:
            AppHelper.callAfter(self._conceal)
        else:
            self.window.hide()

    def _conceal(self) -> None:
        win = self.window.native
        win.setAlphaValue_(0.0)
        win.setIgnoresMouseEvents_(True)
        win.orderFrontRegardless()

    def _reveal(self) -> None:
        win = self.window.native
        # The window was put on screen at launch, on the Space that was
        # active then. Without this flag it opens on that Space, out of the
        # user's sight, and the login looks stuck.
        win.setCollectionBehavior_(1 << 1)  # NSWindowCollectionBehaviorMoveToActiveSpace
        win.setAlphaValue_(1.0)
        win.setIgnoresMouseEvents_(False)
        win.center()
        win.makeKeyAndOrderFront_(None)

    def logout(self) -> None:
        self.window.clear_cookies()
        self.window.load_url(HOME)


def run(on_ready) -> None:
    """Create a visible window and block in the GUI loop; on_ready runs in a worker thread."""
    win = ChatGPTWindow()
    webview.start(on_ready, (win,), private_mode=False, storage_path=str(profile_dir()))
