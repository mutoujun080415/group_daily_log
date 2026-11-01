"""封面图存储。

用户在 WebUI 上传的封面放在 ``data/group_daily_log/covers/``，元信息（焦点、
高度、更新时间）写在 ``data/group_daily_log/cover.json``。渲染时按元信息裁切，
所以换焦点/高度不需要重新上传。
"""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

DATA_DIR = Path("data") / "group_daily_log"
COVER_DIR = DATA_DIR / "covers"
META_FILE = DATA_DIR / "cover.json"
IMAGE_NAME = "cover.png"

MAX_BYTES = 8 * 1024 * 1024
MAX_WIDTH = 1600
DEFAULT_HEIGHT = 300
MIN_HEIGHT = 200
MAX_HEIGHT = 380
DEFAULT_FOCUS = "center"
FOCUS_CHOICES = (
    "top-left",
    "top",
    "top-right",
    "left",
    "center",
    "right",
    "bottom-left",
    "bottom",
    "bottom-right",
)


def _resolve(relative: str) -> Path:
    """把 ``covers/cover.png`` 这类相对路径解析成实际路径。"""
    path = Path(relative)
    return path if path.is_absolute() else DATA_DIR / path


def load_meta() -> dict[str, Any]:
    """读取封面元信息；没有封面或文件丢了都返回空字典。"""
    try:
        raw = META_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    relative = str(data.get("path") or "").strip()
    if not relative or not _resolve(relative).is_file():
        return {}
    focus = str(data.get("focus") or DEFAULT_FOCUS)
    try:
        height = int(data.get("height") or DEFAULT_HEIGHT)
    except (TypeError, ValueError):
        height = DEFAULT_HEIGHT
    return {
        "path": relative,
        "focus": focus if focus in FOCUS_CHOICES else DEFAULT_FOCUS,
        "height": max(MIN_HEIGHT, min(MAX_HEIGHT, height)),
        "updated_at": str(data.get("updated_at") or ""),
    }


def save_meta(meta: dict[str, Any]) -> dict[str, Any]:
    """写入元信息并返回规范化后的结果。"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "path": str(meta.get("path") or f"covers/{IMAGE_NAME}"),
        "focus": str(meta.get("focus") or DEFAULT_FOCUS),
        "height": int(meta.get("height") or DEFAULT_HEIGHT),
        "updated_at": str(meta.get("updated_at") or _now()),
    }
    payload["focus"] = payload["focus"] if payload["focus"] in FOCUS_CHOICES else DEFAULT_FOCUS
    payload["height"] = max(MIN_HEIGHT, min(MAX_HEIGHT, payload["height"]))
    META_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def image_path() -> Path | None:
    """当前封面的实际路径；没有就返回 None。"""
    meta = load_meta()
    if not meta:
        return None
    path = _resolve(meta["path"])
    return path if path.is_file() else None


def save_upload(data: bytes) -> dict[str, Any]:
    """保存上传的封面图。

    Args:
        data: 原始图片字节。

    Returns:
        规范化后的元信息。

    Raises:
        ValueError: 太大或不是可识别的图片。
    """
    if not data:
        raise ValueError("没有收到图片数据")
    if len(data) > MAX_BYTES:
        raise ValueError(f"图片太大了（上限 {MAX_BYTES // (1024 * 1024)} MB）")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("这不是能识别的图片格式") from exc

    image = image.convert("RGB")
    if image.width > MAX_WIDTH:
        ratio = MAX_WIDTH / image.width
        image = image.resize((MAX_WIDTH, int(round(image.height * ratio))), Image.LANCZOS)

    COVER_DIR.mkdir(parents=True, exist_ok=True)
    image.save(COVER_DIR / IMAGE_NAME, "PNG")

    previous = load_meta()
    return save_meta(
        {
            "path": f"covers/{IMAGE_NAME}",
            "focus": previous.get("focus") or DEFAULT_FOCUS,
            "height": previous.get("height") or DEFAULT_HEIGHT,
            "updated_at": _now(),
        }
    )


def update(focus: str | None = None, height: int | None = None) -> dict[str, Any]:
    """只改焦点/高度，不换图。"""
    meta = load_meta()
    if not meta:
        raise ValueError("还没有上传封面")
    if focus is not None:
        meta["focus"] = focus
    if height is not None:
        meta["height"] = height
    meta["updated_at"] = _now()
    return save_meta(meta)


def clear() -> None:
    """删除封面与元信息。"""
    path = image_path()
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass
    try:
        META_FILE.unlink()
    except OSError:
        pass


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
