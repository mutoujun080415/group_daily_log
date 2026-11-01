"""日报海报渲染器。

纯 Pillow 实现，不依赖浏览器或外部服务。所有布局按 750 逻辑像素设计，
内部以 2 倍超采样绘制后再缩放，保证文字与圆角边缘干净。
"""

from __future__ import annotations

import json
import re
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

from .models import DailyReport, strip_markup


def _plugin_version() -> str:
    """读取本插件 manifest.json 中的版本号。

    页脚原来写死 ``v1.0.0``，插件升级后会和实际版本脱节；这里直接读
    manifest，读不到时返回空串，页脚只显示时间戳。
    """
    try:
        raw = (Path(__file__).with_name("manifest.json")).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, ValueError):
        return ""
    return str(data.get("version") or "").strip()

# ---------------------------------------------------------------- 调色板

BG_TOP = "#F4F8FF"
BG_BOTTOM = "#E7EEFF"
BLOB_BLUE = "#D9E6FF"
BLOB_LILAC = "#E7E2FF"

CARD = "#FFFFFF"
INK = "#16233F"
BODY = "#586A8C"
MUTED = "#93A2BE"
FAINT = "#B9C5DA"
LINE = "#E9EFFA"
LINE_SOFT = "#F2F6FD"

ACCENT = "#2F63E8"
ACCENT_DARK = "#1B3F8F"
ACCENT_SOFT = "#E9F0FF"
ACCENT_PALE = "#F6F9FF"

LAV = "#F5F2FF"
LAV_LINE = "#8B7BF0"
GOLD = "#CDA96A"

SCALE = 2
PAGE_W = 750
PAGE_MARGIN = 18
CARD_RADIUS = 26
CARD_PAD = 34
CONTENT_W = PAGE_W - PAGE_MARGIN * 2 - CARD_PAD * 2  # 646

_OPEN_QUOTES = "（【「《“‘([{"
_CLOSE_QUOTES = "，。、；：？！）】」』》”’%,.!?;:…"


