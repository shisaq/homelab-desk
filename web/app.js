// homelab-desk 工作台：config.json 画出卡片（指标服务挂了也能用），/api/status 每 3 秒刷新状态与指标。
'use strict';

const REFRESH_MS = 3000;
const $ = (s, el = document) => el.querySelector(s);
const h = Charts.el;
const root = document.documentElement;
const query = new URLSearchParams(location.search);

// ── 数字格式 ──
function bytes(n, digits = 1) {
  if (n == null) return '–';
  const u = ['B', 'KB', 'MB', 'GB', 'TB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i === 0 ? 0 : n >= 100 ? 0 : digits)} ${u[i]}`;
}
const rate = n => bytes(n) + '/s';
const pct = v => `${v < 10 ? v.toFixed(1) : Math.round(v)}%`;
function duration(sec) {
  const d = Math.floor(sec / 86400), hh = Math.floor(sec % 86400 / 3600), m = Math.floor(sec % 3600 / 60);
  if (d) return `${d} 天 ${hh} 小时`;
  if (hh) return `${hh} 小时 ${m} 分`;
  return `${m} 分钟`;
}
const clockOf = (t, seconds) => new Date(t * 1000).toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit', second: seconds ? '2-digit' : undefined });
const LEVEL_TEXT = { ok: '正常', warn: '偏高', crit: '严重' };
const PRESSURE_TEXT = { ok: '正常', warn: '警告', crit: '严重' };
const SERIES = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)'];

// ── 深浅色：自动 → 浅 → 深，记在浏览器里 ──
const SCHEME_LABEL = { auto: '自动', light: '浅色', dark: '深色' };
const media = matchMedia('(prefers-color-scheme: dark)');
function applyScheme() {
  const a = root.dataset.appearance;
  root.dataset.scheme = a === 'dark' || (a === 'auto' && media.matches) ? 'dark' : 'light';
  const btn = $('#scheme');
  btn.textContent = { auto: '◐', light: '☀︎', dark: '☾' }[a];
  btn.title = `深浅色：${SCHEME_LABEL[a]}（点击切换）`;
  btn.setAttribute('aria-label', btn.title);
}
$('#scheme').addEventListener('click', () => {
  const order = ['auto', 'light', 'dark'];
  root.dataset.appearance = order[(order.indexOf(root.dataset.appearance) + 1) % 3];
  localStorage.setItem('homelab-desk.appearance', root.dataset.appearance);
  applyScheme();
  if (last) render(last);
});
media.addEventListener('change', () => { applyScheme(); if (last) render(last); });

// 图表画法：?trend= > 配置 > 主题默认（CSS 变量）
function chartKinds() {
  const css = getComputedStyle(root);
  return {
    trend: query.get('trend') || config.charts?.trend || css.getPropertyValue('--trend').trim() || 'area',
    gauge: query.get('gauge') || config.charts?.gauge || css.getPropertyValue('--gauge').trim() || 'bar',
  };
}

// ── 图标 ──
function icon(ic, fallbackText) {
  if (ic && ic.mono) return h('div', { class: 'icon' }, h('i', { style: `--m:url(${ic.src})` }));
  if (ic) return h('div', { class: 'icon color' }, h('img', { src: ic.src, alt: '' }));
  return h('div', { class: 'icon letter' }, h('b', {}, (fallbackText || '?').slice(0, 1).toUpperCase()));
}

// ── 服务卡片 ──
let config = null;
const cards = new Map();

function renderServices() {
  const groups = new Map();
  for (const s of config.services) {
    const g = s.group || '服务';
    if (!groups.has(g)) groups.set(g, []);
    groups.get(g).push(s);
  }
  panels = [];
  let n = 0;
  for (const [name, list] of groups) {
    const rows = list.map(s => {
      n++;
      const row = h('a', { class: 'svc', href: s.href, style: `--c:${s.color}`, 'data-key': n <= 9 ? n : null },
        icon(s.icon, s.title),
        h('span', { class: 'svc-text' },
          h('span', { class: 'name' }, s.title),
          h('span', { class: 'desc' }, s.desc || s.host)),
        h('span', { class: 'ticks-slot' }),
        h('span', { class: 'status' }, h('span', { class: 'ms' }), h('span', { class: 'state' }), h('span', { class: 'dot' })));
      cards.set(s.name, row);
      return row;
    });
    panels.push(h('section', { class: 'panel' }, h('h2', {}, name), h('div', { class: 'rows' }, rows)));
  }
}

