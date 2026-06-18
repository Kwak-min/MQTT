# 🚀 설정 및 실행 가이드

Gingerbread 프로젝트 노드와 라즈베리파이(Raspberry Pi) 게이트웨이를 배포하고 평가하기 위해 아래의 지침을 따르십시오.

## 1. 하드웨어 전원 및 설정
물리적인 INA226 하드웨어 전력 모니터링 방식은 소프트웨어 정의 전력 추정 방식으로 대체되어 폐지되었습니다.
1. 보드에 외부 I2C 전력 센서나 션트(Shunt) 저항을 연결하지 **마십시오**.
2. ESP32-S3 노드(Node 1 및 Node 2)에 전원을 공급할 때는 개별적인 **5V 외부 USB 어댑터** 또는 안정적인 보조 배터리를 사용하십시오. 최종 성능 평가 시 컴퓨터의 USB 허브 전류 제한이 무선 성능에 영향을 미치는 것을 방지하기 위해 PC에 직접 연결하여 전원을 공급하는 것은 피하십시오.

## 2. 펌웨어 빌드 및 업로드
1. 타겟으로 삼을 ESP32-S3 보드를 USB-C 케이블을 통해 컴퓨터에 연결합니다. (포트 혼선 방지를 위해 한 번에 하나의 보드만 연결하십시오).
2. 연결 중인 로컬 네트워크 환경에 맞게, 다음 소스 파일들의 최상단에 위치한 Wi-Fi 자격 증명(SSID 및 비밀번호)을 수정합니다:
   - Node 1 (Gingerbread): `firmware/src/main_gingerbread.cpp`
   - Node 2 (베이스라인): `firmware/src/main_standard_MQTT.cpp`
3. 또한, `MQTT_BROKER_IP` / `UDP_SERVER_IP` 값이 라즈베리파이 게이트웨이의 로컬 IP 주소로 설정되어 있는지 확인하고 저장(`Ctrl + S`)합니다.
4. Antigravity IDE (또는 PlatformIO가 설치된 VSCode)의 하단 상태 표시줄에서 해당하는 환경(Environment) 타겟을 선택합니다:
   - Node 1용: `board1_gingerbread`
   - Node 2용: `board2_standard`
5. **업로드(Upload)** 버튼(`➔`)을 클릭하여 펌웨어를 컴파일하고 ESP32-S3 보드에 플래싱합니다.
6. 업로드가 완료되면 선택적으로 **시리얼 모니터(Serial Monitor)**를 열어 보드가 Wi-Fi에 정상적으로 연결되고 데이터 전송을 시작하는지 확인할 수 있습니다.

## 3. 라즈베리파이 게이트웨이 실행
백엔드 게이트웨이는 패킷 수신을 처리하고 IEEE Access 2024 기반 경험적 전력 추정(Empirical Power Estimation)을 동적으로 계산합니다.

1. 라즈베리파이에 SSH로 접속하거나 게이트웨이 머신에서 터미널을 엽니다.
2. 백엔드(backend) 디렉토리로 이동합니다:
   ```bash
   cd backend
   ```
3. 아직 설치하지 않았다면 필요한 종속성을 설치합니다:
   ```bash
   pip install -r requirements.txt
   ```
4. 게이트웨이 서버를 시작합니다:
   ```bash
   python main.py
   ```
   
게이트웨이는 자동으로 다음을 수행합니다:
* 지정된 포트에서 들어오는 UDP 패킷을 수신 대기합니다.
* 성능 메트릭(RTT, 재전송 횟수, 수면 모드 비율)을 추출합니다.
* 경험적 전력 공식을 동적으로 계산합니다.
* 정리되고 병합된 데이터를 `backend/logs/telemetry.csv` 및 `backend/logs/power.csv`에 텔레메트리 로그로 저장합니다.

## 4. 트러블슈팅 (자주 발생하는 오류 해결)
### 🚨 에러: Failed to connect to ESP32-S3: No serial data received.
- **원인**: 보드가 연산으로 바쁘거나 수면 상태(Sleep State)에 진입하여 업로드 동기화 신호를 무시하는 현상입니다.
- **해결책**: 보드를 수동으로 부트로더(Bootloader) 모드로 강제 진입시킵니다:
  1. ESP32-S3 보드의 `BOOT` 버튼을 누른 상태를 유지합니다.
  2. `BOOT`를 누른 상태에서 `RST` (또는 `EN`) 버튼을 한 번 눌렀다 뗍니다.
  3. 누르고 있던 `BOOT` 버튼에서 손을 뗍니다.
  4. IDE에서 업로드 버튼을 다시 클릭합니다.

### 🚨 에러: 시리얼 포트 사용 중 또는 인식 불가 (Serial Port Busy or Unrecognized)
- **원인**: 이전 세션에서 OS가 COM 포트를 해제하지 못했거나, 케이블 연결 불량으로 인해 인식이 끊긴 상태입니다.
- **해결책**: USB 케이블을 완전히 뽑은 후 3초 정도 대기한 다음 다시 단단히 연결합니다. 그 후 시리얼 모니터를 다시 시작하십시오.