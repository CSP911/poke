"""intent — "같은 뜻 다른 말": a new wording served by a record the hub already has, before any op is written.

    m = intent.match(sentence, records)   → {"record", "params", "how": "rule" | "model", "why"} or None

Two judges, cheapest first, and guards that both must pass:

1. rule (no model). A plain reading of one device ("지금 몇 도야?", "온도 좀 봐줘", "현재 온도는?") is
   recognised from a small vocabulary — device words, reading verbs, fillers — and served by the record
   whose own first sentence is a plain reading of the same device. Any word outside the vocabulary
   ("화씨로", "평균") means "not sure" and the rule does not answer.
2. model (one short claude -p). Only when some record is plausible by the guards below; the model sees
   the candidates (sentence, what the op does, parameters) and names one or none, with parameter values.

Guards (a match is refused unless all hold — this is what keeps "온도가 30도 넘으면 LED 0번 켜줘" from
ever being served by "온도 알려줘"):
  G1 devices:   the devices the sentence names == the devices the record's sentence names
  G2 structure: the same structure markers — condition (면/넘/이상/이하), repetition/time (마다/동안/간격/
                부터/까지/뒤), on/off/blink/set verbs, conversions (화씨/평균/최고), negation (말고/빼고)
  G3 numbers:   every digit number in the sentence is a parameter of the record, and the record has no
                parameter the sentence does not give
  G4 policy:    nothing is skipped — a write record still asks for confirmation as usual
"""
import json, re, unicodedata

# ── vocabulary ─────────────────────────────────────────────────────────────
DEVICES = {
    "temp": r"온도|기온|덥|춥|몇\s*도|\d\s*도",
    "clock": r"시계|rtc|시간|시각|몇\s*시|\d\s*시(?!간)|날짜|며칠|요일|오늘|지금\s*몇\s*시",
    "led": r"led|엘이디|불|램프|조명|전등",
    "mag": r"자기|자력",
    "heading": r"나침반|방향|북쪽|동쪽|서쪽|남쪽|방위",          # a heading is not a field strength
    # parts incubated on the extended bench: kept apart from the base ones so G1 never mixes them
    "remote_temp": r"원격|프로브|외부\s*온도|tmp421|보드\s*온도",
    "display": r"화면|oled|디스플레이|띄워|표시|글자|써\s*줘",
    "port": r"확장\s*포트|핀|스위치|pca9554|입력\s*상태",
}
MARKERS = {
    "cond": r"(으)?면(?![가-힣])|넘|이상|이하|미만|초과|보다",
    "time": r"마다|동안|간격|부터|까지|뒤에|후에|차례|동시에",
    "on": r"켜|on\b|turn on",
    "off": r"꺼|끄|off\b|turn off",
    "blink": r"깜빡|점멸|blink",
    "set": r"맞춰|설정|바꿔|변경",
    "fahrenheit": r"화씨|fahrenheit", "celsius": r"섭씨|celsius", "kelvin": r"켈빈|kelvin",
    "avg": r"평균|average", "max": r"최고|최대|max", "min": r"최저|최소|min", "sum": r"합계|합",
    # which axis is asked is part of the request ("x축 값" ≠ "세기"); "만" (only) is not: "x축 값" = "x축 값만"
    "axis_x": r"x\s*축|엑스\s*축", "axis_y": r"y\s*축|와이\s*축", "axis_z": r"z\s*축|제트\s*축",
    "neg": r"말고|빼고|제외|(?<![가-힣])안\s",
}
READ_WORDS = {"알려줘", "알려", "주세요", "줘", "줄래", "줄래요", "알려줄래", "알려주세요", "봐주세요", "알고", "싶어", "궁금해", "알려주세요", "봐줘", "봐", "봐봐", "말해줘", "말해", "확인해줘", "확인", "읽어줘", "읽어",
              "재줘", "재봐", "측정해줘", "보여줘", "얼마", "얼마야", "어때", "몇", "뭐야", "돼", "됐어", "야", "이야", "는", "은"}
FILLER = {"지금", "현재", "좀", "한번", "혹시", "방", "여기", "실내", "방금", "요즘", "나", "나한테", "내게", "제발", "please"}
DEVICE_WORDS = {"temp": {"온도", "기온", "도", "몇도"}, "clock": {"시간", "시각", "시", "몇시", "날짜", "며칠", "요일", "오늘"},
                "mag": {"자기장", "자기장세기", "세기", "자기", "자력"}}
PARTICLE = re.compile(r"(이야|야|이에요|예요|요|은|는|이|가|을|를|좀)$")

def _nfkc(s):
    return unicodedata.normalize("NFKC", s).lower()

def devices(s):
    t = _nfkc(s)
    return {d for d, rx in DEVICES.items() if re.search(rx, t)}

def markers(s):
    t = _nfkc(s)
    return {k for k, rx in MARKERS.items() if re.search(rx, t)}

def numbers(s):
    import record
    return record.numbers(s)

# ── decision 2: what the hub refuses before any op is written (the runtime still enforces) ──
SET_VERBS = r"맞춰|맞추|설정|바꿔|바꾸|변경|조정|세팅|set\b"
LED_INDEX = [r"(?:led|엘이디)\s*(-?\d+(?:\.\d+)?)", r"(-?\d+(?:\.\d+)?)\s*번\s*(?:led|엘이디)"]
TEMP_RANGE = (-55.0, 125.0)                      # TMP105 measuring range

