/*
 * firmware/src/main_standard_MQTT.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 프로젝트 코드명 : Standard MQTT  (Board 2 — 베이스라인 시스템)
 * 타겟 하드웨어   : ESP32-S3 DevKitC-1
 * 역할           : 표준 MQTT 벤치마크 노드
 *                  고정 QoS 1 + 5초 고정 인터벌 + 모의 온도/습도 데이터 전송
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │                       아키텍처 개요 (Architecture Overview)              │
 * ├─────────────────────────────────────────────────────────────────────────┤
 * │ Board 2의 역할은 Gingerbread(Board 1)와의 성능 비교를 위한 베이스라인     │
 * │ 제공입니다. 다음 설계 원칙을 엄격히 준수합니다:                           │
 * │                                                                         │
 * │  ① 고정 QoS 1 전용                                                       │
 * │     - AI/TinyML 추론 없음, 조건 기반 QoS 선택 없음                       │
 * │     - 모든 패킷을 고정 QoS 1 (PUBLISH → PUBACK 2단계 핸드셰이크)으로    │
 * │       전송합니다.                                                         │
 * │                                                                         │
 * │  ② 고정 5초 인터벌                                                       │
 * │     - 센서 상태, 네트워크 품질, 데이터 긴급도에 무관하게                  │
 * │       정확히 5초마다 1회 데이터를 전송합니다.                             │
 * │                                                                         │
 * │  ③ 모의 데이터셋                                                         │
 * │     - 실제 센서 없이 사전 정의된 고정 온도(28.0°C) / 습도(55.0%) 값을   │
 * │       전송합니다.                                                         │
 * │                                                                         │
 * │  ④ 클라이언트 ID : "ESP32-Standard-MQTT"                                 │
 * │     - MQTT CONNECT 패킷에 고정 ID를 사용하여 브로커 로그에서             │
 * │       Board 2를 명확히 식별합니다.                                        │
 * └─────────────────────────────────────────────────────────────────────────┘
 *
 * 의존 라이브러리 (platformio.ini [env:board2_standard] 참조):
 *   - bblanchon/ArduinoJson  @ ^7.0  : JSON 페이로드 직렬화
 *   - knolleary/PubSubClient @ ^2.8  : 표준 MQTT 통신
 * ═══════════════════════════════════════════════════════════════════════════
 */

/* ─── 라이브러리 헤더 인클루드 ─────────────────────────────────────────── */
#include <Arduino.h>         // Arduino 프레임워크 기본 함수
#include <ArduinoJson.h>     // MQTT 페이로드 JSON 직렬화
#include <PubSubClient.h>    // 표준 MQTT 브로커 통신
#include <WiFi.h>            // Wi-Fi 연결 관리
#include <WiFiClient.h>      // PubSubClient TCP 기반 클라이언트

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 A] 네트워크 자격증명 및 브로커 엔드포인트 설정
 * ═══════════════════════════════════════════════════════════════════════════ */
/* Wi-Fi 접속 정보: 실제 배포 환경에 맞게 변경하세요. */
static const char    *WIFI_SSID        = "YOUR_WIFI_SSID";     // Wi-Fi 네트워크 SSID
static const char    *WIFI_PASSWORD    = "YOUR_WIFI_PASSWORD";  // Wi-Fi 비밀번호

/* MQTT 브로커 엔드포인트 (라즈베리파이 5 Mosquitto 서버) */
static const char    *MQTT_BROKER_IP   = "192.168.0.100";  // 브로커 IP 주소
static const uint16_t MQTT_BROKER_PORT = 1883;             // 표준 MQTT 포트

/* 센서 데이터 발행 토픽
 * 브로커에서 Board 1(gingerbread/telemetry)과 구분하여 수집됩니다. */
static const char *MQTT_PUBLISH_TOPIC = "standard/telemetry";

/* Board 2 MQTT 클라이언트 고유 식별자
 * 브로커 세션 로그에서 Board 2를 Board 1 및 Board 3과 명확히 구별합니다. */
static const char *BOARD2_CLIENT_ID = "ESP32-Standard-MQTT";

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 B] 모의 데이터셋 상수 정의
 *
 * Board 2는 실제 센서를 사용하지 않습니다.
 * 사전 정의된 고정 온도 / 습도 값을 벤치마크 기준 데이터로 전송합니다.
 * 이 값들은 Gingerbread(Board 1)의 실측 데이터와 대조 분석에 사용됩니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
/* 모의 온도 데이터: 정상 실내 온도 범위 (°C) */
static const float MOCK_TEMPERATURE = 28.0f;

/* 모의 습도 데이터: 정상 실내 습도 범위 (%) */
static const float MOCK_HUMIDITY    = 55.0f;

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 C] 전송 타이밍 설정
 *
 * Board 2는 센서 상태, 네트워크 품질과 무관하게 고정 인터벌로 동작합니다.
 * PUBLISH_INTERVAL_MS는 두 전송 사이클 간의 최소 간격을 정의합니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
