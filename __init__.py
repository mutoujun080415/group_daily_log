"""群日报（Daily Log）插件包。"""

from __future__ import annotations

from typing import Any

__all__ = ["GroupDailyLogPlugin"]


def __getattr__(name: str) -> Any:
    """按需导入插件类，避免插件加载过程中的循环导入。

    ``PluginManager._load_from_folder`` 会把 ``plugin.py`` 直接以
    ``group_daily_log.plugin`` 的名字执行，此时父包 ``group_daily_log`` 还没
    初始化完成。若在本文件里急切 ``from .plugin import ...``，加载器一执行
    ``plugin.py`` 里的相对导入就会反过来触发本文件，报
    ``cannot import name 'GroupDailyLogPlugin'``。改成惰性导入后，
    包导入与加载器两种路径都能正常取到类。
    """
    if name == "GroupDailyLogPlugin":
        from .plugin import GroupDailyLogPlugin

        return GroupDailyLogPlugin
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
