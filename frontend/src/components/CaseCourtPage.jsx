import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { FileText, Gavel, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, muted, labelLight, hairline, cardBg, cardBgHi } = tokens;

const postureColors = {
  COURT_SUPPORTS_PM: "#4ade80",
  BULLISH_WATCH: "#fbbf24",
  EVIDENCE_CONFLICT: "#a78bfa",
  COURT_OBJECTS: "#fb7185",
  PM_REJECTED: "#fb7185",
  REQUIRES_CLEANER_DATA: "#ef4444",
  EQUITY_ONLY_UNTIL_OPTIONS_CLEAN: "#38bdf8",
  NOT_APPLICABLE: "#8b949e",
};

export default function CaseCourtPage() {
  const [data, setData] = useState(null);
  const [selectedTicker, setSelectedTicker] = useState(null);
  const [tab, setTab] = useState("DOCKET");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const { data: payload } = await axios.get(`${API}/case_court/latest`, { params: { limit: 30 }, timeout: 18000 });
      setData(payload);
      const rows = payload.trials || [];
      setSelectedTicker(prev => prev && rows.some(r => r.ticker === prev) ? prev : rows[0]?.ticker || null);
    } catch (e) {
      setError(e.message || "Case Court unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  const refreshCourt = async () => {
    if (refreshing) return;
    setRefreshing(true);
    setError(null);
    try {
      const { data: payload } = await axios.post(`${API}/case_court/refresh`, null, { params: { limit: 30 }, timeout: 30000 });
      setData(payload);
      const rows = payload.trials || [];
      setSelectedTicker(prev => prev && rows.some(r => r.ticker === prev) ? prev : rows[0]?.ticker || null);
    } catch (e) {
      setError(e.message || "Case Court refresh failed");
    } finally {
      setRefreshing(false);
    }
  };

  const trials = useMemo(() => data?.trials || [], [data]);
  const summary = data?.summary || {};
  const selected = useMemo(
    () => trials.find(t => t.ticker === selectedTicker) || trials[0] || null,
    [trials, selectedTicker]
  );
  return (
    <CrtShell
      title="CASE COURT"
      headerRight={
        <button onClick={refreshCourt} disabled={refreshing} style={buttonStyle(accent2)}>
          <RefreshCw size={15} className={refreshing ? "spin" : ""} />
          {refreshing ? "TRYING CASES" : "RUN COURT"}
        </button>
      }
    >
      {error && <div style={errorBox}><ShieldAlert size={16} /> {error}</div>}

      <div className="case-court-command" style={commandStrip}>
        <div>
          <div style={eyebrow}>ADVERSARIAL ALLOCATION REVIEW</div>
          <div className="case-court-command-title" style={commandTitle}>Defense. Prosecutor. Judge.</div>
        </div>
        <div style={commandMeta}>
          <Mini label="Authority" value="READ ONLY" color={accent2} />
          <Mini label="Rubric" value={summary.rubric_version ? "V2.1 UI" : "SYNCING"} color={accent} />
          <Mini label="Evidence Rule" value="N/A IS NEUTRAL" color="#a78bfa" />
        </div>
      </div>

      <div className="case-court-stats" style={statRow}>
        <Stat label="TRIALS" value={loading ? "--" : summary.trials ?? trials.length} sub="LATEST SCAN DOCKET" color={accent} accentBar />
        <Stat label="SUPPORTS PM" value={summary.supports_pm ?? 0} sub="ADVISORY ONLY" color="#4ade80" />
        <Stat label="WATCH ONLY" value={summary.bullish_watch ?? 0} sub="NO PM AUTHORITY" color="#fbbf24" />
        <Stat label="READY" value={summary.live_run_ready ?? 0} sub="PM APPROVED ONLY" color="#38bdf8" />
        <Stat label="PM REJECT" value={summary.pm_rejected ?? 0} sub="NO AUTHORITY" color="#fb7185" />
        <Stat label="DATA HOLDS" value={summary.requires_cleaner_data ?? 0} sub="CLEAN DATA FIRST" color={summary.requires_cleaner_data ? "#ef4444" : "#4ade80"} />
      </div>

      <div className="case-court-tabs" style={tabBar}>
        {["DOCKET", "COURT DOCS", "LIVE READINESS"].map(x => (
          <button key={x} onClick={() => setTab(x)} style={tabButton(tab === x)}>{x}</button>
        ))}
      </div>

      {tab === "LIVE READINESS" && (
        <Card title="LIVE RUN READINESS" accentColor={accent2}>
          <ReadinessBoard summary={summary} trials={trials} />
        </Card>
      )}

      {tab !== "LIVE READINESS" && <div className="case-court-layout" style={layout}>
        <Card title="COURT DOCKET" accentColor={accent}>
          <div style={docket}>
            {trials.map(t => (
              <button
                key={t.case_id || t.ticker}
                onClick={() => setSelectedTicker(t.ticker)}
                style={docketRow(selected?.ticker === t.ticker, postureColors[t.judge?.advisory_posture] || muted)}
              >
                <div>
                  <div style={tickerStyle}>${t.ticker}</div>
                  <div style={metaLine}>{t.pm_action || "-"} / PM {t.pm_score ?? "-"}</div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ ...postureText, color: postureColors[t.judge?.advisory_posture] || muted }}>
                    {labelPosture(t.judge?.advisory_posture)}
                  </div>
                  <div style={metaLine}>D-P {signed(t.judge?.defense_minus_prosecutor)}</div>
                </div>
              </button>
            ))}
            {!trials.length && <Empty loading={loading} />}
          </div>
        </Card>

        <div className="case-court-main-panel">
          {tab === "COURT DOCS" ? (
            <CourtDocs trial={selected} loading={loading} />
          ) : (
            <>
              <Card title={selected ? `JUDGE RULING / ${selected.ticker}` : "JUDGE RULING"} accentColor={postureColors[selected?.judge?.advisory_posture] || accent2}>
                {selected ? <Ruling trial={selected} /> : <Empty loading={loading} />}
              </Card>

              <div style={lawyerGrid}>
                <LawyerCard title="DEFENSE" side={selected?.defense} color="#4ade80" />
                <LawyerCard title="PROSECUTOR" side={selected?.prosecution} color="#fb7185" />
              </div>

              <Card title="EXPERT WITNESSES" accentColor={accent2}>
                {selected ? <WitnessGrid rows={selected.witnesses || []} /> : <Empty loading={loading} />}
              </Card>

              <Card title="APPEAL TRIGGERS / FUTURE REVIEW" accentColor="#a78bfa">
                {selected ? (
                  <div style={appealGrid}>
                    {(selected.appeal_triggers || []).map((x, i) => <div key={`${x}-${i}`} style={appealItem}>{x}</div>)}
                  </div>
                ) : <Empty loading={loading} />}
              </Card>
            </>
          )}
        </div>
      </div>}
    </CrtShell>
  );
}

