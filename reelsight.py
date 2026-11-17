"""ReelSight terminal version (Windows).

F6 = start / pause, F7 = quit.
`python reelsight.py test` checks detection without clicking.
For the window app, run `python app.py` instead.
"""

from __future__ import annotations

import sys
import time

import mss

import engine as eng

CONFIG_FILE = "config.json"


def status_line(engine: eng.Engine) -> str:
    stats = engine.stats
    return (
        f"[{engine.state}] casts {stats['casts']} | caught {stats['caught']} | "
        f"escaped {stats['escaped']} ({eng.success_text(stats)} ok) | "
        f"no bite {stats['no_bite']} | {stats['last']}"
    )


def test_mode(config: dict) -> None:
    print("Detection test (no clicking). Ctrl+C to stop.")
    with mss.MSS() as sct:
        try:
            while True:
                rect = eng.get_roblox_rect(config)
                if rect is None:
                    line = "Roblox window: not found or minimized"
                else:
                    line = (
                        f"window {rect['width']}x{rect['height']} | "
                        f"focused: {eng.roblox_is_focused(config)} | "
                        f"STOP red: {eng.count_red(sct, config)} "
                        f"(need {config['min_red_pixels']}) | "
                        f"escape text: {eng.count_escape(sct, config)} "
                        f"(need {config['escape_min_pixels']})"
                    )
                print("\r" + line.ljust(120), end="", flush=True)
                time.sleep(0.2)
        except KeyboardInterrupt:
            print()


def main() -> None:
    config = eng.load_config(CONFIG_FILE)
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_mode(config)
        return

    engine = eng.Engine(config)
    keys = eng.Keys()
    print("F6 = start/pause | F7 = quit")
    print("Click into Roblox, put the cursor over the water, then press F6.")
    try:
        while True:
            if keys.pressed(eng.VK_F7):
                break
            if keys.pressed(eng.VK_F6):
                engine.toggle()
            print("\r" + status_line(engine).ljust(120), end="", flush=True)
            time.sleep(0.05)
    except KeyboardInterrupt:
        pass
    finally:
        engine.shutdown()
        print("\nStopped.")


if __name__ == "__main__":
    main()
