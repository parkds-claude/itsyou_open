import base64
import io
import json
import time
import urllib.error
import urllib.request

from PIL import Image
import providers.base as base

MODEL = "gemini-2.5-flash-image"
INPUT_MAX = 1024


def _crop_to_3_4(img):
    w, h = img.size
    target = 3 / 4
    cur = w / h
    if abs(cur - target) < 0.02:
        return img
    if cur > target:
        new_w = int(h * target)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    new_h = int(w / target)
    top = (h - new_h) // 2
    return img.crop((0, top, w, top + new_h))


def _downscale_b64(raw):
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception:
        return base64.b64encode(raw).decode()
    img = _crop_to_3_4(img)
    img.thumbnail((INPUT_MAX, INPUT_MAX))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode()


# Gemini 가 간헐적으로 5xx/429(일시 과부하·레이트리밋) 또는 빈 응답을 줄 때가 있어
# 짧은 backoff 로 최대 3회 재시도한다(영구 4xx 는 즉시 중단).
_RETRY_HTTP = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def generate_image(image_bytes: bytes, prompt: str, key: str) -> bytes:
    image_b64 = _downscale_b64(image_bytes)
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent")
    body = {"contents": [{"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": image_b64}},
        {"text": prompt},
    ]}]}
    payload = json.dumps(body).encode()
    last_err = None
    for attempt in range(_MAX_ATTEMPTS):
        req = urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            for p in parts:
                if "inlineData" in p:
                    return base64.b64decode(p["inlineData"]["data"])
            # 응답은 왔으나 이미지 없음(드물게 일시적) — 재시도 대상
            last_err = RuntimeError("Gemini 응답에 이미지가 없습니다")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            last_err = RuntimeError(f"Gemini HTTP {e.code}: {detail}")
            if e.code not in _RETRY_HTTP:   # 영구 오류(키 오류 등)는 재시도 무의미
                raise last_err
        except urllib.error.URLError as e:
            last_err = RuntimeError(f"Gemini 연결 실패: {e.reason}")
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(1.5 * (attempt + 1))   # 1.5s, 3s backoff
    raise last_err if last_err else RuntimeError("Gemini 이미지 생성 실패")


base.register("gemini", generate_image)