function Ruling({ trial }) {
  const posture = trial.judge?.advisory_posture || "UNKNOWN";
  const color = postureColors[posture] || muted;
  const expression = trial.judge?.expression_hint || "-";
  return (
    <div style={rulingGrid}>
      <div style={rulingMain}>
        <Gavel size={32} color={color} />
        <div style={{ minWidth: 0 }}>
          <div style={smallLabel}>ADVISORY POSTURE</div>
          <div className="case-court-posture" style={{ color, fontSize: 26, fontWeight: 900, letterSpacing: "0.10em", lineHeight: 1.12, overflowWrap: "anywhere" }}>{labelPosture(posture)}</div>
          <div style={expressionBadge}>EXPRESSION: {labelPosture(expression)}</div>
          <div style={{ color: labelLight, marginTop: 8, lineHeight: 1.5 }}>{trial.judge?.detail}</div>
        </div>
      </div>
      <div className="case-court-ruling-metrics" style={rulingMetrics}>
        <Mini label="Defense" value={trial.defense?.score ?? "-"} color="#4ade80" />
        <Mini label="Prosecutor" value={trial.prosecution?.score ?? "-"} color="#fb7185" />
        <Mini label="Scan Age" value={trial.scan_age_hours == null ? "-" : `${trial.scan_age_hours}H`} color={trial.scan_age_hours > 26 ? "#f87171" : accent} />
        <Mini label="Decision Grade" value={trial.evidence_coverage?.decision_grade ? "YES" : "NO"} color={trial.evidence_coverage?.decision_grade ? "#4ade80" : "#fb7185"} />
      </div>
    </div>
  );
}

