#!/usr/bin/env python3
"""The demo screen: one page, one sentence at a time, every stage visible — on the twin.

    cd sentence && ../sim/body/.venv/bin/python ui/server.py          # → http://127.0.0.1:8765

Standard library only (http.server + Server-Sent Events). The page sends a sentence; the hub runs
hub.pipeline() and streams each stage; a confirmation or a question back (NEED) is answered on the page.
"""
import json, os, sys, queue, threading, uuid, time, traceback
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import hub, record, say  # noqa: E402
import bench as BN  # noqa: E402

# What the screen shows for each part of the twin's body, and which of its QOM properties the page may set
# before power-on (anything else in a request is ignored).
PARTS = {
    "tmp105": {"label": "온도 센서 TMP105", "controls": [{"prop": "temperature", "label": "온도", "unit": "°C", "scale": 1000, "min": -10, "max": 50, "step": 0.25}]},
    "ds1338": {"label": "시계 DS1338", "controls": []},
    "lsm303dlhc_mag": {"label": "자기 센서 LSM303", "controls": [{"prop": "mag-x", "label": "자기장 X", "unit": "", "scale": 1, "min": -60000, "max": 60000, "step": 1000},
                                                             {"prop": "mag-y", "label": "자기장 Y", "unit": "", "scale": 1, "min": -60000, "max": 60000, "step": 1000}]},
    "pca9552": {"label": "LED 16개 PCA9552", "controls": []},
    "tmp421": {"label": "온도 센서 TMP421", "controls": [{"prop": "temperature0", "label": "보드 온도", "unit": "°C", "scale": 1000, "min": -10, "max": 80, "step": 0.25},
                                                       {"prop": "temperature1", "label": "원격 온도", "unit": "°C", "scale": 1000, "min": -10, "max": 120, "step": 0.25}]},
    "pca9554": {"label": "입출력 8핀 PCA9554", "controls": [{"prop": f"pin{i}", "label": f"스위치 {i}", "kind": "switch"} for i in range(4)]},
    "ssd0303": {"label": "OLED 화면 96×16 SSD0303", "controls": []},
}

# ── decision 8: the 5-minute demo — its own copy of the hub's memory, the body put back afterwards ──
DEMO = {"dir": None, "saved": None}

def demo_start():
    import shutil, tempfile, glob, say, body
    if hub.DEMO["on"]:
        return {"ok": True, "already": True}
    d = tempfile.mkdtemp(prefix="poke-demo-records-")
    for f in glob.glob(os.path.join(record.RECORDS, "*.json")):
        shutil.copy(f, d)
    DEMO["saved"] = {"records": record.RECORDS, "wiring": say.WIRING, "bench": list(say.BENCH), "name": body._NAME["name"]}
    DEMO["dir"] = d
    record.RECORDS = d
    hub.DEMO["on"] = True
    with LOCK: TWIN["leds_on"] = []
    return {"ok": True}

def demo_body(path):
    """Move to another body for the re-pronunciation step and bring the old-pronunciation sample card."""
    import body
    b = body.load_body_file(path)
    card = os.path.join(os.path.dirname(HERE), "teams", "product", "cards", "temp-old-0x48.json")
    record.import_card(json.load(open(card)), replace=True)
    return {"ok": True, "body": body.label(b)}

def demo_stop():
    import shutil, say, body
    if not hub.DEMO["on"]:
        return {"ok": True}
    sv = DEMO["saved"] or {}
    record.RECORDS = sv.get("records", record.RECORDS)
    say.WIRING = sv.get("wiring", say.WIRING)
    if sv.get("bench"): say.BENCH = sv["bench"]
    body._NAME["name"] = sv.get("name")
    hub.DEMO["on"] = False
    if DEMO["dir"]: shutil.rmtree(DEMO["dir"], ignore_errors=True)
    DEMO.update({"dir": None, "saved": None})
    with LOCK: TWIN["leds_on"] = []
    return {"ok": True}

def bench_info():
    import manifest as MF
    cat = say.catalog()
    at = {}
    for name, e in cat.items():
        try: at[int(str(e.get("default_addr", "")), 0)] = name
        except ValueError: pass
    for fn, e in MF.FN.items():
        at.setdefault(e["addr"], e.get("frag"))
    for frag, addrs in say.wiring().items():               # a part wired away from its catalog address
        for a in addrs: at[int(str(a), 0)] = frag
    defaults = BN.default_state()
    devs = []
    for model, qid, addr in say.BENCH:
        p = PARTS.get(model, {"label": model, "controls": []})
        ctl = [{**c, "key": f"{qid} {c['prop']}", "value": defaults.get(f"{qid} {c['prop']}")} for c in p["controls"]]
        devs.append({"model": model, "qid": qid, "addr": addr, "label": p["label"], "frag": at.get(addr),
                     "new": model not in [m for m, _, _ in BN.BASE], "controls": ctl})
    import body
    return {"name": BN.name(), "devices": devs, "body": body.label()}

