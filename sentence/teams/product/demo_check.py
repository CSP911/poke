"""F2: drive the 5-minute demo in headless Chrome (DevTools protocol) to the end — the page's own button,
its own SSE, real boots — and save screenshots. Needs the demo server on :8765.
    cd sentence && ../sim/body/.venv/bin/python teams/product/demo_check.py OUTDIR"""
import asyncio, base64, json, os, subprocess, sys, tempfile, time, urllib.request
import websockets
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
OUT = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
PORT = 9333

async def main():
    prof = tempfile.mkdtemp(prefix="demo-chrome-")
    ch = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", f"--remote-debugging-port={PORT}", f"--user-data-dir={prof}",
                           "--window-size=1280,1600", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(100):
            try:
                tabs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json").read()); break
            except OSError:
                time.sleep(0.1)
        ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t.get("type") == "page")
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            n = 0
            async def call(method, **params):
                nonlocal n; n += 1; my = n
                await ws.send(json.dumps({"id": my, "method": method, "params": params}))
                while True:
                    m = json.loads(await ws.recv())
                    if m.get("id") == my: return m.get("result", {})
            async def ev(expr):
                r = await call("Runtime.evaluate", expression=expr, returnByValue=True)
                return r.get("result", {}).get("value")
            async def shot(name):
                r = await call("Page.captureScreenshot", format="png")
                open(os.path.join(OUT, name), "wb").write(base64.b64decode(r["data"]))
            await call("Page.enable")
            await call("Page.navigate", url="http://127.0.0.1:8765/#demo")
            t0, seen, steps = time.time(), set(), []
            while time.time() - t0 < 300:
                await asyncio.sleep(1.0)
                text = await ev("document.getElementById('demotext') && document.getElementById('demotext').textContent") or ""
                k = text[:4]
                if k and k not in seen:
                    seen.add(k); steps.append(f"{round(time.time() - t0)}s {text[:70]}")
                    print(steps[-1], flush=True)
                if "6/8" in text and "body" not in seen:
                    await asyncio.sleep(12); await shot("demo-6-body.png"); seen.add("body")
                if await ev("document.body.dataset.demo") == "done":
                    await asyncio.sleep(1); await shot("demo-end.png")
                    tc = await ev("document.getElementById('cardpanel').innerText")
                    print("END", round(time.time() - t0), "s")
                    print("card:", (tc or "")[:600])
                    return 0
            await shot("demo-timeout.png"); print("TIMEOUT"); return 1
    finally:
        ch.terminate()
sys.exit(asyncio.run(main()))
