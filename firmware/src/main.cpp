/*
 * firmware/src/main.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 Gingerbread — ESP32-S3 엣지 디바이스 메인 펌웨어
 *
 * [요구사항 3] 엣지 디바이스 전원 모드 분기 및 전처리 로직 통합 버전
 *
 * 주요 변경 사항:
 *   1. MQTT 설정 구독 통합
 *      - PubSubClient + ArduinoJson 으로 "gingerbread/config" 토픽 구독
 *      - FreeRTOS Mutex 보호 하의 SystemConfig 구조체로 동적 설정 관리
 *
 *   2. 전원 모드 분기 (preprocess_tinyml_inputs)
 *      - EXTERNAL_5V 모드: 대시보드 가상 배터리 레벨을 TinyML 입력으로 사용
 *      - BATTERY 모드: 하드웨어 ADC로 실제 배터리 전압을 측정하여 사용
 *
 *   3. TinyML 추론 동적 임계값 적용
 *      - 하드코딩 임계값 제거 → Mutex 보호 SystemConfig 값 사용
 *      - PUBLISH 페이로드에 gas(가스 저항값) 필드 추가
 *
 *   기존 QoS 0/1/2 핸드셰이크 시퀀스는 완전히 보존됩니다.
 * ═══════════════════════════════════════════════════════════════════════════
 */

/* ─── 라이브러리 헤더 인클루드 ─────────────────────────────────────────── */
#include "protocol.h"       // 커스텀 MQTT-SN 프로토콜 구조체 정의
#include <Arduino.h>
#include <ArduinoJson.h>    // MQTT 페이로드 JSON 파싱 (gingerbread/config)
#include <PubSubClient.h>   // MQTT 브로커 통신 (설정 구독 전용)
#include <WiFi.h>
#include <WiFiClient.h>     // PubSubClient TCP 연결에 필요
#include <WiFiUdp.h>        // 커스텀 MQTT-SN 프로토콜 UDP 전송
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h> // FreeRTOS Mutex (SemaphoreHandle_t)

/* ─── 네트워크 자격증명 및 서버 설정 ──────────────────────────────────── */
static const char *WIFI_SSID       = "YOUR_WIFI_SSID";
static const char *WIFI_PASSWORD   = "YOUR_WIFI_PASSWORD";

// 커스텀 MQTT-SN (UDP) 게이트웨이 서버 주소 및 포트
static const char    *UDP_SERVER_IP   = "192.168.0.100";
static const uint16_t UDP_SERVER_PORT = 5000;

// 표준 MQTT 브로커 주소 및 포트 (설정 구독 전용)
// 동일 라즈베리파이에서 Mosquitto 브로커가 실행 중이라 가정합니다.
static const char    *MQTT_BROKER_IP   = "192.168.0.100";
static const uint16_t MQTT_BROKER_PORT = 1883;

// ESP32가 구독하는 설정 토픽 (게이트웨이 서버가 retain=true로 발행)
static const char *MQTT_CONFIG_TOPIC = "gingerbread/config";

/* ─── 하드웨어 핀 정의 ─────────────────────────────────────────────────── */
// 배터리 전압 측정 ADC 핀 (BATTERY 모드에서만 사용)
// ESP32-S3에서 ADC1_CH0 = GPIO 1 (보드별로 다를 수 있음)
static const int BATTERY_ADC_PIN = 1;

// ADC 참조 전압 및 배터리 전압 범위 (리튬 이온 기준)
static const float ADC_REF_VOLTAGE   = 3.3f;  // ESP32 ADC 기준 전압 (V)
static const float ADC_MAX_VALUE     = 4095.0f; // 12비트 ADC 최대값
static const float BATTERY_VOLT_MAX  = 4.2f;   // 만충 전압 (V)
static const float BATTERY_VOLT_MIN  = 3.0f;   // 방전 한계 전압 (V)

