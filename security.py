"""엔드포인트별 접근 정책 + rate limit.
민감(촬영/설정/키오스크/어드민)은 기본 localhost 전용, 조회는 LAN 허용.
프록시 헤더(X-Forwarded-For 등)의 '값'은 믿지 않는다. 다만 그런 헤더가 '붙어 있다'는 것은 요청이
터널·리버스 프록시를 거쳐 왔다는 뜻이므로, 그 요청은 주소가 127.0.0.1 로 보여도 localhost 로 치지 않는다.
(cloudflared·ngrok 같은 터널은 서버와 같은 기기에서 돌아서 바깥 방문자가 전부 127.0.0.1 로 보인다.)

폰을 키오스크로 쓰는 경우(서버는 맥미니, 촬영 기기는 폰):
환경변수 ITSYOU_KIOSK_IPS 에 신뢰할 기기 IP/CIDR 를 콤마로 지정하면
해당 출처에 한해 민감 경로 접근을 허용한다. 기본값은 빈 값 = localhost 전용(안전 기본).
예) ITSYOU_KIOSK_IPS="192.168.0.42"  또는  "192.168.0.0/24"
"""
import hashlib
import hmac
import ipaddress
import json
import os
import threading
import time
from collections import deque

from itsdangerous import BadData, URLSafeTimedSerializer

_LOCALHOST = {"127.0.0.1", "::1", "localhost"}

# localhost 전용(가장 민감): API 키 설정·어드민. 신뢰 IP 라도 절대 허용하지 않는다.
#  → 키오스크 폰(촬영 기기)에 API 키 교체/어드민 권한을 주지 않기 위함.
_LOCALHOST_ONLY_PREFIXES = ("/config", "/admin")
# 신뢰 출처 허용: 촬영·키오스크 프롬프트 편집. localhost 또는 ITSYOU_KIOSK_IPS.
_TRUSTED_PREFIXES = ("/snap", "/kiosk")
# 터널·리버스 프록시가 붙이는 헤더. 하나라도 있으면 중계된 요청이다(바깥 방문자는 이 헤더를 뗄 수 없다).
_PROXY_HEADERS = ("CF-Connecting-IP", "CF-Ray", "CF-Visitor", "X-Forwarded-For", "X-Forwarded-Proto",
                  "X-Real-IP", "Forwarded")
# 공용 비밀번호가 걸려 있어도 비밀번호 없이 열어 두는 것: 손님 폰이 QR 로 받는 사진, 상태 확인, 비밀번호 확인 자체.
_OPEN_WITHOUT_PIN = ("/healthz", "/sw.js", "/pin", "/favicon.ico")


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


# 모듈 임포트 시 1회만 로드된다. ITSYOU_KIOSK_IPS 를 바꾸면 앱을 재기동해야 반영된다.
_TRUSTED_NETS = _load_trusted_nets()


def came_through_proxy(headers) -> bool:
    """터널·리버스 프록시를 거쳐 온 요청인가."""
    return any(h in headers for h in _PROXY_HEADERS)


def host_is_this_computer(host) -> bool:
    """요청이 localhost 라는 이름으로 왔는가(Host 헤더). 터널·프록시를 거치면 여기에 공개 도메인이 들어 있다.
    프록시 헤더가 떼어진 채 넘어온 요청과 DNS rebinding 을 한 번 더 거른다."""
    host = (host or "").strip().lower()
    if host.startswith("["):                       # [::1]:5080
        name = host[1:host.find("]")] if "]" in host else host
    elif host.count(":") == 1:                     # localhost:5080
        name = host.split(":")[0]
    else:
        name = host
    return name in ("localhost", "127.0.0.1", "::1")


def is_local(remote_addr: str, proxied: bool = False, local_host: bool = True) -> bool:
    """정말 이 컴퓨터에서 직접 온 요청인가 — 중계됐거나 바깥 이름으로 들어온 요청은 주소가 127.0.0.1 이어도 아니다.
    local_host: 접속한 주소 이름이 localhost 인가(host_is_this_computer)."""
    return remote_addr in _LOCALHOST and not proxied and local_host


def is_trusted(remote_addr: str, proxied: bool = False, local_host: bool = True) -> bool:
    """localhost 이거나 ITSYOU_KIOSK_IPS 에 포함된 출처면 True. 중계된 요청은 주소로 판단할 수 없으므로 False."""
    if proxied:
        return False
    if remote_addr in _LOCALHOST:
        return local_host
    try:
        ip = ipaddress.ip_address(remote_addr)
    except ValueError:
        return False
    return any(ip in net for net in _TRUSTED_NETS)


def is_local_only(path: str) -> bool:
    # 대소문자 무시(방어심층): /Snap 같은 변형도 민감 경로로 취급
    lp = path.lower()
    return lp.startswith(_LOCALHOST_ONLY_PREFIXES) or lp.startswith(_TRUSTED_PREFIXES)


