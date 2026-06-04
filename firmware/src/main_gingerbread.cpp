/*
 * firmware/src/main_gingerbread.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 코드명 : Gingerbread  (Board 1 — 제안 시스템)
 * 타겟 하드웨어   : ESP32-S3 DevKitC-1
 * 역할           : 엣지 AI 센서 노드
 *                  환경 데이터(BME680) 수집 → 동적 QoS 선택 → UDP 전송
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │                       아키텍처 개요 (Architecture Overview)              │
 * ├─────────────────────────────────────────────────────────────────────────┤
 * │ 1. MQTT 설정 동기화 (라즈베리파이 5 → ESP32)                            │
 * │    - PubSubClient + ArduinoJson으로 "gingerbread/config" 토픽 구독      │
 * │    - FreeRTOS Mutex 보호 하의 SystemConfig 구조체에 실시간 설정 저장    │
 * │    - 동기화 항목: RSSI_THRESHOLD, PACKET_LOSS_LIMIT,                   │
 * │                  GAS_THRESHOLD_KOHM, TEMP_THRESHOLD_CELSIUS,          │
 * │                  POWER_MODE, CURRENT_BATTERY_LEVEL                    │
 * │                                                                        │
 * │ 2. 전원 모드 전처리 및 다중 노드 식별                                    │
 * │    - 클라이언트 고유 ID : "ESP32-Gingerbread" (Board 1 식별자)         │
 * │    - preprocess_inputs() 내 POWER_MODE 분기:                           │
 * │        EXTERNAL_5V → 대시보드 가상 배터리 레벨을 입력으로 사용          │
 * │        BATTERY     → BATTERY_ADC_PIN 실측 전압을 0~100%로 변환 사용   │
 * │                                                                        │
 * │ 3. 동적 임계값 매핑 및 확장 JSON 텔레메트리                              │
 * │    - 하드코딩 임계값 → Mutex 보호 g_config 런타임 값으로 완전 대체      │
 * │    - PUBLISH 페이로드: temp, hum, gas(저항값 kΩ), battery, power      │
 * │                                                                        │
 * │ 4. QoS 0 / 1 / 2 고충실도 핸드셰이크 루프                              │
 * │    - QoS 0 : 단발 무확인 전송 (fire-and-forget)                        │
 * │    - QoS 1 : PUBLISH → PUBACK 2단계 핸드셰이크 (재전송 최대 3회)      │
 * │    - QoS 2 : PUBLISH → PUBREC → PUBREL → PUBCOMP 4단계 핸드셰이크   │
 * └─────────────────────────────────────────────────────────────────────────┘
 *
 * 의존 라이브러리 (platformio.ini [env:board1_gingerbread] 참조):
 *   - bblanchon/ArduinoJson          @ ^7.0
 *   - knolleary/PubSubClient         @ ^2.8
 *   - adafruit/Adafruit BME680 Library @ ^2.0
 * ═══════════════════════════════════════════════════════════════════════════
 */

/* ─── 라이브러리 헤더 인클루드 ─────────────────────────────────────────── */
#include "protocol.h"               // 커스텀 MQTT-SN 프로토콜 구조체 정의 (include/)
#include <Arduino.h>                // Arduino 프레임워크 기본 함수
#include <Adafruit_BME680.h>        // BME680 온도/습도/기압/가스 복합 센서 드라이버
#include <ArduinoJson.h>            // MQTT "gingerbread/config" 페이로드 JSON 파싱
#include <PubSubClient.h>           // 표준 MQTT 브로커 통신 (설정 구독 전용)
#include <WiFi.h>                   // Wi-Fi 연결 및 RSSI 측정
#include <WiFiClient.h>             // PubSubClient TCP 연결에 필요한 네트워크 클라이언트
#include <WiFiUdp.h>                // 커스텀 MQTT-SN 프로토콜 UDP 전송 소켓
#include <freertos/FreeRTOS.h>      // FreeRTOS 커널 기본 헤더
#include <freertos/semphr.h>        // FreeRTOS Mutex (SemaphoreHandle_t) 지원
#include <Wire.h>                   // I2C 버스 (BME680 I2C 모드 연결용)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A] 네트워크 자격증명 및 서버 엔드포인트 설정
 *
 * 실제 배포 시 아래 상수들을 실제 환경 값으로 변경하세요.
 * ─────────────────────────────────────────────────────────────────────────
 * UDP_SERVER_IP  / UDP_SERVER_PORT  → 커스텀 MQTT-SN 게이트웨이 (라즈베리파이 5)
 * MQTT_BROKER_IP / MQTT_BROKER_PORT → 표준 MQTT 브로커 Mosquitto (동일 라즈베리파이)
 * ═══════════════════════════════════════════════════════════════════════════ */
static const char    *WIFI_SSID         = "YOUR_WIFI_SSID";     // Wi-Fi 네트워크 SSID
static const char    *WIFI_PASSWORD     = "YOUR_WIFI_PASSWORD";  // Wi-Fi 비밀번호

/* 커스텀 MQTT-SN (UDP) 게이트웨이 주소 및 포트
 * 라즈베리파이 5에서 실행 중인 Python 게이트웨이 서버의 수신 엔드포인트 */
static const char    *UDP_SERVER_IP     = "192.168.0.100";
static const uint16_t UDP_SERVER_PORT   = 5000;

/* 표준 MQTT 브로커 주소 및 포트 (Mosquitto)
 * "gingerbread/config" 토픽 설정 구독에만 사용됩니다. */
static const char    *MQTT_BROKER_IP    = "192.168.0.100";
static const uint16_t MQTT_BROKER_PORT  = 1883;

/* ESP32가 구독하는 설정 동기화 토픽
 * 라즈베리파이 게이트웨이 서버가 retain=true 플래그로 이 토픽에 설정을 발행합니다.
 * ESP32가 재부팅 후 재구독하면 즉시 마지막 설정을 수신할 수 있습니다. */
static const char    *MQTT_CONFIG_TOPIC = "gingerbread/config";

/* Board 1 다중 노드 식별자
 * MQTT 연결 패킷(CONNECT packet)과 커스텀 MQTT-SN ConnectPacket 양쪽에
 * 동일하게 사용되어 게이트웨이와 브로커 양측에서 Board 1을 정확히 식별합니다. */
static const char *BOARD1_CLIENT_ID = "ESP32-Gingerbread";

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 B] 하드웨어 핀 정의 및 ADC 파라미터
 *
 * BATTERY 모드에서만 BATTERY_ADC_PIN이 실제로 사용됩니다.
 * EXTERNAL_5V 모드에서는 ADC를 읽지 않으며 핀은 초기화만 됩니다.
 * BME680은 기본 I2C 주소 0x77에서 I2C 모드로 동작합니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
/* 배터리 전압 측정 ADC 핀 (ESP32-S3 GPIO 34 = ADC1_CH6)
 * 요구사항에 따라 핀 34로 지정합니다. */
static const int BATTERY_ADC_PIN = 34;

/* ADC 파라미터 (리튬 이온 배터리 기준, 전압 분배기 회로 사용 가정) */
static const float ADC_REF_VOLTAGE  = 3.3f;    // ESP32-S3 ADC 기준 전압 (V)
static const float ADC_MAX_VALUE    = 4095.0f;  // 12비트 ADC 최대 원시값
static const float BATTERY_VOLT_MAX = 4.2f;     // 리튬 이온 만충 전압 (V)
static const float BATTERY_VOLT_MIN = 3.0f;     // 리튬 이온 방전 한계 전압 (V)

