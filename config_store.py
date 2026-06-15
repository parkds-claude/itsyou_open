"""~/.itsyou/ 에 API 키·어드민 키·일일 사용량을 안전하게 저장/로드한다.
키는 어떤 응답·로그에도 노출하지 않는다(status()는 설정 여부만 반환)."""
import json
import os
import secrets
import threading
from pathlib import Path

_DIR = Path.home() / ".itsyou"
_CONFIG = _DIR / "config.json"
_USAGE = _DIR / "usage.json"
_USAGE_LOCK = threading.Lock()

_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


def _write_600(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # umask 레이스를 피하려 처음부터 0o600으로 생성
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)


def _read(path: Path) -> dict:
    # 파일이 없거나(최초 실행) 손상된 경우만 빈 dict로 시작한다.
    # PermissionError 등 다른 예외는 전파해야 set_key가 기존 키를 덮어쓰지 않는다.
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}


def _config() -> dict:
    return _read(_CONFIG)


def set_key(provider: str, key: str) -> None:
    if provider not in _ENV:
        raise ValueError("unknown provider")
    cfg = _config()
    cfg[f"{provider}_key"] = key.strip()
    cfg["provider"] = provider
    _write_600(_CONFIG, cfg)


def get_key(provider: str) -> str:
    env = os.environ.get(_ENV.get(provider, ""), "").strip()
    if env:
        return env
    return _config().get(f"{provider}_key", "").strip()


def get_provider():
    # 환경변수가 파일보다 우선(get_key와 일관)
    for prov in _ENV:
        if os.environ.get(_ENV[prov], "").strip():
            return prov
    cfg = _config()
    p = cfg.get("provider")
    if p and get_key(p):
        return p
    return None


def status() -> dict:
    p = get_provider()
    return {"configured": p is not None, "provider": p}


def get_or_create_admin_key() -> str:
    env = os.environ.get("ADMIN_KEY", "").strip()
    if env:
        return env
    cfg = _config()
    k = cfg.get("admin_key")
    if not k:
        k = secrets.token_hex(32)
        cfg["admin_key"] = k
        _write_600(_CONFIG, cfg)
    return k


def incr_usage(day: str) -> int:
    # 일일 상한은 과금 가드이므로 read-modify-write를 원자적으로 보호
    with _USAGE_LOCK:
        u = _read(_USAGE)
        u[day] = int(u.get(day, 0)) + 1
        # 과거 날짜 중 오래된 것부터 제거해 최근 7개만 유지(결정론적)
        old = sorted(d for d in u if d < day)
        while len(u) > 7 and old:
            u.pop(old.pop(0), None)
        _write_600(_USAGE, u)
        return u[day]


def usage_count(day: str) -> int:
    return int(_read(_USAGE).get(day, 0))


def reserve_usage(day: str, cap: int) -> bool:
    """일일 상한 내에서 슬롯을 원자적으로 예약(check+increment를 한 락 안에서).
    cap 초과면 증가하지 않고 False. 과금 가드의 TOCTOU race를 막는다."""
    with _USAGE_LOCK:
        u = _read(_USAGE)
        cur = int(u.get(day, 0))
        if cur >= cap:
            return False
        u[day] = cur + 1
        old = sorted(d for d in u if d < day)
        while len(u) > 7 and old:
            u.pop(old.pop(0), None)
        _write_600(_USAGE, u)
        return True


def release_usage(day: str) -> None:
    """예약한 슬롯을 롤백(생성 실패 시)."""
    with _USAGE_LOCK:
        u = _read(_USAGE)
        if int(u.get(day, 0)) > 0:
            u[day] = int(u[day]) - 1
            _write_600(_USAGE, u)
