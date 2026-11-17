"""布局引擎：把设备清单摆成三维场景（纯几何，不渲染）。

规则：
  1. 按工艺段分层（lane），沿 X 轴顺流程推进，段内设备沿 Z 轴并排
  2. 标高递升：重力流，逐段抬高（给料 0.6 / 粗碎 0 / 中细碎 3.0 / 筛分 8.0 / 洗砂 1.2）
  3. 占地用设备真实尺寸的 AABB，段内冲突沿 Z 推开
  4. 输送带按两点连线，校验倾角 ≤ 18°
"""
from __future__ import annotations

import math

from . import equipment as eqm

ELEV = {
    "feed": 0.6,
    "primary": 0.0,
    "secondary": 3.0,
    "tertiary": 3.0,
    "shaping": 3.0,
    "screening": 8.0,
    "washing": 1.2,
    "stock": 0.0,
    "aux": 0.0,
}
LANE_Z = {"feed": 0.0, "primary": 0.0, "secondary": 0.0, "tertiary": 0.0, "shaping": 0.0, "screening": 0.0, "washing": 8.0, "stock": -9.0, "aux": 14.0}
GAP_X = 6.0
GAP_Z = 3.0
MAX_SLOPE_DEG = 18.0


def _stage_of(dev: dict) -> str:
    return eqm.get(dev["eq_id"])["stage"]


def build_scene(devices: list[dict], result: dict, variant: str = "before", site: dict | None = None) -> dict:
    """devices + 求解器结果 → scene.json（浏览器直接吃）。"""
    site_w = (site or {}).get("w") or 90.0
    site_d = (site or {}).get("d") or 45.0

    # 求解器节点按 role 索引，用于取负荷与流量
    by_role = {n["role"]: n for n in result["nodes"]}

    nodes: list[dict] = []
    x = -site_w / 2 + 6.0
    prev_top: tuple[float, float] | None = None
    cursor_by_stage: dict[str, float] = {}

    for dev in devices:
        eq = eqm.get(dev["eq_id"])
        stage = eq["stage"]
        l_m, w_m = eqm.footprint_m(eq)
        h_m = eqm.height_m(eq)
        count = int(dev.get("count", 1))
        elev = ELEV.get(stage, 0.0)
        lane_z = LANE_Z.get(stage, 0.0)

        # 输送带坡度约束：把上一段顶部抬到本段顶部所需的水平距离
        top_y = elev + h_m * 0.62
        if prev_top is not None:
            dz = top_y - prev_top[1]
            if dz > 0:
                x = max(x, prev_top[0] + dz / math.tan(math.radians(MAX_SLOPE_DEG)))

        for k in range(count):
            node_id = f"n{len(nodes)+1}"
            z = lane_z + (k - (count - 1) / 2) * (w_m + GAP_Z)
            node = {
                "id": node_id,
                "role": dev["role"],
                "eq_id": dev["eq_id"],
                "model": eq["model"],
                "stage": stage,
                "pos": [round(x, 2), round(elev, 2), round(z, 2)],
                "rot_y": 0.0,
                "dims": [l_m, h_m, w_m],
                "footprint": [l_m, w_m],
                "count_index": k,
            }
            info = by_role.get(dev["role"])
            if info:
                node["load"] = info["utilization"]
                node["flow_tph"] = info["flow_tph"]
                node["capacity_tph"] = info["capacity_tph"]
                node["flag"] = info["flag"]
                node["css_mm"] = info.get("css_mm")
                node["power_kw"] = info.get("power_kw")
            else:
                node["load"] = 0.0
                node["flag"] = "ok"
            node["label"] = f"{eq['model']}" + (f" ×{count}" if count > 1 else "")
            nodes.append(node)

        prev_top = (x + l_m, top_y)
        x += l_m + GAP_X

    total_len = (x - (-site_w / 2 + 6.0))
    expanded = False
    if total_len + 12 > site_w:
        site_w = round(total_len + 18, 1)
        expanded = True

    belts = _belts(nodes)

    notes = list(result.get("notes", []))
    if expanded:
        notes.append(
            f"按 {GAP_X:.0f} m 最小间距排布后，线长约 {total_len:.0f} m，超出原场地 {site_d:.0f}×… 的可用长度，场景已按实际长度展开。"
        )
    steep = [b for b in belts if not b["ok"]]
    if steep:
        notes.append(f"有 {len(steep)} 条皮带倾角超过 {MAX_SLOPE_DEG:.0f}°，需要中转或加长。")

    bbox = _bbox(nodes)
    return {
        "variant": variant,
        "site": {"w": site_w, "d": site_d},
        "nodes": nodes,
        "belts": belts,
        "notes": notes,
        "metrics": {
            "throughput_tph": result["throughput_tph"],
            "circulating_load": result["circulating_load"],
            "pass_fraction": result["pass_fraction"],
            "ton_power_kwh": result["ton_power_kwh"],
            "unit_cost_cny_t": result["unit_cost_cny_t"],
            "max_utilization": max((n.get("load", 0) for n in nodes), default=0),
        },
        "camera": _camera(bbox),
    }


