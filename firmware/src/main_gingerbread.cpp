/*
 * firmware/src/main_gingerbread.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 코드명 : Gingerbread  (Board 1 — 제안 시스템)
 * 타겟 하드웨어   : ESP32-S3 DevKitC-1
 * 역할           : 온도/습도 기반 동적 QoS + 실제 Deep Sleep 절전 센서 노드
 *
 * [2026-09 재설계] INSTRUCTIONS.md 명세 반영
 *   이전 버전은 학습되지 않은 임시 가중치(다른 AI가 만든 숫자)로 동작하는 MLP와,
 *   보정되지 않은 가정값(net_congestion.h)으로 QoS를 올리는 로직을 썼습니다.
 *   둘 다 "그럴듯해 보이지만 실제로는 검증되지 않은" 로직이었습니다.
 *   지금은 QoS 판단 기준을 온도·습도 두 값에 대한 명시적 임계값으로 단순화했고,
 *   Sleep도 delay()가 아니라 실제 esp_deep_sleep_start()를 씁니다(Light Sleep보다도
 *   전류가 훨씬 낮고, 매 사이클 완전히 재부팅되므로 "진짜로 잔다"는 것이 명확합니다).
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │                    아키텍처 개요 (Architecture Overview)                 │
 * ├─────────────────────────────────────────────────────────────────────────┤
 * │ 1. QoS 판단 = 온도와 습도만 사용 (다른 어떤 값도 QoS에 영향을 주지 않음)   │
 * │      QoS 0 (정상)  : 온도 ≤ TEMP_WARN_C   AND 습도 ≤ HUM_WARN_PCT       │
 * │      QoS 1 (경고)  : (TEMP_WARN < 온도 ≤ TEMP_DANGER)                   │
 * │                       OR (HUM_WARN < 습도 ≤ HUM_DANGER)                 │
 * │      QoS 2 (위험)  : 온도 > TEMP_DANGER_C  OR  습도 > HUM_DANGER_PCT     │
 * │    (온도/습도 중 더 심각한 쪽의 등급을 그대로 씀 — OR 판정)              │
 * │                                                                         │
 * │ 1-2. TinyML 임계값 보정 (qos_calibration.h, 오프라인 학습)               │
 * │    위 TEMP_WARN_C 등은 사람이 정한 SPEC이며 최종 결정권을 가집니다.       │
 * │    TinyML은 그 값을 대체하지 않고, 학습한 만큼만(최대 ±TEMP_ADJUST_LIMIT_C,│
 * │    ±HUM_ADJUST_LIMIT_PCT — 펌웨어에 하드코딩) 살짝 조정합니다. 이 범위는  │
 * │    학습 결과와 무관하게 항상 clamp되므로 SPEC을 크게 벗어날 수 없습니다.  │
 * │                                                                         │
 * │ 2. QoS별 전송 계층 + 사이클 주기 (평소엔 느긋하게, 위험할수록 자주 확인) │
 * │      QoS 0 : UDP, 60초마다 1회, 전송 직후 60초 Deep Sleep               │
 * │      QoS 1 : UDP, 30초마다 1회, 전송 직후 30초 Deep Sleep (더 자주 확인) │
 * │      QoS 2 : TCP(4단계 핸드셰이크), 전송 직후 3초 Deep Sleep (가장 자주) │
 * │                                                                         │
 * │ 3. 실제 Deep Sleep (esp_deep_sleep_start)                              │
 * │    Deep Sleep은 CPU/RAM을 끄고 RTC 메모리만 유지한 채 완전히 재부팅됩니다.│
 * │    그래서 사이클 누적 통계(패킷 수, 바이트, msg_id, Wi-Fi 채널/BSSID)는  │
 * │    일반 전역변수가 아니라 RTC_DATA_ATTR 변수에 저장해 재부팅에도 유지합니다.│
 * │    (전원이 끊기거나 리셋 버튼을 누르면 이 값들도 초기화됩니다 — 정상 동작)│
 * │                                                                         │
 * │ 4. 커스텀 MQTT-SN 프로토콜 (WiFiUDP/TCP 기반 실제 텔레메트리)            │
 * │    QoS 0 : 단발 무확인 전송                                             │
 * │    QoS 1 : PUBLISH → PUBACK 2단계 핸드셰이크 (재전송 최대 3회, UDP)     │
 * │    QoS 2 : PUBLISH → PUBREC → PUBREL → PUBCOMP 4단계 핸드셰이크 (TCP)  │
 * └─────────────────────────────────────────────────────────────────────────┘
 *
 * 의존 라이브러리 (platformio.ini 참조):
 *   - knolleary/PubSubClient  @ ^2.8   : MQTT 브로커 통신 (설정 구독 전용)
 *   - bblanchon/ArduinoJson   @ ^7.0   : JSON 파싱
 *   - Adafruit BME680                  : 온도/습도/가스/기압 센서
 *   - WiFiUdp / WiFiClient             : 커스텀 MQTT-SN 텔레메트리 전송
 *   - esp_sleep.h                      : esp_deep_sleep_start() — 사이클 사이 실제 Deep Sleep
 *
 * 소프트웨어 정의 전력 추정 메트릭 (IEEE Access 2024, DOI: 10.1109/ACCESS.2024.3523864)
 *   1. RTT (rtt_ms)     : PUBLISH 전송~ACK 수신 왕복 시간 (ms)
 *   2. retry_count      : 패킷 재전송 횟수
 *   3. sleep_mode_ratio : 누적 경과 시간 대비 Sleep 시간 비율 (0.0~1.0)
 *   4. packet_count     : 누적 전송 패킷 수
 *   5. total_bytes      : 누적 전송 바이트 수
 * ═══════════════════════════════════════════════════════════════════════════
 */

/* ─── 라이브러리 헤더 인클루드 ──────────────────────────────────────────── */
#include "protocol.h"           // 커스텀 MQTT-SN 프로토콜 구조체 정의 (include/)
#include "qos_calibration.h"    // TinyML이 오프라인 학습한 임계값 보정폭(delta) — MLP 미학습 시 폴백 규칙에 적용
#include "mlp_inference.h"      // TinyML MLP 순전파 — 학습되면 QoS를 직접 결정 (include/, ml_model/train.py가 생성)
#include <Arduino.h>            // Arduino 프레임워크 기본 함수
#include <ArduinoJson.h>        // MQTT "gingerbread/config" 페이로드 JSON 파싱
#include <PubSubClient.h>       // 표준 MQTT 브로커 통신 (설정 구독 전용)
#include <WiFi.h>               // Wi-Fi 연결 및 RSSI 측정
#include <WiFiClient.h>         // PubSubClient TCP 연결에 필요한 네트워크 클라이언트
#include <WiFiUdp.h>            // 커스텀 MQTT-SN 프로토콜 UDP 전송 소켓
#include <esp_sleep.h>          // esp_deep_sleep_start() — 사이클 사이 실제 Deep Sleep
#include <freertos/FreeRTOS.h>  // FreeRTOS 커널 기본 헤더
#include <freertos/semphr.h>    // FreeRTOS Mutex (SemaphoreHandle_t) 지원
#include <Wire.h>               // BME680 I2C 통신
#include <Adafruit_BME680.h>    // BME680 환경 센서 드라이버

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A] 네트워크 자격증명 및 서버 엔드포인트 설정
 * ═══════════════════════════════════════════════════════════════════════════ */
