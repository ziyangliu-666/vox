"""Quiet start and stop ticks, so the user knows the state without looking."""

import sys

IS_MAC = sys.platform == "darwin"
enabled = True


def _play(mac_name: str, win_alias: str) -> None:
    if not enabled:
        return
    try:
        if IS_MAC:
            from AppKit import NSSound

            snd = NSSound.soundNamed_(mac_name)
            if snd:
                snd.setVolume_(0.25)
                snd.play()
        elif sys.platform == "win32":
            import winsound

            winsound.PlaySound(win_alias, winsound.SND_ALIAS | winsound.SND_ASYNC)
    except Exception:
        pass  # A missing sound must never break dictation.


def start() -> None:
    _play("Tink", "SystemAsterisk")


def stop() -> None:
    _play("Pop", "SystemDefault")
