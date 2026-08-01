import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { Gavel, RefreshCw, ShieldAlert, ShieldCheck, Scale } from "lucide-react";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, muted, labelLight, hairline, cardBg, cardBgHi } = tokens;

const postureColors = {
  COURT_SUPPORTS_PM: "#4ade80",
  BULLISH_WATCH: "#fbbf24",
  EVIDENCE_CONFLICT: "#a78bfa",
  COURT_OBJECTS: "#fb7185",
  REQUIRES_CLEANER_DATA: "#ef4444",
};

export default function CaseCourtPage() {
  const [data, setData] = useState(null);
  const [selectedTicker, setSelectedTicker] = useState(null);
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
  const readiness = summary.requires_cleaner_data ? "HOLD AUTHORITY" : "ADVISORY READY";

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
      <div style={hero}>
        <div>
          <div style={eyebrow}>READ-ONLY ADVERSARIAL REVIEW</div>
          <h1 style={h1}>Defense argues upside. Prosecutor attacks risk. PM remains judge.</h1>
          <p style={sub}>
            Case Court reviews scanner candidates with existing terminal evidence only. It does not execute, size, route, or override PM authority.
          </p>
        </div>
        <div style={authorityBox}>
          <Scale size={30} color={accent} />
          <div>
            <div style={smallLabel}>AUTHORITY</div>
            <div style={{ color: accent2, fontWeight: 900, letterSpacing: "0.16em" }}>READ ONLY</div>
            <div style={{ color: muted, fontSize: 12, marginTop: 6 }}>{readiness}</div>
          </div>
        </div>
      </div>

      {error && <div style={errorBox}><ShieldAlert size={16} /> {error}</div>}

      <div style={statRow}>
        <Stat label="TRIALS" value={loading ? "--" : summary.trials ?? trials.length} sub="LATEST SCAN DOCKET" color={accent} accentBar />
        <Stat label="SUPPORTS PM" value={summary.supports_pm ?? 0} sub="ADVISORY ONLY" color="#4ade80" />
        <Stat label="BULL WATCH" value={summary.bullish_watch ?? 0} sub="APPEAL ELIGIBLE" color="#fbbf24" />
        <Stat label="OBJECTS" value={summary.objects ?? 0} sub="PROSECUTOR LEADS" color="#fb7185" />
        <Stat label="CONFLICTS" value={summary.conflicts ?? 0} sub="MIXED RECORD" color="#a78bfa" />
        <Stat label="QC HOLDS" value={summary.requires_cleaner_data ?? 0} sub="CLEAN DATA FIRST" color={summary.requires_cleaner_data ? "#ef4444" : "#4ade80"} />
      </div>

      <div style={layout}>
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

        <div>
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
        </div>
      </div>
    </CrtShell>
  );
}

function Ruling({ trial }) {
  const posture = trial.judge?.advisory_posture || "UNKNOWN";
  const color = postureColors[posture] || muted;
  return (
    <div style={rulingGrid}>
      <div style={rulingMain}>
        <Gavel size={32} color={color} />
        <div>
          <div style={smallLabel}>ADVISORY POSTURE</div>
          <div style={{ color, fontSize: 24, fontWeight: 900, letterSpacing: "0.14em" }}>{labelPosture(posture)}</div>
          <div style={{ color: labelLight, marginTop: 8, lineHeight: 1.5 }}>{trial.judge?.detail}</div>
        </div>
      </div>
      <Mini label="Expression Hint" value={trial.judge?.expression_hint || "-"} color={accent2} />
      <Mini label="Defense" value={trial.defense?.score ?? "-"} color="#4ade80" />
      <Mini label="Prosecutor" value={trial.prosecution?.score ?? "-"} color="#fb7185" />
      <Mini label="Scan Age" value={trial.scan_age_hours == null ? "-" : `${trial.scan_age_hours}H`} color={trial.scan_age_hours > 26 ? "#f87171" : accent} />
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

const hero = {
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) 260px",
  gap: 18,
  alignItems: "stretch",
  marginBottom: 22,
};
const eyebrow = { color: accent2, fontSize: 11, letterSpacing: "0.18em", fontWeight: 900, marginBottom: 10 };
const h1 = { color: accent, fontSize: 30, lineHeight: 1.05, letterSpacing: "0.08em", fontWeight: 900, margin: 0 };
const sub = { color: muted, maxWidth: 760, marginTop: 12, fontSize: 13, lineHeight: 1.55 };
const authorityBox = {
  background: cardBg,
  border: hairline,
  padding: 18,
  display: "flex",
  gap: 14,
  alignItems: "center",
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
const statRow = { display: "flex", background: cardBg, border: hairline, marginBottom: 22, overflowX: "auto" };
const layout = { display: "grid", gridTemplateColumns: "360px minmax(0, 1fr)", gap: 22, alignItems: "start" };
const docket = { display: "grid", gap: 8, maxHeight: 960, overflowY: "auto" };
const tickerStyle = { color: accent, fontWeight: 900, fontSize: 20, letterSpacing: "0.10em" };
const metaLine = { color: muted, fontSize: 11, letterSpacing: "0.06em", marginTop: 4 };
const postureText = { fontSize: 10, letterSpacing: "0.12em", fontWeight: 900, maxWidth: 140 };
const lawyerGrid = { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22 };
const evidenceRow = {
  border: hairline,
  background: "rgba(255,255,255,0.02)",
  padding: 10,
  display: "grid",
  gridTemplateColumns: "minmax(0, 1fr) 46px",
  gap: 10,
  alignItems: "center",
};
const rulingGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.5fr) repeat(4, minmax(110px, 1fr))", gap: 10, alignItems: "stretch" };
const rulingMain = { border: hairline, background: cardBgHi, padding: 16, display: "flex", gap: 14, alignItems: "center" };
const miniBox = { border: hairline, background: "rgba(255,255,255,0.02)", padding: 12 };
const witnessGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))", gap: 10 };
const witnessCard = { border: hairline, background: "rgba(255,255,255,0.02)", padding: 12, minHeight: 116 };
const appealGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 };
const appealItem = { border: hairline, padding: 12, color: labelLight, background: "rgba(167,139,250,0.06)", lineHeight: 1.45 };

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
