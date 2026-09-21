"""
backend/tools/e2e_gateway_sim.py
─────────────────────────────────────────────────────────────────────────────
게이트웨이 통합(종단간) 시뮬레이션. 보드 없이 PC 한 대에서 "실제 main.py"를 띄우고, 가짜 디바이스가 펌웨어와
같은 바이트 형식으로 패킷을 보내서 세션·텔레메트리·전력 기록·REST가 제대로 동작하는지 확인합니다.

  python backend/tools/e2e_gateway_sim.py            # 하드웨어 작업 전에 PC 쪽이 정상인지 점검할 때

하는 일
  1) backend/ 를 임시 폴더로 복사합니다 (로그도 임시 폴더에 쌓이므로 저장소의 logs/는 건드리지 않음).
  2) 복사본의 config.py 가 가리키는 MQTT 브로커를 127.0.0.1 로 바꾸고, 이 파일의 초소형 MQTT 브로커를 띄웁니다.
  3) `python main.py` 를 실제로 실행합니다 (UDP 5000, TCP 5001, UDP 6000, REST 8080, MQTT 클라이언트).
  4) 가짜 Gingerbread(UDP: QoS 0/1/2, TCP: QoS 2)와 가짜 Standard(MQTT)로 시나리오를 실행하고 결과를 검증합니다.

검증하지 못하는 것: 실제 ESP32의 동작, WiFi, 실제 mosquitto와의 호환성(여기서는 최소 구현 브로커),
                    256dpi/MQTT 클라이언트의 재연결. 이 도구는 "PC 쪽 배선"을 점검하는 것입니다.

필요한 포트(모두 비어 있어야 함): UDP 5000, 6000 / TCP 5001, 8080, 1883
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.dirname(HERE)

UDP_PORT, TCP_PORT, POWER_PORT, REST_PORT, MQTT_PORT = 5000, 5001, 6000, 8080, 1883
# ConnectPacket.client_id 는 16바이트(널 없음)라 더 긴 ID는 잘립니다. 실제 펌웨어의 BOARD1_CLIENT_ID("ESP32-Gingerbread",
# 17자)도 게이트웨이에는 16자로 잘려 등록되므로, 시뮬레이션도 같은 값을 씁니다.
DEVICE_ID = "ESP32-Gingerbread"[:16]
STD_CLIENT_ID = "ESP32-Standard-MQTT"


# ══════════════════════════════════════════════════════════════════════════════
# 초소형 MQTT 3.1.1 브로커 (테스트 전용: 정확한 토픽 일치, 보존 메시지, QoS 0/1)
# ══════════════════════════════════════════════════════════════════════════════

def _encode_len(n: int) -> bytes:
    out = bytearray()
    while True:
        b, n = n % 128, n // 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_packet(sock: socket.socket):
    """(첫 바이트, 본문) 또는 연결이 끝나면 None."""
    first = sock.recv(1)
    if not first:
        return None
    mult, length = 1, 0
    while True:
        b = sock.recv(1)
        if not b:
            return None
        length += (b[0] & 0x7F) * mult
        if not b[0] & 0x80:
            break
        mult *= 128
    body = b""
    while len(body) < length:
        chunk = sock.recv(length - len(body))
        if not chunk:
            return None
        body += chunk
    return first[0], body


class MiniBroker:
    def __init__(self, port: int) -> None:
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", port))
        self.srv.listen(8)
        self.srv.settimeout(0.5)
        self.stop_flag = threading.Event()
        self.lock = threading.Lock()
        self.subs = {}          # topic → {sock: granted_qos}
        self.retained = {}      # topic → payload
        self.published = []     # (topic, payload, qos, retain) 브로커가 받은 모든 PUBLISH
        self._pid = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def close(self) -> None:
        self.stop_flag.set()
        try:
            self.srv.close()
        except OSError:
            pass

    def _accept(self) -> None:
        while not self.stop_flag.is_set():
            try:
                conn, _ = self.srv.accept()
            except (socket.timeout, OSError):
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _next_pid(self) -> int:
        self._pid = self._pid % 65535 + 1
        return self._pid

    def _send_publish(self, conn, topic: str, payload: bytes, qos: int, retain: bool) -> None:
        tb = topic.encode()
        head = struct.pack(">H", len(tb)) + tb
        first = 0x30 | (qos << 1) | (1 if retain else 0)
        if qos:
            head += struct.pack(">H", self._next_pid())
        body = head + payload
        try:
            conn.sendall(bytes([first]) + _encode_len(len(body)) + body)
        except OSError:
            pass

    def _serve(self, conn) -> None:
        conn.settimeout(30)
        try:
            while not self.stop_flag.is_set():
                pkt = _read_packet(conn)
                if pkt is None:
                    return
                first, body = pkt
                ptype = first >> 4
                if ptype == 1:                                   # CONNECT → CONNACK
                    conn.sendall(bytes([0x20, 0x02, 0x00, 0x00]))
                elif ptype == 8:                                 # SUBSCRIBE → SUBACK (+ 보존 메시지 전달)
                    pid = body[:2]
                    i, granted, topics = 2, [], []
                    while i < len(body):
                        tl = struct.unpack(">H", body[i:i + 2])[0]
                        topic = body[i + 2:i + 2 + tl].decode()
                        qos = min(body[i + 2 + tl], 1)
                        i += 3 + tl
                        with self.lock:
                            self.subs.setdefault(topic, {})[conn] = qos
                        granted.append(qos)
                        topics.append(topic)
                    conn.sendall(bytes([0x90]) + _encode_len(2 + len(granted)) + pid + bytes(granted))
                    for t in topics:
                        if t in self.retained:
                            self._send_publish(conn, t, self.retained[t], 0, True)
                elif ptype == 10:                                # UNSUBSCRIBE → UNSUBACK
                    conn.sendall(bytes([0xB0, 0x02]) + body[:2])
                elif ptype == 3:                                 # PUBLISH
                    qos, retain = (first >> 1) & 3, bool(first & 1)
                    tl = struct.unpack(">H", body[:2])[0]
                    topic = body[2:2 + tl].decode()
                    i = 2 + tl
                    pid = b""
                    if qos:
                        pid, i = body[i:i + 2], i + 2
                    payload = body[i:]
                    if qos == 1:
                        conn.sendall(bytes([0x40, 0x02]) + pid)   # PUBACK
                    with self.lock:
                        self.published.append((topic, payload, qos, retain))
                        if retain:
                            self.retained[topic] = payload
                        targets = list(self.subs.get(topic, {}).items())
                    for sub_sock, granted in targets:
                        self._send_publish(sub_sock, topic, payload, min(qos, granted), False)
                elif ptype == 12:                                # PINGREQ → PINGRESP
                    conn.sendall(bytes([0xD0, 0x00]))
                elif ptype == 14:                                # DISCONNECT
                    return
                # PUBACK(4) 등은 무시
        except (OSError, struct.error, UnicodeDecodeError):
            return
        finally:
            with self.lock:
                for d in self.subs.values():
                    d.pop(conn, None)
            try:
                conn.close()
            except OSError:
                pass


# ══════════════════════════════════════════════════════════════════════════════
# 펌웨어와 같은 바이트 형식의 패킷 (firmware/include/protocol.h, backend/config.py 참조)
# ══════════════════════════════════════════════════════════════════════════════

MSG_CONNECT, MSG_PUBLISH, MSG_PUBACK, MSG_DISCONNECT, MSG_PUBREC, MSG_PUBREL, MSG_PUBCOMP = 1, 2, 3, 4, 5, 6, 7


def connect_pkt(client_id: str) -> bytes:
    return struct.pack("<BB16sH", 20, MSG_CONNECT, client_id.encode().ljust(16, b"\0"), 5)


def publish_pkt(msg_id: int, qos: int, topic_id: int, payload: dict) -> bytes:
    """publish_packet_size() 형식: 헤더 2 + msg_id 2 + qos 1 + topic_id 2 + JSON + NUL (비트필드 바이트 제외).
    실제 펌웨어의 header.length는 uint8 오버플로로 8이 들어가므로 여기서도 8을 씁니다."""
    body = json.dumps(payload, separators=(",", ":")).encode() + b"\0"
    return struct.pack("<BBHBH", 8, MSG_PUBLISH, msg_id, qos, topic_id) + body


def ack_pkt(msg_type: int, msg_id: int) -> bytes:
    return struct.pack("<BBH", 4, msg_type, msg_id)


def tcp_frame(pkt: bytes) -> bytes:
    return struct.pack("<H", len(pkt)) + pkt


# ══════════════════════════════════════════════════════════════════════════════
# 실행 도우미
# ══════════════════════════════════════════════════════════════════════════════

def wait_for(cond, timeout=6.0, step=0.05):
    end = time.time() + timeout
    while time.time() < end:
        try:
            v = cond()
        except Exception:
            v = None
        if v:
            return v
        time.sleep(step)
    return None


def http_get(path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{REST_PORT}{path}", timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def http_post(path: str, payload: dict):
    req = urllib.request.Request(f"http://127.0.0.1:{REST_PORT}{path}", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))


def read_csv(path: str):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def ports_free() -> list:
    busy = []
    for kind, port in ((socket.SOCK_DGRAM, UDP_PORT), (socket.SOCK_DGRAM, POWER_PORT), (socket.SOCK_STREAM, TCP_PORT),
                       (socket.SOCK_STREAM, REST_PORT), (socket.SOCK_STREAM, MQTT_PORT)):
        s = socket.socket(socket.AF_INET, kind)
        try:
            s.bind(("0.0.0.0", port))
        except OSError:
            busy.append(port)
        finally:
            s.close()
    return busy


def main() -> int:
    busy = ports_free()
    if busy:
        print(f"필요한 포트가 이미 사용 중입니다: {busy}. 게이트웨이나 브로커가 떠 있다면 종료하고 다시 실행하세요.")
        return 2

    work = tempfile.mkdtemp(prefix="gw_e2e_")
    gw_dir = os.path.join(work, "backend")
    shutil.copytree(BACKEND, gw_dir, ignore=shutil.ignore_patterns("__pycache__", "logs", "tools", "*.pyc"))
    os.makedirs(os.path.join(gw_dir, "logs"), exist_ok=True)
    # 복사본이 가리키는 MQTT 브로커를 로컬 초소형 브로커로 바꿉니다 (저장소의 config.py는 그대로)
    cfg_path = os.path.join(gw_dir, "config.py")
    cfg = open(cfg_path, encoding="utf-8").read()
    cfg, n = re.subn(r'^MQTT_BROKER_HOST\s*=\s*".*"', 'MQTT_BROKER_HOST = "127.0.0.1"', cfg, flags=re.M)
    assert n == 1, "config.py 에서 MQTT_BROKER_HOST 를 찾지 못했습니다"
    open(cfg_path, "w", encoding="utf-8").write(cfg)
    logs = os.path.join(gw_dir, "logs")

    broker = MiniBroker(MQTT_PORT)
    out_path = os.path.join(work, "gateway.log")
    out = open(out_path, "w", encoding="utf-8")
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "main.py"], cwd=gw_dir, stdout=out, stderr=subprocess.STDOUT, env=env)

    results = []

    def check(name, cond, detail=""):
        results.append(bool(cond))
        print(("PASS" if cond else "FAIL"), name, detail)

    try:
        # ── 기동 ────────────────────────────────────────────────────────────────
        up = wait_for(lambda: http_get("/api/health"), timeout=20)
        check("0) main.py 기동: REST /api/health 응답", up is not None, f"| {up}")
        if up is None:
            raise RuntimeError("게이트웨이가 시작되지 않았습니다")
        tcp_ok = wait_for(lambda: socket.create_connection(("127.0.0.1", TCP_PORT), timeout=1).close() or True, timeout=5)
        check("   TCP 5001 리스너가 열려 있음", tcp_ok)

        # ── 가짜 Gingerbread (UDP) ──────────────────────────────────────────────
        dev = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        dev.bind(("127.0.0.1", 0))
        dev.settimeout(2.0)
        gw = ("127.0.0.1", UDP_PORT)
        dev_port = dev.getsockname()[1]

        dev.sendto(connect_pkt(DEVICE_ID), gw)
        sess = wait_for(lambda: DEVICE_ID in json.dumps(http_get("/api/sessions")))
        check("1) UDP CONNECT → 세션 등록 (REST /api/sessions)", sess)

        tel = lambda: read_csv(os.path.join(logs, "telemetry.csv"))
        pwr = lambda: read_csv(os.path.join(logs, "power.csv"))

        dev.sendto(publish_pkt(10, 0, 1, {"temp": 24.5, "hum": 50.0, "gas": 120.0, "qos": 0, "sleep_r": 0.0}), gw)
        check("2) UDP QoS 0 PUBLISH → 즉시 기록 (응답 없음)", wait_for(lambda: len(tel()) >= 1),
              f"| telemetry 행 {len(tel())}")

        dev.sendto(publish_pkt(11, 1, 1, {"temp": 25.0, "hum": 51.0, "gas": 118.0, "qos": 1, "sleep_r": 0.0}), gw)
        ack = dev.recv(64)
        check("3) UDP QoS 1 PUBLISH → PUBACK(msg_id=11) 수신", ack == ack_pkt(MSG_PUBACK, 11), f"| {ack.hex()}")
        wait_for(lambda: len(tel()) >= 2)

        dev.sendto(publish_pkt(12, 2, 1, {"temp": 60.0, "hum": 30.0, "gas": 80.0, "qos": 2, "sleep_r": 0.0}), gw)
        rec = dev.recv(64)
        before = len(tel())
        dev.sendto(ack_pkt(MSG_PUBREL, 12), gw)
        comp = dev.recv(64)
        check("4) UDP QoS 2 4단계: PUBLISH→PUBREC, PUBREL→PUBCOMP",
              rec == ack_pkt(MSG_PUBREC, 12) and comp == ack_pkt(MSG_PUBCOMP, 12), f"| {rec.hex()} / {comp.hex()}")
        check("   QoS 2는 PUBREL 이후에 전달(기록)됨", wait_for(lambda: len(tel()) > before))

        # 실측 메트릭(topic 2) — 전력 추정. 헤더 QoS는 0이지만 페이로드의 qos(1)로 계산되어야 함
        dev.sendto(publish_pkt(13, 0, 2, {"qos": 1, "rtt": 15.3, "retry": 0, "sleep_r": 0.9, "act": 120, "slp": 5000,
                                          "tp": "udp", "ls": 0.0, "rr": 1.0, "cg": 0, "pb": 1, "pkt": 4, "bytes": 800}), gw)
        row = wait_for(lambda: pwr()[-1] if pwr() else None)
        check("5) UDP 메트릭(topic 2) → 전력 행 (페이로드의 QoS 1, 사이클 에너지 계산)",
              row and row["client_id"] == DEVICE_ID and row["qos"] == "1" and abs(float(row["estimated_energy_mwh"]) - 0.004515) < 1e-5,
              f"| {dict(row) if row else None}")

        # ── TCP (QoS 2) ─────────────────────────────────────────────────────────
        tcp_before = len(tel())
        s = socket.create_connection(("127.0.0.1", TCP_PORT), timeout=3)
        s.sendall(tcp_frame(publish_pkt(20, 2, 1, {"temp": 62.0, "hum": 28.0, "gas": 75.0, "qos": 2, "sleep_r": 0.0})))
        comp = s.recv(4)
        s.close()
        check("6) TCP QoS 2 PUBLISH → PUBCOMP(msg_id=20)", comp == ack_pkt(MSG_PUBCOMP, 20), f"| {comp.hex()}")
        wait_for(lambda: len(tel()) > tcp_before)
        last = tel()[-1] if tel() else {}
        check("   TCP 패킷의 client_id가 UDP CONNECT 세션으로 복원됨 (다른 발신 포트여도)",
              last.get("client_id") == DEVICE_ID, f"| client_id={last.get('client_id')}")

        # 같은 msg_id 재전송(ACK 유실 시나리오): PUBCOMP는 다시 주되 기록은 한 번만
        n_before = len(tel())
        s = socket.create_connection(("127.0.0.1", TCP_PORT), timeout=3)
        s.sendall(tcp_frame(publish_pkt(20, 2, 1, {"temp": 62.0, "hum": 28.0, "gas": 75.0, "qos": 2})))
        comp = s.recv(4)
        s.close()
        time.sleep(0.4)
        check("7) TCP 중복 msg_id 재전송: PUBCOMP 재응답, 기록은 늘지 않음",
              comp == ack_pkt(MSG_PUBCOMP, 20) and len(tel()) == n_before, f"| telemetry {n_before}→{len(tel())}")

        s = socket.create_connection(("127.0.0.1", TCP_PORT), timeout=3)
        s.sendall(tcp_frame(publish_pkt(21, 2, 2, {"qos": 2, "rtt": 42.0, "retry": 0, "sleep_r": 0.9, "act": 150,
                                                   "slp": 5000, "tp": "tcp", "ls": 2.5, "rr": 1.3, "cg": 0, "pb": 0})))
        s.recv(4)
        s.close()
        row = wait_for(lambda: pwr()[-1] if len(pwr()) >= 2 else None)
        check("8) TCP 메트릭(topic 2) → 전력 행 (QoS 2)", row and row["qos"] == "2" and row["client_id"] == DEVICE_ID,
              f"| qos={row['qos'] if row else None}")

        # ── REST: 최신 전력 스냅샷에 전송 계층·혼잡 지표가 노출되는지 ───────────────
        latest = http_get("/api/telemetry/latest/power")
        text = json.dumps(latest)
        check("9) REST 최신 전력 스냅샷에 transport / 혼잡 지표 포함",
              all(k in text for k in ("transport", "net_loss_pct", "congested")), f"| {text[:160]}...")

        # ── Standard 노드 (MQTT via 초소형 브로커) ──────────────────────────────
        import paho.mqtt.client as mqtt
        pub = mqtt.Client(client_id="e2e-standard-sim", clean_session=True)
        pub.connect("127.0.0.1", MQTT_PORT, 30)
        pub.loop_start()
        sub_ready = wait_for(lambda: len(broker.subs.get("environmental/standard", {})) > 0 and
                             len(broker.subs.get("environmental/standard/metrics", {})) > 0, timeout=10)
        check("10) 게이트웨이가 Standard 두 토픽(data, metrics)을 구독함", sub_ready,
              f"| 구독 토픽 {sorted(t for t, d in broker.subs.items() if d)}")
        n_tel, n_pwr = len(tel()), len(pwr())
        pub.publish("environmental/standard", json.dumps({"temp": 24.0, "hum": 48.0, "gas": 100.0, "qos": 1, "sleep_r": 0.0}), qos=1).wait_for_publish()
        wait_for(lambda: len(tel()) > n_tel)
        check("11) Standard 센서 메시지 → 환경 행만 기록 (전력 행 없음)",
              len(tel()) == n_tel + 1 and len(pwr()) == n_pwr and tel()[-1]["client_id"] == STD_CLIENT_ID,
              f"| telemetry +{len(tel()) - n_tel}, power +{len(pwr()) - n_pwr}")
        pub.publish("environmental/standard/metrics", json.dumps({"qos": 1, "rtt": 38.4, "retry": 0, "sleep_r": 0.0, "act": 5000,
                                                                  "slp": 0, "tp": "tcp", "pkt": 3, "bytes": 200}), qos=1).wait_for_publish()
        row = wait_for(lambda: pwr()[-1] if len(pwr()) > n_pwr else None)
        check("12) Standard 메트릭 → 전력 행 (QoS 1, 항상 켜진 5초 사이클)",
              row and row["client_id"] == STD_CLIENT_ID and row["qos"] == "1" and abs(float(row["estimated_energy_mwh"]) - 0.0974) < 2e-3,
              f"| energy={row['estimated_energy_mwh'] if row else None} mWh")
        pub.loop_stop()
        pub.disconnect()

        # ── 설정 동기화: POST /api/config → MQTT 발행(보존) ─────────────────────
        try:
            # 게이트웨이는 시작할 때도 현재 설정을 한 번 발행하므로, POST가 바꾼 값(손실 상한 3)이 담긴 발행을 기다립니다.
            n_cfg_before = len([p for p in broker.published if p[0] == "gingerbread/config"])
            resp = http_post("/api/config", {"NETWORK": {"RSSI_THRESHOLD": -75, "PACKET_LOSS_LIMIT": 3}})

            def changed_publish():
                for topic, payload, qos, retain in reversed(broker.published):
                    if topic == "gingerbread/config":
                        d = json.loads(payload.decode())
                        if d.get("NETWORK", {}).get("PACKET_LOSS_LIMIT") == 3:
                            return d, retain
                return None

            got = wait_for(changed_publish, timeout=5)
            cfg_msgs = [json.loads(p[1].decode()).get("NETWORK") for p in broker.published if p[0] == "gingerbread/config"]
            check("13) POST /api/config → 변경된 설정이 gingerbread/config 로 보존(retain) 발행",
                  got is not None and got[1], f"| POST 응답 {str(resp)[:90]} | 발행 {len(cfg_msgs)}건(POST 전 {n_cfg_before}건), 마지막 {cfg_msgs[-1] if cfg_msgs else None}")
            saved = http_get("/api/config")
            check("    GET /api/config 가 변경된 값을 돌려줌", "3" in json.dumps(saved), f"| {json.dumps(saved)[:120]}")
        except (urllib.error.URLError, ValueError) as exc:
            check("13) POST /api/config", False, f"| {exc}")

        # ── 비정상 입력이 게이트웨이를 죽이지 않는지 ─────────────────────────────
        for junk in (b"", b"\x01", b"\xff" * 300, b"\x02\x63" + b"\0" * 10):
            dev.sendto(junk, gw)
        s = socket.create_connection(("127.0.0.1", TCP_PORT), timeout=3)
        s.sendall(struct.pack("<H", 60000) + b"junk")
        s.close()
        s = socket.create_connection(("127.0.0.1", TCP_PORT), timeout=3)
        s.sendall(struct.pack("<H", 50) + b"\x00\x02abc")     # 잘린 프레임
        s.close()
        time.sleep(0.5)
        alive = http_get("/api/health")
        check("14) 깨진 UDP/TCP 입력(빈 패킷, 쓰레기, 과대 길이, 잘린 프레임) 후에도 게이트웨이가 살아 있음",
              alive is not None and proc.poll() is None)

        # ── 진단·세션 종료 ──────────────────────────────────────────────────────
        diag = http_get("/api/diagnostics")
        check("15) REST /api/diagnostics 응답", isinstance(diag, dict) and len(diag) > 0, f"| 키 {list(diag)[:6]}")
        dev.sendto(struct.pack("<BBB", 3, MSG_DISCONNECT, 1), gw)
        time.sleep(0.4)
        check("16) UDP DISCONNECT 처리 후에도 정상", http_get("/api/health") is not None)

        # ── 기록 파일 스키마 ────────────────────────────────────────────────────
        hdr = open(os.path.join(logs, "power.csv"), encoding="utf-8").readline().strip()
        check("17) power.csv 스키마 (기존 대시보드와 호환)",
              hdr == "timestamp,client_id,qos,rtt_ms,retry_count,sleep_mode_ratio,estimated_energy_mwh,packet_count,total_bytes", f"| {hdr}")
        # 확장 전력 로그: 분석(backend/tools/analyze_power.py)에 필요한 원시 입력과 혼잡·전송 계층이 기록되는지
        ext = read_csv(os.path.join(logs, "power_ext.csv"))
        udp_row = next((r for r in ext if r["client_id"] == DEVICE_ID and r["transport"] == "udp"), None)
        tcp_row = next((r for r in ext if r["client_id"] == DEVICE_ID and r["transport"] == "tcp"), None)
        std_row = next((r for r in ext if r["client_id"] == STD_CLIENT_ID), None)
        check("18) power_ext.csv: 전송 계층·사이클 시간·혼잡 지표 기록 (UDP/TCP/Standard 모두)",
              len(ext) == len(pwr()) and udp_row and tcp_row and std_row
              and udp_row["act_ms"] == "120.0" and udp_row["probe"] == "1" and tcp_row["qos"] == "2" and std_row["congested"] == "",
              f"| 행 {len(ext)}개(power.csv와 동일), UDP {bool(udp_row)}, TCP {bool(tcp_row)}, Standard {bool(std_row)}")
        # 분석 스크립트가 이 로그를 그대로 읽어 결과를 낼 수 있는지 (표본이 적어 경고가 나오는 것은 정상)
        shutil.copy(os.path.join(logs, "power_ext.csv"), os.path.join(work, "power_ext_copy.csv"))
        an = subprocess.run([sys.executable, os.path.join(HERE, "analyze_power.py"), "--csv", os.path.join(work, "power_ext_copy.csv"),
                             "--a", "ESP32-Gingerbread", "--b", "Standard", "--boot", "100"],
                            capture_output=True, text=True, encoding="utf-8", env=env)
        check("19) analyze_power.py 가 실제 게이트웨이가 쓴 로그를 그대로 분석함",
              an.returncode == 0 and "평균 전류 비" in an.stdout, f"| 종료 코드 {an.returncode}" + ("" if an.returncode == 0 else f" | {an.stderr[-200:]}"))
        dev.close()
    except Exception as exc:   # 시나리오 도중 예외: 원인을 남기고 실패 처리
        check("시뮬레이션 실행 중 예외", False, f"| {type(exc).__name__}: {exc}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        out.close()
        broker.close()

    ok = all(results) and len(results) > 3
    log_text = open(out_path, encoding="utf-8", errors="replace").read()
    errors = [l for l in log_text.splitlines() if re.search(r"Traceback|ERROR|CRITICAL", l)]
    print(f"\n게이트웨이 로그의 ERROR/Traceback: {len(errors)}줄")
    for l in errors[:8]:
        print("   ", l[:160])
    print("결과:", "모두 통과" if ok else "실패 있음", f"({sum(results)}/{len(results)})")
    if not ok:
        print(f"게이트웨이 전체 로그: {out_path}")
    else:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
