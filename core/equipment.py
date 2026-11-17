"""设备库读取、能力插值、占地换算。纯数据层，不含业务判断。"""
from __future__ import annotations

import json
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"

_lib: dict[str, dict] | None = None


def load_library() -> dict[str, dict]:
    global _lib
    if _lib is None:
        raw = json.loads((DATA / "equipment.json").read_text(encoding="utf-8"))
        _lib = {e["id"]: e for e in raw["equipment"]}
    return _lib


def get(eq_id: str) -> dict:
    lib = load_library()
    if eq_id not in lib:
        raise KeyError(f"设备库中没有 {eq_id}")
    return lib[eq_id]


def footprint_m(eq: dict) -> tuple[float, float]:
    """返回占地（长, 宽）米。dims_mm 是 (L, W, H)。"""
    l, w, _h = eq["dims_mm"]
    return round(l / 1000, 2), round(w / 1000, 2)


def height_m(eq: dict) -> float:
    return round(eq["dims_mm"][2] / 1000, 2)


def capacity_at(eq: dict, css_mm: float | None) -> float:
    """按排料口插值处理能力（t/h）。筛分/给料等无 css 的设备返回上限值。"""
    cap = eq.get("capacity_tph")
    if isinstance(cap, (int, float)):
        return float(cap)
    if isinstance(cap, list) and cap and isinstance(cap[0], (int, float)):
        return float(cap[1])
    table = cap
    if not table:
        raise ValueError(f"{eq['id']} 缺少产能参数")
    if css_mm is None:
        return float(table[-1][1])
    pts = sorted(table)
    if css_mm <= pts[0][0]:
        return float(pts[0][1])
    if css_mm >= pts[-1][0]:
        return float(pts[-1][1])
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        if x1 <= css_mm <= x2:
            r = (css_mm - x1) / (x2 - x1)
            return round(y1 + r * (y2 - y1), 1)
    return float(pts[-1][1])


def css_mid(eq: dict) -> float:
    lo, hi = eq.get("css_range_mm", [0, 0])
    return round((lo + hi) / 2, 1)


def power_kw(eq: dict) -> float:
    return float(eq.get("power_kw", 0))
