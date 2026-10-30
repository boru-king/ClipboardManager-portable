"""全局热键：RegisterHotKey + 线程消息循环，纯 ctypes 零依赖。"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

ERROR_HOTKEY_ALREADY_REGISTERED = 1409

user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.RegisterHotKey.restype = wintypes.BOOL
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.UnregisterHotKey.restype = wintypes.BOOL
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]
user32.GetMessageW.restype = wintypes.BOOL
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.PostThreadMessageW.restype = wintypes.BOOL
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

MOD_MAP = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "cmd": MOD_WIN,
    "super": MOD_WIN,
}

# 支持的按键 -> 虚拟键码（统一用小写键名，查找前一律 .lower()）
VK_MAP: dict[str, int] = {chr(c).lower(): c for c in range(0x41, 0x5B)}  # a-z
VK_MAP.update({chr(c): c for c in range(0x41, 0x5B)})  # A-Z（兼容旧配置）
VK_MAP.update({str(d): 0x30 + d for d in range(10)})  # 0-9
VK_MAP.update({f"f{i}": 0x6F + i for i in range(1, 13)})  # F1-F12
VK_MAP.update(
    {
        "space": 0x20,
        "tab": 0x09,
        "enter": 0x0D,
        "return": 0x0D,
        "insert": 0x2D,
        "delete": 0x2E,
        "home": 0x24,
        "end": 0x23,
        "pageup": 0x21,
        "pagedown": 0x22,
        "up": 0x26,
        "down": 0x28,
        "left": 0x25,
        "right": 0x27,
        "`": 0xC0,
        "-": 0xBD,
        "=": 0xBB,
        "[": 0xDB,
        "]": 0xDD,
        "\\": 0xDC,
        ";": 0xBA,
        "'": 0xDE,
        ",": 0xBC,
        ".": 0xBE,
        "/": 0xBF,
    }
)


def describe(mods: list[str], key: str) -> str:
    names = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win", "cmd": "Win", "super": "Win"}
    parts = [names.get(m, m.title()) for m in mods]
    parts.append(key.upper() if len(key) == 1 else key.upper())
    return "+".join(parts)


class HotkeyManager:
    """在后台线程注册热键，回调通过 on_fire 抛给上层（可能不在主线程）。"""

    def __init__(self, on_fire) -> None:
        self._on_fire = on_fire
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._error: str = ""
        self._mods = 0
        self._vk = 0

    @property
    def error(self) -> str:
        return self._error

    def start(self, mods: list[str], key: str) -> bool:
        """注册并开始监听。返回是否成功。"""
        self.stop()
        self._mods = 0
        for m in mods:
            self._mods |= MOD_MAP.get(m.lower(), 0)
        self._vk = VK_MAP.get(key.lower(), 0)
        if not self._mods or not self._vk:
            self._error = "热键无效"
            return False
        self._error = ""
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hotkey-loop", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)
        return not self._error

    def _run(self) -> None:
        self._thread_id = int(kernel32.GetCurrentThreadId())
        ok = user32.RegisterHotKey(None, 1, self._mods | MOD_NOREPEAT, self._vk)
        if not ok:
            err = ctypes.get_last_error()
            self._error = (
                "该快捷键已被其它程序占用" if err == ERROR_HOTKEY_ALREADY_REGISTERED else f"注册失败（错误码 {err}）"
            )
            self._ready.set()
            return
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while True:
                ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if ret == 0 or ret == -1:
                    break
                if msg.message == WM_HOTKEY:
                    try:
                        self._on_fire()
                    except Exception:
                        pass
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=1.0)
        self._thread = None
        self._thread_id = 0
