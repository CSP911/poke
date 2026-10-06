"""RouteMind as the hub's knowledge: two tools, read the same way every time.

RouteMind's contract for an agent: fetch the list of areas (hop 0), pick
one, fetch that area, read what it points at — two operations, never a
third. This module gives a language-model agent exactly those two tools
over the POKE ontology (tools/ontology.py → ontology/repo, served by a
RouteMind ontology container), and renders what the API returns as the
compact tables an agent is handed.

    tools, run = routemind.knowledge_tools()      # Anthropic tool defs + executor
    text = routemind.walk(client, model, "what is at I2C 0x60?")   # a full walk, for non-agent callers
"""
import os, json, urllib.request, urllib.error

API = os.environ.get("POKE_KNOWLEDGE_API", "http://localhost:8120")

def _get(path):
    with urllib.request.urlopen(API + path, timeout=10) as r:
        body = r.read().decode()
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return body

def available():
    try:
        return bool(_get("/healthz").get("ok"))
    except Exception:
        return False

def table(path=None):
    """Hop 0 (no path) or one area: the rows an agent chooses from."""
    if not path or path in ("/", "/v1/regions"):
        d = _get("/v1/regions")
        rows = [f"- {r['id']:<10} {r['fetch']:<22} use when: {r['use_when']}" for r in d.get("regions", [])]
        return ("BACK-BONE — one row per area. Pick the row whose 'use when' matches the question, then fetch it.\n"
                + "\n".join(rows) + "\n(absence: a question none of these rows fit is not answered by this ontology)")
    if path.startswith("/v1/regions/"):
        d = _get(path)
        rows = [f"- {e['id']:<22} [{e['kind']}] {e['one_liner']}  → {e['fetch']}" for e in d.get("entries", [])]
        return (f"AREA {d.get('key')} — {d.get('advertises', '')}\nuse when: {d.get('use_when', '')}\n"
                + "\n".join(rows) + "\n(this table says what this area holds, not what the ontology lacks)")
    raise ValueError("table() takes no path, or /v1/regions/<area>")

def read(path):
    """The text an agent is handed for one node."""
    if not path.startswith("/v1/nodes/"):
        raise ValueError("read() takes /v1/nodes/<id> or /v1/nodes/<id>/body")
    if not path.endswith("/body"):
        path = path.rstrip("/") + "/body"
    d = _get(path)
    return d if isinstance(d, str) else json.dumps(d, ensure_ascii=False)

def knowledge_tools():
    """Anthropic tool definitions + an executor, mirroring RouteMind's MCP (knowledge_table / knowledge_read)."""
    tools = [
        {"name": "knowledge_table", "description": "Read a routing table of what this project already knows. No path: the list of areas "
         "(hop 0) with one 'use when' line each — pick from it before reading anything. With an area's fetch path "
         "(/v1/regions/<area>): the nodes it holds, each with a one-liner and a fetch path.",
         "input_schema": {"type": "object", "properties": {"path": {"type": ["string", "null"]}}}},
        {"name": "knowledge_read", "description": "Read one node's text by its fetch path (/v1/nodes/<id>/body). Say what in the row's line made you open it.",
         "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    ]
    def run(name, inp):
        try:
            if name == "knowledge_table":
                return table(inp.get("path"))
            if name == "knowledge_read":
                return read(inp["path"])
        except urllib.error.HTTPError as e:
            return f"{e.code} for that path — fetch paths come from a table"
        except Exception as e:
            return f"error: {e}"
        return "unknown tool"
    return tools, run

def walk(client, model, question, max_hops=6, log=None):
    """A whole walk for callers that are not agents: returns the text the model gathered and its route."""
    tools, run = knowledge_tools()
    system = ("You read an ontology the same way every time: fetch the list of areas, pick the row whose 'use when' "
              "fits the question, fetch that area, read the one or two nodes that answer. Then reply with ONLY the "
              "relevant facts you read, verbatim where it matters, and the paths you used. If no row fits, say so.")
    messages = [{"role": "user", "content": question}]
    route = []
    for _ in range(max_hops):
        msg = client.messages.create(model=model, max_tokens=3000, system=system, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": msg.content})
        results = []
        for b in msg.content:
            if b.type == "tool_use":
                out = run(b.name, b.input); route.append((b.name, b.input.get("path")))
                if log: log(f"  🧭 {b.name}({b.input.get('path') or 'hop 0'})")
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
        if not results:
            text = "".join(b.text for b in msg.content if b.type == "text")
            return text, route
        messages.append({"role": "user", "content": results})
    return "", route

if __name__ == "__main__":
    print(table())
    print()
    print(table("/v1/regions/devices")[:800])
