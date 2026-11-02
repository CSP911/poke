"""knowledge — what the hub has learned about parts, kept in its RouteMind map (poke-ontology :8120), and used
again at the next call instead of being learned or read again.

    knowledge.regenerate("why")          rebuild the map from the library + sentence/incubated, publish it
    knowledge.parts()                    the map's part rows: [{"id", "name", "one_liner", "fetch"}]
    knowledge.recognise([0x19])          before learning an unknown address: try every part the map knows that has
                                         an identify() — one read-only boot per address — → {0x19: "emc1413"}
    knowledge.route(sentence, cat)       pick the parts a sentence needs from the map's table (one short model call),
                                         read only those nodes → (cat subset, route record)

The map routes and explains; it is never the source of a permission (writes, ranges, counts stay in
incubated/fn.json, safety-reviewed). Recognition only reads: identify() functions are read-only and the image's
allow list lets the node read the one address and nothing else.
"""
import os, sys, re, json, time, hashlib, subprocess
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "sim", "body"))
import routemind

def available():
    return routemind.available()

def regenerate(msg="incubation: parts learned"):
    """tools/ontology.py → commit the container's copy → restart it → wait until it answers."""
    subprocess.run([sys.executable, os.path.join(ROOT, "tools", "ontology.py")], check=True, capture_output=True)
    repo = os.path.join(ROOT, "ontology", "run", "repo")
    subprocess.run(["git", "-C", repo, "add", "-A"], check=True, capture_output=True)
    subprocess.run(["git", "-C", repo, "-c", "user.name=poke", "-c", "user.email=poke@local", "commit", "-q", "-m", msg], capture_output=True)
    subprocess.run(["docker", "restart", "poke-ontology"], capture_output=True)
    for _ in range(100):
        if available() and any(p["id"].startswith("part-") for p in parts()):
            return True
        time.sleep(0.2)
    return False

def parts():
    d = routemind._get("/v1/regions/devices")
    return [{"id": e["id"], "name": e["id"][5:], "one_liner": e["one_liner"], "fetch": e["fetch"]}
            for e in d.get("entries", []) if e["id"].startswith("part-")]

def read(fetch):
    return routemind.read(fetch)

# ── recognise: a part the map already knows, at an address nobody told the hub about ────────────────
def _identify_fn(body):
    m = re.search(r"`static int (\w+_identify)\s*\(", body)
    return m.group(1) if m else None

def recognise(addrs, log=print):
    """For each unknown address: every part in the map with an identify() is tried there, all in one boot
    (read-only, that address only). Exactly one '1' → that part. → {addr: part name}, plus what was read."""
    import say, bench
    found, trail = {}, []
    cands = []
    for p in parts():
        body = read(p["fetch"])
        fn = _identify_fn(body)
        if fn and os.path.exists(os.path.join(say.FRAGS, f"{p['name']}.h")):
            cands.append((p["name"], fn))
        trail.append(p["fetch"])
    if not cands:
        return found, {"read": trail, "candidates": []}
    for a in addrs:
        inc = "".join(f'#include "{n}.h"\n' for n, _ in cands)
        body = "".join(f'    report_int("{n}", {fn}({a:#x}));\n' for n, fn in cands)
        src = f'#include "rt.h"\n{inc}void op_main(void) {{\n{body}}}\n'
        allow = {"bus": "i2c1", "devices": [{"addr": a, "read": True, "pointer": True, "write_regs": [], "max_write": 1}]}
        tag = "recog-" + hashlib.sha1(src.encode()).hexdigest()[:10]
        try:
            _, _, img = say.build(src, tag, allow=allow)
        except RuntimeError as e:
            log(f"[map] recognition image did not build: {str(e)[-300:]}"); continue
        b = bench.boot(img)
        res = b.get("result") or {}
        ones = [n for n, _ in cands if res.get(n) == 1]
        log(f"[map] 0x{a:02x}: tried {len(cands)} known parts' identify() in one read-only boot → "
            + (", ".join(f"{n}={res.get(n)}" for n, _ in cands)))
        if len(ones) == 1 and not res.get("error"):
            found[a] = ones[0]
    return found, {"read": trail, "candidates": [n for n, _ in cands]}

# ── route: the parts a sentence needs, chosen from the map's table ───────────────────────────────────
ROUTE_SYSTEM = ("You route a request to the parts a small machine has. You get the request and a table of parts, "
                "one row each: name, what it is, how sure the hub is. Reply with ONLY the names of the parts the request "
                "needs, comma separated, or NONE. Pick as few as the request needs.")

def route(sentence, cat):
    """→ (cat restricted to the routed parts, {"rows", "picked", "read"}). Falls back to cat when the map is down."""
    if not available():
        return cat, {"fallback": "map not available"}
    rows = [p for p in parts() if p["name"] in cat]
    if not rows:
        return cat, {"fallback": "no part of this body is on the map"}
    import claude_cli
    table = "\n".join(f"- {p['name']}: {p['one_liner']}" for p in rows)
    ans = claude_cli.ask(ROUTE_SYSTEM, f"Request: {sentence}\n\nParts:\n{table}").strip()
    picked = [n.strip() for n in re.split(r"[,\s]+", ans) if n.strip() in cat]
    if not picked:
        return cat, {"rows": len(rows), "picked": [], "answer": ans[:200], "fallback": "router picked nothing usable"}
    read_paths = []
    for p in rows:
        if p["name"] in picked:
            read(p["fetch"]); read_paths.append(p["fetch"])
    return {n: cat[n] for n in picked}, {"rows": len(rows), "picked": picked, "read": read_paths}
