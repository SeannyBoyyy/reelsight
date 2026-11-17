"""ReelSight: screen-reading auto-fishing helper (Windows)."""

import ctypes
import json
import os
import sys
import time
from ctypes import wintypes

import mss


USER32 = ctypes.windll.user32
ctypes.windll.shcore.SetProcessDpiAwareness(2)
CONFIG_FILE = "config.json"
VK_F6 = 0x75
VK_F7 = 0x76

DEFAULTS = {
    "cast_hold": 0.15,
    "bite_timeout": 20,
    "click_interval": 0.02,
    "reel_timeout": 25,
    "after_catch_delay": 4,
    "min_red_pixels": 15,
    "scan_step": 4,
    "area": {"left": 0.70, "right": 0.80, "top": 0.86, "bottom": 0.95},
    "escape_area": {"left": 0.42, "right": 0.58, "top": 0.83, "bottom": 0.90},
    "escape_min_pixels": 40,
    "escape_watch": 3.0,
    "require_focus": True,
}


class Paused(Exception):
    """Signal that the user paused the fishing loop."""


class Keys:
    def __init__(self) -> None:
        self.previous: dict[int, bool] = {}

    def pressed(self, virtual_key: int) -> bool:
        down = bool(USER32.GetAsyncKeyState(virtual_key) & 0x8000)
        was_down = self.previous.get(virtual_key, False)
        self.previous[virtual_key] = down
        return down and not was_down


keys = Keys()


def load_config() -> dict:
    config = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, encoding="utf-8") as file:
            saved = json.load(file)
        for key, value in saved.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value

    with open(CONFIG_FILE, "w", encoding="utf-8") as file:
        json.dump(config, file, indent=2)
    return config


def mouse_down() -> None:
    USER32.mouse_event(0x0002, 0, 0, 0, 0)


def mouse_up() -> None:
    USER32.mouse_event(0x0004, 0, 0, 0, 0)


def click() -> None:
    mouse_down()
    mouse_up()


def check_hotkeys() -> None:
    if keys.pressed(VK_F7):
        raise KeyboardInterrupt
    if keys.pressed(VK_F6):
        raise Paused


def wait_with_hotkeys(seconds: float) -> None:
    end = time.monotonic() + seconds
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return
        check_hotkeys()
        time.sleep(min(0.02, remaining))


