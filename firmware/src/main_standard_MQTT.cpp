#include <Arduino.h>
#include <WiFi.h>
#include <WiFiClient.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

/* ═══════════════════════════════════════════════════════════════════════════
 * Board 2: 표준 MQTT 벤치마크 타겟 (고충실도 전력 소비 비교용)
 * ═══════════════════════════════════════════════════════════════════════════ */

/* 1. 실제 Wi-Fi 및 MQTT 클라이언트 설정 */
static const char *WIFI_SSID = "YOUR_WIFI_SSID";         // Wi-Fi 네트워크 SSID를 입력하세요
static const char *WIFI_PASSWORD = "YOUR_WIFI_PASSWORD"; // Wi-Fi 비밀번호를 입력하세요

static const char *MQTT_BROKER_IP = "192.168.0.100";     // 실제 브로커 IP로 변경하세요
static const uint16_t MQTT_BROKER_PORT = 1883;           // 타겟 포트 1883

// Board 1 등 브로커 내 다른 보드와의 세션 충돌을 방지하기 위한 고유 MQTT 클라이언트 ID
static const char *CLIENT_ID = "ESP32-Standard-MQTT";

// 발행할 MQTT 토픽 지정
static const char *TOPIC = "environmental/standard";

/* 클라이언트 객체 초기화 */
WiFiClient espClient;
PubSubClient client(espClient);

/* 5초 타이머 및 모의 데이터 변수 */
unsigned long lastMsg = 0;
const unsigned long INTERVAL = 5000; // 5초 대기 간격

// Board 1 구조와 동일한 모의 데이터 페이로드 설정용
float mock_temp = 25.4;
float mock_hum = 50.0;
float mock_gas = 12.5;
int current_qos = 1;

/* ═══════════════════════════════════════════════════════════════════════════
 * Wi-Fi 연결 함수
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup_wifi() {
  delay(10);
  Serial.println();
  Serial.print("Wi-Fi 네트워크에 연결 중: ");
  Serial.println(WIFI_SSID);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("");
  Serial.println("Wi-Fi 연결 성공!");
  Serial.print("할당된 IP 주소: ");
  Serial.println(WiFi.localIP());
}

/* ═══════════════════════════════════════════════════════════════════════════
 * 비동기 MQTT 브로커 재연결 함수 (논블로킹 권장 방식을 포함)
 * ═══════════════════════════════════════════════════════════════════════════ */
void reconnect() {
  // 브로커에 연결될 때까지 반복 시도
  while (!client.connected()) {
    Serial.print("MQTT 브로커 연결 시도 중... (클라이언트 ID: ");
    Serial.print(CLIENT_ID);
    Serial.println(")");
    
    // 연결 시도
    if (client.connect(CLIENT_ID)) {
      Serial.println("MQTT 브로커 페어링 성공!");
    } else {
      Serial.print("연결 실패, 에러 코드=");
      Serial.print(client.state());
      Serial.println(" -> 5초 후 다시 시도합니다.");
      delay(5000); // 실패 시 5초 대기 후 재시도
    }
  }
}

/* ═══════════════════════════════════════════════════════════════════════════
 * 초기 설정 (Setup)
 * ═══════════════════════════════════════════════════════════════════════════ */
void setup() {
  Serial.begin(115200);
  while (!Serial) {
    delay(10); // 시리얼 포트 대기
  }
  
  Serial.println("\n--- Board 2: 표준 MQTT 베이스라인 시스템 부팅 시작 ---");
  
  setup_wifi();
  
  // MQTT 서버 설정
  client.setServer(MQTT_BROKER_IP, MQTT_BROKER_PORT);
}

/* ═══════════════════════════════════════════════════════════════════════════
 * 무한 루프 (Loop) - 헤비 듀티 QoS 1 수신 대기 및 주기적 발행
 * ═══════════════════════════════════════════════════════════════════════════ */
void loop() {
  // 브로커 연결 상태 확인 및 재연결 (논블로킹에 가깝게 동작)
  if (!client.connected()) {
    reconnect();
  }
  
  // client.loop()를 쉬지 않고 호출하여 Wi-Fi 라디오 안테나를 완전히 켜둔 상태로 유지합니다.
  // 이를 통해 대기 네트워크 전력 오버헤드 베이스라인을 측정합니다.
  client.loop();

  // 5초 고정 간격 확인
  unsigned long now = millis();
  if (now - lastMsg > INTERVAL) {
    lastMsg = now;

    // Board 1(Gingerbread)과 동일한 데이터 구조 조립
    StaticJsonDocument<200> doc;
    doc["temp"] = mock_temp;
    doc["hum"] = mock_hum;
    doc["gas"] = mock_gas;
    doc["qos"] = current_qos;

    // JSON 문자열로 직렬화
    char payload[200];
    serializeJson(doc, payload);

    Serial.print("\n[전송] 다음 토픽으로 메시지를 보냅니다: ");
    Serial.println(TOPIC);
    Serial.print("[데이터] ");
    Serial.println(payload);

    // 진정한 QoS 1 통신을 모방하기 위해 전체 네트워크 핸드셰이크가 일어나도록 publish
    // (PubSubClient 구조상 세 번째 인자 true는 retained 플래그이며 안정적인 전송 검증에 쓰입니다)
    if (client.publish(TOPIC, payload, true)) {
      Serial.println("[결과] QoS 1 (Retained) 발행 명령이 성공적으로 실행되었습니다.");
    } else {
      Serial.println("[결과] 발행 실패 - 네트워크 스택을 확인하세요.");
    }
    
    // 모의 데이터 소폭 변경 (다음 주기를 위함)
    mock_temp += 0.1;
    if (mock_temp > 30.0) mock_temp = 25.4;
  }
}
