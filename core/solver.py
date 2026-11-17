"""产能平衡求解器：确定性计算，不调用任何模型。

模型：给料 → 粗碎 → 中/细碎 ↔ 筛分（闭路）→ 成品/洗砂
关键假设（全部可调，写在 ctx 里）：
  - 成品产量 = 新鲜给料量（忽略皮带撒漏与水分损失）
  - 闭路稳态：筛下通过率 p 由"破碎机排料口 / 筛孔"比值经验确定
  - 循环负荷 CL = (1-p)/p；二段与筛分的通过量 = F/p
"""
from __future__ import annotations

from . import equipment as eqm

# 通过率经验表：(css / mesh, 通过率)
_PASS_TABLE = [(0.4, 0.90), (0.6, 0.85), (0.8, 0.72), (1.0, 0.55), (1.2, 0.40), (1.5, 0.28)]

DEFAULT_CTX = {
    "shift_hours": 10,
    "days_per_month": 26,
    "electricity_cny_kwh": 0.65,
    "wear_cny_per_t": 3.0,          # 衬板/筛网等磨耗件摊销（示例）
    "other_cny_per_t": 1.5,         # 备件、维修、耗材（示例）
    "ore_cost_cny_per_t": 20.0,     # 原料/爆破/采运（示例，按自有矿还是外购差异很大）
    "transport_cny_per_t": 4.0,     # 厂内倒运与装车（示例）
    "labor_cny_per_month": 4 * 8500,
    "price_cny_per_t": 55.0,        # 成品综合售价（示例）
    "install_factor": 1.9,          # 设备价 → 改造总投（土建/钢结构/电气/安装）
    "downtime_days": 3,             # 改造停机天数
    "hardness_factor": 1.0,         # 硬度折减：软 1.00 / 中 0.92 / 硬 0.82
    "fine_share": 0.35,             # 0-5mm 占成品比例，用于洗砂/制砂段流量
}


def pass_fraction(css_mm: float, mesh_mm: float) -> float:
    """筛下通过率：排料口相对筛孔越大，通过率越低。"""
    if mesh_mm <= 0:
        return 0.7
    ratio = css_mm / mesh_mm
    pts = _PASS_TABLE
    if ratio <= pts[0][0]:
        return pts[0][1]
    if ratio >= pts[-1][0]:
        return pts[-1][1]
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= ratio <= x2:
            r = (ratio - x1) / (x2 - x1)
            return round(y1 + r * (y2 - y1), 3)
    return pts[-1][1]


def _device_stage(dev: dict) -> str:
    return eqm.get(dev["eq_id"])["stage"]


def evaluate(devices: list[dict], throughput: float, ctx: dict | None = None) -> dict:
    """devices: [{role, eq_id, css_mm, count}]；throughput: 成品产量 t/h。"""
    c = {**DEFAULT_CTX, **(ctx or {})}
    nodes: list[dict] = []
    notes: list[str] = []

    by_role: dict[str, dict] = {}
    for d in devices:
        by_role[d["role"]] = d

    # 1) 闭路参数
    sec = by_role.get("secondary") or by_role.get("tertiary") or by_role.get("primary")
    scr = by_role.get("screening")
    mesh = (scr.get("mesh_mm") or [25])[0] if scr else 25
    css_sec = sec.get("css_mm") if sec else None
    p = pass_fraction(css_sec or 20, mesh)
    cl = round((1 - p) / p, 2)
    circuit_tph = round(throughput / p, 1)

    # 2) 各段流量
    flow = {
        "feed": throughput,
        "primary": throughput,
        "secondary": circuit_tph,
        "tertiary": circuit_tph,
        "shaping": round(throughput * c["fine_share"], 1),
        "screening": circuit_tph,
        "washing": round(throughput * c["fine_share"], 1),
        "stock": throughput,
    }

    total_power = 0.0
    for d in devices:
        eq = eqm.get(d["eq_id"])
        stage = eq["stage"]
        q = flow.get(stage, throughput)
        unit_cap = eqm.capacity_at(eq, d.get("css_mm")) * c.get("hardness_factor", 1.0)
        cap = unit_cap * d.get("count", 1)
        u = round(q / cap, 3) if cap else 0
        load = max(0.35, min(u, 1.0))
        power = eqm.power_kw(eq) * d.get("count", 1) * load
        total_power += power
        nodes.append(
            {
                "role": d["role"],
                "eq_id": d["eq_id"],
                "model": eq["model"],
                "stage": stage,
                "count": d.get("count", 1),
                "css_mm": d.get("css_mm"),
                "flow_tph": round(q, 1),
                "unit_capacity_tph": round(unit_cap, 1),
                "capacity_tph": round(cap, 1),
                "utilization": u,
                "power_kw": round(power, 1),
                "flag": "overload" if u > 1.0 else ("near" if u > 0.9 else "ok"),
            }
        )

    # 3) 指标
    ton_power = round(total_power / throughput, 2) if throughput else 0
    elec = ton_power * c["electricity_cny_kwh"]
    labor = c["labor_cny_per_month"] / max(throughput * c["shift_hours"] * c["days_per_month"], 1)
    variable_cost = round(
        elec
        + c["wear_cny_per_t"]
        + c["other_cny_per_t"]
        + c["ore_cost_cny_per_t"]
        + c["transport_cny_per_t"],
        2,
    )
    unit_cost = round(variable_cost + labor, 2)

    overloads = [n for n in nodes if n["flag"] == "overload"]
    nears = [n for n in nodes if n["flag"] == "near"]

    if overloads:
        names = "、".join(f"{n['model']}（{n['utilization']*100:.0f}%）" for n in overloads)
        notes.append(f"过载：{names}。成品 {throughput:.0f} t/h 时这些设备跑不动，实际产量会被它们卡住。")
    if cl > 1.4:
        notes.append(f"循环负荷 {cl}（经验区间 1.1–1.4），说明筛分能力相对破碎能力不足，细料被反复碾磨。")
    elif cl > 0:
        notes.append(f"循环负荷 {cl}，处于经验区间内。")
    if nears and not overloads:
        names = "、".join(n["model"] for n in nears)
        notes.append(f"接近满负荷：{names}，没有余量，产量再往上必先动它。")
    if throughput and throughput < circuit_tph:
        notes.append(f"二段与筛分的通过量是 {circuit_tph:.0f} t/h（成品 {throughput:.0f} ÷ 通过率 {p:.2f}），不是 {throughput:.0f}。")

    return {
        "throughput_tph": throughput,
        "pass_fraction": p,
        "circulating_load": cl,
        "circuit_tph": circuit_tph,
        "nodes": nodes,
        "ton_power_kwh": ton_power,
        "total_power_kw": round(total_power, 1),
        "unit_cost_cny_t": unit_cost,
        "variable_cost_cny_t": variable_cost,
        "notes": notes,
        "context": c,
    }


