# Gingerbread 프로젝트 🍪

**Gingerbread** 프로젝트 저장소에 오신 것을 환영합니다. 본 프로젝트는 MQTT-SN/UDP 환경에서 IoT 네트워크의 지연 시간(Latency)과 전력 소비(Power Consumption)를 최적화하기 위해, 초경량 머신러닝(TinyML) 기반의 동적 서비스 품질(QoS) 제어 전략을 구현하는 데 중점을 둡니다.

## 노드 아키텍처 (Node Architecture)
본 시스템은 A/B 테스트 및 성능 평가를 위해 두 개의 독립적인 ESP32-S3 펌웨어 노드로 구성되어 있습니다:
*   **Node 1: Gingerbread (제안 시스템)**
    *   특징: 동적 QoS 조정을 위한 TinyML 기반 추론 엔진 탑재, 커스텀 MQTT-SN over UDP 프로토콜 사용.
    *   소스 파일: `firmware/src/main_gingerbread.cpp`
*   **Node 2: 베이스라인 (표준 시스템)**
    *   특징: 고정된 QoS 1 (Publish/PubAck) 방식을 사용하는 표준 MQTT over TCP 기반.
    *   소스 파일: `firmware/src/main_standard_MQTT.cpp`

*(참고: 기존에 사용되던 Board 3 하드웨어 전력 모니터링 시스템(INA226)은 본 아키텍처에서 완전히 제거 및 폐지되었습니다.)*

## 학술적 전력 추정 모델 (Academic Power Estimation Base)
INA226과 같은 외부 하드웨어 전력 측정 장치 없이 전력 효율성을 정확하게 평가하기 위해, 본 프로젝트는 라즈베리파이(Raspberry Pi) 게이트웨이에서 동작하는 **소프트웨어 정의 경험적 전력 추정 모델(Software-defined empirical power estimation model)**을 채택하였습니다.

이 접근 방식은 **IEEE Access 2024 논문**에서 제안된 모델을 직접적으로 기반으로 합니다:
> **"Energy-Efficient Dynamic QoS for IoT"** (DOI: [10.1109/ACCESS.2024.3523864](https://doi.org/10.1109/ACCESS.2024.3523864))

이 모델은 패킷 트랜잭션의 QoS 레벨, 송수신(TX/RX) 위상 길이, 활성(Active) 및 수면(Sleep) 상태를 분석하여 패킷당 에너지 소비량(mWh 단위)을 동적으로 추정합니다.

### 성능 평가 지표 (Evaluation Metrics)
게이트웨이는 학술적 평가를 위해 필수적으로 요구되는 다음의 포괄적인 메트릭을 추적하고 기록합니다:
1.  **RTT (왕복 시간, ms)**: `PUBLISH` 패킷 전송 시작부터 최종 핸드셰이크 응답(예: `PUBACK` 또는 `PUBCOMP`)을 수신할 때까지의 정밀한 소요 시간.
2.  **재전송 횟수 (Retry Counter)**: 네트워크 타임아웃 또는 패킷 손실로 인해 트리거된 재전송 시도 횟수.
3.  **수면 모드 비율 (Sleep Mode Ratio, %)**: 전체 시뮬레이션 주기 중 MCU와 무선 모듈이 Deep/Light Sleep 모드에 머문 시간의 비율.
4.  **패킷 수 (Packet Count)**: 성공적으로 전달된 누적 텔레메트리 패킷 수.
5.  **총 전송 바이트 (Total Transmitted Bytes)**: 세션 동안 누적된 전체 네트워크 페이로드 크기.
6.  **알고리즘 복잡도 (Algorithm Complexity)**: Flash(Sketch 크기) 및 정적 RAM(SRAM) 사용량으로 측정된 TinyML 모델의 펌웨어 풋프린트. (정확한 실측 바이트 수는 `main_gingerbread.cpp` 내의 인라인 주석/독스트링 참조).

## 시스템 구성 요소
*   `firmware/`: ESP32-S3 노드를 위한 PlatformIO 프로젝트 폴더입니다.
*   `backend/`: UDP 수신, 경험적 전력 추정 및 로그 기록을 담당하는 Python 기반의 라즈베리파이 게이트웨이입니다.
*   `ml_model/`: TinyML 신경망 학습 스크립트 및 추출된 가중치(weights)를 포함합니다.