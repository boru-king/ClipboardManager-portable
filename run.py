"""入口。开发时：python run.py；打包后由 PyInstaller 以 --noconsole 启动。

这里额外做一层兜底：--noconsole 打包后没有控制台，启动阶段一旦抛异常
（连 app 都还没 import 完）用户只会看到进程一闪而过。所以把异常写进
临时目录的 boot.log，再弹一个 MessageBox。
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path


def _boot_log_path() -> Path:
    base = os.environ.get("TEMP") or tempfile.gettempdir()
    return Path(base) / "ClipboardManager-boot.log"


def _write_boot_log(text: str) -> None:
    for target in (_boot_log_path(), Path(tempfile.gettempdir()) / "ClipboardManager-boot.log"):
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
            return
        except OSError:
            continue


def _run() -> int:
    if not getattr(sys, "frozen", False):
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    from app.app import main

    return main()


if __name__ == "__main__":
    try:
        raise SystemExit(_run())
    except SystemExit:
        raise
    except BaseException:
        tb = traceback.format_exc()
        _write_boot_log(tb)
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, tb[-1500:], "剪贴板管理器启动失败", 0x10)
        except Exception:
            pass
        raise SystemExit(1)
