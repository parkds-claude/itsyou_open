"""HTTPS 강제 — Cloudflare 뒤에서 방문자가 암호화 없는 주소(http)로 들어오면 https 로 넘긴다.

터널 뒤의 앱은 언제나 http 로 요청을 받으므로(터널이 암호화를 풀어 준다) 방문자의 접속 방식은
Cloudflare 가 붙여 주는 CF-Visitor 헤더로만 알 수 있다. 그래서 시험의 base_url 은 전부 http 다.
"""
from app import app as flask_app

CF_HTTP = {"CF-Visitor": '{"scheme":"http"}'}
CF_HTTPS = {"CF-Visitor": '{"scheme":"https"}'}
BASE = "http://booth.example"


def _client():
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_plain_http_visitor_is_redirected_to_https():
    r = _client().get("/?a=1", headers=CF_HTTP, base_url=BASE)
    assert r.status_code == 301
    assert r.headers["Location"] == "https://booth.example/?a=1"


def test_plain_http_static_file_is_redirected_too():
    r = _client().get("/static/style.css", headers=CF_HTTP, base_url=BASE)
    assert r.status_code == 301
    assert r.headers["Location"] == "https://booth.example/static/style.css"


def test_plain_http_post_keeps_its_method():
    # 308 은 POST 를 GET 으로 바꾸지 않는다 — 촬영 요청이 조용히 다른 요청으로 변하지 않게.
    r = _client().post("/snap", headers=CF_HTTP, base_url=BASE)
    assert r.status_code == 308
    assert r.headers["Location"] == "https://booth.example/snap"


def test_https_visitor_is_not_redirected():
    # 이미 https 인 방문자를 또 넘기면 끝없이 맴돈다(사이트 먹통).
    r = _client().get("/healthz", headers=CF_HTTPS, base_url=BASE)
    assert r.status_code == 200


def test_without_proxy_header_nothing_changes():
    # 내 컴퓨터·같은 와이파이에서 http 로 단독 운용하는 경우(헤더 없음)는 그대로 둔다.
    r = _client().get("/healthz", base_url="http://192.168.0.5:5080")
    assert r.status_code == 200


def test_broken_proxy_header_is_ignored():
    for bad in ("not-json", "[]", '{"scheme":5}', ""):
        r = _client().get("/healthz", headers={"CF-Visitor": bad}, base_url=BASE)
        assert r.status_code == 200, bad


def test_head_request_gets_301_like_get():
    r = _client().head("/", headers=CF_HTTP, base_url=BASE)
    assert r.status_code == 301


def test_port_in_address_is_kept():
    r = _client().get("/healthz", headers=CF_HTTP, base_url="http://booth.example:8080")
    assert r.headers["Location"] == "https://booth.example:8080/healthz"


def test_redirect_comes_before_access_policy():
    # http 로 온 바깥 방문자의 민감 경로는 403 이 아니라 먼저 https 로 넘어간다(정책은 https 에서 적용).
    r = _client().get("/config/status", headers=CF_HTTP, base_url=BASE,
                      environ_overrides={"REMOTE_ADDR": "192.168.0.9"})
    assert r.status_code == 301


def test_only_the_scheme_changes_not_the_query():
    # 앱이 직접 https 를 여는 운용(ITSYOU_TLS_CERT) + Cloudflare: 주소 뒤쪽의 http:// 글자는 건드리지 않는다.
    r = _client().get("/?u=http://x.example/a", headers=CF_HTTP, base_url="https://booth.example")
    assert r.headers["Location"] == "https://booth.example/?u=http://x.example/a"


def test_absurd_proxy_header_is_ignored_not_crashed():
    # 깊게 겹친 JSON 은 파서를 넘어뜨린다 — 500 이 아니라 그냥 무시해야 한다.
    r = _client().get("/healthz", headers={"CF-Visitor": "[" * 5000}, base_url=BASE)
    assert r.status_code == 200
