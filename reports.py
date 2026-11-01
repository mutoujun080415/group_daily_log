"""日报产物的路径规则与历史列表。

只依赖标准库，WebUI 的 Router、Service 与测试共用同一套路径解析，
避免「生成时写在 A 目录、列表时扫 B 目录」这类分叉。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path

_SLUG_PATTERN = re.compile(r"[^0-9A-Za-z_-]+")
_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def slug_for_stream(stream_id: str) -> str:
    """把聊天流 ID 转成可安全用作目录名的标识。

    Neo-MoFox 的 stream_id 是 64 位十六进制串，直接用作目录名即可；
    其他平台的 ID 可能带 ``:``、``/`` 等字符，统一替换为下划线。

    Args:
        stream_id: 聊天流 ID。

    Returns:
        最长 64 个字符的安全标识。
    """
    cleaned = _SLUG_PATTERN.sub("_", str(stream_id or "")).strip("_")
    return (cleaned or "stream")[:64]


def is_valid_day(value: str) -> bool:
    """判断字符串是否是合法的 ``YYYY-MM-DD`` 日期。"""
    text = str(value or "")
    if not _DATE_PATTERN.match(text):
        return False
    try:
        date_cls.fromisoformat(text)
    except ValueError:
        return False
    return True


@dataclass(frozen=True, slots=True)
class ReportEntry:
    """一份已生成日报的索引信息。"""

    slug: str
    day: str
    size: int
    mtime: float

    @property
    def mtime_text(self) -> str:
        """生成时间的可读文本。"""
        return datetime.fromtimestamp(self.mtime).strftime("%Y-%m-%d %H:%M")

    def to_dict(self) -> dict[str, object]:
        """转换为 JSON 友好的字典。"""
        return {
            "slug": self.slug,
            "date": self.day,
            "size": self.size,
            "mtime": self.mtime,
            "mtime_text": self.mtime_text,
            "url": report_url(self.slug, self.day),
        }


def report_url(slug: str, day: str, base: str = "/plugins/group-daily-log") -> str:
    """返回独立页面里可访问的海报地址。

    Args:
        slug: 聊天流 slug。
        day: ``YYYY-MM-DD``。
        base: 独立页面挂载路径，默认 ``/plugins/group-daily-log``。

    Returns:
        形如 ``/plugins/group-daily-log/api/image?slug=…&date=…`` 的地址。
    """
    return f"{base.rstrip('/')}/api/image?slug={slug}&date={day}"


def report_root(data_dir: Path) -> Path:
    """返回日报根目录。"""
    return Path(data_dir) / "reports"


def report_file(data_dir: Path, slug: str, day: str) -> Path | None:
    """解析一份日报的 PNG 路径，路径不合法或不存在时返回 None。

    Args:
        data_dir: 插件数据目录（``data/group_daily_log``）。
        slug: 聊天流标识。
        day: ``YYYY-MM-DD``。

    Returns:
        合法的 PNG 绝对路径，或 None。
    """
    safe_slug = slug_for_stream(slug)
    if safe_slug != slug or not is_valid_day(day):
        return None
    path = report_root(data_dir) / safe_slug / f"{day}.png"
    try:
        resolved = path.resolve()
        resolved.relative_to(report_root(data_dir).resolve())
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def list_reports(data_dir: Path, *, limit: int = 60) -> list[ReportEntry]:
    """列出已生成的日报，按日期倒序。

    Args:
        data_dir: 插件数据目录。
        limit: 最多返回条数，<=0 表示不限制。

    Returns:
        日报索引列表。
    """
    root = report_root(data_dir)
    if not root.is_dir():
        return []
    entries: list[ReportEntry] = []
    for png in root.glob("*/*.png"):
        day = png.stem
        if not is_valid_day(day):
            continue
        try:
            stat = png.stat()
        except OSError:
            continue
        entries.append(
            ReportEntry(
                slug=png.parent.name,
                day=day,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )
        )
    entries.sort(key=lambda item: (item.day, item.mtime), reverse=True)
    return entries[:limit] if limit > 0 else entries