def _belts(nodes: list[dict]) -> list[dict]:
    """按工艺顺序连接相邻节点，算倾角。"""
    order = {"feed": 0, "primary": 1, "secondary": 2, "tertiary": 3, "shaping": 4, "screening": 5, "washing": 6, "stock": 7, "aux": 8}
    flow = [n for n in nodes if n["stage"] in order]
    flow.sort(key=lambda n: (order[n["stage"]], n["pos"][2]))

    belts = []
    for a, b in zip(flow, flow[1:]):
        p1 = (a["pos"][0] + a["footprint"][0] / 2, a["pos"][1] + a["dims"][1] * 0.72, a["pos"][2])
        p2 = (b["pos"][0] - b["footprint"][0] / 2, b["pos"][1] + b["dims"][1] * 0.62, b["pos"][2])
        horiz = math.dist((p1[0], p1[2]), (p2[0], p2[2]))
        dz = p2[1] - p1[1]
        # 倾角约束只针对上行（下行靠重力，不受 18° 限制）
        slope = round(math.degrees(math.atan2(dz, max(horiz, 0.1))), 1)
        belts.append(
            {
                "from_id": a["id"],
                "to_id": b["id"],
                "p1": [round(v, 2) for v in p1],
                "p2": [round(v, 2) for v in p2],
                "length": round(math.hypot(horiz, dz), 2),
                "slope_deg": slope,
                "ok": slope <= MAX_SLOPE_DEG,
            }
        )
    return belts


def _bbox(nodes: list[dict]) -> dict:
    if not nodes:
        return {"min": [0, 0, 0], "max": [0, 0, 0], "center": [0, 0, 0], "radius": 20}
    xs = [n["pos"][0] - n["footprint"][0] / 2 for n in nodes] + [n["pos"][0] + n["footprint"][0] / 2 for n in nodes]
    zs = [n["pos"][2] - n["footprint"][1] / 2 for n in nodes] + [n["pos"][2] + n["footprint"][1] / 2 for n in nodes]
    ys = [n["pos"][1] for n in nodes] + [n["pos"][1] + n["dims"][1] for n in nodes]
    mn = [round(min(xs), 2), round(min(ys), 2), round(min(zs), 2)]
    mx = [round(max(xs), 2), round(max(ys), 2), round(max(zs), 2)]
    center = [round((mn[i] + mx[i]) / 2, 2) for i in range(3)]
    radius = round(math.dist(mn, mx) / 2, 2)
    return {"min": mn, "max": mx, "center": center, "radius": radius}


def _camera(bbox: dict) -> dict:
    c = bbox["center"]
    r = max(bbox["radius"], 8)
    d = r / math.sin(math.radians(22.5)) * 0.78
    return {"target": c, "distance": round(d, 1), "pos": [round(c[0] - d * 0.58, 1), round(c[1] + d * 0.52, 1), round(c[2] + d * 0.68, 1)]}
