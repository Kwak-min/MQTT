# 🍞 Gingerbread: 지능형 저전력 MQTT-SN 프로토콜

본 프로젝트는 **TinyML**을 결합하여 네트워크 환경에 따라 QoS를 동적으로 결정하는 **저전력 MQTT-SN 통신 시스템**입니다.

---

## 👥 팀원 역할 분담 (R&R)
양찬승 : 로직, 펌웨어, 게이트웨이 서버
곽민성 : 하드웨어, tiny ML 모델 구축
박환솔 : 대시보드(풀스텍), 로그 
이하연 : 하드웨어, tiny ML 모델 구축
---

## 🚀 빠른 시작 가이드 (Quick Start)

### 1. 게이트웨이 서버 실행 (Python)
```bash
# 필요한 패키지 설치
pip install streamlit pandas

# 서버 실행
python server/gateway.py