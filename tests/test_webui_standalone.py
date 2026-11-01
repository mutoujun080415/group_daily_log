"""独立页面（自挂子应用）的装配与接口测试。

不启动机器人：用假插件 + 假 Service 构建子应用，直接 TestClient 打接口。
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from PIL import Image

from group_daily_log import cover as cover_module
from group_daily_log.config import GroupDailyLogConfig
from group_daily_log.reports import list_reports
from group_daily_log.router import DEFAULT_ROUTE_PATH, DailyLogWebUIRouter
from group_daily_log.webui_standalone import create_app


class _FakeService:
    """只实现独立页面用到的服务方法。"""

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self.generated: list[tuple[str, str, bool]] = []

    def data_dir(self) -> Path:
        return self._data_dir

    def list_reports(self, limit: int = 60):
        return list_reports(self._data_dir, limit=limit)

    def group_room_names(self) -> dict[str, str]:
        return {"10001": "测试群"}

    async def generate(self, stream_id: str, day: str = "", *, room_name: str = "", force: bool = False):
        self.generated.append((stream_id, day, force))
        folder = self._data_dir / "reports" / ("c" * 64)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{day}.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n")
        return True, f"{day} 的日报已生成（3 条消息）", path

    async def send(self, stream_id: str, day: str = "", *, room_name: str = "", regenerate: bool = False):
        return False, "日报已生成，但发送接口返回失败"


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _config(groups: list[str]) -> GroupDailyLogConfig:
    config = GroupDailyLogConfig()
    config.general.groups = groups
    config.general.group_aliases = ["10001=测试群"]
    return config


def _app(data_dir: Path, groups: list[str]):
    plugin = SimpleNamespace(
        config=_config(groups),
        daily_log=_FakeService(data_dir),
        plugin_version="2.0.0",
    )
    return create_app(plugin), plugin


def test_index_serves_single_page() -> None:
    data_dir = _workdir("_tmp_standalone_page")
    try:
        app, _plugin = _app(data_dir, ["10001"])
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "群日报" in response.text
        assert client.get("/api/health").json()["status"] == "ok"
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_state_and_reports_do_not_need_api_key() -> None:
    data_dir = _workdir("_tmp_standalone_state")
    try:
        app, _plugin = _app(data_dir, ["10001"])
        client = TestClient(app)

        state = client.get("/api/state").json()
        assert state["code"] == 200
        assert state["data"]["version"] == "2.0.0"
        assert state["data"]["groups"][0]["group_id"] == "10001"

        reports = client.get("/api/reports")
        assert reports.status_code == 200
        assert reports.json()["data"] == []
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_generate_rejects_unknown_group_and_empty_config() -> None:
    data_dir = _workdir("_tmp_standalone_generate")
    try:
        app, _plugin = _app(data_dir, ["10001"])
        client = TestClient(app)

        bad = client.post("/api/generate", json={"group_id": "99999", "date": "2026-10-30", "send": False})
        assert bad.status_code == 400
        assert "不在" in bad.json()["detail"]

        app2, _ = _app(data_dir, [])
        empty = TestClient(app2).post("/api/generate", json={"date": "2026-10-30"})
        assert empty.status_code == 400
        assert "配置群号" in empty.json()["detail"]
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_generate_preview_returns_ok() -> None:
    data_dir = _workdir("_tmp_standalone_ok")
    try:
        app, plugin = _app(data_dir, ["10001"])
        client = TestClient(app)

        response = client.post(
            "/api/generate",
            json={"group_id": "10001", "date": "2026-10-30", "send": False, "regenerate": True},
        )
        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["ok"] is True
        assert payload["url"].endswith("date=2026-10-30")
        # regenerate 必须以 force=True 传下去，避免先删旧图
        assert plugin.daily_log.generated[0][2] is True
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_cover_endpoints_roundtrip(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cover_module, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cover_module, "COVER_DIR", tmp_path / "data" / "covers")
    monkeypatch.setattr(cover_module, "META_FILE", tmp_path / "data" / "cover.json")

    data_dir = _workdir("_tmp_standalone_cover")
    try:
        app, _plugin = _app(data_dir, ["10001"])
        client = TestClient(app)

        assert client.get("/api/cover").json()["data"]["exists"] is False
        assert client.get("/api/cover/image").status_code == 404

        buffer = io.BytesIO()
        Image.new("RGB", (640, 360), (90, 140, 220)).save(buffer, "PNG")
        upload = client.post(
            "/api/cover",
            content=buffer.getvalue(),
            headers={"Content-Type": "image/png"},
        )
        assert upload.status_code == 200
        assert upload.json()["data"]["path"] == "covers/cover.png"

        state = client.get("/api/cover").json()["data"]
        assert state["exists"] is True
        assert state["url"] == "/api/cover/image"
        assert client.get("/api/cover/image").status_code == 200

        updated = client.put("/api/cover", json={"focus": "top", "height": 320})
        assert updated.json()["data"]["height"] == 320
        assert updated.json()["data"]["focus"] == "top"

        assert client.delete("/api/cover").status_code == 200
        assert client.get("/api/cover").json()["data"]["exists"] is False
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


def test_router_uses_configured_route_path() -> None:
    data_dir = _workdir("_tmp_standalone_route")
    try:
        config = _config(["10001"])
        plugin = SimpleNamespace(config=config, daily_log=_FakeService(data_dir), plugin_version="2.0.0")

        default_router = DailyLogWebUIRouter(plugin)
        assert default_router.get_route_path() == DEFAULT_ROUTE_PATH

        config.webui.route_path = "/plugins/custom-daily"
        custom_router = DailyLogWebUIRouter(plugin)
        assert custom_router.get_route_path() == "/plugins/custom-daily"
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
