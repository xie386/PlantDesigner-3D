"""PlantDesigner 3D — 一句话生成可交互三维产线方案

零配置启动：python app.py（自动找空闲端口并打开浏览器）
"""
from __future__ import annotations

import base64
import json
import os
import queue
import secrets
import shutil
import socket
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import agents.builder as builder
from agents import llm
from core import layout, planner
from core.ops import Session

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / ".env")

WEB = BASE / "web"
DATA = BASE / "data"

app = FastAPI(title="PlantDesigner 3D", version="0.2.0")


class PlanRequest(BaseModel):
    prompt: str = ""


@app.get("/api/health")
def health() -> JSONResponse:
    return JSONResponse(
        {
            "ok": True,
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            "key_present": bool(os.getenv("DEEPSEEK_API_KEY", "").startswith("sk-")),
            "no_key_mode": os.getenv("NO_KEY_MODE", "offline"),
        }
    )


@app.get("/api/equipment")
def equipment() -> JSONResponse:
    payload = json.loads((DATA / "equipment.json").read_text(encoding="utf-8"))
    return JSONResponse(payload)


def _build(prompt: str) -> dict:
    req, source = llm.parse_requirements(prompt)
    plan = planner.make_plan(req)
    site = req.get("site") or {}
    before = layout.build_scene(plan["base_devices"], plan["base"], variant="before", site=site)
    after = layout.build_scene(plan["target_devices"], plan["target"], variant="after", site=site)
    return {
        "source": source,
        "requirements": req,
        "before": before,
        "after": after,
        "retrofit": plan["retrofit"],
        "economics": plan["economics"],
        "notes": plan["notes"],
    }


@app.post("/api/plan")
def plan_endpoint(req: PlanRequest) -> JSONResponse:
    return JSONResponse(_build(req.prompt))


@app.get("/api/generate")
def generate(prompt: str = "") -> StreamingResponse:
    """SSE：逐条推送四个阶段的进度，最后一帧带完整场景。"""

    def evt(step: str, status: str, detail: str = "", data=None) -> str:
        payload = {"step": step, "status": status, "detail": detail}
        if data is not None:
            payload["data"] = data
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def stream():
        try:
            yield evt("parse", "running", "解析客户需求")
            req, source = llm.parse_requirements(prompt)
            who = "DeepSeek" if source == "deepseek" else "离线缓存"
            yield evt(
                "parse",
                "done",
                f"{who}：{req.get('material') or '物料未知'}，现状 {req.get('current_tph'):.0f} t/h → 目标 {req.get('target_tph'):.0f} t/h",
            )

            yield evt("plan", "running", "匹配设备、生成改造候选")
            result = planner.make_plan(req)
            session = _make_session(req, result)
            inv = sum(a["price_cny"] for a in result["retrofit"]) / 10000
            yield evt("plan", "done", f"改造 {len(result['retrofit'])} 项，设备投资 {inv:.0f} 万元")

            yield evt("balance", "running", "产能平衡与瓶颈定位")
            base, target = result["base"], result["target"]
            worst = max(base["nodes"], key=lambda n: n["utilization"])
            yield evt(
                "balance",
                "done",
                f"现状瓶颈 {worst['model']} {worst['utilization']*100:.0f}%，循环负荷 {base['circulating_load']}",
            )

            yield evt("layout", "running", "三维布局与输送带校验")
            site = req.get("site") or {}
            before = layout.build_scene(result["base_devices"], base, "before", site)
            after = layout.build_scene(result["target_devices"], target, "after", site)
            steep = [b for b in after["belts"] if not b["ok"]]
            yield evt("layout", "done", f"{len(after['nodes'])} 台设备，{len(after['belts'])} 条皮带，超坡 {len(steep)} 条")

            yield evt(
                "done",
                "done",
                "方案生成完成",
                {
                    "source": source,
                    "sid": session.sid,
                    "requirements": req,
                    "before": before,
                    "after": after,
                    "retrofit": result["retrofit"],
                    "economics": result["economics"],
                    "notes": result["notes"],
                },
            )
        except Exception as exc:  # noqa: BLE001
            yield evt("error", "error", f"生成失败：{exc}")

    return StreamingResponse(stream(), media_type="text/event-stream")


# ---------------------------------------------------------------- 会话与 Agent 操作
SESSIONS: dict[str, Session] = {}


