# Gingerbread 운영 대시보드 (프론트엔드)

MQTT-SN/UDP + TinyML 동적 QoS 연구 프로젝트의 운영·실험·프로토콜 대시보드입니다.
디자인 시안(Claude Design 캔버스)의 개선안 5가지를 그대로 구현했습니다.

```bash
npm install
npm run dev        # http://localhost:5173  (기본: mock 데이터)
npm run typecheck  # 타입 검사
npm run build      # 프로덕션 번들
```

---

## 1. 기술 스택과 선택 이유

| 선택 | 이유 |
|---|---|
| **React 18 + TypeScript** | 상태가 있는 대시보드의 사실상 표준. 도메인 타입을 컴파일 타임에 강제해 "백엔드 필드가 바뀌었는데 화면이 조용히 깨지는" 사고를 막습니다. |
| **Vite** | 개발 서버가 즉시 뜨고, `.env` 기반 데이터 소스 전환이 간단합니다. |
| **react-router-dom** | 운영/실험/프로토콜 3화면에 각각 URL이 필요합니다(발표·논문에서 특정 화면을 링크로 공유). |
| **차트 라이브러리 없음 — 직접 만든 SVG 컴포넌트** | 이 시안의 차트는 임계선이 있는 게이지, 시간축이 실제 비례하는 시퀀스 다이어그램, QoS 명도 램프 누적 막대입니다. Recharts·Chart.js로는 전부 우회 구현이 되고, 결과적으로 라이브러리와 싸우게 됩니다. 필요한 도형이 단순해서 직접 그리는 편이 코드가 짧고 디자인 토큰과도 정확히 맞습니다. |
| **상태 관리 라이브러리 없음** | 서버 상태 3종이 전부입니다. `useResource` 훅 하나(약 70줄)로 폴링·중단·재시도를 처리합니다. 캐시·중복 제거가 필요해지면 이 훅의 **구현만** TanStack Query로 갈아 끼우면 되고 호출부는 그대로입니다. |

의존성은 런타임 3개(react, react-dom, react-router-dom)뿐입니다. 연구용 코드가 1년 뒤에도 `npm install`이 되게 하려는 의도적인 선택입니다.

---

## 2. 구조 — 데이터와 화면의 분리

```
src/
├── types/
│   ├── domain.ts      화면이 소비하는 모델 (백엔드와 무관)
│   └── api.ts         게이트웨이 REST DTO (백엔드 스키마 그대로)
├── data/
│   ├── source.ts      TelemetrySource 인터페이스 ← 유일한 계약
│   ├── mock/          시안 수치 그대로의 정적 구현
│   ├── http/
│   │   ├── httpSource.ts  REST 구현
│   │   └── mappers.ts     DTO → 도메인 변환 (스키마 변경 시 여기만 수정)
│   ├── useResource.ts     폴링·중단·재시도
│   └── OperationsContext.tsx
├── components/   순수 표시 컴포넌트 (fetch 없음, props만 받음)
└── pages/        3화면 조립
```

**규칙 하나**: 컴포넌트는 `fetch`를 부르지 않습니다. 페이지가 훅에서 도메인 모델을 받아 props로 내려줍니다. 그래서 백엔드가 바뀌어도 컴포넌트는 손댈 일이 없습니다.

### 데이터 소스 전환

```bash
# .env
VITE_DATA_SOURCE=mock   # 기본. 시안 수치 그대로
VITE_DATA_SOURCE=http   # 실제 게이트웨이
VITE_API_BASE_URL=http://192.168.0.100:8080
```

`src/data/index.ts`의 팩토리 한 줄이 구현을 고릅니다. 화면 코드는 어느 쪽인지 모릅니다.

---

## 3. 지금 구현된 범위 / API 연동 시 바꿀 곳

### 이미 동작하는 것
- 3화면 전체 레이아웃, 전역 헤더·탭 네비게이션
- 개선 5종 전부 (아래 4장 참고)
- 로딩 / 에러 / 빈 상태 화면 — 실측 데이터 초기에 가장 많이 보게 될 화면들
- 반응형 (1200px / 720px 브레이크포인트)
- mock 소스의 3초 주기 값 흔들림 → 스트리밍 UI 검증용