static const char    *WIFI_SSID        = "YOUR_WIFI_SSID";     // Wi-Fi 네트워크 SSID
static const char    *WIFI_PASSWORD    = "YOUR_WIFI_PASSWORD"; // Wi-Fi 비밀번호

// 커스텀 MQTT-SN (UDP) 게이트웨이 주소 및 포트 (라즈베리파이 5)
static const char    *UDP_SERVER_IP    = "10.144.246.14";
static const uint16_t UDP_SERVER_PORT  = 5000;

// ── QoS 레벨별 전송 계층 ─────────────────────────────────────────────────
//   QoS 0·1 → UDP (오버헤드가 적은 저전력 모드)
//   QoS 2   → TCP (수신 보장이 필수인 신뢰성 모드, 게이트웨이 backend/config.py
//             GINGERBREAD_TCP_PORT와 일치해야 함)
static const uint16_t TCP_SERVER_PORT  = 5001;
static const uint8_t  TCP_MIN_QOS      = 2;

// 표준 MQTT 브로커 (Mosquitto) — "gingerbread/config" 설정 구독 전용
static const char    *MQTT_BROKER_IP   = "10.144.246.14";
static const uint16_t MQTT_BROKER_PORT = 1883;
static const char    *MQTT_CONFIG_TOPIC = "gingerbread/config";

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A-2] QoS별 사이클 주기 + Deep Sleep 설정
 *
 * 세 QoS 레벨 모두 전송 직후 esp_deep_sleep_start()로 완전히 재부팅됩니다(RAM 초기화,
 * RTC 메모리만 유지). 평소(QoS 0)엔 느긋하게 확인하다가, 이상 징후가 감지되면(QoS 1)
 * 더 자주, 위험 수준(QoS 2)이면 훨씬 더 자주 깨어나 상황을 촘촘히 재확인합니다.
 * 위험·가스 누출 같은 상황은 보통 서서히 악화되므로, 한 번 이상 징후가 잡히면 그 뒤로는
 * 짧은 주기로 계속 지켜봐서 악화를 놓치지 않는 것이 목적입니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
// 너무 짧게 잡으면 Deep Sleep마다 발생하는 재부팅+Wi-Fi/MQTT 재연결 오버헤드(보통 0.5~1초)가
// 사이클에서 차지하는 비중이 커져 절전 효과가 줄어듭니다. QoS 2는 절전보다 신뢰성이 우선이라
// 오버헤드 비중이 커도 감수합니다.
static const uint32_t QOS0_SLEEP_MS = 60000;  // 정상: 60초 Deep Sleep
static const uint32_t QOS1_SLEEP_MS = 30000;  // 경고: 30초 Deep Sleep (더 자주 확인)
static const uint32_t QOS2_SLEEP_MS = 3000;   // 위험: 3초 Deep Sleep (가장 자주 확인)
static const uint32_t WIFI_FAST_TIMEOUT_MS = 4000;  // 저장한 채널/BSSID로 재연결할 때의 제한 시간
static const uint32_t WIFI_FULL_TIMEOUT_MS = 15000; // 전체 스캔 재연결 제한 시간
static const uint32_t RADIO_FLUSH_MS       = 20;    // 마지막 UDP 패킷이 무선으로 나갈 여유 시간
static const uint32_t CONFIG_SYNC_WAIT_MS  = 500;   // 구독 후 retained 설정을 기다리는 최대 시간
static const int8_t   RSSI_UNSTABLE_DBM    = -80;   // 진단용 고정 임계값 (QoS 결정에는 영향 없음)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A-3] TinyML 임계값 보정 한계 — "사람이 정한 기준이 항상 이긴다"
 *
 * SystemConfig의 기본값(30/50/70/85, 아래 g_config 참조)이 사람이 명시한 SPEC입니다.
 * qos_calibration.h의 QOS_CAL_* 값(오프라인 학습, ml_model/train_threshold_calibration.py)은
 * 이 SPEC에서 최대 아래 폭만큼만 조정할 수 있습니다. 학습이 잘못되거나 헤더 값이 손상돼도
 * 이 clamp는 컴파일된 펌웨어 쪽 상수라 절대 뚫리지 않습니다 (apply_calibration() 참조).
 * ═══════════════════════════════════════════════════════════════════════════ */
static const float TEMP_ADJUST_LIMIT_C  = 3.0f;  // 온도 임계값 보정 한계 (± °C)
static const float HUM_ADJUST_LIMIT_PCT = 5.0f;  // 습도 임계값 보정 한계 (± %)

/* ─── Board 1 다중 노드 식별자 ──────────────────────────────────────────── */
static const char *BOARD1_CLIENT_ID = "ESP32-Gingerbread";

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 B] 하드웨어 핀 정의 및 ADC 파라미터
 * ═══════════════════════════════════════════════════════════════════════════ */
static const int BATTERY_ADC_PIN = 34;

static const float ADC_REF_VOLTAGE  = 3.3f;
static const float ADC_MAX_VALUE    = 4095.0f;
static const float BATTERY_VOLT_MAX = 4.2f;
static const float BATTERY_VOLT_MIN = 3.0f;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 C] SystemConfig 구조체 — MQTT 동기화 설정 저장소
 *
 * FreeRTOS Mutex(g_config_mutex)로 스레드 안전하게 보호됩니다.
 * 갱신 경로: 라즈베리파이 → MQTT "gingerbread/config" → on_mqtt_message() → g_config
 * 사용 경로: loop() → decide_qos_plan() (온도/습도 임계값), preprocess_sensor_inputs()
 * ═══════════════════════════════════════════════════════════════════════════ */
struct SystemConfig {
  // ── ENVIRONMENT 섹션 (QoS 판단 기준 — 온도/습도만) ─────────────────────
  float temp_warn_c;     // 온도 경고 임계값 (°C, 기본 30.0) — 이하면 정상
  float temp_danger_c;   // 온도 위험 임계값 (°C, 기본 50.0) — 초과하면 위험
  float hum_warn_pct;    // 습도 경고 임계값 (%, 기본 70.0) — 이하면 정상
  float hum_danger_pct;  // 습도 위험 임계값 (%, 기본 85.0) — 초과하면 위험

  // ── POWER_MANAGEMENT 섹션 ────────────────────────────────────────────
  char    power_mode[16];         // "EXTERNAL_5V" | "BATTERY"
  uint8_t current_battery_level;  // 대시보드 가상 배터리 레벨 (0~100 %)
};

