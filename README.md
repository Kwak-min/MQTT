# Gingerbread 프로젝트 🍪

**Gingerbread** 프로젝트 저장소에 오신 것을 환영합니다. 본 프로젝트는 MQTT-SN/UDP 환경에서 IoT 네트워크의 지연 시간(Latency)과 전력 소비(Power Consumption)를 최적화하기 위해, 초경량 머신러닝(TinyML) 기반의 동적 서비스 품질(QoS) 제어 전략을 구현하는 데 중점을 둡니다.

## 노드 아키텍처 (Node Architecture)
본 시스템은 A/B 테스트 및 성능 평가를 위해 두 개의 독립적인 ESP32-S3 펌웨어 노드로 구성되어 있습니다:
*   **Node 1: Gingerbread (제안 시스템)**
    *   특징: 동적 QoS 조정을 위한 TinyML 기반 추론 엔진 탑재, 커스텀 MQTT-SN 프로토콜 사용.
    *   **가변 전송 계층 (QoS에 따라 UDP/TCP 자동 전환)**:
        *   QoS 0·1 (저전력 모드): 오버헤드가 적은 **UDP** (게이트웨이 포트 5000)
        *   상위 QoS = QoS 2 (신뢰성 모드): **TCP** (게이트웨이 포트 5001). 수신 보장이 필수인 상황(신경망 CRITICAL 판정)에서 사용
        *   전환 기준은 펌웨어의 `TCP_MIN_QOS`(기본 2)로 조정합니다. 3으로 올리면 모든 QoS가 UDP로 처리됩니다.
        *   TCP 프레임: `[길이 uint16][PublishPacket]`, 게이트웨이는 처리 후 `PUBCOMP` 4바이트로 응답합니다. 연결은 시도마다 열고 닫습니다.
        *   설정 동기화(`gingerbread/config` 구독)는 QoS와 무관하게 별도의 표준 MQTT(TCP) 연결을 사용합니다.
    *   **QoS 결정 = max(환경 위험 QoS, 네트워크 QoS)**: 신경망이 센서값으로 환경 위험 QoS(0/1/2)를 정하고, 네트워크가
        불안정하면(RSSI가 임계값 미만 **또는 실측 혼잡**) QoS를 최소 1로 올립니다. 네트워크는 QoS를 최대 1까지만 올리며
        QoS 2(TCP)는 환경 위험 전용입니다.
    *   **네트워크 혼잡도** (`firmware/include/net_congestion.h`): RSSI는 신호 세기일 뿐 혼잡도가 아니므로, ACK를 받는
        전송(QoS 1, TCP)의 실측 결과로 판단합니다. 전송 시도별 손실률(지수이동평균)이 `PACKET_LOSS_LIMIT`(설정, 기본 5%)을
        넘거나, 지연이 평소 최저 RTT의 3배를 넘으면 혼잡으로 판정하고 히스테리시스(손실 상한의 절반 미만 + 지연 2배 미만이어야 해제)를 둡니다.
        QoS 0은 ACK가 없어 관측할 수 없으므로 24사이클(약 2분)마다 QoS 1 프로브를 보내고, 실패가 관측되면 잠시 촘촘히 관측합니다.
        모든 상수는 가정값이며 실제 네트워크에서 보정해야 합니다 (`firmware/test/test_net_congestion.py`로 동작 검증).
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