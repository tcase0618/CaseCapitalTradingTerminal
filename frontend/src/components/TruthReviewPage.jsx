import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { FileCheck2, RefreshCw, Send, ShieldCheck } from "lucide-react";
import { API } from "../config";
import { CrtShell } from "./CrtShell";

const accent = "#c8a84b";
const accent2 = "#5eead4";
const muted = "#7b8190";
const red = "#ef4444";
const green = "#4ade80";
const amber = "#fbbf24";
const hairline = "0.5px solid rgba(255,255,255,0.08)";
const panel = "#0d0d14";
const bg = "#09090f";

export default function TruthReviewPage() {
  const [data, setData] = useState(null);
  const [ledger, setLedger] = useState([]);
  const [packets, setPackets] = useState([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [packetBusy, setPacketBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [overview, ledgerRows, packetRows] = await Promise.all([
        axios.get(`${API}/truth_review/overview`, { params: { persist: false }, timeout: 20000 }).then(r => r.data),
        axios.get(`${API}/truth_review/ledger`, { params: { limit: 80 }, timeout: 12000 }).then(r => r.data).catch(() => ({ events: [] })),
        axios.get(`${API}/truth_review/packets`, { params: { limit: 8 }, timeout: 12000 }).then(r => r.data).catch(() => ({ packets: [] })),
      ]);
      setData(overview);
      setLedger(ledgerRows.events || []);
      setPackets(packetRows.packets || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 60000);
    return () => clearInterval(id);
  }, [load]);

  const refresh = async () => {
    if (refreshing) return;
    setRefreshing(true);
    try {
      const { data: result } = await axios.post(`${API}/truth_review/refresh`, null, { timeout: 30000 });
      setData(result.overview);
      const ledgerRows = await axios.get(`${API}/truth_review/ledger`, { params: { limit: 80 }, timeout: 12000 }).then(r => r.data).catch(() => ({ events: [] }));
      setLedger(ledgerRows.events || []);
    } finally {
      setRefreshing(false);
    }
  };

  const packet = async () => {
    if (packetBusy) return;
    setPacketBusy(true);
    try {
      const { data: result } = await axios.post(`${API}/truth_review/weekly_packet`, null, { timeout: 30000 });
      setPackets([result, ...packets].slice(0, 8));
      await load();
    } finally {
      setPacketBusy(false);
    }
  };

  const systems = data?.systems || {};
  const overall = data?.overall || {};
  const investor = data?.investor_packet || {};
  const holes = overall.holes || [];
  const score = Number(overall.score || 0);

  const topLedger = useMemo(() => ledger.slice(0, 14), [ledger]);

  return (
    <CrtShell
      title="TRUTH REVIEW"
      headerRight={
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <button onClick={packet} disabled={packetBusy} style={secondaryButton}>
            <Send size={15} className={packetBusy ? "spin" : ""} />
            {packetBusy ? "PACKING" : "GENERATE PACKET"}
          </button>
          <button onClick={refresh} disabled={refreshing} style={primaryButton}>
            <RefreshCw size={15} className={refreshing ? "spin" : ""} />
            {refreshing ? "REFRESHING" : "REFRESH TRUTH"}
          </button>
        </div>
      }
    >
      <div style={{ display: "grid", gap: 18 }}>
        <section style={hero}>
          <div>
            <div style={eyebrow}>INSTITUTIONAL PROOF LAYER</div>
            <h1 style={h1}>One review of every desk, every forecast, every gap.</h1>
            <p style={sub}>
              Append-only evidence, closed-trade truth, forecast accountability, QC state, and investor packet logic in one place.
            </p>
          </div>
          <div style={scoreCard}>
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <ShieldCheck size={30} color={score >= 85 ? green : score >= 70 ? amber : red} />
              <div>
                <div style={label}>READINESS SCORE</div>
                <div style={{ ...scoreValue, color: scoreTone(score) }}>{loading ? "--" : `${score.toFixed(1)} / 100`}</div>
              </div>
            </div>
            <div style={{ ...rating, color: scoreTone(score), borderColor: `${scoreTone(score)}66` }}>
              {overall.rating || "SYNCING"}
            </div>
            <div style={packetLine}>{investor.headline || "Truth review loading."}</div>
          </div>
        </section>

        <section style={metricsGrid}>
          <Metric label="SCANNER 30D" value={pct(systems.scanner?.returns?.avg_30d)} sub={`${systems.scanner?.samples?.["30d"] || 0} SAMPLES`} color={metricTone(systems.scanner?.returns?.avg_30d)} />
          <Metric label="OPTIONS CLOSED" value={systems.options?.realized?.sample ?? "--"} sub={`AVG ${pct(systems.options?.realized?.avg_pct)}`} color={metricTone(systems.options?.realized?.avg_pct)} />
          <Metric label="CASE COURT" value={systems.case_court?.record?.graded ?? "--"} sub="FORWARD GRADED" color={accent2} />
          <Metric label="KRONOS W/R" value={pct(systems.kronos?.direction_win_rate)} sub={`${systems.kronos?.calendar_days_scored || 0} DAYS`} color={metricTone((systems.kronos?.direction_win_rate || 0) - 50)} />
          <Metric label="QC GATE" value={systems.qc?.gate_decision || "--"} sub={systems.qc?.truth_grade || "TRUTH"} color={systems.qc?.gate_decision === "BLOCK" ? red : systems.qc?.gate_decision === "WATCH" ? amber : green} />
          <Metric label="LEDGER" value={ledger.length} sub="RECENT EVENTS" color={accent} />
        </section>

        <section style={gridTwo}>
          <Card title="HOLES TO CLOSE">
            <div style={rowStack}>
              {holes.map((h, i) => (
                <div key={`${h}-${i}`} style={holeRow}>
                  <span style={{ color: i === 0 ? red : amber }}>{String(i + 1).padStart(2, "0")}</span>
                  <span>{h}</span>
                </div>
              ))}
              {!holes.length && <Empty text="No holes returned by current review." />}
            </div>
          </Card>
          <Card title="INVESTOR PACKET">
            <div style={packetBox}>
              <div style={packetTitle}>{investor.title || "Case Capital Terminal Truth Review"}</div>
              {(investor.proof_points || []).map((p, i) => <div key={i} style={proofLine}>+ {p}</div>)}
              <div style={{ height: 1, background: "rgba(255,255,255,0.06)", margin: "12px 0" }} />
              {(investor.recommended_next_actions || []).slice(0, 4).map((p, i) => <div key={i} style={actionLine}>{i + 1}. {p}</div>)}
            </div>
          </Card>
        </section>

        <section style={systemGrid}>
          <SystemCard title="SCANNER EDGE" data={[
            ["Latest Scan", shortStamp(systems.scanner?.latest_scan_at)],
            ["Freshness", systems.scanner?.latest_scan_freshness?.label || "-"],
            ["7D Avg / Win", `${pct(systems.scanner?.returns?.avg_7d)} / ${pct(systems.scanner?.returns?.win_7d)}`],
            ["30D Avg / Win", `${pct(systems.scanner?.returns?.avg_30d)} / ${pct(systems.scanner?.returns?.win_30d)}`],
          ]} />
          <SystemCard title="OPTIONS TRUTH" data={[
            ["Trades", systems.options?.trades ?? "-"],
            ["Closed / Active", `${systems.options?.closed ?? "-"} / ${systems.options?.active ?? "-"}`],
            ["Avg / Win", `${pct(systems.options?.realized?.avg_pct)} / ${pct(systems.options?.realized?.win_rate)}`],
            ["Mark Audit", `${systems.options?.mark_audit?.critical || 0} critical / ${systems.options?.mark_audit?.warnings || 0} warn`],
          ]} />
          <SystemCard title="CASE COURT" data={[
            ["Trials", systems.case_court?.latest_trials ?? "-"],
            ["Decision Grade", systems.case_court?.decision_grade ?? "-"],
            ["Alignment", systems.case_court?.advisory_alignment ?? "-"],
            ["Record", systems.case_court?.grade || "-"],
          ]} />
          <SystemCard title="KRONOS" data={[
            ["Snapshots", systems.kronos?.snapshots ?? "-"],
            ["Latest", shortStamp(systems.kronos?.latest_snapshot_at)],
            ["Disagreements", systems.kronos?.disagreements ?? "-"],
            ["Direction W/R", pct(systems.kronos?.direction_win_rate)],
          ]} />
          <SystemCard title="LOTTERY" data={[
            ["Grades", systems.lottery?.grade_count ?? "-"],
            ["EV", pct(systems.lottery?.combined?.ev_per_ticket_pct_haircut)],
            ["Median", pct(systems.lottery?.combined?.median_ticket_pct)],
            ["Status", systems.lottery?.grade || "-"],
          ]} />
          <SystemCard title="QC / EXECUTION" data={[
            ["Truth", `${systems.qc?.truth_grade || "-"} / ${systems.qc?.truth_decision || "-"}`],
            ["Equity", systems.qc?.execution?.equity_execution_enabled ? "ON" : "OFF"],
            ["Options", systems.qc?.execution?.options_execution_enabled ? "ON" : "OFF"],
            ["Blockers", (systems.qc?.blockers || []).length],
          ]} />
        </section>

        <section style={gridTwoWide}>
          <Card title="APPEND-ONLY TRUTH LEDGER">
            <div style={{ overflowX: "auto" }}>
              <table style={table}>
                <thead><tr><Th>TIME</Th><Th>TYPE</Th><Th>TICKER</Th><Th>SOURCE</Th><Th>SUMMARY</Th></tr></thead>
                <tbody>
                  {topLedger.map((row) => (
                    <tr key={row.event_id} style={{ borderTop: hairline }}>
                      <Td>{shortStamp(row.event_at || row.created_at)}</Td>
                      <Td color={accent}>{row.type}</Td>
                      <Td strong>{row.ticker || "-"}</Td>
                      <Td>{row.source}</Td>
                      <Td muted>{summarizeLedger(row)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!topLedger.length && <Empty text="No ledger events yet. Hit Refresh Truth to seed the ledger from existing systems." />}
          </Card>

          <Card title="SCHEDULER / PACKETS">
            <div style={rowStack}>
              {(systems.scheduler?.jobs || []).slice(0, 8).map(job => (
                <div key={job.id} style={jobRow}>
                  <div>
                    <div style={jobName}>{job.name}</div>
                    <div style={jobCron}>{job.cron}</div>
                  </div>
                  <div style={{ color: freshnessTone(job.last_seen_freshness?.label), fontWeight: 900 }}>{job.last_seen_freshness?.label || "unseen"}</div>
                  <div style={jobCron}>{shortStamp(job.last_seen_at)}</div>
                </div>
              ))}
              <div style={{ height: 1, background: "rgba(255,255,255,0.06)" }} />
              {packets.slice(0, 4).map(row => (
                <div key={`${row.week_of}-${row.generated_at}`} style={packetRow}>
                  <FileCheck2 size={15} color={accent2} />
                  <span>{row.week_of || shortStamp(row.generated_at)}</span>
                  <strong style={{ color: scoreTone(row.overall?.score) }}>{row.overall?.rating} {row.overall?.score}/100</strong>
                </div>
              ))}
              {!packets.length && <Empty text="No weekly truth packets generated yet." />}
            </div>
          </Card>
        </section>
      </div>
    </CrtShell>
  );
}

function summarizeLedger(row) {
  const p = row.payload || {};
  if (row.type === "scan_pick") return `${(p.signals || []).slice(0, 4).join(", ")} / score ${p.signal_score ?? "-"}`;
  if (row.type === "pm_decision") return `${p.action || "-"} / PM ${p.pm_score ?? "-"} / RR ${p.risk_reward ?? "-"}`;
  if (row.type === "option_candidate") return `${p.route || "-"} / ${p.ready ? "ready" : "blocked"} / ${p.blocked_reasons?.[0] || p.data_quality || "-"}`;
  if (row.type === "case_court_trial") return `${p.posture || "-"} / defense ${p.defense_score ?? "-"} vs prosecutor ${p.prosecution_score ?? "-"}`;
  if (row.type === "kronos_forecast") return `${p.bias || "-"} / score ${p.kronos_score ?? "-"} / confidence ${p.confidence ?? "-"}`;
  return JSON.stringify(p).slice(0, 110);
}

function pct(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function metricTone(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return muted;
  if (n > 0) return green;
  if (n < 0) return red;
  return amber;
}

function scoreTone(v) {
  const n = Number(v);
  if (n >= 85) return green;
  if (n >= 70) return amber;
  return red;
}

function freshnessTone(v) {
  if (v === "fresh") return green;
  if (v === "watch") return amber;
  if (v === "stale") return red;
  return muted;
}

function shortStamp(v) {
  if (!v) return "-";
  try {
    return new Date(v).toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "-";
  }
}

function Metric({ label, value, sub, color }) {
  return (
    <div style={metric}>
      <div style={labelStyle}>{label}</div>
      <div style={{ ...metricValue, color }}>{value}</div>
      <div style={metricSub}>{sub}</div>
    </div>
  );
}

function Card({ title, children }) {
  return (
    <section style={card}>
      <div style={cardTitle}>{title}</div>
      {children}
    </section>
  );
}

function SystemCard({ title, data }) {
  return (
    <Card title={title}>
      <div style={rowStack}>
        {data.map(([k, v]) => (
          <div key={k} style={lineRow}>
            <span>{k}</span>
            <strong>{v}</strong>
          </div>
        ))}
      </div>
    </Card>
  );
}

function Empty({ text }) {
  return <div style={{ color: muted, padding: 14, border: hairline, fontSize: 12 }}>{text}</div>;
}

function Th({ children }) { return <th style={th}>{children}</th>; }
function Td({ children, color, muted: isMuted, strong }) {
  return <td style={{ ...td, color: color || (isMuted ? muted : "#cbd5e1"), fontWeight: strong ? 900 : 500 }}>{children}</td>;
}

const primaryButton = {
  height: 42, padding: "0 18px", display: "inline-flex", alignItems: "center", gap: 10,
  border: `1px solid ${accent2}`, background: "rgba(94,234,212,0.07)", color: accent2,
  fontWeight: 900, letterSpacing: "0.14em", fontSize: 12, cursor: "pointer",
};
const secondaryButton = { ...primaryButton, border: `1px solid ${accent}`, background: "rgba(200,168,75,0.08)", color: accent };
const hero = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(340px, 480px)", gap: 18, alignItems: "stretch" };
const eyebrow = { color: accent2, fontSize: 11, fontWeight: 900, letterSpacing: "0.18em", marginBottom: 12 };
const h1 = { margin: 0, color: accent, fontSize: 38, letterSpacing: "0.06em", lineHeight: 1.08, maxWidth: 840 };
const sub = { color: muted, maxWidth: 760, lineHeight: 1.55, fontSize: 13 };
const scoreCard = { border: `1px solid rgba(200,168,75,0.32)`, background: "rgba(5,8,12,0.9)", padding: 18, display: "grid", gap: 14 };
const label = { color: muted, fontSize: 10, letterSpacing: "0.16em", fontWeight: 800 };
const scoreValue = { fontSize: 34, letterSpacing: "0.08em", fontWeight: 900 };
const rating = { border: "1px solid", padding: "8px 10px", width: "fit-content", fontSize: 11, letterSpacing: "0.14em", fontWeight: 900 };
const packetLine = { color: "#aeb6c4", fontSize: 12, lineHeight: 1.5 };
const metricsGrid = { display: "grid", gridTemplateColumns: "repeat(6, minmax(0, 1fr))", gap: 12 };
const metric = { border: hairline, background: panel, padding: 14, minWidth: 0 };
const labelStyle = { color: muted, fontSize: 10, letterSpacing: "0.16em", fontWeight: 800 };
const metricValue = { fontSize: 25, fontWeight: 900, marginTop: 10, letterSpacing: "0.08em" };
const metricSub = { color: "#5c6370", fontSize: 10, marginTop: 8, letterSpacing: "0.12em" };
const gridTwo = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(360px, 0.9fr)", gap: 18 };
const gridTwoWide = { display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(360px, 0.8fr)", gap: 18 };
const systemGrid = { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 18 };
const card = { border: hairline, background: bg, padding: 18, minWidth: 0 };
const cardTitle = { color: "#cbd5e1", fontWeight: 900, letterSpacing: "0.16em", fontSize: 12, marginBottom: 16 };
const rowStack = { display: "grid", gap: 10 };
const holeRow = { display: "grid", gridTemplateColumns: "42px minmax(0, 1fr)", gap: 12, alignItems: "start", border: hairline, background: "rgba(255,255,255,0.015)", padding: 12, color: "#d1d5db", fontSize: 12, lineHeight: 1.45 };
const packetBox = { border: hairline, background: "rgba(94,234,212,0.025)", padding: 14 };
const packetTitle = { color: accent, fontWeight: 900, letterSpacing: "0.12em", marginBottom: 12 };
const proofLine = { color: "#cbd5e1", fontSize: 12, marginBottom: 8, lineHeight: 1.4 };
const actionLine = { color: muted, fontSize: 11, marginBottom: 7, lineHeight: 1.45 };
const lineRow = { display: "flex", justifyContent: "space-between", gap: 14, borderBottom: hairline, padding: "8px 0", color: muted, fontSize: 12 };
const table = { width: "100%", borderCollapse: "collapse", minWidth: 860 };
const th = { textAlign: "left", color: "#586174", fontSize: 10, letterSpacing: "0.16em", padding: "0 12px 12px", fontWeight: 900 };
const td = { padding: "12px", fontSize: 12, verticalAlign: "top" };
const jobRow = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) 80px 100px", gap: 12, alignItems: "center", border: hairline, padding: 10 };
const jobName = { color: "#d1d5db", fontWeight: 900, letterSpacing: "0.08em", fontSize: 11 };
const jobCron = { color: muted, fontSize: 10, marginTop: 5 };
const packetRow = { display: "grid", gridTemplateColumns: "20px minmax(0, 1fr) auto", gap: 8, alignItems: "center", color: "#cbd5e1", fontSize: 11, border: hairline, padding: 10 };
