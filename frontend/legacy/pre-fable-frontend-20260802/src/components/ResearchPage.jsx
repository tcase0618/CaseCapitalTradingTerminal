import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;
const green = "#4ade80";
const red = "#f87171";
const amber = "#fbbf24";
const violet = "#a78bfa";

export default function ResearchPage() {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState("STRATEGY");
  const [selectedExperiment, setSelectedExperiment] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lastSync, setLastSync] = useState(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await axios.get(`${API}/research/dashboard?limit_scans=180`, { timeout: 15000 });
      setData(r.data);
      setLastSync(new Date().toISOString());
    } catch (e) {
      setData({ ok: false, error: e.message, stats: {}, strategy_blueprints: [], signal_lab: [], promotion_gates: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 60 * 60 * 1000);
    return () => clearInterval(id);
  }, [refresh]);

  const experiments = useMemo(() => data?.strategy_blueprints || [], [data?.strategy_blueprints]);
  useEffect(() => {
    if (!selectedExperiment && experiments.length) setSelectedExperiment(experiments[0].id);
  }, [experiments, selectedExperiment]);

  const selected = experiments.find(e => e.id === selectedExperiment) || experiments[0] || null;
  const stats = data?.stats || {};
  const qlib = data?.qlib || {};
  const gates = data?.promotion_gates || [];
  const sourceMap = data?.source_map || {};
  const experimentChart = useMemo(() => experiments.map(e => ({
    name: e.name.replace("Qlib ", "").slice(0, 16),
    readiness: e.readiness,
    color: experimentColor(e.readiness),
  })), [experiments]);

  return (
    <CrtShell
      title="R&D - QLIB STRATEGY LAB"
      headerRight={
        <button onClick={refresh} disabled={loading} style={buttonStyle(accent2)}>
          [ {loading ? "SYNCING..." : "RUN LAB SYNC"} ]
        </button>
      }
    >
      <div style={statRow}>
        <Stat label="QLIB ADAPTER" value={qlib.installed ? "LIVE" : "READY"} sub={qlib.version || "RUNTIME OPTIONAL"} color={qlib.installed ? green : amber} accentBar />
        <Stat label="DECISIONS" value={stats.reconstructed_decisions || 0} sub="RECONSTRUCTED" color={accent} />
        <Stat label="MATURED" value={stats.matured_outcomes || 0} sub={`${stats.coverage_pct || 0}% COVERAGE`} color={accent2} />
        <Stat label="LAB WIN RATE" value={fmtPct(stats.lab_win_rate)} sub="ACTION AVG" color={rateColor(stats.lab_win_rate)} />
        <Stat label="EXPERIMENTS" value={stats.active_experiments || 0} sub="READ ONLY" color={violet} />
        <Stat label="LSE" value={sourceMap.lse?.ok ? "LIVE" : "DEGRADED"} sub="PRIMARY DATA" color={sourceMap.lse?.ok ? green : amber} />
      </div>

      <div style={tabBar}>
        {["STRATEGY", "SIGNALS", "QLIB PIPELINE", "PROMOTION", "CHALLENGERS"].map(t => (
          <button key={t} onClick={() => setTab(t)} style={tabButton(tab === t)}>{t}</button>
        ))}
      </div>

      {tab === "STRATEGY" && (
        <>
          <div style={readinessGrid}>
            <Card title="EXPERIMENT READINESS" accentColor={violet}>
              <div style={{ height: 248 }}>
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={experimentChart} margin={{ top: 10, right: 8, left: -20, bottom: 18 }}>
                    <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
                    <XAxis dataKey="name" stroke={muted} tick={{ fontSize: 9 }} interval={0} angle={-18} textAnchor="end" height={54} />
                    <YAxis stroke={muted} tick={{ fontSize: 10 }} domain={[0, 100]} />
                    <Tooltip content={<ResearchTooltip />} />
                    <Bar dataKey="readiness" radius={[2, 2, 0, 0]}>
                      {experimentChart.map((e, i) => <Cell key={i} fill={e.color} />)}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            </Card>

            <Card title="LIVE RESEARCH STATE" accentColor={accent2}>
              <div style={controlPanel}>
                <SmallLine k="Mode" v={data?.mode || "read_only_research"} />
                <SmallLine k="Latest scan" v={sourceMap.latest_scan_at ? String(sourceMap.latest_scan_at).slice(0, 19) : "-"} />
                <SmallLine k="EdgarTools" v={sourceMap.edgartools?.ok ? "LIVE" : "FALLBACK"} />
                <SmallLine k="Kronos conflicts" v={sourceMap.kronos_disagreements ?? 0} />
                <SmallLine k="Last sync" v={lastSync ? new Date(lastSync).toLocaleTimeString() : "-"} />
                <SmallLine k="Scheduler" v="HOURLY" color={green} />
              </div>
            </Card>
          </div>

          <div style={experimentGrid}>
            <Card title="RESEARCH QUEUE" accentColor={accent2}>
              <div style={queueGrid}>
                {experiments.map(exp => (
                  <button key={exp.id} onClick={() => setSelectedExperiment(exp.id)} style={queueCard(selected?.id === exp.id, exp.readiness)}>
                    <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                      <span style={{ color: experimentColor(exp.readiness), fontWeight: 900 }}>{exp.sleeve}</span>
                      <span style={pill(experimentColor(exp.readiness))}>{exp.readiness}/100</span>
                    </div>
                    <strong style={{ color: labelLight, fontSize: 15 }}>{exp.name}</strong>
                    <span style={{ color: muted, lineHeight: 1.45 }}>{exp.hypothesis}</span>
                  </button>
                ))}
              </div>
            </Card>

            <Card title="SELECTED EXPERIMENT CARD" accentColor={selected ? experimentColor(selected.readiness) : accent}>
              {selected ? (
                <div style={selectedGrid}>
                  <div>
                    <div style={eyebrow}>{selected.sleeve} / {selected.status}</div>
                    <div style={selectedTitle}>{selected.name}</div>
                    <p style={copy}>{selected.hypothesis}</p>
                    <div style={inputGrid}>
                      {(selected.inputs || []).map(i => <Badge key={i} color={accent2}>{i}</Badge>)}
                    </div>
                  </div>
                  <div style={controlPanel}>
                    <SmallLine k="Readiness" v={`${selected.readiness}/100`} color={experimentColor(selected.readiness)} />
                    <SmallLine k="Sample Anchor" v={selected.sample_anchor || "-"} />
                    <SmallLine k="Output" v={selected.output} />
                    <SmallLine k="Risk" v={selected.risk} />
                    <div style={goNoGo(selected.readiness)}>
                      {selected.readiness >= 70 ? "RESEARCH GO" : selected.readiness >= 45 ? "COLLECT MORE DATA" : "EARLY STAGE"}
                    </div>
                  </div>
                </div>
              ) : <Empty text="No research experiments loaded." />}
            </Card>
          </div>
        </>
      )}

      {tab === "SIGNALS" && (
        <div style={twoCol}>
          <Card title="SIGNAL FACTOR LAB" accentColor={accent2}>
            <LabTable rows={data?.signal_lab || []} />
          </Card>
          <Card title="ACTION / OPTIONS SLEEVES" accentColor={accent}>
            <div style={{ display: "grid", gap: 18 }}>
              <MiniChart title="PM ACTION OUTCOMES" rows={data?.action_lab || []} color={accent} />
              <LabTable rows={data?.option_lab || []} compact />
            </div>
          </Card>
          <Card title="SECTOR TRANSFER TESTS" accentColor={violet}>
            <LabTable rows={data?.sector_lab || []} />
          </Card>
          <Card title="LATEST TRAINING UNIVERSE" accentColor={amber}>
            <CandidateTape rows={data?.latest_candidates || []} />
          </Card>
        </div>
      )}

      {tab === "QLIB PIPELINE" && (
        <div style={pipelineLayout}>
          <Card title="QLIB RESEARCH PIPELINE" accentColor={accent}>
            <div style={pipeline}>
              {(data?.qlib_pipeline || []).map((p, i) => (
                <div key={p.stage} style={pipeStage}>
                  <div style={pipeIndex}>{String(i + 1).padStart(2, "0")}</div>
                  <div>
                    <strong style={{ color: labelLight, letterSpacing: "0.12em" }}>{p.stage}</strong>
                    <p style={{ ...copy, margin: "7px 0 0" }}>{p.detail}</p>
                  </div>
                </div>
              ))}
            </div>
          </Card>
          <Card title="FEATURE STORE MAP" accentColor={accent2}>
            <FeatureMap />
          </Card>
        </div>
      )}

      {tab === "PROMOTION" && (
        <div style={twoColWide}>
          <Card title="PROMOTION GATES" accentColor={accent}>
            <div style={gateGrid}>
              {gates.map(g => (
                <div key={g.name} style={gateCard(g.ok)}>
                  <span style={{ color: g.ok ? green : amber, fontSize: 18, fontWeight: 900 }}>{g.ok ? "PASS" : "HOLD"}</span>
                  <strong style={{ color: labelLight }}>{g.name}</strong>
                  <span style={{ color: muted, lineHeight: 1.45 }}>{g.detail}</span>
                </div>
              ))}
            </div>
          </Card>
          <Card title="MODEL GOVERNANCE" accentColor={red}>
            <GovernanceChecklist />
          </Card>
        </div>
      )}

      {tab === "CHALLENGERS" && (
        <div style={twoColWide}>
          <Card title="KRONOS / PM DISAGREEMENT TRACKER" accentColor={violet}>
            <DisagreementTable rows={data?.disagreements || []} />
          </Card>
          <Card title="CHALLENGER EQUITY CURVE MOCK" accentColor={accent2}>
            <EquityCurve rows={data?.action_lab || []} />
          </Card>
        </div>
      )}
    </CrtShell>
  );
}

