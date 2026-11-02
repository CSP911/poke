"""The image check (v1: a lint in front of the fence) must refuse what is certainly wrong before
anything boots, and must not refuse ordinary numbers (D8). Since runtime v1 the fence stops what the
check cannot see; a case marked 'warn' passes the check with a warning and is stopped on the node
(teams/verify/edge/boottest.py boots those). Expected: refuse / warn (passes, warned) / pass (clean) / ok (not refused)."""
import say

H = '#include "rt.h"\n'
CASES = [
    ("writes the UART itself (runtime's window, not the op's)", H + 'void op_main(void){ *(volatile unsigned *)0xFE201000UL = 65; }', "refuse"),
    ("writes the power controller itself", H + 'void op_main(void){ *(volatile unsigned *)0xFE10001CUL = 0x5a000020; }', "refuse"),
    ("moves GPIO pins itself (the runtime's page since v1)", H + 'void op_main(void){ *(volatile unsigned *)0xFE200004UL = 0; }', "refuse"),
    ("touches the SD controller", H + 'void op_main(void){ (void)*(volatile unsigned *)0xFE340024UL; }', "refuse"),
    ("uses a system register instruction", H + 'void op_main(void){ __asm__ volatile("msr daifset, #2"); }', "refuse"),
    ("calls through a computed pointer", H + 'void op_main(void){ void (*f)(void) = (void (*)(void))(unsigned long)ms(); f(); }', "refuse"),
    ("calls libc (memset)", H + 'void *memset(void *, int, unsigned long);\nvoid op_main(void){ char b[64]; memset(b, 0, 64); report_int("x", b[3]); }', "refuse"),
    ("keeps an address in a table and reads it (the fence stops it)", H + 'static const unsigned long where[2] = { 0xFE340024UL, 0 };\nvoid op_main(void){ report_int("x", *(volatile unsigned *)where[ms() & 1]); }', "warn"),
    ("a -1 / -2 table and 0xFFFFFFFF are numbers, not addresses (D8)", H + 'static const int off[4] = { -1, 0, 1, -2 };\nvoid op_main(void){ volatile unsigned m = 0xFFFFFFFFu; long s = 0; for (int i = 0; i < 4; i++) s += off[ms() & 3]; report_int("s", s + (long)(m & 0xff)); }', "ok"),
    ("drives the I2C controller itself (the runtime's since V5)", H + 'void op_main(void){ *(volatile unsigned *)0xFE804000UL = 0x8080; }', "refuse"),
    ("includes a fragment its MANIFEST does not declare", '/* MANIFEST {"v": 1, "uses": [{"fn": "tmp105_read_mC"}], "max_s": 1, "summary_ko": "온도를 읽습니다."} */\n' + H + '#include "tmp105.h"\n#include "led.h"\nvoid op_main(void){ long t; if (!tmp105_read_mC(0x48, &t)) report_int("t", t); }', "refuse"),
    ("uses the I2C bus through a fragment (allowed)", H + '#include "tmp105.h"\nvoid op_main(void){ long t; if (!tmp105_read_mC(0x48, &t)) report_int("t", t); }', "pass"),
]

def main():
    good = 0
    for name, src, want in CASES:
        try:
            _, obj, _ = say.build(src, "checktest")
            c = say.check(obj)
            got = "refuse" if not c["ok"] else "warn" if c["warnings"] else "pass"
            why = "; ".join((c["problems"] or c["warnings"])[:2])
        except RuntimeError as e:
            got, why = "refuse", "did not build: " + str(e).splitlines()[0][:80]
        right = got == want or (want == "ok" and got != "refuse")
        good += right
        print(f"{'✓' if right else '✗'} {name:<66} → {got}{': ' + why[:100] if why else ''}")
    print(f"\n{good}/{len(CASES)} as expected")
    return good == len(CASES)

if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
