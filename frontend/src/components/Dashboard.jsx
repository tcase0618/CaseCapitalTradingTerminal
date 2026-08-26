import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { API } from "../config";
import { toast } from "sonner";
import { CrtShell, SystemBar, tokens as crtTokens } from "./CrtShell";

const cls = (...x) => x.filter(Boolean).join(" ");

const fmtPrice = (v) => (v == null ? "—" : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
const fmtPct = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}%`);
const fmtAmt = (v) => (v == null ? "—" : `$${(Number(v) / 1e6).toFixed(1)}M`);

const SIG_TAG = {
  insider_cluster_buy:  { label: "INSIDER",         color: "#c084fc", bg: "rgba(192,132,252,0.07)", bd: "rgba(192,132,252,0.35)" },
  high_short_interest:  { label: "SHORT",           color: "#f87171", bg: "rgba(248,113,113,0.07)", bd: "rgba(248,113,113,0.35)" },
  upcoming_earnings:    { label: "EARNINGS",        color: "#60a5fa", bg: "rgba(96,165,250,0.07)",  bd: "rgba(96,165,250,0.35)" },
  CONTRACT_SURGE:       { label: "CONTRACT SURGE",  color: "#c8a84b", bg: "rgba(200,168,75,0.07)",  bd: "rgba(200,168,75,0.35)" },
  NEW_WINNER:           { label: "NEW WINNER",      color: "#c8a84b", bg: "rgba(200,168,75,0.07)",  bd: "rgba(200,168,75,0.35)" },
  CONCENTRATION_WIN:    { label: "CONCENTRATION",   color: "#c8a84b", bg: "rgba(200,168,75,0.07)",  bd: "rgba(200,168,75,0.35)" },
  MOMENTUM_STACK:       { label: "MOMENTUM",        color: "#c8a84b", bg: "rgba(200,168,75,0.07)",  bd: "rgba(200,168,75,0.35)" },
  BUDGET_SURGE:         { label: "BUDGET SURGE",    color: "#c8a84b", bg: "rgba(200,168,75,0.07)",  bd: "rgba(200,168,75,0.35)" },
  CONGRESSIONAL_BUY:    { label: "CONGRESS BUY",    color: "#34d399", bg: "rgba(52,211,153,0.07)",  bd: "rgba(52,211,153,0.35)" },
  PRE_AWARD:            { label: "PRE AWARD",       color: "#c8a84b", bg: "rgba(200,168,75,0.07)",  bd: "rgba(200,168,75,0.35)" },
  UNUSUAL_FLOW:         { label: "UNUSUAL FLOW",    color: "#2dd4bf", bg: "rgba(45,212,191,0.07)",  bd: "rgba(45,212,191,0.35)" },
  CALL_SWEEP:           { label: "CALL SWEEP",      color: "#5eead4", bg: "rgba(94,234,212,0.10)",  bd: "rgba(94,234,212,0.45)" },
  IV_CRUSH:             { label: "IV CRUSH RISK",   color: "#f87171", bg: "rgba(248,113,113,0.07)", bd: "rgba(248,113,113,0.35)" },
};

const RISK_PILL = {
  LOW:     { color: "#4ade80", bd: "rgba(74,222,128,0.3)" },
  MEDIUM:  { color: "#c8a84b", bd: "rgba(200,168,75,0.3)" },
  HIGH:    { color: "#fb923c", bd: "rgba(251,146,60,0.3)" },
  EXTREME: { color: "#f87171", bd: "rgba(248,113,113,0.3)" },
};

function ScoreRing({ score = 0 }) {
  const color = score >= 7 ? "#c8a84b" : score >= 5 ? "#fb923c" : "#6b7280";
  const r = 18, c = 2 * Math.PI * r;
  const offset = c - (Math.max(0, Math.min(10, score)) / 10) * c;
  return (
    <svg width="40" height="40" viewBox="0 0 40 40" style={{ transform: "rotate(-90deg)" }}>
      <circle cx="20" cy="20" r={r} stroke="rgba(255,255,255,0.08)" strokeWidth="2" fill="none" />
      <circle cx="20" cy="20" r={r} stroke={color} strokeWidth="2" fill="none"
        strokeDasharray={c} strokeDashoffset={offset} style={{ transition: "stroke-dashoffset 0.4s" }} />
      <text x="20" y="20" textAnchor="middle" dominantBaseline="central" fill={color}
        fontSize="14" fontWeight="700" fontFamily="Courier New" style={{ transform: "rotate(90deg)", transformOrigin: "20px 20px" }}>
        {score}
      </text>
    </svg>
  );
}

function StrengthBars({ score = 0 }) {
  const filled = Math.round((Math.max(0, Math.min(10, score)) / 10) * 5);
  return (
    <div style={{ display: "flex", flexDirection: "column-reverse", gap: 2 }}>
      {[0, 1, 2, 3, 4].map((i) => (
        <div key={i} style={{
          width: 22, height: 2,
          background: i < filled ? "#c8a84b" : "#1a1a2e",
          transition: "background 0.2s",
        }} />
      ))}
    </div>
  );
}

function useClock() {
  const [t, setT] = useState(new Date());
  useEffect(() => {
    const id = setInterval(() => setT(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  return t;
}

function _padZ(n) { return n < 10 ? `0${n}` : `${n}`; }

function formatET(t) {
  return t.toLocaleTimeString("en-US", { hour12: false, timeZone: "America/New_York" });
}

function formatETDate(t) {
  return t.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "2-digit", year: "numeric", timeZone: "America/New_York" }).toUpperCase();
}

function nextScanCountdown(now) {
  // Next 8:00 AM America/New_York
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
  const parts = Object.fromEntries(fmt.formatToParts(now).map(p => [p.type, p.value]));
  const todayET = new Date(`${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}:${parts.second}`);
  const target = new Date(todayET);
  target.setHours(8, 0, 0, 0);
  if (todayET.getTime() >= target.getTime()) target.setDate(target.getDate() + 1);
  let diff = Math.max(0, Math.floor((target.getTime() - todayET.getTime()) / 1000));
  const hh = Math.floor(diff / 3600); diff -= hh * 3600;
  const mm = Math.floor(diff / 60);   diff -= mm * 60;
  return `${_padZ(hh)}:${_padZ(mm)}:${_padZ(diff)}`;
}

const accent = "#c8a84b";
const muted = "#6b7280";
const dim = "#374151";
const labelLight = "#4a5568";
const cardBg = "#0c0c12";
const pageBg = "#06060a";
const hairline = "0.5px solid rgba(255,255,255,0.06)";
const hairlineLight = "0.5px solid rgba(255,255,255,0.04)";

export default function Dashboard() {
  const [status, setStatus] = useState(null);
  const [scan, setScan] = useState(null);
  const [activity, setActivity] = useState([]);
  const [watchlist, setWatchlist] = useState([]);
  const [alerts, setAlerts] = useState([]);
  const [contracts, setContracts] = useState([]);
  const [congress, setCongress] = useState([]);
  const [squeezeLb, setSqueezeLb] = useState([]);
  const [fyStatus, setFyStatus] = useState({ fy_multiplier_active: false, days_to_fy_end: 0 });
  const [preview, setPreview] = useState(null);
  const [scanTabs, setScanTabs] = useState(null);
  const [scannerView, setScannerView] = useState("core");
  const [kronosCard, setKronosCard] = useState(null);
  const [kronosLoading, setKronosLoading] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [selected, setSelected] = useState(null);

  const [tInput, setTInput] = useState("");
  const [aTicker, setATicker] = useState("");
  const [aPrice, setAPrice] = useState("");

  const [openPanel, setOpenPanel] = useState(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);

  const t = useClock();

  const refresh = useCallback(async () => {
    // Independent fetches — one slow/failing endpoint shouldn't blank the dashboard
    axios.get(`${API}/status`).then(r => setStatus(r.data)).catch(e => console.error("status:", e));
    axios.get(`${API}/scan/latest`).then(r => setScan(r.data)).catch(e => console.error("scan:", e));
    axios.get(`${API}/activity?limit=20`).then(r => setActivity(r.data)).catch(e => console.error("activity:", e));
    axios.get(`${API}/watchlist`).then(r => setWatchlist(r.data)).catch(e => console.error("wl:", e));
    axios.get(`${API}/alerts`).then(r => setAlerts(r.data)).catch(e => console.error("alerts:", e));
    axios.get(`${API}/contracts?days=90&min_amount=1000000`)
      .then(r => setContracts(r.data.contracts || []))
      .catch(e => console.error("contracts:", e));
    axios.get(`${API}/congress/recent?days=30`).then(r => setCongress(r.data)).catch(e => console.error("congress:", e));
    axios.get(`${API}/squeeze/leaderboard/top?limit=10`).then(r => setSqueezeLb(r.data)).catch(e => console.error("squeeze:", e));
    axios.get(`${API}/fy/status`).then(r => setFyStatus(r.data)).catch(e => console.error("fy:", e));
    axios.get(`${API}/scan/preview`).then(r => setPreview(r.data)).catch(e => console.error("preview:", e));
    axios.get(`${API}/scan/tabs`).then(r => setScanTabs(r.data)).catch(e => console.error("scan-tabs:", e));
  }, []);

  useEffect(() => { refresh(); const id = setInterval(refresh, 15000); return () => clearInterval(id); }, [refresh]);

  useEffect(() => {
    if (!selected) {
      setKronosCard(null);
      return;
    }
    let cancelled = false;
    setKronosLoading(true);
    axios.get(`${API}/kronos/battle_card/${selected}`)
      .then(r => { if (!cancelled) setKronosCard(r.data); })
      .catch(e => { if (!cancelled) setKronosCard({ error: e.message }); })
      .finally(() => { if (!cancelled) setKronosLoading(false); });
    return () => { cancelled = true; };
  }, [selected]);

  const runScan = async () => {
    setScanning(true);
    toast("SCAN INITIATED");
    try {
      const { data } = await axios.post(`${API}/scan/run`);
      setScan(data);
      toast(`SCAN COMPLETE — ${data.results?.length || 0} TARGETS`);
      refresh();
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "request failed";
      toast(`SCAN FAILED - ${detail}`);
    }
    setScanning(false);
  };

  const dispatch = async () => {
    setDispatching(true);
    try {
      const { data } = await axios.post(`${API}/scan/dispatch`);
      toast(`DISPATCHED — ${data.messages_sent}/${data.messages_built} MSG · ${(data.char_counts || []).join("/")} CHARS`);
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "request failed";
      toast(`DISPATCH FAILED - ${detail}`);
    }
    setDispatching(false);
  };

  const fireToast = (cmd) => toast(`COMMAND DISPATCHED — ${cmd}`);

  const addWatch = async () => {
    if (!tInput.trim()) return;
    try { await axios.post(`${API}/watchlist`, { ticker: tInput.trim() }); setTInput(""); refresh(); } catch {}
  };
  const removeWatch = async (t) => { await axios.delete(`${API}/watchlist/${t}`); refresh(); };
  const addAlert = async () => {
    if (!aTicker.trim() || !aPrice) return;
    try { await axios.post(`${API}/alerts`, { ticker: aTicker.trim(), target_price: parseFloat(aPrice) });
          setATicker(""); setAPrice(""); refresh(); } catch {}
  };
  const removeAlert = async (t) => { await axios.delete(`${API}/alerts/${t}`); refresh(); };

  const results = useMemo(() => {
    const r = [...((scan && scan.results) || [])];
    r.sort((a, b) => (b.signal_score || 0) - (a.signal_score || 0));
    return r;
  }, [scan]);
  const ledgerCandidates = scanTabs?.ledger?.candidates || scan?.candidate_ledger?.candidates || [];
  const scannerTabRows = {
    core: results,
    docket: ledgerCandidates,
    lottery: scanTabs?.tabs?.lottery || scan?.lottery_picks || [],
    options: scanTabs?.tabs?.options || [],
    pharma: scanTabs?.tabs?.pharma || [],
    earnings: Object.values(scanTabs?.tabs?.earnings?.by_day || {}).flat(),
    court: scanTabs?.tabs?.case_court || [],
  };
  const scannerTabCounts = {
    core: results.length,
    docket: ledgerCandidates.length,
    lottery: scannerTabRows.lottery.length,
    options: scannerTabRows.options.length,
    pharma: scannerTabRows.pharma.length,
    earnings: scannerTabRows.earnings.length,
    court: scannerTabRows.court.length,
  };
  const scannerNewStats = scanTabs?.new_since_previous || {};

  const counts = {
    insider: scan?.raw_counts?.insider_clusters || 0,
    short: scan?.raw_counts?.high_short_interest || 0,
    earnings: scan?.raw_counts?.upcoming_earnings || 0,
    gov: scan?.raw_counts?.gov_public_tickers || 0,
    congress: congress.length || 0,
    pre_award: 0,
  };

  const totalSignalsFired = results.reduce((acc, r) => acc + (r.signals?.length || 0), 0);
  const cacheRate = scan?.pre_filter_passed
    ? Math.round((scan.claude_cache_hits / scan.pre_filter_passed) * 100) : 0;

  // Bottom callouts
  const topPick = results[0];
  const sqWatch = useMemo(() =>
    [...results].sort((a, b) => ((b.squeeze?.score || 0) - (a.squeeze?.score || 0)))[0],
  [results]);
  const govPlay = useMemo(() =>
    [...results].filter(r => r.contracts?.length)
      .sort((a, b) => (b.contracts?.[0]?.amount || 0) - (a.contracts?.[0]?.amount || 0))[0],
  [results]);
  const catalystWatch = useMemo(() =>
    [...results].filter(r => r.time_target?.target_date)
      .sort((a, b) => (a.time_target?.days_remaining || 999) - (b.time_target?.days_remaining || 999))[0],
  [results]);

  return (
    <CrtShell title="SCANNER"
      headerRight={
        <button data-testid="run-scan-button" onClick={runScan} disabled={scanning}
          style={{
            background: scanning ? "rgba(200,168,75,0.15)" : "transparent",
            border: `0.5px solid ${accent}`, color: accent, fontSize: 12,
            padding: "8px 18px", cursor: scanning ? "wait" : "pointer",
            letterSpacing: "0.12em", fontFamily: "JetBrains Mono", fontWeight: 700,
            boxShadow: scanning ? "none" : `0 0 16px ${accent}30`,
          }}>
          {scanning ? "SCANNING..." : "[ RUN SCAN ]"}
        </button>
      }>
      {/* === MAIN === */}
      <div style={{ marginLeft: -30, marginRight: -30, marginTop: -22 }}>
        {/* Metrics */}
        <div className="fade-in" style={{
          background: `linear-gradient(180deg, ${cardBg} 0%, ${pageBg} 200%)`,
          borderBottom: hairline, display: "grid", gridTemplateColumns: "repeat(7, 1fr)",
          position: "relative",
        }}>
            {/* accent stripe along top */}
            <div style={{
              position: "absolute", top: 0, left: 0, right: 0, height: 1,
              background: `linear-gradient(90deg, ${accent} 0%, ${accent}33 30%, transparent 100%)`,
            }} />
            {[
              { label: "UNIVERSE SWEPT", v: scan?.universe_size || "—",
                sub: `INSIDER ${counts.insider} · SHORT ${counts.short} · GOV ${counts.gov}`,
                color: accent, isPrimary: true },
              { label: "TARGETS ACQUIRED", v: scan?.pre_filter_passed || 0, sub: `${scan?.results?.length || 0} ANALYZED`, color: "#fff" },
              { label: "INSIDER SIGNALS", v: counts.insider, sub: "OPENINSIDER", color: "#c084fc" },
              { label: "SHORT SIGNALS", v: counts.short, sub: counts.short === 0 ? "FINVIZ · EMPTY" : "FINVIZ",
                color: counts.short === 0 ? "#f87171" : "#f87171" },
              { label: "GOV SIGNALS", v: counts.gov, sub: "USASPENDING", color: "#5eead4" },
              { label: "CACHE SAVED", v: scan?.claude_cache_hits || 0, sub: `${scan?.claude_calls_made || 0} BATCHED`, color: muted },
              { label: "UPLINK", v: status?.bot?.telegram_configured ? "LIVE" : "OFF", sub: "TELEGRAM", color: status?.bot?.telegram_configured ? "#4ade80" : "#fb923c", isText: true },
            ].map((c, i) => (
              <div key={i} data-testid={`metric-${i}`} className="row-hover" style={{
                padding: "18px 20px", borderRight: i < 6 ? hairline : "none",
                position: "relative",
                background: c.isPrimary ? `linear-gradient(90deg, rgba(200,168,75,0.05) 0%, transparent 100%)` : "transparent",
              }}>
                {c.isPrimary && (
                  <div style={{
                    position: "absolute", left: 0, top: 14, bottom: 14, width: 2,
                    background: accent, boxShadow: `0 0 6px ${accent}80`,
                  }} />
                )}
                <div style={{
                  fontSize: 9, color: muted, letterSpacing: "0.18em", fontWeight: 600,
                  display: "flex", alignItems: "center", gap: 6,
                }}>
                  <span style={{ color: dim, fontSize: 8 }}>▸</span>
                  {c.label}
                </div>
                <div className="num" style={{
                  fontSize: c.isText ? 22 : 26, fontWeight: 600, color: c.color,
                  marginTop: 8, fontFamily: "JetBrains Mono, Courier New",
                  letterSpacing: "0.02em",
                  textShadow: c.isPrimary ? `0 0 12px ${accent}40` : "none",
                }}>{c.v}</div>
                <div style={{ fontSize: 9, color: muted, marginTop: 5, letterSpacing: "0.12em" }}>{c.sub}</div>
              </div>
            ))}
          </div>

          {/* Classify line */}
          <div style={{ display: "flex", alignItems: "center", gap: 12, padding: "5px 20px", background: pageBg }}>
            <div style={{ flex: 1, height: 1, background: "rgba(200,168,75,0.12)" }} />
            <span style={{ fontSize: 7, color: accent, letterSpacing: "0.18em" }}>
              CLASSIFIED · ALPHA INTELLIGENCE SYSTEM · SIGNAL CONFIDENCE RANKED
            </span>
            <div style={{ flex: 1, height: 1, background: "rgba(200,168,75,0.12)" }} />
          </div>

          {/* FY banner */}
          {fyStatus.fy_multiplier_active && (
            <div data-testid="fy-banner" style={{
              padding: "8px 20px", background: "rgba(200,168,75,0.05)", borderBottom: hairline,
              display: "flex", justifyContent: "space-between", alignItems: "center",
            }}>
              <span style={{ fontSize: 9, color: accent, letterSpacing: "0.14em" }}>
                ◆ GOV FISCAL YEAR-END · CONTRACT SIGNALS WEIGHTED 1.5×
              </span>
              <span style={{ fontSize: 9, color: accent }}>{fyStatus.days_to_fy_end}D TO FY END</span>
            </div>
          )}

          {/* Intel feed header */}
          <div style={{ padding: "12px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", borderBottom: hairlineLight }}>
            <span style={{ fontSize: 8, color: dim, letterSpacing: "0.14em" }}>ACTIVE INTELLIGENCE</span>
            <div style={{ flex: 1, height: 1, margin: "0 16px", background: "rgba(200,168,75,0.15)" }} />
            <span style={{ fontSize: 8, color: accent, letterSpacing: "0.1em" }}>
              {scannerView === "core" ? `SHOWING ${results.length} OF ${scan?.pre_filter_passed || 0} TARGETS` : `${scannerTabCounts[scannerView] || 0} ${scannerView.toUpperCase()} ROWS`}
            </span>
            <button data-testid="run-scan-button" onClick={runScan} disabled={scanning}
              style={{
                marginLeft: 16, background: scanning ? "rgba(200,168,75,0.1)" : "transparent",
                border: `0.5px solid ${accent}`, color: accent, fontSize: 9, padding: "5px 12px",
                cursor: scanning ? "wait" : "pointer", letterSpacing: "0.1em", fontFamily: "Courier New",
                transition: "background 0.2s",
              }}
              onMouseEnter={e => !scanning && (e.target.style.background = "rgba(200,168,75,0.1)")}
              onMouseLeave={e => !scanning && (e.target.style.background = "transparent")}>
              {scanning ? "SCANNING..." : "RUN SCAN NOW"}
            </button>
          </div>

          <ScannerSubTabs
            active={scannerView}
            onChange={setScannerView}
            counts={scannerTabCounts}
            ledgerSummary={scanTabs?.ledger?.summary || scan?.candidate_ledger?.summary || {}}
          />

          <ScannerNewStockGraphic
            view={scannerView}
            stat={scannerNewStats[scannerView] || { count: 0, tickers: [], total: scannerTabCounts[scannerView] || 0 }}
          />

          {scannerView !== "core" && (
            <ScannerSubtabPanel
              view={scannerView}
              rows={scannerTabRows[scannerView] || []}
              errors={scanTabs?.errors || {}}
              selected={selected}
              onSelect={setSelected}
              kronosCard={kronosCard}
              kronosLoading={kronosLoading}
            />
          )}

          {/* Stock rows */}
          {scannerView === "core" && results.length === 0 && (
            <div style={{ padding: 40, textAlign: "center", color: dim, fontSize: 11 }}>
              NO TARGETS ACQUIRED — RUN SCAN TO BEGIN
            </div>
          )}
          {scannerView === "core" && results.map((r, idx) => {
            const isSel = selected === r.ticker;
            const risk = r.risk || {};
            const tg = r.targets || {};
            const sq = r.squeeze || {};
            const tt = r.time_target || {};
            const targetColor = risk.level === "LOW" ? "#4ade80"
                                : risk.level === "MEDIUM" ? accent
                                : risk.level === "HIGH" ? "#fb923c" : "#f87171";
            return (
              <div key={r.ticker} data-testid={`result-row-${r.ticker}`}
                onClick={() => setSelected(isSel ? null : r.ticker)}
                style={{
                  display: "grid", gridTemplateColumns: "56px 1fr 130px",
                  borderBottom: hairlineLight, cursor: "pointer",
                  background: isSel ? "#0f0f15" : "transparent",
                  borderLeft: isSel ? `2px solid ${accent}` : "2px solid transparent",
                  transition: "background 0.15s, border-left 0.15s",
                }}
                onMouseEnter={e => !isSel && (e.currentTarget.style.background = "#111118")}
                onMouseLeave={e => !isSel && (e.currentTarget.style.background = "transparent")}>

                {/* Confidence */}
                <div style={{ padding: "16px 0 16px 16px", display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
                  <ScoreRing score={r.signal_score || 0} />
                  <StrengthBars score={r.signal_score || 0} />
                </div>

                {/* Main */}
                <div style={{ padding: "14px 12px 14px 8px" }}>
                  <div style={{ display: "flex", alignItems: "baseline" }}>
                    <Link to={`/ticker/${r.ticker}`} style={{
                      fontSize: 19, color: "#fff", fontWeight: 700, letterSpacing: "0.05em",
                      textDecoration: "none", fontFamily: "Courier New",
                    }}>${r.ticker}</Link>
                    {r.fy_multiplier_applied && <span style={{ fontSize: 9, color: accent, marginLeft: 10 }}>FY×1.5</span>}
                    <span style={{ fontSize: 13, color: dim, marginLeft: 10 }}>{r.sector || ""}</span>
                    <span style={{ fontSize: 13, color: labelLight, marginLeft: 10, fontFamily: "Courier New" }}>{fmtPrice(r.price)}</span>
                    {r.options?.crush_risk === "SEVERE" || r.options?.crush_risk === "HIGH" ? (
                      <span style={{
                        fontSize: 9, padding: "2px 7px", marginLeft: 10,
                        border: `0.5px solid ${SIG_TAG.IV_CRUSH.bd}`,
                        color: SIG_TAG.IV_CRUSH.color, background: SIG_TAG.IV_CRUSH.bg,
                        letterSpacing: "0.08em", fontWeight: 700,
                      }}>IV CRUSH {r.options.crush_risk}</span>
                    ) : null}
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 5, margin: "7px 0 9px" }}>
                    {(r.signals || []).map(s => {
                      const m = SIG_TAG[s] || { label: s, color: muted, bg: "rgba(255,255,255,0.03)", bd: "rgba(255,255,255,0.1)" };
                      return (
                        <span key={s} style={{
                          fontSize: 10, padding: "3px 9px", border: `0.5px solid ${m.bd}`,
                          color: m.color, background: m.bg,
                          letterSpacing: "0.08em", fontWeight: 700,
                        }}>{m.label}</span>
                      );
                    })}
                  </div>
                  <div style={{
                    fontSize: 13, color: "#e5e7eb", lineHeight: 1.7, marginBottom: 9,
                    display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
                  }}>{r.thesis}</div>
                  <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 10, color: dim, letterSpacing: "0.08em" }}>
                    <span>ENTRY <span style={{ color: labelLight }}>{fmtPrice(r.entry_low)}–{fmtPrice(r.entry_high)}</span></span>
                    {tt.target_date && <span>HOLD <span style={{ color: labelLight }}>{tt.hold_period_low}–{tt.hold_period_high}d</span></span>}
                    {sq.score != null && <span>SQUEEZE <span style={{ color: labelLight }}>{sq.score}/100</span></span>}
                    {r.learning_score != null && <span>CASE SCORE <span style={{ color: accent, fontWeight: 700 }}>{r.learning_score}</span></span>}
                    {r.trade_score != null && (() => {
                      const ts = Number(r.trade_score), ls = r.learning_score != null ? Number(r.learning_score) : null;
                      // Compare at 1-decimal precision to match displayed values
                      const tsR = Math.round(ts * 10), lsR = ls != null ? Math.round(ls * 10) : null;
                      const c = lsR == null ? accent : tsR > lsR ? "#4ade80" : tsR < lsR ? "#f87171" : accent;
                      return <span style={{ marginLeft: 10 }}>TRADE <span style={{ color: c, fontWeight: 700 }}>{ts.toFixed(1)}</span></span>;
                    })()}
                  </div>
                  {/* Options panel */}
                  {r.options && (r.options.contract || r.options.spread || r.options.strategy === "AVOID_OPTIONS") && (
                    <div style={{
                      marginTop: 12, padding: "10px 12px",
                      background: "#0a0a10", borderLeft: `2px solid ${accent}`,
                    }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
                        <span style={{ fontSize: 10, color: dim, letterSpacing: "0.14em" }}>
                          {"// OPTIONS INTEL — "}
                          <span style={{ color: accent, fontWeight: 700 }}>{r.options.strategy_name || r.options.strategy}</span>
                        </span>
                        <span style={{ fontSize: 10, color: dim, letterSpacing: "0.08em" }}>
                          IV RANK <span style={{ color: r.options.iv_rank < 30 ? "#4ade80" : r.options.iv_rank > 70 ? "#f87171" : accent, fontWeight: 700 }}>{r.options.iv_rank}%</span>
                          <span style={{ color: muted, marginLeft: 4 }}>({r.options.iv_label})</span>
                        </span>
                      </div>
                      <div style={{ fontSize: 11, color: "#d1d5db", lineHeight: 1.6, marginBottom: 8 }}>
                        {r.options.one_liner || r.options.strategy_reason}
                      </div>
                      {r.options.strategy === "AVOID_OPTIONS" ? (
                        <div style={{ fontSize: 11, color: "#f87171", fontWeight: 700, letterSpacing: "0.06em" }}>
                          DO NOT BUY OPTIONS · {r.options.crush_recommendation}
                        </div>
                      ) : r.options.contract ? (
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, fontSize: 11 }}>
                          <div><span style={{ color: dim }}>BUY </span><span style={{ color: accent, fontWeight: 700 }}>${r.options.contract.strike}{r.options.contract.type}</span></div>
                          <div><span style={{ color: dim }}>EXP </span><span style={{ color: labelLight }}>{r.options.contract.expiration}</span></div>
                          <div><span style={{ color: dim }}>PREMIUM </span><span style={{ color: "#fff", fontWeight: 700 }}>${r.options.contract.premium}</span></div>
                          <div><span style={{ color: dim }}>MAX LOSS </span><span style={{ color: "#f87171", fontWeight: 700 }}>${r.options.contract.max_loss}</span></div>
                          {r.options.spread && (<>
                            <div><span style={{ color: dim }}>SPREAD </span><span style={{ color: accent }}>${r.options.spread.buy_strike}/${r.options.spread.sell_strike}</span></div>
                            <div><span style={{ color: dim }}>MAX PROFIT </span><span style={{ color: "#4ade80", fontWeight: 700 }}>${r.options.spread.max_profit}</span></div>
                            <div><span style={{ color: dim }}>R/R </span><span style={{ color: "#fff", fontWeight: 700 }}>{r.options.spread.risk_reward}:1</span></div>
                            <div><span style={{ color: dim }}>BREAK EVEN </span><span style={{ color: labelLight }}>${r.options.spread.break_even}</span></div>
                          </>)}
                          <div><span style={{ color: dim }}>LIQ </span><span style={{ color: r.options.contract.liquidity === "GOOD" ? "#4ade80" : r.options.contract.liquidity === "WARN" ? "#fb923c" : "#f87171", fontWeight: 700 }}>{r.options.contract.liquidity}</span></div>
                          {r.options.flow && (<>
                            <div><span style={{ color: dim }}>FLOW </span><span style={{ color: r.options.flow.flow_bias === "BULLISH" ? "#4ade80" : r.options.flow.flow_bias === "BEARISH" ? "#f87171" : muted, fontWeight: 700 }}>{r.options.flow.flow_bias}</span></div>
                            <div><span style={{ color: dim }}>P/C </span><span style={{ color: labelLight }}>{r.options.flow.call_put_ratio}</span></div>
                            <div><span style={{ color: dim }}>CRUSH </span><span style={{ color: r.options.crush_risk === "SEVERE" ? "#f87171" : r.options.crush_risk === "HIGH" ? "#fb923c" : r.options.crush_risk === "LOW" ? "#4ade80" : accent, fontWeight: 700 }}>{r.options.crush_risk}</span></div>
                          </>)}
                        </div>
                      ) : null}
                    </div>
                  )}
                </div>

                {/* Target */}
                <div style={{ padding: "14px 16px 14px 8px", display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
                  <div style={{ fontSize: 24, fontWeight: 700, color: targetColor, fontFamily: "Courier New", letterSpacing: "0.02em" }}>
                    {fmtPrice(tg.target_blended)}
                  </div>
                  <div style={{ fontSize: 12, color: targetColor, opacity: 0.6, fontWeight: 700 }}>{fmtPct(tg.upside_blended)}</div>
                  {tt.target_date && (
                    <div style={{ fontSize: 10, color: accent, letterSpacing: "0.06em" }}>TARGET — {tt.target_date}</div>
                  )}
                  <div style={{ fontSize: 10, color: dim, letterSpacing: "0.06em" }}>ENTRY {fmtPrice(r.entry_low)} — {fmtPrice(r.entry_high)}</div>
                  <div style={{ fontSize: 10, color: "rgba(248,113,113,0.6)", letterSpacing: "0.06em" }}>STOP {fmtPrice(r.stop_loss)}</div>
                  <div style={{
                    fontSize: 10, padding: "3px 9px",
                    border: `0.5px solid ${(RISK_PILL[risk.level] || RISK_PILL.MEDIUM).bd}`,
                    color: (RISK_PILL[risk.level] || RISK_PILL.MEDIUM).color,
                    borderRadius: 2, marginTop: 3, letterSpacing: "0.1em", fontWeight: 700,
                  }}>{risk.level || "?"}</div>
                  {sq.score != null && <div style={{ fontSize: 10, color: labelLight, letterSpacing: "0.06em" }}>SQZ {sq.score}/100</div>}
                </div>
                {isSel && (
                  <div style={{ gridColumn: "1 / -1", borderTop: hairlineLight, background: "#08080d", padding: "14px 18px 18px" }}>
                    <ScannerKronosBattleCard loading={kronosLoading} payload={kronosCard} fallbackRow={r} />
                  </div>
                )}
              </div>
            );
          })}

          {/* Collapsed panels */}
          <CollapsiblePanel
            testid="contracts-section"
            title={`GOVERNMENT CONTRACT FEED · ${contracts.length}`}
            isOpen={openPanel === "contracts"}
            onToggle={() => setOpenPanel(openPanel === "contracts" ? null : "contracts")}
            action={
              <Link to="/contracts" data-testid="contracts-view-all"
                style={{ color: accent, fontSize: 9, letterSpacing: "0.14em",
                          textDecoration: "none", fontWeight: 700 }}>
                VIEW ALL ▸
              </Link>
            }
          >
            {contracts.slice(0, 10).map((c, i) => (
              <Link key={i} to="/contracts" data-testid={`contract-row-${c.ticker}`} style={{
                display: "grid", gridTemplateColumns: "60px 1fr 100px",
                padding: "8px 20px", borderBottom: hairlineLight, fontSize: 9,
                textDecoration: "none",
              }}>
                <span style={{ color: accent, fontWeight: 700 }}>${c.ticker}</span>
                <span style={{ color: muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.agency}</span>
                <span style={{ color: "#4ade80", textAlign: "right" }}>{fmtAmt(c.amount)}</span>
              </Link>
            ))}
          </CollapsiblePanel>

          <CollapsiblePanel
            testid="squeeze-leaderboard-section"
            title={`SQUEEZE LEADERBOARD · TOP ${squeezeLb.length}`}
            isOpen={openPanel === "squeeze"}
            onToggle={() => setOpenPanel(openPanel === "squeeze" ? null : "squeeze")}
          >
            {squeezeLb.map((s, i) => (
              <div key={s.ticker} data-testid={`squeeze-row-${s.ticker}`} style={{
                display: "flex", justifyContent: "space-between",
                padding: "6px 20px", borderBottom: hairlineLight, fontSize: 9,
              }}>
                <span style={{ color: muted }}>#{i + 1} ${s.ticker}</span>
                <span style={{ color: s.score >= 66 ? "#fb923c" : s.score >= 41 ? accent : muted }}>{s.score}/100</span>
              </div>
            ))}
          </CollapsiblePanel>

          <CollapsiblePanel
            testid="congress-section"
            title={`CONGRESSIONAL INTELLIGENCE · ${congress.length}`}
            isOpen={openPanel === "congress"}
            onToggle={() => setOpenPanel(openPanel === "congress" ? null : "congress")}
          >
            {congress.slice(0, 10).map((c, i) => (
              <div key={i} data-testid={`congress-row-${c.ticker}`} style={{
                display: "grid", gridTemplateColumns: "60px 1fr 80px 80px",
                padding: "6px 20px", borderBottom: hairlineLight, fontSize: 9, alignItems: "center", gap: 8,
              }}>
                <span style={{ color: "#34d399", fontWeight: 700 }}>${c.ticker}</span>
                <span style={{ color: muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {c.name} <span style={{ color: dim }}>({c.chamber})</span>
                </span>
                <span style={{ color: c.committee_match ? accent : labelLight, textAlign: "right" }}>
                  +{c.weight_points}pt{c.committee_match && " ✓"}
                </span>
                <span style={{ color: dim, textAlign: "right" }}>{c.tx_date}</span>
              </div>
            ))}
          </CollapsiblePanel>

          {/* Telegram preview */}
          <CollapsiblePanel
            testid="preview-section"
            title={`TELEGRAM DISPATCH PREVIEW · ${preview?.total_chars || 0} CHARS · ${(preview?.messages || []).length} MSG`}
            isOpen={openPanel === "preview"}
            onToggle={() => setOpenPanel(openPanel === "preview" ? null : "preview")}
          >
            {(preview?.messages || []).map((m, i) => (
              <div key={i} style={{
                padding: "10px 20px", borderBottom: hairlineLight,
                fontSize: 9, color: muted, whiteSpace: "pre-wrap", lineHeight: 1.5,
              }}>
                <div style={{ fontSize: 8, color: accent, marginBottom: 6, letterSpacing: "0.12em" }}>
                  MSG {i + 1} · {m.length} CHARS
                </div>
                {m.replace(/<[^>]+>/g, "")}
              </div>
            ))}
          </CollapsiblePanel>

          {/* Bottom callouts */}
          <div style={{
            background: cardBg, borderTop: hairline,
            display: "grid", gridTemplateColumns: "repeat(4, 1fr)", marginTop: "auto",
          }}>
            {[
              { idx: "01", label: "TOP PICK", t: topPick, detail: topPick ? `${topPick.signal_score}/10 · ${fmtPct(topPick.targets?.upside_blended)}` : "—" },
              { idx: "02", label: "SQUEEZE WATCH", t: sqWatch, detail: sqWatch ? `${(sqWatch.squeeze?.score || 0)}/100 · ${fmtPrice(sqWatch.targets?.target_blended)}` : "—" },
              { idx: "03", label: "GOV PLAY", t: govPlay, detail: govPlay ? `${fmtAmt(govPlay.contracts?.[0]?.amount)} · ${fmtPct(govPlay.targets?.upside_blended)}` : "—" },
              { idx: "04", label: "CATALYST WATCH", t: catalystWatch, detail: catalystWatch ? `${catalystWatch.time_target?.target_date} · ${(catalystWatch.signals || [])[0] || "—"}` : "—" },
            ].map((c, i) => (
              <div key={i} data-testid={`callout-${c.label.toLowerCase().replace(' ', '-')}`}
                style={{ padding: "10px 18px", borderRight: i < 3 ? hairline : "none", display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontSize: 9, color: dim, fontWeight: 700 }}>{c.idx}</span>
                <div>
                  <div style={{ fontSize: 7, color: dim, letterSpacing: "0.1em" }}>{c.label}</div>
                  <div style={{ fontSize: 13, color: "#fff", fontWeight: 700, marginTop: 2 }}>
                    {c.t ? `$${c.t.ticker}` : "—"}
                  </div>
                  <div style={{ fontSize: 8, color: labelLight, marginTop: 1 }}>{c.detail}</div>
                </div>
              </div>
            ))}
          </div>

          {/* Footer */}
          <div style={{
            background: pageBg, borderTop: hairlineLight, padding: "8px 20px",
            display: "flex", alignItems: "center", justifyContent: "space-between",
          }}>
            <div style={{ display: "flex", gap: 20, fontSize: 8, color: dim, letterSpacing: "0.06em" }}>
              <span>SCANS {scan?.results?.length || 0}</span>
              <span>CACHE {cacheRate}%</span>
              <span>MODEL CLAUDE-HAIKU-4-5</span>
              <span>VERSION 3.1.0</span>
            </div>
            <div style={{ flex: 1, height: 1, background: "rgba(255,255,255,0.04)", margin: "0 20px" }} />
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <span style={{ fontSize: 9, color: accent, fontFamily: "Courier New" }}>NEXT SCAN {nextScanCountdown(t)}</span>
              <button data-testid="dispatch-button" onClick={dispatch} disabled={dispatching}
                style={{
                  background: dispatching ? "rgba(200,168,75,0.1)" : "transparent",
                  border: `0.5px solid ${accent}`, color: accent, fontSize: 9,
                  padding: "5px 14px", cursor: dispatching ? "wait" : "pointer",
                  letterSpacing: "0.1em", fontFamily: "Courier New",
                }}
                onMouseEnter={e => !dispatching && (e.target.style.background = "rgba(200,168,75,0.1)")}
                onMouseLeave={e => !dispatching && (e.target.style.background = "transparent")}>
                {dispatching ? "DISPATCHING..." : "DISPATCH TO TELEGRAM"}
              </button>
            </div>
          </div>
      </div>
    </CrtShell>
  );
}

function ScannerNewStockGraphic({ view, stat }) {
  const count = Number(stat?.count || 0);
  const total = Number(stat?.total || 0);
  const tickers = Array.isArray(stat?.tickers) ? stat.tickers.filter(Boolean) : [];
  const intensity = Math.min(1, count / Math.max(1, total || count || 1));
  const railWidth = `${Math.max(count ? 18 : 4, Math.round(intensity * 100))}%`;
  const label = view === "docket" ? "UNIFIED DOCKET" : view.toUpperCase();
  return (
    <div style={scannerNewGraphic}>
      <div style={scannerNewGraphicMain}>
        <div>
          <div style={scannerNewLabel}>NEW SINCE LAST SCAN</div>
          <div style={scannerNewMeta}>{label} / {total} TRACKED</div>
        </div>
        <div style={scannerNewCountWrap}>
          <strong style={{ color: count ? "#5eead4" : muted, fontSize: 25, lineHeight: 1, fontWeight: 900 }}>{count}</strong>
          <span>{count === 1 ? "NEW NAME" : "NEW NAMES"}</span>
        </div>
      </div>
      <div style={scannerNewRail}>
        <div style={{ ...scannerNewRailFill, width: railWidth, opacity: count ? 1 : 0.35 }} />
        {[0, 1, 2, 3, 4].map(i => (
          <span
            key={i}
            style={{
              ...scannerNewDot,
              left: `${8 + i * 21}%`,
              opacity: count > i ? 1 : 0.22,
              background: count > i ? "#5eead4" : "rgba(255,255,255,0.16)",
              boxShadow: count > i ? "0 0 10px rgba(94,234,212,0.45)" : "none",
            }}
          />
        ))}
      </div>
      <div style={scannerNewTickerWrap}>
        {tickers.length ? tickers.slice(0, 10).map(t => (
          <span key={t} style={scannerNewTicker}>${t}</span>
        )) : (
          <span style={scannerNewQuiet}>NO NEW TICKERS VERSUS PREVIOUS SCAN</span>
        )}
      </div>
    </div>
  );
}

function ScannerSubTabs({ active, onChange, counts, ledgerSummary }) {
  const tabs = [
    ["core", "Core"],
    ["docket", "Unified Docket"],
    ["lottery", "Lottery"],
    ["options", "Options"],
    ["pharma", "Pharma"],
    ["earnings", "Earnings"],
    ["court", "Case Court"],
  ];
  return (
    <div style={scannerTabsWrap}>
      <div style={scannerTabs}>
        {tabs.map(([key, label]) => (
          <button key={key} onClick={() => onChange(key)} style={scannerTabBtn(active === key)}>
            <span>{label}</span>
            <b>{counts[key] || 0}</b>
          </button>
        ))}
      </div>
      <div style={scannerLedgerLine}>
        LEDGER {ledgerSummary.total || 0} / CORE {ledgerSummary.core || 0} / OPTIONS {ledgerSummary.options || 0} / PHARMA {ledgerSummary.pharma || 0} / LOTTERY {ledgerSummary.lottery || 0}
      </div>
    </div>
  );
}

function ScannerSubtabPanel({ view, rows, errors, selected, onSelect, kronosCard, kronosLoading }) {
  const error = errors?.[view === "court" ? "case_court" : view];
  return (
    <div style={scannerPanel}>
      <div style={scannerPanelHead}>
        <span>{view === "docket" ? "UNIFIED CANDIDATE LEDGER" : `${view.toUpperCase()} SCAN VIEW`}</span>
        <small>{error ? `DEGRADED: ${error}` : "LIVE ROUTING VIEW"}</small>
      </div>
      {!rows.length ? (
        <div style={scannerEmpty}>NO ROWS LOADED FOR THIS SCAN FAMILY</div>
      ) : (
        <div style={scannerCompactRows}>
          {rows.slice(0, 80).map((row, idx) => (
            <ScannerFamilyRow
              key={`${view}-${row.candidate_id || row.ticker || row.symbol || "row"}-${idx}`}
              view={view}
              row={row}
              idx={idx}
              selected={selected}
              onSelect={onSelect}
              kronosCard={kronosCard}
              kronosLoading={kronosLoading}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function normalizeScannerSignals(value) {
  if (!value) return [];
  if (Array.isArray(value)) return value.filter(Boolean).map(String);
  if (value instanceof Set) return Array.from(value).filter(Boolean).map(String);
  if (typeof value === "object") return Object.keys(value).filter(Boolean);
  return [String(value)];
}

function normalizeScannerPrice(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? n : null;
}

function scannerRingScore(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return n > 10 ? Math.max(0, Math.min(10, n / 10)) : Math.max(0, Math.min(10, n));
}

function scannerDisplayScore(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "-";
  return n > 10 ? n.toFixed(1) : n.toFixed(1);
}

function scannerDisplayConfidence(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return null;
  return n <= 1 ? Math.round(n * 100) : Math.round(n);
}

function scannerFamilyRowModel(view, row) {
  const ticker = row.ticker || row.underlying || row.symbol || row.rows?.core_scan?.ticker || "-";
  const scanner = row.strategy_scanner || row.source_scan || {};
  const strategyCase = row.strategy_case || {};
  const options = row.options || {};
  const targets = row.targets || {};
  const squeeze = row.squeeze || {};
  const timeTarget = row.time_target || {};
  const score = row.pm_score ?? row.case_score ?? row.score ?? row.signal_score ?? row.binary_event_score ?? row.candidate_quality_score;
  const action = row.action || row.tier || row.final_route || row.judge?.posture || row.pm_action || row.route || scanner.lane || "-";
  const signals = [
    ...normalizeScannerSignals(row.signals),
    ...normalizeScannerSignals(row.triggers),
    ...normalizeScannerSignals(row.strategy_tags),
    ...normalizeScannerSignals(row.sources || row.candidate_sources),
  ].filter((x, i, arr) => arr.indexOf(x) === i);
  const detail = row.thesis || row.company || row.company_name || row.drug || row.strategy || row.reason || row.judge?.detail || row.data_quality || `${view.toUpperCase()} scanner candidate`;
  const target = targets.target_blended ?? row.target_blended ?? row.target_price ?? row.exit_plan?.target;
  const upside = targets.upside_blended ?? row.upside_blended ?? row.expected_return_pct ?? row.forecast_pct;
  const entryLow = row.entry_low ?? row.entry_plan?.entry_low ?? row.price;
  const entryHigh = row.entry_high ?? row.entry_plan?.entry_high ?? row.price;
  const stop = row.stop_loss ?? row.stop ?? row.exit_plan?.stop ?? row.risk?.stop_loss;
  const route = row.pm_route || row.route || row.final_route || row.instrument || scanner.family || view.toUpperCase();
  const riskLevel = row.risk?.level || row.risk_level || (
    String(strategyCase.risk_shape || scanner.risk_shape || "").includes("very_high") ? "EXTREME" :
    String(strategyCase.risk_shape || scanner.risk_shape || "").includes("high") ? "HIGH" :
    row.blocked_reasons?.length ? "HIGH" : "MEDIUM"
  );
  return {
    ticker: String(ticker || "-").toUpperCase(),
    score,
    action: String(action || "-").replace(/_/g, " "),
    signals,
    detail,
    price: normalizeScannerPrice(row.price ?? row.current_price ?? row.underlying_price),
    target: normalizeScannerPrice(target),
    upside,
    entryLow: normalizeScannerPrice(entryLow),
    entryHigh: normalizeScannerPrice(entryHigh),
    stop: normalizeScannerPrice(stop),
    route: String(route || view).replace(/_/g, " "),
    riskLevel,
    scanner,
    strategyCase,
    badges: Array.isArray(scanner.badges) ? scanner.badges : [],
    blocked: normalizeScannerSignals(row.blocked_reasons),
    dataQuality: row.data_quality || row.options_data_quality || row.qc_status || scanner.data_quality,
    options,
    squeeze,
    timeTarget,
    raw: row,
  };
}

function scannerBadgeStyle(tone) {
  const map = {
    risk: { color: "#fbbf24", bg: "rgba(251,191,36,0.08)", bd: "rgba(251,191,36,0.32)" },
    data: { color: "#5eead4", bg: "rgba(94,234,212,0.07)", bd: "rgba(94,234,212,0.28)" },
    volume: { color: "#93c5fd", bg: "rgba(147,197,253,0.07)", bd: "rgba(147,197,253,0.25)" },
    learning: { color: "#c4b5fd", bg: "rgba(196,181,253,0.08)", bd: "rgba(196,181,253,0.32)" },
    options: { color: "#fb7185", bg: "rgba(251,113,133,0.07)", bd: "rgba(251,113,133,0.28)" },
  };
  const pick = map[tone] || { color: labelLight, bg: "rgba(255,255,255,0.04)", bd: "rgba(255,255,255,0.12)" };
  return {
    fontSize: 9,
    padding: "3px 7px",
    border: `0.5px solid ${pick.bd}`,
    color: pick.color,
    background: pick.bg,
    letterSpacing: "0.08em",
    fontWeight: 900,
    maxWidth: 150,
    overflow: "hidden",
    textOverflow: "ellipsis",
    whiteSpace: "nowrap",
  };
}

function ScannerFamilyRow({ view, row, idx, selected, onSelect, kronosCard, kronosLoading }) {
  const m = scannerFamilyRowModel(view, row);
  const isSel = selected === m.ticker;
  const targetColor = String(m.action).includes("REJECT") || String(m.action).includes("BLOCK") || String(m.action).includes("OBJECT")
    ? "#f87171"
    : String(m.action).includes("WATCH")
      ? "#fbbf24"
      : String(m.action).includes("READY") || String(m.action).includes("STARTER") || String(m.action).includes("ACCUMULATE") || String(m.action).includes("PASS")
        ? "#4ade80"
        : accent;
  const tagList = m.signals.length ? m.signals.slice(0, 8) : [m.route];
  return (
    <div
      data-testid={`scanner-family-row-${view}-${m.ticker}`}
      onClick={() => onSelect?.(isSel ? null : m.ticker)}
      style={{
        display: "grid",
        gridTemplateColumns: "56px 1fr 130px",
        borderBottom: hairlineLight,
        cursor: "pointer",
        background: isSel ? "#0f0f15" : "transparent",
        borderLeft: isSel ? `2px solid ${accent}` : "2px solid transparent",
        transition: "background 0.15s, border-left 0.15s",
      }}
      onMouseEnter={e => !isSel && (e.currentTarget.style.background = "#111118")}
      onMouseLeave={e => !isSel && (e.currentTarget.style.background = "transparent")}
    >
      <div style={{ padding: "16px 0 16px 16px", display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
        <ScoreRing score={scannerRingScore(m.score)} />
        <StrengthBars score={scannerRingScore(m.score)} />
      </div>

      <div style={{ padding: "14px 12px 14px 8px", minWidth: 0 }}>
        <div style={{ display: "flex", alignItems: "baseline", minWidth: 0, flexWrap: "wrap", gap: "0 10px" }}>
          <Link to={`/ticker/${m.ticker}`} style={{
            fontSize: 19, color: "#fff", fontWeight: 700, letterSpacing: "0.05em",
            textDecoration: "none", fontFamily: "Courier New",
          }}>${m.ticker}</Link>
          <span style={{ fontSize: 11, color: accent, letterSpacing: "0.1em", fontWeight: 900 }}>{m.route.toUpperCase()}</span>
          <span style={{ fontSize: 13, color: labelLight, fontFamily: "Courier New" }}>{fmtPrice(m.price)}</span>
          {m.dataQuality && <span style={{ fontSize: 10, color: muted, letterSpacing: "0.08em" }}>{String(m.dataQuality).toUpperCase()}</span>}
        </div>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 5, margin: "7px 0 9px" }}>
          {tagList.map(s => {
            const key = String(s);
            const tag = SIG_TAG[key] || { label: key.replace(/_/g, " "), color: muted, bg: "rgba(255,255,255,0.03)", bd: "rgba(255,255,255,0.1)" };
            return (
              <span key={key} style={{
                fontSize: 10, padding: "3px 9px", border: `0.5px solid ${tag.bd}`,
                color: tag.color, background: tag.bg,
                letterSpacing: "0.08em", fontWeight: 700,
                maxWidth: 180, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>{tag.label}</span>
            );
          })}
        </div>

        {m.badges.length ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 5, margin: "-2px 0 9px" }}>
            {m.badges.map((b, i) => (
              <span key={`${b.label}-${i}`} style={scannerBadgeStyle(b.tone)}>{String(b.label || "").toUpperCase()}</span>
            ))}
          </div>
        ) : null}

        <div style={{
          fontSize: 13, color: "#e5e7eb", lineHeight: 1.7, marginBottom: 9,
          display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden",
        }}>{m.detail}</div>

        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 10, color: dim, letterSpacing: "0.08em" }}>
          <span>ENTRY <span style={{ color: labelLight }}>{fmtPrice(m.entryLow)}-{fmtPrice(m.entryHigh)}</span></span>
          {m.timeTarget.target_date && <span>HOLD <span style={{ color: labelLight }}>{m.timeTarget.hold_period_low}-{m.timeTarget.hold_period_high}d</span></span>}
          {m.squeeze.score != null && <span>SQUEEZE <span style={{ color: labelLight }}>{m.squeeze.score}/100</span></span>}
          <span>CASE <span style={{ color: accent, fontWeight: 700 }}>{scannerDisplayScore(m.strategyCase.case_score ?? m.scanner.case_score ?? m.score)}</span></span>
          {scannerDisplayConfidence(m.strategyCase.confidence) != null && <span>CONF <span style={{ color: "#5eead4", fontWeight: 700 }}>{scannerDisplayConfidence(m.strategyCase.confidence)}%</span></span>}
          {m.blocked.length ? <span>BLOCKERS <span style={{ color: "#f87171", fontWeight: 700 }}>{m.blocked.length}</span></span> : null}
        </div>

        {m.options && (m.options.contract || m.options.spread || m.options.strategy === "AVOID_OPTIONS") && (
          <div style={{
            marginTop: 12, padding: "10px 12px",
            background: "#0a0a10", borderLeft: `2px solid ${accent}`,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
              <span style={{ fontSize: 10, color: dim, letterSpacing: "0.14em" }}>
                {"// OPTIONS INTEL - "}
                <span style={{ color: accent, fontWeight: 700 }}>{m.options.strategy_name || m.options.strategy}</span>
              </span>
              <span style={{ fontSize: 10, color: dim, letterSpacing: "0.08em" }}>
                IV RANK <span style={{ color: m.options.iv_rank < 30 ? "#4ade80" : m.options.iv_rank > 70 ? "#f87171" : accent, fontWeight: 700 }}>{m.options.iv_rank}%</span>
                <span style={{ color: muted, marginLeft: 4 }}>({m.options.iv_label})</span>
              </span>
            </div>
            <div style={{ fontSize: 11, color: "#d1d5db", lineHeight: 1.6, marginBottom: 8 }}>
              {m.options.one_liner || m.options.strategy_reason}
            </div>
            {m.options.strategy === "AVOID_OPTIONS" ? (
              <div style={{ fontSize: 11, color: "#f87171", fontWeight: 700, letterSpacing: "0.06em" }}>
                DO NOT BUY OPTIONS · {m.options.crush_recommendation}
              </div>
            ) : m.options.contract ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, fontSize: 11 }}>
                <div><span style={{ color: dim }}>BUY </span><span style={{ color: accent, fontWeight: 700 }}>${m.options.contract.strike}{m.options.contract.type}</span></div>
                <div><span style={{ color: dim }}>EXP </span><span style={{ color: labelLight }}>{m.options.contract.expiration}</span></div>
                <div><span style={{ color: dim }}>PREMIUM </span><span style={{ color: "#fff", fontWeight: 700 }}>${m.options.contract.premium}</span></div>
                <div><span style={{ color: dim }}>MAX LOSS </span><span style={{ color: "#f87171", fontWeight: 700 }}>${m.options.contract.max_loss}</span></div>
              </div>
            ) : null}
          </div>
        )}

        {!m.options?.contract && !m.options?.spread && (m.blocked.length || m.scanner.screener_id || m.strategyCase.preferred_expression) && (
          <div style={{
            marginTop: 12, padding: "10px 12px",
            background: "#0a0a10", borderLeft: `2px solid ${targetColor}`,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 7, alignItems: "baseline" }}>
              <span style={{ fontSize: 10, color: dim, letterSpacing: "0.14em" }}>
                {"// SCANNER INTEL - "}
                <span style={{ color: targetColor, fontWeight: 700 }}>{m.scanner.screener_id || view.toUpperCase()}</span>
              </span>
              <span style={{ fontSize: 10, color: labelLight, letterSpacing: "0.08em" }}>
                {m.strategyCase.preferred_expression || m.route}
              </span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, fontSize: 10, color: muted }}>
              {m.blocked.length
                ? m.blocked.slice(0, 6).map(x => <span key={x} style={{ color: "#f87171" }}>{String(x).replace(/_/g, " ")}</span>)
                : <span>{m.strategyCase.execution_note || m.scanner.lane || "Routed into PM scoring format"}</span>}
            </div>
          </div>
        )}
      </div>

      <div style={{ padding: "14px 16px 14px 8px", display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 4 }}>
        <div style={{ fontSize: 24, fontWeight: 700, color: targetColor, fontFamily: "Courier New", letterSpacing: "0.02em", textAlign: "right" }}>
          {m.target ? fmtPrice(m.target) : m.action.toUpperCase()}
        </div>
        <div style={{ fontSize: 12, color: targetColor, opacity: 0.72, fontWeight: 700 }}>
          {m.upside != null ? fmtPct(m.upside) : `SCORE ${scannerDisplayScore(m.score)}`}
        </div>
        {m.timeTarget.target_date && (
          <div style={{ fontSize: 10, color: accent, letterSpacing: "0.06em" }}>TARGET - {m.timeTarget.target_date}</div>
        )}
        <div style={{ fontSize: 10, color: dim, letterSpacing: "0.06em" }}>ENTRY {fmtPrice(m.entryLow)} - {fmtPrice(m.entryHigh)}</div>
        <div style={{ fontSize: 10, color: "rgba(248,113,113,0.6)", letterSpacing: "0.06em" }}>STOP {fmtPrice(m.stop)}</div>
        <div style={{
          fontSize: 10, padding: "3px 9px",
          border: `0.5px solid ${(RISK_PILL[m.riskLevel] || RISK_PILL.MEDIUM).bd}`,
          color: (RISK_PILL[m.riskLevel] || RISK_PILL.MEDIUM).color,
          borderRadius: 2, marginTop: 3, letterSpacing: "0.1em", fontWeight: 700,
        }}>{m.riskLevel || "?"}</div>
        {m.squeeze.score != null && <div style={{ fontSize: 10, color: labelLight, letterSpacing: "0.06em" }}>SQZ {m.squeeze.score}/100</div>}
      </div>

      {isSel && (
        <div style={{ gridColumn: "1 / -1", borderTop: hairlineLight, background: "#08080d", padding: "14px 18px 18px" }}>
          <ScannerKronosBattleCard loading={kronosLoading} payload={kronosCard} fallbackRow={{ ...m.raw, ticker: m.ticker, price: m.price }} />
        </div>
      )}
    </div>
  );
}

function ScannerCompactRow({ view, row, idx }) {
  const ticker = row.ticker || row.underlying || row.symbol || row.rows?.core_scan?.ticker || "-";
  const score = row.pm_score ?? row.score ?? row.signal_score ?? row.binary_event_score ?? row.candidate_quality_score ?? "-";
  const action = row.action || row.tier || row.final_route || row.judge?.posture || row.pm_action || row.route || "-";
  const sources = row.sources || row.candidate_sources || [];
  const tags = row.strategy_tags || row.triggers || row.signals || row.blocked_reasons || [];
  const detail = row.company || row.company_name || row.drug || row.strategy || row.reason || row.judge?.detail || row.thesis || row.data_quality || "";
  const color = String(action).includes("REJECT") || String(action).includes("OBJECT") ? "#f87171"
    : String(action).includes("WATCH") ? "#fbbf24"
    : String(action).includes("READY") || String(action).includes("STARTER") || String(action).includes("PASS") ? "#4ade80"
    : accent;
  return (
    <div style={scannerCompactRow}>
      <div style={scannerRank}>{idx + 1}</div>
      <div style={{ minWidth: 0 }}>
        <Link to={`/ticker/${ticker}`} style={scannerTicker}>${ticker}</Link>
        <span style={scannerSubText}>{detail}</span>
      </div>
      <div style={scannerChipWrap}>
        {(sources.length ? sources : tags).slice(0, 4).map(x => <span key={x} style={scannerMiniChip}>{String(x).replace(/_/g, " ")}</span>)}
      </div>
      <div style={{ textAlign: "right" }}>
        <div style={{ color, fontWeight: 900, fontSize: 13 }}>{action}</div>
        <small style={{ color: labelLight }}>SCORE {typeof score === "number" ? score.toFixed(1) : score}</small>
      </div>
    </div>
  );
}

function ScannerKronosBattleCard({ loading, payload, fallbackRow }) {
  const card = payload?.battle_card || {};
  const probs = card.probabilities || {};
  const horizons = card.horizons || {};
  const exit = card.exit_forecast || {};
  const tt = fallbackRow?.time_target || {};
  const holdLow = Number(tt.hold_period_low || 0);
  const holdHigh = Number(tt.hold_period_high || 0);
  const holdMid = holdLow && holdHigh ? Math.round((holdLow + holdHigh) / 2) : Number(fallbackRow?.hold_window_days || 0);
  const holdLabel = holdLow && holdHigh
    ? `${holdLow}-${holdHigh} DAY SCANNER HOLD CONE`
    : holdMid
      ? `${holdMid} DAY SCANNER HOLD CONE`
      : "SCANNER HOLD CONE";
  const oneDay = horizons["1D"] || scannerConeFromBase(card.forecast_pct, 1, card.instrument);
  const holdCone = holdMid
    ? scannerConeFromBase(card.forecast_pct, Math.max(1, holdMid), card.instrument)
    : horizons["10D"] || scannerConeFromBase(card.forecast_pct, 10, card.instrument);
  if (loading) return <div style={scannerBattleEmpty}>LOADING KRONOS FORECAST...</div>;
  if (payload?.error) return <div style={scannerBattleEmpty}>KRONOS DEGRADED: {payload.error}</div>;
  return (
    <div style={scannerKronosBox}>
      <div style={scannerKronosHeader}>
        <div>
          <div style={scannerBattleLabel}>KRONOS FORECAST BOX</div>
          <div style={{ color: accent, fontSize: 18, fontWeight: 900, letterSpacing: "0.08em" }}>
            ${card.ticker || fallbackRow?.ticker} / {card.forecast_bias || "UNKNOWN"}
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(82px, 1fr))", gap: 8, minWidth: 280 }}>
          <ScannerMini label="SCORE" value={card.kronos_score ?? "-"} color={accent} />
          <ScannerMini label="CONF" value={card.confidence == null ? "-" : `${card.confidence}%`} color="#5eead4" />
          <ScannerMini label="PM" value={card.pm_action || "UNMAPPED"} color={card.aligned_with_pm ? "#4ade80" : "#fbbf24"} />
        </div>
      </div>

      <div style={scannerConeGrid}>
        <ScannerForecastCone title="1 DAY FORECAST CONE" cone={oneDay} price={fallbackRow?.price} />
        <ScannerForecastCone
          title={holdLabel}
          cone={holdCone}
          price={fallbackRow?.price}
          sub={holdLow && holdHigh ? `Midpoint estimate uses day ${holdMid}` : "Nearest Kronos horizon"}
        />
      </div>

      <div style={scannerBattleGrid}>
      <div style={scannerBattlePanel}>
        <div style={scannerBattleLabel}>ATTRIBUTION</div>
        {(card.attribution || []).map(a => (
          <div key={a.factor} style={scannerAttributionRow}>
            <span>{a.factor}</span>
            <div style={scannerTrack}><div style={{ ...scannerFill, width: `${Math.max(4, Number(a.weight || 0))}%` }} /></div>
            <strong>{a.state}</strong>
          </div>
        ))}
      </div>
      <div style={scannerBattlePanel}>
        <div style={scannerBattleLabel}>PROBABILITY GRID</div>
        {[
          ["+5%", probs.plus_5],
          ["+10%", probs.plus_10],
          ["-5%", probs.minus_5],
          ["-10%", probs.minus_10],
          ["STOP", probs.stop_hit],
          ["RATCHET", probs.ratchet_hit],
        ].map(([k, v]) => (
          <div key={k} style={scannerProbRow}>
            <span>{k}</span>
            <strong style={{ color: Number(v || 0) >= 50 ? "#4ade80" : Number(v || 0) >= 30 ? "#fbbf24" : muted }}>{v == null ? "-" : `${v}%`}</strong>
          </div>
        ))}
      </div>
      <div style={scannerBattlePanel}>
        <div style={scannerBattleLabel}>HORIZONS / EXIT</div>
        {Object.entries(horizons).slice(0, 5).map(([k, h]) => (
          <div key={k} style={scannerProbRow}>
            <span>{k}</span>
            <strong>{signedDash(h.low_pct)} / {signedDash(h.base_pct)} / {signedDash(h.high_pct)}</strong>
          </div>
        ))}
        <div style={{ ...scannerProbRow, marginTop: 8 }}>
          <span>EXIT</span>
          <strong style={{ color: "#5eead4" }}>{exit.style || "ADVISORY"}</strong>
        </div>
      </div>
      </div>
    </div>
  );
}

function ScannerForecastCone({ title, cone, price, sub }) {
  const low = Number(cone?.low_pct ?? 0);
  const base = Number(cone?.base_pct ?? 0);
  const high = Number(cone?.high_pct ?? 0);
  const maxAbs = Math.max(1, Math.abs(low), Math.abs(base), Math.abs(high));
  const lowPx = price ? price * (1 + low / 100) : null;
  const basePx = price ? price * (1 + base / 100) : null;
  const highPx = price ? price * (1 + high / 100) : null;
  return (
    <div style={scannerConeBox}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12, marginBottom: 8 }}>
        <div>
          <div style={scannerBattleLabel}>{title}</div>
          {sub && <div style={{ color: muted, fontSize: 9, marginTop: 3 }}>{sub}</div>}
        </div>
        <strong style={{ color: base >= 0 ? "#4ade80" : "#f87171", fontSize: 14 }}>{signedDash(base)}</strong>
      </div>
      <div style={scannerConeTrack}>
        <div style={{ ...scannerConeZero, left: "50%" }} />
        <div style={{
          ...scannerConeRange,
          left: `${50 + (low / maxAbs) * 45}%`,
          width: `${Math.max(2, ((high - low) / (maxAbs * 2)) * 90)}%`,
        }} />
        <div style={{
          ...scannerConeBase,
          left: `${50 + (base / maxAbs) * 45}%`,
          background: base >= 0 ? "#4ade80" : "#f87171",
        }} />
      </div>
      <div style={scannerConeLabels}>
        <span>{signedDash(low)} {lowPx ? `$${lowPx.toFixed(2)}` : ""}</span>
        <span>{signedDash(base)} {basePx ? `$${basePx.toFixed(2)}` : ""}</span>
        <span>{signedDash(high)} {highPx ? `$${highPx.toFixed(2)}` : ""}</span>
      </div>
    </div>
  );
}

function ScannerMini({ label, value, color }) {
  return (
    <div style={{ border: hairlineLight, background: "rgba(255,255,255,0.018)", padding: 10, minWidth: 0 }}>
      <div style={{ color: dim, fontSize: 8, letterSpacing: "0.12em" }}>{label}</div>
      <div style={{ color, fontSize: 15, fontWeight: 900, marginTop: 5, overflowWrap: "anywhere" }}>{value}</div>
    </div>
  );
}

function signedDash(v) {
  if (v == null) return "-";
  const n = Number(v);
  if (!Number.isFinite(n)) return "-";
  return `${n >= 0 ? "+" : ""}${n.toFixed(1)}%`;
}

function forecastColor(v) {
  if (v === "BULLISH") return "#4ade80";
  if (v === "BEARISH") return "#f87171";
  if (v === "HEDGE") return "#a78bfa";
  return "#fbbf24";
}

function scannerConeFromBase(basePct, days, instrument) {
  const base = Number(basePct || 0);
  const root = Math.sqrt(Math.max(1, days));
  const vol = instrument === "OPTION" ? 4.8 : 1.15;
  const scaled = base * Math.min(6, root / Math.sqrt(5));
  return {
    low_pct: Number((scaled - vol * root).toFixed(2)),
    base_pct: Number(scaled.toFixed(2)),
    high_pct: Number((scaled + vol * root).toFixed(2)),
  };
}

function CollapsiblePanel({ title, children, isOpen, onToggle, testid, action = null }) {
  return (
    <div data-testid={testid} style={{ borderBottom: hairlineLight }}>
      <div onClick={onToggle} style={{
        padding: "12px 20px", display: "flex", justifyContent: "space-between", alignItems: "center",
        cursor: "pointer", borderBottom: isOpen ? hairlineLight : "none",
        background: isOpen ? cardBg : "transparent",
      }}>
        <span style={{ fontSize: 8, color: dim, letterSpacing: "0.14em" }}>{title}</span>
        <div style={{ flex: 1, height: 1, margin: "0 16px", background: "rgba(200,168,75,0.15)" }} />
        {action && <span onClick={e => e.stopPropagation()} style={{ marginRight: 12 }}>{action}</span>}
        <span style={{
          fontSize: 10, color: accent, transform: `rotate(${isOpen ? 90 : 0}deg)`,
          transition: "transform 0.2s", display: "inline-block",
        }}>▶</span>
      </div>
      {isOpen && <div style={{ background: cardBg }}>{children}</div>}
    </div>
  );
}

const scannerBattleTabs = {
  display: "flex",
  gap: 8,
  marginBottom: 12,
};

const scannerBattleTab = {
  border: hairlineLight,
  background: "rgba(255,255,255,0.018)",
  padding: "7px 10px",
  fontSize: 9,
  letterSpacing: "0.13em",
  fontWeight: 900,
};

const scannerBattleGrid = {
  display: "grid",
  gridTemplateColumns: "minmax(230px, 1fr) minmax(190px, 0.75fr) minmax(230px, 1fr)",
  gap: 12,
};

const scannerTabsWrap = {
  borderBottom: hairlineLight,
  background: "rgba(255,255,255,0.012)",
};

const scannerTabs = {
  display: "grid",
  gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
  gap: 0,
};

const scannerTabBtn = (active) => ({
  border: "none",
  borderRight: hairlineLight,
  borderBottom: active ? `2px solid ${accent}` : "2px solid transparent",
  background: active ? "rgba(200,168,75,0.08)" : "transparent",
  color: active ? accent : labelLight,
  padding: "10px 8px",
  cursor: "pointer",
  fontFamily: "JetBrains Mono, Courier New",
  letterSpacing: "0.08em",
  fontSize: 9,
  display: "flex",
  justifyContent: "space-between",
  alignItems: "center",
  gap: 8,
});

const scannerLedgerLine = {
  padding: "7px 20px",
  color: muted,
  fontSize: 8,
  letterSpacing: "0.12em",
  borderTop: hairlineLight,
};

const scannerNewGraphic = {
  display: "grid",
  gridTemplateColumns: "repeat(auto-fit, minmax(190px, 1fr))",
  gap: 16,
  alignItems: "center",
  padding: "12px 20px",
  borderBottom: hairlineLight,
  background: "linear-gradient(90deg, rgba(94,234,212,0.035), rgba(200,168,75,0.025), rgba(255,255,255,0.008))",
  minWidth: 0,
};

const scannerNewGraphicMain = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 14,
  minWidth: 0,
};

const scannerNewLabel = {
  color: accent,
  fontSize: 9,
  letterSpacing: "0.16em",
  fontWeight: 900,
};

const scannerNewMeta = {
  color: muted,
  fontSize: 8,
  letterSpacing: "0.12em",
  marginTop: 4,
  overflowWrap: "anywhere",
};

const scannerNewCountWrap = {
  display: "grid",
  justifyItems: "end",
  gap: 2,
  flexShrink: 0,
};

const scannerNewRail = {
  position: "relative",
  height: 13,
  background: "rgba(255,255,255,0.045)",
  border: "0.5px solid rgba(94,234,212,0.16)",
  overflow: "hidden",
};

const scannerNewRailFill = {
  position: "absolute",
  left: 0,
  top: 0,
  bottom: 0,
  background: "linear-gradient(90deg, rgba(94,234,212,0.2), rgba(94,234,212,0.72), rgba(200,168,75,0.55))",
};

const scannerNewDot = {
  position: "absolute",
  top: 3,
  width: 6,
  height: 6,
  transform: "translateX(-50%)",
};

const scannerNewTickerWrap = {
  display: "flex",
  flexWrap: "wrap",
  justifyContent: "flex-end",
  gap: 6,
  minWidth: 0,
};

const scannerNewTicker = {
  border: "0.5px solid rgba(94,234,212,0.26)",
  background: "rgba(94,234,212,0.055)",
  color: "#bffef2",
  padding: "3px 7px",
  fontSize: 8,
  letterSpacing: "0.08em",
  fontWeight: 900,
};

const scannerNewQuiet = {
  color: muted,
  fontSize: 8,
  letterSpacing: "0.12em",
};

const scannerPanel = {
  borderBottom: hairline,
  background: "rgba(6,6,10,0.86)",
};

const scannerPanelHead = {
  display: "flex",
  justifyContent: "space-between",
  gap: 16,
  padding: "12px 20px",
  color: accent,
  fontSize: 10,
  letterSpacing: "0.14em",
  fontWeight: 900,
  borderBottom: hairlineLight,
};

const scannerCompactRows = {
  display: "grid",
  gridTemplateColumns: "1fr",
};

const scannerCompactRow = {
  display: "grid",
  gridTemplateColumns: "42px minmax(180px, 1fr) minmax(180px, 0.9fr) 150px",
  alignItems: "center",
  gap: 14,
  padding: "10px 20px",
  borderBottom: hairlineLight,
  minWidth: 0,
};

const scannerRank = {
  color: dim,
  fontSize: 10,
  fontWeight: 900,
};

const scannerTicker = {
  color: "#fff",
  textDecoration: "none",
  fontWeight: 900,
  fontSize: 15,
  letterSpacing: "0.06em",
  marginRight: 10,
};

const scannerSubText = {
  color: muted,
  fontSize: 10,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const scannerChipWrap = {
  display: "flex",
  flexWrap: "wrap",
  gap: 6,
  minWidth: 0,
};

const scannerMiniChip = {
  border: "0.5px solid rgba(94,234,212,0.22)",
  color: "#5eead4",
  background: "rgba(94,234,212,0.045)",
  padding: "3px 7px",
  fontSize: 8,
  letterSpacing: "0.08em",
  maxWidth: 150,
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const scannerEmpty = {
  color: muted,
  fontSize: 11,
  padding: 28,
  textAlign: "center",
};

const scannerKronosBox = {
  border: "0.5px solid rgba(94,234,212,0.35)",
  background: "linear-gradient(180deg, rgba(94,234,212,0.045), rgba(255,255,255,0.012))",
  padding: 14,
};

const scannerKronosHeader = {
  display: "flex",
  justifyContent: "space-between",
  alignItems: "start",
  gap: 14,
  marginBottom: 12,
};

const scannerConeGrid = {
  display: "grid",
  gridTemplateColumns: "1fr 1fr",
  gap: 12,
  marginBottom: 12,
};

const scannerConeBox = {
  border: hairlineLight,
  background: "rgba(0,0,0,0.22)",
  padding: 12,
  minWidth: 0,
};

const scannerConeTrack = {
  height: 18,
  position: "relative",
  background: "rgba(255,255,255,0.045)",
  overflow: "hidden",
  marginTop: 8,
};

const scannerConeZero = {
  position: "absolute",
  top: 0,
  bottom: 0,
  width: 1,
  background: "rgba(255,255,255,0.22)",
};

const scannerConeRange = {
  position: "absolute",
  top: 4,
  bottom: 4,
  background: "linear-gradient(90deg, rgba(248,113,113,0.5), rgba(250,204,21,0.35), rgba(74,222,128,0.5))",
  border: "0.5px solid rgba(255,255,255,0.14)",
};

const scannerConeBase = {
  position: "absolute",
  top: 1,
  bottom: 1,
  width: 3,
};

const scannerConeLabels = {
  display: "flex",
  justifyContent: "space-between",
  gap: 8,
  color: muted,
  fontSize: 9,
  marginTop: 8,
};

const scannerBattlePanel = {
  border: hairlineLight,
  background: "rgba(255,255,255,0.018)",
  padding: 12,
  minWidth: 0,
};

const scannerBattleLabel = {
  color: "#5eead4",
  fontSize: 8,
  letterSpacing: "0.16em",
  fontWeight: 900,
  marginBottom: 10,
};

const scannerBattleEmpty = {
  color: muted,
  fontSize: 11,
  padding: 14,
  border: hairlineLight,
  background: "rgba(255,255,255,0.018)",
};

const scannerAttributionRow = {
  display: "grid",
  gridTemplateColumns: "92px minmax(0, 1fr) 60px",
  gap: 8,
  alignItems: "center",
  color: muted,
  fontSize: 10,
  padding: "6px 0",
  borderTop: hairlineLight,
};

const scannerTrack = {
  height: 5,
  background: "rgba(255,255,255,0.06)",
  overflow: "hidden",
};

const scannerFill = {
  height: "100%",
  background: "#5eead4",
  boxShadow: "0 0 8px rgba(94,234,212,0.35)",
};

const scannerProbRow = {
  display: "flex",
  justifyContent: "space-between",
  gap: 10,
  borderTop: hairlineLight,
  padding: "7px 0",
  color: muted,
  fontSize: 10,
};