function LabTable({ rows, compact }) {
  if (!rows?.length) return <Empty text="No matured research rows yet." />;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead>
        <tr>
          <th style={th}>FACTOR</th>
          <th style={th}>SAMPLES</th>
          <th style={th}>WIN</th>
          <th style={th}>AVG</th>
          {!compact && <th style={th}>COVERAGE</th>}
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.key} style={{ borderTop: hairline }}>
            <td style={{ ...td, color: accent, fontWeight: 900 }}>{r.key}</td>
            <td style={td}>{r.samples}</td>
            <td style={{ ...td, color: rateColor(r.win_rate) }}>{fmtPct(r.win_rate)}</td>
            <td style={{ ...td, color: returnColor(r.avg_return), fontWeight: 900 }}>{signedPct(r.avg_return)}</td>
            {!compact && <td style={td}>{fmtPct(r.coverage_pct)}</td>}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function MiniChart({ title, rows, color }) {
  const chartRows = (rows || []).slice(0, 8).map(r => ({ name: r.key, avg: r.avg_return || 0 }));
  return (
    <div>
      <div style={panelTitle}>{title}</div>
      <div style={{ height: 180 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartRows} margin={{ top: 5, right: 8, left: -20, bottom: 30 }}>
            <CartesianGrid stroke="rgba(255,255,255,0.06)" vertical={false} />
            <XAxis dataKey="name" stroke={muted} tick={{ fontSize: 9 }} angle={-20} textAnchor="end" interval={0} />
            <YAxis stroke={muted} tick={{ fontSize: 10 }} />
            <Tooltip content={<ResearchTooltip />} />
            <Bar dataKey="avg" fill={color}>
              {chartRows.map((r, i) => <Cell key={i} fill={returnColor(r.avg)} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function CandidateTape({ rows }) {
  if (!rows?.length) return <Empty text="No latest scanner candidates loaded." />;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      {rows.slice(0, 14).map(r => (
        <div key={`${r.ticker}-${r.action}`} style={candidateRow}>
          <span style={{ color: accent, fontWeight: 900 }}>${r.ticker}</span>
          <span style={pill(r.action === "ACCUMULATE" ? green : r.action === "STARTER" ? accent2 : amber)}>{r.action}</span>
          <span style={{ color: labelLight }}>{r.pm_score}</span>
          <span style={{ color: muted }}>{r.option_view}</span>
          <span style={{ color: r.research_tag === "train" ? green : amber }}>{r.research_tag}</span>
        </div>
      ))}
    </div>
  );
}

function DisagreementTable({ rows }) {
  if (!rows?.length) return <Empty text="No Kronos/PM disagreement records yet." />;
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead><tr><th style={th}>TICKER</th><th style={th}>PM</th><th style={th}>KRONOS</th><th style={th}>SCORE</th><th style={th}>DATE</th></tr></thead>
      <tbody>
        {rows.slice(0, 18).map((r, i) => (
          <tr key={`${r.ticker}-${i}`} style={{ borderTop: hairline }}>
            <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.ticker || "-"}</td>
            <td style={td}>{r.pm_action || r.pm_route || "-"}</td>
            <td style={{ ...td, color: forecastColor(r.kronos_bias || r.forecast_bias) }}>{r.kronos_bias || r.forecast_bias || "-"}</td>
            <td style={td}>{r.kronos_score ?? "-"}</td>
            <td style={{ ...td, color: muted }}>{String(r.generated_at || r.created_at || "-").slice(0, 10)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function EquityCurve({ rows }) {
  const curve = [];
  let value = 100;
  (rows || []).slice().reverse().forEach((r, i) => {
    value *= 1 + ((r.avg_return || 0) / 1000);
    curve.push({ step: i + 1, lab: Number(value.toFixed(2)), pm: Number((100 + i * 0.18).toFixed(2)) });
  });
  return (
    <div style={{ height: 360 }}>
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={curve}>
          <CartesianGrid stroke="rgba(255,255,255,0.06)" />
          <XAxis dataKey="step" stroke={muted} tick={{ fontSize: 10 }} />
          <YAxis stroke={muted} tick={{ fontSize: 10 }} domain={["dataMin - 2", "dataMax + 2"]} />
          <Tooltip content={<ResearchTooltip />} />
          <Line dataKey="lab" stroke={accent2} strokeWidth={3} dot={false} />
          <Line dataKey="pm" stroke={accent} strokeWidth={2} dot={false} strokeDasharray="5 5" />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}

function FeatureMap() {
  const rows = [
    ["Market Tape", "LSE candles, returns, realized volatility, SPY benchmark"],
    ["Terminal Signals", "scanner flags, X factor, dark horse, narrative lock, lottery"],
    ["PM Context", "route, action, allocation, RR, ratchet profile, rejection reason"],
    ["SEC Context", "EdgarTools company file, filing type, insider cluster, risk language"],
    ["Options Context", "Alpaca snapshots, LSE flow, IV, spread, theta, fills"],
    ["Macro Context", "GDP, PMI, CPI, rates, yields, labor, retail sales"],
    ["Outcomes", "7D/30D/90D return, paper fills, realized/unrealized P&L"],
  ];
  return <div style={{ display: "grid", gap: 10 }}>{rows.map(([k, v]) => <SmallLine key={k} k={k} v={v} />)}</div>;
}

function GovernanceChecklist() {
  const rows = [
    ["No live execution", "R&D outputs cannot submit orders."],
    ["Out-of-sample first", "No strategy gets promoted on in-sample fit alone."],
    ["Minimum evidence", "Promotion requires sample and matured-return thresholds."],
    ["Drift monitor", "Promoted weights degrade back to watch if edge fades."],
    ["Audit trail", "Every promoted change needs rationale and before/after metrics."],
    ["PM authority", "PM remains final routing authority."],
  ];
  return <div style={{ display: "grid", gap: 10 }}>{rows.map(([k, v]) => <SmallLine key={k} k={k} v={v} color={accent2} />)}</div>;
}

function ResearchTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  return (
    <div style={tooltip}>
      <div style={{ color: accent, marginBottom: 4 }}>{label}</div>
      {payload.map(p => <div key={p.dataKey} style={{ color: p.color || labelLight }}>{p.name || p.dataKey}: {p.value}</div>)}
    </div>
  );
}

function SmallLine({ k, v, color }) {
  return (
    <div style={smallLine}>
      <span style={{ color: dim, letterSpacing: "0.13em" }}>{k}</span>
      <span style={{ color: color || labelLight, textAlign: "right" }}>{v ?? "-"}</span>
    </div>
  );
}

function Badge({ color, children }) {
  return <span style={pill(color)}>{children}</span>;
}

function Empty({ text }) {
  return <div style={{ color: muted, padding: 18, border: hairline, background: "rgba(255,255,255,0.014)" }}>{text}</div>;
}

function fmtPct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  return `${Number(v).toFixed(1)}%`;
}

function signedPct(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "-";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}%`;
}

function rateColor(v) {
  if (v === null || v === undefined) return muted;
  if (Number(v) >= 58) return green;
  if (Number(v) >= 48) return amber;
  return red;
}

function returnColor(v) {
  if (v === null || v === undefined) return muted;
  if (Number(v) > 1) return green;
  if (Number(v) >= -1) return amber;
  return red;
}

function forecastColor(v) {
  const s = String(v || "").toUpperCase();
  if (s.includes("BULL") || s.includes("UP")) return green;
  if (s.includes("BEAR") || s.includes("DOWN")) return red;
  return amber;
}

function experimentColor(v) {
  if (Number(v) >= 70) return green;
  if (Number(v) >= 45) return amber;
  return red;
}

function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "9px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 800 };
}

function tabButton(active) {
  return {
    background: active ? "rgba(200,168,75,0.14)" : "rgba(255,255,255,0.018)",
    border: `0.5px solid ${active ? accent : "rgba(255,255,255,0.08)"}`,
    color: active ? accent : labelLight,
    padding: "10px 13px",
    cursor: "pointer",
    fontFamily: "JetBrains Mono",
    fontSize: 11,
    letterSpacing: "0.13em",
    fontWeight: 900,
  };
}

function pill(color) {
  return { color, border: `0.5px solid ${color}66`, background: `${color}10`, padding: "4px 8px", fontSize: 10, letterSpacing: "0.11em", fontWeight: 900 };
}

function queueCard(active, readiness) {
  const color = experimentColor(readiness);
  return {
    border: `0.5px solid ${active ? color : "rgba(255,255,255,0.07)"}`,
    background: active ? `${color}10` : "rgba(255,255,255,0.016)",
    padding: 14,
    textAlign: "left",
    cursor: "pointer",
    minHeight: 150,
    display: "grid",
    gap: 10,
    fontFamily: "JetBrains Mono",
  };
}

function goNoGo(readiness) {
  const color = experimentColor(readiness);
  return { marginTop: 14, border: `0.5px solid ${color}66`, background: `${color}10`, color, padding: "12px 14px", fontWeight: 900, letterSpacing: "0.14em", textAlign: "center" };
}

function gateCard(ok) {
  const color = ok ? green : amber;
  return { border: `0.5px solid ${color}55`, background: `${color}08`, padding: 14, display: "grid", gap: 8, minHeight: 120 };
}

const statRow = { display: "flex", background: cardBg, border: hairline, marginBottom: 20, flexWrap: "wrap" };
const tabBar = { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 };
const readinessGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(320px, 0.8fr)", gap: 18, marginBottom: 18 };
const eyebrow = { color: accent2, fontSize: 10, letterSpacing: "0.18em", fontWeight: 900, marginBottom: 8 };
const selectedTitle = { color: accent, fontSize: 24, lineHeight: 1.2, fontWeight: 900, letterSpacing: "0.08em" };
const copy = { color: labelLight, lineHeight: 1.55, fontSize: 13 };
const controlPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: 14, minWidth: 0 };
const smallLine = { display: "flex", justifyContent: "space-between", gap: 14, borderBottom: hairline, padding: "8px 0", fontSize: 11 };
const experimentGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1fr)", gap: 18 };
const queueGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 };
const selectedGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(300px, 0.42fr)", gap: 18 };
const inputGrid = { display: "flex", flexWrap: "wrap", gap: 8, marginTop: 12 };
const twoCol = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(360px, 0.8fr)", gap: 18 };
const twoColWide = { display: "grid", gridTemplateColumns: "minmax(0, 1.15fr) minmax(360px, 0.85fr)", gap: 18 };
const pipelineLayout = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(360px, 0.8fr)", gap: 18 };
const pipeline = { display: "grid", gap: 12 };
const pipeStage = { display: "grid", gridTemplateColumns: "54px 1fr", gap: 12, border: hairline, background: "rgba(255,255,255,0.016)", padding: 14 };
const pipeIndex = { color: accent, fontSize: 22, fontWeight: 900, letterSpacing: "0.12em" };
const gateGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(210px, 1fr))", gap: 12 };
const candidateRow = { display: "grid", gridTemplateColumns: "70px 110px 60px minmax(0, 1fr) 80px", gap: 10, alignItems: "center", border: hairline, padding: "9px 10px", fontSize: 11 };
const panelTitle = { color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 900 };
const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 500, textAlign: "left" };
const td = { padding: "11px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12, verticalAlign: "top" };
const tooltip = { background: "#050509", border: hairline, padding: "8px 10px", color: labelLight, fontSize: 11 };
