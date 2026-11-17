import { createViewer } from '/web/viewer.js';

const rec = window.__RECORD__ || {};
const info = document.getElementById('pick-info');

function hint(prefix, d) {
  if (!d) return;
  if (d.kind === 'belt') {
    info.innerHTML = `<b>${prefix}</b> · 输送带 · 长度 ${d.length} m · 倾角 ${d.slope}° · ${d.ok ? '合规' : '超 18°'}`;
    return;
  }
  const load = d.load != null ? (d.load * 100).toFixed(0) + '%' : '—';
  const flag = d.flag === 'overload' ? '（过载）' : d.flag === 'near' ? '（接近满负荷）' : '';
  info.innerHTML =
    `<b>${prefix}</b> · ${d.model} · 负荷 <b>${load}</b>${flag} · ` +
    `处理量 ${d.flow_tph ?? '—'} / ${d.capacity_tph ?? '—'} t/h · 标高 ${(d.pos?.[1] ?? 0).toFixed(1)} m`;
}

const vb = createViewer(document.getElementById('view-before'), document.getElementById('labels-before'), {
  onPick: (d) => hint('现状', d),
});
const va = createViewer(document.getElementById('view-after'), document.getElementById('labels-after'), {
  onPick: (d) => hint('改造后', d),
});

vb.render(rec.before);
va.render(rec.after);
