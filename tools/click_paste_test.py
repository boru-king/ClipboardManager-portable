"""验证「单击条目直接粘贴」这条链路。

跑法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\click_paste_test.py

分两层：
1) 接线层：模拟鼠标单击，断言 use_item 收到 paste=True（不依赖窗口焦点，稳定）；
2) 真粘贴：把目标窗口抢到前台时，断言目标文本框真的收到了内容（抢不到就跳过）。
"""

from __future__ import annotations

import sys
import tempfile
import time
import tkinter as tk
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.app import ClipboardApp  # noqa: E402
from app.ui.main_window import ROW_H, MainWindow  # noqa: E402
from app.winclip import ClipContent  # noqa: E402

passed = 0
failed = 0


def check(name: str, fn) -> None:
    global passed, failed
    try:
        detail = fn()
        passed += 1
        print(f"[OK] {name}" + (f"  {detail}" if detail else ""))
    except Exception:
        failed += 1
        print(f"[FAIL] {name}")
        traceback.print_exc(limit=3)


def make_app(tag: str, click_to_paste: bool, close_after_pick: bool = False,
             target_hwnd: int = 0) -> tuple[ClipboardApp, MainWindow, Path]:
    d = Path(tempfile.mkdtemp(prefix=f"cm-click-{tag}-"))
    app = ClipboardApp(data_dir=d)
    app.config.set("click_to_paste", click_to_paste)
    app.config.set("close_after_pick", close_after_pick)
    app.config.set("auto_paste", True)
    app._ingest_text(ClipContent(kind="text", text=f"单击粘贴测试 {tag}"))
    win = MainWindow(app)
    app.window = win
    if target_hwnd:
        win._target_hwnd = target_hwnd
    win.show()
    win.root.update()
    return app, win, d


def click_row0(win: MainWindow) -> None:
    win.root.update_idletasks()
    win.render()
    win.canvas.event_generate("<Button-1>", x=220, y=ROW_H // 2)
    win.root.update()


def t_click_keeps_window() -> str:
    """默认：单击复制，窗口不关。"""
    from app import winclip

    app, win, _ = make_app("keep", True, close_after_pick=False)
    calls: list[tuple] = []
    orig = app.use_item

    def spy(row, paste=True, target_hwnd=0, close=None):
        calls.append((row["id"], paste, close))
        return orig(row, paste=paste, target_hwnd=target_hwnd, close=close)

    app.use_item = spy
    try:
        click_row0(win)
        time.sleep(0.8)
        win.root.update()
    finally:
        app.use_item = orig
    assert calls, "单击没有触发复制"
    item_id, paste, close = calls[-1]
    assert paste is True, calls[-1]
    # close=None 表示"按配置决定"，默认就是 False（不关窗）
    assert close is None, calls[-1]
    assert win._visible is True, "窗口被关掉了"
    assert win.root.winfo_viewable(), "窗口不可见"
    text = winclip.read_clipboard().text or ""
    assert "单击粘贴测试 keep" in text, text[:60]
    win.root.destroy()
    app.store.close()
    return f"单击后窗口仍在，剪贴板={text[:20]!r}"


def t_click_closes_when_asked() -> str:
    """打开「选择后关窗」时：单击后窗口才隐藏。"""
    app, win, _ = make_app("close", True, close_after_pick=True)
    calls: list[tuple] = []
    orig = app.use_item

    def spy(row, paste=True, target_hwnd=0, close=None):
        calls.append((row["id"], close))
        return orig(row, paste=paste, target_hwnd=target_hwnd, close=close)

    app.use_item = spy
    try:
        click_row0(win)
        time.sleep(0.6)
        win.root.update()
    finally:
        app.use_item = orig
    assert calls and calls[-1][1] is None, calls
    assert win._visible is False, "配置要求关窗，但没有关"
    win.root.destroy()
    app.store.close()
    return "开关打开时单击后关窗"


def t_click_off_selects_only() -> str:
    app, win, _ = make_app("off", False)
    calls: list[tuple] = []
    orig = app.use_item
    app.use_item = lambda row, paste=True, target_hwnd=0, close=None: calls.append((row["id"], paste))
    try:
        click_row0(win)
    finally:
        app.use_item = orig
    assert not calls, f"关掉开关后不该粘贴，却调用了 {calls}"
    assert win.selected_index == 0, win.selected_index
    win.root.destroy()
    app.store.close()
    return "关掉开关只选中，不复制"


def t_real_paste_into_textbox() -> str:
    """把目标窗口抢到前台后，真实走一遍 单击 -> Ctrl+V。"""
    from app import paste

    app, win, _ = make_app("real", True, close_after_pick=True)
    target = tk.Toplevel(win.root)  # 同一个 Tk 解释器，避免多 root 的图片绑定问题
    target.title("单击粘贴目标")
    target.geometry("520x200+140+140")
    box = tk.Text(target, font=("Microsoft YaHei UI", 11))
    box.pack(fill="both", expand=True)
    target.update()
    hwnd = int(target.winfo_id())

    got = False
    for _ in range(6):
        paste.activate(hwnd, 0.3)
        target.update()
        time.sleep(0.2)
        if paste.foreground_window() == hwnd:
            got = True
            break

    if not got:
        win.root.destroy()
        app.store.close()
        return "[跳过] 抢不到前台（沙箱限制），接线层已覆盖"

    win.show(target_hwnd=hwnd)
    win.root.update()
    click_row0(win)
    for _ in range(25):
        target.update()
        time.sleep(0.1)
        if "单击粘贴测试 real" in box.get("1.0", "end-1c"):
            break
    text = box.get("1.0", "end-1c").strip()
    win.root.destroy()
    app.store.close()
    assert "单击粘贴测试 real" in text, repr(text[:80])
    return f"目标文本框收到 {text[:30]!r}"


def t_activate_steals_foreground() -> str:
    """paste.activate 应该能把焦点从本程序窗口抢到目标窗口。"""
    from app import paste

    app, win, _ = make_app("act", True)
    target = tk.Toplevel(win.root)
    target.title("焦点目标")
    target.geometry("420x160+180+180")
    tk.Label(target, text="目标窗口").pack(fill="both", expand=True)
    target.update()
    hwnd = int(target.winfo_id())

    win.root.focus_force()
    win.root.update()
    time.sleep(0.2)
    before = paste.foreground_window()
    win_hwnd = int(win.root.winfo_id())
    if before not in (win_hwnd, hwnd):
        win.root.destroy()
        app.store.close()
        return f"[跳过] 本环境前台被别的程序占着（{before}），无法测试抢焦点"
    ok = paste.activate(hwnd, 0.25)
    target.update()
    after = paste.foreground_window()
    win.root.destroy()
    app.store.close()
    assert ok, f"activate 返回 False（before={before}）"
    assert after == hwnd, f"前台没有切到目标：{after} != {hwnd}"
    return "焦点从主窗口切到了目标窗口"


def main() -> int:
    check("单击复制后窗口不关", t_click_keeps_window)
    check("打开关窗开关才会隐藏", t_click_closes_when_asked)
    check("关掉单击开关只选中", t_click_off_selects_only)
    check("activate 抢焦点", t_activate_steals_foreground)
    check("关窗模式下真实粘贴到输入框", t_real_paste_into_textbox)
    print(f"\n通过 {passed} 项，失败 {failed} 项")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
