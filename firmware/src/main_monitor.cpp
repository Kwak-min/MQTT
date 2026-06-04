/*
 * firmware/src/main_monitor.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 코드명 : Power Monitor  (Board 3 — 전력 계측 서비스)
 * 타겟 하드웨어   : ESP32-S3 DevKitC-1
 * 역할           : INA219 이중 채널 전력 계측 → WiFiUDP 비동기 스트리밍
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │                       아키텍처 개요 (Architecture Overview)              │
 * ├─────────────────────────────────────────────────────────────────────────┤
 * │ Board 3의 역할은 Board 1(Gingerbread)과 Board 2(Standard MQTT)의        │
 * │ 실시간 소비 전력을 독립적으로 계측하여 비교 분석 데이터를 제공하는 것입니다. │
 * │                                                                         │
 * │  ① INA219 이중 채널 구성                                                 │
 * │     - 공유 I2C 버스: SDA = GPIO 8, SCL = GPIO 9 (ESP32-S3 하드웨어 I2C) │
 * │     - 센서 #1 (I2C 주소 0x40) : Board 1 (Gingerbread) 전력 계측         │
 * │     - 센서 #2 (I2C 주소 0x41) : Board 2 (Standard MQTT) 전력 계측       │
 * │     - 하드웨어 주소 분리:                                                 │
 * │         0x40 → A1=GND, A0=GND (기본 주소)                               │
 * │         0x41 → A1=GND, A0=VCC (A0 핀 HIGH)                             │
 * │                                                                         │
 * │  ② 주기적 샘플링                                                         │
 * │     - 기본 샘플링 인터벌: 1,000ms (1초)                                   │
 * │     - 두 채널을 순차적으로 읽어 단일 JSON 페이로드로 조합합니다.           │
 * │                                                                         │
 * │  ③ WiFiUDP 비동기 스트리밍                                               │
 * │     - 게이트웨이(라즈베리파이 5)로 UDP 패킷을 비동기 전송합니다.           │
 * │     - MQTT 핸드셰이크 없음: 최소 지연으로 전력 데이터를 스트리밍합니다.    │
 * │     - 클라이언트 ID: "ESP32-Power-Monitor"                               │
 * │                                                                         │
 * │  ④ 측정값 구성 (채널당)                                                   │
 * │     - shunt_mv   : 션트 저항 양단 전압 (mV)                              │
 * │     - current_ma : 전류 (mA)                                             │
 * │     - power_mw   : 소비 전력 (mW)                                        │
 * │     - bus_v      : 버스 전압 (V)                                          │
 * └─────────────────────────────────────────────────────────────────────────┘
 *
 * 의존 라이브러리 (platformio.ini [env:board3_monitor] 참조):
 *   - adafruit/Adafruit INA219 @ ^1.2  : INA219 전류/전력 센서 드라이버
 * ═══════════════════════════════════════════════════════════════════════════
 */

/* ─── 라이브러리 헤더 인클루드 ─────────────────────────────────────────── */
#include <Arduino.h>              // Arduino 프레임워크 기본 함수
#include <Adafruit_INA219.h>      // INA219 전류/전력 센서 드라이버
#include <Wire.h>                 // I2C 버스 마스터 (SDA/SCL 설정 포함)
#include <WiFi.h>                 // Wi-Fi 연결 관리
#include <WiFiUdp.h>              // UDP 비동기 스트리밍 소켓

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A] 네트워크 자격증명 및 UDP 게이트웨이 엔드포인트 설정
 * ═══════════════════════════════════════════════════════════════════════════ */
/* Wi-Fi 접속 정보: 실제 배포 환경에 맞게 변경하세요. */
static const char    *WIFI_SSID        = "YOUR_WIFI_SSID";     // Wi-Fi 네트워크 SSID
static const char    *WIFI_PASSWORD    = "YOUR_WIFI_PASSWORD";  // Wi-Fi 비밀번호

/* UDP 게이트웨이 엔드포인트 (라즈베리파이 5 전력 모니터 수신 서버)
 * 전력 데이터 전용 포트를 별도로 사용하여 Board 1/2의 UDP 트래픽과 충돌을 방지합니다. */
static const char    *UDP_GATEWAY_IP   = "192.168.0.100";  // 게이트웨이 IP 주소
static const uint16_t UDP_GATEWAY_PORT = 5001;             // 전력 모니터 전용 UDP 포트

