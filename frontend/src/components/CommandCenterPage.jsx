import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { API } from "../config";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg, pageBg } = tokens;

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
    complete: d => `${d.messages_sent || 0}/${d.messages_built || 0} messages · ${d.result_count || 0} targets · ${fmtTime(d.scan_finished_at)}`,
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
  const dbPositions = tradeFloor?.db_positions || [];
  const pending = dbPositions.filter(p => p.fill_status === "PENDING");
  const integrations = admin?.integrations || [];
  const fallbackFeeds = integrations.filter(i => qualityForIntegration(i).label === "FALLBACK");
  const downFeeds = integrations.filter(i => qualityForIntegration(i).label === "DOWN");
  const uncheckedFeeds = integrations.filter(i => qualityForIntegration(i).label === "UNCHECKED");
  const optionalFeeds = integrations.filter(i => qualityForIntegration(i).label === "OPTIONAL");
  const readyCount = [
    health?.ready_for_scanning,
    health?.ready_for_pm,
    health?.ready_for_trade_floor,
    health?.ready_for_journal_learning,
  ].filter(Boolean).length;

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
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", background: cardBg, border: hairline, marginBottom: 22 }}>
        <Stat label="READINESS" value={`${readyCount}/4`} sub="SYSTEM GATES" color={readyCount >= 3 ? "#4ade80" : "#fbbf24"} accentBar />
        <Stat label="LAST SCAN" value={scan?.results?.length || 0} sub={fmtTime(scan?.finished_at || status?.last_scan_at)} color={accent} />
        <Stat label="LIVE POS" value={livePositions.length} sub={`${pending.length} PENDING`} color={accent2} />
        <Stat label="EQUITY" value={fmtMoney(account.equity)} sub={`CASH ${fmtMoney(account.cash)}`} color="#e5e7eb" />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1.15fr 0.85fr", gap: 18 }}>
        <Card title="MISSION STATE" accentColor={accent}
          action={<button data-testid="backend-refresh-command-center" onClick={refreshBackend} disabled={backendRefreshing} style={smallTeal}>{backendRefreshing ? "REFRESHING" : "BACKEND REFRESH"}</button>}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10 }}>
            <MissionTile label="Scanner" value={scannerState.value} color={scannerState.color} detail={initialLoad ? "loading scan state" : `${scan?.pre_filter_passed || 0} passed filter`} />
            <MissionTile label="Portfolio Manager" value={pmState.value} color={pmState.color} detail={initialLoad ? "loading PM state" : `${pmSummary.active_count || 0} active decisions`} />
            <MissionTile label="Trade Floor" value={tradeFloorState.value} color={tradeFloorState.color} detail={initialLoad ? "loading execution state" : (health?.alpaca?.reason || "paper execution")} />
          </div>

          {(health?.blockers || []).length > 0 && (
            <div style={{ marginTop: 14, border: `0.5px solid #f8717144`, background: "#f871710d", padding: 12 }}>
              <div style={{ fontSize: 9, color: "#f87171", letterSpacing: "0.16em", marginBottom: 8 }}>BLOCKERS</div>
              {health.blockers.map((b, i) => (
                <div key={i} style={{ fontSize: 11, color: labelLight, padding: "4px 0" }}>{b}</div>
              ))}
            </div>
          )}

          <div style={{ marginTop: 16, display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 10 }}>
            <MiniMetric k="MODE" v={pmSummary.mode || "--"} />
            <MiniMetric k="REGIME" v={(pmSummary.regime?.status || "--").toUpperCase()} />
            <MiniMetric k="BUYING POWER" v={fmtMoney(account.buying_power)} />
            <MiniMetric k="CLAUDE" v={health?.env?.claude_disabled ? "OFF" : "ON"} color={health?.env?.claude_disabled ? "#4ade80" : "#fbbf24"} />
            <MiniMetric k="BACKEND REFRESH" v={backendRefresh ? fmtTime(backendRefresh.at) : "AUTO 20S"} color={backendRefresh?.ok === false ? "#f87171" : accent2} />
          </div>
        </Card>

        <Card title="DATA QUALITY" accentColor={accent2}
          action={<Link to="/quality" style={smallLink}>DETAILS</Link>}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: 8, marginBottom: 12 }}>
            <QualityBadge label="LIVE" value={integrations.filter(i => qualityForIntegration(i).label === "LIVE").length} color="#4ade80" />
            <QualityBadge label="FALLBACK" value={fallbackFeeds.length} color="#fbbf24" />
            <QualityBadge label="UNCHECKED" value={uncheckedFeeds.length} color="#a78bfa" />
            <QualityBadge label="OPTIONAL" value={optionalFeeds.length} color={muted} />
            <QualityBadge label="DOWN" value={downFeeds.length} color="#f87171" />
          </div>
          <div style={{ fontSize: 10, color: muted, letterSpacing: "0.08em", marginBottom: 8 }}>
            PRICE SOURCE: <span style={{ color: accent2 }}>{priceSource?.provider || priceSource?.source || "YFINANCE / CONFIG"}</span>
          </div>
          <div style={{ maxHeight: 214, overflowY: "auto", borderTop: hairline }}>
            {integrations.map(i => {
              const q = qualityForIntegration(i);
              return (
                <div key={i.key} style={{ display: "grid", gridTemplateColumns: "1fr 86px 82px", gap: 8, padding: "8px 0", borderBottom: hairline, alignItems: "center" }}>
                  <span style={{ color: labelLight, fontSize: 11 }}>
                    {i.name}
                    {(i.reason || i.detail) && (
                      <span style={{ display: "block", color: dim, fontSize: 9, marginTop: 2, lineHeight: 1.35 }}>
                        {typeof i.detail === "string" ? i.detail : i.reason}
                      </span>
                    )}
                  </span>
                  <span style={{ color: q.color, fontSize: 10, fontWeight: 700, letterSpacing: "0.1em" }}>{q.label}</span>
                  <span style={{ color: dim, fontSize: 9, textAlign: "right" }}>{fmtTime(i.last)}</span>
                </div>
              );
            })}
          </div>
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 18 }}>
        <Card title="EXECUTION GATE" accentColor={gateColor}
          action={<Link to="/quality" style={smallLink}>QUALITY</Link>}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
            <MiniMetric k="GATE" v={gateDecision} color={gateColor} />
            <MiniMetric k="TRUTH" v={executionGate?.truth_grade || "--"} color={gateColor} />
            <MiniMetric k="EQUITY" v={executionGate?.truth?.execution?.equity_execution_enabled ? "ON" : "OFF"} color={executionGate?.truth?.execution?.equity_execution_enabled ? "#4ade80" : muted} />
            <MiniMetric k="OPTIONS" v={executionGate?.truth?.execution?.options_execution_enabled ? "ON" : "OFF"} color={executionGate?.truth?.execution?.options_execution_enabled ? "#4ade80" : muted} />
          </div>
          <div style={{ marginTop: 12, display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <GateList title="BLOCKERS" rows={executionGate?.blockers || []} color="#f87171" empty="No active blockers." />
            <GateList title="WARNINGS" rows={executionGate?.warnings || []} color="#fbbf24" empty="No active warnings." />
          </div>
        </Card>
        <Card title="KILL SWITCHES" accentColor="#f87171">
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 10 }}>
            <MiniMetric k="GLOBAL" v={executionGate?.kill_switches?.global ? "KILLED" : "CLEAR"} color={executionGate?.kill_switches?.global ? "#f87171" : "#4ade80"} />
            <MiniMetric k="EQUITY" v={executionGate?.kill_switches?.equity ? "KILLED" : "CLEAR"} color={executionGate?.kill_switches?.equity ? "#f87171" : "#4ade80"} />
            <MiniMetric k="OPTIONS" v={executionGate?.kill_switches?.options ? "KILLED" : "CLEAR"} color={executionGate?.kill_switches?.options ? "#f87171" : "#4ade80"} />
            <MiniMetric k="QC STRICT" v={executionGate?.kill_switches?.qc_strict ? "ON" : "OFF"} color={executionGate?.kill_switches?.qc_strict ? "#fbbf24" : muted} />
          </div>
          <div style={{ marginTop: 12, color: muted, fontSize: 10, lineHeight: 1.55 }}>
            TICKER KILL LIST: {(executionGate?.kill_switches?.ticker_kill_list || []).join(", ") || "--"}<br />
            SECTOR KILL LIST: {(executionGate?.kill_switches?.sector_kill_list || []).join(", ") || "--"}
          </div>
        </Card>
        <Card title="SCAN HYGIENE" accentColor={accent}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <MiniMetric k="BAD TICKERS" v={scan?.ticker_hygiene?.rejected_count || 0} color={(scan?.ticker_hygiene?.rejected_count || 0) ? "#fbbf24" : "#4ade80"} />
            <MiniMetric k="LAST SCAN" v={fmtTime(scan?.finished_at || status?.last_scan_at)} />
          </div>
          <div style={{ marginTop: 12, maxHeight: 74, overflowY: "auto", borderTop: hairline }}>
            {(scan?.ticker_hygiene?.rejected || []).slice(0, 6).map((r, i) => (
              <div key={`${r.ticker}-${i}`} style={{ display: "grid", gridTemplateColumns: "50px 1fr", gap: 8, borderBottom: hairline, padding: "6px 0", fontSize: 10 }}>
                <span style={{ color: "#fbbf24", fontWeight: 800 }}>${r.ticker || "--"}</span>
                <span style={{ color: muted }}>{r.reason}</span>
              </div>
            ))}
            {!(scan?.ticker_hygiene?.rejected || []).length && <Empty text="No ticker hygiene rejects in latest scan." />}
          </div>
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 18 }}>
        <Card title="SCAN COMMANDS" accentColor="#f87171"
          action={<button data-testid="launch-control-inline" onClick={() => setLauncherOpen(true)} style={smallDanger}>OPEN LAUNCHER</button>}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
            {scanCommands.slice(0, 6).map(cmd => (
              <CommandButton key={cmd.id} cmd={cmd} running={running} completed={completed} onRun={runCommand} />
            ))}
          </div>
        </Card>

        <Card title="TOP SCANNER OUTPUT" accentColor={accent}>
          {!topSignals.length ? <Empty text="No latest scan rows. Run Full Signal Scan." /> : topSignals.map(r => (
            <Link key={r.ticker} to={`/ticker/${r.ticker}`} style={{ display: "grid", gridTemplateColumns: "70px 58px 1fr", gap: 8, padding: "8px 0", borderBottom: hairline, textDecoration: "none" }}>
              <span style={{ color: accent, fontWeight: 700 }}>${r.ticker}</span>
              <span style={{ color: "#fff" }}>{r.signal_score || 0}/10</span>
              <span style={{ color: muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{(r.signals || []).slice(0, 3).join(" / ")}</span>
            </Link>
          ))}
        </Card>

        <Card title="RECENT COMPLETIONS" accentColor="#4ade80">
          {!completed.length ? <Empty text="Launch completions will appear here." /> : completed.map(item => (
            <div key={`${item.id}-${item.at}`} style={{ display: "grid", gridTemplateColumns: "1fr 62px", gap: 8, padding: "8px 0", borderBottom: hairline }}>
              <div>
                <div style={{ color: item.ok ? "#4ade80" : "#f87171", fontSize: 11, fontWeight: 700 }}>{item.label}</div>
                <div style={{ color: muted, fontSize: 10, marginTop: 3 }}>{item.detail}</div>
              </div>
              <div style={{ color: dim, fontSize: 9, textAlign: "right" }}>{fmtTime(item.at)}</div>
            </div>
          ))}
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 }}>
        <Card title="ORDER WATCH" accentColor={accent2}
          action={<Link to="/trade-floor" style={smallLink}>TRADE FLOOR</Link>}>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
            <MiniMetric k="LIVE ALPACA POSITIONS" v={livePositions.length} color={accent2} />
            <MiniMetric k="PENDING LOCAL ORDERS" v={pending.length} color={pending.length ? "#fbbf24" : labelLight} />
          </div>
          {pending.slice(0, 5).map(p => (
            <div key={`${p.ticker}-${p.created_at}`} style={{ display: "grid", gridTemplateColumns: "64px 1fr 90px", gap: 8, borderTop: hairline, padding: "8px 0", fontSize: 11 }}>
              <span style={{ color: "#fbbf24", fontWeight: 700 }}>${p.ticker}</span>
              <span style={{ color: muted }}>LIMIT {p.limit_price ? `$${Number(p.limit_price).toFixed(2)}` : "--"}</span>
              <span style={{ color: dim, textAlign: "right" }}>{fmtTime(p.created_at)}</span>
            </div>
          ))}
        </Card>

        <Card title="ACTIVITY TAPE" accentColor={accent}>
          {!activity.length ? <Empty text="No recent activity rows." /> : activity.slice(0, 8).map((a, i) => (
            <div key={i} style={{ display: "grid", gridTemplateColumns: "92px 1fr", gap: 10, padding: "7px 0", borderBottom: hairline }}>
              <span style={{ color: dim, fontSize: 9 }}>{fmtTime(a.ts || a.created_at)}</span>
              <span style={{ color: labelLight, fontSize: 11, lineHeight: 1.45 }}>{a.message || a.event || JSON.stringify(a).slice(0, 120)}</span>
            </div>
          ))}
        </Card>
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

function QualityBadge({ label, value, color }) {
  return (
    <div style={{ border: `0.5px solid ${color}44`, background: `${color}10`, padding: "9px 10px" }}>
      <div style={{ color, fontSize: 18, fontWeight: 800 }}>{value}</div>
      <div style={{ color: muted, fontSize: 8, letterSpacing: "0.14em", marginTop: 3 }}>{label}</div>
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

const smallLink = {
  color: accent2,
  fontSize: 9,
  letterSpacing: "0.14em",
  textDecoration: "none",
  fontWeight: 700,
};

const smallDanger = {
  background: "transparent",
  border: "0.5px solid #f87171",
  color: "#f87171",
  fontSize: 9,
  padding: "5px 10px",
  cursor: "pointer",
  letterSpacing: "0.12em",
  fontFamily: "JetBrains Mono",
  fontWeight: 700,
};

const smallTeal = {
  background: "transparent",
  border: `0.5px solid ${accent2}`,
  color: accent2,
  fontSize: 9,
  padding: "5px 10px",
  cursor: "pointer",
  letterSpacing: "0.12em",
  fontFamily: "JetBrains Mono",
  fontWeight: 700,
};

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
