"""Windows 剪贴板底层读写，纯 ctypes 实现，不依赖第三方库。

支持格式：
  CF_UNICODETEXT / CF_TEXT  纯文本
  HTML Format               富文本片段（Word、浏览器等）
  PNG (注册格式)            带透明的高质量位图（Chromium 系）
  CF_DIB / CF_DIBV5         标准 DIB 位图
  CF_HDROP                  文件/文件夹引用（视频、图片文件走这里）
"""

from __future__ import annotations

import ctypes
import re
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass, field

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

CF_TEXT = 1
CF_BITMAP = 2
CF_DIB = 8
CF_UNICODETEXT = 13
CF_HDROP = 15
CF_DIBV5 = 17

GMEM_MOVEABLE = 0x0002
GMEM_ZEROINIT = 0x0040

# ---- 函数原型（64 位下句柄必须声明，否则会被截断成 32 位） ----
user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.CloseClipboard.argtypes = []
user32.CloseClipboard.restype = wintypes.BOOL
user32.EmptyClipboard.argtypes = []
user32.EmptyClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
user32.SetClipboardData.restype = wintypes.HANDLE
user32.IsClipboardFormatAvailable.argtypes = [wintypes.UINT]
user32.IsClipboardFormatAvailable.restype = wintypes.BOOL
user32.GetClipboardSequenceNumber.argtypes = []
user32.GetClipboardSequenceNumber.restype = wintypes.DWORD
user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
user32.RegisterClipboardFormatW.restype = wintypes.UINT

kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = wintypes.HANDLE
kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
kernel32.GlobalUnlock.restype = wintypes.BOOL
kernel32.GlobalSize.argtypes = [wintypes.HANDLE]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [wintypes.HANDLE]
kernel32.GlobalFree.restype = wintypes.HANDLE

shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
shell32.DragQueryFileW.restype = wintypes.UINT

CF_HTML = user32.RegisterClipboardFormatW("HTML Format")
CF_PNG = user32.RegisterClipboardFormatW("PNG")


@dataclass
class ClipContent:
    """一次剪贴板快照。kind 初步取 text / image / files，细分交给上层。"""

    kind: str
    text: str = ""
    html: str = ""
    png: bytes | None = None
    dib: bytes | None = None
    paths: list[str] = field(default_factory=list)


class ClipboardBusy(RuntimeError):
    """剪贴板被其它进程占用，重试后仍打不开。"""


def clipboard_sequence() -> int:
    """剪贴板修改序号；内容每变一次 +1。"""
    return user32.GetClipboardSequenceNumber()


def _open(retries: int = 10, delay: float = 0.015) -> bool:
    for _ in range(retries):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def _read_bytes(fmt: int) -> bytes | None:
    if not user32.IsClipboardFormatAvailable(fmt):
        return None
    handle = user32.GetClipboardData(fmt)
    if not handle:
        return None
    size = kernel32.GlobalSize(handle)
    if not size:
        return None
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.string_at(ptr, size)
    finally:
        kernel32.GlobalUnlock(handle)


def _read_text() -> str | None:
    if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
        return None
    handle = user32.GetClipboardData(CF_UNICODETEXT)
    if not handle:
        return None
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        return None
    try:
        return ctypes.wstring_at(ptr)
    finally:
        kernel32.GlobalUnlock(handle)


def _read_hdrop() -> list[str]:
    handle = user32.GetClipboardData(CF_HDROP)
    if not handle:
        return []
    count = shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)
    if not count:
        return []
    paths: list[str] = []
    for i in range(count):
        need = shell32.DragQueryFileW(handle, i, None, 0)
        if not need:
            continue
        buf = ctypes.create_unicode_buffer(need + 1)
        shell32.DragQueryFileW(handle, i, buf, need + 1)
        if buf.value:
            paths.append(buf.value)
    return paths


def _extract_html(raw: bytes | None) -> str:
    """从 CF_HTML 的头部偏移里切出片段。"""
    if not raw:
        return ""
    try:
        head = raw[:4096].decode("latin-1", "ignore")
        m_start = re.search(r"StartFragment:(\d+)", head)
        m_end = re.search(r"EndFragment:(\d+)", head)
        if m_start and m_end:
            start, end = int(m_start.group(1)), int(m_end.group(1))
            if 0 < start < end <= len(raw):
                return raw[start:end].decode("utf-8", "replace")
        whole = raw.decode("utf-8", "ignore")
        i = whole.find("<!--StartFragment-->")
        j = whole.find("<!--EndFragment-->")
        if i >= 0 and j > i:
            return whole[i + len("<!--StartFragment-->"): j]
    except Exception:
        return ""
    return ""


