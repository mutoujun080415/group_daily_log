"""群日报（Daily Log）插件入口。

把群里一天的聊天记录整理成一张长图海报：统计数字、活跃时段、今日话题、
群友图鉴、今日名言与数据小结，可定时投递，也可以 ``/日报`` 手动补发。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BasePlugin, register_plugin
from src.app.plugin_system.types import ChatStream
from src.kernel.concurrency import get_task_manager
from src.kernel.scheduler import TriggerType, get_unified_scheduler

from .command import DailyLogCommand, _parse_permission
from .config import GroupDailyLogConfig
from .router import DailyLogWebUIRouter
from .service import DailyLogService

logger = get_logger("group_daily_log")


@register_plugin
class GroupDailyLogPlugin(BasePlugin):
    """群日报插件。"""

    plugin_name = "group_daily_log"
    configs: list[type] = [GroupDailyLogConfig]
    dependencies: list[str] = []

    def __init__(self, config: GroupDailyLogConfig | None = None) -> None:
        """初始化插件状态。"""
        super().__init__(config)
        self.daily_log: DailyLogService | None = None
        self._schedule_id: str | None = None
        self._register_task_id: str | None = None
        self._state_path = Path("data") / "group_daily_log" / "state.json"
        self._last_sent_day = ""
        self._retry_after: datetime | None = None

    def _config(self) -> GroupDailyLogConfig:
        """读取配置，未注入时使用默认值。"""
        return self.config if isinstance(self.config, GroupDailyLogConfig) else GroupDailyLogConfig()

    def get_components(self) -> list[type]:
        """返回插件组件；独立页面由 ``webui.mount_on_main_http`` 决定是否挂载。"""
        components: list[type] = [DailyLogService, DailyLogCommand]
        if self._config().webui.mount_on_main_http:
            components.append(DailyLogWebUIRouter)
        return components

    # ------------------------------------------------------------ 生命周期

    async def on_plugin_loaded(self) -> None:
        """初始化服务、权限与定时任务。"""
        config = self._config()
        self.daily_log = DailyLogService(self)
        DailyLogCommand.permission_level = _parse_permission(config.general.command_permission)
        DailyLogCommand.associated_platforms = [config.general.platform]
        self._load_state()

        if not config.general.enabled:
            logger.info("群日报已在配置中关闭")
            return
        if not config.general.auto_send:
            logger.info("群日报已关闭自动发送，仅保留 /日报 手动触发")
            return
        if not config.general.groups:
            logger.info("群日报未配置群号，跳过定时任务注册")
            return

        task = get_task_manager().create_task(
            self._register_schedule_when_ready(),
            name="group_daily_log_register",
            daemon=True,
        )
        self._register_task_id = task.task_id
        logger.info("群日报插件已加载，等待调度器就绪")

    async def on_plugin_unloaded(self) -> None:
        """移除定时任务并清理句柄。"""
        if self._schedule_id is not None:
            try:
                await get_unified_scheduler().remove_schedule(self._schedule_id)
            except Exception as exc:
                logger.warning(f"移除群日报定时任务失败：{exc}")
            self._schedule_id = None
        if self._register_task_id is not None:
            get_task_manager().cancel_task(self._register_task_id)
            self._register_task_id = None
        self.daily_log = None

    async def _register_schedule_when_ready(self) -> None:
        """等待调度器启动后注册周期检查。"""
        config = self._config()
        interval = max(30, config.general.check_interval_seconds)
        scheduler = get_unified_scheduler()
        for _ in range(600):
            try:
                self._schedule_id = await scheduler.create_schedule(
                    callback=self._tick,
                    trigger_type=TriggerType.TIME,
                    trigger_config={"interval_seconds": interval},
                    is_recurring=True,
                    task_name="group_daily_log_tick",
                    force_overwrite=True,
                )
                self._register_task_id = None
                logger.info(f"群日报定时检查已注册：{self._schedule_id}（间隔 {interval}s）")
                return
            except RuntimeError:
                await asyncio.sleep(0.5)
            except Exception as exc:
                logger.error(f"注册群日报定时任务失败：{exc}", exc_info=True)
                self._register_task_id = None
                return
        self._register_task_id = None
        logger.error("等待调度器就绪超时，群日报定时任务未注册")

    # ------------------------------------------------------------ 定时执行

    async def _tick(self) -> None:
        """每分钟检查一次是否到达发送时间。"""
        config = self._config()
        if not config.general.enabled or not config.general.auto_send:
            return
        if self.daily_log is None:
            self.daily_log = DailyLogService(self)

        now = datetime.now()
        if self._retry_after is not None and now < self._retry_after:
            return
        if not self.daily_log.is_due(now):
            return

        day = self.daily_log.report_day(now)
        if day == self._last_sent_day:
            return

        names = self.daily_log.group_room_names()
        success_count = 0
        for raw_group in config.general.groups:
            group_id = str(raw_group).strip()
            if not group_id:
                continue
            stream_id = ChatStream.generate_stream_id(config.general.platform, group_id=group_id)
            try:
                ok, message = await self.daily_log.send(
                    stream_id,
                    day,
                    room_name=names.get(group_id, ""),
                )
            except Exception as exc:
                logger.error(f"群日报投递异常：group={group_id} day={day} {exc}", exc_info=True)
                continue
            if ok:
                success_count += 1
            else:
                logger.warning(f"群日报投递失败：group={group_id} day={day} {message}")

        if success_count == 0:
            # 全部失败（例如适配器离线、当天没消息）时十分钟后重试，避免整天空转。
            self._retry_after = now + timedelta(minutes=10)
            logger.warning(f"群日报本轮全部失败，{self._retry_after:%H:%M} 后重试")
            return

        self._retry_after = None
        self._last_sent_day = day
        self._save_state()
        logger.info(f"群日报定时投递完成：day={day}，成功 {success_count}/{len(config.general.groups)} 个群")

    # ------------------------------------------------------------ 状态持久化

    def _load_state(self) -> None:
        """读取上次发送日期，避免重启后重复投递。"""
        try:
            if self._state_path.exists():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._last_sent_day = str(data.get("last_sent_day") or "")
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(f"读取群日报状态失败：{exc}")
            self._last_sent_day = ""

    def _save_state(self) -> None:
        """写入上次发送日期。"""
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"last_sent_day": self._last_sent_day}, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(f"写入群日报状态失败：{exc}")

    # ------------------------------------------------------------ 可选入口

    async def generate_for_group(self, group_id: str, day: str = "") -> tuple[bool, str, Any]:
        """供其他组件调用的便捷方法：按群号生成日报。"""
        if self.daily_log is None:
            self.daily_log = DailyLogService(self)
        config = self._config()
        stream_id = ChatStream.generate_stream_id(config.general.platform, group_id=group_id)
        return await self.daily_log.generate(
            stream_id,
            day,
            room_name=self.daily_log.group_room_names().get(group_id, ""),
        )