// ★ SPEC — 사람이 명시적으로 정한 기준값입니다. 대시보드(MQTT)로 재정의할 수 있지만, 재정의하지
// 않으면 이 값이 그대로 쓰입니다. TinyML 보정(qos_calibration.h)은 이 값(또는 대시보드가 재정의한
// 값) 위에 작은 delta만 더하며, 그 폭은 TEMP_ADJUST_LIMIT_C/HUM_ADJUST_LIMIT_PCT로 하드클램프됩니다.
static SystemConfig g_config = {
  30.0f,          // temp_warn_c
  50.0f,          // temp_danger_c
  70.0f,          // hum_warn_pct
  85.0f,          // hum_danger_pct
  "EXTERNAL_5V",  // power_mode
  100,            // current_battery_level
};

static SemaphoreHandle_t g_config_mutex = nullptr;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 D] 센서 입력값 구조체
 * ═══════════════════════════════════════════════════════════════════════════ */
struct SensorInputs {
  float  temp;        // BME680 온도 (°C) — QoS 판단에 사용
  float  hum;         // BME680 상대 습도 (%) — QoS 판단에 사용
  float  gas_kohm;    // BME680 가스 저항값 (kΩ) — 정보성 로깅 전용 (QoS에 영향 없음)
  float  pres_hpa;    // BME680 기압 (hPa) — 정보성 로깅 전용 (QoS에 영향 없음)
  float  battery_pct; // 배터리 레벨 (0.0~100.0 %)
  int8_t rssi;        // Wi-Fi RSSI (dBm) — 정보성 로깅 전용 (QoS에 영향 없음)
};

/* ─── 통신 객체 인스턴스 ─────────────────────────────────────────────────── */
static WiFiUDP      udp;
static WiFiClient   wifi_client;
static PubSubClient mqtt_client(wifi_client);
#define BME_SDA_PIN 8
#define BME_SCL_PIN 9
static uint8_t bme680_i2c_address = 0x76;
static Adafruit_BME680 bme680;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 E] RTC 메모리 영속 상태
 *
 * Deep Sleep은 일반 RAM/전역변수를 초기화하고 완전히 재부팅합니다. 사이클을 넘어
 * 유지해야 하는 값(누적 통계, msg_id, Wi-Fi 재연결 힌트)은 RTC_DATA_ATTR로
 * 선언해 RTC 메모리에 저장합니다 — 전원이 끊기거나 리셋 버튼을 누르기 전까지는
 * Deep Sleep을 몇 번 거쳐도 그대로 유지됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
RTC_DATA_ATTR static uint32_t g_boot_count      = 0;  // 콜드 부팅 이후 총 부팅(깨어남) 횟수
RTC_DATA_ATTR static uint32_t g_packet_count    = 0;  // 누적 PUBLISH 전송 성공 패킷 수
RTC_DATA_ATTR static uint32_t g_total_bytes     = 0;  // 누적 전송 바이트 수
RTC_DATA_ATTR static uint32_t g_total_active_ms = 0;  // 누적 활성(awake) 시간 (ms)
RTC_DATA_ATTR static uint32_t g_total_sleep_ms  = 0;  // 누적 Deep Sleep 시간 (ms)
RTC_DATA_ATTR static uint16_t g_msg_id_seed     = 1;  // 단조 증가 메시지 ID (재부팅에도 이어짐)
RTC_DATA_ATTR static bool     g_have_ap         = false; // 저장된 AP 채널/BSSID가 있는가
RTC_DATA_ATTR static uint8_t  g_ap_bssid[6]     = {0};
RTC_DATA_ATTR static int32_t  g_ap_channel      = 0;

static uint32_t g_config_rx_count = 0;  // 이번 부팅에서 설정 메시지 수신 횟수 (일반 RAM — 부팅마다 재설정되어도 무방)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 F] 하드웨어 센서 읽기 함수
 * ═══════════════════════════════════════════════════════════════════════════ */
static float read_temperature()          { return bme680.temperature; }
static float read_humidity()             { return bme680.humidity; }
static float read_gas_resistance_kohm()  { return bme680.gas_resistance / 1000.0f; }
static float read_pressure_hpa()         { return bme680.pressure / 100.0f; }
static bool  read_bme680()               { return bme680.performReading(); }
static int8_t get_wifi_rssi()            { return (int8_t)WiFi.RSSI(); }

static size_t publish_packet_size(const PublishPacket &packet) {
  return sizeof(Header) + sizeof(packet.msg_id) + sizeof(packet.qos)
       + sizeof(packet.topic_id) + strlen(packet.payload) + 1;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 G] 배터리 ADC 측정 함수 (BATTERY 모드 전용)
 * ═══════════════════════════════════════════════════════════════════════════ */
static float read_battery_adc_pct() {
  int raw_adc = analogRead(BATTERY_ADC_PIN);
  float measured_voltage = ((float)raw_adc / ADC_MAX_VALUE) * ADC_REF_VOLTAGE;
  float pct = (measured_voltage - BATTERY_VOLT_MIN)
              / (BATTERY_VOLT_MAX - BATTERY_VOLT_MIN) * 100.0f;
  if (pct < 0.0f)   pct = 0.0f;
  if (pct > 100.0f) pct = 100.0f;
  return pct;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 H] ★ QoS 판단 — TinyML(MLP)이 온도·습도로 직접 결정, 미학습 시 규칙으로 폴백 ★
 *
 * 목표 상태(MLP_WEIGHTS_TRAINED=1, ml_model/train.py --features temp,hum 로 학습 완료):
 *   TinyML MLP가 온도·습도를 입력받아 위험 점수(0~1)를 계산하고, 그 점수를 QoS 0/1/2로
 *   매핑합니다 — QoS를 "직접 고르는" 주체가 사람이 짠 규칙이 아니라 신경망입니다.
 *   가스/기압 입력 열의 가중치는 0으로 고정되어 있어(mlp_weights.h), 학습되어도 온도·습도
 *   외의 값은 결정에 관여하지 않습니다.
 *
 * 현재 상태(MLP_WEIGHTS_TRAINED=0, 아직 실제 데이터로 학습 전):
 *   신경망을 아직 못 믿으므로, 사람이 명시한 SPEC 규칙(+TinyML이 학습한 좁은 범위의 임계값
 *   보정, qos_calibration.h)으로 안전하게 폴백합니다. 두 경로 모두 같은 QoSPlan을 반환하므로
 *   loop() 쪽 코드는 어느 쪽이 활성인지 몰라도 됩니다 — 부팅 로그와 QoS 판정 로그의 "(NN)"/
 *   "(rule)" 표시로 구분할 수 있습니다.
 *
 *   등급 경계(규칙 폴백 한정): value ≤ warn → NORMAL, warn < value ≤ danger → WARNING, value > danger → DANGER
 * ═══════════════════════════════════════════════════════════════════════════ */
enum class Severity : uint8_t { NORMAL = 0, WARNING = 1, DANGER = 2 };

