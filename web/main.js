import { createViewer } from '/web/viewer.js';

/* ---------------------------------------------------------------- 基础环境 */
let viewer;
const scenes = { before: null, after: null };
let currentVariant = 'before';
let lastPlan = null;
let sessionId = null;

const $ = (id) => document.getElementById(id);

function init() {
  viewer = createViewer($('view'), $('labels'), { onPick: showInspector });
  bindUI();
  animate();
}

function animate() {
  requestAnimationFrame(animate);
}

/* ---------------------------------------------------------------- 场景装配 */
function markNew(data, variant) {
  if (variant !== 'after') return data;
  const beforeIds = new Set((scenes.before?.nodes || []).map((n) => n.eq_id));
  return { ...data, nodes: data.nodes.map((n) => ({ ...n, isNew: !beforeIds.has(n.eq_id) })) };
}

function renderScene(data) {
  $('empty-hint')?.classList.add('hidden');
  viewer.render(data);
  updateMetrics(data);
}

function updateMetrics(data) {
  const m = data.metrics || {};
  const pct = ((m.max_utilization || 0) * 100).toFixed(0);
  const cap = currentVariant === 'after' ? '改造后（目标）' : '现状';
  $('metrics').innerHTML = `
    <div class="metrics-cap">当前视图：${cap}</div>
    <div class="metric"><span>成品产量</span><b>${(m.throughput_tph ?? 0).toFixed(0)} <i>t/h</i></b></div>
    <div class="metric"><span>循环负荷</span><b>${m.circulating_load ?? '—'}</b></div>
    <div class="metric"><span>吨电耗</span><b>${m.ton_power_kwh ?? '—'} <i>kWh/t</i></b></div>
    <div class="metric"><span>吨成本</span><b>${m.unit_cost_cny_t ?? '—'} <i>元/t</i></b></div>
    <div class="metric ${(m.max_utilization || 0) > 1 ? 'alert' : ''}"><span>最高负荷</span><b>${pct}<i>%</i></b></div>`;
}

function showInspector(d) {
  const el = $('inspector');
  const back = `<button class="back-btn" id="back-overview">← 返回整体介绍</button>`;
  const bindBack = () => {
    const b = $('back-overview');
    if (b) b.onclick = () => { if (lastPlan) renderEconomics(lastPlan); };
  };

  if (d.kind === 'belt') {
    el.innerHTML = `${back}<dl class="kv">
      <dt>构件</dt><dd>输送带</dd>
      <dt>长度</dt><dd>${d.length} m</dd>
      <dt>倾角</dt><dd>${d.slope}° <span class="tag ${d.ok ? '' : 'warn'}">${d.ok ? '合规' : '超 18°'}</span></dd>
    </dl>`;
    bindBack();
    return;
  }
  const load = d.load != null ? (d.load * 100).toFixed(0) + '%' : '—';
  const flagTxt =
    d.flag === 'overload'
      ? '<span class="tag warn">过载</span>'
      : d.flag === 'near'
        ? '<span class="tag warn">接近满负荷</span>'
        : '<span class="tag">正常</span>';
  el.innerHTML = `${back}<dl class="kv">
    <dt>设备</dt><dd>${d.model ?? '—'}</dd>
    <dt>工艺段</dt><dd>${d.stage ?? '—'}</dd>
    <dt>编号</dt><dd>${d.id ?? '—'}</dd>
    <dt>负荷</dt><dd>${load} ${flagTxt}</dd>
    <dt>处理量</dt><dd>${d.flow_tph ?? '—'} / ${d.capacity_tph ?? '—'} t/h</dd>
    <dt>标高</dt><dd>${(d.pos?.[1] ?? 0).toFixed(2)} m</dd>
    <dt>占地</dt><dd>${(d.footprint?.[0] ?? 0).toFixed(1)} × ${(d.footprint?.[1] ?? 0).toFixed(1)} m</dd>
    ${d.css_mm ? `<dt>排料口</dt><dd>${d.css_mm} mm</dd>` : ''}
    ${d.power_kw ? `<dt>运行功率</dt><dd>${d.power_kw} kW</dd>` : ''}
  </dl>
  <p class="muted" style="margin-top:12px">数字来自产能平衡求解器；设备参数为示例库，可整体替换。</p>`;
  bindBack();
}

