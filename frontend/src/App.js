import "@/App.css";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import Dashboard from "@/components/Dashboard";
import LearningPage from "@/components/LearningPage";
import PerformancePage from "@/components/PerformancePage";
import TickerPage from "@/components/TickerPage";
import SettingsPage from "@/components/SettingsPage";
import { Toaster } from "sonner";

function App() {
  return (
    <div className="App min-h-screen bg-slate-950 text-slate-50">
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/learning" element={<LearningPage />} />
          <Route path="/performance" element={<PerformancePage />} />
          <Route path="/ticker/:ticker" element={<TickerPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Routes>
      </BrowserRouter>
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
