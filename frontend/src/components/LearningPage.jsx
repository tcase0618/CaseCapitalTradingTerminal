import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { ResponsiveContainer, LineChart, Line, XAxis, YAxis, Tooltip, ReferenceLine, CartesianGrid, Legend } from "recharts";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, dim, muted, labelLight, hairline } = tokens;

const STROKES = ["#c8a84b", "#5eead4", "#f87171", "#a78bfa", "#fb923c", "#4ade80",
                  "#60a5fa", "#fbbf24", "#f472b6", "#34d399", "#e879f9", "#facc15"];

export default function LearningPage() {
  const [status, setStatus] = useState(null);
  const [combos, setCombos] = useState([]);
  const [preview, setPreview] = useState(null);
  const [signalStats, setSignalStats] = useState([]);
  const [history, setHistory] = useState([]);
  const [running, setRunning] = useState(false);
  const [selectedSignal, setSelectedSignal] = useState(null);

  const refresh = async () => {
    // Independent .catch so one failure doesn't kill the page
    axios.get(`${API}/learning/status`).then(r => setStatus(r.data)).catch(e => console.error("status:", e));
    axios.get(`${API}/learning/combos`).then(r => setCombos(r.data)).catch(e => console.error("combos:", e));
    axios.get(`${API}/learning/preview`).then(r => setPreview(r.data)).catch(e => console.error("preview:", e));
    axios.get(`${API}/learning/signal_stats`).then(r => setSignalStats(r.data)).catch(e => console.error("stats:", e));
    axios.get(`${API}/learning/weight_history?limit=2000`).then(r => setHistory(r.data)).catch(e => console.error("history:", e));
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

  // Build history chart data: { ts: ..., [signal]: value } grouped by timestamp
  const historyChart = useMemo(() => {
    if (!history.length) return { data: [], keys: [] };
    const byTs = {};
    const keys = new Set();
    for (const h of history) {
      const t = new Date(h.ts).toLocaleDateString();
      byTs[t] = byTs[t] || { ts: t };
      byTs[t][h.weight_key] = h.new_value;
      keys.add(h.weight_key);
    }
    return {
      data: Object.values(byTs).sort((a, b) => new Date(a.ts) - new Date(b.ts)),
      keys: Array.from(keys),
    };
  }, [history]);

  // Filter dropdown lists ALL live signals (so user can browse before first cycle)
  const filterOptions = useMemo(() => {
    if (historyChart.keys.length > 0) return historyChart.keys;
    return weights.map(w => w.weight_key);
  }, [historyChart.keys, weights]);

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
      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 20, flexWrap: "wrap" }}>
        <Stat label="LAST RUN" value={lastRun ? new Date(lastRun.run_at).toLocaleDateString() : "NEVER"}
              sub={lastRun?.run_at ? new Date(lastRun.run_at).toLocaleTimeString() : "—"} color={accent} />
        <Stat label="TRADES ANALYZED" value={preview?.trades_available || 0}
              sub={`${preview?.trades_30d || 0} × 30D + ${(preview?.trades_live || 0) - (preview?.trades_30d || 0)} × LIVE`} />
        <Stat label="OVERALL WIN RATE" value={`${((lastRun?.overall_win_rate || 0) * 100).toFixed(1)}%`}
              color={(lastRun?.overall_win_rate || 0) >= 0.5 ? "#4ade80" : "#f87171"} sub="ALL TRADES" />
        <Stat label="PENDING CHANGES" value={preview?.would_change_count || 0}
              color={preview?.would_change_count > 0 ? "#fb923c" : muted} sub="IF RUN NOW" />
        <Stat label="WEIGHTS ADJUSTED" value={Object.keys(lastRun?.weights_changed || {}).length}
              sub="LAST CYCLE" />
        <Stat label="NEXT AUTO RUN" value="SUN 02:00" sub="ET WEEKLY" />
      </div>

      {/* PREVIEW — what the next cycle would do */}
      <Card title={`PENDING ADJUSTMENTS — DRY-RUN OF NEXT CYCLE${preview?.would_run ? "" : ` · BLOCKED (NEED ${preview?.min_required || 10}+ COMPLETED TRADES)`}`}
        action={preview && (
          <div style={{ fontSize: 11, color: muted, letterSpacing: "0.1em" }}>
            {preview.trades_available} / {preview.min_required} TRADES
          </div>
        )}>
        {!preview || !preview.rows ? (
          <div style={{ color: muted, fontSize: 13 }}>Computing projection...</div>
        ) : preview.would_change_count === 0 ? (
          <div style={{ color: muted, fontSize: 13, padding: "8px 0", letterSpacing: "0.04em" }}>
            {preview.would_run
              ? "No weights would change next cycle — all signals either lack 10+ samples or are within ±0.05 of their current value."
              : `Need ${preview.min_required - preview.trades_available} more completed trades (30d returns) before the engine engages. Trades complete automatically 30 days after each scan.`}
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>SIGNAL</th><th style={th}>CURRENT</th>
                <th style={th}>PROJECTED</th><th style={th}>DELTA</th>
                <th style={th}>WIN RATE</th><th style={th}>SAMPLES</th>
                <th style={th}>BASIS</th>
                <th style={th}>CONF.</th>
              </tr>
            </thead>
            <tbody>
              {preview.rows.filter(r => r.would_change).map(r => (
                <tr key={r.weight_key} data-testid={`preview-${r.weight_key}`} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent, fontSize: 12 }}>{r.weight_key.replace(/_/g, " ").toUpperCase()}</td>
                  <td style={td}>{r.current?.toFixed(2)}</td>
                  <td style={{ ...td, color: r.delta > 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>{r.projected?.toFixed(2)}</td>
                  <td style={{ ...td, color: r.delta > 0 ? "#4ade80" : "#f87171" }}>
                    {r.delta > 0 ? "+" : ""}{r.delta?.toFixed(2)} ({r.pct >= 0 ? "+" : ""}{r.pct?.toFixed(1)}%)
                  </td>
                  <td style={td}>{r.win_rate != null ? `${(r.win_rate * 100).toFixed(0)}%` : "—"}</td>
                  <td style={td}>{r.samples}</td>
                  <td style={{ ...td, color: r.basis === "30d" ? "#4ade80" : accent, fontSize: 10 }}>
                    {r.basis ? r.basis.toUpperCase() : "—"}
                  </td>
                  <td style={td}>{r.confidence != null ? `${(r.confidence * 100).toFixed(0)}%` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title="INSIGHTS — LAST CYCLE">
        {(!lastRun?.insights || lastRun.insights.length === 0) ? (
          <div style={{ color: muted, fontSize: 13, padding: "8px 0", letterSpacing: "0.05em" }}>
            Run a learning cycle once you have 10+ trades with 30-day returns. Insights surface high-WR signals, weak signals, and the best signal combination.
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

      {/* WEIGHT EVOLUTION CHART */}
      <Card title={`WEIGHT EVOLUTION OVER TIME · ${historyChart.keys.length} SIGNALS · ${history.length} ADJUSTMENTS`}
        action={
          <select data-testid="signal-filter" value={selectedSignal || ""} onChange={e => setSelectedSignal(e.target.value || null)}
            style={{
              background: "transparent", border: `0.5px solid ${dim}`, color: muted,
              fontFamily: "Courier New", fontSize: 11, padding: "4px 8px",
              letterSpacing: "0.1em", outline: "none",
            }}>
            <option value="">ALL SIGNALS</option>
            {filterOptions.map(k => (
              <option key={k} value={k}>{k.replace(/_/g, " ").toUpperCase()}</option>
            ))}
          </select>
        }>
        {historyChart.data.length === 0 ? (
          <div style={{ color: muted, fontSize: 13, padding: "12px 0" }}>
            No weight changes recorded yet. Once the engine starts adjusting weights, the trajectory of each signal's importance will plot here.
          </div>
        ) : (
          <div style={{ width: "100%", height: 320, marginLeft: -8 }}>
            <ResponsiveContainer>
              <LineChart data={historyChart.data} margin={{ top: 10, right: 14, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="2 4" stroke="rgba(255,255,255,0.04)" vertical={false} />
                <XAxis dataKey="ts" stroke="#374151"
                  tick={{ fill: "#4a5568", fontSize: 10, fontFamily: "Courier New" }}
                  tickLine={false} axisLine={{ stroke: "rgba(255,255,255,0.05)" }}
                  interval="preserveStartEnd" minTickGap={50} />
                <YAxis stroke="#374151"
                  tick={{ fill: "#4a5568", fontSize: 10, fontFamily: "Courier New" }}
                  tickLine={false} axisLine={{ stroke: "rgba(255,255,255,0.05)" }}
                  width={50} />
                <Tooltip contentStyle={{
                  background: "#0c0c12", border: `0.5px solid ${accent}66`,
                  fontSize: 11, fontFamily: "Courier New", color: "#e5e7eb",
                }} />
                <Legend wrapperStyle={{ fontSize: 10, fontFamily: "Courier New", letterSpacing: "0.1em" }} />
                {(selectedSignal ? [selectedSignal] : historyChart.keys).map((k, i) => (
                  <Line key={k} type="monotone" dataKey={k}
                    stroke={STROKES[i % STROKES.length]} strokeWidth={1.5}
                    dot={{ r: 2 }} activeDot={{ r: 4 }} connectNulls />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <Card title="LIVE SIGNAL WEIGHTS — POST-LEARNING VALUES IN USE BY SCANNER">
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

      {/* SIGNAL LIFETIME LEAGUE TABLE — every scanned stock counts as a trade */}
      <Card title="SIGNAL LIFETIME PERFORMANCE — EVERY SCANNED STOCK COUNTS AS A TRADE">
        {signalStats.length === 0 ? (
          <div style={{ color: muted, fontSize: 13 }}>No completed trades yet.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
            <thead>
              <tr style={{ color: dim, letterSpacing: "0.12em", textAlign: "left" }}>
                <th style={th}>SIGNAL</th><th style={th}>TRADES</th><th style={th}>WIN%</th>
                <th style={th}>AVG LIVE</th><th style={th}>AVG 30D</th>
                <th style={th}>BEST</th><th style={th}>WORST</th>
              </tr>
            </thead>
            <tbody>
              {signalStats.map((s) => (
                <tr key={s.signal} data-testid={`signal-stat-${s.signal}`}
                    style={{ borderTop: hairline, opacity: s.n === 0 ? 0.4 : 1 }}>
                  <td style={{ ...td, color: accent }}>{s.signal.replace(/_/g, " ").toUpperCase()}</td>
                  <td style={td}>
                    {s.n}
                    {s.n_30d > 0 && <span style={{ color: muted, fontSize: 10, marginLeft: 4 }}>
                      ({s.n_30d}×30d)
                    </span>}
                  </td>
                  <td style={{
                    ...td,
                    color: s.win_rate == null ? muted : s.win_rate >= 0.65 ? "#4ade80" : s.win_rate < 0.40 ? "#f87171" : accent,
                  }}>{s.win_rate != null ? `${(s.win_rate * 100).toFixed(0)}%` : "—"}</td>
                  <td style={{ ...td, color: pctColor(s.avg_live) }}>{fmt(s.avg_live)}%</td>
                  <td style={{ ...td, color: pctColor(s.avg_30d) }}>{fmt(s.avg_30d)}%</td>
                  <td style={{ ...td, color: s.best != null ? "#4ade80" : muted }}>
                    {s.best != null ? `+${s.best}%` : "—"}
                  </td>
                  <td style={{ ...td, color: s.worst != null ? "#f87171" : muted }}>
                    {s.worst != null ? `${s.worst}%` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title={`SIGNAL COMBO PERFORMANCE — ${combos.length} TRACKED COMBINATIONS`}>
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
const btnGhost = {
  background: "transparent", border: `0.5px solid ${dim}`, color: muted,
  fontSize: 12, padding: "8px 16px", cursor: "pointer",
  letterSpacing: "0.12em", fontFamily: "Courier New",
};
