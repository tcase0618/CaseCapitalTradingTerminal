import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

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

  const reload = () => {
    axios.get(`${API}/trade_floor/account`).then(r => setAccount(r.data));
    axios.get(`${API}/trade_floor/positions`).then(r => {
      setPositions(r.data.db_positions || []);
      setLiveAlpaca(r.data.live_alpaca || []);
      setScanLog(r.data.last_scan_log);
    });
    axios.get(`${API}/trade_floor/regime`).then(r => setRegime(r.data));
    axios.get(`${API}/trade_floor/history`).then(r => setHistory(r.data.trades || []));
  };
  useEffect(reload, []);

  const acct = account?.account || {};
  const acctReady = account?.alpaca_configured;
  const deployed = liveAlpaca.reduce((a, p) => a + Number(p.market_value || 0), 0);
  const unrealized = liveAlpaca.reduce((a, p) => a + Number(p.unrealized_pl || 0), 0);

  return (
    <CrtShell title="TRADE FLOOR · ALPACA PAPER">
      {!acctReady && (
        <div style={{ padding: "14px 18px", border: `0.5px solid #f87171`,
                       background: "#f8717115", color: "#f87171", fontSize: 11,
                       letterSpacing: "0.1em", marginBottom: 16 }}>
          ⚠ ALPACA NOT CONFIGURED — set APCA_API_KEY_ID + APCA_API_SECRET_KEY in backend .env
        </div>
      )}

      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="OPEN POS" value={positions.length} sub={`MAX 10`} color={accent} accentBar />
        <Stat label="DEPLOYED" value={`$${Math.round(deployed)}`} sub="MARKET VALUE" color={accent2} />
        <Stat label="UNREALIZED" value={`${unrealized >= 0 ? "+" : ""}$${Math.round(unrealized)}`}
              sub="P&L" color={unrealized >= 0 ? "#4ade80" : "#f87171"} />
        <Stat label="CASH" value={`$${Math.round(Number(acct.cash || 0))}`} sub="AVAILABLE" color={labelLight} />
        <Stat label="REGIME" value={(regime?.status || "—").toUpperCase()}
              sub={regime?.vix != null ? `VIX ${regime.vix}` : "—"}
              color={regime?.status === "green" ? "#4ade80" : regime?.status === "yellow" ? "#fbbf24" : "#f87171"} />
      </div>

      <div style={{ display: "flex", borderBottom: hairline, marginBottom: 16 }}>
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

      {tab === "positions" && <LivePositions positions={liveAlpaca} db={positions} reload={reload} scanLog={scanLog} />}
      {tab === "filter" && <SignalFilter scanLog={scanLog} />}
      {tab === "account" && <AccountPerf acct={acct} history={history} />}
      {tab === "risk" && <RiskDash regime={regime} acct={acct} positions={liveAlpaca} />}
      {tab === "journal" && <JournalTab history={history} />}
    </CrtShell>
  );
}

