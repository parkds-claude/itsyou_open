import functools
import hmac
import io
import re
import time
import threading
import uuid
from datetime import date
from pathlib import Path
from flask import Flask, jsonify, request, send_file, abort, render_template

from PIL import Image
import itsyou_presets as ip
import config_store as cs
import security as sec
import providers.base as pbase
import providers.gemini   # noqa: F401  (registry 등록)
import providers.openai   # noqa: F401

_ROOT = Path(__file__).resolve().parent
RESULTS_DIR = Path.home() / ".itsyou" / "results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

_CLEANUP_INTERVAL = 600
_RESULT_MAX_AGE = 3600
_DAILY_CAP = 200   # 하루 생성 상한 (과금 가드)

def _cleanup_old_results():
    while True:
        try:
            now = time.time()
            for p in RESULTS_DIR.glob("*.jpg"):
                if now - p.stat().st_mtime > _RESULT_MAX_AGE:
                    p.unlink(missing_ok=True)
        except Exception:
            pass
        time.sleep(_CLEANUP_INTERVAL)

threading.Thread(target=_cleanup_old_results, daemon=True).start()

_PRESETS_JSON = _ROOT / "data" / "itsyou_presets.json"
_PRESETS_DEFAULT = _ROOT / "defaults" / "itsyou_presets.json"
if not _PRESETS_JSON.exists() and _PRESETS_DEFAULT.exists():
    import shutil
    _PRESETS_JSON.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(_PRESETS_DEFAULT, _PRESETS_JSON)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
_rate = sec.RateLimiter(per_ip_per_min=5, global_per_min=30)


@app.before_request
def _gate():
    if request.path.startswith("/static/"):
        return None
    if not sec.is_allowed(request.path, request.remote_addr):
        return jsonify({"error": "forbidden"}), 403


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/config/status")
def config_status():
    return jsonify(cs.status())


@app.post("/config/key")
def config_set_key():
    d = request.get_json(silent=True) or {}
    provider = (d.get("provider") or "").strip()
    key = (d.get("key") or "").strip()
    if provider not in ("gemini", "openai") or not key:
        return jsonify({"error": "provider and key required"}), 400
    test_png = _tiny_png()
    try:
        pbase.generate(provider, test_png, "test", key=key)
    except Exception:
        app.logger.warning("키 검증 실패 provider=%s", provider)
        return jsonify({"error": "invalid key or provider unreachable"}), 400
    cs.set_key(provider, key)
    return jsonify({"ok": True})


def _tiny_png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (200, 200, 200)).save(buf, format="PNG")
    return buf.getvalue()


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True})

@app.get("/presets")
def presets():
    return jsonify([{"id": p["id"], "name": p["name"]} for p in ip.get_presets()])

@app.post("/snap")
def snap():
    if not _rate.allow(request.remote_addr):
        return jsonify({"error": "rate limited"}), 429
    if "image" not in request.files:
        return jsonify({"error": "image required"}), 400

    image_bytes = request.files["image"].read()
    try:
        Image.open(io.BytesIO(image_bytes)).verify()
    except Exception:
        return jsonify({"error": "invalid image"}), 400

    provider = cs.get_provider()
    if not provider:
        return jsonify({"error": "not configured"}), 400

    preset_id = (request.form.get("preset_id") or "").strip() or None
    preset = ip.get_by_id(preset_id)
    if preset is None:
        import random
        all_presets = ip.get_presets()
        if not all_presets:
            return jsonify({"error": "no presets configured"}), 500
        preset = random.choice(all_presets)

    # 일일 상한 슬롯을 원자적으로 예약(과금 가드). 생성 실패 시 롤백.
    today = date.today().isoformat()
    if not cs.reserve_usage(today, _DAILY_CAP):
        return jsonify({"error": "daily limit reached"}), 503

    prompt = ip.with_framing(preset["prompt"])
    try:
        result_bytes = pbase.generate(provider, image_bytes, prompt)
    except Exception:
        cs.release_usage(today)
        app.logger.exception("이미지 생성 실패 preset=%s", preset["id"])
        return jsonify({"error": "image generation failed"}), 502

    result_id = uuid.uuid4().hex
    result_bytes = _composite_logo(result_bytes)
    (RESULTS_DIR / f"{result_id}.jpg").write_bytes(result_bytes)

    return jsonify({
        "result_id": result_id,
        "result_url": f"/result/{result_id}",
        "preset_name": preset["name"],
    })


