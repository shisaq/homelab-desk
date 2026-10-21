"""配置、生成物、日志放在哪。设了 HOMELAB_DESK_HOME 就全部放进那个目录（测试用）。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
WEB = REPO / "web"
BUILTIN_PLUGINS = REPO / "plugins"
CATALOG = REPO / "catalog" / "services.yaml"


def _home() -> Path | None:
    h = os.environ.get("HOMELAB_DESK_HOME")
    return Path(h).expanduser().resolve() if h else None


def config_dir() -> Path:
    h = _home()
    return h / "config" if h else Path.home() / ".config" / "homelab-desk"


def data_dir() -> Path:
    h = _home()
    return h / "data" if h else Path.home() / ".local" / "share" / "homelab-desk"


def log_dir() -> Path:
    h = _home()
    if h:
        return h / "logs"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Logs" / "homelab-desk"
    return Path.home() / ".local" / "state" / "homelab-desk"


def config_file() -> Path:
    return config_dir() / "services.yaml"


def user_plugins() -> Path:
    return config_dir() / "plugins"


def expand(p: str) -> Path:
    """配置里的路径：支持 ~，相对路径相对于配置目录。"""
    path = Path(os.path.expanduser(p))
    return path if path.is_absolute() else config_dir() / path
