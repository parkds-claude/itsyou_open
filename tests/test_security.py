import ipaddress
import time
import security as sec

LOCAL_ONLY = "/snap"
PUBLIC = "/result/abc"


def test_kiosk_ip_allows_kiosk_not_config(monkeypatch):
    # ITSYOU_KIOSK_IPS 로 신뢰된 출처라도 /config·/admin 은 절대 허용하지 않는다.
    monkeypatch.setattr(sec, "_TRUSTED_NETS", [ipaddress.ip_network("192.168.0.0/24")])
    assert sec.is_allowed("/snap", "192.168.0.7") is True
    assert sec.is_allowed("/kiosk/preset/x", "192.168.0.7") is True
    assert sec.is_allowed("/config/key", "192.168.0.7") is False
    assert sec.is_allowed("/admin/api/presets", "192.168.0.7") is False
    assert sec.is_allowed("/kiosk/preset/x", "10.0.0.9") is False  # 범위 밖은 차단

def test_localhost_required_for_sensitive():
    assert sec.is_allowed(LOCAL_ONLY, "127.0.0.1") is True
    assert sec.is_allowed(LOCAL_ONLY, "192.168.0.50") is False

def test_public_path_allows_lan():
    assert sec.is_allowed(PUBLIC, "192.168.0.50") is True
    assert sec.is_allowed(PUBLIC, "127.0.0.1") is True

def test_admin_is_local_only():
    assert sec.is_allowed("/admin/api/presets", "10.0.0.2") is False
    assert sec.is_allowed("/admin/api/presets", "::1") is True

def test_rate_limit_per_ip(monkeypatch):
    rl = sec.RateLimiter(per_ip_per_min=3, global_per_min=100)
    t = [1000.0]
    monkeypatch.setattr(sec.time, "time", lambda: t[0])
    for _ in range(3):
        assert rl.allow("1.1.1.1") is True
    assert rl.allow("1.1.1.1") is False
    t[0] += 61
    assert rl.allow("1.1.1.1") is True

def test_local_only_case_insensitive():
    # 대소문자 변형으로 민감 경로 우회 불가(방어심층)
    assert sec.is_allowed("/Snap", "192.168.0.50") is False
    assert sec.is_allowed("/CONFIG/key", "10.0.0.2") is False
    assert sec.is_allowed("/Admin/api/presets", "10.0.0.2") is False


# ── 터널·프록시를 거친 요청은 '이 컴퓨터'가 아니다 ──────────────────────────────

def test_proxy_headers_are_detected():
    assert sec.came_through_proxy({"CF-Connecting-IP": "203.0.113.5"}) is True
    assert sec.came_through_proxy({"X-Forwarded-For": "203.0.113.5"}) is True
    assert sec.came_through_proxy({"User-Agent": "x"}) is False


def test_proxied_request_is_never_localhost():
    # 터널은 같은 기기에서 돌아서 모든 방문자가 127.0.0.1 로 보인다 — 그래도 민감 경로는 막아야 한다.
    for path in ("/snap", "/kiosk/preset/x", "/config/key", "/config/status", "/admin/api/presets"):
        assert sec.is_allowed(path, "127.0.0.1", proxied=True) is False, path
    assert sec.is_allowed(PUBLIC, "127.0.0.1", proxied=True) is True


def test_proxied_request_is_not_trusted_by_ip(monkeypatch):
    monkeypatch.setattr(sec, "_TRUSTED_NETS", [ipaddress.ip_network("127.0.0.0/8")])
    assert sec.is_trusted("127.0.0.1", proxied=True) is False


def test_kiosk_session_can_shoot_and_edit_but_not_configure():
    kw = dict(proxied=True, kiosk_session=True)
    assert sec.is_allowed("/snap", "127.0.0.1", **kw) is True
    assert sec.is_allowed("/kiosk/preset/x", "127.0.0.1", **kw) is True
    assert sec.is_allowed("/config/status", "127.0.0.1", **kw) is True
    assert sec.is_allowed("/config/key", "127.0.0.1", **kw) is False
    assert sec.is_allowed("/admin/api/presets", "127.0.0.1", **kw) is False


def test_trusted_kiosk_ip_can_read_config_status(monkeypatch):
    # 화면이 처음 뜰 때 '설정됐는가'를 읽는다 — 못 읽으면 촬영 기기에 API 키 입력 화면이 뜬다.
    monkeypatch.setattr(sec, "_TRUSTED_NETS", [ipaddress.ip_network("192.168.0.0/24")])
    assert sec.is_allowed("/config/status", "192.168.0.7") is True
    assert sec.is_allowed("/config/status", "10.0.0.9") is False
    assert sec.is_allowed("/config/key", "192.168.0.7") is False


def test_only_photo_and_health_are_open_without_pin():
    for path in ("/result/abc", "/healthz", "/sw.js", "/pin"):
        assert sec.is_open_without_pin(path) is True, path
    for path in ("/", "/presets", "/snap", "/config/status", "/admin", "/print/abc", "/pinx", "/resultx"):
        assert sec.is_open_without_pin(path) is False, path


# ── 공용 비밀번호: 기기 기억표(쿠키 값)와 대입 방지 ──────────────────────────────

def test_kiosk_token_roundtrip_and_rejections():
    tok = sec.make_kiosk_token("secret-a", "2468")
    assert sec.kiosk_token_ok(tok, "secret-a", "2468", max_age=60) is True
    assert sec.kiosk_token_ok(tok, "secret-a", "1357", max_age=60) is False   # 비밀번호를 바꾸면 기기들은 다시 입력
    assert sec.kiosk_token_ok(tok, "secret-b", "2468", max_age=60) is False   # 다른 서버 열쇠로 만든 표
    assert sec.kiosk_token_ok(tok[:-2] + "xx", "secret-a", "2468", max_age=60) is False
    assert sec.kiosk_token_ok("", "secret-a", "2468", max_age=60) is False
    assert sec.kiosk_token_ok("garbage", "secret-a", "2468", max_age=60) is False
    assert "2468" not in tok                                                  # 표에 비밀번호가 그대로 실리지 않는다


def test_kiosk_token_expires(monkeypatch):
    tok = sec.make_kiosk_token("secret-a", "2468")
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 120)
    assert sec.kiosk_token_ok(tok, "secret-a", "2468", max_age=60) is False


def test_pin_guard_locks_after_five_tries_then_reopens(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(sec.time, "time", lambda: t[0])
    g = sec.PinGuard(burst=5, burst_window=900, daily=20)
    assert [g.begin() for _ in range(5)] == [True] * 5
    assert g.begin() is False
    t[0] += 901
    assert g.begin() is True


def test_pin_guard_daily_cap(monkeypatch):
    t = [1000.0]
    monkeypatch.setattr(sec.time, "time", lambda: t[0])
    g = sec.PinGuard(burst=5, burst_window=900, daily=20)
    for _ in range(4):
        assert [g.begin() for _ in range(5)] == [True] * 5
        t[0] += 901
    assert g.begin() is False          # 하루 20번을 다 썼다 — 15분이 지나도 잠겨 있다
    t[0] += 86400
    assert g.begin() is True


def test_pin_guard_success_gives_the_try_back():
    g = sec.PinGuard(burst=5, burst_window=900, daily=20)
    for _ in range(50):                # 맞게 넣는 기기가 아무리 많아도 잠기지 않는다
        assert g.begin() is True
        g.succeeded()
