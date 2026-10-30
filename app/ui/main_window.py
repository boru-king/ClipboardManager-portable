"""主窗口：搜索框 + 类型切换 + 虚拟滚动列表。"""

from __future__ import annotations

import json
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

from PIL import Image

from . import theme
from .. import images as image_tools

ROW_H = 78
THUMB = 52
PAD_LEFT = 18


def format_ago(ts: float) -> str:
    now = time.time()
    delta = max(0.0, now - ts)
    if delta < 60:
        return "刚刚"
    if delta < 3600:
        return f"{int(delta // 60)} 分钟前"
    if delta < 86400:
        return f"{int(delta // 3600)} 小时前"
    lt = time.localtime(ts)
    if delta < 86400 * 2:
        return "昨天 " + time.strftime("%H:%M", lt)
    if delta < 86400 * 7:
        return time.strftime("%m-%d %H:%M", lt)
    return time.strftime("%Y-%m-%d", lt)


def human_size(num: int) -> str:
    if num <= 0:
        return "0 B"
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} GB"


def one_line(text: str, limit: int = 400) -> str:
    text = " ".join(text.replace("\r", "\n").split("\n"))
    text = " ".join(text.split())
    return text[:limit] + ("…" if len(text) > limit else "")


def item_preview(row) -> str:
    kind = row["kind"]
    if kind == "text":
        return one_line(row["preview"] or row["content"])
    if kind == "image" and row["width"] and row["height"]:
        return f"剪贴板图片 {row['width']}×{row['height']}"
    paths = []
    if row["paths"]:
        try:
            paths = json.loads(row["paths"])
        except ValueError:
            paths = []
    if not paths:
        return row["preview"] or "空文件引用"
    names = [Path(p).name for p in paths]
    if len(names) == 1:
        return names[0]
    return f"{names[0]} 等 {len(names)} 个文件"


def item_meta(row) -> str:
    kind = row["kind"]
    parts = []
    if kind == "text":
        parts.append("富文本" if row["html"] else "文字")
        parts.append(f"{len(row['content']):,} 字")
    elif kind == "image":
        parts.append("图片")
        if row["size_bytes"]:
            parts.append(human_size(int(row["size_bytes"])))
    elif kind == "video":
        parts.append("视频")
        if row["size_bytes"]:
            parts.append(human_size(int(row["size_bytes"])))
    else:
        parts.append("文件")
    parts.append(format_ago(row["used_at"]))
    if row["use_count"] > 1:
        parts.append(f"用 {row['use_count']} 次")
    return " · ".join(parts)


