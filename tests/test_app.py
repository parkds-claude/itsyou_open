import io
import pytest
from PIL import Image
from unittest.mock import patch
from app import app as flask_app


def _make_jpeg() -> bytes:
    """PIL로 실제 유효한 JPEG 바이트를 생성한다 (Image.verify() 통과용)."""
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (200, 100, 50)).save(buf, format="JPEG")
    return buf.getvalue()

@pytest.fixture
def client():
    flask_app.config["TESTING"] = True
    with flask_app.test_client() as c:
        yield c


def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True


def test_presets_returns_list(client):
    resp = client.get("/presets")
    assert resp.status_code == 200
    data = resp.get_json()
    assert isinstance(data, list)
    assert len(data) > 0


def test_presets_have_id_and_name(client):
    resp = client.get("/presets")
    first = resp.get_json()[0]
    assert "id" in first
    assert "name" in first
    assert "prompt" not in first  # 프롬프트는 클라이언트에 노출 안 함


def test_snap_no_image_returns_400(client):
    # localhost에서 요청해야 보안 게이트 통과
    resp = client.post(
        "/snap",
        data={},
        content_type="multipart/form-data",
        environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
    )
    assert resp.status_code == 400


def test_snap_returns_result_id(client):
    fake_jpeg = _make_jpeg()
    with patch("app.pbase.generate", return_value=fake_jpeg), \
         patch("app.cs.get_provider", return_value="gemini"), \
         patch("app._composite_logo", side_effect=lambda b: b):
        resp = client.post(
            "/snap",
            data={"image": (io.BytesIO(fake_jpeg), "photo.jpg"), "preset_id": "watercolor-anime"},
            content_type="multipart/form-data",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
    assert resp.status_code == 200
    data = resp.get_json()
    assert "result_id" in data
    assert "result_url" in data
    assert "preset_name" in data


def test_result_serves_image(client):
    fake_jpeg = _make_jpeg()
    with patch("app.pbase.generate", return_value=fake_jpeg), \
         patch("app.cs.get_provider", return_value="gemini"), \
         patch("app._composite_logo", side_effect=lambda b: b):
        snap_resp = client.post(
            "/snap",
            data={"image": (io.BytesIO(fake_jpeg), "photo.jpg")},
            content_type="multipart/form-data",
            environ_overrides={"REMOTE_ADDR": "127.0.0.1"},
        )
    assert snap_resp.status_code == 200
    result_id = snap_resp.get_json()["result_id"]
    resp = client.get(f"/result/{result_id}")
    assert resp.status_code == 200
    assert resp.content_type == "image/jpeg"


def test_result_unknown_id_returns_404(client):
    resp = client.get("/result/nonexistent-id")
    assert resp.status_code == 404


# --- 보안 게이트 테스트 ---

def _client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_config_status_localhost_ok():
    c = _client()
    r = c.get("/config/status", environ_overrides={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    assert "provider" in r.get_json()


def test_snap_blocked_from_lan():
    c = _client()
    r = c.post("/snap", environ_overrides={"REMOTE_ADDR": "192.168.0.9"})
    assert r.status_code == 403


def test_result_from_lan_allowed_but_404():
    c = _client()
    r = c.get("/result/" + "0" * 32, environ_overrides={"REMOTE_ADDR": "192.168.0.9"})
    assert r.status_code == 404
