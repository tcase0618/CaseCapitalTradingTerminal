import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { API } from "../config";
import { toast } from "sonner";
import { CrtShell, Card, Stat, tokens } from "./CrtShell";
import { displayResource } from "../hooks/useDisplayResource";

const { accent, accent2, dim, muted, labelLight, hairline, cardBg, pageBg } = tokens;

const SOURCE_WEIGHTS = {
  PM: 22,
  TRADE_FLOOR: 20,
  SCANNER: 16,
  CONVICTION: 16,
  SEC: 14,
  CONTRACT: 13,
  DARK_HORSE: 12,
  X_FACTOR: 11,
  DISCOVERY: 8,
};

const LANE_DEFS = [
  { key: "all", label: "ALL INTEL" },
  { key: "act", label: "ACT NOW" },
  { key: "pm", label: "PM ALIGNED" },
  { key: "catalyst", label: "CATALYSTS" },
  { key: "hidden", label: "HIDDEN TAPE" },
  { key: "watch", label: "WATCH" },
];

function cleanTicker(ticker) {
  return String(ticker || "").replace(/^\$/, "").trim().toUpperCase();
}

function asArray(payload, key) {
  if (Array.isArray(payload)) return payload.filter(item => item != null);
  if (payload && Array.isArray(payload[key])) return payload[key].filter(item => item != null);
  return [];
}

const finite = value => value == null || typeof value === "boolean" || String(value).trim() === "" || !Number.isFinite(Number(value)) ? null : Number(value);
const feedCount = (payload, key) => Array.isArray(payload) ? asArray(payload).length : Array.isArray(payload?.[key]) ? asArray(payload, key).length : null;

// Only inspected DB/catalog reads are cached. No polling of plan/provider GETs.
const sharedFeeds = new Set(["conviction", "darkHorse", "xFactor", "discoveries", "scan", "sec", "freeCatalog"]);

