/*
 * firmware/src/main_standard_MQTT.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 코드명 : Standard MQTT  (Board 2 — 베이스라인 시스템)
 * 타겟 하드웨어   : ESP32-S3 DevKitC-1
 * 역할           : 고정 QoS 1, 고정 60초 주기, 표준 MQTT 발행 + 실제 Deep Sleep
 *
 * [2026-09 재설계] 공정한 비교를 위해 Deep Sleep 도입
 *   이전 버전은 Sleep을 전혀 쓰지 않고 라디오를 계속 켜둔 채 5초마다 발행했습니다.
 *   그 상태로 Gingerbread(온도/습도 기반 동적 QoS + Deep Sleep)와 비교하면, 절감률의
 *   대부분이 "적응형 QoS의 효과"가 아니라 그냥 "Sleep을 쓰느냐 안 쓰느냐의 효과"로
 *   나옵니다 — 항상 깨어있는 기기와 거의 항상 자는 기기를 비교하면 당연히 후자가
 *   압도적으로 유리하기 때문입니다(대시보드에서 봤던 99% 절감이 바로 이 착시).
 *
 *   그래서 Standard도 Gingerbread와 똑같이 실제 esp_deep_sleep_start()를 쓰되,
 *   QoS는 항상 1, 주기는 항상 60초(Gingerbread의 "정상" 상태 주기와 동일)로 고정합니다.
 *   두 노드의 유일한 차이가 "온도/습도로 QoS와 주기를 조정하느냐"가 되도록 만들어서,
 *   절감률이 적응형 QoS 자체의 효과만을 나타내게 하는 것이 목적입니다.
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │                    아키텍처 개요 (Architecture Overview)                 │
 * ├─────────────────────────────────────────────────────────────────────────┤
 * │ 1. 항상 QoS 1, 항상 60초 주기 (온도/습도를 읽긴 하지만 QoS 판단에 쓰지 않음)│
 * │ 2. 전송 직후 esp_deep_sleep_start()로 완전히 재부팅 (Gingerbread와 동일)  │
 * │ 3. Wi-Fi 재연결 최적화(채널/BSSID 기억)도 Gingerbread와 동일하게 적용 —   │
 * │    비교가 "재연결 속도 차이"가 아니라 "QoS 로직 차이"만 반영하도록 함     │
 * └─────────────────────────────────────────────────────────────────────────┘
 *
 * 의존 라이브러리 (platformio.ini [env:board2_standard] 참조):
 *   - 256dpi/MQTT             @ ^2.5.3 : 표준 MQTT 브로커 통신 (QoS 1 지원)
 *   - bblanchon/ArduinoJson   @ ^7.0   : JSON 페이로드 직렬화
 *
 * [라이브러리 선택 근거]
 *   기존 PubSubClient는 publish()가 QoS 0만 지원하여(PUBLISH 헤더의 QoS 비트가 항상 0,
 *   PUBACK 대기 코드 없음) "고정 QoS 1 베이스라인"이 성립하지 않았습니다.
 *   256dpi/MQTT의 publish(topic, payload, retained, qos=1)는 PUBACK 수신(또는 타임아웃)
 *   까지 블로킹하고 실패 시 false를 반환합니다.
 * ═══════════════════════════════════════════════════════════════════════════
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <MQTT.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_BME680.h>
#include <esp_sleep.h>          // esp_deep_sleep_start() — Gingerbread와 동일한 실제 Deep Sleep

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A] 네트워크 자격증명 및 MQTT 브로커 엔드포인트
 * ═══════════════════════════════════════════════════════════════════════════ */
static const char *WIFI_SSID        = "YOUR_WIFI_SSID";      // Wi-Fi SSID
static const char *WIFI_PASSWORD    = "YOUR_WIFI_PASSWORD";   // Wi-Fi 비밀번호
static const char *MQTT_BROKER_IP   = "10.144.246.14";      // MQTT 브로커 IP
static const uint16_t MQTT_BROKER_PORT = 1883;                // 타겟 포트

// Board 1 등 브로커 내 다른 보드와의 세션 충돌 방지용 고유 클라이언트 ID
static const char *CLIENT_ID = "ESP32-Standard-MQTT";

