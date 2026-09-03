# 인수인계 메모

## 전달 형태

1. **저장소** — `Kwak-min/MQTT` 안에 `dashboard/` 디렉터리로 넣거나, `Kwak-min/Network`(현재 비어 있음)를 프론트엔드 저장소로 쓰는 방법이 있습니다. 백엔드와 API 계약을 자주 맞춰야 하므로 **같은 저장소 안 `dashboard/`** 를 권합니다. 이슈·PR에서 백엔드 변경과 화면 변경을 한 번에 볼 수 있습니다.
2. **백엔드 담당자에게는** `docs/API-CONTRACT.md` 를 이슈 본문으로 그대로 붙여 넣으세요. 우선순위 표까지 포함되어 있습니다.
3. **디자인 근거가 필요하면** Claude Design 캔버스 링크를 함께 첨부하세요. 개선 전/후가 나란히 있어 "왜 도넛을 뺐는지"를 설명하지 않아도 됩니다.

## 첫 PR 제안

```
feat(dashboard): 운영·실험·프로토콜 대시보드 프론트엔드 추가

- React + TypeScript + Vite, 런타임 의존성 3개
- 데이터 소스 추상화(TelemetrySource): mock ↔ http 를 환경 변수로 전환
- 시안 개선 5종 구현
- 백엔드 요청 사항은 docs/API-CONTRACT.md
```

## 백엔드와 합의해야 할 것 (연동 전)

- 타임스탬프 포맷 (ISO 8601 + 오프셋)
- 가스 저항 단위 (Ω 전송 / kΩ 표시)
- `power.csv` 에 `msg_id` 추가 여부 — 로그 테이블 조인 키
- `/api/v1/stats/summary` 의 `window` 정의: 세션 기준인지 시각 기준인지
- baseline(Node 2) 데이터 적재 방식: MQTT subscribe vs 별도 수집기
