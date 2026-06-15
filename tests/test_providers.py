import providers.base as base


def test_dispatch_unknown_provider_raises():
    try:
        base.generate("nope", b"x", "p")
        assert False
    except ValueError:
        pass


def test_dispatch_routes_to_registered(monkeypatch):
    called = {}

    def fake(image_bytes, prompt, key):
        called["ok"] = (image_bytes, prompt, key)
        return b"IMG"

    monkeypatch.setitem(base._REGISTRY, "gemini", fake)
    out = base.generate("gemini", b"sel", "make art", key="K")
    assert out == b"IMG"
    assert called["ok"] == (b"sel", "make art", "K")
