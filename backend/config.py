"""
backend/config.py
─────────────────────────────────────────────────────────────────────────────
Central configuration file for the IoT Gateway Backend.
All magic numbers, ports, and file paths live here — import this module
instead of hard-coding values anywhere else.
─────────────────────────────────────────────────────────────────────────────
"""

import os

# ──────────────────────────────────────────────────────────────────────────────
# Network
# ──────────────────────────────────────────────────────────────────────────────

UDP_HOST = "0.0.0.0"               # Listen on all available interfaces

# Node B  ──  Gingerbread (Low-Power Custom UDP Protocol)
GINGERBREAD_PORT = 5000

# ESP32-C3 Power MCU  ──  JSON power streaming (current, voltage, power)
POWER_PORT = 6000

# UDP receive buffer size (bytes)
UDP_BUFFER_SIZE = 1024

# ──────────────────────────────────────────────────────────────────────────────
# Logging / Persistence
# ──────────────────────────────────────────────────────────────────────────────

# Resolve paths relative to this config file so the backend works from any cwd
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_DIR           = os.path.join(_BASE_DIR, "logs")
TELEMETRY_CSV     = os.path.join(LOG_DIR, "telemetry.csv")
POWER_CSV         = os.path.join(LOG_DIR, "power.csv")
SESSION_LOG_CSV   = os.path.join(LOG_DIR, "sessions.csv")

# ──────────────────────────────────────────────────────────────────────────────
# QoS
# ──────────────────────────────────────────────────────────────────────────────

# Seconds to hold a QoS 2 in-flight record before expiring it
QOS2_TIMEOUT_SECONDS = 30

# ──────────────────────────────────────────────────────────────────────────────
# Gingerbread Protocol — Message Type Constants
# (Must match firmware definitions in Node B ESP32-S3)
# ──────────────────────────────────────────────────────────────────────────────

MSG_CONNECT    = 1
MSG_PUBLISH    = 2
MSG_PUBACK     = 3   # QoS 1 acknowledgement  (server → client)
MSG_DISCONNECT = 4
MSG_PUBREC     = 5   # QoS 2 step 1  (server → client: "received")
MSG_PUBREL     = 6   # QoS 2 step 2  (client → server: "release")
MSG_PUBCOMP    = 7   # QoS 2 step 3  (server → client: "complete")

# ──────────────────────────────────────────────────────────────────────────────
# Packet Format Constants
# ──────────────────────────────────────────────────────────────────────────────

CONNECT_MIN_LEN  = 18   # 2-byte header + 16-byte client_id
PUBLISH_MIN_LEN  = 7    # 2-byte header + 2-byte msg_id + 1-byte qos + 2-byte topic_id
