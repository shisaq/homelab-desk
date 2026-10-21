"""homelab-desk 命令行。"""

from __future__ import annotations

import argparse
import errno
import os
import shutil
import socket
import ssl
import subprocess
import sys
import time

from . import __version__, paths
from . import caddy as caddymod
from . import check as checkmod
from . import cloudflare as cf
from . import config as cfgmod
from . import catalog, edit, net, plugins, service, site

OK, WARN, BAD = "✅", "⚠️ ", "❌"


def say(icon: str, text: str) -> None:
    print(f"{icon} {text}")


def tailscale_exe() -> str:
    return shutil.which("tailscale") or "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def tailscale_ip() -> str:
    try:
        out = subprocess.run([tailscale_exe(), "ip", "-4"], capture_output=True, text=True, timeout=10).stdout
    except OSError:
        return ""
    return out.strip().splitlines()[0] if out.strip() else ""


# ── init ──
def cmd_init(a) -> int:
    dst = paths.config_file()
    if dst.exists() and not a.force:
        say(WARN, f"{dst} 已存在，没有覆盖（要重写加 --force）")
        return 1
    dst.parent.mkdir(parents=True, exist_ok=True)
    text = (paths.REPO / "examples" / "services.example.yaml").read_text(encoding="utf-8")
    text = text.replace("desk.example.com", a.domain).replace("you@example.com", a.email)
    dst.write_text(text, encoding="utf-8")
    say(OK, f"已写入 {dst}")
    tok = paths.config_dir() / "cloudflare.token"
    if a.token_stdin:
        # 一创建就是 600，不留别人可读的空档
        fd = os.open(tok, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(sys.stdin.read().strip() + "\n")
        tok.chmod(0o600)
        say(OK, f"Cloudflare Token 已保存到 {tok}（权限 600）")
    elif not tok.exists():
        say(WARN, f"还需要把 Cloudflare Token 存到 {tok}（权限 600），或运行 homelab-desk init --token-stdin")
    return 0


# ── doctor ──
def cmd_doctor(a) -> int:
    bad = 0
    say(OK if sys.version_info >= (3, 9) else BAD, f"Python {sys.version.split()[0]}（需要 3.9+）")
    try:
        cfg = cfgmod.load()
        say(OK, f"配置 {cfg.source}：{cfg.domain}，{len(cfg.services)} 个服务")
    except cfgmod.ConfigError as e:
        say(BAD, str(e))
        return 2
    try:
        plugins.load(cfg, quiet=True)
    except cfgmod.ConfigError as e:
        say(BAD, str(e))
        bad += 1

    ip = tailscale_ip()
    if ip:
        say(OK, f"Tailscale 地址 {ip}")
    else:
        say(BAD, "Tailscale 没装或没登录（https://tailscale.com/download）")
        bad += 1

    try:
        token = caddymod.read_token(cfg)
        if cfg.token_file.stat().st_mode & 0o077:
            say(WARN, f"{cfg.token_file} 别人也能读，建议 chmod 600")
        cf.verify(token)
        zone, zname = cf.find_zone(token, cfg.domain)
        say(OK, f"Cloudflare Token 有效，能管理 {zname}")
        for name in (cfg.domain, f"*.{cfg.domain}"):
            recs = [r for r in cf.records(token, zone, name) if r["type"] == "A"]
            if not recs:
                say(WARN, f"Cloudflare 上还没有 {name} 的记录（homelab-desk dns 可自动创建）")
            elif recs[0]["proxied"]:
                say(BAD, f"{name} 开着橙色云代理，必须改成灰云（DNS only）")
                bad += 1
            elif ip and recs[0]["content"] != ip:
                say(WARN, f"{name} 指向 {recs[0]['content']}，不是本机 Tailscale 地址 {ip}")
            else:
                say(OK, f"{name} → {recs[0]['content']}（灰云）")
    except (FileNotFoundError, cf.CFError) as e:
        say(BAD, f"Cloudflare：{e}")
        bad += 1

    binary = caddymod.find_binary(cfg)
    say(OK if binary else WARN, f"Caddy：{binary}" if binary else "还没有带 cloudflare 插件的 Caddy（deploy 时会自动下载）")

    # 端口：被别的程序占了就没法启动
    for port in (cfg.https_port, cfg.http_port):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("0.0.0.0", port))
            say(OK, f"端口 {port} 可用")
        except OSError as e:
            if e.errno == errno.EADDRINUSE:
                st = service.status(cfg).get("caddy", "")
                mine = st.startswith("running") or st == "active"
                say(OK if mine else WARN, f"端口 {port} 已被{'本工作台的 Caddy' if mine else '别的程序'}占用")
            else:
                say(BAD, f"端口 {port} 无法监听：{e}")
                bad += 1
        finally:
            s.close()

    # 本机代理（Clash 等）：系统代理会把对这些域名的访问截走
    proxy = os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
    if sys.platform == "darwin":
        out = subprocess.run(["/usr/sbin/scutil", "--proxy"], capture_output=True, text=True).stdout
        if "HTTPSEnable : 1" in out:
            proxy = proxy or "系统代理"
    if proxy:
        say(WARN, f"检测到代理（{proxy}）：请在代理软件的绕过/直连列表里加 {cfg.domain} 和 *.{cfg.domain}，否则浏览器可能 502")
    return 2 if bad else 0


