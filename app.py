"""ReelSight window app (Windows). Run: python app.py

Start/pause hotkey: F6 by default (change it under Settings).
F8 = capture a screenshot for the area picker.
"""

from __future__ import annotations

import base64
import math
import os
import sys
import time
import tkinter as tk
from tkinter import messagebox, ttk

import mss
import mss.tools

import engine as eng  # import first: it sets DPI awareness before any window exists

APP_NAME = "ReelSight"

# (label, config key, type, minimum value)
SETTINGS_FIELDS = [
    ("Click interval (s)", "click_interval", float, 0.005),
    ("Cast hold (s)", "cast_hold", float, 0.05),
    ("Bite timeout (s)", "bite_timeout", float, 1.0),
    ("Reel timeout (s)", "reel_timeout", float, 1.0),
    ("After-catch delay (s)", "after_catch_delay", float, 0.0),
    ("Min STOP pixels", "min_red_pixels", int, 1),
    ("Min escape pixels", "escape_min_pixels", int, 1),
]

PICK_LABELS = {
    "area": "STOP button",
    "escape_area": "'It got away...' text",
}

# Status colors (dark enough to read on the default light theme)
COLOR_RUNNING = "#1a7f37"   # green
COLOR_WAITING = "#b26a00"   # amber
COLOR_PAUSED = "#666666"    # gray
WAITING_PREFIX = "Waiting:"  # engine.py state text when Roblox is not ready

DEFAULT_HINT = (
    "1. Click Start.  2. Click into Roblox and hover over the water.\n"
    "Keep this window beside Roblox, not on top of it."
)


