"""
backend/app/services/config_service.py
─────────────────────────────────────────────────────────────────────────────
Gingerbread 프로젝트 — 동적 설정 관리 및 MQTT 게이트웨이 동기화 서비스.

이 서비스는 두 가지 핵심 역할을 담당합니다:
  1. [설정 관리] config.json을 런타임에 동적으로 읽고 쓰기 (하드코딩 임계값 제거)
  2. [MQTT 동기화] 웹 대시보드에서 파라미터가 변경될 때마다 ESP32 엣지 디바이스에
     즉시 최신 설정을 브로드캐스트 (QoS 1, retain=True)

설정 파일 구조 (config.json):
  {
    "NETWORK": {
      "RSSI_THRESHOLD": -80,         // RSSI 신호 강도 임계값 (dBm)
      "PACKET_LOSS_LIMIT": 5         // 허용 패킷 손실률 상한 (%)
    },
    "ENVIRONMENT": {
      "GAS_THRESHOLD_KOHM": 20,      // BME680 가스 저항 위험 임계값 (kΩ)
      "TEMP_THRESHOLD_CELSIUS": 45   // 온도 위험 임계값 (°C)
    },
    "POWER_MANAGEMENT": {
      "POWER_MODE": "EXTERNAL_5V",   // 전원 모드 ("EXTERNAL_5V" | "BATTERY")
      "CURRENT_BATTERY_LEVEL": 100   // 현재 배터리 잔량 (0~100 정수)
    }
  }

MQTT 발행 규격:
  - 토픽  : gingerbread/config
  - QoS   : 1 (최소 1회 전달 보장)
  - Retain: True (ESP32가 연결 즉시 최신 설정을 수신할 수 있도록)
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

# paho-mqtt 라이브러리 — MQTT 브로커와의 통신을 담당합니다.
# 설치: pip install paho-mqtt
try:
    import paho.mqtt.client as mqtt
    _MQTT_AVAILABLE = True
except ImportError:
    # paho-mqtt가 설치되지 않은 경우 MQTT 기능을 비활성화하고
    # 서버는 계속 정상 동작합니다 (설정 읽기/쓰기는 유지됨).
    _MQTT_AVAILABLE = False

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 기본 설정값 — config.json이 없거나 손상된 경우 사용하는 폴백(fallback) 값
# ──────────────────────────────────────────────────────────────────────────────
_DEFAULT_CONFIG: Dict[str, Any] = {
    "NETWORK": {
        "RSSI_THRESHOLD": -80,        # RSSI 신호 강도 임계값 (dBm 단위, 기본값 -80)
        "PACKET_LOSS_LIMIT": 5,       # 패킷 손실률 허용 상한 (%, 기본값 5)
    },
    "ENVIRONMENT": {
        "GAS_THRESHOLD_KOHM": 20,     # 가스 저항 위험 임계값 (kΩ, 기본값 20)
        "TEMP_THRESHOLD_CELSIUS": 45, # 온도 위험 임계값 (°C, 기본값 45)
    },
    "POWER_MANAGEMENT": {
        "POWER_MODE": "EXTERNAL_5V",  # 전원 모드 기본값 (외부 5V 공급)
        "CURRENT_BATTERY_LEVEL": 100, # 배터리 잔량 기본값 (100% = 만충)
    },
}

# MQTT 브로커 기본 연결 설정
_DEFAULT_MQTT_HOST = "localhost"
_DEFAULT_MQTT_PORT = 1883
_MQTT_CONFIG_TOPIC = "gingerbread/config"  # ESP32가 구독하는 설정 토픽


class ConfigService:
    """
    Gingerbread 게이트웨이 서버의 동적 설정 관리 서비스.

    주요 기능:
    ──────────
    1. load_config()    : config.json을 읽어 메모리에 캐싱합니다.
                          파일이 없으면 기본값으로 자동 생성합니다.
    2. get_config()     : 현재 메모리에 캐싱된 설정 딕셔너리를 반환합니다.
    3. update_config()  : 5개 파라미터를 검증 후 config.json에 즉시 저장하고,
                          MQTT 브로커를 통해 ESP32에 설정을 즉시 푸시합니다.
    4. _publish_mqtt()  : gingerbread/config 토픽에 QoS=1, retain=True로 발행합니다.

    스레드 안전성:
    ─────────────
    Flask API 스레드와 UDP 리스너 스레드가 동시에 호출하더라도
    RLock으로 모든 config 읽기/쓰기를 보호합니다.

    MQTT 연결 복원력:
    ─────────────────
    브로커 연결 실패 또는 일시적 단절 시 자동 재연결을 시도합니다.
    MQTT가 없어도 설정 저장(config.json) 기능은 정상 동작합니다.
    """

    def __init__(
        self,
        config_path: Optional[str] = None,
        mqtt_host: str = _DEFAULT_MQTT_HOST,
        mqtt_port: int = _DEFAULT_MQTT_PORT,
    ) -> None:
        """
        ConfigService 초기화.

        매개변수
        ────────
        config_path : config.json 파일의 절대 경로.
                      None이면 backend/ 디렉토리 기준으로 자동 탐색합니다.
        mqtt_host   : MQTT 브로커 호스트 주소 (기본값: localhost).
        mqtt_port   : MQTT 브로커 포트 번호 (기본값: 1883).
        """
        # config.json 파일 경로 결정
        # 이 파일(config_service.py)의 위치를 기준으로 backend/ 루트를 탐색합니다.
        if config_path is None:
            # backend/app/services/ → backend/app/ → backend/
            _service_dir = os.path.dirname(os.path.abspath(__file__))
            _backend_dir = os.path.dirname(os.path.dirname(_service_dir))
            config_path = os.path.join(_backend_dir, "config.json")
        self._config_path: str = config_path

        # 설정 데이터 캐시 및 보호 락
        self._config: Dict[str, Any] = {}
        self._lock: threading.RLock = threading.RLock()

        # MQTT 브로커 연결 설정 값 저장
        self._mqtt_host = mqtt_host
        self._mqtt_port = mqtt_port
        self._mqtt_client: Optional["mqtt.Client"] = None

        # 서비스 시작 시 config.json을 즉시 로드합니다.
        self.load_config()

        # MQTT 클라이언트를 초기화하고 브로커 연결을 시도합니다.
        self._init_mqtt()

        logger.info(
            "[설정서비스] 초기화 완료. config.json 경로: %s | MQTT 브로커: %s:%d",
            self._config_path, self._mqtt_host, self._mqtt_port,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 퍼블릭 인터페이스 — 설정 읽기
    # ──────────────────────────────────────────────────────────────────────────

    def load_config(self) -> Dict[str, Any]:
        """
        config.json 파일을 디스크에서 읽어 메모리 캐시를 갱신합니다.

        처리 흐름:
          1. 파일이 존재하면 JSON으로 파싱합니다.
          2. 파일이 없으면 기본값(_DEFAULT_CONFIG)으로 자동 생성합니다.
          3. JSON 파싱 오류(손상된 파일) 시 기본값을 사용하고 오류를 로깅합니다.
          4. 파싱된 설정에 누락된 키가 있으면 기본값으로 채웁니다(딥 병합).

        반환값
        ──────
        현재 적용 중인 설정 딕셔너리의 복사본.
        """
        with self._lock:
            if not os.path.exists(self._config_path):
                # config.json이 없는 경우 → 기본값으로 새 파일 생성
                logger.warning(
                    "[설정서비스] config.json이 없습니다. "
                    "기본값으로 새 파일을 생성합니다: %s",
                    self._config_path,
                )
                self._config = _deep_copy_dict(_DEFAULT_CONFIG)
                self._write_config_to_disk(self._config)
                return dict(self._config)

            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)

                # 누락된 섹션/키를 기본값으로 보완하는 딥 병합 수행
                # 예: config.json에 ENVIRONMENT 섹션이 없더라도 오류 없이 기본값 사용
                merged = _deep_copy_dict(_DEFAULT_CONFIG)
                _deep_merge(merged, loaded)
                self._config = merged

                logger.info(
                    "[설정서비스] config.json 로드 완료: %s", self._config_path
                )

            except json.JSONDecodeError as exc:
                # JSON 파싱 실패 — 손상된 파일이거나 형식 오류
                logger.error(
                    "[설정서비스] config.json JSON 파싱 오류 → 기본값 사용: %s", exc
                )
                self._config = _deep_copy_dict(_DEFAULT_CONFIG)

            except OSError as exc:
                # 파일 읽기 권한 오류 등 OS 레벨 예외
                logger.error(
                    "[설정서비스] config.json 읽기 실패 → 기본값 사용: %s", exc
                )
                self._config = _deep_copy_dict(_DEFAULT_CONFIG)

            return dict(self._config)

    def get_config(self) -> Dict[str, Any]:
        """
        현재 메모리에 캐싱된 설정 딕셔너리를 반환합니다.

        디스크 I/O 없이 즉시 반환됩니다.
        반환값은 내부 상태를 보호하기 위한 깊은 복사본(deep copy)입니다.
        """
        with self._lock:
            return _deep_copy_dict(self._config)

    # ──────────────────────────────────────────────────────────────────────────
    # 퍼블릭 인터페이스 — 설정 갱신 (웹 대시보드 → 게이트웨이 → ESP32)
    # ──────────────────────────────────────────────────────────────────────────

    def update_config(self, updates: Dict[str, Any]) -> Dict[str, Any]:
        """
        웹 대시보드에서 전달된 설정 파라미터를 검증하고,
        config.json에 즉시 저장한 후 MQTT로 ESP32에 푸시합니다.

        처리 흐름:
          1. 수신된 파라미터 유효성 검증 (타입, 범위 체크)
          2. 메모리 캐시 갱신 (원자적 락 내부 처리)
          3. config.json 파일 즉시 덮어쓰기
          4. gingerbread/config 토픽에 MQTT 발행 (QoS=1, retain=True)

        매개변수
        ────────
        updates : 변경할 파라미터 딕셔너리.
          지원 형식 1 (플랫 구조):
            {
              "RSSI_THRESHOLD": -75,
              "PACKET_LOSS_LIMIT": 3,
              "GAS_THRESHOLD_KOHM": 15,
              "TEMP_THRESHOLD_CELSIUS": 50,
              "POWER_MODE": "BATTERY",
              "CURRENT_BATTERY_LEVEL": 80
            }
          지원 형식 2 (섹션 중첩 구조):
            {
              "NETWORK": { "RSSI_THRESHOLD": -75 },
              "ENVIRONMENT": { "GAS_THRESHOLD_KOHM": 15 }
            }

        반환값
        ──────
        갱신 후 전체 설정 딕셔너리 (메모리 캐시의 깊은 복사본).

        예외
        ────
        ValueError : 입력값이 검증 규칙에 위배되는 경우 (타입/범위 오류).
        """
        # ── 1단계: 입력값 검증 ────────────────────────────────────────────────
        # 플랫 형식으로 전달된 경우 섹션 중첩 구조로 변환합니다.
        normalized = _normalize_updates(updates)
        _validate_config(normalized)

        with self._lock:
            # ── 2단계: 메모리 캐시 갱신 ──────────────────────────────────────
            _deep_merge(self._config, normalized)

            # ── 3단계: config.json 파일에 즉시 저장 ──────────────────────────
            self._write_config_to_disk(self._config)

            # 발행할 설정의 스냅샷을 락 내부에서 추출 (복사본)
            config_snapshot = _deep_copy_dict(self._config)

        # ── 4단계: MQTT 발행 (락 외부 — IO 작업이므로 락 범위 최소화) ───────
        self._publish_mqtt(config_snapshot)

        logger.info(
            "[설정서비스] 설정 갱신 완료. 변경 내용: %s",
            json.dumps(normalized, ensure_ascii=False),
        )
        return config_snapshot

    # ──────────────────────────────────────────────────────────────────────────
    # MQTT 내부 메서드
    # ──────────────────────────────────────────────────────────────────────────

    def _init_mqtt(self) -> None:
        """
        paho-mqtt 클라이언트를 초기화하고 브로커에 비동기 연결을 시도합니다.

        - paho-mqtt가 설치되지 않은 경우 경고 로그만 출력하고 종료합니다.
        - 브로커 연결 실패 시 오류를 로깅하지만 서비스는 계속 동작합니다.
        - loop_start()로 백그라운드 네트워크 스레드를 자동 관리합니다.
        """
        if not _MQTT_AVAILABLE:
            logger.warning(
                "[설정서비스] paho-mqtt가 설치되지 않아 MQTT 동기화가 비활성화됩니다. "
                "설치 명령어: pip install paho-mqtt"
            )
            return

        try:
            # MQTT 클라이언트 ID — 게이트웨이 서버 식별자
            client_id = f"gingerbread-gateway-config-{int(time.time())}"
            self._mqtt_client = mqtt.Client(
                client_id=client_id,
                clean_session=True,
            )

            # 연결/단절 이벤트 콜백 등록
            self._mqtt_client.on_connect    = self._on_mqtt_connect
            self._mqtt_client.on_disconnect = self._on_mqtt_disconnect
            self._mqtt_client.on_publish    = self._on_mqtt_publish

            # 자동 재연결 설정 (paho-mqtt v1.x 호환)
            self._mqtt_client.reconnect_delay_set(min_delay=1, max_delay=60)

            # 비동기 연결 시도 — 블로킹 없이 즉시 반환
            self._mqtt_client.connect_async(
                host=self._mqtt_host,
                port=self._mqtt_port,
                keepalive=60,
            )

            # 백그라운드 네트워크 루프 시작 (별도 스레드에서 자동 관리)
            self._mqtt_client.loop_start()

            logger.info(
                "[설정서비스] MQTT 클라이언트 초기화됨. 브로커 연결 시도 중: %s:%d",
                self._mqtt_host, self._mqtt_port,
            )

        except Exception as exc:
            # 예상치 못한 초기화 오류 — MQTT 없이 계속 동작
            logger.error(
                "[설정서비스] MQTT 클라이언트 초기화 실패: %s — "
                "MQTT 동기화 기능이 비활성화됩니다.",
                exc,
            )
            self._mqtt_client = None

    def _publish_mqtt(self, config: Dict[str, Any]) -> None:
        """
        현재 설정 전체를 MQTT 토픽 'gingerbread/config'에 발행합니다.

        발행 규격:
          - 토픽  : gingerbread/config
          - QoS   : 1 (최소 1회 전달 보장 — PUBACK 핸드셰이크)
          - Retain: True (브로커가 마지막 메시지를 보존하여 ESP32가
                         재연결 시 즉시 최신 설정을 수신할 수 있게 함)
          - 페이로드: JSON 직렬화된 전체 설정 딕셔너리 (UTF-8 인코딩)

        MQTT 클라이언트가 없거나 브로커가 단절된 경우 오류를 로깅하고
        조용히 종료합니다 — 서비스가 중단되지 않습니다.
        """
        if self._mqtt_client is None:
            logger.warning(
                "[설정서비스] MQTT 클라이언트가 없어 발행을 건너뜁니다. "
                "paho-mqtt 설치 여부 및 브로커 설정을 확인하세요."
            )
            return

        try:
            # 설정 딕셔너리를 JSON 문자열로 직렬화 (한글 유지, 가독성 위해 들여쓰기)
            payload_str = json.dumps(config, ensure_ascii=False, indent=2)
            payload_bytes = payload_str.encode("utf-8")

            # MQTT 발행 (QoS=1, retain=True)
            msg_info = self._mqtt_client.publish(
                topic=_MQTT_CONFIG_TOPIC,
                payload=payload_bytes,
                qos=1,       # QoS 1: 최소 1회 전달 보장 (PUBACK 핸드셰이크)
                retain=True, # Retain=True: ESP32 재연결 시 즉시 최신값 수신
            )

            # 발행 결과 코드 확인 (mqtt.MQTT_ERR_SUCCESS = 0)
            if msg_info.rc == mqtt.MQTT_ERR_SUCCESS:
                logger.info(
                    "[설정서비스] MQTT 발행 성공 — 토픽: '%s' | mid=%d | "
                    "페이로드: %d bytes",
                    _MQTT_CONFIG_TOPIC, msg_info.mid, len(payload_bytes),
                )
            else:
                logger.error(
                    "[설정서비스] MQTT 발행 실패 — 토픽: '%s' | 오류코드: %d",
                    _MQTT_CONFIG_TOPIC, msg_info.rc,
                )

        except Exception as exc:
            # 브로커 연결 끊김, 네트워크 오류 등 모든 예외를 포착
            logger.error(
                "[설정서비스] MQTT 발행 중 예외 발생: %s — "
                "config.json은 이미 저장되었으며 다음 연결 시 자동 재시도됩니다.",
                exc,
            )

    def _on_mqtt_connect(
        self,
        client: "mqtt.Client",
        userdata: Any,
        flags: dict,
        rc: int,
    ) -> None:
        """
        MQTT 브로커 연결 성공/실패 이벤트 콜백.

        연결 성공(rc=0) 시 현재 메모리의 설정을 즉시 발행합니다.
        이를 통해 브로커가 재시작되거나 Retain 메시지가 소실된 경우에도
        게이트웨이가 재연결 시 설정을 자동으로 복구합니다.
        """
        if rc == 0:
            logger.info(
                "[설정서비스] MQTT 브로커 연결 성공: %s:%d",
                self._mqtt_host, self._mqtt_port,
            )
            # 연결 즉시 현재 설정을 retain 발행하여 브로커 상태를 동기화합니다.
            current_config = self.get_config()
            self._publish_mqtt(current_config)
        else:
            # paho-mqtt 연결 실패 코드 의미:
            # 1=잘못된 프로토콜, 2=클라이언트ID 거부, 3=서버 불가용,
            # 4=자격증명 오류, 5=권한 없음
            logger.error(
                "[설정서비스] MQTT 브로커 연결 실패 — 오류코드: %d "
                "(1=프로토콜오류, 2=ID거부, 3=서버불가, 4=인증오류, 5=권한없음)",
                rc,
            )

    def _on_mqtt_disconnect(
        self,
        client: "mqtt.Client",
        userdata: Any,
        rc: int,
    ) -> None:
        """
        MQTT 브로커 연결 해제 이벤트 콜백.

        rc=0: 정상 disconnect()호출에 의한 종료.
        rc≠0: 예상치 못한 네트워크 단절 — paho-mqtt가 자동 재연결을 시도합니다.
        """
        if rc == 0:
            logger.info("[설정서비스] MQTT 브로커 연결 정상 종료됨.")
        else:
            logger.warning(
                "[설정서비스] MQTT 브로커 연결 예기치 않게 단절됨 (rc=%d). "
                "paho-mqtt 자동 재연결 대기 중...",
                rc,
            )

    def _on_mqtt_publish(
        self,
        client: "mqtt.Client",
        userdata: Any,
        mid: int,
    ) -> None:
        """
        MQTT 메시지 발행 완료(PUBACK 수신) 이벤트 콜백.

        QoS 1 발행의 경우 브로커로부터 PUBACK를 수신했을 때 호출됩니다.
        """
        logger.debug(
            "[설정서비스] MQTT PUBACK 수신 완료 — mid=%d (전달 확인됨)", mid
        )

    # ──────────────────────────────────────────────────────────────────────────
    # 내부 파일 I/O 메서드
    # ──────────────────────────────────────────────────────────────────────────

    def _write_config_to_disk(self, config: Dict[str, Any]) -> None:
        """
        현재 설정 딕셔너리를 config.json 파일에 원자적으로 저장합니다.

        임시 파일 → 최종 파일 교체 방식(atomic write)을 사용하여
        파일 쓰기 도중 프로세스가 종료되어도 config.json이 손상되지 않습니다.

        주의: 이 메서드는 반드시 self._lock 보유 상태에서 호출해야 합니다.
        """
        # 임시 파일에 먼저 기록 (쓰기 도중 크래시 발생해도 원본 파일 보존)
        tmp_path = self._config_path + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())  # OS 버퍼를 디스크에 강제 플러시

            # 임시 파일을 최종 파일로 원자적 교체
            os.replace(tmp_path, self._config_path)
            logger.debug(
                "[설정서비스] config.json 저장 완료: %s", self._config_path
            )

        except OSError as exc:
            logger.error(
                "[설정서비스] config.json 저장 실패: %s", exc
            )
            # 임시 파일이 남아있으면 정리
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            raise  # 호출자에게 오류 전파

    def shutdown(self) -> None:
        """
        서비스를 안전하게 종료합니다.
        MQTT 클라이언트의 네트워크 루프를 중단하고 브로커 연결을 끊습니다.
        main.py의 종료 핸들러에서 호출해야 합니다.
        """
        if self._mqtt_client is not None:
            logger.info("[설정서비스] MQTT 클라이언트 종료 중...")
            try:
                self._mqtt_client.loop_stop()   # 백그라운드 네트워크 스레드 중단
                self._mqtt_client.disconnect()  # 브로커에 정상 종료 알림
            except Exception as exc:
                logger.warning("[설정서비스] MQTT 종료 중 오류 (무시됨): %s", exc)


# ──────────────────────────────────────────────────────────────────────────────
# 모듈 레벨 유틸리티 함수 (순수 함수 — 클래스 상태 없음)
# ──────────────────────────────────────────────────────────────────────────────

def _deep_copy_dict(d: Dict[str, Any]) -> Dict[str, Any]:
    """
    딕셔너리를 깊은 복사(deep copy)합니다.
    JSON 직렬화/역직렬화를 활용하여 중첩 딕셔너리도 완전히 복사합니다.
    """
    return json.loads(json.dumps(d))


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> None:
    """
    override 딕셔너리의 값을 base 딕셔너리에 재귀적으로 병합합니다.
    (in-place 수정 — 반환값 없음)

    섹션 단위 딕셔너리 값은 재귀 병합하고,
    스칼라 값(숫자, 문자열 등)은 override 값으로 덮어씁니다.
    """
    for key, value in override.items():
        if (
            key in base
            and isinstance(base[key], dict)
            and isinstance(value, dict)
        ):
            # 양쪽 모두 딕셔너리인 경우 → 재귀 병합 (섹션 수준 병합)
            _deep_merge(base[key], value)
        else:
            # 스칼라 값 또는 새 키 → 직접 덮어쓰기
            base[key] = value


def _normalize_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    웹 대시보드에서 전달된 파라미터를 섹션 중첩 구조로 정규화합니다.

    플랫(flat) 형식과 섹션 중첩 형식 모두 지원합니다:

    플랫 형식 입력:
      { "RSSI_THRESHOLD": -75, "GAS_THRESHOLD_KOHM": 15 }

    정규화 출력:
      {
        "NETWORK": { "RSSI_THRESHOLD": -75 },
        "ENVIRONMENT": { "GAS_THRESHOLD_KOHM": 15 }
      }

    이미 섹션 중첩 형식이면 그대로 반환합니다.
    """
    # 5개 파라미터와 해당 섹션의 매핑 테이블
    _FLAT_KEY_SECTION_MAP: Dict[str, str] = {
        "RSSI_THRESHOLD":        "NETWORK",
        "PACKET_LOSS_LIMIT":     "NETWORK",
        "GAS_THRESHOLD_KOHM":    "ENVIRONMENT",
        "TEMP_THRESHOLD_CELSIUS":"ENVIRONMENT",
        "POWER_MODE":            "POWER_MANAGEMENT",
        "CURRENT_BATTERY_LEVEL": "POWER_MANAGEMENT",
    }

    # 입력이 이미 섹션 중첩 구조인지 확인 (최상위 키가 섹션명인 경우)
    _TOP_LEVEL_SECTIONS = {"NETWORK", "ENVIRONMENT", "POWER_MANAGEMENT"}
    if any(k in _TOP_LEVEL_SECTIONS for k in updates.keys()):
        # 이미 섹션 중첩 구조 — 그대로 반환
        return updates

    # 플랫 구조를 섹션 중첩 구조로 변환
    normalized: Dict[str, Any] = {}
    for param_key, value in updates.items():
        section = _FLAT_KEY_SECTION_MAP.get(param_key)
        if section is None:
            # 알 수 없는 파라미터 키는 경고 후 무시
            logger.warning(
                "[설정서비스] 알 수 없는 파라미터 키 '%s' — 무시됩니다.", param_key
            )
            continue
        if section not in normalized:
            normalized[section] = {}
        normalized[section][param_key] = value

    return normalized


