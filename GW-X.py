"""
GW-X - live thumbnail overlay + quick switching for multiple
Guild Wars 1 clients (Windows 10/11 only).

Run:        python GW-X.py        (or build GW-X.exe with build_GW-X_exe.bat)
No console: pythonw GW-X.py
Debug:      python GW-X.py --list   (prints visible windows with class, title and exe path)

Everything is set in the Settings window (it opens when GW-X starts): client folders,
opacity, box size, hotkeys, saving positions. The values are stored in gw1_preview.json
next to the program, so you never have to edit that file by hand.

Controls
  Left-click a box .............. bring that client to the front
  Right-drag a box .............. move it; it snaps to other boxes (saved per slot)
  Ctrl + mouse wheel over a box . resize all boxes (saved)
  Middle-click a box ............ open the Settings window
  Hotkeys (change them in Settings): next / previous client, Ctrl+Alt+1..9 jump to a
  client, Ctrl+Alt+S settings, Ctrl+Alt+Q quit.

This tool only previews windows and switches focus. It never sends input
to the game clients.
"""
import sys

if sys.platform != "win32":
    sys.exit("GW1 Preview only runs on Windows (it uses the DWM thumbnail API).")

import copy
import ctypes
import glob
import json
import os
import queue
import re
import struct
import threading
import time
import traceback
import tkinter as tk
import tkinter.font as tkfont
from tkinter import colorchooser, filedialog, messagebox, ttk
from ctypes import wintypes


def app_dir():
    """Folder that holds this script, or the .exe once it is built into a program."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(app_dir(), "gw1_preview.json")
LOG_PATH = os.path.join(app_dir(), "GW-X.log")
_last_logged = [None]


def log(msg):
    """Print a message and keep errors in GW-X.log (a built .exe has no console)."""
    print(msg)
    if msg == _last_logged[0]:
        return  # the same error repeating every few milliseconds shouldn't flood the file
    _last_logged[0] = msg
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 200_000:
            return
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + msg.rstrip() + "\n")
    except OSError:
        pass

DEFAULTS = {
    # A window counts as a Guild Wars 1 client if its class is listed here...
    "match_classes": ["ArenaNet_Dlg_Class"],
    # ...or its title is exactly one of these...
    "match_titles": ["Guild Wars"],
    # ...or its title contains one of these (handy if your clients show character names).
    "extra_title_contains": [],
    # Never treat windows of these classes as clients (File Explorer folder named "Guild Wars").
    "exclude_classes": ["CabinetWClass"],
    # Folder route: one folder per client (forward slashes, or doubled backslashes).
    # When this list is not empty it replaces the class/title matching above.
    # Slot order = list order. Windows of any .exe inside these folders count.
    "client_folders": [
        "E:/Guild Wars",
        "E:/Guild Wars/Client 2",
        "E:/Guild Wars/Client 3",
        "E:/Guild Wars/Client 4",
        "E:/Guild Wars/Client 5",
    ],
    # In folder mode only these executables count (case-insensitive). Empty list = any .exe.
    "exe_names": ["gw.exe"],
    # Dragging: boxes snap to each other when closer than this many pixels (0 = off).
    "snap_distance": 16,
    # Pixels left between snapped boxes (0 = flush; each box already has a 2px border).
    "snap_gap": 0,
    # Label font. "auto" uses the same Font.ttf that GWToolbox++ uses (found in
    # %LOCALAPPDATA%\GWToolboxpp). Put a full path here to use another .ttf, or ""
    # to skip font files and use "font_family" instead (also the fallback).
    "font_file": "auto",
    "font_family": "Segoe UI",
    "font_size": 7,
    # Border/label colour of the focused client, as #RRGGBB. The default #00FF00 is the
    # green GWToolbox++ uses for enabled pcons (its default is this green at 40% opacity).
    "active_color": "#00FF00",
    # How see-through the overlay boxes are: 1.0 = solid, 0.5 = half see-through.
    # Lowest allowed is 0.1. A percentage also works (85 means 0.85).
    # Changes to this value in the file apply live, no restart needed.
    "opacity": 1.0,
    "thumb_width": 320,
    "thumb_height": 180,
    "show_labels": True,
    # Hide the boxes while no Guild Wars client is in front (e.g. after Alt+Tab).
    "hide_when_not_focused": False,
    # Remember a box's position as soon as you drop it. If off, use "Save positions now".
    "auto_save_positions": True,
    "show_settings_on_start": True,
    # Dark look for the Settings window (untick for the light look).
    "dark_mode": True,
    # Minimizing the Settings window hides it in the system tray (needs pystray + pillow).
    "minimize_to_tray": True,
    "hotkey_next": "`",
    "hotkey_prev": "shift+`",
    "hotkey_slot_modifiers": "ctrl+alt",
    "hotkey_quit": "ctrl+alt+q",
    "hotkey_settings": "ctrl+alt+s",
    "positions": {},
}

BORDER = 2
LABEL_FONT_FAMILY = "Segoe UI"   # replaced at startup from the config / Toolbox font
FONT_SOURCE = "Segoe UI"         # a sentence telling where the font came from (shown in Settings)
ICON_SOURCE = "the built-in green icon"  # likewise for the program icon
LABEL_FONT_SIZE = 7               # replaced at startup from "font_size"
LABEL_H = 14          # recalculated from the real font height at startup
COLOR_ACTIVE = "#00ff00"   # Toolbox's enabled-pcon green; replaced from "active_color"
ACTIVE_TEXT = "black"      # label text colour on the active box, picked for contrast
COLOR_IDLE = "#2b2b2b"
POLL_MS = 200   # slow loop: finding/closing clients, titles, thumbnail sizes
FAST_MS = 20    # fast loop: hotkeys, focus highlight, show/hide

# ---------------------------------------------------------------- Win32 setup
user32 = ctypes.WinDLL("user32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi")
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32")
gdi32.AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_void_p]
gdi32.AddFontResourceExW.restype = ctypes.c_int
FR_PRIVATE = 0x10  # font is available to this process only and vanishes when it exits

HWND = wintypes.HWND
HTHUMBNAIL = ctypes.c_void_p
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClassNameW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsIconic.argtypes = [HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = HWND
user32.SetForegroundWindow.argtypes = [HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.GetAncestor.argtypes = [HWND, wintypes.UINT]
user32.GetAncestor.restype = HWND
user32.RegisterHotKey.argtypes = [HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = ctypes.c_int
kernel32.GetCurrentThreadId.restype = wintypes.DWORD
dwmapi.DwmSetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long
user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), HWND, wintypes.UINT,
                                wintypes.UINT, wintypes.UINT]
user32.PeekMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM,
                                      wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MessageBoxW.argtypes = [HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
user32.MessageBoxW.restype = ctypes.c_int
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateMutexW.restype = wintypes.HANDLE
kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
kernel32.CreateEventW.restype = wintypes.HANDLE
kernel32.SetEvent.argtypes = [wintypes.HANDLE]
kernel32.SetEvent.restype = wintypes.BOOL
kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
kernel32.WaitForSingleObject.restype = wintypes.DWORD
kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
kernel32.CloseHandle.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

_get_long = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
_set_long = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
_get_long.argtypes = [HWND, ctypes.c_int]
_get_long.restype = ctypes.c_ssize_t
_set_long.argtypes = [HWND, ctypes.c_int, ctypes.c_ssize_t]
_set_long.restype = ctypes.c_ssize_t


class DWM_THUMBNAIL_PROPERTIES(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("rcDestination", wintypes.RECT),
        ("rcSource", wintypes.RECT),
        ("opacity", ctypes.c_ubyte),
        ("fVisible", wintypes.BOOL),
        ("fSourceClientAreaOnly", wintypes.BOOL),
    ]


dwmapi.DwmRegisterThumbnail.argtypes = [HWND, HWND, ctypes.POINTER(HTHUMBNAIL)]
dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long
dwmapi.DwmUnregisterThumbnail.argtypes = [HTHUMBNAIL]
dwmapi.DwmUnregisterThumbnail.restype = ctypes.c_long
dwmapi.DwmUpdateThumbnailProperties.argtypes = [HTHUMBNAIL, ctypes.POINTER(DWM_THUMBNAIL_PROPERTIES)]
dwmapi.DwmUpdateThumbnailProperties.restype = ctypes.c_long
dwmapi.DwmQueryThumbnailSourceSize.argtypes = [HTHUMBNAIL, ctypes.POINTER(wintypes.SIZE)]
dwmapi.DwmQueryThumbnailSourceSize.restype = ctypes.c_long

TNP_RECTDESTINATION = 0x1
TNP_OPACITY = 0x4
TNP_VISIBLE = 0x8
TNP_SOURCECLIENTAREAONLY = 0x10

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_NOACTIVATE = 0x08000000
GA_ROOT = 2
SW_RESTORE = 9
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_NOREPEAT = 0x4000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


# ------------------------------------------------------------ window helpers
def win_title(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, buf, 256)
    return buf.value


def win_class(hwnd):
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def win_pid(hwnd):
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def enum_visible_windows():
    out = []

    def cb(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            out.append(hwnd)
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


def win_area(hwnd):
    r = wintypes.RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return max(0, r.right - r.left) * max(0, r.bottom - r.top)
    return 0


_exe_cache = {}


def pid_exe_path(pid):
    """Full path of the executable behind a process id ('' if unreadable)."""
    if pid in _exe_cache:
        return _exe_cache[pid]
    path = ""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if handle:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            path = buf.value
        kernel32.CloseHandle(handle)
    _exe_cache[pid] = path
    return path


def _norm(path):
    return os.path.normcase(os.path.normpath(path))


def client_folder_list(cfg):
    return [f for f in cfg["client_folders"] if f]


def _find_by_folder(folders, exe_names):
    """One window per process whose .exe sits inside one of the folders."""
    norm_folders = [_norm(f) for f in folders]
    best = {}  # pid -> (area, hwnd, title, folder_index)
    seen = set()
    for hwnd in enum_visible_windows():
        title = win_title(hwnd)
        if not title:
            continue
        pid = win_pid(hwnd)
        seen.add(pid)
        exe = pid_exe_path(pid)
        if not exe:
            continue
        exe = _norm(exe)
        if exe_names and os.path.basename(exe) not in exe_names:
            continue
        matches = [i for i, f in enumerate(norm_folders) if exe.startswith(f + os.sep)]
        if not matches:
            continue
        # Nested folders (Client 2 sits inside Guild Wars): the most specific folder wins.
        idx = max(matches, key=lambda i: len(norm_folders[i]))
        area = win_area(hwnd)
        if pid not in best or area > best[pid][0]:
            best[pid] = (area, hwnd, title, idx)
    for pid in list(_exe_cache):  # forget dead pids so a reused pid is re-checked
        if pid not in seen:
            del _exe_cache[pid]
    return {hwnd: (title, idx) for (_a, hwnd, title, idx) in best.values()}


def find_clients(cfg):
    """Return {hwnd: (title, folder_index or None)}."""
    folders = client_folder_list(cfg)
    if folders:
        return _find_by_folder(folders, {n.lower() for n in cfg["exe_names"] if n})
    found = {}
    for hwnd in enum_visible_windows():
        cls = win_class(hwnd)
        title = win_title(hwnd)
        if cls in cfg["exclude_classes"] or "Guild Wars 2" in title:
            continue
        ok = (
            cls in cfg["match_classes"]
            or title in cfg["match_titles"]
            or any(s and s in title for s in cfg["extra_title_contains"])
        )
        if ok:
            found[hwnd] = (title, None)
    return found


def focus_window(hwnd):
    """Bring a window to the foreground, restoring it if minimized."""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    me = kernel32.GetCurrentThreadId()
    fg_thread = user32.GetWindowThreadProcessId(fg, None) if fg else 0
    attached = False
    if fg_thread and fg_thread != me:
        attached = bool(user32.AttachThreadInput(me, fg_thread, True))
    user32.BringWindowToTop(hwnd)
    user32.SetForegroundWindow(hwnd)
    if attached:
        user32.AttachThreadInput(me, fg_thread, False)


def list_windows():
    for hwnd in enum_visible_windows():
        title = win_title(hwnd)
        if title:
            exe = pid_exe_path(win_pid(hwnd))
            print(f"{hwnd:#012x}  class={win_class(hwnd)!r:38}  title={title!r}  exe={exe!r}")


# -------------------------------------------------------------------- config
def load_config():
    cfg = json.loads(json.dumps(DEFAULTS))
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if not saved.get("client_folders"):
                # An older config file may hold an empty list; keep the built-in folders.
                saved.pop("client_folders", None)
            cfg.update(saved)
        except Exception as e:
            print(f"Could not read {CONFIG_PATH}: {e} (using defaults)")
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Could not save config: {e}")


# --------------------------------------------------------------------- fonts
def ttf_family_name(path):
    """Read the font family name (the one Windows/Tk use) from a .ttf/.otf/.ttc file."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        base = 0
        if data[:4] == b"ttcf":  # font collection: use the first font
            base = struct.unpack(">I", data[12:16])[0]
        num_tables = struct.unpack(">H", data[base + 4:base + 6])[0]
        table = None
        for i in range(num_tables):
            rec = base + 12 + 16 * i
            tag, _checksum, offset, _length = struct.unpack(">4sIII", data[rec:rec + 16])
            if tag == b"name":
                table = offset
                break
        if table is None:
            return None
        _fmt, count, str_off = struct.unpack(">HHH", data[table:table + 6])
        names = []  # (priority, text); name ID 1 is the legacy family name GDI matches on
        for i in range(count):
            rec = table + 6 + 12 * i
            pid, _eid, lid, nid, length, off = struct.unpack(">6H", data[rec:rec + 12])
            if nid != 1:
                continue
            raw = data[table + str_off + off: table + str_off + off + length]
            if pid in (0, 3):
                text, prio = raw.decode("utf-16-be", "ignore"), (0 if lid == 0x409 else 1)
            elif pid == 1:
                text, prio = raw.decode("mac_roman", "ignore"), 2
            else:
                continue
            if text.strip():
                names.append((prio, text.strip()))
        return min(names)[1] if names else None
    except Exception:
        return None


