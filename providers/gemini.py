import base64
import io
import json
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


def generate_image(image_bytes: bytes, prompt: str, key: str) -> bytes:
    image_b64 = _downscale_b64(image_bytes)
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent")
    payload = json.dumps({"contents": [{"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": image_b64}},
        {"text": prompt},
    ]}]}).encode()

    def build():
        return urllib.request.Request(
            url, data=payload,
            headers={"Content-Type": "application/json", "x-goog-api-key": key})

    def extract(data):
        # candidates 가 빈 배열(안전필터 발동)일 수 있으므로 IndexError 없이 안전 파싱
        cands = data.get("candidates") or []
        if not cands:
            return None
        parts = cands[0].get("content", {}).get("parts", []) or []
        for p in parts:
            if "inlineData" in p:
                return base64.b64decode(p["inlineData"]["data"])
        return None

    return base.call_with_retry(build, extract)


base.register("gemini", generate_image)
