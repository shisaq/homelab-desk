"""每日体检：输出纯文本（可直接转发到通知），退出码 0 正常 / 1 有警告 / 2 有严重问题。"""

from __future__ import annotations

import calendar
import ipaddress
import json
import shutil
import socket
import ssl
import subprocess
import time
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import net, plugins
from .config import Config

ICON = {"ok": "✅", "warn": "⚠️ ", "crit": "🔴"}
CERT_WARN_DAYS = 20  # Caddy 到期前 30 天开始续期；剩不到 20 天说明续期一直失败


class Report:
    def __init__(self, title: str):
        self.lines = [f"# {title} · {time.strftime('%Y-%m-%d %H:%M')}"]
        self.worst = "ok"

    def section(self, name: str):
        self.lines += ["", f"## {name}"]

    def add(self, level: str, text: str):
        self.lines.append(f"{ICON[level]} {text}")
        if ("ok", "warn", "crit").index(level) > ("ok", "warn", "crit").index(self.worst):
            self.worst = level

    @property
    def code(self) -> int:
        return {"ok": 0, "warn": 1, "crit": 2}[self.worst]

    def text(self) -> str:
        return "\n".join(self.lines)


def _tailscale(r: Report) -> str:
    exe = shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"
    try:
        out = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=10).stdout
        st = json.loads(out)
    except (OSError, ValueError, subprocess.SubprocessError):
        r.add("crit", "读不到 Tailscale 状态（没装，或没登录）——不在家时将无法访问")
        return ""
    if st.get("BackendState") != "Running":
        r.add("crit", f"Tailscale 未连接（{st.get('BackendState')}）")
        return ""
    self = st.get("Self") or {}
    ips = self.get("TailscaleIPs") or []
    ip4 = next((i for i in ips if ":" not in i), "")
    r.add("ok", f"Tailscale 在线：{self.get('HostName', '')} {ip4}")
    exp = self.get("KeyExpiry")
    if exp:
        days = (calendar.timegm(time.strptime(exp[:19], "%Y-%m-%dT%H:%M:%S")) - time.time()) / 86400
        level = "crit" if days <= 30 else "warn"
        r.add(level, f"Tailscale 节点密钥 {int(days)} 天后过期，到期即失联——在管理后台对本机点 Disable key expiry")
    return ip4


def _dns(r: Report, cfg: Config, ts_ip: str):
    """本机解析结果。代理软件的 fake-ip（198.18.0.0/15）会让浏览器的访问走代理而失败（502）。"""
    for host in (cfg.domain, f"check.{cfg.domain}"):
        try:
            ips = {ai[4][0] for ai in socket.getaddrinfo(host, 443, socket.AF_INET)}
        except socket.gaierror:
            r.add("crit", f"{host} 解析不到——检查 Cloudflare 记录；刚改过 nameserver 的话最长要等 48 小时")
            continue
        if any(ipaddress.ip_address(i) in ipaddress.ip_network("198.18.0.0/15") for i in ips):
            r.add("warn", f"{host} 被代理软件接管（解析成 {', '.join(ips)}）——在代理软件的绕过/直连规则里加 *.{cfg.domain} 和 {cfg.domain}")
        elif ts_ip and ts_ip not in ips:
            r.add("warn", f"{host} 解析到 {', '.join(sorted(ips))}，不是本机的 Tailscale 地址 {ts_ip}")
        else:
            r.add("ok", f"{host} → {', '.join(sorted(ips))}")


def run(cfg: Config) -> Report:
    r = Report(f"{cfg.title} 体检")

    r.section("网络")
    ts_ip = _tailscale(r)
    _dns(r, cfg, ts_ip)

    r.section("工作台")
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"http://127.0.0.1:{cfg.agent_port}/api/health", timeout=5
        ) as resp:
            ok = resp.status == 200
    except OSError:
        ok = False
    r.add("ok" if ok else "crit", "指标服务在线" if ok else "指标服务（desk-agent）无响应——homelab-desk status 查看")
    for host in (cfg.domain, f"homelab-desk-check.{cfg.domain}"):
        try:
            code, days = net.https_get(cfg, host)
        except ssl.SSLCertVerificationError as e:
            r.add("crit", f"{host} 证书无效：{e.verify_message}——看 Caddy 日志，多半是 Cloudflare Token 失效")
            continue
        except OSError as e:
            r.add("crit", f"{host} 连不上本机 Caddy（{e}）——homelab-desk status 查看")
            continue
        which = "工作台证书" if host == cfg.domain else "服务通配符证书"
        if days < CERT_WARN_DAYS:
            r.add("warn", f"{which}只剩 {days} 天，自动续期可能一直失败——看 Caddy 日志，检查 Cloudflare Token")
        else:
            r.add("ok", f"{which}还有 {days} 天（自动续期）")

    r.section("服务")
    # 先经 Caddy 测（用户实际走的路）；不通再直连后端，判断问题出在后端还是转发
    with ThreadPoolExecutor(max_workers=8) as pool:
        via = list(pool.map(lambda s: net.probe_via_caddy(cfg, s), cfg.services))
    for svc, v in zip(cfg.services, via):
        if v["state"] == "up":
            if v["code"] == 400:
                r.add("warn", f"{svc.title}：经域名访问返回 400，服务可能不认反向代理（见服务目录里的说明）")
            else:
                r.add("ok", f"{svc.title}：{v['ms']} ms")
            continue
        d = net.probe_direct(svc)
        if v["state"] == "error":
            r.add("warn", f"{svc.title}：返回 HTTP {v['code']}")
        elif d["state"] == "up":
            r.add("warn", f"{svc.title}：后端正常，但经域名访问失败（{v['error']}）")
        else:
            r.add("crit", f"{svc.title}：{v['error']}（{svc.url}）")
    if not cfg.services:
        r.add("ok", "还没有服务")

    r.section("本机")
    du = shutil.disk_usage("/System/Volumes/Data" if Path("/System/Volumes/Data").is_dir() else "/")
    free = du.free / du.total * 100
    level = "crit" if free < 5 else "warn" if free < 10 else "ok"
    r.add(level, f"磁盘可用 {du.free / 2**30:.0f} GB（{free:.0f}%）")
    for p in plugins.load(cfg, quiet=True):
        if not p.has("check"):
            continue
        try:
            for level, text in p.call("check"):
                r.add(level if level in ICON else "warn", text)
        except Exception as e:
            p.ctx.log(traceback.format_exc())
            r.add("warn", f"插件 {p.name} 体检出错：{e}")
    return r


def notify(cfg: Config, report: Report) -> list:
    """把报告交给开了 notify() 的插件。返回出错信息列表。"""
    errors = []
    for p in plugins.load(cfg, quiet=True):
        if p.has("notify"):
            try:
                p.call("notify", report.text(), report.worst)
            except Exception as e:
                errors.append(f"{p.name}: {e}")
    return errors
