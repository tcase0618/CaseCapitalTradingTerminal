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
} from "lucide-react";

const API = `${process.env.REACT_APP_BACKEND_URL}/api`;

const cls = (...x) => x.filter(Boolean).join(" ");

const scoreColor = (s) => {
  if (s >= 9) return "text-green-400 bg-green-500/10 border-green-500/40";
  if (s >= 7) return "text-green-500 bg-green-500/10 border-green-500/30";
  if (s >= 5) return "text-amber-500 bg-amber-500/10 border-amber-500/30";
  return "text-slate-400 bg-slate-800 border-slate-700";
};

const signalLabel = (s) => {
  const map = {
    insider_cluster_buy: { label: "INSIDER", color: "text-indigo-300 bg-indigo-500/10 border-indigo-500/30" },
    high_short_interest: { label: "SHORT", color: "text-red-400 bg-red-500/10 border-red-500/30" },
    upcoming_earnings: { label: "EARNINGS", color: "text-amber-400 bg-amber-500/10 border-amber-500/30" },
  };
  return map[s] || { label: s, color: "text-slate-400 bg-slate-800 border-slate-700" };
};

function Section({ title, right, children, testid }) {
  return (
    <div data-testid={testid} className="border border-slate-800 bg-slate-900/40">
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-2.5">
        <h3 className="font-mono text-[11px] uppercase tracking-[0.25em] text-slate-400">
          {title}
        </h3>
        {right}
      </div>
      <div>{children}</div>
    </div>
  );
}

