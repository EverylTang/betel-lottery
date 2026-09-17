"""反向代理/本地 ngrok 下的真实客户端 IP 解析（app/core/http.py）。"""

from types import SimpleNamespace

import pytest

from app.core import http as client_ip_module
from app.core.http import client_ip_or_unknown, resolve_client_ip


def make_request(peer: str | None, headers: dict[str, str] | None = None):
    return SimpleNamespace(
        client=SimpleNamespace(host=peer) if peer else None,
        headers=headers or {},
    )


@pytest.fixture(autouse=True)
def trust_proxy_headers_on(monkeypatch):
    monkeypatch.setattr(client_ip_module.settings, "trust_proxy_headers", True)


@pytest.mark.parametrize(
    "proxy",
    ["127.0.0.1", "::1", "10.244.0.7", "172.17.0.3", "192.168.65.1", "169.254.1.1", "fd00::2", "[::1]"],
)
def test_uses_forwarded_for_from_trusted_local_proxy(proxy):
    request = make_request(proxy, {"x-forwarded-for": "203.0.113.9, 10.0.0.1"})
    assert resolve_client_ip(request) == "203.0.113.9"


def test_ignores_forwarded_for_from_direct_public_client():
    # 8000 被直接暴露到公网时，访客伪造 XFF 不能绕过按 IP 的限流。
    request = make_request("8.8.8.8", {"x-forwarded-for": "203.0.113.9"})
    assert resolve_client_ip(request) == "8.8.8.8"


def test_falls_back_to_real_ip_header(monkeypatch):
    request = make_request("127.0.0.1", {"x-real-ip": "2001:db8::1"})
    assert resolve_client_ip(request) == "2001:db8::1"


def test_socket_peer_when_no_proxy_headers():
    assert resolve_client_ip(make_request("127.0.0.1")) == "127.0.0.1"


def test_disabled_by_setting():
    request = make_request("127.0.0.1", {"x-forwarded-for": "203.0.113.9"})
    client_ip_module.settings.trust_proxy_headers = False
    assert resolve_client_ip(request) == "127.0.0.1"


def test_truncated_to_safe_length():
    request = make_request("127.0.0.1", {"x-forwarded-for": "9" * 200})
    assert resolve_client_ip(request) == request.client.host


def test_missing_request_shapes():
    assert resolve_client_ip(None) is None
    assert resolve_client_ip(make_request(None)) is None
    assert client_ip_or_unknown(None) == "unknown"
    assert client_ip_or_unknown(make_request(None)) == "unknown"