function renderEconomics(d) {
  const e = d.economics;
  const inv = (e.investment_cny / 10000).toFixed(0);
  const gain = (e.monthly_gain_cny / 10000).toFixed(1);
  const list = d.retrofit
    .map((a) => `<li>${a.action === 'replace' ? '更换' : '增加'} ${a.model}${a.count > 1 ? ' ×' + a.count : ''}（${(a.price_cny / 10000).toFixed(0)} 万）</li>`)
    .join('');
  const notes = (d.notes || []).map((n) => `<li>${n}</li>`).join('');
  $('inspector').innerHTML = `
    <dl class="kv">
      <dt>改造总投</dt><dd>${inv} 万元</dd>
      <dt>月增现金流</dt><dd>${gain} 万元</dd>
      <dt>静态回收期</dt><dd>${e.payback_months ?? '—'} 个月</dd>
      <dt>吨电耗变化</dt><dd>${e.ton_power_delta > 0 ? '+' : ''}${e.ton_power_delta} kWh/t</dd>
      <dt>吨毛利</dt><dd>${e.margin_cny_t} 元/t</dd>
    </dl>
    <h3>改造清单</h3>
    <ul class="list">${list || '<li>无需改造</li>'}</ul>
    <h3>判断依据</h3>
    <ul class="list">${notes}</ul>`;
}

/* ---------------------------------------------------------------- 交互绑定 */
function bindUI() {
  $('generate').addEventListener('click', generate);
  $('reset').addEventListener('click', () => location.reload());
  $('run-agent').addEventListener('click', runAgent);
  $('undo').addEventListener('click', undo);
  $('command').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') runAgent();
  });
  $('history-list').addEventListener('click', onHistoryClick);
  loadHistory();
  document.querySelectorAll('#variants .tab').forEach((btn) => {
    btn.addEventListener('click', () => {
      currentVariant = btn.dataset.variant;
      document.querySelectorAll('#variants .tab').forEach((b) => b.classList.toggle('active', b === btn));
      const data = scenes[currentVariant];
      if (data) renderScene(markNew(data, currentVariant));
      setStatus(currentVariant === 'after' ? '改造后方案' : '现状');
    });
  });
}

function setStatus(text, busy = false) {
  const s = $('status');
  s.textContent = text;
  s.classList.toggle('busy', busy);
  s.classList.remove('error');
}

function progressRow(step, status, detail) {
  const box = $('progress');
  let row = box.querySelector(`[data-step="${step}"]`);
  if (!row) {
    const names = { parse: '① 解析需求', plan: '② 选型与改造', balance: '③ 产能平衡', layout: '④ 三维布局', done: '完成', error: '失败' };
    row = document.createElement('div');
    row.className = 'step';
    row.dataset.step = step;
    row.innerHTML = `<span class="k">${names[step] ?? step}</span><span class="t"></span>`;
    box.appendChild(row);
  }
  row.classList.toggle('done', status === 'done');
  row.classList.toggle('error', status === 'error');
  row.querySelector('.t').textContent = detail || '';
}

async function generate() {
  const prompt = $('prompt').value.trim();
  const btn = $('generate');
  if (!prompt) return;

  $('progress').innerHTML = '';
  btn.disabled = true;
  setStatus('解析中…', true);

  const es = new EventSource(`/api/generate?prompt=${encodeURIComponent(prompt)}`);
  let got = false;

  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    progressRow(msg.step, msg.status, msg.detail);

    if (msg.step === 'done' && msg.data) {
      got = true;
      const d = msg.data;
      lastPlan = d;
      sessionId = d.sid || sessionId;
      scenes.before = d.before;
      scenes.after = markNew(d.after, 'after');
      currentVariant = 'before';
      document.querySelectorAll('#variants .tab').forEach((b) => b.classList.toggle('active', b.dataset.variant === 'before'));
      renderScene(scenes.before);
      renderEconomics(d);
      updateDiff(d);
      setStatus(`完成（${d.source === 'deepseek' ? 'DeepSeek 解析' : '离线缓存'}）`);
      saveRecord(d, prompt);
      es.close();
      btn.disabled = false;
    }
    if (msg.step === 'error') {
      setStatus(msg.detail || '生成失败');
      es.close();
      btn.disabled = false;
    }
  };

  es.onerror = () => {
    es.close();
    btn.disabled = false;
    if (!got) setStatus('连接中断，检查后台日志');
  };
}

