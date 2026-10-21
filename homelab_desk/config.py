"""读取并校验 services.yaml，补上默认值。出错时给出能看懂的中文说明。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import paths
from ._vendor import yaml

THEMES = ("glass", "liquid", "paper", "terminal")
APPEARANCES = ("auto", "light", "dark")
# 图表画法：趋势（曲线）与读数（单个数值占总量的比例）。空 = 用主题自带的
TRENDS = ("area", "line", "bars", "blocks")
GAUGES = ("bar", "ring", "segments")
NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
DOMAIN_RE = re.compile(r"^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
DURATION_RE = re.compile(r"^\d+(ms|s|m|h)$")
# Tailscale 的 IPv4 / IPv6 网段，加上本机
TAILNET_CIDRS = ["100.64.0.0/10", "fd7a:115c:a1e0::/48", "127.0.0.1/8", "::1"]


class ConfigError(Exception):
    pass


@dataclass
class Service:
    name: str  # 子域名
    title: str
    url: str  # 后端地址，如 http://127.0.0.1:8096
    health: str = "/"  # 在线检测路径
    icon: str = ""
    group: str = ""
    desc: str = ""
    read_timeout: str = ""  # 长请求（如本地大模型）可调大
    insecure_tls: bool = False  # 后端是自签证书的 https（如群晖 5001）
    rewrite_host: bool = False  # 转发时把 Host 改成后端地址：只认 localhost 的服务（Syncthing、Ollama 等）需要
    color: str = ""  # 卡片主色，如 #00A4DC；不写按名字自动取色
    path: str = ""  # 点卡片打开的路径，如 /admin/chat

    @property
    def upstream(self) -> str:
        """Caddy reverse_proxy 的写法：http 后端只写 host:port。"""
        u = urlsplit(self.url)
        return u.netloc if u.scheme == "http" else f"{u.scheme}://{u.netloc}"


@dataclass
class PluginRef:
    name: str
    options: dict = field(default_factory=dict)


@dataclass
class Config:
    instance: str  # 同一台机器上跑多套（如测试）时区分后台服务名，默认空
    domain: str
    title: str
    subtitle: str
    theme: str
    appearance: str  # auto 跟随系统；页面上也能手动切换
    trend: str
    gauge: str
    acme_email: str
    token_file: Path
    caddy_bin: str  # 指定 Caddy 路径；空 = 自动找（必须带 cloudflare 插件）
    https_port: int
    http_port: int
    admin: str
    agent_port: int
    allow: list
    check_time: str
    plugins: list
    services: list
    shortcuts: list  # 工作台底部「在 App 里填写」的地址：[{title, value, icon}]
    source: Path

    def label(self, part: str) -> str:
        """后台服务名：io.github.shisaq.homelab-desk[.实例].caddy"""
        base = "io.github.shisaq.homelab-desk"
        return f"{base}.{self.instance}.{part}" if self.instance else f"{base}.{part}"

    def host(self, svc: Service) -> str:
        return f"{svc.name}.{self.domain}"

    def url(self, host: str) -> str:
        port = "" if self.https_port == 443 else f":{self.https_port}"
        return f"https://{host}{port}"


def _need(d: dict, key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise ConfigError(f"{where} 缺少 `{key}`")
    return d[key]


def _service(raw: Any, i: int) -> Service:
    where = f"services 第 {i + 1} 项"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 应该是「名字: 值」的形式")
    name = str(_need(raw, "name", where)).lower()
    where = f"服务 `{name}`"
    if not NAME_RE.match(name):
        raise ConfigError(f"{where}：名字只能用小写字母、数字和连字符（它就是子域名）")
    url = str(_need(raw, "url", where))
    if "://" not in url:
        url = "http://" + url
    u = urlsplit(url)
    if u.scheme not in ("http", "https") or not u.netloc:
        raise ConfigError(f"{where}：url 要写成 http://地址:端口，现在是 {url!r}")
    if u.path not in ("", "/"):
        raise ConfigError(f"{where}：url 只写到端口为止，路径请放到 health")
    health = str(raw.get("health") or "/")
    if not health.startswith("/"):
        raise ConfigError(f"{where}：health 要以 / 开头")
    rt = str(raw.get("read_timeout") or "")
    if rt and not DURATION_RE.match(rt):
        raise ConfigError(f"{where}：read_timeout 写成 30s、10m、1h 这样")
    color = str(raw.get("color") or "")
    if color and not COLOR_RE.match(color):
        raise ConfigError(f"{where}：color 写成 #00A4DC 这样")
    path = str(raw.get("path") or "")
    if path and not path.startswith("/"):
        raise ConfigError(f"{where}：path 要以 / 开头")
    known = {"name", "title", "url", "health", "icon", "group", "desc", "read_timeout", "insecure_tls", "rewrite_host", "color", "path"}
    extra = set(raw) - known
    if extra:
        raise ConfigError(f"{where}：不认识的字段 {', '.join(sorted(extra))}")
    return Service(
        name=name,
        title=str(raw.get("title") or name),
        url=url.rstrip("/"),
        health=health,
        icon=str(raw.get("icon") or ""),
        group=str(raw.get("group") or ""),
        desc=str(raw.get("desc") or ""),
        read_timeout=rt,
        insecure_tls=bool(raw.get("insecure_tls", False)),
        rewrite_host=bool(raw.get("rewrite_host", False)),
        color=color,
        path=path,
    )


def _plugin(raw: Any) -> PluginRef:
    if isinstance(raw, str):
        return PluginRef(raw)
    if isinstance(raw, dict) and raw.get("name"):
        opts = {k: v for k, v in raw.items() if k != "name"}
        return PluginRef(str(raw["name"]), opts)
    raise ConfigError(f"plugins 里的 {raw!r} 看不懂：写插件名，或 {{name: 插件名, 参数: 值}}")


def _shortcuts(raw: Any) -> list:
    out = []
    for i, it in enumerate(raw or []):
        if not isinstance(it, dict) or not it.get("title") or not it.get("value"):
            raise ConfigError(f"shortcuts 第 {i + 1} 项要有 title 和 value")
        out.append({"title": str(it["title"]), "value": str(it["value"]), "icon": str(it.get("icon") or "")})
    return out


def parse(data: Any, source: Path) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("配置文件是空的，或格式不对")
    domain = str(_need(data, "domain", "配置")).lower()
    if not DOMAIN_RE.match(domain):
        raise ConfigError(f"domain {domain!r} 不像一个域名")
    theme = str(data.get("theme") or "glass")
    if theme not in THEMES:
        raise ConfigError(f"theme 只能是 {' / '.join(THEMES)}，现在是 {theme!r}")

    appearance = str(data.get("appearance") or "auto")
    if appearance not in APPEARANCES:
        raise ConfigError(f"appearance 只能是 {' / '.join(APPEARANCES)}")
    charts = data.get("charts") or {}
    trend, gauge = str(charts.get("trend") or ""), str(charts.get("gauge") or "")
    if trend and trend not in TRENDS:
        raise ConfigError(f"charts.trend 只能是 {' / '.join(TRENDS)}")
    if gauge and gauge not in GAUGES:
        raise ConfigError(f"charts.gauge 只能是 {' / '.join(GAUGES)}")

    dns = data.get("dns") or {}
    if (dns.get("provider") or "cloudflare") != "cloudflare":
        raise ConfigError("0.1 版只支持 Cloudflare DNS")
    listen = data.get("listen") or {}
    agent = data.get("agent") or {}
    check = data.get("check") or {}
    check_time = str(check.get("time") or "08:30")
    if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", check_time):
        raise ConfigError("check.time 写成 08:30 这样")

    services = [_service(s, i) for i, s in enumerate(data.get("services") or [])]
    seen = set()
    for s in services:
        if s.name in seen:
            raise ConfigError(f"服务名 `{s.name}` 重复了")
        seen.add(s.name)

    instance = str(data.get("instance") or "")
    if instance and not NAME_RE.match(instance):
        raise ConfigError("instance 只能用小写字母、数字和连字符")
    return Config(
        instance=instance,
        domain=domain,
        title=str(data.get("title") or "我的工作台"),
        subtitle=str(data.get("subtitle") or "家里的服务都在这里 · 只有自己的设备能打开"),
        theme=theme,
        appearance=appearance,
        trend=trend,
        gauge=gauge,
        acme_email=str(_need(data, "acme_email", "配置")),
        token_file=paths.expand(str(dns.get("token_file") or "cloudflare.token")),
        caddy_bin=str(paths.expand(str(data["caddy"]))) if data.get("caddy") else "",
        https_port=int(listen.get("https_port") or 443),
        http_port=int(listen.get("http_port") or 80),
        admin=str(listen.get("admin") or "localhost:2019"),
        agent_port=int(agent.get("port") or 18600),
        allow=TAILNET_CIDRS + [str(c) for c in (data.get("allow_extra") or [])],
        check_time=check_time,
        plugins=[_plugin(p) for p in (data.get("plugins") or [])],
        services=services,
        shortcuts=_shortcuts(data.get("shortcuts")),
        source=source,
    )


def load(path: Path | None = None) -> Config:
    path = path or paths.config_file()
    if not path.exists():
        raise ConfigError(f"找不到配置文件 {path}（可以从 examples/services.example.yaml 复制一份）")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"{path} 不是合法的 YAML：{e}") from None
    return parse(data, path)
