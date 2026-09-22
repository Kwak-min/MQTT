# Instructions for AI Coding Agent: Project Gingerbread

## 1. Project Overview
This project (`MQTT-feat-sw-power-estimation`) aims to optimize battery power consumption in IoT edge devices by dynamically switching network protocols (UDP vs. TCP), transmission intervals, and Deep Sleep modes based on combined Temperature and Humidity thresholds (Gingerbread Protocol).

---

## 2. Core Specification: Gingerbread Protocol

Determine the QoS state based on the higher severity level between Temperature and Humidity (OR logic).

| QoS Level | Mode | Condition (Temp / Humidity) | Protocol | Interval | Sleep Mode | Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **QoS 0** | Normal | Temp ≤ 30°C **AND** Humidity ≤ 70% | **UDP** | 60 sec | Deep Sleep immediately after Tx | Fire-and-forget, minimal overhead |
| **QoS 1** | Warning | (30°C < Temp ≤ 50°C) **OR** (70% < Humidity ≤ 85%) | **UDP** | 30 sec | Short repeated Deep Sleep | Maintains UDP, increases sample rate |
| **QoS 2** | Emergency | Temp > 50°C **OR** Humidity > 85% | **TCP** | 3 sec | Deep Sleep (short) | Establishes TCP connection, 100% ACK guaranteed |

> All three levels use real `esp_deep_sleep_start()` — only the sleep duration changes (60s → 30s → 3s as severity rises), so detection can never be "instant," but once an anomaly is first caught the check interval tightens fast. Going much shorter than 3s at QoS 2 trades away Deep Sleep's power benefit, since each wake pays a fixed Wi-Fi/MQTT reconnect cost (~0.5-1s) that becomes a larger fraction of a very short cycle — acceptable here because QoS 2 prioritizes reliability over power savings.
>
> **Who decides the level above (Temp/Humidity → QoS) is TinyML, not this table directly.** A 2-input (temp, humidity) MLP (`firmware/include/mlp_inference.h` + `mlp_weights.h`) computes a risk score and maps it to QoS 0/1/2 once trained (`MLP_WEIGHTS_TRAINED=1` in `mlp_weights.h`, produced by `ml_model/train.py --features temp,hum`). Until real data is collected and the network is trained, the firmware falls back to exactly the rule in the table above (plus a narrow, hard-clamped TinyML calibration on the thresholds, `qos_calibration.h`) so behavior stays safe and well-defined either way. Boot and per-cycle logs are tagged `(NN)` or `(rule)` to show which path is active.

---

## 3. System Architecture & Module Responsibilities

### A. Firmware (`firmware/src/main_gingerbread.cpp`)
- Evaluate sensor metrics (Temp/Humidity) and switch state between QoS 0, 1, and 2.
- **QoS 0 & 1**: Send payload via UDP to `udp_listener`, then Deep Sleep (`esp_deep_sleep_start()`) for the level's interval (60s / 30s).
- **QoS 2**: Establish a TCP socket, transmit the emergency payload to the Gingerbread TCP listener (port 5001), then Deep Sleep for the short 3s interval so the next check happens almost immediately.

### B. Backend (`backend/app/`)
- **`socket/udp_listener.py` & `services/standard_mqtt_listener.py`**: Asynchronously listen to UDP and TCP incoming streams.
- **`socket/qos_handler.py`**: Validate received QoS state and log telemetry to `logs/telemetry.csv` & `logs/sessions.csv`.
- **`services/power_estimator.py`**: Compute theoretical power consumption ($P_{\text{est}}$) based on transmission counts ($N_{\text{Tx}}$) and sleep duration ($T_{\text{sleep}}$) using datasheet specifications, then log to `logs/power.csv`.
  - Formula: $P_{\text{est}} = (N_{\text{Tx}} \times E_{\text{Tx}}) + (T_{\text{sleep}} \times P_{\text{sleep}})$

### C. Dashboard (`dashboard.py`)
- Real-time visualization using Streamlit for Temp/Humidity, Active Protocol (UDP/TCP), QoS level, and estimated power usage.

---

## 4. Implementation Guidelines for AI
1. **Maintain Separation**: Do not mix `main_standard_MQTT.cpp` (Standard TCP mode) with `main_gingerbread.cpp` (Adaptive mode).
2. **Comment Code**: Add clear Korean inline comments at protocol switching boundaries (e.g., `// [QoS 0 -> QoS 1 스위칭]`).
3. **Refactor Focus**: Prioritize getting `main_gingerbread.cpp`, `qos_handler.py`, and `power_estimator.py` aligned with the specification above.
