import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

export default function MacroPage() {
  const [active, setActive] = useState("WORLD");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await axios.get(`${API}/macro/overview`, { timeout: 45000 });
      setData(res.data);
      setActive(prev => res.data?.regions?.some(r => r.key === prev) ? prev : "WORLD");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const regions = useMemo(() => data?.regions || [], [data]);
  const current = regions.find(r => r.key === active) || regions[0] || null;
  const totals = useMemo(() => {
    const rows = regions.flatMap(r => r.categories || []).flatMap(c => c.indicators || []);
    return {
      fresh: rows.filter(r => r.freshness === "fresh").length,
      watch: rows.filter(r => r.freshness === "watch").length,
      stale: rows.filter(r => r.freshness === "stale").length,
      missing: rows.filter(r => r.bias === "missing").length,
      total: rows.length,
    };
  }, [regions]);

  return (
    <CrtShell
      title="MACRO COMMAND"
      headerRight={<button onClick={load} disabled={loading} style={buttonStyle(accent2)}>{loading ? "SYNCING" : "SYNC MACRO DATA"}</button>}
    >
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 20, flexWrap: "wrap" }}>
        <Stat label="DATA MODEL" value={data?.ok ? "LIVE" : "LOADING"} sub="FRED + WORLD BANK + LSE" color={data?.ok ? "#4ade80" : muted} accentBar />
        <Stat label="FRESH" value={totals.fresh} sub={`/ ${totals.total} INDICATORS`} color="#4ade80" />
        <Stat label="WATCH" value={totals.watch} sub="2-3Y OLD" color="#fbbf24" />
        <Stat label="STALE" value={totals.stale} sub="NOT USED AS BULLISH" color="#f87171" />
        <Stat label="MISSING" value={totals.missing} sub="NEEDS BETTER SOURCE" color={totals.missing ? "#fb923c" : "#4ade80"} />
      </div>

      <div style={sourcePolicy}>
        {data?.staleness_policy || "Freshness policy loading."}
      </div>

      <div style={tabsWrap}>
        {regions.map(region => (
          <button key={region.key} onClick={() => setActive(region.key)} style={tabStyle(active === region.key, region.signal?.color)}>
            {region.label.toUpperCase()}
          </button>
        ))}
      </div>

      {current ? (
        <>
          <div style={heroGrid}>
            <Card title={`${current.label.toUpperCase()} MARKET REGIME`} accentColor={current.signal.color}>
              <div style={signalPanel(current.signal.color)}>
                <div>
                  <div style={{ color: dim, fontSize: 10, letterSpacing: "0.18em" }}>MACRO SIGNAL</div>
                  <div style={{ color: current.signal.color, fontSize: 34, fontWeight: 900, marginTop: 8 }}>
                    {current.signal.icon} {current.signal.label}
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ color: dim, fontSize: 10, letterSpacing: "0.18em" }}>SCORE</div>
                  <div style={{ color: current.signal.color, fontSize: 38, fontWeight: 900 }}>{current.signal.score}</div>
                </div>
              </div>
              <div style={{ color: labelLight, fontSize: 13, lineHeight: 1.7, marginTop: 12 }}>
                {current.signal.reason}
              </div>
              <div style={proxyStrip}>
                <MiniBox label="MARKET PROXY" value={`$${current.proxy}`} color={accent} />
                <MiniBox label="FRESH" value={current.coverage.fresh} color="#4ade80" />
                <MiniBox label="STALE/MISSING" value={`${current.coverage.stale}/${current.coverage.missing}`} color="#f87171" />
              </div>
            </Card>

            <Card title="GLOBAL WATCHBOARD" accentColor={accent2}>
              <div style={{ display: "grid", gap: 9 }}>
                {regions.map(region => (
                  <button key={region.key} onClick={() => setActive(region.key)} style={watchRow(active === region.key, region.signal.color)}>
                    <span style={{ color: region.signal.color, fontWeight: 900, width: 42 }}>{region.signal.label}</span>
                    <span style={{ flex: 1, color: active === region.key ? "#fff" : labelLight }}>{region.label}</span>
                    <span style={{ color: muted }}>{region.coverage.fresh}/{region.coverage.total} fresh</span>
                    <span style={{ color: region.signal.color, fontWeight: 900 }}>{region.signal.score}</span>
                  </button>
                ))}
              </div>
            </Card>
          </div>

          <div style={categoryGrid}>
            {(current.categories || []).map(category => (
              <Card key={category.key} title={category.label.toUpperCase()} accentColor={categoryColor(category)}>
                <div style={{ display: "grid", gap: 10 }}>
                  {(category.indicators || []).map(row => <IndicatorTile key={row.key} row={row} />)}
                </div>
              </Card>
            ))}
          </div>

          <Card title="MISSING / STALE DATA QUEUE" accentColor="#fb923c">
            <MacroTable rows={staleRows(current)} />
          </Card>
        </>
      ) : (
        <Card title="MACRO DATA LOADING">
          <div style={{ color: muted, padding: 24 }}>Fetching macro indicators...</div>
        </Card>
      )}
    </CrtShell>
  );
}

function IndicatorTile({ row }) {
  const color = biasColor(row.bias, row.freshness);
  return (
    <div style={{
      border: `0.5px solid ${color}66`,
      background: "rgba(255,255,255,0.025)",
      padding: 12,
      display: "grid",
      gap: 8,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 10, alignItems: "baseline" }}>
        <div style={{ color: labelLight, fontSize: 12, fontWeight: 900, letterSpacing: "0.08em" }}>{row.label}</div>
        <div style={{ color, fontSize: 10, fontWeight: 900, letterSpacing: "0.12em" }}>
          {row.bias.toUpperCase()} / {row.freshness.toUpperCase()}
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "end" }}>
        <div style={{ color, fontSize: 26, fontWeight: 900, fontFamily: "Courier New" }}>
          {formatValue(row)}
        </div>
        <div style={{ textAlign: "right", color: muted, fontSize: 10, lineHeight: 1.5 }}>
          <div>{row.date || "NO DATE"}</div>
          <div>{row.source || "NO SOURCE"}</div>
        </div>
      </div>
    </div>
  );
}

