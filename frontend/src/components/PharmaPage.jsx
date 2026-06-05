import { useEffect, useState, Fragment } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const TIER_COLOR = {
  STRONG:  "#4ade80",
  WATCH:   "#fbbf24",
  NEUTRAL: "#9ca3af",
  WEAK:    "#6b7280",
  MANUAL:  accent2,
};

function scoreColor(s) {
  if (s == null) return muted;
  if (s >= 80) return "#4ade80";
  if (s >= 65) return "#fbbf24";
  if (s >= 40) return labelLight;
  return muted;
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };

export default function PharmaPage() {
  const [pdufa, setPdufa] = useState([]);
  const [active, setActive] = useState([]);
  const [track, setTrack] = useState({});
  const [expanded, setExpanded] = useState(null);
  const [scanning, setScanning] = useState(false);

  const reload = () => {
    axios.get(`${API}/pharma/pdufa?days=90`).then(r => setPdufa(r.data.results || [])).catch(() => {});
    axios.get(`${API}/pharma/active`).then(r => setActive(r.data.plays || [])).catch(() => {});
    axios.get(`${API}/pharma/track_record`).then(r => setTrack(r.data || {})).catch(() => {});
  };
  useEffect(reload, []);

  const runScan = async () => {
    setScanning(true);
    toast("PHARMA SCAN INITIATED");
    try {
      const { data } = await axios.post(`${API}/pharma/scan`);
      toast(`PHARMA SCAN — ${data.results?.length || 0} PDUFA · ${data.duration_sec}s`);
      reload();
    } catch {
      toast("PHARMA SCAN FAILED");
    } finally {
      setScanning(false);
    }
  };

  const counts = pdufa.reduce((a, p) => {
    a[p.tier] = (a[p.tier] || 0) + 1;
    return a;
  }, {});

  return (
    <CrtShell title="PHARMA INTEL"
      headerRight={
        <button data-testid="pharma-scan-btn" onClick={runScan} disabled={scanning}
          style={{
            background: "transparent", border: `0.5px solid ${accent}`,
            color: accent, fontSize: 11, padding: "8px 16px", cursor: scanning ? "wait" : "pointer",
            letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>
          [ {scanning ? "SCANNING..." : "▶ PHARMA SCAN"} ]
        </button>
      }>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="PDUFA · 90D" value={pdufa.length} sub="UPCOMING" color={accent} accentBar />
        <Stat label="STRONG ≥80" value={counts.STRONG || 0} sub="AUTO-ENTER" color={TIER_COLOR.STRONG} />
        <Stat label="WATCH ≥65" value={counts.WATCH || 0} sub="MONITORING" color={TIER_COLOR.WATCH} />
        <Stat label="ACTIVE PLAYS" value={active.length} sub={`${track.open || 0} OPEN`} color={accent2} />
        <Stat label="HIT RATE" value={track.hit_rate != null ? `${(track.hit_rate * 100).toFixed(0)}%` : "—"}
              sub={`${track.winners || 0}/${track.settled || 0} SETTLED`} color="#4ade80" />
        <Stat label="UNREALIZED P&L"
              value={track.avg_unrealized_pct != null ? `${track.avg_unrealized_pct >= 0 ? "+" : ""}${track.avg_unrealized_pct}%` : "—"}
              sub="AVG OPEN" color={(track.avg_unrealized_pct ?? 0) >= 0 ? "#4ade80" : "#f87171"} />
      </div>

      <Card title="PDUFA CALENDAR · NEXT 90 DAYS · SORTED BY SCORE">
        {!pdufa.length ? (
          <div style={{ color: muted, padding: 20 }}>
            No PDUFA dates loaded. Click <code style={{ color: accent }}>▶ PHARMA SCAN</code> to pull from FDA calendar.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>SCORE</th><th style={th}>TIER</th>
                <th style={th}>TICKER</th><th style={th}>DRUG</th>
                <th style={th}>INDICATION</th><th style={th}>PREVALENCE</th>
                <th style={th}>PDUFA</th><th style={th}>DAYS</th>
                <th style={th}>PRICE</th><th style={th}></th>
              </tr>
            </thead>
            <tbody>
              {pdufa.map(p => {
                const open = expanded === `${p.ticker}-${p.pdufa_date}`;
                const sc = p.binary_event_score;
                return (
                  <Fragment key={`${p.ticker}-${p.pdufa_date}`}>
                    <tr
                      data-testid={`pdufa-${p.ticker}`}
                      className="row-hover"
                      style={{ borderTop: hairline, cursor: "pointer" }}
                      onClick={() => setExpanded(open ? null : `${p.ticker}-${p.pdufa_date}`)}>
                      <td style={{ ...td, color: scoreColor(sc), fontWeight: 700, fontSize: 14 }}>
                        {sc != null ? `${sc.toFixed(0)}` : "—"}<span style={{ color: dim, fontSize: 10 }}>/100</span>
                      </td>
                      <td style={td}>
                        <span style={{
                          color: TIER_COLOR[p.tier], padding: "3px 8px",
                          border: `0.5px solid ${TIER_COLOR[p.tier]}66`,
                          background: `${TIER_COLOR[p.tier]}08`,
                          letterSpacing: "0.14em", fontSize: 10, fontWeight: 700,
                        }}>{p.tier}</span>
                      </td>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                      <td style={{ ...td, color: "#fff" }}>{p.drug}</td>
                      <td style={{ ...td, fontSize: 11 }}>{p.indication?.slice(0, 40)}</td>
                      <td style={{ ...td, color: accent2, fontWeight: 600 }}>
                        {p.prevalence?.pct?.toFixed(2)}%
                        <div style={{ fontSize: 9, color: muted }}>
                          {p.prevalence?.patient_count?.toLocaleString()} US
                        </div>
                      </td>
                      <td style={td}>{p.pdufa_date}</td>
                      <td style={{ ...td, color: p.days_until <= 14 ? "#fb923c" : labelLight }}>
                        {p.days_until}d
                      </td>
                      <td style={td}>{p.current_price != null ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                      <td style={{ ...td, color: dim, textAlign: "right" }}>{open ? "▼" : "▶"}</td>
                    </tr>
                    {open && (
                      <tr style={{ background: "#03030680" }}>
                        <td colSpan={10} style={{ padding: "18px 24px" }}>
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

      <Card title={`ACTIVE PLAYS · ${active.length} OPEN`}>
        {!active.length ? (
          <div style={{ color: muted, padding: 20 }}>
            No active plays yet. Plays scoring ≥ 80 auto-enter; everything else is manual.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>SOURCE</th><th style={th}>TICKER</th>
                <th style={th}>DRUG</th><th style={th}>PDUFA</th>
                <th style={th}>ENTRY</th><th style={th}>CURRENT</th>
                <th style={th}>P&L</th><th style={th}>SCORE</th>
              </tr>
            </thead>
            <tbody>
              {active.map((p, i) => (
                <tr key={`${p.ticker}-${p.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
                  <td style={td}>
                    <span style={{
                      color: p.source === "auto" ? "#4ade80" : accent2,
                      fontSize: 10, letterSpacing: "0.12em", fontWeight: 700,
                    }}>{p.source?.toUpperCase()}</span>
                  </td>
                  <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                  <td style={td}>{p.drug}</td>
                  <td style={td}>{p.pdufa_date}</td>
                  <td style={td}>{p.entry_price ? `$${p.entry_price.toFixed(2)}` : "—"}</td>
                  <td style={td}>{p.current_price ? `$${p.current_price.toFixed(2)}` : "—"}</td>
                  <td style={{ ...td, color: (p.gain_pct ?? 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                    {p.gain_pct != null ? `${p.gain_pct >= 0 ? "+" : ""}${p.gain_pct}%` : "—"}
                  </td>
                  <td style={{ ...td, color: scoreColor(p.entry_score), fontWeight: 700 }}>
                    {p.entry_score?.toFixed(0) || "—"}/100
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>

      <Card title={`PHARMA TRACK RECORD · ${track.settled || 0} SETTLED · ISOLATED`}>
        {!track.history?.length ? (
          <div style={{ color: muted, padding: 20 }}>
            No closed plays yet. Track record is fully isolated from main P&L.
          </div>
        ) : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr>
                <th style={th}>TICKER</th><th style={th}>DRUG</th>
                <th style={th}>PDUFA</th><th style={th}>ENTRY</th>
                <th style={th}>EXIT</th><th style={th}>REALIZED</th>
              </tr>
            </thead>
            <tbody>
              {track.history.map((r, i) => (
                <tr key={`${r.ticker}-${r.exit_date || r.pdufa_date || i}`} className="row-hover" style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent, fontWeight: 700 }}>${r.ticker}</td>
                  <td style={td}>{r.drug}</td>
                  <td style={td}>{r.pdufa_date}</td>
                  <td style={td}>{r.entry_price ? `$${r.entry_price.toFixed(2)}` : "—"}</td>
                  <td style={td}>{r.exit_price ? `$${r.exit_price.toFixed(2)}` : "—"}</td>
                  <td style={{ ...td, color: (r.realized_pct ?? 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                    {r.realized_pct != null ? `${r.realized_pct >= 0 ? "+" : ""}${r.realized_pct}%` : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </CrtShell>
  );
}

function ResearchPanel({ p }) {
  const comp = p.score_components || {};
  const trial = p.trial || {};
  const fdaLink = trial.nct_id
    ? `https://clinicaltrials.gov/study/${trial.nct_id}`
    : `https://www.fda.gov/drugs/development-resources/drug-approvals-and-databases`;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "2fr 1fr", gap: 24 }}>
      <div>
        <div style={{ color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 }}>
          // BINARY EVENT SCORE COMPONENTS
        </div>
        {Object.entries(comp).map(([k, v]) => (
          <div key={k} style={{ display: "flex", justifyContent: "space-between",
                                  padding: "6px 0", borderBottom: hairline, fontSize: 12 }}>
            <span style={{ color: dim, letterSpacing: "0.1em" }}>{k.toUpperCase()}</span>
            <span style={{ color: labelLight }}>{v.note}</span>
            <span style={{ color: scoreColor(v.points * 4), fontWeight: 700, minWidth: 60, textAlign: "right" }}>
              {v.points}/{v.max}
            </span>
          </div>
        ))}
      </div>
      <div>
        <div style={{ color: labelLight, fontSize: 11, letterSpacing: "0.14em", marginBottom: 10, fontWeight: 700 }}>
          // CLINICAL DATA
        </div>
        <Row k="NCT ID" v={trial.nct_id || "—"} />
        <Row k="PHASE" v={(trial.phases || []).join(", ") || "—"} />
        <Row k="STATUS" v={trial.status || "—"} />
        <Row k="ENROLLMENT" v={trial.enrollment || "—"} />
        <Row k="PRIMARY COMPLETION" v={trial.primary_completion || "—"} />
        <Row k="SHORT INTEREST" v={p.short_pct != null ? `${p.short_pct}%` : "—"} />
        <Row k="IV RANK" v={p.iv_rank != null ? `${p.iv_rank}` : "—"} />
        <Row k="PREVALENCE" v={p.prevalence?.pct?.toFixed(2) + "%"} />
        <Row k="EST. US PATIENTS" v={p.prevalence?.patient_count?.toLocaleString() || "—"} />
        <a href={fdaLink} target="_blank" rel="noreferrer"
          style={{
            display: "block", marginTop: 14, padding: "10px 14px",
            border: `0.5px solid ${accent}`, color: accent,
            textAlign: "center", textDecoration: "none",
            letterSpacing: "0.14em", fontWeight: 700, fontSize: 11,
          }}>
          📄 READ THIS BEFORE ENTERING
        </a>
      </div>
    </div>
  );
}

function Row({ k, v }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between",
      padding: "5px 0", borderBottom: hairline, fontSize: 11,
    }}>
      <span style={{ color: dim, letterSpacing: "0.14em" }}>{k}</span>
      <span style={{ color: labelLight, fontWeight: 600 }}>{v}</span>
    </div>
  );
}
