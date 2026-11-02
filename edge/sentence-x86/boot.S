/* POKE sentence image, x86 (32-bit protected mode). Loaded by a PC's network boot (iPXE: Multiboot).
 * Entry, interrupt/exception stubs, the ring-3 entry, and the final scrub + reset. */
        .set MB_MAGIC, 0x1BADB002
        .set MB_FLAGS, 0x00000003            /* align modules, give memory info */

        .section .multiboot, "a"
        .align 4
        .long MB_MAGIC, MB_FLAGS, -(MB_MAGIC + MB_FLAGS)

        .section .text.boot, "ax"
        .globl _start
_start:
        cli
        mov     $__kstack_top, %esp
        /* zero the op's and the runtime's bss (the op's initialised data is left): [__op_bss_start, __rt_end) */
        mov     $__op_bss_start, %edi
        mov     $__rt_end, %ecx
        sub     %edi, %ecx
        xor     %eax, %eax
        cld
        rep stosb
        call    x86_boot
1:      hlt
        jmp     1b

/* entry as a returning PXE program (nbp.S jumps here, eax = where to come back to) */
        .globl _start_nbp
_start_nbp:
        cli
        mov     %eax, nbp_ret
        mov     $__kstack_top, %esp
        mov     $__op_bss_start, %edi
        mov     $__rt_end, %ecx
        sub     %edi, %ecx
        xor     %eax, %eax
        cld
        rep stosb
        call    x86_boot
1:      hlt
        jmp     1b

/* ── exceptions 0–31: push a uniform frame and call x86_trap(frame) ── */
        .macro TRAP n
        .globl trap\n
trap\n:
        .if (\n == 8) || (\n >= 10 && \n <= 14) || (\n == 17)
        .else
        push    $0                          /* no error code from the CPU: push one */
        .endif
        push    $\n
        jmp     trap_common
        .endm
        .irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
        TRAP \n
        .endr

trap_common:
        pusha
        push    %ds
        push    %es
        mov     $0x10, %ax
        mov     %ax, %ds
        mov     %ax, %es
        push    %esp                        /* struct frame * */
        call    x86_trap                    /* returns only for the timer / a call */
        add     $4, %esp
        pop     %es                         /* the interrupted code's segments (ring 3: 0x23) */
        pop     %ds
        popa
        add     $8, %esp                    /* vector, error code */
        iret

/* IRQ0 (PIT) at vector 32 and the op's calls at vector 0x80: same frame, different vector */
        .globl irq0
irq0:   push    $0
        push    $32
        jmp     trap_common
        .globl syscall80
syscall80:
        push    $0
        push    $0x80
        jmp     trap_common

/* table of the 32 exception stubs for the IDT */
        .section .rodata.boot, "a"
        .globl trap_table
trap_table:
        .irp n, 0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29,30,31
        .long trap\n
        .endr

        .section .text.boot, "ax"
/* enter_user(eip, esp): drop to ring 3 with interrupts on, I/O privilege 0 */
        .globl enter_user
enter_user:
        mov     4(%esp), %ecx                /* eip */
        mov     8(%esp), %edx                /* user esp */
        mov     $0x23, %ax
        mov     %ax, %ds
        mov     %ax, %es
        mov     %ax, %fs
        mov     %ax, %gs
        push    $0x23                        /* ss */
        push    %edx                         /* esp */
        push    $0x202                       /* eflags: IF, IOPL 0 */
        push    $0x1B                        /* cs */
        push    %ecx                         /* eip */
        iret

/* scrub_and_return(start, end): as scrub_and_reset, but hand the PC back to the network bootloader
 * (nbp.S pm_return) instead of resetting it */
        .globl scrub_and_return
scrub_and_return:
        cli
        mov     nbp_ret, %ebx
        mov     4(%esp), %edi
        mov     8(%esp), %ecx
        mov     %cr0, %eax
        and     $0x7FFFFFFF, %eax
        mov     %eax, %cr0
        sub     %edi, %ecx
        xor     %eax, %eax
        cld
        rep stosb
        jmp     *%ebx

        .section .data.boot, "aw"
        .globl nbp_ret
nbp_ret: .long 0                             /* 0: booted by Multiboot (reset at the end) */

        .section .text.boot, "ax"
/* scrub_and_reset(start, end): zero [start, end) — the stack this runs on included — with
 * registers only, then reset the PC through the reset control register (0xCF9), falling back
 * to the keyboard controller. Nothing of the run is left in memory the image used. */
        .globl scrub_and_reset
scrub_and_reset:
        cli
        mov     4(%esp), %edi
        mov     8(%esp), %ecx
        mov     %cr0, %eax                   /* paging off: the map is identity, and the guard page */
        and     $0x7FFFFFFF, %eax            /* and the op's pages must be zeroed too */
        mov     %eax, %cr0
        sub     %edi, %ecx
        xor     %eax, %eax
        cld
        rep stosb
        mov     $0xCF9, %dx
        mov     $0x02, %al
        out     %al, %dx
        mov     $0x06, %al
        out     %al, %dx
        mov     $0x64, %dx
        mov     $0xFE, %al
        out     %al, %dx
2:      hlt
        jmp     2b
