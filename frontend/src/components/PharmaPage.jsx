import { Fragment, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Link } from "react-router-dom";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const TIER_COLOR = {
  STRONG: "#4ade80",
  WATCH: "#fbbf24",
  NEUTRAL: "#9ca3af",
  WEAK: "#6b7280",
  MANUAL: accent2,
};

function scoreColor(s) {
  if (s == null) return muted;
  if (s >= 80) return "#4ade80";
  if (s >= 65) return "#fbbf24";
  if (s >= 40) return labelLight;
  return muted;
}

export default function PharmaPage() {
  const [pdufa, setPdufa] = useState([]);
  const [active, setActive] = useState([]);
  const [track, setTrack] = useState({});
  const [freeIntel, setFreeIntel] = useState({});
  const [expanded, setExpanded] = useState(null);
  const [scanning, setScanning] = useState(false);

  const reload = () => {
    axios.get(`${API}/pharma/pdufa?days=90`).then(r => setPdufa(r.data.results || [])).catch(() => {});
    axios.get(`${API}/pharma/active`).then(r => setActive(r.data.plays || [])).catch(() => {});
    axios.get(`${API}/pharma/track_record`).then(r => setTrack(r.data || {})).catch(() => {});
  };

  useEffect(reload, []);

  useEffect(() => {
    const top = [...pdufa]
      .sort((a, b) => (b.binary_event_score || 0) - (a.binary_event_score || 0))
      .slice(0, 4)
      .map(p => p.ticker)
      .filter(Boolean);
    if (!top.length) return;
    let cancelled = false;
    Promise.allSettled(top.map(async ticker => [ticker, (await axios.get(`${API}/data/free/ticker/${ticker}`)).data]))
      .then(results => {
        if (cancelled) return;
        const next = {};
        results.forEach(r => {
          if (r.status === "fulfilled") next[r.value[0]] = r.value[1];
        });
        setFreeIntel(next);
      });
    return () => { cancelled = true; };
  }, [pdufa]);

  const runScan = async () => {
    setScanning(true);
    toast("PHARMA SCAN INITIATED");
    try {
      const { data } = await axios.post(`${API}/pharma/scan`);
      toast(`PHARMA SCAN - ${data.results?.length || 0} PDUFA - ${data.duration_sec}s`);
      reload();
    } catch {
      toast("PHARMA SCAN FAILED");
    } finally {
      setScanning(false);
    }
  };

  const summary = useMemo(() => {
    const counts = pdufa.reduce((a, p) => {
      a[p.tier] = (a[p.tier] || 0) + 1;
      return a;
    }, {});
    const sorted = [...pdufa].sort((a, b) => (b.binary_event_score || 0) - (a.binary_event_score || 0));
    const urgent = pdufa.filter(p => Number(p.days_until) <= 14).sort((a, b) => (a.days_until || 999) - (b.days_until || 999));
    const speculative = sorted.map(p => ({
      ...p,
      riskFlags: [
        p.data_quality === "fallback_calendar" ? "fallback calendar" : null,
        Number(p.short_pct) >= 15 ? "high short interest" : null,
        Number(p.iv_rank) >= 60 ? "high IV" : null,
        !p.trial?.nct_id ? "missing trial id" : null,
        p.trial?.status && !["COMPLETED", "ACTIVE_NOT_RECRUITING"].includes(p.trial.status) ? `trial ${p.trial.status}` : null,
      ].filter(Boolean),
    }));
    return { counts, leader: sorted[0], urgent, speculative };
  }, [pdufa]);

  return (
    <CrtShell
      title="PHARMA INTEL"
      headerRight={
        <button data-testid="pharma-scan-btn" onClick={runScan} disabled={scanning} style={buttonStyle(accent)}>
          [ {scanning ? "SCANNING..." : "PHARMA SCAN"} ]
        </button>
      }
    >
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PDUFA - 90D" value={pdufa.length} sub="UPCOMING" color={accent} accentBar />
        <Stat label="STRONG >=80" value={summary.counts.STRONG || 0} sub="AUTO-ENTER" color={TIER_COLOR.STRONG} />
        <Stat label="WATCH >=65" value={summary.counts.WATCH || 0} sub="MONITORING" color={TIER_COLOR.WATCH} />
        <Stat label="ACTIVE PLAYS" value={active.length} sub={`${track.open || 0} OPEN`} color={accent2} />
        <Stat label="HIT RATE" value={track.hit_rate != null ? `${(track.hit_rate * 100).toFixed(0)}%` : "-"} sub={`${track.winners || 0}/${track.settled || 0} SETTLED`} color="#4ade80" />
        <Stat label="UNREALIZED P&L" value={track.avg_unrealized_pct != null ? `${track.avg_unrealized_pct >= 0 ? "+" : ""}${track.avg_unrealized_pct}%` : "-"} sub="AVG OPEN" color={(track.avg_unrealized_pct ?? 0) >= 0 ? "#4ade80" : "#f87171"} />
      </div>

      <div style={commandGrid}>
        <Card title="BINARY EVENT COMMAND READ" accentColor={accent}>
          {summary.leader ? (
            <div style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(220px, 0.45fr)", gap: 18 }}>
              <div>
                <div style={eyebrow}>TOP CATALYST</div>
                <Link to={`/ticker/${summary.leader.ticker}`} style={tickerHero}>${summary.leader.ticker}</Link>
                <div style={{ color: scoreColor(summary.leader.binary_event_score), fontSize: 30, fontWeight: 900, marginTop: 8 }}>
                  {summary.leader.binary_event_score?.toFixed(0) || "-"}/100
                </div>
                <p style={heroCopy}>
                  {summary.leader.drug || "Unknown drug"} for {summary.leader.indication || "unknown indication"} has a PDUFA date of {summary.leader.pdufa_date || "-"}.
                </p>
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                  <span style={badge(TIER_COLOR[summary.leader.tier] || labelLight)}>{summary.leader.tier || "UNKNOWN"}</span>
                  <span style={badge(Number(summary.leader.days_until) <= 14 ? "#fb923c" : accent2)}>{summary.leader.days_until ?? "-"}D TO EVENT</span>
                  <span style={badge(summary.leader.data_quality === "fallback_calendar" ? "#fbbf24" : "#4ade80")}>{(summary.leader.source || "LIVE").replace(/_/g, " ").toUpperCase()}</span>
                </div>
              </div>
              <div style={miniPanel}>
                <SmallLine k="Prevalence" v={summary.leader.prevalence?.pct != null ? `${summary.leader.prevalence.pct.toFixed(2)}%` : "-"} />
                <SmallLine k="Patients" v={summary.leader.prevalence?.patient_count?.toLocaleString() || "-"} />
                <SmallLine k="Short %" v={summary.leader.short_pct != null ? `${summary.leader.short_pct}%` : "-"} />
                <SmallLine k="IV Rank" v={summary.leader.iv_rank ?? "-"} />
              </div>
            </div>
          ) : (
            <div style={{ color: muted, padding: 20 }}>No PDUFA events loaded. Run Pharma Scan.</div>
          )}
        </Card>

        <Card title="CATALYST COUNTDOWN" accentColor="#fb923c">
          {!summary.urgent.length ? (
            <div style={{ color: muted, padding: 20 }}>No PDUFA events inside 14 days.</div>
          ) : (
            <div style={{ display: "grid", gap: 9 }}>
              {summary.urgent.slice(0, 6).map(p => (
                <div key={`${p.ticker}-${p.pdufa_date}`} style={urgentRow(p.days_until)}>
                  <Link to={`/ticker/${p.ticker}`} style={{ color: accent, fontWeight: 900, textDecoration: "none" }}>${p.ticker}</Link>
                  <span style={{ color: labelLight, flex: 1 }}>{p.drug || p.indication || "Catalyst"}</span>
                  <span style={{ color: Number(p.days_until) <= 7 ? "#f87171" : "#fb923c", fontWeight: 900 }}>{p.days_until}D</span>
                </div>
              ))}
            </div>
          )}
        </Card>
      </div>

      <Card title="SPECULATIVE DATA DEPTH - FREE SOURCE LEDGER" accentColor="#93c5fd">
        <div style={sourceLedgerGrid}>
          {summary.speculative.slice(0, 4).map(p => (
            <DataDepthCard key={p.ticker} p={p} intel={freeIntel[p.ticker]} />
          ))}
          {!summary.speculative.length && <div style={{ color: muted, padding: 20 }}>No pharma candidates loaded.</div>}
        </div>
      </Card>

      <Card title="RISK STACK - WHY THIS IS SPECULATIVE" accentColor="#f87171">
        <div style={riskQueue}>
          {summary.speculative.slice(0, 8).map(p => (
            <div key={`${p.ticker}-${p.pdufa_date}-risk`} style={riskCard(p.riskFlags.length)}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                <Link to={`/ticker/${p.ticker}`} style={{ color: accent, fontWeight: 900, textDecoration: "none" }}>${p.ticker}</Link>
                <span style={{ color: scoreColor(p.binary_event_score), fontWeight: 900 }}>{p.binary_event_score?.toFixed(0) || "-"}/100</span>
              </div>
              <div style={{ color: labelLight, fontSize: 12, marginTop: 6 }}>{p.drug} - {p.indication}</div>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 9 }}>
                {(p.riskFlags.length ? p.riskFlags : ["clean current flags"]).map(flag => <span key={flag} style={badge(p.riskFlags.length ? "#f87171" : "#4ade80")}>{flag.toUpperCase()}</span>)}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="PDUFA CALENDAR - NEXT 90 DAYS - SORTED BY SCORE">
        {!pdufa.length ? (
          <div style={{ color: muted, padding: 20 }}>No PDUFA dates loaded. Click PHARMA SCAN to pull from FDA calendar.</div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>SCORE</th><th style={th}>TIER</th><th style={th}>TICKER</th><th style={th}>DRUG</th>
                <th style={th}>INDICATION</th><th style={th}>PREVALENCE</th><th style={th}>PDUFA</th><th style={th}>DAYS</th><th style={th}>SOURCE</th><th style={th}>PRICE</th><th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {pdufa.map(p => {
                const key = `${p.ticker}-${p.pdufa_date}`;
                const open = expanded === key;
                return (
                  <Fragment key={key}>
                    <tr data-testid={`pdufa-${p.ticker}`} className="row-hover" style={{ borderTop: hairline, cursor: "pointer" }} onClick={() => setExpanded(open ? null : key)}>
                      <td style={{ ...td, color: scoreColor(p.binary_event_score), fontWeight: 700, fontSize: 14 }}>{p.binary_event_score != null ? p.binary_event_score.toFixed(0) : "-"}<span style={{ color: dim, fontSize: 10 }}>/100</span></td>
                      <td style={td}><span style={badge(TIER_COLOR[p.tier] || labelLight)}>{p.tier}</span></td>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                      <td style={{ ...td, color: "#fff" }}>{p.drug}</td>
                      <td style={{ ...td, fontSize: 11 }}>{p.indication?.slice(0, 40)}</td>
                      <td style={{ ...td, color: accent2, fontWeight: 600 }}>{p.prevalence?.pct?.toFixed(2)}%<div style={{ fontSize: 9, color: muted }}>{p.prevalence?.patient_count?.toLocaleString()} US</div></td>
                      <td style={td}>{p.pdufa_date}</td>
                      <td style={{ ...td, color: p.days_until <= 14 ? "#fb923c" : labelLight }}>{p.days_until}d</td>
                      <td style={{ ...td, color: p.data_quality === "fallback_calendar" ? "#fbbf24" : accent2, fontSize: 10, fontWeight: 700 }}>{(p.source || "LIVE").replace(/_/g, " ").toUpperCase()}</td>
                      <td style={td}>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : "-"}</td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}>{open ? "v" : ">"}</td>
                    </tr>
                    {open && (
                      <tr style={{ background: "#03030680" }}>
                        <td colSpan={11} style={{ padding: "18px 24px" }}>
                          <ResearchPanel p={p} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <div style={gridTwo}>
        <Card title={`ACTIVE PLAYS - ${active.length} OPEN`} accentColor={accent2}>
          {!active.length ? <div style={{ color: muted, padding: 20 }}>No active plays yet. Plays scoring >= 80 auto-enter; everything else is manual.</div> : <ActiveTable rows={active} />}
        </Card>

        <Card title={`PHARMA TRACK RECORD - ${track.settled || 0} SETTLED - ISOLATED`} accentColor="#4ade80">
          {!track.history?.length ? <div style={{ color: muted, padding: 20 }}>No closed plays yet. Track record is isolated from main P&L.</div> : <TrackTable rows={track.history} />}
        </Card>
      </div>
    </CrtShell>
  );
}

function ActiveTable({ rows }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead><tr><th style={th}>SOURCE</th><th style={th}>TICKER</th><th style={th}>DRUG</th><th style={th}>PDUFA</th><th style={th}>P&L</th><th style={th}>SCORE</th></tr></thead>
      <tbody>{rows.map((p, i) => (
        <tr key={`${p.ticker}-${p.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
          <td style={td}>{p.source?.toUpperCase()}</td>
          <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
          <td style={td}>{p.drug}</td>
          <td style={td}>{p.pdufa_date}</td>
          <td style={{ ...td, color: (p.gain_pct ?? 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>{p.gain_pct != null ? `${p.gain_pct >= 0 ? "+" : ""}${p.gain_pct}%` : "-"}</td>
          <td style={{ ...td, color: scoreColor(p.entry_score), fontWeight: 700 }}>{p.entry_score?.toFixed(0) || "-"}/100</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function TrackTable({ rows }) {
  return (
    <table style={{ width: "100%", borderCollapse: "collapse" }}>
      <thead><tr><th style={th}>TICKER</th><th style={th}>DRUG</th><th style={th}>PDUFA</th><th style={th}>ENTRY</th><th style={th}>EXIT</th><th style={th}>REALIZED</th></tr></thead>
      <tbody>{rows.map((r, i) => (
        <tr key={`${r.ticker}-${r.exit_date || r.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
          <td style={{ ...td, color: accent, fontWeight: 700 }}>${r.ticker}</td>
          <td style={td}>{r.drug}</td>
          <td style={td}>{r.pdufa_date}</td>
          <td style={td}>{r.entry_price ? `$${r.entry_price.toFixed(2)}` : "-"}</td>
          <td style={td}>{r.exit_price ? `$${r.exit_price.toFixed(2)}` : "-"}</td>
          <td style={{ ...td, color: (r.realized_pct ?? 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>{r.realized_pct != null ? `${r.realized_pct >= 0 ? "+" : ""}${r.realized_pct}%` : "-"}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

function DataDepthCard({ p, intel }) {
  const facts = intel?.sec?.companyfacts || {};
  const metrics = facts.metrics || {};
  const trials = intel?.clinical_trials || {};
  const fda = intel?.openfda || {};
  const q = intel?.sources || intel?.source_quality || [];
  const trialCount = trials.returned_count ?? trials.trial_count;
  const fdaCount = fda.returned_count ?? fda.event_count ?? fda.events?.length;
  return (
    <div style={depthCard}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
        <Link to={`/ticker/${p.ticker}`} style={{ color: accent, fontWeight: 900, fontSize: 18, textDecoration: "none" }}>${p.ticker}</Link>
        <span style={badge(intel?.ok ? "#4ade80" : "#fb923c")}>{intel ? "LOADED" : "FETCHING"}</span>
      </div>
      <div style={{ color: labelLight, fontSize: 12, marginTop: 8, minHeight: 34 }}>{intel?.company_name || p.drug || p.indication}</div>
      <div style={depthMetrics}>
        <DepthMetric label="Revenue" value={money(metrics.revenue?.value)} sub={metrics.revenue?.period_end} />
        <DepthMetric label="Net Income" value={money(metrics.net_income?.value)} sub={metrics.net_income?.period_end} color={(metrics.net_income?.value || 0) >= 0 ? "#4ade80" : "#f87171"} />
        <DepthMetric label="Cash" value={money(metrics.cash?.value || metrics.cash_and_equivalents?.value)} sub={metrics.cash?.period_end || metrics.cash_and_equivalents?.period_end} />
        <DepthMetric label="Facts" value={facts.fact_count || "-"} sub={facts.source || "SEC"} />
      </div>
      <div style={{ display: "grid", gap: 6, marginTop: 10 }}>
        <SmallLine k="ClinicalTrials" v={`${trials.quality || "unknown"} ${trialCount != null ? `(${trialCount})` : ""}`} />
        <SmallLine k="openFDA" v={`${fda.quality || "unknown"} ${fdaCount != null ? `(${fdaCount})` : ""}`} />
        <SmallLine k="SEC CIK" v={intel?.sec?.lookup?.cik || "-"} />
      </div>
      <div style={{ display: "flex", gap: 5, flexWrap: "wrap", marginTop: 10 }}>
        {q.map(source => <span key={source.key} style={badge(source.ok ? "#4ade80" : "#fb923c")}>{source.key}:{source.quality}</span>)}
      </div>
    </div>
  );
}

function DepthMetric({ label, value, sub, color = labelLight }) {
  return (
    <div style={{ border: hairline, background: "rgba(255,255,255,0.016)", padding: "8px 7px", minWidth: 0 }}>
      <div style={{ color: dim, fontSize: 8, letterSpacing: "0.14em" }}>{label}</div>
      <div style={{ color, fontSize: 12, fontWeight: 800, marginTop: 5, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</div>
      <div style={{ color: muted, fontSize: 8, marginTop: 3, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sub || "-"}</div>
    </div>
  );
}

function ResearchPanel({ p }) {
  const comp = p.score_components || {};
  const trial = p.trial || {};
  const fdaLink = trial.nct_id ? `https://clinicaltrials.gov/study/${trial.nct_id}` : "https://www.fda.gov/drugs/development-resources/drug-approvals-and-databases";
  return (
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 24 }}>
      <div>
        <div style={panelTitle}>// BINARY EVENT SCORE COMPONENTS</div>
        {Object.entries(comp).map(([k, v]) => (
          <div key={k} style={{ display: "grid", gridTemplateColumns: "150px 1fr 70px", gap: 12, padding: "6px 0", borderBottom: hairline, fontSize: 12 }}>
            <span style={{ color: dim, letterSpacing: "0.1em" }}>{k.toUpperCase()}</span>
            <span style={{ color: labelLight }}>{v.note}</span>
            <span style={{ color: scoreColor(v.points * 4), fontWeight: 700, textAlign: "right" }}>{v.points}/{v.max}</span>
          </div>
        ))}
      </div>
      <div>
        <div style={panelTitle}>// CLINICAL DATA</div>
        <Row k="NCT ID" v={trial.nct_id || "-"} />
        <Row k="PHASE" v={(trial.phases || []).join(", ") || "-"} />
        <Row k="STATUS" v={trial.status || "-"} />
        <Row k="ENROLLMENT" v={trial.enrollment || "-"} />
        <Row k="PRIMARY COMPLETION" v={trial.primary_completion || "-"} />
        <Row k="SHORT INTEREST" v={p.short_pct != null ? `${p.short_pct}%` : "-"} />
        <Row k="IV RANK" v={p.iv_rank != null ? `${p.iv_rank}` : "-"} />
        <a href={fdaLink} target="_blank" rel="noreferrer" style={{ ...buttonStyle(accent), display: "block", textAlign: "center", textDecoration: "none", marginTop: 14 }}>READ SOURCE</a>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em" }}>{k}</span>
      <span style={{ color: labelLight, fontWeight: 600, textAlign: "right" }}>{v}</span>
    </div>
  );
}

function SmallLine({ k, v }) {
  return <Row k={k} v={v} />;
}

function money(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  return `$${n.toLocaleString(undefined, { maximumFractionDigits: 0 })}`;
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };
const commandGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(300px, 0.8fr)", gap: 18 };
const gridTwo = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 0.9fr)", gap: 18 };
const sourceLedgerGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(280px, 1fr))", gap: 12 };
const riskQueue = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 };
const eyebrow = { color: dim, fontSize: 9, letterSpacing: "0.18em", fontWeight: 800, marginBottom: 8 };
const tickerHero = { color: accent, fontSize: 42, fontWeight: 900, letterSpacing: "0.08em", textDecoration: "none" };
const heroCopy = { color: labelLight, lineHeight: 1.55, margin: "12px 0 0", maxWidth: 760 };
const miniPanel = { border: hairline, background: "rgba(255,255,255,0.018)", padding: "10px 14px", alignSelf: "start" };
const panelTitle = { color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 };
const depthCard = { border: hairline, borderTop: "1px solid rgba(147,197,253,0.7)", background: "linear-gradient(180deg, rgba(147,197,253,0.055), rgba(255,255,255,0.012))", padding: 13 };
const depthMetrics = { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 7, marginTop: 12 };
function riskCard(flagCount) {
  const color = flagCount ? "#f87171" : "#4ade80";
  return { border: `0.5px solid ${color}55`, background: `${color}0b`, padding: "11px 12px" };
}
function badge(color) {
  return { color, padding: "3px 8px", border: `0.5px solid ${color}66`, background: `${color}08`, letterSpacing: "0.12em", fontSize: 10, fontWeight: 700 };
}
function urgentRow(days) {
  const color = Number(days) <= 7 ? "#f87171" : "#fb923c";
  return { display: "flex", alignItems: "center", gap: 10, border: `0.5px solid ${color}55`, background: `${color}0c`, padding: "10px 12px", fontSize: 12 };
}
function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}
