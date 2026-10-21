"""Cloudflare：校验 Token、建 DNS 记录（只会新建或更新 domain 与 *.domain 两条 A 记录，一律灰云）。

Token 权限：模板「Edit zone DNS」，再加一项 Zone · Zone · Read，资源只选这一个域名。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

API = "https://api.cloudflare.com/client/v4"


class CFError(Exception):
    pass


def _call(token: str, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            data = json.load(e)
        except ValueError:
            raise CFError(f"Cloudflare 返回 HTTP {e.code}") from None
    except OSError as e:
        raise CFError(f"连不上 Cloudflare：{e}") from None
    if not data.get("success"):
        msg = "; ".join(f"{x.get('code')} {x.get('message')}" for x in data.get("errors", []))
        raise CFError(msg or "未知错误")
    return data


def verify(token: str) -> None:
    st = _call(token, "GET", "/user/tokens/verify")["result"].get("status")
    if st != "active":
        raise CFError(f"Token 状态是 {st}")


def find_zone(token: str, domain: str) -> tuple:
    """从完整域名往上找属于哪个 zone（如 desk.home.example.com → example.com）。"""
    parts = domain.split(".")
    for i in range(len(parts) - 1):
        name = ".".join(parts[i:])
        res = _call(token, "GET", f"/zones?name={name}")["result"]
        if res:
            return res[0]["id"], name
    raise CFError(f"Token 看不到 {domain} 所在的域名：确认域名在这个 Cloudflare 账号里，且 Token 有 Zone · Zone · Read")


def records(token: str, zone: str, name: str) -> list:
    return _call(token, "GET", f"/zones/{zone}/dns_records?name={name}")["result"]


def ensure_a(token: str, zone: str, name: str, ip: str) -> str:
    """确保 name 是一条指向 ip 的灰云 A 记录。返回做了什么。有同名的其他类型记录时不动，报错让用户处理。"""
    existing = records(token, zone, name)
    others = [r for r in existing if r["type"] not in ("A",)]
    if others:
        raise CFError(f"{name} 已有 {others[0]['type']} 记录，请先在 Cloudflare 后台处理")
    body = {"type": "A", "name": name, "content": ip, "ttl": 1, "proxied": False, "comment": "homelab-desk"}
    if not existing:
        _call(token, "POST", f"/zones/{zone}/dns_records", body)
        return "已新建"
    rec = existing[0]
    if rec["content"] == ip and not rec["proxied"]:
        return "已存在"
    _call(token, "PATCH", f"/zones/{zone}/dns_records/{rec['id']}", {"content": ip, "proxied": False})
    return f"已更新（原来是 {rec['content']}{'，橙色云' if rec['proxied'] else ''}）"


def nameservers(token: str, zone: str) -> list:
    return _call(token, "GET", f"/zones/{zone}")["result"].get("name_servers", [])
