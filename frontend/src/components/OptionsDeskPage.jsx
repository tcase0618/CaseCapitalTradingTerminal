import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";
import TradingViewMiniChart from "./TradingViewMiniChart";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12, verticalAlign: "top" };
const ROUTE_COLOR = { EQUITY: "#5eead4", OPTION: "#c8a84b", BOTH: "#4ade80", PASS: "#f87171" };

export default function OptionsDeskPage() {
  const [account, setAccount] = useState(null);
  const [candidates, setCandidates] = useState(null);
  const [positions, setPositions] = useState(null);
  const [orders, setOrders] = useState(null);
  const [risk, setRisk] = useState(null);
  const [trades, setTrades] = useState(null);
  const [leaps, setLeaps] = useState(null);
  const [activeView, setActiveView] = useState("DESK");
  const [selected, setSelected] = useState(null);
  const [selectedPosition, setSelectedPosition] = useState(null);
  const [lseContext, setLseContext] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const load = useCallback(async () => {
    const [acct, cand, pos, ord, riskCheck, tradeSet, leapsSet] = await Promise.all([
      axios.get(`${API}/options_desk/account`).catch(e => ({ data: { ok: false, reason: e.message } })),
      axios.get(`${API}/options_desk/candidates`).catch(() => ({ data: null })),
      axios.get(`${API}/options_desk/positions`).catch(() => ({ data: { positions: [] } })),
      axios.get(`${API}/options_desk/orders`).catch(() => ({ data: { orders: [] } })),
      axios.get(`${API}/options_desk/risk`).catch(() => ({ data: null })),
      axios.get(`${API}/options_desk/trades?sync_live=false`).catch(() => ({ data: { trades: [] } })),
      axios.get(`${API}/options_desk/leaps`).catch(() => ({ data: null })),
    ]);
    setAccount(acct.data);
    setCandidates(cand.data);
    setPositions(pos.data);
    setOrders(ord.data);
    setRisk(riskCheck.data);
    setTrades(tradeSet.data);
    setLeaps(leapsSet.data);
  }, []);

  useEffect(() => { load(); }, [load]);

  const rows = candidates?.candidates || [];
  const deskRows = rows.filter(r => r.route !== "EQUITY");
  const readyRows = deskRows.filter(r => r.manual_fire_ready);
  const selectedTicket = selected && selected.route !== "EQUITY" ? selected : readyRows[0] || deskRows[0];
  const acct = account?.account || {};
  const deskSummary = {
    total: deskRows.length,
    option: deskRows.filter(r => r.route === "OPTION").length,
    both: deskRows.filter(r => r.route === "BOTH").length,
    pass: deskRows.filter(r => r.route === "PASS").length,
    ready: readyRows.length,
  };
  const dataPolicy = candidates?.options_data_policy || {};
  const openPositions = positions?.positions || [];
  const selectedOpenPosition = selectedPosition || openPositions[0] || null;
  const tradeBySymbol = useMemo(() => {
    const map = {};
    (trades?.trades || []).forEach(t => { if (t.symbol) map[t.symbol] = t; });
    return map;
  }, [trades]);
  const riskBySymbol = useMemo(() => {
    const map = {};
    (risk?.checks || []).forEach(c => { if (c.symbol) map[c.symbol] = c; });
    return map;
  }, [risk]);

  useEffect(() => {
    let cancelled = false;
    const ticker = selectedTicket?.ticker;
    if (!ticker) {
      setLseContext(null);
      return () => { cancelled = true; };
    }
    setLseContext({ loading: true, ticker });
    Promise.all([
      axios.get(`${API}/data/lse/options/${ticker}?limit=24&max_dte=90`).catch(e => ({ data: { error: e.message, rows: [] } })),
      axios.get(`${API}/data/lse/options_flow?underlying=${ticker}&limit=24&max_dte=90`).catch(e => ({ data: { error: e.message, rows: [] } })),
    ]).then(([chain, flowSet]) => {
      if (!cancelled) {
        setLseContext({
          loading: false,
          ticker,
          chain: chain.data,
          flow: flowSet.data,
        });
      }
    });
    return () => { cancelled = true; };
  }, [selectedTicket?.ticker]);

  const refresh = async () => {
    setBusy(true);
    setMessage("");
    try {
      const r = await axios.post(`${API}/options_desk/candidates/refresh`);
      setCandidates(r.data);
      setSelected(((r.data.candidates || []).filter(row => row.route !== "EQUITY"))[0] || null);
      setMessage("OPTIONS CANDIDATES REFRESHED");
    } finally {
      setBusy(false);
    }
  };

  const execute = async () => {
    if (!selectedTicket) return;
    setBusy(true);
    setMessage("");
    try {
      const r = await axios.post(`${API}/options_desk/execute`, { candidate_id: selectedTicket.candidate_id });
      setMessage(r.data.ok ? `ORDER SENT ${r.data.order?.symbol || selectedTicket.ticker}` : `BLOCKED: ${r.data.reason}`);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const runRiskCheck = async () => {
    setBusy(true);
    setMessage("");
    try {
      const r = await axios.post(`${API}/options_desk/risk/check`);
      setRisk(r.data);
      setMessage(`RISK CHECK: ${r.data.positions_checked || 0} POSITIONS / ${r.data.closed?.length || 0} CLOSED`);
      await load();
    } finally {
      setBusy(false);
    }
  };

  const syncDesk = async () => {
    setBusy(true);
    setMessage("");
    try {
      const r = await axios.post(`${API}/options_desk/sync`);
      setRisk(r.data.risk);
      setMessage(`SYNC: ${r.data.fill?.upserted || 0} FILLS / ${r.data.risk?.positions_checked || 0} RISK CHECKS`);
      await load();
    } finally {
      setBusy(false);
    }
  };

  return (
    <CrtShell title="OPTIONS DESK"
      headerRight={
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <button onClick={refresh} disabled={busy} style={buttonStyle(accent)}>{busy ? "WORKING" : "REFRESH CANDIDATES"}</button>
          <button onClick={runRiskCheck} disabled={busy} style={buttonStyle("#f87171")}>RISK CHECK</button>
          <button onClick={syncDesk} disabled={busy} style={buttonStyle(accent2)}>SYNC DESK</button>
        </div>
      }>
      <div style={notice}>
        SEPARATE PAPER OPTIONS ACCOUNT. PM ROUTES THE EXPRESSION. EQUITY TRADE FLOOR IS NOT CONNECTED TO THIS DESK.
      </div>

      <div style={tabStrip}>
        {["DESK", "LEAPS"].map(view => (
          <button key={view} onClick={() => setActiveView(view)} style={tabButton(activeView === view)}>
            {view === "DESK" ? "OPTIONS DESK" : "LEAPS SLEEVE"}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="DESK STATUS" value={account?.ok ? "ARMED" : "DISABLED"} sub={account?.reason || "OPTIONS PAPER"} color={account?.ok ? "#4ade80" : "#f87171"} accentBar />
        <Stat label="EQUITY BASIS" value={`$${Number(candidates?.options_equity_basis || 20000).toLocaleString()}`} sub="OPTIONS PM" color={accent} />
        <Stat label="BUYING POWER" value={acct.buying_power ? `$${Number(acct.buying_power).toFixed(0)}` : "-"} sub={acct.status || "NO ACCOUNT"} color={accent2} />
        <Stat label="READY" value={deskSummary.ready || 0} sub={`${deskSummary.total || 0} DESK CANDIDATES`} color="#4ade80" />
        <Stat label="OPTION/BOTH" value={(deskSummary.option || 0) + (deskSummary.both || 0)} sub="PM ROUTED" color="#fbbf24" />
        <Stat label="DAILY PREMIUM" value={`$${Number(account?.daily_premium_used || 0).toFixed(0)}`} sub={`/ $${Number(account?.daily_premium_cap || 4000).toFixed(0)} CAP`} color="#fbbf24" />
        <Stat label="LIVE DATA" value={`${dataPolicy.alpaca_refreshes_used ?? 0}/${dataPolicy.alpaca_refresh_limit ?? 18}`} sub="SCORE-FIRST ALPACA" color={accent2} />
        <Stat label="HARD STOP" value="-20%" sub={`${risk?.positions_checked || 0} OPEN CHECKED`} color="#f87171" />
        <Stat label="THETA WATCH" value={(risk?.checks || []).filter(c => c.theta_status === "WATCH").length} sub="DECAY FLAGS" color="#fbbf24" />
      </div>

      <div style={dataPolicyBox}>
        ALPACA OPTIONS DATA IS RESERVED FOR THE HIGHEST-RATED PM OPTION CANDIDATES. EQUITY-ONLY ROUTES SKIP LIVE CHAIN REFRESH.
      </div>

      {message && <div style={messageBox}>{message}</div>}

      {activeView === "LEAPS" ? (
        <LeapsSleeve data={leaps} onRefresh={async () => {
          setBusy(true);
          setMessage("");
          try {
            const r = await axios.post(`${API}/options_desk/leaps/refresh`);
            setLeaps(r.data);
            setMessage("LEAPS SLEEVE REFRESHED");
          } finally {
            setBusy(false);
          }
        }} busy={busy} />
      ) : (
      <>

      <div style={deskGrid}>
        <Card title="PM ROUTING BOARD" accentColor={accent}>
          <RouteBars summary={deskSummary} />
          <CandidateTable rows={deskRows} selected={selectedTicket} onSelect={setSelected} />
        </Card>
        <Card title="MANUAL EXECUTION TICKET" accentColor="#fbbf24">
          <ExecutionTicket ticket={selectedTicket} account={account} busy={busy} onExecute={execute} lseContext={lseContext} />
        </Card>
      </div>

      <div style={deskGrid}>
        <Card title="OPEN OPTION POSITIONS" accentColor={accent2}>
          <OpenPositionsTable
            positions={openPositions}
            selected={selectedOpenPosition}
            riskBySymbol={riskBySymbol}
            onSelect={setSelectedPosition}
          />
        </Card>
        <Card title="POSITION PROFILE" accentColor="#4ade80">
          <PositionProfileCard
            position={selectedOpenPosition}
            trade={selectedOpenPosition ? tradeBySymbol[selectedOpenPosition.symbol] : null}
            risk={selectedOpenPosition ? riskBySymbol[selectedOpenPosition.symbol] : null}
          />
        </Card>
      </div>

      <div style={deskGrid}>
        <Card title="OPTIONS ORDERS" accentColor={accent}>
          <SimpleTable
            empty="No options orders."
            head={["SYMBOL", "SIDE", "QTY", "TYPE", "STATUS"]}
            rows={(orders?.orders || []).slice(0, 12).map(o => [
              o.symbol,
              o.side,
              o.qty,
              o.type,
              o.status,
            ])}
          />
        </Card>
        <Card title="FILL SYNC" accentColor={accent2}>
          <SimpleTable
            empty="No synced option fills yet."
            head={["SYMBOL", "STATUS", "ENTRY", "CURRENT", "P/L"]}
            rows={(trades?.trades || []).slice(0, 10).map(t => [
              t.symbol,
              t.status,
              money(t.entry_premium),
              money(t.current_premium),
              t.unrealized_pct != null ? `${num(t.unrealized_pct, 2)}%` : t.realized_pct != null ? `${num(t.realized_pct, 2)}%` : "-",
            ])}
          />
        </Card>
      </div>

      <Card title="OPTIONS RISK MONITOR" accentColor="#f87171">
        <SimpleTable
          empty="No open option contracts checked yet."
          head={["SYMBOL", "ENTRY", "CURRENT", "P/L %", "THETA", "FLOOR", "STATUS"]}
          rows={(risk?.checks || []).map(c => [
            c.symbol,
            money(c.entry_premium),
            money(c.current_premium),
            `${num(c.pnl_pct, 2)}%`,
            c.theta == null ? "-" : Number(c.theta).toFixed(4),
            `${num(c.ratchet?.locked_floor_pct, 1)}%`,
            c.hard_stop_triggered ? "HARD STOP" : c.theta_status || "OK",
          ])}
        />
      </Card>
      </>
      )}
    </CrtShell>
  );
}

function LeapsSleeve({ data, onRefresh, busy }) {
  const holdings = data?.holdings || [];
  const candidates = data?.candidates || [];
  const summary = data?.summary || {};
  return (
    <div>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 18, flexWrap: "wrap" }}>
        <Stat label="OPEN LEAPS" value={summary.open_leaps || 0} sub="BOUGHT CONTRACTS" color={accent} accentBar />
        <Stat label="DIAGONALS" value={summary.diagonal_overlays || 0} sub="SHORT CALL OVERLAYS" color="#4ade80" />
        <Stat label="CANDIDATES" value={summary.candidate_count || 0} sub="PM LONG-TERM WATCH" color="#fbbf24" />
        <Stat label="MODE" value="READ ONLY" sub="NO LEAPS EXECUTION YET" color={accent2} />
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 14 }}>
        <button onClick={onRefresh} disabled={busy} style={buttonStyle(accent2)}>{busy ? "REFRESHING" : "REFRESH LEAPS"}</button>
      </div>
      <div style={deskGrid}>
        <Card title="BOUGHT LEAPS - STRATEGY STATE" accentColor={accent}>
          <SimpleTable
            empty="No bought LEAPS detected in the options account."
            head={["TICKER", "CONTRACT", "DTE", "STRATEGY", "NOW", "1Y EXPECTED"]}
            rows={holdings.map(h => [
              `$${h.ticker}`,
              h.symbol,
              h.days_to_expiration,
              h.strategy_current,
              h.unrealized_pct != null ? `${num(h.unrealized_pct, 2)}%` : "-",
              `${num(h.kronos_1y?.expected_contract_1y_pct, 1)}%`,
            ])}
          />
        </Card>
        <Card title="KRONOS 1Y CONE" accentColor="#4ade80">
          {holdings.length ? <LeapsCone item={holdings[0]} /> : <div style={{ color: muted, padding: 20 }}>Click into bought LEAPS once positions exist. The sleeve will show expected one-year contract gain and cone.</div>}
        </Card>
      </div>
      <Card title="LEAPS CANDIDATE WATCHLIST" accentColor="#fbbf24">
        <SimpleTable
          empty="No long-term PM candidates in the latest scan."
          head={["TICKER", "PM", "ACTION", "STRATEGY", "UNDERLYING 1Y", "CONTRACT 1Y", "CONE"]}
          rows={candidates.map(c => [
            `$${c.ticker}`,
            num(c.pm_score, 1),
            c.pm_action,
            c.strategy_candidate,
            `${num(c.kronos_1y?.expected_underlying_1y_pct, 1)}%`,
            `${num(c.kronos_1y?.expected_contract_1y_pct, 1)}%`,
            `${num(c.kronos_1y?.cone_low_pct, 1)}% / ${num(c.kronos_1y?.cone_high_pct, 1)}%`,
          ])}
        />
      </Card>
      <div style={dataPolicyBox}>
        LEAPS POLICY: BUY LONG-DATED CALLS ONLY AFTER PM APPROVAL AND LIQUIDITY CLEARANCE. COVERED CALL/DIAGONAL OVERLAYS ARE STRATEGY STATE, NOT AUTO-EXECUTION.
      </div>
    </div>
  );
}

function LeapsCone({ item }) {
  const k = item?.kronos_1y || {};
  const low = Number(k.cone_low_pct || 0);
  const expected = Number(k.expected_contract_1y_pct || 0);
  const high = Number(k.cone_high_pct || 0);
  const min = Math.min(low, expected, high, -100);
  const max = Math.max(low, expected, high, 100);
  const pos = value => `${((Number(value) - min) / Math.max(1, max - min)) * 100}%`;
  return (
    <div>
      <div style={{ color: accent, fontSize: 30, fontWeight: 900, marginBottom: 4 }}>${item.ticker}</div>
      <div style={{ color: muted, fontSize: 12, marginBottom: 16 }}>{item.symbol}</div>
      <PlanRow k="Current Strategy" v={item.strategy_current || "-"} color={accent2} />
      <PlanRow k="1Y Contract Expected" v={`${num(k.expected_contract_1y_pct, 1)}%`} color={expected >= 0 ? "#4ade80" : "#f87171"} />
      <PlanRow k="1Y Underlying Expected" v={`${num(k.expected_underlying_1y_pct, 1)}%`} color={k.expected_underlying_1y_pct >= 0 ? "#4ade80" : "#f87171"} />
      <PlanRow k="Cone" v={`${num(k.cone_low_pct, 1)}% to ${num(k.cone_high_pct, 1)}%`} />
      <div style={coneTrack}>
        <div style={{ ...coneBand, left: pos(low), width: `calc(${pos(high)} - ${pos(low)})` }} />
        <div style={{ ...coneMarker, left: pos(expected), background: expected >= 0 ? "#4ade80" : "#f87171" }} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", color: dim, fontSize: 10, letterSpacing: "0.12em" }}>
        <span>{num(min, 0)}%</span><span>{num(max, 0)}%</span>
      </div>
    </div>
  );
}

function RouteBars({ summary }) {
  const total = Math.max(1, summary.total || 0);
  return (
    <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 8, marginBottom: 14 }}>
      {["OPTION", "BOTH", "PASS"].map(key => {
        const v = summary[key.toLowerCase()] || 0;
        return (
          <div key={key} style={routeBox(ROUTE_COLOR[key])}>
            <span>{key}</span>
            <strong>{v}</strong>
            <div style={barTrack}><div style={{ ...barFill, background: ROUTE_COLOR[key], width: `${(v / total) * 100}%` }} /></div>
          </div>
        );
      })}
    </div>
  );
}