# ── dns ──
def cmd_dns(a) -> int:
    cfg = cfgmod.load()
    ip = a.ip or tailscale_ip()
    if not ip:
        say(BAD, "拿不到本机 Tailscale 地址，用 --ip 指定")
        return 2
    token = caddymod.read_token(cfg)
    try:
        zone, zname = cf.find_zone(token, cfg.domain)
        for name in (cfg.domain, f"*.{cfg.domain}"):
            say(OK, f"{name} → {ip}：{cf.ensure_a(token, zone, name, ip)}（灰云）")
        ns = cf.nameservers(token, zone)
    except cf.CFError as e:
        say(BAD, str(e))
        return 2
    print(f"\n自查（直接问 Cloudflare 的 nameserver，不受缓存影响）：\n  dig +short @{ns[0] if ns else 'NS'} {cfg.domain}")
    print("刚把域名迁到 Cloudflare 的话，部分网络最长 48 小时内还会问旧 nameserver，新子域会时好时坏，属正常。")
    return 0


# ── deploy ──
def _wait_https(cfg, host: str, seconds: int) -> str:
    """等证书签好：返回空字符串表示成功，否则返回最后的错误。"""
    err = ""
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            net.https_get(cfg, host)
            return ""
        except ssl.SSLError as e:
            err = f"证书还没好（{getattr(e, 'verify_message', '') or e.reason}）"
        except OSError as e:
            err = str(e)
        time.sleep(3)
    return err


def cmd_deploy(a) -> int:
    cfg = cfgmod.load()
    plugins.load(cfg, quiet=True)  # 插件名写错尽早报
    token = caddymod.read_token(cfg)
    for d in (paths.data_dir(), paths.log_dir()):
        d.mkdir(parents=True, exist_ok=True)

    binary = caddymod.find_binary(cfg)
    if not binary:
        say("⏳", "下载带 cloudflare 插件的 Caddy（官网现编译，约 1 分钟）…")
        binary = caddymod.download()
    say(OK, f"Caddy：{binary}")

    new = caddymod.render(cfg)
    path = caddymod.caddyfile()
    tmp = path.with_suffix(".new")
    tmp.write_text(new, encoding="utf-8")
    err = caddymod.validate(binary, tmp, token)
    if err:
        tmp.unlink()
        say(BAD, f"生成的 Caddy 配置没通过校验，未生效：\n{err}")
        return 2
    caddy_changed = not path.exists() or path.read_text(encoding="utf-8") != new
    tmp.replace(path)
    say(OK, f"Caddy 配置：{path}")

    site_dir, missing = site.build(cfg)
    say(OK, f"工作台页面：{site_dir}（主题 {cfg.theme}）")
    for m in missing:
        say(WARN, f"没找到图标 {m}，用首字母代替（可在 simpleicons.org 或 dashboardicons.com 查名字）")

    actions = dict(service.install(cfg))
    if actions["caddy"] == "未变" and caddy_changed:
        if caddymod.reload(binary, cfg):
            actions["caddy"] = "已热加载"
        else:
            service.restart(cfg, "caddy")
            actions["caddy"] = "已重启"
    if actions["agent"] == "未变":
        service.restart(cfg, "agent")
        actions["agent"] = "已重启"
    names = {"caddy": "Caddy", "agent": "指标服务", "checkup": f"每日体检（{cfg.check_time}）"}
    say(OK, "后台服务：" + "，".join(f"{names[k]} {v}" for k, v in actions.items()))

    if a.no_wait:
        return 0
    say("⏳", "等证书与服务就绪（第一次申请证书约需 1 分钟）…")
    err = _wait_https(cfg, cfg.domain, 150)
    if err:
        say(WARN, f"工作台暂时打不开：{err}\n   看日志：{paths.log_dir() / 'caddy.log'}")
        return 1
    say(OK, f"工作台就绪：{cfg.url(cfg.domain)}")
    if cfg.services and not _wait_https(cfg, cfg.host(cfg.services[0]), 120):
        say(OK, f"服务地址形如：{cfg.url(cfg.host(cfg.services[0]))}")
    return 0