/* BME680 I2C 기본 주소 (SDO 핀이 GND에 연결된 기본 설정) */
static const uint8_t BME680_I2C_ADDR = 0x77;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 C] SystemConfig 구조체 — MQTT 동기화 설정 저장소
 *
 * FreeRTOS Mutex(g_config_mutex)로 스레드 안전하게 보호됩니다.
 *
 * 갱신 경로:
 *   라즈베리파이 브로커 → MQTT "gingerbread/config" 토픽
 *   → on_mqtt_message() 콜백
 *   → Mutex 획득 후 g_config 안전 갱신
 *
 * 사용 경로:
 *   loop() → preprocess_inputs()          : POWER_MODE, CURRENT_BATTERY_LEVEL 참조
 *   loop() → run_agent_inference()        : 임계값(temp, gas, rssi) 참조
 * ═══════════════════════════════════════════════════════════════════════════ */
struct SystemConfig {
  /* ── NETWORK 섹션 ─────────────────────────────────────────────────────── */
  /* MQTT "gingerbread/config" → NETWORK 키 아래에서 수신됩니다. */
  int8_t rssi_threshold;      // RSSI 신호 강도 위험 임계값 (dBm, 기본값: -80)
  float  packet_loss_limit;   // 패킷 손실률 허용 상한 (%, 기본값: 5.0)

  /* ── ENVIRONMENT 섹션 ─────────────────────────────────────────────────── */
  /* MQTT "gingerbread/config" → ENVIRONMENT 키 아래에서 수신됩니다. */
  float gas_threshold_kohm;     // 가스 저항 위험 임계값 (kΩ, 기본값: 20.0)
                                // 이 값 미만이면 공기 오염 / 가스 누출 위험으로 판정
  float temp_threshold_celsius; // 온도 위험 임계값 (°C, 기본값: 45.0)
                                // 이 값 초과 또는 10°C 미만이면 긴급 상황으로 판정

  /* ── POWER_MANAGEMENT 섹션 ───────────────────────────────────────────── */
  /* MQTT "gingerbread/config" → POWER_MANAGEMENT 키 아래에서 수신됩니다. */
  char    power_mode[16];          // 전원 공급 방식 문자열
                                   //   "EXTERNAL_5V" : USB/어댑터 외부 전원 사용 중
                                   //   "BATTERY"     : 내장 배터리 사용 중
  uint8_t current_battery_level;   // 대시보드에서 설정하는 가상 배터리 레벨 (0~100 %)
                                   // EXTERNAL_5V 모드에서 배터리 입력으로 사용
};

/* 전역 설정 인스턴스 및 보호 Mutex
 * 전원 투입 직후 MQTT 설정 수신 전에도 안전하게 동작하도록 기본값으로 초기화합니다.
 * MQTT 연결 성공 후 on_mqtt_message()가 호출되면 실제 브로커 설정으로 덮어씁니다.
 * 멤버 순서: rssi_threshold, packet_loss_limit, gas_threshold_kohm,
 *            temp_threshold_celsius, power_mode, current_battery_level */
static SystemConfig g_config = {
  -80,           // rssi_threshold        : RSSI -80 dBm 미만 → 불안정 판정
  5.0f,          // packet_loss_limit     : 패킷 손실 5% 초과 → 재전송 정책 강화
  20.0f,         // gas_threshold_kohm    : 가스 저항 20 kΩ 미만 → 위험 판정
  45.0f,         // temp_threshold_celsius: 온도 45°C 초과 → 긴급 판정
  "EXTERNAL_5V", // power_mode            : 기본값 — 외부 전원 (ADC 미사용)
  100,           // current_battery_level : 기본값 — 대시보드 가상 배터리 만충 상태
};

/* FreeRTOS Mutex 핸들
 * g_config 구조체에 동시에 접근하는 두 컨텍스트를 보호합니다:
 *   - MQTT 콜백 컨텍스트 (on_mqtt_message): 설정 갱신 (쓰기)
 *   - Arduino loop() 컨텍스트            : 설정 참조 (읽기) */
static SemaphoreHandle_t g_config_mutex = nullptr;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 D] 센서 입력값 구조체
 *
 * preprocess_inputs()가 반환하는 정규화된 센서 데이터 묶음.
 * run_agent_inference()와 PUBLISH 페이로드 조립에 모두 사용됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
struct SensorInputs {
  float  temp;        // BME680 온도 (°C) — 임계값 비교 및 JSON 페이로드 포함
  float  hum;         // BME680 상대 습도 (%) — JSON 페이로드 포함
  float  gas_kohm;    // BME680 가스 저항값 (kΩ) — 임계값 비교 및 JSON 페이로드 포함
  float  power_mw;    // BME680 측정 소비 전력 추정값 (mW) — JSON 페이로드 포함
  float  battery_pct; // 배터리 입력 (0.0~100.0 %)
                      //   EXTERNAL_5V 모드 → 대시보드 CURRENT_BATTERY_LEVEL 값
                      //   BATTERY 모드     → ADC 실측 전압 변환값
  int8_t rssi;        // Wi-Fi RSSI (dBm) — 네트워크 상태 판정에 사용
};

/* ─── 통신 객체 인스턴스 ────────────────────────────────────────────────── */
static WiFiUDP      udp;                       // 커스텀 MQTT-SN 프로토콜 전용 UDP 소켓
static WiFiClient   wifi_client;               // PubSubClient의 TCP 연결 기반 클라이언트
static PubSubClient mqtt_client(wifi_client);  // 표준 MQTT 클라이언트 (설정 구독 전용)

/* BME680 센서 인스턴스 (I2C 버스 사용) */
static Adafruit_BME680 bme;

/* 커스텀 MQTT-SN 메시지 ID 카운터 (단조 증가, 오버플로 시 자연 순환) */
static uint16_t current_msg_id = 1;

/* BME680 초기화 성공 여부 플래그
 * 센서 미연결 시 시뮬레이션 값으로 폴백하여 코드 흐름을 유지합니다. */
static bool bme_initialized = false;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 E] BME680 센서 읽기 함수
 *
 * bme_initialized 플래그에 따라 실제 하드웨어 값 또는 시뮬레이션 값을 반환합니다.
 * 센서 미연결 환경에서도 펌웨어 로직 전체를 테스트할 수 있도록 설계됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */

/*
 * BME680 온도 측정 함수 (°C)
 * 하드웨어 사용 시: bme.temperature (측정 완료 후 자동 갱신)
 * 시뮬레이션 값: 32.5°C (기본 임계값 45°C 이하 → 정상 범위)
 */
static float read_temperature() {
  if (bme_initialized) {
    return bme.temperature; // BME680 실측 온도
  }
  return 32.5f; // 센서 미연결 시 시뮬레이션 값
}

/*
 * BME680 상대 습도 측정 함수 (%)
 * 시뮬레이션 값: 45.2%
 */
static float read_humidity() {
  if (bme_initialized) {
    return bme.humidity; // BME680 실측 습도
  }
  return 45.2f; // 센서 미연결 시 시뮬레이션 값
}

/*
 * BME680 가스 저항값 측정 함수 (kΩ)
 * PUBLISH 페이로드 gas 필드 및 동적 임계값 비교에 사용됩니다.
 * 시뮬레이션 값: 18.5 kΩ (기본 임계값 20 kΩ 미만 → 가스 위험 상황 시뮬레이션)
 */
