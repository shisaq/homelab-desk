"""插件：plugins/<名字>/plugin.py，按需实现 collect / check / notify。接口说明见 docs/plugins.md。"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from . import paths
from .config import Config, ConfigError

LEVELS = ("ok", "warn", "crit")


@dataclass
class Context:
    """传给插件函数的东西。"""

    name: str
    options: dict  # services.yaml 里给这个插件写的参数
    config: Config
    plugin_dir: Path  # 插件自己的目录（放随插件带的文件）
    data_dir: Path  # 插件可写的目录（缓存、编译产物等）

    def log(self, msg: str) -> None:
        print(f"[{self.name}] {msg}", file=sys.stderr)


@dataclass
class Plugin:
    name: str
    title: str
    module: ModuleType
    ctx: Context
    interval: float

    def has(self, fn: str) -> bool:
        return callable(getattr(self.module, fn, None))

    def call(self, fn: str, *args):
        return getattr(self.module, fn)(self.ctx, *args)


def find(name: str) -> Path | None:
    """同名插件：用户目录优先于仓库自带。"""
    for base in (paths.user_plugins(), paths.BUILTIN_PLUGINS):
        p = base / name / "plugin.py"
        if p.exists():
            return p
    return None


def available() -> list:
    names = set()
    for base in (paths.user_plugins(), paths.BUILTIN_PLUGINS):
        if base.is_dir():
            names.update(p.parent.name for p in base.glob("*/plugin.py"))
    return sorted(names)


def _import(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"homelab_desk_plugin_{name.replace('-', '_')}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load(cfg: Config, quiet: bool = False) -> list:
    """加载配置里启用的插件。不支持当前平台的跳过；插件本身导入出错只跳过它自己。"""
    out = []
    for ref in cfg.plugins:
        path = find(ref.name)
        if not path:
            raise ConfigError(f"找不到插件 `{ref.name}`（可用的有：{', '.join(available())}）")
        try:
            mod = _import(ref.name, path)
        except Exception:
            print(f"插件 {ref.name} 加载失败，已跳过：\n{traceback.format_exc()}", file=sys.stderr)
            continue
        platforms = getattr(mod, "PLATFORMS", None)
        plat = "darwin" if sys.platform == "darwin" else "linux"
        if platforms and plat not in platforms:
            if not quiet:
                print(f"插件 {ref.name} 不支持 {plat}，已跳过", file=sys.stderr)
            continue
        data = paths.data_dir() / "plugins" / ref.name
        data.mkdir(parents=True, exist_ok=True)
        ctx = Context(ref.name, ref.options, cfg, path.parent, data)
        out.append(Plugin(ref.name, getattr(mod, "TITLE", ref.name), mod, ctx, float(getattr(mod, "INTERVAL", 5))))
    return out


def normalize_items(raw) -> dict:
    """把 collect() 的返回整理成统一格式，顺便挡住写错的字段。"""
    if not isinstance(raw, dict):
        raise ValueError("collect() 应返回 {'items': [...]}")
    items = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict) or "label" not in it:
            continue
        level = it.get("level", "ok")
        items.append(
            {
                "label": str(it["label"]),
                "value": it.get("value"),
                "unit": str(it.get("unit", "")),
                "max": it.get("max"),
                "level": level if level in LEVELS else "ok",
            }
        )
    out = {"items": items}
    if raw.get("title"):
        out["title"] = str(raw["title"])
    return out
