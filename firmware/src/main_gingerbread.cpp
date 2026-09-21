/*
 * firmware/src/main_gingerbread.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 코드명 : Gingerbread  (Board 1 — 제안 시스템)
 * 타겟 하드웨어   : ESP32-S3 DevKitC-1
 * 역할           : TinyML 엣지 AI 센서 노드
 *                  환경 데이터 수집 → MLP 신경망 추론 → 동적 QoS 선택 → UDP 전송
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │                    아키텍처 개요 (Architecture Overview)                 │
 * ├─────────────────────────────────────────────────────────────────────────┤
 * │ 1. MQTT 설정 동기화 (The Baseline Provider)                              │
 * │    - PubSubClient + ArduinoJson으로 "gingerbread/config" 토픽 구독       │
 * │    - FreeRTOS Mutex 보호 하의 SystemConfig 구조체에 실시간 설정 저장      │
 * │    - 동기화 항목: RSSI_THRESHOLD, PACKET_LOSS_LIMIT,                    │
 * │                  GAS_THRESHOLD_KOHM, TEMP_THRESHOLD_CELSIUS,           │
 * │                  POWER_MODE, CURRENT_BATTERY_LEVEL                     │
 * │                                                                         │
 * │ 2. 이중 전원 모드 전처리 및 다중 노드 식별                                 │
 * │    - 클라이언트 고유 ID: "ESP32-Gingerbread" (Board 1 식별자)             │
 * │    - preprocess_sensor_inputs() 내 POWER_MODE 분기:                     │
 * │        EXTERNAL_5V → 대시보드 가상 배터리 레벨을 AI 입력으로 사용         │
 * │        BATTERY     → BATTERY_ADC_PIN 실측 전압을 0~100%로 변환하여 사용  │
 * │                                                                         │
 * │ 3. 고충실도 MLP 신경망 추론 엔진 (TinyML Edge AI)                         │
 * │    - 외부 TensorFlow Lite Micro 런타임 없이 순수 C++ 구현                 │
 * │    - 은닉층(5노드, ReLU) + 출력층(1노드, Sigmoid) 피드포워드 구조         │
 * │    - run_neural_network_inference(temp, hum, gas_kohm, pres_hpa, gas_ratio) 반환값:          │
 * │        0.0 ~ 1.0 사이의 연속적 위험 확률 점수                             │
 * │                                                                         │
 * │ 4. 신경망 연속 출력 기반 동적 QoS 스위칭                                   │
 * │    - 점수 >= 0.75: CRITICAL → QoS 2 (절대 신뢰성, 4단계 핸드셰이크)      │
 * │    - 점수 >= 0.40: WARNING  → QoS 1 (2단계 핸드셰이크, 재전송 최대 3회)  │
 * │    - 점수  < 0.40: NORMAL   → QoS 0 (무확인 단발 전송, 최대 저전력)      │
 * │                                                                         │
 * │ 5. 커스텀 MQTT-SN 프로토콜 (WiFiUDP 기반 실제 텔레메트리)                  │
 * │    - QoS 0 / 1 / 2 고충실도 핸드셰이크 루프 (기존 100% 보존)              │
 * │    - QoS 0 : 단발 무확인 전송                                             │
 * │    - QoS 1 : PUBLISH → PUBACK 2단계 핸드셰이크 (재전송 최대 3회)         │
 * │    - QoS 2 : PUBLISH → PUBREC → PUBREL → PUBCOMP 4단계 핸드셰이크      │
 * └─────────────────────────────────────────────────────────────────────────┘
 *
 * 의존 라이브러리 (platformio.ini 참조):
 *   - knolleary/PubSubClient  @ ^2.8   : MQTT 브로커 통신 (설정 구독 전용)
 *   - bblanchon/ArduinoJson   @ ^7.0   : JSON 파싱
 *   - WiFiUdp                          : 커스텀 MQTT-SN UDP 텔레메트리 전송
 *   - freertos/semphr.h                : FreeRTOS Mutex (스레드 안전 설정 보호)
 *
 * [2026-06 리팩토링] 소프트웨어 정의 전력 추정 메트릭 추가
 *   하드웨어 INA219 Board 3를 제거하고, IEEE Access 2024 기반 SW 추정 모델로
 *   전환합니다. 펌웨어는 다음 5가지 성능 지표를 수집하여 전송합니다:
 *   1. RTT (rtt_ms)          : PUBLISH 전송~PUBACK/PUBREC 수신 왕복 시간 (ms)
 *   2. retry_count           : 패킷 재전송 횟수 (타임아웃 시 증가)
 *   3. sleep_mode_ratio      : 전체 경과 시간 대비 Sleep 시간 비율 (0.0~1.0)
 *   4. packet_count          : 누적 전송 패킷 수
 *   5. total_bytes           : 누적 전송 바이트 수
 * ═══════════════════════════════════════════════════════════════════════════
 */

/* ─── 라이브러리 헤더 인클루드 ──────────────────────────────────────────── */
#include "protocol.h"           // 커스텀 MQTT-SN 프로토콜 구조체 정의 (include/)
#include <Arduino.h>            // Arduino 프레임워크 기본 함수
#include <ArduinoJson.h>        // MQTT "gingerbread/config" 페이로드 JSON 파싱
#include <PubSubClient.h>       // 표준 MQTT 브로커 통신 (설정 구독 전용)
#include <WiFi.h>               // Wi-Fi 연결 및 RSSI 측정
#include <WiFiClient.h>         // PubSubClient TCP 연결에 필요한 네트워크 클라이언트
#include <WiFiUdp.h>            // 커스텀 MQTT-SN 프로토콜 UDP 전송 소켓
#include <freertos/FreeRTOS.h>  // FreeRTOS 커널 기본 헤더
#include <freertos/semphr.h>    // FreeRTOS Mutex (SemaphoreHandle_t) 지원
#include <math.h>               // expf() 함수 (Sigmoid 활성화 함수 연산용)
#include <Wire.h>               // BME680 I2C 통신
#include <Adafruit_BME680.h>    // BME680 환경 센서 드라이버
#include "mlp_inference.h"      // 위험 점수 MLP 순전파 + 가중치 (include/, ml_model/train.py가 생성)
#include "gas_baseline.h"       // 가스 저항의 기준값 대비 비율 추적기 (include/)
#include "net_congestion.h"     // 네트워크 혼잡도(손실률/지연) 추적기 (include/)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A] 네트워크 자격증명 및 서버 엔드포인트 설정
 *
 * 실제 배포 시 아래 상수들을 실제 환경 값으로 변경하세요.
 * ─────────────────────────────────────────────────────────────────────────
 * UDP_SERVER_IP  / UDP_SERVER_PORT  → 커스텀 MQTT-SN 게이트웨이 (라즈베리파이 5)
 * MQTT_BROKER_IP / MQTT_BROKER_PORT → 표준 MQTT 브로커 Mosquitto (동일 라즈베리파이)
 * ═══════════════════════════════════════════════════════════════════════════ */
static const char    *WIFI_SSID        = "YOUR_WIFI_SSID";     // Wi-Fi 네트워크 SSID
static const char    *WIFI_PASSWORD    = "YOUR_WIFI_PASSWORD"; // Wi-Fi 비밀번호

// 커스텀 MQTT-SN (UDP) 게이트웨이 주소 및 포트
// 라즈베리파이 5에서 실행 중인 Python 게이트웨이 서버의 수신 엔드포인트
static const char    *UDP_SERVER_IP    = "10.61.35.14";
static const uint16_t UDP_SERVER_PORT  = 5000;

// ── QoS 레벨별 전송 계층 (프로젝트 명세) ─────────────────────────────────────
//   QoS 0·1                   → UDP  (오버헤드가 적은 저전력 모드)
//   TCP_MIN_QOS 이상의 상위 QoS → TCP  (수신 보장이 필수인 신뢰성 모드)
// 같은 게이트웨이(UDP_SERVER_IP)의 TCP_SERVER_PORT로 전송합니다. 게이트웨이의
// backend/config.py GINGERBREAD_TCP_PORT와 일치해야 합니다.
// TCP_MIN_QOS를 3으로 올리면 모든 QoS가 UDP로 처리됩니다(QoS 2는 기존 4단계 UDP 핸드셰이크).
static const uint16_t TCP_SERVER_PORT  = 5001;
static const uint8_t  TCP_MIN_QOS      = 2;

// 표준 MQTT 브로커 주소 및 포트 (Mosquitto)
// "gingerbread/config" 토픽 설정 구독에만 사용됩니다.
// UDP 게이트웨이와 동일한 라즈베리파이에서 실행된다고 가정합니다.
static const char    *MQTT_BROKER_IP   = "10.61.35.14";
static const uint16_t MQTT_BROKER_PORT = 1883;

// ESP32가 구독하는 설정 동기화 토픽
// 라즈베리파이 게이트웨이 서버가 retain=true 플래그로 이 토픽에 설정을 발행합니다.
// ESP32가 재부팅 후 재구독하면 즉시 마지막 설정을 수신할 수 있습니다.
static const char    *MQTT_CONFIG_TOPIC = "gingerbread/config";

/* ─── Board 1 다중 노드 식별자 ──────────────────────────────────────────── */
// Board 1의 고유 클라이언트 ID
// MQTT 연결 패킷(CONNECT packet)과 커스텀 MQTT-SN ConnectPacket 양쪽에
// 동일하게 사용되어 게이트웨이와 브로커 양측에서 Board 1을 정확히 식별합니다.
static const char *BOARD1_CLIENT_ID = "ESP32-Gingerbread";

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 B] 하드웨어 핀 정의 및 ADC 파라미터
 *
 * BATTERY 모드에서만 BATTERY_ADC_PIN이 실제로 사용됩니다.
 * EXTERNAL_5V 모드에서는 ADC를 읽지 않으며 핀은 초기화만 됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
// 배터리 전압 측정 ADC 핀 (ESP32-S3: GPIO 34)
// 실제 회로도에 맞게 핀 번호를 수정하세요.
static const int BATTERY_ADC_PIN = 34;

// ADC 파라미터 (리튬 이온 배터리 기준, 전압 분배기 회로 사용 가정)
static const float ADC_REF_VOLTAGE  = 3.3f;   // ESP32-S3 ADC 기준 전압 (V)
static const float ADC_MAX_VALUE    = 4095.0f; // 12비트 ADC 최대 원시값
static const float BATTERY_VOLT_MAX = 4.2f;    // 리튬 이온 만충 전압 (V)
static const float BATTERY_VOLT_MIN = 3.0f;    // 리튬 이온 방전 한계 전압 (V)

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
 *   loop() → preprocess_sensor_inputs()    : POWER_MODE, CURRENT_BATTERY_LEVEL 참조
 *   loop() → run_agent_inference()         : RSSI 임계값 참조 (네트워크 상태 판정)
 * ═══════════════════════════════════════════════════════════════════════════ */
struct SystemConfig {
  // ── NETWORK 섹션 ──────────────────────────────────────────────────────────
  // MQTT "gingerbread/config" → NETWORK 키 아래에서 수신됩니다.
  int8_t rssi_threshold;     // RSSI 신호 강도 위험 임계값 (dBm, 기본값: -80)
  float  packet_loss_limit;  // 패킷 손실률 허용 상한 (%, 기본값: 5.0)

