import { useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";
import useDisplayResource from "../hooks/useDisplayResource";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;
const record = value => value && typeof value === "object" && !Array.isArray(value);
const num = value => (typeof value === "number" || (typeof value === "string" && value.trim())) && Number.isFinite(Number(value)) ? Number(value) : null;
const fmt = (value, suffix = "", digits) => num(value) == null ? "-" : `${digits == null ? num(value) : num(value).toFixed(digits)}${suffix}`;

const PHASE_LABEL = {
  pre_adjustment: "PRE-ADJUSTMENT",
  signal_weight_adjustment: "SIGNAL-WEIGHT PHASE",
  full_adjustment: "FULL PHASE",
};

export default function TFEnginePage() {
  const statusRead = useDisplayResource(`${API}/trade_floor/engine/status`, 60000);
  const combosRead = useDisplayResource(`${API}/trade_floor/engine/combos`, 60000);
  const status = record(statusRead.data) ? statusRead.data : null;
  const combos = Array.isArray(combosRead.data?.combos) ? combosRead.data.combos.filter(row => record(row) && Array.isArray(row.combo)).map(row => ({ ...row, combo: row.combo.filter(x => typeof x === "string") })) : [];
  const [busy, setBusy] = useState(false);
  const [recalError, setRecalError] = useState("");

  const load = async () => {
    await Promise.all([statusRead.refresh(), combosRead.refresh()]);
  };


  const recalibrate = async () => {
    setBusy(true);
    setRecalError("");
    try {
      await axios.post(`${API}/trade_floor/engine/recalibrate`);
      await load();
    } catch {
      setRecalError("Recalibration request failed. Engine state has not been confirmed refreshed.");
    } finally {
      setBusy(false);
    }
  };

  const sortedWeights = useMemo(() => Object.entries(record(status?.weights) ? status.weights : {}).sort((a, b) => (num(b[1]) ?? -Infinity) - (num(a[1]) ?? -Infinity)), [status]);
  const topCombos = useMemo(() => [...combos].sort((a, b) => (num(b.avg_return_pct) ?? -Infinity) - (num(a.avg_return_pct) ?? -Infinity)).slice(0, 6), [combos]);
  const phaseProgress = num(status?.closed_trades) == null ? null : Math.min(100, Math.max(0, (num(status.closed_trades) / 30) * 100));
  const incomplete = !record(status?.weights) || sortedWeights.some(([, v]) => num(v) == null) || !Array.isArray(combosRead.data?.combos) || combos.length !== combosRead.data.combos.length || combos.some(c => c.combo.length === 0 || [c.n, c.win_rate, c.avg_return_pct].some(v => num(v) == null)) || [status?.closed_trades, status?.combos_with_data, status?.inherited_weight_count, status?.days_until_next_recalibration].some(v => num(v) == null);

  if (!status) return <CrtShell title="TRADE FLOOR ENGINE"><div role="status" style={{ color: muted }}>{statusRead.error || statusRead.data != null ? "Engine telemetry unavailable." : "Loading engine telemetry..."}</div></CrtShell>;

  return (
    <CrtShell
      title="TRADE FLOOR ENGINE"
      headerRight={<button onClick={recalibrate} disabled={busy} style={buttonStyle(accent)}>[ {busy ? "RECALIBRATING" : "FORCE RECALIBRATE"} ]</button>}
    >
      <div role="status" style={{ color: muted, marginBottom: 12 }}>Legacy Alpaca equity telemetry. {status.applied === true ? "Reported applied." : status.applied === false ? "Shadow output, not applied." : "Application state unverified."}</div>
      {recalError && <div role="alert" style={{ color: "#f87171", marginBottom: 12 }}>{recalError}</div>}
      {(statusRead.error || combosRead.error || incomplete) && <div role="status" style={{ color: "#fbbf24", marginBottom: 12 }}>Engine display incomplete or stale. Missing results are not zero performance.</div>}
      <div style={engineHero}>
        <div>
          <div style={eyebrow}>EXECUTION-ONLY LEARNING CORE</div>
          <div style={{ color: accent, fontSize: 34, fontWeight: 900, letterSpacing: "0.08em" }}>
            {PHASE_LABEL[status.phase] || (typeof status.phase === "string" ? status.phase : "UNKNOWN")}
          </div>
          <p style={heroCopy}>
            This engine learns only from real Trade Floor executions. Passive scan observations never overwrite it.
          </p>
        </div>
        <div style={readinessPanel}>
          <SmallLine k="Closed Trades" v={fmt(status.closed_trades)} />
          <SmallLine k="Combos With Data" v={fmt(status.combos_with_data)} />
          <SmallLine k="Inherited Weights" v={fmt(status.inherited_weight_count)} />
          <SmallLine k="Next Recal" v={fmt(status.days_until_next_recalibration, "D")} />
          {phaseProgress != null && <div style={barTrack}><div style={{ ...barFill, width: `${phaseProgress}%`, background: accent }} /></div>}
        </div>
      </div>

      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PHASE" value={PHASE_LABEL[status.phase] || (typeof status.phase === "string" ? status.phase : "UNKNOWN")} sub="ADJUSTMENT LEVEL" color={accent} accentBar />
        <Stat label="CLOSED TRADES" value={fmt(status.closed_trades)} sub="EXECUTIONS" color={accent2} />
        <Stat label="COMBOS DATA" value={fmt(status.combos_with_data)} sub={`${num(status.inherited_weight_count) == null || num(status.combos_with_data) == null ? "-" : Math.max(0, num(status.inherited_weight_count) - num(status.combos_with_data))} INHERITED`} color={labelLight} />
        <Stat label="NEXT RECAL" value={fmt(status.days_until_next_recalibration, "d")} sub="WEEKLY" color="#4ade80" />
      </div>

      <div style={gridTwo}>
        <Card title="SIGNAL WEIGHT BOARD" accentColor={accent2}>
          <div style={{ display: "grid", gap: 9 }}>
            {!sortedWeights.length && <div style={{ color: muted }}>No signal weights available.</div>}
            {sortedWeights.map(([k, v]) => (
              <WeightRow key={k} label={k} value={v} />
            ))}
          </div>
        </Card>

        <Card title="COMBO EDGE BOARD" accentColor="#4ade80">
          {!topCombos.length ? (
            <div style={{ color: muted, padding: 20 }}>{combosRead.error || !Array.isArray(combosRead.data?.combos) ? "Combo performance unavailable." : "No valid combo performance rows returned."}</div>
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
  const pct = num(value) == null ? null : Math.min(100, Math.max(0, num(value) * 100));
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, fontSize: 11 }}>
        <span style={{ color: accent2, fontWeight: 800 }}>{label}</span>
        <span style={{ color: accent, fontWeight: 800 }}>{fmt(value, "", 3)}</span>
      </div>
      {pct != null && <div style={barTrack}><div style={{ ...barFill, width: `${pct}%`, background: accent2 }} /></div>}
    </div>
  );
}

function ComboCard({ combo }) {
  const win = num(combo.win_rate) == null ? null : num(combo.win_rate) * 100;
  const ret = num(combo.avg_return_pct);
  const color = ret == null ? muted : ret >= 0 ? "#4ade80" : "#f87171";
  return (
    <div style={{ border: `0.5px solid ${color}55`, background: `${color}0c`, padding: "10px 12px" }}>
      <div style={{ color: labelLight, lineHeight: 1.4 }}>{(combo.combo || []).join(" + ") || "UNKNOWN COMBO"}</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 8 }}>
        <span style={pill(accent)}>N {fmt(combo.n)}</span>
        <span style={pill(win == null ? muted : win >= 50 ? "#4ade80" : "#f87171")}>{fmt(win, "%", 0)} WIN</span>
        <span style={pill(color)}>{ret == null ? "-" : `${ret >= 0 ? "+" : ""}${ret}%`} AVG</span>
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

const engineHero = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))", gap: 18, border: hairline, borderTop: `1px solid ${accent}`, background: "linear-gradient(135deg, rgba(200,168,75,0.11), rgba(94,234,212,0.04))", padding: 20, marginBottom: 22 };
const readinessPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "10px 14px", alignSelf: "start" };
const gridTwo = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 18 };
const safetyGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 10 };
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
