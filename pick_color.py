"""Print the screen position and RGB color under the mouse cursor."""

import ctypes
import time
from ctypes import wintypes

import mss


def calibrate() -> None:
    """Continuously display the cursor position and pixel color."""
    point = wintypes.POINT()

    with mss.MSS() as screenshotter:
        try:
            while True:
                ctypes.windll.user32.GetCursorPos(ctypes.byref(point))
                screenshot = screenshotter.grab(
                    {
                        "left": point.x,
                        "top": point.y,
                        "width": 1,
                        "height": 1,
                    }
                )
                print(
                    point.x,
                    point.y,
                    screenshot.pixel(0, 0),
                    "        ",
                    end="\r",
                )
                time.sleep(0.1)
        except KeyboardInterrupt:
            print("\nStopped.")


if __name__ == "__main__":
    calibrate()
