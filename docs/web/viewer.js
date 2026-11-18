import * as THREE from 'three';
import { OrbitControls } from 'three/addons/OrbitControls.js';
import { CSS2DRenderer, CSS2DObject } from 'three/addons/CSS2DRenderer.js';

/* 三维查看器：主页与存档报告页共用（一处几何，两处使用） */

const COLORS = {
  feed: 0x5c6f85, primary: 0x6f8fb8, secondary: 0x58789f, tertiary: 0x4f6f96,
  shaping: 0x7d6fb0, screening: 0xc9843c, washing: 0x3f9f8f, stock: 0x8a97a5, aux: 0x6a7280,
};
const STATUS = { overload: 0xff6b57, near: 0xf0b132, new: 0x3fd18b };
const CONCRETE = 0x2b333c;

const mat = (color, opts = {}) =>
  new THREE.MeshStandardMaterial({ color, roughness: 0.62, metalness: 0.28, ...opts });

function boxMesh(l, h, w, color, opts) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(l, h, w), mat(color, opts));
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

function cylMesh(r, h, color, seg = 24) {
  const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, h, seg), mat(color));
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

const body = (m, x, y, z) => (m.position.set(x, y, z), m);

function kindOf(stage, model) {
  if (/颚破|PE/.test(model)) return 'jaw';
  if (/圆锥|SC|MC|SH/.test(model)) return 'cone';
  if (/PF|反击/.test(model)) return 'impact';
  if (/VSI/.test(model)) return 'vsi';
  if (/YK|筛/.test(model)) return 'screen';
  if (/ZSW|给料/.test(model)) return 'feeder';
  if (/XSD/.test(model)) return 'washer_wheel';
  if (/XL/.test(model)) return 'washer_screw';
  if (/仓/.test(model)) return 'silo';
  return 'box';
}

function buildEquipment(node) {
  const [l, h, w] = node.dims;
  const g = new THREE.Group();
  const kind = node.kind || kindOf(node.stage, node.model);
  const color = node.isNew
    ? STATUS.new
    : node.flag === 'overload'
      ? STATUS.overload
      : node.flag === 'near'
        ? STATUS.near
        : COLORS[node.stage] ?? 0x6a7280;

  switch (kind) {
    case 'jaw': {
      g.add(body(boxMesh(l * 0.72, h * 0.62, w * 0.86, color), 0, h * 0.31, 0));
      g.add(body(boxMesh(l * 0.9, h * 0.26, w * 0.95, color), l * 0.04, h * 0.75, 0));
      for (const s of [-1, 1]) {
        const fly = cylMesh(w * 0.17, 0.28, 0x8d99a6, 20);
        fly.rotation.z = Math.PI / 2;
        g.add(body(fly, -l * 0.18, h * 0.42, s * (w * 0.46)));
      }
      break;
    }
    case 'cone': {
      g.add(body(cylMesh(w * 0.34, h * 0.3, color), 0, h * 0.15, 0));
      const cone = new THREE.Mesh(new THREE.CylinderGeometry(w * 0.16, w * 0.34, h * 0.52, 24), mat(color));
      cone.castShadow = true;
      g.add(body(cone, 0, h * 0.56, 0));
      g.add(body(boxMesh(l * 0.5, h * 0.08, w * 0.5, 0x9aa6b2), 0, h * 0.86, 0));
      break;
    }
    case 'impact': {
      g.add(body(boxMesh(l * 0.78, h * 0.7, w * 0.82, color), 0, h * 0.35, 0));
      const rotor = cylMesh(w * 0.26, l * 0.5, 0x8d99a6);
      rotor.rotation.z = Math.PI / 2;
      g.add(body(rotor, 0, h * 0.45, 0));
      break;
    }
    case 'vsi': {
      g.add(body(cylMesh(w * 0.36, h * 0.62, color), 0, h * 0.31, 0));
      g.add(body(boxMesh(l * 0.44, h * 0.22, w * 0.44, 0x9aa6b2), 0, h * 0.78, 0));
      break;
    }
    case 'screen': {
      const deck = new THREE.Group();
      deck.add(body(boxMesh(l * 0.92, h * 0.3, w * 0.88, color), 0, h * 0.55, 0));
      for (let i = 0; i < 3; i++) deck.add(body(boxMesh(l * 0.8, 0.06, w * 0.8, 0xb9c3cd), 0, h * 0.5 + i * 0.22, 0));
      deck.rotation.z = THREE.MathUtils.degToRad(-16);
      g.add(deck);
      for (const sx of [-1, 1])
        for (const sz of [-1, 1])
          g.add(body(boxMesh(0.26, h * 0.36, 0.26, 0x6c7784), sx * l * 0.34, h * 0.18, sz * w * 0.32));
      break;
    }
    case 'feeder': {
      g.add(body(boxMesh(l * 0.94, h * 0.34, w * 0.9, color), 0, h * 0.5, 0));
      g.add(body(boxMesh(l * 0.3, h * 0.42, w * 0.5, 0x6c7784), -l * 0.28, h * 0.22, 0));
      break;
    }
    case 'washer_wheel': {
      g.add(body(boxMesh(l * 0.8, h * 0.4, w * 0.8, color), 0, h * 0.2, 0));
      const wheel = cylMesh(w * 0.3, 0.32, 0x9aa6b2, 18);
      wheel.rotation.z = Math.PI / 2;
      g.add(body(wheel, l * 0.12, h * 0.62, 0));
      break;
    }
    case 'washer_screw': {
      const g2 = new THREE.Group();
      g2.add(body(boxMesh(l * 0.9, h * 0.34, w * 0.8, color), 0, 0, 0));
      g2.rotation.z = THREE.MathUtils.degToRad(-14);
      g2.position.y = h * 0.45;
      g.add(g2);
      break;
    }
    case 'silo': {
      g.add(body(cylMesh(w * 0.45, h * 0.72, color, 28), 0, h * 0.36, 0));
      const coneOut = new THREE.Mesh(new THREE.CylinderGeometry(w * 0.45, w * 0.16, h * 0.24, 24), mat(color));
      coneOut.castShadow = true;
      g.add(body(coneOut, 0, h * 0.12, 0));
      break;
    }
    default:
      g.add(body(boxMesh(l * 0.9, h * 0.9, w * 0.9, color), 0, h * 0.45, 0));
  }
  return g;
}