def is_red(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red > 170 and green < 90 and blue < 90


def is_escape_text(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red > 170 and green < 120 and red - blue > 40


def get_roblox_window() -> int:
    return USER32.FindWindowW(None, "Roblox")


def get_roblox_rect() -> dict[str, int] | None:
    """Return Roblox's visible client area in screen coordinates."""
    window = get_roblox_window()
    if not window or USER32.IsIconic(window):
        return None

    client_rect = wintypes.RECT()
    if not USER32.GetClientRect(window, ctypes.byref(client_rect)):
        return None

    client_origin = wintypes.POINT(0, 0)
    if not USER32.ClientToScreen(window, ctypes.byref(client_origin)):
        return None

    return {
        "left": client_origin.x,
        "top": client_origin.y,
        "width": client_rect.right - client_rect.left,
        "height": client_rect.bottom - client_rect.top,
    }


def roblox_is_active() -> bool:
    window = get_roblox_window()
    return bool(
        window
        and not USER32.IsIconic(window)
        and USER32.GetForegroundWindow() == window
    )


def require_roblox_focus(config: dict) -> None:
    if config["require_focus"] and not roblox_is_active():
        raise Paused


def count_matching(
    screenshotter: mss.MSS, config: dict, area: dict, predicate
) -> int:
    window = get_roblox_rect()
    if window is None:
        return 0

    region = {
        "left": window["left"] + int(window["width"] * area["left"]),
        "top": window["top"] + int(window["height"] * area["top"]),
        "width": int(window["width"] * (area["right"] - area["left"])),
        "height": int(window["height"] * (area["bottom"] - area["top"])),
    }
    shot = screenshotter.grab(region)
    step = config["scan_step"]
    return sum(
        predicate(shot.pixel(x, y))
        for x in range(0, shot.width, step)
        for y in range(0, shot.height, step)
    )


def count_red(screenshotter: mss.MSS, config: dict) -> int:
    return count_matching(screenshotter, config, config["area"], is_red)


def count_escape(screenshotter: mss.MSS, config: dict) -> int:
    return count_matching(
        screenshotter, config, config["escape_area"], is_escape_text
    )


def stop_visible(screenshotter: mss.MSS, config: dict) -> bool:
    return count_red(screenshotter, config) >= config["min_red_pixels"]


def show_status(stats: dict, state: str) -> None:
    line = (
        f"[{state}] casts {stats['casts']} | reels {stats['reels']} | "
        f"caught {stats['caught']} | escaped {stats['escaped']} | "
        f"no bite {stats['no_bite']} | last reel: {stats['last']} | "
        "F6 start/pause  F7 quit"
    )
    print("\r" + line.ljust(110), end="", flush=True)


def reel(screenshotter: mss.MSS, config: dict, stats: dict) -> None:
    started_at = time.monotonic()
    last_check = started_at
    clicks = 0
    missing_checks = 0

    while time.monotonic() - started_at < config["reel_timeout"]:
        check_hotkeys()
        require_roblox_focus(config)
        click()
        clicks += 1
        time.sleep(config["click_interval"])

        now = time.monotonic()
        if now - last_check >= 0.3:
            last_check = now
            if stop_visible(screenshotter, config):
                missing_checks = 0
            else:
                missing_checks += 1
                if missing_checks >= 4:
                    break

    elapsed = time.monotonic() - started_at
    stats["reels"] += 1
    stats["last"] = f"{clicks} clicks, {clicks / elapsed:.0f}/s, {elapsed:.1f}s"


def watch_result(
    screenshotter: mss.MSS, config: dict, stats: dict
) -> None:
    deadline = time.monotonic() + config["escape_watch"]
    while time.monotonic() < deadline:
        check_hotkeys()
        if count_escape(screenshotter, config) >= config["escape_min_pixels"]:
            stats["escaped"] += 1
            return
        time.sleep(0.05)
    stats["caught"] += 1


def fishing_cycle(
    screenshotter: mss.MSS, config: dict, stats: dict
) -> None:
    if config["require_focus"]:
        while not roblox_is_active():
            show_status(stats, "waiting for Roblox")
            check_hotkeys()
            time.sleep(0.1)

    show_status(stats, "casting")
    require_roblox_focus(config)
    mouse_down()
    try:
        wait_with_hotkeys(config["cast_hold"])
    finally:
        mouse_up()
    stats["casts"] += 1

    show_status(stats, "waiting")
    deadline = time.monotonic() + config["bite_timeout"]
    while time.monotonic() < deadline:
        check_hotkeys()
        require_roblox_focus(config)
        if stop_visible(screenshotter, config):
            break
        time.sleep(0.05)
    else:
        stats["no_bite"] += 1
        return

    show_status(stats, "reeling")
    reel(screenshotter, config, stats)
    show_status(stats, "checking result")
    watch_result(screenshotter, config, stats)
    wait_with_hotkeys(
        max(0, config["after_catch_delay"] - config["escape_watch"])
    )


def test_mode(config: dict) -> None:
    print("Red pixel test. Press Ctrl+C to stop.")
    with mss.MSS() as screenshotter:
        try:
            while True:
                count = count_red(screenshotter, config)
                escape_count = count_escape(screenshotter, config)
                print(
                    f"\rred pixels: {count:<6} "
                    f"STOP visible: {count >= config['min_red_pixels']} | "
                    f"escape text: {escape_count:<6} "
                    f"visible: {escape_count >= config['escape_min_pixels']}   ",
                    end="",
                    flush=True,
                )
                time.sleep(0.2)
        except KeyboardInterrupt:
            print()


def main() -> None:
    config = load_config()
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_mode(config)
        return

    ctypes.windll.winmm.timeBeginPeriod(1)
    stats = {
        "casts": 0,
        "reels": 0,
        "no_bite": 0,
        "last": "-",
        "caught": 0,
        "escaped": 0,
    }
    running = False
    show_status(stats, "PAUSED")

    with mss.MSS() as screenshotter:
        try:
            while True:
                if not running:
                    if keys.pressed(VK_F6):
                        running = True
                    if keys.pressed(VK_F7):
                        break
                    time.sleep(0.05)
                    continue

                try:
                    fishing_cycle(screenshotter, config, stats)
                except Paused:
                    running = False
                    mouse_up()
                    show_status(stats, "PAUSED")
        except KeyboardInterrupt:
            pass
        finally:
            mouse_up()
            ctypes.windll.winmm.timeEndPeriod(1)
            print("\nStopped.")


if __name__ == "__main__":
    main()
