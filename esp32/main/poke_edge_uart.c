/**
 * POKE Edge — ESP32-C3 (RISC-V) UART Mode
 *
 * USB-Serial/JTAG interface + POKE binary protocol.
 * No WiFi needed — direct USB cable connection to hub.
 *
 * ESP32-C3 has a built-in USB-Serial/JTAG controller.
 * This is NOT hardware UART0 — it's a separate USB peripheral.
 *
 * Protocol (same as PROTOCOL.md §9):
 *   Request:  "POKE" (4) + payload_len (4 LE) + payload
 *   Response: "RESP" (4) + payload_len (4 LE) + payload
 *
 * Payload commands:
 *   "PING"                → "PONG"
 *   "INFO"                → JSON device info
 *   "EXEC" + code_bytes   → execute code, return result
 *   "GPIO"                → JSON GPIO states
 *   "TEMP"                → JSON temperature (internal sensor)
 */

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "driver/usb_serial_jtag.h"
#include "driver/gpio.h"
#include "driver/temperature_sensor.h"
#include "esp_timer.h"

static const char *TAG = "POKE";

/* ── Temperature sensor handle ── */
static temperature_sensor_handle_t tsens = NULL;

/* ── Code Execution Buffer (in IRAM for execute permission) ── */
#define CODE_BUF_SIZE   2048
static uint8_t __attribute__((aligned(4), section(".iram1"))) code_buf[CODE_BUF_SIZE];

/* Result buffer */
#define RESULT_BUF_SIZE 256
static char result_buf[RESULT_BUF_SIZE];
static int result_len = 0;

/* ── Receive buffer for POKE frames ── */
#define RX_BUF_SIZE     4096
static uint8_t rx_buf[RX_BUF_SIZE];
static int rx_pos = 0;

/* ── USB-Serial/JTAG init ── */
static void usb_serial_init(void) {
    usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = 4096,
        .tx_buffer_size = 4096,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
    ESP_LOGI(TAG, "USB-Serial/JTAG driver installed");
}

/* ── Send RESP frame via USB ── */
static void send_resp(const uint8_t *payload, uint32_t len) {
    uint8_t header[8];
    header[0] = 'R'; header[1] = 'E'; header[2] = 'S'; header[3] = 'P';
    header[4] = (len)       & 0xFF;
    header[5] = (len >> 8)  & 0xFF;
    header[6] = (len >> 16) & 0xFF;
    header[7] = (len >> 24) & 0xFF;
    usb_serial_jtag_write_bytes(header, 8, pdMS_TO_TICKS(1000));
    if (len > 0) {
        usb_serial_jtag_write_bytes(payload, len, pdMS_TO_TICKS(1000));
    }
}

static void send_resp_str(const char *str) {
    send_resp((const uint8_t *)str, strlen(str));
}

/* ── Handle EXEC command ── */
static void handle_exec(const uint8_t *code, int code_len) {
    if (code_len <= 0 || code_len > CODE_BUF_SIZE) {
        send_resp_str("error: invalid code size");
        return;
    }

    memcpy(code_buf, code, code_len);

    ESP_LOGI(TAG, "EXEC: %d bytes", code_len);

    /* Check for RET instruction */
    int has_ret = 0;
    for (int i = 0; i < code_len - 1; i++) {
        if (code_buf[i] == 0x67 && code_buf[i+1] == 0x80) { has_ret = 1; break; }
        if (code_buf[i] == 0x82 && code_buf[i+1] == 0x80) { has_ret = 1; break; }
    }

    memset(result_buf, 0, RESULT_BUF_SIZE);
    result_len = 0;
    uint32_t ret_a0 = 0;

    if (has_ret) {
        asm volatile("fence.i");
        uint32_t (*code_fn)(char *rbuf, int *rlen) = (uint32_t (*)(char *, int *))code_buf;
        ret_a0 = code_fn(result_buf, &result_len);
        ESP_LOGI(TAG, "OK a0=%lu", (unsigned long)ret_a0);
    } else {
        ESP_LOGW(TAG, "No RET found");
    }

    char resp[128];
    if (!has_ret) {
        snprintf(resp, sizeof(resp), "no RET found, not executed");
    } else if (result_len > 0) {
        snprintf(resp, sizeof(resp), "%.*s", result_len, result_buf);
    } else {
        snprintf(resp, sizeof(resp), "a0=%lu", (unsigned long)ret_a0);
    }
    send_resp_str(resp);
}

/* ── Handle INFO command ── */
static void handle_info(void) {
    char resp[256];
    snprintf(resp, sizeof(resp),
        "{\"status\":\"alive\",\"arch\":\"riscv32\",\"chip\":\"esp32c3\","
        "\"transport\":\"usb-serial\","
        "\"free_heap\":%lu,"
        "\"uptime\":%llu}",
        (unsigned long)esp_get_free_heap_size(),
        (unsigned long long)(esp_timer_get_time() / 1000000ULL)
    );
    send_resp_str(resp);
}

