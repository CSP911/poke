"""messages_ko — what the person reads: Korean, short, no tracebacks.

    explain(exc)                     → (한 줄 설명, 다음에 할 일)       for anything that went wrong on the hub
    boot_problem(boot)               → 한 줄 or None                    for a boot that did not end cleanly
    result_lines(result, labels)     → ["온도 23.8 °C (센서 해상도 0.0625 °C)", …]
    STAGE[...]                       → stage labels shared by the CLI and the demo screen
"""
import json, os, re, shutil

def _temp_resolution():
    """The TMP105 fragment's resolution: 12-bit (1/16 °C) once it sets the config register (D11), else 0.5 °C."""
    f = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "edge", "sentence", "frags", "tmp105.h")
    try:
        return "0.0625 °C" if "0x60" in open(f).read() else "0.5 °C"
    except OSError:
        return "0.5 °C"
TEMP_NOTE = f"(센서 해상도 {_temp_resolution()})"

STAGE = {
    "sentence": "문장",
    "op": "op 작성",
    "confirm": "확인",
    "check": "이미지 검사",
    "boot": "부팅",
    "result": "결과",
    "empty": "노드 비움",
}

class Refusal(Exception):
    """The hub decided not to boot (deny, unconfirmed write, out-of-range value, need, cannot). Not an error."""
    def __init__(self, kind, text, hint=""):
        super().__init__(text); self.kind, self.text, self.hint = kind, text, hint

# ── particles (조사) after numbers and Latin words ────────────────────────
_DIGIT_BATCHIM = {"0": True, "1": "ㄹ", "2": False, "3": True, "4": False, "5": False, "6": True, "7": "ㄹ", "8": "ㄹ", "9": False}
_PAIRS = [("을", "를"), ("이", "가"), ("은", "는"), ("과", "와"), ("으로", "로"), ("이랑", "랑"), ("이나", "나")]
_LATIN_BATCHIM = {"led": False, "oled": False, "rtc": False, "i2c": False, "gpio": False, "pwm": False, "ok": False}

def _batchim_of_number(num):
    """Does the Korean reading of this number end in a final consonant? → True / False / "ㄹ"."""
    n = num.split(".")[-1] if "." in num else num
    n = n.lstrip("0") or "0"
    if n[-1] != "0" or n == "0":
        return _DIGIT_BATCHIM[n[-1]]
    zeros = len(n) - len(n.rstrip("0"))
    return {1: True, 2: True, 3: True}.get(zeros, True)          # 십·백·천·만 all end in a consonant

def fix_josa(text):
    """'LED 2을' → 'LED 2를', '3로' → '3으로', '7으로' → '7로' (ㄹ), 'LED을' → 'LED를'."""
    if not text:
        return text
    def choose(b, pair):
        with_b, without_b = pair
        if with_b == "으로":
            return "로" if b in (False, "ㄹ") else "으로"
        return with_b if b else without_b
    alts = "|".join(sorted({x for p in _PAIRS for x in p}, key=len, reverse=True))
    def num_repl(m):
        b = _batchim_of_number(m.group(1))
        part = m.group(2)
        pair = next(p for p in _PAIRS if part in p)
        return m.group(1) + choose(b, pair)
    text = re.sub(rf"(\d+(?:\.\d+)?)({alts})(?![가-힣])", num_repl, text)
    def lat_repl(m):
        w = m.group(1).lower()
        if w not in _LATIN_BATCHIM: return m.group(0)
        pair = next(p for p in _PAIRS if m.group(2) in p)
        return m.group(1) + choose(_LATIN_BATCHIM[w], pair)
    return re.sub(rf"\b([A-Za-z][A-Za-z0-9]*)({alts})(?![가-힣])", lat_repl, text)

def korean(line):
    return bool(re.search(r"[가-힣]", line or ""))

