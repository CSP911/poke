/**
 * POKE Edge — ESP32-C3 (RISC-V) UART Mode
 *
 * USB-Serial/JTAG interface + POKE binary protocol.
 * No WiFi needed — direct USB cable connection to hub.
 *
 * Protocol:
 *   Request:  "POKE" (4) + payload_len (4 LE) + payload
 *   Response: "RESP" (4) + payload_len (4 LE) + payload
 *   Event:    "EVNT" (4) + payload_len (4 LE) + JSON payload  (edge → hub, unsolicited)
 *
 * Payload commands:
 *   "PING"                → "PONG"
 *   "INFO"                → JSON device info (with supported commands list)
 *   "EXEC" + code_bytes   → execute code, return result
 *   "GPIO"                → JSON GPIO states
 *   "TEMP"                → JSON temperature (internal sensor, calibrated)
 *   "GPOS" + pin(1) + val(1) → set GPIO output
 *   "EMON" + JSON config  → configure event monitor
 *   "ESTOP"               → stop all event monitors
 */

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "freertos/queue.h"
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

/* ── Event monitor state ── */
#define BOOT_BUTTON_PIN  9

typedef struct {
    bool     gpio_enabled;         /* monitor GPIO interrupts */
    uint8_t  gpio_pin;             /* which pin to watch */
    bool     temp_enabled;         /* monitor temperature threshold */
    float    temp_threshold;       /* celsius threshold */
    uint8_t  temp_op;              /* 0=above, 1=below */
    uint32_t temp_interval_ms;     /* check interval */
    bool     temp_fired;           /* already fired (prevent spam) */
} event_monitor_t;

static event_monitor_t monitor = {0};
static QueueHandle_t gpio_evt_queue = NULL;

/* ── USB-Serial/JTAG init ── */
static void usb_serial_init(void) {
    usb_serial_jtag_driver_config_t cfg = {
        .rx_buffer_size = 4096,
        .tx_buffer_size = 4096,
    };
    ESP_ERROR_CHECK(usb_serial_jtag_driver_install(&cfg));
}

/* ── Send frame (RESP or EVNT) via USB ── */
static void send_frame(const char *magic, const uint8_t *payload, uint32_t len) {
    uint8_t header[8];
    header[0] = magic[0]; header[1] = magic[1]; header[2] = magic[2]; header[3] = magic[3];
    header[4] = (len)       & 0xFF;
    header[5] = (len >> 8)  & 0xFF;
    header[6] = (len >> 16) & 0xFF;
    header[7] = (len >> 24) & 0xFF;
    usb_serial_jtag_write_bytes(header, 8, pdMS_TO_TICKS(1000));
    if (len > 0) {
        usb_serial_jtag_write_bytes(payload, len, pdMS_TO_TICKS(1000));
    }
}

static void send_resp(const uint8_t *payload, uint32_t len) {
    send_frame("RESP", payload, len);
}

static void send_resp_str(const char *str) {
    send_resp((const uint8_t *)str, strlen(str));
}

static void send_event(const char *json) {
    send_frame("EVNT", (const uint8_t *)json, strlen(json));
}

/* ── Handle EXEC command ── */
static void handle_exec(const uint8_t *code, int code_len) {
    if (code_len <= 0 || code_len > CODE_BUF_SIZE) {
        send_resp_str("error: invalid code size");
        return;
    }

    memcpy(code_buf, code, code_len);

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
    char resp[384];
    snprintf(resp, sizeof(resp),
        "{\"status\":\"alive\",\"arch\":\"riscv32\",\"chip\":\"esp32c3\","
        "\"transport\":\"usb-serial\","
        "\"commands\":[\"PING\",\"INFO\",\"EXEC\",\"GPIO\",\"TEMP\",\"GPOS\",\"EMON\",\"ESTOP\"],"
        "\"sensors\":[\"temp_internal\"],"
        "\"events\":true,"
        "\"free_heap\":%lu,"
        "\"uptime\":%llu}",
        (unsigned long)esp_get_free_heap_size(),
        (unsigned long long)(esp_timer_get_time() / 1000000ULL)
    );
    send_resp_str(resp);
}

/* ── Handle GPIO read command ── */
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

