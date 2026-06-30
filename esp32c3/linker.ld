/* POKE OS ESP32-C3 — Linker Script (Direct Boot)
 *
 * Flash is mapped by ROM bootloader:
 *   irom: 0x42000000 (code execution via cache)
 *   drom: 0x3C000000 (read-only data via cache)
 *   ram:  0x3FC80000 (320KB SRAM for .data, .bss, stack)
 */

/* ROM function addresses (ESP32-C3) */
ets_delay_us = 0x40000050;
memcpy = 0x40000358;
memset = 0x40000354;
memcmp = 0x40000360;
strlen = 0x40000374;

/* ROM PHY functions */
phy_get_romfuncs = 0x400018fc;
chip726_phyrom_version_num = 0x40001c34;
rom_phy_param_addr = 0x40001be4;
rom_phy_reg_init = 0x40001be8;
rom_phy_wakeup_init = 0x40001bf0;
rom_phy_i2c_init1 = 0x40001bf4;
rom_phy_bbpll_cal = 0x40001bdc;
rom_phy_ant_init = 0x40001c24;
rom_phy_freq_mem_backup = 0x40001c20;
rom_phy_xpd_rf = 0x40001a78;
rom_phy_close_rf = 0x40001c18;
rom_phy_xpd_tsens = 0x40001c1c;
rom_phy_dig_reg_backup = 0x40001c30;
rom_agc_reg_init = 0x40001a54;
rom_bb_reg_init = 0x40001a58;
rom_bt_filter_reg = 0x40001bd8;
rom_bt_tx_dig_gain = 0x40001bb4;
rom_index_to_txbbgain = 0x40001964;
rom_open_i2c_xpd = 0x40001af8;
rom_pbus_xpd_tx_on = 0x400019b0;
rom_set_chan_reg = 0x40001bec;
rom_set_tx_dig_gain = 0x400019f0;
rom_set_txcap_reg = 0x400019f4;
rom_tsens_code_read = 0x40001b04;
rom_txbbgain_to_index = 0x40001a0c;
rom_wifi_agc_sat_gain = 0x40001bd0;
rom_write_txrate_power_offset = 0x40001a8c;

/* ROM PHY eco3 functions (ram1_ → rom1_ aliases) */
rom1_chip_i2c_readReg = 0x40001924;
rom1_chip_i2c_writeReg = 0x40001928;
rom1_set_noise_floor = 0x400019e8;
rom1_disable_wifi_agc = 0x40001a1c;
rom1_enable_wifi_agc = 0x40001a20;
rom1_tx_paon_set = 0x40001a44;
rom1_set_pbus_reg = 0x40001a70;
rom1_get_rate_fcc_index = 0x40001a90;
rom1_read_sar2_code = 0x40001aa4;
rom2_temp_to_power1 = 0x40001ab4;
rom1_get_i2c_hostid = 0x40001ac8;
rom2_tsens_read_init1 = 0x40001b00;
rom1_phy_en_hw_set_freq = 0x40001b20;
rom1_phy_dis_hw_set_freq = 0x40001b24;
rom1_bt_get_tx_gain = 0x40001bb8;
rom1_get_chan_target_power = 0x40001bbc;
rom1_wifi_tx_dig_gain = 0x40001bc4;
rom1_wifi_get_tx_gain = 0x40001bc8;
rom1_fe_i2c_reg_renew = 0x40001bcc;
rom1_i2c_master_reset = 0x40001bd4;
rom1_phy_wakeup_init = 0x40001bf0;
rom1_phy_i2c_init1 = 0x40001bf4;
rom1_tsens_temp_read = 0x40001bf8;
rom1_wifi_set_tx_gain = 0x40001c04;
rom1_txpwr_cal_track = 0x40001c08;
rom1_bt_set_tx_gain = 0x40001c10;
rom1_phy_close_rf = 0x40001c18;
rom2_pll_cap_mem_update = 0x40001e04;
rom2_phy_i2c_enter_critical = 0x40001e08;
rom2_phy_i2c_exit_critical = 0x40001e0c;
rom2_rfpll_cap_correct = 0x40001e10;
rom2_write_pll_cap = 0x40001e14;
rom2_read_pll_cap = 0x40001e18;

/* ram1_/ram2_ aliases to rom1_/rom2_ */
ram1_fe_i2c_reg_renew = rom1_fe_i2c_reg_renew;
ram1_phy_close_rf = rom1_phy_close_rf;
ram1_phy_dis_hw_set_freq = rom1_phy_dis_hw_set_freq;
ram1_phy_wakeup_init = rom1_phy_wakeup_init;
ram1_set_pbus_reg = rom1_set_pbus_reg;
ram1_wifi_set_tx_gain = rom1_wifi_set_tx_gain;
ram2_rfpll_cap_correct = rom2_rfpll_cap_correct;

/* ROM coex */
wifi_rf_phy_enable = 0x4000187c;
wifi_rf_phy_disable = 0x40001878;
wifi_get_macaddr = 0x40001874;

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
    _data_lma = ORIGIN(drom) + LOADADDR(.data);
    _data_size = _edata - _data_start;

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