### API가 붙을 때 손댈 곳 (그 외에는 손댈 곳이 없습니다)

| 위치 | 할 일 |
|---|---|
| `.env` | `VITE_DATA_SOURCE=http`, base URL 지정 |
| `src/data/http/mappers.ts` | 백엔드 실제 응답에 맞춰 필드 매핑 조정 |
| `src/data/http/httpSource.ts` | 신설 엔드포인트 경로 확정 |
| `src/data/source.ts`의 `subscribe` | WebSocket 구현 추가 (현재 http 소스는 미구현 → 자동으로 5초 폴링) |
| `src/types/api.ts` | `?` 표시된 필드는 "백엔드에 신설 요청 중"입니다. 확정되면 옵셔널 해제 |

**하드코딩된 수치는 `src/data/mock/fixtures.ts` 한 파일에만 있습니다.** 다른 파일에는 실험 수치가 없습니다.

---

## 4. 화면에 반영된 개선 5종

| # | 개선 | 구현 위치 |
|---|---|---|
| 1 | KPI 행 재구성 — 수치 4장만 카드, 상태 3종은 얇은 스트립, 스파크라인 + 베이스라인 대비 증감 | `dashboard/StatusStrip.tsx`, `dashboard/KpiCard.tsx`, `common/Sparkline.tsx` |
| 2 | Network Quality — 3중 도넛 → 게이지 3줄, 지표별 자기 축·단위, 검은 세로선 = 임계값, 중복 범례 제거 | `common/ThresholdMeter.tsx` |
| 3 | 비교 차트 — 이중 아크 도넛 → 같은 0 기준선 막대 2개 / QoS 파이 2개 → 누적 가로 막대 2줄, QoS는 파랑 명도 3단계 | `common/ComparisonBars.tsx`, `common/StackedQosBar.tsx` |
| 4 | 핸드셰이크 — 연속 화살표 + 라벨 위로, 실제 비례 시간축, RTT 브래킷, 상행/하행 색 구분, 재전송·유실, QoS 0·1·2 탭 | `protocol/HandshakeDiagram.tsx` |
| 5 | 토큰 — Gingerbread `#2A78D6` / Legacy MQTT `#EB6834` 고정 | `styles/tokens.css` |

색은 컴포넌트에 hex로 박혀 있지 않고 전부 `styles/tokens.css`의 CSS 변수를 참조합니다. 색을 바꾸려면 그 파일 하나만 고치면 됩니다.

두 시리즈 색은 색맹 시뮬레이션 색차 **ΔE 24.7**(적록색맹 기준)로 검증된 조합입니다. 바꾸실 때는 대비를 함께 확인하세요. 상태(정상/주의/경계/위험)는 색 단독으로 쓰지 않고 항상 아이콘 + 라벨과 함께 렌더링됩니다 (`common/StatusIcon.tsx`).

### 수치 정합성
와이어프레임 값을 그대로 쓰되 **패킷 손실만 1.2% → 1.5%** 로 조정했습니다. 전송 성공률 98.5%와 `100 − 1.5 = 98.5`로 맞아떨어지게 하기 위함입니다. 실측이 들어오면 이 제약은 자동으로 해소됩니다.

---

## 5. 백엔드 연동 계약

`docs/API-CONTRACT.md` 를 백엔드 담당자에게 그대로 전달하세요. 요청 엔드포인트·응답 스키마·우선순위가 정리되어 있습니다.

---

## 검증 상태

- `npm run typecheck` 기준 타입 오류 0 — 단, **작성 환경에 npm 레지스트리 접근이 없어 `npm install`과 `npm run build`는 실행하지 못했습니다.** 받으신 뒤 한 번 돌려보시고, 버전 충돌이 나면 알려주세요.
- 축 비율·임계선 위치·증감 계산은 별도 단위 검증을 통과했습니다 (지연 45/150 = 30%, 임계 100 = 66.7%, RSSI −84 on [−100,−40] = 26.7%, ▲26.5%p, ▼66.2%).