  // ── ENVIRONMENT 섹션 ──────────────────────────────────────────────────────
  // MQTT "gingerbread/config" → ENVIRONMENT 키 아래에서 수신됩니다.
  // 주의: 아래 두 임계값은 신경망 추론이 아닌 로깅/모니터링 목적으로 보존됩니다.
  float gas_threshold_kohm;      // 가스 저항 위험 임계값 (kΩ, 기본값: 20.0)
  float temp_threshold_celsius;  // 온도 위험 임계값 (°C, 기본값: 45.0)

  // ── POWER_MANAGEMENT 섹션 ──────────────────────────────────────────────────
  // MQTT "gingerbread/config" → POWER_MANAGEMENT 키 아래에서 수신됩니다.
  char    power_mode[16];         // 전원 공급 방식 문자열
                                  //   "EXTERNAL_5V" : USB/어댑터 외부 전원 사용 중
                                  //   "BATTERY"     : 내장 배터리 사용 중
  uint8_t current_battery_level; // 대시보드에서 설정하는 가상 배터리 레벨 (0~100 %)
                                  // EXTERNAL_5V 모드에서 AI 배터리 입력으로 사용
};

/* ─── 전역 설정 인스턴스 및 보호 Mutex ──────────────────────────────────── */
// 전원 투입 직후 MQTT 설정 수신 전에도 안전하게 동작하도록 기본값으로 초기화합니다.
// MQTT 연결 성공 후 on_mqtt_message()가 호출되면 실제 브로커 설정으로 덮어씁니다.
// 멤버 순서: rssi_threshold, packet_loss_limit, gas_threshold_kohm,
//            temp_threshold_celsius, power_mode, current_battery_level
static SystemConfig g_config = {
  -80,           // rssi_threshold       : RSSI -80 dBm 미만 → 불안정 판정
  5.0f,          // packet_loss_limit    : 패킷 손실 5% 초과 → 재전송 정책 강화
  20.0f,         // gas_threshold_kohm   : 가스 저항 20 kΩ 미만 → 위험 참고값
  45.0f,         // temp_threshold_celsius: 온도 45°C 초과 → 긴급 참고값
  "EXTERNAL_5V", // power_mode           : 기본값 — 외부 전원 (ADC 미사용)
  100,           // current_battery_level: 기본값 — 대시보드 가상 배터리 만충 상태
};

// FreeRTOS Mutex 핸들
// g_config 구조체에 동시에 접근하는 두 컨텍스트를 보호합니다:
//   - MQTT 콜백 컨텍스트 (on_mqtt_message): 설정 갱신 (쓰기)
//   - Arduino loop() 컨텍스트           : 설정 참조 (읽기)
static SemaphoreHandle_t g_config_mutex = nullptr;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 D] 센서 입력값 구조체
 *
 * preprocess_sensor_inputs()가 반환하는 센서 데이터 묶음.
 * run_neural_network_inference() 호출과 PUBLISH 페이로드 조립에 모두 사용됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
struct SensorInputs {
  float  temp;        // BME680 온도 (°C) — 신경망 입력 특징 1
  float  hum;         // BME680 상대 습도 (%) — 신경망 입력 특징 2
  float  gas_kohm;    // BME680 가스 저항값 (kΩ) — 신경망 입력 특징 3
  float  pres_hpa;    // BME680 기압 (hPa) — 신경망 입력 특징 4
  float  gas_ratio;   // 가스 저항 / 기준값 (0~1, gas_baseline.h) — 신경망 입력 특징 5
  float  battery_pct; // 배터리 레벨 (0.0~100.0 %)
                      //   EXTERNAL_5V 모드 → 대시보드 CURRENT_BATTERY_LEVEL 값
                      //   BATTERY 모드     → ADC 실측 전압 변환값
  int8_t rssi;        // Wi-Fi RSSI (dBm) — 네트워크 상태 판정에 사용
};

/* ─── 통신 객체 인스턴스 ─────────────────────────────────────────────────── */
static WiFiUDP      udp;                       // 커스텀 MQTT-SN 프로토콜 전용 UDP 소켓
static WiFiClient   wifi_client;               // PubSubClient의 TCP 연결 기반 클라이언트
static PubSubClient mqtt_client(wifi_client);  // 표준 MQTT 클라이언트 (설정 구독 전용)
#define BME_SDA_PIN 8
#define BME_SCL_PIN 9
static uint8_t bme680_i2c_address = 0x76;
static Adafruit_BME680 bme680;

// 커스텀 MQTT-SN 메시지 ID 카운터 (단조 증가, 오버플로 시 자연 순환)
static uint16_t current_msg_id = 1;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 E-EXT] 소프트웨어 정의 전력 추정 성능 메트릭 전역 카운터
 *
 * IEEE Access 2024 (DOI: 10.1109/ACCESS.2024.3523864) 기반 전력 추정을 위해
 * 아래 전역 변수를 사용하여 루프 사이클 간 누적 상태를 추적합니다.
 *
 * 갱신 위치:
 *   loop() 내 각 QoS 핸드셰이크 블록 → rtt_ms, retry_count, total_bytes
 *   loop() 진입/종료 타이밍            → g_total_active_ms, g_total_sleep_ms
 * ═══════════════════════════════════════════════════════════════════════════ */
static uint32_t g_packet_count    = 0;  // 누적 PUBLISH 전송 패킷 수
static uint32_t g_total_bytes     = 0;  // 누적 전송 바이트 수 (PublishPacket 기준)
static uint32_t g_total_active_ms = 0;  // 누적 활성(awake) 경과 시간 (ms)
static uint32_t g_total_sleep_ms  = 0;  // 누적 Sleep/delay 경과 시간 (ms)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 E] 하드웨어 센서 읽기 함수
 *
 * 현재는 BME680 드라이버 연동 전 테스트 시뮬레이션 값을 반환합니다.
 * 실제 하드웨어 통합 시 Adafruit_BME680 라이브러리 호출로 교체하세요.
 * ═══════════════════════════════════════════════════════════════════════════ */

/*
 * BME680 온도 측정 함수 (°C)
 * 시뮬레이션: 약 32.5°C 근방의 랜덤 변동값 반환 (정상 구간)
 * 실제 구현: return bme.temperature;
 */
static float read_temperature() {
  return bme680.temperature;
}

/*
 * BME680 상대 습도 측정 함수 (%)
 * 시뮬레이션: 약 45.2% 근방의 랜덤 변동값 반환
 * 실제 구현: return bme.humidity;
 */
static float read_humidity() {
  return bme680.humidity;
}

/*
 * BME680 가스 저항값 측정 함수 (kΩ)
 * PUBLISH 페이로드 gas 필드 및 신경망 입력 특징 3에 사용됩니다.
 * 시뮬레이션: 약 18.5 kΩ 근방의 랜덤 변동값 반환
 * 실제 구현: return bme.gas_resistance / 1000.0f;  // Ω → kΩ 변환
 */
static float read_gas_resistance_kohm() {
  return bme680.gas_resistance / 1000.0f;
}

/*
 * BME680 기압 측정 함수 (hPa)
 * Adafruit_BME680의 pressure는 Pa 단위이므로 100으로 나눠 hPa로 변환합니다.
 * 신경망 입력 특징 4에 사용됩니다. (ml_model/collect.py가 저장하는 pres_hpa 컬럼과 같은 단위)
 */
static float read_pressure_hpa() {
  return bme680.pressure / 100.0f;
}

/*
 * 가스 저항 기준값 추적 상태 (SRAM 8바이트). 부팅하면 초기화되며 처음 GAS_BASELINE_WARMUP_SAMPLES 샘플
 * 동안은 비율이 1.0으로 고정됩니다 (gas_baseline.h 참조). loop()당 한 번 갱신됩니다.
 */
static GasBaseline g_gas_baseline = { 0.0f, 0 };

/*
 * 네트워크 혼잡도 추적 상태 (net_congestion.h). ACK를 받는 전송(QoS 1, TCP)의 결과로 갱신되며,
 * 혼잡하면 QoS를 최소 1로 올립니다. QoS 0이 길어지면 관측을 위해 QoS 1 프로브를 한 번 보냅니다.
 * g_probe_this_cycle : 이번 사이클이 프로브(그렇지 않았다면 QoS 0이었을 사이클)인가
 * g_loss_limit      : 이번 사이클에 읽은 PACKET_LOSS_LIMIT(%) 스냅샷 (혼잡 판정 임계값)
 */
static NetCongestion g_net = { 0.0f, 0.0f, 0.0f, 0, false, 0, 0 };
static bool          g_probe_this_cycle = false;
static float         g_loss_limit = 5.0f;

static bool read_bme680() {
  return bme680.performReading();
}

static size_t publish_packet_size(const PublishPacket &packet) {
  return sizeof(Header) + sizeof(packet.msg_id) + sizeof(packet.qos)
       + sizeof(packet.topic_id) + strlen(packet.payload) + 1;
}

/*
 * Wi-Fi RSSI 측정 함수 (dBm)
 * WiFi.RSSI()가 반환하는 실시간 값 사용
 * 값이 낮을수록 (음수가 클수록) 신호가 약합니다.
 */
static int8_t get_wifi_rssi() {
  return (int8_t)WiFi.RSSI();
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 F] 배터리 ADC 측정 함수 (BATTERY 모드 전용)
 *
 * BATTERY 모드 분기: 하드웨어 ADC를 통해 실제 배터리 전압을 측정하고
 * 0~100% 범위로 선형 변환합니다.
 *
 * 회로 전제 조건:
 *   - 배터리(+)단에 전압 분배기 저항회로를 연결하여 ADC 입력 범위(0~3.3V)로 감압
 *   - 분배 비율에 따라 아래 measured_voltage 계산식에 보정 계수를 곱해야 합니다.
 *     예) 1:1 분배기 → × 2.0, 2:3 분배기 → × 2.5
 *
 * 반환값: 배터리 잔량 (0.0 ~ 100.0 %)
 * ═══════════════════════════════════════════════════════════════════════════ */
