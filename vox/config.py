"""User settings, stored as JSON in the per-user app data folder."""

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path


def data_dir() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    path = base / "vox"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_hotkey() -> list[str]:
    # Right Option on macOS and Right Ctrl on Windows are rarely used alone,
    # so holding them does not collide with normal shortcuts.
    return ["alt_r"] if sys.platform == "darwin" else ["ctrl_r"]


@dataclass
class Config:
    hotkey: list[str] = field(default_factory=default_hotkey)
    # Labels of character keys, keyed by key id, saved at capture time:
    # a virtual key code alone does not tell which letter it is.
    hotkey_labels: dict[str, str] = field(default_factory=dict)
    # The hotkey works one way: hold to talk, double-tap for hands-free.
    mode: str = "hold"
    output: str = "paste"  # "paste" or "copy"
    sounds: bool = True
    # Keep the idle capsule on screen. When off, the bar still shows while
    # the user dictates; the main window opens from the menu bar.
    show_bar: bool = True
    # Where the user dragged the flow bar; None means bottom centre.
    flow_bar: dict | None = None
    # Microphone by device name; None means the system default. A name,
    # because device numbers change when devices come and go.
    input_device: str | None = None
    theme: str = "system"  # "system", "light" or "dark"

    @classmethod
    def load(cls) -> "Config":
        try:
            raw = json.loads((data_dir() / "config.json").read_text(encoding="utf-8"))
            known = {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}
            cfg = cls(**known)
            cfg.mode = "hold"  # vox 0.2 also had a toggle mode.
            return cfg
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self) -> None:
        path = data_dir() / "config.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        tmp.replace(path)
