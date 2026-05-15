import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, dim, muted, labelLight, hairline } = tokens;

export default function PerformancePage() {
  const [perf, setPerf] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [seeding, setSeeding] = useState(false);

  const refresh = async () => {
    try {
      const [p, b] = await Promise.all([
        axios.get(`${API}/performance/summary`),
        axios.get(`${API}/backtest/summary`),
      ]);
      setPerf(p.data); setBacktest(b.data);
    } catch (e) { console.error(e); }
  };
  useEffect(() => { refresh(); }, []);

  const seedBacktest = async () => {
    setSeeding(true);
    toast("SEEDING SYNTHETIC BACKTEST...");
    try {
      const { data } = await axios.post(`${API}/backtest/seed`);
      toast(`SEEDED ${data.written || 0} ROWS · ${data.skipped || 0} SKIPPED`);
      refresh();
    } catch { toast("BACKTEST SEED FAILED"); }
    setSeeding(false);
  };

  const signals = perf?.signals || [];
  const options = perf?.options || {};
  const fwd = backtest?.forward || [];
  const syn = backtest?.synthetic || [];

  return (
    <CrtShell title="PERFORMANCE TRACKER"
      headerRight={
        <button data-testid="seed-backtest-btn" onClick={seedBacktest} disabled={seeding}
          style={btnPrimary(seeding)}>{seeding ? "SEEDING..." : "[ SEED BACKTEST ]"}</button>
      }>
      {/* Summary stat strip */}
      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 20 }}>
        <Stat label="SIGNAL COMBOS TRACKED" value={signals.length} sub="WITH RETURNS DATA" />
        <Stat label="OPTIONS STRATEGIES" value={(options.by_strategy || []).length} sub="WITH RETURNS DATA" />
        <Stat label="BACKTEST FORWARD" value={backtest?.forward_count || 0} sub="LIVE TRADES" />
        <Stat label="BACKTEST SYNTHETIC" value={backtest?.synthetic_count || 0} sub="CONGRESS REPLAY" color={accent} />
      </div>

      <Card title="SIGNAL PERFORMANCE — RETURNS BY COMBINATION">
        {signals.length === 0 ? (
          <div style={{ color: muted, fontSize: 13, padding: "8px 0" }}>
            No completed trades yet. Returns populate 7/30/90 days after each scan.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>COMBO</th><th style={th}>N</th><th style={th}>AVG 7D</th>
                <th style={th}>AVG 30D</th><th style={th}>AVG 90D</th><th style={th}>WIN RATE</th>
              </tr>
            </thead>
            <tbody>
              {signals.slice(0, 30).map((s, i) => (
                <tr key={i} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent }}>{s.combo.replace(/\+/g, " + ")}</td>
                  <td style={td}>{s.n}</td>
                  <td style={{ ...td, color: pctColor(s.avg_7d) }}>{fmt(s.avg_7d)}%</td>
                  <td style={{ ...td, color: pctColor(s.avg_30d) }}>{fmt(s.avg_30d)}%</td>
                  <td style={{ ...td, color: pctColor(s.avg_90d) }}>{fmt(s.avg_90d)}%</td>
                  <td style={td}>{s.win_rate_30d != null ? `${s.win_rate_30d}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="OPTIONS PERFORMANCE BY STRATEGY">
        {(!options.by_strategy || options.by_strategy.length === 0) ? (
          <div style={{ color: muted, fontSize: 13 }}>
            Options P&L data accumulates 3 days post-pick. Both proxy (Δ × stock move) and
            actual price refetches are logged.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>STRATEGY</th><th style={th}>N</th>
                <th style={th}>AVG ACTUAL</th><th style={th}>AVG PROXY</th>
                <th style={th}>AVG IV@ENTRY</th><th style={th}>WIN RATE</th>
              </tr>
            </thead>
            <tbody>
              {options.by_strategy.map((r) => (
                <tr key={r.strategy} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent }}>{r.strategy}</td>
                  <td style={td}>{r.n}</td>
                  <td style={{ ...td, color: pctColor(r.avg_return_actual) }}>{fmt(r.avg_return_actual)}%</td>
                  <td style={{ ...td, color: pctColor(r.avg_return_proxy) }}>{fmt(r.avg_return_proxy)}%</td>
                  <td style={td}>{r.avg_iv_at_entry || "—"}</td>
                  <td style={td}>{r.win_rate_actual != null ? `${r.win_rate_actual}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {options.by_crush_risk && options.by_crush_risk.length > 0 && (
          <div style={{ marginTop: 16, paddingTop: 14, borderTop: hairline }}>
            <div style={{ fontSize: 10, color: dim, letterSpacing: "0.14em", marginBottom: 10 }}>
              {"// BY IV CRUSH RISK LEVEL"}
            </div>
            {options.by_crush_risk.map((r) => (
              <div key={r.crush_risk} style={{
                display: "grid", gridTemplateColumns: "120px 60px 100px 100px",
                fontSize: 12, padding: "6px 0", color: labelLight,
              }}>
                <span style={{ color: r.crush_risk === "SEVERE" ? "#f87171"
                                    : r.crush_risk === "HIGH" ? "#fb923c"
                                    : r.crush_risk === "LOW" ? "#4ade80" : accent }}>
                  {r.crush_risk}</span>
                <span>n={r.n}</span>
                <span style={{ color: pctColor(r.avg_return) }}>{fmt(r.avg_return)}%</span>
                <span>WR {r.win_rate}%</span>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="BACKTEST — SYNTHETIC CONGRESS REPLAY">
        {syn.length === 0 ? (
          <div style={{ color: muted, fontSize: 13 }}>
            Run [SEED BACKTEST] above to populate synthetic returns from the curated
            congressional trading dataset replayed against historical yfinance prices.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>COMBO</th><th style={th}>N</th><th style={th}>AVG 30D</th>
                <th style={th}>WIN RATE</th><th style={th}>BEST</th><th style={th}>WORST</th>
              </tr>
            </thead>
            <tbody>
              {syn.map((r, i) => (
                <tr key={i} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent }}>{r.combo}</td>
                  <td style={td}>{r.n}</td>
                  <td style={{ ...td, color: pctColor(r.avg_30d) }}>{fmt(r.avg_30d)}%</td>
                  <td style={td}>{r.win_rate_30d}%</td>
                  <td style={{ ...td, color: "#4ade80" }}>+{r.best}%</td>
                  <td style={{ ...td, color: "#f87171" }}>{r.worst}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {fwd.length > 0 && (
        <Card title="BACKTEST — FORWARD LIVE SCANS">
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>COMBO</th><th style={th}>N</th><th style={th}>AVG 30D</th>
                <th style={th}>WIN RATE</th><th style={th}>BEST</th><th style={th}>WORST</th>
              </tr>
            </thead>
            <tbody>
              {fwd.map((r, i) => (
                <tr key={i} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent }}>{r.combo}</td>
                  <td style={td}>{r.n}</td>
                  <td style={{ ...td, color: pctColor(r.avg_30d) }}>{fmt(r.avg_30d)}%</td>
                  <td style={td}>{r.win_rate_30d}%</td>
                  <td style={{ ...td, color: "#4ade80" }}>+{r.best}%</td>
                  <td style={{ ...td, color: "#f87171" }}>{r.worst}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </CrtShell>
  );
}

const fmt = (v) => v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}`;
const pctColor = (v) => v == null ? muted : v > 0 ? "#4ade80" : v < 0 ? "#f87171" : labelLight;
const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400 };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em" };
const btnPrimary = (loading) => ({
  background: loading ? "rgba(200,168,75,0.15)" : "transparent",
  border: `0.5px solid ${accent}`, color: accent, fontSize: 12,
  padding: "8px 16px", cursor: loading ? "wait" : "pointer",
  letterSpacing: "0.12em", fontFamily: "Courier New", fontWeight: 700,
});