def allowed_state(raw):
    """Only the listed properties of parts that are on the bench."""
    ok = {c["key"] for d in bench_info()["devices"] for c in d["controls"]}
    out = {}
    for k, v in (raw or {}).items():
        if k in ok and isinstance(v, (int, float, str)):
            out[k] = v
    return out

RUNS = {}            # id → {"answers": Queue}
# The twin starts every boot fresh; a real PCA9552 keeps its LEDs between boots (it is the world, not the node).
# The screen carries the LED state into the next boot so the twin behaves like the chip on the desk.
TWIN = {"leds_on": []}
STATE = {}           # the bench the page last set (temperature slider, switches): standing watches see it too
WATCH_STOP = threading.Event()

def watch_bench():
    with LOCK:
        b = dict(STATE)
        for i in range(16):
            b[f"l1 led{i}"] = "on" if i in TWIN["leds_on"] else "off"
    return b
LOCK = threading.Lock()
PORT = int(os.environ.get("POKE_UI_PORT", "8765"))

def _json(o):
    return json.dumps(o, ensure_ascii=False, default=str)

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):  # quiet
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store"); self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        u = urlparse(self.path); q = parse_qs(u.query)
        if u.path in ("/", "/index.html"):
            return self._send(200, open(os.path.join(HERE, "index.html"), "rb").read(), "text/html; charset=utf-8")
        if u.path == "/learn/status":
            import learn
            jid = (q.get("id") or [""])[0]
            since = int((q.get("since") or ["0"])[0] or 0)
            st = learn.status(jid)
            return self._send(200, _json({"status": st, "events": learn.events(jid, since)}))
        if u.path == "/watches":
            import watch
            return self._send(200, _json({"watches": [watch.describe(w) for w in watch.all_watches()][-12:],
                                          "events": watch.events(15), "caps": say.WATCH}))
        if u.path == "/bench":
            return self._send(200, _json(bench_info()))
        if u.path == "/twin":
            return self._send(200, _json(TWIN))
        if u.path == "/card":
            r = record.load((q.get("id") or [""])[0])
            if not r: return self._send(404, _json({"error": "no such record"}))
            import body
            ok, why = body.fits(r.get("op") or "", r.get("manifest")) if r.get("kind") == "op" else (True, [])
            return self._send(200, _json({**r, "lines": record.card_lines(r), "fits": ok, "fits_why": why, "body_now": body.label()}))
        if u.path == "/export":
            r = record.load((q.get("id") or [""])[0])
            if not r: return self._send(404, _json({"error": "no such record"}))
            b = _json(record.export_card(r)).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(b)))
            self.send_header("Content-Disposition", f'attachment; filename="poke-card-{r["id"]}.json"'); self.end_headers(); self.wfile.write(b); return
        if u.path == "/records":
            rs = [{"id": r["id"], "kind": r["kind"], "sentence": r["sentences"][0], "variants": len(r["sentences"]), "imported": bool(r.get("origin")),
                   "templated": r.get("templated"), "approved": bool(r.get("approval")), "traces": len(r.get("traces", []))}
                  for r in record.all_records()]
            return self._send(200, _json(rs))
        if u.path == "/say":
            return self._stream(q)
        self._send(404, _json({"error": "not found"}))

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        try: body = json.loads(self.rfile.read(n) or b"{}")
        except ValueError: body = {}
        if u.path == "/learn/start":
            if hub.DEMO["on"]:
                return self._send(400, _json({"error": "데모 중에는 새 부품을 배우지 않습니다"}))
            import learn
            try:
                return self._send(200, _json(learn.start(body.get("addr"))))
            except Exception as e:
                return self._send(400, _json({"error": f"{type(e).__name__}: {e}"}))
        if u.path == "/demo/start":
            return self._send(200, _json(demo_start()))
        if u.path == "/demo/body":
            if not hub.DEMO["on"]: return self._send(400, _json({"error": "demo is not running"}))
            return self._send(200, _json(demo_body(os.path.join(os.path.dirname(HERE), "teams", "product", "bodies", "tmp105-0x4a.json"))))
        if u.path == "/demo/stop":
            return self._send(200, _json(demo_stop()))
        if u.path == "/state":
            with LOCK: STATE.update(allowed_state(body.get("state") or {}))
            return self._send(200, _json({"ok": True}))
        if u.path == "/watch_stop":
            import watch
            ws = watch.stop_all("화면에서 모두 멈춤") if body.get("id") == "all" else [watch.stop(body.get("id") or "", "화면에서 멈춤")]
            return self._send(200, _json({"ok": True, "stopped": [w["id"] for w in ws if w]}))
        if u.path == "/reset":
            with LOCK: TWIN["leds_on"] = []
            return self._send(200, _json({"ok": True, "leds_on": []}))
        if u.path in ("/forget", "/unapprove"):
            r = record.load(body.get("id") or "")
            if not r: return self._send(404, _json({"error": "no such record"}))
            if u.path == "/forget":
                record.forget(r["id"])
            else:
                record.unapprove(r)
            return self._send(200, _json({"ok": True}))
        if u.path == "/import":
            try:
                r, msg = record.import_card(body.get("card"), replace=bool(body.get("replace")))
            except ValueError as e:
                return self._send(400, _json({"error": str(e)}))
            return self._send(200, _json({"ok": True, "id": r["id"], "message": msg}))
        if u.path == "/answer":
            run = RUNS.get(body.get("id"))
            if not run: return self._send(404, _json({"error": "no such run"}))
            run["answers"].put(body.get("text"))
            return self._send(200, _json({"ok": True}))
        self._send(404, _json({"error": "not found"}))

    def _stream(self, q):
        sentence = (q.get("sentence") or [""])[0].strip()
        if not sentence:
            return self._send(400, _json({"error": "no sentence"}))
        bench = {}
        try:
            if q.get("state"):
                st = allowed_state(json.loads(q["state"][0]))
                bench.update(st)
                with LOCK: STATE.update(st)
        except ValueError: pass
        try:
            if q.get("temp_c"): bench["t1 temperature"] = int(round(float(q["temp_c"][0]) * 1000))
        except ValueError: pass
        with LOCK:
            start_leds = set(TWIN["leds_on"])
            for i in range(16):
                bench[f"l1 led{i}"] = "on" if i in start_leds else "off"
        rid = uuid.uuid4().hex[:8]
        events, answers = queue.Queue(), queue.Queue()
        RUNS[rid] = {"answers": answers}

        def ask(kind, question):
            events.put({"stage": "ask", "kind": kind, "question": question, "run": rid})
            try: return answers.get(timeout=180)
            except queue.Empty: return None

        def work():
            try:
                for ev in hub.pipeline(sentence, bench=bench, ask=ask, from_watch=bool(q.get("from_watch"))):
                    if ev.get("stage") == "result" and isinstance((ev.get("truth") or {}).get("leds_on"), list):
                        # apply only what THIS boot changed: boots run side by side, and a read that started
                        # from an older picture must not undo a write that finished meanwhile
                        end = set(ev["truth"]["leds_on"])
                        with LOCK:
                            TWIN["leds_on"] = sorted((set(TWIN["leds_on"]) | (end - start_leds)) - (start_leds - end))
                            ev = {**ev, "truth": {**ev["truth"], "leds_on": TWIN["leds_on"]}}
                    events.put(ev)
            except Exception as e:                       # never leave the page hanging
                events.put({"stage": "stop", "kind": "error", "text": "허브에서 예상하지 못한 문제가 생겼습니다.", "hint": f"{type(e).__name__}: {e}"})
                traceback.print_exc()
            finally:
                events.put(None)
        threading.Thread(target=work, daemon=True).start()

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store"); self.send_header("Connection", "close"); self.end_headers()
        try:
            self.wfile.write(f"data: {_json({'stage': 'run', 'run': rid})}\n\n".encode()); self.wfile.flush()
            while True:
                try: ev = events.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n"); self.wfile.flush(); continue
                if ev is None: break
                self.wfile.write(f"data: {_json(ev)}\n\n".encode()); self.wfile.flush()
            self.wfile.write(b"data: {\"stage\": \"end\"}\n\n"); self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            answers.put(None)
        finally:
            RUNS.pop(rid, None)
            self.close_connection = True

def main():
    if os.environ.get("POKE_BODY"):                         # a twin body from a file (body.py), like say.py --body
        import body
        print("몸:", body.label(body.load_body_file(os.environ["POKE_BODY"])))
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    if os.environ.get("POKE_WATCH_RUNNER", "1") != "0":     # the hub keeps standing watches while the screen is up
        import watch
        threading.Thread(target=watch.run_forever, kwargs={"bench": watch_bench, "stop_event": WATCH_STOP, "log": lambda m: print(m, flush=True)},
                         daemon=True).start()
    srv.daemon_threads = True
    print(f"POKE 문장 데모: http://127.0.0.1:{PORT}  (실험대 {BN.name()}, Ctrl+C로 종료)")
    try: srv.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        demo_stop(); WATCH_STOP.set(); srv.server_close()

if __name__ == "__main__":
    main()