function LawyerCard({ title, side, color }) {
  return (
    <Card title={title} accentColor={color}>
      {side ? (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "center", marginBottom: 14 }}>
            <div style={{ color, fontSize: 22, fontWeight: 900, letterSpacing: "0.14em" }}>{side.score}</div>
            <div style={{ color: labelLight, fontSize: 12, lineHeight: 1.45 }}>{side.opening_argument || side.opening_objection}</div>
          </div>
          <div style={{ display: "grid", gap: 9 }}>
            {(side.points || []).map((p, i) => (
              <div key={`${p.label}-${i}`} style={evidenceRow}>
                <div>
                  <div style={{ color: labelLight, fontWeight: 800 }}>{p.label}</div>
                  <div style={metaLine}>{p.detail}</div>
                </div>
                <div style={{ color, fontWeight: 900 }}>+{p.weight}</div>
              </div>
            ))}
            {!(side.points || []).length && <div style={metaLine}>No material evidence points.</div>}
          </div>
        </div>
      ) : <Empty />}
    </Card>
  );
}

function WitnessGrid({ rows }) {
  return (
    <div style={witnessGrid}>
      {rows.map(w => {
        const color = w.stance === "BULL" ? "#4ade80" : w.stance === "BEAR" ? "#fb7185" : "#fbbf24";
        return (
          <div key={w.name} style={witnessCard}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {w.stance === "BULL" ? <ShieldCheck size={15} color={color} /> : <ShieldAlert size={15} color={color} />}
              <span style={{ color: labelLight, fontWeight: 900, letterSpacing: "0.12em", fontSize: 11 }}>{w.name}</span>
            </div>
            <div style={{ color, fontSize: 18, fontWeight: 900, marginTop: 10 }}>{w.stance}</div>
            <div style={{ color: muted, fontSize: 12, marginTop: 8, lineHeight: 1.45 }}>{w.testimony}</div>
          </div>
        );
      })}
    </div>
  );
}

function CourtDocs({ trial, loading }) {
  if (!trial) return <Empty loading={loading} />;
  const docs = trial.court_docs || {};
  const exhibits = trial.exhibits || [];
  const coverage = trial.evidence_coverage || {};
  return (
    <div style={{ display: "grid", gap: 18 }}>
      <Card title="COURT DOCS" accentColor={accent}>
        <div style={docHeader}>
          <FileText size={22} color={accent} />
          <div>
            <div className="case-court-doc-title" style={{ color: accent, fontSize: 20, fontWeight: 900, letterSpacing: "0.12em" }}>
              {docs.caption || `${trial.ticker} Allocation Case`}
            </div>
            <div style={{ color: muted, marginTop: 8, lineHeight: 1.45 }}>{docs.docket_entry}</div>
          </div>
        </div>
        <div style={noteBox}>{docs.clerk_notes}</div>
        <div style={coverageGrid}>
          <Mini label="Applicable" value={`${coverage.scored ?? coverage.scored_exhibits ?? 0}/${coverage.applicable ?? coverage.applicable_exhibits ?? 0}`} color={accent2} />
          <Mini label="Required Missing" value={coverage.missing_required ?? 0} color={coverage.missing_required ? "#fb7185" : "#4ade80"} />
          <Mini label="Decision Grade" value={coverage.decision_grade ? "YES" : "NO"} color={coverage.decision_grade ? "#4ade80" : "#fb7185"} />
          <Mini label="Coverage" value={coverage.coverage_label || "-"} color={accent} />
        </div>
      </Card>

      <Card title="MINI TRIALS" accentColor={accent2}>
        <div style={miniTrialGrid}>
          {(trial.mini_trials || []).map(mt => (
            <div key={mt.name} style={miniTrialCard(postureColors[mt.verdict] || verdictColor(mt.verdict))}>
              <div style={smallLabel}>{labelPosture(mt.name)}</div>
              <div style={{ color: verdictColor(mt.verdict), fontWeight: 900, letterSpacing: "0.12em" }}>{labelPosture(mt.verdict)}</div>
              <div style={metaLine}>D {mt.defense_score} / P {mt.prosecution_score} / spread {signed(mt.spread)}</div>
              {!!(mt.missing_required || []).length && <div style={missingLine}>Missing: {mt.missing_required.join(", ")}</div>}
            </div>
          ))}
        </div>
      </Card>

      <Card title="EVIDENCE EXHIBITS" accentColor="#a78bfa">
        <div style={exhibitGrid}>
          {exhibits.map(ex => {
            const color = ex.side === "DEFENSE" ? "#4ade80" : ex.side === "PROSECUTOR" ? "#fb7185" : muted;
            return (
              <div key={ex.key} style={exhibitCard(color)}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                  <div style={{ color: labelLight, fontWeight: 900, letterSpacing: "0.08em" }}>{ex.label}</div>
                  <div style={{ color, fontWeight: 900 }}>{ex.side === "NEUTRAL" ? "0" : `+${ex.score}`}</div>
                </div>
                <div style={{ color, marginTop: 8, fontSize: 12, letterSpacing: "0.12em", fontWeight: 900 }}>{ex.status}</div>
                <div style={{ color: muted, lineHeight: 1.45, marginTop: 8, fontSize: 12 }}>{ex.detail}</div>
                <div style={metaLine}>{ex.source || "terminal"} {ex.freshness ? `/ ${ex.freshness}` : ""}</div>
              </div>
            );
          })}
        </div>
      </Card>
    </div>
  );
}

