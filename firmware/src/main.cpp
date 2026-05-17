#include "protocol.h"
#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>

// WiFi 설정
const char *ssid = "YOUR_WIFI_SSID";
const char *password = "YOUR_WIFI_PASSWORD";

// 게이트웨이 서버 설정
const char *server_ip = "192.168.0.100"; // 실제 가동 게이트웨이 IP로 할당 필요
const uint16_t server_port = 1883;

WiFiUDP udp;
uint16_t current_msg_id = 1;

// 하드웨어 디바이스 데이터 및 인터페이스 정의 영역 (팀원 A 연동 파트)
float read_temperature() {
  // TODO: [팀원 A] BME280 센서 API 호출 및 실제 온도 반환 구현
  return 26.5; // 테스트 가상 데이터
}

float read_humidity() {
  // TODO: [팀원 A] BME280 센서 API 호출 및 실제 습도 반환 구현
  return 45.2; // 테스트 가상 데이터
}

float read_power_consumption() {
  // TODO: [팀원 A] INA226 모듈 API 호출 및 소모 전류(mA) 데이터 수집 구현
  return 15.4; // 테스트 가상 데이터
}

int8_t get_wifi_rssi() { return WiFi.RSSI(); }

// 2단계: 복합 제어형 AI 에이전트 추론 연산 루틴 (찬승 담당 파트)
QoSLevel run_complex_agent_inference(float temp, float hum, int8_t rssi,
                                     uint8_t &net_status, uint8_t &urgency) {
  // 세부 지표 1. 센서 데이터 기반 변동성 및 위험도 판단 (Data Urgency)
  if (temp > 30.0 || temp < 10.0) {
    urgency = 1; // 긴급 상태(EMERGENCY) 분류
  } else {
    urgency = 0; // 정상 상태(NORMAL) 분류
  }

  // 세부 지표 2. 선로 통신 가용성 판단 (Network Status)
  if (rssi < -75) {
    net_status = 1; // 선로 불안정(UNSTABLE) 분류
  } else {
    net_status = 0; // 선로 양호(GOOD) 분류
  }

  // 세부 지표 3. 다차원 복합 의사결정 매트릭스 도출 (선택지 3 설계 원칙)
  // 데이터가 긴급하거나 무선 환경이 불안정할 경우 신뢰성 보장을 위해 QoS 1 강제
  // 지정 데이터가 정상이고 통신 선로가 안정적일 때만 초저전력 구동을 위해 QoS 0
  // 지정
  if (urgency == 1 || net_status == 1) {
    return QoSLevel::QoS1;
  } else {
    return QoSLevel::QoS0;
  }
}

void setup() {
  Serial.begin(115200);

  // TODO: [팀원 A] BME280 및 INA226 I2C 버스 제어 초기화 선언

  // WiFi 무선 인터페이스 활성화
  Serial.print("Connecting to WiFi");
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected.");

  // UDP 통신 소켓 바인딩
  udp.begin(server_port);

  // CONNECT 패킷 빌드 및 세션 등록
  ConnectPacket conn_pkt;
  memset(&conn_pkt, 0, sizeof(conn_pkt));
  conn_pkt.header.length = sizeof(ConnectPacket);
  conn_pkt.header.msg_type = MsgType::CONNECT;
  strncpy(conn_pkt.client_id, "ESP32-Client", sizeof(conn_pkt.client_id) - 1);
  conn_pkt.sleep_duration = 5; // 5초 주기 스케줄링 세션 동기화

  udp.beginPacket(server_ip, server_port);
  udp.write((uint8_t *)&conn_pkt, sizeof(conn_pkt));
  udp.endPacket();

  Serial.println("Sent CONNECT packet. Gateway session activated.");
}

// QoS 1 통신 확인용 PUBACK 핸들러 루틴
bool wait_for_puback(uint16_t target_msg_id) {
  unsigned long start_time = millis();
  while (millis() - start_time < 2000) { // 교수님 가이드 준수: 2.0초 대기
    int packetSize = udp.parsePacket();
    if (packetSize >= sizeof(PubAckPacket)) {
      PubAckPacket ack_pkt;
      udp.read((char *)&ack_pkt, sizeof(ack_pkt));

      if (ack_pkt.header.msg_type == MsgType::PUBACK &&
          ack_pkt.msg_id == target_msg_id) {
        return true; // 정상 핸드셰이크 성립
      }
    }
    delay(10);
  }
  return false; // 타임아웃 예외 발생
}

