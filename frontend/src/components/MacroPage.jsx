import { useEffect, useMemo, useState } from "react";
import axios from "axios";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

const REGIONS = [
  { key: "WORLD", label: "World", aliases: ["world", "global", "oecd", "imf", "g20"], proxy: "ACWI" },
  { key: "US", label: "United States", aliases: ["united states", "usa", "u.s.", "federal reserve", "america"], proxy: "SPY" },
  { key: "CHINA", label: "China", aliases: ["china", "pboc", "cny"], proxy: "FXI" },
  { key: "GERMANY", label: "Germany", aliases: ["germany", "bundesbank", "eurozone", "eur"], proxy: "EWG" },
  { key: "INDIA", label: "India", aliases: ["india", "rbi", "inr"], proxy: "INDA" },
  { key: "JAPAN", label: "Japan", aliases: ["japan", "boj", "yen", "jpy"], proxy: "EWJ" },
  { key: "SOUTH_KOREA", label: "South Korea", aliases: ["south korea", "korea", "bok", "krw"], proxy: "EWY" },
  { key: "TAIWAN", label: "Taiwan", aliases: ["taiwan", "twd"], proxy: "EWT" },
];

export default function MacroPage() {
  const [active, setActive] = useState("WORLD");
  const [macro, setMacro] = useState(null);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    try {
      const [macroRes, healthRes] = await Promise.all([
        axios.get(`${API}/data/lse/macro?limit=500`).catch(e => ({ data: { error: e.message } })),
        axios.get(`${API}/data/lse/health`).catch(e => ({ data: { ok: false, reason: e.message } })),
      ]);
      setMacro(macroRes.data);
      setHealth(healthRes.data);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const activeRegion = REGIONS.find(r => r.key === active) || REGIONS[0];
  const regionData = useMemo(() => buildRegionData(macro, activeRegion), [macro, activeRegion]);
  const totals = useMemo(() => {
    const allEvents = Array.isArray(macro?.economic_calendar) ? macro.economic_calendar : [];
    const allYields = Array.isArray(macro?.bond_yields) ? macro.bond_yields : [];
    const riskEvents = allEvents.filter(isRiskEvent).length;
    return { events: allEvents.length, yields: allYields.length, riskEvents };
  }, [macro]);

  return (
    <CrtShell
      title="MACRO COMMAND"
      headerRight={<button onClick={load} disabled={loading} style={buttonStyle(accent2)}>{loading ? "SYNCING" : "SYNC LSE MACRO"}</button>}
    >
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 20, flexWrap: "wrap" }}>
        <Stat label="LSE DATA" value={health?.ok ? "LIVE" : "DEGRADED"} sub={health?.reason || "MACRO FEED"} color={health?.ok ? "#4ade80" : "#f87171"} accentBar />
        <Stat label="EVENTS" value={totals.events} sub="ECONOMIC CALENDAR" color={accent} />
        <Stat label="BOND ROWS" value={totals.yields} sub="YIELD CURVE INPUTS" color={accent2} />
        <Stat label="RISK FLAGS" value={totals.riskEvents} sub="INFLATION/RATES/JOBS" color={totals.riskEvents ? "#fbbf24" : "#4ade80"} />
      </div>

      <div style={tabsWrap}>
        {REGIONS.map(region => (
          <button
            key={region.key}
            onClick={() => setActive(region.key)}
            style={tabStyle(active === region.key)}
          >
            {region.label.toUpperCase()}
          </button>
        ))}
      </div>

      <div style={heroGrid}>
        <Card title={`${activeRegion.label.toUpperCase()} REGIME`} accentColor={regionData.signal.color}>
          <div style={signalPanel(regionData.signal.color)}>
            <div>
              <div style={{ color: dim, fontSize: 10, letterSpacing: "0.18em" }}>MARKET ICON</div>
              <div style={{ color: regionData.signal.color, fontSize: 30, fontWeight: 900, marginTop: 8 }}>
                {regionData.signal.icon} {regionData.signal.label}
              </div>
            </div>
            <div style={{ textAlign: "right" }}>
              <div style={{ color: dim, fontSize: 10, letterSpacing: "0.18em" }}>REGIME SCORE</div>
              <div style={{ color: regionData.signal.color, fontSize: 34, fontWeight: 900 }}>
                {regionData.signal.score}
              </div>
            </div>
          </div>
          <div style={{ color: labelLight, fontSize: 13, lineHeight: 1.7, marginTop: 12 }}>
            {regionData.signal.reason}
          </div>
          <div style={proxyStrip}>
            <MiniBox label="MARKET PROXY" value={`$${activeRegion.proxy}`} color={accent} />
            <MiniBox label="CALENDAR HITS" value={regionData.events.length} color={accent2} />
            <MiniBox label="YIELD HITS" value={regionData.yields.length} color="#fbbf24" />
          </div>
        </Card>

        <Card title="WATCHBOARD" accentColor={accent2}>
          <div style={{ display: "grid", gap: 9 }}>
            {REGIONS.map(region => {
              const data = buildRegionData(macro, region);
              return (
                <button key={region.key} onClick={() => setActive(region.key)} style={watchRow(active === region.key, data.signal.color)}>
                  <span style={{ color: data.signal.color, fontWeight: 900 }}>{data.signal.icon}</span>
                  <span style={{ flex: 1, color: active === region.key ? "#fff" : labelLight }}>{region.label}</span>
                  <span style={{ color: muted }}>{data.events.length} events</span>
                  <span style={{ color: data.signal.color, fontWeight: 900 }}>{data.signal.label}</span>
                </button>
              );
            })}
          </div>
        </Card>
      </div>

      <div style={detailGrid}>
        <Card title="ECONOMIC CALENDAR" accentColor={accent}>
          <MacroTable rows={regionData.events} empty="No LSE macro events matched this region yet." />
        </Card>
        <Card title="BOND / RATES CONTEXT" accentColor="#fbbf24">
          <MacroTable rows={regionData.yields} empty="No bond-yield rows matched this region yet." />
        </Card>
      </div>
    </CrtShell>
  );
}