def economics(base: dict, target: dict, investment_cny: float) -> dict:
    c = target["context"]
    hours, days = c["shift_hours"], c["days_per_month"]
    delta_tph = target["throughput_tph"] - base["throughput_tph"]
    price = c["price_cny_per_t"]
    margin_old = price - base["variable_cost_cny_t"]
    margin_new = price - target["variable_cost_cny_t"]

    monthly_old = base["throughput_tph"] * hours * days * margin_old
    monthly_new = target["throughput_tph"] * hours * days * margin_new
    gain = monthly_new - monthly_old

    # 无改造时不存在投资与停机损失，也不该算回收期
    if investment_cny <= 0:
        return {
            "delta_tph": round(delta_tph, 1),
            "monthly_gain_cny": round(gain),
            "equipment_cny": 0,
            "install_cny": 0,
            "downtime_cny": 0,
            "investment_cny": 0,
            "payback_months": None,
            "annual_gain_cny": round(gain * 12),
            "ton_power_delta": round(target["ton_power_kwh"] - base["ton_power_kwh"], 2),
            "margin_cny_t": round(margin_new, 1),
            "no_retrofit": True,
        }

    # 改造总投 = 设备价 × (土建/安装系数)，再加停机损失
    total_inv = investment_cny * c["install_factor"]
    downtime = base["throughput_tph"] * hours * c["downtime_days"] * margin_old
    total_inv += downtime

    payback = round(total_inv / gain, 1) if gain > 0 else None

    return {
        "delta_tph": round(delta_tph, 1),
        "monthly_gain_cny": round(gain),
        "equipment_cny": round(investment_cny),
        "install_cny": round(investment_cny * (c["install_factor"] - 1)),
        "downtime_cny": round(downtime),
        "investment_cny": round(total_inv),
        "payback_months": payback,
        "annual_gain_cny": round(gain * 12),
        "ton_power_delta": round(target["ton_power_kwh"] - base["ton_power_kwh"], 2),
        "margin_cny_t": round(margin_new, 1),
    }


if __name__ == "__main__":
    devs = [
        {"role": "feed", "eq_id": "FD_ZSW490x110", "count": 1},
        {"role": "primary", "eq_id": "JC_PE900x1200", "css_mm": 120, "count": 1},
        {"role": "secondary", "eq_id": "CC_SG1650", "css_mm": 20, "count": 1},
        {"role": "screening", "eq_id": "SC_2YK2160", "mesh_mm": [25, 10], "count": 2},
        {"role": "washing", "eq_id": "WS_XSD3620", "count": 1},
    ]
    for q in (230, 350):
        r = evaluate(devs, q)
        print(f"\n=== 成品 {q} t/h → 二段通过 {r['circuit_tph']} t/h, 循环负荷 {r['circulating_load']}, 吨电耗 {r['ton_power_kwh']} kWh/t, 吨成本 {r['unit_cost_cny_t']}")
        for n in r["nodes"]:
            print(f"  {n['model']:<24} 流量 {n['flow_tph']:>7} / 能力 {n['capacity_tph']:>7} = {n['utilization']*100:>5.0f}%  {n['flag']}")
        for note in r["notes"]:
            print("  -", note)
