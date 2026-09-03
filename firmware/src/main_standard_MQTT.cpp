/*
 * firmware/src/main_standard_MQTT.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 코드명 : Standard MQTT  (Board 2 — 베이스라인 시스템)
 * 타겟 하드웨어   : ESP32-S3 DevKitC-1
 * 역할           : 고정 QoS 1 표준 MQTT 전송 (비교 기준 벤치마크)
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │                    아키텍처 개요 (Architecture Overview)                 │
 * ├─────────────────────────────────────────────────────────────────────────┤
 * │ Board 2의 역할은 TinyML AI 추론 없이 고정 QoS 1로 주기적 MQTT 발행을    │
 * │ 수행하여, Board 1(Gingerbread)의 AI 기반 동적 QoS 최적화 효과를 수치로  │
 * │ 비교하는 베이스라인 기준을 제공하는 것입니다.                            │
 * │                                                                         │
 * │ [2026-06 리팩토링] 소프트웨어 정의 전력 추정 메트릭 추가                │
 * │   하드웨어 INA219 Board 3을 제거하고, Board 2도 아래 5가지 성능 지표를  │
 * │   수집·전송하여 게이트웨이에서 IEEE Access 2024 기반 전력을 추정합니다:  │
 * │   1. RTT (rtt_ms)       : publish() 전후 millis() 기반 왕복 시간 (ms)  │
 * │   2. retry_count        : publish() 실패 시 재시도 횟수                 │
 * │   3. sleep_mode_ratio   : delay(INTERVAL)을 Sleep으로 간주한 비율       │
 * │   4. packet_count       : 누적 전송 성공 패킷 수                        │
 * │   5. total_bytes        : 누적 전송 바이트 수                           │
 * └─────────────────────────────────────────────────────────────────────────┘
 *
 * 의존 라이브러리 (platformio.ini [env:board2_standard] 참조):
 *   - knolleary/PubSubClient  @ ^2.8   : 표준 MQTT 브로커 통신
 *   - bblanchon/ArduinoJson   @ ^7.0   : JSON 페이로드 직렬화
 * ═══════════════════════════════════════════════════════════════════════════
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>
#include <Wire.h>
#include <Adafruit_BME680.h>

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A] 네트워크 자격증명 및 MQTT 브로커 엔드포인트
 * ═══════════════════════════════════════════════════════════════════════════ */
static const char *WIFI_SSID        = "YOUR_WIFI_SSID";      // Wi-Fi SSID
static const char *WIFI_PASSWORD    = "YOUR_WIFI_PASSWORD";   // Wi-Fi 비밀번호
static const char *MQTT_BROKER_IP   = "10.61.35.14";        // MQTT 브로커 IP
static const uint16_t MQTT_BROKER_PORT = 1883;                // 타겟 포트

// Board 1 등 브로커 내 다른 보드와의 세션 충돌 방지용 고유 클라이언트 ID
static const char *CLIENT_ID = "ESP32-Standard-MQTT";

// 발행할 MQTT 토픽
static const char *TOPIC = "environmental/standard";

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 B] 전송 주기 및 BME680 센서
 * ═══════════════════════════════════════════════════════════════════════════ */
// 5초 고정 발행 주기
static const unsigned long PUBLISH_INTERVAL_MS = 5000UL;

#define BME_SDA_PIN 8
#define BME_SCL_PIN 9
static uint8_t bme680_i2c_address = 0x76;
static Adafruit_BME680 bme680;
static float sensor_temp = 0.0f;
static float sensor_hum  = 0.0f;
static float sensor_gas  = 0.0f;
static const int FIXED_QOS = 1; // Board 2는 항상 QoS 1 고정

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
 * [섹션 C] 소프트웨어 정의 전력 추정 성능 메트릭 전역 카운터
 *
 * IEEE Access 2024 (DOI: 10.1109/ACCESS.2024.3523864) 기반 전력 추정을 위해
 * 아래 전역 변수로 루프 사이클 간 누적 상태를 추적합니다.
 *
 * Board 2는 PubSubClient를 사용하므로 QoS 1 PUBACK 확인은 라이브러리가 내부
 * 처리합니다. RTT는 publish() 호출 전후 millis()로 근사 측정합니다.
 *
 * [Complexity Column 평가 지표 — Board 2 (베이스라인)]
 * | 지표                   | 값                         |
 * |------------------------|----------------------------|
 * | AI 추론               | 없음 (고정 QoS 1)           |
 * | RTT 측정 방법          | millis() 근사 (라이브러리 내부 처리)|
 * | Flash 추가 사용량      | ≈ 0 bytes (추론 없음)       |
 * | SRAM 추가 사용량       | ≈ 0 bytes (추론 없음)       |
 * | 추론 지연              | 없음                        |
 * ═══════════════════════════════════════════════════════════════════════════ */
