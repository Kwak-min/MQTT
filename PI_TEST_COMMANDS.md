# 라즈베리파이 테스트 명령어 모음

라즈베리파이(IP: `10.144.246.14`, 펌웨어에 박아둔 값과 같아야 함)에서 백엔드 게이트웨이 +
대시보드를 켜고 확인할 때 쓰는 명령어를 순서대로 정리했습니다. 그대로 복사해서 쓰면 됩니다.

---

## 0. IP 확인 (가장 먼저)
펌웨어의 `UDP_SERVER_IP`/`MQTT_BROKER_IP`(`10.144.246.14`)와 실제 라즈베리파이 IP가 같은지 확인하세요.
와이파이 핫스팟은 재접속할 때마다 IP가 바뀔 수 있습니다.
```bash
hostname -I
```
값이 다르면 보드 펌웨어를 다시 빌드/업로드해야 합니다.

---

## 1. Mosquitto MQTT 브로커 설치 및 실행 (최초 1회)
`gingerbread/config` 설정 동기화에 필요합니다. 이미 설치되어 있으면 건너뛰세요.
```bash
sudo apt update
sudo apt install -y mosquitto mosquitto-clients
sudo systemctl enable --now mosquitto
sudo systemctl status mosquitto        # active (running) 확인
```

---

## 2. 백엔드 파이썬 의존성 설치 (최초 1회, 코드 바뀌면 재실행)
```bash
cd ~/MQTT/backend      # 실제 리포지토리 경로로 바꾸세요
python3 -m venv .venv  # 가상환경 (선택이지만 권장)
source .venv/bin/activate
pip install -r requirements.txt
```

---

## 3. (선택, 권장) 보드 없이 배선부터 점검
실제 ESP32를 켜기 전에, PC/파이 쪽 백엔드 배선이 정상인지 먼저 확인합니다.
**포트 5000/5001/6000/8080/1883이 전부 비어 있어야 합니다** (아래 4번 백엔드를 아직 켜지 않은 상태에서 실행).
```bash
cd ~/MQTT
python backend/tools/e2e_gateway_sim.py
```
마지막 줄이 `결과: 모두 통과 (24/24)`면 정상입니다. 이 스크립트는 임시 폴더에서 도니
실제 `backend/logs/`는 건드리지 않습니다.

---

## 4. 백엔드 게이트웨이 실행 (터미널 1)
```bash
cd ~/MQTT/backend
source .venv/bin/activate   # 가상환경 썼다면
python main.py
```
아래 로그가 보이면 정상 기동된 것입니다:
```
[Gingerbread] Bound to UDP 0.0.0.0:5000
[GingerbreadTCP] ... 5001
...
```
`Ctrl+C`로 종료합니다.

---

## 5. 대시보드 실행 (터미널 2 — 백엔드와 별도로, 동시에 켜둠)
같은 네트워크의 다른 PC/폰 브라우저로도 보려면 `--server.address 0.0.0.0`을 붙입니다.
```bash
cd ~/MQTT
streamlit run dashboard.py --server.address 0.0.0.0 --server.port 8501
```
브라우저에서 `http://10.144.246.14:8501` 접속.

---

## 6. 보드 없이 REST API가 살아있는지 빠르게 확인 (터미널 3)
```bash
curl -s http://localhost:8080/api/health | python3 -m json.tool
curl -s http://localhost:8080/api/diagnostics | python3 -m json.tool
curl -s http://localhost:8080/api/sessions | python3 -m json.tool
curl -s http://localhost:8080/api/config | python3 -m json.tool
```

### 온도/습도 임계값을 대시보드 없이 직접 바꿔보고 싶을 때
```bash
curl -s -X POST http://localhost:8080/api/config \
  -H "Content-Type: application/json" \
  -d '{"ENVIRONMENT": {"TEMP_DANGER_C": 45}}' | python3 -m json.tool
```
성공하면 ESP32가 다음 부팅(=다음 Deep Sleep 웨이크업) 때 이 값을 받아갑니다.

---

## 7. MQTT 설정 토픽이 실제로 발행되는지 직접 구독해서 보기
```bash
mosquitto_sub -h localhost -t 'gingerbread/config' -v
```
6번의 POST 명령을 다른 터미널에서 실행하면 여기 즉시 새 값이 찍혀야 합니다.

---

## 8. 실시간 로그 확인
```bash
tail -f ~/MQTT/backend/logs/telemetry.csv     # 환경 데이터 (온도/습도/가스)
tail -f ~/MQTT/backend/logs/power.csv         # 추정 전력
tail -f ~/MQTT/backend/logs/power_ext.csv     # 확장 전력 로그 (분석용 원시값)
tail -f ~/MQTT/backend/logs/sessions.csv      # CONNECT/DISCONNECT 이벤트
```

---

## 9. 실험 데이터 분석 (충분히 로그가 쌓인 뒤)
```bash
cd ~/MQTT
python backend/tools/analyze_power.py --skip-first 24 --sensitivity --md 결과.md
```

---

## 10. 문제가 생겼을 때 확인할 것
```bash
# 포트가 이미 사용 중인지 (백엔드가 두 번 켜져 있거나 안 죽었을 때)
ss -tulpn | grep -E ':5000|:5001|:6000|:8080|:1883'

# Mosquitto가 실제로 떠 있는지
sudo systemctl status mosquitto

# 방화벽이 ESP32→라즈베리파이 UDP/TCP를 막고 있진 않은지 (필요시)
sudo ufw status
```

---

## 종료
- 백엔드: 터미널 1에서 `Ctrl+C`
- 대시보드: 터미널 2에서 `Ctrl+C`
- Mosquitto는 시스템 서비스라 계속 켜둬도 됩니다. 끄려면: `sudo systemctl stop mosquitto`
