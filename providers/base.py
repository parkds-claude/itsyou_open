"""AI 이미지 변환 프로바이더 디스패처.
공통 인터페이스: 각 provider 모듈은 generate_image(image_bytes, prompt, key) -> bytes 를 제공한다."""
import config_store as cs

# provider 이름 -> 호출 가능한 generate_image(image_bytes, prompt, key)
_REGISTRY = {}


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
