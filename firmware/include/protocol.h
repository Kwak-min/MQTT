#pragma once

#include <stdint.h>
#include <string.h>

// 패킷의 정확한 바이트 정렬을 위해 1바이트 단위 패킹 적용
#pragma pack(push, 1)

// 메시지 타입 정의 (INSTRUCTIONS.md 기반)
enum class MsgType : uint8_t {
  CONNECT = 1,
  PUBLISH = 2,
  PUBACK = 3,
  DISCONNECT = 4
};

// QoS 레벨 정의
enum class QoSLevel : uint8_t { QoS0 = 0, QoS1 = 1 };

// 공통 헤더 구조체
struct Header {
  uint8_t length;   // 메시지 전체 길이
  MsgType msg_type; // 메시지 타입
};

// CONNECT 패킷
struct ConnectPacket {
  Header header;
  char client_id[16];      // 클라이언트 식별자
  uint16_t sleep_duration; // 게이트웨이 동기화용 수면 주기 (초 단위)
};

// PUBLISH 패킷
struct PublishPacket {
  Header header;
  uint16_t msg_id;   // QoS 1 처리를 위한 메시지 ID
  QoSLevel qos;      // QoS Level (0 or 1)
  uint16_t topic_id; // 토픽 ID
  char payload[64];  // 데이터 페이로드 (JSON 문자열)

  // 복합 제어형 AI 에이전트 추론 결과 매핑용 비트필드(Bit-field) 최적화 영역
  uint8_t network_status : 2; // 0: 양호(GOOD), 1: 불안정(UNSTABLE)
  uint8_t data_urgency : 2;   // 0: 정상(NORMAL), 1: 긴급(EMERGENCY)
  uint8_t reserved : 4;       // 1바이트 정렬 마감을 위한 예약 비트
};

// PUBACK 패킷
struct PubAckPacket {
  Header header;
  uint16_t msg_id; // ACK 대상 메시지 ID
};

// DISCONNECT 패킷
struct DisconnectPacket {
  Header header;
  uint8_t sleep_mode_flag; // 1: 하드웨어 Deep Sleep 모드 진입 알림
};

#pragma pack(pop)