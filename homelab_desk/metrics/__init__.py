"""按平台选择机器指标采样器。sample() 返回的字段两个平台一致。"""

from __future__ import annotations

import sys


def sampler():
    if sys.platform == "darwin":
        from .darwin import Sampler
    elif sys.platform.startswith("linux"):
        from .linux import Sampler
    else:
        raise RuntimeError(f"暂不支持 {sys.platform}")
    return Sampler()
