#include "protocol.h"
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

// WiFi 설정
const char *ssid = "YOUR_WIFI_SSID";
const char *password = "YOUR_WIFI_PASSWORD";

// 게이트웨이 서버 설정
const char *server_ip = "192.168.0.100"; // TODO: 실제 게이트웨이 IP로 변경
const uint16_t server_port = 1883;

WiFiUDP udp;
uint16_t current_msg_id = 1;

// TODO: [팀원 역할] BME280 센서 및 INA226 전력 측정 모듈 초기화 변수 선언 영역

void setup() {
  Serial.begin(115200);

  // TODO: [팀원 역할] BME280 및 INA226 초기화 코드 추가

  // WiFi 연결
  Serial.print("Connecting to WiFi");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected.");

  // UDP 소켓 초기화
  udp.begin(server_port);

  // CONNECT 메시지 전송
  ConnectPacket conn_pkt;
  memset(&conn_pkt, 0, sizeof(conn_pkt));
  conn_pkt.header.length = sizeof(ConnectPacket);
  conn_pkt.header.msg_type = MsgType::CONNECT;
  strncpy(conn_pkt.client_id, "ESP32-Client", sizeof(conn_pkt.client_id) - 1);

  udp.beginPacket(server_ip, server_port);
  udp.write((uint8_t *)&conn_pkt, sizeof(conn_pkt));
  udp.endPacket();

  Serial.println("Sent CONNECT packet.");
}

// 지정된 msg_id에 대한 PUBACK 패킷을 기다리는 함수
bool wait_for_puback(uint16_t target_msg_id) {
  unsigned long start_time = millis();
  // 2.0초 타임아웃 대기
  while (millis() - start_time < 2000) {
    int packetSize = udp.parsePacket();
    if (packetSize >= sizeof(PubAckPacket)) {
      PubAckPacket ack_pkt;
      udp.read((char *)&ack_pkt, sizeof(ack_pkt));

      if (ack_pkt.header.msg_type == MsgType::PUBACK &&
          ack_pkt.msg_id == target_msg_id) {
        return true; // 올바른 PUBACK 수신
      }
    }
    delay(10);
  }
  return false; // 타임아웃
}

void loop() {
  PublishPacket pub_pkt;
  memset(&pub_pkt, 0, sizeof(pub_pkt));

  pub_pkt.header.length = sizeof(PublishPacket);
  pub_pkt.header.msg_type = MsgType::PUBLISH;
  pub_pkt.msg_id = current_msg_id++;

  // TODO: [팀원 역할] TinyML 모델을 실행하여 현재 네트워크 상태를 기반으로 동적
  // QoS 레벨 결정 임시로 QoS 1 고정 적용
  pub_pkt.qos = QoSLevel::QoS1;
  pub_pkt.topic_id = 1;

  // TODO: [팀원 역할] BME280에서 읽은 실제 온도/습도 값을 JSON 형식에 담기
  snprintf(pub_pkt.payload, sizeof(pub_pkt.payload),
           "{\"msg\":\"Hello\", \"count\":%d}", current_msg_id);

  bool ack_received = false;
  int retry_count = 0;
  const int max_retries = 3; // 최대 3회 재전송

  // QoS 1 전송 및 재전송 로직 (최초 1회 전송 + 최대 3회 재전송)
  while (retry_count <= max_retries && !ack_received) {
    Serial.printf("[PUBLISH] Sending MsgID %d (Attempt %d/%d)...\n",
                  pub_pkt.msg_id, retry_count + 1, max_retries + 1);

    udp.beginPacket(server_ip, server_port);
    udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
    udp.endPacket();

    if (pub_pkt.qos == QoSLevel::QoS1) {
      // 2.0초 타임아웃 대기 및 PUBACK 수신 확인
      if (wait_for_puback(pub_pkt.msg_id)) {
        Serial.printf("[PUBACK] Received for MsgID %d\n", pub_pkt.msg_id);
        ack_received = true;
      } else {
        Serial.printf("[TIMEOUT] MsgID %d - No PUBACK received in 2s.\n",
                      pub_pkt.msg_id);
        retry_count++;
      }
    } else {
      // QoS 0이면 응답 대기 및 재전송 불필요
      break;
    }
  }

  if (pub_pkt.qos == QoSLevel::QoS1 && !ack_received) {
    Serial.printf(
        "[ERROR] Failed to send MsgID %d after %d retries. (Total losses)\n",
        pub_pkt.msg_id, max_retries);
  }

  // TODO: [팀원 역할] 전송 및 재전송 완료 후 INA226을 통해 소비 전력을
  // 측정/기록하는 로직 추가

  // TODO: [팀원 역할] 필요한 경우 Deep Sleep 모드로 진입하기 전 DISCONNECT 알림
  // 전송 로직 추가

  // 5초 대기 후 반복
  delay(5000);
}
