"""把一天的聊天记录压缩成海报文案。

优先调用 LLM 输出结构化 JSON；模型不可用或输出不合法时，退回
基于统计与原始消息的规则版文案，保证插件在任何环境下都能出图。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .collector import pick_quote_candidates, resolve_timezone, split_day_periods
from .models import (
    DailyReport,
    DailyStats,
    Insight,
    Member,
    Quote,
    Topic,
    clamp_text,
    clean_text,
    count_visible_chars,
    strip_markup,
)

if TYPE_CHECKING:
    from .config import GroupDailyLogConfig

_JSON_START = re.compile(r"\{")
_CODE_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)

_FALLBACK_TITLES = [
    "话痨本体",
    "潜水冠军",
    "夜班常驻",
    "气氛组",
    "技术流",
    "情绪稳定器",
    "梗图仓库",
    "问题儿童",
    "甩锅大师",
    "保温杯选手",
]
_FALLBACK_TAGS = ["话痨", "夜猫子", "技术流", "乐子人", "潜水员", "接梗王", "考据党", "端水大师"]
_FALLBACK_CLOSINGS = [
    "今天的热闹，已经留在上下文里了。",
    "剩下的时间，本鱼申请 DeepSleep。",
    "日报交完，饭碗待领。",
]


def _normalize(text: str) -> str:
    """去掉空白与常见标点，便于做原话比对。"""
    return re.sub(r"[\s，。！？、；：,.!?;:\"'“”‘’（）()【】\[\]…—~～]", "", str(text or ""))


def _split_json(raw: str) -> dict[str, Any] | None:
    """从模型输出中提取第一个完整 JSON 对象。"""
    text = _CODE_FENCE.sub("", str(raw or "")).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate, strict=False)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _member_index(messages: list[dict[str, Any]]) -> dict[str, str]:
    """构建昵称 -> sender_id 的索引。"""
    index: dict[str, str] = {}
    for message in messages:
        name = clean_text(message.get("sender_name") or "", 24)
        if name:
            index.setdefault(name, str(message.get("sender_id") or name))
    return index


def _resolve_member_name(raw_name: str, known: list[str]) -> str:
    """把模型给出的昵称对齐到真实昵称，匹配不到时返回空串。"""
    name = clean_text(strip_markup(raw_name), 24)
    if not name:
        return ""
    if name in known:
        return name
    normalized = _normalize(name)
    for candidate in known:
        if _normalize(candidate) == normalized:
            return candidate
    for candidate in known:
        if normalized and normalized in _normalize(candidate):
            return candidate
    return ""


def _coerce_topics(raw: Any, limit: int) -> list[Topic]:
    """校验并裁剪模型输出的话题。"""
    topics: list[Topic] = []
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            title = clamp_text(strip_markup(item.get("title")), 18)
            summary = clamp_text(strip_markup(item.get("summary")), 180)
            if not title or not summary:
                continue
            topics.append(Topic(title=title, summary=summary))
            if len(topics) >= limit:
                break
    return topics


# 数据洞察只保留这三类，百分比一律由真实统计计算，模型只负责措辞。
_INSIGHT_LABELS: dict[str, str] = {
    "active": "话痨榜",
    "peak": "峰值",
    "night": "夜猫",
}
_INSIGHT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "active": ("话痨", "活跃", "发言", "最多", "刷屏", "贡献"),
    "peak": ("峰", "高峰", "最吵", "时段", "集中"),
    "night": ("夜", "凌晨", "零点", "深夜", "睡", "通宵"),
}


def insight_shares(stats: DailyStats) -> dict[str, int]:
    """按真实统计计算三类洞察的百分比。

    Args:
        stats: 当天统计。

    Returns:
        ``active``（最活跃成员占全部消息的百分比）、``peak``（峰值小时占比）、
        ``night``（零到六点加二十二点以后的占比）。
    """
    total = max(1, stats.message_count)
    top = stats.top_members[0]["count"] if stats.top_members else 0
    night = sum(stats.hourly[0:6]) + sum(stats.hourly[22:24])
    return {
        "active": round(top * 100 / total),
        "peak": round(stats.peak_count * 100 / total),
        "night": round(night * 100 / total),
    }


def _resolve_insight_kind(item: dict[str, Any]) -> str:
    """判断一条洞察属于哪类统计：优先看 ``kind``，其次看 label 关键词。"""
    kind = str(item.get("kind") or "").strip().lower()
    if kind in _INSIGHT_LABELS:
        return kind
    label = str(item.get("label") or "")
    for name, words in _INSIGHT_KEYWORDS.items():
        if any(word in label for word in words):
            return name
    return ""


def _coerce_insights(
    raw: Any,
    stats: DailyStats,
    limit: int = 3,
) -> list[tuple[str, Insight]]:
    """校验模型输出的数据洞察，并识别它属于哪类真实统计。

    模型经常随手写 ``100%`` 这种和事实对不上的数字，所以百分比不再采信模型，
    一律由 :func:`insight_shares` 按真实统计重算；识别不出类型的条目丢弃，
    由回退洞察补齐。

    Args:
        raw: 模型返回的 ``insights`` 字段。
        stats: 当天真实统计。
        limit: 最多保留几条。

    Returns:
        ``(kind, Insight)`` 列表，``kind`` 取自 ``active`` / ``peak`` / ``night``。
    """
    shares = insight_shares(stats)
    insights: list[tuple[str, Insight]] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            kind = _resolve_insight_kind(item)
            if not kind or kind in seen:
                continue
            text = clamp_text(strip_markup(item.get("text")), 80)
            if not text:
                continue
            label = clamp_text(strip_markup(item.get("label")), 8) or _INSIGHT_LABELS[kind]
            insights.append((kind, Insight(label=label, percent=shares[kind], text=text)))
            seen.add(kind)
            if len(insights) >= limit:
                break
    return insights


def _coerce_members(
    raw: Any,
    *,
    known_names: list[str],
    stats: DailyStats,
    limit: int,
) -> list[Member]:
    """校验模型输出的群友图鉴，并把未知昵称丢弃。"""
    members: list[Member] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = _resolve_member_name(item.get("name") or "", known_names)
            if not name or name in seen:
                continue
            title = clamp_text(strip_markup(item.get("title")), 8)
            tag = clamp_text(strip_markup(item.get("tag")), 8)
            desc = clamp_text(strip_markup(item.get("desc")), 90)
            if not desc:
                continue
            seen.add(name)
            members.append(Member(name=name, title=title or "群友", tag=tag, desc=desc))
            if len(members) >= limit:
                break
    return members


def _coerce_quotes(
    raw: Any,
    *,
    candidates: list[dict[str, str]],
    transcript_normalized: str,
    limit: int,
) -> list[Quote]:
    """校验模型输出的名言：原话必须真实出现在聊天记录中。"""
    quotes: list[Quote] = []
    used: set[str] = set()
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            text = clean_text(strip_markup(item.get("text")), 90)
            name = clean_text(strip_markup(item.get("name")), 24)
            comment = clamp_text(strip_markup(item.get("comment")), 70)
            normalized = _normalize(text)
            if not text or not comment or not normalized:
                continue
            if normalized not in transcript_normalized:
                continue
            if text in used:
                continue
            used.add(text)
            quotes.append(Quote(name=name or "群友", text=text, comment=comment))
            if len(quotes) >= limit:
                break

    if len(quotes) < limit:
        for candidate in candidates:
            if len(quotes) >= limit:
                break
            if candidate["text"] in used:
                continue
            used.add(candidate["text"])
            quotes.append(
                Quote(
                    name=candidate["name"],
                    text=candidate["text"],
                    comment="这句被本鱼记进了小本本，适合贴在下班路上反复回味。",
                )
            )
    return quotes


def build_prompt(
    *,
    report_date: str,
    room_name: str,
    stats: DailyStats,
    transcript: str,
    config: "GroupDailyLogConfig",
    member_lines: str,
) -> tuple[str, str]:
    """构造系统提示与用户提示。

    Args:
        report_date: 日报日期。
        room_name: 群名。
        stats: 当天统计。
        transcript: 聊天记录文本。
        config: 插件配置。
        member_lines: 成员发言数摘要。

    Returns:
        ``(system_prompt, user_prompt)``。
    """
    analysis = config.analysis
    system_prompt = (
        f"你是一个群聊日报编辑，服务对象是群「{room_name}」。"
        "你的工作是把一天的聊天记录压缩成一张海报需要的信息，交给群里的人下班前看一眼。\n"
        f"{analysis.persona}\n"
        "硬性要求：\n"
        "1. 只输出一个 JSON 对象，不要 Markdown 代码块，不要任何解释文字。\n"
        "2. 人物昵称、原话必须来自聊天记录，禁止编造昵称、事件与数字。\n"
        "3. 不评价外貌、身份、地域、政治与隐私；不写人身攻击和引战内容。\n"
        "4. 写得具体：写谁说了什么、事情怎么拐弯的；不要「大家聊得很开心」「氛围很好」这类空话。\n"
        "5. 用中文短句，可以自嘲，但不要油腻、不要堆形容词、不要升华。\n"
        "6. 严格遵守字段长度限制，超长会被截断。\n"
    )
    if analysis.custom_prompt.strip():
        system_prompt += f"\n额外要求：\n{analysis.custom_prompt.strip()}\n"

    user_prompt = (
        f"日期：{report_date}\n"
        f"统计：消息 {stats.message_count} 条，参与 {stats.member_count} 人，"
        f"有效字数 {stats.char_count}，表情 {stats.emoji_count} 个，"
        f"最活跃时段 {stats.peak_hour:02d}:00（{stats.peak_count} 条）。\n"
        f"成员发言数：\n{member_lines}\n\n"
        f"聊天记录（{report_date}）：\n{transcript}\n\n"
        "请输出如下结构的 JSON：\n"
        "{\n"
        f'  "inner_os": "40 字以内，第一人称内心戏，可以吐槽今天聊的内容或吃饭、下班",\n'
        f'  "topics": [{{"title": "10 字以内，具体到事", "summary": "80-140 字，讲清这段在聊什么、怎么聊开的；可用 **昵称** 强调，最多两处"}}],\n'
        f'  "members": [{{"name": "记录里的昵称", "title": "6 字以内称号", "tag": "一个标签，如 话痨 / 夜猫子 / 技术流", "desc": "40-70 字，写他今天具体做了什么、说话什么风格"}}],\n'
        f'  "quotes": [{{"name": "昵称", "text": "记录里的原话，可截取但不要改写", "comment": "30-60 字，女仆视角的吐槽或补充"}}],\n'
        f'  "insights": [{{"kind": "active|peak|night", "label": "2-4 字关键词", "text": "30-50 字，说明这个数字背后的现象"}}],\n'
        f'  "closing": "20 字以内，下班口吻"\n'
        "}\n"
        f"数量：topics 恰好 {analysis.topic_count} 条，members 最多 {analysis.member_count} 条，"
        f"quotes {analysis.quote_count} 条，insights 3 条。\n"
        "insights 的 kind 只能取 active（最活跃成员）、peak（峰值时段）、night（夜间消息），"
        "三条各一种；不要在 JSON 里写百分比，程序会按真实统计填。\n"
    )
    return system_prompt, user_prompt


async def _request_llm(
    system_prompt: str,
    user_prompt: str,
    model_task: str,
    timeout: float = 120.0,
) -> str:
    """调用 LLM 并返回原始文本；失败或超时抛异常由上层回退。

    Args:
        system_prompt: 系统提示。
        user_prompt: 用户提示。
        model_task: ``config/model.toml`` 里的模型任务名。
        timeout: 单次调用超时秒数，避免模型卡住时整个生成任务一直挂着。
    """
    from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
    from src.kernel.llm import LLMContextManager, LLMPayload, ROLE, Text

    model_set = get_model_set_by_task(model_task)
    request = create_llm_request(
        model_set=model_set,
        request_name="group_daily_log",
        context_manager=LLMContextManager(),
    )
    request.add_payload(LLMPayload(ROLE.SYSTEM, [Text(system_prompt)]))
    request.add_payload(LLMPayload(ROLE.USER, [Text(user_prompt)]))

    async def _run() -> str:
        response = await request.send(stream=False)
        await response
        return str(response.message or "")

    return await asyncio.wait_for(_run(), timeout=max(5.0, float(timeout)))


def _build_member_lines(stats: DailyStats) -> str:
    """把发言数统计格式化为提示词里的一行行文本。"""
    return "\n".join(
        f"- {item['name']}：{item['count']} 条" for item in stats.top_members[:12]
    ) or "- （无）"


def _fallback_members(
    messages: list[dict[str, Any]],
    stats: DailyStats,
    *,
    limit: int,
    room_seed: str,
) -> list[Member]:
    """在无模型时，用发言量与最长消息生成群友图鉴。"""
    rng = random.Random(int(hashlib.md5(room_seed.encode("utf-8")).hexdigest(), 16) % (2**32))
    by_name: dict[str, list[str]] = {}
    counts: dict[str, int] = {}
    for message in messages:
        name = clean_text(message.get("sender_name") or "", 24)
        if not name:
            continue
        text = clean_text(message.get("processed_plain_text") or "", 0)
        counts[name] = counts.get(name, 0) + 1
        if text:
            by_name.setdefault(name, []).append(text)

    members: list[Member] = []
    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    for name, count in ranked[:limit]:
        texts = by_name.get(name) or []
        sample = max(texts, key=count_visible_chars) if texts else ""
        member = Member(
            name=name,
            title=rng.choice(_FALLBACK_TITLES),
            tag=rng.choice(_FALLBACK_TAGS),
            desc=(
                f"今天出现 {count} 次"
                + (f"，最长一句是「{clamp_text(sample, 42)}」。" if sample else "，安静得像在潜水。")
            ),
        )
        members.append(member)
    return members


def _fallback_topics(
    messages: list[dict[str, Any]],
    *,
    timezone_name: str,
    limit: int,
) -> list[Topic]:
    """在无模型时，按时间段挑最长消息凑出话题摘要。"""
    topics: list[Topic] = []
    for period in split_day_periods(messages, timezone_name):
        if len(topics) >= limit:
            break
        items = sorted(
            period["messages"],
            key=lambda message: count_visible_chars(str(message.get("processed_plain_text") or "")),
            reverse=True,
        )
        picked = [item for item in items[:2] if clean_text(item.get("processed_plain_text") or "", 0)]
        if not picked:
            continue
        head = clean_text(picked[0].get("processed_plain_text") or "", 46)
        speaker = clean_text(picked[0].get("sender_name") or "有人", 16)
        if len(picked) > 1:
            second = clean_text(picked[1].get("processed_plain_text") or "", 40)
            second_speaker = clean_text(picked[1].get("sender_name") or "另一个人", 16)
            summary = f"{speaker} 提到「{head}」，{second_speaker} 接了一句「{second}」。"
        else:
            summary = f"{speaker} 提到「{head}」，这段只有他一个人在说。"
        topics.append(Topic(title=period["label"], summary=summary))
    return topics


def _fallback_insight_map(stats: DailyStats) -> dict[str, Insight]:
    """用真实统计生成三类数据洞察，键与模型的 ``kind`` 一致。"""
    shares = insight_shares(stats)
    top_name = stats.top_members[0]["name"] if stats.top_members else "群友"
    night = sum(stats.hourly[0:6]) + sum(stats.hourly[22:24])
    return {
        "active": Insight(
            label="话痨榜",
            percent=shares["active"],
            text=f"{top_name} 一个人贡献了 {shares['active']}% 的消息，鱼塘水位主要靠他撑着。",
        ),
        "peak": Insight(
            label="峰值",
            percent=shares["peak"],
            text=f"{stats.peak_hour:02d}:00 前后最吵，{stats.peak_count} 条消息挤在一个小时里。",
        ),
        "night": Insight(
            label="夜猫",
            percent=shares["night"],
            text=f"零点到六点、二十二点以后合计 {night} 条，{shares['night']}% 的鱼不肯睡。",
        ),
    }


def _fallback_insights(stats: DailyStats, messages: list[dict[str, Any]]) -> list[Insight]:
    """用真实统计生成三条数据洞察。"""
    return list(_fallback_insight_map(stats).values())


def build_fallback_report(
    *,
    report_date: str,
    room_name: str,
    bot_name: str,
    page_title: str,
    stats: DailyStats,
    messages: list[dict[str, Any]],
    config: "GroupDailyLogConfig",
    reason: str = "",
) -> DailyReport:
    """在无模型或模型失败时生成一张仍然可读的日报。"""
    analysis = config.analysis
    candidates = pick_quote_candidates(messages, limit=max(analysis.quote_count, 3))
    quotes = [
        Quote(
            name=item["name"],
            text=item["text"],
            comment="这句被本鱼记进了小本本，适合贴在下班路上反复回味。",
        )
        for item in candidates[: analysis.quote_count]
    ]
    inner_os = (
        f"今天 {stats.message_count} 条消息，"
        f"最吵的时候是 {stats.peak_hour:02d} 点。"
        "先看看大家在聊什么……有点饿了。"
    )
    return DailyReport(
        date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        stats=stats,
        inner_os=clamp_text(inner_os, 40),
        topics=_fallback_topics(
            messages, timezone_name=config.general.timezone, limit=analysis.topic_count
        ),
        members=_fallback_members(
            messages,
            stats,
            limit=analysis.member_count,
            room_seed=f"{room_name}:{report_date}",
        ),
        quotes=quotes,
        insights=_fallback_insights(stats, messages),
        closing="今天的热闹，已经留在上下文里了。",
        source="fallback" + (f":{reason}" if reason else ""),
        generated_at=datetime.now(resolve_timezone(config.general.timezone)).isoformat(
            timespec="seconds"
        ),
    )


async def analyze_day(
    *,
    report_date: str,
    room_name: str,
    bot_name: str,
    page_title: str,
    stats: DailyStats,
    messages: list[dict[str, Any]],
    transcript: str,
    config: "GroupDailyLogConfig",
) -> DailyReport:
    """生成日报文案：优先 LLM，失败时回退规则版。

    Args:
        report_date: 日报日期。
        room_name: 群名。
        bot_name: 机器人名。
        page_title: 海报主标题。
        stats: 当天统计。
        messages: 当天消息（用于校验原话与回退）。
        transcript: 已构建的聊天记录文本。
        config: 插件配置。

    Returns:
        完整的日报数据。
    """
    analysis = config.analysis
    fallback = build_fallback_report(
        report_date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        stats=stats,
        messages=messages,
        config=config,
    )
    if not transcript.strip():
        return fallback

    known_names = list(_member_index(messages).keys())
    system_prompt, user_prompt = build_prompt(
        report_date=report_date,
        room_name=room_name,
        stats=stats,
        transcript=transcript,
        config=config,
        member_lines=_build_member_lines(stats),
    )
    # 模型偶发返回不带 JSON / 被截断的内容（真机实测同一模型两次结果不同），
    # 失败就再要一次，仍然不行才退回规则版。
    data: dict[str, Any] | None = None
    last_error: Exception | None = None
    timeout = float(getattr(analysis, "llm_timeout_seconds", 120) or 120)
    for _attempt in range(2):
        try:
            raw = await _request_llm(
                system_prompt, user_prompt, analysis.model_task, timeout=timeout
            )
        except Exception as exc:  # 模型未配置 / 超时 / 限流
            last_error = exc
            continue
        data = _split_json(raw)
        if data:
            break

    if not data:
        if last_error is not None:
            fallback.source = f"fallback:llm_error:{type(last_error).__name__}"
        else:
            fallback.source = "fallback:bad_json"
        return fallback

    topics = _coerce_topics(data.get("topics"), analysis.topic_count)
    members = _coerce_members(
        data.get("members"),
        known_names=known_names,
        stats=stats,
        limit=analysis.member_count,
    )
    quotes = _coerce_quotes(
        data.get("quotes"),
        candidates=pick_quote_candidates(messages, limit=max(analysis.quote_count, 3)),
        transcript_normalized=_normalize(transcript),
        limit=analysis.quote_count,
    )
    model_insights = _coerce_insights(data.get("insights"), stats)

    if not topics or not members:
        fallback.source = "fallback:incomplete_json"
        return fallback

    # 模型只负责措辞；缺失的类型用真实统计出的回退洞察补满
    insights = [entry for _kind, entry in model_insights]
    used_kinds = {kind for kind, _entry in model_insights}
    for kind, entry in _fallback_insight_map(stats).items():
        if len(insights) >= 3:
            break
        if kind not in used_kinds:
            insights.append(entry)

    report = DailyReport(
        date=report_date,
        room_name=room_name,
        bot_name=bot_name,
        page_title=page_title,
        stats=stats,
        inner_os=clamp_text(strip_markup(data.get("inner_os")), 44) or fallback.inner_os,
        topics=topics,
        members=members,
        quotes=quotes or fallback.quotes,
        insights=insights or fallback.insights,
        closing=clamp_text(strip_markup(data.get("closing")), 28) or fallback.closing,
        source="llm",
        generated_at=datetime.now(resolve_timezone(config.general.timezone)).isoformat(
            timespec="seconds"
        ),
    )
    return report
