"""registry — the hub's body registry: which edges it has, what is attached to each, what those parts can do,
and how a plan written against capabilities becomes a per-node image (docs/design/hub-registry.md).

    L0 nodes        registry/nodes/<node>.json
    L1 attachments  registry/attachments/<node>.json, or the twin's declared parts (a scan writes the real ones)
    L2 parts        registry/parts/<part>.json      (registry_build.py, from the incubation records)
    L3 caps         registry/caps/<cap>.json        (a small vocabulary, owned by a person)

The model sees only L3 (capability instances, their specs and policy); chip names, registers and addresses stay
here. A step's image gets a generated header (poke_body.h) with only the capabilities the plan granted that
step: the op calls cap_*(), the header binds each to the fragment call at the attachment's address, and the
bus allow list is derived from the bound functions — what the step may do is what its header contains.
"""
import os, sys, re, json, glob
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
REG = os.path.join(HERE, "registry")

def _load(kind):
    return {os.path.splitext(os.path.basename(f))[0]: json.load(open(f)) for f in sorted(glob.glob(os.path.join(REG, kind, "*.json")))}

def nodes(): return _load("nodes")
def parts(): return _load("parts")
def caps(): return _load("caps")

def attachments(node_id):
    f = os.path.join(REG, "attachments", f"{node_id}.json")
    if os.path.exists(f):
        return json.load(open(f))
    n = nodes()[node_id]
    out = []
    for t in n.get("twin", []):
        model, qid, addr, part, label = t[:5]
        out.append({"id": f"{node_id}/i2c1/{addr}", "node": node_id, "bus": "i2c1", "addr": addr, "part": part,
                    "label": label, "params": {k: v for k, v in (t[5] if len(t) > 5 else {}).items() if k not in ("channel_names", "roles")},
                    "channel_names": (t[5] if len(t) > 5 else {}).get("channel_names", {}),
                    "roles": (t[5] if len(t) > 5 else {}).get("roles", []), "how": "declared (twin)", "twin_id": qid})
    return out

def _const(*bits):
    return "CAP_" + re.sub(r"[^A-Z0-9]+", "_", "_".join(str(b) for b in bits if b != "").upper()).strip("_")

def _uniq(used, name, thing):
    """One constant per *thing* (a sensor channel, a light, a curtain): the same name for its set and read
    capabilities, a suffix only when two different things would collide."""
    if used.get(name, thing) == thing:
        used[name] = thing; return name
    k = 2
    while used.get(f"{name}_{k}", thing) != thing: k += 1
    used[f"{name}_{k}"] = thing; return f"{name}_{k}"

# What a pin wired to a role does, per GPIO-expander part: how to set one pin, how to read all levels.
PIN_OPS = {
    "pca9554": {"set": "pca9554_set_pin(ADDR, {p}, {v})", "read": "pca9554_read(ADDR, &l_)", "fset": "pca9554_set_pin", "fread": "pca9554_read"},
    "pca9555": {"set": "pca9555_set_pin(ADDR, {p}, {v})", "read": "pca9555_read(ADDR, &l_)", "fset": "pca9555_set_pin", "fread": "pca9555_read"},
}
# role → [(capability, C body)] — bodies use SET(pin, level), READ, and the role's pins by name
ROLES = {
    "switch":  [("switch.set", "return SET({pin}, on ? 1 : 0);"),
                ("switch.read", "unsigned l_; int r_ = READ; if (!r_) *on = (l_ >> {pin}) & 1; return r_;")],
    "contact": [("contact.read", "unsigned l_; int r_ = READ; if (!r_) *open = (l_ >> {pin}) & 1; return r_;")],
    "motion":  [("motion.read", "unsigned l_; int r_ = READ; if (!r_) *present = (l_ >> {pin}) & 1; return r_;")],
    "leak":    [("leak.read", "unsigned l_; int r_ = READ; if (!r_) *wet = (l_ >> {pin}) & 1; return r_;")],
    "cover":   [("cover.move", "int r_ = 0; if (dir > 0) { r_ |= SET({close}, 0); r_ |= SET({open}, 1); } "
                               "else if (dir < 0) { r_ |= SET({open}, 0); r_ |= SET({close}, 1); } "
                               "else { r_ |= SET({open}, 0); r_ |= SET({close}, 0); } return r_;"),
                ("cover.read", "unsigned l_; int r_ = READ; if (!r_) *closed = (l_ >> {closed}) & 1; return r_;")],
    "climate": [("climate.set", "if (on && (target_c < 16 || target_c > 30)) return -2; int r_ = SET({power}, on ? 1 : 0); "
                                "for (int i_ = 0; i_ < {bits}; i_++) r_ |= SET({base} + i_, on ? ((target_c >> i_) & 1) : 0); return r_;"),
                ("climate.read", "unsigned l_; int r_ = READ; if (!r_) { *on = (l_ >> {power}) & 1; *target_c = (int)((l_ >> {base}) & {mask}); } return r_;")],
}

