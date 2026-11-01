"""封面存储与裁切测试。"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from group_daily_log import cover
from group_daily_log.render import _cover_crop


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把封面的数据目录指到临时目录，避免污染插件目录。"""
    monkeypatch.setattr(cover, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(cover, "COVER_DIR", tmp_path / "data" / "covers")
    monkeypatch.setattr(cover, "META_FILE", tmp_path / "data" / "cover.json")
    return tmp_path


def _png(width: int = 800, height: int = 600, color=(200, 120, 80)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, "PNG")
    return buffer.getvalue()


def test_upload_saves_and_returns_meta(sandbox: Path) -> None:
    meta = cover.save_upload(_png())
    assert meta["path"] == "covers/cover.png"
    assert meta["height"] == cover.DEFAULT_HEIGHT
    assert cover.image_path() is not None
    assert cover.load_meta()["focus"] == cover.DEFAULT_FOCUS


def test_upload_rejects_non_image(sandbox: Path) -> None:
    with pytest.raises(ValueError):
        cover.save_upload(b"definitely not an image")


def test_update_clamps_and_validates(sandbox: Path) -> None:
    cover.save_upload(_png())
    assert cover.update(height=9999)["height"] == cover.MAX_HEIGHT
    assert cover.update(height=1)["height"] == cover.MIN_HEIGHT
    assert cover.update(focus="nonsense")["focus"] == cover.DEFAULT_FOCUS
    assert cover.update(focus="top-left")["focus"] == "top-left"


def test_clear_removes_everything(sandbox: Path) -> None:
    cover.save_upload(_png())
    cover.clear()
    assert cover.load_meta() == {}
    assert cover.image_path() is None


def test_load_meta_ignores_missing_file(sandbox: Path) -> None:
    cover.save_upload(_png())
    image = cover.image_path()
    assert image is not None
    image.unlink()
    assert cover.load_meta() == {}


def test_character_slot_uses_uploaded_cover(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """封面只替换右上角立绘位，不动页眉与其它区块。"""
    import json

    from group_daily_log.config import GroupDailyLogConfig
    from group_daily_log.render import PosterRenderer, SCALE

    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "data" / "group_daily_log" / "covers"
    folder.mkdir(parents=True)
    Image.new("RGB", (800, 800), (30, 60, 120)).save(folder / "cover.png")
    (tmp_path / "data" / "group_daily_log" / "cover.json").write_text(
        json.dumps({"path": "covers/cover.png", "focus": "center", "height": 300}),
        encoding="utf-8",
    )

    renderer = PosterRenderer(GroupDailyLogConfig(), cover=cover.load_meta())
    art = renderer._load_character()

    assert art is not None
    assert renderer._using_cover is True
    assert art.size == (int(286 * SCALE), int(300 * SCALE))


def test_cover_crop_respects_focus() -> None:
    # 竖图：顶部一条红、底部一条蓝，用来判断焦点裁的是哪一头
    source = Image.new("RGB", (600, 1200), (245, 245, 245))
    top = Image.new("RGB", (600, 100), (220, 30, 30))
    bottom = Image.new("RGB", (600, 100), (30, 60, 220))
    source.paste(top, (0, 0))
    source.paste(bottom, (0, 1100))

    top_crop = _cover_crop(source, 646, 300, "top")
    bottom_crop = _cover_crop(source, 646, 300, "bottom")

    assert top_crop.size == (646 * 2, 300 * 2)
    assert bottom_crop.size == (646 * 2, 300 * 2)

    r_top, g_top, _ = top_crop.getpixel((10, 10))[:3]
    assert r_top > 150 and g_top < 90

    r_bottom, _, b_bottom = bottom_crop.getpixel((10, bottom_crop.height - 10))[:3]
    assert b_bottom > 150 and r_bottom < 90
