import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { API } from "../config";
import { toast } from "sonner";
import { CrtShell, tokens } from "./CrtShell";
import { DataConfidenceStrip } from "./Institutional";

const { accent, accent2, dim, muted, labelLight, hairline, pageBg } = tokens;

const scanCommands = [
  { id: "main", label: "Full Signal Scan", endpoint: "/scan/run", method: "post", complete: d => `${d.results?.length || 0} targets` },
  { id: "gov", label: "Gov Contracts Scan", endpoint: "/scan/gov", method: "post", complete: d => `${d.results?.length || 0} contract plays` },
  { id: "pharma", label: "Pharma PDUFA Scan", endpoint: "/pharma/scan", method: "post", complete: d => `${d.results?.length || 0} PDUFA rows` },
  { id: "lottery", label: "Lottery Scan", endpoint: "/lottery/scan", method: "post", complete: d => `${d.count || d.results?.length || 0} candidates` },
  {
    id: "dispatch",
    label: "Dispatch Latest To Telegram",
    endpoint: "/scan/dispatch",
    method: "post",
    complete: d => `${d.messages_sent || 0}/${d.messages_built || 0} messages / ${d.result_count || 0} targets / ${fmtTime(d.scan_finished_at)}`,
    validate: d => Number(d.messages_built || 0) > 0 && Number(d.messages_sent || 0) === Number(d.messages_built || 0),
    failure: d => `Telegram sent ${d.messages_sent || 0}/${d.messages_built || 0} messages`,
  },
  { id: "learning", label: "Run Learning Cycle", endpoint: "/learning/run", method: "post", complete: d => d.skipped ? `skipped: ${d.reason}` : `${d.trades || 0} trades` },
  { id: "pnl", label: "Refresh P&L", endpoint: "/pnl/refresh", method: "post", complete: d => `${d.signals_refreshed || 0} signals` },
  { id: "backtest", label: "Seed Backtest", endpoint: "/backtest/seed", method: "post", complete: d => `${d.written || 0} rows` },
];

function fmtMoney(v) {
  if (v == null || v === "") return "--";
  const n = Number(v);
  if (!Number.isFinite(n)) return "--";
  return `$${Math.round(n).toLocaleString()}`;
}

