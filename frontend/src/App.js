import "@/App.css";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import CommandCenterPage from "@/components/CommandCenterPage";
import Dashboard from "@/components/Dashboard";
import LearningPage from "@/components/LearningPage";
import PerformancePage from "@/components/PerformancePage";
import TickerPage from "@/components/TickerPage";
import SettingsPage from "@/components/SettingsPage";
import EarningsPage from "@/components/EarningsPage";
import LotteryPage from "@/components/LotteryPage";
import IntelPage from "@/components/IntelPage";
import PharmaPage from "@/components/PharmaPage";
import ContractsPage from "@/components/ContractsPage";
import SECPage from "@/components/SECPage";
import GeoRiskPage from "@/components/GeoRiskPage";
import MacroPage from "@/components/MacroPage";
import KronosPage from "@/components/KronosPage";
import CaseCourtPage from "@/components/CaseCourtPage";
import TradeFloorPage from "@/components/TradeFloorPage";
import OptionsDeskPage from "@/components/OptionsDeskPage";
import TFEnginePage from "@/components/TFEnginePage";
import PortfolioManagerPage from "@/components/PortfolioManagerPage";
import AuditLogsPage from "@/components/AuditLogsPage";
import QualityPage from "@/components/QualityPage";
import TruthReviewPage from "@/components/TruthReviewPage";
import StartupGate from "@/components/StartupGate";
import TerminalErrorBoundary from "@/components/TerminalErrorBoundary";
import { Toaster } from "sonner";

function App() {
  return (
    <div className="App min-h-screen bg-slate-950 text-slate-50">
      <StartupGate>
        <TerminalErrorBoundary>
          <BrowserRouter>
            <Routes>
              <Route path="/" element={<CommandCenterPage />} />
              <Route path="/command-center" element={<CommandCenterPage />} />
              <Route path="/scanner" element={<Dashboard />} />
              <Route path="/learning" element={<LearningPage />} />
              <Route path="/tf-engine" element={<TFEnginePage />} />
              <Route path="/performance" element={<PerformancePage />} />
              <Route path="/earnings" element={<EarningsPage />} />
              <Route path="/lottery" element={<LotteryPage />} />
              <Route path="/intel" element={<IntelPage />} />
              <Route path="/pharma" element={<PharmaPage />} />
              <Route path="/contracts" element={<ContractsPage />} />
              <Route path="/sec" element={<SECPage />} />
              <Route path="/georisk" element={<GeoRiskPage />} />
              <Route path="/macro" element={<MacroPage />} />
              <Route path="/kronos" element={<KronosPage />} />
              <Route path="/case-court" element={<CaseCourtPage />} />
              <Route path="/research" element={<Navigate to="/settings" replace />} />
              <Route path="/trade-floor" element={<TradeFloorPage />} />
              <Route path="/options-desk" element={<OptionsDeskPage />} />
              <Route path="/portfolio-manager" element={<PortfolioManagerPage />} />
              <Route path="/ticker/:ticker" element={<TickerPage />} />
              <Route path="/audit-logs" element={<AuditLogsPage />} />
              <Route path="/quality" element={<QualityPage />} />
              <Route path="/truth-review" element={<TruthReviewPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </BrowserRouter>
        </TerminalErrorBoundary>
      </StartupGate>
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "#0f172a",
            border: "1px solid #1e293b",
            color: "#f8fafc",
            fontFamily: "JetBrains Mono, monospace",
            fontSize: "12px",
            borderRadius: 0,
          },
        }}
      />
    </div>
  );
}

export default App;