/* Board 3 식별자: UDP 페이로드의 "client_id" 필드에 포함됩니다.
 * 게이트웨이에서 전력 계측 스트림을 다른 보드의 데이터와 구별합니다. */
static const char *BOARD3_CLIENT_ID = "ESP32-Power-Monitor";

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 B] I2C 버스 핀 정의 및 INA219 I2C 주소
 *
 * ESP32-S3의 두 번째 I2C 버스(Wire1)를 사용하거나,
 * 기본 Wire 버스의 핀을 재정의하여 GPIO 8(SDA), GPIO 9(SCL)로 설정합니다.
 *
 * INA219 I2C 주소 결정 방법 (A0, A1 핀 배선):
 *   0x40: A0=GND, A1=GND  (기본 주소 — Board 1 계측용 센서 #1)
 *   0x41: A0=VCC, A1=GND  (A0 HIGH  — Board 2 계측용 센서 #2)
 *   0x44: A0=GND, A1=VCC
 *   0x45: A0=VCC, A1=VCC
 * ═══════════════════════════════════════════════════════════════════════════ */
/* I2C 버스 핀 번호: 요구사항에 따라 GPIO 8(SDA), GPIO 9(SCL)로 설정 */
static const int I2C_SDA_PIN = 8;   // I2C 데이터 라인 (SDA)
static const int I2C_SCL_PIN = 9;   // I2C 클럭 라인 (SCL)

/* INA219 센서 I2C 주소
 * 센서 #1 (0x40): Board 1 Gingerbread 전력 계측
 * 센서 #2 (0x41): Board 2 Standard MQTT 전력 계측 */
static const uint8_t INA219_ADDR_BOARD1 = 0x40; // A0=GND, A1=GND — Board 1용
static const uint8_t INA219_ADDR_BOARD2 = 0x41; // A0=VCC, A1=GND — Board 2용

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 C] 샘플링 타이밍 설정
 * ═══════════════════════════════════════════════════════════════════════════ */
/* INA219 샘플링 인터벌: 1,000ms(1초)마다 두 채널을 모두 읽습니다.
 * 이 값을 낮추면 더 높은 시간 해상도를 얻을 수 있습니다. */
static const unsigned long SAMPLE_INTERVAL_MS = 1000UL;

/* ─── INA219 센서 인스턴스 ──────────────────────────────────────────────── */
/* 두 INA219를 동일한 I2C 버스의 서로 다른 주소로 초기화합니다. */
static Adafruit_INA219 ina219_board1(INA219_ADDR_BOARD1); // Board 1 계측 센서 (0x40)
static Adafruit_INA219 ina219_board2(INA219_ADDR_BOARD2); // Board 2 계측 센서 (0x41)

/* ─── 통신 객체 인스턴스 ────────────────────────────────────────────────── */
static WiFiUDP udp; // UDP 비동기 스트리밍 전용 소켓

/* ─── 샘플링 타이밍 추적 변수 ───────────────────────────────────────────── */
static unsigned long last_sample_ms = 0; // 마지막 샘플링 시각 (millis 기준)

/* ─── INA219 초기화 상태 플래그 ─────────────────────────────────────────── */
/* 센서 미연결 시 시뮬레이션 값으로 폴백하여 펌웨어 흐름을 유지합니다. */
static bool ina219_board1_ok = false; // Board 1 센서 초기화 성공 여부
static bool ina219_board2_ok = false; // Board 2 센서 초기화 성공 여부

/* ─── 누적 통계 카운터 ──────────────────────────────────────────────────── */
static uint32_t sample_count = 0; // 총 샘플링 횟수 (시퀀스 번호로도 사용)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 D] 전력 측정값 구조체
 *
 * INA219 단일 채널에서 읽어낸 전력 관련 측정값 묶음입니다.
 * 두 채널(Board 1, Board 2)을 각각 이 구조체로 표현합니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
struct PowerReading {
  float shunt_mv;    // 션트 저항 양단 전압 (mV) — 전류 측정의 기반 값
  float current_ma;  // 부하 전류 (mA, INA219 캘리브레이션 기반 계산값)
  float power_mw;    // 소비 전력 (mW = current_mA × bus_V)
  float bus_v;       // 버스 전압 (V) — INA219 V+ 단자와 GND 사이의 전압
  bool  valid;       // 측정값 유효 여부 (센서 통신 오류 시 false)
};

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 E] INA219 단일 채널 측정 함수 — read_ina219()
 *
 * 지정된 INA219 인스턴스에서 션트 전압, 전류, 전력, 버스 전압을 읽습니다.
 * INA219 라이브러리가 내부적으로 I2C 통신을 처리합니다.
 *
 * 매개변수:
 *   sensor - 읽을 INA219 인스턴스의 참조
 *   label  - 시리얼 로깅용 채널 레이블 (예: "Board1", "Board2")
 *
 * 반환값: PowerReading 구조체 (valid 필드로 성공/실패 구별)
 * ═══════════════════════════════════════════════════════════════════════════ */
