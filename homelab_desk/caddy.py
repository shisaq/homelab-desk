"""由配置生成 Caddyfile。"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import urllib.request
from pathlib import Path

from . import paths
from .config import Config, Service

HEADER = """\
# 由 homelab-desk 生成，请勿手改——改 {source} 后运行 homelab-desk deploy。
{{
	admin {admin}
	email {email}
	https_port {https_port}
	http_port {http_port}
	storage file_system {storage}
}}

# 证书：Cloudflare DNS 验证（地址只在 Tailscale 内，HTTP 验证不可能）
(cf_tls) {{
	tls {{
		dns cloudflare {{env.CF_API_TOKEN}}
		resolvers 1.1.1.1
	}}
}}

# 只放行 Tailscale 与本机。macOS 普通用户只能在全部网卡上监听 80/443，
# 局域网设备也连得上；而转发后后端看到的来源是 127.0.0.1，不拦就会绕过后端自己的来源限制。
(tailnet_only) {{
	@outside not remote_ip {allow}
	handle @outside {{
		abort
	}}
}}
"""


def _proxy(svc: Service, indent: str) -> str:
    inner = []
    if svc.rewrite_host:
        inner.append("header_up Host {upstream_hostport}")
    transport = []
    if svc.read_timeout:
        transport.append(f"read_timeout {svc.read_timeout}")
    if svc.insecure_tls:
        transport.append("tls_insecure_skip_verify")
    if transport:
        inner.append("transport http {")
        inner += [f"\t{t}" for t in transport]
        inner.append("}")
    if not inner:
        return f"{indent}reverse_proxy {svc.upstream}\n"
    body = "".join(f"{indent}\t{line}\n" for line in inner)
    return f"{indent}reverse_proxy {svc.upstream} {{\n{body}{indent}}}\n"


def render(cfg: Config) -> str:
    site = paths.data_dir() / "site"
    out = [
        HEADER.format(
            source=cfg.source,
            admin=cfg.admin,
            email=cfg.acme_email,
            https_port=cfg.https_port,
            http_port=cfg.http_port,
            storage=paths.data_dir() / "caddy",
            allow=" ".join(cfg.allow),
        )
    ]
    # 工作台用裸名：泛解析证书不覆盖 desk.example.com 本身，单独一个站点、单独一张证书
    out.append(
        f"""
# 工作台
{cfg.domain} {{
	import cf_tls
	import tailnet_only
	# 用命名匹配：带路径的 handle 会被 Caddy 按路径排序，可能排到来源过滤之前
	@api path /api/*
	handle @api {{
		reverse_proxy 127.0.0.1:{cfg.agent_port}
	}}
	handle {{
		header Cache-Control no-cache
		root * {site}
		file_server
	}}
}}
"""
    )
    blocks = []
    for svc in cfg.services:
        blocks.append(
            f"\t@{svc.name} host {cfg.host(svc)}\n\thandle @{svc.name} {{\n{_proxy(svc, chr(9) * 2)}\t}}\n"
        )
    out.append(
        f"""
# 各服务：<名字>.{cfg.domain}
*.{cfg.domain} {{
	import cf_tls
	import tailnet_only

{chr(10).join(blocks)}
	handle {{
		respond "unknown service" 404
	}}
}}
"""
    )
    return "".join(out)


# ── Caddy 程序 ──
def caddyfile() -> Path:
    return paths.data_dir() / "Caddyfile"


def _has_cloudflare(binary: str) -> bool:
    try:
        r = subprocess.run([binary, "list-modules"], capture_output=True, text=True, timeout=20)
    except OSError:
        return False
    return "dns.providers.cloudflare" in r.stdout


def find_binary(cfg: Config) -> str | None:
    """找一个带 cloudflare 插件的 Caddy。官方 Homebrew 版不带插件，不能用。"""
    cands = [cfg.caddy_bin] if cfg.caddy_bin else []
    cands += [
        str(paths.data_dir() / "bin" / "caddy"),
        shutil.which("caddy") or "",
        str(Path.home() / ".local" / "bin" / "caddy"),
    ]
    for c in cands:
        if c and os.access(c, os.X_OK) and _has_cloudflare(c):
            return c
    return None


def download() -> str:
    """从 Caddy 官网下载带 cloudflare 插件的构建（官网按参数现编译，可能要一两分钟）。"""
    goos = "darwin" if sys.platform == "darwin" else "linux"
    arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64", "amd64": "amd64"}.get(platform.machine().lower())
    if not arch:
        raise RuntimeError(f"不认识的 CPU 架构 {platform.machine()}，请手动下载 Caddy")
    url = f"https://caddyserver.com/api/download?os={goos}&arch={arch}&p=github.com%2Fcaddy-dns%2Fcloudflare"
    dest = paths.data_dir() / "bin" / "caddy"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".download")
    with urllib.request.urlopen(url, timeout=300) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.chmod(0o755)
    if not _has_cloudflare(str(tmp)):
        tmp.unlink()
        raise RuntimeError("下载的 Caddy 不含 cloudflare 插件，请稍后重试")
    tmp.replace(dest)
    return str(dest)


def validate(binary: str, path: Path, token: str) -> str:
    """返回空字符串表示通过，否则返回错误信息。"""
    env = {**os.environ, "CF_API_TOKEN": token}
    r = subprocess.run(
        [binary, "validate", "--config", str(path), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )
    if r.returncode == 0:
        return ""
    lines = [l for l in (r.stderr + r.stdout).splitlines() if "error" in l.lower()]
    return "\n".join(lines[-3:]) or r.stderr[-500:]


def reload(binary: str, cfg: Config) -> bool:
    """通过 admin 接口热加载，不断开现有连接。失败（如 Caddy 没在跑）返回 False。"""
    r = subprocess.run(
        [binary, "reload", "--config", str(caddyfile()), "--adapter", "caddyfile", "--address", cfg.admin],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return r.returncode == 0


def read_token(cfg: Config) -> str:
    if not cfg.token_file.exists():
        raise FileNotFoundError(f"找不到 Cloudflare Token：{cfg.token_file}")
    return cfg.token_file.read_text().strip()


def run(cfg: Config) -> None:
    """后台服务入口：带上 Token 前台运行 Caddy（替换当前进程）。"""
    binary = find_binary(cfg)
    if not binary:
        print("找不到带 cloudflare 插件的 Caddy，请先运行 homelab-desk deploy", file=sys.stderr)
        sys.exit(1)
    os.environ["CF_API_TOKEN"] = read_token(cfg)
    os.execv(binary, [binary, "run", "--config", str(caddyfile()), "--adapter", "caddyfile"])