def _make_session(req: dict, plan: dict) -> Session:
    sess = Session(
        requirements=req,
        baseline=plan["base_devices"],
        design=plan["target_devices"],
        target_tph=float(req.get("target_tph") or plan["target"]["throughput_tph"]),
        current_tph=float(req.get("current_tph") or plan["base"]["throughput_tph"]),
        ctx=plan.get("ctx") or {},
    )
    SESSIONS[sess.sid] = sess
    return sess


def _pack(session: Session, state: dict) -> dict:
    return {
        "sid": session.sid,
        "requirements": session.requirements,
        "before": state["scenes"]["before"],
        "after": state["scenes"]["after"],
        "economics": state["economics"],
        "retrofit": state["retrofit"],
        "notes": state["notes"],
        "log": session.log,
        "target_tph": session.target_tph,
    }


@app.get("/api/agent/stream")
def agent_stream(sid: str = "", q: str = "") -> StreamingResponse:
    """对话式操作：没有会话就按需求从零搭建（B），有会话就让 Agent 改（A）。"""

    def evt(kind: str, status: str, detail: str = "", data=None) -> str:
        payload = {"type": kind, "status": status, "detail": detail}
        if data is not None:
            payload["data"] = data
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def stream():
        session = SESSIONS.get(sid)

        # B 模式：从零搭建
        if session is None:
            yield evt("build", "running", "从零搭建：解析需求")
            req, source = llm.parse_requirements(q)
            plan = planner.make_plan(req)
            session = _make_session(req, plan)
            yield evt(
                "build",
                "done",
                f"{req.get('material') or '物料未识别'}，目标 {session.target_tph:.0f} t/h，{len(session.design)} 个工段",
                {"sid": session.sid},
            )
            state = session.state()
            yield evt("done", "done", "初始方案已生成", {**_pack(session, state), "source": source})
            return

        # A 模式：让 Agent 改
        yield evt("agent", "running", "理解指令…")
        qq: "queue.Queue" = queue.Queue()

        def work():
            try:
                res = builder.run_instruction(session, q, lambda e: qq.put(("ev", e)))
                qq.put(("res", res))
            except Exception as exc:  # noqa: BLE001
                qq.put(("err", str(exc)))
            finally:
                qq.put(None)

        threading.Thread(target=work, daemon=True).start()
        while True:
            item = qq.get()
            if item is None:
                break
            kind, payload = item
            if kind == "ev":
                text = payload.get("message") or payload.get("text") or ""
                yield evt(payload.get("type", "tool"), "step", text, payload)
            elif kind == "res":
                yield evt("agent", "done", (payload.get("reply") or "")[:240])
            elif kind == "err":
                yield evt("error", "error", f"出错了：{payload}")

        state = session.state()
        yield evt("done", "done", "调整完成", _pack(session, state))

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/agent/undo")
def agent_undo(payload: dict) -> JSONResponse:
    session = SESSIONS.get(str(payload.get("sid") or ""))
    if session is None:
        raise HTTPException(404, "session not found")
    res = session.undo()
    state = session.state()
    return JSONResponse({"ok": res.get("ok"), "message": res.get("message"), **_pack(session, state)})


# ---------------------------------------------------------------- 记录存储
RECORDS = BASE / "records"
RECORDS.mkdir(exist_ok=True)


class RecordIn(BaseModel):
    prompt: str = ""
    name: str = ""
    source: str = ""
    requirements: dict = {}
    retrofit: list = []
    economics: dict = {}
    notes: list = []
    before_png: str = ""
    after_png: str = ""
    panel_text: str = ""
    before_scene: dict = {}
    after_scene: dict = {}


def _decode_png(data: str) -> bytes | None:
    if not data or not data.startswith("data:image/png;base64,"):
        return None
    try:
        return base64.b64decode(data.split(",", 1)[1])
    except Exception:  # noqa: BLE001
        return None