@app.get("/result/<result_id>")
def result(result_id):
    if not re.fullmatch(r"[0-9a-f]{32}", result_id):
        abort(404)
    path = RESULTS_DIR / f"{result_id}.jpg"
    if not path.exists():
        abort(404)
    return send_file(path, mimetype="image/jpeg")


@app.get("/print/<result_id>")
def print_page(result_id):
    if not re.fullmatch(r"[0-9a-f]{32}", result_id):
        abort(404)
    return render_template("print.html", result_id=result_id)


# ── 행사 로고 ────────────────────────────────────────────────────────────────
_LOGO_EXTS = ["jpg", "jpeg", "png", "webp", "gif"]
_MIME_TO_EXT = {"image/jpeg": "jpg", "image/png": "png",
                "image/webp": "webp", "image/gif": "gif"}


def _logo_path():
    for ext in _LOGO_EXTS:
        p = _ROOT / "data" / f"event_logo.{ext}"
        if p.exists():
            return p
    return None


def _composite_logo(image_bytes: bytes) -> bytes:
    """생성된 이미지 위에 행사 로고를 합성해 JPEG bytes로 반환.
    로고 없으면 원본 그대로 반환."""
    logo_p = _logo_path()
    if not logo_p:
        return image_bytes
    try:
        with Image.open(io.BytesIO(image_bytes)) as base_raw:
            base = base_raw.convert("RGBA")
        with Image.open(logo_p) as logo_raw:
            logo = logo_raw.convert("RGBA")

        # 로고 크기: 이미지 너비의 35%, 최대 280px
        target_w = min(int(base.width * 0.35), 280)
        ratio = target_w / logo.width
        target_h = int(logo.height * ratio)
        logo = logo.resize((target_w, target_h), Image.LANCZOS)

        # 위치: 우측 상단, 여백 16px
        margin = 16
        x = base.width - target_w - margin
        y = margin

        base.paste(logo, (x, y), logo)

        out = io.BytesIO()
        base.convert("RGB").save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception as exc:
        app.logger.warning("로고 합성 실패: %s", exc)
        return image_bytes


@app.get("/event-config")
def event_config():
    return jsonify({"has_logo": _logo_path() is not None})


@app.get("/event-logo")
def event_logo():
    p = _logo_path()
    if not p:
        abort(404)
    return send_file(p)


# ── 어드민 ──────────────────────────────────────────────────────────────────

def _admin_auth(f):
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if request.remote_addr not in sec._LOCALHOST:
            return jsonify({"error": "forbidden"}), 403
        if not hmac.compare_digest(request.headers.get("X-Admin-Key", ""),
                                   cs.get_or_create_admin_key()):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapped


@app.get("/admin")
def admin_page():
    return render_template("admin.html")


@app.get("/admin/api/auth")
def admin_auth_check():
    if request.remote_addr not in sec._LOCALHOST:
        return jsonify({"error": "forbidden"}), 403
    if not hmac.compare_digest(request.headers.get("X-Admin-Key", ""),
                               cs.get_or_create_admin_key()):
        return jsonify({"error": "unauthorized"}), 401
    return jsonify({"ok": True, "auth_required": True})


