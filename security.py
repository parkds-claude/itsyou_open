"""엔드포인트별 접근 정책 + rate limit.
민감(촬영/설정/키오스크/어드민)은 기본 localhost 전용, 조회는 LAN 허용.
X-Forwarded-For는 신뢰하지 않는다.

폰을 키오스크로 쓰는 경우(서버는 맥미니, 촬영 기기는 폰):
환경변수 ITSYOU_KIOSK_IPS 에 신뢰할 기기 IP/CIDR 를 콤마로 지정하면
해당 출처에 한해 민감 경로 접근을 허용한다. 기본값은 빈 값 = localhost 전용(안전 기본).
예) ITSYOU_KIOSK_IPS="192.168.0.42"  또는  "192.168.0.0/24"
"""
import ipaddress
import os
import threading
import time
from collections import deque

_LOCALHOST = {"127.0.0.1", "::1", "localhost"}

# 민감 경로 접두사 (기본 localhost 전용, 신뢰 IP 허용 가능)
_LOCAL_ONLY_PREFIXES = ("/snap", "/config", "/kiosk", "/admin")


def _load_trusted_nets():
    """ITSYOU_KIOSK_IPS 환경변수에서 신뢰 네트워크 목록을 파싱한다.
    각 토큰은 단일 IP(192.168.0.42) 또는 CIDR(192.168.0.0/24)."""
    nets = []
    for tok in os.environ.get("ITSYOU_KIOSK_IPS", "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        try:
            nets.append(ipaddress.ip_network(tok, strict=False))
        except ValueError:
            pass
    return nets


_TRUSTED_NETS = _load_trusted_nets()


def is_trusted(remote_addr: str) -> bool:
    """localhost 이거나 ITSYOU_KIOSK_IPS 에 포함된 출처면 True."""
    if remote_addr in _LOCALHOST:
        return True
    try:
        ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_NETS)


def is_local_only(path: str) -> bool:
    # 대소문자 무시(방어심층): /Snap 같은 변형도 민감 경로로 취급
    return path.lower().startswith(_LOCAL_ONLY_PREFIXES)


def is_allowed(path: str, remote_addr: str) -> bool:
    if is_local_only(path):
        return is_trusted(remote_addr)
    return True


class RateLimiter:
    """IP별 + 전역 분당 슬라이딩 윈도."""
    def __init__(self, per_ip_per_min=5, global_per_min=30):
        self.per_ip = per_ip_per_min
        self.glob = global_per_min
        self._ip = {}
        self._all = deque()
        self._lock = threading.Lock()

    def _trim(self, dq, now):
        while dq and now - dq[0] > 60:
            dq.popleft()

    def allow(self, ip: str) -> bool:
        # check-then-append를 원자적으로 보호(동시 요청 TOCTOU 방지)
        with self._lock:
            now = time.time()
            self._trim(self._all, now)
            if len(self._all) >= self.glob:
                return False
            dq = self._ip.setdefault(ip, deque())
            self._trim(dq, now)
            if len(dq) >= self.per_ip:
                return False
            dq.append(now)
            self._all.append(now)
            return True