static Severity classify(float value, float warn, float danger) {
  if (value > danger) return Severity::DANGER;
  if (value > warn)   return Severity::WARNING;
  return Severity::NORMAL;
}

static inline float clampf(float v, float lo, float hi) {
  return (v < lo) ? lo : (v > hi) ? hi : v;
}

// SPEC(또는 대시보드가 재정의한 현재 g_config 값) 위에 TinyML이 오프라인으로 학습한 보정폭
// (qos_calibration.h)을 더합니다. MLP가 아직 학습 전이라 규칙으로 폴백할 때만 쓰입니다.
// 보정폭 자체가 TEMP_ADJUST_LIMIT_C/HUM_ADJUST_LIMIT_PCT로 하드클램프되어 있어 학습이
// 잘못돼도 기준을 크게 벗어날 수 없고, 보정 후에도 danger가 warn보다 낮아지지 않도록
// 안전장치를 둡니다(경계가 뒤집혀 판정이 이상해지는 것을 방지).
static void apply_calibration(float &temp_warn, float &temp_danger,
                              float &hum_warn, float &hum_danger) {
  temp_warn   += clampf(QOS_CAL_TEMP_WARN_DELTA_C,    -TEMP_ADJUST_LIMIT_C,  TEMP_ADJUST_LIMIT_C);
  temp_danger += clampf(QOS_CAL_TEMP_DANGER_DELTA_C,  -TEMP_ADJUST_LIMIT_C,  TEMP_ADJUST_LIMIT_C);
  hum_warn    += clampf(QOS_CAL_HUM_WARN_DELTA_PCT,   -HUM_ADJUST_LIMIT_PCT, HUM_ADJUST_LIMIT_PCT);
  hum_danger  += clampf(QOS_CAL_HUM_DANGER_DELTA_PCT, -HUM_ADJUST_LIMIT_PCT, HUM_ADJUST_LIMIT_PCT);

  if (temp_danger < temp_warn) temp_danger = temp_warn;
  if (hum_danger  < hum_warn)  hum_danger  = hum_warn;
}

struct QosPlan {
  QoSLevel    qos;
  uint32_t    sleep_ms;  // 다음 Deep Sleep 시간 (ms) — QoS 레벨별로 다름
  const char *label;     // 로그용 ("NORMAL(NN)" | "WARNING(rule)" 등 — 판단 주체까지 표시
};

// gas_kohm/pres_hpa는 로깅용으로만 읽히며, mlp_weights.h에서 두 입력의 가중치가 0으로
// 고정되어 있어(온도·습도 외 결정 관여 금지) mlp_forward()에 넘겨도 결과에 영향이 없습니다.
// gas_ratio는 가스 기준값 추적(gas_baseline.h)을 쓰지 않으므로 중립값 1.0f를 고정으로 씁니다.
static QosPlan decide_qos_plan(float temp, float hum, float gas_kohm, float pres_hpa,
                               float temp_warn, float temp_danger,
                               float hum_warn, float hum_danger,
                               float &nn_score_out) {
#if MLP_WEIGHTS_TRAINED
  // ── TinyML이 QoS를 직접 결정 ─────────────────────────────────────────────
  nn_score_out = mlp_forward(temp, hum, gas_kohm, pres_hpa, 1.0f);
  if (nn_score_out >= MLP_TH_QOS2) return { QoSLevel::QoS2, QOS2_SLEEP_MS, "EMERGENCY(NN)" };
  if (nn_score_out >= MLP_TH_QOS1) return { QoSLevel::QoS1, QOS1_SLEEP_MS, "WARNING(NN)" };
  return { QoSLevel::QoS0, QOS0_SLEEP_MS, "NORMAL(NN)" };
#else
  // ── MLP 미학습 — 사람이 정한 SPEC 규칙(+TinyML 임계값 보정)으로 안전하게 폴백 ──
  nn_score_out = -1.0f;  // -1 = 이번 판단에 MLP가 쓰이지 않았음을 표시 (텔레메트리 참고용)
  const Severity temp_sev = classify(temp, temp_warn, temp_danger);
  const Severity hum_sev  = classify(hum,  hum_warn,  hum_danger);
  const Severity worst    = (temp_sev > hum_sev) ? temp_sev : hum_sev;

  switch (worst) {
    case Severity::DANGER:
      return { QoSLevel::QoS2, QOS2_SLEEP_MS, "EMERGENCY(rule)" };
    case Severity::WARNING:
      return { QoSLevel::QoS1, QOS1_SLEEP_MS, "WARNING(rule)" };
    default:
      return { QoSLevel::QoS0, QOS0_SLEEP_MS, "NORMAL(rule)" };
  }
#endif
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 I] MQTT 설정 수신 콜백 — on_mqtt_message()
 *
 * 수신 JSON 예시:
 * {
 *   "ENVIRONMENT": { "TEMP_WARN_C": 30, "TEMP_DANGER_C": 50,
 *                     "HUM_WARN_PCT": 70, "HUM_DANGER_PCT": 85 },
 *   "POWER_MANAGEMENT": { "POWER_MODE": "BATTERY", "CURRENT_BATTERY_LEVEL": 85 }
 * }
 * ═══════════════════════════════════════════════════════════════════════════ */
static void on_mqtt_message(char *topic, byte *payload, unsigned int length) {
  g_config_rx_count++;
  Serial.printf("[MQTT설정] 메시지 수신 — 토픽: %s | 페이로드: %u bytes\n", topic, length);

  JsonDocument doc;
  DeserializationError parse_err = deserializeJson(doc, (const char *)payload, length);
  if (parse_err) {
    Serial.printf("[MQTT설정] ⚠ JSON 파싱 실패: %s — 기존 설정 유지\n", parse_err.c_str());
    return;
  }

  if (xSemaphoreTake(g_config_mutex, portMAX_DELAY) == pdTRUE) {
    if (doc["ENVIRONMENT"]["TEMP_WARN_C"].is<float>())
      g_config.temp_warn_c = doc["ENVIRONMENT"]["TEMP_WARN_C"].as<float>();
    if (doc["ENVIRONMENT"]["TEMP_DANGER_C"].is<float>())
      g_config.temp_danger_c = doc["ENVIRONMENT"]["TEMP_DANGER_C"].as<float>();
    if (doc["ENVIRONMENT"]["HUM_WARN_PCT"].is<float>())
      g_config.hum_warn_pct = doc["ENVIRONMENT"]["HUM_WARN_PCT"].as<float>();
    if (doc["ENVIRONMENT"]["HUM_DANGER_PCT"].is<float>())
      g_config.hum_danger_pct = doc["ENVIRONMENT"]["HUM_DANGER_PCT"].as<float>();

    if (doc["POWER_MANAGEMENT"]["POWER_MODE"].is<const char *>()) {
      strncpy(g_config.power_mode,
              doc["POWER_MANAGEMENT"]["POWER_MODE"].as<const char *>(),
              sizeof(g_config.power_mode) - 1);
      g_config.power_mode[sizeof(g_config.power_mode) - 1] = '\0';
    }
    if (doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].is<int>()) {
      int lvl = doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].as<int>();
      if (lvl < 0)   lvl = 0;
      if (lvl > 100) lvl = 100;
      g_config.current_battery_level = (uint8_t)lvl;
    }

    xSemaphoreGive(g_config_mutex);

    Serial.println("[MQTT설정] ✓ g_config 갱신 완료:");
    Serial.printf("  ├ [ENV] 온도 경고/위험  : %.1f / %.1f °C\n", g_config.temp_warn_c, g_config.temp_danger_c);
    Serial.printf("  ├ [ENV] 습도 경고/위험  : %.1f / %.1f %%\n", g_config.hum_warn_pct, g_config.hum_danger_pct);
    Serial.printf("  ├ [POWER] 전원 모드     : %s\n", g_config.power_mode);
    Serial.printf("  └ [POWER] 가상 배터리   : %u %%\n", g_config.current_battery_level);
  } else {
    Serial.println("[MQTT설정] ✗ [오류] Mutex 획득 실패 — 설정 갱신 건너뜀");
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 J] MQTT 브로커 연결 및 설정 토픽 구독 — mqtt_connect_and_subscribe()
 * ═══════════════════════════════════════════════════════════════════════════ */
