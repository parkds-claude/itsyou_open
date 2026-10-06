"""프리셋 미리보기 — 목록 화면은 원본(512px JPEG) 대신 작은 그림(240px WebP)을 받는다."""
from pathlib import Path

from PIL import Image

import make_thumbs as mt
from app import app as flask_app

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "static" / "presets"


def _jpeg(path: Path, size) -> Path:
    Image.new("RGB", size, (200, 100, 50)).save(path, format="JPEG")
    return path


def test_make_thumb_writes_240px_webp(tmp_path):
    dst = mt.make_thumb(_jpeg(tmp_path / "a.jpg", (512, 512)), tmp_path / "thumb")
    assert dst == tmp_path / "thumb" / "a.webp"
    with Image.open(dst) as im:
        assert im.format == "WEBP"
        assert im.size == (240, 240)


def test_make_thumb_keeps_proportions(tmp_path):
    dst = mt.make_thumb(_jpeg(tmp_path / "tall.jpg", (864, 1152)), tmp_path / "thumb")
    with Image.open(dst) as im:
        assert im.size == (180, 240)


def test_make_thumb_never_enlarges(tmp_path):
    dst = mt.make_thumb(_jpeg(tmp_path / "tiny.jpg", (100, 80)), tmp_path / "thumb")
    with Image.open(dst) as im:
        assert im.size == (100, 80)


def test_every_preset_image_has_a_small_thumb():
    jpgs = sorted(PRESETS.glob("*.jpg"))
    assert jpgs
    for jpg in jpgs:
        thumb = PRESETS / "thumb" / f"{jpg.stem}.webp"
        assert thumb.exists(), f"{jpg.name} 의 미리보기가 없다 — python make_thumbs.py 를 돌릴 것"
        assert thumb.stat().st_size * 3 < jpg.stat().st_size, jpg.name
        with Image.open(thumb) as im:
            assert max(im.size) <= mt.MAX_SIDE, jpg.name


def test_thumb_is_served_as_webp():
    r = flask_app.test_client().get("/static/presets/thumb/storybook.webp")
    assert r.status_code == 200
    assert r.mimetype == "image/webp"


def test_preset_list_asks_for_the_small_thumb_first():
    # 화면 스크립트가 원본 경로로 되돌아가면 첫 화면이 다시 무거워진다(약 740KB → 약 100KB).
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "/static/presets/thumb/${thumbId}.webp" in js
