import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, dim, muted, labelLight, hairline } = tokens;

export default function LearningPage() {
  const [status, setStatus] = useState(null);
  const [combos, setCombos] = useState([]);
  const [running, setRunning] = useState(false);

  const refresh = async () => {
    try {
      const [s, c] = await Promise.all([
        axios.get(`${API}/learning/status`),
        axios.get(`${API}/learning/combos`),
      ]);
      setStatus(s.data); setCombos(c.data);
    } catch (e) { console.error(e); }
  };
  useEffect(() => { refresh(); }, []);

  const runCycle = async () => {
    setRunning(true);
    toast("LEARNING CYCLE INITIATED");
    try {
      const { data } = await axios.post(`${API}/learning/run`);
      if (data.skipped) toast(`SKIPPED — ${data.reason}`);
      else toast(`COMPLETE — ${data.trades} TRADES · ${data.changes} WEIGHTS ADJUSTED`);
      refresh();
    } catch { toast("LEARNING FAILED"); }
    setRunning(false);
  };

  const reset = async () => {
    if (!window.confirm("Reset all weights to defaults?")) return;
    await axios.post(`${API}/learning/reset`);
    toast("WEIGHTS RESET");
    refresh();
  };

  const lastRun = status?.last_run;
  const weights = status?.weights || [];

  return (
    <CrtShell title="LEARNING ENGINE"
      headerRight={
        <div style={{ display: "flex", gap: 10 }}>
          <button data-testid="run-learning-btn" onClick={runCycle} disabled={running}
            style={btnPrimary(running)}>{running ? "ANALYZING..." : "[ RUN CYCLE ]"}</button>
          <button data-testid="reset-weights-btn" onClick={reset} style={btnGhost}>[ RESET ]</button>
        </div>
      }>
      {/* Status strip */}
      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 20 }}>
        <Stat label="LAST RUN" value={lastRun ? new Date(lastRun.run_at).toLocaleDateString() : "NEVER"}
              sub={lastRun?.run_at ? new Date(lastRun.run_at).toLocaleTimeString() : "—"} color={accent} />
        <Stat label="TRADES ANALYZED" value={lastRun?.trades_analyzed || 0} sub="LIFETIME" />
        <Stat label="OVERALL WIN RATE" value={`${((lastRun?.overall_win_rate || 0) * 100).toFixed(1)}%`}
              color={(lastRun?.overall_win_rate || 0) >= 0.5 ? "#4ade80" : "#f87171"} sub="30D RETURN BASIS" />
        <Stat label="WEIGHTS ADJUSTED" value={Object.keys(lastRun?.weights_changed || {}).length}
              sub="THIS CYCLE" />
        <Stat label="NEXT RUN" value="SUN 02:00" sub="ET WEEKLY" />
      </div>

      <Card title="INSIGHTS">
        {(!lastRun?.insights || lastRun.insights.length === 0) ? (
          <div style={{ color: muted, fontSize: 13, padding: "8px 0", letterSpacing: "0.05em" }}>
            Need 10+ completed trades (30 days old) to generate insights. P&L records build up over time.
          </div>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            {lastRun.insights.map((ins, i) => (
              <div key={i} data-testid={`insight-${i}`} style={{
                padding: "10px 14px", background: "rgba(200,168,75,0.04)",
                borderLeft: `2px solid ${accent}`, fontSize: 13, color: "#e5e7eb",
                letterSpacing: "0.04em", lineHeight: 1.6,
              }}>{ins}</div>
            ))}
          </div>
        )}
      </Card>

      <Card title="LIVE SIGNAL WEIGHTS">
        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <thead>
            <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
              <th style={th}>SIGNAL</th><th style={th}>DEFAULT</th><th style={th}>CURRENT</th>
              <th style={th}>DELTA</th><th style={th}>WIN RATE</th><th style={th}>SAMPLES</th>
              <th style={th}>CONFIDENCE</th>
            </tr>
          </thead>
          <tbody>
            {weights.map((w) => {
              const delta = (w.current_value || 0) - (w.default_value || 0);
              const deltaColor = delta > 0.01 ? "#4ade80" : delta < -0.01 ? "#f87171" : muted;
              return (
                <tr key={w.weight_key} data-testid={`weight-${w.weight_key}`}
                    style={{ borderTop: hairline }}>
                  <td style={td}>{w.weight_key.replace(/_/g, " ").toUpperCase()}</td>
                  <td style={td}>{w.default_value}</td>
                  <td style={{ ...td, color: deltaColor, fontWeight: 700 }}>{w.current_value}</td>
                  <td style={{ ...td, color: deltaColor }}>
                    {delta > 0 ? "+" : ""}{delta.toFixed(2)}
                  </td>
                  <td style={td}>{w.win_rate != null ? `${(w.win_rate * 100).toFixed(0)}%` : "—"}</td>
                  <td style={{ ...td, color: muted }}>{w.sample_count || 0}</td>
                  <td style={td}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <div style={{ flex: 1, height: 4, background: "#1a1a2e" }}>
                        <div style={{
                          height: "100%", width: `${(w.confidence || 0) * 100}%`,
                          background: accent, transition: "width 0.4s",
                        }} />
                      </div>
                      <span style={{ color: muted, minWidth: 32 }}>
                        {((w.confidence || 0) * 100).toFixed(0)}%
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </Card>

      <Card title={`SIGNAL COMBO PERFORMANCE — ${combos.length} TRACKED`}>
        {combos.length === 0 ? (
          <div style={{ color: muted, fontSize: 13, padding: "8px 0", letterSpacing: "0.05em" }}>
            No signal combinations with 3+ trades yet.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>COMBO</th><th style={th}>TRADES</th><th style={th}>WIN%</th>
                <th style={th}>AVG 30D</th><th style={th}>BEST</th><th style={th}>WORST</th>
              </tr>
            </thead>
            <tbody>
              {combos.map((c) => (
                <tr key={c.signal_combo} data-testid={`combo-${c.signal_combo}`}
                    style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent }}>{c.signal_combo.replace(/\|/g, " + ").toUpperCase()}</td>
                  <td style={td}>{c.trade_count}</td>
                  <td style={{
                    ...td,
                    color: c.win_rate >= 0.65 ? "#4ade80" : c.win_rate < 0.40 ? "#f87171" : accent,
                  }}>{(c.win_rate * 100).toFixed(0)}%</td>
                  <td style={{
                    ...td,
                    color: c.avg_return_30d >= 0 ? "#4ade80" : "#f87171",
                  }}>{c.avg_return_30d >= 0 ? "+" : ""}{c.avg_return_30d}%</td>
                  <td style={{ ...td, color: "#4ade80" }}>+{c.best_return}%</td>
                  <td style={{ ...td, color: "#f87171" }}>{c.worst_return}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </CrtShell>
  );
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400 };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em" };
const btnPrimary = (loading) => ({
  background: loading ? "rgba(200,168,75,0.15)" : "transparent",
  border: `0.5px solid ${accent}`, color: accent, fontSize: 12,
  padding: "8px 16px", cursor: loading ? "wait" : "pointer",
  letterSpacing: "0.12em", fontFamily: "Courier New", fontWeight: 700,
});
const btnGhost = {
  background: "transparent", border: `0.5px solid ${dim}`, color: muted,
  fontSize: 12, padding: "8px 16px", cursor: "pointer",
  letterSpacing: "0.12em", fontFamily: "Courier New",
};