static float read_battery_adc_pct() {
  // ADC 원시값 읽기 (12비트 해상도: 0 ~ 4095)
  // BATTERY_ADC_PIN은 setup()에서 INPUT 모드로 초기화되어 있습니다.
  int raw_adc = analogRead(BATTERY_ADC_PIN);

  // ADC 원시값을 실제 측정 전압으로 변환
  // 주의: 전압 분배기 회로를 사용하는 경우 분배비를 곱해 실제 배터리 전압으로 보정하세요.
  // 예시) float measured_voltage = ((float)raw_adc / ADC_MAX_VALUE) * ADC_REF_VOLTAGE * 2.0f;
  float measured_voltage = ((float)raw_adc / ADC_MAX_VALUE) * ADC_REF_VOLTAGE;

  // 측정 전압을 배터리 잔량(%)으로 선형 보간 변환
  //   공식: pct = (V_measured - V_min) / (V_max - V_min) × 100
  float pct = (measured_voltage - BATTERY_VOLT_MIN)
              / (BATTERY_VOLT_MAX - BATTERY_VOLT_MIN) * 100.0f;

  // 범위 클리핑: ADC 노이즈, 회로 오차로 인한 0% 미만 / 100% 초과 방지
  if (pct < 0.0f)   pct = 0.0f;
  if (pct > 100.0f) pct = 100.0f;

  return pct;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 G] ★ TinyML 고충실도 MLP 신경망 추론 엔진 ★
 *          run_neural_network_inference()
 *
 * 외부 TensorFlow Lite Micro 런타임을 사용하지 않고 순수 C++로 구현한
 * 2층 피드포워드 MLP(Multi-Layer Perceptron) 추론 함수입니다.
 * 무거운 런타임 오버헤드 없이 ESP32-S3 메모리(520KB SRAM) 내에서
 * 효율적으로 동작합니다.
 *
 * 아키텍처:
 *   입력층 (5 노드): 온도, 습도, 가스 저항, 기압 (BME680 측정값 전부) + 가스 비율 (기준값 대비, 파생값)
 *       ↓ 표준 스케일링 정규화 전처리
 *   은닉층 (5 노드): w_hidden[5][5] 가중치 + b_hidden[5] 편향 + ReLU 활성화
 *       ↓ 벡터화 행렬 곱셈 연산
 *   출력층 (1 노드): w_output[5] 가중치 + b_output 편향 + Sigmoid 활성화
 *       ↓
 *   최종 출력: 위험 확률 점수 (danger_probability, 0.0 ~ 1.0)
 *
 * ─── [Algorithm Complexity & Memory Footprint] ────────────────────────────
 *
 * Time Complexity  : O(H × I + O × H)
 *   H = 5 hidden nodes, I = 5 inputs, O = 1 output
 *   → 총 5×5 + 1×5 = 30 MAC(Multiply-Accumulate) 연산/추론 사이클
 *   → 표준 스케일링 전처리: 5 연산 (추가)
 *   → 총 실효 연산 ≈ 35 (단정밀도 부동소수점)
 *   → 가스 기준값 추적: 샘플당 비교/곱/나눗셈 수 회 (gas_baseline.h)
 *
 * Space Complexity : O(H×I + H + O×H + O) = O(36) floats
 *   w_hidden[5][5] = 25 floats (ROM/Flash 상수)
 *   b_hidden[5]    =  5 floats (ROM/Flash 상수)
 *   w_output[5]    =  5 floats (ROM/Flash 상수)
 *   b_output       =  1 float  (ROM/Flash 상수)
 *   정규화 상수 MEAN[5]+STD[5] = 10 floats, QoS 임계값 2 floats (ROM/Flash 상수)
 *   x[5], raw[5], z, z_out, h 스택 지역변수 = ~13 floats (SRAM 스택, 추정)
 *   합계: 가중치 36 floats × 4 bytes = 144 bytes Flash 상수
 *         + 정규화·임계값 12 floats = 48 bytes → 총 192 bytes Flash 상수
 *         + 약 52 bytes SRAM 스택 (함수 호출 중, 추정)
 *         + 가스 기준값 상태 8 bytes SRAM (정적, g_gas_baseline)
 *
 * [Measured Build Metrics — 2026-06-19]
 *   ※ 아래 실측값은 이 MLP(3입력)와 이후 변경(TCP 전송, 4입력 등) 이전 빌드의 것입니다.
 *     논문에 넣기 전에 `pio run -e board1_gingerbread`로 다시 측정하세요.
 *   > pio run -e board1_gingerbread --verbose 2>&1 | findstr /i "ram flash sketch"
 *
 *   Flash (Sketch): 728,649 bytes / 3,342,336 bytes  = 21.8%
 *   Global RAM    :  45,148 bytes /   327,680 bytes  = 13.8%
 *
 *   TinyML MLP 개별 기여분 (전체 대비 근사치):
 *     w_hidden[5][5], b_hidden[5], w_output[5], b_output = 36 floats × 4B = 144B Flash
 *     스택 임시 변수 (x, raw, z 등)                      = 약 52B SRAM (호출 중, 추정)
 *
 * [TinyML Inference Timing Estimate]
 *   ESP32-S3 Xtensa LX7 @ 240 MHz with hardware FPU:
 *   35 연산 × ~5 ns ≈ < 1 μs per inference (추정치 — 실측한 값이 아님)
 *   expf() Sigmoid: ~200 ns (하드웨어 FPU 가속)
 *   총 추론 시간 예상: < 5 μs (측정 오버헤드 제외)
 *
 *   [Complexity Column 평가 지표 요약 (논문 Table 삽입용)]
 *   | 지표                   | 값                         |
 *   |------------------------|----------------------------|
 *   | MLP 구조               | 5-5-1 (Input-Hidden-Output)|
 *   | 추론 연산              | ~35 float 연산/cycle       |
 *   | 가중치 저장 (Flash)    | 144 bytes (+상수 48 bytes) |
 *   | 런타임 SRAM            | ~52 bytes 스택 + 8 bytes 상태|
 *   | 추론 지연 (240 MHz)    | < 5 μs (FPU 가속)          |
 *   | 외부 런타임 라이브러리 | 없음 (순수 C++ 구현)       |
 * ─────────────────────────────────────────────────────────────────────────
 *
 * 가중치 출처:
 *   include/mlp_weights.h (ml_model/train.py가 직접 수집한 데이터로 학습해 생성).
 *   MLP_WEIGHTS_TRAINED가 0이면 학습되지 않은 임시값이며 부팅 시 경고가 출력됩니다.
 *   (이전 주석은 "화재/가스 데이터셋으로 사전 학습"이라고 했으나 사실이 아니었습니다.
 *    값은 다른 AI가 만든 숫자였고 학습 데이터와 스크립트는 존재하지 않았습니다.)
 *
 * 매개변수:
 *   temp      - BME680 온도 (°C)
 *   hum       - BME680 상대 습도 (%)
 *   gas_kohm  - BME680 가스 저항값 (kΩ)
 *   pres_hpa  - BME680 기압 (hPa)
 *   gas_ratio - 가스 저항 / 기준값 (0~1, gas_baseline.h)
 *
 * 반환값:
 *   위험 확률 점수 (0.0 = 완전 안전 ~ 1.0 = 최고 위험)
 * ═══════════════════════════════════════════════════════════════════════════ */
float run_neural_network_inference(float temp, float hum, float gas_kohm, float pres_hpa, float gas_ratio) {
  // 순전파(표준화 → 은닉층 ReLU → 출력층 Sigmoid)는 include/mlp_inference.h로 분리했습니다.
  // 표준 C++만 쓰므로 PC에서도 같은 코드를 컴파일해 파이썬 학습 모델과 출력을 대조할 수 있습니다
  // (ml_model/verify_export.py). 가중치·정규화 상수는 include/mlp_weights.h에서 옵니다.
  return mlp_forward(temp, hum, gas_kohm, pres_hpa, gas_ratio);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 H] MQTT 설정 수신 콜백 — on_mqtt_message()
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

  // ── ArduinoJson v7 JsonDocument 생성 ─────────────────────────────────────
  // ArduinoJson v7에서 StaticJsonDocument<N>은 deprecated 되었습니다.
  // JsonDocument로 교체: 동적 할당 방식이지만 동일한 API를 제공합니다.
  // gingerbread/config JSON 전체 구조(6개 필드)를 파싱하기에 충분합니다.
  JsonDocument doc;

  // JSON 역직렬화: byte* 배열을 const char*로 캐스팅 후 길이 지정 파싱
  // null 종단이 없는 raw 바이트 배열을 안전하게 처리합니다.
  DeserializationError parse_err = deserializeJson(
      doc, (const char *)payload, length);

  if (parse_err) {
    // JSON 파싱 실패 시 기존 g_config 값을 유지하고 오류 원인만 로깅합니다.
    // 잘못된 페이로드로 인해 설정이 오염되는 것을 방지합니다.
    Serial.printf("[MQTT설정] ⚠ JSON 파싱 실패: %s — 기존 설정 유지\n",
                  parse_err.c_str());
    return;
  }

  // ── FreeRTOS Mutex 획득 후 g_config 안전 갱신 ────────────────────────────
  // portMAX_DELAY: 다른 태스크가 Mutex를 반환할 때까지 무기한 블로킹
  // MQTT 콜백은 비교적 드물게 호출되므로 무기한 대기가 안전합니다.
  if (xSemaphoreTake(g_config_mutex, portMAX_DELAY) == pdTRUE) {

    // ── NETWORK 섹션 갱신 ──────────────────────────────────────────────────
    // JSON에 해당 키가 없으면 is<>() 검사가 false를 반환하여 기존 값을 보존합니다.
    if (doc["NETWORK"]["RSSI_THRESHOLD"].is<int>()) {
      g_config.rssi_threshold =
          (int8_t)doc["NETWORK"]["RSSI_THRESHOLD"].as<int>();
    }
    if (doc["NETWORK"]["PACKET_LOSS_LIMIT"].is<float>()) {
      g_config.packet_loss_limit =
          doc["NETWORK"]["PACKET_LOSS_LIMIT"].as<float>();
    }

    // ── ENVIRONMENT 섹션 갱신 ─────────────────────────────────────────────
    // 이 값들은 신경망 추론과 무관하게 모니터링/로깅 목적으로 보존됩니다.
    if (doc["ENVIRONMENT"]["GAS_THRESHOLD_KOHM"].is<float>()) {
      g_config.gas_threshold_kohm =
          doc["ENVIRONMENT"]["GAS_THRESHOLD_KOHM"].as<float>();
    }
    if (doc["ENVIRONMENT"]["TEMP_THRESHOLD_CELSIUS"].is<float>()) {
      g_config.temp_threshold_celsius =
          doc["ENVIRONMENT"]["TEMP_THRESHOLD_CELSIUS"].as<float>();
    }

    // ── POWER_MANAGEMENT 섹션 갱신 ──────────────────────────────────────────
    if (doc["POWER_MANAGEMENT"]["POWER_MODE"].is<const char *>()) {
      // strncpy로 버퍼 오버플로 방지 + 마지막 바이트를 '\0'으로 명시적 종단
      strncpy(g_config.power_mode,
              doc["POWER_MANAGEMENT"]["POWER_MODE"].as<const char *>(),
              sizeof(g_config.power_mode) - 1);
      g_config.power_mode[sizeof(g_config.power_mode) - 1] = '\0';
    }
    if (doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].is<int>()) {
      int lvl = doc["POWER_MANAGEMENT"]["CURRENT_BATTERY_LEVEL"].as<int>();
      // 0~100 범위 클리핑: 대시보드에서 비정상 값이 전송될 경우를 방어합니다.
      if (lvl < 0)   lvl = 0;
      if (lvl > 100) lvl = 100;
      g_config.current_battery_level = (uint8_t)lvl;
    }

    // ── Mutex 반환 ────────────────────────────────────────────────────────────
    // 임계 구역 종료: loop() 컨텍스트가 다시 g_config에 접근할 수 있습니다.
    xSemaphoreGive(g_config_mutex);

    // 갱신된 설정 전체를 시리얼 모니터에 출력하여 현장 디버깅을 지원합니다.
    Serial.println("[MQTT설정] ✓ g_config 갱신 완료:");
    Serial.printf("  ├ [NETWORK] RSSI 임계값          : %d dBm\n",  g_config.rssi_threshold);
    Serial.printf("  ├ [NETWORK] 패킷 손실 상한        : %.1f %%\n", g_config.packet_loss_limit);
    Serial.printf("  ├ [ENV]     가스 저항 참고 임계값  : %.1f kΩ\n", g_config.gas_threshold_kohm);
    Serial.printf("  ├ [ENV]     온도 참고 임계값       : %.1f °C\n", g_config.temp_threshold_celsius);
    Serial.printf("  ├ [POWER]   전원 모드              : %s\n",     g_config.power_mode);
    Serial.printf("  └ [POWER]   가상 배터리 레벨        : %u %%\n", g_config.current_battery_level);

  } else {
    // Mutex 획득 실패: 시스템 이상 상황 (데드락 등) — 설정 갱신을 포기합니다.
    // portMAX_DELAY를 사용했는데도 실패하면 RTOS 스케줄러 문제일 수 있습니다.
    Serial.println("[MQTT설정] ✗ [오류] Mutex 획득 실패 — 설정 갱신 건너뜀");
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 I] MQTT 브로커 연결 및 설정 토픽 구독 함수 — mqtt_connect_and_subscribe()
 *
 * PubSubClient를 MQTT 브로커에 연결하고
 * "gingerbread/config" 토픽을 QoS 1로 구독합니다.
 *
 * 클라이언트 ID로 BOARD1_CLIENT_ID("ESP32-Gingerbread")를 명시적으로 사용하여
 * 브로커 세션 로그에서 Board 1을 다른 노드와 명확히 구별합니다.
 *
 * 이 함수는 setup()에서 최초 1회, 그리고 loop()에서 연결이 끊어질 때마다
 * 재연결을 위해 호출됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
