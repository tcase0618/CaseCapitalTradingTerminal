import { useEffect, useMemo, useState, useCallback } from "react";
import axios from "axios";
import { toast } from "sonner";
import {
  Activity,
  RefreshCw,
  Zap,
  PlayCircle,
  Plus,
  Trash2,
  Bell,
  Eye,
  TrendingUp,
  Database,
  Radio,
  Send,
  ChevronRight,
  Landmark,
  Building2,
  AlertTriangle,
  Target,
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
const cls = (...x) => x.filter(Boolean).join(" ");

const scoreColor = (s) => {
  if (s >= 9) return "text-green-400 bg-green-500/10 border-green-500/40";
  if (s >= 7) return "text-green-500 bg-green-500/10 border-green-500/30";
  if (s >= 5) return "text-amber-500 bg-amber-500/10 border-amber-500/30";
  return "text-slate-400 bg-slate-800 border-slate-700";
};

const SIG_META = {
  insider_cluster_buy: { label: "INSIDER", color: "text-indigo-300 bg-indigo-500/10 border-indigo-500/30" },
  high_short_interest: { label: "SHORT", color: "text-red-400 bg-red-500/10 border-red-500/30" },
  upcoming_earnings: { label: "EARNINGS", color: "text-blue-400 bg-blue-500/10 border-blue-500/30" },
  CONTRACT_SURGE: { label: "CONTRACT_SURGE", color: "text-amber-300 bg-amber-500/10 border-amber-500/40" },
  NEW_WINNER: { label: "NEW_WINNER", color: "text-yellow-300 bg-yellow-500/10 border-yellow-500/40" },
  CONCENTRATION_WIN: { label: "CONCENTRATION_WIN", color: "text-orange-300 bg-orange-500/10 border-orange-500/40" },
  MOMENTUM_STACK: { label: "MOMENTUM_STACK", color: "text-amber-400 bg-amber-500/10 border-amber-500/40" },
  BUDGET_SURGE: { label: "BUDGET_SURGE", color: "text-yellow-400 bg-yellow-500/10 border-yellow-500/40" },
  CONGRESSIONAL_BUY: { label: "CONGRESS_BUY", color: "text-yellow-200 bg-yellow-500/15 border-yellow-400/50" },
  PRE_AWARD: { label: "PRE_AWARD", color: "text-amber-200 bg-amber-500/10 border-amber-400/40" },
  SUB_BENEFICIARY: { label: "SUB_BENEFICIARY", color: "text-orange-200 bg-orange-500/10 border-orange-400/40" },
};

const squeezeColor = (s) => {
  if (s == null) return "text-slate-500 bg-slate-800 border-slate-700";
  if (s >= 86) return "text-red-300 bg-red-500/15 border-red-500/50 animate-pulse";
  if (s >= 66) return "text-orange-300 bg-orange-500/10 border-orange-500/40";
  if (s >= 41) return "text-yellow-300 bg-yellow-500/10 border-yellow-500/40";
  return "text-slate-400 bg-slate-800 border-slate-700";
};

const RISK_BG = {
  LOW: "text-green-400 bg-green-500/10 border-green-500/30",
  MEDIUM: "text-amber-400 bg-amber-500/10 border-amber-500/30",
  HIGH: "text-red-400 bg-red-500/10 border-red-500/30",
  EXTREME: "text-red-300 bg-red-500/20 border-red-500/50",
};

const fmtMoney = (v) => (v == null || v === "" ? "—" : `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`);
const fmtPct = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${Number(v).toFixed(1)}%`);
const fmtAmt = (v) => (v == null ? "—" : `$${(Number(v) / 1e6).toFixed(1)}M`);

function Section({ title, right, children, testid }) {
  return (
    <div data-testid={testid} className="border border-slate-800 bg-slate-900/40">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2.5">
        <h3 className="font-mono text-[11px] uppercase tracking-[0.25em] text-slate-400">{title}</h3>
        {right}
      </div>
      <div>{children}</div>
    </div>
  );
}

function StatTile({ label, value, sub, icon: Icon, accent = "text-slate-50", testid }) {
  return (
    <div data-testid={testid} className="border border-slate-800 bg-slate-900/40 p-4 hover:bg-slate-900 transition-colors">
      <div className="flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-slate-500">{label}</div>
        {Icon && <Icon className="h-3.5 w-3.5 text-slate-600" />}
      </div>
      <div className={cls("mt-3 font-mono text-3xl font-semibold tabular-nums", accent)}>{value}</div>
      {sub && <div className="mt-1 font-mono text-[11px] text-slate-500">{sub}</div>}
    </div>
  );
}

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
  const [scanning, setScanning] = useState(false);
  const [govScanning, setGovScanning] = useState(false);

  const [tInput, setTInput] = useState("");
  const [aTicker, setATicker] = useState("");
  const [aPrice, setAPrice] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [s, sc, ac, wl, al, ct, cg, sq, fy] = await Promise.all([
        axios.get(`${API}/status`),
        axios.get(`${API}/scan/latest`),
        axios.get(`${API}/activity?limit=30`),
        axios.get(`${API}/watchlist`),
        axios.get(`${API}/alerts`),
        axios.get(`${API}/contracts?limit=5`),
        axios.get(`${API}/congress/recent?days=30`),
        axios.get(`${API}/squeeze/leaderboard/top?limit=10`),
        axios.get(`${API}/fy/status`),
      ]);
      setStatus(s.data);
      setScan(sc.data);
      setActivity(ac.data);
      setWatchlist(wl.data);
      setAlerts(al.data);
      setContracts(ct.data);
      setCongress(cg.data);
      setSqueezeLb(sq.data);
      setFyStatus(fy.data);
    } catch (e) {
      console.error(e);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 15000);
    return () => clearInterval(id);
  }, [refresh]);

  const runScan = async () => {
    setScanning(true);
    toast.message("SCAN INITIATED", { description: "Fetching market + gov signals..." });
    try {
      const { data } = await axios.post(`${API}/scan/run`);
      setScan(data);
      toast.success("SCAN COMPLETE", {
        description: `${data.results?.length || 0} results · ${data.claude_calls_made} Claude call · ${data.claude_cache_hits} cached`,
      });
      refresh();
    } catch (e) {
      toast.error("SCAN FAILED", { description: e?.response?.data?.detail || e.message });
    } finally {
      setScanning(false);
    }
  };

  const runGovScan = async () => {
    setGovScanning(true);
    try {
      const { data } = await axios.post(`${API}/scan/gov`);
      toast.success("GOV SCAN COMPLETE", { description: `${data.results?.length || 0} public-co hits` });
      refresh();
    } catch (e) {
      toast.error("GOV SCAN FAILED");
    } finally {
      setGovScanning(false);
    }
  };

  const addWatch = async () => {
    if (!tInput.trim()) return;
    try {
      await axios.post(`${API}/watchlist`, { ticker: tInput.trim() });
      setTInput("");
      toast.success(`ADDED $${tInput.toUpperCase()}`);
      refresh();
    } catch { toast.error("ADD FAILED"); }
  };
  const removeWatch = async (t) => { await axios.delete(`${API}/watchlist/${t}`); refresh(); };
  const addAlert = async () => {
    if (!aTicker.trim() || !aPrice) return;
    try {
      await axios.post(`${API}/alerts`, { ticker: aTicker.trim(), target_price: parseFloat(aPrice) });
      setATicker(""); setAPrice("");
      toast.success("ALERT SET"); refresh();
    } catch { toast.error("ALERT FAILED"); }
  };
  const removeAlert = async (t) => { await axios.delete(`${API}/alerts/${t}`); refresh(); };

  const formatTime = (iso) => {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString("en-US", {
        month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      });
    } catch { return iso; }
  };

  const tickersInScan = scan?.pre_filter_passed ?? 0;
  const callsSaved = useMemo(() => (status?.stats?.cached_analyses_today ?? 0), [status]);
  const webhookOk = status?.bot?.telegram_configured && status?.webhook_url;
  const budgetSurges = scan?.budget_surges || [];

  return (
    <div className="min-h-screen terminal-grid">
      {/* HEADER */}
      <header className="border-b border-slate-800 bg-slate-950/95 backdrop-blur sticky top-0 z-20">
        <div className="mx-auto max-w-[1600px] px-6 py-4 flex items-center justify-between gap-6">
          <div className="flex items-center gap-4">
            <div className="h-9 w-9 border border-slate-800 bg-slate-900 flex items-center justify-center">
              <Radio className="h-4 w-4 text-green-500" />
            </div>
            <div>
              <div className="font-mono text-sm tracking-[0.3em] uppercase text-slate-50">STOCK_INTEL_BOT</div>
              <div className="font-mono text-[10px] tracking-[0.2em] uppercase text-slate-500">
                Daily 8 AM ET · Insider · Short · Earnings · Gov · Claude
              </div>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="hidden md:flex items-center gap-2.5">
              <span className="status-dot" />
              <span className="font-mono text-[11px] uppercase tracking-[0.25em] text-green-400">BOT ONLINE</span>
            </div>
            <div className="hidden md:block font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
              LAST SCAN&nbsp;<span className="text-slate-300">{formatTime(status?.last_scan_at)}</span>
            </div>

            <button
              data-testid="run-gov-scan-button"
              onClick={runGovScan}
              disabled={govScanning}
              className="flex items-center gap-2 border px-3 py-2 font-mono text-[11px] uppercase tracking-[0.25em] border-amber-500/40 bg-amber-500/10 text-amber-300 hover:bg-amber-500 hover:text-slate-950 transition-colors disabled:opacity-50"
            >
              {govScanning ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <Landmark className="h-3.5 w-3.5" />}
              {govScanning ? "RUNNING" : "GOV SCAN"}
            </button>

            <button
              data-testid="run-scan-button"
              onClick={runScan}
              disabled={scanning}
              className="flex items-center gap-2 border px-4 py-2 font-mono text-[11px] uppercase tracking-[0.25em] border-green-500/50 bg-green-500/10 text-green-400 hover:bg-green-500 hover:text-slate-950 transition-colors disabled:opacity-50"
            >
              {scanning ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
              {scanning ? "RUNNING" : "RUN SCAN NOW"}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 py-6 space-y-6">
        {/* FY BANNER */}
        {fyStatus.fy_multiplier_active && (
          <div data-testid="fy-banner" className="border border-amber-500/40 bg-amber-500/10 px-4 py-2.5 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <Building2 className="h-4 w-4 text-amber-400" />
              <span className="font-mono text-xs uppercase tracking-[0.25em] text-amber-300">
                GOV FISCAL YEAR-END · GOV CONTRACT SIGNALS WEIGHTED 1.5x
              </span>
            </div>
            <span className="font-mono text-[11px] text-amber-400">
              {fyStatus.days_to_fy_end}d to FY end
            </span>
          </div>
        )}

        {/* TOP STATS */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatTile
            testid="stat-tickers"
            label="Tickers Passed"
            value={tickersInScan}
            sub={`From ${scan?.raw_counts?.insider_clusters || 0} ins · ${scan?.raw_counts?.high_short_interest || 0} sh · ${scan?.raw_counts?.upcoming_earnings || 0} ern · ${scan?.raw_counts?.gov_public_tickers || 0} gov`}
            icon={TrendingUp}
            accent="text-slate-50"
          />
          <StatTile
            testid="stat-cache"
            label="Calls Saved (Cache)"
            value={callsSaved}
            sub={`${scan?.claude_calls_made || 0} batched call · ${scan?.claude_cache_hits || 0} hits`}
            icon={Database}
            accent="text-indigo-300"
          />
          <StatTile
            testid="stat-signals"
            label="Signals Fired"
            value={scan?.results?.length || 0}
            sub="Passed Claude analysis"
            icon={Zap}
            accent="text-green-400"
          />
          <StatTile
            testid="stat-webhook"
            label="Webhook"
            value={webhookOk ? "LIVE" : status?.bot?.telegram_configured ? "PENDING" : "UNCFG"}
            sub={status?.bot?.telegram_configured ? (status?.webhook_url || "register pending") : "TELEGRAM_BOT_TOKEN missing"}
            icon={Send}
            accent={webhookOk ? "text-green-400" : "text-amber-400"}
          />
        </div>

        {/* MAIN GRID */}
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          <div className="lg:col-span-3">
            <Section
              testid="scan-results-section"
              title={`LATEST SCAN RESULTS · ${scan?.results?.length || 0}`}
              right={
                <span className="font-mono text-[10px] uppercase tracking-[0.2em] text-slate-500">
                  {scan?.finished_at ? formatTime(scan.finished_at) : "no scan yet"}
                </span>
              }
            >
              {!scan?.results?.length ? (
                <div className="p-8 text-center font-mono text-xs text-slate-500">
                  No scan results yet. Click <span className="text-green-400">RUN SCAN NOW</span> to start.
                </div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-slate-800 text-[10px] uppercase tracking-[0.2em] text-slate-500">
                        <th className="px-4 py-2.5 text-left font-mono">Ticker</th>
                        <th className="px-3 py-2.5 text-left font-mono">Score</th>
                        <th className="px-3 py-2.5 text-left font-mono">Risk</th>
                        <th className="px-3 py-2.5 text-left font-mono">Squeeze</th>
                        <th className="px-3 py-2.5 text-left font-mono">Signals</th>
                        <th className="px-3 py-2.5 text-left font-mono">Thesis</th>
                        <th className="px-3 py-2.5 text-left font-mono">Entry</th>
                        <th className="px-3 py-2.5 text-left font-mono">Target</th>
                        <th className="px-3 py-2.5 text-left font-mono">Time</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scan.results.map((r, i) => {
                        const risk = r.risk || {};
                        const tg = r.targets || {};
                        const sq = r.squeeze || {};
                        const tt = r.time_target || {};
                        return (
                          <tr
                            key={r.ticker}
                            data-testid={`result-row-${r.ticker}`}
                            className={cls("border-b border-slate-900 hover:bg-slate-900", i === 0 && "row-flash")}
                          >
                            <td className="px-4 py-3 font-mono font-semibold text-slate-50 align-top">
                              ${r.ticker}
                              {r.cached && <span className="ml-2 font-mono text-[9px] uppercase text-slate-600">cache</span>}
                              {r.price != null && (
                                <div className="font-mono text-[10px] text-slate-500">{fmtMoney(r.price)}</div>
                              )}
                            </td>
                            <td className="px-3 py-3 align-top">
                              <span className={cls("inline-flex items-center px-2 py-0.5 border font-mono text-xs", scoreColor(r.signal_score))}>
                                {r.signal_score}/10
                              </span>
                              {r.fy_multiplier_applied && (
                                <div className="mt-1 font-mono text-[8px] text-amber-400">FY×1.5</div>
                              )}
                            </td>
                            <td className="px-3 py-3 align-top">
                              <span className={cls("inline-flex items-center gap-1 px-2 py-0.5 border font-mono text-[10px]", RISK_BG[risk.level] || RISK_BG.MEDIUM)}>
                                {risk.emoji} {risk.level || "?"}
                              </span>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <span className={cls("inline-flex items-center px-2 py-0.5 border font-mono text-[10px] tabular-nums", squeezeColor(sq.score))}>
                                {sq.score != null ? `${sq.score}/100` : "—"}
                              </span>
                            </td>
                            <td className="px-3 py-3 align-top">
                              <div className="flex flex-wrap gap-1 max-w-[200px]">
                                {(r.signals || []).map((s) => {
                                  const m = SIG_META[s] || { label: s, color: "text-slate-400 bg-slate-800 border-slate-700" };
                                  return (
                                    <span key={s} className={cls("inline-flex items-center px-1.5 py-0.5 border font-mono text-[9px] tracking-wider", m.color)}>
                                      {m.label}
                                    </span>
                                  );
                                })}
                              </div>
                            </td>
                            <td className="px-3 py-3 max-w-md text-slate-300 leading-snug align-top">
                              <div>{r.thesis}</div>
                              {r.conviction && (
                                <div className="font-mono text-[9px] text-slate-500 mt-1 uppercase tracking-wider">
                                  {r.conviction} · {r.time_horizon}
                                </div>
                              )}
                            </td>
                            <td className="px-3 py-3 font-mono text-xs text-slate-300 align-top whitespace-nowrap">
                              {r.entry_low != null && r.entry_high != null
                                ? `${fmtMoney(r.entry_low)}–${fmtMoney(r.entry_high)}`
                                : "—"}
                            </td>
                            <td className="px-3 py-3 font-mono text-xs align-top whitespace-nowrap">
                              {tg.target_blended != null ? (
                                <>
                                  <div className={cls(
                                    Math.abs(tg.upside_blended || 0) > 100 ? "text-amber-400" : "text-green-400"
                                  )}>{fmtMoney(tg.target_blended)}</div>
                                  <div className={cls(
                                    "text-[10px]",
                                    Math.abs(tg.upside_blended || 0) > 100 ? "text-amber-500/70" : "text-green-500/70"
                                  )}>
                                    {fmtPct(tg.upside_blended)}
                                    {Math.abs(tg.upside_blended || 0) > 100 && (
                                      <AlertTriangle className="h-2.5 w-2.5 inline ml-1" />
                                    )}
                                  </div>
                                </>
                              ) : "—"}
                            </td>
                            <td className="px-3 py-3 font-mono text-xs text-amber-400 align-top whitespace-nowrap">
                              {tt.target_date ? (
                                <>
                                  <div>{tt.target_date}</div>
                                  <div className="text-[10px] text-amber-500/70">{tt.days_remaining}d</div>
                                </>
                              ) : (r.catalyst_date || "—")}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </Section>
          </div>

          {/* Right column: Watchlist + Alerts */}
          <div className="space-y-4">
            <Section
              testid="watchlist-section"
              title={`WATCHLIST · ${watchlist.length}`}
              right={<Eye className="h-3.5 w-3.5 text-slate-500" />}
            >
              <div className="p-3 border-b border-slate-800 flex gap-2">
                <input
                  data-testid="watchlist-input"
                  value={tInput}
                  onChange={(e) => setTInput(e.target.value.toUpperCase())}
                  onKeyDown={(e) => e.key === "Enter" && addWatch()}
                  placeholder="TICKER"
                  className="flex-1 bg-slate-950 border border-slate-800 px-2.5 py-1.5 font-mono text-xs text-slate-50 focus:outline-none focus:border-green-500/60"
                />
                <button data-testid="watchlist-add-btn" onClick={addWatch}
                  className="border border-slate-700 px-2.5 py-1.5 hover:bg-slate-800 hover:text-green-400 transition-colors">
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="max-h-72 overflow-y-auto">
                {!watchlist.length && <div className="p-4 font-mono text-xs text-slate-600 text-center">empty</div>}
                {watchlist.map((w) => (
                  <div key={w.ticker} data-testid={`watch-row-${w.ticker}`}
                    className="flex items-center justify-between px-4 py-2.5 border-b border-slate-900 hover:bg-slate-900">
                    <div>
                      <div className="font-mono text-sm font-semibold">${w.ticker}</div>
                      <div className="font-mono text-[10px] text-slate-500 truncate max-w-[150px]">{w.name || ""}</div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs text-slate-300 tabular-nums">{w.price != null ? `$${w.price}` : "—"}</span>
                      <button onClick={() => removeWatch(w.ticker)} className="text-slate-600 hover:text-red-500">
                        <Trash2 className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </Section>

            <Section
              testid="alerts-section"
              title={`PRICE ALERTS · ${alerts.length}`}
              right={<Bell className="h-3.5 w-3.5 text-slate-500" />}
            >
              <div className="p-3 border-b border-slate-800 grid grid-cols-[1fr_1fr_auto] gap-2">
                <input data-testid="alert-ticker-input" value={aTicker} onChange={(e) => setATicker(e.target.value.toUpperCase())}
                  placeholder="TICKER" className="bg-slate-950 border border-slate-800 px-2.5 py-1.5 font-mono text-xs focus:outline-none focus:border-green-500/60" />
                <input data-testid="alert-price-input" value={aPrice} onChange={(e) => setAPrice(e.target.value)}
                  placeholder="PRICE" className="bg-slate-950 border border-slate-800 px-2.5 py-1.5 font-mono text-xs focus:outline-none focus:border-green-500/60" />
                <button data-testid="alert-add-btn" onClick={addAlert}
                  className="border border-slate-700 px-2.5 py-1.5 hover:bg-slate-800 hover:text-green-400 transition-colors">
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="max-h-60 overflow-y-auto">
                {!alerts.length && <div className="p-4 font-mono text-xs text-slate-600 text-center">no alerts</div>}
                {alerts.map((a) => (
                  <div key={`${a.ticker}-${a.created_at}`} className="flex items-center justify-between px-4 py-2.5 border-b border-slate-900 hover:bg-slate-900">
                    <div className="font-mono text-sm">
                      <span className="font-semibold">${a.ticker}</span>{" "}
                      <span className="text-slate-500">@</span>{" "}
                      <span className="text-amber-400 tabular-nums">${a.target_price}</span>
                    </div>
                    <button onClick={() => removeAlert(a.ticker)} className="text-slate-600 hover:text-red-500">
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </Section>
          </div>
        </div>

        {/* GOV CONTRACTS + CONGRESS + SQUEEZE PANELS */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          <Section
            testid="contracts-section"
            title={`GOV CONTRACTS · TOP ${contracts.length}`}
            right={<Landmark className="h-3.5 w-3.5 text-amber-400" />}
          >
            {!contracts.length ? (
              <div className="p-4 font-mono text-xs text-slate-600 text-center">no contracts in last 14d</div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full text-sm">
                  <thead>
                    <tr className="border-b border-slate-800 text-[10px] uppercase tracking-[0.2em] text-slate-500">
                      <th className="px-3 py-2 text-left font-mono">Ticker</th>
                      <th className="px-3 py-2 text-left font-mono">Agency</th>
                      <th className="px-3 py-2 text-right font-mono">Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {contracts.map((c, i) => (
                      <tr key={i} data-testid={`contract-row-${c.ticker}`} className="border-b border-slate-900 hover:bg-slate-900">
                        <td className="px-3 py-2 font-mono text-sm font-semibold text-amber-300">${c.ticker}</td>
                        <td className="px-3 py-2 text-xs text-slate-400 max-w-[180px] truncate">{c.agency}</td>
                        <td className="px-3 py-2 font-mono text-xs text-right text-green-400 tabular-nums">{fmtAmt(c.amount)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <Section
            testid="congress-section"
            title={`CONGRESSIONAL BUYS · ${congress.length}`}
            right={<Landmark className="h-3.5 w-3.5 text-yellow-400" />}
          >
            {!congress.length ? (
              <div className="p-4 font-mono text-xs text-slate-600 text-center">no recent buys</div>
            ) : (
              <div className="max-h-72 overflow-y-auto">
                {congress.slice(0, 10).map((c, i) => (
                  <div key={i} data-testid={`congress-row-${c.ticker}`} className="px-3 py-2 border-b border-slate-900 hover:bg-slate-900">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-sm font-semibold text-yellow-300">${c.ticker}</span>
                        {c.committee_match && (
                          <span className="font-mono text-[9px] uppercase tracking-wider text-amber-400 border border-amber-400/40 px-1">
                            +3pts match
                          </span>
                        )}
                      </div>
                      <span className="font-mono text-[10px] text-slate-500">{c.tx_date}</span>
                    </div>
                    <div className="font-mono text-[11px] text-slate-400 mt-0.5">
                      {c.name} <span className="text-slate-600">({c.chamber})</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Section>

          <Section
            testid="squeeze-leaderboard-section"
            title={`SQUEEZE LEADERBOARD · TOP ${squeezeLb.length}`}
            right={<Target className="h-3.5 w-3.5 text-orange-400" />}
          >
            {!squeezeLb.length ? (
              <div className="p-4 font-mono text-xs text-slate-600 text-center">no squeeze data</div>
            ) : (
              <div className="max-h-72 overflow-y-auto">
                {squeezeLb.map((s, i) => (
                  <div key={i} data-testid={`squeeze-row-${s.ticker}`} className="px-3 py-2 border-b border-slate-900 hover:bg-slate-900 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-[10px] text-slate-600">#{i + 1}</span>
                      <span className="font-mono text-sm font-semibold text-slate-200">${s.ticker}</span>
                    </div>
                    <span className={cls("inline-flex items-center px-2 py-0.5 border font-mono text-xs tabular-nums", squeezeColor(s.score))}>
                      {s.score}/100
                    </span>
                  </div>
                ))}
              </div>
            )}
          </Section>
        </div>

        {/* AGENCY BUDGET TRACKER (full width) */}
        <Section
          testid="budget-surges-section"
          title="AGENCY BUDGET TRACKER"
          right={<Building2 className="h-3.5 w-3.5 text-amber-400" />}
        >
          {!budgetSurges.length ? (
            <div className="p-4 font-mono text-xs text-slate-600 text-center">
              no budget surges detected this scan
            </div>
          ) : (
            <div>
              {budgetSurges.slice(0, 5).map((b, i) => (
                <div key={i} className="px-4 py-3 border-b border-slate-900 hover:bg-slate-900">
                  <div className="flex items-center justify-between">
                    <div className="font-mono text-sm text-slate-200 truncate max-w-[400px]">{b.agency}</div>
                    <div className="font-mono text-sm text-amber-400">+{b.pct_increase}%</div>
                  </div>
                  <div className="mt-1 font-mono text-[10px] text-slate-500">
                    Exposed: {(b.exposed_tickers || []).map(t => `$${t}`).join(" · ") || "—"}
                  </div>
                </div>
              ))}
            </div>
          )}
        </Section>

        {/* ACTIVITY LOG */}
        <Section testid="activity-section" title="ACTIVITY LOG" right={<Activity className="h-3.5 w-3.5 text-slate-500" />}>
          <div className="bg-black/40 max-h-72 overflow-y-auto font-mono text-xs">
            {!activity.length && <div className="p-4 text-slate-600 text-center">no activity yet</div>}
            {activity.map((a, i) => (
              <div key={i} className="px-4 py-1.5 flex items-start gap-3 border-b border-slate-900/50 hover:bg-slate-900/40">
                <span className="text-slate-600 shrink-0">{formatTime(a.ts)}</span>
                <ChevronRight className="h-3 w-3 text-slate-700 shrink-0 mt-0.5" />
                <span className={cls("shrink-0 uppercase tracking-wider w-16",
                  a.level === "success" && "text-green-500",
                  a.level === "info" && "text-slate-400",
                  a.level === "error" && "text-red-500")}>
                  {a.level}
                </span>
                <span className="text-slate-300 break-all">{a.message}</span>
              </div>
            ))}
          </div>
        </Section>

        <footer className="pt-4 pb-8 font-mono text-[10px] uppercase tracking-[0.25em] text-slate-600 flex items-center justify-between">
          <span>STOCK_INTEL_BOT · v3.0 · USASpending + Congress + Squeeze</span>
          <span>{status?.bot?.claude_configured ? "CLAUDE ✓" : "CLAUDE ✗"} · {status?.bot?.telegram_configured ? "TG ✓" : "TG ✗"}</span>
        </footer>
      </main>
    </div>
  );
}
