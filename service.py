"""群日报生成与投递服务。

职责链：读取消息 → 统计 → LLM/规则文案 → 渲染 PNG → 落盘 → 发送。
Service 不缓存跨调用状态，所有中间结果都写入 ``data/group_daily_log``。
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import date as date_cls
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseService

from .analyzer import analyze_day
from .collector import (
    build_transcript,
    compute_stats,
    day_bounds,
    fetch_day_messages,
    resolve_timezone,
    sample_messages,
    split_bot_messages,
    today_in_timezone,
)
from .config import GroupDailyLogConfig
from .cover import load_meta as load_cover_meta
from .render import PosterRenderer
from .reports import ReportEntry, list_reports, slug_for_stream

logger = get_logger("group_daily_log")


class DailyLogService(BaseService):
    """生成并投递群日报海报。"""

    name = "daily_log_service"
    description = "统计群聊消息、生成日报文案并渲染成长图海报"
    version = "1.2.0"
    dependencies: list[str] = []

    def __init__(self, plugin: Any | None = None) -> None:
        """初始化服务。"""
        super().__init__(plugin)
        self._locks: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------ 基础访问

    def _config(self) -> GroupDailyLogConfig:
        """读取插件配置，未加载时退回默认配置。"""
        config = getattr(self.plugin, "config", None)
        return config if isinstance(config, GroupDailyLogConfig) else GroupDailyLogConfig()

    def _renderer(self) -> PosterRenderer:
        """创建海报渲染器（带上当前封面）。"""
        return PosterRenderer(
            self._config(),
            base_dir=Path(__file__).resolve().parent,
            cover=load_cover_meta(),
        )

    def data_dir(self) -> Path:
        """插件数据目录。"""
        return Path("data") / "group_daily_log"

    def report_dir(self, stream_id: str) -> Path:
        """指定聊天流的日报产物目录。"""
        return self.data_dir() / "reports" / slug_for_stream(stream_id)

    def list_reports(self, limit: int = 60) -> list[ReportEntry]:
        """列出已生成的历史日报。"""
        return list_reports(self.data_dir(), limit=limit)

    def report_paths(self, stream_id: str, day: str) -> tuple[Path, Path]:
        """返回 (PNG 路径, JSON 路径)。"""
        folder = self.report_dir(stream_id)
        return folder / f"{day}.png", folder / f"{day}.json"

    def _lock(self, key: str) -> asyncio.Lock:
        """获取按 key 隔离的异步锁。"""
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    def room_name_for(self, stream_id: str, fallback: str = "") -> str:
        """根据配置的群号别名解析群名。

        群号从 stream_id 无法反推，因此调用方通常直接传入群号；
        这里仅处理 ``fallback`` 为空时使用默认群名的情况。
        """
        config = self._config()
        return fallback.strip() or config.poster.room_name

    # ------------------------------------------------------------ 主流程

    async def generate(
        self,
        stream_id: str,
        day: str = "",
        *,
        room_name: str = "",
        force: bool = False,
    ) -> tuple[bool, str, Path | None]:
        """生成指定聊天流某天的日报海报。

        Args:
            stream_id: 聊天流 ID。
            day: ``YYYY-MM-DD``，留空表示今天。
            room_name: 海报顶部群名，留空使用配置。
            force: 为 True 时忽略已有图片重新生成；旧图会一直留到新图写好后
                才会被替换，中途失败不会把历史日报弄丢。

        Returns:
            ``(是否成功, 说明文本, PNG 路径)``。
        """
        config = self._config()
        timezone_name = config.general.timezone
        target_day = day.strip() or today_in_timezone(timezone_name)
        try:
            date_cls.fromisoformat(target_day)
        except ValueError:
            return False, f"日期格式不对：{target_day}（应为 YYYY-MM-DD）", None

        png_path, json_path = self.report_paths(stream_id, target_day)
        if png_path.exists() and not force:
            return True, f"{target_day} 的日报已存在", png_path

        async with self._lock(f"{stream_id}:{target_day}"):
            if png_path.exists() and not force:
                return True, f"{target_day} 的日报已存在", png_path
            return await self._build(stream_id, target_day, room_name, png_path, json_path)

    async def _build(
        self,
        stream_id: str,
        day: str,
        room_name: str,
        png_path: Path,
        json_path: Path,
    ) -> tuple[bool, str, Path | None]:
        """执行一次完整的生成流程。"""
        config = self._config()
        timezone_name = config.general.timezone
        start_ts, end_ts = day_bounds(day, timezone_name)
        logger.info(f"开始生成群日报：stream={stream_id} day={day}")

        messages = await fetch_day_messages(
            stream_id,
            day,
            timezone_name,
            include_bot=True,
            max_messages=0,
        )
        if not messages:
            return False, f"{day} 这一天没有聊天记录，日报没东西可写", None

        human_messages, bot_messages = await split_bot_messages(messages)
        stats_messages = messages if config.analysis.include_bot else human_messages
        stats = compute_stats(
            stats_messages,
            day=day,
            timezone_name=timezone_name,
            bot_messages=bot_messages,
        )

        transcript_source = sample_messages(messages, config.analysis.max_messages)
        transcript = build_transcript(
            transcript_source,
            timezone_name=timezone_name,
            per_message_limit=config.analysis.message_char_limit,
            budget=config.analysis.transcript_budget,
        )

        report = await analyze_day(
            report_date=day,
            room_name=self.room_name_for(stream_id, room_name),
            bot_name=config.poster.bot_name,
            page_title=config.poster.page_title,
            stats=stats,
            messages=stats_messages,
            transcript=transcript,
            config=config,
        )

        try:
            image = await asyncio.to_thread(self._renderer().render, report)
        except Exception as exc:
            logger.error(f"渲染群日报失败：{exc}", exc_info=True)
            return False, f"海报渲染失败：{exc}", None

        try:
            await asyncio.to_thread(self._save_report, image, png_path, json_path, report.to_dict())
        except Exception as exc:
            logger.error(f"保存群日报失败（旧文件已保留）：{exc}", exc_info=True)
            return False, f"海报保存失败：{exc}", None
        self._cleanup_old_reports()
        logger.info(
            f"群日报已生成：{png_path}（{image.width}x{image.height}，来源 {report.source}，"
            f"消息 {stats.message_count} 条）"
        )
        return True, f"{day} 的日报已生成（{stats.message_count} 条消息）", png_path

    @staticmethod
    def _save_report(
        image: Any,
        png_path: Path,
        json_path: Path,
        payload: dict[str, Any],
    ) -> None:
        """先把图和 JSON 写到临时文件，再原子替换。

        这样重新生成失败时旧日报还在，不会出现「历史里忽然空了」的情况。

        Args:
            image: Pillow 图像对象。
            png_path: 目标 PNG 路径。
            json_path: 目标 JSON 路径。
            payload: 写入 JSON 的报表数据。
        """
        png_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_png = png_path.with_name(f".{png_path.name}.tmp")
        tmp_json = json_path.with_name(f".{json_path.name}.tmp")
        try:
            image.save(tmp_png, "PNG")
            tmp_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp_png, png_path)
            os.replace(tmp_json, json_path)
        finally:
            for leftover in (tmp_png, tmp_json):
                try:
                    if leftover.exists():
                        leftover.unlink()
                except OSError:
                    continue

    def _cleanup_old_reports(self) -> None:
        """按配置清理过期图片与 JSON。"""
        keep_days = int(self._config().storage.keep_days)
        if keep_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=keep_days)
        root = self.data_dir() / "reports"
        if not root.exists():
            return
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
            except OSError:
                continue

    # ------------------------------------------------------------ 发送

    async def send(
        self,
        stream_id: str,
        day: str = "",
        *,
        room_name: str = "",
        regenerate: bool = False,
    ) -> tuple[bool, str]:
        """生成（或复用已有）日报并发送到聊天流。

        Args:
            stream_id: 聊天流 ID。
            day: 日期，留空表示今天。
            room_name: 海报群名。
            regenerate: 为 True 时忽略已有图片重新生成。

        Returns:
            ``(是否成功, 说明文本)``。
        """
        config = self._config()
        target_day = day.strip() or today_in_timezone(config.general.timezone)
        png_path, _ = self.report_paths(stream_id, target_day)

        # 重新生成时不先删旧图：generate(force=True) 会把新图写到临时文件再原子替换，
        # 这样即使渲染或发送失败，历史里那张也还在。
        if regenerate or not png_path.exists():
            ok, message, path = await self.generate(
                stream_id, target_day, room_name=room_name, force=regenerate
            )
            if not ok or path is None:
                return False, message
            png_path = path

        from src.app.plugin_system.api.send_api import send_image

        payload = base64.b64encode(png_path.read_bytes()).decode("ascii")
        timeout = max(5, int(config.general.send_timeout_seconds))
        try:
            sent = await asyncio.wait_for(
                send_image(
                    payload,
                    stream_id=stream_id,
                    platform=config.general.platform,
                    processed_plain_text=f"{target_day} 群日报",
                ),
                timeout=timeout,
            )
        except TimeoutError:
            logger.error(f"发送群日报超时（{timeout}s）：stream={stream_id} day={target_day}")
            return False, f"日报已生成，但发送超时（{timeout} 秒），适配器可能没响应"
        except Exception as exc:
            logger.error(f"发送群日报失败：{exc}", exc_info=True)
            return False, f"日报已生成，但发送失败：{exc}"
        if not sent:
            return False, "日报已生成，但发送接口返回失败"
        return True, f"{target_day} 的日报已发出"

    async def preview(self, stream_id: str, day: str = "", *, room_name: str = "") -> tuple[bool, str]:
        """只生成不发送，用于排版调试。"""
        ok, message, _ = await self.generate(stream_id, day, room_name=room_name)
        return ok, message

    # ------------------------------------------------------------ 定时辅助

    def is_due(self, now: datetime | None = None) -> bool:
        """判断当前是否已到配置的发送时间。"""
        config = self._config()
        tz = resolve_timezone(config.general.timezone)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        try:
            hour, minute = (int(part) for part in config.general.send_time.strip().split(":", 1))
            target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except (ValueError, TypeError):
            logger.warning(f"发送时间配置非法：{config.general.send_time!r}，已跳过")
            return False
        return current >= target

    def report_day(self, now: datetime | None = None) -> str:
        """返回本次定时任务应该统计的日期。

        发送时间早于早上六点时，视为「补昨天的日报」。
        """
        config = self._config()
        tz = resolve_timezone(config.general.timezone)
        current = now.astimezone(tz) if now is not None else datetime.now(tz)
        try:
            hour = int(config.general.send_time.strip().split(":", 1)[0])
        except (ValueError, TypeError):
            hour = 23
        target = current.date() if hour >= 6 else current.date() - timedelta(days=1)
        return target.isoformat()

    def group_room_names(self) -> dict[str, str]:
        """解析 ``群号=群名`` 形式的别名配置。"""
        config = self._config()
        result: dict[str, str] = {}
        for item in config.general.group_aliases:
            text = str(item or "").strip()
            if not text or "=" not in text:
                continue
            group_id, name = text.split("=", 1)
            group_id, name = group_id.strip(), name.strip()
            if group_id and name:
                result[group_id] = name
        return result