static void mqtt_connect_and_subscribe() {
  Serial.printf("[MQTT설정] 브로커 연결 시도 — %s:%u (클라이언트 ID: %s)\n",
                MQTT_BROKER_IP, MQTT_BROKER_PORT, BOARD1_CLIENT_ID);

  // PubSubClient MQTT CONNECT 패킷 전송
  // clean_session=true: 재연결 시 이전 구독 세션을 초기화하여 중복 구독 방지
  if (mqtt_client.connect(BOARD1_CLIENT_ID)) {
    Serial.printf("[MQTT설정] ✓ 브로커 연결 성공 (ID: %s)\n", BOARD1_CLIENT_ID);

    // "gingerbread/config" 토픽 QoS 1 구독
    // retain=true로 발행된 마지막 설정 메시지가 구독 즉시 수신됩니다.
    // 이를 통해 ESP32 재부팅 후에도 최신 설정이 자동 복원됩니다.
    if (mqtt_client.subscribe(MQTT_CONFIG_TOPIC, 1)) {
      Serial.printf("[MQTT설정] ✓ 구독 성공 — 토픽: \"%s\" (QoS 1)\n",
                    MQTT_CONFIG_TOPIC);
    } else {
      Serial.printf("[MQTT설정] ⚠ 구독 실패 — 토픽: \"%s\"\n", MQTT_CONFIG_TOPIC);
    }
  } else {
    // 연결 실패: PubSubClient 상태 코드로 원인을 진단합니다.
    // rc 코드 의미:
    //  -4 : MQTT_CONNECTION_TIMEOUT  (서버 응답 없음)
    //  -3 : MQTT_CONNECTION_LOST     (네트워크 단절)
    //  -2 : MQTT_CONNECT_FAILED      (연결 거부됨)
    //  -1 : MQTT_DISCONNECTED        (연결 끊김)
    //   1 : MQTT_CONNECT_BAD_PROTOCOL
    //   2 : MQTT_CONNECT_BAD_CLIENT_ID
    //   5 : MQTT_CONNECT_UNAUTHORIZED
    Serial.printf("[MQTT설정] ⚠ 브로커 연결 실패 (rc=%d) — 다음 루프에서 재시도\n",
                  mqtt_client.state());
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 J] 이중 전원 모드 센서 입력 전처리 — preprocess_sensor_inputs()
 *
 * POWER_MODE에 따라 배터리 레벨 특징(feature)의 데이터 소스를 동적으로 분기합니다.
 *
 * ┌──────────────────┬────────────────────────────────────────────────────┐
 * │ POWER_MODE       │ battery_pct 소스                                   │
 * ├──────────────────┼────────────────────────────────────────────────────┤
 * │ "EXTERNAL_5V"    │ g_config.current_battery_level (대시보드 가상값)   │
 * │                  │ → ADC 읽기 없음, 시뮬레이션 모드                   │
 * ├──────────────────┼────────────────────────────────────────────────────┤
 * │ "BATTERY"        │ read_battery_adc_pct() (BATTERY_ADC_PIN 실측값)   │
 * │                  │ → 대시보드 가상값 완전 무시, GPIO 34 ADC 사용      │
 * └──────────────────┴────────────────────────────────────────────────────┘
 *
 * 공통 센서(temp, hum, gas_kohm, rssi)는 POWER_MODE와 무관하게 항상 수집됩니다.
 *
 * 반환값: 전처리된 SensorInputs 구조체
 * ═══════════════════════════════════════════════════════════════════════════ */
static SensorInputs preprocess_sensor_inputs() {
  SensorInputs inputs;

  // ── 공통 센서 데이터 수집 (전원 모드와 무관하게 항상 실행) ──────────────
  if (!read_bme680()) {
    Serial.println("[BME680] 측정 실패");
  }
  inputs.temp     = read_temperature();         // BME680 온도 (°C)
  inputs.hum      = read_humidity();            // BME680 습도 (%)
  inputs.gas_kohm = read_gas_resistance_kohm(); // BME680 가스 저항 (kΩ)
  inputs.pres_hpa = read_pressure_hpa();        // BME680 기압 (hPa)
  inputs.gas_ratio = gas_baseline_update(g_gas_baseline, inputs.gas_kohm); // 가스 저항 / 기준값
  inputs.rssi     = get_wifi_rssi();            // Wi-Fi 신호 강도 (dBm)

  // ── Mutex 보호 하에 전원 모드 설정의 로컬 스냅샷 획득 ───────────────────
  // 스냅샷 방식을 사용하면 이하 분기 로직에서 Mutex를 보유하지 않아도 됩니다.
  // 타임아웃(50ms): 짧게 설정하여 루프 지연을 최소화합니다.
  // 획득 실패 시에도 기본값으로 안전하게 동작합니다.
  char    mode_snapshot[16] = "EXTERNAL_5V"; // Mutex 실패 시 안전 기본값
  uint8_t virt_batt_snapshot = 100;          // Mutex 실패 시 안전 기본값 (만충)

  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    // 임계 구역: g_config 멤버를 로컬 스택 변수로 복사
    strncpy(mode_snapshot, g_config.power_mode, sizeof(mode_snapshot) - 1);
    mode_snapshot[sizeof(mode_snapshot) - 1] = '\0'; // null 종단 보장
    virt_batt_snapshot = g_config.current_battery_level;
    xSemaphoreGive(g_config_mutex); // 즉시 반환하여 콜백 컨텍스트가 대기하지 않도록 함
  } else {
    // Mutex 타임아웃: 시스템 부하가 높을 때 발생할 수 있습니다.
    // 기본값(EXTERNAL_5V, 100%)으로 폴백하여 데이터 전송을 중단하지 않습니다.
    Serial.println("[전처리] ⚠ Mutex 획득 타임아웃(50ms) — 기본 EXTERNAL_5V 모드로 폴백");
  }

  // ── POWER_MODE 분기: 배터리 레벨 데이터 소스 결정 ────────────────────────
  if (strcmp(mode_snapshot, "BATTERY") == 0) {
    // ── BATTERY 모드 분기 ─────────────────────────────────────────────────
    // 대시보드에서 내려온 CURRENT_BATTERY_LEVEL 가상값을 완전히 무시합니다.
    // BATTERY_ADC_PIN(GPIO 34)에 연결된 전압 분배기 회로에서 실제 배터리 전압을
    // ADC로 측정하여 0~100% 범위로 변환한 값을 배터리 입력으로 바인딩합니다.
    inputs.battery_pct = read_battery_adc_pct();
    Serial.printf("[전처리] 전원모드=BATTERY → GPIO %d ADC 실측 배터리: %.1f %%\n",
                  BATTERY_ADC_PIN, inputs.battery_pct);
  } else {
    // ── EXTERNAL_5V 모드 분기 (기본값 포함) ─────────────────────────────────
    // 외부 전원(USB 5V / DC 어댑터)으로 동작 중이므로 물리 배터리 측정을 생략합니다.
    // 웹 대시보드 운영자가 MQTT로 설정한 CURRENT_BATTERY_LEVEL 가상값을
    // 배터리 특징 페이로드로 직접 사용합니다 (시뮬레이션 모드).
    inputs.battery_pct = (float)virt_batt_snapshot;
    Serial.printf("[전처리] 전원모드=EXTERNAL_5V → 대시보드 가상 배터리: %.0f %%\n",
                  inputs.battery_pct);
  }

  return inputs; // 모든 필드가 채워진 SensorInputs 반환
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 K] ★ TinyML 신경망 기반 동적 QoS 추론 에이전트 ★
 *          run_agent_inference()
 *
 * run_neural_network_inference()가 반환한 연속적 위험 확률 점수(0.0~1.0)를
 * 평가하여 MQTT-SN 전송에 사용할 QoS 레벨과 긴급도를 결정합니다.
 *
 * 신경망 출력 → QoS 매핑 규칙:
 * ┌──────────────────┬──────────────┬─────────────────────────────────────┐
 * │ danger_probability│ 상태        │ 선택 QoS (동작)                      │
 * ├──────────────────┼──────────────┼─────────────────────────────────────┤
 * │ >= TH_QOS2       │ CRITICAL     │ QoS 2 (4단계 핸드셰이크, 절대 신뢰) │
 * │ >= TH_QOS1       │ WARNING      │ QoS 1 (2단계 핸드셰이크, 재전송 보장)│
 * │  < TH_QOS1       │ NORMAL       │ QoS 0 (무확인 단발 전송, 최대 저전력)│
 * └──────────────────┴──────────────┴─────────────────────────────────────┘
 *
 * 추가로 RSSI 기반 네트워크 상태를 평가합니다:
 *   - rssi < rssi_threshold → 네트워크 불안정 (net_status = 1)
 *   - rssi >= rssi_threshold → 네트워크 안정 (net_status = 0)
 *
 * 매개변수:
 *   inputs       - preprocess_sensor_inputs()가 반환한 센서 데이터
 *   net_status   - [출력 참조] 네트워크 안정성 (0: 정상, 1: 불안정)
 *   urgency      - [출력 참조] 신경망 판정 긴급도 (0: 정상, 1: 경고, 2: 위기)
 *   nn_score_out - [출력 참조] 신경망 원시 위험 확률 점수 (0.0 ~ 1.0)
 *
 * 반환값: 선택된 QoSLevel (QoS0 / QoS1 / QoS2)
 * ═══════════════════════════════════════════════════════════════════════════ */
