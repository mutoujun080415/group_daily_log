"""群日报独立 WebUI（自挂子应用）。

和 nai_artist 一样的路子：不依赖官方 Plugin UI，把一个小 FastAPI 应用挂到
主程序 HTTP 服务器上，路径由 ``[webui].route_path`` 决定（默认
``/plugins/group-daily-log``）。页面是单文件 ``webui/index.html``，
接口统一在子应用的 ``/api/*`` 下，只监听本机、无需 API Key。

不再依赖官方 Plugin UI：页面和接口都由这个子应用自己提供，
两边可以同时开，也可以只用其中一边。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from src.app.plugin_system.api.log_api import get_logger
from src.app.plugin_system.types import ChatStream

from . import cover
from .collector import today_in_timezone
from .config import GroupDailyLogConfig
from .reports import is_valid_day, report_file, report_url, slug_for_stream

logger = get_logger("group_daily_log")

PAGE_FILE = Path(__file__).with_name("webui") / "index.html"
NO_CACHE = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"}


class GeneratePayload(BaseModel):
    """生成请求体。"""

    group_id: str = Field(default="", description="群号，留空用第一个配置群")
    date: str = Field(default="", description="YYYY-MM-DD，留空表示今天")
    send: bool = Field(default=False, description="生成后是否直接发群")
    regenerate: bool = Field(default=False, description="忽略已有图片重新生成")


def _ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """统一响应形状，和官方 Plugin UI 那边保持一致。"""
    return {"code": 200, "message": message, "data": data}


def create_app(plugin: Any) -> FastAPI:
    """按插件实例创建一个只读 + 一次生成的子应用。

    Args:
        plugin: ``GroupDailyLogPlugin`` 实例（测试里也可以传替身）。

    Returns:
        可直接 ``mount`` 到主 HTTP 服务器的 FastAPI 应用。
    """
    app = FastAPI(
        title="群日报 WebUI",
        description="群日报的独立页面：选群、选日期、生成海报、补发",
        docs_url=None,
        redoc_url=None,
    )
    app.state.plugin = plugin

    def config() -> GroupDailyLogConfig:
        raw = getattr(plugin, "config", None)
        return raw if isinstance(raw, GroupDailyLogConfig) else GroupDailyLogConfig()

    def service() -> Any:
        return getattr(plugin, "daily_log", None)

    def group_rows() -> list[dict[str, str]]:
        cfg = config()
        svc = service()
        aliases = svc.group_room_names() if svc is not None else {}
        rows: list[dict[str, str]] = []
        for raw in cfg.general.groups:
            group_id = str(raw or "").strip()
            if not group_id:
                continue
            stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group_id)
            rows.append(
                {
                    "group_id": group_id,
                    "name": aliases.get(group_id, cfg.poster.room_name),
                    "slug": slug_for_stream(stream_id),
                }
            )
        return rows

    @app.get("/")
    async def index() -> FileResponse:
        """页面本体。"""
        if not PAGE_FILE.is_file():
            raise HTTPException(status_code=500, detail="webui/index.html 缺失")
        return FileResponse(PAGE_FILE, media_type="text/html; charset=utf-8", headers=NO_CACHE)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """健康检查。"""
        return {"status": "ok", "service": "group_daily_log_webui"}

    @app.get("/api/state")
    async def get_state() -> dict[str, Any]:
        """群列表、已生成数、最近一期。"""
        cfg = config()
        svc = service()
        reports = svc.list_reports(limit=0) if svc is not None else []
        latest = reports[0].to_dict() if reports else None
        return _ok(
            {
                "version": str(getattr(plugin, "plugin_version", "") or "2.0.0"),
                "groups": group_rows(),
                "reports": len(reports),
                "latest": latest,
                "limit": cfg.webui.default_limit,
                "mounted": True,
                "keep_days": cfg.storage.keep_days,
            }
        )

    @app.get("/api/reports")
    async def list_reports_endpoint(limit: int = 0) -> dict[str, Any]:
        """历史日报，按日期倒序。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="日报服务尚未初始化")
        maximum = limit if limit > 0 else cfg.webui.default_limit
        maximum = max(1, min(maximum, 200))
        name_by_slug = {row["slug"]: row["name"] for row in group_rows()}
        data = []
        for entry in svc.list_reports(limit=maximum):
            item = entry.to_dict()
            item["group_name"] = name_by_slug.get(entry.slug, "未知群")
            data.append(item)
        return _ok(data)

    @app.get("/api/image")
    async def get_image(slug: str, date: str) -> FileResponse:
        """返回海报 PNG。"""
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="日报服务尚未初始化")
        path = report_file(svc.data_dir(), slug, date)
        if path is None:
            raise HTTPException(status_code=404, detail="日报不存在")
        return FileResponse(
            path=str(path),
            media_type="image/png",
            headers={"Cache-Control": "private, max-age=60"},
        )

    @app.get("/api/cover")
    async def get_cover() -> dict[str, Any]:
        """当前封面状态（没有也返回 200，页面好渲染空状态）。"""
        meta = cover.load_meta()
        return _ok(
            {
                "exists": bool(meta),
                "focus": meta.get("focus") or cover.DEFAULT_FOCUS,
                "height": int(meta.get("height") or cover.DEFAULT_HEIGHT),
                "updated_at": str(meta.get("updated_at") or ""),
                "url": "/api/cover/image" if meta else "",
                "focusChoices": list(cover.FOCUS_CHOICES),
                "minHeight": cover.MIN_HEIGHT,
                "maxHeight": cover.MAX_HEIGHT,
                "maxBytes": cover.MAX_BYTES,
            }
        )

    @app.get("/api/cover/image")
    async def get_cover_image() -> FileResponse:
        """封面原图，用于页面里的预览。"""
        path = cover.image_path()
        if path is None:
            raise HTTPException(status_code=404, detail="还没有封面")
        return FileResponse(
            path=str(path),
            media_type="image/png",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/api/cover")
    async def upload_cover(request: Request) -> dict[str, Any]:
        """上传封面：请求体就是图片字节（不做 multipart，少一个依赖）。"""
        data = await request.body()
        try:
            meta = cover.save_upload(data)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        logger.info(f"封面已更新：{meta['path']}（{meta['height']}px，焦点 {meta['focus']}）")
        return _ok(meta, message="封面已更新，下一条日报生效")

    @app.put("/api/cover")
    async def update_cover(payload: dict[str, Any]) -> dict[str, Any]:
        """只调焦点/高度，不重新上传。"""
        try:
            meta = cover.update(
                focus=payload.get("focus"),
                height=payload.get("height"),
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _ok(meta, message="封面参数已更新")

    @app.delete("/api/cover")
    async def delete_cover() -> dict[str, Any]:
        """删除封面，海报回到默认页眉。"""
        cover.clear()
        return _ok({"exists": False}, message="封面已删除")

    @app.post("/api/generate")
    async def generate(payload: GeneratePayload) -> dict[str, Any]:
        """生成（可选发送）指定群、指定日期的日报。"""
        cfg = config()
        svc = service()
        if svc is None:
            raise HTTPException(status_code=503, detail="日报服务尚未初始化")
        rows = group_rows()
        if not rows:
            raise HTTPException(status_code=400, detail="还没有配置群号，先在插件配置里填 general.groups")

        group_id = payload.group_id.strip() or rows[0]["group_id"]
        row = next((item for item in rows if item["group_id"] == group_id), None)
        if row is None:
            raise HTTPException(status_code=400, detail=f"群 {group_id} 不在 general.groups 中")

        day = payload.date.strip() or today_in_timezone(cfg.general.timezone)
        if not is_valid_day(day):
            raise HTTPException(status_code=400, detail="日期格式应为 YYYY-MM-DD")
        if day > today_in_timezone(cfg.general.timezone):
            raise HTTPException(status_code=400, detail="不能生成未来日期的日报")

        stream_id = ChatStream.generate_stream_id(cfg.general.platform, group_id=group_id)
        try:
            if payload.send:
                ok, message = await svc.send(
                    stream_id,
                    day,
                    room_name=row["name"],
                    regenerate=payload.regenerate,
                )
            else:
                ok, message, _path = await svc.generate(
                    stream_id,
                    day,
                    room_name=row["name"],
                    force=payload.regenerate,
                )
        except Exception as exc:  # 渲染/发送异常都要变成可读的 500
            logger.error(f"独立页面生成日报失败：group={group_id} day={day} {exc}", exc_info=True)
            raise HTTPException(status_code=500, detail=f"生成失败：{exc}") from exc

        return _ok(
            {
                "ok": bool(ok),
                "message": message,
                "date": day,
                "group_id": group_id,
                "url": report_url(row["slug"], day),
            },
            message=message,
        )

    return app