function MacroTable({ rows }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No stale or missing rows for this region.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 760 }}>
        <thead><tr>{["INDICATOR", "STATUS", "VALUE", "DATE", "SOURCE", "WHY IT MATTERS"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.key} style={{ borderTop: hairline }}>
              <td style={{ ...td, color: labelLight, fontWeight: 900 }}>{row.label}</td>
              <td style={{ ...td, color: biasColor(row.bias, row.freshness), fontWeight: 900 }}>{row.freshness.toUpperCase()}</td>
              <td style={td}>{formatValue(row)}</td>
              <td style={td}>{row.date || "-"}</td>
              <td style={td}>{row.source}</td>
              <td style={{ ...td, color: muted }}>{explain(row.key)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function staleRows(region) {
  return (region.categories || [])
    .flatMap(category => category.indicators || [])
    .filter(row => row.freshness === "stale" || row.bias === "missing");
}

function categoryColor(category) {
  const rows = category.indicators || [];
  if (rows.some(row => row.bias === "bearish" && row.freshness !== "stale")) return "#f87171";
  if (rows.some(row => row.bias === "bullish" && row.freshness === "fresh")) return "#4ade80";
  return accent;
}

function formatValue(row) {
  if (row.value == null) return "-";
  const value = Number(row.value);
  if (!Number.isFinite(value)) return String(row.value);
  if (row.unit === "K") return `${value >= 0 ? "+" : ""}${value.toFixed(0)}K`;
  if (row.unit === "idx") return value.toFixed(1);
  return `${value >= 0 && row.unit.includes("%") ? "+" : ""}${value.toFixed(2)}${row.unit ? ` ${row.unit}` : ""}`;
}

function biasColor(bias, freshness) {
  if (bias === "missing") return muted;
  if (freshness === "stale") return "#fb923c";
  if (bias === "bullish") return "#4ade80";
  if (bias === "bearish") return "#f87171";
  return "#fbbf24";
}

function explain(key) {
  return {
    gdp_growth: "Total output and broad growth pulse.",
    pmi: "Forward-looking business activity health.",
    industrial_production: "Factory, mining, and utility output.",
    retail_sales: "Consumer demand strength.",
    unemployment: "Labor slack and consumer pressure.",
    wage_growth: "Income strength and inflation pressure.",
    job_creation: "Monthly employment momentum.",
    cpi: "Consumer inflation and Fed pressure.",
    ppi: "Wholesale inflation pipeline.",
    policy_rate: "Borrowing cost and liquidity setting.",
    current_account: "External funding and trade balance.",
    debt_to_gdp: "Fiscal sustainability risk.",
    bond_yield: "Market confidence and sovereign borrowing cost.",
  }[key] || "Macro input.";
}

function MiniBox({ label, value, color }) {
  return (
    <div style={{ border: hairline, background: "rgba(255,255,255,0.025)", padding: 10 }}>
      <div style={{ color: dim, fontSize: 9, letterSpacing: "0.14em" }}>{label}</div>
      <div style={{ color, fontSize: 16, fontWeight: 900, marginTop: 5 }}>{value}</div>
    </div>
  );
}

const buttonStyle = color => ({
  background: "transparent",
  border: `0.5px solid ${color}`,
  color,
  fontSize: 11,
  padding: "8px 16px",
  cursor: "pointer",
  letterSpacing: "0.14em",
  fontFamily: "JetBrains Mono",
  fontWeight: 700,
});
const sourcePolicy = { padding: "10px 14px", border: hairline, background: "rgba(255,255,255,0.025)", color: muted, fontSize: 10, letterSpacing: "0.08em", margin: "-8px 0 16px", lineHeight: 1.5 };
const tabsWrap = { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 };
const tabStyle = (active, color = accent) => ({
  background: active ? `${color}14` : "rgba(255,255,255,0.02)",
  border: `0.5px solid ${active ? color : "rgba(255,255,255,0.08)"}`,
  color: active ? color : labelLight,
  padding: "9px 12px",
  fontSize: 10,
  letterSpacing: "0.12em",
  fontFamily: "JetBrains Mono",
  fontWeight: 800,
  cursor: "pointer",
});
const heroGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.15fr) minmax(320px, 0.85fr)", gap: 18, marginBottom: 18 };
const categoryGrid = { display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(390px, 1fr))", gap: 18 };
const signalPanel = color => ({
  display: "flex",
  justifyContent: "space-between",
  gap: 20,
  padding: 16,
  border: `0.5px solid ${color}66`,
  background: `${color}10`,
});
const proxyStrip = { display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10, marginTop: 14 };
const watchRow = (active, color) => ({
  display: "flex",
  alignItems: "center",
  gap: 10,
  width: "100%",
  background: active ? `${color}10` : "rgba(255,255,255,0.018)",
  border: `0.5px solid ${active ? color : "rgba(255,255,255,0.07)"}`,
  padding: "10px 12px",
  fontFamily: "JetBrains Mono",
  fontSize: 11,
  letterSpacing: "0.08em",
  cursor: "pointer",
});
const th = { padding: "10px 8px", fontSize: 10, color: dim, letterSpacing: "0.14em", fontWeight: 400, textAlign: "left" };
const td = { padding: "10px 8px", color: labelLight, letterSpacing: "0.04em", fontSize: 12, verticalAlign: "top" };