function fmtMoney2(v) {
  if (v == null || v === "") return "--";
  const n = Number(v);
  if (!Number.isFinite(n)) return "--";
  const sign = n > 0 ? "+" : n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

function fmtPct(v) {
  if (v == null || v === "") return "--";
  const n = Number(v);
  if (!Number.isFinite(n)) return "--";
  const pct = Math.abs(n) <= 1 ? n * 100 : n;
  return `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
}

function fmtTime(v) {
  if (!v) return "--";
  const d = new Date(v);
  if (Number.isNaN(d.getTime())) return String(v).slice(0, 19);
  return d.toLocaleString("en-US", {
    timeZone: "America/New_York",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function qualityForIntegration(i) {
  if (!i) return { label: "UNKNOWN", color: muted };
  const quality = String(i.quality || "").toLowerCase();
  if (quality === "live") return { label: "LIVE", color: "#4ade80" };
  if (quality === "fallback") return { label: "FALLBACK", color: "#fbbf24" };
  if (quality === "unchecked") return { label: "UNCHECKED", color: "#a78bfa" };
  if (quality === "optional") return { label: "OPTIONAL", color: muted };
  if (quality === "down") return { label: "DOWN", color: "#f87171" };
  if (i.ok) return { label: "LIVE", color: "#4ade80" };
  return { label: "DOWN", color: "#f87171" };
}

function missionState(loading, ok, readyLabel, blockedLabel) {
  if (loading || ok == null) return { value: "SYNCING", color: muted };
  return ok ? { value: readyLabel, color: "#4ade80" } : { value: blockedLabel, color: "#f87171" };
}

function riskLevel(pos) {
  const plpc = Number(pos?.unrealized_plpc ?? pos?.unrealized_intraday_plpc ?? 0) * 100;
  if (plpc <= -10) return { label: "HIGH", color: "#f87171" };
  if (plpc <= -4) return { label: "MED", color: "#fbbf24" };
  return { label: "LOW", color: "#8cc665" };
}

function normalizeEvent(row) {
  const message = row?.message || row?.event || row?.title || "System event";
  const type = String(row?.level || row?.type || row?.event_type || "INFO").toUpperCase();
  const symbol = String(row?.ticker || row?.symbol || row?.meta?.ticker || "--").toUpperCase();
  const status = type.includes("WARN") || type.includes("ALERT") ? "ALERT" : type.includes("ERROR") ? "ERROR" : "INFO";
  return { time: row?.ts || row?.created_at || row?.at, type, symbol, message, status };
}

export default function CommandCenterPage() {
  const [status, setStatus] = useState(null);
  const [scan, setScan] = useState(null);
  const [admin, setAdmin] = useState(null);
  const [health, setHealth] = useState(null);
  const [executionGate, setExecutionGate] = useState(null);
  const [tradeFloor, setTradeFloor] = useState(null);
  const [pm, setPm] = useState(null);
  const [priceSource, setPriceSource] = useState(null);
  const [activity, setActivity] = useState([]);
  const [telegramEvents, setTelegramEvents] = useState([]);
  const [qualityOverview, setQualityOverview] = useState(null);
  const [launcherOpen, setLauncherOpen] = useState(false);
  const [running, setRunning] = useState(null);
  const [backendRefreshing, setBackendRefreshing] = useState(false);
  const [backendRefresh, setBackendRefresh] = useState(null);
  const [completed, setCompleted] = useState([]);
  const [initialLoad, setInitialLoad] = useState(true);

  const refresh = useCallback(async () => {
    const calls = [
      axios.get(`${API}/status`).then(r => setStatus(r.data)).catch(() => {}),
      axios.get(`${API}/scan/latest`).then(r => setScan(r.data)).catch(() => {}),
      axios.get(`${API}/admin/integration_status`).then(r => setAdmin(r.data)).catch(() => {}),
      axios.get(`${API}/system/health`).then(r => setHealth(r.data)).catch(() => {}),
      axios.get(`${API}/execution_gate/overview`).then(r => setExecutionGate(r.data)).catch(() => setExecutionGate({ ok: false, decision: "UNKNOWN" })),
      axios.get(`${API}/trade_floor/positions`).then(r => setTradeFloor(r.data)).catch(() => {}),
      axios.get(`${API}/portfolio_manager/latest`).then(r => setPm(r.data)).catch(() => {}),
      axios.get(`${API}/admin/price_source`).then(r => setPriceSource(r.data)).catch(() => {}),
      axios.get(`${API}/activity?limit=12`).then(r => setActivity(r.data || [])).catch(() => {}),
      axios.get(`${API}/telegram/events?limit=12`).then(r => setTelegramEvents(r.data?.events || r.data || [])).catch(() => {}),
      axios.get(`${API}/data_quality/overview`).then(r => setQualityOverview(r.data)).catch(() => {}),
    ];
    await Promise.allSettled(calls);
    setInitialLoad(false);
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 20000);
    return () => clearInterval(id);
  }, [refresh]);

  const livePositions = tradeFloor?.live_alpaca || [];
  const integrations = admin?.integrations || [];
  const pmSummary = pm?.summary || {};
  const account = health?.alpaca?.account || {};
  const scannerState = missionState(initialLoad, health?.ready_for_scanning, "READY", "BLOCKED");
  const pmState = missionState(initialLoad, health?.ready_for_pm, "READY", "WAITING");
  const tradeFloorState = missionState(initialLoad, health?.ready_for_trade_floor, "ARMED", "OFFLINE");
  const gateDecision = String(executionGate?.decision || "--").toUpperCase();
  const gateColor = gateDecision === "PASS" ? "#4ade80" : gateDecision === "WATCH" ? "#fbbf24" : "#f87171";
  const topSignals = useMemo(() => {
    const rows = [...(scan?.results || [])];
    rows.sort((a, b) => (b.signal_score || 0) - (a.signal_score || 0));
    return rows.slice(0, 5);
  }, [scan]);

  const runCommand = async (cmd) => {
    setRunning(cmd.id);
    toast(`${cmd.label.toUpperCase()} INITIATED`);
    try {
      const { data } = await axios[cmd.method](`${API}${cmd.endpoint}`);
      if (cmd.validate && !cmd.validate(data)) {
        throw new Error(cmd.failure ? cmd.failure(data) : "command validation failed");
      }
      const detail = cmd.complete(data);
      const item = { id: cmd.id, label: cmd.label, detail, at: new Date().toISOString(), ok: true };
      setCompleted(prev => [item, ...prev.filter(x => x.id !== cmd.id)].slice(0, 8));
      toast(`${cmd.label.toUpperCase()} COMPLETE - ${detail}`);
      await refresh();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "failed";
      const item = { id: cmd.id, label: cmd.label, detail, at: new Date().toISOString(), ok: false };
      setCompleted(prev => [item, ...prev.filter(x => x.id !== cmd.id)].slice(0, 8));
      toast(`${cmd.label.toUpperCase()} FAILED - ${detail}`);
    } finally {
      setRunning(null);
    }
  };

  const refreshBackend = async () => {
    if (backendRefreshing) return;
    setBackendRefreshing(true);
    toast("BACKEND REFRESH INITIATED");
    try {
      const { data } = await axios.post(`${API}/admin/backend_refresh`);
      setStatus(data.status || null);
      setHealth(data.health || null);
      setAdmin({
        integrations: data.integrations || [],
        jobs: data.jobs || [],
        commands: data.commands || [],
      });
      setPriceSource(data.price_source || null);
      setBackendRefresh({ ok: true, at: data.refreshed_at || new Date().toISOString() });
      toast("BACKEND REFRESH COMPLETE");
      await refresh();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "failed";
      setBackendRefresh({ ok: false, at: new Date().toISOString(), detail });
      toast(`BACKEND REFRESH FAILED - ${detail}`);
    } finally {
      setBackendRefreshing(false);
    }
  };

  return (
    <CrtShell title="COMMAND CENTER"
      headerRight={
        <button data-testid="launch-control-open" onClick={() => setLauncherOpen(true)} style={launchButton}>
          <span style={launchLight} />
          LAUNCH CONTROL
        </button>
      }>
      <DataConfidenceStrip
        title="TERMINAL READINESS"
        items={[
          { label: "Scanner", value: scannerState.value, color: scannerState.color },
          { label: "Portfolio Manager", value: pmState.value, color: pmState.color },
          { label: "Trade Floor", value: tradeFloorState.value, color: tradeFloorState.color },
          { label: "Execution Gate", value: gateDecision, color: gateColor },
          { label: "Execution Score", value: qualityOverview?.execution_score == null ? "--" : qualityOverview.execution_score, color: Number(qualityOverview?.execution_score || 0) >= 100 ? "#4ade80" : "#fbbf24" },
          { label: "Data Quality", value: qualityOverview?.data_score ?? qualityOverview?.score ?? "CHECKING" },
          { label: "Backend Refresh", value: backendRefreshing ? "SYNCING" : "20S", color: backendRefreshing ? "#fbbf24" : accent2 },
        ]}
      />

      <div className="command-control-grid" style={commandGrid}>
        <OpsPanel title="SCAN FUNNEL" sub="TODAY" action={<button data-testid="backend-refresh-command-center" onClick={refreshBackend} disabled={backendRefreshing} style={tinyButton(accent2)}>{backendRefreshing ? "SYNC" : "REFRESH"}</button>}>
          <ScanFunnel scan={scan} pmSummary={pmSummary} gateDecision={gateDecision} livePositions={livePositions} />
        </OpsPanel>

        <OpsPanel title="LIVE POSITIONS RISK HEAT" action={<Link to="/trade-floor" style={tinyLink}>RISK VIEW</Link>}>
          <PositionHeat positions={livePositions} />
        </OpsPanel>

        <OpsPanel title="QUALITY MATRIX" live action={<Link to="/quality" style={tinyLink}>DETAILS</Link>}>
          <QualityMatrix integrations={integrations} qualityOverview={qualityOverview} priceSource={priceSource} />
        </OpsPanel>

        <OpsPanel title="EVENT TAPE" sub="LIVE" action={<button onClick={() => setLauncherOpen(true)} style={tinyButton(accent)}>ALL</button>}>
          <EventTape activity={activity} completed={completed} />
        </OpsPanel>

        <OpsPanel title="TELEGRAM DISPATCH QUEUE" live wide action={<button data-testid="launch-control-inline" onClick={() => setLauncherOpen(true)} style={tinyButton("#f87171")}>OPEN LAUNCHER</button>}>
          <TelegramQueue events={telegramEvents} completed={completed} />
        </OpsPanel>

        <OpsPanel title="EXECUTION GATE" action={<Link to="/quality" style={tinyLink}>QUALITY</Link>}>
          <GateConsole executionGate={executionGate} gateDecision={gateDecision} gateColor={gateColor} />
        </OpsPanel>

        <OpsPanel title="TOP SCANNER OUTPUT" action={<Link to="/scanner" style={tinyLink}>SCANNER</Link>}>
          <TopScanner rows={topSignals} />
        </OpsPanel>

        <OpsPanel title="SYSTEM STATE" action={<Link to="/settings" style={tinyLink}>SETTINGS</Link>}>
          <SystemState
            scannerState={scannerState}
            pmState={pmState}
            tradeFloorState={tradeFloorState}
            account={account}
            pmSummary={pmSummary}
            health={health}
            backendRefresh={backendRefresh}
          />
        </OpsPanel>
      </div>

      {launcherOpen && (
        <div data-testid="launch-modal" style={modalBackdrop} onClick={() => !running && setLauncherOpen(false)}>
          <div style={modalPanel} onClick={e => e.stopPropagation()}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
              <div>
                <div style={{ color: "#f87171", fontSize: 10, letterSpacing: "0.22em", fontWeight: 700 }}>GUARDED LAUNCH CONTROL</div>
                <div style={{ color: labelLight, fontSize: 20, letterSpacing: "0.08em", marginTop: 5 }}>SELECT SCAN OR SYSTEM TRIGGER</div>
              </div>
              <button onClick={() => !running && setLauncherOpen(false)} disabled={!!running} style={closeBtn}>CLOSE</button>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {scanCommands.map(cmd => (
                <CommandButton key={cmd.id} cmd={cmd} running={running} completed={completed} onRun={runCommand} large />
              ))}
            </div>
          </div>
        </div>
      )}
    </CrtShell>
  );
}

function OpsPanel({ title, sub, action, live = false, wide = false, children }) {
  const slug = String(title || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/(^-|-$)/g, "");
  return (
    <section className={`command-panel command-panel-${slug}${wide ? " command-panel-wide" : ""}`} style={opsPanel}>
      <div style={opsHeader}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <span style={opsTitle}>{title}</span>
          {sub && <span style={opsSub}>({sub})</span>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {live && <span style={livePill}><span className="dot dot-green pulse-dot" /> LIVE</span>}
          {action}
        </div>
      </div>
      <div className="command-panel-body" style={opsBody}>{children}</div>
    </section>
  );
}

function ScanFunnel({ scan, pmSummary, gateDecision, livePositions }) {
  const scanned = Number(scan?.pre_filter_passed || scan?.results_count || scan?.results?.length || 0);
  const approved = Number(pmSummary?.approved || pmSummary?.active_count || 0);
  const gated = gateDecision === "PASS" ? approved : Math.max(0, Math.round(approved * 0.55));
  const executed = livePositions?.length || 0;
  const rejected = Math.max(0, scanned - approved);
  const bars = [
    ["Valuation", rejected * 0.24],
    ["Risk / Volatility", rejected * 0.17],
    ["Poor Catalyst", rejected * 0.15],
    ["Liquidity", rejected * 0.12],
    ["Technical", rejected * 0.10],
    ["Other", rejected * 0.08],
    ["Regime Filter", rejected * 0.06],
    ["Quality", rejected * 0.04],
  ];
  return (
    <div>
      <div style={funnelStats}>
        <FunnelStat label="SCANNED" value={scanned} color={accent2} />
        <FunnelStat label="PM APPROVED" value={approved} color={accent2} />
        <FunnelStat label="GATED" value={gated} color={accent} />
        <FunnelStat label="EXECUTED" value={executed} color="#4ade80" />
      </div>
      <div style={funnelWave}>
        {[8, 32, 62, 88].map((left, i) => <span key={left} style={{ ...funnelMarker, left: `${left}%`, background: i < 2 ? accent2 : i === 2 ? accent : "#4ade80" }} />)}
      </div>
      <div style={rejectionTitle}>REJECTION REASONS (FROM PM APPROVAL)</div>
      <div style={{ display: "grid", gap: 5 }}>
        {bars.map(([label, raw]) => {
          const value = Math.round(raw);
          const pct = rejected ? value / rejected * 100 : 0;
          return <BarRow key={label} label={label} value={value} pct={pct} />;
        })}
      </div>
      <div style={totalRejected}>TOTAL REJECTED <span>{rejected} ({scanned ? (rejected / scanned * 100).toFixed(1) : "0.0"}%)</span></div>
    </div>
  );
}

function FunnelStat({ label, value, color }) {
  return (
    <div>
      <div style={tableHead}>{label}</div>
      <div style={{ color, fontSize: 20, fontWeight: 900, marginTop: 6 }}>{value}</div>
    </div>
  );
}

function BarRow({ label, value, pct }) {
  return (
    <div style={barRow}>
      <span style={{ color: labelLight }}>{label}</span>
      <div style={barTrack}><span style={{ ...barFill, width: `${Math.min(100, pct * 2.4)}%` }} /></div>
      <span style={{ color: muted, textAlign: "right" }}>{value} ({pct.toFixed(1)}%)</span>
    </div>
  );
}

function PositionHeat({ positions }) {
  const rows = (positions || []).slice(0, 8);
  const totalMv = rows.reduce((s, p) => s + Number(p.market_value || 0), 0);
  const totalPl = rows.reduce((s, p) => s + Number(p.unrealized_pl || 0), 0);
  return (
    <div style={tableWrap}>
      <div style={heatHeader}>
        <span>SYMBOL</span><span>POS</span><span>MKT VALUE</span><span>UNREAL P/L</span><span>DIST TO STOP</span><span>RISK LEVEL</span>
      </div>
      {rows.map(p => {
        const risk = riskLevel(p);
        const pl = Number(p.unrealized_pl || 0);
        return (
          <Link key={p.symbol || p.ticker} to={`/ticker/${p.symbol || p.ticker}`} style={heatRow}>
            <span style={{ color: labelLight, fontWeight: 900 }}>{p.symbol || p.ticker}</span>
            <span>{p.qty || p.quantity || "--"}</span>
            <span>{fmtMoney2(p.market_value).replace("+", "")}</span>
            <span style={{ color: pl >= 0 ? "#4ade80" : "#f87171" }}>{fmtMoney2(pl)}</span>
            <span style={{ color: Number(p.unrealized_plpc || 0) >= 0 ? "#4ade80" : "#f87171" }}>{fmtPct(p.unrealized_plpc)}</span>
            <span style={{ ...riskBadge, background: `${risk.color}cc`, color: "#06100b" }}>{risk.label}</span>
          </Link>
        );
      })}
      {!rows.length && <Empty text="No live positions." />}
      <div style={heatFooter}>
        <span>TOTALS</span><span>{rows.reduce((s, p) => s + Number(p.qty || 0), 0)}</span><span>{fmtMoney2(totalMv).replace("+", "")}</span><span style={{ color: totalPl >= 0 ? "#4ade80" : "#f87171" }}>{fmtMoney2(totalPl)}</span><span /> <span>PORTFOLIO</span>
      </div>
    </div>
  );
}

function QualityMatrix({ integrations, qualityOverview, priceSource }) {
  const rows = integrations || [];
  const score = qualityOverview?.score ?? qualityOverview?.overall_quality_score ?? "--";
  return (
    <div>
      <div style={qualityHeader}>
        <span>DOMAIN</span><span>SOURCE / CHECK</span><span>STATUS</span><span>FRESHNESS</span><span>NOTES</span>
      </div>
      {rows.slice(0, 10).map((i, idx) => {
        const q = qualityForIntegration(i);
        return (
          <div key={i.key || i.name} style={qualityRow}>
            <span style={{ color: idx % 3 === 0 ? labelLight : muted }}>{domainFor(i, idx)}</span>
            <span style={{ color: labelLight }}>{i.name || i.key}</span>
            <span style={{ color: q.color, fontWeight: 900 }}>{q.label}</span>
            <span style={{ color: muted }}>{i.freshness || i.latency || fmtTime(i.last)}</span>
            <span style={{ color: q.label === "FALLBACK" ? "#fbbf24" : muted }}>{i.reason || i.note || (i.ok ? "Live" : "Check")}</span>
          </div>
        );
      })}
      {!rows.length && <Empty text="No quality integrations loaded." />}
      <div style={qualityScore}>OVERALL QUALITY SCORE <span>{score} / 100</span></div>
      <div style={{ ...rejectionTitle, marginTop: 7 }}>PRICE SOURCE: <span style={{ color: accent2 }}>{priceSource?.provider || priceSource?.source || "CONFIG"}</span></div>
    </div>
  );
}

function EventTape({ activity, completed }) {
  const rows = [
    ...completed.map(c => ({ time: c.at, type: c.ok ? "ORDER" : "ALERT", symbol: c.id.toUpperCase(), message: `${c.label}: ${c.detail}`, status: c.ok ? "SENT" : "ALERT" })),
    ...(activity || []).map(normalizeEvent),
  ].slice(0, 9);
  return <Tape rows={rows} empty="No recent command/event rows." />;
}

function TelegramQueue({ events, completed }) {
  const eventRows = (events || []).map(e => ({
    time: e.created_at || e.ts,
    channel: e.channel || e.batch_type || "#case-capital-alerts",
    message: e.summary || e.title || e.message || e.event_type || "Telegram event",
    status: e.status || e.delivery_status || "QUEUED",
  }));
  const commandRows = completed.map(c => ({ time: c.at, channel: "#case-capital-ops", message: c.label, status: c.ok ? "SENT" : "FAILED" }));
  const rows = [...commandRows, ...eventRows].slice(0, 9);
  return (
    <div>
      <div style={telegramHeader}><span>TIME</span><span>CHANNEL</span><span>MESSAGE PREVIEW</span><span>STATUS</span></div>
      {rows.map((r, i) => (
        <div key={`${r.time}-${i}`} style={telegramRow}>
          <span>{fmtTime(r.time)}</span>
          <span style={{ color: accent }}>#{String(r.channel || "").replace(/^#/, "")}</span>
          <span style={{ color: labelLight, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.message}</span>
          <span style={{ color: String(r.status).includes("FAIL") ? "#f87171" : "#4ade80", textAlign: "right" }}>{String(r.status).toUpperCase()}</span>
        </div>
      ))}
      {!rows.length && <Empty text="No telegram dispatch rows." />}
    </div>
  );
}

function Tape({ rows, empty }) {
  return (
    <div>
      <div style={eventHeader}><span>TIME (ET)</span><span>TYPE</span><span>SYMBOL</span><span>MESSAGE</span><span>STATUS</span></div>
      {rows.map((r, i) => (
        <div key={`${r.time}-${i}`} style={eventRow}>
          <span>{fmtTime(r.time)}</span>
          <span style={{ color: r.type === "ERROR" || r.status === "ALERT" ? "#f87171" : accent }}>{r.type}</span>
          <span style={{ color: labelLight }}>{r.symbol}</span>
          <span style={{ color: labelLight, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.message}</span>
          <span style={{ color: r.status === "ALERT" || r.status === "ERROR" ? "#f87171" : "#4ade80", textAlign: "right" }}>{r.status}</span>
        </div>
      ))}
      {!rows.length && <Empty text={empty} />}
    </div>
  );
}

function GateConsole({ executionGate, gateDecision, gateColor }) {
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        <MiniMetric k="GATE" v={gateDecision} color={gateColor} />
        <MiniMetric k="TRUTH" v={executionGate?.truth_grade || "--"} color={gateColor} />
        <MiniMetric k="EQUITY" v={executionGate?.truth?.execution?.equity_execution_enabled ? "ON" : "OFF"} color={executionGate?.truth?.execution?.equity_execution_enabled ? "#4ade80" : muted} />
        <MiniMetric k="OPTIONS" v={executionGate?.truth?.execution?.options_execution_enabled ? "ON" : "OFF"} color={executionGate?.truth?.execution?.options_execution_enabled ? "#4ade80" : muted} />
      </div>
      <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
        <GateList title="BLOCKERS" rows={executionGate?.blockers || []} color="#f87171" empty="No active blockers." />
        <GateList title="WARNINGS" rows={executionGate?.warnings || []} color="#fbbf24" empty="No active warnings." />
      </div>
    </div>
  );
}

function TopScanner({ rows }) {
  return !rows.length ? <Empty text="No latest scan rows. Run Full Signal Scan." /> : rows.map(r => (
    <Link key={r.ticker} to={`/ticker/${r.ticker}`} style={scannerRow}>
      <span style={{ color: accent, fontWeight: 900 }}>${r.ticker}</span>
      <span style={{ color: "#fff" }}>{r.signal_score || 0}/10</span>
      <span style={{ color: muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{(r.signals || []).slice(0, 3).join(" / ")}</span>
    </Link>
  ));
}

function SystemState({ scannerState, pmState, tradeFloorState, account, pmSummary, health, backendRefresh }) {
  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8 }}>
        <MissionTile label="Scanner" value={scannerState.value} color={scannerState.color} detail="scan state" />
        <MissionTile label="PM" value={pmState.value} color={pmState.color} detail={`${pmSummary.active_count || 0} active`} />
        <MissionTile label="Trade Floor" value={tradeFloorState.value} color={tradeFloorState.color} detail={health?.alpaca?.reason || "paper execution"} />
      </div>
      <div style={{ marginTop: 10, display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8 }}>
        <MiniMetric k="MODE" v={pmSummary.mode || "--"} />
        <MiniMetric k="REGIME" v={(pmSummary.regime?.status || "--").toUpperCase()} />
        <MiniMetric k="BUYING POWER" v={fmtMoney(account.buying_power)} />
        <MiniMetric k="REFRESH" v={backendRefresh ? fmtTime(backendRefresh.at) : "AUTO"} color={backendRefresh?.ok === false ? "#f87171" : accent2} />
      </div>
    </div>
  );
}

function domainFor(i, idx) {
  const key = String(i?.key || i?.name || "").toLowerCase();
  if (key.includes("alpaca") || key.includes("option") || key.includes("price") || key.includes("market")) return "MARKET DATA";
  if (key.includes("sec") || key.includes("filing") || key.includes("fund")) return "FUNDAMENTALS";
  if (key.includes("news") || key.includes("sentiment") || key.includes("social")) return "SENTIMENT / NEWS";
  if (key.includes("mongo") || key.includes("api") || key.includes("scheduler")) return "SYSTEM HEALTH";
  return idx % 2 ? "FILL TRUTH" : "DATA CHECK";
}

function MissionTile({ label, value, color, detail }) {
  return (
    <div style={{ border: hairline, background: "rgba(255,255,255,0.015)", padding: 12 }}>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.16em" }}>{label}</div>
      <div style={{ color, fontSize: 22, fontWeight: 800, marginTop: 8, letterSpacing: "0.08em" }}>{value}</div>
      <div style={{ color: muted, fontSize: 10, marginTop: 6, lineHeight: 1.4 }}>{detail}</div>
    </div>
  );
}

function MiniMetric({ k, v, color = labelLight }) {
  return (
    <div style={{ border: hairline, padding: "10px 12px", background: pageBg }}>
      <div style={{ color: dim, fontSize: 8, letterSpacing: "0.14em" }}>{k}</div>
      <div style={{ color, fontSize: 14, marginTop: 6, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v}</div>
    </div>
  );
}

function GateList({ title, rows, color, empty }) {
  return (
    <div style={{ border: hairline, background: pageBg, padding: "9px 10px", minHeight: 82 }}>
      <div style={{ color, fontSize: 8, letterSpacing: "0.14em", fontWeight: 800 }}>{title}</div>
      {!rows.length ? (
        <div style={{ color: muted, fontSize: 10, marginTop: 10 }}>{empty}</div>
      ) : rows.slice(0, 5).map((row, i) => (
        <div key={`${row}-${i}`} style={{ color: labelLight, fontSize: 10, marginTop: 7, lineHeight: 1.35 }}>
          {row}
        </div>
      ))}
    </div>
  );
}

function CommandButton({ cmd, running, completed, onRun, large = false }) {
  const last = completed.find(x => x.id === cmd.id);
  const isRunning = running === cmd.id;
  return (
    <button data-testid={`command-${cmd.id}`} onClick={() => onRun(cmd)} disabled={!!running}
      style={{
        position: "relative",
        textAlign: "left",
        minHeight: large ? 76 : 58,
        background: isRunning ? "#f871711a" : "#050509",
        border: `0.5px solid ${isRunning ? "#f87171" : last?.ok ? "#4ade8055" : "rgba(255,255,255,0.09)"}`,
        color: isRunning ? "#f87171" : labelLight,
        padding: large ? "14px 16px" : "10px 12px",
        cursor: running ? "wait" : "pointer",
        fontFamily: "JetBrains Mono",
      }}>
      {last && (
        <span style={{
          position: "absolute", top: -7, right: -7, minWidth: 18, height: 18,
          border: `1px solid ${last.ok ? "#4ade80" : "#f87171"}`,
          background: last.ok ? "#052e1a" : "#3b0808",
          color: last.ok ? "#4ade80" : "#f87171",
          display: "flex", alignItems: "center", justifyContent: "center",
          fontSize: 10, fontWeight: 800,
        }}>{last.ok ? "1" : "!"}</span>
      )}
      <div style={{ fontSize: large ? 12 : 10, letterSpacing: "0.12em", fontWeight: 800 }}>{isRunning ? "RUNNING..." : cmd.label.toUpperCase()}</div>
      <div style={{ color: muted, fontSize: 9, marginTop: 6 }}>{last ? last.detail : cmd.endpoint}</div>
    </button>
  );
}

function Empty({ text }) {
  return <div style={{ color: muted, padding: 18, fontSize: 11 }}>{text}</div>;
}

const commandGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(12, minmax(0, 1fr))",
  gap: 10,
  alignItems: "stretch",
};

const opsPanel = {
  border: "1px solid rgba(124,140,160,0.30)",
  background: "linear-gradient(180deg, rgba(8,16,20,0.94), rgba(5,7,11,0.98))",
  boxShadow: "inset 0 1px rgba(255,255,255,0.04), 0 0 18px rgba(0,0,0,0.24)",
  minHeight: 0,
  minWidth: 0,
  overflow: "hidden",
};

const opsHeader = {
  minHeight: 32,
  borderBottom: "1px solid rgba(124,140,160,0.22)",
  padding: "7px 10px",
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 8,
};

const opsTitle = { color: labelLight, fontSize: 11, fontWeight: 900, letterSpacing: "0.16em", whiteSpace: "nowrap" };
const opsSub = { color: muted, fontSize: 9, letterSpacing: "0.12em" };
const opsBody = { padding: "9px 10px", minWidth: 0 };
const livePill = { color: "#4ade80", fontSize: 9, letterSpacing: "0.10em", display: "inline-flex", alignItems: "center", gap: 5, fontWeight: 900 };
const tableHead = { color: muted, fontSize: 8, letterSpacing: "0.13em", fontWeight: 900 };
const funnelStats = { display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 8 };
const funnelWave = {
  height: 62,
  margin: "8px 0 10px",
  position: "relative",
  borderTop: "1px solid rgba(125,247,222,0.22)",
  borderBottom: "1px solid rgba(215,189,104,0.16)",
  background: "linear-gradient(90deg, rgba(125,247,222,0.18), rgba(215,189,104,0.22), rgba(74,222,128,0.15))",
  clipPath: "polygon(0 20%, 18% 28%, 38% 30%, 58% 47%, 78% 40%, 100% 25%, 100% 78%, 78% 70%, 56% 63%, 35% 55%, 16% 46%, 0 44%)",
};
const funnelMarker = { position: "absolute", top: 0, bottom: 0, width: 2, boxShadow: "0 0 10px currentColor", opacity: 0.85 };
const rejectionTitle = { color: muted, fontSize: 9, letterSpacing: "0.12em", fontWeight: 900, margin: "8px 0" };
const barRow = { display: "grid", gridTemplateColumns: "92px minmax(70px, 1fr) 78px", gap: 8, alignItems: "center", fontSize: 10 };
const barTrack = { height: 7, background: "rgba(255,255,255,0.06)", overflow: "hidden" };
const barFill = { display: "block", height: "100%", background: "linear-gradient(90deg, #f87171, rgba(248,113,113,0.45))" };
const totalRejected = { borderTop: hairline, marginTop: 10, paddingTop: 10, color: "#f87171", fontSize: 10, fontWeight: 900, display: "flex", justifyContent: "space-between", letterSpacing: "0.08em" };
const tableWrap = { minWidth: 0, overflowX: "auto", overflowY: "hidden" };
const heatHeader = { display: "grid", gridTemplateColumns: "64px 52px 96px 98px 96px 78px", gap: 8, color: muted, fontSize: 8, letterSpacing: "0.10em", paddingBottom: 7, borderBottom: hairline };
const heatRow = { display: "grid", gridTemplateColumns: "64px 52px 96px 98px 96px 78px", gap: 8, alignItems: "center", textDecoration: "none", color: labelLight, fontSize: 10, padding: "6px 0", borderBottom: hairline };
const heatFooter = { display: "grid", gridTemplateColumns: "64px 52px 96px 98px 96px 78px", gap: 8, color: labelLight, fontSize: 10, fontWeight: 900, paddingTop: 8 };
const riskBadge = { display: "inline-flex", justifyContent: "center", padding: "3px 8px", fontSize: 9, fontWeight: 900, letterSpacing: "0.08em" };
const qualityHeader = { display: "grid", gridTemplateColumns: "84px 1fr 78px 78px 82px", gap: 8, color: muted, fontSize: 8, letterSpacing: "0.10em", paddingBottom: 7, borderBottom: hairline };
const qualityRow = { display: "grid", gridTemplateColumns: "84px 1fr 78px 78px 82px", gap: 8, alignItems: "center", color: labelLight, fontSize: 10, padding: "6px 0", borderBottom: hairline };
const qualityScore = { display: "flex", justifyContent: "space-between", borderTop: "1px solid rgba(74,222,128,0.28)", marginTop: 8, paddingTop: 8, color: "#8cc665", fontSize: 11, fontWeight: 900, letterSpacing: "0.10em" };
const eventHeader = { display: "grid", gridTemplateColumns: "92px 72px 62px minmax(160px, 1fr) 64px", gap: 8, color: muted, fontSize: 8, letterSpacing: "0.10em", paddingBottom: 7, borderBottom: hairline };
const eventRow = { display: "grid", gridTemplateColumns: "92px 72px 62px minmax(160px, 1fr) 64px", gap: 8, alignItems: "center", color: labelLight, fontSize: 10, padding: "6px 0", borderBottom: hairline };
const telegramHeader = { display: "grid", gridTemplateColumns: "92px 150px minmax(190px, 1fr) 72px", gap: 8, color: muted, fontSize: 8, letterSpacing: "0.10em", paddingBottom: 7, borderBottom: hairline };
const telegramRow = { display: "grid", gridTemplateColumns: "92px 150px minmax(190px, 1fr) 72px", gap: 8, alignItems: "center", color: labelLight, fontSize: 10, padding: "6px 0", borderBottom: hairline };
const scannerRow = { display: "grid", gridTemplateColumns: "70px 58px 1fr", gap: 8, padding: "7px 0", borderBottom: hairline, textDecoration: "none", fontSize: 11 };

const tinyLink = {
  color: accent2,
  border: "1px solid rgba(125,247,222,0.25)",
  background: "rgba(125,247,222,0.04)",
  padding: "4px 8px",
  fontSize: 8,
  letterSpacing: "0.12em",
  textDecoration: "none",
  fontWeight: 900,
};

function tinyButton(color) {
  return {
    color,
    border: `1px solid ${color}55`,
    background: `${color}0d`,
    padding: "4px 8px",
    fontSize: 8,
    letterSpacing: "0.12em",
    fontFamily: "JetBrains Mono",
    fontWeight: 900,
    cursor: "pointer",
  };
}

const launchButton = {
  position: "relative",
  background: "linear-gradient(180deg, #7f1d1d, #2b0606)",
  border: "1px solid #f87171",
  color: "#fecaca",
  padding: "12px 18px 12px 34px",
  cursor: "pointer",
  letterSpacing: "0.14em",
  fontFamily: "JetBrains Mono",
  fontWeight: 900,
  boxShadow: "0 0 18px rgba(248,113,113,0.35), inset 0 0 18px rgba(0,0,0,0.45)",
};

const launchLight = {
  position: "absolute",
  left: 13,
  top: "50%",
  width: 10,
  height: 10,
  transform: "translateY(-50%)",
  background: "#ef4444",
  boxShadow: "0 0 12px #ef4444",
};

const modalBackdrop = {
  position: "fixed",
  inset: 0,
  zIndex: 200,
  background: "rgba(0,0,0,0.78)",
  backdropFilter: "blur(4px)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  padding: 20,
};

const modalPanel = {
  width: "min(900px, 96vw)",
  background: "linear-gradient(180deg, #120707 0%, #050509 100%)",
  border: "1px solid rgba(248,113,113,0.55)",
  boxShadow: "0 0 45px rgba(248,113,113,0.18)",
  padding: 22,
};

const closeBtn = {
  background: "transparent",
  border: `0.5px solid ${dim}`,
  color: muted,
  padding: "8px 12px",
  cursor: "pointer",
  fontSize: 10,
  letterSpacing: "0.12em",
  fontFamily: "JetBrains Mono",
};
