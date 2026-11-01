"""插件装配测试：组件列表与独立页面开关（不依赖官方 Plugin UI）。"""

from __future__ import annotations

import pytest

from group_daily_log.config import GroupDailyLogConfig
from group_daily_log.plugin import GroupDailyLogPlugin
from group_daily_log.router import DailyLogWebUIRouter


def _names(components: list[type]) -> list[str]:
    return [component.__name__ for component in components]


def test_components_include_standalone_router_by_default() -> None:
    plugin = GroupDailyLogPlugin(GroupDailyLogConfig())
    components = plugin.get_components()

    names = _names(components)
    assert "DailyLogService" in names
    assert "DailyLogCommand" in names
    assert "DailyLogWebUIRouter" in names


def test_standalone_router_can_be_disabled() -> None:
    config = GroupDailyLogConfig()
    config.webui.mount_on_main_http = False
    plugin = GroupDailyLogPlugin(config)

    assert "DailyLogWebUIRouter" not in _names(plugin.get_components())


def test_plugin_declares_no_ui_dependency() -> None:
    """学 nai_artist：核心不写 dependent_components，也不硬依赖 webui 插件。"""
    assert not getattr(GroupDailyLogPlugin, "dependent_components", [])
    assert DailyLogWebUIRouter.name == "daily_log_webui"


@pytest.mark.asyncio
async def test_load_and_unload_do_not_touch_any_ui_service(monkeypatch) -> None:
    """生命周期里不该再去取 Plugin UI 服务。"""
    import group_daily_log.plugin as plugin_module

    called: list[str] = []

    def _boom(*args, **kwargs):
        called.append("get_service")
        raise AssertionError("不应该再调用 get_service")

    if hasattr(plugin_module, "get_service"):
        monkeypatch.setattr(plugin_module, "get_service", _boom)

    config = GroupDailyLogConfig()
    config.general.auto_send = False
    config.general.groups = ["10001"]
    plugin = GroupDailyLogPlugin(config)

    await plugin.on_plugin_loaded()
    assert called == []
    assert plugin.daily_log is not None

    await plugin.on_plugin_unloaded()
    assert plugin.daily_log is None