def find_toolbox_font():
    """Locate the Font.ttf that GWToolbox++ uses."""
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~/AppData/Local")
    root = os.path.join(base, "GWToolboxpp")
    candidates = [os.path.join(root, "Font.ttf")]
    candidates += sorted(glob.glob(os.path.join(root, "*", "Font.ttf")))
    return next((c for c in candidates if os.path.isfile(c)), None)


def load_label_font(cfg):
    """Return the family to use for the box labels, loading a font file if configured.

    Must run before the Tk root exists, so Tk sees the newly added font.
    """
    global FONT_SOURCE
    fallback = cfg.get("font_family") or "Segoe UI"
    spec = cfg.get("font_file", "auto")
    FONT_SOURCE = fallback
    if not spec:
        FONT_SOURCE = f"{fallback} (font files are switched off)"
        return fallback
    path = find_toolbox_font() if spec == "auto" else spec
    if not path or not os.path.isfile(path):
        what = "Toolbox's Font.ttf" if spec == "auto" else f"font file {spec!r}"
        print(f"Could not find {what}; using {fallback} instead.")
        FONT_SOURCE = f"{fallback} ({what} was not found)"
        return fallback
    if gdi32.AddFontResourceExW(path, FR_PRIVATE, None) == 0:
        print(f"Windows refused to load {path}; using {fallback} instead.")
        FONT_SOURCE = f"{fallback} (Windows refused the font file)"
        return fallback
    family = ttf_family_name(path)
    if not family:
        print(f"Could not read the font name from {path}; using {fallback} instead.")
        FONT_SOURCE = f"{fallback} (the font file's name couldn't be read)"
        return fallback
    FONT_SOURCE = f"{family} (the same font file as GWToolbox)" if spec == "auto" \
        else f"{family} (from {os.path.basename(path)})"
    return family


# ------------------------------------------------------------------- hotkeys
MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
NAMED_KEYS = {
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "tab": 0x09, "space": 0x20, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "insert": 0x2D, "delete": 0x2E,
    "enter": 0x0D, "backspace": 0x08,
    # Punctuation keys are named after their US-layout character. The physical key may type
    # something else on other layouts (the key left of "1" is VK_OEM_3, "`" on a US keyboard).
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC,
    ";": 0xBA, "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for _n in range(10):
    NAMED_KEYS[f"num{_n}"] = 0x60 + _n  # number pad
NAMED_ALIASES = {"grave": 0xC0, "backtick": 0xC0, "tilde": 0xC0}
VK_NAMES = {vk: name for name, vk in NAMED_KEYS.items()}


def vk_to_name(vk):
    """Name used in the settings file for a Windows virtual-key code (None if unsupported)."""
    if vk in VK_NAMES:
        return VK_NAMES[vk]
    if 0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A:
        return chr(vk).lower()
    if 0x70 <= vk <= 0x87:
        return f"f{vk - 0x6F}"
    return None


def parse_hotkey(text):
    mods, vk = 0, None
    for part in (p.strip().lower() for p in text.split("+")):
        if not part:
            continue
        if part in MODS:
            mods |= MODS[part]
        elif part in NAMED_KEYS:
            vk = NAMED_KEYS[part]
        elif part in NAMED_ALIASES:
            vk = NAMED_ALIASES[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x70 + int(part[1:]) - 1
        else:
            raise ValueError(f"unknown key '{part}' in hotkey '{text}'")
    if vk is None:
        raise ValueError(f"no key in hotkey '{text}'")
    return mods, vk


def hotkey_thread(specs, cmds, state):
    """RegisterHotKey must run in the same thread that pumps messages.

    The thread ends when WM_QUIT is posted to it (see App.stop_hotkeys), which lets the
    hotkeys be switched off while you record a new one and re-registered afterwards.
    """
    actions = {}
    msg = wintypes.MSG()
    try:
        state["tid"] = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)  # creates this thread's queue
        failed = []
        for i, (mods, vk, action, label) in enumerate(specs, start=1):
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                actions[i] = action
            else:
                failed.append(label)
        if failed:
            cmds.put(("hkfail", failed))
    finally:
        state["ready"].set()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        if msg.message == WM_HOTKEY and msg.wParam in actions:
            cmds.put(actions[msg.wParam])
    for i in actions:
        user32.UnregisterHotKey(None, i)


# ------------------------------------------------------------------- preview
def get_opacity(cfg):
    """Opacity from the config as a float between 0.1 and 1.0 (85 is read as 0.85)."""
    try:
        v = float(cfg.get("opacity", 1.0))
    except (TypeError, ValueError):
        return 1.0
    if v > 1:
        v /= 100.0
    return max(0.1, min(1.0, v))


class Preview:
    def __init__(self, app, hwnd, slot):
        self.app = app
        self.hwnd = hwnd
        self.slot = slot
        self.thumb = None
        self.title = None
        self.active = None
        self.visible = True
        self._last_key = None
        self.x = self.y = 0  # where the box is on screen (kept here so it works while hidden)

        cfg = app.cfg
        self.win = tk.Toplevel(app.root)
        self.win.title("GW-X")
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=COLOR_IDLE)
        self.apply_opacity()
        self.label = tk.Label(self.win, bg=COLOR_IDLE, fg="white", anchor="w",
                              font=(LABEL_FONT_FAMILY, LABEL_FONT_SIZE), padx=4)

        w, h = self._size()
        pos = cfg["positions"].get(str(slot))
        if pos:
            x, y = int(pos[0]), int(pos[1])
        else:
            x, y = 20, 20 + (slot - 1) * (h + 10)
        self.place_window(x, y)
        self.win.update_idletasks()

        self.dest = user32.GetAncestor(self.win.winfo_id(), GA_ROOT) or self.win.winfo_id()
        # Windows only decides whether a window gets a taskbar button when it is shown, so
        # hide it, change the style, then show it again. Otherwise every box got its own button.
        self.win.withdraw()
        ex = _get_long(self.dest, GWL_EXSTYLE)
        _set_long(self.dest, GWL_EXSTYLE, (ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW)
        self.win.deiconify()
        self.win.attributes("-topmost", True)

        handle = HTHUMBNAIL()
        hr = dwmapi.DwmRegisterThumbnail(self.dest, self.hwnd, ctypes.byref(handle))
        if hr == 0:
            self.thumb = handle
        else:
            print(f"DwmRegisterThumbnail failed for window {hwnd:#x} (HRESULT {hr & 0xFFFFFFFF:#x})")

        for widget in (self.win, self.label):
            widget.bind("<Button-1>", lambda e: self.app.focus(self.hwnd))
            widget.bind("<Button-2>", lambda e: self.app.open_settings())
            widget.bind("<ButtonPress-3>", self._drag_start)
            widget.bind("<B3-Motion>", self._drag_move)
            widget.bind("<ButtonRelease-3>", self._drag_end)
            widget.bind("<Control-MouseWheel>", self._wheel)

    def _size(self):
        cfg = self.app.cfg
        label_h = LABEL_H if cfg["show_labels"] else 0
        return (cfg["thumb_width"] + 2 * BORDER,
                cfg["thumb_height"] + label_h + 2 * BORDER)

    def apply_opacity(self):
        try:
            self.win.attributes("-alpha", get_opacity(self.app.cfg))
        except tk.TclError:
            pass

    def place_window(self, x=None, y=None):
        cfg = self.app.cfg
        w, h = self._size()
        if x is not None:
            self.x, self.y = x, y
        self.win.geometry(f"{w}x{h}+{self.x}+{self.y}")
        if cfg["show_labels"]:
            self.label.place(x=BORDER, y=BORDER, width=cfg["thumb_width"], height=LABEL_H)
        else:
            self.label.place_forget()
        self._last_key = None  # force a thumbnail update

    # -- appearance
    def set_title(self, title):
        if title != self.title:
            self.title = title
            self.label.configure(text=f"{self.slot}  {title}")

    def set_active(self, active):
        if active != self.active:
            self.active = active
            color = COLOR_ACTIVE if active else COLOR_IDLE
            self.win.configure(bg=color)
            self.label.configure(bg=color, fg=ACTIVE_TEXT if active else "white")

    def set_visible(self, visible):
        """Show or hide the box. Returns True if its visibility actually changed."""
        if visible == self.visible:
            return False
        self.visible = visible
        if visible:
            self.win.deiconify()
            self.win.attributes("-topmost", True)
            self._last_key = None
        else:
            self.win.withdraw()
        return True

    def update_thumb(self):
        if not self.thumb or not self.visible:
            return
        cfg = self.app.cfg
        size = wintypes.SIZE()
        if dwmapi.DwmQueryThumbnailSourceSize(self.thumb, ctypes.byref(size)) != 0 \
                or size.cx <= 0 or size.cy <= 0:
            sw, sh = 16, 9
        else:
            sw, sh = size.cx, size.cy
        tw, th = cfg["thumb_width"], cfg["thumb_height"]
        label_h = LABEL_H if cfg["show_labels"] else 0
        key = (sw, sh, tw, th, label_h)
        if key == self._last_key:
            return
        self._last_key = key

        scale = min(tw / sw, th / sh)
        w, h = max(1, int(sw * scale)), max(1, int(sh * scale))
        x = BORDER + (tw - w) // 2
        y = BORDER + label_h + (th - h) // 2

        props = DWM_THUMBNAIL_PROPERTIES()
        props.dwFlags = TNP_RECTDESTINATION | TNP_VISIBLE | TNP_OPACITY | TNP_SOURCECLIENTAREAONLY
        props.rcDestination = wintypes.RECT(x, y, x + w, y + h)
        props.opacity = 255
        props.fVisible = True
        props.fSourceClientAreaOnly = True
        dwmapi.DwmUpdateThumbnailProperties(self.thumb, ctypes.byref(props))

    # -- mouse handlers
    def _drag_start(self, e):
        self._off = (e.x_root - self.x, e.y_root - self.y)

    def _drag_move(self, e):
        self.x, self.y = self.app.snap(self, e.x_root - self._off[0], e.y_root - self._off[1])
        self.win.geometry(f"+{self.x}+{self.y}")

    def _drag_end(self, _e):
        self.app.cfg["positions"][str(self.slot)] = [self.x, self.y]
        if self.app.cfg.get("auto_save_positions", True):
            self.app.persist("positions")

    def _wheel(self, e):
        self.app.resize(1.1 if e.delta > 0 else 1 / 1.1)
        return "break"

    def destroy(self):
        if self.thumb:
            dwmapi.DwmUnregisterThumbnail(self.thumb)
            self.thumb = None
        try:
            self.win.destroy()
        except tk.TclError:
            pass


# ----------------------------------------------------------------- settings window
# Every setting the window edits. Everything else in the .json stays as it is.
SETTINGS_KEYS = [
    "client_folders", "exe_names",
    "opacity", "thumb_width", "thumb_height", "show_labels", "hide_when_not_focused",
    "font_size", "active_color",
    "snap_distance", "snap_gap", "auto_save_positions", "show_settings_on_start", "dark_mode",
    "minimize_to_tray",
    "hotkey_next", "hotkey_prev", "hotkey_slot_modifiers", "hotkey_quit", "hotkey_settings",
]
HOTKEY_KEYS = {"hotkey_next", "hotkey_prev", "hotkey_slot_modifiers", "hotkey_quit",
               "hotkey_settings"}
MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}