/* ═══════════════════════════════════════════════════════════════════════════
 * [신규] SystemConfig 구조체
 * FreeRTOS Mutex로 보호되는 동적 설정 저장소.
 * MQTT "gingerbread/config" 토픽 수신 시 갱신됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
struct SystemConfig {
  // ── NETWORK 섹션 ─────────────────────────────────────────────────────────
  int8_t  rssi_threshold;       // RSSI 신호 강도 임계값 (dBm, 기본값: -80)
  float   packet_loss_limit;    // 패킷 손실률 허용 상한 (%, 기본값: 5.0)

  // ── ENVIRONMENT 섹션 ─────────────────────────────────────────────────────
  float   gas_threshold_kohm;       // 가스 저항 위험 임계값 (kΩ, 기본값: 20.0)
  float   temp_threshold_celsius;   // 온도 위험 임계값 (°C, 기본값: 45.0)

  // ── POWER_MANAGEMENT 섹션 ────────────────────────────────────────────────
  char    power_mode[16];           // 전원 모드 문자열: "EXTERNAL_5V" 또는 "BATTERY"
  uint8_t current_battery_level;    // 대시보드 가상 배터리 레벨 (0~100, EXTERNAL_5V 모드에서 사용)
};

/* ─── 전역 설정 인스턴스 및 Mutex ──────────────────────────────────────── */
// 기본값으로 초기화 (MQTT 수신 전에도 안전하게 동작)
static SystemConfig g_config = {
  .rssi_threshold       = -80,
  .packet_loss_limit    = 5.0f,
  .gas_threshold_kohm   = 20.0f,
  .temp_threshold_celsius = 45.0f,
  .power_mode           = "EXTERNAL_5V",
  .current_battery_level = 100,
};

// FreeRTOS Mutex: g_config에 대한 동시 접근(루프 태스크 + MQTT 콜백)을 보호
static SemaphoreHandle_t g_config_mutex = nullptr;

/* ─── TinyML 입력 전처리 결과 구조체 ──────────────────────────────────── */
// preprocess_tinyml_inputs()가 반환하는 전처리된 센서 입력값 묶음
struct TinyMLInputs {
  float temp;           // BME680 온도 (°C)
  float hum;            // BME680 상대 습도 (%)
  float gas_kohm;       // BME680 가스 저항값 (kΩ) — PUBLISH 페이로드 포함
  float battery_pct;    // TinyML 배터리 입력 (0.0~100.0, 모드에 따라 소스 다름)
  int8_t rssi;          // Wi-Fi RSSI (dBm)
};

/* ─── 통신 객체 ─────────────────────────────────────────────────────────── */
static WiFiUDP    udp;          // 커스텀 MQTT-SN 프로토콜 전용 UDP 소켓
static WiFiClient wifi_client;  // PubSubClient TCP 연결 기반 클라이언트
static PubSubClient mqtt_client(wifi_client); // MQTT 설정 구독 클라이언트

static uint16_t current_msg_id = 1; // MQTT-SN 메시지 ID (단조 증가)

/* ═══════════════════════════════════════════════════════════════════════════
 * [기존 유지] 하드웨어 센서 읽기 함수
 * 실제 BME680 센서 드라이버 연동 전까지 가상 값을 반환합니다.
 * ═══════════════════════════════════════════════════════════════════════════ */

// BME680 온도 읽기 (°C) — 기획서 테스트: 30도 초과 긴급 상황 시뮬레이션
static float read_temperature() {
  return 32.5f;
}

// BME680 상대 습도 읽기 (%)
static float read_humidity() {
  return 45.2f;
}

/*
 * BME680 가스 저항값 읽기 (kΩ)
 * [신규] PUBLISH 페이로드에 gas 필드 추가를 위해 신설됩니다.
 * 실제 센서에서는 Adafruit_BME680 라이브러리의 bme.gas_resistance / 1000.0 을 반환합니다.
 */
static float read_gas_resistance_kohm() {
  return 18.5f; // 기획서 테스트: gas_threshold_kohm(20kΩ) 미만 → 위험 상황 시뮬레이션
}