@app.get("/admin/api/presets")
@_admin_auth
def admin_presets_list():
    result = []
    for p in ip.get_presets():
        jpg = _ROOT / "static" / "presets" / f"{p['id']}.jpg"
        result.append({**p, "thumb_url": f"/static/presets/{p['id']}.jpg" if jpg.exists() else None})
    return jsonify(result)


@app.post("/admin/api/presets")
@_admin_auth
def admin_presets_add():
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip()
    prompt = (d.get("prompt") or "").strip()
    if not name or not prompt:
        return jsonify({"error": "name and prompt required"}), 400
    item = ip.add_preset(name, prompt, portrait=bool(d.get("portrait")), pid=(d.get("id") or "").strip())
    return jsonify(item), 201


@app.route("/admin/api/presets/<pid>", methods=["PUT", "DELETE"])
@_admin_auth
def admin_presets_item(pid):
    if request.method == "DELETE":
        return (jsonify({"ok": True}) if ip.remove_preset(pid)
                else (jsonify({"error": "not found"}), 404))
    d = request.get_json(silent=True) or {}
    presets = ip.get_presets()
    for p in presets:
        if p["id"] == pid:
            if "name" in d:
                p["name"] = d["name"].strip()
            if "prompt" in d:
                p["prompt"] = d["prompt"].strip()
            ip.save_presets(presets)
            return jsonify({"ok": True})
    return jsonify({"error": "not found"}), 404


@app.post("/admin/api/event-config/logo")
@_admin_auth
def admin_logo_upload():
    if "logo" not in request.files:
        return jsonify({"error": "logo file required"}), 400
    f = request.files["logo"]
    data = f.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        return jsonify({"error": "최대 1MB까지 업로드 가능합니다"}), 400
    # Content-Type 헤더 대신 PIL로 실제 파일 파싱해서 포맷 확인
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format.lower() if img.format else "jpeg"
    except Exception:
        return jsonify({"error": "유효한 이미지 파일이 아닙니다"}), 400
    ext = {"jpeg": "jpg", "png": "png", "webp": "webp", "gif": "gif"}.get(fmt, "jpg")
    # 기존 로고 제거 후 로컬 저장
    for old_ext in _LOGO_EXTS:
        p = _ROOT / "data" / f"event_logo.{old_ext}"
        if p.exists():
            p.unlink()
    save_path = _ROOT / "data" / f"event_logo.{ext}"
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_bytes(data)
    return jsonify({"ok": True})


@app.delete("/admin/api/event-config/logo")
@_admin_auth
def admin_logo_delete():
    for ext in _LOGO_EXTS:
        p = _ROOT / "data" / f"event_logo.{ext}"
        if p.exists():
            p.unlink()
    return jsonify({"ok": True})


@app.post("/admin/api/presets-order")
@_admin_auth
def admin_presets_reorder():
    ids = (request.get_json(silent=True) or {}).get("ids", [])
    presets = ip.get_presets()
    by_id = {p["id"]: p for p in presets}
    reordered = [by_id[i] for i in ids if i in by_id]
    seen = set(ids)
    for p in presets:
        if p["id"] not in seen:
            reordered.append(p)
    ip.save_presets(reordered)
    return jsonify({"ok": True})


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5080))
    _admin_key = cs.get_or_create_admin_key()
    # 키 전체를 터미널에 평문 노출하지 않는다(스크롤 히스토리·어깨너머 탈취 방지).
    # 어드민 페이지 입력용 전체 키는 ~/.itsyou/config.json 의 admin_key 필드에서 확인한다.
    print(f"[itsyou_open] 어드민 키: {_admin_key[:8]}…  (전체 키는 ~/.itsyou/config.json 의 admin_key)")
    print(f"[itsyou_open] 어드민 페이지: http://localhost:{port}/admin")
    print(f"[itsyou_open] http://localhost:{port} 으로 접속하세요. 같은 와이파이의 폰은 QR로 사진만 받을 수 있습니다.")
    app.run(host="0.0.0.0", port=port,
            debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")
