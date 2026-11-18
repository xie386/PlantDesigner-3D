"""Agent 层：把客户原话解析成结构化需求。

铁律：模型只做理解与表述，不产出任何派生数字。解析结果要过校验，
校验不过就用离线案例兜底，绝不让流水线断在半路。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from core import equipment as eqm

DATA = Path(__file__).resolve().parent.parent / "data"
CASES = DATA / "cases"

SYSTEM = """你是选矿/砂石产线的需求解析助手。把客户的口语描述解析成 JSON。

只输出 JSON，不要任何解释文字。字段：
{
  "material": "石灰岩/花岗岩/河卵石/铁矿石等，未知填 null",
  "hardness": "soft|medium|hard，未知按 material 推断，都不确定填 medium",
  "design_tph": 数字或 null,
  "current_tph": 数字或 null,
  "target_tph": 数字或 null,
  "products": ["0-5","5-10"...] 或 [],
  "symptoms": ["粉多","电费高","粒形差","产能不足"...],
  "site": {"w": 数字或 null, "d": 数字或 null},
  "shift_hours": 数字或 null,
  "price_cny_per_t": 数字或 null,
  "existing": [
    {"role": "feed|primary|secondary|screening|washing", "eq_id": "设备库里的 id", "count": 1, "css_mm": 数字或 null, "mesh_mm": [数字] 或 null}
  ]
}

规则：
1. 客户提到的设备必须映射到下面设备库里的 id；映射不上就整条不要，不要编 id。
2. 客户说的“时产/每小时多少吨”是 target_tph 或 design_tph，按语义判断。
3. 客户说“现在只能出 230”是 current_tph。
4. 没有提到的字段填 null 或 []，不要猜。
5. 输出必须是合法 JSON 对象。

