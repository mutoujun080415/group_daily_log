"""海报渲染的冒烟测试：不依赖浏览器，只校验能出图与基本尺寸。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from group_daily_log.models import (
    DailyReport,
    DailyStats,
    Insight,
    Member,
    Quote,
    Topic,
)
from group_daily_log.render import PAGE_W, PosterRenderer


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        poster=SimpleNamespace(
            show_character=False,
            character_path="",
            room_name="测试群",
            bot_name="大肥鱼",
            page_title="今日打工实录",
            subtitle_lines=["嗯哼，这样啊。", "先把日报交了，再去干饭。"],
            brand_text="测试主题",
            credit_text="素材署名",
            footer_note="测试插件",
        ),
        analysis=SimpleNamespace(topic_count=2),
    )


def _report() -> DailyReport:
    return DailyReport(
        date="2026-10-31",
        room_name="测试群",
        bot_name="大肥鱼",
        page_title="今日打工实录",
        stats=DailyStats(
            date="2026-10-31",
            message_count=12,
            member_count=4,
            char_count=233,
            emoji_count=5,
            bot_message_count=3,
            bot_char_count=40,
            hourly=[0] * 9 + [3, 4, 5] + [0] * 12,
            top_members=[{"name": "阿岚", "count": 6}],
            peak_hour=11,
            peak_count=5,
        ),
        inner_os="今天有 12 条消息，先看看大家在聊什么。",
        topics=[Topic(title="抽卡", summary="**阿岚** 又歪了，**豆子** 说先吃饭。")],
        members=[
            Member(name="阿岚", title="夜猫子", tag="话痨", desc="今天说了很多，从抽卡聊到吃饭。"),
            Member(name="豆子", title="端水大师", tag="乐子人", desc="一句先吃饭结束所有争论。"),
        ],
        quotes=[Quote(name="豆子", text="先吃饭，吃完再吵。", comment="很稳的一句劝。")],
        insights=[Insight(label="话痨榜", percent=50, text="阿岚贡献了一半消息。")],
        closing="今天的鱼塘，先到这里。",
        generated_at="2026-10-31T22:04:22+08:00",
    )


def test_render_produces_long_image() -> None:
    base = Path(__file__).resolve().parent
    renderer = PosterRenderer(_config(), base_dir=base)
    image = renderer.render(_report())
    assert image.width == PAGE_W
    assert image.height > 900
    assert image.mode == "RGB"
    output = base / "_poster_test.png"
    try:
        image.save(output)
        assert output.stat().st_size > 5000
    finally:
        output.unlink(missing_ok=True)


def test_render_with_character_asset() -> None:
    base = Path(__file__).resolve().parent.parent
    renderer = PosterRenderer(_config(), base_dir=base)
    image = renderer.render(_report())
    assert image.width == PAGE_W