static void mqtt_connect_and_subscribe() {
  Serial.printf("[MQTT설정] 브로커 연결 시도 — %s:%u (클라이언트 ID: %s)\n",
                MQTT_BROKER_IP, MQTT_BROKER_PORT, BOARD1_CLIENT_ID);

  if (mqtt_client.connect(BOARD1_CLIENT_ID)) {
    Serial.printf("[MQTT설정] ✓ 브로커 연결 성공 (ID: %s)\n", BOARD1_CLIENT_ID);
    if (mqtt_client.subscribe(MQTT_CONFIG_TOPIC, 1)) {
      Serial.printf("[MQTT설정] ✓ 구독 성공 — 토픽: \"%s\" (QoS 1)\n", MQTT_CONFIG_TOPIC);
    } else {
      Serial.printf("[MQTT설정] ⚠ 구독 실패 — 토픽: \"%s\"\n", MQTT_CONFIG_TOPIC);
    }
  } else {
    Serial.printf("[MQTT설정] ⚠ 브로커 연결 실패 (rc=%d) — 이번 부팅은 기본/이전 임계값으로 진행\n",
                  mqtt_client.state());
  }
}

// 구독 직후 최대 timeout_ms 동안 retained 설정 메시지를 기다립니다.
static void wait_for_config_sync(uint32_t timeout_ms) {
  const uint32_t rx0 = g_config_rx_count;
  const uint32_t t0  = millis();
  while (mqtt_client.connected() && g_config_rx_count == rx0 && (millis() - t0) < timeout_ms) {
    mqtt_client.loop();
    delay(5);
  }
  if (g_config_rx_count == rx0) {
    Serial.println("[설정동기화] ⚠ 제한 시간 내 설정 메시지 없음 — 이전/기본 임계값 사용");
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 K] Wi-Fi 연결 — RTC에 저장된 채널/BSSID로 빠른 재연결
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
 * [섹션 L] Deep Sleep 진입 — enter_deep_sleep()
 *
 * 실제 esp_deep_sleep_start()를 사용합니다. 이 함수는 반환하지 않습니다 —
 * 호출 즉시 전원 도메인이 내려가고, ms 후 타이머로 깨어나면 setup()부터
 * 완전히 다시 시작됩니다 (일반 RAM 초기화, RTC_DATA_ATTR만 유지).
 * ═══════════════════════════════════════════════════════════════════════════ */
static void enter_deep_sleep(uint32_t ms) {
  delay(RADIO_FLUSH_MS);         // 직전에 보낸 UDP/TCP 패킷이 무선으로 나갈 시간
  mqtt_client.disconnect();
  udp.stop();
  Serial.printf("[절전] Deep Sleep 진입 — %lu ms 후 재부팅\n", (unsigned long)ms);
  Serial.flush();                // UART 송신 버퍼를 비운 뒤 잠들어야 로그가 깨지지 않음
  esp_sleep_enable_timer_wakeup((uint64_t)ms * 1000ULL);
  esp_deep_sleep_start();        // 반환하지 않음 — 재부팅 후 setup()부터 다시 시작
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 M] 이중 전원 모드 센서 입력 전처리 — preprocess_sensor_inputs()
 * ═══════════════════════════════════════════════════════════════════════════ */
static SensorInputs preprocess_sensor_inputs() {
  SensorInputs inputs;

  if (!read_bme680()) {
    Serial.println("[BME680] 측정 실패");
  }
  inputs.temp     = read_temperature();
  inputs.hum      = read_humidity();
  inputs.gas_kohm = read_gas_resistance_kohm();  // 정보성 로깅 전용 (QoS 판단에 사용하지 않음)
  inputs.pres_hpa = read_pressure_hpa();         // 정보성 로깅 전용 (QoS 판단에 사용하지 않음)
  inputs.rssi     = get_wifi_rssi();             // 정보성 로깅 전용 (QoS 판단에 사용하지 않음)

  char    mode_snapshot[16] = "EXTERNAL_5V";
  uint8_t virt_batt_snapshot = 100;

  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    strncpy(mode_snapshot, g_config.power_mode, sizeof(mode_snapshot) - 1);
    mode_snapshot[sizeof(mode_snapshot) - 1] = '\0';
    virt_batt_snapshot = g_config.current_battery_level;
    xSemaphoreGive(g_config_mutex);
  } else {
    Serial.println("[전처리] ⚠ Mutex 획득 타임아웃(50ms) — 기본 EXTERNAL_5V 모드로 폴백");
  }

  if (strcmp(mode_snapshot, "BATTERY") == 0) {
    inputs.battery_pct = read_battery_adc_pct();
    Serial.printf("[전처리] 전원모드=BATTERY → GPIO %d ADC 실측 배터리: %.1f %%\n",
                  BATTERY_ADC_PIN, inputs.battery_pct);
  } else {
    inputs.battery_pct = (float)virt_batt_snapshot;
    Serial.printf("[전처리] 전원모드=EXTERNAL_5V → 대시보드 가상 배터리: %.0f %%\n",
                  inputs.battery_pct);
  }

  return inputs;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 N] UDP 패킷 수신 대기 함수 — wait_for_packet()
 * ═══════════════════════════════════════════════════════════════════════════ */