function buildRegionData(macro, region) {
  const events = filterRows(macro?.economic_calendar, region).slice(0, 18);
  const yields = filterRows(macro?.bond_yields, region).slice(0, 18);
  const signal = scoreRegion(events, yields);
  return { events, yields, signal };
}

function filterRows(rows, region) {
  const list = Array.isArray(rows) ? rows : [];
  if (region.key === "WORLD") return list.slice(0, 36);
  return list.filter(row => {
    const text = JSON.stringify(row || {}).toLowerCase();
    return region.aliases.some(alias => text.includes(alias));
  });
}

function scoreRegion(events, yields) {
  let score = 50;
  for (const event of events.slice(0, 12)) {
    const text = JSON.stringify(event || {}).toLowerCase();
    if (/(inflation|cpi|ppi|rate|hawkish|jobless|unemployment|deficit)/.test(text)) score -= 6;
    if (/(gdp|pmi|retail sales|industrial production|consumer confidence|employment)/.test(text)) score += 4;
  }
  if (yields.length > 8) score -= 4;
  score = Math.max(0, Math.min(100, score));
  if (score >= 58) return { label: "BULLISH", icon: "UP", color: "#4ade80", score, reason: "Growth-heavy calendar signals outweigh rate and stress flags in the current LSE sample." };
  if (score <= 42) return { label: "BEARISH", icon: "DOWN", color: "#f87171", score, reason: "Rate, inflation, labor, or yield stress flags dominate the current LSE macro sample." };
  return { label: "NEUTRAL", icon: "FLAT", color: "#fbbf24", score, reason: "The current macro sample is mixed; PM should size trades normally and wait for cleaner confirmation." };
}

function isRiskEvent(row) {
  return /(inflation|cpi|ppi|rate|jobless|unemployment|fed|yield)/i.test(JSON.stringify(row || ""));
}

function MacroTable({ rows, empty }) {
  if (!rows.length) return <div style={{ color: muted, padding: 20 }}>{empty}</div>;
  return (
    <div style={{ overflowX: "auto", maxHeight: 520, overflowY: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 680 }}>
        <thead>
          <tr>{["DATE", "REGION", "EVENT", "VALUE", "SOURCE"].map(h => <th key={h} style={th}>{h}</th>)}</tr>
        </thead>
        <tbody>
          {rows.map((row, i) => {
            const normalized = normalizeRow(row);
            return (
              <tr key={`${normalized.title}-${i}`} style={{ borderTop: hairline }}>
                <td style={td}>{normalized.date}</td>
                <td style={{ ...td, color: accent }}>{normalized.region}</td>
                <td style={{ ...td, color: "#e5e7eb" }}>{normalized.title}</td>
                <td style={td}>{normalized.value}</td>
                <td style={{ ...td, color: accent2 }}>LSE</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function normalizeRow(row) {
  const keys = Object.keys(row || {});
  const find = (...names) => {
    const key = keys.find(k => names.includes(k.toLowerCase()) || names.some(name => k.toLowerCase().includes(name)));
    return key ? row[key] : null;
  };
  return {
    date: find("date", "time", "timestamp", "event_date") || "-",
    region: find("country", "region", "currency", "market") || "-",
    title: find("event", "title", "name", "indicator", "series") || JSON.stringify(row || {}).slice(0, 90),
    value: find("value", "actual", "yield", "rate", "close") || "-",
  };
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

const tabsWrap = { display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 18 };
const tabStyle = active => ({
  background: active ? "rgba(200,168,75,0.12)" : "rgba(255,255,255,0.02)",
  border: `0.5px solid ${active ? accent : "rgba(255,255,255,0.08)"}`,
  color: active ? accent : labelLight,
  padding: "9px 12px",
  fontSize: 10,
  letterSpacing: "0.12em",
  fontFamily: "JetBrains Mono",
  fontWeight: 800,
  cursor: "pointer",
});
const heroGrid = { display: "grid", gridTemplateColumns: "minmax(0, 1.2fr) minmax(320px, 0.8fr)", gap: 18, marginBottom: 18 };
const detailGrid = { display: "grid", gridTemplateColumns: "1fr 1fr", gap: 18 };
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