# ── add / remove / list ──
def cmd_add(a) -> int:
    svc = {k: getattr(a, k, None) for k in edit.FIELDS}
    entry = catalog.find(a.name)
    if entry:
        # 目录里有的服务：没写的字段用目录的默认值
        for k in catalog.DEFAULTS:
            if not svc.get(k) and entry.get(k):
                svc[k] = entry[k]
        svc["group"] = svc["group"] or entry["category"]
        svc["url"] = svc["url"] or f"127.0.0.1:{entry['port']}"
    if not svc["url"]:
        say(BAD, f"`{a.name}` 不在服务目录里，需要写地址，如 homelab-desk add {a.name} 127.0.0.1:8080")
        return 2
    if "://" not in svc["url"]:
        svc["url"] = "http://" + svc["url"]
    cfg = edit.add(paths.config_file(), svc)
    say(OK, f"已加入 {svc['name']} → {svc['url']}")
    if entry and entry.get("notes"):
        say(WARN, f"注意：{entry['notes'].replace('<你的域名>', cfg.domain)}")
    return 0 if a.no_deploy else cmd_deploy(argparse.Namespace(no_wait=False)) if _deployed(cfg) else 0


def cmd_remove(a) -> int:
    cfg = edit.remove(paths.config_file(), a.name)
    say(OK, f"已删除 {a.name}（原文件备份为 services.yaml.bak）")
    return 0 if a.no_deploy else cmd_deploy(argparse.Namespace(no_wait=True)) if _deployed(cfg) else 0


def _deployed(cfg) -> bool:
    st = service.status(cfg).get("agent", "未安装")
    if st == "未安装":
        say(WARN, "还没部署过，运行 homelab-desk deploy 生效")
        return False
    return True


def cmd_list(a) -> int:
    cfg = cfgmod.load()
    print(f"{cfg.title} · {cfg.url(cfg.domain)}\n")
    for s in cfg.services:
        print(f"  {s.name:<14} {cfg.url(cfg.host(s)):<44} → {s.url}{s.health if s.health != '/' else ''}")
    if not cfg.services:
        print("  （还没有服务：homelab-desk add <名字> <地址:端口>）")
    return 0


# ── check / status ──
def cmd_check(a) -> int:
    cfg = cfgmod.load()
    r = checkmod.run(cfg)
    print(r.text())
    if a.notify:
        for e in checkmod.notify(cfg, r):
            print(f"通知失败：{e}", file=sys.stderr)
    return r.code


def cmd_status(a) -> int:
    cfg = cfgmod.load()
    names = {"caddy": "Caddy", "agent": "指标服务", "checkup": "每日体检"}
    for k, v in service.status(cfg).items():
        print(f"  {names[k]:<6} {cfg.label(k):<48} {v}")
    print(f"\n  日志：{paths.log_dir()}")
    return 0


def cmd_uninstall(a) -> int:
    cfg = cfgmod.load()
    for x in service.uninstall(cfg):
        say(OK, f"已移除 {x}")
    print(f"配置（{paths.config_dir()}）与证书（{paths.data_dir()}）保留；不要了可以手动删除。")
    return 0


