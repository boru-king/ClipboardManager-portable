"""剪贴板监听：后台线程轮询序列号，只有内容变化时才真正读取。"""

from __future__ import annotations

import threading
from typing import Callable

from . import winclip
from .winclip import ClipContent


class ClipboardWatcher(threading.Thread):
    def __init__(
        self,
        on_content: Callable[[ClipContent], None],
        interval: float = 0.3,
    ) -> None:
        super().__init__(name="clipboard-watcher", daemon=True)
        self._on_content = on_content
        self._interval = max(0.1, interval)
        self._stop = threading.Event()
        self._ignore_until = -1
        self._lock = threading.Lock()
        self.last_error = ""

    def ignore_self_writes(self) -> None:
        """自己写剪贴板前调用：跳过接下来几次序列号变化，避免自录。"""
        with self._lock:
            try:
                self._ignore_until = winclip.clipboard_sequence() + 8
            except Exception:
                self._ignore_until = -1

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            last_seq = winclip.clipboard_sequence()
        except Exception:
            last_seq = 0
        while not self._stop.is_set():
            if self._stop.wait(self._interval):
                break
            try:
                seq = winclip.clipboard_sequence()
            except Exception:
                continue
            if seq == last_seq:
                continue
            last_seq = seq
            with self._lock:
                if seq <= self._ignore_until:
                    continue
            try:
                content = winclip.read_clipboard()
            except Exception as exc:  # 剪贴板被独占等偶发问题
                self.last_error = str(exc)
                continue
            if not content:
                continue
            try:
                self._on_content(content)
            except Exception as exc:
                self.last_error = str(exc)


def classify_files(paths: list[str]) -> str:
    """文件清单归类到 image / video / file。"""
    if not paths:
        return "file"
    exts = {p.rsplit(".", 1)[-1].lower() for p in paths if "." in p}
    if exts and exts <= VIDEO_EXTS:
        return "video"
    if exts and exts <= IMAGE_EXTS:
        return "image"
    return "file"


VIDEO_EXTS = {
    "mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg",
    "ts", "3gp", "rmvb", "rm", "vob", "m2ts", "ogv",
}
AUDIO_EXTS = {"mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "ape", "opus"}
IMAGE_EXTS = {
    "png", "jpg", "jpeg", "gif", "bmp", "webp", "tif", "tiff", "ico", "heic",
    "heif", "avif", "jfif", "psd", "svg",
}
