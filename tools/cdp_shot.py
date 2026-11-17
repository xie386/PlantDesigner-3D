"""CDP 截图与诊断工具（用于无头验证三维渲染）

用法：
    .venv/Scripts/python.exe tools/cdp_shot.py http://127.0.0.1:8848/ out.png

需要先用 --remote-debugging-port=9222 启动 Chrome/Edge。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import urllib.request

import websockets


async def main(url: str, out: str, wait_ms: int = 3500) -> None:
    targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/list", timeout=5).read())
    page = next((t for t in targets if t["type"] == "page"), targets[0])
    ws_url = page["webSocketDebuggerUrl"]

    logs: list[str] = []
    errors: list[str] = []
    async with websockets.connect(ws_url, max_size=64 * 1024 * 1024) as ws:
        msg_id = 0

        async def send(method: str, **params):
            nonlocal msg_id
            msg_id += 1
            await ws.send(json.dumps({"id": msg_id, "method": method, "params": params}))
            return msg_id

        await send("Page.enable")
        await send("Runtime.enable")
        await send("Log.enable")
        await send("Network.enable")
        await send("Network.setCacheDisabled", cacheDisabled=True)
        await send("Page.navigate", url=url)

        loaded = False
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=25)
            evt = json.loads(raw)
            m = evt.get("method")
            if m == "Page.loadEventFired":
                loaded = True
                break
            if m == "Runtime.exceptionThrown":
                d = evt["params"]["exceptionDetails"]
                errors.append(f"exception: {d.get('text')} {d.get('exception', {}).get('description', '')}")
            if m == "Runtime.consoleAPICalled" and evt["params"]["type"] in ("error", "warning"):
                logs.append("console: " + " ".join(str(a.get("value", "")) for a in evt["params"]["args"]))

        if len(sys.argv) > 4:
            await send("Runtime.evaluate", expression=sys.argv[4], returnByValue=True)

        # 等待期间必须持续读取消息，否则 CDP 会因 keepalive 超时断开
        loop = asyncio.get_running_loop()
        deadline = loop.time() + wait_ms / 1000
        while loop.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            evt = json.loads(raw)
            m = evt.get("method")
            if m == "Runtime.exceptionThrown":
                d = evt["params"]["exceptionDetails"]
                errors.append(f"exception: {d.get('text')} {d.get('exception', {}).get('description', '')}")
            if m == "Runtime.consoleAPICalled" and evt["params"]["type"] in ("error", "warning"):
                logs.append("console: " + " ".join(str(a.get("value", "")) for a in evt["params"]["args"]))

        # 可选：用真实鼠标事件（CDP Input）点一下，验证拾取/拖动链路
        click_js = os.environ.get("CDP_CLICK_JS")
        if click_js:
            cid = await send("Runtime.evaluate", expression=click_js, returnByValue=True)
            val = None
            while val is None:
                evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                if evt.get("id") == cid:
                    val = evt.get("result", {}).get("result", {}).get("value")
            if val:
                cx, cy = [float(v) for v in str(val).split(",")[:2]]
                await send("Input.dispatchMouseEvent", type="mouseMoved", x=cx, y=cy)
                await send("Input.dispatchMouseEvent", type="mousePressed", x=cx, y=cy, button="left", clickCount=1)
                await send("Input.dispatchMouseEvent", type="mouseReleased", x=cx, y=cy, button="left", clickCount=1)
                await asyncio.sleep(0.6)

        probe = await send(
            "Runtime.evaluate",
            expression="""(() => {
              const c = document.querySelector('canvas');
              const s = document.getElementById('status');
              return JSON.stringify({
                canvas: c ? c.width + 'x' + c.height : 'none',
                labels: document.querySelectorAll('.equip-label').length,
                status: s ? s.textContent : '',
                errs: (window.__errs || []).slice(0, 5),
                probe: window.__probe || null,
                hit: (() => {
                  const r = c ? c.getBoundingClientRect() : null;
                  if (!r) return null;
                  const el = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                  return el ? el.tagName : null;
                })(),
                webgl: (() => { try { const t=document.createElement('canvas');
                  return !!(t.getContext('webgl2') || t.getContext('webgl')); } catch(e) { return 'err'; } })()
              });
            })()""",
            returnByValue=True,
        )
        res = None
        while res is None:
            evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if evt.get("id") == probe:
                res = evt
        info = json.loads(res["result"]["result"]["value"])

        shot_id = await send("Page.captureScreenshot", format="png")
        data = None
        while data is None:
            evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=25))
            if evt.get("id") == shot_id:
                data = evt["result"]["data"]

    with open(out, "wb") as f:
        f.write(base64.b64decode(data))

    print(json.dumps({"loaded": loaded, "page": info, "console": logs[:8], "errors": errors[:8], "png": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3500))