def read_clipboard() -> ClipContent | None:
    """读取当前剪贴板。没有任何可用内容时返回 None。"""
    if not _open():
        return None
    try:
        # 1) 文件引用优先：复制视频/图片文件时，资源管理器也会带一串路径文本
        if user32.IsClipboardFormatAvailable(CF_HDROP):
            paths = _read_hdrop()
            if paths:
                return ClipContent(kind="files", paths=paths)

        # 2) 文本 + 富文本
        text = _read_text()
        if text and text.strip():
            html = _extract_html(_read_bytes(CF_HTML))
            return ClipContent(kind="text", text=text, html=html)

        # 3) 位图：优先 PNG 注册格式（无损带透明），退回 DIB
        png = _read_bytes(CF_PNG)
        if png and len(png) > 8 and png[:8] == b"\x89PNG\r\n\x1a\n":
            return ClipContent(kind="image", png=png)

        dib = _read_bytes(CF_DIB) or _read_bytes(CF_DIBV5)
        if dib and len(dib) > 40:
            return ClipContent(kind="image", dib=dib)

        return None
    finally:
        user32.CloseClipboard()


# ---------------------------------------------------------------- 写入


def _begin_write() -> None:
    if not _open():
        raise ClipboardBusy("剪贴板被其它程序占用")
    if not user32.EmptyClipboard():
        user32.CloseClipboard()
        raise ClipboardBusy("清空剪贴板失败")


def _set_format(fmt: int, data: bytes) -> bool:
    if not data:
        return False
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE | GMEM_ZEROINIT, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    if not ptr:
        kernel32.GlobalFree(handle)
        return False
    try:
        ctypes.memmove(ptr, data, len(data))
    finally:
        kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)
        return False
    return True


def _make_html(fragment: str) -> bytes:
    """拼出 CF_HTML 要求的完整带偏移头部的数据。"""
    pre = "<html><body>\r\n<!--StartFragment-->"
    post = "<!--EndFragment-->\r\n</body></html>"
    header_fmt = (
        "Version:0.9\r\n"
        "StartHTML:{:010d}\r\n"
        "EndHTML:{:010d}\r\n"
        "StartFragment:{:010d}\r\n"
        "EndFragment:{:010d}\r\n"
    )
    n_header = len(header_fmt.format(0, 0, 0, 0).encode("ascii"))
    start_html = n_header
    start_frag = start_html + len(pre.encode("utf-8"))
    frag_bytes = fragment.encode("utf-8")
    end_frag = start_frag + len(frag_bytes)
    end_html = end_frag + len(post.encode("utf-8"))
    header = header_fmt.format(start_html, end_html, start_frag, end_frag).encode("ascii")
    return header + pre.encode("utf-8") + frag_bytes + post.encode("utf-8")


def write_text(text: str, html: str = "") -> None:
    _begin_write()
    try:
        _set_format(CF_UNICODETEXT, text.encode("utf-16-le") + b"\x00\x00")
        if html:
            _set_format(CF_HTML, _make_html(html))
    finally:
        user32.CloseClipboard()


def write_dib(dib: bytes) -> None:
    _begin_write()
    try:
        _set_format(CF_DIB, dib)
    finally:
        user32.CloseClipboard()


def write_image(png: bytes, dib: bytes) -> None:
    """图片写入：透明图带 PNG 注册格式（浏览器/Office 会优先取它），同时给标准 DIB。"""
    _begin_write()
    try:
        if png:
            _set_format(CF_PNG, png)
        _set_format(CF_DIB, dib)
    finally:
        user32.CloseClipboard()


def write_files(paths: list[str]) -> None:
    """写入文件引用（资源管理器/微信/Word 里粘贴出来就是文件）。"""
    if not paths:
        return
    blob = "".join(p + "\x00" for p in paths) + "\x00"
    # DROPFILES: pFiles(4) pt(8) fNC(4) fWide(4) = 20 字节
    data = struct.pack("<IiiII", 20, 0, 0, 0, 1) + blob.encode("utf-16-le")
    _begin_write()
    try:
        _set_format(CF_HDROP, data)
    finally:
        user32.CloseClipboard()
