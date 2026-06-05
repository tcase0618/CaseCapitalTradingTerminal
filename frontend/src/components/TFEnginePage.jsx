import { useEffect, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const PHASE_LABEL = {
  pre_adjustment:           "PRE-ADJUSTMENT (<5 TRADES)",
  signal_weight_adjustment: "SIGNAL-WEIGHT PHASE (5-29 TRADES)",
  full_adjustment:          "FULL PHASE (≥30 TRADES)",
};

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };

export default function TFEnginePage() {
  const [status, setStatus] = useState(null);
  const [combos, setCombos] = useState([]);

  useEffect(() => {
    axios.get(`${API}/trade_floor/engine/status`).then(r => setStatus(r.data));
    axios.get(`${API}/trade_floor/engine/combos`).then(r => setCombos(r.data.combos || []));
  }, []);

  const recalibrate = async () => {
    const r = await axios.post(`${API}/trade_floor/engine/recalibrate`);
    alert(`Recalibration · ${r.data.phase} · ${r.data.changes} changes`);
  };

  if (!status) return <CrtShell title="TRADE FLOOR ENGINE"><div style={{ color: muted }}>Loading...</div></CrtShell>;

  const w = status.weights || {};

  return (
    <CrtShell title="TRADE FLOOR ENGINE"
      headerRight={
        <button onClick={recalibrate}
          style={{
            background: "transparent", border: `0.5px solid ${accent}`, color: accent,
            fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em",
            fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>[ ▶ FORCE RECALIBRATE ]</button>
      }>
      <div style={{ padding: "14px 18px", border: `0.5px solid ${accent2}`,
                     background: `${accent2}10`, color: accent2, fontSize: 11,
                     letterSpacing: "0.1em", marginBottom: 16 }}>
        ⓘ This is a separate engine forked from the Signal Engine at startup.
        Learns EXCLUSIVELY from real Trade Floor executions, never from passive scan observations.
      </div>

      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PHASE" value={PHASE_LABEL[status.phase] || status.phase}
              sub="ADJUSTMENT LEVEL" color={accent} accentBar />
        <Stat label="CLOSED TRADES" value={status.closed_trades} sub="EXECUTIONS" color={accent2} />
        <Stat label="COMBOS · DATA" value={status.combos_with_data}
              sub={`${status.inherited_weight_count - status.combos_with_data} INHERITED`} color={labelLight} />
        <Stat label="NEXT RECAL" value={`${status.days_until_next_recalibration}d`} sub="WEEKLY" color="#4ade80" />
      </div>

      <Card title="WEIGHTS · INHERITED FROM SIGNAL ENGINE">
        <table style={{ width: "100%", borderCollapse: "collapse" }}>
          <thead><tr><th style={th}>SIGNAL</th><th style={th}>WEIGHT</th></tr></thead>
          <tbody>{Object.entries(w).sort((a, b) => b[1] - a[1]).map(([k, v]) => (
            <tr key={k} style={{ borderTop: hairline }}>
              <td style={{ ...td, color: accent2, fontWeight: 700 }}>{k}</td>
              <td style={{ ...td, color: accent, fontWeight: 700, fontFamily: "JetBrains Mono" }}>{v.toFixed(3)}</td>
            </tr>))}</tbody>
        </table>
      </Card>

      <Card title="COMBO PERFORMANCE · BUILT FROM REAL EXECUTIONS ONLY">
        {!combos.length ? (
          <div style={{ color: muted, padding: 20 }}>
            No combo performance data yet — populates after the first 5 closed trades.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th style={th}>COMBO</th><th style={th}>N</th>
                <th style={th}>WIN RATE</th><th style={th}>AVG RETURN</th></tr></thead>
            <tbody>{combos.map((c, i) => (
              <tr key={i} style={{ borderTop: hairline }}>
                <td style={td}>{(c.combo || []).join(" · ")}</td>
                <td style={td}>{c.n}</td>
                <td style={{ ...td, color: c.win_rate >= 0.5 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                  {(c.win_rate * 100).toFixed(0)}%
                </td>
                <td style={{ ...td, color: c.avg_return_pct >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                  {c.avg_return_pct >= 0 ? "+" : ""}{c.avg_return_pct}%
                </td>
              </tr>))}</tbody>
          </table>
        )}
      </Card>
    </CrtShell>
  );
}
