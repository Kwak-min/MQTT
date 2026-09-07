import { OverviewPage } from "@/pages/OverviewPage";
import { ConnectionBadge } from "@/components/ui/ConnectionBadge";

export function App() {
  return (
    <div className="app-shell-standalone">
      <header className="topbar-standalone">
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, letterSpacing: "-0.01em" }}>노드 콘솔 · 실험 개요</div>
          <div className="mono" style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
            MQTT · UDP 센서 실험
          </div>
        </div>
        <ConnectionBadge />
      </header>
      <main className="main-scroll-standalone">
        <OverviewPage />
      </main>
    </div>
  );
}
