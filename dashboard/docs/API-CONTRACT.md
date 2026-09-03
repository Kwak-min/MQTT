# 프론트엔드 ↔ 게이트웨이 API 계약 요청서

대시보드 프론트엔드가 필요로 하는 데이터와, 현재 백엔드(`backend/`)에서 부족한 부분을 정리했습니다.
프론트엔드는 이미 이 계약대로 구현되어 있고, `VITE_DATA_SOURCE=http` 로 바꾸면 바로 붙습니다.

---

## 0. 먼저 고쳐야 할 버그 (화면과 무관하게 치명적)

**`app/socket/packet_parser.py` `_parse_publish()` 의 페이로드 슬라이싱**

`protocol.h`의 `PublishPacket`은 `payload[128]` 뒤에 `network_status`/`data_urgency` 비트필드 1바이트가 붙습니다.
현재 코드는 `raw[7:]`을 통째로 디코딩한 뒤 `.rstrip("\x00")` 만 합니다.
메타데이터 바이트가 `\x01`(불안정 또는 긴급)이면 마지막 문자가 null이 아니라 잘리지 않고,
`json.loads`가 실패해 **텔레메트리가 통째로 유실**됩니다.

즉 **네트워크가 불안정하거나 긴급 상황일 때만 데이터가 사라집니다.** 대시보드가 가장 필요한 순간에 비는 구조입니다.

```python
PAYLOAD_LEN = 128
META_OFFSET = PUBLISH_MIN_LEN + PAYLOAD_LEN   # 7 + 128 = 135

payload_raw = raw[PUBLISH_MIN_LEN:META_OFFSET].decode("utf-8", "ignore").rstrip("\x00")
meta = raw[META_OFFSET] if len(raw) > META_OFFSET else 0
network_status = meta & 0b11
data_urgency = (meta >> 2) & 0b11
```

---

## 1. 공통 규약

- **시각**: 전부 ISO 8601 + 오프셋. `2026-09-02T17:50:05+09:00`
  (현재는 `time.strftime("%Y-%m-%dT%H:%M:%S")` 라 타임존이 없어 차트 X축 처리에 위험합니다.)
- **단위**: 게이트웨이는 SI 기본 단위로 보내고 표시 단위 변환은 프론트가 합니다.
  가스 저항은 Ω로 보내주세요 (프론트에서 kΩ 변환). 에너지는 mWh, 시간은 ms.
- **없는 값**: `0`이 아니라 `null`. 0과 "측정 안 됨"은 화면에서 다르게 그립니다.
- **CORS**: 개발 중에는 `*`, 배포 시에는 대시보드 오리진만.
- **인증**: 현재 `POST /api/v1/control` 이 무인증입니다. 폐쇄망이 아니면 토큰이 필요합니다.

---

## 2. 기존 엔드포인트 — 필드 추가 요청

### `GET /api/v1/telemetry/latest`

현재도 있지만 아래 필드가 없어 화면 상단 절반이 비어 있습니다.

```jsonc
{
  "environment": {
    "timestamp": "2026-09-02T17:50:05+09:00",
    "client_id": "nodeB_001",
    "temp": 32.5,
    "hum": 45.2,
    "gas": 18500.0,          // Ω
    "gas_valid": true,
    "msg_id": 6731,
    "qos": 2,
    "topic_id": 2,

    // ↓ 신설 요청
    "rssi": -84,             // 펌웨어가 페이로드에 넣어야 함 (지금은 임계 판정에만 사용)
    "battery": 85,           // 페이로드에 이미 있으나 백엔드가 추출하지 않음
    "nn_score": 0.512,       // 동일
    "network_status": 1,     // 비트필드 파싱 필요 (0번 항목)
    "data_urgency": 1        // 동일
  },
  "power": {
    "nodeB_001": {
      "timestamp": "...",
      "estimated_energy_mwh": 42.1,
      "rtt_ms": 18.4,
      "retry_count": 0,
      "sleep_mode_ratio": 0.83
    }
  },
  "server_time": "2026-09-02T17:50:05+09:00"
}
```

**펌웨어 변경도 함께 필요합니다.** `main_gingerbread.cpp`의 payload `snprintf`에 `"rssi":%d` 를 추가해 주세요.
현재 RSSI는 `run_agent_inference()` 안에서 임계 판정에만 쓰이고 게이트웨이로 전송되지 않습니다.

### `GET /api/v1/logs/telemetry?limit=&from=&to=&qos=`

현재 `limit`만 받습니다. 화면의 기간·QoS 필터를 위해 `from` / `to` / `qos` 추가를 요청합니다.
행에는 아래 필드가 더 필요합니다.

```jsonc
{ "msg_type": "PUBLISH", "network_status": 1, "data_urgency": 1,
  "delivered": true, "retry_count": 0, "rtt_ms": 18.4 }
```