function LivePositions({ positions, db, reload, scanLog }) {
  const close = async (t) => {
    if (!confirm(`Close ${t}?`)) return;
    await axios.post(`${API}/trade_floor/close?ticker=${t}`).catch(() => {});
    toast(`Close request sent for ${t}`);
    setTimeout(reload, 1500);
  };
  return (
    <>
      <Card title={`OPEN POSITIONS · ${positions.length}`}>
        {!positions.length ? <div style={{ color: muted, padding: 20 }}>No open positions.</div> : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr>
              <th style={th}>TICKER</th><th style={th}>QTY</th>
              <th style={th}>ENTRY</th><th style={th}>MARK</th>
              <th style={th}>UNREALIZED</th><th style={th}>%</th>
              <th style={th}>STATUS</th><th style={th}></th>
            </tr></thead>
            <tbody>
              {positions.map((p, i) => {
                const pct = Number(p.unrealized_plpc || 0) * 100;
                const status = pct < -10 ? "CRITICAL" : pct < -5 ? "AT RISK" : "RUNNING";
                const sc = status === "CRITICAL" ? "#f87171" : status === "AT RISK" ? "#fbbf24" : "#4ade80";
                return (
                  <tr key={i} style={{ borderTop: hairline }}>
                    <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.symbol}</td>
                    <td style={td}>{Number(p.qty).toFixed(4)}</td>
                    <td style={td}>${Number(p.avg_entry_price).toFixed(2)}</td>
                    <td style={td}>${Number(p.current_price).toFixed(2)}</td>
                    <td style={{ ...td, color: pct >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                      ${Number(p.unrealized_pl).toFixed(2)}
                    </td>
                    <td style={{ ...td, color: pct >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                      {pct >= 0 ? "+" : ""}{pct.toFixed(2)}%
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
          </table>
        )}
      </Card>
      <Card title={`LAST SCAN COMPRESSION · ${scanLog?.scanned || 0} → ${scanLog?.executed || 0}`}>
        <Row k="SCANNED" v={scanLog?.scanned || 0} />
        <Row k="EXECUTED" v={scanLog?.executed || 0} />
        <Row k="REJECTED" v={scanLog?.rejected || 0} />
        <Row k="COMPRESSION" v={scanLog?.compression_ratio != null ? `${(scanLog.compression_ratio * 100).toFixed(0)}%` : "—"} />
        <div style={{ marginTop: 14, color: dim, fontSize: 10, letterSpacing: "0.14em" }}>// REJECTION DETAIL</div>
        {(scanLog?.rejection_details || []).slice(0, 8).map((r, i) => (
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
  const exec = scanLog?.execution_details || [];
  const rej = scanLog?.rejection_details || [];
  return (
    <Card title={`COMPRESSION ${scanLog?.compression_ratio != null ? (scanLog.compression_ratio * 100).toFixed(0) + "%" : "—"}`}>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 24 }}>
        <div>
          <div style={{ color: "#4ade80", fontSize: 11, letterSpacing: "0.14em", marginBottom: 8, fontWeight: 700 }}>
            // EXECUTED · {exec.length}
          </div>
          {exec.map((e, i) => (
            <div key={i} style={{ padding: "6px 0", borderBottom: hairline, fontSize: 11 }}>
              <span style={{ color: accent, fontWeight: 700 }}>${e.ticker}</span>
              <span style={{ color: muted, marginLeft: 10 }}>${e.notional} · score {e.score?.toFixed(1)}</span>
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

function AccountPerf({ acct, history }) {
  const startBal = 1000;
  const cur = Number(acct.equity || startBal);
  const totalRet = ((cur - startBal) / startBal) * 100;
  const wins = history.filter(h => (h.realized_pct || 0) > 0).length;
  const winRate = history.length ? wins / history.length : 0;
  return (
    <>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="START" value={`$${startBal}`} sub="BASELINE" color={dim} />
        <Stat label="CURRENT" value={`$${Math.round(cur)}`} sub="EQUITY" color={accent} accentBar />
        <Stat label="RETURN" value={`${totalRet >= 0 ? "+" : ""}${totalRet.toFixed(2)}%`} sub="ALL-TIME"
              color={totalRet >= 0 ? "#4ade80" : "#f87171"} />
        <Stat label="WIN RATE" value={`${(winRate * 100).toFixed(0)}%`} sub={`${wins}/${history.length}`} color="#4ade80" />
        <Stat label="TRADES" value={history.length} sub="CLOSED" color={labelLight} />
      </div>
      <Card title="CLOSED TRADES · BY COMBO">
        {!history.length ? <div style={{ color: muted, padding: 20 }}>No closed trades yet.</div> : (
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead><tr><th style={th}>TICKER</th><th style={th}>COMBO</th>
                <th style={th}>ENTRY</th><th style={th}>EXIT</th><th style={th}>P&L</th></tr></thead>
            <tbody>{history.map((h, i) => (
              <tr key={i} style={{ borderTop: hairline }}>
                <td style={{ ...td, color: accent, fontWeight: 700 }}>${h.ticker}</td>
                <td style={td}>{(h.signal_combo || []).join(" · ")}</td>
                <td style={td}>${h.entry_price_ref?.toFixed(2)}</td>
                <td style={td}>${h.exit_price?.toFixed(2)}</td>
                <td style={{ ...td, color: (h.realized_pct || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                  {h.realized_pct >= 0 ? "+" : ""}{h.realized_pct?.toFixed(2)}%
                </td>
              </tr>))}</tbody>
          </table>
        )}
      </Card>
    </>
  );
}

function RiskDash({ regime, acct, positions }) {
  const status = regime?.status || "unknown";
  const sColor = status === "green" ? "#4ade80" : status === "yellow" ? "#fbbf24" : "#f87171";
  const txt = status === "green" ? "RISK ON" : status === "yellow" ? "RISK NEUTRAL" : "RISK OFF";
  return (
    <Card title="MARKET REGIME">
      <div style={{ textAlign: "center", padding: 30 }}>
        <div style={{ color: sColor, fontSize: 40, fontWeight: 700, letterSpacing: "0.18em" }}>{txt}</div>
        <div style={{ display: "flex", justifyContent: "center", gap: 40, marginTop: 24 }}>
          <Stat label="VIX" value={regime?.vix || "—"} sub={regime?.vix > 25 ? "ABOVE HALT" : "BELOW HALT"} color={sColor} />
          <Stat label="SPY" value={regime?.spy_last || "—"} sub={`EMA200 ${regime?.spy_ema200 || "—"}`} color={sColor} />
          <Stat label="POSITIONS" value={`${positions.length}/10`} sub="MAX LIMIT" color={positions.length >= 8 ? "#fbbf24" : "#4ade80"} />
        </div>
      </div>
    </Card>
  );
}

function JournalTab({ history }) {
  return (
    <Card title="DAILY JOURNAL">
      {!history.length ? <div style={{ color: muted, padding: 20 }}>Journal entries populate as trades close.</div> : (
        history.map((h, i) => (
          <div key={i} style={{ padding: "14px 0", borderBottom: hairline }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <span style={{ color: accent, fontWeight: 700, fontSize: 13 }}>${h.ticker}</span>
              <span style={{ color: muted, fontSize: 11 }}>{h.closed_at?.slice(0, 10)}</span>
            </div>
            <div style={{ color: labelLight, fontSize: 11, marginTop: 4 }}>
              {(h.signal_combo || []).join(" · ")} · entry ${h.entry_price_ref?.toFixed(2)} → exit ${h.exit_price?.toFixed(2)} ·{" "}
              <span style={{ color: (h.realized_pct || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                {h.realized_pct >= 0 ? "+" : ""}{h.realized_pct?.toFixed(2)}%
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
