import { useEffect, useState } from "react";
import axios from "axios";
import { toast } from "sonner";
import { CrtShell, Card, tokens } from "./CrtShell";

const API = `${(process.env.REACT_APP_BACKEND_URL || "").replace(/\/$/, "")}/api`;
const { accent, accent2, dim, muted, labelLight, hairline, cardBg } = tokens;

export default function SettingsPage() {
  const [status, setStatus] = useState(null);
  const [criteria, setCriteria] = useState(null);
  const [admin, setAdmin] = useState(null);
  const [backendRefreshing, setBackendRefreshing] = useState(false);
  const [backendBooting, setBackendBooting] = useState(false);
  const [backendLink, setBackendLink] = useState(null);

  useEffect(() => {
    axios.get(`${API}/status`).then(r => setStatus(r.data)).catch(() => setStatus({}));
    axios.get(`${API}/admin/pipeline_criteria`).then(r => setCriteria(r.data)).catch(() => {});
    axios.get(`${API}/admin/integration_status`).then(r => setAdmin(r.data)).catch(() => {});
  }, []);

  const refreshBackendLink = async () => {
    if (backendRefreshing) return;
    setBackendRefreshing(true);
    toast("BACKEND LINK REFRESH INITIATED");
    try {
      const { data } = await axios.post(`${API}/admin/backend_refresh`);
      setStatus(data.status || {});
      setAdmin({
        integrations: data.integrations || [],
        jobs: data.jobs || [],
        commands: data.commands || [],
      });
      setBackendLink({ ok: true, at: data.refreshed_at || new Date().toISOString() });
      toast("BACKEND LINK REFRESHED");
    } catch (e) {
      const detail = e?.response?.data?.detail || e?.message || "failed";
      setBackendLink({ ok: false, at: new Date().toISOString(), detail });
      toast(`BACKEND LINK REFRESH FAILED - ${detail}`);
    } finally {
      setBackendRefreshing(false);
    }
  };
  const forceBootBackend = async () => {
    if (backendBooting) return;
    setBackendBooting(true);
    toast("FORCE BACKEND BOOT INITIATED");
    try {
      if (!window.__TAURI_INTERNALS__) {
        toast("FORCE BOOT IS AVAILABLE IN THE DESKTOP APP ONLY");
        return;
      }
      const { invoke } = await import("@tauri-apps/api/core");
      const result = await invoke("force_boot_backend");
      setBackendLink({
        ok: result.ok,
        at: new Date().toISOString(),
        detail: result.message,
      });
      toast(result.ok ? `BACKEND BOOT OK - ${result.message}` : `BACKEND BOOT FAILED - ${result.message}`);
      try {
        const { data } = await axios.post(`${API}/admin/backend_refresh`);
        setStatus(data.status || {});
        setAdmin({
          integrations: data.integrations || [],
          jobs: data.jobs || [],
          commands: data.commands || [],
        });
      } catch {}
    } catch (e) {
      const detail = e?.message || "failed";
      setBackendLink({ ok: false, at: new Date().toISOString(), detail });
      toast(`BACKEND BOOT FAILED - ${detail}`);
    } finally {
      setBackendBooting(false);
    }
  };

  const runLearning = async () => {
    toast("LEARNING CYCLE INITIATED");
    try {
      const { data } = await axios.post(`${API}/learning/run`);
      toast(data.skipped ? `SKIPPED — ${data.reason}` : `COMPLETE — ${data.trades} TRADES`);
    } catch { toast("LEARNING FAILED"); }
  };
  const resetWeights = async () => {
    if (!window.confirm("Reset all weights to defaults?")) return;
    await axios.post(`${API}/learning/reset`);
    toast("WEIGHTS RESET");
  };
  const triggerPnl = async () => {
    toast("REFRESHING P&L RETURNS...");
    try {
      const { data } = await axios.post(`${API}/pnl/refresh`);
      toast(`P&L: ${data.signals_refreshed || 0} SIGNALS · ${data.options_rows_refreshed || 0} OPTIONS`);
    } catch { toast("P&L REFRESH FAILED"); }
  };
  const seedBacktest = async () => {
    toast("SEEDING BACKTEST...");
    try {
      const { data } = await axios.post(`${API}/backtest/seed`);
      toast(`SEEDED ${data.written || 0} ROWS`);
    } catch { toast("BACKTEST SEED FAILED"); }
  };

  return (
    <CrtShell title="SETTINGS & SYSTEM">
      {/* v5.1 — Integration Status / Scheduled Jobs / Telegram Commands */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 14, marginBottom: 18 }}>
        <Card title={`INTEGRATION STATUS · ${(admin?.integrations || []).length}`} accentColor={accent}>
          {!admin ? <div style={{ color: muted, padding: 8 }}>Loading...</div> :
            admin.integrations.map((i, idx) => (
              <div key={idx} data-testid={`integ-${i.key}`} style={{
                display: "grid", gridTemplateColumns: "1fr 60px 80px",
                padding: "6px 0", borderBottom: hairline, fontSize: 10, gap: 8, alignItems: "center",
              }}>
                <span style={{ color: labelLight }}>{i.name}</span>
                <span style={{ color: i.ok ? "#4ade80" : "#f87171", fontWeight: 700, fontSize: 9, letterSpacing: "0.1em" }}>
                  {i.ok ? "● LIVE" : "○ DOWN"}
                </span>
                <span style={{ color: dim, fontSize: 9 }}>{(i.last || "").slice(5, 16) || "—"}</span>
              </div>
            ))}
        </Card>
        <Card title={`SCHEDULED JOBS · ${(admin?.jobs || []).length}`} accentColor={accent2}>
          {!admin ? <div style={{ color: muted, padding: 8 }}>Loading...</div> :
            admin.jobs.map((j, idx) => (
              <div key={idx} data-testid={`job-${j.id}`} style={{
                padding: "6px 0", borderBottom: hairline, fontSize: 10,
              }}>
                <div style={{ color: accent2, fontWeight: 700 }}>{j.name}</div>
                <div style={{ color: dim, fontSize: 9, marginTop: 2 }}>{j.cron}</div>
              </div>
            ))}
        </Card>
        <Card title={`TELEGRAM COMMANDS · ${(admin?.commands || []).length}`} accentColor="#4ade80">
          {!admin ? <div style={{ color: muted, padding: 8 }}>Loading...</div> :
            admin.commands.map((c, idx) => (
              <div key={idx} data-testid={`cmd-${c.cmd.replace('/', '')}`} style={{
                display: "grid", gridTemplateColumns: "100px 1fr",
                padding: "6px 0", borderBottom: hairline, fontSize: 10, gap: 8,
              }}>
                <span style={{ color: "#4ade80", fontFamily: "JetBrains Mono", fontWeight: 700 }}>{c.cmd}</span>
                <span style={{ color: dim }}>{c.desc}</span>
              </div>
            ))}
        </Card>
      </div>

      {/* ── Pipeline Criteria ── */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 4 }}>
        <Card title="PIPELINE CRITERIA · PRE-FILTER SCREENER" accentColor={accent2}>
          <div style={{ color: muted, fontSize: 11, marginBottom: 12, letterSpacing: "0.04em", lineHeight: 1.6 }}>
            What has to be true about a ticker for it to get flagged and passed to the scoring engine.
          </div>
          {!criteria ? (
            <div style={{ color: muted, padding: 10 }}>Loading...</div>
          ) : (
            criteria.pre_filter.map((r, i) => (
              <div key={i} style={{
                display: "grid", gridTemplateColumns: "180px 1fr",
                padding: "8px 0", borderBottom: hairline, fontSize: 12, gap: 12,
              }}>
                <span style={{ color: accent2, letterSpacing: "0.08em", fontWeight: 700 }}>
                  {r.rule}
                </span>
                <span style={{ color: labelLight, fontSize: 11, lineHeight: 1.5 }}>{r.detail}</span>
              </div>
            ))
          )}
        </Card>

        <Card title="PIPELINE CRITERIA · FINAL SCREENER · LIVE WEIGHTS" accentColor={accent}>
          <div style={{ color: muted, fontSize: 11, marginBottom: 12, letterSpacing: "0.04em", lineHeight: 1.6 }}>
            Case Score formula components — weights live from the learning engine.
          </div>
          {!criteria ? (
            <div style={{ color: muted, padding: 10 }}>Loading...</div>
          ) : (
            <>
              {criteria.final_screener.map((w, i) => (
                <div key={i} style={{
                  display: "grid", gridTemplateColumns: "1.6fr 60px 1fr",
                  padding: "8px 0", borderBottom: hairline, fontSize: 12, gap: 12, alignItems: "center",
                }}>
                  <span style={{ color: accent, letterSpacing: "0.06em", fontWeight: 700, fontSize: 11 }}>
                    {w.key}
                  </span>
                  <span className="num" style={{
                    color: accent, fontWeight: 700, fontSize: 14, textAlign: "right",
                    fontFamily: "JetBrains Mono",
                  }}>{w.weight?.toFixed(2)}</span>
                  <span style={{ color: muted, fontSize: 10, lineHeight: 1.5 }}>{w.description}</span>
                </div>
              ))}
              <div style={{ marginTop: 12, padding: "10px 0", borderTop: `0.5px solid ${accent}33`,
                              color: labelLight, fontSize: 10, letterSpacing: "0.08em", lineHeight: 1.6 }}>
                <span style={{ color: dim }}>FORMULA:</span> {criteria.axiom_score_formula}
              </div>
            </>
          )}
        </Card>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
        <div>
          <Card title="INTEGRATIONS STATUS">
            <Row k="MONGODB" v="[CONNECTED]" c="#4ade80" />
            <Row k="TELEGRAM BOT" v="[ACTIVE — @CaseCapitalTerminalQuant]" c="#4ade80" />
            <Row k="CLAUDE LLM" v="[ANTHROPIC KEY - 24H CACHE]" c="#4ade80" />
            <Row k="USASPENDING API" v="[PUBLIC ENDPOINT]" c="#4ade80" />
            <Row k="OPENINSIDER" v="[HTML SCRAPE]" c="#4ade80" />
            <Row k="FINVIZ" v="[HTML SCRAPE]" c="#4ade80" />
            <Row k="YAHOO FINANCE" v="[YFINANCE — DEFAULT PRICE SRC]" c="#4ade80" />
          </Card>

          <Card title="SCHEDULER JOBS">
            <Row k="08:00 ET MON-FRI" v="DAILY SCAN" />
            <Row k="12:01 ET MON-FRI" v="MID-DAY SCAN" />
            <Row k="15:30 ET MON-FRI" v="PRE-CLOSE SCAN" />
            <Row k="EVERY 15 MIN" v="UNUSUAL FLOW REFRESH" />
            <Row k="EVERY 5 MIN" v="PRICE ALERT CHECKS" />
            <Row k="23:00 ET DAILY" v="P&L REFRESH" />
            <Row k="02:00 ET DAILY" v="P&L SECOND PASS" />
            <Row k="02:00 ET SUNDAY" v="LEARNING CYCLE" c={accent} />
          </Card>

          <Card title="LEARNING ENGINE CONFIG">
            <Row k="MIN SAMPLES BEFORE ADJUST" v="10" />
            <Row k="MAX WEIGHT CHANGE PER CYCLE" v="±15%" />
            <Row k="BASELINE WIN RATE" v="50%" />
            <Row k="RETURN BASIS" v="30-DAY" />
            <Row k="HARD FLOOR/CEILING" v="ENFORCED" c={accent} />
            <Row k="FEATURE VERSION" v="3.0" />
          </Card>
        </div>

        <div>
          <Card title="SYSTEM STATUS"
            action={<div style={{ display: "flex", gap: 8 }}>
              <button data-testid="settings-force-backend-boot" onClick={forceBootBackend} disabled={backendBooting} style={btnDanger}>
                [ {backendBooting ? "BOOTING" : "FORCE BOOT BACKEND"} ]
              </button>
              <button data-testid="settings-backend-link-refresh" onClick={refreshBackendLink} disabled={backendRefreshing} style={btnTeal}>
                [ {backendRefreshing ? "REFRESHING" : "BACKEND LINK REFRESH"} ]
              </button>
            </div>}>
            <Row k="BACKEND PORT" v="8001" />
            <Row k="BACKEND LINK" v={backendLink ? (backendLink.ok ? `OK ${new Date(backendLink.at).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit" })}` : "FAILED") : "AUTO"} c={backendLink?.ok === false ? "#f87171" : accent2} />
            {backendLink?.detail && <Row k="BACKEND DETAIL" v={backendLink.detail} c={backendLink.ok ? accent2 : "#f87171"} />}
            <Row k="BOT BACKEND VERSION" v="3.0.0" c={accent} />
            <Row k="LAST SCAN" v={status?.last_scan_at || "UNKNOWN"} />
            <Row k="NEXT SCHEDULED SCAN" v="08:00 ET" />
            <Row k="SCAN IN PROGRESS" v={status?.scan_in_progress ? "YES" : "NO"}
                 c={status?.scan_in_progress ? "#fb923c" : "#4ade80"} />
            <Row k="UNIVERSE SIZE" v="~25 PRE-FILTERED CANDIDATES" />
            <Row k="CACHE STRATEGY" v="24H CLAUDE · 6H NLQ" />
          </Card>

          <Card title="MANUAL TRIGGERS">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              <button data-testid="trigger-learning-btn" onClick={runLearning} style={btnGold}>
                [ RUN LEARNING CYCLE ]
              </button>
              <button data-testid="reset-weights-btn" onClick={resetWeights} style={btnDim}>
                [ RESET WEIGHTS ]
              </button>
              <button data-testid="trigger-pnl-btn" onClick={triggerPnl} style={btnGold}>
                [ REFRESH P&L ]
              </button>
              <button data-testid="seed-backtest-btn-settings" onClick={seedBacktest} style={btnGold}>
                [ SEED BACKTEST ]
              </button>
            </div>
            <div style={{ marginTop: 14, fontSize: 11, color: muted, lineHeight: 1.6 }}>
              All triggers also accessible from Telegram: /scan, /performance, /backtest_seed
            </div>
          </Card>

          <Card title="TELEGRAM COMMANDS">
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", fontSize: 12,
                            color: labelLight, gap: 6, letterSpacing: "0.04em" }}>
              <span style={{ color: accent }}>/scan</span><span>Daily scan</span>
              <span style={{ color: accent }}>/scan_gov</span><span>Gov-only scan</span>
              <span style={{ color: accent }}>/analyze TICKER</span><span>Deep dive</span>
              <span style={{ color: accent }}>/options TICKER</span><span>Options play</span>
              <span style={{ color: accent }}>/flow TICKER</span><span>Unusual flow</span>
              <span style={{ color: accent }}>/iv TICKER</span><span>IV rank</span>
              <span style={{ color: accent }}>/spread TICKER</span><span>Spread analysis</span>
              <span style={{ color: accent }}>/calls /puts</span><span>Filter today's scan</span>
              <span style={{ color: accent }}>/noiv</span><span>Low IV picks</span>
              <span style={{ color: accent }}>/performance</span><span>P&L summary</span>
              <span style={{ color: accent }}>/backtest</span><span>Backtest stats</span>
              <span style={{ color: accent }}>/backtest_seed</span><span>Seed congress data</span>
            </div>
          </Card>
        </div>
      </div>
    </CrtShell>
  );
}

