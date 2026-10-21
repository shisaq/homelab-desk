// 工作台的小图表。不依赖任何库，每个函数返回一个 DOM 元素。
//
// 两类画法由主题决定（CSS 变量 --trend / --gauge），配置或 ?trend= ?gauge= 可以覆盖：
//   trend：area 面积 / line 折线 / bars 柱状 / blocks 字符块 —— 随时间变化的曲线
//   gauge：bar 进度条 / ring 圆环 / segments 分段格        —— 单个数值占总量的比例
// 另有三种固定形式：stack（内存构成）、cores（每个核心）、ticks（服务近 1 小时在线记录）。
// 颜色只用角色变量（--s1…--s4 分类色、--accent、--ok/--warn/--crit 状态色），由主题给深浅两套值。
'use strict';

const Charts = (() => {
  const SVG = 'http://www.w3.org/2000/svg';
  const BLOCKS = '▁▂▃▄▅▆▇█';

  function el(tag, attrs = {}, ...children) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null) continue;
      if (k === 'class') e.className = v;
      else if (k === 'style') e.style.cssText = v;
      else e.setAttribute(k, v);
    }
    for (const c of children.flat(Infinity)) if (c != null) e.append(c.nodeType ? c : String(c));
    return e;
  }
  function svg(tag, attrs = {}) {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    return e;
  }

  // ── 提示框（全局一个）。图表每 3 秒重画，鼠标停在哪个图上，重画后接着显示 ──
  const tip = () => document.getElementById('tooltip');
  let hover = null; // { key, x, y }
  function showTip(e, text) {
    const t = tip();
    t.textContent = text;
    t.hidden = false;
    const x = Math.min(e.clientX + 12, window.innerWidth - t.offsetWidth - 8);
    t.style.transform = `translate(${x}px, ${e.clientY - t.offsetHeight - 10}px)`;
  }
  function hideTip() { tip().hidden = true; }
  function hoverable(node, key, onMove) {
    node.dataset.hoverKey = key;
    node.addEventListener('mousemove', e => { hover = { key, x: e.clientX, y: e.clientY }; onMove(e); });
    node.addEventListener('mouseleave', () => { hover = null; hideTip(); node.classList.remove('hovering'); });
    return node;
  }
  function restoreHover() {
    if (!hover) return;
    const n = document.querySelector(`[data-hover-key="${hover.key}"]`);
    if (n) n.dispatchEvent(new MouseEvent('mousemove', { clientX: hover.x, clientY: hover.y }));
  }

  // 纵轴上限取「好读」的整数：1、2、2.5、5 × 10ⁿ
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (v <= m * p) return m * p;
    return 10 * p;
  }

  // 点太多时（柱状、字符块）按平均值合并
  function downsample(points, n) {
    if (points.length <= n) return points;
    const out = [], size = points.length / n;
    for (let i = 0; i < n; i++) {
      const chunk = points.slice(Math.floor(i * size), Math.floor((i + 1) * size));
      out.push({ t: chunk[chunk.length - 1].t, v: chunk.reduce((a, p) => a + p.v, 0) / chunk.length });
    }
    return out;
  }

  // 平滑曲线：单调三次插值，不会冲出数据范围
  function smoothPath(xy) {
    if (xy.length < 3) return xy.map((p, i) => `${i ? 'L' : 'M'}${p[0]},${p[1]}`).join('');
    const n = xy.length, d = [], m = [];
    for (let i = 0; i < n - 1; i++) d.push((xy[i + 1][1] - xy[i][1]) / (xy[i + 1][0] - xy[i][0]));
    m.push(d[0]);
    for (let i = 1; i < n - 1; i++) m.push(d[i - 1] * d[i] <= 0 ? 0 : (d[i - 1] + d[i]) / 2);
    m.push(d[n - 2]);
    let s = `M${xy[0][0].toFixed(1)},${xy[0][1].toFixed(1)}`;
    for (let i = 0; i < n - 1; i++) {
      const [x0, y0] = xy[i], [x1, y1] = xy[i + 1], h = (x1 - x0) / 3;
      s += `C${(x0 + h).toFixed(1)},${(y0 + m[i] * h).toFixed(1)} ${(x1 - h).toFixed(1)},${(y1 - m[i + 1] * h).toFixed(1)} ${x1.toFixed(1)},${y1.toFixed(1)}`;
    }
    return s;
  }

  // ── 趋势 ──
  // series: [{ label, color: 'var(--s1)', points: [{t, v}] }]
  // opts: { key, kind, max（固定上限）, floor / cap（自动上限的下限 / 上限）, fmt(v), time(t),
  //         small / mini（矮一些 / 指标条里的迷你图，不标刻度）, mirror（两条序列上下对称，用于收/发）}
  function trend(series, opts) {
    const kind = opts.kind || 'area';
    const all = series.flatMap(s => s.points.map(p => p.v));
    const top = opts.max ?? Math.min(niceMax(Math.max(opts.floor ?? 0, ...all, 0) * 1.1), opts.cap ?? Infinity);
    const size = opts.mini ? ' mini' : opts.small ? ' small' : '';
    const wrap = el('div', { class: `chart trend trend-${kind}${size}` });
    const scale = el('span', { class: 'chart-scale' }, opts.fmt(top));
    if (!series[0] || series[0].points.length < 2) {
      wrap.append(el('div', { class: 'chart-empty' }, opts.mini ? '' : '数据收集中…'));
      return wrap;
    }
    if (kind === 'blocks') return blocks(series, top, opts, wrap);

    const W = 300, H = 60;
    const mirror = opts.mirror && series.length === 2;
    const root = svg('svg', { viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: 'none', class: 'chart-svg' });
    const base = mirror ? H / 2 : H;
    const yOf = (v, i) => {
      const h = (Math.min(v, top) / top) * (mirror ? H / 2 - 1 : H - 2);
      return mirror && i === 1 ? base + h : base - h;
    };
    // 基线（镜像时在中间）
    root.append(svg('line', { x1: 0, x2: W, y1: base, y2: base, class: 'chart-base' }));

    if (kind === 'bars') {
      const n = Math.min(48, series[0].points.length);
      series.forEach((s, si) => {
        const pts = downsample(s.points, n);
        const bw = W / n;
        pts.forEach((p, i) => {
          const y = yOf(p.v, si), h = Math.max(Math.abs(base - y), 0.8);
          root.append(svg('rect', {
            x: (i * bw + 0.8).toFixed(1), width: Math.max(bw - 1.6, 0.6).toFixed(1),
            y: (mirror && si === 1 ? base : base - h).toFixed(1), height: h.toFixed(1),
            rx: 1.5, fill: s.color, class: 'chart-bar',
          }));
        });
      });
    } else {
      series.forEach((s, si) => {
        const n = s.points.length;
        const xy = s.points.map((p, i) => [(i / (n - 1)) * W, yOf(p.v, si)]);
        const d = kind === 'line' ? xy.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('') : smoothPath(xy);
        if (kind === 'area') {
          const gid = `g-${opts.key}-${si}`;
          const grad = svg('linearGradient', { id: gid, x1: 0, x2: 0, y1: mirror && si === 1 ? 1 : 0, y2: mirror && si === 1 ? 0 : 1 });
          grad.append(svg('stop', { offset: '0%', 'stop-color': s.color, 'stop-opacity': 0.32 }));
          grad.append(svg('stop', { offset: '100%', 'stop-color': s.color, 'stop-opacity': 0.02 }));
          root.append(grad, svg('path', { d: `${d}L${W},${base}L0,${base}Z`, fill: `url(#${gid})` }));
        }
        root.append(svg('path', { d, stroke: s.color, class: 'chart-line', 'vector-effect': 'non-scaling-stroke' }));
      });
    }
    wrap.append(root, scale);

    // 悬停：竖线 + 每条序列一个点 + 提示框
    const cross = el('i', { class: 'chart-cross' });
    const dots = series.map(s => el('b', { class: 'chart-dot', style: `background:${s.color}` }));
    wrap.append(cross, ...dots);
    hoverable(wrap, opts.key, e => {
      const r = wrap.getBoundingClientRect();
      const n = series[0].points.length;
      const i = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width) * (n - 1))));
      const x = (i / (n - 1)) * 100;
      wrap.classList.add('hovering');
      cross.style.left = `${x}%`;
      series.forEach((s, si) => {
        const p = s.points[Math.min(i, s.points.length - 1)];
        dots[si].style.left = `${x}%`;
        dots[si].style.top = `${(yOf(p.v, si) / H) * 100}%`;
      });
      const t = series[0].points[i].t;
      const parts = series.map(s => `${series.length > 1 ? s.label + ' ' : ''}${opts.fmt(s.points[Math.min(i, s.points.length - 1)].v)}`);
      showTip(e, `${opts.time(t)}　${parts.join('　')}`);
    });
    return wrap;
  }

  function blocks(series, top, opts, wrap) {
    const n = opts.mini ? 20 : 32;
    for (const [si, s] of series.entries()) {
      const pts = downsample(s.points, n);
      const chars = pts.map(p => el('span', {}, BLOCKS[Math.max(0, Math.min(7, Math.round((Math.min(p.v, top) / top) * 7)))]));
      const row = el('div', { class: 'blocks-row' },
        series.length > 1 ? el('span', { class: 'blocks-label' }, s.label) : null,
        el('span', { class: 'blocks', style: `color:${s.color}` }, chars));
      hoverable(row, `${opts.key}-${si}`, e => {
        const r = row.lastChild.getBoundingClientRect();
        const i = Math.max(0, Math.min(pts.length - 1, Math.floor(((e.clientX - r.left) / r.width) * pts.length)));
        showTip(e, `${opts.time(pts[i].t)}　${series.length > 1 ? s.label + ' ' : ''}${opts.fmt(pts[i].v)}`);
      });
      wrap.append(row);
    }
    wrap.append(el('span', { class: 'chart-scale' }, `满格 ${opts.fmt(top)}`));
    return wrap;
  }

  // ── 占比 ──
  function gauge(value, max, opts = {}) {
    const kind = opts.kind || 'bar', level = opts.level || 'ok';
    const pct = Math.max(0, Math.min(100, (value / max) * 100));
    const aria = { role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': max, 'aria-valuenow': value };
    if (kind === 'ring') {
      const R = 26, C = 2 * Math.PI * R;
      const s = svg('svg', { viewBox: '0 0 64 64', class: 'ring-svg' });
      s.append(svg('circle', { cx: 32, cy: 32, r: R, class: 'ring-track' }));
      s.append(svg('circle', { cx: 32, cy: 32, r: R, class: 'ring-fill', 'stroke-dasharray': `${(pct / 100) * C} ${C}`, transform: 'rotate(-90 32 32)' }));
      return el('div', { class: `gauge ring ${level}`, ...aria }, s, el('span', { class: 'ring-text' }, opts.center ?? `${Math.round(pct)}%`));
    }
    if (kind === 'segments') {
      const n = opts.cells || 20, on = Math.round((pct / 100) * n);
      const cells = Array.from({ length: n }, (_, i) => el('i', { class: i < on ? 'on' : '' }));
      return el('div', { class: `gauge segments ${level}`, style: `--n:${n}`, ...aria }, cells);
    }
    return el('div', { class: `gauge bar ${level}`, ...aria }, el('span', { style: `width:${pct.toFixed(1)}%` }));
  }

  // ── 内存构成：堆叠条 + 图例。parts: [{label, bytes, color}]，剩下的是空闲 ──
  function stack(parts, total, fmt, key, withLegend = true) {
    const bar = el('div', { class: 'stack' });
    for (const p of parts) {
      const w = (p.bytes / total) * 100;
      if (w < 0.2) continue;
      const seg = el('span', { style: `width:${w.toFixed(2)}%;background:${p.color}` });
      hoverable(seg, `${key}-${p.label}`, e => showTip(e, `${p.label} ${fmt(p.bytes)}（${w.toFixed(0)}%）`));
      bar.append(seg);
    }
    if (!withLegend) return el('div', { class: 'stack-wrap' }, bar);
    const legend = el('div', { class: 'legend' }, parts.map(p =>
      el('span', { class: 'legend-item' }, el('i', { style: `background:${p.color}` }), `${p.label} `, el('b', {}, fmt(p.bytes)))));
    return el('div', { class: 'stack-wrap' }, bar, legend);
  }

  // ── 每个核心：一排小柱，高度 = 占用 ──
  function cores(values, kind, key) {
    if (kind === 'blocks') {
      const chars = values.map(v => el('span', {}, BLOCKS[Math.max(0, Math.min(7, Math.round((v / 100) * 7)))]));
      const row = el('div', { class: 'cores-blocks' }, el('span', { class: 'blocks' }, chars));
      hoverable(row, key, e => {
        const r = row.firstChild.getBoundingClientRect();
        const i = Math.max(0, Math.min(values.length - 1, Math.floor(((e.clientX - r.left) / r.width) * values.length)));
        showTip(e, `核心 ${i + 1}：${values[i].toFixed(0)}%`);
      });
      return row;
    }
    const row = el('div', { class: 'cores' });
    values.forEach((v, i) => {
      const c = el('span', { class: 'core' }, el('i', { style: `height:${Math.max(v, 2).toFixed(0)}%` }));
      hoverable(c, `${key}-${i}`, e => showTip(e, `核心 ${i + 1}：${v.toFixed(0)}%`));
      row.append(c);
    });
    return row;
  }

  // ── 服务近 1 小时在线记录：每格 2 分钟 ──
  const TICK_TEXT = { up: '在线', error: '异常', down: '离线' };
  function ticks(list, key) {
    const row = el('div', { class: 'ticks' });
    const n = list.length;
    list.forEach((t, i) => {
      const cell = el('i', { class: t ? `tick ${t.s}` : 'tick none' });
      const ago = (n - 1 - i) * 2;
      hoverable(cell, `${key}-${i}`, e => showTip(e, t
        ? `${ago ? `${ago}–${ago + 2} 分钟前` : '最近 2 分钟'}：${TICK_TEXT[t.s]}${t.ms != null ? ` · 平均 ${t.ms} ms` : ''}`
        : '还没有数据'));
      row.append(cell);
    });
    return row;
  }

  return { trend, gauge, stack, cores, ticks, restoreHover, showTip, hideTip, el };
})();
