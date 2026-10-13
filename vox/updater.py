"""Update vox in place from GitHub Releases.

check() asks the GitHub API for the latest release; when its version is
newer, download() fetches the installer for this platform and checks its
SHA-256 against the digest GitHub reports. install() then swaps the app
and starts the new one:

- macOS: mount the .dmg, copy vox.app next to the running one, check that
  the copy has the same designated requirement (the same signing
  certificate), then a small shell script waits for this process to end,
  swaps the bundles and opens the new one. The certificate check means an
  update cannot replace vox with a build someone else signed, and it keeps
  the privacy permissions, which macOS ties to that requirement.
- Windows: a running .exe cannot be overwritten but can be renamed; a
  batch script waits, moves the new .exe in place and starts it.

Only a packaged build updates itself; running from source never does.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from vox.config import data_dir

REPO = "ziyangliu-666/vox"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
IS_MAC = sys.platform == "darwin"
FROZEN = getattr(sys, "frozen", False)


def _version(text: str) -> tuple:
    parts = []
    for p in text.lstrip("v").split("."):
        digits = "".join(c for c in p if c.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


def _get(url: str, timeout: float = 20):
    req = urllib.request.Request(url, headers={
        "User-Agent": "vox-updater", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


class Updater:
    def __init__(self, version: str) -> None:
        self.version = version
        self.latest: dict | None = None  # {"version", "url", "name", "digest"}
        self.file: Path | None = None  # The downloaded installer, checked.
        self.error: str | None = None

    @property
    def enabled(self) -> bool:
        return FROZEN

    def check(self) -> bool:
        """True when a newer release exists; sets self.latest."""
        try:
            with _get(API) as r:
                rel = json.load(r)
        except Exception as exc:
            # A private repository answers 404 to anonymous requests.
            self.error = f"check failed: {exc}"
            return False
        tag = rel.get("tag_name", "")
        if _version(tag) <= _version(self.version):
            self.latest = None
            return False
        want = ".dmg" if IS_MAC else ".exe"
        asset = next((a for a in rel.get("assets", []) if a["name"].endswith(want)), None)
        if not asset:
            return False
        self.latest = {"version": tag.lstrip("v"), "url": asset["browser_download_url"],
                       "name": asset["name"], "digest": asset.get("digest") or ""}
        return True

    def download(self) -> bool:
        """Fetch the installer for self.latest; True when it is ready."""
        if not self.latest:
            return False
        folder = data_dir() / "updates"
        folder.mkdir(exist_ok=True)
        dest = folder / self.latest["name"]
        if dest.exists() and self._digest_ok(dest):
            self.file = dest
            return True
        part = dest.with_suffix(dest.suffix + ".part")
        try:
            with _get(self.latest["url"], timeout=120) as r, open(part, "wb") as out:
                while chunk := r.read(1 << 16):
                    out.write(chunk)
        except Exception as exc:
            self.error = f"download failed: {exc}"
            part.unlink(missing_ok=True)
            return False
        if not self._digest_ok(part):
            self.error = "download failed: checksum mismatch"
            part.unlink(missing_ok=True)
            return False
        part.replace(dest)
        self.file = dest
        return True

    def _digest_ok(self, path: Path) -> bool:
        want = self.latest.get("digest", "") if self.latest else ""
        if not want.startswith("sha256:"):
            return True  # GitHub gave no digest; the signature check still runs.
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest() == want.split(":", 1)[1]

    def install(self) -> None:
        """Hand over to a script that swaps the app once this process ends.

        The caller quits right after; raises when the update cannot go in.
        """
        if not (FROZEN and self.file):
            raise RuntimeError("no update ready")
        if IS_MAC:
            self._install_mac()
        else:
            self._install_windows()

    def _install_mac(self) -> None:
        current = Path(sys.executable).resolve().parents[2]  # .../vox.app
        staged = self._stage_mac(current)
        old = current.with_name("vox.app.old")
        script = (f'while kill -0 {os.getpid()} 2>/dev/null; do sleep 0.2; done; '
                  f'rm -rf "{old}"; mv "{current}" "{old}" && mv "{staged}" "{current}" '
                  f'&& rm -rf "{old}"; open "{current}"')
        subprocess.Popen(["/bin/sh", "-c", script], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _stage_mac(self, current: Path) -> Path:
        """Copy the new vox.app next to current and check its signer; return the copy."""
        if not os.access(current.parent, os.W_OK):
            raise RuntimeError(f"cannot write to {current.parent}")
        mount = Path(tempfile.mkdtemp(prefix="vox-update-"))
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint",
                        str(mount), str(self.file)], check=True, capture_output=True)
        try:
            new = next(mount.glob("*.app"))
            staged = current.with_name("vox.app.new")
            subprocess.run(["rm", "-rf", str(staged)], check=True)
            subprocess.run(["ditto", str(new), str(staged)], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount), "-quiet"], capture_output=True)
        # Same signer as the running app, or no update.
        shown = subprocess.run(["codesign", "-d", "-r-", str(current)],
                               capture_output=True, text=True)
        out = shown.stdout + shown.stderr
        line = next((ln for ln in out.splitlines() if ln.startswith("designated =>")), "")
        requirement = line.split("=>", 1)[1].strip() if line else ""
        if not requirement or "cdhash" in requirement:
            subprocess.run(["rm", "-rf", str(staged)])
            raise RuntimeError("the running app has no stable signature to compare with")
        check = subprocess.run(["codesign", "--verify", "--deep", "--strict",
                                f"-R={requirement}", str(staged)], capture_output=True, text=True)
        if check.returncode != 0:
            subprocess.run(["rm", "-rf", str(staged)])
            raise RuntimeError("the update is not signed with the vox certificate")
        return staged

    def _install_windows(self) -> None:
        current = Path(sys.executable).resolve()
        staged = current.with_name("vox.new.exe")
        old = current.with_name("vox.old.exe")
        staged.write_bytes(self.file.read_bytes())
        bat = Path(tempfile.gettempdir()) / f"vox-update-{int(time.time())}.cmd"
        bat.write_text(
            "@echo off\r\n"
            f":wait\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul "
            "&& (timeout /t 1 /nobreak >nul & goto wait)\r\n"
            f'del /f /q "{old}" 2>nul\r\n'
            f'move /y "{current}" "{old}"\r\n'
            f'move /y "{staged}" "{current}"\r\n'
            f'start "" "{current}"\r\n'
            f'del /f /q "{old}" 2>nul\r\n'
            'del "%~f0"\r\n', encoding="utf-8")
        subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x08000000)  # CREATE_NO_WINDOW