static PowerReading read_ina219(Adafruit_INA219 &sensor, const char *label) {
  PowerReading reading;

  /* INA219에서 측정값 읽기
   * 라이브러리 내부에서 I2C 통신으로 레지스터 값을 읽어 단위 변환합니다. */
  reading.shunt_mv   = sensor.getShuntVoltage_mV(); // 션트 전압 (mV)
  reading.current_ma = sensor.getCurrent_mA();       // 부하 전류 (mA)
  reading.power_mw   = sensor.getPower_mW();         // 소비 전력 (mW)
  reading.bus_v      = sensor.getBusVoltage_V();     // 버스 전압 (V)

  /* 유효성 검사: 전류값이 비정상적으로 큰 경우 센서 오류로 판정합니다.
   * INA219의 측정 범위는 ±3.2A (기본 캘리브레이션 기준)입니다. */
  reading.valid = (reading.current_ma > -3200.0f && reading.current_ma < 3200.0f);

  if (reading.valid) {
    /* 유효한 측정값: 시리얼 모니터에 상세 출력합니다. */
    Serial.printf("[INA219][%s] 션트=%.2f mV | 전류=%.2f mA | "
                  "전력=%.2f mW | 버스=%.3f V\n",
                  label,
                  reading.shunt_mv,
                  reading.current_ma,
                  reading.power_mw,
                  reading.bus_v);
  } else {
    /* 측정값 범위 초과: 센서 연결 상태 또는 캘리브레이션을 확인하세요. */
    Serial.printf("[INA219][%s] ⚠ 유효하지 않은 측정값 — 센서 오류 가능성\n", label);
  }

  return reading;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 F] UDP 전력 데이터 스트리밍 함수 — stream_power_data()
 *
 * 두 채널의 전력 측정값을 단일 JSON 페이로드로 조합하여
 * WiFiUDP를 통해 게이트웨이로 비동기 전송합니다.
 *
 * 전송 JSON 구조:
 * {
 *   "client_id" : "ESP32-Power-Monitor",
 *   "seq"       : <시퀀스 번호>,
 *   "board1": {
 *     "current_ma": ..., "power_mw": ..., "bus_v": ..., "shunt_mv": ...
 *   },
 *   "board2": {
 *     "current_ma": ..., "power_mw": ..., "bus_v": ..., "shunt_mv": ...
 *   }
 * }
 *
 * 매개변수:
 *   r1  - Board 1 채널 전력 측정값 (INA219 #1, 0x40)
 *   r2  - Board 2 채널 전력 측정값 (INA219 #2, 0x41)
 *   seq - 현재 샘플링 시퀀스 번호
 * ═══════════════════════════════════════════════════════════════════════════ */
static void stream_power_data(const PowerReading &r1,
                               const PowerReading &r2,
                               uint32_t seq) {
  /* JSON 페이로드 버퍼: 두 채널의 4개 필드 × 2 + 메타데이터 = 최대 256바이트 */
  char payload[256];

  /* snprintf로 JSON 페이로드를 직접 포맷합니다.
   * ArduinoJson을 사용하지 않아 라이브러리 오버헤드를 최소화합니다.
   * 센서 오류 시 해당 필드에 -1을 기입하여 게이트웨이가 무효값을 식별하게 합니다. */
  snprintf(payload, sizeof(payload),
           "{"
             "\"client_id\":\"%s\","
             "\"seq\":%u,"
             "\"board1\":{"
               "\"current_ma\":%.2f,"
               "\"power_mw\":%.2f,"
               "\"bus_v\":%.3f,"
               "\"shunt_mv\":%.2f,"
               "\"valid\":%s"
             "},"
             "\"board2\":{"
               "\"current_ma\":%.2f,"
               "\"power_mw\":%.2f,"
               "\"bus_v\":%.3f,"
               "\"shunt_mv\":%.2f,"
               "\"valid\":%s"
             "}"
           "}",
           BOARD3_CLIENT_ID,            // 클라이언트 식별자
           seq,                         // 샘플링 시퀀스 번호
           r1.valid ? r1.current_ma : -1.0f,
           r1.valid ? r1.power_mw   : -1.0f,
           r1.valid ? r1.bus_v      : -1.0f,
           r1.valid ? r1.shunt_mv   : -1.0f,
           r1.valid ? "true" : "false", // Board 1 측정값 유효 여부
           r2.valid ? r2.current_ma : -1.0f,
           r2.valid ? r2.power_mw   : -1.0f,
           r2.valid ? r2.bus_v      : -1.0f,
           r2.valid ? r2.shunt_mv   : -1.0f,
           r2.valid ? "true" : "false"  // Board 2 측정값 유효 여부
  );

  /* UDP 패킷 전송: beginPacket → write → endPacket 순서로 단일 데이터그램 전송 */
  udp.beginPacket(UDP_GATEWAY_IP, UDP_GATEWAY_PORT);
  udp.write((const uint8_t *)payload, strlen(payload));

  if (udp.endPacket()) {
    /* 전송 성공: 페이로드 내용과 목적지를 로깅합니다. */
    Serial.printf("[UDP] ✓ 전력 데이터 전송 완료 (seq=%u) → %s:%u\n",
                  seq, UDP_GATEWAY_IP, UDP_GATEWAY_PORT);
    Serial.printf("[UDP]   페이로드(%u bytes): %s\n",
                  (unsigned)strlen(payload), payload);
  } else {
    /* 전송 실패: 네트워크 단절 또는 게이트웨이 응답 없음 */
    Serial.printf("[UDP] ✗ 전력 데이터 전송 실패 (seq=%u)\n", seq);
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * setup() — Arduino 초기화 진입점
 *
 * 실행 순서:
 *   1. 시리얼 모니터 초기화
 *   2. I2C 버스 초기화 (SDA=GPIO 8, SCL=GPIO 9)
 *   3. INA219 #1 (0x40) 초기화 — Board 1 계측용
 *   4. INA219 #2 (0x41) 초기화 — Board 2 계측용
 *   5. Wi-Fi 연결 대기
 *   6. UDP 소켓 초기화
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  /* 1단계: 시리얼 모니터 초기화 */
  Serial.begin(115200);
  delay(100); // USB-시리얼 초기화 안정화 대기
  Serial.println("\n╔══════════════════════════════════════════════════════════╗");
  Serial.println("║  Board 3: Power Monitor — ESP32-S3 전력 계측 부팅 시작   ║");
  Serial.println("╚══════════════════════════════════════════════════════════╝");

  Serial.printf("[설정] I2C 버스: SDA=GPIO%d, SCL=GPIO%d\n",
                I2C_SDA_PIN, I2C_SCL_PIN);
  Serial.printf("[설정] 센서 #1: INA219 @ 0x%02X (Board 1 계측)\n", INA219_ADDR_BOARD1);
  Serial.printf("[설정] 센서 #2: INA219 @ 0x%02X (Board 2 계측)\n", INA219_ADDR_BOARD2);
  Serial.printf("[설정] 샘플링 인터벌: %lu ms | 클라이언트 ID: %s\n",
                SAMPLE_INTERVAL_MS, BOARD3_CLIENT_ID);

  /* 2단계: I2C 버스 초기화
   * Wire.begin(SDA, SCL)로 ESP32-S3의 기본 I2C 핀을 GPIO 8/9로 재정의합니다.
   * 두 INA219는 동일한 I2C 버스를 서로 다른 주소(0x40, 0x41)로 공유합니다. */
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Wire.setTimeOut(50); // I2C 버스 록업 방지 및 통신 안정성 확보
  Serial.printf("[부팅] ✓ I2C 버스 초기화 완료 (SDA=GPIO%d, SCL=GPIO%d)\n",
                I2C_SDA_PIN, I2C_SCL_PIN);

  /* 3단계: INA219 #1 초기화 (0x40, Board 1 계측용)
   * begin()은 내부적으로 I2C 통신 테스트를 수행합니다.
   * 실패 시 해당 채널을 시뮬레이션 모드로 전환합니다. */
  if (ina219_board1.begin(&Wire)) {
    /* INA219 캘리브레이션: 최대 전류 범위 및 션트 저항값 설정
     * setCalibration_32V_2A(): 버스 전압 최대 32V, 전류 최대 2A 범위 */
    ina219_board1.setCalibration_32V_2A();
    ina219_board1_ok = true;
    Serial.printf("[부팅] ✓ INA219 #1 초기화 완료 (0x%02X — Board 1 계측, 32V/2A 캘리브레이션)\n",
                  INA219_ADDR_BOARD1);
  } else {
    ina219_board1_ok = false;
    Serial.printf("[부팅] ⚠ INA219 #1 미검출 (0x%02X) — 시뮬레이션 값으로 동작\n",
                  INA219_ADDR_BOARD1);
  }

  /* 4단계: INA219 #2 초기화 (0x41, Board 2 계측용) */
  if (ina219_board2.begin(&Wire)) {
    /* Board 2는 저전력 모드에서 동작하므로 더 민감한 캘리브레이션을 적용합니다.
     * setCalibration_32V_1A(): 버스 전압 최대 32V, 전류 최대 1A 범위 (더 높은 분해능) */
    ina219_board2.setCalibration_32V_1A();
    ina219_board2_ok = true;
    Serial.printf("[부팅] ✓ INA219 #2 초기화 완료 (0x%02X — Board 2 계측, 32V/1A 캘리브레이션)\n",
                  INA219_ADDR_BOARD2);
  } else {
    ina219_board2_ok = false;
    Serial.printf("[부팅] ⚠ INA219 #2 미검출 (0x%02X) — 시뮬레이션 값으로 동작\n",
                  INA219_ADDR_BOARD2);
  }

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

  /* 6단계: UDP 소켓 초기화
   * 로컬 수신 포트를 바인딩합니다.
   * Board 3은 단방향 스트리밍만 수행하므로 응답 수신은 필요하지 않습니다. */
  udp.begin(UDP_GATEWAY_PORT);
  Serial.printf("[부팅] ✓ UDP 소켓 초기화 완료 (포트 %u)\n", UDP_GATEWAY_PORT);

  /* 마지막 샘플링 시각을 현재 시각 - 인터벌로 설정하여
   * setup() 직후 첫 번째 샘플링이 즉시 발생하도록 합니다. */
  last_sample_ms = millis() - SAMPLE_INTERVAL_MS;

  Serial.println("[부팅] ══ 초기화 완료, INA219 이중 채널 전력 모니터링 시작 ══\n");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * loop() — Arduino 메인 루프
 *
 * 매 루프 사이클 실행 순서:
 *   1. 샘플링 인터벌 확인 (1초 경과 여부)
 *   2. INA219 #1 (0x40) 측정 — Board 1 전력 데이터 수집
 *   3. INA219 #2 (0x41) 측정 — Board 2 전력 데이터 수집
 *   4. JSON 페이로드 조합 및 WiFiUDP 비동기 스트리밍
 *   5. 통계 카운터 갱신 및 진단 요약 출력
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  /* ──────────────────────────────────────────────────────────────────────
   * Wi-Fi 연결 상태 확인 및 재연결
   * Board 3은 MQTT를 사용하지 않으므로 WiFi 상태만 확인합니다.
   * ────────────────────────────────────────────────────────────────────── */
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[루프] ⚠ Wi-Fi 연결 끊김 — 재연결 시도");
    WiFi.reconnect();
    delay(1000); // 재연결 시도 후 1초 대기
    return;
  }

  /* ──────────────────────────────────────────────────────────────────────
   * 1단계: 샘플링 인터벌 타이머 확인
   * millis() 오버플로에 안전한 부호 없는 정수 연산을 사용합니다.
   * ────────────────────────────────────────────────────────────────────── */
  unsigned long now = millis();
  if (now - last_sample_ms < SAMPLE_INTERVAL_MS) {
    /* 인터벌 미경과: 짧은 대기 후 루프를 즉시 반복합니다. */
    delay(10);
    return;
  }

  /* 타이머 갱신 및 시퀀스 번호 증가 */
  last_sample_ms = now;
  sample_count++;

  Serial.printf("\n[루프] ── 전력 샘플링 사이클 #%u (%lu ms) ──\n",
                sample_count, now);

  /* ──────────────────────────────────────────────────────────────────────
   * 2단계: INA219 #1 (0x40) 측정 — Board 1 (Gingerbread) 전력 데이터
   * 센서 미연결 시 모의 값(시뮬레이션)을 반환합니다.
   * ────────────────────────────────────────────────────────────────────── */
  PowerReading r1;

  if (ina219_board1_ok) {
    /* 실제 INA219 #1 하드웨어 측정 */
    r1 = read_ina219(ina219_board1, "Board1@0x40");
  } else {
    /* 센서 미연결 시 시뮬레이션 값: Board 1의 일반적인 IoT 노드 소비 전력 근사값 */
    r1.shunt_mv   = 5.0f;    // 5mV 션트 전압 (시뮬레이션)
    r1.current_ma = 50.0f;   // 50mA 소비 전류 (시뮬레이션)
    r1.power_mw   = 165.0f;  // 165mW 소비 전력 (3.3V × 50mA, 시뮬레이션)
    r1.bus_v      = 3.3f;    // 3.3V 버스 전압 (시뮬레이션)
    r1.valid      = true;    // 시뮬레이션 값이므로 유효로 표시
    Serial.println("[INA219][Board1] ⚠ 시뮬레이션 모드 (50mA, 165mW, 3.3V)");
  }

  /* ──────────────────────────────────────────────────────────────────────
   * 3단계: INA219 #2 (0x41) 측정 — Board 2 (Standard MQTT) 전력 데이터
   * ────────────────────────────────────────────────────────────────────── */
  PowerReading r2;

  if (ina219_board2_ok) {
    /* 실제 INA219 #2 하드웨어 측정 */
    r2 = read_ina219(ina219_board2, "Board2@0x41");
  } else {
    /* 센서 미연결 시 시뮬레이션 값: Board 2는 AI 추론이 없어 더 낮은 소비 전력 */
    r2.shunt_mv   = 3.5f;    // 3.5mV 션트 전압 (시뮬레이션)
    r2.current_ma = 35.0f;   // 35mA 소비 전류 (시뮬레이션)
    r2.power_mw   = 115.5f;  // 115.5mW 소비 전력 (3.3V × 35mA, 시뮬레이션)
    r2.bus_v      = 3.3f;    // 3.3V 버스 전압 (시뮬레이션)
    r2.valid      = true;    // 시뮬레이션 값이므로 유효로 표시
    Serial.println("[INA219][Board2] ⚠ 시뮬레이션 모드 (35mA, 115.5mW, 3.3V)");
  }

  /* ──────────────────────────────────────────────────────────────────────
   * 4단계: JSON 페이로드 조합 및 WiFiUDP 비동기 스트리밍
   * 두 채널의 측정값을 단일 JSON 데이터그램으로 전송합니다.
   * ────────────────────────────────────────────────────────────────────── */
  stream_power_data(r1, r2, sample_count);

  /* ──────────────────────────────────────────────────────────────────────
   * 5단계: 샘플링 통계 진단 요약 출력
   * 두 채널의 전력 차이를 계산하여 보드 간 성능 비교를 즉각 확인합니다.
   * ────────────────────────────────────────────────────────────────────── */
  float diff_mw = 0.0f;
  if (r1.valid && r2.valid) {
    diff_mw = r1.power_mw - r2.power_mw; // 양수: Board 1이 더 많은 전력 소비
  }

  Serial.printf("[통계] 샘플 #%u — Board1: %.2f mW | Board2: %.2f mW | 차이: %+.2f mW\n",
                sample_count,
                r1.valid ? r1.power_mw : -1.0f,
                r2.valid ? r2.power_mw : -1.0f,
                diff_mw);

  if (diff_mw > 0) {
    /* Board 1이 Board 2보다 더 많은 전력을 소비하는 경우
     * AI 추론, MQTT 설정 구독, QoS 2 핸드셰이크 등의 추가 부하가 원인입니다. */
    Serial.printf("[통계]   → Board 1이 Board 2보다 %.2f mW 더 소비 (AI 추론 부하 포함)\n",
                  diff_mw);
  } else if (diff_mw < 0) {
    /* Board 2가 Board 1보다 더 많은 전력을 소비하는 경우 (예상 외) */
    Serial.printf("[통계]   → Board 2가 Board 1보다 %.2f mW 더 소비 (예상 외 상황)\n",
                  -diff_mw);
  } else {
    /* 두 보드의 소비 전력이 동일한 경우 */
    Serial.println("[통계]   → 두 보드의 소비 전력이 동일합니다.");
  }
}