def normalize_settings(d):
    """Bring values to one canonical form so 'did anything change?' compares cleanly."""
    out = {}
    for k in SETTINGS_KEYS:
        if k not in d:
            continue
        v = d[k]
        if k == "client_folders":
            v = [str(f).strip().strip('"').replace("\\", "/") for f in v if str(f).strip()]
        elif k == "exe_names":
            v = [str(n).strip() for n in v if str(n).strip()]
        elif k == "opacity":
            v = round(get_opacity({"opacity": v}), 2)
        elif k in ("thumb_width", "thumb_height", "snap_distance", "snap_gap", "font_size"):
            v = int(round(float(v)))
        elif k in ("show_labels", "hide_when_not_focused", "auto_save_positions",
                   "show_settings_on_start", "dark_mode", "minimize_to_tray"):
            v = bool(v)
        elif k in HOTKEY_KEYS:
            v = str(v or "").strip().lower()
        elif k == "active_color":
            v = str(v).strip().lower()
        out[k] = v
    return out


def pretty_hotkey(text):
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
    parts = [p for p in text.split("+") if p]
    return "+".join(names.get(p, p.upper() if len(p) == 1 else p.capitalize()) for p in parts)


def held_modifiers():
    """Modifier keys physically held down right now, in a fixed order."""
    def down(vk):
        return bool(user32.GetAsyncKeyState(vk) & 0x8000)
    parts = []
    if down(0x11):
        parts.append("ctrl")
    if down(0x12):
        parts.append("alt")
    if down(0x10):
        parts.append("shift")
    if down(0x5B) or down(0x5C):
        parts.append("win")
    return parts


class HotkeyField(ttk.Frame):
    """A box you click, then press the key combination you want."""

    def __init__(self, parent, app, on_change):
        super().__init__(parent, style="Card.TFrame")
        self.app, self.on_change = app, on_change
        self.value = ""
        self.recording = False
        self._previous = ""
        self.var = tk.StringVar()
        self.entry = ttk.Entry(self, textvariable=self.var, width=26, state="readonly",
                               justify="center")
        self.entry.pack(side="left")
        ttk.Button(self, text="Clear", width=6, style="Small.TButton", command=self.clear).pack(side="left", padx=(6, 0))
        self.entry.bind("<Button-1>", self._start)
        self.entry.bind("<KeyPress>", self._key)
        self.entry.bind("<FocusOut>", self._cancel)

    def set(self, value):
        self._finish()
        self.value = (value or "").strip().lower()
        self.var.set(pretty_hotkey(self.value) if self.value else "(not set)")

    def clear(self):
        self.set("")
        self.on_change()

    def _start(self, _e=None):
        if not self.recording:
            self.recording = True
            self._previous = self.value
            self.var.set("Press the keys...")
            self.app.pause_hotkeys()  # otherwise Windows would swallow the key you press
        self.entry.focus_set()
        return "break"

    def _finish(self):
        if self.recording:
            self.recording = False
            self.app.resume_hotkeys()

    def _cancel(self, _e=None):
        if self.recording:
            self._finish()
            self.set(self._previous)

    def _key(self, e):
        if not self.recording:
            return None
        vk = e.keycode  # on Windows Tk reports the virtual-key code here
        if vk in MODIFIER_VKS:
            return "break"
        if vk == 0x1B:  # Esc cancels
            self._cancel()
            self.winfo_toplevel().focus_set()
            return "break"
        name = vk_to_name(vk)
        if not name:
            self.var.set("Not supported, try another key")
            return "break"
        combo = "+".join(held_modifiers() + [name])
        self.set(combo)
        self.on_change()
        self.winfo_toplevel().focus_set()
        return "break"


UI_FONT = "Segoe UI"

# Two colour sets for the Settings window. The green is GWToolbox's pcon green (#00FF00,
# its pressed shade is the (0, 200, 0) Toolbox uses for its tick icon), so text on it is dark.
PALETTES = {
    "dark": dict(
        bg="#1a212c", card="#242d3b", input="#2e3a4d", border="#36435a", hover="#3a4960",
        text="#eef2f7", muted="#93a0b5",
        accent="#00ff00", accent_hover="#52ff52", accent_dark="#00c800", on_accent="#04120a",
        ok="#00ff00", bad="#ff6b6b", warn="#f2b84b"),
    "light": dict(
        bg="#e8ecf2", card="#ffffff", input="#f1f4f8", border="#cdd5e0", hover="#dde4ee",
        text="#1b2430", muted="#5d6b7e",
        accent="#00ff00", accent_hover="#52ff52", accent_dark="#00c800", on_accent="#04120a",
        ok="#177a2d", bad="#c0392b", warn="#a86200"),
}


def set_dark_titlebar(toplevel, dark):
    """Ask Windows 10/11 for a dark title bar (Tk can't colour the title bar itself)."""
    try:
        toplevel.update_idletasks()
        hwnd = user32.GetAncestor(toplevel.winfo_id(), GA_ROOT) or toplevel.winfo_id()
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # 20: Windows 10 20H1 and newer, 19: older Windows 10
            if dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                            ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


