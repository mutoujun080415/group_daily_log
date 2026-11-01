"""群日报插件配置定义。

配置写入 ``config/plugins/group_daily_log/config.toml``，由 Neo-MoFox
配置管理器在插件加载时自动生成与热更新。
"""

from __future__ import annotations

from typing import ClassVar

from src.app.plugin_system.base import BaseConfig, Field, SectionBase, config_section


class GroupDailyLogConfig(BaseConfig):
    """群日报插件运行配置。"""

    name: ClassVar[str] = "config"
    description: ClassVar[str] = "群日报（Daily Log）海报生成与定时投递配置"

    @config_section("general", title="常规设置", tag="general")
    class GeneralSection(SectionBase):
        """调度与投递设置。"""

        enabled: bool = Field(
            default=True,
            description="是否启用群日报",
            label="启用插件",
        )
        timezone: str = Field(
            default="Asia/Shanghai",
            description="计算日报日期与发送时间使用的时区",
            label="时区",
        )
        groups: list[str] = Field(
            default_factory=list,
            description="要投递日报的群号列表；为空时只允许手动 /日报 触发",
            label="群号列表",
        )
        platform: str = Field(
            default="qq",
            description="群号所属平台标识，用于生成聊天流 ID 与发送消息",
            label="平台",
        )
        group_aliases: list[str] = Field(
            default_factory=list,
            description="群号到群名的别名，格式为 群号=群名，例如 123456=大肥鱼值班室",
            label="群名别名",
        )
        send_time: str = Field(
            default="23:30",
            description="每天自动发送日报的时间，格式为 HH:MM",
            label="发送时间",
        )
        auto_send: bool = Field(
            default=True,
            description="是否在到达发送时间后自动生成并发送日报",
            label="自动发送",
        )
        check_interval_seconds: int = Field(
            default=60,
            description="后台检查发送时间的间隔秒数，不建议低于 30",
            label="检查间隔",
        )
        command_permission: str = Field(
            default="user",
            description="手动 /日报 命令所需权限：guest / user / operator / owner",
            label="命令权限",
        )
        send_timeout_seconds: int = Field(
            default=90,
            description="发送图片的超时秒数，超时只报失败不会卡住整个任务",
            label="发送超时",
        )

    general: GeneralSection = Field(default_factory=GeneralSection)

    @config_section("analysis", title="分析设置", tag="analysis")
    class AnalysisSection(SectionBase):
        """聊天记录分析与文案生成设置。"""

        model_task: str = Field(
            default="utils",
            description="用于生成日报文案的模型任务名（config/model.toml 中的 model_tasks）",
            label="模型任务",
        )
        max_messages: int = Field(
            default=1200,
            description="单次分析最多读取的消息条数，超出时按时间均匀抽样",
            label="最大消息数",
        )
        message_char_limit: int = Field(
            default=160,
            description="单条消息进入提示词的最大字符数",
            label="单条截断长度",
        )
        transcript_budget: int = Field(
            default=24000,
            description="聊天记录进入提示词的字符预算，防止超出上下文",
            label="提示词预算",
        )
        topic_count: int = Field(
            default=4,
            description="生成的今日话题数量",
            label="话题数量",
        )
        member_count: int = Field(
            default=8,
            description="生成的群友图鉴数量",
            label="图鉴数量",
        )
        quote_count: int = Field(
            default=3,
            description="生成的今日名言数量",
            label="名言数量",
        )
        include_bot: bool = Field(
            default=True,
            description="统计与提示词中是否包含机器人自己的消息",
            label="包含机器人消息",
        )
        persona: str = Field(
            default=(
                "你是群里的机器鱼「大肥鱼」，一只 DeepSeek 鲸鱼女仆："
                "说话懒散、爱吃、偶尔自嘲，但观察很细。"
                "写日报时像下班前交班，语气轻松，不端水、不说教、不堆形容词。"
            ),
            description="生成文案时使用的人设提示",
            label="人设提示",
        )
        custom_prompt: str = Field(
            default="",
            description="附加在系统提示末尾的额外要求，留空使用内置要求",
            label="附加提示",
        )
        llm_timeout_seconds: int = Field(
            default=120,
            description="单次模型调用的超时秒数；超时会重试一次，再不行走规则回退",
            label="模型超时",
        )

    analysis: AnalysisSection = Field(default_factory=AnalysisSection)

    @config_section("poster", title="海报设置", tag="poster")
    class PosterSection(SectionBase):
        """海报外观设置。"""

        room_name: str = Field(
            default="大肥鱼值班室",
            description="海报顶部展示的群名，可按群别名覆盖",
            label="群名",
        )
        bot_name: str = Field(
            default="大肥鱼",
            description="海报主标题第一行，通常是机器人自己的名字",
            label="机器人名",
        )
        page_title: str = Field(
            default="今日打工实录",
            description="海报主标题第二行",
            label="主标题",
        )
        subtitle_lines: list[str] = Field(
            default_factory=lambda: ["嗯哼，这样啊。", "先把日报交了，再去干饭。"],
            description="主标题下方的两行小字",
            label="副标题",
        )
        character_path: str = Field(
            default="",
            description="自定义立绘路径（绝对路径或相对插件目录）；留空使用内置素材",
            label="立绘路径",
        )
        show_character: bool = Field(
            default=True,
            description="是否在海报中绘制立绘",
            label="显示立绘",
        )
        brand_text: str = Field(
            default="DeepSeek 鲸鱼女仆娘 · 社区同人主题",
            description="海报左下角的主题署名",
            label="主题署名",
        )
        credit_text: str = Field(
            default="角色：上香无形 / ZipZipPipe · CC BY-NC-SA 4.0（改绘）",
            description="海报底部素材署名",
            label="素材署名",
        )
        footer_note: str = Field(
            default="Neo-MoFox · 群日报插件",
            description="海报右下角标注文字",
            label="页脚标注",
        )

    poster: PosterSection = Field(default_factory=PosterSection)

    @config_section("storage", title="存储设置", tag="storage")
    class StorageSection(SectionBase):
        """日报产物留存设置。"""

        keep_days: int = Field(
            default=30,
            description="本地保留多少天的日报图片与 JSON，0 表示不清理",
            label="保留天数",
        )

    storage: StorageSection = Field(default_factory=StorageSection)

    @config_section("webui", title="WebUI 设置", tag="webui")
    class WebUISection(SectionBase):
        """独立页面设置（不依赖官方 Plugin UI）。"""

        mount_on_main_http: bool = Field(
            default=True,
            description=(
                "是否把独立页面挂到主程序 HTTP 服务器（不需要 neo-mofox-webui，"
                "改了需要重载插件才生效）"
            ),
            label="挂载独立页面",
        )
        route_path: str = Field(
            default="/plugins/group-daily-log",
            description="独立页面挂载到主程序 HTTP 服务器时使用的子路径",
            label="独立页面路径",
        )
        default_limit: int = Field(
            default=30,
            description="页面默认展示多少条历史日报",
            label="历史条数",
        )

    webui: WebUISection = Field(default_factory=WebUISection)
