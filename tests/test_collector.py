"""采集与统计逻辑的单元测试（不依赖 Neo-MoFox 运行时）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from group_daily_log.collector import (
    build_transcript,
    compute_stats,
    day_bounds,
    pick_quote_candidates,
    sample_messages,
    split_day_periods,
)

TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


def _ts(hour: int, minute: int = 0) -> float:
    return datetime(2026, 10, 31, hour, minute, tzinfo=TZ).timestamp()


def _message(hour: int, name: str, text: str, *, minute: int = 0, sender: str = "") -> dict:
    return {
        "time": _ts(hour, minute),
        "sender_name": name,
        "sender_id": sender or name,
        "processed_plain_text": text,
        "message_id": f"{name}-{hour}-{minute}",
    }


def test_day_bounds_cover_full_day() -> None:
    start, end = day_bounds("2026-10-31", "Asia/Shanghai")
    assert end - start == 86400
    assert datetime.fromtimestamp(start, TZ).hour == 0


def test_compute_stats_counts_members_hours_and_emoji() -> None:
    messages = [
        _message(9, "阿岚", "早上好呀😄"),
        _message(10, "豆子", "今天吃什么"),
        _message(10, "阿岚", "都可以"),
        _message(21, "小七", "晚安"),
    ]
    stats = compute_stats(messages, day="2026-10-31", timezone_name="Asia/Shanghai")

    assert stats.message_count == 4
    assert stats.member_count == 3
    assert stats.emoji_count == 1
    assert stats.hourly[10] == 2
    assert stats.peak_hour == 10
    assert stats.peak_count == 2
    assert stats.top_members[0] == {"name": "阿岚", "count": 2}
    assert stats.char_count == len("早上好呀😄今天吃什么都可以晚安")


def test_compute_stats_records_bot_activity() -> None:
    human = [_message(9, "阿岚", "在吗")]
    bots = [_message(9, "小七 (bot)", "在的，日报马上来")]
    stats = compute_stats(
        human,
        day="2026-10-31",
        timezone_name="Asia/Shanghai",
        bot_messages=bots,
    )
    assert stats.bot_message_count == 1
    assert stats.bot_char_count == len("在的，日报马上来")


def test_sample_messages_keeps_order_and_cap() -> None:
    messages = [_message(0, "a", str(index)) for index in range(100)]
    picked = sample_messages(messages, 10)
    assert len(picked) == 10
    times = [item["time"] for item in picked]
    assert times == sorted(times)


def test_build_transcript_respects_budget() -> None:
    messages = [_message(10, "阿岚", "很长的一句话" * 10) for _ in range(10)]
    transcript = build_transcript(
        messages,
        timezone_name="Asia/Shanghai",
        per_message_limit=30,
        budget=120,
    )
    assert transcript
    assert len(transcript) <= 130
    assert transcript.splitlines()[0].startswith("10:00 阿岚:")


def test_pick_quote_candidates_filters_noise() -> None:
    messages = [
        _message(9, "阿岚", "/日报"),
        _message(9, "豆子", "https://example.com"),
        _message(9, "小七", "[图片]"),
        _message(10, "阿岚", "抽卡又歪了，不歪就是胜利。"),
        _message(11, "豆子", "先吃饭，吃完再吵。"),
    ]
    picked = pick_quote_candidates(messages, limit=5)
    texts = [item["text"] for item in picked]
    assert "抽卡又歪了，不歪就是胜利。" in texts
    assert all("/日报" not in text and "http" not in text for text in texts)


def test_split_day_periods_groups_by_time() -> None:
    messages = [_message(3, "a", "凌晨"), _message(9, "b", "上午"), _message(15, "c", "下午"), _message(21, "d", "晚上")]
    periods = split_day_periods(messages, "Asia/Shanghai")
    assert [item["label"] for item in periods] == [
        "凌晨的鱼塘",
        "上午的鱼塘",
        "下午的鱼塘",
        "晚上的鱼塘",
    ]
