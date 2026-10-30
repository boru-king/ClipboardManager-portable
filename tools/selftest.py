"""自检脚本：不弹主窗口，逐项验证存储 / 搜索 / 图片 / 界面构建。

运行：.venv\\Scripts\\python.exe tools\\selftest.py
"""

from __future__ import annotations

import io
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

failures: list[str] = []


def check(name: str, fn) -> None:
    try:
        detail = fn()
        print(f"  [OK] {name}" + (f"  {detail}" if detail else ""))
    except Exception as exc:
        failures.append(f"{name}: {exc}")
        print(f"  [FAIL] {name}: {exc}")
        traceback.print_exc()


def main() -> int:
    from app import config as config_mod

    tmp = Path(tempfile.mkdtemp(prefix="cm-selftest-"))
    print(f"临时目录 {tmp}")

    cfg = config_mod.Config(tmp / "config.json")
    check("config 默认值", lambda: f"hotkey={cfg.hotkey} max_items={cfg.get('max_items')}")

    from app.db import Store, item_hash

    store = Store(tmp / "t.db")

    def t_basic():
        i1, new1 = store.add(kind="text", source="plain", content="Hello 世界 clipboard", preview="Hello 世界 clipboard", hash=item_hash("text", "a1"))
        i2, new2 = store.add(kind="text", source="plain", content="第二条 Python 剪贴板管理器", preview="第二条 Python 剪贴板管理器", hash=item_hash("text", "a2"))
        i3, new3 = store.add(kind="image", source="bitmap", preview="图", width=100, height=80, size_bytes=1234, hash="img1")
        assert new1 and new2 and new3, (new1, new2, new3)
        _, again = store.add(kind="text", source="plain", content="Hello 世界 clipboard", preview="x", hash=item_hash("text", "a1"))
        assert not again, "同 hash 不应重复插入"
        return f"3 条 + 去重 ok，count={store.count()}"

    check("items 写入 / 去重", t_basic)

    check("FTS 三字以上", lambda: f"{len(store.query(search='clipboard'))} 条")
    check("中文三字", lambda: f"{len(store.query(search='剪贴板'))} 条")
    check("中文两字回退 LIKE", lambda: f"{len(store.query(search='剪贴'))} 条")
    check("英文两字回退", lambda: f"{len(store.query(search='cl'))} 条")
    check("按类型过滤", lambda: f"image={len(store.query(kind='image'))} text={len(store.query(kind='text'))}")
    check("大小写不敏感", lambda: f"{len(store.query(search='HELLO'))} 条")

    def t_purge():
        for i in range(5):
            store.add(kind="text", source="plain", content=f"purge {i}", preview=f"purge {i}", hash=f"p{i}")
        removed = store.purge(3)
        assert store.count() <= 3, store.count()
        return f"清到 {store.count()} 条，删了 {len(removed)} 个文件引用"

    check("容量上限 purge", t_purge)

    from app import images
    from PIL import Image

    def t_dib():
        img = Image.new("RGB", (37, 21))
        for x in range(37):
            for y in range(21):
                img.putpixel((x, y), (x * 7 % 256, y * 11 % 256, (x + y) % 256))
        dib = images.image_to_dib(img)
        back = images.dib_to_image(dib)
        assert back.size == img.size, (back.size, img.size)
        assert list(back.convert("RGB").getdata()) == list(img.getdata()), "像素不一致"
        return f"{len(dib)} 字节"

    check("DIB 往返", t_dib)

    def t_alpha():
        img = Image.new("RGBA", (16, 16), (255, 0, 0, 0))
        img.putpixel((1, 1), (0, 255, 0, 255))
        dib = images.image_to_dib(img)
        back = images.dib_to_image(dib)
        assert back.mode == "RGBA", back.mode
        assert back.getpixel((0, 0))[3] == 0
        assert back.getpixel((1, 1))[3] == 255
        assert images.has_alpha(img) and not images.has_alpha(Image.new("RGB", (4, 4)))
        return "32bpp 透明通道保留"

    check("透明 PNG 往返", t_alpha)

    def t_webp():
        img = Image.new("RGB", (400, 300), (30, 120, 220))
        p = tmp / "t.webp"
        size = images.save_webp(img, p, 85)
        assert p.exists() and size > 0
        assert images.probe_size(p) == (400, 300), images.probe_size(p)
        thumb = tmp / "t_thumb.webp"
        images.make_thumb(img, thumb, 128)
        assert thumb.exists()
        return f"webp {size} 字节"

    check("webp 落盘 / 缩略图", t_webp)

    def t_fit():
        img = Image.new("RGB", (4000, 1000))
        out = images.fit_within(img, 2560)
        assert out.size == (2560, 640), out.size
        return "4000x1000 -> 2560x640"

    check("等比缩放", t_fit)

    # ---------------- 界面 ----------------
    from app.app import ClipboardApp

    app = ClipboardApp(data_dir=tmp)
    try:
        app.store.add(kind="text", source="plain", content="界面测试 文本 one", preview="界面测试 文本 one", hash="ui1")
        app.store.add(kind="video", source="path", preview="movie.mp4", paths=[str(tmp / "movie.mp4")], size_bytes=10_000, hash="ui2")
        app.store.add(kind="image", source="bitmap", preview="图片", file_path=str(tmp / "t.webp"), width=400, height=300, size_bytes=1234, hash="ui3")
        from app.ui.main_window import MainWindow

        win = MainWindow(app)
        app.window = win

        def t_ui():
            app._pump()
            win.root.update()
            win.show()
            win.root.update()
            assert len(win.items) == 3, len(win.items)
            win.render()
            win.root.update()
            win.search_var.set("界面")
            win._do_search()
            win.root.update()
            win.set_kind("image")
            win.root.update()
            win.search_var.set("")
            win.set_kind("all")
            win.root.update()
            win.hide()
            return f"窗口 {win.root.winfo_width()}x{win.root.winfo_height()}"

        check("界面构建 / 搜索 / 切类", t_ui)

        def t_toast():
            app.toast("测试提示")
            win.root.update()
            win.root.after(100)
            return "toast ok"

        check("提示气泡", t_toast)
        win.root.destroy()
    finally:
        store.close()
        app.store.close()

    # ---------------- 剪贴板 ----------------
    try:
        from app import winclip

        def t_clip_text():
            before = winclip.clipboard_sequence()
            winclip.write_text("自检文本 clip-test")
            after = winclip.clipboard_sequence()
            assert after != before, "写入后序列号应变化"
            content = winclip.read_clipboard()
            assert content and content.kind == "text", content
            assert content.text.strip() == "自检文本 clip-test", repr(content.text)
            return "文本写读一致"

        check("剪贴板写入/读取", t_clip_text)

        def t_clip_files():
            f = tmp / "a.txt"
            f.write_text("x", "utf-8")
            winclip.write_files([str(f)])
            content = winclip.read_clipboard()
            assert content and content.kind == "files", content
            assert content.paths == [str(f)], content.paths
            return "文件路径写读一致"

        check("文件清单写入/读取", t_clip_files)

        def t_html():
            winclip.write_text("粗体", '<b>粗体</b>')
            content = winclip.read_clipboard()
            assert content.html and "粗体" in content.html, content.html
            return "CF_HTML 往返"

        check("富文本写入/读取", t_html)

        def t_clip_image():
            from PIL import Image as _I

            img = _I.new("RGB", (64, 48), (10, 200, 90))
            dib = images.image_to_dib(img)
            winclip.write_image(b"", dib)
            content = winclip.read_clipboard()
            assert content and content.kind == "image", content
            assert content.dib, "应能读回 DIB"
            back = images.dib_to_image(content.dib)
            assert back.size == (64, 48), back.size
            assert back.convert("RGB").getpixel((5, 5)) == (10, 200, 90)
            return "位图写读一致"

        check("图片写入/读取", t_clip_image)

        def t_clip_png():
            img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
            img.putpixel((3, 3), (255, 255, 0, 255))
            png = images.encode_png(img)
            winclip.write_image(png, images.image_to_dib(img))
            content = winclip.read_clipboard()
            assert content and content.kind == "image" and content.png, "应优先读回 PNG"
            back = images.png_to_image(content.png)
            assert back.mode == "RGBA" and back.getpixel((3, 3)) == (255, 255, 0, 255)
            return "PNG 格式优先、透明保留"

        check("透明图片写入/读取", t_clip_png)
    except Exception as exc:
        failures.append(f"剪贴板: {exc}")
        print(f"  [FAIL] 剪贴板: {exc}")
        traceback.print_exc()

    print()
    if failures:
        print(f"失败 {len(failures)} 项：")
        for f in failures:
            print("  -", f)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
