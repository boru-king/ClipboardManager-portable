"""系统托盘图标（pystray）。菜单回调在托盘线程触发，由上层转投主线程。"""

from __future__ import annotations

import threading

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    pystray = None  # type: ignore[assignment]


def make_icon(size: int = 64, bg: str = "#2F6FED"):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([1, 1, size - 2, size - 2], radius=14, fill=bg)
    pad = 14
    d.rounded_rectangle([pad, pad + 5, size - pad, size - pad - 2], radius=5, fill="white")
    d.rounded_rectangle([size // 2 - 10, pad - 1, size // 2 + 10, pad + 8], radius=4, fill="white")
    for i, y in enumerate(range(pad + 20, size - pad - 8, 8)):
        d.line([pad + 6, y, size - pad - 8 + (0 if i % 2 == 0 else 0), y], fill=bg, width=2)
    return img


def _relax_message_filter() -> None:
    """pystray 启动时会调 ChangeWindowMessageFilterEx 放宽任务栏重建消息。

    这一步只是「资源管理器重启后图标能回来」的优化，在部分受限环境会返回
    WinError 5（拒绝访问）并让整个托盘线程崩掉；这里降级为忽略失败。
    """
    try:
        from pystray._util import win32 as pywin32

        orig = getattr(pywin32, "ChangeWindowMessageFilterEx", None)
        if orig is None or getattr(pywin32, "_cmfe_relaxed", False):
            return

        def safe_change_filter(hwnd, message, action, change_filter_struct):
            try:
                return orig(hwnd, message, action, change_filter_struct)
            except Exception:
                return 1

        pywin32.ChangeWindowMessageFilterEx = safe_change_filter
        pywin32._cmfe_relaxed = True
    except Exception:
        pass


class TrayIcon:
    def __init__(self, on_show, on_quit, on_settings=None, on_purge=None) -> None:
        self._on_show = on_show
        self._on_quit = on_quit
        self._on_settings = on_settings
        self._on_purge = on_purge
        self._icon = None
        self._thread: threading.Thread | None = None
        self.available = pystray is not None
        self.error = "" if self.available else "缺少 pystray，托盘不可用"
        if self.available:
            _relax_message_filter()

    def start(self) -> bool:
        if not self.available:
            return False
        try:
            items = [pystray.MenuItem("显示窗口", self._show, default=True)]
            if self._on_settings:
                items.append(pystray.MenuItem("设置", self._settings))
            items.append(pystray.Menu.SEPARATOR)
            if self._on_purge:
                items.append(pystray.MenuItem("清除全部数据并退出…", self._purge))
            items.append(pystray.MenuItem("退出", self._quit))
            self._icon = pystray.Icon(
                "clipboard-manager",
                make_icon(),
                "剪贴板管理器",
                menu=pystray.Menu(*items),
            )
            self._thread = threading.Thread(target=self._icon.run, name="tray", daemon=True)
            self._thread.start()
            return True
        except Exception as exc:
            self.error = str(exc)
            return False

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def notify(self, message: str, title: str = "剪贴板管理器") -> bool:
        """气泡提示。Windows 上会显示在任务栏图标附近，顺便告诉用户图标在哪。"""
        try:
            if self._icon is not None:
                self._icon.notify(message, title)
                return True
        except Exception as exc:
            self.error = str(exc)
        return False

    def _show(self, *_args) -> None:
        self._on_show()

    def _settings(self, *_args) -> None:
        self._on_settings()

    def _purge(self, *_args) -> None:
        self._on_purge()

    def _quit(self, *_args) -> None:
        self._on_quit()

    def stop(self) -> None:
        try:
            if self._icon:
                self._icon.stop()
        except Exception:
            pass