function StatTile({ label, value, sub, icon: Icon, accent = "text-slate-50", testid }) {
  return (
    <div
      data-testid={testid}
      className="border border-slate-800 bg-slate-900/40 p-4 hover:bg-slate-900 transition-colors"
    >
      <div className="flex items-center justify-between">
        <div className="font-mono text-[10px] uppercase tracking-[0.25em] text-slate-500">
          {label}
        </div>
        {Icon && <Icon className="h-3.5 w-3.5 text-slate-600" />}
      </div>
      <div className={cls("mt-3 font-mono text-3xl font-semibold tabular-nums", accent)}>
        {value}
      </div>
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
  const [scanning, setScanning] = useState(false);

  const [tInput, setTInput] = useState("");
  const [aTicker, setATicker] = useState("");
  const [aPrice, setAPrice] = useState("");

  const refresh = useCallback(async () => {
    try {
      const [s, sc, ac, wl, al] = await Promise.all([
        axios.get(`${API}/status`),
        axios.get(`${API}/scan/latest`),
        axios.get(`${API}/activity?limit=30`),
        axios.get(`${API}/watchlist`),
        axios.get(`${API}/alerts`),
      ]);
      setStatus(s.data);
      setScan(sc.data);
      setActivity(ac.data);
      setWatchlist(wl.data);
      setAlerts(al.data);
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
    toast.message("SCAN INITIATED", { description: "Fetching insider, short, earnings…" });
    try {
      const { data } = await axios.post(`${API}/scan/run`);
      setScan(data);
      toast.success("SCAN COMPLETE", {
        description: `${data.results?.length || 0} results · ${data.claude_calls_made} fresh · ${data.claude_cache_hits} cached`,
      });
      refresh();
    } catch (e) {
      toast.error("SCAN FAILED", { description: e?.response?.data?.detail || e.message });
    } finally {
      setScanning(false);
    }
  };

  const addWatch = async () => {
    if (!tInput.trim()) return;
    try {
      await axios.post(`${API}/watchlist`, { ticker: tInput.trim() });
      setTInput("");
      toast.success(`ADDED $${tInput.toUpperCase()}`);
      refresh();
    } catch (e) {
      toast.error("ADD FAILED");
    }
  };

  const removeWatch = async (t) => {
    await axios.delete(`${API}/watchlist/${t}`);
    refresh();
  };

  const addAlert = async () => {
    if (!aTicker.trim() || !aPrice) return;
    try {
      await axios.post(`${API}/alerts`, { ticker: aTicker.trim(), target_price: parseFloat(aPrice) });
      setATicker("");
      setAPrice("");
      toast.success("ALERT SET");
      refresh();
    } catch (e) {
      toast.error("ALERT FAILED");
    }
  };

  const removeAlert = async (t) => {
    await axios.delete(`${API}/alerts/${t}`);
    refresh();
  };

  const formatTime = (iso) => {
    if (!iso) return "—";
    try {
      const d = new Date(iso);
      return d.toLocaleString("en-US", {
        month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
      });
    } catch { return iso; }
  };

  const tickersInScan = scan?.pre_filter_passed ?? 0;
  const fresh = scan?.claude_calls_made ?? 0;
  const cached = scan?.claude_cache_hits ?? 0;
  const callsSaved = useMemo(() => (status?.stats?.cached_analyses_today ?? 0), [status]);

  const webhookOk =
    status?.bot?.telegram_configured &&
    status?.webhook_url;

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
              <div className="font-mono text-sm tracking-[0.3em] uppercase text-slate-50">
                STOCK_INTEL_BOT
              </div>
              <div className="font-mono text-[10px] tracking-[0.2em] uppercase text-slate-500">
                Daily 8:00 AM ET · Insider · Short · Earnings · Claude
              </div>
            </div>
          </div>

          <div className="flex items-center gap-6">
            <div className="hidden md:flex items-center gap-2.5">
              <span className="status-dot" />
              <span className="font-mono text-[11px] uppercase tracking-[0.25em] text-green-400">
                BOT ONLINE
              </span>
            </div>

            <div className="hidden md:block font-mono text-[11px] uppercase tracking-[0.2em] text-slate-500">
              LAST SCAN&nbsp;
              <span className="text-slate-300">{formatTime(status?.last_scan_at)}</span>
            </div>

            <button
              data-testid="run-scan-button"
              onClick={runScan}
              disabled={scanning}
              className={cls(
                "group flex items-center gap-2 border px-4 py-2",
                "font-mono text-[11px] uppercase tracking-[0.25em]",
                "border-green-500/50 bg-green-500/10 text-green-400",
                "hover:bg-green-500 hover:text-slate-950",
                "transition-colors disabled:opacity-50"
              )}
            >
              {scanning ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <PlayCircle className="h-3.5 w-3.5" />}
              {scanning ? "RUNNING" : "RUN SCAN NOW"}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 py-6 space-y-6">
        {/* TOP STATS */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <StatTile
            testid="stat-tickers"
            label="Tickers Scanned"
            value={tickersInScan}
            sub={`From ${scan?.raw_counts?.insider_clusters || 0} · ${scan?.raw_counts?.high_short_interest || 0} · ${scan?.raw_counts?.upcoming_earnings || 0}`}
            icon={TrendingUp}
            accent="text-slate-50"
          />
          <StatTile
            testid="stat-cache"
            label="Calls Saved (Cache)"
            value={callsSaved}
            sub={`${fresh} fresh · ${cached} hits last scan`}
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
          {/* Latest Scan Results */}
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
                        <th className="px-3 py-2.5 text-left font-mono">Signals</th>
                        <th className="px-3 py-2.5 text-left font-mono">Thesis</th>
                        <th className="px-3 py-2.5 text-left font-mono">Entry Zone</th>
                        <th className="px-3 py-2.5 text-left font-mono">Catalyst</th>
                      </tr>
                    </thead>
                    <tbody>
                      {scan.results.map((r, i) => (
                        <tr
                          key={r.ticker}
                          data-testid={`result-row-${r.ticker}`}
                          className={cls(
                            "border-b border-slate-900 hover:bg-slate-900",
                            i === 0 && "row-flash"
                          )}
                        >
                          <td className="px-4 py-3 font-mono font-semibold text-slate-50">
                            ${r.ticker}
                            {r.cached && (
                              <span className="ml-2 font-mono text-[9px] uppercase text-slate-600">cache</span>
                            )}
                          </td>
                          <td className="px-3 py-3">
                            <span
                              className={cls(
                                "inline-flex items-center px-2 py-0.5 border font-mono text-xs",
                                scoreColor(r.signal_score)
                              )}
                            >
                              {r.signal_score}/10
                            </span>
                          </td>
                          <td className="px-3 py-3">
                            <div className="flex flex-wrap gap-1">
                              {(r.signals || []).map((s) => {
                                const sl = signalLabel(s);
                                return (
                                  <span
                                    key={s}
                                    className={cls(
                                      "inline-flex items-center px-1.5 py-0.5 border font-mono text-[9px] tracking-wider",
                                      sl.color
                                    )}
                                  >
                                    {sl.label}
                                  </span>
                                );
                              })}
                            </div>
                          </td>
                          <td className="px-3 py-3 max-w-md text-slate-300 leading-snug">
                            {r.thesis}
                          </td>
                          <td className="px-3 py-3 font-mono text-xs text-slate-300">
                            {r.entry_zone || "—"}
                          </td>
                          <td className="px-3 py-3 font-mono text-xs text-amber-400">
                            {r.catalyst_date || "—"}
                          </td>
                        </tr>
                      ))}
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
                <button
                  data-testid="watchlist-add-btn"
                  onClick={addWatch}
                  className="border border-slate-700 px-2.5 py-1.5 hover:bg-slate-800 hover:text-green-400 transition-colors"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="max-h-72 overflow-y-auto">
                {!watchlist.length && (
                  <div className="p-4 font-mono text-xs text-slate-600 text-center">empty</div>
                )}
                {watchlist.map((w) => (
                  <div
                    key={w.ticker}
                    data-testid={`watch-row-${w.ticker}`}
                    className="flex items-center justify-between px-4 py-2.5 border-b border-slate-900 hover:bg-slate-900"
                  >
                    <div>
                      <div className="font-mono text-sm font-semibold">${w.ticker}</div>
                      <div className="font-mono text-[10px] text-slate-500 truncate max-w-[150px]">
                        {w.name || ""}
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      <span className="font-mono text-xs text-slate-300 tabular-nums">
                        {w.price != null ? `$${w.price}` : "—"}
                      </span>
                      <button
                        onClick={() => removeWatch(w.ticker)}
                        className="text-slate-600 hover:text-red-500"
                      >
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
                <input
                  data-testid="alert-ticker-input"
                  value={aTicker}
                  onChange={(e) => setATicker(e.target.value.toUpperCase())}
                  placeholder="TICKER"
                  className="bg-slate-950 border border-slate-800 px-2.5 py-1.5 font-mono text-xs focus:outline-none focus:border-green-500/60"
                />
                <input
                  data-testid="alert-price-input"
                  value={aPrice}
                  onChange={(e) => setAPrice(e.target.value)}
                  placeholder="PRICE"
                  className="bg-slate-950 border border-slate-800 px-2.5 py-1.5 font-mono text-xs focus:outline-none focus:border-green-500/60"
                />
                <button
                  data-testid="alert-add-btn"
                  onClick={addAlert}
                  className="border border-slate-700 px-2.5 py-1.5 hover:bg-slate-800 hover:text-green-400 transition-colors"
                >
                  <Plus className="h-3.5 w-3.5" />
                </button>
              </div>
              <div className="max-h-60 overflow-y-auto">
                {!alerts.length && (
                  <div className="p-4 font-mono text-xs text-slate-600 text-center">no alerts</div>
                )}
                {alerts.map((a) => (
                  <div
                    key={`${a.ticker}-${a.created_at}`}
                    className="flex items-center justify-between px-4 py-2.5 border-b border-slate-900 hover:bg-slate-900"
                  >
                    <div className="font-mono text-sm">
                      <span className="font-semibold">${a.ticker}</span>{" "}
                      <span className="text-slate-500">@</span>{" "}
                      <span className="text-amber-400 tabular-nums">${a.target_price}</span>
                    </div>
                    <button
                      onClick={() => removeAlert(a.ticker)}
                      className="text-slate-600 hover:text-red-500"
                    >
                      <Trash2 className="h-3 w-3" />
                    </button>
                  </div>
                ))}
              </div>
            </Section>
          </div>
        </div>

        {/* ACTIVITY LOG */}
        <Section
          testid="activity-section"
          title="ACTIVITY LOG"
          right={<Activity className="h-3.5 w-3.5 text-slate-500" />}
        >
          <div className="bg-black/40 max-h-72 overflow-y-auto font-mono text-xs">
            {!activity.length && (
              <div className="p-4 text-slate-600 text-center">no activity yet</div>
            )}
            {activity.map((a, i) => (
              <div
                key={i}
                className="px-4 py-1.5 flex items-start gap-3 border-b border-slate-900/50 hover:bg-slate-900/40"
              >
                <span className="text-slate-600 shrink-0">{formatTime(a.ts)}</span>
                <ChevronRight className="h-3 w-3 text-slate-700 shrink-0 mt-0.5" />
                <span
                  className={cls(
                    "shrink-0 uppercase tracking-wider w-16",
                    a.level === "success" && "text-green-500",
                    a.level === "info" && "text-slate-400",
                    a.level === "error" && "text-red-500"
                  )}
                >
                  {a.level}
                </span>
                <span className="text-slate-300 break-all">{a.message}</span>
              </div>
            ))}
          </div>
        </Section>

        <footer className="pt-4 pb-8 font-mono text-[10px] uppercase tracking-[0.25em] text-slate-600 flex items-center justify-between">
          <span>STOCK_INTEL_BOT · v1.0</span>
          <span>{status?.bot?.claude_configured ? "CLAUDE ✓" : "CLAUDE ✗"} · {status?.bot?.telegram_configured ? "TG ✓" : "TG ✗"}</span>
        </footer>
      </main>
    </div>
  );
}
