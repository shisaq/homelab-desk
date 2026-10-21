# 写一个插件

插件给工作台加一张指标卡、给体检加几行检查，或者把体检报告发到别处。一个插件就是一个目录里的 `plugin.py`。

## 放在哪

| 位置 | 用途 |
| --- | --- |
| 仓库的 `plugins/<名字>/` | 随项目发布的通用插件 |
| `~/.config/homelab-desk/plugins/<名字>/` | 你自己的插件（微信、飞书通知这类本机专用的放这里）。同名时优先于仓库自带的 |

## 怎么开

`services.yaml`：

```yaml
plugins:
  - temps                     # 只写名字
  - name: power               # 带参数
    nut: ups@192.168.1.10
```

改完运行 `homelab-desk deploy`。

## 接口

`plugin.py` 里按需写下面的东西，都是可选的：

```python
TITLE = "温度"                  # 指标卡标题
PLATFORMS = ["darwin", "linux"] # 支持的平台，不符合的自动跳过
INTERVAL = 5                    # collect() 每隔几秒调用一次


def collect(ctx):
    """仪表盘指标。在 desk-agent 的后台线程里调用，出错只影响这张卡（卡上显示错误）。"""
    return {
        "items": [
            # label 必填；value 可以是数字或文字
            # 有 max 的画进度条；level 是 ok / warn / crit，决定颜色并在卡上标出「偏高」「严重」
            {"label": "CPU", "value": 48.5, "unit": "°C", "max": 110, "level": "ok"},
        ]
    }


def check(ctx):
    """每日体检。返回若干 (级别, 一行说明)。"""
    return [("ok", "温度：CPU 48.5°C")]


def notify(ctx, report, level):
    """体检结束后调用。report 是整份纯文本报告，level 是最严重的级别。出错就抛异常。"""
```

`ctx` 里有：

| 字段 | 内容 |
| --- | --- |
| `ctx.options` | `services.yaml` 里给这个插件写的参数（dict） |
| `ctx.config` | 整份配置（`homelab_desk.config.Config`） |
| `ctx.plugin_dir` | 插件自己的目录，放随插件带的文件 |
| `ctx.data_dir` | 插件可写的目录，放缓存等 |
| `ctx.log(msg)` | 写日志（desk-agent 日志） |

## 注意

- **不要依赖 root**。macOS 上的系统信息尽量用 `sysctl`、`ioreg`、IOKit（ctypes）这类普通用户可读的接口。
- **调用系统命令写绝对路径**（如 `/usr/sbin/ioreg`）：launchd 给的 PATH 很短。
- **macOS 的本地网络权限**：后台运行的 python3 第一次连局域网设备时，系统会弹「允许查找本地网络设备」，要点允许；没点或点了拒绝会报 `No route to host`，到「系统设置 › 隐私与安全性 › 本地网络」里打开 Python。连设备的 Tailscale 地址不受这个限制。HTTP 服务不用插件——直接加到 `services`，在线检测经 Caddy 走，不受影响。
- `collect()` 要快（几十毫秒以内）；慢的东西把 `INTERVAL` 调大。
- 只用 Python 标准库；需要第三方库的话，插件自己说明安装方法。

## 内置插件

| 名字 | 做什么 | 参数 |
| --- | --- | --- |
| `temps` | 温度（Apple 芯片 IOHID；Linux thermal / hwmon） | `warn`、`crit`（摄氏度） |
| `gpu` | GPU 占用与显存（macOS ioreg；Linux nvidia-smi） | — |
| `power` | UPS / 电池（macOS pmset；任意平台读 NUT，群晖的「UPS 网络服务器」就是 NUT） | `nut: ups@地址`、`low` |
| `notify-command` | 把体检报告从标准输入交给你的命令 | `command`、`only: warn` |
