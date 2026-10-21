"""推荐服务目录（catalog/services.yaml）。"""

from __future__ import annotations

from . import paths
from ._vendor import yaml

# 目录里的这些字段可以直接作为服务配置的默认值
DEFAULTS = ("title", "desc", "health", "icon", "color", "path", "rewrite_host")


def load() -> list:
    return yaml.safe_load(paths.CATALOG.read_text(encoding="utf-8"))["categories"]


def find(name: str) -> dict | None:
    """按名字找，返回带 category 字段的条目。"""
    for cat in load():
        for s in cat["services"]:
            if s["name"] == name:
                return {**s, "category": cat["name"]}
    return None


def search(query: str = "") -> list:
    """[(分类名, [条目...])]；按名字、标题、说明、适合谁、分类匹配关键词。"""
    q = query.lower()
    out = []
    for cat in load():
        hits = [
            s
            for s in cat["services"]
            if not q or any(q in str(s.get(k, "")).lower() for k in ("name", "title", "desc", "for")) or q in cat["name"].lower()
        ]
        if hits:
            out.append((cat["name"], hits))
    return out