`retry_count`/`rtt_ms`는 지금 `power.csv`에만 있어 조인이 필요합니다.
**`power.csv`에 `msg_id`를 추가**해 주시면 조인 키가 생겨 가장 깔끔합니다.

---

## 3. 신설 요청 엔드포인트

### `GET /api/v1/stats/summary?qos=&range_minutes=` — **최우선**

실험 화면 전체가 이 하나에 달려 있습니다. 지금은 집계 API가 없어서
프론트가 CSV 전량을 받아 직접 계산해야 하는데, `limit` 상한이 1000행이라 장시간 실험은 불가능합니다.

```jsonc
{
  "window": { "from": "...", "to": "...", "sample_count": 1200 },
  "injected_loss_pct": 20.0,
  "systems": {
    "gingerbread": {
      "success_rate_pct": 98.5,
      "packet_loss_pct": 1.5,
      "avg_latency_ms": 45.0,
      "cumulative_energy_mwh": 42.1,
      "retry_per_100": 3,
      "qos_distribution": [
        { "qos": 0, "share_pct": 70 },
        { "qos": 1, "share_pct": 20 },
        { "qos": 2, "share_pct": 10 }
      ],
      "energy_series":  [{ "minute": 0, "value": 0 }, { "minute": 10, "value": 7.0 }],
      "latency_series": [{ "minute": 0, "value": 38 }]
    },
    "legacy": { "...동일 스키마..." }
  }
}
```

**`legacy`(베이스라인) 데이터가 지금 백엔드에 전혀 들어오지 않습니다.**
Node 2(`main_standard_MQTT.cpp`)는 Mosquitto의 `environmental/standard` 토픽으로 publish하는데,
백엔드의 `ConfigService` MQTT 클라이언트는 설정 발행 전용이고 `subscribe` 호출이 한 줄도 없습니다.
**해당 토픽 구독 + baseline CSV 적재**가 실험 화면의 전제 조건입니다.

### `GET /api/v1/protocol/handshake?qos=0|1|2`

프로토콜 화면의 시퀀스 다이어그램용. 각 단계의 **실측 경과 시간**이 핵심입니다(시간축이 이 값에 비례합니다).

```jsonc
{
  "qos": 2,
  "msg_id": "0x1A4B",
  "messages": [
    { "name": "PUBLISH",  "direction": "up",   "t_ms": 0.0 },
    { "name": "PUBREC",   "direction": "down", "t_ms": 5.8 },
    { "name": "PUBREL",   "direction": "up",   "t_ms": 11.2 },
    { "name": "PUBCOMP",  "direction": "down", "t_ms": 18.4 }
  ],
  "rtt_ms": 18.4,
  "retry": { "msg_id": "0x1A48", "retry_count": 1, "lost": true }
}
```

참고: 와이어프레임에 있던 `CONNACK`·`REGISTER`는 현재 프로토콜(`MsgType` 1~7)에 정의되지 않은 메시지라 화면에서 뺐습니다. 추가할 계획이 있으면 알려주세요.

### `WS /ws/telemetry` — 프레임 보강

이미 존재하지만 `estimated_energy_mwh`만 병합해 보냅니다.
`rtt_ms`, `retry_count`, `qos`, `rssi`, `network_status`, `data_urgency`를 함께 실어 주시면
실시간 카드 대부분이 REST 폴링 없이 갱신됩니다.

---

## 4. 정리 요청

- `PowerListener`(포트 6000)와 `record_power_telemetry()`는 deprecated 데드 코드인데 여전히 기동됩니다.
  `/api/telemetry/power`, `/api/diagnostics`의 power 항목이 항상 비어서 나옵니다.
- `control_service`의 예시가 `device_port: 8888`인데 펌웨어는 `udp.begin(UDP_SERVER_PORT)` = **5000**으로 바인딩합니다.
  다운링크가 도달하지 않습니다. 포트를 맞추고, 프론트가 대상 IP/포트를 알 수 있도록
  `GET /api/sessions` 응답에 제어용 포트를 포함해 주세요.

---

## 5. 우선순위

| 순위 | 항목 | 없으면 비는 화면 |
|---|---|---|
| 1 | packet_parser 메타데이터 파싱 버그 | 불안정·긴급 시 전 화면 |
| 2 | `latest`에 rssi·battery·nn_score·network_status·data_urgency | KPI 4장 중 2장, 상태 스트립, AI 판단 패널 |
| 3 | `/api/v1/stats/summary` | 실험 화면 전체 |
| 4 | Mosquitto baseline 토픽 구독 | 실험 화면의 모든 비교 |
| 5 | 로그 API 기간·QoS 필터 + `msg_id` 조인 키 | 패킷 로그 테이블, 필터 |
| 6 | `/api/v1/protocol/handshake` | 프로토콜 화면 |
| 7 | WS 프레임 보강 | 실시간성(현재는 5초 폴링으로 대체 동작) |

1~2번만 끝나도 운영 화면은 실제 데이터로 채워집니다. 3~4번이 실험 화면의 전제 조건입니다.
