"""把 Caddy、desk-agent、每日体检装成后台服务。macOS 用 launchd（LaunchAgent），Linux 用 systemd --user。

只用 launchctl / systemctl 管理进程，不按名字杀进程。
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from . import paths
from .config import Config

BIN = paths.REPO / "bin" / "homelab-desk"
# launchd 给的 PATH 不含 /usr/sbin:/sbin（ipconfig、netstat 等在那里）
SAFE_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


@dataclass
class Unit:
    part: str  # caddy / agent / checkup
    args: list
    keepalive: bool
    at: str = ""  # 定时任务的 HH:MM


def units(cfg: Config) -> list:
    py = sys.executable
    return [
        Unit("caddy", [py, str(BIN), "caddy-run"], True),
        Unit("agent", [py, str(BIN), "agent"], True),
        Unit("checkup", [py, str(BIN), "check", "--notify"], False, cfg.check_time),
    ]


def _env() -> dict:
    env = {"PATH": SAFE_PATH, "LANG": "en_US.UTF-8", "PYTHONUNBUFFERED": "1"}
    if os.environ.get("HOMELAB_DESK_HOME"):
        env["HOMELAB_DESK_HOME"] = os.environ["HOMELAB_DESK_HOME"]
    return env


def _run(*cmd, check=False) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=check)


# ── launchd ──
def _plist(cfg: Config, u: Unit) -> bytes:
    log = str(paths.log_dir() / f"{u.part}.log")
    d = {
        "Label": cfg.label(u.part),
        "ProgramArguments": u.args,
        "EnvironmentVariables": _env(),
        "StandardOutPath": log,
        "StandardErrorPath": log,
        # 不要用 Background：会被系统全面限流
        "ProcessType": "Standard",
    }
    if u.keepalive:
        d["RunAtLoad"] = True
        d["KeepAlive"] = True
        # 开机时 Tailscale / 网络可能还没就绪，退出后 30 秒再拉起
        d["ThrottleInterval"] = 30
    else:
        hh, mm = u.at.split(":")
        d["StartCalendarInterval"] = {"Hour": int(hh), "Minute": int(mm)}
    return plistlib.dumps(d)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _loaded(label: str) -> bool:
    return _run("/bin/launchctl", "print", f"{_domain()}/{label}").returncode == 0


def _launchd_install(cfg: Config) -> list:
    """写 plist 并加载；返回 [(部件, 动作)]。内容没变且已加载的不动（由调用方决定是否重启）。"""
    agents = Path.home() / "Library" / "LaunchAgents"
    agents.mkdir(parents=True, exist_ok=True)
    done = []
    for u in units(cfg):
        label = cfg.label(u.part)
        path = agents / f"{label}.plist"
        data = _plist(cfg, u)
        changed = not path.exists() or path.read_bytes() != data
        if changed:
            path.write_bytes(data)
        if changed or not _loaded(label):
            _run("/bin/launchctl", "bootout", f"{_domain()}/{label}")
            r = _run("/bin/launchctl", "bootstrap", _domain(), str(path))
            if r.returncode != 0:
                raise RuntimeError(f"launchctl bootstrap {label} 失败：{r.stderr.strip()}")
            done.append((u.part, "已安装"))
        else:
            done.append((u.part, "未变"))
    return done


def _launchd_restart(cfg: Config, part: str) -> None:
    _run("/bin/launchctl", "kickstart", "-k", f"{_domain()}/{cfg.label(part)}", check=True)


def _launchd_uninstall(cfg: Config) -> list:
    out = []
    for u in units(cfg):
        label = cfg.label(u.part)
        _run("/bin/launchctl", "bootout", f"{_domain()}/{label}")
        p = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
        if p.exists():
            p.unlink()
            out.append(label)
    return out


def _launchd_status(cfg: Config) -> dict:
    res = {}
    for u in units(cfg):
        r = _run("/bin/launchctl", "print", f"{_domain()}/{cfg.label(u.part)}")
        if r.returncode != 0:
            res[u.part] = "未安装"
            continue
        state = pid = ""
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("state = "):
                state = line.split("= ", 1)[1]
            elif line.startswith("pid = "):
                pid = line.split("= ", 1)[1]
        res[u.part] = f"{state}" + (f"（pid {pid}）" if pid else "")
    return res


# ── systemd --user ──
def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def _systemd_files(cfg: Config, u: Unit) -> dict:
    env = "".join(f'Environment="{k}={v}"\n' for k, v in _env().items())
    cmd = " ".join(f'"{a}"' for a in u.args)
    log = paths.log_dir() / f"{u.part}.log"
    svc = (
        f"[Unit]\nDescription=homelab-desk {u.part}\nAfter=network-online.target\nWants=network-online.target\n\n"
        f"[Service]\n{env}ExecStart={cmd}\nStandardOutput=append:{log}\nStandardError=append:{log}\n"
    )
    files = {}
    if u.keepalive:
        svc += "Restart=always\nRestartSec=30\n\n[Install]\nWantedBy=default.target\n"
    else:
        svc += "Type=oneshot\n"
        files[f"{cfg.label(u.part)}.timer"] = (
            f"[Unit]\nDescription=homelab-desk 每日体检\n\n[Timer]\nOnCalendar=*-*-* {u.at}:00\nPersistent=true\n\n"
            "[Install]\nWantedBy=timers.target\n"
        )
    files[f"{cfg.label(u.part)}.service"] = svc
    return files


def _systemctl(*args) -> subprocess.CompletedProcess:
    return _run("systemctl", "--user", *args)


def _systemd_install(cfg: Config) -> list:
    d = _systemd_dir()
    d.mkdir(parents=True, exist_ok=True)
    done = []
    changed_any = False
    for u in units(cfg):
        changed = False
        for name, text in _systemd_files(cfg, u).items():
            p = d / name
            if not p.exists() or p.read_text() != text:
                p.write_text(text)
                changed = True
        changed_any |= changed
        done.append((u.part, "已安装" if changed else "未变"))
    if changed_any:
        _systemctl("daemon-reload")
    for u in units(cfg):
        unit = cfg.label(u.part) + (".service" if u.keepalive else ".timer")
        r = _systemctl("enable", "--now", unit)
        if r.returncode != 0:
            raise RuntimeError(f"systemctl --user enable {unit} 失败：{r.stderr.strip()}")
    return done


def _systemd_restart(cfg: Config, part: str) -> None:
    r = _systemctl("restart", f"{cfg.label(part)}.service")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())


def _systemd_uninstall(cfg: Config) -> list:
    out = []
    for u in units(cfg):
        for suffix in (".timer", ".service"):
            name = cfg.label(u.part) + suffix
            _systemctl("disable", "--now", name)
            p = _systemd_dir() / name
            if p.exists():
                p.unlink()
                out.append(name)
    _systemctl("daemon-reload")
    return out


def _systemd_status(cfg: Config) -> dict:
    res = {}
    for u in units(cfg):
        unit = cfg.label(u.part) + (".service" if u.keepalive else ".timer")
        res[u.part] = _systemctl("is-active", unit).stdout.strip() or "未安装"
    return res


# ── 对外 ──
DARWIN = sys.platform == "darwin"
install = _launchd_install if DARWIN else _systemd_install
restart = _launchd_restart if DARWIN else _systemd_restart
uninstall = _launchd_uninstall if DARWIN else _systemd_uninstall
status = _launchd_status if DARWIN else _systemd_status