/* ── Handle GPIO command ── */
static void handle_gpio(void) {
    char resp[256];
    int len = 0;
    len += snprintf(resp + len, sizeof(resp) - len, "{\"gpio\":{");
    for (int pin = 0; pin <= 10; pin++) {
        gpio_set_direction(pin, GPIO_MODE_INPUT);
        int val = gpio_get_level(pin);
        if (pin > 0) len += snprintf(resp + len, sizeof(resp) - len, ",");
        len += snprintf(resp + len, sizeof(resp) - len, "\"%d\":%d", pin, val);
    }
    len += snprintf(resp + len, sizeof(resp) - len, "}}");
    send_resp_str(resp);
}

/* ── Handle TEMP command ── */
static void handle_temp(void) {
    char resp[128];
    if (!tsens) {
        snprintf(resp, sizeof(resp), "{\"error\":\"sensor not initialized\"}");
    } else {
        float celsius = 0;
        esp_err_t ret = temperature_sensor_get_celsius(tsens, &celsius);
        if (ret == ESP_OK) {
            snprintf(resp, sizeof(resp),
                "{\"celsius\":%.1f,\"raw_api\":true}",
                celsius);
        } else {
            snprintf(resp, sizeof(resp),
                "{\"error\":\"read failed\",\"code\":%d}", (int)ret);
        }
    }
    send_resp_str(resp);
}

/* ── Process a complete POKE frame ── */
static void process_frame(const uint8_t *payload, uint32_t payload_len) {
    if (payload_len < 4) {
        send_resp_str("error: payload too short");
        return;
    }

    char cmd[5] = {0};
    memcpy(cmd, payload, 4);

    if (strcmp(cmd, "PING") == 0) {
        send_resp_str("PONG");
    } else if (strcmp(cmd, "INFO") == 0) {
        handle_info();
    } else if (strcmp(cmd, "EXEC") == 0) {
        handle_exec(payload + 4, payload_len - 4);
    } else if (strcmp(cmd, "GPIO") == 0) {
        handle_gpio();
    } else if (strcmp(cmd, "TEMP") == 0) {
        handle_temp();
    } else {
        char err[64];
        snprintf(err, sizeof(err), "error: unknown command %.4s", cmd);
        send_resp_str(err);
    }
}

/* ── USB receive + frame parse task ── */
static void usb_rx_task(void *arg) {
    uint8_t tmp[256];

    ESP_LOGI(TAG, "Waiting for POKE frames on USB...");

    while (1) {
        int len = usb_serial_jtag_read_bytes(tmp, sizeof(tmp), pdMS_TO_TICKS(100));
        if (len <= 0) continue;

        /* Append to rx buffer */
        int copy = len;
        if (rx_pos + copy > RX_BUF_SIZE) copy = RX_BUF_SIZE - rx_pos;
        if (copy > 0) {
            memcpy(rx_buf + rx_pos, tmp, copy);
            rx_pos += copy;
        }

        /* Try to parse frames */
        while (rx_pos >= 8) {
            /* Check for "POKE" magic */
            if (rx_buf[0] != 'P' || rx_buf[1] != 'O' || rx_buf[2] != 'K' || rx_buf[3] != 'E') {
                /* Scan for next "POKE" magic */
                int found = -1;
                for (int i = 1; i <= rx_pos - 4; i++) {
                    if (rx_buf[i] == 'P' && rx_buf[i+1] == 'O' && rx_buf[i+2] == 'K' && rx_buf[i+3] == 'E') {
                        found = i;
                        break;
                    }
                }
                if (found < 0) {
                    if (rx_pos > 3) {
                        memmove(rx_buf, rx_buf + rx_pos - 3, 3);
                        rx_pos = 3;
                    }
                    break;
                }
                memmove(rx_buf, rx_buf + found, rx_pos - found);
                rx_pos -= found;
                continue;
            }

            /* Read payload length */
            uint32_t plen = rx_buf[4] | (rx_buf[5] << 8) | (rx_buf[6] << 16) | (rx_buf[7] << 24);
            if (plen > RX_BUF_SIZE - 8) {
                memmove(rx_buf, rx_buf + 4, rx_pos - 4);
                rx_pos -= 4;
                continue;
            }

            /* Wait for full frame */
            if ((uint32_t)rx_pos < 8 + plen) break;

            /* Process frame */
            process_frame(rx_buf + 8, plen);

            /* Remove processed frame */
            uint32_t frame_size = 8 + plen;
            memmove(rx_buf, rx_buf + frame_size, rx_pos - frame_size);
            rx_pos -= frame_size;
        }
    }
}

/* ── Main ── */
void app_main(void) {
    /* Init USB-Serial/JTAG first, then log */
    usb_serial_init();

    ESP_LOGI(TAG, "=================================");
    ESP_LOGI(TAG, "  POKE Edge — ESP32-C3 (RISC-V)");
    ESP_LOGI(TAG, "  Mode: USB-Serial/JTAG");
    ESP_LOGI(TAG, "=================================");

    /* Init temperature sensor (ESP-IDF driver with calibration) */
    temperature_sensor_config_t tsens_cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    ESP_ERROR_CHECK(temperature_sensor_install(&tsens_cfg, &tsens));
    ESP_ERROR_CHECK(temperature_sensor_enable(tsens));
    float t;
    temperature_sensor_get_celsius(tsens, &t);
    ESP_LOGI(TAG, "Temperature sensor: %.1f°C", t);

    xTaskCreate(usb_rx_task, "poke_usb_rx", 4096, NULL, 10, NULL);

    ESP_LOGI(TAG, "POKE Edge ready.");
}
