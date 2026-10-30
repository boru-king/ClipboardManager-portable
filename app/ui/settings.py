"""设置窗口：自定义热键、容量、图片压缩、数据目录。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import messagebox

from . import theme
from .main_window import human_size
from ..hotkey import VK_MAP, describe

MOD_KEYSYMS = {
    "Control_L": "ctrl", "Control_R": "ctrl",
    "Alt_L": "alt", "Alt_R": "alt",
    "Shift_L": "shift", "Shift_R": "shift",
    "Win_L": "win", "Win_R": "win",
}
MOD_ORDER = {"ctrl": 0, "alt": 1, "shift": 2, "win": 3}


class SettingsWindow:
    def __init__(self, app) -> None:
        self.app = app
        self.cfg = app.config
        self._recording = False
        self._rec_mods: set[str] = set()
        self._pending_hotkey = dict(self.cfg.hotkey)
        self._hotkey_changed = False

        c = theme.COLORS
        fam = app.window._ui_family
        self.root = tk.Toplevel(app.window.root)
        self.root.title("设置")
        self.root.configure(bg=c["bg"])
        self.root.geometry(self._win_size())
        self.root.resizable(False, False)
        self.root.transient(app.window.root)
        self.root.grab_set()

        wrap = tk.Frame(self.root, bg=c["bg"])
        wrap.pack(fill="both", expand=True, padx=22, pady=(12, 0))

        # ---- 快捷键 ----
        self._section(wrap, "快捷键")
        row = tk.Frame(wrap, bg=c["bg"])
        row.pack(fill="x", pady=(4, 0))
        self.hotkey_label = tk.Label(
            row, text=self._hotkey_text(), bg=c["card"], fg=c["text"], font=(fam, 11),
            anchor="w", padx=12, pady=7, highlightbackground=c["border"], highlightthickness=1,
        )
        self.hotkey_label.pack(side="left", fill="x", expand=True)
        self.hotkey_btn = tk.Label(
            row, text="录制", bg=c["accent_soft"], fg=c["accent"], font=(fam, 10),
            padx=14, pady=8, cursor="hand2",
        )
        self.hotkey_btn.pack(side="left", padx=(8, 0))
        self.hotkey_btn.bind("<Button-1>", lambda e: self._begin_record())
        self.hint = tk.Label(
            wrap, text="点击「录制」后按下组合键，Esc 取消", bg=c["bg"], fg=c["faint"], font=(fam, 9),
            anchor="w",
        )
        self.hint.pack(fill="x", pady=(4, 0))

        # ---- 记录 ----
        self._section(wrap, "记录")
        self.max_items = self._field_row(
            wrap,
            "最多保留条数",
            lambda p: tk.Spinbox(
                p, from_=100, to=100000, increment=100, font=(fam, 10), bd=1,
                relief="solid", highlightthickness=0, width=10, justify="right",
            ),
            "置顶条目不受限制",
        )
        self.max_items.delete(0, "end")
        self.max_items.insert(0, str(self.cfg.get("max_items")))

        self.click_to_paste = tk.BooleanVar(value=bool(self.cfg.get("click_to_paste", True)))
        chk2 = tk.Checkbutton(
            wrap, text="单击条目即复制（关掉则单击只选中）", variable=self.click_to_paste,
            bg=c["bg"], fg=c["text"], font=(fam, 10), activebackground=c["bg"],
            selectcolor=c["card"], bd=0, highlightthickness=0, anchor="w",
        )
        chk2.pack(fill="x", pady=(8, 0))

        self.close_after_pick = tk.BooleanVar(value=bool(self.cfg.get("close_after_pick", False)))
        chk = tk.Checkbutton(
            wrap, text="选择后关窗并自动粘贴（默认关）",
            variable=self.close_after_pick,
            bg=c["bg"], fg=c["text"], font=(fam, 10), activebackground=c["bg"],
            selectcolor=c["card"], bd=0, highlightthickness=0, anchor="w",
        )
        chk.pack(fill="x", pady=(2, 0))

        # ---- 图片 ----
        self._section(wrap, "图片")
        qrow = tk.Frame(wrap, bg=c["bg"])
        qrow.pack(fill="x", pady=(5, 0))
        tk.Label(qrow, text="WebP 质量", bg=c["bg"], fg=c["text"], font=(fam, 10),
                 width=12, anchor="w").pack(side="left")
        quality0 = int(self.cfg.get("image_quality"))
        self.quality = tk.Scale(
            qrow, from_=40, to=100, orient="horizontal", bg=c["bg"], fg=c["text"],
            highlightthickness=0, bd=0, font=(fam, 9), length=170, width=10,
            troughcolor=c["border"], activebackground=c["accent"], sliderlength=16,
            showvalue=False,
        )
        self.quality.pack(side="left")
        self.quality_val = tk.Label(qrow, text=str(quality0), bg=c["bg"], fg=c["text"],
                                    font=(fam, 10), width=3, anchor="w")
        self.quality_val.pack(side="left", padx=(8, 0))
        tk.Label(qrow, text="越小越省空间", bg=c["bg"], fg=c["faint"], font=(fam, 8),
                 anchor="w").pack(side="left", padx=(4, 0))
        self.quality.set(quality0)
        self.quality.configure(
            command=lambda v: self.quality_val.configure(text=str(int(float(v))))
        )

        self.max_edge = self._field_row(
            wrap,
            "最大边长",
            lambda p: tk.Spinbox(
                p, from_=480, to=8192, increment=160, font=(fam, 10), bd=1,
                relief="solid", highlightthickness=0, width=10, justify="right",
            ),
            "像素，超出等比缩小",
        )
        self.max_edge.delete(0, "end")
        self.max_edge.insert(0, str(self.cfg.get("max_image_edge")))

        # ---- 数据 ----
        self._section(wrap, "数据")
        self.stats_label = tk.Label(
            wrap, text=self._stats_text(), bg=c["bg"], fg=c["subtext"],
            font=(fam, 9), anchor="w",
        )
        self.stats_label.pack(fill="x", pady=(2, 2))
        dir_row = tk.Frame(wrap, bg=c["bg"])
        dir_row.pack(fill="x", pady=(4, 0))
        open_btn = tk.Label(dir_row, text="打开", bg=c["accent_soft"], fg=c["accent"],
                            font=(fam, 9), padx=10, pady=4, cursor="hand2")
        open_btn.pack(side="right")
        open_btn.bind("<Button-1>", lambda e: os.startfile(str(self.app.data_dir)))
        self.dir_label = tk.Label(dir_row, text="", bg=c["bg"], fg=c["subtext"],
                                  font=(fam, 9), anchor="w")
        self.dir_label.pack(side="left", fill="x", expand=True)
        self.root.update_idletasks()
        self.dir_label.configure(
            text=self._elide(str(self.app.data_dir), (fam, 9), self.dir_label.winfo_width() - 8)
        )

        danger = tk.Label(
            wrap, text="清空全部记录（保留置顶）", bg=c["bg"], fg=c["danger"],
            font=(fam, 9), anchor="w", cursor="hand2",
        )
        danger.pack(fill="x", pady=(10, 0))
        danger.bind("<Button-1>", lambda e: self._clear_all())

        wipe = tk.Label(
            wrap, text="彻底清除所有数据并退出（只删数据，不卸载程序）", bg=c["bg"],
            fg=c["danger"], font=(fam, 9), anchor="w", cursor="hand2",
        )
        wipe.pack(fill="x", pady=(4, 0))
        wipe.bind("<Button-1>", lambda e: self._purge_all())

        # ---- 按钮 ----
        btns = tk.Frame(self.root, bg=c["bg"])
        btns.pack(fill="x", padx=22, pady=(0, 18))
        save = tk.Label(btns, text="保存", bg=c["accent"], fg="#FFFFFF", font=(fam, 10),
                        padx=26, pady=8, cursor="hand2")
        save.pack(side="right")
        save.bind("<Button-1>", lambda e: self._save())
        cancel = tk.Label(btns, text="取消", bg=c["bg"], fg=c["subtext"], font=(fam, 10),
                          padx=20, pady=8, cursor="hand2", highlightbackground=c["border"], highlightthickness=1)
        cancel.pack(side="right", padx=(0, 10))
        cancel.bind("<Button-1>", lambda e: self._close())

        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.bind("<Escape>", lambda e: self._cancel_record() if self._recording else self._close())

    # ------------------------------------------------------------ 小组件

    def _section(self, parent, title: str) -> None:
        fam = self.app.window._ui_family
        frame = tk.Frame(parent, bg=theme.COLORS["bg"])
        frame.pack(fill="x", pady=(14, 2))
        tk.Label(frame, text=title, bg=theme.COLORS["bg"], fg=theme.COLORS["subtext"],
                 font=(fam, 9, "bold"), anchor="w").pack(fill="x")

    def _field_row(self, parent, label: str, factory, hint: str = ""):
        fam = self.app.window._ui_family
        c = theme.COLORS
        row = tk.Frame(parent, bg=c["bg"])
        row.pack(fill="x", pady=(5, 0))
        tk.Label(row, text=label, bg=c["bg"], fg=c["text"], font=(fam, 10), width=12,
                 anchor="w").pack(side="left")
        widget = factory(row)
        widget.pack(side="left")
        if hint:
            tk.Label(row, text=hint, bg=c["bg"], fg=c["faint"], font=(fam, 8),
                     anchor="w").pack(side="left", padx=(8, 0))
        return widget

    def _win_size(self) -> str:
        """窗口尺寸跟着 DPI 走，避免高缩放下内容溢出。"""
        s = float(getattr(self.app, "scale", 1.0) or 1.0)
        return f"{int(480 * s)}x{int(634 * s)}"

    def _stats_text(self) -> str:
        try:
            count = self.app.store.count(None)
            size = self.app.store.total_size()
            return f"当前 {count} 条记录 · 占用 {human_size(size)}"
        except Exception as exc:
            return f"统计不可用：{exc}"

    def _elide(self, text: str, font_spec, max_px: int) -> str:
        from tkinter import font as tkfont

        if max_px <= 20:
            return text
        f = tkfont.Font(family=font_spec[0], size=font_spec[1])
        if f.measure(text) <= max_px:
            return text
        while text and f.measure(text + "…") > max_px:
            text = text[:-1]
        return text + "…"

    # ------------------------------------------------------------ 热键录制

    def _hotkey_text(self) -> str:
        return describe(self._pending_hotkey["mods"], self._pending_hotkey["key"])

    def _begin_record(self) -> None:
        self._recording = True
        self._rec_mods = set()
        self.hotkey_btn.configure(text="录制中…")
        self.hotkey_label.configure(text="请按下组合键", fg=theme.COLORS["accent"])
        self.root.bind("<KeyPress>", self._on_record_key)
        self.root.focus_set()

    def _end_record(self) -> None:
        self._recording = False
        self.hotkey_btn.configure(text="录制")
        self.hotkey_label.configure(text=self._hotkey_text(), fg=theme.COLORS["text"])
        try:
            self.root.unbind("<KeyPress>")
        except Exception:
            pass

    def _cancel_record(self) -> None:
        if not self._recording:
            return
        self._end_record()
        self.hint.configure(text="已取消，原快捷键保持不变")

    def _on_record_key(self, event) -> str:
        ks = event.keysym
        if ks == "Escape":
            self._cancel_record()
            return "break"
        if ks in MOD_KEYSYMS:
            self._rec_mods.add(MOD_KEYSYMS[ks])
            self.hotkey_label.configure(text=self._preview_text(), fg=theme.COLORS["accent"])
            return "break"
        key = ks.lower()
        if key not in VK_MAP or not self._rec_mods:
            self.hint.configure(text="需要至少一个 Ctrl / Alt / Shift 加字符键或 F1-F12")
            return "break"
        mods = sorted(self._rec_mods, key=lambda m: MOD_ORDER.get(m, 9))
        self._pending_hotkey = {"mods": mods, "key": key}
        self._hotkey_changed = True
        self._end_record()
        self.hint.configure(text="保存后立即生效")
        return "break"

    def _preview_text(self) -> str:
        names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
        return "+".join(names[m] for m in sorted(self._rec_mods, key=lambda m: MOD_ORDER.get(m, 9))) + "+…"

    # ------------------------------------------------------------ 保存

    def _save(self) -> None:
        if self._recording:
            self._cancel_record()
        try:
            max_items = max(50, int(self.max_items.get()))
            max_edge = max(320, int(self.max_edge.get()))
        except ValueError:
            messagebox.showerror("设置", "数值格式不正确", parent=self.root)
            return

        old_hotkey = dict(self.cfg.hotkey)
        self.cfg.set("max_items", max_items)
        self.cfg.set("image_quality", int(self.quality.get()))
        self.cfg.set("max_image_edge", max_edge)
        self.cfg.set("click_to_paste", bool(self.click_to_paste.get()))
        self.cfg.set("close_after_pick", bool(self.close_after_pick.get()))
        self.cfg.set("hotkey", self._pending_hotkey)
        self.cfg.save()

        if self._hotkey_changed and self._pending_hotkey != old_hotkey:
            ok, error = self.app.apply_hotkey(self._pending_hotkey)
            if not ok:
                self.cfg.set("hotkey", old_hotkey)
                self.cfg.save()
                self.app.apply_hotkey(old_hotkey)
                messagebox.showerror("快捷键设置失败", error, parent=self.root)
                return
        self._close()

    def _clear_all(self) -> None:
        if messagebox.askyesno("确认", "清空全部记录？此操作不可撤销（置顶条目保留）。", parent=self.root):
            self.app.clear_items(None)
            self.app.window.refresh()
            self.stats_label.configure(text=self._stats_text())

    def _purge_all(self) -> None:
        # 确认框和实际清除都在 app 里，托盘菜单调用的是同一套流程
        self.app._confirm_purge()

    def _close(self) -> None:
        if self._recording:
            self._end_record()
        try:
            self.root.grab_release()
        except Exception:
            pass
        self.root.destroy()
