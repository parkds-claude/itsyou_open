"""AI 이미지 변환 프로바이더 디스패처.
공통 인터페이스: 각 provider 모듈은 generate_image(image_bytes, prompt, key) -> bytes 를 제공한다."""
import json
import time
import urllib.error
import urllib.request

import config_store as cs

# provider 이름 -> 호출 가능한 generate_image(image_bytes, prompt, key)
_REGISTRY = {}

# 일시적 오류(과부하·레이트리밋·빈 응답) 공통 재시도 정책 — 모든 provider 가 공유한다.
_RETRY_HTTP = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3


def call_with_retry(build_request, extract_image):
    """HTTP 이미지 생성 호출을 일시적 오류에 대해 backoff 재시도한다.

    - build_request() -> urllib.request.Request : 매 시도마다 새 요청 생성
    - extract_image(data: dict) -> bytes | None : 응답에서 이미지 추출(없으면 None → 재시도)
    빈 candidates/data 같은 구조는 None 으로 처리되어야 하며(IndexError 금지),
    영구 오류(429 외 4xx)는 즉시 중단한다. provider 별 재시도 로직 중복을 막는다."""
    last_err = None
    for attempt in range(_MAX_ATTEMPTS):
        try:
            with urllib.request.urlopen(build_request(), timeout=120) as r:
                data = json.loads(r.read())
            img = extract_image(data)
            if img is not None:
                return img
            last_err = RuntimeError("AI 응답에 이미지가 없습니다(빈 결과/안전필터)")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "ignore")[:200]
            except Exception:
                pass
            last_err = RuntimeError(f"AI HTTP {e.code}: {detail}")
            if e.code not in _RETRY_HTTP:   # 키 오류 등 영구 오류는 재시도 무의미
                raise last_err
        except urllib.error.URLError as e:
            last_err = RuntimeError(f"AI 연결 실패: {e.reason}")
        if attempt < _MAX_ATTEMPTS - 1:
            time.sleep(1.5 * (attempt + 1))   # 1.5s, 3s backoff
    raise last_err if last_err else RuntimeError("AI 이미지 생성 실패")


def register(name, fn):
    _REGISTRY[name] = fn


def generate(provider: str, image_bytes: bytes, prompt: str, key: str = None) -> bytes:
    fn = _REGISTRY.get(provider)
    if fn is None:
        raise ValueError(f"unknown provider: {provider}")
    if key is None:
        key = cs.get_key(provider)
    if not key:
        raise RuntimeError(f"{provider} key not set")
    return fn(image_bytes, prompt, key)
