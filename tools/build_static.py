"""把 demo 的真实运行结果固化成静态站点（docs/），用于 GitHub Pages 发布。

为什么不直接发布服务：这是一台本地机器 + 需要 API key 的后端，
静态版把「三套真实案例」的完整场景数据固化下来，浏览器里照样能
旋转、缩放、点选设备、切换现状/改造后、看算账结论。

用法：
    .venv/Scripts/python.exe tools/build_static.py
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import layout, planner  # noqa: E402

DOCS = ROOT / "docs"
WEB = ROOT / "web"

CASES = [
    {
        "id": "zhang-limestone",
        "name": "张总石灰岩线",
        "subtitle": "现状 230 t/h → 目标 350 t/h",
        "req": json.loads((ROOT / "data" / "cases" / "zhang_line.json").read_text(encoding="utf-8")),
    },
    {
        "id": "river-pebble-200",
        "name": "河卵石新建线",
        "subtitle": "零信息输入，自动选型 200 t/h",
        "req": {
            "material": "河卵石",
            "hardness": "hard",
            "design_tph": None,
            "current_tph": 150,
            "target_tph": 200,
            "products": ["0-5", "5-10", "10-20"],
            "symptoms": ["新建线", "无现成设备"],
            "site": {"w": 60, "d": 30},
            "shift_hours": 10,
            "price_cny_per_t": 62,
            "existing": [],
        },
    },
    {
        "id": "big-line-500",
        "name": "石灰岩大线扩产",
        "subtitle": "现状 420 t/h → 目标 500 t/h",
        "req": {
            "material": "石灰岩",
            "hardness": "medium",
            "design_tph": 500,
            "current_tph": 420,
            "target_tph": 500,
            "products": ["0-5", "5-10", "10-25", "25-31.5"],
            "symptoms": ["扩产"],
            "site": {"w": 110, "d": 55},
            "shift_hours": 12,
            "price_cny_per_t": 58,
            "existing": [
                {"role": "feed", "eq_id": "FD_ZSW600x130", "count": 1},
                {"role": "primary", "eq_id": "JC_PE1200x1500", "css_mm": 200, "count": 1},
                {"role": "secondary", "eq_id": "CC_SG1650", "css_mm": 25, "count": 2},
                {"role": "screening", "eq_id": "SC_3YK2460", "mesh_mm": [25, 10], "count": 2},
            ],
        },
    },
]

INDEX = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>PlantDesigner 3D · 产线方案在线演示</title>
<link rel="stylesheet" href="./web/style.css" />
<script type="importmap">
{"imports": {"three": "./web/lib/three/three.module.js", "three/addons/": "./web/lib/three/addons/"}}
</script>
</head>
<body>
<div id="app">
  <header id="topbar">
    <div class="brand">
      <span class="logo">PD</span>
      <div>
        <h1>PlantDesigner 3D</h1>
        <p>矿山破碎筛分产线 · 三维方案演示（静态版）</p>
      </div>
    </div>
    <div class="status" id="status">点击左侧案例开始</div>
  </header>

  <aside id="left">
    <div class="hist-head">案例（真实运行结果）</div>
    <div id="case-list" class="hist-list"></div>
    <div class="hint">
      <p><b>这是什么</b></p>
      <p>三个案例都是求解器真实算出来的：设备选型、产能平衡、循环负荷、投资回收期。</p>
      <p>左键旋转 / 滚轮缩放 / 右键平移 / 点击设备看参数。</p>
      <p class="muted">本页为静态版：场景数据在浏览器本地渲染，不调用任何模型。
      要输入自己的需求，请本地运行仓库里的服务。</p>
    </div>
  </aside>

  <main id="stage">
    <div id="view"></div>
    <div id="labels"></div>
    <div id="variants">
      <button class="tab active" data-variant="before">现状</button>
      <button class="tab" data-variant="after">改造后</button>
    </div>
    <div id="legend">
      <span class="dot crush"></span>破碎
      <span class="dot screen"></span>筛分
      <span class="dot belt"></span>输送
      <span class="dot over"></span>过载
      <span class="dot new"></span>新增/更换
    </div>
  </main>

  <aside id="right">
    <div id="metrics" class="metrics"></div>
    <h2>方案结论</h2>
    <div id="inspector"><p class="muted">选择左侧案例，或点击三维场景中的设备。</p></div>
  </aside>
</div>
<script type="module" src="./web/app.js"></script>
</body>
</html>
"""

APP_JS = r"""import { createViewer } from './viewer.js';

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
"""

EXTRA_CSS = """
.hist-item { width: 100%; text-align: left; cursor: pointer; }
.case-item.active { border-color: var(--accent); }
"""


def build() -> None:
    DOCS.mkdir(exist_ok=True)
    (DOCS / "web").mkdir(exist_ok=True)
    (DOCS / "data").mkdir(exist_ok=True)

    # 前端资源：复用 demo 的 viewer.js 与 three.js
    shutil.copy(WEB / "viewer.js", DOCS / "web" / "viewer.js")
    shutil.copy(WEB / "style.css", DOCS / "web" / "style.css")
    with (DOCS / "web" / "style.css").open("a", encoding="utf-8") as f:
        f.write(EXTRA_CSS)

    lib_dst = DOCS / "web" / "lib"
    if lib_dst.exists():
        shutil.rmtree(lib_dst)
    shutil.copytree(WEB / "lib", lib_dst)

    # 页面与脚本
    (DOCS / "index.html").write_text(INDEX, encoding="utf-8")
    (DOCS / "web" / "app.js").write_text(APP_JS, encoding="utf-8")

    # 真实案例数据
    out = []
    for c in CASES:
        req = c["req"]
        plan = planner.make_plan(req)
        site = req.get("site") or {}
        before = layout.build_scene(plan["base_devices"], plan["base"], "before", site)
        after = layout.build_scene(plan["target_devices"], plan["target"], "after", site)

        before_ids = {n["eq_id"] for n in before["nodes"]}
        for n in after["nodes"]:
            n["isNew"] = n["eq_id"] not in before_ids

        out.append(
            {
                "id": c["id"],
                "name": c["name"],
                "subtitle": c["subtitle"],
                "requirements": req,
                "before": before,
                "after": after,
                "economics": plan["economics"],
                "retrofit": plan["retrofit"],
                "notes": plan["notes"],
            }
        )
        print(f"  {c['name']}: {len(after['nodes'])} 台设备, 投资 {plan['economics']['investment_cny']/1e4:.0f} 万")

    (DOCS / "data" / "cases.json").write_text(
        json.dumps({"cases": out}, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"\n已生成 {DOCS}")


if __name__ == "__main__":
    build()