static float read_gas_resistance_kohm() {
  if (bme_initialized) {
    /* BME680 가스 저항값: Ω 단위를 kΩ 단위로 변환하여 반환 */
    return bme.gas_resistance / 1000.0f;
  }
  return 18.5f; // 센서 미연결 시 시뮬레이션 값
}

/*
 * Wi-Fi RSSI 측정 함수 (dBm)
 * WiFi.RSSI()가 반환하는 실시간 신호 강도 값을 사용합니다.
 * 값이 낮을수록 (음수가 클수록) 신호가 약합니다.
 */
static int8_t get_wifi_rssi() {
  return (int8_t)WiFi.RSSI();
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 F] 배터리 ADC 측정 함수 (BATTERY 모드 전용)
 *
 * 하드웨어 ADC를 통해 실제 배터리 전압을 측정하고 0~100% 범위로 선형 변환합니다.
 * GPIO 34 (ADC1_CH6) 핀을 사용합니다.
 *
 * 회로 전제 조건:
 *   - 배터리(+)단에 전압 분배기 저항회로를 연결하여 ADC 입력 범위(0~3.3V)로 감압
 *   - 분배 비율에 따라 measured_voltage 계산식에 보정 계수를 곱해야 합니다.
 *     예) 1:1 분배기 → × 2.0, 2:3 분배기 → × 2.5
 *
 * 반환값: 배터리 잔량 (0.0 ~ 100.0 %)
 * ═══════════════════════════════════════════════════════════════════════════ */
static float read_battery_adc_pct() {
  /* ADC 원시값 읽기 (12비트 해상도: 0 ~ 4095)
   * BATTERY_ADC_PIN은 setup()에서 INPUT 모드로 초기화되어 있습니다. */
  int raw_adc = analogRead(BATTERY_ADC_PIN);

  /* ADC 원시값을 실제 측정 전압으로 변환
   * 전압 분배기 회로 사용 시 분배비를 곱해 실제 배터리 전압으로 보정하세요. */
  float measured_voltage = ((float)raw_adc / ADC_MAX_VALUE) * ADC_REF_VOLTAGE;

  /* 측정 전압을 배터리 잔량(%)으로 선형 보간 변환
   * 공식: pct = (V_measured - V_min) / (V_max - V_min) × 100 */
  float pct = (measured_voltage - BATTERY_VOLT_MIN)
              / (BATTERY_VOLT_MAX - BATTERY_VOLT_MIN) * 100.0f;

  /* 범위 클리핑: ADC 노이즈, 회로 오차로 인한 0% 미만 / 100% 초과 방지 */
  if (pct < 0.0f)   pct = 0.0f;
  if (pct > 100.0f) pct = 100.0f;

  return pct;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 G] MQTT 설정 수신 콜백 — on_mqtt_message()
 *
 * PubSubClient가 "gingerbread/config" 토픽 메시지를 수신할 때 자동 호출됩니다.
 * ArduinoJson으로 JSON 페이로드를 파싱하고, FreeRTOS Mutex를 획득하여
 * g_config 구조체를 스레드 안전하게 갱신합니다.
 *
 * 수신하는 JSON 페이로드 예시:
 * {
 *   "NETWORK": { "RSSI_THRESHOLD": -75, "PACKET_LOSS_LIMIT": 3.0 },
 *   "ENVIRONMENT": { "GAS_THRESHOLD_KOHM": 15.0, "TEMP_THRESHOLD_CELSIUS": 40.0 },
 *   "POWER_MANAGEMENT": { "POWER_MODE": "BATTERY", "CURRENT_BATTERY_LEVEL": 85 }
 * }
 *
 * 매개변수:
 *   topic   - 수신된 MQTT 토픽 문자열 (항상 "gingerbread/config"이어야 함)
 *   payload - JSON 페이로드 바이트 배열 (null 종단 없음, length로 길이 결정)
 *   length  - payload 바이트 수
 * ═══════════════════════════════════════════════════════════════════════════ */
static void on_mqtt_message(char *topic, byte *payload, unsigned int length) {
  Serial.printf("[MQTT설정] 메시지 수신 — 토픽: %s | 페이로드: %u bytes\n",
                topic, length);

  /* ArduinoJson v7 JsonDocument 생성
   * ArduinoJson v7에서 StaticJsonDocument<N>은 deprecated 됩니다.
   * JsonDocument로 교체: 동적 할당 방식이지만 동일한 API를 제공합니다. */
  JsonDocument doc;

  /* JSON 역직렬화: byte* 배열을 const char*로 캐스팅 후 길이 지정 파싱
   * null 종단이 없는 raw 바이트 배열을 안전하게 처리합니다. */
  DeserializationError parse_err = deserializeJson(
      doc, (const char *)payload, length);

  if (parse_err) {
    /* JSON 파싱 실패 시 기존 g_config 값을 유지하고 오류 원인만 로깅합니다.
     * 잘못된 페이로드로 인해 설정이 오염되는 것을 방지합니다. */
    Serial.printf("[MQTT설정] ⚠ JSON 파싱 실패: %s — 기존 설정 유지\n",
                  parse_err.c_str());
    return;
  }

  /* FreeRTOS Mutex 획득 후 g_config 안전 갱신
   * portMAX_DELAY: 다른 태스크가 Mutex를 반환할 때까지 무기한 블로킹
   * MQTT 콜백은 비교적 드물게 호출되므로 무기한 대기가 안전합니다. */
  if (xSemaphoreTake(g_config_mutex, portMAX_DELAY) == pdTRUE) {

    /* NETWORK 섹션 갱신
     * JSON에 해당 키가 없으면 is<>() 검사가 false를 반환하여 기존 값을 보존합니다. */
    if (doc["NETWORK"]["RSSI_THRESHOLD"].is<int>()) {
      g_config.rssi_threshold =
          (int8_t)doc["NETWORK"]["RSSI_THRESHOLD"].as<int>();
    }
    if (doc["NETWORK"]["PACKET_LOSS_LIMIT"].is<float>()) {
      g_config.packet_loss_limit =
          doc["NETWORK"]["PACKET_LOSS_LIMIT"].as<float>();
    }

    /* ENVIRONMENT 섹션 갱신 */
    if (doc["ENVIRONMENT"]["GAS_THRESHOLD_KOHM"].is<float>()) {
      g_config.gas_threshold_kohm =
          doc["ENVIRONMENT"]["GAS_THRESHOLD_KOHM"].as<float>();
    }
    if (doc["ENVIRONMENT"]["TEMP_THRESHOLD_CELSIUS"].is<float>()) {
      g_config.temp_threshold_celsius =
          doc["ENVIRONMENT"]["TEMP_THRESHOLD_CELSIUS"].as<float>();
    }

    /* POWER_MANAGEMENT 섹션 갱신 */
    if (doc["POWER_MANAGEMENT"]["POWER_MODE"].is<const char *>()) {
      /* strncpy로 버퍼 오버플로 방지 + 마지막 바이트를 '\0'으로 명시적 종단 */
      strncpy(g_config.power_mode,
              doc["POWER_MANAGEMENT"]["POWER_MODE"].as<const char *>(),
              sizeof(g_config.power_mode) - 1);
      g_config.power_mode[sizeof(g_config.power_mode) - 1] = '\0';
    }
    if (doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].is<int>()) {
      int lvl = doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].as<int>();
      /* 0~100 범위 클리핑: 대시보드에서 비정상 값이 전송될 경우를 방어합니다. */
      if (lvl < 0)   lvl = 0;
      if (lvl > 100) lvl = 100;
      g_config.current_battery_level = (uint8_t)lvl;
    }

    /* Mutex 반환: 임계 구역 종료 → loop() 컨텍스트가 다시 g_config에 접근 가능 */
    xSemaphoreGive(g_config_mutex);

    /* 갱신된 설정 전체를 시리얼 모니터에 출력하여 현장 디버깅을 지원합니다. */
    Serial.println("[MQTT설정] ✓ g_config 갱신 완료:");
    Serial.printf("  ├ [NETWORK] RSSI 임계값         : %d dBm\n",  g_config.rssi_threshold);
    Serial.printf("  ├ [NETWORK] 패킷 손실 상한       : %.1f %%\n", g_config.packet_loss_limit);
    Serial.printf("  ├ [ENV]     가스 저항 임계값      : %.1f kΩ\n", g_config.gas_threshold_kohm);
    Serial.printf("  ├ [ENV]     온도 임계값           : %.1f °C\n", g_config.temp_threshold_celsius);
    Serial.printf("  ├ [POWER]   전원 모드             : %s\n",     g_config.power_mode);
    Serial.printf("  └ [POWER]   가상 배터리 레벨      : %u %%\n", g_config.current_battery_level);

  } else {
    /* Mutex 획득 실패: 시스템 이상 상황 (데드락 등) — 설정 갱신을 포기합니다.
     * portMAX_DELAY를 사용했는데도 실패하면 RTOS 스케줄러 문제일 수 있습니다. */
    Serial.println("[MQTT설정] ✗ [오류] Mutex 획득 실패 — 설정 갱신 건너뜀");
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 H] MQTT 브로커 연결 및 설정 토픽 구독 함수 — mqtt_connect_and_subscribe()
 *
 * PubSubClient를 MQTT 브로커에 연결하고 "gingerbread/config" 토픽을
 * QoS 1로 구독합니다. 클라이언트 ID로 BOARD1_CLIENT_ID("ESP32-Gingerbread")를
 * 명시적으로 사용합니다.
 *
 * 이 함수는 setup()에서 최초 1회, 그리고 loop()에서 연결이 끊어질 때마다
 * 재연결을 위해 호출됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