static bool wait_for_packet(MsgType expected_type, uint16_t target_msg_id) {
  unsigned long start_time = millis();

  while (millis() - start_time < 2000) {
    int packetSize = udp.parsePacket();

    if (packetSize >= (int)sizeof(Header)) {
      uint8_t buffer[128];
      udp.read(buffer, sizeof(buffer));
      Header *header = (Header *)buffer;

      if (header->msg_type == expected_type) {
        if (expected_type == MsgType::PUBACK &&
            ((PubAckPacket *)buffer)->msg_id == target_msg_id)
          return true;
        if (expected_type == MsgType::PUBREC &&
            ((PubRecPacket *)buffer)->msg_id == target_msg_id)
          return true;
        if (expected_type == MsgType::PUBCOMP &&
            ((PubCompPacket *)buffer)->msg_id == target_msg_id)
          return true;
      }
    }
    delay(10);
  }

  return false;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 O] TCP 전송 — QoS 2 신뢰성 모드
 *
 * 와이어 포맷: [len: uint16][PublishPacket 바이트], 응답: PUBCOMP 4바이트.
 * 1회 시도 = 연결 → 전송 → PUBCOMP 대기(최대 2.0초) → 연결 종료.
 * ═══════════════════════════════════════════════════════════════════════════ */
static bool tcp_publish_and_wait_ack(const PublishPacket &pkt) {
  WiFiClient tcp;
  if (!tcp.connect(UDP_SERVER_IP, TCP_SERVER_PORT, 2000)) {
    return false;
  }
  tcp.setNoDelay(true);

  const uint16_t body_len = (uint16_t)publish_packet_size(pkt);
  uint8_t frame[2 + sizeof(PublishPacket)];
  frame[0] = (uint8_t)(body_len & 0xFF);
  frame[1] = (uint8_t)(body_len >> 8);
  memcpy(frame + 2, &pkt, body_len);
  if (tcp.write(frame, 2u + body_len) != (size_t)(2u + body_len)) {
    tcp.stop();
    return false;
  }

  uint8_t ack[sizeof(PubCompPacket)];
  size_t got = 0;
  const unsigned long start_ms = millis();
  while (got < sizeof(ack) && millis() - start_ms < 2000) {
    if (tcp.available()) {
      int n = tcp.read(ack + got, sizeof(ack) - got);
      if (n > 0) got += (size_t)n;
    } else if (!tcp.connected()) {
      break;
    } else {
      delay(5);
    }
  }
  tcp.stop();

  if (got < sizeof(ack)) return false;
  const PubCompPacket *comp = (const PubCompPacket *)ack;
  return comp->header.msg_type == MsgType::PUBCOMP && comp->msg_id == pkt.msg_id;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * setup() — Arduino 초기화 진입점
 *
 * Deep Sleep에서 깨어날 때마다(타이머 웨이크업이든 콜드 부팅이든) 이 함수부터
 * 다시 실행됩니다. Wi-Fi/BME680/MQTT를 매번 새로 초기화하되, RTC_DATA_ATTR로
 * 저장해 둔 채널/BSSID 덕분에 Wi-Fi 재연결은 빠르게 끝납니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
// 이번 부팅(=Deep Sleep 사이클)의 시작 시각. loop()에서 활성 시간(active_ms)을 계산할 때
// 여기서부터 재는 것이 중요합니다 — setup()의 Wi-Fi/BME680/MQTT 재연결도 실제 전력을 쓰는
// 활성 구간이라, loop() 진입 시점부터만 재면 재부팅형 사이클에서는 그 시간이 통째로
// 전력 추정에서 빠져 버립니다(Sleep 비율이 실제보다 좋게 나오는 착시가 생김).
static unsigned long g_boot_start_ms = 0;

void setup() {
  g_boot_start_ms = millis();
  Serial.begin(115200);
  delay(100);

  g_boot_count++;
  const esp_sleep_wakeup_cause_t wake_reason = esp_sleep_get_wakeup_cause();
  Serial.println("\n╔═══════════════════════════════════════════════════════════╗");
  Serial.println("║  Board 1: Gingerbread — 온도/습도 QoS + Deep Sleep 펌웨어  ║");
  Serial.println("╚═══════════════════════════════════════════════════════════╝");
  Serial.printf("[부팅] #%lu | 원인: %s\n", (unsigned long)g_boot_count,
                wake_reason == ESP_SLEEP_WAKEUP_TIMER ? "Deep Sleep 타이머 깨어남" : "콜드 부팅 / 리셋");

  // QoS를 실제로 누가 결정하는지 — 학습되지 않은 상태를 학습된 것처럼 오인하지 않도록
  // 매 부팅마다 표시합니다 (mlp_weights.h의 MLP_WEIGHTS_TRAINED 플래그 기준).
#if MLP_WEIGHTS_TRAINED
  Serial.println("[MLP] ✓ TinyML이 QoS를 직접 결정합니다 (학습된 값, ml_model/train.py 생성)");
  Serial.printf("[MLP] 위험 점수 임계값: QoS1 ≥ %.3f | QoS2 ≥ %.3f\n", MLP_TH_QOS1, MLP_TH_QOS2);
#else
  Serial.println("[MLP] ⚠ 아직 학습되지 않음 — 사람이 정한 SPEC 규칙으로 QoS를 폴백 결정합니다");
  Serial.println("[MLP] ⚠ ml_model/README.md 절차로 데이터를 모으고 train.py를 실행하면 MLP가 직접 결정하도록 전환됩니다");
  // TinyML 임계값 보정 상태 — MLP 미학습 시 폴백 규칙에만 적용됩니다.
#if QOS_CALIBRATION_TRAINED
  Serial.println("[보정] TinyML 임계값 보정: 학습된 값 사용 중 (ml_model/train_threshold_calibration.py 생성)");
#else
  Serial.println("[보정] TinyML 임계값 보정: 아직 학습되지 않음 (delta=0, SPEC 기준 그대로 사용)");
#endif
  Serial.printf("[보정] 온도 delta: warn %+.1f°C, danger %+.1f°C (최대 ±%.1f°C) | "
                "습도 delta: warn %+.1f%%, danger %+.1f%% (최대 ±%.1f%%)\n",
                QOS_CAL_TEMP_WARN_DELTA_C, QOS_CAL_TEMP_DANGER_DELTA_C, TEMP_ADJUST_LIMIT_C,
                QOS_CAL_HUM_WARN_DELTA_PCT, QOS_CAL_HUM_DANGER_DELTA_PCT, HUM_ADJUST_LIMIT_PCT);
#endif

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

  g_config_mutex = xSemaphoreCreateMutex();
  if (g_config_mutex == nullptr) {
    Serial.println("[부팅] ✗ [심각] FreeRTOS Mutex 생성 실패! 5초 후 재시작합니다.");
    delay(5000);
    ESP.restart();
  }

  analogSetAttenuation(ADC_11db);
  pinMode(BATTERY_ADC_PIN, INPUT);

  Serial.printf("[부팅] Wi-Fi 연결 시도 중... SSID: \"%s\"\n", WIFI_SSID);
  if (!wifi_connect_with_fallback()) {
    // Wi-Fi 연결 실패: 이번 사이클은 아무것도 전송하지 못하므로 짧게 재시도하고
    // 그래도 안 되면 QoS 0 주기만큼 Deep Sleep 후 다시 시도합니다.
    Serial.println("[부팅] ✗ Wi-Fi 연결 실패 — 이번 사이클을 건너뛰고 Deep Sleep 재시도");
    g_total_active_ms += (uint32_t)(millis() - g_boot_start_ms);
    enter_deep_sleep(QOS0_SLEEP_MS);
  }
  wifi_remember_ap();
  Serial.printf("[부팅] ✓ Wi-Fi 연결 성공 — 할당 IP: %s | RSSI: %d dBm\n",
                WiFi.localIP().toString().c_str(), (int8_t)WiFi.RSSI());

  Serial.printf("[절전] QoS별 주기 — 정상(QoS0): %lus Deep Sleep | 경고(QoS1): %lus Deep Sleep | "
                "위험(QoS2): %lus Deep Sleep (가장 자주 확인)\n",
                (unsigned long)(QOS0_SLEEP_MS / 1000), (unsigned long)(QOS1_SLEEP_MS / 1000),
                (unsigned long)(QOS2_SLEEP_MS / 1000));

  udp.begin(UDP_SERVER_PORT);
  Serial.printf("[부팅] ✓ UDP 소켓 초기화 완료 (포트 %u, 게이트웨이: %s)\n",
                UDP_SERVER_PORT, UDP_SERVER_IP);

  mqtt_client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT);
  mqtt_client.setCallback(on_mqtt_message);
  mqtt_client.setKeepAlive(60);
  mqtt_client.setSocketTimeout(2);
  mqtt_connect_and_subscribe();
  wait_for_config_sync(CONFIG_SYNC_WAIT_MS);  // retained 설정(온도/습도 임계값)을 받을 시간을 줌

  // 커스텀 MQTT-SN CONNECT 패킷 전송 — 게이트웨이에 세션 등록
  ConnectPacket conn_pkt;
  conn_pkt.header.length   = sizeof(ConnectPacket);
  conn_pkt.header.msg_type = MsgType::CONNECT;
  strncpy(conn_pkt.client_id, BOARD1_CLIENT_ID, sizeof(conn_pkt.client_id) - 1);
  conn_pkt.client_id[sizeof(conn_pkt.client_id) - 1] = '\0';
  conn_pkt.sleep_duration = (uint16_t)(QOS0_SLEEP_MS / 1000);  // 정상 상태 기준 주기 힌트

  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&conn_pkt, sizeof(conn_pkt));
  udp.endPacket();
  Serial.printf("[부팅] ✓ CONNECT 패킷 전송 → 게이트웨이 세션 등록 (ID: %s)\n", BOARD1_CLIENT_ID);

  Serial.println("[부팅] ══ 초기화 완료, 메인 루프 시작 ══\n");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * loop() — Arduino 메인 루프
 *
 * 매 사이클 전송 후 enter_deep_sleep()에서 재부팅되므로 이 함수가 다시 호출되는 일은
 * 없습니다(다음 실행은 항상 setup()부터). Sleep 시간만 QoS 레벨에 따라 다릅니다
 * (정상 60초 → 경고 30초 → 위험 3초로 점점 짧아짐).
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {

  if (!mqtt_client.connected()) {
    mqtt_connect_and_subscribe();
  }
  mqtt_client.loop();

  Serial.println("\n────────────────────────────────────────────────────────────");
  Serial.println("[루프] ▶ 새로운 측정 및 전송 사이클 시작");

  SensorInputs sensor_data = preprocess_sensor_inputs();
  Serial.printf("[센싱] 온도: %.2f°C | 습도: %.2f%% | 가스저항: %.2f kΩ | "
                "배터리: %.0f%% | RSSI: %d dBm\n",
                sensor_data.temp, sensor_data.hum, sensor_data.gas_kohm,
                sensor_data.battery_pct, sensor_data.rssi);

  // ── QoS 판단: 온도/습도 임계값 스냅샷 획득 후 결정 ─────────────────────
  float t_warn = 30.0f, t_danger = 50.0f, h_warn = 70.0f, h_danger = 85.0f;
  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    t_warn   = g_config.temp_warn_c;
    t_danger = g_config.temp_danger_c;
    h_warn   = g_config.hum_warn_pct;
    h_danger = g_config.hum_danger_pct;
    xSemaphoreGive(g_config_mutex);
  }

  // TinyML 보정(qos_calibration.h)을 SPEC/대시보드 값 위에 적용 — MLP 미학습 시 폴백 규칙에서만
  // 쓰이며, 폭은 항상 하드클램프됩니다.
  apply_calibration(t_warn, t_danger, h_warn, h_danger);

  float nn_score = -1.0f;  // MLP 위험 점수 (0~1). 규칙 폴백 중이면 -1
  const QosPlan plan = decide_qos_plan(sensor_data.temp, sensor_data.hum,
                                       sensor_data.gas_kohm, sensor_data.pres_hpa,
                                       t_warn, t_danger, h_warn, h_danger, nn_score);
  const uint8_t net_status = (sensor_data.rssi < RSSI_UNSTABLE_DBM) ? 1 : 0;  // 정보성 로깅 전용

