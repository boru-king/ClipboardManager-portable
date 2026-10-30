"""图片转换：剪贴板 DIB -> PIL，PIL -> DIB，以及 WebP 落盘和缩略图。"""

from __future__ import annotations

import io
import struct
from pathlib import Path

try:
    from PIL import Image
except ImportError:  # pragma: no cover - 依赖缺失时给可读报错
    Image = None  # type: ignore[assignment]


def _require_pil():
    if Image is None:
        raise RuntimeError("缺少 Pillow，请先安装：pip install pillow")
    return Image


def dib_to_bmp(dib: bytes) -> bytes:
    """把剪贴板 CF_DIB 数据补一个 14 字节文件头，得到合法 BMP。"""
    if len(dib) < 40:
        raise ValueError("DIB 数据太短")
    bi_size = struct.unpack_from("<I", dib, 0)[0]
    palette = 0
    if bi_size == 12:  # BITMAPCOREHEADER
        bit_count = struct.unpack_from("<H", dib, 10)[0]
        if bit_count <= 8:
            palette = (1 << bit_count) * 3
    elif bi_size >= 40:
        bit_count = struct.unpack_from("<H", dib, 14)[0]
        compression = struct.unpack_from("<I", dib, 16)[0]
        colors_used = struct.unpack_from("<I", dib, 32)[0]
        if bit_count <= 8:
            palette = (colors_used or (1 << bit_count)) * 4
        elif compression == 3 and bi_size == 40:  # BI_BITFIELDS，3 个 DWORD 掩码跟在头后
            palette = 12
    offset = 14 + bi_size + palette
    header = struct.pack("<2sIHHI", b"BM", 14 + len(dib), 0, 0, offset)
    return header + dib


def dib_to_image(dib: bytes):
    """CF_DIB -> PIL.Image（BMP 解码）。32bpp 的 alpha 通道单独补回。"""
    Image = _require_pil()
    bmp = dib_to_bmp(dib)
    img = Image.open(io.BytesIO(bmp))
    img.load()
    rebuilt = _rgba_from_32bpp(dib, bmp, img)
    return rebuilt if rebuilt is not None else img


def _rgba_from_32bpp(dib: bytes, bmp: bytes, img):
    """BMP 解码器会丢掉 32 位 BI_RGB 的 alpha，这里按 DIB 头重建。"""
    try:
        bi_size = struct.unpack_from("<I", dib, 0)[0]
        if bi_size < 40:
            return None
        bit_count = struct.unpack_from("<H", dib, 14)[0]
        compression = struct.unpack_from("<I", dib, 16)[0]
        if bit_count != 32 or compression not in (0, 3):
            return None
        width, height = img.size
        bi_height = struct.unpack_from("<i", dib, 8)[0]
        top_down = bi_height < 0
        stride = ((width * 32 + 31) // 32) * 4
        extra = 12 if (compression == 3 and bi_size == 40) else 0
        offset = 14 + bi_size + extra
        raw = bmp[offset:offset + stride * height]
        if len(raw) != stride * height:
            return None
        rows = [raw[i * stride:(i + 1) * stride] for i in range(height)]
        if not top_down:
            rows.reverse()
        buf = bytearray(b"".join(rows))
        buf[0::4], buf[2::4] = buf[2::4], buf[0::4]  # BGRA -> RGBA
        out = Image.frombytes("RGBA", (width, height), bytes(buf))
        lo, _hi = out.getchannel("A").getextrema()
        return out if lo < 255 else out.convert("RGB")
    except Exception:
        return None


def has_alpha(img) -> bool:
    """是否真的带透明像素。"""
    if img.mode not in ("RGBA", "LA", "P", "PA"):
        return False
    try:
        rgba = img.convert("RGBA")
        return rgba.getchannel("A").getextrema()[0] < 255
    except Exception:
        return False


def encode_png(img) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def png_to_image(data: bytes):
    Image = _require_pil()
    img = Image.open(io.BytesIO(data))
    img.load()
    return img


def image_to_dib(img) -> bytes:
    """PIL.Image -> CF_DIB（BMP 去掉文件头，32bpp 带透明 / 24bpp 不透明）。"""
    has_alpha = False
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        lo, _hi = rgba.getchannel("A").getextrema()
        has_alpha = lo < 255
        img = rgba
    if has_alpha:
        bpp = 32
    else:
        img = img.convert("RGB")
        bpp = 24

    w, h = img.size
    stride = ((w * bpp + 31) // 32) * 4
    raw = img.tobytes()
    row_bytes = w * bpp // 8
    rows = []
    for y in range(h - 1, -1, -1):  # BMP 自下而上
        seg = bytearray(raw[y * row_bytes:(y + 1) * row_bytes])
        if bpp == 32:
            seg[0::4], seg[2::4] = seg[2::4], seg[0::4]  # RGBA -> BGRA
        else:
            seg[0::3], seg[2::3] = seg[2::3], seg[0::3]  # RGB -> BGR
        seg += b"\x00" * (stride - row_bytes)
        rows.append(bytes(seg))
    header = struct.pack("<IiiHHIIiiII", 40, w, h, 1, bpp, 0, 0, 0, 0, 0, 0)
    return header + b"".join(rows)


def fit_within(img, max_edge: int):
    """长边超过 max_edge 就等比缩小。"""
    w, h = img.size
    longest = max(w, h)
    if max_edge and longest > max_edge:
        ratio = max_edge / longest
        img = img.resize((max(1, int(w * ratio)), max(1, int(h * ratio))), Image.LANCZOS)
    return img


def save_webp(img, path: Path, quality: int = 85) -> int:
    """存成 WebP，返回文件字节数。透明图保留 alpha。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if "A" in img.getbands() else "RGB")
    img.save(path, "WEBP", quality=quality, method=4)
    return path.stat().st_size


def make_thumb(img, path: Path, size: int = 128) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    thumb = img.copy()
    thumb.thumbnail((size, size), Image.LANCZOS)
    if thumb.mode not in ("RGB", "RGBA"):
        thumb = thumb.convert("RGBA")
    thumb.save(path, "WEBP", quality=82, method=4)


def load_thumb_pil(path: Path, size: int = 64):
    """读缩略图并缩放到界面需要的大小。"""
    Image = _require_pil()
    img = Image.open(path)
    img.load()
    img.thumbnail((size, size), Image.LANCZOS)
    return img


def probe_size(path: Path) -> tuple[int, int]:
    try:
        Image = _require_pil()
        with Image.open(path) as im:
            return im.size
    except Exception:
        return (0, 0)