static void mqtt_connect_and_subscribe() {
  Serial.printf("[MQTT설정] 브로커 연결 시도 — %s:%u (클라이언트 ID: %s)\n",
                MQTT_BROKER_IP, MQTT_BROKER_PORT, BOARD1_CLIENT_ID);

  /* PubSubClient MQTT CONNECT 패킷 전송
   * clean_session=true: 재연결 시 이전 구독 세션을 초기화하여 중복 구독 방지 */
  if (mqtt_client.connect(BOARD1_CLIENT_ID)) {
    Serial.printf("[MQTT설정] ✓ 브로커 연결 성공 (ID: %s)\n", BOARD1_CLIENT_ID);

    /* "gingerbread/config" 토픽 QoS 1 구독
     * retain=true로 발행된 마지막 설정 메시지가 구독 즉시 수신됩니다. */
    if (mqtt_client.subscribe(MQTT_CONFIG_TOPIC, 1)) {
      Serial.printf("[MQTT설정] ✓ 구독 성공 — 토픽: \"%s\" (QoS 1)\n",
                    MQTT_CONFIG_TOPIC);
    } else {
      Serial.printf("[MQTT설정] ⚠ 구독 실패 — 토픽: \"%s\"\n", MQTT_CONFIG_TOPIC);
    }
  } else {
    /* 연결 실패: PubSubClient 상태 코드로 원인을 진단합니다.
     * rc 코드 의미:
     *  -4 : MQTT_CONNECTION_TIMEOUT  (서버 응답 없음)
     *  -3 : MQTT_CONNECTION_LOST     (네트워크 단절)
     *  -2 : MQTT_CONNECT_FAILED      (연결 거부됨)
     *  -1 : MQTT_DISCONNECTED        (연결 끊김)
     *   1 : MQTT_CONNECT_BAD_PROTOCOL
     *   2 : MQTT_CONNECT_BAD_CLIENT_ID
     *   5 : MQTT_CONNECT_UNAUTHORIZED */
    Serial.printf("[MQTT설정] ⚠ 브로커 연결 실패 (rc=%d) — 다음 루프에서 재시도\n",
                  mqtt_client.state());
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 I] 센서 입력 전처리 함수 — preprocess_inputs()
 *
 * POWER_MODE에 따라 배터리 데이터 소스를 동적으로 분기합니다.
 *
 * ┌──────────────────┬────────────────────────────────────────────────────┐
 * │ POWER_MODE       │ battery_pct 소스                                   │
 * ├──────────────────┼────────────────────────────────────────────────────┤
 * │ "EXTERNAL_5V"    │ g_config.current_battery_level (대시보드 가상값)   │
 * │                  │ → ADC 읽기 없음, 시뮬레이션 모드                   │
 * ├──────────────────┼────────────────────────────────────────────────────┤
 * │ "BATTERY"        │ read_battery_adc_pct() (BATTERY_ADC_PIN 실측값)   │
 * │                  │ → 대시보드 가상값 완전 무시                         │
 * └──────────────────┴────────────────────────────────────────────────────┘
 *
 * BME680 측정 실행: 250ms 타임아웃 내에 완료해야 합니다.
 * 반환값: 전처리된 SensorInputs 구조체
 * ═══════════════════════════════════════════════════════════════════════════ */
static SensorInputs preprocess_inputs() {
  SensorInputs inputs;

  /* BME680 측정 트리거 및 완료 대기
   * beginReading()이 완료 예상 시각(ms)을 반환합니다.
   * 실패 시(0 반환) 시뮬레이션 값을 사용합니다. */
  if (bme_initialized) {
    unsigned long end_time = bme.beginReading();
    if (end_time == 0) {
      Serial.println("[전처리] ⚠ BME680 측정 시작 실패 — 시뮬레이션 값 사용");
      bme_initialized = false; // 오류 발생 후 폴백 상태로 전환
    } else {
      /* 측정 완료 시각까지 블로킹 대기 후 결과를 읽습니다. */
      delay(end_time - millis() > 0 ? end_time - millis() : 0);
      if (!bme.endReading()) {
        Serial.println("[전처리] ⚠ BME680 읽기 완료 실패 — 시뮬레이션 값 사용");
        bme_initialized = false;
      }
    }
  }

  /* 공통 센서 데이터 수집 (전원 모드와 무관하게 항상 실행) */
  inputs.temp     = read_temperature();         // BME680 온도 (°C)
  inputs.hum      = read_humidity();            // BME680 습도 (%)
  inputs.gas_kohm = read_gas_resistance_kohm(); // BME680 가스 저항 (kΩ)
  inputs.rssi     = get_wifi_rssi();            // Wi-Fi 신호 강도 (dBm)

  /* 전력 추정값: BME680 가스 히터 소비 전력 근사값
   * 실제 전력 계측은 Board 3(INA219)에서 수행합니다.
   * 여기서는 센서 샘플링 주기 기준 추정 전력값을 페이로드에 포함합니다. */
  inputs.power_mw = bme_initialized ? 3.3f * 0.012f * 1000.0f : 0.0f; // P = V × I (추정)

  /* Mutex 보호 하에 전원 모드 설정의 로컬 스냅샷 획득
   * 스냅샷 방식을 사용하면 이하 분기 로직에서 Mutex를 보유하지 않아도 됩니다.
   * 타임아웃(50ms): 짧게 설정하여 루프 지연을 최소화합니다. */
  char    mode_snapshot[16] = "EXTERNAL_5V"; // Mutex 실패 시 안전 기본값
  uint8_t virt_batt_snapshot = 100;          // Mutex 실패 시 안전 기본값 (만충)

  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    /* 임계 구역: g_config 멤버를 로컬 스택 변수로 복사 */
    strncpy(mode_snapshot, g_config.power_mode, sizeof(mode_snapshot) - 1);
    mode_snapshot[sizeof(mode_snapshot) - 1] = '\0'; // null 종단 보장
    virt_batt_snapshot = g_config.current_battery_level;
    xSemaphoreGive(g_config_mutex); // 즉시 반환하여 콜백 컨텍스트가 대기하지 않도록 함
  } else {
    /* Mutex 타임아웃: 시스템 부하가 높을 때 발생할 수 있습니다.
     * 기본값(EXTERNAL_5V, 100%)으로 폴백하여 데이터 전송을 중단하지 않습니다. */
    Serial.println("[전처리] ⚠ Mutex 획득 타임아웃(50ms) — 기본 EXTERNAL_5V 모드로 폴백");
  }

  /* POWER_MODE 분기: 배터리 레벨 데이터 소스 결정 */
  if (strcmp(mode_snapshot, "BATTERY") == 0) {
    /* BATTERY 모드 분기
     * 대시보드에서 내려온 CURRENT_BATTERY_LEVEL 가상값을 완전히 무시합니다.
     * BATTERY_ADC_PIN(GPIO 34)에 연결된 전압 분배기 회로에서 실제 배터리 전압을
     * ADC로 측정하여 0~100% 범위로 변환한 값을 사용합니다. */
    inputs.battery_pct = read_battery_adc_pct();
    Serial.printf("[전처리] 전원모드=BATTERY → ADC 실측 배터리: %.1f %%\n",
                  inputs.battery_pct);
  } else {
    /* EXTERNAL_5V 모드 분기 (기본값 포함)
     * 외부 전원(USB 5V / DC 어댑터)으로 동작 중이므로 물리 배터리 측정을 생략합니다.
     * 웹 대시보드 운영자가 MQTT로 설정한 CURRENT_BATTERY_LEVEL 가상값을
     * 배터리 특징 페이로드로 직접 사용합니다 (시뮬레이션 모드). */
    inputs.battery_pct = (float)virt_batt_snapshot;
    Serial.printf("[전처리] 전원모드=EXTERNAL_5V → 대시보드 가상 배터리: %.0f %%\n",
                  inputs.battery_pct);
  }

  return inputs; // 모든 필드가 채워진 SensorInputs 반환
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 J] 동적 임계값 기반 AI QoS 추론 — run_agent_inference()
 *
 * 기존 하드코딩 임계값을 완전히 제거하고,
 * Mutex 보호된 g_config의 런타임 동적 임계값으로 교체합니다.
 *
 * ┌───────────────┬──────────────────────────────────────────────────────┐
 * │ 긴급도(urgency)│ 판정 조건                                           │
 * ├───────────────┼──────────────────────────────────────────────────────┤
 * │    1 (긴급)   │ temp > temp_threshold_celsius (고온 위험)            │
 * │               │ OR temp < 10.0°C             (저온 이상 / 동결 위험) │
 * │               │ OR gas_kohm < gas_threshold  (가스 누출 위험)        │
 * ├───────────────┼──────────────────────────────────────────────────────┤
 * │    0 (정상)   │ 위 조건 모두 해당 없음                                │
 * └───────────────┴──────────────────────────────────────────────────────┘
 *
 * QoS 선택 매트릭스:
 *   긴급도=1 AND 네트워크=불안정 → QoS 2 (최고 신뢰성, 4단계 핸드셰이크)
 *   긴급도=1 XOR 네트워크=불안정 → QoS 1 (신뢰성 보장, 2단계 핸드셰이크)
 *   긴급도=0 AND 네트워크=안정   → QoS 0 (최대 저전력, 단발 무확인 전송)
 *
 * 매개변수:
 *   inputs     - preprocess_inputs()가 반환한 센서 데이터
 *   net_status - [출력 참조] 네트워크 안정성 (0: 정상, 1: 불안정)
 *   urgency    - [출력 참조] 데이터 긴급도 (0: 정상, 1: 긴급)
 *
 * 반환값: 선택된 QoSLevel (QoS0 / QoS1 / QoS2)
 * ═══════════════════════════════════════════════════════════════════════════ */
