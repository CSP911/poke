; ============================================
; POKE OS — Kernel Entry (Assembly → C bridge)
; BSS zeroing + C main call
; ============================================

[BITS 32]

global _start
extern kernel_main
extern __bss_start
extern __bss_end

section .text
_start:
    ; Setup stack
    mov esp, 0x90000

    ; Zero BSS section
    mov edi, __bss_start
    mov ecx, __bss_end
    sub ecx, edi
    shr ecx, 2          ; bytes to dwords
    xor eax, eax
    rep stosd

    ; Call C kernel
    call kernel_main

    ; Halt if kernel returns
.halt:
    cli
    hlt
    jmp .halt
