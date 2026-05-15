# 🍞 Gingerbread: 지능형 저전력 MQTT-SN 프로토콜

본 프로젝트는 **TinyML**을 결합하여 네트워크 환경에 따라 QoS를 동적으로 결정하는 **저전력 MQTT-SN 통신 시스템**입니다.

---

## 👥 팀원 역할 분담 (R&R)
| 담당자 | 역할 | 주요 개발 범위 |
| :--- | :--- | :--- |
| **양찬승** | **AI/프로토콜 설계** | 전체 아키텍처, TinyML 모델 학습, QoS 결정 로직 |
| **팀원 A** | **임베디드/HW** | ESP32-S3 펌웨어, BME280/INA226 센서 연동 |
| **팀원 B** | **백엔드/실험** | Python 게이트웨이 서버, 데이터 로깅, tc netem 시뮬레이션 |
| **팀원 C** | **프론트엔드/시연** | Streamlit 기반 실시간 전력 및 통신 지표 시각화 대시보드 |

---

## 🚀 빠른 시작 가이드 (Quick Start)

### 1. 게이트웨이 서버 실행 (Python)
```bash
# 필요한 패키지 설치
pip install streamlit pandas

# 서버 실행
python server/gateway.py