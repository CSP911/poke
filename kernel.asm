; ============================================
; POKE OS — Kernel
; Protected Mode, VGA text output, CLI shell
; ============================================

[BITS 32]
[ORG 0x1000]

; ── Kernel Entry ──

kernel_start:
    ; Clear screen
    call clear_screen

    ; Print welcome
    mov esi, msg_welcome
    mov edi, 0xB8000        ; VGA text buffer
    mov ah, 0x0A            ; green on black
    call print_string_32

    ; Print prompt
    mov esi, msg_prompt
    mov edi, 0xB80A0        ; line 1
    mov ah, 0x0F            ; white on black
    call print_string_32

    ; Setup keyboard handler
    call setup_keyboard

    ; Main loop
    jmp shell_loop

; ── Clear Screen ──

clear_screen:
    mov edi, 0xB8000
    mov ecx, 2000           ; 80*25 characters
    mov ax, 0x0720          ; space, light gray
    rep stosw
    ret

; ── Print String (32-bit, VGA) ──
; ESI = string, EDI = VGA position, AH = color

print_string_32:
    lodsb
    or al, al
    jz .done
    stosw                   ; write char + color
    jmp print_string_32
.done:
    ret

; ── Keyboard Setup ──

setup_keyboard:
    ; Install IRQ1 handler
    ; For now, use polling instead of interrupts
    ret

; ── Shell Loop ──

shell_loop:
    ; Poll keyboard
    call read_key
    cmp al, 0
    je shell_loop

    ; Echo character
    cmp al, 13              ; Enter key
    je .handle_enter
    cmp al, 8               ; Backspace
    je .handle_backspace

    ; Store character in buffer
    mov edi, [cursor_pos]
    mov ah, 0x0F
    stosw
    mov [cursor_pos], edi

    ; Store in command buffer
    movzx ebx, byte [cmd_len]
    mov [cmd_buffer + ebx], al
    inc byte [cmd_len]

    jmp shell_loop

.handle_enter:
    ; Null terminate command
    movzx ebx, byte [cmd_len]
    mov byte [cmd_buffer + ebx], 0

    ; Process command
    call process_command

    ; New line + prompt
    call next_line
    mov esi, msg_prompt
    mov edi, [cursor_pos]
    mov ah, 0x0F
    call print_string_32
    mov [cursor_pos], edi

    ; Reset command buffer
    mov byte [cmd_len], 0

    jmp shell_loop

.handle_backspace:
    cmp byte [cmd_len], 0
    je shell_loop
    dec byte [cmd_len]
    sub dword [cursor_pos], 2
    mov edi, [cursor_pos]
    mov word [edi], 0x0720  ; clear char
    jmp shell_loop

; ── Read Key (polling) ──

read_key:
    in al, 0x64             ; keyboard status
    test al, 1
    jz .no_key

    in al, 0x60             ; scancode
    call scancode_to_ascii
    ret

.no_key:
    xor al, al
    ret

; ── Scancode to ASCII ──

scancode_to_ascii:
    ; Only handle key press (bit 7 = 0)
    test al, 0x80
    jnz .release

    cmp al, 58              ; table size
    jae .unknown

    movzx eax, al
    mov al, [scancode_table + eax]
    ret

.release:
    xor al, al
    ret
.unknown:
    xor al, al
    ret

; ── Process Command ──

process_command:
    ; Compare command with known commands
    mov esi, cmd_buffer
    mov edi, cmd_help
    call strcmp
    je .do_help

    mov esi, cmd_buffer
    mov edi, cmd_clear
    call strcmp
    je .do_clear

    mov esi, cmd_buffer
    mov edi, cmd_info
    call strcmp
    je .do_info

    ; Unknown command
    call next_line
    mov esi, msg_unknown
    mov edi, [cursor_pos]
    mov ah, 0x0C            ; red
    call print_string_32
    mov [cursor_pos], edi
    ret

.do_help:
    call next_line
    mov esi, msg_help
    mov edi, [cursor_pos]
    mov ah, 0x0B            ; cyan
    call print_string_32
    mov [cursor_pos], edi
    ret

.do_clear:
    call clear_screen
    mov dword [cursor_pos], 0xB8000
    ret

.do_info:
    call next_line
    mov esi, msg_info
    mov edi, [cursor_pos]
    mov ah, 0x0E            ; yellow
    call print_string_32
    mov [cursor_pos], edi
    ret

; ── String Compare ──

strcmp:
    push esi
    push edi
.loop:
    lodsb
    mov cl, [edi]
    inc edi
    cmp al, cl
    jne .not_equal
    or al, al
    jz .equal
    jmp .loop
.equal:
    pop edi
    pop esi
    xor eax, eax           ; ZF = 1
    ret
.not_equal:
    pop edi
    pop esi
    or eax, 1              ; ZF = 0
    ret

; ── Next Line ──

next_line:
    mov eax, [cursor_pos]
    sub eax, 0xB8000
    xor edx, edx
    mov ecx, 160           ; bytes per line (80 chars * 2)
    div ecx
    inc eax
    mul ecx
    add eax, 0xB8000
    mov [cursor_pos], eax

    ; Scroll if at bottom
    cmp eax, 0xB8FA0       ; line 25
    jb .no_scroll
    call scroll_screen
.no_scroll:
    ret

; ── Scroll Screen ──

scroll_screen:
    mov esi, 0xB80A0        ; line 1
    mov edi, 0xB8000        ; line 0
    mov ecx, 960            ; 24 lines * 80 chars / 2 (dwords)
    rep movsd

    ; Clear last line
    mov edi, 0xB8F00
    mov ecx, 40
    mov eax, 0x07200720
    rep stosd

    sub dword [cursor_pos], 160
    ret

; ── Data ──

cursor_pos:  dd 0xB8140     ; start at line 2
cmd_buffer:  times 256 db 0
cmd_len:     db 0

msg_welcome: db " POKE OS v0.1 ", 0
msg_prompt:  db "poke> ", 0
msg_unknown: db "unknown command. type 'help'", 0
msg_help:    db "commands: help, clear, info", 0
msg_info:    db "POKE OS - inject & run. IA-32.", 0

cmd_help:    db "help", 0
cmd_clear:   db "clear", 0
cmd_info:    db "info", 0

; ── Scancode Table (US QWERTY) ──

scancode_table:
    db 0, 27              ; 0-1: none, ESC
    db '1234567890-='     ; 2-13
    db 8                  ; 14: backspace
    db 0                  ; 15: tab
    db 'qwertyuiop[]'     ; 16-27
    db 13                 ; 28: enter
    db 0                  ; 29: ctrl
    db 'asdfghjkl;'       ; 30-39
    db "'`"               ; 40-41
    db 0                  ; 42: lshift
    db '\'                ; 43
    db 'zxcvbnm,./'       ; 44-53
    db 0, 0, 0            ; 54-56: rshift, *, alt
    db ' '                ; 57: space

; Padding to fill sectors
times 8192-($-$$) db 0
