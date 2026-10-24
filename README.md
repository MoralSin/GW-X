# GW-X

A window switcher with live preview boxes for **Guild Wars 1** multiboxing.

Each running client gets a small live thumbnail on your screen. Click a thumbnail or press a hotkey to jump to that client. GW-X only shows previews and switches windows. It does **not** send keys or mouse clicks to the clients, and it does not read or change the game.

## Features

- Live thumbnail of every client (uses the Windows desktop compositor, so it costs almost no CPU)
- Click a box or use hotkeys to switch client
- Clients are found by **folder**, so separate installs (each with its own `.dat` and `Gw.exe`) just work
- Boxes snap to each other when you drag them
- Positions, size, opacity and all other settings are saved
- Option to hide the boxes when you alt-tab away from the game
- Active client is highlighted in Toolbox green
- Uses the GWToolbox++ font for the labels and the settings window, if Toolbox is installed
- Settings window with dark and light mode, and a tray icon
- Customisable program icon

## Requirements

- Windows 10 or 11
- Guild Wars clients running in **windowed** or **windowed fullscreen** mode (thumbnails do not work with exclusive fullscreen)

To run from source or build the exe you also need [Python 3](https://www.python.org/downloads/) (tick "Add python.exe to PATH" when installing).

## Quick start

**Using the exe:** download `GW-X.exe` from the Releases page, put it in a folder of its own and run it. It saves its settings in `gw1_preview.json` next to itself.

**Building the exe yourself:** put `GW-X.py`, `build_GW-X_exe.bat` and the icon files in one folder and double-click `build_GW-X_exe.bat`. The finished program is `dist\GW-X.exe`.

**Running from source:**

```
python GW-X.py
```

For the tray icon, install the optional packages: `pip install pystray pillow`. Without them GW-X still works, it just stays on the taskbar instead of the tray.

## Setting up your clients

1. Open the **Clients** tab.
2. Enter the folder of each client (one folder per client, in the order you want them numbered), or start your clients and use the button that detects running ones.
3. Click **Save settings**.

GW-X matches a window to a folder by the location of its `Gw.exe`. Nested folders work too, for example `E:\Guild Wars` and `E:\Guild Wars\Client 2`. The most specific folder wins. If your executable has a different name, change the executable names on the same tab.

The defaults are `E:\Guild Wars` and `E:\Guild Wars\Client 2` to `Client 5`. Change them to match your own setup.

## Controls

| Action | What it does |
| --- | --- |
| Left-click a box | Switch to that client |
| Right-click and drag a box | Move it (snaps to other boxes) |
| Ctrl + mouse wheel over a box | Make all boxes bigger or smaller |
| Middle-click a box | Open the settings window |
| Tray icon, left-click | Open the settings window |
| Tray icon, right-click | Open settings or quit |

### Default hotkeys

All hotkeys can be changed on the **Hotkeys** tab.

| Hotkey | What it does |
| --- | --- |
| `` ` `` | Next client |
| `Shift` + `` ` `` | Previous client |
| `Ctrl` + `Alt` + `1` to `9` | Jump straight to that client |
| `Ctrl` + `Alt` + `S` | Open the settings window |
| `Ctrl` + `Alt` + `Q` | Quit GW-X |

Hotkeys are global, so a key used for GW-X cannot be typed normally in other programs while GW-X runs. If the `` ` `` key is a problem, pick another one.

## Settings

The settings window has four tabs: **Clients**, **Display**, **Hotkeys** and **Layout and saving**. Changes to the look (size, opacity, colours) show up straight away. Press **Save settings** to keep them and to apply hotkey and folder changes. **Revert** goes back to the last saved settings.

Everything is stored in `gw1_preview.json`, next to the program. You can edit it by hand, but the settings window covers all of it. Delete the file to go back to the defaults.

## Changing the icon

Put a `GW-X.ico` (or `GW-X.png`) in the same folder as GW-X. The window, taskbar and tray icons use it the next time the program starts. If you build the exe, put the same file next to `build_GW-X_exe.bat` to use it as the icon of the `.exe` file as well. With no icon file, a built-in green icon is used.

Windows sometimes keeps showing an old exe icon after a rebuild. Renaming the exe or restarting Explorer clears it.

## Troubleshooting

- **A box stays empty or black:** make sure that client is in windowed or windowed fullscreen mode.
- **A client is not found:** check the folder on the Clients tab and that the executable name matches. Each folder row on the Clients tab shows whether the folder and executable were found.
- **A hotkey does nothing:** another program may already use it. Record a different key on the Hotkeys tab.
- **Something went wrong:** look at `GW-X.log` next to the program.

## Notes

GW-X is a small hobby tool and is not affiliated with ArenaNet or GWToolbox++. It does not touch the game or send input to it. Please check the Guild Wars rules on multiboxing for yourself.
