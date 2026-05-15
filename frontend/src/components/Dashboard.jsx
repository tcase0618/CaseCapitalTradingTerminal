import { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import axios from "axios";
import { toast } from "sonner";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
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
    axios.get(`${API}/contracts?limit=10`).then(r => setContracts(r.data)).catch(e => console.error("contracts:", e));
    axios.get(`${API}/congress/recent?days=30`).then(r => setCongress(r.data)).catch(e => console.error("congress:", e));
    axios.get(`${API}/squeeze/leaderboard/top?limit=10`).then(r => setSqueezeLb(r.data)).catch(e => console.error("squeeze:", e));
    axios.get(`${API}/fy/status`).then(r => setFyStatus(r.data)).catch(e => console.error("fy:", e));
    axios.get(`${API}/scan/preview`).then(r => setPreview(r.data)).catch(e => console.error("preview:", e));
  }, []);

  useEffect(() => { refresh(); const id = setInterval(refresh, 15000); return () => clearInterval(id); }, [refresh]);

  const runScan = async () => {
    setScanning(true);
    toast("SCAN INITIATED");
    try {
      const { data } = await axios.post(`${API}/scan/run`);
      setScan(data);
      toast(`SCAN COMPLETE — ${data.results?.length || 0} TARGETS`);
      refresh();
    } catch { toast("SCAN FAILED"); }
    setScanning(false);
  };

  const dispatch = async () => {
    setDispatching(true);
    try {
      const { data } = await axios.post(`${API}/scan/dispatch`);
      toast(`DISPATCHED — ${data.messages_sent}/${data.messages_built} MSG · ${(data.char_counts || []).join("/")} CHARS`);
    } catch { toast("DISPATCH FAILED"); }
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
    <>
      <div className="scanline-overlay" />

      {/* Sidebar toggle (mobile) */}
      <button onClick={() => setSidebarOpen(o => !o)}
        className="fixed left-0 top-1/2 -translate-y-1/2 z-30 lg:hidden"
        style={{ background: cardBg, border: hairline, color: accent, padding: "8px 4px", fontSize: 10 }}>
        {sidebarOpen ? "<" : ">"}
      </button>

      <div className="h-screen w-screen relative z-10" style={{
        display: "grid",
        gridTemplateColumns: "180px 1fr",
        background: pageBg,
      }}>
        {/* === SIDEBAR === */}
        <aside style={{
          background: cardBg, borderRight: hairline,
          padding: "16px 14px", overflowY: "auto",
          display: "flex", flexDirection: "column", gap: 20,
        }} className={cls("z-20", !sidebarOpen && "hidden lg:flex")}>

          {/* Identity */}
          <div>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14 }}>
              <div style={{ width: 20, height: 20, border: `1.5px solid ${accent}`, display: "flex", alignItems: "center", justifyContent: "center" }}>
                <div style={{ width: 7, height: 7, background: accent }} />
              </div>
              <span style={{ fontSize: 14, color: accent, letterSpacing: "0.18em", fontWeight: 700 }}>AXIOM</span>
            </div>
            <div style={{ fontSize: 13, color: accent, fontWeight: 700, fontFamily: "Courier New" }}>{formatET(t)} ET</div>
            <div style={{ fontSize: 9, color: dim, marginTop: 2 }}>{formatETDate(t)}</div>
          </div>

          {/* System status */}
          <div>
            <div style={{ fontSize: 8, color: dim, letterSpacing: "0.12em", marginBottom: 8 }}>SYSTEM STATUS</div>
            {[
              { label: "BOT STATUS", color: "#4ade80", text: status ? "ONLINE" : "..." },
              { label: "WEBHOOK", color: status?.webhook_url ? "#4ade80" : "#fb923c", text: status?.webhook_url ? "ACTIVE" : "PENDING" },
              { label: "CLAUDE API", color: accent, text: status?.bot?.claude_configured ? "CONNECTED" : "OFFLINE" },
            ].map((s, i) => (
              <div key={i} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "3px 0", fontSize: 9 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span style={{ width: 4, height: 4, borderRadius: "50%", background: s.color }} />
                  <span style={{ color: labelLight }}>{s.label}</span>
                </div>
                <span style={{ color: s.color }}>{s.text}</span>
              </div>
            ))}
          </div>

          {/* Signal sources */}
          <div>
            <div style={{ fontSize: 8, color: dim, letterSpacing: "0.12em", marginBottom: 8 }}>SIGNAL SOURCES</div>
            {[
              { label: "INSIDER", v: counts.insider },
              { label: "SHORT", v: counts.short },
              { label: "EARNINGS", v: counts.earnings },
              { label: "GOV CONTRACTS", v: counts.gov },
              { label: "CONGRESS", v: counts.congress },
              { label: "PRE-AWARD", v: counts.pre_award },
            ].map((s, i) => (
              <div key={i} className="hover:bg-white/[0.03]"
                style={{ display: "flex", justifyContent: "space-between", padding: "3px 4px", fontSize: 9 }}>
                <span style={{ color: labelLight }}>{s.label}</span>
                <span style={{ color: muted }}>{s.v}</span>
              </div>
            ))}
          </div>

          {/* Coverage */}
          <div>
            <div style={{ fontSize: 8, color: dim, letterSpacing: "0.12em", marginBottom: 8 }}>COVERAGE</div>
            {[
              { label: "TICKERS SCANNED", v: Math.min(100, ((scan?.pre_filter_passed || 0) / 50) * 100) },
              { label: "SIGNALS FIRED", v: Math.min(100, (totalSignalsFired / 30) * 100) },
              { label: "CACHE HIT RATE", v: cacheRate },
            ].map((s, i) => (
              <div key={i} style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                  <span style={{ fontSize: 8, color: labelLight }}>{s.label}</span>
                  <span style={{ fontSize: 8, color: muted }}>{Math.round(s.v)}%</span>
                </div>
                <div style={{ height: 4, background: "#1a1a2e" }}>
                  <div style={{ height: "100%", width: `${s.v}%`, background: accent, transition: "width 0.5s" }} />
                </div>
              </div>
            ))}
          </div>

          {/* Next scan */}
          <div>
            <div style={{ fontSize: 8, color: dim, letterSpacing: "0.12em" }}>NEXT SCAN</div>
            <div style={{ fontSize: 14, color: accent, fontFamily: "Courier New", marginTop: 2 }}>{nextScanCountdown(t)}</div>
            <div style={{ fontSize: 8, color: dim }}>DAILY 08:00 ET</div>
          </div>

          {/* Watchlist */}
          <div>
            <div style={{ fontSize: 8, color: dim, letterSpacing: "0.12em", marginBottom: 6 }}>WATCHLIST · {watchlist.length}</div>
            <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
              <input data-testid="watchlist-input" value={tInput} onChange={e => setTInput(e.target.value.toUpperCase())}
                onKeyDown={e => e.key === "Enter" && addWatch()}
                placeholder="TICKER" style={{
                  flex: 1, background: "transparent", border: "none", borderBottom: "0.5px solid rgba(255,255,255,0.1)",
                  color: "#fff", fontSize: 9, fontFamily: "Courier New", outline: "none", padding: "3px 0",
                }} />
              <button data-testid="watchlist-add-btn" onClick={addWatch}
                style={{ background: "transparent", border: "none", color: accent, fontSize: 9, cursor: "pointer" }}>+ ADD</button>
            </div>
            {watchlist.slice(0, 8).map(w => (
              <div key={w.ticker} style={{ display: "flex", justifyContent: "space-between", padding: "2px 0", fontSize: 9 }}>
                <span style={{ color: muted }}>${w.ticker}</span>
                <button onClick={() => removeWatch(w.ticker)} style={{ background: "transparent", border: "none", color: dim, cursor: "pointer", fontSize: 9 }}>×</button>
              </div>
            ))}
          </div>

          {/* Alerts */}
          <div>
            <div style={{ fontSize: 8, color: dim, letterSpacing: "0.12em", marginBottom: 6 }}>PRICE ALERTS · {alerts.length}</div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr auto", gap: 4, marginBottom: 6 }}>
              <input data-testid="alert-ticker-input" value={aTicker} onChange={e => setATicker(e.target.value.toUpperCase())}
                placeholder="TICK" style={{ background: "transparent", border: "none", borderBottom: "0.5px solid rgba(255,255,255,0.1)", color: "#fff", fontSize: 9, fontFamily: "Courier New", outline: "none", padding: "3px 0" }} />
              <input data-testid="alert-price-input" value={aPrice} onChange={e => setAPrice(e.target.value)}
                placeholder="$" style={{ background: "transparent", border: "none", borderBottom: "0.5px solid rgba(255,255,255,0.1)", color: "#fff", fontSize: 9, fontFamily: "Courier New", outline: "none", padding: "3px 0" }} />
              <button data-testid="alert-add-btn" onClick={addAlert}
                style={{ background: "transparent", border: "none", color: accent, fontSize: 9, cursor: "pointer" }}>+</button>
            </div>
            {alerts.slice(0, 6).map(a => (
              <div key={`${a.ticker}-${a.created_at}`} style={{ display: "flex", justifyContent: "space-between", padding: "2px 0", fontSize: 9 }}>
                <span style={{ color: muted }}>${a.ticker} @ ${a.target_price}</span>
                <button onClick={() => removeAlert(a.ticker)} style={{ background: "transparent", border: "none", color: dim, cursor: "pointer", fontSize: 9 }}>×</button>
              </div>
            ))}
          </div>

          {/* Quick commands (push to bottom) */}
          <div style={{ marginTop: "auto" }}>
            <div style={{ fontSize: 9, color: dim, letterSpacing: "0.14em", marginBottom: 8 }}>NAVIGATION</div>
            {[
              { to: "/performance", label: "[PERFORMANCE]" },
              { to: "/learning",    label: "[LEARNING]" },
              { to: "/settings",    label: "[SETTINGS]" },
            ].map(n => (
              <Link key={n.to} to={n.to} data-testid={`sidebar-nav-${n.label.replace(/[\[\]]/g, "").toLowerCase()}`} style={{
                display: "block", fontSize: 11, color: accent, padding: "4px 0",
                textDecoration: "none", letterSpacing: "0.08em", fontWeight: 700,
              }}>{n.label}</Link>
            ))}
            <div style={{ fontSize: 9, color: dim, letterSpacing: "0.14em", marginTop: 14, marginBottom: 8 }}>COMMANDS</div>
            {["/scan", "/scan_gov", "/contracts", "/congress", "/options", "/flow", "/iv", "/backtest", "/help"].map(c => (
              <div key={c} onClick={() => fireToast(c)} className="hover:text-amber-400" style={{
                fontSize: 10, color: labelLight, cursor: "pointer", padding: "3px 0", transition: "color 0.15s", letterSpacing: "0.04em",
              }}>{c}</div>
            ))}
          </div>
        </aside>

        {/* === MAIN === */}
        <main style={{ overflowY: "auto", display: "flex", flexDirection: "column" }}>
          {/* Metrics */}
          <div style={{ background: cardBg, borderBottom: hairline, display: "grid", gridTemplateColumns: "repeat(7, 1fr)" }}>
            {[
              { label: "PRE-FILTER UNIVERSE", v: scan?.universe_size || "—", sub: "S&P 500 · NASDAQ · RUSSELL 2K", color: accent },
              { label: "TARGETS ACQUIRED", v: scan?.pre_filter_passed || 0, sub: `${scan?.results?.length || 0} ANALYZED`, color: "#fff" },
              { label: "INSIDER SIGNALS", v: counts.insider, sub: "OPENINSIDER", color: accent },
              { label: "SHORT SIGNALS", v: counts.short, sub: "FINVIZ", color: accent },
              { label: "GOV SIGNALS", v: counts.gov, sub: "USASPENDING", color: accent },
              { label: "CACHE SAVED", v: scan?.claude_cache_hits || 0, sub: `${scan?.claude_calls_made || 0} BATCHED CALLS`, color: muted },
              { label: "UPLINK", v: status?.bot?.telegram_configured ? "LIVE" : "OFF", sub: "TELEGRAM CONNECTED", color: status?.bot?.telegram_configured ? "#4ade80" : "#fb923c", isText: true },
            ].map((c, i) => (
              <div key={i} data-testid={`metric-${i}`} style={{ padding: "16px 18px", borderRight: i < 6 ? hairline : "none" }}>
                <div style={{ fontSize: 10, color: dim, letterSpacing: "0.14em" }}>{c.label}</div>
                <div style={{ fontSize: c.isText ? 22 : 26, fontWeight: 300, color: c.color, marginTop: 5, fontFamily: "Courier New", letterSpacing: "0.02em" }}>{c.v}</div>
                <div style={{ fontSize: 10, color: dim, marginTop: 3, letterSpacing: "0.08em" }}>{c.sub}</div>
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
            <span style={{ fontSize: 8, color: accent, letterSpacing: "0.1em" }}>SHOWING {results.length} OF {scan?.pre_filter_passed || 0} TARGETS</span>
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

          {/* Stock rows */}
          {results.length === 0 && (
            <div style={{ padding: 40, textAlign: "center", color: dim, fontSize: 11 }}>
              NO TARGETS ACQUIRED — RUN SCAN TO BEGIN
            </div>
          )}
          {results.map((r, idx) => {
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
                    {r.learning_score != null && <span>AXIOM <span style={{ color: accent, fontWeight: 700 }}>{r.learning_score}</span></span>}
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
              </div>
            );
          })}

          {/* Collapsed panels */}
          <CollapsiblePanel
            testid="contracts-section"
            title={`GOVERNMENT CONTRACT FEED · ${contracts.length}`}
            isOpen={openPanel === "contracts"}
            onToggle={() => setOpenPanel(openPanel === "contracts" ? null : "contracts")}
          >
            {contracts.map((c, i) => (
              <div key={i} data-testid={`contract-row-${c.ticker}`} style={{
                display: "grid", gridTemplateColumns: "60px 1fr 100px",
                padding: "8px 20px", borderBottom: hairlineLight, fontSize: 9,
              }}>
                <span style={{ color: accent, fontWeight: 700 }}>${c.ticker}</span>
                <span style={{ color: muted, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.agency}</span>
                <span style={{ color: "#4ade80", textAlign: "right" }}>{fmtAmt(c.amount)}</span>
              </div>
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
              <span>MODEL CLAUDE-SONNET-4-5</span>
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
        </main>
      </div>
    </>
  );
}

function CollapsiblePanel({ title, children, isOpen, onToggle, testid }) {
  return (
    <div data-testid={testid} style={{ borderBottom: hairlineLight }}>
      <div onClick={onToggle} style={{
        padding: "12px 20px", display: "flex", justifyContent: "space-between", alignItems: "center",
        cursor: "pointer", borderBottom: isOpen ? hairlineLight : "none",
        background: isOpen ? cardBg : "transparent",
      }}>
        <span style={{ fontSize: 8, color: dim, letterSpacing: "0.14em" }}>{title}</span>
        <div style={{ flex: 1, height: 1, margin: "0 16px", background: "rgba(200,168,75,0.15)" }} />
        <span style={{
          fontSize: 10, color: accent, transform: `rotate(${isOpen ? 90 : 0}deg)`,
          transition: "transform 0.2s", display: "inline-block",
        }}>▶</span>
      </div>
      {isOpen && <div style={{ background: cardBg }}>{children}</div>}
    </div>
  );
}
