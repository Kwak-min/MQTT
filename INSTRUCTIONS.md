# 프로젝트명: Gingerbread (지능형 저전력 MQTT-SN 프로토콜)

## 1. 프로젝트 개요
본 프로젝트는 UDP 기반의 MQTT-SN 프로토콜을 구현하고, TinyML을 결합하여 네트워크 상태에 따라 QoS를 동적으로 결정하는 지능형 저전력 통신 시스템을 구축하는 것을 목표로 함.

## 2. 시스템 아키텍처
- **Sensor Client**: ESP32-S3 (BME280 센서 데이터 전송, TinyML 기반 QoS 결정)
- **Gateway Emulator**: Ubuntu 22.04 기반 Python UDP 서버 (메시지 중계 및 세션 관리)
- **Log/Analysis**: CSV 기반 로그 저장 및 Streamlit 실시간 대시보드 시각화

## 3. 프로토콜 상세 규격 (교수님 가이드 준수)
### 필수 메시지 타입
- **CONNECT**: 클라이언트 접속 및 세션 생성
- **PUBLISH**: 센서 데이터 전송 (QoS 0 또는 1 적용)
- **PUBACK**: QoS 1 메시지에 대한 수신 확인 응답
- **DISCONNECT**: 세션 종료 또는 수면(Sleep) 모드 진입 알림

### QoS 1 및 재전송 로직
- **Timeout**: 2.0초 대기
- **Max Retry**: 최대 3회 재전송 시도
- **메시지 식별**: `msg_id`를 사용하여 중복 수신 여부 확인

### 세션 관리 테이블 필드
- `client_id`, `status` (active/asleep), `topic_id`, `last_seen`, `pending_msg`

## 4. 실험 및 평가 지표
### 네트워크 실험 (tc netem 활용)
- **시나리오**: 정상, Loss 5%, Loss 10%, Delay 100ms, 지연+손실 복합 시나리오
- **비교 분석**: 표준 MQTT-SN vs TinyML 적용 Gingerbread 방식의 전력 소모 및 성공률 비교

### 핵심 데이터 로그 필드
- `time`, `scenario`, `qos`, `msg_id`, `success`, `retry_count`, `rtt_ms`, `power_consumption(mA)`

## 5. 에이전트 지시 사항
1. 모든 Python 코드는 교수님 PDF 예제를 기반으로 하되, 확장성을 위해 클래스 구조로 리팩토링할 것.
2. C++ 코드는 ESP32-S3 및 PlatformIO 환경을 타겟으로 작성할 것.
3. 데이터 패킷은 초기 단계에서는 JSON을 사용하고, 추후 비트필드(Bit-field) 방식으로 최적화할 예정임을 인지할 것.