---
name: homelab-desk
description: 搭建和维护家庭服务器工作台（homelab-desk）：给家里常开机器（Mac mini / Linux）上的服务配好记的域名、正规 HTTPS、只有自己设备能访问（Tailscale）、实时仪表盘和每日体检。用户说「搭家庭服务器入口 / 给服务配域名 / 加一个服务 / 我想要 X（相册、密码管理、广告过滤…）/ 工作台打不开 / 体检报错」时使用。
---

# homelab-desk

你在帮用户搭建或维护 homelab-desk。所有操作都通过仓库里的 `bin/homelab-desk` 命令完成；这份说明告诉你按什么顺序做、每一步怎么验收、出了问题去哪查。

用户可能完全不懂网络。每一步先用一两句大白话说清楚要做什么、为什么，再动手；需要用户自己点网页的地方，给出具体的菜单路径。

## 不能做的事

- **不要让用户把 Cloudflare Token 贴进对话。** 让用户复制 Token 后自己在终端运行 `pbpaste | homelab-desk init ... --token-stdin`（Linux 用 `xclip -o` 或手动写文件），Token 就不会出现在对话记录里。
- 结束进程只用 `launchctl kickstart -k` / `launchctl bootout`（Linux 用 `systemctl --user`），**不要 `pkill -f` 或按名字杀进程**。
- 不切换、不注销图形会话。
- 用户机器上已经在跑的反向代理（别的 Caddy、nginx）不要动；端口冲突时先问用户。

## 第一次搭建

### 0. 了解情况

问清楚（已经知道的不要再问）：
1. 跑服务的机器是 Mac 还是 Linux？`homelab-desk` 装在这台机器上。
2. 有没有自己的域名？在不在 Cloudflare 上？
3. 装没装 Tailscale？
4. 想用哪个地址当工作台？推荐 `desk.<域名>` 或 `home.<域名>`；服务地址会是 `<名字>.desk.<域名>`。

### 1. Tailscale

- 没装：macOS 从 App Store 或 https://tailscale.com/download 安装并登录；Linux 用 `curl -fsSL https://tailscale.com/install.sh | sh && sudo tailscale up`。
- 手机、笔记本这些要访问工作台的设备也装上，用同一个账号登录。
- **在 https://login.tailscale.com/admin/machines 里对这台服务器点「⋯ › Disable key expiry」**。不点的话节点密钥默认 180 天过期，到期那天人在外面就连不上了。
- 验收：`tailscale ip -4` 能输出 `100.x.x.x`。

### 2. 域名放到 Cloudflare

已经在 Cloudflare 上的跳过这一步。

迁移 nameserver 前**必须**提醒用户：
- Cloudflare 自动导入会漏记录（尤其是 CNAME），而且导入的记录默认开橙色云代理。**切 nameserver 之前**，把旧 DNS 服务商的记录逐条和 Cloudflare 里的比对，缺的补上。可以用 `dig +short @<Cloudflare 分配的 nameserver> <记录名>` 逐条问新 nameserver。
- 改 nameserver 后最长 48 小时内，部分网络仍会去问旧的 nameserver，新子域会时好时坏地报「不存在」。这是正常的，等就行；自查用 `dig +short @<Cloudflare nameserver> <域名>`。

### 3. Cloudflare Token

让用户在 https://dash.cloudflare.com/profile/api-tokens 点「Create Token」：
1. 模板选「Edit zone DNS」；
2. 权限里**再加一行 Zone · Zone · Read**（不加的话找不到域名）；
3. Zone Resources 只选这一个域名；
4. TTL（有效期）可以不设。

### 4. 安装与配置

```bash
git clone https://github.com/shisaq/homelab-desk ~/homelab-desk
cd ~/homelab-desk
# 用户复制好 Token 后，在终端里运行（Token 不进对话）：
pbpaste | bin/homelab-desk init --domain desk.example.com --email 用户邮箱 --token-stdin
bin/homelab-desk doctor
```

`doctor` 有 ❌ 先解决。⚠️ 里的代理提示要转告用户（见下文「本机代理」）。

可以建议用户把 `~/homelab-desk/bin` 加进 PATH，以后直接敲 `homelab-desk`。

### 5. DNS 记录

```bash
bin/homelab-desk dns
```

它在 Cloudflare 上建两条指向本机 Tailscale 地址的 A 记录：`desk.example.com` 和 `*.desk.example.com`，一律灰云（DNS only）。泛解析不包含裸名本身，所以要两条。

### 6. 部署

```bash
bin/homelab-desk deploy
```

可以重复执行。第一次会下载带 cloudflare 插件的 Caddy（约 1 分钟），再申请证书（约 1 分钟）。

**macOS 上第一次部署后要提醒用户看一眼屏幕**：可能会弹出「"caddy" 想要查找本地网络上的设备」和「"python3" 想要查找……」，都点**允许**。点了拒绝的话，到「系统设置 › 隐私与安全性 › 本地网络」里打开。不允许就访问不了局域网里的其他设备（NAS、树莓派）。