function CandidateTable({ rows, selected, onSelect }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No option candidates available. Run a scan first.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 920 }}>
        <thead><tr>{["TICKER", "ROUTE", "PLAYBOOK", "STRATEGY", "PM", "R/R", "DATA", "READY"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.candidate_id} onClick={() => onSelect(r)} style={{ borderTop: hairline, cursor: "pointer", background: selected?.candidate_id === r.candidate_id ? "rgba(200,168,75,0.08)" : "transparent" }}>
              <td style={{ ...td, color: accent, fontWeight: 900 }}>${r.ticker}</td>
              <td style={{ ...td, color: ROUTE_COLOR[r.route] || labelLight, fontWeight: 900 }}>{r.route}</td>
              <td style={{ ...td, color: accent2, fontWeight: 800 }}>{r.strategy_lane?.lane || "-"}</td>
              <td style={td}>{r.strategy || "-"}</td>
              <td style={td}>{num(r.pm_score, 1)}</td>
              <td style={td}>{num(r.risk_reward, 2)}</td>
              <td style={{ ...td, color: r.data_provider === "ALPACA_OPTIONS" ? accent2 : "#fbbf24", fontWeight: 800 }}>
                {(r.data_provider || r.instrument?.data_provider || "UNKNOWN").replace("_OPTIONS", "")}
                <br /><span style={{ color: muted }}>{r.data_quality || r.instrument?.data_quality || "-"}</span>
              </td>
              <td style={{ ...td, color: r.manual_fire_ready ? "#4ade80" : "#f87171" }}>{r.manual_fire_ready ? "YES" : "NO"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ExecutionTicket({ ticket, account, busy, onExecute, lseContext }) {
  if (!ticket) return <div style={{ color: muted, padding: 20 }}>Select a candidate.</div>;
  const instrument = ticket.instrument || {};
  const exit = ticket.exit_policy || {};
  const blocks = ticket.blocked_reasons || [];
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, borderBottom: hairline, paddingBottom: 12, marginBottom: 12 }}>
        <div>
          <div style={{ color: accent, fontSize: 28, fontWeight: 900 }}>${ticket.ticker}</div>
          <div style={{ color: ROUTE_COLOR[ticket.route] || labelLight, fontSize: 11, letterSpacing: "0.14em" }}>{ticket.route} / {ticket.pm_action}</div>
        </div>
        <div style={{ color: ticket.manual_fire_ready ? "#4ade80" : "#f87171", fontSize: 12, fontWeight: 900 }}>{ticket.manual_fire_ready ? "READY" : "BLOCKED"}</div>
      </div>
      <PlanRow k="Strategy" v={ticket.strategy || "-"} />
      <PlanRow k="Playbook Lane" v={ticket.strategy_lane?.lane || "-"} color={accent2} />
      <PlanRow k="Risk Posture" v={ticket.strategy_lane?.risk_posture || "-"} color="#fbbf24" />
      <PlanRow k="Preferred Structure" v={ticket.strategy_lane?.preferred_structure || "-"} />
      <PlanRow k="Kind" v={instrument.kind || "-"} />
      <PlanRow k="Expiration" v={instrument.expiration || ticket.expiration || "-"} />
      <PlanRow k="Strike" v={instrument.strike || instrument.buy_strike || "-"} />
      <PlanRow k="Premium / Debit" v={money(instrument.premium || instrument.net_debit)} />
      <PlanRow k="Max Loss" v={money(instrument.max_loss)} color="#fbbf24" />
      <PlanRow k="Risk Budget" v={money(ticket.risk_budget)} color={accent} />
      <PlanRow k="Contracts" v={ticket.contracts || 0} />
      <PlanRow k="IV" v={`${ticket.iv_rank ?? "-"} ${ticket.iv_label || ""}`} />
      <PlanRow k="Data" v={`${ticket.data_provider || instrument.data_provider || "UNKNOWN"} / ${ticket.data_quality || instrument.data_quality || "-"}`} color={ticket.data_provider === "ALPACA_OPTIONS" ? accent2 : "#fbbf24"} />
      <PlanRow k="Feed" v={ticket.data_feed || instrument.data_feed || "-"} />
      <LseOptionsIntel context={lseContext} ticket={ticket} />
      <PlanRow k="Exit" v={exit.policy ? "NO TP / RATCHET" : "-"} color="#4ade80" />
      <PlanRow k="Initial Floor" v={exit.initial_stop_pct != null ? `${exit.initial_stop_pct}%` : "-"} color="#f87171" />
      <PlanRow k="Locked Floor" v={exit.locked_floor_pct != null ? `${exit.locked_floor_pct}%` : "-"} color={accent} />
      {exit.tiers?.length > 0 && (
        <div style={ratchetGrid}>
          {exit.tiers.map(t => (
            <div key={`${t.trigger_gain_pct}-${t.locked_gain_pct}`} style={ratchetTier}>
              <span>+{t.trigger_gain_pct}%</span>
              <strong>+{t.locked_gain_pct}%</strong>
            </div>
          ))}
        </div>
      )}
      <div style={{ color: muted, fontSize: 12, lineHeight: 1.6, marginTop: 12 }}>{ticket.strategy_reason || "No thesis text."}</div>
      {(ticket.strategy_lane?.reasons || []).length > 0 && (
        <div style={{ color: labelLight, fontSize: 11, lineHeight: 1.55, marginTop: 10, borderTop: hairline, paddingTop: 10 }}>
          {ticket.strategy_lane.reasons.map((reason, i) => <div key={i}>{reason}</div>)}
        </div>
      )}
      <div style={{ marginTop: 12 }}>
        {blocks.map(b => <div key={b} style={{ color: "#f87171", fontSize: 11, padding: "5px 0", borderTop: hairline }}>{b}</div>)}
      </div>
      <button onClick={onExecute} disabled={busy || !ticket.manual_fire_ready || !account?.ok} style={{ ...buttonStyle("#4ade80"), width: "100%", marginTop: 14, opacity: (!ticket.manual_fire_ready || !account?.ok) ? 0.45 : 1 }}>
        MANUAL FIRE PAPER ORDER
      </button>
    </div>
  );
}

