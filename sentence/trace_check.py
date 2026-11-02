"""trace_check — 흔적 대조 재발음 (meeting decision 6).

A card's identity is the sentence AND its result traces; the model is only the voice. When an op is
pronounced again (another body, another model, or the old op no longer builds), its first result is held
against the card's traces from earlier pronunciations before the new op is adopted:

  effects          (POKE-EFFECTS, written by the runtime) the set of room-changing functions must be the same
                   → else mismatch ("it did something else"); the LAST bytes written per (part, register) must be
                   the same (T12: another LED, an extra LED, a blink mode) → else mismatch; a part read before
                   and not read now → mismatch (a fixed value instead of a reading); a new part read → partial. When the old
                   traces carry no effects, the previous pronunciation's MANIFEST write functions stand in.
  values           every number the card reported must still be among the new result's numbers (by value,
                   not by key name — T9: names change on every regeneration, effects do not); same body and
                   bench → the value itself, otherwise the traces' range ± a margin. Some missing → partial,
                   none left (only Fahrenheit now) → mismatch: the person would get a different answer.
  result keys      reported only, never judged
  errors           the new run failed or reported an error → mismatch

verdict "match" → adopt; "partial" → adopt, and the card shows "다르게 발음됨" with the reasons;
"mismatch" → do not adopt: the earlier pronunciation stays the card's op, the reasons are kept.

    fx_summary(b, img)                → what a trace keeps of a run's effects (functions, parts)
    compare(record, result, fx, body_fp, bench) → {"verdict", "reasons", "ref": n traces, "checked": [...]}
"""
NUM = (int, float)

def fx_summary(b, img):
    """{"writes": [write functions that reached a device], "reads": [fragments read]} from the run's effects."""
    import say, manifest as MF
    try:
        r = say.effects_report(b, img)
    except Exception:
        return None
    if r.get("ok") is None:
        return None
    writes = sorted({a["fn"] for a in r.get("actions") or []})
    fx = say.effects_of(b) or {}
    frag_at = {}
    for fn, e in MF.FN.items():
        for a in [int(str(x), 0) for x in say.wired_addrs(e["frag"], e["addr"])]:
            frag_at[a] = e["frag"]
    reads = sorted({frag_at.get(d.get("a"), f"0x{d.get('a', 0):02x}") for d in fx.get("dev", []) if d.get("r")})
    # T12: what was written where — the LAST data bytes per (part, register) (T9: identical across regenerations)
    last = {}
    for x in fx.get("x", []):
        if len(x) >= 6 and x[1] == "w" and x[5] == 0 and isinstance(x[3], str) and len(x[3]) >= 4:
            part = frag_at.get(x[2], f"0x{x[2]:02x}")
            last[f"{part}@0x{x[3][:2]}"] = x[3][2:]
    return {"writes": writes, "reads": reads, "last_writes": last, "truncated": bool(fx.get("truncated"))}

def _num(v):
    return isinstance(v, NUM) and not isinstance(v, bool)

def reference(record):
    """Traces of earlier pronunciations (not the current op's), successful ones, newest last."""
    cur = (record.get("colophon") or {}).get("date")
    return [t for t in record.get("traces", []) if t.get("ok") and t.get("op_date") != cur and isinstance(t.get("result"), dict)]

