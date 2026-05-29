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

  useEffect(() => {
    axios.get(`${API}/v32/lottery/current`).then(r => setCurrent(r.data)).catch(() => {});
    axios.get(`${API}/v32/lottery`).then(r => setHistory(r.data)).catch(() => {});
  }, []);

  const picks = current?.picks || [];
  const tr = history?.track_record || {};
  const tierCounts = picks.reduce((acc, p) => {
    acc[p.tier] = (acc[p.tier] || 0) + 1; return acc;
  }, {});

  return (
    <CrtShell title="LOTTERY PICKS">
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
        {[["current", "CURRENT SCAN"], ["history", "TRACK RECORD"]].map(([k, l]) => (
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
