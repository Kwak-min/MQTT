import { Navigate, Route, Routes } from 'react-router-dom';
import { AppHeader } from '@/components/layout/AppHeader';
import { OperationsPage } from '@/pages/OperationsPage';
import { ExperimentPage } from '@/pages/ExperimentPage';
import { ProtocolPage } from '@/pages/ProtocolPage';
import { OperationsProvider, useOperations } from '@/data/OperationsContext';
import { relativeTime } from '@/lib/format';

/** 헤더의 연결 배지는 운영 스냅샷의 성공/실패를 그대로 반영합니다. */
function Header() {
  const { data, error } = useOperations();
  const connected = !error && (data?.node.gatewayConnected ?? false);
  const updatedLabel = data ? `${relativeTime(data.node.updatedAt)} 갱신` : '연결 확인 중';
  return <AppHeader connected={connected} updatedLabel={updatedLabel} />;
}

export default function App() {
  return (
    <OperationsProvider>
      <Header />
      <Routes>
        <Route path="/" element={<OperationsPage />} />
        <Route path="/experiment" element={<ExperimentPage />} />
        <Route path="/protocol" element={<ProtocolPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </OperationsProvider>
  );
}