def compare(record, result, fx=None, body_fp=None, bench=None, ref=None, ref_writes=None, ref_reads=None):
    """Judge a new pronunciation against the card. Result KEY NAMES are not trusted (the test team's T9:
    regenerating the same sentence kept the keys 0/12 times, the effects 12/12) — what counts is what reached
    the bus (EFFECTS) and the values, found by value, not by name."""
    ref = ref if ref is not None else reference(record)
    if not ref:
        return {"verdict": "unknown", "reasons": ["대조할 이전 결과 흔적이 없습니다"], "ref": 0, "checked": []}
    if not isinstance(result, dict) or result.get("error"):
        why = (result or {}).get("error") if isinstance(result, dict) else "결과가 없음"
        return {"verdict": "mismatch", "reasons": [f"새 발음이 실패했습니다: {why}"], "ref": len(ref), "checked": ["error"]}
    reasons, level, checked = [], "match", []
    def worse(l):
        nonlocal level
        order = {"match": 0, "partial": 1, "mismatch": 2}
        if order[l] > order[level]:
            level = l
    # 1. effects — the strongest evidence: what the runtime saw reach the bus
    ref_fx = [t["fx"] for t in ref if t.get("fx")]
    w_ref = set(ref_fx[-1].get("writes", [])) if ref_fx else (set(ref_writes) if ref_writes is not None else None)
    if fx is not None and w_ref is not None:
        checked.append("effects")
        if set(fx.get("writes", [])) != w_ref:
            worse("mismatch")
            reasons.append("장치를 바꾸는 일이 다릅니다 (전: " + (", ".join(sorted(w_ref)) or "없음") + " / 새: " + (", ".join(fx.get("writes", [])) or "없음") + ")")
        if ref_fx or ref_reads is not None:
            r_old = set(ref_fx[-1].get("reads", [])) if ref_fx else set(ref_reads)
            r_new = set(fx.get("reads", []))
            if r_old - r_new:
                worse("mismatch"); reasons.append("전에 읽던 부품을 읽지 않았습니다: " + ", ".join(sorted(r_old - r_new)))
            elif r_new - r_old:
                worse("partial"); reasons.append("새로 읽은 부품이 있습니다: " + ", ".join(sorted(r_new - r_old)))
            lw_old, lw_new = (ref_fx[-1].get("last_writes") if ref_fx else None), fx.get("last_writes")
            if lw_old is not None and lw_new is not None and not (ref_fx[-1].get("truncated") or fx.get("truncated")) and lw_old != lw_new:
                worse("mismatch")
                diff = sorted(set(lw_old.items()) ^ set(lw_new.items()))
                reasons.append("장치에 쓴 값이 다릅니다 (" + ", ".join(f"{k}={v}" for k, v in diff[:6]) + ")")
    # 2. values, matched by value: every number the card used to report should still be reported
    new_vals = [v for v in result.values() if _num(v)]
    last = ref[-1]["result"]
    same_world = bool(body_fp) and ref[-1].get("body") == body_fp and (bench is None or ref[-1].get("bench") == bench)
    lost = []
    for k, v in last.items():
        if not _num(v) or k == "error":
            continue
        span = [t["result"][k] for t in ref if _num(t["result"].get(k))]
        lo, hi = min(span), max(span)
        tol = max(abs(v) * 0.01, 0.07) if same_world else max((hi - lo) * 0.25, abs(hi) * 0.05, 0.5)
        target = [v] if same_world else [lo, hi]
        if not any(min(target) - tol <= x <= max(target) + tol for x in new_vals):
            lost.append(k)
    checked.append("values")
    if lost:
        if len(lost) == len([k for k, v in last.items() if _num(v)]):
            # nothing the card used to report is there any more (e.g. only Fahrenheit now): not the same answer
            worse("mismatch"); reasons.append("이전에 보고하던 값이 하나도 없습니다: " + ", ".join(lost))
        else:
            worse("partial"); reasons.append("이전에 보고하던 값을 새 결과에서 찾지 못했습니다: " + ", ".join(lost))
    keys_old = {k for k in last if k != "error"}; keys_new = {k for k in result if k != "error"}
    if keys_old != keys_new:
        reasons.append("결과 항목 이름이 다릅니다(판정에는 쓰지 않음): 전 " + ", ".join(sorted(keys_old)) + " / 새 " + ", ".join(sorted(keys_new)))
    if not ref_fx and w_ref is None:
        reasons.append("이전 흔적에 버스 기록이 없어 값만 대조했습니다")
    return {"verdict": level, "reasons": reasons, "ref": len(ref), "checked": checked}

VERDICT_KO = {"match": "일치 — 새 발음을 씀", "partial": "다르게 발음됨 — 새 발음을 쓰되 차이를 카드에 남김",
              "mismatch": "불일치 — 새 발음을 쓰지 않고 이전 발음 유지", "unknown": "대조할 흔적 없음 — 새 발음을 씀"}
