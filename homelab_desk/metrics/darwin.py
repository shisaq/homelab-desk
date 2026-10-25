"""macOS 机器指标：直接调系统接口（ctypes），不需要 root。"""

from __future__ import annotations

import ctypes
import json
import os
import platform
import shutil
import subprocess
import time

_libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
_libc.mach_host_self.restype = ctypes.c_uint32

HOST_CPU_LOAD_INFO = 3
HOST_VM_INFO64 = 4
PROCESSOR_CPU_LOAD_INFO = 2
_task_self = ctypes.c_uint32.in_dll(_libc, "mach_task_self_")


class _VMStats64(ctypes.Structure):
    _fields_ = [
        ("free_count", ctypes.c_uint32),
        ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32),
        ("wire_count", ctypes.c_uint32),
        ("zero_fill_count", ctypes.c_uint64),
        ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64),
        ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64),
        ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64),
        ("purgeable_count", ctypes.c_uint32),
        ("speculative_count", ctypes.c_uint32),
        ("decompressions", ctypes.c_uint64),
        ("compressions", ctypes.c_uint64),
        ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64),
        ("compressor_page_count", ctypes.c_uint32),
        ("throttled_count", ctypes.c_uint32),
        ("external_page_count", ctypes.c_uint32),
        ("internal_page_count", ctypes.c_uint32),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


class _SwapUsage(ctypes.Structure):
    _fields_ = [
        ("total", ctypes.c_uint64),
        ("avail", ctypes.c_uint64),
        ("used", ctypes.c_uint64),
        ("pagesize", ctypes.c_uint32),
        ("encrypted", ctypes.c_bool),
    ]


class _TimeVal(ctypes.Structure):
    _fields_ = [("sec", ctypes.c_long), ("usec", ctypes.c_int32)]


def _sysctl_raw(name: str, buf) -> None:
    size = ctypes.c_size_t(ctypes.sizeof(buf))
    if _libc.sysctlbyname(name.encode(), ctypes.byref(buf), ctypes.byref(size), None, 0) != 0:
        raise OSError(f"sysctl {name} 失败")


def _sysctl_int(name: str, ctype=ctypes.c_int64) -> int:
    v = ctype(0)
    _sysctl_raw(name, v)
    return v.value


def _sysctl_str(name: str) -> str:
    buf = ctypes.create_string_buffer(256)
    _sysctl_raw(name, buf)
    return buf.value.decode(errors="replace")


def _cpu_ticks() -> tuple:
    info = (ctypes.c_uint32 * 4)()  # user, system, idle, nice
    count = ctypes.c_uint32(4)
    _libc.host_statistics(_libc.mach_host_self(), HOST_CPU_LOAD_INFO, info, ctypes.byref(count))
    return tuple(info)


def _core_ticks() -> list:
    """每个核心的 (忙, 总) 累计 tick。"""
    count = ctypes.c_uint32()
    info = ctypes.POINTER(ctypes.c_int32)()
    n = ctypes.c_uint32()
    if _libc.host_processor_info(
        _libc.mach_host_self(), PROCESSOR_CPU_LOAD_INFO, ctypes.byref(count), ctypes.byref(info), ctypes.byref(n)
    ):
        return []
    out = []
    for i in range(count.value):
        user, system, idle, nice = (info[i * 4 + k] & 0xFFFFFFFF for k in range(4))
        out.append((user + system + nice, user + system + idle + nice))
    _libc.vm_deallocate(_task_self, ctypes.cast(info, ctypes.c_void_p), ctypes.c_size_t(n.value * 4))
    return out


