import { useEffect, useMemo, useState } from "react";
import { useNodes } from "@/hooks/useNodes";
import { useLiveTelemetry } from "@/hooks/useLiveTelemetry";
import { usePowerHistory } from "@/hooks/usePowerHistory";
import { useTelemetrySource, useConnectionState } from "@/hooks/useTelemetrySource";
import { StatTile } from "@/components/ui/StatTile";
import { DeviceStateBadge } from "@/components/ui/DeviceStateBadge";
import { GasValidTag } from "@/components/ui/GasValidTag";
import { LineChart } from "@/components/charts/LineChart";
import { EnergyBarCompare } from "@/components/dashboard/EnergyBarCompare";
import { SpikeFilterToggle } from "@/components/dashboard/SpikeFilterToggle";
import { PayloadViewer } from "@/components/dashboard/PayloadViewer";
import { ExperimentControlPanel } from "@/components/dashboard/ExperimentControlPanel";
import {
  FIRMWARE_COLOR,
  formatEnergyMj,
  formatGasResistance,
  formatHumidity,
  formatRelativeTime,
  formatTemperature,
} from "@/lib/format";
import type { NodeInfo, PowerSample, SessionSummary } from "@/lib/dataSource/types";

const EXPERIMENT_STATUS_LABEL: Record<SessionSummary["status"], string> = {
  running: "실험 진행 중",
  completed: "실험 완료",
  error: "실험 오류",
  aborted: "실험 중단됨",
};

const EXPERIMENT_STATUS_COLOR: Record<SessionSummary["status"], string> = {
  running: "var(--signal-green)",
  completed: "var(--text-secondary)",
  error: "var(--signal-red)",
  aborted: "var(--signal-amber)",
};

const CONNECTION_LABEL: Record<string, string> = {
  connected: "연결됨",
  connecting: "연결 중",
  disconnected: "연결 끊김",
  error: "오류",
};

function deriveExperimentStatus(sessions: SessionSummary[]): SessionSummary["status"] | null {
  if (sessions.length === 0) return null;
  const priority: SessionSummary["status"][] = ["error", "running", "aborted", "completed"];
  for (const status of priority) {
    if (sessions.some((s) => s.status === status)) return status;
  }
  return sessions[0].status;
}

function summarizeEnergy(samples: PowerSample[], excludeSpikes: boolean) {
  const filtered = excludeSpikes ? samples.filter((s) => !s.isConnectionSpike) : samples;
  const withEnergy = filtered.filter(
    (s): s is PowerSample & { energyMj: number } => s.energyMj !== undefined
  );
  if (withEnergy.length === 0) return { avgMj: null as number | null, totalMj: 0 };
  const totalMj = withEnergy.reduce((sum, s) => sum + s.energyMj, 0);
  return { avgMj: totalMj / withEnergy.length, totalMj };
}

