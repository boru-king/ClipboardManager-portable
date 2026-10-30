"""把主窗口渲染成 PNG（PrintWindow，窗口被遮挡也能截到），用于检查排版。

运行：.venv\\Scripts\\python.exe tools\\shot.py [输出png] [宽] [高]
"""

from __future__ import annotations

import ctypes
import json
import sys
import tempfile
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from preview import seed  # noqa: E402

from app.app import ClipboardApp  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

# 进程 DPI 感知：否则高缩放下 GetWindowRect 返回虚拟化尺寸，图会被裁掉一圈
try:
    ctypes.WinDLL("shcore").SetProcessDpiAwareness(1)
except Exception:
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def capture(hwnd: int, path: Path) -> tuple[int, int]:
    from PIL import Image

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        raise RuntimeError(f"窗口尺寸异常 {w}x{h}")

    hwnd_dc = user32.GetWindowDC(hwnd)
    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bitmap = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    gdi32.SelectObject(mem_dc, bitmap)
    try:
        ok = user32.PrintWindow(hwnd, mem_dc, 2)  # PW_RENDERFULLCONTENT
        if not ok:
            ok = user32.PrintWindow(hwnd, mem_dc, 0)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h  # 负数 = 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0  # BI_RGB
        buf = ctypes.create_string_buffer(w * h * 4)
        got = gdi32.GetDIBits(mem_dc, bitmap, 0, h, buf, ctypes.byref(bmi), 0)
        if got != h:
            raise RuntimeError(f"GetDIBits 返回 {got}/{h}")
        img = Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)
        img.save(path, "PNG")
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)
    return w, h


def find_window(title: str) -> int:
    """按窗口标题找可见窗口的句柄（找不到返回 0）。"""
    found: list[int] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _cb(hwnd, _lparam):
        buf = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value == title and user32.IsWindowVisible(hwnd):
            found.append(hwnd)
            return False
        return True

    user32.EnumWindows(_cb, 0)
    return found[0] if found else 0


def main() -> int:
    raw_args = sys.argv[1:]
    want_settings = "--settings" in raw_args
    args = [a for a in raw_args if not a.startswith("--")]
    out = Path(args[0]) if len(args) > 0 else ROOT / "build" / "shot_main.png"
    width = int(args[1]) if len(args) > 1 else 1000
    height = int(args[2]) if len(args) > 2 else 660

    tmp = Path(tempfile.mkdtemp(prefix="cm-shot-"))
    app = ClipboardApp(data_dir=tmp)
    seed(app)
    win = MainWindow(app)
    app.window = win
    win.root.geometry(f"{width}x{height}")
    win.show()
    win.root.update()
    win.root.lift()

    results = {}

    def shoot_settings() -> None:
        try:
            from app.ui.settings import SettingsWindow

            sw = SettingsWindow(app)
            sw.root.update()
            sw.root.lift()
            sw.root.update()
            hwnd = int(sw.root.wm_frame(), 16)
            target = ROOT / "build" / "shot_settings.png"
            w, h = capture(hwnd, target)
            results["settings"] = f"{w}x{h} -> {target}"
            sw.root.grab_release()
            sw.root.destroy()
        except Exception as exc:  # noqa: BLE001
            results["settings_error"] = repr(exc)
        finally:
            win.root.destroy()

    def work() -> None:
        try:
            win.root.update()
            hwnd = int(win.root.wm_frame(), 16)
            w, h = capture(hwnd, out)
            results["main"] = f"{w}x{h} -> {out}"
            rect = {
                "x": win.root.winfo_rootx(),
                "y": win.root.winfo_rooty(),
                "w": win.root.winfo_width(),
                "h": win.root.winfo_height(),
            }
            (ROOT / "build" / "window_rect.json").write_text(json.dumps(rect), "utf-8")
        except Exception as exc:  # noqa: BLE001
            results["error"] = repr(exc)
            win.root.destroy()
            return
        if want_settings:
            win.root.after(400, shoot_settings)
        else:
            win.root.destroy()

    win.root.after(900, work)
    win.root.mainloop()
    try:
        app.store.close()
    except Exception:
        pass
    print(results)
    bad = "error" in results or "settings_error" in results
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