def _sub_set(b, fmt):
    """Replace every SET(pin, level) — arguments may hold parentheses — by the part's set-pin call."""
    out, i = "", 0
    while True:
        j = b.find("SET(", i)
        if j < 0: return out + b[i:]
        k, depth, comma = j + 4, 1, None
        while depth:
            ch = b[k]
            if ch == "(": depth += 1
            elif ch == ")": depth -= 1
            elif ch == "," and depth == 1 and comma is None: comma = k
            k += 1
        out += b[i:j] + fmt.format(p=b[j + 4:comma].strip(), v=b[comma + 1:k - 1].strip())
        i = k

def _role_body(part, body, pins):
    ops = PIN_OPS[part]
    if "base" in pins:                                      # a number carried on pins base … base+bits-1
        pins = {"bits": 8, **pins}; pins["mask"] = hex((1 << int(pins["bits"])) - 1)
    for k, v in pins.items(): body = body.replace("{" + k + "}", str(v))      # C braces stay as they are
    if re.search(r"\{[a-z_]+\}", body):                        # the role does not wire this capability (e.g. no limit pin)
        return None, []
    b = _sub_set(body, ops["set"]).replace("READ", ops["read"])
    fns = ([ops["fset"]] if ops["fset"] + "(" in b else []) + ([ops["fread"]] if ops["fread"] + "(" in b else [])
    return b, sorted(set(fns))

def instances(node_id=None, cap=None, include_denied=False):
    """Capability instances. A sensor: attachment × capability × channel. A GPIO expander with roles (L1 says what
    each pin is wired to): one instance per role and capability; pins without a role are not exposed at all."""
    import manifest as MF
    P, C, N = parts(), caps(), nodes()
    table = MF.policy()
    order = {"allow": 0, "confirm": 1, "deny": 2}
    out = []
    for nid in ([node_id] if node_id else N):
        used = {}
        for a in attachments(nid):
            p = P.get(a["part"])
            if not p: continue
            if a.get("roles") and a["part"] in PIN_OPS:
                for r in a["roles"]:
                    pins = {k: v for k, v in r.items() if k in ("pin", "open", "close", "closed", "power", "base", "bits")}
                    thing = f"{a['id']}:{r['key']}"
                    const = _uniq(used, _const(nid, r["key"]), thing)
                    for capname, tmpl in ROLES[r["role"]]:
                        if cap and capname != cap: continue
                        body, fns = _role_body(a["part"], tmpl, pins)
                        if body is None: continue
                        pol = max((table.get(f, "deny") for f in fns), key=lambda x: order.get(x, 2), default="deny")
                        if pol == "deny" and not include_denied: continue
                        out.append({"id": f"{a['id']}#{capname}@{r['key']}", "const": const, "thing": thing,
                                    "node": nid, "place": N[nid].get("place", nid), "cap": capname, "channel": r["key"], "chv": 0,
                                    "label": r.get("name", r["key"]), "name": r.get("name", ""), "role": r["role"],
                                    "access": "write" if any(MF.FN[f].get("access") == "write" for f in fns) else "read", "policy": pol,
                                    "spec": r.get("spec", {}), "describes": C.get(capname, {}).get("describes_ko", ""),
                                    "safety": C.get(capname, {}).get("safety", ""),
                                    "_part": a["part"], "_addr": a["addr"], "_body": body, "_fns": fns, "_params": a.get("params", {})})
                continue
            for b in p["implements"]:
                if cap and b["cap"] != cap: continue
                if b["policy"] == "deny" and not include_denied: continue
                for ch, chv in b["channels"].items():
                    thing = f"{a['id']}@{ch}"
                    out.append({"id": f"{a['id']}#{b['cap']}" + (f"@{ch}" if ch else ""),
                                "const": _uniq(used, _const(nid, b["cap"].split(".")[0], ch), thing), "thing": thing,
                                "node": nid, "place": N[nid].get("place", nid), "cap": b["cap"], "channel": ch, "chv": chv,
                                "label": a.get("label", "") + (f" [{b.get('what', 'ch')} {ch}" + (f" = {a['channel_names'][ch]}" if a.get("channel_names", {}).get(ch) else "") + "]" if ch else ""),
                                "name": a.get("channel_names", {}).get(ch, ""),
                                "access": b["access"], "policy": b["policy"],
                                "spec": {**{k: v for k, v in b.get("spec", {}).items() if k != "basis"},
                                         **({"measured_max_err": (b["measured"]["channels"].get(ch or "0") or {}).get("max_abs_err_mC"),
                                             "measured": (b["measured"]["channels"].get(ch or "0") or {}).get("verdict")}
                                            if b.get("measured") else {"measured": "not yet (claimed by the fragment)"})},
                                "describes": C.get(b["cap"], {}).get("describes_ko", ""), "safety": C.get(b["cap"], {}).get("safety", ""),
                                "_part": a["part"], "_addr": a["addr"], "_body": b["body"], "_fns": b["fns"], "_params": a.get("params", {})})
    return out

