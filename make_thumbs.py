"""프리셋 미리보기(작은 그림) 만들기 — static/presets/*.jpg → static/presets/thumb/<이름>.webp

목록 화면의 카드는 가로 100px 안팎이라 원본(512px JPEG, 장당 40~80KB)을 그대로 받으면 대부분이 낭비다.
프리셋 그림을 추가하거나 바꾼 뒤 한 번 돌린다:  python make_thumbs.py
"""
from pathlib import Path

from PIL import Image

MAX_SIDE = 240      # 카드 100px × 화면 배율 2.4 까지 선명하다
QUALITY = 80
_PRESETS = Path(__file__).resolve().parent / "static" / "presets"


def make_thumb(src: Path, out_dir: Path) -> Path:
    """src 그림을 긴 변 MAX_SIDE 이하 WebP 로 줄여 out_dir/<이름>.webp 에 쓰고 그 경로를 돌려준다."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"{src.stem}.webp"
    with Image.open(src) as im:
        im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)   # 비율 유지, 확대하지 않는다
        im.save(dst, format="WEBP", quality=QUALITY, method=6)
    return dst


if __name__ == "__main__":
    for jpg in sorted(_PRESETS.glob("*.jpg")):
        out = make_thumb(jpg, _PRESETS / "thumb")
        print(f"{jpg.name} {jpg.stat().st_size // 1024}KB → thumb/{out.name} {out.stat().st_size // 1024}KB")
