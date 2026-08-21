import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { CalendarClock, RefreshCw, ShieldCheck, ShieldAlert, Zap, Wrench } from "lucide-react";
import { API } from "../config";
import { CrtShell } from "./CrtShell";
import { DataConfidenceStrip, InstitutionalEmpty } from "./Institutional";

const accent = "#c8a84b";
const accent2 = "#5eead4";
const muted = "#7b8190";
const hairline = "0.5px solid rgba(255,255,255,0.08)";
const bg = "#09090f";
const panel = "#0d0d14";

function tone(status) {
  if (status === "LIVE") return "#4ade80";
  if (status === "WARN") return "#fbbf24";
  if (status === "FALLBACK") return "#f59e0b";
  if (status === "STALE" || status === "MISSING") return "#fb7185";
  if (status === "DOWN") return "#ef4444";
  return muted;
}

function fmtAge(v) {
  if (v == null) return "-";
  if (v < 1) return `${Math.round(v * 60)}S`;
  if (v < 60) return `${Math.round(v)}M`;
  return `${(v / 60).toFixed(1)}H`;
}

export default function QualityPage() {
  const [data, setData] = useState(null);
  const [scheduler, setScheduler] = useState(null);
  const [events, setEvents] = useState([]);
  const [ibkrApps, setIbkrApps] = useState(null);
  const [tab, setTab] = useState("QC");
  const [loading, setLoading] = useState(true);
  const [repulling, setRepulling] = useState(false);
  const [remediating, setRemediating] = useState(false);
  const [watchdogRunning, setWatchdogRunning] = useState(false);
  const [repairing, setRepairing] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [overview, schedulerRows, eventRows, ibkrRows] = await Promise.all([
        axios.get(`${API}/data_quality/overview`, { timeout: 14000 }).then(r => r.data),
        axios.get(`${API}/scheduler/overview`, { timeout: 8000 }).then(r => r.data).catch(() => null),
        axios.get(`${API}/data_quality/events`, { params: { limit: 25 }, timeout: 8000 }).then(r => r.data).catch(() => ({ events: [] })),
        axios.get(`${API}/ibkr/applications`, { timeout: 12000 }).then(r => r.data).catch(e => ({ ok: false, reason: e?.message || "request failed", applications: [] })),
      ]);
      setData(overview);
      setScheduler(schedulerRows);
      setEvents(eventRows.events || []);
      setIbkrApps(ibkrRows);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30000);
    return () => clearInterval(id);
  }, [load]);

  const repull = async () => {
    if (repulling) return;
    setRepulling(true);
    try {
      const { data: fresh } = await axios.post(`${API}/data_quality/refresh`);
      setData(fresh);
      const [schedulerRows, eventRows, ibkrRows] = await Promise.all([
        axios.get(`${API}/scheduler/overview`, { timeout: 8000 }).then(r => r.data).catch(() => null),
        axios.get(`${API}/data_quality/events`, { params: { limit: 25 }, timeout: 8000 }).then(r => r.data).catch(() => ({ events: [] })),
        axios.get(`${API}/ibkr/applications`, { timeout: 12000 }).then(r => r.data).catch(e => ({ ok: false, reason: e?.message || "request failed", applications: [] })),
      ]);
      setScheduler(schedulerRows);
      setEvents(eventRows.events || []);
      setIbkrApps(ibkrRows);
    } finally {
      setRepulling(false);
    }
  };

  const remediate = async () => {
    if (remediating) return;
    setRemediating(true);
    try {
      const { data: result } = await axios.post(`${API}/data_quality/remediate`, null, { params: { limit: 18 } });
      setData(result.overview || result);
      const [schedulerRows, eventRows, ibkrRows] = await Promise.all([
        axios.get(`${API}/scheduler/overview`, { timeout: 8000 }).then(r => r.data).catch(() => null),
        axios.get(`${API}/data_quality/events`, { params: { limit: 25 }, timeout: 8000 }).then(r => r.data).catch(() => ({ events: [] })),
        axios.get(`${API}/ibkr/applications`, { timeout: 12000 }).then(r => r.data).catch(e => ({ ok: false, reason: e?.message || "request failed", applications: [] })),
      ]);
      setScheduler(schedulerRows);
      setEvents(eventRows.events || []);
      setIbkrApps(ibkrRows);
    } finally {
      setRemediating(false);
    }
  };

  const runWatchdog = async () => {
    if (watchdogRunning) return;
    setWatchdogRunning(true);
    try {
      await axios.post(`${API}/scheduler/watchdog`, null, { params: { auto_fix: true, max_repairs: 8, critical_only: false } });
      const { data: schedulerRows } = await axios.get(`${API}/scheduler/overview`, { timeout: 8000 });
      setScheduler(schedulerRows);
    } finally {
      setWatchdogRunning(false);
    }
  };

  const repairSource = async (key) => {
    if (!key || repairing) return;
    setRepairing(key);
    try {
      await axios.post(`${API}/scheduler/repair/${encodeURIComponent(key)}`);
      const { data: schedulerRows } = await axios.get(`${API}/scheduler/overview`, { timeout: 8000 });
      setScheduler(schedulerRows);
    } finally {
      setRepairing("");
    }
  };

  const checks = useMemo(() => data?.checks || [], [data]);
  const critical = checks.filter(c => c.critical);
  const blockers = data?.trading_gate?.blockers || [];
  const fallback = checks.filter(c => c.status === "FALLBACK");
  const warnings = checks.filter(c => c.status === "WARN" || (c.warnings || []).length);
  const gateOk = data?.trading_gate?.decision !== "BLOCK";
  const remediation = data?.remediation || {};
  const attempts = remediation.attempts || [];

  const sourceRows = useMemo(() => [...checks].sort((a, b) => {
    if (a.blocks_trading !== b.blocks_trading) return a.blocks_trading ? -1 : 1;
    if (a.critical !== b.critical) return a.critical ? -1 : 1;
    return (a.score || 0) < (b.score || 0) ? 1 : -1;
  }), [checks]);

  return (
    <CrtShell
      title="QUALITY"
      headerRight={
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", justifyContent: "flex-end" }}>
          <button onClick={remediate} disabled={remediating} style={secondaryButton}>
            <Wrench size={15} className={remediating ? "spin" : ""} />
            {remediating ? "FIXING" : "AUTO FIX DEGRADED"}
          </button>
          <button onClick={repull} disabled={repulling} style={primaryButton}>
            <RefreshCw size={15} className={repulling ? "spin" : ""} />
            {repulling ? "REPULLING" : "INSTANT REPULL"}
          </button>
        </div>
      }
    >
      <div style={tabs}>
        {["QC", "SCHEDULER"].map(k => (
          <button key={k} onClick={() => setTab(k)} style={{ ...tabBtn, ...(tab === k ? tabActive : {}) }}>
            {k === "SCHEDULER" && <CalendarClock size={14} />}
            {k}
          </button>
        ))}
      </div>
      {tab === "SCHEDULER" ? (
        <SchedulerPanel
          scheduler={scheduler}
          loading={loading}
          watchdogRunning={watchdogRunning}
          runWatchdog={runWatchdog}
          repairing={repairing}
          repairSource={repairSource}
        />
      ) : (
      <div style={{ display: "grid", gap: 18 }}>
        <DataConfidenceStrip
          items={[
            { label: "Trading Gate", value: data?.trading_gate?.decision || "CHECKING" },
            { label: "Execution Score", value: data?.execution_score == null ? "--" : `${data.execution_score}`, color: data?.execution_score >= 100 ? "#4ade80" : data?.execution_score >= 65 ? "#fbbf24" : "#f87171" },
            { label: "Critical Checks", value: `${critical.filter(c => c.status === "LIVE" || c.status === "PASS").length}/${critical.length || 0}`, color: critical.every(c => !c.blocks_trading) ? "#4ade80" : "#f87171" },
            { label: "Fallbacks", value: fallback.length, color: fallback.length ? "#fbbf24" : "#4ade80", detail: "display-only tolerated" },
            { label: "Warnings", value: warnings.length, color: warnings.length ? "#fbbf24" : "#4ade80" },
            { label: "Auto Remediation", value: remediation.pending_count ? "PENDING" : "CLEAR", detail: shortStamp(remediation.last_run_at) },
          ]}
        />

        <section style={hero}>
          <div>
            <div style={eyebrow}>QC CONTROL ROOM</div>
            <h1 style={h1}>Data quality before execution.</h1>
            <p style={sub}>
              Fresh cached authority first. Critical stale data gets repulled directly. Display-only fallbacks never slow the order path.
            </p>
          </div>
          <div style={{ ...gateCard, borderColor: gateOk ? "rgba(94,234,212,0.55)" : "rgba(239,68,68,0.65)" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {gateOk ? <ShieldCheck size={28} color={accent2} /> : <ShieldAlert size={28} color="#ef4444" />}
              <div>
                <div style={gateLabel}>TRADING GATE</div>
                <div style={{ ...gateValue, color: gateOk ? accent2 : "#ef4444" }}>{data?.trading_gate?.decision || "CHECKING"}</div>
              </div>
            </div>
            <div style={gatePolicy}>{data?.trading_gate?.policy || "Loading QC policy..."}</div>
            <div style={miniLine}><Zap size={13} /> Max gate delay: {data?.trading_gate?.max_gate_delay_ms || 1500}ms target</div>
          </div>
        </section>

        <section style={metricsGrid}>
          <Metric label="EXECUTION SCORE" value={loading ? "--" : `${data?.execution_score ?? "--"}`} sub={data?.trading_gate?.decision || "GATE"} color={(data?.execution_score || 0) >= 100 ? "#4ade80" : "#fbbf24"} />
          <Metric label="DATA QUALITY" value={loading ? "--" : `${data?.data_score ?? data?.score ?? "--"}`} sub="ALL SOURCES" color={accent} />
          <Metric label="CRITICAL SCORE" value={loading ? "--" : `${data?.critical_score ?? "--"}`} sub={`${critical.length} CRITICAL`} color={accent2} />
          <Metric label="BLOCKERS" value={blockers.length} sub="TRADING IMPACT" color={blockers.length ? "#ef4444" : "#4ade80"} />
          <Metric label="WARNINGS" value={warnings.length} sub="REVIEW" color={warnings.length ? "#fbbf24" : "#4ade80"} />
          <Metric label="FALLBACKS" value={fallback.length} sub="DISPLAY-ONLY RISK" color={fallback.length ? "#f59e0b" : "#4ade80"} />
        </section>

        <Card title="AUTO REMEDIATION">
          <div style={remediationHeader}>
            <div>
              <div style={metricLabel}>LAST RUN</div>
              <div style={{ ...posValue, color: accent2 }}>{shortStamp(remediation.last_run_at)}</div>
            </div>
            <div>
              <div style={metricLabel}>FIXED / CHECKED</div>
              <div style={{ ...posValue, color: accent }}>{remediation.fixed_count ?? 0} / {remediation.attempts_count ?? 0}</div>
            </div>
            <div>
              <div style={metricLabel}>PENDING</div>
              <div style={{ ...posValue, color: (remediation.pending_count || 0) ? "#fbbf24" : "#4ade80" }}>{remediation.pending_count ?? 0}</div>
            </div>
          </div>
          <div style={rowStack}>
            {attempts.slice(0, 8).map((a, idx) => (
              <div key={`${a.key || idx}-${idx}`} style={fixRow}>
                <div>
                  <div style={qTitle}>{a.label || a.key}</div>
                  <div style={qDetail}>{a.action || "probe"} / {a.before_status || "-"} -> {a.after_status || "-"}</div>
                </div>
                <span style={{ ...badge, color: outcomeTone(a.outcome), borderColor: `${outcomeTone(a.outcome)}66`, background: `${outcomeTone(a.outcome)}12` }}>{(a.outcome || "-").toUpperCase()}</span>
                <div style={{ ...qDetail, fontSize: 11 }}>{a.detail || "-"}</div>
                <div style={{ color: a.blocks_trading_after ? "#ef4444" : "#4ade80", fontSize: 11, fontWeight: 900 }}>
                  {a.blocks_trading_after ? "BLOCKS" : a.trading_impact || "NO BLOCK"}
                </div>
              </div>
            ))}
            {!attempts.length && <InstitutionalEmpty title="No remediation history yet." detail="Use Auto Fix Degraded to probe warnings, stale sources, and fallbacks." />}
          </div>
        </Card>

        <Card title="IBKR READ-ONLY COVERAGE">
          <div style={remediationHeader}>
            <div>
              <div style={metricLabel}>GATEWAY</div>
              <div style={{ ...posValue, color: ibkrApps?.ok ? "#4ade80" : "#fbbf24" }}>{ibkrApps?.ok ? "LIVE" : "CHECK"}</div>
            </div>
            <div>
              <div style={metricLabel}>ACTIVE APPS</div>
              <div style={{ ...posValue, color: accent2 }}>{ibkrApps?.summary?.live_apps ?? "--"} / {ibkrApps?.applications?.length ?? "--"}</div>
            </div>
            <div>
              <div style={metricLabel}>AVG IMPACT</div>
              <div style={{ ...posValue, color: accent }}>{ibkrApps?.summary?.avg_impact ?? "--"}</div>
            </div>
          </div>
          <div style={rowStack}>
            {(ibkrApps?.applications || []).slice(0, 10).map(app => (
              <div key={app.key} style={fixRow}>
                <div>
                  <div style={qTitle}>{app.name}</div>
                  <div style={qDetail}>{app.role} / {(app.uses || []).join(", ")}</div>
                </div>
                <span style={{ ...badge, color: app.status === "live" ? "#4ade80" : app.status === "planned" ? "#a78bfa" : "#fbbf24", borderColor: "rgba(94,234,212,0.32)" }}>
                  {String(app.status || "CHECK").toUpperCase()}
                </span>
                <div style={{ ...qDetail, textAlign: "right" }}>IMPACT {app.impact}%</div>
                <div style={{ color: muted, fontSize: 10, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{app.endpoint}</div>
              </div>
            ))}
            {!ibkrApps?.applications?.length && <InstitutionalEmpty title="IBKR coverage not loaded." detail={ibkrApps?.reason || "Gateway may be offline or disabled."} />}
          </div>
        </Card>

        <section style={gridTwo}>
          <Card title="CRITICAL GATE CHECKS">
            <div style={rowStack}>
              {critical.map(row => <QualityRow key={row.key} row={row} />)}
              {!critical.length && <Empty text="No critical checks returned." />}
            </div>
          </Card>
          <Card title="LIVE POSITION AUTHORITY">
            <div style={positionGrid}>
              <PositionTile label="TOTAL POS" value={data?.latest_positions?.totals?.positions ?? "-"} />
              <PositionTile label="OPEN ORDERS" value={data?.latest_positions?.totals?.open_orders ?? "-"} />
              <PositionTile label="TOTAL U/P&L" value={money(data?.latest_positions?.totals?.unrealized_pl)} color={(data?.latest_positions?.totals?.unrealized_pl || 0) >= 0 ? "#4ade80" : "#ef4444"} />
              <PositionTile label="EQUITY U/P&L" value={money(data?.latest_positions?.equities?.unrealized_pl)} />
              <PositionTile label="OPTIONS U/P&L" value={money(data?.latest_positions?.options?.unrealized_pl)} />
              <PositionTile label="SNAPSHOT" value={shortTime(data?.latest_positions?.snapshot_at)} />
            </div>
          </Card>
        </section>

        <Card title="SOURCE LEDGER">
          <div style={{ overflowX: "auto" }}>
            <table style={table}>
              <thead>
                <tr>
                  <Th>SOURCE</Th><Th>STATUS</Th><Th>SCORE</Th><Th>AGE</Th><Th>TRADING</Th><Th>AUTO FIX</Th><Th>DETAIL</Th>
                </tr>
              </thead>
              <tbody>
                {sourceRows.map(row => (
                  <tr key={row.key} style={{ borderTop: hairline }}>
                    <Td strong>{row.label}</Td>
                    <Td><Badge status={row.status} /></Td>
                    <Td color={tone(row.status)}>{row.score}</Td>
                    <Td>{fmtAge(row.age_minutes)}</Td>
                    <Td color={row.blocks_trading ? "#ef4444" : "#4ade80"}>{row.blocks_trading ? "BLOCK" : row.critical ? "CLEAR" : "DISPLAY"}</Td>
                    <Td color={row.auto_fix === "none_needed" ? "#4ade80" : accent}>{(row.auto_fix || "-").replaceAll("_", " ").toUpperCase()}</Td>
                    <Td muted>{[row.detail, ...(row.warnings || [])].filter(Boolean).join(" / ") || "-"}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        <Card title="RECENT QC EVENTS">
          <div style={rowStack}>
            {events.slice(0, 12).map((e, idx) => (
              <div key={idx} style={eventRow}>
                <span style={{ color: accent2 }}>{shortTime(e.created_at || e.generated_at)}</span>
                <span>score {e.score ?? "-"}</span>
                <span>critical {e.critical_score ?? "-"}</span>
                <span style={{ color: (e.summary?.blockers || 0) ? "#ef4444" : "#4ade80" }}>{e.summary?.blockers || 0} blockers</span>
                <span style={{ color: muted }}>{e.force_refreshed ? "force refresh" : "passive check"}</span>
              </div>
            ))}
            {!events.length && <Empty text="No QC events yet." />}
          </div>
        </Card>
      </div>
      )}
    </CrtShell>
  );
}

function money(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return `${n >= 0 ? "+" : "-"}$${Math.abs(n).toFixed(2)}`;
}

function shortTime(v) {
  if (!v) return "-";
  try {
    return new Date(v).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" });
  } catch {
    return "-";
  }
}

function shortStamp(v) {
  if (!v) return "-";
  try {
    return new Date(v).toLocaleString("en-US", { timeZone: "America/New_York", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return "-";
  }
}

function outcomeTone(outcome) {
  if (["live", "refreshed", "repulled", "rechecked_clean"].includes(outcome)) return "#4ade80";
  if (["fallback_confirmed", "provider_conflict_confirmed", "unchecked_confirmed", "needs_configuration", "risk_condition_confirmed"].includes(outcome)) return "#fbbf24";
  if (["still_down", "timeout", "error"].includes(outcome)) return "#ef4444";
  return muted;
}

function Metric({ label, value, sub, color }) {
  return (
    <div style={metric}>
      <div style={metricLabel}>{label}</div>
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

function QualityRow({ row }) {
  return (
    <div style={qrow}>
      <div>
        <div style={qTitle}>{row.label}</div>
        <div style={qDetail}>{row.source} / age {fmtAge(row.age_minutes)}</div>
      </div>
      <Badge status={row.status} />
      <div style={{ ...qScore, color: tone(row.status) }}>{row.score}</div>
      <div style={{ color: row.blocks_trading ? "#ef4444" : "#4ade80", fontSize: 11, fontWeight: 800 }}>
        {row.blocks_trading ? "BLOCK" : "CLEAR"}
      </div>
    </div>
  );
}

function Badge({ status }) {
  return <span style={{ ...badge, color: tone(status), borderColor: `${tone(status)}66`, background: `${tone(status)}12` }}>{status || "-"}</span>;
}

function PositionTile({ label, value, color = accent2 }) {
  return (
    <div style={posTile}>
      <div style={metricLabel}>{label}</div>
      <div style={{ ...posValue, color }}>{value}</div>
    </div>
  );
}

function Empty({ text }) {
  return <div style={{ color: muted, padding: 16, border: hairline }}>{text}</div>;
}

function SchedulerPanel({ scheduler, loading, watchdogRunning, runWatchdog, repairing, repairSource }) {
  const rows = scheduler?.rows || [];
  const jobs = scheduler?.jobs || [];
  const summary = scheduler?.summary || {};
  const last = scheduler?.last_watchdog || {};
  const staleRows = rows.filter(r => r.stale);
  return (
    <div style={{ display: "grid", gap: 18 }}>
      <DataConfidenceStrip
        items={[
          { label: "Sources", value: loading ? "--" : summary.sources ?? rows.length },
          { label: "Live", value: summary.live ?? 0, color: "#4ade80" },
          { label: "Standby", value: summary.standby ?? 0, color: accent },
          { label: "Stale", value: summary.stale ?? 0, color: (summary.stale || 0) ? "#fbbf24" : "#4ade80" },
          { label: "Critical Stale", value: summary.critical_stale ?? 0, color: (summary.critical_stale || 0) ? "#ef4444" : "#4ade80" },
          { label: "Runtime Jobs", value: summary.scheduled_jobs ?? jobs.length },
        ]}
      />

      <section style={hero}>
        <div>
          <div style={eyebrow}>SCHEDULER CONTROL</div>
          <h1 style={h1}>No source gets to rot silently.</h1>
          <p style={sub}>{scheduler?.policy || "Loading scheduler policy..."}</p>
        </div>
        <div style={gateCard}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 14, alignItems: "center" }}>
            <div>
              <div style={gateLabel}>WATCHDOG</div>
              <div style={{ ...gateValue, color: (summary.critical_stale || 0) ? "#ef4444" : accent2 }}>
                {(summary.critical_stale || 0) ? "REPAIR" : "ARMED"}
              </div>
            </div>
            <button onClick={runWatchdog} disabled={watchdogRunning} style={secondaryButton}>
              <Wrench size={15} className={watchdogRunning ? "spin" : ""} />
              {watchdogRunning ? "RUNNING" : "RUN WATCHDOG"}
            </button>
          </div>
          <div style={gatePolicy}>
            Last pass: {shortStamp(last.created_at || last.generated_at)} / repairs {last.summary?.repairs_attempted ?? 0} / stale after {last.summary?.stale_after ?? "-"}.
          </div>
          <div style={miniLine}><Zap size={13} /> Repairs run out-of-band; display feeds do not slow execution gates.</div>
        </div>
      </section>

      <Card title="STALE / REPAIR QUEUE">
        <div style={rowStack}>
          {staleRows.map(row => (
            <SchedulerRow key={row.key} row={row} repairing={repairing === row.key} repairSource={repairSource} />
          ))}
          {!staleRows.length && <InstitutionalEmpty title="No stale scheduler sources." detail="Every declared source is fresh or correctly standing by." />}
        </div>
      </Card>

      <Card title="SOURCE SLA REGISTRY">
        <div style={{ overflowX: "auto" }}>
          <table style={table}>
            <thead>
              <tr>
                <Th>SOURCE</Th><Th>DOMAIN</Th><Th>STATUS</Th><Th>AGE</Th><Th>SLA</Th><Th>TRADING</Th><Th>REPAIR</Th><Th>CADENCE</Th>
              </tr>
            </thead>
            <tbody>
              {rows.map(row => (
                <tr key={row.key} style={{ borderTop: hairline }}>
                  <Td strong>{row.label}</Td>
                  <Td muted>{row.domain}</Td>
                  <Td><Badge status={row.status} /></Td>
                  <Td>{fmtAge(row.age_minutes)}</Td>
                  <Td>{fmtAge(row.max_age_minutes)}</Td>
                  <Td color={row.critical ? (row.stale ? "#ef4444" : "#4ade80") : muted}>{row.critical ? (row.stale ? "IMPACT" : "CLEAR") : "DISPLAY"}</Td>
                  <Td color={accent}>{String(row.repair || "-").replaceAll("_", " ").toUpperCase()}</Td>
                  <Td muted>{row.cadence}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <Card title="LIVE APSCHEDULER JOBS">
        <div style={{ overflowX: "auto" }}>
          <table style={table}>
            <thead>
              <tr><Th>JOB</Th><Th>NEXT RUN</Th><Th>TRIGGER</Th></tr>
            </thead>
            <tbody>
              {jobs.map(job => (
                <tr key={job.id} style={{ borderTop: hairline }}>
                  <Td strong>{job.id}</Td>
                  <Td color={accent2}>{shortStamp(job.next_run_time)}</Td>
                  <Td muted>{job.trigger}</Td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function SchedulerRow({ row, repairing, repairSource }) {
  return (
    <div style={scheduleRepairRow}>
      <div>
        <div style={qTitle}>{row.label}</div>
        <div style={qDetail}>{row.domain} / age {fmtAge(row.age_minutes)} / max {fmtAge(row.max_age_minutes)}</div>
      </div>
      <Badge status={row.status} />
      <div style={{ color: row.critical ? "#ef4444" : "#fbbf24", fontSize: 11, fontWeight: 900 }}>
        {row.critical ? "EXECUTION SOURCE" : "DISPLAY SOURCE"}
      </div>
      <button onClick={() => repairSource(row.key)} disabled={repairing} style={miniButton}>
        {repairing ? "FIXING" : "REPAIR NOW"}
      </button>
    </div>
  );
}

function Th({ children }) { return <th style={th}>{children}</th>; }
function Td({ children, color, muted: isMuted, strong }) {
  return <td style={{ ...td, color: color || (isMuted ? muted : "#cbd5e1"), fontWeight: strong ? 800 : 500 }}>{children}</td>;
}

const primaryButton = {
  height: 42, padding: "0 18px", display: "inline-flex", alignItems: "center", gap: 10,
  border: `1px solid ${accent2}`, background: "rgba(94,234,212,0.07)", color: accent2,
  fontWeight: 900, letterSpacing: "0.14em", fontSize: 12, cursor: "pointer",
};
const secondaryButton = {
  ...primaryButton,
  border: `1px solid ${accent}`,
  background: "rgba(200,168,75,0.08)",
  color: accent,
};
const hero = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) minmax(320px, 440px)", gap: 18, alignItems: "stretch" };
const tabs = { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 };
const tabBtn = { minHeight: 34, display: "inline-flex", alignItems: "center", gap: 8, padding: "0 13px", border: hairline, background: "rgba(255,255,255,0.02)", color: muted, fontSize: 11, fontWeight: 900, letterSpacing: "0.14em", cursor: "pointer" };
const tabActive = { color: accent2, borderColor: "rgba(94,234,212,0.55)", background: "rgba(94,234,212,0.08)" };
const eyebrow = { color: accent2, fontSize: 11, fontWeight: 900, letterSpacing: "0.18em", marginBottom: 12 };
const h1 = { margin: 0, color: accent, fontSize: 36, letterSpacing: "0.08em", lineHeight: 1.08 };
const sub = { color: muted, maxWidth: 760, lineHeight: 1.55, fontSize: 13 };
const gateCard = { border: `1px solid rgba(94,234,212,0.35)`, background: "rgba(5,8,12,0.9)", padding: 18, display: "grid", gap: 16 };
const gateLabel = { color: muted, fontSize: 10, letterSpacing: "0.16em", fontWeight: 800 };
const gateValue = { fontSize: 30, letterSpacing: "0.12em", fontWeight: 900 };
const gatePolicy = { color: "#a6adbb", fontSize: 12, lineHeight: 1.5 };
const miniLine = { color: accent, fontSize: 11, display: "flex", alignItems: "center", gap: 8, letterSpacing: "0.08em" };
const metricsGrid = { display: "grid", gridTemplateColumns: "repeat(5, minmax(0, 1fr))", gap: 12 };
const metric = { border: hairline, background: panel, padding: 14, minWidth: 0 };
const metricLabel = { color: muted, fontSize: 10, letterSpacing: "0.16em", fontWeight: 800 };
const metricValue = { fontSize: 27, fontWeight: 900, marginTop: 10, letterSpacing: "0.08em" };
const metricSub = { color: "#5c6370", fontSize: 10, marginTop: 8, letterSpacing: "0.12em" };
const gridTwo = { display: "grid", gridTemplateColumns: "minmax(0, 1.1fr) minmax(320px, 0.9fr)", gap: 18 };
const card = { border: hairline, background: bg, padding: 18, minWidth: 0 };
const cardTitle = { color: "#cbd5e1", fontWeight: 900, letterSpacing: "0.16em", fontSize: 12, marginBottom: 16 };
const rowStack = { display: "grid", gap: 10 };
const qrow = { display: "grid", gridTemplateColumns: "minmax(0, 1fr) 100px 64px 72px", gap: 12, alignItems: "center", border: hairline, background: "rgba(255,255,255,0.015)", padding: 12 };
const qTitle = { color: "#d1d5db", fontWeight: 900, letterSpacing: "0.08em", fontSize: 12 };
const qDetail = { color: muted, fontSize: 10, marginTop: 6 };
const qScore = { fontSize: 18, fontWeight: 900 };
const badge = { display: "inline-flex", justifyContent: "center", border: "1px solid", padding: "5px 8px", fontSize: 10, fontWeight: 900, letterSpacing: "0.1em" };
const positionGrid = { display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 10 };
const posTile = { border: hairline, padding: 13, background: "rgba(94,234,212,0.025)" };
const posValue = { fontSize: 22, fontWeight: 900, marginTop: 8 };
const table = { width: "100%", borderCollapse: "collapse", minWidth: 920 };
const remediationHeader = { display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10, marginBottom: 14 };
const fixRow = { display: "grid", gridTemplateColumns: "minmax(180px, 0.9fr) 170px minmax(260px, 1.4fr) 150px", gap: 12, alignItems: "center", border: hairline, background: "rgba(255,255,255,0.015)", padding: 12 };
const scheduleRepairRow = { display: "grid", gridTemplateColumns: "minmax(220px, 1fr) 110px 150px 130px", gap: 12, alignItems: "center", border: hairline, background: "rgba(255,255,255,0.015)", padding: 12 };
const miniButton = { minHeight: 34, border: `1px solid ${accent2}`, background: "rgba(94,234,212,0.06)", color: accent2, fontWeight: 900, letterSpacing: "0.12em", fontSize: 10, cursor: "pointer" };
const th = { textAlign: "left", color: "#586174", fontSize: 10, letterSpacing: "0.16em", padding: "0 12px 12px", fontWeight: 900 };
const td = { padding: "13px 12px", fontSize: 12, verticalAlign: "top" };
const eventRow = { display: "grid", gridTemplateColumns: "110px 90px 110px 100px minmax(0, 1fr)", gap: 10, border: hairline, padding: 10, color: "#cbd5e1", fontSize: 11 };
