"""Agent 可操作的产线模型（受控指令集）。

设计要点：
  - Agent 只操作「设备清单 + 目标产量」，不碰坐标；坐标由 layout 重新算
  - 每个操作都要过校验，失败返回原因让 Agent 自己重试
  - 每次操作前拍快照，支持 undo
  - 所有数字由 solver 重算，Agent 不能写数字
"""
from __future__ import annotations

import copy
import json
import secrets

from . import equipment as eqm
from . import layout, solver

ROLES = ("feed", "primary", "secondary", "screening", "washing")
MAX_COUNT = 4
TARGET_RANGE = (20.0, 1200.0)


def _role_cn(role: str) -> str:
    return {
        "feed": "给料",
        "primary": "粗碎",
        "secondary": "中/细碎",
        "screening": "筛分",
        "washing": "洗选",
    }.get(role, role)


class Session:
    def __init__(self, requirements: dict, baseline: list[dict], design: list[dict],
                 target_tph: float, current_tph: float, ctx: dict):
        self.sid = "s-" + secrets.token_hex(4)
        self.requirements = requirements
        self.baseline = copy.deepcopy(baseline)
        self.design = copy.deepcopy(design)
        self.target_tph = float(target_tph)
        self.current_tph = float(current_tph)
        self.ctx = dict(ctx)
        self.history: list[dict] = []
        self.log: list[dict] = []

    # ---------------------------------------------------------------- 状态
    def snapshot(self) -> None:
        self.history.append(
            {
                "design": copy.deepcopy(self.design),
                "target_tph": self.target_tph,
            }
        )
        if len(self.history) > 30:
            self.history.pop(0)

    def undo(self) -> dict:
        if not self.history:
            return {"ok": False, "message": "没有可撤销的操作"}
        prev = self.history.pop()
        self.design = prev["design"]
        self.target_tph = prev["target_tph"]
        return {"ok": True, "message": "已撤销上一步"}

    # ---------------------------------------------------------------- 计算
    def _investment(self) -> tuple[float, list[dict]]:
        by_role_new = {d["role"]: d for d in self.design}
        by_role_old = {d["role"]: d for d in self.baseline}
        investment = 0.0
        actions: list[dict] = []

        for role, new in by_role_new.items():
            old = by_role_old.get(role)
            new_eq = eqm.get(new["eq_id"])
            new_val = new_eq["price_cny"] * int(new.get("count", 1))
            old_val = 0.0
            if old:
                old_eq = eqm.get(old["eq_id"])
                old_val = old_eq["price_cny"] * int(old.get("count", 1))

            delta = new_val - old_val
            if delta <= 0 and (not old or old["eq_id"] == new["eq_id"]):
                continue

            if old is None:
                kind, label = "add", new_eq["model"]
            elif old["eq_id"] != new["eq_id"]:
                kind, label = "replace", new_eq["model"]
            else:
                kind, label = "add", new_eq["model"]

            investment += max(delta, 0.0)
            actions.append(
                {
                    "role": role,
                    "action": kind,
                    "model": label,
                    "from_model": old and eqm.get(old["eq_id"])["model"],
                    "count": int(new.get("count", 1)),
                    "price_cny": max(delta, 0.0),
                }
            )

        for role, old in by_role_old.items():
            if role not in by_role_new:
                actions.append(
                    {
                        "role": role,
                        "action": "remove",
                        "model": eqm.get(old["eq_id"])["model"],
                        "price_cny": 0.0,
                    }
                )
        return investment, actions

    def state(self) -> dict:
        base = solver.evaluate(self.baseline, self.current_tph, self.ctx)
        target = solver.evaluate(self.design, self.target_tph, self.ctx)
        investment, actions = self._investment()
        econ = solver.economics(base, target, investment)
        site = self.requirements.get("site") or {}
        before = layout.build_scene(self.baseline, base, "before", site)
        after = layout.build_scene(self.design, target, "after", site)
        return {
            "target": target,
            "base": base,
            "scenes": {"before": before, "after": after},
            "economics": econ,
            "retrofit": actions,
            "notes": self._notes(base, target, econ, actions),
        }

    def _notes(self, base: dict, target: dict, econ: dict, actions: list[dict]) -> list[str]:
        notes: list[str] = []
        oos = self.requirements.get("out_of_scope") or []
        if oos:
            notes.append(
                "超出范围：本次只覆盖破碎筛分段（给料—破碎—筛分—洗选）。"
                + "、".join(oos)
                + " 属于选矿/冶炼工序，本方案不包含。"
            )
        notes.extend(base.get("notes", [])[:3])

        worst = max(target["nodes"], key=lambda n: n["utilization"])
        if worst["utilization"] > 1.0:
            notes.append(
                f"改造后仍有过载：{worst['model']} 负荷 {worst['utilization']*100:.0f}%，"
                f"目标 {self.target_tph:.0f} t/h 达不到。"
            )
        else:
            notes.append(
                f"改造后最高负荷 {worst['utilization']*100:.0f}%（{worst['model']}），目标 {self.target_tph:.0f} t/h 可达成。"
            )

        if actions:
            names = "、".join(f"{a['model']}({a['action']})" for a in actions)
            notes.append("当前改造：" + names)
        else:
            notes.append("当前配置与现状一致，无改造项。")

        if econ.get("payback_months"):
            notes.append(
                f"总投约 {econ['investment_cny']/10000:.0f} 万元，月增现金流 {econ['monthly_gain_cny']/10000:.1f} 万元，"
                f"回收期约 {econ['payback_months']} 个月。"
            )
        return notes

    # ---------------------------------------------------------------- 操作
    def catalog(self, stage: str | None = None) -> list[dict]:
        lib = eqm.load_library()
        rows = []
        for eq in lib.values():
            if stage and eq["stage"] != stage:
                continue
            cap = eq.get("capacity_tph")
            if isinstance(cap, list) and cap and isinstance(cap[0], list):
                cap_txt = f"{cap[0][1]}–{cap[-1][1]}"
            elif isinstance(cap, list):
                cap_txt = f"{cap[0]}–{cap[1]}"
            else:
                cap_txt = str(cap or "-")
            rows.append(
                {
                    "id": eq["id"],
                    "model": eq["model"],
                    "stage": eq["stage"],
                    "capacity": cap_txt,
                    "price_wan": round(eq["price_cny"] / 10000, 1),
                }
            )
        return rows

    def _find(self, role: str) -> dict | None:
        for d in self.design:
            if d["role"] == role:
                return d
        return None

    def _resolve(self, value) -> str | None:
        """模型经常给型号而不是 id（如 'PE900×1200'），这里同时接受两种。"""
        lib = eqm.load_library()
        if not value:
            return None
        if value in lib:
            return value
        norm = str(value).replace(" ", "").replace("×", "x").replace("X", "x").lower()
        for eq_id, eq in lib.items():
            model = eq["model"].replace(" ", "").replace("×", "x").replace("X", "x").lower()
            if model == norm or norm in model or model in norm:
                return eq_id
        return None

    def _hint_ids(self, stage: str | None = None) -> str:
        rows = self.catalog(stage)[:8]
        return "、".join(f"{r['id']}（{r['model']}）" for r in rows)

    def apply(self, name: str, args: dict) -> dict:
        handlers = {
            "list_equipment": self._op_list,
            "add_equipment": self._op_add,
            "replace_equipment": self._op_replace,
            "remove_equipment": self._op_remove,
            "set_css": self._op_css,
            "set_mesh": self._op_mesh,
            "set_target": self._op_target,
            "run_balance": self._op_balance,
        }
        fn = handlers.get(name)
        if fn is None:
            return {"ok": False, "message": f"未知操作 {name}"}
        if name != "list_equipment" and name != "run_balance":
            self.snapshot()
        result = fn(args or {})
        self.log.append({"op": name, "args": args, "ok": result.get("ok"), "message": result.get("message")})
        return result

    # -- 各操作 ------------------------------------------------------
    def _op_list(self, args: dict) -> dict:
        stage = args.get("stage")
        rows = self.catalog(stage)
        return {"ok": True, "message": f"设备库 {len(rows)} 台", "equipment": rows}

    def _op_add(self, args: dict) -> dict:
        role = args.get("role")
        count = int(args.get("count") or 1)
        if role not in ROLES:
            return {"ok": False, "message": f"role 只能是 {ROLES}"}
        eq_id = self._resolve(args.get("eq_id"))
        if eq_id is None:
            return {
                "ok": False,
                "message": f"设备库没有 {args.get('eq_id')}。{_role_cn(role)}可选：{self._hint_ids(role if role != 'secondary' else 'secondary')}",
            }
        eq = eqm.get(eq_id)
        cur = self._find(role)
        if cur:
            cur["count"] = min(MAX_COUNT, int(cur.get("count", 1)) + count)
            cur["eq_id"] = eq_id
            msg = f"{_role_cn(role)} 现有 {cur['eq_id']} ×{cur['count']}"
        else:
            self.design.append({"role": role, "eq_id": eq_id, "count": min(MAX_COUNT, count)})
            msg = f"新增 {_role_cn(role)}：{eq['model']}"
        return {"ok": True, "message": msg}

    def _op_replace(self, args: dict) -> dict:
        role = args.get("role")
        cur = self._find(role)
        if cur is None:
            return {"ok": False, "message": f"当前没有 {_role_cn(role)} 段，用 add_equipment 新增"}
        eq_id = self._resolve(args.get("eq_id"))
        if eq_id is None:
            return {"ok": False, "message": f"设备库没有 {args.get('eq_id')}。可选：{self._hint_ids()}"}
        eq = eqm.get(eq_id)
        old_model = eqm.get(cur["eq_id"])["model"]
        cur["eq_id"] = eq_id
        if eq.get("css_range_mm") and not cur.get("css_mm"):
            lo, hi = eq["css_range_mm"]
            cur["css_mm"] = round(lo + 0.6 * (hi - lo), 1)
        if eq.get("mesh_mm") and not cur.get("mesh_mm"):
            cur["mesh_mm"] = eq["mesh_mm"]
        return {"ok": True, "message": f"{_role_cn(role)}：{old_model} → {eq['model']}"}

    def _op_remove(self, args: dict) -> dict:
        role = args.get("role")
        cur = self._find(role)
        if cur is None:
            return {"ok": False, "message": f"当前没有 {_role_cn(role)} 段"}
        if cur.get("count", 1) > 1:
            cur["count"] -= 1
            return {"ok": True, "message": f"{_role_cn(role)} 减为 ×{cur['count']}"}
        self.design.remove(cur)
        return {"ok": True, "message": f"已移除 {_role_cn(role)} 段"}

    def _op_css(self, args: dict) -> dict:
        role, css = args.get("role"), args.get("css_mm")
        cur = self._find(role)
        if cur is None:
            return {"ok": False, "message": f"没有 {_role_cn(role)} 段"}
        eq = eqm.get(cur["eq_id"])
        lo, hi = eq.get("css_range_mm") or [1, 400]
        try:
            css = float(css)
        except (TypeError, ValueError):
            return {"ok": False, "message": "css_mm 必须是数字"}
        cur["css_mm"] = max(lo, min(hi, css))
        return {"ok": True, "message": f"{_role_cn(role)} 排料口 {cur['css_mm']} mm（范围 {lo}–{hi}）"}

    def _op_mesh(self, args: dict) -> dict:
        role, mesh = args.get("role"), args.get("mesh_mm")
        cur = self._find(role)
        if cur is None:
            return {"ok": False, "message": f"没有 {_role_cn(role)} 段"}
        try:
            mesh = float(mesh)
        except (TypeError, ValueError):
            return {"ok": False, "message": "mesh_mm 必须是数字"}
        cur["mesh_mm"] = [mesh, *(cur.get("mesh_mm") or [])[1:]]
        return {"ok": True, "message": f"{_role_cn(role)} 主筛孔 {mesh} mm"}

    def _op_target(self, args: dict) -> dict:
        try:
            tph = float(args.get("target_tph"))
        except (TypeError, ValueError):
            return {"ok": False, "message": "target_tph 必须是数字"}
        lo, hi = TARGET_RANGE
        self.target_tph = max(lo, min(hi, tph))
        return {"ok": True, "message": f"目标产量 {self.target_tph:.0f} t/h"}

    def _op_balance(self, args: dict) -> dict:
        target = solver.evaluate(self.design, self.target_tph, self.ctx)
        rows = [
            {
                "设备": n["model"],
                "工段": n["stage"],
                "处理量": n["flow_tph"],
                "能力": n["capacity_tph"],
                "负荷%": round(n["utilization"] * 100),
            }
            for n in target["nodes"]
        ]
        worst = max(target["nodes"], key=lambda n: n["utilization"])
        return {
            "ok": True,
            "message": (
                f"目标 {self.target_tph:.0f} t/h：最高负荷 {worst['utilization']*100:.0f}%"
                f"（{worst['model']}），循环负荷 {target['circulating_load']}"
            ),
            "loads": rows,
            "overload": [r["设备"] for r in rows if r["负荷%"] > 100],
        }
