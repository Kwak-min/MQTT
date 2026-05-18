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
};

struct ConnectPacket {
  Header header;
  char client_id[16];
  uint16_t sleep_duration;
};

struct PublishPacket {
  Header header;
  uint16_t msg_id;
  QoSLevel qos;
  uint16_t topic_id;
  char payload[64];

  // AI 에이전트 추론 결과 메타데이터 (비트필드)
  uint8_t network_status : 2; // 0: GOOD, 1: UNSTABLE
  uint8_t data_urgency : 2;   // 0: NORMAL, 1: EMERGENCY
  uint8_t reserved : 4;
};

struct PubAckPacket {
  Header header;
  uint16_t msg_id;
};

// QoS 2 제어용 구조체 정의
struct PubRecPacket {
  Header header;
  uint16_t msg_id;
};

struct PubRelPacket {
  Header header;
  uint16_t msg_id;
};

struct PubCompPacket {
  Header header;
  uint16_t msg_id;
};

struct DisconnectPacket {
  Header header;
  uint8_t sleep_mode_flag;
};

#pragma pack(pop)