// 발행할 MQTT 토픽
static const char *TOPIC = "environmental/standard";

// 핸드셰이크 완료 후 실측 RTT/retry를 담아 보내는 메트릭 토픽 (QoS 0).
static const char *METRICS_TOPIC = "environmental/standard/metrics";

// Gingerbread와 동일한 재전송 조건 (비교 공정성)
static const int MAX_RETRIES    = 3;     // 최초 시도 + 최대 3회 재전송
static const int ACK_TIMEOUT_MS = 2000;  // PUBACK 대기 타임아웃 (Gingerbread wait_for_packet과 동일)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A-2] 고정 주기 + Deep Sleep 설정 — Gingerbread의 "정상(QoS 0)" 주기와 동일
 * ═══════════════════════════════════════════════════════════════════════════ */
static const uint32_t STANDARD_SLEEP_MS    = 60000;  // 고정 60초 Deep Sleep
static const uint32_t WIFI_FAST_TIMEOUT_MS = 4000;   // 저장한 채널/BSSID로 재연결할 때의 제한 시간
static const uint32_t WIFI_FULL_TIMEOUT_MS = 15000;  // 전체 스캔 재연결 제한 시간
static const uint32_t RADIO_FLUSH_MS       = 20;     // 마지막 패킷이 무선으로 나갈 여유 시간
static const uint32_t MQTT_RECONNECT_TIMEOUT_MS = 10000;  // 브로커 재연결 최대 대기 (넘으면 이번 사이클 포기하고 Sleep)
static const int      FIXED_QOS = 1;                 // Board 2는 항상 QoS 1 고정

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 B] BME680 센서
 * 온도/습도/가스를 측정은 하지만 QoS 판단에는 쓰지 않습니다 (Board 2는 항상 QoS 1).
 * ═══════════════════════════════════════════════════════════════════════════ */
#define BME_SDA_PIN 8
#define BME_SCL_PIN 9
static uint8_t bme680_i2c_address = 0x76;
static Adafruit_BME680 bme680;
static float sensor_temp = 0.0f;
static float sensor_hum  = 0.0f;
static float sensor_gas  = 0.0f;

static bool read_bme680() {
  if (!bme680.performReading()) {
    return false;
  }
  sensor_temp = bme680.temperature;
  sensor_hum  = bme680.humidity;
  sensor_gas  = bme680.gas_resistance / 1000.0f;
  return true;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 C] RTC 메모리 영속 상태 — Deep Sleep은 재부팅이라 RTC_DATA_ATTR로 유지해야 함
 * (Gingerbread의 같은 패턴 참조: firmware/src/main_gingerbread.cpp)
 * ═══════════════════════════════════════════════════════════════════════════ */
RTC_DATA_ATTR static uint32_t g_boot_count      = 0;
RTC_DATA_ATTR static uint32_t g_packet_count    = 0;  // 누적 발행 성공 패킷 수
RTC_DATA_ATTR static uint32_t g_total_bytes     = 0;  // 누적 전송 바이트 수
RTC_DATA_ATTR static uint32_t g_total_active_ms = 0;  // 누적 활성(awake) 시간 (ms)
RTC_DATA_ATTR static uint32_t g_total_sleep_ms  = 0;  // 누적 Deep Sleep 시간 (ms)
RTC_DATA_ATTR static bool     g_have_ap         = false;
RTC_DATA_ATTR static uint8_t  g_ap_bssid[6]     = {0};
RTC_DATA_ATTR static int32_t  g_ap_channel      = 0;

// 이번 부팅 시작 시각. active_ms는 여기서부터 재야 Wi-Fi/MQTT 재연결 시간도 포함됩니다
// (Gingerbread에서 겪었던 "재연결 시간 누락" 버그와 동일한 함정 — main_gingerbread.cpp 참조).
static unsigned long g_boot_start_ms = 0;

/* ─── 클라이언트 객체 초기화 ────────────────────────────────────────────── */
static WiFiClient  espClient;
static MQTTClient  client(512);  // 읽기/쓰기 버퍼 512바이트 (기본값 128은 센서 페이로드 + 토픽에 부족)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 D] Wi-Fi 연결 — RTC에 저장된 채널/BSSID로 빠른 재연결 (Gingerbread와 동일)
 * 두 노드의 재연결 속도를 동등하게 맞춰서, 비교가 QoS 로직 차이만 반영하게 합니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