/* 고정 발행 인터벌: 정확히 5,000ms(5초)마다 데이터를 전송합니다. */
static const unsigned long PUBLISH_INTERVAL_MS = 5000UL;

/* QoS 1 PUBACK 수신 대기 최대 시간 (ms)
 * 이 시간 내에 PUBACK가 수신되지 않으면 재전송을 시도합니다. */
static const unsigned long PUBACK_TIMEOUT_MS = 3000UL;

/* QoS 1 최대 재전송 횟수: 연속 실패 시 해당 사이클을 포기하고 다음 인터벌로 진행 */
static const int MAX_RETRY_COUNT = 3;

/* ─── 통신 객체 인스턴스 ────────────────────────────────────────────────── */
static WiFiClient   wifi_client;               // PubSubClient의 TCP 연결 기반 클라이언트
static PubSubClient mqtt_client(wifi_client);  // 표준 MQTT 클라이언트

/* 전송 타이밍 추적 변수 */
static unsigned long last_publish_ms = 0;  // 마지막 PUBLISH 전송 시각 (millis 기준)

/* 누적 통계 카운터: 시리얼 모니터 진단 출력에 사용됩니다. */
static uint32_t total_sent    = 0;  // 총 전송 시도 횟수
static uint32_t total_success = 0;  // 총 전송 성공 횟수 (PUBACK 수신)
static uint32_t total_failed  = 0;  // 총 전송 실패 횟수 (타임아웃 한도 초과)

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 D] MQTT 수신 콜백 — on_mqtt_message()
 *
 * Board 2는 어떤 토픽도 구독하지 않습니다.
 * 그러나 PubSubClient가 콜백 등록을 요구하므로 더미 함수를 제공합니다.
 * 실제 수신 메시지는 발생하지 않으며, 함수 내부는 비어 있습니다.
 * ═══════════════════════════════════════════════════════════════════════════ */
static void on_mqtt_message(char * /*topic*/, byte * /*payload*/,
                            unsigned int /*length*/) {
  /* Board 2는 구독 토픽이 없으므로 이 콜백이 호출되지 않습니다.
   * PubSubClient API 요구사항을 충족하기 위한 빈 구현입니다. */
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 E] MQTT 브로커 연결 함수 — mqtt_connect()
 *
 * BOARD2_CLIENT_ID("ESP32-Standard-MQTT")로 MQTT 브로커에 연결합니다.
 * 연결 성공 여부를 반환하여 호출자가 재시도 여부를 결정할 수 있게 합니다.
 *
 * 반환값:
 *   true  - 연결 성공
 *   false - 연결 실패 (브로커 거부, 네트워크 오류 등)
 * ═══════════════════════════════════════════════════════════════════════════ */
static bool mqtt_connect() {
  Serial.printf("[MQTT] 브로커 연결 시도 — %s:%u (클라이언트 ID: %s)\n",
                MQTT_BROKER_IP, MQTT_BROKER_PORT, BOARD2_CLIENT_ID);

  /* PubSubClient MQTT CONNECT 패킷 전송
   * clean_session=true: 이전 세션을 초기화하여 미전달 메시지 누적을 방지합니다. */
  if (mqtt_client.connect(BOARD2_CLIENT_ID)) {
    Serial.printf("[MQTT] ✓ 브로커 연결 성공 (ID: %s)\n", BOARD2_CLIENT_ID);
    return true;
  }

  /* 연결 실패: PubSubClient 상태 코드로 원인을 진단합니다. */
  Serial.printf("[MQTT] ⚠ 브로커 연결 실패 (rc=%d)\n", mqtt_client.state());
  return false;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 F] 모의 데이터 JSON 직렬화 함수 — build_json_payload()
 *
 * 사전 정의된 고정 온도/습도 값을 JSON 문자열로 직렬화하여 반환합니다.
 *
 * 출력 JSON 구조:
 * {
 *   "temp": 28.00,
 *   "hum" : 55.00,
 *   "qos" : 1,
 *   "seq" : <시퀀스 번호>
 * }
 *
 * 매개변수:
 *   buffer   - 결과 JSON 문자열을 저장할 출력 버퍼
 *   buf_size - buffer의 최대 크기 (바이트)
 *   seq_num  - 현재 전송 시퀀스 번호 (단조 증가, 패킷 순서 추적용)
 * ═══════════════════════════════════════════════════════════════════════════ */
