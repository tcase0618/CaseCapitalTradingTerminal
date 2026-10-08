import "@/App.css";
import { Suspense } from "react";
import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { pages } from "@/lib/pageRegistry";
import { CrtShell } from "@/components/CrtShell";
import StartupGate from "@/components/StartupGate";
import TerminalErrorBoundary from "@/components/TerminalErrorBoundary";
import { Toaster } from "sonner";

function App() {
  return (
    <div className="App min-h-screen bg-slate-950 text-slate-50">
      <StartupGate>
        <TerminalErrorBoundary>
          <BrowserRouter>
            <Suspense fallback={<CrtShell title="LOADING WORKSPACE"><div className="workspace-skeleton" role="status" aria-label="Loading workspace"><span /><span /><span /></div></CrtShell>}>
              <Routes>
                {pages.map(({ path, Component }) => <Route key={path} path={path} element={<Component />} />)}
                <Route path="/command-center" element={<Navigate to="/" replace />} />
                <Route path="/research" element={<Navigate to="/settings" replace />} />
                <Route path="*" element={<CrtShell title="WORKSPACE NOT FOUND"><a href="/">Return to Command Center</a></CrtShell>} />
              </Routes>
            </Suspense>
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
