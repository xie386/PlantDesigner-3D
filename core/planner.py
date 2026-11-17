"""工艺规划与改造方案生成：把「需求」变成「设备清单 + 改造清单」。

规则型，不用模型。所有结论都能回溯到设备库的能力表与求解器的平衡结果。
"""
from __future__ import annotations

from . import equipment as eqm
from . import solver

# 需求里若没给现有设备，按目标产量自动选型的候选顺序（从小到大）
AUTO_PICK = {
    "feed": ["FD_ZSW380x96", "FD_ZSW490x110", "FD_ZSW600x130"],
    "primary": ["JC_PE600x900", "JC_PE750x1060", "JC_PE900x1200", "JC_PE1200x1500"],
    "secondary": ["CC_MC1400", "CC_SG1650"],
    "screening": ["SC_2YK2160", "SC_3YK2460"],
    "washing": ["WS_XSD3620", "WS_XL914"],
}

# 产能不足时的升级替换（同角色换更大）
UPGRADE = {
    "feed": {"FD_ZSW380x96": "FD_ZSW490x110", "FD_ZSW490x110": "FD_ZSW600x130"},
    "primary": {"JC_PE600x900": "JC_PE750x1060", "JC_PE750x1060": "JC_PE900x1200", "JC_PE900x1200": "JC_PE1200x1500"},
    "secondary": {},
    "screening": {"SC_2YK2160": "SC_3YK2460", "SC_2YK2460": "SC_3YK2460"},
    "washing": {"WS_XL914": "WS_XSD3620"},
}

STAGE_ORDER = ["feed", "primary", "secondary", "screening", "washing", "stock"]


def default_existing(target_tph: float, hardness: str = "medium") -> list[dict]:
    """没有给现有配置时，按目标产量拼一条最小可用线。"""
    hf = {"soft": 1.0, "medium": 0.92, "hard": 0.82}.get(hardness, 0.92)
    flow = target_tph / 0.72  # 二段与筛分的通过量
    prim = _pick("primary", target_tph, hf)
    return [
        {"role": "feed", "eq_id": _pick("feed", target_tph, hf), "count": 1},
        {"role": "primary", "eq_id": prim, "css_mm": primary_css(eqm.get(prim)), "count": 1},
        {"role": "secondary", "eq_id": _pick("secondary", flow, hf), "css_mm": 20, "count": 1},
        {"role": "screening", "eq_id": _pick("screening", flow, hf), "mesh_mm": [25, 10], "count": 1},
        {"role": "washing", "eq_id": _pick("washing", target_tph * 0.35, hf), "count": 1},
    ]


def primary_css(eq: dict) -> float:
    """粗碎排料口选型惯例：0–31.5 成品线常取 110–130 mm，按设备范围夹紧。"""
    lo, hi = eq.get("css_range_mm") or [120, 120]
    return min(max(120.0, lo), hi)


def _pick(role: str, need_tph: float, hf: float = 1.0) -> str:
    """按需求产能选一台：留 15% 余量，并按硬度折减反推所需标称能力。"""
    need = need_tph * 1.15 / max(hf, 0.5)
    for eq_id in AUTO_PICK[role]:
        eq = eqm.get(eq_id)
        lo, hi = eq.get("css_range_mm") or [None, None]
        if role == "primary":
            css = primary_css(eq)
        elif lo:
            css = eqm.css_mid(eq)
        else:
            css = None
        if eqm.capacity_at(eq, css) >= need:
            return eq_id
    return AUTO_PICK[role][-1]


