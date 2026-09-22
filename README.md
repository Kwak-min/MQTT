# Gingerbread 프로젝트 🍪

**Gingerbread** 프로젝트 저장소에 오신 것을 환영합니다. 본 프로젝트는 MQTT-SN/UDP 환경에서 IoT 네트워크의 지연 시간(Latency)과 전력 소비(Power Consumption)를 최적화하기 위해, 온도·습도 임계값 기반의 동적 서비스 품질(QoS) 제어와 실제 Deep Sleep 절전을 구현하는 데 중점을 둡니다.

> **[2026-09] 아키텍처 변경**: Sleep을 `delay()`로 흉내만 내던 것을 `esp_deep_sleep_start()` 실제 절전으로 바꿨고, QoS 판단은 온도·습도 두 값만 보도록 제한했습니다(`INSTRUCTIONS.md` 참조). `gas_baseline.h`/`net_congestion.h`(가스 기준값, 네트워크 혼잡도 기반 QoS 상향)는 더 이상 쓰이지 않지만 코드는 남겨 두었습니다.
>
> **QoS는 TinyML(MLP)이 직접 결정하는 것이 목표 상태입니다.** `firmware/include/mlp_weights.h`의 `MLP_WEIGHTS_TRAINED` 플래그로 상태를 구분합니다:
> - **학습 후(`=1`)**: 온도·습도 2개 입력 MLP가 위험 점수(0~1)를 계산하고, 그 점수를 QoS 0/1/2로 매핑합니다 — 신경망이 QoS를 직접 고릅니다.
> - **학습 전(`=0`, 현재 상태 — `raw_dataset.csv`가 아직 비어 있음)**: 신경망을 아직 믿을 수 없으므로, 사람이 명시한 SPEC 규칙(+TinyML이 좁은 범위에서 학습한 임계값 보정, `qos_calibration.h`)으로 안전하게 폴백합니다.
>
> 두 경로 모두 같은 로직(`decide_qos_plan()`)에서 나오며, 부팅 로그와 QoS 판정 로그에 `(NN)`/`(rule)` 표시로 어느 쪽이 활성인지 항상 드러납니다. `ml_model/README.md`의 절차로 데이터를 모으고 `train.py --features temp,hum`을 실행하면 MLP가 QoS를 직접 결정하도록 전환됩니다.