/* ── Handle GPOS (GPIO Set) command ── */
static void handle_gpos(const uint8_t *data, int data_len) {
    if (data_len < 2) {
        send_resp_str("{\"error\":\"need pin + value\"}");
        return;
    }
    uint8_t pin = data[0];
    uint8_t val = data[1];

    if (pin > 21) {
        send_resp_str("{\"error\":\"invalid pin\"}");
        return;
    }

    gpio_set_direction(pin, GPIO_MODE_OUTPUT);
    gpio_set_level(pin, val ? 1 : 0);

    char resp[64];
    snprintf(resp, sizeof(resp), "{\"pin\":%d,\"value\":%d}", pin, val);
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

/* ── GPIO ISR handler ── */
static void IRAM_ATTR gpio_isr_handler(void *arg) {
    uint32_t pin = (uint32_t)arg;
    xQueueSendFromISR(gpio_evt_queue, &pin, NULL);
}

/* ── Handle EMON (Event Monitor) command ── */
static void handle_emon(const uint8_t *data, int data_len) {
    /* Simple config: first byte = type
     * type=0x01: GPIO monitor — pin(1) + edge(1: 0=falling,1=rising,2=both)
     * type=0x02: Temp monitor — op(1: 0=above,1=below) + threshold_x10(2 LE) + interval_ms(2 LE)
     */
    if (data_len < 1) {
        send_resp_str("{\"error\":\"empty config\"}");
        return;
    }

    uint8_t type = data[0];

    if (type == 0x01 && data_len >= 3) {
        /* GPIO monitor */
        uint8_t pin = data[1];
        uint8_t edge = data[2];

        gpio_config_t io_conf = {
            .pin_bit_mask = (1ULL << pin),
            .mode = GPIO_MODE_INPUT,
            .pull_up_en = GPIO_PULLUP_ENABLE,
            .pull_down_en = GPIO_PULLDOWN_DISABLE,
            .intr_type = (edge == 0) ? GPIO_INTR_NEGEDGE :
                         (edge == 1) ? GPIO_INTR_POSEDGE : GPIO_INTR_ANYEDGE,
        };
        gpio_config(&io_conf);
        gpio_install_isr_service(0);
        gpio_isr_handler_add(pin, gpio_isr_handler, (void *)(uint32_t)pin);

        monitor.gpio_enabled = true;
        monitor.gpio_pin = pin;

        char resp[96];
        snprintf(resp, sizeof(resp), "{\"ok\":true,\"monitor\":\"gpio\",\"pin\":%d,\"edge\":%d}", pin, edge);
        send_resp_str(resp);

    } else if (type == 0x02 && data_len >= 6) {
        /* Temp monitor */
        monitor.temp_op = data[1];
        uint16_t thresh_x10 = data[2] | (data[3] << 8);
        monitor.temp_threshold = thresh_x10 / 10.0f;
        monitor.temp_interval_ms = data[4] | (data[5] << 8);
        if (monitor.temp_interval_ms < 1000) monitor.temp_interval_ms = 1000;
        monitor.temp_enabled = true;
        monitor.temp_fired = false;

        char resp[128];
        snprintf(resp, sizeof(resp),
            "{\"ok\":true,\"monitor\":\"temp\",\"op\":\"%s\",\"threshold\":%.1f,\"interval_ms\":%lu}",
            monitor.temp_op == 0 ? "above" : "below",
            monitor.temp_threshold,
            (unsigned long)monitor.temp_interval_ms);
        send_resp_str(resp);

    } else {
        send_resp_str("{\"error\":\"unknown monitor type or bad length\"}");
    }
}

/* ── Handle ESTOP command ── */
static void handle_estop(void) {
    if (monitor.gpio_enabled) {
        gpio_isr_handler_remove(monitor.gpio_pin);
        monitor.gpio_enabled = false;
    }
    monitor.temp_enabled = false;
    monitor.temp_fired = false;
    send_resp_str("{\"ok\":true,\"stopped\":\"all\"}");
}

/* ── Event monitor task — checks GPIO queue + temperature ── */
static void event_monitor_task(void *arg) {
    uint32_t gpio_pin;
    TickType_t last_temp_check = 0;

    while (1) {
        /* Check GPIO events (non-blocking, 50ms timeout) */
        if (monitor.gpio_enabled && xQueueReceive(gpio_evt_queue, &gpio_pin, pdMS_TO_TICKS(50))) {
            int val = gpio_get_level(gpio_pin);
            char evt[128];
            snprintf(evt, sizeof(evt),
                "{\"type\":\"gpio\",\"pin\":%lu,\"value\":%d,\"edge\":\"%s\"}",
                (unsigned long)gpio_pin, val, val ? "rising" : "falling");
            ESP_LOGI(TAG, "EVENT: GPIO %lu = %d", (unsigned long)gpio_pin, val);
            send_event(evt);
        } else {
            vTaskDelay(pdMS_TO_TICKS(50));
        }

        /* Check temperature threshold */
        if (monitor.temp_enabled && tsens) {
            TickType_t now = xTaskGetTickCount();
            if ((now - last_temp_check) * portTICK_PERIOD_MS >= monitor.temp_interval_ms) {
                last_temp_check = now;
                float celsius = 0;
                if (temperature_sensor_get_celsius(tsens, &celsius) == ESP_OK) {
                    bool triggered = false;
                    if (monitor.temp_op == 0 && celsius > monitor.temp_threshold) triggered = true;
                    if (monitor.temp_op == 1 && celsius < monitor.temp_threshold) triggered = true;

                    if (triggered && !monitor.temp_fired) {
                        monitor.temp_fired = true;
                        char evt[128];
                        snprintf(evt, sizeof(evt),
                            "{\"type\":\"temp\",\"celsius\":%.1f,\"threshold\":%.1f,\"op\":\"%s\"}",
                            celsius, monitor.temp_threshold,
                            monitor.temp_op == 0 ? "above" : "below");
                        ESP_LOGI(TAG, "EVENT: temp %.1f°C %s %.1f°C",
                            celsius, monitor.temp_op == 0 ? ">" : "<", monitor.temp_threshold);
                        send_event(evt);
                    } else if (!triggered) {
                        monitor.temp_fired = false;  /* reset when condition clears */
                    }
                }
            }
        }
    }
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
    } else if (strcmp(cmd, "GPOS") == 0) {
        handle_gpos(payload + 4, payload_len - 4);
    } else if (strcmp(cmd, "EMON") == 0) {
        handle_emon(payload + 4, payload_len - 4);
    } else if (memcmp(cmd, "ESTO", 4) == 0) {
        handle_estop();
    } else {
        char err[64];
        snprintf(err, sizeof(err), "error: unknown command %.4s", cmd);
        send_resp_str(err);
    }
}

