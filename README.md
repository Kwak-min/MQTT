# 실험 개요 대시보드 (Overview Standalone)

원본 `mqtt-node-dashboard` 프로젝트의 **개요(Overview) 화면만** 떼어낸 별도 프로젝트입니다.
사이드바 · 상단 페이지 탭 없이, Gingerbread(UDP) vs Standard MQTT 비교 화면 하나만 띄웁니다.

## 실행

```bash
npm install
npm run dev
```

브라우저에서 `http://localhost:5173` 접속.

## 구성

- 라우터(react-router-dom) 없이 `src/App.tsx` 가 `OverviewPage` 하나만 렌더링합니다.
- 다른 페이지(텔레메트리 / 세션 / 전력 / 프로토콜 / 제어 / 설정), `Sidebar`, `AppShell` 는 포함되어 있지 않습니다.
- 상단 바는 최소 헤더(타이틀 + 연결 상태 뱃지)만 유지했습니다.
- 데이터 소스 추상화(`lib/dataSource`), 컴포넌트, 스타일 토큰은 원본 프로젝트와 동일합니다.

## 데이터 소스 전환

`.env.example` 을 `.env` 로 복사해 값을 바꾸면, 원본 프로젝트와 동일하게 mock ↔ 실제 백엔드(HTTP/WebSocket)를 전환할 수 있습니다.

```bash
cp .env.example .env
# VITE_DATA_SOURCE=http 로 바꾸고 VITE_API_BASE_URL / VITE_WS_URL 설정
```

## 원본 프로젝트와의 관계

이 프로젝트는 원본 `dashboard/` 프로젝트에서 개요 화면 렌더링에 필요한 파일만 복사한
독립 사본입니다. 원본에서 Overview 관련 컴포넌트를 수정하면 이 프로젝트에는 자동으로
반영되지 않으니, 필요하면 해당 파일을 다시 복사해주세요.
