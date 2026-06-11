; ============================================
; POKE OS — Stage 1 Bootloader
; Real Mode → Protected Mode → Kernel Jump
; ============================================

[BITS 16]
[ORG 0x7C00]

; ── Stage 1: Real Mode Setup ──

start:
    cli
    xor ax, ax
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov sp, 0x7C00

    ; Save boot drive
    mov [boot_drive], dl

    ; Print boot message
    mov si, msg_boot
    call print_string_16

    ; Load kernel in 2 chunks to 0x10000 (64KB mark)
    ; Avoids overwriting bootloader at 0x7C00
    ; Chunk 1: 54 sectors (27KB) → 0x10000
    mov ax, 0x1000      ; segment 0x1000 → linear 0x10000
    mov es, ax
    mov ah, 0x02
    mov al, 54
    mov ch, 0
    mov cl, 2
    mov dh, 0
    mov dl, [boot_drive]
    mov bx, 0x0000      ; offset 0 within segment
    int 0x13
    jc disk_error

    ; Chunk 2: 26 sectors (13KB) → 0x16C00 (right after chunk 1)
    mov ax, 0x16C0      ; segment 0x16C0 → linear 0x16C00
    mov es, ax
    mov ah, 0x02
    mov al, 26
    mov ch, 0
    mov cl, 56          ; sector 56 (continuing from chunk 1)
    mov dh, 0
    mov dl, [boot_drive]
    mov bx, 0x0000
    int 0x13
    ; ignore error (kernel might be < 40KB)

    ; Restore ES
    xor ax, ax
    mov es, ax

    ; Enable A20 line
    call enable_a20

    ; Load GDT
    lgdt [gdt_descriptor]

    ; Switch to Protected Mode
    mov eax, cr0
    or eax, 1
    mov cr0, eax

    ; Far jump to 32-bit code
    jmp 0x08:protected_mode_entry

; ── A20 Line Enable ──

enable_a20:
    in al, 0x92
    or al, 2
    out 0x92, al
    ret

; ── 16-bit Print ──

print_string_16:
    lodsb
    or al, al
    jz .done
    mov ah, 0x0E
    int 0x10
    jmp print_string_16
.done:
    ret

disk_error:
    mov si, msg_disk_err
    call print_string_16
    jmp $

; ── Data ──

boot_drive:  db 0
msg_boot:    db "POKE OS booting...", 13, 10, 0
msg_disk_err: db "Disk error!", 0

; ── GDT ──

gdt_start:
    ; Null descriptor
    dq 0

    ; Code segment: base=0, limit=4GB, exec/read, 32-bit
gdt_code:
    dw 0xFFFF       ; limit low
    dw 0x0000       ; base low
    db 0x00         ; base mid
    db 10011010b    ; access: present, ring0, code, exec/read
    db 11001111b    ; flags: 4KB gran, 32-bit, limit high
    db 0x00         ; base high

    ; Data segment: base=0, limit=4GB, read/write, 32-bit
gdt_data:
    dw 0xFFFF
    dw 0x0000
    db 0x00
    db 10010010b    ; access: present, ring0, data, read/write
    db 11001111b
    db 0x00
gdt_end:

gdt_descriptor:
    dw gdt_end - gdt_start - 1
    dd gdt_start

; ── Protected Mode Entry (32-bit) ──

[BITS 32]
protected_mode_entry:
    ; Setup segments
    mov ax, 0x10        ; data segment selector
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax
    mov esp, 0x90000    ; stack at 576KB

    ; Jump to kernel at 0x10000
    jmp 0x10000

; ── Boot Sector Padding ──

times 510-($-$$) db 0
dw 0xAA55