function clearGroup(g) {
  while (g.children.length) {
    const c = g.children.pop();
    c.traverse?.((o) => {
      if (o.geometry) o.geometry.dispose();
      if (o.material) o.material.dispose?.();
      if (o.isCSS2DObject) o.element?.remove();
    });
  }
}

export function roundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

/**
 * @param {HTMLElement} viewEl   画布容器
 * @param {HTMLElement} labelEl  名牌容器
 * @param {{onPick?: (node:object)=>void}} opts
 */
export function createViewer(viewEl, labelEl, opts = {}) {
  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x0e1116);
  scene.fog = new THREE.Fog(0x0e1116, 220, 520);

  const camera = new THREE.PerspectiveCamera(45, Math.max(viewEl.clientWidth, 1) / Math.max(viewEl.clientHeight, 1), 0.1, 3000);
  camera.position.set(-60, 40, 70);

  const renderer = new THREE.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
  renderer.setSize(Math.max(viewEl.clientWidth, 1), Math.max(viewEl.clientHeight, 1));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  viewEl.appendChild(renderer.domElement);

  const labelRenderer = new CSS2DRenderer();
  labelRenderer.setSize(Math.max(viewEl.clientWidth, 1), Math.max(viewEl.clientHeight, 1));
  labelRenderer.domElement.style.position = 'absolute';
  labelRenderer.domElement.style.top = '0';
  labelRenderer.domElement.style.pointerEvents = 'none';
  // 名牌容器必须穿透点击，否则会挡住画布、导致无法拖动旋转
  labelEl.style.pointerEvents = 'none';
  labelEl.appendChild(labelRenderer.domElement);

  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.08;
  controls.maxPolarAngle = Math.PI / 2.05;
  controls.minDistance = 6;

  scene.add(new THREE.HemisphereLight(0x9fb6cc, 0x1a2028, 1.15));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(-80, 120, 60);
  sun.castShadow = true;
  sun.shadow.mapSize.set(2048, 2048);
  const d = 160;
  sun.shadow.camera.left = -d;
  sun.shadow.camera.right = d;
  sun.shadow.camera.top = d;
  sun.shadow.camera.bottom = -d;
  scene.add(sun);

  const ground = new THREE.Mesh(new THREE.PlaneGeometry(900, 900), new THREE.MeshStandardMaterial({ color: 0x1c242c, roughness: 1 }));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);

  const grid = new THREE.GridHelper(900, 180, 0x35424f, 0x222b34);
  grid.position.y = 0.01;
  scene.add(grid);

  const plant = new THREE.Group();
  const belts = new THREE.Group();
  const site = new THREE.Group();
  scene.add(plant, belts, site);

  const pickables = [];
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  let frameTick = 0;
  let disposed = false;

  function buildBelt(b) {
    const p1 = new THREE.Vector3(...b.p1);
    const p2 = new THREE.Vector3(...b.p2);
    const dir = new THREE.Vector3().subVectors(p2, p1);
    const len = dir.length();
    const color = b.ok ? 0x4a5563 : 0xff6b57;
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(len, 0.45, 1.7), mat(color, { roughness: 0.85, metalness: 0.25 }));
    mesh.position.copy(p1).add(dir.clone().multiplyScalar(0.5));
    mesh.lookAt(p2);
    mesh.rotateY(Math.PI / 2);
    mesh.castShadow = true;
    mesh.userData = { kind: 'belt', slope: b.slope_deg, length: b.length, ok: b.ok };
    return mesh;
  }

  function drawSite(s) {
    const w = s.w || 90;
    const dd = s.d || 45;
    const pts = [
      new THREE.Vector3(-w / 2, 0.03, -dd / 2),
      new THREE.Vector3(w / 2, 0.03, -dd / 2),
      new THREE.Vector3(w / 2, 0.03, dd / 2),
      new THREE.Vector3(-w / 2, 0.03, dd / 2),
      new THREE.Vector3(-w / 2, 0.03, -dd / 2),
    ];
    site.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), new THREE.LineBasicMaterial({ color: 0x3b4a5a })));

    const el = document.createElement('div');
    el.className = 'site-label';
    el.textContent = `场地 ${w.toFixed(0)} × ${dd.toFixed(0)} m`;
    const lbl = new CSS2DObject(el);
    lbl.position.set(-w / 2, 0.5, -dd / 2);
    site.add(lbl);
  }

  function frameCamera(cam) {
    if (!cam) return;
    camera.position.set(...cam.pos);
    camera.near = Math.max(0.1, cam.distance / 500);
    camera.far = cam.distance * 12;
    camera.updateProjectionMatrix();
    controls.target.set(...cam.target);
    controls.maxDistance = cam.distance * 2.6;
    controls.update();
  }

  function declutter() {
    const els = labelEl.querySelectorAll('.equip-label');
    const shown = [];
    for (const el of els) {
      el.style.visibility = 'visible';
      const r = el.getBoundingClientRect();
      const clash = shown.some((s) => !(r.right < s.left - 6 || r.left > s.right + 6 || r.bottom < s.top - 2 || r.top > s.bottom + 2));
      if (clash) el.style.visibility = 'hidden';
      else shown.push(r);
    }
  }

  function render(data) {
    clearGroup(plant);
    clearGroup(belts);
    clearGroup(site);
    pickables.length = 0;
    if (!data) return;

    const labelled = new Set();
    for (const node of data.nodes) {
      const grp = buildEquipment(node);
      grp.position.set(node.pos[0], node.pos[1], node.pos[2]);
      grp.userData = { ...node };

      if (node.pos[1] > 0.4) {
        const [l, , w] = node.dims;
        const base = boxMesh(l * 0.66, node.pos[1], w * 0.66, CONCRETE);
        base.position.set(0, -node.pos[1] / 2, 0);
        base.userData = { ...node, id: node.id + '_base', isBase: true };
        grp.add(base);
      }

      plant.add(grp);
      pickables.push(grp);

      if (!labelled.has(node.role)) {
        labelled.add(node.role);
        const el = document.createElement('div');
        el.className = 'equip-label' + (node.flag === 'overload' ? ' over' : '');
        el.textContent = node.label;
        const lbl = new CSS2DObject(el);
        lbl.position.set(0, node.dims[1] / 2 + 1.4, 0);
        grp.add(lbl);
      }
    }

    for (const b of data.belts || []) belts.add(buildBelt(b));
    drawSite(data.site || {});
    frameCamera(data.camera);
  }

  function onClick(event) {
    if (!pickables.length) return;
    const rect = renderer.domElement.getBoundingClientRect();
    pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
    pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    raycaster.setFromCamera(pointer, camera);
    const hits = raycaster.intersectObjects(pickables, true);
    if (!hits.length) return;
    let obj = hits[0].object;
    while (obj && !obj.userData?.id) obj = obj.parent;
    if (obj && opts.onPick) opts.onPick(obj.userData);
  }

  function resize() {
    const w = Math.max(viewEl.clientWidth, 1);
    const h = Math.max(viewEl.clientHeight, 1);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    labelRenderer.setSize(w, h);
  }

  function capture() {
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);

    const src = renderer.domElement;
    const out = document.createElement('canvas');
    out.width = src.width;
    out.height = src.height;
    const ctx = out.getContext('2d');
    ctx.drawImage(src, 0, 0);

    const rect = src.getBoundingClientRect();
    const dpr = src.width / Math.max(rect.width, 1);
    const px = 11 * dpr;

    labelEl.querySelectorAll('.equip-label, .site-label').forEach((el) => {
      if (el.style.visibility === 'hidden') return;
      const r = el.getBoundingClientRect();
      if (r.width === 0) return;
      const x = (r.left - rect.left) * dpr;
      const y = (r.top - rect.top) * dpr;
      const w = r.width * dpr;
      const h = r.height * dpr;
      if (el.classList.contains('equip-label')) {
        ctx.fillStyle = 'rgba(14,17,22,0.82)';
        roundRect(ctx, x, y, w, h, 4 * dpr);
        ctx.fill();
        ctx.strokeStyle = el.classList.contains('over') ? 'rgba(255,107,87,0.8)' : 'rgba(90,105,120,0.8)';
        ctx.lineWidth = 1 * dpr;
        ctx.stroke();
      }
      ctx.fillStyle = el.classList.contains('over') ? '#ffb3a8' : el.classList.contains('site-label') ? '#5f6d7c' : '#cfd8e3';
      ctx.font = `${px}px "Microsoft YaHei", "Segoe UI", sans-serif`;
      ctx.textBaseline = 'middle';
      ctx.fillText(el.textContent, x + (el.classList.contains('equip-label') ? 7 * dpr : 0), y + h / 2);
    });
    return out.toDataURL('image/png');
  }

  function animate() {
    if (disposed) return;
    requestAnimationFrame(animate);
    controls.update();
    renderer.render(scene, camera);
    labelRenderer.render(scene, camera);
    if (++frameTick % 12 === 0) declutter();
  }

  // 变更的设备脉冲高亮一下，让人看见“哪里被改了”
  function flash(roles = []) {
    if (!roles.length) return;
    const items = pickables.filter((g) => roles.includes(g.userData?.role));
    if (!items.length) return;
    const t0 = performance.now();
    const tick = (t) => {
      const k = Math.min(1, (t - t0) / 1300);
      const s = 1 + 0.14 * Math.sin(k * Math.PI * 3) * (1 - k);
      items.forEach((g) => g.scale.setScalar(s));
      if (k < 1) requestAnimationFrame(tick);
      else items.forEach((g) => g.scale.setScalar(1));
    };
    requestAnimationFrame(tick);
  }

  renderer.domElement.addEventListener('pointerdown', onClick);
  window.addEventListener('resize', resize);
  animate();

  return {
    render,
    capture,
    resize,
    flash,
    scene,
    dispose() {
      disposed = true;
      window.removeEventListener('resize', resize);
      renderer.dispose();
    },
  };
}
