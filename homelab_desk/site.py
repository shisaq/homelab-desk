"""生成工作台静态页：复制 web/，写 config.json，取图标。"""

from __future__ import annotations

import hashlib
import html
import json
import shutil
import urllib.request
from pathlib import Path

from . import __version__, paths
from .config import Config

# 图标来源。simple-icons 是单色品牌图标（CC0），由主题着色；dashboard-icons 是彩色图标（Apache-2.0）
SIMPLE_ICONS = "https://cdn.jsdelivr.net/npm/simple-icons@16/icons/{}.svg"
DASHBOARD_ICONS = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/{}.svg"


def _fetch(url: str, dest: Path) -> bool:
    if dest.exists():
        return True
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            data = r.read()
    except OSError:
        return False
    if b"<svg" not in data[:2000]:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    return True


def resolve_icon(icon: str, out: Path) -> dict | None:
    """icon 写法：jellyfin（先找 simple-icons，再找 dashboard-icons）、si:xxx、di:xxx，
    或放在 ~/.config/homelab-desk/icons/ 下的文件名（不带扩展名）。返回 {src, mono}。"""
    if not icon:
        return None
    cache = paths.data_dir() / "icon-cache"
    src_name = icon.split(":", 1)[-1]
    for ext in (".svg", ".png"):
        user = paths.config_dir() / "icons" / f"{src_name}{ext}"
        if user.exists() and ":" not in icon:
            shutil.copyfile(user, out / f"user-{src_name}{ext}")
            return {"src": f"icons/user-{src_name}{ext}", "mono": False}
    tries = []
    if not icon.startswith("di:"):
        tries.append(("si", SIMPLE_ICONS, True))
    if not icon.startswith("si:"):
        tries.append(("di", DASHBOARD_ICONS, False))
    for prefix, url, mono in tries:
        cached = cache / f"{prefix}-{src_name}.svg"
        if _fetch(url.format(src_name), cached):
            shutil.copyfile(cached, out / f"{prefix}-{src_name}.svg")
            return {"src": f"icons/{prefix}-{src_name}.svg", "mono": mono}
    return None


def auto_color(name: str) -> str:
    """没写 color 时按名字取一个稳定的颜色。"""
    hue = int(hashlib.md5(name.encode()).hexdigest()[:4], 16) % 360
    return f"hsl({hue} 65% 52%)"


def build(cfg: Config) -> tuple:
    """返回 (站点目录, 没找到图标的名字列表)。"""
    site = paths.data_dir() / "site"
    tmp = site.with_name("site.new")
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(paths.WEB, tmp)
    icons = tmp / "icons"
    icons.mkdir(exist_ok=True)

    missing = []
    services = []
    for s in cfg.services:
        ic = resolve_icon(s.icon or s.name, icons)
        if s.icon and not ic:
            missing.append(s.icon)
        services.append(
            {
                "name": s.name,
                "title": s.title,
                "desc": s.desc,
                "group": s.group,
                "href": cfg.url(cfg.host(s)) + s.path,
                "host": cfg.host(s),
                "color": s.color or auto_color(s.name),
                "icon": ic,
            }
        )
    shortcuts = []
    for sc in cfg.shortcuts:
        ic = resolve_icon(sc["icon"], icons)
        if sc["icon"] and not ic:
            missing.append(sc["icon"])
        shortcuts.append({**sc, "icon": ic})

    conf = {
        "title": cfg.title,
        "subtitle": cfg.subtitle,
        "theme": cfg.theme,
        "appearance": cfg.appearance,
        "charts": {"trend": cfg.trend, "gauge": cfg.gauge},
        "version": __version__,
        "services": services,
        "shortcuts": shortcuts,
    }
    (tmp / "config.json").write_text(json.dumps(conf, ensure_ascii=False, indent=1), encoding="utf-8")

    # 主题和标题直接写进页面，避免打开时先闪一下默认样式
    index = tmp / "index.html"
    index.write_text(
        index.read_text(encoding="utf-8")
        .replace("{{THEME}}", cfg.theme)
        .replace("{{APPEARANCE}}", cfg.appearance)
        .replace("{{TITLE}}", html.escape(cfg.title)),
        encoding="utf-8",
    )

    # 整体替换，Caddy 不会读到写了一半的目录
    old = site.with_name("site.old")
    if old.exists():
        shutil.rmtree(old)
    if site.exists():
        site.rename(old)
    tmp.rename(site)
    if old.exists():
        shutil.rmtree(old)
    return site, missing
