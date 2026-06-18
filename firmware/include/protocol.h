#pragma once

#include <stdint.h>
#include <string.h>

#pragma pack(push, 1)

// 메시지 타입 정의 (QoS 2 확장을 위한 제어 패킷 추가)
enum class MsgType : uint8_t {
  CONNECT = 1,
  PUBLISH = 2,
  PUBACK = 3,
  DISCONNECT = 4,
  PUBREC = 5, // QoS 2: 수신 확인 (Gateway -> Client)
  PUBREL = 6, // QoS 2: 방출 통지 (Client -> Gateway)
  PUBCOMP = 7 // QoS 2: 완료 통지 (Gateway -> Client)
};

// QoS 레벨 정의 (QoS 2 추가)
enum class QoSLevel : uint8_t { QoS0 = 0, QoS1 = 1, QoS2 = 2 };

struct Header {
  uint8_t length;
  MsgType msg_type;
} __attribute__((packed));

struct ConnectPacket {
  Header header;
  char client_id[16];
  uint16_t sleep_duration;
} __attribute__((packed));

struct PublishPacket {
  Header header;
  uint16_t msg_id;
  QoSLevel qos;
  uint16_t topic_id;
  // [2026-06 확장] RTT/retry_count/sleep_mode_ratio JSON 필드 추가로 인해
  // 버퍼를 64 → 128 바이트로 확장합니다.
  // 확장된 페이로드 예시 (~95 chars):
  //   {"temp":32.50,"hum":45.20,"gas":18.50,"battery":100,"nn":0.512,
  //    "qos":1,"rtt":12.3,"retry":0,"sleep_r":0.833}
  char payload[128];

  // AI 에이전트 추론 결과 메타데이터 (비트필드)
  uint8_t network_status : 2; // 0: GOOD, 1: UNSTABLE
  uint8_t data_urgency : 2;   // 0: NORMAL, 1: EMERGENCY
  uint8_t reserved : 4;
} __attribute__((packed));

struct PubAckPacket {
  Header header;
  uint16_t msg_id;
} __attribute__((packed));

// QoS 2 제어용 구조체 정의
struct PubRecPacket {
  Header header;
  uint16_t msg_id;
} __attribute__((packed));

struct PubRelPacket {
  Header header;
  uint16_t msg_id;
} __attribute__((packed));

struct PubCompPacket {
  Header header;
  uint16_t msg_id;
} __attribute__((packed));

struct DisconnectPacket {
  Header header;
  uint8_t sleep_mode_flag;
} __attribute__((packed));

#pragma pack(pop)