# ── what reached the bus (POKE-EFFECTS, reported by the runtime — the op cannot forge it) ──
def effects_lines(fx):
    """effects_report → short Korean lines; [] when there is no record."""
    if not isinstance(fx, dict) or fx.get("ok") is None:
        return []
    out = []
    n = fx.get("transfers")
    acts = fx.get("actions") or []
    if acts:
        out.append("장치를 바꾼 쓰기: " + ", ".join(dict.fromkeys(f"{a.get('dev') or a.get('fn')}(0x{a['addr']:02x})" for a in acts)))
    else:
        out.append("장치를 바꾼 쓰기 없음 (읽기·센서 설정만)")
    if n is not None:
        out[-1] += f" · 버스 전송 {n}건"
    for r in fx.get("refused") or []:
        out.append(f"런타임이 막은 전송: 0x{r['addr']:02x} {r['count']}건 (장치에 닿지 않음)")
    for u in fx.get("undeclared") or []:
        out.append(f"⚠ 허용 목록 밖 전송: 0x{u['addr']:02x} — {u['what']}")
    for m in fx.get("missing") or []:
        out.append(f"⚠ 선언했지만 일어나지 않은 쓰기: {m}")
    return out

# ── hub-side failures ───────────────────────────────────────────────────────
def explain(e):
    s = str(e)
    name = type(e).__name__
    if isinstance(e, Refusal):
        return e.text, e.hint
    if name == "CLIError" or "claude -p" in s:
        if "login" in s.lower() or "auth" in s.lower():
            return "Claude Code에 로그인되어 있지 않아 op를 쓸 수 없습니다.", "터미널에서 `claude`를 한 번 실행해 로그인하세요. 이미 들은 문장은 로그인 없이도 됩니다."
        return "Claude Code(claude -p)가 op를 돌려주지 못했습니다.", "잠시 뒤 다시 말해 보세요. 자세한 내용은 --debug."
    if isinstance(e, FileNotFoundError):
        tool = getattr(e, "filename", "") or s
        if "claude" in str(tool):
            return "Claude Code CLI(`claude`)를 찾을 수 없습니다.", "Claude Code를 설치하세요. 이미 들은 문장은 없이도 됩니다."
        if "qemu" in str(tool):
            return "트윈(QEMU)을 찾을 수 없습니다.", "brew install qemu"
        if "aarch64" in str(tool):
            return "이미지를 만들 컴파일러(aarch64-elf-gcc)가 없습니다.", "brew install aarch64-elf-gcc aarch64-elf-binutils"
        return f"필요한 파일이 없습니다: {tool}", ""
    if "no acceptable op" in s:
        why = s.split(":", 1)[-1].strip()
        return "이 문장으로 안전한 op를 만들지 못했습니다 (세 번 시도).", f"마지막 이유: {why[:200]} — 다르게 말해 보세요."
    if "undefined reference" in s or "aarch64-elf-ld" in s or "aarch64-elf-gcc" in s and "error" in s:
        return "이미지를 조립하지 못했습니다 (허브의 빌드 오류).", "런타임이나 조각이 고쳐지는 중일 수 있습니다. 잠시 뒤 다시 해 보거나 --debug로 확인하세요."
    if "image check" in s or "refused" in s.lower():
        return "이미지 검사가 부팅을 막았습니다.", s[:240]
    if "virtual bootloader" in s:
        return "가상 부트로더가 이미지를 가져가지 않았습니다.", "netboot/tftpd.py와 netboot/vboot.py가 떠 있는지 확인하세요."
    if "USB-serial" in s:
        return "실기기 보고선(USB-시리얼 어댑터)이 연결되어 있지 않습니다.", "GPIO 14 → 어댑터 RX, GND → GND (3.3 V)."
    if isinstance(e, KeyboardInterrupt):
        return "중단했습니다.", ""
    return f"허브에서 예상하지 못한 문제가 생겼습니다 ({name}).", "--debug로 다시 실행하면 자세한 내용이 나옵니다."