/* ---------------------------------------------------------------- Agent 操作区 */
const OP_CN = {
  list_equipment: '查设备库',
  add_equipment: '加设备',
  replace_equipment: '换型号',
  remove_equipment: '减设备',
  set_css: '改排料口',
  set_mesh: '改筛孔',
  set_target: '改目标产量',
  run_balance: '重算平衡',
};

function logRow(kind, text, sub) {
  const box = $('agent-log');
  const row = document.createElement('div');
  row.className = 'log-item ' + kind;
  const icon = kind === 'fail' ? '×' : kind === 'ok' ? '✓' : kind === 'say' ? '»' : '·';
  row.innerHTML = `<span class="ic">${icon}</span><span>${sub ? `<span class="nm">${sub}</span> ` : ''}${text}</span>`;
  box.appendChild(row);
  box.scrollTop = box.scrollHeight;
}

function rolesSignature(scene) {
  const m = {};
  (scene?.nodes || []).forEach((n) => {
    m[n.role] = (m[n.role] || '') + n.eq_id + '#' + n.count_index;
  });
  return m;
}

function changedRoles(prevScene, nextScene) {
  const a = rolesSignature(prevScene);
  const b = rolesSignature(nextScene);
  const roles = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...roles].filter((r) => a[r] !== b[r]);
}

function applyPlan(d, { flash = true } = {}) {
  const prevAfter = scenes.after;
  scenes.before = d.before;
  scenes.after = markNew(d.after, 'after');
  sessionId = d.sid || sessionId;

  const roles = flash ? changedRoles(prevAfter, d.after) : [];
  const wasAfter = currentVariant === 'after';
  currentVariant = wasAfter ? 'after' : 'before';
  document.querySelectorAll('#variants .tab').forEach((b) => b.classList.toggle('active', b.dataset.variant === currentVariant));
  renderScene(scenes[currentVariant]);
  renderEconomics(d);
  updateDiff(d);
  if (flash && roles.length) viewer.flash(roles);
  return roles;
}

function updateDiff(d) {
  const e = d.economics || {};
  const n = (d.retrofit || []).length;
  $('diff').innerHTML =
    `目标 <b>${(d.target_tph || d.before?.metrics?.throughput_tph || 0).toFixed(0)} t/h</b> · ` +
    `改造 <b>${n}</b> 项 · 总投 <b>${((e.investment_cny || 0) / 10000).toFixed(0)}</b> 万 · ` +
    `回收 <b>${e.payback_months ?? '—'}</b> 个月`;
}

function runAgent() {
  const q = $('command').value.trim();
  const btn = $('run-agent');
  if (!q) return;
  btn.disabled = true;
  $('command').value = '';
  setStatus('Agent 处理中…', true);
  logRow('say', q, '你');

  const url = `/api/agent/stream?sid=${encodeURIComponent(sessionId || '')}&q=${encodeURIComponent(q)}`;
  const es = new EventSource(url);
  let got = false;

  es.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === 'say') {
      logRow('say', msg.detail, 'Agent');
    } else if (msg.type === 'tool') {
      const label = OP_CN[msg.data?.name] || msg.data?.name || '操作';
      logRow(msg.data?.ok ? 'ok' : 'fail', msg.detail || '', label);
    } else if (msg.type === 'build') {
      logRow(msg.status === 'done' ? 'ok' : 'op', msg.detail, '搭建');
    } else if (msg.type === 'agent') {
      if (msg.status === 'done' && msg.detail) logRow('say', msg.detail, 'Agent');
    } else if (msg.type === 'error') {
      logRow('fail', msg.detail, '错误');
      setStatus(msg.detail);
    } else if (msg.type === 'done' && msg.data) {
      got = true;
      const roles = applyPlan(msg.data);
      setStatus(`已更新${roles.length ? '：' + roles.join('、') : ''}`);
      es.close();
      btn.disabled = false;
      saveRecord({ ...msg.data, source: msg.data.source || 'agent' }, q);
    }
  };

  es.onerror = () => {
    es.close();
    btn.disabled = false;
    if (!got) setStatus('连接中断，检查后台日志');
  };
}

