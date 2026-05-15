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
enum class QoSLevel : uint8_t {
    QoS0 = 0,
    QoS1 = 1
};

// 공통 헤더 구조체
struct Header {
    uint8_t length;     // 메시지 전체 길이
    MsgType msg_type;   // 메시지 타입
};

// CONNECT 패킷
struct ConnectPacket {
    Header header;
    char client_id[16]; // 클라이언트 식별자
    
    // TODO: [팀원 역할] 추가적인 CONNECT 필드가 필요하다면 여기에 추가 (예: 수면 주기, 프로토콜 버전 등)
};

// PUBLISH 패킷
struct PublishPacket {
    Header header;
    uint16_t msg_id;    // QoS 1 처리를 위한 메시지 ID
    QoSLevel qos;       // QoS Level (0 or 1)
    uint16_t topic_id;  // 토픽 ID
    char payload[64];   // 데이터 페이로드 (초기 단계에서는 JSON 문자열 사용)
    
    // TODO: [팀원 역할] TinyML 추론 결과 등을 담을 추가 필드가 필요하다면 비트필드(Bit-field)로 여기에 추가
};

// PUBACK 패킷
struct PubAckPacket {
    Header header;
    uint16_t msg_id;    // ACK 대상 메시지 ID
};

// DISCONNECT 패킷
struct DisconnectPacket {
    Header header;
    
    // TODO: [팀원 역할] 수면 모드 진입 이유 등을 담을 추가 필드가 필요하다면 여기에 추가
};

#pragma pack(pop)