def build_retrofit(base_devices: list[dict], target_tph: float, ctx: dict | None = None) -> dict:
    """在当前设备清单上，找出为达到目标产量必须动的地方。"""
    c = {**solver.DEFAULT_CTX, **(ctx or {})}
    staged = solver.evaluate(base_devices, target_tph, c)

    actions: list[dict] = []
    target_devices = [dict(d) for d in base_devices]
    added_price = 0.0

    for node in staged["nodes"]:
        if node["utilization"] <= 0.95:
            continue
        role = node["role"]
        idx = next(i for i, d in enumerate(target_devices) if d["role"] == role)
        dev = target_devices[idx]
        eq = eqm.get(dev["eq_id"])

        # 优先加同型号一台（最省的方案），加不动就换更大型号
        if node["utilization"] > 1.0 and node["utilization"] <= 2.0 and role in ("secondary", "screening"):
            dev["count"] = dev.get("count", 1) + 1
            added_price += eq["price_cny"]
            actions.append(
                {
                    "role": role,
                    "action": "add",
                    "eq_id": dev["eq_id"],
                    "model": eq["model"],
                    "count_add": 1,
                    "price_cny": eq["price_cny"],
                    "reason": f"{eq['model']} 在 {target_tph:.0f} t/h 时负荷 {node['utilization']*100:.0f}%，加一台同型号最省",
                }
            )
        else:
            bigger = UPGRADE.get(role, {}).get(dev["eq_id"])
            if not bigger:
                # 没有更大的型号：加一台
                dev["count"] = dev.get("count", 1) + 1
                added_price += eq["price_cny"]
                actions.append(
                    {
                        "role": role,
                        "action": "add",
                        "eq_id": dev["eq_id"],
                        "model": eq["model"],
                        "count_add": 1,
                        "price_cny": eq["price_cny"],
                        "reason": f"{eq['model']} 负荷 {node['utilization']*100:.0f}%，设备库无更大型号，加一台",
                    }
                )
            else:
                new_eq = eqm.get(bigger)
                unit = new_eq["price_cny"]
                n_units = int(dev.get("count", 1))
                added_price += unit * n_units
                actions.append(
                    {
                        "role": role,
                        "action": "replace",
                        "eq_id": bigger,
                        "model": new_eq["model"],
                        "from_model": eq["model"],
                        "count": n_units,
                        "price_cny": unit * n_units,
                        "reason": f"{eq['model']} 负荷 {node['utilization']*100:.0f}%，换 {new_eq['model']} 才能覆盖",
                    }
                )
                dev["eq_id"] = bigger
                if role == "primary":
                    dev["css_mm"] = 150

    return {
        "actions": actions,
        "devices": target_devices,
        "equipment_cny": added_price,
        "preview": staged,
    }


def make_plan(req: dict) -> dict:
    """req 是解析后的需求；返回完整方案（现状 + 改造后 + 经济性 + 说明）。"""
    ctx = {
        "shift_hours": req.get("shift_hours") or solver.DEFAULT_CTX["shift_hours"],
        "price_cny_per_t": req.get("price_cny_per_t") or solver.DEFAULT_CTX["price_cny_per_t"],
        "hardness_factor": {"soft": 1.0, "medium": 0.92, "hard": 0.82}.get(req.get("hardness"), 0.92),
    }
    if req.get("site"):
        ctx["site"] = req["site"]

    base_devices = req.get("existing") or default_existing(
        req.get("target_tph") or 300, req.get("hardness") or "medium"
    )
    base_tph = float(req.get("current_tph") or req.get("design_tph") or 230)
    target_tph = float(req.get("target_tph") or base_tph)

    base = solver.evaluate(base_devices, base_tph, ctx)
    retrofit = build_retrofit(base_devices, target_tph, ctx)
    target = solver.evaluate(retrofit["devices"], target_tph, ctx)
    econ = solver.economics(base, target, retrofit["equipment_cny"])
    econ["downtime_days"] = ctx.get("downtime_days", solver.DEFAULT_CTX["downtime_days"])

    notes = list(base["notes"])
    oos = req.get("out_of_scope") or []
    if oos:
        notes.insert(
            0,
            "超出范围：本次只覆盖破碎筛分段（给料—破碎—筛分—洗选）。"
            + "、".join(oos)
            + " 属于选矿/冶炼工序，本方案不包含，也不做估算。",
        )
    if req.get("hardness") in ("medium", "hard"):
        notes.insert(
            1,
            f"硬度折减：按 {req.get('hardness')} 取 {ctx['hardness_factor']:.2f}，"
            "设备库标称能力已按此折减后再算负荷。",
        )
    if retrofit["actions"]:
        notes.append(
            "改造要点："
            + "；".join(f"{a['model']}（{a['action']}）" for a in retrofit["actions"])
        )
    else:
        notes.append(
            f"目标 {target_tph:.0f} t/h 下现有设备均有余量，不需要改造；"
            "若实测产量仍上不去，优先核对排料口、筛孔与给料均匀性。"
        )
    if econ.get("payback_months"):
        notes.append(
            f"改造总投约 {econ['investment_cny']/10000:.0f} 万元，月增现金流 {econ['monthly_gain_cny']/10000:.1f} 万元，静态回收期约 {econ['payback_months']:.1f} 个月。"
        )

    return {
        "requirements": req,
        "base_devices": base_devices,
        "target_devices": retrofit["devices"],
        "base": base,
        "target": target,
        "retrofit": retrofit["actions"],
        "economics": econ,
        "notes": notes,
        "ctx": ctx,
    }
