"""OpenAI gpt-image-1 이미지 편집 API. 셀카를 입력으로 받아 프롬프트대로 변환.
multipart/form-data로 image + prompt 전송, b64_json 응답."""
import base64
import io
import json
import urllib.request

from PIL import Image
import providers.base as base

MODEL = "gpt-image-1"
INPUT_MAX = 1024
_API = "https://api.openai.com/v1/images/edits"


def _prep_png(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    w, h = img.size
    target = 3 / 4
    cur = w / h
    if cur > target:
        nw = int(h * target); left = (w - nw) // 2
        img = img.crop((left, 0, left + nw, h))
    elif cur < target:
        nh = int(w / target); top = (h - nh) // 2
        img = img.crop((0, top, w, top + nh))
    img.thumbnail((INPUT_MAX, INPUT_MAX))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _multipart(fields: dict, files: dict):
    boundary = "----itsyouopenaiboundary"
    body = b""
    for k, v in fields.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode()
    for k, (fname, data, ctype) in files.items():
        body += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fname}\"\r\n"
                 f"Content-Type: {ctype}\r\n\r\n").encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    return body, boundary


def generate_image(image_bytes: bytes, prompt: str, key: str) -> bytes:
    png = _prep_png(image_bytes)
    body, boundary = _multipart(
        {"model": MODEL, "prompt": prompt, "size": "1024x1536", "n": "1"},
        {"image": ("selfie.png", png, "image/png")},
    )

    def build():
        return urllib.request.Request(
            _API, data=body, method="POST",
            headers={"Authorization": f"Bearer {key}",
                     "Content-Type": f"multipart/form-data; boundary={boundary}"})

    def extract(data):
        # data 가 빈 배열일 수 있으므로 IndexError 없이 안전 파싱
        items = data.get("data") or []
        if not items:
            return None
        b64 = items[0].get("b64_json")
        return base64.b64decode(b64) if b64 else None

    return base.call_with_retry(build, extract)


base.register("openai", generate_image)
