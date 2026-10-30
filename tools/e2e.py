"""端到端集成测试：真实剪贴板 + 监听线程 + 全局热键 + 托盘 + 回写。

只跑流水线，不弹主窗口（热键用例会短暂开一个置顶小窗）。测试结束尽量还原原剪贴板。
运行：.venv\\Scripts\\python.exe tools\\e2e.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app import images, paste, winclip  # noqa: E402
from app.app import ClipboardApp  # noqa: E402
from app.hotkey import HotkeyManager  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, fn) -> None:
    global PASS, FAIL
    try:
        detail = fn()
        PASS += 1
        print(f"  [OK] {name}  {detail or ''}")
    except Exception as exc:  # noqa: BLE001
        FAIL += 1
        print(f"  [FAIL] {name}  {type(exc).__name__}: {exc}")


def drain(app, seconds: float = 1.2) -> list[tuple[str, object]]:
    """等监听线程把内容丢进队列，然后手工把队列里的 clip 事件走完 ingest。"""
    out = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            kind, payload = app.queue.get(timeout=0.05)
        except Exception:
            continue
        out.append((kind, payload))
        if kind == "clip":
            app._ingest(payload)
        elif kind == "toast":
            print("      提示:", payload)
    return out


def wait_for(fn, seconds: float = 3.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            value = fn()
        except Exception:
            value = None
        if value:
            return value
        time.sleep(0.05)
    return None


def newest(app, kind: str, seconds: float = 3.0):
    return wait_for(
        lambda: (app.store.query(kind=kind, limit=1) or [None])[0], seconds
    )


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cm-e2e-"))
    print("数据目录", tmp)

    original = winclip.read_clipboard()
    print("原剪贴板类型", original.kind if original else "空")

    app = ClipboardApp(data_dir=tmp)

    # ---------------------------------------------------------------- 监听
    print("\n[监听线程]")
    app._start_watcher()
    time.sleep(0.4)

    def t_text():
        winclip.write_text("端到端测试 - 剪贴板文字 E2E-abc123")
        events = drain(app)

        def found():
            rows = app.store.query(search="E2E-abc123", limit=5)
            return rows[0] if rows else None

        row = wait_for(found, 3.0)
        # 机器上可能有人在同时用剪贴板，别因为抓到了别人的内容就判失败
        assert row, f"没等到自己写的文字入库（本次捕获事件 {len(events)} 个）"
        return f"捕获 {len(events)} 次，id={row['id']}"

    check("文字捕获", t_text)

    def t_dedupe():
        rows_before = app.store.query(search="E2E-abc123", limit=10)
        before = len(rows_before)
        assert before == 1, f"测试前应只有 1 条同名记录，实际 {before}"
        winclip.write_text("端到端测试 - 剪贴板文字 E2E-abc123")
        drain(app)
        rows_after = app.store.query(search="E2E-abc123", limit=10)
        assert len(rows_after) == 1, f"重复内容应去重，{before}->{len(rows_after)}"
        assert rows_after[0]["use_count"] >= 2, rows_after[0]["use_count"]
        return f"仍为 1 条，use_count={rows_after[0]['use_count']}"

    check("重复内容去重", t_dedupe)

    def t_image():
        img = Image.new("RGB", (120, 80), (200, 40, 60))
        winclip.write_image(b"", images.image_to_dib(img))
        drain(app)
        row = newest(app, "image")
        assert row, "图片入库超时"
        assert row["file_path"] and Path(row["file_path"]).exists(), row["file_path"]
        assert row["thumb_path"] and Path(row["thumb_path"]).exists(), row["thumb_path"]
        assert (row["width"], row["height"]) == (120, 80), (row["width"], row["height"])
        return f"webp {Path(row['file_path']).stat().st_size} 字节，{row['width']}x{row['height']}"

    check("图片捕获", t_image)

    def t_files():
        a = tmp / "报告 2026.txt"
        a.write_text("文件引用测试", "utf-8")
        winclip.write_files([str(a)])
        drain(app)
        row = newest(app, "file")
        paths = json.loads(row["paths"] or "[]") if row else []
        assert str(a) in paths, paths
        return f"引用路径 {a.name}"

    check("文件捕获", t_files)

    def t_video():
        v = tmp / "演示.mp4"
        v.write_bytes(b"\x00" * 2048)
        winclip.write_files([str(v)])
        drain(app)
        row = newest(app, "video")
        paths = json.loads(row["paths"] or "[]") if row else []
        assert "演示.mp4" in " ".join(paths), paths
        return f"视频归类为 {row['kind']}，{row['size_bytes']} 字节"

    check("视频捕获（按扩展名归类）", t_video)

    def t_search():
        hit = app.store.query(search="E2E", limit=10)
        assert hit, "搜索 E2E 应有结果"
        hit2 = app.store.query(search="端到", limit=10)
        assert hit2, "中文两字应回退 LIKE 命中"
        return f"『E2E』{len(hit)} 条，『端到』{len(hit2)} 条"

    check("搜索 / 模糊搜索", t_search)

    # ---------------------------------------------------------------- 回写
    print("\n[回写剪贴板]")

    def t_write_text():
        row = app.store.query(kind="text", limit=1)[0]
        app._write_row(row)
        content = winclip.read_clipboard()
        assert content.text == row["content"][: len(content.text)], content.text[:40]
        return "文字回写一致"

    check("从记录粘贴文字", t_write_text)

    def t_write_image():
        row = app.store.query(kind="image", limit=1)[0]
        app._write_row(row)
        content = winclip.read_clipboard()
        assert content.kind == "image" and content.dib, content.kind
        back = images.dib_to_image(content.dib)
        assert back.size == (120, 80), back.size
        return f"位图回写 {back.size}"

    check("从记录粘贴图片", t_write_image)

    def t_write_files():
        row = app.store.query(kind="file", limit=1)[0]
        app._write_row(row)
        content = winclip.read_clipboard()
        assert content.paths and content.paths[0].endswith("报告 2026.txt"), content.paths
        return "文件清单回写一致"

    check("从记录粘贴文件", t_write_files)

    def t_write_missing():
        row = dict(app.store.query(kind="video", limit=1)[0])
        for p in json.loads(row["paths"] or "[]"):
            Path(p).unlink(missing_ok=True)
        try:
            app._write_row(row)
        except RuntimeError as exc:
            assert "不存在" in str(exc), exc
            return "原文件缺失时给出明确提示"
        raise AssertionError("原文件缺失应报错")

    check("原文件缺失处理", t_write_missing)

    # ---------------------------------------------------------------- 热键
    print("\n[全局热键]")
    fired = {"n": 0}

    def on_hotkey() -> None:
        fired["n"] += 1

    hk = HotkeyManager(on_hotkey)

    def t_register():
        assert hk.start(["ctrl", "alt"], "f9"), "注册 Ctrl+Alt+F9 失败"
        return "注册 Ctrl+Alt+F9"

    check("注册热键", t_register)

    def t_trigger():
        import ctypes
        import tkinter as tk

        # 属于本进程的窗口在前台时才收得到注入按键（前台窗口权限更高时会被 UIPI 拦掉）
        root = tk.Tk()
        root.geometry("260x90+320+320")
        root.attributes("-topmost", True)
        root.update()
        hwnd = int(root.wm_frame(), 16)
        got_foreground = False
        try:
            for _ in range(4):
                paste.activate(hwnd, 0.25)
                if paste.foreground_window() == hwnd:
                    got_foreground = True
                    break
                time.sleep(0.2)
            if got_foreground:
                paste.send_hotkey(["ctrl", "alt"], 0x78)
                deadline = time.time() + 2.5
                while time.time() < deadline and fired["n"] == 0:
                    time.sleep(0.05)
        finally:
            root.destroy()

        if not got_foreground:
            # 自动化环境抢不到前台（Windows 前台锁），改用直接投递 WM_HOTKEY 验证消息循环
            ctypes.WinDLL("user32").PostThreadMessageW(hk._thread_id, 0x0312, 1, 0)
            deadline = time.time() + 1.5
            while time.time() < deadline and fired["n"] == 0:
                time.sleep(0.05)
            hk.stop()
            assert fired["n"] > 0, "消息循环没有响应 WM_HOTKEY"
            return "前台被占用，改投递 WM_HOTKEY 验证消息循环"

        hk.stop()
        assert fired["n"] > 0, "热键没有触发"
        return f"真实按键触发 {fired['n']} 次"

    check("按下热键触发回调", t_trigger)

    # ---------------------------------------------------------------- 托盘
    print("\n[托盘]")

    def t_tray():
        app._start_tray()
        assert app.tray is not None and app.tray.available, "托盘不可用"
        visible = wait_for(lambda: getattr(app.tray._icon, "visible", False))
        alive = app.tray._thread is not None and app.tray._thread.is_alive()
        app.tray.stop()
        assert alive, f"托盘线程没起来（{app.tray.error}）"
        assert visible, "托盘图标未显示"
        return "托盘图标已创建并显示"

    check("托盘启动/退出", t_tray)

    # ---------------------------------------------------------------- 还原
    print("\n[还原原剪贴板]")

    def t_restore():
        if original and original.kind == "text":
            winclip.write_text(original.text, original.html or "")
        elif original and original.kind == "image" and original.dib:
            winclip.write_image(original.png or b"", original.dib)
        elif original and original.kind == "file" and original.paths:
            winclip.write_files(original.paths)
        else:
            return "原剪贴板为空或不可还原，跳过"
        return "已还原"

    check("还原", t_restore)

    app.watcher.stop()
    app.store.close()

    print(f"\n通过 {PASS} 项，失败 {FAIL} 项")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
