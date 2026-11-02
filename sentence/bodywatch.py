"""The hub's body hook (hub.pipeline calls after_failure after a boot with a problem) → 실기기팀's bodycheck.

hub.pipeline expects  {"kind": "moved" | "gone" | "new" | "unclear", "text", "rerun", "wiring"}  or None;
bodycheck.after_boot answers  {"kind": "moved" | "gone" | "new" | "unclear", "text", "hint", "retry", "wiring", ...}.
This file only translates; the judging (which part went silent, the read-only scan, signatures, the
wiring update for a moved part) is bodycheck's.
"""
import bodycheck

import os

def after_failure(boot, op=None):
    v = bodycheck.after_boot(boot, op)
    if not v:
        return None
    text = v.get("text", "")
    if v.get("hint"):
        text = f"{text} — {v['hint']}" if text else v["hint"]
    return {"kind": v.get("kind"), "text": text, "rerun": bool(v.get("retry")),
            "wiring": v.get("wiring") or {}, "silent": [f"0x{a:02x}" for a in v.get("silent", [])]}

def after_success():
    """After a boot that went fine: mention a part nobody knows yet, at most once per body every
    POKE_BODY_HINT_S seconds (default 600; a scan costs about a second)."""
    v = bodycheck.maybe_hint(max_age_s=float(os.environ.get("POKE_BODY_HINT_S", "600")))
    if not v:
        return None
    text = v.get("text", "") + (f" — {v['hint']}" if v.get("hint") else "")
    return {"kind": "new", "text": text, "rerun": False, "wiring": {}}
