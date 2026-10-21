# homelab-desk 设计（0.1）

## 已定的取舍

| 问题 | 决定 | 理由 |
| --- | --- | --- |
| 运行方式 | macOS 用 launchd，Linux 用 systemd；0.1 不做 Docker | Mac 上的 Docker 跑在虚拟机里，拿不到 Tailscale 地址、读不到宿主机 CPU/温度；项目本身只有 Caddy + 一个小服务 + 静态页，容器只会多一层。被管理的服务装在哪都行，项目只认「地址:端口」 |
| 仪表盘 | 自己写；图标用 simple-icons（CC0）和 dashboard-icons（Apache-2.0） | 同类项目要么只管页面、要么只管监控，拼起来太重；Homepage（GPL-3.0）、Glance（AGPL-3.0）的代码不能进 MIT 仓库 |
| 实现语言 | Python 3.9+，只用标准库；YAML 用随仓库带的 PyYAML（MIT，纯 Python） | macOS 装 Homebrew 时自带 python3，用户不用多装东西 |
| 访问方式 | 0.1 只做 Cloudflare DNS + Tailscale | 作者定 |

## 组成

```
services.yaml ──homelab-desk deploy──▶ Caddyfile ─▶ caddy（launchd/systemd）
                                     ▶ 工作台静态页（主题 + 服务卡片）
                                     ▶ desk-agent（只听 127.0.0.1）：机器指标 + 服务在线检测 → /api/*
                                     ▶ 每日体检定时任务
```

- **Caddy**：监听全部网卡，按来源 IP 只放行 Tailscale 网段与本机（macOS 普通用户只能在全部网卡上监听 443）；DNS 验证申请证书（地址只在 Tailscale 内，HTTP 验证不可能）；工作台用裸名，单独一张证书（泛解析证书不覆盖裸名）。
- **desk-agent**：Python 小服务，每几秒采样一次，浏览器只请求同域的 `/api/status`。服务在线检测由它在服务器上发起、经本机 Caddy 走一遍（连 127.0.0.1，SNI 用服务域名）：
  - 不经浏览器，避开 `Cross-Origin-Resource-Policy` 之类导致的跨域误判；
  - macOS 的「本地网络」隐私限制下，LaunchAgent 启动的 python3 默认连不上局域网设备（`No route to host`），而 Caddy 授权过就能连；
  - 顺带验证了证书和转发。
- **体检**：`homelab-desk check`，输出纯文本与退出码（0 正常 / 1 警告 / 2 严重），既检查后端，也经 Caddy 走一遍 HTTPS（证书、转发都验到）。

## 插件

核心只管「清单 → 转发 → 页面 → 检测」这条主线，其余能力都做成插件，按需开启。内置的温度、GPU、电源等也走同一套接口，保证接口好用。

**放在哪**：仓库自带的在 `plugins/<名字>/`，用户自己的放在 `~/.config/homelab-desk/plugins/<名字>/`（同名时用户的优先；微信、飞书通知这类本机专用的就放这里，不进开源核心）。

**怎么开**：`services.yaml` 里写

```yaml
plugins:
  - temps
  - gpu
  - name: notify-feishu        # 带参数的写法
    webhook_file: ~/.config/homelab-desk/feishu.webhook
```

**怎么写**：一个 `plugin.py`，按需实现下面几个函数（都可选）：

| 函数 | 何时调用 | 返回 |
| --- | --- | --- |
| `collect(ctx)` | desk-agent 每隔 `INTERVAL` 秒 | 一组仪表盘指标 `{"title": "温度", "items": [{"label": "CPU", "value": 48.5, "unit": "°C", "max": 100, "level": "ok"}]}` |
| `check(ctx)` | 每日体检 | `[("ok"/"warn"/"crit", "一行说明"), ...]` |
| `notify(ctx, report, level)` | 体检结束后 | 把报告发出去 |

另有模块级常量：`TITLE`、`PLATFORMS = ["darwin", "linux"]`（不符合的平台自动跳过）、`INTERVAL`（秒）。`ctx` 里有插件参数、整份配置、数据目录、日志函数。

前端不需要插件写任何 JS：`collect` 返回的统一格式由工作台画成一张指标卡（带 `max` 的画进度条，`level` 决定颜色）。插件出错只影响它自己那张卡，并显示原因。

**以后可以扩成插件的**：DNS 服务商（阿里云等）、访问方式（除 Tailscale 外的方案）、主题。0.1 先把这几处在代码里隔离成独立模块，接口稳定后再开放。

## 工作台页面

- 四套主题（glass / liquid / paper / terminal），每套都有深浅两种：主题文件里分别写 `:root[data-scheme="dark"]` 与 `[data-scheme="light"]` 两组变量。`index.html` 头部的小脚本在样式生效前定好 `data-scheme`（页面上手动选的 > 配置的 `appearance` > 系统设置），避免闪烁。
- 图表画法由主题的 CSS 变量 `--trend`（area / line / bars / blocks）和 `--gauge`（bar / ring / segments）决定，配置 `charts:` 或地址参数 `?trend=` `?gauge=` 可覆盖。图表代码在 `web/charts.js`，不依赖任何库。
- 分类色固定四个（蓝、橙、青绿、黄），按顺序分配、跟着含义走（内存的 App / 联动 / 压缩 / 缓存，网络的下载 / 上传），已在每套主题深浅两种底色上用色弱模拟校验；状态色（正常 / 警告 / 严重）只表示状态，并且总配文字。
- desk-agent 保留两段历史：每 2 秒一点的 5 分钟、每 30 秒一点的 1 小时；每个服务保留 1 小时检测结果，页面上画成 30 格的在线记录条。
- `?demo` 用 `web/demo.js` 的虚构数据渲染，README 截图由此生成。

## 配置与目录

| 内容 | 位置 |
| --- | --- |
| 清单 | `~/.config/homelab-desk/services.yaml`（仓库里只有 `examples/services.example.yaml`） |
| Cloudflare Token | `~/.config/homelab-desk/cloudflare.token`（权限 600） |
| 生成物（Caddyfile、页面、证书） | `~/.local/share/homelab-desk/` |
| 日志 | macOS `~/Library/Logs/homelab-desk/`，Linux `~/.local/state/homelab-desk/` |

环境变量 `HOMELAB_DESK_HOME` 可把以上三处一并指到别的目录（测试用）。

## 同一台机器跑第二套（开发测试）

给测试实例单独一个子域名（如 `test.desk.example.com` + `*.test.desk.example.com`），配置里设 `instance: test`（后台服务名带上它）、`listen` 换端口（https / http / admin）、`agent.port` 换端口，再用 `HOMELAB_DESK_HOME=$PWD/.local/test` 把配置与生成物放进仓库的 `.local/`（已 gitignore）。两套互不影响。
