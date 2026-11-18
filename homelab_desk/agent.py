"""desk-agent：只听 127.0.0.1，由 Caddy 把工作台的 /api/* 转过来。

后台线程定时做三件事，请求来了只读缓存：
- 机器指标，每 2 秒
- 各服务在线检测，每 10 秒：经本机 Caddy 走一遍（避开浏览器跨域误判，也绕开 macOS 本地网络限制，见 net.py）
- 插件 collect()，各按自己的 INTERVAL
在线检测的结果顺带交给 Alerter：服务连续离线 alert.after 分钟发一条通知，恢复后再发一条。
"""

from __future__ import annotations

import errno
import json
import queue
import socket
import sys
import threading
import time
import traceback
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, metrics, net, plugins
from .config import Config

SYSTEM_EVERY = 2
PROBE_EVERY = 10
SHORT = 150  # 短曲线：150 × 2 秒 = 5 分钟
LONG_STEP = 30  # 长曲线每点取 30 秒平均
LONG = 120  # 长曲线：120 × 30 秒 = 1 小时
PROBE_KEEP = 360  # 每个服务保留最近 1 小时（360 × 10 秒）的检测结果
TICKS = 30  # 服务卡片上的在线记录条：1 小时分 30 格，每格 2 分钟


def _explain(e: Exception) -> str:
    if sys.platform == "darwin" and isinstance(e, OSError) and e.errno == errno.EHOSTUNREACH:
        # 后台运行的 python3 默认没有「本地网络」权限，连局域网设备会这样（见 net.py）
        return "连不上局域网设备：在「系统设置 › 隐私与安全性 › 本地网络」里打开 Python，或改用设备的 Tailscale 地址"
    return f"{type(e).__name__}: {e}"[:200]


