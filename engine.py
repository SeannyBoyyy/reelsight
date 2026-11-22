"""ReelSight engine: screen reading, mouse clicks and the fishing loop.

Windows only. Used by both the window app (app.py) and the terminal
version (reelsight.py).
"""

from __future__ import annotations

import ctypes
import json
import os
import threading
import time
from ctypes import wintypes

import mss

__version__ = "0.2.5"

ctypes.windll.shcore.SetProcessDpiAwareness(2)  # must run before any window exists

USER32 = ctypes.windll.user32
USER32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
USER32.FindWindowW.restype = wintypes.HWND
USER32.IsIconic.argtypes = [wintypes.HWND]
USER32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
USER32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
USER32.GetForegroundWindow.restype = wintypes.HWND
USER32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]

VK_F6, VK_F7, VK_F8 = 0x75, 0x76, 0x77
VK_ESCAPE = 0x1B


def _build_key_table() -> dict[str, int]:
    """Key name -> Windows virtual-key code, for every key allowed as the hotkey.

    Left out on purpose: Esc (cancels hotkey capture), Shift/Ctrl/Alt/Windows
    (they combine with other keys) and mouse buttons.
    """
    table: dict[str, int] = {}
    for n in range(1, 25):
        table[f"F{n}"] = 0x6F + n  # F1 = 0x70
    for i in range(26):
        table[chr(ord("A") + i)] = 0x41 + i
    for i in range(10):
        table[str(i)] = 0x30 + i
        table[f"Num {i}"] = 0x60 + i
    table.update({
        "Num *": 0x6A, "Num +": 0x6B, "Num -": 0x6D, "Num .": 0x6E, "Num /": 0x6F,
        "Space": 0x20, "Enter": 0x0D, "Tab": 0x09, "Backspace": 0x08,
        "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23,
        "Page Up": 0x21, "Page Down": 0x22,
        "Left": 0x25, "Up": 0x26, "Right": 0x27, "Down": 0x28,
        "Caps Lock": 0x14, "Pause": 0x13, "Scroll Lock": 0x91, "Num Lock": 0x90,
        "Print Screen": 0x2C,
        ";": 0xBA, "=": 0xBB, ",": 0xBC, "-": 0xBD, ".": 0xBE, "/": 0xBF,
        "`": 0xC0, "[": 0xDB, "\\": 0xDC, "]": 0xDD, "'": 0xDE,
    })
    return table


KEY_CODES = _build_key_table()
KEY_NAMES = {code: name for name, code in KEY_CODES.items()}
CAPTURE_VKS = sorted(KEY_NAMES)

# Not allowed as the start/pause hotkey:
#   F7 = quit in the terminal version, F8 = screenshot capture for the area picker
HOTKEY_RESERVED = {"F7", "F8"}

# Keys that do not type a character or move the player in Roblox. Any other key
# is allowed, but the app asks for confirmation first.
SAFE_HOTKEYS = ({f"F{n}" for n in range(1, 25)} - {"F9", "F11"}) | {
    "Insert", "Delete", "Home", "End", "Page Up", "Page Down",
    "Pause", "Scroll Lock", "Num Lock", "Print Screen",
}
_CANONICAL = {name.upper(): name for name in KEY_CODES}

