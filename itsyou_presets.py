"""itsyou 포토부스 프리셋 관리 모듈 (정본 = data/itsyou_presets.json).

★ 핵심: 셀카 사진 → Gemini 이미지 변환. 인물(얼굴) 중심, 풍경 치환 금지.
★ 프레이밍: 공통 FRAMING 지시를 생성 직전 모든 prompt 끝에 자동으로 붙인다
   (app.py 가 presets.with_framing(preset['prompt']) 으로 적용).
   개별 프리셋 JSON 에 중복 작성 금지 — FRAMING 한 곳만 관리.
★ 관리: JSON 정본(data/itsyou_presets.json) 직접 편집 → 재기동 없이 즉시 반영(mtime 캐시).
"""
import json
import re
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRESETS_JSON = ROOT / "data" / "itsyou_presets.json"

# 모든 '인물 초상' 프리셋 공통 헤더 — 1인·단체 모두 대응.
# (얼굴/신원을 주인공으로 고정, 인원수 유지, 풍경화 금지.)
PORTRAIT = (
    "This is a PORTRAIT of the person or group of people in the uploaded photo. "
    "Preserve EVERY person's face, facial features, individual identity, hairstyle, "
    "expression and pose as the clear MAIN SUBJECTS — the result must still clearly "
    "look like the SAME person(s). If there are multiple people, keep ALL of them; "
    "do NOT reduce the number of people, do NOT omit or merge anyone. "
    "Do NOT turn it into a landscape or scenery; keep the background simple and "
    "subordinate. Restyle the rendering as follows: "
)

# 모든 프리셋 공통 지시 — 1인·단체(최대 4인) 모두 대응.
# ① 그룹 보존: 여러 명이 있으면 전원 유지, 누락·합성 금지
# ② 프레이밍: 피사체가 프레임을 꽉 채우도록 강제
# 개별 프리셋 JSON 에 중복 작성 금지 — with_framing() 으로 일괄 적용.
FRAMING = (
    " GROUP RULE — applies whenever multiple people appear in the uploaded photo: "
    "keep ALL people in the output without exception — do NOT reduce the number of "
    "people, do NOT omit, merge or replace anyone; preserve every individual's "
    "facial identity and their relative positions exactly as in the original. "
    "FRAMING: compose for a 3:4 VERTICAL PORTRAIT frame (tall, not wide); ALL "
    "subjects (one person or a group) must collectively FILL the frame — large and "
    "prominent with only minimal margins. Maintain the group's spatial arrangement. "
    "Use a tight crop; do NOT leave wide borders or place subjects floating "
    "in a large empty background. "
    "IDENTITY: the output must visibly reflect the uploaded person(s) — preserve "
    "recognizable facial features, skin tone, hairstyle and body type; even in "
    "highly stylized results the subject(s) must remain identifiable. "
    "TEXT: avoid adding readable brand names, real logos or trademarks; if text is "
    "essential to the visual style (e.g. a magazine title), keep it minimal and "
    "generic."
)


def with_framing(prompt: str) -> str:
    """프리셋 prompt 끝에 공통 FRAMING 지시를 1회만 덧붙여 반환.

    이미 적용된 경우(중복 호출) 그대로 반환한다."""
    p = (prompt or "").rstrip()
    if FRAMING.strip() in p:
        return p
    return p + FRAMING

_lock = threading.Lock()
_cache = {"mtime": -1.0, "presets": [], "by_id": {}}


def _read_disk() -> list:
    if not PRESETS_JSON.exists():
        return []
    try:
        data = json.loads(PRESETS_JSON.read_text(encoding="utf-8"))
        return [p for p in data if p.get("id") and p.get("prompt")]
    except Exception as e:  # noqa: BLE001
        import logging; logging.getLogger(__name__).error("[itsyou_presets] load err: %s", e)
        return []


def get_presets() -> list:
    """JSON 정본을 mtime 캐시로 반환. 파일이 바뀌면 자동 재로드(재기동 불필요)."""
    try:
        mt = PRESETS_JSON.stat().st_mtime
    except OSError:
        mt = -1.0
    with _lock:
        if mt != _cache["mtime"]:
            presets = _read_disk()
            _cache.update(mtime=mt, presets=presets,
                          by_id={p["id"]: p for p in presets})
        return list(_cache["presets"])


def get_by_id(pid):
    if not pid:
        return None
    get_presets()  # 캐시 최신화
    with _lock:
        return _cache["by_id"].get(pid)


def save_presets(presets: list) -> None:
    """정본 저장 + 캐시 무효화. 파일 쓰기도 _lock 안에서 직렬화."""
    with _lock:
        PRESETS_JSON.parent.mkdir(parents=True, exist_ok=True)
        PRESETS_JSON.write_text(
            json.dumps(presets, ensure_ascii=False, indent=2), encoding="utf-8")
        _cache["mtime"] = -1.0  # 다음 get_presets()에서 재로드


def _slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (s or "").strip().lower()).strip("_")
    return s[:24]


def add_preset(name: str, prompt: str, portrait: bool = False, pid: str = "") -> dict:
    """새 프리셋 추가. id 미지정 시 영문 slug 자동(한글 이름이면 preset_N). 중복 회피.

    portrait=True 면 PORTRAIT 공통 헤더를 prompt 앞에 자동으로 붙인다."""
    presets = get_presets()
    existing = {p["id"] for p in presets}
    pid = _slugify(pid) or _slugify(name)
    if not pid:
        pid = "preset_%d" % (len(presets) + 1)
    base, n = pid, 2
    while pid in existing:
        pid = "%s%d" % (base, n)
        n += 1
    full = (PORTRAIT + prompt) if portrait else prompt
    item = {"id": pid, "name": (name or pid).strip(), "prompt": full.strip()}
    presets.append(item)
    save_presets(presets)
    return item


def remove_preset(pid: str) -> bool:
    """id 로 삭제. 삭제됐으면 True."""
    presets = get_presets()
    kept = [p for p in presets if p["id"] != pid]
    if len(kept) == len(presets):
        return False
    save_presets(kept)
    return True