// 服务区按可用高度选疏密：先试最大，放不下再一档档缩小（服务少时大而好点，多时紧凑也能一屏放下）。
// 分组面板按「瀑布流」排：列数由宽度决定，每个面板放进当前最矮的一列。
const DENSITIES = [['l', 380], ['m', 320], ['s', 270], ['xs', 220]]; // 档位, 每列最小宽度
let panels = [];
function layoutColumns(minWidth) {
  const box = $('#groups');
  const gap = 14;
  const n = Math.max(1, Math.floor((box.clientWidth + gap) / (minWidth + gap)));
  const cols = Array.from({ length: Math.min(n, panels.length || 1) }, () => h('div', { class: 'col' }));
  box.replaceChildren(...cols);
  for (const p of panels) {
    cols.reduce((a, b) => (b.offsetHeight < a.offsetHeight ? b : a)).append(p);
  }
}
function fitServices() {
  const box = $('#groups');
  const mobile = matchMedia('(max-width: 700px)').matches;
  for (const [d, w] of mobile ? [['s', 0]] : DENSITIES) {
    box.dataset.density = d;
    layoutColumns(mobile ? 1e6 : w);
    if (mobile || box.scrollHeight <= box.clientHeight + 1) return;
  }
}
let fitTimer = null;
window.addEventListener('resize', () => { clearTimeout(fitTimer); fitTimer = setTimeout(fitServices, 100); });

function renderShortcuts() {
  if (!config.shortcuts.length) return;
  const box = $('#shortcuts');
  for (const sc of config.shortcuts) {
    const btn = h('button', { class: 'copy', type: 'button' }, '复制');
    btn.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(sc.value); } catch { return; }
      btn.textContent = '已复制'; btn.classList.add('done');
      setTimeout(() => { btn.textContent = '复制'; btn.classList.remove('done'); }, 1500);
    });
    box.append(h('div', { class: 'chip', title: '不走域名，在 App 里手动填写的地址' }, icon(sc.icon, sc.title),
      h('div', { class: 'chip-text' }, h('div', { class: 'label' }, sc.title), h('code', {}, sc.value)), btn));
  }
}

function updateServices(list) {
  let up = 0, known = 0;
  for (const r of list) {
    const card = cards.get(r.name);
    if (!card) continue;
    card.classList.remove('up', 'down', 'error');
    const ms = $('.ms', card), state = $('.state', card);
    if (r.ticks) $('.ticks-slot', card).replaceChildren(Charts.ticks(r.ticks, `t-${r.name}`));
    if (r.state === 'unknown') { state.textContent = '检测中'; ms.textContent = ''; continue; }
    known++;
    card.classList.add(r.state);
    if (r.state === 'up') {
      up++;
      ms.textContent = `${r.ms} ms`;
      state.textContent = '';
    } else if (r.state === 'error') {
      ms.textContent = `HTTP ${r.code}`;
      state.textContent = '异常';
    } else {
      ms.textContent = '';
      state.textContent = '离线';
    }
    const since = `自 ${new Date(r.since * 1000).toLocaleString('zh-CN', { hour12: false })}`;
    const label = { up: '在线', error: '异常', down: '离线' }[r.state];
    const avail = r.uptime == null ? '' : ` · 近 1 小时可用 ${r.uptime}%`;
    card.title = `${label}${r.error ? '：' + r.error : ''}（${since}${avail}）\n${card.getAttribute('href')}`;
  }
  const total = config.services.length;
  const s = $('#summary');
  if (!total) { s.className = 'summary'; s.lastElementChild.textContent = '还没有服务'; return; }
  if (!known) return;
  s.className = 'summary ' + (up === total ? 'up' : 'down');
  s.lastElementChild.textContent = up === total ? `全部 ${up} 项在线` : `${total - up} 项离线 · ${up}/${total}`;
}

// ── 指标卡 ──
function badge(level, text) {
  return h('span', { class: `badge ${level}` }, h('span', { class: 'dot' }), text);
}

function tile(o) {
  return h('div', { class: `tile${o.wide ? ' wide' : ''}${o.cls ? ' ' + o.cls : ''}` },
    h('div', { class: 'tile-head' }, h('span', { class: 'tile-label' }, o.label), o.level && o.level !== 'ok' ? badge(o.level, LEVEL_TEXT[o.level]) : null, o.head),
    o.value != null ? h('div', { class: 'tile-value' }, o.value) : null,
    o.sub ? h('div', { class: 'tile-sub' }, o.sub) : null,
    h('div', { class: 'tile-body' }, o.body));
}