function LseOptionsIntel({ context, ticket }) {
  const chainRows = Array.isArray(context?.chain?.rows) ? context.chain.rows : [];
  const flowRows = Array.isArray(context?.flow?.rows) ? context.flow.rows : [];
  const sample = bestLseOptionRow(chainRows, ticket);
  if (context?.loading) {
    return <PlanRow k="LSE Options" v="SYNCING..." color={accent2} />;
  }
  if (!context || (context.chain?.error && context.flow?.error)) {
    return <PlanRow k="LSE Options" v="UNAVAILABLE" color="#fbbf24" />;
  }
  return (
    <div style={{ marginTop: 10, marginBottom: 8, paddingTop: 10, borderTop: hairline }}>
      <div style={{ color: accent2, fontSize: 10, letterSpacing: "0.14em", marginBottom: 7 }}>
        LSE OPTION BATTLE DATA
      </div>
      <PlanRow k="Chain Rows" v={chainRows.length} color={accent2} />
      <PlanRow k="Flow Prints" v={flowRows.length} color={flowRows.length ? accent : muted} />
      <PlanRow k="Nearest Contract" v={sample ? compactContract(sample) : "-"} color={sample ? labelLight : muted} />
      <PlanRow k="LSE IV / Delta" v={sample ? `${fieldValue(sample, ["iv", "implied_volatility"]) || "-"} / ${fieldValue(sample, ["delta"]) || "-"}` : "-"} />
      <PlanRow k="LSE Bid / Ask" v={sample ? `${fieldValue(sample, ["bid"]) || "-"} / ${fieldValue(sample, ["ask"]) || "-"}` : "-"} />
    </div>
  );
}

