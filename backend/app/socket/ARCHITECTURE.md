# backend/app/socket/ARCHITECTURE.md

# Socket Layer Architecture

This directory implements the **Transport Layer** of the IoT Gateway Backend.
It is responsible for:
1. Binding UDP sockets and running them on daemon threads
2. Translating raw bytes into typed packet dataclasses
3. Implementing the full QoS 0/1/2 handshake state machine

---

## Data Flow

```
                         ┌──────────────────────────────────────────┐
                         │             app/socket/                   │
                         │                                          │
  [ESP32-S3 Node B]      │  GingerbreadListener (port 5000)         │
  Binary UDP packets ───►│    _run_loop() [daemon thread]           │
                         │      │                                   │
                         │      ▼                                   │
                         │  packet_parser.parse_gingerbread_packet() │
                         │      │                                   │
                         │      ├─── ConnectPacket ────────────────►│ on_connect()
                         │      │                                   │   └─► SessionService.on_connect()
                         │      ├─── DisconnectPacket ─────────────►│ on_disconnect()
                         │      │                                   │   └─► SessionService.on_disconnect()
                         │      ├─── PubrelPacket ─────────────────►│ QoSHandler.handle_pubrel()
                         │      │                                   │   └─► sends PUBCOMP → ESP32
                         │      └─── PublishPacket ────────────────►│ QoSHandler.handle_publish()
                         │              │                           │   ├─ QoS 0: on_deliver() immediately
                         │              │                           │   ├─ QoS 1: sends PUBACK → ESP32
                         │              │                           │   │          on_deliver()
                         │              │                           │   └─ QoS 2: sends PUBREC → ESP32
                         │              │                           │          (waits for PUBREL)
                         │              │                           │          then sends PUBCOMP
                         │              ▼                           │          on_deliver()
                         │         on_deliver(PublishPacket)        │
                         │              │                           │
                         │              ▼                           │
                         │     SessionService.increment_packet()    │
                         │     TelemetryService.record_env()        │
                         │     SSE fan-out queue (→ frontend)       │
                         │                                          │
  [ESP32-C3 Power MCU]   │  PowerListener (port 6000)               │
  JSON UDP stream    ───►│    _run_loop() [daemon thread]           │
                         │      │                                   │
                         │      ▼                                   │
                         │  packet_parser.parse_power_packet()      │
                         │      │                                   │
                         │      ▼                                   │
                         │  on_power(PowerPacket)                   │
                         │      └─► TelemetryService.record_power() │
                         └──────────────────────────────────────────┘
```

---

## Module Responsibilities

| Module | Responsibility |
|--------|----------------|
| `udp_listener.py` | Bind socket, run daemon thread, dispatch raw bytes, expose metrics |
| `packet_parser.py` | Translate raw bytes → typed dataclasses (pure functions, no state) |
| `qos_handler.py` | QoS 0/1/2 state machine, send ACKs, manage in-flight QoS 2 records |

---

## QoS 2 State Machine

```
Client (ESP32)                 Server (QoSHandler)
    │                               │
    │── PUBLISH (qos=2) ───────────►│  stores QoS2Record{AWAITING_PUBREL}
    │                               │  sends PUBREC
    │◄─ PUBREC ─────────────────────│
    │                               │
    │── PUBREL ─────────────────────►│  updates state to PUBREL_RECEIVED
    │                               │  sends PUBCOMP
    │                               │  calls on_deliver(packet)  ← service layer
    │◄─ PUBCOMP ─────────────────────│  removes record from pending map
    │                               │
```

---

## Timeout / GC

The `QoSHandler` runs a background **garbage-collector thread** (`qos2-gc`) that
wakes up every `QOS2_TIMEOUT_SECONDS / 2` seconds and evicts any QoS 2 records
older than `QOS2_TIMEOUT_SECONDS` (default 30 s).

This handles the case where an ESP32 goes back to deep sleep after sending PUBLISH
but before sending PUBREL (e.g. power loss, firmware panic).