function pluginTile(p, kinds) {
  const order = ['ok', 'warn', 'crit'];
  const worst = p.items.reduce((w, it) => (order.indexOf(it.level) > order.indexOf(w) ? it.level : w), 'ok');
  const unit = it => `${it.unit && it.unit !== '%' ? ' ' : ''}${it.unit || ''}`;
  let body;
  if (p.error) {
    body = h('div', { class: 'tile-error' }, `出错：${p.error}`);
  } else if (!p.items.length) {
    body = h('div', { class: 'tile-sub' }, '暂无数据');
  } else if (kinds.gauge === 'ring') {
    // 圆环：有上限的读数排成一排圆环，其余的列在下面
    const rings = p.items.filter(it => typeof it.max === 'number' && typeof it.value === 'number');
    const rest = p.items.filter(it => !rings.includes(it));
    body = [
      rings.length ? h('div', { class: 'rings' }, rings.map(it => h('div', { class: 'ring-item' },
        Charts.gauge(it.value, it.max, { kind: 'ring', level: it.level, center: `${it.value}${it.unit === '%' ? '%' : ''}` }),
        h('span', { class: 'ring-label' }, it.unit && it.unit !== '%' ? `${it.label} · ${it.unit}` : it.label)))) : null,
      rest.map(it => h('div', { class: 'kv-row' }, h('span', { class: 'kv-label' }, it.label), h('span', { class: 'kv-value' }, `${it.value ?? '–'}${unit(it)}`))),
    ];
  } else {
    body = h('div', { class: 'kvs' }, p.items.map(it => h('div', { class: 'kv' },
      h('div', { class: 'kv-row' },
        h('span', { class: 'kv-label' }, it.label),
        h('span', { class: 'kv-value' }, `${it.value ?? '–'}${unit(it)}`,
          it.level !== 'ok' ? h('span', { class: `lvl ${it.level}` }, ` ${LEVEL_TEXT[it.level]}`) : null)),
      typeof it.max === 'number' && typeof it.value === 'number' ? Charts.gauge(it.value, it.max, { kind: kinds.gauge, level: it.level }) : null)));
  }
  return tile({ label: p.title, level: worst, body, cls: 'plugin' });
}

let range = '5m';
let last = null;

function render(st) {
  last = st;
  if (!st.system || st.system.cpu == null) return;
  renderStrip(st);
  if (!$('#detail').hidden) renderDetail(st);
  Charts.restoreHover();
}

// 指标条：一项一个小格，数字 + 迷你图
function metric(o) {
  return h('div', { class: `metric${o.level && o.level !== 'ok' ? ' ' + o.level : ''}` },
    h('div', { class: 'metric-head' }, h('span', { class: 'metric-label' }, o.label),
      o.level && o.level !== 'ok' ? badge(o.level, LEVEL_TEXT[o.level]) : null),
    h('div', { class: 'metric-value' }, o.value),
    o.sub ? h('div', { class: 'metric-sub' }, o.sub) : null,
    o.viz ? h('div', { class: 'metric-viz' }, o.viz) : null);
}

