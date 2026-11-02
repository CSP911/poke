"""Each fragment against the bench's ground truth, one boot per check (no model involved)."""
import json, time, datetime
import say

CHECKS = [
    ("tmp105", '#include "rt.h"\n#include "tmp105.h"\nvoid op_main(void){ long t; int e = tmp105_read_mC(0x48, &t); if (e) { fail("tmp105"); return; } report_int("temp_mC", t); }',
     {"t1 temperature": 21500}, lambda r, b: abs(r["temp_mC"] - 21500) <= 500),
    ("rtc", '#include "rt.h"\n#include "rtc.h"\nvoid op_main(void){ int y,mo,d,h,mi,s; if (rtc_read(0x68,&y,&mo,&d,&h,&mi,&s)) { fail("rtc"); return; }'
            ' report_int("y",y); report_int("mo",mo); report_int("d",d); report_int("h",h); report_int("mi",mi); report_int("s",s); }',
     {}, lambda r, b: abs((datetime.datetime(r["y"], r["mo"], r["d"], r["h"], r["mi"], r["s"], tzinfo=datetime.timezone.utc)
                           - datetime.datetime.now(datetime.timezone.utc)).total_seconds()) < 120),
    ("mag", '#include "rt.h"\n#include "mag.h"\nvoid op_main(void){ int x,y,z; if (mag_read(0x1e,&x,&y,&z)) { fail("mag"); return; } report_int("x",x); report_int("y",y); report_int("z",z); }',
     {"m1 mag-x": 9091, "m1 mag-y": 18182}, lambda r, b: abs(r["x"] - 100) <= 3 and abs(r["y"] - 200) <= 3),
    ("led", '#include "rt.h"\n#include "led.h"\nvoid op_main(void){ unsigned p; if (led_set(0x60,2,0) || led_set(0x60,5,0)) { fail("led"); return; }'
            ' if (led_inputs(0x60,&p)) { fail("inputs"); return; } report_int("pins", p); }',
     {}, lambda r, b: b["truth"].get("leds_on") == [2, 5] and (r["pins"] & 0x24) == 0),
]

def main():
    ok_all = True
    for name, src, bench, good in CHECKS:
        img, obj, path = say.build(src, f"selftest-{name}")
        c = say.check(obj)
        b = say.boot(path, bench)
        r = b["result"] or {}
        try:
            ok = (not r.get("error")) and good(r, b) and b["powered_off"]
        except Exception:
            ok = False
        ok_all &= ok
        print(f"{'✓' if ok else '✗'} {name:<7} image {len(img)} B · check {'ok' if c['ok'] else c['problems'][:2]} · result {json.dumps(r)} · "
              f"truth {json.dumps(b['truth'])} · power-on→off {b['boot_to_off_ms']} ms · {'off' if b['powered_off'] else 'STILL ON'}")
    print("selftest", "PASS" if ok_all else "FAIL")
    return ok_all