export function OverviewPage() {
  const { nodes, loading } = useNodes();
  const source = useTelemetrySource();
  const connectionState = useConnectionState();
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [spikeFilter, setSpikeFilter] = useState(true);
  const [reportIntervalS, setReportIntervalS] = useState<number | null>(null);
  const [applyingInterval, setApplyingInterval] = useState(false);
  const [truncating, setTruncating] = useState(false);
  const [exportingNodeId, setExportingNodeId] = useState<string | null>(null);
  const [selectedPayloadNode, setSelectedPayloadNode] = useState<string | null>(null);

  useEffect(() => {
    source.listSessions().then(setSessions);
  }, [source]);

  const gingerbread = nodes.find((n) => n.firmware === "gingerbread") ?? null;
  const standardMqtt = nodes.find((n) => n.firmware === "standard_mqtt") ?? null;

  const gbTelemetry = useLiveTelemetry(gingerbread?.nodeId ?? null, 40);
  const mqttTelemetry = useLiveTelemetry(standardMqtt?.nodeId ?? null, 40);

  const gbPower = usePowerHistory(gingerbread?.nodeId ?? null, 60, 3000);
  const mqttPower = usePowerHistory(standardMqtt?.nodeId ?? null, 60, 3000);

  const gbEnergy = useMemo(() => summarizeEnergy(gbPower.samples, spikeFilter), [gbPower.samples, spikeFilter]);
  const mqttEnergy = useMemo(
    () => summarizeEnergy(mqttPower.samples, spikeFilter),
    [mqttPower.samples, spikeFilter]
  );

  const savingsPct =
    gbEnergy.avgMj !== null && mqttEnergy.avgMj !== null && mqttEnergy.avgMj > 0
      ? ((mqttEnergy.avgMj - gbEnergy.avgMj) / mqttEnergy.avgMj) * 100
      : null;

  const relevantSessions = sessions.filter(
    (s) => s.nodeId === gingerbread?.nodeId || s.nodeId === standardMqtt?.nodeId
  );
  const experimentStatus = deriveExperimentStatus(relevantSessions);

  const gbFiltered = spikeFilter ? gbPower.samples.filter((s) => !s.isConnectionSpike) : gbPower.samples;
  const mqttFiltered = spikeFilter ? mqttPower.samples.filter((s) => !s.isConnectionSpike) : mqttPower.samples;

  const payloadOptions = [
    gingerbread && {
      nodeId: gingerbread.nodeId,
      label: gingerbread.name,
      color: FIRMWARE_COLOR.gingerbread,
      payload: gbTelemetry.latest?.rawPayload ?? null,
    },
    standardMqtt && {
      nodeId: standardMqtt.nodeId,
      label: standardMqtt.name,
      color: FIRMWARE_COLOR.standard_mqtt,
      payload: mqttTelemetry.latest?.rawPayload ?? null,
    },
  ].filter((o): o is { nodeId: string; label: string; color: string; payload: string | null } => Boolean(o));

  const activePayloadNodeId = selectedPayloadNode ?? payloadOptions[0]?.nodeId ?? null;

  async function handleSetInterval(seconds: number) {
    const targets = [gingerbread, standardMqtt].filter((n): n is NodeInfo => n !== null);
    if (targets.length === 0) return;
    setApplyingInterval(true);
    try {
      await Promise.all(
        targets.map((n) => source.sendControlCommand(n.nodeId, "set_report_interval", { seconds }))
      );
      setReportIntervalS(seconds);
    } finally {
      setApplyingInterval(false);
    }
  }

  async function handleTruncate() {
    const targets = [gingerbread, standardMqtt].filter((n): n is NodeInfo => n !== null);
    if (targets.length === 0) return;
    if (!window.confirm("로그를 초기화할까요? 헤더만 남고 데이터 본문이 모두 삭제됩니다.")) return;
    setTruncating(true);
    try {
      await Promise.all(targets.map((n) => source.truncateTelemetry(n.nodeId)));
    } finally {
      setTruncating(false);
    }
  }

  async function handleExportCsv(nodeId: string) {
    setExportingNodeId(nodeId);
    try {
      const blob = await source.exportTelemetryCsv(nodeId);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${nodeId}-telemetry.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } finally {
      setExportingNodeId(null);
    }
  }

  if (loading) {
    return <div className="empty-state">노드 정보를 불러오는 중…</div>;
  }

  if (!gingerbread || !standardMqtt) {
    return (
      <div className="empty-state">
        Gingerbread 와 Standard MQTT 노드가 모두 등록되어야 비교 대시보드를 표시할 수 있습니다.
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div>
          <h1>실험 개요</h1>
          <p className="page-sub">
            {gingerbread.name} vs {standardMqtt.name} · 통신 1회당 에너지 소모 비교
          </p>
        </div>
      </div>

      {/* KPI: 실험 상태 → 에너지 절감 순으로 시선이 이동하도록 상태 타일을 가장 먼저 배치 */}
      <div className="grid-stats">
        <StatTile
          label="실험 / 연결 상태"
          value={experimentStatus ? EXPERIMENT_STATUS_LABEL[experimentStatus] : "대기 중"}
          accent={experimentStatus ? EXPERIMENT_STATUS_COLOR[experimentStatus] : undefined}
          sub={`연결 ${CONNECTION_LABEL[connectionState] ?? connectionState}`}
        />
        <StatTile
          label="Gingerbread 1회 통신 에너지"
          value={gbEnergy.avgMj !== null ? gbEnergy.avgMj.toFixed(1) : "-"}
          unit="mJ"
          accent="var(--signal-violet)"
          sub={gingerbread.name}
        />
        <StatTile
          label="Standard MQTT 1회 통신 에너지"
          value={mqttEnergy.avgMj !== null ? mqttEnergy.avgMj.toFixed(1) : "-"}
          unit="mJ"
          accent="var(--signal-teal)"
          sub={standardMqtt.name}
        />
        <StatTile
          label="Gingerbread 상대 에너지 절감률"
          value={savingsPct !== null ? savingsPct.toFixed(0) : "-"}
          unit="%"
          accent={savingsPct !== null && savingsPct >= 0 ? "var(--signal-green)" : "var(--signal-red)"}
          sub="Standard MQTT 대비"
        />
      </div>

      {/* 두 보드 비교 차트: 회당 에너지 추이 + 누적 에너지 */}
      <div className="panel">
        <div className="panel-header">
          <h2>회당 통신 에너지 추이</h2>
          <SpikeFilterToggle checked={spikeFilter} onChange={setSpikeFilter} />
        </div>
        <div className="panel-body">
          <LineChart
            series={[
              {
                label: `${standardMqtt.name} (mJ)`,
                color: FIRMWARE_COLOR.standard_mqtt,
                values: mqttFiltered.map((s) => s.energyMj ?? 0),
              },
              {
                label: `${gingerbread.name} (mJ)`,
                color: FIRMWARE_COLOR.gingerbread,
                values: gbFiltered.map((s) => s.energyMj ?? 0),
              },
            ]}
            formatValue={formatEnergyMj}
          />
        </div>
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-header">
          <h2>누적 통신 에너지</h2>
        </div>
        <div className="panel-body">
          <EnergyBarCompare
            bars={[
              { label: gingerbread.name, color: FIRMWARE_COLOR.gingerbread, valueMj: gbEnergy.totalMj },
              { label: standardMqtt.name, color: FIRMWARE_COLOR.standard_mqtt, valueMj: mqttEnergy.totalMj },
            ]}
          />
        </div>
      </div>

      {/* 센서 및 단말 상태 */}
      <div className="grid-two" style={{ marginTop: 16 }}>
        <div className="panel">
          <div className="panel-header">
            <h2>센서 · 단말 상태</h2>
          </div>
          <div className="panel-body">
            <div style={{ display: "flex", flexDirection: "column", gap: 14, marginBottom: 16 }}>
              <BoardStatusRow
                name={gingerbread.name}
                color={FIRMWARE_COLOR.gingerbread}
                deviceState={gbTelemetry.latest?.deviceState}
                gasValid={gbTelemetry.latest?.gasValid}
                temperatureC={gbTelemetry.latest?.temperatureC}
                humidityPct={gbTelemetry.latest?.humidityPct}
                gasResistanceOhm={gbTelemetry.latest?.gasResistanceOhm}
                updatedAt={gbTelemetry.latest?.timestamp}
              />
              <BoardStatusRow
                name={standardMqtt.name}
                color={FIRMWARE_COLOR.standard_mqtt}
                deviceState={mqttTelemetry.latest?.deviceState}
                gasValid={mqttTelemetry.latest?.gasValid}
                temperatureC={mqttTelemetry.latest?.temperatureC}
                humidityPct={mqttTelemetry.latest?.humidityPct}
                gasResistanceOhm={mqttTelemetry.latest?.gasResistanceOhm}
                updatedAt={mqttTelemetry.latest?.timestamp}
              />
            </div>
            <LineChart
              series={[
                {
                  label: `${standardMqtt.name} 가스저항 (kΩ)`,
                  color: FIRMWARE_COLOR.standard_mqtt,
                  values: mqttTelemetry.samples.map((s) => s.gasResistanceOhm / 1000),
                },
                {
                  label: `${gingerbread.name} 가스저항 (kΩ)`,
                  color: FIRMWARE_COLOR.gingerbread,
                  values: gbTelemetry.samples.map((s) => s.gasResistanceOhm / 1000),
                },
              ]}
              formatValue={(v) => `${v.toFixed(1)} kΩ`}
              height={140}
            />
          </div>
        </div>

        <div className="panel">
          <div className="panel-header">
            <h2>최신 페이로드</h2>
          </div>
          <div className="panel-body">
            <PayloadViewer
              options={payloadOptions}
              selectedNodeId={activePayloadNodeId}
              onSelect={setSelectedPayloadNode}
            />
          </div>
        </div>
      </div>

      {/* 실험 제어 */}
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="panel-header">
          <h2>실험 제어</h2>
        </div>
        <div className="panel-body">
          <ExperimentControlPanel
            activeIntervalS={reportIntervalS}
            applyingInterval={applyingInterval}
            onSetInterval={handleSetInterval}
            onTruncate={handleTruncate}
            truncating={truncating}
            onExportCsv={handleExportCsv}
            exportingNodeId={exportingNodeId}
            statusLabel={experimentStatus ? EXPERIMENT_STATUS_LABEL[experimentStatus] : "대기 중"}
            statusColor={experimentStatus ? EXPERIMENT_STATUS_COLOR[experimentStatus] : "var(--text-tertiary)"}
            exportTargets={[
              { nodeId: gingerbread.nodeId, label: gingerbread.name },
              { nodeId: standardMqtt.nodeId, label: standardMqtt.name },
            ]}
          />
        </div>
      </div>
    </div>
  );
}

function BoardStatusRow({
  name,
  color,
  deviceState,
  gasValid,
  temperatureC,
  humidityPct,
  gasResistanceOhm,
  updatedAt,
}: {
  name: string;
  color: string;
  deviceState?: "active" | "asleep";
  gasValid?: boolean;
  temperatureC?: number;
  humidityPct?: number;
  gasResistanceOhm?: number;
  updatedAt?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        flexWrap: "wrap",
        gap: 10,
        padding: "10px 12px",
        borderRadius: "var(--radius-sm)",
        background: "var(--bg-inset)",
        border: "1px solid var(--border-hair)",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 140 }}>
        <span className="dot" style={{ background: color }} />
        <span style={{ fontSize: 13 }}>{name}</span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        {temperatureC !== undefined && (
          <MiniValue label="온도" value={formatTemperature(temperatureC)} />
        )}
        {humidityPct !== undefined && <MiniValue label="습도" value={formatHumidity(humidityPct)} />}
        {gasResistanceOhm !== undefined && (
          <MiniValue label="가스" value={formatGasResistance(gasResistanceOhm)} />
        )}
        <DeviceStateBadge state={deviceState} />
        <GasValidTag valid={gasValid} />
        {updatedAt && (
          <span className="mono" style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            {formatRelativeTime(updatedAt)}
          </span>
        )}
      </div>
    </div>
  );
}

function MiniValue({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{label}</div>
      <div className="mono" style={{ fontSize: 13 }}>
        {value}
      </div>
    </div>
  );
}