static void wifi_remember_ap() {
  const uint8_t *b = WiFi.BSSID();
  if (b != nullptr) {
    memcpy(g_ap_bssid, b, sizeof(g_ap_bssid));
    g_ap_channel = WiFi.channel();
    g_have_ap    = true;
  }
}

static bool wifi_connect(uint32_t timeout_ms) {
  WiFi.mode(WIFI_STA);
  if (g_have_ap) {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD, g_ap_channel, g_ap_bssid, true);
  } else {
    WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  }
  const uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - t0) < timeout_ms) {
    delay(5);
  }
  return WiFi.status() == WL_CONNECTED;
}

static bool wifi_connect_with_fallback() {
  bool ok = wifi_connect(WIFI_FAST_TIMEOUT_MS);
  if (!ok && g_have_ap) {
    Serial.println("[Wi-Fi] ⚠ 저장된 채널/BSSID로 재연결 실패 — 전체 스캔으로 재시도");
    g_have_ap = false;
    ok = wifi_connect(WIFI_FULL_TIMEOUT_MS);
  }
  return ok;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 E] Deep Sleep 진입 — Gingerbread의 enter_deep_sleep()과 동일
 * ═══════════════════════════════════════════════════════════════════════════ */
static void enter_deep_sleep(uint32_t ms) {
  delay(RADIO_FLUSH_MS);
  client.disconnect();
  Serial.printf("[절전] Deep Sleep 진입 — %lu ms 후 재부팅\n", (unsigned long)ms);
  Serial.flush();
  esp_sleep_enable_timer_wakeup((uint64_t)ms * 1000ULL);
  esp_deep_sleep_start();  // 반환하지 않음 — 재부팅 후 setup()부터 다시 시작
}

/* ═══════════════════════════════════════════════════════════════════════════
 * MQTT 브로커 재연결 함수
 * ═══════════════════════════════════════════════════════════════════════════ */
// 1회 연결 시도. 성공하면 true.
static bool mqtt_connect_once() {
  Serial.printf("[MQTT] 브로커 연결 시도 (클라이언트 ID: %s)\n", CLIENT_ID);
  if (client.connect(CLIENT_ID)) {
    Serial.println("[MQTT] ✓ 브로커 페어링 성공!");
    return true;
  }
  Serial.printf("[MQTT] ⚠ 연결 실패 (lastError=%d, returnCode=%d)\n",
                (int)client.lastError(), (int)client.returnCode());
  return false;
}

// timeout_ms 안에 연결되면 true. 넘으면 false (호출한 쪽이 이번 사이클을 포기하고 Sleep).
static bool reconnect(uint32_t timeout_ms) {
  const uint32_t t0 = millis();
  while (!client.connected() && (millis() - t0) < timeout_ms) {
    if (!mqtt_connect_once()) {
      delay(1000);
    }
  }
  return client.connected();
}

