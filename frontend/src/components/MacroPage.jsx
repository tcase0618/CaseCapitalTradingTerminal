import { useCallback, useEffect, useMemo, useState } from "react";
import axios from "axios";
import { API } from "../config";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

export default function MacroPage() {
  const [active, setActive] = useState("WORLD");
  const [data, setData] = useState(null);
  const [eventsData, setEventsData] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [overviewRes, eventsRes] = await Promise.all([
        axios.get(`${API}/macro/overview`, { timeout: 45000 }),
        axios.get(`${API}/v32/macro`, { params: { days_ahead: 14 }, timeout: 25000 }),
      ]);
      setData(overviewRes.data);
      setEventsData(eventsRes.data);
      setActive(prev => prev === "EVENTS" || overviewRes.data?.regions?.some(r => r.key === prev) ? prev : "WORLD");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const regions = useMemo(() => data?.regions || [], [data]);
  const current = regions.find(r => r.key === active) || regions[0] || null;
  const eventRows = useMemo(() => eventsData?.events || [], [eventsData]);
  const nextEvent = eventsData?.next_event || eventRows.find(e => Number(e.hours_until) >= 0);
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
        <Stat label="EVENTS" value={eventRows.length} sub={(eventsData?.macro_source || "FOREX FACTORY").toUpperCase()} color={eventsData?.source?.fresh ? "#4ade80" : "#fbbf24"} />
        <Stat label="FRESH" value={totals.fresh} sub={`/ ${totals.total} INDICATORS`} color="#4ade80" />
        <Stat label="WATCH" value={totals.watch} sub="2-3Y OLD" color="#fbbf24" />
        <Stat label="STALE" value={totals.stale} sub="NOT USED AS BULLISH" color="#f87171" />
        <Stat label="MISSING" value={totals.missing} sub="NEEDS BETTER SOURCE" color={totals.missing ? "#fb923c" : "#4ade80"} />
      </div>

      <div style={sourcePolicy}>
        {data?.staleness_policy || "Freshness policy loading."}
      </div>

      <div style={tabsWrap}>
        <button onClick={() => setActive("EVENTS")} style={tabStyle(active === "EVENTS", accent2)}>
          EVENTS
        </button>
        {regions.map(region => (
          <button key={region.key} onClick={() => setActive(region.key)} style={tabStyle(active === region.key, region.signal?.color)}>
            {region.label.toUpperCase()}
          </button>
        ))}
      </div>

      {active === "EVENTS" ? (
        <MacroEventsView
          data={eventsData}
          rows={eventRows}
          nextEvent={nextEvent}
          loading={loading}
          refresh={load}
        />
      ) : current ? (
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
          {row.bias.toUpperCase()} / {row.freshness.toUpperCase()}{row.proxy ? " / PROXY" : ""}
        </div>
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "end" }}>
        <div>
          <div style={{ color, fontSize: 26, fontWeight: 900, fontFamily: "Courier New", display: "flex", alignItems: "center", gap: 8 }}>
            {formatValue(row)}
            <TrendArrow row={row} />
          </div>
          <div style={{ color: muted, fontSize: 10, marginTop: 4 }}>
            {row.previous_value != null ? `PRIOR ${formatValue({ ...row, value: row.previous_value })}${row.previous_date ? ` | ${row.previous_date}` : ""}` : "NO PRIOR PRINT"}
          </div>
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
        <thead><tr>{["INDICATOR", "STATUS", "VALUE", "TREND", "DATE", "SOURCE", "WHY IT MATTERS"].map(h => <th key={h} style={th}>{h}</th>)}</tr></thead>
        <tbody>
          {rows.map(row => (
            <tr key={row.key} style={{ borderTop: hairline }}>
              <td style={{ ...td, color: labelLight, fontWeight: 900 }}>{row.label}</td>
              <td style={{ ...td, color: biasColor(row.bias, row.freshness), fontWeight: 900 }}>{row.freshness.toUpperCase()}{row.proxy ? " / PROXY" : ""}</td>
              <td style={td}>{formatValue(row)}</td>
              <td style={{ ...td, color: trendColor(row.trend) }}>{trendLabel(row)}</td>
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

function MacroEventsView({ data, rows, nextEvent, loading, refresh }) {
  const counts = useMemo(() => ({
    high: rows.filter(r => String(r.impact).toLowerCase() === "high").length,
    medium: rows.filter(r => String(r.impact).toLowerCase() === "medium").length,
    low: rows.filter(r => String(r.impact).toLowerCase() === "low").length,
    imminent: rows.filter(r => r.is_imminent).length,
  }), [rows]);
  const grouped = useMemo(() => {
    const map = new Map();
    rows.forEach(row => {
      const key = row.date || "undated";
      if (!map.has(key)) map.set(key, []);
      map.get(key).push(row);
    });
    return Array.from(map.entries()).map(([date, items]) => ({ date, items }));
  }, [rows]);

  return (
    <>
      <div style={heroGrid}>
        <Card title="FOREXFACTORY EVENT CLOCK" accentColor={nextEvent ? impactColor(nextEvent.impact) : accent2}>
          {nextEvent ? (
            <>
              <div style={eventHero(impactColor(nextEvent.impact))}>
                <div>
                  <div style={{ color: dim, fontSize: 10, letterSpacing: "0.18em" }}>NEXT U.S. MACRO PRINT</div>
                  <div style={{ color: impactColor(nextEvent.impact), fontSize: 34, fontWeight: 900, marginTop: 8 }}>
                    {nextEvent.tag} / {timeUntilEvent(nextEvent)}
                  </div>
                  <div style={{ color: labelLight, fontSize: 15, marginTop: 10, lineHeight: 1.55 }}>
                    {nextEvent.name}
                  </div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div style={{ color: dim, fontSize: 10, letterSpacing: "0.18em" }}>IMPACT</div>
                  <div style={{ color: impactColor(nextEvent.impact), fontSize: 28, fontWeight: 900 }}>{(nextEvent.impact || "UNKNOWN").toUpperCase()}</div>
                  <div style={{ color: muted, fontSize: 11, marginTop: 8 }}>{formatEventDate(nextEvent)}</div>
                </div>
              </div>
              <div style={proxyStrip}>
                <MiniBox label="FORECAST" value={nextEvent.forecast || "-"} color={accent} />
                <MiniBox label="PREVIOUS" value={nextEvent.previous || "-"} color={labelLight} />
                <MiniBox label="ACTUAL" value={nextEvent.actual || "PENDING"} color={nextEvent.actual ? "#4ade80" : "#fbbf24"} />
              </div>
            </>
          ) : (
            <div style={{ color: muted, padding: 24 }}>{loading ? "Loading ForexFactory events..." : "No U.S. ForexFactory events in the selected window."}</div>
          )}
        </Card>

        <Card title="EVENT SOURCE QUALITY" accentColor={data?.source?.fresh ? "#4ade80" : "#fbbf24"}>
          <div style={{ display: "grid", gap: 10 }}>
            <MiniBox label="SOURCE" value={(data?.source?.meta?.source || "ForexFactory/FairEconomy XML").toUpperCase()} color={accent2} />
            <MiniBox label="STATUS" value={data?.source?.fresh ? "FRESH" : "WATCH"} color={data?.source?.fresh ? "#4ade80" : "#fbbf24"} />
            <MiniBox label="AGE" value={data?.source?.age_minutes != null ? `${data.source.age_minutes}M` : "-"} color={labelLight} />
            <button onClick={refresh} disabled={loading} style={buttonStyle(accent2)}>{loading ? "SYNCING" : "REFRESH EVENTS"}</button>
          </div>
        </Card>
      </div>

      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 18, flexWrap: "wrap" }}>
        <Stat label="HIGH IMPACT" value={counts.high} sub="RED FOLDER" color="#f87171" accentBar />
        <Stat label="MEDIUM" value={counts.medium} sub="ORANGE FOLDER" color="#fb923c" />
        <Stat label="LOW" value={counts.low} sub="LOWER PRIORITY" color="#fbbf24" />
        <Stat label="IMMINENT" value={counts.imminent} sub="WITHIN 48H" color={counts.imminent ? "#f87171" : "#4ade80"} />
      </div>

      <div style={{ display: "grid", gap: 18 }}>
        {grouped.map(group => (
          <Card key={group.date} title={`EVENT DOCKET / ${group.date}`} accentColor={group.items.some(i => String(i.impact).toLowerCase() === "high") ? "#f87171" : accent}>
            <MacroEventsTable rows={group.items} />
          </Card>
        ))}
      </div>
    </>
  );
}

function MacroEventsTable({ rows }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>No events.</div>;
  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 920 }}>
        <thead>
          <tr>{["TIME", "EVENT", "TAG", "IMPACT", "FORECAST", "PREVIOUS", "ACTUAL", "COUNTDOWN"].map(h => <th key={h} style={th}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={`${row.datetime_et || row.date}-${row.name}-${idx}`} style={{ borderTop: hairline }}>
              <td style={{ ...td, color: labelLight, fontWeight: 900 }}>{row.time_et || "-"}</td>
              <td style={{ ...td, color: labelLight, fontWeight: 900 }}>{row.name || "U.S. macro event"}</td>
              <td style={{ ...td, color: accent2, fontWeight: 900 }}>{row.tag || "USD"}</td>
              <td style={{ ...td, color: impactColor(row.impact), fontWeight: 900 }}>{(row.impact || "-").toUpperCase()}</td>
              <td style={td}>{row.forecast || "-"}</td>
              <td style={td}>{row.previous || "-"}</td>
              <td style={{ ...td, color: row.actual ? "#4ade80" : "#fbbf24", fontWeight: 900 }}>{row.actual || "PENDING"}</td>
              <td style={{ ...td, color: row.is_imminent ? "#f87171" : accent }}>{timeUntilEvent(row) || "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function timeUntilEvent(event) {
  if (!event?.datetime_et) return null;
  const target = new Date(event.datetime_et);
  const diffMs = target.getTime() - Date.now();
  if (!Number.isFinite(diffMs) || diffMs < 0) return "DUE";
  const days = Math.floor(diffMs / 86400000);
  const hours = Math.floor((diffMs % 86400000) / 3600000);
  const mins = Math.floor((diffMs % 3600000) / 60000);
  if (days >= 1) return `${days}D ${hours}H`;
  if (hours >= 1) return `${hours}H ${mins}M`;
  return `${mins}M`;
}

function formatEventDate(event) {
  if (!event?.datetime_et) return event?.date || "-";
  try {
    return new Date(event.datetime_et).toLocaleString("en-US", {
      timeZone: "America/New_York",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return event.date || "-";
  }
}

function impactColor(impact) {
  const key = String(impact || "").toLowerCase();
  if (key === "high") return "#f87171";
  if (key === "medium") return "#fb923c";
  if (key === "low") return "#fbbf24";
  return muted;
}

function TrendArrow({ row }) {
  const color = trendColor(row.trend);
  return (
    <span title={trendLabel(row)} style={{
      color,
      fontSize: 18,
      lineHeight: 1,
      fontWeight: 900,
      textShadow: row.trend === "unknown" ? "none" : `0 0 8px ${color}66`,
    }}>
      {row.trend === "up" ? "↑" : row.trend === "down" ? "↓" : row.trend === "flat" ? "→" : "·"}
    </span>
  );
}

function trendColor(trend) {
  if (trend === "up") return "#4ade80";
  if (trend === "down") return "#f87171";
  if (trend === "flat") return "#fbbf24";
  return muted;
}

function trendLabel(row) {
  if (row.delta == null) return "NO PRIOR";
  const sign = Number(row.delta) > 0 ? "+" : "";
  return `${row.trend?.toUpperCase() || "FLAT"} ${sign}${row.delta}`;
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
const eventHero = color => ({
  display: "flex",
  justifyContent: "space-between",
  gap: 20,
  padding: 16,
  border: `0.5px solid ${color}66`,
  background: `${color}10`,
  alignItems: "flex-start",
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