## 노드 아키텍처 (Node Architecture)
본 시스템은 A/B 테스트 및 성능 평가를 위해 두 개의 독립적인 ESP32-S3 펌웨어 노드로 구성되어 있습니다:
*   **Node 1: Gingerbread (제안 시스템)**
    *   특징: 온도·습도 기반 TinyML(MLP) 동적 QoS 조정 + 실제 Deep Sleep 절전, 커스텀 MQTT-SN 프로토콜 사용.
    *   **QoS 결정 = 온도와 습도만 사용** (다른 어떤 값도 QoS에 영향을 주지 않음). 학습된 MLP가 있으면 그 위험 점수로,
        없으면 온도/습도를 각각 NORMAL/WARNING/DANGER로 분류해 더 심각한 쪽을 그대로 쓰는 규칙(OR 판정)으로 폴백합니다.
        규칙 폴백의 임계값은 `gingerbread/config` MQTT 토픽으로 실시간 조정 가능합니다
        (`ENVIRONMENT.TEMP_WARN_C` / `TEMP_DANGER_C` / `HUM_WARN_PCT` / `HUM_DANGER_PCT`).

        | QoS | 조건 | 전송 계층 | 사이클 주기 |
        |---|---|---|---|
        | 0 (정상) | 온도 ≤ 30°C **AND** 습도 ≤ 70% | UDP | 60초 Deep Sleep |
        | 1 (경고) | (30 < 온도 ≤ 50°C) **OR** (70 < 습도 ≤ 85%) | UDP | 30초 Deep Sleep |
        | 2 (위험) | 온도 > 50°C **OR** 습도 > 85% | TCP (4단계 핸드셰이크) | 3초 Deep Sleep |

        (평소엔 느긋하게(60초) 확인하다가, 이상 징후가 잡히면(경고 30초, 위험 3초) 확인 주기를
        점점 좁혀 악화를 놓치지 않는 구조입니다. 위험 상태는 절전보다 신뢰성이 우선이라 재연결
        오버헤드 비중이 커도 감수합니다.)

    *   **가변 전송 계층 (QoS에 따라 UDP/TCP 자동 전환)**:
        *   QoS 0·1 (저전력 모드): 오버헤드가 적은 **UDP** (게이트웨이 포트 5000)
        *   QoS 2 (신뢰성 모드): **TCP** (게이트웨이 포트 5001). 수신 보장이 필수인 위험 상태에서 사용
        *   전환 기준은 펌웨어의 `TCP_MIN_QOS`(기본 2)로 조정합니다. 3으로 올리면 모든 QoS가 UDP로 처리됩니다.
        *   TCP 프레임: `[길이 uint16][PublishPacket]`, 게이트웨이는 처리 후 `PUBCOMP` 4바이트로 응답합니다. 연결은 시도마다 열고 닫습니다.
        *   설정 동기화(`gingerbread/config` 구독)는 부팅(=매 Deep Sleep 웨이크업) 시 한 번, 별도의 표준 MQTT(TCP) 연결로 이루어집니다.
    *   **실제 Deep Sleep** (`esp_deep_sleep_start()`): QoS 0/1 사이클은 전송 직후 완전히 재부팅되는 Deep Sleep에 들어갑니다
        (CPU/RAM 전원 차단, RTC 메모리만 유지). 누적 패킷 수·바이트·msg_id·Wi-Fi 채널/BSSID 힌트는 `RTC_DATA_ATTR` 변수에
        저장해 재부팅 사이에도 유지됩니다. 평소(QoS 0)엔 60초 간격으로 느긋하게 확인하다가, 이상 징후가
        잡히면(QoS 1) 30초, 위험 수준(QoS 2)이면 3초로 확인 주기를 점점 좁혀 악화를 놓치지 않습니다.
    *   소스 파일: `firmware/src/main_gingerbread.cpp`
*   **Node 2: 베이스라인 (표준 시스템)**
    *   특징: 고정된 QoS 1 (Publish/PubAck) 방식을 사용하는 표준 MQTT over TCP 기반.
    *   **고정 60초 주기 + 실제 Deep Sleep** (Gingerbread의 "정상(QoS 0)" 주기와 동일). 처음엔
        Sleep을 아예 안 쓰고 5초마다 라디오를 계속 켜둔 채 발행했는데, 그 상태로 비교하면
        절감률의 대부분이 "적응형 QoS의 효과"가 아니라 "Sleep을 쓰냐 안 쓰냐의 효과"로
        나와서(거의 항상 켜진 기기 vs 거의 항상 자는 기기 비교) 대시보드에 99%대의 비현실적인
        절감률이 뜨는 원인이 됐습니다. 지금은 QoS/주기 모두 고정이고 온도·습도에 반응하지
        않는다는 점만 Gingerbread와 다르므로, 절감률이 적응형 QoS 자체의 효과를 더 정확히
        반영합니다.
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
6.  **알고리즘 복잡도 (Algorithm Complexity)**: Flash(Sketch 크기) 및 정적 RAM(SRAM) 사용량으로 측정된 펌웨어 풋프린트. (정확한 실측 바이트 수는 `pio run -e board1_gingerbread --verbose`로 직접 측정하세요.)

## 실험 결과 분석
게이트웨이는 `backend/logs/power.csv`(대시보드 호환, 스키마 고정) 외에, 분석용 원시 입력을 담은
`backend/logs/power_ext.csv`를 함께 기록합니다 (전송 계층, 사이클의 활성/Sleep 시간, 에너지 구성). 혼잡 지표
컬럼(`net_loss_pct`/`rtt_ratio`/`congested`/`probe`)은 과거 `net_congestion.h` 기반 로직이 남긴 것으로,
현재 Gingerbread 펌웨어는 이 값을 채우지 않으므로 빈 값으로 기록됩니다.
```
python backend/tools/analyze_power.py --skip-first 24 --sensitivity --md 결과.md
```
- 두 노드를 **평균 전류**(사이클 길이가 달라도 공정)로 비교하고, 부트스트랩 95% 신뢰구간과 표본 수를 함께 출력합니다.
- `--sensitivity`는 같은 로그의 원시 입력으로 `IDLE_MA` 등 모델 상수를 바꿔 다시 계산하고, "Sleep이 실제로는 대기였다면"의
  해석도 함께 보여 줍니다. 두 해석의 차이가 크면 절감은 시스템이 아니라 Sleep 구현 여부에 좌우됩니다.
