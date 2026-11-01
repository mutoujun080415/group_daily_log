"""文案解析与回退逻辑的单元测试。"""

from __future__ import annotations

from types import SimpleNamespace

from group_daily_log.analyzer import (
    _coerce_insights,
    _coerce_members,
    _coerce_quotes,
    _coerce_topics,
    _fallback_insight_map,
    _split_json,
    build_fallback_report,
    insight_shares,
)
from group_daily_log.models import DailyStats


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        analysis=SimpleNamespace(
            quote_count=3,
            topic_count=4,
            member_count=8,
            timezone="Asia/Shanghai",
            persona="测试人设",
            custom_prompt="",
        ),
        general=SimpleNamespace(timezone="Asia/Shanghai"),
    )


def _stats() -> DailyStats:
    return DailyStats(
        date="2026-10-31",
        message_count=3,
        member_count=2,
        top_members=[{"name": "阿岚", "count": 2}, {"name": "豆子", "count": 1}],
    )


def test_split_json_handles_code_fence_and_noise() -> None:
    raw = '好的，结果如下：\n```json\n{"inner_os": "饿"}\n```\n以上。'
    assert _split_json(raw) == {"inner_os": "饿"}


def test_split_json_returns_none_for_invalid() -> None:
    assert _split_json("没有 JSON") is None
    assert _split_json("{坏掉的}") is None


def test_coerce_topics_limits_and_strips_markup() -> None:
    topics = _coerce_topics(
        [
            {"title": "**抽卡**", "summary": "**阿岚** 又歪了"},
            {"title": "短", "summary": "内容"},
            {"title": "", "summary": "没有标题"},
        ],
        limit=1,
    )
    assert len(topics) == 1
    assert topics[0].title == "抽卡"
    assert "**" not in topics[0].summary


def test_coerce_members_drops_unknown_names() -> None:
    members = _coerce_members(
        [
            {"name": "阿岚", "title": "夜猫子", "tag": "话痨", "desc": "今天说了很多"},
            {"name": "不存在的人", "title": "路人", "tag": "", "desc": "编的"},
        ],
        known_names=["阿岚", "豆子"],
        stats=_stats(),
        limit=8,
    )
    assert [item.name for item in members] == ["阿岚"]


def test_coerce_quotes_rejects_fabricated_text() -> None:
    quotes = _coerce_quotes(
        [
            {"name": "阿岚", "text": "这句话根本没说过", "comment": "点评"},
            {"name": "豆子", "text": "先吃饭，吃完再吵。", "comment": "点评"},
        ],
        candidates=[{"name": "阿岚", "text": "抽卡又歪了，不歪就是胜利。"}],
        transcript_normalized="豆子先吃饭吃完再吵阿岚抽卡又歪了不歪就是胜利",
        limit=3,
    )
    assert quotes[0].text == "先吃饭，吃完再吵。"
    assert all("根本没说过" not in item.text for item in quotes)
    assert len(quotes) == 2


def test_insight_shares_come_from_real_stats() -> None:
    stats = DailyStats(
        date="2026-10-31",
        message_count=100,
        member_count=2,
        top_members=[{"name": "阿岚", "count": 40}],
        hourly=[10] * 24,
        peak_hour=23,
        peak_count=25,
    )
    shares = insight_shares(stats)
    assert shares["active"] == 40
    assert shares["peak"] == 25
    assert shares["night"] == 80


def test_coerce_insights_overrides_model_percent() -> None:
    stats = DailyStats(
        date="2026-10-31",
        message_count=100,
        top_members=[{"name": "阿岚", "count": 40}],
        hourly=[10] * 24,
        peak_hour=23,
        peak_count=25,
    )
    raw = [
        {"kind": "active", "label": "最活跃成员", "percent": 100, "text": "阿岚最积极"},
        {"label": "夜间消息", "percent": 100, "text": "晚上最热闹"},
        {"kind": "whatever", "label": "瞎编", "text": "这条没有真实统计对应"},
        {"kind": "active", "label": "话痨", "text": "重复类型应被丢弃"},
    ]
    items = _coerce_insights(raw, stats)
    assert [kind for kind, _item in items] == ["active", "night"]
    assert items[0][1].percent == 40
    assert items[1][1].percent == 80


def test_fallback_insight_map_covers_three_kinds() -> None:
    stats = DailyStats(
        date="2026-10-31",
        message_count=10,
        top_members=[{"name": "阿岚", "count": 4}],
        hourly=[0] * 24,
        peak_hour=9,
        peak_count=3,
    )
    mapping = _fallback_insight_map(stats)
    assert set(mapping) == {"active", "peak", "night"}
    assert mapping["active"].percent == 40


def test_build_fallback_report_survives_without_llm() -> None:
    messages = [
        {"time": 1793440000.0, "sender_name": "阿岚", "processed_plain_text": "今天的风好大，我差点被吹回上周"},
        {"time": 1793443600.0, "sender_name": "豆子", "processed_plain_text": "先吃饭，吃完再吵，别饿着肚子讲道理。"},
    ]
    report = build_fallback_report(
        report_date="2026-10-31",
        room_name="测试群",
        bot_name="大肥鱼",
        page_title="今日打工实录",
        stats=_stats(),
        messages=messages,
        config=_config(),
    )
    assert report.topics
    assert report.members
    assert report.quotes
    assert len(report.insights) == 3
    assert report.source.startswith("fallback")
