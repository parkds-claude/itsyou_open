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