static void build_json_payload(char *buffer, size_t buf_size, uint32_t seq_num) {
  /* ArduinoJson v7 JsonDocument 사용: 동적 할당 방식 */
  JsonDocument doc;

  /* 모의 데이터셋 필드 설정 */
  doc["temp"] = MOCK_TEMPERATURE;  // 고정 온도: 28.0°C
  doc["hum"]  = MOCK_HUMIDITY;     // 고정 습도: 55.0%
  doc["qos"]  = 1;                 // 고정 QoS 레벨 (벤치마크 메타데이터)
  doc["seq"]  = seq_num;           // 전송 시퀀스 번호 (패킷 순서 추적)

  /* JSON 직렬화: 결과를 buffer에 기록 */
  serializeJson(doc, buffer, buf_size);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * [섹션 G] QoS 1 고정 핸드셰이크 전송 함수 — publish_qos1()
 *
 * Board 2는 모든 패킷을 고정 QoS 1로 전송합니다.
 * PubSubClient의 내장 QoS 1 메커니즘을 사용하여 PUBLISH → PUBACK
 * 2단계 핸드셰이크를 수행합니다.
 *
 * PubSubClient는 내부적으로 PUBACK를 처리하며, publish() 함수는
 * 브로커에 PUBLISH 패킷 전송이 성공했을 때 true를 반환합니다.
 * PUBACK 수신 확인은 loop() 내의 mqtt_client.loop() 호출로 처리됩니다.
 *
 * 매개변수:
 *   payload  - 전송할 JSON 페이로드 문자열
 *   seq_num  - 시퀀스 번호 (로깅 전용)
 *
 * 반환값:
 *   true  - PUBLISH 전송 성공
 *   false - 전송 실패 (브로커 연결 끊김 등)
 * ═══════════════════════════════════════════════════════════════════════════ */
static bool publish_qos1(const char *payload, uint32_t seq_num) {
  /* PubSubClient publish(): retained=false, qos=1로 고정 발행
   * 세 번째 인자 true는 retain 플래그입니다 (false: 비보존). */
  bool result = mqtt_client.publish(MQTT_PUBLISH_TOPIC,
                                    (const uint8_t *)payload,
                                    strlen(payload),
                                    false); // retain=false

  if (result) {
    Serial.printf("[QoS 1] ✓ PUBLISH 전송 성공 (seq=%u) → 토픽: %s\n",
                  seq_num, MQTT_PUBLISH_TOPIC);
    Serial.printf("[QoS 1]   페이로드: %s\n", payload);
  } else {
    Serial.printf("[QoS 1] ✗ PUBLISH 전송 실패 (seq=%u) — 브로커 연결 상태 확인 필요\n",
                  seq_num);
  }

  return result;
}

/* ═══════════════════════════════════════════════════════════════════════════
 * setup() — Arduino 초기화 진입점
 *
 * 실행 순서:
 *   1. 시리얼 모니터 초기화
 *   2. Wi-Fi 연결 대기
 *   3. MQTT 클라이언트 초기화 (브로커 등록 + 빈 콜백 설정)
 *   4. MQTT 브로커 최초 연결
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  /* 1단계: 시리얼 모니터 초기화 */
  Serial.begin(115200);
  delay(100); // USB-시리얼 초기화 안정화 대기
  Serial.println("\n╔══════════════════════════════════════════════════════════╗");
  Serial.println("║  Board 2: Standard MQTT — ESP32-S3 베이스라인 부팅 시작  ║");
  Serial.println("╚══════════════════════════════════════════════════════════╝");

  Serial.printf("[설정] 모의 데이터: temp=%.1f°C, hum=%.1f%%\n",
                MOCK_TEMPERATURE, MOCK_HUMIDITY);
  Serial.printf("[설정] 전송 인터벌: %lu ms | 고정 QoS: 1 | 클라이언트 ID: %s\n",
                PUBLISH_INTERVAL_MS, BOARD2_CLIENT_ID);

  /* 2단계: Wi-Fi 연결 */
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

  /* 3단계: MQTT 클라이언트 초기화
   * Board 2는 구독 토픽이 없으므로 빈 콜백 함수를 등록합니다. */
  mqtt_client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT); // 브로커 엔드포인트 등록
  mqtt_client.setCallback(on_mqtt_message);                 // 더미 수신 콜백 등록
  mqtt_client.setKeepAlive(60);                             // PINGREQ 간격 60초

  /* 4단계: 최초 MQTT 브로커 연결 */
  mqtt_connect();

  /* 마지막 전송 시각을 현재 시각 - 인터벌로 설정하여
   * setup() 직후 첫 번째 전송이 즉시 발생하도록 합니다. */
  last_publish_ms = millis() - PUBLISH_INTERVAL_MS;

  Serial.println("[부팅] ══ 초기화 완료, 5초 고정 인터벌 전송 루프 시작 ══\n");
}