/* ── USB receive + frame parse task ── */
static void usb_rx_task(void *arg) {
    uint8_t tmp[256];

    while (1) {
        int len = usb_serial_jtag_read_bytes(tmp, sizeof(tmp), pdMS_TO_TICKS(100));
        if (len <= 0) continue;

        int copy = len;
        if (rx_pos + copy > RX_BUF_SIZE) copy = RX_BUF_SIZE - rx_pos;
        if (copy > 0) {
            memcpy(rx_buf + rx_pos, tmp, copy);
            rx_pos += copy;
        }

        while (rx_pos >= 8) {
            if (rx_buf[0] != 'P' || rx_buf[1] != 'O' || rx_buf[2] != 'K' || rx_buf[3] != 'E') {
                int found = -1;
                for (int i = 1; i <= rx_pos - 4; i++) {
                    if (rx_buf[i] == 'P' && rx_buf[i+1] == 'O' && rx_buf[i+2] == 'K' && rx_buf[i+3] == 'E') {
                        found = i; break;
                    }
                }
                if (found < 0) {
                    if (rx_pos > 3) { memmove(rx_buf, rx_buf + rx_pos - 3, 3); rx_pos = 3; }
                    break;
                }
                memmove(rx_buf, rx_buf + found, rx_pos - found);
                rx_pos -= found;
                continue;
            }

            uint32_t plen = rx_buf[4] | (rx_buf[5] << 8) | (rx_buf[6] << 16) | (rx_buf[7] << 24);
            if (plen > RX_BUF_SIZE - 8) { memmove(rx_buf, rx_buf + 4, rx_pos - 4); rx_pos -= 4; continue; }
            if ((uint32_t)rx_pos < 8 + plen) break;

            process_frame(rx_buf + 8, plen);

            uint32_t frame_size = 8 + plen;
            memmove(rx_buf, rx_buf + frame_size, rx_pos - frame_size);
            rx_pos -= frame_size;
        }
    }
}

/* ── Main ── */
void app_main(void) {
    usb_serial_init();

    ESP_LOGI(TAG, "=================================");
    ESP_LOGI(TAG, "  POKE Edge — ESP32-C3 (RISC-V)");
    ESP_LOGI(TAG, "  Mode: USB-Serial/JTAG");
    ESP_LOGI(TAG, "=================================");

    /* Init temperature sensor */
    temperature_sensor_config_t tsens_cfg = TEMPERATURE_SENSOR_CONFIG_DEFAULT(-10, 80);
    ESP_ERROR_CHECK(temperature_sensor_install(&tsens_cfg, &tsens));
    ESP_ERROR_CHECK(temperature_sensor_enable(tsens));
    float t;
    temperature_sensor_get_celsius(tsens, &t);
    ESP_LOGI(TAG, "Temperature sensor: %.1f°C", t);

    /* GPIO event queue */
    gpio_evt_queue = xQueueCreate(16, sizeof(uint32_t));

    /* Start tasks */
    xTaskCreate(usb_rx_task, "poke_usb_rx", 4096, NULL, 10, NULL);
    xTaskCreate(event_monitor_task, "poke_evt_mon", 4096, NULL, 5, NULL);

    ESP_LOGI(TAG, "POKE Edge ready.");
}
