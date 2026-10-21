// 演示模式：地址后加 ?demo，用虚构的数据展示工作台（README 截图用）。不请求任何接口。
'use strict';

(() => {
  const SI = n => ({ src: `https://cdn.jsdelivr.net/npm/simple-icons@16/icons/${n}.svg`, mono: true });
  const DI = n => ({ src: `https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/${n}.svg`, mono: false });
  const svc = (name, title, desc, group, color, icon) =>
    ({ name, title, desc, group, color, icon, host: `${name}.desk.example.com`, href: '#' });

  const config = {
    title: '家里的 Mac mini',
    subtitle: '家里的服务都在这里 · 只有自己的设备能打开',
    version: '0.1.0',
    charts: {},
    services: [
      svc('jellyfin', 'Jellyfin', '电影与剧集', '影音与相册', '#00A4DC', SI('jellyfin')),
      svc('immich', 'Immich', '手机照片自动备份', '影音与相册', '#4250AF', SI('immich')),
      svc('vault', 'Vaultwarden', '全家的密码库', '日常', '#175DDC', SI('vaultwarden')),
      svc('adguard', 'AdGuard Home', '全家设备去广告', '日常', '#68BC71', SI('adguard')),
      svc('sync', 'Syncthing', '电脑与手机同步文件', '日常', '#0891D1', SI('syncthing')),
      svc('ollama', 'Ollama', '本地大模型接口', 'AI', '#7C5CFF', SI('ollama')),
      svc('chat', 'Open WebUI', '家里人的 AI 聊天', 'AI', '#3A3F4B', DI('open-webui')),
      svc('ha', 'Home Assistant', '智能家居 · 树莓派', '家里其他设备', '#18BCF2', SI('homeassistant')),
      svc('n8n', 'n8n', '自动化流程 · NAS', '家里其他设备', '#EA4B71', SI('n8n')),
    ],
    shortcuts: [{ title: 'SSH · 屏幕共享', value: 'home-mini', icon: SI('tailscale') }],
  };

  // 可复现的伪随机：同一时刻画出来的图一样
  function noise(seed) {
    let x = Math.sin(seed * 12.9898) * 43758.5453;
    return x - Math.floor(x);
  }
  // 平滑起伏：几条正弦叠加，再加一点噪声和偶发的尖峰
  function wave(t, base, amp, seed, spikes = 0) {
    const v = base
      + amp * 0.6 * Math.sin(t / 47 + seed)
      + amp * 0.3 * Math.sin(t / 13 + seed * 2)
      + amp * 0.25 * (noise(Math.floor(t / 2) + seed) - 0.5);
    const spike = spikes && noise(Math.floor(t / 6) * 7 + seed) > 1 - spikes ? amp * 1.2 * noise(t + seed) : 0;
    return Math.max(0, v + spike);
  }

  const GB = 2 ** 30;
  const start = Math.floor(Date.now() / 1000);

  function history(range, now) {
    const step = range === '1h' ? 30 : 2, n = range === '1h' ? 120 : 150;
    return Array.from({ length: n }, (_, i) => {
      const t = now - (n - 1 - i) * step;
      return {
        t,
        cpu: Math.min(100, wave(t, 14, 10, 1, 0.08)),
        mem: (21 + 2.5 * Math.sin(t / 900) + 0.6 * noise(Math.floor(t / 20))) * GB,
        rx: wave(t, 420e3, 380e3, 3, 0.04),
        tx: wave(t, 160e3, 140e3, 5, 0.04),
      };
    });
  }

  function ticks(name, now) {
    return Array.from({ length: 30 }, (_, i) => {
      // n8n 半小时前重启过一次
      if (name === 'n8n' && (i === 14 || i === 15)) return { s: i === 14 ? 'down' : 'error', ms: null };
      return { s: 'up', ms: Math.round(8 + 30 * noise(i + name.length + now / 600)) };
    });
  }

  function status(range) {
    const now = Math.floor(Date.now() / 1000);
    const hist = history(range, now);
    const cur = hist[hist.length - 1];
    const cores = Array.from({ length: 10 }, (_, i) => Math.min(100, wave(now, i < 4 ? 30 : 8, i < 4 ? 25 : 8, i * 3)));
    return {
      version: config.version,
      now,
      range,
      step: range === '1h' ? 30 : 2,
      host: { os: 'macOS 26.1', model: 'Mac mini', cpu: 'Apple M4', cores: 10, mem_total: 32 * GB, hostname: 'home-mini' },
      system: {
        cpu: cur.cpu, cores, load: [2.1, 1.8, 1.6],
        mem_used: cur.mem, mem_total: 32 * GB, mem_pressure: 'ok', mem_free_pct: 71, swap_used: 0,
        mem_parts: [
          { key: 'app', label: 'App', bytes: cur.mem - 6.4 * GB },
          { key: 'wired', label: '联动', bytes: 3.9 * GB },
          { key: 'compressed', label: '压缩', bytes: 2.5 * GB },
          { key: 'cache', label: '缓存', bytes: 7.2 * GB },
        ],
        disk_used: 612 * GB, disk_total: 994 * GB,
        net_rx: cur.rx, net_tx: cur.tx, uptime: 12 * 86400 + 5 * 3600 + (now - start),
      },
      history: hist,
      services: config.services.map((s, i) => ({
        name: s.name, state: 'up', code: 200, ms: Math.round(6 + 40 * noise(i + now / 3)), error: '',
        since: now - 3600 * (i + 2), uptime: s.name === 'n8n' ? 93.3 : 100, ticks: ticks(s.name, now),
      })),
      plugins: [
        { name: 'temps', title: '温度', error: '', items: [
          { label: 'CPU', value: +(46 + cur.cpu * 0.3).toFixed(1), unit: '°C', max: 110, level: 'ok' },
          { label: 'SSD', value: 41, unit: '°C', max: 110, level: 'ok' } ] },
        { name: 'gpu', title: 'GPU', error: '', items: [
          { label: '占用', value: Math.round(wave(now, 22, 15, 9)), unit: '%', max: 100, level: 'ok' },
          { label: '显存', value: 3.4, unit: 'GB', max: null, level: 'ok' } ] },
        { name: 'power', title: '电源', error: '', items: [
          { label: 'UPS 电量', value: 100, unit: '%', max: 100, level: 'ok' },
          { label: 'UPS 状态', value: '市电', unit: '', max: null, level: 'ok' },
          { label: '可撑', value: 48, unit: '分钟', max: null, level: 'ok' },
          { label: '负载', value: 14, unit: '%', max: 100, level: 'ok' } ] },
      ],
    };
  }

  window.DeskDemo = { config, status: async range => status(range) };
})();
