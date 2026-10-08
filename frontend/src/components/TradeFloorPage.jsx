import { useCallback, useEffect, useRef, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;
const record = value => value && typeof value === "object" && !Array.isArray(value);
const rows = value => Array.isArray(value) ? value.filter(record) : [];
const texts = value => Array.isArray(value) ? value.filter(x => typeof x === "string") : [];
const num = value => (typeof value === "number" || (typeof value === "string" && value.trim())) && Number.isFinite(Number(value)) ? Number(value) : null;
const fixed = (value, digits = 2) => num(value) == null ? "-" : num(value).toFixed(digits);
const money = value => num(value) == null ? "-" : `$${fixed(value)}`;
const pctText = value => num(value) == null ? "-" : `${num(value) >= 0 ? "+" : ""}${fixed(value)}%`;
const pnlColor = value => num(value) == null ? muted : num(value) >= 0 ? "#4ade80" : "#f87171";
const sum = (items, key, available) => available && items.every(p => num(p[key]) != null) ? items.reduce((total, p) => total + num(p[key]), 0) : null;

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12 };

const TABS = [
  ["positions", "LIVE POSITIONS"],
  ["filter",    "SIGNAL FILTER"],
  ["account",   "ACCOUNT PERFORMANCE"],
  ["risk",      "RISK DASHBOARD"],
  ["journal",   "JOURNAL"],
];