def public(i):
    return {k: v for k, v in i.items() if not k.startswith("_") and k != "chv"}

def body_summary():
    """What the model gets for planning: places, nodes, and their capability instances (no chips, no addresses)."""
    N, lines = nodes(), []
    for nid, n in N.items():
        lines.append(f"## node {nid} — {n.get('place', nid)} ({n['arch']}, link {n['link']['kind']}, run budget {n['limits']['run_budget_ms']} ms)")
        groups = {}
        for i in instances(nid):
            groups.setdefault((i["cap"], i["label"].split(" [")[0], i["policy"], json.dumps(i["spec"], ensure_ascii=False)), []).append(i)
        for (cap, label, pol, spec), xs in groups.items():
            ids = [x["id"] for x in xs]
            named = [f"{x['channel']}={x['name']}" for x in xs if x.get("name")]
            if len(ids) == 1:
                lines.append(f"- {cap} · {label} · policy {pol} · spec {spec}\n    id: {ids[0]}")
            else:                                         # every channel's id, so a plan can name each one
                lines.append(f"- {cap} · {label} · policy {pol} · spec {spec} · {len(ids)} channels"
                             + (f" ({'; '.join(named)}{'' if len(named) == len(ids) else '; the rest unnamed'})" if named else " (no channel is named)")
                             + "".join(f"\n    id: {x}" for x in ids))
        denied = [i for i in instances(nid, include_denied=True) if i["policy"] == "deny"]
        if denied:
            lines.append(f"- (present but denied by the owner's policy: {', '.join(sorted({d['cap'] for d in denied}))})")
    return "\n".join(lines)

def vocabulary():
    return "\n".join(f"- {c['cap']}: {c['call']}  — {c['describes_ko']} (safety {c['safety']})" for c in caps().values())

# ── a plan: checked against the registry, never trusted ────────────────────────────────────────────
def validate_plan(plan):
    """→ list of problems (English, for the model's next round)."""
    probs = []
    allinst = {i["id"]: i for i in instances(include_denied=True)}
    N = nodes()
    if not isinstance(plan, dict) or not isinstance(plan.get("steps"), list) or not plan["steps"]:
        return ["the plan must be a JSON object with a non-empty 'steps' list"]
    ids = set()
    for s in plan["steps"]:
        sid = s.get("id", "?"); ids.add(sid)
        if s.get("on") == "hub":
            continue
        if s.get("on") not in N:
            probs.append(f"step {sid}: node '{s.get('on')}' does not exist"); continue
        for u in s.get("uses", []):
            i = allinst.get(u)
            if not i: probs.append(f"step {sid}: capability '{u}' does not exist — use ids from the body summary"); continue
            if i["node"] != s["on"]: probs.append(f"step {sid}: '{u}' is on node {i['node']}, not {s['on']}")
            if i["policy"] == "deny": probs.append(f"step {sid}: '{u}' is denied by the owner's policy")
        if not s.get("uses"):
            probs.append(f"step {sid}: a node step must use at least one capability")
        for a in s.get("after", []):
            if a not in ids: probs.append(f"step {sid}: 'after' names '{a}', which is not an earlier step")
        rp = s.get("repeat")
        if rp is not None:
            if not (isinstance(rp, dict) and isinstance(rp.get("every_min"), int) and isinstance(rp.get("times"), int)
                    and 5 <= rp["every_min"] <= 240 and 2 <= rp["times"] <= 24):
                probs.append(f"step {sid}: repeat must be {{\"every_min\": 5–240, \"times\": 2–24}}")
            elif any(allinst.get(u, {}).get("policy") != "allow" for u in s.get("uses", [])):
                probs.append(f"step {sid}: only reading steps may repeat — an action happens once")
    return probs

WHEN_OPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
            "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}