async function undo() {
  if (!sessionId) return;
  const res = await fetch('/api/agent/undo', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ sid: sessionId }),
  });
  if (!res.ok) {
    logRow('fail', '没有可撤销的操作', '撤销');
    return;
  }
  const d = await res.json();
  applyPlan(d, { flash: true });
  logRow('ok', d.message || '已撤销', '撤销');
  setStatus('已撤销上一步');
}

/* ---------------------------------------------------------------- 记录存档 */
const nextFrame = () => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(r)));

async function captureBoth() {
  const prev = currentVariant;
  const shots = {};

  currentVariant = 'before';
  viewer.render(scenes.before);
  await nextFrame();
  shots.before = viewer.capture();

  currentVariant = 'after';
  viewer.render(scenes.after);
  await nextFrame();
  shots.after = viewer.capture();

  currentVariant = prev;
  viewer.render(scenes[prev]);
  document.querySelectorAll('#variants .tab').forEach((b) => b.classList.toggle('active', b.dataset.variant === prev));
  return shots;
}

async function saveRecord(d, prompt) {
  try {
    const shots = await captureBoth();
    const body = {
      prompt,
      source: d.source,
      requirements: d.requirements,
      retrofit: d.retrofit,
      economics: d.economics,
      notes: d.notes,
      before_png: shots.before,
      after_png: shots.after,
      panel_text: $('right').innerText,
      before_scene: d.before,
      after_scene: scenes.after,
    };
    const res = await fetch('/api/records', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const out = await res.json();
    if (out.id) {
      setStatus(`完成 · 已存档 ${out.id}`);
      loadHistory();
    }
  } catch (err) {
    console.warn('[PD] 存档失败', err);
  }
}

async function loadHistory() {
  const box = $('history-list');
  const cnt = $('hist-count');
  if (!box) return;
  try {
    const res = await fetch('/api/records');
    const { records } = await res.json();
    cnt.textContent = records.length ? `(${records.length})` : '';
    if (!records.length) {
      box.innerHTML = '<p class="muted">还没有记录，生成一次方案就会自动存档。</p>';
      return;
    }
    box.innerHTML = records
      .map((r) => {
        const pay = r.payback_months ? `${r.payback_months} 个月` : '—';
        const inv = r.investment_cny ? `${(r.investment_cny / 10000).toFixed(0)} 万` : '—';
        const title = r.name ? `<span class="hi-name">${r.name}</span>` : '';
        return `<div class="hist-item" data-id="${r.id}" data-name="${r.name || ''}">
          <a class="hist-link" href="/report/${r.id}" target="_blank" rel="noopener">
            ${title}
            <span class="hi-time">${r.created_at}</span>
            <span class="hi-main">${r.material || '未识别'} ${r.current_tph ?? '—'} → ${r.target_tph ?? '—'} t/h</span>
            <span class="hi-meta">改造 ${r.retrofit_count} 项 · 总投 ${inv} · 回收 ${pay}</span>
          </a>
          <div class="hist-actions">
            <button class="hist-btn" data-act="rename" title="改名">改名</button>
            <button class="hist-btn danger" data-act="delete" title="删除">删除</button>
          </div>
        </div>`;
      })
      .join('');
  } catch (err) {
    box.innerHTML = '<p class="muted">记录读取失败</p>';
  }
}

async function onHistoryClick(ev) {
  const btn = ev.target.closest('.hist-btn');
  if (!btn) return;
  ev.preventDefault();
  ev.stopPropagation();
  const item = btn.closest('.hist-item');
  const id = item.dataset.id;
  const oldName = item.dataset.name || '';

  if (btn.dataset.act === 'rename') {
    const name = prompt('给这条记录起个名字（留空=取消命名）', oldName);
    if (name === null) return;
    await fetch(`/api/records/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name.trim() }),
    });
    loadHistory();
  }

  if (btn.dataset.act === 'delete') {
    if (!confirm(`删除记录 ${oldName || id} ？\n目录和两张全景图会一起删掉，不可恢复。`)) return;
    await fetch(`/api/records/${id}`, { method: 'DELETE' });
    loadHistory();
    setStatus('已删除记录');
  }
}

init();