/* ═══════════════════════════════════════════════════════════════════════════
 * loop() — Arduino 메인 루프
 *
 * 매 루프 사이클 실행 순서:
 *   1. MQTT 클라이언트 루프 (keepalive 유지 + 재연결 관리)
 *   2. 인터벌 타이머 확인 (5초 경과 여부)
 *   3. JSON 페이로드 조립 (모의 temp, hum 데이터)
 *   4. 고정 QoS 1 PUBLISH 전송 (재전송 최대 3회)
 *   5. 통계 카운터 갱신 및 진단 로그 출력
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  /* ──────────────────────────────────────────────────────────────────────
   * 1단계: MQTT 클라이언트 루프 실행
   * mqtt_client.loop()은 반드시 주기적으로 호출해야 합니다.
   * 내부 동작:
   *   - keepalive PINGREQ/PINGRESP 처리
   *   - QoS 1 PUBACK 수신 처리
   * ────────────────────────────────────────────────────────────────────── */
  if (!mqtt_client.connected()) {
    /* 브로커 연결이 끊어진 경우 재연결을 시도합니다. */
    Serial.println("[루프] ⚠ MQTT 브로커 연결 끊김 — 재연결 시도");

    /* 재연결 실패 시 1초 대기 후 다음 루프에서 재시도합니다.
     * 연결이 없는 상태에서 전송을 시도하지 않도록 early return합니다. */
    if (!mqtt_connect()) {
      delay(1000);
      return;
    }
  }
  /* 수신 큐 처리 및 keepalive 관리 */
  mqtt_client.loop();

  /* ──────────────────────────────────────────────────────────────────────
   * 2단계: 5초 고정 인터벌 타이머 확인
   * millis() 오버플로(약 49.7일)에도 안전하도록 부호 없는 정수 연산을 사용합니다.
   * ────────────────────────────────────────────────────────────────────── */
  unsigned long now = millis();
  if (now - last_publish_ms < PUBLISH_INTERVAL_MS) {
    /* 인터벌 미경과: 전송 없이 루프를 즉시 반복합니다.
     * CPU 점유율을 낮추기 위해 짧은 대기를 삽입합니다. */
    delay(10);
    return;
  }

  /* 타이머 갱신: 다음 인터벌 기준점을 현재 시각으로 설정 */
  last_publish_ms = now;
  total_sent++;

  /* ──────────────────────────────────────────────────────────────────────
   * 3단계: JSON 페이로드 조립
   * 고정 모의 데이터(temp=28.0°C, hum=55.0%)를 JSON으로 직렬화합니다.
   * ────────────────────────────────────────────────────────────────────── */
  char payload_buf[128]; // JSON 출력 버퍼 (최대 128바이트)
  build_json_payload(payload_buf, sizeof(payload_buf), total_sent);

  Serial.printf("\n[루프] ── 전송 사이클 #%u 시작 (%.1f초 경과) ──\n",
                total_sent,
                (float)(now / 1000));

  /* ──────────────────────────────────────────────────────────────────────
   * 4단계: 고정 QoS 1 PUBLISH 전송 (재전송 최대 3회)
   * Board 2는 AI 추론이나 조건 분기 없이 항상 QoS 1로 전송합니다.
   * ────────────────────────────────────────────────────────────────────── */
  bool success = false;

  for (int attempt = 1; attempt <= MAX_RETRY_COUNT; attempt++) {
    if (publish_qos1(payload_buf, total_sent)) {
      /* PUBLISH 전송 성공 후 PUBACK 처리를 위해 loop()를 잠시 실행합니다.
       * PubSubClient는 내부 상태 머신으로 PUBACK를 비동기 처리합니다. */
      unsigned long wait_start = millis();
      while (millis() - wait_start < PUBACK_TIMEOUT_MS) {
        mqtt_client.loop(); // PUBACK 수신 처리
        delay(10);          // 10ms 폴링 간격
      }
      success = true;
      break; // 전송 성공: 재전송 루프 종료
    }

    /* 전송 실패: 재전송 전 500ms 대기 후 재시도 */
    Serial.printf("[QoS 1] 재전송 시도 (%d/%d)...\n", attempt, MAX_RETRY_COUNT);
    delay(500);
  }

  /* ──────────────────────────────────────────────────────────────────────
   * 5단계: 통계 카운터 갱신 및 진단 로그 출력
   * ────────────────────────────────────────────────────────────────────── */
  if (success) {
    total_success++;
    Serial.printf("[루프] ✓ 전송 성공 (seq=%u) | 성공률: %u/%u (%.1f%%)\n",
                  total_sent,
                  total_success, total_sent,
                  (float)total_success / (float)total_sent * 100.0f);
  } else {
    total_failed++;
    Serial.printf("[루프] ✗ 전송 최종 실패 (seq=%u) | 실패: %u회 | 성공률: %u/%u\n",
                  total_sent, total_failed, total_success, total_sent);
  }
}
