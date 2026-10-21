"""增删服务：直接改 services.yaml 的文本（保留用户写的注释），改完重新校验，失败则不落盘。"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from . import config as cfgmod
from ._vendor import yaml

FIELDS = ("name", "title", "url", "health", "path", "icon", "color", "group", "desc", "read_timeout", "insecure_tls", "rewrite_host")


def _scalar(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    s = str(v)
    # 能不加引号就不加（读起来干净）；有歧义的用 JSON 字符串，它也是合法的 YAML
    if s == s.strip() and not re.search(r"""[:#{}\[\],&*!|>'"%@`]""", s) and yaml.safe_load(s) == s:
        return s
    return json.dumps(s, ensure_ascii=False)


def _services_span(lines: list) -> tuple:
    """返回 (services: 那一行的下标, 段落结束下标)。没有就返回 (-1, -1)。"""
    start = next((i for i, l in enumerate(lines) if re.match(r"^services\s*:", l)), -1)
    if start < 0:
        return -1, -1
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^[^\s#-]", lines[i]):
            end = i
            break
    return start, end


def _item_indent(lines: list, start: int, end: int) -> str:
    for l in lines[start + 1 : end]:
        m = re.match(r"^(\s*)- ", l)
        if m:
            return m.group(1)
    return "  "


def _write_checked(path: Path, text: str) -> cfgmod.Config:
    cfg = cfgmod.parse(yaml.safe_load(text), path)  # 校验不过会抛 ConfigError，文件不动
    shutil.copyfile(path, path.with_suffix(".yaml.bak"))
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return cfg


def add(path: Path, svc: dict) -> cfgmod.Config:
    text = path.read_text(encoding="utf-8")
    current = cfgmod.parse(yaml.safe_load(text), path)
    if any(s.name == svc["name"] for s in current.services):
        raise cfgmod.ConfigError(f"已经有叫 `{svc['name']}` 的服务了")
    lines = text.splitlines()
    start, end = _services_span(lines)
    if start < 0:
        lines += ["", "services:"]
        start, end = len(lines) - 1, len(lines)
    elif re.match(r"^services\s*:\s*\[\s*\]", lines[start]):
        lines[start] = "services:"
    ind = _item_indent(lines, start, end)
    block = []
    for k in FIELDS:
        if svc.get(k) in (None, "", False):
            continue
        prefix = f"{ind}- " if not block else f"{ind}  "
        block.append(f"{prefix}{k}: {_scalar(svc[k])}")
    # 插在段落最后一个非空行之后
    pos = end
    while pos - 1 > start and not lines[pos - 1].strip():
        pos -= 1
    lines[pos:pos] = block
    return _write_checked(path, "\n".join(lines) + "\n")


def remove(path: Path, name: str) -> cfgmod.Config:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start, end = _services_span(lines)
    pat = re.compile(rf"""^(\s*)- name:\s*["']?{re.escape(name)}["']?\s*(#.*)?$""")
    for i in range(start + 1, end if start >= 0 else 0):
        m = pat.match(lines[i])
        if not m:
            continue
        ind = m.group(1)
        j = i + 1
        while j < end:
            l = lines[j]
            if l.startswith(f"{ind}- ") or (l.strip() and not l.startswith(ind + " ")):
                break
            j += 1
        # 服务块上方紧贴的注释一起删
        k = i
        while k - 1 > start and lines[k - 1].strip().startswith("#") and lines[k - 1].startswith(ind):
            k -= 1
        del lines[k:j]
        return _write_checked(path, "\n".join(lines) + "\n")
    raise cfgmod.ConfigError(f"没有叫 `{name}` 的服务")
