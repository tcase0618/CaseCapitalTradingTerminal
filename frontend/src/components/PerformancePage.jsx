import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { ResponsiveContainer, AreaChart, Area, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid } from "recharts";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, dim, muted, labelLight, hairline } = tokens;

export default function PerformancePage() {
  const [perf, setPerf] = useState(null);
  const [backtest, setBacktest] = useState(null);
  const [tracker, setTracker] = useState(null);
  const [curve, setCurve] = useState(null);
  const [curveDays, setCurveDays] = useState(90);
  const [seeding, setSeeding] = useState(false);

  const refresh = async () => {
    // Use independent .catch so one failure doesn't kill the others
    axios.get(`${API}/performance/summary`).then(r => setPerf(r.data)).catch(e => console.error("perf:", e));
    axios.get(`${API}/backtest/summary`).then(r => setBacktest(r.data)).catch(e => console.error("backtest:", e));
    axios.get(`${API}/signals/tracker?limit=200&_=${Date.now()}`).then(r => setTracker(r.data)).catch(e => console.error("tracker:", e));
    axios.get(`${API}/signals/curve?days=${curveDays}`).then(r => setCurve(r.data.curve)).catch(e => console.error("curve:", e));
  };
  useEffect(() => { refresh(); }, [curveDays]);

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
        <Stat label="SIGNALS TRACKED" value={tracker?.tracked || 0}
              sub={`${tracker?.total || 0} TOTAL · ${tracker?.winners || 0}W / ${tracker?.losers || 0}L`} color={accent} />
        <Stat label="AVG GAIN SINCE SIGNAL" value={fmt(tracker?.avg_gain_pct) + "%"}
              color={pctColor(tracker?.avg_gain_pct)} sub="ALL TIME" />
        <Stat label="BEST PICK" value={tracker?.best ? `${tracker.best.ticker} ${fmt(tracker.best.gain_pct)}%` : "—"}
              color="#4ade80" sub="HIGHEST GAIN" />
        <Stat label="WORST PICK" value={tracker?.worst ? `${tracker.worst.ticker} ${fmt(tracker.worst.gain_pct)}%` : "—"}
              color="#f87171" sub="LOWEST GAIN" />
        <Stat label="SIGNAL COMBOS" value={(perf?.signals || []).length} sub="WITH 7/30/90D" />
      </div>

      {/* ALL BUY SIGNALS — DAILY P/L */}
      <Card title={`ALL BUY SIGNALS — DAILY P/L · BOUGHT-ON-SIGNAL · ${tracker?.tracked || 0} TRACKED`}>
        {!tracker || !tracker.rows || tracker.rows.length === 0 ? (
          <div style={{ color: muted, fontSize: 13, padding: "10px 0" }}>
            No signals tracked yet. Every scan now records the first time it surfaces a
            ticker — the price at that moment becomes your entry. Run /scan to start.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>TICKER</th><th style={th}>FIRST SIGNAL</th>
                <th style={th}>ENTRY</th><th style={th}>CURRENT</th>
                <th style={th}>GAIN $</th><th style={th}>GAIN %</th>
                <th style={th}>OPT P/L %</th>
                <th style={th}>SIGNALS</th>
                <th style={th}>SEEN</th><th style={th}>STRATEGY</th>
              </tr>
            </thead>
            <tbody>
              {tracker.rows.map((r) => (
                <tr key={r.ticker} data-testid={`signal-row-${r.ticker}`}
                    style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent, fontWeight: 700, fontSize: 14 }}>
                    <a href={`/ticker/${r.ticker}`} style={{ color: accent, textDecoration: "none" }}>
                      ${r.ticker}
                    </a>
                  </td>
                  <td style={td}>{r.first_seen_date || "—"}</td>
                  <td style={td}>${r.first_seen_price?.toFixed(2) || "—"}</td>
                  <td style={td}>${r.current_price?.toFixed(2) || "—"}</td>
                  <td style={{ ...td, color: pctColor(r.gain_abs), fontWeight: 700 }}>
                    {r.gain_abs != null ? `${r.gain_abs >= 0 ? "+" : ""}$${Math.abs(r.gain_abs).toFixed(2)}` : "—"}
                  </td>
                  <td style={{ ...td, color: pctColor(r.gain_pct), fontWeight: 700, fontSize: 14 }}>
                    {fmt(r.gain_pct)}%
                  </td>
                  <td style={{ ...td, color: pctColor(r.options_return_proxy_pct), fontWeight: 700 }}>
                    {r.options_return_proxy_pct != null ? `${fmt(r.options_return_proxy_pct)}%` : "—"}
                  </td>
                  <td style={{ ...td, fontSize: 11 }}>
                    {(r.signals || []).slice(0, 3).map(s => (
                      <span key={s} style={{
                        display: "inline-block", padding: "2px 6px", marginRight: 4,
                        border: `0.5px solid ${tokens.dim}`, color: tokens.labelLight,
                        fontSize: 10, letterSpacing: "0.06em",
                      }}>{s.replace(/_/g, " ")}</span>
                    ))}
                  </td>
                  <td style={td}>{r.times_found}×</td>
                  <td style={{ ...td, color: r.options_strategy ? accent : muted, fontSize: 11 }}>
                    {r.options_strategy
                      ? `${r.options_strategy.replace(/_/g, " ")}${r.options_strike ? ` $${r.options_strike}${r.options_type || ""}` : ""}`
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      {/* AXIOM Performance Curve — Robinhood-style */}
      <Card title={`AXIOM PERFORMANCE — ${curveDays}D · AVG % GAIN ACROSS ALL TRACKED SIGNALS`}
        action={
          <div style={{ display: "flex", gap: 6 }}>
            {[30, 60, 90, 180].map(d => (
              <button key={d} data-testid={`curve-range-${d}`} onClick={() => setCurveDays(d)}
                style={{
                  background: curveDays === d ? "rgba(200,168,75,0.12)" : "transparent",
                  border: `0.5px solid ${curveDays === d ? accent : tokens.dim}`,
                  color: curveDays === d ? accent : tokens.muted,
                  fontSize: 10, padding: "4px 10px", cursor: "pointer",
                  letterSpacing: "0.1em", fontFamily: "Courier New", fontWeight: 700,
                }}>{d}D</button>
            ))}
          </div>
        }>
        {!curve || curve.length === 0 ? (
          <div style={{ color: muted, fontSize: 13, padding: "12px 0" }}>
            Building performance curve... Need at least 1 day of signal history.
          </div>
        ) : (
          <>
            <div style={{ display: "flex", gap: 32, marginBottom: 14, paddingLeft: 8 }}>
              <div>
                <div style={{ fontSize: 9, color: tokens.dim, letterSpacing: "0.14em" }}>CURRENT</div>
                <div style={{
                  fontSize: 30, fontWeight: 300, fontFamily: "Courier New",
                  color: pctColor(curve[curve.length - 1]?.avg_gain_pct),
                  letterSpacing: "0.02em",
                }}>{fmt(curve[curve.length - 1]?.avg_gain_pct)}%</div>
              </div>
              <div>
                <div style={{ fontSize: 9, color: tokens.dim, letterSpacing: "0.14em" }}>PEAK</div>
                <div style={{
                  fontSize: 18, fontWeight: 700, color: "#4ade80", fontFamily: "Courier New",
                }}>+{Math.max(...curve.map(c => c.avg_gain_pct || 0)).toFixed(2)}%</div>
              </div>
              <div>
                <div style={{ fontSize: 9, color: tokens.dim, letterSpacing: "0.14em" }}>TROUGH</div>
                <div style={{
                  fontSize: 18, fontWeight: 700, color: "#f87171", fontFamily: "Courier New",
                }}>{Math.min(...curve.map(c => c.avg_gain_pct || 0)).toFixed(2)}%</div>
              </div>
              <div>
                <div style={{ fontSize: 9, color: tokens.dim, letterSpacing: "0.14em" }}>POSITIONS</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: "#fff", fontFamily: "Courier New" }}>
                  {curve[curve.length - 1]?.positions || 0}
                </div>
              </div>
            </div>
            <div style={{ width: "100%", height: 280, marginLeft: -8 }}>
              <ResponsiveContainer>
                <AreaChart data={curve} margin={{ top: 10, right: 14, left: 0, bottom: 0 }}>
                  <defs>
                    <linearGradient id="axiomGain" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#c8a84b" stopOpacity={0.45} />
                      <stop offset="100%" stopColor="#c8a84b" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
                  <XAxis dataKey="date" stroke="#374151"
                    tick={{ fill: "#4a5568", fontSize: 10, fontFamily: "Courier New" }}
                    tickLine={false} axisLine={{ stroke: "rgba(255,255,255,0.05)" }}
                    interval="preserveStartEnd" minTickGap={50} />
                  <YAxis stroke="#374151" tickFormatter={(v) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%`}
                    tick={{ fill: "#4a5568", fontSize: 10, fontFamily: "Courier New" }}
                    tickLine={false} axisLine={{ stroke: "rgba(255,255,255,0.05)" }}
                    width={70} />
                  <ReferenceLine y={0} stroke="#374151" strokeDasharray="3 3" />
                  <Tooltip
                    contentStyle={{
                      background: "#0c0c12", border: "0.5px solid rgba(200,168,75,0.4)",
                      fontSize: 11, fontFamily: "Courier New", color: "#e5e7eb",
                      letterSpacing: "0.04em",
                    }}
                    formatter={(v, n) => [`${v >= 0 ? "+" : ""}${Number(v).toFixed(2)}%`, "AVG GAIN"]}
                    labelFormatter={(l) => `${l}`}
                  />
                  <Area type="monotone" dataKey="avg_gain_pct" stroke="#c8a84b" strokeWidth={2}
                    fill="url(#axiomGain)" />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </Card>

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