DEFAULTS = {
    "window_title": "Roblox",
    "cast_hold": 0.15,           # seconds to hold the mouse button when casting
    "bite_timeout": 20,          # seconds to wait for a bite before recasting
    "click_interval": 0.02,      # seconds between clicks while reeling
    "reel_timeout": 25,          # max seconds of reeling per fish
    "after_catch_delay": 4,      # seconds to let the catch popup clear
    "after_escape_delay": 0.5,   # seconds before recasting after a fish gets away
    "min_red_pixels": 15,        # red pixels needed to count STOP as visible
    "scan_step": 4,              # pixel skip when scanning (higher = faster)
    # Areas are fractions of the Roblox window: left, right, top, bottom
    "area": {"left": 0.70, "right": 0.80, "top": 0.86, "bottom": 0.95},
    "escape_area": {"left": 0.42, "right": 0.58, "top": 0.83, "bottom": 0.90},
    "escape_min_pixels": 40,
    "escape_watch": 3.0,         # seconds to look for the escape text
    "require_focus": True,       # only act while Roblox is the active window
    "toggle_hotkey": "F6",       # start/pause key name, e.g. F9, Insert, Q (not F7/F8)
    # The cursor must stay inside this box (over the water), so clicks never
    # land on Shop, Index, STOP, the hotbar or the Roblox menu.
    "safe_zone": {"left": 0.20, "right": 0.80, "top": 0.15, "bottom": 0.80},
}


# ---------- config ----------

def normalize_hotkey(name) -> str:
    """Return a valid hotkey name (case-insensitive), falling back to the default."""
    key = _CANONICAL.get(str(name).strip().upper())
    return key if key and key not in HOTKEY_RESERVED else DEFAULTS["toggle_hotkey"]


def hotkey_vk(name) -> int:
    """Windows virtual-key code for a hotkey name such as "F6" or "Insert"."""
    return KEY_CODES[normalize_hotkey(name)]


def is_risky_hotkey(name) -> bool:
    """True for keys that type or move in Roblox (letters, digits, Space, arrows...)."""
    return normalize_hotkey(name) not in SAFE_HOTKEYS


def load_config(path: str) -> dict:
    config = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as file:
                saved = json.load(file)
        except (OSError, ValueError):
            saved = {}
        for key, value in saved.items():
            if key not in config:
                continue
            if isinstance(value, dict) and isinstance(config[key], dict):
                config[key].update(value)
            else:
                config[key] = value
    config["toggle_hotkey"] = normalize_hotkey(config["toggle_hotkey"])
    # escape_watch is no longer in the Settings panel. Reset values that are
    # missing, not numbers, or too short to be useful (older builds allowed 0.2).
    try:
        watch = float(config["escape_watch"])
    except (TypeError, ValueError):
        watch = 0.0
    config["escape_watch"] = watch if watch >= 1.0 else DEFAULTS["escape_watch"]
    save_config(config, path)
    return config


def save_config(config: dict, path: str) -> None:
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)


# ---------- input ----------

class Keys:
    """Edge-triggered global key polling (fires once per key press)."""

    def __init__(self) -> None:
        self.previous: dict[int, bool] = {}

    def pressed(self, virtual_key: int) -> bool:
        down = bool(USER32.GetAsyncKeyState(virtual_key) & 0x8000)
        was_down = self.previous.get(virtual_key, False)
        self.previous[virtual_key] = down
        return down and not was_down


def mouse_down() -> None:
    USER32.mouse_event(0x0002, 0, 0, 0, 0)


def mouse_up() -> None:
    USER32.mouse_event(0x0004, 0, 0, 0, 0)


def click() -> None:
    mouse_down()
    mouse_up()


# ---------- Roblox window ----------

def find_roblox(config: dict):
    return USER32.FindWindowW(None, config["window_title"])


def get_roblox_rect(config: dict) -> dict | None:
    """Roblox's visible client area in screen coordinates, or None."""
    window = find_roblox(config)
    if not window or USER32.IsIconic(window):
        return None
    client = wintypes.RECT()
    if not USER32.GetClientRect(window, ctypes.byref(client)):
        return None
    origin = wintypes.POINT(0, 0)
    if not USER32.ClientToScreen(window, ctypes.byref(origin)):
        return None
    width, height = client.right - client.left, client.bottom - client.top
    if width <= 0 or height <= 0:
        return None
    return {"left": origin.x, "top": origin.y, "width": width, "height": height}


