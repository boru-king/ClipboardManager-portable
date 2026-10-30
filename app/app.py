"""装配层：把配置、存储、监听、热键、托盘、窗口串起来。"""

from __future__ import annotations

import ctypes
import datetime
import hashlib
import json
import os
import queue
import shutil
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox

from PIL import Image

from . import APP_NAME, APP_TITLE, __version__
from . import config as config_mod
from . import images, paste, winclip
from .db import Store, item_hash
from .hotkey import HotkeyManager
from .tray import TrayIcon
from .ui import main_window as mw_module
from .ui.main_window import MainWindow, one_line
from .ui.settings import SettingsWindow
from .watcher import ClipboardWatcher, classify_files

MUTEX_NAME = "Local\\ClipboardManager.ControlPanel"

_LOG_PATH: Path | None = None


def set_log_path(path: Path) -> None:
    global _LOG_PATH
    _LOG_PATH = path


def log_text(msg: str) -> None:
    """把一行信息写进数据目录的 error.log（noconsole 下没有控制台可看）。"""
    try:
        if _LOG_PATH is None:
            return
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        if _LOG_PATH.exists() and _LOG_PATH.stat().st_size > 512 * 1024:
            _LOG_PATH.write_text("", encoding="utf-8")
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with _LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"[{stamp}] {msg}\n")
    except Exception:
        pass


def log_exc() -> None:
    """noconsole 模式下 sys.stderr 可能是 None，打印不能抛异常。"""
    try:
        traceback.print_exc()
    except Exception:
        pass
    log_text(traceback.format_exc(limit=12).strip())


# ---------------------------------------------------------------- 进程级准备