def resource_path(relative: str) -> str:
    """Path to a bundled file, in source runs and inside a PyInstaller exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, relative)


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, "config.json")


def box_to_fractions(box, scale: int, width: int, height: int) -> dict:
    """Convert a box drawn on a scaled-down image into window fractions."""

    def clamp(value: float) -> float:
        return max(0.0, min(1.0, value))

    x0, y0, x1, y1 = box
    return {
        "left": round(clamp(x0 * scale / width), 4),
        "right": round(clamp(x1 * scale / width), 4),
        "top": round(clamp(y0 * scale / height), 4),
        "bottom": round(clamp(y1 * scale / height), 4),
    }


class Picker(tk.Toplevel):
    """Shows a frozen screenshot of Roblox; the user drags a box around a target."""

    def __init__(self, master, shot, label: str, on_save) -> None:
        super().__init__(master)
        self.title(f"Drag a box around the {label}")
        self.transient(master)
        self.shot = shot
        self.on_save = on_save
        self.start = None
        self.box = None
        self.rect_id = None

        screen_w, screen_h = self.winfo_screenwidth(), self.winfo_screenheight()
        self.scale = max(
            1,
            math.ceil(max(shot.width / (screen_w * 0.85), shot.height / (screen_h * 0.7))),
        )
        png = mss.tools.to_png(shot.rgb, shot.size)
        image = tk.PhotoImage(data=base64.b64encode(png).decode("ascii"))
        if self.scale > 1:
            image = image.subsample(self.scale)
        self.image = image  # keep a reference so Tk doesn't discard it

        ttk.Label(
            self, text=f"Drag a box tightly around the {label}, then click Save."
        ).pack(padx=8, pady=(8, 4))
        self.canvas = tk.Canvas(
            self,
            width=image.width(),
            height=image.height(),
            cursor="crosshair",
            highlightthickness=0,
        )
        self.canvas.pack(padx=8)
        self.canvas.create_image(0, 0, anchor="nw", image=image)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)

        buttons = ttk.Frame(self)
        buttons.pack(pady=8)
        ttk.Button(buttons, text="Save", command=self.save).pack(side="left", padx=4)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="left", padx=4)
        self.bind("<Escape>", lambda _event: self.destroy())

    def on_press(self, event) -> None:
        self.start = (event.x, event.y)
        if self.rect_id is not None:
            self.canvas.delete(self.rect_id)
        self.rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y, outline="#ff3030", width=2
        )

    def on_drag(self, event) -> None:
        if self.start is not None and self.rect_id is not None:
            self.canvas.coords(self.rect_id, self.start[0], self.start[1], event.x, event.y)

    def on_release(self, event) -> None:
        if self.start is None:
            return
        width, height = self.image.width(), self.image.height()
        x0, x1 = sorted((self.start[0], event.x))
        y0, y1 = sorted((self.start[1], event.y))
        x0, x1 = max(0, x0), min(width, x1)
        y0, y1 = max(0, y0), min(height, y1)
        self.box = (x0, y0, x1, y1) if x1 - x0 >= 3 and y1 - y0 >= 3 else None

    def save(self) -> None:
        if self.box is None:
            messagebox.showinfo(APP_NAME, "Drag a box first.", parent=self)
            return
        fractions = box_to_fractions(self.box, self.scale, self.shot.width, self.shot.height)
        pixel_box = (
            int(fractions["left"] * self.shot.width),
            int(fractions["top"] * self.shot.height),
            int(fractions["right"] * self.shot.width),
            int(fractions["bottom"] * self.shot.height),
        )
        self.on_save(self.shot, fractions, pixel_box)
        self.destroy()


class App:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.cfg_path = config_path()
        self.config = eng.load_config(self.cfg_path)
        self.engine = eng.Engine(self.config)
        self.keys = eng.Keys()
        self.sct = mss.MSS()  # main-thread screenshotter (the engine has its own)
        self.pending_pick: str | None = None
        self.capturing = False  # True while waiting for the user to press a new hotkey
        self.tick = 0

        self.build_ui()
        self.sync_fields()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.after(50, self.poll)

    # ---------- UI ----------

    def build_ui(self) -> None:
        root = self.root
        root.title(f"{APP_NAME} {eng.__version__}")
        root.resizable(False, False)
        try:
            root.iconbitmap(default=resource_path(os.path.join("assets", "reelsight.ico")))
        except Exception:
            pass  # missing icon is not fatal
        main = ttk.Frame(root, padding=12)
        main.grid()

        ttk.Label(main, text=APP_NAME, font=("Segoe UI", 16, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        self.state_var = tk.StringVar(value="\u25cf Paused")
        self.state_label = ttk.Label(
            main,
            textvariable=self.state_var,
            font=("Segoe UI", 10, "bold"),
            foreground=COLOR_PAUSED,
            wraplength=380,
        )
        self.state_label.grid(row=1, column=0, sticky="w", pady=(2, 8))

        buttons = ttk.Frame(main)
        buttons.grid(row=2, column=0, sticky="w")
        self.toggle_btn = ttk.Button(
            buttons,
            text=f"Start ({self.config['toggle_hotkey']})",
            width=18,
            command=self.toggle,
        )
        self.toggle_btn.pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Reset stats", command=self.engine.reset_stats).pack(
            side="left"
        )

        # Stats
        stats_box = ttk.LabelFrame(main, text="Stats", padding=8)
        stats_box.grid(row=3, column=0, sticky="we", pady=(10, 0))
        self.stat_vars: dict[str, tk.StringVar] = {}
        stat_names = [
            ("casts", "Casts"),
            ("caught", "Caught"),
            ("escaped", "Escaped"),
            ("rate", "Success"),
            ("no_bite", "No bite"),
            ("last", "Last reel"),
            ("runtime", "Runtime"),
        ]
        for index, (key, label) in enumerate(stat_names):
            row, col = index // 2, (index % 2) * 2
            ttk.Label(stats_box, text=label + ":").grid(
                row=row, column=col, sticky="w", padx=(0, 6)
            )
            var = tk.StringVar(value="-")
            self.stat_vars[key] = var
            ttk.Label(stats_box, textvariable=var, width=14).grid(
                row=row, column=col + 1, sticky="w", padx=(0, 14)
            )

        # Settings (hidden until the user opens it)
        self.settings_visible = False
        self.settings_toggle_btn = ttk.Button(
            main, text="Settings \u25b8", command=self.toggle_settings
        )
        self.settings_toggle_btn.grid(row=4, column=0, sticky="w", pady=(10, 0))
        settings_box = ttk.LabelFrame(main, text="Settings", padding=8)
        settings_box.grid(row=5, column=0, sticky="we", pady=(6, 0))
        settings_box.grid_remove()
        self.settings_box = settings_box
        self.field_vars: dict[str, tk.StringVar] = {}
        for index, (label, key, _kind, _low) in enumerate(SETTINGS_FIELDS):
            row, col = index // 2, (index % 2) * 2
            ttk.Label(settings_box, text=label).grid(
                row=row, column=col, sticky="w", padx=(0, 6), pady=2
            )
            var = tk.StringVar()
            self.field_vars[key] = var
            ttk.Entry(settings_box, textvariable=var, width=8).grid(
                row=row, column=col + 1, sticky="w", padx=(0, 14), pady=2
            )
        hotkey_row = (len(SETTINGS_FIELDS) + 1) // 2
        ttk.Label(settings_box, text="Start/Pause hotkey").grid(
            row=hotkey_row, column=0, sticky="w", padx=(0, 6), pady=2
        )
        self.hotkey_var = tk.StringVar()
        ttk.Label(
            settings_box,
            textvariable=self.hotkey_var,
            font=("Segoe UI", 10, "bold"),
            width=14,
        ).grid(row=hotkey_row, column=1, sticky="w", pady=2)
        ttk.Button(settings_box, text="Change...", command=self.start_hotkey_capture).grid(
            row=hotkey_row, column=2, sticky="w", pady=2
        )
        last_row = hotkey_row + 1
        self.focus_var = tk.BooleanVar()
        ttk.Checkbutton(
            settings_box, text="Only act while Roblox is focused", variable=self.focus_var
        ).grid(row=last_row, column=0, columnspan=3, sticky="w", pady=(4, 0))
        ttk.Button(settings_box, text="Save settings", command=self.save_settings).grid(
            row=last_row, column=3, sticky="e", pady=(4, 0)
        )

        # Detection
        detect_box = ttk.LabelFrame(main, text="Detection", padding=8)
        detect_box.grid(row=6, column=0, sticky="we", pady=(10, 0))
        self.live_var = tk.StringVar(value="-")
        ttk.Label(detect_box, textvariable=self.live_var, wraplength=380).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=(0, 6)
        )
        ttk.Button(
            detect_box, text="Set STOP area...", command=lambda: self.start_pick("area")
        ).grid(row=1, column=0, padx=(0, 6))
        ttk.Button(
            detect_box,
            text="Set escape area...",
            command=lambda: self.start_pick("escape_area"),
        ).grid(row=1, column=1, padx=(0, 6))
        ttk.Button(detect_box, text="Reset areas", command=self.reset_areas).grid(
            row=1, column=2
        )

        # Hint + footer
        self.hint_var = tk.StringVar(value=DEFAULT_HINT)
        ttk.Label(main, textvariable=self.hint_var, wraplength=380, justify="left").grid(
            row=7, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Label(
            main,
            text=(
                "Not affiliated with Roblox. Automation may break a game's rules; "
                "use at your own risk."
            ),
            wraplength=380,
            foreground="#666666",
            font=("Segoe UI", 8),
        ).grid(row=8, column=0, sticky="w", pady=(8, 0))

    def sync_fields(self) -> None:
        for _label, key, _kind, _low in SETTINGS_FIELDS:
            self.field_vars[key].set(str(self.config[key]))
        self.focus_var.set(bool(self.config["require_focus"]))
        self.hotkey_var.set(eng.normalize_hotkey(self.config["toggle_hotkey"]))

    # ---------- actions ----------

    def toggle_settings(self) -> None:
        self.settings_visible = not self.settings_visible
        if self.settings_visible:
            self.settings_box.grid()
            self.settings_toggle_btn.config(text="Settings \u25be")
        else:
            self.settings_box.grid_remove()
            self.settings_toggle_btn.config(text="Settings \u25b8")

    def toggle(self) -> None:
        self.cancel_pick()
        self.engine.toggle()

    def cancel_pick(self) -> None:
        if self.pending_pick is not None:
            self.pending_pick = None
            self.hint_var.set(DEFAULT_HINT)

    def save_settings(self) -> None:
        new_values = {}
        for label, key, kind, low in SETTINGS_FIELDS:
            try:
                value = kind(self.field_vars[key].get().strip())
            except ValueError:
                messagebox.showerror(APP_NAME, f"Enter a number for: {label}")
                return
            if value < low:
                messagebox.showerror(APP_NAME, f"{label} must be at least {low}.")
                return
            new_values[key] = value
        self.config.update(new_values)
        self.config["require_focus"] = bool(self.focus_var.get())
        eng.save_config(self.config, self.cfg_path)
        self.hint_var.set("Settings saved.")

    def reset_areas(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Reset the STOP and escape areas to defaults?"):
            return
        for key in ("area", "escape_area"):
            self.config[key] = dict(eng.DEFAULTS[key])
        for key in ("min_red_pixels", "escape_min_pixels"):
            self.config[key] = eng.DEFAULTS[key]
        eng.save_config(self.config, self.cfg_path)
        self.sync_fields()
        self.hint_var.set("Areas reset to defaults.")

    # ---------- area picker ----------

    def start_pick(self, key: str) -> None:
        self.engine.pause()
        self.pending_pick = key
        self.hint_var.set(
            f"Go to Roblox and wait until the {PICK_LABELS[key]} is on screen, "
            "then press F8. Don't cover the game with this window."
        )

    def capture_for_pick(self) -> None:
        key = self.pending_pick
        if key is None:
            return
        rect = eng.get_roblox_rect(self.config)
        if rect is None:
            self.hint_var.set("Roblox window not found (or minimized). Try F8 again.")
            return
        try:
            shot = self.sct.grab(rect)
        except Exception as error:
            self.hint_var.set(f"Could not capture the screen: {error}")
            return
        self.pending_pick = None
        self.hint_var.set(DEFAULT_HINT)
        Picker(
            self.root,
            shot,
            PICK_LABELS[key],
            lambda s, fractions, box, k=key: self.on_picked(k, s, fractions, box),
        )

    def on_picked(self, key: str, shot, fractions: dict, pixel_box) -> None:
        predicate = eng.is_red if key == "area" else eng.is_escape_text
        threshold_key = "min_red_pixels" if key == "area" else "escape_min_pixels"
        count = eng.count_in_shot(shot, self.config["scan_step"], predicate, pixel_box)

        self.config[key] = fractions
        if count >= 3:
            self.config[threshold_key] = max(3, count // 2)
            note = (
                f"Area saved. Found {count} matching pixels, so the minimum was set "
                f"to {self.config[threshold_key]}."
            )
        else:
            note = (
                f"Area saved, but only {count} matching pixels were found in it. "
                "The box may be on the wrong spot, or the color check may not match "
                "your screen."
            )
        eng.save_config(self.config, self.cfg_path)
        self.sync_fields()
        self.hint_var.set(note)
        messagebox.showinfo(APP_NAME, note)

    # ---------- hotkey capture ----------

    def start_hotkey_capture(self) -> None:
        self.cancel_pick()
        self.engine.pause()
        for code in eng.CAPTURE_VKS + [eng.VK_ESCAPE]:
            self.keys.pressed(code)  # prime: ignore keys that are already held down
        self.capturing = True
        self.hotkey_var.set("Press a key...")
        self.hint_var.set("Press the key you want for Start/Pause. Esc cancels.")

    def finish_hotkey_capture(self, note: str) -> None:
        self.capturing = False
        self.hotkey_var.set(eng.normalize_hotkey(self.config["toggle_hotkey"]))
        self.hint_var.set(note)

    def poll_hotkey_capture(self) -> None:
        if self.keys.pressed(eng.VK_ESCAPE):
            self.finish_hotkey_capture("Hotkey unchanged.")
            return
        for code in eng.CAPTURE_VKS:
            if not self.keys.pressed(code):
                continue
            name = eng.KEY_NAMES[code]
            if name in eng.HOTKEY_RESERVED:
                self.hint_var.set(f"{name} is reserved. Press a different key (Esc cancels).")
                return
            self.capturing = False  # stop listening while a dialog is open
            if eng.is_risky_hotkey(name) and not messagebox.askyesno(
                APP_NAME,
                f"The {name} key is also used for typing or moving in Roblox. "
                "ReelSight will start or pause every time you press it, "
                "including while you chat or play.\n\n"
                "Safer choices: F6, Insert, Delete, Home, End.\n\n"
                "Use it anyway?",
                icon="warning",
                default="no",
            ):
                self.finish_hotkey_capture("Hotkey unchanged.")
                return
            self.config["toggle_hotkey"] = name
            eng.save_config(self.config, self.cfg_path)
            self.finish_hotkey_capture(f"Start/Pause hotkey set to {name}.")
            return

    # ---------- refresh loop ----------

    def poll(self) -> None:
        try:
            if self.capturing:
                self.poll_hotkey_capture()
            else:
                if self.keys.pressed(eng.hotkey_vk(self.config["toggle_hotkey"])):
                    self.toggle()
                if self.keys.pressed(eng.VK_F8):
                    self.capture_for_pick()
            self.refresh()
            self.tick += 1
            if self.tick % 6 == 0:
                self.update_live()
        except Exception as error:  # keep the loop alive no matter what
            self.hint_var.set(f"Error: {error}")
        finally:
            self.root.after(50, self.poll)

    def refresh(self) -> None:
        engine = self.engine
        if not engine.is_active:
            text, color = "Paused", COLOR_PAUSED
        elif engine.state.startswith(WAITING_PREFIX):
            text, color = engine.state, COLOR_WAITING
        else:
            text, color = engine.state, COLOR_RUNNING
        self.state_var.set(f"\u25cf {text}")
        self.state_label.config(foreground=color)
        hotkey = eng.normalize_hotkey(self.config["toggle_hotkey"])
        self.toggle_btn.config(
            text=f"Pause ({hotkey})" if engine.is_active else f"Start ({hotkey})"
        )
        stats = engine.stats
        values = {
            "casts": stats["casts"],
            "caught": stats["caught"],
            "escaped": stats["escaped"],
            "rate": eng.success_text(stats),
            "no_bite": stats["no_bite"],
            "last": stats["last"],
            "runtime": time.strftime("%H:%M:%S", time.gmtime(engine.runtime())),
        }
        for key, value in values.items():
            self.stat_vars[key].set(str(value))

    def update_live(self) -> None:
        config = self.config
        if self.engine.is_active:
            self.live_var.set("Detection is running.")
            return
        if eng.get_roblox_rect(config) is None:
            self.live_var.set("Roblox window not found (or minimized).")
            return
        try:
            red = eng.count_red(self.sct, config)
            escape = eng.count_escape(self.sct, config)
        except Exception:
            self.live_var.set("Could not read the screen.")
            return
        self.live_var.set(
            f"STOP pixels: {red} (need {config['min_red_pixels']})   |   "
            f"Escape pixels: {escape} (need {config['escape_min_pixels']})"
        )

    def on_close(self) -> None:
        self.engine.shutdown()
        try:
            self.sct.close()
        except Exception:
            pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