def _minutes(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    return f"{m} 分钟" if m < 60 else f"{m // 60} 小时" + (f" {m % 60} 分钟" if m % 60 else "")


class Alerter:
    """离线通知。只看状态变化：同一次故障只发一次「离线」，恢复时发一次「已恢复」。

    同一轮检测里一起出事的服务合成一条（比如 NAS 断电，上面几个服务一起离线）。
    发送放在单独的线程里排队，通知命令慢也不耽误检测。
    """

    BAD = ("down", "error")

    def __init__(self, cfg: Config, loaded: list):
        self.cfg = cfg
        self.after = cfg.alert_after * 60
        self.senders = [p for p in loaded if p.has("notify")]
        self.bad_since = {}  # 服务名 → 开始不正常的时间
        self.alerted = {}  # 服务名 → 已发过离线通知的那次故障的开始时间
        self.outbox = queue.Queue()
        if self.enabled:
            threading.Thread(target=self._send_loop, daemon=True).start()

    @property
    def enabled(self) -> bool:
        return self.after > 0 and bool(self.senders)

    def update(self, results: dict, now: float) -> None:
        if not self.enabled:
            return
        down, back = [], []
        for s in self.cfg.services:
            r = results.get(s.name)
            if r is None:
                continue
            if r["state"] in self.BAD:
                start = self.bad_since.setdefault(s.name, now)
                if s.name not in self.alerted and now - start >= self.after:
                    self.alerted[s.name] = start
                    why = r["error"] or f"返回 {r['code']}"
                    down.append(f"🔴 {s.title} 离线 {_minutes(now - start)}：{why}\n   {self.cfg.url(self.cfg.host(s))}")
            else:
                self.bad_since.pop(s.name, None)
                start = self.alerted.pop(s.name, None)
                if start is not None:
                    back.append(f"✅ {s.title} 已恢复，离线约 {_minutes(now - start)}")
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
        if down:
            self.outbox.put(("crit", "\n".join([f"# {self.cfg.title} · 服务离线 · {stamp}", *down])))
        if back:
            self.outbox.put(("ok", "\n".join([f"# {self.cfg.title} · 服务恢复 · {stamp}", *back])))

    def _send_loop(self):
        while True:
            level, text = self.outbox.get()
            for p in self.senders:
                try:
                    p.notify(text, level, "alert")
                except Exception as e:
                    p.ctx.log(f"离线通知没发出去：{e}")


class State:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.sampler = metrics.sampler()
        self.system = {}
        self.short = deque(maxlen=SHORT)
        self.long = deque(maxlen=LONG)
        self._bucket = []  # 正在累积的长曲线点
        self.services = {s.name: {"state": "unknown", "code": 0, "ms": None, "error": "", "since": time.time()} for s in cfg.services}
        self.probes = {s.name: deque(maxlen=PROBE_KEEP) for s in cfg.services}  # (时间, 状态, 毫秒)
        self.plugins = plugins.load(cfg)
        self.plugin_data = {p.name: {"title": p.title, "items": [], "error": ""} for p in self.plugins if p.has("collect")}
        self.alerter = Alerter(cfg, self.plugins)

    # ── 后台循环 ──
    def loop_system(self):
        while True:
            try:
                s = self.sampler.sample()
                point = {"t": int(time.time()), "cpu": s["cpu"], "mem": s["mem_used"], "rx": s["net_rx"], "tx": s["net_tx"]}
                with self.lock:
                    self.system = s
                    self.short.append(point)
                    self._bucket.append(point)
                    if point["t"] - self._bucket[0]["t"] >= LONG_STEP:
                        n = len(self._bucket)
                        avg = {k: round(sum(p[k] for p in self._bucket) / n, 1) for k in ("cpu", "mem", "rx", "tx")}
                        self.long.append({"t": point["t"], **avg})
                        self._bucket = []
            except Exception:
                traceback.print_exc()
            time.sleep(SYSTEM_EVERY)

    def loop_probe(self):
        pool = ThreadPoolExecutor(max_workers=8)
        while True:
            results = dict(
                zip([s.name for s in self.cfg.services], pool.map(lambda s: net.probe_via_caddy(self.cfg, s), self.cfg.services))
            )
            now = time.time()
            with self.lock:
                for name, r in results.items():
                    old = self.services[name]
                    r["since"] = old["since"] if old["state"] == r["state"] else now
                    self.services[name] = r
                    self.probes[name].append((now, r["state"], r["ms"]))
            self.alerter.update(results, now)
            time.sleep(PROBE_EVERY)

    def loop_plugin(self, p):
        while True:
            try:
                data = plugins.normalize_items(p.call("collect"))
                data.setdefault("title", p.title)
                data["error"] = ""
            except Exception as e:
                p.ctx.log(traceback.format_exc())
                data = {"title": p.title, "items": [], "error": _explain(e)}
            with self.lock:
                self.plugin_data[p.name] = data
            time.sleep(p.interval)

    def start(self):
        for target, args in [(self.loop_system, ()), (self.loop_probe, ())] + [
            (self.loop_plugin, (p,)) for p in self.plugins if p.has("collect")
        ]:
            threading.Thread(target=target, args=args, daemon=True).start()

    # ── 对外 ──
    @staticmethod
    def _ticks(probes, now: float) -> list:
        """近 1 小时分成 TICKS 格：每格取最差的状态和平均响应时间；还没数据的格子是 None。"""
        span = PROBE_KEEP * PROBE_EVERY / TICKS
        cells = [[] for _ in range(TICKS)]
        for t, state, ms in probes:
            i = TICKS - 1 - int((now - t) // span)
            if 0 <= i < TICKS:
                cells[i].append((state, ms))
        out = []
        rank = {"up": 0, "error": 1, "down": 2}
        for c in cells:
            if not c:
                out.append(None)
                continue
            worst = max((st for st, _ in c), key=lambda st: rank.get(st, 0))
            ms = [m for _, m in c if m is not None]
            out.append({"s": worst, "ms": round(sum(ms) / len(ms)) if ms else None})
        return out

    def snapshot(self, rng: str = "5m") -> dict:
        cfg = self.cfg
        now = time.time()
        with self.lock:
            services = []
            for s in cfg.services:
                r = self.services[s.name]
                hist = self.probes[s.name]
                ups = [st == "up" for _, st, _ in hist]
                services.append(
                    {
                        "name": s.name,
                        **r,
                        "uptime": round(100 * sum(ups) / len(ups), 1) if ups else None,
                        "ticks": self._ticks(hist, now),
                    }
                )
            return {
                "version": __version__,
                "now": int(time.time()),
                "host": {**self.sampler.static, "hostname": socket.gethostname().split(".")[0]},
                "system": dict(self.system),
                "range": "1h" if rng == "1h" else "5m",
                "history": list(self.long if rng == "1h" else self.short),
                "step": LONG_STEP if rng == "1h" else SYSTEM_EVERY,
                "services": services,
                "plugins": [{"name": n, **d} for n, d in self.plugin_data.items()],
            }


def make_handler(state: State):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"desk-agent/{__version__}"

        def _send(self, code: int, body: dict):
            data = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            if path == "/api/status":
                self._send(200, state.snapshot("1h" if "range=1h" in query else "5m"))
            elif path == "/api/health":
                self._send(200, {"ok": True, "version": __version__})
            else:
                self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            pass  # 每 3 秒一次请求，不记

    return Handler


def serve(cfg: Config) -> None:
    state = State(cfg)
    state.start()
    srv = ThreadingHTTPServer(("127.0.0.1", cfg.agent_port), make_handler(state))
    print(f"desk-agent {__version__} 在 127.0.0.1:{cfg.agent_port}，{len(cfg.services)} 个服务，插件：{', '.join(p.name for p in state.plugins) or '无'}", file=sys.stderr)
    a = state.alerter
    if a.enabled:
        print(f"离线通知：连续离线 {cfg.alert_after:g} 分钟后经 {', '.join(p.name for p in a.senders)} 发出", file=sys.stderr)
    else:
        print("离线通知：未开启（" + ("alert.after 为 0" if a.after <= 0 else "没有带 notify 的插件，如 notify-command") + "）", file=sys.stderr)
    srv.serve_forever()
