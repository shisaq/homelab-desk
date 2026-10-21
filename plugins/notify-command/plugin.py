"""体检通知：把体检报告交给你自己的命令（从标准输入读），由它发到微信、飞书、邮件等。

参数：
  command: ~/.config/homelab-desk/send-feishu.sh   # 必填；收到的环境变量 HOMELAB_DESK_LEVEL = ok / warn / crit
  only: warn                                       # 可选：只在有警告（warn）或严重（crit）时才发，默认每天都发
"""

from __future__ import annotations

import os
import shlex
import subprocess

TITLE = "命令通知"
LEVELS = ("ok", "warn", "crit")


def notify(ctx, report, level):
    cmd = ctx.options.get("command")
    if not cmd:
        raise ValueError("notify-command 需要 command 参数")
    only = ctx.options.get("only", "ok")
    if LEVELS.index(level) < LEVELS.index(only if only in LEVELS else "ok"):
        return
    args = [os.path.expanduser(a) for a in shlex.split(str(cmd))]
    r = subprocess.run(
        args, input=report, text=True, capture_output=True, timeout=60, env={**os.environ, "HOMELAB_DESK_LEVEL": level}
    )
    if r.returncode != 0:
        raise RuntimeError(f"命令退出码 {r.returncode}：{r.stderr.strip()[:200]}")
