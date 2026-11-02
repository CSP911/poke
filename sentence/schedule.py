"""schedule — a request longer than one boot (20 s) waits on the hub, not on the node.

The model answers such a sentence with
    LONG: {"each": "온도 알려줘", "every_s": 10, "total_s": 30}
and the hub boots `each` (an ordinary sentence: its own record, its own op, cached after the first) once
every `every_s` seconds for `total_s` seconds. Between boots the node is empty; the waiting is the hub's.
The results are put side by side and numeric fields summarized (min / avg / max).

Limits (hub-attended): total ≤ 600 s, every ≥ 1 s, at most 60 boots.
"""
import time
import messages_ko as KO

MAX_TOTAL_S, MIN_EVERY_S, MAX_BOOTS = 600, 1, 60

def plan(spec):
    """→ (each, every_s, n_boots, problem_ko)"""
    if not isinstance(spec, dict) or not isinstance(spec.get("each"), str) or not spec.get("each").strip():
        return None, 0, 0, "나눠 실행할 문장을 정하지 못했습니다"
    try:
        every, total = float(spec.get("every_s", 0)), float(spec.get("total_s", 0))
    except (TypeError, ValueError):
        return None, 0, 0, "간격이나 전체 시간을 읽지 못했습니다"
    if every < MIN_EVERY_S:
        return None, 0, 0, f"간격은 {MIN_EVERY_S}초 이상이어야 합니다"
    if total <= 0 or total > MAX_TOTAL_S:
        return None, 0, 0, f"예약 실행은 {MAX_TOTAL_S // 60}분까지입니다 (요청: {total:g}초)"
    n = int(total // every) + 1
    if n > MAX_BOOTS:
        return None, 0, 0, f"부팅이 {n}번 필요합니다 — 한 번에 {MAX_BOOTS}번까지입니다"
    return spec["each"].strip(), every, n, None

def summarize(results):
    nums = {}
    for r in results:
        for k, v in (r or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                nums.setdefault(k, []).append(v)
    return {k: {"min": min(v), "avg": round(sum(v) / len(v), 3), "max": max(v), "n": len(v)} for k, v in nums.items()}

def run(sentence, spec, *, bench=None, yes=False, no=False, ask=None, target="twin", sleep=time.sleep):
    import hub
    each, every, n, prob = plan(spec)
    if prob:
        yield {"stage": "stop", "kind": "too_long", "text": f"예약 실행을 만들 수 없습니다: {prob}", "hint": "노드를 켜지 않았습니다."}
        return
    yield {"stage": "schedule", "state": "plan", "each": each, "every_s": every, "boots": n,
           "text": f"허브가 \"{each}\"를 {every:g}초마다 {n}번 부팅합니다. 부팅 사이 노드는 비어 있습니다."}
    t0, results, ok_boots, last_zeroed, last_nonzero = time.time(), [], 0, False, None
    for i in range(n):
        due = t0 + i * every
        if time.time() < due:
            yield {"stage": "schedule", "state": "waiting", "i": i, "wait_s": round(due - time.time(), 1)}
            sleep(max(0.0, due - time.time()))
        res, stop = None, None
        for ev in hub.pipeline(each, bench=bench, yes=yes, no=no, ask=ask, target=target, depth=2):
            if ev["stage"] == "confirm" and ev.get("state") == "ok":
                yes = True                                     # one confirmation covers the whole schedule
            if ev["stage"] == "result":
                res = ev
            elif ev["stage"] == "empty":
                last_zeroed = bool(ev.get("zeroed"))
                if ev.get("wipe_check"):
                    last_nonzero = (last_nonzero or 0) + ev["wipe_check"].get("nonzero", 0)
            elif ev["stage"] == "stop":
                stop = ev
            elif ev["stage"] == "confirm" and ev.get("state") == "asking":
                yield ev
        if stop:
            yield {"stage": "stop", "kind": stop["kind"], "text": f"{i + 1}번째 부팅에서 멈췄습니다: {stop['text']}", "hint": stop.get("hint", "")}
            break
        results.append(res.get("result") if res else None)
        ok_boots += bool(res and res.get("ok"))
        yield {"stage": "schedule", "state": "boot", "i": i, "at_s": round(time.time() - t0, 1),
               "result": res.get("result") if res else None, "lines": res.get("lines") if res else [], "ok": bool(res and res.get("ok"))}
    if results:
        agg = summarize(results)
        lines = []
        for k, v in agg.items():
            if k.lower().endswith("mc") and any(x.endswith("_c") for x in agg):
                continue                                        # the same temperature in milli-degrees
            base = (KO.result_lines({k: v["avg"]}) or [f"{k} {v['avg']:g}"])[0]
            lines.append(f"평균 {base} — 최저 {v['min']:g} · 최고 {v['max']:g} ({v['n']}회)")
        yield {"stage": "result", "ok": ok_boots == len(results), "result": {"boots": len(results), "summary": agg, "series": results},
               "lines": [f"부팅 {len(results)}번 ({ok_boots}번 정상)"] + lines, "problem": None if ok_boots == len(results) else "일부 부팅이 정상으로 끝나지 않았습니다"}
        yield {"stage": "empty", "powered_off": ok_boots == len(results), "zeroed": last_zeroed, "vm_status": "매 부팅 뒤 리셋",
               "text": f"부팅 {len(results)}번 — 매번 " + (f"리셋 뒤 실측 0이 아닌 바이트 {last_nonzero}" if last_nonzero is not None else hub.empty_text(True, last_zeroed).split(" — ", 1)[-1]), "total_s": round(time.time() - t0, 1)}