function renderStrip(st) {
  const sys = st.system, host = st.host, kinds = chartKinds();
  const time = t => clockOf(t, st.range !== '1h');
  const pts = key => st.history.map(p => ({ t: p.t, v: p[key] }));
  const diskPct = (sys.disk_used / sys.disk_total) * 100;
  const diskLevel = diskPct >= 95 ? 'crit' : diskPct >= 85 ? 'warn' : 'ok';
  const miniGauge = (v, max, level) => Charts.gauge(v, max, { kind: kinds.gauge, level, cells: 12, center: '' });
  const metrics = [
    metric({ label: host.model, value: host.hostname, sub: `已运行 ${duration(sys.uptime)}` }),
    metric({
      label: 'CPU', level: sys.cpu >= 90 ? 'warn' : 'ok', value: pct(sys.cpu),
      viz: Charts.trend([{ label: 'CPU', color: 'var(--s1)', points: pts('cpu') }], { key: 'm-cpu', kind: kinds.trend, mini: true, floor: 10, fmt: pct, time }),
    }),
    metric({
      label: '内存', level: sys.mem_pressure, value: bytes(sys.mem_used),
      sub: `共 ${bytes(sys.mem_total, 0)} · 压力${PRESSURE_TEXT[sys.mem_pressure]}`,
      viz: Charts.stack((sys.mem_parts || []).map((p, i) => ({ ...p, color: SERIES[i % 4] })), sys.mem_total, bytes, 'm-mem', false),
    }),
    metric({
      label: '网络', value: h('span', { class: 'net-values' }, `↓ ${rate(sys.net_rx)}`),
      sub: `↑ ${rate(sys.net_tx)}`,
      viz: Charts.trend([{ label: '下载', color: 'var(--s1)', points: pts('rx') }, { label: '上传', color: 'var(--s2)', points: pts('tx') }],
        { key: 'm-net', kind: kinds.trend, mini: true, mirror: true, floor: 1024, fmt: rate, time }),
    }),
    metric({
      label: '磁盘', level: diskLevel, value: `${bytes(sys.disk_total - sys.disk_used, 0)} 可用`,
      sub: `已用 ${diskPct.toFixed(0)}%`, viz: miniGauge(diskPct, 100, diskLevel),
    }),
    ...st.plugins.map(p => {
      if (p.error) return metric({ label: p.title, value: '—', sub: p.error, level: 'warn' });
      const [first, ...rest] = p.items;
      if (!first) return metric({ label: p.title, value: '—' });
      const order = ['ok', 'warn', 'crit'];
      const worst = p.items.reduce((w, it) => (order.indexOf(it.level) > order.indexOf(w) ? it.level : w), 'ok');
      const fmt = it => `${it.value ?? '–'}${it.unit && it.unit !== '%' ? ' ' : ''}${it.unit || ''}`;
      return metric({
        label: p.title, level: worst,
        value: h('span', {}, h('small', {}, `${first.label} `), fmt(first)),
        sub: rest.map(it => `${it.label} ${fmt(it)}`).join(' · '),
        viz: typeof first.max === 'number' && typeof first.value === 'number' ? miniGauge(first.value, first.max, first.level) : null,
      });
    }),
  ];
  $('#metrics').replaceChildren(...metrics);
  if ($('#strip').hidden) { $('#strip').hidden = false; fitServices(); }
}

function renderDetail(st) {
  const sys = st.system, host = st.host;
  const kinds = chartKinds();
  $('#machine-title').textContent = `${host.model} · ${host.hostname}`;
  $('#hostline').textContent = `${host.cpu} · ${host.cores} 核 · ${host.os} · 已运行 ${duration(sys.uptime)}`;

  const hist = st.history;
  const time = t => clockOf(t, st.range !== '1h');
  const series = (key, label, color) => ({ label, color, points: hist.map(p => ({ t: p.t, v: p[key] })) });
  const memPct = (sys.mem_used / sys.mem_total) * 100;
  const diskPct = (sys.disk_used / sys.disk_total) * 100;
  const diskLevel = diskPct >= 95 ? 'crit' : diskPct >= 85 ? 'warn' : 'ok';
  const cpuLevel = sys.cpu >= 90 ? 'warn' : 'ok';
  const peak = hist.length ? Math.max(...hist.map(p => p.cpu)) : sys.cpu;

  const tiles = [
    tile({
      label: 'CPU', wide: true, level: cpuLevel, value: pct(sys.cpu),
      sub: `${st.range === '1h' ? '1 小时' : '5 分钟'}内峰值 ${pct(peak)} · 负载 ${sys.load.map(x => x.toFixed(1)).join(' / ')}`,
      body: [
        Charts.trend([series('cpu', 'CPU', 'var(--s1)')], { key: 'cpu', kind: kinds.trend, floor: 10, fmt: pct, time }),
        sys.cores && sys.cores.length ? h('div', { class: 'cores-wrap' }, h('span', { class: 'mini-label' }, `每个核心（${sys.cores.length}）`), Charts.cores(sys.cores, kinds.trend, 'cores')) : null,
      ],
    }),
    tile({
      label: '内存', wide: true, level: sys.mem_pressure, value: bytes(sys.mem_used),
      sub: h('span', {}, `共 ${bytes(sys.mem_total, 0)} · 已用 ${memPct.toFixed(0)}% · 压力 `,
        h('span', { class: `lvl ${sys.mem_pressure}` }, PRESSURE_TEXT[sys.mem_pressure]),
        sys.swap_used > 0 ? ` · 交换 ${bytes(sys.swap_used)}` : ''),
      body: [
        Charts.stack((sys.mem_parts || []).map((p, i) => ({ ...p, color: SERIES[i % 4] })), sys.mem_total, bytes, 'mem'),
        Charts.trend([series('mem', '已用', 'var(--s1)')], { key: 'memt', kind: kinds.trend, small: true, floor: sys.mem_total * 0.05, cap: sys.mem_total, fmt: v => bytes(v, 0), time }),
      ],
    }),
    tile({
      label: '网络', wide: true,
      value: h('span', { class: 'net-values' },
        h('span', {}, h('i', { class: 'swatch', style: 'background:var(--s1)' }), `↓ ${rate(sys.net_rx)}`),
        h('span', {}, h('i', { class: 'swatch', style: 'background:var(--s2)' }), `↑ ${rate(sys.net_tx)}`)),
      sub: kinds.trend === 'blocks' ? '下载 / 上传' : '上方为下载，下方为上传',
      body: Charts.trend([series('rx', '下载', 'var(--s1)'), series('tx', '上传', 'var(--s2)')],
        { key: 'net', kind: kinds.trend, mirror: true, floor: 1024, fmt: rate, time }),
    }),
    tile({
      label: '磁盘', level: diskLevel, value: `${bytes(sys.disk_total - sys.disk_used, 0)} 可用`,
      sub: `已用 ${diskPct.toFixed(0)}% · 共 ${bytes(sys.disk_total, 1)}`,
      body: Charts.gauge(diskPct, 100, { kind: kinds.gauge, level: diskLevel }),
    }),
    ...st.plugins.map(p => pluginTile(p, kinds)),
  ];
  $('#tiles').replaceChildren(...tiles);
}

