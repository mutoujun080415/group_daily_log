"""日报产物路径与历史列表的单元测试。"""

from __future__ import annotations

import shutil
from pathlib import Path

from group_daily_log.reports import (
    is_valid_day,
    list_reports,
    report_file,
    report_url,
    slug_for_stream,
)


def _workdir(name: str) -> Path:
    path = Path(__file__).resolve().parent / name
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_slug_for_stream_is_safe() -> None:
    assert slug_for_stream("qq:123456") == "qq_123456"
    assert slug_for_stream("a" * 80) == "a" * 64
    assert slug_for_stream("") == "stream"


def test_is_valid_day() -> None:
    assert is_valid_day("2026-10-31")
    assert not is_valid_day("2026-13-01")
    assert not is_valid_day("../../etc/passwd")
    assert not is_valid_day("2026-1-1")


def test_list_reports_sorted_and_limited() -> None:
    root = _workdir("_tmp_reports")
    try:
        folder = root / "reports" / ("a" * 64)
        folder.mkdir(parents=True)
        for day in ("2026-10-29", "2026-10-31", "2026-10-30"):
            (folder / f"{day}.png").write_bytes(b"png")
        (folder / "not-a-date.png").write_bytes(b"png")

        entries = list_reports(root, limit=2)
        assert [item.day for item in entries] == ["2026-10-31", "2026-10-30"]
        assert entries[0].size == 3
        assert "api/image?slug=" in entries[0].to_dict()["url"]

        assert len(list_reports(root, limit=0)) == 3
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_report_file_rejects_unknown_paths() -> None:
    root = _workdir("_tmp_report_file")
    try:
        slug = "b" * 64
        folder = root / "reports" / slug
        folder.mkdir(parents=True)
        target = folder / "2026-10-31.png"
        target.write_bytes(b"png")

        assert report_file(root, slug, "2026-10-31") == target.resolve()
        assert report_file(root, slug, "../../secret") is None
        assert report_file(root, "c" * 64, "2026-10-31") is None
        assert report_file(root, "../" + slug, "2026-10-31") is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_report_url_shape() -> None:
    assert report_url("abc", "2026-10-31") == "/plugins/group-daily-log/api/image?slug=abc&date=2026-10-31"