# ── catalog ──
def cmd_catalog(a) -> int:
    found = catalog.search(a.query or "")
    if not found:
        print(f"目录里没有和「{a.query}」相关的服务")
        return 1
    for cat, items in found:
        print(f"\n## {cat}")
        for s in items:
            where = "原生" if s.get("native") else "Docker"
            print(f"  {s['name']:<14} {s['title']}：{s['desc']}（端口 {s.get('port', '-')}，{where}，{s.get('license', '')}）")
            if a.query:
                print(f"  {'':<14} 适合：{s.get('for', '')}")
                for k, v in (s.get("install") or {}).items():
                    print(f"  {'':<14} 安装（{k}）：{v}")
                if s.get("notes"):
                    print(f"  {'':<14} 注意：{s['notes']}")
    print("\n加入：homelab-desk add <名字> [地址:端口]（目录里的服务可以不写地址，默认 127.0.0.1:<默认端口>）")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="homelab-desk", description="家庭服务器工作台")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="生成配置文件")
    s.add_argument("--domain", required=True, help="工作台域名，如 desk.example.com；服务是 <名字>.desk.example.com")
    s.add_argument("--email", required=True, help="申请证书用的邮箱")
    s.add_argument("--token-stdin", action="store_true", help="从标准输入读 Cloudflare Token 并保存")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    sub.add_parser("doctor", help="检查前置条件").set_defaults(fn=cmd_doctor)

    s = sub.add_parser("dns", help="在 Cloudflare 上创建 / 修正两条 A 记录（灰云）")
    s.add_argument("--ip", help="默认用本机 Tailscale IPv4")
    s.set_defaults(fn=cmd_dns)

    s = sub.add_parser("deploy", help="按配置生成并启用一切（可重复执行）")
    s.add_argument("--no-wait", action="store_true", help="不等证书就绪")
    s.set_defaults(fn=cmd_deploy)

    s = sub.add_parser("add", help="加一个服务")
    s.add_argument("name", help="名字，也是子域名")
    s.add_argument("url", nargs="?", help="地址:端口，如 127.0.0.1:8096 或 https://192.168.1.5:5001；目录里的服务可省略")
    for f in ("title", "health", "path", "icon", "color", "group", "desc", "read_timeout"):
        s.add_argument(f"--{f.replace('_', '-')}", dest=f)
    s.add_argument("--insecure-tls", action="store_true", help="后端是自签证书的 https")
    s.add_argument("--rewrite-host", action="store_true", help="转发时把 Host 改成后端地址（只认 localhost 的服务需要）")
    s.add_argument("--no-deploy", action="store_true", help="只改配置，不部署")
    s.set_defaults(fn=cmd_add)

    s = sub.add_parser("remove", help="删一个服务")
    s.add_argument("name")
    s.add_argument("--no-deploy", action="store_true")
    s.set_defaults(fn=cmd_remove)

    sub.add_parser("list", help="列出服务").set_defaults(fn=cmd_list)

    s = sub.add_parser("check", help="体检，输出纯文本；退出码 0 正常 / 1 警告 / 2 严重")
    s.add_argument("--notify", action="store_true", help="结果交给通知插件")
    s.set_defaults(fn=cmd_check)

    sub.add_parser("status", help="后台服务状态").set_defaults(fn=cmd_status)

    s = sub.add_parser("catalog", help="推荐服务目录")
    s.add_argument("query", nargs="?", help="按关键词筛选，如 相册、广告")
    s.set_defaults(fn=cmd_catalog)

    sub.add_parser("uninstall", help="移除后台服务（保留配置和证书）").set_defaults(fn=cmd_uninstall)

    # 以下两个给后台服务用
    sub.add_parser("agent", help=argparse.SUPPRESS).set_defaults(fn=lambda a: _agent())
    sub.add_parser("caddy-run", help=argparse.SUPPRESS).set_defaults(fn=lambda a: caddymod.run(cfgmod.load()))

    a = p.parse_args(argv)
    try:
        return a.fn(a) or 0
    except cfgmod.ConfigError as e:
        say(BAD, f"配置有误：{e}")
        return 2
    except (FileNotFoundError, cf.CFError, RuntimeError) as e:
        say(BAD, str(e))
        return 2
    except KeyboardInterrupt:
        return 130


def _agent() -> int:
    from . import agent

    agent.serve(cfgmod.load())
    return 0