# ── a boot that did not end cleanly ─────────────────────────────────────────
def boot_problem(b):
    lines = b.get("lines") or []
    fault = next((l for l in lines if l.startswith("POKE-FAULT")), None)
    res = b.get("result") or {}
    if fault:
        return "op가 노드에서 오류(FAULT)를 냈습니다. 런타임이 이를 보고하고 노드를 껐습니다."
    if b.get("result_error"):
        return f"노드의 결과 줄을 읽지 못했습니다 ({b['result_error']})."
    if not b.get("powered_off"):
        if b.get("warning"):
            return "노드가 끝났다고 했지만 기계는 아직 돌고 있었습니다. 허브가 강제로 껐습니다."
        return "노드가 제한 시간 안에 끝나지 않아 허브가 강제로 껐습니다."
    if res.get("error"):
        return f"노드가 요청을 끝내지 못했습니다: {res['error']}"
    if b.get("result") is None:
        return "노드가 결과 없이 꺼졌습니다."
    return None

# ── results in Korean ───────────────────────────────────────────────────────
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)

def _fmt(v):
    return f"{v:g}" if isinstance(v, float) else str(v)

def result_lines(res, labels=None):
    """The op's own Korean labels (MANIFEST labels_ko) first, then known shapes, then key: value. Never raises."""
    if not isinstance(res, dict):
        return []
    try:
        if isinstance(labels, dict) and labels:
            return _labelled(res, labels)
    except Exception:
        pass
    out, used = [], set()
    keys = list(res)
    # temperature
    for k in keys:
        if k == "samples" and _num(res[k]):
            out.append(f"측정 {res[k]}회"); used.add(k)
        elif k != "threshold_c" and _num(res[k]) and (re.search(r"_c$|celsius|^temp$|^temperature$", k)):
            out.append(f"{_label(k, '온도')} {_fmt(res[k])} °C " + TEMP_NOTE + ""); used.add(k)
        elif re.search(r"(temp|temperature).*m[cC]$", k) and _num(res[k]) and not any(re.search(r"_c$", x) for x in keys):
            out.append(f"{_label(k, '온도')} {res[k] / 1000:g} °C " + TEMP_NOTE + ""); used.add(k)
    if any(re.search(r"_c$", x) for x in used):                   # the same temperature in milli-degrees: not twice
        used |= {k for k in keys if re.search(r"m[cC]$", k)}
    # clock (ops report KST; a bare hour/minute is shown as given)
    tk = next((k for k in keys if re.fullmatch(r"(time|local_time|time_kst)", k) and isinstance(res[k], str)), None)
    dk = next((k for k in keys if re.fullmatch(r"(date|local_date|date_kst)", k) and isinstance(res[k], str)), None)
    if tk or dk:
        out.append("시각 " + " ".join(res[k] for k in (dk, tk) if k) + (" (KST)" if any(k and "kst" in k for k in (tk, dk)) or "tz" in res else ""))
        used |= {tk, dk}
        used |= {k for k in keys if k in ("hour", "minute", "second", "year", "month", "day", "tz", "utc_offset_min")}
    # LEDs
    for k in keys:
        if k in used: continue
        m = re.fullmatch(r"led_?(\d+)(_state)?", k)
        if m and isinstance(res[k], str):
            out.append(f"LED {m.group(1)}번 {'켜짐' if res[k].lower() == 'on' else '꺼짐' if res[k].lower() == 'off' else res[k]}"); used.add(k)
    if "led" in res and _num(res["led"]) and isinstance(res.get("state"), str):
        out.append(f"LED {res['led']}번 {'켜짐' if res['state'].lower() == 'on' else '꺼짐' if res['state'].lower() == 'off' else res['state']}")
        used |= {"led", "state", "pin_level"}
    if isinstance(res.get("leds"), str):
        out.append(f"LED {res['leds']}번 켜짐"); used |= {"leds", "leds_on"}
    # magnetometer
    if any(k.startswith("mag_") for k in keys):
        xs = [res.get(k) for k in ("mag_x", "mag_y", "mag_z")]
        if all(_num(x) for x in xs):
            out.append(f"자기장 X {xs[0]} · Y {xs[1]} · Z {xs[2]} (센서 원시값, 1 G ≈ 1090)")
        for k in keys:
            if k.startswith("mag_") or "field" in k:
                if "field" in k and _num(res[k]):
                    out.append(f"자기장 세기 {res[k]} (원시값) ≈ {res[k] / 1090:.2f} G")
                used.add(k)
    if "hit" in res:
        out.append("조건: " + ("맞음 ✓" if res["hit"] in (1, True, "1") else "아직 아님")); used.add("hit")
    if "threshold_c" in res and _num(res["threshold_c"]):
        out.append(f"기준 {_fmt(res['threshold_c'])} °C"); used.add("threshold_c")
    for k in keys:
        if k in used or k == "error": continue
        v = res[k]
        out.append(f"{k}: {json.dumps(v, ensure_ascii=False) if not isinstance(v, (int, float, str)) else _fmt(v)}")
    return out