function ReadinessBoard({ summary, trials }) {
  const ready = trials.filter(t => t.judge?.live_run_ready);
  const holds = trials.filter(t => !t.judge?.live_run_ready);
  return (
    <div style={readinessGrid}>
      <div>
        <div style={eyebrow}>READ-ONLY LIVE RUN</div>
        <h2 style={sectionH}>Case Court is prepared as an advisory layer only.</h2>
        <div style={noteBox}>
          It can run against the newest scan, preserve court docs, neutralize non-applicable evidence, and flag which records are clean enough for review. It still has no execution authority and does not override PM.
        </div>
      </div>
      <div className="case-court-coverage-grid" style={coverageGrid}>
        <Mini label="Live Ready" value={summary.live_run_ready ?? ready.length} color="#38bdf8" />
        <Mini label="Decision Grade" value={summary.decision_grade ?? 0} color="#4ade80" />
        <Mini label="Cleaner Data" value={summary.requires_cleaner_data ?? 0} color="#fb7185" />
        <Mini label="Neutralized" value={summary.neutralized_exhibits ?? 0} color={muted} />
      </div>
      <div className="case-court-split-list" style={splitList}>
        <div>
          <div style={smallLabel}>READY DOCKET</div>
          {ready.slice(0, 12).map(t => <div key={t.ticker} style={compactRow}>${t.ticker}<span>{labelPosture(t.judge?.advisory_posture)}</span></div>)}
          {!ready.length && <div style={metaLine}>No decision-grade cases yet.</div>}
        </div>
        <div>
          <div style={smallLabel}>HELD DOCKET</div>
          {holds.slice(0, 12).map(t => <div key={t.ticker} style={compactRow}>${t.ticker}<span>{labelPosture(t.judge?.advisory_posture)}</span></div>)}
          {!holds.length && <div style={metaLine}>No held cases.</div>}
        </div>
      </div>
    </div>
  );
}

function Mini({ label, value, color }) {
  return (
    <div style={miniBox}>
      <div style={smallLabel}>{label}</div>
      <div style={{ color, fontSize: 14, fontWeight: 900, wordBreak: "break-word" }}>{value}</div>
    </div>
  );
}

function Empty({ loading }) {
  return <div style={{ color: muted, padding: 16 }}>{loading ? "Court is assembling evidence..." : "No trial records available."}</div>;
}

function labelPosture(v) {
  return String(v || "UNKNOWN").replaceAll("_", " ");
}

function verdictColor(v) {
  const s = String(v || "");
  if (s.includes("SUPPORTS")) return "#4ade80";
  if (s.includes("OBJECTS") || s.includes("NOT_DECISION")) return "#fb7185";
  if (s.includes("WATCH")) return "#fbbf24";
  if (s.includes("CONFLICT")) return "#a78bfa";
  return muted;
}

function signed(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return n > 0 ? `+${n}` : `${n}`;
}

function buttonStyle(color) {
  return {
    border: `1px solid ${color}77`,
    background: `${color}10`,
    color,
    padding: "10px 14px",
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    fontSize: 12,
    fontWeight: 900,
    letterSpacing: "0.14em",
  };
}

