"""Linux 机器指标：读 /proc，不需要 root。"""

from __future__ import annotations

import os
import platform
import shutil
import time


def _ticks(line: str) -> tuple:
    vals = [int(x) for x in line.split()[1:]]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    return sum(vals) - idle, sum(vals)


def _cpu_ticks() -> tuple:
    with open("/proc/stat") as f:
        return _ticks(f.readline())


def _core_ticks() -> list:
    with open("/proc/stat") as f:
        return [_ticks(l) for l in f if l.startswith("cpu") and l[3].isdigit()]


def _meminfo() -> dict:
    out = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, v = line.split(":", 1)
            out[k] = int(v.split()[0]) * 1024
    return out


def _net_bytes() -> tuple:
    rx = tx = 0
    with open("/proc/net/dev") as f:
        for line in f.readlines()[2:]:
            name, data = line.split(":", 1)
            name = name.strip()
            if name == "lo" or name.startswith(("tailscale", "docker", "veth", "br-", "virbr")):
                continue
            cols = data.split()
            rx += int(cols[0])
            tx += int(cols[8])
    return rx, tx


def _pressure() -> str:
    """PSI：过去 10 秒有进程因内存而等待的比例（内核 4.20+；没有就当正常）。"""
    try:
        with open("/proc/pressure/memory") as f:
            avg10 = float(f.readline().split()[1].split("=")[1])
    except (OSError, IndexError, ValueError):
        return "ok"
    return "crit" if avg10 >= 20 else "warn" if avg10 >= 5 else "ok"


def _model() -> str:
    for p in ("/sys/devices/virtual/dmi/id/product_name", "/proc/device-tree/model"):
        try:
            with open(p) as f:
                return f.read().strip("\x00\n ")
        except OSError:
            pass
    return platform.machine()


def _cpu_name() -> str:
    try:
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith(("model name", "Model")):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or platform.machine()


class Sampler:
    def __init__(self):
        self._cpu = _cpu_ticks()
        self._cores = _core_ticks()
        self._net = _net_bytes()
        self._t = time.monotonic()
        self.static = {
            "os": f"Linux {platform.release()}",
            "model": _model(),
            "cpu": _cpu_name(),
            "cores": os.cpu_count() or 1,
            "mem_total": _meminfo()["MemTotal"],
        }

    def sample(self) -> dict:
        now = time.monotonic()
        dt = max(now - self._t, 0.001)
        cpu = _cpu_ticks()
        busy, total = cpu[0] - self._cpu[0], cpu[1] - self._cpu[1]
        cores_now = _core_ticks()
        cores = [round(100.0 * (b[0] - a[0]) / max(b[1] - a[1], 1), 1) for a, b in zip(self._cores, cores_now)]
        self._cores = cores_now
        mi = _meminfo()
        avail = mi.get("MemAvailable", mi["MemFree"])
        cache = mi.get("Cached", 0) + mi.get("Buffers", 0) + mi.get("SReclaimable", 0)
        du = shutil.disk_usage("/")
        net = _net_bytes()
        with open("/proc/uptime") as f:
            uptime = int(float(f.read().split()[0]))
        res = {
            "cpu": round(100.0 * busy / max(total, 1), 1),
            "cores": cores,
            "load": [round(x, 2) for x in os.getloadavg()],
            "mem_used": mi["MemTotal"] - avail,
            "mem_parts": [
                {"key": "app", "label": "已使用", "bytes": mi["MemTotal"] - avail},
                {"key": "cache", "label": "缓存", "bytes": max(min(cache, avail - mi["MemFree"]), 0)},
            ],
            "mem_total": mi["MemTotal"],
            "mem_pressure": _pressure(),
            "mem_free_pct": round(100 * avail / mi["MemTotal"]),
            "swap_used": mi.get("SwapTotal", 0) - mi.get("SwapFree", 0),
            "disk_used": du.total - du.free,
            "disk_total": du.total,
            "net_rx": round(max(net[0] - self._net[0], 0) / dt),
            "net_tx": round(max(net[1] - self._net[1], 0) / dt),
            "uptime": uptime,
        }
        self._cpu, self._net, self._t = cpu, net, now
        return res