class SettingsWindow:
    def __init__(self, app):
        self.app = app
        self._loading = False
        self._busy = False
        self.folder_vars = []
        self.row_status = []
        self.color = COLOR_ACTIVE
        self.hk = {}
        self.mod_vars = {}
        self.tabs = []
        self._paint = []
        self._sel = 0
        self._status_kind = "muted"
        self._dark_applied = None
        self.dark_var = tk.BooleanVar(value=bool(app.cfg.get("dark_mode", True)))
        self.pal = PALETTES["dark" if self.dark_var.get() else "light"]

        self.win = tk.Toplevel(app.root)
        self.win.title("GW-X")
        self.win.minsize(740, 700)
        self.win.protocol("WM_DELETE_WINDOW", self.on_close)
        self.win.bind("<Unmap>", self._on_unmap)
        self.style = ttk.Style(self.win)

        # Bottom bar (packed before the tabs so it always keeps its space)
        bottom = self.paint(tk.Frame(self.win), bg="bg")
        bottom.pack(side="bottom", fill="x")
        self.paint(tk.Frame(self.win, height=1), bg="border").pack(side="bottom", fill="x")
        inner = self.paint(tk.Frame(bottom), bg="bg")
        inner.pack(fill="x", padx=16, pady=12)
        self.status = self.paint(tk.Label(inner, text="", wraplength=290, justify="left",
                                          anchor="w", font=(UI_FONT, 9)), bg="bg")
        self.status.pack(side="left")
        ttk.Button(inner, text="Save settings", style="Accent.TButton", command=self.save
                   ).pack(side="right")
        ttk.Button(inner, text="Revert", command=self.revert).pack(side="right", padx=8)
        ttk.Button(inner, text="Quit GW-X", command=self.on_quit).pack(side="right")
        ttk.Checkbutton(inner, text="Dark mode", variable=self.dark_var, command=self.on_dark_toggle
                        ).pack(side="right", padx=(0, 20))

        # Tab bar and pages
        self.tab_bar = self.paint(tk.Frame(self.win), bg="bg")
        self.tab_bar.pack(side="top", fill="x", pady=(8, 0))
        self.paint(tk.Frame(self.win, height=1), bg="border").pack(side="top", fill="x")
        self.pages = self.paint(tk.Frame(self.win), bg="bg")
        self.pages.pack(side="top", fill="both", expand=True)
        self._build_clients()
        self._build_display()
        self._build_hotkeys()
        self._build_layout()
        self.select_tab(0)

        self.load(app.cfg)
        w, h = 800, 780
        x = max(0, (self.win.winfo_screenwidth() - w) // 2)
        y = max(0, (self.win.winfo_screenheight() - h) // 3)
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # ----------------------------------------------------------------- look
    def paint(self, widget, **opts):
        """Colour a plain tk widget from the palette and remember it for theme changes."""
        self._paint.append((widget, opts))
        self._apply_paint(widget, opts)
        return widget

    def _apply_paint(self, widget, opts):
        try:
            widget.configure(**{k: self.pal[v] for k, v in opts.items()})
        except tk.TclError:
            pass

    def on_dark_toggle(self):
        self.apply_theme()
        self.push_live()

    def apply_theme(self):
        dark = bool(self.dark_var.get())
        self._dark_applied = dark
        self.pal = p = PALETTES["dark" if dark else "light"]
        st = self.style
        try:
            st.theme_use("clam")  # the only built-in theme that accepts custom colours
        except tk.TclError:
            pass
        st.configure(".", font=(UI_FONT, 9), background=p["bg"], foreground=p["text"],
                     troughcolor=p["input"], bordercolor=p["border"], lightcolor=p["border"],
                     darkcolor=p["border"], focuscolor=p["card"], fieldbackground=p["input"],
                     insertcolor=p["text"], selectbackground=p["accent"],
                     selectforeground=p["on_accent"])
        st.configure("TFrame", background=p["bg"])
        st.configure("Card.TFrame", background=p["card"])
        st.configure("Card.TLabel", background=p["card"], foreground=p["text"])
        st.configure("Muted.TLabel", background=p["card"], foreground=p["muted"],
                     font=(UI_FONT, 8))
        st.configure("Card.TSeparator", background=p["border"])
        st.configure("TCheckbutton", background=p["bg"], foreground=p["text"],
                     focuscolor=p["bg"], indicatorbackground=p["input"],
                     indicatorforeground=p["on_accent"], bordercolor=p["border"],
                     lightcolor=p["input"], darkcolor=p["input"])
        st.map("TCheckbutton", background=[("active", p["bg"])],
               indicatorbackground=[("selected", p["accent"]), ("pressed", p["border"])])
        st.configure("Card.TCheckbutton", background=p["card"], foreground=p["text"],
                     focuscolor=p["card"], indicatorbackground=p["input"],
                     indicatorforeground=p["on_accent"], bordercolor=p["border"],
                     lightcolor=p["input"], darkcolor=p["input"])
        st.map("Card.TCheckbutton", background=[("active", p["card"])],
               indicatorbackground=[("selected", p["accent"]), ("pressed", p["border"])],
               foreground=[("disabled", p["muted"])])
        st.configure("TButton", background=p["input"], foreground=p["text"],
                     bordercolor=p["border"], lightcolor=p["input"], darkcolor=p["input"],
                     focuscolor=p["input"], padding=(12, 6), relief="flat")
        st.map("TButton", background=[("pressed", p["border"]), ("active", p["hover"])],
               foreground=[("disabled", p["muted"])])
        st.configure("Small.TButton", padding=(8, 3))
        st.configure("Accent.TButton", background=p["accent"], foreground=p["on_accent"],
                     bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"],
                     focuscolor=p["accent"], padding=(18, 6), font=(UI_FONT, 9, "bold"))
        st.map("Accent.TButton",
               background=[("pressed", p["accent_dark"]), ("active", p["accent_hover"])])
        st.configure("TEntry", fieldbackground=p["input"], foreground=p["text"],
                     bordercolor=p["border"], lightcolor=p["border"], darkcolor=p["border"],
                     insertcolor=p["text"], padding=4)
        st.map("TEntry", fieldbackground=[("readonly", p["input"]), ("disabled", p["card"])],
               bordercolor=[("focus", p["accent"])], lightcolor=[("focus", p["accent"])],
               darkcolor=[("focus", p["accent"])])
        st.configure("TSpinbox", fieldbackground=p["input"], foreground=p["text"],
                     background=p["input"], arrowcolor=p["text"], bordercolor=p["border"],
                     lightcolor=p["border"], darkcolor=p["border"], insertcolor=p["text"],
                     padding=3)
        st.map("TSpinbox", bordercolor=[("focus", p["accent"])],
               background=[("active", p["hover"])])
        st.configure("Card.Horizontal.TScale", background=p["accent"], troughcolor=p["input"],
                     bordercolor=p["border"], lightcolor=p["accent"], darkcolor=p["accent"])
        st.map("Card.Horizontal.TScale", background=[("active", p["accent_hover"])])
        self.win.configure(bg=p["bg"])
        for widget, opts in self._paint:
            self._apply_paint(widget, opts)
        self._recolor_tabs()
        self.refresh_row_status()
        self._paint_color()
        self._apply_status()
        set_dark_titlebar(self.win, dark)

    # ------------------------------------------------------------- tabs, cards
    def add_tab(self, text):
        page = self.paint(tk.Frame(self.pages), bg="bg")
        cell = self.paint(tk.Frame(self.tab_bar), bg="bg")
        cell.pack(side="left", padx=(10, 0))
        label = tk.Label(cell, text=text.upper(), font=(UI_FONT, 8, "bold"), padx=14, pady=10,
                         cursor="hand2")
        label.pack()
        under = tk.Frame(cell, height=3)
        under.pack(fill="x")
        index = len(self.tabs)
        for widget in (cell, label, under):
            widget.bind("<Button-1>", lambda _e, i=index: self.select_tab(i))
        self.tabs.append((label, under, page))
        return page

    def select_tab(self, index):
        self._sel = index
        for j, (_label, _under, page) in enumerate(self.tabs):
            if j == index:
                page.pack(fill="both", expand=True, padx=18, pady=16)
            else:
                page.pack_forget()
        self._recolor_tabs()

    def _recolor_tabs(self):
        p = self.pal
        for j, (label, under, _page) in enumerate(self.tabs):
            on = j == self._sel
            label.configure(bg=p["bg"], fg=p["text"] if on else p["muted"])
            under.configure(bg=p["accent"] if on else p["bg"])

    def card(self, parent, title, hint=None):
        """A panel with a green marker and a heading. Returns the frame to put things in."""
        outer = self.paint(tk.Frame(parent, highlightthickness=1), bg="card",
                           highlightbackground="border")
        outer.pack(fill="x", pady=(0, 14))
        head = self.paint(tk.Frame(outer), bg="card")
        head.pack(fill="x", padx=16, pady=(12, 0))
        self.paint(tk.Frame(head, width=4, height=16), bg="accent").pack(side="left", padx=(0, 9))
        self.paint(tk.Label(head, text=title, anchor="w", font=(UI_FONT, 11, "bold")),
                   bg="card", fg="text").pack(side="left")
        if hint:
            self.paint(tk.Label(outer, text=hint, anchor="w", justify="left", wraplength=700,
                                font=(UI_FONT, 8)), bg="card", fg="muted"
                       ).pack(fill="x", padx=16, pady=(4, 0))
        body = ttk.Frame(outer, style="Card.TFrame", padding=(16, 10, 16, 14))
        body.pack(fill="x")
        return body

    def _note(self, parent, text, pady=(4, 0)):
        label = ttk.Label(parent, text=text, style="Muted.TLabel", wraplength=700, justify="left")
        label.pack(anchor="w", pady=pady)
        return label

    def _slider(self, parent, row, text, var, lo, hi, unit, on_change):
        ttk.Label(parent, text=text, style="Card.TLabel").grid(row=row, column=0, sticky="w",
                                                                pady=7)
        ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", length=330,
                  style="Card.Horizontal.TScale", command=lambda _v: on_change()
                  ).grid(row=row, column=1, sticky="w", padx=14)
        out = ttk.Label(parent, width=10, style="Card.TLabel")
        out.grid(row=row, column=2, sticky="w")

        def update(*_):
            out.configure(text=f"{int(round(var.get()))} {unit}")
        var.trace_add("write", update)
        update()

    # ---------------------------------------------------------------- the tabs
    def _build_clients(self):
        page = self.add_tab("Clients")
        c = self.card(page, "Client folders",
                      "One folder per Guild Wars client, in slot order. The first row is slot 1 "
                      "(Ctrl+Alt+1), the second is slot 2, and so on.")
        self.rows_frame = ttk.Frame(c, style="Card.TFrame")
        self.rows_frame.pack(fill="x", pady=(2, 4))
        bar = ttk.Frame(c, style="Card.TFrame")
        bar.pack(fill="x", pady=(4, 0))
        ttk.Button(bar, text="Add client", style="Small.TButton", command=self.add_row
                   ).pack(side="left")
        ttk.Button(bar, text="Detect running clients", style="Small.TButton",
                   command=self.detect).pack(side="left", padx=8)

        c2 = self.card(page, "Client program",
                       "Only windows from these programs inside your folders get a box.")
        line = ttk.Frame(c2, style="Card.TFrame")
        line.pack(fill="x")
        ttk.Label(line, text="Program name(s)", style="Card.TLabel").pack(side="left")
        self.exe_var = tk.StringVar()
        entry = ttk.Entry(line, textvariable=self.exe_var, width=26)
        entry.pack(side="left", padx=12)
        entry.bind("<KeyRelease>", lambda _e: self.refresh_row_status())
        self._note(c2, "Separate several names with commas. Leave it empty to accept any program. "
                       "A client folder inside another client's folder (Client 2 inside "
                       "Guild Wars) is fine: the deepest matching folder wins.", pady=(8, 0))

    def _build_display(self):
        page = self.add_tab("Display")
        c = self.card(page, "Boxes", "Changes here show up straight away; Save settings keeps them.")
        grid = ttk.Frame(c, style="Card.TFrame")
        grid.pack(fill="x")
        self.opacity_var = tk.DoubleVar(value=100)
        self.w_var = tk.DoubleVar(value=320)
        self.h_var = tk.DoubleVar(value=180)
        self.ratio_var = tk.BooleanVar(value=True)
        self._slider(grid, 0, "Opacity", self.opacity_var, 10, 100, "%", self.on_opacity)
        self._slider(grid, 1, "Box width", self.w_var, 120, 800, "px", self.on_width)
        self._slider(grid, 2, "Box height", self.h_var, 68, 450, "px", self.on_height)
        ttk.Checkbutton(grid, text="Keep width and height in 16:9", variable=self.ratio_var,
                        style="Card.TCheckbutton").grid(row=3, column=1, sticky="w", padx=14,
                                                        pady=(2, 0))

        c = self.card(page, "Behaviour")
        self.hide_var = tk.BooleanVar()
        self.labels_var = tk.BooleanVar()
        ttk.Checkbutton(c, text="Hide the boxes when no Guild Wars client is in front "
                                "(for example when you Alt+Tab to another program)",
                        variable=self.hide_var, command=self.push_live,
                        style="Card.TCheckbutton").pack(anchor="w", pady=3)
        self._note(c, "They come back as soon as you switch to one of your clients again.",
                   pady=(0, 6))
        ttk.Checkbutton(c, text="Show the client name on top of each box",
                        variable=self.labels_var, command=self.push_live,
                        style="Card.TCheckbutton").pack(anchor="w", pady=3)

        c = self.card(page, "Labels and colour")
        self._note(c, f"Font: {FONT_SOURCE}", pady=(0, 8))
        line = ttk.Frame(c, style="Card.TFrame")
        line.pack(fill="x")
        ttk.Label(line, text="Label text size", style="Card.TLabel").pack(side="left")
        self.font_var = tk.IntVar(value=7)
        spin = ttk.Spinbox(line, from_=5, to=16, width=5, textvariable=self.font_var,
                           command=self.push_live)
        spin.pack(side="left", padx=12)
        spin.bind("<Return>", lambda _e: self.push_live())
        spin.bind("<FocusOut>", lambda _e: self.push_live())
        line2 = ttk.Frame(c, style="Card.TFrame")
        line2.pack(fill="x", pady=(10, 0))
        ttk.Label(line2, text="Colour of the client in front", style="Card.TLabel"
                  ).pack(side="left")
        self.color_btn = tk.Button(line2, width=10, relief="flat", bd=0, cursor="hand2",
                                   command=self.pick_color)
        self.color_btn.pack(side="left", padx=12)
        ttk.Button(line2, text="Toolbox green", style="Small.TButton",
                   command=lambda: self.set_color("#00ff00")).pack(side="left")

    def _build_hotkeys(self):
        page = self.add_tab("Hotkeys")
        c = self.card(page, "Hotkeys",
                      "Click a box, then press the keys you want. Esc cancels. "
                      "Press Save settings to apply.")
        grid = ttk.Frame(c, style="Card.TFrame")
        grid.pack(fill="x")
        rows = [("hotkey_next", "Next client"), ("hotkey_prev", "Previous client"),
                ("hotkey_settings", "Open this window"), ("hotkey_quit", "Quit GW-X")]
        for r, (key, label) in enumerate(rows):
            ttk.Label(grid, text=label, width=18, style="Card.TLabel").grid(
                row=r, column=0, sticky="w", pady=6)
            field = HotkeyField(grid, self.app, self.on_hotkey_change)
            field.grid(row=r, column=1, sticky="w")
            self.hk[key] = field

        c = self.card(page, "Jump straight to a client",
                      "Hold these keys and press a number from 1 to 9.")
        mods = ttk.Frame(c, style="Card.TFrame")
        mods.pack(anchor="w", pady=2)
        for name, text in (("ctrl", "Ctrl"), ("alt", "Alt"), ("shift", "Shift"), ("win", "Win")):
            var = tk.BooleanVar()
            self.mod_vars[name] = var
            ttk.Checkbutton(mods, text=text, variable=var, command=self.on_hotkey_change,
                            style="Card.TCheckbutton").pack(side="left", padx=(0, 16))
        ttk.Label(mods, text="+  1 ... 9", style="Card.TLabel").pack(side="left")
        self._note(c, "Untick all four to turn the number hotkeys off.", pady=(6, 0))
        self._note(c, "Hotkeys work everywhere in Windows while GW-X runs, so a key used here "
                      "can't be typed normally in other programs (for example the ` key).",
                   pady=(6, 0))

        c = self.card(page, "Mouse controls",
                      "What you can do with the preview boxes on your screen.")
        grid = ttk.Frame(c, style="Card.TFrame")
        grid.pack(fill="x")
        controls = [
            ("Left-click a box", "Switch to that client."),
            ("Right-click and drag", "Move the box. It snaps to the other boxes when it gets close."),
            ("Ctrl + mouse wheel", "Make all the boxes bigger or smaller (over any box)."),
            ("Middle-click a box", "Open this settings window."),
            ("Tray icon", "Left-click opens this window. Right-click for Quit."),
            ("Minimize this window", "It hides in the tray, if that is ticked on the Layout tab."),
        ]
        for r, (how, what) in enumerate(controls):
            ttk.Label(grid, text=how, width=22, style="Card.TLabel").grid(
                row=r, column=0, sticky="nw", pady=4)
            ttk.Label(grid, text=what, style="Muted.TLabel", wraplength=480,
                      justify="left").grid(row=r, column=1, sticky="w", pady=4)

    def _build_layout(self):
        page = self.add_tab("Layout and saving")
        c = self.card(page, "Box positions")
        self.autosave_var = tk.BooleanVar()
        ttk.Checkbutton(c, text="Remember a box's position automatically when I move it",
                        variable=self.autosave_var, command=self.push_live,
                        style="Card.TCheckbutton").pack(anchor="w")
        self._note(c, "Drag a box with the right mouse button to move it. Ctrl + mouse wheel over "
                      "a box resizes all of them. Positions are remembered per slot.",
                   pady=(4, 8))
        line = ttk.Frame(c, style="Card.TFrame")
        line.pack(anchor="w")
        ttk.Button(line, text="Save positions now", style="Small.TButton",
                   command=self.save_positions).pack(side="left")
        ttk.Button(line, text="Reset positions", style="Small.TButton",
                   command=self.reset_positions).pack(side="left", padx=8)

        c = self.card(page, "Snapping")
        grid = ttk.Frame(c, style="Card.TFrame")
        grid.pack(anchor="w")
        self.snapd_var = tk.IntVar(value=16)
        self.snapg_var = tk.IntVar(value=0)
        for r, (text, var, hi) in enumerate((
                ("Snap to other boxes within", self.snapd_var, 60),
                ("Gap between snapped boxes", self.snapg_var, 40))):
            ttk.Label(grid, text=text, style="Card.TLabel").grid(row=r, column=0, sticky="w",
                                                                 pady=5)
            spin = ttk.Spinbox(grid, from_=0, to=hi, width=5, textvariable=var,
                               command=self.push_live)
            spin.grid(row=r, column=1, padx=12)
            spin.bind("<Return>", lambda _e: self.push_live())
            spin.bind("<FocusOut>", lambda _e: self.push_live())
            ttk.Label(grid, text="pixels" + ("  (0 turns snapping off)" if r == 0 else ""),
                      style="Muted.TLabel").grid(row=r, column=2, sticky="w")

        c = self.card(page, "Startup and system tray")
        self.startup_var = tk.BooleanVar()
        self.tray_var = tk.BooleanVar()
        ttk.Checkbutton(c, text="Open this window when GW-X starts (untick to start in the tray)",
                        variable=self.startup_var, command=self.push_live,
                        style="Card.TCheckbutton").pack(anchor="w")
        ttk.Checkbutton(c, text="Minimize to the system tray instead of the taskbar",
                        variable=self.tray_var, command=self.push_live,
                        style="Card.TCheckbutton").pack(anchor="w", pady=(6, 0))
        self.tray_note = self._note(c, "", pady=(6, 0))
        self._note(c, "Closing this window doesn't stop GW-X. Reopen it from the tray icon, "
                      "with your settings hotkey, or by middle-clicking a box.", pady=(4, 0))

        c = self.card(page, "Program icon")
        self._note(c, f"In use: {ICON_SOURCE}", pady=(0, 6))
        self._note(c, "To use your own icon, save it as GW-X.ico (or GW-X.png) in the program "
                      "folder and restart GW-X. The window, the taskbar button and the tray icon "
                      "use it. To change the icon of the .exe file itself, put GW-X.ico next to "
                      "GW-X.py and run the build file again.")
        ttk.Button(c, text="Open program folder", style="Small.TButton", command=self.open_folder
                   ).pack(anchor="w", pady=(8, 0))

    # ------------------------------------------------------- client folder rows
    def exe_list(self):
        return [n.strip() for n in self.exe_var.get().split(",") if n.strip()]

    def rebuild_rows(self):
        for w in self.rows_frame.winfo_children():
            w.destroy()
        self.row_status = []
        count = len(self.folder_vars)
        for i, var in enumerate(self.folder_vars):
            block = ttk.Frame(self.rows_frame, style="Card.TFrame")
            block.pack(fill="x", pady=(0, 6))
            line = ttk.Frame(block, style="Card.TFrame")
            line.pack(fill="x")
            ttk.Label(line, text=str(i + 1), width=2, anchor="e", style="Card.TLabel"
                      ).pack(side="left")
            entry = ttk.Entry(line, textvariable=var)
            entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
            entry.bind("<KeyRelease>", lambda _e: self.refresh_row_status())
            entry.bind("<FocusOut>", lambda _e: self.refresh_row_status())
            ttk.Button(line, text="Browse...", width=9, style="Small.TButton",
                       command=lambda v=var: self.browse(v)).pack(side="left", padx=(6, 0))
            up = ttk.Button(line, text="Up", width=4, style="Small.TButton",
                            command=lambda i=i: self.move_row(i, -1))
            up.pack(side="left", padx=(6, 0))
            down = ttk.Button(line, text="Down", width=5, style="Small.TButton",
                              command=lambda i=i: self.move_row(i, 1))
            down.pack(side="left", padx=(2, 0))
            ttk.Button(line, text="X", width=3, style="Small.TButton",
                       command=lambda i=i: self.remove_row(i)).pack(side="left", padx=(6, 0))
            if i == 0:
                up.state(["disabled"])
            if i == count - 1:
                down.state(["disabled"])
            status = ttk.Label(block, text="", style="Card.TLabel", font=(UI_FONT, 8))
            status.pack(anchor="w", padx=(26, 0))
            self.row_status.append(status)
        self.refresh_row_status()

    def refresh_row_status(self):
        exes = self.exe_list()
        seen = set()
        p = self.pal
        for var, label in zip(self.folder_vars, self.row_status):
            folder = var.get().strip().strip('"')
            key = _norm(folder) if folder else None
            if not folder:
                text, color = "Type or browse to this client's folder", p["muted"]
            elif key in seen:
                text, color = "This is the same folder as an earlier client", p["bad"]
            elif not os.path.isdir(folder):
                text, color = "Folder not found", p["bad"]
            elif exes and not any(os.path.isfile(os.path.join(folder, n)) for n in exes):
                text, color = f"Folder found, but {exes[0]} isn't in it", p["warn"]
            else:
                text, color = (("Ready: " + exes[0] + " found") if exes else "Folder found"), p["ok"]
            if key:
                seen.add(key)
            label.configure(text=text, foreground=color)

    def add_row(self):
        self.folder_vars.append(tk.StringVar())
        self.rebuild_rows()

    def remove_row(self, i):
        del self.folder_vars[i]
        self.rebuild_rows()

    def move_row(self, i, step):
        j = i + step
        if 0 <= j < len(self.folder_vars):
            self.folder_vars[i], self.folder_vars[j] = self.folder_vars[j], self.folder_vars[i]
            self.rebuild_rows()

    def browse(self, var):
        start = var.get().strip()
        path = filedialog.askdirectory(
            parent=self.win, title="Choose the Guild Wars client folder", mustexist=True,
            initialdir=start if start and os.path.isdir(start) else None)
        if path:
            var.set(path)
            self.refresh_row_status()

    def detect(self):
        c = self.app.cfg
        found = []
        for hwnd in enum_visible_windows():
            cls, title = win_class(hwnd), win_title(hwnd)
            if cls in c["exclude_classes"] or "Guild Wars 2" in title:
                continue
            if cls in c["match_classes"] or title in c["match_titles"]:
                exe = pid_exe_path(win_pid(hwnd))
                folder = os.path.dirname(exe).replace("\\", "/") if exe else ""
                if folder and _norm(folder) not in {_norm(f) for f in found}:
                    found.append(folder)
        if not found:
            messagebox.showinfo("GW-X", "No running Guild Wars windows were found.\n\n"
                                "Start your clients first, or add the folders with Browse.",
                                parent=self.win)
            return
        self.folder_vars = [v for v in self.folder_vars if v.get().strip()]
        have = {_norm(v.get().strip()) for v in self.folder_vars}
        added = 0
        for folder in found:
            if _norm(folder) not in have:
                self.folder_vars.append(tk.StringVar(value=folder))
                added += 1
        self.rebuild_rows()
        self.set_status(f"Found {len(found)} running client folder(s), added {added} new.")

    # ---------------------------------------------------------- display handlers
    def on_opacity(self):
        self.push_live()

    def on_width(self):
        if self._loading or self._busy:
            return
        if self.ratio_var.get():
            self._busy = True
            self.h_var.set(max(68, min(450, round(self.w_var.get() * 9 / 16))))
            self._busy = False
        self.push_live()

    def on_height(self):
        if self._loading or self._busy:
            return
        if self.ratio_var.get():
            self._busy = True
            self.w_var.set(max(120, min(800, round(self.h_var.get() * 16 / 9))))
            self._busy = False
        self.push_live()

    def _paint_color(self):
        r, g, b = (int(self.color[i:i + 2], 16) for i in (1, 3, 5))
        fg = "black" if (0.299 * r + 0.587 * g + 0.114 * b) > 128 else "white"
        self.color_btn.configure(bg=self.color, fg=fg, activebackground=self.color,
                                 activeforeground=fg, text=self.color.upper())

    def set_color(self, color):
        self.color = color.lower()
        self._paint_color()
        self.push_live()

    def pick_color(self):
        _rgb, chosen = colorchooser.askcolor(color=self.color, parent=self.win,
                                             title="Colour of the client in front")
        if chosen:
            self.set_color(chosen)

    def open_folder(self):
        try:
            os.startfile(app_dir())
        except Exception as e:
            self.set_status(f"Couldn't open the folder: {e}", error=True)

    def on_hotkey_change(self):
        self.set_status("Hotkeys changed. Press Save settings to apply them.")

    # --------------------------------------------------------- reading the form
    @staticmethod
    def _int(var, lo, hi):
        try:
            return max(lo, min(hi, int(round(float(var.get())))))
        except (tk.TclError, ValueError, TypeError):
            raise ValueError("Please enter whole numbers in the number boxes.")

    def collect_live(self):
        return {
            "opacity": round(self._int(self.opacity_var, 10, 100) / 100, 2),
            "thumb_width": self._int(self.w_var, 120, 800),
            "thumb_height": self._int(self.h_var, 68, 450),
            "show_labels": bool(self.labels_var.get()),
            "hide_when_not_focused": bool(self.hide_var.get()),
            "font_size": self._int(self.font_var, 5, 16),
            "active_color": self.color,
            "snap_distance": self._int(self.snapd_var, 0, 60),
            "snap_gap": self._int(self.snapg_var, 0, 40),
            "auto_save_positions": bool(self.autosave_var.get()),
            "show_settings_on_start": bool(self.startup_var.get()),
            "dark_mode": bool(self.dark_var.get()),
            "minimize_to_tray": bool(self.tray_var.get()),
        }

    def collect_all(self):
        values = self.collect_live()
        values["client_folders"] = [v.get() for v in self.folder_vars]
        values["exe_names"] = self.exe_list()
        for key, field in self.hk.items():
            values[key] = field.value
        values["hotkey_slot_modifiers"] = "+".join(
            m for m in ("ctrl", "alt", "shift", "win") if self.mod_vars[m].get())
        return values

    def is_dirty(self):
        try:
            now = normalize_settings(self.collect_all())
        except ValueError:
            return True
        saved = normalize_settings(self.app.saved)
        return any(now.get(k) != saved.get(k) for k in SETTINGS_KEYS)

    def validate(self, v):
        """Return a problem description, or None when the settings are fine."""
        used = {}
        for key, label in (("hotkey_next", "Next client"), ("hotkey_prev", "Previous client"),
                           ("hotkey_settings", "Open this window"), ("hotkey_quit", "Quit GW-X")):
            if not v[key]:
                continue
            try:
                combo = parse_hotkey(v[key])
            except ValueError as e:
                return f"{label}: {e}"
            if combo in used:
                return f"'{label}' and '{used[combo]}' use the same keys."
            used[combo] = label
        if v["hotkey_slot_modifiers"]:
            mods = 0
            for part in v["hotkey_slot_modifiers"].split("+"):
                mods |= MODS.get(part, 0)
            for n in range(1, 10):
                combo = (mods, ord(str(n)))
                if combo in used:
                    return f"'{used[combo]}' uses the same keys as 'jump to client {n}'."
        keys = [_norm(f) for f in v["client_folders"]]
        if len(keys) != len(set(keys)):
            return "Two clients use the same folder."
        return None

    # ------------------------------------------------------------------ actions
    def push_live(self):
        """Display and layout changes show up immediately; they are saved with Save settings."""
        if self._loading:
            return
        try:
            values = self.collect_live()
        except ValueError:
            return
        self.app.apply_settings(values)
        self.set_status("Unsaved changes. Press Save settings to keep them."
                        if self.is_dirty() else "")

    def save(self):
        try:
            values = normalize_settings(self.collect_all())
        except ValueError as e:
            messagebox.showerror("GW-X", str(e), parent=self.win)
            return False
        problem = self.validate(values)
        if problem:
            messagebox.showerror("GW-X", problem, parent=self.win)
            return False
        self.app.apply_settings(values)
        self.app.persist(*SETTINGS_KEYS)
        self.load(self.app.cfg)
        if not values["client_folders"]:
            self.set_status("Saved. No client folders are set, so GW-X looks for Guild Wars "
                            "windows by name instead.", ok=True)
        else:
            self.set_status("Settings saved.", ok=True)
        return True

    def revert(self):
        saved = normalize_settings(self.app.saved)
        self.app.apply_settings(saved)
        self.load(self.app.cfg)
        self.set_status("Back to the last saved settings.")

    def save_positions(self):
        n = self.app.save_positions()
        if n:
            self.set_status(f"Saved the position of {n} box{'es' if n != 1 else ''}.", ok=True)
        else:
            self.set_status("No clients are running, so there are no positions to save yet.")

    def reset_positions(self):
        if messagebox.askyesno("GW-X", "Move all boxes back to their starting positions?",
                               parent=self.win):
            self.app.reset_positions()
            self.set_status("Positions reset.", ok=True)

    def _ask_about_unsaved(self):
        """True when it is fine to go on (saved, discarded or nothing to save)."""
        if not self.is_dirty():
            return True
        answer = messagebox.askyesnocancel("GW-X", "Save your changes first?", parent=self.win)
        if answer is None:
            return False
        if answer:
            return self.save()
        self.revert()
        return True

    def on_close(self):
        if self._ask_about_unsaved():
            self.hide()

    def on_quit(self):
        if self._ask_about_unsaved():
            self.app.quit()

    def hide(self):
        for field in self.hk.values():
            field._cancel()
        self.win.withdraw()

    def _on_unmap(self, event):
        """Minimizing hides the window in the tray (when the tray icon is running)."""
        if str(event.widget) != str(self.win) or not self.app.tray_active():
            return
        if not self.app.cfg.get("minimize_to_tray", True):
            return
        try:
            if self.win.state() == "iconic":
                self.win.after_idle(self.win.withdraw)
        except tk.TclError:
            pass

    def show(self):
        self.tray_note.configure(text=self.app.tray_text())
        self.load(self.app.cfg)
        self.set_status("")
        self.win.deiconify()
        self.win.lift()
        self.win.focus_force()
        set_dark_titlebar(self.win, bool(self.dark_var.get()))

    def set_status(self, text, ok=False, error=False):
        self._status_kind = "bad" if error else ("ok" if ok else "muted")
        self.status.configure(text=text)
        self._apply_status()

    def _apply_status(self):
        self.status.configure(fg=self.pal[self._status_kind])

    # ------------------------------------------------------------ outside updates
    def sync_sizes(self):
        """The box size changed outside this window (Ctrl + mouse wheel)."""
        self._loading = True
        try:
            self.w_var.set(self.app.cfg["thumb_width"])
            self.h_var.set(self.app.cfg["thumb_height"])
        finally:
            self._loading = False

    def sync_opacity(self):
        """The opacity in the .json file was edited by hand while the program ran."""
        self._loading = True
        try:
            self.opacity_var.set(round(get_opacity(self.app.cfg) * 100))
        finally:
            self._loading = False

    def load(self, cfg):
        self._loading = True
        try:
            v = normalize_settings(cfg)
            self.folder_vars = [tk.StringVar(value=f) for f in v["client_folders"]]
            self.exe_var.set(", ".join(v["exe_names"]))
            self.rebuild_rows()
            self.opacity_var.set(round(v["opacity"] * 100))
            self.w_var.set(v["thumb_width"])
            self.h_var.set(v["thumb_height"])
            self.ratio_var.set(abs(v["thumb_width"] * 9 / 16 - v["thumb_height"]) <= 2)
            self.labels_var.set(v["show_labels"])
            self.hide_var.set(v["hide_when_not_focused"])
            self.font_var.set(v["font_size"])
            self.color = v["active_color"] if re.fullmatch(r"#[0-9a-f]{6}", v["active_color"]) \
                else COLOR_ACTIVE
            self._paint_color()
            self.snapd_var.set(v["snap_distance"])
            self.snapg_var.set(v["snap_gap"])
            self.autosave_var.set(v["auto_save_positions"])
            self.startup_var.set(v["show_settings_on_start"])
            self.tray_var.set(v["minimize_to_tray"])
            self.dark_var.set(v["dark_mode"])
            if self._dark_applied != v["dark_mode"]:
                self.apply_theme()
            for key, field in self.hk.items():
                field.set(v[key])
            mods = set(v["hotkey_slot_modifiers"].split("+")) - {""}
            for name, var in self.mod_vars.items():
                var.set(name in mods)
        finally:
            self._loading = False


# ----------------------------------------------------------------------- app
class App:
    def __init__(self):
        self.cfg = load_config()
        self.saved = copy.deepcopy(self.cfg)  # what is on disk right now
        if not os.path.exists(CONFIG_PATH):
            save_config(self.saved)
        global LABEL_FONT_FAMILY, UI_FONT, FONT_SOURCE
        family = load_label_font(self.cfg)  # before Tk starts, so Tk can see the font
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("GW-X")
        set_window_icon(self.root)
        if family.lower() not in {f.lower() for f in tkfont.families(self.root)}:
            print(f"Font '{family}' isn't usable here; using Segoe UI instead.")
            family = "Segoe UI"
            FONT_SOURCE = "Segoe UI (the font file couldn't be used)"
        LABEL_FONT_FAMILY = UI_FONT = family  # the Settings window uses the same font too
        self.previews = {}
        self.cmds = queue.Queue()
        self.settings = None
        self.tray = None
        self.hk_failed = []
        self._hk = None
        self._cfg_mtime = self._mtime()
        self.apply_look()
        print(f"Label font: {family}, {LABEL_FONT_SIZE} pt")
        self.start_hotkeys()

    # -- saving
    @staticmethod
    def _mtime():
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return None

    def persist(self, *keys):
        """Write the given settings to the .json file (everything else stays as saved)."""
        for k in keys:
            self.saved[k] = copy.deepcopy(self.cfg[k])
        save_config(self.saved)
        self._cfg_mtime = self._mtime()

    def save_positions(self):
        n = 0
        for p in self.previews.values():
            self.cfg["positions"][str(p.slot)] = [p.x, p.y]
            n += 1
        if n:
            self.persist("positions")
        return n

    def reset_positions(self):
        self.cfg["positions"] = {}
        for p in self.previews.values():
            _w, h = p._size()
            p.place_window(20, 20 + (p.slot - 1) * (h + 10))
        self.persist("positions")

    # -- applying settings to the running program
    def apply_look(self):
        """(Re)calculate label font size, label strip height and the in-front colour."""
        global LABEL_FONT_SIZE, LABEL_H, COLOR_ACTIVE, ACTIVE_TEXT
        try:
            size = max(4, min(24, int(round(float(self.cfg["font_size"])))))
        except (TypeError, ValueError):
            size = 7
        LABEL_FONT_SIZE = size
        LABEL_H = tkfont.Font(root=self.root, family=LABEL_FONT_FAMILY,
                              size=size).metrics("linespace") + 4
        color = str(self.cfg.get("active_color", "")).strip()
        if re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            COLOR_ACTIVE = color.lower()
            r, g, b = (int(COLOR_ACTIVE[i:i + 2], 16) for i in (1, 3, 5))
            ACTIVE_TEXT = "black" if (0.299 * r + 0.587 * g + 0.114 * b) > 128 else "white"
        else:
            print(f"active_color {color!r} isn't a #RRGGBB value; using {COLOR_ACTIVE}.")
        for p in self.previews.values():
            p.label.configure(font=(LABEL_FONT_FAMILY, size))
            p.active = None  # force the colours to be applied again
            p.place_window()
        self.refresh_state()

    def apply_settings(self, new):
        """Apply some settings to the running program (does not write the file)."""
        c = self.cfg
        old = {k: c.get(k) for k in new}
        c.update(new)
        changed = {k for k in new if old[k] != new[k]}
        if changed & {"client_folders", "exe_names"}:
            for hwnd in list(self.previews):  # slots depend on the folder order
                self.previews.pop(hwnd).destroy()
            self.sync()
        if "opacity" in changed:
            for p in self.previews.values():
                p.apply_opacity()
        if changed & {"font_size", "active_color"}:
            self.apply_look()
        elif changed & {"thumb_width", "thumb_height", "show_labels"}:
            for p in self.previews.values():
                p.place_window()
        if "hide_when_not_focused" in changed:
            self.refresh_state()
        if changed & HOTKEY_KEYS:
            self.start_hotkeys()

    def open_settings(self):
        if self.settings is None:
            self.settings = SettingsWindow(self)
        self.settings.show()

    def quit(self):
        self.root.quit()

    def tray_active(self):
        return bool(self.tray and self.tray.active)

    def tray_text(self):
        if self.tray_active():
            return "Tray icon is running: left-click it to open this window, right-click for the menu."
        return (self.tray.problem if self.tray and self.tray.problem
                else "The tray icon isn't running.") + " Minimizing uses the taskbar instead."

    # -- hotkeys
    def start_hotkeys(self):
        self.stop_hotkeys()
        c = self.cfg
        self.hk_failed = []
        wanted = []
        for key, action in (("hotkey_next", "next"), ("hotkey_prev", "prev"),
                            ("hotkey_settings", "settings"), ("hotkey_quit", "quit")):
            if c.get(key):
                wanted.append((c[key], action))
        if c.get("hotkey_slot_modifiers"):
            for n in range(1, 10):
                wanted.append((f"{c['hotkey_slot_modifiers']}+{n}", ("slot", n)))
        specs = []
        for text, action in wanted:
            try:
                mods, vk = parse_hotkey(text)
                specs.append((mods, vk, action, text))
            except ValueError as e:
                print(f"Warning: {e}")
        state = {"tid": None, "ready": threading.Event()}
        thread = threading.Thread(target=hotkey_thread, args=(specs, self.cmds, state), daemon=True)
        state["thread"] = thread
        self._hk = state
        thread.start()

    def stop_hotkeys(self):
        state, self._hk = self._hk, None
        if not state:
            return
        if state["ready"].wait(2) and state["tid"]:
            user32.PostThreadMessageW(state["tid"], WM_QUIT, 0, 0)
        state["thread"].join(2)

    def pause_hotkeys(self):
        self.stop_hotkeys()

    def resume_hotkeys(self):
        self.start_hotkeys()

    # -- slots and labels
    def free_slot(self):
        used = {p.slot for p in self.previews.values()}
        # In folder mode, slots 1..N belong to the listed folders; extras go after them.
        n = len(client_folder_list(self.cfg)) + 1
        while n in used:
            n += 1
        return n

    def pick_slot(self, folder_index):
        if folder_index is not None:
            want = folder_index + 1
            if all(p.slot != want for p in self.previews.values()):
                return want
        return self.free_slot()

    def display_text(self, info):
        title, idx = info
        if idx is None:
            return title
        folder = client_folder_list(self.cfg)[idx]
        name = os.path.basename(folder.rstrip("/\\")) or folder
        return name if title in ("", "Guild Wars") else f"{name} - {title}"

    def ordered(self):
        return sorted(self.previews.values(), key=lambda p: p.slot)

    def resize(self, factor):
        c = self.cfg
        c["thumb_width"] = max(120, min(1200, int(c["thumb_width"] * factor)))
        c["thumb_height"] = max(68, min(700, int(c["thumb_height"] * factor)))
        for p in self.previews.values():
            p.place_window()
        self.persist("thumb_width", "thumb_height")
        if self.settings:
            self.settings.sync_sizes()

    def snap(self, p, x, y):
        """Snap a dragged preview to the edges of the other previews.

        It sticks side-by-side or on top/bottom (flush, with `snap_gap` pixels between)
        and also lines up matching edges, whenever it comes within `snap_distance`.
        """
        c = self.cfg
        dist, gap = c["snap_distance"], c["snap_gap"]
        if dist <= 0:
            return x, y
        w, h = p._size()
        others = [(q.x, q.y, *q._size())
                  for q in self.previews.values() if q is not p and q.visible]

        best = None  # horizontal: only against boxes that are near us vertically
        for qx, qy, qw, qh in others:
            if y < qy + qh + dist and y + h > qy - dist:
                for cand in (qx - w - gap, qx + qw + gap, qx, qx + qw - w):
                    d = abs(cand - x)
                    if d <= dist and (best is None or d < best[0]):
                        best = (d, cand)
        if best:
            x = best[1]

        best = None  # vertical: uses the already-snapped x
        for qx, qy, qw, qh in others:
            if x < qx + qw + dist and x + w > qx - dist:
                for cand in (qy - h - gap, qy + qh + gap, qy, qy + qh - h):
                    d = abs(cand - y)
                    if d <= dist and (best is None or d < best[0]):
                        best = (d, cand)
        if best:
            y = best[1]
        return x, y

    def focus(self, hwnd):
        """Focus a client and move the highlight right away instead of waiting for Windows."""
        focus_window(hwnd)
        for p in self.previews.values():
            p.set_active(p.hwnd == hwnd)

    def refresh_state(self):
        """Keep highlight and show/hide in step with the real foreground window."""
        fg = user32.GetForegroundWindow()
        own = {p.dest for p in self.previews.values()}
        show = (not self.cfg["hide_when_not_focused"]) or fg in self.previews or fg in own
        for hwnd, p in self.previews.items():
            p.set_active(hwnd == fg)
            if p.set_visible(show):
                p.update_thumb()

    def cycle(self, step):
        order = self.ordered()
        if not order:
            return
        fg = user32.GetForegroundWindow()
        idx = next((i for i, p in enumerate(order) if p.hwnd == fg), None)
        target = order[0] if idx is None else order[(idx + step) % len(order)]
        self.focus(target.hwnd)

    def run_commands(self):
        while True:
            try:
                cmd = self.cmds.get_nowait()
            except queue.Empty:
                break
            if cmd == "next":
                self.cycle(1)
            elif cmd == "prev":
                self.cycle(-1)
            elif cmd == "quit":
                self.quit()
            elif cmd == "settings":
                self.open_settings()
            elif isinstance(cmd, tuple) and cmd[0] == "slot":
                for p in self.previews.values():
                    if p.slot == cmd[1]:
                        self.focus(p.hwnd)
            elif isinstance(cmd, tuple) and cmd[0] == "hkfail":
                self.hk_failed = cmd[1]
                text = ("Another program already uses these hotkeys: "
                        + ", ".join(pretty_hotkey(h) for h in cmd[1]))
                log(text)
                if self.settings:
                    self.settings.set_status(text, error=True)
        # A second GW-X started: it asks this one to show its settings instead.
        if SHOW_EVENT and kernel32.WaitForSingleObject(SHOW_EVENT, 0) == 0:
            self.open_settings()

    def reload_opacity(self):
        """Pick up an opacity edited by hand in the .json file, without a restart."""
        mtime = self._mtime()
        if mtime is None or mtime == self._cfg_mtime:
            return
        self._cfg_mtime = mtime
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                on_disk = json.load(f)
        except Exception:
            return  # file is mid-edit or invalid; the next save triggers another look
        if "opacity" in on_disk and on_disk["opacity"] != self.saved.get("opacity"):
            self.cfg["opacity"] = self.saved["opacity"] = on_disk["opacity"]
            for p in self.previews.values():
                p.apply_opacity()
            if self.settings:
                self.settings.sync_opacity()

    def sync(self):
        current = find_clients(self.cfg)

        for hwnd in list(self.previews):
            if hwnd not in current:
                self.previews.pop(hwnd).destroy()

        for hwnd in sorted((h for h in current if h not in self.previews), key=win_pid):
            self.previews[hwnd] = Preview(self, hwnd, self.pick_slot(current[hwnd][1]))

        for hwnd, p in self.previews.items():
            p.set_title(self.display_text(current[hwnd]))
            p.update_thumb()
        self.refresh_state()

    def tick(self):
        try:
            self.reload_opacity()
            self.sync()
        except Exception:
            log(traceback.format_exc())
        self.root.after(POLL_MS, self.tick)

    def fast_tick(self):
        try:
            self.run_commands()
            self.refresh_state()
        except Exception:
            log(traceback.format_exc())
        self.root.after(FAST_MS, self.fast_tick)

    def run(self):
        c = self.cfg
        folders = client_folder_list(c)
        print("GW-X running "
              + (f"(folder mode, {len(folders)} folders)." if folders else "(class/title mode)."))
        print("  Left-click a box: focus | Right-drag: move | Ctrl+wheel: resize | "
              "Middle-click: settings")
        self.tray = Tray(self.cmds)
        self.tray.start()
        if c.get("show_settings_on_start", True) or not self.tray_active():
            self.open_settings()  # never start with nothing to click on
        self.tick()
        self.fast_tick()
        try:
            self.root.mainloop()
        finally:
            self.tray.stop()
            self.stop_hotkeys()
            for p in list(self.previews.values()):
                p.destroy()


# ---------------------------------------------------------------------- system tray
ICON_NAMES = ("GW-X.ico", "GW-X.png")  # your own icon: save one of these next to the program


def find_custom_icon():
    for name in ICON_NAMES:
        path = os.path.join(app_dir(), name)
        if os.path.isfile(path):
            return path
    return None


def make_icon_image(size=256):
    """The built-in icon: a Toolbox-green rounded square with a dark X, drawn at any size."""
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    k = size / 64
    draw.rounded_rectangle((2 * k, 2 * k, size - 2 * k - 1, size - 2 * k - 1), radius=12 * k,
                           fill=(0, 255, 0, 255))
    dark, width = (4, 18, 10, 255), max(2, round(8 * k))
    draw.line((18 * k, 18 * k, 46 * k, 46 * k), fill=dark, width=width)
    draw.line((46 * k, 18 * k, 18 * k, 46 * k), fill=dark, width=width)
    return img


def make_tray_icon_image(size=64):
    """Icon for the tray: your GW-X.ico / GW-X.png if there is one, else the built-in drawing."""
    from PIL import Image
    path = find_custom_icon()
    if path:
        try:
            img = Image.open(path)
            img.load()
            resample = Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS
            return img.convert("RGBA").resize((size, size), resample)
        except Exception as e:
            log(f"Could not read the icon {path}: {e}")
    return make_icon_image(size)


def make_icon_file(path):
    """Write the built-in icon as a multi-size .ico (the build file uses it for the .exe)."""
    make_icon_image(256).save(path, format="ICO",
                              sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64),
                                     (128, 128), (256, 256)])