const eyebrow = { color: accent2, fontSize: 11, letterSpacing: "0.18em", fontWeight: 900, marginBottom: 10 };
const commandStrip = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) minmax(420px, 0.95fr)",
  gap: 14,
  alignItems: "stretch",
  marginBottom: 16,
  border: hairline,
  background: "linear-gradient(90deg, rgba(200,168,75,0.065), rgba(94,234,212,0.025), rgba(255,255,255,0.012))",
  padding: 14,
};
const commandTitle = {
  color: accent,
  fontSize: 24,
  lineHeight: 1.08,
  letterSpacing: "0.08em",
  fontWeight: 900,
};
const commandMeta = {
  display: "grid",
  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  gap: 10,
};
const smallLabel = { color: muted, fontSize: 10, letterSpacing: "0.16em", fontWeight: 800, textTransform: "uppercase", marginBottom: 6 };
const errorBox = {
  border: "1px solid rgba(248,113,113,0.45)",
  color: "#fca5a5",
  background: "rgba(127,29,29,0.18)",
  padding: 12,
  marginBottom: 16,
  display: "flex",
  alignItems: "center",
  gap: 8,
};
const statRow = { display: "grid", gridTemplateColumns: "repeat(6, minmax(118px, 1fr))", background: cardBg, border: hairline, marginBottom: 16, overflow: "hidden" };
const tabBar = { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 16 };
const layout = { display: "grid", gridTemplateColumns: "330px minmax(0, 1fr)", gap: 18, alignItems: "start", maxWidth: "100%" };
const docket = { display: "grid", gap: 7, maxHeight: "calc(100vh - 310px)", minHeight: 380, overflowY: "auto", paddingRight: 2 };
const tickerStyle = { color: accent, fontWeight: 900, fontSize: 20, letterSpacing: "0.10em" };
const metaLine = { color: muted, fontSize: 11, letterSpacing: "0.06em", marginTop: 4 };
const postureText = { fontSize: 10, letterSpacing: "0.12em", fontWeight: 900, maxWidth: 140 };
const lawyerGrid = { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 };
const evidenceRow = {
  border: hairline,
  background: "rgba(255,255,255,0.02)",
  padding: 10,
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) 46px",
  gap: 10,
  alignItems: "center",
};
const rulingGrid = { display: "grid", gridTemplateColumns: "1fr", gap: 10, alignItems: "stretch" };
const rulingMain = { border: hairline, background: cardBgHi, padding: 16, display: "flex", gap: 14, alignItems: "flex-start", minWidth: 0 };
const rulingMetrics = { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 };
const expressionBadge = {
  display: "inline-flex",
  maxWidth: "100%",
  marginTop: 9,
  padding: "5px 8px",
  border: `0.5px solid ${accent2}55`,
  background: "rgba(94,234,212,0.055)",
  color: accent2,
  fontSize: 11,
  fontWeight: 900,
  letterSpacing: "0.12em",
  lineHeight: 1.25,
  overflowWrap: "anywhere",
};
const miniBox = { border: hairline, background: "rgba(255,255,255,0.02)", padding: 12 };
const witnessGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 10 };
const witnessCard = { border: hairline, background: "rgba(255,255,255,0.02)", padding: 12, minHeight: 116 };
const appealGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 };
const appealItem = { border: hairline, padding: 12, color: labelLight, background: "rgba(167,139,250,0.06)", lineHeight: 1.45 };
const docHeader = { display: "flex", gap: 12, alignItems: "flex-start", marginBottom: 14 };
const noteBox = { border: hairline, background: "rgba(255,255,255,0.02)", color: labelLight, padding: 13, lineHeight: 1.5 };
const coverageGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(132px, 1fr))", gap: 10, marginTop: 14 };
const miniTrialGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 10 };
const exhibitGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 10 };
const missingLine = { color: "#fb7185", fontSize: 11, lineHeight: 1.4, marginTop: 8 };
const readinessGrid = { display: "grid", gap: 18 };
const sectionH = { color: accent, fontSize: 24, letterSpacing: "0.08em", lineHeight: 1.15, margin: 0 };
const splitList = { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 };
const compactRow = { borderBottom: hairline, color: accent, padding: "9px 0", display: "flex", justifyContent: "space-between", gap: 12, fontWeight: 900 };

function docketRow(active, color) {
  return {
    width: "100%",
    display: "grid",
    gridTemplateColumns: "minmax(0, 1fr) 150px",
    gap: 10,
    alignItems: "center",
    textAlign: "left",
    padding: 12,
    border: `1px solid ${active ? color : "rgba(255,255,255,0.08)"}`,
    background: active ? `${color}12` : "rgba(255,255,255,0.015)",
    cursor: "pointer",
  };
}

function tabButton(active) {
  return {
    border: `1px solid ${active ? accent2 : "rgba(255,255,255,0.12)"}`,
    background: active ? "rgba(157,255,229,0.10)" : "rgba(255,255,255,0.02)",
    color: active ? accent2 : labelLight,
    padding: "10px 14px",
    fontSize: 12,
    fontWeight: 900,
    letterSpacing: "0.14em",
    cursor: "pointer",
  };
}

function miniTrialCard(color) {
  return {
    border: `1px solid ${color}55`,
    background: `${color}0d`,
    padding: 12,
  };
}

function exhibitCard(color) {
  return {
    border: `1px solid ${color}44`,
    background: "rgba(255,255,255,0.02)",
    padding: 12,
    minHeight: 138,
  };
}
