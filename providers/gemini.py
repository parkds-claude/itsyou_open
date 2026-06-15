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
    body = {"contents": [{"parts": [
        {"inlineData": {"mimeType": "image/jpeg", "data": image_b64}},
        {"text": prompt},
    ]}]}
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": key})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
    for p in parts:
        if "inlineData" in p:
            return base64.b64decode(p["inlineData"]["data"])
    raise RuntimeError("no image in response")


base.register("gemini", generate_image)