export default function TradeFloorPage() {
  const [tab, setTab] = useState("positions");
  const [account, setAccount] = useState(null);
  const [positions, setPositions] = useState([]);
  const [liveAlpaca, setLiveAlpaca] = useState([]);
  const [scanLog, setScanLog] = useState(null);
  const [regime, setRegime] = useState(null);
  const [history, setHistory] = useState([]);
  const [loadError, setLoadError] = useState(false);
  const requestRef = useRef(null);
  const mountedRef = useRef(false);
  const reloadTimer = useRef(null);
  const [positionsReady, setPositionsReady] = useState(false);
  const [historyReady, setHistoryReady] = useState(false);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!mountedRef.current) return;
    requestRef.current?.abort();
    const controller = new AbortController(); requestRef.current = controller;
    setLoading(true);
    const request = path => axios.get(`${API}${path}`, { signal: controller.signal, timeout: 15000 });
    const results = await Promise.allSettled([request("/trade_floor/account"), request("/trade_floor/positions"), request("/trade_floor/regime"), request("/trade_floor/history")]);
    if (controller.signal.aborted) return;
    const malformed = results.some((result, i) => result.status === "fulfilled" && (!record(result.value.data) || (i === 1 && [result.value.data.db_positions, result.value.data.live_alpaca].some(value => !Array.isArray(value) || rows(value).length !== value.length)) || (i === 3 && (!Array.isArray(result.value.data.trades) || rows(result.value.data.trades).length !== result.value.data.trades.length))));
    setLoadError(malformed || results.some(result => result.status === "rejected"));
    setLoading(false);
    if (results[0].status === "fulfilled") setAccount(results[0].value.data);
    if (results[1].status === "fulfilled") {
      const payload = results[1].value.data || {};
      setPositions(rows(payload.db_positions));
      setLiveAlpaca(rows(payload.live_alpaca));
      setPositionsReady(Array.isArray(payload.db_positions) && Array.isArray(payload.live_alpaca));
      setScanLog(payload.last_scan_log);
    }
    if (results[2].status === "fulfilled") setRegime(results[2].value.data);
    if (results[3].status === "fulfilled") { setHistory(rows(results[3].value.data?.trades)); setHistoryReady(Array.isArray(results[3].value.data?.trades)); }
  }, []);
  useEffect(() => { mountedRef.current = true; reload(); return () => { mountedRef.current = false; requestRef.current?.abort(); clearTimeout(reloadTimer.current); }; }, [reload]);
  const scheduleReload = () => {
    if (!mountedRef.current) return;
    clearTimeout(reloadTimer.current);
    reloadTimer.current = setTimeout(reload, 1500);
  };

  const acct = account?.account || {};
  const acctReady = account?.alpaca_configured;
  const deployed = sum(liveAlpaca, "market_value", positionsReady);
  const unrealized = sum(liveAlpaca, "unrealized_pl", positionsReady);
  const pendingOrders = positions.filter(p => p.fill_status === "PENDING");

  return (
    <CrtShell title="TRADE FLOOR · LEGACY ALPACA EQUITY">
      <div role="status" style={{ color: muted, marginBottom: 12 }}>Legacy equity history. Alpaca equity execution is retired; current equity execution is managed through Public.</div>
      {loading && <div role="status" style={{ color: muted }}>Loading legacy Trade Floor data...</div>}
      {loadError && <div role="status" style={{ color: "#fbbf24", marginBottom: 12 }}>Trade Floor refresh incomplete. Prior available data is retained.</div>}
      {acctReady === false && (
        <div style={{ padding: "14px 18px", border: `0.5px solid #f87171`,
                       background: "#f8717115", color: "#f87171", fontSize: 11,
                       letterSpacing: "0.1em", marginBottom: 16 }}>
          ⚠ ALPACA NOT CONFIGURED — set APCA_API_KEY_ID + APCA_API_SECRET_KEY in backend .env
        </div>
      )}

      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="LIVE POS" value={positionsReady ? liveAlpaca.length : "-"} sub={`MAX 10`} color={accent} accentBar />
        <Stat label="PENDING" value={positionsReady ? pendingOrders.length : "-"} sub="UNFILLED ORDERS" color={pendingOrders.length ? "#fbbf24" : muted} />
        <Stat label="DEPLOYED" value={money(deployed)} sub="MARKET VALUE" color={accent2} />
        <Stat label="UNREALIZED" value={money(unrealized)}
              sub="P&L" color={pnlColor(unrealized)} />
        <Stat label="CASH" value={money(acct.cash)} sub="AVAILABLE" color={labelLight} />
        <Stat label="REGIME" value={typeof regime?.status === "string" ? regime.status.toUpperCase() : "UNKNOWN"}
              sub={regime?.vix != null ? `VIX ${regime.vix}` : "—"}
              color={regime?.status === "green" ? "#4ade80" : regime?.status === "yellow" ? "#fbbf24" : "#f87171"} />
      </div>

      <div style={{ display: "flex", flexWrap: "wrap", borderBottom: hairline, marginBottom: 16 }}>
        {TABS.map(([k, l]) => (
          <button key={k} data-testid={`tf-tab-${k}`} onClick={() => setTab(k)}
            style={{
              background: "transparent", border: "none", padding: "10px 22px",
              color: tab === k ? accent : muted, cursor: "pointer",
              borderBottom: tab === k ? `2px solid ${accent}` : "2px solid transparent",
              fontSize: 11, letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>{l}</button>
        ))}
      </div>

      {tab === "positions" && <LivePositions positions={liveAlpaca} db={positions} reload={scheduleReload} scanLog={scanLog} available={positionsReady} />}
      {tab === "filter" && <SignalFilter scanLog={scanLog} />}
      {tab === "account" && <AccountPerf acct={acct} history={history} available={historyReady} />}
      {tab === "risk" && <RiskDash regime={regime} acct={acct} positions={liveAlpaca} available={positionsReady} />}
      {tab === "journal" && <JournalTab history={history} available={historyReady} />}
    </CrtShell>
  );
}

function LivePositions({ positions, db, reload, scanLog, available }) {
  const pending = db.filter(p => p.fill_status === "PENDING");
  const close = async (t) => {
    if (!confirm(`Close ${t}?`)) return;
    try {
      await axios.post(`${API}/trade_floor/close?ticker=${t}`);
      toast(`Close request sent for ${t}`);
      reload();
    } catch (error) { toast.error(error?.response?.data?.detail || `Close request failed for ${t}`); }
  };
  return (
    <>
      <Card title={`OPEN POSITIONS · ${positions.length}`}>
        {!positions.length ? <div style={{ color: muted, padding: 20 }}>{available ? "No positions returned by the legacy API." : "Legacy positions unavailable."}</div> : (
          <div style={{ overflowX: "auto" }}><table style={{ width: "100%", minWidth: 700, borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>TICKER</th><th style={th}>QTY</th>
              <th style={th}>ENTRY</th><th style={th}>MARK</th>
              <th style={th}>UNREALIZED</th><th style={th}>%</th>
              <th style={th}>STATUS</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {positions.map((p, i) => {
                const pct = num(p.unrealized_plpc) == null ? null : num(p.unrealized_plpc) * 100;
                const status = pct == null ? "UNKNOWN" : pct < -10 ? "CRITICAL" : pct < -5 ? "AT RISK" : "RUNNING";
                const sc = status === "UNKNOWN" ? muted : status === "CRITICAL" ? "#f87171" : status === "AT RISK" ? "#fbbf24" : "#4ade80";
                return (
                  <tr key={i} style={{ borderTop: hairline }}>
                    <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.symbol}</td>
                    <td style={td}>{fixed(p.qty, 4)}</td>
                    <td style={td}>{money(p.avg_entry_price)}</td>
                    <td style={td}>{money(p.current_price)}</td>
                    <td style={{ ...td, color: pnlColor(p.unrealized_pl), fontWeight: 700 }}>
                      {money(p.unrealized_pl)}
                    </td>
                    <td style={{ ...td, color: pct >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                      {pctText(pct)}
                    </td>
                    <td style={{ ...td, color: sc, fontWeight: 700 }}>{status}</td>
                    <td style={td}>
                      <button onClick={() => close(p.symbol)} data-testid={`tf-close-${p.symbol}`}
                        style={{ background: "transparent", border: `0.5px solid #f87171`, color: "#f87171",
                                  fontSize: 9, padding: "4px 10px", cursor: "pointer", fontWeight: 700 }}>
                        CLOSE
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table></div>
        )}
      </Card>
      <Card title={`PENDING ORDERS · ${pending.length}`}>
        {!pending.length ? <div style={{ color: muted, padding: 20 }}>{available ? "No pending orders returned by the legacy API." : "Pending orders unavailable."}</div> : (
          <div style={{ overflowX: "auto" }}><table style={{ width: "100%", minWidth: 650, borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>TICKER</th><th style={th}>NOTIONAL</th>
              <th style={th}>LIMIT</th><th style={th}>RAW ASK</th>
              <th style={th}>GUARD</th><th style={th}>STOP AFTER FILL</th>
              <th style={th}>SUBMITTED</th>
            </tr></thead>
            <tbody>
              {pending.map((p, i) => (
                <tr key={`${p.client_order_id || p.order_id || p.ticker}-${i}`} style={{ borderTop: hairline }}>
                  <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                  <td style={td}>{money(p.notional)}</td>
                  <td style={{ ...td, color: "#fbbf24", fontWeight: 700 }}>{money(p.limit_price)}</td>
                  <td style={td}>{money(p.raw_alpaca_ask)}</td>
                  <td style={{ ...td, color: p.limit_price_guard?.capped ? "#4ade80" : muted, fontWeight: 700 }}>
                    {p.limit_price_guard?.capped === true ? "CAPPED" : p.limit_price_guard?.capped === false ? "PASS" : "UNKNOWN"}
                  </td>
                  <td style={{ ...td, color: "#f87171", fontWeight: 700 }}>{money(p.current_stop ?? p.stop_price)}</td>
                  <td style={{ ...td, color: muted }}>{typeof p.submitted_at === "string" ? p.submitted_at.slice(0, 19) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
      </Card>
      <Card title={`LAST SCAN COMPRESSION · ${fixed(scanLog?.scanned, 0)} → ${fixed(scanLog?.executed, 0)}`}>
        <Row k="SCANNED" v={fixed(scanLog?.scanned, 0)} />
        <Row k="EXECUTED" v={fixed(scanLog?.executed, 0)} />
        <Row k="REJECTED" v={fixed(scanLog?.rejected, 0)} />
        <Row k="COMPRESSION" v={num(scanLog?.compression_ratio) == null ? "-" : pctText(num(scanLog.compression_ratio) * 100)} />
        <div style={{ marginTop: 14, color: dim, fontSize: 10, letterSpacing: "0.14em" }}>// REJECTION DETAIL</div>
        {rows(scanLog?.rejection_details).slice(0, 8).map((r, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "80px 1fr", padding: "4px 0",
                                  fontSize: 11, borderBottom: hairline }}>
            <span style={{ color: accent, fontWeight: 700 }}>${r.ticker}</span>
            <span style={{ color: muted }}>{r.reason}</span>
          </div>
        ))}
      </Card>
    </>
  );
}

function SignalFilter({ scanLog }) {
  const exec = rows(scanLog?.execution_details);
  const rej = rows(scanLog?.rejection_details);
  return (
    <Card title={`COMPRESSION ${num(scanLog?.compression_ratio) == null ? "-" : pctText(num(scanLog.compression_ratio) * 100)}`}>
      {(!Array.isArray(scanLog?.execution_details) || !Array.isArray(scanLog?.rejection_details)) && <div role="status" style={{ color: muted }}>Signal detail unavailable or incomplete.</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 240px), 1fr))", gap: 24 }}>
        <div>
          <div style={{ color: "#4ade80", fontSize: 11, letterSpacing: "0.14em", marginBottom: 8, fontWeight: 700 }}>
            // EXECUTED · {exec.length}
          </div>
          {exec.map((e, i) => (
            <div key={i} style={{ padding: "6px 0", borderBottom: hairline, fontSize: 11 }}>
              <span style={{ color: accent, fontWeight: 700 }}>${e.ticker}</span>
              <span style={{ color: muted, marginLeft: 10 }}>{money(e.notional)} · score {fixed(e.score, 1)}</span>
            </div>
          ))}
        </div>
        <div>
          <div style={{ color: "#f87171", fontSize: 11, letterSpacing: "0.14em", marginBottom: 8, fontWeight: 700 }}>
            // REJECTED · {rej.length}
          </div>
          {rej.map((r, i) => (
            <div key={i} style={{ padding: "6px 0", borderBottom: hairline, fontSize: 11 }}>
              <span style={{ color: accent, fontWeight: 700 }}>${r.ticker}</span>
              <span style={{ color: "#f87171", marginLeft: 10 }}>{r.reason}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function AccountPerf({ acct, history, available }) {
  const startBal = 1000;
  const cur = num(acct.equity);
  const totalRet = cur == null ? null : ((cur - startBal) / startBal) * 100;
  const measured = history.filter(h => num(h.realized_pct) != null);
  const wins = measured.filter(h => num(h.realized_pct) > 0).length;
  const winRate = measured.length ? wins / measured.length : null;
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="START" value={`$${startBal}`} sub="DISPLAY BASELINE" color={dim} />
        <Stat label="CURRENT" value={money(cur)} sub="EQUITY" color={accent} accentBar />
        <Stat label="RETURN" value={pctText(totalRet)} sub="VS $1000 BASELINE" color={pnlColor(totalRet)} />
        <Stat label="WIN RATE" value={winRate == null ? "-" : `${fixed(winRate * 100, 0)}%`} sub={`${wins}/${measured.length} MEASURED`} color={winRate == null ? muted : "#4ade80"} />
        <Stat label="TRADES" value={available ? history.length : "-"} sub="CLOSED" color={labelLight} />
      </div>
      <Card title="CLOSED TRADES · BY COMBO">
        {!history.length ? <div style={{ color: muted, padding: 20 }}>{available ? "No closed trades returned." : "Closed trade history unavailable."}</div> : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th style={th}>TICKER</th><th style={th}>COMBO</th>
                <th style={th}>ENTRY</th><th style={th}>EXIT</th><th style={th}>P&L</th></tr></thead>
            <tbody>{history.map((h, i) => (
              <tr key={i} style={{ borderTop: hairline }}>
                <td style={{ ...td, color: accent, fontWeight: 700 }}>${h.ticker}</td>
                <td style={td}>{texts(h.signal_combo).join(" · ") || "-"}</td>
                <td style={td}>{money(h.entry_price_ref)}</td>
                <td style={td}>{money(h.exit_price)}</td>
                <td style={{ ...td, color: pnlColor(h.realized_pct), fontWeight: 700 }}>
                  {pctText(h.realized_pct)}
                </td>
              </tr>))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function RiskDash({ regime, acct, positions, available }) {
  const status = regime?.status || "unknown";
  const sColor = status === "green" ? "#4ade80" : status === "yellow" ? "#fbbf24" : status === "red" ? "#f87171" : muted;
  const txt = status === "green" ? "RISK ON" : status === "yellow" ? "RISK NEUTRAL" : status === "red" ? "RISK OFF" : "REGIME UNKNOWN";
  return (
    <Card title="MARKET REGIME">
      <div style={{ textAlign: "center", padding: 30 }}>
        <div style={{ color: sColor, fontSize: 40, fontWeight: 700, letterSpacing: "0.18em" }}>{txt}</div>
        <div style={{ display: "flex", flexWrap: "wrap", justifyContent: "center", gap: 20, marginTop: 24 }}>
          <Stat label="VIX" value={fixed(regime?.vix)} sub={num(regime?.vix) == null ? "UNKNOWN" : regime.vix > 25 ? "ABOVE HALT" : "BELOW HALT"} color={sColor} />
          <Stat label="SPY" value={fixed(regime?.spy_last)} sub={`EMA200 ${fixed(regime?.spy_ema200)}`} color={sColor} />
          <Stat label="POSITIONS" value={available ? `${positions.length}/10` : "-"} sub="MAX LIMIT" color={positions.length >= 8 ? "#fbbf24" : muted} />
        </div>
      </div>
    </Card>
  );
}

function JournalTab({ history, available }) {
  return (
    <Card title="DAILY JOURNAL">
      {!history.length ? <div style={{ color: muted, padding: 20 }}>{available ? "No journal entries returned." : "Journal history unavailable."}</div> : (
        history.map((h, i) => (
          <div key={i} style={{ padding: "14px 0", borderBottom: hairline }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: accent, fontWeight: 700, fontSize: 13 }}>${h.ticker}</span>
              <span style={{ color: muted, fontSize: 11 }}>{typeof h.closed_at === "string" ? h.closed_at.slice(0, 10) : "-"}</span>
            </div>
            <div style={{ color: labelLight, fontSize: 11, marginTop: 4 }}>
              {texts(h.signal_combo).join(" · ") || "-"} · entry {money(h.entry_price_ref)} → exit {money(h.exit_price)} ·{" "}
              <span style={{ color: pnlColor(h.realized_pct), fontWeight: 700 }}>
                {pctText(h.realized_pct)}
              </span>
            </div>
          </div>
        ))
      )}
    </Card>
  );
}

function Row({ k, v }) {
  return (
    <div style={{ display: "flex", padding: "5px 0", borderBottom: hairline, fontSize: 11 }}>
      <span style={{ color: dim, letterSpacing: "0.14em", flex: "0 0 160px" }}>{k}</span>
      <span style={{ color: labelLight, flex: 1 }}>{v}</span>
    </div>
  );
}