def set_window_icon(window):
    """Use the program icon for the window, the taskbar button and Alt+Tab."""
    global ICON_SOURCE
    path = find_custom_icon()
    try:
        if path and path.lower().endswith(".ico"):
            window.iconbitmap(default=path)
            ICON_SOURCE = f"{os.path.basename(path)} from the program folder"
            return
        if path:
            photo = tk.PhotoImage(file=path)
            ICON_SOURCE = f"{os.path.basename(path)} from the program folder"
        else:
            import base64
            import io
            buffer = io.BytesIO()
            make_icon_image(64).save(buffer, format="PNG")
            photo = tk.PhotoImage(data=base64.b64encode(buffer.getvalue()))
            ICON_SOURCE = "the built-in green icon"
        window.iconphoto(True, photo)
        window._gwx_icon = photo  # Tk forgets the icon if nothing keeps the image alive
    except Exception as e:
        ICON_SOURCE = "Tk's plain default icon (the icon couldn't be loaded)"
        log(f"Could not set the window icon: {e}")


class Tray:
    """Icon in the notification area. Needs the optional packages pystray and pillow.

    The menu callbacks only put commands in the queue; the Tk thread carries them out.
    """

    def __init__(self, cmds):
        self.cmds = cmds
        self.icon = None
        self.problem = ""

    @property
    def active(self):
        return self.icon is not None

    def start(self):
        try:
            import pystray
            image = make_tray_icon_image()
        except Exception as e:
            self.problem = "The tray icon needs two add-ons: pip install pystray pillow"
            log(f"Tray icon unavailable: {e}")
            return False
        menu = pystray.Menu(
            pystray.MenuItem("Open settings", lambda icon, item: self.cmds.put("settings"),
                             default=True),  # the default item is what a left click runs
            pystray.MenuItem("Quit GW-X", lambda icon, item: self.cmds.put("quit")))
        try:
            icon = pystray.Icon("GW-X", image, "GW-X", menu)
            icon.run_detached()
        except Exception as e:
            self.problem = f"The tray icon couldn't start: {e}"
            log(self.problem)
            return False
        self.icon = icon
        return True

    def stop(self):
        icon, self.icon = self.icon, None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass


# --------------------------------------------------------------------- start up
SHOW_EVENT = None
_keepalive = []


def single_instance():
    """False when GW-X is already running; that copy is asked to show its settings."""
    global SHOW_EVENT
    mutex = kernel32.CreateMutexW(None, False, "Local\\GW-X-single-instance")
    already = ctypes.get_last_error() == 183  # ERROR_ALREADY_EXISTS
    SHOW_EVENT = kernel32.CreateEventW(None, False, False, "Local\\GW-X-show-settings")
    _keepalive.append(mutex)
    if already:
        if SHOW_EVENT:
            kernel32.SetEvent(SHOW_EVENT)
        return False
    return True


def main():
    # Must happen before Tk creates any window, so coordinates match DWM's pixels.
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass
    if "--list" in sys.argv:
        list_windows()
        return
    if "--make-icon" in sys.argv:
        target = os.path.join(app_dir(), "GW-X.ico")
        make_icon_file(target)
        print(f"Wrote the built-in icon to {target}")
        return
    try:  # gives GW-X its own taskbar identity (and icon) instead of python's
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("GW-X.GuildWars1.Preview")
    except Exception:
        pass
    if not single_instance():
        return
    try:
        App().run()
    except Exception:
        text = traceback.format_exc()
        log(text)
        user32.MessageBoxW(None, "GW-X stopped because of an error:\n\n" + text[-1200:]
                           + "\n\nThe details are also in GW-X.log next to the program.",
                           "GW-X", 0x10)


if __name__ == "__main__":
    main()
