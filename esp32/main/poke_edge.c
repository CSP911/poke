/**
 * POKE Edge — ESP32-C3 (RISC-V)
 *
 * WiFi + HTTP server + RISC-V code injection.
 * Same protocol as x86 edge: POST /poke, GET /health
 */

#include <stdio.h>
#include <string.h>
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_http_server.h"
#include "nvs_flash.h"
#include "esp_netif.h"
#include "driver/gpio.h"

static const char *TAG = "POKE";

/* ── WiFi Config ── */
#define WIFI_SSID      CONFIG_WIFI_SSID
#define WIFI_PASS      CONFIG_WIFI_PASSWORD

/* ── Code Execution Buffer (in IRAM for execute permission) ── */
#define CODE_BUF_SIZE 2048
static uint8_t __attribute__((aligned(4), section(".iram1"))) code_buf[CODE_BUF_SIZE];

/* Result buffer */
#define RESULT_BUF_SIZE 256
static char result_buf[RESULT_BUF_SIZE];
static int result_len = 0;

/* ── POST /poke — inject and execute RISC-V code ── */
static esp_err_t poke_handler(httpd_req_t *req) {
    int len = req->content_len;
    if (len <= 0 || len > CODE_BUF_SIZE) {
        httpd_resp_send_err(req, HTTPD_400_BAD_REQUEST, "invalid size");
        return ESP_FAIL;
    }

    /* Receive binary */
    int received = httpd_req_recv(req, (char *)code_buf, len);
    if (received != len) {
        httpd_resp_send_err(req, HTTPD_500_INTERNAL_SERVER_ERROR, "recv error");
        return ESP_FAIL;
    }

    ESP_LOGI(TAG, "Code received: %d bytes", len);

    /* Check for RET instruction (RISC-V: 0x8067 = ret = jalr x0, x1, 0) */
    int has_ret = 0;
    for (int i = 0; i < len - 1; i++) {
        if (code_buf[i] == 0x67 && code_buf[i+1] == 0x80) { has_ret = 1; break; }
        /* Also check compressed ret: 0x8082 */
        if (code_buf[i] == 0x82 && code_buf[i+1] == 0x80) { has_ret = 1; break; }
    }

    /* Clear result */
    memset(result_buf, 0, RESULT_BUF_SIZE);
    result_len = 0;
    uint32_t ret_a0 = 0;

    if (has_ret) {
        /* Flush instruction cache */
        asm volatile("fence.i");

        /* Execute: function returns result in a0 (RISC-V calling convention) */
        uint32_t (*code_fn)(char *rbuf, int *rlen) = (uint32_t (*)(char *, int *))code_buf;
        ret_a0 = code_fn(result_buf, &result_len);

        ESP_LOGI(TAG, "Executed, a0=%lu", (unsigned long)ret_a0);
    } else {
        ESP_LOGW(TAG, "No RET found, rejected");
    }

    /* Build response */
    char resp[128];
    if (!has_ret) {
        snprintf(resp, sizeof(resp), "no RET found, not executed\n");
    } else if (result_len > 0) {
        snprintf(resp, sizeof(resp), "%.*s", result_len, result_buf);
    } else {
        snprintf(resp, sizeof(resp), "a0=%lu\n", (unsigned long)ret_a0);
    }

    httpd_resp_set_type(req, "text/plain");
    httpd_resp_send(req, resp, strlen(resp));
    return ESP_OK;
}

/* ── GET /health ── */
static esp_err_t health_handler(httpd_req_t *req) {
    /* Get WiFi info */
    esp_netif_ip_info_t ip_info;
    esp_netif_t *netif = esp_netif_get_handle_from_ifkey("WIFI_STA_DEF");
    esp_netif_get_ip_info(netif, &ip_info);

    char resp[256];
    snprintf(resp, sizeof(resp),
        "{\"status\":\"alive\",\"arch\":\"riscv32\",\"chip\":\"esp32c3\","
        "\"ip\":\"%d.%d.%d.%d\","
        "\"free_heap\":%lu,"
        "\"uptime\":%lu}",
        IP2STR(&ip_info.ip),
        (unsigned long)esp_get_free_heap_size(),
        (unsigned long)(xTaskGetTickCount() * portTICK_PERIOD_MS / 1000)
    );

    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, resp, strlen(resp));
    return ESP_OK;
}

/* ── GET /gpio — read all GPIO states ── */
static esp_err_t gpio_handler(httpd_req_t *req) {
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
    httpd_resp_set_type(req, "application/json");
    httpd_resp_send(req, resp, strlen(resp));
    return ESP_OK;
}

/* ── HTTP Server ── */
static httpd_handle_t start_webserver(void) {
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.server_port = 80;
    httpd_handle_t server = NULL;

    if (httpd_start(&server, &config) == ESP_OK) {
        httpd_uri_t poke_uri = { .uri = "/poke", .method = HTTP_POST, .handler = poke_handler };
        httpd_uri_t health_uri = { .uri = "/health", .method = HTTP_GET, .handler = health_handler };
        httpd_uri_t gpio_uri = { .uri = "/gpio", .method = HTTP_GET, .handler = gpio_handler };
        httpd_register_uri_handler(server, &poke_uri);
        httpd_register_uri_handler(server, &health_uri);
        httpd_register_uri_handler(server, &gpio_uri);
        ESP_LOGI(TAG, "HTTP server started on port 80");
    }
    return server;
}

/* ── WiFi Event Handler ── */
static void wifi_event_handler(void *arg, esp_event_base_t base, int32_t id, void *data) {
    if (base == WIFI_EVENT && id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        ESP_LOGW(TAG, "WiFi disconnected, reconnecting...");
        esp_wifi_connect();
    } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        ip_event_got_ip_t *event = (ip_event_got_ip_t *)data;
        ESP_LOGI(TAG, "Connected! IP: " IPSTR, IP2STR(&event->ip_info.ip));
        start_webserver();
    }
}

/* ── WiFi Init ── */
static void wifi_init(void) {
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t inst_any, inst_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &wifi_event_handler, NULL, &inst_any));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &wifi_event_handler, NULL, &inst_ip));

    wifi_config_t wifi_config = {
        .sta = {
            .ssid = WIFI_SSID,
            .password = WIFI_PASS,
        },
    };
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "WiFi connecting to %s...", WIFI_SSID);
}

/* ── Main ── */
void app_main(void) {
    ESP_LOGI(TAG, "POKE Edge — ESP32-C3 (RISC-V)");
    ESP_LOGI(TAG, "Initializing...");

    /* Init NVS (required for WiFi) */
    esp_err_t ret = nvs_flash_init();
    if (ret == ESP_ERR_NVS_NO_FREE_PAGES || ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        ret = nvs_flash_init();
    }
    ESP_ERROR_CHECK(ret);

    /* Start WiFi → HTTP server starts on connect */
    wifi_init();

    ESP_LOGI(TAG, "POKE Edge ready. Waiting for WiFi...");
}