// Wi-Fi RSSI 읽기 (dBm) — 기획서 테스트: -80 (불안정 경계값 시뮬레이션)
static int8_t get_wifi_rssi() {
  return (int8_t)WiFi.RSSI();
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [신규] 배터리 전압 ADC 읽기 함수 (BATTERY 모드 전용)
 *
 * 하드웨어 ADC를 통해 실제 배터리 전압을 측정하고 0~100% 로 변환합니다.
 * 전압 분배기(저항 분배회로)를 통해 배터리 전압을 ADC 입력 범위(0~3.3V)로
 * 낮춰서 읽는다고 가정합니다. 분배 비율은 회로도에 따라 보정하세요.
 *
 * 반환값: 배터리 잔량 (0.0 ~ 100.0 %)
 * ═══════════════════════════════════════════════════════════════════════════ */
static float read_battery_adc_pct() {
  // ADC 원시 값 읽기 (12비트: 0~4095)
  int raw_adc = analogRead(BATTERY_ADC_PIN);

  // ADC 원시 값을 실제 전압으로 변환
  // 주의: 전압 분배기 회로를 사용하는 경우 분배비(예: 2.0)를 곱해야 합니다.
  float measured_voltage = ((float)raw_adc / ADC_MAX_VALUE) * ADC_REF_VOLTAGE;

  // 배터리 전압을 0~100% 범위로 선형 변환
  float pct = (measured_voltage - BATTERY_VOLT_MIN)
              / (BATTERY_VOLT_MAX - BATTERY_VOLT_MIN) * 100.0f;

  // 경계값 클리핑 (ADC 노이즈로 인한 범위 초과 방지)
  if (pct < 0.0f)   pct = 0.0f;
  if (pct > 100.0f) pct = 100.0f;

  return pct;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [신규] MQTT 설정 수신 콜백 함수
 *
 * PubSubClient가 "gingerbread/config" 토픽 메시지를 수신할 때 자동 호출됩니다.
 * ArduinoJson으로 JSON 페이로드를 파싱하고, Mutex를 획득하여
 * g_config 구조체를 안전하게 갱신합니다.
 *
 * 매개변수:
 *   topic   : 수신된 MQTT 토픽 문자열
 *   payload : JSON 페이로드 바이트 배열
 *   length  : 페이로드 길이
 * ═══════════════════════════════════════════════════════════════════════════ */
static void on_mqtt_message(char *topic, byte *payload, unsigned int length) {
  Serial.printf("[MQTT설정] 수신 — 토픽: %s, 페이로드 길이: %u bytes\n",
                topic, length);

  // ArduinoJson 파싱용 정적 도큐먼트 (512바이트 스택 할당)
  // config.json 전체 구조를 담기 충분한 크기입니다.
  StaticJsonDocument<512> doc;

  // JSON 페이로드 역직렬화 (byte* → char* 캐스팅 후 길이 지정 파싱)
  DeserializationError parse_err = deserializeJson(
    doc, (const char *)payload, length
  );

  if (parse_err) {
    // JSON 파싱 실패 — 기존 설정 유지, 오류만 로깅
    Serial.printf("[MQTT설정] JSON 파싱 실패: %s — 기존 설정 유지\n",
                  parse_err.c_str());
    return;
  }

  // ── Mutex 획득 후 g_config 안전하게 갱신 ─────────────────────────────
  // portMAX_DELAY: 무한 대기 (다른 태스크가 Mutex 해제할 때까지 블로킹)
  if (xSemaphoreTake(g_config_mutex, portMAX_DELAY) == pdTRUE) {

    // NETWORK 섹션 갱신 (키가 없으면 기존 값 유지)
    if (doc["NETWORK"]["RSSI_THRESHOLD"].is<int>()) {
      g_config.rssi_threshold =
          (int8_t)doc["NETWORK"]["RSSI_THRESHOLD"].as<int>();
    }
    if (doc["NETWORK"]["PACKET_LOSS_LIMIT"].is<float>()) {
      g_config.packet_loss_limit =
          doc["NETWORK"]["PACKET_LOSS_LIMIT"].as<float>();
    }

    // ENVIRONMENT 섹션 갱신
    if (doc["ENVIRONMENT"]["GAS_THRESHOLD_KOHM"].is<float>()) {
      g_config.gas_threshold_kohm =
          doc["ENVIRONMENT"]["GAS_THRESHOLD_KOHM"].as<float>();
    }
    if (doc["ENVIRONMENT"]["TEMP_THRESHOLD_CELSIUS"].is<float>()) {
      g_config.temp_threshold_celsius =
          doc["ENVIRONMENT"]["TEMP_THRESHOLD_CELSIUS"].as<float>();
    }

    // POWER_MANAGEMENT 섹션 갱신
    if (doc["POWER_MANAGEMENT"]["POWER_MODE"].is<const char *>()) {
      strncpy(g_config.power_mode,
              doc["POWER_MANAGEMENT"]["POWER_MODE"].as<const char *>(),
              sizeof(g_config.power_mode) - 1);
      g_config.power_mode[sizeof(g_config.power_mode) - 1] = '\0'; // null 종단 보장
    }
    if (doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].is<int>()) {
      int lvl = doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].as<int>();
      // 0~100 범위 클리핑 (비정상 값 방어)
      if (lvl < 0)   lvl = 0;
      if (lvl > 100) lvl = 100;
      g_config.current_battery_level = (uint8_t)lvl;
    }

    // Mutex 반환
    xSemaphoreGive(g_config_mutex);

    Serial.println("[MQTT설정] g_config 갱신 완료:");
    Serial.printf("  RSSI 임계값: %d dBm\n",       g_config.rssi_threshold);
    Serial.printf("  패킷손실 상한: %.1f %%\n",    g_config.packet_loss_limit);
    Serial.printf("  가스 임계값: %.1f kΩ\n",      g_config.gas_threshold_kohm);
    Serial.printf("  온도 임계값: %.1f °C\n",      g_config.temp_threshold_celsius);
    Serial.printf("  전원 모드: %s\n",              g_config.power_mode);
    Serial.printf("  가상 배터리: %u %%\n",         g_config.current_battery_level);
  } else {
    // Mutex 획득 실패 (시스템 이상 상황) — 설정 갱신 포기
    Serial.println("[MQTT설정] [오류] Mutex 획득 실패 — 설정 갱신 건너뜀");
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [신규] MQTT 브로커 연결 및 설정 토픽 구독 함수
 *
 * PubSubClient를 MQTT 브로커에 연결하고 "gingerbread/config" 토픽을 구독합니다.
 * 연결이 끊어진 경우 loop()에서 재연결 시도에 사용됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
static void mqtt_connect_and_subscribe() {
  // 고유 클라이언트 ID 생성 (MAC 주소 기반)
  // 동일 브로커에 다중 ESP32가 연결될 때 충돌 방지
  char client_id[32];
  uint64_t mac = ESP.getEfuseMac();
  snprintf(client_id, sizeof(client_id), "gingerbread-%08X", (uint32_t)(mac & 0xFFFFFFFF));

  Serial.printf("[MQTT설정] 브로커 연결 시도 — %s:%u (ID: %s)\n",
                MQTT_BROKER_IP, MQTT_BROKER_PORT, client_id);

  // PubSubClient 연결 시도 (clean_session=true, 인증 없음)
  if (mqtt_client.connect(client_id)) {
    Serial.println("[MQTT설정] 브로커 연결 성공");

    // "gingerbread/config" 토픽 구독 (QoS 1)
    // retain=true 로 발행된 메시지가 있으면 구독 즉시 수신됩니다.
    if (mqtt_client.subscribe(MQTT_CONFIG_TOPIC, 1)) {
      Serial.printf("[MQTT설정] 구독 성공 — 토픽: %s (QoS 1)\n",
                    MQTT_CONFIG_TOPIC);
    } else {
      Serial.printf("[MQTT설정] [경고] 구독 실패 — 토픽: %s\n",
                    MQTT_CONFIG_TOPIC);
    }
  } else {
    // 연결 실패 — 상태 코드 출력 (다음 루프에서 재시도)
    // rc: -4=연결끊김, -3=서버없음, -2=자격증명오류, -1=타임아웃
    Serial.printf("[MQTT설정] [경고] 브로커 연결 실패 (rc=%d) — 다음 루프에서 재시도\n",
                  mqtt_client.state());
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [신규] TinyML 입력 전처리 함수 — 전원 모드 분기
 *
 * POWER_MODE에 따라 배터리 레벨 소스를 결정합니다:
 *
 *   EXTERNAL_5V 모드:
 *     - 하드웨어 ADC를 사용하지 않습니다 (외부 전원 공급 중)
 *     - 웹 대시보드에서 내려온 CURRENT_BATTERY_LEVEL 가상값을
 *       TinyML 배터리 특징(feature)으로 직접 사용합니다 (시뮬레이션 모드)
 *
 *   BATTERY 모드:
 *     - 대시보드 가상값을 완전히 무시합니다
 *     - BATTERY_ADC_PIN에서 실제 배터리 전압을 측정하여
 *       0~100% 범위로 변환한 값을 TinyML 입력으로 사용합니다
 *
 * 반환값: 전처리된 TinyMLInputs 구조체 (모든 센서값 + 배터리 레벨)
 * ═══════════════════════════════════════════════════════════════════════════ */
static TinyMLInputs preprocess_tinyml_inputs() {
  TinyMLInputs inputs;

  // ── 공통 센서 데이터 읽기 (모드와 무관하게 항상 수집) ──────────────────
  inputs.temp      = read_temperature();
  inputs.hum       = read_humidity();
  inputs.gas_kohm  = read_gas_resistance_kohm(); // [신규] 가스 저항값 (kΩ)
  inputs.rssi      = get_wifi_rssi();

  // ── 전원 모드 분기: 배터리 레벨 소스 결정 ─────────────────────────────
  // g_config.power_mode를 읽기 위해 Mutex를 획득합니다.
  // Mutex가 없거나 획득 실패 시 기본값(100%)을 사용합니다.
  char   mode_snapshot[16]  = "EXTERNAL_5V"; // Mutex 없이도 안전한 기본값
  uint8_t virt_batt_snapshot = 100;

  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    // Mutex 보호 하에 설정값의 로컬 스냅샷 복사
    strncpy(mode_snapshot, g_config.power_mode, sizeof(mode_snapshot) - 1);
    virt_batt_snapshot = g_config.current_battery_level;
    xSemaphoreGive(g_config_mutex);
  } else {
    Serial.println("[전처리] [경고] Mutex 획득 타임아웃 — 기본 EXTERNAL_5V 모드 사용");
  }

  if (strcmp(mode_snapshot, "BATTERY") == 0) {
    // ── BATTERY 모드: 실제 하드웨어 ADC로 배터리 전압 측정 ──────────────
    // 대시보드 가상값(CURRENT_BATTERY_LEVEL)을 완전히 무시합니다.
    inputs.battery_pct = read_battery_adc_pct();
    Serial.printf("[전처리] BATTERY 모드 — ADC 실측 배터리: %.1f %%\n",
                  inputs.battery_pct);
  } else {
    // ── EXTERNAL_5V 모드: 대시보드 가상 배터리 레벨 사용 ─────────────────
    // 외부 전원으로 동작 중이므로 물리적 배터리 측정을 건너뜁니다.
    // 웹 대시보드에서 설정한 CURRENT_BATTERY_LEVEL 값을 그대로 사용합니다.
    inputs.battery_pct = (float)virt_batt_snapshot;
    Serial.printf("[전처리] EXTERNAL_5V 모드 — 대시보드 가상 배터리: %.0f %%\n",
                  inputs.battery_pct);
  }

  return inputs;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [리팩토링] 3단계 AI 기반 동적 QoS 자동 선택 알고리즘
 *
 * 기존 하드코딩 임계값(30.0°C, -75 dBm 등)을 완전히 제거하고,
 * Mutex로 보호된 g_config 의 동적 임계값으로 교체합니다.
 *
 * 입력 매개변수:
 *   inputs      : preprocess_tinyml_inputs()가 반환한 전처리 데이터
 *   net_status  : [출력] 네트워크 상태 (0: 정상, 1: 불안정)
 *   urgency     : [출력] 데이터 긴급도 (0: 정상, 1: 위험)
 *
 * QoS 선택 매트릭스 (기획서 §4 로직 동일):
 *   긴급도=1 AND 네트워크=1 → QoS 2 (최고 신뢰성, 4단계 핸드셰이크)
 *   긴급도=1 XOR 네트워크=1 → QoS 1 (신뢰성 보장, 2단계 핸드셰이크)
 *   긴급도=0 AND 네트워크=0 → QoS 0 (최대 저전력, 단발 전송)
 * ═══════════════════════════════════════════════════════════════════════════ */
static QoSLevel run_complex_agent_inference(const TinyMLInputs &inputs,
                                            uint8_t &net_status,
                                            uint8_t &urgency) {
  // ── 동적 임계값을 g_config에서 안전하게 스냅샷으로 읽기 ────────────────
  float   local_temp_thresh = 45.0f;  // 읽기 실패 시 기본값
  float   local_gas_thresh  = 20.0f;
  int8_t  local_rssi_thresh = -80;

  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    local_temp_thresh = g_config.temp_threshold_celsius;
    local_gas_thresh  = g_config.gas_threshold_kohm;
    local_rssi_thresh = g_config.rssi_threshold;
    xSemaphoreGive(g_config_mutex);
  } else {
    Serial.println("[추론] [경고] Mutex 획득 타임아웃 — 기본 임계값 사용");
  }

  // ── 데이터 긴급도 판정 (data_urgency) ─────────────────────────────────
  // 판정 기준 (기획서 §4 준수):
  //   - 온도가 임계값(기본 45°C) 초과 → 고온 위험
  //   - 온도가 10°C 미만 → 저온 이상 (동결 위험 등)
  //   - 가스 저항값이 임계값(기본 20kΩ) 미만 → 공기 오염 / 가스 누출 위험
  bool temp_danger = (inputs.temp > local_temp_thresh) || (inputs.temp < 10.0f);
  bool gas_danger  = (inputs.gas_kohm < local_gas_thresh);
  urgency = (temp_danger || gas_danger) ? 1 : 0;

  // ── 네트워크 상태 판정 (network_status) ────────────────────────────────
  // RSSI가 동적 임계값(기본 -80 dBm) 미만이면 불안정 선로로 판정
  net_status = (inputs.rssi < local_rssi_thresh) ? 1 : 0;

  Serial.printf("[추론] temp=%.1f°C(임계:%.1f) gas=%.1f kΩ(임계:%.1f) "
                "rssi=%d dBm(임계:%d) → 긴급도=%u 네트워크=%u\n",
                inputs.temp,      local_temp_thresh,
                inputs.gas_kohm,  local_gas_thresh,
                inputs.rssi,      local_rssi_thresh,
                urgency, net_status);

  // ── 기획서 §4 3단계 QoS 조건 매트릭스 ─────────────────────────────────
  if (urgency == 1 && net_status == 1) {
    // [QoS 2] 위험 데이터 + 통신 불안정 동시 발생 → 최고 신뢰성 강제 가동
    return QoSLevel::QoS2;
  } else if (urgency == 1 || net_status == 1) {
    // [QoS 1] 둘 중 하나만 불안정 → 신뢰성 보장 모드
    return QoSLevel::QoS1;
  } else {
    // [QoS 0] 둘 다 정상 → 최대 저전력 단발 전송
    return QoSLevel::QoS0;
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [기존 유지] 범용 UDP 패킷 수신 대기 함수
 * 특정 메시지 타입이 올 때까지 최대 2초 동안 폴링합니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
static bool wait_for_packet(MsgType expected_type, uint16_t target_msg_id) {
  unsigned long start_time = millis();
  while (millis() - start_time < 2000) { // 2.0초 타임아웃 (기획서 §3 준수)
    int packetSize = udp.parsePacket();
    if (packetSize >= (int)sizeof(Header)) {
      uint8_t buffer[128];
      udp.read(buffer, sizeof(buffer));
      Header *header = (Header *)buffer;

      // 수신 타입이 기대 타입과 일치하고 msg_id도 매칭되면 성공
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
  return false; // 2초 초과 — 타임아웃
}

/* ═══════════════════════════════════════════════════════════════════════════
 * setup() — Arduino 초기화 진입점
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  Serial.begin(115200);
  Serial.println("\n[부팅] Gingerbread ESP32-S3 펌웨어 시작");

  // ── FreeRTOS Mutex 생성 ────────────────────────────────────────────────
  // g_config 구조체에 대한 MQTT 콜백과 루프 태스크 간 동시 접근을 보호합니다.
  g_config_mutex = xSemaphoreCreateMutex();
  if (g_config_mutex == nullptr) {
    Serial.println("[부팅] [심각] Mutex 생성 실패! 시스템을 재시작합니다.");
    ESP.restart();
  }
  Serial.println("[부팅] FreeRTOS Mutex 생성 완료");

  // ── 배터리 ADC 핀 설정 ─────────────────────────────────────────────────
  // BATTERY 모드에서만 실제로 사용되지만 항상 입력 모드로 초기화합니다.
  analogSetAttenuation(ADC_11db); // ADC 감쇠 설정: 0~3.3V 전체 범위 측정
  pinMode(BATTERY_ADC_PIN, INPUT);
  Serial.printf("[부팅] 배터리 ADC 핀 초기화 완료 (GPIO %d)\n", BATTERY_ADC_PIN);

  // ── Wi-Fi 연결 ─────────────────────────────────────────────────────────
  Serial.printf("[부팅] Wi-Fi 연결 중... SSID: %s\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[부팅] Wi-Fi 연결 성공 — IP: %s\n",
                WiFi.localIP().toString().c_str());

  // ── UDP 소켓 초기화 (커스텀 MQTT-SN 프로토콜) ─────────────────────────
  udp.begin(UDP_SERVER_PORT);
  Serial.printf("[부팅] UDP 소켓 초기화 완료 (포트 %u)\n", UDP_SERVER_PORT);

  // ── MQTT 클라이언트 초기화 (설정 구독 전용) ───────────────────────────
  mqtt_client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT);
  mqtt_client.setCallback(on_mqtt_message); // 설정 수신 콜백 등록
  mqtt_client.setKeepAlive(60);             // 연결 유지 간격 (초)
  mqtt_connect_and_subscribe();             // 최초 연결 및 구독 시도

  // ── 커스텀 MQTT-SN CONNECT 패킷 전송 ──────────────────────────────────
  // 게이트웨이 서버에 세션 등록 (수면 주기 5초로 초기화)
  ConnectPacket conn_pkt;
  conn_pkt.header.length   = sizeof(ConnectPacket);
  conn_pkt.header.msg_type = MsgType::CONNECT;
  strncpy(conn_pkt.client_id, "ESP32-Client", sizeof(conn_pkt.client_id) - 1);
  conn_pkt.sleep_duration = 5;

  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&conn_pkt, sizeof(conn_pkt));
  udp.endPacket();
  Serial.println("[부팅] CONNECT 패킷 전송 완료 → 게이트웨이 세션 등록");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * loop() — Arduino 메인 루프
 *
 * 매 루프 사이클:
 *   1. MQTT 클라이언트 루프 실행 (설정 수신 폴링 + 재연결 관리)
 *   2. TinyML 입력 전처리 (전원 모드 분기 포함)
 *   3. 동적 임계값 기반 QoS 추론
 *   4. PUBLISH 패킷 조립 및 QoS 레벨별 핸드셰이크 전송
 *   5. DISCONNECT(Sleep) 패킷 전송 후 절전 대기
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  // ── 1단계: MQTT 클라이언트 루프 ────────────────────────────────────────
  // mqtt_client.loop()는 반드시 주기적으로 호출해야 합니다.
  // 내부적으로 PINGREQ/PINGRESP, 수신 메시지 콜백 디스패치를 처리합니다.
  if (!mqtt_client.connected()) {
    // 브로커 연결이 끊어진 경우 재연결을 시도합니다.
    Serial.println("[루프] MQTT 브로커 연결 끊김 — 재연결 시도");
    mqtt_connect_and_subscribe();
  }
  mqtt_client.loop(); // 수신 메시지가 있으면 on_mqtt_message() 콜백 실행

  // ── 2단계: TinyML 입력 전처리 (전원 모드 분기) ─────────────────────────
  // POWER_MODE에 따라 배터리 레벨 소스(ADC vs 대시보드 가상값)를 결정합니다.
  TinyMLInputs ml_inputs = preprocess_tinyml_inputs();

  // ── 3단계: 동적 임계값 기반 AI QoS 추론 ────────────────────────────────
  // 하드코딩 임계값 없이 g_config의 런타임 설정값으로 판정합니다.
  uint8_t  net_status   = 0;
  uint8_t  data_urgency = 0;
  QoSLevel selected_qos = run_complex_agent_inference(
      ml_inputs, net_status, data_urgency);

  Serial.printf("[루프] QoS 선택 결과: QoS%d\n", (int)selected_qos);

  // ── 4단계: PUBLISH 패킷 조립 ────────────────────────────────────────────
  PublishPacket pub_pkt;
  pub_pkt.header.length   = sizeof(PublishPacket);
  pub_pkt.header.msg_type = MsgType::PUBLISH;
  pub_pkt.msg_id          = current_msg_id++;
  pub_pkt.topic_id        = 1;
  pub_pkt.qos             = selected_qos;
  pub_pkt.network_status  = net_status;   // AI 추론 결과 비트필드
  pub_pkt.data_urgency    = data_urgency; // AI 추론 결과 비트필드

  // JSON 페이로드 조립:
  //   [신규] gas 필드 추가 — BME680 가스 저항값 (kΩ), 게이트웨이에서 임계값 비교에 활용
  //   [신규] battery 필드 추가 — 전원 모드 분기 결과 배터리 레벨
  snprintf(pub_pkt.payload, sizeof(pub_pkt.payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,\"battery\":%.0f}",
           ml_inputs.temp,
           ml_inputs.hum,
           ml_inputs.gas_kohm,      // [신규] 가스 저항값 (kΩ)
           ml_inputs.battery_pct);  // [신규] 전원 모드 분기 배터리 레벨

  // ── 5단계: QoS 레벨별 전송 시퀀스 (기존 로직 100% 보존) ─────────────
  int  retry_count        = 0;
  const int max_retries   = 3;        // 기획서 §3: 최대 3회 재전송 제한
  bool transaction_success = false;

  if (selected_qos == QoSLevel::QoS0) {
    // ── [QoS 0] 무확인 단발성 전송 — PUBACK 없이 즉시 Sleep 진입 ──────
    udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
    udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
    udp.endPacket();
    transaction_success = true;
    Serial.println("[QoS 0] 전송 완료 (응답 대기 없음)");

  } else if (selected_qos == QoSLevel::QoS1) {
    // ── [QoS 1] 2단계 핸드셰이크: PUBLISH → PUBACK ──────────────────────
    while (retry_count <= max_retries) {
      udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
      udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
      udp.endPacket();

      if (wait_for_packet(MsgType::PUBACK, pub_pkt.msg_id)) {
        transaction_success = true;
        Serial.printf("[QoS 1] 성공 — PUBACK 수신 (MsgID: %d)\n", pub_pkt.msg_id);
        break;
      }
      retry_count++;
      Serial.printf("[QoS 1] 타임아웃 — 재전송 시도 (%d/%d)\n",
                    retry_count, max_retries);
    }

  } else if (selected_qos == QoSLevel::QoS2) {
    // ── [QoS 2] 4단계 핸드셰이크: PUBLISH → PUBREC → PUBREL → PUBCOMP ──
    bool pubrec_received = false;

    // 단계 1: PUBLISH 송신 및 PUBREC 대기 (최대 3회 재전송)
    while (retry_count <= max_retries) {
      udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
      udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
      udp.endPacket();

      if (wait_for_packet(MsgType::PUBREC, pub_pkt.msg_id)) {
        pubrec_received = true;
        Serial.printf("[QoS 2] 단계1 완료 — PUBREC 수신 (MsgID: %d)\n",
                      pub_pkt.msg_id);
        break;
      }
      retry_count++;
      Serial.printf("[QoS 2] 단계1 타임아웃 — PUBLISH 재전송 (%d/%d)\n",
                    retry_count, max_retries);
    }

    // 단계 2: PUBREC 수신 확인 후 PUBREL 송신 및 PUBCOMP 대기
    if (pubrec_received) {
      retry_count = 0; // 단계 2용 재전송 카운터 초기화

      PubRelPacket rel_pkt;
      rel_pkt.header.length   = sizeof(PubRelPacket);
      rel_pkt.header.msg_type = MsgType::PUBREL;
      rel_pkt.msg_id          = pub_pkt.msg_id;

      while (retry_count <= max_retries) {
        udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
        udp.write((uint8_t *)&rel_pkt, sizeof(rel_pkt));
        udp.endPacket();

        if (wait_for_packet(MsgType::PUBCOMP, pub_pkt.msg_id)) {
          transaction_success = true;
          Serial.printf("[QoS 2] 최종 성공 — PUBCOMP 수신 (MsgID: %d)\n",
                        pub_pkt.msg_id);
          break;
        }
        retry_count++;
        Serial.printf("[QoS 2] 단계2 타임아웃 — PUBREL 재전송 (%d/%d)\n",
                      retry_count, max_retries);
      }
    }
  }

  // ── 전송 결과 로깅 ────────────────────────────────────────────────────
  if (!transaction_success) {
    Serial.printf("[통신 에러] QoS %d 전송 최종 실패 (MsgID: %d)\n",
                  (int)selected_qos, pub_pkt.msg_id - 1);
  }

  // ── 저전력 수면 동기화: DISCONNECT(Sleep) 패킷 전송 ─────────────────
  DisconnectPacket disc_pkt;
  disc_pkt.header.length    = sizeof(DisconnectPacket);
  disc_pkt.header.msg_type  = MsgType::DISCONNECT;
  disc_pkt.sleep_mode_flag  = 1; // Sleep 진입 플래그

  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&disc_pkt, sizeof(disc_pkt));
  udp.endPacket();
  Serial.println("[루프] DISCONNECT(Sleep) 전송 완료 — 5초 대기 후 재시작");

  // 5초 대기 (실제 구현에서는 esp_deep_sleep()로 교체하여 전력 절감)
  delay(5000);
}