def _vm() -> _VMStats64:
    st = _VMStats64()
    count = ctypes.c_uint32(ctypes.sizeof(st) // 4)
    _libc.host_statistics64(_libc.mach_host_self(), HOST_VM_INFO64, ctypes.byref(st), ctypes.byref(count))
    return st


def _net_bytes() -> tuple:
    """物理网卡（en*）累计收发字节。Tailscale 的流量最终也走 en*，不重复计。"""
    out = subprocess.run(["/usr/sbin/netstat", "-ibn"], capture_output=True, text=True, timeout=5).stdout
    rx = tx = 0
    for line in out.splitlines()[1:]:
        cols = line.split()
        if len(cols) >= 10 and cols[0].startswith("en") and cols[2].startswith("<Link#"):
            rx += int(cols[-5])
            tx += int(cols[-2])
    return rx, tx


def _machine_name() -> str:
    """「Mac mini」这样的名字；只在启动时查一次。"""
    try:
        out = subprocess.run(
            ["/usr/sbin/system_profiler", "SPHardwareDataType", "-json"], capture_output=True, text=True, timeout=10
        ).stdout
        return json.loads(out)["SPHardwareDataType"][0].get("machine_name", "")
    except (OSError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
        return ""


# 内存压力：kern.memorystatus_vm_pressure_level 1=正常 2=警告 4=严重
_PRESSURE = {1: "ok", 2: "warn", 4: "crit"}


class Sampler:
    def __init__(self):
        self._cpu = _cpu_ticks()
        self._cores = _core_ticks()
        self._net = _net_bytes()
        self._t = time.monotonic()
        self.static = {
            "os": f"macOS {platform.mac_ver()[0]}",
            "model": _machine_name() or _sysctl_str("hw.model"),
            "cpu": _sysctl_str("machdep.cpu.brand_string"),
            "cores": _sysctl_int("hw.ncpu", ctypes.c_int32),
            "mem_total": _sysctl_int("hw.memsize"),
        }
        self._page = _sysctl_int("hw.pagesize")
        boot = _TimeVal()
        _sysctl_raw("kern.boottime", boot)
        self._boot = boot.sec

    def sample(self) -> dict:
        now = time.monotonic()
        dt = max(now - self._t, 0.001)

        cpu = _cpu_ticks()
        d = [b - a for a, b in zip(self._cpu, cpu)]
        busy = d[0] + d[1] + d[3]
        cpu_pct = 100.0 * busy / max(sum(d), 1)

        cores_now = _core_ticks()
        cores = [
            round(100.0 * (b[0] - a[0]) / max(b[1] - a[1], 1), 1) for a, b in zip(self._cores, cores_now)
        ]
        self._cores = cores_now

        vm = _vm()
        # 与「活动监视器」的「已使用内存」一致：App 内存 + 联动 + 被压缩
        pg = self._page
        app = vm.internal_page_count - vm.purgeable_count
        used = (app + vm.wire_count + vm.compressor_page_count) * pg
        parts = [
            {"key": "app", "label": "App", "bytes": app * pg},
            {"key": "wired", "label": "联动", "bytes": vm.wire_count * pg},
            {"key": "compressed", "label": "压缩", "bytes": vm.compressor_page_count * pg},
            # 文件缓存：随时可以让出来，不算「已使用」
            {"key": "cache", "label": "缓存", "bytes": (vm.external_page_count + vm.purgeable_count) * pg},
        ]
        swap = _SwapUsage()
        _sysctl_raw("vm.swapusage", swap)
        level = _sysctl_int("kern.memorystatus_vm_pressure_level", ctypes.c_int32)

        disk_path = "/System/Volumes/Data" if os.path.isdir("/System/Volumes/Data") else "/"
        du = shutil.disk_usage(disk_path)

        net = _net_bytes()
        rx_rate = max(net[0] - self._net[0], 0) / dt
        tx_rate = max(net[1] - self._net[1], 0) / dt

        self._cpu, self._net, self._t = cpu, net, now
        return {
            "cpu": round(cpu_pct, 1),
            "cores": cores,
            "load": [round(x, 2) for x in os.getloadavg()],
            "mem_used": used,
            "mem_parts": parts,
            "mem_total": self.static["mem_total"],
            "mem_pressure": _PRESSURE.get(level, "ok"),
            # 可分页页与空闲页的比例，包含应用正在使用的页，不能当作空闲内存比例
            "mem_free_pct": _sysctl_int("kern.memorystatus_level", ctypes.c_int32),
            "swap_used": swap.used,
            # APFS 各卷共享容量，按「总量 - 可用」算，和访达里看到的一致
            "disk_used": du.total - du.free,
            "disk_total": du.total,
            "net_rx": round(rx_rate),
            "net_tx": round(tx_rate),
            "uptime": int(time.time() - self._boot),
        }
