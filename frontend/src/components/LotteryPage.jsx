import { useEffect, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, accent2, dim, muted, labelLight, hairline } = tokens;

const TIER_COLOR = {
  JACKPOT: "#c8a84b",
  HOT:     "#fb923c",
  WARM:    "#5eead4",
  COLD:    "#6b7280",
};

const TIER_GLOW = {
  JACKPOT: "0 0 12px rgba(200,168,75,0.5)",
  HOT:     "0 0 8px rgba(251,146,60,0.4)",
  WARM:    "0 0 6px rgba(94,234,212,0.3)",
  COLD:    "none",
};

export default function LotteryPage() {
  const [current, setCurrent] = useState(null);
  const [history, setHistory] = useState(null);
  const [tab, setTab] = useState("current");
  const [screener, setScreener] = useState([]);
  const [manualPlays, setManualPlays] = useState([]);
  const [manualTR, setManualTR] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [mForm, setMForm] = useState({ ticker: "", entry_price: "", lottery_score: "", risk_amount: "50" });
  const [mAdding, setMAdding] = useState(false);
  const [settleRow, setSettleRow] = useState(null);  // {ticker, date, exit_price}

  const reloadAll = () => {
    axios.get(`${API}/v32/lottery/current`).then(r => setCurrent(r.data)).catch(() => {});
    axios.get(`${API}/v32/lottery`).then(r => setHistory(r.data)).catch(() => {});
    axios.get(`${API}/lottery/screener`).then(r => setScreener(r.data.candidates || []));
    axios.get(`${API}/lottery/manual_plays`).then(r => setManualPlays(r.data.plays || []));
    axios.get(`${API}/lottery/manual_track_record`).then(r => setManualTR(r.data));
  };
  useEffect(() => { reloadAll(); }, []);

  const runLotteryScan = async () => {
    setScanning(true);
    try {
      const r = await axios.post(`${API}/lottery/scan`);
      alert(`Lottery scan complete: ${r.data.count} candidates`);
      reloadAll();
    } finally { setScanning(false); }
  };

  const addManual = async () => {
    if (!mForm.ticker || !mForm.entry_price) { alert("Ticker + Entry Price required"); return; }
    setMAdding(true);
    try {
      await axios.post(`${API}/lottery/manual`, {
        ticker: mForm.ticker.toUpperCase().trim(),
        entry_price: Number(mForm.entry_price),
        lottery_score: mForm.lottery_score ? Number(mForm.lottery_score) : null,
        risk_amount: mForm.risk_amount ? Number(mForm.risk_amount) : 50,
      });
      setMForm({ ticker: "", entry_price: "", lottery_score: "", risk_amount: "50" });
      reloadAll();
    } catch (e) {
      alert(`Add failed: ${e?.response?.data?.detail || e.message}`);
    } finally { setMAdding(false); }
  };

  const submitSettle = async () => {
    if (!settleRow?.exit_price) { alert("Exit price required"); return; }
    try {
      const r = await axios.post(
        `${API}/lottery/settle?ticker=${settleRow.ticker}&exit_price=${settleRow.exit_price}&play_date=${settleRow.date}`
      );
      alert(`Realized P/L: ${r.data.realized_pct?.toFixed(2)}%`);
      setSettleRow(null);
      reloadAll();
    } catch (e) {
      alert(`Settle failed: ${e?.response?.data?.detail || e.message}`);
    }
  };

  const picks = current?.picks || [];
  const tr = history?.track_record || {};
  const tierCounts = picks.reduce((acc, p) => {
    acc[p.tier] = (acc[p.tier] || 0) + 1; return acc;
  }, {});

  return (
    <CrtShell title="LOTTERY PICKS"
      headerRight={
        <button data-testid="lottery-scan-btn" onClick={runLotteryScan} disabled={scanning}
          style={{
            background: "transparent", border: `0.5px solid ${accent}`, color: accent,
            fontSize: 11, padding: "8px 16px", cursor: scanning ? "wait" : "pointer",
            letterSpacing: "0.14em", fontFamily: "JetBrains Mono", fontWeight: 700,
          }}>[ {scanning ? "SCANNING..." : "▶ LOTTERY SCAN"} ]</button>
      }>
      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="JACKPOT" value={tierCounts.JACKPOT || 0} sub="MAX $500" color={TIER_COLOR.JACKPOT} accentBar />
        <Stat label="HOT" value={tierCounts.HOT || 0} sub="MAX $200" color={TIER_COLOR.HOT} />
        <Stat label="WARM" value={tierCounts.WARM || 0} sub="MAX $100" color={TIER_COLOR.WARM} />
        <Stat label="COLD" value={tierCounts.COLD || 0} sub="MAX $50" color={TIER_COLOR.COLD} />
        <Stat label="OPEN POSITIONS" value={tr.open || 0} sub={`${tr.unrealized_winners || 0} GREEN`} color={accent} />
        <Stat label="UNREALIZED P&L" value={tr.unrealized_avg_pct != null ? `${tr.unrealized_avg_pct >= 0 ? "+" : ""}${tr.unrealized_avg_pct}%` : "—"}
              sub="OPEN AVG" color={(tr.unrealized_avg_pct ?? 0) >= 0 ? "#4ade80" : "#f87171"} />
        <Stat label="LIFETIME HIT RATE" value={tr.hit_rate != null ? `${(tr.hit_rate * 100).toFixed(0)}%` : "—"}
              sub={`${tr.winners || 0}/${tr.settled || 0} SETTLED`} color="#4ade80" />
        <Stat label="AVG WINNER" value={tr.avg_winner_pct != null ? `+${tr.avg_winner_pct}%` : "—"}
              sub="ON CONTRACT" color="#4ade80" />
      </div>

      <div style={{ display: "flex", gap: 4, marginBottom: 18 }}>
        {[["current", "CURRENT SCAN"], ["screener", "SCREENER"],
            ["manual", `MANUAL · ${manualPlays.filter(p => p.is_active).length}`],
            ["history", "TRACK RECORD"]].map(([k, l]) => (
          <button key={k} data-testid={`lottery-tab-${k}`} onClick={() => setTab(k)}
            style={{
              background: tab === k ? `${accent}15` : "transparent",
              border: `0.5px solid ${tab === k ? accent : dim}`,
              color: tab === k ? accent : muted,
              fontSize: 11, padding: "8px 16px", cursor: "pointer",
              letterSpacing: "0.12em", fontFamily: "JetBrains Mono", fontWeight: 700,
            }}>{l}</button>
        ))}
      </div>

      {tab === "screener" && (
        <Card title={`DEDICATED FINVIZ SCREENER · ${screener.length} CANDIDATES`}>
          {!screener.length ? <div style={{ color: muted, padding: 20 }}>Click LOTTERY SCAN to run the dedicated Finviz screener (float&lt;20M · $1-$20 · vol&gt;2× · SI&gt;15%).</div> : (
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead><tr>
                <th style={th}>TICKER</th><th style={th}>PRICE</th><th style={th}>SCORE</th>
                <th style={th}>BONUSES</th><th style={th}>RISK $</th><th style={th}>ADD</th>
              </tr></thead>
              <tbody>{screener.map((c, i) => (
                <tr key={i} className="row-hover" style={{ borderTop: hairline }}
                  data-testid={`lottery-screener-${c.ticker}`}>
                  <td style={{ ...td, color: accent, fontWeight: 700 }}>${c.ticker}</td>
                  <td style={td}>${c.price?.toFixed(2)}</td>
                  <td style={{ ...td, color: TIER_COLOR[c.tier], fontWeight: 700 }}>{c.lottery_score}</td>
                  <td style={{ ...td, fontSize: 9, color: accent2 }}>{(c.bonuses || []).join(" · ") || "—"}</td>
                  <td style={td}>${c.suggested_risk}</td>
                  <td style={td}>
                    <button data-testid={`add-${c.ticker}`} onClick={async () => {
                      const entry = prompt(`Entry price for ${c.ticker}? (current $${c.price})`, c.price?.toFixed(2));
                      if (!entry) return;
                      await axios.post(`${API}/lottery/manual`, {
                        ticker: c.ticker, entry_price: Number(entry),
                        lottery_score: c.lottery_score, risk_amount: c.suggested_risk,
                      });
                      reloadAll();
                    }} style={{ background: "transparent", border: `0.5px solid ${accent}`,
                                  color: accent, fontSize: 9, padding: "4px 10px", cursor: "pointer", fontWeight: 700 }}>
                      ADD
                    </button>
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      )}

      {tab === "manual" && (
        <>
          <Card title="ADD MANUAL PLAY · INSIDER OR EARNINGS RUNNER">
            <div style={{ display: "flex", gap: 10, alignItems: "flex-end", padding: 12, flexWrap: "wrap" }}>
              <FormInput label="TICKER" placeholder="ABCD" value={mForm.ticker}
                onChange={v => setMForm(f => ({ ...f, ticker: v }))} testid="manual-form-ticker" width={110} />
              <FormInput label="ENTRY PRICE $" placeholder="4.20" value={mForm.entry_price}
                onChange={v => setMForm(f => ({ ...f, entry_price: v }))} testid="manual-form-entry" width={130} />
              <FormInput label="LOTTERY SCORE" placeholder="70" value={mForm.lottery_score}
                onChange={v => setMForm(f => ({ ...f, lottery_score: v }))} testid="manual-form-score" width={130} />
              <FormInput label="RISK $" placeholder="50" value={mForm.risk_amount}
                onChange={v => setMForm(f => ({ ...f, risk_amount: v }))} testid="manual-form-risk" width={100} />
              <button data-testid="manual-form-submit" onClick={addManual} disabled={mAdding}
                style={{ background: "transparent", border: `0.5px solid ${accent}`, color: accent,
                          fontSize: 11, padding: "8px 22px", cursor: mAdding ? "wait" : "pointer", fontWeight: 700,
                          letterSpacing: "0.12em", fontFamily: "JetBrains Mono" }}>
                [ {mAdding ? "ADDING…" : "+ ADD PLAY"} ]
              </button>
            </div>
          </Card>

          <Card title={`ACTIVE MANUAL PLAYS · ${manualPlays.filter(p => p.is_active).length}`}>
            {!manualPlays.filter(p => p.is_active).length ? (
              <div style={{ color: muted, padding: 20 }}>No active manual plays yet. Use the form above to add one.</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead><tr>
                  <th style={th}>TICKER</th><th style={th}>ENTRY</th><th style={th}>CURRENT</th>
                  <th style={th}>P&L</th><th style={th}>PEAK</th><th style={th}>SCORE</th>
                  <th style={th}>RISK $</th><th style={th}></th>
                </tr></thead>
                <tbody>{manualPlays.filter(p => p.is_active).map((p) => {
                  const pnl = p.current_price ? ((p.current_price - p.entry_price) / p.entry_price * 100) : 0;
                  const peak = p.peak_price ? ((p.peak_price - p.entry_price) / p.entry_price * 100) : 0;
                  const isSettling = settleRow?.ticker === p.ticker && settleRow?.date === p.date;
                  return (
                    <tr key={`${p.ticker}-${p.date}`} style={{ borderTop: hairline }} data-testid={`manual-${p.ticker}`}>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                      <td style={td}>${p.entry_price?.toFixed(2)}</td>
                      <td style={td}>${p.current_price?.toFixed(2) || "—"}</td>
                      <td style={{ ...td, color: pnl >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                        {pnl >= 0 ? "+" : ""}{pnl.toFixed(2)}%
                      </td>
                      <td style={{ ...td, color: "#4ade80", fontWeight: 700 }}>+{peak.toFixed(2)}%</td>
                      <td style={td}>{p.lottery_score}</td>
                      <td style={td}>${p.risk_amount}</td>
                      <td style={td}>
                        {isSettling ? (
                          <span style={{ display: "inline-flex", gap: 4, alignItems: "center" }}>
                            <input data-testid={`settle-input-${p.ticker}`}
                              placeholder="EXIT $" value={settleRow.exit_price}
                              onChange={e => setSettleRow(s => ({ ...s, exit_price: e.target.value }))}
                              style={inputCss(80)} />
                            <button data-testid={`settle-confirm-${p.ticker}`} onClick={submitSettle}
                              style={btnCss("#4ade80")}>OK</button>
                            <button data-testid={`settle-cancel-${p.ticker}`} onClick={() => setSettleRow(null)}
                              style={btnCss(muted)}>X</button>
                          </span>
                        ) : (
                          <button data-testid={`settle-${p.ticker}`}
                            onClick={() => setSettleRow({ ticker: p.ticker, date: p.date, exit_price: "" })}
                            style={{ background: "transparent", border: `0.5px solid #f87171`, color: "#f87171",
                                      fontSize: 9, padding: "4px 10px", cursor: "pointer", fontWeight: 700, marginRight: 6 }}>
                            SETTLE
                          </button>
                        )}
                        <button data-testid={`send-tf-${p.ticker}`}
                          onClick={async () => {
                            if (!confirm(`Send $${p.risk_amount} of ${p.ticker} to Trade Floor?`)) return;
                            const r = await axios.post(
                              `${API}/trade_floor/manual_send?ticker=${p.ticker}&risk_dollars=${p.risk_amount}&source=lottery_manual`
                            );
                            alert(r.data.ok ? `Sent · notional $${r.data.notional}` : `Failed: ${r.data.reason}`);
                          }}
                          style={{ background: "transparent", border: `0.5px solid ${accent}`, color: accent,
                                    fontSize: 9, padding: "4px 10px", cursor: "pointer", fontWeight: 700 }}>
                          → TRADE FLOOR
                        </button>
                      </td>
                    </tr>);
                })}</tbody>
              </table>
            )}
          </Card>

          <Card title={`MANUAL TRACK RECORD · ${manualTR?.settled || 0} SETTLED · ISOLATED`}>
            <div style={{ display: "flex", gap: 24, padding: "10px 0", borderBottom: hairline, marginBottom: 12 }}>
              <Stat label="WIN RATE" value={manualTR?.win_rate != null ? `${(manualTR.win_rate * 100).toFixed(0)}%` : "—"}
                    sub={`${manualTR?.winners || 0}/${manualTR?.settled || 0}`} color="#4ade80" />
              <Stat label="AVG WINNER" value={manualTR?.avg_winner_pct != null ? `+${manualTR.avg_winner_pct}%` : "—"} color="#4ade80" />
              <Stat label="AVG LOSER" value={manualTR?.avg_loser_pct != null ? `${manualTR.avg_loser_pct}%` : "—"} color="#f87171" />
              <Stat label="TOTAL P/L" value={manualTR?.total_pnl_pct != null ? `${manualTR.total_pnl_pct >= 0 ? "+" : ""}${manualTR.total_pnl_pct}%` : "—"}
                    color={(manualTR?.total_pnl_pct ?? 0) >= 0 ? "#4ade80" : "#f87171"} />
            </div>
            {!manualTR?.history?.length ? (
              <div style={{ color: muted, padding: 14 }}>No settled plays yet.</div>
            ) : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead><tr>
                  <th style={th}>TICKER</th><th style={th}>ENTRY</th><th style={th}>EXIT</th>
                  <th style={th}>P&L</th><th style={th}>SCORE</th><th style={th}>TF?</th>
                </tr></thead>
                <tbody>{manualTR.history.map((p, i) => (
                  <tr key={`${p.ticker}-${p.exit_date || p.date || i}`} style={{ borderTop: hairline }}>
                    <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                    <td style={td}>${p.entry_price?.toFixed(2)}</td>
                    <td style={td}>${p.exit_price?.toFixed(2)}</td>
                    <td style={{ ...td, color: (p.realized_pct || 0) >= 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                      {p.realized_pct >= 0 ? "+" : ""}{p.realized_pct?.toFixed(2)}%
                    </td>
                    <td style={td}>{p.lottery_score || "—"}</td>
                    <td style={{ ...td, color: p.sent_to_trade_floor ? accent : muted }}>{p.sent_to_trade_floor ? "YES" : "no"}</td>
                  </tr>
                ))}</tbody>
              </table>
            )}
          </Card>
        </>
      )}

      {tab === "current" && (
        picks.length === 0 ? (
          <Card title="NO ACTIVE LOTTERY PICKS">
            <div style={{ color: muted, padding: 20 }}>
              Run a scan to surface new lottery candidates.
            </div>
          </Card>
        ) : (
          <Card title={`LOTTERY · ${picks.length} ACTIVE PICKS · ${current?.scan_at?.slice(0, 16) || ""}`}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: dim, letterSpacing: "0.14em", textAlign: "left" }}>
                  <th style={th}>TIER</th>
                  <th style={th}>TICKER</th>
                  <th style={th}>SCORE</th>
                  <th style={th}>CONTRACT</th>
                  <th style={th}>COST</th>
                  <th style={th}>BREAKEVEN</th>
                  <th style={th}>P(2X)</th>
                  <th style={th}>P(10X)</th>
                  <th style={th}>P(LOSS)</th>
                  <th style={th}>MAX BET</th>
                  <th style={th}></th>
                </tr>
              </thead>
              <tbody>
                {picks.map(p => (
                  <tr key={p.ticker} data-testid={`pick-${p.ticker}`} className="row-hover" style={{ borderTop: hairline }}>
                    <td style={{ ...td, fontWeight: 700 }}>
                      <span style={{
                        color: TIER_COLOR[p.tier], padding: "3px 8px",
                        border: `0.5px solid ${TIER_COLOR[p.tier]}66`,
                        background: `${TIER_COLOR[p.tier]}08`,
                        letterSpacing: "0.14em", fontSize: 10,
                        boxShadow: TIER_GLOW[p.tier],
                      }}>{p.tier}</span>
                    </td>
                    <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                    <td style={td}>{p.score?.toFixed(0)}/100</td>
                    <td style={td}>
                      {p.contract ? `$${p.contract.strike}C ${p.contract.exp?.slice(5)}` : "—"}
                    </td>
                    <td style={{ ...td, color: "#fff" }}>
                      {p.contract ? `$${p.contract.total_cost}` : "—"}
                    </td>
                    <td style={td}>{p.contract ? `$${p.contract.breakeven}` : "—"}</td>
                    <td style={{ ...td, color: "#4ade80" }}>
                      {p.ev ? `${(p.ev.p_double * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td style={{ ...td, color: accent }}>
                      {p.ev ? `${(p.ev.p_10x * 100).toFixed(1)}%` : "—"}
                    </td>
                    <td style={{ ...td, color: "#f87171" }}>
                      {p.ev ? `${(p.ev.p_total_loss * 100).toFixed(0)}%` : "—"}
                    </td>
                    <td style={{ ...td, color: TIER_COLOR[p.tier], fontWeight: 700 }}>
                      ${p.max_bet}
                    </td>
                    <td style={{ padding: "8px" }}>
                      <button data-testid={`lottery-send-tf-${p.ticker}`}
                        onClick={async () => {
                          if (!confirm(`Send $${p.max_bet} of ${p.ticker} to Trade Floor (fractional, ATR stop)?`)) return;
                          try {
                            const r = await axios.post(
                              `${API}/trade_floor/manual_send?ticker=${p.ticker}&risk_dollars=${p.max_bet}&source=lottery`
                            );
                            alert(r.data.ok ? `Sent → $${r.data.notional} notional · stop $${r.data.stop?.toFixed(2)}` : `Failed: ${r.data.reason}`);
                          } catch { alert("Trade Floor send failed"); }
                        }}
                        style={{
                          background: "transparent", border: `0.5px solid ${accent}`,
                          color: accent, fontSize: 9, padding: "4px 8px", cursor: "pointer",
                          fontWeight: 700, letterSpacing: "0.1em",
                        }}>→ TRADE FLOOR</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )
      )}

      {tab === "history" && (
        <Card title={`TRACK RECORD · ${history?.picks?.length || 0} AUTO-BOUGHT PICKS · 14D`}>
          {!history?.picks?.length ? (
            <div style={{ color: muted, padding: 20 }}>
              No picks scored ≥ 50/100 yet — they'll auto-log here once the next scan produces them.
            </div>
          ) : (
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
              <thead>
                <tr style={{ color: dim, letterSpacing: "0.14em", textAlign: "left" }}>
                  <th style={th}>DATE</th><th style={th}>TIER</th><th style={th}>TICKER</th>
                  <th style={th}>STRIKE</th><th style={th}>ENTRY</th>
                  <th style={th}>CURRENT</th><th style={th}>SETTLED</th><th style={th}>P&L</th>
                </tr>
              </thead>
              <tbody>
                {history.picks.map((p, i) => {
                  const cur = p.settled_ask != null ? p.settled_ask : p.current_ask;
                  const ret = cur != null && p.entry_ask
                    ? ((cur - p.entry_ask) / p.entry_ask * 100) : null;
                  const isOpen = p.settled_ask == null;
                  return (
                    <tr key={i} className="row-hover" style={{ borderTop: hairline }}>
                      <td style={td}>{p.date}</td>
                      <td style={{ ...td, color: TIER_COLOR[p.tier], fontWeight: 700 }}>{p.tier}</td>
                      <td style={{ ...td, color: accent, fontWeight: 700 }}>${p.ticker}</td>
                      <td style={td}>${p.strike}C {p.exp?.slice(5)}</td>
                      <td style={td}>${p.entry_ask}</td>
                      <td style={{ ...td, color: isOpen ? accent2 : muted }}>
                        {p.current_ask != null ? `$${p.current_ask}` : "—"}
                      </td>
                      <td style={td}>{p.settled_ask != null ? `$${p.settled_ask}` : "OPEN"}</td>
                      <td style={{ ...td, color: ret == null ? muted : ret > 0 ? "#4ade80" : "#f87171", fontWeight: 700 }}>
                        {ret != null ? `${ret >= 0 ? "+" : ""}${ret.toFixed(0)}%` : "—"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Card>
      )}
    </CrtShell>
  );
}

const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400 };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em" };

const inputCss = (w = 110) => ({
  background: "rgba(0,0,0,0.4)", border: `0.5px solid ${dim}`, color: labelLight,
  padding: "5px 8px", fontSize: 11, fontFamily: "JetBrains Mono", letterSpacing: "0.06em",
  width: w, outline: "none",
});

const btnCss = (c) => ({
  background: "transparent", border: `0.5px solid ${c}`, color: c,
  fontSize: 9, padding: "4px 8px", cursor: "pointer", fontWeight: 700,
  fontFamily: "JetBrains Mono", letterSpacing: "0.08em",
});

function FormInput({ label, placeholder, value, onChange, testid, width }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span style={{ color: dim, fontSize: 9, letterSpacing: "0.14em" }}>{label}</span>
      <input data-testid={testid} placeholder={placeholder} value={value}
        onChange={e => onChange(e.target.value)} style={inputCss(width)} />
    </div>
  );
}