function num(value, digits = 0) {
  const n = finite(value);
  if (n == null) return "-";
  return n.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

function money(value) {
  const n = finite(value);
  if (n == null) return "-";
  if (Math.abs(n) >= 1000000000) return `$${(n / 1000000000).toFixed(1)}B`;
  if (Math.abs(n) >= 1000000) return `$${(n / 1000000).toFixed(1)}M`;
  if (Math.abs(n) >= 1000) return `$${(n / 1000).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
}

function shortDate(value) {
  if (!value) return "-";
  const raw = String(value);
  if (/^\d{8}$/.test(raw)) return `${raw.slice(4, 6)}/${raw.slice(6, 8)}/${raw.slice(2, 4)}`;
  return raw.slice(0, 16).replace("T", " ");
}

function percent(value, digits = 1) {
  const n = finite(value);
  if (n == null) return "-";
  return `${n.toFixed(digits)}%`;
}

function createRow(ticker) {
  return {
    ticker,
    company: "",
    score: 0,
    lane: "WATCH",
    price: null,
    sector: "",
    pmAction: null,
    pmScore: null,
    allocation: null,
    rr: null,
    tradeFloor: false,
    sourceSet: new Set(),
    sourceNotes: {},
    signals: [],
    catalysts: [],
    reasons: [],
    cautions: [],
    thesis: "",
    updatedAt: "",
  };
}

function ensure(map, ticker) {
  const t = cleanTicker(ticker);
  if (!t) return null;
  if (!map.has(t)) map.set(t, createRow(t));
  return map.get(t);
}

function addSource(row, source, note) {
  row.sourceSet.add(source);
  if (note) row.sourceNotes[source] = note;
}

function addUnique(list, value) {
  if (!value) return;
  const text = String(value).trim();
  if (text && !list.includes(text)) list.push(text);
}

function scoreRow(row) {
  let score = 0;
  row.sourceSet.forEach(source => { score += SOURCE_WEIGHTS[source] || 5; });
  if (finite(row.pmScore) != null) score += Math.min(18, finite(row.pmScore) / 5);
  if (row.tradeFloor) score += 12;
  if (row.signals.length >= 4) score += 8;
  if (row.catalysts.length >= 2) score += 7;
  if (row.rr != null && Number(row.rr) >= 2.5) score += 6;
  if (row.sourceSet.has("DISCOVERY") && row.sourceSet.size === 1) score -= 6;
  return Math.max(0, Math.min(100, Math.round(score)));
}

function laneFor(row) {
  if (row.tradeFloor || ["ACCUMULATE", "STARTER"].includes(row.pmAction) || row.score >= 76) return "ACT NOW";
  if (row.sourceSet.has("DISCOVERY") && row.sourceSet.size <= 2) return "HIDDEN TAPE";
  if (row.catalysts.length || row.sourceSet.has("SEC") || row.sourceSet.has("CONTRACT")) return "CATALYST";
  return "WATCH";
}

function sourceColor(source) {
  return {
    PM: accent,
    TRADE_FLOOR: "#4ade80",
    SCANNER: accent2,
    CONVICTION: "#a78bfa",
    SEC: "#f87171",
    CONTRACT: "#fb923c",
    DARK_HORSE: "#f59e0b",
    X_FACTOR: "#22d3ee",
    DISCOVERY: "#93c5fd",
  }[source] || labelLight;
}

function sourceLabel(source) {
  return source.replace("_", " ");
}

export default function IntelPage() {
  const [payloads, setPayloads] = useState(null);
  const [loading, setLoading] = useState(true);
  const [lane, setLane] = useState("all");
  const [lanesOpen, setLanesOpen] = useState(false);
  const [laneLimit, setLaneLimit] = useState(24);
  const [selected, setSelected] = useState(null);
  const [watching, setWatching] = useState(null);
  const [lastError, setLastError] = useState("");
  const [failedFeeds, setFailedFeeds] = useState([]);
  const mounted = useRef(false);
  const refreshController = useRef(null);
  const refreshing = useRef(false);

  const refresh = useCallback(async (force = false) => {
    if (refreshing.current && !refreshController.current?.signal.aborted) return;
    refreshing.current = true;
    const controller = new AbortController();
    refreshController.current = controller;
    setLoading(true);
    setLastError("");
    const endpoints = {
      conviction: "/v32/conviction",
      darkHorse: "/v32/dark_horse?days=14",
      xFactor: "/v32/x_factor?days=14",
      discoveries: "/v32/x_factor/discoveries?days=7",
      macro: "/v32/macro",
      scan: "/scan/latest",
      sec: "/sec/filings?days=7",
      contracts: "/contracts?days=90&min_amount=1000000",
      pm: "/portfolio_manager/latest",
      tradeFloor: "/trade_floor/positions",
      georisk: "/georisk/live",
      newsIntel: "/news_intel/latest?limit=80",
      freeCatalog: "/data/free/catalog",
      lseMacro: "/data/lse/macro?limit=100",
      fredVix: "/data/free/fred/latest/VIXCLS",
      fredTenYear: "/data/free/fred/latest/DGS10",
      fredHighYield: "/data/free/fred/latest/BAMLH0A0HYM2",
    };

    const entries = await Promise.allSettled(
      Object.entries(endpoints).map(async ([key, path]) => {
        try {
          if (!sharedFeeds.has(key)) return [key, (await axios.get(`${API}${path}`, { timeout: 15000, signal: controller.signal })).data];
          const resource = displayResource(`${API}${path}`, 60000);
          const cached = resource.getSnapshot();
          if (force || cached.updatedAt == null || Date.now() - cached.updatedAt >= 60000) await resource.refresh(force);
          const state = resource.getSnapshot();
          if (state.error) throw state.error;
          return [key, state.data];
        } catch (error) { throw { key, error }; }
      })
    );

    const next = {};
    const failed = [];
    entries.forEach(result => {
      if (result.status === "fulfilled") {
        next[result.value[0]] = result.value[1];
      } else {
        failed.push(result.reason?.key || "endpoint");
      }
    });

    if (refreshController.current === controller) refreshing.current = false;
    if (!mounted.current || controller.signal.aborted) return;
    if (failed.length) setLastError(`${failed.length} intel feeds degraded; retained values may be stale`);
    setFailedFeeds(failed);
    setPayloads(previous => ({ ...previous, ...next }));
    setLoading(false);
  }, []);

  useEffect(() => {
    mounted.current = true;
    const initialRead = () => {
      if (document.visibilityState === "hidden") return;
      document.removeEventListener("visibilitychange", initialRead);
      refresh();
    };
    initialRead();
    if (document.visibilityState === "hidden") document.addEventListener("visibilitychange", initialRead);
    return () => { mounted.current = false; refreshController.current?.abort(); document.removeEventListener("visibilitychange", initialRead); };
  }, [refresh]);

  const intel = useMemo(() => {
    const data = payloads || {};
    const map = new Map();
    const scanRows = asArray(data.scan, "results");

    scanRows.forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "SCANNER", `score ${item.signal_score ?? "-"}`);
      row.price = item.price ?? row.price;
      row.sector = item.sector || row.sector;
      row.thesis = item.thesis || row.thesis;
      row.updatedAt = data.scan?.finished_at || data.scan?.created_at || row.updatedAt;
      asArray(item.signals).forEach(signal => addUnique(row.signals, signal));
      if (item.catalyst_date) addUnique(row.catalysts, `Catalyst ${item.catalyst_date}`);
      if (item.targets?.upside_blended != null) addUnique(row.reasons, `${percent(item.targets.upside_blended)} blended upside`);
      if (item.options?.strategy_name) addUnique(row.reasons, item.options.strategy_name);
      if (item.gov_summary?.total_30d) addUnique(row.catalysts, `Gov awards ${money(item.gov_summary.total_30d)} in 30D`);
    });

    asArray(data.conviction?.top3, "top3").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "CONVICTION", `conviction ${item.conviction_score ?? "-"}`);
      row.price = item.price ?? row.price;
      row.thesis = item.thesis || row.thesis;
      asArray(item.components).forEach(component => addUnique(row.signals, component));
      if (item.narrative_lock) addUnique(row.catalysts, "Narrative lock");
    });

    asArray(data.conviction?.narrative_locks_14d, "narrative_locks_14d").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "CONVICTION", "narrative lock");
      addUnique(row.catalysts, "Narrative lock");
      row.thesis = item.thesis || row.thesis;
    });

    asArray(data.darkHorse, "alerts").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "DARK_HORSE", `${percent(item.off_exchange_pct)} off-exchange`);
      row.price = item.close ?? row.price;
      addUnique(row.catalysts, `${percent(item.block_pct_of_adv)} ADV block tape`);
      addUnique(row.reasons, `${percent(item.premium_pct)} price premium`);
      row.updatedAt = item.fired_at || item.created_at || row.updatedAt;
    });

    asArray(data.xFactor, "alerts").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      const trigger = item.primary_trigger || {};
      addSource(row, "X_FACTOR", `${trigger.platform || "social"} ${trigger.type || "surge"}`);
      addUnique(row.catalysts, `${trigger.platform || "Retail"} ${trigger.type || "trigger"}`);
      if (item.stocktwits?.bullish_pct != null) addUnique(row.reasons, `${percent(item.stocktwits.bullish_pct * 100, 0)} StockTwits bullish`);
      row.updatedAt = item.fired_at || item.created_at || row.updatedAt;
    });

    asArray(data.discoveries, "discoveries").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "DISCOVERY", asArray(item.sources).join(", ") || "outside universe");
      addUnique(row.catalysts, `Discovered via ${asArray(item.sources).join(", ") || "feed"}`);
      row.updatedAt = item.discovered_at || item.created_at || row.updatedAt;
    });

    asArray(data.sec, "filings").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "SEC", `${item.form || "filing"} ${item.significance ?? "-"} sig`);
      row.company = item.company || row.company;
      addUnique(row.catalysts, `${item.form || "SEC"} ${shortDate(item.filing_date || item.accepted_at || item.updated)}`);
      addUnique(row.reasons, item.summary);
      if (item.bias && item.bias !== "NEUTRAL") addUnique(row.signals, `SEC_${item.bias}`);
      row.updatedAt = item.accepted_at || item.updated || row.updatedAt;
    });

    asArray(data.contracts, "contracts").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "CONTRACT", money(item.amount));
      row.company = item.recipient || row.company;
      addUnique(row.catalysts, `${item.agency || "Agency"} ${money(item.amount)}`);
      if (item.description) addUnique(row.reasons, item.description);
    });

    asArray(data.pm, "recommendations").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "PM", item.action || "reviewed");
      row.pmAction = item.action || row.pmAction;
      row.pmScore = item.pm_score ?? row.pmScore;
      row.allocation = item.allocation_usd ?? row.allocation;
      row.rr = item.risk_reward ?? row.rr;
      row.price = item.price ?? row.price;
      row.sector = item.sector || row.sector;
      asArray(item.signals).forEach(signal => addUnique(row.signals, signal));
      asArray(item.reasons).forEach(reason => addUnique(row.reasons, reason));
      asArray(item.cautions).forEach(caution => addUnique(row.cautions, caution));
    });

    asArray(data.tradeFloor, "db_positions").forEach(item => {
      const row = ensure(map, item.ticker);
      if (!row) return;
      addSource(row, "TRADE_FLOOR", item.pm_action || "position");
      row.tradeFloor = true;
      row.pmAction = row.pmAction || item.pm_action;
      row.pmScore = row.pmScore ?? item.pm_score;
      row.rr = row.rr ?? item.risk_reward;
      addUnique(row.catalysts, "Live trade-floor position");
    });

    const rows = Array.from(map.values()).map(row => {
      const sourceSet = row.sourceSet;
      const sources = Array.from(sourceSet).sort((a, b) => (SOURCE_WEIGHTS[b] || 0) - (SOURCE_WEIGHTS[a] || 0));
      const scored = { ...row, sources, sourceSet };
      scored.score = scoreRow(scored);
      scored.lane = laneFor(scored);
      return scored;
    });

    rows.sort((a, b) => b.score - a.score || b.sources.length - a.sources.length || a.ticker.localeCompare(b.ticker));
    return rows;
  }, [payloads]);

  const visibleIntel = useMemo(() => {
    if (lane === "all") return intel;
    if (lane === "act") return intel.filter(row => row.lane === "ACT NOW");
    if (lane === "pm") return intel.filter(row => row.sourceSet.has("PM"));
    if (lane === "catalyst") return intel.filter(row => row.sourceSet.has("SEC") || row.sourceSet.has("CONTRACT") || row.catalysts.length);
    if (lane === "hidden") return intel.filter(row => row.sourceSet.has("DISCOVERY") || row.sourceSet.has("DARK_HORSE") || row.sourceSet.has("X_FACTOR"));
    if (lane === "watch") return intel.filter(row => row.lane === "WATCH" || row.lane === "CATALYST");
    return intel;
  }, [intel, lane]);

  const top = intel[0] || null;
  const fusionLoaded = [[payloads?.scan, "results"], [payloads?.pm, "recommendations"], [payloads?.tradeFloor, "db_positions"], [payloads?.conviction, "top3"], [payloads?.darkHorse, "alerts"], [payloads?.xFactor, "alerts"], [payloads?.discoveries, "discoveries"], [payloads?.sec, "filings"], [payloads?.contracts, "contracts"]].some(([payload, key]) => feedCount(payload, key) != null);
  const macroEvents = asArray(payloads?.macro, "events");
  const imminent = asArray(payloads?.macro, "imminent_warnings");
  const pmSummary = payloads?.pm?.summary || {};
  const scanFinished = payloads?.scan?.finished_at || payloads?.scan?.created_at;
  const activePositions = asArray(payloads?.tradeFloor, "db_positions");
  const xFactorTape = asArray(payloads?.xFactor, "alerts");
  const xDiscoveries = asArray(payloads?.discoveries, "discoveries");
  const { darkPoolTape, darkPoolSummary } = useMemo(() => {
    const tape = asArray(payloads?.darkHorse, "alerts")
    .map(item => ({
      ...item,
      ticker: cleanTicker(item.ticker),
      notional_proxy: finite(item.block_volume) != null && finite(item.close ?? item.price) != null ? finite(item.block_volume) * finite(item.close ?? item.price) : null,
    }))
    .filter(item => item.ticker)
    .sort((a, b) =>
      Number(b.block_pct_of_adv || 0) - Number(a.block_pct_of_adv || 0)
      || Number(b.off_exchange_pct || 0) - Number(a.off_exchange_pct || 0)
      || Number(b.notional_proxy || 0) - Number(a.notional_proxy || 0)
    );
    const total = key => feedCount(payloads?.darkHorse, "alerts") == null || tape.some(item => finite(item[key]) == null) ? null : tape.reduce((sum, item) => sum + finite(item[key]), 0);
    return { darkPoolTape: tape, darkPoolSummary: {
      prints: feedCount(payloads?.darkHorse, "alerts") == null ? null : tape.length,
      totalVolume: total("block_volume"),
      totalNotional: total("notional_proxy"),
      maxAdv: !tape.length || tape.some(item => finite(item.block_pct_of_adv) == null) ? null : Math.max(...tape.map(item => finite(item.block_pct_of_adv))),
      avgOffExchange: tape.length && total("off_exchange_pct") != null ? total("off_exchange_pct") / tape.length : null,
    } };
  }, [payloads?.darkHorse]);
  const newsIntel = payloads?.newsIntel || {};
  const newsTape = useMemo(() => asArray(payloads?.newsIntel, "articles").sort((a, b) => (finite(b.score) ?? -Infinity) - (finite(a.score) ?? -Infinity)), [payloads?.newsIntel]);
  const georiskEvents = asArray(payloads?.georisk, "events");
  const freeSources = asArray(payloads?.freeCatalog, "sources");
  const macroSeries = [
    { key: "VIX", label: "VOL", source: payloads?.fredVix, risk: finite(payloads?.fredVix?.value) == null ? null : Number(payloads.fredVix.value) >= 20 },
    { key: "10Y", label: "RATES", source: payloads?.fredTenYear, risk: finite(payloads?.fredTenYear?.value) == null ? null : Number(payloads.fredTenYear.value) >= 4.75 },
    { key: "HY", label: "CREDIT", source: payloads?.fredHighYield, risk: finite(payloads?.fredHighYield?.value) == null ? null : Number(payloads.fredHighYield.value) >= 3.5 },
  ];
  const sourceCoverage = [
    { key: "scan", label: "Scanner", count: feedCount(payloads?.scan, "results") },
    { key: "pm", label: "PM", count: feedCount(payloads?.pm, "recommendations") },
    { key: "tradeFloor", label: "Trade Floor", count: feedCount(payloads?.tradeFloor, "db_positions") },
    { key: "sec", label: "SEC", count: feedCount(payloads?.sec, "filings") },
    { key: "contracts", label: "Contracts", count: feedCount(payloads?.contracts, "contracts") },
    { key: "xFactor", label: "X Factor", count: feedCount(payloads?.xFactor, "alerts") },
    { key: "georisk", label: "GeoRisk", count: feedCount(payloads?.georisk, "events") },
    { key: "newsIntel", label: "Newswire", count: feedCount(payloads?.newsIntel, "articles") },
    { key: "lseMacro", label: "LSE", count: Array.isArray(payloads?.lseMacro?.economic_calendar) && Array.isArray(payloads?.lseMacro?.bond_yields) ? asArray(payloads.lseMacro.economic_calendar).length + asArray(payloads.lseMacro.bond_yields).length : null },
    { key: "fred", label: "FRED", count: macroSeries.some(item => item.source) ? macroSeries.filter(item => item.source?.ok && finite(item.source.value) != null).length : null },
  ].map(source => ({ ...source, ok: source.count != null && !failedFeeds.includes(source.key), stale: failedFeeds.includes(source.key) }));
  const criticalGeo = georiskEvents.filter(event => ["CRITICAL", "HIGH"].includes(event.severity)).slice(0, 6);
  const freeConfigured = freeSources.filter(source => source.configured).length;
  const freeApiNeeded = freeSources.filter(source => !source.configured && String(source.cost || "").toLowerCase().includes("key"));
  const catalystTape = useMemo(() => {
    const tape = [];
    intel.forEach(row => {
      row.catalysts.slice(0, 3).forEach(catalyst => {
        tape.push({ ticker: row.ticker, text: catalyst, score: row.score, sources: row.sources });
      });
    });
    return tape.sort((a, b) => b.score - a.score).slice(0, 18);
  }, [intel]);

  const addToWatchlist = async (ticker) => {
    try {
      setWatching(ticker);
      await axios.post(`${API}/watchlist`, { ticker });
      toast.success(`Added ${ticker} to watchlist`);
    } catch {
      toast.error(`Could not add ${ticker}`);
    } finally {
      setWatching(null);
    }
  };

  return (
    <CrtShell
      title="INTEL FEED"
      headerRight={
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center", minWidth: 0 }}>
          {lastError && <span style={pill("#fb923c")}>{lastError}</span>}
          <button onClick={() => refresh(true)} disabled={loading} aria-busy={loading} style={buttonStyle(accent)}>
            {loading ? "SYNCING" : "REFRESH"}
          </button>
        </div>
      }
    >
      <div style={{ minWidth: 0, maxWidth: "100%", overflowWrap: "anywhere" }}>
      <div style={{ display: "flex", background: cardBg, border: hairline, marginBottom: 22, flexWrap: "wrap" }}>
        <Stat label="STACKED NAMES" value={fusionLoaded ? intel.length : "-"} sub="UNIFIED FEED" color={accent} accentBar />
        <Stat label="ACT NOW" value={fusionLoaded ? intel.filter(row => row.lane === "ACT NOW").length : "-"} sub="PM OR LIVE" color="#4ade80" />
        <Stat label="PM ALIGNED" value={feedCount(payloads?.pm, "recommendations") == null ? "-" : intel.filter(row => row.sourceSet.has("PM")).length} sub={pmSummary.mode || "UNKNOWN"} color={accent2} />
        <Stat label="TRADE FLOOR" value={feedCount(payloads?.tradeFloor, "db_positions") ?? "-"} sub="ACTIVE POSITIONS" color="#a78bfa" />
        <Stat label="CATALYSTS" value={fusionLoaded ? catalystTape.length : "-"} sub="SEC / CONTRACT / TAPE" color="#fb923c" />
        <Stat label="DARK POOL" value={darkPoolSummary.prints ?? "-"} sub="FINRA PROXY PRINTS" color={darkPoolSummary.prints ? "#f59e0b" : muted} />
        <Stat label="MACRO WARNINGS" value={feedCount(payloads?.macro, "imminent_warnings") ?? "-"} sub="<48H" color={imminent.length ? "#f87171" : muted} />
      </div>

      <section style={commandDeck}>
        <div style={spotlightPanel}>
          <div style={eyebrow}>CASE CAPITAL LIVE INTEL</div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <Link to={top ? `/ticker/${top.ticker}` : "/scanner"} style={tickerLink(42)}>
              {top ? `$${top.ticker}` : "NO STACK"}
            </Link>
            {top && <span style={scoreBadge(top.score)}>{top.score}/100 FUSION</span>}
            {top && <span style={pill(top.tradeFloor ? "#4ade80" : accent2)}>{top.lane}</span>}
          </div>
          <p style={spotlightText}>
            {top?.thesis || "Run a fresh scan to generate a top stacked read. The feed now fuses scanner, PM, SEC, contracts, X Factor, FRED, georisk, and live position context."}
          </p>
          {top && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}>
              {top.sources.map(source => <SourceChip key={source} source={source} note={top.sourceNotes[source]} />)}
            </div>
          )}
          <div style={deckActions}>
            <Link to="/scanner" style={{ ...buttonStyle(accent2), textDecoration: "none" }}>SCANNER</Link>
            <Link to="/georisk" style={{ ...buttonStyle("#fb923c"), textDecoration: "none" }}>GEORISK MAP</Link>
            <Link to="/portfolio-manager" style={{ ...buttonStyle(accent), textDecoration: "none" }}>PM DESK</Link>
          </div>
        </div>

        <div style={radarPanel}>
          <div style={eyebrow}>MARKET WEATHER</div>
          <div style={macroGrid}>
            {macroSeries.map(item => (
              <div key={item.key} style={weatherTile(item.source?.ok ? item.risk : null)}>
                <div style={{ color: dim, fontSize: 9, letterSpacing: "0.16em" }}>{item.label}</div>
                <div style={{ color: !item.source?.ok || item.risk == null ? muted : item.risk ? "#fb923c" : "#4ade80", fontSize: 22, fontWeight: 900, marginTop: 6 }}>
                  {item.source?.ok ? num(item.source.value, item.key === "VIX" ? 2 : 2) : "-"}
                </div>
                <div style={{ color: muted, fontSize: 9, marginTop: 4 }}>{item.source?.date || "no print"}</div>
              </div>
            ))}
          </div>
          <BriefLine label="PM MODE" value={pmSummary.mode || payloads?.pm?.mode || "-"} color={accent2} />
          <BriefLine label="EQUITY BASIS" value={money(pmSummary.equity_basis)} color={accent} />
          <BriefLine label="LATEST SCAN" value={shortDate(scanFinished)} />
          <BriefLine label="NEXT MACRO" value={macroEvents[0]?.tag || (feedCount(payloads?.macro, "events") == null ? "unavailable" : "none")} color={macroEvents[0]?.is_imminent ? "#fb923c" : labelLight} />
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 320px), 1fr))", gap: 18 }}>
        <Card title="SOURCE FUSION MATRIX" accentColor="#4ade80">
          <div style={matrixGrid}>
            {sourceCoverage.map(source => (
              <div key={source.label} style={matrixCell(source.ok)}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10 }}>
                  <span style={{ color: source.ok ? labelLight : muted, fontSize: 10, letterSpacing: "0.12em" }}>{source.label}</span>
                  <span style={{ color: source.ok ? "#4ade80" : muted, fontSize: 10 }}>{source.stale ? "STALE" : source.ok ? "LOADED" : "UNKNOWN"}</span>
                </div>
                <div style={{ color: source.ok ? accent : muted, fontSize: 24, fontWeight: 900, marginTop: 8 }}>{source.count ?? "-"}</div>
                <PulseBar value={Math.min(100, source.count * 8)} color={source.ok ? "#4ade80" : "#f87171"} />
              </div>
            ))}
          </div>
        </Card>

        <Card title="FREE DATA UPLINK" accentColor="#93c5fd">
          <BriefLine label="CONFIGURED" value={feedCount(payloads?.freeCatalog, "sources") == null ? "-/-" : `${freeConfigured}/${freeSources.length}`} color="#4ade80" />
          <BriefLine label="API KEYS NEEDED" value={feedCount(payloads?.freeCatalog, "sources") == null ? "-" : freeApiNeeded.length} color={freeApiNeeded.length ? "#fb923c" : muted} />
          <BriefLine label="OFFICIAL SOURCES" value={feedCount(payloads?.freeCatalog, "sources") == null ? "-" : freeSources.filter(source => source.official).length} color={accent} />
          <div style={{ display: "grid", gap: 7, marginTop: 12 }}>
            {freeSources.slice(0, 6).map(source => (
              <div key={source.key} style={{ display: "flex", justifyContent: "space-between", gap: 10, borderTop: hairline, paddingTop: 7 }}>
                <span style={{ color: labelLight, fontSize: 10 }}>{source.name}</span>
                <span style={{ color: source.configured ? "#4ade80" : "#fb923c", fontSize: 10 }}>{source.configured ? "READY" : "KEY"}</span>
              </div>
            ))}
          </div>
        </Card>
      </div>

      <section style={laneShell}>
        <div style={laneBar}>
          <div style={{ minWidth: 180 }}>
            <div style={eyebrow}>ACTION LANES</div>
            <div style={{ color: accent, fontSize: 15, letterSpacing: "0.12em", fontWeight: 800 }}>
              {LANE_DEFS.find(item => item.key === lane)?.label || "ALL INTEL"}
            </div>
          </div>
          <select
            value={lane}
            onChange={(event) => { setLane(event.target.value); setLaneLimit(24); }}
            style={laneSelect}
            aria-label="Select intel action lane"
          >
            {LANE_DEFS.map(item => (
              <option key={item.key} value={item.key}>{item.label}</option>
            ))}
          </select>
          <div style={lanePreview}>
            {visibleIntel.slice(0, 5).map(row => (
              <Link key={row.ticker} to={`/ticker/${row.ticker}`} style={previewChip(row.score)}>
                ${row.ticker}
              </Link>
            ))}
            {!visibleIntel.length && <span style={{ color: muted, fontSize: 10 }}>NO MATCHES</span>}
          </div>
          <span style={{ color: muted, fontSize: 10, letterSpacing: "0.12em", whiteSpace: "nowrap" }}>
            {visibleIntel.length} VISIBLE
          </span>
          <button aria-expanded={lanesOpen} aria-controls="intel-action-lanes" onClick={() => setLanesOpen(value => !value)} style={buttonStyle(lanesOpen ? "#f87171" : accent2)}>
            {lanesOpen ? "HIDE" : "EXPAND"}
          </button>
        </div>

        {lanesOpen && (
          <div id="intel-action-lanes" style={laneBody}>
            {loading && !payloads ? (
              <EmptyState text="Syncing all intel sources..." />
            ) : visibleIntel.length === 0 ? (
              <EmptyState text="No names match this lane yet." />
            ) : (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 330px), 1fr))", gap: 12 }}>
                {visibleIntel.slice(0, laneLimit).map(row => (
                  <IntelCard
                    key={row.ticker}
                    row={row}
                    selected={selected === row.ticker}
                    onSelect={() => setSelected(selected === row.ticker ? null : row.ticker)}
                    onWatch={() => addToWatchlist(row.ticker)}
                    watching={watching === row.ticker}
                  />
                ))}
              </div>
            )}
            {visibleIntel.length > laneLimit && <button type="button" onClick={() => setLaneLimit(limit => limit + 24)} style={buttonStyle(accent2)}>SHOW MORE ({visibleIntel.length - laneLimit})</button>}
          </div>
        )}
      </section>

      <section style={intelMosaic}>
        <div style={mosaicStack}>
      <Card
        title="X FACTOR RADAR - GOOGLE TRENDS / STOCKTWITS / RETAIL TAPE"
        accentColor="#22d3ee"
        action={<span style={{ color: muted, fontSize: 10, letterSpacing: "0.12em" }}>{xFactorTape.length} ALERTS / {xDiscoveries.length} DISCOVERIES</span>}
      >
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 250px), 1fr))", gap: 14 }}>
          <div style={{ display: "grid", gap: 8 }}>
            {xFactorTape.length === 0 ? (
              <EmptyState text="No X Factor alerts in the current 14 day window." />
            ) : (
              xFactorTape.slice(0, 10).map(alert => <XFactorRow key={`${alert.ticker}-${alert.fired_at}`} alert={alert} />)
            )}
          </div>
          <div style={discoveryPanel}>
            <div style={eyebrow}>OUTSIDE UNIVERSE DISCOVERIES</div>
            <div style={{ display: "flex", gap: 7, flexWrap: "wrap" }}>
              {xDiscoveries.slice(0, 24).map(item => (
                <Link
                  key={`${item.ticker}-${item.discovered_at || item.created_at}`}
                  to={`/ticker/${cleanTicker(item.ticker)}`}
                  style={previewChip(45)}
                  title={asArray(item.sources).join(", ")}
                >
                  ${cleanTicker(item.ticker)}
                </Link>
              ))}
              {!xDiscoveries.length && <span style={{ color: muted, fontSize: 11 }}>No new discovery tickers.</span>}
            </div>
          </div>
        </div>
      </Card>

      <Card
        title="INSTITUTIONAL NEWSWIRE - FREE RSS EVENT TAPE"
        accentColor="#93c5fd"
        action={<span style={{ color: muted, fontSize: 10, letterSpacing: "0.12em" }}>{num(newsIntel?.live_source_count)}/{num(newsIntel?.source_count)} SOURCES / {newsIntel?.cache || "UNKNOWN"}</span>}
      >
        <div style={newsWireGrid}>
          <div style={newsWireCommand}>
            <div style={eyebrow}>EVENT RADAR</div>
            <div style={{ color: newsTape.length ? "#93c5fd" : muted, fontSize: 34, letterSpacing: "0.08em", fontWeight: 900 }}>
              {newsTape.length ? `${newsTape.length} HEADLINES` : "NO TAPE"}
            </div>
            <p style={{ color: labelLight, lineHeight: 1.55, margin: "10px 0 0", fontSize: 12 }}>
              Free RSS newswire fused against the current scan, PM, and trade-floor universe. It flags urgent language, maps tickers, and labels bullish/bearish tone without using Claude.
            </p>
            <div style={newsWireMetrics}>
              <MiniMetric label="URGENT" value={num(newsIntel?.summary?.urgent)} color={newsIntel?.summary?.urgent ? "#f87171" : muted} />
              <MiniMetric label="MAPPED" value={num(newsIntel?.summary?.ticker_mapped)} color={accent2} />
              <MiniMetric label="BULL" value={num(newsIntel?.summary?.bullish)} color="#4ade80" />
              <MiniMetric label="BEAR" value={num(newsIntel?.summary?.bearish)} color="#f87171" />
            </div>
            {!!newsIntel?.failed_source_count && (
              <div style={{ ...pill("#fb923c"), marginTop: 12 }}>
                {newsIntel.failed_source_count} DEGRADED SOURCE{newsIntel.failed_source_count === 1 ? "" : "S"}
              </div>
            )}
          </div>
          <div style={newsWireList}>
            {newsTape.length === 0 ? (
              <EmptyState text="No free RSS headlines are mapped to the current universe yet." />
            ) : (
              newsTape.slice(0, 10).map(item => <NewsWireRow key={item.id || `${item.title}-${item.url}`} item={item} />)
            )}
          </div>
        </div>
      </Card>
        </div>

        <div style={mosaicStack}>
      <Card
        title="DARK POOL TAPE - FINRA OFF-EXCHANGE PROXY"
        accentColor="#f59e0b"
        action={<span style={{ color: muted, fontSize: 10, letterSpacing: "0.12em" }}>{darkPoolSummary.prints ?? "-"} QUALIFIED PRINTS / 14D</span>}
      >
        <div style={darkPoolGrid}>
          <div style={darkPoolCommand}>
            <div style={eyebrow}>INSTITUTIONAL BLOCK WATCH</div>
            <div style={{ color: darkPoolSummary.prints ? "#f59e0b" : muted, fontSize: 34, letterSpacing: "0.08em", fontWeight: 900 }}>
              {darkPoolSummary.prints ? `${darkPoolSummary.prints} PRINTS` : "NO PRINTS"}
            </div>
            <p style={{ color: labelLight, lineHeight: 1.55, margin: "10px 0 0", fontSize: 12 }}>
              Uses the terminal Dark Horse feed as a dark-pool proxy: FINRA off-exchange volume, block size versus ADV, and price-premium confirmation. This is institutional tape context, not an execution signal by itself.
            </p>
            <div style={darkPoolMetrics}>
              <MiniMetric label="SHARES" value={num(darkPoolSummary.totalVolume)} color={accent} />
              <MiniMetric label="NOTIONAL" value={money(darkPoolSummary.totalNotional)} color="#f59e0b" />
              <MiniMetric label="MAX ADV" value={percent(darkPoolSummary.maxAdv)} color={darkPoolSummary.maxAdv >= 10 ? "#4ade80" : labelLight} />
              <MiniMetric label="AVG OFF-EX" value={percent(darkPoolSummary.avgOffExchange)} color={darkPoolSummary.avgOffExchange >= 45 ? "#4ade80" : labelLight} />
            </div>
          </div>
          <div style={darkPoolTableWrap}>
            {darkPoolTape.length === 0 ? (
              <EmptyState text="No qualified off-exchange block tape in the current window." />
            ) : (
              <table style={table}>
                <thead>
                  <tr>
                    <Th>TICKER</Th><Th>OFF-EX</Th><Th>ADV BLOCK</Th><Th>SHARES</Th><Th>PREMIUM</Th><Th>STAMP</Th>
                  </tr>
                </thead>
                <tbody>
                  {darkPoolTape.slice(0, 12).map(item => (
                    <tr key={`${item.ticker}-${item.fired_at || item.created_at || item.block_volume}`} style={{ borderTop: hairline }}>
                      <Td strong><Link to={`/ticker/${item.ticker}`} style={tickerLink(14)}>${item.ticker}</Link></Td>
                      <Td color={Number(item.off_exchange_pct || 0) >= 45 ? "#4ade80" : labelLight}>{percent(item.off_exchange_pct)}</Td>
                      <Td color={Number(item.block_pct_of_adv || 0) >= 10 ? "#f59e0b" : labelLight}>{percent(item.block_pct_of_adv)}</Td>
                      <Td>{num(item.block_volume)}</Td>
                      <Td color={Number(item.premium_pct || 0) > 0 ? "#4ade80" : "#f87171"}>{percent(item.premium_pct)}</Td>
                      <Td muted>{shortDate(item.fired_at || item.created_at || item.trade_date)}</Td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </Card>

      <Card
        title="GEORISK SPILLOVER WATCH"
        accentColor="#fb923c"
        action={<span style={{ color: muted, fontSize: 10, letterSpacing: "0.12em" }}>{payloads?.georisk?.cache_status || "UNKNOWN"} / {feedCount(payloads?.georisk, "events") ?? "-"} EVENTS</span>}
      >
        <div style={geoGrid}>
          <div style={geoHero}>
            <div style={eyebrow}>HOT ZONES TOUCHING THE MARKET</div>
            <div style={{ color: "#fb923c", fontSize: 30, fontWeight: 900, letterSpacing: "0.08em" }}>
              {criticalGeo.length || 0} HIGH-RISK PINS
            </div>
            <p style={{ color: labelLight, lineHeight: 1.55, margin: "10px 0 0" }}>
              Pulls the live GeoRisk map feed into Intel so defense, energy, shipping, semiconductor, and risk-off stories can collide with ticker-level setups.
            </p>
            <Link to="/georisk" style={{ ...buttonStyle("#fb923c"), textDecoration: "none", display: "inline-block", marginTop: 14 }}>
              OPEN MAP
            </Link>
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {criticalGeo.length === 0 ? <EmptyState text="No high-severity georisk pins are loaded." /> : criticalGeo.map(event => (
              <div key={event.id || `${event.title}-${event.location}`} style={geoRow(event.severity)}>
                <div style={{ display: "flex", justifyContent: "space-between", gap: 10, marginBottom: 6 }}>
                  <span style={{ color: event.severity === "CRITICAL" ? "#f87171" : "#fb923c", fontSize: 10, letterSpacing: "0.12em", fontWeight: 900 }}>
                    {event.severity} / {event.score || "-"}
                  </span>
                  <span style={{ color: muted, fontSize: 10 }}>{event.location || event.domain || "global"}</span>
                </div>
                <div style={{ color: labelLight, lineHeight: 1.4 }}>{event.title}</div>
                <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
                  {asArray(event.sectors).slice(0, 4).map(sector => <span key={sector} style={pill("#fb923c")}>{String(sector)}</span>)}
                  {asArray(event.tickers).slice(0, 5).map(ticker => (
                    <Link key={ticker} to={`/ticker/${cleanTicker(ticker)}`} style={previewChip(60)}>${cleanTicker(ticker)}</Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </Card>
        </div>
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 18 }}>
        <Card title="CATALYST TAPE" accentColor="#fb923c">
          <div style={{ display: "grid", gap: 8 }}>
            {catalystTape.length === 0 ? <EmptyState text="No catalyst tape is loaded." /> : catalystTape.map((item, idx) => (
              <div key={`${item.ticker}-${idx}`} style={tapeRow}>
                <Link to={`/ticker/${item.ticker}`} style={tickerLink(15)}>${item.ticker}</Link>
                <span style={{ flex: 1, color: labelLight, lineHeight: 1.45 }}>{item.text}</span>
                <span style={scoreBadge(item.score)}>{item.score}</span>
              </div>
            ))}
          </div>
        </Card>

        <Card title="MACRO AND FEED HEALTH" accentColor="#a78bfa">
          <div style={{ display: "grid", gap: 10 }}>
            <BriefLine label="FRED" value={payloads?.macro?.fred_available == null ? "UNKNOWN" : payloads.macro.fred_available ? "ONLINE" : "DEGRADED"} color={payloads?.macro?.fred_available == null ? muted : payloads.macro.fred_available ? "#4ade80" : "#fb923c"} />
            <BriefLine label="SCAN ROWS" value={num(feedCount(payloads?.scan, "results"))} color={accent} />
            <BriefLine label="CLAUDE CALLS" value={num(payloads?.scan?.claude_calls_made)} color={payloads?.scan?.claude_calls_made ? "#fb923c" : muted} />
            <BriefLine label="PM RISK" value={money(pmSummary.planned_risk)} color="#f87171" />
            <div style={{ borderTop: hairline, paddingTop: 10 }}>
              <div style={eyebrow}>UPCOMING MACRO</div>
              {macroEvents.slice(0, 5).map(event => (
                <div key={`${event.tag}-${event.date}`} style={{ display: "flex", gap: 10, padding: "8px 0", borderBottom: hairline }}>
                  <span style={{ color: event.is_imminent ? "#fb923c" : accent, minWidth: 54 }}>{event.days_until}D</span>
                  <span style={{ color: labelLight, flex: 1 }}>{event.tag} - {event.name}</span>
                </div>
              ))}
            </div>
          </div>
        </Card>
      </div>
      </div>
    </CrtShell>
  );
}

function IntelCard({ row, selected, onSelect, onWatch, watching }) {
  const borderColor = row.lane === "ACT NOW" ? "#4ade80" : row.lane === "HIDDEN TAPE" ? "#93c5fd" : sourceColor(row.sources[0]);
  return (
    <div className="row-hover" style={{
      border: `0.5px solid ${borderColor}66`,
      background: `linear-gradient(135deg, ${borderColor}10 0%, rgba(255,255,255,0.015) 55%, transparent 100%)`,
      padding: 14,
      minHeight: 220,
      display: "flex",
      flexDirection: "column",
      gap: 10,
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <Link to={`/ticker/${row.ticker}`} style={tickerLink(25)}>${row.ticker}</Link>
          <div style={{ color: muted, fontSize: 10, letterSpacing: "0.12em", marginTop: 4 }}>
            {row.company || row.sector || "INTEL STACK"}
          </div>
        </div>
        <div style={{ textAlign: "right" }}>
          <div style={scoreBadge(row.score)}>{row.score}/100</div>
          <div style={{ ...pill(borderColor), marginTop: 6 }}>{row.lane}</div>
        </div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 80px), 1fr))", gap: 8 }}>
        <MiniMetric label="PRICE" value={finite(row.price) != null ? `$${num(row.price, 2)}` : "-"} />
        <MiniMetric label="PM" value={row.pmAction || "-"} color={row.pmAction === "ACCUMULATE" ? "#4ade80" : accent} />
        <MiniMetric label="RR" value={num(row.rr, 2)} color={row.rr >= 2 ? "#4ade80" : labelLight} />
        <MiniMetric label="ALLOC" value={money(row.allocation)} />
      </div>

      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {row.sources.map(source => <SourceChip key={source} source={source} note={row.sourceNotes[source]} />)}
      </div>

      <div style={{ color: labelLight, lineHeight: 1.45, fontSize: 12, minHeight: 38 }}>
        {row.thesis || row.reasons[0] || row.catalysts[0] || "No thesis text yet. Open ticker profile for the raw evidence stack."}
      </div>

      <div style={{ display: "flex", gap: 8, marginTop: "auto", flexWrap: "wrap" }}>
        <button aria-expanded={selected} onClick={onSelect} style={buttonStyle(selected ? "#f87171" : accent2)}>
          {selected ? "CLOSE STACK" : "OPEN STACK"}
        </button>
        <button onClick={onWatch} disabled={watching} style={buttonStyle(accent)}>
          {watching ? "ADDING" : "WATCH"}
        </button>
        <Link to={`/ticker/${row.ticker}`} style={{ ...buttonStyle(labelLight), textDecoration: "none" }}>
          TICKER SHEET
        </Link>
      </div>

      {selected && (
        <div style={{ borderTop: hairline, paddingTop: 10, display: "grid", gap: 8 }}>
          <StackList label="SIGNALS" items={row.signals} />
          <StackList label="CATALYSTS" items={row.catalysts} />
          <StackList label="REASONS" items={row.reasons} />
          <StackList label="CAUTIONS" items={row.cautions} color="#fb923c" />
        </div>
      )}
    </div>
  );
}

function XFactorRow({ alert }) {
  const trigger = alert.primary_trigger || {};
  const stocktwits = alert.stocktwits || {};
  const google = alert.google_trends || {};
  const bullish = stocktwits.bullish_pct != null ? stocktwits.bullish_pct * 100 : null;
  const trendScore = google.score ?? google.current ?? google.value ?? google.trend_score;
  const trendRatio = trigger.spike_x ?? trigger.ratio ?? google.spike_x ?? google.ratio;

  return (
    <div style={xfRow}>
      <div style={{ minWidth: 72 }}>
        <Link to={`/ticker/${cleanTicker(alert.ticker)}`} style={tickerLink(18)}>
          ${cleanTicker(alert.ticker)}
        </Link>
        <div style={{ color: muted, fontSize: 9, marginTop: 4 }}>{shortDate(alert.fired_at || alert.created_at)}</div>
      </div>
      <div style={{ flex: 1, minWidth: 180 }}>
        <div style={{ display: "flex", gap: 7, flexWrap: "wrap", marginBottom: 7 }}>
          <span style={pill("#22d3ee")}>{trigger.platform || "RETAIL"}</span>
          <span style={pill(accent)}>{trigger.type || "TRIGGER"}</span>
          {trendRatio != null && <span style={pill("#4ade80")}>{trendRatio}X SPIKE</span>}
        </div>
        <div style={{ color: labelLight, fontSize: 11, lineHeight: 1.45 }}>
          {asArray(alert.triggers).map(item => `${item.platform || "FEED"}:${item.type || "signal"}`).join(" / ") || "Retail tape alert"}
        </div>
      </div>
      <div style={xfMetricWrap}>
        <MiniMetric label="ST MSGS" value={stocktwits.mentions_24h != null ? num(stocktwits.mentions_24h) : "-"} color="#22d3ee" />
        <MiniMetric label="BULLISH" value={bullish != null ? percent(bullish, 0) : "-"} color={bullish >= 60 ? "#4ade80" : bullish < 40 ? "#f87171" : labelLight} />
        <MiniMetric label="GOOGLE" value={trendScore != null ? num(trendScore, 0) : "NO DATA"} color={trendScore != null ? accent : muted} />
        <MiniMetric label="REDDIT" value={alert.reddit_mentions != null ? num(alert.reddit_mentions) : "-"} color="#a78bfa" />
      </div>
    </div>
  );
}

function SourceChip({ source, note }) {
  const color = sourceColor(source);
  return (
    <span title={note || sourceLabel(source)} style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 6,
      padding: "4px 7px",
      border: `0.5px solid ${color}66`,
      background: `${color}10`,
      color,
      fontSize: 9,
      letterSpacing: "0.12em",
      fontWeight: 700,
      whiteSpace: "nowrap",
    }}>
      {sourceLabel(source)}
    </span>
  );
}

function NewsWireRow({ item }) {
  const biasColor = item.bias === "BULLISH" ? "#4ade80" : item.bias === "BEARISH" ? "#f87171" : accent;
  const laneColor = item.intel_lane === "URGENT" ? "#f87171" : item.intel_lane === "WATCH" ? "#93c5fd" : muted;
  return (
    <div style={newsRow}>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <span style={scoreBadge(finite(item.score))}>{num(item.score)}</span>
        <span style={pill(laneColor)}>{item.intel_lane || "MARKET"}</span>
        <span style={pill(biasColor)}>{item.bias || "NEUTRAL"}</span>
        <span style={{ color: muted, fontSize: 10, letterSpacing: "0.1em" }}>{item.source || "RSS"} / {fmtNewsAge(item.age_minutes)}</span>
      </div>
      <a href={item.url || "#"} target="_blank" rel="noreferrer" style={newsTitle}>
        {item.title}
      </a>
      {item.summary && <div style={newsSummary}>{item.summary}</div>}
      <div style={{ display: "flex", gap: 7, flexWrap: "wrap", alignItems: "center" }}>
        {asArray(item.tickers).slice(0, 8).map(ticker => (
          <Link key={ticker} to={`/ticker/${cleanTicker(ticker)}`} style={previewChip(item.score || 50)}>
            ${cleanTicker(ticker)}
          </Link>
        ))}
        {asArray(item.urgency_terms).slice(0, 5).map(term => <span key={term} style={pill("#fb923c")}>{String(term).toUpperCase()}</span>)}
      </div>
    </div>
  );
}

function MiniMetric({ label, value, color = labelLight }) {
  return (
    <div style={{ border: hairline, padding: "8px 7px", background: "rgba(255,255,255,0.015)", minWidth: 0 }}>
      <div style={{ color: dim, fontSize: 8, letterSpacing: "0.16em" }}>{label}</div>
      <div title={String(value ?? "-")} style={{ color, fontSize: 12, marginTop: 5, overflowWrap: "anywhere" }}>
        {value}
      </div>
    </div>
  );
}

function StackList({ label, items, color = labelLight }) {
  const visible = (items || []).slice(0, 6);
  if (!visible.length) return null;
  return (
    <div>
      <div style={eyebrow}>{label}</div>
      <div style={{ display: "grid", gap: 5 }}>
        {visible.map((item, idx) => (
          <div key={`${label}-${idx}`} style={{ color, fontSize: 11, lineHeight: 1.45 }}>
            <span style={{ color: dim, marginRight: 7 }}>{String(idx + 1).padStart(2, "0")}</span>{item}
          </div>
        ))}
      </div>
    </div>
  );
}

function fmtNewsAge(minutes) {
  const n = finite(minutes);
  if (n == null) return "NO TIME";
  if (n < 60) return `${Math.round(n)}M AGO`;
  if (n < 1440) return `${Math.round(n / 60)}H AGO`;
  return `${Math.round(n / 1440)}D AGO`;
}

function BriefLine({ label, value, color = labelLight }) {
  return (
    <div style={{ display: "flex", justifyContent: "space-between", gap: 12, borderBottom: hairline, padding: "8px 0" }}>
      <span style={{ color: dim, fontSize: 10, letterSpacing: "0.16em" }}>{label}</span>
      <span style={{ color, fontSize: 12, textAlign: "right" }}>{value}</span>
    </div>
  );
}

function PulseBar({ value, color }) {
  return (
    <div aria-hidden="true" style={{ height: 4, background: "rgba(255,255,255,0.06)", marginTop: 10, overflow: "hidden" }}>
      <div style={{
        width: `${Math.max(0, Math.min(100, finite(value) ?? 0))}%`,
        height: "100%",
        background: color,
        boxShadow: `0 0 10px ${color}66`,
      }} />
    </div>
  );
}

function EmptyState({ text }) {
  return <div style={{ color: muted, padding: "18px 6px", letterSpacing: "0.08em" }}>{text}</div>;
}

function Th({ children }) {
  return <th style={th}>{children}</th>;
}

function Td({ children, color = labelLight, muted: isMuted = false, strong = false }) {
  return (
    <td style={{ ...td, color: isMuted ? muted : color, fontWeight: strong ? 800 : 500 }}>
      {children}
    </td>
  );
}

const commandDeck = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 310px), 1fr))",
  gap: 18,
  marginBottom: 22,
};

const spotlightPanel = {
  border: hairline,
  borderTop: `1px solid ${accent}`,
  background: `linear-gradient(135deg, rgba(200,168,75,0.12), rgba(94,234,212,0.035) 42%, ${pageBg} 120%)`,
  padding: 22,
  minHeight: 260,
  display: "flex",
  flexDirection: "column",
};

const spotlightText = {
  color: labelLight,
  lineHeight: 1.65,
  margin: "14px 0 0",
  maxWidth: 860,
  fontSize: 13,
};

const deckActions = {
  display: "flex",
  gap: 8,
  flexWrap: "wrap",
  marginTop: "auto",
  paddingTop: 18,
};

const radarPanel = {
  border: hairline,
  borderTop: `1px solid ${accent2}`,
  background: `linear-gradient(180deg, rgba(94,234,212,0.06), ${pageBg} 140%)`,
  padding: 18,
};

const macroGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(3, minmax(0, 1fr))",
  gap: 8,
  marginBottom: 12,
};

function weatherTile(risk) {
  const color = risk == null ? muted : risk ? "#fb923c" : "#4ade80";
  return {
    border: `0.5px solid ${color}44`,
    background: `${color}0d`,
    padding: "10px 9px",
    minWidth: 0,
  };
}

const matrixGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 150px), 1fr))",
  gap: 10,
};

function matrixCell(ok) {
  return {
    border: `0.5px solid ${ok ? "rgba(74,222,128,0.28)" : "rgba(248,113,113,0.32)"}`,
    background: ok ? "rgba(74,222,128,0.035)" : "rgba(248,113,113,0.035)",
    padding: 12,
  };
}

const intelMosaic = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 430px), 1fr))",
  gap: 18,
  alignItems: "start",
};

const mosaicStack = {
  display: "grid",
  gap: 18,
  minWidth: 0,
};

const darkPoolGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))",
  gap: 14,
};

const newsWireGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 260px), 1fr))",
  gap: 14,
};

const newsWireCommand = {
  border: "0.5px solid rgba(147,197,253,0.28)",
  background: "linear-gradient(180deg, rgba(147,197,253,0.085), rgba(255,255,255,0.014))",
  padding: 16,
  alignSelf: "start",
};

const newsWireMetrics = {
  display: "grid",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: 8,
  marginTop: 14,
};

const newsWireList = {
  display: "grid",
  gap: 9,
  alignSelf: "start",
};

const newsRow = {
  border: hairline,
  background: "linear-gradient(90deg, rgba(147,197,253,0.045), rgba(255,255,255,0.012))",
  padding: "11px 12px",
  display: "grid",
  gap: 8,
};

const newsTitle = {
  color: labelLight,
  fontSize: 13,
  lineHeight: 1.42,
  textDecoration: "none",
  fontWeight: 800,
  letterSpacing: "0.035em",
};

const newsSummary = {
  color: muted,
  fontSize: 11,
  lineHeight: 1.45,
};

const darkPoolCommand = {
  border: "0.5px solid rgba(245,158,11,0.28)",
  background: "linear-gradient(180deg, rgba(245,158,11,0.095), rgba(255,255,255,0.014))",
  padding: 16,
  alignSelf: "start",
};

const darkPoolMetrics = {
  display: "grid",
  gridTemplateColumns: "repeat(2, minmax(0, 1fr))",
  gap: 8,
  marginTop: 14,
};

const darkPoolTableWrap = {
  border: hairline,
  background: "rgba(255,255,255,0.012)",
  overflowX: "auto",
  minWidth: 0,
  maxWidth: "100%",
};

const geoGrid = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 250px), 1fr))",
  gap: 14,
};

const geoHero = {
  border: hairline,
  background: "linear-gradient(180deg, rgba(251,146,60,0.08), rgba(255,255,255,0.015))",
  padding: 16,
  alignSelf: "start",
};

function geoRow(severity) {
  const color = severity === "CRITICAL" ? "#f87171" : "#fb923c";
  return {
    border: `0.5px solid ${color}55`,
    background: `${color}0c`,
    padding: "11px 12px",
  };
};

const table = {
  width: "100%",
  borderCollapse: "collapse",
  minWidth: 680,
};

const th = {
  color: dim,
  fontSize: 9,
  letterSpacing: "0.16em",
  fontWeight: 800,
  textAlign: "left",
  padding: "10px 11px",
  borderBottom: hairline,
  whiteSpace: "nowrap",
};

const td = {
  padding: "11px",
  fontSize: 11,
  letterSpacing: "0.08em",
  verticalAlign: "middle",
  whiteSpace: "nowrap",
};

const eyebrow = {
  color: dim,
  fontSize: 9,
  letterSpacing: "0.18em",
  fontWeight: 700,
  marginBottom: 8,
};

const tapeRow = {
  display: "flex",
  flexWrap: "wrap",
  minWidth: 0,
  alignItems: "center",
  gap: 10,
  border: hairline,
  background: "rgba(255,255,255,0.015)",
  padding: "10px 12px",
};

const xfRow = {
  display: "flex",
  gap: 12,
  alignItems: "center",
  border: hairline,
  background: "linear-gradient(90deg, rgba(34,211,238,0.055), rgba(255,255,255,0.012))",
  padding: "11px 12px",
  flexWrap: "wrap",
};

const xfMetricWrap = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 74px), 1fr))",
  gap: 7,
  width: "100%",
  minWidth: 0,
};

const discoveryPanel = {
  border: hairline,
  background: "rgba(147,197,253,0.04)",
  padding: 12,
  alignSelf: "start",
};

function tickerLink(size) {
  return {
    color: accent,
    fontSize: size,
    overflowWrap: "anywhere",
    fontWeight: 800,
    letterSpacing: "0.08em",
    textDecoration: "none",
    textShadow: "0 0 12px rgba(200,168,75,0.18)",
  };
}

function scoreBadge(score) {
  const color = score >= 76 ? "#4ade80" : score >= 55 ? accent : score >= 35 ? "#fb923c" : muted;
  return {
    display: "inline-block",
    color,
    border: `0.5px solid ${color}66`,
    background: `${color}10`,
    padding: "5px 8px",
    fontSize: 11,
    letterSpacing: "0.1em",
    fontWeight: 800,
  };
}

function previewChip(score) {
  const color = score >= 76 ? "#4ade80" : score >= 55 ? accent : score >= 35 ? "#fb923c" : muted;
  return {
    color,
    border: `0.5px solid ${color}55`,
    background: `${color}10`,
    padding: "5px 7px",
    fontSize: 10,
    letterSpacing: "0.1em",
    fontWeight: 800,
    textDecoration: "none",
    whiteSpace: "normal",
    overflowWrap: "anywhere",
  };
}

function pill(color) {
  return {
    display: "inline-block",
    color,
    border: `0.5px solid ${color}66`,
    background: `${color}10`,
    padding: "4px 8px",
    fontSize: 9,
    letterSpacing: "0.12em",
    fontWeight: 700,
    whiteSpace: "normal",
    overflowWrap: "anywhere",
  };
}

function buttonStyle(color) {
  return {
    background: `${color}10`,
    border: `0.5px solid ${color}66`,
    color,
    padding: "8px 10px",
    cursor: "pointer",
    fontFamily: "inherit",
    fontSize: 10,
    letterSpacing: "0.12em",
    fontWeight: 700,
  };
}

const laneShell = {
  border: hairline,
  background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 200%)`,
  marginBottom: 22,
  position: "relative",
};

const laneBar = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 14,
  padding: "12px 14px",
  borderTop: `1px solid ${accent2}`,
  background: "rgba(255,255,255,0.02)",
  flexWrap: "wrap",
};

const lanePreview = {
  flex: 1,
  minWidth: "min(100%, 260px)",
  display: "flex",
  alignItems: "center",
  gap: 7,
  flexWrap: "wrap",
};

const laneBody = {
  borderTop: hairline,
  padding: 14,
};

const laneSelect = {
  minWidth: "min(100%, 230px)",
  maxWidth: "100%",
  background: `${accent}10`,
  border: `0.5px solid ${accent}88`,
  color: accent,
  padding: "10px 12px",
  cursor: "pointer",
  fontFamily: "inherit",
  fontSize: 11,
  letterSpacing: "0.12em",
  fontWeight: 800,
};
