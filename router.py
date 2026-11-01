"""群日报独立页面的 Router。

把 ``webui_standalone.create_app()`` 生成的子应用挂到主程序 HTTP 服务器的
``[webui].route_path`` 上（默认 ``/plugins/group-daily-log``），不经过官方
Plugin UI 插件，所以 ``.mfp`` 安装也能用页面。
"""

from __future__ import annotations

from typing import Any

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.base import BaseRouter

from .config import GroupDailyLogConfig
from .webui_standalone import create_app

logger = get_logger("group_daily_log")

DEFAULT_ROUTE_PATH = "/plugins/group-daily-log"


class DailyLogWebUIRouter(BaseRouter):
    """群日报独立 WebUI（自挂子应用）。"""

    name = "daily_log_webui"
    description = "群日报独立页面：选群选日期、生成海报、查看历史"
    dependencies: list[str] = []
    cors_origins = ["*"]

    def __init__(self, plugin: Any) -> None:
        """从插件配置里读出挂载路径。

        Args:
            plugin: 插件实例。
        """
        config = getattr(plugin, "config", None)
        if isinstance(config, GroupDailyLogConfig):
            route_path = (config.webui.route_path or "").strip()
            self.custom_route_path = route_path or DEFAULT_ROUTE_PATH
        self._sub_app: Any | None = None
        super().__init__(plugin)

    def register_endpoints(self) -> None:
        """挂载子应用。"""
        self._sub_app = create_app(self.plugin)
        self.app.mount("/", self._sub_app)

    async def startup(self) -> None:
        """启动钩子：把最终路径写进日志，方便找页面。"""
        logger.info(f"群日报 WebUI 已挂载到主程序 HTTP 路径: {self.get_route_path()}")

    async def shutdown(self) -> None:
        """关闭钩子。"""
        self._sub_app = None
