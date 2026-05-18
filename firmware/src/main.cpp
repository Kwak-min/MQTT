#include "protocol.h"
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

const char *ssid = "YOUR_WIFI_SSID";
const char *password = "YOUR_WIFI_PASSWORD";
const char *server_ip = "192.168.0.100";
const uint16_t server_port = 1883;

WiFiUDP udp;
uint16_t current_msg_id = 1;

// 하드웨어 데이터 수집 함수 (가상 데이터)
float read_temperature() {
  return 32.5;
} // 기획서 테스트용 (30도 초과 긴급 상황 가정)
float read_humidity() { return 45.2; }
float read_power_consumption() { return 15.4; }
int8_t get_wifi_rssi() {
  return -80;
} // 기획서 테스트용 (-75 미만 불안정 환경 가정)

// 3단계 AI 기반 동적 QoS 자동 선택 알고리즘 (기획서 로직 구현)
QoSLevel run_complex_agent_inference(float temp, float hum, int8_t rssi,
                                     uint8_t &net_status, uint8_t &urgency) {
  urgency = (temp > 30.0 || temp < 10.0) ? 1 : 0; // 데이터 위험도 판단
  net_status = (rssi < -75) ? 1 : 0;              // 네트워크 불안정 판단

  // 기획서 3단계 조건 매핑
  if (urgency == 1 && net_status == 1) {
    return QoSLevel::QoS2; // 최고 신뢰성 모드 (위험 데이터 + 통신 불안정)
  } else if (urgency == 1 || net_status == 1) {
    return QoSLevel::QoS1; // 신뢰성 보장 모드 (둘 중 하나만 불안정)
  } else {
    return QoSLevel::QoS0; // 최대 저전력 모드 (둘 다 정상)
  }
}

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  udp.begin(server_port);

  ConnectPacket conn_pkt;
  conn_pkt.header.length = sizeof(ConnectPacket);
  conn_pkt.header.msg_type = MsgType::CONNECT;
  strncpy(conn_pkt.client_id, "ESP32-Client", sizeof(conn_pkt.client_id) - 1);
  conn_pkt.sleep_duration = 5;

  udp.beginPacket(server_ip, server_port);
  udp.write((uint8_t *)&conn_pkt, sizeof(conn_pkt));
  udp.endPacket();
}

// 특정 패킷 타입을 타임아웃(2초) 동안 대기하는 범용 수신 핸들러
bool wait_for_packet(MsgType expected_type, uint16_t target_msg_id) {
  unsigned long start_time = millis();
  while (millis() - start_time < 2000) { // 2.0초 타임아웃
    int packetSize = udp.parsePacket();
    if (packetSize >= sizeof(Header)) {
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

void loop() {
  float temperature = read_temperature();
  float humidity = read_humidity();
  int8_t rssi = get_wifi_rssi();

  uint8_t net_status = 0, data_urgency = 0;
  QoSLevel selected_qos = run_complex_agent_inference(
      temperature, humidity, rssi, net_status, data_urgency);

  PublishPacket pub_pkt;
  pub_pkt.header.length = sizeof(PublishPacket);
  pub_pkt.header.msg_type = MsgType::PUBLISH;
  pub_pkt.msg_id = current_msg_id++;
  pub_pkt.topic_id = 1;
  pub_pkt.qos = selected_qos;
  pub_pkt.network_status = net_status;
  pub_pkt.data_urgency = data_urgency;

  snprintf(pub_pkt.payload, sizeof(pub_pkt.payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"power\":%.2f}", temperature, humidity,
           read_power_consumption());

  int retry_count = 0;
  const int max_retries = 3; // 최대 3회 재전송
  bool transaction_success = false;

  // --- QoS 레벨별 전송 시퀀스 제어 ---
  if (selected_qos == QoSLevel::QoS0) {
    // [QoS 0] 무확인 단발성 전송
    udp.beginPacket(server_ip, server_port);
    udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
    udp.endPacket();
    transaction_success = true;
    Serial.println("[QoS 0] 전송 완료 (응답 대기 없음)");

  } else if (selected_qos == QoSLevel::QoS1) {
    // [QoS 1] 2단계 핸드셰이크 (PUBLISH -> PUBACK)
    while (retry_count <= max_retries) {
      udp.beginPacket(server_ip, server_port);
      udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
      udp.endPacket();

      if (wait_for_packet(MsgType::PUBACK, pub_pkt.msg_id)) {
        transaction_success = true;
        Serial.printf("[QoS 1] 성공 - PUBACK 수신 (MsgID: %d)\n",
                      pub_pkt.msg_id);
        break;
      }
      retry_count++;
      Serial.printf("[QoS 1] 타임아웃 - 재전송 시도 (%d/%d)\n", retry_count,
                    max_retries);
    }

  } else if (selected_qos == QoSLevel::QoS2) {
    // [QoS 2] 4단계 핸드셰이크 (PUBLISH -> PUBREC -> PUBREL -> PUBCOMP)
    bool pubrec_received = false;

    // 단계 1: PUBLISH 송신 및 PUBREC 대기
    while (retry_count <= max_retries) {
      udp.beginPacket(server_ip, server_port);
      udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
      udp.endPacket();

      if (wait_for_packet(MsgType::PUBREC, pub_pkt.msg_id)) {
        pubrec_received = true;
        break;
      }
      retry_count++;
      Serial.printf("[QoS 2] 단계1 타임아웃 - PUBLISH 재전송 (%d/%d)\n",
                    retry_count, max_retries);
    }

    // 단계 2: PUBREL 송신 및 PUBCOMP 대기
    if (pubrec_received) {
      retry_count = 0;
      PubRelPacket rel_pkt;
      rel_pkt.header.length = sizeof(PubRelPacket);
      rel_pkt.header.msg_type = MsgType::PUBREL;
      rel_pkt.msg_id = pub_pkt.msg_id;

      while (retry_count <= max_retries) {
        udp.beginPacket(server_ip, server_port);
        udp.write((uint8_t *)&rel_pkt, sizeof(rel_pkt));
        udp.endPacket();

        if (wait_for_packet(MsgType::PUBCOMP, pub_pkt.msg_id)) {
          transaction_success = true;
          Serial.printf("[QoS 2] 최종 성공 - PUBCOMP 수신 (MsgID: %d)\n",
                        pub_pkt.msg_id);
          break;
        }
        retry_count++;
        Serial.printf("[QoS 2] 단계2 타임아웃 - PUBREL 재전송 (%d/%d)\n",
                      retry_count, max_retries);
      }
    }
  }

  if (!transaction_success) {
    Serial.printf("[통신 에러] QoS %d 전송 최종 실패\n", (int)selected_qos);
  }

  // 저전력 수면 동기화 시퀀스 후 절전 모드 진입
  DisconnectPacket disc_pkt;
  disc_pkt.header.length = sizeof(DisconnectPacket);
  disc_pkt.header.msg_type = MsgType::DISCONNECT;
  disc_pkt.sleep_mode_flag = 1;

  udp.beginPacket(server_ip, server_port);
  udp.write((uint8_t *)&disc_pkt, sizeof(disc_pkt));
  udp.endPacket();

  delay(5000);
}