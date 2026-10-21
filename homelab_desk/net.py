"""在线检测与经本机 Caddy 的 HTTPS 请求。

macOS 的「本地网络」隐私限制：LaunchAgent 启动的命令行程序（这里是 python3）默认连不上局域网设备
（报 No route to host），而 Caddy 授权过就可以。所以后台的在线检测一律经本机 Caddy 走一遍：
连 127.0.0.1，SNI 用服务的真实域名。顺带也验证了证书和转发。
"""

from __future__ import annotations

import http.client
import socket
import ssl
import time
import urllib.error
import urllib.request

from . import __version__
from .config import Config, Service

UA = f"homelab-desk/{__version__}"
# Caddy 连不上后端时回 502，后端超时回 504
CADDY_DOWN = (502, 504)


def https_get(cfg: Config, host: str, path: str = "/", timeout: float = 8) -> tuple:
    """返回 (状态码, 证书剩余天数)。证书无效抛 ssl.SSLCertVerificationError，连不上抛 OSError。"""
    ctx = ssl.create_default_context()
    raw = socket.create_connection(("127.0.0.1", cfg.https_port), timeout=timeout)
    sock = ctx.wrap_socket(raw, server_hostname=host)
    try:
        days = (ssl.cert_time_to_seconds(sock.getpeercert()["notAfter"]) - time.time()) / 86400
        conn = http.client.HTTPSConnection(host, cfg.https_port, timeout=timeout, context=ctx)
        conn.sock = sock
        host_hdr = host if cfg.https_port == 443 else f"{host}:{cfg.https_port}"
        conn.request("GET", path, headers={"Host": host_hdr, "User-Agent": UA})
        code = conn.getresponse().status
    finally:
        sock.close()
    return code, int(days)


def probe_via_caddy(cfg: Config, svc: Service, timeout: float = 8) -> dict:
    """任何 HTTP 回应都算在线（401/404 说明服务活着）；Caddy 回 502/504 = 后端连不上；其他 5xx 算异常。"""
    t = time.monotonic()
    try:
        code, _ = https_get(cfg, cfg.host(svc), svc.health, timeout)
    except ssl.SSLCertVerificationError as e:
        return {"state": "down", "code": 0, "ms": None, "error": f"证书无效：{e.verify_message}"}
    except (OSError, http.client.HTTPException) as e:
        return {"state": "down", "code": 0, "ms": None, "error": f"本机 Caddy 无响应：{e}"[:160]}
    ms = round((time.monotonic() - t) * 1000)
    if code in CADDY_DOWN:
        return {"state": "down", "code": code, "ms": None, "error": "后端连不上" if code == 502 else "后端超时"}
    return {"state": "error" if code >= 500 else "up", "code": code, "ms": ms, "error": ""}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **kw):
        return None  # 跳转也算在线，不跟


# 不走系统代理（Clash 等会截走本机请求）
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
_opener_insecure = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _NoRedirect(),
    urllib.request.HTTPSHandler(context=ssl._create_unverified_context()),
)


def probe_direct(svc: Service, timeout: float = 6) -> dict:
    """直接连后端（不经 Caddy），体检时用来判断问题出在后端还是转发。"""
    req = urllib.request.Request(svc.url + svc.health, headers={"User-Agent": UA})
    t = time.monotonic()
    try:
        with (_opener_insecure if svc.insecure_tls else _opener).open(req, timeout=timeout) as r:
            code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return {"state": "down", "code": 0, "ms": None, "error": str(getattr(e, "reason", e))[:120]}
    ms = round((time.monotonic() - t) * 1000)
    return {"state": "error" if code >= 500 else "up", "code": code, "ms": ms, "error": ""}
