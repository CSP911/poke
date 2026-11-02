"""Claude Code as the hub's language model.

The hub's generation steps (skill writing, driver writing, routing lines)
were calls to the Anthropic API. This module routes them through the
Claude Code CLI instead (`claude -p`), which runs on the person's own Claude
login — no API key or API credit involved.

    text = claude_cli.ask(system, user)                       # one question, no tools
    text, sid = claude_cli.ask(system, user, keep=True)       # keep the conversation
    text, sid = claude_cli.ask(None, "it failed: …", resume=sid, keep=True)

Agent loops that need tools (incubation) run Claude Code itself with only
the tools they need allowed; see incubate_cc.py.
"""
import json, os, subprocess

CLAUDE = os.environ.get("POKE_CLAUDE", "claude")
WORKDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build", "cc")

class CLIError(RuntimeError):
    pass

def _run(args, timeout, cwd=None):
    os.makedirs(WORKDIR, exist_ok=True)
    # The hub loads .env (ANTHROPIC_API_KEY) into the environment for its API path; handed to the
    # CLI, that key would win over the person's own Claude login. Strip it so `claude` uses the login.
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
    r = subprocess.run([CLAUDE, *args], capture_output=True, text=True, timeout=timeout, cwd=cwd or WORKDIR, env=env)
    try:
        out = json.loads(r.stdout)
    except json.JSONDecodeError:
        raise CLIError(f"claude -p failed (exit {r.returncode}): {(r.stderr or r.stdout)[-400:]}")
    if out.get("is_error") or out.get("subtype", "success") != "success":
        raise CLIError(f"claude -p error: {str(out.get('result') or out)[:400]}")
    return out

def ask(system, user, model=None, keep=False, resume=None, timeout=900):
    """One question to Claude Code with no tools. Returns the text (and the session id with keep=True)."""
    args = ["-p", user, "--output-format", "json", "--tools", ""]
    if system:
        args += ["--system-prompt", system]
    if model:
        args += ["--model", model]
    if resume:
        args += ["--resume", resume]
    if not keep:
        args += ["--no-session-persistence"]
    out = _run(args, timeout)
    text = (out.get("result") or "").strip()
    return (text, out.get("session_id")) if keep else text

def agent(prompt, allowed, system=None, model=None, cwd=None, timeout=1800, max_turns=None):
    """Run Claude Code as an agent that may use only the allowed tool patterns
    (e.g. ['Bash(.venv/bin/python bus.py:*)']). Returns the final result dict."""
    args = ["-p", prompt, "--output-format", "json", "--tools", "Bash", "--allowedTools", *allowed,
            "--no-session-persistence"]
    if system:
        args += ["--append-system-prompt", system]
    if model:
        args += ["--model", model]
    if max_turns:
        args += ["--max-turns", str(max_turns)]
    return _run(args, timeout, cwd=cwd)

class _Block:
    type = "text"
    def __init__(self, text): self.text = text

class _Msg:
    def __init__(self, text): self.content = [_Block(text)]; self.stop_reason = "end_turn"

class _Messages:
    """client.messages.create(model=…, system=…, messages=[…]) over the CLI. A conversation
    (assistant turns in `messages`) continues the same Claude Code session, so feedback
    rounds keep their context the way they did over the API."""
    def __init__(self):
        self.sessions = {}                         # id(first user message) → session id
    def create(self, model=None, max_tokens=None, system=None, messages=(), **_):
        first = messages[0]["content"] if messages else ""
        key = hash((system or "", first if isinstance(first, str) else json.dumps(first, default=str)))
        last = messages[-1]["content"]
        last = last if isinstance(last, str) else "\n".join(b.get("text", "") for b in last if isinstance(b, dict))
        sid = self.sessions.get(key) if len(messages) > 1 else None
        text, sid = ask(None if sid else system, last, keep=True, resume=sid)
        self.sessions[key] = sid
        return _Msg(text)

class Client:
    """Drop-in for anthropic.Anthropic() in single-shot and feedback-loop callers."""
    def __init__(self): self.messages = _Messages()

def strip_fences(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()

if __name__ == "__main__":
    print(ask("Answer in one word.", "What is the capital of France?"))
