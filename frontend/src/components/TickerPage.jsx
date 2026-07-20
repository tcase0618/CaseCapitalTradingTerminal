import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";
import TradingViewMiniChart from "./TradingViewMiniChart";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, dim, muted, labelLight, hairline } = tokens;

export default function TickerPage() {
  const { ticker } = useParams();
  const [data, setData] = useState(null);
  const [opts, setOpts] = useState(null);
  const [flow, setFlow] = useState(null);
  const [freeData, setFreeData] = useState(null);

  useEffect(() => {
    if (!ticker) return;
    axios.get(`${API}/ticker/${ticker}`).then(r => setData(r.data)).catch(() => setData({ ticker }));
    axios.get(`${API}/options/${ticker}`).then(r => setOpts(r.data.options)).catch(() => {});
    axios.get(`${API}/flow/${ticker}`).then(r => setFlow(r.data.flow)).catch(() => {});
    axios.get(`${API}/data/free/ticker/${ticker}`).then(r => setFreeData(r.data)).catch(() => setFreeData(null));
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
  const companyName = fund.longName || fund.shortName || fund.name || data.name || "";
  const changeColor = (data.change_pct || 0) >= 0 ? "#4ade80" : "#f87171";
  const changeArrow = data.change_pct == null ? "" : data.change_pct >= 0 ? "▲" : "▼";

  return (
    <CrtShell title={`$${t} — ${companyName}`}
      headerRight={
        <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
          <span style={{ fontSize: 22, color: "#fff", fontWeight: 700, fontFamily: "Courier New" }}>
            ${data.price ? data.price.toFixed(2) : "—"}
          </span>
          <span style={{
            fontSize: 14,
            color: data.change_pct == null ? muted : changeColor,
            fontWeight: 800,
            letterSpacing: "0.08em",
          }}>
            {data.change_pct != null
              ? `${changeArrow} ${data.change_pct >= 0 ? "+" : ""}${data.change_pct}%`
              : "—"}
          </span>
        </div>
      }>
      <TradingViewMiniChart ticker={t} companyName={companyName} />

      <div style={{ display: "flex", background: tokens.cardBg, border: hairline, marginBottom: 20 }}>
        <Stat label="SIGNAL SCORE" value={`${data.signal_score || 0}/10`} color={accent} />
        <Stat label="CASE SCORE" value={data.learning_score || 0} color={accent} sub="WEIGHTED" />
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

          <KeyRatiosCard freeData={freeData} />
          <FreeDataCard freeData={freeData} />
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
            <Row k="FIRST ALERT PRICE" v={data.first_alert?.price ? `$${Number(data.first_alert.price).toFixed(2)}` : "—"} />
            <Row k="SINCE FIRST ALERT" v={data.change_since_first_alert_pct != null ? `${data.change_since_first_alert_pct >= 0 ? "+" : ""}${data.change_since_first_alert_pct}%` : "—"}
                 c={pctColor(data.change_since_first_alert_pct)} />
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

function KeyRatiosCard({ freeData }) {
  const ratios = freeData?.key_ratios || {};
  const meta = freeData?.key_ratios_meta || {};
  const groups = [
    {
      label: "LIQUIDITY",
      keys: ["current_ratio", "quick_ratio", "working_capital"],
    },
    {
      label: "SOLVENCY & LEVERAGE",
      keys: ["debt_to_equity", "debt_to_assets", "interest_coverage"],
    },
    {
      label: "PROFITABILITY",
      keys: ["gross_profit_margin", "operating_margin", "ebitda_margin", "net_profit_margin", "revenue_growth_rate"],
    },
    {
      label: "MANAGEMENT EFFICIENCY",
      keys: ["roe", "roa", "inventory_turnover"],
    },
    {
      label: "CASH FLOW & MARKET VALUE",
      keys: ["operating_cash_flow", "cash_conversion", "free_cash_flow_margin", "pe_ratio"],
    },
  ];
  const ratioKeys = groups.flatMap(group => group.keys);
  const ratioSummary = ratioKeys.reduce((acc, key) => {
    const status = (ratios[key] || fallbackRatio(key)).status || "missing";
    if (status === "good") acc.good += 1;
    if (status === "watch") acc.watch += 1;
    if (status === "bad") acc.bad += 1;
    if (status !== "missing") acc.scored += 1;
    return acc;
  }, { good: 0, watch: 0, bad: 0, scored: 0 });
  const netSignal = ratioSummary.good - ratioSummary.bad;
  const ratioSignal = ratioSummary.scored === 0
    ? { label: "NO SIGNAL", color: muted }
    : netSignal >= 3
      ? { label: "BULLISH", color: "#4ade80" }
      : netSignal <= -2
        ? { label: "BEARISH", color: "#f87171" }
        : { label: "NEUTRAL", color: "#facc15" };

  return (
    <Card title="KEY RATIOS">
      {!freeData ? (
        <div style={{ color: muted, fontSize: 13 }}>Ratio model loading...</div>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          <div style={{
            display: "grid",
            gap: 7,
            padding: "9px 10px",
            border: `0.5px solid ${ratioSignal.color}`,
            background: "rgba(255,255,255,0.025)",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
              <div style={{ display: "flex", gap: 10, alignItems: "baseline", flexWrap: "wrap" }}>
                <span style={{ color: "#4ade80", fontSize: 13, fontWeight: 800 }}>
                  {ratioSummary.good} GOOD
                </span>
                <span style={{ color: "#facc15", fontSize: 13, fontWeight: 800 }}>
                  {ratioSummary.watch} WATCH
                </span>
                <span style={{ color: "#f87171", fontSize: 13, fontWeight: 800 }}>
                  {ratioSummary.bad} BAD
                </span>
              </div>
              <div style={{
                color: ratioSignal.color,
                fontSize: 14,
                fontWeight: 900,
                letterSpacing: "0.12em",
                whiteSpace: "nowrap",
              }}>
                {ratioSignal.label}
              </div>
            </div>
            <div style={{ color: muted, fontSize: 10, letterSpacing: "0.08em", lineHeight: 1.5 }}>
              UPDATED FROM {meta.form || "SEC"} {meta.fiscal_year ? `FY${meta.fiscal_year}` : ""}
              {meta.fiscal_period ? ` ${meta.fiscal_period}` : ""}
              {meta.period_end ? ` | PERIOD ${meta.period_end}` : ""}
              {meta.filed ? ` | FILED ${meta.filed}` : ""}
            </div>
          </div>
          {groups.map(group => (
            <div key={group.label}>
              <div style={{ fontSize: 10, color: dim, letterSpacing: "0.14em", marginBottom: 8 }}>
                {`// ${group.label}`}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "7px 10px" }}>
                {group.keys.map(key => (
                  <RatioCell key={key} ratio={ratios[key] || fallbackRatio(key)} />
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function FreeDataCard({ freeData }) {
  const secFacts = freeData?.sec?.companyfacts || {};
  const secLookup = freeData?.sec?.lookup || {};
  const metrics = secFacts.metrics || {};
  const trials = freeData?.clinical_trials || {};
  const fda = freeData?.openfda || {};
  const alpha = freeData?.alpha_vantage || {};
  const overview = alpha.overview || {};
  const visibleSources = (freeData?.sources || []).filter(source => {
    const quality = source.quality || "";
    if (quality === "no_match" || quality === "optional") return false;
    if (source.key === "alpha_vantage" && !alpha.ok) return false;
    return source.ok || quality === "live";
  });
  const showTrials = Boolean(trials.ok && (trials.returned_count || 0) > 0);
  const showFda = Boolean(fda.ok && ((fda.adverse_events_top || []).length || (fda.recalls_by_class || []).length));
  const showAlpha = Boolean(alpha.ok && Object.keys(overview).length > 0);

  return (
    <Card title="FREE DATA INTELLIGENCE">
      {!freeData ? (
        <div style={{ color: muted, fontSize: 13 }}>Source bundle loading...</div>
      ) : (
        <>
          {visibleSources.length > 0 && (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 12 }}>
              {visibleSources.map(source => (
                <SourcePill key={source.key} label={source.key} quality={source.quality} ok={source.ok} />
              ))}
            </div>
          )}

          <div style={{ paddingBottom: 10, borderBottom: hairline, marginBottom: 10 }}>
            <Row k="SEC ENTITY" v={secFacts.entity_name || secLookup.company_name || "NO MATCH"} c={secFacts.ok ? accent : muted} />
            <Row k="CIK / EXCHANGE" v={secLookup.cik ? `${secLookup.cik} / ${secLookup.exchange || "-"}` : "-"} />
            <MetricRow label="REVENUE" metric={metrics.revenue} />
            <MetricRow label="NET INCOME" metric={metrics.net_income} />
            <MetricRow label="CASH" metric={metrics.cash} />
            <MetricRow label="ASSETS" metric={metrics.assets} />
            <MetricRow label="LIABILITIES" metric={metrics.liabilities} />
          </div>

          {showTrials && (
            <div style={{ paddingBottom: 10, borderBottom: hairline, marginBottom: 10 }}>
              <Row k="CLINICAL TRIALS" v={`${trials.returned_count || 0} RETURNED`} c={accent} />
              <Row k="TRIAL STATUS" v={compactCounts(trials.statuses)} />
              <Row k="TRIAL PHASES" v={compactCounts(trials.phases)} />
              {trials.examples?.[0]?.title && (
                <div style={{ color: labelLight, fontSize: 12, lineHeight: 1.5, marginTop: 8 }}>
                  {trials.examples[0].title}
                </div>
              )}
            </div>
          )}

          {showFda && (
            <div style={{ paddingBottom: 10, borderBottom: hairline, marginBottom: 10 }}>
              <Row k="OPENFDA" v="MATCHED" c={accent} />
              <Row k="ADVERSE EVENT TOP" v={fda.adverse_events_top?.[0] ? `${fda.adverse_events_top[0].term} (${fda.adverse_events_top[0].count})` : "-"} />
              <Row k="RECALL CLASS TOP" v={fda.recalls_by_class?.[0] ? `${fda.recalls_by_class[0].classification} (${fda.recalls_by_class[0].count})` : "-"} />
            </div>
          )}

          {showAlpha && (
            <div>
              <Row k="ALPHA VANTAGE" v="LIVE OVERVIEW" c={accent} />
              <Row k="ANALYST TARGET" v={overview.AnalystTargetPrice ? `$${overview.AnalystTargetPrice}` : "-"} />
              <Row k="P/E / BETA" v={`${overview.PERatio || "-"} / ${overview.Beta || "-"}`} />
              <Row k="REV GROWTH YOY" v={overview.QuarterlyRevenueGrowthYOY || "-"} />
            </div>
          )}
        </>
      )}
    </Card>
  );
}

function RatioCell({ ratio }) {
  const status = ratio?.status || "missing";
  const color = ratioColor(status);
  const value = ratioValue(ratio);
  return (
    <div style={{
      minHeight: 52,
      padding: "8px 9px",
      border: `0.5px solid ${color}`,
      background: "rgba(255,255,255,0.025)",
    }}>
      <div style={{
        display: "flex",
        justifyContent: "space-between",
        gap: 8,
        alignItems: "baseline",
      }}>
        <span style={{ color: dim, fontSize: 10, letterSpacing: "0.1em" }}>
          {(ratio?.label || "RATIO").toUpperCase()}
        </span>
        <span style={{ color, fontSize: 10, fontWeight: 800, letterSpacing: "0.08em" }}>
          {status.toUpperCase()}
        </span>
      </div>
      <div style={{ color, fontSize: 18, fontWeight: 800, marginTop: 4, fontFamily: "Courier New" }}>
        {value}
      </div>
    </div>
  );
}

function fallbackRatio(key) {
  const labels = {
    current_ratio: "Current Ratio",
    quick_ratio: "Quick Ratio",
    working_capital: "Working Capital",
    debt_to_equity: "Debt-to-Equity",
    interest_coverage: "Interest Coverage",
    gross_profit_margin: "Gross Profit Margin",
    operating_margin: "Operating Margin",
    ebitda_margin: "EBITDA Margin",
    net_profit_margin: "Net Profit Margin",
    revenue_growth_rate: "Revenue Growth Rate",
    free_cash_flow_margin: "Free Cash Flow Margin",
    roe: "Return on Equity",
    roa: "Return on Assets",
    inventory_turnover: "Inventory Turnover",
    operating_cash_flow: "Operating Cash Flow",
    cash_conversion: "Cash Conversion",
    debt_to_assets: "Debt-to-Assets",
    pe_ratio: "Price-to-Earnings",
  };
  return { key, label: labels[key] || key, value: null, unit: "x", status: "missing" };
}

function ratioValue(ratio) {
  if (!ratio || ratio.value == null) return "-";
  const n = Number(ratio.value);
  if (!Number.isFinite(n)) return "-";
  if (ratio.unit === "pct") return `${(n * 100).toFixed(1)}%`;
  if (ratio.unit === "USD") return formatMetric(n, "USD");
  return `${n.toFixed(2)}x`;
}

function ratioColor(status) {
  if (status === "good") return "#4ade80";
  if (status === "bad") return "#f87171";
  if (status === "watch") return "#facc15";
  return muted;
}

function SourcePill({ label, quality, ok }) {
  const q = (quality || (ok ? "live" : "down")).toUpperCase();
  const color = q === "LIVE" ? "#4ade80" : q === "OPTIONAL" || q === "NO_MATCH" ? muted : q === "FALLBACK" ? accent : "#f87171";
  return (
    <span style={{
      fontSize: 10,
      padding: "5px 8px",
      border: `0.5px solid ${color}`,
      color,
      background: "rgba(255,255,255,0.03)",
      letterSpacing: "0.1em",
      fontWeight: 800,
    }}>
      {label.replace(/_/g, " ").toUpperCase()} | {q.replace("_", " ")}
    </span>
  );
}

function MetricRow({ label, metric }) {
  if (!metric) return <Row k={label} v="-" />;
  const suffix = metric.period_end ? `FY${metric.fiscal_year || ""} ${metric.fiscal_period || ""}`.trim() : "";
  return <Row k={label} v={formatMetric(metric.value, metric.unit)} sub={suffix} c={accent} />;
}

function formatMetric(value, unit) {
  if (value == null) return "-";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  if (unit === "shares" || Math.abs(n) >= 1_000_000) {
    if (Math.abs(n) >= 1_000_000_000) return `${(n / 1_000_000_000).toFixed(2)}B`;
    if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  }
  if (unit === "USD" && Math.abs(n) < 1_000_000) return `$${n.toLocaleString()}`;
  if (unit === "USD") return `$${formatMetric(n, "shares")}`;
  return n.toLocaleString();
}

function compactCounts(counts) {
  if (!counts || Object.keys(counts).length === 0) return "-";
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([k, v]) => `${k}:${v}`)
    .join("  ");
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
