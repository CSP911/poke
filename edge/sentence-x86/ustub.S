/* The op's side of the runtime calls (ring 3): cdecl arguments → ebx, ecx, edx; number in eax;
 * int 0x80. Linked with the op (its pages), never with the runtime. */
        .text
        .macro RTCALL fname, num
        .globl \fname
\fname:
        push    %ebx
        mov     8(%esp), %ebx
        mov     12(%esp), %ecx
        mov     16(%esp), %edx
        mov     $\num, %eax
        int     $0x80
        pop     %ebx
        ret
        .endm
        RTCALL say, 1
        RTCALL report_int, 2
        RTCALL report_fixed, 3
        RTCALL report_str, 4
        RTCALL fail, 5
        RTCALL ms, 6
        RTCALL delay_ms, 7

/* where the runtime drops the op: run it, then tell the runtime it returned */
        .globl _op_entry
_op_entry:
        call    op_main
        xor     %eax, %eax
        int     $0x80
1:      jmp     1b