class MainWindow:
    def __init__(self, app) -> None:
        self.app = app
        theme.clear_icon_cache()  # 新 Tk 解释器，旧的 PhotoImage 已经失效
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.title("剪贴板管理器")
        self.root.configure(bg=theme.COLORS["bg"])
        self.root.minsize(760, 460)
        self.root.geometry("1000x660")

        self._ui_family = theme.pick_font_family(self.root)
        self.root._ui_family = self._ui_family  # 供 theme.font 使用
        try:
            self.root.iconphoto(True, theme.type_icon(self.root, "text", 64))
        except Exception:
            pass

        self.current_kind = "all"
        self.items: list = []
        self.selected_index = -1
        self._thumb_cache: dict[int, object] = {}
        self._search_job = None
        self._last_query = ""
        self._target_hwnd = 0
        self._visible = False
        self._chips: dict[str, tk.Label] = {}

        self._build()
        self._bind()
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.refresh()

    # ------------------------------------------------------------ 构建界面

    def _build(self) -> None:
        c = theme.COLORS
        fam = self._ui_family

        outer = tk.Frame(self.root, bg=c["bg"])
        outer.pack(fill="both", expand=True)

        # --- 顶栏 ---
        topbar = tk.Frame(outer, bg=c["bg"], height=66)
        topbar.pack(fill="x", side="top")
        topbar.pack_propagate(False)

        search_wrap = tk.Frame(
            topbar,
            bg=c["card"],
            highlightbackground=c["border"],
            highlightthickness=1,
            bd=0,
            height=42,
        )
        search_wrap.pack(side="left", fill="x", expand=True, padx=(18, 10), pady=12)
        search_wrap.pack_propagate(False)

        self._search_icon = tk.Label(search_wrap, image=self._make_search_icon(), bg=c["card"], bd=0)
        self._search_icon.pack(side="left", padx=(12, 6))

        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_wrap,
            textvariable=self.search_var,
            bd=0,
            relief="flat",
            bg=c["card"],
            fg=c["text"],
            insertbackground=c["accent"],
            highlightthickness=0,
            font=(fam, 11),
        )
        self.search_entry.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=(9, 8))
        self.search_entry.bind("<Escape>", lambda e: self._on_escape_in_search())

        self._ph = tk.Label(
            search_wrap,
            text="搜索文字、文件名…",
            bg=c["card"],
            fg=c["faint"],
            font=(fam, 10),
            bd=0,
        )
        self._ph.place(x=38, y=11)
        self._ph.bind("<Button-1>", lambda e: self.search_entry.focus_set())
        self._ph_visible = True

        self._clear_btn = tk.Label(
            search_wrap, text="×", bg=c["card"], fg=c["faint"], font=(fam, 14), bd=0, cursor="hand2"
        )
        self._clear_btn.bind("<Button-1>", lambda e: self._clear_search())

        self._settings_btn = tk.Label(
            topbar, text="设置", bg=c["bg"], fg=c["subtext"], font=(fam, 10), bd=0, cursor="hand2", padx=10
        )
        self._settings_btn.pack(side="right", padx=(0, 14))
        self._settings_btn.bind("<Button-1>", lambda e: self.app.open_settings())

        # --- 类型切换 ---
        tabbar = tk.Frame(outer, bg=c["bg"], height=38)
        tabbar.pack(fill="x", side="top")
        tabbar.pack_propagate(False)

        chips_holder = tk.Frame(tabbar, bg=c["bg"])
        chips_holder.pack(side="left", padx=16)
        for key in ("all", "text", "image", "video", "file"):
            label, _color = theme.KIND_META[key]
            chip = tk.Label(
                chips_holder,
                text=label,
                bg=c["bg"],
                fg=c["subtext"],
                font=(fam, 10),
                padx=12,
                pady=5,
                bd=0,
                cursor="hand2",
            )
            chip.pack(side="left", padx=(0, 4))
            chip.bind("<Button-1>", lambda e, k=key: self.set_kind(k))
            chip.bind("<Enter>", lambda e, lab=chip: self._chip_hover(lab, True))
            chip.bind("<Leave>", lambda e, lab=chip: self._chip_hover(lab, False))
            self._chips[key] = chip

        self._stat_label = tk.Label(tabbar, text="", bg=c["bg"], fg=c["faint"], font=(fam, 9))
        self._stat_label.pack(side="right", padx=18)

        # --- 列表 ---
        body = tk.Frame(outer, bg=c["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=(0, 4))

        self.canvas = tk.Canvas(
            body,
            bg=c["card"],
            highlightthickness=1,
            highlightbackground=c["border"],
            bd=0,
            yscrollincrement=26,
        )
        self.canvas.pack(side="left", fill="both", expand=True)

        self.vbar = tk.Scrollbar(body, orient="vertical", command=self._scroll_to, width=12, bd=0)
        self.vbar.pack(side="right", fill="y")
        self.canvas.configure(yscrollcommand=self._on_yscroll)

        # --- 状态栏 ---
        status = tk.Frame(outer, bg=c["bg"], height=30)
        status.pack(fill="x", side="bottom")
        status.pack_propagate(False)
        self._status = tk.Label(
            status,
            text="Enter 粘贴 · 双击粘贴 · Del 删除 · Ctrl+P 置顶 · Esc 隐藏",
            bg=c["bg"],
            fg=c["faint"],
            font=(fam, 9),
        )
        self._status.pack(side="left", padx=20)

        self._empty_label = None

        # 列表事件
        self.canvas.bind("<Configure>", lambda e: self.render())
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Double-Button-1>", self._on_double_click)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)

        self.menu = tk.Menu(
            self.root,
            tearoff=0,
            font=(fam, 10),
            bg=c["card"],
            fg=c["text"],
            activebackground=c["accent_soft"],
            activeforeground=c["text"],
            bd=1,
            relief="solid",
        )
        self.menu.add_command(label="复制到剪贴板（窗口不关）", command=lambda: self._activate_item(paste=True, close=False))
        self.menu.add_command(label="复制并关窗、粘贴到刚才的窗口", command=lambda: self._activate_item(paste=True, close=True))
        self.menu.add_separator()
        self.menu.add_command(label="置顶 / 取消置顶", command=self._toggle_pin)
        self.menu.add_command(label="删除", command=self._delete_selected)
        self.menu.add_separator()
        self.menu.add_command(label="清空当前分类", command=self._clear_kind)

    def _make_search_icon(self):
        from PIL import ImageDraw

        size = 18
        img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        w = 2
        d.ellipse([2, 2, 12, 12], outline="#8A919B", width=w)
        d.line([11, 11, 16, 16], fill="#8A919B", width=w)
        from PIL import ImageTk

        return ImageTk.PhotoImage(img, master=self.root)

    def _bind(self) -> None:
        self.root.bind("<Up>", lambda e: self._move(-1))
        self.root.bind("<Down>", lambda e: self._move(1))
        self.root.bind("<Prior>", lambda e: self._move(-8))
        self.root.bind("<Next>", lambda e: self._move(8))
        self.root.bind("<Return>", lambda e: self._activate_item(paste=True))
        self.root.bind("<KP_Enter>", lambda e: self._activate_item(paste=True))
        self.root.bind("<Escape>", self._on_escape)
        self.root.bind("<Delete>", lambda e: self._delete_selected())
        self.root.bind("<Control-p>", lambda e: self._toggle_pin())
        self.root.bind("<Control-f>", lambda e: self._focus_search())
        self.root.bind("<F5>", lambda e: self.refresh())

        self.search_var.trace_add("write", self._on_search_changed)
        self.search_entry.bind("<FocusIn>", lambda e: self._set_placeholder(False))
        self.search_entry.bind("<FocusOut>", lambda e: self._set_placeholder(not self.search_var.get()))

    # ------------------------------------------------------------ 状态

    def _set_placeholder(self, show: bool) -> None:
        if show and not self.search_var.get():
            self._ph.place(x=38, y=11)
            self._ph_visible = True
        else:
            self._ph.place_forget()
            self._ph_visible = False

    def _focus_search(self) -> str:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")
        return "break"

    def _clear_search(self) -> str:
        self.search_var.set("")
        self.search_entry.focus_set()
        return "break"

    def _on_escape(self, _event=None) -> str:
        """Esc：搜索框里有字先清空，没字就收起窗口。"""
        if self.search_var.get():
            return self._clear_search()
        self.hide()
        return "break"

    def _on_escape_in_search(self) -> str:
        # 搜索框自己的 Esc 绑定会吞掉事件，这里转给同一个逻辑
        return self._on_escape()

    def _chip_hover(self, label: tk.Label, entering: bool) -> None:
        key = next((k for k, v in self._chips.items() if v is label), None)
        if key == self.current_kind:
            return
        label.configure(bg=theme.COLORS["accent_soft"] if entering else theme.COLORS["bg"])

    def set_kind(self, kind: str) -> None:
        if kind == self.current_kind:
            return
        self.current_kind = kind
        self.selected_index = -1
        self._refresh_chips()
        self.refresh()

    def _refresh_chips(self) -> None:
        counts = self.app.kind_counts()
        c = theme.COLORS
        for key, chip in self._chips.items():
            active = key == self.current_kind
            n = counts.get("all", 0) if key == "all" else counts.get(key, 0)
            text = theme.KIND_META[key][0]
            if n:
                text = f"{text} {n}"
            chip.configure(
                text=text,
                bg=c["accent"] if active else c["bg"],
                fg="#FFFFFF" if active else c["subtext"],
            )

    # ------------------------------------------------------------ 数据

    def refresh(self, keep_selection: bool = True) -> None:
        old_id = None
        if keep_selection and 0 <= self.selected_index < len(self.items):
            old_id = self.items[self.selected_index]["id"]
        query = self.search_var.get().strip()
        self.items = self.app.query_items(self.current_kind, query, limit=1000)
        self._last_query = query
        if old_id is not None:
            found = -1
            for i, row in enumerate(self.items):
                if row["id"] == old_id:
                    found = i
                    break
            self.selected_index = found
        self._refresh_chips()
        total = len(self.items)
        if total == 0:
            self.selected_index = -1
        self._update_stat(total)
        self._update_scrollregion()
        self.render()

    def _update_stat(self, total: int) -> None:
        if self.current_kind == "all":
            self._stat_label.configure(text=f"共 {total} 条")
        else:
            self._stat_label.configure(text=f"{theme.KIND_META[self.current_kind][0]} · {total} 条")

    def _update_scrollregion(self) -> None:
        width = max(1, self.canvas.winfo_width())
        height = max(1, len(self.items) * ROW_H)
        self.canvas.configure(scrollregion=(0, 0, width, height))

    # ------------------------------------------------------------ 绘制

    def _on_yscroll(self, first: str, last: str) -> None:
        self.vbar.set(first, last)
        self.render()

    def _scroll_to(self, *args) -> None:
        self.canvas.yview(*args)
        self.render()

    def _on_wheel(self, event) -> str:
        steps = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(steps * 3, "units")
        self.render()
        return "break"

    def render(self) -> None:
        c = self.canvas
        c.delete("row")
        width = c.winfo_width()
        if width <= 1 or not self.items:
            self._draw_empty(width)
            return
        self._clear_empty()
        top = c.canvasy(0)
        height = c.winfo_height()
        first = max(0, int(top // ROW_H) - 1)
        last = min(len(self.items), int((top + height) // ROW_H) + 2)
        for idx in range(first, last):
            self._draw_row(idx, width)

    def _draw_empty(self, width: int) -> None:
        if width <= 1:
            return
        c = self.canvas
        c.delete("empty")
        h = max(c.winfo_height(), 100)
        if self.search_var.get().strip():
            msg = "没有匹配的内容"
        elif self.current_kind == "all":
            msg = "还没有记录，复制点什么试试"
        else:
            msg = f"暂无{theme.KIND_META[self.current_kind][0]}记录"
        c.create_text(
            width // 2, h // 2 - 10, text=msg, fill=theme.COLORS["faint"],
            font=(self._ui_family, 11), tags="empty",
        )
        c.create_text(
            width // 2, h // 2 + 16, text="后台监听中，新复制的内容会自动出现在这里",
            fill=theme.COLORS["faint"], font=(self._ui_family, 9), tags="empty",
        )

    def _clear_empty(self) -> None:
        self.canvas.delete("empty")

    def _draw_row(self, idx: int, width: int) -> None:
        row = self.items[idx]
        c = self.canvas
        y0 = idx * ROW_H
        selected = idx == self.selected_index
        bg = theme.COLORS["selected"] if selected else theme.COLORS["card"]
        tags = ("row", f"r{idx}")
        c.create_rectangle(0, y0, width, y0 + ROW_H, fill=bg, outline="", tags=tags)

        # 选中时左侧强调条
        if selected:
            c.create_rectangle(0, y0 + 10, 3, y0 + ROW_H - 10, fill=theme.COLORS["accent"], outline="", tags=tags)

        photo = self._thumb_for(row)
        c.create_image(PAD_LEFT + THUMB // 2, y0 + ROW_H // 2, image=photo, tags=tags)

        text_x = PAD_LEFT + THUMB + 16
        right_reserve = 52
        preview = theme_font_truncate(item_preview(row), (self._ui_family, 10), width - text_x - right_reserve)
        c.create_text(
            text_x, y0 + 27, text=preview, anchor="w",
            font=(self._ui_family, 10), fill=theme.COLORS["text"], tags=tags,
        )
        meta = item_meta(row)
        c.create_text(
            text_x, y0 + 50, text=meta, anchor="w",
            font=(self._ui_family, 8), fill=theme.COLORS["subtext"], tags=tags,
        )

        if row["pinned"]:
            c.create_text(
                width - 22, y0 + ROW_H // 2, text="★", anchor="center",
                font=(self._ui_family, 12), fill=theme.COLORS["pinned"], tags=tags,
            )

        if idx < len(self.items) - 1:
            c.create_line(PAD_LEFT, y0 + ROW_H - 1, width - PAD_LEFT, y0 + ROW_H - 1,
                          fill="#F1F3F5", tags=tags)

    def _thumb_for(self, row):
        key = row["id"]
        cached = self._thumb_cache.get(key)
        if cached is not None:
            return cached
        photo = None
        if row["kind"] == "image" and row["thumb_path"]:
            try:
                img = image_tools.load_thumb_pil(Path(row["thumb_path"]), THUMB)
                photo = theme.rounded_thumb(img, THUMB, master=self.root)
            except Exception:
                photo = None
        if photo is None and row["kind"] == "image" and row["paths"]:
            try:
                first = json.loads(row["paths"])[0]
                with Image.open(first) as src:
                    src.thumbnail((THUMB * 2, THUMB * 2))
                    photo = theme.rounded_thumb(src.convert("RGB"), THUMB, master=self.root)
            except Exception:
                photo = None
        if photo is None:
            photo = theme.type_icon(self.root, row["kind"], 40)
        if len(self._thumb_cache) > 800:
            self._thumb_cache.clear()
        self._thumb_cache[key] = photo
        return photo

    # ------------------------------------------------------------ 交互

    def _index_at(self, y: int) -> int:
        top = self.canvas.canvasy(0)
        idx = int((y + top) // ROW_H)
        if 0 <= idx < len(self.items):
            return idx
        return -1

    def _on_click(self, event) -> None:
        idx = self._index_at(event.y)
        if idx < 0:
            return
        self.select(idx, scroll=False)
        # 单击直接复制；是否关窗、是否自动粘贴由配置决定
        if bool(self.app.config.get("click_to_paste", True)):
            self._activate_item(paste=True)

    def _on_double_click(self, event) -> None:
        idx = self._index_at(event.y)
        if idx >= 0:
            self.select(idx, scroll=False)
            self._activate_item(paste=True)

    def _on_right_click(self, event) -> None:
        idx = self._index_at(event.y)
        if idx < 0:
            return
        self.select(idx, scroll=False)
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _on_motion(self, event) -> None:
        pass

    def _move(self, delta: int) -> str:
        if not self.items:
            return "break"
        if self.selected_index < 0:
            self.selected_index = 0
        else:
            self.selected_index = min(max(0, self.selected_index + delta), len(self.items) - 1)
        self._ensure_visible(self.selected_index)
        self.render()
        return "break"

    def select(self, idx: int, scroll: bool = True) -> None:
        self.selected_index = idx
        if scroll:
            self._ensure_visible(idx)
        self.render()

    def _ensure_visible(self, idx: int) -> None:
        if idx < 0:
            return
        c = self.canvas
        top = c.canvasy(0)
        height = c.winfo_height()
        row_top = idx * ROW_H
        row_bottom = row_top + ROW_H
        if row_top < top:
            c.yview_moveto(row_top / max(1, len(self.items) * ROW_H))
        elif row_bottom > top + height:
            c.yview_moveto((row_bottom - height) / max(1, len(self.items) * ROW_H))

    def _selected_row(self):
        if 0 <= self.selected_index < len(self.items):
            return self.items[self.selected_index]
        return None

    def _activate_item(self, paste: bool, close: bool | None = None) -> None:
        row = self._selected_row()
        if row is None:
            return
        self.app.use_item(row, paste=paste, target_hwnd=self._target_hwnd, close=close)

    def _toggle_pin(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self.app.toggle_pin(row["id"])
        self.refresh()

    def _delete_selected(self) -> None:
        row = self._selected_row()
        if row is None:
            return
        self.app.delete_item(row["id"])
        self.selected_index = min(self.selected_index, len(self.items) - 1)
        self.refresh()

    def _clear_kind(self) -> None:
        label = theme.KIND_META[self.current_kind][0]
        if not messagebox.askyesno("确认", f"清空所有{label}记录？置顶的条目会保留。", parent=self.root):
            return
        self.app.clear_items(None if self.current_kind == "all" else self.current_kind)
        self.refresh()

    def _on_search_changed(self, *_args) -> None:
        if self._ph_visible and self.search_var.get():
            self._ph.place_forget()
            self._ph_visible = False
        elif not self.search_var.get() and not self.search_entry.focus_get():
            pass
        if self._search_job:
            self.root.after_cancel(self._search_job)
        self._search_job = self.root.after(160, self._do_search)

    def _do_search(self) -> None:
        self._search_job = None
        self.refresh(keep_selection=False)

    # ------------------------------------------------------------ 显示 / 隐藏

    def show(self, target_hwnd: int = 0) -> None:
        from .. import paste

        if target_hwnd:
            self._target_hwnd = target_hwnd
        elif not self._visible:
            self._target_hwnd = paste.foreground_window()
        self.refresh(keep_selection=False)
        self.root.deiconify()
        self.root.lift()
        try:
            self.root.attributes("-topmost", True)
            self.root.after(60, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        self.root.focus_force()
        self.search_entry.focus_set()
        if not self._visible:
            self._center()
        self._visible = True
        if self.items:
            self.selected_index = 0
            self.render()

    def _center(self) -> None:
        try:
            self.root.update_idletasks()
            w = max(self.root.winfo_width(), 1)
            h = max(self.root.winfo_height(), 1)
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = max(0, (sw - w) // 2)
            y = max(0, (sh - h) // 3)
            self.root.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def hide(self) -> None:
        self._visible = False
        self.root.withdraw()

    def toggle(self) -> None:
        if self._visible:
            self.hide()
        else:
            self.show()

    def notify_new_item(self) -> None:
        if self._visible:
            self.refresh()


def theme_font_truncate(text: str, font_spec: tuple, max_width: int) -> str:
    """按像素宽度截断，超出加省略号。"""
    if max_width <= 20:
        return ""
    if not text:
        return ""
    import tkinter.font as tkfont

    f = tkfont.Font(family=font_spec[0], size=font_spec[1], weight=font_spec[2] if len(font_spec) > 2 else "normal")
    if f.measure(text) <= max_width:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if f.measure(text[:mid] + "…") <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + "…"
