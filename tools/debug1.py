"""排查：图片入库失败、全局热键不触发、托盘权限。"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app import images, paste, winclip  # noqa: E402
from app.app import ClipboardApp  # noqa: E402
from app.hotkey import HotkeyManager  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


def env_info() -> None:
    pid = kernel32.GetCurrentProcessId()
    sid = ctypes.c_ulong()
    kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid))
    print(f"进程 {pid} 会话 {sid.value}")
    print("前台窗口", int(user32.GetForegroundWindow() or 0))
    print("窗口站", int(user32.GetProcessWindowStation() or 0))
    print("桌面", int(user32.GetThreadDesktop(kernel32.GetCurrentThreadId()) or 0))


def image_case() -> None:
    print("\n=== 图片入库 ===")
    tmp = Path(tempfile.mkdtemp(prefix="cm-dbg-"))
    app = ClipboardApp(data_dir=tmp)
    img = Image.new("RGB", (120, 80), (200, 40, 60))
    winclip.write_image(b"", images.image_to_dib(img))
    content = winclip.read_clipboard()
    print("读回", content.kind, "png" if content.png else "dib", len(content.png or content.dib or b""))
    try:
        app._ingest_image(content)
        print("正常返回")
    except Exception:
        traceback.print_exc()
    while not app.queue.empty():
        print("队列事件", app.queue.get_nowait())
    print("图片条数", app.store.count("image") if hasattr(app.store, "count") else "?")
    app.store.close()


def hotkey_case() -> None:
    print("\n=== 全局热键 ===")
    fired = {"n": 0}

    def on_fire():
        fired["n"] += 1
        print("  <- 热键回调", fired["n"])

    hk = HotkeyManager(on_fire)
    ok = hk.start(["ctrl", "alt"], "f9")
    print("注册结果", ok, "错误", hk.error, "线程 id", hk._thread_id)
    time.sleep(0.3)
    sent = paste.send_hotkey(["ctrl", "alt"], 0x78)
    print("SendInput 返回", sent, "错误码", ctypes.get_last_error())
    time.sleep(1.5)
    print("真实按键后回调次数", fired["n"])

    print("改用 PostThreadMessage 直接投递 WM_HOTKEY")
    user32.PostThreadMessageW(hk._thread_id, 0x0312, 1, 0)
    time.sleep(0.6)
    print("投递后回调次数", fired["n"])
    hk.stop()


def tray_case() -> None:
    print("\n=== 托盘 ===")
    try:
        import pystray._win32 as w32
        import pystray._util.win32 as uw

        print("ChangeWindowMessageFilterEx", uw.win32.ChangeWindowMessageFilterEx)
    except Exception:
        traceback.print_exc()
    from app.tray import TrayIcon

    tray = TrayIcon(lambda: None, lambda: None, lambda: None)
    print("start ->", tray.start(), tray.error)
    time.sleep(1.5)
    print("icon.visible", getattr(tray._icon, "visible", None))
    tray.stop()


if __name__ == "__main__":
    env_info()
    image_case()
    hotkey_case()
    tray_case()
