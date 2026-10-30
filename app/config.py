"""配置读写：JSON 文件，默认放在 %APPDATA%\\ClipboardManager。"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

APP_NAME = "ClipboardManager"

DEFAULTS: dict[str, Any] = {
    # 热键：mods 取 ctrl / alt / shift / win，key 取 a-z、0-9、f1-f12
    "hotkey": {"mods": ["ctrl", "alt"], "key": "v"},
    # 轮询间隔（毫秒）。序列号查询几乎零开销，300ms 足够灵敏
    "poll_interval_ms": 300,
    # 最多保留多少条（置顶的永不清理）
    "max_items": 3000,
    # 图片：存 WebP，长边超过 max_image_edge 先缩放
    "image_quality": 85,
    "max_image_edge": 2560,
    # 选完条目后自动粘贴到之前的窗口（只在「选择后关闭窗口」开着时生效）
    "auto_paste": True,
    # 鼠标单击条目就复制（关掉则单击只选中，双击/回车才复制）
    "click_to_paste": True,
    # 选择后是否关闭窗口：默认 False —— 复制完窗口留着，自己切回去按 Ctrl+V
    "close_after_pick": False,
    # 窗口隐藏后到发送 Ctrl+V 的延迟（毫秒），给目标窗口抢回焦点留时间
    "paste_delay_ms": 260,
    # 启动时只驻留托盘，不弹主窗口
    "start_minimized": True,
    # 首次启动时弹一次托盘位置的提示（之后不再弹）
    "tray_tip_shown": False,
    # 受限环境（%APPDATA% 不可写）已经提示过一次
    "restricted_tip_shown": False,
    # 界面缩放（高分屏可调 1.25 / 1.5）
    "ui_scale": 1.0,
    # 单条文字最大长度，超出截断入库
    "max_text_chars": 2_000_000,
}


def exe_root() -> Path:
    """程序所在目录（打包后是 exe 所在目录）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """便携模式（存在 portable.flag）用程序目录，否则用 %APPDATA%。"""
    root = exe_root()
    if (root / "portable.flag").exists():
        return root / "data"
    base = os.environ.get("APPDATA") or str(Path.home())
    return Path(base) / APP_NAME


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or (data_dir() / "config.json")
        self.data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            # utf-8-sig：记事本 / PowerShell 另存的文件可能带 BOM
            raw = json.loads(self.path.read_text("utf-8-sig"))
            if isinstance(raw, dict):
                self.data = _merge(DEFAULTS, raw)
        except (OSError, ValueError):
            self.data = copy.deepcopy(DEFAULTS)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), "utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value

    # --- 便捷属性 ---
    @property
    def hotkey(self) -> dict:
        hk = self.data.get("hotkey") or {}
        mods = [m for m in hk.get("mods", []) if m in ("ctrl", "alt", "shift", "win")]
        key = str(hk.get("key", "v")).lower()
        return {"mods": mods or ["ctrl", "alt"], "key": key or "v"}