void loop() {
  // 1단계: 실시간 데이터 정밀 측정
  float temperature = read_temperature();
  float humidity = read_humidity();
  int8_t rssi = get_wifi_rssi();

  // 2단계: 다차원 지능형 에이전트 연산 구동
  uint8_t net_status = 0;
  uint8_t data_urgency = 0;
  QoSLevel selected_qos = run_complex_agent_inference(
      temperature, humidity, rssi, net_status, data_urgency);

  // 3단계: 프로토콜 적응형 변환 및 데이터 전송 실행
  PublishPacket pub_pkt;
  memset(&pub_pkt, 0, sizeof(pub_pkt));

  pub_pkt.header.length = sizeof(PublishPacket);
  pub_pkt.header.msg_type = MsgType::PUBLISH;
  pub_pkt.msg_id = current_msg_id++;
  pub_pkt.topic_id = 1;
  pub_pkt.qos = selected_qos;

  // 비트필드 구조 영역에 AI 의사결정 상태 데이터 팩 처리
  pub_pkt.network_status = net_status;
  pub_pkt.data_urgency = data_urgency;

  // 페이로드 정형화 구성 (온습도 및 INA226 기반 측정 전류 통합 구성)
  snprintf(pub_pkt.payload, sizeof(pub_pkt.payload),
           "{\"temp\":%.2f,\"hum\":%.2f,\"power\":%.2f}", temperature, humidity,
           read_power_consumption());

  bool ack_received = false;
  int retry_count = 0;
  const int max_retries = 3; // 교수님 가이드 준수: 최대 3회 재전송 제한

  while (retry_count <= max_retries && !ack_received) {
    Serial.printf("[PUBLISH] MsgID %d 전송 (QoS レ벨: %d, 시도 회수: %d/%d)\n",
                  pub_pkt.msg_id, (int)pub_pkt.qos, retry_count + 1,
                  max_retries + 1);

    udp.beginPacket(server_ip, server_port);
    udp.write((uint8_t *)&pub_pkt, sizeof(pub_pkt));
    udp.endPacket();

    if (pub_pkt.qos == QoSLevel::QoS1) {
      if (wait_for_puback(pub_pkt.msg_id)) {
        Serial.printf("[PUBACK] 수신 성공 - MsgID: %d\n", pub_pkt.msg_id);
        ack_received = true;
      } else {
        Serial.printf(
            "[TIMEOUT] 응답 미수신 - MsgID: %d 재전송 프로세스 이행\n",
            pub_pkt.msg_id);
        retry_count++;
      }
    } else {
      break; // QoS 0 상태인 경우 응답 대기 및 재전송 생략 후 즉시 루프 해제
    }
  }

  if (pub_pkt.qos == QoSLevel::QoS1 && !ack_received) {
    Serial.printf("[ERROR] 최대 재전송 실패 - MsgID %d 유실 처리됨\n",
                  pub_pkt.msg_id);
  }

  // 4단계: 에너지 세션 동기화 및 초저전력 수면 모드 핸들링
  DisconnectPacket disc_pkt;
  disc_pkt.header.length = sizeof(DisconnectPacket);
  disc_pkt.header.msg_type = MsgType::DISCONNECT;
  disc_pkt.sleep_mode_flag = 1; // Deep Sleep 동기화 마킹

  udp.beginPacket(server_ip, server_port);
  udp.write((uint8_t *)&disc_pkt, sizeof(disc_pkt));
  udp.endPacket();
  Serial.println("[SYSTEM] DISCONNECT 통지 완료. 하드웨어 Deep Sleep 진입.");

  // TODO: [팀원 A] 실제 기기 전력 최적화 보존을 위한 하드웨어 Deep Sleep 트리거
  // 수행 위치 esp_deep_sleep_start();

  delay(5000); // 하드웨어 Sleep 대체용 테스트 딜레이 루틴
}