@app.post("/api/records")
def save_record(r: RecordIn) -> JSONResponse:
    rid = time.strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(2)
    d = RECORDS / rid
    d.mkdir(parents=True, exist_ok=True)

    for name, data in (("before.png", r.before_png), ("after.png", r.after_png)):
        raw = _decode_png(data)
        if raw:
            (d / name).write_bytes(raw)

    meta = {
        "id": rid,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "name": r.name or "",
        "prompt": r.prompt,
        "source": r.source,
        "requirements": r.requirements,
        "retrofit": r.retrofit,
        "economics": r.economics,
        "notes": r.notes,
        "before_scene": r.before_scene,
        "after_scene": r.after_scene,
        "has_images": {
            "before": bool(_decode_png(r.before_png)),
            "after": bool(_decode_png(r.after_png)),
        },
    }
    (d / "record.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (d / "panel.txt").write_text(r.panel_text or "", encoding="utf-8")
    return JSONResponse({"id": rid, "dir": str(d)})


@app.get("/api/records")
def list_records() -> JSONResponse:
    items = []
    for d in sorted(RECORDS.iterdir(), reverse=True):
        f = d / "record.json"
        if not d.is_dir() or not f.exists():
            continue
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        req = m.get("requirements") or {}
        eco = m.get("economics") or {}
        items.append(
            {
                "id": m.get("id"),
                "name": m.get("name") or "",
                "created_at": m.get("created_at"),
                "prompt": (m.get("prompt") or "")[:60],
                "material": req.get("material"),
                "current_tph": req.get("current_tph"),
                "target_tph": req.get("target_tph"),
                "payback_months": eco.get("payback_months"),
                "investment_cny": eco.get("investment_cny"),
                "retrofit_count": len(m.get("retrofit") or []),
                "has_images": m.get("has_images"),
            }
        )
    return JSONResponse({"records": items[:60]})


@app.patch("/api/records/{rid}")
def rename_record(rid: str, payload: dict) -> JSONResponse:
    f = RECORDS / rid / "record.json"
    if not f.exists():
        raise HTTPException(404, "record not found")
    m = json.loads(f.read_text(encoding="utf-8"))
    m["name"] = str(payload.get("name") or "").strip()[:60]
    f.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"id": rid, "name": m["name"]})


@app.delete("/api/records/{rid}")
def delete_record(rid: str) -> JSONResponse:
    d = RECORDS / rid
    if not d.is_dir():
        raise HTTPException(404, "record not found")
    shutil.rmtree(d)
    return JSONResponse({"deleted": rid})


@app.get("/records/{rid}/{name}")
def record_asset(rid: str, name: str) -> FileResponse:
    if name not in ("before.png", "after.png", "panel.txt", "record.json"):
        raise HTTPException(404, "not found")
    path = RECORDS / rid / name
    if not path.exists():
        raise HTTPException(404, "not found")
    return FileResponse(path)


@app.get("/report/{rid}", response_class=HTMLResponse)
def report(rid: str) -> HTMLResponse:
    d = RECORDS / rid
    meta_path = d / "record.json"
    if not meta_path.exists():
        raise HTTPException(404, "record not found")
    m = json.loads(meta_path.read_text(encoding="utf-8"))
    panel = (d / "panel.txt").read_text(encoding="utf-8") if (d / "panel.txt").exists() else ""
    req = m.get("requirements") or {}
    eco = m.get("economics") or {}
    has_scenes = bool(m.get("before_scene")) and bool(m.get("after_scene"))
    if has_scenes:
        imgs = "".join(
            f'<div class="view-wrap"><div class="view" id="view-{k}"></div>'
            f'<div class="view-labels" id="labels-{k}"></div>'
            f'<div class="cap">{"现状" if k == "before" else "改造后"}</div></div>'
            for k in ("before", "after")
        )
    else:
        imgs = "".join(
            f'<figure><img src="/records/{rid}/{k}.png" alt="{k}"><figcaption>{"现状" if k == "before" else "改造后"}</figcaption></figure>'
            for k in ("before", "after")
            if (d / f"{k}.png").exists()
        )
    notes = "".join(f"<li>{n}</li>" for n in (m.get("notes") or []))
    retrofit = "".join(
        f"<li>{'更换' if a.get('action') == 'replace' else '增加'} {a.get('model')}"
        f"（{a.get('price_cny', 0) / 10000:.0f} 万）</li>"
        for a in (m.get("retrofit") or [])
    )
    scripts = ""
    if has_scenes:
        payload = json.dumps(
            {"before": m.get("before_scene"), "after": m.get("after_scene")}, ensure_ascii=False
        ).replace("</", "<\\/")
        scripts = (
            "<script>window.__RECORD__=" + payload + ";</script>\n"
            '<script type="importmap">{"imports":{"three":"/web/lib/three/three.module.js",'
            '"three/addons/":"/web/lib/three/addons/"}}</script>\n'
            '<script type="module" src="/web/report.js"></script>'
        )

    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>方案记录 {rid}</title>
