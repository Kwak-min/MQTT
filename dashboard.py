import streamlit as st
import pandas as pd
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def get_csv_path(filename: str) -> str:
    """
    CSV 파일의 실제 경로를 유연하게 탐색하여 반환합니다.
    (backend/logs/, logs/, 루트 경로 순으로 확인)
    """
    candidates = [
        os.path.join(BASE_DIR, "backend", "logs", filename),
        os.path.join(BASE_DIR, "logs", filename),
        os.path.join("backend", "logs", filename),
        os.path.join("logs", filename),
        filename,
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return os.path.join(BASE_DIR, "backend", "logs", filename)

# 1. 페이지 기본 설정 (가로 넓게)
st.set_page_config(
    page_title="IoT 성능 비교 대시보드",
    page_icon="⚡",
    layout="wide"
)

st.title("⚡ Gingerbread vs Standard MQTT 성능 비교 대시보드")
st.caption("🟢 백엔드 수신 데이터가 1초 간격으로 대시보드에 실시간 반영됩니다. (페이지 전체 새로고침 없음)")

# 사이드바 컨트롤
with st.sidebar:
    st.header("⚙️ 컨트롤 판넬")
    if st.button("🔄 지금 새로고침"):
        st.rerun()
    telemetry_info_path = get_csv_path("telemetry.csv")
    power_info_path = get_csv_path("power.csv")
    st.info(f"로그 파일 경로:\n- `{telemetry_info_path}`\n- `{power_info_path}`")

# 2. 실시간 프래그먼트 컴포넌트 (페이지 깜빡임 없이 1초 주기로 데이터 부분 업데이트)
@st.fragment(run_every="1s")
def render_live_dashboard():
    col1, col2 = st.columns(2)

    # ==========================================
    # 왼쪽 단: 환경 센서 데이터 (온도/습도)
    # ==========================================
    with col1:
        st.subheader("🌡️ 환경 센서 데이터 (온도/습도)")
        
        telemetry_path = get_csv_path("telemetry.csv")

        try:
            if os.path.exists(telemetry_path):
                df_env = pd.read_csv(telemetry_path)
                
                if df_env.empty:
                    st.info("telemetry.csv 파일에 아직 수신된 데이터가 없습니다.")
                else:
                    # 최근 50개 데이터만 추출
                    df_env_recent = df_env.tail(50).copy()
                    
                    # 컬럼 매핑 (temp -> 온도 (°C), hum -> 습도 (%))
                    chart_cols = {}
                    if 'temp' in df_env_recent.columns:
                        chart_cols['temp'] = '온도 (°C)'
                    elif 'temperature' in df_env_recent.columns:
                        chart_cols['temperature'] = '온도 (°C)'
                        
                    if 'hum' in df_env_recent.columns:
                        chart_cols['hum'] = '습도 (%)'
                    elif 'humidity' in df_env_recent.columns:
                        chart_cols['humidity'] = '습도 (%)'

                    if chart_cols:
                        df_chart = df_env_recent[list(chart_cols.keys())].rename(columns=chart_cols)
                        st.line_chart(df_chart)
                    else:
                        st.warning("온도/습도 컬럼을 찾을 수 없습니다.")

                    # 원본 데이터 표 (최근 5개)
                    st.caption("최근 수신된 환경 데이터 원본 (최신 5개)")
                    display_cols = [c for c in ['timestamp', 'client_id', 'temp', 'hum', 'gas'] if c in df_env_recent.columns]
                    if not display_cols:
                        display_cols = df_env_recent.columns.tolist()
                    st.dataframe(df_env_recent[display_cols].tail(5), use_container_width=True)
            else:
                st.warning("아직 telemetry.csv 파일이 없습니다. 센서 데이터 수신을 기다리는 중입니다...")
        except Exception as e:
            st.error(f"환경 데이터를 불러오는 중 오류가 발생했습니다: {e}")

    # ==========================================
    # 오른쪽 단: 전력 소모량 비교 및 절감률
    # ==========================================
    with col2:
        st.subheader("🔋 프로토콜별 소비 전력 비교")

        power_path = get_csv_path("power.csv")
        power_ext_path = get_csv_path("power_ext.csv")

        # power_ext.csv에 있는 average_current_ma를 쓸 수 있으면 그걸 우선합니다.
        # 이유: power.csv의 "사이클당 에너지(mWh)"는 두 노드의 사이클 길이가 다르면
        # (예: Gingerbread 30초 vs Standard 60초) 비교가 왜곡됩니다 — 사이클이 짧을수록
        # 더 자주 깨어나 전송하므로, "사이클 1번당" 에너지가 비슷해도 "같은 시간 동안"
        # 쓰는 전력은 다릅니다. average_current_ma는 사이클 길이로 이미 나눈 값이라
        # 이 문제가 없습니다 (backend/tools/analyze_power.py와 같은 방식).
        use_current = os.path.exists(power_ext_path)
        source_path = power_ext_path if use_current else power_path
        metric_col = "average_current_ma" if use_current else None

        try:
            if os.path.exists(source_path):
                df_power = pd.read_csv(source_path)
                if use_current and "average_current_ma" not in df_power.columns:
                    use_current = False
                    source_path = power_path
                    df_power = pd.read_csv(power_path) if os.path.exists(power_path) else df_power

                if df_power.empty:
                    st.info("전력 로그 파일에 아직 수신된 데이터가 없습니다.")
                else:
                    if use_current:
                        energy_col = "average_current_ma"
                    else:
                        # 구버전 로그(power_ext.csv 없음) 호환: 에너지 컬럼으로 폴백
                        energy_col = None
                        if 'estimated_energy_mwh' in df_power.columns:
                            energy_col = 'estimated_energy_mwh'
                        elif 'energy' in df_power.columns:
                            energy_col = 'energy'

                    if energy_col is None:
                        st.warning("전력 컬럼(average_current_ma / estimated_energy_mwh)을 찾을 수 없습니다.")
                    else:
                        # 클라이언트 / 프로토콜별 구분
                        # 1) client_id 기준 Gingerbread vs Standard 탐색
                        df_ginger = pd.DataFrame()
                        df_standard = pd.DataFrame()

                        if 'protocol' in df_power.columns:
                            df_ginger = df_power[df_power['protocol'].astype(str).str.contains('Gingerbread|UDP|NodeB', case=False, regex=True)]
                            df_standard = df_power[df_power['protocol'].astype(str).str.contains('Standard|TCP|NodeA', case=False, regex=True)]
                        elif 'client_id' in df_power.columns:
                            # ConnectPacket.client_id는 16바이트라 "ESP32-Gingerbread"는 "ESP32-Gingerbre"로 잘려서 기록됩니다.
                            # "Gingerbread" 전체를 찾으면 절대 안 걸리므로 잘린 형태("Gingerbre")로 맞춥니다.
                            df_ginger = df_power[df_power['client_id'].astype(str).str.contains('Gingerbre|UDP|NodeB|GB', case=False, regex=True)]
                            df_standard = df_power[df_power['client_id'].astype(str).str.contains('Standard|TCP|NodeA|STD', case=False, regex=True)]

                        # 만약 명시적 이름 매칭이 안 된다면 최신 수신된 서로 다른 2개 client_id로 그룹핑
                        if (df_ginger.empty or df_standard.empty) and 'client_id' in df_power.columns:
                            unique_clients = df_power['client_id'].unique()
                            if len(unique_clients) >= 2:
                                df_ginger = df_power[df_power['client_id'] == unique_clients[0]]
                                df_standard = df_power[df_power['client_id'] == unique_clients[1]]
                            elif len(unique_clients) == 1:
                                df_ginger = df_power[df_power['client_id'] == unique_clients[0]]

                        if not df_ginger.empty and not df_standard.empty:
                            # 값 하나(가장 최신 사이클)만 비교하면 QoS가 어느 순간 위험으로 바뀌었는지에 따라
                            # 결과가 크게 튑니다. 최근 WINDOW개 사이클로 비교해야 안정적입니다.
                            WINDOW = 20
                            n_g = min(len(df_ginger), WINDOW)
                            n_s = min(len(df_standard), WINDOW)

                            if use_current and {"act_ms", "slp_ms"} <= set(df_power.columns):
                                # average_current_ma 컬럼을 그냥 .mean() 하면 안 됩니다 — Gingerbread는
                                # 사이클마다 길이가 다른데(60s/30s/3s), 짧은 사이클과 긴 사이클을 똑같은
                                # 비중으로 평균 내면 왜곡됩니다(속도를 평균할 때 거리로 가중해야 하는 것과 같은
                                # 문제). "총 에너지 ÷ 총 시간"으로 시간 가중 평균을 내야 합니다
                                # (backend/tools/analyze_power.py와 동일한 방식).
                                def _time_weighted_current(d: pd.DataFrame) -> float:
                                    cycle_h = (d["act_ms"] + d["slp_ms"]) / 3_600_000.0
                                    total_h = cycle_h.sum()
                                    if total_h <= 0:
                                        return float(d["average_current_ma"].mean())
                                    return float(d["estimated_energy_mwh"].sum() / 3.3 / total_h)

                                avg_ginger = _time_weighted_current(df_ginger.tail(n_g))
                                avg_standard = _time_weighted_current(df_standard.tail(n_s))
                            else:
                                avg_ginger = float(df_ginger[energy_col].tail(n_g).mean())
                                avg_standard = float(df_standard[energy_col].tail(n_s).mean())

                            unit = "mA" if use_current else "mWh"
                            metric_name = "평균 전류" if use_current else "에너지"

                            if avg_standard > 0:
                                diff_percent = ((avg_standard - avg_ginger) / avg_standard) * 100.0
                                diff_val = avg_standard - avg_ginger

                                # Gingerbread가 경고/위험 상태라 더 자주 확인하면 Standard보다 전력을
                                # 더 쓸 수도 있습니다 — 이건 버그가 아니라 의도된 트레이드오프이므로,
                                # 부호에 따라 "절약"과 "더 사용"을 정확히 구분해서 표시합니다.
                                if diff_percent >= 0:
                                    st.metric(
                                        label=f"✨ Gingerbread {metric_name} 비교 (최근 {n_g}/{n_s}개 평균)",
                                        value=f"{diff_percent:.1f}% 절약",
                                        delta=f"Standard 대비 -{diff_val:.4f} {unit}",
                                        delta_color="normal",
                                    )
                                else:
                                    st.metric(
                                        label=f"⚠️ Gingerbread {metric_name} 비교 (최근 {n_g}/{n_s}개 평균)",
                                        value=f"{-diff_percent:.1f}% 더 사용",
                                        delta=f"Standard 대비 +{-diff_val:.4f} {unit}",
                                        delta_color="inverse",
                                    )
                                if use_current:
                                    st.caption(
                                        "⚠️ 소프트웨어 모델 기반 **추정값**입니다 (전류계 실측 아님). 사이클 길이가 달라도 "
                                        "공정하게 비교하려고 평균 전류(mA)를 씁니다. 평상시(QoS 0, 60초 주기)엔 두 노드 "
                                        "주기가 같아 비슷하고, Gingerbread가 경고/위험 상태(QoS 1·2, 30초/3초 주기)면 더 "
                                        "자주 확인하느라 전력을 더 쓸 수 있습니다 — 반응속도를 위한 의도된 트레이드오프입니다. "
                                        f"표본이 {n_g}/{n_s}개로 적으면 이 차이는 통계적으로 유의미하지 않을 수 있습니다 "
                                        "(`backend/tools/analyze_power.py`로 신뢰구간을 확인하세요)."
                                    )
                                else:
                                    st.caption(
                                        "⚠️ 소프트웨어 모델 기반 **추정값**입니다 (전류계 실측 아님). power_ext.csv가 없어 "
                                        "사이클당 에너지로 비교 중인데, 두 노드의 사이클 길이(주기)가 다르면 이 비교가 "
                                        "왜곡될 수 있습니다 — 가능하면 게이트웨이를 새로 실행해 power_ext.csv를 생성하세요."
                                    )
                            else:
                                st.metric(
                                    label=f"✨ Gingerbread {metric_name} 추정값 (최근 {n_g}개 평균)",
                                    value=f"{avg_ginger:.4f} {unit}",
                                )

                            st.divider()

                            chart_data = pd.DataFrame({
                                "프로토콜": ["Gingerbread (적응형)", "Standard (고정)"],
                                f"평균 {metric_name} ({unit})": [avg_ginger, avg_standard],
                            })
                            st.bar_chart(chart_data, x="프로토콜", y=f"평균 {metric_name} ({unit})", color="프로토콜")

                            # Gingerbread가 최근 어느 QoS 상태에 얼마나 있었는지 — 위 숫자가 왜 나왔는지 설명
                            if "qos" in df_ginger.columns:
                                qos_labels = {"0": "정상 (QoS 0)", "1": "경고 (QoS 1)", "2": "위험 (QoS 2)"}
                                qos_counts = (
                                    df_ginger["qos"].astype(str).tail(n_g).value_counts()
                                    .reindex(list(qos_labels.keys()), fill_value=0)
                                )
                                qos_df = pd.DataFrame({
                                    "상태": [qos_labels[k] for k in qos_counts.index],
                                    "사이클 수": qos_counts.values,
                                })
                                st.caption(f"최근 {n_g}개 사이클 중 Gingerbread의 QoS 분포 (위 비교 결과의 배경)")
                                st.bar_chart(qos_df, x="상태", y="사이클 수", color="상태")

                        elif not df_ginger.empty:
                            latest_ginger = float(df_ginger[energy_col].iloc[-1])
                            unit = "mA" if use_current else "mWh"
                            st.info(f"현재 Gingerbread 단말 데이터 수신 중: {latest_ginger:.4f} {unit} (Standard 비교 데이터 대기 중)")
                        else:
                            st.info("비교를 위한 보드별 데이터 수신을 기다리고 있습니다.")

                        # 최근 10개 기록 표출
                        st.caption("최근 전력 소모 기록 원본 (최신 10개)")
                        disp_cols = [c for c in ['timestamp', 'client_id', 'qos', 'rtt_ms', energy_col] if c in df_power.columns]
                        if not disp_cols:
                            disp_cols = df_power.columns.tolist()
                        st.dataframe(df_power[disp_cols].tail(10), use_container_width=True)

            else:
                st.warning("아직 power.csv 파일이 없습니다. 전력 데이터 수신을 기다리는 중입니다...")

        except Exception as e:
            st.error(f"전력 데이터를 불러오는 중 오류가 발생했습니다: {e}")

# 실시간 프래그먼트 호출
render_live_dashboard()