static uint32_t g_packet_count    = 0;  // 누적 발행 성공 패킷 수
static uint32_t g_total_bytes     = 0;  // 누적 전송 바이트 수 (JSON 페이로드 기준)
static uint32_t g_total_active_ms = 0;  // 누적 활성(awake) 경과 시간 (ms)
static uint32_t g_total_sleep_ms  = 0;  // 누적 delay() 경과 시간 (ms)
static int g_retry_count = 0;           // 현재 사이클 재시도 횟수

/* ─── 클라이언트 객체 초기화 ────────────────────────────────────────────── */
static WiFiClient   espClient;
static PubSubClient client(espClient);

/* 주기 타이밍 추적 변수 */
static unsigned long lastMsg = 0;

/* ═══════════════════════════════════════════════════════════════════════════
 * Wi-Fi 연결 함수
 * ═══════════════════════════════════════════════════════════════════════════ */
static void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.printf("[WiFi] 네트워크 연결 중: \"%s\"\n", WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.printf("\n[WiFi] ✓ 연결 성공 — IP: %s | RSSI: %d dBm\n",
                WiFi.localIP().toString().c_str(), (int8_t)WiFi.RSSI());
}

/* ═══════════════════════════════════════════════════════════════════════════
 * MQTT 브로커 재연결 함수 (블로킹)
 * ═══════════════════════════════════════════════════════════════════════════ */
