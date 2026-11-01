"""纯数据模型的单元测试。"""

from __future__ import annotations

from group_daily_log.models import (
    DailyStats,
    clamp_text,
    clean_text,
    count_emoji,
    count_visible_chars,
    strip_markup,
)


def test_clean_text_removes_cq_and_collapses_space() -> None:
    raw = "[CQ:at,qq=1] 你好\n\n  世界 \u200b "
    assert clean_text(raw) == "你好 世界"


def test_clean_text_truncates_with_ellipsis() -> None:
    text = clean_text("一二三四五六七八九十", 6)
    assert len(text) == 6
    assert text.endswith("…")


def test_count_visible_chars_ignores_space() -> None:
    assert count_visible_chars("你 好 呀") == 3


def test_count_emoji() -> None:
    assert count_emoji("笑死😄😄[表情]") == 3


def test_strip_markup() -> None:
    assert strip_markup("今天 **阿岚** 说") == "今天 阿岚 说"


def test_clamp_text_normalizes_whitespace() -> None:
    assert clamp_text("  a \n b  ", 10) == "a b"


def test_stats_roundtrip_keeps_bot_fields() -> None:
    stats = DailyStats(date="2026-10-31", message_count=10, bot_message_count=2, bot_char_count=30)
    stats.hourly[9] = 4
    restored = DailyStats.from_dict(stats.to_dict())
    assert restored.message_count == 10
    assert restored.bot_message_count == 2
    assert restored.bot_char_count == 30
    assert restored.hourly[9] == 4
    assert len(restored.hourly) == 24