- 출력되는 모든 에너지는 소프트웨어 모델의 **추정값**입니다. 실측 전류로 검증하기 전에는 절대값을 주장하지 마세요.
- 게이트웨이 쪽 배선은 하드웨어 없이 `python backend/tools/e2e_gateway_sim.py`로 점검할 수 있습니다.
- 참고: 장비 ID는 `ConnectPacket.client_id`(16바이트) 제한으로 잘려 기록됩니다 (`ESP32-Gingerbread` → `ESP32-Gingerbrea`).

### 초기 실측 결과 (2026-09-22, 실내 약 12분, `backend/logs/power_ext.csv` 기준, `analyze_power.py --sensitivity` 실행 결과)

측정 당시 실내 온도 30~31°C · 습도 54~55%. 표본이 매우 적어(Gingerbread 13사이클, Standard 7사이클)
아래 수치는 **참고용**이며, 통계적으로 확정된 결과가 아닙니다.

| 노드 | 사이클 수 | 평균 사이클 길이 | QoS 분포 | 평균 전류 (95% CI) |
|---|---|---|---|---|
| Gingerbread | 13 | 43.8초 | 정상 38.5% · 경고 61.5% · 위험 0% | 1.178 mA [0.772, 1.894] |
| Standard | 7 | 63.4초 | (항상 고정 QoS 1) | 1.478 mA [0.980, 2.214] |

사이클 길이로 정규화한 **평균 전류**로 비교하면 (사이클당 에너지가 아니라 이 값이 공정한 비교 기준입니다),
**Gingerbread가 Standard보다 평균 전류를 약 20.3% 덜 썼습니다** (전류 비 0.797, 95% CI [0.435, 1.550]).
다만 이 신뢰구간이 1을 포함하므로, 표본 13/7개로는 **통계적으로 유의미한 차이라고 주장할 수 없습니다.**

**절감률은 모델 가정(IDLE_MA)에 민감합니다** — 같은 로그로 `IDLE_MA`만 바꿔 다시 계산하면:

| IDLE_MA (mA) | 추정 절감 |
|---|---|
| 10 | 30.3% |
| 20 (기본값) | 20.3% |
| 50 | 10.7% |
| 80 | 7.6% |
| 100 | 6.4% |

**후속 측정 권장**: ① 더 시원한 환경(30°C 이하)에서 30분 이상, ② 의도적으로 위험 상황(온도 자극)을
만든 상태에서 각각 측정해 "정상 시 동등 · 위험 시 트레이드오프" 곡선을 함께 제시하면 더 방어 가능한
결과가 됩니다. 표본도 최소 수십~수백 사이클(수 시간)로 늘려야 신뢰구간이 의미 있어집니다. IDLE_MA 등
모델 상수도 실측 전류로 보정해야 절대적인 절감률을 주장할 수 있습니다.

## 시스템 구성 요소
*   `firmware/`: ESP32-S3 노드를 위한 PlatformIO 프로젝트 폴더입니다.
*   `backend/`: UDP 수신, 경험적 전력 추정 및 로그 기록을 담당하는 Python 기반의 라즈베리파이 게이트웨이입니다.
*   `ml_model/`: TinyML 신경망 학습 스크립트 및 추출된 가중치(weights)를 포함합니다. **[미사용]** 현재 배포된
    `main_gingerbread.cpp`는 QoS를 온도·습도 임계값으로만 판단하므로 이 학습 파이프라인을 쓰지 않습니다
    (데이터 기반 접근으로 되돌아갈 경우를 위해 코드만 보존).