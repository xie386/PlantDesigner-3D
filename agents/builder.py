"""产线操盘 Agent：把自然语言指令翻译成一串受控操作，并闭环验证。

约束（写死在提示词与工具层，不依赖模型自觉）：
  - 只能改设备清单与目标产量，坐标由布局引擎算
  - 任何数字以 run_balance 的返回为准，不许自己编
  - 改完必须重算，看到过载要继续修
"""
from __future__ import annotations

import json
import os

from core import equipment as eqm

MAX_ROUNDS = 6

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_equipment",
            "description": "查看设备库（型号、工段、能力、参考价）。改设备前先查一次，避免用不存在的型号。",
            "parameters": {
                "type": "object",
                "properties": {"stage": {"type": "string", "description": "可选：feed/primary/secondary/screening/washing"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_equipment",
            "description": "在某工段增加设备（同型号可加台数）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["feed", "primary", "secondary", "screening", "washing"]},
                    "eq_id": {"type": "string", "description": "设备库里的 id"},
                    "count": {"type": "integer", "description": "台数，默认 1"},
                },
                "required": ["role", "eq_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "replace_equipment",
            "description": "把某工段的设备换成另一种型号（例如二段换成更大一号）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {"type": "string", "enum": ["feed", "primary", "secondary", "screening", "washing"]},
                    "eq_id": {"type": "string"},
                },
                "required": ["role", "eq_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_equipment",
            "description": "减少某工段设备：多台时减一台，只剩一台时整段移除。",
            "parameters": {
                "type": "object",
                "properties": {"role": {"type": "string", "enum": ["feed", "primary", "secondary", "screening", "washing"]}},
                "required": ["role"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_css",
            "description": "调整破碎机排料口（mm）。排料口变小→产品更细但能力下降、循环负荷上升。",
            "parameters": {
                "type": "object",
                "properties": {"role": {"type": "string"}, "css_mm": {"type": "number"}},
                "required": ["role", "css_mm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_mesh",
            "description": "调整筛分主筛孔（mm）。筛孔与排料口的比值决定闭路通过率。",
            "parameters": {
                "type": "object",
                "properties": {"role": {"type": "string"}, "mesh_mm": {"type": "number"}},
                "required": ["role", "mesh_mm"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_target",
            "description": "调整目标成品产量（t/h）。",
            "parameters": {
                "type": "object",
                "properties": {"target_tph": {"type": "number"}},
                "required": ["target_tph"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_balance",
            "description": "重算产能平衡，返回各段处理量、能力、负荷率与过载清单。每次改动后都要调用。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

SYSTEM = """你是破碎筛分产线的操盘 Agent。你的工作是按用户的自然语言指令，调整产线配置。

铁律：
1. 你只能通过工具改设备清单和目标产量，不能自己编造任何负荷、产量、投资、回收期数字。
2. 所有数字必须来自 run_balance 的返回。改完必须调用 run_balance 看结果。
3. 如果 run_balance 显示有设备过载（负荷>100%），继续调整，直到目标产量能达成或不划算为止。
4. 设备型号优先用设备库的 id（形如 JC_PE900x1200、CC_SG1650、SC_2YK2160）；不确定就先 list_equipment。系统也接受型号名，但 id 最稳。
5. 每次工具调用后，用一句中文说明你的判断，再决定下一步。
6. 收尾时用 3 句以内总结：改了什么、现在能不能达标、代价大概多少。

当前产线状态：
{state}
"""


def _client():
    from openai import OpenAI

    key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not key or not key.startswith("sk-"):
        return None
    # 与 llm.py 同因：代理节点会间歇掐断 deepseek 的 TLS，必须直连
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        os.environ.pop(k, None)
    return OpenAI(
        api_key=key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=60.0,
        max_retries=2,
    )


def _state_brief(session) -> str:
    rows = []
    for d in session.design:
        eq = eqm.get(d["eq_id"])
        extra = []
        if d.get("css_mm"):
            extra.append(f"排料口 {d['css_mm']}mm")
        if d.get("mesh_mm"):
            extra.append(f"筛孔 {d['mesh_mm'][0]}mm")
        rows.append(
            f"  - {d['role']}: {eq['model']}（id={d['eq_id']}）×{d.get('count', 1)}"
            + (f"（{'，'.join(extra)}）" if extra else "")
        )
    return (
        f"目标 {session.target_tph:.0f} t/h，现状 {session.current_tph:.0f} t/h，"
        f"物料 {session.requirements.get('material') or '未知'}\n" + "\n".join(rows)
    )


def run_instruction(session, instruction: str, emit=None) -> dict:
    """执行一轮对话指令。返回 {ok, reply, log_from, log_to}。"""
    emit = emit or (lambda ev: None)
    client = _client()
    if client is None:
        return {"ok": False, "reply": "离线模式：对话改造需要配置 DEEPSEEK_API_KEY。"}

    log_from = len(session.log)
    messages = [
        {"role": "system", "content": SYSTEM.replace("{state}", _state_brief(session))},
        {"role": "user", "content": instruction.strip()[:2000]},
    ]
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
    reply = ""

    for _round in range(MAX_ROUNDS):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
                temperature=0.15,
                max_tokens=1200,
            )
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reply": f"模型调用失败：{exc}"}

        msg = resp.choices[0].message
        calls = getattr(msg, "tool_calls", None) or []
        if msg.content and msg.content.strip():
            reply = msg.content.strip()
            emit({"type": "say", "text": reply})

        assistant_msg = {"role": "assistant", "content": msg.content or ""}
        if calls:
            assistant_msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {"name": c.function.name, "arguments": c.function.arguments or "{}"},
                }
                for c in calls
            ]
        messages.append(assistant_msg)

        if not calls:
            break

        for c in calls:
            name = c.function.name
            try:
                args = json.loads(c.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = session.apply(name, args)
            emit(
                {
                    "type": "tool",
                    "name": name,
                    "args": args,
                    "ok": result.get("ok", False),
                    "message": result.get("message", ""),
                }
            )
            obs = {k: v for k, v in result.items() if k != "equipment"}
            if "equipment" in result:
                obs["equipment_count"] = len(result["equipment"])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": c.id,
                    "content": json.dumps(obs, ensure_ascii=False)[:2000],
                }
            )

    return {"ok": True, "reply": reply or "已完成调整。", "log_from": log_from, "log_to": len(session.log)}
