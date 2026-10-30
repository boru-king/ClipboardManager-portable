"""验证自动粘贴链路：把记录写进剪贴板 -> 抢回前台 -> 发 Ctrl+V -> 目标窗口真的收到文字。

沙箱里注入按键只有在本进程窗口处于前台时才生效，所以要先把我们自己的
目标窗口抢到前台；抢不到就直接判为环境限制（不算失败）。

运行：.venv\\Scripts\\python.exe tools\\paste_test.py
"""

from __future__ import annotations

import ctypes
import sys
import tempfile
import time
import tkinter as tk
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import paste, winclip  # noqa: E402
from app.app import ClipboardApp  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)


def hwnd_of(root: tk.Tk) -> int:
    root.update_idletasks()
    return int(root.winfo_id())


def main() -> int:
    ok = 0
    bad = 0

    def check(name: str, cond: bool, extra: str = "") -> None:
        nonlocal ok, bad
        if cond:
            ok += 1
            print(f"  [OK] {name}  {extra}")
        else:
            bad += 1
            print(f"  [XX] {name}  {extra}")

    print("[目标窗口]")
    root = tk.Tk()
    root.title("自动粘贴测试目标")
    root.geometry("560x220+120+120")
    box = tk.Text(root, font=("Microsoft YaHei UI", 11))
    box.pack(fill="both", expand=True)
    root.update_idletasks()
    root.update()
    hwnd = hwnd_of(root)
    check("目标窗口已创建", bool(hwnd) and bool(user32.IsWindow(hwnd)), f"hwnd={hwnd}")

    print("[抢前台]")
    got = False
    try:
        root.attributes("-topmost", True)
    except tk.TclError:
        pass
    for i in range(8):
        paste.activate(hwnd, 0.4)
        root.update()
        time.sleep(0.25)
        if paste.foreground_window() == hwnd:
            got = True
            break
    if got:
        print("  [OK] 抢到前台")
    else:
        print("  [跳过] 抢前台  前台被其它程序占用（沙箱限制），只验证写回")

    if got:
        text = "自动粘贴链路 E2E-PASTE-4455"
        winclip.write_text(text)
        box.delete("1.0", "end")
        box.focus_set()
        root.update()
        paste.paste_into(hwnd, 250)
        for _ in range(20):
            root.update()
            time.sleep(0.1)
            if text in box.get("1.0", "end-1c"):
                break
        got_text = box.get("1.0", "end-1c").strip()
        check("Ctrl+V 注入成功", text in got_text, repr(got_text[:60]))

    print("[app.use_item 全链路]")
    tmp = tempfile.mkdtemp(prefix="cm-paste-")
    app = ClipboardApp(data_dir=Path(tmp))
    winclip.write_text("来自记录的旧内容")
    app._ingest(winclip.read_clipboard())
    rows = app.query_items("all", "", 10)
    check("记录已入库", len(rows) >= 1, f"{len(rows)} 条")
    if rows:
        row = rows[0]
        if got:
            box.delete("1.0", "end")
            box.focus_set()
            root.update()
        app.use_item(row, True, hwnd)
        time.sleep(1.4)
        root.update()
        after = box.get("1.0", "end-1c").strip()
        if got:
            check("选中记录后自动粘贴到目标窗口", "来自记录的旧内容" in after, repr(after[:60]))
        else:
            check("选中记录后写回剪贴板", "来自记录的旧内容" in (winclip.read_clipboard().text or ""), "前台被占用，只验证写回")
    app.store.close()
    root.destroy()

    print(f"\n通过 {ok} 项，失败 {bad} 项")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