function Row({ k, v, c = "#e5e7eb" }) {
  return (
    <div style={{
      display: "flex", justifyContent: "space-between", padding: "7px 0",
      fontSize: 13, letterSpacing: "0.04em",
    }}>
      <span style={{ color: dim, fontSize: 11, letterSpacing: "0.12em" }}>{k}</span>
      <span style={{ color: c, fontWeight: c === "#e5e7eb" ? 400 : 700 }}>{v}</span>
    </div>
  );
}

const btnGold = {
  background: "transparent", border: `0.5px solid ${accent}`,
  color: accent, fontSize: 12, padding: "10px 14px", cursor: "pointer",
  letterSpacing: "0.1em", fontFamily: "Courier New", fontWeight: 700,
};
const btnDim = {
  background: "transparent", border: `0.5px solid ${dim}`,
  color: muted, fontSize: 12, padding: "10px 14px", cursor: "pointer",
  letterSpacing: "0.1em", fontFamily: "Courier New",
};
const btnTeal = {
  background: "transparent", border: `0.5px solid ${accent2}`,
  color: accent2, fontSize: 10, padding: "7px 10px", cursor: "pointer",
  letterSpacing: "0.1em", fontFamily: "Courier New", fontWeight: 700,
};
const btnDanger = {
  background: "transparent", border: "0.5px solid #f87171",
  color: "#f87171", fontSize: 10, padding: "7px 10px", cursor: "pointer",
  letterSpacing: "0.1em", fontFamily: "Courier New", fontWeight: 700,
};