static QoSLevel run_agent_inference(const SensorInputs &inputs,
                                    uint8_t &net_status,
                                    uint8_t &urgency) {
  /* g_config에서 동적 임계값 스냅샷 획득
   * Mutex 획득 실패 시 안전한 기본값으로 폴백합니다. */
  float  local_temp_thresh = 45.0f;
  float  local_gas_thresh  = 20.0f;
  int8_t local_rssi_thresh = -80;

  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    /* 임계 구역: 세 임계값을 로컬 변수로 원자적으로 복사 */
    local_temp_thresh = g_config.temp_threshold_celsius;
    local_gas_thresh  = g_config.gas_threshold_kohm;
    local_rssi_thresh = g_config.rssi_threshold;
    xSemaphoreGive(g_config_mutex);
  } else {
    /* Mutex 타임아웃: 기본 임계값으로 추론을 계속합니다.
     * 데이터 전송이 중단되는 것보다 기본값으로 추론하는 것이 안전합니다. */
    Serial.println("[추론] ⚠ Mutex 타임아웃(50ms) — 기본 임계값으로 추론 진행");
  }

  /* 데이터 긴급도 판정: 동적 임계값 사용 */
  bool temp_danger = (inputs.temp > local_temp_thresh) // 고온: 임계값 초과
                  || (inputs.temp < 10.0f);            // 저온: 10°C 미만 (동결 / 냉각 이상)
  bool gas_danger  = (inputs.gas_kohm < local_gas_thresh); // 가스 오염: 저항 저하
  urgency = (temp_danger || gas_danger) ? 1 : 0;

  /* 네트워크 상태 판정
   * RSSI가 동적 임계값(기본 -80 dBm) 미만이면 불안정 선로로 판정합니다. */
  net_status = (inputs.rssi < local_rssi_thresh) ? 1 : 0;

  /* 추론 근거 및 결과를 시리얼 모니터에 상세 출력합니다. */
  Serial.printf("[추론] temp=%.1f°C(임계:%.1f) | gas=%.1f kΩ(임계:%.1f) | "
                "rssi=%d dBm(임계:%d) → 긴급도=%u | 네트워크상태=%u\n",
                inputs.temp,     local_temp_thresh,
                inputs.gas_kohm, local_gas_thresh,
                inputs.rssi,     local_rssi_thresh,
                urgency, net_status);

  /* QoS 선택 매트릭스 적용 */
  if (urgency == 1 && net_status == 1) {
    /* QoS 2: 긴급 데이터 + 불안정 통신 동시 발생
     * 4단계 핸드셰이크로 정확히 1회 전달을 보장합니다. */
    return QoSLevel::QoS2;
  } else if (urgency == 1 || net_status == 1) {
    /* QoS 1: 한 가지 조건만 불안정
     * PUBACK 수신 확인으로 최소 1회 이상 전달을 보장합니다. */
    return QoSLevel::QoS1;
  } else {
    /* QoS 0: 정상 상황 — 긴급도 없음 + 통신 안정
     * 응답 대기 없이 즉시 전송하여 전력 소모를 최소화합니다. */
    return QoSLevel::QoS0;
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 K] UDP 패킷 수신 대기 함수 — wait_for_packet()
 *
 * 특정 MsgType과 msg_id가 일치하는 응답 패킷이 올 때까지
 * 최대 2.0초 동안 폴링합니다.
 *
 * 매개변수:
 *   expected_type  - 기대하는 응답 MsgType (PUBACK / PUBREC / PUBCOMP)
 *   target_msg_id  - 일치해야 하는 메시지 ID (전송한 PUBLISH의 msg_id)
 *
 * 반환값:
 *   true  - 지정된 타임아웃 이내에 올바른 응답 수신
 *   false - 2.0초 초과 (타임아웃)
 * ═══════════════════════════════════════════════════════════════════════════ */
