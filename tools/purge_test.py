"""验证「彻底清除」功能：含置顶的全量清空、图片文件删除、数据目录删除、purge_data 全链路。

用法（项目根目录）：
    python tools/purge_test.py
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app.app import ClipboardApp, delete_data_dir  # noqa: E402
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


def fresh_dir(tag: str) -> Path:
    d = Path(tempfile.gettempdir()) / f"cm-purge-{tag}"
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    return d


def write_image(path: Path, size=(80, 60), color=(30, 120, 220)) -> bytes:
    img = Image.new("RGB", size, color)
    import io

    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ 用例

def t_wipe_keeps_nothing() -> str:
    """wipe() 连置顶一起删，并且返回需要清理的图片路径。"""
    d = fresh_dir("wipe")
    app = ClipboardApp(d)
    try:
        app._ingest_text(ClipContent(kind="text", text="第一条记录"))
        app._ingest_text(ClipContent(kind="text", text="第二条记录"))
        rows = app.store.query(kind=None, search="", limit=10)
        assert len(rows) == 2, len(rows)
        app.store.toggle_pin(rows[0]["id"])
        assert app.store.count(None) == 2
        files = app.store.wipe()
        assert app.store.count(None) == 0, app.store.count(None)
        assert files == [], files
    finally:
        app.store.close()
    return "置顶也被清空，记录数 0"


def t_wipe_returns_image_files() -> str:
    """图片条目被 wipe 后返回 webp / 缩略图路径，且文件确实删掉。"""
    d = fresh_dir("img")
    app = ClipboardApp(d)
    try:
        png = write_image(Path("x"), size=(120, 90))
        app._ingest_image(ClipContent(kind="image", png=png))
        rows = app.store.query(kind="image", search="", limit=10)
        assert len(rows) == 1, len(rows)
        img_path = Path(rows[0]["file_path"])
        thumb_path = Path(rows[0]["thumb_path"])
        assert img_path.exists() and thumb_path.exists(), (img_path, thumb_path)
        files = app.store.wipe()
        app._remove_files(files)
        assert not img_path.exists(), img_path
        assert not thumb_path.exists(), thumb_path
        assert app.store.count("image") == 0
    finally:
        app.store.close()
    return "webp 与缩略图均被删除"


def t_delete_data_dir() -> str:
    """delete_data_dir 把数据库/图片/配置/日志清掉，并删掉目录本身。"""
    d = fresh_dir("dir")
    app = ClipboardApp(d)
    app._ingest_text(ClipContent(kind="text", text="用来占位的记录"))
    app.store.close()
    (d / "config.json").write_text("{}", encoding="utf-8")
    (d / "error.log").write_text("x", encoding="utf-8")
    (d / "images" / "a.webp").write_bytes(b"x")
    (d / "thumbs" / "a.webp").write_bytes(b"x")
    assert d.exists()
    errors = delete_data_dir(d, remove_root=True)
    assert errors == [], errors
    assert not d.exists(), f"目录还在：{d}"
    return "目录已删除"


def t_purge_data_end_to_end() -> str:
    """purge_data() 全链路：清空 + 删文件 + 退出准备，不留下 config.json。"""
    d = fresh_dir("e2e")
    app = ClipboardApp(d)
    app._ingest_text(ClipContent(kind="text", text="彻底清除测试"))
    app.config.set("max_items", 1234)
    app.config.save()
    assert (d / "clipboard.db").exists()
    ok, error = app.purge_data()
    assert ok, error
    assert app._purged is True
    assert not d.exists(), f"数据目录还在：{d}"
    return "目录与数据库全部清除"


def t_delete_data_dir_keeps_foreign_dir() -> str:
    """remove_root=True 但目录不像本程序的数据目录时，只清内容不删目录。"""
    d = fresh_dir("foreign")
    d.mkdir(parents=True)
    (d / "someone-else.txt").write_text("hi", encoding="utf-8")
    errors = delete_data_dir(d, remove_root=True)
    assert errors == [], errors
    assert d.exists() and (d / "someone-else.txt").exists()
    return "无关目录不被删除"


def t_cli_purge() -> str:
    """main(["--purge"]) 走命令行清除：确认后删目录，弹两次提示。"""
    import ctypes

    import app.app as app_mod

    d = fresh_dir("cli")
    app = ClipboardApp(d)
    app._ingest_text(ClipContent(kind="text", text="命令行清除测试"))
    app.store.close()
    assert d.exists()

    boxes: list[tuple] = []
    orig_data_dir = app_mod.config_mod.data_dir
    orig_box = ctypes.windll.user32.MessageBoxW
    app_mod.config_mod.data_dir = lambda: d
    ctypes.windll.user32.MessageBoxW = lambda *a, **k: (boxes.append(a), 1)[1]
    try:
        rc = app_mod.main(["--purge"])
    finally:
        app_mod.config_mod.data_dir = orig_data_dir
        ctypes.windll.user32.MessageBoxW = orig_box
    if rc == 1 and len(boxes) == 1 and d.exists():
        # 有别的实例在跑（命名互斥体占用），--purge 会提示"正在运行"并退出
        return "[跳过] 当前有实例在运行，--purge 拒绝执行"
    assert rc == 0, rc
    assert not d.exists(), f"目录还在：{d}"
    assert len(boxes) == 2, f"应该先确认再提示，实际 {len(boxes)} 次"
    return "确认 + 完成提示，目录已删除"


def t_tray_menu_has_purge() -> str:
    """托盘菜单里出现「清除全部数据并退出…」，且回调接到 on_purge。"""
    from app.tray import TrayIcon

    fired: list[str] = []
    tray = TrayIcon(
        on_show=lambda: fired.append("show"),
        on_quit=lambda: fired.append("quit"),
        on_settings=lambda: fired.append("settings"),
        on_purge=lambda: fired.append("purge"),
    )
    assert tray.start(), tray.error
    try:
        texts = [str(item.text) for item in tray._icon.menu.items]
        assert any("清除全部数据" in t for t in texts), texts
        assert texts[-1] == "退出", texts
        for item in tray._icon.menu.items:
            if "清除全部数据" in str(item.text):
                item(None)
    finally:
        tray.stop()
    assert fired == ["purge"], fired
    return "菜单项：" + " / ".join(t for t in texts if t)


def t_confirm_purge_yes() -> str:
    """点「确定」→ 数据目录删除、程序进入退出流程。"""
    from tkinter import messagebox

    d = fresh_dir("confirm-yes")
    app = ClipboardApp(d)
    app._ingest_text(ClipContent(kind="text", text="确认框里点确定"))
    orig = messagebox.askyesno
    messagebox.askyesno = lambda *a, **k: True
    try:
        app._confirm_purge()
    finally:
        messagebox.askyesno = orig
    assert app._purged is True
    assert not d.exists(), f"目录还在：{d}"
    return "确认后清除"


def t_confirm_purge_no() -> str:
    """点「取消」→ 什么都不删。"""
    from tkinter import messagebox

    d = fresh_dir("confirm-no")
    app = ClipboardApp(d)
    try:
        app._ingest_text(ClipContent(kind="text", text="确认框里点取消"))
        orig = messagebox.askyesno
        messagebox.askyesno = lambda *a, **k: False
        try:
            app._confirm_purge()
        finally:
            messagebox.askyesno = orig
        assert app._purged is False
        assert d.exists() and app.store.count(None) == 1
    finally:
        app.store.close()
    return "取消后记录原样保留"


def main() -> int:
    check("wipe 含置顶全量清空", t_wipe_keeps_nothing)
    check("wipe 返回图片文件并删除", t_wipe_returns_image_files)
    check("delete_data_dir 删目录", t_delete_data_dir)
    check("purge_data 全链路", t_purge_data_end_to_end)
    check("delete_data_dir 不动无关目录", t_delete_data_dir_keeps_foreign_dir)
    check("命令行 --purge", t_cli_purge)
    check("托盘菜单含清除项", t_tray_menu_has_purge)
    check("确认框点确定", t_confirm_purge_yes)
    check("确认框点取消", t_confirm_purge_no)
    print(f"\n通过 {passed} 项，失败 {failed} 项")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
