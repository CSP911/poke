/* POKE sentence runtime v1 — the op's side of rt.h.
 * Linked into the op's pages (EL0 code). Each call is one `svc`; the runtime
 * at EL1 does the work. Fixed and audited once, like the runtime. */
.text
.macro CALL name, nr
.global \name
.type \name, %function
\name:
    svc     #\nr
    ret
.endm
CALL say,          1
CALL report_int,   2
CALL report_fixed, 3
CALL report_str,   4
CALL fail,         5
CALL ms,           6
CALL delay_ms,     7
CALL poke_i2c_xfer, 8                              /* i2c_bus.h: the runtime does the transfer, if allowed */

/* op_main returns here: hand the node back to the runtime */
.global op_return
.type op_return, %function
op_return:
    svc     #0
1:  b       1b