#if MLP_WEIGHTS_TRAINED
  Serial.printf("[QoS결정] %s → QoS %d (MLP 위험 점수: %.4f, 임계값 QoS1≥%.3f/QoS2≥%.3f)\n",
                plan.label, (int)plan.qos, nn_score, MLP_TH_QOS1, MLP_TH_QOS2);
#else
  Serial.printf("[QoS결정] %s → QoS %d (온도 임계 %.1f/%.1f°C, 습도 임계 %.1f/%.1f%% — MLP 미학습, TinyML 보정 반영됨)\n",
                plan.label, (int)plan.qos, t_warn, t_danger, h_warn, h_danger);
#endif

  // ── PUBLISH 패킷 조립 (topic 1: 환경 데이터) ────────────────────────────
  PublishPacket pub_pkt;
  pub_pkt.header.length   = sizeof(PublishPacket);
  pub_pkt.header.msg_type = MsgType::PUBLISH;
  pub_pkt.msg_id          = g_msg_id_seed++;
  pub_pkt.topic_id        = 1;
  pub_pkt.qos             = plan.qos;
  pub_pkt.network_status  = net_status;
  pub_pkt.data_urgency    = (plan.qos != QoSLevel::QoS0) ? 1 : 0;

  float current_sleep_ratio = 0.0f;
  {
    uint32_t tot = g_total_active_ms + g_total_sleep_ms;
    if (tot > 0) current_sleep_ratio = (float)g_total_sleep_ms / (float)tot;
  }

  snprintf(pub_pkt.payload, sizeof(pub_pkt.payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,"
           "\"battery\":%.0f,\"mode\":\"%s\",\"qos\":%d,"
           "\"nn\":%.3f,\"sleep_r\":%.3f}",
           sensor_data.temp, sensor_data.hum, sensor_data.gas_kohm,
           sensor_data.battery_pct, plan.label, (int)plan.qos, nn_score, current_sleep_ratio);

  Serial.printf("[루프] PUBLISH 페이로드 (MsgID=%u): %s\n", pub_pkt.msg_id, pub_pkt.payload);

  // ── QoS 레벨별 전송 (UDP QoS0/1, TCP QoS2) ──────────────────────────────
  int       retry_count         = 0;
  const int max_retries         = 3;
  bool      transaction_success = false;
  float     rtt_ms              = 0.0f;

  unsigned long rtt_start_us = micros();
  const bool use_tcp = ((uint8_t)plan.qos >= TCP_MIN_QOS);
  Serial.printf("[루프] 전송 계층: %s (QoS %d)\n", use_tcp ? "TCP" : "UDP", (int)plan.qos);

  if (use_tcp) {
    while (retry_count <= max_retries) {
      if (tcp_publish_and_wait_ack(pub_pkt)) {
        rtt_ms = (float)(micros() - rtt_start_us) / 1000.0f;
        transaction_success = true;
        Serial.printf("[TCP QoS %d] ✓ 성공 — PUBCOMP 수신 확인 | RTT: %.2f ms | 재전송: %d회 (MsgID: %u)\n",
                      (int)plan.qos, rtt_ms, retry_count, pub_pkt.msg_id);
        break;
      }
      retry_count++;
      Serial.printf("[TCP QoS %d] ⚠ 실패/타임아웃 — 재전송 (%d/%d)\n",
                    (int)plan.qos, retry_count, max_retries);
    }
  } else if (plan.qos == QoSLevel::QoS0) {
    udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
    udp.write((uint8_t *)&pub_pkt, publish_packet_size(pub_pkt));
    udp.endPacket();
    rtt_ms = (float)(micros() - rtt_start_us) / 1000.0f;
    transaction_success = true;
    Serial.printf("[QoS 0] ✓ 단발 전송 완료 | RTT(단방향): %.2f ms (응답 대기 없음)\n", rtt_ms);
  } else if (plan.qos == QoSLevel::QoS1) {
    while (retry_count <= max_retries) {
      udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
      udp.write((uint8_t *)&pub_pkt, publish_packet_size(pub_pkt));
      udp.endPacket();

      if (wait_for_packet(MsgType::PUBACK, pub_pkt.msg_id)) {
        rtt_ms = (float)(micros() - rtt_start_us) / 1000.0f;
        transaction_success = true;
        Serial.printf("[QoS 1] ✓ 성공 — PUBACK 수신 확인 | RTT: %.2f ms | 재전송: %d회 (MsgID: %u)\n",
                      rtt_ms, retry_count, pub_pkt.msg_id);
        break;
      }
      retry_count++;
      Serial.printf("[QoS 1] ⚠ 타임아웃 — PUBLISH 재전송 (%d/%d)\n", retry_count, max_retries);
    }
  }

  // ── 활성 시간 누적 ───────────────────────────────────────────────────────
  // setup()의 Wi-Fi/BME680/MQTT 재연결부터 포함해서 잽니다 (g_boot_start_ms 참고).
  // loop() 진입 시점부터만 재면 이 재부팅형 사이클에서는 재연결 시간이 통째로 빠집니다.
  unsigned long active_elapsed_ms = millis() - g_boot_start_ms;
  g_total_active_ms += (uint32_t)active_elapsed_ms;

  if (transaction_success) {
    g_packet_count++;
    g_total_bytes += (uint32_t)sizeof(pub_pkt);
  }

  float sleep_mode_ratio = 0.0f;
  uint32_t total_elapsed = g_total_active_ms + g_total_sleep_ms;
  if (total_elapsed > 0) sleep_mode_ratio = (float)g_total_sleep_ms / (float)total_elapsed;

  if (transaction_success) {
    Serial.printf("[루프] == QoS %d 전송 트랜잭션 성공 (MsgID: %u, 판정: %s)\n"
                  "       RTT: %.2f ms | 재전송: %d회 | Sleep비율: %.1f%%\n"
                  "       누적 패킷: %u | 누적 바이트: %u\n",
                  (int)plan.qos, pub_pkt.msg_id, plan.label,
                  rtt_ms, retry_count, sleep_mode_ratio * 100.0f,
                  g_packet_count, g_total_bytes);

    // ── topic 2: 핸드셰이크 완료 후 실측 RTT/retry 텔레메트리 ────────────
    PublishPacket telemetry_pkt;
    telemetry_pkt.header.length   = sizeof(PublishPacket);
    telemetry_pkt.header.msg_type = MsgType::PUBLISH;
    telemetry_pkt.msg_id          = g_msg_id_seed++;
    telemetry_pkt.topic_id        = 2;
    telemetry_pkt.qos             = QoSLevel::QoS0;
    telemetry_pkt.network_status  = net_status;
    telemetry_pkt.data_urgency    = 0;

    snprintf(telemetry_pkt.payload, sizeof(telemetry_pkt.payload),
             "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,"
             "\"battery\":%.0f,\"mode\":\"%s\",\"qos\":%d,\"nn\":%.3f,"
             "\"rtt\":%.2f,\"retry\":%d,\"sleep_r\":%.4f,"
             "\"act\":%lu,\"slp\":%lu,\"tp\":\"%s\","
             "\"pkt\":%u,\"bytes\":%u}",
             sensor_data.temp, sensor_data.hum, sensor_data.gas_kohm,
             sensor_data.battery_pct, plan.label, (int)plan.qos, nn_score,
             rtt_ms, retry_count, sleep_mode_ratio,
             (unsigned long)active_elapsed_ms,
             (unsigned long)plan.sleep_ms,
             use_tcp ? "tcp" : "udp",
             g_packet_count, g_total_bytes);

    udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
    udp.write((uint8_t *)&telemetry_pkt, publish_packet_size(telemetry_pkt));
    udp.endPacket();
    Serial.printf("[텔레메트리] 실측 메트릭 전송 완료 (MsgID=%u, topic=2)\n", telemetry_pkt.msg_id);
  } else {
    Serial.printf("[루프] X QoS %d 전송 최종 실패 -- 재전송 한도(%d회) 초과 (MsgID: %u)\n",
                  (int)plan.qos, max_retries, pub_pkt.msg_id);
  }

  // ── DISCONNECT 통지 후 Deep Sleep (세 QoS 레벨 모두 동일하게 처리, 주기만 다름) ──
  DisconnectPacket disc_pkt;
  disc_pkt.header.length   = sizeof(DisconnectPacket);
  disc_pkt.header.msg_type = MsgType::DISCONNECT;
  disc_pkt.sleep_mode_flag = 1;
  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&disc_pkt, sizeof(disc_pkt));
  udp.endPacket();
  Serial.printf("[루프] DISCONNECT(Sleep) 전송 완료 — %.1f초 후 재부팅\n", plan.sleep_ms / 1000.0f);

  g_total_sleep_ms += plan.sleep_ms;
  enter_deep_sleep(plan.sleep_ms);  // 반환하지 않음 — 다음 실행은 setup()부터
}
