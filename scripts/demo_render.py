"""离线渲染演示：不启动机器人，直接用假数据生成一张海报。

用法::

    python scripts/demo_render.py [输出路径]

脚本只依赖 Pillow 与插件内的纯数据模型，便于快速调整视觉。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(Path.cwd()))

from group_daily_log.analyzer import build_fallback_report  # noqa: E402
from group_daily_log.config import GroupDailyLogConfig  # noqa: E402
from group_daily_log.models import (  # noqa: E402
    DailyReport,
    DailyStats,
    Insight,
    Member,
    Quote,
    Topic,
)
from group_daily_log.render import PosterRenderer  # noqa: E402


def demo_report() -> DailyReport:
    """构造一份与参考稿信息量接近的演示数据。"""
    hourly = [2, 1, 0, 0, 0, 0, 1, 3, 9, 14, 22, 18, 12, 9, 15, 34, 58, 12, 8, 11, 26, 41, 17, 6]
    stats = DailyStats(
        date="2026-10-31",
        message_count=400,
        member_count=34,
        char_count=2847,
        emoji_count=43,
        bot_message_count=38,
        bot_char_count=412,
        hourly=hourly,
        peak_hour=16,
        peak_count=58,
        avg_chars=7.1,
        top_members=[
            {"name": "成分本头的宅主人", "count": 58},
            {"name": "Tomben er avant vers demain", "count": 43},
            {"name": "小七 (bot)", "count": 38},
            {"name": "夏百叶", "count": 29},
        ],
    )
    report = DailyReport(
        date="2026-10-31",
        room_name="大肥鱼值班室",
        bot_name="大肥鱼",
        page_title="今日打工实录",
        stats=stats,
        inner_os="今天有 400 条消息。先看看大家在聊什么…… 有点饿了。中午吃什么呢？ 不行，先把上下文整理完。",
        topics=[
            Topic(
                title="阿哈与星铁抽卡",
                summary=(
                    "话题从 **成分本头的宅主人** 和 **白澪** 分享星穹铁道抽卡截图开始，"
                    "**Tomben er avant vers demain** 用一句「星铁抽卡就像在赌命」把气氛点着；"
                    "随后 **某个某个朋友** 开始复读「不歪就是胜利」，一群人顺势聊到保底机制与玄学垫抽。"
                ),
            ),
            Topic(
                title="手机 Root 折腾记",
                summary=(
                    "**小七 (bot)** 提到手机被刷了 root，**成分本头的宅主人** 立刻追问「解 BL 锁要不要清数据」，"
                    "**夏百叶** 补了一句「bootloader 不解锁都是白搭」，讨论最后拐到 Linux 内核编译和温控。"
                ),
            ),
            Topic(
                title="高数极限题互助",
                summary=(
                    "**白澪** 贴出一道极限题，**成分本头的宅主人** 用等价无穷小拆了一遍，"
                    "**喵~** 在旁边提醒「洛必达用之前先看条件」，最后发现是题目抄错了一个符号。"
                ),
            ),
            Topic(
                title="首钢园区夜聊",
                summary=(
                    "**Tomben er avant vers demain** 说去首钢园看夜场，结果被风吹到怀疑人生；"
                    "**喵** 建议下次带件外套，顺便安利了园区门口的烤肠。"
                ),
            ),
        ],
        members=[
            Member(
                name="成分本头的宅主人",
                title="成分本头",
                tag="技术流",
                desc="发言 58 条，从抽卡池歪到 root 权限，再到高数极限，话题跨度大得像在开盲盒。",
            ),
            Member(
                name="Tomben er avant vers demain",
                title="夜行诗人",
                tag="夜猫子",
                desc="平均每条 8 个字，喜欢用短句和省略号制造余味，是深夜话题的常驻嘉宾。",
            ),
            Member(
                name="小七 (bot)",
                title="值班搭子",
                tag="机器人",
                desc="会把「稍等我看下上下文」挂在嘴边，偶尔冒出一句冷幽默，被群友当成半个真人。",
            ),
            Member(
                name="夏百叶",
                title="补刀选手",
                tag="考据党",
                desc="别人聊得热闹时她安静围观，等话题快凉了再补一句精准吐槽，效果拔群。",
            ),
            Member(
                name="白澪",
                title="问题制造机",
                tag="乐子人",
                desc="擅长把简单问题问出哲学味，今天用一道极限题成功让三个人掏出草稿纸。",
            ),
            Member(
                name="喵",
                title="人间清醒",
                tag="端水大师",
                desc="总在争吵边缘说一句「都行都行」，然后把话题拐去吃饭，群内和平指数靠她维持。",
            ),
            Member(
                name="某个某个朋友",
                title="复读机",
                tag="接梗王",
                desc="今天把「不歪就是胜利」复读了七遍，成功让这句话变成群内临时口号。",
            ),
            Member(
                name="喵~",
                title="夜宵雷达",
                tag="吃货",
                desc="全天候播报附近哪家店还开着，是群里最稳定的深夜信息来源。",
            ),
        ],
        quotes=[
            Quote(
                name="Tomben er avant vers demain",
                text="不歪就是胜利，歪了就是命。",
                comment="把抽卡讲成了宿命论，本鱼听完决定不抽了，省钱。",
            ),
            Quote(
                name="成分本头的宅主人",
                text="先别急着刷机，你先想想要是变砖了谁给你送饭。",
                comment="技术讨论突然变成生存问题，很有群友风格的一句劝。",
            ),
            Quote(
                name="喵",
                text="都行都行，先吃饭，吃完再吵。",
                comment="一句话结束三场辩论，本鱼愿称之为鱼塘稳定器。",
            ),
        ],
        insights=[
            Insight(label="话痨榜", percent=35, text="宅主人一个人贡献了 35% 的消息，鱼塘水位主要靠他撑着。"),
            Insight(label="峰值", percent=35, text="16:00 前后最吵，58 条消息挤在一个小时里，像下班前的最后一波。"),
            Insight(label="夜猫", percent=30, text="零点后仍有 30% 的消息，这群鱼里至少有三条不需要睡觉。"),
        ],
        closing="今天的热闹，已经留在上下文里了。",
        source="demo",
        generated_at="2026-10-31T22:04:22+08:00",
    )
    return report


def main() -> None:
    """渲染并保存演示海报。"""
    output = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "preview_demo.png"
    config = GroupDailyLogConfig()
    renderer = PosterRenderer(config, base_dir=ROOT)
    image = renderer.render(demo_report())
    image.save(output, "PNG")
    print(f"saved {output} ({image.width}x{image.height})")

    # 顺带验证无模型回退路径可以跑通
    messages = [
        {
            "time": 1793450000 + index * 60,
            "sender_name": name,
            "processed_plain_text": text,
            "sender_id": f"u{index}",
        }
        for index, (name, text) in enumerate(
            [
                ("阿岚", "今天的风好大，我差点被吹回上周"),
                ("小七 (bot)", "记得把日报交了再下班。"),
                ("豆子", "先吃饭，吃完再吵。"),
                ("阿岚", "抽卡又歪了，不歪就是胜利。"),
            ]
        )
    ]
    hourly = [0] * 24
    for index in range(4):
        hourly[(9 + index) % 24] = 1
    stats = DailyStats(
        date="2026-10-30",
        message_count=4,
        member_count=3,
        char_count=60,
        emoji_count=0,
        bot_message_count=1,
        bot_char_count=11,
        hourly=hourly,
        peak_hour=9,
        peak_count=1,
        top_members=[
            {"name": "阿岚", "count": 2},
            {"name": "小七 (bot)", "count": 1},
            {"name": "豆子", "count": 1},
        ],
    )
    fallback = build_fallback_report(
        report_date="2026-10-30",
        room_name="测试群",
        bot_name="大肥鱼",
        page_title="今日打工实录",
        stats=stats,
        messages=messages,
        config=config,
    )
    fallback_image = renderer.render(fallback)
    fallback_path = output.with_name("preview_fallback.png")
    fallback_image.save(fallback_path, "PNG")
    print(f"saved {fallback_path} ({fallback_image.width}x{fallback_image.height})")


if __name__ == "__main__":
    main()