def acquire_single_instance() -> Any:
    """命名互斥体。返回句柄；None 表示已有实例在跑。"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
    if not handle:
        return handle
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        return None
    return handle


def enable_dpi_awareness() -> float:
    """开启 DPI 感知并返回缩放倍数（1.0 = 96dpi）。"""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            return 1.0
    try:
        dc = ctypes.windll.user32.GetDC(None)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(ctypes.c_void_p(dc), 88)
        ctypes.windll.user32.ReleaseDC(None, ctypes.c_void_p(dc))
        return max(1.0, min(3.0, dpi / 96.0))
    except Exception:
        return 1.0


def _parse_paths(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(data, list):
        return []
    return [str(p) for p in data if p]


_DATA_FILES = (
    "clipboard.db", "clipboard.db-wal", "clipboard.db-shm", "clipboard.db-journal",
    "config.json", "error.log",
)
_DATA_DIRS = ("images", "thumbs")


def delete_data_dir(data_dir: Path, remove_root: bool = False) -> list[str]:
    """删掉数据目录里的全部内容（记录、图片、缩略图、设置、日志）。

    remove_root=True 时连目录本身一起删（会先确认它确实是本程序的数据目录，
    便携模式下 data\\ 被删也不会动到 exe）。返回没能删掉的项。
    """
    errs: list[str] = []
    d = Path(data_dir)
    if not d.exists():
        return errs
    looks_ours = d.name.lower() in ("clipboardmanager", "data") or any(
        (d / name).exists() for name in _DATA_FILES + _DATA_DIRS
    )
    for name in _DATA_DIRS:
        sub = d / name
        if sub.exists():
            try:
                shutil.rmtree(sub)
            except OSError as exc:
                errs.append(f"{name}: {exc}")
    for name in _DATA_FILES:
        f = d / name
        try:
            f.unlink(missing_ok=True)
        except OSError as exc:
            errs.append(f"{name}: {exc}")
    if remove_root and looks_ours:
        try:
            d.rmdir()  # 只删空目录；还有别的东西就留着
        except OSError:
            pass
    return errs


# ---------------------------------------------------------------- 应用

class ClipboardApp:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir else config_mod.data_dir()
        self.image_dir = self.data_dir / "images"
        self.thumb_dir = self.data_dir / "thumbs"
        self.fallback_dir = ""
        try:
            for d in (self.data_dir, self.image_dir, self.thumb_dir):
                d.mkdir(parents=True, exist_ok=True)
        except OSError:
            # 数据目录不可写（被策略限制 / 只读盘 / 沙箱）时依次退：
            # 1) %LOCALAPPDATA%\ClipboardManager（还在正常位置）
            # 2) 临时目录
            # 不往程序目录里塞数据，免得和打包产物混在一起、还把文件锁住
            self.fallback_dir = str(self.data_dir)
            local = os.environ.get("LOCALAPPDATA")
            candidates = []
            if local:
                candidates.append(Path(local) / APP_NAME)
            candidates.append(Path(tempfile.gettempdir()) / APP_NAME)
            for candidate in candidates:
                try:
                    (candidate / "images").mkdir(parents=True, exist_ok=True)
                    (candidate / "thumbs").mkdir(parents=True, exist_ok=True)
                except OSError:
                    continue
                self.data_dir = candidate
                self.image_dir = candidate / "images"
                self.thumb_dir = candidate / "thumbs"
                break
            else:
                raise
        set_log_path(self.data_dir / "error.log")
        if self.fallback_dir:
            log_text(f"数据目录不可写：{self.fallback_dir}，改用 {self.data_dir}")

        self.config = config_mod.Config(self.data_dir / "config.json")
        self.first_run = not (self.data_dir / "config.json").exists()
        self.store = Store(self.data_dir / "clipboard.db")

        self.queue: "queue.Queue[tuple[str, Any]]" = queue.Queue()
        self.window: MainWindow | None = None
        self.watcher: ClipboardWatcher | None = None
        self.hotkey: HotkeyManager | None = None
        self.tray: TrayIcon | None = None
        self._settings_win: SettingsWindow | None = None
        self._toast_win: tk.Toplevel | None = None
        self._quitting = False
        self._purged = False

    # ------------------------------------------------------------ 启动

    def run(self) -> None:
        self.scale = enable_dpi_awareness()
        self.window = MainWindow(self)
        self._apply_window_scale()
        self._pump()
        self._start_watcher()
        self._start_hotkey()
        self._start_tray()
        self.config.save()
        hk = self.config.hotkey
        log_text(
            f"启动 v{__version__} 数据={self.data_dir} 缩放={self.scale:.2f} "
            f"热键={hk['mods']}+{hk['key']} 记录={self.store.count()} 首次={self.first_run} "
            f"APPDATA={os.environ.get('APPDATA')} TEMP={os.environ.get('TEMP')}"
        )
        if self.fallback_dir:
            log_text(f"受限环境：{self.fallback_dir} 不可写，托盘图标和自动粘贴可能不可用")
        if self.first_run or not self.config.get("start_minimized", True):
            self.window.show()
        self.window.root.after(800, self._after_start)
        self.window.root.mainloop()

    def _after_start(self) -> None:
        """启动后自检托盘，并在第一次运行时告诉用户图标藏在哪。"""
        if self.tray is None:
            return
        if not self.tray.is_alive():
            log_text(f"托盘线程没起来：{self.tray.error}")
            self.window.show()
            self.toast("托盘没起来（安全软件拦截？），先用热键唤出窗口")
            return
        log_text("托盘图标已创建")
        if self.fallback_dir:
            # 受限令牌下托盘和按键注入都会被系统拦掉，必须让用户看见
            self.window.show()
            if not self.config.get("restricted_tip_shown", False):
                self.config.set("restricted_tip_shown", True)
                self.config.save()
                exe_dir = config_mod.exe_root()
                messagebox.showwarning(
                    "启动方式不对：程序跑在受限环境里",
                    "数据写不进 %APPDATA%（只能落在：\n"
                    f"{self.data_dir}\n）\n\n"
                    "这是从聊天窗口 / IDE 的“文件卡片”之类的地方点开才会出现的现象：\n"
                    "托盘图标不会显示，自动粘贴也会被系统拦掉。\n\n"
                    "正确的打开方式：\n"
                    "1. 按 Win+E 打开资源管理器\n"
                    "2. 地址栏粘贴下面这行并回车\n"
                    f"{exe_dir}\n"
                    "3. 双击 ClipboardManager.exe\n",
                    parent=self.window.root,
                )
            return
        if not self.config.get("tray_tip_shown", False):
            shown = self.tray.notify(
                "已启动：图标在任务栏右下角的通知区域（可能收在 ^ 里，可拖出来固定）。"
                "按 Ctrl+Alt+V 能随时唤出窗口。",
                "剪贴板管理器已就绪",
            )
            if shown:
                self.config.set("tray_tip_shown", True)
                self.config.save()

    def _apply_window_scale(self) -> None:
        """按 DPI 放大画布行高与窗口尺寸（Tk 的字体点数会自己缩放）。"""
        s = getattr(self, "scale", 1.0)
        if s <= 1.01:
            return
        for name, base in (("ROW_H", 78), ("THUMB", 52), ("PAD_LEFT", 18)):
            try:
                setattr(mw_module, name, int(base * s))
            except Exception:
                pass
        root = self.window.root
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w = min(int(1000 * s), int(sw * 0.9))
        h = min(int(660 * s), int(sh * 0.86))
        root.geometry(f"{w}x{h}")
        root.minsize(int(760 * s * 0.85), int(460 * s * 0.85))

    def _start_watcher(self) -> None:
        interval = max(0.1, float(self.config.get("poll_interval_ms", 300)) / 1000.0)
        self.watcher = ClipboardWatcher(self._enqueue_clip, interval=interval)
        self.watcher.start()

    def _start_hotkey(self) -> None:
        self.hotkey = HotkeyManager(lambda: self.queue.put(("hotkey", None)))
        hk = self.config.hotkey
        if not self.hotkey.start(hk["mods"], hk["key"]):
            log_text(f"热键注册失败：{self.hotkey.error}")
            self.queue.put(("toast", f"全局快捷键不可用：{self.hotkey.error}"))

    def _start_tray(self) -> None:
        self.tray = TrayIcon(
            on_show=lambda: self.queue.put(("show", None)),
            on_quit=lambda: self.queue.put(("quit", None)),
            on_settings=lambda: self.queue.put(("settings", None)),
            on_purge=lambda: self.queue.put(("purge", None)),
        )
        if not self.tray.start():
            log_text(f"托盘启动失败：{self.tray.error}")
            self.queue.put(("toast", self.tray.error))

    # ------------------------------------------------------------ 消息泵

    def _pump(self) -> None:
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                try:
                    self._dispatch(kind, payload)
                except Exception:
                    log_exc()
        except queue.Empty:
            pass
        if not self._quitting and self.window is not None:
            self.window.root.after(70, self._pump)

    def _dispatch(self, kind: str, payload: Any) -> None:
        if kind == "clip":
            self._ingest(payload)
        elif kind == "show":
            self.window.show()
        elif kind == "hotkey":
            self.window.toggle()
        elif kind == "settings":
            self.open_settings()
        elif kind == "purge":
            self._confirm_purge()
        elif kind == "paste":
            self._do_paste(*payload)
        elif kind == "quit":
            self.quit()
        elif kind == "toast":
            self.toast(str(payload))
        elif kind == "added":
            if payload:
                self._post_add()
            else:
                self.window.notify_new_item()
        elif kind == "used":
            pass

    # ------------------------------------------------------------ 采集

    def _enqueue_clip(self, content) -> None:
        """由监听线程调用，只入队，不碰 Tk。"""
        self.queue.put(("clip", content))

    def _ingest(self, content) -> None:
        kind = getattr(content, "kind", "")
        if kind == "text":
            self._ingest_text(content)
        elif kind == "files":
            self._ingest_files(content)
        elif kind == "image":
            if content.png or content.dib:
                threading.Thread(
                    target=self._ingest_image, args=(content,), name="image-ingest", daemon=True
                ).start()

    def _ingest_text(self, content) -> None:
        text = content.text or ""
        if not text.strip():
            return
        limit = int(self.config.get("max_text_chars", 2_000_000))
        if len(text) > limit:
            text = text[:limit]
        h = item_hash("text", text)
        _, is_new = self.store.add(
            kind="text",
            source="html" if content.html else "plain",
            content=text,
            preview=one_line(text, 200),
            html=content.html or "",
            hash=h,
        )
        self._after_store(is_new)

    def _ingest_files(self, content) -> None:
        paths = [p for p in (content.paths or []) if p]
        if not paths:
            return
        kind = classify_files(paths)
        if kind not in ("image", "video", "file"):
            kind = "file"
        h = item_hash(kind, "\n".join(paths))
        total = 0
        for p in paths:
            try:
                total += os.path.getsize(p)
            except OSError:
                pass
        names = [os.path.basename(p) for p in paths]
        if len(names) == 1:
            preview = names[0]
        elif len(names) <= 3:
            preview = "、".join(names)
        else:
            preview = f"{names[0]} 等 {len(names)} 个文件"
        _, is_new = self.store.add(
            kind=kind,
            source="path",
            content="",
            preview=preview,
            paths=paths,
            size_bytes=total,
            hash=h,
        )
        self._after_store(is_new)

    def _ingest_image(self, content) -> None:
        """在后台线程跑：解码 → 压缩 → 落盘 → 入库。"""
        try:
            if content.png:
                img = images.png_to_image(content.png)
            else:
                img = images.dib_to_image(content.dib)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")
            max_edge = int(self.config.get("max_image_edge", 2560))
            img = images.fit_within(img, max_edge)
        except Exception as exc:
            self.queue.put(("toast", f"图片无法保存：{exc}"))
            return

        try:
            digest = hashlib.sha1()
            digest.update(f"{img.size[0]}x{img.size[1]}".encode())
            digest.update(img.tobytes())
            h = item_hash("image", digest.hexdigest())
            name = f"{h[:20]}.webp"
            fpath = self.image_dir / name
            tpath = self.thumb_dir / name
            quality = int(self.config.get("image_quality", 85))
            size = images.save_webp(img, fpath, quality)
            images.make_thumb(img, tpath, 128)
            _, is_new = self.store.add(
                kind="image",
                source="bitmap",
                preview=f"图片 {img.size[0]}×{img.size[1]}",
                file_path=str(fpath),
                thumb_path=str(tpath),
                width=img.size[0],
                height=img.size[1],
                size_bytes=size,
                hash=h,
            )
            self.queue.put(("added", is_new))
        except Exception as exc:
            self.queue.put(("toast", f"图片无法保存：{exc}"))

    def _after_store(self, is_new: bool) -> None:
        if is_new:
            self._post_add()
        elif self.window is not None:
            self.window.notify_new_item()

    # ------------------------------------------------------------ 维护

    def _post_add(self) -> None:
        removed = self.store.purge(int(self.config.get("max_items", 3000)))
        self._remove_files(removed)
        if self.window is not None:
            self.window.notify_new_item()

    @staticmethod
    def _remove_files(paths) -> None:
        for p in paths or []:
            try:
                Path(p).unlink(missing_ok=True)
            except OSError:
                pass

    # ------------------------------------------------------------ 窗口接口

    def query_items(self, kind: str, search: str, limit: int = 1000):
        return self.store.query(kind=None if kind == "all" else kind, search=search, limit=limit)

    def kind_counts(self) -> dict[str, int]:
        counts = self.store.kind_counts()
        counts["all"] = sum(counts.values())
        return counts

    def toggle_pin(self, item_id: int) -> None:
        self.store.toggle_pin(item_id)
        self.window.notify_new_item()

    def delete_item(self, item_id: int) -> None:
        self._remove_files(self.store.delete(item_id))
        self.window.refresh()

    def clear_items(self, kind: str | None) -> None:
        self._remove_files(self.store.clear(kind))
        self.window.refresh()

    def _confirm_purge(self) -> None:
        """托盘菜单与设置页共用的二次确认。只删数据，不会删掉 exe 本身。"""
        parent = self.window.root if self.window is not None else None
        if not messagebox.askyesno(
            "清除全部数据并退出",
            f"将删除这个目录里的全部内容：\n\n{self.data_dir}\n\n"
            "· 全部记录（含置顶）\n"
            "· 图片与缩略图文件\n"
            "· 设置项\n"
            "· 错误日志\n\n"
            "这不会卸载程序：ClipboardManager.exe 还在原处，随时可以再打开（记录是空的）。\n"
            "删除后无法恢复，程序会立即退出。确定继续吗？",
            icon="warning", parent=parent,
        ):
            return
        ok, error = self.purge_data()
        if not ok and parent is not None:
            messagebox.showerror("清除未完成", f"以下内容没能删除：\n\n{error}", parent=parent)

    def purge_data(self) -> tuple[bool, str]:
        """彻底清除：所有记录（含置顶）、图片与缩略图、设置、日志，然后退出程序。"""
        try:
            if self.watcher is not None:
                self.watcher.stop()
        except Exception:
            pass
        errors: list[str] = []
        try:
            self._remove_files(self.store.wipe())
        except Exception as exc:
            errors.append(f"清空数据库失败：{exc}")
        try:
            self.store.close()
        except Exception:
            pass
        self._purged = True
        errors.extend(delete_data_dir(self.data_dir, remove_root=True))
        self.quit()
        return (not errors, "；".join(errors))

    def open_settings(self) -> None:
        if self._settings_win is not None:
            try:
                if self._settings_win.root.winfo_exists():
                    self._settings_win.root.lift()
                    self._settings_win.root.focus_force()
                    return
            except Exception:
                pass
            self._settings_win = None
        self.window.show()
        self._settings_win = SettingsWindow(self)

    def apply_hotkey(self, hotkey: dict) -> tuple[bool, str]:
        if self.hotkey is None:
            return False, "热键模块未启动"
        mods = [str(m) for m in (hotkey.get("mods") or [])]
        key = str(hotkey.get("key") or "")
        if self.hotkey.start(mods, key):
            return True, ""
        return False, self.hotkey.error or "注册失败"

    # ------------------------------------------------------------ 使用 / 粘贴

    def use_item(self, row, paste: bool = True, target_hwnd: int = 0, close: bool | None = None) -> None:
        """把一条内容写回剪贴板。

        close=None 时看配置 close_after_pick：
          False（默认）复制完窗口留着，让用户自己切回去按 Ctrl+V；
          True 复制完关窗并自动粘贴到刚才那个窗口。
        """
        if row is None:
            return
        if close is None:
            close = bool(self.config.get("close_after_pick", False))
        auto = paste and close and bool(self.config.get("auto_paste", True)) and bool(target_hwnd)
        if close and self.window is not None:
            self.window.hide()
        threading.Thread(
            target=self._write_and_paste,
            args=(dict(row), auto, target_hwnd, paste),
            name="paste-worker",
            daemon=True,
        ).start()

    def _write_and_paste(self, row: dict, auto: bool, target_hwnd: int, paste: bool) -> None:
        if self.watcher is not None:
            self.watcher.ignore_self_writes()
        try:
            self._write_row(row)
        except Exception as exc:
            self.queue.put(("toast", f"写入剪贴板失败：{exc}"))
            return
        try:
            self.store.touch(int(row["id"]))
        except Exception:
            pass
        if auto and target_hwnd:
            # 抢焦点 / 发按键放到 Tk 主线程做：主线程有消息队列，AttachThreadInput
            # 才有意义；后台线程调 SetForegroundWindow 经常被前台锁吃掉。
            self.queue.put(("paste", (target_hwnd, int(self.config.get("paste_delay_ms", 260)))))
        elif paste:
            preview = one_line(str(row.get("preview") or row.get("content") or ""), 40)
            self.queue.put(("toast", f"已复制：{preview}" if preview else "已复制到剪贴板"))

    def _do_paste(self, hwnd: int, delay_ms: int) -> None:
        if self.window is None:
            return
        self.window.root.after(
            max(0, delay_ms - 60),
            lambda: self._run_paste(hwnd),
        )

    def _run_paste(self, hwnd: int) -> None:
        ok = paste.paste_into(hwnd, delay_ms=60, retries=3)
        log_text(f"自动粘贴 hwnd={hwnd} 结果={ok} 前台={paste.foreground_window()}")
        if not ok:
            self.toast("内容已复制到剪贴板，但没能自动粘进去，按 Ctrl+V 即可")

    def _write_row(self, row: dict) -> None:
        kind = row["kind"]
        if kind == "text":
            winclip.write_text(row["content"] or "", row["html"] or "")
            return
        if kind == "image":
            if row["file_path"]:
                path = Path(row["file_path"])
                if not path.exists():
                    raise RuntimeError("图片文件已丢失")
                with Image.open(path) as img:
                    img.load()
                    dib = images.image_to_dib(img)
                    png = images.encode_png(img) if images.has_alpha(img) else b""
                winclip.write_image(png, dib)
                return
            paths = [p for p in _parse_paths(row["paths"]) if os.path.exists(p)]
            if paths:
                winclip.write_files(paths)
                return
            raise RuntimeError("图片数据缺失")
        paths = [p for p in _parse_paths(row["paths"]) if os.path.exists(p)]
        if not paths:
            raise RuntimeError("原文件已不存在，无法粘贴")
        winclip.write_files(paths)

    # ------------------------------------------------------------ 提示 / 退出

    def toast(self, text: str) -> None:
        root = self.window.root
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
            self._toast_win = None
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.96)
        except Exception:
            pass
        frame = tk.Frame(win, bg="#2B2F38", padx=16, pady=11)
        frame.pack()
        tk.Label(
            frame, text=text, bg="#2B2F38", fg="#F2F3F5",
            font=(self.window._ui_family, 9), wraplength=360, justify="left",
        ).pack()
        win.update_idletasks()
        w, h = win.winfo_reqwidth(), win.winfo_reqheight()
        x = root.winfo_screenwidth() - w - 28
        y = root.winfo_screenheight() - h - 76
        win.geometry(f"+{x}+{y}")
        self._toast_win = win
        win.after(2600, lambda: self._close_toast(win))

    def _close_toast(self, win) -> None:
        try:
            win.destroy()
        except Exception:
            pass
        if self._toast_win is win:
            self._toast_win = None

    def quit(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        for part in (self.watcher, self.hotkey, self.tray):
            try:
                if part is not None:
                    part.stop()
            except Exception:
                pass
        try:
            self.store.close()
        except Exception:
            pass
        if not self._purged:
            try:
                self.config.save()
            except Exception:
                pass
        if self.window is not None:
            try:
                self.window.root.after(60, self.window.root.destroy)
            except Exception:
                pass


def purge_installation() -> int:
    """命令行 --purge：删掉数据目录里的全部内容（卸载前用）。"""
    handle = acquire_single_instance()
    if handle is None:
        ctypes.windll.user32.MessageBoxW(
            None,
            f"{APP_TITLE} 正在运行，请先从托盘菜单退出，再执行清除。",
            APP_TITLE, 0x30,
        )
        return 1
    target = config_mod.data_dir()
    if not target.exists():
        ctypes.windll.user32.MessageBoxW(
            None, f"没有找到数据目录：\n{target}\n\n已经没有什么需要清理了。", APP_TITLE, 0x40
        )
        return 0
    ret = ctypes.windll.user32.MessageBoxW(
        None,
        "将删除以下目录中的全部内容：\n\n"
        f"{target}\n\n"
        "记录、图片、缩略图、设置、日志都会被删除，无法恢复。\n\n确定继续吗？",
        f"{APP_TITLE} · 清除数据", 0x31,  # MB_OKCANCEL | MB_ICONWARNING
    )
    if ret != 1:  # IDOK
        return 0
    errors = delete_data_dir(target, remove_root=True)
    if errors:
        ctypes.windll.user32.MessageBoxW(
            None, "以下内容没能删除：\n\n" + "\n".join(errors[:8]), APP_TITLE, 0x10
        )
        return 1
    ctypes.windll.user32.MessageBoxW(
        None,
        f"已清除：\n{target}\n\n如果不再使用，直接删掉 ClipboardManager.exe 就完成卸载了。",
        APP_TITLE, 0x40,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if any(str(a).lower() in ("--purge", "--uninstall", "/purge") for a in args):
        return purge_installation()
    handle = acquire_single_instance()
    if handle is None:
        ctypes.windll.user32.MessageBoxW(
            None, f"{APP_TITLE} 已经在运行了。\n请用托盘图标或快捷键唤出窗口。", APP_TITLE, 0x40
        )
        return 0
    try:
        app = ClipboardApp()
    except Exception:
        log_exc()
        ctypes.windll.user32.MessageBoxW(
            None, traceback.format_exc(limit=4), f"{APP_TITLE} 启动失败", 0x10
        )
        return 1
    try:
        app.run()
    except Exception:
        log_exc()
        try:
            ctypes.windll.user32.MessageBoxW(
                None, traceback.format_exc(limit=4), f"{APP_TITLE} 启动失败", 0x10
            )
        except Exception:
            pass
        return 1
    return 0