function openDetail(open) {
  $('#detail').hidden = !open;
  document.body.classList.toggle('detail-open', open);
  if (open && last) renderDetail(last);
  if (open) $('#close').focus();
}
$('#more').addEventListener('click', () => openDetail(true));
$('#close').addEventListener('click', () => openDetail(false));
$('#detail').addEventListener('click', e => { if (e.target.id === 'detail') openDetail(false); });

// ── 刷新 ──
let failures = 0;
async function refresh() {
  if (document.hidden) return;
  try {
    const st = await fetchStatus();
    failures = 0;
    updateServices(st.services);
    render(st);
  } catch {
    if (++failures >= 2) {
      const s = $('#summary');
      s.className = 'summary down';
      s.lastElementChild.textContent = '指标服务未响应';
    }
  }
}

async function fetchStatus() {
  if (window.DeskDemo) return window.DeskDemo.status(range);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 8000);
  const r = await fetch(`api/status?range=${range}`, { cache: 'no-store', signal: ctrl.signal });
  clearTimeout(timer);
  if (!r.ok) throw new Error(r.status);
  return r.json();
}

for (const b of document.querySelectorAll('.range button')) {
  b.addEventListener('click', () => {
    range = b.dataset.range;
    for (const x of document.querySelectorAll('.range button')) x.setAttribute('aria-pressed', String(x === b));
    refresh();
  });
}

function tick() {
  $('#clock').textContent = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

async function main() {
  applyScheme();
  tick(); setInterval(tick, 10000);
  config = window.DeskDemo ? window.DeskDemo.config : await (await fetch('config.json', { cache: 'no-store' })).json();
  document.title = config.title;
  $('#title').textContent = config.title;
  $('#subtitle').textContent = config.subtitle;
  $('#footer').append('按 ', h('kbd', {}, '1'), '–', h('kbd', {}, '9'), ' 打开服务 · ', h('kbd', {}, 'D'), ` 详细指标 · homelab-desk ${config.version}`);
  renderServices();
  renderShortcuts();
  fitServices();
  refresh();
  setInterval(refresh, REFRESH_MS);
  if (query.has('detail')) openDetail(true);
  document.addEventListener('visibilitychange', refresh);
}

document.addEventListener('keydown', e => {
  if (e.metaKey || e.ctrlKey || e.altKey || /input|textarea/i.test(e.target.tagName)) return;
  if (e.key === 'Escape') return openDetail(false);
  if (e.key === 'd' || e.key === 'D') return openDetail($('#detail').hidden);
  const row = document.querySelector(`.svc[data-key="${e.key}"]`);
  if (row && $('#detail').hidden) location.href = row.href;
});

main();