def _validate_config(config: Dict[str, Any]) -> None:
    """
    업데이트할 설정값의 유효성을 검증합니다.

    검증 규칙:
      NETWORK.RSSI_THRESHOLD      : int 또는 float, -120 ≤ x ≤ 0 (dBm 범위)
      NETWORK.PACKET_LOSS_LIMIT   : int 또는 float, 0 ≤ x ≤ 100 (%)
      ENVIRONMENT.GAS_THRESHOLD_KOHM    : int 또는 float, 0 < x (kΩ, 양수여야 함)
      ENVIRONMENT.TEMP_THRESHOLD_CELSIUS: int 또는 float, -40 ≤ x ≤ 125 (°C, 센서 범위)
      POWER_MANAGEMENT.POWER_MODE         : str, "EXTERNAL_5V" 또는 "BATTERY"
      POWER_MANAGEMENT.CURRENT_BATTERY_LEVEL: int, 0 ≤ x ≤ 100

    예외
    ────
    ValueError : 검증 실패 시 구체적인 오류 메시지와 함께 발생.
    """
    errors: list = []

    # ── NETWORK 섹션 검증 ────────────────────────────────────────────────────
    net = config.get("NETWORK", {})

    if "RSSI_THRESHOLD" in net:
        v = net["RSSI_THRESHOLD"]
        if not isinstance(v, (int, float)):
            errors.append(f"RSSI_THRESHOLD는 숫자여야 합니다 (수신값: {v!r})")
        elif not (-120 <= v <= 0):
            errors.append(f"RSSI_THRESHOLD는 -120 ~ 0 dBm 범위여야 합니다 (수신값: {v})")

    if "PACKET_LOSS_LIMIT" in net:
        v = net["PACKET_LOSS_LIMIT"]
        if not isinstance(v, (int, float)):
            errors.append(f"PACKET_LOSS_LIMIT는 숫자여야 합니다 (수신값: {v!r})")
        elif not (0 <= v <= 100):
            errors.append(f"PACKET_LOSS_LIMIT는 0 ~ 100 % 범위여야 합니다 (수신값: {v})")

    # ── ENVIRONMENT 섹션 검증 ────────────────────────────────────────────────
    env = config.get("ENVIRONMENT", {})

    if "GAS_THRESHOLD_KOHM" in env:
        v = env["GAS_THRESHOLD_KOHM"]
        if not isinstance(v, (int, float)):
            errors.append(f"GAS_THRESHOLD_KOHM는 숫자여야 합니다 (수신값: {v!r})")
        elif v <= 0:
            errors.append(f"GAS_THRESHOLD_KOHM는 0보다 큰 양수여야 합니다 (수신값: {v})")

    if "TEMP_THRESHOLD_CELSIUS" in env:
        v = env["TEMP_THRESHOLD_CELSIUS"]
        if not isinstance(v, (int, float)):
            errors.append(f"TEMP_THRESHOLD_CELSIUS는 숫자여야 합니다 (수신값: {v!r})")
        elif not (-40 <= v <= 125):
            errors.append(f"TEMP_THRESHOLD_CELSIUS는 -40 ~ 125 °C 범위여야 합니다 (수신값: {v})")

    # ── POWER_MANAGEMENT 섹션 검증 ───────────────────────────────────────────
    pwr = config.get("POWER_MANAGEMENT", {})

    if "POWER_MODE" in pwr:
        v = pwr["POWER_MODE"]
        _valid_modes = {"EXTERNAL_5V", "BATTERY"}
        if not isinstance(v, str) or v not in _valid_modes:
            errors.append(
                f"POWER_MODE는 'EXTERNAL_5V' 또는 'BATTERY'여야 합니다 (수신값: {v!r})"
            )

    if "CURRENT_BATTERY_LEVEL" in pwr:
        v = pwr["CURRENT_BATTERY_LEVEL"]
        if not isinstance(v, int):
            errors.append(
                f"CURRENT_BATTERY_LEVEL는 정수여야 합니다 (수신값: {v!r}, 타입: {type(v).__name__})"
            )
        elif not (0 <= v <= 100):
            errors.append(f"CURRENT_BATTERY_LEVEL는 0 ~ 100 범위여야 합니다 (수신값: {v})")

    # 검증 오류가 하나라도 있으면 ValueError 발생
    if errors:
        error_summary = "; ".join(errors)
        raise ValueError(f"[설정서비스] 유효성 검증 실패: {error_summary}")
