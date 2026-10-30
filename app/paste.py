"""把焦点还给目标窗口并模拟 Ctrl+V（SendInput）。"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002
INPUT_KEYBOARD = 1


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
user32.SendInput.restype = wintypes.UINT
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.SetActiveWindow.argtypes = [wintypes.HWND]
user32.SetActiveWindow.restype = wintypes.HWND
try:  # 未公开但一直存在；拿不到就退化为只靠 SetForegroundWindow
    user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    user32.SwitchToThisWindow.restype = None
except AttributeError:  # pragma: no cover
    user32.SwitchToThisWindow = lambda *a: None

SW_RESTORE = 9


def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def _key(vk: int, up: bool) -> INPUT:
    inp = INPUT()
    inp.type = INPUT_KEYBOARD
    inp.u.ki.wVk = vk
    inp.u.ki.wScan = 0
    inp.u.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
    inp.u.ki.time = 0
    inp.u.ki.dwExtraInfo = None
    return inp


def send_ctrl_v() -> bool:
    seq = (_key(VK_CONTROL, False), _key(VK_V, False), _key(VK_V, True), _key(VK_CONTROL, True))
    arr = (INPUT * len(seq))(*seq)
    sent = user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))
    return int(sent) == len(seq)


VK_MENU = 0x12
VK_SHIFT = 0x10
VK_LWIN = 0x5B
_MOD_VK = {"ctrl": VK_CONTROL, "alt": VK_MENU, "shift": VK_SHIFT, "win": VK_LWIN}


def send_hotkey(mods: list[str], vk: int) -> bool:
    """模拟按下一组组合键（自动化测试用）。"""
    keys = [_MOD_VK[m] for m in mods if m in _MOD_VK]
    seq = [_key(k, False) for k in keys]
    seq.append(_key(vk, False))
    seq.append(_key(vk, True))
    seq.extend(_key(k, True) for k in reversed(keys))
    arr = (INPUT * len(seq))(*seq)
    return int(user32.SendInput(len(seq), arr, ctypes.sizeof(INPUT))) == len(seq)


def activate(hwnd: int, delay: float = 0.15) -> bool:
    """把目标窗口切到前台，返回是否成功。

    Windows 只在"前台进程"自己调 SetForegroundWindow 时才老实听话，所以
    把当前前台窗口的线程和本线程都 AttachThreadInput 上去，失败再退到
    BringWindowToTop / SwitchToThisWindow（未公开但常用）。
    """
    if not is_window(hwnd):
        return False
    try:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        if foreground_window() == hwnd:
            time.sleep(delay)
            return True

        current = int(kernel32.GetCurrentThreadId())
        threads: list[int] = []
        for other in (foreground_window(), hwnd):
            if not other:
                continue
            tid = int(user32.GetWindowThreadProcessId(other, None))
            if tid and tid != current and tid not in threads:
                threads.append(tid)

        attached: list[int] = []
        for tid in threads:
            if user32.AttachThreadInput(current, tid, True):
                attached.append(tid)
        try:
            user32.SetForegroundWindow(hwnd)
            user32.BringWindowToTop(hwnd)
            user32.SetActiveWindow(hwnd)
            if foreground_window() != hwnd:
                user32.SwitchToThisWindow(hwnd, True)
        finally:
            for tid in attached:
                user32.AttachThreadInput(current, tid, False)
        time.sleep(delay)
        return foreground_window() == hwnd
    except Exception:
        return False


def paste_into(hwnd: int, delay_ms: int = 200, retries: int = 3) -> bool:
    """激活目标窗口后发送 Ctrl+V，并确认焦点真的回去了。

    Windows 的前台锁会让 SetForegroundWindow 偶尔失败；失败时目标窗口
    收不到按键，粘贴就"没反应"。所以先切前台、确认 GetForegroundWindow
    等于目标窗口，再发按键，最多重试几次。
    """
    if not is_window(hwnd):
        return False
    for attempt in range(max(1, retries)):
        wait = delay_ms / 1000.0 if attempt == 0 else 0.12
        activate(hwnd, delay=wait)
        if foreground_window() != hwnd:
            continue
        time.sleep(0.03)
        if send_ctrl_v():
            return True
    return False
