import { createViewer } from './viewer.js';

const $ = (id) => document.getElementById(id);
let cases = [];
let current = null;
let variant = 'before';
let viewer = null;

const fmtWan = (v) => (v ? (v / 10000).toFixed(0) : '0');

function init() {
  viewer = createViewer($('view'), $('labels'), { onPick: showDevice });
  document.querySelectorAll('#variants .tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      variant = btn.dataset.variant;
      document.querySelectorAll('#variants .tab').forEach((b) => b.classList.toggle('active', b === btn));
      renderCase(current);
    });
  });
  fetch('./data/cases.json')
    .then((r) => r.json())
    .then((data) => {
      cases = data.cases;
      renderList();
      if (cases.length) select(cases[0].id);
    })
    .catch(() => {
      $('status').textContent = '案例数据加载失败';
    });
}

function renderList() {
  $('case-list').innerHTML = cases
    .map(
      (c) => `<button class="hist-item case-item" data-id="${c.id}">
        <span class="hi-main">${c.name}</span>
        <span class="hi-meta">${c.subtitle}</span>
      </button>`
    )
    .join('');
  document.querySelectorAll('.case-item').forEach((btn) => {
    btn.addEventListener('click', () => select(btn.dataset.id));
  });
}

function select(id) {
  current = cases.find((c) => c.id === id);
  document.querySelectorAll('.case-item').forEach((b) => b.classList.toggle('active', b.dataset.id === id));
  variant = 'before';
  document.querySelectorAll('#variants .tab').forEach((b) => b.classList.toggle('active', b.dataset.variant === 'before'));
  renderCase(current);
  $('status').textContent = current.name;
}

function renderCase(c) {
  if (!c) return;
  const scene = variant === 'after' ? c.after : c.before;
  viewer.render(scene);
  updateMetrics(scene, variant);
  renderPanel(c);
}

function updateMetrics(scene, v) {
  const m = scene.metrics || {};
  const pct = ((m.max_utilization || 0) * 100).toFixed(0);
  $('metrics').innerHTML = `
    <div class="metrics-cap">当前视图：${v === 'after' ? '改造后（目标）' : '现状'}</div>
    <div class="metric"><span>成品产量</span><b>${(m.throughput_tph || 0).toFixed(0)} <i>t/h</i></b></div>
    <div class="metric"><span>循环负荷</span><b>${m.circulating_load ?? '—'}</b></div>
    <div class="metric"><span>吨电耗</span><b>${m.ton_power_kwh ?? '—'} <i>kWh/t</i></b></div>
    <div class="metric"><span>吨成本</span><b>${m.unit_cost_cny_t ?? '—'} <i>元/t</i></b></div>
    <div class="metric ${(m.max_utilization || 0) > 1 ? 'alert' : ''}"><span>最高负荷</span><b>${pct}<i>%</i></b></div>`;
}

function renderPanel(c) {
  const e = c.economics || {};
  const list = (c.retrofit || [])
    .map(
      (a) =>
        `<li>${a.action === 'replace' ? '更换' : a.action === 'add' ? '增加' : '移除'} ${a.model}` +
        (a.count > 1 ? ` ×${a.count}` : '') +
        `（${fmtWan(a.price_cny)} 万）</li>`
    )
    .join('');
  const notes = (c.notes || []).map((n) => `<li>${n}</li>`).join('');
  $('inspector').innerHTML = `
    <dl class="kv">
      <dt>物料</dt><dd>${c.requirements.material || '—'}</dd>
      <dt>现状 → 目标</dt><dd>${(c.requirements.current_tph || 0).toFixed(0)} → ${(c.requirements.target_tph || 0).toFixed(0)} t/h</dd>
      <dt>改造总投</dt><dd>${fmtWan(e.investment_cny)} 万元</dd>
      <dt>月增现金流</dt><dd>${(e.monthly_gain_cny || 0) / 10000 < 0.1 ? '0.1' : ((e.monthly_gain_cny || 0) / 10000).toFixed(1)} 万元</dd>
      <dt>静态回收期</dt><dd>${e.payback_months ?? '—'} 个月</dd>
      <dt>吨毛利</dt><dd>${e.margin_cny_t ?? '—'} 元/t</dd>
    </dl>
    <h3>改造清单</h3>
    <ul class="list">${list || '<li>无需改造</li>'}</ul>
    <h3>判断依据</h3>
    <ul class="list">${notes}</ul>`;
}

function showDevice(d) {
  const el = $('inspector');
  if (d.kind === 'belt') {
    el.innerHTML = `<dl class="kv"><dt>构件</dt><dd>输送带</dd><dt>长度</dt><dd>${d.length} m</dd>
      <dt>倾角</dt><dd>${d.slope}° <span class="tag ${d.ok ? '' : 'warn'}">${d.ok ? '合规' : '超 18°'}</span></dd></dl>
      <p class="muted" style="margin-top:10px"><a href="#" id="back">← 返回方案结论</a></p>`;
    $('back').addEventListener('click', (e) => {
      e.preventDefault();
      renderPanel(current);
    });
    return;
  }
  const load = d.load != null ? (d.load * 100).toFixed(0) + '%' : '—';
  const flag = d.flag === 'overload' ? '（过载）' : d.flag === 'near' ? '（接近满负荷）' : '';
  el.innerHTML = `<dl class="kv">
    <dt>设备</dt><dd>${d.model ?? '—'}</dd>
    <dt>工艺段</dt><dd>${d.stage ?? '—'}</dd>
    <dt>负荷</dt><dd>${load}${flag}</dd>
    <dt>处理量</dt><dd>${d.flow_tph ?? '—'} / ${d.capacity_tph ?? '—'} t/h</dd>
    <dt>标高</dt><dd>${(d.pos?.[1] ?? 0).toFixed(2)} m</dd>
    <dt>占地</dt><dd>${(d.footprint?.[0] ?? 0).toFixed(1)} × ${(d.footprint?.[1] ?? 0).toFixed(1)} m</dd>
  </dl>
  <p class="muted" style="margin-top:10px"><a href="#" id="back">← 返回方案结论</a></p>`;
  $('back').addEventListener('click', (e) => {
    e.preventDefault();
    renderPanel(current);
  });
}

init();
