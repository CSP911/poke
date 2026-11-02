/* POKE sentence image as a returning PXE program (NBP) for a PC that stays in its network
 * bootloader (iPXE) between sentences. iPXE loads this at 0000:7C00 and far-calls it in real mode.
 *
 *   real mode   save iPXE's segments, stack, flags, interrupt masks; protected mode
 *   32-bit      copy the payload (runtime + op, linked at 1 MiB) up to 1 MiB and run it
 *   payload     the usual sentence boot; at the end it zeroes everything the run used, turns paging
 *               off and jumps back here (eax unused) instead of resetting the PC
 *   32 → 16     reload this GDT, leave protected mode
 *   real mode   restore iPXE's interrupt table, PIC, timer, segments; zero the payload copy that
 *               was loaded after this stub; return to iPXE with AX = 0
 * What remains of ours after the return: this stub's code (no part of the op) until iPXE's next load
 * overwrites 0x7C00. The builder patches payload_off / payload_len / payload_entry. */
        .code16
        .text
        .globl _start
_start:
        jmp     over
        .align 4
        .globl payload_off, payload_len, payload_entry
payload_off:    .long 0                      /* offset of the payload from _start */
payload_len:    .long 0
payload_entry:  .long 0                      /* the payload's _start_nbp */
save_ss:        .word 0
save_sp:        .word 0
save_ds:        .word 0
save_es:        .word 0
save_fs:        .word 0
save_gs:        .word 0
save_m1:        .byte 0
save_m2:        .byte 0
        .align 8
gdt:    .quad   0
        .quad   0x00CF9A000000FFFF           /* 0x08 32-bit code */
        .quad   0x00CF92000000FFFF           /* 0x10 32-bit data */
        .quad   0x00009A000000FFFF           /* 0x18 16-bit code (base 0, 64 KiB) */
        .quad   0x000092000000FFFF           /* 0x20 16-bit data */
gdtr:   .word   gdtr - gdt - 1
        .long   gdt
rm_idtr: .word  0x3FF
        .long   0

over:
        pushfw                               /* iPXE's flags, popped just before returning */
        cli
        mov     %ss, %cs:save_ss
        mov     %sp, %cs:save_sp
        mov     %ds, %cs:save_ds
        mov     %es, %cs:save_es
        mov     %fs, %cs:save_fs
        mov     %gs, %cs:save_gs
        in      $0x21, %al
        mov     %al, %cs:save_m1
        in      $0xA1, %al
        mov     %al, %cs:save_m2
        xor     %ax, %ax
        mov     %ax, %ds
        lgdtl   gdtr
        mov     %cr0, %eax
        or      $1, %eax
        mov     %eax, %cr0
        ljmpl   $0x08, $pm32

        .code32
pm32:
        mov     $0x10, %ax
        mov     %ax, %ds
        mov     %ax, %es
        mov     %ax, %fs
        mov     %ax, %gs
        mov     %ax, %ss
        mov     $0x7000, %esp                /* below 0x7C00: free conventional memory */
        mov     $_start, %esi
        add     payload_off, %esi
        mov     $0x100000, %edi
        mov     payload_len, %ecx
        cld
        rep movsb
        mov     $pm_return, %eax             /* where the payload comes back to */
        jmp     *payload_entry

        .globl pm_return
pm_return:                                   /* from the payload: interrupts off, paging off */
        cli
        lgdtl   gdtr                         /* first: the payload's GDT was in what it just zeroed */
        ljmp    $0x08, $pm_back
pm_back:
        mov     $0x10, %ax
        mov     %ax, %ds
        mov     %ax, %es
        mov     %ax, %ss
        mov     $0x7000, %esp
        /* zero the payload copy that came in after this stub (the op's code and data were in it) */
        mov     $_start, %edi
        add     payload_off, %edi
        mov     payload_len, %ecx
        xor     %eax, %eax
        cld
        rep stosb
        ljmp    $0x18, $pm16

        .code16
pm16:
        mov     $0x20, %ax
        mov     %ax, %ds
        mov     %ax, %es
        mov     %ax, %fs
        mov     %ax, %gs
        mov     %ax, %ss
        mov     %cr0, %eax
        and     $0xFFFFFFFE, %eax
        mov     %eax, %cr0
        ljmp    $0, $rm

rm:
        xor     %ax, %ax
        mov     %ax, %ds
        mov     %ax, %es
        mov     %ax, %ss
        mov     $0x7000, %sp
        lidtl   rm_idtr                      /* the real-mode interrupt vector table */
        /* PIC back to the BIOS layout (IRQ0–7 → 0x08, IRQ8–15 → 0x70) and iPXE's masks */
        mov     $0x11, %al
        out     %al, $0x20
        out     %al, $0xA0
        mov     $0x08, %al
        out     %al, $0x21
        mov     $0x70, %al
        out     %al, $0xA1
        mov     $0x04, %al
        out     %al, $0x21
        mov     $0x02, %al
        out     %al, $0xA1
        mov     $0x01, %al
        out     %al, $0x21
        out     %al, $0xA1
        mov     save_m1, %al
        out     %al, $0x21
        mov     save_m2, %al
        out     %al, $0xA1
        /* PIT channel 0 back to 18.2 Hz (mode 3, divisor 65536) */
        mov     $0x36, %al
        out     %al, $0x43
        xor     %al, %al
        out     %al, $0x40
        out     %al, $0x40
        /* iPXE's segments, stack, flags; AX = 0 (success) */
        mov     save_ds, %ax
        mov     save_es, %bx
        mov     save_fs, %cx
        mov     save_gs, %dx
        mov     save_ss, %si
        mov     save_sp, %di
        mov     %si, %ss
        mov     %di, %sp
        mov     %bx, %es
        mov     %cx, %fs
        mov     %dx, %gs
        mov     %ax, %ds
        popfw
        xor     %ax, %ax
        lret