static QoSLevel run_agent_inference(const SensorInputs &inputs,
                                    uint8_t &net_status,
                                    uint8_t &urgency,
                                    float   &nn_score_out) {

  // ── 단계 1: TinyML MLP 신경망 피드포워드 추론 실행 ──────────────────────
  // 3개 환경 센서 특징을 입력으로 받아 위험 확률 점수를 계산합니다.
  Serial.println("[신경망] 🧠 MLP 피드포워드 추론 시작 (입력: 온도/습도/가스저항/기압/가스비율)");
  float danger_probability = run_neural_network_inference(
      inputs.temp, inputs.hum, inputs.gas_kohm, inputs.pres_hpa, inputs.gas_ratio);
  nn_score_out = danger_probability; // 호출자에게 원시 점수 노출 (페이로드 포함용)

  // 추론 결과 상세 출력 (가중치 연산 → 최종 확률 점수)
  Serial.printf("[신경망] 입력값 → 온도: %.2f°C | 습도: %.2f%% | 가스저항: %.2f kΩ | 기압: %.2f hPa | 가스비율: %.3f\n",
                inputs.temp, inputs.hum, inputs.gas_kohm, inputs.pres_hpa, inputs.gas_ratio);
  Serial.printf("[신경망] MLP 추론 완료 → 위험 확률 점수: %.4f (%.2f%%)\n",
                danger_probability, danger_probability * 100.0f);

  // ── 단계 2: 신경망 출력 → 긴급도(urgency) 매핑 ─────────────────────────
  // 연속 확률 점수를 3단계 이산 긴급도로 분류합니다.
  if (danger_probability >= MLP_TH_QOS2) {
    // 위험 확률 75% 이상: CRITICAL 판정
    // 화재 또는 심각한 가스 누출 등 즉각적 위협이 감지된 상태입니다.
    urgency = 2;
    Serial.println("[신경망] 판정: 🔴 CRITICAL — 즉각적 위협 감지 (점수 ≥ QoS 2 임계값)");
  } else if (danger_probability >= MLP_TH_QOS1) {
    // 위험 확률 40% 이상 ~ 75% 미만: WARNING 판정
    // 주의가 필요한 환경 변화가 감지된 경계 상태입니다.
    urgency = 1;
    Serial.println("[신경망] 판정: 🟡 WARNING  — 경계 상태 감지 (점수가 QoS 1~2 임계값 사이)");
  } else {
    // 위험 확률 40% 미만: NORMAL 판정
    // 환경이 정상 범위 내에 있으며 추가 조치가 불필요합니다.
    urgency = 0;
    Serial.println("[신경망] 판정: 🟢 NORMAL   — 정상 상태 확인 (점수 < QoS 1 임계값)");
  }

  // ── 단계 3: 네트워크 상태 판정 (RSSI + 실측 혼잡도) ─────────────────────
  // Mutex 보호 하에 RSSI 임계값과 패킷 손실 상한의 스냅샷 획득
  int8_t local_rssi_thresh = -80;   // Mutex 실패 시 안전 기본값
  float  local_loss_limit  = 5.0f;  // Mutex 실패 시 안전 기본값 (PACKET_LOSS_LIMIT, %)

  if (g_config_mutex != nullptr &&
      xSemaphoreTake(g_config_mutex, pdMS_TO_TICKS(50)) == pdTRUE) {
    local_rssi_thresh = g_config.rssi_threshold;
    local_loss_limit  = g_config.packet_loss_limit;
    xSemaphoreGive(g_config_mutex);
  } else {
    Serial.println("[추론] ⚠ 네트워크 임계값 Mutex 타임아웃 — 기본값(RSSI -80 dBm, 손실 상한 5%) 사용");
  }
  g_loss_limit = local_loss_limit;  // 전송 후 혼잡도 갱신(net_observe)에서 같은 값을 씁니다

  // RSSI가 동적 임계값(기본 -80 dBm) 미만이면 불안정 선로로 판정합니다.
  // 불안정 선로에서는 QoS 레벨을 높여 재전송을 통한 신뢰성을 확보합니다.
  net_status = (inputs.rssi < local_rssi_thresh) ? 1 : 0;
  Serial.printf("[추론] 네트워크 상태: RSSI=%d dBm (임계: %d dBm) → %s\n",
                inputs.rssi, local_rssi_thresh,
                net_status == 1 ? "불안정" : "안정");

  // 혼잡도: ACK를 받는 전송의 실측 손실률/지연으로 판정합니다. RSSI는 신호 세기일 뿐 혼잡도가 아닙니다
  // (신호가 강해도 혼잡해서 손실될 수 있음). 환경이 정상이고 RSSI도 좋은데 QoS 0이 길게 이어져 관측이
  // 없었다면, 이번 사이클은 QoS 1 프로브로 보내 현재 네트워크 상태를 측정합니다.
  // 판정 규칙은 net_congestion.h의 net_decide_qos()에 있습니다 (시험이 같은 코드를 검증).
  const bool congested = g_net.degraded;
  const QosDecision decision = net_decide_qos(danger_probability >= MLP_TH_QOS2,
                                              danger_probability >= MLP_TH_QOS1,
                                              net_status == 1, g_net);
  g_probe_this_cycle = decision.probe;
  Serial.printf("[추론] 혼잡도: 손실 %.1f%% (상한 %.1f%%) | 지연 배율 %.2f | ACK 없는 사이클 %u/%d → %s%s\n",
                g_net.loss_pct, local_loss_limit, net_rtt_ratio(g_net),
                (unsigned)g_net.idle_cycles, NET_PROBE_INTERVAL,
                congested ? "혼잡" : "정상", g_probe_this_cycle ? " (관측용 프로브 사이클)" : "");

  // ── 단계 4: 신경망 분류 결과 → QoS 레벨 최종 결정 ────────────────────
  // QoS = max(환경 위험 QoS, 네트워크 QoS). 네트워크(RSSI 약함/혼잡/프로브)는 QoS를 최대 1까지만 올립니다.
  // QoS 2(TCP)는 환경 위험 전용으로 유지합니다.
  QoSLevel selected_qos;

  if (decision.qos == 2) {
    // CRITICAL 상태: 신경망이 고신뢰도로 위험을 판정
    // QoS 2의 4단계 핸드셰이크(PUBLISH→PUBREC→PUBREL→PUBCOMP)로
    // 정확히 1회 전달(Exactly-Once Delivery)을 보장합니다.
    // 네트워크 상태와 무관하게 QoS 2를 강제 할당합니다.
    selected_qos = QoSLevel::QoS2;
    Serial.println("[QoS결정] ⚡ QoS 2 강제 할당 — CRITICAL: 절대 신뢰성 전송 모드");
  } else if (decision.qos == 1) {
    // WARNING 상태 또는 네트워크 불안정: 중간 수준 위협 감지
    // QoS 1의 2단계 핸드셰이크(PUBLISH→PUBACK)로 최소 1회 전달을 보장합니다.
    // 재전송(최대 3회)으로 메시지 손실을 방지합니다.
    selected_qos = QoSLevel::QoS1;
    // 사유를 모두 표시합니다 (현장에서 "왜 QoS 1인가"를 바로 확인할 수 있도록).
    Serial.printf("[QoS결정] ⚡ QoS 1 전환 — 사유:%s%s%s%s\n",
                  danger_probability >= MLP_TH_QOS1 ? " 환경경고" : "",
                  net_status == 1 ? " RSSI약함" : "",
                  congested ? " 네트워크혼잡" : "",
                  g_probe_this_cycle ? " 관측프로브" : "");
  } else {
    // NORMAL 상태 + 네트워크 안정: 위협 없음
    // QoS 0의 무확인 단발 전송(Fire-and-Forget)으로 전력 소모를 최소화합니다.
    // 응답 대기 없이 즉시 Sleep 진입 가능합니다.
    selected_qos = QoSLevel::QoS0;
    Serial.println("[QoS결정] ✅ QoS 0 유지 — NORMAL: 무선 오버헤드 최소화, 저전력 모드");
  }

  return selected_qos;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 L] UDP 패킷 수신 대기 함수 — wait_for_packet() (기존 로직 100% 보존)
 *
 * 특정 MsgType과 msg_id가 일치하는 응답 패킷이 올 때까지
 * 최대 2.0초 동안 폴링합니다 (타임아웃 명세 준수).
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

  // 2,000ms(2.0초) 이내에 수신되지 않으면 타임아웃으로 판정합니다.
  while (millis() - start_time < 2000) {
    int packetSize = udp.parsePacket();

    // Header 크기 이상의 패킷이 수신된 경우에만 처리합니다.
    if (packetSize >= (int)sizeof(Header)) {
      uint8_t buffer[128];
      udp.read(buffer, sizeof(buffer));
      Header *header = (Header *)buffer;

      // 수신된 패킷의 MsgType이 기대 타입과 일치하는지 확인합니다.
      if (header->msg_type == expected_type) {
        // QoS 1: PUBACK의 msg_id가 전송 msg_id와 일치하는지 검증
        if (expected_type == MsgType::PUBACK &&
            ((PubAckPacket *)buffer)->msg_id == target_msg_id)
          return true;

        // QoS 2 단계 1: PUBREC의 msg_id 검증
        if (expected_type == MsgType::PUBREC &&
            ((PubRecPacket *)buffer)->msg_id == target_msg_id)
          return true;

        // QoS 2 단계 2: PUBCOMP의 msg_id 검증
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
 * [섹션 L-2] TCP 전송 — 상위 QoS(TCP_MIN_QOS 이상) 신뢰성 모드
 *
 * 와이어 포맷 (little-endian):
 *   디바이스 → 게이트웨이 : [len: uint16][PublishPacket 바이트 (len 바이트)]
 *   게이트웨이 → 디바이스 : PUBCOMP 4바이트 [len=4][type=7][msg_id: uint16]
 * Header.length는 uint8라 264바이트 PublishPacket을 표현할 수 없으므로, TCP 스트림의
 * 패킷 경계는 2바이트 길이 접두로 구분합니다.
 *
 * 1회 시도 = 연결 → 전송 → PUBCOMP 대기(최대 2.0초) → 연결 종료.
 * 시도마다 연결하므로 TCP 핸드셰이크 시간이 RTT에 포함됩니다. (UDP 경로와 달리 연결 설정
 * 비용이 있다는 점이 전송 계층 전환의 실제 비용이며, 연결을 유지하지 않아 Sleep과 충돌하지 않습니다.)
 *
 * 반환값: true = 올바른 msg_id의 PUBCOMP 수신 / false = 연결·전송 실패 또는 타임아웃
 * ═══════════════════════════════════════════════════════════════════════════ */
static bool tcp_publish_and_wait_ack(const PublishPacket &pkt) {
  WiFiClient tcp;
  if (!tcp.connect(UDP_SERVER_IP, TCP_SERVER_PORT, 2000)) {
    return false; // 연결 실패 (게이트웨이 다운, 포트 차단, 2.0초 타임아웃)
  }
  tcp.setNoDelay(true); // 작은 프레임을 Nagle 지연 없이 즉시 전송

  // 길이 접두 + 본문을 한 번의 write로 보내 한 세그먼트로 묶습니다.
  const uint16_t body_len = (uint16_t)publish_packet_size(pkt);
  uint8_t frame[2 + sizeof(PublishPacket)];
  frame[0] = (uint8_t)(body_len & 0xFF);
  frame[1] = (uint8_t)(body_len >> 8);
  memcpy(frame + 2, &pkt, body_len);
  if (tcp.write(frame, 2u + body_len) != (size_t)(2u + body_len)) {
    tcp.stop();
    return false;
  }

  // PUBCOMP 4바이트 수신 대기 (최대 2.0초)
  uint8_t ack[sizeof(PubCompPacket)];
  size_t got = 0;
  const unsigned long start_ms = millis();
  while (got < sizeof(ack) && millis() - start_ms < 2000) {
    if (tcp.available()) {
      int n = tcp.read(ack + got, sizeof(ack) - got);
      if (n > 0) got += (size_t)n;
    } else if (!tcp.connected()) {
      break;        // 게이트웨이가 응답 없이 연결을 닫음
    } else {
      delay(5);     // 5ms 폴링: CPU 점유와 응답성의 균형
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
 * 실행 순서:
 *   1. 시리얼 모니터 초기화
 *   2. FreeRTOS Mutex 생성 (g_config 보호용)
 *   3. 배터리 ADC 핀 초기화 (GPIO 34)
 *   4. Wi-Fi 연결 대기
 *   5. UDP 소켓 초기화 (커스텀 MQTT-SN)
 *   6. MQTT 클라이언트 초기화 및 설정 토픽 구독
 *   7. 커스텀 MQTT-SN CONNECT 패킷 전송 (Board 1 ID: "ESP32-Gingerbread")
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  // ── 1단계: 시리얼 모니터 초기화 ─────────────────────────────────────────
  Serial.begin(115200);
  delay(100); // USB-시리얼 초기화 안정화 대기
  Serial.println("\n╔═══════════════════════════════════════════════════════════╗");
  Serial.println("║  Board 1: Gingerbread — TinyML MLP 엣지 AI 펌웨어 부팅   ║");
  Serial.println("║  아키텍처: MLP 신경망 추론 + FreeRTOS Mutex + MQTT-SN    ║");
  Serial.println("╚═══════════════════════════════════════════════════════════╝");

  // 위험 점수 MLP 가중치의 출처를 매 부팅마다 명시합니다. 학습되지 않은 임시값으로 실험 데이터를
  // 수집하면 결과가 "학습된 모델"의 것으로 오해될 수 있으므로 눈에 띄게 경고합니다.
#if MLP_WEIGHTS_TRAINED
  Serial.println("[MLP] 가중치: 학습된 값 (ml_model/train.py 생성)");
#else
  Serial.println("[MLP] ⚠ 경고: 가중치가 학습되지 않은 임시값입니다 (mlp_weights.h, MLP_WEIGHTS_TRAINED=0).");
  Serial.println("[MLP] ⚠ 이 상태의 QoS 결정 결과를 학습 모델의 성능으로 보고하면 안 됩니다.");
#endif
  Serial.printf("[MLP] QoS 임계값: QoS1 >= %.3f | QoS2 >= %.3f\n", MLP_TH_QOS1, MLP_TH_QOS2);

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

  // ── 2단계: FreeRTOS Mutex 생성 ───────────────────────────────────────────
  // g_config 구조체에 대한 동시 접근을 보호하는 Mutex를 생성합니다.
  // Mutex가 없으면 MQTT 콜백(쓰기)과 loop()(읽기)가 동시에 접근할 때
  // 데이터 레이스(data race)가 발생할 수 있습니다.
  g_config_mutex = xSemaphoreCreateMutex();
  if (g_config_mutex == nullptr) {
    // Mutex 생성 실패는 심각한 오류입니다. 즉시 재부팅하여 복구를 시도합니다.
    Serial.println("[부팅] ✗ [심각] FreeRTOS Mutex 생성 실패! 5초 후 재시작합니다.");
    delay(5000);
    ESP.restart();
  }
  Serial.println("[부팅] ✓ FreeRTOS Mutex 생성 완료 (g_config 스레드 안전 보호용)");

  // ── 3단계: 배터리 ADC 핀 초기화 ─────────────────────────────────────────
  // BATTERY 모드에서만 실제로 ADC를 읽지만, 핀은 항상 초기화합니다.
  // ADC_11db 감쇠: 입력 전압 범위를 0~3.6V(실효 0~3.3V)로 설정합니다.
  analogSetAttenuation(ADC_11db);
  pinMode(BATTERY_ADC_PIN, INPUT);
  Serial.printf("[부팅] ✓ 배터리 ADC 핀 초기화 완료 (GPIO %d, 감쇠: 11dB)\n",
                BATTERY_ADC_PIN);

  // ── 4단계: Wi-Fi 연결 ────────────────────────────────────────────────────
  Serial.printf("[부팅] Wi-Fi 연결 시도 중... SSID: \"%s\"\n", WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // WL_CONNECTED 상태가 될 때까지 500ms 간격으로 폴링합니다.
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\n[부팅] ✓ Wi-Fi 연결 성공 — 할당 IP: %s | RSSI: %d dBm\n",
                WiFi.localIP().toString().c_str(),
                (int8_t)WiFi.RSSI());

  // ── 5단계: UDP 소켓 초기화 (커스텀 MQTT-SN 프로토콜 전용) ──────────────
  // UDP_SERVER_PORT로 수신을 바인딩합니다. 서버 응답(PUBACK, PUBREC, PUBCOMP)도
  // 동일한 포트로 수신됩니다.
  udp.begin(UDP_SERVER_PORT);
  Serial.printf("[부팅] ✓ UDP 소켓 초기화 완료 (포트 %u, 게이트웨이: %s)\n",
                UDP_SERVER_PORT, UDP_SERVER_IP);

  // ── 6단계: 표준 MQTT 클라이언트 초기화 및 설정 구독 ─────────────────────
  mqtt_client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT); // 브로커 엔드포인트 등록
  mqtt_client.setCallback(on_mqtt_message);                 // 설정 수신 콜백 바인딩
  mqtt_client.setKeepAlive(60);                             // PINGREQ 전송 간격 60초
  mqtt_connect_and_subscribe(); // 최초 MQTT 연결 및 "gingerbread/config" 구독

  // ── 7단계: 커스텀 MQTT-SN CONNECT 패킷 전송 ─────────────────────────────
  // UDP 게이트웨이에 Board 1을 "ESP32-Gingerbread" ID로 세션 등록합니다.
  ConnectPacket conn_pkt;
  conn_pkt.header.length   = sizeof(ConnectPacket);
  conn_pkt.header.msg_type = MsgType::CONNECT;

  // Board 1 고유 식별자를 ConnectPacket에 명시적으로 삽입
  strncpy(conn_pkt.client_id, BOARD1_CLIENT_ID, sizeof(conn_pkt.client_id) - 1);
  conn_pkt.client_id[sizeof(conn_pkt.client_id) - 1] = '\0'; // null 종단 보장

  conn_pkt.sleep_duration = 5; // 절전 주기 5초 (게이트웨이 세션 유지 시간)

  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&conn_pkt, sizeof(conn_pkt));
  udp.endPacket();
  Serial.printf("[부팅] ✓ CONNECT 패킷 전송 → 게이트웨이 세션 등록 (ID: %s)\n",
                BOARD1_CLIENT_ID);

  Serial.println("[부팅] ══ 초기화 완료, TinyML 메인 루프 시작 ══\n");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * loop() — Arduino 메인 루프
 *
 * 매 루프 사이클 실행 순서:
 *   1. MQTT 클라이언트 루프 (설정 구독 유지 + 재연결 관리)
 *   2. 이중 전원 모드 센서 입력 전처리 (EXTERNAL_5V / BATTERY 분기)
 *   3. TinyML MLP 신경망 추론 + 동적 QoS 결정
 *   4. PUBLISH 패킷 조립 (확장 JSON: temp, hum, gas, battery, nn_score, qos)
 *   5. QoS 레벨별 고충실도 핸드셰이크 전송 (QoS 0 / 1 / 2)
 *   6. 전송 결과 로깅
 *   7. DISCONNECT(Sleep) 패킷 전송 + 절전 대기 (5초)
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ 1단계: MQTT 클라이언트 루프 실행                                     │
  // │ mqtt_client.loop()은 반드시 주기적으로 호출해야 합니다.               │
  // │ 내부 동작:                                                           │
  // │   - 수신 메시지가 있으면 on_mqtt_message() 콜백 디스패치              │
  // │   - PINGREQ/PINGRESP로 keepalive 유지                               │
  // └─────────────────────────────────────────────────────────────────────┘
  if (!mqtt_client.connected()) {
    // 브로커 연결이 끊어진 경우 재연결을 시도합니다.
    // 재연결 성공 시 retain 메시지를 다시 수신하여 최신 설정이 복원됩니다.
    Serial.println("[루프] ⚠ MQTT 브로커 연결 끊김 감지 — 재연결 시도");
    mqtt_connect_and_subscribe();
  }
  // 수신 큐에 있는 메시지를 처리하고 on_mqtt_message() 콜백을 실행합니다.
  mqtt_client.loop();

  Serial.println("\n────────────────────────────────────────────────────────────");
  Serial.println("[루프] ▶ 새로운 측정 및 전송 사이클 시작");

  // ── [2026-06 추가] 사이클 시작 타임스탬프 (active/sleep 비율 추적용) ────────
  // 이 시점부터 DISCONNECT 전송 직전까지를 활성 구간으로 평가합니다.
  unsigned long cycle_start_ms = millis();

  // 이번 사이클 말미의 Sleep 시간(ms). 게이트웨이 전력 추정용 텔레메트리("slp")에도 쓰이므로
  // 전송 전에 확정되어 있어야 합니다.
  const uint32_t SLEEP_DURATION_MS = 5000;

  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ 2단계: 이중 전원 모드 센서 입력 전처리                               │
  // │ POWER_MODE에 따라 battery_pct의 데이터 소스가 결정됩니다.            │
  // │   EXTERNAL_5V → 대시보드 가상 배터리 레벨 (ADC 미사용)              │
  // │   BATTERY     → GPIO 34 ADC 실측 전압 (0~100% 변환)                │
  // └─────────────────────────────────────────────────────────────────────┘
  SensorInputs sensor_data = preprocess_sensor_inputs();

  Serial.printf("[센싱] 온도: %.2f°C | 습도: %.2f%% | 가스저항: %.2f kΩ | "
                "배터리: %.0f%% | RSSI: %d dBm\n",
                sensor_data.temp, sensor_data.hum, sensor_data.gas_kohm,
                sensor_data.battery_pct, sensor_data.rssi);

  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ 3단계: TinyML MLP 신경망 추론 + 동적 QoS 결정                       │
  // │ 규칙 기반 임계값 비교를 완전히 대체한 신경망 기반 추론입니다.          │
  // │ 연속 위험 확률 점수(0.0~1.0) → QoS 레벨 매핑                        │
  // └─────────────────────────────────────────────────────────────────────┘
  uint8_t  net_status   = 0;    // 네트워크 상태 판정 결과 (0: 안정, 1: 불안정)
  uint8_t  urgency      = 0;    // 신경망 긴급도 분류 (0: 정상, 1: 경고, 2: 위기)
  float    nn_score     = 0.0f; // 신경망 원시 위험 확률 점수 (페이로드 포함)

  QoSLevel selected_qos = run_agent_inference(
      sensor_data, net_status, urgency, nn_score);

  Serial.printf("[루프] ── TinyML 추론 완료 → QoS %d 선택 "
                "(신경망 점수: %.4f) ──\n", (int)selected_qos, nn_score);

  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ 4단계: PUBLISH 패킷 조립                                             │
  // │ JSON 페이로드 확장:                                                   │
  // │   "temp"     : BME680 온도 (°C)                                     │
  // │   "hum"      : BME680 습도 (%)                                      │
  // │   "gas"      : BME680 가스 저항값 (kΩ)                              │
  // │   "battery"  : 전원 모드 분기 배터리 레벨 (%)                        │
  // │   "nn_score" : 신경망 위험 확률 점수 (0.0~1.0)                      │
  // │   "qos"      : 선택된 QoS 레벨 (0 / 1 / 2)                         │
  // └─────────────────────────────────────────────────────────────────────┘
  PublishPacket pub_pkt;
  pub_pkt.header.length   = sizeof(PublishPacket);
  pub_pkt.header.msg_type = MsgType::PUBLISH;
  pub_pkt.msg_id          = current_msg_id++;  // 단조 증가 메시지 ID
  pub_pkt.topic_id        = 1;                 // 사전 등록된 토픽 ID (게이트웨이와 협의)
  pub_pkt.qos             = selected_qos;      // 신경망 추론으로 결정된 QoS 레벨
  pub_pkt.network_status  = net_status;        // 네트워크 상태 (게이트웨이 모니터링용)
  pub_pkt.data_urgency    = (urgency > 0) ? 1 : 0; // 긴급도 플래그 (게이트웨이 알람용)

  // JSON 페이로드 직렬화 (256바이트 PublishPacket.payload 버퍼에 직접 작성)
  // [2026-06 확장] RTT, retry_count, sleep_mode_ratio 필드를 페이로드에 포함
  // 게이트웨이(Python)에서 rtt_ms, retry, sleep_r 키로 파싱합니다.
  //
  // sleep_mode_ratio 사전 계산: PUBLISH 전송 시점의 최신 Sleep비율
  // (RTT 측정 후 6단계에서 최종 갱신되지만, 이미 종료된 원시 데이터를 포함할 수 없으므로
  //  이전 사이클까지의 누적값을 사용합니다.)
  float current_sleep_ratio = 0.0f;
  {
    uint32_t tot = g_total_active_ms + g_total_sleep_ms;
    if (tot > 0) current_sleep_ratio = (float)g_total_sleep_ms / (float)tot;
  }

  snprintf(pub_pkt.payload, sizeof(pub_pkt.payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,"
           "\"battery\":%.0f,\"nn\":%.3f,\"qos\":%d,"
           "\"sleep_r\":%.3f}",
           sensor_data.temp,
           sensor_data.hum,
           sensor_data.gas_kohm,
           sensor_data.battery_pct,
           nn_score,
           (int)selected_qos,
           current_sleep_ratio);
  // 주: 이 PUBLISH는 핸드셰이크 "이전"에 만들어지므로 RTT/retry를 담을 수 없습니다.
  // 게이트웨이는 이 패킷으로 전력을 추정하지 않습니다. 실측 RTT/retry와 사이클 활성/Sleep
  // 시간은 핸드셰이크 완료 후 6단계에서 보내는 topic 2 텔레메트리 패킷에 담깁니다.

  Serial.printf("[루프] PUBLISH 페이로드 (MsgID=%u): %s\n",
                pub_pkt.msg_id, pub_pkt.payload);

  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ 5단계: QoS 레벨별 고충실도 핸드셰이크 전송 시퀀스                    │
  // │ [2026-06 추가] RTT 측정 및 retry_count, total_bytes 누적 추적       │
  // └─────────────────────────────────────────────────────────────────────┘
  int       retry_count         = 0;
  const int max_retries         = 3;    // 최대 3회 재전송 제한
  bool      transaction_success = false;
  float     rtt_ms              = 0.0f; // PUBLISH~ACK 왕복 시간 (ms)
  float     last_attempt_rtt_ms = -1.0f; // 마지막(성공한) 시도만의 RTT (ms). 혼잡도용, 알 수 없으면 -1

  // ── RTT 측정 시작점: PUBLISH 전송 직전 마이크로초 타임스탬프 캡처 ─────
  // micros()는 ESP32 부팅 후 경과 μs를 반환합니다.
  // 오버플로(약 71.6분 주기)는 부호 없는 정수 연산으로 자동 처리됩니다.
  unsigned long rtt_start_us = micros();

  // 전송 계층 결정: 상위 QoS는 TCP, 나머지는 UDP (프로젝트 명세, TCP_MIN_QOS 참조)
  const bool use_tcp = ((uint8_t)selected_qos >= TCP_MIN_QOS);
  Serial.printf("[루프] 전송 계층: %s (QoS %d)\n", use_tcp ? "TCP" : "UDP", (int)selected_qos);

  if (use_tcp) {
    // ══════════════════════════════════════════════════════════════════════
    // [TCP 신뢰성 모드] 상위 QoS — 수신 보장이 필수인 상황 (신경망 CRITICAL 판정)
    // TCP가 전달·순서를 보장하고, 게이트웨이가 처리를 마친 뒤 PUBCOMP로 확인합니다.
    // UDP 경로와 같은 재전송 정책(최대 3회, 2.0초 타임아웃)을 적용합니다.
    // RTT: 최초 시도의 연결 시작 ~ PUBCOMP 수신 (연결 설정·재시도 대기 시간 포함).
    // ══════════════════════════════════════════════════════════════════════
    while (retry_count <= max_retries) {
      if (tcp_publish_and_wait_ack(pub_pkt)) {
        rtt_ms = (float)(micros() - rtt_start_us) / 1000.0f;
        transaction_success = true;
        Serial.printf("[TCP QoS %d] ✓ 성공 — PUBCOMP 수신 확인 | RTT: %.2f ms | 재전송: %d회 (MsgID: %u)\n",
                      (int)selected_qos, rtt_ms, retry_count, pub_pkt.msg_id);
        break;
      }
      retry_count++;
      Serial.printf("[TCP QoS %d] ⚠ 실패/타임아웃 — 재전송 (%d/%d)\n",
                    (int)selected_qos, retry_count, max_retries);
    }

  } else if (selected_qos == QoSLevel::QoS0) {
    // ══════════════════════════════════════════════════════════════════════
    // [QoS 0] 무확인 단발성 전송 — Fire and Forget
    // PUBACK를 기다리지 않고 즉시 Sleep 진입합니다.
    // 응답 대기 없으므로 전력 소모가 가장 낮습니다.
    // 적용 조건: 신경망 점수 < 0.40 (NORMAL 상태) + 네트워크 안정
    // RTT: QoS 0는 응답이 없으므로 단방향 전송 시간만 근사합니다.
    // ══════════════════════════════════════════════════════════════════════
    udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
    udp.write((uint8_t *)&pub_pkt, publish_packet_size(pub_pkt));
    udp.endPacket();
    // QoS 0: 확인응답 없음 → RTT는 전송 완료 직후로 측정 (단방향 전송 시간)
    rtt_ms = (float)(micros() - rtt_start_us) / 1000.0f;
    transaction_success = true;
    Serial.printf("[QoS 0] ✓ 단발 전송 완료 | RTT(단방향): %.2f ms (응답 대기 없음)\n",
                  rtt_ms);
    Serial.println("[전력 모드] NORMAL 판정 → 무선 칩셋 오버헤드 최소화 유지");

  } else if (selected_qos == QoSLevel::QoS1) {
    // ══════════════════════════════════════════════════════════════════════
    // [QoS 1] 2단계 핸드셰이크: PUBLISH → PUBACK
    // PUBACK 수신으로 최소 1회 이상 전달을 보장합니다.
    // 적용 조건: 신경망 점수 >= 0.40 (WARNING 상태) 또는 네트워크 불안정
    // ══════════════════════════════════════════════════════════════════════
    while (retry_count <= max_retries) {
      // PUBLISH 패킷 UDP 전송
      const unsigned long attempt_start_us = micros(); // 이번 시도의 시작 (재전송 대기 시간이 섞이지 않는 RTT용)
      udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
      udp.write((uint8_t *)&pub_pkt, publish_packet_size(pub_pkt));
      udp.endPacket();

      // PUBACK 수신 대기 (최대 2.0초 타임아웃)
      if (wait_for_packet(MsgType::PUBACK, pub_pkt.msg_id)) {
        // ── RTT 측정 완료: 최초 PUBLISH 전송 시점 ~ PUBACK 수신 시점 ──
        rtt_ms = (float)(micros() - rtt_start_us) / 1000.0f;
        last_attempt_rtt_ms = (float)(micros() - attempt_start_us) / 1000.0f;
        transaction_success = true;
        Serial.printf("[QoS 1] ✓ 성공 — PUBACK 수신 확인 | RTT: %.2f ms | 재전송: %d회 (MsgID: %u)\n",
                      rtt_ms, retry_count, pub_pkt.msg_id);
        break; // 핸드셰이크 완료: 재전송 루프 종료
      }

      retry_count++;
      Serial.printf("[QoS 1] ⚠ 타임아웃 — PUBLISH 재전송 (%d/%d)\n",
                    retry_count, max_retries);
    }

  } else if (selected_qos == QoSLevel::QoS2) {
    // ══════════════════════════════════════════════════════════════════════
    // [QoS 2] 4단계 핸드셰이크: PUBLISH → PUBREC → PUBREL → PUBCOMP
    // 정확히 1회 전달(Exactly Once Delivery)을 보장합니다.
    // 적용 조건: 신경망 점수 >= 0.75 (CRITICAL 상태, 최고 위험도)
    //
    // 단계 1: PUBLISH 전송 + PUBREC 대기 (수신 확인)
    // 단계 2: PUBREL 전송 + PUBCOMP 대기 (완료 확인)
    // ══════════════════════════════════════════════════════════════════════
    bool pubrec_received = false;

    // ── QoS 2 단계 1: PUBLISH 전송 및 PUBREC 수신 대기 ──────────────────
    while (retry_count <= max_retries) {
      udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
      udp.write((uint8_t *)&pub_pkt, publish_packet_size(pub_pkt));
      udp.endPacket();

      // PUBREC 수신 대기 (최대 2.0초)
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

    // ── QoS 2 단계 2: PUBREC 수신 후 PUBREL 전송 및 PUBCOMP 대기 ────────
    if (pubrec_received) {
      int retry_phase2 = 0; // 단계 2 전용 재전송 카운터

      // PUBREL 패킷 조립 (동일한 msg_id 사용)
      PubRelPacket rel_pkt;
      rel_pkt.header.length   = sizeof(PubRelPacket);
      rel_pkt.header.msg_type = MsgType::PUBREL;
      rel_pkt.msg_id          = pub_pkt.msg_id; // PUBLISH와 동일한 msg_id

      while (retry_phase2 <= max_retries) {
        udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
        udp.write((uint8_t *)&rel_pkt, sizeof(rel_pkt));
        udp.endPacket();

        // PUBCOMP 수신 대기 (최대 2.0초) — 게이트웨이가 처리 완료를 통지
        if (wait_for_packet(MsgType::PUBCOMP, pub_pkt.msg_id)) {
          // ── RTT 측정 완료: 최초 PUBLISH 전송 시점 ~ PUBCOMP 수신 시점 ──
          rtt_ms = (float)(micros() - rtt_start_us) / 1000.0f;
          transaction_success = true;
          Serial.printf("[QoS 2] 단계2 ✓ — PUBCOMP 수신, 4단계 핸드셰이크 완료 "
                        "| RTT(4-way): %.2f ms | 재전송: %d+%d회 (MsgID: %u)\n",
                        rtt_ms, retry_count, retry_phase2, pub_pkt.msg_id);
          // QoS 2 총 재전송 횟수: 두 단계 합산
          retry_count += retry_phase2;
          break; // 핸드셰이크 완료
        }

        retry_phase2++;
        Serial.printf("[QoS 2] 단계2 ⚠ 타임아웃 — PUBREL 재전송 (%d/%d)\n",
                      retry_phase2, max_retries);
      }
    } else {
      // PUBREC를 최대 재전송 횟수까지도 수신하지 못한 경우
      // 게이트웨이 다운, 심각한 네트워크 단절 등의 상황입니다.
      Serial.printf("[QoS 2] ✗ 단계1 최종 실패 — PUBREC 미수신 (MsgID: %u)\n",
                    pub_pkt.msg_id);
    }
  }

  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ 6단계: 전송 결과 최종 로깅 + 성능 메트릭 누적 집계                   │
  // │ [2026-06 추가] packet_count, total_bytes, sleep_mode_ratio 누적     │
  // └─────────────────────────────────────────────────────────────────────┘

  // ── 혼잡도 추적기 갱신 (net_congestion.h) ───────────────────────────────
  // ACK를 받는 전송(QoS 1, TCP)의 결과만 손실/지연을 관측할 수 있습니다. QoS 0은 관측이 없으므로 idle로 세어
  // 일정 횟수가 지나면 프로브(QoS 1)를 보냅니다. 실패한 시도 수 = 재전송 횟수입니다.
  // (참고: UDP QoS 2 4단계 경로는 2단계 실패 시 재전송 횟수가 합산되지 않아 손실이 약간 적게 잡힙니다.)
  // RTT는 UDP QoS 1의 마지막 시도만 씁니다: TCP는 연결 설정 시간이 섞이고, 재전송 타임아웃은 지연이 아닙니다.
  if (selected_qos != QoSLevel::QoS0) {
    const uint32_t failed_attempts = (uint32_t)retry_count;
    const uint32_t total_attempts  = failed_attempts + (transaction_success ? 1u : 0u);
    const float rtt_sample = (!use_tcp && selected_qos == QoSLevel::QoS1 && transaction_success)
                                 ? last_attempt_rtt_ms : -1.0f;
    net_observe(g_net, total_attempts, failed_attempts, rtt_sample, g_loss_limit);
  } else {
    net_on_idle_cycle(g_net);
  }

  // ── 활성 구간 경과 시간 계산 (Sleep 직전까지의 활성 시간) ────────────────
  // 사이클 시작(loop 진입) ~ DISCONNECT 전송 직전까지를 활성 시간으로 정의합니다.
  unsigned long active_elapsed_ms = millis() - cycle_start_ms;
  g_total_active_ms += (uint32_t)active_elapsed_ms;

  // ── 누적 전송량 갱신 ─────────────────────────────────────────────────────
  if (transaction_success) {
    g_packet_count++;                           // 성공적으로 전달된 패킷 수 증가
    g_total_bytes += (uint32_t)sizeof(pub_pkt); // PublishPacket 구조체 크기 누적
  }

  // ── sleep_mode_ratio 계산: 전체 경과 시간 대비 Sleep 비율 ───────────────
  // Sleep 시간이 아직 없으면 0.0으로 초기화 (첫 사이클 처리)
  float sleep_mode_ratio = 0.0f;
  uint32_t total_elapsed = g_total_active_ms + g_total_sleep_ms;
  if (total_elapsed > 0) {
    sleep_mode_ratio = (float)g_total_sleep_ms / (float)total_elapsed;
  }

  if (transaction_success) {
    Serial.printf("[루프] == QoS %d 전송 트랜잭션 성공 (MsgID: %u, 신경망 점수: %.3f)\n"
                  "       RTT: %.2f ms | 재전송: %d회 | Sleep비율: %.1f%%\n"
                  "       누적 패킷: %u | 누적 바이트: %u\n",
                  (int)selected_qos, pub_pkt.msg_id, nn_score,
                  rtt_ms, retry_count, sleep_mode_ratio * 100.0f,
                  g_packet_count, g_total_bytes);

    // ── [핵심] 실측 메트릭 텔레메트리 패킷 전송 ──────────────────────────────
    // 설계 근거:
    //   원래 PUBLISH 패킷은 핸드셰이크 "시작" 전에 전송되므로 구조적으로
    //   RTT와 retry_count를 포함할 수 없습니다 (값이 아직 결정되지 않음).
    //   핸드셰이크 완료 후 실측된 RTT/retry/sleep_ratio를 별도의 QoS 0
    //   텔레메트리 패킷(topic_id=2)으로 전송하여 게이트웨이가 정확한
    //   값으로 IEEE Access 2024 전력 추정을 수행하게 합니다.
    //
    //   게이트웨이(Python)는 topic_id==2 패킷을 수신하면
    //   telemetry_service._estimate_and_record_power()를 트리거합니다.
    PublishPacket telemetry_pkt;
    telemetry_pkt.header.length   = sizeof(PublishPacket);
    telemetry_pkt.header.msg_type = MsgType::PUBLISH;
    telemetry_pkt.msg_id          = current_msg_id++;  // 단조 증가 ID
    telemetry_pkt.topic_id        = 2;                 // topic 2 = 성능/전력 메트릭 채널
    telemetry_pkt.qos             = QoSLevel::QoS0;   // 텔레메트리는 항상 QoS 0 (오버헤드 없음)
    telemetry_pkt.network_status  = net_status;
    telemetry_pkt.data_urgency    = 0;

    // 핸드셰이크 완료 후 실측된 진짜 RTT/retry/sleep 값으로 직렬화
    snprintf(telemetry_pkt.payload, sizeof(telemetry_pkt.payload),
             "{\"temp\":%.2f,\"hum\":%.2f,\"gas\":%.2f,"
             "\"battery\":%.0f,\"nn\":%.3f,\"qos\":%d,"
             "\"rtt\":%.2f,\"retry\":%d,\"sleep_r\":%.4f,"
             "\"act\":%lu,\"slp\":%lu,\"tp\":\"%s\","
             "\"ls\":%.1f,\"rr\":%.2f,\"cg\":%d,\"pb\":%d,"
             "\"pkt\":%u,\"bytes\":%u}",
             sensor_data.temp, sensor_data.hum, sensor_data.gas_kohm,
             sensor_data.battery_pct, nn_score,
             (int)selected_qos,
             rtt_ms,           // 실측 RTT (핸드셰이크 완료 후 확정)
             retry_count,      // 실제 재전송 횟수
             sleep_mode_ratio, // 누적 Sleep 비율 (참고용)
             (unsigned long)active_elapsed_ms,  // 이번 사이클 활성 시간 (ms) — 전력 추정에 사용
             (unsigned long)SLEEP_DURATION_MS,  // 이번 사이클 Sleep 시간 (ms) — 전력 추정에 사용
             use_tcp ? "tcp" : "udp",           // 이번 트랜잭션의 전송 계층
             g_net.loss_pct,                    // 혼잡도: 손실률 EWMA (%)
             net_rtt_ratio(g_net),              // 혼잡도: 평소 대비 지연 배율 (1.0 = 평소)
             g_net.degraded ? 1 : 0,            // 혼잡 상태 여부 (히스테리시스 적용)
             g_probe_this_cycle ? 1 : 0,        // 이번 사이클이 관측용 QoS 1 프로브였는가
             g_packet_count, g_total_bytes);

    udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
    udp.write((uint8_t *)&telemetry_pkt, publish_packet_size(telemetry_pkt));
    udp.endPacket();
    Serial.printf("[텔레메트리] 실측 메트릭 전송 완료 (MsgID=%u, topic=2)\n"
                  "  rtt=%.2f ms | retry=%d | sleep_r=%.4f | pkt=%u | bytes=%u\n",
                  telemetry_pkt.msg_id,
                  rtt_ms, retry_count, sleep_mode_ratio,
                  g_packet_count, g_total_bytes);

  } else {
    // 최대 재전송 횟수 초과 후에도 확인 응답이 없는 경우
    // 다음 루프에서 새로운 msg_id로 재시도됩니다.
    Serial.printf("[루프] X QoS %d 전송 최종 실패 -- 재전송 한도(%d회) 초과 (MsgID: %u)\n",
                  (int)selected_qos, max_retries, pub_pkt.msg_id);
  }

  // ┌─────────────────────────────────────────────────────────────────────┐
  // │ 7단계: 저전력 Sleep 동기화 — DISCONNECT(Sleep) 패킷 전송            │
  // │ 게이트웨이에 수면 진입을 알려 불필요한 패킷 수신을 차단합니다.       │
  // └─────────────────────────────────────────────────────────────────────┘
  DisconnectPacket disc_pkt;
  disc_pkt.header.length   = sizeof(DisconnectPacket);
  disc_pkt.header.msg_type = MsgType::DISCONNECT;
  disc_pkt.sleep_mode_flag = 1; // Sleep 진입 플래그: 게이트웨이가 수신 버퍼링 시작

  udp.beginPacket(UDP_SERVER_IP, UDP_SERVER_PORT);
  udp.write((uint8_t *)&disc_pkt, sizeof(disc_pkt));
  udp.endPacket();
  Serial.printf("[루프] DISCONNECT(Sleep) 전송 완료 — %.1f초 후 다음 사이클 시작\n",
                5000.0f / 1000.0f);

  // ── Sleep 시간 누적 (delay 기반 소프트웨어 Sleep 추정) ─────────────────
  // 실제 esp_deep_sleep_start() 사용 시 이 지점 이후 코드는 실행되지 않으며,
  // Deep Sleep에서 깨어나면 setup()부터 재시작됩니다.
  // 현재는 delay()로 경량 시뮬레이션 Sleep을 사용합니다.
  g_total_sleep_ms += SLEEP_DURATION_MS;

  // 배포 환경에서는 delay()를 esp_deep_sleep_start()로 교체하면
  // 수면 중 전력 소모를 ~10μA 수준으로 절감할 수 있습니다.
  // 예시) esp_sleep_enable_timer_wakeup(5ULL * 1000000ULL);
  //       esp_deep_sleep_start();
  delay(SLEEP_DURATION_MS);
}