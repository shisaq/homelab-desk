"""温度。macOS：IOHID 温度传感器（Apple 芯片，不需要 root）；Linux：/sys/class/thermal 与 hwmon。

参数（都可选）：
  warn: 80   # 超过多少度显示黄色
  crit: 95   # 超过多少度显示红色
"""

from __future__ import annotations

import ctypes
import sys
from pathlib import Path

TITLE = "温度"
PLATFORMS = ["darwin", "linux"]
INTERVAL = 5


# ── macOS：IOHIDEventSystemClient（私有但稳定的接口，活动监视器类工具都用它）──
_hid = None


def _hid_init():
    global _hid
    if _hid is not None:
        return _hid
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    io = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    vp = ctypes.c_void_p
    cf.CFStringCreateWithCString.restype = vp
    cf.CFStringCreateWithCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFNumberCreate.restype = vp
    cf.CFNumberCreate.argtypes = [vp, ctypes.c_int, vp]
    cf.CFDictionaryCreate.restype = vp
    cf.CFDictionaryCreate.argtypes = [vp, vp, vp, ctypes.c_long, vp, vp]
    cf.CFArrayGetCount.restype = ctypes.c_long
    cf.CFArrayGetCount.argtypes = [vp]
    cf.CFArrayGetValueAtIndex.restype = vp
    cf.CFArrayGetValueAtIndex.argtypes = [vp, ctypes.c_long]
    cf.CFStringGetCString.restype = ctypes.c_bool
    cf.CFStringGetCString.argtypes = [vp, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    cf.CFRelease.argtypes = [vp]
    io.IOHIDEventSystemClientCreate.restype = vp
    io.IOHIDEventSystemClientCreate.argtypes = [vp]
    io.IOHIDEventSystemClientSetMatching.argtypes = [vp, vp]
    io.IOHIDEventSystemClientCopyServices.restype = vp
    io.IOHIDEventSystemClientCopyServices.argtypes = [vp]
    io.IOHIDServiceClientCopyProperty.restype = vp
    io.IOHIDServiceClientCopyProperty.argtypes = [vp, vp]
    io.IOHIDServiceClientCopyEvent.restype = vp
    io.IOHIDServiceClientCopyEvent.argtypes = [vp, ctypes.c_int64, ctypes.c_int32, ctypes.c_int64]
    io.IOHIDEventGetFloatValue.restype = ctypes.c_double
    io.IOHIDEventGetFloatValue.argtypes = [vp, ctypes.c_int32]

    utf8 = 0x08000100

    def cfstr(s):
        return cf.CFStringCreateWithCString(None, s.encode(), utf8)

    def cfint(n):
        v = ctypes.c_int32(n)
        return cf.CFNumberCreate(None, 3, ctypes.byref(v))  # kCFNumberSInt32Type

    keys = (vp * 2)(cfstr("PrimaryUsagePage"), cfstr("PrimaryUsage"))
    vals = (vp * 2)(cfint(0xFF00), cfint(5))  # 厂商页 / 温度传感器
    match = cf.CFDictionaryCreate(
        None,
        keys,
        vals,
        2,
        ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryKeyCallBacks")),
        ctypes.addressof(ctypes.c_char.in_dll(cf, "kCFTypeDictionaryValueCallBacks")),
    )
    client = io.IOHIDEventSystemClientCreate(None)
    io.IOHIDEventSystemClientSetMatching(client, match)
    _hid = (cf, io, client, cfstr("Product"))
    return _hid


def _darwin_sensors() -> list:
    cf, io, client, product = _hid_init()
    services = io.IOHIDEventSystemClientCopyServices(client)
    out = []
    if not services:
        return out
    buf = ctypes.create_string_buffer(128)
    for i in range(cf.CFArrayGetCount(services)):
        sc = cf.CFArrayGetValueAtIndex(services, i)
        name = io.IOHIDServiceClientCopyProperty(sc, product)
        ev = io.IOHIDServiceClientCopyEvent(sc, 15, 0, 0)  # kIOHIDEventTypeTemperature
        if name and ev:
            if cf.CFStringGetCString(name, buf, 128, 0x08000100):
                out.append((buf.value.decode(errors="replace"), io.IOHIDEventGetFloatValue(ev, 15 << 16)))
        if ev:
            cf.CFRelease(ev)
        if name:
            cf.CFRelease(name)
    cf.CFRelease(services)
    return out


def _darwin_groups() -> dict:
    """把几十个传感器归成几类，取最高值。"""
    groups = {}
    for name, t in _darwin_sensors():
        if not -20 < t < 150:  # 有的传感器返回 -9000 之类的无效值
            continue
        n = name.lower()
        if "tdie" in n or "pacc" in n or "eacc" in n or "cpu" in n:
            key = "CPU"
        elif "gpu" in n:
            key = "GPU"
        elif "nand" in n:
            key = "SSD"
        elif "battery" in n or "gas gauge" in n:
            key = "电池"
        else:
            continue
        groups[key] = max(groups.get(key, -999.0), t)
    return groups


# ── Linux ──
def _linux_groups() -> dict:
    groups = {}
    for zone in Path("/sys/class/thermal").glob("thermal_zone*"):
        try:
            kind = (zone / "type").read_text().strip()
            t = int((zone / "temp").read_text()) / 1000
        except (OSError, ValueError):
            continue
        key = "CPU" if any(k in kind.lower() for k in ("cpu", "x86_pkg", "soc", "k10temp")) else kind
        groups[key] = max(groups.get(key, -999.0), t)
    for hw in Path("/sys/class/hwmon").glob("hwmon*"):
        try:
            chip = (hw / "name").read_text().strip()
        except OSError:
            continue
        key = {"k10temp": "CPU", "coretemp": "CPU", "nvme": "SSD", "amdgpu": "GPU"}.get(chip)
        if not key:
            continue
        for f in hw.glob("temp*_input"):
            try:
                groups[key] = max(groups.get(key, -999.0), int(f.read_text()) / 1000)
            except (OSError, ValueError):
                pass
    return groups


def collect(ctx):
    warn = float(ctx.options.get("warn", 80))
    crit = float(ctx.options.get("crit", 95))
    groups = _darwin_groups() if sys.platform == "darwin" else _linux_groups()
    order = ["CPU", "GPU", "SSD", "电池"]
    items = []
    for key in sorted(groups, key=lambda k: (order.index(k) if k in order else 99, k)):
        t = round(groups[key], 1)
        level = "crit" if t >= crit else "warn" if t >= warn else "ok"
        items.append({"label": key, "value": t, "unit": "°C", "max": 110, "level": level})
    return {"items": items}


def check(ctx):
    res = collect(ctx)["items"]
    if not res:
        return [("ok", "读不到温度传感器（虚拟机或不支持的硬件）")]
    worst = max(res, key=lambda it: it["value"])
    text = "、".join(f"{it['label']} {it['value']}°C" for it in res)
    return [(worst["level"], f"温度：{text}")]
