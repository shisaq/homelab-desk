# homelab-desk

[English](README.en.md)

给家里那台常开的机器（首选 Mac mini，也支持 Linux）上的所有服务一个**工作台**：

- 每个服务一个好记的名字：`https://jellyfin.desk.example.com`
- 正规 HTTPS 证书，浏览器不报警，自动续期
- **只有自己的设备能打开**（Tailscale），不对公网开端口
- 实时仪表盘：每个服务在线与否、响应时间、近 1 小时记录；机器的 CPU（含每个核心）、内存构成与压力、磁盘、网络、温度、GPU、UPS
- 每天自动体检，报告可以接到你自己的通知渠道
- 由 Claude Code / Codex 的 skill 一句话搭好，加新服务也是一句话

![液态玻璃主题（深色）](docs/screenshots/liquid-dark.jpg)

<table><tr>
<td><img src="docs/screenshots/glass-dark.jpg" alt="玻璃"><br>glass 玻璃</td>
<td><img src="docs/screenshots/liquid-light.jpg" alt="液态玻璃（浅色）"><br>liquid 液态玻璃 · 浅色</td>
</tr><tr>
<td><img src="docs/screenshots/paper-light.jpg" alt="简洁"><br>paper 简洁</td>
<td><img src="docs/screenshots/terminal-dark.jpg" alt="终端"><br>terminal 终端</td>
</tr></table>

<table><tr>
<td><img src="docs/screenshots/detail.jpg" alt="详细指标"><br>详细指标（点「详情」或按 D）</td>
<td width="30%"><img src="docs/screenshots/mobile.jpg" alt="手机"><br>手机</td>
</tr></table>

整页一屏放下，不用滚动：上面一条是机器指标，下面是服务，服务越多排得越紧凑。截图是演示数据：任意部署后的工作台地址加 `?demo` 就能看到。

```
手机 / 笔记本 ──Tailscale──▶ desk.example.com ──▶ Caddy（证书、只放行 Tailscale）──▶ 各个服务
                                                  └─▶ 工作台页面 + 指标服务（desk-agent）
```

## 和同类项目的区别

Homepage、Homarr、Dashy、Glance 管的是「页面」，Uptime Kuma 管「监控」，Beszel、Glances 管「指标」。homelab-desk 管的是**从零到能用的整条链路**：域名、证书、只有自己能访问、页面、监控、体检，并且让 AI 一步步带你做完。已经在用上面这些的，也可以把它们当作一个服务加进来。

## 开始

### 让 AI 来（推荐）

```bash
git clone https://github.com/shisaq/homelab-desk ~/homelab-desk
# Claude Code
ln -s ~/homelab-desk/skill ~/.claude/skills/homelab-desk
# Codex
ln -s ~/homelab-desk/skill ~/.codex/skills/homelab-desk
```

然后对 Claude Code 或 Codex 说：「帮我用 homelab-desk 搭一个家庭服务器工作台」。它会检查前置条件、带你在 Cloudflare 建 Token、建 DNS 记录、部署、验收。

### 手动

需要：一个托管在 Cloudflare 的域名、装好并登录的 Tailscale、Python 3.9+（macOS 装过 Homebrew 就有）。

```bash
cd ~/homelab-desk
# 先复制好 Cloudflare Token（模板「Edit zone DNS」+ Zone·Zone·Read，只选这个域名）
pbpaste | bin/homelab-desk init --domain desk.example.com --email you@example.com --token-stdin
bin/homelab-desk doctor      # 检查前置条件
bin/homelab-desk dns         # 建 desk.example.com 和 *.desk.example.com 两条记录（灰云）
bin/homelab-desk deploy      # 生成并启用一切，可重复执行
```

打开 `https://desk.example.com`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `init` | 生成 `~/.config/homelab-desk/services.yaml` |
| `doctor` | 检查 Tailscale、Token、DNS 记录、端口、本机代理 |
| `dns` | 在 Cloudflare 建 / 修正两条 A 记录，一律灰云 |
| `deploy` | 生成 Caddy 配置和工作台页面，装成后台服务（macOS launchd / Linux systemd），等证书就绪 |
| `add <名字> [地址:端口]` | 加服务并部署；目录里有的服务不用写地址 |
| `remove <名字>` | 删服务并部署 |
| `list` / `status` | 列出服务 / 后台服务状态 |
| `catalog [关键词]` | 推荐服务目录 |
| `check [--notify]` | 体检；退出码 0 正常、1 警告、2 严重 |
| `uninstall` | 移除后台服务（保留配置和证书） |

## 配置

全部在一个 `services.yaml` 里，见 [examples/services.example.yaml](examples/services.example.yaml)。改完运行 `deploy`。

- **主题**：`glass`（玻璃）/ `liquid`（苹果液态玻璃）/ `paper`（简洁）/ `terminal`（终端），每套都有深浅两种。`appearance: auto / light / dark` 指定深浅，页面右上角也能随手切换。地址后加 `?theme=liquid` 可以先预览。
- **布局**：桌面上整页一屏放下。指标缩成一条，大图表收在「详情」浮层里（按 `D`，或地址加 `?detail`）；服务按分组排成列表，按屏幕高度在四档疏密间自动选择。
- **图表**：每套主题有自己的画法——面积曲线、柱状、字符块；进度条、圆环、分段格。`charts:` 里可以单独换。仪表盘上有 CPU 每个核心、内存构成、下载与上传两条线、每个服务近 1 小时的在线记录，曲线可在 5 分钟 / 1 小时之间切换。
- **插件**：温度、GPU、UPS / 电池、体检通知，也可以自己写。见 [docs/plugins.md](docs/plugins.md)。
- **推荐服务**：影音、相册、密码、广告过滤、自动化、智能家居、AI、文件同步、笔记、开发、监控、下载、阅读，共 30 项，每项写明适合谁、怎么装、放到反向代理后要注意什么。见 [catalog/services.yaml](catalog/services.yaml)。

## 为什么这样设计

- **原生运行，不用 Docker**：Mac 上的 Docker 跑在虚拟机里，看不到 Tailscale 地址、读不到宿主机温度和 GPU。项目本身只有 Caddy、一个 Python 小服务和一个静态页面。你的服务装在哪都行，Docker 里的也一样，项目只认「地址:端口」。
- **证书走 DNS 验证**：服务地址只在 Tailscale 里，Let's Encrypt 从外面访问不到，HTTP 验证行不通。
- **按来源 IP 只放行 Tailscale**：macOS 普通用户只能在全部网卡上监听 443，局域网设备也连得上，所以由 Caddy 按来源 IP 拦下。
- **在线检测在服务器上做**：浏览器直接跨域探测会被部分服务的安全头误判为离线；在服务器上经 Caddy 测，还顺带验证了证书和转发。

设计细节见 [docs/design.md](docs/design.md)。

## 平台

- macOS 13+（在 Apple 芯片 Mac mini 上开发和日常使用）
- Linux（systemd）：代码已写好，尚未在真机上验证，欢迎反馈

## 许可证

MIT，见 [LICENSE](LICENSE)。第三方代码与图标见 [NOTICE.md](NOTICE.md)。