/* ═══════════════════════════════════════════════════════════════════════════
 * 초기 설정 (Setup) — Deep Sleep에서 깨어날 때마다 처음부터 다시 실행됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  g_boot_start_ms = millis();
  Serial.begin(115200);
  while (!Serial) { delay(10); }

  g_boot_count++;
  const esp_sleep_wakeup_cause_t wake_reason = esp_sleep_get_wakeup_cause();
  Serial.println("\n╔══════════════════════════════════════════════════════════╗");
  Serial.println("║  Board 2: 표준 MQTT 베이스라인 — 고정 QoS 1 + Deep Sleep ║");
  Serial.println("╚══════════════════════════════════════════════════════════╝");
  Serial.printf("[부팅] #%lu | 원인: %s\n", (unsigned long)g_boot_count,
                wake_reason == ESP_SLEEP_WAKEUP_TIMER ? "Deep Sleep 타이머 깨어남" : "콜드 부팅 / 리셋");

  Wire.begin(BME_SDA_PIN, BME_SCL_PIN);
  if (!bme680.begin(bme680_i2c_address)) {
    bme680_i2c_address = 0x77;
    if (!bme680.begin(bme680_i2c_address)) {
      Serial.println("[BME680] 센서를 찾을 수 없습니다 (주소 0x76/0x77, 배선 확인 필요)");
      while (true) {
        delay(1000);
      }
    }
  }
  bme680.setTemperatureOversampling(BME680_OS_8X);
  bme680.setHumidityOversampling(BME680_OS_2X);
  bme680.setPressureOversampling(BME680_OS_4X);
  bme680.setIIRFilterSize(BME680_FILTER_SIZE_3);
  bme680.setGasHeater(320, 150);
  Serial.printf("[BME680] 실제 센서 초기화 완료 (SDA=%d, SCL=%d, 주소=0x%02X)\n",
                BME_SDA_PIN, BME_SCL_PIN, bme680_i2c_address);

  Serial.printf("[부팅] Wi-Fi 연결 시도 중... SSID: \"%s\"\n", WIFI_SSID);
  if (!wifi_connect_with_fallback()) {
    Serial.println("[부팅] ✗ Wi-Fi 연결 실패 — 이번 사이클을 건너뛰고 Deep Sleep 재시도");
    g_total_active_ms += (uint32_t)(millis() - g_boot_start_ms);
    enter_deep_sleep(STANDARD_SLEEP_MS);
  }
  wifi_remember_ap();
  Serial.printf("[부팅] ✓ Wi-Fi 연결 성공 — IP: %s | RSSI: %d dBm\n",
                WiFi.localIP().toString().c_str(), (int8_t)WiFi.RSSI());
  Serial.printf("[절전] 고정 주기 — QoS %d, %lus마다 1회 전송 후 Deep Sleep\n",
                FIXED_QOS, (unsigned long)(STANDARD_SLEEP_MS / 1000));

  client.begin(MQTT_BROKER_IP, MQTT_BROKER_PORT, espClient);
  client.setTimeout(ACK_TIMEOUT_MS); // QoS 1 PUBACK 대기 한도
  Serial.printf("[설정] MQTT 브로커: %s:%u | 토픽: %s\n",
                MQTT_BROKER_IP, MQTT_BROKER_PORT, TOPIC);

  if (!reconnect(MQTT_RECONNECT_TIMEOUT_MS)) {
    Serial.println("[부팅] ✗ MQTT 브로커 연결 실패 — 이번 사이클을 건너뛰고 Deep Sleep 재시도");
    g_total_active_ms += (uint32_t)(millis() - g_boot_start_ms);
    enter_deep_sleep(STANDARD_SLEEP_MS);
  }
  Serial.println("[부팅] ══ 초기화 완료, 표준 MQTT 메인 루프 시작 ══\n");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * 무한 루프 (Loop)
 *
 * 매 사이클 전송 후 enter_deep_sleep()에서 재부팅되므로 이 함수가 다시 호출되는 일은
 * 없습니다(다음 실행은 항상 setup()부터). QoS/주기는 항상 고정(1, 60초)입니다 —
 * Gingerbread와 달리 온도/습도에 따라 바뀌지 않는 것이 baseline의 정의입니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  if (!client.connected() && !reconnect(MQTT_RECONNECT_TIMEOUT_MS)) {
    Serial.println("[루프] ✗ MQTT 재연결 실패 — 이번 사이클을 건너뛰고 Deep Sleep 재시도");
    g_total_active_ms += (uint32_t)(millis() - g_boot_start_ms);
    g_total_sleep_ms  += STANDARD_SLEEP_MS;
    enter_deep_sleep(STANDARD_SLEEP_MS);
  }
  client.loop();

  Serial.println("\n────────────────────────────────────────────────────────────");
  Serial.printf("[루프] ▶ 표준 MQTT 발행 사이클 시작 (고정 QoS %d, %lus 주기)\n",
                FIXED_QOS, (unsigned long)(STANDARD_SLEEP_MS / 1000));

  if (!read_bme680()) {
    Serial.println("[BME680] 측정 실패 — 이번 사이클은 전송 없이 Deep Sleep");
    g_total_active_ms += (uint32_t)(millis() - g_boot_start_ms);
    g_total_sleep_ms  += STANDARD_SLEEP_MS;
    enter_deep_sleep(STANDARD_SLEEP_MS);
  }

  float sleep_mode_ratio = 0.0f;
  uint32_t total_elapsed = g_total_active_ms + g_total_sleep_ms;
  if (total_elapsed > 0) {
    sleep_mode_ratio = (float)g_total_sleep_ms / (float)total_elapsed;
  }

  // 이 페이로드는 발행 "이전"에 만들어지므로 이번 사이클의 RTT/retry를 담을 수 없습니다.
  // 실측 값은 아래 5단계 이후 별도 메트릭 메시지(METRICS_TOPIC)로 보냅니다.
  char payload[200];
  snprintf(payload, sizeof(payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,"
           "\"qos\":%d,\"sleep_r\":%.3f}",
           sensor_temp, sensor_hum, sensor_gas,
           FIXED_QOS, sleep_mode_ratio);

  Serial.printf("[전송] 토픽: %s\n[데이터] %s\n", TOPIC, payload);

  // QoS 1 발행 + 실제 PUBACK RTT 측정.
  // client.publish(..., qos=1)은 PUBACK을 받을 때까지(최대 ACK_TIMEOUT_MS) 블로킹하고,
  // 실패하면 false를 반환하며 연결을 스스로 닫습니다. 따라서 실패 시 재연결 후 재전송합니다.
  int   retry_count = 0;
  bool  publish_ok  = false;
  float rtt_ms      = 0.0f;

  unsigned long rtt_start_us = micros();
  while (retry_count <= MAX_RETRIES) {
    if (!client.connected() && !mqtt_connect_once()) {
      retry_count++;
      Serial.printf("[QoS 1] ⚠ 재연결 실패 (%d/%d)\n", retry_count, MAX_RETRIES);
      continue;
    }
    if (client.publish(TOPIC, payload, false, FIXED_QOS)) {
      rtt_ms     = (float)(micros() - rtt_start_us) / 1000.0f;
      publish_ok = true;
      break;
    }
    retry_count++;
    Serial.printf("[QoS 1] ⚠ PUBACK 미수신 — PUBLISH 재전송 (%d/%d, lastError=%d)\n",
                  retry_count, MAX_RETRIES, (int)client.lastError());
  }

  // 활성 시간 누적: setup()의 Wi-Fi/MQTT 재연결부터 포함해서 잽니다 (g_boot_start_ms 참고).
  unsigned long active_elapsed_ms = millis() - g_boot_start_ms;
  g_total_active_ms += (uint32_t)active_elapsed_ms;

  if (publish_ok) {
    g_packet_count++;
    g_total_bytes += (uint32_t)strlen(payload);

    Serial.printf("[결과] ✓ QoS 1 발행 성공 | PUBACK RTT: %.2f ms | 재전송: %d회 | Sleep비율: %.1f%%\n"
                  "       누적 패킷: %u | 누적 바이트: %u\n",
                  rtt_ms, retry_count, sleep_mode_ratio * 100.0f,
                  g_packet_count, g_total_bytes);

    // 실측 메트릭 메시지 (QoS 0) — Gingerbread의 topic_id=2 텔레메트리 패킷과 같은 역할.
    char metrics[200];
    snprintf(metrics, sizeof(metrics),
             "{\"qos\":%d,\"rtt\":%.2f,\"retry\":%d,\"sleep_r\":%.4f,"
             "\"act\":%lu,\"slp\":%lu,\"tp\":\"tcp\",\"pkt\":%u,\"bytes\":%u}",
             FIXED_QOS, rtt_ms, retry_count, sleep_mode_ratio,
             (unsigned long)active_elapsed_ms,
             (unsigned long)STANDARD_SLEEP_MS,
             g_packet_count, g_total_bytes);
    client.publish(METRICS_TOPIC, metrics, false, 0);
    Serial.printf("[메트릭] %s\n", metrics);
  } else {
    Serial.printf("[결과] ✗ QoS 1 발행 최종 실패 — 재전송 한도(%d회) 초과\n", MAX_RETRIES);
  }

  g_total_sleep_ms += STANDARD_SLEEP_MS;
  enter_deep_sleep(STANDARD_SLEEP_MS);  // 반환하지 않음 — 다음 실행은 setup()부터
}
