import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const PHASE_LABEL = {
  pre_adjustment: "PRE-ADJUSTMENT",
  signal_weight_adjustment: "SIGNAL-WEIGHT PHASE",
  full_adjustment: "FULL PHASE",
};

export default function TFEnginePage() {
  const [status, setStatus] = useState(null);
  const [combos, setCombos] = useState([]);
  const [busy, setBusy] = useState(false);

  const load = async () => {
    const [s, c] = await Promise.all([
      axios.get(`${API}/trade_floor/engine/status`),
      axios.get(`${API}/trade_floor/engine/combos`),
    ]);
    setStatus(s.data);
    setCombos(c.data.combos || []);
  };

  useEffect(() => { load().catch(() => {}); }, []);

  const recalibrate = async () => {
    setBusy(true);
    try {
      await axios.post(`${API}/trade_floor/engine/recalibrate`);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const sortedWeights = useMemo(() => Object.entries(status?.weights || {}).sort((a, b) => b[1] - a[1]), [status]);
  const topCombos = useMemo(() => [...combos].sort((a, b) => (b.avg_return_pct || 0) - (a.avg_return_pct || 0)).slice(0, 6), [combos]);
  const phaseProgress = Math.min(100, ((status?.closed_trades || 0) / 30) * 100);

  if (!status) return <CrtShell title="TRADE FLOOR ENGINE"><div style={{ color: muted }}>Loading engine telemetry...</div></CrtShell>;

  return (
    <CrtShell
      title="TRADE FLOOR ENGINE"
      headerRight={<button onClick={recalibrate} disabled={busy} style={buttonStyle(accent)}>[ {busy ? "RECALIBRATING" : "FORCE RECALIBRATE"} ]</button>}
    >
      <div style={engineHero}>
        <div>
          <div style={eyebrow}>EXECUTION-ONLY LEARNING CORE</div>
          <div style={{ color: accent, fontSize: 34, fontWeight: 900, letterSpacing: "0.08em" }}>
            {PHASE_LABEL[status.phase] || status.phase}
          </div>
          <p style={heroCopy}>
            This engine learns only from real Trade Floor executions. Passive scan observations never overwrite it.
          </p>
        </div>
        <div style={readinessPanel}>
          <SmallLine k="Closed Trades" v={status.closed_trades} />
          <SmallLine k="Combos With Data" v={status.combos_with_data} />
          <SmallLine k="Inherited Weights" v={status.inherited_weight_count} />
          <SmallLine k="Next Recal" v={`${status.days_until_next_recalibration}D`} />
          <div style={barTrack}><div style={{ ...barFill, width: `${phaseProgress}%`, background: accent }} /></div>
        </div>
      </div>

      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PHASE" value={PHASE_LABEL[status.phase] || status.phase} sub="ADJUSTMENT LEVEL" color={accent} accentBar />
        <Stat label="CLOSED TRADES" value={status.closed_trades} sub="EXECUTIONS" color={accent2} />
        <Stat label="COMBOS DATA" value={status.combos_with_data} sub={`${Math.max(0, (status.inherited_weight_count || 0) - (status.combos_with_data || 0))} INHERITED`} color={labelLight} />
        <Stat label="NEXT RECAL" value={`${status.days_until_next_recalibration}d`} sub="WEEKLY" color="#4ade80" />
      </div>

      <div style={gridTwo}>
        <Card title="SIGNAL WEIGHT BOARD" accentColor={accent2}>
          <div style={{ display: "grid", gap: 9 }}>
            {sortedWeights.map(([k, v]) => (
              <WeightRow key={k} label={k} value={v} />
            ))}
          </div>
        </Card>

        <Card title="COMBO EDGE BOARD" accentColor="#4ade80">
          {!topCombos.length ? (
            <div style={{ color: muted, padding: 20 }}>No combo performance data yet. Populates after the first 5 closed trades.</div>
          ) : (
            <div style={{ display: "grid", gap: 9 }}>
              {topCombos.map((c, i) => <ComboCard key={`${i}-${(c.combo || []).join("-")}`} combo={c} />)}
            </div>
          )}
        </Card>
      </div>

      <Card title="ENGINE SAFETY CONTRACT" accentColor="#fbbf24">
        <div style={safetyGrid}>
          <Safety label="PM Owns Exits" value="Trade Floor cannot overwrite PM stops or ratchets." />
          <Safety label="Execution Only" value="Learning is based on real filled trades, not scan fantasy P/L." />
          <Safety label="Phase Gated" value="Full adjustment waits until enough closed trades exist." />
          <Safety label="Weekly Cadence" value="Recalibration is staged, not twitchy intraday curve fitting." />
        </div>
      </Card>
    </CrtShell>
  );
}

function WeightRow({ label, value }) {
  const pct = Math.min(100, Math.max(3, Number(value || 0) * 100));
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 11 }}>
        <span style={{ color: accent2, fontWeight: 800 }}>{label}</span>
        <span style={{ color: accent, fontWeight: 800 }}>{Number(value || 0).toFixed(3)}</span>
      </div>
      <div style={barTrack}><div style={{ ...barFill, width: `${pct}%`, background: accent2 }} /></div>
    </div>
  );
}

function ComboCard({ combo }) {
  const win = (combo.win_rate || 0) * 100;
  const ret = Number(combo.avg_return_pct || 0);
  const color = ret >= 0 ? "#4ade80" : "#f87171";
  return (
    <div style={{ border: `0.5px solid ${color}55`, background: `${color}0c`, padding: "10px 12px" }}>
      <div style={{ color: labelLight, lineHeight: 1.4 }}>{(combo.combo || []).join(" + ") || "UNKNOWN COMBO"}</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
        <span style={pill(accent)}>N {combo.n}</span>
        <span style={pill(win >= 50 ? "#4ade80" : "#f87171")}>{win.toFixed(0)}% WIN</span>
        <span style={pill(color)}>{ret >= 0 ? "+" : ""}{ret}% AVG</span>
      </div>
    </div>
  );
}

function Safety({ label, value }) {
  return (
    <div style={{ border: hairline, background: "rgba(255,255,255,0.018)", padding: 12 }}>
      <div style={{ color: accent, fontSize: 11, letterSpacing: "0.12em", fontWeight: 900 }}>{label}</div>
      <div style={{ color: labelLight, fontSize: 12, lineHeight: 1.45, marginTop: 7 }}>{value}</div>
    </div>
  );
}

function SmallLine({ k, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, borderBottom: hairline, padding: "7px 0", fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em" }}>{k}</span>
      <span style={{ color: labelLight, textAlign: "right" }}>{v}</span>
    </div>
  );
}

const engineHero = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(260px, 0.45fr)", gap: 18, border: hairline, borderTop: `1px solid ${accent}`, background: "linear-gradient(135deg, rgba(200,168,75,0.11), rgba(94,234,212,0.04))", padding: 20, marginBottom: 22 };
const readinessPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "10px 14px", alignSelf: "start" };
const gridTwo = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 0.9fr)", gap: 18 };
const safetyGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 };
const eyebrow = { color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 800, marginBottom: 8 };
const heroCopy = { color: labelLight, lineHeight: 1.55, margin: "12px 0 0", maxWidth: 780 };
const barTrack = { height: 5, background: "rgba(255,255,255,0.06)", marginTop: 7, overflow: "hidden" };
const barFill = { height: "100%", boxShadow: "0 0 10px rgba(200,168,75,0.45)" };
function pill(color) {
  return { color, border: `0.5px solid ${color}66`, background: `${color}0d`, padding: "3px 7px", fontSize: 10, letterSpacing: "0.1em", fontWeight: 800 };
}
function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}
