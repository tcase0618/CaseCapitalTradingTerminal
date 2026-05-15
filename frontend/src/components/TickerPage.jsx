import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const { accent, dim, muted, labelLight, hairline } = tokens;

export default function TickerPage() {
  const { ticker } = useParams();
  const [data, setData] = useState(null);
  const [opts, setOpts] = useState(null);
  const [flow, setFlow] = useState(null);

  useEffect(() => {
    if (!ticker) return;
    axios.get(`${API}/ticker/${ticker}`).then(r => setData(r.data)).catch(() => setData({ ticker }));
    axios.get(`${API}/options/${ticker}`).then(r => setOpts(r.data.options)).catch(() => {});
    axios.get(`${API}/flow/${ticker}`).then(r => setFlow(r.data.flow)).catch(() => {});
  }, [ticker]);

  if (!data) return (
    <CrtShell title={`LOADING ${ticker?.toUpperCase()}...`}>
      <div style={{ color: muted, fontSize: 14 }}>FETCHING INTELLIGENCE...</div>
    </CrtShell>
  );

  const t = data.ticker || ticker?.toUpperCase();
  const risk = data.risk || {};
  const targets = data.targets || {};
  const sq = data.squeeze || {};
  const tt = data.time_target || {};
  const pnl = data.pnl_record || {};
  const fund = data.fundamentals || {};

  return (
    <CrtShell title={`$${t} — ${fund.shortName || fund.longName || ""}`}
      headerRight={
        <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
          <span style={{ fontSize: 22, color: "#fff", fontWeight: 700, fontFamily: "Courier New" }}>
            ${data.price ? data.price.toFixed(2) : "—"}
          </span>
          <span style={{
            fontSize: 14, color: (data.change_pct || 0) >= 0 ? "#4ade80" : "#f87171",
          }}>
            {data.change_pct != null
              ? `${data.change_pct >= 0 ? "+" : ""}${data.change_pct}%`
              : "—"}
          </span>
        </div>
      }>
      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 20 }}>
        <Stat label="SIGNAL SCORE" value={`${data.signal_score || 0}/10`} color={accent} />
        <Stat label="LEARNING SCORE" value={data.learning_score || 0} color={accent} sub="AXIOM WEIGHTED" />
        <Stat label="RISK" value={risk.level || "—"}
              color={risk.level === "LOW" ? "#4ade80" : risk.level === "MEDIUM" ? accent
                     : risk.level === "HIGH" ? "#fb923c" : "#f87171"} />
        <Stat label="SQUEEZE" value={sq.score != null ? `${sq.score}/100` : "—"} sub={sq.band} />
        <Stat label="TIMES FOUND" value={data.times_found || 1}
              sub={data.first_found ? `FIRST ${data.first_found}` : "TODAY"} />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div>
          <Card title="SIGNALS DETECTED">
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {(data.signals || []).map((s) => (
                <span key={s} style={{
                  fontSize: 12, padding: "5px 11px", border: `0.5px solid rgba(200,168,75,0.35)`,
                  color: accent, background: "rgba(200,168,75,0.07)",
                  letterSpacing: "0.08em", fontWeight: 700,
                }}>{s}</span>
              ))}
              {(!data.signals || data.signals.length === 0) && (
                <span style={{ color: muted, fontSize: 12 }}>None in latest scan</span>
              )}
            </div>
          </Card>

          <Card title="PRICE TARGETS">
            <Row k="ENTRY ZONE" v={`$${data.entry_low?.toFixed(2) || "—"} — $${data.entry_high?.toFixed(2) || "—"}`} />
            <Row k="TARGET (BEAR)" v={`$${targets.target_low?.toFixed(2) || "—"}`} c="#f87171" />
            <Row k="TARGET (BLENDED)" v={`$${targets.target_blended?.toFixed(2) || "—"}`} c={accent}
                 sub={targets.upside_blended != null ? `${targets.upside_blended >= 0 ? "+" : ""}${targets.upside_blended}%` : ""} />
            <Row k="TARGET (BULL)" v={`$${targets.target_high?.toFixed(2) || "—"}`} c="#4ade80" />
            <Row k="STOP LOSS" v={`$${data.stop_loss?.toFixed(2) || "—"}`} c="#f87171" />
            <Row k="CONVICTION" v={(data.conviction || "—").toUpperCase()} />
            <Row k="TIME HORIZON" v={(data.time_horizon || "—").toUpperCase()} />
            {tt.target_date && <Row k="TARGET DATE" v={tt.target_date} c={accent}
                 sub={`HOLD ${tt.hold_period_low}–${tt.hold_period_high}d`} />}
          </Card>

          <Card title="THESIS">
            <div style={{ fontSize: 13, color: "#e5e7eb", lineHeight: 1.7, letterSpacing: "0.02em" }}>
              {data.thesis || "No thesis available. Run a scan."}
            </div>
          </Card>
        </div>

        <div>
          <Card title="OPTIONS INTELLIGENCE">
            {!opts ? (
              <div style={{ color: muted, fontSize: 13 }}>No options data available.</div>
            ) : (
              <>
                <Row k="STRATEGY" v={(opts.strategy_name || opts.strategy || "—").toUpperCase()} c={accent} />
                <Row k="DIRECTION" v={opts.direction || "—"} />
                <Row k="IV RANK" v={`${opts.iv_rank || "—"}% (${opts.iv_label || "—"})`}
                     c={opts.iv_rank < 30 ? "#4ade80" : opts.iv_rank > 70 ? "#f87171" : accent} />
                <Row k="CRUSH RISK" v={opts.crush_risk || "—"}
                     c={opts.crush_risk === "SEVERE" ? "#f87171"
                         : opts.crush_risk === "HIGH" ? "#fb923c"
                         : opts.crush_risk === "LOW" ? "#4ade80" : accent} />
                {opts.contract && (
                  <>
                    <div style={{ marginTop: 14, paddingTop: 12, borderTop: hairline,
                                   fontSize: 10, color: dim, letterSpacing: "0.14em" }}>
                      {"// BEST CONTRACT"}
                    </div>
                    <Row k="STRIKE" v={`$${opts.contract.strike}${opts.contract.type}`} c={accent} />
                    <Row k="EXPIRATION" v={opts.contract.expiration} />
                    <Row k="PREMIUM" v={`$${opts.contract.premium}`} />
                    <Row k="MAX LOSS" v={`$${opts.contract.max_loss}`} c="#f87171" />
                    <Row k="LIQUIDITY" v={opts.contract.liquidity}
                         c={opts.contract.liquidity === "GOOD" ? "#4ade80"
                             : opts.contract.liquidity === "WARN" ? "#fb923c" : "#f87171"} />
                    <Row k="CONTRACTS AT $100" v={opts.contract.contracts_at_budget} />
                  </>
                )}
                {opts.spread && (
                  <>
                    <div style={{ marginTop: 14, paddingTop: 12, borderTop: hairline,
                                   fontSize: 10, color: dim, letterSpacing: "0.14em" }}>
                      {"// SPREAD DETAILS"}
                    </div>
                    <Row k="BUY/SELL" v={`$${opts.spread.buy_strike} / $${opts.spread.sell_strike}`} c={accent} />
                    <Row k="NET DEBIT" v={`$${opts.spread.net_debit}`} />
                    <Row k="MAX PROFIT" v={`$${opts.spread.max_profit}`} c="#4ade80" />
                    <Row k="MAX LOSS" v={`$${opts.spread.max_loss}`} c="#f87171" />
                    <Row k="BREAK EVEN" v={`$${opts.spread.break_even}`} />
                    <Row k="R/R" v={`${opts.spread.risk_reward}:1`} c={accent} />
                  </>
                )}
                <div style={{ marginTop: 12, padding: "10px 12px", background: "rgba(200,168,75,0.04)",
                               borderLeft: `2px solid ${accent}`, fontSize: 12, color: labelLight,
                               lineHeight: 1.6 }}>
                  {opts.crush_recommendation || opts.strategy_reason || ""}
                </div>
              </>
            )}
          </Card>

          <Card title="UNUSUAL FLOW">
            {!flow ? (
              <div style={{ color: muted, fontSize: 13 }}>No flow data.</div>
            ) : (
              <>
                <Row k="FLOW BIAS" v={flow.flow_bias}
                     c={flow.flow_bias === "BULLISH" ? "#4ade80"
                         : flow.flow_bias === "BEARISH" ? "#f87171" : accent} />
                <Row k="CALL VOLUME" v={flow.total_call_volume?.toLocaleString() || 0} />
                <Row k="PUT VOLUME" v={flow.total_put_volume?.toLocaleString() || 0} />
                <Row k="C/P RATIO" v={flow.call_put_ratio || 0} c={accent} />
                <Row k="CALL VOL/OI" v={flow.call_volume_ratio || 0} />
                {flow.call_sweep && (
                  <div style={{ marginTop: 12, padding: "8px 12px",
                                 background: "rgba(45,212,191,0.07)",
                                 borderLeft: "2px solid #2dd4bf", color: "#2dd4bf",
                                 fontWeight: 700, fontSize: 12 }}>
                    🔥 CALL SWEEP DETECTED
                  </div>
                )}
              </>
            )}
          </Card>

          <Card title="P&L HISTORY">
            <Row k="FIRST FOUND" v={data.first_found || "—"} />
            <Row k="TIMES IN SCAN" v={data.times_found || 1} />
            <Row k="RETURN 7D" v={pnl.return_7d != null ? `${pnl.return_7d >= 0 ? "+" : ""}${pnl.return_7d}%` : "—"}
                 c={pctColor(pnl.return_7d)} />
            <Row k="RETURN 30D" v={pnl.return_30d != null ? `${pnl.return_30d >= 0 ? "+" : ""}${pnl.return_30d}%` : "—"}
                 c={pctColor(pnl.return_30d)} />
            <Row k="RETURN 90D" v={pnl.return_90d != null ? `${pnl.return_90d >= 0 ? "+" : ""}${pnl.return_90d}%` : "—"}
                 c={pctColor(pnl.return_90d)} />
          </Card>
        </div>
      </div>

      <div style={{ marginTop: 20, padding: "10px 0" }}>
        <Link to="/" style={{ color: muted, fontSize: 12, textDecoration: "none", letterSpacing: "0.1em" }}>
          ← BACK TO DASHBOARD
        </Link>
      </div>
    </CrtShell>
  );
}

function Row({ k, v, c = "#e5e7eb", sub = "" }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", alignItems: "baseline",
      padding: "7px 0", fontSize: 13, letterSpacing: "0.04em",
    }}>
      <span style={{ color: dim, fontSize: 11, letterSpacing: "0.12em" }}>{k}</span>
      <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
        <span style={{ color: c, fontWeight: c === "#e5e7eb" ? 400 : 700 }}>{v}</span>
        {sub && <span style={{ color: muted, fontSize: 11 }}>{sub}</span>}
      </div>
    </div>
  );
}

const pctColor = (v) => v == null ? muted : v > 0 ? "#4ade80" : v < 0 ? "#f87171" : labelLight;