static bool wait_for_packet(MsgType expected_type, uint16_t target_msg_id) {
  unsigned long start_time = millis();

  /* 2,000ms(2.0초) 이내에 수신되지 않으면 타임아웃으로 판정합니다. */
  while (millis() - start_time < 2000) {
    int packetSize = udp.parsePacket();

    /* Header 크기 이상의 패킷이 수신된 경우에만 처리합니다. */
    if (packetSize >= (int)sizeof(Header)) {
      uint8_t buffer[128];
      udp.read(buffer, sizeof(buffer));
      Header *header = (Header *)buffer;

      /* 수신된 패킷의 MsgType이 기대 타입과 일치하는지 확인합니다. */
      if (header->msg_type == expected_type) {
        /* QoS 1: PUBACK의 msg_id가 전송 msg_id와 일치하는지 검증 */
        if (expected_type == MsgType::PUBACK &&
            ((PubAckPacket *)buffer)->msg_id == target_msg_id)
          return true;

        /* QoS 2 단계 1: PUBREC의 msg_id 검증 */
        if (expected_type == MsgType::PUBREC &&
            ((PubRecPacket *)buffer)->msg_id == target_msg_id)
          return true;

        /* QoS 2 단계 2: PUBCOMP의 msg_id 검증 */
        if (expected_type == MsgType::PUBCOMP &&
            ((PubCompPacket *)buffer)->msg_id == target_msg_id)
          return true;
      }
    }
    delay(10); // 10ms 폴링 간격: CPU 점유율과 응답성의 균형
  }

  return false; // 2.0초 초과: 타임아웃
}

