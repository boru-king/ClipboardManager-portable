"""界面主题：配色、字体、类型图标（Pillow 现画，不依赖图片资源）。"""

from __future__ import annotations

import tkinter.font as tkfont

from PIL import Image, ImageDraw, ImageTk

COLORS = {
    "bg": "#F4F5F7",
    "card": "#FFFFFF",
    "card_hover": "#F7F9FF",
    "selected": "#E4EEFF",
    "border": "#E6E8EB",
    "text": "#1F2328",
    "subtext": "#7A828C",
    "faint": "#AEB6C2",
    "accent": "#2F6FED",
    "accent_soft": "#EAF1FE",
    "danger": "#D93F3F",
    "pinned": "#E8A33D",
    "chip_bg": "#FFFFFF",
    "scroll": "#D9DDE3",
}

KIND_META = {
    "all": ("全部", "#2F6FED"),
    "text": ("文字", "#2F6FED"),
    "image": ("图片", "#16A34A"),
    "video": ("视频", "#DC2626"),
    "file": ("文件", "#6B7280"),
}

_TYPE_ICON_COLOR = {
    "text": "#2F6FED",
    "image": "#16A34A",
    "video": "#DC2626",
    "file": "#6B7280",
}


def pick_font_family(root) -> str:
    try:
        families = set(tkfont.families(root))
    except Exception:
        families = set()
    for name in ("Microsoft YaHei UI", "Microsoft YaHei", "微软雅黑", "Segoe UI", "Arial"):
        if name in families:
            return name
    return "TkDefaultFont"


def font(root, size: int = 10, weight: str = "normal") -> tuple:
    family = getattr(root, "_ui_family", None) or pick_font_family(root)
    return (family, size, weight)


_icon_cache: dict[str, ImageTk.PhotoImage] = {}


def clear_icon_cache() -> None:
    """换 Tk 解释器（比如重建窗口、跑测试）时必须清掉，否则旧 PhotoImage 失效。"""
    _icon_cache.clear()


def type_icon(root, kind: str, size: int = 40) -> ImageTk.PhotoImage:
    key = f"{kind}-{size}"
    if key in _icon_cache:
        return _icon_cache[key]
    color = _TYPE_ICON_COLOR.get(kind, "#6B7280")
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size * 0.22
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=_lighten(color, 0.9))
    pad = size * 0.26
    if kind == "text":
        for i in range(3):
            y = pad + i * (size - 2 * pad) / 2.4
            d.rounded_rectangle(
                [pad, y, size - pad - (size * 0.14 if i == 2 else 0), y + max(1.6, size * 0.075)],
                radius=1, fill=color,
            )
    elif kind == "image":
        d.rounded_rectangle([pad, pad + size * 0.06, size - pad, size - pad], radius=2, outline=color, width=max(1, int(size * 0.055)))
        d.ellipse([size * 0.36, size * 0.36, size * 0.46, size * 0.46], fill=color)
        d.polygon(
            [(pad + size * 0.03, size - pad - size * 0.05),
             (size * 0.44, size * 0.52),
             (size - pad - size * 0.03, size - pad - size * 0.05)],
            fill=color,
        )
    elif kind == "video":
        d.polygon(
            [(size * 0.34, size * 0.26), (size * 0.74, size * 0.5), (size * 0.34, size * 0.74)],
            fill=color,
        )
    else:  # file
        d.rounded_rectangle([size * 0.24, size * 0.18, size * 0.76, size * 0.84], radius=2, outline=color, width=max(1, int(size * 0.055)))
        d.polygon(
            [(size * 0.56, size * 0.18), (size * 0.76, size * 0.38), (size * 0.56, size * 0.38)],
            fill=color,
        )
    photo = ImageTk.PhotoImage(img, master=root)
    _icon_cache[key] = photo
    return photo


def _lighten(hex_color: str, amount: float) -> str:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r = int(r + (255 - r) * amount)
    g = int(g + (255 - g) * amount)
    b = int(b + (255 - b) * amount)
    return f"#{r:02X}{g:02X}{b:02X}"


def rounded_thumb(img, size: int = 52, master=None) -> ImageTk.PhotoImage:
    """把 PIL 图裁成方图并加圆角，用于列表缩略图。master 传 Tk 控件，绑定到正确的解释器。"""
    img = img.copy()
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=8, fill=255)
    x = (size - img.width) // 2
    y = (size - img.height) // 2
    canvas.paste(img.convert("RGBA"), (x, y))
    canvas.putalpha(mask)
    return ImageTk.PhotoImage(canvas, master=master)
