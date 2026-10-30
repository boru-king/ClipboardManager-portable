"""打包脚本。

产物（默认两个都出）：
  dist\\ClipboardManager.exe              单文件版，方便分发
  dist\\ClipboardManager-portable\\       文件夹版，启动快、不依赖解压，单文件版被拦时用它

用法：
  python build.py                # 两个都打
  python build.py --onefile      # 只打单文件
  python build.py --portable     # 只打文件夹版
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
NAME = "ClipboardManager"

EXCLUDES = [
    "numpy", "pandas", "matplotlib", "scipy", "IPython", "pytest",
    "setuptools", "pip", "wheel", "unittest", "pydoc_data", "lib2to3",
    "PIL.ImageQt", "PIL.ImageShow", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "test", "sqlite3.test", "tkinter.test", "asyncio", "multiprocessing",
]

# pystray 的后端是运行时按平台动态导入的，静态分析看不到
HIDDEN = ["pystray._win32", "pystray._util.win32", "PIL._tkinter_finder"]


def make_icon() -> Path:
    BUILD.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT))
    from app.tray import make_icon as draw

    img = draw(256)
    path = BUILD / "icon.ico"
    img.save(
        path,
        format="ICO",
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    return path


def _run_pyinstaller(icon: Path, *, onefile: bool, dist: Path, work: Path, spec: Path) -> int:
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile" if onefile else "--onedir",
        "--noconsole",
        "--name", NAME,
        "--icon", str(icon),
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(spec),
    ]
    for mod in EXCLUDES:
        cmd += ["--exclude-module", mod]
    for mod in HIDDEN:
        cmd += ["--hidden-import", mod]
    cmd.append(str(ROOT / "run.py"))
    print(" ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(ROOT)).returncode


def build_onefile(icon: Path) -> int:
    code = _run_pyinstaller(icon, onefile=True, dist=DIST, work=BUILD / "pyi", spec=BUILD)
    exe = DIST / f"{NAME}.exe"
    if code == 0 and exe.exists():
        print(f"  单文件：{exe}  ({exe.stat().st_size / 1024 / 1024:.1f} MB)", flush=True)
    return code


def build_portable(icon: Path) -> int:
    staging = DIST / "portable_staging"
    code = _run_pyinstaller(icon, onefile=False, dist=staging, work=BUILD / "pyi_dir", spec=BUILD / "portable_spec")
    if code != 0:
        return code
    src = staging / NAME
    dst = DIST / f"{NAME}-portable"
    if dst.exists():
        try:
            shutil.rmtree(dst)
        except PermissionError:
            print(
                f"\n[!] {dst} 里有文件被占用（通常是程序正在运行）。"
                "请先从托盘退出程序，再重新打包。",
                file=sys.stderr,
            )
            shutil.rmtree(staging, ignore_errors=True)
            return 1
    shutil.move(str(src), str(dst))
    shutil.rmtree(staging, ignore_errors=True)
    total = sum(f.stat().st_size for f in dst.rglob("*") if f.is_file())
    print(f"  文件夹版：{dst}  ({total / 1024 / 1024:.1f} MB, {len(list(dst.rglob('*')))} 项)", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = set(sys.argv[1:] if argv is None else argv)
    want_one = "--portable" not in args
    want_dir = "--onefile" not in args
    icon = make_icon()

    if want_one and build_onefile(icon) != 0:
        print("单文件打包失败", file=sys.stderr)
        return 1
    if want_dir and build_portable(icon) != 0:
        print("文件夹版打包失败", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