/* ═══════════════════════════════════════════════════════════════════════════
 * setup() — Arduino 초기화 진입점
 *
 * 실행 순서:
 *   1. 시리얼 모니터 초기화
 *   2. FreeRTOS Mutex 생성 (g_config 보호용)
 *   3. BME680 I2C 센서 초기화
 *   4. 배터리 ADC 핀 초기화 (GPIO 34)
 *   5. Wi-Fi 연결 대기
 *   6. UDP 소켓 초기화 (커스텀 MQTT-SN)
 *   7. MQTT 클라이언트 초기화 및 설정 토픽 구독
 *   8. 커스텀 MQTT-SN CONNECT 패킷 전송 (Board 1 ID: "ESP32-Gingerbread")
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  /* 1단계: 시리얼 모니터 초기화 */
  Serial.begin(115200);
  delay(100); // USB-시리얼 초기화 안정화 대기
  Serial.println("\n╔══════════════════════════════════════════════════════╗");
  Serial.println("║   Board 1: Gingerbread — ESP32-S3 펌웨어 부팅 시작  ║");
  Serial.println("╚══════════════════════════════════════════════════════╝");

  /* 2단계: FreeRTOS Mutex 생성
   * g_config 구조체에 대한 동시 접근을 보호하는 Mutex를 생성합니다.
   * Mutex가 없으면 MQTT 콜백(쓰기)과 loop()(읽기)가 동시에 접근할 때
   * 데이터 레이스(data race)가 발생할 수 있습니다. */
  g_config_mutex = xSemaphoreCreateMutex();
  if (g_config_mutex == nullptr) {
    /* Mutex 생성 실패는 심각한 오류입니다. 즉시 재부팅하여 복구를 시도합니다. */
    Serial.println("[부팅] ✗ [심각] FreeRTOS Mutex 생성 실패! 5초 후 재시작합니다.");
    delay(5000);
    ESP.restart();
  }
  Serial.println("[부팅] ✓ FreeRTOS Mutex 생성 완료 (g_config 보호용)");

  /* 3단계: BME680 I2C 센서 초기화
   * 기본 I2C 버스(SDA=GPIO 8, SCL=GPIO 9)에 0x77 주소로 연결된 센서를 초기화합니다.
   * 연결되지 않은 경우 시뮬레이션 값으로 폴백합니다. */
  Wire.begin(); // 기본 I2C 버스 초기화 (SDA=8, SCL=9 on ESP32-S3)
  if (bme.begin(BME680_I2C_ADDR, &Wire)) {
    /* BME680 측정 설정: 온도, 습도, 기압, 가스 저항 모두 활성화 */
    bme.setTemperatureOversampling(BME680_OS_8X);   // 온도 오버샘플링 8배
    bme.setHumidityOversampling(BME680_OS_2X);      // 습도 오버샘플링 2배
    bme.setPressureOversampling(BME680_OS_4X);      // 기압 오버샘플링 4배
    bme.setIIRFilterSize(BME680_FILTER_SIZE_3);     // IIR 필터 크기 3단계 (노이즈 감쇠)
    bme.setGasHeater(320, 150);                     // 가스 히터: 320°C, 150ms 가열
    bme_initialized = true;
    Serial.println("[부팅] ✓ BME680 센서 초기화 완료 (I2C 0x77, 온도/습도/가스 활성화)");
  } else {
    /* 센서 미연결 또는 I2C 통신 오류: 시뮬레이션 모드로 진행 */
    bme_initialized = false;
    Serial.println("[부팅] ⚠ BME680 센서 미검출 — 시뮬레이션 값으로 동작합니다.");
  }

  /* 4단계: 배터리 ADC 핀 초기화 (GPIO 34)
   * BATTERY 모드에서만 실제로 ADC를 읽지만, 핀은 항상 초기화합니다.
   * ADC_11db 감쇠: 입력 전압 범위를 0~3.6V(실효 0~3.3V)로 설정합니다. */
  analogSetAttenuation(ADC_11db);
  pinMode(BATTERY_ADC_PIN, INPUT);
  Serial.printf("[부팅] ✓ 배터리 ADC 핀 초기화 완료 (GPIO %d, 감쇠: 11dB)\n",
                BATTERY_ADC_PIN);

  /* 5단계: Wi-Fi 연결 */
  Serial.printf("[부팅] Wi-Fi 연결 시도 중... SSID: \"%s\"\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  /* WL_CONNECTED 상태가 될 때까지 500ms 간격으로 폴링합니다. */
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[부팅] ✓ Wi-Fi 연결 성공 — 할당 IP: %s | RSSI: %d dBm\n",
                WiFi.localIP().toString().c_str(),
                (int8_t)WiFi.RSSI());

  /* 6단계: UDP 소켓 초기화 (커스텀 MQTT-SN 프로토콜 전용)
   * UDP_SERVER_PORT로 수신을 바인딩합니다.
   * 서버 응답(PUBACK, PUBREC, PUBCOMP)도 동일한 포트로 수신됩니다. */
  udp.begin(UDP_SERVER_PORT);
  Serial.printf("[부팅] ✓ UDP 소켓 초기화 완료 (포트 %u)\n", UDP_SERVER_PORT);

  /* 7단계: 표준 MQTT 클라이언트 초기화 및 설정 구독 */
  mqtt_client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT); // 브로커 엔드포인트 등록
  mqtt_client.setCallback(on_mqtt_message);                 // 설정 수신 콜백 바인딩
  mqtt_client.setKeepAlive(60);                             // PINGREQ 전송 간격 60초
  mqtt_connect_and_subscribe(); // 최초 MQTT 연결 및 "gingerbread/config" 구독

  /* 8단계: 커스텀 MQTT-SN CONNECT 패킷 전송
   * UDP 게이트웨이에 Board 1을 "ESP32-Gingerbread" ID로 세션 등록합니다. */
  ConnectPacket conn_pkt;
  conn_pkt.header.length   = sizeof(ConnectPacket);
  conn_pkt.header.msg_type = MsgType::CONNECT;

  /* Board 1 고유 식별자를 ConnectPacket에 명시적으로 삽입 */
  strncpy(conn_pkt.client_id, BOARD1_CLIENT_ID, sizeof(conn_pkt.client_id) - 1);
  conn_pkt.client_id[sizeof(conn_pkt.client_id) - 1] = '\0'; // null 종단 보장

  conn_pkt.sleep_duration = 5; // 절전 주기 5초 (게이트웨이 세션 유지 시간)

  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&conn_pkt, sizeof(conn_pkt));
  udp.endPacket();
  Serial.printf("[부팅] ✓ CONNECT 패킷 전송 → 게이트웨이 세션 등록 (ID: %s)\n",
                BOARD1_CLIENT_ID);

  Serial.println("[부팅] ══ 초기화 완료, 메인 루프 시작 ══\n");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * loop() — Arduino 메인 루프
 *
 * 매 루프 사이클 실행 순서:
 *   1. MQTT 클라이언트 루프 (설정 구독 유지 + 재연결 관리)
 *   2. BME680 센서 입력 전처리 (전원 모드 분기 포함)
 *   3. 동적 임계값 기반 AI QoS 추론
 *   4. PUBLISH 패킷 조립 (확장 JSON: temp, hum, gas, battery, power)
 *   5. QoS 레벨별 고충실도 핸드셰이크 전송 (QoS 0 / 1 / 2)
 *   6. 전송 결과 로깅
 *   7. DISCONNECT(Sleep) 패킷 전송 + 절전 대기 (5초)
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  /* ──────────────────────────────────────────────────────────────────────
   * 1단계: MQTT 클라이언트 루프 실행
   * mqtt_client.loop()은 반드시 주기적으로 호출해야 합니다.
   * 내부 동작:
   *   - 수신 메시지가 있으면 on_mqtt_message() 콜백 디스패치
   *   - PINGREQ/PINGRESP로 keepalive 유지
   *   - QoS 1 PUBACK / QoS 2 핸드셰이크 완료 처리
   * ────────────────────────────────────────────────────────────────────── */
  if (!mqtt_client.connected()) {
    /* 브로커 연결이 끊어진 경우 재연결을 시도합니다.
     * 재연결 성공 시 retain 메시지를 다시 수신하여 최신 설정이 복원됩니다. */
    Serial.println("[루프] ⚠ MQTT 브로커 연결 끊김 감지 — 재연결 시도");
    mqtt_connect_and_subscribe();
  }
  /* 수신 큐에 있는 메시지를 처리하고 on_mqtt_message() 콜백을 실행합니다. */
  mqtt_client.loop();

  /* ──────────────────────────────────────────────────────────────────────
   * 2단계: BME680 센서 입력 전처리 (전원 모드 분기)
   * POWER_MODE에 따라 battery_pct의 데이터 소스가 결정됩니다.
   *   EXTERNAL_5V → 대시보드 가상 배터리 레벨 (ADC 미사용)
   *   BATTERY     → BATTERY_ADC_PIN(GPIO 34) 실측 전압 (0~100% 변환)
   * ────────────────────────────────────────────────────────────────────── */
  SensorInputs ml_inputs = preprocess_inputs();

  /* ──────────────────────────────────────────────────────────────────────
   * 3단계: 동적 임계값 기반 AI QoS 추론
   * 하드코딩 임계값 없이 g_config 런타임 설정으로 QoS를 결정합니다.
   * ────────────────────────────────────────────────────────────────────── */
  uint8_t  net_status   = 0; // 네트워크 상태 판정 결과 (0: 안정, 1: 불안정)
  uint8_t  data_urgency = 0; // 데이터 긴급도 판정 결과 (0: 정상, 1: 긴급)
  QoSLevel selected_qos = run_agent_inference(ml_inputs, net_status, data_urgency);

  Serial.printf("[루프] ── QoS 선택 결과: QoS %d ──\n", (int)selected_qos);

  /* ──────────────────────────────────────────────────────────────────────
   * 4단계: PUBLISH 패킷 조립
   * 확장 JSON 페이로드:
   *   "temp"    : BME680 온도 (°C, 소수 2자리)
   *   "hum"     : BME680 습도 (%, 소수 2자리)
   *   "gas"     : BME680 가스 저항값 (kΩ, 소수 2자리) ← gas resistance 추가
   *   "battery" : 전원 모드 분기 배터리 레벨 (%, 정수)
   *   "power"   : 센서 소비 전력 추정값 (mW, 소수 1자리)
   * ────────────────────────────────────────────────────────────────────── */
  PublishPacket pub_pkt;
  pub_pkt.header.length   = sizeof(PublishPacket);
  pub_pkt.header.msg_type = MsgType::PUBLISH;
  pub_pkt.msg_id          = current_msg_id++;  // 단조 증가 메시지 ID
  pub_pkt.topic_id        = 1;                 // 사전 등록된 토픽 ID (게이트웨이와 협의)
  pub_pkt.qos             = selected_qos;      // AI 추론으로 결정된 QoS 레벨
  pub_pkt.network_status  = net_status;        // AI 추론 결과 — 게이트웨이 모니터링 활용
  pub_pkt.data_urgency    = data_urgency;      // AI 추론 결과 — 게이트웨이 알람 발동 활용

  /* JSON 페이로드 포맷 (확장 버전): temp, hum, gas, battery, power 포함 */
  snprintf(pub_pkt.payload, sizeof(pub_pkt.payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,\"battery\":%.0f,\"power\":%.1f}",
           ml_inputs.temp,        // BME680 온도
           ml_inputs.hum,         // BME680 습도
           ml_inputs.gas_kohm,    // BME680 가스 저항 (kΩ) — 신규 추가
           ml_inputs.battery_pct, // 전원 모드 분기 배터리 레벨 — 신규 추가
           ml_inputs.power_mw);   // 센서 소비 전력 추정값 (mW) — 신규 추가

  Serial.printf("[루프] PUBLISH 페이로드 (MsgID=%u): %s\n",
                pub_pkt.msg_id, pub_pkt.payload);

  /* ──────────────────────────────────────────────────────────────────────
   * 5단계: QoS 레벨별 고충실도 핸드셰이크 전송 시퀀스
   * QoS 0 / 1 / 2 핸드셰이크 로직을 원형 그대로 보존합니다.
   * ────────────────────────────────────────────────────────────────────── */
  int       retry_count        = 0;
  const int max_retries        = 3;    // 최대 3회 재전송 제한
  bool      transaction_success = false;

  if (selected_qos == QoSLevel::QoS0) {
    /* [QoS 0] 무확인 단발성 전송 — Fire and Forget
     * PUBACK를 기다리지 않고 즉시 Sleep 진입합니다.
     * 응답 대기 없으므로 전력 소모가 가장 낮습니다.
     * 적용 조건: 긴급도=0 AND 네트워크=안정 */
    udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
    udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
    udp.endPacket();
    transaction_success = true;
    Serial.println("[QoS 0] ✓ 단발 전송 완료 (응답 대기 없음)");

  } else if (selected_qos == QoSLevel::QoS1) {
    /* [QoS 1] 2단계 핸드셰이크: PUBLISH → PUBACK
     * PUBACK 수신으로 최소 1회 이상 전달을 보장합니다.
     * 적용 조건: 긴급도=1 XOR 네트워크=불안정 */
    while (retry_count <= max_retries) {
      /* PUBLISH 패킷 UDP 전송 */
      udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
      udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
      udp.endPacket();

      /* PUBACK 수신 대기 (최대 2.0초) */
      if (wait_for_packet(MsgType::PUBACK, pub_pkt.msg_id)) {
        transaction_success = true;
        Serial.printf("[QoS 1] ✓ 성공 — PUBACK 수신 확인 (MsgID: %u)\n",
                      pub_pkt.msg_id);
        break; // 핸드셰이크 완료: 재전송 루프 종료
      }

      retry_count++;
      Serial.printf("[QoS 1] ⚠ 타임아웃 — PUBLISH 재전송 (%d/%d)\n",
                    retry_count, max_retries);
    }

  } else if (selected_qos == QoSLevel::QoS2) {
    /* [QoS 2] 4단계 핸드셰이크: PUBLISH → PUBREC → PUBREL → PUBCOMP
     * 정확히 1회 전달(Exactly Once Delivery)을 보장합니다.
     * 적용 조건: 긴급도=1 AND 네트워크=불안정 (최악의 조건)
     *
     * 단계 1: PUBLISH 전송 + PUBREC 대기 (수신 확인)
     * 단계 2: PUBREL 전송 + PUBCOMP 대기 (완료 확인) */
    bool pubrec_received = false;

    /* QoS 2 단계 1: PUBLISH 전송 및 PUBREC 수신 대기 */
    while (retry_count <= max_retries) {
      udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
      udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
      udp.endPacket();

      /* PUBREC 수신 대기 (최대 2.0초) */
      if (wait_for_packet(MsgType::PUBREC, pub_pkt.msg_id)) {
        pubrec_received = true;
        Serial.printf("[QoS 2] 단계1 ✓ — PUBREC 수신 확인 (MsgID: %u)\n",
                      pub_pkt.msg_id);
        break; // PUBREC 수신 성공: 단계 2로 진행
      }

      retry_count++;
      Serial.printf("[QoS 2] 단계1 ⚠ 타임아웃 — PUBLISH 재전송 (%d/%d)\n",
                    retry_count, max_retries);
    }

    /* QoS 2 단계 2: PUBREC 수신 후 PUBREL 전송 및 PUBCOMP 대기 */
    if (pubrec_received) {
      retry_count = 0; // 단계 2 전용 재전송 카운터 초기화

      /* PUBREL 패킷 조립 (동일한 msg_id 사용) */
      PubRelPacket rel_pkt;
      rel_pkt.header.length   = sizeof(PubRelPacket);
      rel_pkt.header.msg_type = MsgType::PUBREL;
      rel_pkt.msg_id          = pub_pkt.msg_id; // PUBLISH와 동일한 msg_id

      while (retry_count <= max_retries) {
        udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
        udp.write((uint8_t *)&rel_pkt, sizeof(rel_pkt));
        udp.endPacket();

        /* PUBCOMP 수신 대기 (최대 2.0초) — 게이트웨이가 처리 완료를 통지 */
        if (wait_for_packet(MsgType::PUBCOMP, pub_pkt.msg_id)) {
          transaction_success = true;
          Serial.printf("[QoS 2] 단계2 ✓ — PUBCOMP 수신, 4단계 핸드셰이크 최종 완료 (MsgID: %u)\n",
                        pub_pkt.msg_id);
          break; // 핸드셰이크 완료
        }

        retry_count++;
        Serial.printf("[QoS 2] 단계2 ⚠ 타임아웃 — PUBREL 재전송 (%d/%d)\n",
                      retry_count, max_retries);
      }
    } else {
      /* PUBREC를 최대 재전송 횟수까지도 수신하지 못한 경우
       * 게이트웨이 다운, 심각한 네트워크 단절 등의 상황입니다. */
      Serial.printf("[QoS 2] ✗ 단계1 최종 실패 — PUBREC 미수신 (MsgID: %u)\n",
                    pub_pkt.msg_id);
    }
  }

  /* ──────────────────────────────────────────────────────────────────────
   * 6단계: 전송 결과 최종 로깅
   * ────────────────────────────────────────────────────────────────────── */
  if (transaction_success) {
    Serial.printf("[루프] ══ QoS %d 전송 트랜잭션 성공 (MsgID: %u) ══\n",
                  (int)selected_qos, pub_pkt.msg_id);
  } else {
    /* 최대 재전송 횟수 초과 후에도 확인 응답이 없는 경우
     * 다음 루프에서 새로운 msg_id로 재시도됩니다. */
    Serial.printf("[루프] ✗ QoS %d 전송 최종 실패 — 재전송 한도(%d회) 초과 (MsgID: %u)\n",
                  (int)selected_qos, max_retries, pub_pkt.msg_id);
  }

  /* ──────────────────────────────────────────────────────────────────────
   * 7단계: 저전력 Sleep 동기화 — DISCONNECT(Sleep) 패킷 전송
   * 게이트웨이에 수면 진입을 알려 불필요한 패킷 수신을 차단합니다.
   * ────────────────────────────────────────────────────────────────────── */
  DisconnectPacket disc_pkt;
  disc_pkt.header.length   = sizeof(DisconnectPacket);
  disc_pkt.header.msg_type = MsgType::DISCONNECT;
  disc_pkt.sleep_mode_flag = 1; // Sleep 진입 플래그: 게이트웨이가 수신 버퍼링 시작

  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&disc_pkt, sizeof(disc_pkt));
  udp.endPacket();
  Serial.println("[루프] DISCONNECT(Sleep) 전송 완료 — 5초 후 다음 사이클 시작\n");

  /* 5초 대기 후 다음 측정/전송 사이클을 시작합니다.
   * 배포 환경에서는 delay()를 esp_deep_sleep_start()로 교체하면
   * 수면 중 전력 소모를 ~10μA 수준으로 절감할 수 있습니다.
   * 예시) esp_sleep_enable_timer_wakeup(5ULL * 1000000ULL);
   *       esp_deep_sleep_start(); */
  delay(5000);
}