设备库（id | 型号 | 工艺段 | 能力 t/h）：
__CATALOG__
"""


def _catalog() -> str:
    lib = eqm.load_library()
    rows = []
    for eq in lib.values():
        cap = eq.get("capacity_tph")
        if isinstance(cap, list) and cap and isinstance(cap[0], list):
            cap_txt = f"{cap[0][1]}–{cap[-1][1]}"
        elif isinstance(cap, list):
            cap_txt = f"{cap[0]}–{cap[1]}"
        elif cap:
            cap_txt = str(cap)
        else:
            cap_txt = "-"
        rows.append(f"{eq['id']} | {eq['model']} | {eq['stage']} | {cap_txt}")
    return "\n".join(rows)


def _client():
    from openai import OpenAI

    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key or not key.startswith("sk-"):
        return None
    # 关键：清掉代理环境变量。api.deepseek.com 直连可达；若走系统代理，
    # 代理节点对 deepseek 的 TLS 握手会间歇性被掐（SSLEOFError → 请求超时）。
    # openai SDK 底层 httpx 读大写 HTTP(S)_PROXY，环境里 NO_PROXY 覆盖不全时会绕道代理。
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    return OpenAI(
        api_key=key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=45.0,
        max_retries=2,
    )


OUT_OF_SCOPE_TERMS = ["磨矿", "磨机", "浮选", "磁选", "浸出", "氰化", "烧结", "球团", "重介质", "洗煤", "选煤", "焙烧"]


def _scope(req: dict, prompt: str) -> dict:
    """标出超出破碎筛分范围的工序，让报告诚实说明边界。"""
    req["out_of_scope"] = [t for t in OUT_OF_SCOPE_TERMS if t in (prompt or "")]
    return req


def offline_case() -> dict:
    path = CASES / "zhang_line.json"
    return json.loads(Path(path).read_text(encoding="utf-8"))


def parse_requirements(prompt: str) -> tuple[dict, str]:
    """返回 (需求, 来源)。来源 = "deepseek" 或 "offline"。"""
    client = _client()
    if client is None:
        return _scope(offline_case(), prompt), "offline"

    model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    messages = [
        {"role": "system", "content": SYSTEM.replace("__CATALOG__", _catalog())},
        {"role": "user", "content": prompt.strip()[:4000]},
    ]

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1,
                max_tokens=1200,
            )
            text = (resp.choices[0].message.content or "").strip()
            if not text:
                raise ValueError("模型返回空内容")
            data = json.loads(text)
            return _scope(validate(data), prompt), "deepseek"
        except Exception as exc:  # noqa: BLE001
            if attempt == 1:
                print(f"[agent] 解析失败，走离线兜底：{exc}")
                return _scope(offline_case(), prompt), "offline"
            messages.append({"role": "assistant", "content": "{解析失败}"})
            messages.append({"role": "user", "content": "上面的输出不是合法 JSON。请重新只输出一个合法 JSON 对象。"})

    return _scope(offline_case(), prompt), "offline"


def validate(data: dict) -> dict:
    """把模型给的 JSON 收敛成求解器能吃的形状。不能用的字段直接丢弃。"""
    lib = eqm.load_library()
    out: dict = {
        "material": data.get("material"),
        "hardness": data.get("hardness") or "medium",
        "design_tph": _num(data.get("design_tph")),
        "current_tph": _num(data.get("current_tph")),
        "target_tph": _num(data.get("target_tph")),
        "products": [p for p in (data.get("products") or []) if isinstance(p, str)],
        "symptoms": [s for s in (data.get("symptoms") or []) if isinstance(s, str)],
        "site": data.get("site") or {},
        "shift_hours": _num(data.get("shift_hours")),
        "price_cny_per_t": _num(data.get("price_cny_per_t")),
        "existing": [],
    }

    for dev in data.get("existing") or []:
        if not isinstance(dev, dict):
            continue
        eq_id = dev.get("eq_id")
        if eq_id not in lib:
            eq_id = _match_model(lib, dev.get("model") or dev.get("name") or "")
        if not eq_id:
            continue
        eq = lib[eq_id]
        role = dev.get("role") if dev.get("role") in ("feed", "primary", "secondary", "screening", "washing") else _role_of(eq)
        if not role:
            continue
        item = {"role": role, "eq_id": eq_id, "count": int(dev.get("count") or 1)}
        if eq.get("mesh_mm"):
            item["mesh_mm"] = dev.get("mesh_mm") or eq["mesh_mm"]
        if eq.get("css_range_mm"):
            css = _num(dev.get("css_mm"))
            if css is None:
                lo0, hi0 = eq["css_range_mm"]
                if role == "primary":
                    # 粗碎排料口与筛孔无关，取偏粗的一档
                    css = round(lo0 + 0.6 * (hi0 - lo0), 1)
                else:
                    # 中/细碎按筛孔反推（css ≈ 0.8 × 筛孔）
                    mesh = (item.get("mesh_mm") or [25])[0]
                    css = round(mesh * 0.8, 1)
            lo, hi = eq["css_range_mm"]
            item["css_mm"] = max(lo, min(hi, css))
        out["existing"].append(item)

    # 给料机是线头，客户常常不提；缺了就按现状产量补一台
    if out["existing"] and not any(d["role"] == "feed" for d in out["existing"]):
        from core import planner as _planner

        out["existing"].insert(
            0, {"role": "feed", "eq_id": _planner._pick("feed", out["current_tph"] or 300), "count": 1}
        )

    # 兜底：产量字段缺失时按常见情形补齐
    if not out["target_tph"]:
        out["target_tph"] = out["design_tph"] or 300
    if not out["current_tph"]:
        out["current_tph"] = round(out["target_tph"] * 0.75)
    return out


def _role_of(eq: dict) -> str | None:
    stage = eq.get("stage")
    if stage in ("feed", "primary", "secondary", "screening", "washing"):
        return stage
    return None


def _match_model(lib: dict, text: str) -> str | None:
    text = (text or "").replace(" ", "").replace("×", "x").lower()
    if not text:
        return None
    for eq_id, eq in lib.items():
        model = eq["model"].replace(" ", "").replace("×", "x").lower()
        if model in text or text in model:
            return eq_id
    return None


def _num(v) -> float | None:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
