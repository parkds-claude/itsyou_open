"""엔드포인트별 접근 정책 + rate limit.
민감(촬영/설정/어드민)은 localhost 전용, 조회는 LAN 허용. X-Forwarded-For는 신뢰하지 않는다."""
import threading
import time
from collections import deque

_LOCALHOST = {"127.0.0.1", "::1", "localhost"}

# 민감 경로 접두사 (localhost 전용)
_LOCAL_ONLY_PREFIXES = ("/snap", "/config", "/admin")


def is_local_only(path: str) -> bool:
    # 대소문자 무시(방어심층): /Snap 같은 변형도 민감 경로로 취급
    return path.lower().startswith(_LOCAL_ONLY_PREFIXES)


def is_allowed(path: str, remote_addr: str) -> bool:
    if is_local_only(path):
        return remote_addr in _LOCALHOST
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