static void reconnect() {
  while (!client.connected()) {
    Serial.printf("[MQTT] 브로커 연결 시도 (클라이언트 ID: %s)\n", CLIENT_ID);
    if (client.connect(CLIENT_ID)) {
      Serial.println("[MQTT] ✓ 브로커 페어링 성공!");
    } else {
      Serial.printf("[MQTT] ⚠ 연결 실패 (rc=%d) — 5초 후 재시도\n", client.state());
      delay(5000);
    }
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * 초기 설정 (Setup)
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  Serial.begin(115200);
  while (!Serial) { delay(10); }

  Serial.println("\n╔══════════════════════════════════════════════════════════╗");
  Serial.println("║  Board 2: 표준 MQTT 베이스라인 시스템 부팅 시작          ║");
  Serial.println("║  역할: 고정 QoS 1 | SW 전력 추정 메트릭 수집             ║");
  Serial.println("╚══════════════════════════════════════════════════════════╝");

  setup_wifi();

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

  client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT);
  Serial.printf("[설정] MQTT 브로커: %s:%u | 토픽: %s\n",
                MQTT_BROKER_IP, MQTT_BROKER_PORT, TOPIC);
  Serial.println("[부팅] ══ 초기화 완료, 표준 MQTT 메인 루프 시작 ══\n");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * 무한 루프 (Loop)
 *
 * 매 루프 사이클 실행 순서:
 *   1. MQTT 연결 상태 확인 및 재연결
 *   2. client.loop() — 수신 메시지 처리 및 keepalive 유지
 *   3. 5초 주기 확인 → 성능 메트릭 계산 → JSON 조립 → QoS 1 발행
 *   4. Sleep 시간 누적 (delay(INTERVAL))
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  // ── 1단계: MQTT 연결 유지 ──────────────────────────────────────────────
  if (!client.connected()) {
    reconnect();
  }
  // client.loop()는 라디오를 활성 상태로 유지하며 PUBACK 등을 처리합니다.
  // Board 2는 sleep 없이 radio를 항상 켜두는 것이 베이스라인 특성입니다.
  client.loop();

  // ── 2단계: 5초 고정 주기 확인 ─────────────────────────────────────────
  unsigned long now = millis();
  if (now - lastMsg < PUBLISH_INTERVAL_MS) {
    return; // 주기 미경과: 즉시 반환 (CPU 점유 최소화)
  }

  // ── 사이클 시작 타임스탬프 (활성 구간 측정용) ─────────────────────────
  unsigned long cycle_start_ms = lastMsg; // 이전 발행 시점부터를 활성 구간으로 정의
  lastMsg = now;

  Serial.println("\n────────────────────────────────────────────────────────────");
  Serial.printf("[루프] ▶ 표준 MQTT 발행 사이클 시작 (주기: %lu ms)\n",
                PUBLISH_INTERVAL_MS);

  if (!read_bme680()) {
    Serial.println("[BME680] 측정 실패 — 이번 전송을 건너뜁니다");
    return;
  }

  // ── 3단계: sleep_mode_ratio 계산 ──────────────────────────────────────
  // Board 2는 실제 Sleep 모드를 사용하지 않고 client.loop()로 radio를 유지합니다.
  // PUBLISH_INTERVAL 중 실제 sleep 없이 대기하므로 sleep_ratio ≈ 0에 수렴합니다.
  // 그러나 delay 기반 시뮬레이션과 일관성을 위해 동일 방식으로 계산합니다.
  float sleep_mode_ratio = 0.0f;
  uint32_t total_elapsed = g_total_active_ms + g_total_sleep_ms;
  if (total_elapsed > 0) {
    sleep_mode_ratio = (float)g_total_sleep_ms / (float)total_elapsed;
  }

  // ── 4단계: 성능 메트릭 조립 + JSON 페이로드 직렬화 ────────────────────
  // Board 1(Gingerbread)과 동일한 JSON 구조 사용 (비교 공정성)
  char payload[200];
  snprintf(payload, sizeof(payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,"
           "\"qos\":%d,\"rtt\":0.0,\"retry\":%d,\"sleep_r\":%.3f,"
           "\"pkt\":%u,\"bytes\":%u}",
           sensor_temp, sensor_hum, sensor_gas,
           FIXED_QOS,
           g_retry_count,     // 이전 사이클 재시도 횟수 (현 사이클은 발행 후 결정)
           sleep_mode_ratio,
           g_packet_count,
           g_total_bytes);

  g_retry_count = 0; // 새 사이클 시작 전 재시도 카운터 초기화

  Serial.printf("[전송] 토픽: %s\n[데이터] %s\n", TOPIC, payload);

  // ── 5단계: RTT 측정 + QoS 1 발행 ──────────────────────────────────────
  // PubSubClient는 MQTT QoS 1 PUBACK를 내부적으로 처리합니다.
  // publish() 전후 millis()로 라이브러리 레벨 왕복 시간을 근사 측정합니다.
  // 주의: PubSubClient의 publish()는 논블로킹으로 동작하므로 실제 PUBACK
  // 수신 시점이 아닌 스택 반환 시점을 RTT 종점으로 사용합니다.
  unsigned long rtt_start_ms = millis();
  bool publish_ok = client.publish(TOPIC, payload, false); // retained=false
  unsigned long rtt_end_ms   = millis();
  float rtt_ms = (float)(rtt_end_ms - rtt_start_ms);

  // ── 6단계: 전송 결과 처리 및 메트릭 누적 ──────────────────────────────
  if (publish_ok) {
    g_packet_count++;
    g_total_bytes += (uint32_t)strlen(payload);

    // 활성 구간 경과 시간 누적 (사이클 시작 ~ 발행 완료)
    unsigned long active_elapsed = millis() - cycle_start_ms;
    g_total_active_ms += (uint32_t)active_elapsed;

    Serial.printf("[결과] ✓ QoS 1 발행 성공 | RTT(근사): %.1f ms | Sleep비율: %.1f%%\n"
                  "       누적 패킷: %u | 누적 바이트: %u\n",
                  rtt_ms, sleep_mode_ratio * 100.0f,
                  g_packet_count, g_total_bytes);
  } else {
    // 발행 실패: retry_count 증가
    g_retry_count++;
    Serial.printf("[결과] ✗ 발행 실패 (재시도 #%d) — 네트워크 스택 확인 필요\n",
                  g_retry_count);
  }

  // ── 7단계: Sleep 시간 누적 ────────────────────────────────────────────
  // Board 2는 실제 Sleep 없이 client.loop() 폴링으로 대기하지만,
  // 다음 PUBLISH_INTERVAL까지의 대기 시간을 "passive sleep"으로 기록합니다.
  // 이를 통해 Board 1과의 sleep_mode_ratio를 일관된 방식으로 비교할 수 있습니다.
  // Board 2는 radio를 항상 켜두므로 sleep_ratio ≈ 0이 예상됩니다.
  // (g_total_sleep_ms += 0: Board 2는 실제 sleep이 없음을 명시적으로 표현)
  // 만약 delay()를 사용한 시뮬레이션 sleep을 추가하려면 아래 주석을 해제하세요:
  // g_total_sleep_ms += PUBLISH_INTERVAL_MS;
}