def roblox_is_focused(config: dict) -> bool:
    window = find_roblox(config)
    return bool(window) and USER32.GetForegroundWindow() == window


def roblox_ready(config: dict) -> bool:
    """True only when it is safe to click: Roblox visible, focused, cursor over water."""
    rect = get_roblox_rect(config)
    if rect is None:
        return False
    if config["require_focus"] and not roblox_is_focused(config):
        return False
    point = wintypes.POINT()
    USER32.GetCursorPos(ctypes.byref(point))
    zone = config["safe_zone"]
    return (
        rect["left"] + rect["width"] * zone["left"] <= point.x
        < rect["left"] + rect["width"] * zone["right"]
        and rect["top"] + rect["height"] * zone["top"] <= point.y
        < rect["top"] + rect["height"] * zone["bottom"]
    )


# ---------- detection ----------

def is_red(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red > 170 and green < 90 and blue < 90


def is_escape_text(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red > 170 and green < 120 and red - blue > 40  # red-pink, not purple water


def area_to_region(rect: dict, area: dict) -> dict:
    return {
        "left": rect["left"] + int(rect["width"] * area["left"]),
        "top": rect["top"] + int(rect["height"] * area["top"]),
        "width": max(1, round(rect["width"] * (area["right"] - area["left"]))),
        "height": max(1, round(rect["height"] * (area["bottom"] - area["top"]))),
    }


def count_in_shot(shot, step: int, predicate, box=None) -> int:
    """Count matching pixels in a screenshot, optionally inside box=(l, t, r, b)."""
    left, top, right, bottom = box or (0, 0, shot.width, shot.height)
    count = 0
    for x in range(left, right, step):
        for y in range(top, bottom, step):
            if predicate(shot.pixel(x, y)):
                count += 1
    return count


def count_matching(sct, config: dict, area: dict, predicate) -> int:
    rect = get_roblox_rect(config)
    if rect is None:
        return 0
    shot = sct.grab(area_to_region(rect, area))
    return count_in_shot(shot, config["scan_step"], predicate)


def count_red(sct, config: dict) -> int:
    return count_matching(sct, config, config["area"], is_red)


def count_escape(sct, config: dict) -> int:
    return count_matching(sct, config, config["escape_area"], is_escape_text)


def stop_visible(sct, config: dict) -> bool:
    return count_red(sct, config) >= config["min_red_pixels"]


def escape_visible(sct, config: dict) -> bool:
    return count_escape(sct, config) >= config["escape_min_pixels"]


# ---------- stats ----------

def new_stats() -> dict:
    return {"casts": 0, "reels": 0, "caught": 0, "escaped": 0, "no_bite": 0, "last": "-"}


def success_text(stats: dict) -> str:
    total = stats["caught"] + stats["escaped"]
    return f"{100 * stats['caught'] / total:.0f}%" if total else "-"


# ---------- fishing loop ----------

class Stopped(Exception):
    """The engine is shutting down."""


class Paused(Exception):
    """The user paused the loop."""


class Lost(Exception):
    """Roblox is minimized, unfocused, or the cursor left the safe zone."""


class Engine:
    """Runs the fishing loop on a background thread.

    Start, pause and read state from any thread. `state` and `stats` are
    plain values that a window can poll.
    """

    def __init__(self, config: dict) -> None:
        self.config = config
        self.stats = new_stats()
        self.state = "Paused"
        self._active = False
        self._active_since = 0.0
        self._active_total = 0.0
        self._quit = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # --- control ---

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._quit.clear()
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()
            if not self._active:
                self._active = True
                self._active_since = time.monotonic()

    def pause(self) -> None:
        with self._lock:
            if self._active:
                self._active = False
                self._active_total += time.monotonic() - self._active_since

    def toggle(self) -> None:
        if self._active:
            self.pause()
        else:
            self.start()

    def shutdown(self) -> None:
        self._quit.set()
        self.pause()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        mouse_up()

    def reset_stats(self) -> None:
        self.stats = new_stats()
        with self._lock:
            self._active_total = 0.0
            self._active_since = time.monotonic()

    def runtime(self) -> float:
        with self._lock:
            total = self._active_total
            if self._active:
                total += time.monotonic() - self._active_since
            return total

    # --- internals ---

    def _check(self, full: bool = True) -> None:
        if self._quit.is_set():
            raise Stopped
        if not self._active:
            raise Paused
        if full and not roblox_ready(self.config):
            raise Lost

    def _sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            self._check(False)
            time.sleep(min(0.02, remaining))

    def _run(self) -> None:
        ctypes.windll.winmm.timeBeginPeriod(1)  # accurate 1 ms sleeps = faster clicks
        try:
            with mss.MSS() as sct:
                while not self._quit.is_set():
                    if not self._active:
                        self.state = "Paused"
                        time.sleep(0.05)
                        continue
                    if not roblox_ready(self.config):
                        self.state = (
                            "Waiting: focus Roblox and put the cursor over the water"
                        )
                        time.sleep(0.1)
                        continue
                    try:
                        self._cycle(sct)
                    except (Paused, Lost):
                        pass
                    except Stopped:
                        break
                    finally:
                        mouse_up()
        finally:
            mouse_up()
            ctypes.windll.winmm.timeEndPeriod(1)

    def _cycle(self, sct) -> None:
        config = self.config
        self.state = "Casting"
        self._check()
        mouse_down()
        try:
            self._sleep(config["cast_hold"])
        finally:
            mouse_up()
        self.stats["casts"] += 1

        self.state = "Waiting for a bite"
        deadline = time.monotonic() + config["bite_timeout"]
        bite = False
        while time.monotonic() < deadline:
            self._check()
            if stop_visible(sct, config):
                bite = True
                break
            time.sleep(0.05)
        if not bite:
            self.stats["no_bite"] += 1
            return

        self.state = "Reeling"
        spent = 0.0
        escaped = self._reel(sct)
        if escaped:
            self.stats["escaped"] += 1
        else:
            self.state = "Checking result"
            started = time.monotonic()
            escaped = self._watch_result(sct)
            spent = time.monotonic() - started

        if escaped:
            # No reward popup follows an escape, so the next cast can go out sooner.
            self.state = "Fish got away"
            delay = config["after_escape_delay"]
        else:
            self.state = "Catch popup"
            delay = config["after_catch_delay"]
        self._sleep(max(0.0, delay - spent))

    def _reel(self, sct) -> bool:
        """Click fast while STOP is visible. Returns True if the fish escaped."""
        config = self.config
        started = time.monotonic()
        last_check = started
        clicks = 0
        gone = 0
        escaped = False
        while time.monotonic() - started < config["reel_timeout"]:
            self._check(False)
            click()
            clicks += 1
            time.sleep(config["click_interval"])
            now = time.monotonic()
            if now - last_check >= 0.3:
                last_check = now
                self._check(True)
                if escape_visible(sct, config):
                    escaped = True
                    break
                gone = 0 if stop_visible(sct, config) else gone + 1
                if gone >= 4:
                    break
        elapsed = max(time.monotonic() - started, 0.001)
        self.stats["reels"] += 1
        self.stats["last"] = f"{clicks / elapsed:.0f} clicks/s"
        return escaped

    def _watch_result(self, sct) -> bool:
        """After reeling ends, look briefly for the escape text. True if it escaped."""
        config = self.config
        # Never watch longer than the catch delay, so After-catch delay alone
        # sets how soon the next cast goes out after a catch.
        end = time.monotonic() + min(config["escape_watch"], config["after_catch_delay"])
        while time.monotonic() < end:
            self._check()
            if escape_visible(sct, config):
                self.stats["escaped"] += 1
                return True
            time.sleep(0.05)
        self.stats["caught"] += 1
        return False