### 7. 验收

1. 在一台装了 Tailscale 的手机上打开 `https://desk.example.com`，能看到工作台、机器指标在跳。
2. 关掉手机的 Tailscale 再打开同一地址：应该打不开。这正是「只有自己能访问」。
3. `bin/homelab-desk check` 全绿。

## 加服务

用户说「我想要 X」或「我装了 X」：

1. 先查目录：`bin/homelab-desk catalog <关键词>`（如「相册」「密码」「广告」）。有多个候选时，按目录里的「适合谁」给出推荐和理由，让用户选。
2. 还没装的，按目录里的 install 帮用户装。`native: false` 的需要 Docker，Mac 上推荐 OrbStack。
3. 加入：
   - 目录里有的：`bin/homelab-desk add <名字>`，不写地址时默认 `127.0.0.1:<默认端口>`；端口改过就写上，如 `add immich 127.0.0.1:2284`。
   - 目录里没有的：`bin/homelab-desk add <名字> <地址:端口> --title ... --icon ... --health ...`。图标名可以在 simpleicons.org 或 dashboardicons.com 查到。
   - 在别的设备上的服务（NAS、树莓派）：地址写那台设备的局域网 IP 或 Tailscale IP。
4. `add` 输出的「注意」一条都别漏，逐条帮用户落实（Home Assistant 的 trusted_proxies、各服务的「外部地址」设置等）。
5. 验收：工作台上新卡片显示「在线」，点开能用。

删服务：`bin/homelab-desk remove <名字>`。

## 改外观、开插件

- 主题：`services.yaml` 里 `theme:` 改成 `glass`（玻璃）/ `liquid`（苹果液态玻璃）/ `paper`（简洁）/ `terminal`（终端），然后 `deploy`。想先看看效果，在地址后面加 `?theme=liquid` 预览。
- 深浅色：`appearance: auto / light / dark`；用户也可以点页面右上角的按钮临时切换（记在浏览器里）。
- 图表画法：`charts:` 下 `trend: area / line / bars / blocks`、`gauge: bar / ring / segments`；不写就用主题自带的。
- 插件：`plugins:` 下加 `temps`、`gpu`、`power` 等，说明见 `docs/plugins.md`。UPS 接在群晖上的，用 `power` 插件的 `nut: ups@NAS的IP`，并在群晖「UPS › 允许的 DiskStation 设备」里加上这台机器的 IP。
- 体检通知：用 `notify-command` 插件把报告交给用户自己的脚本（发微信、飞书、邮件由那个脚本负责）。

## 排错

| 现象 | 原因与处理 |
| --- | --- |
| 浏览器 502，但 `check` 全绿 | 本机代理软件（Clash 等）截走了请求。在代理软件的绕过 / 直连规则里加 `desk.example.com` 和 `*.desk.example.com`；用 fake-ip 模式的还要加进 fake-ip 过滤列表 |
| 时好时坏地「找不到服务器」 | 刚改 nameserver，等最长 48 小时；用 `dig +short @<Cloudflare nameserver> <域名>` 自查 |
| 证书一直申请不下来 | 看 `homelab-desk status` 列出的日志目录里的 `caddy.log`。多半是 Token 少了 Zone · Zone · Read，或 Token 只授权了别的域名 |
| Home Assistant 返回 400 | `configuration.yaml` 的 http 段加 `use_x_forwarded_for: true` 和 `trusted_proxies`（Caddy 所在机器的 IP），然后**完整重启**，快速重载不生效 |
| 某服务 403「Host check error」或类似 | 服务只认 localhost：`services.yaml` 里给它加 `rewrite_host: true` 再 `deploy` |
| 局域网设备的卡片离线，`curl` 却能通 | macOS 本地网络权限没给，见第 6 步 |
| 工作台打得开，指标不动 | `homelab-desk status` 看指标服务；日志在同一目录的 `agent.log` |
| 端口 80/443 被占用 | `doctor` 会报。查是哪个程序：`sudo lsof -nP -iTCP:443 -sTCP:LISTEN`。别直接杀，先问用户 |

macOS 普通用户只能在「全部网卡」上监听 80/443，局域网里的设备也能连上来。项目在 Caddy 里按来源 IP 只放行 Tailscale 网段和本机，其余直接断开。**不要删这条过滤**：转发后后端看到的来源是 127.0.0.1，删了会绕过后端自己的访问限制。

## Linux 额外步骤

- 普通用户默认不能监听 80/443。二选一：`sudo setcap cap_net_bind_service=+ep <caddy 路径>`（`doctor` 里能看到路径），或 `sudo sysctl -w net.ipv4.ip_unprivileged_port_start=80`（要持久化就写进 `/etc/sysctl.d/`）。
- 让用户服务在没登录时也运行：`sudo loginctl enable-linger $USER`。