def _labelled(res, labels):
    out, note = [], False
    for k, v in res.items():
        if k == "error": continue
        lab = labels.get(k)
        if lab == "": continue                                   # hidden by the op's author
        if lab is None:
            out += result_lines({k: v}); continue
        m = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", lab)
        name, unit = (m.group(1), m.group(2)) if m else (lab, "")
        legend = dict(re.findall(r"(-?\w+)\s*=\s*([^,]+)", unit)) if "=" in unit else None   # "1=켜짐, 0=꺼짐"
        if legend is not None or re.fullmatch(r"\s*-?\d+\s*[~–-]\s*-?\d+\s*", unit or ""):
            unit = ""                                            # a legend or a range ("0~23") is not a unit
        val = {"on": "켜짐", "off": "꺼짐"}.get(v, v) if isinstance(v, str) else (_fmt(v) if _num(v) else json.dumps(v, ensure_ascii=False))
        if legend:
            val = legend.get(str(v), legend.get(str(val), val)).strip()
            words = [w.strip() for w in legend.values()] + ["여부", "확인", "상태"]
            stripped = name
            for w in words:                                      # "켜짐 확인 (1=켜짐)" / "LED 켜짐 (1=켜짐, 0=꺼짐)"
                stripped = re.sub(rf"(^|\s){re.escape(w)}(?=\s|$)", " ", stripped)
            stripped = " ".join(stripped.split())
            name = (stripped + " 상태").strip() if stripped != name else name   # → "상태 켜짐" / "LED 상태 꺼짐"
        if isinstance(val, str) and val and val in name.split():
            name = " ".join(w for w in name.split() if w != val) or "상태"    # never "켜짐 … 켜짐"
        out.append(fix_josa(f"{name} {val}{(' ' + unit) if unit else ''}"))
        if unit in ("°C", "℃") and not note:
            out[-1] += " " + TEMP_NOTE + ""; note = True
    return out

def _label(k, base):
    for w, ko in (("avg", "평균 "), ("max", "최고 "), ("min", "최저 ")):
        if w in k: return ko + base
    return base

# ── refusals ───────────────────────────────────────────────────────────────
def need(question):
    return Refusal("need", f"되물음: {question}", "답을 붙여 다시 말하거나 --answer \"…\"로 답하세요.")

def cannot(why):
    return Refusal("cannot", f"이 노드로는 할 수 없습니다: {why}", "노드를 켜지 않았습니다.")

def out_of_range(text):
    return Refusal("range", text, "노드를 켜지 않았습니다.")

def denied(reasons):
    return Refusal("deny", "허브 정책이 이 요청을 막았습니다: " + "; ".join(reasons), "노드를 켜지 않았습니다.")

def unconfirmed(summary):
    return Refusal("unconfirmed", f"확인이 필요한 요청이라 실행하지 않았습니다: {summary}", "--yes를 붙이면 확인 없이 실행합니다.")

def declined():
    return Refusal("declined", "실행하지 않았습니다.", "")

def too_long(seconds):
    return Refusal("too_long", f"한 번의 부팅은 20초까지입니다 (요청: 약 {seconds:g}초).",
                   "허브가 여러 번 나눠 부팅하는 예약 실행이 필요합니다.")

def width():
    return min(shutil.get_terminal_size((100, 20)).columns, 120)
