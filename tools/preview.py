"""界面预览：塞几条样例数据，直接打开主窗口，便于截图检查排版。

运行：.venv\\Scripts\\python.exe tools\\preview.py [秒数]
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402

from app import images  # noqa: E402
from app.app import ClipboardApp  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402


def seed(app: ClipboardApp) -> None:
    store = app.store
    samples = [
        ("text", "plain", "https://github.com/anthropics/claude-code 这个是仓库地址，记得看 README", "https://github.com/anthropics/claude-code 这个是仓库地址，记得看 README", ""),
        ("text", "plain", "会议纪要：\n1. 确认剪贴板管理器的热键方案\n2. 图片走 WebP 压缩，默认质量 85\n3. 文件和视频只存路径引用", "会议纪要：1. 确认剪贴板管理器的热键方案 2. 图片走 WebP 压缩", ""),
        ("text", "plain", "def fibonacci(n):\n    a, b = 0, 1\n    for _ in range(n):\n        yield a\n        a, b = b, a + b", "def fibonacci(n): a, b = 0, 1 ...", ""),
        ("text", "html", "加粗的标题和一段普通说明文字，用来测试富文本类型的展示效果。", "加粗的标题和一段普通说明文字，用来测试富文本类型的展示效果。", "<b>加粗的标题</b>"),
        ("text", "plain", "C:\\Users\\boruz\\Desktop\\demo-python\\app\\app.py", "C:\\Users\\boruz\\Desktop\\demo-python\\app\\app.py", ""),
    ]
    for kind, source, content, preview, html in samples:
        store.add(kind=kind, source=source, content=content, preview=preview, html=html,
                  hash=f"preview-{abs(hash(content))}")

    # 图片：一张渐变图，模拟截图
    img = Image.new("RGB", (960, 540))
    draw = ImageDraw.Draw(img)
    for y in range(540):
        draw.line([(0, y), (960, y)], fill=(30 + y // 8, 90 + y // 12, 200 - y // 6))
    draw.rectangle([60, 60, 420, 300], fill=(255, 255, 255))
    draw.ellipse([520, 120, 820, 420], fill=(255, 210, 90))
    path = app.image_dir / "preview.webp"
    thumb = app.thumb_dir / "preview.webp"
    size = images.save_webp(img, path, 85)
    images.make_thumb(img, thumb, 128)
    store.add(kind="image", source="bitmap", preview="图片 960×540", file_path=str(path),
              thumb_path=str(thumb), width=960, height=540, size_bytes=size, hash="preview-img")

    # 视频 / 文件：只有路径引用
    store.add(kind="video", source="path", preview="录屏 2026-10-29.mp4",
              paths=[r"C:\Users\boruz\Videos\录屏 2026-10-29.mp4"], size_bytes=48 * 1024 * 1024,
              hash="preview-video")
    store.add(kind="file", source="path", preview="需求说明.docx",
              paths=[r"C:\Users\boruz\Desktop\需求说明.docx"], size_bytes=26 * 1024,
              hash="preview-file")
    store.add(kind="file", source="path", preview="预算表.xlsx",
              paths=[r"C:\Users\boruz\Desktop\预算表.xlsx", r"C:\Users\boruz\Desktop\明细.xlsx"],
              size_bytes=88 * 1024, hash="preview-file2")
    store.toggle_pin(store.query(limit=1)[0]["id"] if store.query(limit=1) else 1)


def main() -> int:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 9
    tmp = Path(tempfile.mkdtemp(prefix="cm-preview-"))
    app = ClipboardApp(data_dir=tmp)
    seed(app)

    win = MainWindow(app)
    app.window = win
    app._apply_window_scale()
    win.show()
    win.root.update()
    win.root.lift()

    rect_file = ROOT / "build" / "window_rect.json"
    rect_file.parent.mkdir(parents=True, exist_ok=True)

    def dump_rect() -> None:
        win.root.update_idletasks()
        rect = {
            "x": win.root.winfo_rootx(),
            "y": win.root.winfo_rooty(),
            "w": win.root.winfo_width(),
            "h": win.root.winfo_height(),
        }
        rect_file.write_text(json.dumps(rect), "utf-8")
        print("RECT", rect)

    win.root.after(600, dump_rect)
    win.root.after(int(seconds * 1000), win.root.destroy)
    win.root.mainloop()
    try:
        app.store.close()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
