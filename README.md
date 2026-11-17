# ReelSight

A small Windows tool that automates the click-to-cast, click-fast fishing minigame in the Roblox game *Steal an Egg*. It reads your screen for the reel UI and sends mouse clicks. It runs outside Roblox.

> **Not affiliated with Roblox Corporation or the developers of *Steal an Egg*.** "Roblox" is a trademark of Roblox Corporation.

## Read this first

- **It may break a game's rules.** Roblox and individual game developers can ban accounts for automation, and enforcement varies. ReelSight does not inject code, read game memory or use an executor, but that does not guarantee you are safe. Use it at your own risk, ideally on an alt account.
- **It only works with Roblox visible and focused.** It captures the screen and clicks where your cursor is. A minimized or covered window can't be read.
- **It is tuned to one game's UI** (as of November 2026). If the developers change the fishing UI, detection may stop working until the settings are adjusted.

## How it works

1. Holds the mouse button briefly to cast.
2. Watches a small area for the red **STOP** button that appears while a fish is biting.
3. While STOP is visible, clicks rapidly (around 40 clicks per second on the author's PC).
4. After reeling, looks for the red **"It got away..."** text to count escapes.
5. Repeats.

It only touches your screen and mouse. It does not talk to the game or the network.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer
- Roblox in windowed or borderless mode, visible on screen (exclusive fullscreen may block screen capture; untested)

## Setup

```powershell
git clone https://github.com/SeannyBoyyy/reelsight.git
cd reelsight
python -m pip install -r requirements.txt
```

## Usage

1. Open Roblox and equip the fishing rod at the water.
2. Run:

   ```powershell
   python reelsight.py
   ```

3. Click into Roblox and put the cursor over empty water (not over a button).
4. Press **F6** to start. Press **F6** again to pause and **F7** to quit.

The status line shows casts, catches, escapes and the click rate of the last reel.

### Check detection first

```powershell
python reelsight.py test
```

With no clicking, this prints live pixel counts:

- `red pixels` should jump when the STOP button appears.
- `escape text` should jump when "It got away..." appears.

If the numbers never jump, see [Tuning](#tuning).

## Settings

A `config.json` is created on first run. Edit it to change settings.

| Setting | Default | Meaning |
|---|---|---|
| `cast_hold` | `0.15` | Seconds to hold the mouse button when casting. |
| `bite_timeout` | `20` | Seconds to wait for a bite before recasting. |
| `click_interval` | `0.02` | Seconds between clicks while reeling. |
| `reel_timeout` | `25` | Maximum seconds of reeling per fish. |
| `after_catch_delay` | `4` | Seconds to wait after a catch for the popup to clear. |
| `min_red_pixels` | `15` | Red pixels needed to count the STOP button as visible. |
| `scan_step` | `4` | Pixels skipped when scanning. Higher is faster but less precise. |
| `area` | see file | STOP button area, as fractions of the Roblox window. |
| `escape_area` | see file | "It got away..." text area, as fractions of the Roblox window. |
| `escape_min_pixels` | `40` | Pixels needed to count the escape text as visible. |
| `escape_watch` | `3.0` | Seconds to look for the escape text after reeling. |
| `require_focus` | `true` | Only act while Roblox is the active window. |

## Tuning

- **Fish keep escaping.** Check the click rate in the status line. If it is low, lower `click_interval`. If it is high but fish still escape, try raising it to about `0.04`, since some games drop clicks that come too fast.
- **Never detects STOP.** The `area` box is probably on the wrong spot for your layout. Run `python pick_color.py` and hover over the STOP button to read its color and position, then adjust `area` or the red check in `reelsight.py`.
- **Many "no bite" results.** Raise `bite_timeout`, or make sure the cast lands in water.
- **Escape count looks wrong.** The catch count is an estimate: "caught" means no escape text was seen. If escape detection misses, escapes get counted as catches.
- **Different screen sizes.** The detection areas are fractions of the Roblox window, so they scale with window size, but they are only tested on one layout.

## Limitations

- Windows only (uses the Win32 API).
- Not tested on multiple monitors with different scaling.
- Success rate figures are estimates.
- Works only while Roblox is visible and focused.

## Disclaimer

This project is provided as is, with no warranty. You are responsible for following the rules of Roblox and of any game you use it with. The author is not responsible for account actions or any other consequences of using it.

## License

[MIT](LICENSE)
