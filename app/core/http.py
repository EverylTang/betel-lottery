"""反向代理场景下的客户端 IP 解析。

本地 ngrok 联调与生产反向代理都会让 API 看到的直连对端固定为代理地址
（Vite dev server、nginx、Docker 网关）。如果直接用 `request.client.host`
做限流键，所有真实用户会塌缩成同一个 IP 桶：一个人就能把管理员登录、
dev-login 等接口打到 429，同时审计日志里的来源 IP 也全部失真。

这里只在直连对端本身是回环或内网地址（即可信的本地代理）时才接受
`X-Forwarded-For` / `X-Real-IP`，公网客户端伪造这些头不会生效；
把 `TRUST_PROXY_HEADERS=false` 可以彻底退回“只认 socket 对端”的行为。
"""

import ipaddress
from typing import Final

from fastapi import Request

from app.core.config import get_settings

settings = get_settings()

# 只把回环、链路本地与 RFC1918/RFC4193 内网当作可信代理跳。刻意不用
# ip_address.is_private：它还把 198.51.100.0/24 等文档保留段算作内网。
TRUSTED_PROXY_NETWORKS: Final = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def _is_trusted_proxy(peer: str) -> bool:
    try:
        address = ipaddress.ip_address(peer.strip().strip("[]"))
    except ValueError:
        return False
    return any(address in network for network in TRUSTED_PROXY_NETWORKS)


def resolve_client_ip(request: Request | None) -> str | None:
    """Return the best-effort client IP, or None when the request is absent."""
    if request is None:
        return None
    peer = request.client.host if request.client else ""
    if not peer:
        return None
    if settings.trust_proxy_headers and _is_trusted_proxy(peer):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            first_hop = forwarded.split(",")[0].strip()
            if first_hop:
                try:
                    return str(ipaddress.ip_address(first_hop.strip("[]")))
                except ValueError:
                    pass
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            try:
                return str(ipaddress.ip_address(real_ip.strip("[]")))
            except ValueError:
                pass
    return peer


def client_ip_or_unknown(request: Request | None) -> str:
    """Rate-limit key variant that never yields an empty bucket name."""
    return resolve_client_ip(request) or "unknown"
