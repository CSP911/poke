/* POKE OS ESP32-C3 — Linker Script (Direct Boot)
 *
 * Flash is mapped by ROM bootloader:
 *   irom: 0x42000000 (code execution via cache)
 *   drom: 0x3C000000 (read-only data via cache)
 *   ram:  0x3FC80000 (320KB SRAM for .data, .bss, stack)
 */

ENTRY(_entry)

MEMORY
{
    irom (x):  org = 0x42000000, len = 0x400000
    drom (r):  org = 0x3C000000, len = 0x400000
    ram  (rw): org = 0x3FC80000, len = 0x50000
}

SECTIONS
{
    /* Direct Boot magic: 0xaedb041d repeated twice at flash offset 0 */
    .header : AT(0)
    {
        LONG(0xaedb041d)
        LONG(0xaedb041d)
    } > irom

    /* Entry point at flash+8 (0x42000008) */
    .text.entry ORIGIN(irom) + 8 :
    {
        KEEP(*(.text.entry))
    } > irom

    /* Code */
    .text :
    {
        *(.text .text.*)
    } > irom

    . = ALIGN(4);
    _irom_size = . - ORIGIN(irom);

    /* Read-only data (mapped to drom) */
    _drom_start = ORIGIN(drom) + _irom_size;
    .rodata _drom_start : AT(_irom_size)
    {
        *(.rodata .rodata.* .srodata .srodata.*)
    } > drom

    . = ALIGN(4);
    _drom_size = . - _drom_start;

    /* Initialized data (loaded from flash to RAM) */
    .data ORIGIN(ram) : AT(_irom_size + _drom_size)
    {
        _data_start = .;
        *(.data .data.* .sdata .sdata.*)
        . = ALIGN(4);
        _edata = .;
    } > ram

    __SDATA_BEGIN__ = _data_start;

    /* BSS (zero-initialized in RAM) */
    . = ALIGN(4);
    __bss_start = .;
    .bss :
    {
        *(.bss .bss.* .sbss .sbss.*)
        *(COMMON)
    } > ram
    . = ALIGN(4);
    __bss_end = .;

    /* Global pointer (for efficient access to small data) */
    __global_pointer$ = MIN(__SDATA_BEGIN__ + 0x800,
                            MAX(_data_start + 0x800, __bss_end - 0x800));

    /* Stack grows down from top of RAM */
    __stack_top = ORIGIN(ram) + LENGTH(ram);
}