<style>
 body{{background:#0e1116;color:#e6edf3;font:14px/1.7 "Microsoft YaHei",system-ui;margin:0;padding:28px 34px}}
 h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#8b98a5;font-size:12px;margin-bottom:20px}}
 .views{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:14px}}
 .view-wrap{{position:relative;height:56vh;background:#151a21;border:1px solid #2a333f;border-radius:10px;overflow:hidden}}
 .view,.view-labels{{position:absolute;inset:0}}
 .view-labels{{pointer-events:none}}
 .view-wrap canvas{{display:block;outline:none}}
 .cap{{position:absolute;left:10px;bottom:8px;font-size:12px;color:#8b98a5;background:rgba(14,17,22,.72);padding:2px 9px;border-radius:999px;border:1px solid #2a333f}}
 #pick-info{{font-size:12px;color:#8b98a5;background:#151a21;border:1px solid #2a333f;border-radius:8px;padding:8px 12px;margin-bottom:18px;min-height:20px}}
 #pick-info b{{color:#e6edf3}}
 .equip-label{{font-size:11px;color:#cfd8e3;background:rgba(14,17,22,.72);border:1px solid rgba(90,105,120,.55);padding:2px 7px;border-radius:4px;white-space:nowrap;transform:translateY(-6px)}}
 .equip-label.over{{color:#ffb3a8;border-color:rgba(255,107,87,.7)}}
 .site-label{{font-size:11px;color:#5f6d7c}}
 figure{{margin:0;background:#151a21;border:1px solid #2a333f;border-radius:10px;overflow:hidden}}
 figure img{{width:100%;display:block}} figcaption{{padding:6px 10px;font-size:12px;color:#8b98a5}}
 .cards{{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:18px}}
 .card{{background:#151a21;border:1px solid #2a333f;border-radius:8px;padding:8px 12px;font-size:12px;color:#8b98a5}}
 .card b{{display:block;font-size:15px;color:#e6edf3}}
 h2{{font-size:13px;color:#8b98a5;margin:18px 0 6px}}
 ul{{margin:0;padding-left:18px;font-size:13px}} li{{margin:3px 0}}
 pre{{white-space:pre-wrap;background:#151a21;border:1px solid #2a333f;border-radius:8px;padding:12px;font-size:12.5px;color:#c3ccd6}}
</style></head><body>
<h1>{m.get('name') or '产线方案记录'} · {rid}</h1>
<div class="sub">{m.get('created_at')} ｜ 解析来源：{m.get('source')} ｜ {req.get('material') or '物料未识别'} ｜
 {req.get('current_tph') or '—'} → {req.get('target_tph') or '—'} t/h</div>
<div class="views">{imgs}</div>
<div id="pick-info">左键拖动旋转 · 滚轮缩放 · 右键平移 · 点击设备看参数与负荷</div>
<div class="cards">
  <div class="card">改造总投<b>{eco.get('investment_cny', 0) / 10000:.0f} 万元</b></div>
  <div class="card">月增现金流<b>{eco.get('monthly_gain_cny', 0) / 10000:.1f} 万元</b></div>
  <div class="card">静态回收期<b>{eco.get('payback_months') or '—'} 个月</b></div>
  <div class="card">吨电耗变化<b>{eco.get('ton_power_delta', 0):+.2f} kWh/t</b></div>
  <div class="card">吨毛利<b>{eco.get('margin_cny_t', 0)} 元/t</b></div>
</div>
<h2>改造清单</h2><ul>{retrofit or '<li>无需改造</li>'}</ul>
<h2>判断依据</h2><ul>{notes}</ul>
<h2>右侧栏原文（存档）</h2><pre>{panel}</pre>
{scripts}
</body></html>"""
    return HTMLResponse(html)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB / "index.html")


app.mount("/web", StaticFiles(directory=str(WEB)), name="web")


def _free_port(start: int = 8848) -> int:
    for port in range(start, start + 40):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def main() -> None:
    port = int(os.getenv("PORT") or 0) or _free_port()
    if os.getenv("PORT"):
        try:
            port = _free_port(int(os.getenv("PORT")))
        except ValueError:
            port = _free_port()
    url = f"http://127.0.0.1:{port}"
    print(f"\n  PlantDesigner 3D  ->  {url}\n")
    if os.getenv("NO_BROWSER") != "1":
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