def is_allowed(path: str, remote_addr: str, proxied: bool = False, kiosk_session: bool = False,
               local_host: bool = True) -> bool:
    """kiosk_session: 공용 비밀번호를 통과한 기기(신뢰 IP 와 같은 권한 — 촬영·프롬프트 편집까지)."""
    # 대소문자 무시로 우회 차단. localhost 전용이 신뢰IP 허용보다 우선.
    lp = path.lower()
    trusted = kiosk_session or is_trusted(remote_addr, proxied, local_host)
    if lp == "/config/status":
        # 화면이 처음 뜰 때 읽는 '설정됐는가' 한 줄(키는 들어 있지 않다). 촬영 기기가 못 읽으면 키 입력 화면이 뜬다.
        return trusted
    if lp.startswith(_LOCALHOST_ONLY_PREFIXES):
        return is_local(remote_addr, proxied, local_host)
    if lp.startswith(_TRUSTED_PREFIXES):
        return trusted
    return True


def is_open_without_pin(path: str) -> bool:
    lp = path.lower()
    return lp.startswith("/result/") or lp in _OPEN_WITHOUT_PIN


def visitor_used_plain_http(cf_visitor: str) -> bool:
    """Cloudflare 가 붙이는 CF-Visitor 헤더(예: {"scheme":"http"})로 방문자가 암호화 없이 들어왔는지 본다.
    헤더가 없거나 깨져 있으면 False — 내 컴퓨터·같은 와이파이 단독 운용(http)은 건드리지 않는다.
    접근 권한 판단에는 쓰지 않는다(위 정책은 여전히 프록시 헤더를 믿지 않는다)."""
    return _cf_scheme(cf_visitor) == "http"


def _cf_scheme(cf_visitor: str) -> str:
    if len(cf_visitor) > 64:        # 정상 값은 20자 안팎. 길고 깊게 겹친 JSON 은 파서를 넘어뜨린다(RecursionError)
        return ""
    try:
        scheme = json.loads(cf_visitor or "").get("scheme")
    except (ValueError, AttributeError):
        return ""
    return scheme if isinstance(scheme, str) else ""


def visitor_on_https(headers) -> bool:
    """프록시가 알려 준 방문자의 접속 방식이 https 인가 — 쿠키에 Secure 를 붙일지 정할 때만 쓴다."""
    forwarded = headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    return _cf_scheme(headers.get("CF-Visitor", "")) == "https" or forwarded == "https"


# ── 공용 비밀번호(원격 기기용) ───────────────────────────────────────────────
# 비밀번호를 통과한 기기에는 서명한 표를 쿠키로 준다. 표에는 비밀번호가 아니라 '어느 비밀번호로 통과했는지'의
# 지문만 들어 있어서, 비밀번호를 바꾸면 기존 표는 전부 무효가 된다.

def _pin_mark(secret: str, pin: str) -> str:
    return hmac.new(secret.encode(), pin.encode(), hashlib.sha256).hexdigest()[:16]


def _signer(secret: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(secret, salt="itsyou-kiosk")


def make_kiosk_token(secret: str, pin: str) -> str:
    return _signer(secret).dumps(_pin_mark(secret, pin))


def kiosk_token_ok(token: str, secret: str, pin: str, max_age: int) -> bool:
    if not token or not pin or len(token) > 256:
        return False
    try:
        mark = _signer(secret).loads(token, max_age=max_age)
    except BadData:
        return False
    return isinstance(mark, str) and mark.isascii() and hmac.compare_digest(mark, _pin_mark(secret, pin))


class PinGuard:
    """공용 비밀번호 대입 방지. 틀린 시도를 '전체 합산'으로 센다 — 주소(IP)를 바꿔 가며 시도해도 한도는 같다.
    시도는 확인하기 전에 먼저 세고(begin) 맞았을 때만 돌려준다(succeeded) — 동시에 여러 번 보내 한도를 넘지 못하게.
    기본값(15분에 5번, 하루 20번)이면 4자리 비밀번호를 다 넣어 보는 데 500일이 걸린다.
    비용: 누군가 일부러 틀려 잠가 두면 그동안 새 기기가 못 들어온다(이미 들어온 기기는 그대로 쓴다)."""
    def __init__(self, burst=5, burst_window=15 * 60, daily=20):
        self.burst = burst
        self.burst_window = burst_window
        self.daily = daily
        self._tries = deque()
        self._lock = threading.Lock()

    def begin(self) -> bool:
        with self._lock:
            now = time.time()
            while self._tries and now - self._tries[0] > 86400:
                self._tries.popleft()
            recent = sum(1 for t in self._tries if now - t <= self.burst_window)
            if len(self._tries) >= self.daily or recent >= self.burst:
                return False
            self._tries.append(now)
            return True

    def succeeded(self) -> None:
        with self._lock:
            if self._tries:
                self._tries.pop()


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
