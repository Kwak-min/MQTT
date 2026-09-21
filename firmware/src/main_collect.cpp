/*
 * firmware/src/main_collect.cpp
 * ═══════════════════════════════════════════════════════════════════════════
 * 위험 점수 MLP 학습용 데이터 수집 펌웨어 (네트워크 없음, BME680만 사용)
 *
 * BME680 값을 시리얼로 한 줄씩 출력합니다. PC의 ml_model/collect.py가 이 줄을 읽어
 * 사용자가 키보드로 지정한 라벨(0 정상 / 1 경고 / 2 위험)과 함께 CSV로 저장합니다.
 *
 * 출력 형식 (한 줄):   #D,<millis>,<온도 °C>,<습도 %>,<가스저항 kΩ>,<기압 hPa>
 *   BME680이 측정하는 4가지 값을 모두 내보냅니다. 학습에 무엇을 쓸지는 ml_model/train.py의
 *   --features 로 고릅니다 (전부 기록해 두면 나중에 특징 조합을 바꿔 비교할 수 있습니다).
 *
 * [중요] 학습 데이터의 분포를 실제 동작 환경과 일치시키기 위해 다음을 main_gingerbread.cpp와
 *        똑같이 맞췄습니다. 하나라도 다르면 학습한 모델이 실기에서 다른 값을 보게 됩니다.
 *   - I2C 핀 (SDA=8, SCL=9), 주소 0x76/0x77 자동 탐색
 *   - BME680 오버샘플링/필터/가스 히터 설정 (온도 8x, 습도 2x, 압력 4x, IIR 3, 히터 320°C/150ms)
 *   - 측정 주기 5초: BME680 가스 히터는 자체 발열로 온도를 몇 °C 높게 읽게 하며, 그 정도는
 *     측정 주기에 따라 달라집니다. 실제 동작 주기(5초)와 같아야 같은 오프셋이 학습됩니다.
 *     (샘플을 더 빨리 모으려고 주기를 줄이면 오프셋이 달라지므로 권장하지 않습니다.)
 * ═══════════════════════════════════════════════════════════════════════════
 */

#include <Arduino.h>
#include <Wire.h>
#include <Adafruit_BME680.h>

#define BME_SDA_PIN 8
#define BME_SCL_PIN 9

// 측정 주기(ms). 실제 동작 주기와 같아야 합니다 (위 설명 참조).
#ifndef COLLECT_PERIOD_MS
#define COLLECT_PERIOD_MS 5000UL
#endif

static Adafruit_BME680 bme680;

void setup() {
  Serial.begin(115200);
  delay(100);
  Serial.println("\n# collect: 데이터 수집 펌웨어 시작 (BME680 전용)");

  Wire.begin(BME_SDA_PIN, BME_SCL_PIN);
  if (!bme680.begin(0x76)) {
    if (!bme680.begin(0x77)) {
      Serial.println("# collect: BME680을 찾을 수 없습니다 (주소 0x76/0x77, 배선 확인)");
      while (true) { delay(1000); }
    }
  }

  // main_gingerbread.cpp와 동일한 설정
  bme680.setTemperatureOversampling(BME680_OS_8X);
  bme680.setHumidityOversampling(BME680_OS_2X);
  bme680.setPressureOversampling(BME680_OS_4X);
  bme680.setIIRFilterSize(BME680_FILTER_SIZE_3);
  bme680.setGasHeater(320, 150);

  Serial.printf("# collect: 준비 완료 (주기 %lu ms)\n", (unsigned long)COLLECT_PERIOD_MS);
}

void loop() {
  const unsigned long t0 = millis();

  if (bme680.performReading()) {
    // gas_resistance는 Ω 단위 → kΩ (main_gingerbread.cpp의 read_gas_resistance_kohm()과 동일)
    // 기압: Adafruit_BME680은 Pa 단위 → hPa (main_gingerbread.cpp의 read_pressure_hpa()와 동일)
    Serial.printf("#D,%lu,%.2f,%.2f,%.2f,%.2f\n", t0,
                  bme680.temperature, bme680.humidity, bme680.gas_resistance / 1000.0f,
                  bme680.pressure / 100.0f);
  } else {
    Serial.println("# collect: 측정 실패 — 이번 샘플을 건너뜁니다");
  }

  // 측정에 걸린 시간을 빼서 주기를 일정하게 유지합니다.
  const unsigned long elapsed = millis() - t0;
  if (elapsed < COLLECT_PERIOD_MS) {
    delay(COLLECT_PERIOD_MS - elapsed);
  }
}