def hex_rgb(value: str) -> tuple[int, int, int]:
    """把 ``#RRGGBB`` 转成 RGB 元组。"""
    text = value.lstrip("#")
    return tuple(int(text[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _blend(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    """给颜色附加透明度。"""
    return (*color, alpha)


class Canvas:
    """按逻辑坐标绘制、内部超采样的画布封装。"""

    def __init__(self, width: int, height: int, scale: int = SCALE) -> None:
        self.scale = scale
        self.width = width
        self.height = height
        self.image = Image.new("RGBA", (width * scale, height * scale), (255, 255, 255, 0))
        self.draw = ImageDraw.Draw(self.image)
        self._fonts: dict[tuple[int, bool], ImageFont.FreeTypeFont] = {}

    def font(self, size: float, bold: bool = False) -> ImageFont.FreeTypeFont:
        """获取指定逻辑字号的字体。"""
        key = (max(8, int(round(size))), bold)
        if key not in self._fonts:
            self._fonts[key] = _load_font(key[0] * self.scale, bold)
        return self._fonts[key]

    def rounded(
        self,
        box: tuple[float, float, float, float],
        radius: float,
        *,
        fill: Any = None,
        outline: Any = None,
        width: float = 1,
    ) -> None:
        """绘制圆角矩形。"""
        x1, y1, x2, y2 = box
        self.draw.rounded_rectangle(
            (x1 * self.scale, y1 * self.scale, x2 * self.scale, y2 * self.scale),
            radius=radius * self.scale,
            fill=fill,
            outline=outline,
            width=max(1, int(round(width * self.scale))),
        )

    def rect(
        self,
        box: tuple[float, float, float, float],
        *,
        fill: Any = None,
        outline: Any = None,
        width: float = 1,
    ) -> None:
        """绘制直角矩形。"""
        x1, y1, x2, y2 = box
        self.draw.rectangle(
            (x1 * self.scale, y1 * self.scale, x2 * self.scale, y2 * self.scale),
            fill=fill,
            outline=outline,
            width=max(1, int(round(width * self.scale))),
        )

    def line(self, points: Iterable[tuple[float, float]], *, fill: Any, width: float = 1) -> None:
        """绘制折线。"""
        scaled = [(x * self.scale, y * self.scale) for x, y in points]
        self.draw.line(scaled, fill=fill, width=max(1, int(round(width * self.scale))))

    def ellipse(self, box: tuple[float, float, float, float], *, fill: Any = None) -> None:
        """绘制椭圆。"""
        x1, y1, x2, y2 = box
        self.draw.ellipse(
            (x1 * self.scale, y1 * self.scale, x2 * self.scale, y2 * self.scale), fill=fill
        )

    def text(
        self,
        xy: tuple[float, float],
        content: str,
        size: float,
        *,
        bold: bool = False,
        color: str = BODY,
        anchor: str = "la",
    ) -> None:
        """绘制单行文本。"""
        self.draw.text(
            (xy[0] * self.scale, xy[1] * self.scale),
            content,
            font=self.font(size, bold),
            fill=hex_rgb(color),
            anchor=anchor,
        )

    def measure(self, content: str, size: float, bold: bool = False) -> float:
        """测量文本宽度（逻辑像素）。"""
        return self.font(size, bold).getlength(content) / self.scale

    def paste(self, image: Image.Image, xy: tuple[float, float]) -> None:
        """粘贴图片。"""
        self.image.alpha_composite(
            image.convert("RGBA"), (int(xy[0] * self.scale), int(xy[1] * self.scale))
        )


_FONT_CANDIDATES: list[tuple[str, int]] = [
    ("C:/Windows/Fonts/msyh.ttc", 0),
    ("C:/Windows/Fonts/msyh.ttf", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
]
_FONT_CANDIDATES_BOLD: list[tuple[str, int]] = [
    ("C:/Windows/Fonts/msyhbd.ttc", 0),
    ("C:/Windows/Fonts/msyhbd.ttf", 0),
    ("C:/Windows/Fonts/simhei.ttf", 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
    ("/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", 0),
]


def _load_font(pixel_size: int, bold: bool) -> ImageFont.FreeTypeFont:
    """按优先级加载中文字体，全部缺失时退回 Pillow 默认字体。"""
    for path, index in _FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, pixel_size, index=index)
            except OSError:
                continue
    return ImageFont.load_default(pixel_size)  # type: ignore[return-value]


def _segments(text: str) -> list[tuple[str, bool]]:
    """把 ``**加粗**`` 文本拆成 (片段, 是否加粗) 列表。"""
    parts = re.split(r"(\*\*.+?\*\*)", str(text or ""))
    result: list[tuple[str, bool]] = []
    for part in parts:
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            result.append((part[2:-2], True))
        else:
            result.append((part.replace("*", ""), False))
    return result


def wrap_rich(
    canvas: Canvas,
    text: str,
    size: float,
    max_width: float,
    *,
    max_lines: int = 0,
) -> list[list[tuple[str, bool]]]:
    """按宽度折行，保留加粗分段。

    Args:
        canvas: 画布。
        text: 可含 ``**加粗**`` 的文本。
        size: 字号。
        max_width: 行宽。
        max_lines: 最多行数，0 表示不限制；超出时补省略号。

    Returns:
        每行由 ``(文字, 是否加粗)`` 片段组成。
    """
    chars: list[tuple[str, bool]] = []
    for segment, bold in _segments(text):
        chars.extend((char, bold) for char in segment)

    lines: list[list[tuple[str, bool]]] = []
    current: list[tuple[str, bool]] = []
    width = 0.0
    truncated = False

    for index, (char, bold) in enumerate(chars):
        if char == "\n":
            lines.append(current)
            current, width = [], 0.0
            continue
        char_width = canvas.measure(char, size, bold)
        if current and width + char_width > max_width and char not in _CLOSE_QUOTES:
            lines.append(current)
            current, width = [], 0.0
            if max_lines and len(lines) >= max_lines:
                truncated = True
                break
        if char == " " and not current:
            continue
        current.append((char, bold))
        width += char_width

    if current and not truncated:
        lines.append(current)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    if truncated:
        if not lines:
            lines = [[("…", False)]]
        else:
            lines[-1] = lines[-1][:-1] + [("…", False)]
    return lines


def draw_rich(
    canvas: Canvas,
    xy: tuple[float, float],
    text: str,
    size: float,
    max_width: float,
    *,
    color: str = BODY,
    bold_color: str = INK,
    line_height: float | None = None,
    max_lines: int = 0,
) -> float:
    """绘制一段可折行、可加粗的文本，返回占用高度。"""
    lines = wrap_rich(canvas, text, size, max_width, max_lines=max_lines)
    lh = line_height or size * 1.62
    x, y = xy
    for line in lines:
        cursor = x
        buffer = ""
        buffer_bold: bool | None = None
        for char, bold in line:
            if buffer_bold is None:
                buffer_bold = bold
            if bold != buffer_bold:
                canvas.text(
                    (cursor, y),
                    buffer,
                    size,
                    bold=bool(buffer_bold),
                    color=bold_color if buffer_bold else color,
                )
                cursor += canvas.measure(buffer, size, bool(buffer_bold))
                buffer = ""
                buffer_bold = bold
            buffer += char
        if buffer:
            canvas.text(
                (cursor, y),
                buffer,
                size,
                bold=bool(buffer_bold),
                color=bold_color if buffer_bold else color,
            )
        y += lh
    return y - xy[1]


def _feather_edges(image: Image.Image, fade: int) -> Image.Image:
    """把图片四周的 alpha 渐变到 0，避免出现生硬的矩形边界。"""
    if fade <= 0:
        return image
    width, height = image.size
    mask = Image.new("L", (width, height), 255)
    draw = ImageDraw.Draw(mask)
    for index in range(fade):
        alpha = int(255 * index / fade)
        draw.rectangle(
            (index, index, width - 1 - index, height - 1 - index),
            outline=alpha,
            width=1,
        )
    alpha_channel = image.getchannel("A")
    image.putalpha(ImageChops.multiply(alpha_channel, mask))
    return image


def _number(value: int) -> str:
    """千分位数字。"""
    return f"{int(value):,}"


def fit_text(
    canvas: Canvas, text: str, size: float, max_width: float, *, min_size: float = 9
) -> tuple[str, float]:
    """把文本缩到能放进 ``max_width``，必要时缩小字号或截断。

    Args:
        canvas: 画布。
        text: 原始文本。
        size: 期望字号。
        max_width: 可用宽度。
        min_size: 允许的最小字号。

    Returns:
        ``(处理后的文本, 实际字号)``。
    """
    value = str(text or "")
    if canvas.measure(value, size) <= max_width:
        return value, size
    current = size - 0.5
    while current >= min_size:
        if canvas.measure(value, current) <= max_width:
            return value, current
        current -= 0.5
    while value and canvas.measure(value + "…", min_size) > max_width:
        value = value[:-1]
    return (value + "…") if value else value, min_size


def _format_date_cn(value: str) -> str:
    """把 ``YYYY-MM-DD`` 转成中文日期。"""
    try:
        parsed = date_cls.fromisoformat(value)
    except ValueError:
        return value
    weekdays = "一二三四五六日"
    return f"{parsed.year}年{parsed.month}月{parsed.day}日 周{weekdays[parsed.weekday()]}"


def _format_stamp(value: str) -> str:
    """把 ISO 时间戳转成 ``YYYY-MM-DD HH:MM``。"""
    try:
        return datetime.fromisoformat(value).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _avatar_color(name: str) -> str:
    """根据昵称稳定地生成头像底色。"""
    palette = ["#E4ECFF", "#EDE7FF", "#E3F4F2", "#FFEFE2", "#E9F0DE", "#FDE8EF"]
    return palette[sum(ord(char) for char in name) % len(palette)]


def _initial(name: str) -> str:
    """取昵称首字作为头像文字。"""
    clean = strip_markup(name).strip()
    return clean[0] if clean else "鱼"


_COVER_ANCHORS: dict[str, tuple[float, float]] = {
    "top-left": (0.0, 0.0),
    "top": (0.5, 0.0),
    "top-right": (1.0, 0.0),
    "left": (0.0, 0.5),
    "center": (0.5, 0.5),
    "right": (1.0, 0.5),
    "bottom-left": (0.0, 1.0),
    "bottom": (0.5, 1.0),
    "bottom-right": (1.0, 1.0),
}


def _cover_crop(source: Image.Image, width: float, height: float, focus: str) -> Image.Image:
    """等比铺满目标区域后按焦点裁切（CSS ``object-fit: cover`` 的语义）。

    Args:
        source: 原图（RGB）。
        width: 目标逻辑宽（会乘 2 倍超采样）。
        height: 目标逻辑高。
        focus: ``top-left`` … ``bottom-right`` 九宫格之一。

    Returns:
        目标尺寸的裁切结果（像素尺寸已乘 ``SCALE``）。
    """
    target_w = int(round(width * SCALE))
    target_h = int(round(height * SCALE))
    ratio = max(target_w / max(1, source.width), target_h / max(1, source.height))
    resized = source.resize(
        (max(target_w, int(round(source.width * ratio))), max(target_h, int(round(source.height * ratio)))),
        Image.LANCZOS,
    )
    fx, fy = _COVER_ANCHORS.get(focus, (0.5, 0.5))
    left = int(round((resized.width - target_w) * fx))
    top = int(round((resized.height - target_h) * fy))
    return resized.crop((left, top, left + target_w, top + target_h))


def _round_corners(image: Image.Image, radius: int) -> Image.Image:
    """给图片切圆角（返回带 alpha 的新图）。"""
    mask = Image.new("L", image.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, image.width - 1, image.height - 1), radius=max(0, radius), fill=255
    )
    rounded = image.copy()
    rounded.putalpha(mask)
    return rounded


class PosterRenderer:
    """把 :class:`DailyReport` 绘制成一张长图。"""

    def __init__(
        self,
        config: Any,
        base_dir: Path | None = None,
        cover: dict[str, Any] | None = None,
    ) -> None:
        """初始化渲染器。

        Args:
            config: 插件配置。
            base_dir: 插件根目录（找内置立绘用）。
            cover: 封面元信息（``path`` / ``focus`` / ``height``），为空则不画封面。
        """
        self.config = config
        self.base_dir = base_dir or Path(__file__).resolve().parent
        self._character: Image.Image | None = None
        self._character_size: tuple[float, float] = (0.0, 0.0)
        self._cover_meta: dict[str, Any] = dict(cover or {})
        self._cover_image: Image.Image | None = None
        self._cover_size: tuple[float, float] = (0.0, 0.0)
        self._using_cover = False

    # ------------------------------------------------------------ 公共入口

    def render(self, report: DailyReport) -> Image.Image:
        """渲染海报。

        Args:
            report: 日报数据。

        Returns:
            RGB 长图。
        """
        inner = Canvas(CONTENT_W, 6400)
        y = 0
        y = self._draw_header(inner, y, report)
        y = self._draw_hero(inner, y + 26, report)
        y = self._draw_inner_os(inner, y + 22, report)
        y = self._draw_stats(inner, y + 26, report)
        y = self._draw_bot_record(inner, y + 14, report)
        y = self._draw_radar(inner, y + 26, report)
        if report.topics:
            y = self._draw_topics(inner, y + 24, report)
        if report.members:
            y = self._draw_members(inner, y + 24, report)
        if report.quotes:
            y = self._draw_quotes(inner, y + 24, report)
        if report.insights:
            y = self._draw_insights(inner, y + 24, report)
        y = self._draw_closing(inner, y + 24, report)
        y = self._draw_footer(inner, y + 26, report)
        inner = inner.image.crop((0, 0, CONTENT_W * SCALE, int((y + 4) * SCALE)))

        card_h = y + 4 + CARD_PAD * 2
        page_h = int(PAGE_MARGIN * 2 + card_h)
        page = Canvas(PAGE_W, page_h)
        self._paint_background(page)
        self._paint_card(page, card_h)
        page.image.alpha_composite(inner, (int((PAGE_MARGIN + CARD_PAD) * SCALE), int((PAGE_MARGIN + CARD_PAD) * SCALE)))
        page.image = page.image.resize((PAGE_W, page_h), Image.LANCZOS)
        return page.image.convert("RGB")

    # ------------------------------------------------------------ 背景与卡片

    def _paint_background(self, page: Canvas) -> None:
        """绘制渐变底色与柔光斑。"""
        top, bottom = hex_rgb(BG_TOP), hex_rgb(BG_BOTTOM)
        height = page.height
        for index in range(height):
            ratio = index / max(1, height - 1)
            color = tuple(
                int(top[channel] + (bottom[channel] - top[channel]) * ratio) for channel in range(3)
            )
            page.draw.line(
                [(0, index * page.scale), (page.width * page.scale, index * page.scale)],
                fill=(*color, 255),
                width=page.scale,
            )

        blobs = Image.new("RGBA", page.image.size, (0, 0, 0, 0))
        blob_draw = ImageDraw.Draw(blobs)
        width, height = page.image.size
        for cx, cy, radius, color in (
            (width * 0.88, height * 0.05, width * 0.32, BLOB_BLUE),
            (width * 0.06, height * 0.42, width * 0.30, BLOB_LILAC),
            (width * 0.92, height * 0.82, width * 0.34, BLOB_BLUE),
        ):
            blob_draw.ellipse(
                (cx - radius, cy - radius, cx + radius, cy + radius),
                fill=_blend(hex_rgb(color), 150),
            )
        blobs = blobs.filter(ImageFilter.GaussianBlur(width * 0.05))
        page.image.alpha_composite(blobs)

    def _paint_card(self, page: Canvas, card_h: float) -> None:
        """绘制白色主卡片与投影。"""
        margin = PAGE_MARGIN
        box = (margin, margin, PAGE_W - margin, margin + card_h)
        shadow = Image.new("RGBA", page.image.size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow)
        offset = 8 * page.scale
        shadow_draw.rounded_rectangle(
            (
                box[0] * page.scale,
                box[1] * page.scale + offset,
                box[2] * page.scale,
                box[3] * page.scale + offset,
            ),
            radius=CARD_RADIUS * page.scale,
            fill=(38, 70, 140, 46),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(14 * page.scale))
        page.image.alpha_composite(shadow)
        page.rounded(box, CARD_RADIUS, fill=hex_rgb(CARD))

    # ------------------------------------------------------------ 区块

    def _draw_header(self, c: Canvas, y: float, report: DailyReport) -> float:
        """顶部群名与日期。"""
        self._draw_whale(c, 0, y + 2, 22, ACCENT)
        c.text((30, y), report.room_name or "值班室", 17, bold=True, color=INK)
        c.text(
            (CONTENT_W, y + 3),
            f"DAILY LOG / {_format_date_cn(report.date).split(' 周')[0]}",
            12,
            color=MUTED,
            anchor="ra",
        )
        c.line([(0, y + 40), (CONTENT_W, y + 40)], fill=hex_rgb(LINE), width=1)
        return y + 40

    def _load_cover(self) -> Image.Image | None:
        """把上传的封面裁成「立绘位」尺寸（286 宽 × 配置高度），切圆角。

        封面只替换右侧立绘那一块，不影响顶部的群名/日期，也不新增整条背景。
        """
        if self._cover_image is not None:
            return self._cover_image

        meta = self._cover_meta or {}
        raw = str(meta.get("path") or "").strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = Path("data") / "group_daily_log" / raw
        if not path.is_file():
            return None
        try:
            source = Image.open(path).convert("RGB")
        except OSError:
            return None

        try:
            height = int(meta.get("height") or 300)
        except (TypeError, ValueError):
            height = 300
        height = max(200, min(380, height))

        art_w = 286.0
        image = _cover_crop(source, art_w, height, str(meta.get("focus") or "center"))
        image = _round_corners(image.convert("RGBA"), int(18 * SCALE))
        self._cover_size = (art_w, float(height))
        self._cover_image = image
        return image

    def _draw_whale(self, c: Canvas, x: float, y: float, size: float, color: str) -> None:
        """画一只极简鲸鱼图标。"""
        body = (x + size * 0.04, y + size * 0.26, x + size * 0.74, y + size * 0.86)
        c.ellipse(body, fill=hex_rgb(color))
        # 尾巴
        tail = [
            (x + size * 0.68, y + size * 0.52),
            (x + size * 0.98, y + size * 0.30),
            (x + size * 0.94, y + size * 0.72),
        ]
        c.draw.polygon([(px * c.scale, py * c.scale) for px, py in tail], fill=hex_rgb(color))
        # 眼睛
        c.ellipse(
            (x + size * 0.20, y + size * 0.44, x + size * 0.30, y + size * 0.54),
            fill=(255, 255, 255, 255),
        )
        # 水柱
        c.line(
            [(x + size * 0.34, y + size * 0.24), (x + size * 0.34, y + size * 0.04)],
            fill=hex_rgb(color),
            width=max(1.2, size * 0.07),
        )
        c.line(
            [(x + size * 0.34, y + size * 0.12), (x + size * 0.20, y + size * 0.00)],
            fill=hex_rgb(color),
            width=max(1.0, size * 0.06),
        )
        c.line(
            [(x + size * 0.34, y + size * 0.12), (x + size * 0.48, y + size * 0.00)],
            fill=hex_rgb(color),
            width=max(1.0, size * 0.06),
        )

    def _draw_hero(self, c: Canvas, y: float, report: DailyReport) -> float:
        """标题区：主标题、副标题、日期与立绘。"""
        character = self._load_character()
        art_w, art_h = self._character_size if character else (0.0, 0.0)

        c.ellipse((0, y + 4, 7, y + 11), fill=hex_rgb(ACCENT))
        c.text((16, y), "DeepSeek 鲸鱼女仆 · DAILY LOG", 13, color=ACCENT)

        title_y = y + 26
        c.text((0, title_y), report.bot_name, 44, bold=True, color=INK)
        c.text((0, title_y + 54), report.page_title, 44, bold=True, color=ACCENT)

        subtitle = self.config.poster.subtitle_lines or []
        sub_y = title_y + 120
        for line in subtitle[:2]:
            c.text((0, sub_y), str(line), 15, color=BODY)
            sub_y += 25

        chip_y = sub_y + 6
        chip_w = 252
        c.rounded((0, chip_y, chip_w, chip_y + 42), 11, fill=hex_rgb(ACCENT_PALE), outline=hex_rgb(LINE))
        c.text((14, chip_y + 12), "值班日期", 11.5, color=MUTED)
        date_text, date_size = fit_text(c, _format_date_cn(report.date), 14, chip_w - 84, min_size=11)
        c.text((70, chip_y + 10), date_text, date_size, bold=True, color=INK)

        tag_y = chip_y + 54
        tag_text = "今日份工作，真的做了"
        tag_w = c.measure(tag_text, 12) + 26
        c.rounded((0, tag_y, tag_w, tag_y + 30), 15, fill=hex_rgb("#FBFDFF"), outline=hex_rgb("#D8E3F7"))
        c.text((13, tag_y + 7), tag_text, 12, color=MUTED)

        left_bottom = tag_y + 30

        if character:
            art_x = CONTENT_W - art_w
            art_y = y - 4
            c.paste(character, (art_x, art_y))
            if not self._using_cover:
                bubble_y = art_y + art_h - 18
                bubble_text = "不是鱼，是鲸!"
                bubble_w = c.measure(bubble_text, 11.5) + 26
                bubble_x = CONTENT_W - bubble_w - 4
                c.rounded(
                    (bubble_x, bubble_y, bubble_x + bubble_w, bubble_y + 30),
                    15,
                    fill=hex_rgb("#FFFFFF"),
                    outline=hex_rgb("#DCE6F8"),
                )
                c.text((bubble_x + 13, bubble_y + 7), bubble_text, 11.5, color=ACCENT)
                left_bottom = max(left_bottom, bubble_y + 32)
            else:
                left_bottom = max(left_bottom, art_y + art_h)

        return max(left_bottom, y + 40)

    def _draw_inner_os(self, c: Canvas, y: float, report: DailyReport) -> float:
        """内心 OS 卡片。"""
        text = report.inner_os or "今天没什么想说的，先把日报交了。"
        lines = wrap_rich(c, text, 14.5, CONTENT_W - 44, max_lines=3)
        body_h = len(lines) * 23
        height = 22 + 22 + body_h + 10 + 20

        c.rounded((0, y, CONTENT_W, y + height), 14, fill=hex_rgb(LAV))
        c.rounded((0, y, 3.5, y + height), 2, fill=hex_rgb(LAV_LINE))
        c.ellipse((18, y + 20, 23, y + 25), fill=hex_rgb(LAV_LINE))
        c.text((30, y + 13), "鱼鱼的内心 OS", 13.5, bold=True, color="#3A3550")

        badge_text = "工作便签"
        badge_w = c.measure(badge_text, 10.5) + 20
        c.rounded(
            (CONTENT_W - badge_w - 16, y + 13, CONTENT_W - 16, y + 35),
            6,
            fill="#FFFFFF",
            outline="#DAD3F5",
        )
        c.text((CONTENT_W - badge_w - 6, y + 18), badge_text, 10.5, color="#7A6FB5")

        draw_rich(
            c,
            (18, y + 46),
            text,
            14.5,
            CONTENT_W - 44,
            color="#5B5670",
            bold_color="#3A3550",
            line_height=23,
            max_lines=3,
        )
        c.text((18, y + height - 24), "// 日报已交，饭碗待领", 11, color="#9C94BC")
        return y + height

    def _draw_stats(self, c: Canvas, y: float, report: DailyReport) -> float:
        """今日数据四宫格。"""
        y = self._section_title(
            c, y, "", "鱼塘今日收获", note="数字记清楚，才好理直气壮下班"
        )
        stats = report.stats
        gap = 11
        card_w = (CONTENT_W - gap * 3) / 4
        card_h = 104
        items = [
            ("消息总数", _number(stats.message_count), "条"),
            ("参与人数", _number(stats.member_count), "人"),
            ("总字符数", _number(stats.char_count), "个字"),
            ("表情数量", _number(stats.emoji_count), "个"),
        ]
        for index, (label, value, unit) in enumerate(items):
            x = index * (card_w + gap)
            c.rounded((x, y, x + card_w, y + card_h), 13, fill=hex_rgb("#FBFCFE"), outline=hex_rgb("#EEF3FC"))
            c.text((x + 15, y + 14), label, 12, color=BODY)
            c.text((x + card_w - 14, y + 13), f"0{index + 1}", 10, color=FAINT, anchor="ra")
            c.text((x + 15, y + 40), value, 29, bold=True, color=INK)
            c.text((x + 15, y + 80), unit, 10.5, color=MUTED)
        return y + card_h

    def _draw_bot_record(self, c: Canvas, y: float, report: DailyReport) -> float:
        """机器人自己的「摸鱼记录」卡片。"""
        height = 84
        stats = report.stats
        total = max(1, stats.message_count)
        share = round(stats.bot_message_count * 100 / total)
        c.rounded((0, y, CONTENT_W, y + height), 14, fill=hex_rgb("#F8FAFF"), outline=hex_rgb("#EDF2FC"))
        c.rounded((16, y + 18, 58, y + 60), 12, fill=hex_rgb("#FFFFFF"), outline=hex_rgb("#E3ECFB"))
        c.text((37, y + 38), "鱼", 17, bold=True, color=ACCENT, anchor="mm")

        c.text((70, y + 14), "本鱼的摸鱼记录", 11.5, color=BODY)
        number = _number(stats.bot_message_count)
        c.text((70, y + 32), number, 24, bold=True, color=ACCENT)
        c.text((70 + c.measure(number, 24, True) + 6, y + 42), "条消息", 11, color=MUTED)
        if stats.bot_message_count:
            detail = f"占全群 {share}% · 输出 {_number(stats.bot_char_count)} 字"
        else:
            detail = "今天本鱼只负责看，不负责说"
        c.text((70, y + 61), detail, 10.5, color=MUTED)

        tag = "先记账 再干活"
        tag_w = c.measure(tag, 10.5) + 20
        c.rounded(
            (CONTENT_W - tag_w - 16, y + 18, CONTENT_W - 16, y + 40), 6, fill="#FFFFFF", outline="#E3ECFB"
        )
        c.text((CONTENT_W - tag_w - 6, y + 23), tag, 10.5, color=MUTED)
        return y + height

    def _draw_radar(self, c: Canvas, y: float, report: DailyReport) -> float:
        """24 小时活跃度柱状图。"""
        y = self._section_title(
            c, y, "01", "报鱼雷达", note="本鱼最关心：群里哪个时段最热闹？", badge="24h 消息活跃度"
        )
        height = 206
        c.rounded((0, y, CONTENT_W, y + height), 14, fill=hex_rgb("#FCFDFF"), outline=hex_rgb("#EDF2FC"))

        chart_x, chart_w = 18, CONTENT_W - 36
        base_y = y + 152
        top_y = y + 26
        hourly = report.stats.hourly or [0] * 24
        peak_value = max(hourly) if hourly else 0
        peak = peak_value or 1
        gap = 7
        bar_w = (chart_w - gap * 23) / 24
        if peak_value <= 0:
            c.text(
                (CONTENT_W / 2, y + 92),
                "今天鱼塘很安静，一条消息都没有。",
                13,
                color=MUTED,
                anchor="mm",
            )
        for hour, count in enumerate(hourly):
            bar_h = max(3.0, (count / peak) * (base_y - top_y))
            x = chart_x + hour * (bar_w + gap)
            color = ACCENT if count == peak else ("#9DBBF7" if count > peak * 0.45 else "#D7E3FA")
            c.rounded((x, base_y - bar_h, x + bar_w, base_y), min(4, bar_w / 2), fill=hex_rgb(color))

        c.line([(chart_x, base_y + 1), (chart_x + chart_w, base_y + 1)], fill=hex_rgb("#E6EEFB"), width=1)
        for hour in (0, 4, 8, 12, 16, 20):
            x = chart_x + hour * (bar_w + gap) + bar_w / 2
            c.text((x, base_y + 12), f"{hour:02d}", 9.5, color=FAINT, anchor="ma")
        c.text((chart_x + chart_w, base_y + 12), "时", 9.5, color=FAINT, anchor="ra")
        return y + height

    def _draw_topics(self, c: Canvas, y: float, report: DailyReport) -> float:
        """今日话题列表。"""
        y = self._section_title(
            c,
            y,
            "02",
            "上下文还在，别急",
            badge=f"今日话题 {len(report.topics)}/{self.config.analysis.topic_count}",
        )
        for index, topic in enumerate(report.topics):
            if index:
                c.line([(0, y + 2), (CONTENT_W, y + 2)], fill=hex_rgb(LINE_SOFT), width=1)
                y += 14
            box_y = y
            c.rounded((0, box_y + 2, 26, box_y + 24), 6, fill=hex_rgb(ACCENT_SOFT))
            c.text((13, box_y + 6), f"{index + 1:02d}", 10.5, bold=True, color=ACCENT, anchor="ma")
            title_text, title_size = fit_text(c, topic.title, 16, CONTENT_W - 36, min_size=12)
            c.text((36, box_y), title_text, title_size, bold=True, color=INK)
            body_top = box_y + 30
            used = draw_rich(
                c,
                (36, body_top),
                topic.summary,
                13.5,
                CONTENT_W - 36,
                line_height=23,
                max_lines=4,
            )
            y = body_top + used + 8
        return y

    def _draw_members(self, c: Canvas, y: float, report: DailyReport) -> float:
        """群友图鉴双列卡片。"""
        y = self._section_title(c, y, "03", "今日鱼塘图鉴", note="发言多的、梗多的，都在这里")
        gap = 11
        card_w = (CONTENT_W - gap) / 2
        members = report.members[:8]
        index = 0
        while index < len(members):
            row = members[index : index + 2]
            heights: list[float] = []
            for member in row:
                desc_lines = wrap_rich(c, member.desc, 12, card_w - 74, max_lines=3)
                heights.append(max(96, 58 + len(desc_lines) * 18.5))
            row_h = max(heights)
            for column, member in enumerate(row):
                x = column * (card_w + gap)
                c.rounded(
                    (x, y, x + card_w, y + row_h), 12, fill=hex_rgb("#FBFCFE"), outline=hex_rgb("#EEF3FC")
                )
                c.ellipse((x + 14, y + 15, x + 52, y + 53), fill=hex_rgb(_avatar_color(member.name)))
                c.text((x + 33, y + 34), _initial(member.name), 15, bold=True, color=ACCENT_DARK, anchor="mm")
                name_text, name_size = fit_text(c, member.name, 14, card_w - 74, min_size=10.5)
                c.text((x + 62, y + 13), name_text, name_size, bold=True, color=INK)
                chip_y = y + 34
                chip_x = x + 62
                if member.title:
                    width = c.measure(member.title, 9.5) + 14
                    c.rounded((chip_x, chip_y, chip_x + width, chip_y + 18), 5, fill=hex_rgb(ACCENT_SOFT))
                    c.text((chip_x + 7, chip_y + 3.5), member.title, 9.5, color=ACCENT)
                    chip_x += width + 6
                if member.tag and chip_x + c.measure(member.tag, 9.5) + 14 < x + card_w - 8:
                    width = c.measure(member.tag, 9.5) + 14
                    c.rounded((chip_x, chip_y, chip_x + width, chip_y + 18), 5, fill="#F4F6FB")
                    c.text((chip_x + 7, chip_y + 3.5), member.tag, 9.5, color=MUTED)
                draw_rich(
                    c,
                    (x + 62, y + 60),
                    member.desc,
                    12,
                    card_w - 74,
                    line_height=18.5,
                    max_lines=3,
                )
            y += row_h + gap
            index += 2
        return y - gap

    def _draw_quotes(self, c: Canvas, y: float, report: DailyReport) -> float:
        """今日名言收藏。"""
        y = self._section_title(c, y, "04", "今日名言收藏", note="只收原话，不改写")
        for index, quote in enumerate(report.quotes[:3]):
            if index:
                y += 10
            c.ellipse((0, y + 2, 22, y + 24), fill=hex_rgb(_avatar_color(quote.name)))
            c.text((11, y + 13), _initial(quote.name), 10, bold=True, color=ACCENT_DARK, anchor="mm")
            quote_name, quote_size = fit_text(c, quote.name, 13, CONTENT_W - 120, min_size=10)
            c.text((30, y + 2), quote_name, quote_size, bold=True, color=INK)

            label = "今日金句"
            label_w = c.measure(label, 9.5) + 14
            c.rounded((CONTENT_W - label_w, y + 2, CONTENT_W, y + 20), 5, fill="#FFF7E8")
            c.text((CONTENT_W - label_w + 7, y + 5.5), label, 9.5, color=GOLD)

            text_lines = wrap_rich(c, strip_markup(quote.text), 15.5, CONTENT_W - 40, max_lines=3)
            box_h = 26 + len(text_lines) * 25 + 14
            c.rounded((0, y + 28, CONTENT_W, y + 28 + box_h), 12, fill=hex_rgb("#F8FAFF"))
            c.text((14, y + 32), "“", 30, bold=True, color="#C9D7F2")
            draw_rich(
                c,
                (24, y + 40),
                quote.text,
                15.5,
                CONTENT_W - 48,
                color=INK,
                bold_color=ACCENT_DARK,
                line_height=25,
                max_lines=3,
            )
            comment = f"女仆小注 · {strip_markup(quote.comment)}"
            used = draw_rich(
                c,
                (0, y + 34 + box_h),
                comment,
                11.5,
                CONTENT_W,
                color=MUTED,
                bold_color=BODY,
                line_height=19,
                max_lines=2,
            )
            y = y + 34 + box_h + used
        return y

    def _draw_insights(self, c: Canvas, y: float, report: DailyReport) -> float:
        """数据洞察区。"""
        y = self._section_title(
            c, y, "05", "铁盆防漏 · 鱼塘浅讲", note="把今天的数据翻译成人话", badge="今日份小结"
        )
        height = 26 + len(report.insights[:3]) * 56 + 14
        c.rounded((0, y, CONTENT_W, y + height), 14, fill=hex_rgb("#FCFDFF"), outline=hex_rgb("#EDF2FC"))
        cursor = y + 20
        for index, insight in enumerate(report.insights[:3]):
            if index:
                c.line([(22, cursor - 8), (CONTENT_W - 22, cursor - 8)], fill=hex_rgb(LINE_SOFT), width=1)
            c.text((22, cursor), f"{insight.percent}%", 19, bold=True, color=ACCENT)
            c.text((22 + c.measure(f"{insight.percent}%", 19, True) + 12, cursor + 2), insight.label, 12.5, bold=True, color=INK)
            draw_rich(
                c,
                (22, cursor + 24),
                insight.text,
                11.5,
                CONTENT_W - 48,
                color=MUTED,
                line_height=18,
                max_lines=2,
            )
            cursor += 56
        return y + height

    def _draw_closing(self, c: Canvas, y: float, report: DailyReport) -> float:
        """收尾卡片。"""
        lines = [
            report.closing or "今天的热闹，已经留在上下文里了。",
            "剩下的时间，本鱼申请 DeepSleep。",
        ]
        height = 104
        c.rounded((0, y, CONTENT_W, y + height), 16, fill=hex_rgb("#F7FAFF"), outline=hex_rgb("#E9F0FC"))
        cursor = y + 24
        for line in lines:
            c.text((26, cursor), line, 14.5, color="#42506B")
            cursor += 26
        c.text(
            (CONTENT_W - 24, y + height - 26),
            f"—— {report.bot_name}，先溜了",
            12,
            color=MUTED,
            anchor="ra",
        )
        c.text((26, y + height - 28), "LET ME GO · 今日份已下班", 9.5, color=FAINT)
        return y + height

    def _draw_footer(self, c: Canvas, y: float, report: DailyReport) -> float:
        """页脚署名。"""
        c.line([(0, y), (CONTENT_W, y)], fill=hex_rgb(LINE), width=1)
        y += 18
        c.text((0, y), self.config.poster.brand_text, 13, bold=True, color="#5B6A85")
        c.text((0, y + 24), self.config.poster.credit_text, 9.5, color=FAINT)
        c.text((CONTENT_W, y + 1), self.config.poster.footer_note, 9.5, color=MUTED, anchor="ra")
        version = _plugin_version()
        stamp = _format_stamp(report.generated_at)
        c.text(
            (CONTENT_W, y + 16),
            f"v{version} · {stamp}" if version else stamp,
            9.5,
            color=FAINT,
            anchor="ra",
        )
        return y + 44

    # ------------------------------------------------------------ 小部件

    def _section_title(
        self,
        c: Canvas,
        y: float,
        index: str,
        title: str,
        *,
        note: str = "",
        badge: str = "",
    ) -> float:
        """区块标题行，返回下一行 y。"""
        x = 0
        if index:
            c.rounded((x, y + 3, x + 30, y + 27), 7, fill=hex_rgb(ACCENT_SOFT))
            c.text((x + 15, y + 8), index, 11, bold=True, color=ACCENT, anchor="ma")
            x += 40
        c.text((x, y), title, 19.5, bold=True, color=INK)
        if note and not badge:
            c.text((CONTENT_W, y + 7), note, 11, color=MUTED, anchor="ra")
        if badge:
            width = c.measure(badge, 10) + 18
            c.rounded((CONTENT_W - width, y + 4, CONTENT_W, y + 26), 6, fill="#F7F9FE", outline="#E9EFFA")
            c.text((CONTENT_W - width + 9, y + 9), badge, 10, color=MUTED)
        return y + 40

    def _load_character(self) -> Image.Image | None:
        """加载右上角那块图：优先用上传的封面，没有就用内置立绘。"""
        if not self.config.poster.show_character:
            return None
        if self._character is not None:
            return self._character

        cover = self._load_cover()
        if cover is not None:
            self._using_cover = True
            self._character_size = self._cover_size
            self._character = cover
            return cover

        raw = (self.config.poster.character_path or "").strip()
        path = Path(raw) if raw else self.base_dir / "assets" / "whale_maid.png"
        if raw and not path.is_absolute():
            path = self.base_dir / raw
        if not path.exists():
            return None
        try:
            source = Image.open(path).convert("RGBA")
        except OSError:
            return None
        target_w = 286.0
        ratio = target_w / max(1, source.width)
        target_h = source.height * ratio
        image = source.resize(
            (int(target_w * SCALE), int(round(target_h * SCALE))), Image.LANCZOS
        )
        self._character_size = (target_w, target_h)
        self._character = _feather_edges(image, int(9 * SCALE))
        return self._character
