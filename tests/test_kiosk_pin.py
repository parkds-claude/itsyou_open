"""원격 기기용 공용 비밀번호 — 터널이나 같은 와이파이로 들어온 기기는 비밀번호를 넣어야 부스를 쓴다.

터널(cloudflared 등)은 서버와 같은 기기에서 돌아서 모든 방문자가 127.0.0.1 로 보인다.
그래서 여기 시험의 '바깥 기기'는 REMOTE_ADDR 가 127.0.0.1 인 채로 프록시 헤더만 붙어 있다.
"""
import pytest

import app as app_module
import security as sec
from app import app as flask_app

PIN = "2468"
VIA_TUNNEL = {"CF-Connecting-IP": "203.0.113.5", "CF-Visitor": '{"scheme":"https"}'}
BASE = "http://booth.example"          # 터널 뒤의 앱은 늘 http 로 받는다
BOOTH, PIN_SCREEN = b'id="screen-camera"', b'id="pin"'


@pytest.fixture
def booth(monkeypatch):
    """비밀번호가 걸린 부스. 실제 설정 파일(~/.itsyou)은 건드리지 않는다."""
    state = {"pin": PIN}
    monkeypatch.setattr(app_module.cs, "get_kiosk_pin", lambda: state["pin"])
    monkeypatch.setattr(app_module.cs, "get_or_create_session_secret", lambda: "test-secret")
    monkeypatch.setattr(app_module, "_pin_guard", sec.PinGuard())
    flask_app.config["TESTING"] = True
    return state


def _get(c, path):
    return c.get(path, headers=VIA_TUNNEL, base_url=BASE)


def _post(c, path, **kw):
    return c.post(path, headers=VIA_TUNNEL, base_url=BASE, **kw)


def _login(c, pin=PIN):
    return _post(c, "/pin", json={"pin": pin})


def test_remote_visitor_sees_pin_screen_instead_of_booth(booth):
    r = _get(flask_app.test_client(), "/")
    assert r.status_code == 200
    assert PIN_SCREEN in r.data and BOOTH not in r.data


def test_remote_visitor_cannot_use_booth_without_pin(booth):
    c = flask_app.test_client()
    for path in ("/presets", "/config/status", "/event-config", "/kiosk/preset/storybook", "/admin"):
        assert _get(c, path).status_code == 401, path
    assert _post(c, "/snap").status_code == 401
    assert _post(c, "/kiosk/preset", json={"name": "x", "prompt": "y"}).status_code == 401
    assert _post(c, "/config/key", json={"provider": "gemini", "key": "k"}).status_code == 401


def test_guest_can_still_fetch_a_photo_without_pin(booth):
    c = flask_app.test_client()
    assert _get(c, "/result/" + "0" * 32).status_code == 404      # 열려 있다(그 사진이 없을 뿐)
    assert _get(c, "/healthz").status_code == 200
    assert _get(c, "/static/style.css").status_code == 200


def test_wrong_pin_is_rejected(booth):
    c = flask_app.test_client()
    r = _login(c, "0000")
    assert r.status_code == 401
    assert "Set-Cookie" not in r.headers
    assert PIN_SCREEN in _get(c, "/").data


def test_right_pin_opens_the_booth(booth):
    c = flask_app.test_client()
    r = _login(c)
    assert r.status_code == 200
    cookie = r.headers["Set-Cookie"]
    for attr in ("HttpOnly", "SameSite=Lax", "Secure", "Max-Age=2592000", "Path=/"):
        assert attr in cookie, attr
    assert PIN not in cookie
    assert BOOTH in _get(c, "/").data
    assert _get(c, "/config/status").status_code == 200
    assert _get(c, "/presets").status_code == 200
    assert _post(c, "/snap", data={}, content_type="multipart/form-data").status_code == 400   # 문은 통과, 사진이 없을 뿐


def test_pin_holder_still_cannot_set_keys_or_open_admin(booth):
    c = flask_app.test_client()
    _login(c)
    assert _post(c, "/config/key", json={"provider": "gemini", "key": "k"}).status_code == 403
    assert _get(c, "/admin").status_code == 403
    assert _get(c, "/admin/api/auth").status_code == 403


def test_five_wrong_tries_lock_even_the_right_pin(booth):
    c = flask_app.test_client()
    for _ in range(5):
        assert _login(c, "0000").status_code == 401
    assert _login(c).status_code == 429
    assert PIN_SCREEN in _get(c, "/").data


def test_changing_the_pin_makes_devices_enter_it_again(booth):
    c = flask_app.test_client()
    _login(c)
    booth["pin"] = "1357"
    assert PIN_SCREEN in _get(c, "/").data


def test_odd_pin_input_is_just_wrong_not_a_crash(booth):
    c = flask_app.test_client()
    assert _post(c, "/pin", json={"pin": "9" * 5000}).status_code == 401
    assert _post(c, "/pin", json={"pin": ["2468"]}).status_code == 401
    assert _post(c, "/pin", data="not json", content_type="text/plain").status_code == 401
    assert _post(c, "/pin", json={"pin": "２４６８"}).status_code == 401       # 전각 숫자


def test_this_computer_needs_no_pin(booth):
    c = flask_app.test_client()
    here = {"REMOTE_ADDR": "127.0.0.1"}
    assert BOOTH in c.get("/", environ_overrides=here).data
    assert c.get("/config/status", environ_overrides=here).status_code == 200
    assert c.post("/snap", data={}, content_type="multipart/form-data", environ_overrides=here).status_code == 400


def test_device_on_same_wifi_can_enter_with_pin(booth):
    c = flask_app.test_client()
    lan = dict(base_url="http://192.168.0.5:5080", environ_overrides={"REMOTE_ADDR": "192.168.0.9"})
    assert PIN_SCREEN in c.get("/", **lan).data
    r = c.post("/pin", json={"pin": PIN}, **lan)
    assert r.status_code == 200
    assert "Secure" not in r.headers["Set-Cookie"]          # http 로 쓰는 와이파이 운용에서는 Secure 를 붙이면 저장되지 않는다
    assert BOOTH in c.get("/", **lan).data
    assert c.post("/snap", data={}, content_type="multipart/form-data", **lan).status_code == 400


def test_without_a_pin_tunnel_visitors_are_strangers(booth):
    # 비밀번호를 안 건 채 터널로 내보낸 경우: 화면은 보이지만 촬영·설정·어드민은 전부 막힌다.
    booth["pin"] = ""
    c = flask_app.test_client()
    assert BOOTH in _get(c, "/").data
    assert _post(c, "/snap").status_code == 403
    assert _get(c, "/config/status").status_code == 403
    assert _get(c, "/kiosk/preset/storybook").status_code == 403
    assert _get(c, "/admin/api/auth").status_code == 403
    assert _login(c).status_code == 404                     # 비밀번호 기능이 꺼져 있다
