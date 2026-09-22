"""
ml_model/collect.py
─────────────────────────────────────────────────────────────────────────────
위험 점수 MLP 학습용 데이터 수집 도구.

firmware/src/main_collect.cpp (env: board1_collect)가 시리얼로 내보내는
    #D,<millis>,<온도>,<습도>,<가스저항 kΩ>,<기압 hPa>
줄을 읽어, 사용자가 지정한 라벨과 함께 CSV로 저장합니다.

  python ml_model/collect.py --list-ports            # 사용 가능한 시리얼 포트 확인
  python ml_model/collect.py --port COM5             # 수집 시작

수집 중 키 (창을 클릭한 상태에서 누르세요):
  0 = 정상   1 = 경고   2 = 위험   p = 일시정지(저장 안 함)   q = 종료

  ※ 시작 직후처럼 라벨이 없는 동안에도 값은 label = -1 로 저장합니다. -1 은 "라벨 없음"이며 학습에는
    쓰이지 않습니다 (잘못된 라벨이 섞이지 않음). 저장하는 이유는 가스 저항의 "기준값 대비 비율"이 시계열의
    연속성에 의존하기 때문입니다. 샘플이 빠지면 학습할 때 계산한 비율이 실기에서 계산될 값과 달라집니다.
    라벨 없이 두어도 되는 구간: 예열, 준비, 쉬는 시간.

--auto-bands LOW HIGH 를 주면 측정 온도로 라벨을 자동 지정합니다.
  온도 < LOW → 0(정상),  LOW ≤ 온도 < HIGH → 1(경고),  온도 ≥ HIGH → 2(위험)
  주의: 라벨이 모델 입력(온도)의 함수가 되므로, 학습된 모델은 "온도 구간 규칙"을 그대로 재현합니다.
  이 경우 논문에는 "라벨을 온도 구간으로 정의했다"고 명시해야 합니다 (ml_model/README.md 참조).

저장 형식 (CSV, 이어쓰기):  timestamp,session,ms,temp,hum,gas_kohm,pres_hpa,label
  session = 이 도구를 실행한 시각. 학습 시 같은 세션의 연속 샘플이 학습/검증에 섞이지 않도록
  train.py가 세션·라벨별로 시간순 분할하는 데 쓰입니다.
─────────────────────────────────────────────────────────────────────────────
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import queue
import sys
import threading
import time
from datetime import datetime
from typing import Callable, Iterable, Optional, Tuple

CSV_FIELDS = ["timestamp", "session", "ms", "temp", "hum", "gas_kohm", "pres_hpa", "label"]
LABEL_NAMES = {0: "정상", 1: "경고", 2: "위험"}
UNLABELED = -1   # 라벨 없음: 학습에는 쓰지 않지만 시계열 연속성(가스 기준값 계산)을 위해 저장

# BME680 동작 범위. 벗어난 값은 센서 오류나 시리얼 전송 오류로 보고 버립니다.
TEMP_RANGE = (-40.0, 85.0)
HUM_RANGE = (0.0, 100.0)
PRES_RANGE = (300.0, 1100.0)   # BME680 기압 측정 범위 (hPa)

Sample = Tuple[int, float, float, float, float]  # (ms, temp, hum, gas_kohm, pres_hpa)


def parse_line(line: str) -> Optional[Sample]:
    """'#D,ms,temp,hum,gas,pres' 형식의 줄을 파싱합니다. 형식이 다르거나 범위를 벗어나면 None."""
    line = line.strip()
    if not line.startswith("#D,"):
        return None
    parts = line[3:].split(",")
    if len(parts) != 5:      # 4개 값(구버전 수집 펌웨어)은 기압이 없으므로 받지 않습니다
        return None
    try:
        ms = int(parts[0])
        temp, hum, gas, pres = (float(p) for p in parts[1:])
    except ValueError:
        return None
    if not all(math.isfinite(v) for v in (temp, hum, gas, pres)):
        return None
    if not (TEMP_RANGE[0] <= temp <= TEMP_RANGE[1]):
        return None
    if not (HUM_RANGE[0] <= hum <= HUM_RANGE[1]):
        return None
    if gas <= 0.0:
        return None
    if not (PRES_RANGE[0] <= pres <= PRES_RANGE[1]):
        return None
    return ms, temp, hum, gas, pres


class Labeler:
    """
    현재 라벨을 관리합니다. 키 입력(0/1/2/p/q)으로 바꾸거나, bands가 있으면 온도로 자동 지정합니다.
    poll()은 대기 중인 키 입력을 처리하며 논블로킹입니다.
    """

    def __init__(self, bands: Optional[Tuple[float, float]] = None,
                 key_source: Optional[Callable[[], Optional[str]]] = None) -> None:
        self.manual_label: Optional[int] = None   # None = 라벨 없음(일시정지) → 저장 안 함
        self.bands = bands
        self.quit = False
        self._key_source = key_source or _make_key_source()

    def poll(self) -> None:
        while True:
            key = self._key_source()
            if key is None:
                return
            key = key.lower()
            if key in ("0", "1", "2"):
                self.manual_label = int(key)
            elif key == "p":
                self.manual_label = None
            elif key == "q":
                self.quit = True

    def label_for(self, temp: float) -> Optional[int]:
        if self.bands is not None:
            if self.manual_label is None:      # 자동 라벨 모드에서도 p로 일시정지 가능
                return None
            low, high = self.bands
            return 0 if temp < low else (1 if temp < high else 2)
        return self.manual_label


def _make_key_source() -> Callable[[], Optional[str]]:
    """플랫폼별 논블로킹 키 입력. Windows는 msvcrt, 그 외는 stdin 줄 입력 스레드."""
    try:
        import msvcrt  # type: ignore

        def win_key() -> Optional[str]:
            return msvcrt.getwch() if msvcrt.kbhit() else None
        return win_key
    except ImportError:
        q: "queue.Queue[str]" = queue.Queue()

        def reader() -> None:
            for line in sys.stdin:
                for ch in line.strip():
                    q.put(ch)
        threading.Thread(target=reader, daemon=True).start()

        def other_key() -> Optional[str]:
            try:
                return q.get_nowait()
            except queue.Empty:
                return None
        return other_key


class CsvSink:
    """수집 결과를 CSV에 이어씁니다. 파일이 비어 있으면 헤더를 씁니다. 행마다 flush합니다."""

    def __init__(self, path: str, session: str) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        need_header = (not os.path.exists(path)) or os.path.getsize(path) == 0
        self._file = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.writer(self._file)
        self._session = session
        self.count = 0
        if need_header:
            self._writer.writerow(CSV_FIELDS)
            self._file.flush()

    def write(self, sample: Sample, label: int) -> None:
        ms, temp, hum, gas, pres = sample
        self._writer.writerow([
            datetime.now().isoformat(timespec="seconds"), self._session,
            ms, f"{temp:.2f}", f"{hum:.2f}", f"{gas:.2f}", f"{pres:.2f}", label,
        ])
        self._file.flush()   # 도중에 끊겨도 지금까지의 데이터가 남도록
        self.count += 1

    def close(self) -> None:
        self._file.close()


def collect_loop(lines: Iterable[str], sink: CsvSink, labeler: Labeler,
                 max_samples: Optional[int] = None,
                 on_sample: Optional[Callable[[Sample, Optional[int]], None]] = None) -> dict:
    """
    줄 스트림을 읽어 모든 유효한 샘플을 저장합니다. 라벨이 없으면 label = -1 로 저장합니다.
    반환: {"saved": 라벨 있는 저장 수, "unlabeled": 라벨 없이(-1) 저장한 수, "invalid": 형식/범위 오류 수}
    max_samples 는 "라벨 있는" 샘플 수 기준입니다.
    """
    stats = {"saved": 0, "unlabeled": 0, "invalid": 0}
    for line in lines:
        labeler.poll()
        if labeler.quit:
            break
        if not line.strip() or not line.startswith("#D,"):
            continue                       # 펌웨어의 안내 메시지("# collect: ...")는 무시
        sample = parse_line(line)
        if sample is None:
            stats["invalid"] += 1
            continue
        label = labeler.label_for(sample[1])
        if on_sample:
            on_sample(sample, label)
        if label is None:
            sink.write(sample, UNLABELED)      # 학습엔 안 쓰이지만 시계열은 이어져야 함
            stats["unlabeled"] += 1
            continue
        sink.write(sample, label)
        stats["saved"] += 1
        if max_samples is not None and stats["saved"] >= max_samples:
            break
    return stats


def _serial_lines(ser) -> Iterable[str]:
    """시리얼에서 줄을 계속 읽습니다. 타임아웃(빈 줄)도 그대로 넘겨 키 입력을 계속 처리하게 합니다."""
    while True:
        raw = ser.readline()
        yield raw.decode("utf-8", errors="ignore") if raw else ""


def main() -> int:
    ap = argparse.ArgumentParser(description="BME680 학습 데이터 수집 (라벨 포함 CSV 저장)")
    ap.add_argument("--port", help="시리얼 포트 (예: COM5, /dev/ttyACM0, loop://)")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--out", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw_dataset.csv"))
    ap.add_argument("--auto-bands", nargs=2, type=float, metavar=("LOW", "HIGH"),
                    help="측정 온도로 라벨 자동 지정 (정상 < LOW ≤ 경고 < HIGH ≤ 위험)")
    ap.add_argument("--max-samples", type=int, default=None, help="이 개수를 저장하면 종료")
    ap.add_argument("--list-ports", action="store_true", help="시리얼 포트 목록을 보고 종료")
    args = ap.parse_args()

    try:
        import serial
        from serial.tools import list_ports
    except ImportError:
        print("pyserial이 필요합니다:  pip install pyserial")
        return 1

    if args.list_ports:
        ports = list(list_ports.comports())
        if not ports:
            print("시리얼 포트를 찾지 못했습니다. 보드의 USB 연결과 드라이버를 확인하세요.")
        for p in ports:
            print(f"{p.device}\t{p.description}")
        return 0
    if not args.port:
        ap.error("--port 가 필요합니다 (포트 목록: --list-ports)")
    if args.auto_bands and not args.auto_bands[0] < args.auto_bands[1]:
        ap.error("--auto-bands 는 LOW < HIGH 여야 합니다")

    session = datetime.now().strftime("%Y%m%d-%H%M%S")
    sink = CsvSink(args.out, session)
    labeler = Labeler(bands=tuple(args.auto_bands) if args.auto_bands else None)
    if labeler.bands:
        labeler.manual_label = 0          # 자동 모드는 시작 즉시 저장 (p 로 일시정지)

    print(f"저장 파일: {args.out}\n세션: {session}")
    print("키: 0=정상  1=경고  2=위험  p=일시정지  q=종료" +
          (f"   (자동 라벨: <{args.auto_bands[0]:g}=정상, <{args.auto_bands[1]:g}=경고, 그 이상=위험)"
           if args.auto_bands else "   ※ 0/1/2 를 누르기 전에는 label=-1(학습 제외)로 저장합니다"))

    def show(sample: Sample, label: Optional[int]) -> None:
        _, t, h, g, pr = sample
        tag = "라벨 없음(-1, 학습 제외)" if label is None else f"라벨 {label}={LABEL_NAMES[label]}"
        print(f"\r온도 {t:6.2f}°C 습도 {h:5.1f}% 가스 {g:8.2f}kΩ 기압 {pr:7.2f}hPa | {tag} | 저장 {sink.count}개   ",
              end="", flush=True)

    ser = serial.serial_for_url(args.port, baudrate=args.baud, timeout=1)
    try:
        stats = collect_loop(_serial_lines(ser), sink, labeler, args.max_samples, on_sample=show)
    except KeyboardInterrupt:
        stats = {"saved": sink.count, "unlabeled": "-", "invalid": "-"}
    finally:
        ser.close()
        sink.close()
    print(f"\n종료. 라벨 있는 저장 {stats['saved']}개 | 라벨 없이(-1) 저장 {stats['unlabeled']} | 형식/범위 오류 {stats['invalid']}")
    if stats["saved"] == 0 and stats["unlabeled"] in (0, "-") and isinstance(stats["invalid"], int) and stats["invalid"] > 0:
        print("힌트: 형식 오류만 있고 저장이 0개입니다. 수집 펌웨어가 구버전(기압 없는 4값 형식)일 수 있으니\n"
              "      pio run -e board1_collect -t upload 로 다시 올리세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
