"""
backend/config.py
─────────────────────────────────────────────────────────────────────────────
IoT 게이트웨이 백엔드를 위한 중앙 설정(Configuration) 파일입니다.
모든 매직 넘버, 포트 및 파일 경로는 여기에 위치합니다 — 다른 곳에
값을 하드코딩하는 대신 이 모듈을 임포트하여 사용하세요.
─────────────────────────────────────────────────────────────────────────────
"""

import os

# ──────────────────────────────────────────────────────────────────────────────
# 네트워크 (Network)
# ──────────────────────────────────────────────────────────────────────────────

UDP_HOST = "0.0.0.0"               # 사용 가능한 모든 인터페이스에서 수신 대기

# Standard MQTT Board 2 브로커
MQTT_BROKER_HOST = "10.144.246.14"
MQTT_BROKER_PORT = 1883

# Node B  ──  Gingerbread (저전력 맞춤형 UDP 프로토콜)
GINGERBREAD_PORT = 5000

# Node B  ──  Gingerbread 상위 QoS 신뢰성 모드 (TCP)
# 프로젝트 명세: QoS 0·1은 UDP, 상위 QoS(TCP_MIN_QOS 이상)는 TCP로 자동 전환합니다.
# 펌웨어의 TCP_SERVER_PORT와 일치해야 합니다.
GINGERBREAD_TCP_PORT = 5001

# TCP 프레임 최대 길이(바이트). PublishPacket은 최대 264바이트입니다.
TCP_MAX_FRAME = 512

# TCP 연결에서 다음 데이터를 기다리는 최대 시간(초). 응답 없이 열려 있는 연결을 정리합니다.
TCP_CONN_TIMEOUT_SECONDS = 5

# 같은 (IP, msg_id)를 중복으로 간주하는 시간(초).
# 디바이스의 최대 재전송 시간(4회 × 약 2초)보다 길고, 재부팅 후 msg_id 재사용과는
# 충돌하기 어렵도록 짧게 잡습니다. 응답(PUBCOMP) 유실로 재전송된 메시지의 이중 처리를 막습니다.
TCP_DEDUP_TTL_SECONDS = 15

# ESP32-C3 전력 MCU  ──  JSON 전력 스트리밍 (전류, 전압, 전력)
POWER_PORT = 6000

# UDP 수신 버퍼 크기 (바이트)
UDP_BUFFER_SIZE = 1024

# ──────────────────────────────────────────────────────────────────────────────
# 로깅 / 영구 저장 (Logging / Persistence)
# ──────────────────────────────────────────────────────────────────────────────

# 백엔드가 어떤 작업 디렉토리에서도 작동하도록 이 설정 파일을 기준으로 경로를 확인합니다.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR           = os.path.join(_BASE_DIR, "logs")
TELEMETRY_CSV     = os.path.join(LOG_DIR, "telemetry.csv")
POWER_CSV         = os.path.join(LOG_DIR, "power.csv")
SESSION_LOG_CSV   = os.path.join(LOG_DIR, "sessions.csv")

# ──────────────────────────────────────────────────────────────────────────────
# QoS
# ──────────────────────────────────────────────────────────────────────────────

# 진행 중인 QoS 2 레코드를 만료시키기 전까지 유지할 초 단위 시간
QOS2_TIMEOUT_SECONDS = 30

# ──────────────────────────────────────────────────────────────────────────────
# Gingerbread 프로토콜 — 메시지 유형 상수
# (Node B ESP32-S3의 펌웨어 정의와 일치해야 함)
# ──────────────────────────────────────────────────────────────────────────────

MSG_CONNECT    = 1
MSG_PUBLISH    = 2
MSG_PUBACK     = 3   # QoS 1 확인(acknowledgement) (서버 → 클라이언트)
MSG_DISCONNECT = 4
MSG_PUBREC     = 5   # QoS 2 단계 1 (서버 → 클라이언트: "수신됨(received)")
MSG_PUBREL     = 6   # QoS 2 단계 2 (클라이언트 → 서버: "해제(release)")
MSG_PUBCOMP    = 7   # QoS 2 단계 3 (서버 → 클라이언트: "완료(complete)")

# ──────────────────────────────────────────────────────────────────────────────
# 패킷 포맷 상수
# ──────────────────────────────────────────────────────────────────────────────

CONNECT_MIN_LEN  = 20   # 2바이트 헤더 + 16바이트 client_id + 2바이트 sleep_duration (protocol.h ConnectPacket)
PUBLISH_MIN_LEN  = 7    # 2바이트 헤더 + 2바이트 msg_id + 1바이트 qos + 2바이트 topic_id