def validate_standing(st):
    """A standing intent: watch one reading step every N minutes; when a reported value meets the condition
    k readings in a row, run the action steps once. → problems."""
    if not isinstance(st, dict): return ["standing must be a JSON object"]
    probs = []
    w = st.get("watch")
    if not isinstance(w, dict): return ["standing.watch must be one step"]
    probs += [f"watch: {p}" for p in validate_plan({"steps": [w]})]
    allinst = {i["id"]: i for i in instances(include_denied=True)}
    if any(allinst.get(u, {}).get("policy") != "allow" for u in w.get("uses", [])):
        probs.append("watch: the watching step may only read")
    if not (isinstance(st.get("every_min"), int) and 5 <= st["every_min"] <= 240):
        probs.append("standing.every_min must be 5–240")
    if not (isinstance(st.get("until_min"), int) and 10 <= st["until_min"] <= 24 * 60):
        probs.append("standing.until_min (how long the intent lives) must be 10–1440")
    c = st.get("when") or {}
    if not (isinstance(c.get("key"), str) and c.get("op") in WHEN_OPS and isinstance(c.get("value"), (int, float))
            and isinstance(c.get("times", 1), int) and 1 <= c.get("times", 1) <= 12):
        probs.append('standing.when must be {"key": "<a key the watch step reports>", "op": "==|!=|<|<=|>|>=", "value": <number>, "times": 1–12}')
    then = st.get("then")
    if not isinstance(then, list) or not then: probs.append("standing.then must be a non-empty list of steps")
    else: probs += [f"then: {p}" for p in validate_plan({"steps": then})]
    return probs

def confirmations(plan):
    allinst = {i["id"]: i for i in instances()}
    return sorted({u for s in plan["steps"] for u in s.get("uses", []) if allinst.get(u, {}).get("policy") == "confirm"})

# ── a step's image: the generated header and the derived allow list ─────────────────────────────────
def header(node_id, cap_ids):
    """poke_body.h for one image: only the granted capability instances. → (C text, functions, allow list)."""
    import manifest as MF
    inst = {i["id"]: i for i in instances(node_id)}
    chosen = [inst[c] for c in cap_ids]
    C = caps()
    frags = sorted({i["_part"] for i in chosen})
    out = [f"/* poke_body.h — generated by the hub for node {node_id}: the capabilities this step was granted, nothing else */",
           *[f'#include "{p}.h"' for p in frags], "typedef int cap_t;"]
    index = {}
    for i in chosen:
        if i["const"] not in index:
            index[i["const"]] = len(index)
            out.append(f"#define {i['const']} {index[i['const']]}   /* {i['place']} · {i['label']} */")
    by_cap = {}
    for i in chosen: by_cap.setdefault(i["cap"], []).append((index[i["const"]], i))
    for cap, xs in by_cap.items():
        sig = C[cap]["call"]
        out.append(f"static inline {sig} {{")
        out.append("    switch (c) {")
        for k, i in xs:
            body = i["_body"].replace("ADDR", i["_addr"]).replace("CH", str(i["chv"]))
            for pk, pv in i["_params"].items(): body = body.replace(pk, str(pv))
            out.append(f"    case {k}: {{ {body} }}")
        out.append("    }\n    return -1;\n}")
    fns = sorted({f for i in chosen for f in i["_fns"]})
    devs = {}
    for i in chosen:
        a = int(i["_addr"], 16)
        d = devs.setdefault(a, {"addr": a, "read": True, "pointer": True, "write_regs": set(), "max_write": 1})
        for f in i["_fns"]:
            d["write_regs"] |= set(MF.FN[f].get("write_regs", []))
            d["max_write"] = max(d["max_write"], MF.FN[f].get("max_write", 1))
    allow = {"bus": "i2c1", "devices": [{**d, "write_regs": sorted(d["write_regs"])} for _, d in sorted(devs.items())]}
    return "\n".join(out) + "\n", fns, allow

def build_step(node_id, cap_ids, op_src, tag, summary_ko=""):
    """The op (written against cap_*) + its generated header → an image for that node. The MANIFEST is the hub's."""
    import say
    hdr, fns, allow = header(node_id, cap_ids)
    body = re.sub(r"/\*\s*MANIFEST.*?\*/", "", op_src, flags=re.S)
    body = re.sub(r'#include\s+"poke_body\.h"\s*\n', "", body)
    mani = {"v": 1, "uses": [{"fn": f} for f in fns], "max_s": 20, "summary_ko": summary_ko or "능력 계획의 한 단계"}
    src = f"/* MANIFEST {json.dumps(mani, ensure_ascii=False)} */\n#include \"rt.h\"\n{hdr}\n{body}"
    data, obj, img = say.build(src, tag, allow=allow)
    return img, src, allow

def world_on():
    """The virtual home (world.py) drives the twin when POKE_WORLD names its state file."""
    return bool(os.environ.get("POKE_WORLD"))

def boot_step(node_id, img, state=None):
    import bench
    n = nodes()[node_id]
    b = [(t[0], t[1], int(t[2], 16)) for t in n.get("twin", [])]
    st = dict(n.get("twin_state", {}))
    if world_on():
        import world
        st.update(world.knobs(node_id))
    st.update(state or {})
    r = bench.boot(img, st, bench=b)
    if world_on():
        r["world"] = {"clock": world.clock(world.load()), "changed": world.observe(node_id, r)}
    return r

if __name__ == "__main__":
    print(body_summary())