def prefilter(sentence):
    """→ None, or (kind, Korean text) for a sentence the hub can refuse without writing an op:
       deny  — it asks for something the policy forbids (setting the clock)
       range — a LED number outside 0–15 or not whole, a temperature threshold the sensor cannot see."""
    import manifest as MF
    t = _nfkc(sentence)
    dev = devices(sentence)
    pol = MF.policy()
    if "clock" in dev and re.search(SET_VERBS, t) and pol.get("rtc_set", "deny") == "deny":
        return "deny", MF.DENY_WHY.get("rtc_set", "시계를 바꾸는 요청은 허브 정책상 막혀 있습니다")
    if "led" in dev:
        for rx in LED_INDEX:
            for m in re.finditer(rx, t):
                v = float(m.group(1))
                if v != int(v) or not 0 <= v <= 15:
                    return "range", f"LED 번호는 0–15의 정수만 됩니다 (말한 값 {v:g})"
    if "temp" in dev and markers(sentence) & {"cond"}:
        for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*도(?!\s*씩)", t):
            v = float(m.group(1))
            if not TEMP_RANGE[0] <= v <= TEMP_RANGE[1]:
                return "range", f"온도 센서가 잴 수 있는 범위는 {TEMP_RANGE[0]:g}–{TEMP_RANGE[1]:g} °C입니다 (말한 값 {v:g}도)"
    return None

def plain_read(s):
    """The one device a plain reading asks about, or None (any unknown word → None)."""
    if markers(s) or numbers(s):
        return None
    words = re.findall(r"[가-힣a-z]+", _nfkc(s))
    dev = set()
    for w in words:
        base = w
        for _ in range(2):
            if base in READ_WORDS or base in FILLER: break
            base = PARTICLE.sub("", base) or base
        if base in READ_WORDS or base in FILLER or w in READ_WORDS:
            continue
        hit = [d for d, ws in DEVICE_WORDS.items() if base in ws or w in ws]
        if not hit:
            return None                                      # a word the rule does not know: not sure
        dev |= set(hit)
    return dev.pop() if len(dev) == 1 else None

# ── guards ─────────────────────────────────────────────────────────────────
def guards(sentence, rec, params=None):
    """→ list of reasons the record must not serve this sentence ([] = allowed)."""
    base = rec["sentences"][0]
    why = []
    if devices(sentence) != devices(base):
        why.append(f"장치가 다름 ({sorted(devices(sentence))} ≠ {sorted(devices(base))})")
    if markers(sentence) != markers(base):
        why.append(f"구조가 다름 ({sorted(markers(sentence))} ≠ {sorted(markers(base))})")
    nums, decl = numbers(sentence), rec.get("params", [])
    if len(nums) != len(decl):
        why.append(f"숫자 {len(nums)}개 ≠ 파라미터 {len(decl)}개")
    elif decl and params is not None:
        for d in decl:
            v = params.get(d["name"])
            if v is None:
                why.append(f"{d['name']} 값이 없음"); continue
            if not any(abs(n * (d.get("scale", 1) or 1) - v) < 1e-6 for n in nums):
                why.append(f"{d['name']}={v}는 문장의 숫자에서 온 값이 아님")
    return why

def _op_records(records):
    return [r for r in records if r.get("kind") == "op" and r.get("sentences")]

def match_rule(sentence, records):
    dev = plain_read(sentence)
    if not dev:
        return None
    for r in sorted(_op_records(records), key=lambda r: -len(r.get("traces", []))):
        if plain_read(r["sentences"][0]) == dev and not r.get("params") and not guards(sentence, r):
            return {"record": r, "params": {}, "how": "rule", "why": f"{dev} 읽기"}
    return None

def candidates(sentence, records):
    """Records that could serve the sentence by the guards (numbers checked by count only)."""
    return [r for r in _op_records(records) if not guards(sentence, r)]

CLASSIFY_SYSTEM = """You decide whether a new sentence asks for exactly the same thing as one the hub already knows. Answer ONE line of JSON and nothing else:
{"id": "<the id of the known sentence that means the same>", "params": {"P_NAME": <value as an integer, the spoken number × scale>}} — or {"id": null} when none means exactly the same.
Same means: the same devices, the same action, the same conditions and the same output; only wording (and the numbers a parameter takes) may differ. A request that adds, removes or changes any condition, unit, device, repetition or output is NOT the same. When unsure, answer {"id": null}."""

def match_model(sentence, cands, ask):
    lines = []
    for r in cands[:12]:
        m = r.get("manifest") or {}
        ps = ", ".join(f"{p['name']} (=문장의 {p.get('ko', '')} × {p.get('scale', 1)}, {p.get('min')}..{p.get('max')})" for p in r.get("params", []))
        lines.append(f'- id {r["id"]}: "{r["sentences"][0]}" — {m.get("summary_ko", "")}' + (f" — parameters: {ps}" if ps else ""))
    prompt = f'New sentence: "{sentence}"\n\nKnown sentences:\n' + "\n".join(lines)
    raw = ask(CLASSIFY_SYSTEM, prompt)
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        ans = json.loads(m.group(0))
    except ValueError:
        return None
    rid = ans.get("id")
    rec = next((r for r in cands if r["id"] == rid), None)
    if not rec:
        return None
    params = {k: int(v) for k, v in (ans.get("params") or {}).items() if isinstance(v, (int, float))}
    if guards(sentence, rec, params):
        return None
    return {"record": rec, "params": params, "how": "model", "why": "같은 뜻으로 판정"}

def match(sentence, records, ask=None):
    """Rule first; the model only when a candidate exists and `ask(system, prompt)` is given."""
    m = match_rule(sentence, records)
    if m or not ask:
        return m
    cands = candidates(sentence, records)
    if not cands:
        return None
    return match_model(sentence, cands, ask)