function bestLseOptionRow(rows, ticket) {
  const instrument = ticket?.instrument || {};
  const targetStrike = Number(instrument.strike || instrument.buy_strike || ticket?.strike);
  if (!rows.length) return null;
  if (!Number.isFinite(targetStrike)) return rows[0];
  return [...rows].sort((a, b) => {
    const av = Math.abs(Number(fieldValue(a, ["strike", "strike_price"])) - targetStrike);
    const bv = Math.abs(Number(fieldValue(b, ["strike", "strike_price"])) - targetStrike);
    return (Number.isFinite(av) ? av : 999999) - (Number.isFinite(bv) ? bv : 999999);
  })[0];
}

function fieldValue(row, keys) {
  const found = Object.keys(row || {}).find(k => keys.some(key => k.toLowerCase() === key || k.toLowerCase().includes(key)));
  const value = found ? row[found] : null;
  return value == null || value === "" ? null : value;
}

function compactContract(row) {
  const exp = fieldValue(row, ["expiration", "expiration_date", "expiry"]);
  const strike = fieldValue(row, ["strike", "strike_price"]);
  const type = fieldValue(row, ["type", "option_type", "right"]);
  return [exp, strike ? `$${strike}` : null, type].filter(Boolean).join(" / ") || "-";
}

function OpenPositionsTable({ positions, selected, riskBySymbol, onSelect }) {
  if (!positions.length) return <div style={{ color: muted, padding: 20 }}>No open option positions.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 620 }}>
        <thead><tr>{["SYMBOL", "QTY", "VALUE", "P/L", "RISK"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>
          {positions.map(p => {
            const risk = riskBySymbol[p.symbol] || {};
            const isSelected = selected?.symbol === p.symbol;
            return (
              <tr key={p.symbol} onClick={() => onSelect(p)} style={{ borderTop: hairline, cursor: "pointer", background: isSelected ? "rgba(74,222,128,0.08)" : "transparent" }}>
                <td style={{ ...td, color: accent, fontWeight: 900 }}>{p.symbol}</td>
                <td style={td}>{p.qty}</td>
                <td style={td}>{money(p.market_value)}</td>
                <td style={{ ...td, color: Number(p.unrealized_plpc) >= 0 ? "#4ade80" : "#f87171" }}>{num(Number(p.unrealized_plpc) * 100, 2)}%</td>
                <td style={{ ...td, color: risk.hard_stop_triggered ? "#f87171" : risk.theta_status === "WATCH" ? "#fbbf24" : "#4ade80" }}>
                  {risk.hard_stop_triggered ? "HARD STOP" : risk.theta_status || "SYNC"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PositionProfileCard({ position, trade, risk }) {
  if (!position) return <div style={{ color: muted, padding: 20 }}>Click an open option position to open its profile.</div>;
  const symbol = position.symbol || trade?.symbol || "";
  const ticker = trade?.ticker || parseOptionRoot(symbol);
  const exit = risk?.ratchet || trade?.exit_policy || {};
  const pnlPct = risk?.pnl_pct ?? (Number(position.unrealized_plpc) * 100);
  const nextTier = nextRatchetTier(exit);
  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <TradingViewMiniChart ticker={ticker} companyName={`$${symbol}`} height={260} />
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
        <MiniStat label="CONTRACT" value={symbol} color={accent} />
        <MiniStat label="STATUS" value={trade?.status || "LIVE"} color="#4ade80" />
        <MiniStat label="ENTRY" value={money(risk?.entry_premium ?? trade?.entry_premium ?? position.avg_entry_price)} />
        <MiniStat label="CURRENT" value={money(risk?.current_premium ?? trade?.current_premium ?? position.current_price)} color={Number(pnlPct) >= 0 ? "#4ade80" : "#f87171"} />
        <MiniStat label="P/L" value={`${num(pnlPct, 2)}%`} color={Number(pnlPct) >= 0 ? "#4ade80" : "#f87171"} />
        <MiniStat label="THETA" value={risk?.theta == null ? "-" : Number(risk.theta).toFixed(4)} color={risk?.theta_status === "WATCH" ? "#fbbf24" : labelLight} />
      </div>
      <PlanRow k="Qty" v={position.qty || trade?.qty || "-"} />
      <PlanRow k="Strike" v={trade?.strike || optionStrike(symbol) || "-"} />
      <PlanRow k="Expiration" v={trade?.expiration || optionExpiration(symbol) || "-"} />
      <PlanRow k="Hard Stop" v="-20%" color="#f87171" />
      <PlanRow k="Locked Floor" v={exit.locked_floor_pct != null ? `${exit.locked_floor_pct}%` : "-"} color={accent} />
      <PlanRow k="Floor Premium" v={money(exit.floor_premium)} color={accent} />
      <PlanRow k="Peak Premium" v={money(exit.peak_premium)} color="#4ade80" />
      <PlanRow k="Next Tier" v={nextTier ? `+${nextTier.trigger_gain_pct}% -> +${nextTier.locked_gain_pct}%` : "MAX LOCK"} color="#fbbf24" />
      {exit.tiers?.length > 0 && (
        <div style={ratchetGrid}>
          {exit.tiers.map(t => (
            <div key={`${symbol}-${t.trigger_gain_pct}`} style={{ ...ratchetTier, background: Number(exit.peak_gain_pct || 0) >= Number(t.trigger_gain_pct) ? "rgba(74,222,128,0.13)" : "rgba(255,255,255,0.025)" }}>
              <span>+{t.trigger_gain_pct}%</span>
              <strong>+{t.locked_gain_pct}%</strong>
            </div>
          ))}
        </div>
      )}
      <div style={{ color: muted, fontSize: 12, lineHeight: 1.6, marginTop: 12 }}>
        {risk?.hard_stop_triggered ? "Hard stop is triggered. Monitor will submit a close while enforcement is active." : "Profile tracks fill price, live premium, theta decay, and the active no-TP ratchet floor."}
      </div>
    </div>
  );
}

function MiniStat({ label, value, color = labelLight }) {
  return (
    <div style={{ border: hairline, background: "rgba(255,255,255,0.025)", padding: 10, minWidth: 0 }}>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.14em", marginBottom: 6 }}>{label}</div>
      <div style={{ color, fontSize: 14, fontWeight: 900, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{value}</div>
    </div>
  );
}

function SimpleTable({ head, rows, empty }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>{empty}</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 520 }}>
        <thead><tr>{head.map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i} style={{ borderTop: hairline }}>{r.map((v, j) => <td key={j} style={td}>{v}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function parseOptionRoot(symbol) {
  return String(symbol || "").match(/^([A-Z]{1,6})\d{6}[CP]\d{8}$/)?.[1] || "";
}

function optionExpiration(symbol) {
  const raw = String(symbol || "").match(/^[A-Z]{1,6}(\d{6})[CP]\d{8}$/)?.[1];
  if (!raw) return "";
  return `20${raw.slice(0, 2)}-${raw.slice(2, 4)}-${raw.slice(4, 6)}`;
}

function optionStrike(symbol) {
  const raw = String(symbol || "").match(/^[A-Z]{1,6}\d{6}[CP](\d{8})$/)?.[1];
  return raw ? (Number(raw) / 1000).toFixed(2) : "";
}

function nextRatchetTier(exit) {
  const peak = Number(exit?.peak_gain_pct || 0);
  return (exit?.tiers || []).find(t => Number(t.trigger_gain_pct) > peak);
}

function PlanRow({ k, v, color = labelLight }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, borderBottom: hairline, padding: "7px 0", fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em" }}>{k}</span>
      <span style={{ color, textAlign: "right" }}>{v}</span>
    </div>
  );
}

function num(v, d = 1) {
  const n = Number(v);
  return Number.isFinite(n) ? n.toFixed(d) : "-";
}

function money(v) {
  const n = Number(v);
  return Number.isFinite(n) ? `$${n.toFixed(2)}` : "-";
}

function buttonStyle(color) {
  return { background: "transparent", border: `0.5px solid ${color}`, color, fontSize: 11, padding: "8px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700 };
}

function routeBox(color) {
  return { border: `0.5px solid ${color}55`, background: `${color}0d`, padding: 10, color, display: "grid", gap: 6, fontSize: 10, letterSpacing: "0.12em" };
}

const notice = { padding: "14px 18px", border: `0.5px solid ${accent2}`, background: `${accent2}10`, color: accent2, fontSize: 11, letterSpacing: "0.1em", marginBottom: 16 };
const dataPolicyBox = { padding: "10px 14px", border: hairline, background: "rgba(255,255,255,0.025)", color: muted, fontSize: 10, letterSpacing: "0.12em", margin: "-10px 0 16px" };
const messageBox = { padding: "10px 14px", border: `0.5px solid ${accent}`, background: `${accent}12`, color: accent, fontSize: 11, letterSpacing: "0.1em", marginBottom: 16 };
const tabStrip = { display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" };
const tabButton = active => ({ background: active ? "rgba(200,168,75,0.18)" : "transparent", border: `0.5px solid ${active ? accent : "rgba(255,255,255,0.14)"}`, color: active ? accent : muted, fontSize: 11, padding: "9px 16px", cursor: "pointer", letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 900 });
const deskGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.25fr) minmax(340px, 0.75fr)", gap: 18, marginBottom: 18 };
const barTrack = { height: 4, background: "rgba(255,255,255,0.06)", marginTop: 4, overflow: "hidden" };
const barFill = { height: "100%" };
const ratchetGrid = { display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 6, marginTop: 10 };
const ratchetTier = { border: hairline, background: "rgba(74,222,128,0.06)", padding: "7px 6px", color: muted, fontSize: 10, letterSpacing: "0.08em", display: "flex", justifyContent: "space-between", gap: 6 };
const coneTrack = { position: "relative", height: 34, border: hairline, background: "rgba(255,255,255,0.04)", margin: "18px 0 8px", overflow: "hidden" };
const coneBand = { position: "absolute", top: 10, bottom: 10, background: "rgba(94,234,212,0.20)", borderLeft: `1px solid ${accent2}`, borderRight: `1px solid ${accent2}` };
const coneMarker = { position: "absolute", top: 4, bottom: 4, width: 3, boxShadow: "0 0 12px currentColor" };
