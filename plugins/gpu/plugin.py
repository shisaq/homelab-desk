"""GPU 占用。macOS：ioreg 的 IOAccelerator（Apple 芯片与独显都有）；Linux：有 nvidia-smi 时读 NVIDIA 显卡。"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

TITLE = "GPU"
PLATFORMS = ["darwin", "linux"]
INTERVAL = 3


def _darwin():
    out = subprocess.run(
        ["/usr/sbin/ioreg", "-r", "-d", "1", "-w", "0", "-c", "IOAccelerator"],
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout
    items = []
    # 每块 GPU 一段 PerformanceStatistics
    for i, block in enumerate(re.findall(r'"PerformanceStatistics" = \{(.*?)\}', out)):
        util = re.search(r'"Device Utilization %"=(\d+)', block)
        mem = re.search(r'"In use system memory"=(\d+)', block)
        suffix = f" {i + 1}" if i else ""
        if util:
            v = int(util.group(1))
            items.append({"label": f"占用{suffix}", "value": v, "unit": "%", "max": 100})
        if mem:
            gb = round(int(mem.group(1)) / 2**30, 1)
            items.append({"label": f"显存{suffix}", "value": gb, "unit": "GB"})
    return items


def _linux():
    if not shutil.which("nvidia-smi"):
        return []
    out = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,memory.used,memory.total",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout
    items = []
    for i, line in enumerate(out.strip().splitlines()):
        util, used, total = (float(x) for x in line.split(","))
        suffix = f" {i + 1}" if i else ""
        items.append({"label": f"占用{suffix}", "value": util, "unit": "%", "max": 100})
        items.append({"label": f"显存{suffix}", "value": round(used / 1024, 1), "unit": "GB", "max": round(total / 1024, 1)})
    return items


def collect(ctx):
    return {"items": _darwin() if sys.platform == "darwin" else _linux()}
