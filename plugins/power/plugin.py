"""电源 / UPS。

- macOS：读 pmset（接在本机 USB 上的 UPS、笔记本电池）
- 任意平台：连 NUT 服务读 UPS（群晖「UPS 网络服务器」、Linux 的 nut-server 都是它）

参数（都可选）：
  nut: ups@192.168.1.10      # NUT 的 UPS 名@地址[:端口]，群晖的 UPS 名是 ups
  low: 30                    # 电量低于多少时告警
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys

TITLE = "电源"
PLATFORMS = ["darwin", "linux"]
INTERVAL = 15


def _pmset() -> list:
    if sys.platform != "darwin":
        return []
    out = subprocess.run(["/usr/bin/pmset", "-g", "ps"], capture_output=True, text=True, timeout=5).stdout
    src = re.search(r"drawing from '([^']+)'", out)
    res = []
    for line in out.splitlines():
        # " -InternalBattery-0 (id=...)	85%; discharging; 3:20 remaining present: true"
        m = re.match(r"\s*-(.+?) \(id=\d+\)\s+(\d+)%;\s*([^;]+);\s*(.*)", line)
        if m:
            name, pct, state, rest = m.groups()
            remain = re.search(r"(\d+:\d+) remaining", rest)
            res.append(
                {
                    "name": "电池" if name.startswith("InternalBattery") else name,
                    "charge": int(pct),
                    "on_battery": state.strip() == "discharging",
                    "remaining": remain.group(1) if remain else "",
                }
            )
    return [{"source": src.group(1) if src else "", "devices": res}]


def _nut(target: str) -> dict:
    """NUT 网络协议：LIST VAR <ups>，每行 VAR <ups> <名字> "<值>"。"""
    ups, _, hostport = target.partition("@")
    host, _, port = hostport.partition(":")
    vals = {}
    with socket.create_connection((host or "127.0.0.1", int(port or 3493)), timeout=4) as s:
        s.sendall(f"LIST VAR {ups}\n".encode())
        buf = b""
        while b"END LIST VAR" not in buf and b"ERR" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        s.sendall(b"LOGOUT\n")
    text = buf.decode(errors="replace")
    if text.startswith("ERR"):
        raise RuntimeError(f"NUT 返回 {text.strip()}")
    for m in re.finditer(r'^VAR \S+ (\S+) "(.*)"$', text, re.M):
        vals[m.group(1)] = m.group(2)
    return vals


def collect(ctx):
    low = int(ctx.options.get("low", 30))
    items = []
    nut = ctx.options.get("nut")
    if nut:
        v = _nut(str(nut))
        status = v.get("ups.status", "")
        on_batt = "OB" in status.split()
        charge = int(float(v.get("battery.charge", 0)))
        level = "crit" if on_batt and charge < low else "warn" if on_batt else "ok"
        items.append({"label": "UPS 电量", "value": charge, "unit": "%", "max": 100, "level": level})
        items.append({"label": "UPS 状态", "value": "电池供电" if on_batt else "市电", "level": level})
        if v.get("battery.runtime"):
            items.append({"label": "可撑", "value": int(float(v["battery.runtime"]) // 60), "unit": "分钟"})
        if v.get("ups.load"):
            items.append({"label": "负载", "value": int(float(v["ups.load"])), "unit": "%", "max": 100})
    for p in _pmset():
        for d in p["devices"]:
            level = "crit" if d["on_battery"] and d["charge"] < low else "warn" if d["on_battery"] else "ok"
            items.append({"label": f"{d['name']}", "value": d["charge"], "unit": "%", "max": 100, "level": level})
            if d["remaining"] and d["on_battery"]:
                items.append({"label": "剩余", "value": d["remaining"]})
        if not p["devices"] and p["source"] and not nut:
            items.append({"label": "供电", "value": "市电" if p["source"] == "AC Power" else p["source"]})
    return {"items": items}


def check(ctx):
    try:
        items = collect(ctx)["items"]
    except Exception as e:
        return [("warn", f"读不到 UPS：{e}")]
    worst = max((it.get("level", "ok") for it in items), key=("ok", "warn", "crit").index, default="ok")
    text = "，".join(f"{it['label']} {it['value']}{it.get('unit', '')}" for it in items) or "无 UPS / 电池信息"
    return [(worst, f"电源：{text}")]
