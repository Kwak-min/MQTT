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

        try:
            if os.path.exists(power_path):
                df_power = pd.read_csv(power_path)
                
                if df_power.empty:
                    st.info("power.csv 파일에 아직 수신된 데이터가 없습니다.")
                else:
                    # 에너지 컬럼 찾기 (estimated_energy_mwh 또는 energy)
                    energy_col = None
                    if 'estimated_energy_mwh' in df_power.columns:
                        energy_col = 'estimated_energy_mwh'
                    elif 'energy' in df_power.columns:
                        energy_col = 'energy'

                    if energy_col is None:
                        st.warning("전력/에너지 컬럼(estimated_energy_mwh)을 찾을 수 없습니다.")
                    else:
                        # 클라이언트 / 프로토콜별 구분
                        # 1) client_id 기준 Gingerbread vs Standard 탐색
                        df_ginger = pd.DataFrame()
                        df_standard = pd.DataFrame()

                        if 'protocol' in df_power.columns:
                            df_ginger = df_power[df_power['protocol'].astype(str).str.contains('Gingerbread|UDP|NodeB', case=False, regex=True)]
                            df_standard = df_power[df_power['protocol'].astype(str).str.contains('Standard|TCP|NodeA', case=False, regex=True)]
                        elif 'client_id' in df_power.columns:
                            df_ginger = df_power[df_power['client_id'].astype(str).str.contains('Gingerbread|UDP|NodeB|GB', case=False, regex=True)]
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
                            latest_ginger = float(df_ginger[energy_col].iloc[-1])
                            latest_standard = float(df_standard[energy_col].iloc[-1])

                            if latest_standard > 0:
                                saved_percent = ((latest_standard - latest_ginger) / latest_standard) * 100.0
                                saved_energy = latest_standard - latest_ginger

                                st.metric(
                                    label="✨ Gingerbread 에너지 절감 효과",
                                    value=f"{saved_percent:.1f}% 절약!",
                                    delta=f"Standard 대비 -{saved_energy:.6f} mWh ↓",
                                    delta_color="inverse"
                                )
                            else:
                                st.metric(
                                    label="✨ Gingerbread 에너지 추정값",
                                    value=f"{latest_ginger:.6f} mWh"
                                )

                            st.divider()

                            chart_data = pd.DataFrame({
                                "프로토콜": ["Gingerbread (UDP)", "Standard (TCP)"],
                                "소비 에너지 (mWh)": [latest_ginger, latest_standard]
                            })
                            st.bar_chart(chart_data, x="프로토콜", y="소비 에너지 (mWh)", color="프로토콜")

                        elif not df_ginger.empty:
                            latest_ginger = float(df_ginger[energy_col].iloc[-1])
                            st.info(f"현재 Gingerbread 단말 데이터 수신 중: {latest_ginger:.6f} mWh (Standard 비교 데이터 대기 중)")
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