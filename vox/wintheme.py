"""Light or dark title bars on Windows.

The page draws its own theme; the title bar is the system's, so it follows
through DWM. pywebview sets it once from the system theme at start.
"""

import ctypes
import winreg

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
SWP_FRAMECHANGED = 0x20
SWP_KEEP = 0x1 | 0x2 | 0x4 | 0x10  # NOSIZE | NOMOVE | NOZORDER | NOACTIVATE

_user32 = ctypes.WinDLL("user32")
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
_dwm = ctypes.WinDLL("dwmapi")
_dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]


def system_dark() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def is_dark(theme: str) -> bool:
    """theme is "system", "light" or "dark"."""
    return system_dark() if theme == "system" else theme == "dark"


def set_title_bar(form, dark: bool) -> None:
    """Run on the form's UI thread."""
    hwnd = form.Handle.ToInt64()
    value = ctypes.c_int(1 if dark else 0)
    _dwm.DwmSetWindowAttribute(hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE,
                               ctypes.byref(value), ctypes.sizeof(value))
    # The frame repaints in the new colours only when told it changed.
    _user32.SetWindowPos(hwnd, None, 0, 0, 0, 0, SWP_KEEP | SWP_FRAMECHANGED)
