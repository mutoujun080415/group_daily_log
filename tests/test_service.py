"""服务层的落盘行为：重新生成时不能把旧日报弄丢。"""

from __future__ import annotations

from pathlib import Path

import pytest

from group_daily_log.service import DailyLogService


class _FakeImage:
    """够用的 Pillow 替身：只实现 save/width/height。"""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.width = 750
        self.height = 1000

    def save(self, path: str | Path, fmt: str | None = None) -> None:
        if self.fail:
            raise OSError("disk full")
        Path(path).write_bytes(b"PNG")


def test_save_report_replaces_files(tmp_path: Path) -> None:
    png = tmp_path / "2026-11-01.png"
    meta = tmp_path / "2026-11-01.json"
    png.write_bytes(b"OLD")

    DailyLogService._save_report(_FakeImage(), png, meta, {"date": "2026-11-01"})

    assert png.read_bytes() == b"PNG"
    assert meta.exists()
    assert not list(tmp_path.glob(".*.tmp"))


def test_save_report_keeps_old_file_when_render_fails(tmp_path: Path) -> None:
    png = tmp_path / "2026-11-01.png"
    meta = tmp_path / "2026-11-01.json"
    png.write_bytes(b"OLD")

    with pytest.raises(OSError):
        DailyLogService._save_report(_FakeImage(fail=True), png, meta, {"date": "2026-11-01"})

    # 旧图必须原地不动，JSON 也不能写一半
    assert png.read_bytes() == b"OLD"
    assert not meta.exists()
    assert not list(tmp_path.glob(".*.tmp"))
